"""Disposable managed-repository, real-Workflow-action integration
evidence (``REQ-T18``, capability 5,
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, CP9's own "Disposable
managed-repository integration evidence").

Proves the one capability no unit test can: that the Controller, run
exactly as an operator would run it, drives a **real** Workflow command
(``/milestone-plan``) against a disposable managed repository through a
**real** ``claude`` worker, and that the resulting Workflow lifecycle
transition is genuine and durable.

Opt-in: skipped unless ``CONTROLLER_LIVE_WORKER=1`` is set, since it
requires a live ``claude`` binary, network access and real spend -- none
of which the default suite may depend on. Per the plan, it is nevertheless
run for real, once, during CP9, with its output (worker ``session_id``,
pre-phase, post-phase, target commit, wall-clock and cost) recorded
verbatim in the implementation bundle's ``TEST_RESULTS.md`` and in the
functional-review checklist.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller.managed_repo import (  # noqa: E402
    REFERENCE_WORKFLOW_RELEASE, SUPPORTED_PROFILES, VALIDATED_WORKFLOW_RELEASES,
)
from tests import fixtures  # noqa: E402

REPO_ROOT = fixtures.REPO_ROOT
WORKFLOW_MANAGER_BIN = shutil.which("workflow-manager")
CLAUDE_BIN = shutil.which("claude")

_TRIVIAL_MILESTONE = """# Active Milestone

## Milestone

hello-file

## Goal

Add a file named `hello.txt` at the repository root containing exactly
the single line `hello, controller`. This is a disposable integration
fixture (`tests/test_integration_disposable_repo.py`, `REQ-T18`) -- the
smallest possible real change, chosen only to give `/milestone-plan`
something genuine to plan.

## Current checkpoint

None.

## Current blockers

None.

## Active plan

None. `/milestone-plan` records the plan document path on the work item's
`WORKFLOW_STATE.json` entry when a work item is created.

## Functional review checklist

Empty. `/prepare-functional-review` writes the numbered checklist for the
active work item into this section; `/apply-functional-review` and
`/accept-milestone` read it back from here.

