"""The Workflow 2.5.1 → 2.6.0 migration scenarios M1-M5
(``docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md``, Design F,
``workflow-controller-workflow-2-6-integration`` CP5).

Every scenario runs in a disposable repository built from the vendored
release trees (``tests/workflow_releases/``): the real 2.5.1 and then 2.6.0
scripts and plan-bundle generator, the executable fake ``gh``
(``tests/fake_gh.py``) for the forge, and the scripted fake worker
(``tests/fake_claude.py``) where a dispatch runs. A worker's Workflow write
is the state the real writer produces, computed by the test with the real
script (:meth:`_MigrationCase.real_write`).

**The update step** (:func:`update_workflow`) reproduces the
Controller-relevant subset of Workflow Manager's ``update`` write set: every
vendored managed file (the seventeen command files and the three scripts) is
replaced with the 2.6.0 bytes and ``installation.json`` is rewritten to
2.6.0, then committed as the operator would, since the Manager commits
nothing. ``tests/test_integration_disposable_repo.py``'s
``RealManagerMigrationTest`` runs M1 against the real Manager's full write
set, locally.

- M1 (:class:`BoundPlanReviewMigrationTest`): a bound milestone with a
  Draft PR at ``AWAITING_LOCAL_PLAN_REVIEW``, updated in place on its branch;
  M1a mid-revision, M1b a leftover REJECTED marker, M1c a completed 2.5.1
  withdrawal, M1d a leftover marker at ``AWAITING_PLAN_APPROVAL``.
- M2 (:class:`ImplementingMigrationTest`): a bound milestone at
  ``IMPLEMENTING`` after the plan approval and one checkpoint.
- M3 (:class:`TrunkUpdatedMigrationTest`): the trunk updated while a bound
  milestone's branch stays on 2.5.1, then accepted and closed out.
- M4 (:class:`NewScopedItemMigrationTest`): a work item created by 2.6.0
  after the update.
- M5 (:class:`JobStraddlingUpdateTest`): a job launched under 2.5.1 whose
  target is updated before verification.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli, evidence, job, managed_repo, milestone_branch as mb, workflow_contract  # noqa: E402
from controller.errors import WorkflowReleaseChangedError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests import test_lifecycle_orchestration as lifecycle  # noqa: E402
from tests import test_trunk_orchestration_e2e as e2e  # noqa: E402
from tests.test_milestone_branch import policy  # noqa: E402

WI = lifecycle.WI
BRANCH = f"milestone/{WI}"
STATE_REL = lifecycle.STATE_REL
LEGACY = "2.5.1"
UPDATED = "2.6.0"
MANIFEST_REL = ".workflow-manager/installation.json"
REVIEW_PLAN = f"/review-plan {WI}"
APPLY_PLAN_REVIEW = f"/apply-plan-review {WI}"
AWAITING_LOCAL_PLAN = "AWAITING_LOCAL_PLAN_REVIEW"
AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
REVISING_PLAN = "REVISING_PLAN"

#: Records a local plan-review verdict for ``argv[1]`` through the installed
#: release's own writer, against the item's current plan bundle, and prints
#: the bundle and review-content ids it recorded.
RECORD_LOCAL_VERDICT = r"""
import json
wid, verdict = sys.argv[1], sys.argv[2]
root = Path.cwd()
bundle_dir = root / fingerprint.resolve_bundle_dir(root, wid, stage="plan")
bundle_id = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["bundle_id"]
content_id = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)[0]
ws.state_transaction(root, lambda s: ws.record_local_plan_review(
    s, wid, verdict=verdict, bundle_id=bundle_id, review_content_id=content_id, round=1,
    now="2026-09-28T00:00:00Z"))
print(json.dumps({"bundle_id": bundle_id, "review_content_id": content_id}))
"""

#: Records the manual plan-review ``APPROVE`` for ``argv[1]`` through the
#: installed release's own writer (the local ``APPROVE`` already recorded).
RECORD_MANUAL_APPROVE = r"""
wid = sys.argv[1]
root = Path.cwd()
bundle_dir = root / fingerprint.resolve_bundle_dir(root, wid, stage="plan")
bundle_id = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["bundle_id"]
content_id = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)[0]
ws.state_transaction(root, lambda s: ws.record_manual_plan_review(
    s, wid, verdict="APPROVE", bundle_id=bundle_id, round=1, now="2026-09-28T00:00:00Z",
    current_review_content_id=content_id, feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
    feedback_review_content_id=content_id))
"""

#: The entry write of 2.6.0's ``/apply-plan-review`` for a 2.5.1 item
#: mid-round (row 5): its first ``state_transaction``.
ENSURE_BINDING_MARKER = r"""
ws.state_transaction(Path.cwd(), lambda s: ws.ensure_plan_review_binding_marker(
    s, sys.argv[1], "2026-09-28T00:00:00Z"))
