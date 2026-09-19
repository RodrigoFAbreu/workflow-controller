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
admissibility model, which is deliberately not re-derived here). Two more
report-only phases (``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW``,
``AWAITING_FUNCTIONAL_REVIEW``) gain evidence-driven sub-case reports
here too, and every bundle-bearing phase gains a **withdrawn-bundle**
outcome that is checked ahead of everything else.

This module imports nothing from ``controller.managed_repo`` or
``controller.target_state`` for the same reason ``controller.decision``
does not: both sit later in the dependency order. ``managed_repo``,
``snapshot`` and ``work_item`` are accepted as plain, duck-typed objects;
this module only ever reads ``managed_repo.root`` and, off ``work_item``:
``work_item_id``, ``phase``, ``base_commit``, ``implementation_revision``,
``plan_review_stages``, ``incomplete_children``, ``registry_complete``.

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
#: review.md``, step 5).
BUNDLE_BEARING_PHASES: frozenset[str] = PLAN_STAGE_PHASES | frozenset(
    {"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"}
)


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
    stage argument."""
    return _scoped_else_flat(root, work_item_id, leaf="feedback")


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


def read_manifest_fields(root: Path, bundle_dir: Path) -> dict[str, Any]:
    """Read ``<bundle_dir>/MANIFEST.md``'s ``bundle_id:``/
    ``generation_head:`` labelled lines. ``_exists`` is ``False`` when the
    file itself cannot be read -- distinct from the file existing but
    lacking one of the two lines, which each read as ``None``."""
    path = root / bundle_dir / "MANIFEST.md"
    try:
        text = path.read_text()
    except OSError:
        return {"bundle_id": None, "generation_head": None, "_exists": False}
    return {
        "bundle_id": read_labelled_line(text, "bundle_id:"),
        "generation_head": read_labelled_line(text, "generation_head:"),
        "_exists": True,
    }


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
        "reviewed_content_id": read_labelled_line(text, "Reviewed content ID:"),
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
# Report-only phases whose report is now evidence-driven.
# ---------------------------------------------------------------------------


def _decide_awaiting_external_implementation_review(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
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
        what = (
            f"MANIFEST.md's generation_head ({manifest_head}) is behind the target's "
            f"committed HEAD ({current_head}); an ordinary post-fix regeneration refuses "
            "in this state -- a human runs /recover-implementation-provenance"
        )
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


def _decide_awaiting_manual_external_implementation_review(
    root: Path, work_item_id: str, work_item: Any,
) -> Decision:
    """Revision 64's own three-way sub-case, "the same read-only evidence
    the plan stage's manual gate uses one stage over": no current-round
    ``REVIEW_FEEDBACK.md`` (or one declaring the *local*-stage role) hands
    the bundle to a reviewer; a ``MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW``
    verdict on file names ``/record-manual-implementation-review`` as next;
    an admissible ``Status: BLOCK`` needs explicit user resolution first.
    **Report-only at every sub-case, unlike its plan-stage counterpart**:
    the Controller reports and never launches here, so the plan-stage
    admissibility model (bundle-id/generation-head currency,
    ``ClauseFailure``/``AdmissibilityResult``) is deliberately not
    re-derived -- nothing at this phase turns on it.

    **No legacy-cased role alias** (unlike the plan stage's
    :func:`_normalize_role`): ``validate_manual_implementation_review_
    preconditions`` (``scripts/workflow_state.py:12709``) refuses on an
    exact-spelling mismatch, since the ``implementation_review_stages``
    ledger is introduced fresh at ``"2.2"`` with no pre-``SCREAMING_SNAKE_
    CASE`` history behind it -- so this read matches on the literal string,
    never normalized."""
    phase = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
    feedback_dir = resolve_feedback_dir(root, work_item_id)
    bundle_dir = resolve_bundle_dir(root, work_item_id, phase=phase)
    feedback = read_feedback_fields(root, feedback_dir)
    feedback_path = feedback_dir / "REVIEW_FEEDBACK.md"

    role = feedback.get("reviewer_role") if feedback is not None else None
    if feedback is None or role != "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW":
        return Decision(
            observed_phase=phase, evidence=(), action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required=(
                    "upload the current implementation bundle to a manual external "
                    "reviewer and paste the verdict into REVIEW_FEEDBACK.md, declaring "
                    "Reviewer role: MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
                ),
                artifact_path=str(bundle_dir),
                safe_resume_command=f"/record-manual-implementation-review {work_item_id}",
            ),
            declined=False,
            reason=(
                f"{phase}: no current-round REVIEW_FEEDBACK.md on file"
                if feedback is None else
                f"{phase}: feedback declares Reviewer role {role!r}, not "
                "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
            ),
        )

    status = feedback.get("status")
    if status == "BLOCK":
        return Decision(
            observed_phase=phase, evidence=("Status: BLOCK",), action=None, automatic=False,
            gate=HumanGate(
                repository=str(root), work_item_id=work_item_id, phase=phase,
                what_is_required="the external reviewer blocked this round; a human "
                                  "resolves it before anything else runs",
                artifact_path=str(feedback_path),
                safe_resume_command=f"/record-manual-implementation-review {work_item_id}",
            ),
            declined=False,
            reason=f"{phase}: Status: BLOCK -- a human resolves before anything else runs",
        )

    return Decision(
        observed_phase=phase, evidence=(f"MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW verdict on "
                                         f"file, Status: {status}",),
        action=None, automatic=False,
        gate=HumanGate(
            repository=str(root), work_item_id=work_item_id, phase=phase,
            what_is_required=(
                "a MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW verdict is on file; a human runs "
                "/record-manual-implementation-review"
            ),
            artifact_path=str(feedback_path),
            safe_resume_command=f"/record-manual-implementation-review {work_item_id}",
        ),
        declined=False,
        reason=f"{phase}: a MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW verdict is on file "
               f"(Status: {status})",
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
    "AWAITING_FUNCTIONAL_REVIEW": _decide_awaiting_functional_review,
}


def _regeneration_command(phase: str, work_item_id: str) -> str:
    if phase in PLAN_STAGE_PHASES:
        return f"scripts/prepare-ai-review.sh <base-sha> plan {work_item_id}"
    return f"scripts/prepare-ai-review.sh <base-sha> post-fix {work_item_id}"


def decide(managed_repo: Any, snapshot: Any, work_item: Any) -> Decision:
    """CP4B's real entry point: checks the withdrawn-bundle outcome ahead
    of every other row for a bundle-bearing phase, then either resolves a
    phase this module owns evidence for, or falls through unchanged to
    :func:`controller.decision.decide` for every phase whose mapping needs
    no ``.ai-review/`` read at all."""
    phase = work_item.phase
    work_item_id = work_item.work_item_id
    root = managed_repo.root

    if phase in BUNDLE_BEARING_PHASES:
        rejected, detail = rejected_marker_detail(root, work_item_id)
        if rejected:
            marker_path = resolve_rejected_marker_path(root, work_item_id)
            regen = _regeneration_command(phase, work_item_id)
            return Decision(
                observed_phase=phase,
                evidence=(f"REJECTED marker present at {marker_path}: {detail}",),
                action=None, automatic=False,
                gate=HumanGate(
                    repository=str(root), work_item_id=work_item_id, phase=phase,
                    what_is_required=(
                        f"the current bundle was withdrawn ({detail}); regenerate it with "
                        f"{regen} before any further command runs"
                    ),
                    artifact_path=str(marker_path), safe_resume_command=regen,
                ),
                declined=False,
                reason=f"{phase}: a REJECTED marker is present at {marker_path}, checked "
                       "ahead of every other row",
            )

    handler = _EVIDENCE_HANDLERS.get(phase)
    if handler is not None:
        return handler(root, work_item_id, work_item)

    return _decision.decide(managed_repo, snapshot, work_item)
