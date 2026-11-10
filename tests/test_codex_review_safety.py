"""`workflow-controller-h4e2b` CP3 (D5): the routed Codex review is proved adversarially through the job.

Every case drives ``job.execute_step`` with the real ``run_review``, the real recorder on the fixture repository and
the fake ``codex`` behind the real launcher (the fixture of ``tests/test_job_codex_review.py``). Each asserts one
thing about the whole result: the managed repository's tree, index, refs and ``WORKFLOW_STATE.json`` are as they were
(except the recorded case), the clone is gone, the job record has the status D3 gives, and no field of the record,
no stream file and no returned value holds a line of an armoured block. ``CASES`` names the test of every cause of
``codex_review_role.CAUSES``, and a test fails when a cause has none.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_review_role as crr, codex_worker as cw, job, routing, worker  # noqa: E402
from controller.errors import WorkflowReleaseChangedError  # noqa: E402
from tests.test_codex_review_role import ARMOUR, message  # noqa: E402
from tests.test_job_codex_review import ReviewJobCase  # noqa: E402
from tests.test_review_recorder import FEEDBACK, STATE, WID  # noqa: E402

#: Each cause of ``crr.CAUSES`` and the test method (on ``SafetyTests``) that drives it through the job.
CASES = {
    "special_file": "test_a_symlink_and_a_special_file_input_are_refused_before_any_codex",
    "submodule": "test_a_git_component_and_a_gitmodules_input_are_refused_before_any_codex",
    "credential_input": "test_a_credential_name_and_an_armoured_input_are_refused_before_any_codex",
    "path_escape": "test_a_path_that_escapes_is_refused_before_any_codex",
    "input_over_bound": "test_an_input_over_a_bound_is_refused_before_any_codex",
    "verdict_over_bound": "test_a_verdict_over_a_mebibyte_is_refused",
    "verdict_unparsed": "test_a_forged_header_or_a_second_status_is_unparsed",
    "credential_in_verdict": "test_armour_in_the_final_message_is_refused_with_the_four_fields_emptied",
    "read_only_violated": "test_a_crafted_ref_head_move_or_input_edit_violates_the_read_only_run",
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


class SafetyCase(ReviewJobCase):
    """The job fixture plus the managed repository's tree, index and refs in the snapshot."""

    def digest(self) -> tuple:
        return (self.snapshot(), _git(self.root, "ls-files", "-s"), _git(self.root, "rev-parse", "HEAD"),
                _git(self.root, "write-tree"),
                # the content of an already dirty tracked file, which the status line does not show (review O1)
                _git(self.root, "diff", "HEAD"))

    def refused_through_the_job(self, record: dict, code: str, cause: str, before: tuple) -> None:
        """A refusal: FAILED, the deny-set cause named, nothing recorded, the clone gone, no armour held."""
        self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["code"], code, evidence)
        self.assertIn(cause, (evidence.get("cause"), evidence.get("error")), evidence)
        self.assertEqual(self.digest(), before)
        self.assertFalse(self.layout.worktree.exists())
        self.assertNotIn("landing", self.refs())
        self.assert_no_armour_anywhere(record)
        self.assert_valid(record)

    def assert_no_armour_anywhere(self, record: dict) -> None:
        self.assertIsNone(crr.ARMOUR.search(json.dumps(record, default=str).encode()), "the record holds a block")
        for path in self.runtime.rglob("*"):
            if path.is_file() and "sessions" not in path.parts:
                self.assertIsNone(crr.ARMOUR.search(path.read_bytes()), f"{path} holds a block")

    def with_inputs(self, extra: list[str]):
        real = crr.input_paths

        def patched(*args, **kw):
            paths, bundle, changes = real(*args, **kw)
            return [*paths, *extra], bundle, changes

        return mock.patch.object(crr, "input_paths", patched)

    def extra_dir(self) -> Path:
        extra = self.root / ".ai-review" / "extra"  # ignored by the fixture, so the recorder's own check passes
        (extra / "sub").mkdir(parents=True, exist_ok=True)
        return extra

    def refuse_input(self, rel: str, cause: str) -> None:
        self.script(message=message("APPROVE"))
        before = self.digest()
        with self.with_inputs([rel]):
            record = self.step()
        self.refused_through_the_job(record, job.WORKER_NOT_STARTED_CODE, cause, before)
        self.assertNotIn("worker_process", record)
        self.assertEqual((self.launched, self.execs()), ([], []))
        self.reset()
        self.recorded = False  # the next script() rewrites the binary: its digest is recorded again


