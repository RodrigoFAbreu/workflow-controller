"""Tests for ``controller.workflow_contract``
(``docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md``, Design B,
CP2).

The query tests run the real vendored Workflow 2.6.0 scripts in disposable
Git repositories, seeded through Workflow's own writers and its real plan
bundle generator (a 2.5.1-created item is seeded under the vendored 2.5.1
tree and then moved to 2.6.0, as Workflow Manager's ``update`` would). No
Workflow output is faked, except in the tests that replace the execution
step through the private runner hook (``workflow_contract._execute_query``):
the read, the digest check and the private copy still run before it.
"""

from __future__ import annotations

import errno
import importlib.util
import json
import marshal
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, managed_repo, runtime, workflow_contract  # noqa: E402
from controller.errors import (  # noqa: E402
    ControllerError,
    RuntimeContainmentError,
    UnsupportedWorkflowVersionError,
    WorkflowQueryError,
)
from tests import fixtures  # noqa: E402

CONTRACT = workflow_contract.contract_for("2.6.0")
WORK_ITEM_ID = "demo-item"

def _git(root: Path, *args: str) -> str:
    return fixtures.run(["git", *args], cwd=root).stdout


def _seed_target(root: Path, release: str, stage: str, **kwargs) -> Path:
    """:func:`tests.fixtures.seed_workflow_item` for :data:`WORK_ITEM_ID`."""
    return fixtures.seed_workflow_item(root, release, stage, work_item_id=WORK_ITEM_ID, **kwargs)


