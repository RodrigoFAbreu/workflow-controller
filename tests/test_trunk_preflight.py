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
import hashlib
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
from tests import process_fixtures  # noqa: E402
from tests.test_milestone_branch import policy  # noqa: E402
from tests.test_pull_request_lifecycle import TITLE, plan_text, release_wait_policy  # noqa: E402

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


class NoPolicyGoldenMergeRowsTest(unittest.TestCase):
    """auto-merge-release-wait I1 (revision 6): CP1 regenerated the golden,
    and its only difference from the base's (``854d25c``) is the three
    ``merge.*`` keys added to each job record's ``controller_settings``
    ``values`` and ``sources``."""

    #: The SHA-256 of ``tests/golden/no_policy_lifecycle.json`` at
    #: ``854d25c`` (1.5.0).
    BASE_SHA256 = "41c176990518852a07819071f114e24f4c76411cf66c3ef90b9438c4114817f1"
    MERGE_KEYS = ("merge.auto", "merge.poll_seconds", "merge.wait_seconds")

    def test_the_golden_differs_from_the_base_only_by_the_merge_rows(self) -> None:
        data = json.loads(golden.GOLDEN_PATH.read_text())
        blocks = []

        def collect(node: object) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("controller_settings"), dict):
                    blocks.append(node["controller_settings"])
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for value in node:
                    collect(value)

        collect(data)
        self.assertEqual(len(blocks), 8)
        expected = {"values": {"merge.auto": True, "merge.poll_seconds": 30, "merge.wait_seconds": 3600},
                    "sources": dict.fromkeys(self.MERGE_KEYS, "file")}
        for block in blocks:
            for part in ("values", "sources"):
                self.assertEqual({key: block[part].pop(key) for key in self.MERGE_KEYS}, expected[part])
                self.assertFalse([key for key in block[part] if key.startswith("merge.")])
        self.assertEqual(hashlib.sha256(golden.render(data).encode("utf-8")).hexdigest(), self.BASE_SHA256)


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

    def test_a_close_out_after_the_release_wait_stops_the_step(self) -> None:
        # auto-merge-release-wait D.4: no worker, no job record, exit 0, and
        # the run's no_action event names the release; the next step plans.
        release = {"state": "ALREADY_RELEASED", "version": "1.6.0", "tag": "v1.6.0",
                   "url": "https://github.com/example-owner/example-repo/releases/tag/v1.6.0"}
        closed = {"work_item_id": WI, "state": mb.CLOSED, "release": release}
        stop = mb.Proceed(binding=closed, action="closed_out", stop=True)
        before = self.worker_count()
        with unittest.mock.patch.object(mb, "repository_preflight", return_value=stop):
            result = self.cli("step")
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assert_no_launch(result, before)
        self.assertEqual(result.records, [])
        self.assertFalse((self.lc.runtime / "jobs").is_dir() and any((self.lc.runtime / "jobs").glob("*.json")))
        [events] = (self.lc.runtime / "runs").glob("*/events.jsonl")
        [no_action] = [json.loads(line) for line in events.read_text().splitlines()
                       if json.loads(line)["event"] == "no_action"]
        self.assertEqual((no_action["observed_phase"], no_action["reason"], no_action["release"]),
                         ("MILESTONE_COMPLETE", job.REASON_CLOSED_OUT_RELEASED, f"v1.6.0 {release['url']}"))
        result = self.cli("step")
        self.assertEqual(result.records[-1]["selected_action"]["command"], f"/milestone-plan {self.trunk_tip}")

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
        # auto-merge-release-wait F: merge and release are additions, None until written.
        self.assertEqual(set(binding), {"work_item_id", "state", "branch", "trunk", "branch_point",
                                        "binding_generation", "pr", "last_observation", "merge", "release"})
        self.assertEqual((binding["merge"], binding["release"]), (None, None))
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
        self.assertIn(f"milestone: {WI} BRANCH_BOUND on {BRANCH} (worktree {self.root})\n", result.stdout)
        [entry] = json.loads(self.cli("status", json_out=True, with_repo=False).stdout)["bindings"]
        self.assertEqual((entry["merge"], entry["release"]), (None, None))

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



#: ``cli.main`` in a child process with the pinned identity (``sys.argv``:
#: the Controller checkout, the identity's source root, source commit,
#: pinned time and version, then the command line).
_CHILD_CLI = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pathlib import Path; from controller import cli, identity; "
    "ident = identity.ControllerIdentity(generation=1, source_root=Path(sys.argv[2]), "
    "origin_source_root=Path(sys.argv[2]), source_kind=identity.SOURCE_KIND_COMMIT, "
    "source_commit=sys.argv[3], tree_digest='d'*64, generation_source='head', "
    "pinned_at=sys.argv[4], version=sys.argv[5]); "
    "identity.pin = lambda: ident; identity.current = lambda: ident; "
    "sys.exit(cli.main(sys.argv[6:]))"
)


