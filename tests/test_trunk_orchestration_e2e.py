"""End-to-end disposable scenarios of
``workflow-controller-trunk-branch-pr-release-orchestration`` (CP9).

Every lifecycle scenario runs a real ``"2.2"`` target through the real
``cli.main``: a clone of a disposable bare origin whose trunk carries the
real Workflow 2.5.1 tree (``fixtures.build_workflow_line_fixture``: the
vendored release's ``.claude/commands/`` and two Workflow scripts),
the executable fake ``gh`` (``tests/fake_gh.py``) first on ``PATH``, and the
scripted fake worker (``tests/fake_claude.py``), with the identity pin and
the Workflow Manager stubbed exactly as in
``tests/test_lifecycle_orchestration``. The user-only acts (the plan
approval, the milestone acceptance, the merge on GitHub) are simulated in
the target, the origin and the fake forge's state file.

1. :class:`PolicyLifecycleTest`: trunk start, the scripted ``/milestone-plan``,
   bind, the plan-approval commit, the Draft PR, implementation steps with
   sync pushes, ``MILESTONE_COMPLETE``, ready, the merge gate, the human
   merge, close-out and the next trunk start.
2. :class:`InterruptedLifecycleTest`: the same lifecycle with every
   Controller invocation in a child process that ``SIGKILL``\\ s itself just
   before, then just after, each binding-record write, and is then resumed.
3. :class:`TrunkDriftTest`: the trunk advances mid-milestone; readiness
   gates and nothing is integrated; a human merges from ``PR_OPEN`` and the
   Controller converges to ``CLOSED``.
4. :class:`NoPolicyLifecycleTest`: the same scripted lifecycle with no
   policy leaves the records of the job path without the repository
   preflight (the pre-milestone path).
5. :class:`ReleaseHistoryTest`: the release classification walked over a
   disposable trunk history that reproduces ``v1.1.0``/``v1.1.1``, a bump
   to 1.2.0, and a 1.2.0 publish interrupted after the tag push and resumed
   by the next merge -- through ``tools/release.py``, as ``main.yml`` runs it.
6. :class:`SquashLifecycleTest`: scenario 1 under a squash policy, through a
   verified squash merge to the next trunk start, whose bootstrap names the
   squash commit.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli, gitrepo, job, milestone_branch as mb, repo_policy, routing  # noqa: E402
from tests import fake_gh, fixtures  # noqa: E402
from tests import test_lifecycle_orchestration as lifecycle  # noqa: E402
from tests import test_release_tools as release_tools, test_release_txn  # noqa: E402
from tests.test_ci_workflows import ci as ci_workflows  # noqa: E402
from tests.golden import generate_no_policy_lifecycle as golden  # noqa: E402
from tests.test_milestone_branch import policy  # noqa: E402

WI = lifecycle.WI
WI_NEXT = "wi-2"
BRANCH = f"milestone/{WI}"
STATE_REL = lifecycle.STATE_REL
POLICY = repo_policy.POLICY_PATH
PLAN_FEEDBACK_REL = f".ai-review/{WI}/feedback/REVIEW_FEEDBACK.md"
BOOTSTRAP_PLAN = "/milestone-plan"
REVIEW_PLAN = f"/review-plan {WI}"
AWAITING_LOCAL_PLAN = "AWAITING_LOCAL_PLAN_REVIEW"
AWAITING_MANUAL_PLAN = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
MILESTONE_COMPLETE = "MILESTONE_COMPLETE"
PLAN_BUNDLE_ID = "a" * 64
PASSING_CHECKS = [{"name": "validate", "state": "SUCCESS", "bucket": "pass"}]

#: Every worker task of the scripted lifecycle, in launch order, without a
#: policy. With one, each bootstrap names its trunk tip
#: (:meth:`_E2ECase.lifecycle_tasks`).
LIFECYCLE_TASKS = [BOOTSTRAP_PLAN, REVIEW_PLAN, lifecycle.MILESTONE_IMPLEMENT, lifecycle.MILESTONE_IMPLEMENT,
                   lifecycle.MILESTONE_IMPLEMENT, lifecycle.REVIEW_IMPLEMENTATION, BOOTSTRAP_PLAN]


# ---------------------------------------------------------------------------
# The child process of scenario 2.
# ---------------------------------------------------------------------------

#: The environment variable carrying the child's JSON instructions.
CHILD_ENV = "E2E_CONTROLLER_CHILD"


def _kill_label(before: dict | None, after: dict) -> str:
    """What a binding-record write persists: a state transition, a push
    intent, a push outcome, or a same-state refresh."""
    if before is None or before["state"] != after["state"]:
        return f"{before['state'] if before else None}->{after['state']}"
    push = (after.get("last_observation") or {}).get("push") or {}
    prior = ((before.get("last_observation") or {}).get("push") or {})
    if push.get("intent") and push.get("outcome") is None:
        return "push_intent"
    if push.get("outcome") and prior.get("outcome") is None and prior.get("intent"):
        return "pushed"
    return "refresh"


def _child_main() -> int:
    """``cli.main`` with the pinned identity, every ``subprocess.run`` argv
    appended to a log, and -- when asked -- ``SIGKILL`` of this very process
    at the first persist boundary of the kind ``where`` (just before, or
    just after, a binding-record write) not in ``skip``, the boundaries
    already killed."""
    spec = json.loads(os.environ.pop(CHILD_ENV))
    from controller import identity
    from controller.identity import ControllerIdentity

    fields = dict(spec["identity"])
    for name in ("source_root", "origin_source_root"):
        fields[name] = None if fields[name] is None else Path(fields[name])
    ident = ControllerIdentity(**fields)

    real_run = subprocess.run

    def logged_run(args, *a, **kw):
        with open(spec["argv_log"], "a") as handle:
            handle.write(json.dumps([str(arg) for arg in args]) + "\n")
        return real_run(args, *a, **kw)

    real_write = mb.write_record
    skip = {tuple(point) for point in spec["skip"]} if spec.get("kill") else None

    def boundary(where: str, before: dict | None, record: dict) -> None:
        point = (where, _kill_label(before, record))
        if skip is not None and where == spec["where"] and point not in skip:
            with open(spec["kill_log"], "a") as handle:
                handle.write(json.dumps(point) + "\n")
            os.kill(os.getpid(), signal.SIGKILL)

    def killing_write(runtime_root, key, record, *, now):
        before = mb.read_record(runtime_root, key, record["work_item_id"])
        boundary("before", before, record)
        written = real_write(runtime_root, key, record, now=now)
        boundary("after", before, written)
        return written

    with unittest.mock.patch.object(identity, "pin", return_value=ident), \
            unittest.mock.patch.object(identity, "current", return_value=ident), \
            unittest.mock.patch("subprocess.run", logged_run), \
            unittest.mock.patch.object(mb, "write_record", killing_write):
        return cli.main(spec["argv"])


# ---------------------------------------------------------------------------
# The shared target and the scripted lifecycle.
# ---------------------------------------------------------------------------


class _E2ECase(lifecycle._LifecycleTestCase):
    """A target on a real Workflow 2.5.1 tree, cloned from a bare origin,
    and the scripted lifecycle's workers, user acts and assertions."""

    with_policy = True

    def setUp(self) -> None:
        super().setUp()
        self.origin, self.root = self.build_target(self.tmp_root)
        self.trunk_tip = fixtures.current_head(self.root)
        self.lc = lifecycle.Lifecycle(self.tmp_root, self.root, self.trunk_tip, phase=AWAITING_LOCAL_PLAN,
                                      checkpoints={}, plan_approval=None,
                                      plan_review_stages=None)
        self.gh_env = fixtures.fake_gh_env(self.tmp_root, origin=self.origin)
        self.key = mb.repo_key(gitrepo.common_dir(self.root))
        self.diag_log = self.tmp_root / "worker-argv.jsonl"

    def build_target(self, tmp: Path) -> tuple[Path, Path]:
        """``(origin, clone)``: a bare origin whose ``main`` holds the line
        fixture, the policy (unless ``with_policy`` is false) and an empty
        Workflow state, and a clone of it on ``main``."""
        seed = fixtures.build_workflow_line_fixture(tmp / "seed", workflow_version="2.5.1")
        fixtures.run(["git", "branch", "-q", "-M", "main"], cwd=seed)
        (seed / ".gitignore").write_text(".ai-review/\n")
        (seed / "README.md").write_text("trunk orchestration fixture\n")
        if self.with_policy:
            (seed / POLICY).parent.mkdir(parents=True, exist_ok=True)
            (seed / POLICY).write_text(json.dumps(policy(), indent=2) + "\n")
        fixtures.write_workflow_state(seed, {"schema_version": 1, "active_work_item_id": None, "work_items": {}})
        fixtures.commit_all(seed, "Add the policy and the Workflow state")
        origin = tmp / "origin.git"
        fixtures.git_init(origin, "--bare", "--initial-branch=main")
        # Every ref update on the origin is logged, so a forced update is visible.
        fixtures.run(["git", "config", "core.logAllRefUpdates", "always"], cwd=origin)
        fixtures.run(["git", "push", "-q", str(origin), "main"], cwd=seed)
        clone = tmp / "clone"
        fixtures.git_clone(origin, clone)
        fixtures.run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=clone)
        fixtures.run(["git", "config", "user.name", "Controller Tests"], cwd=clone)
        return origin, clone.resolve()

    # -- the target, the origin and the forge ---------------------------------------------

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return fixtures.run(["git", *args], cwd=cwd or self.root).stdout.strip()

    def origin_ref(self, ref: str) -> str | None:
        result = fixtures.run(["git", "--git-dir", str(self.origin), "rev-parse", "-q", "--verify", ref],
                              check=False)
        return result.stdout.strip() or None

    def origin_branches(self) -> list[str]:
        out = self.git("--git-dir", str(self.origin), "for-each-ref", "--format=%(refname:short)", "refs/heads")
        return sorted(out.splitlines())

    def local_branches(self) -> list[str]:
        return sorted(self.git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())

    def head(self) -> gitrepo.HeadState:
        return gitrepo.head_state(self.root)

    def write_state(self, active: str | None, items: dict) -> None:
        fixtures.write_workflow_state(self.root, {"schema_version": 1, "active_work_item_id": active,
                                                  "work_items": items})

    def record(self) -> dict | None:
        return mb.read_record(self.lc.runtime, self.key, WI)

    def binding_events(self) -> list[str]:
        path = self.lc.runtime / mb.events_rel(self.key, WI)
        return [json.loads(line)["event"] for line in path.read_text().splitlines()] if path.exists() else []

    def gh_calls(self, group: str | None = None, sub: str | None = None) -> list[list[str]]:
        calls = fake_gh.invocations(Path(self.gh_env["FAKE_GH_LOG"]))
        return [c for c in calls if group is None or c[:2] == [group, sub]]

    def gh_state(self) -> dict:
        return fake_gh.read_state(Path(self.gh_env["FAKE_GH_STATE"]))

    def gh_edit(self, number: int, **fields) -> None:
        state_path = Path(self.gh_env["FAKE_GH_STATE"])
        data = fake_gh.read_state(state_path)
        for pr in data["prs"]:
            if pr["number"] == number:
                pr.update(fields)
        fake_gh.write_state(state_path, data)

    def human(self) -> Path:
        """A second clone of the origin, on ``main`` at the origin's tip."""
        human = self.tmp_root / "human"
        if not human.exists():
            fixtures.git_clone(self.origin, human)
            self.git("config", "user.email", "human@example.invalid", cwd=human)
            self.git("config", "user.name", "Human", cwd=human)
        self.git("fetch", "-q", "origin", cwd=human)
        self.git("switch", "-q", "main", cwd=human)
        self.git("reset", "-q", "--hard", "origin/main", cwd=human)
        return human

    def land_on_trunk(self, name: str) -> str:
        """Someone else lands a commit on the origin's ``main``."""
        human = self.human()
        (human / name).write_text("landed elsewhere\n")
        commit = fixtures.commit_all(human, f"Land {name} on main")
        self.git("push", "-q", "origin", "main", cwd=human)
        return commit

    def human_merge(self, number: int) -> str:
        """A human merges pull request ``number`` on GitHub with "Create a
        merge commit"; returns the merge commit."""
        head = self.origin_ref(f"refs/heads/{BRANCH}")
        human = self.human()
        self.git("merge", "-q", "--no-ff", "-m", f"Merge pull request #{number}", f"origin/{BRANCH}", cwd=human)
        self.git("push", "-q", "origin", "main", cwd=human)
        merge_commit = fixtures.current_head(human)
        self.gh_edit(number, state="MERGED", headRefOid=head, mergedAt="2026-09-25T01:00:00Z",
                     mergeCommit={"oid": merge_commit})
        return merge_commit

    # -- the scripted workers and the user acts --------------------------------------------

    def plan_actions(self, work_item_id: str, items: dict) -> list[dict]:
        """``/milestone-plan``'s effect: the plan, the registry, the plan
        bundle and the work item at ``AWAITING_LOCAL_PLAN_REVIEW``, all
        uncommitted on the trunk."""
        registry = {"schema_version": 1, "work_item_id": work_item_id, "plan_revision": 1,
                    "checkpoints": [{"id": cid, "name": cid, "depends_on": [], "complexity": 1,
                                     "session_target": 1} for cid in lifecycle.CHECKPOINT_IDS]}
        return [
            fixtures.script_write(f"docs/plans/{work_item_id}.md", f"# The plan of {work_item_id}\n"),
            fixtures.script_write(fixtures.registry_rel_path(work_item_id), json.dumps(registry, indent=2) + "\n"),
            fixtures.script_write(f".ai-review/{work_item_id}/current/MANIFEST.md",
                                  fixtures.build_plan_manifest_text(work_item_id, 1, bundle_id=PLAN_BUNDLE_ID)),
            fixtures.script_write(STATE_REL, json.dumps({"schema_version": 1, "active_work_item_id": work_item_id,
                                                         "work_items": items}, indent=2) + "\n"),
        ]

    def review_plan_actions(self) -> list[dict]:
        """``/review-plan``'s local ``APPROVE``: the verdict on file and the
        work item at ``AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW``, uncommitted."""
        self.lc.entry.update(phase=AWAITING_MANUAL_PLAN, plan_review_stages={
            "review_content_id": "c" * 64,
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": PLAN_BUNDLE_ID, "verdict": "APPROVE", "round": 1,
                                        "completed_at": lifecycle.COMPLETED_AT},
            "MANUAL_EXTERNAL_PLAN_REVIEW": None})
        feedback = fixtures.build_review_feedback_text(
            status="APPROVE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id=PLAN_BUNDLE_ID,
            reviewed_base_commit=self.trunk_tip, work_item=WI)
        return [fixtures.script_write(PLAN_FEEDBACK_REL, feedback), self.lc.write_state()]

    def bootstrap(self, base: str | None) -> str:
        """The bootstrap command: ``/milestone-plan <trunk tip>`` once
        milestone branches are enabled (squash-merge Design F), bare
        without a policy."""
        return f"{BOOTSTRAP_PLAN} {base}" if self.with_policy else BOOTSTRAP_PLAN

    def lifecycle_tasks(self, next_base: str) -> list[str]:
        return [self.bootstrap(self.trunk_tip), *LIFECYCLE_TASKS[1:-1], self.bootstrap(next_base)]

    def script_lifecycle(self) -> None:
        """Every worker of the lifecycle, filed under its exact task."""
        lc = self.lc
        lc.add(self.bootstrap(self.trunk_tip), self.plan_actions(WI, {WI: dict(lc.entry)}))
        lc.add(REVIEW_PLAN, self.review_plan_actions())

    def script_implementation(self) -> None:
        lc = self.lc
        lc.entry.update(phase=lifecycle.IMPLEMENTING)
        lc.add(lifecycle.MILESTONE_IMPLEMENT, lc.implement("CP1"))
        lc.add(lifecycle.MILESTONE_IMPLEMENT, lc.implement("CP2", last=True))
        lc.add(lifecycle.MILESTONE_IMPLEMENT, lc.generate())
        lc.add(lifecycle.REVIEW_IMPLEMENTATION, lc.local_review("APPROVE", 1))

    def script_next_plan(self, base: str | None = None) -> None:
        """The next milestone's ``/milestone-plan <base>``, on the trunk
        after close-out, next to the accepted item."""
        entry = dict(self.lc.entry, work_item_id=WI_NEXT, phase=AWAITING_LOCAL_PLAN, plan_approval=None,
                     base_commit="{HEAD}", checkpoints={}, registry_path=fixtures.registry_rel_path(WI_NEXT),
                     implementation_revision=None, reviewed_implementation_head=None,
                     implementation_review_stages=None, plan_review_stages=None,
                     last_completed_checkpoint_id=None)
        self.lc.add(self.bootstrap(base), self.plan_actions(WI_NEXT, {WI: dict(self.lc.entry), WI_NEXT: entry}))

    def approve_plan(self) -> str:
        """``/approve-review plan``: the plan-approval commit on the branch."""
        self.lc.sync()
        self.lc.entry.update(phase=lifecycle.IMPLEMENTING, plan_approval={"status": "CURRENT"})
        self.write_state(WI, {WI: self.lc.entry})
        return fixtures.commit_all(self.root, fixtures.trailer_message(
            f"Approve plan for {WI}", ("Workflow-Plan-Approval", "ab" * 32), ("Workflow-Work-Item", WI)))

    def accept(self) -> str:
        """The manual review, ``/approve-review implementation`` and
        ``/accept-milestone``: the acceptance commit on the branch."""
        self.lc.sync()
        self.lc.entry.update(phase=MILESTONE_COMPLETE)
        self.write_state(None, {WI: self.lc.entry})
        return fixtures.commit_all(self.root, fixtures.trailer_message(f"Accept {WI}", ("Workflow-Work-Item", WI)))

    # -- the command line ----------------------------------------------------------------

    def env(self) -> dict[str, str]:
        return {**self.gh_env, "FAKE_CLAUDE_SCRIPT": str(self.lc.script_path),
                "FAKE_CLAUDE_INVOCATIONS_FILE": str(self.lc.processes_file),
                "FAKE_CLAUDE_DIAG_LOG": str(self.diag_log)}

    def argv(self, command: str, *args: str, json_out: bool = False) -> list[str]:
        argv = ["--runtime-dir", str(self.lc.runtime), "--workflow-manager", str(self.stub_manager),
                "--claude-binary", str(lifecycle.FAKE_CLAUDE), "--timeout", "60"]
        if json_out:
            argv.append("--json")
        return argv + [command, *args, str(self.root)]

    def invoke(self, argv: list[str]) -> tuple[int, str, str]:
        """One ``cli.main``, in this process."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with unittest.mock.patch.dict(os.environ, self.env()), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def cli(self, command: str, *args: str, json_out: bool = False) -> lifecycle.Run:
        fixtures.write_worker_script(self.lc.script_path, self.lc.script)
        jobs_dir = self.lc.runtime / "jobs"
        before = {p.name for p in jobs_dir.glob("*.json")} if jobs_dir.is_dir() else set()
        code, stdout, stderr = self.invoke(self.argv(command, *args, json_out=json_out))
        created = sorted((p for p in jobs_dir.glob("*.json") if p.name not in before),
                         key=lambda p: p.stat().st_mtime_ns) if jobs_dir.is_dir() else []
        return lifecycle.Run(code, [json.loads(p.read_text()) for p in created], stdout, stderr)

    def step(self, expected_code: int) -> lifecycle.Run:
        result = self.cli("step")
        self.assertEqual(result.code, expected_code, result.stderr)
        return result

    def worker_tasks(self) -> list[str]:
        return fixtures.scripted_worker_tasks(self.lc.script_path)

    def assert_launched(self, result: lifecycle.Run, command: str, observed: str, *, bound: bool = True) -> dict:
        record = result.records[-1]
        self.assertEqual(record["selected_action"]["command"], command)
        self.assert_finished(record, observed)
        if bound:
            self.assertEqual(record["branch_binding"]["branch"], BRANCH)
        else:
            self.assertNotIn("branch_binding", record)
        return record

    def assert_branch_gate(self, result: lifecycle.Run, code: str) -> dict:
        record = result.records[-1]
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(record["human_gate_pending"]["phase"], "MILESTONE_BRANCH")
        self.assertIn(code, record["human_gate_pending"]["what_is_required"])
        return record

    # -- the lifecycle -------------------------------------------------------------------

    def drive_to_pr(self) -> None:
        """Trunk start and the bootstrap plan, bind and the plan review, the
        manual plan-review gate, the plan approval, and the step that opens
        the Draft PR and implements CP1."""
        self.script_lifecycle()
        started = self.step(cli.EXIT_OK)
        self.assert_launched(started, self.bootstrap(self.trunk_tip), AWAITING_LOCAL_PLAN, bound=False)
        self.assertIsNone(self.record(), "the trunk start wrote a binding record")
        self.assertEqual(self.head().branch, "main")

        bound = self.step(cli.EXIT_OK)
        self.assert_launched(bound, REVIEW_PLAN, AWAITING_MANUAL_PLAN)
        self.assertEqual(bound.records[-1]["branch_binding"]["pre_step_tip"], self.trunk_tip)
        self.assertEqual((self.record()["state"], self.head().branch), (mb.BRANCH_BOUND, BRANCH))
        self.assertEqual(self.record()["branch_point"], self.trunk_tip)

        gated = self.step(cli.EXIT_GATE)
        self.assertEqual(gated.records[-1]["observed_phase_before"], AWAITING_MANUAL_PLAN)
        # Nothing to push or propose yet: the branch has no commit of its own.
        self.assertEqual(self.gh_calls("pr", "create"), [])
        self.assertIsNone(self.origin_ref(f"refs/heads/{BRANCH}"))

        self.approval = self.approve_plan()
        self.script_implementation()
        opened = self.step(cli.EXIT_OK)
        self.assert_launched(opened, lifecycle.MILESTONE_IMPLEMENT, lifecycle.IMPLEMENTING)
        record = self.record()
        self.assertEqual(record["state"], mb.PR_OPEN)
        self.assertEqual(record["pr"]["number"], 1)
        [pr] = self.gh_state()["prs"]
        self.assertTrue(pr["isDraft"])
        self.assertEqual((pr["headRefName"], pr["baseRefName"]), (BRANCH, "main"))
        self.assertIn(mb.PR_MARKER.format(work_item_id=WI), pr["body"])
        self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), self.approval)

    def drive_implementation(self) -> None:
        """CP2, the final pass and the local review, each step first
        pushing the previous step's commits."""
        for command, observed in ((lifecycle.MILESTONE_IMPLEMENT, lifecycle.SELF_REVIEWING),
                                  (lifecycle.MILESTONE_IMPLEMENT, lifecycle.AWAITING_LOCAL),
                                  (lifecycle.REVIEW_IMPLEMENTATION, lifecycle.AWAITING_MANUAL)):
            tip = fixtures.current_head(self.root)
            result = self.step(cli.EXIT_OK)
            self.assert_launched(result, command, observed)
            self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), tip, "the step did not sync the branch")
        manual = self.step(cli.EXIT_GATE)
        self.assertEqual(manual.records[-1]["human_gate_pending"]["safe_resume_command"], lifecycle.RECORD_MANUAL)

    def drive_to_merge_gate(self) -> str:
        """Acceptance, then readiness: pending checks gate, green checks
        mark the pull request ready at the acceptance commit, and the merge
        gate holds."""
        accepted = self.accept()
        pending = self.step(cli.EXIT_GATE)
        self.assert_branch_gate(pending, "no checks reported yet")
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.gh_edit(1, checks=PASSING_CHECKS)
        ready = self.step(cli.EXIT_GATE)
        self.assert_branch_gate(ready, "Create a merge commit")
        record = self.record()
        self.assertEqual((record["state"], record["accepted_head"]), (mb.READY, accepted))
        self.assertFalse(self.gh_state()["prs"][0]["isDraft"])
        self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), accepted)
        again = self.step(cli.EXIT_GATE)
        self.assert_branch_gate(again, "The Controller never merges")
        self.assertEqual(len(self.gh_calls("pr", "ready")), 1)
        return accepted

    def drive_close_out(self) -> str:
        """The human merge, then the step that closes out, returns to the
        trunk and starts the next milestone."""
        merge_commit = self.human_merge(1)
        self.script_next_plan(merge_commit)
        closed = self.step(cli.EXIT_OK)
        record = self.record()
        self.assertEqual(record["state"], mb.CLOSED)
        self.assertEqual((self.head().branch, self.head().commit), ("main", merge_commit))
        self.assert_launched(closed, self.bootstrap(merge_commit), AWAITING_LOCAL_PLAN, bound=False)
        return merge_commit

    def assert_no_force_and_no_merge(self, argvs: list[list[str]]) -> None:
        """No Controller argv forces, deletes or merges, the origin's
        milestone branch only ever moved forward, and ``gh`` was never asked
        to merge."""
        controller = [argv for argv in argvs if argv[:3] == ["git", "-C", str(self.root)]]
        self.assertTrue(any("push" in argv for argv in controller), "no Controller push was observed")
        for argv in controller:  # the rest are the test's own user acts
            self.assertFalse(any(a.startswith("--force") or a in ("-f", "--delete", "-D") for a in argv), argv)
            self.assertFalse(any(a.startswith("+") for a in argv if ":" in a), argv)
            if "merge" in argv:
                self.assertIn("--ff-only", argv, f"the Controller merged: {argv}")
        self.assertEqual([c for c in self.gh_calls() if c[:2] == ["pr", "merge"]], [])
        log = self.git("--git-dir", str(self.origin), "reflog", "show", "--format=%H", f"refs/heads/{BRANCH}")
        tips = list(reversed(log.splitlines()))
        self.assertGreaterEqual(len(tips), 4, "the origin's reflog of the branch is not being recorded")
        for older, newer in zip(tips, tips[1:]):
            self.assertTrue(gitrepo.is_ancestor(self.root, older, newer), f"{BRANCH} moved from {older} to {newer}")

    def assert_single_branch_and_pr(self) -> None:
        self.assertEqual(self.origin_branches(), ["main", BRANCH])
        self.assertEqual(self.local_branches(), ["main", BRANCH])
        self.assertEqual([pr["number"] for pr in self.gh_state()["prs"]], [1])
        self.assertEqual(len(self.gh_calls("pr", "create")), 1)


