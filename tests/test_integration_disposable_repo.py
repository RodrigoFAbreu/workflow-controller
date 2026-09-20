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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import identity, job  # noqa: E402
from controller.cli import EXIT_INCOMPLETE, EXIT_OK, EXIT_WORKER_FAILED  # noqa: E402
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT  # noqa: E402
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


def _expected_controller_source_tree_digest(runtime_root: Path) -> str:
    """Independently derive the Controller source-tree digest the launched
    subprocess's own snapshot must carry (`REQ-T18` step 4, round 3's
    `I1`/`M1`): calls the *same* immutable source/pin primitive
    (`identity.materialise`) the subprocess itself goes through on its
    unpinned-source branch (`cli._dispatch`), against the same origin
    (`REPO_ROOT`) and the same dirty/clean classification
    (`_source_is_dirty`), rather than trusting the job record's own
    self-reported value. `materialise()`'s destination directory is always
    named after the digest it just computed (`identity.materialise`'s own
    `dest = source_dir / tree_digest`), so `.name` is that digest."""
    snapshot_dir = identity.materialise(REPO_ROOT, runtime_root, allow_dirty=_source_is_dirty())
    return snapshot_dir.name


def _assert_job_record_source_tree_digest_matches_expected(
    job_record: dict, expected_digest: str,
) -> str:
    """The binding assertion `REQ-T18` step 4 requires: not merely that
    `job_record["controller_source_tree_digest"]` looks like a sha256 digest,
    but that it equals `expected_digest`. Raises `AssertionError` (never
    returns falsy) so a regression that persists a different, syntactically
    valid 64-hex digest into the job record is caught rather than passing on
    shape alone. Returns the validated digest for the caller to log/print."""
    source_tree_digest = job_record.get("controller_source_tree_digest")
    if not isinstance(source_tree_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", source_tree_digest):
        raise AssertionError(
            f"controller_source_tree_digest must be a 64-hex sha256 digest, "
            f"got {source_tree_digest!r}"
        )
    if source_tree_digest != expected_digest:
        raise AssertionError(
            f"job record's controller_source_tree_digest {source_tree_digest!r} does not "
            f"match the independently derived expected Controller source-tree digest "
            f"{expected_digest!r} -- the live run did not execute from the exact Controller "
            f"bytes under review"
        )
    return source_tree_digest


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


def _seed_target_2_2(target: Path) -> None:
    """CP5's own sibling to :func:`_seed_target`: the same disposable
    target, plus the one extra fact ``scripts/workflow_state.py``'s own
    ``validate_governing_version`` (around line 7007) requires before a
    ``"2.2"``-governed work item can even be *created*: ``WORKFLOW_CONFIG.
    json``'s ``default_workflow_version`` set to ``"2.2"`` **and** ``"2.2"``
    added to ``supported_versions``. Both keys are required -- seeding only
    the default leaves ``validate_governing_version`` refusing the work
    item at creation, before the Controller ever dispatches it, exactly the
    mistake this repository's own activation commit ``21a304d`` had to
    correct by hand (adding ``"2.2"`` to ``supported_versions``). The
    reference bootstrap template
    (``workflow-manager/distribution/workflow/2.5.1/templates/docs/
    ai-workflow/WORKFLOW_CONFIG.json``) declares ``supported_versions:
    ["1", "2.1"]`` with no ``"2.2"`` at all, so this patch is never a no-op
    against either the real-Manager or the fallback install path
    :func:`_seed_target` itself chooses between."""
    _seed_target(target)
    config_path = target / "docs" / "ai-workflow" / "WORKFLOW_CONFIG.json"
    config = json.loads(config_path.read_text())
    config["default_workflow_version"] = "2.2"
    supported_versions = list(config.get("supported_versions") or [])
    if "2.2" not in supported_versions:
        supported_versions.append("2.2")
    config["supported_versions"] = supported_versions
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    fixtures.run(["git", "add", "-A"], cwd=target)
    fixtures.run(["git", "commit", "-q", "-m", "seed 2.2 governing-version support"], cwd=target)


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


class ControllerSourceTreeDigestVerificationTest(unittest.TestCase):
    """Default-suite coverage for `_expected_controller_source_tree_digest`
    and `_assert_job_record_source_tree_digest_matches_expected` (`REQ-T18`
    step 4, round 3's `I1`/`M1`): the live fixture below is skipped unless
    `CONTROLLER_LIVE_WORKER=1`, so the discriminating behavior the round-3
    external review asked for -- that a job record carrying a different but
    syntactically valid 64-hex digest is rejected, not merely shape-checked
    -- must also be provable without a live worker. None of the cases here
    spend a live `claude` call; `test_expected_digest_is_well_formed` is the
    only one that touches `identity.materialise`, and it does so against
    this repository's own current source, exactly as the live fixture would."""

    def test_expected_digest_is_well_formed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controller-digest-verify-") as td:
            expected = _expected_controller_source_tree_digest(Path(td))
        self.assertRegex(expected, r"^[0-9a-f]{64}$")

    def test_matching_digest_is_accepted(self) -> None:
        expected = "a" * 64
        result = _assert_job_record_source_tree_digest_matches_expected(
            {"controller_source_tree_digest": expected}, expected,
        )
        self.assertEqual(result, expected)

    def test_different_but_syntactically_valid_digest_is_rejected(self) -> None:
        expected = "a" * 64
        forged = "b" * 64
        with self.assertRaises(AssertionError) as ctx:
            _assert_job_record_source_tree_digest_matches_expected(
                {"controller_source_tree_digest": forged}, expected,
            )
        self.assertIn(forged, str(ctx.exception))
        self.assertIn(expected, str(ctx.exception))

    def test_malformed_digest_is_rejected_on_shape_alone(self) -> None:
        with self.assertRaises(AssertionError) as ctx:
            _assert_job_record_source_tree_digest_matches_expected(
                {"controller_source_tree_digest": "not-a-digest"}, "a" * 64,
            )
        self.assertIn("64-hex", str(ctx.exception))

    def test_missing_digest_is_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            _assert_job_record_source_tree_digest_matches_expected({}, "a" * 64)


#: A fixed, non-``None`` identity for CP5's own seeded-state test below --
#: every one of its five decisions is a report/decline, never a real
#: worker launch, so this identity is never compared against a live pin;
#: it exists only so ``job.execute_step``/``job.resume`` have something to
#: stamp ``controller_generation``/``controller_source_commit``/
#: ``controller_source_tree_digest`` with, and so ``resume``'s own case-1
#: ``validate_record`` check (``record_generation > running_generation``)
#: reads the same generation on both the write and the read side.
_FAKE_2_2_IDENTITY = ControllerIdentity(
    generation=1,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_COMMIT,
    source_commit="a" * 40,
    tree_digest="b" * 64,
    generation_source="head",
    pinned_at="2024-01-01T00:00:00Z",
)


class Protocol22ImplementationReviewGatesTest(unittest.TestCase):
    """CP5: a genuine ``"2.2"``-governed work item, seeded in turn at five
    real on-disk states of the split implementation-review lifecycle
    (``AWAITING_LOCAL_IMPLEMENTATION_REVIEW``, then
    ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` across its own
    no-ledger / BLOCK / not-yet-ingested-APPROVE sub-cases, then the reused
    terminal ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` with an APPROVE on
    file) -- proving CP4's ``declined=True`` classification and CP4B's
    evidence-reading gate classification against **real** ``git``-committed
    target state, real ``WORKFLOW_STATE.json``, and real ``.ai-review/``
    ``MANIFEST.md``/``REVIEW_FEEDBACK.md`` files written through
    ``tests.fixtures``'s own builders -- never a live ``claude`` worker,
    since every one of these five decisions is a report or a decline and
    ``job.execute_step`` never reaches its own worker-launch guard for any
    of them (``controller/job.py``'s own positive launch guard: a worker is
    launched only when ``decision.automatic`` is ``True``, which none of
    these five ever is).

    Calls ``controller.job.execute_step``/``controller.job.resume``
    directly against a real, disposable target repository -- the same
    "real git repo, synthetic (root-only) ``ManagedRepository``" fixture
    shape ``tests/test_job.py`` already establishes for exercising
    ``execute_step`` without a live worker or a real Workflow Manager
    install -- rather than the pinned-re-exec ``python -P -m controller``
    CLI invocation :class:`DisposableRepoRealWorkflowActionTest` below
    uses: nothing about a declined/gated decision needs pinned execution or
    a real installation to prove, and every fact ``controller step``/
    ``explain`` would themselves read (``evidence.decide`` off real
    on-disk state) is exactly what ``execute_step`` already calls."""

    def _seed(self, root: Path, *, work_item_id: str, base_commit: str) -> dict:
        """The base, mostly-constant work-item entry a genuine ``"2.2"``
        item would carry by the time it first reaches
        ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` -- past ``PLANNING``/
        ``IMPLEMENTING``, so its own registry is fully checkpoint-complete
        -- with a real registry file (``tests.fixtures.write_target_
        registry``) plus a mapping file and an artifacts-declaration file
        (real files at the same paths a genuine work item's own entry would
        name; neither is read by any Controller code path, so their
        content is inert beyond existing)."""
        registry_rel = f"docs/ai-workflow/registry/{work_item_id}-registry.json"
        mapping_rel = f"docs/ai-workflow/registry/{work_item_id}-mapping.json"
        artifacts_rel = f"docs/ai-workflow/registry/{work_item_id}-artifacts.json"

        fixtures.write_target_registry(root, registry_rel, {
            "work_item_id": work_item_id,
            "checkpoints": [{"id": "CP1"}],
        })
        (root / mapping_rel).write_text(
            json.dumps({"work_item_id": work_item_id, "requirements": []}, indent=2) + "\n"
        )
        (root / artifacts_rel).write_text(
            json.dumps({"work_item_id": work_item_id, "artifacts": []}, indent=2) + "\n"
        )

        return {
            "work_item_type": "product",
            "work_item_kind": "product",
            "work_item_id": work_item_id,
            "governing_workflow_version": "2.2",
            "plan_revision": 1,
            "implementation_revision": 1,
            "state_revision": 1,
            "checkpoints": {"CP1": {"status": "COMPLETE"}},
            "current_bundle_id": "b" * 64,
            "last_completed_checkpoint_id": "CP1",
            "base_commit": base_commit,
            "parent_work_item_id": None,
            "registry_path": registry_rel,
            "mapping_path": mapping_rel,
        }

    def test_five_seeded_states_report_or_gate_and_resume_never_self_approves(self) -> None:
        work_item_id = "wi-2-2-impl-review"

        with tempfile.TemporaryDirectory(prefix="controller-2-2-impl-review-") as td:
            tmp_root = Path(td)
            root = tmp_root / "target"
            runtime_root = tmp_root / "runtime"

            fixtures.build_target_git_repo(root)
            (root / "README.md").write_text("2.2 implementation-review fixture\n")
            base_commit = fixtures.commit_all(root, "initial")

            managed_repo = fixtures.build_target_managed_repository(root)
            base_entry = self._seed(root, work_item_id=work_item_id, base_commit=base_commit)

            def _write_phase(phase: str) -> None:
                entry = {**base_entry, "phase": phase}
                fixtures.write_workflow_state(root, {
                    "schema_version": 1,
                    "active_work_item_id": work_item_id,
                    "work_items": {work_item_id: entry},
                })

            def _run() -> dict:
                result = job.execute_step(
                    managed_repo, work_item_id=work_item_id, identity=_FAKE_2_2_IDENTITY,
                    runtime=runtime_root,
                )
                self.assertIsInstance(
                    result, dict,
                    "every one of these five decisions is a report/decline, never the "
                    "no-action (LEGACY_READY/MILESTONE_COMPLETE) class, so execute_step "
                    "must always return a JobRecord dict here, never a bare Decision",
                )
                # The human-only-approval boundary, checked at every one of
                # the five states alike: the Controller itself never
                # selects /approve-review or /accept-milestone as its own
                # automatic action, and never marks a decision automatic
                # here at all.
                self.assertFalse(result["selected_action"]["automatic"])
                command = result["selected_action"]["command"]
                if command is not None:
                    self.assertNotIn("/approve-review", command)
                    self.assertNotIn("/accept-milestone", command)
                return result

            feedback_dir_rel = Path(".ai-review") / work_item_id / "feedback"

            # 1. AWAITING_LOCAL_IMPLEMENTATION_REVIEW, no ledger yet:
            # declined=True, names /review-implementation, no worker.
            with self.subTest(state="AWAITING_LOCAL_IMPLEMENTATION_REVIEW"):
                _write_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
                record_1 = _run()
                self.assertEqual(record_1["status"], job.STATUS_DECLINED)
                self.assertIsNone(record_1["human_gate_pending"])
                self.assertTrue(record_1["selected_action"]["declined"])
                self.assertEqual(
                    record_1["selected_action"]["command"], f"/review-implementation {work_item_id}",
                )

            # 2. AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW, no
            # current-round feedback: reports the gate, names
            # /record-manual-implementation-review.
            with self.subTest(state="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW/no-feedback"):
                _write_phase("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
                record_2 = _run()
                self.assertEqual(record_2["status"], job.STATUS_GATE_BLOCKED)
                gate_2 = record_2["human_gate_pending"]
                self.assertIsNotNone(gate_2)
                self.assertEqual(
                    gate_2["safe_resume_command"],
                    f"/record-manual-implementation-review {work_item_id}",
                )
                self.assertIn("MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", gate_2["what_is_required"])

            # 3. Same phase, Status: BLOCK on file: reports the BLOCK gate;
            # still declines to act.
            with self.subTest(state="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW/BLOCK"):
                fixtures.write_review_feedback(root, feedback_dir_rel, fixtures.build_review_feedback_text(
                    status="BLOCK", reviewer_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                    reviewed_bundle_id="b" * 64, reviewed_base_commit=base_commit,
                    work_item=work_item_id, reviewed_content_id="c" * 64,
                ))
                record_3 = _run()
                self.assertEqual(record_3["status"], job.STATUS_GATE_BLOCKED)
                gate_3 = record_3["human_gate_pending"]
                self.assertIsNotNone(gate_3)
                self.assertEqual(
                    gate_3["safe_resume_command"],
                    f"/record-manual-implementation-review {work_item_id}",
                )
                self.assertIn("blocked", gate_3["what_is_required"])

            # 4. Same phase, Status: APPROVE on file but not yet ingested
            # (phase unchanged, ledger not yet updated): reports "a verdict
            # is on file; run /record-manual-implementation-review" --
            # never invents the approval itself.
            with self.subTest(state="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW/APPROVE-not-ingested"):
                fixtures.write_review_feedback(root, feedback_dir_rel, fixtures.build_review_feedback_text(
                    status="APPROVE", reviewer_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                    reviewed_bundle_id="b" * 64, reviewed_base_commit=base_commit,
                    work_item=work_item_id, reviewed_content_id="c" * 64,
                ))
                record_4 = _run()
                self.assertEqual(record_4["status"], job.STATUS_GATE_BLOCKED)
                gate_4 = record_4["human_gate_pending"]
                self.assertIsNotNone(gate_4)
                self.assertEqual(
                    gate_4["safe_resume_command"],
                    f"/record-manual-implementation-review {work_item_id}",
                )
                self.assertIn("verdict is on file", gate_4["what_is_required"])
                self.assertIn("/record-manual-implementation-review", gate_4["what_is_required"])

            # 5. The reused terminal AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW,
            # APPROVE on file: reports the user-only
            # /approve-review implementation gate, exactly as for a
            # "2.1"/"1" item -- the same current-round REVIEW_FEEDBACK.md
            # written at step 4 is read again here (resolve_feedback_dir is
            # stage-agnostic), and a fresh, current MANIFEST.md is all this
            # state adds.
            with self.subTest(state="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW/APPROVE"):
                bundle_dir_rel = Path(".ai-review") / work_item_id / "current"
                fixtures.write_manifest(root, bundle_dir_rel, fixtures.build_manifest_text(
                    bundle_id="b" * 64, generation_head=base_commit,
                ))
                _write_phase("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
                record_5 = _run()
                self.assertEqual(record_5["status"], job.STATUS_GATE_BLOCKED)
                gate_5 = record_5["human_gate_pending"]
                self.assertIsNotNone(gate_5)
                self.assertEqual(
                    gate_5["safe_resume_command"],
                    f"/approve-review implementation {work_item_id}",
                )
                self.assertIn("user-only", gate_5["what_is_required"])
                self.assertIn("/approve-review implementation", gate_5["what_is_required"])

            # A `controller resume` pass across the same five seeded
            # states must not crash or misclassify at any of them: every
            # one of the five job records above is already terminal
            # (GATE_BLOCKED/DECLINED), so `resume` must report each back
            # unchanged (never reconciled, never relaunched, never
            # rewritten as `resume_marked`), and none of them may have
            # become an automatic /approve-review or /accept-milestone
            # selection along the way.
            all_records = [record_1, record_2, record_3, record_4, record_5]
            resumed = job.resume(managed_repo, identity=_FAKE_2_2_IDENTITY, runtime=runtime_root)
            self.assertEqual(len(resumed), len(all_records))

            resumed_by_job_id = {r["job_id"]: r for r in resumed}
            self.assertEqual(set(resumed_by_job_id), {r["job_id"] for r in all_records})
            for original in all_records:
                with self.subTest(job_id=original["job_id"]):
                    reconciled = resumed_by_job_id[original["job_id"]]
                    self.assertNotIn(
                        "resume_marked", reconciled,
                        f"job {original['job_id']!r} was marked malformed/unreadable by resume",
                    )
                    self.assertEqual(reconciled["status"], original["status"])
                    self.assertIn(reconciled["status"], (job.STATUS_DECLINED, job.STATUS_GATE_BLOCKED))
                    self.assertFalse(reconciled["selected_action"]["automatic"])
                    command = reconciled["selected_action"]["command"]
                    if command is not None:
                        self.assertNotIn("/approve-review", command)
                        self.assertNotIn("/accept-milestone", command)


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

            # `O2` (MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 2): step 5's
            # declared post-condition is the post-run `work_items.keys()`
            # minus *this fixture's own recorded pre-run* `work_items.keys()`
            # -- identified the same way row 7's own predicate is -- not a
            # bare `len(work_items) == 1`, which is only equivalent to it
            # under step 3's own precondition (a freshly seeded target's
            # `work_items` is `{}`) holding.
            state_path = target / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            pre_run_state = json.loads(state_path.read_text())
            pre_run_work_item_keys = set(pre_run_state.get("work_items", {}).keys())

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

            state = json.loads(state_path.read_text())
            work_items = state["work_items"]
            post_run_work_item_keys = set(work_items.keys())
            new_work_item_keys = post_run_work_item_keys - pre_run_work_item_keys
            self.assertEqual(
                len(new_work_item_keys), 1,
                f"expected exactly one new work item, pre-run keys={pre_run_work_item_keys!r} "
                f"post-run keys={post_run_work_item_keys!r}",
            )
            work_item_id = next(iter(new_work_item_keys))
            entry = work_items[work_item_id]
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

            # `REQ-T18` step 4 (revision 71, round 2's `I1`/`M3`; round 3's
            # `I1`/`M1`): the binding identifier of the bytes this run
            # executed is `controller_source_tree_digest`, present under
            # both the `commit` and `worktree` pin outcomes -- never
            # `controller_source_commit` alone, which is `None` under the
            # `worktree` outcome by design (`identity.py`'s own scope
            # rule). Shape-checking the digest alone would pass for an
            # arbitrary or stale 64-hex value; the binding proof round 3
            # asked for is equality with the digest independently derived
            # from the same immutable source/pin model
            # (`_expected_controller_source_tree_digest`), which the
            # subprocess above could not have influenced.
            expected_source_tree_digest = _expected_controller_source_tree_digest(
                tmp_root / "verify-runtime"
            )
            source_tree_digest = _assert_job_record_source_tree_digest_matches_expected(
                job_record, expected_source_tree_digest,
            )
            source_commit = job_record.get("controller_source_commit")

            evidence = {
                "worker_session_id": worker.get("session_id"),
                "worker_outcome_classification": worker_outcome,
                "pre_phase": job_record["pre_state"]["phase"],
                "post_phase": job_record["observed_phase_after"],
                "target_repo_commit_before": job_record["pre_state"].get("target_head"),
                "wall_clock_seconds": round(wall_clock, 1),
                "worker_duration_ms": worker.get("duration_ms"),
                "controller_source_tree_digest": source_tree_digest,
                "controller_source_commit": source_commit,
            }
            print("DISPOSABLE_REPO_INTEGRATION_EVIDENCE " + json.dumps(evidence))

    def test_real_review_plan_reaches_the_2_2_plan_review_expected_outcomes_row(self) -> None:
        """CP5's own live-Claude sibling: the discriminating regression for
        CP1's ``("AWAITING_LOCAL_PLAN_REVIEW", "2.2", "/review-plan")``
        ``EXPECTED_OUTCOMES`` row (``controller/job.py``), added by the
        ``workflow-controller-protocol-2-2-compatibility`` milestone's CP1.
        Before CP1, ``_expected_outcome_for`` raised an uncaught
        ``AssertionError`` for that exact key -- there was no row for it at
        all, since only the ``"2.1"``/``"1"`` rows existed. Two real
        ``controller step`` calls, not one:

        1. Row 7's own ``NoWorkItemYet`` bootstrap
           (``from_phase=NO_PHASE, governing_version=None``) -- unaffected
           by CP1's fix (row 7 never keys on ``governing_workflow_version``
           at all), so this step alone proves nothing about CP1; it only
           creates the ``"2.2"``-governed work item CP1's own row needs a
           real target for.
        2. The discriminating step: ``/review-plan`` from
           ``AWAITING_LOCAL_PLAN_REVIEW`` on that same, now-``"2.2"``-
           governed work item -- exactly CP1's own added row. A crash here
           (an uncaught ``AssertionError``, not one of the closed,
           named exit codes) is exactly the pre-CP1 defect; CP1's fix is
           that this step instead launches a real ``/review-plan`` session
           and the work item durably reconciles to one of that row's own
           ``to_any_of`` outcomes.

        Deliberately does not drive a live run through the full
        ``IMPLEMENTING`` lifecycle to reach the five states
        :class:`Protocol22ImplementationReviewGatesTest` seeds directly --
        those are all ``declined``/``gate`` outcomes, so Controller never
        launches a worker at any of them regardless of how the target
        repository reached them, and seeding them is both sufficient and
        far cheaper than a real multi-round implementation round-trip."""
        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")

        with tempfile.TemporaryDirectory(prefix="controller-live-2-2-") as td:
            tmp_root = Path(td)
            target = tmp_root / "target"
            runtime_root = tmp_root / "runtime"
            _seed_target_2_2(target)

            state_path = target / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            pre_run_state = json.loads(state_path.read_text())
            pre_run_work_item_keys = set(pre_run_state.get("work_items", {}).keys())

            def _run_step(*, work_item_id: str | None) -> subprocess.CompletedProcess:
                argv = ["--runtime-dir", str(runtime_root)]
                if work_item_id is not None:
                    argv += ["--work-item", work_item_id]
                if _source_is_dirty():
                    argv.append("--allow-dirty-source")
                argv += ["--permission-mode", "bypassPermissions", "step", str(target)]

                env = dict(os.environ)
                env["PYTHONPATH"] = str(REPO_ROOT)
                env.pop("WORKFLOW_CONTROLLER_EXEC_HANDOFF", None)

                return subprocess.run(
                    [sys.executable, "-P", "-m", "controller", *argv],
                    cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=1800,
                )

            # Step 1: row 7's own NoWorkItemYet bootstrap. Passes identically
            # with or without CP1's fix -- it does not by itself prove
            # anything about the discriminating row below.
            proc_1 = _run_step(work_item_id=None)
            self.assertEqual(
                proc_1.returncode, 0,
                f"bootstrap `step` did not exit 0 (exit {proc_1.returncode})\n"
                f"stdout:\n{proc_1.stdout}\nstderr:\n{proc_1.stderr}",
            )

            state_after_bootstrap = json.loads(state_path.read_text())
            work_items_after_bootstrap = state_after_bootstrap["work_items"]
            new_work_item_keys = set(work_items_after_bootstrap.keys()) - pre_run_work_item_keys
            self.assertEqual(
                len(new_work_item_keys), 1,
                f"expected exactly one new work item, pre-run keys={pre_run_work_item_keys!r} "
                f"post-run keys={set(work_items_after_bootstrap)!r}",
            )
            work_item_id = next(iter(new_work_item_keys))
            entry_after_bootstrap = work_items_after_bootstrap[work_item_id]
            self.assertEqual(entry_after_bootstrap.get("governing_workflow_version"), "2.2")
            self.assertEqual(entry_after_bootstrap["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

            # Step 2: the discriminating row,
            # ("AWAITING_LOCAL_PLAN_REVIEW", "2.2", "/review-plan"). Named
            # explicitly by --work-item, rather than relying on
            # active_work_item_id/the single-non-terminal-item fallback, so
            # this step is unambiguously the same work item step 1 created.
            proc_2 = _run_step(work_item_id=work_item_id)
            self.assertNotIn(
                "Traceback (most recent call last)", proc_2.stderr,
                f"the discriminating /review-plan step crashed with an uncaught exception "
                f"(the pre-CP1 defect) instead of resolving to one of job.py's closed exit "
                f"codes:\nstdout:\n{proc_2.stdout}\nstderr:\n{proc_2.stderr}",
            )
            self.assertIn(
                proc_2.returncode, (EXIT_OK, EXIT_WORKER_FAILED, EXIT_INCOMPLETE),
                f"expected one of EXIT_OK/EXIT_WORKER_FAILED/EXIT_INCOMPLETE (CP6B's own "
                f"FINISHED/FAILED/INCOMPLETE outcomes for a launched action), got "
                f"{proc_2.returncode!r}\nstdout:\n{proc_2.stdout}\nstderr:\n{proc_2.stderr}",
            )

            state_after_review = json.loads(state_path.read_text())
            post_phase = state_after_review["work_items"][work_item_id]["phase"]
            self.assertIn(
                post_phase,
                {"AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW"},
                f"expected {work_item_id!r}'s phase to durably reconcile to one of "
                f"(\"AWAITING_LOCAL_PLAN_REVIEW\", \"2.2\", \"/review-plan\")'s own to_any_of, "
                f"got {post_phase!r}",
            )

            jobs_dir = runtime_root / "jobs"
            job_files = sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []
            self.assertEqual(len(job_files), 2, f"expected exactly two job records, got {job_files!r}")


if __name__ == "__main__":
    unittest.main()
