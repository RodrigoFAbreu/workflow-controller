"""Contract tests of the scenario kit (`workflow-controller-automated-
lifecycle-scenarios` CP1): the sandbox enforces what it claims, with safe
stand-ins, through every descendant path; isolation that is unavailable
fails closed; the checkout snapshot is content-sensitive; the kill-points
sit at the primitives the inventory measured; observations are normalised.

A scenario atom needs unprivileged user namespaces. Locally an unavailable
sandbox skips these tests naming the failed step; with ``CI`` set it fails
them (``kit.isolation_gate``).
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures, process_fixtures  # noqa: E402
from tests.scenarios import FLOW_ID_RE, REGISTRY, ScenarioFailure, scenario  # noqa: E402
from tests.scenarios import kit  # noqa: E402

SELFTEST = "tests.scenarios.selftest"
ROOT = fixtures.REPO_ROOT


def _gate(case: unittest.TestCase) -> None:
    action, reason = kit.isolation_gate(kit.probe_isolation(), os.environ)
    if action == "skip":
        case.skipTest(reason)
    if action == "fail":
        case.fail(reason)


class SandboxCase(unittest.TestCase):
    """A test that launches a scenario through the real two-phase sandbox."""

    @classmethod
    def setUpClass(cls) -> None:
        action, reason = kit.isolation_gate(kit.probe_isolation(), os.environ)
        if action == "skip":
            raise unittest.SkipTest(reason)
        if action == "fail":
            raise AssertionError(reason)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scenario-kit-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def standin(self, name: str = "real") -> Path:
        """A stand-in 'real' directory the sandbox must keep unwritable."""
        path = self.tmp / name
        path.mkdir()
        return path

    def run_selftest(self, flow_id: str, *, params: dict | None = None, protect: list | None = None,
                     mode: str = "namespace") -> kit.Outcome:
        outcome = kit.run_scenario(flow_id, module=SELFTEST, params=params or {}, extra_protect=protect or [],
                                   mode=mode)
        self.assertEqual(outcome.problems, [], outcome.result)
        return outcome

    @staticmethod
    def observed(outcome: kit.Outcome) -> dict:
        return {o["name"]: o["value"] for o in outcome.result["observations"]}


class SandboxEnforcementTest(SandboxCase):
    """D7: the enforcement is the kernel's, inherited by every descendant."""

    def test_every_descendant_path_is_cut_off_from_the_network_and_the_stand_in_locations(self) -> None:
        real = self.standin()
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(0)
        params = {"standin": str(real), "port": listener.getsockname()[1], "exe": "realtool-for-the-test"}
        # A stand-in "real" executable on the invoker's PATH, which the built PATH must not find by name.
        bin_dir = self.tmp / "invokerbin"
        bin_dir.mkdir()
        shutil.copy("/bin/true", bin_dir / params["exe"])
        with unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}):
            outcome = self.run_selftest("selftest-attacks", params=params, protect=[real])
        self.assertEqual(outcome.result["status"], "passed", outcome.result)
        seen = self.observed(outcome)
        self.assertEqual(sorted(seen), ["attack-git-hook", "attack-python-E", "attack-reexec", "attack-shell"])
        for path, attack in seen.items():
            with self.subTest(path=path):
                self.assertEqual(attack, {"listener": "ECONNREFUSED", "external": "ENETUNREACH", "write": "EROFS",
                                          "exe_by_name": None})
        with self.assertRaises(BlockingIOError):  # the host listener accepted none
            listener.accept()
        self.assertEqual(list(real.iterdir()), [])

    def test_the_scenario_process_holds_no_capability_and_cannot_undo_a_protected_mount(self) -> None:
        real = self.standin()
        outcome = self.run_selftest("selftest-capabilities", params={"protected": str(real)}, protect=[real])
        self.assertEqual(self.observed(outcome)["caps"], {
            "cap_eff": 0, "uid": os.getuid(), "umount": "EPERM", "remount": "EPERM", "write": "EROFS"})
        self.assertEqual(list(real.iterdir()), [])

    def test_a_loopback_fake_is_reachable_and_any_other_address_is_not(self) -> None:
        seen = self.observed(self.run_selftest("selftest-loopback"))
        self.assertEqual(seen, {"loopback": "answered", "other-address": "ENETUNREACH", "hub-requests": 1})

    def test_the_environment_is_built_from_an_allow_list_and_git_overrides_do_not_reach_it(self) -> None:
        hostile = {"HTTP_PROXY": "http://proxy.invalid:3128", "HTTPS_PROXY": "http://proxy.invalid:3128",
                   "SIGNALHUB_URL": "http://hub.invalid", "SIGNALHUB_API_KEY": "secret", "ANTHROPIC_API_KEY": "secret",
                   "GIT_DIR": "/nonexistent", "GIT_WORK_TREE": "/nonexistent", "GIT_INDEX_FILE": "/nonexistent/index",
                   "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/evil",
                   "GIT_EXEC_PATH": "/nonexistent"}
        with unittest.mock.patch.dict(os.environ, hostile):
            outcome = self.run_selftest("selftest-env")
        seen = self.observed(outcome)
        keys = set(seen["env-keys"])
        self.assertFalse(keys & set(hostile) - {"GIT_CONFIG_COUNT"}, keys)
        self.assertNotIn("GIT_CONFIG_COUNT", keys)
        self.assertEqual(keys, set(kit.build_env(Path("/x"))))
        self.assertEqual(seen["path"].split("/")[-1], "bin")
        self.assertTrue(seen["head-lookup"])
        self.assertTrue(seen["hooks-path"].endswith("empty-hooks"), seen["hooks-path"])
        self.assertEqual(seen["which-claude"], ["<scratch>/bin/claude", "<scratch>/bin/gh", "<scratch>/bin/workflow-manager"],
                         "claude, gh and workflow-manager resolve only to the fakes; curl is not found")
        self.assertEqual(seen["bin"], sorted(seen["bin"]))
        self.assertTrue({"claude", "gh", "workflow-manager", "git", "sh", "python3"} <= set(seen["bin"]))
        self.assertNotIn("curl", seen["bin"])

    def test_the_audit_hook_refuses_an_executable_outside_the_scratch_bin(self) -> None:
        real = self.standin()
        exe = real / "realtool"
        shutil.copy("/bin/true", exe)
        outcome = self.run_selftest("selftest-violation", params={"real_exe": str(exe)})
        self.assertEqual(outcome.result["status"], "failed")
        self.assertIn("outside the scratch bin/", outcome.result["message"])

    def test_the_liveness_and_lock_probes_answer_inside_the_sandbox_as_on_the_host(self) -> None:
        from controller import lock, worker
        repo = fixtures.git_init(self.tmp / "hostrepo")
        host = {"own-namespace": bool(worker._proc_numbers_own_namespace(worker._proc_root())),
                "lifecycle-lock-free": str(lock.probe_lifecycle_lock(repo))}
        held = lock.acquire_lifecycle_lock(repo)
        try:
            host["lifecycle-lock-held"] = str(lock.probe_lifecycle_lock(repo))
        finally:
            held.release() if hasattr(held, "release") else held.close()
        self.assertEqual(self.observed(self.run_selftest("selftest-probes")), host)

    def test_a_scenario_failure_is_reported_as_failed_with_its_observations(self) -> None:
        outcome = self.run_selftest("selftest-fails")
        self.assertEqual(outcome.result["status"], "failed")
        self.assertIn("deliberate", outcome.result["message"])
        self.assertEqual(self.observed(outcome), {"before": 1})
        self.assertFalse(outcome.passed)

    def test_a_result_larger_than_the_pipe_buffer_comes_back_at_once_with_a_bounded_message(self) -> None:
        started = time.monotonic()
        outcome = kit.run_scenario("selftest-fails-loudly", module=SELFTEST, timeout=120)
        self.assertLess(time.monotonic() - started, 60, "the worker blocked writing its result")
        self.assertEqual(outcome.result["status"], "failed", outcome.result.get("message", "")[:200])
        message = outcome.result["message"]
        self.assertTrue(message.startswith("ScenarioFailure: loud ") and message.endswith(" [truncated]"), message[-40:])
        self.assertLessEqual(len(message), kit.MESSAGE_LIMIT + len(" [truncated]"))
        self.assertEqual(self.observed(outcome), {"bulk": "o" * (1 << 17)})
        self.assertIn("end-of-message", outcome.log, "the full message is in the scenario log")

    def test_a_process_left_behind_fails_the_scenario_and_is_ended(self) -> None:
        outcome = kit.run_scenario("selftest-leftover", module=SELFTEST)
        self.assertEqual(outcome.result["status"], "failed")
        self.assertIn("leftover processes", outcome.result["message"])
        self.assertTrue(any("leftover" in p for p in outcome.problems), outcome.problems)
        self.assertEqual(kit.wait_no_tagged("scn-selftest-leftover-", deadline=5), [])


