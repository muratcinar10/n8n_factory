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
        self.bridge.RECEIPTS_DIR = Path(self.temp.name) / "receipts"
        self.bridge.RECEIPTS_DIR.mkdir(parents=True)
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
            ("recovery_mode", "PRIOR_APPLY_VERIFICATION"),
            ("prior_execution_id", "113"),
            ("provenance", {"git_head": "abc"}),
            ("fingerprint", {"app.js": "deadbeef"}),
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
        self.assertFalse(result["recovery_verification"]["eligible"])

    def _seed_committed_hangman(self) -> Path:
        workspace = self.bridge.trusted_workspace("PROJECT-HANGMAN-PILOT-001")
        self.bridge.ensure_repo(workspace)
        (workspace / "index.html").write_text("<!doctype html><html><body>Hangman</body></html>\n", encoding="utf-8")
        (workspace / "app.js").write_text('"use strict";\n', encoding="utf-8")
        (workspace / "package.json").write_text(
            '{"name":"hangman-pilot","scripts":{"test":"node --check app.js"}}\n',
            encoding="utf-8",
        )
        self.bridge.git(workspace, "add", "-A")
        self.bridge.git(
            workspace,
            "-c",
            "user.email=factory@local",
            "-c",
            "user.name=Factory",
            "commit",
            "-m",
            "factory snapshot W001",
        )
        self.bridge.write_applied_provenance(
            workspace,
            "PROJECT-HANGMAN-PILOT-001",
            "W001",
            ["app.js", "index.html", "package.json"],
            prior_execution_id="113",
        )
        return workspace

    def test_recovery_http_fields_cannot_force_tests(self):
        def fake_invoke(_workspace, _prompt):
            return 0, '{"result":"already applied"}', "already applied", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke):
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        self.assertFalse(result["implementation_applied"])
        self.assertEqual([], result["tests_executed"])
        self.assertEqual("CURSOR_NO_APPLIED_CHANGE", result["failure_class"])
        self.assertEqual("recovery_mode_not_explicit", result["recovery_verification"]["reason"])

    def test_recovery_rejects_fingerprint_mismatch(self):
        workspace = self._seed_committed_hangman()
        self.bridge.write_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W001")
        (workspace / "app.js").write_text('"use strict";\nmutated\n', encoding="utf-8")
        self.bridge.git(workspace, "add", "-A")
        self.bridge.git(
            workspace,
            "-c",
            "user.email=factory@local",
            "-c",
            "user.name=Factory",
            "commit",
            "-m",
            "unrelated mutation",
        )

        def fake_invoke(_workspace, _prompt):
            return 0, '{"result":"no changes"}', "no changes", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke) as invoke:
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        invoke.assert_called_once()
        self.assertEqual("WRITE", result["developer_action"])
        self.assertFalse(result["implementation_applied"])
        self.assertEqual([], result["tests_executed"])
        self.assertFalse(result["recovery_verification"]["eligible"])
        self.assertIn(result["recovery_verification"]["reason"], {"fingerprint_mismatch", "git_head_mismatch", "unrelated_workspace_mutation"})
        self.assertEqual("CURSOR_NO_APPLIED_CHANGE", result["failure_class"])

    def test_recovery_runs_allowlisted_tests_without_new_apply(self):
        self._seed_committed_hangman()
        self.bridge.write_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W001")

        def fake_invoke(_workspace, _prompt):
            return 0, '{"result":"workspace already has W001"}', "workspace already has W001", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke) as invoke, mock.patch.object(
            self.bridge,
            "run_known_tests",
            return_value={
                "tests_executed": ["npm test"],
                "test_commands": ["/usr/local/bin/npm test --silent"],
                "test_exit_codes": [0],
                "tests_passed": True,
                "test_stdout_summary": "ok",
                "test_stderr_summary": "",
                "test_duration_ms": 9,
            },
        ) as run_tests:
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        invoke.assert_not_called()
        run_tests.assert_called_once()
        self.assertEqual("SKIP_ALREADY_APPLIED", result["developer_action"])
        self.assertFalse(result["developer_write_required"])
        self.assertFalse(result["cursor_cli_invoked"])
        self.assertFalse(result["implementation_applied"])
        self.assertEqual([], result["changed_files"])
        self.assertFalse(result["diff_present"])
        self.assertTrue(result["recovery_verification"]["eligible"])
        self.assertEqual("113", result["recovery_verification"]["prior_execution_id"])
        self.assertEqual("PRIOR_APPLY_VERIFICATION", result["recovery_verification"]["mode"])
        self.assertEqual(["npm test"], result["tests_executed"])
        self.assertEqual([0], result["test_exit_codes"])
        self.assertTrue(result["tests_passed"])
        self.assertIsNone(result["failure_class"])
        self.assertTrue(result["ok"])
        self.assertEqual("COMPLETED", result["status"])
        self.assertFalse(self.bridge.intent_path("PROJECT-HANGMAN-PILOT-001", "W001").exists())

    def test_consume_intent_survives_unlink_permission_error(self):
        self.bridge.write_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W001")
        path = self.bridge.intent_path("PROJECT-HANGMAN-PILOT-001", "W001")
        self.assertTrue(path.is_file())
        with mock.patch.object(self.bridge.Path, "unlink", side_effect=PermissionError("Operation not permitted")):
            consumed = self.bridge.consume_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W001")
        self.assertEqual("PRIOR_APPLY_VERIFICATION", consumed["mode"])
        self.assertEqual("W001", consumed["work_unit_id"])

    def test_receipts_never_use_downloads(self):
        downloads = Path.home() / "Downloads" / "n8n-self-hosted-ai" / "forbidden-receipts"
        self.bridge.RECEIPTS_DIR = downloads
        relocated = self.bridge.receipts_dir()
        self.assertFalse(self.bridge.path_is_under_downloads(relocated))
        self.assertEqual(self.bridge.DEFAULT_RECEIPTS_DIR, relocated)
        self.bridge.RECEIPTS_DIR = Path(self.temp.name) / "receipts"
        self.assertEqual(self.bridge.RECEIPTS_DIR, self.bridge.receipts_dir())

    def test_missing_intent_does_not_raise(self):
        self.assertIsNone(self.bridge.consume_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W001"))

    def _lineage(self, workspace, provenance):
        return self.bridge.evaluate_recovery_lineage(
            workspace,
            "PROJECT-HANGMAN-PILOT-001",
            "W001",
            provenance,
        )

    def test_recovery_lineage_ignores_unrelated_outer_git_head(self):
        workspace = self._seed_committed_hangman()
        provenance = self.bridge.load_applied_provenance("PROJECT-HANGMAN-PILOT-001", "W001")
        outer = Path(self.temp.name) / "factory-repo"
        outer.mkdir()
        self.bridge.git(outer, "init", check=True)
        (outer / "infra.txt").write_text("unrelated factory commit\n", encoding="utf-8")
        self.bridge.git(outer, "add", "-A")
        self.bridge.git(
            outer,
            "-c",
            "user.email=factory@local",
            "-c",
            "user.name=Factory",
            "commit",
            "-m",
            "unrelated infrastructure",
            check=True,
        )
        outer_head = self.bridge.git_head(outer)
        hangman_head = self.bridge.git_head(workspace)
        self.assertNotEqual(outer_head, hangman_head)
        self.assertEqual(hangman_head, provenance["git_head"])
        matched, reason = self._lineage(workspace, provenance)
        self.assertTrue(matched)
        self.assertIsNone(reason)

    def test_recovery_lineage_blocks_altered_app_js(self):
        workspace = self._seed_committed_hangman()
        provenance = self.bridge.load_applied_provenance("PROJECT-HANGMAN-PILOT-001", "W001")
        (workspace / "app.js").write_text('"use strict";\nmutated\n', encoding="utf-8")
        matched, reason = self._lineage(workspace, provenance)
        self.assertFalse(matched)
        self.assertEqual("unrelated_workspace_mutation", reason)

    def test_recovery_lineage_blocks_altered_package_json(self):
        workspace = self._seed_committed_hangman()
        provenance = self.bridge.load_applied_provenance("PROJECT-HANGMAN-PILOT-001", "W001")
        (workspace / "package.json").write_text('{"name":"mutated"}\n', encoding="utf-8")
        matched, reason = self._lineage(workspace, provenance)
        self.assertFalse(matched)
        self.assertEqual("unrelated_workspace_mutation", reason)

    def test_recovery_lineage_blocks_different_project_provenance(self):
        workspace = self._seed_committed_hangman()
        self.bridge.write_recovery_intent("PROJECT-OTHER", "W001")
        recovery = self.bridge.attempt_recovery_verification(workspace, "PROJECT-OTHER", "W001")
        self.assertFalse(recovery["eligible"])
        self.assertEqual("prior_provenance_missing", recovery["reason"])
        self.assertIsNone(self.bridge.load_applied_provenance("PROJECT-OTHER", "W001"))

    def test_recovery_lineage_blocks_different_work_unit_provenance(self):
        self._seed_committed_hangman()
        self.assertIsNone(self.bridge.load_applied_provenance("PROJECT-HANGMAN-PILOT-001", "W002"))
        self.bridge.write_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W002")
        recovery = self.bridge.attempt_recovery_verification(
            self.bridge.trusted_workspace("PROJECT-HANGMAN-PILOT-001"),
            "PROJECT-HANGMAN-PILOT-001",
            "W002",
        )
        self.assertFalse(recovery["eligible"])
        self.assertEqual("prior_provenance_missing", recovery["reason"])

    def test_content_hashes_detect_uncommitted_tracked_mutation(self):
        workspace = self._seed_committed_hangman()

        def fake_invoke(ws, _prompt):
            (ws / "app.js").write_text('"use strict";\nchanged\n', encoding="utf-8")
            (ws / "styles.css").write_text("body{}\n", encoding="utf-8")
            return 0, '{"result":"no git commit"}', "no git commit", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke), mock.patch.object(
            self.bridge,
            "run_known_tests",
            return_value=self.bridge.empty_test_evidence(),
        ):
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        self.assertTrue(result["implementation_applied"])
        self.assertEqual(["app.js", "styles.css"], result["changed_files"])

    def test_content_hashes_survive_transient_commit_and_head_restore(self):
        workspace = self._seed_committed_hangman()
        original_head = self.bridge.git_head(workspace)
        restored = {}

        def fake_invoke(ws, _prompt):
            (ws / "app.js").write_text('"use strict";\ntransient\n', encoding="utf-8")
            self.bridge.git(ws, "add", "-A")
            self.bridge.git(
                ws,
                "-c",
                "user.email=factory@local",
                "-c",
                "user.name=Factory",
                "commit",
                "-m",
                "transient",
            )
            self.bridge.git(ws, "reset", "--mixed", "HEAD~1")
            restored["head"] = self.bridge.git_head(ws)
            return 0, '{"result":"reset"}', "reset", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke), mock.patch.object(
            self.bridge,
            "run_known_tests",
            return_value=self.bridge.empty_test_evidence(),
        ):
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        self.assertEqual(original_head, restored["head"])
        self.assertTrue(result["implementation_applied"])
        self.assertIn("app.js", result["changed_files"])
        self.assertTrue(result["diff_present"])

    def test_outside_workspace_write_is_not_w001_apply(self):
        outside = Path(self.temp.name) / "evil.txt"

        def fake_invoke(_ws, _prompt):
            outside.write_text("not in hangman\n", encoding="utf-8")
            return 0, '{"result":"wrote outside"}', "wrote outside", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke):
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        self.assertTrue(outside.is_file())
        self.assertFalse(result["implementation_applied"])
        self.assertEqual([], result["changed_files"])
        self.assertEqual("CURSOR_NO_APPLIED_CHANGE", result["failure_class"])

    def test_git_commands_ignore_inherited_git_dir(self):
        workspace = self._seed_committed_hangman()
        (workspace / "app.js").write_text('"use strict";\nmutated\n', encoding="utf-8")
        outer = Path(self.temp.name) / "outer-repo"
        outer.mkdir()
        self.bridge.git(outer, "init")
        with mock.patch.dict(
            os.environ,
            {"GIT_DIR": str((outer / ".git").resolve()), "GIT_WORK_TREE": str(outer.resolve())},
        ):
            self.assertIn("app.js", self.bridge.porcelain_files(workspace))
            self.assertEqual(self.bridge.git_head(workspace), json.loads(
                self.bridge.provenance_path("PROJECT-HANGMAN-PILOT-001", "W001").read_text(encoding="utf-8")
            )["git_head"])

    def test_recovery_lineage_blocks_missing_provenance(self):
        workspace = self.bridge.trusted_workspace("PROJECT-HANGMAN-PILOT-001")
        self.bridge.ensure_repo(workspace)
        self.bridge.write_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W001")
        recovery = self.bridge.attempt_recovery_verification(
            workspace,
            "PROJECT-HANGMAN-PILOT-001",
            "W001",
        )
        self.assertFalse(recovery["eligible"])
        self.assertEqual("prior_provenance_missing", recovery["reason"])

    def test_no_provenance_uses_developer_write(self):
        def fake_invoke(_workspace, _prompt):
            return 0, '{"result":"new work"}', "new work", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke) as invoke:
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        invoke.assert_called_once()
        self.assertEqual("WRITE", result["developer_action"])
        self.assertTrue(result["developer_write_required"])

    def test_remediation_intent_uses_developer_even_when_trusted(self):
        self._seed_committed_hangman()
        self.bridge.write_recovery_intent(
            "PROJECT-HANGMAN-PILOT-001",
            "W001",
            remediation_requested=True,
        )

        def fake_invoke(_workspace, _prompt):
            return 0, '{"result":"remediate"}', "remediate", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke) as invoke:
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        invoke.assert_called_once()
        self.assertEqual("WRITE", result["developer_action"])

    def test_skip_does_not_authorize_tests_when_file_mutated(self):
        workspace = self._seed_committed_hangman()
        self.bridge.write_recovery_intent("PROJECT-HANGMAN-PILOT-001", "W001")
        (workspace / "app.js").write_text('"use strict";\nstale\n', encoding="utf-8")

        def fake_invoke(_workspace, _prompt):
            return 0, '{"result":"mismatch"}', "mismatch", None

        with mock.patch.object(self.bridge, "invoke_cursor", side_effect=fake_invoke) as invoke, mock.patch.object(
            self.bridge,
            "run_known_tests",
        ) as run_tests:
            result = self.bridge.execute_task(self.bridge.validate_payload(self.payload))
        invoke.assert_called_once()
        run_tests.assert_not_called()
        self.assertNotEqual("SKIP_ALREADY_APPLIED", result["developer_action"])
        self.assertEqual([], result["tests_executed"])


if __name__ == "__main__":
    unittest.main()
