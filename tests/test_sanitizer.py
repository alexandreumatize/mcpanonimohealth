from __future__ import annotations

import re

import pytest

from mcpanonimohealth.detection import DeterministicDetector, is_valid_cns, is_valid_cpf
from mcpanonimohealth.domain import JobState
from mcpanonimohealth.sanitizer import Sanitizer


@pytest.fixture
def sanitizer() -> Sanitizer:
    return Sanitizer(DeterministicDetector(), require_ner=False)


def test_validates_brazilian_checksums() -> None:
    assert is_valid_cpf("529.982.247-25")
    assert not is_valid_cpf("111.111.111-11")
    assert is_valid_cns("100 0000 0000 0007")
    assert not is_valid_cns("111 1111 1111 1111")


def test_masks_seeded_identifiers(sanitizer: Sanitizer) -> None:
    raw = """Paciente: Maria da Silva
Nascimento: 03/02/1982
CPF: 529.982.247-25
Telefone: (11) 99876-5432
E-mail: maria.silva@example.com
CEP: 01310-100
Prontuário: ABC-77891
Médica: Dra. Beatriz Oliveira
Instituição: Hospital Sintético Central
Consulta: 10/08/2026
Retorno: 20/08/2026
"""

    result = sanitizer.sanitize(raw)

    assert result.state is JobState.PASS
    assert result.sanitized_text is not None
    for secret in (
        "Maria da Silva",
        "529.982.247-25",
        "99876-5432",
        "maria.silva@example.com",
        "01310-100",
        "ABC-77891",
        "Beatriz Oliveira",
        "Hospital Sintético Central",
    ):
        assert secret not in result.sanitized_text
    assert "[PACIENTE_001]" in result.sanitized_text
    assert "[DATA_NASCIMENTO]" in result.sanitized_text
    assert "[DATA_D0]" in result.sanitized_text
    assert "[DATA_D+10]" in result.sanitized_text


def test_same_identifier_gets_same_token(sanitizer: Sanitizer) -> None:
    result = sanitizer.sanitize(
        "Paciente: Maria da Silva\nMaria da Silva compareceu.\nPaciente: Maria da Silva"
    )
    assert result.state is JobState.PASS
    assert result.sanitized_text is not None
    tokens = re.findall(r"\[PACIENTE_\d{3}\]", result.sanitized_text)
    assert len(set(tokens)) == 1


def test_default_is_fail_closed_without_local_ner() -> None:
    result = Sanitizer(openmed_model_path=None).sanitize("Paciente sintético sem rótulo.")
    assert result.state is JobState.HOLD
    assert result.sanitized_text is None
    assert "NER_NOT_READY" in result.reasons


def test_empty_input_is_hold(sanitizer: Sanitizer) -> None:
    result = sanitizer.sanitize("  \n")
    assert result.state is JobState.HOLD
    assert result.sanitized_text is None
