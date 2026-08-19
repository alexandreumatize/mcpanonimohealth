"""Interface dedicada, estritamente local, para entrada de documentos clínicos."""

from __future__ import annotations

import io
import json
import os
import secrets
import tempfile
import threading
import time
import webbrowser
import zipfile
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any

from .batch import MAX_BATCH_FILES, process_batch_payloads
from .documents import MAX_BYTES, SUPPORTED_SUFFIXES

MAX_REQUEST_BYTES = min(200 * 1024 * 1024, MAX_BYTES * MAX_BATCH_FILES)
SESSION_SECONDS = 15 * 60

_sessions: set[LocalIntakeSession] = set()
_sessions_lock = threading.Lock()


def _asset(name: str) -> bytes:
    return files("mcpanonimohealth.web").joinpath(name).read_bytes()


def _safe_uploads(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    if not content_type.casefold().startswith("multipart/form-data;"):
        raise ValueError("INVALID_CONTENT_TYPE")
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    if not message.is_multipart():
        raise ValueError("INVALID_MULTIPART")
    uploads: list[tuple[str, bytes]] = []
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        name = part.get_param("name", header="content-disposition")
        if name not in {"document", "documents"}:
            continue
        if "form-data" not in disposition.casefold():
            continue
        filename = part.get_filename() or "document"
        suffix = Path(filename).suffix.casefold()
        payload = part.get_payload(decode=True) or b""
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError("UNSUPPORTED_FORMAT")
        if not payload:
            raise ValueError("EMPTY_FILE")
        if len(payload) > MAX_BYTES:
            raise ValueError("FILE_TOO_LARGE")
        uploads.append((suffix, payload))
        if len(uploads) > MAX_BATCH_FILES:
            raise ValueError("TOO_MANY_FILES")
    if not uploads:
        raise ValueError("DOCUMENT_MISSING")
    return uploads


def _zip_directory(root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                archive.write(path, arcname=str(path.relative_to(root)))
    return buffer.getvalue()


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, session: LocalIntakeSession) -> None:
        self.session = session
        super().__init__(("127.0.0.1", 0), _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    server: _LoopbackServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        # Nunca registrar nome, caminho, query string ou metadados do documento.
        return

    def version_string(self) -> str:
        return "mcpanonimohealth-local"

    def _headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Security-Policy", self.server.session.csp)
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )

    def _send(self, status: HTTPStatus, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self._headers(content_type, len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: HTTPStatus, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        self._send(status, payload, "application/json; charset=utf-8")

    def _valid_host(self) -> bool:
        return self.headers.get("Host", "") == self.server.session.host

    def do_GET(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "codigo": "INVALID_HOST"})
            return
        session = self.server.session
        if self.path == session.route:
            html = _asset("index.html").replace(b"{{SESSION_TOKEN}}", session.token.encode())
            self._send(HTTPStatus.OK, html, "text/html; charset=utf-8")
            return
        if self.path == f"{session.route}app.css":
            self._send(HTTPStatus.OK, _asset("app.css"), "text/css; charset=utf-8")
            return
        if self.path == f"{session.route}app.js":
            self._send(HTTPStatus.OK, _asset("app.js"), "text/javascript; charset=utf-8")
            return
        if self.path == f"{session.route}baixar.zip":
            zip_bytes = session.take_download()
            if zip_bytes is None:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "codigo": "DOWNLOAD_UNAVAILABLE"})
                return
            self.send_response(HTTPStatus.OK)
            self._headers("application/zip", len(zip_bytes))
            self.send_header(
                "Content-Disposition",
                'attachment; filename="mcpanonimohealth-lote.zip"',
            )
            self.end_headers()
            self.wfile.write(zip_bytes)
            session.finish()
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "codigo": "NOT_FOUND"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._json(HTTPStatus.FORBIDDEN, {"ok": False, "codigo": "CROSS_ORIGIN_BLOCKED"})

    def do_POST(self) -> None:  # noqa: N802
        session = self.server.session
        if not self._valid_host() or self.path != f"{session.route}processar":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "codigo": "NOT_FOUND"})
            return
        if self.headers.get("Origin") != session.origin:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "codigo": "ORIGIN_BLOCKED"})
            return
        if not secrets.compare_digest(self.headers.get("X-Local-Session", ""), session.token):
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "codigo": "SESSION_BLOCKED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, session.ui_error("FILE_TOO_LARGE"))
            return
        if session.is_finished():
            self._json(HTTPStatus.CONFLICT, session.ui_error("SESSION_ALREADY_USED"))
            return
        try:
            uploads = _safe_uploads(
                self.headers.get("Content-Type", ""), self.rfile.read(length)
            )
            result = session.process_uploads(uploads)
        except ValueError as exc:
            code = str(exc)
            status = (
                HTTPStatus.CONFLICT if code == "SESSION_ALREADY_USED" else HTTPStatus.BAD_REQUEST
            )
            self._json(status, session.ui_error(code))
            if code == "SESSION_ALREADY_USED":
                session.finish()
            return
        except Exception:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                session.ui_error("LOCAL_PROCESSING_FAILED"),
            )
            session.finish()
            return
        # Lote com ZIP: mantém a sessão até baixar ou expirar.
        if result.get("modo") == "lote" and result.get("baixar_disponivel"):
            self._json(HTTPStatus.OK, result)
            return
        self._json(HTTPStatus.OK, result)
        session.finish()