@contextlib.contextmanager
def _run_spy():
    """Every ``subprocess.run`` argv, let through unchanged."""
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(args, *a, **kw):
        calls.append([str(arg) for arg in args])
        return real(args, *a, **kw)

    with unittest.mock.patch("subprocess.run", spy):
        yield calls


# ---------------------------------------------------------------------------
# Scenario 1.
# ---------------------------------------------------------------------------


class PolicyLifecycleTest(_E2ECase):
    def test_trunk_to_merge_to_the_next_trunk_start(self) -> None:
        with _run_spy() as argvs:
            self.drive_to_pr()
            self.drive_implementation()
            accepted = self.drive_to_merge_gate()
            merge_commit = self.drive_close_out()

        self.assertEqual(self.worker_tasks(), self.lifecycle_tasks(merge_commit))
        self.assert_single_branch_and_pr()
        self.assert_no_force_and_no_merge(argvs)
        record = self.record()
        self.assertEqual((record["merged_head"], record["accepted_head"]), (accepted, accepted))
        self.assertTrue(gitrepo.is_ancestor(self.root, accepted, merge_commit))
        events = self.binding_events()
        for event in ("bind_planned", "bound", "push_intent", "pushed", "pr_planned", "pr_created", "ready",
                      "merged", "closed"):
            self.assertIn(event, events)
        self.assertEqual(events.count("pr_created"), 1)
        # Every worker under the binding ran with the branch guard, the one on the trunk without it.
        argv = [json.loads(line)["argv"] for line in self.diag_log.read_text().splitlines()]
        guard = ",".join(routing.BRANCH_GUARD_TOOLS)
        self.assertEqual([guard in a[-1] for a in argv], [False, True, True, True, True, True, False])
        state = json.loads((self.root / STATE_REL).read_text())
        self.assertEqual(state["work_items"][WI]["phase"], MILESTONE_COMPLETE)
        self.assertEqual(state["work_items"][WI_NEXT]["phase"], AWAITING_LOCAL_PLAN)
        # The next step binds the next milestone from the new trunk tip.
        self.lc.add(f"/review-plan {WI_NEXT}", [])
        self.cli("step")
        following = mb.read_record(self.lc.runtime, self.key, WI_NEXT)
        self.assertEqual((following["state"], following["branch_point"]), (mb.BRANCH_BOUND, merge_commit))


