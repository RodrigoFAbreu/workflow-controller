"""Tests for ``controller.review_recorder`` (H4e2, CP1, D1).

They run the Workflow scripts this repository itself has installed
(``.workflow-manager/installation.json``'s managed set), copied into
disposable Git repositories and seeded through the Workflow's own writers
and bundle generator. Nothing the Workflow answers is faked. The expected
files are the ones the in-place command's function sequence leaves on a
fresh copy of the same fixture (:data:`IN_PLACE`), compared byte for byte.

A fixture is built once per class and put back before each test: a bundle
records the path of the worktree it was generated in, so the copy is made
back onto the very same path.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import protocol, review_recorder, workflow_contract  # noqa: E402
from controller.errors import ControllerError, WorkflowQueryError  # noqa: E402
from tests import fixtures  # noqa: E402

WID = "wi-1"
STATE = "docs/ai-workflow/WORKFLOW_STATE.json"
FEEDBACK = f".ai-review/{WID}/feedback/REVIEW_FEEDBACK.md"
COMPLETED_AT = "2026-11-07T10:00:00Z"
SECTIONS = ("Blocking findings", "Important findings", "Optional findings", "Missing tests",
            "Architecture and maintainability concerns", "Migration and data-integrity concerns",
            "Usability concerns", "Required acceptance criteria")


def _state(root: Path) -> dict:
    return json.loads((root / STATE).read_text())


def _entry(root: Path) -> dict:
    return _state(root)["work_items"][WID]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _install_workflow(root: Path) -> None:
    """The Workflow this repository has installed, at its target paths."""
    record = json.loads((fixtures.REPO_ROOT / ".workflow-manager/installation.json").read_text())
    for rel in record["managed"]:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixtures.REPO_ROOT / rel, dest)
    fixtures.write_installation_manifest(root, workflow_version=record["workflow_version"], profile="full",
                                         managed=record["managed"])


_PATCH_TO_SELF_REVIEW = '''
from pathlib import Path
root = Path.cwd()
def patch(state):
    entry = state["work_items"]["wi-1"]
    entry["phase"] = "SELF_REVIEWING_IMPLEMENTATION"
    entry["checkpoints"] = {"CP1": {"status": "COMPLETE", "start_commit": entry["base_commit"]}}
    entry["plan_approval"] = {"status": "CURRENT", "basis": "LEGACY_V1", "user_confirmation": "fixture",
                              "reviewed_bundle_id": None, "approved_review_content_id": None,
                              "review_content_manifest": None, "reviewed_content_commit": None,
                              "legacy_evidence": None, "waived_guarantees": [], "recorded_at": "2026-11-07T08:00:00Z"}
    return state
ws.state_transaction(root, patch)
'''

_RECORD_GENERATION = '''
import subprocess
from pathlib import Path
root = Path.cwd()
def head():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
ws.state_transaction(root, lambda state: ws.record_bundle_generation(
    state, "wi-1", stage="implementation", head=head(), now="2026-11-07T09:00:00Z"))
'''

_REQUEST = '''
from pathlib import Path
root = Path.cwd()
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"]["wi-1"]
content_id = ws.approval_review_content_id(
    root, stage="implementation", base_commit=entry["base_commit"], work_item_type="product",
    work_item_id="wi-1", head="HEAD", artifacts_path=fingerprint.artifacts_path_for_work_item("wi-1"))
(root / ".ai-review/wi-1/current/REVIEW_REQUEST.md").write_text(
    f"# Review request\\n\\nstage: implementation\\nreview_content_id: {content_id}\\n")
'''


def build_target(root: Path, stage: str) -> None:
    """A repository with the installed Workflow and ``WID`` at
    ``AWAITING_LOCAL_PLAN_REVIEW`` (``plan``) or
    ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` (``implementation``), its bundle
    generated and verified."""
    root.mkdir(parents=True)
    fixtures.git_init(root)
    fixtures.run(["git", "config", "user.email", "recorder@example.invalid"], cwd=root)
    fixtures.run(["git", "config", "user.name", "Recorder"], cwd=root)
    (root / "README.md").write_text("recorder fixture\n")
    (root / ".gitignore").write_text(".ai-review/\n")
    _install_workflow(root)
    config = root / "docs/ai-workflow"
    config.mkdir(parents=True, exist_ok=True)
    (config / "WORKFLOW_CONFIG.json").write_text(json.dumps(
        {"schema_version": 1, "default_workflow_version": "2.2", "supported_versions": ["1", "2.1", "2.2"]}) + "\n")
    (root / STATE).write_text(json.dumps({"schema_version": 1, "active_work_item_id": None, "work_items": {}}) + "\n")
    base = fixtures.commit_all(root, "seed")
    fixtures.run_workflow_seed(root, "ready", work_item_id=WID, base_commit=base)
    if stage == "plan":
        return
    fixtures.commit_all(root, "seed state")
    fixtures.run_workflow_python(root, _PATCH_TO_SELF_REVIEW)
    fixtures.commit_all(root, "implemented")
    fixtures.run_workflow_python(root, _RECORD_GENERATION)
    fixtures.commit_paths(root, "generation record\n\nWorkflow-Bundle-Generation-Record: wi-1/1\n"
                          "Workflow-Work-Item: wi-1", STATE)
    bundle = root / ".ai-review" / WID / "current"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "IMPLEMENTATION_SUMMARY.md").write_text("# Summary\n\nimplementation_revision: 1\n")
    (bundle / "TEST_RESULTS.md").write_text("# Test results\n\nall green\n")
    (bundle / "CONTEXT_FILES.txt").write_text("README.md\n")
    fixtures.run_workflow_python(root, _REQUEST)
    result = subprocess.run(["./scripts/prepare-ai-review.sh", _entry(root)["base_commit"], "implementation", WID], cwd=root,
                            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if result.returncode != 0:
        raise AssertionError(f"bundle generation failed:\n{result.stdout}\n{result.stderr}")


class RecorderCase(unittest.TestCase):
    """A fixture of ``STAGE`` put back at its own path before each test."""

    STAGE = "plan"
    _built: Path
    _kept: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._area = Path(tempfile.mkdtemp(prefix="recorder-"))
        cls._built = cls._area / "target"
        build_target(cls._built, cls.STAGE)
        cls._kept = cls._area / "kept"
        shutil.copytree(cls._built, cls._kept, symlinks=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._area, ignore_errors=True)

    def setUp(self) -> None:
        shutil.rmtree(self._built, ignore_errors=True)
        shutil.copytree(self._kept, self._built, symlinks=True)
        self.root = self._built
        self.isolated_config = self._area / "gitconfig"
        self.isolated_config.write_text("")

    # -- helpers -------------------------------------------------------------

    def check(self) -> review_recorder.RecorderResult:
        return review_recorder.check(self.root, self.STAGE, WID)

    def identity(self) -> str:
        return protocol.next_action(self.root, WID).basis.state_identity

    def compose(self, values: dict, status: str = "APPROVE", *, round_: int | None = None, filler: str = "",
                model: str | None = "anthropic/claude-test") -> str:
        lines = ["# Review Decision", "", f"Status: {status}",
                 f"Reviewed bundle ID: {values['bundle_id']}", f"Reviewed base commit: {values['base_commit']}",
                 f"Work item: {WID}", f"Reviewed review_content_id: {values['review_content_id']}",
                 f"{values['revision_label']} {values['revision']}", f"Reviewer role: {values['reviewer_role']}"]
        if model is not None and values["gate"]["requires_distinct"]:
            lines.append(f"Reviewer model: {model}")
        lines += [f"Review round: {round_ or values['round']}", f"Completed at: {COMPLETED_AT}", ""]
        body = {name: ["None."] for name in SECTIONS}
        if status == "REVISE":
            body["Important findings"] = ["- I1: The thing is wrong."]
        if status == "BLOCK":
            body["Blocking findings"] = ["- B1: The thing cannot stand."]
        body["Optional findings"] = ["- O1: A note for later." + filler]
        for name in SECTIONS:
            lines += [f"## {name}", "", *body[name], ""]
        return "\n".join(lines)

    def verdict_file(self, text: str) -> Path:
        path = self._area / "verdict.md"
        path.write_text(text, encoding="utf-8")
        return path

    def record(self, text: str, values: dict, **kwargs) -> review_recorder.RecorderResult:
        kwargs.setdefault("state_identity", self.identity())
        kwargs.setdefault("digests", values["digests"])
        return review_recorder.record(self.root, self.STAGE, WID, self.verdict_file(text), **kwargs)

    def snapshot(self) -> tuple[bytes, str]:
        return (self.root / STATE).read_bytes(), _git(self.root, "status", "--porcelain=v1", "-uall")

    def written(self) -> dict[str, bytes]:
        paths = [STATE]
        if (self.root / FEEDBACK).is_file():
            paths.append(FEEDBACK)
        return {path: (self.root / path).read_bytes() for path in paths}


# The in-place command's function sequence (review-plan.md steps 5 to 8,
# review-implementation.md A3 to A6), run by the Workflow's own interpreter
# on a fresh copy of the fixture.
IN_PLACE = '''
import subprocess
from pathlib import Path
root = Path.cwd()
stage, status, now, round_ = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
text = Path(sys.argv[5]).read_text()
state = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
fingerprint.assert_bundle_not_rejected(root, "wi-1")
fingerprint.ensure_feedback_dir(root, "wi-1")
fingerprint.assert_verdict_well_formed(text)
feedback = root / fingerprint.resolve_feedback_dir(root, "wi-1") / "REVIEW_FEEDBACK.md"
ws._write_review_feedback_atomically(feedback, text)
rel = feedback.relative_to(root).as_posix()
entry = state["work_items"]["wi-1"]
if stage == "plan":
    writer = ws.record_local_plan_review
    bundle_id = fingerprint.compute_bundle_id(root / fingerprint.resolve_bundle_dir(root, "wi-1", stage="plan"))[0]
    content_id = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, "wi-1")[0]
else:
    writer = ws.record_local_implementation_review
    found = ws.verify_implementation_review_bundle(root, "wi-1")
    bundle_id, content_id = found["bundle_id"], found["review_content_id"]
ws.state_transaction(root, lambda current: writer(
    current, "wi-1", verdict=status, bundle_id=bundle_id, review_content_id=content_id, round=round_, now=now,
    audit=ws.local_review_audit(root, current, "wi-1", stage, feedback_text=text, feedback_path=rel),
    verdict_text=text))
if stage == "implementation" and status == "REVISE":
    ws.commit_pending_applying_review_feedback_entry(root, "wi-1", attribution=())
'''


class PlanStageTest(RecorderCase):
    STAGE = "plan"

    def test_check_returns_the_header_values_gate_round_and_digests_and_writes_nothing(self) -> None:
        before = self.snapshot()
        result = self.check()
        self.assertTrue(result.ok, result)
        values = result.values
        self.assertEqual(values["work_item_id"], WID)
        self.assertEqual(values["reviewer_role"], "LOCAL_MODEL_PLAN_REVIEW")
        self.assertEqual(values["revision_label"], "Plan revision reviewed:")
        self.assertEqual(values["round"], 1)
        self.assertEqual(values["base_commit"], _entry(self.root)["base_commit"])
        self.assertEqual(values["bundle_id"], _entry(self.root)["current_bundle_id"])
        self.assertEqual(values["gate"]["gate"], "plan_approval")
        self.assertIn("scripts/workflow_state.py", values["digests"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / FEEDBACK).exists())

    def test_the_round_is_the_local_count_plus_one(self) -> None:
        state = _state(self.root)
        state["work_items"][WID]["review_round_counts"]["plan"]["local"] = 4
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        self.assertEqual(self.check().values["round"], 5)

    def _recorded_matches_in_place(self, status: str) -> None:
        values = self.check().values
        text = self.compose(values, status)
        result = self.record(text, values)
        self.assertTrue(result.ok, result)
        recorded = self.written()
        self.assertEqual(recorded[FEEDBACK], text.encode())
        self.setUp()
        verdict = self.verdict_file(text)
        fixtures.run_workflow_python(self.root, IN_PLACE, "plan", status, COMPLETED_AT, str(values["round"]),
                                     str(verdict))
        self.assertEqual(self.written(), recorded)
        return result

    def test_approve_leaves_what_the_in_place_command_leaves(self) -> None:
        result = self._recorded_matches_in_place("APPROVE")
        self.assertEqual(result.values["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        entry = _entry(self.root)
        stage = entry["plan_review_stages"]["LOCAL_MODEL_PLAN_REVIEW"]
        self.assertEqual(stage["completed_at"], COMPLETED_AT)
        self.assertEqual(stage["run_ref"], f"session:local {FEEDBACK}")
        self.assertEqual(stage["reviewer_model"], "anthropic/claude-test")
        self.assertIn("O1", json.dumps(entry["optional_findings"]))

    def test_revise_leaves_what_the_in_place_command_leaves(self) -> None:
        result = self._recorded_matches_in_place("REVISE")
        self.assertEqual(result.values["phase"], "REVISING_PLAN")
        self.assertIsNone(result.values["committed"])

    def test_block_leaves_what_the_in_place_command_leaves(self) -> None:
        result = self._recorded_matches_in_place("BLOCK")
        self.assertEqual(result.values["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

    def test_the_reviewer_model_line_reaches_the_ledger_when_the_gate_requires_it(self) -> None:
        values = self.check().values
        self.assertTrue(values["gate"]["requires_distinct"])
        self.assertIn("Reviewer model: anthropic/claude-test", self.compose(values))
        self.assertNotIn("Reviewer model:", self.compose(values, model=None))
        self.assertTrue(self.record(self.compose(values), values).ok)
        stage = _entry(self.root)["plan_review_stages"]["LOCAL_MODEL_PLAN_REVIEW"]
        self.assertEqual(stage["reviewer_model"], "anthropic/claude-test")

    def test_a_wrong_phase_is_the_workflows_own_refusal(self) -> None:
        state = _state(self.root)
        state["work_items"][WID]["phase"] = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        result = self.check()
        self.assertFalse(result.ok)
        self.assertEqual(result.cause, "workflow_refused")
        self.assertEqual(result.workflow_class, "WrongPhaseForPlanReviewStageError")

    def test_a_rejected_bundle_is_the_workflows_own_refusal_and_nothing_is_written(self) -> None:
        fixtures.write_rejected_marker(self.root, WID, scoped=True)
        before = self.snapshot()
        result = self.check()
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "workflow_refused", "BundleRejectedError"))
        self.assertEqual(self.snapshot(), before)

    def test_a_malformed_verdict_is_refused_with_nothing_written(self) -> None:
        values = self.check().values
        before = self.snapshot()
        text = self.compose(values, "APPROVE").replace("Status: APPROVE", "Status: APPROVE\nReview scope: changes")
        result = self.record(text, values)
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "workflow_refused", "MalformedVerdictError"))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / FEEDBACK).exists())

    def test_a_foreign_feedback_directory_is_refused(self) -> None:
        # A scoped item's directory is private; the legacy flat path is where another item's file can sit.
        state = _state(self.root)
        del state["work_items"][WID]["feedback_layout"]
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        shutil.rmtree(self.root / ".ai-review" / WID / "feedback", ignore_errors=True)
        flat = self.root / ".ai-review" / "feedback"
        flat.mkdir(parents=True, exist_ok=True)
        (flat / "REVIEW_FEEDBACK.md").write_text("# Review Decision\n\nStatus: REVISE\nWork item: another-item\n")
        result = self.check()
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "recorder_feedback_foreign", "FeedbackOwnedByOtherWorkItemError"))

    def test_a_changed_bundle_is_an_identity_mismatch(self) -> None:
        manifest = self.root / ".ai-review" / WID / "current" / "MANIFEST.md"
        text = manifest.read_text()
        manifest.write_text(text.replace("bundle_id: ", "bundle_id: 0", 1))
        result = self.check()
        self.assertEqual((result.ok, result.cause), (False, "recorder_identity_mismatch"))

    def test_a_header_that_differs_from_the_recomputed_values_is_an_identity_mismatch(self) -> None:
        values = self.check().values
        before = self.snapshot()
        text = self.compose(values).replace(values["bundle_id"], "0" * 64)
        result = self.record(text, values)
        self.assertEqual((result.ok, result.cause), (False, "recorder_identity_mismatch"))
        self.assertEqual(self.snapshot(), before)

    def test_a_renamed_workflow_parameter_fails_closed_before_any_launch(self) -> None:
        script = self.root / "scripts" / "workflow_state.py"
        source = script.read_text()
        self.assertIn("verdict_text: str | None = None", source)
        script.write_text(source.replace("def record_local_plan_review(", "def record_local_plan_review_renamed(", 1)
                          + "\n")
        result = self.check()
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "recorder_interface_changed", None))
        self.assertIn("record_local_plan_review", result.message)

    def test_a_renamed_parameter_of_a_workflow_writer_is_an_interface_change(self) -> None:
        script = self.root / "scripts" / "workflow_state.py"
        source = script.read_text()
        old = "    verdict_text: str | None = None,\n"
        self.assertIn(old, source[source.index("def record_local_plan_review("):])
        head = source.index("def record_local_plan_review(")
        script.write_text(source[:head] + source[head:].replace(old, "    verdict_body: str | None = None,\n", 1))
        result = self.check()
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "recorder_interface_changed", None))
        self.assertIn("workflow_state.record_local_plan_review", result.message)
        self.assertIn("verdict_body", result.message)
        self.assertIn("verdict_text", result.message)

    def test_a_moved_state_is_state_identity_changed_and_nothing_is_written(self) -> None:
        values = self.check().values
        stale = self.identity()
        state = _state(self.root)
        state["work_items"][WID]["state_revision"] += 1
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        before = self.snapshot()
        result = self.record(self.compose(values), values, state_identity=stale)
        self.assertEqual((result.ok, result.cause), (False, "state_identity_changed"))
        self.assertEqual(self.snapshot(), before)

    def test_a_script_changed_between_check_and_record_is_workflow_release_changed(self) -> None:
        values = self.check().values
        identity = self.identity()
        script = self.root / "scripts" / "workflow_backlog.py"
        script.write_text(script.read_text() + "\n# changed\n")
        before = self.snapshot()
        result = self.record(self.compose(values), values, state_identity=identity)
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "workflow_release_changed", "WorkflowReleaseChangedError"))
        self.assertEqual(self.snapshot()[0], before[0])
        self.assertFalse((self.root / FEEDBACK).exists())

    def test_a_verdict_near_the_one_mebibyte_bound_is_recorded(self) -> None:
        values = self.check().values
        text = self.compose(values)
        text = self.compose(values, filler=" " + "x" * (1024 * 1024 - len(text.encode()) - 64))
        self.assertLessEqual(len(text.encode()), 1024 * 1024)
        result = self.record(text, values)
        self.assertTrue(result.ok, result)
        self.assertEqual((self.root / FEEDBACK).read_bytes(), text.encode())

    def test_runner_errors_propagate_unchanged(self) -> None:
        hook = self.root / ".git" / "hooks" / "post-index-change"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        with self.assertRaises(WorkflowQueryError) as caught:
            self.check()
        self.assertEqual(caught.exception.evidence["reason"], "query_git_not_isolated")
        self.assertEqual(caught.exception.evidence["facility"], "hook")

    def test_a_next_action_refusal_other_than_stale_decision_propagates_with_nothing_written(self) -> None:
        values = self.check().values
        before = self.snapshot()
        with mock.patch.object(protocol, "next_action", side_effect=WorkflowQueryError("boom", evidence={})):
            with self.assertRaises(WorkflowQueryError):
                self.record(self.compose(values), values, state_identity="x")
        self.assertEqual(self.snapshot(), before)


class ImplementationStageTest(RecorderCase):
    STAGE = "implementation"

    def test_check_returns_the_implementation_values_and_writes_nothing(self) -> None:
        before = self.snapshot()
        result = self.check()
        self.assertTrue(result.ok, result)
        self.assertEqual(result.values["reviewer_role"], "LOCAL_MODEL_IMPLEMENTATION_REVIEW")
        self.assertEqual(result.values["revision_label"], "Implementation revision reviewed:")
        self.assertEqual(result.values["revision"], "1")
        self.assertEqual(result.values["gate"]["gate"], "technical_approval")
        self.assertEqual(self.snapshot(), before)

    def _matches_in_place(self, status: str, on_recorded: Any = None) -> review_recorder.RecorderResult:
        values = self.check().values
        text = self.compose(values, status)
        head = fixtures.current_head(self.root)
        result = self.record(text, values)
        self.assertTrue(result.ok, result)
        recorded = self.written()
        if on_recorded:
            on_recorded()
        log = _git(self.root, "log", "--format=%H%n%B%x00", f"{head}..HEAD")
        self.setUp()
        fixtures.run_workflow_python(self.root, IN_PLACE, "implementation", status, COMPLETED_AT,
                                     str(values["round"]), str(self.verdict_file(text)))
        self.assertEqual(self.written(), recorded)
        self.assertEqual(_git(self.root, "log", "--format=%B%x00", f"{head}..HEAD").split("\0")[:-1],
                         [chunk.partition("\n")[2] for chunk in log.split("\0")[:-1]])
        return result

    def test_approve_is_left_uncommitted_as_the_command_leaves_it(self) -> None:
        result = self._matches_in_place("APPROVE")
        self.assertEqual(result.values["phase"], "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertIsNone(result.values["committed"])
        self.assertIn(STATE, _git(self.root, "status", "--porcelain"))

    def test_block_changes_no_state(self) -> None:
        result = self._matches_in_place("BLOCK")
        self.assertEqual(result.values["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

    def test_revise_leaves_one_commit_holding_only_the_state_and_a_clean_index(self) -> None:
        head = fixtures.current_head(self.root)
        recorder_head: list[str] = []
        result = self._matches_in_place("REVISE", on_recorded=lambda: recorder_head.append(
            fixtures.current_head(self.root)))
        self.assertEqual(result.values["phase"], "APPLYING_REVIEW_FEEDBACK")
        self.assertEqual(result.values["committed"], recorder_head[0])
        self.setUp()
        values = self.check().values
        self.record(self.compose(values, "REVISE"), values)
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{head}..HEAD").strip(), "1")
        self.assertEqual(_git(self.root, "show", "--name-only", "--format=", "HEAD").split(), [STATE])
        message = _git(self.root, "show", "-s", "--format=%B", "HEAD")
        self.assertEqual(message.strip().splitlines()[-1], f"Workflow-Work-Item: {WID}")
        self.assertEqual(_git(self.root, "diff", "--cached", "--name-only"), "")
        self.assertEqual(_git(self.root, "status", "--porcelain"), "")

    def _pending_revise(self) -> str:
        """The state the in-place command leaves for a manual-ingest `REVISE`: the entry written, nothing committed."""
        values = self.check().values
        text = self.compose(values, "REVISE")
        fixtures.run_workflow_python(
            self.root, IN_PLACE.replace("    ws.commit_pending_applying_review_feedback_entry(root, \"wi-1\", "
                                        "attribution=())\n", "    pass\n"),
            "implementation", "REVISE", COMPLETED_AT, str(values["round"]), str(self.verdict_file(text)))
        return fixtures.current_head(self.root)

    def test_commit_pending_commits_a_pending_entry_alone_and_is_then_a_no_op(self) -> None:
        head = self._pending_revise()
        self.assertIn(STATE, _git(self.root, "status", "--porcelain"))
        result = review_recorder.commit_pending(self.root, WID, digests=self.check().values["digests"])
        self.assertTrue(result.ok, result)
        committed = result.values["committed"]
        self.assertEqual(committed, fixtures.current_head(self.root))
        self.assertEqual(_git(self.root, "rev-parse", f"{committed}^").strip(), head)
        self.assertEqual(_git(self.root, "show", "--name-only", "--format=", "HEAD").split(), [STATE])
        self.assertEqual(_git(self.root, "status", "--porcelain"), "")
        again = review_recorder.commit_pending(self.root, WID)
        self.assertTrue(again.ok, again)
        self.assertIsNone(again.values["committed"])
        self.assertEqual(fixtures.current_head(self.root), committed)

    def test_commit_pending_after_a_controller_recorded_revise_has_nothing_to_commit(self) -> None:
        values = self.check().values
        self.record(self.compose(values, "REVISE"), values)
        head = fixtures.current_head(self.root)
        result = review_recorder.commit_pending(self.root, WID)
        self.assertTrue(result.ok, result)
        self.assertIsNone(result.values["committed"])
        self.assertEqual(fixtures.current_head(self.root), head)

    def test_commit_pending_refusal_is_recorder_commit_pending_and_a_changed_script_set_is_release_changed(self) -> None:
        self._pending_revise()
        state = _state(self.root)
        state["work_items"][WID]["technical_review_block_pins"] = [
            {"bundle_id": "d" * 64, "review_content_id": "e" * 64, "recorded_at": "2026-11-07T08:30:00Z"}]
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        head = fixtures.current_head(self.root)
        result = review_recorder.commit_pending(self.root, WID)
        self.assertEqual((result.ok, result.cause), (False, "recorder_commit_pending"))
        self.assertEqual(fixtures.current_head(self.root), head)
        stale = review_recorder.commit_pending(self.root, WID, digests={"scripts/workflow_state.py": "0" * 64})
        self.assertEqual((stale.ok, stale.cause), (False, "workflow_release_changed"))

    def _payload(self, **changes) -> tuple[Path, dict, dict]:
        before = _entry(self.root)
        after = {**before, **changes}
        path = self._area / "carry-payload.json"
        path.write_text(json.dumps({"before": before, "after": after}))
        return path, before, after

    def test_carry_state_replaces_only_this_items_entry_and_keeps_what_is_there_now(self) -> None:
        path, before, after = self._payload(last_transition="2026-11-09T09:09:09Z")
        concurrent = _state(self.root)
        concurrent["active_work_item_id"] = WID
        (self.root / STATE).write_text(json.dumps(concurrent, indent=2) + "\n")
        result = review_recorder.carry_state(self.root, WID, path)
        self.assertTrue(result.ok, result)
        self.assertTrue(result.values["written"])
        written = _state(self.root)
        self.assertEqual(written["active_work_item_id"], WID)
        self.assertEqual(written["work_items"][WID]["last_transition"], "2026-11-09T09:09:09Z")

    def test_carry_state_is_a_no_op_when_the_entry_already_is_the_workers(self) -> None:
        path, before, after = self._payload(last_transition="2026-11-09T09:09:09Z")
        self.assertTrue(review_recorder.carry_state(self.root, WID, path).values["written"])
        bytes_after = (self.root / STATE).read_bytes()
        again = review_recorder.carry_state(self.root, WID, path)
        self.assertTrue(again.ok, again)
        self.assertFalse(again.values["written"])
        self.assertEqual((self.root / STATE).read_bytes(), bytes_after)

    def test_carry_state_refuses_an_entry_that_is_neither_and_writes_nothing(self) -> None:
        path, before, after = self._payload(last_transition="2026-11-09T09:09:09Z")
        moved = _state(self.root)
        moved["work_items"][WID]["last_transition"] = "2026-11-09T01:01:01Z"
        (self.root / STATE).write_text(json.dumps(moved, indent=2) + "\n")
        snapshot = self.snapshot()
        result = review_recorder.carry_state(self.root, WID, path)
        self.assertEqual((result.ok, result.cause), (False, "carry_managed_changed"))
        self.assertEqual(self.snapshot(), snapshot)
        stale = review_recorder.carry_state(self.root, WID, path, digests={"scripts/workflow_state.py": "0" * 64})
        self.assertEqual((stale.ok, stale.cause), (False, "workflow_release_changed"))

    def _hook_marker(self, name: str) -> Path:
        return self._area / name

    def test_no_hook_runs_for_the_commit(self) -> None:
        marker = self._hook_marker("hook-ran")
        for event in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "reference-transaction"):
            hook = self.root / ".git" / "hooks" / event
            hook.write_text(f"#!/bin/sh\necho {event} >> {marker}\n")
            hook.chmod(0o755)
        operator = self._area / "operator-gitconfig"
        operator.write_text(f'[hook "marker"]\n\tevent = post-commit\n\tcommand = echo configured >> {marker}\n')
        values = self.check().values
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(operator)}):
            result = self.record(self.compose(values, "REVISE"), values)
        self.assertTrue(result.ok, result)
        self.assertFalse(marker.exists())
        self.assertEqual(result.values["committed"], fixtures.current_head(self.root))

    def test_a_repository_hook_command_is_refused_before_the_driver_and_no_commit_is_made(self) -> None:
        values = self.check().values
        identity = self.identity()
        _git(self.root, "config", "hook.planted.event", "post-commit")
        _git(self.root, "config", "hook.planted.command", "true")
        head = fixtures.current_head(self.root)
        before = self.snapshot()
        with self.assertRaises(ControllerError) as caught:
            self.record(self.compose(values, "REVISE"), values, state_identity=identity)
        self.assertIn(caught.exception.evidence["reason"], ("query_git_not_isolated", "protocol_git_not_isolated"))
        self.assertEqual(fixtures.current_head(self.root), head)
        self.assertEqual(self.snapshot()[0], before[0])

    def test_an_implementation_item_the_command_routes_to_its_advisory_branch_is_unsupported(self) -> None:
        for phase in ("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK"):
            with self.subTest(phase=phase):
                self.setUp()
                state = _state(self.root)
                state["work_items"][WID]["phase"] = phase
                (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
                result = self.check()
                self.assertEqual((result.ok, result.cause), (False, "recorder_branch_unsupported"))

    def test_staged_content_is_recorder_index_dirty_at_check(self) -> None:
        (self.root / "stray.txt").write_text("x\n")
        _git(self.root, "add", "stray.txt")
        before = self.snapshot()
        result = self.check()
        self.assertEqual((result.ok, result.cause), (False, "recorder_index_dirty"))
        self.assertEqual(self.snapshot(), before)

    def test_a_file_staged_between_check_and_record_is_recorder_index_dirty_and_nothing_is_written(self) -> None:
        values = self.check().values
        text = self.compose(values, "REVISE")
        (self.root / "stray.txt").write_text("x\n")
        _git(self.root, "add", "stray.txt")
        before = self.snapshot()
        result = self.record(text, values)
        self.assertEqual((result.ok, result.cause), (False, "recorder_index_dirty"))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / FEEDBACK).exists())

    def test_a_stray_uncommitted_field_leaves_the_state_written_and_the_commit_pending(self) -> None:
        values = self.check().values
        state = _state(self.root)
        state["work_items"][WID]["technical_review_block_pins"] = [
            {"bundle_id": "d" * 64, "review_content_id": "e" * 64, "recorded_at": "2026-11-07T08:30:00Z"}]
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        head = fixtures.current_head(self.root)
        result = self.record(self.compose(values, "REVISE"), values)
        self.assertFalse(result.ok, result)
        self.assertEqual(result.cause, "recorder_commit_pending")
        self.assertEqual(result.workflow_class, "ReviewStageWriteNotCommittableError")
        self.assertEqual(_entry(self.root)["phase"], "APPLYING_REVIEW_FEEDBACK")
        self.assertTrue((self.root / FEEDBACK).is_file())
        self.assertEqual(fixtures.current_head(self.root), head)
        # Resolving the stray field lets the next role's commit succeed.
        state = _state(self.root)
        state["work_items"][WID]["technical_review_block_pins"] = []
        (self.root / STATE).write_text(json.dumps(state, indent=2) + "\n")
        fixtures.run_workflow_python(
            self.root, "from pathlib import Path\nws.commit_pending_applying_review_feedback_entry("
                       "Path.cwd(), 'wi-1', attribution=())\n")
        self.assertNotEqual(fixtures.current_head(self.root), head)

    def test_a_manifest_that_no_longer_matches_the_bundle_is_an_identity_mismatch(self) -> None:
        manifest = self.root / ".ai-review" / WID / "current" / "MANIFEST.md"
        manifest.write_text(manifest.read_text().replace("bundle_id: ", "bundle_id: 0", 1))
        before = self.snapshot()
        result = self.check()
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "recorder_identity_mismatch", "ImplementationReviewBundleUnverifiedError"))
        self.assertEqual(self.snapshot(), before)

    def test_a_rejected_bundle_is_the_workflows_own_refusal(self) -> None:
        fixtures.write_rejected_marker(self.root, WID, scoped=True)
        result = self.check()
        self.assertEqual((result.ok, result.cause, result.workflow_class),
                         (False, "workflow_refused", "BundleRejectedError"))


class CausesTest(unittest.TestCase):
    def test_every_cause_is_listed_once(self) -> None:
        self.assertEqual(len(set(review_recorder.CAUSES)), len(review_recorder.CAUSES))

    def test_the_driver_names_only_listed_causes(self) -> None:
        import re
        named = set(re.findall(r'"(recorder_[a-z_]+|state_identity_changed|workflow_release_changed|workflow_refused)"',
                               review_recorder.DRIVER_SOURCE))
        self.assertLessEqual(named, set(review_recorder.CAUSES))


if __name__ == "__main__":
    unittest.main()
