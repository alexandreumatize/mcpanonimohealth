from __future__ import annotations

import http.client
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
            "clean_text": "NUNCA DEVE IR PARA O NAVEGADOR",
        }

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


def test_local_upload_returns_only_metrics_and_never_clean_text() -> None:
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
    assert "clean_text" not in payload
    assert "NUNCA DEVE" not in payload


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
