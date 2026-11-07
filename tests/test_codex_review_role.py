"""`workflow-controller-h4e2` CP2: the read-only run of a local review role (`controller.codex_review_role`).

The worker is the scripted fake Codex behind the real launcher (as `tests/test_codex_worker.py` does); the managed
repository is the recorder tests' fixture (the installed Workflow scripts, a generated bundle), and the recorder is
the real one. A test whose point is a path the real launcher cannot take uses a stub `launch` seam.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_review_role as crr, codex_worker as cw, codex_worker_sandbox as ws  # noqa: E402
from controller import codex_worker_tree, protocol, review, review_recorder, worker  # noqa: E402
from controller.errors import ControllerError, WorkflowQueryError, WorkflowReleaseChangedError  # noqa: E402
from tests import fake_codex, fixtures  # noqa: E402
from tests.test_codex_worker import LaunchFixture  # noqa: E402
from tests.test_review_recorder import FEEDBACK, SECTIONS, STATE, WID, build_target  # noqa: E402

ARMOUR = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"


def message(status: str = "APPROVE", *, scope: str | None = None, extra: str = "") -> str:
    """A role's final message: the `Status:` line, an optional scope line and the eight sections."""
    lines = [f"Status: {status}"] + ([f"Review scope: {scope}"] if scope else []) + [""]
    body = {name: ["None."] for name in SECTIONS}
    if status == "REVISE":
        body["Important findings"] = ["- I1: The thing is wrong."]
    if status == "BLOCK":
        body["Blocking findings"] = ["- B1: The thing cannot stand."]
    body["Optional findings"] = ["- O1: A note for later." + extra]
    for name in SECTIONS:
        lines += [f"## {name}", "", *body[name], ""]
    return "\n".join(lines)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


class RoleCase(LaunchFixture):
    """A recorder fixture of ``STAGE`` restored before each test, beside the launcher fixture."""

    STAGE = "plan"
    _area: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._area = Path(tempfile.mkdtemp(prefix="role-"))
        cls._built = cls._area / "target"
        build_target(cls._built, cls.STAGE)
        cls._kept = cls._area / "kept"
        shutil.copytree(cls._built, cls._kept, symlinks=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._area, ignore_errors=True)

    def setUp(self) -> None:
        super().setUp()
        shutil.rmtree(self.worktree)  # the launcher fixture's own clone; the run makes its own
        shutil.rmtree(self._built, ignore_errors=True)
        shutil.copytree(self._kept, self._built, symlinks=True)
        self.root = self._built
        self.managed = types.SimpleNamespace(root=self.root)
        self.base = _git(self.root, "rev-parse", "HEAD").strip()
        self.events: list[dict] = []
        self.launched: list[dict] = []

    # -- helpers -------------------------------------------------------------

    def identity(self) -> str:
        return protocol.next_action(self.root, WID).basis.state_identity

    def seam(self, task, *, runtime_root, job_id, operator_home, cwd, timeout, model, effort, prompt, on_spawn=None):
        self.launched.append({"task": task, "prompt": prompt, "cwd": cwd})
        return cw.launch_codex_worker(
            task, runtime_root=runtime_root, job_id=job_id, operator_home=operator_home, cwd=cwd, timeout=timeout,
            on_spawn=on_spawn, model=model, effort=effort, prompt=prompt, binary_name=str(self.binary),
            system_dir=self.system, real_home=self.real_home, run=self.controls(job_id))

    def run_role(self, *, verified: bool = True, **over) -> crr.Ending:
        shutil.rmtree(self.layout.base, ignore_errors=True)  # a launch takes a fresh job
        if verified:
            self.record()
        kw = dict(runtime_root=self.runtime, job_id="job1", operator_home=self.operator, stage=self.STAGE,
                  work_item_id=WID, base=self.base, model=review.DEFAULT_MODEL, effort=review.DEFAULT_EFFORT, timeout=20,
                  launch=self.seam, on_progress=self.events.append)
        kw.setdefault("state_identity", None)
        kw.update(over)
        if kw["state_identity"] is None:
            kw["state_identity"] = self.identity()
        return crr.run_review(self.managed, **kw)

    def snapshot(self) -> tuple:
        feedback = self.root / FEEDBACK
        return ((self.root / STATE).read_bytes(), _git(self.root, "status", "--porcelain=v1", "-uall"),
                _git(self.root, "for-each-ref"), feedback.read_bytes() if feedback.is_file() else None)

    def assert_clone_gone(self) -> None:
        self.assertFalse(self.layout.worktree.exists())

    def assert_refused(self, ending: crr.Ending, code: str, cause: str, before: tuple) -> None:
        self.assertEqual((ending.code, ending.cause), (code, cause), dict(ending.evidence))
        self.assertEqual(self.snapshot(), before)
        self.assert_clone_gone()


