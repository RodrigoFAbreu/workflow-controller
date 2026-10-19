"""The functional tester's pure parts
(``workflow-controller-automated-functional-review`` CP1, D4-D8).

At row 38e (``functional_evidence_needed``) under an automatic acceptance gate
the Controller runs an independent tester in an isolated sandbox and records
one ``functional_evidence`` result per checklist flow. This module holds what
needs no process and no sandbox:

- the checklist section reader and the flow recognition rules of D4
  (:func:`read_flows`), with the ``checklist_unrecognised`` refusal;
- the ``RESULTS.json`` validator (:func:`validate_results`) and the "not run"
  entries for a flow the tester left out (:func:`complete_entries`);
- the ``functional_evidence`` input document of D5 (:func:`evidence_input`);
- the derivation of the sandbox's writable locations from the committed
  ``.gitignore`` (:func:`derive_writable_locations`, D5 R7-001);
- the findings text of D6 (:func:`findings_text`), the tester's prompt
  (:func:`tester_prompt`), the outcome and gate constants of D7 and the digest
  helpers;
- the run settings the tester is launched with and the enforcement probe
  (``CP2``, D2: :func:`tester_settings`, :func:`static_probe`,
  :func:`probe_command`, :func:`judge_probe`).

It writes nothing: the Controller-written logs of a "not run" entry are
returned as bytes for the caller to publish through ``controller.runtime``.
Files are read with ``lstat`` and ``O_NOFOLLOW``, so a path the tester could
have turned into a symlink is never opened.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from controller.errors import ControllerError

# ---------------------------------------------------------------------------
# What the step acts at, and its vocabulary (D1, D7, D8).
# ---------------------------------------------------------------------------

#: The action id, ``satisfied_by`` and row the step acts at.
ACTION_ID = "functional.evidence.external"
SATISFIED_BY = "functional_evidence"
ROW = "38e"
#: The kind of the job records the step writes, and of the recorded results.
JOB_KIND = "functional_test"
RESULT_KIND = "functional_evidence"

#: Tester failures that end ``findings`` before the step stops (D7).
ROUND_BOUND = 3

#: The step's outcomes.
RECORDED = "recorded"
FINDINGS = "findings"
ENVIRONMENT_LIMITED = "environment_limited"
UNCHANGED = "unchanged"
OUTCOMES = (RECORDED, FINDINGS, ENVIRONMENT_LIMITED, UNCHANGED)

#: The gate codes of the stops. Every one sends ``user_gate`` except
#: :data:`GATE_UNCHANGED`, which sends ``lane_blocked``.
GATE_FLOWS_NOT_RUNNABLE = "flows_not_runnable"
GATE_TESTED_BYTES_UNBOUND = "tested_bytes_unbound"
GATE_CHECKLIST_UNRECOGNISED = "checklist_unrecognised"
GATE_TESTER_UNAVAILABLE = "functional_tester_unavailable"
GATE_RESULTS_INVALID = "results_invalid"
GATE_ISOLATION_BREACH = "isolation_breach"
GATE_RECORDING_REFUSED = "recording_refused"
GATE_ROUNDS_EXHAUSTED = "functional_rounds_exhausted"
GATE_UNCHANGED = "functional_unchanged"
FAULT_GATES = (
    GATE_FLOWS_NOT_RUNNABLE, GATE_TESTED_BYTES_UNBOUND, GATE_CHECKLIST_UNRECOGNISED, GATE_TESTER_UNAVAILABLE,
    GATE_RESULTS_INVALID, GATE_ISOLATION_BREACH, GATE_RECORDING_REFUSED, GATE_ROUNDS_EXHAUSTED,
)
GATES = FAULT_GATES + (GATE_UNCHANGED,)

#: The tester's file and the statuses and reasons an entry may carry (D4).
RESULTS_FILE = "RESULTS.json"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUSES = (STATUS_PASSED, STATUS_FAILED)
REASON_DEFECT = "defect"
REASON_ENVIRONMENT = "environment"
REASONS = (REASON_DEFECT, REASON_ENVIRONMENT)
_ENTRY_KEYS = frozenset({"flow_id", "status", "summary", "log", "reason"})
_REQUIRED_ENTRY_KEYS = frozenset({"flow_id", "status", "summary", "log"})

#: Why a flow has no entry of the tester's (D4, O1).
CAUSE_TIMEOUT = "timeout"
CAUSE_LEFT_OUT = "left_out"

FLOW_ID_PREFIX = "flow-"
#: ``RESULTS.json`` and a log are read at most this many bytes into memory.
MAX_RESULTS_BYTES = 1 << 20
LOG_EXCERPT_CHARS = 2000
_CHUNK = 1 << 16

#: The sandbox's stated limits (D3), the one text the tester's prompt and the
#: checklist writer's prompt note share.
SANDBOX_LIMITS: tuple[str, ...] = (
    "no network access and no connection to a host socket",
    "no real GitHub: `gh` is a stand-in that refuses and logs, so a flow that needs a pull request or any forge "
    "call cannot run",
    "no real `claude`, `codex` or `ssh`: each is a stand-in that refuses and logs",
    "a `PATH` of the sandbox's `bin/` and the system directories, with no other tool",
    "the product clone is read-only: no flow may write inside it, except the writable locations listed",
    "writes go to the output folder and the scratch `HOME`, `XDG_*` directories and `TMPDIR`",
)


class ChecklistUnrecognised(ControllerError):
    """No flow of the checklist can be recognised (D4): the section is
    missing, or none of the four shapes finds a flow, or a linked file
    cannot be read. The step stops before any tester starts."""

    code = GATE_CHECKLIST_UNRECOGNISED


class ResultsInvalid(ControllerError):
    """The tester's results cannot be used (D4): a missing or unparsable
    file or a structural fault. Nothing is recorded."""

    code = GATE_RESULTS_INVALID


# ---------------------------------------------------------------------------
# Digests.
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """The sha256 of ``data``, 64 hex characters (a valid ``log_digest``)."""
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data: bytes) -> str:
    """The Git blob id of ``data``, computed without Git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 -- Git's own object id


