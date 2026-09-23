"""Evidence-reading disambiguations and human-gate classification (CP4B,
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP4 / CP4B -- Next-action
decision engine and human-gate classification").

:func:`decide` is CP4B's own entry point -- the one a later checkpoint's
``job``/``cli`` modules call (the dependency graph in the plan's
"Dependency direction" section has ``job -> {managed_repo, target_state,
evidence, worker, handoff} -> decision``, so ``evidence`` sits *beside*
``managed_repo``/``target_state``, never imported by either, and imports
only ``controller.decision`` -- never the reverse). For every phase this
module does not itself refine, it is a pure pass-through to
:func:`controller.decision.decide`.

**Automatic-lifecycle-orchestration CP4** gives the implementation-review
phases their evidence handlers: ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW``
(automatic ``/review-implementation`` unless a local ``BLOCK`` is on file),
``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` (automatic ingestion of
an admissible manual verdict), and a ledger-, feedback- and pin-aware
``"2.2"`` ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW``; at ``"2.2"`` an
implementation bundle incoherent with the work item's implementation round
gates ahead of all of them (:func:`implementation_bundle_coherence`).
**CP4B** gives that gate its generation-record-aware recovery (a malformed
record, provenance only, or the ordered regeneration steps) and adds the
``"2.2"`` ``APPLYING_REVIEW_FEEDBACK`` automatic path: an admissible
two-stage ``REVISE`` launches ``/apply-implementation-review`` -- with the
pinned pending-review-stage-write task addendum while ``HEAD`` does not yet
record the phase -- unless the apply relaunch bound applies.

Four phases genuinely need a ``.ai-review/`` evidence read to resolve
their "ordinary case vs. blocked/withdrawn/superseded" sub-cases
correctly -- the same four ``controller.decision`` names in its own
module docstring as carrying an interim, evidence-independent
placeholder: ``AWAITING_LOCAL_PLAN_REVIEW``,
``AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW``, and
``AWAITING_EXTERNAL_PLAN_REVIEW`` on a ``"1"``-governed item, plus
``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` on a ``"2.2"``-governed
item (revision 64's own three-way sub-case, added to this checkpoint's own
scope at the same revision -- CP4's fixed gate text sharpens into the same
"hand to reviewer / verdict on file / Status: BLOCK" three-way read the
plan-stage manual gate already performs, one stage over, minus that stage's
admissibility model -- which automatic-lifecycle-orchestration CP4 then
added, with :func:`evaluate_manual_implementation_stage_admissibility`). Two more
report-only phases (``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW``,
``AWAITING_FUNCTIONAL_REVIEW``) gain evidence-driven sub-case reports
here too, and every bundle-bearing phase gains a **withdrawn-bundle**
outcome that is checked ahead of everything else.

This module imports nothing from ``controller.managed_repo`` or
``controller.target_state`` for the same reason ``controller.decision``
does not: both sit later in the dependency order. ``managed_repo``,
``snapshot`` and ``work_item`` are accepted as plain, duck-typed objects;
this module only ever reads ``managed_repo.root`` and, off ``work_item``:
``work_item_id``, ``phase``, ``base_commit``, ``plan_revision``,
``implementation_revision``, ``plan_review_stages``, ``incomplete_children``,
``registry_complete``, and -- for the implementation-stage readers and
handlers (automatic-lifecycle-orchestration CP1/CP4) --
``governing_workflow_version``, ``work_item_type``,
``reviewed_implementation_head``, ``implementation_review_stages`` and
``technical_review_block_pins``.

**What this module does not do.** It never recomputes ``review_content_id``
or ``bundle_id`` -- ``controller.target_state`` already declares that
reimplementation out of scope, and every comparison here is against a
string already read out of ``WORKFLOW_STATE.json`` or a plain labelled
line in an on-disk file. It is a filter over evidence the frozen Workflow
commands would themselves read, never a second implementation of their
authority: a file this model admits as automatic is never a file the
command it selects would refuse on the evidence this model can see.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from controller import decision as _decision
from controller.decision import Action, Decision, HumanGate

# ---------------------------------------------------------------------------
# The text model for every labelled-line read (revision 47, local round
# 46's ``OPUS-R46-B1``).
# ---------------------------------------------------------------------------


def provenance_block(text: str) -> str:
    """The file's text from its start up to, but never including, its
    first line beginning ``"## "`` -- the block every labelled line this
    module reads lives inside. Both ``REVIEW_FEEDBACK.md`` and
    ``MANIFEST.md`` open their first ``## `` heading immediately after
    this block, so a body section quoting a label as prose (for example,
    restating the feedback template while explaining a finding) always
    sits after it and is never read."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            break
        out.append(line)
    return "".join(out)


def read_labelled_line(text: str, label: str) -> str | None:
    """A labelled line is one, inside :func:`provenance_block` only,
    whose text -- after stripping leading whitespace -- begins with the
    exact literal ``label`` (including its trailing colon) and exactly
    one following space; its value is the rest of that line with trailing
    whitespace stripped. A label with no matching line is ``None`` --
    never a default."""
    prefix = f"{label} "
    for raw_line in provenance_block(text).splitlines():
        stripped = raw_line.lstrip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].rstrip()
    return None


# ---------------------------------------------------------------------------
# ``<bundle_dir>``/``<feedback_dir>``/marker resolution -- the two-rule
# model the plan states explicitly (round 3's I3, round 6's I2).
# ---------------------------------------------------------------------------

#: The four plan-stage phases whose ``<bundle_dir>`` is unconditionally
#: ``.ai-review/<work_item_id>/current/``, with no existence gate at all.
PLAN_STAGE_PHASES: frozenset[str] = frozenset({
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
})

#: Every phase whose bundle can be withdrawn and so gains the REJECTED-
#: marker outcome ahead of all its other rows. ``AWAITING_MANUAL_EXTERNAL_
#: IMPLEMENTATION_REVIEW`` joins this set at revision 64:
#: ``/record-manual-implementation-review`` calls
#: ``assert_bundle_not_rejected`` exactly as ``/record-manual-plan-review``
#: does at the plan stage (``.claude/commands/record-manual-implementation-
#: review.md``, step 5). ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` and
#: ``APPLYING_REVIEW_FEEDBACK`` join it with automatic-lifecycle-
#: orchestration CP4: ``/review-implementation`` (A3, A6) and
#: ``/apply-implementation-review`` (step 1) call ``assert_bundle_not_rejected``
#: too, at every version.
BUNDLE_BEARING_PHASES: frozenset[str] = PLAN_STAGE_PHASES | frozenset({
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK",
})


def _scoped_root(work_item_id: str) -> Path:
    return Path(".ai-review") / work_item_id


def _scoped_else_flat(root: Path, work_item_id: str, *, leaf: str) -> Path:
    """"Is on the scoped layout" is decided from the work item's own root
    directory (``.ai-review/<work_item_id>/``), never from the transient
    existence of the leaf inside it -- a withdrawal renames ``current/``
    away without changing which layout the work item is on."""
    scoped = _scoped_root(work_item_id)
    if (root / scoped).is_dir():
        return scoped / leaf
    return Path(".ai-review") / leaf


def resolve_feedback_dir(root: Path, work_item_id: str) -> Path:
    """``<feedback_dir>``, at every stage: scoped-else-flat, taking no
    stage argument -- but keyed on the feedback leaf's **own** existence
    (``.ai-review/<work_item_id>/feedback/``), never on the work item's
    root directory the way ``resolve_bundle_dir``/``resolve_rejected_marker_path``
    are (``_scoped_else_flat``). ``docs/ai-workflow/REVIEW_PROTOCOL.md``'s
    "Bundle location" section states this explicitly: "`feedback/` is
    stage-agnostic and always follows this same scoped-else-flat rule, for
    every stage alike, keyed on `.ai-review/<work_item_id>/feedback/`'s own
    existence" -- deliberately different from the bundle directory's own
    rule, and exactly what ``scripts/workflow_fingerprint.py``'s own
    ``resolve_feedback_dir`` implements. Reusing ``_scoped_else_flat``'s
    root-existence gate here (the prior shape of this function) made this
    resolver disagree with that one for a work item that already has a
    scoped bundle round (so ``.ai-review/<work_item_id>/`` exists) but has
    never had a scoped ``feedback/`` round of its own -- exactly the state
    a real work item reaches immediately after its first implementation
    review: this function returned the scoped path while the Workflow
    commands that actually write/read feedback resolved to the flat one,
    so a human gate's reported ``artifact_path`` named a location nothing
    downstream ever reads."""
    scoped = _scoped_root(work_item_id) / "feedback"
    if (root / scoped).is_dir():
        return scoped
    return Path(".ai-review") / "feedback"


def resolve_bundle_dir(root: Path, work_item_id: str, *, phase: str) -> Path:
    """``<bundle_dir>``: unconditionally scoped at the four plan-stage
    phases (no existence gate), scoped-else-flat everywhere else
    (correct at the implementation-stage phases, whose
    ``prepare-ai-review.sh`` ``work-item-id`` argument really is
    optional)."""
    if phase in PLAN_STAGE_PHASES:
        return _scoped_root(work_item_id) / "current"
    return _scoped_else_flat(root, work_item_id, leaf="current")


def resolve_rejected_marker_path(root: Path, work_item_id: str) -> Path:
    """The ``REJECTED`` marker follows the *stage-less* scoped-else-flat
    rule -- deliberately unlike the plan-stage ``<bundle_dir>`` beside
    it -- because the real resolver
    (``workflow_fingerprint.resolve_rejected_marker_path``) takes no
    ``stage`` argument at all."""
    return _scoped_else_flat(root, work_item_id, leaf="REJECTED")


def rejected_marker_detail(root: Path, work_item_id: str) -> tuple[bool, str | None]:
    """``(is_rejected, detail)``. Presence alone is a rejection -- an
    empty, truncated or unreadable marker degrades the diagnostic, it
    never passes as "not rejected" -- and a presence check that cannot
    complete is treated as present rather than absent, mirroring
    ``workflow_fingerprint.assert_bundle_not_rejected``'s own "cannot
    complete => present" rule."""
    import os

    marker_path = root / resolve_rejected_marker_path(root, work_item_id)
    try:
        os.stat(marker_path)
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        return True, f"<presence could not be determined: {exc!r}>"
    try:
        detail = marker_path.read_text().strip()
    except OSError as exc:
        detail = f"<unreadable: {exc!r}>"
    return True, (detail or "<empty marker>")


# ---------------------------------------------------------------------------
# ``MANIFEST.md`` / ``REVIEW_FEEDBACK.md`` labelled-line reads.
# ---------------------------------------------------------------------------


_MANIFEST_LABELS: tuple[str, ...] = (
    "bundle_id", "generation_head", "stage", "work_item_id", "plan_revision",
    "review_content_id", "implementation_revision", "reviewed_implementation_head",
    "worktree_root",
)


def read_manifest_fields(root: Path, bundle_dir: Path) -> dict[str, Any]:
    """Read ``<bundle_dir>/MANIFEST.md``'s ``bundle_id:``/
    ``generation_head:``/``stage:``/``work_item_id:``/``plan_revision:``
    labelled lines (the last three are the generator's own
    ``render_manifest_md`` identity lines, read by
    :func:`plan_bundle_coherence`), plus the implementation-stage
    ``render_manifest_md_implementation_stage`` lines
    ``review_content_id:``/``implementation_revision:``/
    ``reviewed_implementation_head:``/``worktree_root:`` (read by
    :func:`implementation_bundle_coherence` and the implementation-stage
    admissibility evaluators). ``_exists`` is ``False`` when the file
    itself cannot be read -- distinct from the file existing but lacking
    one of the lines, which each read as ``None``."""
    path = root / bundle_dir / "MANIFEST.md"
    try:
        text = path.read_text()
    except OSError:
        return {**{label: None for label in _MANIFEST_LABELS}, "_exists": False}
    return {
        **{label: read_labelled_line(text, f"{label}:") for label in _MANIFEST_LABELS},
        "_exists": True,
    }


_DECIMAL_RE = re.compile(r"[0-9]+")


def plan_bundle_coherence(root: Path, work_item_id: str, plan_revision: int | None) -> tuple[bool, str]:
    """``(coherent, detail)``: the single definition of "the current plan
    bundle belongs to this work item's current plan revision", shared by
    reconciliation postconditions and the decision-time plan-review gate.

    Coherent iff the plan-stage ``<bundle_dir>``
    (``.ai-review/<work_item_id>/current/``) has a readable ``MANIFEST.md``
    declaring ``stage: plan``, ``work_item_id`` equal to ``work_item_id``,
    a ``bundle_id``, and a ``plan_revision`` that is a plain decimal
    integer equal to ``plan_revision``. The check is revision-level only:
    it never recomputes ``review_content_id``/``bundle_id``, which stays
    Workflow's own fingerprinting authority. A state-side
    ``plan_revision`` of ``None`` is incoherent (fail closed). ``detail``
    names the first failing clause and the value observed there."""
    if plan_revision is None or isinstance(plan_revision, bool) or not isinstance(plan_revision, int):
        return False, f"state plan_revision is {plan_revision!r}, not an integer"
    bundle_dir = resolve_bundle_dir(root, work_item_id, phase="AWAITING_LOCAL_PLAN_REVIEW")
    if not (root / bundle_dir).is_dir():
        return False, f"plan bundle directory {bundle_dir} is absent (withdrawn or never generated)"
    manifest = read_manifest_fields(root, bundle_dir)
    if not manifest["_exists"]:
        return False, f"{bundle_dir / 'MANIFEST.md'} is missing or unreadable"
    if manifest["stage"] != "plan":
        return False, f"manifest stage {manifest['stage']!r} != 'plan'"
    if manifest["work_item_id"] != work_item_id:
        return False, f"manifest work_item_id {manifest['work_item_id']!r} != {work_item_id!r}"
    if not manifest["bundle_id"]:
        return False, "manifest bundle_id is missing"
    observed = manifest["plan_revision"]
    if observed is None:
        return False, "manifest plan_revision is missing"
    if not _DECIMAL_RE.fullmatch(observed):
        return False, f"manifest plan_revision {observed!r} is not an integer"
    if int(observed) != plan_revision:
        return False, f"manifest plan_revision {int(observed)} != state plan_revision {plan_revision}"
    return True, (
        f"manifest at {bundle_dir / 'MANIFEST.md'} matches work_item_id {work_item_id!r}, "
        f"plan_revision {plan_revision}"
    )


def read_feedback_fields(root: Path, feedback_dir: Path) -> dict[str, str | None] | None:
    """Read ``<feedback_dir>/REVIEW_FEEDBACK.md``'s labelled lines, or
    ``None`` if the file itself does not exist -- "no current-round
    feedback" and "feedback present but missing a field" are distinct
    outcomes throughout this module."""
    path = root / feedback_dir / "REVIEW_FEEDBACK.md"
    try:
        text = path.read_text()
    except OSError:
        return None
    return {
        "status": read_labelled_line(text, "Status:"),
        "reviewer_role": read_labelled_line(text, "Reviewer role:"),
        "reviewed_bundle_id": read_labelled_line(text, "Reviewed bundle ID:"),
        "reviewed_base_commit": read_labelled_line(text, "Reviewed base commit:"),
        "work_item": read_labelled_line(text, "Work item:"),
        "reviewed_content_id": read_labelled_line(text, "Reviewed review content ID:"),
    }


_VALID_STATUSES = ("APPROVE", "REVISE", "BLOCK")


