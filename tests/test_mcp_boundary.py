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
    def __init__(
        self,
        state: JobState = JobState.PASS,
        *,
        modo: str = "unico",
        itens: list[dict] | None = None,
    ) -> None:
        self.state = state
        self.modo = modo
        self.itens = itens or []

    def health(self):
        return {"backend": "ok", "path": "/private/runtime"}

    def create_from_selection(self):
        return FakeJob("CASE-TEST", self.state)

    def get(self, _job_id: str):
        return FakeJob("CASE-TEST", self.state)

    def get_clean_text(self, _job_id: str):
        return "Paciente [PACIENTE_001]."

    def get_release(self, _job_id: str):
        if self.modo == "lote":
            return {
                "modo": "lote",
                "estrutura": "{INICIAIS}/{tipo}_{YYYY-MM-DD}.txt",
                "itens": list(self.itens),
                "texto_desidentificado": "\n\n-----\n\n".join(
                    f"## {item.get('relativo')}\n\n{item['texto_desidentificado']}"
                    for item in self.itens
                    if item.get("texto_desidentificado")
                ),
            }
        return {
            "modo": "unico",
            "texto_desidentificado": "Paciente [PACIENTE_001].",
        }

    def discard(self, _job_id: str):
        return {"job_id": "CASE-TEST", "discarded": True}


def test_server_exposes_exactly_six_narrow_tools() -> None:
    async def names() -> list[str]:
        return [tool.name for tool in await server.mcp.list_tools()]

    assert set(asyncio.run(names())) == {
        "verificar_instalacao",
        "selecionar_e_desidentificar",
        "processar_lote_local",
        "consultar_job",
        "obter_texto_desidentificado",
        "descartar_job",
    }


def test_agent_instructions_require_warning_before_any_tool() -> None:
    assert "ANTES de qualquer ferramenta" in server.INSTRUCTIONS
    assert "não anexe, arraste, cole nem envie" in server.INSTRUCTIONS
    assert "Mostre o aviso mesmo" in server.INSTRUCTIONS
    assert "obter_texto_desidentificado imediatamente" in server.INSTRUCTIONS
    assert "itens[]" in server.INSTRUCTIONS
    assert "NÃO espere o usuário confirmar" in server.INSTRUCTIONS
    assert "consultar_job imediatamente" in server.INSTRUCTIONS
    assert "PROCESSING" in server.INSTRUCTIONS


def test_consultar_job_directs_polling_while_processing(monkeypatch) -> None:
    monkeypatch.setattr(server, "_manager", FakeManager(JobState.PROCESSING))
    result = server.consultar_job("CASE-TEST")
    assert result["ok"] is True
    assert result["estado"] == "PROCESSING"
    assert result["proxima_acao"] == "consultar_job"
    assert "consultar_job" in result["orientacao"]


def test_consultar_job_directs_obtain_on_pass(monkeypatch) -> None:
    monkeypatch.setattr(server, "_manager", FakeManager(JobState.PASS))
    result = server.consultar_job("CASE-TEST")
    assert result["ok"] is True
    assert result["proxima_acao"] == "obter_texto_desidentificado"


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
    assert result["modo"] == "unico"
    assert result["texto_desidentificado"] == "Paciente [PACIENTE_001]."


def test_pass_lote_releases_organized_items(monkeypatch) -> None:
    itens = [
        {
            "estado": "PASS",
            "iniciais": "MS",
            "tipo": "receita",
            "data_documento": "2026-08-10",
            "relativo": "MS/receita_2026-08-10.txt",
            "texto_desidentificado": "Paciente [PACIENTE_001]. MTX.",
        },
        {
            "estado": "PASS",
            "iniciais": "JP",
            "tipo": "receita",
            "data_documento": "2026-08-11",
            "relativo": "JP/receita_2026-08-11.txt",
            "texto_desidentificado": "Paciente [PACIENTE_002]. Pred.",
        },
    ]
    monkeypatch.setattr(
        server,
        "_manager",
        FakeManager(JobState.PASS, modo="lote", itens=itens),
    )
    result = server.obter_texto_desidentificado("CASE-TEST")
    assert result["ok"] is True
    assert result["modo"] == "lote"
    assert len(result["itens"]) == 2
    assert result["itens"][0]["relativo"] == "MS/receita_2026-08-10.txt"
    assert "[PACIENTE_001]" in result["texto_desidentificado"]
    assert "itens[]" in result["aviso"] or "lote" in result["aviso"].lower()