# ---------------------------------------------------------------------------
# Scenario 2.
# ---------------------------------------------------------------------------


class InterruptedLifecycleTest(_E2ECase):
    """Every Controller invocation runs in a child process. Each ``step``
    is attempted again and again, each attempt killed at the first persist
    boundary -- just before a binding-record write in one lifecycle, just
    after one in the other, told apart by what the write persists -- that no
    earlier attempt of the same step was killed at, and resuming from what
    the killed ones left, until an attempt meets no new boundary and
    completes. (One lifecycle for both would never reach some boundaries:
    killed just before ``pushed``, the push has happened, so the resumed
    attempt never writes ``pushed`` at all.)"""

    #: The writes each lifecycle must have been killed at.
    REQUIRED = ("None->BRANCH_PLANNED", "BRANCH_PLANNED->BRANCH_BOUND", "push_intent", "pushed",
                "BRANCH_BOUND->PR_PLANNED", "PR_PLANNED->PR_OPEN", "PR_OPEN->READY", "READY->MERGED",
                "MERGED->CLOSED")

    def setUp(self) -> None:
        super().setUp()
        self.argv_log = self.tmp_root / "controller-argv.jsonl"
        self.kill_log = self.tmp_root / "kills.jsonl"
        self.kills = 0

    def child(self, argv: list[str], skip: list | None) -> subprocess.CompletedProcess:
        spec = {"argv": argv, "kill": skip is not None, "skip": skip or [], "where": self.where,
                "argv_log": str(self.argv_log), "kill_log": str(self.kill_log),
                "identity": {k: (str(v) if isinstance(v, Path) else v)
                             for k, v in dataclasses.asdict(self.ident).items()}}
        env = {**os.environ, **self.env(), CHILD_ENV: json.dumps(spec)}
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from tests.test_trunk_orchestration_e2e import _child_main; sys.exit(_child_main())")
        return subprocess.run([sys.executable, "-c", code, str(REPO_ROOT)], env=env, cwd=self.tmp_root,
                              capture_output=True, text=True, timeout=300, check=False)

    def invoke(self, argv: list[str]) -> tuple[int, str, str]:
        if "step" not in argv:
            result = self.child(argv, None)
            return result.returncode, result.stdout, result.stderr
        skip: list = []
        while True:
            workers = self.processes(self.lc)
            result = self.child(argv, skip)
            if result.returncode != -signal.SIGKILL:
                return result.returncode, result.stdout, result.stderr
            self.kills += 1
            self.assertEqual(self.processes(self.lc), workers, "a killed preflight launched a worker")
            skip.append(json.loads(self.kill_log.read_text().splitlines()[-1]))

    def test_killed_just_before_each_binding_write_and_resumed(self) -> None:
        self.where = "before"
        self.assert_converges_after_every_kill()

    def test_killed_just_after_each_binding_write_and_resumed(self) -> None:
        self.where = "after"
        self.assert_converges_after_every_kill()

    def assert_converges_after_every_kill(self) -> None:
        self.drive_to_pr()
        self.drive_implementation()
        accepted = self.drive_to_merge_gate()
        merge_commit = self.drive_close_out()

        # Each automatic action ran exactly once, and nothing was duplicated.
        self.assertEqual(self.worker_tasks(), self.lifecycle_tasks(merge_commit))
        self.assert_single_branch_and_pr()
        argvs = [json.loads(line) for line in self.argv_log.read_text().splitlines()]
        self.assert_no_force_and_no_merge(argvs)
        self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, accepted))

        killed = [json.loads(line) for line in self.kill_log.read_text().splitlines()]
        self.assertEqual(len(killed), self.kills)
        self.assertGreaterEqual(self.kills, len(self.REQUIRED))
        self.assertEqual({at for at, _ in killed}, {self.where})
        labels = {label for _, label in killed}
        for label in self.REQUIRED:
            self.assertIn(label, labels, f"never killed {self.where} the {label} write")


