import json
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import project_orchestrator as orchestrator


def unit(unit_id, dependencies=None):
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
    }


def manifest(units, project_id="PROJECT-TEST"):
    return {
        "schema_version": "1.0",
        "project_id": project_id,
        "project_name": "Synthetic Project",
        "project_goal": "Test deterministic orchestration without executing Product Factory.",
        "global_constraints": ["Synthetic only"],
        "global_forbidden_actions": ["No MI", "No production Host Writer", "No credentials"],
        "definition_of_done": ["All eligible synthetic units reach deterministic terminal states"],
        "work_units": units,
    }


def passed_result():
    return {"sprint_report": {"status": "COMPLETED", "qa_result": "PASS", "qa_lead_result": "QA_APPROVED", "skipped_or_deferred_tasks": []}}


def failed_result():
    return {"sprint_report": {"status": "FAILED", "qa_result": "FAIL", "qa_lead_result": "QA_INCOMPLETE", "skipped_or_deferred_tasks": ["synthetic failure"]}}


def deferred_result():
    return {"sprint_report": {"status": "DEFERRED", "qa_result": "NOT_VERIFIED", "qa_lead_result": "QA_INCOMPLETE", "skipped_or_deferred_tasks": ["deferred"]}}


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

    def assert_invalid(self, value):
        with self.assertRaises(orchestrator.ManifestError):
            orchestrator.validate_manifest(value)

    def test_01_valid_five_unit_plan_accepted(self):
        value = orchestrator.validate_manifest(manifest([unit(f"W00{i}") for i in range(1, 6)]))
        self.assertEqual(5, len(value["work_units"]))
        view = orchestrator.validation_view(value)
        self.assertTrue(view["valid"])
        self.assertEqual("PROJECT-TEST", view["project_id"])

    def test_02_duplicate_work_unit_id_rejected(self):
        self.assert_invalid(manifest([unit("W001"), unit("W001")]))

    def test_03_missing_dependency_rejected(self):
        self.assert_invalid(manifest([unit("W001", ["W999"])]))

    def test_04_self_dependency_rejected(self):
        self.assert_invalid(manifest([unit("W001", ["W001"])]))

    def test_05_dependency_cycle_rejected(self):
        self.assert_invalid(manifest([unit("W001", ["W002"]), unit("W002", ["W001"])]))

    def test_06_missing_acceptance_criteria_rejected(self):
        payload = manifest([unit("W001")])
        payload["work_units"][0]["acceptance_criteria"] = []
        self.assert_invalid(payload)

    def test_07_malformed_payload_rejected(self):
        self.assert_invalid("not-json-object")
        self.assert_invalid({"schema_version": "1.0"})

    def test_08_privileged_control_fields_rejected(self):
        payload = manifest([unit("W001")])
        payload["workflow_id"] = "arbitrary"
        self.assert_invalid(payload)
        shell = manifest([unit("W001")])
        shell["work_units"][0]["shell_command"] = "rm -rf"
        self.assert_invalid(shell)

    def test_09_root_unit_is_ready(self):
        state = orchestrator.create_state(orchestrator.validate_manifest(manifest([unit("W001"), unit("W002", ["W001"])])))
        statuses = {item["id"]: item["status"] for item in state["work_units"]}
        self.assertEqual("READY", statuses["W001"])
        self.assertEqual("PENDING", statuses["W002"])

    def test_10_dependent_stays_pending_until_done(self):
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002", ["W001"])]))
        state = orchestrator.prepare_project(value)
        statuses = {item["id"]: item["status"] for item in state["work_units"]}
        self.assertEqual("PENDING", statuses["W002"])
        self.assertEqual("W001", orchestrator.eligible_unit(state)["id"])

    def test_11_done_unlocks_dependent(self):
        calls = []
        orchestrator.start_validated_project(
            manifest([unit("W001"), unit("W002", ["W001"])]),
            lambda brief: calls.append(brief["work_unit_id"]) or passed_result(),
            background=False,
        )
        self.assertEqual(["W001", "W002"], calls)

    def test_12_failed_independent_does_not_block_unrelated(self):
        calls = []
        def submit(brief):
            calls.append(brief["work_unit_id"])
            if brief["work_unit_id"] == "W032":
                return {"sprint_report": {"status": "COMPLETED_WITH_OPEN_ITEMS", "qa_result": "NOT_VERIFIED", "qa_lead_result": "QA_INCOMPLETE", "skipped_or_deferred_tasks": ["review"]}}
            return passed_result()
        orchestrator.start_validated_project(
            manifest([unit("W032"), unit("W056")]),
            submit,
            background=False,
        )
        statuses = {item["id"]: item["status"] for item in orchestrator.load_state("PROJECT-TEST")["work_units"]}
        self.assertEqual(["W032", "W056"], calls)
        self.assertEqual("NEEDS_REVIEW", statuses["W032"])
        self.assertEqual("DONE", statuses["W056"])

    def test_13_failed_dependency_blocks_dependent(self):
        calls = []
        def submit(brief):
            calls.append(brief["work_unit_id"])
            if brief["work_unit_id"] == "W001":
                return {"sprint_report": {"status": "COMPLETED_WITH_OPEN_ITEMS", "qa_result": "NOT_VERIFIED", "qa_lead_result": "QA_INCOMPLETE", "skipped_or_deferred_tasks": ["review"]}}
            return passed_result()
        orchestrator.start_validated_project(
            manifest([unit("W001"), unit("W002", ["W001"]), unit("W003")]),
            submit,
            background=False,
        )
        statuses = {item["id"]: item["status"] for item in orchestrator.load_state("PROJECT-TEST")["work_units"]}
        self.assertEqual("NEEDS_REVIEW", statuses["W001"])
        self.assertEqual("BLOCKED", statuses["W002"])
        self.assertEqual("DONE", statuses["W003"])
        self.assertNotIn("W002", calls)

    def test_14_exactly_one_running_at_once(self):
        seen = []
        def submit(brief):
            state = orchestrator.load_state("PROJECT-TEST")
            running = [item["id"] for item in state["work_units"] if item["status"] == "RUNNING"]
            seen.append(running)
            self.assertEqual(1, len(running))
            return passed_result()
        orchestrator.start_validated_project(manifest([unit("W001"), unit("W002"), unit("W003")]), submit, background=False)
        self.assertEqual(3, len(seen))

    def test_15_sequence_order_not_numeric_shuffle(self):
        calls = []
        orchestrator.start_validated_project(
            manifest([unit("W010"), unit("W002"), unit("W100")]),
            lambda brief: calls.append(brief["work_unit_id"]) or passed_result(),
            background=False,
        )
        self.assertEqual(["W010", "W002", "W100"], calls)

    def test_16_done_persisted(self):
        orchestrator.start_validated_project(manifest([unit("W001")]), lambda _brief: passed_result(), background=False)
        self.assertEqual("DONE", orchestrator.load_state("PROJECT-TEST")["work_units"][0]["status"])

    def test_17_needs_review_persisted(self):
        orchestrator.start_validated_project(
            manifest([unit("W001")]),
            lambda _brief: {"sprint_report": {"status": "COMPLETED_WITH_OPEN_ITEMS", "qa_result": "NOT_VERIFIED", "qa_lead_result": "QA_INCOMPLETE", "skipped_or_deferred_tasks": ["x"]}},
            background=False,
        )
        self.assertEqual("NEEDS_REVIEW", orchestrator.load_state("PROJECT-TEST")["work_units"][0]["status"])

    def test_18_deferred_persisted(self):
        orchestrator.start_validated_project(manifest([unit("W001")]), lambda _brief: deferred_result(), background=False)
        self.assertEqual("DEFERRED", orchestrator.load_state("PROJECT-TEST")["work_units"][0]["status"])

    def test_19_attempt_count_persisted(self):
        orchestrator.start_validated_project(manifest([unit("W001")]), lambda _brief: passed_result(), background=False)
        self.assertEqual(1, orchestrator.load_state("PROJECT-TEST")["work_units"][0]["attempts"])

    def test_20_restart_resume_preserves_project(self):
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002")]))
        orchestrator.prepare_project(value)
        restored = orchestrator.load_state("PROJECT-TEST")
        self.assertEqual("VALIDATED", restored["status"])
        self.assertEqual(2, len(restored["work_units"]))

    def test_21_double_start_does_not_duplicate(self):
        calls = []
        payload = manifest([unit("W001")])
        orchestrator.start_validated_project(payload, lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        with self.assertRaises(orchestrator.ManifestError):
            orchestrator.start_validated_project(payload, lambda brief: calls.append("dup") or passed_result(), background=False)
        self.assertEqual(["W001"], calls)
        self.assertEqual(1, len(list((orchestrator.STATE_DIR).glob("PROJECT-TEST.json"))))

    def test_22_running_reconciliation_does_not_redispatch(self):
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002", ["W001"])]))
        state = orchestrator.create_state(value)
        orchestrator.write_immutable_plan(value)
        state["status"] = "RUNNING"
        state["current_work_unit_id"] = "W001"
        state["work_units"][0]["status"] = "RUNNING"
        state["work_units"][0]["dispatch_id"] = "known-dispatch"
        orchestrator.atomic_write(state)
        orchestrator.recover_interrupted_projects()
        recovered = orchestrator.load_state("PROJECT-TEST")
        self.assertEqual("NEEDS_REVIEW", recovered["status"])
        self.assertEqual("READY", recovered["work_units"][0]["status"])
        self.assertEqual(0, recovered["work_units"][0]["attempts"])
        self.assertEqual(0, recovered["work_units"][1]["attempts"])

    def test_packager_sends_one_unit_not_master_plan(self):
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002")]))
        state = orchestrator.prepare_project(value)
        brief = orchestrator.package_work_unit(state, state["work_units"][0])
        self.assertEqual("W001", brief["work_unit_id"])
        self.assertNotIn("work_units", brief)
        self.assertNotIn("workflow_id", json.dumps(brief))
        self.assertEqual("SMOKE_FIXTURE", brief["requested_target"])
        self.assertEqual(brief["goal"], brief["objective"])
        self.assertNotIn("work_units", json.dumps(brief))

    def test_real_project_uses_production_writer_target(self):
        payload = manifest([unit("W001")])
        payload["project_id"] = "PROJECT-HANGMAN-PILOT-001"
        value = orchestrator.validate_manifest(payload)
        state = orchestrator.prepare_project(value)
        brief = orchestrator.package_work_unit(state, state["work_units"][0])
        self.assertEqual("PRODUCTION", brief["requested_target"])
        self.assertEqual("PRODUCTION", brief["writer_target"])
        self.assertTrue(brief["cursor_available"])
        self.assertTrue(brief["codex_available"])
        self.assertFalse(brief["minimax_available"])
        self.assertEqual("W001", brief["work_unit_id"])
        self.assertNotIn("workflow_id", json.dumps(brief))
        self.assertNotIn("filesystem_path", json.dumps(brief))

    def test_synthetic_project_sets_codex_available_for_allowlisted_smoke_target(self):
        value = orchestrator.validate_manifest(manifest([unit("W001")]))
        state = orchestrator.prepare_project(value)
        brief = orchestrator.package_work_unit(state, state["work_units"][0])
        self.assertEqual("SMOKE_FIXTURE", brief["writer_target"])
        self.assertTrue(brief["codex_available"])

    def test_transport_failure_shows_diagnosis_and_blocks_dependent(self):
        def submit(_brief):
            return {"transport_status": "NON_JSON_RESPONSE", "reason": "JSONDecodeError"}
        orchestrator.start_validated_project(manifest([unit("W001"), unit("W002", ["W001"])]), submit, background=False)
        visible = orchestrator.public_state(orchestrator.load_state("PROJECT-TEST"))
        w001 = visible["work_units"][0]
        w002 = visible["work_units"][1]
        self.assertEqual("NEEDS_REVIEW", w001["status"])
        self.assertEqual("BLOCKED", w002["status"])
        self.assertEqual("Factory Submission", w001["diagnosis"]["stage"])
        self.assertEqual("Work Unit Packaging", w001["diagnosis"]["last_success_stage"])
        self.assertIn("JSONDecodeError", w001["diagnosis"]["root_failure"])
        self.assertEqual(["W002"], w001["diagnosis"]["affected_work_unit_ids"])
        self.assertEqual("W001", w002["diagnosis"]["blocking_unit_id"])
        self.assertIsNone(w001["factory_execution_id"])

    def test_unproven_transport_review_is_reset_and_not_counted(self):
        orchestrator.start_validated_project(
            manifest([unit("W001"), unit("W002", ["W001"])]),
            lambda _brief: {"transport_status": "NON_JSON_RESPONSE", "reason": "JSONDecodeError"},
            background=False,
        )
        calls = []
        orchestrator.continue_project("PROJECT-TEST", lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        self.assertEqual(["W001", "W002"], calls)
        self.assertEqual("DONE", orchestrator.load_state("PROJECT-TEST")["work_units"][0]["status"])
        self.assertEqual(1, orchestrator.load_state("PROJECT-TEST")["work_units"][0]["attempts"])

    def test_sanitize_hides_secrets(self):
        self.assertNotIn("secret-token", orchestrator.sanitize_text("Authorization: Bearer secret-token"))

    def test_keep_running_is_not_terminal(self):
        status, summary = orchestrator.classify_result({"keep_running": True, "factory_execution_id": "91"})
        self.assertEqual("RUNNING", status)
        self.assertIn("still running", summary)

    def test_execution_id_makes_transport_review_proven(self):
        unit = {
            "status": "NEEDS_REVIEW",
            "factory_execution_id": "89",
            "qa_status": None,
            "last_error": "Factory transport failed: JSONDecodeError",
        }
        self.assertFalse(orchestrator.is_unproven_transport_review(unit))

    def test_infrastructure_routing_review_is_reset_and_keeps_history(self):
        orchestrator.start_validated_project(
            manifest([unit("W001"), unit("W002", ["W001"])]),
            lambda _brief: {
                "sprint_report": {
                    "status": "COMPLETED_WITH_OPEN_ITEMS",
                    "qa_result": "NOT_VERIFIED",
                    "qa_lead_result": "NOT_VERIFIED",
                    "skipped_or_deferred_tasks": ["x"],
                },
                "factory_execution_id": "90",
            },
            background=False,
        )
        state = orchestrator.load_state("PROJECT-TEST")
        state["work_units"][0]["diagnosis"] = {
            "stage": "Provider Success Router",
            "summary": "completed provider role was UNKNOWN",
        }
        state["work_units"][0]["last_error"] = "completed provider role was UNKNOWN"
        orchestrator.atomic_write(state)
        calls = []
        orchestrator.continue_project("PROJECT-TEST", lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        recovered = orchestrator.load_state("PROJECT-TEST")
        self.assertEqual(["W001", "W002"], calls)
        self.assertEqual(["90"], recovered["work_units"][0]["historical_execution_ids"])
        self.assertEqual("DONE", recovered["work_units"][0]["status"])
        self.assertEqual(1, recovered["work_units"][0]["attempts"])

    def test_failed_and_deferred_diagnosis_visible(self):
        orchestrator.start_validated_project(manifest([unit("W001")]), lambda _brief: deferred_result(), background=False)
        deferred = orchestrator.public_state(orchestrator.load_state("PROJECT-TEST"))["work_units"][0]
        self.assertEqual("DEFERRED", deferred["status"])
        self.assertEqual("Product Factory", deferred["diagnosis"]["stage"])
        self.assertTrue(deferred["diagnosis"]["summary_tr"])
        orchestrator.STATE_DIR = Path(self.temp.name) / "state-failed"
        orchestrator.start_validated_project(
            manifest([unit("W001")], project_id="PROJECT-TEST-FAIL"),
            lambda _brief: failed_result(),
            background=False,
        )
        failed = orchestrator.public_state(orchestrator.load_state("PROJECT-TEST-FAIL"))["work_units"][0]
        self.assertIn(failed["status"], {"NEEDS_REVIEW", "FAILED"})
        self.assertTrue(failed["diagnosis"]["root_failure"])

    def test_maybe_continue_does_not_raise_without_payload(self):
        orchestrator.maybe_continue_projects(lambda _brief: passed_result())

    def test_pause_after_current_then_resume(self):
        calls = []
        def submit(brief):
            calls.append(brief["work_unit_id"])
            if brief["work_unit_id"] == "W001":
                orchestrator.pause_project("PROJECT-TEST")
            return passed_result()
        orchestrator.start_validated_project(manifest([unit("W001"), unit("W002", ["W001"])]), submit, background=False)
        self.assertEqual(["W001"], calls)
        self.assertEqual("PAUSED", orchestrator.load_state("PROJECT-TEST")["status"])
        orchestrator.resume_project("PROJECT-TEST", lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        self.assertEqual(["W001", "W002"], calls)

    def test_synthetic_five_unit_graph(self):
        example = json.loads(Path(__file__).resolve().parents[2].joinpath("EXAMPLE_PROJECT_MASTER_PLAN.json").read_text())
        calls = []
        orchestrator.start_validated_project(example, lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        self.assertEqual(["W001", "W002", "W003", "W004", "W005"], calls)
        self.assertEqual("COMPLETED", orchestrator.load_state("PROJECT-SYNTHETIC-001")["status"])

    def test_three_attempt_limit_no_fourth(self):
        calls = []
        orchestrator.start_validated_project(
            manifest([unit("W002")]),
            lambda brief: calls.append(brief["work_unit_id"]) or failed_result(),
            background=False,
        )
        item = orchestrator.load_state("PROJECT-TEST")["work_units"][0]
        self.assertEqual(["W002", "W002", "W002"], calls)
        self.assertEqual(3, item["attempts"])
        self.assertEqual("NEEDS_REVIEW", item["status"])

    def test_independent_continuation_around_review(self):
        calls = []
        def submit(brief):
            calls.append(brief["work_unit_id"])
            if brief["work_unit_id"] == "W002":
                return {"sprint_report": {"status": "COMPLETED_WITH_OPEN_ITEMS", "qa_result": "NOT_VERIFIED", "qa_lead_result": "QA_INCOMPLETE", "skipped_or_deferred_tasks": ["x"]}}
            return passed_result()
        orchestrator.start_validated_project(
            manifest([unit("W002"), unit("W003", ["W002"]), unit("W004")]),
            submit,
            background=False,
        )
        statuses = {item["id"]: item["status"] for item in orchestrator.load_state("PROJECT-TEST")["work_units"]}
        self.assertEqual("NEEDS_REVIEW", statuses["W002"])
        self.assertEqual("BLOCKED", statuses["W003"])
        self.assertEqual("DONE", statuses["W004"])
        self.assertEqual(["W002", "W004"], calls)

    def test_alias_schema_three_unit_plan(self):
        payload = {
            "schema_version": "1.0",
            "project_id": "PROJECT-ALIAS",
            "project_title": "Alias Plan",
            "project_goal": "Accept the documented field names.",
            "global_context": "Synthetic",
            "global_constraints": ["Synthetic only"],
            "global_forbidden_actions": ["No MI"],
            "completion_criteria": ["Three units validate"],
            "work_units": [
                {"work_unit_id": "W001", "title": "A", "goal": "A", "context": "A", "dependencies": [], "allowed_files": ["sandbox/a.txt"], "forbidden_actions": ["No MI"], "acceptance_criteria": ["A"], "test_requirements": ["t"], "priority": 100},
                {"work_unit_id": "W002", "title": "B", "goal": "B", "context": "B", "dependencies": ["W001"], "allowed_files": ["sandbox/b.txt"], "forbidden_actions": ["No MI"], "acceptance_criteria": ["B"], "test_requirements": ["t"], "priority": 90},
                {"work_unit_id": "W003", "title": "C", "goal": "C", "context": "C", "dependencies": ["W001"], "allowed_files": ["sandbox/c.txt"], "forbidden_actions": ["No MI"], "acceptance_criteria": ["C"], "test_requirements": ["t"], "priority": 80},
            ],
        }
        value = orchestrator.validate_manifest(payload)
        self.assertEqual(["W001", "W002", "W003"], [unit["id"] for unit in value["work_units"]])

    def test_resume_does_not_resubmit_done(self):
        calls = []
        value = orchestrator.validate_manifest(manifest([unit("W001"), unit("W002")]))
        orchestrator.prepare_project(value)
        state = orchestrator.load_state("PROJECT-TEST")
        state["work_units"][0]["status"] = "DONE"
        state["work_units"][0]["attempts"] = 1
        state["status"] = "PAUSED"
        orchestrator.atomic_write(state)
        orchestrator.resume_project("PROJECT-TEST", lambda brief: calls.append(brief["work_unit_id"]) or passed_result(), background=False)
        self.assertEqual(["W002"], calls)
        self.assertEqual("DONE", orchestrator.load_state("PROJECT-TEST")["work_units"][0]["status"])
        payload = manifest([unit("W001")])
        payload["work_units"][0]["test_requirements"] = []
        self.assert_invalid(payload)

    def test_absolute_path_rejected(self):
        payload = manifest([unit("W001")])
        payload["work_units"][0]["allowed_scope"] = ["/etc/passwd"]
        self.assert_invalid(payload)

    def test_open_item_ids_remain_addressable(self):
        value = orchestrator.validate_manifest(manifest([unit(value) for value in ("W032", "W056", "W099", "W193")]))
        state = orchestrator.create_state(value)
        orchestrator.write_immutable_plan(value)
        for item, status in zip(state["work_units"], ("NEEDS_REVIEW", "READY", "FAILED", "DEFERRED")):
            item["status"] = status
        orchestrator.atomic_write(state)
        visible = orchestrator.public_state(orchestrator.load_state("PROJECT-TEST"))
        self.assertEqual(["W032"], visible["open_ids"]["NEEDS_REVIEW"])
        self.assertEqual(["W099"], visible["open_ids"]["FAILED"])
        self.assertEqual(["W193"], visible["open_ids"]["DEFERRED"])


    def test_n8n_success_at_specialist_is_not_done(self):
        merged = orchestrator.merge_execution_snapshot(
            {"transport_status": "FAILED", "reason": "empty_factory_response"},
            {
                "id": "94",
                "status": "success",
                "finished": True,
                "data": {"resultData": {"lastNodeExecuted": "Specialist"}},
            },
        )
        self.assertNotIn("sprint_report", merged)
        self.assertEqual("FAILED", merged["transport_status"])
        self.assertIn("incomplete_factory_success", merged["reason"])
        self.assertEqual("Specialist", merged["last_executed_node"])
        self.assertEqual("Normalize Specialist Result", merged["expected_next_node"])
        status, summary = orchestrator.classify_result(merged)
        self.assertEqual("NEEDS_REVIEW", status)
        self.assertNotEqual("DONE", status)
        self.assertIn("Normalize Specialist Result", summary)
        tr = orchestrator.diagnosis_tr(summary, "Normalize Specialist Result")
        self.assertIn("beklenen geçiş", tr)

    def test_specialist_constraint_diagnosis_is_turkish(self):
        tr = orchestrator.diagnosis_tr(
            "SPECIALIST_INVALID_CONTRACT: constraints disappeared: The final result must be a complete playable browser application.",
            "Normalize Specialist Result",
        )
        self.assertIn("devralınan kısıtlar", tr)

    def test_specialist_acceptance_diagnosis_is_turkish(self):
        tr = orchestrator.diagnosis_tr(
            "SPECIALIST_INVALID_CONTRACT: acceptance criteria disappeared: No blocking runtime error occurs during normal gameplay.",
            "Normalize Specialist Result",
        )
        self.assertIn("kabul kriterleri", tr)

    def test_n8n_success_is_not_done_without_terminal_contract(self):
        status, _summary = orchestrator.classify_result({
            "sprint_report": {"status": "COMPLETED", "qa_result": "PASS", "qa_lead_result": "QA_APPROVED", "skipped_or_deferred_tasks": []},
            "n8n_status": "success",
            "last_executed_node": "Specialist",
            "expected_next_node": "Normalize Specialist Result",
        })
        self.assertEqual("NEEDS_REVIEW", status)

    def test_keep_running_still_wins_over_last_node(self):
        status, summary = orchestrator.classify_result({
            "keep_running": True,
            "last_executed_node": "Specialist",
            "factory_execution_id": "95",
        })
        self.assertEqual("RUNNING", status)
        self.assertIn("still running", summary)

    def test_specialist_handoff_review_is_infrastructure(self):
        unit = {
            "status": "NEEDS_REVIEW",
            "qa_status": None,
            "last_error": "incomplete_factory_success last=Specialist expected_next=Normalize Specialist Result missing handoff",
            "diagnosis": {
                "stage": "Normalize Specialist Result",
                "last_success_stage": "Specialist",
                "expected_next_stage": "Normalize Specialist Result",
            },
        }
        self.assertTrue(orchestrator.is_infrastructure_routing_review(unit))

    def test_analyst_timeout_diagnosis_is_turkish(self):
        tr = orchestrator.diagnosis_tr(
            "The connection was aborted, perhaps the server is offline",
            "Analyst",
        )
        self.assertIn("Birincil Analyst sağlayıcısı zaman aşımına uğradı", tr)
        self.assertEqual("Analyst sağlayıcı çağrısı", orchestrator.stuck_stage_tr("Normalize Analyst Result", "PROVIDER_TIMEOUT 300000ms", "Analyst"))
        self.assertEqual("DENENMEDİ", orchestrator.fallback_status_tr("The connection was aborted, perhaps the server is offline", "Analyst"))
        self.assertEqual("DENENDİ", orchestrator.fallback_status_tr("PRIMARY_TIMEOUT", "Analyst", {"_provider_timeout_state": "PRIMARY_TIMEOUT", "_provider_fallback_attempted": True}))
        self.assertEqual("BAŞARILI", orchestrator.fallback_status_tr("", "", {"_provider_timeout_state": "FALLBACK_SUCCESS"}))
        self.assertEqual("BAŞARISIZ", orchestrator.fallback_status_tr("PROVIDER_CHAIN_EXHAUSTED", "Provider Failure Terminal"))

    def test_webhook_timeout_keeps_running_execution(self):
        merged = orchestrator.merge_execution_snapshot(
            {"transport_status": "TIMEOUT", "reason": "TimeoutError"},
            {"id": "97", "status": "running", "finished": False, "data": {"resultData": {"lastNodeExecuted": "Analyst"}}},
        )
        self.assertEqual("RUNNING", merged["transport_status"])
        self.assertTrue(merged["keep_running"])
        self.assertEqual("97", merged["factory_execution_id"])
        status, summary = orchestrator.classify_result(merged)
        self.assertEqual("RUNNING", status)
        self.assertIn("still running", summary)

    def test_analyst_timeout_review_is_infrastructure(self):
        unit = {
            "status": "NEEDS_REVIEW",
            "qa_status": None,
            "last_error": "Analyst: The connection was aborted, perhaps the server is offline",
            "diagnosis": {
                "stage": "Analyst",
                "last_success_stage": "Director Sprint Input",
                "expected_next_stage": "Normalize Analyst Result",
            },
        }
        self.assertTrue(orchestrator.is_infrastructure_routing_review(unit))
        unit["last_error"] = "provider_chain_exhausted TECHNICAL_TIMEOUT"
        self.assertTrue(orchestrator.is_infrastructure_routing_review(unit))
        timeout_unit = {
            "status": "NEEDS_REVIEW",
            "qa_status": None,
            "factory_execution_id": "97",
            "meaningful_factory_attempt": False,
            "last_error": "Factory transport failed: timeout",
            "diagnosis": {
                "stage": "Factory Submission",
                "last_success_stage": "Work Unit Packaging",
                "expected_next_stage": None,
            },
        }
        self.assertTrue(orchestrator.is_infrastructure_routing_review(timeout_unit))

    def test_director_sprint_input_hang_is_turkish_and_infrastructure(self):
        self.assertEqual(
            "Director Sprint Input aşamasında execution ilerlemedi.",
            orchestrator.diagnosis_tr("TimeoutError", "Factory webhook response"),
        )
        self.assertEqual(
            "Director Sprint Input aşamasında execution ilerlemedi.",
            orchestrator.diagnosis_tr("unknown_node: crashed", "Factory webhook response"),
        )
        self.assertEqual(
            "Director Sprint Input aşamasında execution ilerlemedi.",
            orchestrator.diagnosis_tr("controlled hang", "Director Sprint Input"),
        )
        self.assertEqual("Director Sprint Input", orchestrator.stuck_stage_tr("Director Sprint Input", "unknown_node: crashed"))
        hang = {
            "status": "NEEDS_REVIEW",
            "qa_status": None,
            "factory_execution_id": "107",
            "last_error": "unknown_node: crashed",
            "diagnosis": {
                "stage": "Factory webhook response",
                "last_success_stage": "Director Sprint Input",
                "summary_tr": "Director Sprint Input aşamasında execution ilerlemedi.",
            },
        }
        self.assertTrue(orchestrator.is_infrastructure_routing_review(hang))

    def test_developer_fallback_review_is_infrastructure(self):
        hangman_style = {
            "status": "NEEDS_REVIEW",
            "qa_status": "NOT_VERIFIED",
            "factory_execution_id": None,
            "inspector_score": None,
            "meaningful_factory_attempt": False,
            "last_error": "Completion contract not met: status=COMPLETED_WITH_OPEN_ITEMS, qa=NOT_VERIFIED",
            "diagnosis": {"stage": "Product Factory"},
        }
        self.assertFalse(orchestrator.is_infrastructure_routing_review(hangman_style))
        deferred = dict(hangman_style)
        deferred["factory_execution_id"] = "99"
        deferred["last_error"] = "Completion contract not met: status=COMPLETED_WITH_OPEN_ITEMS, qa=NOT_VERIFIED, deferred=Developer pool exhausted after technical failure."
        self.assertTrue(orchestrator.is_infrastructure_routing_review(deferred))
        qa_after_developer = dict(hangman_style)
        qa_after_developer["last_error"] = "Completion contract not met: status=COMPLETED_WITH_OPEN_ITEMS, qa=NOT_VERIFIED, deferred=Deterministic QA could not verify mandatory evidence."
        self.assertTrue(orchestrator.is_infrastructure_routing_review(qa_after_developer))
        scored_qa_fail = dict(qa_after_developer)
        scored_qa_fail["qa_status"] = "FAIL"
        scored_qa_fail["inspector_score"] = 42
        scored_qa_fail["last_error"] = "Completion contract not met: status=COMPLETED_WITH_OPEN_ITEMS, qa=FAIL"
        self.assertFalse(orchestrator.is_infrastructure_routing_review(scored_qa_fail))
        chain = orchestrator.developer_chain_tr(
            "provider_not_found MODEL_UNAVAILABLE",
            {
                "codex_available": False,
                "developer_exclusions": ["CODEX"],
                "developer_route": "MINIMAX",
                "technical_failures": [{"developer_route": "MINIMAX"}],
            },
        )
        self.assertEqual("Kullanılamadı", chain["codex_tr"])
        self.assertEqual("Sağlayıcı bulunamadı", chain["minimax_tr"])
        self.assertEqual("Denenmedi", chain["laguna_tr"])
        selected = orchestrator.developer_chain_tr(
            "Deterministic QA could not verify mandatory evidence",
            {"sprint_report": {"laguna_developed_count": 1, "task_outcomes": [{"developer": "LAGUNA"}]}},
        )
        self.assertEqual("Seçildi", selected["laguna_tr"])
        self.assertIn("Laguna'ya devam etmedi", orchestrator.diagnosis_tr("provider_not_found", "Developer Dispatcher"))
        self.assertIn(
            "hız sınırına",
            orchestrator.diagnosis_tr(
                "rate limit",
                "Laguna Developer",
                {
                    "developer_route": "LAGUNA",
                    "technical_failures": [{"developer_route": "LAGUNA", "failure_type": "RATE_LIMIT", "message": "429"}],
                },
            ),
        )
        self.assertNotIn(
            "Laguna'ya devam etmedi",
            orchestrator.diagnosis_tr(
                "rate limit",
                "Laguna Developer",
                {
                    "developer_route": "LAGUNA",
                    "technical_failures": [{"developer_route": "LAGUNA", "failure_type": "RATE_LIMIT"}],
                },
            ),
        )
        self.assertEqual("Developer seçimi", orchestrator.stuck_stage_tr("Recovery Controller", "PROVIDER_NOT_FOUND", "MiniMax Developer"))
        self.assertIn("öneri üretebilir", orchestrator.diagnosis_tr("Codex unavailable", "Codex Executor"))

    def test_infrastructure_reset_preserves_attempt_ledger(self):
        orchestrator.prepare_project(orchestrator.validate_manifest(manifest([unit("W001")])))
        state = orchestrator.load_state("PROJECT-TEST")
        w001 = state["work_units"][0]
        w001["status"] = "NEEDS_REVIEW"
        w001["attempts"] = 3
        w001["qa_status"] = "NOT_VERIFIED"
        w001["last_error"] = "Completion contract not met: status=COMPLETED_WITH_OPEN_ITEMS, qa=NOT_VERIFIED, deferred=Developer pool exhausted after technical failure."
        w001["outcome_summary"] = w001["last_error"]
        w001["last_result"] = {"status": "NEEDS_REVIEW", "summary": w001["last_error"]}
        w001["historical_execution_ids"] = ["100", "101"]
        orchestrator.atomic_write(state)
        self.assertTrue(orchestrator.reset_unproven_transport_reviews(orchestrator.load_state("PROJECT-TEST")))
        recovered = orchestrator.load_state("PROJECT-TEST")["work_units"][0]
        self.assertEqual("READY", recovered["status"])
        self.assertEqual(0, recovered["attempts"])
        self.assertEqual(3, recovered["historical_attempt_ledger"][0]["attempts"])
        self.assertEqual(["100", "101"], recovered["historical_attempt_ledger"][0]["historical_execution_ids"])
        self.assertEqual(["100", "101"], recovered["historical_execution_ids"])

    def test_meaningful_open_items_are_not_auto_reset(self):
        unit = {
            "status": "NEEDS_REVIEW",
            "qa_status": "NOT_VERIFIED",
            "factory_execution_id": "100",
            "meaningful_factory_attempt": True,
            "last_error": "Completion contract not met: status=COMPLETED_WITH_OPEN_ITEMS, qa=NOT_VERIFIED",
            "diagnosis": {"stage": "Product Factory"},
        }
        self.assertFalse(orchestrator.is_infrastructure_routing_review(unit))

    def test_error_execution_with_unfinished_flag_is_finished(self):
        self.assertTrue(orchestrator.execution_finished({"id": "93", "status": "error", "finished": False}))
        self.assertTrue(orchestrator.execution_finished({"id": "97", "status": "success", "finished": True}))
        self.assertFalse(orchestrator.execution_finished({"id": "98", "status": "running", "finished": False}))

    def test_urlerror_timeout_is_transport_timeout(self):
        self.assertTrue(orchestrator._is_transport_timeout(TimeoutError("timed out")))
        self.assertTrue(orchestrator._is_transport_timeout(orchestrator.URLError("timed out")))
        self.assertFalse(orchestrator._is_transport_timeout(orchestrator.URLError("connection refused")))



    def test_correlate_timeout_uses_unique_running_factory_execution(self):
        from unittest import mock
        rows = [
            {"id": "106", "status": "crashed", "finished": True},
            {"id": "107", "status": "running", "finished": False},
        ]
        brief = {
            "project_id": "PROJECT-HANGMAN-PILOT-001",
            "work_unit_id": "W001",
            "sprint_id": "PROJECT-HANGMAN-PILOT-001-W001-1",
            "historical_execution_ids": ["105", "106"],
        }
        with mock.patch.object(orchestrator, "n8n_request", return_value=None), mock.patch.object(
            orchestrator, "n8n_db_execution_rows", return_value=rows
        ), mock.patch.object(orchestrator, "n8n_execution_snapshot", side_effect=lambda eid: next(r for r in rows if r["id"] == eid)):
            matched = orchestrator.correlate_timeout(brief)
        self.assertEqual("107", matched["id"])
        with mock.patch.object(orchestrator, "n8n_request", return_value=None), mock.patch.object(
            orchestrator, "n8n_db_execution_rows",
            return_value=rows + [{"id": "108", "status": "running", "finished": False}],
        ), mock.patch.object(orchestrator, "n8n_execution_snapshot", side_effect=lambda eid: {"id": eid, "status": "running", "finished": False}):
            self.assertIsNone(orchestrator.correlate_timeout(brief))


if __name__ == "__main__":
    unittest.main()