# ---------------------------------------------------------------------------
# The manual-stage / "1"-path admissibility rule (round 38's
# ``EXT-PLAN-R38-B1``/``-I1``).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ClauseFailure:
    clause: str
    detail: str


@dataclasses.dataclass(frozen=True)
class AdmissibilityResult:
    admissible: bool
    failures: tuple[ClauseFailure, ...]
    advisories: tuple[str, ...] = ()

    def failure_summary(self) -> str:
        return "; ".join(f"{f.clause}: {f.detail}" for f in self.failures)


def _normalize_role(role: str | None) -> str | None:
    return role.strip().upper() if role else None


def evaluate_manual_stage_admissibility(
    *, feedback: dict[str, str | None], manifest: dict[str, Any], work_item: Any,
    current_head: str | None,
) -> AdmissibilityResult:
    """The ``"2.1"`` ``AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`` ->
    ``/record-manual-plan-review`` column of the admissibility table:
    hard on role, ``Status:``, presence of all three ``WFR-03`` fields,
    ``Work item:`` and ``Reviewed base commit:`` equality,
    ``review_content_id`` equality, a current local ``APPROVE``, and
    ``generation_head`` currency; **advisory only** on ``Reviewed bundle
    ID:`` equality."""
    failures: list[ClauseFailure] = []
    advisories: list[str] = []

    if _normalize_role(feedback.get("reviewer_role")) != "MANUAL_EXTERNAL_PLAN_REVIEW":
        failures.append(ClauseFailure(
            "Reviewer role",
            f"declares {feedback.get('reviewer_role')!r}, expected MANUAL_EXTERNAL_PLAN_REVIEW "
            "(or legacy manual_external_plan_review)",
        ))

    status = feedback.get("status")
    if status not in _VALID_STATUSES:
        failures.append(ClauseFailure(
            "Status", f"parses as {status!r}, expected one of {_VALID_STATUSES}",
        ))

    for label, key in (
        ("Reviewed bundle ID", "reviewed_bundle_id"),
        ("Reviewed base commit", "reviewed_base_commit"),
        ("Work item", "work_item"),
    ):
        if feedback.get(key) is None:
            failures.append(ClauseFailure(label, "absent"))

    work_item_id = work_item.work_item_id
    if feedback.get("work_item") is not None and feedback["work_item"] != work_item_id:
        failures.append(ClauseFailure(
            "Work item", f"names {feedback['work_item']!r}, expected {work_item_id!r}",
        ))

    base_commit = getattr(work_item, "base_commit", None)
    if feedback.get("reviewed_base_commit") is not None and feedback["reviewed_base_commit"] != base_commit:
        failures.append(ClauseFailure(
            "Reviewed base commit",
            f"names {feedback['reviewed_base_commit']!r}, expected {base_commit!r}",
        ))

    manifest_bundle_id = manifest.get("bundle_id")
    if (
        feedback.get("reviewed_bundle_id") is not None and manifest_bundle_id is not None
        and feedback["reviewed_bundle_id"] != manifest_bundle_id
    ):
        advisories.append(
            f"bundle_id mismatch (advisory only, does not block ingestion): "
            f"feedback={feedback['reviewed_bundle_id']!r}, current={manifest_bundle_id!r}"
        )

    stages = getattr(work_item, "plan_review_stages", None) or {}
    current_content_id = stages.get("review_content_id")
    feedback_content_id = feedback.get("reviewed_content_id")
    if feedback_content_id is None or feedback_content_id != current_content_id:
        failures.append(ClauseFailure(
            "review_content_id",
            f"feedback declares {feedback_content_id!r}, ledger's current value is "
            f"{current_content_id!r}",
        ))

    local = stages.get("LOCAL_MODEL_PLAN_REVIEW")
    if not (isinstance(local, dict) and local.get("verdict") == "APPROVE"):
        failures.append(ClauseFailure(
            "local approval",
            "no current LOCAL_MODEL_PLAN_REVIEW APPROVE is recorded for this review_content_id",
        ))

    manifest_head = manifest.get("generation_head")
    if manifest_head is None or current_head is None or manifest_head != current_head:
        failures.append(ClauseFailure(
            "generation_head",
            f"MANIFEST.md's generation_head is {manifest_head!r}, target HEAD is "
            f"{current_head!r}",
        ))

    return AdmissibilityResult(
        admissible=not failures, failures=tuple(failures), advisories=tuple(advisories),
    )


def evaluate_apply_plan_review_admissibility(
    *, feedback: dict[str, str | None], manifest: dict[str, Any], work_item: Any,
    current_head: str | None,
) -> AdmissibilityResult:
    """The ``"1"`` ``AWAITING_EXTERNAL_PLAN_REVIEW`` ->
    ``/apply-plan-review`` column: no role, no ``review_content_id`` (a
    ``"1"`` item has no ``plan_review_stages`` ledger) -- but
    ``Reviewed bundle ID:`` equality is **hard** here, unlike the manual
    column, since there is no content id to fall back on."""
    failures: list[ClauseFailure] = []

    status = feedback.get("status")
    if status not in _VALID_STATUSES:
        failures.append(ClauseFailure(
            "Status", f"parses as {status!r}, expected one of {_VALID_STATUSES}",
        ))

    for label, key in (
        ("Reviewed bundle ID", "reviewed_bundle_id"),
        ("Reviewed base commit", "reviewed_base_commit"),
        ("Work item", "work_item"),
    ):
        if feedback.get(key) is None:
            failures.append(ClauseFailure(label, "absent"))

    work_item_id = work_item.work_item_id
    if feedback.get("work_item") is not None and feedback["work_item"] != work_item_id:
        failures.append(ClauseFailure(
            "Work item", f"names {feedback['work_item']!r}, expected {work_item_id!r}",
        ))

    base_commit = getattr(work_item, "base_commit", None)
    if feedback.get("reviewed_base_commit") is not None and feedback["reviewed_base_commit"] != base_commit:
        failures.append(ClauseFailure(
            "Reviewed base commit",
            f"names {feedback['reviewed_base_commit']!r}, expected {base_commit!r}",
        ))

    manifest_bundle_id = manifest.get("bundle_id")
    if (
        feedback.get("reviewed_bundle_id") is not None
        and feedback["reviewed_bundle_id"] != manifest_bundle_id
    ):
        failures.append(ClauseFailure(
            "Reviewed bundle ID",
            f"names {feedback['reviewed_bundle_id']!r}, current bundle_id is "
            f"{manifest_bundle_id!r} -- hard at this path, unlike the manual column",
        ))

    manifest_head = manifest.get("generation_head")
    if manifest_head is None or current_head is None or manifest_head != current_head:
        failures.append(ClauseFailure(
            "generation_head",
            f"MANIFEST.md's generation_head is {manifest_head!r}, target HEAD is "
            f"{current_head!r}",
        ))

    return AdmissibilityResult(admissible=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# git plumbing this module needs directly (never imports ``scripts/``).
# ---------------------------------------------------------------------------


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           check=False)