"""

#: A 2.5.1 withdrawal of ``argv[1]``'s plan bundle through the real
#: ``withdraw_bundle``; prints the quarantine directory, target-relative.
WITHDRAW_BUNDLE = r"""
root = Path.cwd()
print(fingerprint.withdraw_bundle(root, sys.argv[1], "closing checks failed").relative_to(root))
"""

#: The marker text a 2.5.1 ``withdraw_bundle`` holds between its quarantine
#: rename and its own marker removal: the crash window that leaves one.
CRASHED_WITHDRAWAL_MARKER = ("REJECTED: withdrawal in progress\nreason: closing checks failed\n"
                             "step: MANIFEST.md removed\n")


#: ``/approve-review plan``'s state write for ``argv[1]`` through the
#: installed release's own writers (both plan reviews already approved):
#: the approval record, then ``apply_plan_approval``. Prints the approved
#: ``review_content_id``. The approval commit is the caller's.
APPLY_PLAN_APPROVAL = r"""
wid = sys.argv[1]
root = Path.cwd()
now = "2026-09-28T00:00:00Z"
bundle_dir = root / fingerprint.resolve_bundle_dir(root, wid, stage="plan")
bundle_id = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["bundle_id"]
content_id, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
record = ws.build_approval_record(
    basis="EXTERNAL_APPROVE", stage="plan", user_confirmation=f"I approve the plan stage for {wid}", now=now,
    reviewed_bundle_id=bundle_id, approved_review_content_id=content_id,
    review_content_manifest=projection["review_content_manifest"])
ws.state_transaction(root, lambda s: ws.apply_plan_approval(s, wid, record, now))
print(content_id)
"""

#: ``/milestone-implement``'s step 1 for checkpoint ``argv[2]`` of
#: ``argv[1]``, through the installed release's own writers: identity,
#: claim, ``IN_PROGRESS`` under the guard, the change (``argv[3]``), then
#: ``complete_checkpoint`` and the checkpoint commit with its trailers under
#: the guard, and the release once the completion is durable.
IMPLEMENT_CHECKPOINT = r"""
import json
import subprocess
wid, cid, path = sys.argv[1], sys.argv[2], sys.argv[3]
root = Path.cwd()
now = "2026-09-28T00:00:00Z"