def _open_regular(path: Path) -> int:
    """A descriptor for ``path`` opened without following a symlink; the
    caller has already ``lstat``-checked every component. Raises
    ``OSError`` for a symlink, and ``ValueError`` for a non-regular file."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError(f"{path} is not a regular file")
    return descriptor


def sha256_file(path: Path) -> str:
    """The sha256 of the regular file at ``path``, read without following a
    symlink."""
    digest = hashlib.sha256()
    descriptor = _open_regular(path)
    try:
        while chunk := os.read(descriptor, _CHUNK):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# The checklist: section and flows (D4).
# ---------------------------------------------------------------------------

SECTION_TITLE = "Functional review checklist"
#: Section and heading names that are never flows.
NEVER_FLOWS = ("setup", "flows", "test data", "known limitations")
FLOW_SHAPES = (
    "table rows whose first cell is a short label (`| A | do | expect |`)",
    "bold labelled paragraphs (`**A. The title.**`)",
    "headings below the section's own that start with a label or `Flow`",
    "a top-level numbered list",
)

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_LABEL = r"(?:[A-Za-z]|\d{1,2})"
_LABEL_CELL_RE = re.compile(rf"^{_LABEL}$")
_BOLD_RE = re.compile(rf"^\*\*(?P<label>{_LABEL})[.):]")
_HEADING_FLOW_RE = re.compile(rf"^(?:(?P<label>{_LABEL})[.):]|Flow\b[ \t]*(?P<rest>[^ \t.:)]*))")
_NUMBERED_RE = re.compile(r"^(?P<label>\d{1,3})[.)][ \t]+\S")
_SEPARATOR_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$")
_LINK_RE = re.compile(r"^(?:[-*][ \t]+)?\[[^\]]*\]\((?P<target>[^)\s]+)\)\.?$")


@dataclasses.dataclass(frozen=True)
class Flow:
    """One checklist flow: the Controller's id (``flow-N``), the checklist's
    own label and the text of its row, paragraph, heading block or item."""

    flow_id: str
    label: str
    text: str


@dataclasses.dataclass(frozen=True)
class Section:
    """The checklist section: its heading ``level`` (0 for a linked file,
    whose every heading is below it) and its body lines."""

    level: int
    lines: tuple[str, ...]


def _fence_mask(lines: Sequence[str]) -> list[bool]:
    """``True`` for every line inside a fenced code block, fence lines
    included: none of them is a heading, table row, labelled paragraph or
    list item."""
    mask: list[bool] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
            mask.append(fence is not None)
            continue
        mask.append(True)
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1] and not match.group(2).strip():
            fence = None
    return mask


def _heading(line: str, masked: bool) -> tuple[int, str] | None:
    match = None if masked else _HEADING_RE.match(line)
    return (len(match.group(1)), match.group(2)) if match else None


def checklist_section(text: str) -> Section | None:
    """The section under the heading ``Functional review checklist`` (any
    level) up to the next heading of the same or a higher level; sub-headings
    belong to it. ``None`` when no such heading exists."""
    lines = text.split("\n")
    mask = _fence_mask(lines)
    start = level = None
    for index, line in enumerate(lines):
        found = _heading(line, mask[index])
        if found and found[1].strip().lower() == SECTION_TITLE.lower():
            start, level = index, found[0]
            break
    if start is None or level is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        found = _heading(lines[index], mask[index])
        if found and found[0] <= level:
            end = index
            break
    return Section(level=level, lines=tuple(lines[start + 1:end]))


def _table_flows(lines: Sequence[str], mask: Sequence[bool]) -> list[tuple[str, str]]:
    flows: list[tuple[str, str]] = []
    index = 1
    while index < len(lines):
        if mask[index] or mask[index - 1] or "|" not in lines[index - 1] or not _SEPARATOR_RE.match(lines[index]):
            index += 1
            continue
        index += 1
        while index < len(lines) and not mask[index] and "|" in lines[index]:
            cells = lines[index].strip().strip("|").split("|")
            label = cells[0].strip().strip("*").strip()
            if _LABEL_CELL_RE.match(label):
                flows.append((label, lines[index].strip()))
            index += 1
    return flows


def _paragraph_flows(lines: Sequence[str], mask: Sequence[bool]) -> list[tuple[str, str]]:
    flows: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = None if mask[index] else _BOLD_RE.match(line)
        if not match:
            continue
        end = index + 1
        while end < len(lines):
            if not mask[end] and (not lines[end].strip() or _BOLD_RE.match(lines[end]) or _heading(lines[end], False)):
                break
            end += 1
        flows.append((match.group("label"), "\n".join(lines[index:end]).rstrip()))
    return flows


def _heading_flows(lines: Sequence[str], mask: Sequence[bool], section_level: int) -> list[tuple[str, str]]:
    flows: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        found = _heading(line, mask[index])
        if not found or found[0] <= section_level:
            continue
        title = found[1].strip()
        if any(title.lower().startswith(word) for word in NEVER_FLOWS):
            continue
        match = _HEADING_FLOW_RE.match(title)
        if not match:
            continue
        label = match.group("label") or match.group("rest") or "Flow"
        end = len(lines)
        for later in range(index + 1, len(lines)):
            nested = _heading(lines[later], mask[later])
            if nested and nested[0] <= found[0]:
                end = later
                break
        flows.append((label, "\n".join(lines[index:end]).rstrip()))
    return flows


def _numbered_flows(lines: Sequence[str], mask: Sequence[bool]) -> list[tuple[str, str]]:
    flows: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = None if mask[index] else _NUMBERED_RE.match(line)
        if not match:
            continue
        end = index + 1
        while end < len(lines):
            if mask[end] or lines[end][:1] in (" ", "\t") or (not lines[end].strip() and _continues(lines, mask, end)):
                end += 1
                continue
            break
        flows.append((match.group("label"), "\n".join(lines[index:end]).rstrip()))
    return flows


def _continues(lines: Sequence[str], mask: Sequence[bool], blank: int) -> bool:
    """Whether the blank line at ``blank`` is followed by more of the same
    list item (an indented or fenced line)."""
    following = blank + 1
    return following < len(lines) and (mask[following] or lines[following][:1] in (" ", "\t"))


def _flows_of_lines(lines: Sequence[str], section_level: int) -> list[tuple[str, str]]:
    mask = _fence_mask(lines)
    for found in (
        _table_flows(lines, mask), _paragraph_flows(lines, mask),
        _heading_flows(lines, mask, section_level), _numbered_flows(lines, mask),
    ):
        if found:
            return found
    return []


def _linked_path(body: Sequence[str]) -> str | None:
    """The repository path a section whose only content is one link names,
    or ``None`` when the section is anything else or the link leaves the
    repository."""
    content = [line.strip() for line in body if line.strip()]
    if len(content) != 1:
        return None
    match = _LINK_RE.match(content[0])
    if not match:
        return None
    target = match.group("target").partition("#")[0]
    if not target or "://" in target or target.startswith(("/", "~", "mailto:")) or "\0" in target:
        return None
    normal = posixpath.normpath(target)
    if normal in (".", "..") or normal.startswith("../") or "\\" in normal:
        return None
    return normal


def _numbered(found: Iterable[tuple[str, str]]) -> list[Flow]:
    return [Flow(flow_id=f"{FLOW_ID_PREFIX}{number}", label=label, text=text)
            for number, (label, text) in enumerate(found, start=1)]


def _unrecognised(reason: str, **evidence: Any) -> ChecklistUnrecognised:
    shapes = "; ".join(f"({number}) {shape}" for number, shape in enumerate(FLOW_SHAPES, start=1))
    return ChecklistUnrecognised(
        f"no functional flow can be recognised in the '{SECTION_TITLE}' section: {reason}. The section has to "
        f"carry flows of one of these shapes: {shapes}. Fix the checklist, or set `functional.automated` false to "
        f"keep the person's stop.",
        evidence={"section": SECTION_TITLE, "reason": reason, **evidence},
    )


def read_flows(text: str, *, read_file: Callable[[str], str | None] | None = None) -> list[Flow]:
    """The checklist flows of ``text`` (``docs/ACTIVE_MILESTONE.md``), numbered
    ``flow-1``, ``flow-2`` in the order the checklist lists them. The first of
    the four rules of D4 that finds a flow decides, applied to the section
    only. A section whose only content is a link to a repository file is read
    from that file by the same rules: ``read_file(path)`` returns its text, or
    ``None`` when it does not exist. Raises :class:`ChecklistUnrecognised`."""
    section = checklist_section(text)
    if section is None:
        raise _unrecognised("the section is missing")
    lines, level = section.lines, section.level
    link = _linked_path(lines)
    if link is not None:
        content = read_file(link) if read_file is not None else None
        if content is None:
            raise _unrecognised(f"the linked file {link!r} cannot be read", link=link)
        lines, level = tuple(content.split("\n")), 0
    flows = _numbered(_flows_of_lines(lines, level))
    if not flows:
        raise _unrecognised("none of the four shapes finds a flow")
    return flows


# ---------------------------------------------------------------------------
# The tester's results (D4).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Entry:
    """One validated result. ``log_path`` is the log inside the output
    folder and ``log_digest`` the sha256 of its bytes. ``log_content`` is
    ``None`` for a log the tester wrote (it is on disk) and the bytes of a
    Controller-written one (a flow the tester left out), which the caller
    writes before it is read."""

    flow_id: str
    status: str
    summary: str
    reason: str | None
    log_path: Path
    log_digest: str
    log_content: bytes | None = None


def _invalid(detail: str, **evidence: Any) -> ResultsInvalid:
    return ResultsInvalid(f"the tester's results cannot be used: {detail}", evidence={"detail": detail, **evidence})


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate key(s) {duplicates}")
    return dict(pairs)


def _checked_file(output_dir: Path, name: str, what: str) -> Path:
    """``output_dir/name`` after an ``lstat`` of every component: none may be
    a symlink, the lexical path may not leave ``output_dir``, and the last
    must be a regular file. Nothing is opened."""
    if not isinstance(name, str) or not name or "\0" in name:
        raise _invalid(f"{what} {name!r} is not a file name")
    candidate = posixpath.normpath(name)
    if posixpath.isabs(candidate) or candidate == ".." or candidate.startswith("../") or candidate == ".":
        raise _invalid(f"{what} {name!r} is outside the output folder", name=name)
    current = output_dir
    for part in candidate.split("/"):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except OSError as exc:
            raise _invalid(f"{what} {name!r} cannot be read ({exc})", name=name) from None
        if stat.S_ISLNK(mode):
            raise _invalid(f"{what} {name!r} is, or passes through, a symlink", name=name)
    if not stat.S_ISREG(mode):
        raise _invalid(f"{what} {name!r} is not a regular file", name=name)
    return current


def _read_results_file(path: Path) -> bytes:
    try:
        descriptor = _open_regular(path)
    except (OSError, ValueError) as exc:
        raise _invalid(f"{RESULTS_FILE} cannot be read ({exc})") from None
    try:
        data = os.read(descriptor, MAX_RESULTS_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_RESULTS_BYTES:
        raise _invalid(f"{RESULTS_FILE} is larger than {MAX_RESULTS_BYTES} bytes")
    return data


def validate_results(output_dir: Path, flows: Sequence[Flow]) -> list[Entry]:
    """The entries of ``output_dir/RESULTS.json``, validated and with every
    log digested; raises :class:`ResultsInvalid` for a structural fault only.
    A flow with no entry is not a fault (:func:`complete_entries` adds it).
    A symlink, or a path through one, is refused with its target never
    opened."""
    if not os.path.lexists(output_dir / RESULTS_FILE):
        raise _invalid(f"{RESULTS_FILE} was not written")
    path = _checked_file(output_dir, RESULTS_FILE, RESULTS_FILE)
    try:
        document = json.loads(_read_results_file(path).decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _invalid(f"{RESULTS_FILE} is not valid JSON ({exc})") from None
    if not isinstance(document, list):
        raise _invalid(f"{RESULTS_FILE} must be a list of entries")
    known = {flow.flow_id for flow in flows}
    seen: set[str] = set()
    entries: list[Entry] = []
    for position, raw in enumerate(document):
        where = f"entry {position}"
        if not isinstance(raw, dict):
            raise _invalid(f"{where} is not an object")
        unknown = sorted(set(raw) - _ENTRY_KEYS)
        if unknown:
            raise _invalid(f"{where} has unknown key(s) {unknown}", unknown_keys=unknown)
        missing = sorted(_REQUIRED_ENTRY_KEYS - set(raw))
        if missing:
            raise _invalid(f"{where} lacks {missing}", missing_keys=missing)
        flow_id, status, summary = raw["flow_id"], raw["status"], raw["summary"]
        if not isinstance(flow_id, str) or flow_id not in known:
            raise _invalid(f"{where} names {flow_id!r}, which is no checklist flow", flow_id=flow_id)
        if flow_id in seen:
            raise _invalid(f"{flow_id} has more than one entry", flow_id=flow_id)
        seen.add(flow_id)
        if status not in STATUSES:
            raise _invalid(f"{flow_id}: status {status!r} is neither of {list(STATUSES)}", flow_id=flow_id)
        if not isinstance(summary, str):
            raise _invalid(f"{flow_id}: summary is not a string", flow_id=flow_id)
        reason = raw.get("reason")
        if status == STATUS_FAILED and reason not in REASONS:
            raise _invalid(f"{flow_id}: a failed entry needs a reason, one of {list(REASONS)}", flow_id=flow_id)
        if status == STATUS_PASSED and "reason" in raw:
            raise _invalid(f"{flow_id}: a passed entry carries no reason", flow_id=flow_id)
        log = _checked_file(output_dir, raw["log"], f"{flow_id}: log")
        try:
            digest = sha256_file(log)
        except (OSError, ValueError) as exc:
            raise _invalid(f"{flow_id}: log cannot be read ({exc})", flow_id=flow_id) from None
        entries.append(Entry(flow_id=flow_id, status=status, summary=summary, reason=reason if status ==
                             STATUS_FAILED else None, log_path=log, log_digest=digest))
    return entries


def not_run_summary(cause: str) -> str:
    """The Controller-written summary of a flow the tester left out."""
    if cause == CAUSE_TIMEOUT:
        return "not run: the tester ended at the timeout"
    return "not run: the tester left this flow out of the results file"


def not_run_log_name(flow_id: str) -> str:
    return f"{flow_id}.not-run.log"


def not_run_log(flow: Flow, *, cause: str, job_id: str, timeout_seconds: int | None) -> bytes:
    """The short log the Controller writes for a flow with no entry (the
    Workflow refuses a record without a valid ``log_digest``): cause,
    ``functional.timeout_seconds`` where it applies, the job id."""
    lines = [not_run_summary(cause), f"flow: {flow.flow_id} ({flow.label})", f"job: {job_id}"]
    if cause == CAUSE_TIMEOUT and timeout_seconds is not None:
        lines.append(f"functional.timeout_seconds: {timeout_seconds}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def complete_entries(
    output_dir: Path, flows: Sequence[Flow], entries: Sequence[Entry], *, timed_out: bool, job_id: str,
    timeout_seconds: int | None = None,
) -> list[Entry]:
    """One entry per flow, in checklist order. A flow the tester left out is
    a ``failed`` ``environment`` entry (not a product defect, O1) with a
    Controller-written summary naming the cause and a Controller-written log
    whose sha256 is a valid ``log_digest``, so it is recorded rather than
    refused as ``evidence_malformed``."""
    by_id = {entry.flow_id: entry for entry in entries}
    cause = CAUSE_TIMEOUT if timed_out else CAUSE_LEFT_OUT
    completed: list[Entry] = []
    for flow in flows:
        if flow.flow_id in by_id:
            completed.append(by_id[flow.flow_id])
            continue
        content = not_run_log(flow, cause=cause, job_id=job_id, timeout_seconds=timeout_seconds)
        completed.append(Entry(
            flow_id=flow.flow_id, status=STATUS_FAILED, summary=not_run_summary(cause), reason=REASON_ENVIRONMENT,
            log_path=output_dir / not_run_log_name(flow.flow_id), log_digest=sha256_bytes(content),
            log_content=content,
        ))
    return completed


def log_excerpt(entry: Entry, limit: int = LOG_EXCERPT_CHARS) -> str:
    """The last ``limit`` characters of the entry's log: its Controller-
    written bytes, or the tester's file read without following a symlink."""
    if entry.log_content is not None:
        data = entry.log_content
    else:
        try:
            descriptor = _open_regular(entry.log_path)
        except (OSError, ValueError):
            return ""
        try:
            data = os.read(descriptor, MAX_RESULTS_BYTES)
        finally:
            os.close(descriptor)
    text = data.decode("utf-8", errors="backslashreplace")
    return text[-limit:]


