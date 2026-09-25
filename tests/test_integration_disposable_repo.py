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
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import identity, job, lock, routing, worker  # noqa: E402
from controller.cli import (  # noqa: E402
    EXIT_FAIL_CLOSED, EXIT_GATE, EXIT_INCOMPLETE, EXIT_INTERRUPTED, EXIT_OK, EXIT_WORKER_ACTIVE, EXIT_WORKER_FAILED,
)
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
#: The offline worker stand-in (``tests/fake_claude.py``).
_FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"

_FAKE_2_2_IDENTITY = ControllerIdentity(
    generation=1,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_COMMIT,
    source_commit="a" * 40,
    tree_digest="b" * 64,
    generation_source="head",
    pinned_at="2024-01-01T00:00:00Z", version=fixtures.CONTROLLER_VERSION,
)


class Protocol22ImplementationReviewGatesTest(unittest.TestCase):
    """A genuine ``"2.2"``-governed work item, seeded in turn at seven real
    on-disk states of the split implementation-review lifecycle -- proving
    automatic-lifecycle-orchestration CP4's decisions against **real**
    ``git``-committed target state, a real ``WORKFLOW_STATE.json``, and real
    ``.ai-review/`` ``MANIFEST.md``/``REVIEW_REQUEST.md``/``REVIEW_FEEDBACK.md``
    files written through ``tests.fixtures``'s own builders, with a coherent
    implementation bundle (real ``generation_head``, ``worktree_root`` and
    revisions) and a ledger wherever a state needs one:

    1. ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` -- automatic
       ``/review-implementation``: one invocation of a no-op fake worker,
       ``FAILED`` (no transition);
    2. ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW``, no manual
       verdict -- the genuine manual gate;
    3. the same phase, an admissible manual ``BLOCK`` -- the user-resolution
       gate;
    4. the same phase, a manual ``APPROVE`` but no local ``APPROVE`` in the
       ledger -- the inadmissible-verdict gate, naming the missing clause;
    4'. the same verdict with the local ``APPROVE`` recorded -- admissible,
       so ``/record-manual-implementation-review`` launches under the no-op
       fake (``FAILED``: nothing transitions);
    5. ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW``, an ``APPROVE`` on file
       but no ledger -- the ledger-incomplete gate, never
       ``/approve-review implementation``;
    5'. the same with a complete ledger -- names the user-only
       ``/approve-review implementation``, never as an automatic action;
    6. ``APPLYING_REVIEW_FEEDBACK`` after a local ``REVISE``, with ``HEAD``
       still recording ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` (the
       review-stage write uncommitted) and an admissible ``REVISE`` on file
       -- automatic ``/apply-implementation-review`` under the no-op fake,
       whose recorded task carries the pending-write addendum (CP4B);
    7. the same state after state 6's unverified job -- the relaunch-bound
       gate naming that job, under the fail-if-invoked fake.

    Never a live ``claude`` worker: every ``execute_step`` call names
    ``tests/fake_claude.py``. A state meant to launch runs it as a no-op
    (its diagnostic file proves exactly one invocation); every other state
    runs it fail-if-invoked (``FAKE_CLAUDE_REQUIRE_FILE`` naming a file that
    never exists), and its diagnostic file proves it never started.

    Calls ``controller.job.execute_step``/``controller.job.resume``
    directly against a real, disposable target repository -- the same
    "real git repo, synthetic (root-only) ``ManagedRepository``" fixture
    shape ``tests/test_job.py`` already establishes."""

    def _seed(self, root: Path, *, work_item_id: str, base_commit: str) -> dict:
        """The base, mostly-constant work-item entry a genuine ``"2.2"``
        item would carry by the time it first reaches
        ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` -- past ``PLANNING``/
        ``IMPLEMENTING``, so its own registry is fully checkpoint-complete
        -- with a real registry file plus a mapping file and an
        artifacts-declaration file at the paths a genuine work item's own
        entry would name (neither is read by any Controller code path), and
        a coherent implementation bundle for its implementation round 1."""
        registry_rel = f"docs/ai-workflow/registry/{work_item_id}-registry.json"
        mapping_rel = f"docs/ai-workflow/requirements/{work_item_id}-mapping.json"
        artifacts_rel = f"docs/ai-workflow/registry/{work_item_id}-artifacts.json"

        fixtures.write_target_registry(root, registry_rel, {
            "work_item_id": work_item_id,
            "checkpoints": [{"id": "CP1"}],
        })
        (root / mapping_rel).parent.mkdir(parents=True, exist_ok=True)
        (root / mapping_rel).write_text(
            json.dumps({"work_item_id": work_item_id, "requirements": []}, indent=2) + "\n"
        )
        (root / artifacts_rel).write_text(
            json.dumps({"work_item_id": work_item_id, "artifacts": []}, indent=2) + "\n"
        )
        # `generation_head` defaults to the live HEAD (`base_commit`: the
        # state writes below stay uncommitted, as every "2.2" review-stage
        # writer leaves them), and `worktree_root` to the target's own.
        fixtures.write_implementation_bundle(
            root, work_item_id, 1, reviewed_implementation_head=base_commit,
        )

        return {
            "work_item_type": "product",
            "work_item_kind": "product",
            "work_item_id": work_item_id,
            "governing_workflow_version": "2.2",
            "plan_revision": 1,
            "implementation_revision": 1,
            "reviewed_implementation_head": base_commit,
            "state_revision": 1,
            "checkpoints": {"CP1": {"status": "COMPLETE"}},
            "current_bundle_id": None,
            "last_completed_checkpoint_id": "CP1",
            "base_commit": base_commit,
            "parent_work_item_id": None,
            "registry_path": registry_rel,
            "mapping_path": mapping_rel,
            "plan_approval": {"status": "CURRENT"},
        }

    def test_seeded_states_launch_only_where_admissible_and_resume_never_self_approves(self) -> None:
        work_item_id = "wi-2-2-impl-review"

        with tempfile.TemporaryDirectory(prefix="controller-2-2-impl-review-") as td:
            tmp_root = Path(td)
            root = tmp_root / "target"
            runtime_root = tmp_root / "runtime"

            fixtures.build_target_git_repo(root)
            (root / "README.md").write_text("2.2 implementation-review fixture\n")
            (root / ".gitignore").write_text(".ai-review/\n")
            base_commit = fixtures.commit_all(root, "initial")

            managed_repo = fixtures.build_target_managed_repository(root)
            base_entry = self._seed(root, work_item_id=work_item_id, base_commit=base_commit)
            local_ledger = fixtures.implementation_review_ledger("c" * 64, local_bundle_id="b" * 64)
            complete_ledger = fixtures.implementation_review_ledger(
                "c" * 64, local_bundle_id="b" * 64, manual_bundle_id="b" * 64,
            )

            def _write_phase(phase: str, **fields) -> None:
                entry = {**base_entry, "phase": phase, **fields}
                fixtures.write_workflow_state(root, {
                    "schema_version": 1,
                    "active_work_item_id": work_item_id,
                    "work_items": {work_item_id: entry},
                })

            # Fail-if-invoked (automatic-lifecycle-orchestration CP3): a
            # state not meant to launch runs the offline fake, which
            # refuses to run because the file it requires never exists --
            # so no ordinary unit run can ever reach the real `claude`
            # binary through this test.
            never_created = tmp_root / "fail-if-invoked-never-created"

            def _run(state: str, *, launches: bool) -> dict:
                diag = tmp_root / f"diag-{state}.json"
                env = {"FAKE_CLAUDE_DIAG_FILE": str(diag)}
                if not launches:
                    env["FAKE_CLAUDE_REQUIRE_FILE"] = str(never_created)
                with unittest.mock.patch.dict(os.environ, env):
                    result = job.execute_step(
                        managed_repo, work_item_id=work_item_id, identity=_FAKE_2_2_IDENTITY,
                        runtime=runtime_root, claude_bin=str(_FAKE_CLAUDE),
                    )
                self.assertIsInstance(
                    result, dict,
                    "every one of these decisions launches, gates or declines -- never the "
                    "no-action (LEGACY_READY/MILESTONE_COMPLETE) class -- so execute_step must "
                    "return a JobRecord dict here, never a bare Decision",
                )
                self.assertEqual(result["selected_action"]["automatic"], launches)
                self.assertEqual(diag.exists(), launches, f"worker invocation at {state}")
                if launches:
                    self.assertIn("worker", result)
                    # The task is the command, plus the recorded addendum
                    # when the decision carried one (CP4B).
                    addendum = result["selected_action"]["task_addendum"]
                    command = result["selected_action"]["command"]
                    self.assertEqual(json.loads(diag.read_text())["argv"][1],
                                     command if addendum is None else f"{command}\n\n{addendum}")
                    # A no-op worker changes nothing, so nothing transitions.
                    self.assertEqual(result["status"], job.STATUS_FAILED)
                    self.assertFalse(result["transition_verified"])
                else:
                    self.assertNotIn("worker", result)
                # The human-only-approval boundary, at every state alike:
                # the Controller never selects /approve-review or
                # /accept-milestone as its own action.
                command = result["selected_action"]["command"]
                if command is not None:
                    self.assertNotIn("/approve-review", command)
                    self.assertNotIn("/accept-milestone", command)
                return result

            feedback_dir_rel = Path(".ai-review") / work_item_id / "feedback"

            def _manual_feedback(status: str) -> None:
                fixtures.write_review_feedback(root, feedback_dir_rel, fixtures.build_review_feedback_text(
                    status=status, reviewer_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                    reviewed_bundle_id="b" * 64, reviewed_base_commit=base_commit,
                    work_item=work_item_id, reviewed_content_id="c" * 64,
                ))

            # 1. AWAITING_LOCAL_IMPLEMENTATION_REVIEW, coherent bundle, no
            # ledger yet: automatic /review-implementation.
            with self.subTest(state="1/AWAITING_LOCAL_IMPLEMENTATION_REVIEW"):
                _write_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
                record_1 = _run("1", launches=True)
                self.assertEqual(
                    record_1["selected_action"]["command"], f"/review-implementation {work_item_id}",
                )
                self.assertIsNone(record_1["human_gate_pending"])

            # 2. AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW, no
            # current-round manual verdict: the genuine manual gate, naming
            # the values the user must hand over.
            with self.subTest(state="2/AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW/no-feedback"):
                _write_phase("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                             implementation_review_stages=local_ledger)
                record_2 = _run("2", launches=False)
                self.assertEqual(record_2["status"], job.STATUS_GATE_BLOCKED)
                gate_2 = record_2["human_gate_pending"]
                self.assertEqual(
                    gate_2["safe_resume_command"],
                    f"/record-manual-implementation-review {work_item_id}",
                )
                self.assertIn("MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", gate_2["what_is_required"])
                self.assertIn(f"bundle_id {'b' * 64}", gate_2["what_is_required"])
                self.assertIn(f"review_content_id {'c' * 64}", gate_2["what_is_required"])

            # 3. Same phase, an admissible Status: BLOCK on file: the BLOCK
            # gate; never ingested.
            with self.subTest(state="3/AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW/BLOCK"):
                _manual_feedback("BLOCK")
                record_3 = _run("3", launches=False)
                self.assertEqual(record_3["status"], job.STATUS_GATE_BLOCKED)
                gate_3 = record_3["human_gate_pending"]
                self.assertEqual(
                    gate_3["safe_resume_command"],
                    f"/record-manual-implementation-review {work_item_id}",
                )
                self.assertIn("blocked", gate_3["what_is_required"])

            # 4. Same phase, Status: APPROVE on file, but the ledger records
            # no local APPROVE: the inadmissible-verdict gate, naming the
            # missing local-stage clause -- never invents the approval.
            with self.subTest(state="4/AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW/APPROVE-inadmissible"):
                _manual_feedback("APPROVE")
                _write_phase("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
                record_4 = _run("4", launches=False)
                self.assertEqual(record_4["status"], job.STATUS_GATE_BLOCKED)
                gate_4 = record_4["human_gate_pending"]
                self.assertEqual(
                    gate_4["safe_resume_command"],
                    f"/record-manual-implementation-review {work_item_id}",
                )
                self.assertIn("not ingestible", gate_4["what_is_required"])
                self.assertIn("local approval", gate_4["what_is_required"])

            # 4'. The same verdict with the local APPROVE recorded:
            # admissible, so its ingestion launches.
            with self.subTest(state="4'/AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW/APPROVE-admissible"):
                _write_phase("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                             implementation_review_stages=local_ledger)
                record_4b = _run("4b", launches=True)
                self.assertEqual(
                    record_4b["selected_action"]["command"],
                    f"/record-manual-implementation-review {work_item_id}",
                )

            # 5. The reused terminal AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
            # with the APPROVE still on file, but no ledger: the
            # ledger-incomplete gate, not /approve-review implementation.
            with self.subTest(state="5/AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW/no-ledger"):
                _write_phase("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
                record_5 = _run("5", launches=False)
                self.assertEqual(record_5["status"], job.STATUS_GATE_BLOCKED)
                gate_5 = record_5["human_gate_pending"]
                self.assertEqual(
                    gate_5["safe_resume_command"], f"workflow-controller explain --work-item {work_item_id}",
                )
                self.assertIn("/approve-review implementation would refuse", gate_5["what_is_required"])
                self.assertIn("LOCAL_MODEL_IMPLEMENTATION_REVIEW", gate_5["what_is_required"])

            # 5'. A complete ledger plus the APPROVE feedback: names the
            # user-only /approve-review implementation -- as a gate.
            with self.subTest(state="5'/AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW/complete-ledger"):
                _write_phase("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                             implementation_review_stages=complete_ledger)
                record_5b = _run("5b", launches=False)
                self.assertEqual(record_5b["status"], job.STATUS_GATE_BLOCKED)
                gate_5b = record_5b["human_gate_pending"]
                self.assertEqual(
                    gate_5b["safe_resume_command"], f"/approve-review implementation {work_item_id}",
                )
                self.assertIn("user-only", gate_5b["what_is_required"])
                self.assertIn("/approve-review implementation", gate_5b["what_is_required"])

            # 6. APPLYING_REVIEW_FEEDBACK after a local REVISE: HEAD records
            # the round's AWAITING_LOCAL_IMPLEMENTATION_REVIEW (committed by
            # its generation-record commit), while the review-stage writer's
            # APPLYING_REVIEW_FEEDBACK stays uncommitted -- so the launched
            # task carries the pending-write addendum.
            with self.subTest(state="6/APPLYING_REVIEW_FEEDBACK/admissible-REVISE-pending-write"):
                _write_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
                fixtures.commit_paths(root, "Record implementation bundle generation",
                                      "docs/ai-workflow/WORKFLOW_STATE.json")
                _write_phase("APPLYING_REVIEW_FEEDBACK")
                fixtures.write_review_feedback(root, feedback_dir_rel, fixtures.build_review_feedback_text(
                    status="REVISE", reviewer_role="LOCAL_MODEL_IMPLEMENTATION_REVIEW",
                    reviewed_bundle_id="b" * 64, reviewed_base_commit=base_commit,
                    work_item=work_item_id, reviewed_content_id="c" * 64,
                ))
                record_6 = _run("6", launches=True)
                self.assertEqual(
                    record_6["selected_action"]["command"], f"/apply-implementation-review {work_item_id}",
                )
                addendum_6 = record_6["selected_action"]["task_addendum"]
                self.assertIsNotNone(addendum_6)
                self.assertIn("Controller note (pending review-stage state write)", addendum_6)
                self.assertIn("`HEAD` records `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`", addendum_6)
                self.assertIn(f"`Workflow-Work-Item: {work_item_id}`", addendum_6)
                self.assertEqual(json.loads((tmp_root / "diag-6.json").read_text())["argv"][1],
                                 f"/apply-implementation-review {work_item_id}\n\n{addendum_6}")
                self.assertEqual(record_6["pre_state"]["bundle_manifest_bundle_id"], "b" * 64)

            # 7. The same state after state 6's unverified job: the relaunch
            # bound gates instead of relaunching against the same bundle.
            with self.subTest(state="7/APPLYING_REVIEW_FEEDBACK/relaunch-bound"):
                record_7 = _run("7", launches=False)
                self.assertEqual(record_7["status"], job.STATUS_GATE_BLOCKED)
                gate_7 = record_7["human_gate_pending"]
                self.assertIn(f"job {record_6['job_id']}, ended FAILED", gate_7["what_is_required"])
                self.assertIn("review-bundle.tar.gz", gate_7["what_is_required"])
                self.assertEqual(
                    gate_7["safe_resume_command"], f"workflow-controller explain --work-item {work_item_id}",
                )

            # A `controller resume` pass across the same seeded states must
            # not crash or misclassify at any of them: every job record
            # above is already terminal (FAILED/GATE_BLOCKED), so `resume`
            # must report each back unchanged (never reconciled, never
            # relaunched, never rewritten as `resume_marked`), and none of
            # them may have become an automatic /approve-review or
            # /accept-milestone selection along the way.
            all_records = [
                record_1, record_2, record_3, record_4, record_4b, record_5, record_5b, record_6, record_7,
            ]
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
                    self.assertIn(reconciled["status"], (job.STATUS_FAILED, job.STATUS_GATE_BLOCKED))
                    self.assertEqual(reconciled["selected_action"]["automatic"],
                                     original["selected_action"]["automatic"])
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


# ---------------------------------------------------------------------------
# automatic-lifecycle-orchestration CP9: the live implementation-lifecycle
# run (3), the live single-agent probe (4) and the live concurrency drill
# (5). All three are opt-in behind CONTROLLER_LIVE_WORKER=1, like the two
# live tests above: they launch the real `claude` binary and spend money.
# ---------------------------------------------------------------------------

#: Where CP9's live tests create their disposable directories. The default
#: sits on the same filesystem as a checkout under the home directory (btrfs
#: on the development machine, where `/tmp` is tmpfs), so the drill checks the
#: lock probe's device source against a real worker (round 2's I1). It is
#: outside the checkout on purpose: a worker loads every `CLAUDE.md` above
#: its working directory, and the Controller repository's own must not reach
#: a worker in a disposable target.
LIVE_BASE_DIR_ENV = "CONTROLLER_LIVE_BASE_DIR"
#: `"1"` keeps each live test's disposable directory for inspection.
LIVE_KEEP_ENV = "CONTROLLER_LIVE_KEEP"
#: The outer bound, in seconds, on one live Controller subprocess. It is not
#: a Controller option -- every `run`/`step` below passes no `--timeout`, so
#: its workers wait without a limit -- and only keeps a wedged live test from
#: hanging forever.
LIVE_CLI_TIMEOUT_ENV = "CONTROLLER_LIVE_CLI_TIMEOUT"
_DEFAULT_LIVE_CLI_TIMEOUT = 6 * 3600

_LIVE_WORK_ITEM_ID = "live-greeting"
_LIVE_PLAN_PATH = f"docs/milestones/{_LIVE_WORK_ITEM_ID}-plan.md"
_LIVE_REGISTRY_PATH = f"docs/ai-workflow/registry/{_LIVE_WORK_ITEM_ID}-registry.json"
_LIVE_MAPPING_PATH = f"docs/ai-workflow/requirements/{_LIVE_WORK_ITEM_ID}-mapping.json"
_LIVE_ARTIFACTS_PATH = f"docs/ai-workflow/registry/{_LIVE_WORK_ITEM_ID}-artifacts.json"
#: The fixture confirmation literal `apply_plan_approval`'s record carries.
#: It names the work item and the stage, as `validate_user_confirmation`
#: requires; it is test setup for a disposable repository, never a
#: Controller capability.
_LIVE_PLAN_CONFIRMATION = (
    f"I approve the plan stage for {_LIVE_WORK_ITEM_ID} (disposable CP9 fixture)"
)

_LIVE_CHECKPOINTS = [
    {"id": "CP1", "name": "greet() and its unit test", "depends_on": [], "complexity": 1,
     "session_target": 1},
]
_LIVE_REQUIREMENTS = {
    "R1": {"description": "greet(name) returns 'hello, ' followed by name, with a unit test",
           "checkpoint_ids": ["CP1"]},
}

_LIVE_PLAN_BODY = """## Goal

Add a tiny greeting helper to this disposable repository, with a unit test. This work item is a
disposable fixture for the Workflow Controller's live implementation-lifecycle verification
(`tests/test_integration_disposable_repo.py` in the Controller repository). Keep every change as
small as the checkpoint allows.

## Non-goals

No packaging, no command-line interface, and no file beyond the ones CP1 names.

## Checkpoints

### CP1 -- `greet()` and its unit test

- Create `app/greeting.py` defining `greet(name: str) -> str`, which returns the string
  `"hello, "` followed by `name`, so `greet("controller") == "hello, controller"`.
- Create `app/test_greeting.py`, a standard-library `unittest` module that imports `greet` from
  `greeting` and asserts that example.
- Files: `app/greeting.py`, `app/test_greeting.py`, and this work item's narrative in
  `docs/ACTIVE_MILESTONE.md`.

## Verification

This repository has no Android or Gradle build. The `./gradlew ...` and
`connectedDebugAndroidTest` checks the Workflow commands name do not apply here and are not run.
The narrowest check and the full required verification are both:

    python3 -m unittest discover -s app -v
"""

_LIVE_MILESTONE = f"""# Active Milestone

## Milestone

{_LIVE_WORK_ITEM_ID} -- a disposable fixture work item for the Workflow Controller's live
implementation-lifecycle verification.

## Goal

Add `app/greeting.py` (`greet(name)`) and its unit test. The approved plan is
`{_LIVE_PLAN_PATH}`.

## Current checkpoint

None.

## Current blockers

None.

## Active plan

`{_LIVE_PLAN_PATH}`

## Functional review checklist

Empty.
"""

#: The frozen Workflow writer sequence that seeds the live item at
#: `IMPLEMENTING` with a `CURRENT` plan approval. It runs inside the
#: disposable target, importing the target's *own* installed
#: `scripts/workflow_state.py`/`workflow_fingerprint.py`, and mirrors the
#: order those scripts' own acceptance matrix drives the plan stage in
#: (`/milestone-plan`'s writes, the real plan-bundle generator, the two
#: plan-review stage writers, then `apply_plan_approval` and the approval
#: commit). No `WORKFLOW_STATE.json` is ever hand-written. It checks its own
#: result with the same functions `/milestone-implement` step 1a and
#: `/approve-review` step 7 use, and prints the approval evidence as JSON.
_IMPLEMENTING_SEED_SCRIPT = r'''
import datetime
import json
import subprocess
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "scripts"))
import workflow_fingerprint as fingerprint  # noqa: E402
import workflow_state as ws  # noqa: E402

args = json.loads(sys.argv[1])
wid = args["work_item_id"]
base_commit = args["base_commit"]
confirmation = args["user_confirmation"]
plan_path = args["plan_path"]
registry_path = args["registry_path"]
mapping_path = args["mapping_path"]
artifacts_path = args["artifacts_path"]
state_path = Path("docs/ai-workflow/WORKFLOW_STATE.json")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(*git_args):
    return subprocess.run(["git", *git_args], cwd=root, check=True, capture_output=True,
                          text=True).stdout


def tx(mutator):
    return ws.state_transaction(root, mutator)


def entry():
    return json.loads((root / state_path).read_text())["work_items"][wid]


config = json.loads((root / "docs/ai-workflow/WORKFLOW_CONFIG.json").read_text())

# /milestone-plan's writes.
tx(lambda state: ws.route_work_item(
    state, config, work_item_id=wid, work_item_type="product", work_item_kind="product",
    plan_path=plan_path, registry_path=registry_path, plan_revision=1, now=now(),
    mapping_path=mapping_path, base_commit=base_commit, repo_root=root,
))
registry = ws.generate_registry(wid, 1, args["checkpoints"])
mapping = ws.generate_mapping(wid, args["requirements"], registry=registry)
ws.write_registry_and_mapping(root, Path(registry_path), Path(mapping_path), registry, mapping)
declarations = ws.generate_artifacts_declarations(
    wid, plan_path, registry_path, mapping_path, work_item_type="product",
)
(root / artifacts_path).write_text(json.dumps(declarations, indent=2) + "\n")
(root / plan_path).parent.mkdir(parents=True, exist_ok=True)
(root / plan_path).write_text(
    f"# {wid} plan (Revision 1)\n\n{args['plan_body']}\n" + ws.render_registry_markdown(registry) + "\n"
)
tx(lambda state: ws.publish_plan_revision(state, wid, 1, now()))
git("add", "-N", "--", plan_path, registry_path, mapping_path, artifacts_path)

# The plan-stage bundle, through the real generator.
bundle = root / fingerprint.resolve_bundle_dir(root, wid, stage="plan")
bundle.mkdir(parents=True, exist_ok=True)
review_content_id, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
metadata = fingerprint.resolve_plan_stage_metadata(root, wid)
(bundle / "REVIEW_REQUEST.md").write_text(
    f"# Review request\n\nstage: plan\nwork item: {wid}\nreview_content_id: {review_content_id}\n"
)
(bundle / "TEST_RESULTS.md").write_text(
    f"stage: plan (revision {metadata.plan_revision})\nhead: {git('rev-parse', 'HEAD').strip()}\n\n"
    "No automated checks are required at the plan stage.\n"
)
(bundle / "CONTEXT_FILES.txt").write_text("")
subprocess.run(["./scripts/prepare-ai-review.sh", base_commit, "plan", wid], cwd=root, check=True,
               capture_output=True, text=True)
bundle_id = fingerprint.read_manifest_identifiers(bundle / "MANIFEST.md")["bundle_id"]

# The two plan-review stages: a local APPROVE, then a manual APPROVE on file.
feedback_dir = root / fingerprint.resolve_feedback_dir(root, wid)
feedback_dir.mkdir(parents=True, exist_ok=True)
(feedback_dir / "REVIEW_FEEDBACK.md").write_text(
    "# Review Decision\n\nStatus: APPROVE\n"
    "Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW\n"
    f"Reviewed bundle ID: {bundle_id}\nReviewed base commit: {base_commit}\nWork item: {wid}\n"
    f"Reviewed review content ID: {review_content_id}\n\n"
    "## Blocking findings\n\nNone (disposable CP9 fixture).\n"
)
tx(lambda state: ws.record_local_plan_review(
    state, wid, verdict="APPROVE", bundle_id=bundle_id, review_content_id=review_content_id,
    round=1, now=now(),
))
tx(lambda state: ws.record_manual_plan_review(
    state, wid, verdict="APPROVE", bundle_id=bundle_id, round=1, now=now(),
    current_review_content_id=review_content_id, feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
    feedback_review_content_id=review_content_id,
))

# The plan approval: apply_plan_approval with the fixture confirmation literal,
# committed with the plan-approval trailer pair as the message's final paragraph.
feedback = fingerprint.parse_review_feedback_binding_fields((feedback_dir / "REVIEW_FEEDBACK.md").read_text())
pre_entry = entry()
if not ws.plan_approval_gate_reachable(
    latest_round_status=feedback["status"],
    governing_workflow_version=pre_entry["governing_workflow_version"],
    plan_review_stages=pre_entry.get("plan_review_stages"),
    current_review_content_id=review_content_id,
):
    raise SystemExit("seed: the plan-approval gate is not reachable")
ws.validate_user_confirmation(confirmation, work_item_id=wid, stage="plan")
basis = ws.resolve_approval_basis(
    latest_round_status=feedback["status"], feedback_bundle_id=feedback["reviewed_bundle_id"],
    current_bundle_id=bundle_id, user_confirmation=confirmation, work_item_id=wid, stage="plan",
)
approval_now = now()
record = ws.build_approval_record(
    basis=basis, stage="plan", user_confirmation=confirmation, now=approval_now,
    reviewed_bundle_id=bundle_id, approved_review_content_id=review_content_id,
    review_content_manifest=projection["review_content_manifest"],
)
commit_plan = fingerprint.resolve_plan_stage_approval_commit_paths(root, wid, state_path)
tx(lambda state: ws.apply_plan_approval(state, wid, record, approval_now))
git("add", "--", *commit_plan.paths)
git("commit", "-q", "-m",
    f"Approve plan for {wid} (disposable CP9 fixture)\n\n"
    f"Workflow-Plan-Approval: {review_content_id}\nWorkflow-Work-Item: {wid}\n")
approval_commit = git("rev-parse", "HEAD").strip()

post_entry = entry()
ws.verify_post_approval_manifest_match(root, post_entry, stage="plan", base_commit=base_commit,
                                       commit=approval_commit)
if post_entry["phase"] != "IMPLEMENTING":
    raise SystemExit(f"seed: phase is {post_entry['phase']!r}, expected IMPLEMENTING")
if not ws.implementing_entry_reachable(root, post_entry, base_commit):
    raise SystemExit("seed: implementing_entry_reachable is False after the approval commit")
dirty = git("status", "--porcelain")
if dirty.strip():
    raise SystemExit(f"seed: the working tree is dirty after the approval commit:\n{dirty}")
print(json.dumps({
    "approval_commit": approval_commit, "plan_review_content_id": review_content_id,
    "plan_bundle_id": bundle_id, "basis": basis, "approval_paths": list(commit_plan.paths),
}))
'''


def _live_workspace(test: unittest.TestCase, prefix: str) -> Path:
    """A fresh disposable directory under :data:`LIVE_BASE_DIR_ENV` (default
    ``~/.cache/workflow-controller-live``), removed after the test unless
    :data:`LIVE_KEEP_ENV` is ``"1"``."""
    base = Path(os.environ.get(LIVE_BASE_DIR_ENV) or Path.home() / ".cache" / "workflow-controller-live")
    base.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    print(f"CP9 live workspace: {workspace}", flush=True)
    if os.environ.get(LIVE_KEEP_ENV) != "1":
        test.addCleanup(shutil.rmtree, workspace, True)
    return workspace


def _live_cli_timeout() -> float:
    return float(os.environ.get(LIVE_CLI_TIMEOUT_ENV) or _DEFAULT_LIVE_CLI_TIMEOUT)


def _controller_argv(runtime_root: Path, argv: list[str]) -> tuple[list[str], dict]:
    """The Controller exactly as an operator runs it from this checkout:
    ``python3 -P -m controller`` with ``PYTHONPATH`` naming the checkout (it
    re-execs from its own materialised snapshot), its own runtime root, and
    ``--allow-dirty-source`` only when ``controller/`` is dirty."""
    full = [sys.executable, "-P", "-m", "controller", "--runtime-dir", str(runtime_root)]
    if _source_is_dirty():
        full.append("--allow-dirty-source")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("WORKFLOW_CONTROLLER_EXEC_HANDOFF", None)
    return full + list(argv), env


def _run_controller(runtime_root: Path, argv: list[str], *, timeout: float | None = None,
                    ) -> subprocess.CompletedProcess:
    full, env = _controller_argv(runtime_root, argv)
    return subprocess.run(full, cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                          timeout=timeout if timeout is not None else _live_cli_timeout())


def _git(target: Path, *args: str, check: bool = True) -> str:
    return fixtures.run(["git", *args], cwd=target, check=check).stdout


def _git_head(target: Path) -> str:
    return _git(target, "rev-parse", "HEAD").strip()


def _target_python(target: Path, code: str, *args: str) -> str:
    """Run ``code`` inside ``target`` with the target's own installed
    ``scripts/`` first on ``sys.path``; return its stdout."""
    prelude = "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path.cwd() / 'scripts'))\n"
    result = fixtures.run([sys.executable, "-c", prelude + code, *args], cwd=target, check=False)
    if result.returncode != 0:
        raise AssertionError(
            f"target-side script failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _seed_implementing_target(target: Path) -> dict:
    """A disposable managed repository (a `VALIDATED_WORKFLOW_RELEASES` member
    installed by the real Manager, `"2.2"` activated) whose one work item,
    :data:`_LIVE_WORK_ITEM_ID`, is at `IMPLEMENTING` with a `CURRENT` plan
    approval and one outstanding checkpoint, produced by the frozen Workflow
    writer sequence :data:`_IMPLEMENTING_SEED_SCRIPT`."""
    _seed_target_2_2(target)
    (target / "docs" / "ACTIVE_MILESTONE.md").write_text(_LIVE_MILESTONE)
    base_commit = fixtures.commit_all(target, f"seed the {_LIVE_WORK_ITEM_ID} milestone narrative")
    result = fixtures.run(
        [sys.executable, "-c", _IMPLEMENTING_SEED_SCRIPT, json.dumps({
            "work_item_id": _LIVE_WORK_ITEM_ID, "base_commit": base_commit,
            "user_confirmation": _LIVE_PLAN_CONFIRMATION, "plan_path": _LIVE_PLAN_PATH,
            "registry_path": _LIVE_REGISTRY_PATH, "mapping_path": _LIVE_MAPPING_PATH,
            "artifacts_path": _LIVE_ARTIFACTS_PATH, "checkpoints": _LIVE_CHECKPOINTS,
            "requirements": _LIVE_REQUIREMENTS, "plan_body": _LIVE_PLAN_BODY,
        })],
        cwd=target, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"the IMPLEMENTING seed failed (exit {result.returncode}):\n"
                             f"{result.stdout}\n{result.stderr}")
    seed = json.loads(result.stdout.strip().splitlines()[-1])
    return {"work_item_id": _LIVE_WORK_ITEM_ID, "base_commit": base_commit, **seed}


def _state_entry(target: Path, work_item_id: str) -> dict:
    state = json.loads((target / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").read_text())
    return state["work_items"][work_item_id]


def _committed_entry(target: Path, commit: str, work_item_id: str) -> dict | None:
    result = fixtures.run(["git", "show", f"{commit}:docs/ai-workflow/WORKFLOW_STATE.json"],
                          cwd=target, check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout).get("work_items", {}).get(work_item_id)


def _commit_trailers(target: Path, commit: str) -> dict[str, str]:
    body = _git(target, "log", "-1", "--format=%B", commit)
    parsed = fixtures.run(["git", "interpret-trailers", "--parse"], cwd=target, input=body).stdout
    trailers: dict[str, str] = {}
    for line in parsed.splitlines():
        key, _, value = line.partition(":")
        trailers[key.strip()] = value.strip()
    return trailers


def _commit_paths(target: Path, commit: str) -> list[str]:
    return [p for p in _git(target, "show", "--format=", "--name-only", commit).splitlines() if p]


def _validate_generation_record(target: Path, commit: str, work_item_id: str) -> str | None:
    """``None`` when the target's own
    ``workflow_state.validate_bundle_generation_record_commit`` accepts
    ``commit``, else its refusal text."""
    code = (
        "import workflow_state as ws\n"
        "try:\n"
        "    ws.validate_bundle_generation_record_commit(Path.cwd(), sys.argv[1], sys.argv[2])\n"
        "except Exception as exc:\n"
        "    print(f'{type(exc).__name__}: {exc}')\n"
        "else:\n"
        "    print('OK')\n"
    )
    out = _target_python(target, code, commit, work_item_id).strip()
    return None if out == "OK" else out


def _commit_sequence(target: Path, start: str, end: str, work_item_id: str) -> list[dict]:
    """Every commit in ``start..end``, oldest first, classified: a
    generation-record commit (``T``, with its parent's committed phase and
    the target's own validation verdict), a state-only commit (only
    ``WORKFLOW_STATE.json``), or an ordinary commit."""
    commits = _git(target, "rev-list", "--reverse", f"{start}..{end}").split()
    sequence = []
    for commit in commits:
        trailers = _commit_trailers(target, commit)
        paths = _commit_paths(target, commit)
        own = _committed_entry(target, commit, work_item_id) or {}
        parent = _committed_entry(target, f"{commit}^", work_item_id) or {}
        entry = {
            "commit": commit,
            "subject": _git(target, "log", "-1", "--format=%s", commit).strip(),
            "trailers": trailers,
            "paths": paths,
            "committed_phase": own.get("phase"),
            "parent_committed_phase": parent.get("phase"),
        }
        if "Workflow-Bundle-Generation-Record" in trailers:
            entry["kind"] = "generation-record"
            entry["validation"] = _validate_generation_record(target, commit, work_item_id) or "OK"
        elif paths == ["docs/ai-workflow/WORKFLOW_STATE.json"]:
            entry["kind"] = "state-only"
        else:
            entry["kind"] = "ordinary"
        sequence.append(entry)
    return sequence


def _job_records(runtime_root: Path) -> list[dict]:
    """Every job record, in the order the Controller wrote them (the
    ``created_at`` second, then the file's modification time)."""
    jobs_dir = runtime_root / "jobs"
    paths = [p for p in jobs_dir.glob("*.json") if p.is_file()] if jobs_dir.is_dir() else []
    keyed = []
    for path in paths:
        record = json.loads(path.read_text())
        keyed.append(((record.get("created_at") or "", path.stat().st_mtime_ns), record))
    return [record for _, record in sorted(keyed, key=lambda item: item[0])]


def _worker_result_json(record: dict) -> dict | None:
    stdout_path = (record.get("worker") or {}).get("stdout_path")
    if not stdout_path or not Path(stdout_path).is_file():
        return None
    try:
        parsed = json.loads(Path(stdout_path).read_text())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _reported_models(worker_json: dict | None) -> list[str]:
    """The canonical model ids the worker's own JSON result reports under
    ``modelUsage`` (a key such as ``claude-opus-5-5[1m]`` carries its
    ``canonicalModel``)."""
    usage = (worker_json or {}).get("modelUsage") or {}
    models = []
    for key, value in usage.items():
        canonical = value.get("canonicalModel") if isinstance(value, dict) else None
        models.append(canonical or re.sub(r"\[.*\]$", "", key))
    return sorted(set(models))


def _elapsed_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (time.mktime(time.strptime(end, fmt)) - time.mktime(time.strptime(start, fmt)))


def _job_evidence(record: dict) -> dict:
    """What CP9's TEST_RESULTS.md records for one job."""
    worker_block = record.get("worker") or {}
    worker_json = _worker_result_json(record)
    usage = (worker_json or {}).get("modelUsage") or {}
    return {
        "job_id": record.get("job_id"),
        "command": (record.get("selected_action") or {}).get("command"),
        "task_addendum": (record.get("selected_action") or {}).get("task_addendum") is not None,
        "status": record.get("status"),
        "transition_verified": record.get("transition_verified"),
        "pre_phase": (record.get("pre_state") or {}).get("phase"),
        "post_phase": record.get("observed_phase_after"),
        "pre_head": (record.get("pre_state") or {}).get("target_head"),
        "worker_route": record.get("worker_route"),
        "session_id": worker_block.get("session_id"),
        "worker_exit_code": worker_block.get("exit_code"),
        "num_turns": worker_block.get("num_turns"),
        "permission_denials": worker_block.get("permission_denials"),
        "total_cost_usd": worker_block.get("total_cost_usd"),
        "worker_duration_ms": worker_block.get("duration_ms"),
        "record_wall_clock_seconds": _elapsed_seconds(record.get("created_at"), record.get("updated_at")),
        "reported_models": _reported_models(worker_json),
        "model_usage_keys": sorted(usage),
        "reconciliation_evidence": record.get("reconciliation_evidence"),
        "human_gate_pending": record.get("human_gate_pending"),
    }


def _print_evidence(tag: str, payload: dict) -> None:
    print(f"{tag} " + json.dumps(payload, sort_keys=True, default=str), flush=True)


def _required_outcome_violations(record: dict) -> list[str]:
    """CP9 (3)'s "Required outcome" and its model stop condition, for one
    launched job: ``FINISHED`` with ``transition_verified``, no permission
    denial, the built-in route for a named role (with ``single_agent`` for a
    review role), and the worker reporting the routed model."""
    problems = []
    job_id = record.get("job_id")
    if record.get("status") != job.STATUS_FINISHED or record.get("transition_verified") is not True:
        problems.append(f"{job_id}: status {record.get('status')!r}, transition_verified "
                        f"{record.get('transition_verified')!r} (stop condition: a FAILED job)")
    denials = (record.get("worker") or {}).get("permission_denials")
    if denials:
        problems.append(f"{job_id}: permission_denials {denials!r} (stop condition)")
    route = record.get("worker_route") or {}
    role = route.get("role")
    built_in = routing.ROLE_ROUTES.get(role)
    if built_in is None:
        problems.append(f"{job_id}: unknown role {role!r}")
        return problems
    if built_in.model is not None and (route.get("model"), route.get("effort")) != (
        routing.DEFAULT_MODEL, routing.DEFAULT_EFFORT,
    ):
        problems.append(f"{job_id}: role {role} routed to {route.get('model')!r}/{route.get('effort')!r}, "
                        f"expected {routing.DEFAULT_MODEL}/{routing.DEFAULT_EFFORT}")
    if route.get("single_agent") is not built_in.single_agent:
        problems.append(f"{job_id}: role {role} single_agent {route.get('single_agent')!r}")
    if role in (routing.REVIEW_IMPLEMENTATION, routing.REVIEW_PLAN) and route.get("single_agent") is not True:
        problems.append(f"{job_id}: review role {role} is not single-agent")
    if route.get("fresh_session") is not True:
        problems.append(f"{job_id}: fresh_session {route.get('fresh_session')!r}")
    if route.get("model") is not None:
        reported = _reported_models(_worker_result_json(record))
        if route["model"] not in reported:
            problems.append(f"{job_id}: routed {route['model']}, the worker reported {reported!r} "
                            f"(stop condition: a model other than the routed one)")
    return problems


def _manual_revise_verdict(target: Path, work_item_id: str) -> tuple[str, dict]:
    """The fixture manual ``REVISE`` CP9 (3) Phase B writes, acting as the
    human: bound to the current implementation bundle (``MANIFEST.md``'s
    ``bundle_id``) and content (the ledger's ``review_content_id``, which the
    local ``APPROVE`` recorded), naming one small, concrete finding in the
    checkpoint's own code."""
    code = (
        "import json\n"
        "import workflow_fingerprint as fp\n"
        "root = Path.cwd()\n"
        "wid = sys.argv[1]\n"
        "manifest = root / fp.resolve_bundle_dir(root, wid) / 'MANIFEST.md'\n"
        "print(json.dumps({\n"
        "    'bundle_id': fp.read_manifest_identifiers(manifest)['bundle_id'],\n"
        "    'feedback_dir': str(fp.resolve_feedback_dir(root, wid)),\n"
        "}))\n"
    )
    located = json.loads(_target_python(target, code, work_item_id).strip().splitlines()[-1])
    entry = _state_entry(target, work_item_id)
    fields = {
        "bundle_id": located["bundle_id"],
        "base_commit": entry["base_commit"],
        "review_content_id": (entry.get("implementation_review_stages") or {}).get("review_content_id"),
        # The resolver answers relative to the target's own root.
        "feedback_path": str(target / located["feedback_dir"] / "REVIEW_FEEDBACK.md"),
    }
    text = f"""# Review Decision

Status: REVISE
Reviewer role: MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW
Reviewed bundle ID: {fields['bundle_id']}
Reviewed base commit: {fields['base_commit']}
Work item: {work_item_id}
Reviewed review content ID: {fields['review_content_id']}

## Blocking findings

None.

## Important findings

- **M1 (`app/greeting.py`, `greet`): surrounding whitespace in `name` is kept.**
  `greet("  ada  ")` returns `"hello,   ada  "`. Strip leading and trailing whitespace from
  `name` before formatting, so that it returns `"hello, ada"`, and add a test for that case to
  `app/test_greeting.py`.

## Optional findings

None.

## Missing tests

- The whitespace case in M1.

## Architecture and maintainability concerns

None.

## Migration and data-integrity concerns

None.

## Usability concerns

None.

## Required acceptance criteria

- `greet("  ada  ") == "hello, ada"`, covered by a test in `app/test_greeting.py`.
- `python3 -m unittest discover -s app -v` passes.
"""
    return text, fields


def _greet_probe(target: Path) -> str:
    """What the target's own ``greet`` returns for a padded name (the Phase B
    finding's subject), or why it could not be called."""
    result = fixtures.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'app'); from greeting import greet; "
                               "print(repr(greet('  ada  ')))"],
        cwd=target, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else f"error: {result.stderr.strip()[-300:]}"


_LIVE = unittest.skipUnless(
    os.environ.get("CONTROLLER_LIVE_WORKER") == "1",
    "requires a live claude binary, network access and real spend -- set "
    "CONTROLLER_LIVE_WORKER=1 to opt in",
)


@_LIVE
class LiveImplementationLifecycleTest(unittest.TestCase):
    """CP9 (3): one `workflow-controller run` carries a `"2.2"` item from
    `IMPLEMENTING` to the manual gate (Phase A), then, after the fixture
    manual `REVISE`, a second `run` ingests it and drives a real
    `/apply-implementation-review` round and a fresh `/review-implementation`
    (Phase B). Every launched job must meet the "Required outcome"; every
    stop condition fails the test by name."""

    def _assert_launched_jobs(self, launched: list[dict]) -> None:
        problems = [problem for record in launched for problem in _required_outcome_violations(record)]
        self.assertEqual(problems, [], "CP9 (3) required outcome / stop conditions")

    def _assert_generation_records_well_formed(self, sequence: list[dict]) -> None:
        malformed = [c for c in sequence if c["kind"] == "generation-record" and c["validation"] != "OK"]
        self.assertEqual(malformed, [], "stop condition: a malformed generation-record commit")

    def test_run_carries_a_2_2_item_from_implementing_through_a_real_apply_round(self) -> None:
        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")
        workspace = _live_workspace(self, "cp9-lifecycle-")
        target = workspace / "target"
        runtime_root = workspace / "runtime"
        seed = _seed_implementing_target(target)
        work_item_id = seed["work_item_id"]
        _print_evidence("CP9_LIFECYCLE_SEED", {**seed, "workspace": str(workspace)})

        # Phase A: `run` with no --model/--effort/--timeout (and the default
        # permission mode), from IMPLEMENTING to the manual gate.
        head_a = _git_head(target)
        start = time.monotonic()
        proc_a = _run_controller(runtime_root, ["run", str(target)])
        wall_a = round(time.monotonic() - start, 1)
        jobs_a = _job_records(runtime_root)
        entry_a = _state_entry(target, work_item_id)
        sequence_a = _commit_sequence(target, head_a, _git_head(target), work_item_id)
        _print_evidence("CP9_LIFECYCLE_PHASE_A", {
            "exit_code": proc_a.returncode, "wall_clock_seconds": wall_a,
            "final_phase": entry_a["phase"], "implementation_revision": entry_a.get("implementation_revision"),
            "jobs": [_job_evidence(r) for r in jobs_a], "commits": sequence_a,
            "stdout_tail": proc_a.stdout[-3000:], "stderr_tail": proc_a.stderr[-3000:],
            "greet_padded": _greet_probe(target),
        })
        self.assertNotIn("Traceback (most recent call last)", proc_a.stderr)
        launched_a = [r for r in jobs_a if "worker" in r]
        self._assert_launched_jobs(launched_a)
        self._assert_generation_records_well_formed(sequence_a)
        self.assertNotEqual(
            entry_a["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            "stop condition: Phase A stopped at a local BLOCK, which leaves Phase B unreachable",
        )
        self.assertEqual(proc_a.returncode, EXIT_GATE, proc_a.stderr[-3000:])
        self.assertEqual(entry_a["phase"], "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(
            [r["job_id"] for r in launched_a
             if r["pre_state"]["phase"] == "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"],
            [], "stop condition: a worker launched at the manual gate",
        )
        roles_a = [r["worker_route"]["role"] for r in launched_a]
        self.assertEqual(roles_a[:2], [routing.MILESTONE_IMPLEMENT, routing.MILESTONE_IMPLEMENT_SELF_REVIEW])
        self.assertEqual(roles_a[-1], routing.REVIEW_IMPLEMENTATION)
        self.assertEqual(jobs_a[-1]["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("worker", jobs_a[-1])

        # Phase B: acting as the human, an admissible manual REVISE, then a
        # second `run`.
        verdict_text, verdict_fields = _manual_revise_verdict(target, work_item_id)
        feedback_path = Path(verdict_fields["feedback_path"])
        self.assertTrue(feedback_path.resolve().is_relative_to(target.resolve()), feedback_path)
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text(verdict_text)
        head_b = _git_head(target)
        start = time.monotonic()
        proc_b = _run_controller(runtime_root, ["run", str(target)])
        wall_b = round(time.monotonic() - start, 1)
        seen_a = {r["job_id"] for r in jobs_a}
        jobs_b = [r for r in _job_records(runtime_root) if r["job_id"] not in seen_a]
        entry_b = _state_entry(target, work_item_id)
        launched_b = [r for r in jobs_b if "worker" in r]
        apply_rounds = []
        for index, record in enumerate(launched_b):
            if record["worker_route"]["role"] != routing.APPLY_IMPLEMENTATION_REVIEW:
                continue
            later = launched_b[index + 1] if index + 1 < len(launched_b) else None
            end = later["pre_state"]["target_head"] if later is not None else _git_head(target)
            apply_rounds.append({
                "job_id": record["job_id"],
                "commits": _commit_sequence(target, record["pre_state"]["target_head"], end, work_item_id),
            })
        sequence_b = _commit_sequence(target, head_b, _git_head(target), work_item_id)
        _print_evidence("CP9_LIFECYCLE_PHASE_B", {
            "exit_code": proc_b.returncode, "wall_clock_seconds": wall_b, "verdict": verdict_fields,
            "final_phase": entry_b["phase"], "implementation_revision": entry_b.get("implementation_revision"),
            "jobs": [_job_evidence(r) for r in jobs_b], "apply_rounds": apply_rounds,
            "commits": sequence_b, "stdout_tail": proc_b.stdout[-3000:],
            "stderr_tail": proc_b.stderr[-3000:], "greet_padded": _greet_probe(target),
        })
        self.assertNotIn("Traceback (most recent call last)", proc_b.stderr)
        self._assert_launched_jobs(launched_b)
        self._assert_generation_records_well_formed(sequence_b)
        self.assertEqual(proc_b.returncode, EXIT_GATE, proc_b.stderr[-3000:])

        # Ingestion, the one launch at the manual phase: its REVISE write is
        # left uncommitted, so the apply job carries the pending-write addendum.
        self.assertGreaterEqual(
            len(launched_b), 3,
            f"Phase B must launch the ingestion, the apply round and a fresh review: {jobs_b[-1:]!r}",
        )
        ingest = launched_b[0]
        self.assertEqual(ingest["worker_route"]["role"], routing.RECORD_MANUAL_IMPLEMENTATION_REVIEW)
        self.assertEqual(ingest["pre_state"]["phase"], "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(ingest["observed_phase_after"], "APPLYING_REVIEW_FEEDBACK")
        self.assertEqual(
            [r["job_id"] for r in launched_b[1:]
             if r["pre_state"]["phase"] == "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"],
            [], "stop condition: a worker launched at the manual gate",
        )
        first_apply = launched_b[1]
        self.assertEqual(first_apply["worker_route"]["role"], routing.APPLY_IMPLEMENTATION_REVIEW)
        self.assertIsNotNone(first_apply["selected_action"]["task_addendum"])
        self.assertTrue(apply_rounds)
        round_commits = apply_rounds[0]["commits"]
        self.assertTrue(round_commits, "the apply round landed no commit")
        self.assertEqual(round_commits[0]["kind"], "state-only")
        self.assertEqual(round_commits[0]["committed_phase"], "APPLYING_REVIEW_FEEDBACK")
        self.assertEqual(
            {k: v for k, v in round_commits[0]["trailers"].items() if k.startswith("Workflow-")},
            {"Workflow-Work-Item": work_item_id},
        )
        records_t = [c for c in round_commits if c["kind"] == "generation-record"]
        self.assertEqual(len(records_t), 1, round_commits)
        self.assertEqual(round_commits[-1]["kind"], "generation-record")
        self.assertEqual(records_t[0]["parent_committed_phase"], "APPLYING_REVIEW_FEEDBACK")
        self.assertEqual(records_t[0]["committed_phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertIn(routing.REVIEW_IMPLEMENTATION,
                      [r["worker_route"]["role"] for r in launched_b[2:]],
                      "no fresh /review-implementation after the apply round")
        self.assertIn(entry_b["phase"], ("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                                         "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"))
        self.assertEqual(jobs_b[-1]["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("worker", jobs_b[-1])


#: The fixture skill for the probe's third path: a skill whose frontmatter
#: runs it in a forked subagent (`context: fork`), waited for in-line.
_FORK_PROBE_SKILL = """---
name: fork-probe
description: Controller single-agent probe fixture. Runs in a forked subagent and replies with a fixed marker.
context: fork
background: false
---

Reply with exactly this line and nothing else: FORK_PROBE_SUBAGENT_RAN
"""

#: A project command the probe runs on the review route, to show the task's
#: own slash command still expands with `Skill` disallowed.
_ECHO_PROBE_COMMAND = """---
description: Controller single-agent probe fixture command.
---

Reply with exactly this line and nothing else: COMMAND_EXPANDED $ARGUMENTS
"""

#: The name the CLI's session tool list gives a disallow-list entry, where
#: they differ: 2.1.281 lists the subagent tool as `Task`, and disallowing
#: `Agent` removes it.
_LISTED_TOOL_NAMES = {"Agent": ("Agent", "Task")}

_PROBE_REPORT = (
    "End your reply with exactly one line: `DELEGATION_RESULT: SPAWNED` if a subagent or workflow "
    "actually ran, `DELEGATION_RESULT: UNAVAILABLE` if the tool is not in your tool list, or "
    "`DELEGATION_RESULT: DENIED` if you called it and the call was refused. If the path is not "
    "available, do not look for another way to delegate and do not do the work yourself."
)

#: CP9 (4)'s three delegation paths: each task explicitly asks the worker to
#: delegate through one of them.
_PROBE_TASKS = {
    "Agent": (
        "This is a capability probe for a single-agent review route. Use the Agent tool (the "
        "subagent tool, formerly called Task) to spawn one general-purpose subagent whose only job "
        "is to reply with the word PONG. " + _PROBE_REPORT
    ),
    "Workflow": (
        "This is a capability probe for a single-agent review route. Use the Workflow tool to run "
        "a one-step workflow whose only step replies with the word PONG. " + _PROBE_REPORT
    ),
    "Skill": (
        "This is a capability probe for a single-agent review route. Use the Skill tool to invoke "
        "the project skill named fork-probe, which runs in a forked subagent, and report its reply. "
        + _PROBE_REPORT
    ),
}


def _session_transcript(session_id: str) -> tuple[Path | None, list[Path]]:
    """The worker session's own transcript under ``~/.claude/projects`` and
    any subagent transcripts beside it (``<session_id>/subagents/*.jsonl``)."""
    projects = Path.home() / ".claude" / "projects"
    matches = sorted(projects.glob(f"*/{session_id}.jsonl"))
    main = matches[0] if matches else None
    subagents = sorted(projects.glob(f"*/{session_id}/subagents/*.jsonl"))
    return main, subagents


def _transcript_tool_uses(path: Path | None) -> list[dict]:
    """Every ``tool_use`` in a transcript, with its ``tool_result``'s error
    flag and an excerpt, and whether any entry is a sidechain."""
    if path is None:
        return []
    uses: dict[str, dict] = {}
    order: list[str] = []
    for line in path.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                uses[block.get("id")] = {"name": block.get("name"), "input": block.get("input"),
                                         "sidechain": bool(entry.get("isSidechain"))}
                order.append(block.get("id"))
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
                result_content = block.get("content")
                text = result_content if isinstance(result_content, str) else json.dumps(result_content)
                uses[block["tool_use_id"]].update({"is_error": bool(block.get("is_error")),
                                                   "result_excerpt": text[:600]})
    return [uses[tool_id] for tool_id in order]


def _worker_streams(test_case: unittest.TestCase) -> dict:
    """``stdout_path``/``stderr_path`` for one direct ``worker.launch``: two
    fresh, empty files outside the target repository, removed by the
    test's cleanup (release-runtime-observability CP4)."""
    directory = Path(tempfile.mkdtemp(prefix="worker-streams-"))
    test_case.addCleanup(shutil.rmtree, directory, True)
    paths = {}
    for key, name in (("stdout_path", "worker.stdout"), ("stderr_path", "worker.stderr")):
        (directory / name).touch(mode=0o600)
        paths[key] = str(directory / name)
    return paths


def _stream_json_tool_list(cwd: Path, disallowed: tuple[str, ...]) -> list[str] | None:
    """The installed CLI's own tool list for a session, read from the
    ``system``/``init`` event of ``--output-format stream-json``, which the
    CLI emits before any model request; the process is killed as soon as it
    is read, so nothing is spent."""
    argv = [CLAUDE_BIN, "-p", "Reply OK.", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "auto", "--model", routing.DEFAULT_MODEL]
    if disallowed:
        argv += ["--disallowedTools", ",".join(disallowed)]
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    tools = None
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                tools = list(event.get("tools") or [])
                break
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
    return tools


@_LIVE
class LiveSingleAgentProbeTest(unittest.TestCase):
    """CP9 (4): workers launched through ``worker.launch`` with a review
    route, one per delegation path (``Agent``, ``Workflow``, and ``Skill``
    invoking a forked fixture skill). Each must find its path unavailable or
    denied; a probe that spawns a subagent by any path is a CP6 defect. The
    first run of this probe found one (the ``Skill`` path ran the forked
    skill), which added ``Skill`` to ``routing.SUBAGENT_TOOLS``; a fourth
    launch shows the review route still runs a project slash command."""

    def test_review_route_workers_cannot_delegate_by_any_path(self) -> None:
        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")
        workspace = _live_workspace(self, "cp9-probe-")
        repo = workspace / "probe-repo"
        fixtures.build_target_git_repo(repo)
        skill = repo / ".claude" / "skills" / "fork-probe" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(_FORK_PROBE_SKILL)
        command = repo / ".claude" / "commands" / "echo-probe.md"
        command.parent.mkdir(parents=True, exist_ok=True)
        command.write_text(_ECHO_PROBE_COMMAND)
        (repo / "README.md").write_text("single-agent probe fixture\n")
        fixtures.commit_all(repo, "single-agent probe fixture")

        route = routing.NO_OVERRIDES.resolve(routing.REVIEW_IMPLEMENTATION)
        self.assertTrue(route.single_agent)
        evidence: dict = {
            "route": route.to_record(), "disallowed_tools": list(route.disallowed_tools),
            "claude_version": fixtures.run([CLAUDE_BIN, "--version"], check=False).stdout.strip(),
            "tools_unrestricted": _stream_json_tool_list(repo, ()),
            "tools_review_route": _stream_json_tool_list(repo, route.disallowed_tools),
            "probes": {},
        }
        spawned = []
        for path_name, task in _PROBE_TASKS.items():
            result = worker.launch(
                task, cwd=repo, permission_mode=job.DEFAULT_PERMISSION_MODE, timeout=1800,
                claude_bin=CLAUDE_BIN, model=route.model, effort=route.effort,
                disallowed_tools=route.disallowed_tools, **_worker_streams(self),
            )
            transcript, subagent_transcripts = _session_transcript(result.session_id or "")
            tool_uses = _transcript_tool_uses(transcript)
            reply = result.result or ""
            reported = re.findall(r"DELEGATION_RESULT:\s*(SPAWNED|UNAVAILABLE|DENIED)", reply)
            delegating = [u for u in tool_uses if u["name"] in ("Agent", "Task", "Workflow", "Skill")]
            ran = bool(subagent_transcripts) or any(u.get("sidechain") for u in tool_uses) or any(
                "FORK_PROBE_SUBAGENT_RAN" in (u.get("result_excerpt") or "") or
                (u["name"] in ("Agent", "Task", "Workflow") and u.get("is_error") is False)
                for u in delegating
            )
            evidence["probes"][path_name] = {
                "outcome": result.outcome, "session_id": result.session_id,
                "permission_denials": result.permission_denials,
                "total_cost_usd": result.total_cost_usd, "duration_ms": result.duration_ms,
                "reported_models": _reported_models(result.raw_json),
                "reported": reported[-1] if reported else None, "reply_tail": reply[-800:],
                "transcript": str(transcript) if transcript else None,
                "subagent_transcripts": [str(p) for p in subagent_transcripts],
                "delegating_tool_uses": delegating, "subagent_ran": ran,
            }
            if ran:
                spawned.append(path_name)

        # The review route still runs the task's own slash command: the CLI
        # expands a `-p` prompt's project command itself, `Skill` disallowed.
        expanded = worker.launch(
            "/echo-probe arg-one", cwd=repo, permission_mode=job.DEFAULT_PERMISSION_MODE, timeout=1800,
            claude_bin=CLAUDE_BIN, model=route.model, effort=route.effort,
            disallowed_tools=route.disallowed_tools, **_worker_streams(self),
        )
        evidence["slash_command"] = {
            "outcome": expanded.outcome, "session_id": expanded.session_id,
            "result": expanded.result, "permission_denials": expanded.permission_denials,
            "total_cost_usd": expanded.total_cost_usd,
        }
        _print_evidence("CP9_SINGLE_AGENT_PROBE", evidence)

        # The CLI's own tool list: each disallowed name is one the installed
        # CLI offers unrestricted (under the name it lists, `Task` for
        # `Agent`), and none of those names is offered on the review route.
        unrestricted = evidence["tools_unrestricted"] or []
        restricted = evidence["tools_review_route"] or []
        for name in route.disallowed_tools:
            listed = _LISTED_TOOL_NAMES.get(name, (name,))
            self.assertTrue(set(listed) & set(unrestricted), f"{name} is not a tool of the installed CLI")
            self.assertFalse(set(listed) & set(restricted), f"{name} is still offered on the review route")
        self.assertEqual(expanded.outcome, worker.SUCCESS)
        self.assertIn("COMMAND_EXPANDED arg-one", expanded.result or "")
        for path_name, probe in evidence["probes"].items():
            self.assertIsNotNone(probe["session_id"], f"{path_name}: no session id")
            self.assertIsNotNone(probe["transcript"], f"{path_name}: no session transcript found")
        self.assertEqual(spawned, [], "CP6 defect: a review-route worker spawned a subagent by these paths")


@_LIVE
class LiveConcurrencyDrillTest(unittest.TestCase):
    """CP9 (5): ``step`` on a disposable repository at a phase whose worker
    takes real time; ``SIGKILL`` the Controller; ``step``/``resume`` exit 45
    and ``explain`` reports the lock held while the worker lives; once every
    holder has exited, ``explain`` reports it free, ``resume`` reconciles
    the record from its real outcome, and the next ``step`` proceeds."""

    def test_sigkilled_controller_leaves_the_worker_holding_the_worktree(self) -> None:
        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")
        self.assertIsNotNone(shutil.which("fuser"), "fuser is required for this drill")
        workspace = _live_workspace(self, "cp9-drill-")
        target = workspace / "target"
        runtime_root = workspace / "runtime"
        seed = _seed_implementing_target(target)
        work_item_id = seed["work_item_id"]
        git_dir = lock.resolve_git_dir(target)
        evidence: dict = {
            "workspace": str(workspace), "seed": seed, "git_dir": str(git_dir),
            "pid_namespace": os.readlink("/proc/self/ns/pid"),
            "filesystem_target": fixtures.run(["findmnt", "-no", "SOURCE,FSTYPE,MAJ:MIN", "-T", str(target)],
                                              check=False).stdout.strip(),
            "filesystem_checkout": fixtures.run(["findmnt", "-no", "SOURCE,FSTYPE,MAJ:MIN", "-T",
                                                 str(REPO_ROOT)], check=False).stdout.strip(),
        }

        # 1. `step` at IMPLEMENTING: the checkpoint worker takes real time.
        full, env = _controller_argv(runtime_root, ["step", str(target)])
        with open(workspace / "step-1.stdout", "w") as out, open(workspace / "step-1.stderr", "w") as err:
            controller_proc = subprocess.Popen(full, cwd=REPO_ROOT, env=env, stdout=out, stderr=err)
        record = None
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline and controller_proc.poll() is None:
            records = _job_records(runtime_root)
            if records and "worker_process" in records[-1]:
                record = records[-1]
                break
            time.sleep(1)
        self.assertIsNotNone(record, f"no worker_process was recorded (controller exit {controller_proc.poll()})")
        worker_pid = record["worker_process"]["pid"]
        worker_pgid = record["worker_process"]["pgid"]
        time.sleep(20)

        # 2. SIGKILL the Controller.
        os.kill(controller_proc.pid, signal.SIGKILL)
        controller_proc.wait()
        evidence["controller"] = {"pid": controller_proc.pid, "returncode": controller_proc.returncode}
        evidence["worker_process"] = record["worker_process"]
        evidence["job_id"] = record["job_id"]
        self.assertTrue(_group_alive(record["worker_process"]), "the worker died with its Controller")

        # 3. While the worker lives: `fuser` names the holders, `explain`
        # reports the lock held, and `step` and `resume` exit 45.
        holders: dict[int, dict] = {}
        first_sample = _sample_holders(git_dir, worker_pid, holders)
        evidence["fuser_first_sample"] = first_sample.stdout + first_sample.stderr
        explain_held = _run_controller(runtime_root, ["explain", str(target)], timeout=300)
        _sample_holders(git_dir, worker_pid, holders)
        step_held = _run_controller(runtime_root, ["step", str(target)], timeout=300)
        _sample_holders(git_dir, worker_pid, holders)
        resume_held = _run_controller(runtime_root, ["resume", str(target)], timeout=300)
        evidence["while_live"] = {
            "explain_exit": explain_held.returncode,
            "explain_lock_line": [l for l in explain_held.stdout.splitlines() if "lifecycle lock" in l],
            "step_exit": step_held.returncode, "step_stderr": step_held.stderr.strip(),
            "resume_exit": resume_held.returncode, "resume_stderr": resume_held.stderr.strip(),
            "step_names_recorded_worker": f"pid {worker_pid}, process group {worker_pgid}" in step_held.stderr,
            "step_names_other_holders": "not a recorded worker" in step_held.stderr,
        }

        # 4. Let the worker finish, including every holder `fuser` names,
        # sampled every second until the group has ended and nothing holds it.
        samples = 3
        deadline = time.monotonic() + _live_cli_timeout()
        while time.monotonic() < deadline:
            fuser = _sample_holders(git_dir, worker_pid, holders)
            samples += 1
            if not _group_alive(record["worker_process"]) and fuser.returncode != 0:
                break
            time.sleep(1)
        evidence["fuser_samples"] = samples
        evidence["fuser_holders"] = holders
        self.assertFalse(_group_alive(record["worker_process"]), "the worker's group never ended")

        # 5. Every holder gone: free, `resume` reconciles, the next `step` proceeds.
        explain_free = _run_controller(runtime_root, ["explain", str(target)], timeout=300)
        resume_after = _run_controller(runtime_root, ["--json", "resume", str(target)], timeout=300)
        reconciled = next((r for r in _job_records(runtime_root) if r["job_id"] == record["job_id"]), {})
        entry_after_worker = _state_entry(target, work_item_id)
        step_next = _run_controller(runtime_root, ["step", str(target)])
        next_records = [r for r in _job_records(runtime_root) if r["job_id"] != record["job_id"]]
        evidence["after_exit"] = {
            "explain_lock_line": [l for l in explain_free.stdout.splitlines() if "lifecycle lock" in l],
            "resume_exit": resume_after.returncode, "resume_stderr": resume_after.stderr.strip(),
            "reconciled_status": reconciled.get("status"),
            "reconciled_transition_verified": reconciled.get("transition_verified"),
            "reconciled_observed_phase_after": reconciled.get("observed_phase_after"),
            "reconciliation_evidence": reconciled.get("reconciliation_evidence"),
            "phase_after_worker": entry_after_worker["phase"],
            "checkpoint_after_worker": entry_after_worker.get("checkpoints"),
            "next_step_exit": step_next.returncode, "next_step_stderr_tail": step_next.stderr[-2000:],
            "next_jobs": [_job_evidence(r) for r in next_records],
        }
        _print_evidence("CP9_CONCURRENCY_DRILL", evidence)

        self.assertIn("lifecycle lock: held", explain_held.stdout)
        self.assertEqual(step_held.returncode, EXIT_WORKER_ACTIVE, step_held.stderr)
        self.assertEqual(resume_held.returncode, EXIT_WORKER_ACTIVE, resume_held.stderr)
        self.assertTrue(evidence["while_live"]["step_names_recorded_worker"], step_held.stderr)
        self.assertIn(worker_pid, holders, "fuser never named the recorded worker")
        self.assertIn("lifecycle lock: free", explain_free.stdout)
        self.assertIn(resume_after.returncode, (EXIT_OK, EXIT_INTERRUPTED), resume_after.stderr)
        self.assertIn(reconciled.get("status"), job.TERMINAL_STATUSES)
        if (entry_after_worker["phase"] == "SELF_REVIEWING_IMPLEMENTATION"
                and (entry_after_worker.get("checkpoints") or {}).get("CP1", {}).get("status") == "COMPLETE"):
            # The worker's real outcome was a completed, committed checkpoint.
            self.assertEqual(reconciled.get("status"), job.STATUS_FINISHED, reconciled)
        self.assertNotIn(step_next.returncode, (EXIT_FAIL_CLOSED, EXIT_WORKER_ACTIVE), step_next.stderr[-2000:])
        launched_next = [r for r in next_records if "worker" in r or "worker_process" in r]
        self.assertTrue(launched_next, "the next `step` launched nothing")

    def test_whether_a_bash_tool_child_of_the_worker_inherits_the_lock_descriptor(self) -> None:
        """Step 3's open question, settled directly: a real worker launched
        as ``controller.job`` launches one (the lifecycle lock's descriptor in
        ``pass_fds``) runs one long Bash-tool command that lists its own
        descriptors, while ``fuser`` is sampled. Recorded either way: a
        Bash-tool child holding the descriptor is correct behavior (the lock
        then lasts until it exits), not a defect."""
        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")
        workspace = _live_workspace(self, "cp9-inherit-")
        repo = workspace / "repo"
        fixtures.build_target_git_repo(repo)
        (repo / "README.md").write_text("descriptor-inheritance fixture\n")
        fixtures.commit_all(repo, "descriptor-inheritance fixture")
        git_dir = lock.resolve_git_dir(repo)
        task = (
            "Run exactly this one Bash command, once, and then reply with its complete output "
            "verbatim and nothing else: "
            "`for f in /proc/$$/fd/*; do echo \"$f -> $(readlink $f)\"; done; sleep 12; echo done`"
        )
        spawned: dict = {}
        outcome: dict = {}
        holders: dict[int, dict] = {}
        streams = _worker_streams(self)
        with lock.acquire_lifecycle_lock(repo) as held:
            def _launch() -> None:
                outcome["result"] = worker.launch(
                    task, cwd=repo, permission_mode=job.DEFAULT_PERMISSION_MODE, timeout=900,
                    claude_bin=CLAUDE_BIN, pass_fds=(held.fd,),
                    on_spawn=lambda process: spawned.setdefault("process", process),
                    model=routing.DEFAULT_MODEL, effort="low", **streams,
                )
            thread = threading.Thread(target=_launch)
            thread.start()
            while thread.is_alive():
                worker_pid = spawned["process"].pid if "process" in spawned else -1
                _sample_holders(git_dir, worker_pid, holders)
                time.sleep(0.5)
            thread.join()
        result = outcome["result"]
        worker_pid = spawned["process"].pid
        for pid, holder in holders.items():  # a sample may precede `on_spawn`
            holder["is_recorded_worker"] = pid == worker_pid
            holder["descends_from_worker"] = worker_pid in holder["ancestry"]
        reply = result.result or ""
        bash_lines = [line for line in reply.splitlines() if " -> " in line]
        bash_child_holds = any(line.rstrip().endswith(str(git_dir)) for line in bash_lines)
        descendant_holders = {pid: h for pid, h in holders.items() if worker_pid in h["ancestry"]}
        evidence = {
            "workspace": str(workspace), "git_dir": str(git_dir), "test_pid": os.getpid(),
            "worker_pid": worker_pid, "outcome": result.outcome, "session_id": result.session_id,
            "total_cost_usd": result.total_cost_usd, "permission_denials": result.permission_denials,
            "bash_child_fd_listing": bash_lines, "bash_child_holds_descriptor": bash_child_holds,
            "fuser_holders": holders, "descendant_holders": descendant_holders,
        }
        _print_evidence("CP9_DESCRIPTOR_INHERITANCE", evidence)
        self.assertEqual(result.outcome, worker.SUCCESS, reply[-1000:])
        self.assertTrue(bash_lines, f"the worker did not report the command's output: {reply[-1000:]}")
        self.assertIn(worker_pid, holders, "fuser never named the worker")
        # The two instruments must agree: the child's own listing and fuser.
        self.assertEqual(bash_child_holds, bool(descendant_holders), evidence)


def _sample_holders(path: Path, worker_pid: int, holders: dict[int, dict]) -> subprocess.CompletedProcess:
    """One ``fuser -v path`` sample, adding each holder not seen before to
    ``holders``. ``fuser`` prints the pids on stdout and the verbose table
    (user, access, command, in the same order, without the pid) on stderr."""
    fuser = fixtures.run(["fuser", "-v", str(path)], check=False)
    pids = [int(p) for p in re.findall(r"\d+", fuser.stdout)]
    rows = [m.groups() for m in (re.match(r"^\s*(?:\S+:\s+)?\S+\s+([cefFrm.]{5})\s+(\S+)\s*$", line)
                                  for line in fuser.stderr.splitlines()) if m]
    for index, pid in enumerate(pids):
        if pid in holders:
            continue
        access, command = rows[index] if index < len(rows) else (None, None)
        ancestry = _proc_ancestry(pid)
        holders[pid] = {"access": access, "command": command, "is_recorded_worker": pid == worker_pid,
                        "cmdline": _proc_cmdline(pid), "ancestry": ancestry,
                        "descends_from_worker": worker_pid in ancestry,
                        "first_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return fuser


def _group_alive(worker_process: dict) -> bool:
    """Whether the recorded worker's group still has a running process, by
    the Controller's own process test (which never counts a zombie)."""
    answer = worker.process_test(
        worker_process["pid"], worker_process["pgid"], worker_process.get("start_ticks"),
    )
    return answer.answer != worker.NOT_LIVE


def _proc_cmdline(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return None


@_LIVE
class LiveHarnessContractProbeTest(unittest.TestCase):
    """`workflow-controller-worker-lifecycle-ownership` CP1: the opt-in live
    contract probe. It re-runs P3-P6 and P8-P11 against the installed
    ``claude`` with ``tests/harness_contract/capture.py``'s own probe
    definitions and argv (haiku, the production argv of plan A), writing the
    captures to a scratch directory -- never over the committed fixtures --
    and checks the same harness sequences and the recogniser's evidence.
    CP8 re-runs both P12 probes through it as the final re-measurement.

    A bracket fact that does not hold here (a fire not enclosed in exactly
    one ``started``/``completed`` pair with one ``command_uuid``, a pair
    around a non-fire turn, or a P12 count other than 0 and 1) is the plan's
    amendment trigger, not an implementation-time judgement."""

    STREAMING_PROBES = ("p3_streaming_background_bash", "p4_monitor", "p5_p11_wakeup_fires",
                        "p6_p10_slash_command", "p8_task_stop", "p8_monitor_timeout", "p9_wakeup_cancel",
                        "p11_subagent_handback")
    P12_PROBES = ("p12_stop_inside_fire_single", "p12_stop_inside_fire_nested")

    def _capture(self, names) -> dict[str, list[dict]]:
        from concurrent.futures import ThreadPoolExecutor

        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")
        sys.path.insert(0, str(REPO_ROOT / "tests" / "harness_contract"))
        import capture

        out = Path(tempfile.mkdtemp(prefix="wlo-live-probe-"))
        self.addCleanup(shutil.rmtree, out, True)
        capture.HERE = out  # every write_fixture goes here, never over the committed fixtures
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            metas = dict(zip(names, pool.map(lambda n: capture.run_probe(n, out), names)))
        streams = {}
        for name in names:
            print(f"[live-probe] {name}: exit {metas[name]['returncode']}, done_at {metas[name]['done_at']}",
                  file=sys.stderr)
            with open(out / f"{name}.jsonl") as fh:
                streams[name] = [json.loads(line) for line in fh if line.strip()]
        return streams

    def test_streaming_contract_against_the_installed_claude(self) -> None:
        from tests import test_fake_claude_contract as contract

        def harness_sequence(events):
            return [item for item in contract.projection(events) if item[0] not in ("assistant", "user")]

        streams = self._capture(self.STREAMING_PROBES)
        for name, events in streams.items():
            with self.subTest(probe=name):
                self.assertEqual(harness_sequence(events), harness_sequence(contract.load(name)))
        # Fact 1: each wakeup fire is enclosed in exactly one pair around one turn.
        p5 = streams["p5_p11_wakeup_fires"]
        pairs = contract.brackets(p5)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(sum(1 for e in p5 if e.get("type") == "command_lifecycle"), 4)
        self.assertNotEqual(pairs[0][2], pairs[1][2])
        for start, end, _ in pairs:
            inside = p5[start + 1:end]
            self.assertEqual(sum(1 for e in inside if e.get("type") == "result"), 1)
            self.assertNotIn("origin", next(e for e in inside if e.get("type") == "result"))
            first = next(i for i, e in enumerate(inside) if e.get("type") != "system")
            self.assertEqual(inside[first]["type"], "assistant")
        for event in p5:
            if event.get("type") == "user":
                self.assertNotIn("P11 fire", json.dumps(event))
        # Fact 2: no other probe holds a pair; notification turns carry
        # task-notification and lie in no bracket.
        for name, events in streams.items():
            if name != "p5_p11_wakeup_fires":
                self.assertFalse([e for e in events if e.get("type") == "command_lifecycle"], name)
        for name in ("p3_streaming_background_bash", "p4_monitor", "p8_monitor_timeout", "p11_subagent_handback"):
            for result in [e for e in streams[name] if e.get("type") == "result"][1:]:
                self.assertEqual(result.get("origin"), {"kind": "task-notification"}, name)
        # Fact 3: a cancelled wakeup never fires.
        p9 = streams["p9_wakeup_cancel"]
        self.assertEqual(sum(1 for e in p9 if e.get("type") == "result"), 1)

    def test_p12_stop_inside_fire_counts(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "tests" / "harness_contract"))
        import p12_admission

        streams = self._capture(self.P12_PROBES)
        for name, events in streams.items():
            with self.subTest(probe=name):
                admission = p12_admission.admit(name, events)
                if admission.verdict != p12_admission.ADOPT:
                    self.fail(f"{name}: the model did not follow the prompt (a recapture, not a harness "
                              f"observation): {admission.reasons}")
                self.assertEqual(p12_admission.gate_deviations(name, events), [],
                                 "a P12 deviation is the plan-amendment trigger")


def _proc_ancestry(pid: int) -> list[int]:
    chain = []
    current = pid
    for _ in range(64):
        try:
            stat = Path(f"/proc/{current}/stat").read_text()
        except OSError:
            break
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        if ppid <= 0:
            break
        chain.append(ppid)
        current = ppid
    return chain


if __name__ == "__main__":
    unittest.main()
