"""Jobs efêmeros em memória, sem retenção do arquivo original."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .documents import DocumentError, extract_document
from .domain import JobState
from .models import installed_model_path
from .sanitizer import Sanitizer
from .selectors import SelectionCancelled, select_local_file

TTL_SECONDS = 60 * 60


@dataclass(slots=True)
class _Job:
    job_id: str
    state: JobState
    created_monotonic: float
    expires_at: datetime
    pages: int = 0
    duration_ms: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    clean_text: str | None = None
    modo: str = "unico"
    batch_items: list[dict[str, object]] | None = None

    def public(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "job_id": self.job_id,
            "state": self.state.value,
            "pages": self.pages,
            "duration_ms": self.duration_ms,
            "counts": dict(self.counts),
            "reasons": list(self.reasons),
            "expires_at": self.expires_at.isoformat(),
            "modo": self.modo,
        }
        if self.modo == "lote":
            payload["liberados"] = int(self.counts.get("LOTE_LIBERADOS", 0))
            payload["retidos"] = int(self.counts.get("LOTE_RETIDOS", 0))
            payload["erros"] = int(self.counts.get("LOTE_ERROS", 0))
            payload["processados"] = (
                int(payload["liberados"]) + int(payload["retidos"]) + int(payload["erros"])
            )
        return payload


class JobManager:
    def __init__(self, *, ttl_seconds: int = TTL_SECONDS) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._jobs: dict[str, _Job] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.RLock()
        self._sanitizer = Sanitizer(openmed_model_path=installed_model_path())

    def health(self) -> dict[str, object]:
        from .diagnose import diagnose

        report = diagnose()
        return {
            "backend": "disponivel" if report["ok"] else "indisponivel",
            "ocr_local": True,
            "modelo_openmed_local": bool(report["verificacoes"].get("modelo_openmed_local")),
            "ner_carregado": bool(report["verificacoes"].get("ner_carregado")),
            "caso_sintetico": bool(report["verificacoes"].get("caso_sintetico")),
            "retencao_minutos": self._ttl_seconds // 60,
            "ok": report["ok"],
        }

    def create_from_selection(self) -> dict[str, object]:
        """Compatibilidade interna; o MCP público usa a interface web local."""

        job = self._reserve_job()
        try:
            selected = select_local_file()
            self._process_path(job, selected)
            # A referência ao caminho deixa de existir imediatamente após o uso.
            del selected
        except SelectionCancelled:
            self._fail_job(job, JobState.ERROR, "SELECTION_CANCELLED")
        except DocumentError as exc:
            self._fail_job(job, JobState.HOLD, str(exc))
        except Exception:
            self._fail_job(job, JobState.ERROR, "LOCAL_PROCESSING_FAILED")
        return job.public()

    def reserve(self) -> dict[str, object]:
        """Reserva um job PROCESSING para uma única seleção na interface local."""

        return self._reserve_job().public()

    def process_path(self, job_id: str, path: Path) -> dict[str, object]:
        """Processa um caminho efêmero já validado pela camada de interface local."""

        job = self._lookup(job_id)
        if job.state is not JobState.PROCESSING:
            raise RuntimeError("job não aceita novo documento")
        try:
            self._process_path(job, Path(path))
        except DocumentError as exc:
            self._fail_job(job, JobState.HOLD, str(exc))
        except Exception:
            self._fail_job(job, JobState.ERROR, "LOCAL_PROCESSING_FAILED")
        return job.public()

    def complete_batch(
        self,
        job_id: str,
        *,
        liberados: int,
        retidos: int,
        erros: int,
        pages: int,
        duration_ms: int,
        itens: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Fecha o job da UI após um lote local e guarda derivados PASS para o agente."""

        job = self._lookup(job_id)
        if job.state is not JobState.PROCESSING:
            raise RuntimeError("job não aceita lote")
        job.modo = "lote"
        job.pages = pages
        job.duration_ms = duration_ms
        job.counts = {
            "LOTE_LIBERADOS": liberados,
            "LOTE_RETIDOS": retidos,
            "LOTE_ERROS": erros,
        }
        safe_items: list[dict[str, object]] = []
        released_chunks: list[str] = []
        for item in itens or []:
            entry = {
                "estado": item.get("estado"),
                "iniciais": item.get("iniciais"),
                "tipo": item.get("tipo"),
                "data_documento": item.get("data_documento"),
                "relativo": item.get("relativo"),
                "paginas": item.get("paginas", 0),
                "motivos": list(item.get("motivos") or []),
            }
            texto = item.get("texto") or item.get("texto_desidentificado")
            if entry["estado"] == JobState.PASS.value and isinstance(texto, str) and texto.strip():
                entry["texto_desidentificado"] = texto
                header = entry.get("relativo") or (
                    f"{entry.get('iniciais') or 'XX'}/"
                    f"{entry.get('tipo') or 'documento'}_"
                    f"{entry.get('data_documento') or 'sem-data'}.txt"
                )
                released_chunks.append(f"## {header}\n\n{texto.strip()}")
            safe_items.append(entry)
        job.batch_items = safe_items
        job.clean_text = "\n\n-----\n\n".join(released_chunks) if released_chunks else None
        if liberados > 0 and job.clean_text:
            job.state = JobState.PASS
            job.reasons = ("LOTE_LOCAL",)
        elif retidos > 0:
            job.state = JobState.HOLD
            job.reasons = ("LOTE_LOCAL_SEM_PASS",)
            job.clean_text = None
            job.batch_items = safe_items
        else:
            job.state = JobState.ERROR
            job.reasons = ("LOTE_LOCAL_FALHOU",)
            job.clean_text = None
            job.batch_items = safe_items
        return job.public()

    def cancel_pending(self, job_id: str, reason: str = "LOCAL_INTERFACE_EXPIRED") -> None:
        """Fecha, sem conteúdo, uma reserva que não recebeu documento."""

        job = self._lookup(job_id)
        if job.state is JobState.PROCESSING:
            self._fail_job(job, JobState.ERROR, reason)

    def _reserve_job(self) -> _Job:
        self._expire()
        job = _Job(
            job_id=f"CASE-{secrets.token_hex(6).upper()}",
            state=JobState.PROCESSING,
            created_monotonic=time.monotonic(),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl_seconds),
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._arm_timer(job.job_id)
        return job

    def _process_path(self, job: _Job, path: Path) -> None:
        document = extract_document(path)
        job.pages = len(document.pages)
        job.duration_ms = document.duration_ms
        if document.hold_reasons:
            job.state = JobState.HOLD
            job.reasons = document.hold_reasons
            job.clean_text = None
            return
        result = self._sanitizer.sanitize(document.text)
        job.state = result.state
        # Avisos de OCR/QR acompanham o PASS; não bloqueiam a liberação.
        job.reasons = tuple(dict.fromkeys([*document.warnings, *result.reasons]))
        job.counts = dict(result.replacements)
        job.clean_text = result.sanitized_text if result.state is JobState.PASS else None
        job.duration_ms = round((time.monotonic() - job.created_monotonic) * 1000)

    @staticmethod
    def _fail_job(job: _Job, state: JobState, reason: str) -> None:
        job.state = state
        job.reasons = (reason,)
        job.clean_text = None

    def get(self, job_id: str) -> dict[str, object]:
        job = self._lookup(job_id)
        return job.public()

    def get_clean_text(self, job_id: str) -> str:
        job = self._lookup(job_id)
        if job.state is not JobState.PASS or not job.clean_text:
            raise RuntimeError("texto indisponível")
        return job.clean_text

    def get_release(self, job_id: str) -> dict[str, object]:
        """Pacote liberado ao agente: texto único ou itens organizados do lote."""

        job = self._lookup(job_id)
        if job.state is not JobState.PASS:
            raise RuntimeError("texto indisponível")
        if job.modo == "lote":
            itens = [dict(item) for item in (job.batch_items or [])]
            if not any(item.get("texto_desidentificado") for item in itens):
                raise RuntimeError("texto indisponível")
            return {
                "modo": "lote",
                "estrutura": "{INICIAIS}/{tipo}_{YYYY-MM-DD}.txt",
                "itens": itens,
                "texto_desidentificado": job.clean_text or "",
            }
        if not job.clean_text:
            raise RuntimeError("texto indisponível")
        return {
            "modo": "unico",
            "texto_desidentificado": job.clean_text,
        }

    def discard(self, job_id: str) -> dict[str, object]:
        with self._lock:
            self._cancel_timer(job_id)
            job = self._jobs.pop(job_id, None)
        if job is None:
            raise KeyError("job inexistente")
        job.clean_text = None
        job.batch_items = None
        return {"job_id": job_id, "discarded": True, "state": JobState.EXPIRED.value}

    def _lookup(self, job_id: str) -> _Job:
        self._expire()
        if not job_id.startswith("CASE-") or len(job_id) != 17:
            raise KeyError("job inválido")
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError("job inexistente")
        return job

    def _arm_timer(self, job_id: str) -> None:
        """Agenda remoção ativa; deve ser chamado com ``_lock`` adquirido."""

        self._cancel_timer(job_id)
        timer = threading.Timer(self._ttl_seconds, self._expire_job, args=(job_id,))
        timer.daemon = True
        self._timers[job_id] = timer
        timer.start()

    def _cancel_timer(self, job_id: str) -> None:
        """Cancela o timer do job; deve ser chamado com ``_lock`` adquirido."""

        timer = self._timers.pop(job_id, None)
        if timer is not None:
            timer.cancel()

    def _expire_job(self, job_id: str) -> None:
        """Remove um job pelo timer sem depender de nova consulta MCP."""

        with self._lock:
            self._timers.pop(job_id, None)
            job = self._jobs.pop(job_id, None)
            if job is not None:
                job.clean_text = None
                job.batch_items = None

    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if now - job.created_monotonic >= self._ttl_seconds
            ]
            for job_id in expired:
                self._cancel_timer(job_id)
                job = self._jobs.pop(job_id)
                job.clean_text = None
                job.batch_items = None


__all__ = ["JobManager", "TTL_SECONDS"]
