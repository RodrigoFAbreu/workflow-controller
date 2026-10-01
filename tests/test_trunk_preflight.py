"""The lifecycle wiring of the milestone-branch preflight
(``workflow-controller-trunk-branch-pr-release-orchestration`` CP8): step 1b of
``job._execute_step_locked``, the worker tool restrictions and the post-step
branch verification under an active binding, the ``inspect``/``explain``/
``status`` blocks, the ``milestone-binding`` subcommand, and -- with no policy
and no binding -- byte-identical 1.1.1 behaviour (I1).

The policy-enabled scenarios run a real ``"2.2"`` target through the real
``cli.main``: a clone of a disposable bare origin carrying a committed,
enabled policy, the executable fake ``gh`` (``tests/fake_gh.py``) first on
``PATH``, and the scripted fake worker (``tests/fake_claude.py``), with the
identity pin and the Workflow Manager stubbed exactly as in
``tests/test_lifecycle_orchestration``.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, gitrepo, job, milestone_branch as mb, repo_policy, routing  # noqa: E402
from controller.errors import GitOperationError, InvalidRepositoryPolicyError  # noqa: E402
from tests import fake_gh, fixtures  # noqa: E402
from tests import test_lifecycle_orchestration as lifecycle  # noqa: E402
from tests.golden import generate_no_policy_lifecycle as golden  # noqa: E402
from tests.test_milestone_branch import policy  # noqa: E402

WI = lifecycle.WI
BRANCH = f"milestone/{WI}"
STATE_REL = lifecycle.STATE_REL
POLICY = repo_policy.POLICY_PATH
MILESTONE_PLAN = f"/milestone-plan {WI}"
MILESTONE_IMPLEMENT = lifecycle.MILESTONE_IMPLEMENT
BINDING_EVENTS = ("bind_planned", "bound", "push_intent", "pushed", "pr_planned", "pr_created", "acknowledged")


@contextlib.contextmanager
def _run_spy():
    """Every ``subprocess.run`` argv (each call once), let through unchanged."""
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(args, *a, **kw):
        calls.append([str(arg) for arg in args])
        return real(args, *a, **kw)

    with unittest.mock.patch("subprocess.run", spy):
        yield calls


def _probe_argv(root: Path) -> list[list[str]]:
    return [["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            ["git", "-C", str(root), "ls-tree", "-z", "HEAD", "--", POLICY]]


# ---------------------------------------------------------------------------
# No policy, no binding: I1.
# ---------------------------------------------------------------------------


class NoPolicyGoldenTest(unittest.TestCase):
    """Job records, worker argv, ``inspect``/``explain`` JSON and exit codes,
    re-derived from the code as it stands, equal the golden captured from
    commit ``86801f6``, before CP8 (with worker-lifecycle-ownership CP3's
    additive record fields and streaming argv, the generator's docstring)."""

    def test_the_no_policy_lifecycle_is_byte_identical_to_the_pre_cp8_golden(self) -> None:
        derived = golden.render(golden.derive())
        self.assertEqual(derived, golden.GOLDEN_PATH.read_text())
        for key in ("repository_policy", "milestone_branch", "repository_preflight", "branch_binding"):
            self.assertNotIn(f'"{key}"', derived, f"a no-policy document carries {key}")
        self.assertIn("Agent,Workflow,Skill", derived)


class ProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name).resolve()
        self.runtime = self.tmp / "runtime"

    def ctx(self, root: Path) -> mb.Context:
        return mb.Context(repo_root=root, runtime_root=self.runtime)

    def test_no_policy_is_exactly_the_two_probe_calls_and_nothing_else(self) -> None:
        root = fixtures.build_managed_repo(self.tmp / "repo").resolve()
        with _run_spy() as calls:
            outcome = mb.repository_preflight(self.ctx(root))
        self.assertEqual(outcome, mb.Proceed())
        self.assertEqual(calls, _probe_argv(root))
        self.assertFalse(self.runtime.exists(), "the probe wrote to the runtime root")

    def test_an_unborn_head_is_four_calls_and_classified_absent(self) -> None:
        root = fixtures.build_bare_git_repo(self.tmp / "unborn").resolve()
        fixtures.write_installation_manifest(root)
        with _run_spy() as calls:
            outcome = mb.repository_preflight(self.ctx(root))
        self.assertEqual(outcome, mb.Proceed())
        self.assertEqual(calls, _probe_argv(root) + [
            ["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"],
            ["git", "-C", str(root), "symbolic-ref", "-q", "HEAD"]])

    def test_an_undecidable_listing_refuses(self) -> None:
        root = fixtures.build_managed_repo(self.tmp / "repo").resolve()
        real = gitrepo.subprocess_runner()

        def runner(argv):
            if argv[3] == "ls-tree":
                return subprocess.CompletedProcess(argv, 128, b"", b"fatal: bad tree object")
            return real(argv)

        # HEAD is born, so exit 128 is not the unborn case: never "absent" (I9).
        with self.assertRaises(GitOperationError):
            mb.repository_preflight(mb.Context(repo_root=root, runtime_root=self.runtime, runner=runner))

    def test_a_policy_committed_at_head_but_deleted_in_the_worktree_is_still_active(self) -> None:
        origin, clone = fixtures.build_origin_pair(self.tmp)
        (clone / POLICY).parent.mkdir(parents=True)
        (clone / POLICY).write_text(json.dumps(policy()) + "\n")
        fixtures.write_workflow_state(clone, {"schema_version": 1, "active_work_item_id": None, "work_items": {}})
        fixtures.commit_all(clone, "Add the policy")
        fixtures.run(["git", "push", "-q", "origin", "main"], cwd=clone)
        (clone / POLICY).unlink()
        found = mb.probe(self.ctx(clone))
        self.assertTrue(found.policy_committed)
        self.assertFalse(found.inactive)
        with self.assertRaises(mb.BranchBindingError) as caught:
            mb.repository_preflight(self.ctx(clone))
        # The trunk start runs -- and refuses the deletion as a tracked change.
        self.assertIn("tracked tree on main has changes", caught.exception.message)

    def test_an_inadmissible_committed_policy_refuses(self) -> None:
        root = fixtures.build_managed_repo(self.tmp / "repo").resolve()
        (root / POLICY).parent.mkdir(parents=True)
        (root / POLICY).write_text('{"schema_version": 99}\n')
        fixtures.commit_all(root, "Add a bad policy")
        with self.assertRaises(InvalidRepositoryPolicyError):
            mb.repository_preflight(self.ctx(root))


