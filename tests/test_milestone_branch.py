"""``controller.milestone_branch``: milestone branch binding
(``workflow-controller-trunk-branch-pr-release-orchestration`` CP6), over real
disposable repositories -- a bare origin and a clone
(``fixtures.build_origin_pair``) with a committed, enabled policy."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import gitrepo, milestone_branch as mb, repo_policy, runtime  # noqa: E402
from controller.errors import BranchBindingError  # noqa: E402
from tests.fixtures import (  # noqa: E402
    FAKE_GH_REPOSITORY, build_origin_pair, commit_all, current_head, run, trailer_message,
)

WID = "wi-alpha"
BRANCH = f"milestone/{WID}"
STATE = "docs/ai-workflow/WORKFLOW_STATE.json"
POLICY = ".workflow-controller/policy.json"


def policy(*, enabled: bool = True, branch_format: str = "milestone/{work_item_id}") -> dict:
    return {
        "schema_version": 1,
        "trunk": {"branch": "main", "remote": "origin"},
        "forge": {"kind": "github", "repository": FAKE_GH_REPOSITORY},
        "milestone_branches": {"enabled": enabled, "branch_format": branch_format,
                               "pull_request": {"draft": True, "ready_requires_green_checks": True}},
        "release": {
            "enabled": False, "trigger": "version_change",
            "version_source": {"kind": "pyproject", "path": "pyproject.toml"},
            "version_scheme": "semver", "tag_format": "v{version}",
            "build": {"kind": "command", "argv": ["true"]},
            "verify": {"kind": "command", "argv": ["true", "{artifact}"]},
            "artifacts": {"paths": ["dist/pkg-{version}.txt"], "checksums": "SHA256SUMS"},
            "publication": {"kind": "github_release", "title": "{tag}", "notes": "pkg {tag}"},
        },
    }


class _StubForge:
    """A forge whose ``view_pr`` answers from a dict; nothing else is used."""

    def __init__(self, prs: dict[int, str]) -> None:
        self.prs, self.repository = prs, FAKE_GH_REPOSITORY

    def view_pr(self, number: int):
        return mock.Mock(number=number, state=self.prs[number])


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.origin, self.clone = build_origin_pair(self.tmp)
        self.write_policy(policy())
        self.write_state({"active_work_item_id": None,
                          "work_items": {"old": {"phase": "MILESTONE_COMPLETE", "parent_work_item_id": None}}})
        self.t = commit_all(self.clone, "Add the policy and the Workflow state")
        self.push("main")
        self.rt = self.tmp / "runtime"
        self.forge = _StubForge({})
        self.ctx = mb.Context(repo_root=self.clone, runtime_root=self.rt, forge_factory=lambda repo: self.forge,
                              clock=lambda: "2026-09-25T00:00:00Z")
        self.key = mb.repo_key(gitrepo.common_dir(self.clone))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- repository helpers ------------------------------------------------------------

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return run(["git", *args], cwd=cwd or self.clone).stdout.strip()

    def push(self, branch: str) -> None:
        self.git("push", "-q", "origin", f"refs/heads/{branch}:refs/heads/{branch}")

    def write_policy(self, data: dict) -> None:
        path = self.clone / POLICY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    def read_state(self) -> dict:
        path = self.clone / STATE
        return json.loads(path.read_text()) if path.exists() else {}

    def write_state(self, state: dict) -> None:
        path = self.clone / STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n")

    def set_item(self, wid: str = WID, **fields) -> None:
        state = self.read_state()
        state.setdefault("work_items", {}).setdefault(wid, {"parent_work_item_id": None})
        state["work_items"][wid].update(fields)
        self.write_state(state)

    def plan(self, wid: str = WID, *, phase: str = "PLANNING", base: str | None = None) -> None:
        """``/milestone-plan``: the item and a plan file, uncommitted."""
        self.set_item(wid, phase=phase, base_commit=base or current_head(self.clone), plan_approval=None,
                      parent_work_item_id=None)
        state = self.read_state()
        state["active_work_item_id"] = wid
        self.write_state(state)
        (self.clone / f"PLAN-{wid}.md").write_text(f"plan of {wid}\n")

    def approve(self, wid: str = WID) -> str:
        self.set_item(wid, phase="IMPLEMENTING", plan_approval={"status": "CURRENT"})
        return commit_all(self.clone, trailer_message(f"Approve plan for {wid}", ("Workflow-Plan-Approval", "ab" * 32),
                                                      ("Workflow-Work-Item", wid)))

    def accept(self, wid: str = WID, *, trailer: bool = True) -> str:
        self.set_item(wid, phase="MILESTONE_COMPLETE")
        state = self.read_state()
        state["active_work_item_id"] = None
        self.write_state(state)
        message = (trailer_message(f"Accept {wid}", ("Workflow-Work-Item", wid)) if trailer
                   else f"Set {wid} complete by hand")
        return commit_all(self.clone, message)

    def commit_file(self, name: str, text: str = "x\n") -> str:
        (self.clone / name).write_text(text)
        return commit_all(self.clone, f"change {name}")

    def discard_plan(self) -> None:
        self.git("checkout", "--", ".")
        self.git("clean", "-fdq")

    def head(self) -> gitrepo.HeadState:
        return gitrepo.head_state(self.clone)

    # -- binding helpers -------------------------------------------------------------------

    def preflight(self, **kwargs):
        return mb.preflight(self.ctx, **kwargs)

    def record(self, wid: str = WID) -> dict | None:
        return mb.read_record(self.rt, self.key, wid)

    def seed(self, state: str, wid: str = WID, **fields) -> dict:
        """Put the live record of ``wid`` in ``state`` directly (a fixture,
        not a transition)."""
        record = dict(self.record(wid) or self.minimal_record(wid), state=state, **fields)
        runtime.write_json(self.rt, mb.record_rel(self.key, wid), record)
        return record

    def minimal_record(self, wid: str = WID) -> dict:
        return {"schema_version": 1, "work_item_id": wid,
                "repository": {"common_dir": str(gitrepo.common_dir(self.clone).resolve()),
                               "worktree_root": str(self.clone), "remote": "origin",
                               "remote_url": str(self.origin), "forge_repository": FAKE_GH_REPOSITORY},
                "policy": {"sha256": "0", "raw": "", "snapshot": {}}, "trunk": "main",
                "branch": f"milestone/{wid}", "branch_point": self.t, "workflow_base_commit": self.t,
                "binding_generation": 1, "state": mb.BRANCH_BOUND, "pr": None, "superseded_prs": [],
                "merged_head": None, "last_observation": None}

    def events(self, wid: str = WID) -> list[dict]:
        path = self.rt / mb.events_rel(self.key, wid)
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def bind(self) -> dict:
        self.plan()
        outcome = self.preflight()
        self.assertIsInstance(outcome, mb.Proceed)
        self.assertEqual(outcome.action, "bound")
        return self.record()

    def crash_before_switch(self) -> None:
        self.plan()
        with mock.patch.object(gitrepo, "create_and_switch", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertEqual(self.record()["state"], mb.BRANCH_PLANNED)
        self.assertEqual(self.head().branch, "main")

    def crash_after_switch(self) -> None:
        self.plan()
        real = mb.write_record

        def write(root, key, record, *, now):
            if record["state"] == mb.BRANCH_BOUND:
                raise KeyboardInterrupt
            return real(root, key, record, now=now)

        with mock.patch.object(mb, "write_record", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertEqual(self.record()["state"], mb.BRANCH_PLANNED)
        self.assertEqual(self.head(), gitrepo.HeadState(BRANCH, self.t))

    def assertRefuses(self, *fragments: str, **kwargs) -> BranchBindingError:
        with self.assertRaises(BranchBindingError) as caught:
            self.preflight(**kwargs)
        for fragment in fragments:
            self.assertIn(fragment, caught.exception.message)
        return caught.exception

    def assertGate(self, outcome, code: str) -> mb.Gate:
        self.assertIsInstance(outcome, mb.Gate, outcome)
        self.assertEqual(outcome.code, code)
        return outcome

    def assertBoundOn(self, branch_point: str | None = None) -> dict:
        record = self.record()
        self.assertEqual(record["state"], mb.BRANCH_BOUND)
        self.assertEqual(self.head().branch, BRANCH)
        self.assertEqual(record["branch_point"], branch_point or self.t)
        branches = self.git("for-each-ref", "--format=%(refname:short)", "refs/heads/milestone/").split()
        self.assertEqual(branches, [BRANCH])
        return record


# ---------------------------------------------------------------------------
# The state model and the record writer.
# ---------------------------------------------------------------------------


EXPECTED_TRANSITIONS = {
    (None, "BRANCH_PLANNED"), (None, "BRANCH_BOUND"),
    ("BRANCH_PLANNED", "BRANCH_BOUND"),
    ("BRANCH_BOUND", "PR_PLANNED"), ("BRANCH_BOUND", "MERGED"), ("BRANCH_BOUND", "MERGED_BEFORE_ACCEPTANCE"),
    ("PR_PLANNED", "BRANCH_BOUND"), ("PR_PLANNED", "MERGED"), ("PR_PLANNED", "MERGED_BEFORE_ACCEPTANCE"),
    ("PR_PLANNED", "PR_OPEN"), ("PR_PLANNED", "PR_CLOSED_UNMERGED"), ("PR_PLANNED", "MERGED_REWRITTEN"),
    ("PR_OPEN", "READY"),
    ("PR_OPEN", "PR_CLOSED_UNMERGED"), ("READY", "PR_CLOSED_UNMERGED"),
    ("PR_OPEN", "MERGED"), ("PR_OPEN", "MERGED_REWRITTEN"), ("PR_OPEN", "MERGED_BEFORE_ACCEPTANCE"),
    ("READY", "MERGED"), ("READY", "MERGED_REWRITTEN"), ("READY", "MERGED_BEFORE_ACCEPTANCE"),
    ("PR_CLOSED_UNMERGED", "PR_OPEN"),
    ("PR_CLOSED_UNMERGED", "MERGED"), ("PR_CLOSED_UNMERGED", "MERGED_REWRITTEN"),
    ("PR_CLOSED_UNMERGED", "MERGED_BEFORE_ACCEPTANCE"),
    ("PR_CLOSED_UNMERGED", "BRANCH_BOUND"), ("MERGED_BEFORE_ACCEPTANCE", "BRANCH_BOUND"),
    ("PR_CLOSED_UNMERGED", "ABANDONED"), ("MERGED_BEFORE_ACCEPTANCE", "ABANDONED"),
    ("BRANCH_PLANNED", "ABANDONED"), ("BRANCH_BOUND", "ABANDONED"),
    ("MERGED", "CLOSED"),
}


class StateModelTest(_Case):
    def test_every_state_is_in_exactly_one_class(self) -> None:
        self.assertEqual(len(mb.STATES), 11)
        for state in mb.STATES:
            classes = [state in mb.NON_TERMINAL_STATES, state in mb.REFUSAL_STATES, state in mb.TERMINAL_STATES]
            self.assertEqual(classes.count(True), 1, state)
        self.assertEqual(mb.NON_TERMINAL_STATES | mb.REFUSAL_STATES | mb.TERMINAL_STATES, set(mb.STATES))

    def test_the_writer_accepts_exactly_the_table(self) -> None:
        self.assertEqual(set(mb.TRANSITIONS), EXPECTED_TRANSITIONS)
        rel = mb.record_rel(self.key, WID)
        for source in (None, *mb.STATES):
            for target in mb.STATES:
                with self.subTest(source=source, target=target):
                    path = self.rt / rel
                    path.unlink(missing_ok=True)
                    if source is not None:
                        runtime.write_json(self.rt, rel, dict(self.minimal_record(), state=source))
                    before = path.read_bytes() if path.exists() else None
                    record = dict(self.minimal_record(), state=target, merged_head="c" * 40)
                    if source == target or (source, target) in EXPECTED_TRANSITIONS:
                        mb.write_record(self.rt, self.key, record, now="t")
                        self.assertEqual(json.loads(path.read_text())["state"], target)
                    else:
                        with self.assertRaises(BranchBindingError):
                            mb.write_record(self.rt, self.key, record, now="t")
                        self.assertEqual(path.read_bytes() if path.exists() else None, before)

    def test_a_same_state_rewrite_is_accepted_including_merged_rewritten(self) -> None:
        self.seed(mb.MERGED_REWRITTEN)
        mb.write_record(self.rt, self.key, dict(self.record(), rewrite_gate_shown=True), now="t")
        self.assertTrue(self.record()["rewrite_gate_shown"])

    def test_a_malformed_record_refuses(self) -> None:
        path = self.rt / mb.record_rel(self.key, WID)
        path.parent.mkdir(parents=True)
        path.write_text('{"schema_version": 1, "state": "NOPE"}')
        with self.assertRaises(BranchBindingError):
            mb.live_records(self.rt, self.key)

    def test_an_interrupted_rename_aside_is_completed_not_repeated(self) -> None:
        self.seed(mb.ABANDONED)
        live = self.rt / mb.record_rel(self.key, WID)
        (live.parent / f"{WID}.abandoned-1.json").write_bytes(live.read_bytes())
        mb._retire_abandoned(self.rt, self.key, WID)
        self.assertFalse(live.exists())
        self.assertEqual([n for n, _ in mb.abandoned_records(self.rt, self.key, WID)], [1])


# ---------------------------------------------------------------------------
# No activation, no change.
# ---------------------------------------------------------------------------


class InactiveTest(_Case):
    def test_no_policy_and_no_record_proceeds_and_writes_nothing(self) -> None:
        (self.clone / POLICY).unlink()
        commit_all(self.clone, "Remove the policy")
        self.plan()
        self.assertEqual(self.preflight(), mb.Proceed())
        self.assertFalse(self.rt.exists())

    def test_a_disabled_policy_proceeds(self) -> None:
        self.write_policy(policy(enabled=False))
        commit_all(self.clone, "Disable")
        self.plan()
        self.assertEqual(self.preflight(), mb.Proceed())
        self.assertEqual(self.head().branch, "main")


# ---------------------------------------------------------------------------
# The bind step and the trunk-start preflight.
# ---------------------------------------------------------------------------


class BindTest(_Case):
    def test_fresh_bind_carries_the_uncommitted_plan(self) -> None:
        record = self.bind()
        self.assertBoundOn()
        self.assertTrue((self.clone / f"PLAN-{WID}.md").exists())
        self.assertIn(WID, self.read_state()["work_items"])
        self.assertEqual(record["binding_generation"], 1)
        self.assertEqual(record["workflow_base_commit"], self.t)
        self.assertEqual(mb.binding_policy(record).trunk_branch, "main")
        self.assertEqual([e["event"] for e in self.events()], ["bind_planned", "bound"])
        # the next step on the branch observes it
        outcome = self.preflight()
        self.assertEqual((outcome.action, outcome.work_item_override), ("observed", None))
        self.assertEqual(self.record()["last_observation"]["tip"], self.t)

    def test_only_the_plan_stage_phases_bind(self) -> None:
        self.assertEqual(mb.PLAN_STAGE_PHASES, {
            "PLANNING", "SELF_REVIEWING_PLAN", "AWAITING_EXTERNAL_PLAN_REVIEW", "AWAITING_LOCAL_PLAN_REVIEW",
            "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_PLAN_APPROVAL"})
        for phase in ("AMENDING_PLAN", "IMPLEMENTING"):
            with self.subTest(phase=phase):
                self.plan(phase=phase)
                self.assertRefuses("not a plan-stage phase")
                self.assertIsNone(self.record())
        self.plan(phase="AWAITING_PLAN_APPROVAL")
        self.preflight()
        self.assertBoundOn()

    def test_a_plan_approved_on_trunk_refuses_with_manual_recovery(self) -> None:
        self.plan()
        self.approve()
        self.set_item(phase="PLANNING")
        self.assertRefuses("already approved", "recover by hand")

    def test_a_base_commit_not_on_trunk_refuses(self) -> None:
        self.plan(base="f" * 40)
        self.assertRefuses("base_commit")

    def test_two_unbound_top_level_items_refuse_naming_both(self) -> None:
        self.plan("wi-a")
        self.plan("wi-b")
        self.assertRefuses("wi-a", "wi-b")

    def test_a_remediation_child_is_never_a_bind_candidate(self) -> None:
        self.set_item("wi-child", phase="PLANNING", parent_work_item_id="wi-parent", base_commit=self.t)
        commit_all(self.clone, "child")
        self.push("main")
        self.assertEqual(self.preflight().action, "trunk_start")
        self.assertEqual(self.head().branch, "main")

    def test_a_local_or_remote_branch_collision_refuses(self) -> None:
        self.git("branch", BRANCH)
        self.plan()
        self.assertRefuses("collision")
        self.git("branch", "-D", BRANCH)
        self.git("push", "-q", "origin", f"{self.t}:refs/heads/{BRANCH}")
        self.assertRefuses("another clone may own")
        self.assertIsNone(self.record())


class TrunkStartTest(_Case):
    def test_clean_trunk_equal_to_the_remote_proceeds(self) -> None:
        self.assertEqual(self.preflight(), mb.Proceed(action="trunk_start"))

    def test_behind_gates_and_ahead_or_diverged_refuse(self) -> None:
        other = self.tmp / "other"
        run(["git", "clone", "-q", str(self.origin), str(other)])
        run(["git", "-c", "user.email=o@example.invalid", "-c", "user.name=O", "commit", "-q", "--allow-empty",
             "-m", "remote work"], cwd=other)
        run(["git", "push", "-q", "origin", "main"], cwd=other)
        self.assertGate(self.preflight(), mb.GATE_FAST_FORWARD_TRUNK)
        self.commit_file("local.txt")
        self.assertRefuses("diverged")
        self.git("merge", "-q", "--no-edit", "origin/main")
        self.assertRefuses("not on origin/main")

    def test_a_dirty_tracked_tree_refuses(self) -> None:
        (self.clone / "README.md").write_text("edited\n")
        self.assertRefuses("tracked tree")

    def test_detached_or_unrelated_head_refuses(self) -> None:
        self.git("switch", "-q", "-c", "feature")
        self.assertRefuses("neither the trunk")
        self.git("switch", "-q", "--detach")
        self.assertRefuses("detached")


# ---------------------------------------------------------------------------
# Crash recovery and the BRANCH_PLANNED rows.
# ---------------------------------------------------------------------------


class CrashTest(_Case):
    def test_crash_before_switch_then_restart_rebinds(self) -> None:
        self.crash_before_switch()
        self.preflight()
        self.assertBoundOn()

    def test_crash_after_switch_then_restart_completes_on_the_branch(self) -> None:
        self.crash_after_switch()
        self.assertEqual(self.preflight().action, "completed")
        self.assertBoundOn()

    def test_crash_after_switch_then_back_on_trunk_switches_at_the_same_commit(self) -> None:
        self.crash_after_switch()
        self.git("switch", "-q", "main")
        self.preflight()
        record = self.assertBoundOn()
        self.assertTrue((self.clone / f"PLAN-{WID}.md").exists())
        self.assertEqual(record["state"], mb.BRANCH_BOUND)

    def test_trunk_moved_past_t_rebinds_at_the_new_tip(self) -> None:
        self.crash_before_switch()
        (self.clone / "later.txt").write_text("later\n")
        run(["git", "add", "later.txt"], cwd=self.clone)
        run(["git", "commit", "-q", "-m", "later"], cwd=self.clone)
        later = current_head(self.clone)
        self.preflight()
        self.assertBoundOn(branch_point=later)
        self.assertIn("rebind", [e["event"] for e in self.events()])

    def test_a_policy_changed_on_trunk_is_reread_not_refused(self) -> None:
        self.crash_before_switch()
        recorded = self.record()["policy"]["sha256"]
        self.write_policy(policy(branch_format="milestone/{work_item_id}"))
        (self.clone / POLICY).write_text((self.clone / POLICY).read_text() + "\n")
        run(["git", "add", POLICY], cwd=self.clone)
        run(["git", "commit", "-q", "-m", "reformat the policy"], cwd=self.clone)
        self.preflight()
        record = self.assertBoundOn(branch_point=current_head(self.clone))
        self.assertNotEqual(record["policy"]["sha256"], recorded)

    def test_a_forge_repository_mismatch_refuses_naming_both(self) -> None:
        self.crash_before_switch()
        self.git("remote", "set-url", "origin", "https://github.com/someone/else.git")
        with self.assertRaises(BranchBindingError) as caught:
            mb.preflight(self.ctx)
        self.assertIn(FAKE_GH_REPOSITORY, caught.exception.message)
        self.assertIn("someone/else", caught.exception.message)
        self.assertIn("restore origin's URL", caught.exception.message)


class PlannedExitsTest(_Case):
    def test_exit_a_commit_on_the_branch_then_trunk(self) -> None:
        self.crash_after_switch()
        tip = self.commit_file("work.txt")
        self.git("switch", "-q", "main")
        error = self.assertRefuses(BRANCH, "switch to it")
        self.assertNotIn("--abandon", error.message)
        self.git("switch", "-q", BRANCH)
        self.assertEqual(self.preflight().action, "completed")
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.assertEqual(self.head().commit, tip)

    def test_exit_b_a_branch_not_descending_from_t(self) -> None:
        self.crash_before_switch()
        root = self.git("rev-list", "--max-parents=0", "HEAD")
        self.git("branch", BRANCH, root)
        self.assertRefuses("does not descend", "remove or rename")
        self.git("stash", "-q", "-u")
        self.git("switch", "-q", BRANCH)
        self.assertRefuses("does not descend", "remove or rename")
        self.git("switch", "-q", "main")
        self.git("stash", "pop", "-q")
        self.git("push", "-q", "origin", f"{BRANCH}:{BRANCH}")
        self.git("branch", "-D", BRANCH)
        self.assertRefuses("remote", "does not descend")
        self.git("push", "-q", "origin", f":refs/heads/{BRANCH}")
        self.preflight()
        self.assertBoundOn()

    def test_exit_c_approved_on_trunk_after_the_crash(self) -> None:
        self.crash_before_switch()
        approval = self.approve()
        error = self.assertRefuses("cannot be completed", f"git switch -c {BRANCH} {approval}")
        self.assertNotIn("--abandon", error.message)
        self.git("switch", "-q", "-c", BRANCH)
        self.assertEqual(self.preflight().action, "completed")
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)

    def test_exit_c_trunk_rewound_below_base_commit(self) -> None:
        base = self.commit_file("base.txt")
        self.push("main")
        self.plan(base=base)
        with mock.patch.object(gitrepo, "create_and_switch", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.git("reset", "-q", "--keep", "HEAD~1")
        self.assertRefuses("base_commit", f"git switch -c {BRANCH} {base}")
        self.git("switch", "-q", "-c", BRANCH, base)
        self.preflight()
        self.assertBoundOn(branch_point=base)

    def test_exit_c_plan_discarded_abandons_and_trunk_starts(self) -> None:
        self.crash_before_switch()
        with self.assertRaises(BranchBindingError) as caught:
            mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertIn("git switch -c", caught.exception.message)
        self.assertEqual(self.record()["state"], mb.BRANCH_PLANNED)
        self.discard_plan()
        error = self.assertRefuses("--abandon")
        self.assertNotIn("git switch -c", error.message)
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertEqual(self.record()["state"], mb.ABANDONED)
        self.assertEqual(self.preflight().action, "trunk_start")

    def test_exit_c_plan_discarded_only_in_the_working_tree_cannot_abandon(self) -> None:
        self.crash_before_switch()
        self.approve()
        self.set_item(phase="MILESTONE_COMPLETE")
        state = self.read_state()
        del state["work_items"][WID]
        self.write_state(state)
        with self.assertRaises(BranchBindingError) as caught:
            mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertIn("HEAD's committed", caught.exception.message)
        self.assertEqual(self.record()["state"], mb.BRANCH_PLANNED)


# ---------------------------------------------------------------------------
# Every later preflight on the branch.
# ---------------------------------------------------------------------------


class LaterPreflightTest(_Case):
    def test_a_rewritten_branch_refuses(self) -> None:
        self.bind()
        self.approve()
        self.commit_file("one.txt")
        self.preflight()
        self.git("reset", "-q", "--keep", "HEAD~1")
        self.assertRefuses("rewritten")

    def test_sync_fast_forwards_an_existing_remote_branch(self) -> None:
        self.bind()
        self.commit_file("one.txt")
        self.push(BRANCH)
        tip = self.commit_file("two.txt")
        self.preflight()
        self.assertEqual(self.git("ls-remote", "origin", f"refs/heads/{BRANCH}").split()[0], tip)
        self.assertEqual(self.record()["last_observation"]["push"], {"intent": tip, "outcome": tip})
        self.assertIn("push_intent", [e["event"] for e in self.events()])

    def test_no_push_while_the_remote_branch_is_absent(self) -> None:
        self.bind()
        self.commit_file("one.txt")
        self.preflight()
        self.assertEqual(self.git("ls-remote", "origin", f"refs/heads/{BRANCH}"), "")

    def test_a_remote_ahead_and_diverged_branch_refuses(self) -> None:
        self.bind()
        self.commit_file("one.txt")
        self.push(BRANCH)
        other = self.tmp / "other"
        run(["git", "clone", "-q", "--branch", BRANCH, str(self.origin), str(other)])
        run(["git", "-c", "user.email=o@example.invalid", "-c", "user.name=O", "commit", "-q", "--allow-empty",
             "-m", "Merge branch main (Update branch)"], cwd=other)
        run(["git", "push", "-q", "origin", BRANCH], cwd=other)
        self.commit_file("two.txt")
        self.assertRefuses("Update branch")

    def test_a_policy_edited_on_the_branch_does_not_change_the_binding(self) -> None:
        record = self.bind()
        self.write_policy(policy(enabled=False, branch_format="feature/{work_item_id}"))
        commit_all(self.clone, "Edit the policy on the branch")
        outcome = self.preflight()
        self.assertIsInstance(outcome, mb.Proceed)
        self.assertEqual(self.record()["policy"], record["policy"])

    def test_an_unrelated_explicit_work_item_refuses_but_a_child_is_admitted(self) -> None:
        self.bind()
        self.set_item("wi-other", phase="PLANNING", parent_work_item_id=None)
        self.set_item("wi-child", phase="PLANNING", parent_work_item_id=WID)
        self.assertRefuses("wi-other", WID, requested_work_item_id="wi-other")
        outcome = self.preflight(requested_work_item_id="wi-child")
        self.assertIsInstance(outcome, mb.Proceed)
        self.assertEqual(list(mb.live_records(self.rt, self.key)), [WID])
        self.assertEqual(self.head().branch, BRANCH)

    def test_after_acceptance_the_bound_item_overrides_the_selection(self) -> None:
        self.bind()
        self.approve()
        self.accept()
        self.assertEqual(self.preflight().work_item_override, WID)

    def test_terminal_bindings_gate_switch_to_trunk(self) -> None:
        self.bind()
        for state in (mb.CLOSED, mb.MERGED_REWRITTEN, mb.ABANDONED):
            with self.subTest(state=state):
                self.seed(state)
                gate = self.assertGate(self.preflight(), mb.GATE_SWITCH_TO_TRUNK)
                self.assertIn("git switch main", gate.exits)


# ---------------------------------------------------------------------------
# Trunk-side blocking.
# ---------------------------------------------------------------------------


class TrunkBlockTest(_Case):
    def bound_past_the_plan_stage(self) -> None:
        self.bind()
        self.approve()
        self.preflight()
        self.git("switch", "-q", "main")
        self.assertNotIn(WID, self.read_state()["work_items"])

    def test_a_bound_record_blocks_the_trunk_naming_its_branch(self) -> None:
        self.bound_past_the_plan_stage()
        for state in (mb.BRANCH_BOUND, mb.PR_OPEN, mb.READY):
            with self.subTest(state=state):
                self.seed(state, pr=None if state == mb.BRANCH_BOUND else {"number": 7})
                error = self.assertRefuses(BRANCH, f"git switch {BRANCH}")
                self.assertNotIn("--abandon", error.message)
                self.assertEqual(self.head().branch, "main")

    def test_a_missing_branch_names_the_last_observed_tip(self) -> None:
        self.bound_past_the_plan_stage()
        tip = self.record()["last_observation"]["tip"]
        self.git("branch", "-D", BRANCH)
        self.assertRefuses(f"git branch {BRANCH} {tip}")

    def test_refusal_states_block_with_their_exits_and_terminal_ones_do_not(self) -> None:
        self.bound_past_the_plan_stage()
        for state in (mb.CLOSED, mb.ABANDONED, mb.MERGED_REWRITTEN):
            with self.subTest(state=state):
                self.seed(state)
                self.assertEqual(self.preflight().action, "trunk_start")
        self.seed(mb.PR_CLOSED_UNMERGED, pr={"number": 7})
        self.assertRefuses(BRANCH, "reopen pull request #7", "--new-pr", "--abandon")
        self.git("push", "-q", "origin", f"{BRANCH}:main")  # a merge before acceptance
        self.seed(mb.MERGED_BEFORE_ACCEPTANCE, pr=None)
        error = self.assertRefuses(BRANCH, "--new-pr")
        self.assertNotIn("--abandon", error.message)  # the merge carried the item's state to trunk


# ---------------------------------------------------------------------------
# A plan discarded after a completed bind (TBR-R8-001), and --abandon.
# ---------------------------------------------------------------------------


class DiscardedPlanTest(_Case):
    def test_from_the_branch_the_step_gates_bound_item_missing(self) -> None:
        self.bind()
        self.discard_plan()
        gate = self.assertGate(self.preflight(), mb.GATE_BOUND_ITEM_MISSING)
        self.assertIn(WID, gate.message)
        self.assertIn(BRANCH, gate.message)
        self.assertEqual(len(gate.exits), 2)
        self.assertIn("--abandon", gate.exits[1])

    def test_from_trunk_discarded_and_stashed_give_the_same_refusal(self) -> None:
        self.bind()
        self.discard_plan()
        self.git("switch", "-q", "main")
        discarded = self.assertRefuses("--abandon", "git stash pop").message
        self.git("switch", "-q", BRANCH)
        (self.clone / f"PLAN-{WID}.md").write_text("plan\n")
        self.set_item(phase="PLANNING", base_commit=self.t, plan_approval=None)
        self.git("stash", "-q", "-u")
        self.git("switch", "-q", "main")
        self.assertEqual(self.assertRefuses().message, discarded)
        self.git("switch", "-q", BRANCH)
        self.git("stash", "pop", "-q")
        self.assertIsInstance(self.preflight(), mb.Proceed)

    def test_abandon_from_the_branch_then_switch_to_trunk(self) -> None:
        self.bind()
        self.discard_plan()
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertEqual(self.record()["state"], mb.ABANDONED)
        self.assertEqual(self.events()[-1]["event"], "acknowledged")
        self.assertGate(self.preflight(), mb.GATE_SWITCH_TO_TRUNK)
        self.git("switch", "-q", "main")
        self.assertEqual(self.preflight().action, "trunk_start")

    def test_abandon_from_trunk_then_the_trunk_starts(self) -> None:
        self.bind()
        self.discard_plan()
        self.git("switch", "-q", "main")
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertEqual(self.preflight().action, "trunk_start")

    def test_abandon_refuses_outside_the_plan_stage_window(self) -> None:
        cases = {
            "tip past branch_point": lambda: self.commit_file("one.txt"),
            "remote branch": lambda: self.push(BRANCH),
            "working tree entry": lambda: self.set_item(phase="PLANNING"),
            "superseded PRs": lambda: self.seed(mb.BRANCH_BOUND, superseded_prs=[3]),
            "observed tip": lambda: self.seed(mb.BRANCH_BOUND, last_observation={"tip": "c" * 40}),
        }
        for name, spoil in cases.items():
            with self.subTest(name=name):
                self.tearDown()
                self.setUp()
                self.bind()
                if name != "working tree entry":
                    self.discard_plan()
                spoil()
                if name == "tip past branch_point":
                    self.discard_plan()
                before = self.record()
                with self.assertRaises(BranchBindingError) as caught:
                    mb.acknowledge(self.ctx, WID, mb.ABANDON)
                self.assertIn("restore the plan", caught.exception.message)
                self.assertEqual(self.record(), before)

    def test_replanning_an_abandoned_id_needs_the_leftover_branch_gone(self) -> None:
        self.bind()
        self.discard_plan()
        self.git("switch", "-q", "main")
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.plan()
        self.assertRefuses(f"git branch -d {BRANCH}")
        self.assertEqual(self.record()["state"], mb.ABANDONED)
        self.git("branch", "-D", BRANCH)
        self.preflight()
        record = self.assertBoundOn()
        self.assertEqual(record["binding_generation"], 2)
        self.assertEqual([n for n, _ in mb.abandoned_records(self.rt, self.key, WID)], [1])
        self.assertEqual({e["binding_generation"] for e in self.events()}, {1, 2})

    def test_a_crash_between_the_rename_and_the_create_converges(self) -> None:
        self.bind()
        self.discard_plan()
        self.git("switch", "-q", "main")
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.git("branch", "-D", BRANCH)
        self.plan()
        with mock.patch.object(runtime, "create_json", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertIsNone(self.record())
        self.preflight()
        self.assertEqual(self.assertBoundOn()["binding_generation"], 2)

    def test_an_abandoned_record_on_its_leftover_branch_never_adopts(self) -> None:
        self.bind()
        self.discard_plan()
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.plan()  # even with the item back in the working tree
        self.assertGate(self.preflight(), mb.GATE_SWITCH_TO_TRUNK)
        self.assertEqual(self.record()["state"], mb.ABANDONED)

    def test_a_planned_record_on_its_branch_completes_then_gates(self) -> None:
        self.crash_after_switch()
        self.discard_plan()
        gate = self.assertGate(self.preflight(), mb.GATE_BOUND_ITEM_MISSING)
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.assertIn("--abandon", gate.exits[-1])


# ---------------------------------------------------------------------------
# Adopt (rule 2).
# ---------------------------------------------------------------------------


class AdoptTest(_Case):
    def lose_runtime_root(self) -> None:
        shutil.rmtree(self.rt)

    def test_a_hand_made_branch_adopts_the_branch_point_policy(self) -> None:
        main_policy = mb._policy_snapshot(repo_policy.read_committed_policy(self.clone))
        self.plan()
        self.git("switch", "-q", "-c", BRANCH)
        self.approve()
        self.write_policy(policy(branch_format="feature/{work_item_id}"))
        commit_all(self.clone, "Edit the policy on the branch")
        outcome = self.preflight()
        self.assertEqual(outcome.action, "adopted")
        record = self.record()
        self.assertEqual((record["state"], record["branch_point"]), (mb.BRANCH_BOUND, self.t))
        self.assertEqual(record["policy"], main_policy)

    def test_adopt_mid_implementation_after_a_lost_runtime_root(self) -> None:
        self.bind()
        self.approve()
        self.commit_file("impl.txt")
        self.lose_runtime_root()
        self.assertEqual(self.preflight().action, "adopted")
        self.assertEqual(self.record()["branch_point"], self.t)

    def test_adopt_refuses_an_approval_on_trunk_or_a_base_off_the_branch_point(self) -> None:
        self.plan()
        self.approve()
        self.push("main")
        self.git("switch", "-q", "-c", BRANCH)
        self.assertRefuses("approved on the trunk")
        self.assertIsNone(self.record())
        self.set_item(base_commit=self.commit_file("x.txt"))
        self.assertRefuses("base_commit")

    def test_adopt_after_acceptance_needs_the_acceptance_commit_on_the_branch(self) -> None:
        self.bind()
        self.approve()
        self.accept()
        self.lose_runtime_root()
        outcome = self.preflight()
        self.assertEqual((outcome.action, outcome.work_item_override), ("adopted", WID))

    def test_adopt_after_acceptance_refuses_an_untrailered_or_trunk_acceptance(self) -> None:
        self.bind()
        self.approve()
        self.accept(trailer=False)
        self.lose_runtime_root()
        self.assertRefuses("acceptance commit")
        # the same with the acceptance on trunk: P is then past it
        self.git("switch", "-q", "main")
        self.git("merge", "-q", "--ff-only", BRANCH)
        self.push("main")
        self.git("switch", "-q", BRANCH)
        self.assertRefuses("acceptance commit")

    def test_a_remediation_child_rides_on_its_parent_binding(self) -> None:
        self.bind()
        self.approve()
        self.set_item("wi-child", phase="PLANNING", parent_work_item_id=WID, base_commit=current_head(self.clone))
        state = self.read_state()
        state["active_work_item_id"] = "wi-child"
        self.write_state(state)
        outcome = self.preflight()
        self.assertIsInstance(outcome, mb.Proceed)
        self.assertEqual(list(mb.live_records(self.rt, self.key)), [WID])
        self.assertEqual(self.git("for-each-ref", "--format=%(refname:short)", "refs/heads/milestone/"), BRANCH)


# ---------------------------------------------------------------------------
# The acknowledgement for refusal states.
# ---------------------------------------------------------------------------


class AcknowledgeTest(_Case):
    def setUp(self) -> None:
        super().setUp()
        self.bind()
        self.approve()
        self.preflight()

    def test_new_pr_supersedes_a_closed_pull_request(self) -> None:
        self.seed(mb.PR_CLOSED_UNMERGED, pr={"number": 7})
        self.forge.prs[7] = "CLOSED"
        record = mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertEqual((record["state"], record["pr"], record["superseded_prs"]), (mb.BRANCH_BOUND, None, [7]))
        event = self.events()[-1]
        self.assertEqual((event["event"], event["disposition"], event["previous_state"], event["pr"]),
                         ("acknowledged", mb.NEW_PR, mb.PR_CLOSED_UNMERGED, 7))

    def test_a_reopened_pull_request_refuses_and_writes_nothing(self) -> None:
        before = self.seed(mb.PR_CLOSED_UNMERGED, pr={"number": 7})
        self.forge.prs[7] = "OPEN"
        for disposition in mb.DISPOSITIONS:
            with self.assertRaises(BranchBindingError):
                mb.acknowledge(self.ctx, WID, disposition)
        self.assertEqual(self.record()["state"], before["state"])

    def test_a_non_refusal_state_refuses(self) -> None:
        self.seed(mb.PR_OPEN, pr={"number": 7})
        for disposition in mb.DISPOSITIONS:
            with self.assertRaises(BranchBindingError):
                mb.acknowledge(self.ctx, WID, disposition)
        with self.assertRaises(BranchBindingError):
            mb.acknowledge(self.ctx, "wi-none", mb.ABANDON)

    def test_abandon_of_a_refusal_state_needs_the_item_off_remote_trunk(self) -> None:
        self.seed(mb.PR_CLOSED_UNMERGED, pr={"number": 7})
        self.forge.prs[7] = "CLOSED"
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertEqual(self.record()["state"], mb.ABANDONED)

    def test_merged_before_acceptance_gates_and_new_pr_needs_an_acceptance_path(self) -> None:
        self.push(BRANCH)
        self.git("push", "-q", "origin", f"{BRANCH}:main")  # merged before acceptance
        self.seed(mb.MERGED_BEFORE_ACCEPTANCE, pr=None)
        gate = self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        self.assertEqual(len(gate.exits), 1)
        self.assertIn("--new-pr", gate.exits[0])
        with self.assertRaises(BranchBindingError):
            mb.acknowledge(self.ctx, WID, mb.ABANDON)  # the item's non-terminal state is on trunk
        # a phase set by hand and merged: --new-pr would loop, so --abandon alone
        self.accept(trailer=False)
        self.push(BRANCH)
        self.git("push", "-q", "origin", f"{BRANCH}:main")
        gate = self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        self.assertEqual(len(gate.exits), 1)
        self.assertIn("--abandon", gate.exits[0])
        with self.assertRaises(BranchBindingError):
            mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertEqual(self.record()["state"], mb.MERGED_BEFORE_ACCEPTANCE)

    def test_a_branch_checked_out_in_another_worktree_refuses(self) -> None:
        self.seed(mb.PR_CLOSED_UNMERGED, pr={"number": 7})
        self.forge.prs[7] = "CLOSED"
        self.git("switch", "-q", "main")
        self.git("worktree", "add", "-q", str(self.tmp / "second"), BRANCH)
        with self.assertRaises(BranchBindingError) as caught:
            mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertIn("second", caught.exception.message)


class AcceptanceCommitTest(_Case):
    def test_a_child_acceptance_is_not_the_parents(self) -> None:
        self.bind()
        self.approve()
        self.set_item("wi-child", phase="MILESTONE_COMPLETE", parent_work_item_id=WID)
        commit_all(self.clone, trailer_message("Accept child", ("Workflow-Work-Item", "wi-child")))
        a = self.accept()
        self.assertEqual(mb.find_acceptance_commit(self.ctx, WID, self.t, a), mb.AcceptanceSearch(a))

    def test_an_untrailered_completion_is_named(self) -> None:
        self.bind()
        c = self.accept(trailer=False)
        self.assertEqual(mb.find_acceptance_commit(self.ctx, WID, self.t, c),
                         mb.AcceptanceSearch(None, untrailered=c))


if __name__ == "__main__":
    unittest.main()
