from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class DirectorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = load("director_bridge_test", ROOT / "bridge" / "director_bridge.py")
        self.temp = tempfile.TemporaryDirectory()
        self.bridge.AUDIT_DIR = Path(self.temp.name)
        self.valid = {
            "request_id": "SYNTHETIC-001",
            "sprint_title": "Harmless infrastructure validation",
            "goal": "Validate the narrow bridge contract without executing a product sprint.",
            "scope": ["Synthetic validation only"],
            "constraints": ["Do not create Hangman"],
            "acceptance_criteria": ["Validation succeeds"],
            "allowed_actions": ["READ_PROJECT"],
            "forbidden_actions": ["DEPLOY", "DATABASE_WRITE"],
            "requested_target": "SMOKE_FIXTURE",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_and_idempotent_duplicate(self):
        brief = self.bridge.validate_brief(self.valid)
        first, duplicate = self.bridge.register_brief(brief)
        second, duplicate_again = self.bridge.register_brief(brief)
        self.assertFalse(duplicate)
        self.assertTrue(duplicate_again)
        self.assertEqual(first["sprint_id"], second["sprint_id"])
        production = self.bridge.validate_brief({**self.valid, "request_id": "SYNTHETIC-PROD", "requested_target": "PRODUCTION"})
        self.assertEqual("PRODUCTION", production["requested_target"])

    def test_rejects_generic_rpc_surfaces(self):
        for field, value in (("workflow_id", "arbitrary"), ("filesystem_path", "/Users/example"), ("shell_command", "id")):
            with self.assertRaises(self.bridge.ValidationError):
                self.bridge.validate_brief({**self.valid, field: value})

    def test_rejects_unknown_target_action_and_malformed(self):
        for mutation in ({"requested_target": "../../production"}, {"allowed_actions": ["EXECUTE_SHELL"]}, {"goal": ""}):
            with self.assertRaises(self.bridge.ValidationError):
                self.bridge.validate_brief({**self.valid, **mutation})


class MonitorTests(unittest.TestCase):
    def test_23_project_progress_renders(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        self.assertIn("İş Birimleri", html)
        self.assertIn("id=\"project-progress\"", html)

    def test_24_work_unit_table_renders(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        for column in ("Kimlik", "Başlık", "Durum", "Deneme", "Bağımlılıklar", "Güncellendi"):
            self.assertIn(column, html)
        self.assertIn("id=\"unit-rows\"", html)

    def test_25_current_work_unit_renders(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        self.assertIn("MEVCUT İŞ BİRİMİ", html)
        self.assertIn("id=\"current-work-unit\"", html)
        self.assertIn("Deneme", html)

    def test_26_existing_node_table_renders(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        self.assertIn("<th>Ad</th>", html)
        self.assertIn("id=\"node-rows\"", html)

    def test_27_timestamps_use_europe_istanbul(self):
        script = (ROOT / "monitor" / "assets" / "app.js").read_text()
        self.assertIn("Europe/Istanbul", script)

    def test_28_no_secrets_prompts_or_thoughts_rendered(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        script = (ROOT / "monitor" / "assets" / "app.js").read_text()
        blob = html + script
        for forbidden in ("FACTORY_BRIDGE_TOKEN", "Authorization", "chain-of-thought", "localStorage", "prompt"):
            self.assertNotIn(forbidden, blob)

    def test_telemetry_reader_fails_closed(self):
        monitor = load("factory_monitor_test", ROOT / "monitor" / "factory_monitor.py")
        with tempfile.TemporaryDirectory() as directory:
            monitor.TELEMETRY = Path(directory) / "missing.json"
            self.assertEqual("WAITING", monitor.read_telemetry()["factory_status"])

    def test_director_console_controls(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        script = (ROOT / "monitor" / "assets" / "app.js").read_text()
        self.assertIn("PLANI DOĞRULA", html)
        self.assertIn("PROJEYİ BAŞLAT", html)
        self.assertIn("SORUN DETAYI", html)
        self.assertIn("Fallback", html)
        self.assertIn("diag-fallback", html)
        self.assertIn("diag-codex", html)
        self.assertIn("diag-minimax", html)
        self.assertIn("diag-laguna", html)
        self.assertIn("Takıldığı aşama", html)
        self.assertIn("/api/projects/validate", script)
        self.assertIn("/api/projects/start", script)
        self.assertIn("İNCELEME GEREKİYOR", script)
        self.assertIn("diagnosis-panel", html)
        self.assertIn("diagnosis-root", html)
        self.assertIn("BLOKE", script)
        self.assertNotIn("Execute Node", html)
        self.assertNotIn("Retry Node", html)
        self.assertNotIn("Change Model", html)
        self.assertNotIn("Workflow Editor", html)

    def test_localhost_only_in_monitor_assets(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        script = (ROOT / "monitor" / "assets" / "app.js").read_text()
        self.assertNotIn("https://", html + script)
        self.assertNotIn("http://", html + script)

    def test_monitor_write_methods_are_narrow(self):
        source = (ROOT / "monitor" / "factory_monitor.py").read_text()
        self.assertIn("def do_PUT", source)
        self.assertIn("self.send_error(405)", source)
        self.assertIn('HOST = "127.0.0.1"', source)


class SecurityAndRegressionTests(unittest.TestCase):
    def test_35_b04_artifact_unchanged_on_disk(self):
        path = REPO / "AI-Product-Factory-V4-Codex-Executor-B04.json"
        self.assertTrue(path.exists())
        workflow = json.loads(path.read_text())
        self.assertEqual("y5gVRIcXPvXoTQic", workflow.get("id") or workflow.get("id"))

    def test_36_inspector_remains_observer(self):
        workflow = json.loads((REPO / "AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json").read_text())
        inspector = next(node for node in workflow["nodes"] if node["name"] == "Factory Inspector")
        self.assertIn("control_plane_mutation_allowed", inspector["parameters"]["jsCode"])
        self.assertIn("false", inspector["parameters"]["jsCode"])

    def test_37_node_61_is_telemetry_publisher(self):
        workflow = json.loads((REPO / "AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json").read_text())
        self.assertEqual(61, len(workflow["nodes"]))
        self.assertTrue(any(node["name"] == "Factory Telemetry Publisher" for node in workflow["nodes"]))

    def test_38_deterministic_qa_name_preserved(self):
        workflow = json.loads((REPO / "AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json").read_text())
        self.assertTrue(any(node["name"] == "Deterministic QA Gate" for node in workflow["nodes"]))

    def test_39_developer_order_codex_minimax_laguna(self):
        workflow = json.loads((REPO / "AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json").read_text())
        names = [node["name"] for node in workflow["nodes"]]
        self.assertLess(names.index("Codex Executor"), names.index("MiniMax Developer"))
        self.assertLess(names.index("MiniMax Developer"), names.index("Laguna Developer"))
        dispatcher = next(node for node in workflow["nodes"] if node["name"] == "Developer Dispatcher")
        code = dispatcher["parameters"]["jsCode"]
        self.assertIn("CODEX", code)
        self.assertIn("MINIMAX", code)
        self.assertIn("LAGUNA", code)

    def test_40_provider_routing_roles_preserved(self):
        workflow = json.loads((REPO / "AI-Product-Factory-V4-Production-Ready-V4.3.2-Infrastructure.json").read_text())
        names = {node["name"] for node in workflow["nodes"]}
        for required in ("Analyst", "Analyst NVIDIA Fallback", "Analyst Groq Fallback", "Specialist", "Planner", "QA Lead"):
            self.assertIn(required, names)

    def test_bridge_binds_localhost(self):
        source = (ROOT / "bridge" / "director_bridge.py").read_text()
        self.assertIn('HOST = "127.0.0.1"', source)
        self.assertIn("/v1/start_sprint", source)

    def test_b04_id_is_codex_executor(self):
        raw = (REPO / "AI-Product-Factory-V4-Codex-Executor-B04.json").read_text()
        self.assertIn("y5gVRIcXPvXoTQic", raw)
        self.assertNotIn("market-intelligence", raw.lower())


if __name__ == "__main__":
    unittest.main()