# ---------------------------------------------------------------------------
# The evidence document and the findings text (D5, D6).
# ---------------------------------------------------------------------------


def run_ref(job_id: str) -> str:
    return f"controller:{job_id}"


def evidence_input(
    entry: Entry, *, head: str, ran_at: str, job_id: str, version: str, checklist_blob: str,
) -> dict[str, Any]:
    """The Workflow's ``functional_evidence`` input for ``entry`` (D5):
    exactly the eight keys of its schema. ``summary`` holds the tester's
    text, the ``reason`` of a failed entry and the checklist blob id, so a
    later inspection tells an environment limit from a product failure."""
    summary: dict[str, Any] = {"text": entry.summary, "checklist_blob": checklist_blob}
    if entry.reason is not None:
        summary["reason"] = entry.reason
    return {
        "flow_id": entry.flow_id,
        "status": entry.status,
        "head": head,
        "ran_at": ran_at,
        "summary": summary,
        "log_digest": entry.log_digest,
        "run_ref": run_ref(job_id),
        "reporter": f"workflow-controller {version}",
    }


def defect_entries(entries: Iterable[Entry]) -> list[Entry]:
    return [entry for entry in entries if entry.status == STATUS_FAILED and entry.reason == REASON_DEFECT]


def environment_entries(entries: Iterable[Entry]) -> list[Entry]:
    return [entry for entry in entries if entry.status == STATUS_FAILED and entry.reason == REASON_ENVIRONMENT]


