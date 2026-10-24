"""The scenario runner (``tools/run_scenarios.py``): record shape, acceptance by
the Workflow's own ingestion, the scoped refusal, fail-closed isolation and
determinism (``workflow-controller-automated-lifecycle-scenarios`` D4, D9).

The runner refuses a tree whose code differs from ``HEAD``, and a development
tree usually does. So every test runs the *clone's* copy of the runner: a
disposable clone of the checkout with the working tree's code committed on top
(``CloneCase``), which is clean by construction.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.scenarios import kit

REPO_ROOT = Path(__file__).resolve().parent.parent
SELFTEST = "tests.scenarios.selftest"
WORK_ITEM = "workflow-controller-automated-lifecycle-scenarios"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}

_PROBE = kit.probe_isolation()


# A pass of the catalogue determinism check gets 45 minutes, against about 16 for one pass run alone.
PASS_DEADLINE = 2700
# The bounds of ``run_bounded``'s forced path: the grace given to the reaper after ``SIGTERM``, and the sweep's own bound.
PASS_GRACE = 10
SWEEP_BOUND = 10.0

# The reaping subreaper ``run_bounded`` starts the real command under. The scenario kit puts its heavy descendants in
# sessions of their own, so a process-group signal does not reach them; as a subreaper this process adopts every
# descendant that survives the real command, in any session, and kills and reaps them before it exits, on every path.
REAPER_SOURCE = r"""
import ctypes, os, signal, subprocess, sys, time

if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    sys.exit(3)
child = subprocess.Popen(sys.argv[2:], start_new_session=True)  # argv: -c -- <command>


def adopted():
    found = set()
    for task in os.listdir("/proc/self/task"):
        try:
            with open(f"/proc/self/task/{task}/children") as fh:
                found.update(int(pid) for pid in fh.read().split())
        except OSError:
            continue
    return found


def kill_group(group):
    if group != os.getpgrp():
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            pass


def reap_all():
    for _ in range(400):
        pids = adopted()
        if not pids:
            return
        for pid in pids:
            for target in (lambda: os.getpgid(pid), lambda: os.getsid(pid)):
                try:
                    kill_group(target())
                except OSError:
                    pass
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        time.sleep(0.01)


def on_term(signum, frame):
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except OSError:
        pass
    reap_all()
    os._exit(143)


