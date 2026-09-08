"""Deterministic Work Unit packager and sequential project orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
from typing import Callable, Optional
from urllib import request
from urllib.error import HTTPError, URLError


FACTORY_WEBHOOK = "http://127.0.0.1:5678/webhook/ai-product-factory-v4-sprint"
STATE_DIR = Path(__file__).resolve().parent / "state"
PROJECT_TELEMETRY_PATH = Path(__file__).resolve().parents[1] / "monitor" / "telemetry" / "latest.json"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_WORK_UNITS = 500
MAX_TEXT = 8_000
MAX_CONTEXT = 20_000
MAX_LIST = 100
MAX_DEPENDENCY_CONTEXT = 4_000
PROJECT_KEYS = {"schema_version", "project_id", "project_name", "project_goal", "global_context", "work_units"}
CONTEXT_KEYS = {"summary", "architecture", "constraints", "forbidden_actions", "completion_criteria"}
UNIT_KEYS = {"work_unit_id", "title", "objective", "context", "dependencies", "allowed_scope", "forbidden_scope", "acceptance_criteria", "test_requirements", "expected_artifacts", "priority"}
ALLOWED_ACTIONS = {"READ_PROJECT", "CREATE_FILES", "MODIFY_FILES", "RUN_TESTS"}
TERMINAL_OPEN = {"FAILED", "NEEDS_REVIEW", "DEFERRED", "BLOCKED"}
UNIT_STATUSES = {"PENDING", "READY", "RUNNING", "DONE", "FAILED", "NEEDS_REVIEW", "DEFERRED", "BLOCKED"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
LOCK = threading.RLock()
RUNNERS: dict[str, threading.Thread] = {}


class ManifestError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: object, name: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ManifestError(f"{name} exceeds {maximum} characters")
    return value


def strings(value: object, name: str, *, required: bool = False, maximum: int = MAX_LIST) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum or (required and not value):
        raise ManifestError(f"{name} must be a bounded string array")
    return [text(item, f"{name}[]", 1_000) for item in value]


def safe_relative_file(value: str, name: str) -> str:
    normalized = value.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or ".." in candidate.parts or normalized.startswith("~"):
        raise ManifestError(f"{name} contains an unsafe path")
    if any(part in {".git", ".env", ".codex"} for part in candidate.parts):
        raise ManifestError(f"{name} targets a protected path")
    return normalized


def validate_manifest(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ManifestError("manifest must be an object")
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest exceeds size limit")
    unexpected = set(payload) - PROJECT_KEYS
    missing = PROJECT_KEYS - set(payload)
    if unexpected or missing:
        raise ManifestError(f"manifest fields invalid; missing={sorted(missing)}, unexpected={sorted(unexpected)}")
    project_id = text(payload["project_id"], "project_id", 128)
    if not ID_PATTERN.fullmatch(project_id):
        raise ManifestError("project_id format is invalid")
    if payload["schema_version"] != "1.0":
        raise ManifestError("schema_version must be 1.0")
    context = payload["global_context"]
    if not isinstance(context, dict) or set(context) != CONTEXT_KEYS:
        raise ManifestError("global_context fields are invalid")
    normalized_context = {
        "summary": text(context["summary"], "summary", MAX_CONTEXT),
        "architecture": text(context["architecture"], "architecture", MAX_CONTEXT),
        "constraints": strings(context["constraints"], "constraints"),
        "forbidden_actions": strings(context["forbidden_actions"], "forbidden_actions", required=True),
        "completion_criteria": strings(context["completion_criteria"], "completion_criteria", required=True),
    }
    units = payload["work_units"]
    if not isinstance(units, list) or not units or len(units) > MAX_WORK_UNITS:
        raise ManifestError(f"work_units must contain 1-{MAX_WORK_UNITS} items")
    normalized_units = []
    identifiers: set[str] = set()
    for index, unit in enumerate(units):
        if not isinstance(unit, dict) or set(unit) != UNIT_KEYS:
            raise ManifestError(f"work_units[{index}] fields are invalid")
        unit_id = text(unit["work_unit_id"], f"work_units[{index}].work_unit_id", 128)
        if not ID_PATTERN.fullmatch(unit_id) or unit_id in identifiers:
            raise ManifestError(f"duplicate or invalid work unit id: {unit_id}")
        identifiers.add(unit_id)
        normalized_units.append({
            "id": unit_id,
            "work_unit_id": unit_id,
            "title": text(unit["title"], f"{unit_id}.title", 300),
            "goal": text(unit["objective"], f"{unit_id}.objective"),
            "description": text(unit["context"], f"{unit_id}.context", MAX_CONTEXT),
            "dependencies": strings(unit["dependencies"], f"{unit_id}.dependencies"),
            "allowed_files": [safe_relative_file(item, f"{unit_id}.allowed_scope") for item in strings(unit["allowed_scope"], f"{unit_id}.allowed_scope")],
            "forbidden_files": [safe_relative_file(item, f"{unit_id}.forbidden_scope") for item in strings(unit["forbidden_scope"], f"{unit_id}.forbidden_scope")],
            "acceptance_criteria": strings(unit["acceptance_criteria"], f"{unit_id}.acceptance_criteria", required=True),
            "test_requirements": strings(unit["test_requirements"], f"{unit_id}.test_requirements"),
            "expected_artifacts": [safe_relative_file(item, f"{unit_id}.expected_artifacts") for item in strings(unit["expected_artifacts"], f"{unit_id}.expected_artifacts")],
            "allowed_actions": sorted(ALLOWED_ACTIONS),
            "forbidden_actions": normalized_context["forbidden_actions"],
            "priority": unit["priority"],
        })
        if not isinstance(unit["priority"], int) or not 1 <= unit["priority"] <= 100:
            raise ManifestError(f"{unit_id}.priority must be an integer from 1 to 100")
    known = {unit["id"] for unit in normalized_units}
    for unit in normalized_units:
        unknown = set(unit["dependencies"]) - known
        if unknown or unit["id"] in unit["dependencies"]:
            raise ManifestError(f"{unit['id']} has unknown or self dependencies: {sorted(unknown)}")
    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {unit["id"]: unit for unit in normalized_units}

    def visit(unit_id: str) -> None:
        if unit_id in visiting:
            raise ManifestError("dependency cycle detected")
        if unit_id in visited:
            return
        visiting.add(unit_id)
        for dependency in by_id[unit_id]["dependencies"]:
            visit(dependency)
        visiting.remove(unit_id)
        visited.add(unit_id)

    for unit_id in by_id:
        visit(unit_id)
    return {
        "project_id": project_id,
        "schema_version": "1.0",
        "project_title": text(payload["project_name"], "project_name", 300),
        "project_goal": text(payload["project_goal"], "project_goal", MAX_CONTEXT),
        "global_context": normalized_context,
        "work_units": sorted(normalized_units, key=lambda unit: (unit["priority"], natural_id_key(unit["id"]))),
    }


def natural_id_key(value: str) -> tuple[tuple[int, object], ...]:
    """Compare identifiers predictably while treating digit runs numerically."""
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", value) if part)


def manifest_hash(manifest: dict[str, object]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def state_path(project_id: str) -> Path:
    return STATE_DIR / f"{project_id}.json"


def plan_path(project_id: str) -> Path:
    return STATE_DIR / "plans" / f"{project_id}.json"


def write_immutable_plan(manifest: dict[str, object]) -> None:
    (STATE_DIR / "plans").mkdir(parents=True, exist_ok=True)
    destination = plan_path(str(manifest["project_id"]))
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if manifest_hash(existing) != manifest_hash(manifest):
            raise ManifestError("immutable original plan conflicts with the supplied project")
        return
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def atomic_write(state: dict[str, object]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    destination = state_path(str(state["project_id"]))
    temporary = destination.with_suffix(f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    publish_project_status(state)


def publish_project_status(state: dict[str, object]) -> None:
    try:
        telemetry = json.loads(PROJECT_TELEMETRY_PATH.read_text(encoding="utf-8"))
        if not isinstance(telemetry, dict):
            telemetry = {"schema_version": 1, "nodes": []}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        telemetry = {"schema_version": 1, "nodes": []}
    units = state.get("work_units", [])
    plan = load_plan(str(state.get("project_id", ""))) or {"work_units": []}
    definitions = {unit["id"]: unit for unit in plan["work_units"]}
    current_id = state.get("current_work_unit_id")
    current = next((unit for unit in units if unit.get("id") == current_id), None)
    telemetry["project"] = {
        "project_id": state.get("project_id"),
        "project_title": state.get("project_title"),
        "status": state.get("status"),
        "done": sum(1 for unit in units if unit.get("status") == "DONE"),
        "total": len(units),
        "current_work_unit_id": current_id,
        "current_work_unit_title": definitions.get(current_id, {}).get("title") if current else None,
        "health_score": project_health(state),
        "failure_ids": [unit.get("id") for unit in units if unit.get("status") in TERMINAL_OPEN],
        "updated_at": state.get("updated_at"),
    }
    temporary = PROJECT_TELEMETRY_PATH.with_suffix(f".{os.getpid()}.tmp")
    PROJECT_TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(telemetry, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, PROJECT_TELEMETRY_PATH)


def load_state(project_id: str) -> dict[str, object] | None:
    if not ID_PATTERN.fullmatch(project_id):
        return None
    try:
        value = json.loads(state_path(project_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_plan(project_id: str) -> dict[str, object] | None:
    if not ID_PATTERN.fullmatch(project_id):
        return None
    try:
        value = json.loads(plan_path(project_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def create_state(manifest: dict[str, object]) -> dict[str, object]:
    timestamp = now()
    return {
        "version": 1,
        "project_id": manifest["project_id"],
        "project_title": manifest["project_title"],
        "goal": manifest["project_goal"],
        "manifest_hash": manifest_hash(manifest),
        "status": "LOADED",
        "pause_requested": False,
        "current_work_unit_id": None,
        "created_at": timestamp,
        "started_at": None,
        "completed_at": None,
        "updated_at": timestamp,
        "work_units": [{"id": unit["id"], "status": "READY" if not unit["dependencies"] else "PENDING", "attempts": 0, "dispatch_id": None, "started_at": None, "completed_at": None, "factory_execution_id": None, "qa_status": None, "qa_lead_status": None, "inspector_score": None, "acceptance_evidence": None, "last_result": None, "outcome_summary": None, "blocked_reason": None, "updated_at": timestamp} for unit in manifest["work_units"]],
    }


def prepare_project(manifest: dict[str, object]) -> dict[str, object]:
    """Persist a validated queue without authorizing Product Factory execution."""
    with LOCK:
        existing = load_state(str(manifest["project_id"]))
        if existing:
            if existing.get("manifest_hash") != manifest_hash(manifest):
                raise ManifestError("project_id already exists with a different manifest")
            write_immutable_plan(manifest)
            return existing
        state = create_state(manifest)
        write_immutable_plan(manifest)
        atomic_write(state)
        return state


def another_project_running(project_id: str) -> bool:
    for path in STATE_DIR.glob("*.json"):
        candidate = load_state(path.stem)
        if candidate and candidate.get("project_id") != project_id and candidate.get("status") == "RUNNING":
            return True
    return False


def result_evidence(result: dict[str, object]) -> dict[str, object]:
    report = result.get("sprint_report") if isinstance(result.get("sprint_report"), dict) else result
    inspector = report.get("factory_inspector") if isinstance(report.get("factory_inspector"), dict) else {}
    execution_id = result.get("execution_id") or report.get("factory_execution_id")
    return {
        "factory_execution_id": str(execution_id) if isinstance(execution_id, (str, int)) else None,
        "qa_status": str(report.get("qa_result")) if report.get("qa_result") is not None else None,
        "qa_lead_status": str(report.get("qa_lead_result")) if report.get("qa_lead_result") is not None else None,
        "inspector_score": inspector.get("sprint_score") if isinstance(inspector.get("sprint_score"), (int, float)) else None,
        "acceptance_evidence": {
            "status": report.get("status"),
            "qa_result": report.get("qa_result"),
            "qa_lead_result": report.get("qa_lead_result"),
            "open_item_count": len(report.get("skipped_or_deferred_tasks", [])) if isinstance(report.get("skipped_or_deferred_tasks"), list) else None,
        },
    }


def project_health(state: dict[str, object]) -> Optional[float]:
    scores = [float(unit["inspector_score"]) for unit in state.get("work_units", []) if isinstance(unit.get("inspector_score"), (int, float))]
    return round(sum(scores) / len(scores), 2) if scores else None


def dependency_outcomes(state: dict[str, object], unit: dict[str, object]) -> list[dict[str, str]]:
    by_id = {candidate["id"]: candidate for candidate in state["work_units"]}
    outcomes = []
    budget = MAX_DEPENDENCY_CONTEXT
    for dependency_id in unit["dependencies"]:
        dependency = by_id[dependency_id]
        summary = str(dependency.get("outcome_summary") or dependency.get("last_result") or "No bounded outcome recorded")[:600]
        summary = summary[:budget]
        budget -= len(summary)
        outcomes.append({"work_unit_id": dependency_id, "status": dependency["status"], "outcome": summary})
        if budget <= 0:
            break
    return outcomes


def package_work_unit(state: dict[str, object], unit: dict[str, object]) -> dict[str, object]:
    plan = load_plan(str(state["project_id"]))
    if not plan:
        raise ManifestError("immutable project plan is missing")
    definition = next((item for item in plan["work_units"] if item["id"] == unit["id"]), None)
    if not definition:
        raise ManifestError("work unit definition is missing from immutable plan")
    context = plan["global_context"]
    return {
        "request_id": f"{state['project_id']}:{unit['id']}:{unit['attempts']}",
        "sprint_title": f"{state['project_title']} — {unit['id']} {definition['title']}",
        "project_id": state["project_id"],
        "work_unit_id": unit["id"],
        "product_identity": state["project_title"],
        "goal": definition["goal"],
        "scope": definition["allowed_files"] or [definition["title"]],
        "constraints": [*context["constraints"], f"Work only on {unit['id']}: {definition['title']}"],
        "acceptance_criteria": definition["acceptance_criteria"],
        "allowed_actions": definition["allowed_actions"],
        "forbidden_actions": definition["forbidden_actions"],
        "requested_target": "SMOKE_FIXTURE",
        "writer_target": "SMOKE_FIXTURE",
        "context": {
            "product_summary": context["summary"][:4_000],
            "architecture": context["architecture"][:4_000],
            "work_unit_description": definition["description"][:8_000],
            "dependencies": dependency_outcomes(state, definition),
            "allowed_files": definition["allowed_files"],
            "forbidden_files": definition["forbidden_files"],
            "test_requirements": definition["test_requirements"],
            "expected_artifacts": definition["expected_artifacts"],
        },
    }


def submit_to_factory(brief: dict[str, object]) -> dict[str, object]:
    body = json.dumps(brief, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = request.Request(FACTORY_WEBHOOK, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=25 * 60) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return {"transport_status": "FAILED", "reason": "factory_response_too_large"}
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {"transport_status": "FAILED", "reason": "factory_response_not_object"}
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"transport_status": "FAILED", "reason": type(exc).__name__}


def classify_result(result: dict[str, object]) -> tuple[str, str]:
    report = result.get("sprint_report") if isinstance(result.get("sprint_report"), dict) else result
    if result.get("transport_status") == "FAILED":
        return "NEEDS_REVIEW", f"Fabrika iletişimi başarısız: {result.get('reason', 'BİLİNMİYOR')}"
    status = str(report.get("status") or "").upper()
    qa = str(report.get("qa_result") or "").upper()
    lead = str(report.get("qa_lead_result") or "").upper()
    open_items = report.get("skipped_or_deferred_tasks")
    if status == "COMPLETED" and qa == "PASS" and lead == "QA_APPROVED" and isinstance(open_items, list) and not open_items:
        return "DONE", "Ürün Fabrikası deterministik kalite kontrolü ve QA Lideri onayıyla tamamladı."
    if status in {"FAILED", "RECOVERY_EXHAUSTED"}:
        return "FAILED", f"Fabrika sınırlı kurtarma sonrasında kalıcı hata bildirdi: durum={status}, qa={qa or 'BİLİNMİYOR'}, qa_lideri={lead or 'BİLİNMİYOR'}"
    if status in {"COMPLETED_WITH_OPEN_ITEMS", "BLOCKED"} or qa in {"NOT_VERIFIED", "FAIL"} or lead in {"QA_INCOMPLETE", "NOT_VERIFIED"}:
        return "NEEDS_REVIEW", f"Fabrika tamamlanma sözleşmesi sağlanmadı: durum={status or 'BİLİNMİYOR'}, qa={qa or 'BİLİNMİYOR'}, qa_lideri={lead or 'BİLİNMİYOR'}"
    return "NEEDS_REVIEW", "Fabrika sonucu eksiksiz ve doğrulanmış bir tamamlanma sözleşmesi içermiyor."


def update_blocked_units(state: dict[str, object]) -> None:
    plan = load_plan(str(state["project_id"]))
    if not plan:
        raise ManifestError("immutable project plan is missing")
    dependencies = {unit["id"]: unit["dependencies"] for unit in plan["work_units"]}
    by_id = {unit["id"]: unit for unit in state["work_units"]}
    for unit in state["work_units"]:
        if unit["status"] not in {"PENDING", "READY"}:
            continue
        blockers = [dependency for dependency in dependencies[unit["id"]] if by_id[dependency]["status"] in TERMINAL_OPEN]
        if blockers:
            unit["status"] = "BLOCKED"
            unit["blocked_reason"] = "Çözümlenmemiş bağımlılıklar nedeniyle engellendi: " + ", ".join(blockers)
            unit["updated_at"] = now()


def eligible_unit(state: dict[str, object]) -> dict[str, object] | None:
    plan = load_plan(str(state["project_id"]))
    if not plan:
        raise ManifestError("immutable project plan is missing")
    by_id = {unit["id"]: unit for unit in state["work_units"]}
    for definition in plan["work_units"]:
        unit = by_id[definition["id"]]
        dependencies_done = all(by_id[dependency]["status"] == "DONE" for dependency in definition["dependencies"])
        if unit["status"] in {"PENDING", "READY"} and dependencies_done:
            return unit
    return None


def finalize_project(state: dict[str, object]) -> None:
    statuses = [unit["status"] for unit in state["work_units"]]
    if all(status == "DONE" for status in statuses):
        state["status"] = "COMPLETED"
    elif any(status == "DONE" for status in statuses):
        state["status"] = "COMPLETED_WITH_OPEN_ITEMS"
    else:
        state["status"] = "FAILED_NO_PROGRESS"
    state["current_work_unit_id"] = None
    state["completed_at"] = now()
    state["updated_at"] = now()
    atomic_write(state)


def run_project(project_id: str, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory) -> None:
    while True:
        with LOCK:
            state = load_state(project_id)
            if not state or state["status"] != "RUNNING":
                return
            if any(unit["status"] == "RUNNING" for unit in state["work_units"]):
                return
            update_blocked_units(state)
            if state.get("pause_requested"):
                state["status"] = "PAUSED_HUMAN_AUTH"
                state["updated_at"] = now()
                atomic_write(state)
                return
            unit = eligible_unit(state)
            if not unit:
                finalize_project(state)
                return
            unit["status"] = "READY"
            unit["attempts"] = int(unit.get("attempts", 0)) + 1
            unit["dispatch_id"] = hashlib.sha256(f"{project_id}:{unit['id']}:{unit['attempts']}".encode()).hexdigest()[:24]
            unit["status"] = "RUNNING"
            unit["started_at"] = unit.get("started_at") or now()
            unit["updated_at"] = now()
            state["status"] = "RUNNING"
            state["current_work_unit_id"] = unit["id"]
            state["updated_at"] = now()
            atomic_write(state)
            brief = package_work_unit(state, unit)
        result = submitter(brief)
        unit_status, summary = classify_result(result)
        with LOCK:
            state = load_state(project_id)
            if not state:
                return
            current = next(candidate for candidate in state["work_units"] if candidate["id"] == unit["id"])
            if current["status"] != "RUNNING" or current["dispatch_id"] != unit["dispatch_id"]:
                return
            current["status"] = unit_status
            current["last_result"] = {"status": unit_status, "summary": summary}
            current.update(result_evidence(result))
            current["outcome_summary"] = summary[:1_000]
            current["completed_at"] = now()
            current["updated_at"] = now()
            state["current_work_unit_id"] = None
            state["updated_at"] = now()
            atomic_write(state)


def start_project(project_id: str, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory, *, background: bool = True) -> dict[str, object]:
    with LOCK:
        state = load_state(project_id)
        if not state:
            raise ManifestError("project must be loaded before production can start")
        if state["status"] != "LOADED":
            raise ManifestError("only a LOADED project can start production")
        if another_project_running(project_id):
            raise ManifestError("another project is already running; conservative mode permits one factory Work Unit globally")
        state["status"] = "RUNNING"
        state["pause_requested"] = False
        state["started_at"] = state.get("started_at") or now()
        state["updated_at"] = now()
        atomic_write(state)
    if background:
        runner = threading.Thread(target=run_project, args=(project_id, submitter), daemon=True)
        RUNNERS[project_id] = runner
        runner.start()
    else:
        run_project(project_id, submitter)
    return load_state(project_id) or state


def pause_project(project_id: str) -> dict[str, object]:
    with LOCK:
        state = load_state(project_id)
        if not state:
            raise ManifestError("project not found")
        if state["status"] != "RUNNING":
            raise ManifestError("project is not running")
        state["pause_requested"] = True
        if not any(unit["status"] == "RUNNING" for unit in state["work_units"]):
            state["status"] = "PAUSED_HUMAN_AUTH"
        state["updated_at"] = now()
        atomic_write(state)
        return state


def resume_project(project_id: str, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory, *, background: bool = True) -> dict[str, object]:
    with LOCK:
        state = load_state(project_id)
        if not state or state["status"] != "PAUSED_HUMAN_AUTH":
            raise ManifestError("project is not paused")
        if another_project_running(project_id):
            raise ManifestError("another project is already running; conservative mode permits one factory Work Unit globally")
        state["pause_requested"] = False
        state["status"] = "RUNNING"
        state["updated_at"] = now()
        atomic_write(state)
    if background:
        runner = threading.Thread(target=run_project, args=(project_id, submitter), daemon=True)
        RUNNERS[project_id] = runner
        runner.start()
    else:
        run_project(project_id, submitter)
    return load_state(project_id) or state


def recover_interrupted_projects() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK:
        for path in STATE_DIR.glob("*.json"):
            state = load_state(path.stem)
            if not state:
                continue
            changed = False
            for unit in state.get("work_units", []):
                if unit.get("status") == "RUNNING":
                    unit["status"] = "NEEDS_REVIEW"
                    unit["last_result"] = {"status": "NEEDS_REVIEW", "summary": "Bu iş birimi çalışırken köprü yeniden başladı; sonuç belirsiz olduğu için yeniden gönderilmedi."}
                    unit["outcome_summary"] = unit["last_result"]["summary"]
                    unit["updated_at"] = now()
                    changed = True
            if changed:
                state["status"] = "PAUSED_HUMAN_AUTH"
                state["pause_requested"] = True
                state["current_work_unit_id"] = None
                state["updated_at"] = now()
                atomic_write(state)


def public_state(state: dict[str, object]) -> dict[str, object]:
    plan = load_plan(str(state.get("project_id", ""))) or {"work_units": []}
    definitions = {unit["id"]: unit for unit in plan["work_units"]}
    units = []
    for unit in state.get("work_units", []):
        definition = definitions.get(unit["id"], {})
        units.append({**{key: definition.get(key) for key in ("work_unit_id", "title", "goal", "dependencies", "priority")}, **{key: unit.get(key) for key in ("id", "status", "attempts", "started_at", "completed_at", "factory_execution_id", "qa_status", "qa_lead_status", "inspector_score", "acceptance_evidence", "last_result", "blocked_reason", "updated_at")}})
    counts = {status: sum(1 for unit in units if unit["status"] == status) for status in UNIT_STATUSES}
    return {"project_id": state.get("project_id"), "project_title": state.get("project_title"), "status": state.get("status"), "created_at": state.get("created_at"), "started_at": state.get("started_at"), "completed_at": state.get("completed_at"), "current_work_unit_id": state.get("current_work_unit_id"), "pause_requested": state.get("pause_requested"), "counts": counts, "total": len(units), "project_health": project_health(state), "updated_at": state.get("updated_at"), "work_units": units}
