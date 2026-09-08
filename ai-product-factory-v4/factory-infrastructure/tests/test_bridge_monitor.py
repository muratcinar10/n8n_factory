from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


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
        self.assertEqual("VALIDATED_NOT_SUBMITTED", first["current_state"])

    def test_rejects_generic_rpc_surfaces(self):
        for field, value in (
            ("workflow_id", "arbitrary"),
            ("filesystem_path", "/Users/example"),
            ("shell_command", "id"),
        ):
            payload = {**self.valid, field: value}
            with self.assertRaises(self.bridge.ValidationError):
                self.bridge.validate_brief(payload)

    def test_rejects_unknown_target_action_and_malformed(self):
        for mutation in (
            {"requested_target": "../../production"},
            {"allowed_actions": ["EXECUTE_SHELL"]},
            {"goal": ""},
        ):
            with self.assertRaises(self.bridge.ValidationError):
                self.bridge.validate_brief({**self.valid, **mutation})

    def test_rejects_oversized_and_conflicting_duplicate(self):
        with self.assertRaises(self.bridge.ValidationError):
            self.bridge.validate_brief({**self.valid, "goal": "x" * 4_001})
        brief = self.bridge.validate_brief(self.valid)
        self.bridge.register_brief(brief)
        with self.assertRaises(self.bridge.ValidationError):
            self.bridge.register_brief({**brief, "goal": "different"})


class MonitorTests(unittest.TestCase):
    def test_telemetry_reader_fails_closed(self):
        monitor = load("factory_monitor_test", ROOT / "monitor" / "factory_monitor.py")
        with tempfile.TemporaryDirectory() as directory:
            monitor.TELEMETRY = Path(directory) / "missing.json"
            self.assertEqual("WAITING", monitor.read_telemetry()["factory_status"])
            monitor.TELEMETRY.write_text(json.dumps({"factory_status": "RUNNING", "nodes": []}))
            self.assertEqual("RUNNING", monitor.read_telemetry()["factory_status"])
            monitor.TELEMETRY.write_text(json.dumps({"factory_status": "COMPLETED", "nodes": []}))
            self.assertEqual("COMPLETED", monitor.read_telemetry()["factory_status"])
            monitor.TELEMETRY.write_text(json.dumps({"factory_status": "FAILED", "nodes": []}))
            self.assertEqual("FAILED", monitor.read_telemetry()["factory_status"])

    def test_no_control_or_external_assets(self):
        html = (ROOT / "monitor" / "assets" / "index.html").read_text()
        script = (ROOT / "monitor" / "assets" / "app.js").read_text()
        self.assertNotIn("Start Sprint", html)
        local_navigation = 'http://127.0.0.1:8765'
        self.assertEqual(1, (html + script).count(local_navigation))
        self.assertNotIn("http://", (html + script).replace(local_navigation, ""))
        self.assertNotIn("https://", html + script)
        self.assertIn("setInterval(refresh,5000)", script)

    def test_unified_turkish_console_is_safe_and_complete(self):
        html = (ROOT / "bridge" / "console" / "index.html").read_text()
        script = (ROOT / "bridge" / "console" / "console.js").read_text()
        for label in ("Planı Doğrula", "Projeyi Yükle", "Projeyi Başlat", "Projeyi Sürdür"):
            self.assertIn(label, html)
        self.assertIn("61 Aşamalı Fabrika Ekranı", html)
        self.assertIn("Ana Proje Planı", html)
        self.assertIn("<th>Öncelik</th>", html)
        self.assertIn("<th>Bağımlılıklar</th>", html)
        self.assertIn("Geçerli İş Birimi", html)
        self.assertIn("/api/factory-status", script)
        for endpoint in ("/api/projects/validate", "/api/projects/prepare", "/api/projects/start", "/resume"):
            self.assertIn(endpoint, script)
        self.assertNotIn("load-start", html + script)
        self.assertNotIn("FACTORY_BRIDGE_TOKEN", html + script)
        self.assertNotIn("Authorization", html + script)
        self.assertNotIn("localStorage", html + script)
        self.assertNotIn("shell", (html + script).lower())


if __name__ == "__main__":
    unittest.main()
