"""Tests for ``controller.protocol``
(``docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md``, Design A,
CP1).

Most tests replace only the execution step (``workflow_contract._execute_query``)
so the managed-set read, the private copy and the Git preparation still run
first; the round-trip tests run the real vendored Workflow 2.7.0 scripts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import protocol, workflow_contract  # noqa: E402
from controller.errors import (  # noqa: E402
    WorkflowProtocolFailedError,
    WorkflowProtocolRefusedError,
    WorkflowProtocolUnsupportedError,
    WorkflowReleaseChangedError,
)
from tests import fixtures  # noqa: E402
from tests.test_protocol_schema import DECISION, envelope, refusal  # noqa: E402


def managed_of(*keys: str) -> dict:
    return {key: {"sha256": "0" * 64, "executable": False} for key in keys}


DESCRIBE = {"workflow_release": "2.7.0", "protocol_version": "1.0", "supported_protocol_majors": [1],
            "supported_governing_versions": ["2.2"],
            "capabilities": {name: [] for name in ("operations", "dispositions", "action_ids", "artifact_kinds",
                                                   "external_result_kinds", "reserved_result_kinds", "error_codes")}}


def completed(stdout: object, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout) + "\n"
    return subprocess.CompletedProcess([], returncode, text.encode(), stderr.encode())


class QualifyingKeysTest(unittest.TestCase):
    def test_only_one_segment_scripts_py_files_qualify(self) -> None:
        managed = managed_of(
            "scripts/workflow_protocol.py", "scripts/workflow_state.py", "scripts/workflow_state_test.py",
            "scripts/workflow_protocol_test.py", "scripts/prepare-ai-review.sh", "scripts/pkg/mod.py",
            "scripts/../x.py", "/scripts/a.py", "scripts/", "scripts/.py", "scripts/a/../b.py", "other/a.py",
            ".claude/commands/x.md", "scripts/nul\x00.py", "scripts/a.pyc",
        )
        self.assertEqual(protocol.qualifying_script_keys(managed),
                         ["scripts/workflow_protocol.py", "scripts/workflow_state.py"])

    def test_an_absent_or_non_object_managed_lists_nothing(self) -> None:
        for managed in (None, [], "scripts/workflow_protocol.py", 3, ["scripts/workflow_protocol.py"]):
            with self.subTest(managed=managed):
                self.assertEqual(protocol.qualifying_script_keys(managed), [])
                self.assertFalse(protocol.protocol_script_listed(managed))
        self.assertFalse(protocol.protocol_script_listed(managed_of("scripts/workflow_state.py")))
        self.assertTrue(protocol.protocol_script_listed(managed_of("scripts/workflow_protocol.py")))


class _TargetCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "target"
        self.root.mkdir()
        fixtures.git_init(self.root)
        fixtures.install_workflow_release(self.root, "2.7.0")
        fixtures.commit_all(self.root, "install")

    def record(self) -> dict:
        return json.loads((self.root / ".workflow-manager" / "installation.json").read_text())

    def write_record(self, record: dict) -> None:
        (self.root / ".workflow-manager" / "installation.json").write_text(json.dumps(record))

    def fake(self, result: subprocess.CompletedProcess, seen: list | None = None):
        def execute(argv, *, cwd, env, timeout):
            if seen is not None:
                seen.append({"argv": argv, "cwd": cwd, "copied": sorted(p.name for p in Path(argv[4]).parent.iterdir())})
            return result
        return mock.patch.object(workflow_contract, "_execute_query", execute)


class NoGlobalGitIdentityTest(unittest.TestCase):
    def test_scratch_repositories_commit_without_a_global_or_system_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
                os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
            for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                         "EMAIL"):
                os.environ.pop(name, None)
            root = Path(td) / "plain"
            fixtures.git_init(root)
            (root / "a").write_text("a")
            self.assertEqual(len(fixtures.commit_all(root, "plain")), 40)
            fixtures.seed_workflow_item(Path(td) / "item", "2.7.0", "publish", work_item_id="demo")


class ScriptSetTest(_TargetCase):
    def test_every_managed_script_is_digested_and_tests_are_not(self) -> None:
        scripts = protocol.script_set(self.root, self.record()["managed"])
        self.assertEqual(sorted(scripts.digests), [
            "scripts/workflow_fingerprint.py", "scripts/workflow_protocol.py",
            "scripts/workflow_state.py", "scripts/workflow_test_harness.py"])
        for key, digest in scripts.digests.items():
            self.assertEqual(digest, fixtures.workflow_release_files("2.7.0")[key]["sha256"])

    def test_a_file_the_record_does_not_list_is_neither_copied_nor_digested_nor_refused(self) -> None:
        (self.root / "scripts" / "extra.py").write_text("print('product')\n")
        (self.root / "scripts" / "link.py").symlink_to("extra.py")
        (self.root / "scripts" / "pkg").mkdir()
        (self.root / "scripts" / "pkg" / "mod.py").write_text("x = 1\n")
        seen: list = []
        with self.fake(completed(envelope("describe", DESCRIBE), 0), seen):
            result = protocol.run(self.root, "describe")
        self.assertEqual(sorted(result.digests), sorted(protocol.script_set(self.root, self.record()["managed"]).digests))
        self.assertEqual(seen[0]["copied"], ["workflow_fingerprint.py", "workflow_protocol.py",
                                             "workflow_state.py", "workflow_test_harness.py"])

    def test_a_managed_symlink_is_refused_naming_it(self) -> None:
        path = self.root / "scripts" / "workflow_state.py"
        path.unlink()
        (self.root / "scripts" / "real.txt").write_text("x")
        path.symlink_to("real.txt")
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            protocol.run(self.root, "describe")
        self.assertEqual(raised.exception.evidence["reason"], "protocol_script_modified")
        self.assertEqual(raised.exception.evidence["path"], "scripts/workflow_state.py")
        self.assertIn("scripts/workflow_state.py", raised.exception.message)

    def test_a_missing_managed_file_and_a_directory_are_refused_naming_it(self) -> None:
        (self.root / "scripts" / "workflow_test_harness.py").unlink()
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            protocol.script_set(self.root, self.record()["managed"])
        self.assertEqual(raised.exception.evidence["path"], "scripts/workflow_test_harness.py")
        (self.root / "scripts" / "workflow_test_harness.py").mkdir()
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            protocol.script_set(self.root, self.record()["managed"])
        self.assertIn("regular file", raised.exception.message)

    def test_a_nested_or_traversal_key_is_never_copied_or_written_outside(self) -> None:
        record = self.record()
        record["managed"]["scripts/pkg/mod.py"] = {"sha256": "0" * 64, "executable": False}
        record["managed"]["scripts/../escape.py"] = {"sha256": "0" * 64, "executable": False}
        self.write_record(record)
        (self.root / "escape.py").write_text("print('x')\n")
        seen: list = []
        before = set(Path(tempfile.gettempdir()).iterdir())
        with self.fake(completed(envelope("describe", DESCRIBE)), seen):
            result = protocol.run(self.root, "describe")
        self.assertNotIn("scripts/pkg/mod.py", result.digests)
        self.assertNotIn("scripts/../escape.py", result.digests)
        self.assertNotIn("mod.py", seen[0]["copied"])
        self.assertNotIn("escape.py", seen[0]["copied"])
        leftovers = [p for p in set(Path(tempfile.gettempdir()).iterdir()) - before
                     if p.name.startswith(workflow_contract._PRIVATE_DIR_PREFIX)]
        self.assertEqual(leftovers, [])

    def test_an_absent_or_non_object_managed_field_is_no_protocol(self) -> None:
        for managed in (None, ["scripts/workflow_protocol.py"], "x"):
            with self.subTest(managed=managed):
                record = self.record()
                if managed is None:
                    del record["managed"]
                else:
                    record["managed"] = managed
                self.write_record(record)
                self.assertIsNone(protocol.read_managed(self.root))
                with self.assertRaises(WorkflowProtocolFailedError) as raised:
                    protocol.run(self.root, "describe")
                self.assertEqual(raised.exception.evidence["reason"], "no_protocol")

    def test_a_release_without_the_protocol_script_is_no_protocol(self) -> None:
        record = self.record()
        del record["managed"]["scripts/workflow_protocol.py"]
        self.write_record(record)
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            protocol.run(self.root, "describe")
        self.assertEqual(raised.exception.evidence["reason"], "no_protocol")

    def test_an_unreadable_record_is_refused_not_read_as_absent(self) -> None:
        (self.root / ".workflow-manager" / "installation.json").write_text("{not json")
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            protocol.read_managed(self.root)
        self.assertEqual(raised.exception.evidence["reason"], "protocol_installation_unreadable")

    def test_the_identity_is_derived_afresh_for_every_operation(self) -> None:
        describe = envelope("describe", DESCRIBE)
        with self.fake(completed(describe)):
            first = protocol.run(self.root, "describe")
            (self.root / "scripts" / "workflow_state.py").write_bytes(b"# edited\n")
            second = protocol.run(self.root, "describe")
        self.assertNotEqual(first.digests["scripts/workflow_state.py"], second.digests["scripts/workflow_state.py"])
        self.assertEqual(first.digests["scripts/workflow_protocol.py"], second.digests["scripts/workflow_protocol.py"])

    def test_expected_digests_are_compared_with_the_bytes_copied_for_execution(self) -> None:
        describe = envelope("describe", DESCRIBE)
        with self.fake(completed(describe)):
            expected = dict(protocol.run(self.root, "describe").digests)
        seen: list = []
        with self.fake(completed(describe), seen):
            protocol.run(self.root, "describe", expected_digests=expected)
            self.assertEqual(len(seen), 1)
            (self.root / "scripts" / "workflow_state.py").write_bytes(b"# replaced after the identity check\n")
            with self.assertRaises(WorkflowReleaseChangedError) as raised:
                protocol.run(self.root, "describe", expected_digests=expected)
        self.assertEqual(len(seen), 1, "nothing ran under bytes other than the expected ones")
        self.assertEqual(raised.exception.evidence["changed_scripts"], ["scripts/workflow_state.py"])

    def test_the_command_line_names_the_major_and_the_repository(self) -> None:
        seen: list = []
        with self.fake(completed(refusal(), 3), seen):
            protocol.run(self.root, "next-action", ["--work-item", "wi"])
        argv = seen[-1]["argv"]
        self.assertEqual(argv[:4], [sys.executable, "-B", "-E", "-s"])
        self.assertEqual(argv[5:], ["--protocol-major", "1", "--repo-root", str(self.root), "next-action",
                                    "--work-item", "wi"])
        self.assertEqual(seen[-1]["cwd"], self.root)


class EnvelopeHandlingTest(_TargetCase):
    def run_with(self, result: subprocess.CompletedProcess, operation: str = "next-action"):
        with self.fake(result):
            return protocol.run(self.root, operation)

    def test_an_ok_answer_and_a_refusal_are_both_envelopes(self) -> None:
        ok = self.run_with(completed(envelope()))
        self.assertTrue(ok.ok)
        self.assertEqual((ok.operation, ok.workflow_release, ok.protocol_version), ("next-action", "2.7.0", "1.0"))
        refused = self.run_with(completed(refusal("stale_decision"), 3))
        self.assertFalse(refused.ok)
        self.assertEqual((refused.error.code, refused.error.retryable), ("stale_decision", True))

    def test_exit_status_and_ok_must_agree(self) -> None:
        for result in (completed(envelope(), 3), completed(refusal(), 0), completed(envelope(), 1)):
            with self.subTest(returncode=result.returncode):
                with self.assertRaises(WorkflowProtocolFailedError) as raised:
                    self.run_with(result)
                self.assertEqual(raised.exception.evidence["reason"], "protocol_envelope_invalid")

    def test_each_error_code_has_its_own_exit_status(self) -> None:
        self.assertEqual(self.run_with(completed(refusal("invalid_request"), 2)).error.code, "invalid_request")
        self.assertEqual(self.run_with(completed(refusal("internal_error"), 1)).error.code, "internal_error")
        for code, status in (("invalid_request", 3), ("internal_error", 3), ("stale_decision", 2), ("refused", 0)):
            with self.subTest(code=code, status=status):
                with self.assertRaises(WorkflowProtocolFailedError):
                    self.run_with(completed(refusal(code), status))

    def test_an_unknown_error_code_is_a_refusal_carrying_it(self) -> None:
        self.assertEqual(self.run_with(completed(refusal("a_future_code"), 3)).error.code, "a_future_code")

    def test_two_documents_or_none_or_garbage_are_no_document(self) -> None:
        two = json.dumps(envelope()) + "\n" + json.dumps(envelope()) + "\n"
        for stdout in (two, "", "not json", "[1]\n"):
            with self.subTest(stdout=stdout[:12]):
                with self.assertRaises(WorkflowProtocolFailedError) as raised:
                    self.run_with(completed(stdout))
                self.assertIn(raised.exception.evidence["reason"], ("protocol_no_document", "protocol_envelope_invalid"))

    def test_the_operation_must_be_the_one_asked(self) -> None:
        with self.assertRaisesRegex(WorkflowProtocolFailedError, "answers 'verify'"):
            self.run_with(completed(envelope("verify", {"healthy": True, "checks": []})))

    def test_a_result_that_fails_the_schema_is_invalid(self) -> None:
        bad = dict(DECISION, disposition=5)
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            self.run_with(completed(envelope(result=bad)))
        self.assertEqual(raised.exception.evidence["reason"], "protocol_envelope_invalid")
        no_result = envelope()
        del no_result["result"]
        with self.assertRaises(WorkflowProtocolFailedError):
            self.run_with(completed(no_result))

    def test_a_refusal_without_error_is_invalid(self) -> None:
        body = refusal()
        del body["error"]
        with self.assertRaises(WorkflowProtocolFailedError):
            self.run_with(completed(body, 3))

    def test_protocol_major_two_is_unsupported(self) -> None:
        body = envelope()
        body["protocol"]["version"] = "2.0"
        with self.assertRaises(WorkflowProtocolUnsupportedError) as raised:
            self.run_with(completed(body))
        self.assertEqual(raised.exception.code, "WORKFLOW_PROTOCOL_UNSUPPORTED")

    def test_an_unsupported_protocol_answer_is_unsupported(self) -> None:
        with self.assertRaises(WorkflowProtocolUnsupportedError):
            self.run_with(completed(refusal("unsupported_protocol"), 3))

    def test_a_minor_extension_is_accepted_end_to_end(self) -> None:
        body = envelope()
        body["protocol"]["version"] = "1.1"
        body["extra"] = 1
        body["result"]["action"]["id"] = "plan.future"
        decision = protocol.parse_decision(self.run_with(completed(body)).result)
        self.assertEqual(decision.action.id, "plan.future")

    def test_a_missing_imported_module_is_named_from_the_last_stderr_line(self) -> None:
        stderr = ("Traceback (most recent call last):\n  File \"x.py\", line 41, in <module>\n"
                  "    import workflow_gate_policy\nModuleNotFoundError: No module named 'workflow_gate_policy'\n")
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            self.run_with(completed("", 1, stderr))
        self.assertEqual(raised.exception.evidence["reason"], "protocol_no_document")
        self.assertEqual(raised.exception.evidence["missing_module"], "workflow_gate_policy")
        self.assertIn("workflow_gate_policy", raised.exception.message)

    def test_any_other_start_failure_is_the_bare_failure_plus_the_stderr_tail(self) -> None:
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            self.run_with(completed("", 1, "Traceback (most recent call last):\nSyntaxError: invalid syntax\n"))
        self.assertNotIn("missing_module", raised.exception.evidence)
        self.assertIn("exited 1", raised.exception.message)
        self.assertIn("SyntaxError: invalid syntax", raised.exception.message)

    def test_a_missing_sibling_is_named_when_the_real_script_runs_without_it(self) -> None:
        record = self.record()
        del record["managed"]["scripts/workflow_fingerprint.py"]
        self.write_record(record)
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            protocol.describe(self.root)
        self.assertEqual(raised.exception.evidence["missing_module"], "workflow_fingerprint")


class TypedOperationsTest(_TargetCase):
    def test_a_refusal_raises_a_refused_error_carrying_the_code(self) -> None:
        with self.fake(completed(refusal("stale_decision"), 3)):
            with self.assertRaises(WorkflowProtocolRefusedError) as raised:
                protocol.next_action(self.root, "wi", expect_state_identity="f" * 64)
        self.assertEqual(raised.exception.refusal["code"], "stale_decision")
        self.assertEqual(raised.exception.code, "WORKFLOW_PROTOCOL_FAILED")

    def test_next_action_passes_its_arguments(self) -> None:
        seen: list = []
        with self.fake(completed(envelope()), seen):
            decision = protocol.next_action(self.root, "wi", expect_state_identity="f" * 64)
        self.assertEqual(seen[0]["argv"][-4:], ["--work-item", "wi", "--expect-state-identity", "f" * 64])
        self.assertEqual((decision.row, decision.disposition, decision.action.id), ("7", "automatic", "plan.author"))
        self.assertEqual(decision.basis.state_identity, "a" * 64)
        self.assertEqual(decision.action.worker.role, "planner")

    def test_next_action_without_a_work_item_asks_for_none(self) -> None:
        seen: list = []
        with self.fake(completed(envelope()), seen):
            protocol.next_action(self.root)
        self.assertEqual(seen[0]["argv"][-1], "next-action")

    def test_reconcile_hands_over_the_decision_file(self) -> None:
        result = {"class": "progress", "from": {"phase": "PLANNING", "state_identity": "a"},
                  "to": None, "evidence": {"completed_checkpoints": [], "started_checkpoints": [],
                                           "recorded_stage": None},
                  "invalid_reasons": [{"code": "illegal_edge", "text": "t"}], "basis": None, "next": None}
        seen: list = []
        with self.fake(completed(envelope("reconcile", result)), seen):
            outcome = protocol.reconcile(self.root, Path("/x/decision.json"), "wi")
        self.assertEqual(seen[0]["argv"][-4:], ["--decision", "/x/decision.json", "--work-item", "wi"])
        self.assertEqual((outcome.outcome, outcome.from_.phase, outcome.to), ("progress", "PLANNING", None))
        self.assertEqual(outcome.invalid_reasons, (("illegal_edge", "t"),))


class RealScriptsTest(unittest.TestCase):
    """The vendored Workflow 2.7.0 scripts, run through the client."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "target"
        fixtures.seed_workflow_item(cls.root, "2.7.0", "route", work_item_id="demo")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_describe(self) -> None:
        described = protocol.describe(self.root)
        self.assertEqual((described.workflow_release, described.protocol_version), ("2.7.0", "1.0"))
        self.assertEqual(described.supported_protocol_majors, (1,))
        self.assertIn("plan.author", described.capabilities["action_ids"])

    def test_verify_reports_its_checks(self) -> None:
        verified = protocol.verify(self.root)
        self.assertEqual({c.id for c in verified.checks}, {
            "state_readable", "state_valid", "config_valid", "active_item_resolvable",
            "checkpoint_completions_provable", "installation_release_matches", "protocol_ready"})
        self.assertEqual(next(c for c in verified.checks if c.id == "installation_release_matches").status, "pass")

    def test_next_action_and_the_stale_check(self) -> None:
        decision = protocol.next_action(self.root, "demo")
        self.assertEqual((decision.disposition, decision.action.id), ("automatic", "plan.author"))
        again = protocol.next_action(self.root, "demo", expect_state_identity=decision.basis.state_identity)
        self.assertEqual(again.raw, decision.raw)
        with self.assertRaises(WorkflowProtocolRefusedError) as raised:
            protocol.next_action(self.root, "demo", expect_state_identity="0" * 64)
        self.assertEqual(raised.exception.refusal["code"], "stale_decision")

    def test_an_unknown_work_item_is_a_refusal(self) -> None:
        with self.assertRaises(WorkflowProtocolRefusedError) as raised:
            protocol.next_action(self.root, "nope")
        self.assertEqual(raised.exception.refusal["code"], "unknown_work_item")

    def test_the_target_is_not_written(self) -> None:
        before = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=self.root,
                                capture_output=True, text=True).stdout
        protocol.next_action(self.root, "demo")
        protocol.verify(self.root)
        after = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=self.root,
                               capture_output=True, text=True).stdout
        self.assertEqual(before, after)
        self.assertEqual(sorted(self.root.rglob("__pycache__")), [])