# ---------------------------------------------------------------------------
# Scenario 3.
# ---------------------------------------------------------------------------


class TrunkDriftTest(_E2ECase):
    def test_drift_gates_readiness_and_a_merge_from_pr_open_converges(self) -> None:
        with _run_spy() as argvs:
            self.drive_to_pr()
            landed = self.land_on_trunk("drift.txt")
            self.drive_implementation()
            observation = self.record()["last_observation"]
            self.assertEqual((observation["remote_trunk"], observation["fresh"], observation["behind"]),
                             (landed, False, 1))
            accepted = self.accept()
            self.gh_edit(1, checks=PASSING_CHECKS)
            gated = self.step(cli.EXIT_GATE)
            self.assert_branch_gate(gated, "the Controller does not integrate")
            self.assertEqual(gated.records[-1]["human_gate_pending"]["what_is_required"].count("1 commit(s)"), 1)
            self.assertEqual(self.record()["state"], mb.PR_OPEN)
            # Nothing was integrated: the branch is still exactly the accepted history.
            self.assertEqual(fixtures.current_head(self.root), accepted)
            self.assertFalse(gitrepo.is_ancestor(self.root, landed, accepted))
            self.assertEqual(self.gh_calls("pr", "ready"), [])
            self.assertTrue(self.gh_state()["prs"][0]["isDraft"])

            # The human marks it ready and merges it on GitHub, from PR_OPEN.
            self.gh_edit(1, isDraft=False)
            merge_commit = self.human_merge(1)
            self.script_next_plan(merge_commit)
            closed = self.step(cli.EXIT_OK)
        self.assertEqual(self.record()["state"], mb.CLOSED)
        self.assertEqual(self.record()["merged_head"], accepted)
        self.assertEqual((self.head().branch, self.head().commit), ("main", merge_commit))
        self.assertTrue(gitrepo.is_ancestor(self.root, landed, merge_commit))
        self.assert_launched(closed, self.bootstrap(merge_commit), AWAITING_LOCAL_PLAN, bound=False)
        self.assertNotIn("ready", self.binding_events())
        self.assert_single_branch_and_pr()
        self.assert_no_force_and_no_merge(argvs)


