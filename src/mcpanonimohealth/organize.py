"""Metadados de organização local sem persistir o nome completo do paciente."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .detection import DeterministicDetector
from .domain import EntityType

_DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d")
_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("receita", re.compile(r"\breceita\b|prescri[cç][aã]o|prolia\b", re.I)),
    ("formulario", re.compile(r"formul[aá]rio", re.I)),
    ("relatorio", re.compile(r"relat[oó]rio\s+m[eé]dico|\brelat[oó]rio\b", re.I)),
    ("declaracao", re.compile(r"declara[cç][aã]o", re.I)),
    ("laudo", re.compile(r"\blaudo\b|densitometr|\bdmo\b", re.I)),
    ("pedido", re.compile(r"solicita[cç][aã]o\s+de\s+medicamento|pedido\s+de\s+medicamento", re.I)),
    ("consulta", re.compile(r"\bconsulta\b|atendimento\s+ambulatorial", re.I)),
)
_LABELED_DATE = re.compile(
    r"(?:emiss[aã]o|consulta|data\s+d[ao]\s+exame|data\s+d[ao]\s+atendimento|"
    r"data\s+d[oa]\s+documento|data)\s*[:\-]?\s*"
    r"(?P<value>(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}|"
    r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))",
    re.I,
)


@dataclass(frozen=True, slots=True)
class DocumentOrganization:
    """Como gravar o derivado localmente, sem o nome completo."""

    initials: str
    doc_type: str
    doc_date: date
    relative_path: str


def patient_initials(name: str) -> str:
    """Gera iniciais a partir do nome detectado (inclui partículas: Maria da Silva → MDS)."""

    normalized = unicodedata.normalize("NFKC", name).strip()
    parts = [part for part in re.split(r"\s+", normalized) if part]
    letters = [part[0].upper() for part in parts if part[:1].isalpha()]
    initials = "".join(letters)[:8]
    return initials or "SEM_ID"


def infer_document_type(text: str) -> str:
    for label, pattern in _TYPE_PATTERNS:
        if pattern.search(text):
            return label
    return "documento"


def _parse_date(value: str) -> date | None:
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).date()
        except ValueError:
            continue
    return None


def infer_document_date(text: str, *, fallback: date | None = None) -> date:
    match = _LABELED_DATE.search(text)
    if match:
        parsed = _parse_date(match.group("value"))
        if parsed is not None:
            return parsed
    detector = DeterministicDetector()
    for finding in detector.detect(text):
        if finding.entity is EntityType.DATE:
            parsed = _parse_date(text[finding.span.start : finding.span.end])
            if parsed is not None and parsed.year >= 1990:
                return parsed
    return fallback or date.today()


def infer_patient_initials(text: str) -> str:
    detector = DeterministicDetector()
    for finding in detector.detect(text):
        if finding.entity in {EntityType.PATIENT, EntityType.PERSON}:
            return patient_initials(text[finding.span.start : finding.span.end])
    # Fallback: "Nome completo: ..."
    labeled = re.search(
        r"(?:nome\s+completo|paciente)\s*[:\-]\s*([A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ][^\n]{3,80})",
        text,
        re.I,
    )
    if labeled:
        return patient_initials(labeled.group(1))
    return "SEM_ID"


def build_organization(
    text: str,
    *,
    source_mtime: float | None = None,
) -> DocumentOrganization:
    fallback = (
        datetime.fromtimestamp(source_mtime).date() if source_mtime is not None else date.today()
    )
    initials = infer_patient_initials(text)
    doc_type = infer_document_type(text)
    doc_date = infer_document_date(text, fallback=fallback)
    relative = f"{initials}/{doc_type}_{doc_date.isoformat()}.txt"
    return DocumentOrganization(
        initials=initials,
        doc_type=doc_type,
        doc_date=doc_date,
        relative_path=relative,
    )


def unique_target(output_root: Path, relative_path: str) -> Path:
    """Evita sobrescrever quando há vários documentos do mesmo tipo/dia."""

    candidate = output_root / relative_path
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    index = 2
    while True:
        alternate = parent / f"{stem}_{index}{suffix}"
        if not alternate.exists():
            return alternate
        index += 1


__all__ = [
    "DocumentOrganization",
    "build_organization",
    "infer_document_date",
    "infer_document_type",
    "infer_patient_initials",
    "patient_initials",
    "unique_target",
]