class IsolationTest(_TargetCase):
    """The ADR 0006 isolation, now for the protocol script."""

    def test_a_hook_the_runs_git_would_fire_is_refused_before_anything_runs(self) -> None:
        hook = self.root / ".git" / "hooks" / "post-index-change"
        hook.write_text("#!/bin/sh\ntouch hook-ran\n")
        hook.chmod(0o755)
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            protocol.describe(self.root)
        self.assertEqual(raised.exception.evidence["reason"], "protocol_git_not_isolated")
        self.assertFalse((self.root / "hook-ran").exists())

    def test_a_planted_module_in_the_targets_scripts_is_never_imported(self) -> None:
        (self.root / "scripts" / "uuid.py").write_text("raise SystemExit('planted')\n")
        self.assertEqual(protocol.describe(self.root).workflow_release, "2.7.0")

    def test_an_environment_python_path_is_ignored(self) -> None:
        planted = self.root / "plant"
        planted.mkdir()
        (planted / "json.py").write_text("raise SystemExit('planted')\n")
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(planted)}):
            self.assertEqual(protocol.describe(self.root).workflow_release, "2.7.0")

    def test_a_timeout_is_a_failure(self) -> None:
        def slow(argv, *, cwd, env, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        with mock.patch.object(workflow_contract, "_execute_query", slow):
            with self.assertRaises(WorkflowProtocolFailedError) as raised:
                protocol.describe(self.root, timeout=5)
        self.assertEqual(raised.exception.evidence["reason"], "protocol_timeout")


if __name__ == "__main__":
    unittest.main()
