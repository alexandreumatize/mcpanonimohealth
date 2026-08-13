"""Sanitização local com liberação bloqueada por risco residual."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from .detection import Detector, create_detector, is_ner_ready
from .domain import EntityType, Finding, JobState, SanitizationResult

_DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d")


def _normalized_key(entity: EntityType, value: str) -> tuple[EntityType, str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    if entity in {
        EntityType.CPF,
        EntityType.CNS,
        EntityType.PHONE,
        EntityType.POSTAL_CODE,
        EntityType.CRM,
        EntityType.RG,
    }:
        normalized = "".join(character for character in normalized if character.isalnum())
    else:
        normalized = re.sub(r"\s+", " ", normalized)
    return entity, normalized


def _parse_date(value: str) -> date | None:
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).date()
        except ValueError:
            continue
    return None


def _parse_age(value: str) -> int | None:
    match = re.search(r"\d{1,3}", value)
    if match is None:
        return None
    age = int(match.group())
    return age if 0 <= age <= 125 else None


class _TokenRegistry:
    """Gera pseudônimos estáveis somente durante uma sanitização."""

    def __init__(self, date_anchor: date | None) -> None:
        self._date_anchor = date_anchor
        self._tokens: dict[tuple[EntityType, str], str] = {}
        self._next_index: defaultdict[EntityType, int] = defaultdict(int)

    def token_for(self, entity: EntityType, value: str) -> str:
        key = _normalized_key(entity, value)
        if key in self._tokens:
            return self._tokens[key]

        token = self._new_token(entity, value)
        self._tokens[key] = token
        return token

    def _new_token(self, entity: EntityType, value: str) -> str:
        if entity is EntityType.DATE_OF_BIRTH:
            return "[DATA_NASCIMENTO]"

        if entity is EntityType.DATE:
            parsed = _parse_date(value)
            if parsed is not None and self._date_anchor is not None:
                offset = (parsed - self._date_anchor).days
                suffix = "D0" if offset == 0 else f"D{offset:+d}"
                return f"[DATA_{suffix}]"

        if entity is EntityType.AGE:
            age = _parse_age(value)
            if age is not None:
                lower = (age // 10) * 10
                upper = min(lower + 9, 129)
                return f"[FAIXA_ETARIA_{lower}_{upper}]"

        self._next_index[entity] += 1
        return f"[{entity.value}_{self._next_index[entity]:03d}]"


def _date_anchor(text: str, findings: tuple[Finding, ...]) -> date | None:
    # A primeira data clínica no documento define D0; nascimento nunca ancora a
    # linha temporal para não revelar indiretamente a idade exata.
    for finding in findings:
        if finding.entity is EntityType.DATE:
            if parsed := _parse_date(text[finding.span.start : finding.span.end]):
                return parsed
    return None


class Sanitizer:
    """Desidentificador de texto com segundo passe fail-closed."""

    def __init__(
        self,
        detector: Detector | None = None,
        *,
        openmed_model_path: str | Path | None = None,
        require_ner: bool = True,
    ) -> None:
        if detector is not None and openmed_model_path is not None:
            raise ValueError("provide detector or openmed_model_path, not both")
        self._detector = detector or create_detector(openmed_model_path)
        self._require_ner = require_ner

    @property
    def ner_ready(self) -> bool:
        """Verdadeiro somente quando uma camada NER local está operacional."""

        return is_ner_ready(self._detector)

    def sanitize(self, text: str) -> SanitizationResult:
        if not isinstance(text, str):
            raise TypeError("text must be str")
        if not text.strip():
            return SanitizationResult(
                state=JobState.HOLD,
                sanitized_text=None,
                reasons=("EMPTY_INPUT",),
            )

        # Fail-closed: regras cobrem identificadores estruturados, mas não são
        # suficientes para liberar prosa clínica arbitrária ao agente na nuvem.
        if self._require_ner and not self.ner_ready:
            return SanitizationResult(
                state=JobState.HOLD,
                sanitized_text=None,
                reasons=("NER_NOT_READY",),
            )

        findings = self._detector.detect(text)
        if self._require_ner and not self.ner_ready:
            return SanitizationResult(
                state=JobState.HOLD,
                sanitized_text=None,
                reasons=("NER_RUNTIME_FAILURE",),
            )
        registry = _TokenRegistry(_date_anchor(text, findings))
        counts: Counter[str] = Counter()

        sanitized = text
        for finding in reversed(findings):
            start, end = finding.span.start, finding.span.end
            token = registry.token_for(finding.entity, text[start:end])
            sanitized = f"{sanitized[:start]}{token}{sanitized[end:]}"
            counts[finding.entity.value] += 1

        # Tokens são marcadores controlados pelo programa, não conteúdo do
        # documento. Alguns NERs confundem sinais como ``+`` em ``[DATA_D+7]``
        # com telefone; remova somente esses marcadores antes do segundo passe.
        residual_scan = re.sub(r"\[[A-Z0-9_+\-]+\]", " x ", sanitized)
        residual = self._detector.detect(residual_scan)
        if self._require_ner and not self.ner_ready:
            return SanitizationResult(
                state=JobState.HOLD,
                sanitized_text=None,
                findings=findings,
                reasons=("NER_RUNTIME_FAILURE",),
                replacements=dict(counts),
            )
        if residual:
            residual_types = sorted({finding.entity.value for finding in residual})
            reasons = tuple(f"RESIDUAL_{entity}" for entity in residual_types)
            return SanitizationResult(
                state=JobState.HOLD,
                sanitized_text=None,
                findings=findings,
                residual_findings=residual,
                reasons=reasons,
                replacements=dict(counts),
            )

        return SanitizationResult(
            state=JobState.PASS,
            sanitized_text=sanitized,
            findings=findings,
            replacements=dict(counts),
        )


def sanitize_text(
    text: str,
    *,
    detector: Detector | None = None,
    openmed_model_path: str | Path | None = None,
    require_ner: bool = True,
) -> SanitizationResult:
    """API funcional para sanitizar um único texto em memória."""

    return Sanitizer(
        detector,
        openmed_model_path=openmed_model_path,
        require_ner=require_ner,
    ).sanitize(text)


__all__ = ["Sanitizer", "sanitize_text"]
