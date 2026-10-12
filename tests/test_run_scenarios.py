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
import subprocess
import sys
import tempfile
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
        _git(cls.clone, "add", "-A")
        _git(cls.clone, "commit", "-q", "--allow-empty", "-m", "test: working tree code")
        cls.head = _git(cls.clone, "rev-parse", "HEAD").strip()

    def setUp(self) -> None:
        self.out = Path(tempfile.mkdtemp(prefix="run-scenarios-out-"))
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        _git(self.clone, "reset", "-q", "--hard", self.head)
        _git(self.clone, "clean", "-qfd", "--", *kit.CODE_PATHS)

    def runner(self, *args: str, output: "Path | None" = None, module: bool = True):
        argv = [sys.executable, "-B", str(self.clone / "tools" / "run_scenarios.py"), *args]
        if args[0] == "run":
            argv += ["--output", str(output or self.out)] + (["--module", SELFTEST] if module else [])
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(argv, cwd=self.clone, env=env, capture_output=True, text=True)

    def records(self, output: "Path | None" = None) -> dict:
        folder = output or self.out
        return {p.stem: json.loads(p.read_text()) for p in sorted(folder.glob("*.json")) if p.name != "index.json"}


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
        scripts = str(self.clone / "scripts")
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        import workflow_gate_policy as gate
        import workflow_state as ws
        import workflow_fingerprint as fingerprint
        state = json.loads((self.clone / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        item = state["work_items"][WORK_ITEM]
        records = self.records()
        self.assertEqual(len(records), 3)
        for flow_id, record in records.items():
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

    def test_the_output_directory_inside_the_tree_is_not_code(self) -> None:
        inside = self.clone / "tools" / "evidence-out"
        done = self.runner("run", "selftest-basic", output=inside)
        self.assertEqual(done.returncode, 0, done.stderr)
        again = self.runner("run", "selftest-basic", output=inside)
        self.assertEqual(again.returncode, 0, again.stderr)


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


class CatalogueDeterminismTest(CloneCase):
    """D9, whole catalogue: two complete runs against one HEAD agree on every summary."""

    def complete_run(self) -> dict:
        folder = Path(tempfile.mkdtemp(prefix="run-scenarios-cat-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = self.runner("run", output=folder, module=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return {k: v["summary"] for k, v in self.records(folder).items()}

    def test_two_complete_runs_agree_on_every_normalised_summary(self) -> None:
        first = self.complete_run()
        second = self.complete_run()
        self.assertGreaterEqual(len(first), 15)
        self.assertFalse([name for name in first if name.startswith("selftest")])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
