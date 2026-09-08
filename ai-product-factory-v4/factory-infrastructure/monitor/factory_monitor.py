#!/usr/bin/env python3
"""Local-only, read-only Factory Monitor."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
TELEMETRY = ROOT / "telemetry" / "latest.json"
HOST = "127.0.0.1"
PORT = int(os.environ.get("FACTORY_MONITOR_PORT", "8787"))
MAX_TELEMETRY_BYTES = 1024 * 1024
MAX_PROJECT_BYTES = 256 * 1024
CONSOLE_SESSION = secrets.token_urlsafe(32)
BRIDGE_URL = "http://127.0.0.1:8765"


def read_telemetry() -> dict[str, object]:
    try:
        raw = TELEMETRY.read_bytes()
        if len(raw) > MAX_TELEMETRY_BYTES:
            raise ValueError("telemetry_too_large")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
            raise ValueError("telemetry_invalid")
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"factory_status": "WAITING", "sprint_id": None, "current_stage": None, "updated_at": None, "nodes": []}


class MonitorHandler(BaseHTTPRequestHandler):
    server_version = "FactoryMonitor/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def headers_common(self, content_type: str, length: int, *, session_cookie: bool = False) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        if session_cookie:
            self.send_header("Set-Cookie", f"factory_desk_session={CONSOLE_SESSION}; HttpOnly; SameSite=Strict; Path=/")

    def console_authorized(self) -> bool:
        cookies = self.headers.get("Cookie", "")
        valid_cookie = any(hmac.compare_digest(part.strip(), f"factory_desk_session={CONSOLE_SESSION}") for part in cookies.split(";"))
        origin = self.headers.get("Origin")
        return valid_cookie and (not origin or origin == f"http://{HOST}:{PORT}")

    def proxy_project(self, method: str, path: str, body: bytes = b"") -> None:
        token = os.environ.get("FACTORY_BRIDGE_TOKEN", "")
        if not token:
            self.send_error(503)
            return
        headers = {"Authorization": f"Bearer {token}"}
        if body:
            headers["Content-Type"] = "application/json"
        req = request.Request(BRIDGE_URL + path, data=body or None, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                result = response.read(MAX_PROJECT_BYTES + 1)
                status = response.status
        except HTTPError as exc:
            result = exc.read(MAX_PROJECT_BYTES + 1)
            status = exc.code
        except (URLError, TimeoutError, OSError):
            result = b'{"error":"director_bridge_unavailable"}'
            status = 503
        if len(result) > MAX_PROJECT_BYTES:
            result = b'{"error":"response_too_large"}'
            status = 502
        self.send_response(status)
        self.headers_common("application/json; charset=utf-8", len(result))
        self.end_headers()
        self.wfile.write(result)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            body = json.dumps(read_telemetry(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.headers_common("application/json; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/factory-status":
            body = json.dumps(read_telemetry(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.headers_common("application/json; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        if re.fullmatch(r"/api/projects/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", path):
            if not self.console_authorized():
                self.send_error(401)
                return
            self.proxy_project("GET", path)
            return
        if path == "/health":
            body = b'{"ok":true}'
            self.send_response(200)
            self.headers_common("application/json", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        asset = {"/": "index.html", "/factory-floor": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}.get(path)
        if not asset:
            self.send_error(404)
            return
        body = (ASSETS / asset).read_bytes()
        self.send_response(200)
        self.headers_common(mimetypes.guess_type(asset)[0] or "application/octet-stream", len(body), session_cookie=path in {"/", "/factory-floor"})
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        allowed = path in {"/api/projects/validate", "/api/projects/prepare", "/api/projects/start"} or bool(re.fullmatch(r"/api/projects/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/(pause|resume)", path))
        if not allowed:
            self.send_error(405)
            return
        if not self.console_authorized():
            self.send_error(401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_PROJECT_BYTES:
            self.send_error(413)
            return
        self.proxy_project("POST", path, self.rfile.read(length))

    def do_PUT(self) -> None:
        self.send_error(405)

    def do_PATCH(self) -> None:
        self.send_error(405)

    def do_DELETE(self) -> None:
        self.send_error(405)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), MonitorHandler)
    print(f"Factory Monitor listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
