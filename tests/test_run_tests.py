"""End-to-end tests for ``tools/run_tests.py`` (``workflow-controller-adaptive-test-sharding`` CP4).

Each test builds a throwaway repository with its own ``tests/`` package and
managed conformance workflow, and runs the real CLI against it with
``--repo-root``, so the real suite is never re-run inside itself. The
synthetic tests pass, fail, crash, hang, leak a process, or write a probe of
their environment into ``$SHARD_PROBE_DIR``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

RUN_TESTS = fixtures.REPO_ROOT / "tools" / "run_tests.py"
MARKER = "WORKFLOW_CONTROLLER_TEST_SHARD"

MODULES = {
    "test_pass": """
        import unittest

        class PassTest(unittest.TestCase):
            def test_a(self): pass
            def test_b(self): pass

        class MorePassTest(unittest.TestCase):
            def test_c(self): pass
    """,
    "test_fail": """
        import unittest

        class FailTest(unittest.TestCase):
            def test_fails(self):
                self.assertEqual(1, 2, "one is not two")
    """,
    "test_env": """
        import json
        import os
        import signal
        import subprocess
        import unittest

        class EnvTest(unittest.TestCase):
            def test_probe(self):
                status = subprocess.run(["cat", "/proc/self/status"], capture_output=True,
                                        text=True, check=True).stdout
                ignored = int(next(line for line in status.splitlines()
                                   if line.startswith("SigIgn:")).split()[1], 16)
                doc = {name: os.environ.get(name) for name in (
                    "TMPDIR", "XDG_STATE_HOME", "PIP_CACHE_DIR", "WORKFLOW_CONTROLLER_TEST_SHARD",
                    "HOME", "PATH", "PYTHONPATH")}
                doc["sigint_default"] = signal.getsignal(signal.SIGINT) == signal.SIG_DFL
                doc["child_ignores_sigint"] = bool(ignored & (1 << (signal.SIGINT - 1)))
                doc["cwd"] = os.getcwd()
                path = os.path.join(os.environ["SHARD_PROBE_DIR"], "env.json")
                with open(path, "w") as handle:
                    json.dump(doc, handle)
    """,
    "test_crash": """
        import os
        import unittest

        class CrashTest(unittest.TestCase):
            def test_a_crashes(self):
                os._exit(3)

            def test_b_never_runs(self): pass
    """,
    "test_hang": """
        import os
        import subprocess
        import time
        import unittest

        class HangTest(unittest.TestCase):
            def test_hangs(self):
                stubborn = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 600"])
                probe = os.environ["SHARD_PROBE_DIR"]
                with open(os.path.join(probe, "hang.pids"), "w") as handle:
                    handle.write(f"{os.getpid()} {stubborn.pid}")
                time.sleep(600)
    """,
    "test_timed": """
        import os
        import time
        import unittest

        def span(name):
            start = time.monotonic()
            time.sleep(0.5)
            with open(os.path.join(os.environ["SHARD_PROBE_DIR"], f"{name}.span"), "w") as h:
                h.write(f"{start} {time.monotonic()}")

        class FirstTest(unittest.TestCase):
            def test_span(self): span("first")

        class SecondTest(unittest.TestCase):
            def test_span(self): span("second")

        class AloneTest(unittest.TestCase):
            def test_span(self): span("alone")
    """,
    "test_leak": """
        import os
        import subprocess
        import unittest

        class LeakTest(unittest.TestCase):
            def test_leaks(self):
                process = subprocess.Popen(["sleep", "300"], start_new_session=True)
                with open(os.path.join(os.environ["SHARD_PROBE_DIR"], "leak.pid"), "w") as h:
                    h.write(str(process.pid))
                process.returncode = 0  # no ResourceWarning: the leak is the point
    """,
}

SUITE = "fake_suite_test.py"
WORKFLOW = f"""\
name: workflow-conformance
jobs:
  suites:
    steps:
      - name: {SUITE}
        run: python3 {SUITE}