def git(*args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


registry = json.loads((root / f"docs/ai-workflow/registry/{wid}-registry.json").read_text())
ws.write_worktree_identity(root, wid, now=now)
token = ws.claim_checkpoint(root, wid, cid, now=now)["owner_token"]
with ws.owner_mutation(root, wid, token, checkpoint_id=cid, step="1d", step_class=ws.DESTRUCTIVE, now=now):
    ws.write_worktree_identity(root, wid, now=now)
    head = git("rev-parse", "HEAD")
    ws.state_transaction(root, lambda s: ws.transition_checkpoint_in_progress(s, wid, cid, start_commit=head, now=now))
(root / path).parent.mkdir(parents=True, exist_ok=True)
(root / path).write_text(f"{cid}'s change\n")
with ws.owner_mutation(root, wid, token, checkpoint_id=cid, step="1f-commit", step_class=ws.DESTRUCTIVE, now=now):
    ws.state_transaction(root, lambda s: ws.complete_checkpoint(s, wid, cid, registry, now=now, repo_root=root))
    git("add", "--", path, "docs/ai-workflow/WORKFLOW_STATE.json")
    git("commit", "-q", "-m", f"Implement {cid}\n\nWorkflow-Checkpoint: {cid}\nWorkflow-Work-Item: {wid}")
assert ws.committed_checkpoint_status(root, wid, cid) == "COMPLETE"
ws.release_checkpoint(root, wid, cid, owner_token=token, now=now)
"""

#: ``/milestone-implement``'s steps 1d.1-1d.2 for checkpoint ``argv[2]``:
#: the identity write and the claim, through the installed release; prints
#: the claim record.
CLAIM_CHECKPOINT = r"""
import json
wid, cid = sys.argv[1], sys.argv[2]
root = Path.cwd()
ws.write_worktree_identity(root, wid, now="2026-09-28T01:00:00Z")
print(json.dumps(ws.claim_checkpoint(root, wid, cid, now="2026-09-28T01:00:00Z")))
"""


def vendored_paths(release: str) -> list[str]:
    """The target paths of ``release``'s vendored managed files."""
    return sorted(fixtures.workflow_release_files(release))


def update_workflow(root: Path) -> str:
    """The update step: ``root``'s vendored managed files replaced with the
    2.6.0 bytes and ``installation.json`` rewritten, which is all of
    Workflow Manager's ``update`` write set the Controller reads; then (the
    Manager commits nothing) the operator commits exactly those paths.
    Returns the commit."""
    fixtures.install_workflow_release(root, UPDATED)
    return fixtures.commit_paths(root, "Update Workflow to 2.6.0", *vendored_paths(UPDATED), MANIFEST_REL)


def publication_status(root: Path, work_item_id: str = WI):
    return workflow_contract.plan_review_publication_status(
        root, workflow_contract.contract_for(UPDATED), work_item_id)


def feedback_path(root: Path, work_item_id: str = WI):
    return workflow_contract.resolve_feedback_path(root, workflow_contract.contract_for(UPDATED), work_item_id)


def rule_feedback_dir(root: Path, work_item_id: str = WI) -> str:
    """Where the 2.5.1 contract's own rule finds the feedback directory."""
    return str(evidence.resolve_feedback_dir(root, work_item_id, workflow_contract.bind_release(LEGACY)))


def publication_line(row: str, status: str) -> str:
    return f"plan-review publication status: row {row} ({status})"


def run_generator(root: Path, *args: str) -> subprocess.CompletedProcess:
    """The target's own ``scripts/prepare-ai-review.sh``, run as an operator
    would, with no bytecode written into the target."""
    return subprocess.run(["./scripts/prepare-ai-review.sh", *args], cwd=root, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, check=False, timeout=120,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


class QuerySpy:
    """Every Workflow query the Controller executes while installed: the
    query option of each run (``--resolve-feedback-path`` or
    ``--plan-review-publication-status``), through the private runner hook,
    which replaces only the execution step (the query still runs)."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.queries: list[str] = []
        real = workflow_contract._execute_query

        def execute(argv, *, cwd, timeout):
            self.queries.append(argv[5].split("=", 1)[0])
            return real(argv, cwd=cwd, timeout=timeout)

        patcher = unittest.mock.patch.object(workflow_contract, "_execute_query", execute)
        patcher.start()
        test.addCleanup(patcher.stop)


class _MigrationCase(e2e._E2ECase):
    """A clone of a disposable bare origin whose ``main`` carries the
    vendored Workflow 2.5.1 tree, the Workflow configuration and, unless
    ``with_policy`` is false, the milestone-branch policy; driven through the
    real ``cli.main`` with the fake ``gh`` and the scripted worker
    (``tests/test_trunk_orchestration_e2e``)."""

    def build_target(self, tmp: Path) -> tuple[Path, Path]:
        seed = tmp / "seed"
        seed.mkdir(parents=True)
        fixtures.run(["git", "init", "-q", "--initial-branch=main"], cwd=seed)
        fixtures.run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=seed)
        fixtures.run(["git", "config", "user.name", "Controller Tests"], cwd=seed)
        (seed / "README.md").write_text("Workflow release migration fixture\n")
        (seed / ".gitignore").write_text(".ai-review/\n")
        fixtures.install_workflow_release(seed, LEGACY)
        fixtures.write_workflow_config(seed, {"schema_version": 1, "default_workflow_version": "2.2",
                                              "supported_versions": ["1", "2.1", "2.2"]})
        fixtures.write_workflow_state(seed, {"schema_version": 1, "active_work_item_id": None, "work_items": {}})
        if self.with_policy:
            (seed / e2e.POLICY).parent.mkdir(parents=True, exist_ok=True)
            (seed / e2e.POLICY).write_text(json.dumps(policy(), indent=2) + "\n")
        fixtures.commit_all(seed, "Install Workflow 2.5.1")
        origin = tmp / "origin.git"
        fixtures.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)])
        fixtures.run(["git", "config", "core.logAllRefUpdates", "always"], cwd=origin)
        fixtures.run(["git", "push", "-q", str(origin), "main"], cwd=seed)
        clone = tmp / "clone"
        fixtures.run(["git", "clone", "-q", str(origin), str(clone)])
        fixtures.run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=clone)
        fixtures.run(["git", "config", "user.name", "Controller Tests"], cwd=clone)
        return origin, clone.resolve()

    # -- Workflow, for real -----------------------------------------------------------------

    def seed_item(self, stage: str, *, work_item_id: str = WI, **kwargs) -> str:
        """``work_item_id`` seeded to ``stage`` by the installed release's own
        writers and generator (``fixtures.WORKFLOW_SEED_SCRIPT``), based at
        ``HEAD``, which it returns."""
        base = fixtures.current_head(self.root)
        fixtures.run_workflow_seed(self.root, stage, work_item_id=work_item_id, base_commit=base, **kwargs)
        return base

    def workflow(self, code: str, *args: str) -> str:
        """Run ``code`` against the installed release's own scripts; its stdout."""
        return fixtures.run_workflow_python(self.root, code, *args).stdout

    def real_write(self, code: str, *args: str) -> tuple[str, str]:
        """The state the installed release's writer ``code`` produces, and
        its stdout, with the working tree's state restored: what a scripted
        worker then writes."""
        state = self.root / STATE_REL
        before = state.read_bytes()
        out = self.workflow(code, *args)
        after = state.read_text()
        state.write_bytes(before)
        return after, out

    def withdraw(self, *, crashed: bool) -> str:
        """The real 2.5.1 ``withdraw_bundle``; ``crashed`` plants the marker
        it holds between its quarantine rename and its own marker removal."""
        quarantine = self.workflow(WITHDRAW_BUNDLE, WI).strip()
        self.assertFalse((self.root / f".ai-review/{WI}/REJECTED").exists())
        if crashed:
            (self.root / f".ai-review/{WI}/REJECTED").write_text(CRASHED_WITHDRAWAL_MARKER)
        return quarantine

    # -- the Controller ---------------------------------------------------------------------

    def preflight(self) -> mb.Proceed | mb.Gate:
        """The Controller's own repository preflight, as a step runs it
        before deciding: binds, syncs and opens the pull request."""
        with unittest.mock.patch.dict(os.environ, self.gh_env):
            return mb.repository_preflight(mb.Context(repo_root=self.root, runtime_root=self.lc.runtime))

    def record_bytes(self) -> bytes:
        return (self.lc.runtime / mb.record_rel(self.key, WI)).read_bytes()

    def explained(self) -> dict:
        result = self.cli("explain", json_out=True)
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assertEqual(result.records, [])
        return json.loads(result.stdout)

    def inspected(self) -> managed_repo.ManagedRepository:
        return managed_repo.inspect(self.root, manager_bin=str(self.stub_manager))

    def bind_with_draft_pr(self) -> None:
        """The milestone bound to its branch, with a Draft PR: the preflight
        binds, the operator commits the milestone narrative (excluded from
        the plan-stage content), and the next preflight pushes the branch
        and opens the pull request."""
        bound = self.preflight()
        self.assertEqual((bound.action, self.record()["state"], self.head().branch),
                         ("bound", mb.BRANCH_BOUND, BRANCH))
        (self.root / "docs" / "ACTIVE_MILESTONE.md").write_text(f"# Active milestone\n\n{WI}\n")
        fixtures.commit_paths(self.root, f"Start the {WI} narrative", "docs/ACTIVE_MILESTONE.md")
        opened = self.preflight()
        self.assertEqual((opened.action, self.record()["state"]), ("pr_created", mb.PR_OPEN))
        [pr] = self.gh_state()["prs"]
        self.assertTrue(pr["isDraft"])

    def assert_decision(self, explained: dict, action: str | None, *, gate: str | None = None) -> None:
        self.assertEqual(explained["action"], action, explained)
        self.assertEqual(explained["automatic"], action is not None, explained)
        if gate is not None:
            self.assertEqual(explained["gate"]["safe_resume_command"], gate)

    def execute(self, managed: managed_repo.ManagedRepository) -> dict:
        """One ``job.execute_step`` against ``managed``, as ``step`` runs it."""
        fixtures.write_worker_script(self.lc.script_path, self.lc.script)
        with unittest.mock.patch.dict(os.environ, self.env()):
            return job.execute_step(managed, identity=self.ident, runtime=self.lc.runtime,
                                    claude_bin=str(lifecycle.FAKE_CLAUDE), timeout=60)

    def job_records(self) -> list[dict]:
        jobs = self.lc.runtime / "jobs"
        return [json.loads(p.read_text()) for p in sorted(jobs.glob("*.json"))] if jobs.is_dir() else []

    def assert_admitted(self) -> None:
        self.assertEqual(self.inspected().workflow_version, UPDATED)
        self.assertEqual(self.cli("inspect", json_out=True).code, cli.EXIT_OK)


# ---------------------------------------------------------------------------
# M1 and its variants.
# ---------------------------------------------------------------------------


class BoundPlanReviewMigrationTest(_MigrationCase):
    """M1: a bound milestone with a Draft PR at ``AWAITING_LOCAL_PLAN_REVIEW``,
    its real 2.5.1-generated plan bundle, updated to 2.6.0 in place."""

    def at_ready(self, *, scoped_directory: bool = False) -> str:
        base = self.seed_item("ready")
        if scoped_directory:
            (self.root / ".ai-review" / WI / "feedback").mkdir(parents=True)
        self.bind_with_draft_pr()
        return base

    def updated_in_place(self, layout: str, *, scoped_directory: bool) -> None:
        """M1's assertions for one legacy feedback layout."""
        base = self.at_ready(scoped_directory=scoped_directory)
        before = self.explained()
        self.assert_decision(before, REVIEW_PLAN)
        legacy_dir = rule_feedback_dir(self.root)
        record = self.record_bytes()

        update = update_workflow(self.root)
        self.assertEqual(self.record_bytes(), record)
        self.assert_admitted()
        answer = feedback_path(self.root)
        self.assertEqual((answer.layout, answer.feedback_dir), (layout, legacy_dir))
        status = publication_status(self.root)
        self.assertEqual((status.phase, status.row, status.status), (AWAITING_LOCAL_PLAN, "3", "BOUND"))
        after = self.explained()
        self.assert_decision(after, before["action"])
        self.assertIn(publication_line("3", "BOUND"), after["evidence"])

        # The dispatched /review-plan worker records a local REVISE
        # through the real 2.6.0 writer.
        revised, out = self.real_write(RECORD_LOCAL_VERDICT, WI, "REVISE")
        ids = json.loads(out)
        verdict = fixtures.build_review_feedback_text(
            status="REVISE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id=ids["bundle_id"],
            reviewed_base_commit=base, work_item=WI, reviewed_content_id=ids["review_content_id"])
        self.lc.add(REVIEW_PLAN, [fixtures.script_write(STATE_REL, revised),
                                  fixtures.script_write(answer.review_feedback_path, verdict)])
        reviewed = self.step(cli.EXIT_OK)
        self.assert_launched(reviewed, REVIEW_PLAN, REVISING_PLAN)
        self.assertEqual(reviewed.records[-1]["target_workflow_version"], UPDATED)
        # Branch sync and PR verification proceeded on the updated branch.
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), update)
        self.assertEqual([pr["number"] for pr in self.gh_state()["prs"]], [1])
        self.assertEqual(len(self.gh_calls("pr", "create")), 1)

        status = publication_status(self.root)
        self.assertEqual((status.phase, status.row, status.status), (REVISING_PLAN, "10", "NEEDS_EDIT"))
        binding = fixtures.state_entry(self.root, WI)["plan_review_binding"]
        self.assertEqual(binding["status"], "CONSUMED")
        self.assertFalse(binding["consumed"]["legacy"])
        next_step = self.explained()
        self.assert_decision(next_step, APPLY_PLAN_REVIEW)
        self.assertIn(publication_line("10", "NEEDS_EDIT"), next_step["evidence"])

    def test_m1_legacy_flat_updated_in_place(self) -> None:
        self.updated_in_place("legacy-flat", scoped_directory=False)

    def test_m1_legacy_scoped_updated_in_place(self) -> None:
        """The variant with ``.ai-review/<id>/feedback/`` present."""
        self.updated_in_place("legacy-scoped", scoped_directory=True)

    def test_m1a_a_2_5_1_revise_mid_round(self) -> None:
        """A local ``REVISE`` recorded under 2.5.1 before the update: row 5.
        The dispatched ``/apply-plan-review`` makes only 2.6.0's entry write
        (the legacy marker, row 11) and stays at ``REVISING_PLAN``, so its
        job fails verification, the ``run`` stops, and the next invocation
        dispatches the same command again."""
        self.seed_item("revise")
        self.bind_with_draft_pr()
        self.assert_decision(self.explained(), APPLY_PLAN_REVIEW)
        update_workflow(self.root)
        self.assert_admitted()
        status = publication_status(self.root)
        self.assertEqual((status.phase, status.row, status.status), (REVISING_PLAN, "5", "LEGACY_UNMARKED"))
        self.assert_decision(self.explained(), APPLY_PLAN_REVIEW)

        marked, _ = self.real_write(ENSURE_BINDING_MARKER, WI)
        self.lc.add(APPLY_PLAN_REVIEW, [fixtures.script_write(STATE_REL, marked)])
        run = self.cli("run")
        self.assertEqual(run.code, cli.EXIT_WORKER_FAILED, run.stderr)
        [failed] = run.records
        self.assertEqual(failed["selected_action"]["command"], APPLY_PLAN_REVIEW)
        self.assert_failed(failed, "phase_not_in_to_any_of")
        self.assertEqual(self.processes(self.lc), 1)
        status = publication_status(self.root)
        self.assertEqual((status.phase, status.row, status.status), (REVISING_PLAN, "11", "EDIT_IN_PROGRESS"))
        binding = fixtures.state_entry(self.root, WI)["plan_review_binding"]
        self.assertEqual(binding["status"], "CONSUMED")
        self.assertTrue(binding["consumed"]["legacy"])

        self.lc.add(APPLY_PLAN_REVIEW, [])
        again = self.step(cli.EXIT_WORKER_FAILED)
        self.assertEqual(again.records[-1]["selected_action"]["command"], APPLY_PLAN_REVIEW)
        self.assertIn(publication_line("11", "EDIT_IN_PROGRESS"), again.records[-1]["selected_action"]["evidence"])
        self.assertEqual(self.processes(self.lc), 2)

    def rejected_gate_after_update(self, phase: str) -> tuple[dict, tuple[str, ...]]:
        """After the update, the REJECTED-marker gate fires ahead of the
        publication-status query (which never runs), with the plan-stage
        branch: the marker-clearing clause, then the author-file steps in
        ``current/`` (no ``plan-inputs/`` exists) and the generator."""
        update_workflow(self.root)
        self.assert_admitted()
        self.assertFalse((self.root / ".ai-review" / WI / "plan-inputs").exists())
        spy = QuerySpy(self)
        explained = self.explained()
        self.assertNotIn("--plan-review-publication-status", spy.queries)
        self.assertEqual(explained["observed_phase"], phase)
        self.assertIsNone(explained["action"])
        gate = explained["gate"]
        self.assertTrue(gate["artifact_path"].endswith(f".ai-review/{WI}/REJECTED"), gate)
        self.assertEqual(len(explained["evidence"]), 1)
        self.assertTrue(explained["evidence"][0].startswith("REJECTED marker present at "))
        clause, numbered = gate["safe_resume_command"].split("; 0. ", 1)
        steps = ("0. " + numbered).split("; ")
        self.assertTrue(clause.startswith("resolve the failure the REJECTED marker"), clause)
        self.assertIn(CRASHED_WITHDRAWAL_MARKER.strip().splitlines()[0], clause)
        current = f".ai-review/{WI}/current"
        self.assertTrue(steps[0].startswith(f"0. write {current}/CONTEXT_FILES.txt, restoring the previous "
                                            f"round's author files from .ai-review/{WI}/current.rejected-"), steps)
        self.assertTrue(steps[1].startswith(f"1. write {current}/REVIEW_REQUEST.md"), steps)
        self.assertTrue(steps[2].startswith(f"2. write {current}/TEST_RESULTS.md"), steps)
        self.assertTrue(steps[3].startswith("3. run scripts/prepare-ai-review.sh "), steps)
        self.assertEqual(len(steps), 4)
        self.assertNotIn("/milestone-plan", gate["safe_resume_command"])
        return gate, tuple(steps)

    def copy_of_target(self) -> Path:
        """A separate copy of the target's working tree, for a remedy that
        must not touch the target itself."""
        copy = self.tmp_root / "copy"
        shutil.copytree(self.root, copy, symlinks=True)
        return copy

    def assert_bare_generator_fails(self, base: str) -> subprocess.CompletedProcess:
        copy = self.copy_of_target()
        result = run_generator(copy, base, "plan", WI)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def assert_recovered_to_row_3(self, phase: str) -> None:
        self.assertFalse((self.root / ".ai-review" / WI / "REJECTED").exists())
        status = publication_status(self.root)
        self.assertEqual((status.phase, status.row, status.status), (phase, "3", "BOUND"))

    def test_m1b_a_leftover_rejected_marker_at_local_plan_review(self) -> None:
        self.seed_item("ready")
        self.withdraw(crashed=True)
        self.bind_with_draft_pr()
        _gate, steps = self.rejected_gate_after_update(AWAITING_LOCAL_PLAN)
        fixtures.carry_out_plan_recovery_steps(self.root, WI, steps)
        self.assert_recovered_to_row_3(AWAITING_LOCAL_PLAN)
        self.assert_decision(self.explained(), REVIEW_PLAN)

    def test_m1c_a_completed_2_5_1_withdrawal_at_local_plan_review(self) -> None:
        """No marker (a completed withdrawal removes its own): row 4c, and
        the stale-plan-bundle gate's author-file steps, which Workflow's
        bare remedy cannot replace."""
        base = self.seed_item("ready")
        quarantine = self.withdraw(crashed=False)
        self.bind_with_draft_pr()
        update_workflow(self.root)
        self.assert_admitted()
        status = publication_status(self.root)
        self.assertEqual((status.phase, status.row, status.status), (AWAITING_LOCAL_PLAN, "4c", "LEGACY_UNVERIFIED"))
        explained = self.explained()
        self.assertIsNone(explained["action"])
        self.assertEqual(explained["evidence"][0], publication_line("4c", "LEGACY_UNVERIFIED"))
        gate = explained["gate"]
        self.assertIn(status.remedy, gate["what_is_required"])
        self.assertIn(status.detail, gate["what_is_required"])
        steps = tuple(gate["safe_resume_command"].split("; "))
        self.assertEqual(steps[0], f"0. write .ai-review/{WI}/current/CONTEXT_FILES.txt, restoring the previous "
                                   f"round's author files from {quarantine}/")
        self.assertTrue(steps[-1].startswith(f"3. run scripts/prepare-ai-review.sh {base} plan {WI} -- "), steps)
        self.assertNotIn("/milestone-plan", gate["safe_resume_command"])

        # Workflow's bare remedy alone cannot succeed: the author files are gone.
        self.assertIn(f"regenerate the bundle (./scripts/prepare-ai-review.sh <base> plan {WI})", status.remedy)
        bare = self.assert_bare_generator_fails(base)
        self.assertIn("MissingReviewContentIdStatementError", bare.stdout + bare.stderr)

        fixtures.carry_out_plan_recovery_steps(self.root, WI, steps)
        self.assert_recovered_to_row_3(AWAITING_LOCAL_PLAN)
        self.assert_decision(self.explained(), REVIEW_PLAN)

    def test_m1d_a_leftover_rejected_marker_at_plan_approval(self) -> None:
        """Both 2.5.1 plan reviews approved, then a crashed withdrawal: the
        REJECTED-marker gate at ``AWAITING_PLAN_APPROVAL`` takes the
        plan-stage branch, never the bare generator, and following it lets
        the real 2.6.0 ``/approve-review plan`` bundle check accept the item."""
        base = self.seed_item("ready")
        self.workflow(RECORD_LOCAL_VERDICT, WI, "APPROVE")
        self.workflow(RECORD_MANUAL_APPROVE, WI)
        self.assertEqual(fixtures.state_entry(self.root, WI)["phase"], AWAITING_PLAN_APPROVAL)
        self.withdraw(crashed=True)
        self.bind_with_draft_pr()
        gate, steps = self.rejected_gate_after_update(AWAITING_PLAN_APPROVAL)
        self.assertNotEqual(gate["safe_resume_command"], f"scripts/prepare-ai-review.sh {base} plan {WI}")
        self.assert_bare_generator_fails(base)

        fixtures.carry_out_plan_recovery_steps(self.root, WI, steps)
        self.assert_recovered_to_row_3(AWAITING_PLAN_APPROVAL)
        self.assertEqual(self.workflow(
            "import json\nprint(json.dumps(ws.assert_plan_review_bundle_bound(Path.cwd(), sys.argv[1])))", WI,
        ).strip(), "null")
        explained = self.explained()
        self.assertIsNone(explained["action"])
        self.assertEqual(explained["gate"]["safe_resume_command"], f"/approve-review plan {WI}")
        self.assertEqual(explained["evidence"], [publication_line("3", "BOUND")])


