"""Tipos de domínio seguros para o núcleo de desidentificação.

Os objetos deliberadamente não carregam o trecho original detectado. O texto
bruto existe somente durante a chamada de sanitização e não deve ser incluído
em logs, exceções ou respostas MCP.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class JobState(StrEnum):
    """Estados públicos de um trabalho de desidentificação."""

    PROCESSING = "PROCESSING"
    PASS = "PASS"  # noqa: S105 - estado de processamento, não credencial
    HOLD = "HOLD"
    ERROR = "ERROR"
    EXPIRED = "EXPIRED"


class EntityType(StrEnum):
    """Classes de identificadores que podem ser substituídas."""

    CPF = "CPF"
    CNS = "CNS"
    EMAIL = "EMAIL"
    PHONE = "TELEFONE"
    POSTAL_CODE = "CEP"
    CRM = "CRM"
    RG = "RG"
    RECORD_ID = "PRONTUARIO"
    ORDER_ID = "PEDIDO"
    URL = "URL"
    IP_ADDRESS = "ENDERECO_IP"
    USERNAME = "USUARIO"
    CREDENTIAL = "CREDENCIAL"
    GENERIC_ID = "IDENTIFICADOR"
    PATIENT = "PACIENTE"
    DOCTOR = "MEDICO"
    PERSON = "PESSOA"
    INSTITUTION = "SERVICO"
    ADDRESS = "ENDERECO"
    DATE_OF_BIRTH = "DATA_NASCIMENTO"
    DATE = "DATA"
    AGE = "IDADE"


@dataclass(frozen=True, slots=True, order=True)
class Span:
    """Intervalo semiaberto no texto de entrada: ``start <= i < end``."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("invalid text span")

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: Span) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class Finding:
    """Achado sem o valor sensível correspondente.

    ``source`` descreve o detector, nunca o conteúdo encontrado.
    """

    entity: EntityType
    span: Span
    confidence: float
    source: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.source or len(self.source) > 80:
            raise ValueError("invalid detector source")


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    """Resultado que pode atravessar a fronteira do sanitizador.

    Em ``HOLD``, ``sanitized_text`` é sempre ``None`` para impedir liberação
    acidental de conteúdo com risco residual.
    """

    state: JobState
    sanitized_text: str | None
    findings: tuple[Finding, ...] = ()
    residual_findings: tuple[Finding, ...] = ()
    reasons: tuple[str, ...] = ()
    replacements: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state is JobState.PASS and self.sanitized_text is None:
            raise ValueError("PASS requires sanitized text")
        if self.state is not JobState.PASS and self.sanitized_text is not None:
            raise ValueError("non-PASS results cannot expose text")
        if self.state is JobState.PASS and self.residual_findings:
            raise ValueError("PASS cannot contain residual findings")
        object.__setattr__(self, "replacements", MappingProxyType(dict(self.replacements)))
