"""Deterministic Local Project Director: validate, persist, sequence, and package Work Units."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import threading
from typing import Callable, Optional
from urllib import request
from urllib.error import HTTPError, URLError
import time


FACTORY_WEBHOOK = "http://127.0.0.1:5678/webhook/ai-product-factory-v4-sprint"
FACTORY_WORKFLOW_ID = "gzMmJQSUwVdXab8W"
N8N_BASE = os.environ.get("N8N_BASE_URL", "http://127.0.0.1:5678")
STATE_DIR = Path(__file__).resolve().parent / "state"
PROJECT_TELEMETRY_PATH = Path(__file__).resolve().parents[1] / "monitor" / "telemetry" / "latest.json"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_WORK_UNITS = 500
MAX_TEXT = 8_000
MAX_CONTEXT = 20_000
MAX_LIST = 100
MAX_DEPENDENCY_CONTEXT = 4_000
MAX_FACTORY_ATTEMPTS = 3
MAX_FACTORY_ATTEMPTS_DISPLAY = 3
PROJECT_ALLOWED = {
    "schema_version", "project_id", "project_name", "project_title", "project_goal",
    "global_context", "global_constraints", "global_forbidden_actions",
    "definition_of_done", "completion_criteria", "work_units",
}
UNIT_ALLOWED = {
    "work_unit_id", "title", "goal", "objective", "context", "dependencies",
    "allowed_files", "allowed_scope", "forbidden_files", "forbidden_scope",
    "forbidden_actions", "acceptance_criteria", "test_requirements",
    "related_components", "notes", "priority",
}
PRIVILEGED_HINTS = {
    "workflow_id", "workflowId", "shell", "shell_command", "filesystem_path", "credential",
    "credentials", "host_writer", "hostWriter", "prompt", "model", "n8n", "mi_database",
    "market_intelligence", "execute_node", "arbitrary_http",
}
ALLOWED_ACTIONS = {"READ_PROJECT", "CREATE_FILES", "MODIFY_FILES", "RUN_TESTS"}
ALLOWED_WRITER_TARGETS = {"SMOKE_FIXTURE", "PRODUCTION"}
SECRET_RE = re.compile(r"(?i)((?:authorization\s*:\s*)?bearer\s+\S+|authorization\s*:\s*.+|(api[_-]?key|token|password|cookie)\s*[:=]\s*\S+)")
TERMINAL_OPEN = {"FAILED", "NEEDS_REVIEW", "DEFERRED", "BLOCKED"}
UNIT_STATUSES = {"PENDING", "READY", "RUNNING", "DONE", "FAILED", "NEEDS_REVIEW", "DEFERRED", "BLOCKED"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
LOCK = threading.RLock()
RUNNERS: dict[str, threading.Thread] = {}


class ManifestError(ValueError):
    pass


def sanitize_text(value: object, maximum: int = 500) -> str:
    cleaned = SECRET_RE.sub("[REDACTED]", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:maximum]


HANDOFF_NEXT = {
    "Specialist": "Normalize Specialist Result",
    "Specialist Fallback": "Normalize Specialist Result",
    "Specialist Groq Fallback": "Normalize Specialist Result",
    "Normalize Specialist Result": "Planner",
    "Analyst": "Normalize Analyst Result",
    "Planner": "Normalize Planner Result",
    "Consistency Reviewer": "Normalize Consistency Result",
}

TERMINAL_FACTORY_NODES = frozenset({"Director Webhook Response", "Factory Telemetry Publisher"})


def diagnosis_tr(summary: str, stage: str) -> str:
    text_value = summary.lower()
    if "jsondecodeerror" in text_value or "non_json" in text_value or "empty_factory_response" in text_value:
        return "Product Factory geçerli bir JSON sonuç döndürmedi. Gönderim başlamış olabilir; webhook hata yolunda yanıt düğümüne ulaşmadı."
    if (
        "provider_timeout" in text_value
        or "primary_timeout" in text_value
        or "connection was aborted" in text_value
        or "server is offline" in text_value
        or (stage in {"Analyst", "Provider Failure Classifier"} and "timeout" in text_value)
    ):
        return "Birincil Analyst sağlayıcısı zaman aşımına uğradı."
    if "timeout" in text_value:
        return "Factory HTTP yanıtı zaman aşımına uğradı. Eşleşen execution hâlâ çalışıyorsa bu tek başına terminal sonuç değildir."
    if "no execution data" in text_value or "expressionerror" in text_value:
        return "Factory bir düğümde boş execution verisi nedeniyle durdu."
    if "known_context" in text_value or "normalize analyst" in text_value or stage == "Normalize Analyst Result":
        return "Kök sorun: known_context veri sözleşmesi beklenen biçimle uyuşmadı."
    if (
        "acceptance criteria disappeared" in text_value
        or "inherited acceptance" in text_value
        or (stage == "Normalize Specialist Result" and "acceptance" in text_value)
    ):
        return "Kök sorun: Specialist çıktısında devralınan kabul kriterleri korunmadı."
    if (
        "constraints disappeared" in text_value
        or "inherited constraints" in text_value
        or ("specialist" in text_value and "kısıt" in text_value)
        or (stage == "Normalize Specialist Result" and "constraint" in text_value)
    ):
        return "Kök sorun: Specialist çıktısında devralınan kısıtlar korunmadı."
    if "incomplete_factory_success" in text_value and "last=unknown" in text_value:
        return "Factory HTTP beklemesi bitti; Codex Host Writer sonucu gelmedi. Uygulanmış kanıt webhook sözleşmesine yazılmadı."
    if (
        "incomplete_factory_success" in text_value
        or "missing handoff" in text_value
        or ("normalize specialist" in text_value and "constraint" not in text_value)
    ):
        return "Kök sorun: başarılı Specialist çıktısından sonra beklenen geçiş çalışmadı."
    if stage == "Factory Submission":
        return "Product Factory gönderimi tamamlanmış bir sprint raporu üretmedi."
    if (
        "host writer" in text_value
        or "codex executor" in text_value
        or "codex unavailable" in text_value
        or stage in {"Codex Executor", "Codex Task Safety Gate", "Host Writer Submit", "Host Writer Timeout"}
    ):
        return "Codex uygulanmış dosya kanıtı üretemedi. MiniMax ve Laguna yalnızca öneri üretebilir; QA bunları uygulanmış iş olarak kabul etmez."
    if "provider_role_unknown" in text_value or "tamamlanan factory rolü" in text_value or (stage == "Provider Success Router" and "unknown" in text_value):
        return "Kök sorun: Provider sonucu başarıyla döndü ancak tamamlanan factory rolü belirlenemedi."
    if (
        "developer pool exhausted" in text_value
        or "provider_not_found" in text_value
        or "model_unavailable" in text_value
        or (stage in {"MiniMax Developer", "Developer Dispatcher", "Recovery Controller"} and "minimax" in text_value)
    ):
        return "MiniMax teknik olarak kullanılamaz hale geldikten sonra Developer fallback zinciri Laguna'ya devam etmedi."
    return sanitize_text(summary, 800)


def writer_target_for(project_id: str) -> str:
    identifier = str(project_id)
    if identifier.startswith("PROJECT-TEST") or "SYNTHETIC" in identifier.upper():
        return "SMOKE_FIXTURE"
    return "PRODUCTION"


def affected_units(state: dict[str, object], blocker_id: str) -> list[str]:
    plan = load_plan(str(state.get("project_id", ""))) or {"work_units": []}
    dependencies = {unit["id"]: unit["dependencies"] for unit in plan["work_units"]}
    return [unit["id"] for unit in state.get("work_units", []) if blocker_id in dependencies.get(unit["id"], [])]


def developer_chain_tr(summary: str, result: dict[str, object] | None = None) -> dict[str, str]:
    payload = result if isinstance(result, dict) else {}
    report = payload.get("sprint_report") if isinstance(payload.get("sprint_report"), dict) else {}
    exclusions = {str(item).upper() for item in (payload.get("developer_exclusions") or [])}
    selected = str(payload.get("developer_route") or payload.get("selected_developer") or "").upper()
    failures = payload.get("technical_failures") if isinstance(payload.get("technical_failures"), list) else []
    if not failures and isinstance(report, dict) and isinstance(report.get("technical_failures"), list):
        failures = report["technical_failures"]
    failed = {str(item.get("developer_route") or "").upper() for item in failures if isinstance(item, dict)}
    outcomes = report.get("task_outcomes") if isinstance(report.get("task_outcomes"), list) else []
    used = {str(item.get("developer") or "").upper() for item in outcomes if isinstance(item, dict) and item.get("developer")}
    if int(report.get("codex_developed_count") or 0) > 0:
        used.add("CODEX")
    if int(report.get("minimax_developed_count") or 0) > 0:
        used.add("MINIMAX")
    if int(report.get("laguna_developed_count") or 0) > 0:
        used.add("LAGUNA")
    blob = summary.lower()

    def one(name: str) -> str:
        if name == "CODEX" and (payload.get("codex_available") is False or name in exclusions) and name not in used:
            return "Kullanılamadı"
        if name == "MINIMAX" and (
            name in failed
            or "provider_not_found" in blob
            or "model_unavailable" in blob
            or "unavailable for free" in blob
        ) and name not in used:
            return "Sağlayıcı bulunamadı"
        if name in used or selected == name:
            return "Seçildi"
        if name in exclusions or name in failed:
            return "Kullanılamadı"
        if name == "LAGUNA" and selected != "LAGUNA" and name not in failed:
            return "Denenmedi"
        return "—"

    return {"codex_tr": one("CODEX"), "minimax_tr": one("MINIMAX"), "laguna_tr": one("LAGUNA")}


def stuck_stage_tr(stage: str, summary: str, last_success: str = "") -> str:
    blob = f"{stage} {summary} {last_success}".lower()
    if "analyst" in blob and (
        "timeout" in blob
        or "aborted" in blob
        or "offline" in blob
        or "provider_timeout" in blob
        or "primary_timeout" in blob
        or "chain_exhausted" in blob
        or "provider_chain_exhausted" in blob
    ):
        return "Analyst sağlayıcı çağrısı"
    labels = {
        "Analyst": "Analyst sağlayıcı çağrısı",
        "Analyst NVIDIA Fallback": "Analyst yedek sağlayıcı çağrısı",
        "Analyst Groq Fallback": "Analyst yedek sağlayıcı çağrısı",
        "Provider Failure Classifier": "Analyst sağlayıcı çağrısı",
        "Provider Failure Terminal": "Sağlayıcı zinciri sonu",
        "Developer Dispatcher": "Developer seçimi",
        "MiniMax Developer": "Developer seçimi",
        "Recovery Controller": "Developer seçimi",
        "Factory Submission": "Factory gönderimi",
        "Factory webhook response": "Factory webhook yanıtı",
    }
    return labels.get(stage, stage)


def fallback_status_tr(summary: str, stage: str = "", result: dict[str, object] | None = None) -> str:
    payload = result if isinstance(result, dict) else {}
    state = str(payload.get("_provider_timeout_state") or "")
    if state == "FALLBACK_SUCCESS":
        return "BAŞARILI"
    if state == "PRIMARY_SUCCESS":
        return "DENENMEDİ"
    if state == "CHAIN_EXHAUSTED":
        return "BAŞARISIZ"
    if state in {"PRIMARY_TIMEOUT", "FALLBACK_TIMEOUT"} or payload.get("_provider_fallback_attempted"):
        return "DENENDİ" if payload.get("_provider_fallback_attempted") or state == "FALLBACK_TIMEOUT" else "DENENMEDİ"
    blob = f"{summary} {stage}".lower()
    if "fallback_success" in blob:
        return "BAŞARILI"
    if "primary_success" in blob:
        return "DENENMEDİ"
    if "chain_exhausted" in blob or "provider_chain_exhausted" in blob:
        return "BAŞARISIZ"
    if any(token in blob for token in ("fallback_timeout", "fallback_attempted", "analyst nvidia", "analyst groq", "failover")):
        return "DENENDİ"
    if any(token in blob for token in ("provider_timeout", "primary_timeout", "connection was aborted", "server is offline")):
        return "DENENMEDİ"
    return "—"


def build_diagnosis(state: dict[str, object], unit: dict[str, object], *, status: str, summary: str, stage: str, last_success: str, execution_id: object = None, expected_next: object = None, result: dict[str, object] | None = None) -> dict[str, object]:
    blocked = affected_units(state, str(unit["id"]))
    expected = str(expected_next or HANDOFF_NEXT.get(last_success) or "")
    chain = developer_chain_tr(summary, result)
    return {
        "summary": sanitize_text(summary, 800),
        "summary_tr": diagnosis_tr(summary, stage),
        "stage": stage,
        "stage_tr": stuck_stage_tr(stage, summary, last_success),
        "fallback_tr": fallback_status_tr(summary, stage, result),
        "codex_tr": chain["codex_tr"],
        "minimax_tr": chain["minimax_tr"],
        "laguna_tr": chain["laguna_tr"],
        "last_success_stage": last_success,
        "expected_next_stage": expected or None,
        "root_failure": sanitize_text(summary, 400),
        "why_not_done": sanitize_text(summary, 400),
        "execution_id": str(execution_id) if execution_id else None,
        "attempt": int(unit.get("attempts") or 0),
        "qa_status": unit.get("qa_status"),
        "qa_lead_status": unit.get("qa_lead_status"),
        "inspector_score": unit.get("inspector_score"),
        "affected_work_unit_ids": blocked,
        "status": status,
    }


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


def reject_privileged(payload: dict[str, object], location: str) -> None:
    hits = sorted(set(payload) & PRIVILEGED_HINTS)
    if hits:
        raise ManifestError(f"{location} contains privileged control fields: {hits}")


def safe_scope_item(value: str, name: str) -> str:
    normalized = value.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or ".." in candidate.parts or normalized.startswith("~"):
        raise ManifestError(f"{name} contains an unsafe path")
    if any(part in {".git", ".env", ".codex", "market-intelligence-platform"} for part in candidate.parts):
        raise ManifestError(f"{name} targets a protected path")
    return normalized


def validate_manifest(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ManifestError("manifest must be an object")
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest exceeds size limit")
    reject_privileged(payload, "manifest")
    unexpected = set(payload) - PROJECT_ALLOWED
    if unexpected:
        raise ManifestError(f"manifest fields invalid; unexpected={sorted(unexpected)}")
    project_id = text(payload.get("project_id"), "project_id", 128)
    if not ID_PATTERN.fullmatch(project_id):
        raise ManifestError("project_id format is invalid")
    if payload.get("schema_version") != "1.0":
        raise ManifestError("schema_version must be 1.0")
    project_title = text(payload.get("project_title") or payload.get("project_name"), "project_title", 300)
    if payload.get("project_goal"):
        project_goal = text(payload["project_goal"], "project_goal", MAX_CONTEXT)
    elif isinstance(payload.get("global_context"), str):
        project_goal = text(payload["global_context"], "global_context", MAX_CONTEXT)
    else:
        raise ManifestError("project_goal is required")
    raw_context = payload.get("global_context", project_goal)
    if isinstance(raw_context, dict):
        global_context = text(str(raw_context.get("summary") or project_goal), "global_context", MAX_CONTEXT)
    else:
        global_context = text(raw_context, "global_context", MAX_CONTEXT)
    forbidden_actions = strings(payload.get("global_forbidden_actions"), "global_forbidden_actions", required=True)
    definition_of_done = strings(payload.get("completion_criteria", payload.get("definition_of_done")), "completion_criteria", required=True)
    units = payload.get("work_units")
    if not isinstance(units, list) or not units or len(units) > MAX_WORK_UNITS:
        raise ManifestError(f"work_units must contain 1-{MAX_WORK_UNITS} items")
    normalized_units = []
    identifiers: set[str] = set()
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ManifestError(f"work_units[{index}] must be an object")
        reject_privileged(unit, f"work_units[{index}]")
        unexpected_unit = set(unit) - UNIT_ALLOWED
        if unexpected_unit:
            raise ManifestError(f"work_units[{index}] fields are invalid; unexpected={sorted(unexpected_unit)}")
        unit_id = text(unit.get("work_unit_id"), f"work_units[{index}].work_unit_id", 128)
        if not ID_PATTERN.fullmatch(unit_id) or unit_id in identifiers:
            raise ManifestError(f"duplicate or invalid work unit id: {unit_id}")
        identifiers.add(unit_id)
        allowed = unit.get("allowed_files", unit.get("allowed_scope", []))
        forbidden_files = unit.get("forbidden_files", unit.get("forbidden_scope", []))
        unit_forbidden = unit.get("forbidden_actions", forbidden_actions)
        if "priority" in unit and (not isinstance(unit["priority"], int) or not 1 <= unit["priority"] <= 1000):
            raise ManifestError(f"{unit_id}.priority must be an integer from 1 to 1000")
        if unit.get("notes") is not None:
            text(unit["notes"], f"{unit_id}.notes", MAX_TEXT)
        normalized_units.append({
            "id": unit_id,
            "work_unit_id": unit_id,
            "sequence": index,
            "title": text(unit.get("title"), f"{unit_id}.title", 300),
            "goal": text(unit.get("goal") or unit.get("objective"), f"{unit_id}.goal"),
            "description": text(unit.get("context"), f"{unit_id}.context", MAX_CONTEXT),
            "dependencies": strings(unit.get("dependencies", []), f"{unit_id}.dependencies"),
            "allowed_files": [safe_scope_item(item, f"{unit_id}.allowed_files") for item in strings(allowed, f"{unit_id}.allowed_files")],
            "forbidden_files": [safe_scope_item(item, f"{unit_id}.forbidden_files") for item in strings(forbidden_files, f"{unit_id}.forbidden_files")],
            "acceptance_criteria": strings(unit.get("acceptance_criteria"), f"{unit_id}.acceptance_criteria", required=True),
            "test_requirements": strings(unit.get("test_requirements"), f"{unit_id}.test_requirements", required=True),
            "allowed_actions": sorted(ALLOWED_ACTIONS),
            "forbidden_actions": strings(unit_forbidden, f"{unit_id}.forbidden_actions", required=True),
            "priority": unit.get("priority"),
            "related_components": strings(unit.get("related_components", []), f"{unit_id}.related_components"),
        })
    known = {unit["id"] for unit in normalized_units}
    for unit in normalized_units:
        unknown = set(unit["dependencies"]) - known
        if unknown:
            raise ManifestError(f"{unit['id']} has unknown dependencies: {sorted(unknown)}")
        if unit["id"] in unit["dependencies"]:
            raise ManifestError(f"{unit['id']} has a self-dependency")
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
        "project_title": project_title,
        "project_goal": project_goal,
        "global_context": global_context,
        "global_constraints": strings(payload.get("global_constraints", []), "global_constraints"),
        "global_forbidden_actions": forbidden_actions,
        "definition_of_done": definition_of_done,
        "work_units": normalized_units,
    }


def validation_view(manifest: dict[str, object]) -> dict[str, object]:
    units = manifest["work_units"]
    ready = [unit["id"] for unit in units if not unit["dependencies"]]
    pending = [unit["id"] for unit in units if unit["dependencies"]]
    return {
        "valid": True,
        "project_id": manifest["project_id"],
        "project_name": manifest["project_title"],
        "work_unit_count": len(units),
        "dependency_validation": "PASS",
        "dependency_summary": [{"work_unit_id": unit["id"], "dependencies": unit["dependencies"]} for unit in units],
        "estimated_order": [unit["id"] for unit in units],
        "ready_units": ready,
        "pending_until_dependencies": pending,
        "invalid_units": [],
    }


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
    counts = {status: sum(1 for unit in units if unit.get("status") == status) for status in UNIT_STATUSES}
    telemetry["project"] = {
        "project_id": state.get("project_id"),
        "project_title": state.get("project_title"),
        "status": state.get("status"),
        "done": counts["DONE"],
        "total": len(units),
        "counts": counts,
        "current_work_unit_id": current_id,
        "current_work_unit_title": definitions.get(current_id, {}).get("title") if current else None,
        "current_attempt": current.get("attempts") if current else None,
        "health_score": project_health(state),
        "open_ids": {
            "FAILED": [unit.get("id") for unit in units if unit.get("status") == "FAILED"],
            "NEEDS_REVIEW": [unit.get("id") for unit in units if unit.get("status") == "NEEDS_REVIEW"],
            "DEFERRED": [unit.get("id") for unit in units if unit.get("status") == "DEFERRED"],
            "BLOCKED": [unit.get("id") for unit in units if unit.get("status") == "BLOCKED"],
        },
        "started_at": state.get("started_at"),
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
        "status": "VALIDATED",
        "pause_requested": False,
        "current_work_unit_id": None,
        "created_at": timestamp,
        "started_at": None,
        "completed_at": None,
        "updated_at": timestamp,
        "work_units": [{
            "id": unit["id"],
            "status": "READY" if not unit["dependencies"] else "PENDING",
            "attempts": 0,
            "dispatch_id": None,
            "started_at": None,
            "completed_at": None,
            "factory_execution_id": None,
            "qa_status": None,
            "qa_lead_status": None,
            "inspector_score": None,
            "acceptance_evidence": None,
            "last_result": None,
            "outcome_summary": None,
            "last_error": None,
            "failure_signature": None,
            "diagnosis": None,
            "updated_at": timestamp,
        } for unit in manifest["work_units"]],
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
    if not STATE_DIR.exists():
        return False
    for path in STATE_DIR.glob("*.json"):
        candidate = load_state(path.stem)
        if candidate and candidate.get("project_id") != project_id and candidate.get("status") == "RUNNING":
            return True
    return False


def result_evidence(result: dict[str, object]) -> dict[str, object]:
    report = result.get("sprint_report") if isinstance(result.get("sprint_report"), dict) else result
    inspector = report.get("factory_inspector") if isinstance(report.get("factory_inspector"), dict) else {}
    execution_id = result.get("factory_execution_id") or result.get("execution_id") or report.get("factory_execution_id")
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
    target = writer_target_for(str(state["project_id"]))
    if target not in ALLOWED_WRITER_TARGETS:
        raise ManifestError("writer target is not allowlisted")
    objective = definition["goal"]
    return {
        "request_id": f"{state['project_id']}:{unit['id']}:{unit['attempts']}",
        "sprint_title": f"{state['project_title']} — {unit['id']} {definition['title']}",
        "sprint_id": f"{state['project_id']}-{unit['id']}-{unit['attempts']}",
        "project_id": state["project_id"],
        "work_unit_id": unit["id"],
        "product_identity": state["project_title"],
        "goal": objective,
        "objective": objective,
        "scope": definition["allowed_files"] or [definition["title"]],
        "constraints": [*plan["global_constraints"], f"Work only on {unit['id']}: {definition['title']}"],
        "acceptance_criteria": definition["acceptance_criteria"],
        "allowed_actions": definition["allowed_actions"],
        "forbidden_actions": definition["forbidden_actions"],
        "requested_target": target,
        "writer_target": target,
        "codex_available": True,
        "historical_execution_ids": list(unit.get("historical_execution_ids") or []),
        "context": {
            "product_summary": (plan.get("global_context") or plan["project_goal"])[:4_000],
            "definition_of_done": plan["definition_of_done"],
            "work_unit_description": definition["description"][:8_000],
            "dependencies": dependency_outcomes(state, definition),
            "allowed_files": definition["allowed_files"],
            "forbidden_files": definition["forbidden_files"],
            "test_requirements": definition["test_requirements"],
        },
    }


def publish_dispatch_telemetry(state: dict[str, object], unit: dict[str, object], brief: dict[str, object]) -> None:
    try:
        telemetry = json.loads(PROJECT_TELEMETRY_PATH.read_text(encoding="utf-8"))
        if not isinstance(telemetry, dict) or not isinstance(telemetry.get("nodes"), list):
            telemetry = {"schema_version": 1, "nodes": []}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        telemetry = {"schema_version": 1, "nodes": []}
    work_unit_id = str(unit["id"])
    telemetry["factory_status"] = "RUNNING"
    telemetry["sprint_id"] = str(brief.get("sprint_id") or brief.get("request_id"))
    telemetry["current_stage"] = "Director Sprint Input"
    telemetry["updated_at"] = now()
    telemetry["project_id"] = state.get("project_id")
    telemetry["work_unit_id"] = work_unit_id
    nodes = telemetry.get("nodes") if isinstance(telemetry.get("nodes"), list) else []
    if nodes:
        first = nodes[0] if isinstance(nodes[0], dict) else {"name": "Director Sprint Input"}
        first["status"] = "RUNNING"
        first["activity"] = f"{work_unit_id} — submitted to Product Factory webhook"
        first["updated_at"] = now()
        nodes[0] = first
        telemetry["nodes"] = nodes
    temporary = PROJECT_TELEMETRY_PATH.with_suffix(f".{os.getpid()}.tmp")
    PROJECT_TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(telemetry, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, PROJECT_TELEMETRY_PATH)


def n8n_db_execution_rows(limit: int = 10) -> list[dict[str, object]]:
    if os.environ.get("FACTORY_N8N_DB_CORRELATE", "1").lower() in {"0", "false", "no"}:
        return []
    if os.environ.get("FACTORY_N8N_DB_CORRELATE") != "1" and "unittest" in sys.modules:
        return []
    try:
        sql = (
            "SELECT json_build_object('id', id::text, 'status', status, 'finished', finished) "
            f"FROM execution_entity WHERE \"workflowId\"='{FACTORY_WORKFLOW_ID}' "
            f"ORDER BY id DESC LIMIT {int(limit)}"
        )
        out = subprocess.check_output(
            [
                "docker",
                "exec",
                "n8n-self-hosted-ai-postgres-1",
                "psql",
                "-U",
                "n8n_local",
                "-d",
                "n8n",
                "-tAc",
                sql,
            ],
            timeout=10,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, object]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("id"):
            rows.append(value)
    return rows


def n8n_request(path: str) -> dict[str, object] | None:
    key = os.environ.get("N8N_API_KEY", "").strip()
    if not key:
        return None
    req = request.Request(
        f"{N8N_BASE.rstrip('/')}{path}",
        headers={"X-N8N-API-KEY": key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            value = json.loads(response.read().decode("utf-8", errors="replace"))
        return value if isinstance(value, dict) else None
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def n8n_execution_snapshot(execution_id: str) -> dict[str, object] | None:
    if not execution_id:
        return None
    snapshot = n8n_request(f"/api/v1/executions/{execution_id}?includeData=true")
    if snapshot:
        return snapshot
    for row in n8n_db_execution_rows(20):
        if str(row.get("id")) == str(execution_id):
            return row
    return None


def execution_finished(snapshot: dict[str, object]) -> bool:
    status = str(snapshot.get("status") or "").lower()
    if status in {"success", "error", "crashed", "failed", "canceled", "cancelled"}:
        return True
    if snapshot.get("stoppedAt") or snapshot.get("stopped_at"):
        return True
    if status in {"running", "waiting", "new"}:
        return False
    if snapshot.get("finished") is False:
        return False
    return True


def execution_failure_summary(snapshot: dict[str, object]) -> str:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    result = data.get("resultData") if isinstance(data.get("resultData"), dict) else {}
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    last_node = str(result.get("lastNodeExecuted") or snapshot.get("stoppedAt") or "unknown_node")
    message = sanitize_text(error.get("message") or error.get("name") or snapshot.get("status") or "factory_execution_error", 300)
    if "prompt" in message.lower() or "authorization" in message.lower():
        message = "factory_node_error"
    return f"{last_node}: {message}"


def wait_for_running_execution(execution_id: str, *, seconds: int = 1200) -> dict[str, object] | None:
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        last = n8n_execution_snapshot(execution_id)
        if not last:
            return None
        if execution_finished(last):
            return last
        time.sleep(5)
    return last


def correlate_timeout(brief: dict[str, object]) -> dict[str, object] | None:
    listing = n8n_request(f"/api/v1/executions?workflowId={FACTORY_WORKFLOW_ID}&limit=10")
    rows = listing.get("data") if isinstance(listing, dict) and isinstance(listing.get("data"), list) else n8n_db_execution_rows(10)
    if not rows:
        return None
    history = {str(item) for item in (brief.get("historical_execution_ids") or []) if item}
    marker = f"{brief.get('project_id')}:{brief.get('work_unit_id')}"
    sprint = str(brief.get("sprint_id") or "")
    for row in rows:
        if not isinstance(row, dict):
            continue
        execution_id = str(row.get("id") or "")
        if execution_id in history:
            continue
        snapshot = n8n_execution_snapshot(execution_id) or row
        if execution_finished(snapshot):
            continue
        blob = json.dumps(snapshot, ensure_ascii=False)[:20_000]
        if marker in blob or (sprint and sprint in blob):
            return snapshot
    running = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("id") or "") not in history
            and not execution_finished(row)
        ),
        None,
    )
    if not running:
        return None
    return n8n_execution_snapshot(str(running.get("id"))) or running


def last_executed_node(snapshot: dict[str, object]) -> str:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    result = data.get("resultData") if isinstance(data.get("resultData"), dict) else {}
    return str(result.get("lastNodeExecuted") or snapshot.get("lastNodeExecuted") or "")


def merge_execution_snapshot(result: dict[str, object], snapshot: dict[str, object]) -> dict[str, object]:
    execution_id = str(snapshot.get("id") or result.get("factory_execution_id") or "")
    merged = dict(result)
    if execution_id:
        merged["factory_execution_id"] = execution_id
        merged["execution_id"] = execution_id
    last_node = last_executed_node(snapshot)
    expected_next = HANDOFF_NEXT.get(last_node)
    if last_node:
        merged["last_executed_node"] = last_node
    if expected_next:
        merged["expected_next_node"] = expected_next
    if not execution_finished(snapshot):
        merged["transport_status"] = "RUNNING"
        merged["keep_running"] = True
        merged["reason"] = "matching_factory_execution_running"
        return merged
    status = str(snapshot.get("status") or "").lower()
    merged["n8n_status"] = status
    if status in {"success", "crashed", "error", "failed", "canceled"}:
        merged.pop("keep_running", None)
        if status == "success":
            merged.pop("sprint_report", None)
            if last_node not in TERMINAL_FACTORY_NODES:
                merged["transport_status"] = "FAILED"
                merged["reason"] = (
                    f"incomplete_factory_success last={last_node or 'unknown'} "
                    f"expected_next={expected_next or 'terminal factory stage'} missing handoff"
                )
            else:
                merged["transport_status"] = "FAILED"
                merged["reason"] = "n8n_success_without_factory_completion_contract"
        else:
            merged["transport_status"] = "FAILED"
            merged["reason"] = execution_failure_summary(snapshot)
    return merged


def resolve_factory_result(result: dict[str, object], brief: dict[str, object]) -> dict[str, object]:
    transport = str(result.get("transport_status") or "")
    execution_id = str(result.get("factory_execution_id") or result.get("execution_id") or "")
    if transport == "TIMEOUT":
        snapshot = n8n_execution_snapshot(execution_id) if execution_id else correlate_timeout(brief)
        if snapshot and not execution_finished(snapshot):
            waited = wait_for_running_execution(str(snapshot.get("id") or execution_id))
            snapshot = waited or snapshot
        if snapshot:
            return merge_execution_snapshot(result, snapshot)
        return result
    if transport in {"NON_JSON_RESPONSE", "FAILED"}:
        snapshot = n8n_execution_snapshot(execution_id) if execution_id else correlate_timeout(brief)
        if snapshot and not execution_finished(snapshot):
            waited = wait_for_running_execution(str(snapshot.get("id") or execution_id))
            return merge_execution_snapshot(result, waited or snapshot)
        if snapshot:
            return merge_execution_snapshot(result, snapshot)
    return result


def parse_factory_payload(raw: bytes, headers: object = None) -> dict[str, object]:
    execution_id = None
    if headers is not None:
        execution_id = headers.get("n8n-execution-id") or headers.get("N8N-Execution-Id")
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {"transport_status": "FAILED", "reason": "empty_factory_response", "factory_execution_id": execution_id}
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            if execution_id and not value.get("execution_id"):
                value["execution_id"] = execution_id
            return value
        return {"transport_status": "FAILED", "reason": "factory_response_not_object", "factory_execution_id": execution_id}
    except json.JSONDecodeError:
        return {
            "transport_status": "NON_JSON_RESPONSE",
            "reason": "JSONDecodeError",
            "factory_execution_id": execution_id,
            "sanitized_body": sanitize_text(text, 200),
        }


def _is_transport_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, TimeoutError):
        return True
    blob = f"{exc} {reason or ''}".lower()
    return "timed out" in blob or "timeout" in blob


def submit_to_factory(brief: dict[str, object]) -> dict[str, object]:
    body = json.dumps(brief, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = request.Request(FACTORY_WEBHOOK, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=25 * 60) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return {"transport_status": "FAILED", "reason": "factory_response_too_large"}
            return parse_factory_payload(raw, response.headers)
    except HTTPError as exc:
        raw = exc.read(64 * 1024)
        parsed = parse_factory_payload(raw, exc.headers)
        parsed["http_status"] = exc.code
        if parsed.get("sprint_report") or str(parsed.get("status") or ""):
            return parsed
        parsed.setdefault("transport_status", parsed.get("transport_status") or "FAILED")
        parsed["reason"] = parsed.get("reason") or f"HTTPError:{exc.code}"
        return parsed
    except (URLError, TimeoutError, OSError) as exc:
        failed = {"transport_status": "TIMEOUT" if _is_transport_timeout(exc) else "FAILED", "reason": type(exc).__name__}
        snapshot = correlate_timeout(brief)
        if snapshot:
            return merge_execution_snapshot(failed, snapshot)
        return failed


def classify_result(result: dict[str, object]) -> tuple[str, str]:
    report = result.get("sprint_report") if isinstance(result.get("sprint_report"), dict) else result
    if result.get("keep_running") or str(result.get("transport_status") or "") == "RUNNING":
        return "RUNNING", "Matching factory execution is still running"
    last_node = str(result.get("last_executed_node") or "")
    n8n_status = str(result.get("n8n_status") or "").lower()
    if n8n_status == "success" and last_node and last_node not in TERMINAL_FACTORY_NODES:
        expected = str(result.get("expected_next_node") or HANDOFF_NEXT.get(last_node) or "terminal factory stage")
        return (
            "NEEDS_REVIEW",
            f"incomplete_factory_success last={last_node} expected_next={expected} missing handoff",
        )
    transport = str(result.get("transport_status") or "")
    if transport in {"FAILED", "NON_JSON_RESPONSE", "TIMEOUT"}:
        return "NEEDS_REVIEW", f"Factory transport failed: {result.get('reason', 'UNKNOWN')}"
    status = str(report.get("status") or "").upper()
    qa = str(report.get("qa_result") or "").upper()
    lead = str(report.get("qa_lead_result") or "").upper()
    open_items = report.get("skipped_or_deferred_tasks")
    if status == "COMPLETED" and qa == "PASS" and lead == "QA_APPROVED" and isinstance(open_items, list) and not open_items:
        return "DONE", "PASS"
    if status == "DEFERRED":
        return "DEFERRED", f"Factory deferred the Work Unit: qa={qa or 'UNKNOWN'}"
    if status == "RECOVERY_EXHAUSTED":
        return "DEFERRED", "Factory recovery exhausted after bounded attempts"
    if status == "FAILED":
        return "FAILED", str(report.get("failure_signature") or report.get("failure_class") or "FAIL")
    if status in {"COMPLETED_WITH_OPEN_ITEMS", "BLOCKED"} or qa in {"NOT_VERIFIED", "FAIL"} or lead in {"QA_INCOMPLETE", "NOT_VERIFIED"}:
        deferred_reason = ""
        if isinstance(open_items, list) and open_items:
            first = open_items[0]
            if isinstance(first, dict):
                deferred_reason = str(first.get("reason") or "")
            else:
                deferred_reason = str(first)
        extra = f", deferred={deferred_reason}" if deferred_reason else ""
        return "NEEDS_REVIEW", f"Completion contract not met: status={status or 'UNKNOWN'}, qa={qa or 'UNKNOWN'}{extra}"
    if status == "COMPLETED":
        return "NEEDS_REVIEW", f"Factory completed without a verified QA contract: qa={qa or 'UNKNOWN'}"
    return "NEEDS_REVIEW", "Factory result did not include a complete verified completion contract."


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
            unit["blocked_reason"] = "Blocked by unresolved dependencies: " + ", ".join(blockers)
            unit["diagnosis"] = {
                "summary": f"Blocked until {blockers[0]} is DONE",
                "summary_tr": f"Gerekli bağımlılık henüz tamamlanmadı: {', '.join(blockers)}",
                "stage": "Dependency gate",
                "last_success_stage": "Plan validation",
                "root_failure": None,
                "blocking_unit_id": blockers[0],
                "blocking_unit_status": by_id[blockers[0]]["status"],
                "blocking_unit_summary": sanitize_text(str((by_id[blockers[0]].get("diagnosis") or {}).get("summary_tr") or by_id[blockers[0]].get("outcome_summary") or "")),
            }
            unit["updated_at"] = now()
        elif all(by_id[dependency]["status"] == "DONE" for dependency in dependencies[unit["id"]]):
            if unit["status"] == "PENDING":
                unit["status"] = "READY"
                unit["updated_at"] = now()


def eligible_unit(state: dict[str, object]) -> dict[str, object] | None:
    plan = load_plan(str(state["project_id"]))
    if not plan:
        raise ManifestError("immutable project plan is missing")
    by_id = {unit["id"]: unit for unit in state["work_units"]}
    for definition in sorted(plan["work_units"], key=lambda item: item["sequence"]):
        unit = by_id[definition["id"]]
        dependencies_done = all(by_id[dependency]["status"] == "DONE" for dependency in definition["dependencies"])
        if unit["status"] in {"PENDING", "READY"} and dependencies_done and int(unit.get("attempts") or 0) < MAX_FACTORY_ATTEMPTS:
            return unit
    return None


def finalize_project(state: dict[str, object]) -> None:
    statuses = [unit["status"] for unit in state["work_units"]]
    if all(status == "DONE" for status in statuses):
        state["status"] = "COMPLETED"
    elif any(status == "DONE" for status in statuses):
        state["status"] = "COMPLETED_WITH_OPEN_ITEMS"
    elif any(status in {"NEEDS_REVIEW", "DEFERRED", "BLOCKED"} for status in statuses):
        state["status"] = "NEEDS_REVIEW"
    else:
        state["status"] = "FAILED"
    state["current_work_unit_id"] = None
    state["completed_at"] = now()
    state["updated_at"] = now()
    atomic_write(state)


def run_project(project_id: str, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory) -> None:
    while True:
        await_existing = False
        with LOCK:
            state = load_state(project_id)
            if not state or state["status"] != "RUNNING":
                return
            running = next((item for item in state["work_units"] if item["status"] == "RUNNING"), None)
            if running:
                if not running.get("factory_execution_id"):
                    return
                brief = package_work_unit(state, running)
                unit = running
                await_existing = True
            else:
                update_blocked_units(state)
                if state.get("pause_requested"):
                    state["status"] = "PAUSED"
                    state["updated_at"] = now()
                    atomic_write(state)
                    return
                unit = eligible_unit(state)
                if not unit:
                    finalize_project(state)
                    return
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
                publish_dispatch_telemetry(state, unit, brief)
        if await_existing:
            result = resolve_factory_result(
                {"transport_status": "TIMEOUT", "reason": "TimeoutError", "factory_execution_id": unit["factory_execution_id"]},
                brief,
            )
        else:
            existing = correlate_timeout(brief) if submitter is submit_to_factory else None
            if existing and not execution_finished(existing):
                result = merge_execution_snapshot({"transport_status": "RUNNING"}, existing)
            else:
                result = submitter(brief)
            result = resolve_factory_result(result, brief)
        unit_status, summary = classify_result(result)
        if unit_status == "RUNNING":
            with LOCK:
                state = load_state(project_id)
                if not state:
                    return
                current = next(candidate for candidate in state["work_units"] if candidate["id"] == unit["id"])
                current["status"] = "RUNNING"
                if result.get("factory_execution_id"):
                    current["factory_execution_id"] = str(result["factory_execution_id"])
                current["updated_at"] = now()
                state["current_work_unit_id"] = current["id"]
                state["updated_at"] = now()
                atomic_write(state)
            time.sleep(5)
            continue
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
            if result.get("factory_execution_id") and not current.get("factory_execution_id"):
                current["factory_execution_id"] = str(result["factory_execution_id"])
            current["outcome_summary"] = summary[:1_000]
            current["last_error"] = None if unit_status == "DONE" else summary[:1_000]
            transport = str(result.get("transport_status") or "")
            stage = "Factory Submission" if transport else "Product Factory"
            last_success = "Work Unit Packaging" if transport else "Factory webhook accepted"
            expected_next = result.get("expected_next_node")
            if result.get("last_executed_node"):
                last_success = str(result["last_executed_node"])
                stage = str(expected_next or HANDOFF_NEXT.get(last_success) or "missing factory handoff")
            elif current.get("factory_execution_id") and transport:
                last_success = "Director Sprint Input"
                stage = "Factory webhook response"
            current["diagnosis"] = None if unit_status == "DONE" else build_diagnosis(
                state, current, status=unit_status, summary=summary, stage=stage, last_success=last_success,
                execution_id=current.get("factory_execution_id"), expected_next=expected_next, result=result,
            )
            if unit_status == "FAILED":
                previous = current.get("failure_signature")
                current["failure_signature"] = summary
                if int(current.get("attempts") or 0) < MAX_FACTORY_ATTEMPTS and (not previous or previous == summary):
                    current["status"] = "READY"
                    current["completed_at"] = None
                    current["last_result"] = {"status": "RETRYING", "summary": f"Attempt {current['attempts']} failed; bounded retry remains."}
                else:
                    current["status"] = "NEEDS_REVIEW"
                    current["completed_at"] = now()
                    current["last_result"] = {"status": "NEEDS_REVIEW", "summary": summary}
            else:
                current["completed_at"] = now()
            current["updated_at"] = now()
            state["current_work_unit_id"] = None
            state["updated_at"] = now()
            update_blocked_units(state)
            atomic_write(state)


def start_project(project_id: str, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory, *, background: bool = True) -> dict[str, object]:
    with LOCK:
        state = load_state(project_id)
        if not state:
            raise ManifestError("project must be validated before production can start")
        if state["status"] == "RUNNING":
            raise ManifestError("project is already running")
        if state["status"] not in {"VALIDATED", "DRAFT"}:
            raise ManifestError("only a validated, unstarted project can start production")
        if another_project_running(project_id):
            raise ManifestError("another project is already running; one Work Unit globally")
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


def start_validated_project(payload: object, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory, *, background: bool = True) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ManifestError("start requires a Master Plan or {project_id}")
    if set(payload) == {"project_id"}:
        if not isinstance(payload.get("project_id"), str):
            raise ManifestError("start requires exactly one prepared project_id")
        return start_project(payload["project_id"], submitter, background=background)
    manifest = validate_manifest(payload)
    prepare_project(manifest)
    return start_project(str(manifest["project_id"]), submitter, background=background)


def is_unproven_transport_review(unit: dict[str, object]) -> bool:
    error = str(unit.get("last_error") or "")
    return (
        unit.get("status") == "NEEDS_REVIEW"
        and not unit.get("factory_execution_id")
        and unit.get("qa_status") is None
        and "Factory transport failed" in error
    )


def is_infrastructure_routing_review(unit: dict[str, object]) -> bool:
    if unit.get("status") != "NEEDS_REVIEW" or unit.get("qa_status") not in {None, "NOT_VERIFIED"}:
        return False
    diagnosis = unit.get("diagnosis") if isinstance(unit.get("diagnosis"), dict) else {}
    stage = str(diagnosis.get("stage") or "")
    error = str(unit.get("last_error") or unit.get("outcome_summary") or "")
    blob = f"{stage} {error} {diagnosis.get('last_success_stage') or ''} {diagnosis.get('expected_next_stage') or ''}".lower()
    return (
        stage == "Provider Success Router"
        or "provider_role_unknown" in blob
        or "tamamlanan factory rolü" in blob
        or "completed provider role was unknown" in blob
        or ("unknown" in blob and "provider success" in blob)
        or "incomplete_factory_success" in blob
        or "missing handoff" in blob
        or stage == "Normalize Specialist Result"
        or ("specialist" in blob and "normalize specialist" in blob)
        or "constraints disappeared" in blob
        or "inherited constraints" in blob
        or "acceptance criteria disappeared" in blob
        or "inherited acceptance" in blob
        or "specialist_contract" in blob
        or "provider_chain_exhausted" in blob
        or "provider_timeout" in blob
        or "technical_timeout" in blob
        or "connection was aborted" in blob
        or ("factory transport failed" in blob and "timeout" in blob)
        or "developer pool exhausted" in blob
        or "provider_not_found" in blob
        or "model_unavailable" in blob
    )


def reset_unproven_transport_reviews(state: dict[str, object]) -> bool:
    changed = False
    plan = load_plan(str(state["project_id"])) or {"work_units": []}
    dependencies = {unit["id"]: unit["dependencies"] for unit in plan["work_units"]}
    for unit in state.get("work_units", []):
        if not (is_unproven_transport_review(unit) or is_infrastructure_routing_review(unit)):
            continue
        history = list(unit.get("historical_execution_ids") or [])
        if unit.get("factory_execution_id"):
            history.append(str(unit["factory_execution_id"]))
        unit["historical_execution_ids"] = history[-8:]
        ledger = list(unit.get("historical_attempt_ledger") or [])
        ledger.append({
            "attempts": int(unit.get("attempts") or 0),
            "status": unit.get("status"),
            "last_result": unit.get("last_result"),
            "outcome_summary": unit.get("outcome_summary"),
            "qa_status": unit.get("qa_status"),
            "qa_lead_status": unit.get("qa_lead_status"),
            "completed_at": unit.get("completed_at"),
            "historical_execution_ids": list(history),
        })
        unit["historical_attempt_ledger"] = ledger[-8:]
        unit["status"] = "READY" if not dependencies.get(unit["id"]) else "PENDING"
        unit["attempts"] = 0
        unit["dispatch_id"] = None
        unit["completed_at"] = None
        unit["factory_execution_id"] = None
        unit["meaningful_factory_attempt"] = False
        unit["updated_at"] = now()
        changed = True
    if changed:
        for unit in state["work_units"]:
            if unit["status"] == "BLOCKED":
                unit["status"] = "PENDING" if dependencies.get(unit["id"]) else "READY"
                unit["blocked_reason"] = None
                unit["updated_at"] = now()
        state["completed_at"] = None
        state["pause_requested"] = False
        state["updated_at"] = now()
        update_blocked_units(state)
        atomic_write(state)
    return changed


def continue_project(project_id: str, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory, *, background: bool = True) -> dict[str, object]:
    with LOCK:
        state = load_state(project_id)
        if not state:
            raise ManifestError("project not found")
        reset_unproven_transport_reviews(state)
        state = load_state(project_id) or state
        if another_project_running(project_id):
            raise ManifestError("another project is already running; one Work Unit globally")
        if any(unit["status"] == "RUNNING" for unit in state["work_units"]):
            return state
        update_blocked_units(state)
        if not eligible_unit(state):
            finalize_project(state)
            return load_state(project_id) or state
        state["status"] = "RUNNING"
        state["pause_requested"] = False
        state["updated_at"] = now()
        atomic_write(state)
    if background:
        runner = threading.Thread(target=run_project, args=(project_id, submitter), daemon=True)
        RUNNERS[project_id] = runner
        runner.start()
    else:
        run_project(project_id, submitter)
    return load_state(project_id) or state


def maybe_continue_projects(submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory) -> None:
    if not STATE_DIR.exists():
        return
    for path in STATE_DIR.glob("*.json"):
        state = load_state(path.stem)
        if not state:
            continue
        reset_unproven_transport_reviews(state)
        state = load_state(path.stem)
        if not state or state.get("status") == "RUNNING" or another_project_running(str(state["project_id"])):
            continue
        if eligible_unit(state):
            continue_project(str(state["project_id"]), submitter, background=True)


def pause_project(project_id: str) -> dict[str, object]:
    with LOCK:
        state = load_state(project_id)
        if not state:
            raise ManifestError("project not found")
        if state["status"] != "RUNNING":
            raise ManifestError("project is not running")
        state["pause_requested"] = True
        if not any(unit["status"] == "RUNNING" for unit in state["work_units"]):
            state["status"] = "PAUSED"
        state["updated_at"] = now()
        atomic_write(state)
        return state


def resume_project(project_id: str, submitter: Callable[[dict[str, object]], dict[str, object]] = submit_to_factory, *, background: bool = True) -> dict[str, object]:
    with LOCK:
        state = load_state(project_id)
        if not state or state["status"] != "PAUSED":
            raise ManifestError("project is not paused")
        if another_project_running(project_id):
            raise ManifestError("another project is already running; one Work Unit globally")
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
    resume_ids: list[str] = []
    with LOCK:
        for path in STATE_DIR.glob("*.json"):
            state = load_state(path.stem)
            if not state:
                continue
            changed = False
            resume = False
            for unit in state.get("work_units", []):
                if unit.get("status") != "RUNNING":
                    continue
                if unit.get("factory_execution_id"):
                    resume = True
                    continue
                unit["status"] = "READY" if not unit.get("dependencies") else "PENDING"
                unit["attempts"] = 0
                unit["dispatch_id"] = None
                unit["completed_at"] = None
                unit["last_result"] = None
                unit["last_error"] = None
                unit["outcome_summary"] = None
                unit["diagnosis"] = None
                unit["meaningful_factory_attempt"] = False
                unit["updated_at"] = now()
                changed = True
            if resume:
                state["status"] = "RUNNING"
                state["pause_requested"] = False
                state["updated_at"] = now()
                atomic_write(state)
                resume_ids.append(str(state["project_id"]))
            elif changed:
                if any(unit.get("status") == "BLOCKED" for unit in state.get("work_units", [])):
                    for unit in state["work_units"]:
                        if unit["status"] == "BLOCKED":
                            unit["status"] = "PENDING"
                            unit["blocked_reason"] = None
                    update_blocked_units(state)
                state["status"] = "NEEDS_REVIEW"
                state["pause_requested"] = False
                state["current_work_unit_id"] = None
                state["completed_at"] = None
                state["updated_at"] = now()
                atomic_write(state)
    for project_id in resume_ids:
        runner = threading.Thread(target=run_project, args=(project_id,), daemon=True)
        RUNNERS[project_id] = runner
        runner.start()


def public_state(state: dict[str, object]) -> dict[str, object]:
    plan = load_plan(str(state.get("project_id", ""))) or {"work_units": []}
    definitions = {unit["id"]: unit for unit in plan["work_units"]}
    units = []
    for unit in state.get("work_units", []):
        definition = definitions.get(unit["id"], {})
        diagnosis = unit.get("diagnosis")
        if not diagnosis and unit.get("status") in {"NEEDS_REVIEW", "FAILED", "DEFERRED"}:
            diagnosis = build_diagnosis(
                state, unit, status=str(unit["status"]),
                summary=str(unit.get("last_error") or unit.get("outcome_summary") or "Terminal factory result without a verified DONE contract"),
                stage="Factory Submission" if not unit.get("factory_execution_id") else "Product Factory",
                last_success="Work Unit Packaging" if not unit.get("factory_execution_id") else "Director Sprint Input",
                execution_id=unit.get("factory_execution_id"),
            )
        units.append({
            **{key: definition.get(key) for key in ("work_unit_id", "title", "goal", "dependencies", "sequence")},
            **{key: unit.get(key) for key in ("id", "status", "attempts", "started_at", "completed_at", "factory_execution_id", "qa_status", "qa_lead_status", "inspector_score", "acceptance_evidence", "last_result", "blocked_reason", "last_error", "updated_at")},
            "diagnosis": diagnosis,
        })
    counts = {status: sum(1 for unit in units if unit["status"] == status) for status in UNIT_STATUSES}
    current = next((unit for unit in units if unit["id"] == state.get("current_work_unit_id")), None)
    return {
        "project_id": state.get("project_id"),
        "project_title": state.get("project_title"),
        "status": state.get("status"),
        "created_at": state.get("created_at"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "current_work_unit_id": state.get("current_work_unit_id"),
        "current_work_unit": current,
        "pause_requested": state.get("pause_requested"),
        "counts": counts,
        "total": len(units),
        "max_attempts": MAX_FACTORY_ATTEMPTS_DISPLAY,
        "project_health": project_health(state),
        "open_ids": {
            "FAILED": [unit["id"] for unit in units if unit["status"] == "FAILED"],
            "NEEDS_REVIEW": [unit["id"] for unit in units if unit["status"] == "NEEDS_REVIEW"],
            "DEFERRED": [unit["id"] for unit in units if unit["status"] == "DEFERRED"],
            "BLOCKED": [unit["id"] for unit in units if unit["status"] == "BLOCKED"],
        },
        "updated_at": state.get("updated_at"),
        "work_units": units,
    }