def _current_head(root: Path) -> str | None:
    result = _run_git(root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


_FUNCTIONAL_CHECKLIST_TRAILER_RE = re.compile(
    r"^Workflow-Functional-Checklist:\s*(\S+)\s*$", re.MULTILINE,
)


def functional_checklist_evidence(
    root: Path, work_item_id: str, base_commit: str | None, head: str | None,
    implementation_revision: int | None,
) -> dict[str, str] | None:
    """The round-scoped, content-identity-aware lookup for the
    ``Workflow-Functional-Checklist: <work_item_id>/<implementation_revision>/<blob>``
    trailer this module's own read-only reimplementation of
    ``workflow_state.discover_current_functional_checklist_evidence``
    performs: walks ``head``'s first-parent chain within
    ``base_commit..head`` (nearest-``head``-first, since ``git log``'s
    default order is reverse-chronological) and returns the first commit
    whose trailer value starts with ``<work_item_id>/<implementation_revision>/``.
    ``None`` if any of ``base_commit``/``head``/``implementation_revision``
    is unavailable, or if the range holds no matching commit."""
    if base_commit is None or head is None or implementation_revision is None:
        return None
    result = _run_git(
        root, ["log", "--first-parent", "--format=%H%x00%B%x03", f"{base_commit}..{head}"],
    )
    if result.returncode != 0:
        return None
    prefix = f"{work_item_id}/{implementation_revision}/"
    for chunk in result.stdout.split("\x03"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        commit_sha, _, body = chunk.partition("\x00")
        match = _FUNCTIONAL_CHECKLIST_TRAILER_RE.search(body)
        if match and match.group(1).startswith(prefix):
            return {"commit_sha": commit_sha, "blob": match.group(1)[len(prefix):]}
    return None


def functional_review_findings_path(root: Path, work_item_id: str) -> Path:
    return resolve_feedback_dir(root, work_item_id) / "FUNCTIONAL_REVIEW.md"


def functional_review_consumed_marker_path(root: Path, work_item_id: str) -> Path:
    return resolve_feedback_dir(root, work_item_id) / "FUNCTIONAL_REVIEW.consumed"


def functional_review_findings_consumed(root: Path, work_item_id: str) -> bool | None:
    """``None`` if ``FUNCTIONAL_REVIEW.md`` does not exist yet (there is
    nothing to have consumed). ``True`` if the consumed-marker's recorded
    hash equals the findings file's current git blob hash -- mirroring
    ``workflow_fingerprint.assert_functional_review_not_already_consumed``'s
    own presence-then-content read shape. ``False`` for a missing marker
    or a hash mismatch (genuinely new, unconsumed findings)."""
    findings_path = root / functional_review_findings_path(root, work_item_id)
    if not findings_path.is_file():
        return None
    marker_path = root / functional_review_consumed_marker_path(root, work_item_id)
    try:
        recorded = marker_path.read_text().strip()
    except OSError:
        return False
    result = subprocess.run(
        ["git", "hash-object", "--", str(findings_path)], cwd=root, capture_output=True,
        text=True, check=False,
    )
    if result.returncode != 0:
        return False
    return bool(recorded) and recorded == result.stdout.strip()


# ---------------------------------------------------------------------------
# Implementation-stage evidence readers (automatic-lifecycle-orchestration
# CP1). Pure, read-only, fail closed, and not yet wired into any decision:
# CP2's postconditions and CP4/CP4B's gates consume them.
# ---------------------------------------------------------------------------

#: The two ``"2.2"`` implementation-review stage roles, spelled exactly as
#: ``workflow_state.LOCAL_MODEL_IMPLEMENTATION_REVIEW``/
#: ``MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` spell them. The ledger was
#: introduced fresh at ``"2.2"``, so no legacy-cased alias exists for either
#: (``_normalize_implementation_review_stage_key`` is the identity).
LOCAL_IMPLEMENTATION_ROLE = "LOCAL_MODEL_IMPLEMENTATION_REVIEW"
MANUAL_IMPLEMENTATION_ROLE = "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"

#: Any implementation-stage phase resolves ``<bundle_dir>`` by the same
#: scoped-else-flat rule (``resolve_bundle_dir``); this one is used as the
#: representative, so the resolution never depends on the caller's phase.
_IMPLEMENTATION_STAGE_BUNDLE_PHASE = "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"

#: The ``clause`` codes :func:`implementation_bundle_coherence` returns, in
#: evaluation order -- a caller chooses recovery text from the code, never
#: by parsing ``detail``.
IMPLEMENTATION_BUNDLE_CLAUSES: tuple[str, ...] = (
    "absent", "stage", "work_item_id", "bundle_id", "worktree_root",
    "implementation_revision", "reviewed_implementation_head", "generation_head",
)

_STATE_REL_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"


def implementation_bundle_dir(root: Path, work_item_id: str) -> Path:
    """The implementation-stage ``<bundle_dir>``, scoped-else-flat."""
    return resolve_bundle_dir(root, work_item_id, phase=_IMPLEMENTATION_STAGE_BUNDLE_PHASE)


def target_worktree_root(root: Path) -> str | None:
    """``git rev-parse --show-toplevel`` for the target -- the exact value
    ``workflow_fingerprint.current_worktree_root_and_head`` records as a
    manifest's ``worktree_root`` and ``assert_local_generation_matches``
    compares. ``None`` when it cannot be read."""
    result = _run_git(root, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def implementation_bundle_coherence(
    root: Path, work_item: Any, current_head: str | None, *,
    require_current_generation_head: bool,
) -> tuple[bool, str | None, str]:
    """``(coherent, clause, detail)``: the single definition of "the
    current implementation bundle belongs to this work item's current
    implementation round".

    Coherent iff the implementation-stage ``<bundle_dir>``
    (:func:`implementation_bundle_dir`) has a readable ``MANIFEST.md``
    whose ``stage`` is ``implementation``; whose ``work_item_id`` matches;
    which carries a ``bundle_id``; whose ``worktree_root`` equals the
    target's :func:`target_worktree_root`; whose ``implementation_revision``
    is a plain decimal equal to the state's; whose
    ``reviewed_implementation_head`` equals the state's; and -- only when
    ``require_current_generation_head`` -- whose ``generation_head`` equals
    ``current_head``. The flag is ``False`` at ``APPLYING_REVIEW_FEEDBACK``
    alone, where ``HEAD`` is legitimately ahead of ``generation_head``.

    Revision- and binding-level only: nothing here recomputes
    ``review_content_id``/``bundle_id``. A ``None`` on any state side
    (``implementation_revision``, ``reviewed_implementation_head``, the
    worktree root, or ``current_head`` when it is required) is incoherent.
    ``clause`` is ``None`` when coherent, otherwise the first failing
    member of :data:`IMPLEMENTATION_BUNDLE_CLAUSES`; ``detail`` names that
    clause's observed value."""
    work_item_id = work_item.work_item_id
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    manifest_path = bundle_dir / "MANIFEST.md"
    if not (root / bundle_dir).is_dir():
        return False, "absent", (
            f"implementation bundle directory {bundle_dir} is absent (withdrawn or never generated)"
        )
    manifest = read_manifest_fields(root, bundle_dir)
    if not manifest["_exists"]:
        return False, "absent", f"{manifest_path} is missing or unreadable"
    if manifest["stage"] != "implementation":
        return False, "stage", f"manifest stage {manifest['stage']!r} != 'implementation'"
    if manifest["work_item_id"] != work_item_id:
        return False, "work_item_id", (
            f"manifest work_item_id {manifest['work_item_id']!r} != {work_item_id!r}"
        )
    if not manifest["bundle_id"]:
        return False, "bundle_id", "manifest bundle_id is missing"

    worktree_root = target_worktree_root(root)
    if worktree_root is None:
        return False, "worktree_root", (
            "the target's worktree root (git rev-parse --show-toplevel) could not be read"
        )
    if manifest["worktree_root"] != worktree_root:
        return False, "worktree_root", (
            f"manifest worktree_root {manifest['worktree_root']!r} != target worktree root "
            f"{worktree_root!r}"
        )

    state_revision = getattr(work_item, "implementation_revision", None)
    if not _is_plain_int(state_revision):
        return False, "implementation_revision", (
            f"state implementation_revision is {state_revision!r}, not an integer"
        )
    observed_revision = manifest["implementation_revision"]
    if observed_revision is None:
        return False, "implementation_revision", "manifest implementation_revision is missing"
    if not _DECIMAL_RE.fullmatch(observed_revision):
        return False, "implementation_revision", (
            f"manifest implementation_revision {observed_revision!r} is not an integer"
        )
    if int(observed_revision) != state_revision:
        return False, "implementation_revision", (
            f"manifest implementation_revision {int(observed_revision)} != state "
            f"implementation_revision {state_revision}"
        )

    state_head = getattr(work_item, "reviewed_implementation_head", None)
    if state_head is None:
        return False, "reviewed_implementation_head", "state reviewed_implementation_head is None"
    if manifest["reviewed_implementation_head"] != state_head:
        return False, "reviewed_implementation_head", (
            f"manifest reviewed_implementation_head {manifest['reviewed_implementation_head']!r} "
            f"!= state reviewed_implementation_head {state_head!r}"
        )

    if require_current_generation_head:
        manifest_head = manifest["generation_head"]
        if current_head is None:
            return False, "generation_head", "the target's HEAD could not be read"
        if manifest_head != current_head:
            return False, "generation_head", (
                f"manifest generation_head {manifest_head!r} != target HEAD {current_head!r}"
            )

    return True, None, (
        f"manifest at {manifest_path} matches work_item_id {work_item_id!r}, "
        f"implementation_revision {state_revision}, reviewed_implementation_head {state_head}"
        + (f", generation_head {current_head}" if require_current_generation_head else "")
    )


_REVIEW_REQUEST_CONTENT_ID_RE = re.compile(r"^review_content_id: ([0-9a-f]{64})$", re.MULTILINE)


def read_review_request_fields(root: Path, bundle_dir: Path) -> dict[str, Any]:
    """``<bundle_dir>/REVIEW_REQUEST.md``'s ``review_content_id: <hex>``
    statements, read with the exact whole-file pattern
    ``workflow_fingerprint.assert_review_request_states_review_content_id``
    uses (the generator asserted at generation time that they state the
    manifest's value). ``review_content_ids`` holds every distinct stated
    value in file order -- empty when none is stated, more than one when
    the file disagrees with itself -- and ``_exists`` is ``False`` when the
    file cannot be read."""
    path = root / bundle_dir / "REVIEW_REQUEST.md"
    try:
        text = path.read_text()
    except OSError:
        return {"_exists": False, "review_content_ids": ()}
    values = tuple(dict.fromkeys(_REVIEW_REQUEST_CONTENT_ID_RE.findall(text)))
    return {"_exists": True, "review_content_ids": values}


@dataclasses.dataclass(frozen=True)
class LedgerView:
    """The ``"2.2"`` ``implementation_review_stages`` ledger, as read by
    :func:`read_implementation_review_ledger`. ``local``/``manual`` are
    each ``{"verdict", "bundle_id", "round"}`` or ``None``. ``malformed``
    names the shape defect when the ledger was present but unreadable, in
    which case every other field is ``None`` ("no stage recorded")."""

    review_content_id: str | None
    local: dict | None
    manual: dict | None
    malformed: str | None = None


def _read_ledger_stage(name: str, value: Any) -> tuple[dict | None, str | None]:
    """``(stage, problem)`` for one ledger stage entry. Workflow records a
    stage only as a completed ``APPROVE`` carrying ``bundle_id``, ``round``
    and ``completed_at`` (``record_local_implementation_review``/
    ``record_manual_implementation_review``; ``_validate_implementation_
    review_stages`` refuses any other verdict), so anything else is a
    problem, never a partial read."""
    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, f"{name} is {type(value).__name__}, not a JSON object"
    verdict = value.get("verdict")
    if verdict != "APPROVE":
        return None, f"{name}.verdict is {verdict!r}, not 'APPROVE' (the only verdict Workflow records)"
    bundle_id = value.get("bundle_id")
    if not isinstance(bundle_id, str) or not bundle_id:
        return None, f"{name}.bundle_id is {bundle_id!r}, not a non-empty string"
    round_number = value.get("round")
    if not _is_plain_int(round_number):
        return None, f"{name}.round is {round_number!r}, not an integer"
    return {"verdict": verdict, "bundle_id": bundle_id, "round": round_number}, None


def read_implementation_review_ledger(work_item: Any) -> LedgerView:
    """Read ``work_item.implementation_review_stages`` into a
    :class:`LedgerView`. Shape-tolerant (it never raises, and extra keys
    are ignored) and fail closed: a ledger that is not a JSON object, has
    no string ``review_content_id``, carries a malformed stage entry, or
    records the manual stage without the local one (Workflow's own
    ``ManualImplementationStageWithoutLocalStageError`` invariant) reads as
    "no stage recorded", with ``malformed`` naming why. An absent/``null``
    ledger is simply empty, not malformed."""
    stages = getattr(work_item, "implementation_review_stages", None)
    if stages is None:
        return LedgerView(review_content_id=None, local=None, manual=None)

    def _malformed(problem: str) -> LedgerView:
        return LedgerView(review_content_id=None, local=None, manual=None, malformed=problem)

    if not isinstance(stages, dict):
        return _malformed(f"implementation_review_stages is {type(stages).__name__}, not a JSON object")
    review_content_id = stages.get("review_content_id")
    if not isinstance(review_content_id, str) or not review_content_id:
        return _malformed(f"review_content_id is {review_content_id!r}, not a non-empty string")
    local, problem = _read_ledger_stage(LOCAL_IMPLEMENTATION_ROLE, stages.get(LOCAL_IMPLEMENTATION_ROLE))
    if problem is not None:
        return _malformed(problem)
    manual, problem = _read_ledger_stage(MANUAL_IMPLEMENTATION_ROLE, stages.get(MANUAL_IMPLEMENTATION_ROLE))
    if problem is not None:
        return _malformed(problem)
    if manual is not None and local is None:
        return _malformed(f"{MANUAL_IMPLEMENTATION_ROLE} is recorded without {LOCAL_IMPLEMENTATION_ROLE}")
    return LedgerView(review_content_id=review_content_id, local=local, manual=manual)


# --- committed (``HEAD``-side) Workflow state -------------------------------


def _committed_state(root: Path, rev: str) -> dict | None:
    """``git show <rev>:docs/ai-workflow/WORKFLOW_STATE.json``, parsed.
    ``None`` when the revision or the file cannot be read, or the content
    is not a JSON object whose ``work_items`` is an object."""
    result = _run_git(root, ["show", f"{rev}:{_STATE_REL_PATH}"])
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("work_items"), dict):
        return None
    return data


def committed_work_item(root: Path, work_item_id: str, rev: str = "HEAD") -> dict | None:
    """The work item's entry in ``WORKFLOW_STATE.json`` as committed at
    ``rev`` -- never the working tree's. ``None`` when that file is
    unreadable at ``rev`` or carries no object entry for the work item."""
    state = _committed_state(root, rev)
    if state is None:
        return None
    entry = state["work_items"].get(work_item_id)
    return entry if isinstance(entry, dict) else None


def _committed_phase(root: Path, work_item_id: str, rev: str) -> str | None:
    entry = committed_work_item(root, work_item_id, rev)
    if entry is None:
        return None
    phase = entry.get("phase")
    return phase if isinstance(phase, str) else None


def committed_checkpoint_statuses(root: Path, rev: str = "HEAD") -> dict[tuple[str, str], str] | None:
    """Every checkpoint status in ``WORKFLOW_STATE.json`` as committed at
    ``rev``, keyed ``(work_item_id, checkpoint_id)`` -- the same committed
    fact ``workflow_state.committed_checkpoint_status`` reads. ``None``
    when the file is unreadable at ``rev``. An entry whose ``checkpoints``
    is not an object, or a checkpoint without a string ``status``, is
    simply absent from the result (so it never reads as ``COMPLETE``)."""
    state = _committed_state(root, rev)
    if state is None:
        return None
    statuses: dict[tuple[str, str], str] = {}
    for work_item_id, entry in state["work_items"].items():
        checkpoints = entry.get("checkpoints") if isinstance(entry, dict) else None
        if not isinstance(checkpoints, dict):
            continue
        for checkpoint_id, checkpoint in checkpoints.items():
            status = checkpoint.get("status") if isinstance(checkpoint, dict) else None
            if isinstance(status, str):
                statuses[(work_item_id, checkpoint_id)] = status
    return statuses


# --- bundle-generation-record commits ---------------------------------------

_GENERATION_RECORD_TRAILER = "Workflow-Bundle-Generation-Record"
_WORK_ITEM_TRAILER = "Workflow-Work-Item"
_SUPERSEDES_TRAILER = "Workflow-Supersedes"


def commit_trailers(root: Path, commit: str) -> dict[str, str] | None:
    """A commit's trailers, parsed exactly as
    ``workflow_state._commit_trailers`` parses them: the full message piped
    through ``git interpret-trailers --parse``, one ``key: value`` per
    line, a later duplicate key overwriting an earlier one. ``None`` when
    the commit or the parse cannot be read."""
    body = _run_git(root, ["log", "-1", "--format=%B", commit])
    if body.returncode != 0:
        return None
    parsed = subprocess.run(
        ["git", "-C", str(root), "interpret-trailers", "--parse"],
        input=body.stdout, capture_output=True, text=True, check=False,
    )
    if parsed.returncode != 0:
        return None
    trailers: dict[str, str] = {}
    for line in parsed.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        trailers[key.strip()] = value.strip()
    return trailers


def _names_work_item_generation_record(trailers: dict[str, str] | None, work_item_id: str) -> bool:
    """Whether ``trailers`` carry this work item's generation-record
    trailer: ``Workflow-Work-Item`` is the work item (the pairing
    ``_discover_trailer_commits`` requires) and the
    ``Workflow-Bundle-Generation-Record`` value is
    ``<work_item_id>/<implementation_revision>`` for it."""
    if not trailers or trailers.get(_WORK_ITEM_TRAILER) != work_item_id:
        return False
    value = trailers.get(_GENERATION_RECORD_TRAILER)
    if not value:
        return False
    named, separator, _revision = value.rpartition("/")
    return bool(separator) and named == work_item_id


def bundle_generation_record_role(trailers: dict[str, str] | None, work_item_id: str) -> str | None:
    """``"ordinary"``, ``"recovered"`` or ``None``, classified exactly as
    ``workflow_state._bundle_generation_record_role`` does -- the exact
    two-trailer set, or that set plus ``Workflow-Supersedes``; any other
    set is never coerced into a role -- and only for a trailer set that
    names this work item (:func:`_names_work_item_generation_record`)."""
    if not _names_work_item_generation_record(trailers, work_item_id):
        return None
    keys = set(trailers)
    if keys == {_GENERATION_RECORD_TRAILER, _WORK_ITEM_TRAILER}:
        return "ordinary"
    if keys == {_GENERATION_RECORD_TRAILER, _WORK_ITEM_TRAILER, _SUPERSEDES_TRAILER}:
        return "recovered"
    return None


@dataclasses.dataclass(frozen=True)
class GenerationRecordView:
    """What plain ``git log``/``git show`` say about this work item's
    bundle-generation-record commits (:func:`generation_record_view`).

    - ``head``: ``HEAD``'s SHA, or ``None`` if unreadable.
    - ``head_role``: ``HEAD``'s own role (:func:`bundle_generation_record_role`).
    - ``head_phase``/``parent_phase``: the work item's committed phase at
      ``HEAD``/``HEAD^``.
    - ``newer_records``: the first-parent commits in
      ``manifest_generation_head..HEAD`` carrying this work item's
      generation-record trailer (whatever their trailer set's role), newest
      first; ``None`` when ``manifest_generation_head`` is absent, is not a
      full object name, or is not an ancestor of ``HEAD``.
    - ``latest_record_parent_phase``: the committed phase at the parent of
      ``newer_records[0]``, or ``None``."""

    head: str | None
    head_role: str | None
    head_phase: str | None
    parent_phase: str | None
    newer_records: tuple[str, ...] | None
    latest_record_parent_phase: str | None


#: A full object name, the only shape a manifest's ``generation_head`` line
#: carries (``git rev-parse HEAD``). Anything else is never handed to
#: ``git`` as a revision -- a value read from a file must not be able to
#: become a command-line option.
_FULL_OBJECT_NAME_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _newer_generation_records(
    root: Path, work_item_id: str, manifest_generation_head: str | None,
) -> tuple[str, ...] | None:
    if not manifest_generation_head or not _FULL_OBJECT_NAME_RE.fullmatch(manifest_generation_head):
        return None
    if _run_git(root, ["merge-base", "--is-ancestor", manifest_generation_head, "HEAD"]).returncode != 0:
        return None
    log = _run_git(root, ["log", "--first-parent", "--format=%H", f"{manifest_generation_head}..HEAD"])
    if log.returncode != 0:
        return None
    return tuple(
        commit for commit in log.stdout.split()
        if _names_work_item_generation_record(commit_trailers(root, commit), work_item_id)
    )


def generation_record_view(
    root: Path, work_item_id: str, manifest_generation_head: str | None,
) -> GenerationRecordView:
    """The read-only generation-record facts CP4B's recovery gate chooses
    its text from (see :class:`GenerationRecordView`). Read with plain
    ``git``; nothing from ``scripts/`` is imported."""
    head = _current_head(root)
    head_role = (
        bundle_generation_record_role(commit_trailers(root, head), work_item_id)
        if head is not None else None
    )
    newer_records = _newer_generation_records(root, work_item_id, manifest_generation_head)
    latest_record_parent_phase = (
        _committed_phase(root, work_item_id, f"{newer_records[0]}^") if newer_records else None
    )
    return GenerationRecordView(
        head=head,
        head_role=head_role,
        head_phase=_committed_phase(root, work_item_id, "HEAD") if head is not None else None,
        parent_phase=_committed_phase(root, work_item_id, "HEAD^") if head is not None else None,
        newer_records=newer_records,
        latest_record_parent_phase=latest_record_parent_phase,
    )


# --- admissibility at the implementation stage ------------------------------


def evaluate_manual_implementation_stage_admissibility(
    *, feedback: dict[str, str | None], manifest: dict[str, Any], review_request: dict[str, Any],
    work_item: Any, current_head: str | None, current_worktree_root: str | None,
) -> AdmissibilityResult:
    """The ``"2.2"`` ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` ->
    ``/record-manual-implementation-review`` filter. It mirrors
    ``validate_manual_implementation_review_preconditions`` plus that
    command's steps 4-6, so that a verdict admitted here is never one the
    command -- or the ``/apply-implementation-review`` its ``REVISE`` leads
    to -- refuses on evidence the Controller can see:

    - ``Reviewer role`` exactly ``MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW``
      (no alias, unlike the plan stage);
    - ``Status`` one of ``APPROVE``/``REVISE``/``BLOCK``;
    - the three ``WFR-03`` binding lines present, with ``Work item`` and
      ``Reviewed base commit`` equal to the work item's;
    - ``Reviewed review content ID`` equal to the ledger's
      ``review_content_id`` (hard), and the ledger's equal to the
      manifest's;
    - ``REVIEW_REQUEST.md`` stating exactly the manifest's
      ``review_content_id`` (the command reads that file at its step 4, and
      the generator asserted the equality, so a difference means a
      post-generation edit);
    - a ``LOCAL_MODEL_IMPLEMENTATION_REVIEW`` ``APPROVE`` recorded and no
      manual stage recorded yet;
    - ``generation_head`` equal to ``current_head`` and ``worktree_root``
      equal to ``current_worktree_root`` (:func:`target_worktree_root`) --
      the coherence clauses the command's own recomputation enforces.

    A ``Reviewed bundle ID`` mismatch is **advisory** for ``APPROVE``/
    ``BLOCK`` (the command's own ``check_manual_stage_bundle_id_advisory``)
    and **hard** for ``REVISE``: an ingested mismatched ``REVISE`` moves the
    item to ``APPLYING_REVIEW_FEEDBACK``, where
    ``/apply-implementation-review`` step 1's
    ``assert_feedback_matches_bundle`` refuses on the same mismatch."""
    failures: list[ClauseFailure] = []
    advisories: list[str] = []

    role = feedback.get("reviewer_role")
    if role != MANUAL_IMPLEMENTATION_ROLE:
        failures.append(ClauseFailure(
            "Reviewer role",
            f"declares {role!r}, expected exactly {MANUAL_IMPLEMENTATION_ROLE} (no alias at the "
            "implementation stage)",
        ))

    status = feedback.get("status")
    if status not in _VALID_STATUSES:
        failures.append(ClauseFailure(
            "Status", f"parses as {status!r}, expected one of {_VALID_STATUSES}",
        ))

    for label, key in (
        ("Reviewed bundle ID", "reviewed_bundle_id"),
        ("Reviewed base commit", "reviewed_base_commit"),
        ("Work item", "work_item"),
    ):
        if feedback.get(key) is None:
            failures.append(ClauseFailure(label, "absent"))

    work_item_id = work_item.work_item_id
    if feedback.get("work_item") is not None and feedback["work_item"] != work_item_id:
        failures.append(ClauseFailure(
            "Work item", f"names {feedback['work_item']!r}, expected {work_item_id!r}",
        ))

    base_commit = getattr(work_item, "base_commit", None)
    if feedback.get("reviewed_base_commit") is not None and feedback["reviewed_base_commit"] != base_commit:
        failures.append(ClauseFailure(
            "Reviewed base commit",
            f"names {feedback['reviewed_base_commit']!r}, expected {base_commit!r}",
        ))

    ledger = read_implementation_review_ledger(work_item)
    feedback_content_id = feedback.get("reviewed_content_id")
    if feedback_content_id is None or feedback_content_id != ledger.review_content_id:
        failures.append(ClauseFailure(
            "review_content_id",
            f"feedback declares {feedback_content_id!r}, the ledger's review_content_id is "
            f"{ledger.review_content_id!r}"
            + (f" (ledger malformed: {ledger.malformed})" if ledger.malformed else ""),
        ))

    manifest_content_id = manifest.get("review_content_id")
    if ledger.review_content_id is None or ledger.review_content_id != manifest_content_id:
        failures.append(ClauseFailure(
            "ledger review_content_id",
            f"the ledger records {ledger.review_content_id!r}, MANIFEST.md states "
            f"{manifest_content_id!r}",
        ))

    stated = tuple(review_request.get("review_content_ids") or ())
    if manifest_content_id is None or stated != (manifest_content_id,):
        failures.append(ClauseFailure(
            "REVIEW_REQUEST.md review_content_id",
            (f"REVIEW_REQUEST.md states {list(stated)!r}" if review_request.get("_exists")
             else "REVIEW_REQUEST.md is missing or unreadable")
            + f", MANIFEST.md states {manifest_content_id!r}",
        ))

    if ledger.local is None:
        failures.append(ClauseFailure(
            "local approval",
            f"no {LOCAL_IMPLEMENTATION_ROLE} APPROVE is recorded in the ledger"
            + (f" (ledger malformed: {ledger.malformed})" if ledger.malformed else ""),
        ))
    if ledger.manual is not None:
        failures.append(ClauseFailure(
            "manual stage",
            f"{MANUAL_IMPLEMENTATION_ROLE} is already recorded against review_content_id "
            f"{ledger.review_content_id!r}",
        ))

    manifest_head = manifest.get("generation_head")
    if manifest_head is None or current_head is None or manifest_head != current_head:
        failures.append(ClauseFailure(
            "generation_head",
            f"MANIFEST.md's generation_head is {manifest_head!r}, target HEAD is {current_head!r}",
        ))

    manifest_root = manifest.get("worktree_root")
    if manifest_root is None or current_worktree_root is None or manifest_root != current_worktree_root:
        failures.append(ClauseFailure(
            "worktree_root",
            f"MANIFEST.md's worktree_root is {manifest_root!r}, the target's worktree root is "
            f"{current_worktree_root!r}",
        ))

    feedback_bundle_id = feedback.get("reviewed_bundle_id")
    manifest_bundle_id = manifest.get("bundle_id")
    if status == "REVISE":
        if feedback_bundle_id is not None and feedback_bundle_id != manifest_bundle_id:
            failures.append(ClauseFailure(
                "Reviewed bundle ID",
                f"names {feedback_bundle_id!r}, current bundle_id is {manifest_bundle_id!r} -- hard "
                "for a REVISE: once ingested, /apply-implementation-review step 1 "
                "(assert_feedback_matches_bundle) refuses on the same mismatch",
            ))
    elif (
        feedback_bundle_id is not None and manifest_bundle_id is not None
        and feedback_bundle_id != manifest_bundle_id
    ):
        advisories.append(
            f"bundle_id mismatch (advisory only, does not block ingestion): "
            f"feedback={feedback_bundle_id!r}, current={manifest_bundle_id!r}"
        )

    return AdmissibilityResult(
        admissible=not failures, failures=tuple(failures), advisories=tuple(advisories),
    )


def evaluate_apply_implementation_review_admissibility(
    *, feedback: dict[str, str | None], manifest: dict[str, Any], work_item: Any,
    current_head: str | None,
) -> AdmissibilityResult:
    """The ``"2.2"`` ``APPLYING_REVIEW_FEEDBACK`` ->
    ``/apply-implementation-review`` filter.

    The command's own step-1 clauses: the three ``WFR-03`` binding lines
    present, ``Reviewed bundle ID`` equal to the manifest's ``bundle_id``
    (hard -- ``assert_feedback_matches_bundle``), and ``Work item``/
    ``Reviewed base commit`` equal to the work item's.

    Two further clauses are **not** the command's own -- its step 1 checks
    neither role nor ``Status``, and it pins and then applies a ``BLOCK``.
    They are this Controller's deliberately narrower automation scope, fail
    closed: ``Reviewer role`` exactly ``LOCAL_MODEL_IMPLEMENTATION_REVIEW``
    or ``MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW``, and ``Status`` exactly
    ``REVISE``. The automated ``"2.2"`` loop reaches this phase only
    through a two-stage writer's ``REVISE`` branch; a ``BLOCK`` from either
    writer never leaves its review phase. So any other verdict on file here
    is human territory.

    There is deliberately **no** ``generation_head`` clause: ``HEAD`` is
    legitimately ahead of ``generation_head`` at this phase (the pending
    review-stage state commit and any fix commits land before the next
    generation record), and the command's own step 7 handles those
    commits. ``current_head`` is accepted for signature symmetry with the
    other evaluators and is not consulted."""
    failures: list[ClauseFailure] = []

    for label, key in (
        ("Reviewed bundle ID", "reviewed_bundle_id"),
        ("Reviewed base commit", "reviewed_base_commit"),
        ("Work item", "work_item"),
    ):
        if feedback.get(key) is None:
            failures.append(ClauseFailure(label, "absent"))

    manifest_bundle_id = manifest.get("bundle_id")
    if feedback.get("reviewed_bundle_id") is not None and feedback["reviewed_bundle_id"] != manifest_bundle_id:
        failures.append(ClauseFailure(
            "Reviewed bundle ID",
            f"names {feedback['reviewed_bundle_id']!r}, current bundle_id is {manifest_bundle_id!r} "
            "-- /apply-implementation-review step 1 (assert_feedback_matches_bundle) refuses on it",
        ))

    work_item_id = work_item.work_item_id
    if feedback.get("work_item") is not None and feedback["work_item"] != work_item_id:
        failures.append(ClauseFailure(
            "Work item", f"names {feedback['work_item']!r}, expected {work_item_id!r}",
        ))

    base_commit = getattr(work_item, "base_commit", None)
    if feedback.get("reviewed_base_commit") is not None and feedback["reviewed_base_commit"] != base_commit:
        failures.append(ClauseFailure(
            "Reviewed base commit",
            f"names {feedback['reviewed_base_commit']!r}, expected {base_commit!r}",
        ))

    role = feedback.get("reviewer_role")
    if role not in (LOCAL_IMPLEMENTATION_ROLE, MANUAL_IMPLEMENTATION_ROLE):
        failures.append(ClauseFailure(
            "Reviewer role",
            f"declares {role!r}; this Controller automates only a verdict from one of the two "
            f"\"2.2\" implementation-review stages ({LOCAL_IMPLEMENTATION_ROLE} or "
            f"{MANUAL_IMPLEMENTATION_ROLE}) -- a narrower scope than the command's own step 1",
        ))

    status = feedback.get("status")
    if status == "APPROVE":
        failures.append(ClauseFailure(
            "Status",
            "APPROVE is on file: legal here only for a late fix entered from "
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW over the manual APPROVE "
            "(enter_applying_review_feedback) and then interrupted, which is human territory -- "
            "this Controller automates only a two-stage writer's REVISE",
        ))
    elif status == "BLOCK":
        failures.append(ClauseFailure(
            "Status",
            "BLOCK is on file: a two-stage writer's BLOCK never leaves its review phase, so the "
            "file changed after the transition, and review-implementation.md A7 reserves a BLOCK "
            "for explicit user resolution",
        ))
    elif status != "REVISE":
        failures.append(ClauseFailure("Status", f"parses as {status!r}, expected REVISE"))

    return AdmissibilityResult(admissible=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# The three CP4-placeholder phases, now with a real evidence read.
# ---------------------------------------------------------------------------


def _decide_awaiting_local_plan_review(root: Path, work_item_id: str, work_item: Any) -> Decision:
    phase = "AWAITING_LOCAL_PLAN_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    feedback = read_feedback_fields(root, feedback_dir)
    if feedback is not None:
        role = _normalize_role(feedback.get("reviewer_role"))
        status = feedback.get("status")
        if role == "LOCAL_MODEL_PLAN_REVIEW" and status == "BLOCK":
            # The local stage is deliberately exempt from the admissibility
            # rule: applying it here would invert this repair's polarity,
            # turning a genuine BLOCK with one malformed binding field into
            # a launch of /review-plan.
            return Decision(
                observed_phase=phase,
                evidence=("current-round LOCAL_MODEL_PLAN_REVIEW Status: BLOCK",),
                action=None, automatic=False,
                gate=HumanGate(
                    repository=str(root), work_item_id=work_item_id, phase=phase,
                    what_is_required=(
                        "the local reviewer blocked this round; explicit user resolution "
                        "is required before any further command runs"
                    ),
                    artifact_path=str(feedback_dir / "REVIEW_FEEDBACK.md"),
                    safe_resume_command=f"/review-plan {work_item_id}",
                ),
                declined=False,
                reason=f"{phase}: /review-plan step 9 requires explicit user resolution "
                       "before any further command runs after a BLOCK",
            )
    return Decision(
        observed_phase=phase, evidence=(),
        action=Action(command=f"/review-plan {work_item_id}"),
        automatic=True, gate=None, declined=False,
        reason=f"{phase}: no current-round BLOCK verdict on file -- a fresh worker is the "
               "fresh, independent session this stage asks for",
    )


def _decide_awaiting_manual_external_plan_review(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
    phase = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    bundle_dir = resolve_bundle_dir(root, work_item_id, phase=phase)
    feedback = read_feedback_fields(root, feedback_dir)

    role = _normalize_role(feedback.get("reviewer_role")) if feedback is not None else None
    if feedback is None or role != "MANUAL_EXTERNAL_PLAN_REVIEW":
        # The local stage's own feedback must never satisfy the manual
        # stage's arrival test (round 3's B1).
        reason = (
            f"{phase}: no current-round REVIEW_FEEDBACK.md on file"
            if feedback is None else
            f"{phase}: feedback declares Reviewer role {feedback.get('reviewer_role')!r}, "
            "not MANUAL_EXTERNAL_PLAN_REVIEW"
        )
        return Decision(
            observed_phase=phase, evidence=(), action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "upload the current plan bundle to a manual external reviewer and "
                    "paste the verdict into REVIEW_FEEDBACK.md, declaring "
                    "Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW"
                ),
                artifact_path=str(bundle_dir), safe_resume_command=f"/record-manual-plan-review {work_item_id}",
            ),
            declined=False, reason=reason,
        )

    manifest = read_manifest_fields(root, bundle_dir)
    current_head = _current_head(root)
    result = evaluate_manual_stage_admissibility(
        feedback=feedback, manifest=manifest, work_item=work_item, current_head=current_head,
    )
    if not result.admissible:
        failing = result.failure_summary()
        return Decision(
            observed_phase=phase, evidence=(f"inadmissible manual-stage feedback: {failing}",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    f"a verdict is on file but it is not ingestible ({failing}) -- correct "
                    "the named field and re-paste the verdict"
                ),
                artifact_path=str(feedback_dir / "REVIEW_FEEDBACK.md"),
                safe_resume_command=f"/record-manual-plan-review {work_item_id}",
            ),
            declined=False, reason=f"{phase}: {failing}",
        )

    status = feedback.get("status")
    if status == "BLOCK":
        return Decision(
            observed_phase=phase, evidence=("admissible manual-stage Status: BLOCK",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required="the external reviewer blocked this round; a human "
                                  "resolves it before anything else runs",
                artifact_path=str(feedback_dir / "REVIEW_FEEDBACK.md"),
                safe_resume_command=f"/record-manual-plan-review {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: admissible Status: BLOCK -- a human resolves before anything "
                   "else runs",
        )

    evidence = (f"admissible manual-stage Status: {status}",) + result.advisories
    reason = f"{phase}: admissible feedback, Status: {status}"
    if result.advisories:
        reason += " (" + "; ".join(result.advisories) + ")"
    return Decision(
        observed_phase=phase, evidence=evidence,
        action=Action(command=f"/record-manual-plan-review {work_item_id}"),
        automatic=True, gate=None, declined=False, reason=reason,
    )


def _decide_awaiting_external_plan_review(root: Path, work_item_id: str, work_item: Any) -> Decision:
    phase = "AWAITING_EXTERNAL_PLAN_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    bundle_dir = resolve_bundle_dir(root, work_item_id, phase=phase)
    feedback = read_feedback_fields(root, feedback_dir)

    if feedback is None:
        return Decision(
            observed_phase=phase, evidence=(), action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "upload the current plan bundle to an external reviewer and paste the "
                    "verdict into REVIEW_FEEDBACK.md"
                ),
                artifact_path=str(bundle_dir), safe_resume_command=f"/apply-plan-review {work_item_id}",
            ),
            declined=False, reason=f"{phase}: no current-round REVIEW_FEEDBACK.md on file",
        )

    status = feedback.get("status")
    if status == "APPROVE":
        # AWAITING_PLAN_APPROVAL has exactly one writer, on a "2.1"-only
        # path, so a "1" item never occupies it -- the plan-approval gate
        # sits here instead once an APPROVE is on file.
        return Decision(
            observed_phase=phase, evidence=("current-round Status: APPROVE",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "the plan was approved; a human runs the user-only "
                    "/approve-review plan"
                ),
                artifact_path=str(bundle_dir), safe_resume_command=f"/approve-review plan {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: an APPROVE is on file -- this phase now hosts the "
                   "plan-approval gate, not a further /apply-plan-review",
        )

    manifest = read_manifest_fields(root, bundle_dir)
    current_head = _current_head(root)
    result = evaluate_apply_plan_review_admissibility(
        feedback=feedback, manifest=manifest, work_item=work_item, current_head=current_head,
    )
    if not result.admissible:
        failing = result.failure_summary()
        return Decision(
            observed_phase=phase, evidence=(f"inadmissible feedback: {failing}",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=f"REVIEW_FEEDBACK.md is on file but not admissible: {failing}",
                artifact_path=str(feedback_dir / "REVIEW_FEEDBACK.md"),
                safe_resume_command=f"/apply-plan-review {work_item_id}",
            ),
            declined=False, reason=f"{phase}: {failing}",
        )

    return Decision(
        observed_phase=phase, evidence=(f"admissible Status: {status}",),
        action=Action(command=f"/apply-plan-review {work_item_id}"),
        automatic=True, gate=None, declined=False,
        reason=f"{phase}: admissible current-round Status: {status}",
    )


