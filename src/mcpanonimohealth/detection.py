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
# (?-i:...) mantém a inicial maiúscula mesmo com IGNORECASE nos rótulos.
_NAME_WORD = (
    r"(?-i:[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ])"
    r"[A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇáàâãéèêíìîóòôõúùûç'-]+"
)
_CITY_WORD = (
    r"(?-i:[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ])"
    r"[A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇáàâãéèêíìîóòôõúùûç'-]+"
)
# Espaço horizontal é intencional: nomes nunca podem consumir o rótulo da
# linha seguinte quando o OCR preserva quebras de linha.
_HSPACE = r"[ \t]"
_NAME = rf"{_NAME_WORD}(?:{_HSPACE}+(?:(?:d[aeo]s?|e){_HSPACE}+)?{_NAME_WORD}){{1,5}}"
_CITY_NAME = rf"{_CITY_WORD}(?:{_HSPACE}+{_CITY_WORD}){{0,3}}"


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
            rf"(?:nome\s+d[oa]\s+paciente|nome\s+completo|paciente){_HSPACE}*[:\-]{_HSPACE}*"
            rf"(?P<value>{_NAME})"
        ),
        0.99,
    ),
    _Rule(
        "patient_label_nearby_line",
        EntityType.PATIENT,
        # OCR de receita: "Paciente:" + linhas de UI + nome em linha própria.
        _compile(
            rf"(?:nome\s+d[oa]\s+paciente|nome\s+completo|paciente){_HSPACE}*[:\-]"
            rf"(?:[^\n]{{0,60}}\n){{1,4}}"
            rf"{_HSPACE}*(?P<value>{_NAME}){_HSPACE}*(?=\n|$)"
        ),
        0.96,
    ),
    _Rule(
        "patient_usuario",
        EntityType.PATIENT,
        _compile(
            rf"(?:para\s+[ao]\s+)?usu[aá]ri[oa]{_HSPACE}+(?P<value>{_NAME})"
        ),
        0.98,
    ),
    _Rule(
        "doctor_label",
        EntityType.DOCTOR,
        _compile(
            rf"(?:nome\s+d[oa]\s+m[eé]dic[oa](?:\s+solicitante)?|"
            rf"m[eé]dic[oa]\s+solicitante|m[eé]dic[oa](?!mento)|dr\.?|dra\.?|dr\s*\(\s*a\s*\)\.?)"
            rf"{_HSPACE}*[:\-]?{_HSPACE}*(?P<value>{_NAME})"
        ),
        0.98,
    ),
    _Rule(
        "doctor_before_specialty",
        EntityType.DOCTOR,
        _compile(
            rf"(?P<value>{_NAME})\s*(?:\n|\r\n)\s*"
            rf"(?:m[eé]dico(?:\s+reumatologista|\s+prescritor)?|reumatologista)\b"
        ),
        0.97,
    ),
    _Rule(
        "doctor_after_atenciosamente",
        EntityType.DOCTOR,
        _compile(rf"atenciosamente\s*[,:]?\s*(?P<value>{_NAME})"),
        0.96,
    ),
    _Rule(
        "institution_label",
        EntityType.INSTITUTION,
        _compile(
            rf"(?:institui[cç][aã]o|estabelecimento|servi[cç]o|unidade|"
            rf"nome\s+d[oa]\s+institui[cç][aã]o(?:\s+de\s+sa[uú]de)?)"
            rf"{_HSPACE}*[:\-]{_HSPACE}*"
            r"(?P<value>[^\s\n;][^\n;]{2,99})"
        ),
        0.97,
    ),
    _Rule(
        "institution_label_nearby_line",
        EntityType.INSTITUTION,
        # Só quando o valor está em linha própria (rótulo + quebra).
        _compile(
            rf"(?:nome\s+d[oa]\s+institui[cç][aã]o(?:\s+de\s+sa[uú]de)?|"
            rf"institui[cç][aã]o(?:\s+de\s+sa[uú]de)?)"
            rf"{_HSPACE}*[:\-]{_HSPACE}*\n"
            rf"(?:{_HSPACE}*\n){{0,1}}"
            rf"{_HSPACE}*(?P<value>[^\s\n;][^\n;]{{2,99}}){_HSPACE}*(?=\n|$)"
        ),
        0.96,
    ),
    _Rule(
        "institution_prefix",
        EntityType.INSTITUTION,
        _compile(
            r"(?P<value>\b(?:hospital|cl[ií]nica|laborat[oó]rio|maternidade|policl[ií]nica|"
            r"unidade\s+b[aá]sica\s+de\s+sa[uú]de|ubs|upa)\s+"
            r"[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ][^\n,;]{2,80})"
        ),
        0.98,
    ),
    _Rule(
        "ses_estado",
        EntityType.INSTITUTION,
        # Estado de atendimento / órgão emissor (ex.: SES-SP).
        _compile(
            r"(?P<value>\b(?:secretaria\s+de\s+estado\s+da\s+sa[uú]de(?:\s+de\s+[^\n,]{2,60})?|"
            r"secretaria\s+da\s+sa[uú]de|"
            r"governo\s+do\s+estado(?:\s+de\s+[^\n,]{2,40})?|"
            r"goyerno\s+do\s+estado|"
            r"coordenadoria\s+de\s+regi[oõ]es\s+de\s+sa[uú]de))"
        ),
        0.97,
    ),
    _Rule(
        "doctor_specialty",
        EntityType.DOCTOR,
        # Especialidade do assinante (OCR: MédicoReumatologista / SPReumatologia).
        _compile(
            r"(?P<value>\bm[eé]dico\s*reumatologista\b|"
            r"\b(?:sp\s*)?reumatologista\b|"
            r"\b(?:sp\s*)?reumatologia\b)"
        ),
        0.93,
    ),
    _Rule(
        "estado_sao_paulo_ocr",
        EntityType.ADDRESS,
        _compile(
            r"(?P<value>\bs[ãa\u00c3]o\s*paulo\b|\bsãopaulo\b|\bsaopaulo\b|\bs[\u00c3ã]opaulo\b)",
            flags=re.IGNORECASE,
        ),
        0.96,
    ),
    _Rule(
        "uf_after_crm_marker",
        EntityType.ADDRESS,
        # Após mascarar CRM sobra "SP — Reumatologia".
        _compile(r"\[CRM_\d+\]\s*(?P<value>[A-Z]{2})\b"),
        0.95,
    ),
    _Rule(
        "address_label",
        EntityType.ADDRESS,
        _compile(
            rf"(?:endere[cç]o|resid[eê]ncia|domic[ií]lio){_HSPACE}*[:\-]?\s*"
            r"(?P<value>[^\s\n;][^\n;]{4,139})"
        ),
        0.99,
    ),
    _Rule(
        "address_complemento",
        EntityType.ADDRESS,
        _compile(
            rf"(?:complemento|apto\.?|apartamento|sala|bloco|torre)"
            rf"{_HSPACE}*[:\-]{_HSPACE}*"
            r"(?P<value>[^\s\n;][^\n;]{1,79})"
        ),
        0.98,
    ),
    _Rule(
        "address_sala_inline",
        EntityType.ADDRESS,
        # "Sala 1012 e 1013" mesmo sem rótulo "Complemento:"
        _compile(
            r"(?P<value>\b(?:sala|apto\.?|apartamento|bloco)\s+"
            r"[A-Z0-9][A-Za-z0-9.\-/ \t]{0,40}\d{1,5}(?:\s+e\s+\d{1,5})?)"
        ),
        0.94,
    ),
    _Rule(
        "address_street",
        EntityType.ADDRESS,
        _compile(
            r"(?P<value>\b(?:rua|r\.|avenida|av\.|alameda|al\.|travessa|tv\.|"
            r"estrada|rodovia|pra[cç]a|largo|viela)\s+"
            r"[A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇáàâãéèêíìîóòôõúùûç0-9][^\n]{4,119})"
        ),
        0.97,
    ),
    _Rule(
        "address_bairro_municipio",
        EntityType.ADDRESS,
        _compile(
            rf"(?:bairro|munic[ií]pio|cidade){_HSPACE}*[:\-]{_HSPACE}*"
            r"(?P<value>[^\s\n;][^\n;]{2,79})"
        ),
        0.96,
    ),
    _Rule(
        "address_bairro_municipio_nearby_line",
        EntityType.ADDRESS,
        # OCR de formulário: "Bairro:\nBoqueirão"
        _compile(
            rf"(?:bairro|munic[ií]pio|cidade|complemento){_HSPACE}*[:\-]"
            rf"(?:[^\n]{{0,40}}\n){{1,2}}"
            rf"{_HSPACE}*(?P<value>[^\s\n;][^\n;]{{2,79}}){_HSPACE}*(?=\n|$)"
        ),
        0.95,
    ),
    _Rule(
        "address_number_labeled",
        EntityType.ADDRESS,
        _compile(
            rf"(?:n[ºo°]\.?|n[uú]mero){_HSPACE}*[:\-]{_HSPACE}*"
            r"(?P<value>\d{1,6})\b"
        ),
        0.9,
    ),
    _Rule(
        "health_region_drs",
        EntityType.ADDRESS,
        _compile(
            r"(?P<value>\bDRS\s+[IVXLC\d]{1,8}(?:\s*[-–]\s*[^\n]{3,80})?)",
            flags=re.IGNORECASE,
        ),
        0.97,
    ),
    _Rule(
        "city_uf_slash",
        EntityType.ADDRESS,
        # Praia Grande/SP | Santos/SP — só espaço horizontal entre palavras.
        _compile(rf"(?P<value>\b{_CITY_NAME}{_HSPACE}*/{_HSPACE}*[A-Z]{{2}}\b)"),
        0.95,
    ),
    _Rule(
        "city_uf_dash",
        EntityType.ADDRESS,
        _compile(rf"(?P<value>\b{_CITY_NAME}{_HSPACE}*[-–]{_HSPACE}*[A-Z]{{2}}\b)"),
        0.94,
    ),
    _Rule(
        "city_before_letter_date",
        EntityType.ADDRESS,
        # "Praia Grande, 24 de abril de 2026." / "Santos, 24 de abril de 2026."
        _compile(
            rf"(?P<value>\b{_CITY_NAME})"
            r"(?=,\s*(?:0?[1-9]|[12]\d|3[01])\s+de\s+[a-zç]+)"
        ),
        0.93,
    ),
    _Rule(
        "uf_labeled",
        EntityType.ADDRESS,
        _compile(rf"\buf{_HSPACE}*[:\-]{_HSPACE}*(?P<value>[A-Z]{{2}})\b"),
        0.92,
    ),
    _Rule(
        "signature_before_patient",
        EntityType.PERSON,
        # Linha de assinatura OCR (ex.: "Eore tily Sos Rhhels") antes do rótulo.
        _compile(
            rf"(?P<value>[^\n]{{8,80}})\n{_HSPACE}*"
            r"assinatura\s+d[oa]\s+paciente"
        ),
        0.91,
    ),
    _Rule(
        "dob_after_age_fragment",
        EntityType.DATE_OF_BIRTH,
        # "…-83,de 22-02-1942" / "83 anos, de 03/02/1942"
        _compile(
            r"(?:-\d{1,3}|\d{1,3}\s*anos?)\s*,?\s*de\s+"
            r"(?P<value>(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-]"
            r"(?:19|20)\d{2})"
        ),
        0.98,
    ),
    _Rule(
        "access_code_seu",
        EntityType.CREDENTIAL,
        _compile(r"\bseu\s+(?P<value>\d{2,10})\b"),
        0.94,
    ),
    _Rule(
        "convenio_label",
        EntityType.INSURANCE,
        _compile(
            rf"(?:conv[eê]nio|plano\s+de\s+sa[uú]de|operadora(?:\s+de\s+sa[uú]de)?|"
            rf"seguro\s+sa[uú]de|plano\s+de\s+s[aá]ude){_HSPACE}*[:\-–]\s*"
            r"(?P<value>(?!n[aã]o\b|sim\b)[A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ0-9][^\n;]{1,79})"
        ),
        0.98,
    ),
    _Rule(
        "convenio_qual",
        EntityType.INSURANCE,
        _compile(
            rf"(?:qual|nome\s+d[oa]\s+plano|nome\s+d[oa]\s+conv[eê]nio)"
            rf"{_HSPACE}*[:\-–]\s*"
            r"(?P<value>(?!n[aã]o\b|sim\b)[A-Za-zÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ0-9][^\n;]{1,79})"
        ),
        0.97,
    ),
    _Rule(
        "birth_date_label",
        EntityType.DATE_OF_BIRTH,
        _compile(
            rf"(?:data\s+de\s+nascimento|nascimento|nasc\.?|dn){_HSPACE}*[:\-]?"
            rf"{_HSPACE}*"
            r"(?P<value>(?:0?[1-9]|[12]\d|3[01])[/.\-\s]+(?:0?[1-9]|1[0-2])[/.\-\s]+(?:19|20)\d{2}|"
            r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))"
        ),
        1.0,
    ),
    _Rule(
        "birth_date_ocr_fragment",
        EntityType.DATE_OF_BIRTH,
        # OCR quebrado: "Nascimento: ... /04 ... 1942" (ano 19xx perto do rótulo)
        _compile(
            r"(?:data\s+de\s+nascimento|nascimento|nasc\.?|dn)"
            r"[\s\S]{0,80}?"
            r"(?P<value>(?:0?[1-9]|[12]\d|3[01])?\s*[/.\-]\s*(?:0?[1-9]|1[0-2])"
            r"[\s\S]{0,12}?19\d{2}|(?<!\d)19\d{2}(?!\d))"
        ),
        0.99,
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
        "email_labeled",
        EntityType.EMAIL,
        _compile(
            r"(?:e-?mail|email)\s*[:\-–]?\s*"
            r"(?P<value>[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63})"
        ),
        1.0,
    ),
    _Rule(
        "url",
        EntityType.URL,
        _compile(r"(?P<value>\b(?:https?://|www\.)[^\s<>\]\[\"']+)", flags=re.IGNORECASE),
        1.0,
    ),
    _Rule(
        "url_bare_domain",
        EntityType.URL,
        # r.mevosaude.com.br/MUVV4RN | validar.iti.gov.br
        _compile(
            r"(?P<value>\b(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+"
            r"(?:com\.br|gov\.br|edu\.br|org\.br|net\.br|com|org|net|br|gov)"
            r"(?:/[^\s<>\]\[\"']*)?)",
            flags=re.IGNORECASE,
        ),
        0.99,
    ),
    _Rule(
        "access_code_labeled",
        EntityType.CREDENTIAL,
        _compile(
            r"(?:c[oó]digo\s+de\s+acesso|c[oó]digo\s+de\s+valida[cç][aã]o|"
            r"senha\s+de\s+acesso|pin\s+de\s+acesso)"
            r"\s*(?:[eé]\s*)?[:\-–]?\s*(?P<value>\d{4,10})\b"
        ),
        0.98,
    ),
    _Rule(
        "doctor_before_crm",
        EntityType.DOCTOR,
        _compile(
            rf"(?P<value>{_NAME})"
            rf"(?:[ \t]{{0,48}}|[ \t]*\n[ \t]*)"
            # CNES é instituição — não usar como âncora de médico.
            rf"(?=\b(?:c\s*)?r\s*m\b|\brqe\b|\broe\b)"
        ),
        0.95,
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
        _compile(
            r"(?:telefone(?:\s*\(\s*s\s*\))?|celular|fone|whatsapp|tel\.?)"
            r"\s*[:\-–]?\s*"
            r"(?P<value>(?:\(?[1-9]\d\)?[\s.-]*)?(?:9\d{4}|[2-8]\d{3})[\s.-]?\d{4})"
        ),
        0.99,
    ),
    _Rule(
        "phone_mobile_local",
        EntityType.PHONE,
        # Celular BR sem DDD, comum em OCR de formulários: 99135-9190
        _compile(r"(?P<value>(?<!\d)9\d{4}[\s.-]\d{4}(?!\d))"),
        0.95,
    ),
    _Rule(
        "phone_ddd_labeled",
        EntityType.PHONE,
        _compile(r"\bddd\s*[:\-–]?\s*(?P<value>\d{2})\b"),
        0.96,
    ),
    _Rule(
        "phone_ddd_then_number",
        EntityType.PHONE,
        _compile(
            r"\bddd\s*[:\-–]?\s*\d{2}\D{0,40}"
            r"(?:telefone(?:\s*\(\s*s\s*\))?|celular|fone)?\s*[:\-–]?\s*"
            r"(?P<value>(?:9\d{4}|[2-8]\d{3})[\s.-]?\d{4})"
        ),
        0.97,
    ),
    _Rule(
        "state_alone_labeled",
        EntityType.ADDRESS,
        _compile(
            r"(?:estado|uf)\s*[:\-–]\s*(?P<value>[A-Z]{2})\b"
        ),
        0.96,
    ),
    _Rule(
        "postal_code_formatted",
        EntityType.POSTAL_CODE,
        _compile(r"(?P<value>(?<!\d)\d{5}\s*[-–]?\s*\d{3}(?!\d))"),
        0.99,
    ),
    _Rule(
        "postal_code_compact",
        EntityType.POSTAL_CODE,
        _compile(r"(?P<value>(?<!\d)\d{8}(?!\d))"),
        0.93,
    ),
    _Rule(
        "postal_code_labeled",
        EntityType.POSTAL_CODE,
        _compile(
            r"\bcep\s*[:\-]?\s*(?P<value>\d{5}\s*[-–]?\s*\d{3}|\d{8})(?!\d)"
        ),
        0.99,
    ),
    _Rule(
        "crm",
        EntityType.CRM,
        _compile(
            # Captura o bloco inteiro para não sobrar CRMISP/CRMSP no texto.
            r"(?P<value>\b(?:c\s*)?r\s*m(?:[-/\s]*[A-Z]{0,3})?\s*"
            r"(?:n[ºo°]\.?)?\s*[:#\-]?\s*"
            r"(?:\d{2,3}(?:[.\s]\d{3}){1,2}|\d{4,8})"
            r"(?:\s*[-/]\s*[A-Z]{2})?)\b"
        ),
        0.99,
    ),
    _Rule(
        "rqe",
        EntityType.GENERIC_ID,
        _compile(
            # RQE 83515 | ROE83515 (OCR) | RQE[token]
            r"(?P<value>\b(?:rqe|roe|rqo)\s*[:#\-]?\s*\d{3,8})\b"
        ),
        0.98,
    ),
    _Rule(
        "cnes",
        EntityType.GENERIC_ID,
        _compile(r"\bcnes\s*[:#\-]?\s*(?P<value>\d{5,8})\b"),
        0.97,
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
    _Rule(
        "cpf_fragment_before_dob",
        EntityType.CPF,
        # OCR: "252.77[DATA_NASCIMENTO]" / CPF partido.
        _compile(r"(?P<value>(?<!\d)\d{3}\.\d{2,3})(?=\s*(?:\d{3}|\[DATA_NASCIMENTO\]))"),
        0.95,
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


def _span_contains(outer: Span, inner: Span) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


def _resolve_overlaps(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    # Prefere o maior trecho (ex.: linha de endereço completa) para não deixar
    # CEP/UF internos “vencerem” e vazarem o restante da rua/bairro.
    ranked = sorted(
        findings,
        key=lambda item: (-item.span.length, -item.confidence, item.span.start, item.entity.value),
    )
    selected: list[Finding] = []
    for candidate in ranked:
        if any(_span_contains(existing.span, candidate.span) for existing in selected):
            continue
        selected = [
            existing
            for existing in selected
            if not _span_contains(candidate.span, existing.span)
        ]
        if any(candidate.span.overlaps(existing.span) for existing in selected):
            continue
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
    "INSURANCE": EntityType.INSURANCE,
    "HEALTHINSURANCE": EntityType.INSURANCE,
    "HEALTH_PLAN": EntityType.INSURANCE,
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
