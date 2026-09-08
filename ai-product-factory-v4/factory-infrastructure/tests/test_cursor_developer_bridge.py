from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "cursor_developer_bridge_test",
        ROOT / "bridge" / "cursor_developer_bridge.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class CursorBridgeContractTests(unittest.TestCase):
    def setUp(self):
        self.bridge = load()
        self.temp = tempfile.TemporaryDirectory()
        self.bridge.WORKSPACES_ROOT = Path(self.temp.name) / "workspaces"
        self.bridge.WORKSPACES_ROOT.mkdir(parents=True)
        self.payload = {
            "project_id": "PROJECT-HANGMAN-PILOT-001",
            "work_unit_id": "W001",
            "sprint_id": "PROJECT-HANGMAN-PILOT-001-W001-1",
            "task_id": "T01",
            "objective": "Create the Hangman shell",
            "requirements": ["visible shell"],
            "constraints": ["no paid APIs"],
            "acceptance_criteria": ["Application renders a visible Hangman shell"],
            "test_requirements": ["Verify the primary page renders"],
            "known_context": ["browser game"],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_arbitrary_path_and_command_fields(self):
        for field, value in (
            ("workspace", "/tmp/evil"),
            ("path", "/etc/passwd"),
            ("command", "rm -rf /"),
            ("env", {"CURSOR_API_KEY": "secret"}),
            ("workflow_id", "abc"),
            ("repository", "https://example.com/repo.git"),
        ):
            with self.assertRaises(self.bridge.BridgeError):
                self.bridge.validate_payload({**self.payload, field: value})

    def test_rejects_unknown_project_and_keeps_workspace_inside_root(self):
        with self.assertRaises(self.bridge.BridgeError):
            self.bridge.validate_payload({**self.payload, "project_id": "PROJECT-OTHER"})
        workspace = self.bridge.trusted_workspace("PROJECT-HANGMAN-PILOT-001")
        self.assertEqual(workspace.parent.resolve(), self.bridge.WORKSPACES_ROOT.resolve())
        self.assertEqual(workspace.name, "PROJECT-HANGMAN-PILOT-001")

    def test_applied_flag_comes_from_filesystem_not_prose(self):
        def fake_invoke(workspace, prompt):
            (workspace / "index.html").write_text("<!doctype html><html><body>Hangman</body></html>\n", encoding="utf-8")
            self.assertIn("FACTORY_TASK=", prompt)
            self.assertNotIn("CURSOR_API_KEY", prompt)
            return 0, '{"result":"I changed /etc/passwd and tests pass"}', "I changed /etc/passwd and tests pass", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke):
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        self.assertTrue(result["implementation_applied"])
        self.assertEqual(["index.html"], result["changed_files"])
        self.assertTrue(result["diff_present"])
        self.assertIn("index.html contains an HTML document", result["runtime_checks"])
        self.assertNotIn("/etc/passwd", result["changed_files"])

    def test_prose_without_files_is_not_applied(self):
        def fake_invoke(_workspace, _prompt):
            return 0, '{"result":"I changed app.js and tests pass"}', "I changed app.js and tests pass", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke):
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        self.assertFalse(result["implementation_applied"])
        self.assertEqual([], result["changed_files"])
        self.assertEqual("CURSOR_NO_APPLIED_CHANGE", result["failure_class"])
        self.assertFalse(result["tests_passed"])


if __name__ == "__main__":
    unittest.main()