# ---------------------------------------------------------------------------
# The implementation-review phases (automatic-lifecycle-orchestration CP4).
# Each handler runs after the REJECTED marker gate and -- at "2.2" -- the
# implementation-bundle gate (:func:`decide`), so it reads a bundle that
# belongs to the work item's current implementation round.
# ---------------------------------------------------------------------------


def _explain_command(work_item_id: str) -> str:
    return f"workflow-controller explain --work-item {work_item_id}"


def _decide_awaiting_external_implementation_review_legacy(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
    """The ``"1"``/``"2.1"`` handler (and any version other than
    ``"2.2"``), byte-identical to the one before automatic-lifecycle-
    orchestration CP4 -- pinned by ``tests/golden/external_implementation_
    review_decisions.json``."""
    phase = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    bundle_dir = resolve_bundle_dir(root, work_item_id, phase=phase)
    feedback = read_feedback_fields(root, feedback_dir)
    manifest = read_manifest_fields(root, bundle_dir)
    current_head = _current_head(root)
    manifest_head = manifest.get("generation_head")
    stale = (
        manifest.get("_exists") and manifest_head is not None and current_head is not None
        and manifest_head != current_head
    )

    if stale:
        # The generator refuses in this state (prepare-ai-review.sh:244);
        # /recover-implementation-provenance is the human's action, and
        # the Controller never selects it -- it can never be automatic
        # (round 9's B1).
        what = _provenance_only_text(manifest_head, current_head)
        safe = f"/recover-implementation-provenance {work_item_id}"
    elif feedback is None:
        what = (
            "hand the current implementation bundle to an external reviewer and paste "
            "the verdict into REVIEW_FEEDBACK.md"
        )
        safe = f"/apply-implementation-review {work_item_id}"
    else:
        status = feedback.get("status")
        if status == "APPROVE":
            what = "an APPROVE is on file; a human runs the user-only /approve-review implementation"
            safe = f"/approve-review implementation {work_item_id}"
        elif status in _VALID_STATUSES:  # REVISE or BLOCK
            what = f"Status: {status} is on file; run /apply-implementation-review"
            safe = f"/apply-implementation-review {work_item_id}"
        else:
            what = f"REVIEW_FEEDBACK.md's Status field does not parse ({status!r})"
            safe = f"/apply-implementation-review {work_item_id}"

    return Decision(
        observed_phase=phase, evidence=(), action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=what, artifact_path=str(bundle_dir), safe_resume_command=safe,
        ),
        declined=False, reason=f"{phase} is a report-only phase (revision 10's scope): {what}",
    )