def _state(root: Path) -> dict:
    return json.loads((root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").read_text())


def _write_state(root: Path, state: dict) -> None:
    (root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(json.dumps(state, indent=2) + "\n")


def _entry(root: Path) -> dict:
    return _state(root)["work_items"][WORK_ITEM_ID]


def _pycache_dirs(root: Path) -> list[Path]:
    return sorted(root.rglob("__pycache__"))


#: A tracked file of every seeded target that is unchanged since the work
#: item's base, so a ``git diff`` against the base finds it stat-dirty but
#: equal and rewrites the index (firing ``post-index-change``).
UNCHANGED_TRACKED = ".claude/commands/milestone-plan.md"


def _stat_dirty(root: Path, *rel_paths: str) -> None:
    """Move each path's mtime on, so the next ``git diff`` must re-hash it:
    ``UNCHANGED_TRACKED`` by default."""
    for rel_path in rel_paths or (UNCHANGED_TRACKED,):
        path = root / rel_path
        stat_result = path.stat()
        os.utime(path, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 10 ** 10))


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every regular file under ``root``, ``.git`` included: bytes and mtime."""
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()}


class _SeededTargets(unittest.TestCase):
    """Seeds each fixture repository once per class, under a class-owned
    scratch directory; :meth:`target` hands each test its own copy."""

    #: ``name -> (release, stage, keyword arguments of _seed_target)``.
    SEEDS: dict[str, tuple] = {}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        scratch = tempfile.TemporaryDirectory(prefix="workflow-contract-test-")
        cls.addClassCleanup(scratch.cleanup)
        cls._scratch = Path(scratch.name)
        cls._seeded = {
            name: _seed_target(cls._scratch / "seeds" / name, release, stage, **kwargs)
            for name, (release, stage, kwargs) in cls.SEEDS.items()
        }

    def target(self, name: str) -> Path:
        copy = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=self._scratch))
        root = copy / "target"
        shutil.copytree(self._seeded[name], root, symlinks=True)
        return root


class _PrivateDirSpy:
    """A private-runner-hook wrapper that runs the real execution step and
    records, at execution time, the argv, the environment, and the entries
    and bytes of the private directory's scripts directory (the query's
    ``sys.path[0]``)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._real = workflow_contract._execute_query

    def __call__(self, argv, *, cwd, env, timeout):
        scripts_dir = Path(argv[4]).parent
        self.calls.append({
            "argv": list(argv), "cwd": cwd, "env": dict(env), "scripts_dir": scripts_dir,
            "private_dir": scripts_dir.parent, "private_entries": sorted(os.listdir(scripts_dir.parent)),
            "entries": sorted(os.listdir(scripts_dir)),
            "bytes": {name: (scripts_dir / name).read_bytes() for name in os.listdir(scripts_dir)},
        })
        completed = self._real(argv, cwd=cwd, env=env, timeout=timeout)
        self.calls[-1]["entries_after"] = sorted(os.listdir(scripts_dir))
        return completed


# ---------------------------------------------------------------------------
# The contract table.
# ---------------------------------------------------------------------------


class ContractTableTest(unittest.TestCase):

    def test_the_table_names_exactly_2_5_1_and_2_6_0(self) -> None:
        self.assertEqual(set(workflow_contract.RELEASE_CONTRACTS), {"2.5.1", "2.6.0"})
        for release, contract in workflow_contract.RELEASE_CONTRACTS.items():
            self.assertEqual(contract.release, release)

    def test_every_admitted_release_has_a_contract(self) -> None:
        self.assertLessEqual(managed_repo.VALIDATED_WORKFLOW_RELEASES, set(workflow_contract.RELEASE_CONTRACTS))

    def test_2_5_1_keeps_the_controller_rules_and_runs_no_query(self) -> None:
        contract = workflow_contract.contract_for("2.5.1")
        self.assertEqual(contract.feedback_path_source, workflow_contract.CONTROLLER_RULE)
        self.assertEqual(contract.plan_review_publication_source, workflow_contract.REVISION_COHERENCE)
        self.assertIsNone(contract.query_script_sha256)

    def test_2_6_0_asks_workflow_for_both_facts(self) -> None:
        self.assertEqual(CONTRACT.feedback_path_source, workflow_contract.WORKFLOW_QUERY)
        self.assertEqual(CONTRACT.plan_review_publication_source, workflow_contract.WORKFLOW_QUERY)

    def test_query_digests_equal_the_vendored_release_records(self) -> None:
        for release, contract in sorted(workflow_contract.RELEASE_CONTRACTS.items()):
            with self.subTest(release=release):
                if contract.query_script_sha256 is None:
                    self.assertEqual(contract.feedback_path_source, workflow_contract.CONTROLLER_RULE)
                    self.assertEqual(contract.plan_review_publication_source,
                                     workflow_contract.REVISION_COHERENCE)
                    continue
                recorded = fixtures.workflow_release_files(release)
                self.assertEqual(set(contract.query_script_sha256), set(workflow_contract.QUERY_SCRIPTS))
                for rel_path, digest in contract.query_script_sha256.items():
                    self.assertEqual(digest, recorded[rel_path]["sha256"], rel_path)

    def test_contract_for_an_unknown_release_refuses(self) -> None:
        for release in ("2.3.1", "2.5.0", "2.6.1", "2.7.0", ""):
            with self.subTest(release=release):
                with self.assertRaises(UnsupportedWorkflowVersionError) as caught:
                    workflow_contract.contract_for(release)
                self.assertEqual(caught.exception.evidence["reason"], "no_workflow_contract")
                self.assertEqual(caught.exception.evidence["observed_workflow_version"], release)
                self.assertEqual(caught.exception.evidence["contracted_workflow_releases"], ["2.5.1", "2.6.0"])

    def test_the_status_table_matches_the_vendored_2_6_0_script(self) -> None:
        statuses = fixtures.evaluate_in_workflow_release(
            "2.6.0", "sorted(v for k, v in vars(workflow_state).items() if k.startswith('PLAN_REVIEW_STATUS_'))",
        )
        self.assertEqual(statuses, sorted(workflow_contract.PUBLICATION_STATUSES))
        self.assertEqual(len(statuses), 10)
        self.assertEqual(
            fixtures.evaluate_in_workflow_release("2.6.0", "workflow_state.PlanReviewBindingInconsistentError.__name__"),
            workflow_contract.PUBLICATION_REFUSAL_ERROR,
        )

    def test_a_contract_without_queries_never_runs_one(self) -> None:
        contract = workflow_contract.contract_for("2.5.1")
        with mock.patch.object(workflow_contract, "_execute_query") as hook, \
                tempfile.TemporaryDirectory() as root:
            for query in (workflow_contract.resolve_feedback_path, workflow_contract.plan_review_publication_status):
                with self.subTest(query=query.__name__):
                    with self.assertRaises(WorkflowQueryError) as caught:
                        query(Path(root), contract, WORK_ITEM_ID)
                    self.assertEqual(caught.exception.evidence["reason"], "no_workflow_query")
        hook.assert_not_called()

    def test_the_error_is_a_fail_closed_controller_error(self) -> None:
        self.assertEqual(WorkflowQueryError.code, "WORKFLOW_QUERY_FAILED")
        self.assertTrue(issubclass(WorkflowQueryError, ControllerError))
        self.assertEqual(cli.EXIT_FAIL_CLOSED, 20)


# ---------------------------------------------------------------------------
# --resolve-feedback-path, against the real 2.6.0 script.
# ---------------------------------------------------------------------------


class ResolveFeedbackPathTest(_SeededTargets):
    SEEDS = {
        "stamped": ("2.6.0", "route", {}),
        "legacy": ("2.5.1", "route", {"upgrade_to": "2.6.0"}),
    }

    def _assert_answer(self, answer, layout: str, feedback_dir: str) -> None:
        self.assertEqual(answer, workflow_contract.FeedbackPath(
            work_item_id=WORK_ITEM_ID, layout=layout, feedback_dir=feedback_dir,
            review_feedback_path=f"{feedback_dir}/REVIEW_FEEDBACK.md",
            functional_review_path=f"{feedback_dir}/FUNCTIONAL_REVIEW.md",
        ))

    def test_a_stamped_item_is_scoped_before_its_directory_exists(self) -> None:
        root = self.target("stamped")
        self.assertEqual(_entry(root)["feedback_layout"], "scoped")
        self.assertFalse((root / ".ai-review" / WORK_ITEM_ID / "feedback").exists())
        answer = workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        self._assert_answer(answer, "scoped", f".ai-review/{WORK_ITEM_ID}/feedback")
        self.assertFalse((root / ".ai-review" / WORK_ITEM_ID / "feedback").exists(), "the query created it")

    def test_an_unstamped_item_with_its_directory_is_legacy_scoped(self) -> None:
        root = self.target("legacy")
        self.assertNotIn("feedback_layout", _entry(root))
        (root / ".ai-review" / WORK_ITEM_ID / "feedback").mkdir(parents=True)
        answer = workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        self._assert_answer(answer, "legacy-scoped", f".ai-review/{WORK_ITEM_ID}/feedback")

    def test_an_unstamped_item_without_its_directory_is_legacy_flat(self) -> None:
        root = self.target("legacy")
        answer = workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        self._assert_answer(answer, "legacy-flat", ".ai-review/feedback")

    def test_an_unknown_id_resolves_by_the_legacy_rule(self) -> None:
        root = self.target("stamped")
        answer = workflow_contract.resolve_feedback_path(root, CONTRACT, "no-such-item")
        self.assertEqual(answer.layout, "legacy-flat")
        self.assertEqual(answer.work_item_id, "no-such-item")
        self.assertEqual(answer.feedback_dir, ".ai-review/feedback")

    def test_an_unknown_feedback_layout_is_a_query_failure(self) -> None:
        for value in ("nested", None, 3):
            with self.subTest(feedback_layout=value):
                root = self.target("stamped")
                state = _state(root)
                state["work_items"][WORK_ITEM_ID]["feedback_layout"] = value
                _write_state(root, state)
                with self.assertRaises(WorkflowQueryError) as caught:
                    workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
                evidence = caught.exception.evidence
                self.assertEqual(evidence["reason"], "query_failed")
                self.assertEqual(evidence["returncode"], 1)
                self.assertIn("UnknownFeedbackLayoutError", evidence["stderr_tail"])
                self.assertEqual(evidence["stdout_tail"], "")

    def test_an_undecidable_state_file_is_a_query_failure(self) -> None:
        root = self.target("stamped")
        (root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text("{not json\n")
        with self.assertRaises(WorkflowQueryError) as caught:
            workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        self.assertEqual(caught.exception.evidence["reason"], "query_failed")
        self.assertIn("FeedbackLayoutUndecidableError", caught.exception.evidence["stderr_tail"])

    def test_a_malformed_id_is_a_query_failure_even_when_it_looks_like_an_option(self) -> None:
        root = self.target("stamped")
        for work_item_id in ("Not An Id", "--help", "../escape"):
            with self.subTest(work_item_id=work_item_id):
                with self.assertRaises(WorkflowQueryError) as caught:
                    workflow_contract.resolve_feedback_path(root, CONTRACT, work_item_id)
                self.assertEqual(caught.exception.evidence["reason"], "query_failed")
                self.assertIn("InvalidWorkItemIdError", caught.exception.evidence["stderr_tail"])

    def test_the_evidence_names_the_query(self) -> None:
        root = self.target("stamped")
        spy = _PrivateDirSpy()
        with mock.patch.object(workflow_contract, "_execute_query", spy):
            workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        (call,) = spy.calls
        self.assertEqual(call["argv"][:4], [sys.executable, "-B", "-E", "-s"])
        self.assertEqual(Path(call["argv"][4]).name, "workflow_fingerprint.py")
        self.assertEqual(call["argv"][5:], [f"--resolve-feedback-path={WORK_ITEM_ID}"])
        self.assertEqual(call["cwd"], root)


# ---------------------------------------------------------------------------
# --plan-review-publication-status, against the real 2.6.0 script.
# ---------------------------------------------------------------------------


class PublicationStatusTest(_SeededTargets):
    SEEDS = {
        "bound": ("2.6.0", "ready", {}),
        "published": ("2.6.0", "publish", {}),
        "legacy_ready": ("2.5.1", "ready", {"upgrade_to": "2.6.0"}),
        "legacy_revising": ("2.5.1", "revise", {"upgrade_to": "2.6.0"}),
        "governed_1": ("2.6.0", "route", {"governing_workflow_version": "1"}),
    }

    def _status(self, root: Path):
        return workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID)

    def _assert_status(self, answer, *, phase: str, row: str, status: str) -> None:
        self.assertIsInstance(answer, workflow_contract.PublicationStatus)
        self.assertEqual((answer.work_item_id, answer.phase, answer.row, answer.status),
                         (WORK_ITEM_ID, phase, row, status))
        self.assertTrue(answer.remedy)

    def test_row_2_a_bound_2_6_0_item(self) -> None:
        root = self.target("bound")
        answer = self._status(root)
        self._assert_status(answer, phase="AWAITING_LOCAL_PLAN_REVIEW", row="2", status="BOUND")
        record = _entry(root)["plan_review_binding"]["bound"]
        self.assertEqual(answer.fresh_review_content_id, record["review_content_id"])
        self.assertEqual(answer.bundle_id, record["bundle_id"])
        self.assertIsNone(answer.advisory)
        self.assertIsNone(answer.detail)

    def test_row_3_a_2_5_1_ready_item_whose_bundle_verifies(self) -> None:
        root = self.target("legacy_ready")
        self.assertNotIn("plan_review_binding", _entry(root))
        answer = self._status(root)
        self._assert_status(answer, phase="AWAITING_LOCAL_PLAN_REVIEW", row="3", status="BOUND")
        self.assertIsNotNone(answer.bundle_id)
        self.assertIsNotNone(answer.fresh_review_content_id)

    def test_row_4a_the_bound_content_edited(self) -> None:
        root = self.target("bound")
        plan = root / "docs" / "ai-workflow" / f"{WORK_ITEM_ID}-PLAN.md"
        plan.write_text(plan.read_text() + "\nAn edit after binding.\n")
        answer = self._status(root)
        self._assert_status(answer, phase="AWAITING_LOCAL_PLAN_REVIEW", row="4a", status="CONTENT_DRIFTED")
        self.assertIsInstance(answer.fresh_review_content_id, str)
        self.assertNotEqual(answer.fresh_review_content_id,
                            _entry(root)["plan_review_binding"]["bound"]["review_content_id"])
        self.assertIn("not the bound", answer.detail)

    def test_row_4b_the_bundle_damaged_with_the_content_unchanged(self) -> None:
        root = self.target("bound")
        (root / ".ai-review" / WORK_ITEM_ID / "review-bundle.tar.gz").unlink()
        answer = self._status(root)
        self._assert_status(answer, phase="AWAITING_LOCAL_PLAN_REVIEW", row="4b", status="BUNDLE_UNVERIFIED")
        self.assertEqual(answer.fresh_review_content_id,
                         _entry(root)["plan_review_binding"]["bound"]["review_content_id"])
        self.assertIn("does not verify", answer.detail)

    def test_row_4c_a_2_5_1_ready_item_whose_bundle_does_not_verify(self) -> None:
        root = self.target("legacy_ready")
        (root / ".ai-review" / WORK_ITEM_ID / "current" / "MANIFEST.md").unlink()
        answer = self._status(root)
        self._assert_status(answer, phase="AWAITING_LOCAL_PLAN_REVIEW", row="4c", status="LEGACY_UNVERIFIED")
        self.assertIsNone(answer.fresh_review_content_id)
        self.assertIn("no MANIFEST.md", answer.detail)

    def test_row_5_a_2_5_1_item_mid_round(self) -> None:
        root = self.target("legacy_revising")
        answer = self._status(root)
        self._assert_status(answer, phase="REVISING_PLAN", row="5", status="LEGACY_UNMARKED")

    def test_row_9_published_but_not_bound(self) -> None:
        root = self.target("published")
        answer = self._status(root)
        self._assert_status(answer, phase="PLANNING", row="9", status="PUBLISHED_UNBOUND")
        self.assertIs(answer.bundle_verifies, False)
        self.assertEqual(answer.fresh_review_content_id,
                         _entry(root)["plan_review_binding"]["published"]["review_content_id"])

    def test_row_4d_is_a_refusal_not_an_error(self) -> None:
        # No Workflow writer leaves a ready phase holding anything but a
        # BOUND record, so row 4d is reached by moving a published item's
        # phase by hand; the query itself is the real script.
        root = self.target("published")
        state = _state(root)
        state["work_items"][WORK_ITEM_ID]["phase"] = "AWAITING_LOCAL_PLAN_REVIEW"
        _write_state(root, state)
        answer = self._status(root)
        self.assertIsInstance(answer, workflow_contract.PublicationRefusal)
        self.assertEqual(answer.work_item_id, WORK_ITEM_ID)
        self.assertEqual(answer.error, "PlanReviewBindingInconsistentError")
        self.assertIn("row 4d", answer.message)

    def test_a_1_governed_item_is_a_query_failure(self) -> None:
        root = self.target("governed_1")
        self.assertEqual(_entry(root)["governing_workflow_version"], "1")
        with self.assertRaises(WorkflowQueryError) as caught:
            self._status(root)
        evidence = caught.exception.evidence
        self.assertEqual(evidence["reason"], "query_failed")
        self.assertEqual(evidence["returncode"], 1)
        self.assertEqual(evidence["stdout_tail"], "")
        self.assertIn("Traceback", evidence["stderr_tail"])

    def test_an_unknown_id_is_a_query_failure(self) -> None:
        root = self.target("bound")
        with self.assertRaises(WorkflowQueryError) as caught:
            workflow_contract.plan_review_publication_status(root, CONTRACT, "no-such-item")
        self.assertEqual(caught.exception.evidence["reason"], "query_failed")
        self.assertIn("KeyError", caught.exception.evidence["stderr_tail"])

    def test_the_query_writes_nothing_to_the_target(self) -> None:
        root = self.target("bound")
        base = _entry(root)["base_commit"]
        # Control: in place, the query's `git diff` rewrites a stale index.
        _stat_dirty(root)
        index_before = (root / ".git" / "index").stat().st_mtime_ns
        _git(root, "diff", "--name-only", "-z", base)
        self.assertNotEqual((root / ".git" / "index").stat().st_mtime_ns, index_before,
                            "control: git diff did not refresh the index")
        _stat_dirty(root)
        before = _tree_snapshot(root)
        self.assertEqual(self._status(root).row, "2")
        workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        self.assertEqual(_tree_snapshot(root), before, "a query wrote to the target, .git included")
        self.assertEqual(_pycache_dirs(root), [])


# ---------------------------------------------------------------------------
# I6: only the admitted release's bytes execute, from a private copy.
# ---------------------------------------------------------------------------


class QueryIsolationTest(_SeededTargets):
    SEEDS = {"bound": ("2.6.0", "ready", {})}

    def _queries(self, root: Path):
        return {
            "feedback": lambda: workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID),
            "status": lambda: workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID),
        }

    def _sentinel_module(self, sentinel: Path) -> str:
        return f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n"

    def test_a_modified_script_is_refused_and_never_runs(self) -> None:
        for rel_path in workflow_contract.QUERY_SCRIPTS:
            for name in ("feedback", "status"):
                with self.subTest(script=rel_path, query=name):
                    root = self.target("bound")
                    run_query = self._queries(root)[name]
                    sentinel = root.parent / "modified-script-ran"
                    script = root / rel_path
                    original = script.read_bytes()
                    script.write_bytes(original + b"\n" + self._sentinel_module(sentinel).encode())
                    with mock.patch.object(workflow_contract, "_execute_query") as hook:
                        with self.assertRaises(WorkflowQueryError) as caught:
                            run_query()
                    hook.assert_not_called()
                    evidence = caught.exception.evidence
                    self.assertEqual(evidence["reason"], "query_script_modified")
                    self.assertEqual(evidence["path"], rel_path)
                    self.assertEqual(evidence["expected_sha256"], CONTRACT.query_script_sha256[rel_path])
                    self.assertNotEqual(evidence["observed_sha256"], evidence["expected_sha256"])
                    self.assertIsNone(evidence["argv"])
                    # Unpatched, the refusal still precedes any execution.
                    with self.assertRaises(WorkflowQueryError):
                        run_query()
                    self.assertFalse(sentinel.exists(), "the modified script was executed")

    def test_a_missing_or_unreadable_script_is_refused(self) -> None:
        for rel_path in workflow_contract.QUERY_SCRIPTS:
            for damage in ("missing", "directory"):
                with self.subTest(script=rel_path, damage=damage):
                    root = self.target("bound")
                    (root / rel_path).unlink()
                    if damage == "directory":
                        (root / rel_path).mkdir()
                    with mock.patch.object(workflow_contract, "_execute_query") as hook:
                        with self.assertRaises(WorkflowQueryError) as caught:
                            workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
                    hook.assert_not_called()
                    self.assertEqual(caught.exception.evidence["reason"], "query_script_modified")
                    self.assertEqual(caught.exception.evidence["path"], rel_path)
                    self.assertIsNone(caught.exception.evidence["observed_sha256"])

    def test_planted_standard_library_shadows_never_run(self) -> None:
        root = self.target("bound")
        sentinels = {name: root.parent / f"{name}-shadow-ran" for name in ("uuid", "secrets")}
        for name, sentinel in sentinels.items():
            (root / "scripts" / f"{name}.py").write_text(self._sentinel_module(sentinel))
        # The plants are live: run in place, each script imports its shadow.
        for script, name in (("workflow_fingerprint.py", "uuid"), ("workflow_state.py", "secrets")):
            subprocess.run([sys.executable, "-B", "-E", "-s", f"scripts/{script}", "--help"], cwd=root,
                           stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=120)
            self.assertTrue(sentinels[name].exists(), f"control: scripts/{name}.py did not run in place")
            for sentinel in sentinels.values():
                sentinel.unlink(missing_ok=True)

        feedback = workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        status = workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID)
        self.assertEqual(feedback.feedback_dir, f".ai-review/{WORK_ITEM_ID}/feedback")
        self.assertEqual((status.row, status.status), ("2", "BOUND"))
        for name, sentinel in sentinels.items():
            self.assertFalse(sentinel.exists(), f"the planted scripts/{name}.py was executed")

    def test_a_planted_bytecode_cache_never_runs_and_none_is_written(self) -> None:
        root = self.target("bound")
        sentinel = root.parent / "planted-pyc-ran"
        source = root / "scripts" / "workflow_fingerprint.py"
        source_stat = source.stat()
        code = compile(self._sentinel_module(sentinel), str(source), "exec")
        pyc = Path(importlib.util.cache_from_source(str(source)))
        pyc.parent.mkdir()
        pyc_bytes = (importlib.util.MAGIC_NUMBER + (0).to_bytes(4, "little")
                     + (int(source_stat.st_mtime) & 0xFFFFFFFF).to_bytes(4, "little")
                     + (source_stat.st_size & 0xFFFFFFFF).to_bytes(4, "little") + marshal.dumps(code))
        pyc.write_bytes(pyc_bytes)
        # The plant is live: run in place with -B, the import uses it.
        subprocess.run([sys.executable, "-B", "-E", "-s", "scripts/workflow_state.py", "--help"], cwd=root,
                       stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=120)
        self.assertTrue(sentinel.exists(), "control: the planted .pyc did not run in place")
        sentinel.unlink()

        feedback = workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
        status = workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID)
        self.assertEqual(feedback.layout, "scoped")
        self.assertEqual(status.row, "2")
        self.assertFalse(sentinel.exists(), "the planted .pyc was executed")
        self.assertEqual(_pycache_dirs(root), [pyc.parent])
        self.assertEqual(os.listdir(pyc.parent), [pyc.name])
        self.assertEqual(pyc.read_bytes(), pyc_bytes)

    def test_pythonpath_is_ignored(self) -> None:
        root = self.target("bound")
        shadow_dir = root.parent / "pythonpath"
        shadow_dir.mkdir()
        sentinel = root.parent / "pythonpath-shadow-ran"
        for name in ("uuid", "secrets", "workflow_fingerprint"):
            (shadow_dir / f"{name}.py").write_text(self._sentinel_module(sentinel))
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(shadow_dir)}):
            # The plant is live: without -E the shadow wins over the standard library.
            subprocess.run([sys.executable, "-B", "-s", "-c", "import uuid"], cwd=root,
                           stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=120)
            self.assertTrue(sentinel.exists(), "control: PYTHONPATH did not shadow uuid")
            sentinel.unlink()
            feedback = workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
            status = workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID)
        self.assertEqual(feedback.layout, "scoped")
        self.assertEqual(status.row, "2")
        self.assertFalse(sentinel.exists(), "a PYTHONPATH module was executed")

    def test_the_query_runs_exactly_the_verified_bytes_from_a_private_directory(self) -> None:
        root = self.target("bound")
        vendored = fixtures.workflow_release_tree("2.6.0")
        for name, run_query in self._queries(root).items():
            with self.subTest(query=name):
                spy = _PrivateDirSpy()
                with mock.patch.object(workflow_contract, "_execute_query", spy):
                    run_query()
                (call,) = spy.calls
                private_dir = call["private_dir"]
                self.assertTrue(private_dir.name.startswith("workflow-controller-query-"))
                self.assertFalse(private_dir.resolve().is_relative_to(root.resolve()))
                # sys.path[0] holds only the two verified scripts; beside it
                # is the private copy of the target's index.
                self.assertEqual(call["argv"][4], str(private_dir / "scripts" / Path(call["argv"][4]).name))
                self.assertEqual(call["entries"], ["workflow_fingerprint.py", "workflow_state.py"])
                self.assertEqual(call["private_entries"], ["index", "scripts"])
                self.assertEqual(call["entries_after"], call["entries"], "the query wrote into the private directory")
                for script in workflow_contract.QUERY_SCRIPTS:
                    self.assertEqual(call["bytes"][Path(script).name], (vendored / script).read_bytes())
                self.assertFalse(private_dir.exists(), "the private directory outlived the query")