class SquashLifecycleTest(_E2ECase):
    """Scenario 1 under a squash policy (``workflow-controller-squash-merge-
    tag-versioning`` Design E and F): the Draft PR carries the plan's
    declared title, readiness syncs the body, a human squash-merges on
    GitHub, close-out verifies the squash, and the next trunk start's
    bootstrap names the squash commit. Workflow's own resolution of the
    ``<base-sha>`` argument is not run (the worker is scripted); the
    launched command text is what is asserted."""

    TITLE = "feat: the squash lifecycle"

    def setUp(self) -> None:
        super().setUp()
        self.lc.entry["plan_path"] = f"docs/plans/{WI}.md"

    def build_target(self, tmp: Path) -> tuple[Path, Path]:
        from tests.test_pull_request_lifecycle import squash_policy

        origin, clone = super().build_target(tmp)
        (clone / POLICY).write_text(json.dumps(squash_policy(), indent=2) + "\n")
        fixtures.commit_all(clone, "Squash merges")
        fixtures.run(["git", "push", "-q", "origin", "main"], cwd=clone)
        return origin, clone

    def plan_actions(self, work_item_id: str, items: dict) -> list[dict]:
        actions = super().plan_actions(work_item_id, items)
        actions[0] = fixtures.script_write(f"docs/plans/{work_item_id}.md",
                                           f"# The plan of {work_item_id}\n\nPull request title: `{self.TITLE}`\n")
        return actions

    def squash_merge(self, number: int) -> str:
        """GitHub's "Squash and merge": the title and number, then the body."""
        pr = next(pr for pr in self.gh_state()["prs"] if pr["number"] == number)
        head = self.origin_ref(f"refs/heads/{BRANCH}")
        human = self.human()
        self.git("merge", "-q", "--squash", f"origin/{BRANCH}", cwd=human)
        self.git("commit", "-q", "--cleanup=verbatim", "-m", f"{pr['title']} (#{number})\n\n{pr['body']}", cwd=human)
        self.git("push", "-q", "origin", "main", cwd=human)
        squash = fixtures.current_head(human)
        self.gh_edit(number, state="MERGED", headRefOid=head, mergedAt="2026-09-29T01:00:00Z",
                     mergeCommit={"oid": squash})
        return squash

    def test_trunk_to_squash_merge_to_the_next_trunk_start(self) -> None:
        with _run_spy() as argvs:
            self.drive_to_pr()
            self.assertEqual(self.gh_state()["prs"][0]["title"], self.TITLE)
            self.drive_implementation()
            accepted = self.accept()
            synced = self.step(cli.EXIT_GATE)
            self.assert_branch_gate(synced, "just updated")
            self.assertIn(f"Accepted at {accepted}", self.gh_state()["prs"][0]["body"])
            self.gh_edit(1, checks=PASSING_CHECKS)
            ready = self.step(cli.EXIT_GATE)
            self.assert_branch_gate(ready, "Squash and merge")
            self.assertEqual(self.record()["state"], mb.READY)
            squash = self.squash_merge(1)
            self.script_next_plan(squash)
            closed = self.step(cli.EXIT_OK)
        record = self.record()
        self.assertEqual((record["state"], record["merged_head"], record["accepted_head"], record["merge_commit"]),
                         (mb.CLOSED, accepted, accepted, squash))
        self.assertEqual((self.head().branch, self.head().commit), ("main", squash))
        self.assertFalse(gitrepo.is_ancestor(self.root, accepted, squash))
        self.assertEqual(self.git("log", "-1", "--format=%s", squash), f"{self.TITLE} (#1)")
        self.assertEqual(self.git("log", "-1", "--format=%(trailers:only=true)", squash), "")
        self.assert_launched(closed, f"{BOOTSTRAP_PLAN} {squash}", AWAITING_LOCAL_PLAN, bound=False)
        self.assertEqual(self.worker_tasks(), self.lifecycle_tasks(squash))
        events = self.binding_events()
        self.assertIn("merged_squashed", events)
        self.assertNotIn("merged", events)
        self.assert_single_branch_and_pr()
        self.assert_no_force_and_no_merge(argvs)
        # The next step binds the next milestone at the squash commit.
        self.lc.add(f"/review-plan {WI_NEXT}", [])
        self.cli("step")
        following = mb.read_record(self.lc.runtime, self.key, WI_NEXT)
        self.assertEqual((following["state"], following["branch_point"]), (mb.BRANCH_BOUND, squash))


