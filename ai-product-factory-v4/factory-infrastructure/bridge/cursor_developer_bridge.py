#!/usr/bin/env python3
"""Trusted local Cursor applied-developer bridge for AI Product Factory V4."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
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
# Inspect-and-keep: only include a candidate if that directory exists on this host.
TRUSTED_RUNTIME_DIR_CANDIDATES = (
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
    "/bin",
)
TRUSTED_EXECUTABLE_NAMES = frozenset({"node", "npm", "python3"})
APPROVED_NPM_TEST_ARGV = ("test", "--silent")
APPROVED_PYTHON_TEST_ARGV = ("-m", "unittest", "discover", "-s", ".", "-q")
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
    "recovery_mode",
    "recovery_verification",
    "prior_execution_id",
    "provenance",
    "fingerprint",
    "receipts",
})
RECOVERY_MODE = "PRIOR_APPLY_VERIFICATION"
TRUSTED_RUNTIME_ROOT = Path.home() / "Library/Application Support/ai-product-factory-v4"
DEFAULT_RECEIPTS_DIR = TRUSTED_RUNTIME_ROOT / "cursor_receipts"
RECEIPTS_DIR = DEFAULT_RECEIPTS_DIR
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
    workspace = cwd.resolve()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_DIR"] = str(workspace / ".git")
    env["GIT_WORK_TREE"] = str(workspace)
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
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


def empty_recovery(reason: str | None = None) -> dict[str, object]:
    return {
        "eligible": False,
        "mode": None,
        "prior_execution_id": None,
        "prior_implementation_applied": False,
        "prior_changed_files": [],
        "workspace_fingerprint_matched": False,
        "reason": reason,
    }


def _downloads_root() -> Path:
    return (Path.home() / "Downloads").resolve()


def path_is_under_downloads(path: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(_downloads_root())
        return True
    except (ValueError, OSError):
        return False


def receipts_dir() -> Path:
    candidate = RECEIPTS_DIR
    if path_is_under_downloads(candidate):
        return DEFAULT_RECEIPTS_DIR
    return candidate


def ensure_receipts_dir() -> Path:
    directory = receipts_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def write_private_json(path: Path, payload: dict[str, object]) -> None:
    directory = ensure_receipts_dir()
    target = directory / path.name
    tmp = target.with_name(target.name + ".tmp")
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        tmp.write_text(body, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except OSError:
        unlink_quietly(tmp)
        raise


def _receipt_stem(project_id: str, work_unit_id: str) -> str:
    return f"{project_id}__{work_unit_id}"


def provenance_path(project_id: str, work_unit_id: str) -> Path:
    return receipts_dir() / f"{_receipt_stem(project_id, work_unit_id)}.provenance.json"


def intent_path(project_id: str, work_unit_id: str) -> Path:
    return receipts_dir() / f"{_receipt_stem(project_id, work_unit_id)}.recovery-intent.json"


def git_head(workspace: Path) -> str:
    result = git(workspace, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def tracked_files(workspace: Path) -> list[str]:
    result = git(workspace, "ls-files")
    return [line for line in result.stdout.splitlines() if line]


def untracked_files(workspace: Path) -> list[str]:
    result = git(workspace, "ls-files", "--others", "--exclude-standard")
    return [line for line in result.stdout.splitlines() if line]


def _safe_workspace_file(workspace: Path, rel: str) -> Path | None:
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    root = workspace.resolve()
    path = (workspace / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_fingerprints(workspace: Path, files: list[str]) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for rel in files:
        path = _safe_workspace_file(workspace, rel)
        if path is None:
            continue
        fingerprints[rel] = file_sha256(path)
    return fingerprints


def bounded_workspace_relpaths(workspace: Path, *, maximum: int = 200) -> list[str]:
    root = workspace.resolve()
    rels: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if ".git" in rel.parts or ".." in rel.parts:
            continue
        rels.append(str(rel).replace("\\", "/"))
        if len(rels) >= maximum:
            break
    return rels


def workspace_content_fingerprints(workspace: Path) -> dict[str, str]:
    return workspace_fingerprints(workspace, bounded_workspace_relpaths(workspace))


def content_changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    names = sorted(set(before) | set(after))
    return [name for name in names if before.get(name) != after.get(name)]


def write_applied_provenance(
    workspace: Path,
    project_id: str,
    work_unit_id: str,
    changed_files: list[str],
    *,
    prior_execution_id: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    ensure_receipts_dir()
    tracked = tracked_files(workspace)
    payload = {
        "project_id": project_id,
        "work_unit_id": work_unit_id,
        "implementation_applied": True,
        "prior_execution_id": prior_execution_id,
        "changed_files": list(changed_files),
        "tracked_files": tracked,
        "git_head": git_head(workspace),
        "file_fingerprints": workspace_fingerprints(workspace, tracked),
    }
    if extra:
        payload.update(extra)
    write_private_json(provenance_path(project_id, work_unit_id), payload)


def load_applied_provenance(project_id: str, work_unit_id: str) -> dict[str, object] | None:
    path = provenance_path(project_id, work_unit_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("project_id") != project_id or data.get("work_unit_id") != work_unit_id:
        return None
    return data


def write_recovery_intent(
    project_id: str,
    work_unit_id: str,
    *,
    remediation_requested: bool = False,
) -> None:
    write_private_json(
        intent_path(project_id, work_unit_id),
        {
            "mode": RECOVERY_MODE,
            "project_id": project_id,
            "work_unit_id": work_unit_id,
            "created_at_ms": utc_ms(),
            "remediation_requested": remediation_requested is True,
        },
    )


def peek_recovery_intent(project_id: str, work_unit_id: str) -> dict[str, object] | None:
    path = intent_path(project_id, work_unit_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("mode") != RECOVERY_MODE:
        return None
    if data.get("project_id") != project_id or data.get("work_unit_id") != work_unit_id:
        return None
    return data


def consume_recovery_intent(project_id: str, work_unit_id: str) -> dict[str, object] | None:
    path = intent_path(project_id, work_unit_id)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        unlink_quietly(path)
        return None
    unlink_quietly(path)
    if not isinstance(data, dict):
        return None
    if data.get("mode") != RECOVERY_MODE:
        return None
    if data.get("project_id") != project_id or data.get("work_unit_id") != work_unit_id:
        return None
    return data


def evaluate_recovery_lineage(
    workspace: Path,
    project_id: str,
    work_unit_id: str,
    provenance: dict[str, object],
) -> tuple[bool, str | None]:
    prior_id = str(provenance.get("prior_execution_id") or "").strip()
    changed = provenance.get("changed_files")
    tracked = provenance.get("tracked_files")
    fingerprints = provenance.get("file_fingerprints")
    expected_head = str(provenance.get("git_head") or "").strip()
    if provenance.get("implementation_applied") is not True:
        return False, "prior_apply_not_recorded"
    if not prior_id:
        return False, "prior_execution_id_missing"
    if not isinstance(changed, list) or not changed or any(not isinstance(item, str) or not item for item in changed):
        return False, "prior_changed_files_missing"
    if not isinstance(tracked, list) or not tracked or any(not isinstance(item, str) or not item for item in tracked):
        return False, "prior_tracked_files_missing"
    if not isinstance(fingerprints, dict) or not fingerprints:
        return False, "prior_fingerprints_missing"
    if porcelain_files(workspace) or untracked_files(workspace):
        return False, "unrelated_workspace_mutation"
    if expected_head and git_head(workspace) != expected_head:
        return False, "git_head_mismatch"
    current_tracked = tracked_files(workspace)
    if set(current_tracked) != set(tracked):
        return False, "tracked_files_mismatch"
    current = workspace_fingerprints(workspace, list(tracked))
    expected = {str(key): str(value) for key, value in fingerprints.items()}
    if current != expected:
        return False, "fingerprint_mismatch"
    return True, None


def attempt_recovery_verification(workspace: Path, project_id: str, work_unit_id: str) -> dict[str, object]:
    intent = consume_recovery_intent(project_id, work_unit_id)
    if intent is None:
        return empty_recovery("recovery_mode_not_explicit")
    provenance = load_applied_provenance(project_id, work_unit_id)
    if provenance is None:
        return empty_recovery("prior_provenance_missing")
    matched, reason = evaluate_recovery_lineage(workspace, project_id, work_unit_id, provenance)
    prior_files = [item for item in provenance.get("changed_files") or [] if isinstance(item, str)]
    recovery = {
        "eligible": matched,
        "mode": RECOVERY_MODE if matched else None,
        "prior_execution_id": str(provenance.get("prior_execution_id") or "") or None,
        "prior_implementation_applied": provenance.get("implementation_applied") is True,
        "prior_changed_files": prior_files,
        "workspace_fingerprint_matched": matched,
        "reason": None if matched else reason,
    }
    return recovery


def existing_trusted_runtime_dirs() -> list[str]:
    dirs: list[str] = []
    seen: set[str] = set()
    for raw in TRUSTED_RUNTIME_DIR_CANDIDATES:
        path = Path(raw)
        if not path.is_dir():
            continue
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        dirs.append(resolved)
    return dirs


def trusted_runtime_path() -> str:
    return os.pathsep.join(existing_trusted_runtime_dirs())


def resolve_trusted_executable(name: str) -> Path | None:
    if name not in TRUSTED_EXECUTABLE_NAMES:
        return None
    for directory in existing_trusted_runtime_dirs():
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def trusted_subprocess_env(*, extra_dirs: tuple[str, ...] = ()) -> dict[str, str]:
    env = {key: os.environ[key] for key in ("HOME", "USER", "LANG", "TERM") if key in os.environ}
    dirs = existing_trusted_runtime_dirs()
    for raw in extra_dirs:
        path = Path(raw)
        if path.is_dir():
            resolved = str(path.resolve())
            if resolved not in dirs:
                dirs.append(resolved)
    env["PATH"] = os.pathsep.join(dirs)
    return env


def package_has_approved_test_script(workspace: Path) -> bool:
    package = workspace / "package.json"
    if not package.is_file():
        return False
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return False
    test = scripts.get("test")
    return isinstance(test, str) and bool(test.strip())


def _summarize_output(value: str | None, limit: int = 1_500) -> str:
    return (value or "")[-limit:]


def empty_test_evidence() -> dict[str, object]:
    return {
        "tests_executed": [],
        "test_commands": [],
        "test_exit_codes": [],
        "tests_passed": False,
        "test_stdout_summary": "",
        "test_stderr_summary": "",
        "test_duration_ms": 0,
    }


def run_known_tests(workspace: Path) -> dict[str, object]:
    evidence = empty_test_evidence()
    env = trusted_subprocess_env()
    started = utc_ms()
    command: list[str] | None = None
    label = ""
    if package_has_approved_test_script(workspace):
        npm = resolve_trusted_executable("npm")
        node = resolve_trusted_executable("node")
        if npm is not None and node is not None:
            command = [str(npm), *APPROVED_NPM_TEST_ARGV]
            label = "npm test"
    elif (workspace / "tests").exists() or list(workspace.glob("test_*.py")):
        python3 = resolve_trusted_executable("python3")
        if python3 is not None:
            command = [str(python3), *APPROVED_PYTHON_TEST_ARGV]
            label = "python3 -m unittest discover -s . -q"
    if command is None:
        return evidence
    result = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    evidence["tests_executed"] = [label]
    evidence["test_commands"] = [" ".join(command)]
    evidence["test_exit_codes"] = [int(result.returncode)]
    evidence["tests_passed"] = int(result.returncode) == 0
    evidence["test_stdout_summary"] = _summarize_output(result.stdout)
    evidence["test_stderr_summary"] = _summarize_output(result.stderr)
    evidence["test_duration_ms"] = max(0, utc_ms() - started)
    return evidence


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
        agent_dir = str(agent.resolve().parent)
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=CURSOR_TIMEOUT_SEC,
            env=trusted_subprocess_env(extra_dirs=(agent_dir,)),
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


def _result_payload(
    *,
    payload: dict[str, object],
    started: int,
    applied: bool,
    changed: list[str],
    diff: str,
    tests: dict[str, object],
    runtime: list[str],
    exit_code: int | None,
    summary: str,
    failure: str | None,
    recovery: dict[str, object],
    developer_action: str,
    developer_write_required: bool,
) -> dict[str, object]:
    recovery_verified = (
        recovery.get("eligible") is True
        and bool(tests["tests_executed"])
        and tests["tests_passed"] is True
        and failure is None
        and (exit_code in {0, None} or developer_action == "SKIP_ALREADY_APPLIED")
    )
    completed = bool(applied or recovery_verified)
    exit_ok = exit_code in {0, None} or developer_action == "SKIP_ALREADY_APPLIED"
    return {
        "developer": "CURSOR",
        "developer_class": "APPLIED_DEVELOPER",
        "project_id": payload["project_id"],
        "work_unit_id": payload["work_unit_id"],
        "implementation_applied": applied,
        "changed_files": changed,
        "diff_present": bool(diff),
        "tests_executed": tests["tests_executed"],
        "test_commands": tests["test_commands"],
        "test_exit_codes": tests["test_exit_codes"],
        "tests_passed": tests["tests_passed"],
        "test_stdout_summary": tests["test_stdout_summary"],
        "test_stderr_summary": tests["test_stderr_summary"],
        "test_duration_ms": tests["test_duration_ms"],
        "runtime_checks": runtime,
        "cursor_exit_code": 0 if developer_action == "SKIP_ALREADY_APPLIED" else exit_code,
        "cursor_cli_invoked": developer_action != "SKIP_ALREADY_APPLIED",
        "developer_action": developer_action,
        "developer_write_required": developer_write_required,
        "execution_duration_ms": utc_ms() - started,
        "developer_summary": summary[:2000],
        "failure_class": failure,
        "recovery_verification": recovery,
        "status": "COMPLETED" if completed else "FAILED",
        "task_completed": completed,
        "ok": completed and exit_ok and failure is None,
    }


def attempt_skip_already_applied(
    workspace: Path,
    project_id: str,
    work_unit_id: str,
) -> dict[str, object] | None:
    intent = peek_recovery_intent(project_id, work_unit_id)
    if intent is not None and intent.get("remediation_requested") is True:
        return None
    provenance = load_applied_provenance(project_id, work_unit_id)
    if provenance is None:
        return None
    matched, _reason = evaluate_recovery_lineage(workspace, project_id, work_unit_id, provenance)
    if not matched:
        return None
    if intent is not None:
        consume_recovery_intent(project_id, work_unit_id)
    tests = run_known_tests(workspace)
    runtime = runtime_checks(workspace)
    failure = None
    if tests["tests_executed"] and not tests["tests_passed"]:
        failure = "CURSOR_TEST_FAILURE"
    elif not tests["tests_executed"]:
        failure = "CURSOR_NO_APPLIED_CHANGE"
    prior_files = [item for item in provenance.get("changed_files") or [] if isinstance(item, str)]
    recovery = {
        "eligible": failure is None,
        "mode": RECOVERY_MODE if failure is None else None,
        "prior_execution_id": str(provenance.get("prior_execution_id") or "") or None,
        "prior_implementation_applied": provenance.get("implementation_applied") is True,
        "prior_changed_files": prior_files,
        "workspace_fingerprint_matched": True,
        "reason": None if failure is None else failure,
    }
    return {
        "tests": tests,
        "runtime": runtime,
        "failure": failure,
        "recovery": recovery,
        "summary": "SKIP_ALREADY_APPLIED: trusted applied provenance reused; developer write skipped",
    }


def execute_task(payload: dict[str, object]) -> dict[str, object]:
    started = utc_ms()
    project_id = str(payload["project_id"])
    work_unit_id = str(payload["work_unit_id"])
    workspace = trusted_workspace(project_id)
    ensure_repo(workspace)
    with LOCK:
        skipped = attempt_skip_already_applied(workspace, project_id, work_unit_id)
        if skipped is not None:
            return _result_payload(
                payload=payload,
                started=started,
                applied=False,
                changed=[],
                diff="",
                tests=skipped["tests"],
                runtime=skipped["runtime"],
                exit_code=0,
                summary=str(skipped["summary"]),
                failure=skipped["failure"],
                recovery=skipped["recovery"],
                developer_action="SKIP_ALREADY_APPLIED",
                developer_write_required=False,
            )
        prompt = build_prompt(payload)
        before_hashes = workspace_content_fingerprints(workspace)
        exit_code, _stdout, summary, failure = invoke_cursor(workspace, prompt)
    after_hashes = workspace_content_fingerprints(workspace)
    changed = content_changed_files(before_hashes, after_hashes)
    diff = diff_text(workspace) if changed else ""
    applied = bool(changed) and failure not in {"CURSOR_UNAVAILABLE", "CURSOR_UNAUTHENTICATED", "CURSOR_TIMEOUT"}
    tests = empty_test_evidence()
    runtime: list[str] = []
    recovery = empty_recovery()
    if applied:
        consume_recovery_intent(project_id, work_unit_id)
        tests = run_known_tests(workspace)
        runtime = runtime_checks(workspace)
        snapshot_commit(workspace, work_unit_id)
        write_applied_provenance(workspace, project_id, work_unit_id, changed)
        if tests["tests_executed"] and not tests["tests_passed"]:
            failure = "CURSOR_TEST_FAILURE"
    elif exit_code == 0 and failure is None:
        try:
            recovery = attempt_recovery_verification(workspace, project_id, work_unit_id)
        except OSError:
            recovery = empty_recovery("recovery_control_unavailable")
        if recovery["eligible"]:
            tests = run_known_tests(workspace)
            runtime = runtime_checks(workspace)
            if tests["tests_executed"] and not tests["tests_passed"]:
                failure = "CURSOR_TEST_FAILURE"
            elif not tests["tests_executed"]:
                failure = "CURSOR_NO_APPLIED_CHANGE"
        else:
            failure = "CURSOR_NO_APPLIED_CHANGE"
    return _result_payload(
        payload=payload,
        started=started,
        applied=applied,
        changed=changed,
        diff=diff,
        tests=tests,
        runtime=runtime,
        exit_code=exit_code,
        summary=summary,
        failure=failure,
        recovery=recovery,
        developer_action="WRITE",
        developer_write_required=True,
    )


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
                **empty_test_evidence(),
                "runtime_checks": [],
                "cursor_exit_code": None,
                "failure_class": exc.failure_class,
                "developer_summary": str(exc)[:300],
                "recovery_verification": empty_recovery(),
                "ok": False,
                "task_completed": False,
                "status": "FAILED",
            })
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid_json", "failure_class": "CURSOR_MALFORMED_RESULT"})
        except Exception as exc:
            self.send_json(500, {
                "developer": "CURSOR",
                "developer_class": "APPLIED_DEVELOPER",
                "implementation_applied": False,
                "changed_files": [],
                "diff_present": False,
                **empty_test_evidence(),
                "runtime_checks": [],
                "cursor_exit_code": None,
                "failure_class": "CURSOR_EXECUTION_ERROR",
                "developer_summary": str(exc)[:300],
                "recovery_verification": empty_recovery(),
                "ok": False,
                "task_completed": False,
                "status": "FAILED",
            })


def main() -> None:
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), CursorBridgeHandler)
    print(f"Cursor Developer Bridge listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