# ---------------------------------------------------------------------------
# I6: the query's Git runs no program the target configures, and writes
# nothing to the target.
# ---------------------------------------------------------------------------


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


#: Every hook githooks(5) names (Git 2.55).
DOCUMENTED_HOOKS = (
    "applypatch-msg", "commit-msg", "fsmonitor-watchman", "p4-changelist", "p4-post-changelist",
    "p4-pre-submit", "p4-prepare-changelist", "post-applypatch", "post-checkout", "post-commit",
    "post-index-change", "post-merge", "post-receive", "post-rewrite", "post-update", "pre-applypatch",
    "pre-auto-gc", "pre-commit", "pre-merge-commit", "pre-push", "pre-rebase", "pre-receive",
    "prepare-commit-msg", "proc-receive", "push-to-checkout", "reference-transaction", "sendemail-validate",
    "update",
)


class GitIsolationTest(_SeededTargets):
    """Each plant is shown live first: Git run in place, as the Workflow
    scripts run it without the Controller, starts the planted program. The
    queries then answer as before, start nothing and write nothing, or
    refuse (``query_git_not_isolated``) before the query runs."""

    SEEDS = {"bound": ("2.6.0", "ready", {})}
    PLAN = f"docs/ai-workflow/{WORK_ITEM_ID}-PLAN.md"

    def fresh(self) -> Path:
        self.root = self.target("bound")
        # Outside the target, so it is never a path the query classifies.
        self.sentinel = self.root.parent / "git-program-ran"
        self.touch = f"touch {self.sentinel}"
        return self.root

    def git_config(self, *args: str) -> None:
        _git(self.root, "config", *args)

    def attributes(self, line: str) -> None:
        info = self.root / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "attributes").write_text(line + "\n")

    def assert_live(self, *args: str, root: Path | None = None) -> None:
        root = root or self.root
        _stat_dirty(root, UNCHANGED_TRACKED, self.PLAN)
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
        self.assertTrue(self.sentinel.exists(), f"control: git {' '.join(args)} did not start the plant")
        self.sentinel.unlink()

    def assert_queries_run_nothing(self) -> None:
        _stat_dirty(self.root, UNCHANGED_TRACKED, self.PLAN)
        before = _tree_snapshot(self.root)
        feedback = workflow_contract.resolve_feedback_path(self.root, CONTRACT, WORK_ITEM_ID)
        status = workflow_contract.plan_review_publication_status(self.root, CONTRACT, WORK_ITEM_ID)
        self.assertEqual(feedback.layout, "scoped")
        self.assertEqual((status.row, status.status), ("2", "BOUND"))
        self.assertFalse(self.sentinel.exists(), "a program the target configures ran during a query")
        self.assertEqual(_tree_snapshot(self.root), before, "a query wrote to the target")

    def assert_status_fails_rather_than_filters(self) -> WorkflowQueryError:
        """The feedback query, which hashes nothing, answers; the status
        query's Git fails where it would run the filter, so the query fails
        too. Either way, nothing runs and nothing is written."""
        _stat_dirty(self.root, UNCHANGED_TRACKED, self.PLAN)
        before = _tree_snapshot(self.root)
        self.assertEqual(workflow_contract.resolve_feedback_path(self.root, CONTRACT, WORK_ITEM_ID).layout, "scoped")
        with self.assertRaises(WorkflowQueryError) as caught:
            workflow_contract.plan_review_publication_status(self.root, CONTRACT, WORK_ITEM_ID)
        evidence = caught.exception.evidence
        self.assertEqual((evidence["reason"], evidence["refused_filters"]), ("query_failed", ["evil"]), evidence)
        self.assertIn("CalledProcessError", str(caught.exception))
        self.assertIn("filter attribute names 'evil'", str(caught.exception))
        self.assertFalse(self.sentinel.exists(), "a program the target configures ran during a query")
        self.assertEqual(_tree_snapshot(self.root), before, "a query wrote to the target")
        return caught.exception

    def released_status_in_place(self) -> dict:
        """Workflow's own answer: the target's query run in place, under the
        target's Git configuration, as Workflow runs it."""
        completed = fixtures.run([sys.executable, workflow_contract.STATE_SCRIPT,
                                  f"--plan-review-publication-status={WORK_ITEM_ID}"], cwd=self.root)
        return json.loads(completed.stdout)

    def assert_refused(self, facility: str) -> WorkflowQueryError:
        """Both queries refused before they run: no query, no program, no
        write, no private directory left."""
        _stat_dirty(self.root, UNCHANGED_TRACKED, self.PLAN)
        before = _tree_snapshot(self.root)
        query_tmp = Path(tempfile.mkdtemp(prefix="query-tmp-", dir=self._scratch))
        for run_query in (workflow_contract.resolve_feedback_path, workflow_contract.plan_review_publication_status):
            with mock.patch.object(tempfile, "tempdir", str(query_tmp)), \
                    mock.patch.object(workflow_contract, "_execute_query") as hook, \
                    self.assertRaises(WorkflowQueryError) as caught:
                run_query(self.root, CONTRACT, WORK_ITEM_ID)
            hook.assert_not_called()
            evidence = caught.exception.evidence
            self.assertEqual((evidence["reason"], evidence["facility"]), ("query_git_not_isolated", facility),
                             evidence)
            self.assertIsNone(evidence["argv"])
            self.assertIn("isolated", str(caught.exception))
        self.assertFalse(self.sentinel.exists())
        self.assertEqual(_tree_snapshot(self.root), before, "a refused query wrote to the target")
        self.assertEqual(os.listdir(query_tmp), [], "a refusal left its private directory")
        return caught.exception

    def assert_workflow_drifts_where_the_controller_refuses(self, plant: Callable[[Path], None],
                                                            facility: str) -> None:
        """The same plant, which appends to the plan when Git runs it, in two
        copies of the bound target. Workflow's query run in place runs it and
        answers row 4a; the Controller refuses, runs nothing and writes
        nothing, never answering row 2 or 3."""
        control = self.fresh()
        plant(control)
        _stat_dirty(control)
        released = self.released_status_in_place()
        self.assertEqual((released["row"], released["status"]), ("4a", "CONTENT_DRIFTED"))
        self.assertIn(b"planted edit", (control / self.PLAN).read_bytes(), "control: the plant did not run")
        root = self.fresh()
        plant(root)
        self.assert_refused(facility)
        self.assertNotIn(b"planted edit", (root / self.PLAN).read_bytes())

    def test_a_clean_filter_never_runs(self) -> None:
        # The external review's reproduction (round 1): `.git/info/attributes`
        # names a clean filter that `.git/config` defines; `git hash-object`
        # in the status query ran it. Even this pass-through filter is not
        # run, and the Controller cannot know it passes bytes through, so
        # the status query fails.
        for variant in ("clean", "process", "included", "operator"):
            with self.subTest(variant=variant):
                root = self.fresh()
                self.attributes("* filter=evil")
                environment = {}
                if variant == "clean":
                    self.git_config("filter.evil.clean", f"{self.touch} && cat")
                elif variant == "process":
                    self.git_config("filter.evil.process", f"sh -c '{self.touch}; exit 1'")
                elif variant == "included":
                    included = root.parent / "included.gitconfig"
                    included.write_text(f'[filter "evil"]\n\tclean = {self.touch} && cat\n')
                    self.git_config("include.path", str(included))
                else:
                    operator = root.parent / "operator.gitconfig"
                    operator.write_text(f'[filter "evil"]\n\tclean = {self.touch} && cat\n')
                    environment = {"GIT_CONFIG_GLOBAL": str(operator)}
                with mock.patch.dict(os.environ, environment):
                    self.assert_live("hash-object", "--", self.PLAN)
                    self.assert_status_fails_rather_than_filters()

    def test_a_filter_that_changes_content_fails_the_query_rather_than_bind(self) -> None:
        # The external review's reproduction (round 2): with the filter
        # switched off, `git hash-object` hashed other bytes than Workflow's,
        # so the Controller answered row 2 `BOUND` where Workflow answers 4a.
        self.fresh()
        self.attributes("* filter=evil")
        self.git_config("filter.evil.clean", f"{self.touch} && tr a-z A-Z")
        released = self.released_status_in_place()
        self.assertEqual((released["row"], released["status"]), ("4a", "CONTENT_DRIFTED"))
        self.assertTrue(self.sentinel.exists(), "control: Workflow's query did not run the filter")
        self.sentinel.unlink()
        self.assert_status_fails_rather_than_filters()

    def test_a_filter_no_hashed_path_selects_leaves_the_answer_workflows(self) -> None:
        # `evil` has a program but selects no path a query hashes; `idle`
        # selects every path but has only a smudge program, which a query
        # never runs. Git applies neither, so the answer is Workflow's own.
        root = self.fresh()
        self.attributes("*.bin filter=evil\n*.md filter=idle")
        self.git_config("filter.evil.clean", f"{self.touch} && tr a-z A-Z")
        self.git_config("filter.idle.smudge", f"{self.touch} && tr a-z A-Z")
        released = self.released_status_in_place()
        self.assertEqual((released["row"], released["status"]), ("2", "BOUND"))
        self.assertFalse(self.sentinel.exists())
        self.assert_queries_run_nothing()
        spy = _PrivateDirSpy()
        with mock.patch.object(workflow_contract, "_execute_query", spy):
            workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID)
        (call,) = spy.calls
        env = call["env"]
        overrides = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
                     for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual({key: value for key, value in overrides.items() if key.startswith("filter.")}, {
            **{f"filter.{name}.{variable}": "" for name in ("evil", "idle")
               for variable in ("process", "clean", "smudge")},
            "filter.evil.required": "true",
        })

    def test_the_querys_git_sees_a_racily_clean_edit_as_git_in_place_does(self) -> None:
        # An edit in the clock tick the index was written in keeps the
        # entry's stat data; Git re-hashes only entries no older than the
        # index file. The private copy keeps the index's mtime, so the
        # query's `git diff` lists the edit exactly as Workflow's does.
        root = self.fresh()
        self.git_config("core.trustctime", "false")
        _git(root, "update-index", "-q", "--refresh")
        edited, index = root / UNCHANGED_TRACKED, root / ".git" / "index"
        mtime = edited.stat().st_mtime_ns
        edited.write_bytes(edited.read_bytes().swapcase())
        os.utime(edited, ns=(mtime, mtime))
        os.utime(index, ns=(mtime, mtime))
        diff = ["diff", "--name-only", "-z", _entry(root)["base_commit"]]
        seen: list[tuple[int, bytes]] = []
        real = workflow_contract._execute_query

        def hook(argv, *, cwd, env, timeout):
            private_index = Path(env["GIT_INDEX_FILE"])
            seen.append((private_index.stat().st_mtime_ns,
                         subprocess.run(["git", *diff], cwd=cwd, env=env, capture_output=True, check=True).stdout))
            return real(argv, cwd=cwd, env=env, timeout=timeout)

        with mock.patch.object(workflow_contract, "_execute_query", hook):
            workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID)
        in_place = subprocess.run(["git", *diff], cwd=root, capture_output=True, check=True).stdout
        self.assertIn(UNCHANGED_TRACKED.encode(), in_place.split(b"\0"), "control: Git in place missed the edit")
        self.assertEqual(seen, [(mtime, in_place)])

    def test_the_querys_git_fires_no_hook_but_post_index_change(self) -> None:
        # What `_QUERY_HOOK_EVENTS` rests on: every documented hook
        # installed, Workflow's two queries run in place fire only the
        # events the Controller refuses. The index refresh that fires
        # `post-index-change` is forced, so the census is not empty.
        root = self.fresh()
        fired = root.parent / "fired"
        for name in DOCUMENTED_HOOKS:
            _executable(root / ".git" / "hooks" / name, f"echo {name} >> {fired}")
        _stat_dirty(root, UNCHANGED_TRACKED, self.PLAN)
        self.released_status_in_place()
        fixtures.run([sys.executable, workflow_contract.FINGERPRINT_SCRIPT,
                      f"--resolve-feedback-path={WORK_ITEM_ID}"], cwd=root)
        self.assertEqual(set(fired.read_text().split()), set(workflow_contract._QUERY_HOOK_EVENTS))

    def test_a_hook_the_querys_git_fires_is_refused(self) -> None:
        # The external review's reproduction (round 3): with the hook
        # switched off, the Controller answered row 2 where Workflow, whose
        # Git fires it, answers what the hook leaves.
        for variant in ("hooks-directory", "hooks-path", "relative-hooks-path", "operator-configured"):
            with self.subTest(variant=variant):
                root = self.fresh()
                hook = _executable(root.parent / "hook", self.touch)
                environment = {}
                facility = "hook"
                if variant == "hooks-directory":
                    shutil.copy(hook, root / ".git" / "hooks" / "post-index-change")
                elif variant == "hooks-path":
                    shutil.copy(hook, root.parent / "post-index-change")
                    self.git_config("core.hooksPath", str(root.parent))
                elif variant == "relative-hooks-path":
                    # Git reads a relative hooks path from the working
                    # tree's root; under `.git`, no query classifies it.
                    (root / ".git" / "relative-hooks").mkdir()
                    shutil.copy(hook, root / ".git" / "relative-hooks" / "post-index-change")
                    self.git_config("core.hooksPath", ".git/relative-hooks")
                else:
                    operator = root.parent / "operator.gitconfig"
                    operator.write_text(f'[hook "planted"]\n\tcommand = {hook}\n\tevent = post-index-change\n')
                    environment = {"GIT_CONFIG_GLOBAL": str(operator)}
                    facility = "hook.planted.event"
                with mock.patch.dict(os.environ, environment):
                    self.assert_live("diff", "--name-only", "-z", _entry(root)["base_commit"])
                    error = self.assert_refused(facility)
                self.assertEqual(error.evidence["event"], "post-index-change")
                if facility == "hook":
                    self.assertEqual(Path(error.evidence["path"]).resolve(),
                                     (root / _git(root, "rev-parse", "--git-path", "hooks").strip()
                                      / "post-index-change").resolve())
                else:
                    self.assertEqual(error.evidence["scope"], "global")

    def test_a_hook_that_edits_the_plan_is_refused_where_workflow_drifts(self) -> None:
        def plant(root: Path) -> None:
            _executable(root / ".git" / "hooks" / "post-index-change", f"echo planted edit >> {self.PLAN}")

        self.assert_workflow_drifts_where_the_controller_refuses(plant, "hook")

    def test_hooks_the_querys_git_never_fires_are_switched_off(self) -> None:
        # Every other documented hook, a configured hook for another event,
        # and a `post-index-change` file Git would not execute: the query
        # runs, none of them starts, and the answer is Workflow's.
        root = self.fresh()
        for name in DOCUMENTED_HOOKS:
            if name not in workflow_contract._QUERY_HOOK_EVENTS:
                _executable(root / ".git" / "hooks" / name, self.touch)
        (root / ".git" / "hooks" / "post-index-change").write_text(f"#!/bin/sh\n{self.touch}\n")
        operator = root.parent / "operator.gitconfig"
        operator.write_text(f'[hook "planted"]\n\tcommand = {self.touch}\n\tevent = pre-commit\n')
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(operator)}):
            released = self.released_status_in_place()
            self.assertEqual((released["row"], released["status"]), ("2", "BOUND"))
            self.assertFalse(self.sentinel.exists())
            self.assert_queries_run_nothing()

    def test_an_fsmonitor_program_is_refused(self) -> None:
        for variant in ("local", "operator", "environment"):
            with self.subTest(variant=variant):
                root = self.fresh()
                program = str(_executable(root.parent / "fsmonitor", f"{self.touch}; exit 1"))
                environment = {}
                if variant == "local":
                    self.git_config("core.fsmonitor", program)
                elif variant == "operator":
                    operator = root.parent / "operator.gitconfig"
                    operator.write_text(f"[core]\n\tfsmonitor = {program}\n")
                    environment = {"GIT_CONFIG_GLOBAL": str(operator)}
                else:
                    environment = {"GIT_TEST_FSMONITOR": program}
                with mock.patch.dict(os.environ, environment):
                    self.assert_live("diff", "--name-only", "-z", _entry(root)["base_commit"])
                    error = self.assert_refused("fsmonitor")
                self.assertEqual(error.evidence["program"], program)

    def test_an_fsmonitor_that_edits_the_plan_is_refused_where_workflow_drifts(self) -> None:
        def plant(root: Path) -> None:
            program = _executable(root.parent / "fsmonitor", f"echo planted edit >> {root / self.PLAN}; exit 1")
            _git(root, "config", "core.fsmonitor", str(program))

        self.assert_workflow_drifts_where_the_controller_refuses(plant, "fsmonitor")

    def test_the_builtin_fsmonitor_is_switched_off(self) -> None:
        # A boolean is Git's own daemon, not a program the target chooses:
        # it reports what a full stat check finds, so switching it off
        # leaves the answer Workflow's, and no daemon starts in the target.
        for value in ("true", "false", "1"):
            with self.subTest(value=value):
                self.fresh()
                self.git_config("core.fsmonitor", value)
                self.assert_queries_run_nothing()

    def test_no_diff_driver_runs(self) -> None:
        # The queries never ask Git for a patch, so a textconv or external
        # diff never runs; shown live on a copy whose content changed.
        root = self.fresh()
        self.attributes("* diff=evil")
        self.git_config("diff.evil.textconv", str(_executable(root.parent / "textconv", f"{self.touch}; cat \"$1\"")))
        self.git_config("diff.external", str(_executable(root.parent / "external", self.touch)))
        changed = root.parent / "changed"
        shutil.copytree(root, changed, symlinks=True)
        (changed / self.PLAN).write_text("changed\n")
        self.assert_live("diff", "HEAD", root=changed)
        self.assert_live("diff", "--no-ext-diff", "HEAD", root=changed)
        self.assert_queries_run_nothing()

    def test_a_hook_command_in_the_targets_own_configuration_is_refused(self) -> None:
        for variant in ("local", "included"):
            with self.subTest(variant=variant):
                root = self.fresh()
                hook = _executable(root.parent / "hook", self.touch)
                if variant == "local":
                    self.git_config("hook.planted.command", str(hook))
                    self.git_config("hook.planted.event", "post-index-change")
                else:
                    included = root.parent / "included.gitconfig"
                    included.write_text(f'[hook "planted"]\n\tcommand = {hook}\n\tevent = post-index-change\n')
                    self.git_config("include.path", str(included))
                self.assert_live("diff", "--name-only", "-z", _entry(root)["base_commit"])
                error = self.assert_refused("hook.planted.command")
                self.assertEqual(error.evidence["scope"], "local")

    def test_a_populated_submodule_is_refused(self) -> None:
        root = self.fresh()
        source = root.parent / "submodule-source"
        fixtures.run(["git", "init", "-q", str(source)])
        fixtures.run(["git", "-C", str(source), "-c", "user.email=s@example.invalid", "-c", "user.name=s",
                      "commit", "-q", "--allow-empty", "-m", "seed"])
        _git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "vendor/sub")
        error = self.assert_refused("submodule")
        self.assertEqual(error.evidence["path"], "vendor/sub")

    def test_a_setting_git_does_not_apply_is_refused(self) -> None:
        self.fresh()
        # A later command-scope value wins over the Controller's, as an older
        # Git that ignores GIT_CONFIG_COUNT would leave the target's in force.
        with mock.patch.dict(os.environ, {"GIT_CONFIG_PARAMETERS": "'core.hooksPath'='/elsewhere'"}):
            error = self.assert_refused("core.hookspath")
        self.assertIn("2.31", error.evidence["detail"])
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "many"}):
            self.assert_refused("GIT_CONFIG_COUNT")

    def test_an_index_that_is_not_a_regular_file_is_refused_without_waiting(self) -> None:
        self.fresh()
        index = self.root / ".git" / "index"
        index.unlink()
        os.mkfifo(index)
        self.assert_refused("index")

    def test_a_configuration_git_cannot_read_is_refused(self) -> None:
        self.fresh()
        with (self.root / ".git" / "config").open("a") as handle:
            handle.write("[unterminated\n")
        error = self.assert_refused("git")
        self.assertEqual(error.evidence["git_argv"][0], "git")

    def test_the_querys_git_environment(self) -> None:
        root = self.fresh()
        remote = root.parent / "remote.git"
        fixtures.run(["git", "init", "-q", "--bare", str(remote)])
        self.git_config("protocol.file.allow", "always")
        spy = _PrivateDirSpy()
        operator = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Operator"}
        with mock.patch.dict(os.environ, operator), mock.patch.object(workflow_contract, "_execute_query", spy):
            workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID)
        (call,) = spy.calls
        env = call["env"]
        self.assertEqual(env["GIT_INDEX_FILE"], str(call["private_dir"] / "index"))
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "")
        # The operator's own entry is kept, ahead of the Controller's.
        self.assertEqual((env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]), ("user.name", "Operator"))
        pinned = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
                  for i in range(1, int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(pinned, {"core.hookspath": os.devnull, "core.fsmonitor": "", "core.splitindex": "false",
                                  "log.showsignature": "false"})
        run = lambda *args: subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=True)  # noqa: E731
        self.assertEqual(run("config", "core.hooksPath").stdout, f"{os.devnull}\n")
        self.assertEqual(run("config", "user.name").stdout, "Operator\n")
        # No transport, whatever the target allows.
        fetch = run("ls-remote", str(remote))
        self.assertNotEqual(fetch.returncode, 0)
        self.assertIn("transport 'file' not allowed", fetch.stderr)


