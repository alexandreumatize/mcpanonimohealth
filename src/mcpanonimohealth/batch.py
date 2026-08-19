"""Processamento em lote local com organização por iniciais do paciente."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .documents import SUPPORTED_SUFFIXES, DocumentError, extract_document
from .domain import JobState
from .models import installed_model_path
from .organize import build_organization, unique_target
from .sanitizer import Sanitizer

MAX_BATCH_FILES = 40


@dataclass(slots=True)
class BatchItemResult:
    estado: str
    iniciais: str | None = None
    tipo: str | None = None
    data_documento: str | None = None
    relativo: str | None = None
    motivos: list[str] = field(default_factory=list)
    paginas: int = 0
    # Somente para revisão na UI local; nunca gravar no manifesto.
    texto: str | None = None


@dataclass(slots=True)
class BatchResult:
    ok: bool
    processados: int
    liberados: int
    retidos: int
    erros: int
    saida: str
    itens: list[BatchItemResult] = field(default_factory=list)

    def public(self, *, include_text: bool = False) -> dict[str, object]:
        items = []
        for item in self.itens:
            payload = asdict(item)
            if not include_text:
                payload.pop("texto", None)
            items.append(payload)
        return {
            "ok": self.ok,
            "processados": self.processados,
            "liberados": self.liberados,
            "retidos": self.retidos,
            "erros": self.erros,
            "saida": self.saida,
            "itens": items,
            "aviso": (
                "Só o texto desidentificado é gravado. Pastas usam iniciais, não o nome completo. "
                "Revise os arquivos antes de uso em pesquisa."
            ),
        }


def iter_input_documents(input_dir: Path) -> list[Path]:
    root = input_dir.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("INPUT_DIR_INVALID")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
            continue
        files.append(path)
    return files


def _write_pass_item(
    *,
    output_root: Path,
    source_text: str,
    sanitized_text: str,
    warnings: list[str],
    pages: int,
    source_mtime: float | None,
    keep_text: bool,
) -> BatchItemResult:
    organization = build_organization(source_text, source_mtime=source_mtime)
    target = unique_target(output_root, organization.relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    header = (
        f"# mcpanonimohealth lote\n"
        f"# iniciais={organization.initials}\n"
        f"# tipo={organization.doc_type}\n"
        f"# data_documento={organization.doc_date.isoformat()}\n"
        f"# avisos={','.join(warnings) if warnings else '-'}\n\n"
    )
    target.write_text(header + sanitized_text, encoding="utf-8")
    os.chmod(target, 0o600)
    return BatchItemResult(
        estado=JobState.PASS.value,
        iniciais=organization.initials,
        tipo=organization.doc_type,
        data_documento=organization.doc_date.isoformat(),
        relativo=str(target.relative_to(output_root)),
        motivos=list(warnings),
        paginas=pages,
        texto=sanitized_text if keep_text else None,
    )


def _process_one_path(
    path: Path,
    *,
    output_root: Path,
    engine: Sanitizer,
    keep_text: bool,
) -> BatchItemResult:
    document = extract_document(path)
    if document.hold_reasons and not document.text.strip():
        return BatchItemResult(
            estado=JobState.HOLD.value,
            motivos=list(document.hold_reasons),
            paginas=len(document.pages),
        )
    result = engine.sanitize(document.text)
    if result.state is not JobState.PASS or not result.sanitized_text:
        return BatchItemResult(
            estado=result.state.value,
            motivos=list(result.reasons),
            paginas=len(document.pages),
        )
    return _write_pass_item(
        output_root=output_root,
        source_text=document.text,
        sanitized_text=result.sanitized_text,
        warnings=list(document.warnings),
        pages=len(document.pages),
        source_mtime=path.stat().st_mtime,
        keep_text=keep_text,
    )


def process_batch_payloads(
    uploads: list[tuple[str, bytes]],
    output_dir: Path,
    *,
    sanitizer: Sanitizer | None = None,
    keep_text: bool = False,
) -> BatchResult:
    """Desidentifica uploads em memória (interface local) e organiza a saída."""

    if not uploads:
        raise ValueError("DOCUMENT_MISSING")
    if len(uploads) > MAX_BATCH_FILES:
        raise ValueError("TOO_MANY_FILES")

    output_root = output_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    os.chmod(output_root, 0o700)
    engine = sanitizer or Sanitizer(openmed_model_path=installed_model_path())
    items: list[BatchItemResult] = []
    liberados = retidos = erros = 0

    with tempfile.TemporaryDirectory(prefix="mcpanonimohealth-batch-") as staging:
        os.chmod(staging, 0o700)
        for index, (suffix, payload) in enumerate(uploads, start=1):
            try:
                if suffix.casefold() not in SUPPORTED_SUFFIXES:
                    raise DocumentError("UNSUPPORTED_FORMAT")
                if not payload:
                    raise DocumentError("EMPTY_FILE")
                path = Path(staging) / f"doc-{index:03d}{suffix.casefold()}"
                path.write_bytes(payload)
                os.chmod(path, 0o600)
                del payload
                item = _process_one_path(
                    path, output_root=output_root, engine=engine, keep_text=keep_text
                )
                if item.estado == JobState.PASS.value:
                    liberados += 1
                elif item.estado == JobState.HOLD.value:
                    retidos += 1
                else:
                    erros += 1
                items.append(item)
            except DocumentError as exc:
                erros += 1
                items.append(BatchItemResult(estado=JobState.ERROR.value, motivos=[str(exc)]))
            except (OSError, ValueError, RuntimeError):
                erros += 1
                items.append(
                    BatchItemResult(estado=JobState.ERROR.value, motivos=["LOCAL_BATCH_FAILED"])
                )
            except Exception:
                erros += 1
                items.append(
                    BatchItemResult(estado=JobState.ERROR.value, motivos=["LOCAL_BATCH_FAILED"])
                )

    _write_manifest(output_root, items, liberados=liberados, retidos=retidos, erros=erros)
    return BatchResult(
        ok=erros == 0,
        processados=len(items),
        liberados=liberados,
        retidos=retidos,
        erros=erros,
        saida=str(output_root),
        itens=items,
    )


def process_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    sanitizer: Sanitizer | None = None,
) -> BatchResult:
    """Desidentifica todos os documentos suportados e organiza a saída local."""

    output_root = output_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    os.chmod(output_root, 0o700)
    engine = sanitizer or Sanitizer(openmed_model_path=installed_model_path())
    items: list[BatchItemResult] = []
    liberados = retidos = erros = 0

    for path in iter_input_documents(input_dir):
        try:
            if output_root in path.resolve().parents or path.resolve() == output_root:
                continue
            item = _process_one_path(
                path, output_root=output_root, engine=engine, keep_text=False
            )
            if item.estado == JobState.PASS.value:
                liberados += 1
            elif item.estado == JobState.HOLD.value:
                retidos += 1
            else:
                erros += 1
            items.append(item)
        except (DocumentError, OSError, ValueError, RuntimeError) as exc:
            erros += 1
            code = str(exc) if isinstance(exc, DocumentError) else "LOCAL_BATCH_FAILED"
            items.append(BatchItemResult(estado=JobState.ERROR.value, motivos=[code]))
        except Exception:
            erros += 1
            items.append(
                BatchItemResult(estado=JobState.ERROR.value, motivos=["LOCAL_BATCH_FAILED"])
            )

    _write_manifest(output_root, items, liberados=liberados, retidos=retidos, erros=erros)
    return BatchResult(
        ok=erros == 0,
        processados=len(items),
        liberados=liberados,
        retidos=retidos,
        erros=erros,
        saida=str(output_root),
        itens=items,
    )


def _write_manifest(
    output_root: Path,
    items: list[BatchItemResult],
    *,
    liberados: int,
    retidos: int,
    erros: int,
) -> None:
    manifest = {
        "processados": len(items),
        "liberados": liberados,
        "retidos": retidos,
        "erros": erros,
        "itens": [
            {key: value for key, value in asdict(item).items() if key != "texto"}
            for item in items
        ],
    }
    manifest_path = output_root / "manifesto_lote.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)


__all__ = [
    "BatchItemResult",
    "BatchResult",
    "MAX_BATCH_FILES",
    "iter_input_documents",
    "process_batch",
    "process_batch_payloads",
]
