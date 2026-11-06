"""`workflow-controller-h4e` CP3: the landing of a Codex worker's commits at job end (`controller.job`, D6, D7).

Offline, through ``job.execute_step`` with the H4c1 fixture adapter, the fake Codex and a commit-making step over a real
managed repository: the real preparer makes the clone, the fixture launcher makes the worker's commits in it once the
fake run has ended, and the real lander returns them to the managed branch. Nothing real reaches the branch.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import (  # noqa: E402
    codex_tester, codex_worker_landing, codex_worker_sandbox as ws, codex_worker_tree, gitrepo, job, review,
    usage_record, worker,
)
from controller.decision import phase_from_wire  # noqa: E402
from controller.errors import GitOperationError  # noqa: E402
from tests import fixtures, process_fixtures  # noqa: E402
from tests.test_job import FAKE_IDENTITY, _build_target, _WriteSpy  # noqa: E402
from tests.test_job_codex_worker import JobFixture, per_test_job_id  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_protocol_outcome import WID, _CHECKPOINT_CODE, _Env  # noqa: E402

STATE = "docs/ai-workflow/WORKFLOW_STATE.json"


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


class LandingFixture(JobFixture):
    """A job fixture whose clone is the real preparer's and whose landing is the real lander's. ``self.after`` is a
    callable run with the clone's path once the fake Codex has ended, the worker's own work (a commit, say)."""

    def setUp(self) -> None:
        super().setUp()
        shutil.rmtree(self.worktree)  # the launch fixture's stand-in clone: the real preparer makes the job's own
        self.enterContext(mock.patch.object(job, "CODEX_WORKTREE_PREPARER", codex_worker_tree.prepare))
        self.enterContext(mock.patch.object(job, "CODEX_LANDER", codex_worker_landing.land))
        fixtures.commit_all(self.managed.root, "the state")
        # The plan bundle the real postcondition looks for is not what these tests are about.
        self.enterContext(mock.patch.object(job, "_postcondition_for_phase", return_value=None))
        self.base = git(self.managed.root, "rev-parse", "HEAD")
        self.after = None
        original = self.launcher.launch

        def launch(task, **options):
            result = original(task, **options)
            if self.after is not None:
                self.after(Path(options["cwd"]))
            return result

        self.enterContext(mock.patch.object(self.launcher, "launch", launch))

    def next_id(self) -> str:
        # per_test_job_id keeps the `t-` prefix: the id must pass `gitrepo._LANDING_JOB_ID` (it starts alphanumeric).
        self.ids += 1
        return per_test_job_id(self.id(), self.ids)

    @property
    def job_id(self) -> str:
        """The id of the job this test goes on to run (its first), computed, not read from the counter (a probe
        run may have advanced and reset it)."""
        return per_test_job_id(self.id(), 1)

    # -- the worker's work ----------------------------------------------------------------------------------------
    def commit_in(self, clone: Path, files: dict[str, str], message: str = "work") -> str:
        for name, text in files.items():
            (clone / name).parent.mkdir(parents=True, exist_ok=True)
            (clone / name).write_text(text)
        git(clone, "add", "--", *files)
        git(clone, "commit", "-q", "-m", message)
        return git(clone, "rev-parse", "HEAD")

    def state_with_phase(self, phase: str) -> str:
        state = json.loads((self.managed.root / STATE).read_text())
        state["work_items"]["wi-1"]["phase"] = phase
        return json.dumps(state, indent=2) + "\n"

    def expected_phase(self) -> str:
        """The phase the legacy transition expects, from a throwaway refused run on a scratch runtime."""
        return self.expected_to_phase()

    expected_to_phase = None  # bound below to the probe of ``OutcomeTest``

    # -- what the run left ----------------------------------------------------------------------------------------
    def branch_tip(self) -> str:
        return git(self.managed.root, "rev-parse", f"refs/heads/{self.branch()}")

    def branch(self) -> str:
        return git(self.managed.root, "symbolic-ref", "--short", "HEAD")

    def job_dir(self, job_id: str | None = None) -> Path:
        return self.runtime / "worker" / (job_id or self.job_id)

    def leftovers(self, job_id: str | None = None) -> list[str]:
        """Every file under the job directory except the retained rollouts, relative to it."""
        base = self.job_dir(job_id)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(base)) for p in base.rglob("*")
                      if "codex-home/sessions" not in str(p.relative_to(base)) and p.relative_to(base) != Path("codex-home")
                      and not p.is_dir())

    def populate_job_dir(self, job_id: str | None = None) -> None:
        """Fill the job directory as the real launcher does: every directory and file it leaves beside the tree."""
        base = self.job_dir(job_id)
        for name in ("bin", "home", "scratch", "tmp", "codex-home/sessions"):
            (base / name).mkdir(parents=True, exist_ok=True)
        for name in ("last-message.md", "codex-home/config.toml", "codex-home/installation_id",
                     "codex-home/models_cache.json", "codex-home/state_5.sqlite", "codex-home/sessions/rollout.jsonl"):
            (base / name).write_text("x\n")

    def assert_only_tree_and_sessions(self, job_id: str | None = None) -> None:
        """What a kept clone leaves (D7): ``tree`` and ``codex-home/sessions`` alone."""
        base = self.job_dir(job_id)
        self.assertEqual(sorted(p.name for p in base.iterdir()), ["codex-home", "tree"])
        self.assertEqual(sorted(p.name for p in (base / "codex-home").iterdir()), ["sessions"])

    def clone_kept(self, job_id: str | None = None) -> bool:
        return (self.job_dir(job_id) / "tree" / ".git").is_dir()

    def managed_snapshot(self) -> dict:
        root = self.managed.root
        return {"refs": git(root, "for-each-ref"), "head": git(root, "rev-parse", "HEAD"),
                "status": git(root, "status", "--porcelain", "--untracked-files=all")}

    def assert_refused(self, record: dict, cause: str) -> dict:
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["cause"]), (job.CODEX_LANDING_REFUSED_CODE, cause))
        block = record["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["cause"], block["clone_kept"]), ("refused", cause, True))
        self.assertEqual(evidence["clone"], str(ws.WorkerLayout(self.runtime, self.job_id).worktree))
        self.assertTrue(self.clone_kept())
        self.assertEqual(self.tagged(self.job_id), [])
        return block


class LandedTest(LandingFixture):
    def test_a_legacy_worker_that_commits_the_expected_transition_ends_finished_and_verified(self) -> None:
        phase = self.expected_phase()
        self.script(message="all done", subagent=500)
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)}, "transition")
        record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertTrue(record["transition_verified"])
        block = record["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["cause"], block["moved"], block["clone_kept"]),
                         ("landed", None, True, False))
        self.assertEqual(block["base"], self.base)
        self.assertEqual(block["tip"], self.branch_tip())
        self.assertEqual(len(block["commits"]), 1)
        self.assertEqual(json.loads((self.managed.root / STATE).read_text())["work_items"]["wi-1"]["phase"], phase)
        self.assertEqual(git(self.managed.root, "for-each-ref", "refs/controller"), "")
        self.assertEqual(git(self.managed.root, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.job_dir() / "tree").exists())
        validity = job.validate_record(record, managed_repo=self.managed, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid, validity)

    def test_the_usage_of_the_run_is_unchanged_by_the_landing(self) -> None:
        phase = self.expected_phase()
        self.script(message="all done", subagent=500)
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        record = self.run_step()
        rollouts = record["worker"]["stream_diagnosis"]["usage_rollouts"]
        self.assertEqual(record["usage_record"], usage_record.from_codex_rollouts(
            rollouts, model=review.DEFAULT_MODEL, wall_ms=record["worker"]["duration_ms"]).to_dict())
        self.assertEqual(record["usage_record"]["subagent_total_tokens"], 500)

    def test_the_intent_is_written_before_the_fetch_and_the_final_block_replaces_it(self) -> None:
        phase = self.expected_phase()
        seen = []
        real = gitrepo.landing_fetch

        def fetch(*args, **kwargs):
            seen.append(self.only_record()["codex_worker"]["landing"])
            return real(*args, **kwargs)

        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        with mock.patch.object(gitrepo, "landing_fetch", fetch):
            record = self.run_step()
        (intent,) = seen
        self.assertEqual((intent["outcome"], intent["base"], intent["branch"]), ("started", self.base, self.branch()))
        self.assertEqual(intent["tip"], record["codex_worker"]["landing"]["tip"])

    def test_a_worker_that_commits_nothing_keeps_the_ordinary_failure_and_the_tree_is_removed(self) -> None:
        record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "phase_not_in_to_any_of")
        block = record["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["cause"], block["moved"]), ("nothing", "nothing_to_land", False))
        self.assertEqual(self.branch_tip(), self.base)
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_an_ordinary_worker_failure_with_a_clean_range_is_landed_and_judged_by_the_reconciliation(self) -> None:
        phase = self.expected_phase()
        self.script(message="partial", exit=1)
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        record = self.run_step()
        self.assertEqual(record["worker_outcome"], worker.FAILURE)
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")
        self.assertEqual(self.branch_tip(), record["codex_worker"]["landing"]["tip"])
        self.assertNotEqual(self.branch_tip(), self.base)
        self.assertNotEqual((record.get("reconciliation_evidence") or {}).get("code"), job.CODEX_LANDING_REFUSED_CODE)
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_a_failed_removal_of_the_tree_is_recorded_as_tree_left_and_ends_the_job_as_it_would_have(self) -> None:
        phase = self.expected_phase()
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        real = job.runtime.remove_tree

        def remove(root, rel, **kw):
            if str(rel).endswith("/tree"):
                raise OSError("busy")
            return real(root, rel, **kw)

        with mock.patch.object(job.runtime, "remove_tree", remove):
            record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        block = record["codex_worker"]["landing"]
        self.assertTrue(block["clone_kept"])
        self.assertIn("busy", block["tree_left"]["reason"])
        self.assertEqual(block["tree_left"]["path"], str(ws.WorkerLayout(self.runtime, self.job_id).worktree))


class RefusedTest(LandingFixture):
    def test_a_branch_that_moved_during_the_run_refuses_and_leaves_the_repository_unchanged(self) -> None:
        phase = self.expected_phase()

        def work(clone: Path) -> None:
            self.commit_in(clone, {STATE: self.state_with_phase(phase)})
            fixtures.commit_all(self.managed.root, "moved", allow_empty=True)
            moved.append(self.managed_snapshot())

        moved: list[dict] = []
        self.after = work
        record = self.run_step()
        self.assert_refused(record, "managed_branch_moved")
        self.assertEqual(self.managed_snapshot(), moved[0])

    def test_a_dirty_managed_tree_refuses(self) -> None:
        self.after = lambda clone: (self.commit_in(clone, {"a.txt": "a\n"}),
                                    (self.managed.root / "stray.txt").write_text("x\n"))
        record = self.run_step()
        self.assert_refused(record, "managed_tree_not_clean")
        self.assertEqual(self.branch_tip(), self.base)

    def test_an_extra_ref_in_the_clone_refuses(self) -> None:
        self.after = lambda clone: (self.commit_in(clone, {"a.txt": "a\n"}), git(clone, "branch", "extra"))
        before = self.managed_snapshot()
        record = self.run_step()
        self.assert_refused(record, "extra_refs")
        self.assertEqual(self.managed_snapshot(), before)

    def test_every_cause_ends_the_job_the_same_way(self) -> None:
        for cause in codex_worker_landing.CAUSES:
            with self.subTest(cause=cause):
                moved = cause == "landing_unverified"

                def lander(managed_repo, prepared, runtime_root, job_id, *, worker_breach, on_intent, cause=cause,
                           moved=moved):
                    return codex_worker_landing.Landing("refused", cause, prepared.base, None, prepared.branch,
                                                        moved=moved, message=f"refused: {cause}")

                self.ids = 0
                self.runtime_clean()
                with mock.patch.object(job, "CODEX_LANDER", lander):
                    record = self.run_step()
                block = self.assert_refused(record, cause)
                self.assertEqual(block["moved"], moved)
                self.assertEqual(record["reconciliation_evidence"]["message"], f"refused: {cause}")

    def runtime_clean(self) -> None:
        for path in (self.runtime / "jobs", self.runtime / "worker"):
            shutil.rmtree(path, ignore_errors=True)

    def test_a_refused_block_and_failed_are_one_write(self) -> None:
        self.after = lambda clone: (self.commit_in(clone, {"a.txt": "a\n"}), git(clone, "branch", "extra"))
        with _WriteSpy() as spy:
            record = self.run_step()
        self.assert_refused(record, "extra_refs")
        for rel, obj in spy.calls:
            if "jobs/" in rel:
                landing = (obj.get("codex_worker") or {}).get("landing") or {}
                if landing.get("outcome") == "refused":
                    self.assertEqual(obj["status"], job.STATUS_FAILED, obj["status"])

    def test_a_breach_through_the_nested_repository_scan_lands_nothing(self) -> None:
        phase = self.expected_phase()
        self.script(message="done", git=[["init", "-q", "sub"]])
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        before = self.managed_snapshot()
        record = self.run_step()
        self.assertEqual(record["worker"]["subtype"], "sandbox_breach")
        self.assert_refused(record, "sandbox_breach")
        self.assertEqual(self.managed_snapshot(), before)

    def test_a_breach_through_the_codex_build_lands_nothing(self) -> None:
        phase = self.expected_phase()
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        before = self.managed_snapshot()
        with mock.patch.object(codex_tester, "_build_breach", return_value=["codex build: changed"]):
            record = self.run_step()
        self.assertEqual(record["worker"]["subtype"], "sandbox_breach")
        self.assert_refused(record, "sandbox_breach")
        self.assertEqual(self.managed_snapshot(), before)

    def test_a_lander_that_raises_is_a_landing_error_refusal(self) -> None:
        def lander(*args, **kwargs):
            raise RuntimeError("boom")

        with mock.patch.object(job, "CODEX_LANDER", lander):
            record = self.run_step()
        block = self.assert_refused(record, "landing_error")
        self.assertIn("boom", block["message"])


class DirectoryTest(LandingFixture):
    def test_a_launcher_refusal_before_the_spawn_removes_the_job_directory(self) -> None:
        self.recorded = True  # nothing recorded in the build record: the launcher refuses
        with self.assertRaises(Exception):
            self.run_step()
        self.assertEqual(self.only_record()["reconciliation_evidence"]["code"], job.WORKER_NOT_STARTED_CODE)
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_a_clone_that_fails_partway_removes_the_job_directory(self) -> None:
        self.record()
        with mock.patch.object(gitrepo, "checkout_branch_at", side_effect=GitOperationError("no checkout")), \
                self.assertRaises(codex_worker_tree.CodexTreeError):
            self.run_step()
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_an_exception_after_the_spawn_keeps_the_tree_and_names_it_in_the_record(self) -> None:
        def fake(task, **options):
            self.populate_job_dir()
            options["on_spawn"](__import__("os").getpid(), __import__("os").getpgid(0))
            raise OSError("lost")

        from controller import codex_worker as cw
        with mock.patch.object(cw, "launch_codex_worker", fake), self.assertRaises(OSError):
            self.run_step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], "OSError")
        self.assertTrue(self.clone_kept())
        self.assert_only_tree_and_sessions()
        self.assertEqual(record["codex_worker"]["clone"]["path"], str(ws.WorkerLayout(self.runtime, self.job_id).worktree))
        self.assertEqual(record["reconciliation_evidence"]["clone"], record["codex_worker"]["clone"]["path"])


def _busy_tree_removal():
    """A ``runtime.remove_tree`` whose removal of any ``.../tree`` fails, as a busy mount or a held file does."""
    real = job.runtime.remove_tree

    def remove(root, rel, **kw):
        if str(rel).endswith("/tree"):
            raise OSError("busy")
        return real(root, rel, **kw)

    return mock.patch.object(job.runtime, "remove_tree", remove)


class CloneNamedLiveTest(LandingFixture):
    """H4e leftovers, D1 sites 6 and 7: the live path names the clone its removal left, and only that one."""

    def clone_path(self) -> str:
        return str(ws.WorkerLayout(self.runtime, self.job_id).worktree)

    def test_a_finished_job_whose_tree_removal_failed_carries_a_clone_kept_block(self) -> None:
        phase = self.expected_phase()
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        with _busy_tree_removal():
            record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["clone"]), (job.CODEX_CLONE_KEPT_CODE, self.clone_path()))
        self.assertEqual(record["codex_worker"]["landing"]["tree_left"]["path"], evidence["clone"])

    def test_a_failed_job_whose_tree_removal_failed_names_the_clone_beside_its_evidence(self) -> None:
        self.after = lambda clone: self.commit_in(clone, {"unrelated.txt": "x\n"})
        with _busy_tree_removal():
            record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["clone"], self.clone_path())
        self.assertNotEqual(evidence["code"], job.CODEX_CLONE_KEPT_CODE)

    def test_status_and_explain_read_a_clone_kept_record_without_failing(self) -> None:
        """No reader treats a ``FINISHED`` record's evidence as a failure (the open point of the plan): ``status``'s
        job line and ``explain``'s last-job diagnosis go by status and stream files, never by the evidence."""
        from controller import cli, observe
        phase = self.expected_phase()
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        with _busy_tree_removal():
            record = self.run_step()
        self.assertEqual(record["reconciliation_evidence"]["code"], job.CODEX_CLONE_KEPT_CODE)
        summary = observe.job_summary(record, now=record["updated_at"])
        self.assertTrue(summary["finished"])
        self.assertIn(job.STATUS_FINISHED, observe.job_summary_text(summary))
        self.assertEqual(job._reconciled_details(record)["code"], job.CODEX_CLONE_KEPT_CODE)
        diagnosis = cli._last_job_diagnosis(self.runtime, self.managed)
        self.assertTrue(diagnosis is None or diagnosis["job_id"] == record["job_id"])

    def test_a_job_that_removed_its_tree_names_no_clone(self) -> None:
        phase = self.expected_phase()
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(phase)})
        record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertNotIn("reconciliation_evidence", record)
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_a_refused_launch_whose_removal_failed_is_worker_not_started_and_names_the_clone(self) -> None:
        self.recorded = True  # nothing recorded in the build record: the launcher refuses with a ControllerError
        with _busy_tree_removal(), self.assertRaises(Exception):
            self.run_step()
        record = self.only_record()
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["clone"]), (job.WORKER_NOT_STARTED_CODE, self.clone_path()))
        self.assertTrue(job.never_launched(record))
        self.assertTrue(self.clone_kept())

    def test_a_refused_launch_whose_removal_succeeded_names_no_clone(self) -> None:
        self.recorded = True
        with self.assertRaises(Exception):
            self.run_step()
        self.assertNotIn("clone", self.only_record()["reconciliation_evidence"])

    def test_another_exception_before_the_spawn_names_the_clone_and_keeps_its_type_as_the_code(self) -> None:
        self.record()
        with mock.patch.object(self.launcher, "launch", side_effect=RuntimeError("launcher broke")), \
                _busy_tree_removal(), self.assertRaises(RuntimeError):
            self.run_step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["clone"]), ("RuntimeError", self.clone_path()))

    def test_another_exception_before_the_spawn_with_the_tree_removed_names_no_clone(self) -> None:
        self.record()
        with mock.patch.object(self.launcher, "launch", side_effect=RuntimeError("launcher broke")), \
                self.assertRaises(RuntimeError):
            self.run_step()
        evidence = self.only_record()["reconciliation_evidence"]
        self.assertEqual(evidence["code"], "RuntimeError")
        self.assertNotIn("clone", evidence)