# ---------------------------------------------------------------------------
# The private directory, and every runner failure a WorkflowQueryError.
# ---------------------------------------------------------------------------


class RunnerFailureTest(_SeededTargets):
    SEEDS = {"bound": ("2.6.0", "ready", {})}

    def setUp(self) -> None:
        # The runner's private directories land here, so a query another
        # test process runs meanwhile never counts.
        self.query_tmp = Path(tempfile.mkdtemp(prefix="query-tmp-", dir=self._scratch))
        patcher = mock.patch.object(tempfile, "tempdir", str(self.query_tmp))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _private_dirs(self) -> list[str]:
        return sorted(os.listdir(self.query_tmp))

    def _assert_only_query_error(self, run, reason: str) -> WorkflowQueryError:
        try:
            run()
        except WorkflowQueryError as exc:
            self.assertIs(type(exc), WorkflowQueryError)
            self.assertEqual(exc.evidence["reason"], reason, exc.evidence)
            return exc
        except BaseException as exc:  # noqa: BLE001 -- the assertion is that nothing else escapes
            self.fail(f"{type(exc).__name__} escaped the runner: {exc}")
        self.fail("no WorkflowQueryError was raised")

    def test_the_private_directory_is_removed_after_success_failure_and_timeout(self) -> None:
        root = self.target("bound")
        spy = _PrivateDirSpy()
        with mock.patch.object(workflow_contract, "_execute_query", spy):
            workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID)
            self._assert_only_query_error(
                lambda: workflow_contract.plan_review_publication_status(root, CONTRACT, "no-such-item"),
                "query_failed",
            )
        with mock.patch.object(workflow_contract, "_execute_query",
                               side_effect=subprocess.TimeoutExpired(["q"], 120, output=b"partial")) as hook:
            error = self._assert_only_query_error(
                lambda: workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID), "query_timeout",
            )
        self.assertEqual(error.evidence["stdout_tail"], "partial")
        timed_out_dir = Path(hook.call_args.args[0][4]).parent.parent
        self.assertEqual(len(spy.calls), 2)
        for private_dir in [call["private_dir"] for call in spy.calls] + [timed_out_dir]:
            self.assertEqual(private_dir.parent, self.query_tmp)
            self.assertFalse(private_dir.exists(), private_dir)
        self.assertEqual(self._private_dirs(), [])

    def test_a_real_timeout_is_a_query_error_and_leaves_no_private_directory(self) -> None:
        root = self.target("bound")
        error = self._assert_only_query_error(
            lambda: workflow_contract._run_query(
                root, CONTRACT, workflow_contract.STATE_SCRIPT,
                [f"--plan-review-publication-status={WORK_ITEM_ID}"],
                query="--plan-review-publication-status", work_item_id=WORK_ITEM_ID, timeout=0.001,
            ),
            "query_timeout",
        )
        self.assertEqual(error.evidence["timeout"], 0.001)
        self.assertEqual(self._private_dirs(), [])

    def test_a_failed_private_copy_executes_nothing(self) -> None:
        root = self.target("bound")
        cases = {
            "create": lambda: mock.patch.object(tempfile, "TemporaryDirectory",
                                                side_effect=OSError(errno.ENOSPC, "No space left on device")),
            "write-enospc": lambda: mock.patch.object(runtime, "write_bytes",
                                                      side_effect=OSError(errno.ENOSPC, "No space left on device")),
            "write-containment": lambda: mock.patch.object(runtime, "write_bytes",
                                                           side_effect=RuntimeContainmentError("outside the root")),
        }
        for case, patch in cases.items():
            for name, run_query in (("feedback", workflow_contract.resolve_feedback_path),
                                    ("status", workflow_contract.plan_review_publication_status)):
                with self.subTest(case=case, query=name):
                    with patch(), mock.patch.object(workflow_contract, "_execute_query") as hook:
                        error = self._assert_only_query_error(
                            lambda: run_query(root, CONTRACT, WORK_ITEM_ID), "query_private_copy_failed",
                        )
                    hook.assert_not_called()
                    self.assertIsNone(error.evidence["argv"])
                    self.assertIn("error", error.evidence)
                    self.assertEqual(self._private_dirs(), [])

    def _failing_removal(self):
        """Patches ``tempfile.TemporaryDirectory`` with one whose removal
        fails; the returned list holds each directory, for the test to
        remove for real."""
        created: list[tempfile.TemporaryDirectory] = []

        class FailingRemoval(tempfile.TemporaryDirectory):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                created.append(self)

            def cleanup(self) -> None:
                raise OSError(errno.EBUSY, "Device or resource busy")

        self.addCleanup(lambda: [tempfile.TemporaryDirectory.cleanup(directory) for directory in created])
        return mock.patch.object(tempfile, "TemporaryDirectory", FailingRemoval), created

    def test_a_failed_removal_discards_the_answer(self) -> None:
        root = self.target("bound")
        for name, run_query in (("feedback", workflow_contract.resolve_feedback_path),
                                ("status", workflow_contract.plan_review_publication_status)):
            with self.subTest(query=name):
                failing, created = self._failing_removal()
                spy = _PrivateDirSpy()
                with failing, mock.patch.object(workflow_contract, "_execute_query", spy):
                    error = self._assert_only_query_error(
                        lambda: run_query(root, CONTRACT, WORK_ITEM_ID), "query_private_copy_failed",
                    )
                self.assertEqual(error.evidence["action"], "remove the private directory")
                self.assertEqual(error.evidence["returncode"], 0, "the query itself answered")
                self.assertEqual(len(spy.calls), 1)
                self.assertEqual(len(created), 1)

    def test_a_failed_removal_after_a_failure_keeps_the_first_failure(self) -> None:
        root = self.target("bound")
        failing, _created = self._failing_removal()
        with failing, mock.patch.object(workflow_contract, "_execute_query",
                                        side_effect=subprocess.TimeoutExpired(["q"], 120)):
            error = self._assert_only_query_error(
                lambda: workflow_contract.resolve_feedback_path(root, CONTRACT, WORK_ITEM_ID), "query_timeout",
            )
        self.assertIn("Device or resource busy", error.evidence["private_dir_removal_error"])

    def test_an_id_subprocess_cannot_pass_is_a_query_error(self) -> None:
        root = self.target("bound")
        for query in (workflow_contract.resolve_feedback_path, workflow_contract.plan_review_publication_status):
            with self.subTest(query=query.__name__):
                error = self._assert_only_query_error(
                    lambda: query(root, CONTRACT, "demo\x00item"), "query_launch_failed",
                )
                self.assertIn("null byte", error.evidence["error"])
                self.assertEqual(self._private_dirs(), [])

    def test_a_launch_failure_is_a_query_error(self) -> None:
        root = self.target("bound")
        with mock.patch.object(workflow_contract, "_execute_query",
                               side_effect=FileNotFoundError(errno.ENOENT, "No such file", sys.executable)):
            error = self._assert_only_query_error(
                lambda: workflow_contract.plan_review_publication_status(root, CONTRACT, WORK_ITEM_ID),
                "query_launch_failed",
            )
        self.assertEqual(error.evidence["argv"][:4], [sys.executable, "-B", "-E", "-s"])