class RunWaitTest(_PolicyCase):
    """``run``'s bounded wait end to end (auto-merge-release-wait CP5,
    Design E): an auto-merge binding with releases versioned by
    ``pyproject.toml``, over the fake forge, with the wait's sleep and
    clock replaced."""

    def setUp(self) -> None:
        super().setUp()
        (self.root / POLICY).write_text(json.dumps(release_wait_policy(), indent=2) + "\n")
        (self.root / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "1.0.0"\n')
        self.trunk_tip = fixtures.commit_all(self.root, "Opt in to auto-merge, version 1.0.0")
        self.git("push", "-q", "origin", "main")
        self.now = 0.0
        self.sleeps: list[float] = []
        self.changes: list = []

    def plan(self) -> None:
        super().plan()
        self.lc.entry.update(plan_path="PLAN.md")
        (self.root / "PLAN.md").write_text(plan_text(TITLE))
        self.write_state(WI, {WI: self.lc.entry})

    # -- GitHub's side, and the wait's clock --------------------------------------------

    def gh(self) -> dict:
        return fake_gh.read_state(Path(self.gh_env["FAKE_GH_STATE"]))

    def set_checks(self, number: int, bucket: str) -> None:
        self.gh_edit(number, checks=[{"name": "ci", "state": bucket.upper(), "bucket": bucket}])

    def origin_ref(self, ref: str) -> str:
        return fixtures.run(["git", "--git-dir", str(self.origin), "rev-parse", ref]).stdout.strip()

    def release(self) -> str:
        """The publishing workflow's run at the trunk tip publishes v1.0.0; its URL."""
        m = self.origin_ref("refs/heads/main")
        fixtures.run(["git", "--git-dir", str(self.origin), "tag", "v1.0.0", m])
        data = self.gh()
        url = f"{data['url']}/releases/tag/v1.0.0"
        data["releases"].append({"tagName": "v1.0.0", "isDraft": False, "url": url, "assets": []})
        data["runs"].append({"databaseId": 100, "workflowName": "Main", "workflowFile": "main.yml",
                             "headSha": m, "headBranch": "main", "event": "push", "status": "completed",
                             "conclusion": "success", "attempt": 1, "url": "https://example.invalid/runs/100"})
        fake_gh.write_state(Path(self.gh_env["FAKE_GH_STATE"]), data)
        return url

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.changes:
            self.changes.pop(0)()

    def waiting(self):
        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch.object(mb, "_sleep", self.sleep))
        stack.enter_context(unittest.mock.patch.object(mb, "_monotonic", lambda: self.now))
        return stack

    def accepted(self) -> int:
        """Bound, the Draft PR open, accepted: the pull request's number."""
        self.open_pr_step()
        self.accept()
        return self.record()["pr"]["number"]

    def events_of(self, run_id: str) -> list[dict]:
        path = self.lc.runtime / "runs" / run_id / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def run_record(self) -> dict:
        """The one ``run`` invocation's record (the fixture's own steps are ``step``)."""
        [record] = [r for r in (json.loads(p.read_text()) for p in (self.lc.runtime / "runs").glob("*.json"))
                    if r["command"] == "run"]
        return record

    # -- the tests ------------------------------------------------------------------------

    def test_one_run_step_waits_from_pending_checks_to_the_release_and_stops(self) -> None:
        number = self.accepted()
        before = self.worker_count()
        url: dict[str, str] = {}
        self.changes = [lambda: (self.set_checks(number, "pass"), self.gh_edit(number, mergeStateStatus="UNKNOWN")),
                        lambda: self.gh_edit(number, mergeStateStatus="CLEAN"),
                        lambda: url.update(url=self.release())]
        with self.waiting():
            result = self.cli("run")
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assertEqual(result.records, [])
        self.assert_no_launch(result, before)
        self.assertEqual(self.sleeps, [30, 30, 30])
        record = self.record()
        self.assertEqual((record["state"], record["release"]),
                         (mb.CLOSED, {"state": "ALREADY_RELEASED", "version": "1.0.0", "tag": "v1.0.0",
                                      "url": url["url"]}))
        self.assertEqual(len(self.gh_calls("pr", "merge")), 1)
        run = self.run_record()
        self.assertEqual((run["command"], run["state"], run["exit_code"]), ("run", "ended", cli.EXIT_OK))
        events = self.events_of(run["run_id"])
        self.assertEqual([e["event"] for e in events],
                         ["run_started", "step_started", "waiting", "waiting", "waiting", "no_action", "run_ended"])
        self.assertEqual([e["gate"] for e in events if e["event"] == "waiting"],
                         [mb.GATE_CHECKS_PENDING, mb.GATE_MERGE_PENDING, mb.GATE_RELEASE_PENDING])
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", e["deadline"])
                            for e in events if e["event"] == "waiting"))
        self.assertEqual(events[-2]["reason"], job.REASON_CLOSED_OUT_RELEASED)
        # status and inspect show the merge and the release.
        status = self.cli("status", with_repo=False)
        a = record["accepted_head"]
        self.assertIn(f"merge: accepted at {a} (attempt 1), release: v1.0.0 {url['url']}", status.stdout)
        [entry] = json.loads(self.cli("status", json_out=True, with_repo=False).stdout)["bindings"]
        self.assertEqual(entry["merge"], record["merge"])
        self.assertEqual(entry["release"], record["release"])
        self.assertEqual(set(entry), {"work_item_id", "state", "branch", "pull_request", "worktree_root",
                                      "merge", "release"})

    def test_the_budget_expiring_records_the_last_gate(self) -> None:
        self.accepted()
        settings = self.tmp_root / "settings.json"
        settings.write_text(json.dumps({"merge": {"wait_seconds": 50}}))
        with self.waiting(), unittest.mock.patch.dict(os.environ, {"WORKFLOW_CONTROLLER_SETTINGS": str(settings)}):
            result = self.cli("run")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.sleeps, [30, 20])
        [record] = result.records
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertIn(mb.GATE_CHECKS_PENDING, json.dumps(record))
        events = [e["event"] for e in self.events_of(record["run_id"])]
        self.assertEqual(events.count("waiting"), 1)

    def test_step_and_zero_seconds_never_sleep(self) -> None:
        self.accepted()
        with self.waiting():
            result = self.cli("step")
            self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
            settings = self.tmp_root / "settings.json"
            settings.write_text(json.dumps({"merge": {"wait_seconds": 0}}))
            with unittest.mock.patch.dict(os.environ, {"WORKFLOW_CONTROLLER_SETTINGS": str(settings)}):
                ran = self.cli("run")
        self.assertEqual(ran.code, cli.EXIT_GATE, ran.stderr)
        self.assertEqual(self.sleeps, [])
        for run_id in (result.records[-1]["run_id"], ran.records[-1]["run_id"]):
            self.assertNotIn("waiting", [e["event"] for e in self.events_of(run_id)])

    def test_inspect_shows_the_merge_and_release_fields(self) -> None:
        number = self.accepted()
        self.assertEqual(self.cli("step").code, cli.EXIT_GATE)  # A pushed; its checks pending
        self.set_checks(number, "pass")
        self.cli("step")  # merged; the release is pending
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.git("switch", "-q", BRANCH)
        inspected = json.loads(self.cli("inspect", json_out=True).stdout)["milestone_branch"]
        self.assertEqual((inspected["merge"], inspected["release"]), (self.record()["merge"], None))
        self.assertIn(f"merge: accepted at {self.record()['accepted_head']} (attempt 1)", self.cli("inspect").stdout)

    def test_ctrl_c_during_the_wait_writes_no_job_record_and_the_next_step_continues(self) -> None:
        import signal

        number = self.accepted()
        state_before = self.record()["state"]
        jobs = self.lc.runtime / "jobs"
        jobs_before = set(jobs.glob("*.json")) if jobs.is_dir() else set()
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_CLI, str(fixtures.REPO_ROOT), str(self.ident.source_root),
             self.ident.source_commit, self.ident.pinned_at, self.ident.version,
             "--runtime-dir", str(self.lc.runtime), "--workflow-manager", str(self.stub_manager),
             "--claude-binary", str(lifecycle.FAKE_CLAUDE), "run", str(self.root)],
            env={**os.environ, **self.gh_env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait(10)))

        def waiting() -> bool:
            return any('"event": "waiting"' in p.read_text()
                       for p in (self.lc.runtime / "runs").glob("*/events.jsonl"))

        self.assertTrue(process_fixtures.wait_until(waiting, timeout=60), "the run never waited")
        child.send_signal(signal.SIGINT)
        _out, err = child.communicate(timeout=30)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("KeyboardInterrupt", err)
        run = self.run_record()
        self.assertEqual((run["command"], run["state"], run["exit_code"]), ("run", "interrupted", None))
        self.assertEqual(self.events_of(run["run_id"])[-1]["event"], "run_interrupted")
        self.assertEqual(set(jobs.glob("*.json")) if jobs.is_dir() else set(), jobs_before)
        self.assertEqual(self.record()["state"], state_before)
        # The next step continues from the last written binding state: it merges.
        self.set_checks(number, "pass")
        result = self.cli("step")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.assertEqual(len(self.gh_calls("pr", "merge")), 1)

if __name__ == "__main__":
    unittest.main()