# ---------------------------------------------------------------------------
# M2.
# ---------------------------------------------------------------------------


class ImplementingMigrationTest(_MigrationCase):
    """M2: as M1, but at ``IMPLEMENTING`` after the plan approval and one
    completed checkpoint under 2.5.1, all through 2.5.1's own writers."""

    def test_m2_implementing_bound_milestone_updated_in_place(self) -> None:
        self.seed_item("ready", checkpoint_ids=("CP1", "CP2"))
        bound = self.preflight()
        self.assertEqual((bound.action, self.head().branch), ("bound", BRANCH))
        self.workflow(RECORD_LOCAL_VERDICT, WI, "APPROVE")
        self.workflow(RECORD_MANUAL_APPROVE, WI)
        approved = self.workflow(APPLY_PLAN_APPROVAL, WI).strip()
        plan_paths = [f"docs/ai-workflow/{WI}-PLAN.md", fixtures.registry_rel_path(WI),
                      f"docs/ai-workflow/requirements/{WI}-mapping.json",
                      f"docs/ai-workflow/registry/{WI}-artifacts.json", STATE_REL]
        approval = fixtures.commit_paths(self.root, fixtures.trailer_message(
            f"Approve plan for {WI}", ("Workflow-Plan-Approval", approved), ("Workflow-Work-Item", WI)), *plan_paths)
        self.workflow(IMPLEMENT_CHECKPOINT, WI, "CP1", "src/cp1.txt")
        entry = fixtures.state_entry(self.root, WI)
        self.assertEqual((entry["phase"], entry["checkpoints"]["CP1"]["status"]), ("IMPLEMENTING", "COMPLETE"))
        opened = self.preflight()
        self.assertEqual((opened.action, self.record()["state"]), ("pr_created", mb.PR_OPEN))
        self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), fixtures.current_head(self.root))
        self.assertEqual(fixtures.run(["git", "merge-base", "--is-ancestor", approval, "HEAD"], cwd=self.root,
                                      check=False).returncode, 0)
        before = self.explained()
        self.assert_decision(before, f"/milestone-implement {WI}")
        legacy_dir = rule_feedback_dir(self.root)
        record = self.record_bytes()

        update_workflow(self.root)
        self.assertEqual(self.record_bytes(), record)
        self.assert_admitted()
        after = self.explained()
        self.assert_decision(after, before["action"])
        self.assertEqual(after["evidence"], before["evidence"])
        answer = feedback_path(self.root)
        self.assertEqual((answer.layout, answer.feedback_dir), ("legacy-flat", legacy_dir))

        # The real 2.6.0 claim takes its new repository-global lifecycle
        # lock, under the git common dir, which 2.5.1 never wrote.
        common_dir = Path(fixtures.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                       cwd=self.root).stdout.strip())
        claims = common_dir / "ai-workflow" / "checkpoint-claims"
        self.assertEqual(sorted(claims.glob("*.lifecycle.lock")), [])
        claim = json.loads(self.workflow(CLAIM_CHECKPOINT, WI, "CP2"))
        self.assertEqual((claim["work_item_id"], claim["checkpoint_id"]), (WI, "CP2"))
        self.assertEqual(len(list(claims.glob("*.lifecycle.lock"))), 1)


