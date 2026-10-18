"""The ``pr_review_result`` reporter's pure parts
(``workflow-controller-pr-defect-loop`` CP2, D4-D6).

At an external pull-request gate under an automatic acceptance gate (rows 38f
and 38g) the Controller runs the Workflow's one fixed query, wraps the
verbatim output in the ``forge`` block the Workflow's external-result
recorder (kind ``pr_review_result``) takes, and hands it over. This module holds what needs no
I/O: the anchor commit, the ``forge`` block, the one wait predicate of D6 and
the idempotence comparison of D5. The Controller derives no pull-request fact
from the output: the Workflow parses ``raw`` itself, and the single
hand-written reading here (:func:`pending_checks`) only decides whether to
*wait before* reporting, mirroring the Workflow's own ``parse_forge_raw`` and
``_checks`` (a test ties the two together over the vendored tree).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from typing import Any

#: The action id and the ``satisfied_by`` of the gates the reporter acts at.
REPORT_ACTION_ID = "pr.review.external"
REPORT_SATISFIED_BY = "pr_review_result"
#: The rows it acts at: 38f waits for a fact, 38g for an approval. The wait of
#: D6 applies to 38f only.
ROW_EVIDENCE_NEEDED = "38f"
ROW_APPROVAL_NEEDED = "38g"
REPORT_ROWS = (ROW_EVIDENCE_NEEDED, ROW_APPROVAL_NEEDED)
#: The Workflow's name for the requirement 38f lists only when ``require_ci``
#: is effective; D6 (b) reads it from the stop text.
CI_GREEN_REQUIREMENT = "ci_green"

#: The kind of the job records the reporter writes, and its ``forge`` block's
#: constants (the vendored ``workflow_forge`` names the same query).
JOB_KIND = "pr_report"
FORGE_SOURCE = "github"
FORGE_QUERY_NAME = "pr-list-v1"
FETCHED_BY_NAME = "workflow-controller"
#: A page this size or larger is the Workflow's ``forge_undecidable``.
PAGE_LIMIT = 200

#: The reporter's outcomes.
REPORTED = "reported"
UNCHANGED = "unchanged"
PENDING = "pending"
FORGE_UNAVAILABLE = "forge_unavailable"
NO_PULL_REQUEST = "no_pull_request"
REFUSED = "refused"
STALE = "stale"
OUTCOMES = (REPORTED, UNCHANGED, PENDING, FORGE_UNAVAILABLE, NO_PULL_REQUEST, REFUSED, STALE)

#: The gate codes of the stops.
GATE_UNCHANGED = "pr_report_unchanged"
GATE_CHECKS_PENDING = "pr_checks_pending"
GATE_NO_PULL_REQUEST = "pr_report_no_pull_request"
GATE_FORGE_UNAVAILABLE = "pr_report_forge_unavailable"
GATE_REFUSED = "pr_report_refused"
GATE_STALE = "pr_report_stale"

_FAILED_CONCLUSIONS = frozenset({"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE",
                                 "ERROR"})
_PENDING_STATUSES = frozenset({"QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"})


def anchor_commit(work_item: Any) -> str | None:
    """The commit the Workflow queries for ``work_item`` (its
    ``gate_policy.anchor_of``): the technical approval's
    ``reviewed_content_commit`` when the approval carries it together with
    ``approved_review_content_id``, else ``reviewed_implementation_head``,
    else ``None`` (nothing to report yet). ``work_item`` is a
    :class:`~controller.target_state.WorkItemView`."""
    approval = getattr(work_item, "technical_approval", None)
    if isinstance(approval, Mapping) and approval.get("reviewed_content_commit") \
            and approval.get("approved_review_content_id"):
        return str(approval["reviewed_content_commit"])
    head = getattr(work_item, "reviewed_implementation_head", None)
    return str(head) if head else None


def raw_sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def forge_block(*, repository: str, queried_commit: str, raw: str, fetched_at: str, version: str) -> dict:
    """The ``forge`` block of a ``pr_review_result`` input: the verbatim
    stdout and its digest, with the provenance the Workflow requires."""
    return {"source": FORGE_SOURCE, "query": FORGE_QUERY_NAME, "repository": repository,
            "queried_commit": queried_commit, "fetched_at": fetched_at,
            "fetched_by": {"name": FETCHED_BY_NAME, "version": version}, "raw": raw,
            "raw_sha256": raw_sha256(raw)}


def input_document(block: Mapping[str, Any], run_ref: str) -> dict:
    """The JSON object the external-result recorder with kind ``pr_review_result``
    reads: the ``forge`` block and the Controller job's ``run_ref``."""
    return {"forge": dict(block), "run_ref": run_ref}


