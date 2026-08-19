from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass
from urllib.request import urlopen

from mcpanonimohealth.webapp import LocalIntakeSession


@dataclass
class FakeLocalManager:
    state: str = "PASS"
    processed: bytes | None = None

    def reserve(self):
        return {
            "job_id": "CASE-LOCALTEST01",
            "state": "PROCESSING",
            "pages": 0,
            "duration_ms": 0,
            "counts": {},
            "reasons": [],
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    def process_path(self, _job_id, path):
        self.processed = path.read_bytes()
        return {
            "job_id": "CASE-LOCALTEST01",
            "state": self.state,
            "pages": 1,
            "duration_ms": 1200,
            "counts": {"PACIENTE": 2},
            "reasons": [],
        }

    def complete_batch(
        self, _job_id, *, liberados, retidos, erros, pages, duration_ms, itens=None
    ):
        self.state = "PASS" if liberados else "HOLD" if retidos else "ERROR"
        self.batch_itens = list(itens or [])
        return {
            "job_id": "CASE-LOCALTEST01",
            "state": self.state,
            "pages": pages,
            "duration_ms": duration_ms,
            "counts": {
                "LOTE_LIBERADOS": liberados,
                "LOTE_RETIDOS": retidos,
                "LOTE_ERROS": erros,
            },
            "reasons": ["LOTE_LOCAL"],
            "modo": "lote",
        }

    def get_clean_text(self, _job_id):
        return "Paciente [PACIENTE_001]. Sexo: feminino. Idade: 55 anos."

    def cancel_pending(self, _job_id, _reason="LOCAL_INTERFACE_EXPIRED"):
        self.state = "ERROR"


def _multipart(payload: bytes, filename: str = "caso-sintetico.txt") -> tuple[str, bytes]:
    boundary = "----mcpanonimohealth-test"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


def test_local_page_has_no_external_resources_and_security_headers() -> None:
    session = LocalIntakeSession(FakeLocalManager(), open_browser=False)
    session.start()
    try:
        with urlopen(session.url, timeout=3) as response:  # noqa: S310
            html = response.read().decode()
            assert response.headers["Cache-Control"].startswith("no-store")
            assert "default-src 'none'" in response.headers["Content-Security-Policy"]
            assert response.headers["X-Frame-Options"] == "DENY"
        assert "fonts.googleapis" not in html
        assert "https://" not in html
        assert "{{SESSION_TOKEN}}" not in html
        assert re.search(r'<script src="app\.js" defer>', html)
    finally:
        session.finish()


def _multipart_many(files: list[tuple[bytes, str]]) -> tuple[str, bytes]:
    boundary = "----mcpanonimohealth-test"
    chunks = []
    for payload, filename in files:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            "Content-Type: text/plain\r\n\r\n".encode()
            + payload
            + b"\r\n"
        )
    body = b"".join(chunks) + f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


def test_local_batch_upload_returns_summary_and_zip() -> None:
    manager = FakeLocalManager()
    session = LocalIntakeSession(manager, open_browser=False)
    session.start()
    content_type, body = _multipart_many(
        [
            (
                b"Paciente: Maria da Silva\nCPF: 529.982.247-25\n"
                b"Emissao: 10/08/2026\nReceita: metotrexato 15 mg\n"
                b"Idade: 55 anos\nSexo: feminino\n",
                "a.txt",
            ),
            (
                b"Paciente: Joao Pedro\nCPF: 390.533.447-05\n"
                b"Emissao: 11/08/2026\nReceita: prednisona 5 mg\n"
                b"Idade: 40 anos\nSexo: masculino\n",
                "b.txt",
            ),
        ]
    )
    connection = http.client.HTTPConnection("127.0.0.1", session.server.server_port, timeout=30)
    try:
        connection.request(
            "POST",
            f"{session.route}processar",
            body=body,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                "Origin": session.origin,
                "X-Local-Session": session.token,
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode())
        assert response.status == 200
        assert payload["modo"] == "lote"
        assert payload["processados"] == 2
        assert payload["liberados"] == 2
        assert payload["baixar_disponivel"] is True
        assert len(payload["itens"]) == 2
        assert all(item.get("texto_desidentificado") for item in payload["itens"])
        assert manager.batch_itens and len(manager.batch_itens) == 2
        assert all(item.get("texto") for item in manager.batch_itens)
        assert "agente" in payload["aviso"].lower() or "MCP" in payload["aviso"]

        connection.request(
            "GET",
            f"{session.route}baixar.zip",
            headers={"Host": session.host},
        )
        zip_response = connection.getresponse()
        zip_bytes = zip_response.read()
        assert zip_response.status == 200
        assert zip_bytes[:2] == b"PK"
    finally:
        connection.close()
        if not session.is_finished():
            session.finish()