# ---------------------------------------------------------------------------
# Worker restrictions.
# ---------------------------------------------------------------------------


class DisallowedToolsTest(unittest.TestCase):
    #: The pinned list, in order (CP8).
    EXPECTED = ("Bash(gh:*)", "Bash(git push:*)", "Bash(git rebase:*)", "Bash(git switch:*)",
                "Bash(git checkout -b:*)", "Bash(git reset --hard:*)")

    def test_the_branch_guard_list_is_pinned(self) -> None:
        self.assertEqual(routing.BRANCH_GUARD_TOOLS, self.EXPECTED)
        for absent in ("merge", "branch", "tag"):
            self.assertFalse(any(tool.startswith(f"Bash(git {absent}") for tool in routing.BRANCH_GUARD_TOOLS))

    def test_the_union_with_the_subagent_tools(self) -> None:
        # Worker-lifecycle-ownership CP3: every list ends with the unownable
        # tools, including a route that disallowed nothing before.
        single = routing.NO_OVERRIDES.resolve(routing.REVIEW_IMPLEMENTATION)
        multi = routing.NO_OVERRIDES.resolve(routing.MILESTONE_PLAN)
        unownable = routing.ASYNC_UNOWNABLE_TOOLS
        self.assertEqual(unownable, ("CronCreate", "CronDelete", "RemoteTrigger"))
        self.assertEqual(routing.worker_disallowed_tools(single, branch_bound=False),
                         routing.SUBAGENT_TOOLS + unownable)
        self.assertEqual(routing.worker_disallowed_tools(multi, branch_bound=False), unownable)
        self.assertEqual(routing.worker_disallowed_tools(single, branch_bound=True),
                         routing.SUBAGENT_TOOLS + self.EXPECTED + unownable)
        self.assertEqual(routing.worker_disallowed_tools(multi, branch_bound=True), self.EXPECTED + unownable)


# ---------------------------------------------------------------------------
# A policy-enabled target, end to end through the CLI.
# ---------------------------------------------------------------------------