<!--
This file is `workflow_state.FUNCTIONAL_CHECKLIST_PATH`. It is
repository-local state: workflow-manager generates it once at bootstrap and
never overwrites it on update.
-->
"""


def _release_to_install() -> str:
    """`REQ-T18` step 2's rule, stated once: install a member of
    `VALIDATED_WORKFLOW_RELEASES` -- `REFERENCE_WORKFLOW_RELEASE` when it is
    itself a member, otherwise any other named member (the lexicographically
    smallest, for determinism). Never the Manager's newest release and never
    this repository's own installed release -- neither is a statement about
    validation (revision 69, round 68's `B1`)."""
    if REFERENCE_WORKFLOW_RELEASE in VALIDATED_WORKFLOW_RELEASES:
        return REFERENCE_WORKFLOW_RELEASE
    return sorted(VALIDATED_WORKFLOW_RELEASES)[0]


def _assert_target_installation_admissible(target: Path) -> None:
    """`REQ-T18C` (revision 69, round 68's missing test): after step 2's
    install and before step 4's real Controller run, read the *target's own*
    `.workflow-manager/installation.json` -- the file the Manager actually
    wrote, never the value this fixture asked for -- and assert its
    `workflow_version` is a member of `VALIDATED_WORKFLOW_RELEASES` and its
    `profile` a member of `SUPPORTED_PROFILES`. This is what refuses to let
    live evidence run ahead of the Controller's own admission gate, whether
    the divergence came from the preferred mechanism's `--release-version`
    being dropped/mistyped/unhonoured, or from the fallback mechanism
    copying an `installation.json` this repository's own Manager update has
    moved to an unvalidated release. Fails with a message naming the
    observed value, the current membership of `VALIDATED_WORKFLOW_RELEASES`,
    and the "Supported Workflow baseline" growth procedure."""
    manifest_path = target / ".workflow-manager" / "installation.json"
    if not manifest_path.is_file():
        raise AssertionError(
            f"REQ-T18C: no installation manifest found at {manifest_path} after step 2's install"
        )
    manifest = json.loads(manifest_path.read_text())
    observed_version = manifest.get("workflow_version")
    observed_profile = manifest.get("profile")
    if observed_version not in VALIDATED_WORKFLOW_RELEASES:
        raise AssertionError(
            f"REQ-T18C: target installation declares workflow_version {observed_version!r}, "
            f"which is not a member of VALIDATED_WORKFLOW_RELEASES "
            f"({sorted(VALIDATED_WORKFLOW_RELEASES)!r}). This fixture's install step (REQ-T18 "
            f"step 2) must produce a validated release; if the Manager has genuinely moved on, "
            f"re-derive the three inventories and re-run the seven baseline-verification suites "
            f"against the newly-installed release, then add it to VALIDATED_WORKFLOW_RELEASES by "
            f"name in a plan revision that states what was measured."
        )
    if observed_profile not in SUPPORTED_PROFILES:
        raise AssertionError(
            f"REQ-T18C: target installation declares profile {observed_profile!r}, which is not "
            f"a member of SUPPORTED_PROFILES ({sorted(SUPPORTED_PROFILES)!r})."
        )


def _source_is_dirty() -> bool:
    """The exact pathspec-scoped dirty check CP1's own ``materialise()``
    uses: the tree is dirty iff ``git status --porcelain -- controller
    pyproject.toml`` produces any output."""
    result = fixtures.run(
        ["git", "status", "--porcelain", "--", "controller", "pyproject.toml"],
        cwd=REPO_ROOT, check=False,
    )
    return bool(result.stdout.strip())


def _seed_target(target: Path) -> None:
    """Steps 1-3: a throwaway repository, a real installation of a
    `VALIDATED_WORKFLOW_RELEASES` member (never the Manager's default, never
    this repository's own installed release -- `REQ-T18` step 2, revision
    69), and a trivial committed milestone for `/milestone-plan` to plan."""
    target.mkdir(parents=True, exist_ok=True)
    fixtures.run(["git", "init", "-q"], cwd=target)
    fixtures.run(["git", "config", "user.email", "controller-live-test@example.invalid"], cwd=target)
    fixtures.run(["git", "config", "user.name", "Controller Live Test"], cwd=target)
    (target / "README.md").write_text("disposable integration fixture\n")
    fixtures.run(["git", "add", "-A"], cwd=target)
    fixtures.run(["git", "commit", "-q", "-m", "seed"], cwd=target)

    release = _release_to_install()

    if WORKFLOW_MANAGER_BIN is not None:
        # Preferred mechanism: the real installation path, naming the
        # release it wants. `--release-version` is a *global* option and is
        # written *before* the subcommand -- measured, not assumed
        # (`workflow-manager bootstrap --help`: the bootstrap subparser
        # accepts only `--profile`/`--force`; the release selector sits on
        # the top-level parser).
        result = fixtures.run(
            [WORKFLOW_MANAGER_BIN, "--release-version", release, "bootstrap", str(target),
             "--profile", "full"],
            cwd=target, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"workflow-manager --release-version {release} bootstrap failed "
                f"(exit {result.returncode}): {result.stdout}\n{result.stderr}"
            )
    else:
        # Fallback fixture detail, not a reimplementation of install
        # semantics: copy this repository's own installed Workflow tree and
        # manifest. CP2 still asks the real Manager to verify the result at
        # `step` time; if the Manager refuses this fixture, the test fails
        # rather than proceeding. The copied `installation.json`'s own
        # `workflow_version` is validated below by
        # `_assert_target_installation_admissible` (REQ-T18C) -- if it is
        # not a member of `VALIDATED_WORKFLOW_RELEASES`, the fallback does
        # not silently produce an unvalidated fixture and does not rewrite
        # the manifest to claim a release it did not copy: this call fails
        # with that named assertion instead.
        shutil.copytree(REPO_ROOT / ".claude" / "commands", target / ".claude" / "commands",
                         dirs_exist_ok=True)
        shutil.copytree(REPO_ROOT / "scripts", target / "scripts", dirs_exist_ok=True,
                         ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ai-review"))
        shutil.copytree(REPO_ROOT / "docs" / "ai-workflow", target / "docs" / "ai-workflow",
                         dirs_exist_ok=True)
        (target / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(
            json.dumps({"schema_version": 1, "active_work_item_id": None, "work_items": {}}, indent=2)
            + "\n"
        )
        # `O3` (MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 2): step 2 says
        # "state **and config** reset to the bootstrap template" -- the
        # wholesale `docs/ai-workflow/` copy above carries this repository's
        # own operational `WORKFLOW_CONFIG.json` along with it, the same
        # "an operations fact treated as an installation fact" class round
        # 68's `B1` found in this fixture's own install step. Reset it to
        # the real bootstrap template's own value, not this repository's
        # copied one.
        (target / "docs" / "ai-workflow" / "WORKFLOW_CONFIG.json").write_text(
            json.dumps(
                {"schema_version": 1, "default_workflow_version": "2.1", "supported_versions": ["1", "2.1"]},
                indent=2,
            )
            + "\n"
        )
        shutil.copytree(REPO_ROOT / ".workflow-manager", target / ".workflow-manager",
                         dirs_exist_ok=True)

    # Step 3b (`REQ-T18C`): assert the target's own installation is
    # admissible before anything is launched against it -- the one
    # instrument that keeps both mechanisms' divergence from this
    # repository's installed release audible rather than silent.
    _assert_target_installation_admissible(target)

    (target / "docs" / "ACTIVE_MILESTONE.md").write_text(_TRIVIAL_MILESTONE)
    fixtures.run(["git", "add", "-A"], cwd=target)
    fixtures.run(["git", "commit", "-q", "-m", "seed trivial milestone"], cwd=target)


class InstallationAdmissibilityTest(unittest.TestCase):
    """Default-suite coverage for `_release_to_install` and
    `_assert_target_installation_admissible` (`M1`,
    MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 2): both were previously
    exercised only from inside `DisposableRepoRealWorkflowActionTest`,
    which the default suite never runs (`CONTROLLER_LIVE_WORKER=1`-gated) --
    so the one instrument whose entire value is "where it fires" ran only
    under a real live worker. None of the cases here needs a live `claude`
    binary, the real Manager, or network access: each writes a synthetic
    `.workflow-manager/installation.json` straight into a `tmp_path`. These
    belong outside the skipped class so the growth procedure named in
    `_assert_target_installation_admissible`'s own refusal message stays
    load-bearing after a future `VALIDATED_WORKFLOW_RELEASES` change."""

    def _write_manifest(self, target: Path, *, workflow_version, profile) -> None:
        manifest_dir = target / ".workflow-manager"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        payload: dict = {}
        if workflow_version is not None:
            payload["workflow_version"] = workflow_version
        if profile is not None:
            payload["profile"] = profile
        (manifest_dir / "installation.json").write_text(json.dumps(payload))

    def test_release_to_install_returns_a_validated_release(self) -> None:
        self.assertIn(_release_to_install(), VALIDATED_WORKFLOW_RELEASES)

    def test_admissible_installation_raises_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            self._write_manifest(
                target,
                workflow_version=sorted(VALIDATED_WORKFLOW_RELEASES)[0],
                profile=sorted(SUPPORTED_PROFILES)[0],
            )
            _assert_target_installation_admissible(target)  # must not raise

    def test_unvalidated_inline_release_is_refused(self) -> None:
        # A release one point release ahead of every VALIDATED_WORKFLOW_
        # RELEASES member -- plausible "the Manager moved on" drift, not a
        # typo of a validated one.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            self._write_manifest(target, workflow_version="2.5.2", profile="full")
            with self.assertRaises(AssertionError) as ctx:
                _assert_target_installation_admissible(target)
            self.assertIn("2.5.2", str(ctx.exception))
            self.assertIn("VALIDATED_WORKFLOW_RELEASES", str(ctx.exception))

    def test_wrong_line_release_is_refused(self) -> None:
        # A release on an entirely different line (2.3.x) -- distinguishes
        # "not this exact validated version" from "not even the same
        # generation of Workflow".
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            self._write_manifest(target, workflow_version="2.3.1", profile="full")
            with self.assertRaises(AssertionError) as ctx:
                _assert_target_installation_admissible(target)
            self.assertIn("2.3.1", str(ctx.exception))

    def test_unsupported_profile_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            self._write_manifest(
                target, workflow_version=sorted(VALIDATED_WORKFLOW_RELEASES)[0], profile="minimal",
            )
            with self.assertRaises(AssertionError) as ctx:
                _assert_target_installation_admissible(target)
            self.assertIn("minimal", str(ctx.exception))
            self.assertIn("SUPPORTED_PROFILES", str(ctx.exception))

    def test_absent_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            with self.assertRaises(AssertionError) as ctx:
                _assert_target_installation_admissible(target)
            self.assertIn("no installation manifest found", str(ctx.exception))


@unittest.skipUnless(
    os.environ.get("CONTROLLER_LIVE_WORKER") == "1",
    "requires a live claude binary, network access and real spend -- set "
    "CONTROLLER_LIVE_WORKER=1 to opt in",
)
class DisposableRepoRealWorkflowActionTest(unittest.TestCase):
    def test_real_milestone_plan_produces_a_durable_awaiting_local_plan_review_transition(self) -> None:
        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")

        with tempfile.TemporaryDirectory(prefix="controller-live-") as td:
            tmp_root = Path(td)
            target = tmp_root / "target"
            runtime_root = tmp_root / "runtime"
            _seed_target(target)

            argv = ["--runtime-dir", str(runtime_root)]
            if _source_is_dirty():
                argv.append("--allow-dirty-source")
            argv += ["--permission-mode", "bypassPermissions", "step", str(target)]

            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT)
            env.pop("WORKFLOW_CONTROLLER_EXEC_HANDOFF", None)

            start = time.monotonic()
            proc = subprocess.run(
                [sys.executable, "-P", "-m", "controller", *argv],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=1800,
            )
            wall_clock = time.monotonic() - start

            self.assertEqual(
                proc.returncode, 0,
                f"`step` did not exit 0 (exit {proc.returncode})\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
            )

            state_path = target / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            work_items = state["work_items"]
            self.assertEqual(len(work_items), 1, f"expected exactly one work item, got {work_items!r}")
            work_item_id, entry = next(iter(work_items.items()))
            self.assertEqual(entry["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

            registry_path = entry.get("registry_path")
            mapping_path = entry.get("mapping_path")
            self.assertIsNotNone(registry_path)
            self.assertIsNotNone(mapping_path)
            self.assertTrue((target / registry_path).is_file())
            self.assertTrue((target / mapping_path).is_file())

            artifacts_path = (
                target / "docs" / "ai-workflow" / "registry" / f"{work_item_id}-artifacts.json"
            )
            self.assertTrue(
                artifacts_path.is_file(),
                f"expected an artifacts-declaration file at {artifacts_path}",
            )

            jobs_dir = runtime_root / "jobs"
            job_files = sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []
            self.assertEqual(len(job_files), 1, f"expected exactly one job record, got {job_files!r}")
            job_record = json.loads(job_files[0].read_text())
            self.assertEqual(job_record["status"], "FINISHED")
            self.assertTrue(job_record["transition_verified"])
            # `job.py`'s own schema (`_worker_dict`/`execute_step`):
            # `worker_outcome` is the plain classification string itself
            # (`worker.SUCCESS`/etc.), never a dict; the worker's own
            # session id, exit code and timing live under the sibling
            # `"worker"` block. B1 (self-review round, `docs/ACTIVE_
            # MILESTONE.md`'s "Current blockers"): this test previously
            # read `worker_outcome` as a dict with `session_id`/
            # `classification`/`duration_seconds` keys, a schema that has
            # never existed in `job.py`'s actual records.
            worker = job_record["worker"]
            worker_outcome = job_record["worker_outcome"]
            self.assertIsNotNone(worker.get("session_id"))

            evidence = {
                "worker_session_id": worker.get("session_id"),
                "worker_outcome_classification": worker_outcome,
                "pre_phase": job_record["pre_state"]["phase"],
                "post_phase": job_record["observed_phase_after"],
                "target_repo_commit_before": job_record["pre_state"].get("target_head"),
                "wall_clock_seconds": round(wall_clock, 1),
                "worker_duration_ms": worker.get("duration_ms"),
            }
            print("DISPOSABLE_REPO_INTEGRATION_EVIDENCE " + json.dumps(evidence))


if __name__ == "__main__":
    unittest.main()
