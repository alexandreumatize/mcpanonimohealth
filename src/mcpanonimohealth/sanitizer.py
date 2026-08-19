"""Sanitização local: mascara identificadores e libera clínica, idade e sexo."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from .detection import Detector, _resolve_overlaps, create_detector, is_ner_ready
from .domain import EntityType, Finding, JobState, SanitizationResult, Span

_DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d")
_MAX_MASK_ROUNDS = 8
# Idade, sexo e datas clínicas/documento ficam para dataset; nascimento segue mascarado.
_PRESERVE_ENTITIES = frozenset({EntityType.AGE, EntityType.DATE})
# Nomes detectados uma vez devem ser mascarados em todas as ocorrências do PDF.
_NAME_ENTITIES = frozenset(
    {EntityType.PATIENT, EntityType.PERSON, EntityType.DOCTOR}
)
_NAME_ENTITY_PRIORITY = {
    EntityType.PATIENT: 0,
    EntityType.PERSON: 1,
    EntityType.DOCTOR: 2,
}
_LETTER = r"A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇáàâãéèêíìîóòôõúùûç"
_NAME_PARTICLES = frozenset({"da", "de", "do", "das", "dos", "e", "di", "du"})
_ORG_NAME_STARTERS = frozenset(
    {
        "clinica",
        "clínica",
        "hospital",
        "laboratorio",
        "laboratório",
        "maternidade",
        "policlinica",
        "policlínica",
        "ubs",
        "upa",
        "secretaria",
        "governo",
        "goyerno",
        "coordenadoria",
    }
)
_CLINICAL_NOISE = frozenset(
    {
        "mg",
        "ml",
        "mcg",
        "ui",
        "sc",
        "ev",
        "vo",
        "kg",
        "cm",
        "mm",
        "g",
        "dp",
        "cp",
        "amp",
        "fr",
        "cx",
    }
)
_CANDIDATE_NAME = re.compile(
    rf"(?<![{_LETTER}])"
    rf"(?-i:[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ])[{_LETTER}'-]+"
    rf"(?:[ \t]+(?:(?:d[aeo]s?|e)[ \t]+)?(?-i:[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ])[{_LETTER}'-]+){{1,5}}"
    rf"(?![{_LETTER}])"
)
_FIRST_NAME_NEAR_CRM = re.compile(
    rf"(?<![{_LETTER}])"
    rf"(?P<value>(?-i:[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ])[{_LETTER}'-]{{5,}})"
    rf"(?![{_LETTER}])"
    rf"(?:[ \t]+(?:\[[A-Z0-9_+\-]+\][ \t]*)*)"
    rf"(?=\b(?:c\s*)?r\s*m\b|\brqe\b|\broe\b|\bcrmsp\b|\[CRM_)",
    re.IGNORECASE | re.UNICODE,
)


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


def _is_clinical_noise(value: str) -> bool:
    cleaned = re.sub(r"\s+", " ", value.strip()).casefold()
    if not cleaned:
        return True
    if cleaned in _CLINICAL_NOISE:
        return True
    parts = cleaned.split()
    if parts and all(part in _CLINICAL_NOISE for part in parts):
        return True
    return bool(re.fullmatch(r"\d+\s*(?:mg|ml|mcg|ui|g)(?:\s*(?:sc|ev|vo))?", cleaned))


def _after_dose_number(text: str, start: int) -> bool:
    return re.search(r"\d{1,4}\s*$", text[max(0, start - 8) : start]) is not None


def _maskable(text: str, findings: tuple[Finding, ...] | list[Finding]) -> list[Finding]:
    kept: list[Finding] = []
    for finding in findings:
        if finding.entity in _PRESERVE_ENTITIES:
            continue
        value = text[finding.span.start : finding.span.end]
        if finding.entity is EntityType.ADDRESS:
            if _is_clinical_noise(value):
                continue
            # "denosumabe 60 mg|SC" — unidade/via após dose não é endereço.
            if _after_dose_number(text, finding.span.start) and len(value.strip()) <= 12:
                continue
        kept.append(finding)
    return kept


def _is_propagatable_name(value: str) -> bool:
    cleaned = re.sub(r"\s+", " ", value.strip())
    if len(cleaned) < 8 or re.search(r"\d", cleaned):
        return False
    words = cleaned.split()
    if len(words) < 2:
        return False
    if words[0].casefold() in _ORG_NAME_STARTERS:
        return False
    for word in words:
        if not re.fullmatch(rf"[{_LETTER}'-]+", word):
            return False
        if word.casefold() in _NAME_PARTICLES:
            continue
        # Evita eco de trechos que engoliram verbo/frase após o nome.
        if not word[0].isupper():
            return False
    return True


def _name_echo_pattern(value: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in re.split(r"\s+", value.strip()) if word]
    body = r"[\s]+".join(words)
    return re.compile(
        rf"(?<![{_LETTER}]){body}(?![{_LETTER}])",
        re.IGNORECASE | re.UNICODE,
    )


def _fold_name_token(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return ascii_only.casefold().replace("-", "").replace("'", "")


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def _token_similar(left: str, right: str) -> bool:
    a = _fold_name_token(left)
    b = _fold_name_token(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if abs(len(a) - len(b)) > 3:
        return False
    limit = 3 if min(len(a), len(b)) >= 8 else 2
    if min(len(a), len(b)) >= 5 and (a.startswith(b[:4]) or b.startswith(a[:4])):
        limit = max(limit, 3)
    return _levenshtein(a, b) <= limit


def _content_name_words(value: str) -> list[str]:
    return [
        word
        for word in re.split(r"\s+", value.strip())
        if word and word.casefold() not in _NAME_PARTICLES
    ]


def _names_fuzzy_match(candidate: str, seed: str) -> bool:
    candidate_words = _content_name_words(candidate)
    seed_words = _content_name_words(seed)
    if len(candidate_words) < 2 or len(seed_words) < 2:
        return False
    surname_hit = any(
        _token_similar(candidate_word, seed_word)
        for candidate_word in candidate_words[1:]
        for seed_word in seed_words[1:]
    )
    if not surname_hit:
        return False
    if _token_similar(candidate_words[0], seed_words[0]):
        return True
    # OCR severo no prenome, mas sobrenome ancora (ex.: Alexanaed-ima + Matos).
    first_candidate = _fold_name_token(candidate_words[0])
    first_seed = _fold_name_token(seed_words[0])
    if len(first_candidate) < 6 or len(first_seed) < 6:
        return False
    if first_candidate[:4] != first_seed[:4]:
        return False
    return _levenshtein(first_candidate, first_seed) <= 5


def _collect_name_seeds(
    text: str, findings: tuple[Finding, ...]
) -> dict[str, tuple[EntityType, str]]:
    seeds: dict[str, tuple[EntityType, str]] = {}
    for finding in findings:
        if finding.entity not in _NAME_ENTITIES:
            continue
        value = text[finding.span.start : finding.span.end]
        if not _is_propagatable_name(value):
            continue
        surface = re.sub(r"\s+", " ", value.strip())
        key = unicodedata.normalize("NFKC", surface).casefold()
        existing = seeds.get(key)
        if existing is None:
            seeds[key] = (finding.entity, surface)
            continue
        current_entity, current_surface = existing
        preferred_entity = (
            finding.entity
            if _NAME_ENTITY_PRIORITY.get(finding.entity, 9)
            < _NAME_ENTITY_PRIORITY.get(current_entity, 9)
            else current_entity
        )
        preferred_surface = (
            surface if len(surface) >= len(current_surface) else current_surface
        )
        seeds[key] = (preferred_entity, preferred_surface)
    return seeds


def _expand_name_echoes(text: str, findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """Propaga nomes já detectados para eco exato, OCR aproximado e 1º nome perto de CRM."""

    seeds = _collect_name_seeds(text, findings)
    if not seeds:
        return findings

    extras: list[Finding] = []
    for entity, surface in seeds.values():
        for match in _name_echo_pattern(surface).finditer(text):
            extras.append(
                Finding(
                    entity=entity,
                    span=Span(match.start(), match.end()),
                    confidence=0.99,
                    source="deterministic:name_echo",
                )
            )

        for match in _CANDIDATE_NAME.finditer(text):
            candidate = match.group(0)
            if re.search(r"\b(?:crm|rqe|roe|cnes|dr|dra)\b", candidate, flags=re.IGNORECASE):
                continue
            if not _names_fuzzy_match(candidate, surface):
                continue
            extras.append(
                Finding(
                    entity=entity,
                    span=Span(match.start(), match.end()),
                    confidence=0.97,
                    source="deterministic:name_echo_fuzzy",
                )
            )

        first_name = (_content_name_words(surface) or [""])[0]
        if entity is EntityType.DOCTOR and len(_fold_name_token(first_name)) >= 6:
            for match in _FIRST_NAME_NEAR_CRM.finditer(text):
                if not _token_similar(match.group("value"), first_name):
                    continue
                extras.append(
                    Finding(
                        entity=entity,
                        span=Span(*match.span("value")),
                        confidence=0.96,
                        source="deterministic:doctor_firstname_near_crm",
                    )
                )

    if not extras:
        return findings
    return _resolve_overlaps((*findings, *extras))


def _is_propagatable_address(value: str) -> bool:
    cleaned = re.sub(r"\s+", " ", value.strip())
    if len(cleaned) < 5 or cleaned.isdigit():
        return False
    if re.fullmatch(r"[A-Z]{2}", cleaned, flags=re.IGNORECASE):
        return False
    if _is_clinical_noise(cleaned):
        return False
    return re.search(rf"[{_LETTER}]", cleaned) is not None


def _expand_address_echoes(text: str, findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """Propaga bairro/cidade/complemento já detectados para outras ocorrências no PDF."""

    seeds: dict[str, str] = {}
    for finding in findings:
        if finding.entity is not EntityType.ADDRESS:
            continue
        value = text[finding.span.start : finding.span.end]
        if not _is_propagatable_address(value):
            continue
        surface = re.sub(r"\s+", " ", value.strip())
        key = unicodedata.normalize("NFKC", surface).casefold()
        current = seeds.get(key)
        if current is None or len(surface) > len(current):
            seeds[key] = surface
    if not seeds:
        return findings

    extras: list[Finding] = []
    for surface in seeds.values():
        for match in _name_echo_pattern(surface).finditer(text):
            extras.append(
                Finding(
                    entity=EntityType.ADDRESS,
                    span=Span(match.start(), match.end()),
                    confidence=0.98,
                    source="deterministic:address_echo",
                )
            )
    if not extras:
        return findings
    return _resolve_overlaps((*findings, *extras))


def _crm_echo_pattern(digits: str) -> re.Pattern[str] | None:
    if len(digits) < 5 or not digits.isdigit():
        return None
    if len(digits) == 6:
        body = rf"{digits[:3]}[.\s]?{digits[3:]}"
    elif len(digits) == 7:
        body = rf"{digits[:3]}[.\s]?{digits[3:6]}[.\s]?{digits[6:]}"
    else:
        body = re.escape(digits)
    return re.compile(rf"(?<!\d){body}(?!\d)")


def _expand_crm_echoes(text: str, findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """Propaga número de CRM em formatos com/sem pontuação."""

    extras: list[Finding] = []
    seen_digits: set[str] = set()
    for finding in findings:
        if finding.entity is not EntityType.CRM:
            continue
        digits = "".join(
            character
            for character in text[finding.span.start : finding.span.end]
            if character.isdigit()
        )
        if digits in seen_digits:
            continue
        seen_digits.add(digits)
        pattern = _crm_echo_pattern(digits)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            extras.append(
                Finding(
                    entity=EntityType.CRM,
                    span=Span(match.start(), match.end()),
                    confidence=0.99,
                    source="deterministic:crm_echo",
                )
            )
    if not extras:
        return findings
    return _resolve_overlaps((*findings, *extras))


def _expand_echoes(text: str, findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    return _expand_crm_echoes(
        text, _expand_address_echoes(text, _expand_name_echoes(text, findings))
    )


class Sanitizer:
    """Desidentificador orientado a liberar clínica útil, não a bloquear o documento."""

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

        reasons: list[str] = []
        if self._require_ner and not self.ner_ready:
            # Continua com o detector disponível (em geral só determinístico).
            reasons.append("NER_NOT_READY_CONTINUED")

        findings = _expand_echoes(text, self._detector.detect(text))
        registry = _TokenRegistry(_date_anchor(text, findings))
        counts: Counter[str] = Counter()
        sanitized = text
        all_findings: list[Finding] = list(findings)

        for _round in range(_MAX_MASK_ROUNDS):
            expanded = _expand_echoes(sanitized, self._detector.detect(sanitized))
            maskable = _maskable(sanitized, expanded)
            if not maskable:
                break
            for finding in reversed(maskable):
                start, end = finding.span.start, finding.span.end
                value = sanitized[start:end]
                token = registry.token_for(finding.entity, value)
                sanitized = f"{sanitized[:start]}{token}{sanitized[end:]}"
                counts[finding.entity.value] += 1
                all_findings.append(finding)

        # Remove marcadores antes de um último scan informativo (não bloqueia).
        residual_scan = re.sub(r"\[[A-Z0-9_+\-]+\]", " x ", sanitized)
        residual = _maskable(
            residual_scan,
            _expand_echoes(residual_scan, self._detector.detect(residual_scan)),
        )
        if residual:
            residual_types = sorted({finding.entity.value for finding in residual})
            reasons.extend(f"RESIDUAL_BEST_EFFORT_{entity}" for entity in residual_types)

        return SanitizationResult(
            state=JobState.PASS,
            sanitized_text=sanitized,
            findings=tuple(all_findings),
            residual_findings=(),
            reasons=tuple(reasons),
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
