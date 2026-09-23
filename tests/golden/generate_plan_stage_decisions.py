"""Generator for ``tests/golden/plan_stage_decisions.json`` -- the
plan-stage decision golden (``docs/ai-workflow/
CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md``, CP1's first act and
CP3 item 1).

The golden pins every plan-stage decision ``controller.evidence.decide``
makes, so that the general automatic-dispatch rule (CP3) and every other
change of the milestone can be shown to leave the plan stage unchanged. It
was generated **before any other change of that milestone**, from the base
commit's unchanged ``controller/decision.py``/``controller/evidence.py``.

**Coverage.** The full evidence matrix the existing plan-stage tests use
(``tests/test_evidence.py``'s plan-stage classes: the local, manual and
``"1"`` external review verdicts, the admissibility clauses, the
stale-bundle recovery variants and the ``REJECTED`` marker), applied to
every plan-stage phase at every governing version at which a Workflow
writer can reach it (:data:`PHASE_VERSIONS`). Every scenario runs against
every one of those ``(phase, version)`` combinations, so a phase whose
decision reads no evidence is also pinned as evidence-independent.

**Normalisation** (:func:`normalise`, shared by this generator and
``tests/test_golden_plan_stage_decisions.py``): the temporary target root,
wherever it appears, becomes ``<ROOT>``; every 40-hex-digit token (live
``HEAD``/``generation_head``/base SHAs, which vary run to run because
``fixtures.commit_all`` does not pin dates) becomes ``<SHA>``. Fixture
constant ids of another length (``"b" * 64`` and similar) stay literal. The
file is canonical JSON (``sort_keys``, indent 2, trailing newline).

Run ``python3 tests/golden/generate_plan_stage_decisions.py`` to rewrite
the golden, or with ``--check`` to compare without writing. Rewriting it is
a deliberate act: the golden's whole value is that it does not move.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from controller import evidence  # noqa: E402
from tests import fixtures  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "plan_stage_decisions.json"

WORK_ITEM_ID = "wi-1"

#: Every plan-stage phase, at every governing version at which a Workflow
#: writer can reach it (CP3 item 1's coverage list).
PHASE_VERSIONS: tuple[tuple[str, str], ...] = (
    ("PLANNING", "1"), ("PLANNING", "2.1"), ("PLANNING", "2.2"),
    ("AWAITING_PLAN_APPROVAL", "1"), ("AWAITING_PLAN_APPROVAL", "2.1"),
    ("AWAITING_PLAN_APPROVAL", "2.2"),
    ("AMENDING_PLAN", "1"), ("AMENDING_PLAN", "2.1"), ("AMENDING_PLAN", "2.2"),
    ("REVISING_PLAN", "2.1"), ("REVISING_PLAN", "2.2"),
    ("AWAITING_LOCAL_PLAN_REVIEW", "2.1"), ("AWAITING_LOCAL_PLAN_REVIEW", "2.2"),
    ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.1"),
    ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.2"),
    ("AWAITING_EXTERNAL_PLAN_REVIEW", "1"),
)

_BUNDLE = Path(".ai-review") / WORK_ITEM_ID / "current"
_SCOPED_FEEDBACK = Path(".ai-review") / WORK_ITEM_ID / "feedback"
_FLAT_FEEDBACK = Path(".ai-review") / "feedback"
_AUTHOR_FILES = ("REVIEW_REQUEST.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt")

_LOCAL_APPROVE_LEDGER = {
    "review_content_id": "c" * 64,
    "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
}


# ---------------------------------------------------------------------------
# Scenario building blocks.
# ---------------------------------------------------------------------------


def _manifest(root: Path, head: str, *, plan_revision: int = 1, generation_head: str | None = None) -> None:
    fixtures.write_manifest(
        root, _BUNDLE,
        fixtures.build_plan_manifest_text(
            WORK_ITEM_ID, plan_revision,
            generation_head=head if generation_head is None else generation_head,
        ),
    )


def _author_files(root: Path, *, text: str = "previous round\n", names=_AUTHOR_FILES) -> None:
    for name in names:
        path = root / _BUNDLE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _feedback(root: Path, feedback_dir: Path, **fields) -> None:
    fixtures.write_review_feedback(root, feedback_dir, fixtures.build_review_feedback_text(**fields))


def _quarantine(root: Path, name: str, *, mtime_ns: int | None = None, files=("REVIEW_REQUEST.md",)) -> None:
    path = root / ".ai-review" / WORK_ITEM_ID / name
    path.mkdir(parents=True, exist_ok=True)
    for file_name in files:
        (path / file_name).write_text("previous round\n")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


_MANUAL_BINDING = dict(reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40, reviewed_content_id="c" * 64)


def _s_nothing_on_disk(root: Path, head: str) -> None:
    pass


def _s_coherent_no_feedback(root: Path, head: str) -> None:
    _manifest(root, head)
    _author_files(root)


def _s_coherent_local_block(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _FLAT_FEEDBACK, status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW")


def _s_coherent_local_block_missing_base_commit(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _FLAT_FEEDBACK, status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW",
              reviewed_base_commit=None)


def _s_coherent_local_approve(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _FLAT_FEEDBACK, status="APPROVE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW")


def _s_coherent_local_role_at_manual_stage(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="APPROVE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW",
              **_MANUAL_BINDING)


def _s_coherent_manual_approve(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
              **_MANUAL_BINDING)


def _s_coherent_manual_revise_legacy_role(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="REVISE", reviewer_role="manual_external_plan_review",
              **_MANUAL_BINDING)


def _s_coherent_manual_revise_missing_base_commit(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="REVISE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
              reviewed_bundle_id="b" * 64, reviewed_base_commit=None, reviewed_content_id="c" * 64)


def _s_coherent_manual_approve_bundle_id_mismatch(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
              reviewed_bundle_id="a" * 64, reviewed_base_commit="0" * 40, reviewed_content_id="c" * 64)


def _s_coherent_manual_approve_stale_content_id(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
              reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
              reviewed_content_id="stale" + "0" * 59)


def _s_stale_generation_head_manual_approve(root: Path, head: str) -> None:
    _manifest(root, head, generation_head="9" * 40)
    _author_files(root)
    _feedback(root, _SCOPED_FEEDBACK, status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
              **_MANUAL_BINDING)


def _s_coherent_manual_block(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="BLOCK", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
              **_MANUAL_BINDING)


def _s_coherent_unroled_approve(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="APPROVE", reviewed_bundle_id="b" * 64,
              reviewed_base_commit="0" * 40)


def _s_coherent_unroled_revise(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="REVISE", reviewed_bundle_id="b" * 64,
              reviewed_base_commit="0" * 40)


def _s_coherent_unroled_revise_bundle_id_mismatch(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="REVISE", reviewed_bundle_id="a" * 64,
              reviewed_base_commit="0" * 40)


def _s_coherent_unroled_revise_missing_work_item(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="REVISE", reviewed_bundle_id="b" * 64,
              reviewed_base_commit="0" * 40, work_item=None)


def _s_coherent_unroled_unparseable_status(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    _feedback(root, _SCOPED_FEEDBACK, status="MAYBE", reviewed_bundle_id="b" * 64,
              reviewed_base_commit="0" * 40)


def _s_stale_revision_refresh(root: Path, head: str) -> None:
    # Manifest at plan_revision 1; the scenario's work item is at 2.
    _manifest(root, head, plan_revision=1)
    _author_files(root)


def _s_stale_revision_with_local_block(root: Path, head: str) -> None:
    _manifest(root, head, plan_revision=1)
    _author_files(root)
    _feedback(root, _FLAT_FEEDBACK, status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW")


def _s_stale_revision_unroled_revise(root: Path, head: str) -> None:
    _manifest(root, head, plan_revision=1)
    _feedback(root, _SCOPED_FEEDBACK, status="REVISE", reviewed_bundle_id="b" * 64,
              reviewed_base_commit="a" * 40)


def _s_rejected_scoped_over_stale_bundle(root: Path, head: str) -> None:
    _manifest(root, head, plan_revision=1)
    _author_files(root)
    fixtures.write_rejected_marker(root, WORK_ITEM_ID, scoped=True, detail="step rename failed")


def _s_rejected_scoped_over_coherent_bundle(root: Path, head: str) -> None:
    _s_coherent_no_feedback(root, head)
    fixtures.write_rejected_marker(root, WORK_ITEM_ID, scoped=True, detail="finalize failed")


def _s_rejected_flat_only(root: Path, head: str) -> None:
    fixtures.write_rejected_marker(root, WORK_ITEM_ID, scoped=False, detail="withdrawn")


def _s_withdrawn_with_quarantine(root: Path, head: str) -> None:
    _quarantine(root, "current.rejected-0123abcd")


def _s_two_quarantines_newest_named(root: Path, head: str) -> None:
    _quarantine(root, "current.rejected-zzzz", mtime_ns=1_000_000_000)
    _quarantine(root, "current.rejected-aaaa", mtime_ns=2_000_000_000)


def _s_stale_revision_absent_author_files(root: Path, head: str) -> None:
    _manifest(root, head, plan_revision=1)


def _s_stale_revision_one_missing_author_file(root: Path, head: str) -> None:
    _manifest(root, head, plan_revision=1)
    _author_files(root, names=("REVIEW_REQUEST.md", "TEST_RESULTS.md"))


def _s_zero_byte_stubs_with_quarantine(root: Path, head: str) -> None:
    _quarantine(root, "current.rejected-0123abcd", files=_AUTHOR_FILES)
    _author_files(root, text="")


def _s_stale_revision_one_zero_byte_author_file(root: Path, head: str) -> None:
    _manifest(root, head, plan_revision=1)
    _author_files(root, names=("REVIEW_REQUEST.md", "TEST_RESULTS.md"))
    _author_files(root, text="", names=("CONTEXT_FILES.txt",))


def _s_missing_manifest(root: Path, head: str) -> None:
    _author_files(root)


def _s_implementation_stage_manifest(root: Path, head: str) -> None:
    fixtures.write_manifest(root, _BUNDLE, fixtures.build_manifest_text(
        bundle_id="b" * 64, generation_head=head, stage="implementation",
        work_item_id=WORK_ITEM_ID, plan_revision=1,
    ))
    _author_files(root)


def _s_wrong_work_item_manifest(root: Path, head: str) -> None:
    fixtures.write_manifest(root, _BUNDLE, fixtures.build_manifest_text(
        bundle_id="b" * 64, generation_head=head, stage="plan", work_item_id="wi-2", plan_revision=1,
    ))
    _author_files(root)


#: ``(scenario id, setup, work-item overrides)``. The overrides are applied
#: on top of :data:`_BASE_OVERRIDES`; every field a plan-stage decision
#: reads is stated explicitly, so fixture-default changes in later
#: checkpoints cannot move the golden.
_BASE_OVERRIDES: dict[str, Any] = dict(
    plan_revision=1, base_commit="0" * 40, plan_review_stages=_LOCAL_APPROVE_LEDGER,
    incomplete_children=(), implementation_revision=None,
)

SCENARIOS: tuple[tuple[str, Callable[[Path, str], None], dict[str, Any]], ...] = (
    ("nothing_on_disk", _s_nothing_on_disk, {}),
    ("coherent_no_feedback", _s_coherent_no_feedback, {}),
    ("coherent_local_block", _s_coherent_local_block, {}),
    ("coherent_local_block_missing_base_commit", _s_coherent_local_block_missing_base_commit, {}),
    ("coherent_local_approve", _s_coherent_local_approve, {}),
    ("coherent_local_role_approve_scoped", _s_coherent_local_role_at_manual_stage, {}),
    ("coherent_manual_approve", _s_coherent_manual_approve, {}),
    ("coherent_manual_revise_legacy_lowercase_role", _s_coherent_manual_revise_legacy_role, {}),
    ("coherent_manual_revise_missing_base_commit", _s_coherent_manual_revise_missing_base_commit, {}),
    ("coherent_manual_approve_bundle_id_mismatch", _s_coherent_manual_approve_bundle_id_mismatch, {}),
    ("coherent_manual_approve_stale_content_id", _s_coherent_manual_approve_stale_content_id, {}),
    ("coherent_manual_approve_no_local_approval", _s_coherent_manual_approve,
     {"plan_review_stages": {"review_content_id": "c" * 64}}),
    ("coherent_manual_approve_no_ledger", _s_coherent_manual_approve, {"plan_review_stages": None}),
    ("stale_generation_head_manual_approve", _s_stale_generation_head_manual_approve, {}),
    ("coherent_manual_block", _s_coherent_manual_block, {}),
    ("coherent_unroled_approve", _s_coherent_unroled_approve, {}),
    ("coherent_unroled_revise", _s_coherent_unroled_revise, {}),
    ("coherent_unroled_revise_bundle_id_mismatch", _s_coherent_unroled_revise_bundle_id_mismatch, {}),
    ("coherent_unroled_revise_missing_work_item", _s_coherent_unroled_revise_missing_work_item, {}),
    ("coherent_unroled_unparseable_status", _s_coherent_unroled_unparseable_status, {}),
    ("stale_revision_refresh", _s_stale_revision_refresh, {"plan_revision": 2, "base_commit": "a" * 40}),
    ("stale_revision_with_local_block", _s_stale_revision_with_local_block,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("stale_revision_unroled_revise", _s_stale_revision_unroled_revise,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("stale_revision_base_commit_none", _s_stale_revision_refresh, {"plan_revision": 2, "base_commit": None}),
    ("state_plan_revision_none", _s_coherent_no_feedback, {"plan_revision": None}),
    ("rejected_scoped_over_stale_bundle", _s_rejected_scoped_over_stale_bundle,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("rejected_scoped_over_coherent_bundle", _s_rejected_scoped_over_coherent_bundle, {}),
    ("rejected_flat_only", _s_rejected_flat_only, {}),
    ("withdrawn_with_quarantine", _s_withdrawn_with_quarantine, {"plan_revision": 2, "base_commit": "a" * 40}),
    ("two_quarantines_newest_named", _s_two_quarantines_newest_named,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("stale_revision_absent_author_files", _s_stale_revision_absent_author_files,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("stale_revision_one_missing_author_file", _s_stale_revision_one_missing_author_file,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("zero_byte_stubs_with_quarantine", _s_zero_byte_stubs_with_quarantine,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("stale_revision_one_zero_byte_author_file", _s_stale_revision_one_zero_byte_author_file,
     {"plan_revision": 2, "base_commit": "a" * 40}),
    ("missing_manifest", _s_missing_manifest, {}),
    ("implementation_stage_manifest", _s_implementation_stage_manifest, {}),
    ("wrong_work_item_manifest", _s_wrong_work_item_manifest, {}),
)


# ---------------------------------------------------------------------------
# Normalisation and serialisation (shared with the test).
# ---------------------------------------------------------------------------

_SHA_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])")


def normalise(value: Any, roots: tuple[str, ...]) -> Any:
    """Replace every occurrence of each of ``roots`` (longest first) with
    ``<ROOT>`` and every standalone 40-hex-digit token with ``<SHA>``,
    recursively through lists and dicts. Anything else passes through."""
    if isinstance(value, str):
        text = value
        for root in sorted(set(roots), key=len, reverse=True):
            text = text.replace(root, "<ROOT>")
        return _SHA_RE.sub("<SHA>", text)
    if isinstance(value, dict):
        return {key: normalise(item, roots) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalise(item, roots) for item in value]
    return value


def decision_to_dict(decision: Any) -> dict[str, Any]:
    gate = decision.gate
    return {
        "observed_phase": decision.observed_phase,
        "automatic": decision.automatic,
        "declined": decision.declined,
        "action_command": decision.action.command if decision.action is not None else None,
        "gate": None if gate is None else {
            "repository": gate.repository,
            "work_item_id": gate.work_item_id,
            "phase": gate.phase,
            "what_is_required": gate.what_is_required,
            "artifact_path": gate.artifact_path,
            "safe_resume_command": gate.safe_resume_command,
        },
        "evidence": list(decision.evidence),
        "reason": decision.reason,
    }


def case_key(scenario_id: str, phase: str, version: str) -> str:
    return f"{scenario_id} | {phase} | {version}"


def derive_cases() -> dict[str, dict[str, Any]]:
    """Run every scenario against every ``(phase, version)`` of
    :data:`PHASE_VERSIONS` through ``controller.evidence.decide`` and return
    the normalised decisions, keyed by :func:`case_key`."""
    cases: dict[str, dict[str, Any]] = {}
    for scenario_id, setup, overrides in SCENARIOS:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "target"
            fixtures.build_target_git_repo(root)
            (root / "README.md").write_text("target fixture\n")
            head = fixtures.commit_all(root, "initial")
            setup(root, head)
            managed_repo = fixtures.build_target_managed_repository(root)
            roots = (str(root), str(root.resolve()))
            for phase, version in PHASE_VERSIONS:
                fields = dict(_BASE_OVERRIDES)
                fields.update(overrides)
                work_item = fixtures.build_work_item_view(
                    work_item_id=WORK_ITEM_ID, phase=phase, governing_workflow_version=version,
                    **fields,
                )
                try:
                    decision = evidence.decide(managed_repo, snapshot=None, work_item=work_item)
                    body = decision_to_dict(decision)
                except Exception as exc:  # pinned, never swallowed: a raise is a decision too
                    body = {"raises": type(exc).__name__, "message": str(exc)}
                cases[case_key(scenario_id, phase, version)] = normalise(body, roots)
    return cases


def render_document(cases: dict[str, dict[str, Any]]) -> str:
    """The golden file's exact text for ``cases``. Identical decisions are
    stored once, under a content-derived id (the first 12 hex digits of the
    SHA-256 of their canonical JSON), and ``cases`` maps each case key to
    that id -- a phase whose decision reads no evidence decides identically
    under every scenario, so the file would otherwise repeat the same body
    dozens of times."""
    decisions: dict[str, dict[str, Any]] = {}
    index: dict[str, str] = {}
    for key, body in cases.items():
        canonical = json.dumps(body, sort_keys=True)
        decision_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        existing = decisions.get(decision_id)
        if existing is not None and json.dumps(existing, sort_keys=True) != canonical:
            raise AssertionError(f"decision id collision on {decision_id}")
        decisions[decision_id] = body
        index[key] = decision_id
    document = {
        "schema_version": 1,
        "description": (
            "Plan-stage decisions of controller.evidence.decide, generated from the base "
            "commit's unchanged decision.py/evidence.py (tests/golden/"
            "generate_plan_stage_decisions.py). 'cases' maps 'scenario | phase | version' "
            "to a key of 'decisions'."
        ),
        "phase_versions": [list(pair) for pair in PHASE_VERSIONS],
        "cases": index,
        "decisions": decisions,
    }
    return json.dumps(document, sort_keys=True, indent=2) + "\n"


def load_cases(text: str) -> dict[str, dict[str, Any]]:
    """The inverse of :func:`render_document`: every case key mapped to its
    full decision body."""
    document = json.loads(text)
    decisions = document["decisions"]
    return {key: decisions[decision_id] for key, decision_id in document["cases"].items()}


def render() -> str:
    """The golden file's exact text, freshly derived from the code."""
    return render_document(derive_cases())


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = GOLDEN_PATH.read_text() if GOLDEN_PATH.is_file() else None
        if current == text:
            print(f"{GOLDEN_PATH} is current")
            return 0
        print(f"{GOLDEN_PATH} differs from a fresh derivation", file=sys.stderr)
        return 1
    GOLDEN_PATH.write_text(text)
    document = json.loads(text)
    print(f"wrote {GOLDEN_PATH} ({len(document['cases'])} cases, "
          f"{len(document['decisions'])} distinct decisions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