signal.signal(signal.SIGTERM, on_term)
code = child.wait()
reap_all()
sys.exit(code if code >= 0 else 128 - code)
"""


class PassFailure(AssertionError):
    """A bounded pass that timed out or left a process behind."""


def _sweep(tags: "list[str]", *, bound: float) -> "list[int]":
    """SIGKILL every process carrying any of ``tags`` until none is left or ``bound`` seconds pass; returns survivors."""
    end = time.monotonic() + bound
    while True:
        alive = sorted({pid for tag in tags for pid in kit.tagged_pids(tag)})
        for pid in alive:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(OSError):
                os.waitpid(pid, os.WNOHANG)
        if not alive or time.monotonic() >= end:
            return alive
        time.sleep(0.05)


def run_bounded(argv: "list[str]", *, cwd: Path, env: dict, deadline: float, grace: float = PASS_GRACE,
                tags: "tuple[str, ...]" = (), name: str = "pass", reaper: str = REAPER_SOURCE):
    """Run ``argv`` to completion within ``deadline`` seconds and leave nothing of it behind.

    The command starts in a session of its own under a reaping subreaper (``REAPER_SOURCE``) with ``TMPDIR`` set to a
    pass-owned temporary root, which the scenario kit's scratch directories, and so every sandbox descendant's
    environment, live under. At the deadline the group and the reaper get ``SIGTERM``; after ``grace`` seconds the
    group gets ``SIGKILL`` and every process still carrying the root (or one of ``tags``) is swept for at most
    ``SWEEP_BOUND`` seconds. The reaper is always ``wait()``ed before a failure is raised, so the whole call takes at
    most ``deadline + grace + SWEEP_BOUND``. Returns a ``CompletedProcess``; raises ``PassFailure`` on a timeout or a
    leak. The pass-owned root is removed on return.
    """
    root = Path(tempfile.mkdtemp(prefix="det-pass-"))
    try:
        out, err = root / "stdout", root / "stderr"
        with open(out, "w") as out_fh, open(err, "w") as err_fh:
            proc = subprocess.Popen([sys.executable, "-I", "-c", reaper, "--", *argv], cwd=cwd,
                                    env={**env, "TMPDIR": str(root)}, stdout=out_fh, stderr=err_fh,
                                    start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=deadline)
            except subprocess.TimeoutExpired:
                timed_out = True
                for send in (lambda: os.killpg(proc.pid, signal.SIGTERM), lambda: proc.send_signal(signal.SIGTERM)):
                    with contextlib.suppress(OSError):
                        send()
                try:
                    proc.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    proc.kill()
            proc.wait()
            survivors = _sweep([str(root), *tags], bound=SWEEP_BOUND)
        stdout, stderr = out.read_text(errors="replace"), err.read_text(errors="replace")
        if survivors:
            raise PassFailure(f"{name} left processes behind: {survivors}\n{stdout[-2000:]}{stderr[-2000:]}")
        if timed_out:
            raise PassFailure(f"{name} did not finish within its {deadline}-second deadline\n"
                              f"stdout: {stdout[-2000:]}\nstderr: {stderr[-2000:]}")
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_concurrently(jobs: list):
    """Run the callables ``jobs`` in threads at the same time and wait for all of them; returns their results in job
    order. The first failure, in job order, is raised only after every job has finished, so a failing job never masks
    another and nothing runs once this returns. Each job is bounded by its own deadline, so the wait is finite."""
    results: list = [None] * len(jobs)
    failures: "list[BaseException | None]" = [None] * len(jobs)

    def work(index: int) -> None:
        try:
            results[index] = jobs[index]()
        except BaseException as exc:  # noqa: BLE001 -- re-raised below, after every job has finished
            failures[index] = exc

    threads = [threading.Thread(target=work, args=(i,), name=f"pass-{i}") for i in range(len(jobs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for failure in failures:
        if failure is not None:
            raise failure
    return results


def _git(cwd: Path, *args: str) -> str:
    env = {**kit.sanitised_git_env(), **GIT_ENV}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def load_runner(root: Path):
    spec = importlib.util.spec_from_file_location("_run_scenarios_under_test", root / "tools" / "run_scenarios.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CloneCase(unittest.TestCase):
    """A clone of the checkout carrying the working tree's code as one commit."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _PROBE.ok:
            raise unittest.SkipTest(f"sandbox unavailable at {_PROBE.step}: {_PROBE.detail}")
        cls.base = Path(tempfile.mkdtemp(prefix="run-scenarios-test-"))
        cls.addClassCleanup(shutil.rmtree, cls.base, ignore_errors=True)
        cls.clone = cls.base / "clone"
        _git(cls.base, "clone", "-q", "--no-hardlinks", str(REPO_ROOT), str(cls.clone))
        listing = _git(REPO_ROOT, "ls-files", "-co", "--exclude-standard", "-z", "--",
                       *kit.CODE_PATHS).split("\0")
        for name in filter(None, listing):
            source, target = REPO_ROOT / name, cls.clone / name
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        # the working tree's Workflow bookkeeping, so the clone resolves the same work item
        for name in ("docs/ai-workflow/WORKFLOW_STATE.json",):
            shutil.copy2(REPO_ROOT / name, cls.clone / name)
        # CI's checkout is shallow, so the work item's base commit, which the Workflow's identity
        # is computed from, may be missing; the clone then measures from the checkout's own HEAD.
        state_path = cls.clone / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        item = state["work_items"][WORK_ITEM]
        probe = subprocess.run(["git", "cat-file", "-e", f"{item['base_commit']}^{{commit}}"], cwd=cls.clone,
                               env=kit.sanitised_git_env(), capture_output=True)
        if probe.returncode != 0:
            item["base_commit"] = _git(cls.clone, "rev-parse", "HEAD").strip()
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _git(cls.clone, "add", "-A")
        _git(cls.clone, "commit", "-q", "--allow-empty", "-m", "test: working tree code")
        cls.head = _git(cls.clone, "rev-parse", "HEAD").strip()

    def setUp(self) -> None:
        self.out = Path(tempfile.mkdtemp(prefix="run-scenarios-out-"))
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        _git(self.clone, "reset", "-q", "--hard", self.head)
        _git(self.clone, "clean", "-qfd", "--", *kit.CODE_PATHS)

    def runner(self, *args: str, output: "Path | None" = None, module: bool = True, clone: "Path | None" = None,
               timeout: "float | None" = None):
        clone = clone or self.clone
        argv = [sys.executable, "-B", str(clone / "tools" / "run_scenarios.py"), *args]
        if args[0] == "run":
            argv += ["--output", str(output or self.out)] + (["--module", SELFTEST] if module else [])
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        if timeout is not None:
            tags = (str(clone), str(output or self.out))
            return run_bounded(argv, cwd=clone, env=env, deadline=timeout, tags=tags, name=f"run in {clone.name}")
        return subprocess.run(argv, cwd=clone, env=env, capture_output=True, text=True)

    def records(self, output: "Path | None" = None) -> dict:
        folder = output or self.out
        return {p.stem: json.loads(p.read_text()) for p in sorted(folder.glob("*.json")) if p.name != "index.json"}

    def assert_ingested(self, records: dict) -> None:
        """Every record passes the Workflow's real ``ingest_functional_evidence`` in the clone, with the stored head and
        the stored identity asserted separately (D4)."""
        scripts = str(self.clone / "scripts")
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        import workflow_gate_policy as gate
        import workflow_state as ws
        import workflow_fingerprint as fingerprint
        state = json.loads((self.clone / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        item = state["work_items"][WORK_ITEM]
        for flow_id, record in records.items():
            with self.subTest(flow_id=flow_id):
                _, result = gate.ingest_functional_evidence(
                    self.clone, state, WORK_ITEM, record, now="2026-10-12T12:00:00Z")
                stored = result["stored"]
                self.assertEqual(stored["head"], _git(self.clone, "rev-parse", "HEAD").strip(), flow_id)
                expected = ws.approval_review_content_id(
                    self.clone, stage="implementation", base_commit=item["base_commit"],
                    work_item_type=item["work_item_type"], work_item_id=WORK_ITEM, head=record["head"],
                    artifacts_path=fingerprint.artifacts_path_for_work_item(WORK_ITEM))
                self.assertEqual(stored["identity"], expected, flow_id)
                self.assertNotEqual(len(stored["head"]), len(stored["identity"]))  # a commit, not a content id


class RecordShapeTest(CloneCase):
    def test_passing_and_failing_scenarios_write_protocol_records(self) -> None:
        done = self.runner("run", "selftest-basic", "selftest-fails")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)  # a failure is evidence, the status is 1
        runner = load_runner(self.clone)
        records = self.records()
        self.assertEqual(sorted(records), ["selftest-basic", "selftest-fails"])
        for flow_id, record in records.items():
            self.assertEqual(runner.validate_record(record, flow_id), [])
            self.assertEqual(sorted(record), sorted(runner.RECORD_KEYS))
            self.assertEqual(record["head"], self.head)
            self.assertEqual(record["summary"]["isolation"], "namespace")
            self.assertIsNone(record["run_ref"])
            self.assertTrue(record["reporter"].startswith("workflow-controller-scenarios/"))
        self.assertEqual(records["selftest-basic"]["status"], "passed")
        self.assertEqual(records["selftest-fails"]["status"], "failed")
        self.assertIn("deliberate", records["selftest-fails"]["summary"]["message"])
        index = json.loads((self.out / "index.json").read_text())
        self.assertEqual(index["head"], self.head)
        self.assertEqual(index["exit_status"], 1)
        self.assertEqual(index["order"], ["selftest-basic", "selftest-fails"])
        self.assertEqual(sorted(index["records"]), sorted(records))

    def test_the_schema_check_alone_misses_a_dotted_flow_id_so_the_runner_checks_it(self) -> None:
        runner = load_runner(self.clone)
        self.runner("run", "selftest-basic")
        record = self.records()["selftest-basic"]
        self.assertEqual(runner.validate_record(record), [])
        for bad in ({**record, "flow_id": "scenario.dotted"}, {**record, "status": "maybe"},
                    {**record, "head": "abc"}, {**record, "extra": 1}, {**record, "log_digest": "zz"},
                    {**record, "ran_at": "yesterday"}, {**record, "summary": []}):
            self.assertNotEqual(runner.validate_record(bad), [], bad)

    def test_unknown_scenario_is_refused_and_writes_nothing(self) -> None:
        done = self.runner("run", "no-such-scenario")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(list(self.out.glob("*")), [])

    def test_list_hides_the_selftest_scenarios_unless_asked(self) -> None:
        catalogue = self.runner("list", module=False).stdout.split()
        self.assertTrue(catalogue and all(name.startswith("scenario-") for name in catalogue), catalogue)
        listed = self.runner("list", "--all").stdout.split()
        self.assertIn("selftest-basic", listed)


class IngestionTest(CloneCase):
    """Every record is accepted by the Workflow's real ingestion in a clone at the runner's HEAD."""

    def test_records_are_accepted_with_head_and_identity_asserted_separately(self) -> None:
        self.runner("run", "selftest-basic", "selftest-fails", "selftest-env")
        records = self.records()
        self.assertEqual(len(records), 3)
        self.assert_ingested(records)


class ScopedRefusalTest(CloneCase):
    def refused(self, why: str) -> None:
        done = self.runner("run", "selftest-basic")
        self.assertEqual(done.returncode, 2, why + done.stdout + done.stderr)
        self.assertIn("code differs from HEAD", done.stderr)
        self.assertEqual(list(self.out.glob("*")), [], "a refused run writes nothing")

    def test_dirty_workflow_bookkeeping_is_accepted(self) -> None:
        for name in ("docs/ai-workflow/WORKFLOW_STATE.json", "docs/ACTIVE_MILESTONE.md"):
            with (self.clone / name).open("a") as fh:
                fh.write("\n")
        (self.clone / ".ai-review").mkdir(exist_ok=True)
        (self.clone / ".ai-review" / "note.txt").write_text("x")
        done = self.runner("run", "selftest-basic")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_modified_controller_file_is_refused(self) -> None:
        with (self.clone / "controller" / "__init__.py").open("a") as fh:
            fh.write("\n# edit\n")
        self.refused("controller")

    def test_a_staged_tests_file_is_refused(self) -> None:
        target = self.clone / "tests" / "test_run_scenarios.py"
        target.write_text(target.read_text() + "\n# edit\n")
        _git(self.clone, "add", "tests/test_run_scenarios.py")
        self.refused("staged")

    def test_a_new_untracked_tools_file_is_refused(self) -> None:
        (self.clone / "tools" / "stray.py").write_text("x = 1\n")
        self.refused("untracked")

    def test_a_module_outside_the_tests_package_is_refused(self) -> None:
        stray = self.clone / "docs" / "evil"
        stray.mkdir()
        self.addCleanup(shutil.rmtree, stray, ignore_errors=True)
        (stray / "fake.py").write_text("x = 1\n")
        for module in ("docs.evil.fake", "scripts.x"):
            done = self.runner("run", "selftest-basic", "--module", module, module=False)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("outside the tests package", done.stderr)
            self.assertEqual(list(self.out.glob("*")), [], "a refused run writes nothing")

    def test_the_output_directory_inside_the_tree_is_not_code(self) -> None:
        inside = self.clone / "tools" / "evidence-out"
        done = self.runner("run", "selftest-basic", output=inside)
        self.assertEqual(done.returncode, 0, done.stderr)
        again = self.runner("run", "selftest-basic", output=inside)
        self.assertEqual(again.returncode, 0, again.stderr)


class OutputPlacementTest(CloneCase):
    """The output directory exempts only the evidence files the runner writes (round 4, I2)."""

    def _refused_with(self, output: Path) -> None:
        (self.clone / "tests" / "scenarios" / "stray.py").write_text("x = 1\n")
        done = self.runner("run", "selftest-basic", output=output)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("stray.py", done.stderr)

    def test_an_output_directory_over_tests_does_not_hide_a_new_scenario_module(self) -> None:
        self._refused_with(self.clone / "tests")

    def test_an_output_directory_over_the_root_does_not_hide_a_new_scenario_module(self) -> None:
        self._refused_with(self.clone)

    def test_an_output_directory_over_controller_does_not_hide_a_tracked_edit(self) -> None:
        with (self.clone / "controller" / "__init__.py").open("a") as fh:
            fh.write("\n# edit\n")
        done = self.runner("run", "selftest-basic", output=self.clone / "controller")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("code differs from HEAD", done.stderr)

    def test_only_generated_evidence_files_are_exempt(self) -> None:
        runner = load_runner(self.clone)
        out = self.clone / "tools" / "evidence-out"
        (out / "degraded").mkdir(parents=True)
        outcome = kit.Outcome("selftest-basic", "namespace", {"status": "passed"}, "", "0" * 40, True, [], 0.0)
        record = runner._canonical(runner.build_record(outcome, "workflow-controller-scenarios/1", "2026-10-12T12:00:00Z"))
        (out / "selftest-basic.json").write_text(record)
        (out / "index.json").write_text(runner._canonical(
            {"head": "0" * 40, "isolation": "namespace", "reporter": "workflow-controller-scenarios/1", "order": [],
             "exit_status": 0, "records": {"selftest-basic": runner._digest(record)}}))
        (out / "degraded" / "index.json").write_text(runner._canonical(
            {"evidence": False, "isolation": "degraded", "failed_step": None, "detail": "", "reports": {}}))
        (out / "degraded" / "a.report.json").write_text(runner._canonical(
            {"scenario": "a", "passed": True, "observations": [], "message": "", "problems": []}))
        self.assertEqual(runner.dirty_code_paths(self.clone, ignore=out.resolve()), [])
        for name in ("helper.py", "Not A Flow.json", "degraded/helper.py", "nested/x.json"):
            (out / name).parent.mkdir(exist_ok=True)
            (out / name).write_text("x = 1\n")
        self.assertEqual(sorted(runner.dirty_code_paths(self.clone, ignore=out.resolve())),
                         sorted(f"tools/evidence-out/{n}" for n in
                                ("helper.py", "Not A Flow.json", "degraded/helper.py", "nested/x.json")))

    def test_a_smaller_run_leaves_no_stale_record_beside_its_index(self) -> None:
        self.assertEqual(self.runner("run", "selftest-basic", "selftest-env").returncode, 0)
        self.assertTrue((self.out / "selftest-env.json").exists())
        self.assertEqual(self.runner("run", "selftest-basic").returncode, 0)
        self.assertFalse((self.out / "selftest-env.json").exists())
        self.assertEqual(self.runner("check", "--output", str(self.out)).returncode, 0)

    def test_a_run_leaves_foreign_json_in_the_output_directory(self) -> None:
        foreign = {"my-settings.json": '{"a": 1}', "package.json": "{}", "list.json": "[1]", "broken.json": "{",
                   "other-flow.json": '{"flow_id": "different"}',
                   "my-config.json": json.dumps({"flow_id": "my-config", "log_digest": "0" * 64})}
        for name, text in foreign.items():
            (self.out / name).write_text(text)
        done = self.runner("run", "selftest-basic")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for name, text in foreign.items():
            self.assertEqual((self.out / name).read_text(), text, name)

    def test_a_run_leaves_a_tracked_json_under_a_code_directory(self) -> None:
        tracked = self.clone / "tools" / "test_timings.json"
        self.assertTrue(tracked.exists())
        before = tracked.read_text()
        done = self.runner("run", "selftest-basic", output=self.clone / "tools")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(tracked.read_text(), before)
        self.assertNotIn(" D tools/test_timings.json", _git(self.clone, "status", "--porcelain"))


class DiscoveryIsolationTest(CloneCase):
    """Scenario modules are imported only inside the sandbox (round 4, I1)."""

    def _module(self, marker: Path) -> None:
        (self.clone / "tests" / "scenarios" / "hostile.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")

    def test_a_refused_dirty_run_does_not_import_the_module(self) -> None:
        marker = self.base / "marker-dirty"
        self._module(marker)
        done = self.runner("run", "--module", "tests.scenarios.hostile", module=False)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(marker.exists(), "the host imported a scenario module before refusing")

    def test_a_committed_module_is_imported_inside_the_sandbox(self) -> None:
        marker = self.clone / "marker-committed"  # the real checkout is write-protected inside the sandbox
        self._module(marker)
        _git(self.clone, "add", "-A")
        _git(self.clone, "commit", "-q", "-m", "hostile module")
        done = self.runner("run", "--module", "tests.scenarios.hostile", module=False)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(marker.exists(), "the module wrote to the real checkout during discovery")


class IsolationRefusalTest(unittest.TestCase):
    """Fail closed: unavailable isolation exits 2 and writes nothing; the degraded
    diagnostic writes reports that are not evidence."""

    def setUp(self) -> None:
        self.out = Path(tempfile.mkdtemp(prefix="run-scenarios-out-"))
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.runner = load_runner(REPO_ROOT)
        self.broken = kit.Probe(False, "setup", "no user namespaces here")

    def invoke(self, *args: str) -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch.object(self.runner, "dirty_code_paths", return_value=[]), \
                mock.patch.object(self.runner.kit, "probe_isolation", return_value=self.broken), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = self.runner.main(["run", "--module", SELFTEST, "--output", str(self.out), *args])
        return code, err.getvalue()

    def test_unavailable_isolation_exits_2_and_writes_nothing(self) -> None:
        code, err = self.invoke("selftest-basic")
        self.assertEqual(code, 2)
        self.assertIn("'setup'", err)
        self.assertEqual(list(self.out.glob("*")), [])

    def test_degraded_diagnostic_writes_no_evidence(self) -> None:
        code, _ = self.invoke("--degraded-diagnostic", "selftest-basic")
        self.assertEqual(code, 0)
        self.assertEqual([p.name for p in self.out.iterdir()], ["degraded"], "no <flow_id>.json at the top")
        index = json.loads((self.out / "degraded" / "index.json").read_text())
        self.assertIs(index["evidence"], False)
        self.assertEqual(index["failed_step"], "setup")
        report = json.loads((self.out / "degraded" / "selftest-basic.report.json").read_text())
        self.assertTrue({"flow_id", "head", "status"}.isdisjoint(report))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(self.runner.main(["check", "--output", str(self.out)]), 0)

    def test_a_forged_isolation_label_is_not_what_protects_ingestion(self) -> None:
        # check reads only <flow_id>.json and index.json: a degraded directory has neither
        self.invoke("--degraded-diagnostic", "selftest-basic")
        self.assertFalse((self.out / "index.json").exists())
        self.assertEqual(list(self.out.glob("*.json")), [])

    def test_a_record_is_never_built_for_a_degraded_outcome(self) -> None:
        outcome = kit.Outcome("selftest-basic", "degraded", {"status": "passed"}, "", "0" * 40, True, [], 0.0)
        with self.assertRaises(AssertionError):
            self.runner.build_record(outcome, "r", "2026-10-12T12:00:00Z")


class CheckTest(CloneCase):
    def test_check_accepts_a_fresh_output_and_refuses_tampering(self) -> None:
        self.assertEqual(self.runner("run", "selftest-basic").returncode, 0)
        self.assertEqual(self.runner("check", "--output", str(self.out)).returncode, 0)
        path = self.out / "selftest-basic.json"
        record = json.loads(path.read_text())
        path.write_text(json.dumps({**record, "status": "passed", "ran_at": "2000-01-01T00:00:00Z"}))
        done = self.runner("check", "--output", str(self.out))
        self.assertEqual(done.returncode, 1)
        self.assertIn("index digest", done.stdout)

    def test_check_refuses_a_failed_record_and_a_stale_head(self) -> None:
        self.runner("run", "selftest-fails")
        done = self.runner("check", "--output", str(self.out))
        self.assertEqual(done.returncode, 1)
        self.assertIn("status is 'failed'", done.stdout)
        self.runner("run", "selftest-basic", output=self.out / "again")
        (self.clone / "tools" / "later.py").write_text("x = 1\n")
        _git(self.clone, "add", "-A")
        _git(self.clone, "commit", "-q", "-m", "later")
        stale = self.runner("check", "--output", str(self.out / "again"))
        self.assertEqual(stale.returncode, 1)
        self.assertIn("is not HEAD", stale.stdout)


class DeterminismTest(CloneCase):
    """D9: two runs against one HEAD give the same normalised summary."""

    def summaries(self, *names: str) -> dict:
        folder = Path(tempfile.mkdtemp(prefix="run-scenarios-det-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.runner("run", *names, output=folder)
        return {k: v["summary"] for k, v in self.records(folder).items()}

    def test_two_runs_agree_on_every_normalised_summary(self) -> None:
        first = self.summaries("selftest-basic", "selftest-env")
        second = self.summaries("selftest-basic", "selftest-env")
        self.assertEqual(set(first), {"selftest-basic", "selftest-env"})
        self.assertEqual(first, second)

    def test_an_unnormalised_timestamp_makes_the_determinism_check_fail(self) -> None:
        self.assertNotEqual(self.summaries("selftest-unnormalised"), self.summaries("selftest-unnormalised"))


#: The determinism check every pull request keeps: two cheap catalogue flows and one kill-and-resume variant. The
#: whole-catalogue comparison runs after the merge (``tests/test_catalogue_determinism.py``).
SUBSET_FLOWS = ("scenario-settings-file-effect", "scenario-human-gates-stop-and-resume",
                "scenario-kill-and-resume-run-after")


class SubsetDeterminismTest(CloneCase):
    """D9, a fixed subset of the catalogue: two runs against one HEAD agree on every summary, and every record passes
    the Workflow's real ingestion. A difference these three scenarios expose in the runner or the normaliser still fails before the merge."""

    def subset_run(self) -> dict:
        folder = Path(tempfile.mkdtemp(prefix="run-scenarios-subset-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = self.runner("run", *SUBSET_FLOWS, output=folder, module=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return self.records(folder)

    def test_two_subset_runs_agree_on_every_normalised_summary(self) -> None:
        records = self.subset_run()
        self.assertEqual(set(records), set(SUBSET_FLOWS))
        self.assert_ingested(records)
        first = {k: v["summary"] for k, v in records.items()}
        second = {k: v["summary"] for k, v in self.subset_run().items()}
        self.assertEqual(first, second)


class _GitCase(unittest.TestCase):
    """A scratch git repository with the code directories, for guard and ownership tests that need no namespace."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="run-scenarios-guard-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for name in ("tests/scenarios", "tools", "controller"):
            (self.root / name).mkdir(parents=True)
            (self.root / name / ".keep").write_text("")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
        self.runner = load_runner(REPO_ROOT)

    def record(self, flow_id: str = "selftest-basic") -> str:
        outcome = kit.Outcome(flow_id, "namespace", {"status": "passed"}, "", "0" * 40, True, [], 0.0)
        return self.runner._canonical(self.runner.build_record(outcome, "workflow-controller-scenarios/1",
                                                               "2026-10-12T12:00:00Z"))

    def dirty(self, output: Path) -> list:
        return self.runner.dirty_code_paths(self.root, ignore=output.resolve())


class SymlinkedEvidenceTest(_GitCase):
    """A symlink named like an evidence file stays code, however the output directory is placed (round 5, I2)."""

    def alias(self) -> None:
        (self.root / "tests" / "review-alias.json").write_text("x = 1\n")
        os.symlink("../review-alias.json", self.root / "tests" / "scenarios" / "review_alias.py")

    def test_the_alias_and_its_target_are_dirty_whatever_the_output_placement(self) -> None:
        self.alias()
        links = self.root / "linked"
        os.symlink(self.root / "tests", links)
        for output in (self.root / "tests", self.root, self.root / "tests" / "scenarios" / ".." , links):
            with self.subTest(output=str(output)):
                found = self.dirty(output)
                self.assertIn("tests/scenarios/review_alias.py", found)
                self.assertIn("tests/review-alias.json", found)

    def test_a_symlinked_evidence_name_is_not_exempt(self) -> None:
        out = self.root / "tools" / "out"
        out.mkdir()
        (self.root / "elsewhere.json").write_text(self.record())
        os.symlink(self.root / "elsewhere.json", out / "selftest-basic.json")
        self.assertEqual(self.dirty(out), ["tools/out/selftest-basic.json"])

    def test_a_file_under_a_symlinked_directory_is_not_exempt(self) -> None:
        real = self.root / "tools" / "real"
        real.mkdir()
        (real / "selftest-basic.json").write_text(self.record())
        os.symlink(real, self.root / "tools" / "out")
        self.assertIn("tools/out", " ".join(self.dirty(self.root / "tools" / "out")))

    def test_code_in_an_evidence_named_file_is_not_exempt_but_a_real_record_is(self) -> None:
        out = self.root / "tools" / "out"
        out.mkdir()
        (out / "selftest-basic.json").write_text("import os\n")
        self.assertEqual(self.dirty(out), ["tools/out/selftest-basic.json"])
        (out / "selftest-basic.json").write_text(self.record())
        self.assertEqual(self.dirty(out), [])
        (out / "selftest-basic.json").write_text(self.record("other-flow"))  # a record under another name
        self.assertEqual(self.dirty(out), ["tools/out/selftest-basic.json"])


class OutputOwnershipTest(_GitCase):
    """A run deletes or overwrites only what an earlier run's own index attests (round 5, I3)."""

    def run_with(self, output: Path, ids: list, status: str = "passed", degraded: bool = False) -> int:
        outcomes = [kit.Outcome(i, "namespace", {"status": status}, "", "0" * 40, True, [], 0.0) for i in ids]
        err = io.StringIO()
        with mock.patch.object(self.runner, "dirty_code_paths", return_value=[]), \
                mock.patch.object(self.runner.kit, "probe_isolation", return_value=kit.Probe(True, None, "")), \
                mock.patch.object(self.runner, "selected", return_value=ids), \
                mock.patch.object(self.runner.kit, "run_scenario", side_effect=outcomes), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = self.runner.main(["run", "--output", str(output), *(["--degraded-diagnostic"] if degraded else [])])
        self.err = err.getvalue()
        return code

    def test_a_smaller_run_removes_only_what_the_previous_index_lists(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a", "flow-b"]), 0)
        self.assertEqual(self.run_with(out, ["flow-a"]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["flow-a.json", "index.json"])

    def test_a_file_with_matching_fields_that_the_index_does_not_list_is_preserved(self) -> None:
        out = self.root / "out"
        out.mkdir()
        mine = json.dumps({"flow_id": "my-settings", "log_digest": "0" * 64})
        (out / "my-settings.json").write_text(mine)
        self.assertEqual(self.run_with(out, ["flow-a"]), 0)
        self.assertEqual((out / "my-settings.json").read_text(), mine)

    def test_a_record_from_another_run_is_preserved(self) -> None:
        other, out = self.root / "other", self.root / "out"
        self.assertEqual(self.run_with(other, ["flow-x"]), 0)
        self.assertEqual(self.run_with(out, ["flow-a", "flow-b"]), 0)
        text = (other / "flow-x.json").read_text()
        (out / "flow-x.json").write_text(text)  # a valid record the out index does not list
        self.assertEqual(self.run_with(out, ["flow-a"]), 0)
        self.assertEqual((out / "flow-x.json").read_text(), text)
        self.assertFalse((out / "flow-b.json").exists())

    def test_a_listed_record_edited_since_is_preserved(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a", "flow-b"]), 0)
        (out / "flow-b.json").write_text("{}")
        self.assertEqual(self.run_with(out, ["flow-a"]), 0)
        self.assertEqual((out / "flow-b.json").read_text(), "{}")

    def test_a_foreign_file_that_a_run_would_overwrite_is_refused_untouched(self) -> None:
        out = self.root / "out"
        out.mkdir()
        (out / "index.json").write_text('{"mine": true}')
        (out / "flow-a.json").write_text("{}")
        self.assertEqual(self.run_with(out, ["flow-a"]), 2)
        self.assertIn("did not write", self.err)
        self.assertEqual((out / "index.json").read_text(), '{"mine": true}')
        self.assertEqual((out / "flow-a.json").read_text(), "{}")

    def test_a_symlink_is_never_followed_or_removed(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a", "flow-b"]), 0)
        target = self.root / "precious.json"
        target.write_text("precious")
        (out / "flow-b.json").unlink()
        os.symlink(target, out / "flow-b.json")
        self.assertEqual(self.run_with(out, ["flow-a"]), 0)
        self.assertTrue((out / "flow-b.json").is_symlink())
        self.assertEqual(target.read_text(), "precious")

    def test_a_degraded_run_preserves_a_foreign_report_and_index(self) -> None:
        out = self.root / "out"
        (out / "degraded").mkdir(parents=True)
        (out / "degraded" / "flow-a.report.json").write_text("mine")
        (out / "degraded" / "index.json").write_text('{"mine": true}')
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 2)
        self.assertIn("did not write", self.err)
        self.assertEqual((out / "degraded" / "flow-a.report.json").read_text(), "mine")
        self.assertEqual((out / "degraded" / "index.json").read_text(), '{"mine": true}')

    def _foreign_degraded(self, out: Path, report: "str | None" = None, index: "dict | None" = None) -> None:
        """Files with exactly the runner's keys that no previous index attests."""
        (out / "degraded").mkdir(parents=True)
        shaped = {"scenario": "flow-a", "passed": True, "observations": [], "message": "mine", "problems": []}
        (out / "degraded" / "flow-a.report.json").write_text(report or self.runner._canonical(shaped))
        if index is not None:
            (out / "degraded" / "index.json").write_text(self.runner._canonical(index))

    def test_a_degraded_report_shaped_like_ours_but_in_no_index_is_preserved(self) -> None:
        out = self.root / "out"
        self._foreign_degraded(out)
        before = (out / "degraded" / "flow-a.report.json").read_text()
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 2)
        self.assertIn("did not write", self.err)
        self.assertEqual((out / "degraded" / "flow-a.report.json").read_text(), before)
        self.assertFalse((out / "degraded" / "index.json").exists())

    def test_a_foreign_degraded_index_with_our_keys_but_invalid_values_is_preserved(self) -> None:
        out = self.root / "out"
        bad = {"evidence": True, "isolation": "x", "failed_step": 3, "detail": None, "reports": ["flow-a"]}
        self._foreign_degraded(out, index=bad)
        before = (out / "degraded" / "index.json").read_text()
        self.assertEqual(self.run_with(out, ["flow-b"], degraded=True), 2)
        self.assertEqual((out / "degraded" / "index.json").read_text(), before)
        self.assertFalse((out / "degraded" / "flow-b.report.json").exists())

    def test_a_degraded_report_edited_since_the_previous_run_is_preserved(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 0)
        report = out / "degraded" / "flow-a.report.json"
        edited = json.loads(report.read_text())
        edited["message"] = "annotated by hand"
        report.write_text(self.runner._canonical(edited))
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 2)
        self.assertEqual(json.loads(report.read_text())["message"], "annotated by hand")

    def test_a_degraded_report_copied_from_another_run_is_preserved(self) -> None:
        first, second = self.root / "first", self.root / "second"
        self.assertEqual(self.run_with(first, ["flow-a"], status="failed", degraded=True), 1)
        self.assertEqual(self.run_with(second, ["flow-a", "flow-b"], degraded=True), 0)
        copied = (first / "degraded" / "flow-a.report.json").read_text()
        (second / "degraded" / "flow-a.report.json").write_text(copied)
        self.assertEqual(self.run_with(second, ["flow-a", "flow-b"], degraded=True), 2)
        self.assertEqual((second / "degraded" / "flow-a.report.json").read_text(), copied)

    def test_a_smaller_degraded_run_removes_only_what_the_previous_index_lists(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a", "flow-b"], degraded=True), 0)
        (out / "degraded" / "mine.txt").write_text("mine")
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 0)
        self.assertEqual(sorted(p.name for p in (out / "degraded").iterdir()),
                         ["flow-a.report.json", "index.json", "mine.txt"])

    def test_a_degraded_run_never_follows_a_linked_destination(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 0)
        target = self.root / "precious.json"
        target.write_text("precious")
        for name in ("flow-a.report.json", "index.json"):
            (out / "degraded" / name).unlink()
        os.symlink(target, out / "degraded" / "flow-a.report.json")
        os.link(target, out / "degraded" / "index.json")
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 2)
        self.assertEqual(target.read_text(), "precious")

    def test_a_degraded_run_refuses_a_linked_degraded_directory(self) -> None:
        out, elsewhere = self.root / "out", self.root / "elsewhere"
        out.mkdir()
        elsewhere.mkdir()
        os.symlink(elsewhere, out / "degraded")
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 2)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_degraded_run_replaces_its_own_earlier_output(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a"], degraded=True), 0)
        self.assertEqual(self.run_with(out, ["flow-a", "flow-b"], degraded=True), 0)
        self.assertTrue((out / "degraded" / "flow-b.report.json").is_file())

    def test_a_hard_linked_namespace_index_is_refused_and_its_other_name_keeps_its_content(self) -> None:
        out = self.root / "out"
        self.assertEqual(self.run_with(out, ["flow-a"]), 0)
        alias = self.root / "alias.json"
        os.link(out / "index.json", alias)
        before = alias.read_text()
        self.assertEqual(self.run_with(out, ["flow-a"]), 2)
        self.assertEqual(alias.read_text(), before)

    def test_publication_never_writes_through_the_old_inode(self) -> None:
        target = self.root / "shared.txt"
        target.write_text("shared")
        os.link(target, self.root / "dest.txt")
        self.runner._publish(self.root / "dest.txt", "new")
        self.assertEqual(target.read_text(), "shared")
        self.assertEqual((self.root / "dest.txt").read_text(), "new")


if __name__ == "__main__":
    unittest.main()
