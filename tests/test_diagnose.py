from __future__ import annotations

from mcpanonimohealth.detection import DeterministicDetector
from mcpanonimohealth.diagnose import diagnose
from mcpanonimohealth.domain import JobState
from mcpanonimohealth.sanitizer import Sanitizer as RealSanitizer


def test_diagnose_reports_flags_without_clinical_text(monkeypatch) -> None:
    class ReadyDetector(DeterministicDetector):
        @property
        def ner_ready(self) -> bool:
            return True

    monkeypatch.setattr(
        "mcpanonimohealth.diagnose.installed_model_path",
        lambda: "/var/empty/fake-openmed-model",
    )
    monkeypatch.setattr(
        "mcpanonimohealth.diagnose.create_detector",
        lambda _path: ReadyDetector(),
    )
    monkeypatch.setattr(
        "mcpanonimohealth.diagnose.Sanitizer",
        lambda detector, require_ner=True: RealSanitizer(detector, require_ner=False),
    )

    report = diagnose()
    serialized = str(report)
    assert "529.982.247-25" not in serialized
    assert "Maria da Silva" not in serialized
    assert "texto" not in report
    assert report["verificacoes"]["ner_carregado"] is True
    assert report["verificacoes"]["caso_sintetico"] is True
    assert report["ok"] is True


def test_diagnose_fails_when_ner_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcpanonimohealth.diagnose.installed_model_path",
        lambda: None,
    )

    class ColdDetector(DeterministicDetector):
        @property
        def ner_ready(self) -> bool:
            return False

    monkeypatch.setattr(
        "mcpanonimohealth.diagnose.create_detector",
        lambda _path: ColdDetector(),
    )
    report = diagnose()
    assert report["ok"] is False
    assert report["verificacoes"]["ner_carregado"] is False
    assert report["verificacoes"]["caso_sintetico"] is False
    assert "MODELO_AUSENTE" in report["falhas"]
    assert "NER_NAO_CARREGADO" in report["falhas"]


def test_diagnose_caso_sintetico_requires_pass(monkeypatch) -> None:
    class ReadyDetector(DeterministicDetector):
        @property
        def ner_ready(self) -> bool:
            return True

    class HoldingSanitizer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def sanitize(self, _text: str):
            return type(
                "R",
                (),
                {
                    "state": JobState.HOLD,
                    "sanitized_text": None,
                    "reasons": ("FORCED",),
                },
            )()

    monkeypatch.setattr(
        "mcpanonimohealth.diagnose.installed_model_path",
        lambda: "/var/empty/fake-openmed-model",
    )
    monkeypatch.setattr(
        "mcpanonimohealth.diagnose.create_detector",
        lambda _path: ReadyDetector(),
    )
    monkeypatch.setattr("mcpanonimohealth.diagnose.Sanitizer", HoldingSanitizer)
    report = diagnose()
    assert report["ok"] is False
    assert report["verificacoes"]["ner_carregado"] is True
    assert report["verificacoes"]["caso_sintetico"] is False
    assert "CASO_SINTETICO_FALHOU" in report["falhas"]