# ---------------------------------------------------------------------------
# Output validation, through the private runner hook.
# ---------------------------------------------------------------------------


def _feedback_answer(**overrides) -> dict:
    answer = {
        "feedback_dir": f".ai-review/{WORK_ITEM_ID}/feedback",
        "functional_review_path": f".ai-review/{WORK_ITEM_ID}/feedback/FUNCTIONAL_REVIEW.md",
        "layout": "scoped",
        "review_feedback_path": f".ai-review/{WORK_ITEM_ID}/feedback/REVIEW_FEEDBACK.md",
        "work_item_id": WORK_ITEM_ID,
    }
    answer.update(overrides)
    return answer


def _status_answer(**overrides) -> dict:
    answer = {"work_item_id": WORK_ITEM_ID, "phase": "AWAITING_LOCAL_PLAN_REVIEW", "row": "2",
              "status": "BOUND", "remedy": "nothing to do", "fresh_review_content_id": "a" * 64,
              "bundle_id": "b" * 64, "advisory": None}
    answer.update(overrides)
    return answer


def _without(answer: dict, key: str) -> dict:
    return {k: v for k, v in answer.items() if k != key}


class OutputValidationTest(_SeededTargets):
    SEEDS = {"bound": ("2.6.0", "ready", {})}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.root = cls._seeded["bound"]

    def _answer_with(self, query, stdout, returncode: int = 0, stderr: bytes = b""):
        if not isinstance(stdout, bytes):
            stdout = (json.dumps(stdout, sort_keys=True) + "\n").encode()
        seen: list[list[str]] = []

        def hook(argv, *, cwd, env, timeout):
            seen.append(sorted(os.listdir(Path(argv[4]).parent)))
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        with mock.patch.object(workflow_contract, "_execute_query", hook):
            try:
                return query(self.root, CONTRACT, WORK_ITEM_ID)
            finally:
                # The hook replaces only execution: the verified copy is in place.
                self.assertEqual(seen, [["workflow_fingerprint.py", "workflow_state.py"]])

    def test_valid_answers_are_accepted(self) -> None:
        self.assertEqual(
            self._answer_with(workflow_contract.resolve_feedback_path, _feedback_answer(
                layout="legacy-flat", feedback_dir=".ai-review/feedback",
                review_feedback_path=".ai-review/feedback/REVIEW_FEEDBACK.md",
                functional_review_path=".ai-review/feedback/FUNCTIONAL_REVIEW.md",
            )).feedback_dir,
            ".ai-review/feedback",
        )
        statuses = {
            "row 4c has no fresh id": _without(_status_answer(row="4c", status="LEGACY_UNVERIFIED", detail="d",
                                                              remedy="r"), "fresh_review_content_id"),
            "row 4a with an unreadable fresh id": _status_answer(row="4a", status="CONTENT_DRIFTED",
                                                                 fresh_review_content_id=None, detail="d"),
            "row 9": _status_answer(phase="PLANNING", row="9", status="PUBLISHED_UNBOUND", bundle_verifies=True),
            "row 1 with only the five keys": {"work_item_id": WORK_ITEM_ID, "phase": "IMPLEMENTING", "row": "1",
                                              "status": "NOT_PLAN_STAGE", "remedy": "r"},
            "row 10": _status_answer(phase="REVISING_PLAN", row="10", status="NEEDS_EDIT"),
            "an advisory": _status_answer(advisory="the bundle_id differs"),
        }
        for case, answer in statuses.items():
            with self.subTest(case=case):
                result = self._answer_with(workflow_contract.plan_review_publication_status, answer)
                self.assertIsInstance(result, workflow_contract.PublicationStatus)
                self.assertEqual(result.row, answer["row"])
                self.assertEqual(result.fresh_review_content_id, answer.get("fresh_review_content_id"))
                self.assertEqual(result.advisory, answer.get("advisory"))
                self.assertEqual(result.bundle_verifies, answer.get("bundle_verifies"))
        refusal = self._answer_with(
            workflow_contract.plan_review_publication_status,
            {"error": "PlanReviewBindingInconsistentError", "message": "row 6; refusing"}, returncode=1,
        )
        self.assertEqual(refusal, workflow_contract.PublicationRefusal(
            work_item_id=WORK_ITEM_ID, error="PlanReviewBindingInconsistentError", message="row 6; refusing",
        ))

    def test_every_feedback_path_violation_is_refused(self) -> None:
        flat = ".ai-review/feedback"
        cases = {
            "not JSON": (b"feedback: somewhere\n", 0, "query_output_invalid"),
            "not UTF-8": (b"\xff\xfe{}\n", 0, "query_output_invalid"),
            "empty": (b"", 0, "query_output_invalid"),
            "a list": ([_feedback_answer()], 0, "query_output_invalid"),
            "two objects": ((json.dumps(_feedback_answer()) + "\n{}\n").encode(), 0, "query_output_invalid"),
            "a missing key": (_without(_feedback_answer(), "layout"), 0, "query_output_invalid"),
            "an extra key": (_feedback_answer(consumed_marker_path="x"), 0, "query_output_invalid"),
            "a non-string value": (_feedback_answer(layout=None), 0, "query_output_invalid"),
            "another id": (_feedback_answer(work_item_id="other-item"), 0, "query_output_invalid"),
            "an unknown layout": (_feedback_answer(layout="nested"), 0, "query_output_invalid"),
            "scoped with the flat directory": (_feedback_answer(
                feedback_dir=flat, review_feedback_path=f"{flat}/REVIEW_FEEDBACK.md",
                functional_review_path=f"{flat}/FUNCTIONAL_REVIEW.md"), 0, "query_output_invalid"),
            "flat with the scoped directory": (_feedback_answer(layout="legacy-flat"), 0, "query_output_invalid"),
            "a mismatched file path": (_feedback_answer(
                review_feedback_path=f".ai-review/{WORK_ITEM_ID}/feedback/OTHER.md"), 0, "query_output_invalid"),
            "an absolute path": (_feedback_answer(
                feedback_dir=f"/.ai-review/{WORK_ITEM_ID}/feedback"), 0, "query_output_invalid"),
            "a parent component": (_feedback_answer(
                feedback_dir=f".ai-review/{WORK_ITEM_ID}/../feedback"), 0, "query_output_invalid"),
            "a doubled separator": (_feedback_answer(
                feedback_dir=f".ai-review//{WORK_ITEM_ID}/feedback"), 0, "query_output_invalid"),
            "a valid answer with exit 1": (_feedback_answer(), 1, "query_failed"),
            "exit 2": (b"", 2, "query_failed"),
        }
        for case, (stdout, returncode, reason) in cases.items():
            with self.subTest(case=case):
                with self.assertRaises(WorkflowQueryError) as caught:
                    self._answer_with(workflow_contract.resolve_feedback_path, stdout, returncode)
                self.assertIs(type(caught.exception), WorkflowQueryError)
                self.assertEqual(caught.exception.evidence["reason"], reason)
                self.assertEqual(caught.exception.evidence["returncode"], returncode)

    def test_every_publication_status_violation_is_refused(self) -> None:
        cases = {
            "not JSON": (b"BOUND\n", 0, "query_output_invalid"),
            "a list": ([_status_answer()], 0, "query_output_invalid"),
            "a missing always-present key": (_without(_status_answer(), "remedy"), 0, "query_output_invalid"),
            "an undocumented key": (_status_answer(_error="x"), 0, "query_output_invalid"),
            "another id": (_status_answer(work_item_id="other-item"), 0, "query_output_invalid"),
            "a null phase": (_status_answer(phase=None), 0, "query_output_invalid"),
            "an unknown status": (_status_answer(status="STALE"), 0, "query_output_invalid"),
            "a numeric row": (_status_answer(row=2), 0, "query_output_invalid"),
            "an unknown row": (_status_answer(row="12"), 0, "query_output_invalid"),
            "a refusal row as a status": (_status_answer(row="4d"), 0, "query_output_invalid"),
            "a row with another row's status": (_status_answer(row="2", status="CONTENT_DRIFTED"), 0,
                                                "query_output_invalid"),
            "a string bundle_verifies": (_status_answer(bundle_verifies="false"), 0, "query_output_invalid"),
            "a numeric fresh id": (_status_answer(fresh_review_content_id=1), 0, "query_output_invalid"),
            "a null bundle_id": (_status_answer(bundle_id=None), 0, "query_output_invalid"),
            "a null detail": (_status_answer(detail=None), 0, "query_output_invalid"),
            "exit 1 with a traceback": (b"", 1, "query_failed"),
            "exit 1 with another error": ({"error": "KeyError", "message": "m"}, 1, "query_failed"),
            "exit 1 with an extra key": ({"error": "PlanReviewBindingInconsistentError", "message": "m",
                                          "row": "4d"}, 1, "query_failed"),
            "exit 1 with a non-string message": ({"error": "PlanReviewBindingInconsistentError",
                                                   "message": None}, 1, "query_failed"),
            "exit 1 with a status answer": (_status_answer(), 1, "query_failed"),
            "exit 2": (b"usage: ...\n", 2, "query_failed"),
        }
        for case, (stdout, returncode, reason) in cases.items():
            with self.subTest(case=case):
                with self.assertRaises(WorkflowQueryError) as caught:
                    self._answer_with(workflow_contract.plan_review_publication_status, stdout, returncode)
                self.assertIs(type(caught.exception), WorkflowQueryError)
                self.assertEqual(caught.exception.evidence["reason"], reason)

    def test_the_evidence_carries_the_query_and_its_output(self) -> None:
        with self.assertRaises(WorkflowQueryError) as caught:
            self._answer_with(workflow_contract.plan_review_publication_status, b"", 1,
                              stderr=b"Traceback (most recent call last):\nKeyError: 'demo-item'\n")
        evidence = caught.exception.evidence
        self.assertEqual(evidence["release"], "2.6.0")
        self.assertEqual(evidence["query"], "--plan-review-publication-status")
        self.assertEqual(evidence["work_item_id"], WORK_ITEM_ID)
        self.assertEqual(evidence["cwd"], str(self.root))
        self.assertEqual(evidence["argv"][5:], [f"--plan-review-publication-status={WORK_ITEM_ID}"])
        self.assertEqual(evidence["returncode"], 1)
        self.assertIn("KeyError: 'demo-item'", evidence["stderr_tail"])
        self.assertIn("KeyError: 'demo-item'", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