def _provenance_only_text(manifest_head: str | None, current_head: str | None) -> str:
    """The ``generation_head``-only provenance text of the ``"1"``/``"2.1"``
    ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` handler, unconditional and
    byte-identical (pinned by its golden). The ``"2.2"``
    implementation-bundle gate replaced its own use of it with
    :func:`_provenance_only_gate`'s conditional text (CP4B)."""
    return (
        f"MANIFEST.md's generation_head ({manifest_head}) is behind the target's "
        f"committed HEAD ({current_head}); an ordinary post-fix regeneration refuses "
        "in this state -- a human runs /recover-implementation-provenance"
    )


def _pinned_bundle(work_item: Any, bundle_id: str | None) -> bool:
    """``workflow_state.is_technical_review_block_pinned``, read-only: a
    ``technical_review_block_pins`` entry names ``bundle_id`` exactly."""
    pins = getattr(work_item, "technical_review_block_pins", ()) or ()
    return bundle_id is not None and any(
        isinstance(pin, dict) and pin.get("bundle_id") == bundle_id for pin in pins
    )


def _decide_awaiting_external_implementation_review_two_stage(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
    """The ``"2.2"`` handler. This phase is reached only through a recorded
    manual ``APPROVE`` (``record_manual_implementation_review``), so "no
    feedback -> hand the bundle to an external reviewer" is wrong here; the
    real next action is the user-only ``/approve-review implementation``,
    named only when every clause of ``technical_approval_gate_reachable``
    this Controller can see holds. Read in order:

    1. a ``MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` ``REVISE``/``BLOCK`` on
       file, bound to the current content -> a gate: a later manual verdict
       is on file, and a human decides whether to reopen remediation (never
       automatic);
    2. the ledger records both stages ``APPROVE`` against the manifest's
       ``review_content_id``, ``REVIEW_FEEDBACK.md`` is on file with
       ``Status`` ``REVISE`` or ``APPROVE`` (``approval_gate_reachable``;
       ``/approve-review`` step 1 reads the file), and no
       ``technical_review_block_pins`` entry names the manifest's
       ``bundle_id`` -> the ``/approve-review implementation`` gate;
    3. otherwise -> a gate naming every missing clause, and why
       ``/approve-review implementation`` would refuse.

    Clauses the Controller cannot see (an uncommitted protected-content
    edit) stay ``/approve-review``'s own recomputation to refuse on."""
    phase = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    feedback_path = feedback_dir / "REVIEW_FEEDBACK.md"
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    feedback = read_feedback_fields(root, feedback_dir)
    manifest = read_manifest_fields(root, bundle_dir)
    ledger = read_implementation_review_ledger(work_item)
    manifest_content_id = manifest.get("review_content_id")
    manifest_bundle_id = manifest.get("bundle_id")
    status = feedback.get("status") if feedback is not None else None

    # Matched case-insensitively (unlike the manual-stage arrival test):
    # this clause only ever turns a decision into the "a human decides"
    # gate, so a mis-cased role must not fall through to naming approval.
    if (
        feedback is not None
        and _normalize_role(feedback.get("reviewer_role")) == MANUAL_IMPLEMENTATION_ROLE
        and status in ("REVISE", "BLOCK") and manifest_content_id is not None
        and feedback.get("reviewed_content_id") == manifest_content_id
    ):
        what = (
            f"a later manual verdict is on file (Status: {status}, bound to the current "
            f"review_content_id {manifest_content_id}); /apply-implementation-review is legal from "
            "this phase (it re-enters APPLYING_REVIEW_FEEDBACK); a human decides whether to reopen "
            "remediation"
        )
        return Decision(
            observed_phase=phase,
            evidence=(f"{MANUAL_IMPLEMENTATION_ROLE} Status: {status} on file for the current content",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=what, artifact_path=str(feedback_path),
                safe_resume_command=f"/apply-implementation-review {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: a later {MANUAL_IMPLEMENTATION_ROLE} {status} is on file -- never "
                   "automatic; a human decides whether to reopen remediation",
        )

    missing: list[str] = []
    if ledger.malformed is not None:
        missing.append(f"the implementation_review_stages ledger is malformed ({ledger.malformed})")
    else:
        if ledger.local is None:
            missing.append(f"the ledger records no {LOCAL_IMPLEMENTATION_ROLE} APPROVE")
        if ledger.manual is None:
            missing.append(f"the ledger records no {MANUAL_IMPLEMENTATION_ROLE} APPROVE")
        if ledger.review_content_id is None or ledger.review_content_id != manifest_content_id:
            missing.append(
                f"the ledger's review_content_id {ledger.review_content_id!r} is not the current "
                f"content's (MANIFEST.md states {manifest_content_id!r})"
            )
    if feedback is None:
        missing.append(
            f"no REVIEW_FEEDBACK.md is on file at {feedback_path} (/approve-review step 1 reads it)"
        )
    elif status not in ("REVISE", "APPROVE"):
        missing.append(
            f"REVIEW_FEEDBACK.md's Status is {status!r}, and approval_gate_reachable admits only "
            "REVISE or APPROVE"
        )
    if manifest_bundle_id is None:
        missing.append("MANIFEST.md states no bundle_id to check technical_review_block_pins against")
    elif _pinned_bundle(work_item, manifest_bundle_id):
        missing.append(
            f"technical_review_block_pins pins a BLOCK against the current bundle_id {manifest_bundle_id}"
        )

    if not missing:
        return Decision(
            observed_phase=phase,
            evidence=(
                f"ledger: {LOCAL_IMPLEMENTATION_ROLE} and {MANUAL_IMPLEMENTATION_ROLE} APPROVE for "
                f"review_content_id {manifest_content_id}",
                f"REVIEW_FEEDBACK.md Status: {status}",
            ),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "both implementation-review stages approved the current content; a human runs "
                    "the user-only /approve-review implementation"
                ),
                artifact_path=str(bundle_dir),
                safe_resume_command=f"/approve-review implementation {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: both ledger stages approve the current content, the feedback on file "
                   f"is {status}, and no pin names the bundle -- the user-only technical approval is next",
        )

    summary = "; ".join(missing)
    return Decision(
        observed_phase=phase, evidence=tuple(missing), action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=(
                f"/approve-review implementation would refuse here: {summary} -- a human resolves "
                "that first"
            ),
            artifact_path=str(bundle_dir),
            safe_resume_command=_explain_command(work_item_id),
        ),
        declined=False,
        reason=f"{phase}: the technical-approval gate is not reachable ({summary})",
    )


def _decide_awaiting_external_implementation_review(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
    """``"2.2"`` is ledger-, feedback- and pin-aware
    (:func:`_decide_awaiting_external_implementation_review_two_stage`);
    every other version keeps its pre-CP4 decision, byte for byte
    (:func:`_decide_awaiting_external_implementation_review_legacy`)."""
    if getattr(work_item, "governing_workflow_version", None) == "2.2":
        return _decide_awaiting_external_implementation_review_two_stage(root, work_item_id, work_item)
    return _decide_awaiting_external_implementation_review_legacy(root, work_item_id, work_item)


def _decide_awaiting_local_implementation_review(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
    """A ``LOCAL_MODEL_IMPLEMENTATION_REVIEW`` ``BLOCK`` on file gates:
    ``review-implementation.md`` A7 requires explicit user resolution
    before any further command runs. Like the plan-stage handler's
    any-local-``BLOCK`` rule, the role is compared case-insensitively and
    no binding line is required -- fail closed, since the alternative
    turns a genuine ``BLOCK`` with one malformed field into a launch.
    Otherwise ``/review-implementation <id>`` is selected (automatic by the
    dispatch rule at ``"2.2"``)."""
    phase = "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    feedback = read_feedback_fields(root, feedback_dir)
    if (
        feedback is not None and feedback.get("status") == "BLOCK"
        and _normalize_role(feedback.get("reviewer_role")) == LOCAL_IMPLEMENTATION_ROLE
    ):
        return Decision(
            observed_phase=phase,
            evidence=(f"current-round {LOCAL_IMPLEMENTATION_ROLE} Status: BLOCK",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "the local implementation reviewer blocked this round; explicit user "
                    "resolution is required before any further command runs"
                ),
                artifact_path=str(feedback_dir / "REVIEW_FEEDBACK.md"),
                safe_resume_command=f"/review-implementation {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: review-implementation.md A7 requires explicit user resolution "
                   "before any further command runs after a BLOCK",
        )
    return Decision(
        observed_phase=phase, evidence=(),
        action=Action(command=f"/review-implementation {work_item_id}"),
        automatic=True, gate=None, declined=False,
        reason=f"{phase}: no current-round local BLOCK on file and the implementation bundle is "
               "coherent -- a fresh worker is the independent local review this stage asks for",
    )


def _decide_awaiting_manual_external_implementation_review(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
    """The ``"2.2"`` manual stage, mirroring the plan stage's
    ``/record-manual-plan-review`` ingestion. Every sub-case but the last is
    a gate:

    - no feedback, or feedback not declaring the manual role (matched on the
      literal string -- no legacy-cased alias at this stage,
      ``validate_manual_implementation_review_preconditions`` refuses an
      exact-spelling mismatch) -> the genuine manual gate, naming the bundle
      and the ``bundle_id``/``review_content_id`` the user must hand over;
    - inadmissible (:func:`evaluate_manual_implementation_stage_admissibility`)
      -> a gate naming every failing clause (a ``REVISE`` bound to another
      bundle included: ingesting it would wedge the item at
      ``APPLYING_REVIEW_FEEDBACK``);
    - an admissible ``BLOCK`` -> the user-resolution gate;
    - an admissible ``APPROVE``/``REVISE`` -> ``/record-manual-implementation-
      review <id>`` is selected (automatic by the dispatch rule), its
      advisories carried into ``evidence``.

    The Controller never writes ``REVIEW_FEEDBACK.md``: ingestion launches
    only once a human has placed the verdict there."""
    phase = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    feedback = read_feedback_fields(root, feedback_dir)
    feedback_path = feedback_dir / "REVIEW_FEEDBACK.md"
    manifest = read_manifest_fields(root, bundle_dir)

    role = feedback.get("reviewer_role") if feedback is not None else None
    if feedback is None or role != MANUAL_IMPLEMENTATION_ROLE:
        ledger = read_implementation_review_ledger(work_item)
        return Decision(
            observed_phase=phase, evidence=(), action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    f"upload {bundle_dir} (bundle_id {manifest.get('bundle_id')}, review_content_id "
                    f"{ledger.review_content_id} from the ledger) to a manual external reviewer and "
                    f"paste the verdict into {feedback_path}, declaring Reviewer role: "
                    f"{MANUAL_IMPLEMENTATION_ROLE}"
                ),
                artifact_path=str(bundle_dir),
                safe_resume_command=f"/record-manual-implementation-review {work_item_id}",
            ),
            declined=False,
            reason=(
                f"{phase}: no current-round REVIEW_FEEDBACK.md on file"
                if feedback is None else
                f"{phase}: feedback declares Reviewer role {role!r}, not {MANUAL_IMPLEMENTATION_ROLE}"
            ),
        )

    result = evaluate_manual_implementation_stage_admissibility(
        feedback=feedback, manifest=manifest,
        review_request=read_review_request_fields(root, bundle_dir),
        work_item=work_item, current_head=_current_head(root),
        current_worktree_root=target_worktree_root(root),
    )
    if not result.admissible:
        failing = result.failure_summary()
        return Decision(
            observed_phase=phase, evidence=(f"inadmissible manual-stage feedback: {failing}",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    f"a {MANUAL_IMPLEMENTATION_ROLE} verdict is on file but it is not ingestible "
                    f"({failing}) -- correct the named field and re-paste the verdict; this "
                    "Controller never ingests a verdict /record-manual-implementation-review, or the "
                    "/apply-implementation-review its REVISE leads to, would refuse on"
                ),
                artifact_path=str(feedback_path),
                safe_resume_command=f"/record-manual-implementation-review {work_item_id}",
            ),
            declined=False, reason=f"{phase}: {failing}",
        )

    status = feedback.get("status")
    if status == "BLOCK":
        return Decision(
            observed_phase=phase,
            evidence=("admissible manual-stage Status: BLOCK",) + result.advisories,
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required="the external reviewer blocked this round; a human "
                                  "resolves it before anything else runs",
                artifact_path=str(feedback_path),
                safe_resume_command=f"/record-manual-implementation-review {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: admissible Status: BLOCK -- a human resolves before anything else runs",
        )

    evidence = (f"admissible manual-stage Status: {status}",) + result.advisories
    reason = f"{phase}: admissible {MANUAL_IMPLEMENTATION_ROLE} feedback, Status: {status}"
    if result.advisories:
        reason += " (" + "; ".join(result.advisories) + ")"
    return Decision(
        observed_phase=phase, evidence=evidence,
        action=Action(command=f"/record-manual-implementation-review {work_item_id}"),
        automatic=True, gate=None, declined=False, reason=reason,
    )