class RunTests:
    """Tests that hold for either stage."""

    def test_an_approve_is_recorded_with_the_header_the_controller_wrote(self) -> None:
        self.script(message=message("APPROVE"))
        ending = self.run_role()
        self.assertEqual((ending.code, ending.cause), (crr.RECORDED, None), dict(ending.evidence))
        text = (self.root / FEEDBACK).read_text()
        head = text.split("\n## ", 1)[0]
        self.assertIn(f"Reviewer model: openai/{review.DEFAULT_MODEL}", head)
        self.assertIn(f"Reviewer role: {review.LOCAL_REVIEWER_ROLES[self.STAGE]}", head)
        self.assertIn(f"Work item: {WID}", head)
        self.assertEqual(json.loads((self.root / STATE).read_text())["work_items"][WID]["review_round_counts"]
                         [self.STAGE]["local"], 1)
        self.assert_clone_gone()
        self.assertEqual([e["stage"] for e in self.events], ["preparing", "recording", "recorded"])
        self.assertEqual(self.events[2]["result"]["code"], crr.RECORDED)
        self.assertEqual(self.events[2]["result"]["values"]["feedback_path"], FEEDBACK)
        self.assertEqual(len(self.events[1]["verdict_sha256"]), 64)

    def test_the_prompt_names_the_local_role_and_the_bundle_and_asks_for_no_file(self) -> None:
        self.script(message=message("APPROVE"))
        self.run_role()
        (call,) = self.launched
        self.assertEqual(call["task"], crr.COMMANDS[self.STAGE])
        prompt = call["prompt"]
        self.assertIn(review.LOCAL_REVIEWER_ROLES[self.STAGE], prompt)
        self.assertIn(".ai-review/", prompt)
        self.assertIn("write no file", prompt)
        for name in SECTIONS:
            self.assertIn(f"`## {name}`", prompt)
        (entry,) = self.execs()
        self.assertEqual(entry["prompt"], prompt)

    def test_the_composed_header_passes_the_workflows_parser_with_the_model_exactly_when_required(self) -> None:
        values = review_recorder.check(self.root, self.STAGE, WID).values
        body = message("APPROVE").split("\n", 2)[2]
        probe = ("text = Path(sys.argv[1]).read_text()\nfingerprint.assert_verdict_well_formed(text)\n"
                 "print(ws.json.dumps(fingerprint.parse_review_feedback_header(text)))\n")
        for distinct in (True, False):
            with self.subTest(distinct=distinct):
                composed = crr.compose_verdict({**values, "gate": {**(values.get("gate") or {}), "requires_distinct": distinct}},
                                               "APPROVE", None, body, model="gpt-x", completed_at="2026-11-07T00:00:00Z")
                path = self._area / "composed.md"
                path.write_text(composed)
                out = fixtures.run_workflow_python(self.root, probe, str(path)).stdout
                header = json.loads(out.strip().splitlines()[-1])
                self.assertEqual(header["status"], "APPROVE")
                self.assertEqual(header["reviewer_model"], "openai/gpt-x" if distinct else None)

    def test_the_workflows_validator_and_next_action_accept_the_recorded_state(self) -> None:
        self.script(message=message("APPROVE"))
        ending = self.run_role()
        self.assertEqual(ending.code, crr.RECORDED, dict(ending.evidence))
        fixtures.run_workflow_python(
            self.root, "ws.validate_state(ws.json.loads(Path('docs/ai-workflow/WORKFLOW_STATE.json').read_text()), "
                       "repo_root=Path.cwd())\n")
        action = protocol.next_action(self.root, WID)
        self.assertNotEqual(action.basis.state_identity, "")
        self.assertIn(json.loads((self.root / STATE).read_text())["work_items"][WID]["phase"],
                      ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))

    def test_the_inputs_include_a_previous_feedback_and_a_verified_changes_file(self) -> None:
        feedback = self.root / FEEDBACK
        feedback.parent.mkdir(parents=True, exist_ok=True)
        feedback.write_text("# Review Decision\n\nStatus: REVISE\n")
        real = review.changes_file_for
        changes_rel = f".ai-review/{WID}/CHANGES_SINCE_REVIEW.md"
        (self.root / changes_rel).write_text("# Changes\n")
        with mock.patch.object(review, "changes_file_for", lambda *a, **k: changes_rel):
            seen: dict = {}

            def spy(task, **kw):
                clone = kw["cwd"]
                seen["files"] = sorted(p.relative_to(clone).as_posix() for p in (clone / ".ai-review").rglob("*")
                                       if p.is_file())
                return self.seam(task, **kw)

            self.script(message=message("APPROVE"))
            self.run_role(launch=spy)
        self.assertIn(FEEDBACK, seen["files"])
        self.assertIn(changes_rel, seen["files"])
        self.assertTrue(real)

    def test_the_clone_holds_exactly_the_inputs_and_a_dirty_managed_tree_is_still_reviewed(self) -> None:
        (self.root / "README.md").write_text("uncommitted\n")
        self.script(message=message("APPROVE"))
        seen: dict = {}

        def spy(task, **kw):
            clone = kw["cwd"]
            seen["files"] = sorted(p.relative_to(clone).as_posix() for p in (clone / ".ai-review").rglob("*")
                                   if p.is_file())
            seen["scratch"] = (clone / "README.md").read_text() != "uncommitted\n"
            seen["scratch"] = not seen["scratch"]
            return self.seam(task, **kw)

        ending = self.run_role(launch=spy)
        self.assertEqual(ending.code, crr.RECORDED, dict(ending.evidence))
        self.assertTrue(seen["files"])
        self.assertTrue(all(name.startswith(f".ai-review/{WID}/") for name in seen["files"]))
        self.assertFalse(seen["scratch"])
        self.assertIn(" M README.md", ending.evidence["managed_dirty"]["tracked_changes"])

    # -- D4: the way in ------------------------------------------------------

    def _with_inputs(self, extra: list[str]):
        real = crr.input_paths

        def patched(*args, **kw):
            paths, bundle, changes = real(*args, **kw)
            return [*paths, *extra], bundle, changes

        return mock.patch.object(crr, "input_paths", patched)

    def test_each_input_refusal_ends_before_the_launch(self) -> None:
        outside = self._area / "outside.txt"
        outside.write_text("x")
        extra = self.root / ".ai-review" / "extra"  # ignored by the fixture, so the recorder's check passes
        (extra / "sub").mkdir(parents=True)
        (extra / "link").symlink_to(outside)
        os.mkfifo(extra / "fifo")
        (extra / "sub" / ".gitmodules").write_text("[submodule]\n")
        (extra / "auth.json").write_text("{}")
        (extra / "armoured.txt").write_text(f"x\n{ARMOUR}\n")
        (extra / "big.bin").write_bytes(b"0" * (crr.MAX_INPUT_FILE_BYTES + 1))
        cases = (("link", "special_file"), ("fifo", "special_file"), ("sub/.gitmodules", "submodule"),
                 ("auth.json", "credential_input"), ("armoured.txt", "credential_input"),
                 ("big.bin", "input_over_bound"), ("../../../outside.txt", "path_escape"),
                 (str(outside), "path_escape"))
        for name, cause in cases:
            rel = name if name.startswith(("/", "..")) else f".ai-review/extra/{name}"
            with self.subTest(path=name):
                self.script(message=message("APPROVE"))
                before = self.snapshot()
                with self._with_inputs([rel]):
                    ending = self.run_role()
                self.assert_refused(ending, crr.NOT_STARTED, cause, before)
                self.assertEqual(self.launched, [])

    def test_a_path_under_the_runtime_root_is_a_path_escape(self) -> None:
        (self.root / "rt").mkdir()
        (self.root / "rt" / "x").write_text("x")
        with self.assertRaises(crr.InputRefused) as ctx:
            crr.collect_inputs(self.root, self.root / "rt", ["rt/x"])
        self.assertEqual(ctx.exception.cause, "path_escape")

    def test_too_many_paths_and_too_many_bytes_are_over_bound(self) -> None:
        for name, patch in (("paths", {"MAX_INPUT_PATHS": 1}), ("total", {"MAX_INPUT_BYTES": 10})):
            with self.subTest(bound=name), mock.patch.multiple(crr, **patch), self.assertRaises(crr.InputRefused) as ctx:
                crr.collect_inputs(self.root, self._area / "runtime", ["README.md", ".gitignore"])
            self.assertEqual(ctx.exception.cause, "input_over_bound")

    def test_credential_names_are_refused(self) -> None:
        for rel in ("a/auth.json", ".env", "x/.env.local", "k.pem", "k.key", "id_rsa", "id_ed25519", ".claude.json",
                    ".credentials.json", ".claude/settings.json", ".claude/settings.local.json", "d/.ssh/x",
                    "d/.aws/x", ".codex/x"):
            with self.subTest(path=rel), self.assertRaises(crr.InputRefused) as ctx:
                crr._check_path(rel, self.root, self._area / "runtime")
            self.assertEqual(ctx.exception.cause, "credential_input")
        crr._check_path("docs/notes.md", self.root, self._area / "runtime")

    # -- D3 and D4: the way out ----------------------------------------------

    def test_a_message_the_result_would_have_repaired_is_unparsed(self) -> None:
        self.script(message_bytes=base64.b64encode(message("APPROVE").encode() + b"\xff\xfe").decode())
        before = self.snapshot()
        self.assert_refused(self.run_role(), crr.REFUSED, "verdict_unparsed", before)

    def test_every_malformed_message_is_unparsed(self) -> None:
        sections = message("APPROVE").split("\n", 2)[2]
        cases = {
            "no status": sections,
            "bad status": "Status: MAYBE\n" + sections,
            "forged model": "Status: APPROVE\nReviewer model: openai/other\n" + sections,
            "second status": "Status: APPROVE\nStatus: REVISE\n" + sections,
            "title": "# Review Decision\nStatus: APPROVE\n" + sections,
            "prose first": "Looks fine.\nStatus: APPROVE\n" + sections,
            "no section at all": "Status: APPROVE\n",
            "a missing section": message("APPROVE").replace("## Usability concerns", "## Usability"),
            "a bad scope on a revise": message("REVISE", scope="banana"),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.script(message=text)
                before = self.snapshot()
                self.assert_refused(self.run_role(), crr.REFUSED, "verdict_unparsed", before)
                shutil.rmtree(self.layout.base, ignore_errors=True)
                self.events.clear()

    def test_a_message_over_a_mebibyte_is_over_bound(self) -> None:
        self.script(message=message("APPROVE", extra="x" * (crr.MAX_VERDICT_BYTES + 1)))
        before = self.snapshot()
        self.assert_refused(self.run_role(), crr.REFUSED, "verdict_over_bound", before)

    def test_prose_naming_the_markers_is_recorded_and_real_armour_is_refused(self) -> None:
        self.script(message=message("APPROVE", extra=" The check looks for `-----BEGIN` and `PRIVATE KEY-----`."))
        self.assertEqual(self.run_role().code, crr.RECORDED)

    def test_a_pgp_private_key_block_is_armour_too(self) -> None:
        self.assertTrue(crr.ARMOUR.search(b"-----BEGIN PGP PRIVATE KEY BLOCK-----"))
        self.assertTrue(crr.ARMOUR.search(b"-----BEGIN PRIVATE KEY-----"))
        self.assertFalse(crr.ARMOUR.search(b"-----BEGIN PUBLIC KEY-----"))

    def test_the_recorders_advisory_reaches_the_ending_evidence(self) -> None:
        real = review_recorder.check

        def with_advisory(*args, **kw):
            result = real(*args, **kw)
            return dataclasses.replace(result, values={**result.values, "advisory": "bundle_id differs"})

        self.script(message=message("APPROVE"))
        with mock.patch.object(review_recorder, "check", with_advisory):
            ending = self.run_role()
        self.assertEqual(ending.code, crr.RECORDED, dict(ending.evidence))
        self.assertEqual(ending.evidence["advisory"], "bundle_id differs")

    def test_armour_in_the_message_is_refused_and_leaves_no_text(self) -> None:
        self.script(message=message("APPROVE", extra=f" {ARMOUR}"))
        before = self.snapshot()
        ending = self.run_role()
        self.assert_refused(ending, crr.REFUSED, "credential_in_verdict", before)
        self.assertFalse(self.layout.last_message.exists())
        result = ending.worker
        self.assertEqual((result.result, result.stdout, result.stderr, result.stream_diagnosis["detail"]),
                         ("", "", "", ""))
        self.assertEqual(len(ending.evidence["message_sha256"]), 64)
        self.assertGreater(ending.evidence["message_length"], 0)
        self.assertNotIn("PRIVATE KEY", json.dumps(dataclasses.asdict(result), default=str))
        self.assertNotIn("PRIVATE KEY", json.dumps(dict(ending.evidence), default=str))
        self.assertTrue([p for p in (self.layout.codex_home / "sessions").rglob("*") if p.is_file()])

    def test_armour_on_stderr_of_a_failed_worker_is_refused_not_failed(self) -> None:
        self.script(message="", exit=1, stderr=f"boom {ARMOUR}\nmore")
        before = self.snapshot()
        ending = self.run_role()
        self.assert_refused(ending, crr.REFUSED, "credential_in_verdict", before)
        self.assertEqual(ending.evidence["worker_outcome"], worker.FAILURE)
        result = ending.worker
        self.assertEqual((result.result, result.stdout, result.stderr, result.stream_diagnosis["detail"]),
                         ("", "", "", ""))
        self.assertNotIn("PRIVATE KEY", json.dumps(dataclasses.asdict(result), default=str))

    def test_a_write_a_new_ref_and_a_changed_input_each_violate_the_read_only_run(self) -> None:
        def changed_input(task, **kw):
            result = self.seam(task, **kw)
            target = next((kw["cwd"] / ".ai-review").rglob("REVIEW_REQUEST.md"))
            target.write_text(target.read_text() + "tampered\n")
            return result

        def unreadable_input(task, **kw):
            result = self.seam(task, **kw)
            next((kw["cwd"] / ".ai-review").rglob("REVIEW_REQUEST.md")).chmod(0)
            return result

        def grown_input(task, **kw):
            result = self.seam(task, **kw)
            os.truncate(next((kw["cwd"] / ".ai-review").rglob("REVIEW_REQUEST.md")), crr.MAX_INPUT_FILE_BYTES + 1)
            return result

        def extra_input(task, **kw):
            result = self.seam(task, **kw)
            (kw["cwd"] / ".ai-review" / "extra.md").write_text("x")
            return result

        cases = {
            "write": ({"write_outside": [str(self.layout.worktree / "new.txt")]}, None),
            "ref": ({"git": [["update-ref", "refs/heads/evil", "HEAD"]]}, None),
            "commit": ({"git": [["commit", "--allow-empty", "-m", "x"]]}, None),
            "input": ({}, changed_input),
            "unreadable input": ({}, unreadable_input),
            "grown input": ({}, grown_input),
            "extra": ({}, extra_input),
        }
        for name, (spec, launch) in cases.items():
            with self.subTest(name):
                self.script(message=message("APPROVE"), **spec)
                before = self.snapshot()
                ending = self.run_role(**({"launch": launch} if launch else {}))
                self.assert_refused(ending, crr.REFUSED, "read_only_violated", before)
                shutil.rmtree(self.layout.base, ignore_errors=True)

    # -- D5: the endings -----------------------------------------------------

    def test_not_started_endings_start_no_codex(self) -> None:
        self.script(message=message("APPROVE"))
        refused = review_recorder.RecorderResult(False, "recorder_identity_mismatch", None, "mismatch", {})
        runner = WorkflowQueryError("runner", evidence={"reason": "query_git_not_isolated"})
        cases = (
            ("recorder", {"patch": mock.patch.object(review_recorder, "check", return_value=refused)},
             "recorder_identity_mismatch"),
            ("runner", {"patch": mock.patch.object(review_recorder, "check", side_effect=runner)}, "check"),
            ("release", {"patch": mock.patch.object(protocol, "resolve_artifact", side_effect=WorkflowReleaseChangedError(
                "changed", evidence={}))}, crr.RELEASE_CHANGED),
            ("preparer", {"over": {"base": "0" * 40}}, "managed_branch_moved"),
        )
        for name, spec, cause in cases:
            with self.subTest(name):
                before = self.snapshot()
                with spec.get("patch", mock.MagicMock()):
                    ending = self.run_role(verified=spec.get("verified", True), **spec.get("over", {}))
                self.assert_refused(ending, crr.NOT_STARTED, cause, before)
                self.assertEqual(self.execs(), [])

    def test_an_unverified_build_is_a_launcher_refusal_before_any_codex(self) -> None:
        self.script(message=message("APPROVE"))
        before = self.snapshot()
        ending = self.run_role(verified=False)
        self.assert_refused(ending, crr.NOT_STARTED, "build", before)
        self.assertEqual(self.execs(), [])

    def test_each_worker_failure_subtype_is_named(self) -> None:
        cases = (
            ("non_zero_exit", {"message": "", "exit": 1}, {}),
            ("no_final_message", {"verdict": "none"}, {}),
            ("timeout", {"verdict": "hang"}, {"timeout": 1}),
            ("usage_limit", {"verdict": "limit"}, {}),
            ("terminated_by_signal", {"message": message("APPROVE"), "signal": "SIGTERM"}, {}),
            ("sandbox_breach", {"message": message("APPROVE"),
                                "write_outside": [str(self.layout.worktree / ".git" / "hooks" / "post-merge")]}, {}),
        )
        for cause, spec, over in cases:
            with self.subTest(cause):
                self.script(**spec)
                before = self.snapshot()
                ending = self.run_role(**over)
                self.assert_refused(ending, crr.WORKER_FAILED, cause, before)
                self.assertEqual(ending.worker.subtype, cause)
                shutil.rmtree(self.layout.base, ignore_errors=True)

    def test_a_usage_limit_keeps_its_fact(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        ending = self.run_role()
        self.assertEqual(ending.cause, "usage_limit")
        self.assertIn("usage_limit", ending.worker.stream_diagnosis)

    def test_a_changed_state_identity_discards_the_verdict(self) -> None:
        self.script(message=message("APPROVE"))
        before = self.snapshot()
        ending = self.run_role(state_identity="not-the-identity")
        self.assert_refused(ending, crr.STATE_IDENTITY_CHANGED, "state_identity_changed", before)
        self.assertIn("Status: APPROVE", ending.evidence["verdict"])

    def test_a_script_changed_during_the_run_is_a_release_change(self) -> None:
        self.script(message=message("APPROVE"))

        def change(task, **kw):
            result = self.seam(task, **kw)
            script = self.root / "scripts" / "workflow_state.py"
            script.write_text(script.read_text() + "\n# changed\n")
            return result

        ending = self.run_role(launch=change)
        self.assertEqual((ending.code, ending.cause), (crr.RELEASE_CHANGED, "workflow_release_changed"))
        self.assertFalse((self.root / FEEDBACK).exists())
        self.assert_clone_gone()

    def test_a_runner_error_during_record_names_the_state_as_the_truth(self) -> None:
        self.script(message=message("APPROVE"))
        with mock.patch.object(review_recorder, "record", side_effect=WorkflowQueryError("timed out", evidence={})):
            ending = self.run_role()
        self.assertEqual((ending.code, ending.cause), (crr.RECORD_REFUSED, "WorkflowQueryError"))
        self.assertIn("state_is_the_truth", ending.evidence)
        self.assertIn("Status: APPROVE", ending.evidence["verdict"])
        self.assertEqual(self.events[-1]["stage"], "recorded")
        self.assert_clone_gone()

    def test_an_exception_after_the_spawn_propagates_with_the_clone_gone(self) -> None:
        def boom(task, *, on_spawn, **kw):
            on_spawn(1, 1)
            raise ControllerError("after the spawn")

        with self.assertRaises(ControllerError):
            self.run_role(launch=boom)
        self.assert_clone_gone()

    def test_an_exception_from_a_step_propagates_with_the_clone_gone(self) -> None:
        self.script(message=message("APPROVE"))
        with mock.patch.object(crr, "after_check", side_effect=RuntimeError("fault")), self.assertRaises(RuntimeError):
            self.run_role()
        self.assert_clone_gone()

    def test_a_removal_failure_is_named_and_never_raised(self) -> None:
        def stub(task, *, runtime_root, job_id, cwd, **kw):
            Path(runtime_root, "worker", job_id, "last-message.md").write_text(message("APPROVE"))
            return types.SimpleNamespace(outcome=worker.SUCCESS, subtype="success", result="", stdout="", stderr="",
                                         stream_diagnosis={})

        with mock.patch.object(crr.runtime, "remove_tree", side_effect=OSError("busy")):
            ending = self.run_role(launch=stub)
        self.assertIn("busy", ending.evidence["clone_removal_error"])


class PlanRoleTests(RunTests, RoleCase):
    STAGE = "plan"


class ImplementationRoleTests(RunTests, RoleCase):
    STAGE = "implementation"

    def test_a_revise_is_recorded_and_committed_with_a_clean_index(self) -> None:
        self.script(message=message("REVISE"))
        head = fixtures.current_head(self.root)
        ending = self.run_role()
        self.assertEqual((ending.code, ending.cause), (crr.RECORDED, None), dict(ending.evidence))
        self.assertNotEqual(fixtures.current_head(self.root), head)
        self.assertEqual(_git(self.root, "diff", "--cached", "--name-only"), "")
        self.assert_clone_gone()

    def test_a_file_staged_between_check_and_record_is_a_record_refusal(self) -> None:
        self.script(message=message("APPROVE"))

        def stage(task, **kw):
            result = self.seam(task, **kw)
            (self.root / "stray.txt").write_text("x\n")
            _git(self.root, "add", "stray.txt")
            return result

        ending = self.run_role(launch=stage)
        self.assertEqual((ending.code, ending.evidence["recorder_cause"]), (crr.RECORD_REFUSED, "recorder_index_dirty"))
        self.assertIn("Status: APPROVE", ending.evidence["verdict"])
        self.assertFalse((self.root / FEEDBACK).exists())
        self.assert_clone_gone()

    def test_a_staged_file_before_the_run_starts_no_codex(self) -> None:
        (self.root / "stray.txt").write_text("x\n")
        _git(self.root, "add", "stray.txt")
        ending = self.run_role()
        self.assertEqual((ending.code, ending.cause), (crr.NOT_STARTED, "recorder_index_dirty"))
        self.assertEqual(self.execs(), [])

    def test_a_revise_whose_commit_refuses_is_pending_and_offers_no_by_hand_record(self) -> None:
        state = json.loads((self.root / STATE).read_text())
        state["work_items"][WID]["technical_review_block_pins"] = [
            {"bundle_id": "d" * 64, "review_content_id": "e" * 64, "recorded_at": "2026-11-07T08:30:00Z"}]
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        self.script(message=message("REVISE"))
        ending = self.run_role()
        self.assertEqual((ending.code, ending.cause), (crr.RECORD_PENDING, "ReviewStageWriteNotCommittableError"),
                         dict(ending.evidence))
        self.assertNotIn("verdict", ending.evidence)
        self.assertIn("/apply-implementation-review", ending.evidence["next"])
        self.assertTrue((self.root / FEEDBACK).is_file())
        self.assert_clone_gone()


class RecoverTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name).resolve() / "runtime"
        self.layout = ws.WorkerLayout(self.runtime, "job1")
        self.layout.worktree.mkdir(parents=True)
        (self.layout.worktree / "marker").write_text("clone")
        self.record_spy = self.enterContext(mock.patch.object(review_recorder, "record"))
        self.check_spy = self.enterContext(mock.patch.object(review_recorder, "check"))

    def recorded(self, code: str) -> dict:
        return {"read_only": True, "stage": "recorded", "result": {"code": code, "cause": "c"}}

    def test_abandon_and_drift_end_every_stage_the_same_way(self) -> None:
        blocks = [{"stage": "preparing"}, {"stage": "recording", "verdict_sha256": "a" * 64},
                  self.recorded(crr.RECORDED)]
        for path, code in (("abandon", "OperatorAbandoned"), ("drift", "workflow_release_changed")):
            for block in blocks:
                with self.subTest(path=path, stage=block["stage"]):
                    self.layout.worktree.mkdir(parents=True, exist_ok=True)
                    ending = crr.recover(self.runtime, "job1", block, path)
                    self.assertEqual(ending.code, code)
                    self.assertFalse(self.layout.worktree.exists())
                    if block["stage"] == "recorded":
                        self.assertEqual(ending.evidence["result"], block["result"])
                    self.assertNotIn(str(self.layout.worktree), json.dumps(dict(ending.evidence)))

    def test_resume_ends_each_stage_with_its_own_code(self) -> None:
        cases = [({"stage": "preparing"}, crr.NOT_RECORDED), ({"stage": "recording"}, crr.RECORD_INTERRUPTED)]
        cases += [(self.recorded(code), code) for code in (
            crr.RECORDED, crr.STATE_IDENTITY_CHANGED, crr.RELEASE_CHANGED, crr.RECORD_REFUSED, crr.RECORD_PENDING)]
        for block, code in cases:
            with self.subTest(code=code):
                self.layout.worktree.mkdir(parents=True, exist_ok=True)
                ending = crr.recover(self.runtime, "job1", block, "resume")
                self.assertEqual(ending.code, code)
                self.assertFalse(self.layout.worktree.exists())
        self.record_spy.assert_not_called()
        self.check_spy.assert_not_called()

    def test_an_unknown_stage_and_an_unknown_path_raise_after_the_clone_is_removed(self) -> None:
        for block, path in (({"stage": "weird"}, "resume"), ({"stage": "preparing"}, "elsewhere"),
                            ({"stage": "recorded", "result": {}}, "resume")):
            with self.subTest(block=block, path=path):
                self.layout.worktree.mkdir(parents=True, exist_ok=True)
                with self.assertRaises(crr.CodexReviewRecoveryError):
                    crr.recover(self.runtime, "job1", block, path)
                self.assertFalse(self.layout.worktree.exists())


class ModuleTests(unittest.TestCase):
    def test_the_module_does_not_import_the_landing(self) -> None:
        tree = ast.parse((REPO_ROOT / "controller" / "codex_review_role.py").read_text())
        names = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                 for alias in node.names} | {node.module for node in ast.walk(tree)
                                             if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse([n for n in names if "codex_worker_landing" in (n or "")])

    def test_the_abandon_and_drift_codes_are_the_ones_the_job_writes(self) -> None:
        from controller import job
        self.assertEqual(crr.OPERATOR_ABANDONED, job.OPERATOR_ABANDONED_CODE)
        self.assertEqual(crr.RELEASE_CHANGED, job.WORKFLOW_RELEASE_CHANGED_REASON)

    def test_the_body_is_the_message_unchanged_from_its_first_section(self) -> None:
        rest = "".join(f"\n## {name}\n\nNone.\n" for name in review.LOCAL_VERDICT_SECTIONS[1:])
        body = (b"## Blocking findings\r\n\r\nNone.\x0cstill the same line\n" + rest.encode())
        status, scope, parsed = crr.parse_verdict(b"Status: APPROVE\nReview scope: full\n\n" + body)
        self.assertEqual((status, scope, parsed.encode()), ("APPROVE", "full", body))

    def test_the_sections_are_the_headings_the_protocol_requires(self) -> None:
        text = (REPO_ROOT / "docs" / "ai-workflow" / "REVIEW_PROTOCOL.md").read_text()
        template = text.split("Reviewed review_content_id: <the exact review_content_id this feedback reviewed>", 1)[1]
        headings = [line[3:] for line in template.split("```", 1)[0].splitlines() if line.startswith("## ")]
        self.assertEqual(headings, list(review.LOCAL_VERDICT_SECTIONS))

    def test_a_message_with_no_section_or_a_missing_one_is_refused(self) -> None:
        for data in (b"Status: APPROVE\n", b"Status: APPROVE\n\nLooks good.\n",
                     b"Status: APPROVE\n\n## Blocking findings\n\nNone.\n"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                crr.parse_verdict(data)
        with self.assertRaises(ValueError):
            crr.parse_verdict(b"Status: REVISE\nReview scope: banana\n\n" + message("REVISE").split("\n", 2)[2].encode())

    def test_the_causes_are_the_ones_the_plan_names(self) -> None:
        self.assertEqual(set(crr.CAUSES), {
            "special_file", "submodule", "credential_input", "path_escape", "input_over_bound", "verdict_over_bound",
            "verdict_unparsed", "credential_in_verdict", "read_only_violated"})


if __name__ == "__main__":
    unittest.main()