class _PolicyCase(lifecycle._LifecycleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.origin, clone = fixtures.build_origin_pair(self.tmp_root)
        self.root = clone.resolve()
        (self.root / ".gitignore").write_text(".ai-review/\n")
        fixtures.write_installation_manifest(self.root)
        (self.root / POLICY).parent.mkdir(parents=True, exist_ok=True)
        (self.root / POLICY).write_text(json.dumps(policy(), indent=2) + "\n")
        self.write_state(None, {})
        self.trunk_tip = fixtures.commit_all(self.root, "Add the policy and the Workflow state")
        self.git("push", "-q", "origin", "main")
        self.lc = lifecycle.Lifecycle(self.tmp_root, self.root, self.trunk_tip, phase="PLANNING",
                                      checkpoints={}, plan_approval=None)
        self.gh_env = fixtures.fake_gh_env(self.tmp_root, origin=self.origin)
        self.key = mb.repo_key(gitrepo.common_dir(self.root))
        self.diag_log = self.tmp_root / "worker-argv.jsonl"

    # -- the target ---------------------------------------------------------------------

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return fixtures.run(["git", *args], cwd=cwd or self.root).stdout.strip()

    def head(self) -> gitrepo.HeadState:
        return gitrepo.head_state(self.root)

    def write_state(self, active: str | None, items: dict) -> None:
        fixtures.write_workflow_state(self.root, {"schema_version": 1, "active_work_item_id": active,
                                                  "work_items": items})

    def plan(self) -> None:
        """``/milestone-plan``: the work item and its plan files, uncommitted, on the trunk."""
        self.lc.entry.update(phase="PLANNING", plan_approval=None, base_commit=fixtures.current_head(self.root))
        fixtures.write_registry(self.root, WI, lifecycle.CHECKPOINT_IDS)
        (self.root / "PLAN.md").write_text("the plan\n")
        self.write_state(WI, {WI: self.lc.entry})

    def approve(self) -> str:
        self.lc.entry.update(phase=lifecycle.IMPLEMENTING, plan_approval={"status": "CURRENT"})
        self.write_state(WI, {WI: self.lc.entry})
        return fixtures.commit_all(self.root, fixtures.trailer_message(
            f"Approve plan for {WI}", ("Workflow-Plan-Approval", "ab" * 32), ("Workflow-Work-Item", WI)))

    def accept(self) -> str:
        self.lc.entry.update(phase="MILESTONE_COMPLETE")
        self.write_state(None, {WI: self.lc.entry})
        return fixtures.commit_all(self.root, fixtures.trailer_message(f"Accept {WI}", ("Workflow-Work-Item", WI)))

    # -- the command line ---------------------------------------------------------------

    def cli(self, command: str, *args: str, json_out: bool = False, work_item: str | None = None,
            with_repo: bool = True) -> lifecycle.Run:
        fixtures.write_worker_script(self.lc.script_path, self.lc.script)
        jobs_dir = self.lc.runtime / "jobs"
        before = {p.name for p in jobs_dir.glob("*.json")} if jobs_dir.is_dir() else set()
        env = {**self.gh_env, "FAKE_CLAUDE_SCRIPT": str(self.lc.script_path),
               "FAKE_CLAUDE_INVOCATIONS_FILE": str(self.lc.processes_file),
               "FAKE_CLAUDE_DIAG_LOG": str(self.diag_log)}
        argv = ["--runtime-dir", str(self.lc.runtime), "--workflow-manager", str(self.stub_manager),
                "--claude-binary", str(lifecycle.FAKE_CLAUDE), "--timeout", "60"]
        if work_item is not None:
            argv += ["--work-item", work_item]
        if json_out:
            argv.append("--json")
        argv += [command, *args] + ([str(self.root)] if with_repo else [])
        stdout, stderr = io.StringIO(), io.StringIO()
        with unittest.mock.patch.dict(os.environ, env), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        created = sorted((p for p in jobs_dir.glob("*.json") if p.name not in before),
                         key=lambda p: p.stat().st_mtime_ns) if jobs_dir.is_dir() else []
        return lifecycle.Run(code, [json.loads(p.read_text()) for p in created], stdout.getvalue(),
                             stderr.getvalue())

    def worker_count(self) -> int:
        return lifecycle._LifecycleTestCase.processes(self.lc)

    def record(self) -> dict | None:
        return mb.read_record(self.lc.runtime, self.key, WI)

    def binding_events(self) -> list[dict]:
        path = self.lc.runtime / mb.events_rel(self.key, WI)
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def gh_calls(self, group: str | None = None, sub: str | None = None) -> list[list[str]]:
        calls = fake_gh.invocations(Path(self.gh_env["FAKE_GH_LOG"]))
        return [c for c in calls if group is None or c[:2] == [group, sub]]

    def gh_edit(self, number: int, **fields) -> None:
        state_path = Path(self.gh_env["FAKE_GH_STATE"])
        data = fake_gh.read_state(state_path)
        for pr in data["prs"]:
            if pr["number"] == number:
                pr.update(fields)
        fake_gh.write_state(state_path, data)

    def worker_argv(self) -> list[list[str]]:
        return [json.loads(line)["argv"] for line in self.diag_log.read_text().splitlines()] \
            if self.diag_log.exists() else []

    # -- lifecycle shortcuts --------------------------------------------------------------

    def bind_step(self) -> lifecycle.Run:
        """Plan on the trunk, then ``step``: the bind, and the plan-stage worker on the branch."""
        self.plan()
        result = self.cli("step")
        self.assertNotEqual(result.code, cli.EXIT_FAIL_CLOSED, result.stderr)
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.assertEqual(self.head().branch, BRANCH)
        return result

    def open_pr_step(self, implement=None) -> lifecycle.Run:
        """Bind, approve the plan on the branch, and ``step``: the Draft PR, then
        the implementation worker, scripted with ``implement()``'s actions when
        given (called once the plan is approved)."""
        self.bind_step()
        self.approve()
        if implement is not None:
            self.lc.add(MILESTONE_IMPLEMENT, implement())
        result = self.cli("step")
        self.assertNotEqual(result.code, cli.EXIT_FAIL_CLOSED, result.stderr)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        return result

    def assert_no_launch(self, result: lifecycle.Run, processes_before: int) -> None:
        self.assertEqual(self.worker_count(), processes_before, "a worker was launched")
        self.assertTrue(all("worker" not in record for record in result.records))


class PolicyStepTest(_PolicyCase):
    def test_the_bind_step_launches_the_plan_worker_on_the_branch_with_the_guard_tools(self) -> None:
        result = self.bind_step()
        [record] = result.records
        self.assertEqual(record["selected_action"]["command"], MILESTONE_PLAN)
        self.assertEqual(record["branch_binding"], {"work_item_id": WI, "branch": BRANCH,
                                                    "pre_step_tip": self.trunk_tip})
        [argv] = self.worker_argv()
        self.assertEqual(argv[-2:], ["--disallowedTools",
                                     ",".join(routing.BRANCH_GUARD_TOOLS + routing.ASYNC_UNOWNABLE_TOOLS)])

    def test_a_single_agent_worker_gets_the_union(self) -> None:
        self.assertEqual(self.open_pr_step(lambda: self.lc.implement("CP1")).code, cli.EXIT_OK)
        self.lc.add(MILESTONE_IMPLEMENT, self.lc.implement("CP2", last=True))
        self.assertEqual(self.cli("step").code, cli.EXIT_OK)
        self.lc.add(MILESTONE_IMPLEMENT, self.lc.generate())
        result = self.cli("step")
        self.assertEqual(result.records[-1]["worker_route"]["single_agent"], True, result.stderr)
        self.assertEqual(self.worker_argv()[-1][-1], ",".join(
            routing.SUBAGENT_TOOLS + routing.BRANCH_GUARD_TOOLS + routing.ASYNC_UNOWNABLE_TOOLS))

    def test_a_step_on_the_wrong_branch_refuses(self) -> None:
        self.git("switch", "-q", "-c", "feature-x")
        before = self.worker_count()
        result = self.cli("step")
        self.assertEqual(result.code, cli.EXIT_FAIL_CLOSED)
        self.assertIn("HEAD is on feature-x", result.stderr)
        self.assertEqual(result.records, [])
        self.assert_no_launch(result, before)

    def test_on_a_bound_branch_an_unrelated_work_item_refuses_and_a_child_is_admitted(self) -> None:
        self.bind_step()
        other = dict(self.lc.entry, work_item_id="wi-2", registry_path=fixtures.registry_rel_path("wi-2"))
        child = dict(self.lc.entry, work_item_id="wi-1-fix", parent_work_item_id=WI,
                     registry_path=fixtures.registry_rel_path("wi-1-fix"))
        fixtures.write_registry(self.root, "wi-2", lifecycle.CHECKPOINT_IDS)
        fixtures.write_registry(self.root, "wi-1-fix", lifecycle.CHECKPOINT_IDS)
        self.write_state(WI, {WI: self.lc.entry, "wi-2": other, "wi-1-fix": child})
        before = self.worker_count()
        refused = self.cli("step", work_item="wi-2")
        self.assertEqual(refused.code, cli.EXIT_FAIL_CLOSED)
        self.assertIn("wi-2 is neither wi-1", refused.stderr)
        self.assertEqual(refused.records, [])
        self.assert_no_launch(refused, before)
        for admitted in (WI, "wi-1-fix"):
            with self.subTest(work_item=admitted):
                result = self.cli("step", work_item=admitted)
                self.assertNotEqual(result.code, cli.EXIT_FAIL_CLOSED, result.stderr)
                self.assertEqual(result.records[-1]["work_item_id"], admitted)
                self.assertEqual(result.records[-1]["branch_binding"]["branch"], BRANCH)

    def test_a_worker_that_keeps_the_branch_finishes(self) -> None:
        result = self.open_pr_step(lambda: self.lc.implement("CP1"))
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assertEqual(result.records[-1]["status"], job.STATUS_FINISHED)
        self.assertEqual(self.head().branch, BRANCH)

    def test_a_worker_that_switches_branch_fails_its_job(self) -> None:
        self.bind_step()
        pre = self.approve()
        self.lc.add(MILESTONE_IMPLEMENT, self.lc.implement("CP1") + [fixtures.script_git("switch", "-q", "-c", "x")])
        result = self.cli("step")
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED)
        record = result.records[-1]
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["code"], job.BRANCH_INVARIANT_VIOLATED_CODE)
        self.assertIn("not on the bound branch milestone/wi-1", evidence["message"])
        self.assertEqual(evidence["evidence"]["pre_step_tip"], pre)
        self.assertEqual(self.head().branch, "x", "the Controller repaired the branch")

    def test_a_worker_that_rewrites_history_fails_its_job(self) -> None:
        # The worker squashes its checkpoint commit into the approval commit:
        # the state it leaves is intact, but the tip no longer descends from
        # the pre-step tip.
        result = self.open_pr_step(lambda: self.lc.implement("CP1") + [
            fixtures.script_git("reset", "-q", "--soft", "HEAD~2"),
            fixtures.script_git("commit", "-q", "-m", "Squash the approval and CP1")])
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED)
        evidence = result.records[-1]["reconciliation_evidence"]
        self.assertEqual(evidence["code"], job.BRANCH_INVARIANT_VIOLATED_CODE)
        self.assertIn("does not descend from the pre-step tip", evidence["message"])

    def test_resume_applies_the_post_step_verification_too(self) -> None:
        finished = self.open_pr_step(lambda: self.lc.implement("CP1")).records[-1]
        self.assertEqual(finished["status"], job.STATUS_FINISHED)
        self.rewrite_as(self.lc, finished, job.STATUS_COMPLETED)
        self.git("switch", "-q", "-c", "elsewhere")
        result = self.cli("resume")
        reconciled = self.read_record(self.lc, finished["job_id"])
        self.assertEqual(reconciled["status"], job.STATUS_FAILED, result.stderr)
        self.assertEqual(reconciled["reconciliation_evidence"]["code"], job.BRANCH_INVARIANT_VIOLATED_CODE)