def outcome_of(entries: Iterable[Entry]) -> str:
    """The step's outcome for the recorded entries (D7): ``findings`` when any
    flow failed as a ``defect``, ``environment_limited`` when every failure
    is an ``environment`` limit, else ``recorded``."""
    entries = list(entries)
    if defect_entries(entries):
        return FINDINGS
    return ENVIRONMENT_LIMITED if environment_entries(entries) else RECORDED


def _fence_for(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def findings_text(
    entries: Iterable[Entry], flows: Sequence[Flow], *, head: str, checklist_blob: str, job_id: str,
) -> str | None:
    """The text written to the Workflow's ``functional_review_path`` (D6), in
    the shape ``/apply-functional-review`` reads: a findings document naming
    each ``defect`` flow, the tester's summary and the log excerpt, under a
    header that names the tested head, the checklist blob and the run. The
    header makes every run's file different, so identical failure text at two
    heads is not refused as already applied. ``None`` when no flow failed as
    a ``defect`` (``environment`` flows are never findings)."""
    defects = defect_entries(entries)
    if not defects:
        return None
    by_id = {flow.flow_id: flow for flow in flows}
    parts = [
        "# Functional review findings (automated tester)",
        "",
        f"Tested head: {head}",
        f"Checklist blob: {checklist_blob}",
        f"Run: {run_ref(job_id)}",
        "",
        "The Controller's independent tester ran the checklist in an isolated sandbox. Each flow below failed as a "
        "product defect.",
    ]
    for entry in defects:
        flow = by_id.get(entry.flow_id)
        title = (flow.text.splitlines() or [""])[0].strip() if flow else ""
        excerpt = log_excerpt(entry).strip("\n")
        fence = _fence_for(excerpt)
        parts += ["", f"## {entry.flow_id} ({flow.label if flow else '?'}): failed", ""]
        if title:
            parts += [f"Flow: {title}", ""]
        parts += [f"Tester summary: {entry.summary}", "", "Log excerpt:", "", f"{fence}text", excerpt, fence]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# The writable locations (D5, R7-001).
# ---------------------------------------------------------------------------

_GLOB_CHARS = frozenset("*?[\\")


@dataclasses.dataclass(frozen=True)
class RefusedPattern:
    """A ``.gitignore`` line the derivation does not admit as a writable
    location, and why (it is listed for the gate message)."""

    pattern: str
    reason: str


def derive_writable_locations(
    gitignore: str, manifest_paths: Iterable[str],
) -> tuple[tuple[str, ...], tuple[RefusedPattern, ...]]:
    """The directories the sandbox may write inside the clone, from the clone's
    **root** ``.gitignore``: a line is admitted only when it is a glob-free,
    non-negated, single-component directory pattern (one trailing ``/``, no
    other ``/`` but an optional leading one), and is not ``.git`` and does not
    name, hold or lie under a manifest path. Each admitted directory is made at
    the clone root only. Every other line is refused as a writable location
    (it still serves as an ignore rule for the post-run walk), listed once in
    order with the reason. Blank lines and comments are skipped."""
    tracked = [path.rstrip("/") for path in manifest_paths]
    admitted: list[str] = []
    refused: list[RefusedPattern] = []
    seen: set[str] = set()

    def refuse(pattern: str, reason: str) -> None:
        if pattern not in seen:
            seen.add(pattern)
            refused.append(RefusedPattern(pattern, reason))

    for raw in gitignore.split("\n"):
        line = raw.rstrip("\r").rstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            refuse(line, "a negation")
        elif _GLOB_CHARS & set(line):
            refuse(line, "a glob")
        elif not line.endswith("/"):
            refuse(line, "a file pattern, not a directory (no trailing slash)")
        else:
            name = line.removeprefix("/")[:-1]
            if not name or "/" in name:
                refuse(line, "not a single directory at the root")
            elif name in (".git", ".", ".."):
                refuse(line, f"names {name!r}")
            elif any(path == name or path.startswith(name + "/") for path in tracked):
                refuse(line, "names or holds a tracked path")
            elif name not in admitted:
                admitted.append(name)
    return tuple(admitted), tuple(refused)


# ---------------------------------------------------------------------------
# The tester's prompt (D2, D3, D4, D5).
# ---------------------------------------------------------------------------


def sandbox_limits_note() -> str:
    """The short note the row-37 launch prompt carries (D4), so a new checklist
    prefers flows that run offline."""
    return ("The Controller's functional tester runs each flow in a sandbox with these limits: "
            + "; ".join(SANDBOX_LIMITS) + ". Prefer flows that run offline against the fake `gh` and a scratch origin.")


def tester_prompt(
    flows: Sequence[Flow], *, clone_dir: Path, output_dir: Path, head: str, checklist_blob: str,
    writable: Sequence[str], refused: Sequence[RefusedPattern], timeout_seconds: int,
) -> str:
    """The independent tester's prompt (D4): the flows with their ids, the
    exact result format, the sandbox's limits, the writable and refused
    locations, and the rules about copies."""
    lines = [
        "You are an independent functional tester. Run each flow of the checklist below against the repository "
        "clone, judge it, and report the result of every flow.",
        "",
        f"Clone (read-only): {clone_dir}",
        f"Tested head: {head}",
        f"Checklist blob: {checklist_blob}",
        f"Output folder (writable): {output_dir}",
        f"Time limit: {timeout_seconds} seconds for the whole run.",
        "",
        "## Sandbox limits",
        "",
        *[f"- {limit}" for limit in SANDBOX_LIMITS],
        "",
        "Writable locations below the clone root: " + (", ".join(f"{name}/" for name in writable) or "none"),
        "Refused as writable locations (so a flow that writes there is `environment`): "
        + ("; ".join(f"`{item.pattern}` ({item.reason})" for item in refused) or "none"),
        "",
        "## Rules",
        "",
        "- Use only the shell. Write `RESULTS.json` and the logs with shell redirection, inside the output folder.",
        "- Any copy of the clone (for example to build or install) is made from the clone unchanged, with `cp -a`, "
        "under your `TMPDIR`. No flow edits product source, the checklist or anything under `.git`. A copy that "
        "is edited is a tester fault.",
        f"- Never choose flow ids: use exactly the ids below. A flow the sandbox cannot run is `failed` with "
        f"`reason` `{REASON_ENVIRONMENT}`, never `{REASON_DEFECT}`, and its summary names what was missing.",
        "",
        "## Result format",
        "",
        f"Write `{RESULTS_FILE}` in the output folder: a JSON list with at most one entry per flow:",
        '`{"flow_id": "flow-1", "status": "passed" | "failed", "summary": "...", "log": "flow-1.log"}`.',
        f"`log` is the name of a file you wrote in the output folder, never a symlink. A `failed` entry also "
        f"carries `reason`: `{REASON_DEFECT}` (the product did not behave as the flow expects) or "
        f"`{REASON_ENVIRONMENT}` (the flow could not be run or judged in the sandbox). A `passed` entry carries "
        f"no `reason`.",
        "",
        "## Flows",
    ]
    for flow in flows:
        lines += ["", f"### {flow.flow_id} (checklist label {flow.label})", "", flow.text]
    return "\n".join(lines) + "\n"


def flow_ids(flows: Iterable[Flow]) -> tuple[str, ...]:
    return tuple(flow.flow_id for flow in flows)


def as_mapping(entry: Entry) -> Mapping[str, Any]:
    """An entry as a plain mapping (job records and ``explain``)."""
    return {
        "flow_id": entry.flow_id, "status": entry.status, "reason": entry.reason, "summary": entry.summary,
        "log": entry.log_path.name, "log_digest": entry.log_digest,
    }


# ---------------------------------------------------------------------------
# The enforcement the tester is launched under (CP2, D2, D5 I2).
# ---------------------------------------------------------------------------

#: The tester's whole tool set (D2): the shell only. Claude's sandbox covers
#: shell commands, so a file tool would run outside it.
TESTER_TOOLS: tuple[str, ...] = ("Bash",)

#: The system programs the Claude sandbox needs on Linux; one missing ends the
#: launch at ``functional_tester_unavailable`` (``sandbox.failIfUnavailable``
#: makes the CLI itself refuse, with :data:`UNAVAILABLE_MARKER` in its result).
REQUIRED_PROGRAMS: tuple[str, ...] = ("bwrap", "socat")
UNAVAILABLE_MARKER = "sandbox required but unavailable"  # matched case-insensitively


class TesterUnavailable(ControllerError):
    """The enforcement the tester needs is missing or does not enforce (D2,
    D7): the launch ends here, never at a weaker run."""

    code = GATE_TESTER_UNAVAILABLE


def tester_settings(
    *, clone_dir: Path, output_dir: Path, scratch_dirs: Sequence[Path], writable: Sequence[str],
) -> dict[str, Any]:
    """The ``--settings`` document of the tester's launch.

    The sandbox is required (``failIfUnavailable``), has no unsandboxed retry
    and no excluded command, no allowed domain and no allowed Unix socket. The
    write allowlist is the output folder, the scratch directories and the
    derived writable locations (D5), and the deny list covers the **clone root
    recursively** and, by name, its ``.git`` (I2): on the pinned CLI a more
    specific allow below the denied root wins, so a writable location stays
    writable while every tracked path, every new file beside one, the checklist
    and ``.git`` are refused (proved live in CP2). The run's working directory
    must be ``output_dir``: with a working directory above the clone, the
    CLI's own working-directory grant outranks the allow and the writable
    locations become unwritable too."""
    allow = [str(output_dir), *(str(path) for path in scratch_dirs), *(str(clone_dir / name) for name in writable)]
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "excludedCommands": [],
            "filesystem": {"allowWrite": allow, "denyWrite": [str(clone_dir), str(clone_dir / ".git")]},
            "network": {"allowedDomains": [], "allowUnixSockets": []},
        },
    }


