from __future__ import annotations

from datetime import date
from pathlib import Path

from mcpanonimohealth.batch import process_batch
from mcpanonimohealth.detection import DeterministicDetector
from mcpanonimohealth.organize import (
    build_organization,
    infer_document_type,
    patient_initials,
)
from mcpanonimohealth.sanitizer import Sanitizer


def test_patient_initials_include_particules() -> None:
    assert patient_initials("Maria da Silva") == "MDS"
    assert patient_initials("João Pedro de Oliveira") == "JPDO"


def test_infer_document_type_from_keywords() -> None:
    assert infer_document_type("Receita médica\nProlia 60 mg") == "receita"
    assert infer_document_type("Formulário para Avaliação") == "formulario"
    assert infer_document_type("RELATÓRIO MÉDICO — AO SUS") == "relatorio"


def test_build_organization_uses_initials_type_and_date() -> None:
    text = """
Paciente: Maria da Silva
Emissão: 24/04/2026
Receita de medicamento
CID M80.0
"""
    org = build_organization(text)
    assert org.initials == "MDS"
    assert org.doc_type == "receita"
    assert org.doc_date == date(2026, 4, 24)
    assert org.relative_path == "MDS/receita_2026-04-24.txt"


def test_process_batch_writes_organized_deidentified_files(tmp_path: Path) -> None:
    source = tmp_path / "entrada"
    destination = tmp_path / "saida"
    source.mkdir()
    (source / "caso.txt").write_text(
        "Paciente: Maria da Silva\n"
        "CPF: 529.982.247-25\n"
        "Emissão: 10/08/2026\n"
        "Receita: metotrexato 15 mg\n"
        "Idade: 55 anos\n"
        "Sexo: feminino\n",
        encoding="utf-8",
    )
    result = process_batch(
        source,
        destination,
        sanitizer=Sanitizer(DeterministicDetector(), require_ner=False),
    )
    assert result.liberados == 1
    assert result.erros == 0
    target = destination / "MDS" / "receita_2026-08-10.txt"
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "Maria da Silva" not in content
    assert "529.982.247-25" not in content
    assert "metotrexato" in content.lower()
    assert "55 anos" in content
    assert (destination / "manifesto_lote.json").is_file()
