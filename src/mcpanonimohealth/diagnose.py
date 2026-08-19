"""Diagnóstico compartilhado: módulos, NER local e caso sintético curto."""

from __future__ import annotations

import sys
from importlib.util import find_spec
from typing import Any

from . import __version__
from .detection import create_detector
from .domain import JobState
from .models import installed_model_path
from .sanitizer import Sanitizer

# Identificadores fictícios usados só no diagnóstico; nunca PHI real.
_SYNTHETIC_SECRETS = (
    "Maria da Silva",
    "529.982.247-25",
)
_SYNTHETIC_CASE = (
    "Paciente: Maria da Silva\n"
    "CPF: 529.982.247-25\n"
    "Diagnóstico: artrite reumatoide sintética."
)


def diagnose() -> dict[str, Any]:
    """Verifica instalação e carrega o NER com um caso sintético curto.

    Não executa OCR de imagem/PDF (isso fica nos testes de regressão).
    A saída contém somente flags e códigos — nunca o texto clínico.
    """

    checks: dict[str, object] = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "mcp": find_spec("mcp") is not None,
        "ocr": find_spec("rapidocr") is not None,
        "pdf": find_spec("pypdfium2") is not None,
        "backend": find_spec("mcpanonimohealth.jobs") is not None,
        "modelo_openmed_local": installed_model_path() is not None,
        "ner_carregado": False,
        "caso_sintetico": False,
    }
    falhas: list[str] = []

    if not checks["modelo_openmed_local"]:
        falhas.append("MODELO_AUSENTE")

    try:
        model_path = installed_model_path()
        detector = create_detector(model_path)
        checks["ner_carregado"] = bool(getattr(detector, "ner_ready", False))
        if not checks["ner_carregado"]:
            falhas.append("NER_NAO_CARREGADO")
        else:
            result = Sanitizer(detector, require_ner=True).sanitize(_SYNTHETIC_CASE)
            secrets_absent = all(
                secret not in (result.sanitized_text or "") for secret in _SYNTHETIC_SECRETS
            )
            checks["caso_sintetico"] = (
                result.state is JobState.PASS
                and result.sanitized_text is not None
                and secrets_absent
            )
            if not checks["caso_sintetico"]:
                falhas.append("CASO_SINTETICO_FALHOU")
    except Exception:
        falhas.append("DIAGNOSTICO_FALHOU")
        checks["ner_carregado"] = False
        checks["caso_sintetico"] = False

    module_ok = all(value is not False for key, value in checks.items() if key != "python")
    ok = module_ok and not falhas
    return {
        "ok": ok,
        "versao": __version__,
        "processamento": "local",
        "verificacoes": checks,
        "falhas": falhas,
        "aviso": ("Nunca anexe dados de pacientes ao chat; use somente a interface localhost."),
    }


__all__ = ["diagnose"]