class ObservationTest(_PolicyCase):
    def test_inspect_and_explain_on_the_trunk_carry_the_policy_and_the_bind_prediction(self) -> None:
        self.plan()
        inspected = json.loads(self.cli("inspect", json_out=True).stdout)
        self.assertEqual(inspected["repository_policy"]["source"], "HEAD")
        self.assertEqual(inspected["repository_policy"]["trunk"], {"branch": "main", "remote": "origin"})
        self.assertTrue(inspected["repository_policy"]["milestone_branches_enabled"])
        self.assertNotIn("milestone_branch", inspected)
        explained = json.loads(self.cli("explain", json_out=True).stdout)
        self.assertEqual(explained["repository_preflight"]["action"], "bind")
        self.assertEqual(explained["repository_preflight"]["branch"], BRANCH)

    def test_explain_names_the_trunk_tip_as_the_next_base(self) -> None:
        """Squash-merge Design F: with no work item, the bootstrap ``explain``
        predicts is ``/milestone-plan <trunk tip>``, as ``step`` launches it."""
        explained = json.loads(self.cli("explain", json_out=True).stdout)
        self.assertEqual(explained["repository_preflight"]["base"], self.trunk_tip)
        self.assertEqual(explained["action"], f"/milestone-plan {self.trunk_tip}")
        self.assertTrue(explained["automatic"])
        # Behind the remote trunk (as of the last fetch), no base is predicted.
        other = self.tmp_root / "other"
        fixtures.git_clone(self.origin, other)
        fixtures.run(["git", "-c", "user.email=o@example.invalid", "-c", "user.name=O", "commit", "-q",
                      "--allow-empty", "-m", "elsewhere"], cwd=other)
        fixtures.run(["git", "push", "-q", "origin", "main"], cwd=other)
        self.git("fetch", "-q", "origin")
        explained = json.loads(self.cli("explain", json_out=True).stdout)
        self.assertIsNone(explained["repository_preflight"]["base"])
        self.assertEqual(explained["action"], "/milestone-plan")

    def test_inspect_and_explain_on_a_bound_branch(self) -> None:
        self.open_pr_step()
        inspected = json.loads(self.cli("inspect", json_out=True).stdout)
        self.assertEqual(inspected["repository_policy"]["source"], "binding")
        binding = inspected["milestone_branch"]
        self.assertEqual((binding["state"], binding["branch"], binding["branch_point"]),
                         (mb.PR_OPEN, BRANCH, self.trunk_tip))
        self.assertEqual(binding["pr"]["number"], 1)
        self.assertTrue(binding["pr"]["draft"])
        self.assertEqual(set(binding["last_observation"]),
                         {"observed_at", "tip", "remote_branch", "remote_trunk", "fresh", "behind"})
        # Every pre-existing key is still there, unchanged in shape.
        self.assertEqual(set(inspected) - {"repository_policy", "milestone_branch"},
                         {"repository", "work_item", "lifecycle_lock", "controller", "last_job_telemetry"})
        text = self.cli("inspect").stdout
        self.assertIn(f"milestone branch: {BRANCH} for {WI} (PR_OPEN)", text)
        self.assertIn("repository policy: .workflow-controller/policy.json (binding", text)

    def test_explain_predicts_without_fetching_or_calling_gh(self) -> None:
        self.open_pr_step()
        fixtures.run(["git", "commit", "-q", "--allow-empty", "-m", "more"], cwd=self.root)
        gh_before = len(self.gh_calls())
        with fixtures.git_call_spy() as calls:
            result = self.cli("explain", json_out=True)
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        for argv in calls:
            self.assertFalse({"fetch", "ls-remote", "push", "pull"} & set(argv), argv)
        self.assertEqual(len(self.gh_calls()), gh_before)
        predicted = json.loads(result.stdout)["repository_preflight"]
        self.assertEqual(predicted["action"], "push")
        self.assertEqual(predicted["binding_state"], mb.PR_OPEN)
        self.assertIsNotNone(predicted["as_of"])
        self.assertIn(f"(as of {predicted['as_of']})", predicted["detail"])
        self.assertIn("repository preflight: push", self.cli("explain").stdout)

    def test_status_names_each_binding(self) -> None:
        self.bind_step()
        result = self.cli("status", with_repo=False)
        self.assertIn(f"milestone: {WI} BRANCH_BOUND on {BRANCH} (worktree {self.root})", result.stdout)

    def test_follow_output_is_unchanged_on_a_policy_enabled_fixture(self) -> None:
        """The step that pushes and opens the Draft PR renders, through
        ``follow``, exactly what the same step renders on a target with no
        policy: binding events go to the binding's own log only."""
        self.bind_step()
        self.approve()
        result = self.cli("step")  # the push and the Draft PR happen in this step's preflight
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.assertTrue({"push_intent", "pushed", "pr_planned", "pr_created"}
                        <= {event["event"] for event in self.binding_events()})
        followed = self.cli("follow", "--run", result.records[-1]["run_id"], "--from-start")
        self.assertEqual(followed.code, cli.EXIT_OK, followed.stderr)

        plain = self.seed("no-policy", lifecycle.IMPLEMENTING)
        base = lifecycle._LifecycleTestCase.cli(self, plain, "step")
        self.assertEqual(base.code, result.code)
        plain_followed = lifecycle._LifecycleTestCase.cli(self, plain, "follow", "--run",
                                                         base.records[-1]["run_id"], "--from-start")
        self.assertEqual(_normalise_follow(followed.stdout), _normalise_follow(plain_followed.stdout))
        for record in result.records:
            log = (self.lc.runtime / "jobs" / record["job_id"] / "events.jsonl").read_text()
            self.assertFalse({json.loads(line)["event"] for line in log.splitlines()} & set(BINDING_EVENTS))
        run_log = (self.lc.runtime / "runs" / result.records[-1]["run_id"] / "events.jsonl").read_text()
        self.assertFalse({json.loads(line)["event"] for line in run_log.splitlines()} & set(BINDING_EVENTS))


