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

    def public(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "pages": self.pages,
            "duration_ms": self.duration_ms,
            "counts": dict(self.counts),
            "reasons": list(self.reasons),
            "expires_at": self.expires_at.isoformat(),
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        self._sanitizer = Sanitizer(openmed_model_path=installed_model_path())

    def health(self) -> dict[str, object]:
        return {
            "backend": "disponivel",
            "ocr_local": True,
            "modelo_openmed_local": installed_model_path() is not None,
            "retencao_minutos": TTL_SECONDS // 60,
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
            expires_at=datetime.now(UTC) + timedelta(seconds=TTL_SECONDS),
        )
        with self._lock:
            self._jobs[job.job_id] = job
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
        job.reasons = result.reasons
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

    def discard(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            raise KeyError("job inexistente")
        job.clean_text = None
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

    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if now - job.created_monotonic >= TTL_SECONDS
            ]
            for job_id in expired:
                job = self._jobs.pop(job_id)
                job.clean_text = None


__all__ = ["JobManager", "TTL_SECONDS"]
