"""Generator for ``tests/golden/external_implementation_review_decisions.json``
-- the ``"1"``/``"2.1"`` ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` decision
golden (``docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md``,
CP4: "``"1"``/``"2.1"``: byte-identical to today, pinned by a golden in the
CP3 style over the existing ``tests/test_evidence.py`` cases").

CP4 makes ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` ledger-, feedback- and
pin-aware at ``"2.2"`` only, and adds the ``"2.2"`` implementation-bundle
gate in front of it. At ``"1"``/``"2.1"`` nothing may move, so this golden
was generated **before any CP4 change**, from CP3's unchanged
``controller/decision.py``/``controller/evidence.py``.

**Coverage.** The evidence matrix of ``tests/test_evidence.py``'s
``AwaitingExternalImplementationReviewTest`` (no feedback, ``REVISE``,
``APPROVE``, a stale ``generation_head``) widened with the variants CP4's
``"2.2"`` handler reads (a ``BLOCK``/unparseable status, the scoped
layout, a coherent implementation-stage manifest, a complete ledger, a
``technical_review_block_pins`` entry naming the bundle, no manifest) and
the ``REJECTED`` marker on both layouts -- so every input the ``"2.2"``
handler consults is shown to leave ``"1"``/``"2.1"`` unchanged.

Normalisation, serialisation and the deduplicated document form are the
plan-stage generator's own (:mod:`tests.golden.generate_plan_stage_decisions`),
imported rather than copied.

Run ``python3 tests/golden/generate_external_implementation_review_decisions.py``
to rewrite the golden, or with ``--check`` to compare without writing.
Rewriting it is a deliberate act: the golden's whole value is that it does
not move.

**The release dimension** (``docs/ai-workflow/
CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md``, Design C, CP3), as in the
plan-stage generator: ``--release 2.5.1`` (the default) writes and checks the
file above, derived exactly as before; ``--release 2.6.0`` writes and checks
``external_implementation_review_decisions.2.6.0.json``, where each scenario
runs once per Workflow 2.6.0 feedback layout, its feedback written at that
layout's path and Workflow's feedback-path answer replayed as a recorded
value. This phase reads no publication status. Case keys gain ``| layout``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from controller import evidence  # noqa: E402
from controller.managed_repo import REFERENCE_WORKFLOW_RELEASE  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.golden import generate_plan_stage_decisions as plan_golden  # noqa: E402
from tests.golden.generate_plan_stage_decisions import (  # noqa: E402
    FEEDBACK_LAYOUTS, case_key, decision_to_dict, load_cases, normalise, queries_workflow, recorded_feedback_path,
)

GOLDEN_PATH = Path(__file__).resolve().parent / "external_implementation_review_decisions.json"

#: Each release this generator derives a golden for, and that golden's file.
GOLDEN_PATHS: dict[str, Path] = {
    REFERENCE_WORKFLOW_RELEASE: GOLDEN_PATH,
    "2.6.0": GOLDEN_PATH.with_name("external_implementation_review_decisions.2.6.0.json"),
}

WORK_ITEM_ID = "wi-1"
PHASE = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"

#: The versions whose decisions at this phase must not move (CP4).
PHASE_VERSIONS: tuple[tuple[str, str], ...] = ((PHASE, "1"), (PHASE, "2.1"))

_FLAT_BUNDLE = Path(".ai-review") / "current"
_FLAT_FEEDBACK = Path(".ai-review") / "feedback"
_SCOPED_FEEDBACK = Path(".ai-review") / WORK_ITEM_ID / "feedback"

_REVIEWED_HEAD = "1" * 40
_COMPLETE_LEDGER = fixtures.implementation_review_ledger(
    "c" * 64, local_bundle_id="b" * 64, manual_bundle_id="b" * 64,
)
_PIN_ON_THE_BUNDLE = (
    {"bundle_id": "b" * 64, "review_content_id": "c" * 64, "recorded_at": "2026-01-01T00:00:00Z"},
)


def _plain_manifest(bundle: Path, *, generation_head: str | None) -> Callable[[Path, str], None]:
    def write(root: Path, head: str) -> None:
        fixtures.write_manifest(root, bundle, fixtures.build_manifest_text(
            generation_head=head if generation_head is None else generation_head,
        ))
    return write


def _implementation_manifest(root: Path, head: str) -> None:
    fixtures.write_implementation_manifest(
        root, WORK_ITEM_ID, 1, reviewed_implementation_head=_REVIEWED_HEAD, generation_head=head,
    )


#: Under a ``workflow_query`` release, the feedback directory of the layout
#: the case runs on (every scenario's feedback is written there); ``None``
#: for the reference release.
_feedback_redirect: Path | None = None


def _feedback(feedback_dir: Path, **fields) -> Callable[[Path, str], None]:
    def write(root: Path, head: str) -> None:
        target_dir = feedback_dir if _feedback_redirect is None else _feedback_redirect
        fixtures.write_review_feedback(root, target_dir, fixtures.build_review_feedback_text(**fields))
    return write


def _rejected(*, scoped: bool, detail: str) -> Callable[[Path, str], None]:
    def write(root: Path, head: str) -> None:
        fixtures.write_rejected_marker(root, WORK_ITEM_ID, scoped=scoped, detail=detail)
    return write


def _steps(*steps: Callable[[Path, str], None]) -> Callable[[Path, str], None]:
    def run(root: Path, head: str) -> None:
        for step in steps:
            step(root, head)
    return run


_FLAT_CURRENT = _plain_manifest(_FLAT_BUNDLE, generation_head=None)
_FLAT_STALE = _plain_manifest(_FLAT_BUNDLE, generation_head="9" * 40)
_MANUAL = "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"

#: ``(scenario id, setup, work-item overrides)``, each override applied on
#: top of :data:`_BASE_OVERRIDES`.
_BASE_OVERRIDES: dict[str, Any] = dict(
    plan_revision=1, base_commit="0" * 40, implementation_revision=1,
    reviewed_implementation_head=_REVIEWED_HEAD, implementation_review_stages=None,
    technical_review_block_pins=(),
)

SCENARIOS: tuple[tuple[str, Callable[[Path, str], None], dict[str, Any]], ...] = (
    ("nothing_on_disk", _steps(), {}),
    ("flat_current_no_feedback", _FLAT_CURRENT, {}),
    ("flat_current_revise", _steps(_FLAT_CURRENT, _feedback(_FLAT_FEEDBACK, status="REVISE")), {}),
    ("flat_current_approve", _steps(_FLAT_CURRENT, _feedback(_FLAT_FEEDBACK, status="APPROVE")), {}),
    ("flat_current_block", _steps(_FLAT_CURRENT, _feedback(_FLAT_FEEDBACK, status="BLOCK")), {}),
    ("flat_current_unparseable_status",
     _steps(_FLAT_CURRENT, _feedback(_FLAT_FEEDBACK, status="MAYBE")), {}),
    ("flat_current_status_absent", _steps(_FLAT_CURRENT, _feedback(_FLAT_FEEDBACK, status=None)), {}),
    ("flat_stale_generation_head", _FLAT_STALE, {}),
    ("flat_stale_generation_head_approve",
     _steps(_FLAT_STALE, _feedback(_FLAT_FEEDBACK, status="APPROVE")), {}),
    ("no_manifest_approve", _feedback(_FLAT_FEEDBACK, status="APPROVE"), {}),
    ("scoped_coherent_no_feedback", _implementation_manifest, {}),
    ("scoped_coherent_manual_approve_complete_ledger",
     _steps(_implementation_manifest, _feedback(_SCOPED_FEEDBACK, status="APPROVE", reviewer_role=_MANUAL)),
     {"implementation_review_stages": _COMPLETE_LEDGER}),
    ("scoped_coherent_manual_approve_no_ledger",
     _steps(_implementation_manifest, _feedback(_SCOPED_FEEDBACK, status="APPROVE", reviewer_role=_MANUAL)),
     {}),
    ("scoped_coherent_manual_revise_complete_ledger",
     _steps(_implementation_manifest, _feedback(_SCOPED_FEEDBACK, status="REVISE", reviewer_role=_MANUAL)),
     {"implementation_review_stages": _COMPLETE_LEDGER}),
    ("scoped_coherent_manual_block_complete_ledger",
     _steps(_implementation_manifest, _feedback(_SCOPED_FEEDBACK, status="BLOCK", reviewer_role=_MANUAL)),
     {"implementation_review_stages": _COMPLETE_LEDGER}),
    ("scoped_coherent_approve_pinned_bundle",
     _steps(_implementation_manifest, _feedback(_SCOPED_FEEDBACK, status="APPROVE", reviewer_role=_MANUAL)),
     {"implementation_review_stages": _COMPLETE_LEDGER, "technical_review_block_pins": _PIN_ON_THE_BUNDLE}),
    ("scoped_incoherent_revision_approve",
     _steps(_implementation_manifest, _feedback(_SCOPED_FEEDBACK, status="APPROVE", reviewer_role=_MANUAL)),
     {"implementation_revision": 2, "implementation_review_stages": _COMPLETE_LEDGER}),
    ("rejected_scoped", _steps(_implementation_manifest, _rejected(scoped=True, detail="step rename failed")),
     {}),
    ("rejected_flat", _steps(_FLAT_CURRENT, _rejected(scoped=False, detail="finalize failed")), {}),
)


def derive_cases(release: str = REFERENCE_WORKFLOW_RELEASE) -> dict[str, dict[str, Any]]:
    """Run every scenario at every ``(phase, version)`` of
    :data:`PHASE_VERSIONS` through ``controller.evidence.decide``, normalised
    and keyed by :func:`~tests.golden.generate_plan_stage_decisions.case_key`;
    under a ``workflow_query`` release, once per feedback layout."""
    if queries_workflow(release):
        return _derive_query_cases(release)
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
                    work_item_id=WORK_ITEM_ID, phase=phase, governing_workflow_version=version, **fields,
                )
                try:
                    body = decision_to_dict(evidence.decide(managed_repo, snapshot=None, work_item=work_item))
                except Exception as exc:  # pinned, never swallowed: a raise is a decision too
                    body = {"raises": type(exc).__name__, "message": str(exc)}
                cases[case_key(scenario_id, phase, version)] = normalise(body, roots)
    return cases


def _derive_query_cases(release: str) -> dict[str, dict[str, Any]]:
    """:func:`derive_cases` under a ``workflow_query`` release: one target
    per ``(scenario, layout)``, with its feedback at the layout's directory
    (created for ``legacy-scoped``), its manifest declaring ``release``, and
    the plan-stage generator's recorded answers for that layout."""
    global _feedback_redirect
    cases: dict[str, dict[str, Any]] = {}
    for scenario_id, setup, overrides in SCENARIOS:
        for layout in FEEDBACK_LAYOUTS:
            previous = _feedback_redirect
            _feedback_redirect = Path(recorded_feedback_path(WORK_ITEM_ID, layout).feedback_dir)
            try:
                with TemporaryDirectory() as tmp, plan_golden.recorded_answers(layout):
                    root = Path(tmp) / "target"
                    fixtures.build_target_git_repo(root)
                    (root / "README.md").write_text("target fixture\n")
                    head = fixtures.commit_all(root, "initial")
                    setup(root, head)
                    if layout == "legacy-scoped":
                        (root / _SCOPED_FEEDBACK).mkdir(parents=True, exist_ok=True)
                    fixtures.write_installation_manifest(root, workflow_version=release)
                    managed_repo = fixtures.build_target_managed_repository(root)
                    roots = (str(root), str(root.resolve()))
                    for phase, version in PHASE_VERSIONS:
                        plan_golden._current_case.update(
                            layout=layout, status=plan_golden.NO_STATUS, phase=phase, version=version)
                        fields = dict(_BASE_OVERRIDES)
                        fields.update(overrides)
                        work_item = fixtures.build_work_item_view(
                            work_item_id=WORK_ITEM_ID, phase=phase, governing_workflow_version=version, **fields,
                        )
                        try:
                            body = decision_to_dict(evidence.decide(managed_repo, snapshot=None, work_item=work_item))
                        except Exception as exc:  # pinned, never swallowed: a raise is a decision too
                            body = {"raises": type(exc).__name__, "message": str(exc)}
                        cases[case_key(scenario_id, phase, version, layout)] = normalise(body, roots)
            finally:
                _feedback_redirect = previous
    return cases


