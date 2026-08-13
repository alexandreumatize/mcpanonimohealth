"""Instalação explícita e descoberta de modelos usados somente de forma local."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from platformdirs import user_data_path

OPENMED_REPOSITORY = "OpenMed/OpenMed-PII-Portuguese-ClinicalE5-Small-33M-v1"
OPENMED_REVISION = "01a1677dea6daab4cb31d60a4ec3b1176a4a0244"


def model_root() -> Path:
    """Diretório privado do usuário, fora do repositório e dos jobs."""

    return user_data_path("mcpanonimohealth", appauthor=False) / "models" / "openmed-pt-33m"


def is_model_ready(path: Path | None = None) -> bool:
    candidate = path or model_root()
    weights = list(candidate.glob("*.safetensors")) + list(candidate.glob("pytorch_model*.bin"))
    return (candidate / "config.json").is_file() and bool(weights)


def installed_model_path() -> Path | None:
    candidate = model_root()
    return candidate if is_model_ready(candidate) else None


def install_openmed_model() -> Path:
    """Baixa o modelo durante o setup; nunca é chamada pelo processamento."""

    from huggingface_hub import snapshot_download

    destination = model_root()
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=OPENMED_REPOSITORY,
        revision=OPENMED_REVISION,
        local_dir=destination,
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "*.txt",
            "*.model",
            "tokenizer*",
            "vocab*",
            "merges*",
        ],
    )
    if not is_model_ready(destination):
        raise RuntimeError("modelo OpenMed incompleto após a instalação")
    _write_manifest(destination)
    return destination


def _write_manifest(directory: Path) -> None:
    files: dict[str, str] = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.name != "manifest.local.json":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files[path.name] = digest
    manifest = {
        "repository": OPENMED_REPOSITORY,
        "revision": OPENMED_REVISION,
        "sha256": files,
    }
    (directory / "manifest.local.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "OPENMED_REPOSITORY",
    "install_openmed_model",
    "installed_model_path",
    "is_model_ready",
    "model_root",
]