class ProtocolLandingTest(unittest.TestCase):
    """A protocol decision: the worker's commits land, then the Workflow's own ``reconcile`` judges the progress."""

    def setUp(self) -> None:
        self.fixture = LandingFixture("run_step")
        self.fixture.id = self.id  # before setUp: the fixture's own id is the same for every test of this class
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.env = _Env(self, "ready", implementing=True)
        self.fixture.managed = self.env.inspect()
        self.fixture.runtime = self.env.runtime
        self.fixture.record()

    def step(self) -> dict:
        fixture = self.fixture
        return job.execute_step(self.env.inspect(), identity=IDENTITY, runtime=self.env.runtime,
                                routing=fixture.options, timeout=30)

    def test_a_worker_that_commits_the_expected_progress_reconciles_to_progress(self) -> None:
        def work(clone: Path) -> None:
            fixtures.run_workflow_python(clone, _CHECKPOINT_CODE, WID, "all")

        self.fixture.after = work
        base = git(self.env.root, "rev-parse", "HEAD")
        record = self.step()
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")
        self.assertNotEqual(git(self.env.root, "rev-parse", "HEAD"), base)
        self.assertEqual(record["protocol"]["reconcile"]["class"], "progress")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertNotIn("progress", record["protocol"])

    def test_a_finished_protocol_job_whose_tree_removal_failed_carries_a_clone_kept_block(self) -> None:
        self.fixture.after = lambda clone: fixtures.run_workflow_python(clone, _CHECKPOINT_CODE, WID, "all")
        with _busy_tree_removal():
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["clone"]),
                         (job.CODEX_CLONE_KEPT_CODE, str(ws.WorkerLayout(self.env.runtime, record["job_id"]).worktree)))

    def test_a_worker_that_commits_nothing_keeps_no_progress(self) -> None:
        record = self.step()
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "nothing")
        self.assertEqual(record["protocol"]["reconcile"]["class"], "no_progress")
        self.assertEqual(record["protocol"]["progress"], "none")


def _bind_probe() -> None:
    from tests.test_job_codex_worker import OutcomeTest
    LandingFixture.expected_to_phase = OutcomeTest.expected_to_phase


_bind_probe()


if __name__ == "__main__":
    unittest.main()