# ---------------------------------------------------------------------------
# M3.
# ---------------------------------------------------------------------------


class TrunkUpdatedMigrationTest(_MigrationCase):
    """M3: the trunk is updated to 2.6.0 while a bound milestone's branch
    stays on 2.5.1, and the milestone is later accepted. The scripted
    lifecycle is ``tests/test_trunk_orchestration_e2e``'s."""

    def update_trunk(self) -> str:
        """Someone updates Workflow on the origin's ``main`` and pushes."""
        human = self.human()
        update = update_workflow(human)
        self.git("push", "-q", "origin", "main", cwd=human)
        return update

    def test_m3_the_branch_keeps_2_5_1_integration_is_manual_and_close_out_refuses_the_decision(self) -> None:
        spy = QuerySpy(self)
        self.drive_to_pr()
        update = self.update_trunk()
        self.drive_implementation()
        observation = self.record()["last_observation"]
        self.assertEqual((observation["remote_trunk"], observation["fresh"]), (update, False))
        accepted = self.accept()
        self.gh_edit(1, checks=e2e.PASSING_CHECKS)
        gated = self.step(cli.EXIT_GATE)
        self.assert_branch_gate(gated, mb.GATE_INTEGRATION_REQUIRED)
        self.assertIn("Workflow 2.5.1 and 2.6.0 have no transition that moves a work item's base, so the "
                      "Controller does not integrate", gated.records[-1]["human_gate_pending"]["what_is_required"])
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.assertEqual(fixtures.current_head(self.root), accepted)
        # On the branch every step ran under the branch's own 2.5.1 contract.
        self.assertEqual(spy.queries, [])
        records = self.job_records()
        self.assertTrue(records)
        self.assertEqual({record["target_workflow_version"] for record in records}, {LEGACY})
        self.assertEqual(json.loads((self.root / MANIFEST_REL).read_text())["workflow_version"], LEGACY)

        # The documented manual merge: ready, and "Create a merge commit".
        self.gh_edit(1, isDraft=False)
        merge_commit = self.human_merge(1)
        managed = self.inspected()
        self.assertEqual(managed.workflow_version, LEGACY)
        workers = self.processes(self.lc)
        with self.assertRaises(WorkflowReleaseChangedError) as caught:
            self.execute(managed)
        found = caught.exception.evidence
        self.assertEqual((found["admitted"], found["installed"]), (LEGACY, UPDATED))
        self.assertEqual((found["preflight_action"], found["preflight_gate"]), ("closed_out", None))
        self.assertIn("closed", found["preflight_events"])
        self.assertIn("A milestone close-out completed and was recorded.", caught.exception.message)
        self.assertIn("Only the decision was refused", caught.exception.message)
        # The close-out converged and is recorded; nothing was decided under 2.5.1.
        self.assertEqual(self.record()["state"], mb.CLOSED)
        self.assertEqual(self.record()["merged_head"], accepted)
        self.assertEqual((self.head().branch, self.head().commit), ("main", merge_commit))
        self.assertEqual(len(self.job_records()), len(records))
        self.assertEqual(self.processes(self.lc), workers)
        self.assertEqual(spy.queries, [])

        # The next invocation's fresh inspect, on the trunk, admits 2.6.0.
        inspected = self.cli("inspect", json_out=True)
        self.assertEqual(inspected.code, cli.EXIT_OK, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)["repository"]["workflow_version"], UPDATED)
        self.assertEqual(self.cli("explain").code, cli.EXIT_OK)


