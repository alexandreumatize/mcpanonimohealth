"""CLI de instalação, diagnóstico e inicialização do servidor MCP."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.util import find_spec

from . import __version__


def _doctor() -> int:
    try:
        from .models import installed_model_path

        openmed_ready = installed_model_path() is not None
    except Exception:
        openmed_ready = False
    checks = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "mcp": find_spec("mcp") is not None,
        "ocr": find_spec("rapidocr") is not None,
        "pdf": find_spec("pypdfium2") is not None,
        "backend": find_spec("mcpanonimohealth.jobs") is not None,
        "modelo_openmed_local": openmed_ready,
    }
    ok = all(value is not False for value in checks.values())
    print(
        json.dumps(
            {
                "ok": ok,
                "versao": __version__,
                "processamento": "local",
                "verificacoes": checks,
                "aviso": (
                    "Nunca anexe dados de pacientes ao chat; use somente a interface localhost."
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


def _install_models() -> int:
    """Pré-carrega o OCR e instala o detector OpenMed no diretório do usuário."""
    try:
        from rapidocr import RapidOCR

        from .models import install_openmed_model

        RapidOCR()
        install_openmed_model()
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "codigo": "MODELO_LOCAL_INDISPONIVEL",
                    "mensagem": "Não foi possível preparar os modelos locais.",
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {"ok": True, "mensagem": "Modelos locais preparados.", "processamento": "local"},
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcpanonimohealth",
        description="Desidentificação local antes do uso de IA.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="inicia o servidor MCP local por stdio")
    commands.add_parser("doctor", help="verifica a instalação sem abrir documentos")
    models = commands.add_parser("models", help="gerencia modelos locais")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    model_commands.add_parser("install", help="baixa e prepara modelos locais")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from .server import main as serve

        serve()
        return 0
    if args.command == "doctor":
        return _doctor()
    if args.command == "models" and args.models_command == "install":
        return _install_models()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