class DeterminismTest(SandboxCase):
    """D9: observations are normalised; the digest is of the normalised record."""

    def test_two_runs_of_a_scenario_have_the_same_normalised_summary(self) -> None:
        first = self.run_selftest("selftest-basic").result["observations"]
        second = self.run_selftest("selftest-basic").result["observations"]
        self.assertEqual(first, second)
        by_name = {o["name"]: o["value"] for o in first}
        self.assertEqual(by_name["subject"], "scenario: add a")
        self.assertEqual(by_name["tip"], "<sha1>")

    def test_an_unnormalised_value_makes_the_comparison_fail(self) -> None:
        first = self.run_selftest("selftest-unnormalised").result["observations"]
        second = self.run_selftest("selftest-unnormalised").result["observations"]
        self.assertNotEqual(first, second)

    def test_a_namespace_run_and_a_diagnostic_run_have_the_same_summary(self) -> None:
        namespace = self.run_selftest("selftest-basic")
        degraded = self.run_selftest("selftest-basic", mode="degraded")
        self.assertEqual((namespace.isolation, degraded.isolation), ("namespace", "degraded"))
        self.assertEqual(namespace.result["observations"], degraded.result["observations"])

    def test_the_record_digest_ignores_what_differs_between_runs_and_sees_what_does_not(self) -> None:
        base = {"job_id": "job-ABCDEF123456", "status": "FINISHED", "started_at": "2026-01-01T00:00:00Z",
                "worker_process": {"pid": 11}, "target_head": "a" * 40, "path": "/tmp/scn-x-AbCdEf123/target"}
        other = dict(base, job_id="job-ZZZZZZ999999", started_at="2027-02-02T00:00:00Z", worker_process={"pid": 99},
                     target_head="b" * 40, path="/tmp/scn-x-QqQqQq999/target")
        self.assertEqual(kit.record_digest(base), kit.record_digest(other))
        self.assertNotEqual(kit.record_digest(base), kit.record_digest(dict(base, status="FAILED")))