# ---------------------------------------------------------------------------
# M4.
# ---------------------------------------------------------------------------


class NewScopedItemMigrationTest(_MigrationCase):
    """M4: after the update, a work item created by the real 2.6.0
    ``route_work_item`` is stamped ``scoped``, and the Controller reads its
    feedback where Workflow says it is, before the directory exists."""

    with_policy = False

    def test_m4_a_new_item_resolves_the_scoped_path_before_its_directory_exists(self) -> None:
        update_workflow(self.root)
        self.seed_item("ready")
        self.assertEqual(fixtures.state_entry(self.root, WI)["feedback_layout"], "scoped")
        scoped = self.root / ".ai-review" / WI / "feedback"
        self.assertFalse(scoped.exists())
        answer = feedback_path(self.root)
        self.assertEqual((answer.layout, answer.feedback_dir), ("scoped", f".ai-review/{WI}/feedback"))
        # The 2.5.1 rule would have said flat: the defect this replaces.
        self.assertEqual(rule_feedback_dir(self.root), ".ai-review/feedback")
        self.assertEqual(str(evidence.resolve_feedback_dir(self.root, WI, workflow_contract.bind_release(UPDATED))),
                         answer.feedback_dir)
        self.assert_decision(self.explained(), REVIEW_PLAN)

        block = fixtures.build_review_feedback_text(status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW",
                                                    work_item=WI)
        # A verdict where the 2.5.1 rule looks is not this item's.
        flat = self.root / ".ai-review" / "feedback" / "REVIEW_FEEDBACK.md"
        flat.parent.mkdir(parents=True)
        flat.write_text(block)
        self.assert_decision(self.explained(), REVIEW_PLAN)
        shutil.rmtree(flat.parent)
        # Written where Workflow says it is, it drives the decision.
        scoped.mkdir(parents=True)
        (scoped / "REVIEW_FEEDBACK.md").write_text(block)
        explained = self.explained()
        self.assert_decision(explained, None, gate=REVIEW_PLAN)
        self.assertTrue(explained["gate"]["artifact_path"].endswith(f".ai-review/{WI}/feedback/REVIEW_FEEDBACK.md"))
        self.assertIn("current-round LOCAL_MODEL_PLAN_REVIEW Status: BLOCK", explained["evidence"])