"""


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat[stat.rindex(")") + 2] != "Z"


def _wait_dead(pid: int, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


class SyntheticRepo:
    """A throwaway repository the runner can plan and execute."""

    def __init__(self, test_case: unittest.TestCase) -> None:
        tmp = tempfile.TemporaryDirectory()
        test_case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "tests" / "__init__.py").write_text("")
        for name, source in MODULES.items():
            (self.root / "tests" / f"{name}.py").write_text(textwrap.dedent(source))
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / SUITE).write_text(
            "import os, sys\nprint('fake suite ran')\n"
            "sys.exit(int(os.environ.get('FAKE_SUITE_STATUS', '0')))\n")
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / ".github" / "workflows" / "workflow-conformance.yml").write_text(WORKFLOW)
        self.probe = Path(tmp.name) / "probe"
        self.probe.mkdir()
        self.cache = Path(tmp.name) / "cache"
        self.results_parent = Path(tmp.name) / "results"
        self.runs = 0
        self.env = {**os.environ, "XDG_CACHE_HOME": str(self.cache),
                    "SHARD_PROBE_DIR": str(self.probe)}
        self.env.pop("PIP_CACHE_DIR", None)
        self.env.pop("GITHUB_STEP_SUMMARY", None)

    def results(self) -> Path:
        self.runs += 1
        return self.results_parent / f"run-{self.runs}"

    def command(self, *args: str) -> list[str]:
        return [sys.executable, str(RUN_TESTS), *args, "--repo-root", str(self.root)]

    def run(self, *args: str, env: dict | None = None, timeout: float = 120,
            **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(self.command(*args), env={**self.env, **(env or {})},
                              capture_output=True, text=True, timeout=timeout, **kwargs)

    def run_selection(self, *names: str, extra=(), **kwargs):
        results = self.results()
        completed = self.run(*names, "--results-dir", str(results), *extra, **kwargs)
        return completed, results

    def record(self, results: Path, index: int) -> dict:
        return json.loads((results / f"shard-{index}.json").read_text())


class RunnerVerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SyntheticRepo(self)

    def test_a_passing_selection_exits_0_with_exact_coverage(self) -> None:
        completed, results = self.repo.run_selection("tests.test_pass", f"conformance:{SUITE}")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("# Test run: PASS (exit 0)", completed.stdout)
        plan = json.loads((results / "plan.json").read_text())
        self.assertEqual(plan["selected_ids"], [
            "tests.test_pass.MorePassTest.test_c", "tests.test_pass.PassTest.test_a",
            "tests.test_pass.PassTest.test_b", f"conformance:{SUITE}"])
        reported = [entry["id"] for index in range(plan["shard_count"])
                    for entry in self.repo.record(results, index)["tests"]]
        self.assertEqual(sorted(reported), sorted(plan["selected_ids"]))
        self.assertEqual((results / "SUMMARY.md").read_text(),
                         completed.stdout[completed.stdout.index("# Test run"):])
        for index in range(plan["shard_count"]):
            argv = self.repo.record(results, index)["argv"]
            self.assertIn("exec-shard", argv)
            self.assertNotIn("--count", argv)
        self.assertIn("fake suite ran", "".join(
            (results / f"shard-{i}.log").read_text() for i in range(plan["shard_count"])))

    def test_a_failure_exits_1_names_the_test_and_replays_exactly(self) -> None:
        completed, results = self.repo.run_selection("tests.test_pass", "tests.test_fail",
                                                     extra=["--shards", "2"])
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        plan = json.loads((results / "plan.json").read_text())
        index = next(shard["index"] for shard in plan["shards"]
                     if "tests.test_fail.FailTest.test_fails" in shard["test_ids"])
        out = completed.stdout
        self.assertIn("### `tests.test_fail.FailTest.test_fails`", out)
        self.assertIn(f"- shard {index}, outcome `fail`", out)
        self.assertIn(f"- log: `{results / f'shard-{index}.log'}`", out)
        self.assertIn("`python3 -m unittest tests.test_fail.FailTest.test_fails`", out)
        replay = f"python3 tools/run_tests.py --replay {results / 'plan.json'} --shard {index}"
        self.assertIn(f"`{replay}`", out)
        self.assertIn("one is not two", out)

        replayed, replay_results = self.repo.run_selection(
            extra=["--replay", str(results / "plan.json"), "--shard", str(index)])
        self.assertEqual(replayed.returncode, 1, replayed.stdout + replayed.stderr)
        self.assertEqual(json.loads((replay_results / "plan.json").read_text()), plan)
        self.assertEqual([e["id"] for e in self.repo.record(replay_results, index)["tests"]],
                         [e["id"] for e in self.repo.record(results, index)["tests"]])
        self.assertFalse((replay_results / f"shard-{1 - index}.json").exists())

    def test_a_failing_conformance_suite_fails_with_its_own_reproduction(self) -> None:
        completed, _ = self.repo.run_selection(f"conformance:{SUITE}",
                                               env={"FAKE_SUITE_STATUS": "4"})
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn(f"`cd scripts && python3 {SUITE}`", completed.stdout)
        self.assertIn("exited with status 4", completed.stdout)

    def test_a_crashed_shard_exits_2_and_names_what_did_not_run(self) -> None:
        completed, results = self.repo.run_selection("tests.test_crash", "tests.test_pass")
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertIn("CRASHED (no result record)", completed.stdout)
        self.assertIn("- `tests.test_crash.CrashTest.test_a_crashes`", completed.stdout)
        self.assertIn("- `tests.test_crash.CrashTest.test_b_never_runs`", completed.stdout)

    def test_the_local_timing_cache_learns_only_from_passing_atoms(self) -> None:
        completed, _ = self.repo.run_selection("tests.test_pass", "tests.test_fail")
        self.assertEqual(completed.returncode, 1)
        cache = self.repo.cache / "workflow-controller-tests" / "timings-local.json"
        profile = json.loads(cache.read_text())
        self.assertEqual(profile["profile"], "local")
        self.assertEqual(sorted(profile["atoms"]), ["tests.test_pass.MorePassTest",
                                                    "tests.test_pass.PassTest"])

    def test_a_selection_that_matches_nothing_refuses(self) -> None:
        completed, _ = self.repo.run_selection("tests.test_nothing_here")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("no test matches 'tests.test_nothing_here'", completed.stderr)

    def test_plan_only_prints_the_plan_and_runs_nothing(self) -> None:
        completed = self.repo.run("--plan-only", "tests.test_pass", "--shards", "2")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("3 selected tests in 2 shards", completed.stdout)
        self.assertIn("EXCLUSIVE_ATOMS: 0 registered, 0 in this plan's exclusive shard",
                      completed.stdout)
        self.assertFalse(self.repo.results_parent.exists())


class EnvironmentTest(unittest.TestCase):
    """The shard environment, SIGINT disposition and ``--serial``."""

    def setUp(self) -> None:
        self.repo = SyntheticRepo(self)

    def probe(self, *extra: str, ignore_sigint: bool = False):
        def ignore() -> None:
            signal.signal(signal.SIGINT, signal.SIG_IGN)

        completed, results = self.repo.run_selection(
            "tests.test_env", extra=extra, preexec_fn=ignore if ignore_sigint else None)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads((self.repo.probe / "env.json").read_text()), results

    def test_the_shard_environment_is_shaped_exactly(self) -> None:
        env, results = self.probe()
        record = self.repo.record(results, 0)
        self.assertEqual(env["TMPDIR"], str(results / "shard-0" / "tmp"))
        self.assertEqual(env["XDG_STATE_HOME"], str(results / "shard-0" / "state"))
        self.assertIsNone(env["PIP_CACHE_DIR"])
        self.assertRegex(env[MARKER], r"^\d{8}T\d{6}Z-[0-9a-f]{8}/0/\d+$")
        for name in ("HOME", "PATH", "PYTHONPATH"):
            self.assertEqual(env[name], self.repo.env.get(name), name)
        self.assertEqual(env["cwd"], str(self.repo.root))
        self.assertEqual(record["exit_status"], 0)

    def test_an_isolated_pip_cache_is_per_shard(self) -> None:
        env, results = self.probe("--isolated-pip-cache")
        self.assertEqual(env["PIP_CACHE_DIR"], str(results / "pip-cache-0"))

    def test_serial_gets_the_same_shaping_as_shard_0_of_1(self) -> None:
        env, results = self.probe("--serial", "tests.test_pass")
        plan = json.loads((results / "plan.json").read_text())
        self.assertEqual(plan["shard_count"], 1)
        self.assertEqual(plan["parameters"]["shards"], 1)
        self.assertEqual(env["TMPDIR"], str(results / "shard-0" / "tmp"))
        self.assertEqual(env["XDG_STATE_HOME"], str(results / "shard-0" / "state"))
        self.assertRegex(env[MARKER], r"/0/\d+$")

    def test_shards_have_sigint_at_default_even_when_the_runner_ignores_it(self) -> None:
        env, _ = self.probe(ignore_sigint=True)
        self.assertTrue(env["sigint_default"])
        self.assertFalse(env["child_ignores_sigint"])


class ProcessHygieneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SyntheticRepo(self)

    def test_a_leaked_process_is_reported_and_killed_without_failing(self) -> None:
        completed, results = self.repo.run_selection("tests.test_leak")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        pid = int((self.repo.probe / "leak.pid").read_text())
        self.assertTrue(_wait_dead(pid), f"leaked pid {pid} is still alive")
        leaks = self.repo.record(results, 0)["leaked_processes"]
        self.assertEqual([(leak["pid"], leak["argv"]) for leak in leaks],
                         [(pid, ["sleep", "300"])])
        self.assertIn("## Leaked processes (warning, killed)", completed.stdout)
        self.assertIn(f"pid {pid}", completed.stdout)

    def test_ctrl_c_interrupts_every_shard_and_leaves_nothing_behind(self) -> None:
        results = self.repo.results()
        runner = subprocess.Popen(
            self.repo.command("tests.test_hang", "tests.test_pass", "--shards", "2",
                              "--results-dir", str(results)),
            env=self.repo.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        self.addCleanup(lambda: runner.poll() is None and os.killpg(runner.pid, signal.SIGKILL))
        pids_file = self.repo.probe / "hang.pids"
        deadline = time.monotonic() + 60
        while not pids_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
        pids = [int(pid) for pid in pids_file.read_text().split()]
        os.kill(runner.pid, signal.SIGINT)
        output, _ = runner.communicate(timeout=60)
        self.assertEqual(runner.returncode, 130, output)
        self.assertIn("INTERRUPTED", output)
        self.assertIn("tests.test_hang.HangTest.test_hangs", output)
        for pid in pids:
            self.assertTrue(_wait_dead(pid), f"shard process {pid} survived Ctrl-C")


class NestedRunTest(unittest.TestCase):
    """A run started inside a shard (as these very tests do) inherits that
    shard's marker; its executor's leak scan must never find, and kill, the
    shard's own processes."""

    def test_a_nested_exec_shard_leaves_its_ancestors_alone(self) -> None:
        repo = SyntheticRepo(self)
        results = repo.results()
        outer = subprocess.Popen(["sleep", "300"], env={**repo.env, MARKER: "outer-run/0"},
                                 start_new_session=True)
        self.addCleanup(lambda: outer.poll() is None and outer.kill())
        plan = repo.run("plan", "tests.test_pass", "--profile", "ci", "--shards", "1",
                        "--output", str(results / "plan.json"))
        self.assertEqual(plan.returncode, 0, plan.stderr)
        completed = repo.run("exec-shard", "--plan", str(results / "plan.json"), "--shard", "0",
                             "--results-dir", str(results), env={MARKER: "outer-run/0"})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIsNone(outer.poll(), "the nested executor killed a process of its parent shard")
        self.assertEqual(repo.record(results, 0)["leaked_processes"], [])