def _decide_awaiting_functional_review(root: Path, work_item_id: str, work_item: Any) -> Decision:
    phase = "AWAITING_FUNCTIONAL_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    current_head = _current_head(root)
    checklist = functional_checklist_evidence(
        root, work_item_id, getattr(work_item, "base_commit", None), current_head,
        getattr(work_item, "implementation_revision", None),
    )

    if checklist is None:
        return Decision(
            observed_phase=phase, evidence=(), action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "no current-round functional-review checklist evidence commit is on "
                    "file; run /prepare-functional-review to write and commit the checklist"
                ),
                artifact_path=str(feedback_dir), safe_resume_command=f"/prepare-functional-review {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: no current-round Workflow-Functional-Checklist evidence found",
        )

    findings_path = functional_review_findings_path(root, work_item_id)
    if not (root / findings_path).is_file():
        return Decision(
            observed_phase=phase, evidence=(f"checklist evidence commit {checklist['commit_sha']}",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "the checklist is current; a human must perform manual functional "
                    f"testing and place findings at {findings_path}"
                ),
                artifact_path=str(findings_path), safe_resume_command=f"/apply-functional-review {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: checklist is current and no FUNCTIONAL_REVIEW.md exists yet",
        )

    consumed = functional_review_findings_consumed(root, work_item_id)
    if consumed is False:
        return Decision(
            observed_phase=phase, evidence=("unconsumed FUNCTIONAL_REVIEW.md findings",),
            action=Action(command=f"/apply-functional-review {work_item_id}"),
            automatic=True, gate=None, declined=False,
            reason=f"{phase}: findings are present and unconsumed",
        )

    incomplete_children = tuple(getattr(work_item, "incomplete_children", ()) or ())
    if incomplete_children:
        return Decision(
            observed_phase=phase,
            evidence=(f"incomplete children: {', '.join(incomplete_children)}",),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "findings are consumed, but a remediation child work item is still "
                    f"non-terminal: {', '.join(incomplete_children)} -- resolve it first"
                ),
                artifact_path=str(findings_path),
                safe_resume_command=f"workflow-controller explain --work-item {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: findings consumed, but incomplete_children is non-empty",
        )

    return Decision(
        observed_phase=phase,
        evidence=("findings consumed, registry terminal, no incomplete children",),
        action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=(
                "findings are consumed and every checkpoint is complete; a human runs "
                "the user-only /accept-milestone"
            ),
            artifact_path=str(findings_path), safe_resume_command=f"/accept-milestone {work_item_id}",
        ),
        declined=False,
        reason=f"{phase}: findings consumed, registry terminal, no incomplete children -- "
               "ready for /accept-milestone",
    )


# ---------------------------------------------------------------------------
# decide()
# ---------------------------------------------------------------------------

_EVIDENCE_HANDLERS = {
    "AWAITING_LOCAL_PLAN_REVIEW": _decide_awaiting_local_plan_review,
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": _decide_awaiting_manual_external_plan_review,
    "AWAITING_EXTERNAL_PLAN_REVIEW": _decide_awaiting_external_plan_review,
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW": _decide_awaiting_external_implementation_review,
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": _decide_awaiting_manual_external_implementation_review,
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW": _decide_awaiting_local_implementation_review,
    "AWAITING_FUNCTIONAL_REVIEW": _decide_awaiting_functional_review,
}


def _regeneration_command(phase: str, work_item_id: str) -> str:
    if phase in PLAN_STAGE_PHASES:
        return f"scripts/prepare-ai-review.sh <base-sha> plan {work_item_id}"
    return f"scripts/prepare-ai-review.sh <base-sha> post-fix {work_item_id}"


# ---------------------------------------------------------------------------
# The stale-plan-bundle gate (worker-execution hardening, CP4).
# ---------------------------------------------------------------------------

#: The two phases whose automatic actions (``/review-plan``,
#: ``/record-manual-plan-review``) consume the current plan bundle, and so
#: must never run against one that belongs to an earlier plan revision.
#: ``AWAITING_EXTERNAL_PLAN_REVIEW`` (``"1"``) is deliberately absent: its
#: stale-bundle recovery is re-running ``/apply-plan-review``, which its
#: own handler already selects.
PLAN_BUNDLE_CONSUMING_PHASES: frozenset[str] = frozenset({
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
})

#: The plan-stage author-written bundle files. The generator only ever
#: creates them as empty stubs (``prepare-ai-review.sh``), so a missing one
#: -- or a zero-byte one, which is exactly such a stub -- must be
#: *written*, not refreshed.
_PLAN_AUTHOR_FILES: tuple[str, ...] = ("REVIEW_REQUEST.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt")


def _author_file_has_content(path: Path) -> bool:
    """Whether ``path`` is a regular, non-empty file. A zero-byte file is
    the generator's own empty stub (``prepare-ai-review.sh`` creates each
    missing author file as ``: > "$path"`` before its closing checks can
    refuse or withdraw), so it carries no previous-round content to
    refresh and counts as absent. A stat that cannot complete counts as
    absent too -- "write" is the safe variant."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _newest_quarantine_dir(root: Path, work_item_id: str) -> Path | None:
    """The newest ``.ai-review/<id>/current.rejected-*/`` directory a
    completed withdrawal left behind, root-relative, or ``None``."""
    parent = _scoped_root(work_item_id)
    try:
        candidates = [
            (path.stat().st_mtime_ns, path.name, path)
            for path in (root / parent).glob("current.rejected-*") if path.is_dir()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return parent / max(candidates)[2].name


def _plan_bundle_recovery_steps(root: Path, work_item: Any) -> tuple[str, ...]:
    """The ordered human steps that actually regenerate a coherent plan
    bundle after ``publish_plan_revision`` ran but bundle generation did
    not complete. The bare generator is not enough on its own: its closing
    checks refuse or withdraw a bundle whose ``REVIEW_REQUEST.md``/
    ``TEST_RESULTS.md`` still describe the previous round, and no Workflow
    command performs these refreshes once the item is past
    ``REVISING_PLAN``. Controller performs none of the steps itself.

    When ``<bundle_dir>`` or any author file is absent or zero bytes (a
    completed withdrawal, a first-round generator failure, or the empty
    stubs a later generator run left behind before refusing or
    withdrawing -- :func:`_author_file_has_content`) the steps say "write"
    rather than "refresh", name the protocol's request format, and a step
    0 restoring ``CONTEXT_FILES.txt`` is prepended -- naming the newest
    quarantine directory, when one exists, as the previous round's source."""
    work_item_id = work_item.work_item_id
    bundle_dir = resolve_bundle_dir(root, work_item_id, phase="AWAITING_LOCAL_PLAN_REVIEW")
    base_commit = work_item.base_commit or "<base-sha>"
    revision = work_item.plan_revision if work_item.plan_revision is not None else "<plan_revision>"
    absent = not (root / bundle_dir).is_dir() or any(
        not _author_file_has_content(root / bundle_dir / name) for name in _PLAN_AUTHOR_FILES
    )
    verb = "write" if absent else "refresh"
    shape = " (in REVIEW_PROTOCOL.md's \"Review request format\" shape)" if absent else ""

    steps: list[str] = []
    if absent:
        quarantine = _newest_quarantine_dir(root, work_item_id)
        source = (
            f", restoring the previous round's author files from {quarantine}/"
            if quarantine is not None else ""
        )
        steps.append(f"write {bundle_dir / 'CONTEXT_FILES.txt'}{source}")
    steps.append(
        f"{verb} {bundle_dir / 'REVIEW_REQUEST.md'}{shape} so that it states "
        f"`review_content_id: <hex>` with the value from "
        f"workflow_fingerprint.compute_review_content_id_plan_stage_for_work_item(repo_root, "
        f"\"{work_item_id}\")[0] (REVIEW_PROTOCOL.md's \"Computing `review_content_id`\" "
        "entry point), never the previous round's value"
    )
    steps.append(
        f"{verb} {bundle_dir / 'TEST_RESULTS.md'}{shape} so that its labelled lines read "
        f"`stage: plan (revision {revision})` and `head: <output of git rev-parse HEAD>`"
    )
    steps.append(
        f"run scripts/prepare-ai-review.sh {base_commit} plan {work_item_id} -- if this "
        "refuses at preflight (base_commit, plan_revision mirror vs. registry, or plan-stage "
        "metadata such as the plan document's (Revision N) marker), its message names the "
        "upstream plan/registry artifact to repair first"
    )
    first = 0 if absent else 1
    return tuple(f"{number}. {step}" for number, step in enumerate(steps, start=first))


def _stale_plan_bundle_gate(root: Path, work_item: Any, detail: str) -> Decision:
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    bundle_dir = resolve_bundle_dir(root, work_item_id, phase=phase)
    steps = _plan_bundle_recovery_steps(root, work_item)
    return Decision(
        observed_phase=phase,
        evidence=(f"plan bundle is not coherent with the work item's state: {detail}",),
        action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=(
                f"the current plan bundle does not belong to this work item's current plan "
                f"revision ({detail}); before any plan review runs, perform in order: "
                + " ".join(steps)
            ),
            artifact_path=str(bundle_dir / "MANIFEST.md"),
            safe_resume_command="; ".join(steps),
        ),
        declined=False,
        reason=f"{phase}: the current plan bundle is stale or withdrawn ({detail}), checked "
               "ahead of the per-phase handlers",
    )


# ---------------------------------------------------------------------------
# The implementation-bundle gates (automatic-lifecycle-orchestration CP4).
# ---------------------------------------------------------------------------

#: The four phases whose ``"2.2"`` actions consume the current
#: implementation bundle, and so gate on :func:`implementation_bundle_coherence`
#: ahead of their per-phase handler. The first two exist only at ``"2.2"``;
#: at ``"1"``/``"2.1"`` nothing at ``APPLYING_REVIEW_FEEDBACK`` is automatic
#: and ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` stays byte-identical, so no
#: bundle gate stands in front of either there
#: (:data:`_IMPLEMENTATION_BUNDLE_GATED_VERSIONS`).
IMPLEMENTATION_BUNDLE_CONSUMING_PHASES: frozenset[str] = frozenset({
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
})

#: The governing versions at which the implementation-bundle gate runs.
_IMPLEMENTATION_BUNDLE_GATED_VERSIONS: frozenset[str] = frozenset({"2.2"})

#: The one phase at which ``HEAD`` legitimately runs ahead of the manifest's
#: ``generation_head`` (the pending review-stage state commit and any fix
#: commits land before the next generation record), so the coherence check
#: there never requires a current ``generation_head``.
_GENERATION_HEAD_EXEMPT_PHASE = "APPLYING_REVIEW_FEEDBACK"

#: The implementation-stage author-written bundle files
#: (``/milestone-implement`` step 4; ``prepare-ai-review.sh``'s
#: ``STUB_FILES``). The generator only ever creates them as empty stubs, so
#: a missing or zero-byte one must be *written*, not refreshed.
_IMPLEMENTATION_AUTHOR_FILES: tuple[str, ...] = (
    "IMPLEMENTATION_SUMMARY.md", "REVIEW_REQUEST.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt",
)

_SELF_REVIEWING_IMPLEMENTATION = "SELF_REVIEWING_IMPLEMENTATION"


def _latest_generation_record_parent_phase(root: Path, work_item: Any) -> str | None:
    """The committed phase at the parent of this work item's most recent
    generation-record commit, located from the manifest's own
    ``generation_head`` (:func:`generation_record_view`): the newest record
    in ``generation_head..HEAD`` when there is one, else ``generation_head``
    itself when it is one of this work item's records.

    When there is no anchor at all -- no readable manifest, or one without
    a ``generation_head`` line: a first round whose generator failed right
    after its record, or a withdrawn ``current/`` -- and ``HEAD`` itself
    carries this work item's generation-record trailer, ``HEAD`` *is* the
    most recent record, and its parent's committed phase decides (CP4B).

    ``None`` when no record can be located that way -- a ``generation_head``
    that is not an ancestor of ``HEAD`` (never overridden by ``HEAD``: the
    plan names both forms there), or no record at all -- or when that
    parent's committed phase is unreadable."""
    work_item_id = work_item.work_item_id
    manifest = read_manifest_fields(root, implementation_bundle_dir(root, work_item_id))
    generation_head = manifest.get("generation_head")
    view = generation_record_view(root, work_item_id, generation_head)
    if view.newer_records:
        return view.latest_record_parent_phase
    if view.newer_records is not None and _names_work_item_generation_record(
        commit_trailers(root, generation_head), work_item_id,
    ):
        return _committed_phase(root, work_item_id, f"{generation_head}^")
    if generation_head is None and view.head is not None and _names_work_item_generation_record(
        commit_trailers(root, view.head), work_item_id,
    ):
        return view.parent_phase
    return None


def implementation_generator_stages(root: Path, work_item: Any) -> tuple[str, ...]:
    """The ``prepare-ai-review.sh`` ``<stage>`` a regeneration must use,
    derived from Workflow's own durable fact -- the committed phase of the
    parent of this work item's most recent generation-record commit
    (:func:`_latest_generation_record_parent_phase`), never Controller job
    records: ``SELF_REVIEWING_IMPLEMENTATION`` means ``("implementation",)``
    (``/milestone-implement`` step 4's record, whose parent is step 1f's
    checkpoint commit or step 2's state-only commit), any other phase means
    ``("post-fix",)`` (``/apply-implementation-review`` step 7's). When no
    record can be located, both forms, ``("implementation", "post-fix")``."""
    parent_phase = _latest_generation_record_parent_phase(root, work_item)
    if parent_phase is None:
        return ("implementation", "post-fix")
    if parent_phase == _SELF_REVIEWING_IMPLEMENTATION:
        return ("implementation",)
    return ("post-fix",)


