#!/usr/bin/env python3
"""Local-only, narrow Director Bridge for AI Product Factory V4."""

from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
from datetime import datetime, timezone
from urllib import request
from urllib.error import HTTPError, URLError

from project_orchestrator import (
    ManifestError, STATE_DIR, load_state, pause_project, prepare_project, public_state,
    recover_interrupted_projects, resume_project, start_project, validate_manifest,
)


HOST = "127.0.0.1"
PORT = int(os.environ.get("FACTORY_BRIDGE_PORT", "8765"))
FACTORY_WEBHOOK = "http://127.0.0.1:5678/webhook/ai-product-factory-v4-sprint"
AUDIT_DIR = Path(__file__).resolve().parent / "audit"
ASSET_DIR = Path(__file__).resolve().parent / "console"
MONITOR_TELEMETRY = Path(__file__).resolve().parents[1] / "monitor" / "telemetry" / "latest.json"
CONSOLE_SESSION = secrets.token_urlsafe(32)
MAX_BODY_BYTES = 64 * 1024
MAX_LIST_ITEMS = 50
REQUIRED_FIELDS = {
    "request_id", "sprint_title", "goal", "scope", "constraints",
    "acceptance_criteria", "allowed_actions", "forbidden_actions", "requested_target",
}
OPTIONAL_FIELDS = {"context", "notes", "product_identity", "product_name", "product"}
ALLOWED_TARGETS = {"SMOKE_FIXTURE"}
ALLOWED_ACTIONS = {"READ_PROJECT", "CREATE_FILES", "MODIFY_FILES", "RUN_TESTS"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_string(value: object, name: str, *, maximum: int = 4_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValidationError(f"{name} exceeds {maximum} characters")
    return cleaned


def validate_string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > MAX_LIST_ITEMS:
        raise ValidationError(f"{name} must contain 1-{MAX_LIST_ITEMS} strings")
    return [validate_string(item, f"{name}[]", maximum=1_000) for item in value]


def validate_brief(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be an object")
    keys = set(payload)
    missing = REQUIRED_FIELDS - keys
    unexpected = keys - REQUIRED_FIELDS - OPTIONAL_FIELDS
    if missing:
        raise ValidationError("missing fields: " + ", ".join(sorted(missing)))
    if unexpected:
        raise ValidationError("unexpected fields: " + ", ".join(sorted(unexpected)))

    request_id = validate_string(payload["request_id"], "request_id", maximum=128)
    if not ID_PATTERN.fullmatch(request_id):
        raise ValidationError("request_id has an invalid format")
    target = validate_string(payload["requested_target"], "requested_target", maximum=32)
    if target not in ALLOWED_TARGETS:
        raise ValidationError("requested_target is not allowlisted")
    allowed = validate_string_list(payload["allowed_actions"], "allowed_actions")
    unknown_actions = set(allowed) - ALLOWED_ACTIONS
    if unknown_actions:
        raise ValidationError("unknown or privileged allowed_actions: " + ", ".join(sorted(unknown_actions)))

    result: dict[str, object] = {
        "request_id": request_id,
        "sprint_title": validate_string(payload["sprint_title"], "sprint_title", maximum=300),
        "goal": validate_string(payload["goal"], "goal"),
        "scope": validate_string_list(payload["scope"], "scope"),
        "constraints": validate_string_list(payload["constraints"], "constraints"),
        "acceptance_criteria": validate_string_list(payload["acceptance_criteria"], "acceptance_criteria"),
        "allowed_actions": allowed,
        "forbidden_actions": validate_string_list(payload["forbidden_actions"], "forbidden_actions"),
        "requested_target": target,
    }
    for key in OPTIONAL_FIELDS:
        if key in payload and payload[key] is not None:
            result[key] = validate_string(payload[key], key)
    return result


def sprint_id_for(request_id: str) -> str:
    return "SPRINT-" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:20].upper()


def record_path(sprint_id: str) -> Path:
    return AUDIT_DIR / f"{sprint_id}.json"


def load_record(sprint_id: str) -> dict[str, object] | None:
    if not re.fullmatch(r"SPRINT-[A-F0-9]{20}", sprint_id):
        return None
    path = record_path(sprint_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_record(record: dict[str, object]) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = record_path(str(record["sprint_id"]))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_factory_telemetry() -> dict[str, object]:
    try:
        value = json.loads(MONITOR_TELEMETRY.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"factory_status": "WAITING", "nodes": [], "telemetry_available": False}
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
        return {"factory_status": "WAITING", "nodes": [], "telemetry_available": False}
    value["telemetry_available"] = True
    return value


def register_brief(brief: dict[str, object]) -> tuple[dict[str, object], bool]:
    sprint_id = sprint_id_for(str(brief["request_id"]))
    digest = hashlib.sha256(canonical_json(brief).encode("utf-8")).hexdigest()
    existing = load_record(sprint_id)
    if existing:
        if existing.get("request_id") != brief["request_id"] or existing.get("brief_hash") != digest:
            raise ValidationError("request_id already exists with a different brief")
        return existing, True
    record: dict[str, object] = {
        "request_id": brief["request_id"],
        "sprint_id": sprint_id,
        "brief_hash": digest,
        "created_at": utc_now(),
        "current_state": "VALIDATED_NOT_SUBMITTED",
        "factory_execution_id": None,
        "completed_at": None,
        "result_status": None,
        "requested_target": brief["requested_target"],
        "result": None,
    }
    write_record(record)
    return record, False


def invoke_factory(sprint_id: str, brief: dict[str, object]) -> None:
    record = load_record(sprint_id)
    if not record:
        return
    record["current_state"] = "SUBMITTING"
    write_record(record)
    payload = canonical_json({**brief, "sprint_id": sprint_id, "writer_target": brief["requested_target"]}).encode("utf-8")
    req = request.Request(FACTORY_WEBHOOK, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=25 * 60) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise RuntimeError("factory response exceeded size limit")
            raw_result = json.loads(body.decode("utf-8"))
            if not isinstance(raw_result, dict):
                raise RuntimeError("factory response must be an object")
            report = raw_result.get("sprint_report") if isinstance(raw_result.get("sprint_report"), dict) else raw_result
            inspector = report.get("factory_inspector") if isinstance(report.get("factory_inspector"), dict) else None
            result = {
                "sprint_id": report.get("sprint_id", sprint_id),
                "status": report.get("status") or report.get("result_status"),
                "completed_task_count": len(report.get("completed_task_ids", [])) if isinstance(report.get("completed_task_ids"), list) else None,
                "review_backlog_count": report.get("review_backlog_count"),
                "deferred_blocker_count": report.get("deferred_blocker_count"),
                "factory_inspector": inspector,
            }
            execution_id = raw_result.get("execution_id")
            if isinstance(execution_id, (str, int)):
                record["factory_execution_id"] = str(execution_id)
        record["current_state"] = "COMPLETED"
        record["result_status"] = "SUCCESS"
        record["result"] = result
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        record["current_state"] = "FAILED"
        record["result_status"] = "FAILED"
        record["result"] = {"error_type": type(exc).__name__}
    record["completed_at"] = utc_now()
    write_record(record)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "FactoryDirectorBridge/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, status: int, value: object) -> None:
        body = canonical_json(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_asset(self, name: str, *, session_cookie: bool = False) -> None:
        body = (ASSET_DIR / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        if session_cookie:
            self.send_header("Set-Cookie", f"factory_console_session={CONSOLE_SESSION}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def console_authorized(self) -> bool:
        cookies = self.headers.get("Cookie", "")
        valid_cookie = any(hmac.compare_digest(part.strip(), f"factory_console_session={CONSOLE_SESSION}") for part in cookies.split(";"))
        origin = self.headers.get("Origin")
        valid_origin = not origin or origin == f"http://{HOST}:{PORT}"
        return valid_cookie and valid_origin

    def project_authorized(self) -> bool:
        return self.console_authorized() or self.authorized()

    def read_json_body(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES * 4:
            raise ValidationError("invalid_body_size")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def handle_project_post(self) -> bool:
        match = re.fullmatch(r"/api/projects/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})/(pause|resume)", self.path)
        if self.path not in {"/api/projects/validate", "/api/projects/prepare", "/api/projects/start"} and not match:
            return False
        if not self.project_authorized():
            self.send_json(401, {"error": "unauthorized"})
            return True
        try:
            if self.path == "/api/projects/validate":
                manifest = validate_manifest(self.read_json_body())
                self.send_json(200, {"valid": True, "project_id": manifest["project_id"], "project_title": manifest["project_title"], "work_unit_count": len(manifest["work_units"])})
            elif self.path == "/api/projects/prepare":
                manifest = validate_manifest(self.read_json_body())
                self.send_json(201, public_state(prepare_project(manifest)))
            elif self.path == "/api/projects/start":
                if os.environ.get("FACTORY_EXECUTION_ENABLED") != "true":
                    self.send_json(503, {"error": "factory_execution_disabled"})
                else:
                    payload = self.read_json_body()
                    if not isinstance(payload, dict) or set(payload) != {"project_id"} or not isinstance(payload.get("project_id"), str):
                        raise ManifestError("start requires exactly one prepared project_id")
                    self.send_json(202, public_state(start_project(payload["project_id"])))
            elif match and match.group(2) == "pause":
                self.send_json(200, public_state(pause_project(match.group(1))))
            elif match:
                self.send_json(200, public_state(resume_project(match.group(1))))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ManifestError) as exc:
            self.send_json(400, {"error": "invalid_project_request", "reason": str(exc)})
        return True

    def authorized(self) -> bool:
        expected = os.environ.get("FACTORY_BRIDGE_TOKEN", "")
        supplied = self.headers.get("Authorization", "")
        if not expected or not supplied.startswith("Bearer "):
            return False
        return hmac.compare_digest(supplied[7:], expected)

    def do_POST(self) -> None:
        if self.handle_project_post():
            return
        if self.path != "/v1/start_sprint":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "invalid_body_size"})
            return
        try:
            brief = validate_brief(json.loads(self.rfile.read(length).decode("utf-8")))
            record, duplicate = register_brief(brief)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            self.send_json(400, {"error": "invalid_brief", "reason": str(exc)})
            return
        enabled = os.environ.get("FACTORY_EXECUTION_ENABLED") == "true"
        if enabled and not duplicate and record["current_state"] == "VALIDATED_NOT_SUBMITTED":
            threading.Thread(target=invoke_factory, args=(str(record["sprint_id"]), brief), daemon=True).start()
        self.send_json(200 if duplicate else 202, {
            "sprint_id": record["sprint_id"],
            "current_state": record["current_state"],
            "duplicate": duplicate,
            "factory_submission_enabled": enabled,
        })

    def do_GET(self) -> None:
        if self.path == "/":
            self.send_asset("index.html", session_cookie=True)
            return
        if self.path in {"/console.js", "/console.css"}:
            self.send_asset(self.path[1:])
            return
        if self.path == "/api/factory-status":
            if not self.project_authorized():
                self.send_json(401, {"error": "unauthorized"})
                return
            self.send_json(200, read_factory_telemetry())
            return
        project_match = re.fullmatch(r"/api/projects/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})", self.path)
        if project_match:
            if not self.project_authorized():
                self.send_json(401, {"error": "unauthorized"})
                return
            state = load_state(project_match.group(1))
            self.send_json(200, public_state(state)) if state else self.send_json(404, {"error": "project_not_found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        match = re.fullmatch(r"/v1/sprints/(SPRINT-[A-F0-9]{20})/(status|result)", self.path)
        if not match:
            self.send_json(404, {"error": "not_found"})
            return
        record = load_record(match.group(1))
        if not record:
            self.send_json(404, {"error": "unknown_sprint"})
            return
        if match.group(2) == "status":
            keys = ("request_id", "sprint_id", "created_at", "current_state", "factory_execution_id", "completed_at", "result_status")
        else:
            keys = ("sprint_id", "current_state", "completed_at", "result_status", "result")
        self.send_json(200, {key: record.get(key) for key in keys})


def main() -> None:
    if not os.environ.get("FACTORY_BRIDGE_TOKEN"):
        os.environ["FACTORY_BRIDGE_TOKEN"] = secrets.token_urlsafe(32)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    recover_interrupted_projects()
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    print(f"Director Bridge listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