def render_document(cases: dict[str, dict[str, Any]], release: str = REFERENCE_WORKFLOW_RELEASE) -> str:
    """The golden file's exact text for ``cases``: the plan-stage golden's
    own deduplicated form (identical decisions stored once, under a
    content-derived id), with this golden's own description."""
    document = json.loads(plan_golden.render_document(cases))
    document["description"] = (
        "\"1\"/\"2.1\" AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW decisions of "
        "controller.evidence.decide, generated before automatic-lifecycle-orchestration CP4 "
        "from CP3's unchanged decision.py/evidence.py (tests/golden/"
        "generate_external_implementation_review_decisions.py). 'cases' maps "
        "'scenario | phase | version' to a key of 'decisions'."
    )
    document["phase_versions"] = [list(pair) for pair in PHASE_VERSIONS]
    if queries_workflow(release):
        document["description"] = (
            f"\"1\"/\"2.1\" AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW decisions of controller.evidence.decide "
            f"under the Workflow {release} contract, Workflow's feedback-path answer replayed as a recorded "
            f"value (tests/golden/generate_external_implementation_review_decisions.py --release {release}). "
            "'cases' maps 'scenario | phase | version | layout' to a key of 'decisions'."
        )
        document["release"] = release
        document["feedback_layouts"] = list(FEEDBACK_LAYOUTS)
    return json.dumps(document, sort_keys=True, indent=2) + "\n"


def render(release: str = REFERENCE_WORKFLOW_RELEASE) -> str:
    """The golden file's exact text, freshly derived from the code."""
    return render_document(derive_cases(release), release)


__all__ = ["GOLDEN_PATH", "GOLDEN_PATHS", "PHASE_VERSIONS", "SCENARIOS", "derive_cases", "load_cases", "render",
           "render_document"]


def main(argv: list[str]) -> int:
    release = plan_golden.release_argument(argv)
    golden_path = GOLDEN_PATHS[release]
    text = render(release)
    if "--check" in argv:
        current = golden_path.read_text() if golden_path.is_file() else None
        if current == text:
            print(f"{golden_path} is current")
            return 0
        print(f"{golden_path} differs from a fresh derivation", file=sys.stderr)
        return 1
    golden_path.write_text(text)
    document = json.loads(text)
    print(f"wrote {golden_path} ({len(document['cases'])} cases, "
          f"{len(document['decisions'])} distinct decisions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