def _implementation_bundle_recovery_steps(root: Path, work_item: Any) -> tuple[str, ...]:
    """The ordered human steps that regenerate a coherent implementation
    bundle -- the implementation-stage counterpart of
    :func:`_plan_bundle_recovery_steps`. The bare generator is never enough
    on its own: ``IMPLEMENTATION_SUMMARY.md``'s ``implementation_revision:``
    line and ``REVIEW_REQUEST.md``'s ``review_content_id:`` line are hard
    generator preconditions (``assert_stage_completeness``,
    ``assert_review_request_states_review_content_id``), and after a
    withdrawal the author files may be stale or gone. Controller performs
    none of the steps itself.

    When ``<bundle_dir>`` or any author file is absent or zero bytes, the
    steps say "write" rather than "refresh", and a step 0 writing
    ``CONTEXT_FILES.txt`` is prepended, naming the newest
    ``current.rejected-*`` quarantine, when one exists, as the source to
    restore from -- at ``APPLYING_REVIEW_FEEDBACK``, the bundle the feedback
    on file reviewed. The generator's ``<stage>`` comes from
    :func:`implementation_generator_stages`; when it cannot be derived, both
    forms are named, each with the command whose record it follows."""
    work_item_id = work_item.work_item_id
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    base_commit = work_item.base_commit or "<base-sha>"
    revision = (
        work_item.implementation_revision if work_item.implementation_revision is not None
        else "<implementation_revision>"
    )
    work_item_type = getattr(work_item, "work_item_type", None) or "<work_item_type>"
    absent = not (root / bundle_dir).is_dir() or any(
        not _author_file_has_content(root / bundle_dir / name) for name in _IMPLEMENTATION_AUTHOR_FILES
    )
    verb = "write" if absent else "refresh"

    steps: list[str] = []
    if absent:
        quarantine = _newest_quarantine_dir(root, work_item_id)
        if quarantine is None:
            source = ""
        elif work_item.phase == _GENERATION_HEAD_EXEMPT_PHASE:
            source = (
                f", restoring the author files of the bundle REVIEW_FEEDBACK.md reviewed from "
                f"{quarantine}/"
            )
        else:
            source = f", restoring the previous round's author files from {quarantine}/"
        steps.append(f"write {bundle_dir / 'CONTEXT_FILES.txt'}{source}")
    steps.append(
        f"{verb} {bundle_dir / 'IMPLEMENTATION_SUMMARY.md'} so that it states "
        f"`implementation_revision: {revision}` as a plain labelled line (assert_stage_completeness)"
    )
    shape = " (in REVIEW_PROTOCOL.md's \"Review request format\" shape)" if absent else ""
    steps.append(
        f"{verb} {bundle_dir / 'REVIEW_REQUEST.md'}{shape} so that it states "
        "`review_content_id: <hex>` with the value from "
        f"workflow_state.approval_review_content_id(repo_root, stage=\"implementation\", "
        f"base_commit=\"{base_commit}\", head=\"HEAD\", work_item_type=\"{work_item_type}\", "
        f"work_item_id=\"{work_item_id}\", "
        f"artifacts_path=workflow_fingerprint.artifacts_path_for_work_item(\"{work_item_id}\")) "
        "(REVIEW_PROTOCOL.md's \"Computing `review_content_id`\" entry point), never the previous "
        "round's value"
    )
    steps.append(
        f"{verb} {bundle_dir / 'TEST_RESULTS.md'} with the exact commands and results of this round"
    )
    preflight = "if this refuses at preflight, its message names the upstream artifact to repair first"
    stages = implementation_generator_stages(root, work_item)
    if len(stages) == 1:
        steps.append(
            f"run scripts/prepare-ai-review.sh {base_commit} {stages[0]} {work_item_id} -- {preflight}"
        )
    else:
        steps.append(
            f"run scripts/prepare-ai-review.sh {base_commit} implementation {work_item_id} if this "
            "work item's most recent generation-record commit is /milestone-implement's (step 4, "
            f"its parent records {_SELF_REVIEWING_IMPLEMENTATION}), or scripts/prepare-ai-review.sh "
            f"{base_commit} post-fix {work_item_id} if it is /apply-implementation-review's (step 7) "
            "-- no generation-record commit could be located from MANIFEST.md's generation_head, so "
            f"this Controller cannot derive which; {preflight}"
        )
    first = 0 if absent else 1
    return tuple(f"{number}. {step}" for number, step in enumerate(steps, start=first))


#: The phases ``/recover-implementation-provenance`` is legal from at
#: ``"2.2"`` -- a literal copy of ``workflow_state.
#: bundle_generation_recovered_role_legal_committed_phases("2.2")`` (this
#: module never imports ``scripts/``), held equal to it by a test. The
#: implementation-bundle gate names that command only at one of these; they
#: are exactly the phases at which it evaluates the ``generation_head``
#: clause (never ``APPLYING_REVIEW_FEEDBACK``).
PROVENANCE_RECOVERY_LEGAL_PHASES: frozenset[str] = frozenset({
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
})


def _incoherence_evidence(clause: str, detail: str) -> str:
    return f"implementation bundle is not coherent with the work item's state ({clause}): {detail}"


def commits_since(root: Path, generation_head: str | None) -> tuple[tuple[str, str, tuple[str, ...]], ...] | None:
    """Every commit in ``generation_head..HEAD``, oldest first, as
    ``(sha, subject, changed paths)`` (``git log --reverse --name-only``) --
    the evidence the provenance-only gate lists. ``None`` when
    ``generation_head`` is absent, is not a full object name (a value read
    from a file never becomes a ``git`` option), or the log cannot be
    read."""
    if not generation_head or not _FULL_OBJECT_NAME_RE.fullmatch(generation_head):
        return None
    result = _run_git(root, [
        "log", "--reverse", "--name-only", "--format=%x1e%H%x1f%s", f"{generation_head}..HEAD",
    ])
    if result.returncode != 0:
        return None
    commits: list[tuple[str, str, tuple[str, ...]]] = []
    for chunk in result.stdout.split("\x1e"):
        if not chunk.strip():
            continue
        header, _, rest = chunk.partition("\n")
        sha, _, subject = header.partition("\x1f")
        commits.append((sha, subject, tuple(line for line in rest.splitlines() if line.strip())))
    return tuple(commits)


def _malformed_generation_record_gate(
    root: Path, work_item: Any, view: GenerationRecordView, clause: str, detail: str,
) -> Decision:
    """Case 1 of the implementation-bundle gate ("The pending review-stage
    state write", item 2): ``HEAD`` is this work item's ordinary-role
    generation-record commit ``T`` whose committed phase equals ``HEAD^``'s,
    so ``phase`` is not in ``T``'s own field diff and
    ``validate_bundle_generation_record_commit`` rejects it
    (``OPUS-R101-001``) -- the shape a worker leaves when it folds the
    pending review-stage state write into ``T``. Every regeneration would
    refuse at the generator's preflight, so the steps are never offered;
    no Workflow command repairs ``T``."""
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    what = (
        f"HEAD ({view.head}) is this work item's ordinary-role bundle-generation-record commit, but "
        f"the work item's committed phase at HEAD^ ({view.parent_phase}) equals its committed phase "
        f"at HEAD ({view.head_phase}): phase is not in the record's own field diff, so "
        "validate_bundle_generation_record_commit rejects it (OPUS-R101-001) and the generator's "
        "preflight refuses every regeneration against it. No Workflow command repairs this: a human "
        "repairs the unpushed history so that the pending review-stage state write lands alone, in "
        "its own commit, before the generation-record commit, and then reruns the generation-record "
        "step (/apply-implementation-review step 7)"
    )
    return Decision(
        observed_phase=phase,
        evidence=(
            _incoherence_evidence(clause, detail),
            f"HEAD {view.head} carries this work item's ordinary-role Workflow-Bundle-Generation-Record "
            f"trailer set, and its committed phase ({view.head_phase}) equals HEAD^'s "
            f"({view.parent_phase})",
        ),
        action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=what, artifact_path=str(bundle_dir / "MANIFEST.md"),
            safe_resume_command=_explain_command(work_item_id),
        ),
        declined=False,
        reason=f"{phase}: HEAD {view.head} is a malformed bundle-generation-record commit "
               "(OPUS-R101-001), checked ahead of every regeneration step",
    )


def _provenance_only_gate(
    root: Path, work_item: Any, generation_head: str, current_head: str, detail: str,
) -> Decision:
    """Case 2 of the implementation-bundle gate (round 2's O3): the only
    failing clause is ``generation_head``, and no generation-record commit
    for this work item lies in ``generation_head..HEAD``, so ``HEAD`` moved
    only through non-record commits after ``T``. ``/recover-implementation-
    provenance`` is that case's command only when those commits are
    excluded-only -- it refuses (``ImplementationProvenanceRecoveryNot
    ApplicableError``) when one changes protected content. This Controller
    does not classify paths, so the gate lists the commits as evidence and
    conditions both its text and its ``safe_resume_command`` on them. The
    command stays user-only and never automatic."""
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    commits = commits_since(root, generation_head)
    if commits:
        listing = "; ".join(
            f"{sha} ({subject}): {', '.join(paths) if paths else 'no changed paths'}"
            for sha, subject, paths in commits
        )
        commit_evidence = tuple(
            f"commit in {generation_head}..HEAD: {sha} {subject!r} changes "
            f"{', '.join(paths) if paths else 'no paths'}"
            for sha, subject, paths in commits
        )
    else:
        listing = "(the commits could not be listed)"
        commit_evidence = (f"the commits in {generation_head}..HEAD could not be listed",)
    condition = (
        "if every listed commit changes only paths the implementation stage excludes (the "
        "implementation_stage excluded paths/prefixes of "
        f"workflow_fingerprint.artifacts_path_for_work_item(\"{work_item_id}\")), a human runs "
        "/recover-implementation-provenance. Otherwise the listed content is not what was reviewed, "
        "and that command refuses: revert those commits, or carry the change through a review "
        "round, before any implementation review runs"
    )
    what = (
        f"MANIFEST.md's generation_head ({generation_head}) is behind the target's committed HEAD "
        f"({current_head}), and no generation-record commit for this work item lies in "
        f"{generation_head}..HEAD, so HEAD moved only through these commits, oldest first: {listing}; "
        + condition
    )
    safe = (
        f"/recover-implementation-provenance {work_item_id} -- only if every commit in "
        f"{generation_head}..HEAD changes only paths the implementation stage excludes; otherwise "
        "revert those commits, or carry the change through a review round, before any "
        "implementation review runs"
    )
    return Decision(
        observed_phase=phase,
        evidence=(_incoherence_evidence("generation_head", detail),) + commit_evidence,
        action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=what, artifact_path=str(bundle_dir / "MANIFEST.md"),
            safe_resume_command=safe,
        ),
        declined=False,
        reason=f"{phase}: only generation_head is stale and HEAD moved through non-record commits -- "
               "/recover-implementation-provenance applies only if they are excluded-only",
    )


def _implementation_bundle_regeneration_gate(
    root: Path, work_item: Any, view: GenerationRecordView, generation_head: str | None, clause: str,
    detail: str,
) -> Decision:
    """Case 3 of the implementation-bundle gate: every other incoherence,
    answered with the ordered recovery steps
    (:func:`_implementation_bundle_recovery_steps`, mirroring the
    plan-stage stale-bundle gate). This includes a ``generation_head``-only
    failure whose ``generation_head..HEAD`` holds a newer generation-record
    commit -- a ``same_content`` post-fix whose generator failed, where
    ``/recover-implementation-provenance`` would refuse ("nothing to
    recover"). ``safe_resume_command`` is the steps, never the bare
    ``_regeneration_command``."""
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    steps = _implementation_bundle_recovery_steps(root, work_item)
    evidence_lines: tuple[str, ...] = (_incoherence_evidence(clause, detail),)
    if view.newer_records:
        evidence_lines += (
            f"generation-record commit(s) for this work item newer than MANIFEST.md's generation_head "
            f"({generation_head}), newest first: {', '.join(view.newer_records)} -- that round's "
            "bundle generation did not complete",
        )
    return Decision(
        observed_phase=phase,
        evidence=evidence_lines,
        action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=(
                "the current implementation bundle does not belong to this work item's current "
                f"implementation round ({clause}: {detail}); before any implementation review runs, "
                "perform in order: " + " ".join(steps)
            ),
            artifact_path=str(bundle_dir / "MANIFEST.md"),
            safe_resume_command="; ".join(steps),
        ),
        declined=False,
        reason=f"{phase}: the implementation bundle is incoherent ({clause}: {detail}), checked "
               "ahead of the per-phase handlers",
    )


def _implementation_bundle_gate(
    root: Path, work_item: Any, clause: str, detail: str, current_head: str | None,
) -> Decision:
    """The ``"2.2"`` implementation-bundle gate, final form (CP4B). Its
    inputs are :func:`implementation_bundle_coherence`'s ``clause`` and
    :func:`generation_record_view` -- read-only ``git``/file facts, never
    Controller job records. Evaluated in order:

    1. **Malformed T** (``head_role == "ordinary"`` and the committed phase
       at ``HEAD`` equals ``HEAD^``'s): :func:`_malformed_generation_record_gate`
       -- first, because every other recovery ends in a generator run the
       generator's preflight would refuse;
    2. **Provenance only** (the only failing clause is ``generation_head``,
       no generation-record commit for this work item lies in
       ``generation_head..HEAD``, and the phase is one
       ``/recover-implementation-provenance`` is legal from):
       :func:`_provenance_only_gate`;
    3. **everything else**: :func:`_implementation_bundle_regeneration_gate`.

    The bare ``_regeneration_command`` is never advertised."""
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    generation_head = read_manifest_fields(root, implementation_bundle_dir(root, work_item_id)).get(
        "generation_head",
    )
    view = generation_record_view(root, work_item_id, generation_head)
    if view.head_role == "ordinary" and view.head_phase is not None and view.head_phase == view.parent_phase:
        return _malformed_generation_record_gate(root, work_item, view, clause, detail)
    if (
        clause == "generation_head" and view.newer_records == () and generation_head is not None
        and current_head is not None and phase in PROVENANCE_RECOVERY_LEGAL_PHASES
    ):
        return _provenance_only_gate(root, work_item, generation_head, current_head, detail)
    return _implementation_bundle_regeneration_gate(root, work_item, view, generation_head, clause, detail)


def _marker_clearing_clause(marker_path: Path, detail: str | None) -> str:
    return (
        f"resolve the failure the REJECTED marker at {marker_path} records ({detail}); "
        "do not delete surviving author files unless that specific failure requires it "
        "-- a successful generation then clears the marker itself"
    )


def _rejected_marker_gate(root: Path, work_item: Any, detail: str | None) -> Decision:
    """The withdrawn-bundle gate, by version and phase (CP4, four branches
    in order):

    1. ``"2.2"``, at a phase of :data:`IMPLEMENTATION_BUNDLE_CONSUMING_PHASES`:
       the marker-clearing clause, then the ordered implementation-bundle
       recovery steps (:func:`_implementation_bundle_recovery_steps`);
    2. a phase of :data:`PLAN_BUNDLE_CONSUMING_PHASES`: the clause, then the
       plan-bundle recovery steps, unchanged;
    3. ``"1"``/``"2.1"`` ``APPLYING_REVIEW_FEEDBACK`` (newly bundle-bearing,
       so no earlier text binds): the clause, then the facts -- the reviewed
       bundle was withdrawn, and ``/apply-implementation-review``'s step 1
       refuses until a successful generation clears the marker. No
       generator command is named: nothing is automatic there, a
       regenerated bundle cannot in general reproduce the reviewed
       ``bundle_id``, and a human decides;
    4. every other bundle-bearing phase: today's bare text, byte-identical.

    A marker survives only an in-progress or partially failed withdrawal,
    after which the author files may be stale or gone -- so, outside branch
    4, the bare generator is never the advertised recovery. The marker's
    "surviving" path can be ``current/`` itself (a failed quarantine
    rename), whose author files the steps then refresh, so the text never
    tells the operator to delete surviving paths."""
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    version = work_item.governing_workflow_version
    marker_path = resolve_rejected_marker_path(root, work_item_id)
    clear = _marker_clearing_clause(marker_path, detail)
    if version in _IMPLEMENTATION_BUNDLE_GATED_VERSIONS and phase in IMPLEMENTATION_BUNDLE_CONSUMING_PHASES:
        steps = _implementation_bundle_recovery_steps(root, work_item)
        what_is_required = (
            f"the current bundle was withdrawn ({detail}); {clear}; then, before any "
            "implementation review runs, perform in order: " + " ".join(steps)
        )
        safe_resume_command = "; ".join((clear,) + steps)
    elif phase in PLAN_BUNDLE_CONSUMING_PHASES:
        steps = _plan_bundle_recovery_steps(root, work_item)
        what_is_required = (
            f"the current bundle was withdrawn ({detail}); {clear}; then, before any plan "
            "review runs, perform in order: " + " ".join(steps)
        )
        safe_resume_command = "; ".join((clear,) + steps)
    elif phase == "APPLYING_REVIEW_FEEDBACK":
        quarantine = _newest_quarantine_dir(root, work_item_id)
        quarantine_text = (
            f"the newest current.rejected-* quarantine ({quarantine}/)" if quarantine is not None
            else "the newest current.rejected-* quarantine"
        )
        what_is_required = (
            f"the bundle REVIEW_FEEDBACK.md reviewed was withdrawn ({detail}); {clear}. "
            "/apply-implementation-review is legal from this phase (it skips its own entry "
            "transition), but its step 1 (assert_bundle_not_rejected) refuses until a successful "
            f"generation clears the marker: a human restores that bundle from {quarantine_text} and "
            "regenerates it, then reruns /apply-implementation-review only if the regenerated "
            "bundle_id equals the feedback's Reviewed bundle ID (step 1's "
            "assert_feedback_matches_bundle) -- otherwise the verdict has to be obtained again, "
            "for the regenerated bundle"
        )
        safe_resume_command = _explain_command(work_item_id)
    else:
        regen = _regeneration_command(phase, work_item_id)
        what_is_required = (
            f"the current bundle was withdrawn ({detail}); regenerate it with "
            f"{regen} before any further command runs"
        )
        safe_resume_command = regen
    return Decision(
        observed_phase=phase,
        evidence=(f"REJECTED marker present at {marker_path}: {detail}",),
        action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=what_is_required,
            artifact_path=str(marker_path), safe_resume_command=safe_resume_command,
        ),
        declined=False,
        reason=f"{phase}: a REJECTED marker is present at {marker_path}, checked "
               "ahead of every other row",
    )


