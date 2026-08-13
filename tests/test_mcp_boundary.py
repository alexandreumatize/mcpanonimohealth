from __future__ import annotations

import asyncio
from dataclasses import dataclass

import mcpanonimohealth.server as server
from mcpanonimohealth.domain import JobState


@dataclass
class FakeJob:
    job_id: str
    state: JobState
    pages: int = 1
    raw_path: str = "/private/patient-name.pdf"
    original_name: str = "patient-name.pdf"


class FakeManager:
    def __init__(self, state: JobState = JobState.PASS) -> None:
        self.state = state

    def health(self):
        return {"backend": "ok", "path": "/private/runtime"}

    def create_from_selection(self):
        return FakeJob("CASE-TEST", self.state)

    def get(self, _job_id: str):
        return FakeJob("CASE-TEST", self.state)

    def get_clean_text(self, _job_id: str):
        return "Paciente [PACIENTE_001]."

    def discard(self, _job_id: str):
        return {"job_id": "CASE-TEST", "discarded": True}


def test_server_exposes_exactly_five_narrow_tools() -> None:
    async def names() -> list[str]:
        return [tool.name for tool in await server.mcp.list_tools()]

    assert set(asyncio.run(names())) == {
        "verificar_instalacao",
        "selecionar_e_desidentificar",
        "consultar_job",
        "obter_texto_desidentificado",
        "descartar_job",
    }


def test_public_job_never_exposes_paths_or_filenames(monkeypatch) -> None:
    monkeypatch.setattr(server, "_manager", FakeManager())
    monkeypatch.setattr(
        "mcpanonimohealth.webapp.start_local_intake",
        lambda _manager: {
            "job_id": "CASE-TEST",
            "state": "PROCESSING",
            "pages": 0,
            "duration_ms": 0,
            "counts": {},
            "reasons": [],
        },
    )
    result = server.selecionar_e_desidentificar()
    serialized = str(result)
    assert result["ok"] is True
    assert "/private" not in serialized
    assert "patient-name.pdf" not in serialized
    assert result["interface_local_aberta"] is True


def test_hold_never_releases_text(monkeypatch) -> None:
    monkeypatch.setattr(server, "_manager", FakeManager(JobState.HOLD))
    result = server.obter_texto_desidentificado("CASE-TEST")
    assert result["ok"] is False
    assert "texto_desidentificado" not in result


def test_pass_releases_only_clean_text(monkeypatch) -> None:
    monkeypatch.setattr(server, "_manager", FakeManager(JobState.PASS))
    result = server.obter_texto_desidentificado("CASE-TEST")
    assert result["ok"] is True
    assert result["texto_desidentificado"] == "Paciente [PACIENTE_001]."
