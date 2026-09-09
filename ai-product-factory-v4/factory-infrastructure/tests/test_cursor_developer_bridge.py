from __future__ import annotations

import importlib.util
import json
import os
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
            ("executable", "/usr/bin/env"),
            ("shell", "bash -c 'id'"),
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
        self.assertEqual([], result["tests_executed"])
        self.assertEqual([], result["test_commands"])

    def test_trusted_runtime_resolves_npm_without_process_path(self):
        fake = Path(self.temp.name) / "trusted-bin"
        fake.mkdir()
        npm = fake / "npm"
        npm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        npm.chmod(0o755)
        node = fake / "node"
        node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        node.chmod(0o755)
        self.bridge.TRUSTED_RUNTIME_DIR_CANDIDATES = (str(fake),)
        with mock.patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=False):
            self.assertEqual(self.bridge.resolve_trusted_executable("npm"), npm.resolve())
            self.assertEqual(self.bridge.resolve_trusted_executable("node"), node.resolve())
            self.assertIsNone(self.bridge.resolve_trusted_executable("bash"))
            self.assertIn(str(fake.resolve()), self.bridge.trusted_runtime_path().split(os.pathsep))
        env = self.bridge.trusted_subprocess_env()
        self.assertEqual(env["PATH"], str(fake.resolve()))
        self.assertNotIn("npm", env)

    def test_known_tests_use_allowlisted_npm_from_package_json(self):
        fake = Path(self.temp.name) / "trusted-bin"
        fake.mkdir()
        recorder = Path(self.temp.name) / "npm-invocations.txt"
        npm = fake / "npm"
        npm.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$PATH\" \"$@\" >> \"" + str(recorder) + "\"\nexit 0\n",
            encoding="utf-8",
        )
        npm.chmod(0o755)
        node = fake / "node"
        node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        node.chmod(0o755)
        self.bridge.TRUSTED_RUNTIME_DIR_CANDIDATES = (str(fake), "/usr/bin", "/bin")
        workspace = self.bridge.trusted_workspace("PROJECT-HANGMAN-PILOT-001")
        (workspace / "package.json").write_text(
            '{"name":"hangman-pilot","scripts":{"test":"node --check app.js && node test.mjs"}}\n',
            encoding="utf-8",
        )
        (workspace / "app.js").write_text('"use strict";\n', encoding="utf-8")
        evidence = self.bridge.run_known_tests(workspace)
        self.assertEqual(["npm test"], evidence["tests_executed"])
        self.assertEqual([0], evidence["test_exit_codes"])
        self.assertTrue(evidence["tests_passed"])
        self.assertTrue(any(str(npm.resolve()) in item for item in evidence["test_commands"]))
        self.assertIn("test --silent", evidence["test_commands"][0])
        recorded = recorder.read_text(encoding="utf-8").splitlines()
        self.assertEqual(["test", "--silent"], recorded[1:])
        self.assertEqual(recorded[0], self.bridge.trusted_runtime_path())

    def test_known_tests_fail_when_approved_npm_test_fails(self):
        fake = Path(self.temp.name) / "trusted-bin"
        fake.mkdir()
        npm = fake / "npm"
        npm.write_text("#!/bin/sh\necho boom >&2\nexit 7\n", encoding="utf-8")
        npm.chmod(0o755)
        node = fake / "node"
        node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        node.chmod(0o755)
        self.bridge.TRUSTED_RUNTIME_DIR_CANDIDATES = (str(fake),)
        workspace = self.bridge.trusted_workspace("PROJECT-HANGMAN-PILOT-001")
        (workspace / "package.json").write_text(
            '{"scripts":{"test":"node test.mjs"}}\n',
            encoding="utf-8",
        )
        evidence = self.bridge.run_known_tests(workspace)
        self.assertEqual(["npm test"], evidence["tests_executed"])
        self.assertEqual([7], evidence["test_exit_codes"])
        self.assertFalse(evidence["tests_passed"])
        self.assertIn("boom", evidence["test_stderr_summary"])

    def test_known_tests_do_not_run_without_approved_script(self):
        workspace = self.bridge.trusted_workspace("PROJECT-HANGMAN-PILOT-001")
        (workspace / "package.json").write_text('{"name":"hangman-pilot"}\n', encoding="utf-8")
        evidence = self.bridge.run_known_tests(workspace)
        self.assertEqual([], evidence["tests_executed"])
        self.assertFalse(evidence["tests_passed"])

    def test_execute_task_records_subprocess_test_evidence(self):
        def fake_invoke(workspace, _prompt):
            (workspace / "index.html").write_text("<!doctype html><html><body>Hangman</body></html>\n", encoding="utf-8")
            (workspace / "package.json").write_text(
                '{"scripts":{"test":"node --check app.js && node test.mjs"}}\n',
                encoding="utf-8",
            )
            return 0, '{"result":"done"}', "done", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke), mock.patch.object(
            self.bridge,
            "run_known_tests",
            return_value={
                "tests_executed": ["npm test"],
                "test_commands": ["/usr/local/bin/npm test --silent"],
                "test_exit_codes": [0],
                "tests_passed": True,
                "test_stdout_summary": "ok",
                "test_stderr_summary": "",
                "test_duration_ms": 12,
            },
        ):
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        self.assertTrue(result["implementation_applied"])
        self.assertEqual(["npm test"], result["tests_executed"])
        self.assertEqual([0], result["test_exit_codes"])
        self.assertTrue(result["tests_passed"])
        self.assertEqual(12, result["test_duration_ms"])
        self.assertIsNone(result["failure_class"])


if __name__ == "__main__":
    unittest.main()