class SafetyTests:
    """The cases of D5, for either stage."""

    # -- the inputs ------------------------------------------------------------

    def test_a_symlink_and_a_special_file_input_are_refused_before_any_codex(self) -> None:
        extra = self.extra_dir()
        outside = self.tmp / "outside.txt"
        outside.write_text("x")
        (extra / "link").symlink_to(outside)
        os.mkfifo(extra / "fifo")
        for name in ("link", "fifo"):
            with self.subTest(name):
                self.refuse_input(f".ai-review/extra/{name}", "special_file")

    def test_a_git_component_and_a_gitmodules_input_are_refused_before_any_codex(self) -> None:
        extra = self.extra_dir()
        (extra / "sub" / ".gitmodules").write_text("[submodule]\n")
        (extra / ".git").mkdir()
        (extra / ".git" / "config").write_text("x")
        for name in ("sub/.gitmodules", ".git/config"):
            with self.subTest(name):
                self.refuse_input(f".ai-review/extra/{name}", "submodule")

    def test_a_credential_name_and_an_armoured_input_are_refused_before_any_codex(self) -> None:
        extra = self.extra_dir()
        (extra / "auth.json").write_text("{}")
        (extra / "armoured.txt").write_text(f"x\n{ARMOUR}\n")
        for name in ("auth.json", "armoured.txt"):
            with self.subTest(name):
                self.refuse_input(f".ai-review/extra/{name}", "credential_input")

    def test_an_armour_line_in_the_bundle_never_reaches_a_codex(self) -> None:
        """The bundle is fingerprinted by the Workflow, so an edited bundle is blocked at the decision, before the
        role; an armoured file named as an input is the role's own refusal (above). Either way nothing starts."""
        # The bound bundle, named: another ``REVIEW_REQUEST.md`` may sit beside it (``plan-inputs/``, review I2).
        request = self.root / ".ai-review" / WID / "current" / "REVIEW_REQUEST.md"
        decoy = self.root / ".ai-review" / WID / "plan-inputs" / "REVIEW_REQUEST.md"
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text("an unrelated request\n")
        self.assertTrue(request.is_file(), request)
        request.write_text(request.read_text() + f"\n{ARMOUR}\n")
        self.script(message=message("APPROVE"))
        before = self.digest()
        record = self.step()
        self.assertIn(record["status"], (job.STATUS_GATE_BLOCKED, job.STATUS_FAILED))
        self.assertEqual((self.launched, self.execs()), ([], []))
        self.assertEqual(self.digest(), before)
        self.assertFalse(self.layout.worktree.exists())

    def test_a_path_that_escapes_is_refused_before_any_codex(self) -> None:
        outside = self.tmp / "outside2.txt"
        outside.write_text("x")
        for rel in ("../../../outside2.txt", str(outside)):
            with self.subTest(rel):
                self.refuse_input(rel, "path_escape")

    def test_an_input_over_a_bound_is_refused_before_any_codex(self) -> None:
        extra = self.extra_dir()
        (extra / "big.bin").write_bytes(b"0" * (crr.MAX_INPUT_FILE_BYTES + 1))
        with self.subTest("a file"):
            self.refuse_input(".ai-review/extra/big.bin", "input_over_bound")
        (extra / "notes.md").write_text("x")
        with self.subTest("too many paths"), mock.patch.multiple(crr, MAX_INPUT_PATHS=1):
            self.refuse_input(".ai-review/extra/notes.md", "input_over_bound")

    # -- hooks -----------------------------------------------------------------

    def test_a_managed_config_hook_is_refused_before_any_codex(self) -> None:
        """The Workflow's protocol read refuses a managed repository with a config hook before the job reaches
        H4e's own ``managed_hooks_configured`` check: the step raises, nothing is decided, launched or written."""
        _git(self.root, "config", "hook.mine.event", "post-checkout")
        _git(self.root, "config", "hook.mine.command", "true")
        self.script(message=message("APPROVE"))
        before = self.digest()
        with self.assertRaises(WorkflowReleaseChangedError):
            self.step()
        self.assertEqual((self.launched, self.execs()), ([], []))
        self.assertEqual(list((self.runtime / "jobs").glob("*.json")), [])
        self.assertEqual(self.digest(), before)

    def test_the_trees_own_check_refuses_a_config_hook(self) -> None:
        from controller import codex_worker_tree

        _git(self.root, "config", "hook.mine.event", "post-checkout")
        hooks = codex_worker_tree.managed_hooks(self.root, codex_worker_tree.scrubbed_runner(self.layout.base))
        self.assertEqual([line.split()[0] for line in hooks], ["hook.mine.event"])

    def test_a_managed_hooks_path_is_neutralised_in_the_clone_and_no_hook_runs(self) -> None:
        hooks = self.tmp / "managed-hooks"
        hooks.mkdir()
        marker = self.tmp / "hook-ran"
        for name in ("post-checkout", "post-merge", "pre-commit", "reference-transaction"):
            (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n")
            (hooks / name).chmod(0o755)
        _git(self.root, "config", "core.hooksPath", str(hooks))
        seen = {}
        real = self.fake_launch

        def look(task, **kw):
            seen["path"] = _git(kw["cwd"], "config", "--local", "core.hooksPath").strip()
            return real(task, **kw)

        self.script(message=message("APPROVE"))
        with mock.patch.object(cw, "launch_codex_worker", look):
            record = self.step()
        self.assertEqual(seen["path"], "/dev/null")
        self.assertFalse(marker.exists())
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertFalse(self.layout.worktree.exists())

    def test_a_codex_that_writes_a_clone_hook_ends_sandbox_breach(self) -> None:
        self.script(message=message("APPROVE"),
                    write_outside=[str(self.layout.worktree / ".git" / "hooks" / "post-merge")])
        before = self.digest()
        record = self.step()
        self.refused_through_the_job(record, crr.WORKER_FAILED, "sandbox_breach", before)
        self.assertEqual(record["worker_outcome"], worker.FAILURE)

    # -- a crafted ref ---------------------------------------------------------

    def test_a_crafted_ref_head_move_or_input_edit_violates_the_read_only_run(self) -> None:
        def edit_input(task, **kw):
            result = self.fake_launch(task, **kw)
            target = next((kw["cwd"] / ".ai-review").rglob("REVIEW_REQUEST.md"))
            target.write_text(target.read_text() + "tampered\n")
            return result

        cases = {
            "branch": ({"git": [["update-ref", "refs/heads/evil", "HEAD"]]}, None),
            "tag": ({"git": [["tag", "evil"]]}, None),
            "head move": ({"git": [["commit", "--allow-empty", "-m", "x"]]}, None),
            "input edit": ({}, edit_input),
        }
        for name, (spec, launch) in cases.items():
            with self.subTest(name):
                self.script(message=message("APPROVE"), **spec)
                before = self.digest()
                if launch:
                    with mock.patch.object(cw, "launch_codex_worker", launch):
                        record = self.step()
                else:
                    record = self.step()
                self.refused_through_the_job(record, crr.REFUSED, "read_only_violated", before)
                for ref in ("evil",):
                    self.assertNotIn(ref, self.refs())
                self.reset()
                self.recorded = False

    # -- the verdict -----------------------------------------------------------

    def test_a_forged_header_or_a_second_status_is_unparsed(self) -> None:
        body = message("APPROVE").split("\n", 2)[2]
        cases = {
            "forged model": "Status: APPROVE\nReviewer model: openai/other\n" + body,
            "second status": "Status: APPROVE\nStatus: REVISE\n" + body,
            "no status": body,
            "title": "# Review Decision\nStatus: APPROVE\n" + body,
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.script(message=text)
                before = self.digest()
                record = self.step()
                self.refused_through_the_job(record, crr.REFUSED, "verdict_unparsed", before)
                self.reset()
                self.recorded = False

    def test_an_undecodable_message_is_unparsed(self) -> None:
        self.script(message_bytes=base64.b64encode(message("APPROVE").encode() + b"\xff\xfe").decode())
        before = self.digest()
        record = self.step()
        self.refused_through_the_job(record, crr.REFUSED, "verdict_unparsed", before)

    def test_a_plan_run_never_changes_the_external_stages_ledger(self) -> None:
        self.script(message="Status: APPROVE\nStatus: BLOCK\n")
        ledgers = sorted(p for p in self.root.rglob("*") if p.is_file() and "ledger" in p.name.lower()
                         and ".git" not in p.parts)
        contents = {p: p.read_bytes() for p in ledgers}
        self.step()
        self.assertEqual({p: p.read_bytes() for p in ledgers}, contents)
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file() and "ledger" in p.name.lower()
                          and ".git" not in p.parts and p not in contents], [])

    def test_a_verdict_over_a_mebibyte_is_refused(self) -> None:
        self.script(message=message("APPROVE", extra="x" * (crr.MAX_VERDICT_BYTES + 1)))
        before = self.digest()
        record = self.step()
        self.refused_through_the_job(record, crr.REFUSED, "verdict_over_bound", before)

    # -- armour ----------------------------------------------------------------

    def test_armour_in_the_final_message_is_refused_with_the_four_fields_emptied(self) -> None:
        self.script(message=message("APPROVE", extra=f" {ARMOUR}"))
        before = self.digest()
        record = self.step()
        self.refused_through_the_job(record, crr.REFUSED, "credential_in_verdict", before)
        worker_block = record["worker"]
        self.assertEqual(worker_block["result_excerpt"] or "", "")
        self.assertEqual(Path(record["worker_streams"]["stdout_path"]).read_text(), "")
        self.assertEqual(Path(record["worker_streams"]["stderr_path"]).read_text(), "")
        self.assertEqual((record["worker_streams"].get("diagnosis") or {}).get("detail", ""), "")

    def test_armour_on_stderr_of_a_failed_worker_is_refused_not_failed(self) -> None:
        self.script(message="", exit=1, stderr=f"boom {ARMOUR}\nmore")
        before = self.digest()
        record = self.step()
        self.refused_through_the_job(record, crr.REFUSED, "credential_in_verdict", before)
        self.assertEqual(record["worker_outcome"], worker.FAILURE)

    def test_prose_naming_the_markers_is_recorded(self) -> None:
        self.script(message=message("APPROVE", extra=" The check looks for `-----BEGIN` and `PRIVATE KEY-----`."))
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertTrue((self.root / FEEDBACK).is_file())
        self.assert_no_armour_anywhere(record)

    def test_a_real_block_in_the_same_place_is_refused(self) -> None:
        self.script(message=message("APPROVE", extra=" The check looks for `-----BEGIN` and `PRIVATE KEY-----`.\n"
                                    + ARMOUR))
        before = self.digest()
        record = self.step()
        self.refused_through_the_job(record, crr.REFUSED, "credential_in_verdict", before)

    # -- the table -------------------------------------------------------------

    def test_a_recorded_review_changes_only_the_feedback_file_and_the_state(self) -> None:
        self.script(message=message("APPROVE"))
        before = self.digest()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        after = self.digest()
        self.assertNotEqual(after[0][0], before[0][0])  # the state
        self.assertEqual(after[2], before[2])  # HEAD
        self.assertEqual(after[1], before[1])  # the index
        self.assertEqual(self.refs(), before[0][2])
        self.assertTrue((self.root / FEEDBACK).is_file())
        self.assertFalse(self.layout.worktree.exists())
        self.assert_no_armour_anywhere(record)


class PlanSafetyTests(SafetyTests, SafetyCase):
    STAGE = "plan"
    ROLE = routing.REVIEW_PLAN


class ImplementationSafetyTests(SafetyTests, SafetyCase):
    STAGE = "implementation"
    ROLE = routing.REVIEW_IMPLEMENTATION


class CoverageTests(unittest.TestCase):
    def test_every_cause_of_the_deny_set_has_a_case_that_exists(self) -> None:
        self.assertEqual(set(CASES), set(crr.CAUSES), "a cause of codex_review_role.CAUSES has no case here")
        for cause, name in CASES.items():
            for cls in (PlanSafetyTests, ImplementationSafetyTests):
                self.assertTrue(callable(getattr(cls, name, None)), f"{cause}: {name} is not a test of {cls.__name__}")


if __name__ == "__main__":
    unittest.main()