def test_local_page_has_batch_controls() -> None:
    session = LocalIntakeSession(FakeLocalManager(), open_browser=False)
    session.start()
    try:
        with urlopen(session.url, timeout=3) as response:  # noqa: S310
            html = response.read().decode()
        assert 'id="folder"' in html
        assert "webkitdirectory" in html
        assert "multiple" in html
        assert "Escolher pasta" in html
        assert "não precisa" in html.lower() or "Feito" in html
    finally:
        session.finish()


def test_local_upload_returns_deidentified_text_on_pass() -> None:
    manager = FakeLocalManager()
    session = LocalIntakeSession(manager, open_browser=False)
    session.start()
    content_type, body = _multipart(b"Caso inteiramente sintetico sem pessoa real.")
    connection = http.client.HTTPConnection("127.0.0.1", session.server.server_port, timeout=3)
    connection.request(
        "POST",
        f"{session.route}processar",
        body=body,
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Origin": session.origin,
            "X-Local-Session": session.token,
        },
    )
    response = connection.getresponse()
    payload = response.read().decode()
    connection.close()
    assert response.status == 200
    assert manager.processed == b"Caso inteiramente sintetico sem pessoa real."
    assert '"estado":"PASS"' in payload
    assert "texto_desidentificado" in payload
    assert "[PACIENTE_001]" in payload
    assert "55 anos" in payload


def test_second_upload_rejected_as_session_already_used(monkeypatch) -> None:
    manager = FakeLocalManager()
    session = LocalIntakeSession(manager, open_browser=False)
    session.start()

    def finish_keep_server_up() -> None:
        # Marca uso único sem derrubar o listener, para exercitar o segundo POST.
        session._finished.set()  # noqa: SLF001

    monkeypatch.setattr(session, "finish", finish_keep_server_up)
    content_type, body = _multipart(b"Caso inteiramente sintetico sem pessoa real.")
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Origin": session.origin,
        "X-Local-Session": session.token,
    }
    connection = http.client.HTTPConnection("127.0.0.1", session.server.server_port, timeout=3)
    try:
        connection.request("POST", f"{session.route}processar", body=body, headers=headers)
        first = connection.getresponse()
        first.read()
        assert first.status == 200

        connection.request("POST", f"{session.route}processar", body=body, headers=headers)
        second = connection.getresponse()
        payload = second.read().decode()
        assert second.status == 409
        assert "SESSION_ALREADY_USED" in payload
        assert manager.processed == b"Caso inteiramente sintetico sem pessoa real."
    finally:
        connection.close()
        LocalIntakeSession.finish(session)


def test_process_lock_held_after_first_success() -> None:
    manager = FakeLocalManager()
    session = LocalIntakeSession(manager, open_browser=False)
    session.start()
    try:
        session.process(".txt", b"primeiro sinteticamente")
        try:
            session.process(".txt", b"segundo nao deve passar")
        except ValueError as error:
            assert str(error) == "SESSION_ALREADY_USED"
        else:
            raise AssertionError("segunda chamada deveria falhar")
        assert manager.processed == b"primeiro sinteticamente"
    finally:
        session.finish()


def test_local_upload_blocks_cross_origin_request() -> None:
    session = LocalIntakeSession(FakeLocalManager(), open_browser=False)
    session.start()
    try:
        content_type, body = _multipart(b"Caso inteiramente sintetico sem pessoa real.")
        connection = http.client.HTTPConnection(
            "127.0.0.1", session.server.server_port, timeout=3
        )
        connection.request(
            "POST",
            f"{session.route}processar",
            body=body,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                "Origin": "https://example.invalid",
                "X-Local-Session": session.token,
            },
        )
        response = connection.getresponse()
        payload = response.read().decode()
        connection.close()
        assert response.status == 403
        assert "ORIGIN_BLOCKED" in payload
    finally:
        session.finish()