# ---------------------------------------------------------------------------
# Scenario 4.
# ---------------------------------------------------------------------------


class NoPolicyLifecycleTest(_E2ECase):
    """The identical scripted lifecycle on a target with no policy, once
    through the code as it stands and once with the repository preflight
    taken out of the job path -- the path before this milestone: the job
    records, the worker argv and the target's history are identical, and
    nothing touched the origin or ``gh``."""

    with_policy = False

    def drive(self) -> None:
        self.script_lifecycle()
        self.step(cli.EXIT_OK)
        self.step(cli.EXIT_OK)
        self.step(cli.EXIT_GATE)
        self.approve_plan()
        self.script_implementation()
        for _ in range(4):
            self.step(cli.EXIT_OK)
        self.step(cli.EXIT_GATE)
        self.accept()
        self.script_next_plan()
        self.step(cli.EXIT_OK)

    def observed(self) -> dict:
        jobs = sorted((self.lc.runtime / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        log = self.git("log", "--format=%s%n%b%n--", "--first-parent", "HEAD")
        return golden.normalise({
            "jobs": [json.loads(p.read_text()) for p in jobs],
            "worker_argv": [json.loads(line)["argv"] for line in self.diag_log.read_text().splitlines()],
            "history": log, "branches": self.local_branches(), "head": self.head().branch,
        }, str(self.tmp_root))

    def run_case(self, *, preflight: bool) -> dict:
        with _run_spy() as argvs:
            if preflight:
                self.drive()
            else:
                with unittest.mock.patch.object(job.milestone_branch, "repository_preflight",
                                                return_value=mb.Proceed()):
                    self.drive()
        for argv in argvs:
            self.assertFalse({"fetch", "push", "ls-remote", "switch"} & set(argv), argv)
        self.assertEqual(self.gh_calls(), [])
        self.assertFalse((self.lc.runtime / "repositories").exists())
        return self.observed()

    def test_the_no_policy_lifecycle_equals_the_pre_milestone_path(self) -> None:
        current = self.run_case(preflight=True)
        self.assertEqual(self.worker_tasks(), LIFECYCLE_TASKS)
        self.setUp()
        before = self.run_case(preflight=False)
        self.assertEqual(len(current["jobs"]), 9)
        self.assertEqual(current, before)
        for record in current["jobs"]:
            self.assertNotIn("branch_binding", record)
        self.assertEqual(current["head"], "main")
        self.assertEqual(current["branches"], ["main"])


# ---------------------------------------------------------------------------
# Scenario 5.
# ---------------------------------------------------------------------------


class ReleaseHistoryTest(test_release_txn._ReleaseCase):
    """``main.yml``'s release path, per trunk commit: ``classify``, then --
    for ``RELEASE_DUE`` or ``RESUME`` -- ``build`` and ``verify`` at the
    classified commit with ``RELEASE_VERSION`` set to the classified version,
    and ``publish`` from the trunk tip."""

    _main = release_tools.ReleaseTransactionCliTest._main

    def ci_run(self, tip: str, *, publish_failure: str | None = None) -> str:
        """One ``main.yml`` run for trunk commit ``tip``; returns its state."""
        code, out, err = self._main("classify", "--commit", tip)
        self.assertEqual(code, 0, err)
        outputs = dict(line.split("=", 1) for line in out.splitlines())
        if outputs["state"] in ("RELEASE_DUE", "RESUME"):
            fixtures.run(["git", "switch", "-q", "--detach", outputs["commit"]], cwd=self.clone)
            env = {"RELEASE_VERSION": outputs["version"]}
            self.assertEqual(self._main("build", extra_env=env)[0], 0)
            self.assertEqual(self._main("verify", extra_env=env)[0], 0)
            fixtures.run(["git", "switch", "-q", "--detach", tip], cwd=self.clone)
            env = {"FAKE_GH_FAIL": json.dumps({"release create": publish_failure})} if publish_failure else {}
            with unittest.mock.patch.dict(self.env, env):
                code, _, err = self._main("publish", "--commit", outputs["commit"])
            self.assertEqual(code, 1 if publish_failure else 0, err)
            fixtures.run(["git", "switch", "-q", "main"], cwd=self.clone)
            shutil.rmtree(self.clone / "dist")
        return outputs["state"]

    def test_the_v1_1_history_then_an_interrupted_1_2_0_resumed_by_the_next_merge(self) -> None:
        self.assertEqual(self.ci_run(self.base), "RELEASE_DUE")  # 1.0.0
        self.assertEqual(self.ci_run(self.merge("docs")), "NO_CHANGE")
        # v1.1.0: a tag pushed for 1.1.0 with no release, acknowledged as abandoned.
        orphan = self.bump("1.1.0")
        self.tag("v1.1.0", orphan)
        self.assertEqual(self.ci_run(self.bump("1.1.0", abandoned=["v1.1.0"])), "ABANDONED_VERSION")
        patch = self.bump("1.1.1")
        self.assertEqual(self.ci_run(patch), "RELEASE_DUE")
        self.assertEqual(self.ci_run(self.merge("fix")), "NO_CHANGE")

        minor = self.bump("1.2.0")
        self.assertEqual(self.ci_run(minor, publish_failure="server_error"), "RELEASE_DUE")
        # The tag landed at the 1.2.0 commit; the release did not.
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.2.0"), minor)
        self.assertIsNone(self.release_state("v1.2.0"))
        self.assertEqual(self.ci_run(self.merge("next")), "RESUME")
        released = self.release_state("v1.2.0")
        self.assertFalse(released["isDraft"])
        self.assertEqual(sorted(a["name"] for a in released["assets"]), ["SHA256SUMS", "pkg-1.2.0.txt"])
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.2.0"), minor)
        self.assertEqual(self.ci_run(self.merge("later")), "NO_CHANGE")
        for tag, commit in (("v1.0.0", self.base), ("v1.1.1", patch)):
            self.assertFalse(self.release_state(tag)["isDraft"])
            self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", tag), commit)
        self.assertIsNone(self.release_state("v1.1.0"))
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.1.0"), orphan)

    def test_the_cutover_history_under_conventional_commits(self) -> None:
        # v1.0.0 stands for v1.3.0, released under version_change.
        self.assertEqual(self.ci_run(self.base), "RELEASE_DUE")
        self.assertEqual(self.ci_run(self.merge("docs")), "NO_CHANGE")
        # The milestone's own merge, still legacy, releases nothing.
        self.assertEqual(self.ci_run(self.merge("milestone")), "NO_CHANGE")
        # The cutover pull request, squashed with a feat title.
        cutover = self.squash("feat: squash merges and tag-derived versions (#9)")
        self.assertEqual(self.ci_run(cutover), "RELEASE_DUE")
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.1.0"), cutover)
        self.assertEqual(self.ci_run(self.squash("docs: guide (#10)")), "NO_CHANGE")
        # A fix whose publication is interrupted, then resumed by the next merge.
        fix = self.squash("fix: a (#11)")
        self.assertEqual(self.ci_run(fix, publish_failure="server_error"), "RELEASE_DUE")
        self.assertIsNone(self.release_state("v1.1.1"))
        self.assertEqual(self.ci_run(self.squash("feat: b (#12)")), "RESUME")
        self.assertFalse(self.release_state("v1.1.1")["isDraft"])
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.1.1"), fix)
        # The feat the resume skipped is released by the next run.
        tip = self.squash("chore: tidy (#13)")
        self.assertEqual(self.ci_run(tip), "RELEASE_DUE")
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.2.0"), tip)
        self.assertEqual(self.ci_run(tip), "ALREADY_RELEASED")
        # An unclassifiable subject fails the run and releases nothing.
        bad = self.squash("Update README.md")
        code, out, err = self._main("classify", "--commit", bad)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("state=INVALID_SUBJECT\nversion=1.2.0\ntag=v1.2.0\n"), out)
        self.assertIn(bad, err)
        self.assertEqual(self.ci_run(self.squash("chore: settle", overrides={bad: "none"})), "NO_CHANGE")

    def _build_job_step(self, name: str, outputs: dict[str, str]) -> None:
        """Run ``main.yml``'s build-job step ``name`` as the generated model
        declares it, in the checked-out clone, with the job's environment."""
        job = ci_workflows.main_workflow()["jobs"]["build"]
        step = next(step for step in job["steps"] if step.get("name") == name)
        env = {**job.get("env", {}), **step.get("env", {})}
        for key, value in env.items():
            for output, found in outputs.items():
                value = value.replace(f"${{{{ needs.release-plan.outputs.{output} }}}}", found)
            self.assertNotIn("${{", value, f"{name}: {key} is not a release-plan output")
            env[key] = value
        # The classified version reaches build and verify only through the
        # job's own environment, as main.yml declares it.
        self.assertEqual(env.get("RELEASE_VERSION"), outputs["version"])
        runner_env = dict(self.env, TOY_VERIFY_LOG=str(self.verify_log), **env)
        result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=self.clone, env=runner_env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_resume_at_a_target_carrying_1_3_0s_tooling_builds(self) -> None:
        # The base commit carries a release.py with 1.3.0's parser (build and
        # verify take no option) and a static version; its tag is pushed and
        # its publication interrupted before the cutover.
        tools = self.clone / "tools"
        tools.mkdir()
        (tools / "release.py").write_text(RELEASE_PY_1_3_0)
        base = self.bump("1.0.0")
        self.tag("v1.0.0", base)
        tip = self.squash("feat: after the cutover (#9)")
        code, out, err = self._main("classify", "--commit", tip)
        self.assertEqual(code, 0, err)
        outputs = dict(line.split("=", 1) for line in out.splitlines())
        self.assertEqual(outputs, {"state": "RESUME", "version": "1.0.0", "tag": "v1.0.0", "commit": base})
        fixtures.run(["git", "switch", "-q", "--detach", base], cwd=self.clone)
        self._build_job_step("build", outputs)
        self._build_job_step("verify", outputs)
        self.assertEqual(sorted(p.name for p in (self.clone / "dist").iterdir()), ["pkg-1.0.0.txt"])
        fixtures.run(["git", "switch", "-q", "--detach", tip], cwd=self.clone)
        code, _, err = self._main("publish", "--commit", base)
        self.assertEqual(code, 0, err)
        self.assertFalse(self.release_state("v1.0.0")["isDraft"])
        fixtures.run(["git", "switch", "-q", "main"], cwd=self.clone)
        self.assertEqual(self._main("classify", "--commit", tip)[1].splitlines()[:2],
                         ["state=RELEASE_DUE", "version=1.1.0"])


#: ``tools/release.py``'s ``build`` and ``verify`` as 1.3.0 parses them: no
#: option, and the version read from the committed ``pyproject.toml``.
RELEASE_PY_1_3_0 = """\
import argparse, subprocess, sys, tomllib
parser = argparse.ArgumentParser(prog="release.py")
sub = parser.add_subparsers(dest="command", required=True)
sub.add_parser("build")
sub.add_parser("verify")
args = parser.parse_args()
with open("pyproject.toml", "rb") as handle:
    version = tomllib.load(handle)["project"]["version"]
commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                        check=True).stdout.strip()
if args.command == "build":
    argv = [sys.executable, "build.py", version, commit]
else:
    argv = [sys.executable, "verify.py", f"dist/pkg-{version}.txt", version, commit, f"v{version}"]
sys.exit(subprocess.run(argv).returncode)
"""


if __name__ == "__main__":
    unittest.main()