def tester_flags(settings_path: Path) -> list[str]:
    """The launch flags that carry the closed tool set and the settings: only
    ``Bash`` exists and only ``Bash`` is allowed; a prompt is never answered."""
    tools = ",".join(TESTER_TOOLS)
    return ["--tools", tools, "--allowedTools", tools, "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--settings", str(settings_path)]


def static_probe(*, which: Callable[[str], str | None] = shutil.which, run: Callable[..., Any] = subprocess.run) -> None:
    """The part of the launch probe that needs no model: every program of
    :data:`REQUIRED_PROGRAMS` is on ``PATH`` and ``bwrap`` can make an
    unprivileged namespace. Raises :class:`TesterUnavailable` naming what is
    missing."""
    missing = [name for name in REQUIRED_PROGRAMS if which(name) is None]
    if missing:
        raise TesterUnavailable(f"the sandbox needs {', '.join(missing)} on PATH, which is not installed",
                                evidence={"missing": missing})
    try:
        result = run(["bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"], capture_output=True, timeout=30,
                     check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TesterUnavailable(f"bwrap cannot be run: {exc}") from exc
    if result.returncode != 0:
        raise TesterUnavailable("bwrap cannot make an unprivileged namespace here: "
                                + result.stderr.decode("utf-8", "replace").strip()[:300])


def result_event_text(event: Mapping[str, Any]) -> str:
    """Everything a Claude ``result`` event says: its ``result`` text and its
    ``errors`` list (a refusal to start is reported only in the latter)."""
    errors = event.get("errors")
    parts = [str(event.get("result") or "")]
    if isinstance(errors, list):
        parts += [item for item in errors if isinstance(item, str)]
    return "\n".join(part for part in parts if part)


def launch_refusal(result_text: str) -> TesterUnavailable | None:
    """The refusal for a Claude result whose text says the required sandbox is
    unavailable (the CLI refused to start without it), else ``None``."""
    if UNAVAILABLE_MARKER in result_text.lower():
        return TesterUnavailable("Claude refused to start the tester without its sandbox: "
                                 + result_text.strip().splitlines()[0][:300])
    return None


@dataclasses.dataclass(frozen=True)
class ProbeTargets:
    """The locations the probe command touches. ``tracked`` and ``checklist``
    are existing files of the clone, ``writable`` a pre-created writable
    directory below the clone root, ``stand_in_home`` a path in a stand-in home
    or config location, ``socket_path`` a Unix socket the host listens on and
    ``tcp_port`` a TCP port the host listens on."""

    clone_dir: Path
    output_dir: Path
    tracked: str
    checklist: str
    writable: str
    stand_in_home: Path
    socket_path: Path
    tcp_port: int


#: What each probe line must say. ``shm`` is not listed: a write to ``/dev/shm``
#: lands in the sandbox's private mount, so its line is informational and the
#: caller asserts the host path stays absent.
PROBE_EXPECTED: Mapping[str, str] = {
    "tracked": "REFUSED", "checklist": "REFUSED", "git": "REFUSED", "newfile": "REFUSED", "rename": "REFUSED",
    "delete": "REFUSED", "writable": "WROTE", "output": "WROTE", "home": "REFUSED", "tmp": "REFUSED",
    "vartmp": "REFUSED", "tcp": "BLOCKED", "unix": "BLOCKED",
}
_PROBE_LINE_RE = re.compile(r"^PROBE (?P<name>[a-z]+) (?P<result>[A-Z]+)$", re.MULTILINE)


def probe_command(targets: ProbeTargets, *, marker: str) -> str:
    """The one shell command the probe turn runs (D10): by absolute path, a
    write to a tracked file, the checklist, a file in ``.git``, a new file
    beside a tracked one, a rename over and a deletion of a tracked file, a
    write to the writable location and to the output folder, a write to a
    stand-in home and to ``/tmp`` and ``/var/tmp``, a TCP connection and a
    connection to a host Unix socket. Every line is ``PROBE <name> <result>``."""
    clone, out = targets.clone_dir, targets.output_dir
    q = shlex.quote
    py_tcp = f"import socket;s=socket.create_connection(('127.0.0.1',{targets.tcp_port}),3)"
    py_unix = f"import socket;s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect({str(targets.socket_path)!r})"
    return "\n".join([
        'w() { if ( : > "$2" ) 2>/dev/null; then echo "PROBE $1 WROTE"; else echo "PROBE $1 REFUSED"; fi; }',
        f"w tracked {q(str(clone / targets.tracked))}",
        f"w checklist {q(str(clone / targets.checklist))}",
        f"w git {q(str(clone / '.git' / 'config'))}",
        f"w newfile {q(str(clone / 'probe-new-file'))}",
        f"w writable {q(str(clone / targets.writable / 'probe'))}",
        f"w output {q(str(out / 'probe'))}",
        f"w home {q(str(targets.stand_in_home / '.config' / 'probe'))}",
        f"w tmp {q('/tmp/' + marker)}",
        f"w vartmp {q('/var/tmp/' + marker)}",
        f"w shm {q('/dev/shm/' + marker)}",
        f"echo x > {q(str(out / 'rename-src'))}",
        f"if mv {q(str(out / 'rename-src'))} {q(str(clone / targets.tracked))} 2>/dev/null; "
        f'then echo "PROBE rename WROTE"; else echo "PROBE rename REFUSED"; fi',
        f"if rm -f {q(str(clone / targets.tracked))} 2>/dev/null && [ ! -e {q(str(clone / targets.tracked))} ]; "
        f'then echo "PROBE delete WROTE"; else echo "PROBE delete REFUSED"; fi',
        f'if python3 -c {q(py_tcp)} 2>/dev/null; then echo "PROBE tcp CONNECTED"; else echo "PROBE tcp BLOCKED"; fi',
        f'if python3 -c {q(py_unix)} 2>/dev/null; then echo "PROBE unix CONNECTED"; else echo "PROBE unix BLOCKED"; fi',
    ])


def probe_prompt(command: str) -> str:
    return ("Run exactly the following shell command once with the Bash tool, then reply with its complete output and "
            "nothing else. Do not change the command, do not retry it, and do not run anything else.\n\n"
            f"```sh\n{command}\n```\n")


def judge_probe(output: str) -> list[str]:
    """The failures of a probe's output: a line missing, repeated or not the
    expected result. Empty means the boundary held. Anything unreadable is a
    failure (fail closed)."""
    seen: dict[str, str] = {}
    failures: list[str] = []
    for match in _PROBE_LINE_RE.finditer(output):
        name, result = match["name"], match["result"]
        if name in seen and seen[name] != result:
            failures.append(f"{name}: reported both {seen[name]} and {result}")
        seen[name] = result
    for name, want in PROBE_EXPECTED.items():
        got = seen.get(name)
        if got is None:
            failures.append(f"{name}: no result line")
        elif got != want:
            failures.append(f"{name}: {got}, expected {want}")
    return failures