class ExclusivePhaseTest(unittest.TestCase):
    """CP5: an atom registered in ``EXCLUSIVE_ATOMS`` runs on the final
    exclusive shard, which starts only after every other shard has ended."""

    def test_the_exclusive_shard_runs_alone_after_the_parallel_shards(self) -> None:
        repo = SyntheticRepo(self)
        results = repo.results()
        alone = "tests.test_timed.AloneTest"
        driver = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(RUN_TESTS.parent)!r})
            import test_shards
            test_shards.EXCLUSIVE_ATOMS[{alone!r}] = "the CP5 exclusive-phase test"
            import run_tests
            sys.exit(run_tests.main(sys.argv[1:]))
        """)
        completed = subprocess.run(
            [sys.executable, "-c", driver, "tests.test_timed", "--shards", "2", "--results-dir",
             str(results), "--repo-root", str(repo.root)],
            env=repo.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        plan = json.loads((results / "plan.json").read_text())
        self.assertEqual([(s["atoms"], s.get("exclusive")) for s in plan["shards"]], [
            (["tests.test_timed.FirstTest"], None), (["tests.test_timed.SecondTest"], None),
            ([alone], True)])
        spans = {name: [float(v) for v in (repo.probe / f"{name}.span").read_text().split()]
                 for name in ("first", "second", "alone")}
        self.assertGreaterEqual(spans["alone"][0], max(spans["first"][1], spans["second"][1]),
                                spans)
        self.assertIn("EXCLUSIVE_ATOMS: 1 registered, 1 in this plan's exclusive shard",
                      completed.stdout)
        self.assertIn("| 2 (exclusive) | PASS | 1 |", completed.stdout)


class BuildingBlocksTest(unittest.TestCase):
    """``plan``, ``exec-shard``'s two forms, and ``aggregate``."""

    def setUp(self) -> None:
        self.repo = SyntheticRepo(self)
        self.results = self.repo.results()

    def plan(self, root: Path | None = None) -> dict:
        repo = self.repo
        command = [sys.executable, str(RUN_TESTS), "plan", "tests.test_pass", "--profile", "ci",
                   "--max-shards", "4", "--output", str(self.results / "plan.json"),
                   "--repo-root", str(root or repo.root)]
        completed = subprocess.run(command, env=repo.env, capture_output=True, text=True,
                                   timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def exec_shard(self, *args: str) -> subprocess.CompletedProcess:
        return self.repo.run("exec-shard", *args, "--shard", "0",
                             "--results-dir", str(self.results))

    def test_the_plan_digest_does_not_depend_on_the_checkout_path(self) -> None:
        other = SyntheticRepo(self)
        self.assertNotEqual(self.repo.root, other.root)
        self.assertEqual(self.plan()["plan_digest"], self.plan(other.root)["plan_digest"])

    def test_exec_shard_takes_exactly_one_form_of_the_plan(self) -> None:
        summary = self.plan()
        plan_path = str(self.results / "plan.json")
        both = self.exec_shard("--plan", plan_path, "--expect-digest", summary["plan_digest"])
        self.assertEqual(both.returncode, 2)
        self.assertIn("not both", both.stderr)
        neither = self.exec_shard()
        self.assertEqual(neither.returncode, 2)
        self.assertIn("needs --plan", neither.stderr)
        mismatch = self.exec_shard("tests.test_pass", "--profile", "ci", "--max-shards", "4",
                                   "--expect-digest", "0" * 64)
        self.assertEqual(mismatch.returncode, 2)
        self.assertIn("is not the expected", mismatch.stderr)
        self.assertFalse((self.results / "shard-0.json").exists())

    def test_the_planning_inputs_form_runs_and_aggregates(self) -> None:
        summary = self.plan()
        for index in summary["shards"]:
            completed = self.repo.run(
                "exec-shard", "tests.test_pass", "--profile", "ci", "--max-shards", "4",
                "--expect-digest", summary["plan_digest"], "--shard", str(index),
                "--results-dir", str(self.results))
            self.assertEqual(completed.returncode, 0, completed.stderr)
        step_summary = self.results / "step-summary.md"
        aggregated = self.repo.run("aggregate", "--plan", str(self.results / "plan.json"),
                                   "--results-dir", str(self.results),
                                   env={"GITHUB_STEP_SUMMARY": str(step_summary)})
        self.assertEqual(aggregated.returncode, 0, aggregated.stdout + aggregated.stderr)
        self.assertIn("# Test run: PASS (exit 0)", step_summary.read_text())

    def test_aggregate_refuses_a_result_from_another_plan(self) -> None:
        self.plan()
        plan_path = self.results / "plan.json"
        completed = self.exec_shard("--plan", str(plan_path))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record_path = self.results / "shard-0.json"
        record = json.loads(record_path.read_text())
        record["plan_digest"] = "e" * 64
        record_path.write_text(json.dumps(record))
        aggregated = self.repo.run("aggregate", "--plan", str(plan_path),
                                   "--results-dir", str(self.results))
        self.assertEqual(aggregated.returncode, 2)
        self.assertIn("plan_digest", aggregated.stderr)

    def test_plan_hands_its_shards_and_digest_to_github_outputs(self) -> None:
        self.results.mkdir(parents=True)
        outputs = self.results / "github-output"
        outputs.write_text("earlier=kept\n")
        completed = self.repo.run("plan", "tests.test_pass", "--profile", "ci", "--shards", "2",
                                  "--github-output", env={"GITHUB_OUTPUT": str(outputs)})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(outputs.read_text(),
                         "earlier=kept\nshards=[0,1]\ncount=2\n"
                         f"digest={summary['plan_digest']}\n")
        unset = {key: value for key, value in self.repo.env.items() if key != "GITHUB_OUTPUT"}
        missing = subprocess.run(self.repo.command("plan", "tests.test_pass", "--github-output"),
                                 env=unset, capture_output=True, text=True, timeout=60)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("$GITHUB_OUTPUT", missing.stderr)

    def test_aggregate_without_a_plan_refuses_and_says_why(self) -> None:
        self.results.mkdir(parents=True)
        step_summary = self.results / "step-summary.md"
        aggregated = self.repo.run("aggregate", "--plan", str(self.results / "plan.json"),
                                   "--results-dir", str(self.results),
                                   env={"GITHUB_STEP_SUMMARY": str(step_summary)})
        self.assertEqual(aggregated.returncode, 2)
        self.assertIn("there is no plan", aggregated.stderr)
        self.assertIn("nothing can pass", aggregated.stderr)

    def test_aggregate_with_a_missing_shard_result_does_not_pass(self) -> None:
        # A tests job that never started leaves no result artifact.
        summary = self.plan()
        self.assertGreater(len(summary["shards"]), 1)
        completed = self.exec_shard("--plan", str(self.results / "plan.json"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        aggregated = self.repo.run("aggregate", "--plan", str(self.results / "plan.json"),
                                   "--results-dir", str(self.results))
        self.assertEqual(aggregated.returncode, 2, aggregated.stdout)
        self.assertIn("no result record", aggregated.stdout)

    def test_timings_merge_folds_a_results_directory_into_a_profile(self) -> None:
        completed, results = self.repo.run_selection("tests.test_pass", "tests.test_fail")
        self.assertEqual(completed.returncode, 1)
        into = self.results / "timings.json"
        merged = self.repo.run("timings", "merge", "--into", str(into), "--profile-name",
                               "seed-local", str(results))
        self.assertEqual(merged.returncode, 0, merged.stderr)
        profile = json.loads(into.read_text())
        self.assertEqual(profile["profile"], "seed-local")
        self.assertEqual(sorted(profile["atoms"]), ["tests.test_pass.MorePassTest",
                                                    "tests.test_pass.PassTest"])
        plan = json.loads((results / "plan.json").read_text())
        self.assertEqual(profile["updated_from"], [plan["plan_digest"]])


if __name__ == "__main__":
    unittest.main()