def records_of(raw: str) -> list[dict] | None:
    """The query output as a list of objects, or ``None`` when it is not one
    (the Workflow then refuses the report as ``forge_unparseable``)."""
    try:
        records = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return None
    return records


def selected_record(records: list[dict]) -> dict | None:
    """The record the Workflow would select (``parse_forge_raw``): the single
    ``OPEN`` one, else the newest (highest ``number``) closed or merged one.
    ``None`` when there is none, or when the answer cannot decide (two open
    records, a full page, a record with no number): the Workflow refuses
    those, so the Controller never waits on them."""
    if not records or len(records) >= PAGE_LIMIT:
        return None
    opened = [r for r in records if r.get("state") == "OPEN"]
    if len(opened) > 1:
        return None
    if opened:
        return opened[0]
    numbered = [r for r in records if isinstance(r.get("number"), int) and not isinstance(r.get("number"), bool)]
    if len(numbered) != len(records):
        return None
    return max(numbered, key=lambda r: r["number"])


def checks_state(rollup: Any) -> str | None:
    """The Workflow's ``_checks`` rule over a ``statusCheckRollup``:
    ``failure`` when any check failed, else ``pending`` when any is
    unfinished or none reported (fail closed), else ``success``; ``None``
    when an entry is not an object (the Workflow refuses that)."""
    entries = rollup if isinstance(rollup, list) else []
    failing = pending = False
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        conclusion = str(entry.get("conclusion") or entry.get("state") or "").upper()
        status = str(entry.get("status") or "").upper()
        if conclusion in _FAILED_CONCLUSIONS:
            failing = True
        elif status in _PENDING_STATUSES or conclusion in _PENDING_STATUSES or conclusion == "EXPECTED":
            pending = True
        elif not conclusion or status not in ("", "COMPLETED"):
            pending = True
    if failing:
        return "failure"
    return "pending" if pending or not entries else "success"


@dataclasses.dataclass(frozen=True)
class Wait:
    """D6's verdict for one query output: ``applies`` when the step waits
    instead of reporting; ``empty`` says the selected record has no checks
    registered (the stop text then says "no checks reported")."""

    applies: bool
    empty: bool = False


NO_WAIT = Wait(False)


_ENTRY_PREAMBLE = "pull-request fact: "


def names_ci_green(reason_text: str | None) -> bool:
    """The stop text names ``ci_green`` as an unmet requirement: ``ci_green: `` at the start, right
    after the row's preamble (``... pull-request fact: ``), or right after an entry separator
    ``; `` -- not the token inside another entry's detail, whatever colons that detail holds."""
    text = reason_text or ""
    token = CI_GREEN_REQUIREMENT + ": "
    at = text.find(token)
    while at >= 0:
        if at == 0 or text[:at].endswith((_ENTRY_PREAMBLE, "; ")):
            return True
        at = text.find(token, at + 1)
    return False


def pending_checks(raw: str, *, row: str | None, reason_text: str) -> Wait:
    """D6: wait only when **all three** hold -- (a) the decision is row 38f,
    (b) the row's own stop text names the unmet ``ci_green`` requirement, and
    (c) the record the Workflow would select has pending checks and no
    standing objection (no ``CHANGES_REQUESTED`` decision, no failing check).
    A query with no record at the anchor commit never waits."""
    if row != ROW_EVIDENCE_NEEDED or not names_ci_green(reason_text):
        return NO_WAIT
    records = records_of(raw)
    record = None if records is None else selected_record(records)
    if record is None:
        return NO_WAIT
    state = checks_state(record.get("statusCheckRollup"))
    if state != "pending" or record.get("reviewDecision") == "CHANGES_REQUESTED":
        return NO_WAIT
    rollup = record.get("statusCheckRollup")
    return Wait(True, empty=not (isinstance(rollup, list) and rollup))


def is_unchanged(raw: str, last_reported_sha: str | None) -> bool:
    """D5: the fresh output equals what the last report of this item sent."""
    return last_reported_sha is not None and raw_sha256(raw) == last_reported_sha


def pending_text(wait: Wait, deadline_note: str = "") -> str:
    what = "no checks reported" if wait.empty else "checks pending"
    return (f"the pull request has {what} and the Workflow's gate awaits {CI_GREEN_REQUIREMENT}; nothing is "
            f"reported until they finish{deadline_note}")
