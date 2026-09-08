#!/usr/bin/env python3
"""Trusted local Cursor applied-developer bridge for AI Product Factory V4."""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse

HOST = os.environ.get("FACTORY_CURSOR_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("FACTORY_CURSOR_BRIDGE_PORT", "8766"))
AGENT_BIN = os.environ.get("FACTORY_CURSOR_AGENT_BIN", str(Path.home() / ".local" / "bin" / "agent"))
WORKSPACES_ROOT = Path(
    os.environ.get(
        "FACTORY_CURSOR_WORKSPACES",
        str(Path(__file__).resolve().parents[1] / "workspaces"),
    )
).resolve()
ALLOWED_PROJECT_IDS = frozenset({"PROJECT-HANGMAN-PILOT-001"})
PROJECT_ID_RE = re.compile(r"^PROJECT-[A-Z0-9][A-Z0-9._-]{0,80}$")
ALLOWED_FIELDS = frozenset({
    "project_id",
    "work_unit_id",
    "sprint_id",
    "task_id",
    "objective",
    "requirements",
    "constraints",
    "acceptance_criteria",
    "test_requirements",
    "planner_task",
    "known_context",
})
REJECTED_FIELDS = frozenset({
    "workspace",
    "workspace_path",
    "path",
    "cwd",
    "executable",
    "command",
    "shell",
    "shell_command",
    "env",
    "environment",
    "flags",
    "cursor_flags",
    "workflow_id",
    "api_key",
    "token",
    "credentials",
    "repository",
    "repo",
    "git_url",
})
MAX_BODY_BYTES = 64 * 1024
CURSOR_TIMEOUT_SEC = int(os.environ.get("FACTORY_CURSOR_TIMEOUT_SEC", "480"))
LOCK = threading.Lock()


class BridgeError(ValueError):
    def __init__(self, failure_class: str, message: str, status: int = 400):
        super().__init__(message)
        self.failure_class = failure_class
        self.status = status


def utc_ms() -> int:
    return int(time.time() * 1000)


def expected_token() -> str:
    return os.environ.get("FACTORY_BRIDGE_TOKEN") or os.environ.get("FACTORY_CURSOR_BRIDGE_TOKEN") or ""


def authorized(handler: BaseHTTPRequestHandler) -> bool:
    expected = expected_token()
    supplied = handler.headers.get("Authorization", "")
    if not expected or not supplied.startswith("Bearer "):
        return False
    return hmac.compare_digest(supplied[7:], expected)


def bounded_text(value: object, name: str, maximum: int = 4_000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BridgeError("CURSOR_MALFORMED_RESULT", f"{name} must be a string")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise BridgeError("CURSOR_MALFORMED_RESULT", f"{name} exceeds {maximum} characters")
    return cleaned


def bounded_string_list(value: object, name: str, *, maximum: int = 50) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise BridgeError("CURSOR_MALFORMED_RESULT", f"{name} must be a bounded string array")
    return [bounded_text(item, f"{name}[]", 1_000) for item in value]


def trusted_workspace(project_id: str) -> Path:
    if not PROJECT_ID_RE.fullmatch(project_id) or project_id not in ALLOWED_PROJECT_IDS:
        raise BridgeError("CURSOR_MALFORMED_RESULT", "project_id is not allowlisted")
    root = WORKSPACES_ROOT.resolve()
    workspace = (root / project_id).resolve()
    if workspace.parent != root or workspace.name != project_id:
        raise BridgeError("CURSOR_MALFORMED_RESULT", "workspace path rejected")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def git(cwd: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


def ensure_repo(workspace: Path) -> None:
    if not (workspace / ".git").exists():
        git(workspace, "init", check=True)
        git(workspace, "add", "-A")
        git(
            workspace,
            "-c",
            "user.email=factory@local",
            "-c",
            "user.name=Factory",
            "commit",
            "--allow-empty",
            "-m",
            "factory baseline",
            check=True,
        )


def porcelain_files(workspace: Path) -> list[str]:
    result = git(workspace, "status", "--porcelain")
    files: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in files:
            files.append(path)
    return files


def diff_text(workspace: Path) -> str:
    named = git(workspace, "diff", "--name-only", "HEAD")
    untracked = git(workspace, "ls-files", "--others", "--exclude-standard")
    diff = git(workspace, "diff", "HEAD")
    parts = [named.stdout.strip(), untracked.stdout.strip(), diff.stdout.strip()]
    return "\n".join(part for part in parts if part)


def snapshot_commit(workspace: Path, work_unit_id: str) -> None:
    git(workspace, "add", "-A")
    status = git(workspace, "status", "--porcelain")
    if not status.stdout.strip():
        return
    git(
        workspace,
        "-c",
        "user.email=factory@local",
        "-c",
        "user.name=Factory",
        "commit",
        "-m",
        f"factory snapshot {work_unit_id}",
    )


def run_known_tests(workspace: Path) -> tuple[list[str], list[int], bool]:
    executed: list[str] = []
    codes: list[int] = []
    if (workspace / "package.json").exists() and shutil.which("npm"):
        executed.append("npm test")
        result = subprocess.run(
            ["npm", "test", "--silent"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
        codes.append(int(result.returncode))
    elif (workspace / "tests").exists() or list(workspace.glob("test_*.py")):
        executed.append("python3 -m unittest discover -s . -q")
        result = subprocess.run(
            ["python3", "-m", "unittest", "discover", "-s", ".", "-q"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
        codes.append(int(result.returncode))
    passed = bool(executed) and all(code == 0 for code in codes)
    return executed, codes, passed


def runtime_checks(workspace: Path) -> list[str]:
    checks: list[str] = []
    html = workspace / "index.html"
    if html.exists():
        text = html.read_text(encoding="utf-8", errors="replace")[:20_000].lower()
        if "<html" in text or "<!doctype html" in text:
            checks.append("index.html contains an HTML document")
        if "hangman" in text:
            checks.append("index.html contains hangman shell text")
    return checks


def build_prompt(payload: dict[str, object]) -> str:
    planner = payload.get("planner_task") if isinstance(payload.get("planner_task"), dict) else {}
    task = {
        "task_id": payload.get("task_id") or planner.get("task_id"),
        "objective": payload.get("objective") or planner.get("objective"),
        "expected_result": planner.get("expected_result"),
        "acceptance_criteria": payload.get("acceptance_criteria") or planner.get("acceptance_criteria") or [],
        "test_requirements": payload.get("test_requirements") or planner.get("test_requirements") or [],
    }
    contract = {
        "project_id": payload.get("project_id"),
        "work_unit_id": payload.get("work_unit_id"),
        "sprint_id": payload.get("sprint_id"),
        "task": task,
        "requirements": payload.get("requirements") or [],
        "constraints": payload.get("constraints") or [],
        "acceptance_criteria": task["acceptance_criteria"],
        "known_context": payload.get("known_context") or [],
    }
    return (
        "You are the applied Developer for AI Product Factory V4.\n"
        "Implement only the supplied Work Unit in this trusted workspace.\n"
        "Do not access other repositories, credentials, environment variables, or n8n workflows.\n"
        "Do not invent secrets. Prefer a simple browser Hangman skeleton when that is the task.\n"
        "Add a tiny automated test if the workspace has no test yet.\n"
        "When finished, stop. Your prose is not evidence; the Factory will inspect the filesystem.\n"
        f"FACTORY_TASK={json.dumps(contract, ensure_ascii=False)}"
    )


def validate_payload(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise BridgeError("CURSOR_MALFORMED_RESULT", "request body must be an object")
    rejected = sorted(set(raw) & REJECTED_FIELDS)
    if rejected:
        raise BridgeError("CURSOR_MALFORMED_RESULT", "unsupported control fields rejected")
    unexpected = sorted(set(raw) - ALLOWED_FIELDS)
    if unexpected:
        raise BridgeError("CURSOR_MALFORMED_RESULT", "unexpected fields rejected")
    project_id = bounded_text(raw.get("project_id"), "project_id", 128)
    work_unit_id = bounded_text(raw.get("work_unit_id"), "work_unit_id", 32)
    if not project_id or not work_unit_id:
        raise BridgeError("CURSOR_MALFORMED_RESULT", "project_id and work_unit_id are required")
    if project_id not in ALLOWED_PROJECT_IDS:
        raise BridgeError("CURSOR_MALFORMED_RESULT", "project_id is not allowlisted")
    planner = raw.get("planner_task") if isinstance(raw.get("planner_task"), dict) else {}
    planner_clean = {
        "task_id": bounded_text(planner.get("task_id"), "planner_task.task_id", 64),
        "objective": bounded_text(planner.get("objective"), "planner_task.objective"),
        "expected_result": bounded_text(planner.get("expected_result"), "planner_task.expected_result"),
        "acceptance_criteria": bounded_string_list(planner.get("acceptance_criteria"), "planner_task.acceptance_criteria"),
        "test_requirements": bounded_string_list(planner.get("test_requirements"), "planner_task.test_requirements"),
    }
    return {
        "project_id": project_id,
        "work_unit_id": work_unit_id,
        "sprint_id": bounded_text(raw.get("sprint_id"), "sprint_id", 128),
        "task_id": bounded_text(raw.get("task_id"), "task_id", 64) or planner_clean["task_id"],
        "objective": bounded_text(raw.get("objective"), "objective") or planner_clean["objective"],
        "requirements": bounded_string_list(raw.get("requirements"), "requirements"),
        "constraints": bounded_string_list(raw.get("constraints"), "constraints"),
        "acceptance_criteria": bounded_string_list(raw.get("acceptance_criteria"), "acceptance_criteria") or planner_clean["acceptance_criteria"],
        "test_requirements": bounded_string_list(raw.get("test_requirements"), "test_requirements") or planner_clean["test_requirements"],
        "planner_task": planner_clean,
        "known_context": bounded_string_list(raw.get("known_context"), "known_context", maximum=30),
    }


def classify_agent_failure(returncode: int, stderr: str, stdout: str) -> str | None:
    blob = f"{stderr}\n{stdout}".lower()
    if "authentication required" in blob or "not logged in" in blob:
        return "CURSOR_UNAUTHENTICATED"
    if returncode in {124, -9} or "timed out" in blob:
        return "CURSOR_TIMEOUT"
    if returncode != 0:
        return "CURSOR_EXECUTION_ERROR"
    return None


def invoke_cursor(workspace: Path, prompt: str) -> tuple[int, str, str, str | None]:
    agent = Path(AGENT_BIN).expanduser()
    if not agent.exists():
        raise BridgeError("CURSOR_UNAVAILABLE", "Cursor Agent CLI is not installed", status=503)
    command = [
        str(agent),
        "-p",
        "--output-format",
        "json",
        "--trust",
        "--force",
        "--workspace",
        str(workspace),
        prompt,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=CURSOR_TIMEOUT_SEC,
            env={key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "TERM") if key in os.environ},
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else "CURSOR_TIMEOUT"
        return 124, stdout[-4000:], stderr[-4000:], "CURSOR_TIMEOUT"
    stdout = (result.stdout or "")[-8000:]
    stderr = (result.stderr or "")[-4000:]
    summary = stdout
    try:
        parsed = json.loads(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "{}")
        if isinstance(parsed, dict):
            summary = str(parsed.get("result") or parsed.get("summary") or stdout)[:2000]
    except json.JSONDecodeError:
        pass
    failure = classify_agent_failure(int(result.returncode), stderr, stdout)
    return int(result.returncode), stdout, summary, failure


def execute_task(payload: dict[str, object]) -> dict[str, object]:
    started = utc_ms()
    workspace = trusted_workspace(str(payload["project_id"]))
    ensure_repo(workspace)
    prompt = build_prompt(payload)
    with LOCK:
        exit_code, _stdout, summary, failure = invoke_cursor(workspace, prompt)
    changed = porcelain_files(workspace)
    diff = diff_text(workspace)
    applied = bool(changed) and bool(diff) and failure not in {"CURSOR_UNAVAILABLE", "CURSOR_UNAUTHENTICATED", "CURSOR_TIMEOUT"}
    tests_executed, test_exit_codes, tests_passed = ([], [], False)
    runtime = []
    if applied:
        tests_executed, test_exit_codes, tests_passed = run_known_tests(workspace)
        runtime = runtime_checks(workspace)
        snapshot_commit(workspace, str(payload["work_unit_id"]))
    if applied and tests_executed and not tests_passed:
        failure = "CURSOR_TEST_FAILURE"
    elif not applied and failure is None:
        failure = "CURSOR_NO_APPLIED_CHANGE"
    return {
        "developer": "CURSOR",
        "developer_class": "APPLIED_DEVELOPER",
        "project_id": payload["project_id"],
        "work_unit_id": payload["work_unit_id"],
        "implementation_applied": applied,
        "changed_files": changed,
        "diff_present": bool(diff),
        "tests_executed": tests_executed,
        "test_exit_codes": test_exit_codes,
        "tests_passed": tests_passed,
        "runtime_checks": runtime,
        "cursor_exit_code": exit_code,
        "execution_duration_ms": utc_ms() - started,
        "developer_summary": summary[:2000],
        "failure_class": failure,
        "status": "COMPLETED" if applied else "FAILED",
        "task_completed": applied,
        "ok": applied and exit_code == 0 and failure is None,
    }


class CursorBridgeHandler(BaseHTTPRequestHandler):
    server_version = "FactoryCursorBridge/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/health":
            self.send_json(404, {"error": "not_found"})
            return
        self.send_json(200, {"ok": True, "developer": "CURSOR", "timeout_sec": CURSOR_TIMEOUT_SEC})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/v1/cursor-developer":
            self.send_json(404, {"error": "not_found"})
            return
        if not authorized(self):
            self.send_json(401, {"error": "CURSOR_UNAUTHENTICATED", "failure_class": "CURSOR_UNAUTHENTICATED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "invalid_body_size"})
            return
        try:
            payload = validate_payload(json.loads(self.rfile.read(length).decode("utf-8")))
            result = execute_task(payload)
            status = 200
            if result.get("failure_class") in {"CURSOR_UNAVAILABLE", "CURSOR_UNAUTHENTICATED"}:
                status = 503
            elif result.get("failure_class") == "CURSOR_TIMEOUT":
                status = 504
            self.send_json(status, result)
        except BridgeError as exc:
            status = exc.status
            self.send_json(status, {
                "developer": "CURSOR",
                "developer_class": "APPLIED_DEVELOPER",
                "implementation_applied": False,
                "changed_files": [],
                "diff_present": False,
                "tests_executed": [],
                "test_exit_codes": [],
                "tests_passed": False,
                "runtime_checks": [],
                "cursor_exit_code": None,
                "failure_class": exc.failure_class,
                "developer_summary": str(exc)[:300],
                "ok": False,
                "task_completed": False,
                "status": "FAILED",
            })
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid_json", "failure_class": "CURSOR_MALFORMED_RESULT"})


def main() -> None:
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), CursorBridgeHandler)
    print(f"Cursor Developer Bridge listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