# ---------------------------------------------------------------------------
# ``"2.2"`` ``APPLYING_REVIEW_FEEDBACK``: the automatic path in front of the
# corrected gate (automatic-lifecycle-orchestration CP4B).
# ---------------------------------------------------------------------------

_APPLYING_REVIEW_FEEDBACK = "APPLYING_REVIEW_FEEDBACK"
_APPLY_IMPLEMENTATION_REVIEW = "/apply-implementation-review"

#: The governing versions whose ``APPLYING_REVIEW_FEEDBACK`` gets the
#: automatic path. ``"1"``/``"2.1"`` keep the corrected gate
#: (``controller.decision``'s static one): their feedback carries no
#: reviewer-role or content binding ("Scope judgments").
_APPLY_AUTOMATION_VERSIONS: frozenset[str] = frozenset({"2.2"})

#: The one launched-job status that is not an unverified attempt.
_VERIFIED_JOB_STATUS = "FINISHED"

#: The task addendum for row 18 only ("The pending review-stage state
#: write", item 1), pinned byte-for-byte by a test. A worker following the
#: frozen ``/apply-implementation-review`` text literally folds the ``"2.2"``
#: review-stage writer's uncommitted state write into step 7's
#: generation-record commit ``T``, whose own diff then carries no phase
#: change -- a malformed ``T`` (``OPUS-R101-001``). The addendum authorizes
#: exactly the one state-only commit every precedent's operator made before
#: ``T``, and nothing else. It names no user-only command
#: (``worker._assert_not_user_only`` checks the whole task). Formatted with
#: ``work_item_id`` and ``committed_phase`` (the work item's phase at
#: ``HEAD``) by :func:`pending_review_stage_write_addendum`.
PENDING_REVIEW_STAGE_WRITE_ADDENDUM = (
    "Controller note (pending review-stage state write): `docs/ai-workflow/WORKFLOW_STATE.json` "
    "carries this work item's uncommitted review-stage write (working-tree phase "
    "`APPLYING_REVIEW_FEEDBACK`; `HEAD` records `{committed_phase}`). Before any other commit this "
    "command makes, commit that pending change alone: stage exactly "
    "`docs/ai-workflow/WORKFLOW_STATE.json`, unmodified from what the review-stage writer "
    "produced, with a message whose final paragraph is the single trailer "
    "`Workflow-Work-Item: {work_item_id}` and no other Workflow trailer. This is the same shape "
    "`/milestone-implement` step 2 uses for its state-only transition commit. Without it, step 7's "
    "generation-record commit has no phase change in its own diff, and "
    "`validate_bundle_generation_record_commit` rejects it (`OPUS-R101-001`)."
)


def pending_review_stage_write_addendum(work_item_id: str, committed_phase: str) -> str:
    """:data:`PENDING_REVIEW_STAGE_WRITE_ADDENDUM`, formatted."""
    return PENDING_REVIEW_STAGE_WRITE_ADDENDUM.format(
        work_item_id=work_item_id, committed_phase=committed_phase,
    )


@dataclasses.dataclass(frozen=True)
class LaunchedJobView:
    """The one Controller job record the apply relaunch bound reads, as
    ``controller.job.last_launched_apply_job_view`` builds it (this module
    cannot import ``job``): the most recent *terminal* record for the work
    item that reached ``LAUNCHED`` with the ``/apply-implementation-review``
    token from ``APPLYING_REVIEW_FEEDBACK``, skipping a record whose worker
    never started (``reconciliation_evidence.code == "WorkerNotStarted"``).

    ``command`` is the slash-prefixed command token; ``from_phase`` the
    phase the launch was keyed on; ``pre_bundle_manifest_bundle_id`` the
    record's ``pre_state.bundle_manifest_bundle_id`` -- the bundle the
    attempt started from, ``None`` when the record carries none."""

    job_id: str
    command: str
    from_phase: str
    status: str
    pre_bundle_manifest_bundle_id: str | None


def relaunch_bound_applies(job: LaunchedJobView | None, manifest_bundle_id: str | None) -> bool:
    """The apply relaunch bound (round 2's O1): ``job`` -- J, the last
    launched ``/apply-implementation-review`` from this phase -- ended in a
    status other than ``FINISHED``, against the bundle the current manifest
    still names. Keyed on the bundle, never on job order: it lapses exactly
    when a new generation replaces the manifest. A null
    ``pre_bundle_manifest_bundle_id`` counts as equal (fail closed)."""
    if job is None or job.command != _APPLY_IMPLEMENTATION_REVIEW or job.from_phase != _APPLYING_REVIEW_FEEDBACK:
        return False
    if job.status == _VERIFIED_JOB_STATUS:
        return False
    return job.pre_bundle_manifest_bundle_id is None or job.pre_bundle_manifest_bundle_id == manifest_bundle_id


def _decide_applying_review_feedback_two_stage(
    root: Path, work_item_id: str, work_item: Any, *, last_apply_job: LaunchedJobView | None,
) -> Decision:
    """``"2.2"`` ``APPLYING_REVIEW_FEEDBACK`` (CP4B), after the ``REJECTED``
    and implementation-bundle gates, in front of CP4's corrected gate:

    1. the feedback on file must be admissible
       (:func:`evaluate_apply_implementation_review_admissibility`: a
       ``REVISE`` from one of the two ``"2.2"`` roles, bound to the
       manifest's ``bundle_id``); absent or inadmissible -> the corrected
       gate, naming why;
    2. the relaunch bound (:func:`relaunch_bound_applies`) -> a gate naming
       the earlier job, never an automatic relaunch;
    3. the work item's committed phase at ``HEAD`` must be readable (fail
       closed: otherwise nothing tells whether the review-stage write is
       pending);
    4. otherwise ``/apply-implementation-review <id>`` is selected (automatic
       by the dispatch rule), carrying the pending-write ``task_addendum``
       exactly when ``HEAD`` does not yet record ``APPLYING_REVIEW_FEEDBACK``."""
    phase = _APPLYING_REVIEW_FEEDBACK
    corrected_what, corrected_resume = _decision._STATIC_GATES[phase]
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    feedback_path = feedback_dir / "REVIEW_FEEDBACK.md"
    bundle_dir = implementation_bundle_dir(root, work_item_id)
    manifest = read_manifest_fields(root, bundle_dir)
    manifest_bundle_id = manifest.get("bundle_id")

    def _corrected_gate(problem: str, evidence_lines: tuple[str, ...]) -> Decision:
        return Decision(
            observed_phase=phase, evidence=evidence_lines, action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=f"{corrected_what} -- this Controller does not run it automatically: {problem}",
                artifact_path=str(feedback_path),
                safe_resume_command=corrected_resume.format(wid=work_item_id),
            ),
            declined=False, reason=f"{phase}: {problem}",
        )

    feedback = read_feedback_fields(root, feedback_dir)
    if feedback is None:
        return _corrected_gate(
            f"no REVIEW_FEEDBACK.md is on file at {feedback_path}",
            ("no current-round REVIEW_FEEDBACK.md on file",),
        )
    result = evaluate_apply_implementation_review_admissibility(
        feedback=feedback, manifest=manifest, work_item=work_item, current_head=_current_head(root),
    )
    if not result.admissible:
        failing = result.failure_summary()
        return _corrected_gate(
            f"the feedback on file is not an admissible two-stage REVISE for the current bundle ({failing})",
            (f"inadmissible apply-stage feedback: {failing}",),
        )

    if relaunch_bound_applies(last_apply_job, manifest_bundle_id):
        recorded = last_apply_job.pre_bundle_manifest_bundle_id
        same_bundle = (
            f"the same bundle (bundle_id {manifest_bundle_id})" if recorded is not None else
            f"the current bundle (bundle_id {manifest_bundle_id}; the job recorded no pre-state "
            "bundle_id, which counts as the same one -- fail closed)"
        )
        archive = bundle_dir.parent / "review-bundle.tar.gz"
        return Decision(
            observed_phase=phase,
            evidence=(
                f"last launched {_APPLY_IMPLEMENTATION_REVIEW} job from {phase}: {last_apply_job.job_id} "
                f"({last_apply_job.status}), pre-state bundle_manifest_bundle_id {recorded!r}",
                f"current MANIFEST.md bundle_id: {manifest_bundle_id}",
            ),
            action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    f"the previous {_APPLY_IMPLEMENTATION_REVIEW} attempt from this phase, job "
                    f"{last_apply_job.job_id}, ended {last_apply_job.status} against {same_bundle}, and "
                    "this Controller never relaunches an attempt that did not verify against the bundle "
                    f"it started from. If that attempt passed its own step 4, {bundle_dir} no longer "
                    "matches its MANIFEST.md's bundle_id, so every retry would refuse at step 1 "
                    "(assert_feedback_matches_bundle). A human either restores the edited bundle file "
                    f"from the bundle's archive ({archive}) and reruns {_APPLY_IMPLEMENTATION_REVIEW} "
                    f"{work_item_id} in a supervised session, or completes the round by hand"
                ),
                artifact_path=str(bundle_dir),
                safe_resume_command=_explain_command(work_item_id),
            ),
            declined=False,
            reason=f"{phase}: the previous {_APPLY_IMPLEMENTATION_REVIEW} job {last_apply_job.job_id} ended "
                   f"{last_apply_job.status} against the current bundle -- the relaunch bound gates "
                   "instead of relaunching",
        )

    committed = committed_work_item(root, work_item_id)
    committed_phase = committed.get("phase") if committed is not None else None
    if not isinstance(committed_phase, str):
        return _corrected_gate(
            f"the work item's committed phase at HEAD cannot be read (git show "
            f"HEAD:{_STATE_REL_PATH}), so nothing tells whether the review-stage state write is "
            "still pending",
            ("committed phase at HEAD: unreadable",),
        )

    role = feedback.get("reviewer_role")
    command = f"{_APPLY_IMPLEMENTATION_REVIEW} {work_item_id}"
    evidence_lines = (
        f"admissible {role} Status: REVISE, bound to bundle {manifest_bundle_id}",
        f"committed phase at HEAD: {committed_phase}",
    )
    if committed_phase == phase:
        return Decision(
            observed_phase=phase, evidence=evidence_lines, action=Action(command=command),
            automatic=True, gate=None, declined=False,
            reason=f"{phase}: admissible {role} REVISE on file and HEAD already records {phase} -- the "
                   "remediation round runs as the bare command",
        )
    return Decision(
        observed_phase=phase, evidence=evidence_lines,
        action=Action(
            command=command, task_addendum=pending_review_stage_write_addendum(work_item_id, committed_phase),
        ),
        automatic=True, gate=None, declined=False,
        reason=f"{phase}: admissible {role} REVISE on file; HEAD records {committed_phase}, so the "
               "review-stage state write is pending and the task carries the pending-write addendum",
    )


def decide(
    managed_repo: Any, snapshot: Any, work_item: Any, *, last_apply_job: LaunchedJobView | None = None,
) -> Decision:
    """CP4B's real entry point: checks the withdrawn-bundle outcome ahead
    of every other row for a bundle-bearing phase, then either resolves a
    phase this module owns evidence for, or falls through unchanged to
    :func:`controller.decision.decide` for every phase whose mapping needs
    no ``.ai-review/`` read at all. At the two plan-bundle-consuming
    phases, a plan bundle incoherent with the state's ``plan_revision``
    (:func:`plan_bundle_coherence`) gates next -- still ahead of the
    per-phase handlers, so ahead of the local-review BLOCK gate. At a
    ``"2.2"`` implementation-bundle-consuming phase, an implementation
    bundle incoherent with the state's implementation round
    (:func:`implementation_bundle_coherence`) gates in the same position
    (automatic-lifecycle-orchestration CP4). A handler's selected action
    then goes through the same general automatic-dispatch rule
    (``decision.apply_dispatch_rule``) as every selection
    :func:`controller.decision.decide` makes.

    ``last_apply_job`` (CP4B) is the job history the ``"2.2"``
    ``APPLYING_REVIEW_FEEDBACK`` relaunch bound reads -- built by
    ``controller.job.last_launched_apply_job_view``, which this module
    cannot import. Both callers (``job.execute_step`` and ``cli``'s
    ``explain``) pass it, so both see the same J and make the same
    decision; it is read at no other phase."""
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    root = managed_repo.root

    if phase in BUNDLE_BEARING_PHASES:
        rejected, detail = rejected_marker_detail(root, work_item_id)
        if rejected:
            return _rejected_marker_gate(root, work_item, detail)

    if phase in PLAN_BUNDLE_CONSUMING_PHASES:
        coherent, detail = plan_bundle_coherence(root, work_item_id, work_item.plan_revision)
        if not coherent:
            return _stale_plan_bundle_gate(root, work_item, detail)

    if (
        work_item.governing_workflow_version in _IMPLEMENTATION_BUNDLE_GATED_VERSIONS
        and phase in IMPLEMENTATION_BUNDLE_CONSUMING_PHASES
    ):
        current_head = _current_head(root)
        coherent, clause, detail = implementation_bundle_coherence(
            root, work_item, current_head,
            require_current_generation_head=phase != _GENERATION_HEAD_EXEMPT_PHASE,
        )
        if not coherent:
            return _implementation_bundle_gate(root, work_item, clause, detail, current_head)

    if phase == _APPLYING_REVIEW_FEEDBACK and work_item.governing_workflow_version in _APPLY_AUTOMATION_VERSIONS:
        return _decision.apply_dispatch_rule(
            _decide_applying_review_feedback_two_stage(
                root, work_item_id, work_item, last_apply_job=last_apply_job,
            ),
            work_item.governing_workflow_version,
        )

    handler = _EVIDENCE_HANDLERS.get(phase)
    if handler is not None:
        # The general automatic-dispatch rule (automatic-lifecycle-
        # orchestration CP3): a handler only selects; whether the selection
        # launches is decided by the same rule `decision.decide` applies to
        # its own handlers' output.
        return _decision.apply_dispatch_rule(
            handler(root, work_item_id, work_item), work_item.governing_workflow_version,
        )

    return _decision.decide(managed_repo, snapshot, work_item)
