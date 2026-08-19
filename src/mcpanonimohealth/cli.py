"""CLI de instalação, diagnóstico, lote e servidor MCP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__


def _doctor() -> int:
    from .diagnose import diagnose

    report = diagnose()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


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


def _batch(input_dir: str | None, output_dir: str | None) -> int:
    from .batch import process_batch
    from .selectors import SelectionCancelled, select_local_directory

    try:
        source = (
            Path(input_dir)
            if input_dir
            else select_local_directory(
                prompt="Selecione a pasta de documentos originais (processamento local)"
            )
        )
        destination = (
            Path(output_dir)
            if output_dir
            else select_local_directory(
                prompt="Selecione a pasta de saída dos textos desidentificados"
            )
        )
        result = process_batch(source, destination)
    except SelectionCancelled:
        print(
            json.dumps(
                {
                    "ok": False,
                    "codigo": "SELECAO_CANCELADA",
                    "mensagem": "Seleção de pasta cancelada.",
                },
                ensure_ascii=False,
            )
        )
        return 1
    except TimeoutError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "codigo": "SELECAO_TIMEOUT",
                    "mensagem": "Tempo esgotado no seletor de pastas. Tente de novo.",
                },
                ensure_ascii=False,
            )
        )
        return 1
    except ValueError as exc:
        print(
            json.dumps(
                {"ok": False, "codigo": str(exc), "mensagem": "Pasta de entrada inválida."},
                ensure_ascii=False,
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "codigo": "LOTE_FALHOU",
                    "mensagem": "Não foi possível processar o lote localmente.",
                },
                ensure_ascii=False,
            )
        )
        return 1
    payload = result.public()
    payload["saida"] = "local"
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcpanonimohealth",
        description="Desidentificação local antes do uso de IA.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="inicia o servidor MCP local por stdio")
    commands.add_parser("doctor", help="verifica a instalação sem abrir documentos")
    batch = commands.add_parser(
        "batch",
        help="desidentifica uma pasta e organiza a saída por iniciais/tipo/data",
    )
    batch.add_argument(
        "--input",
        dest="input_dir",
        help="pasta de entrada (se omitida, abre seletor nativo)",
    )
    batch.add_argument(
        "--output",
        dest="output_dir",
        help="pasta de saída (se omitida, abre seletor nativo)",
    )
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
    if args.command == "batch":
        return _batch(args.input_dir, args.output_dir)
    if args.command == "models" and args.models_command == "install":
        return _install_models()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
