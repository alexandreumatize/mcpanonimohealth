"""Detecção local de identificadores brasileiros.

O caminho padrão é inteiramente determinístico. O adaptador OpenMed é opcional,
carregado sob demanda e só aceita um diretório de modelo que já exista no disco;
assim, nenhuma chamada de processamento pode iniciar download ou acesso à rede.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import EntityType, Finding, Span


class Detector(Protocol):
    @property
    def ner_ready(self) -> bool: ...

    def detect(self, text: str) -> tuple[Finding, ...]: ...


_FLAGS = re.IGNORECASE | re.MULTILINE
_NAME_WORD = r"[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ][A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇáàâãéèêíìîóòôõúùûç'-]+"
# Espaço horizontal é intencional: nomes nunca podem consumir o rótulo da
# linha seguinte quando o OCR preserva quebras de linha.
_HSPACE = r"[ \t]"
_NAME = rf"{_NAME_WORD}(?:{_HSPACE}+(?:(?:d[aeo]s?|e){_HSPACE}+)?{_NAME_WORD}){{1,5}}"


@dataclass(frozen=True, slots=True)
class _Rule:
    name: str
    entity: EntityType
    regex: re.Pattern[str]
    confidence: float


def _compile(pattern: str, *, flags: int = _FLAGS) -> re.Pattern[str]:
    return re.compile(pattern, flags)


_CONTEXT_RULES: tuple[_Rule, ...] = (
    _Rule(
        "patient_label",
        EntityType.PATIENT,
        _compile(
            rf"(?:nome\s+d[oa]\s+paciente|paciente){_HSPACE}*[:\-]{_HSPACE}*"
            rf"(?P<value>{_NAME})"
        ),
        0.99,
    ),
    _Rule(
        "doctor_label",
        EntityType.DOCTOR,
        _compile(
            rf"(?:nome\s+d[oa]\s+m[eé]dic[oa]|m[eé]dic[oa]|dr\.?|dra\.?)"
            rf"{_HSPACE}*[:\-]?{_HSPACE}*(?P<value>{_NAME})"
        ),
        0.98,
    ),
    _Rule(
        "institution_label",
        EntityType.INSTITUTION,
        _compile(
            rf"(?:institui[cç][aã]o|estabelecimento|servi[cç]o|unidade){_HSPACE}*"
            rf"[:\-]{_HSPACE}*"
            r"(?P<value>[^\s\n;][^\n;]{2,99})"
        ),
        0.97,
    ),
    _Rule(
        "institution_prefix",
        EntityType.INSTITUTION,
        _compile(
            r"(?P<value>\b(?:hospital|cl[ií]nica|laborat[oó]rio|maternidade|policl[ií]nica|"
            r"unidade\s+b[aá]sica\s+de\s+sa[uú]de|ubs|upa)\s+"
            r"[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ][^\n,;]{2,80})"
        ),
        0.94,
    ),
    _Rule(
        "address_label",
        EntityType.ADDRESS,
        _compile(
            rf"(?:endere[cç]o|resid[eê]ncia|domic[ií]lio){_HSPACE}*[:\-]{_HSPACE}*"
            r"(?P<value>[^\s\n;][^\n;]{4,139})"
        ),
        0.99,
    ),
    _Rule(
        "birth_date_label",
        EntityType.DATE_OF_BIRTH,
        _compile(
            rf"(?:data\s+de\s+nascimento|nascimento|nasc\.?|dn){_HSPACE}*[:\-]"
            rf"{_HSPACE}*"
            r"(?P<value>(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}|"
            r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))"
        ),
        1.0,
    ),
)


_IDENTIFIER_RULES: tuple[_Rule, ...] = (
    _Rule(
        "email",
        EntityType.EMAIL,
        _compile(r"(?P<value>\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}\b)"),
        1.0,
    ),
    _Rule(
        "url",
        EntityType.URL,
        _compile(r"(?P<value>\b(?:https?://|www\.)[^\s<>\]\[\"']+)", flags=re.IGNORECASE),
        1.0,
    ),
    _Rule(
        "phone_formatted",
        EntityType.PHONE,
        _compile(
            r"(?P<value>(?<!\d)(?:\+?55[\s.-]?)?\(?[1-9]\d\)?[\s.-]?"
            r"(?:9\d{4}|[2-8]\d{3})[\s.-]?\d{4}(?!\d))"
        ),
        0.98,
    ),
    _Rule(
        "phone_labeled",
        EntityType.PHONE,
        _compile(r"(?:telefone|celular|fone|whatsapp)\s*[:\-]\s*(?P<value>\d{10,11})"),
        0.98,
    ),
    _Rule(
        "postal_code_formatted",
        EntityType.POSTAL_CODE,
        _compile(r"(?P<value>(?<!\d)\d{5}-\d{3}(?!\d))"),
        0.99,
    ),
    _Rule(
        "postal_code_labeled",
        EntityType.POSTAL_CODE,
        _compile(r"\bcep\s*[:\-]\s*(?P<value>\d{8})(?!\d)"),
        0.99,
    ),
    _Rule(
        "crm",
        EntityType.CRM,
        _compile(
            r"\bcrm(?:\s*[-/]?\s*[A-Z]{2})?\s*[:#\-]?\s*"
            r"(?P<value>\d{3,8}(?:\s*[-/]\s*[A-Z]{2})?)\b"
        ),
        0.99,
    ),
    _Rule(
        "rg_labeled",
        EntityType.RG,
        _compile(
            r"\b(?:rg|registro\s+geral)\s*[:#\-]?\s*"
            r"(?P<value>\d{1,2}\.?\d{3}\.?\d{3}[-\s]?[0-9Xx])\b"
        ),
        0.98,
    ),
    _Rule(
        "record_id",
        EntityType.RECORD_ID,
        _compile(
            r"\b(?:prontu[aá]rio|registro\s+d[oa]\s+paciente|id\s+d[oa]\s+paciente)"
            r"\s*[:#\-]?\s*(?P<value>[A-Z0-9][A-Z0-9._/\-]{2,31})"
        ),
        0.98,
    ),
    _Rule(
        "order_id",
        EntityType.ORDER_ID,
        _compile(
            r"\b(?:pedido|solicita[cç][aã]o|requisi[cç][aã]o|atendimento|protocolo)"
            r"\s*(?:n[ºo°]\.?|id)?\s*[:#\-]?\s*(?P<value>[A-Z0-9][A-Z0-9._/\-]{2,31})"
        ),
        0.96,
    ),
    _Rule(
        "date",
        EntityType.DATE,
        _compile(
            r"(?P<value>(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-]"
            r"(?:19|20)\d{2}(?!\d)|(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-"
            r"(?:0[1-9]|[12]\d|3[01])(?!\d))"
        ),
        0.96,
    ),
    _Rule(
        "age",
        EntityType.AGE,
        _compile(r"(?P<value>(?<!\d)(?:[0-9]|[1-9]\d|1[01]\d|12[0-5])\s*anos?\b)"),
        0.97,
    ),
)


_CPF_RE = _compile(r"(?P<value>(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d))")
_CNS_RE = _compile(r"(?P<value>(?<!\d)\d{3}\s?\d{4}\s?\d{4}\s?\d{4}(?!\d))")


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def is_valid_cpf(value: str) -> bool:
    """Valida os dois dígitos verificadores de um CPF."""

    digits = _digits(value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for index in (9, 10):
        weighted = sum(int(digits[pos]) * (index + 1 - pos) for pos in range(index))
        verifier = (weighted * 10) % 11
        if verifier == 10:
            verifier = 0
        if verifier != int(digits[index]):
            return False
    return True


def is_valid_cns(value: str) -> bool:
    """Valida um CNS pelo módulo 11 definido para seus 15 algarismos."""

    digits = _digits(value)
    if len(digits) != 15 or len(set(digits)) == 1:
        return False
    weighted_sum = sum(
        int(digit) * weight for digit, weight in zip(digits, range(15, 0, -1), strict=True)
    )
    return weighted_sum % 11 == 0


def _finding(rule: _Rule, match: re.Match[str]) -> Finding | None:
    start, end = match.span("value")
    value = match.group("value").strip()
    if not value or "[" in value or "]" in value:
        return None
    # Remove espaços capturados no fim de linhas contextuais sem alterar o texto.
    leading = len(match.group("value")) - len(match.group("value").lstrip())
    trailing = len(match.group("value")) - len(match.group("value").rstrip())
    return Finding(
        entity=rule.entity,
        span=Span(start + leading, end - trailing),
        confidence=rule.confidence,
        source=f"deterministic:{rule.name}",
    )


def _resolve_overlaps(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    # Confiança ganha; em empate, o maior trecho ganha. O resultado final volta
    # à ordem textual para permitir substituição determinística.
    ranked = sorted(
        findings,
        key=lambda item: (-item.confidence, -item.span.length, item.span.start, item.entity.value),
    )
    selected: list[Finding] = []
    for candidate in ranked:
        if not any(candidate.span.overlaps(existing.span) for existing in selected):
            selected.append(candidate)
    return tuple(sorted(selected, key=lambda item: (item.span.start, item.span.end)))


class DeterministicDetector:
    """Detector sem I/O e sem dependências de modelos."""

    @property
    def ner_ready(self) -> bool:
        """Regras não substituem uma camada NER para liberação documental."""

        return False

    def detect(self, text: str) -> tuple[Finding, ...]:
        if not text:
            return ()

        findings: list[Finding] = []
        for rule in (*_CONTEXT_RULES, *_IDENTIFIER_RULES):
            findings.extend(
                finding
                for match in rule.regex.finditer(text)
                if (finding := _finding(rule, match)) is not None
            )

        for match in _CPF_RE.finditer(text):
            if is_valid_cpf(match.group("value")):
                findings.append(
                    Finding(
                        EntityType.CPF,
                        Span(*match.span("value")),
                        1.0,
                        "deterministic:cpf_checksum",
                    )
                )

        for match in _CNS_RE.finditer(text):
            if is_valid_cns(match.group("value")):
                findings.append(
                    Finding(
                        EntityType.CNS,
                        Span(*match.span("value")),
                        1.0,
                        "deterministic:cns_checksum",
                    )
                )

        return _resolve_overlaps(findings)


_OPENMED_LABELS: dict[str, EntityType] = {
    # Categorias publicadas pela família OpenMed/AI4Privacy.
    "FIRSTNAME": EntityType.PERSON,
    "MIDDLENAME": EntityType.PERSON,
    "LASTNAME": EntityType.PERSON,
    "PREFIX": EntityType.PERSON,
    "AGE": EntityType.AGE,
    "CITY": EntityType.ADDRESS,
    "STATE": EntityType.ADDRESS,
    "COUNTY": EntityType.ADDRESS,
    "COUNTRY": EntityType.ADDRESS,
    "ZIPCODE": EntityType.POSTAL_CODE,
    "STREET": EntityType.ADDRESS,
    "BUILDINGNUMBER": EntityType.ADDRESS,
    "SECONDARYADDRESS": EntityType.ADDRESS,
    "DATEOFBIRTH": EntityType.DATE_OF_BIRTH,
    "SOCIALNUMBER": EntityType.GENERIC_ID,
    "SSN": EntityType.GENERIC_ID,
    "DRIVERLICENSE": EntityType.GENERIC_ID,
    "PASSPORT": EntityType.GENERIC_ID,
    "ACCOUNTNUMBER": EntityType.GENERIC_ID,
    "ACCOUNTNAME": EntityType.PERSON,
    "TAXID": EntityType.GENERIC_ID,
    "BIC": EntityType.GENERIC_ID,
    "IBAN": EntityType.GENERIC_ID,
    "CREDITCARDNUMBER": EntityType.GENERIC_ID,
    "CREDITCARDCVV": EntityType.CREDENTIAL,
    "PIN": EntityType.CREDENTIAL,
    "PHONENUMBER": EntityType.PHONE,
    "PHONEIMEI": EntityType.GENERIC_ID,
    "EMAILADDRESS": EntityType.EMAIL,
    "USERNAME": EntityType.USERNAME,
    "PASSWORD": EntityType.CREDENTIAL,
    "IP": EntityType.IP_ADDRESS,
    "IPADDRESS": EntityType.IP_ADDRESS,
    "IPV4": EntityType.IP_ADDRESS,
    "IPV6": EntityType.IP_ADDRESS,
    "MAC": EntityType.GENERIC_ID,
    "NEARBYGPSCOORDINATE": EntityType.ADDRESS,
    "VEHICLEIDENTIFICATIONNUMBER": EntityType.GENERIC_ID,
    "VEHICLEVRM": EntityType.GENERIC_ID,
    "PATIENT": EntityType.PATIENT,
    "PACIENTE": EntityType.PATIENT,
    "DOCTOR": EntityType.DOCTOR,
    "PHYSICIAN": EntityType.DOCTOR,
    "MEDICO": EntityType.DOCTOR,
    "MÉDICO": EntityType.DOCTOR,
    "PERSON": EntityType.PERSON,
    "PER": EntityType.PERSON,
    "NAME": EntityType.PERSON,
    "HOSPITAL": EntityType.INSTITUTION,
    "ORGANIZATION": EntityType.INSTITUTION,
    "ORG": EntityType.INSTITUTION,
    "INSTITUTION": EntityType.INSTITUTION,
    "LOCATION": EntityType.ADDRESS,
    "LOC": EntityType.ADDRESS,
    "ADDRESS": EntityType.ADDRESS,
    "DATE": EntityType.DATE,
    "DATE_OF_BIRTH": EntityType.DATE_OF_BIRTH,
    "DOB": EntityType.DATE_OF_BIRTH,
    "PHONE": EntityType.PHONE,
    "EMAIL": EntityType.EMAIL,
    "ID": EntityType.RECORD_ID,
}


class OpenMedAdapter:
    """Adaptador lazy e fail-closed para um modelo OpenMed já instalado.

    A ausência de ``transformers``, um diretório inválido ou um modelo
    incompatível apenas desativa esta camada; o detector determinístico segue
    funcionando. ``local_files_only=True`` impede downloads implícitos.
    """

    def __init__(self, model_path: str | Path, *, threshold: float = 0.45) -> None:
        self._model_path = Path(model_path).expanduser().resolve()
        self._threshold = threshold
        self._pipeline: Any | None = None
        self._disabled = not self._model_path.is_dir()
        self._lock = threading.Lock()

    def _load(self) -> Any | None:
        if self._disabled:
            return None
        if self._pipeline is not None:
            return self._pipeline
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            try:
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForTokenClassification,
                    AutoTokenizer,
                    pipeline,
                )

                tokenizer = AutoTokenizer.from_pretrained(
                    self._model_path,
                    local_files_only=True,
                )
                model = AutoModelForTokenClassification.from_pretrained(
                    self._model_path,
                    local_files_only=True,
                )
                self._pipeline = pipeline(
                    "token-classification",
                    model=model,
                    tokenizer=tokenizer,
                    aggregation_strategy="simple",
                )
            except Exception:  # dependência/modelo opcional; não expor detalhes ou texto
                self._disabled = True
                return None
        return self._pipeline

    @property
    def ner_ready(self) -> bool:
        """Indica que o modelo local foi efetivamente carregado sem download."""

        return self._load() is not None

    def detect(self, text: str) -> tuple[Finding, ...]:
        model_pipeline = self._load()
        if model_pipeline is None or not text:
            return ()
        try:
            predictions: Sequence[dict[str, Any]] = model_pipeline(text)
        except Exception:
            self._disabled = True
            return ()

        findings: list[Finding] = []
        for prediction in predictions:
            score = float(prediction.get("score", 0.0))
            if score < self._threshold:
                continue
            # Com aggregation_strategy=simple, entity_group normalmente já vem
            # sem BIO; o fallback abaixo também aceita saídas token a token.
            raw_label = str(prediction.get("entity_group") or prediction.get("entity") or "")
            label = re.sub(r"^(?:B|I|S|E)[-_]", "", raw_label.upper())
            canonical_label = re.sub(r"[^A-Z0-9ÁÉÍÓÚÃÕÇ]", "", label)
            if canonical_label in {"", "O", "LABEL0"}:
                continue
            entity = (
                _OPENMED_LABELS.get(label)
                or _OPENMED_LABELS.get(canonical_label)
                or EntityType.GENERIC_ID
            )
            start = prediction.get("start")
            end = prediction.get("end")
            valid_span = isinstance(start, int) and isinstance(end, int) and end > start
            if entity is None or not valid_span:
                continue
            assert isinstance(start, int) and isinstance(end, int)
            findings.append(Finding(entity, Span(start, end), min(score, 1.0), "openmed:local"))
        return _resolve_overlaps(findings)


class CompositeDetector:
    """Combina detectores preservando achados determinísticos prioritários."""

    def __init__(self, detectors: Sequence[Detector]) -> None:
        self._detectors = tuple(detectors)

    @property
    def ner_ready(self) -> bool:
        return any(getattr(detector, "ner_ready", False) for detector in self._detectors)

    def detect(self, text: str) -> tuple[Finding, ...]:
        findings = (finding for detector in self._detectors for finding in detector.detect(text))
        return _resolve_overlaps(findings)


def create_detector(openmed_model_path: str | Path | None = None) -> Detector:
    """Cria o pipeline padrão, opcionalmente com OpenMed local."""

    deterministic = DeterministicDetector()
    if openmed_model_path is None:
        return deterministic
    return CompositeDetector((deterministic, OpenMedAdapter(openmed_model_path)))


def is_ner_ready(detector: Detector) -> bool:
    """Consulta de capacidade sem presumir que um detector customizado tem NER."""

    return bool(getattr(detector, "ner_ready", False))


__all__ = [
    "CompositeDetector",
    "Detector",
    "DeterministicDetector",
    "OpenMedAdapter",
    "create_detector",
    "is_ner_ready",
    "is_valid_cns",
    "is_valid_cpf",
]
