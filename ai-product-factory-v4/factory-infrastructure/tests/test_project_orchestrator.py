import copy
import json
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import project_orchestrator as orchestrator


def unit(unit_id, dependencies=None, priority=50):
    return {
        "work_unit_id": unit_id,
        "title": f"Synthetic {unit_id}",
        "objective": f"Complete harmless synthetic unit {unit_id}",
        "context": "Synthetic state-machine fixture only.",
        "dependencies": dependencies or [],
        "allowed_scope": [f"sandbox/{unit_id}.txt"],
        "forbidden_scope": ["protected.txt"],
        "acceptance_criteria": [f"{unit_id} is represented in the fake result"],
        "test_requirements": ["Use the injected test submitter"],
        "expected_artifacts": [f"sandbox/{unit_id}.txt"],
        "priority": priority,
    }


def manifest(units):
    return {
        "project_id": "PROJECT-TEST",
        "schema_version": "1.0",
        "project_name": "Synthetic Project",
        "project_goal": "Test deterministic orchestration without executing Product Factory.",
        "global_context": {
            "summary": "Synthetic test product.",
            "architecture": "No runtime architecture.",
            "constraints": ["Synthetic only"],
            "forbidden_actions": ["No MI", "No production Host Writer", "No credentials"],
            "completion_criteria": ["All eligible synthetic units reach deterministic terminal states"],
        },
        "work_units": units,
    }


def passed_result():
    return {"sprint_report": {"status": "COMPLETED", "qa_result": "PASS", "qa_lead_result": "QA_APPROVED", "skipped_or_deferred_tasks": []}}