# ---------------------------------------------------------------------------
# M5.
# ---------------------------------------------------------------------------


class JobStraddlingUpdateTest(_MigrationCase):
    """M5: a ``/review-plan`` job launched under 2.5.1 whose worker's run
    spans a Workflow update (the Manager's overlay of the 2.6.0 files and
    manifest, uncommitted) before the job is verified."""

    with_policy = False
    OVERLAY = "origin/workflow-2.6.0"

    def setUp(self) -> None:
        super().setUp()
        self.base = self.seed_item("ready")
        human = self.human()
        update_workflow(human)
        self.git("push", "-q", "origin", f"HEAD:refs/heads/{self.OVERLAY.split('/', 1)[1]}", cwd=human)
        self.git("fetch", "-q", "origin")
        revised, out = self.real_write(RECORD_LOCAL_VERDICT, WI, "REVISE")
        ids = json.loads(out)
        verdict = fixtures.build_review_feedback_text(
            status="REVISE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id=ids["bundle_id"],
            reviewed_base_commit=self.base, work_item=WI, reviewed_content_id=ids["review_content_id"])
        self.lc.add(REVIEW_PLAN, [
            fixtures.script_write(STATE_REL, revised),
            fixtures.script_write(".ai-review/feedback/REVIEW_FEEDBACK.md", verdict),
            fixtures.script_git("restore", f"--source={self.OVERLAY}", "--worktree", "--",
                                *vendored_paths(UPDATED), MANIFEST_REL),
        ])

    def assert_release_changed(self, record: dict) -> None:
        self.assertEqual(record["selected_action"]["command"], REVIEW_PLAN)
        self.assertEqual(record["target_workflow_version"], LEGACY)
        self.assertEqual(record["observed_phase_after"], REVISING_PLAN)
        self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
        self.assertFalse(record["transition_verified"])
        found = record["reconciliation_evidence"]
        self.assertEqual(found["reason"], "workflow_release_changed")
        self.assertEqual(found["workflow_error"]["code"], "WORKFLOW_RELEASE_CHANGED")
        self.assertEqual((found["workflow_error"]["evidence"]["recorded"],
                          found["workflow_error"]["evidence"]["installed"]), (LEGACY, UPDATED))

    def assert_nothing_pending(self) -> None:
        self.assertEqual(job.pending_reconciliation_jobs(self.lc.runtime, self.inspected(), self.ident), [])

    def test_m5_verification_at_launch_and_at_resume_records_failed(self) -> None:
        self.assert_decision(self.explained(), REVIEW_PLAN)
        launched = self.step(cli.EXIT_WORKER_FAILED)
        [record] = launched.records
        self.assert_release_changed(record)
        self.assertEqual(self.processes(self.lc), 1)
        self.assertEqual(json.loads((self.root / MANIFEST_REL).read_text())["workflow_version"], UPDATED)
        self.assertEqual(self.git("status", "--porcelain", "--", "scripts/workflow_state.py"),
                         "M scripts/workflow_state.py")
        self.assert_nothing_pending()

        # The Controller died before verifying: resume reconciles the same
        # FAILED outcome under the job's recorded 2.5.1, never re-dispatching.
        for status in (job.STATUS_COMPLETED, job.STATUS_LAUNCHED):
            with self.subTest(lost_at=status):
                self.rewrite_as(self.lc, record, status)
                self.assertEqual(len(job.pending_reconciliation_jobs(self.lc.runtime, self.inspected(), self.ident)), 1)
                resumed = self.cli("resume")
                self.assertEqual((resumed.code, resumed.records), (cli.EXIT_OK, []), resumed.stderr)
                self.assertIn(f"{record['job_id']}: {job.STATUS_FAILED}", resumed.stdout)
                self.assert_release_changed(self.read_record(self.lc, record["job_id"]))
                self.assertEqual(self.processes(self.lc), 1)
                self.assert_nothing_pending()

        # Nothing is pending, so the next step decides, under 2.6.0 now.
        explained = self.explained()
        self.assert_decision(explained, APPLY_PLAN_REVIEW)
        self.assertIn(publication_line("5", "LEGACY_UNMARKED"), explained["evidence"])


if __name__ == "__main__":
    unittest.main()