def _normalise_follow(text: str) -> str:
    text = re.sub(r"\b\d{2}:\d{2}:\d{2}\b", "<T>", text)
    text = re.sub(r"\b\d{8}T\d{6}Z-[0-9a-f]{8}\b", "<ID>", text)
    return re.sub(r"\b(pid|process group) \d+", r"\1 <N>", text)


class MilestoneBindingTest(_PolicyCase):
    def test_the_parser_requires_the_work_item_and_one_disposition(self) -> None:
        for argv in (["milestone-binding", "--new-pr", "repo"],
                     ["--work-item", WI, "milestone-binding", "repo"],
                     ["--work-item", WI, "milestone-binding", "--new-pr", "--abandon", "repo"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    cli.main(argv)
                self.assertEqual(caught.exception.code, cli.EXIT_USAGE)
        self.assertIn("milestone-binding", cli.ALL_COMMANDS)
        self.assertNotIn("milestone-binding", cli.READ_ONLY_COMMANDS)

    def test_abandon_after_a_closed_pr_then_the_trunk_plans_again(self) -> None:
        self.open_pr_step()
        self.gh_edit(1, state="CLOSED")
        before = self.worker_count()
        gated = self.cli("step")
        self.assertEqual(gated.code, cli.EXIT_GATE, gated.stderr)
        self.assertEqual(gated.records[-1]["human_gate_pending"]["phase"], "MILESTONE_BRANCH")
        self.assert_no_launch(gated, before)
        self.assertEqual(self.record()["state"], mb.PR_CLOSED_UNMERGED)
        acknowledged = self.cli("milestone-binding", "--abandon", work_item=WI)
        self.assertEqual(acknowledged.code, cli.EXIT_OK, acknowledged.stderr)
        self.assertEqual(acknowledged.records, [])
        self.assertEqual(self.record()["state"], mb.ABANDONED)
        self.assertEqual(self.worker_count(), before)
        self.git("switch", "-q", "main")
        result = self.cli("step")
        self.assertEqual(result.records[-1]["selected_action"]["command"], f"/milestone-plan {self.head().commit}")
        self.assertNotIn("branch_binding", result.records[-1])

    def test_new_pr_after_a_merge_before_acceptance(self) -> None:
        self.open_pr_step()
        human = self.human_merge(1)
        gated = self.cli("step")
        self.assertEqual(gated.code, cli.EXIT_GATE, gated.stderr)
        self.assertEqual(self.record()["state"], mb.MERGED_BEFORE_ACCEPTANCE)
        acknowledged = self.cli("milestone-binding", "--new-pr", work_item=WI)
        self.assertEqual(acknowledged.code, cli.EXIT_OK, acknowledged.stderr)
        self.assertEqual((self.record()["state"], self.record()["pr"], self.record()["superseded_prs"]),
                         (mb.BRANCH_BOUND, None, [1]))
        creates = len(self.gh_calls("pr", "create"))
        before = self.worker_count()
        launched = self.cli("step")
        self.assertEqual(self.worker_count(), before + 1, launched.stderr)
        self.assertEqual(launched.records[-1]["selected_action"]["command"], MILESTONE_IMPLEMENT)
        self.assertEqual(len(self.gh_calls("pr", "create")), creates)
        fixtures.run(["git", "commit", "-q", "--allow-empty", "-m", "next branch commit"], cwd=self.root)
        self.cli("step")
        self.assertEqual(len(self.gh_calls("pr", "create")), creates + 1)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.assertEqual(self.record()["pr"]["number"], 2)
        self.assertTrue(self.record()["pr"]["is_draft"])
        del human

    def human_merge(self, number: int) -> str:
        """A human merges pull request ``number`` on GitHub with a merge commit."""
        human = self.tmp_root / "human"
        fixtures.git_clone(self.origin, human)
        self.git("config", "user.email", "human@example.invalid", cwd=human)
        self.git("config", "user.name", "Human", cwd=human)
        head = self.git("--git-dir", str(self.origin), "rev-parse", f"refs/heads/{BRANCH}")
        self.git("merge", "-q", "--no-ff", "-m", f"Merge pull request #{number}", f"origin/{BRANCH}", cwd=human)
        self.git("push", "-q", "origin", "main", cwd=human)
        self.gh_edit(number, state="MERGED", headRefOid=head, mergedAt="2026-09-25T01:00:00Z",
                     mergeCommit={"oid": fixtures.current_head(human)})
        return head

    def _accepted_then_closed(self) -> None:
        """An accepted milestone whose PR is closed unmerged, then ``--new-pr``,
        and the branch fast-forwarded into ``main`` by hand on the origin."""
        self.open_pr_step()
        tip = self.accept()
        self.gh_edit(1, state="CLOSED")
        self.assertEqual(self.cli("step").code, cli.EXIT_GATE)
        self.assertEqual(self.record()["state"], mb.PR_CLOSED_UNMERGED)
        self.assertEqual(self.cli("milestone-binding", "--new-pr", work_item=WI).code, cli.EXIT_OK)
        self.git("push", "-q", "origin", f"{tip}:refs/heads/main")

    def test_the_prless_close_out_from_the_branch(self) -> None:
        self._accepted_then_closed()
        creates = len(self.gh_calls("pr", "create"))
        result = self.cli("step")
        self.assertEqual(self.record()["state"], mb.CLOSED, result.stderr)
        self.assertEqual(self.head().branch, "main")
        self.assertEqual(result.records[-1]["selected_action"]["command"], f"/milestone-plan {self.head().commit}")
        self.assertNotIn("branch_binding", result.records[-1])
        self.assertEqual(len(self.gh_calls("pr", "create")), creates)

    def test_the_prless_close_out_from_the_trunk(self) -> None:
        self._accepted_then_closed()
        creates = len(self.gh_calls("pr", "create"))
        self.git("switch", "-q", "main")
        self.git("fetch", "-q", "origin")
        self.git("merge", "-q", "--ff-only", "origin/main")
        result = self.cli("step")
        self.assertEqual(self.record()["state"], mb.CLOSED, result.stderr)
        self.assertEqual(result.records[-1]["selected_action"]["command"], f"/milestone-plan {self.head().commit}")
        self.assertEqual(len(self.gh_calls("pr", "create")), creates)

    def test_a_plan_discarded_after_the_bind(self) -> None:
        self.bind_step()
        self.git("checkout", "--", ".")
        self.git("clean", "-fdq")
        before = self.worker_count()
        gated = self.cli("step")
        self.assertEqual(gated.code, cli.EXIT_GATE, gated.stderr)
        self.assertIn("bound_item_missing", gated.records[-1]["human_gate_pending"]["what_is_required"])
        self.assert_no_launch(gated, before)
        self.assertEqual(self.cli("milestone-binding", "--abandon", work_item=WI).code, cli.EXIT_OK)
        self.assertEqual(self.record()["state"], mb.ABANDONED)
        self.git("switch", "-q", "main")
        result = self.cli("step")
        self.assertEqual(result.records[-1]["selected_action"]["command"], f"/milestone-plan {self.head().commit}")

    def test_a_refused_disposition_exits_20_and_changes_nothing(self) -> None:
        self.bind_step()
        path = self.lc.runtime / mb.record_rel(self.key, WI)
        before, events = path.read_bytes(), len(self.binding_events())
        result = self.cli("milestone-binding", "--new-pr", work_item=WI)
        self.assertEqual(result.code, cli.EXIT_FAIL_CLOSED)
        self.assertIn("--new-pr does not apply to the BRANCH_BOUND binding", result.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.binding_events()), events)
        self.assertEqual(result.records, [])

    def test_no_binding_record_refuses_with_the_ordinary_dispatch_footprint(self) -> None:
        result = self.cli("milestone-binding", "--abandon", work_item=WI)
        self.assertEqual(result.code, cli.EXIT_FAIL_CLOSED)
        self.assertIn(f"no binding record for {WI}", result.stderr)
        self.assertFalse((self.lc.runtime / "repositories").exists())
        self.assertFalse((self.lc.runtime / "jobs").exists())
        footprint = sorted(p.relative_to(self.lc.runtime).as_posix() for p in self.lc.runtime.rglob("*"))
        # A `step` refused in its own dispatch leaves the same runtime root.
        refused_root = self.tmp_root / "refused-step-runtime"
        with contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--runtime-dir", str(refused_root), "--routing-config",
                             str(self.tmp_root / "missing.json"), "step", str(self.root)])
        self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
        self.assertEqual(sorted(p.relative_to(refused_root).as_posix() for p in refused_root.rglob("*")), footprint)
        self.assertEqual(self.worker_count(), 0)


if __name__ == "__main__":
    unittest.main()