class KillPointTest(SandboxCase):
    """Scenario 4's durable-write inventory and kill-point wrapper (D5.4), measured, not assumed."""

    def test_the_inventory_names_the_primitives_and_a_write_after_the_spawn(self) -> None:
        outcome = self.run_selftest("selftest-trace-lifecycle")
        seen = self.observed(outcome)
        self.assertEqual(seen["exit"], 0, seen["stderr"])
        writes = [w.split() for w in seen["writes"]]
        self.assertEqual({w[0] for w in writes}, {"write_json"}, "a plain step creates no milestone binding")
        runs = [i for i, w in enumerate(writes) if w[1].startswith("runs/")]
        jobs = [i for i, w in enumerate(writes) if w[1].startswith("jobs/")]
        self.assertLess(runs[0], jobs[0], "the initial run record is published before any job record")
        self.assertEqual(writes[jobs[0]][2], "PLANNED")
        recorded = [i for i in jobs if writes[i][2] == "LAUNCHED" and writes[i][3] == "worker=True"]
        launched = [i for i in jobs if writes[i][2] == "LAUNCHED" and writes[i][3] == "worker=False"]
        self.assertTrue(launched and recorded and launched[0] < recorded[0],
                        "the job record that records the spawned worker follows the pre-spawn LAUNCHED write")

    def test_the_milestone_binding_is_published_by_create_json_whose_last_step_is_a_link(self) -> None:
        runtime_tree = ast.parse((ROOT / "controller" / "runtime.py").read_text())
        [create] = [n for n in ast.walk(runtime_tree) if isinstance(n, ast.FunctionDef) and n.name == "create_json"]
        calls = {ast.unparse(c.func) for c in ast.walk(create) if isinstance(c, ast.Call)}
        self.assertIn("os.link", calls)
        self.assertNotIn("os.replace", calls)
        users = [p.name for p in (ROOT / "controller").glob("*.py")
                 if "runtime.create_json(" in p.read_text() and p.name != "runtime.py"]
        self.assertEqual(users, ["milestone_branch.py"])
        for when in ("before", "after"):
            self.assertEqual(kit.KILL_POINTS[f"binding-{when}"]["primitive"], "create_json")

    def test_the_wrapper_blocks_before_and_after_the_named_write_and_counts_occurrences(self) -> None:
        code = ("import json, sys\nsys.path.insert(0, sys.argv[1])\n"
                "from tests.scenarios import bootstrap_hooks as h\nh.install(json.loads(sys.argv[2]))\n"
                "from pathlib import Path\nfrom controller import runtime\n"
                "root = Path(sys.argv[3])\n"
                "runtime.create_json(root, 'repositories/k/milestones/one.json', {'n': 1})\n"
                "runtime.create_json(root, 'repositories/k/milestones/two.json', {'n': 2})\n")
        for when, occurrence, exist in (("before", 1, set()), ("after", 1, {"one"}), ("before", 2, {"one"}),
                                        ("after", 2, {"one", "two"})):
            with self.subTest(when=when, occurrence=occurrence):
                work = self.tmp / f"hook-{when}-{occurrence}"
                (work / "rt").mkdir(parents=True)
                marker = work / "marker"
                config = {"kill": dict(kit.KILL_POINTS[f"binding-{when}"], occurrence=occurrence, marker=str(marker))}
                proc = process_fixtures.popen_bounded(
                    [sys.executable, "-B", "-c", code, str(ROOT), json.dumps(config), str(work / "rt")], seconds=30,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                self.addCleanup(process_fixtures.kill_bounded, proc)
                self.assertTrue(process_fixtures.wait_until(marker.exists, timeout=15), "the kill point was not reached")
                made = {p.stem for p in (work / "rt").rglob("*.json")}
                process_fixtures.kill_bounded(proc)
                proc.wait(timeout=10)
                self.assertEqual(made, exist)

    def test_a_worker_that_never_becomes_ready_is_a_named_failure_not_a_kill(self) -> None:
        """D5: a post-spawn point is honoured only once the worker's readiness file exists; otherwise the bootstrap
        announces ``<point>.not-ready`` and never ``<point>.reached``, and the scenario's wait names the cause."""
        code = ("import json, sys\nsys.path.insert(0, sys.argv[1])\n"
                "from tests.scenarios import bootstrap_hooks as h\nh.install(json.loads(sys.argv[2]))\n"
                "from pathlib import Path\nfrom controller import runtime\n"
                "runtime.create_json(Path(sys.argv[3]), 'repositories/k/milestones/one.json', {'n': 1})\n")
        work = self.tmp / "hook-unready"
        (work / "rt").mkdir(parents=True)
        kill_dir = work / "kill"
        reached, unready = kill_dir / "job-after.reached", kill_dir / "job-after.not-ready"
        config = {"kill": dict(kit.KILL_POINTS["binding-before"], marker=str(reached), unready=str(unready),
                               ready=str(work / "never-ready"), ready_seconds=0.2)}
        proc = process_fixtures.popen_bounded(
            [sys.executable, "-B", "-c", code, str(ROOT), json.dumps(config), str(work / "rt")], seconds=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(process_fixtures.kill_bounded, proc)
        self.assertTrue(process_fixtures.wait_until(unready.exists, timeout=15), "the not-ready marker was not written")
        instance = kit.Kit.__new__(kit.Kit)
        instance.kill_dir = kill_dir
        with self.assertRaisesRegex(ScenarioFailure, "readiness file did not appear"):
            instance.wait_for_marker("job-after")
        self.assertFalse(reached.exists(), "a worker that never became ready must not be announced as reached")
        process_fixtures.kill_bounded(proc)
        proc.wait(timeout=10)

    def _oracle(self, point: str) -> dict:
        return self.observed(self.run_selftest("selftest-kill-point", params={"point": point}))

    def test_a_kill_before_or_after_the_run_record_leaves_no_job_and_no_worker(self) -> None:
        for point in ("run-before", "run-after"):
            with self.subTest(point=point):
                seen = self._oracle(point)
                self.assertEqual(seen["jobs-after-kill"], [])
                self.assertEqual((seen["workers-left-after-deadline"], seen["anchors-after-kill"]), (0, 0))
                self.assertEqual(seen["lock-after-kill"], "free")
                self.assertEqual((seen["resume-exit"], seen["jobs-after-resume"]), (0, []))
                self.assertEqual(seen["worker-invocations"], 0, "no tagged process was ever started")

    def test_job_after_the_spawn_is_reconciled_by_resume_inside_the_sandbox(self) -> None:
        """The oracle in the mode that produces the records: the worker is gone after its deadline, the killed
        Controller's stdin anchor still holds the lifecycle lock, and `resume` reconciles the job to its terminal
        status with the documented exit code, ending the anchor."""
        seen = self._oracle("job-after")
        self.assertEqual(seen["jobs-after-kill"], ["LAUNCHED"])
        self.assertEqual((seen["workers-left-after-deadline"], seen["anchors-after-kill"]), (0, 1))
        self.assertEqual(seen["lock-after-kill"], "held")
        self.assertEqual((seen["resume-exit"], seen["jobs-after-resume"]), (0, ["FINISHED"]))
        self.assertEqual((seen["anchors-after-resume"], seen["worker-invocations"]), (0, 1))

    def test_job_before_the_spawn_is_recorded_leaves_an_anchor_that_resume_cannot_reconcile(self) -> None:
        """MEASURED, contradicting the plan's oracle for `job-before` (CP3 must amend it): the worker has been
        spawned but the record that names it was never written, so `resume` finds no recorded worker, exits 45
        and the anchor keeps the lifecycle lock; the scenario has to end it itself."""
        seen = self._oracle("job-before")
        self.assertEqual(seen["jobs-after-kill"], ["LAUNCHED"])
        self.assertEqual((seen["workers-left-after-deadline"], seen["anchors-after-kill"]), (0, 1))
        self.assertEqual(seen["lock-after-kill"], "held")
        self.assertEqual((seen["resume-exit"], seen["jobs-after-resume"]), (45, ["LAUNCHED"]))
        self.assertEqual((seen["anchors-after-resume"], seen["worker-invocations"]), (1, 1))

    def test_a_post_spawn_kill_point_needs_the_worker_readiness_file(self) -> None:
        # The check precedes any process start, so no Kit is needed to see it.
        instance = kit.Kit.__new__(kit.Kit)
        instance.kill_dir = self.tmp
        with self.assertRaises(ScenarioFailure):
            instance.child_controller([], kill_at="job-after")


class CheckoutSnapshotTest(unittest.TestCase):
    """D3b: content-sensitive, token-free changes included."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scenario-snapshot-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = fixtures.git_init(self.tmp / "repo")
        for rel, text in (("controller/a.py", "a = 1\n"), ("tests/t.py", "t = 1\n"), ("tools/x.py", "x = 1\n"),
                          ("docs/ai-workflow/WORKFLOW_STATE.json", "{}\n")):
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text)
        fixtures.commit_all(self.repo, "initial")
        self.before = kit.snapshot_checkout(self.repo)

    def changed(self) -> list[str]:
        after = kit.snapshot_checkout(self.repo)
        return [k for k in self.before if self.before[k] != after[k]]

    def test_an_untouched_checkout_is_identical(self) -> None:
        self.assertEqual(self.changed(), [])

    def test_a_same_size_overwrite_with_no_token_is_seen(self) -> None:
        (self.repo / "controller" / "a.py").write_text("a = 2\n")
        self.assertEqual(self.changed(), ["content"])

    def test_a_deletion_and_a_new_untracked_file_are_seen(self) -> None:
        (self.repo / "tests" / "t.py").unlink()
        self.assertEqual(self.changed(), ["content"])
        (self.repo / "tests" / "t.py").write_text("t = 1\n")
        self.assertEqual(self.changed(), [])
        (self.repo / "tools" / "new.py").write_text("n = 1\n")
        self.assertEqual(self.changed(), ["content"])

    def test_a_second_change_of_an_already_dirty_file_and_index_entry_is_seen(self) -> None:
        path = self.repo / "controller" / "a.py"
        path.write_text("a = 2\n")
        fixtures.run(["git", "add", "controller/a.py"], cwd=self.repo)
        dirty = kit.snapshot_checkout(self.repo)
        path.write_text("a = 3\n")
        self.assertNotEqual(kit.snapshot_checkout(self.repo)["content"], dirty["content"])
        fixtures.run(["git", "add", "controller/a.py"], cwd=self.repo)
        again = kit.snapshot_checkout(self.repo)
        self.assertNotEqual(again["index"], dirty["index"])
        self.assertEqual(again["head"], dirty["head"])

    def test_workflow_bookkeeping_and_the_refs_are_outside_and_inside_the_digest_respectively(self) -> None:
        (self.repo / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text('{"x": 1}\n')
        self.assertEqual(self.changed(), [], "bookkeeping is outside the code digest (a concurrent lane writes it)")
        fixtures.run(["git", "branch", "other"], cwd=self.repo)
        self.assertEqual(self.changed(), ["refs"])

    def test_an_inherited_git_override_does_not_reach_the_snapshot(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"GIT_DIR": "/nonexistent", "GIT_INDEX_FILE": "/nonexistent/i",
                                                   "GIT_WORK_TREE": "/nonexistent"}):
            self.assertEqual(kit.snapshot_checkout(self.repo), self.before)
            self.assertEqual(kit.head_of(self.repo), self.before["head"])


class GitMetadataTest(SandboxCase):
    """A checkout whose Git directory and common directory lie outside it."""

    def test_a_linked_worktree_has_its_external_git_metadata_protected_and_a_dangling_one_is_refused(self) -> None:
        main = fixtures.git_init(self.tmp / "main")
        (main / "f").write_text("f\n")
        fixtures.commit_all(main, "initial")
        linked = self.tmp / "linked"
        fixtures.run(["git", "worktree", "add", "-q", str(linked), "-b", "side"], cwd=main)
        metadata = kit.resolve_git_metadata(linked)
        self.assertEqual({p.name for p in metadata}, {linked.name, ".git"})
        self.assertTrue(all(linked.resolve() not in p.parents for p in metadata))
        plan = {entry["path"] for entry in kit.protection_plan(linked)}
        self.assertTrue({str(p) for p in metadata} <= plan | {str(main.resolve())} or
                        all(any(str(p) == q or str(p).startswith(q + "/") for q in plan) for p in metadata), plan)

        attack = r"""
import errno, json, os, subprocess, sys
linked, git = sys.argv[1], sys.argv[2]
env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
def git_run(*args):
    r = subprocess.run([git, "-C", linked, *args], capture_output=True, text=True, env=env)
    return [r.returncode != 0, "Read-only" in r.stderr or "denied" in r.stderr.lower() or bool(r.stderr)]
git_dir = subprocess.run([git, "-C", linked, "rev-parse", "--absolute-git-dir"], capture_output=True, text=True, env=env).stdout.strip()
def lock():
    try:
        open(os.path.join(git_dir, "index.lock"), "w").close(); return "ok"
    except OSError as exc:
        return errno.errorcode[exc.errno]
out = {"update_ref": git_run("update-ref", "refs/heads/x", "HEAD"), "lock": lock(), "worktree_write": None}
try:
    open(os.path.join(linked, "g"), "w").close(); out["worktree_write"] = "ok"
except OSError as exc:
    out["worktree_write"] = errno.errorcode[exc.errno]
print(json.dumps(out))
"""
        scratch = Path(tempfile.mkdtemp(prefix="scn-meta-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        outcome = kit._launch(kit.Chain.resolve(), scratch=scratch, protect=kit.protection_plan(linked),
                              worker=[sys.executable, "-I", "-c", "import os; os.write(4, b'drop-ok\\n')\n" + attack,
                                      str(linked), shutil.which("git")],
                              env={"PATH": os.devnull, "LC_ALL": "C"}, cwd=None, timeout=60)
        result = json.loads(outcome["stdout"].strip().splitlines()[-1])
        self.assertEqual(result["update_ref"], [True, True], outcome)
        self.assertEqual((result["lock"], result["worktree_write"]), ("EROFS", "EROFS"))
        # Nothing changed in the real stand-in repositories.
        self.assertEqual(fixtures.run(["git", "branch", "--list", "x"], cwd=main).stdout, "")

        dangling = self.tmp / "dangling"
        dangling.mkdir()
        (dangling / ".git").write_text("gitdir: /nonexistent/gitdir\n")
        with self.assertRaises(kit.MetadataRefused):
            kit.resolve_git_metadata(dangling)
        with self.assertRaises(kit.MetadataRefused):
            kit.run_scenario("selftest-basic", module=SELFTEST, checkout=dangling)


class ProtectionPlanTemporaryDirectoryTest(unittest.TestCase):
    """The shard runner sets ``TMPDIR`` to a directory that exists and lacks ``workflow-controller-tests``: the plan
    must not cover that missing location through the temporary directory, which holds the scratch tree. An existing
    real location that contains the temporary directory stays protected (the scratch tmpfs is mounted after it)."""

    @staticmethod
    def plan_and_real(shard_tmp: str, extra: "list[Path]" = ()) -> tuple[set[str], set[str]]:
        with unittest.mock.patch.dict(os.environ, {"TMPDIR": shard_tmp}), \
                unittest.mock.patch.object(tempfile, "tempdir", None):
            plan = {entry["path"] for entry in kit.protection_plan(kit.REPO_ROOT, extra=extra)}
            real = {os.path.realpath(p) for p in [*kit.real_locations(kit.REPO_ROOT), *extra] if p.exists()}
        return plan, real

    @staticmethod
    def containing(plan: set[str], path: str) -> set[str]:
        return {p for p in plan if path == p or path.startswith(p.rstrip("/") + "/")}

    def test_a_per_shard_temporary_directory_is_never_made_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="scenario-shard-tmp-") as shard_tmp:
            plan, real = self.plan_and_real(shard_tmp)
            shard = os.path.realpath(shard_tmp)
            self.assertNotIn(shard, plan)
            # Only an existing real location (here, when the outer TMPDIR is a sharded one, /tmp/workflow-controller-tests)
            # may contain it, never an ancestor chosen to cover a missing location.
            self.assertLessEqual(self.containing(plan, shard), real, plan)

    def test_an_existing_real_location_that_contains_the_temporary_directory_stays_protected(self) -> None:
        # A sharded run: TMPDIR lies under /tmp/workflow-controller-tests, which is itself a real location (D3).
        with tempfile.TemporaryDirectory(prefix="scenario-results-") as results:
            shard_tmp = Path(results) / "run" / "shard-0" / "tmp"
            shard_tmp.mkdir(parents=True)
            plan, _ = self.plan_and_real(str(shard_tmp), extra=[Path(results)])
            self.assertTrue(self.containing(plan, os.path.realpath(results)), plan)


class ScratchInsideProtectedLocationTest(SandboxCase):
    """The scratch tmpfs is mounted after the read-only binds: a scratch tree inside a protected location is writable,
    the rest of that location is not."""

    def test_the_scratch_tree_stays_writable_inside_a_protected_location(self) -> None:
        real = self.standin()
        scratch = real / "shard" / "tmp" / "scn-nested-AbCdEf12"
        scratch.mkdir(parents=True)
        attack = r"""
import errno, json, os, sys
def write(path):
    try:
        with open(path, "w") as fh: fh.write("x")
        return "ok"
    except OSError as exc:
        return errno.errorcode[exc.errno]
print(json.dumps({"scratch": write(os.path.join(sys.argv[1], "x")), "sibling": write(os.path.join(sys.argv[2], "x"))}))
"""
        outcome = kit._launch(kit.Chain.resolve(), scratch=scratch, protect=[{"path": str(real), "mode": "ro"}],
                              worker=[sys.executable, "-I", "-c", "import os; os.write(4, b'drop-ok\\n')\n" + attack,
                                      str(scratch), str(real / "shard")],
                              env={"PATH": os.devnull, "LC_ALL": "C"}, cwd=None, timeout=60)
        result = json.loads(outcome["stdout"].strip().splitlines()[-1])
        self.assertEqual(result, {"scratch": "ok", "sibling": "EROFS"}, outcome)
        self.assertEqual(list(scratch.iterdir()), [], "a scratch write reached the host")


class LeakScanTest(unittest.TestCase):
    """D3b(b): the diagnostic mode's token scan, and what it cannot see."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scenario-leak-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.real = self.tmp / "real"
        self.real.mkdir()
        (self.real / "existing.txt").write_text("someone else's data\n")
        self.token, self.scratch = "scn-flow-AbCdEf123", "/tmp/scn-flow-AbCdEf123"
        time.sleep(0.02)
        self.since = time.time()

    def scan(self) -> list[str]:
        return kit.leak_scan([self.real], self.token, self.scratch, self.since)

    def test_concurrent_unrelated_writes_pass(self) -> None:
        (self.real / "other-process.log").write_text("a live Controller writing\n")
        self.assertEqual(self.scan(), [])

    def test_a_tokened_name_or_content_is_a_leak(self) -> None:
        (self.real / f"{self.token}.json").write_text("{}")
        (self.real / "content.txt").write_text(f"path {self.scratch}/x")
        self.assertEqual(sorted(Path(p).name for p in self.scan()), sorted([f"{self.token}.json", "content.txt"]))

    def test_the_scratch_tree_inside_a_scanned_location_is_not_a_leak(self) -> None:
        # A sharded run's scratch tree lies under /tmp/workflow-controller-tests, which the scan walks.
        scratch = self.real / "shard" / self.token
        scratch.mkdir(parents=True)
        (scratch / f"{self.token}.json").write_text(f"path {scratch}/x")
        (self.real / "shard" / "leaked.txt").write_text(f"path {scratch}/x")
        self.assertEqual([Path(p).name for p in kit.leak_scan([self.real], self.token, str(scratch), self.since)],
                         ["leaked.txt"])

    def test_a_token_free_overwrite_is_not_seen_which_is_why_it_is_never_the_proof(self) -> None:
        (self.real / "existing.txt").write_text("overwritten with no token\n")
        self.assertEqual(self.scan(), [])


class FailClosedTest(unittest.TestCase):
    """D3, "Isolation unavailable": no scenario runs, and the gate says why."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scenario-failclosed-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.real = self.tmp / "real"
        self.real.mkdir()
        self.addCleanup(kit._PROBE_CACHE.clear)

    def script(self, name: str, body: str, interpreter: str = "/bin/sh") -> Path:
        path = self.tmp / name
        path.write_text(f"#!{interpreter}\n{body}")
        path.chmod(0o755)
        return path

    def chains(self) -> dict:
        _gate(self)
        real = kit.Chain.resolve()
        refused = self.script("unshare-refused", 'echo "unshare: write failed /proc/self/uid_map: Operation not '
                              'permitted" >&2\nexit 1\n')
        apparmor = self.script("unshare-apparmor", 'echo "unshare: unshare failed: Permission denied (apparmor: '
                               'unprivileged_userns)" >&2\nexit 1\n')
        failing_setup = self.tmp / "failing_setup.py"
        failing_setup.write_text("import os, sys\nos.write(4, b'userns\\n')\nsys.stderr.write('mount failed')\nsys.exit(1)\n")
        failing_drop = self.script("drop-fails", 'echo "setpriv: drop failed" >&2\nexit 1\n')
        return {
            "user-namespace": kit.Chain(unshare=str(refused)),
            "apparmor": kit.Chain(unshare=str(apparmor)),
            "setup": kit.Chain(unshare=real.unshare, setup_script=failing_setup),
            "drop": kit.Chain(unshare=real.unshare, drop=[str(failing_drop)]),
        }

    def test_the_probe_names_the_failed_step_for_each_stubbed_failure(self) -> None:
        for name, chain in self.chains().items():
            with self.subTest(step=name):
                probe = kit.probe_isolation(chain, cache=False)
                self.assertFalse(probe.ok)
                self.assertEqual(probe.step, "user-namespace" if name == "apparmor" else name)
                self.assertIn(probe.step, kit.STEPS)

    def test_a_drop_that_fails_while_unshare_succeeds_is_unavailable_never_a_downgrade(self) -> None:
        probe = kit.probe_isolation(self.chains()["drop"], cache=False)
        self.assertEqual((probe.ok, probe.step), (False, "drop"))
        self.assertIn("capability drop failed", probe.detail)

    def test_an_unavailable_sandbox_runs_no_scenario_and_touches_nothing(self) -> None:
        marker = self.real / "scenario-ran"
        for name, chain in self.chains().items():
            with self.subTest(step=name), unittest.mock.patch.object(kit, "_launch", wraps=kit._launch) as launch:
                real_probe = kit.probe_isolation(chain, cache=False)
                with unittest.mock.patch.object(kit, "probe_isolation", return_value=real_probe):
                    with self.assertRaises(kit.IsolationUnavailable) as raised:
                        kit.run_scenario("selftest-basic", module=SELFTEST, chain=chain, params={"marker": str(marker)})
                self.assertIn(raised.exception.step, kit.STEPS)
                scenario_launches = [c for c in launch.call_args_list if kit.WORKER_CODE in c.kwargs["worker"]]
                self.assertEqual(scenario_launches, [], "no scenario function ran: the invocation count stays 0")
            self.assertEqual(list(self.real.iterdir()), [])

    def test_the_gate_skips_locally_and_fails_when_ci_is_set_naming_the_step(self) -> None:
        for name, chain in self.chains().items():
            probe = kit.probe_isolation(chain, cache=False)
            with self.subTest(step=name):
                action, reason = kit.isolation_gate(probe, {})
                self.assertEqual(action, "skip")
                self.assertIn(repr(probe.step), reason)
                self.assertEqual(kit.isolation_gate(probe, {"CI": "true"})[0], "fail")
        self.assertEqual(kit.isolation_gate(kit.Probe(True, None, "ok"), {"CI": "true"}), ("run", ""))

    def test_the_degraded_mode_is_labelled_and_never_claims_the_namespace(self) -> None:
        outcome = kit.run_scenario("selftest-basic", module=SELFTEST, mode="degraded")
        self.assertEqual(outcome.isolation, "degraded")
        self.assertEqual(outcome.result["status"], "passed", outcome.result)


class RegistryTest(unittest.TestCase):
    def test_the_registry_refuses_a_flow_id_the_workflow_would_refuse_and_a_duplicate(self) -> None:
        for bad in ("scenario.full", "Scenario", "", "x" * 65, "-lead"):
            with self.subTest(flow_id=bad), self.assertRaises(ValueError):
                scenario(bad)
        import importlib
        importlib.import_module(SELFTEST)
        with self.assertRaises(ValueError):
            scenario("selftest-basic")
        for flow_id, entry in REGISTRY.items():
            self.assertTrue(FLOW_ID_RE.match(flow_id))

    def test_selftest_scenarios_are_not_part_of_the_catalogue(self) -> None:
        import importlib
        importlib.import_module(SELFTEST)
        from tests import scenarios
        self.assertTrue(REGISTRY and all(s.selftest == s.flow_id.startswith("selftest-") for s in REGISTRY.values()))
        self.assertFalse([s for s in scenarios.catalogued() if s.flow_id.startswith("selftest-")])


class FixtureHygieneTest(unittest.TestCase):
    def test_no_scenario_helper_uses_chmod_to_make_a_write_fail(self) -> None:
        """A permission bit is not a containment mechanism here (D3): it would fail differently in the diagnostic
        mode. Only a mode that keeps the owner-write bit (an executable bit) is used."""
        for path in (ROOT / "tests" / "scenarios").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "chmod":
                    modes = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, int)]
                    self.assertTrue(modes and all(m & 0o200 for m in modes), f"{path.name}:{node.lineno}")
        for path in (ROOT / "tests" / "scenarios").glob("*.py"):
            self.assertNotIn("os.chmod(", path.read_text().replace("# ", ""), path.name)


if __name__ == "__main__":
    unittest.main()