def failed_result():
    return {"sprint_report": {"status": "FAILED", "qa_result": "FAIL", "qa_lead_result": "QA_INCOMPLETE", "skipped_or_deferred_tasks": ["synthetic failure"]}}


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_state_dir = orchestrator.STATE_DIR
        self.original_telemetry = orchestrator.PROJECT_TELEMETRY_PATH
        orchestrator.STATE_DIR = Path(self.temp.name) / "state"
        orchestrator.PROJECT_TELEMETRY_PATH = Path(self.temp.name) / "telemetry" / "latest.json"

    def tearDown(self):
        orchestrator.STATE_DIR = self.original_state_dir
        orchestrator.PROJECT_TELEMETRY_PATH = self.original_telemetry
        self.temp.cleanup()

    def test_valid_five_unit_manifest(self):
        value = orchestrator.validate_manifest(manifest([unit(f"W00{i}") for i in range(1, 6)]))
        self.assertEqual(5, len(value["work_units"]))

    def assert_invalid(self, value):
        with self.assertRaises(orchestrator.ManifestError):
            orchestrator.validate_manifest(value)

    def test_manifest_rejections(self):
        duplicate = manifest([unit("W001"), unit("W001")])
        self.assert_invalid(duplicate)
        unknown = manifest([unit("W001", ["W999"])])
        self.assert_invalid(unknown)
        cycle = manifest([unit("W001", ["W002"]), unit("W002", ["W001"])])
        self.assert_invalid(cycle)
        arbitrary_workflow = manifest([unit("W001")])
        arbitrary_workflow["workflow_id"] = "arbitrary"
        self.assert_invalid(arbitrary_workflow)
        arbitrary_filesystem = manifest([unit("W001")])
        arbitrary_filesystem["work_units"][0]["allowed_scope"] = ["/etc/passwd"]
        self.assert_invalid(arbitrary_filesystem)
        shell = manifest([unit("W001")])
        shell["work_units"][0]["shell_command"] = "rm -rf"
        self.assert_invalid(shell)
        credentials = manifest([unit("W001")])
        credentials["global_context"]["credential"] = "secret"
        self.assert_invalid(credentials)
        missing_acceptance = manifest([unit("W001")])
        missing_acceptance["work_units"][0]["acceptance_criteria"] = []
        self.assert_invalid(missing_acceptance)
        oversized = manifest([unit("W001")])
        oversized["project_goal"] = "x" * (orchestrator.MAX_MANIFEST_BYTES + 1)
        self.assert_invalid(oversized)

    def test_sequential_dependency_order(self):
        calls = []
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002", ["W001"]), unit("W003", ["W002"])]))
        orchestrator.prepare_project(value)
        self.assertEqual([], calls)
        orchestrator.start_project("PROJECT-TEST", lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        self.assertEqual(["W001", "W002", "W003"], calls)
        self.assertEqual("COMPLETED", orchestrator.load_state("PROJECT-TEST")["status"])

    def test_independent_failure_continues(self):
        calls = []
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002", ["W001"]), unit("W003", ["W002"]), unit("W004", ["W001"])]))
        def submit(brief):
            calls.append(brief["work_unit_id"])
            return failed_result() if brief["work_unit_id"] == "W002" else passed_result()
        orchestrator.prepare_project(value)
        orchestrator.start_project("PROJECT-TEST", submit, background=False)
        state = orchestrator.load_state("PROJECT-TEST")
        statuses = {item["id"]: item["status"] for item in state["work_units"]}
        self.assertEqual(["W001", "W002", "W004"], calls)
        self.assertEqual("FAILED", statuses["W002"])
        self.assertEqual("BLOCKED", statuses["W003"])
        self.assertEqual("DONE", statuses["W004"])

    def test_exact_open_item_ids(self):
        value = orchestrator.validate_manifest(manifest([unit(value) for value in ("W032", "W056", "W099", "W193")]))
        state = orchestrator.create_state(value)
        for item in state["work_units"]:
            item["status"] = "FAILED"
        orchestrator.atomic_write(state)
        visible = orchestrator.public_state(orchestrator.load_state("PROJECT-TEST"))
        self.assertEqual(["W032", "W056", "W099", "W193"], [item["id"] for item in visible["work_units"] if item["status"] == "FAILED"])

    def test_review_and_deferred_states_persist(self):
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002")]))
        state = orchestrator.create_state(value)
        state["work_units"][0]["status"] = "NEEDS_REVIEW"
        state["work_units"][1]["status"] = "DEFERRED"
        orchestrator.atomic_write(state)
        restored = orchestrator.load_state("PROJECT-TEST")
        self.assertEqual(["NEEDS_REVIEW", "DEFERRED"], [item["status"] for item in restored["work_units"]])

    def test_pause_after_current_then_resume(self):
        calls = []
        value = orchestrator.validate_manifest(manifest([unit("W005"), unit("W006", ["W005"])]))
        def submit(brief):
            calls.append(brief["work_unit_id"])
            if brief["work_unit_id"] == "W005":
                orchestrator.pause_project("PROJECT-TEST")
            return passed_result()
        orchestrator.prepare_project(value)
        orchestrator.start_project("PROJECT-TEST", submit, background=False)
        self.assertEqual(["W005"], calls)
        self.assertEqual("PAUSED_HUMAN_AUTH", orchestrator.load_state("PROJECT-TEST")["status"])
        orchestrator.resume_project("PROJECT-TEST", lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        self.assertEqual(["W005", "W006"], calls)

    def test_restart_fails_closed_without_redispatch(self):
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002", ["W001"])]))
        state = orchestrator.create_state(value)
        state["status"] = "RUNNING"
        state["current_work_unit_id"] = "W001"
        state["work_units"][0]["status"] = "RUNNING"
        state["work_units"][0]["dispatch_id"] = "known-dispatch"
        orchestrator.atomic_write(state)
        orchestrator.recover_interrupted_projects()
        recovered = orchestrator.load_state("PROJECT-TEST")
        self.assertEqual("PAUSED_HUMAN_AUTH", recovered["status"])
        self.assertEqual("NEEDS_REVIEW", recovered["work_units"][0]["status"])
        self.assertEqual(0, recovered["work_units"][1]["attempts"])

    def test_packager_is_bounded_and_fixed_target(self):
        value = orchestrator.validate_manifest(manifest([unit("W001")]))
        state = orchestrator.prepare_project(value)
        brief = orchestrator.package_work_unit(state, state["work_units"][0])
        self.assertEqual("SMOKE_FIXTURE", brief["requested_target"])
        self.assertEqual("SMOKE_FIXTURE", brief["writer_target"])
        self.assertNotIn("work_units", brief)
        self.assertNotIn("workflow_id", json.dumps(brief))

    def test_priority_then_natural_work_unit_id_order(self):
        value = orchestrator.validate_manifest(manifest([unit("W010", priority=2), unit("W002", priority=2), unit("W100", priority=1)]))
        self.assertEqual(["W100", "W002", "W010"], [item["id"] for item in value["work_units"]])

    def test_immutable_plan_is_separate_from_mutable_state(self):
        value = orchestrator.validate_manifest(manifest([unit("W001")]))
        state = orchestrator.prepare_project(value)
        original = orchestrator.plan_path("PROJECT-TEST").read_bytes()
        self.assertNotIn("title", state["work_units"][0])
        self.assertNotIn("dependencies", state["work_units"][0])
        orchestrator.start_project("PROJECT-TEST", lambda _brief: passed_result(), background=False)
        self.assertEqual(original, orchestrator.plan_path("PROJECT-TEST").read_bytes())

    def test_duplicate_start_is_rejected(self):
        value = orchestrator.validate_manifest(manifest([unit("W001")]))
        orchestrator.prepare_project(value)
        orchestrator.start_project("PROJECT-TEST", lambda _brief: passed_result(), background=False)
        with self.assertRaises(orchestrator.ManifestError):
            orchestrator.start_project("PROJECT-TEST", lambda _brief: passed_result(), background=False)

    def test_two_hundred_units_validate(self):
        value = orchestrator.validate_manifest(manifest([unit(f"W{i:03d}") for i in range(1, 201)]))
        self.assertEqual(200, len(value["work_units"]))

    def test_terminal_and_ambiguous_units_are_not_selected(self):
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002"), unit("W003"), unit("W004"), unit("W005")]))
        state = orchestrator.prepare_project(value)
        for item, status in zip(state["work_units"][:4], ("DONE", "RUNNING", "NEEDS_REVIEW", "DEFERRED")):
            item["status"] = status
        orchestrator.atomic_write(state)
        self.assertEqual("W005", orchestrator.eligible_unit(state)["id"])

    def test_prepare_requires_explicit_start_and_persists_evidence(self):
        calls = []
        value = orchestrator.validate_manifest(manifest([unit("W001")]))
        prepared = orchestrator.prepare_project(value)
        self.assertEqual("LOADED", prepared["status"])
        self.assertIsNone(prepared["started_at"])
        self.assertEqual([], calls)
        result = passed_result()
        result["execution_id"] = 123
        result["sprint_report"]["factory_inspector"] = {"sprint_score": 94}
        orchestrator.start_project("PROJECT-TEST", lambda brief: calls.append(brief["work_unit_id"]) or result, background=False)
        completed = orchestrator.load_state("PROJECT-TEST")
        item = completed["work_units"][0]
        self.assertEqual(["W001"], calls)
        self.assertEqual("123", item["factory_execution_id"])
        self.assertEqual("PASS", item["qa_status"])
        self.assertEqual("QA_APPROVED", item["qa_lead_status"])
        self.assertEqual(94, item["inspector_score"])
        self.assertEqual(94.0, orchestrator.project_health(completed))

    def test_conservative_mode_rejects_second_running_project(self):
        first_manifest = orchestrator.validate_manifest(manifest([unit("W001")]))
        first = orchestrator.prepare_project(first_manifest)
        first["status"] = "RUNNING"
        first["work_units"][0]["status"] = "RUNNING"
        orchestrator.atomic_write(first)
        second_raw = manifest([unit("W101")])
        second_raw["project_id"] = "PROJECT-SECOND"
        orchestrator.prepare_project(orchestrator.validate_manifest(second_raw))
        with self.assertRaises(orchestrator.ManifestError):
            orchestrator.start_project("PROJECT-SECOND", background=False)


if __name__ == "__main__":
    unittest.main()