class LocalIntakeSession:
    """Página localhost: um ou vários documentos, com revisão e ZIP local."""

    def __init__(self, manager: Any, *, open_browser: bool = True) -> None:
        self.manager = manager
        self.open_browser = open_browser
        self.token = secrets.token_urlsafe(32)
        self.route = f"/local/{self.token}/"
        self.server = _LoopbackServer(self)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.origin = f"http://{self.host}"
        self.url = f"{self.origin}{self.route}"
        self.csp = (
            "default-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
            "img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'"
        )
        self.job = manager.reserve()
        self.job_id = str(self.job["job_id"])
        self._finished = threading.Event()
        self._process_lock = threading.Lock()
        self._download_zip: bytes | None = None
        self._download_lock = threading.Lock()

    def start(self) -> dict[str, object]:
        with _sessions_lock:
            _sessions.add(self)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        expiry = threading.Timer(SESSION_SECONDS, self.expire)
        expiry.daemon = True
        expiry.start()
        if self.open_browser:
            if not webbrowser.open(self.url, new=2, autoraise=True):
                self.manager.cancel_pending(self.job_id, "LOCAL_INTERFACE_COULD_NOT_OPEN")
                self.finish()
                raise RuntimeError("não foi possível abrir o navegador local")
        return dict(self.job)

    def is_finished(self) -> bool:
        return self._finished.is_set()

    def process(self, suffix: str, payload: bytes) -> dict[str, object]:
        """Compatibilidade com testes de arquivo único."""

        return self.process_uploads([(suffix, payload)])

    def process_uploads(self, uploads: list[tuple[str, bytes]]) -> dict[str, Any]:
        # Uso único: o lock permanece adquirido após o primeiro sucesso.
        if self._finished.is_set() or not self._process_lock.acquire(blocking=False):
            raise ValueError("SESSION_ALREADY_USED")
        started = time.monotonic()
        if len(uploads) == 1:
            suffix, payload = uploads[0]
            with tempfile.TemporaryDirectory(prefix="mcpanonimohealth-local-") as directory:
                os.chmod(directory, 0o700)
                path = Path(directory) / f"document{suffix}"
                path.write_bytes(payload)
                os.chmod(path, 0o600)
                del payload
                result = self.manager.process_path(self.job_id, path)
            return self.ui_result(result, clean_text=self.released_text())

        with tempfile.TemporaryDirectory(prefix="mcpanonimohealth-lote-out-") as out_dir:
            os.chmod(out_dir, 0o700)
            batch = process_batch_payloads(
                uploads,
                Path(out_dir),
                keep_text=True,
            )
            zip_bytes = _zip_directory(Path(out_dir))
        duration_ms = round((time.monotonic() - started) * 1000)
        pages = sum(item.paginas for item in batch.itens)
        job = self.manager.complete_batch(
            self.job_id,
            liberados=batch.liberados,
            retidos=batch.retidos,
            erros=batch.erros,
            pages=pages,
            duration_ms=duration_ms,
            itens=[
                {
                    "estado": item.estado,
                    "iniciais": item.iniciais,
                    "tipo": item.tipo,
                    "data_documento": item.data_documento,
                    "relativo": item.relativo,
                    "paginas": item.paginas,
                    "motivos": list(item.motivos),
                    "texto": item.texto,
                }
                for item in batch.itens
            ],
        )
        with self._download_lock:
            self._download_zip = zip_bytes
        return self.ui_batch_result(job, batch, download_ready=True)

    def take_download(self) -> bytes | None:
        with self._download_lock:
            payload = self._download_zip
            self._download_zip = None
            return payload

    def released_text(self) -> str | None:
        """Texto desidentificado para revisão local na própria página (somente PASS)."""

        try:
            return self.manager.get_clean_text(self.job_id)
        except Exception:
            return None

    def finish(self) -> None:
        if self._finished.is_set():
            return
        self._finished.set()
        with self._download_lock:
            self._download_zip = None
        threading.Thread(target=self._shutdown, daemon=True).start()

    def expire(self) -> None:
        """Expira em fail-closed uma página que não recebeu documento."""

        if self._finished.is_set():
            return
        self.manager.cancel_pending(self.job_id)
        self.finish()

    def wait(self, timeout: float = 0.5) -> bool:
        """Aguarda o encerramento sem expor o evento interno ao CLI."""

        return self._finished.wait(timeout)

    def _shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        with _sessions_lock:
            _sessions.discard(self)

    @staticmethod
    def ui_error(code: str) -> dict[str, Any]:
        return {"ok": False, "estado": "ERROR", "codigo": code}

    @staticmethod
    def ui_result(result: dict[str, object], *, clean_text: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": True,
            "modo": "unico",
            "estado": str(result.get("state", "ERROR")),
            "paginas": int(result.get("pages", 0)),
            "duracao_ms": int(result.get("duration_ms", 0)),
            "contagens": dict(result.get("counts", {})),
            "motivos": list(result.get("reasons", [])),
        }
        if payload["estado"] == "PASS" and clean_text:
            payload["texto_desidentificado"] = clean_text
        return payload

    @staticmethod
    def ui_batch_result(
        job: dict[str, object],
        batch: Any,
        *,
        download_ready: bool,
    ) -> dict[str, Any]:
        items = []
        for index, item in enumerate(batch.itens, start=1):
            entry: dict[str, Any] = {
                "indice": index,
                "estado": item.estado,
                "iniciais": item.iniciais,
                "tipo": item.tipo,
                "data_documento": item.data_documento,
                "relativo": item.relativo,
                "paginas": item.paginas,
                "motivos": list(item.motivos),
            }
            if item.estado == "PASS" and item.texto:
                entry["texto_desidentificado"] = item.texto
            items.append(entry)
        return {
            "ok": True,
            "modo": "lote",
            "estado": str(job.get("state", "ERROR")),
            "paginas": int(job.get("pages", 0)),
            "duracao_ms": int(job.get("duration_ms", 0)),
            "processados": batch.processados,
            "liberados": batch.liberados,
            "retidos": batch.retidos,
            "erros": batch.erros,
            "contagens": dict(job.get("counts", {})),
            "motivos": list(job.get("reasons", [])),
            "itens": items,
            "baixar_disponivel": download_ready and batch.liberados > 0,
            "aviso": (
                "Lote local pronto. Baixe o ZIP se quiser arquivo no disco. "
                "O agente já pode obter os textos (obter_texto_desidentificado) "
                "e organizar a análise — sem precisar de confirmação no chat."
            ),
        }


def start_local_intake(manager: Any, *, open_browser: bool = True) -> dict[str, object]:
    """Abre a interface local e retorna imediatamente o job que o agente deve consultar."""

    return LocalIntakeSession(manager, open_browser=open_browser).start()


__all__ = ["LocalIntakeSession", "start_local_intake"]
