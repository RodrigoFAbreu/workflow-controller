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
  :func:`probe_command`, :func:`judge_probe`);
- the sandbox of D3 and the binding of D5 (``CP3``): the candidate captured
  from the real repository (:func:`capture_candidate`), the sandbox built from
  it (:func:`build_sandbox`), the walk that binds the clone to the candidate
  after a run (:func:`check_bound`) and the fakes' logs
  (:func:`read_fake_calls`). The run itself is ``review.launch_functional``.

Apart from the sandbox it builds under the runtime root (through
``controller.runtime``), it writes nothing: the Controller-written logs of a
"not run" entry are returned as bytes for the caller to publish.
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
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from controller import runtime, workflow_contract
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
    "a writable location in the clone is a link to a folder outside it: a tool that deletes and recreates the "
    "linked folder (`shutil.rmtree('build')`, `rm -rf build`) is refused on the link, a sandbox limit "
    "(`environment`), not a product `defect`",
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
#: A bold paragraph after a blank line ends a flow only when it opens one of the
#: checklist's own non-flow sections; any other label (``Expected``, ``Verify``,
#: ``Check``...) belongs to the flow, so nothing is dropped silently.
_SECTION_END_RE = re.compile(
    r"^\*\*[ \t]*(?:" + "|".join(re.escape(word) for word in (*NEVER_FLOWS, "out of scope"))
    # the section word is the whole bold label (``**Setup**``, ``**Out of scope.**``), not a sentence prefix
    + r")[ \t]*(?:[:.]|\*\*)"
    # or a flow header: one qualifier word, a comma, then the label (``**Optional, K. Title.**``)
    rf"|^\*\*[ \t]*[A-Za-z]+,[ \t]*{_LABEL}[.)][ \t]", re.IGNORECASE)
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
        # A flow runs to the next flow, heading or bold paragraph: a blank line
        # does not end it, since a table or a list of cases may follow one. A
        # bold paragraph stays in the flow unless it opens a non-flow section.
        end = index + 1
        while end < len(lines):
            if not mask[end] and (_BOLD_RE.match(lines[end]) or _heading(lines[end], False)
                                  or (lines[end].startswith("**") and not lines[end - 1].strip()
                                      and _SECTION_END_RE.match(lines[end]))):
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
    log_dir: Path, flows: Sequence[Flow], entries: Sequence[Entry], *, timed_out: bool, job_id: str,
    timeout_seconds: int | None = None,
) -> list[Entry]:
    """One entry per flow, in checklist order. A flow the tester left out is
    a ``failed`` ``environment`` entry (not a product defect, O1) with a
    Controller-written summary naming the cause and a Controller-written log
    whose sha256 is a valid ``log_digest``, so it is recorded rather than
    refused as ``evidence_malformed``. ``log_dir`` is where the Controller
    writes those logs: a folder the tester cannot write (never the output
    folder, where a planted symlink would steer the write, D2/O2)."""
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
            log_path=log_dir / not_run_log_name(flow.flow_id), log_digest=sha256_bytes(content),
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
        "Writable locations in the clone root, each a link to a folder outside the clone: "
        + (", ".join(f"{name}/" for name in writable) or "none"),
        "Refused as writable locations (so a flow that writes there is `environment`): "
        + ("; ".join(f"`{item.pattern}` ({item.reason})" for item in refused) or "none"),
        "",
        "## Rules",
        "",
        "- Use only the shell. Write `RESULTS.json` and the logs with shell redirection, inside the output folder.",
        "- Any copy of the clone (for example to build or install) is made from the clone unchanged, with `cp -a`, "
        "under your `TMPDIR`. A writable location in the clone is a link, and a copy of it would share the "
        "clone's folder: right after `cp -a <clone> <copy>`, replace each of them in the copy with an empty "
        "directory that holds what the clone's folder holds, `rm <copy>/<name> && mkdir <copy>/<name> && "
        "cp -a <clone>/<name>/. <copy>/<name>/`, so two copies never meet in one folder. No flow edits product "
        "source, the checklist or anything under `.git`. A copy that is edited, except by that step, is a tester "
        "fault.",
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


def assert_writes_not_below_denies(filesystem: Mapping[str, Any]) -> None:
    """The layout invariant of D1: no ``allowWrite`` entry equals or lies below a
    ``denyWrite`` entry, so the document means the same whether a build lets a
    more specific allow win over the deny above it (Claude Code 2.1.303) or
    lets the deny above win (2.1.304 and later). A ``ValueError``: a document
    that breaks it is a programming error, never a runtime fallback."""
    denies = [Path(entry) for entry in filesystem.get("denyWrite", [])]
    for entry in filesystem.get("allowWrite", []):
        # Lexically and as the file system resolves it: a symlinked parent (or a
        # ``..`` after one) can put an allowance below a denial it does not
        # name. ``realpath`` of the raw entry, so ``..`` follows the links.
        for allowed, denied in ((Path(os.path.abspath(entry)), [Path(os.path.abspath(d)) for d in denies]),
                                (Path(os.path.realpath(entry)), [Path(os.path.realpath(d)) for d in denies])):
            for blocked in denied:
                if allowed == blocked or blocked in allowed.parents:
                    raise ValueError(f"the write allowance {entry} equals or lies below the denied path {blocked}")


def tester_settings(
    *, clone_dir: Path, output_dir: Path, scratch_dirs: Sequence[Path], writable_dir: Path,
    writable: Sequence[str],
) -> dict[str, Any]:
    """The ``--settings`` document of the tester's launch.

    The sandbox is required (``failIfUnavailable``), has no unsandboxed retry
    and no excluded command, no allowed domain and no allowed Unix socket. The
    write allowlist is the output folder, the scratch directories and the
    external folders of the derived writable locations ``<writable_dir>/<name>``
    (D2: the clone holds a link to each, so the allow list never names a path
    below the clone). The deny list covers the **clone root recursively** and,
    by name, its ``.git`` (I2): every tracked path, every new file beside one,
    the checklist and ``.git`` are refused, and no allowance lies below a
    denial (D1, :func:`assert_writes_not_below_denies`), so the document does not
    depend on which precedence the build implements. The run's working
    directory must be ``output_dir``: with a working directory above the clone,
    the CLI's own working-directory grant outranks the allow and the writable
    locations become unwritable too (pinned release-side by
    ``test_a_launch_from_above_the_clone_cannot_write_through_a_link_either``)."""
    allow = [str(output_dir), *(str(path) for path in scratch_dirs), *(str(writable_dir / name) for name in writable)]
    filesystem = {"allowWrite": allow, "denyWrite": [str(clone_dir), str(clone_dir / ".git")]}
    assert_writes_not_below_denies(filesystem)
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "excludedCommands": [],
            "filesystem": filesystem,
            "network": {"allowedDomains": [], "allowUnixSockets": []},
        },
    }


def make_writable_links(clone: Path, writable_dir: Path, names: Iterable[str]) -> None:
    """Make ``<writable_dir>/<name>/`` for each writable location and, in the
    clone, a symlink ``<clone>/<name>`` to it (an absolute target), so a flow
    writes ``build/x`` relative to the clone and the write lands outside the
    denied clone (D2)."""
    for name in names:
        target = writable_dir / name
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(target, clone / name)


def exclude_writable_links(runtime_root: Path, git_dir_rel: str, names: Iterable[str]) -> None:
    """One ``/<name>`` line per link in ``<git_dir>/info/exclude`` (no trailing
    slash, so a link matches; ``name/`` matches only a directory), so the
    clone's ``git status`` is empty exactly as with empty directories (D2).
    ``git_dir_rel`` is the clone's ``.git`` relative to the runtime root."""
    exclude = runtime_root / git_dir_rel / "info" / "exclude"
    kept = exclude.read_bytes() if exclude.exists() else b""
    if kept and not kept.endswith(b"\n"):
        kept += b"\n"
    lines = "".join(f"/{name}\n" for name in names).encode()
    runtime.write_bytes(runtime_root, f"{git_dir_rel}/info/exclude", kept + lines)


def tester_flags(settings_path: Path) -> list[str]:
    """The launch flags that carry the closed tool set and the settings: only
    ``Bash`` exists and only ``Bash`` is allowed; a prompt is never answered."""
    tools = ",".join(TESTER_TOOLS)
    return ["--tools", tools, "--allowedTools", tools, "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--settings", str(settings_path)]


_SECCOMP_PROGRAM = (
    "import ctypes,struct,sys\n"
    "libc=ctypes.CDLL(None,use_errno=True)\n"
    "libc.prctl.argtypes=[ctypes.c_int]+[ctypes.c_ulong]*4\n"
    "if libc.prctl(38,1,0,0,0)!=0: sys.exit(2)\n"
    "insn=ctypes.create_string_buffer(struct.pack('HBBI',6,0,0,0x7fff0000),8)\n"
    "prog=ctypes.create_string_buffer(struct.pack('HxxxxxxP',1,ctypes.addressof(insn)),16)\n"
    "sys.exit(0 if libc.prctl(22,2,ctypes.addressof(prog),0,0)==0 else 3)\n"
)


#: SHA-256 digests of the native Claude builds on which the enforcement spike
#: (CP2) proved the vendored seccomp helper blocks Unix sockets, by version. A
#: build is admitted only by identity: a name found inside the file does not
#: show the helper is bundled (a package CLI can carry the text without the
#: executable), so an unknown build ends the launch before it starts.
VERIFIED_NATIVE_BUILDS: dict[str, str] = {
    "2.1.303": "375988f457756524234695cf2d8f64ace99c5f68682e47ea24862a843c191450",
}


def _is_native_executable(path: str, *, verified: Mapping[str, str] | None = None,
                          recorded: Iterable[str] = ()) -> bool:
    """Whether the file ``path`` resolves to is a verified native Claude build:
    an ELF binary whose SHA-256 digest is one of :data:`VERIFIED_NATIVE_BUILDS`
    (or ``verified``) or one of the ``recorded`` digests of the Controller's
    runtime root (``claude_build``). The digest is taken from the file itself,
    the one the launch will execute, so a launcher, a script, a package CLI
    bundle or a newer unverified build is refused (I1)."""
    digests = set((VERIFIED_NATIVE_BUILDS if verified is None else verified).values()) | set(recorded)
    digest = hashlib.sha256()
    try:
        with open(os.path.realpath(path), "rb") as handle:
            if handle.read(4) != b"\x7fELF":
                return False
            handle.seek(0)
            while chunk := handle.read(1 << 22):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() in digests


def host_probe(*, which: Callable[[str], str | None] = shutil.which, run: Callable[..., Any] = subprocess.run) -> None:
    """The host part of the launch probe, which needs no model and does not look
    at ``claude``: every program of :data:`REQUIRED_PROGRAMS` is on ``PATH``,
    ``bwrap`` can make an unprivileged namespace and the kernel accepts a
    seccomp filter. The CLI itself only warns when its seccomp helper is missing
    and then runs without Unix-socket blocking, so ``failIfUnavailable`` cannot
    be relied on for that component (D2). Raises :class:`TesterUnavailable`
    naming what is missing."""
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
    try:
        result = run([sys.executable, "-I", "-c", _SECCOMP_PROGRAM], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TesterUnavailable(f"the seccomp filter check cannot be run: {exc}") from exc
    if result.returncode != 0:
        raise TesterUnavailable("the kernel does not accept a seccomp filter here, so the sandbox cannot block "
                                "Unix sockets")


def identity_check(claude: str | None, *, is_native: Callable[[str], bool] | None = None,
                   recorded: Iterable[str] = (), record_note: str | None = None) -> None:
    """The identity part of the launch probe: ``claude`` is an ELF whose digest is
    built in or recorded. The refusal names the remedy (``verify-claude``), the
    digest of the file and, when a record exists that could not be trusted, that
    it was ignored (D6)."""
    admitted = is_native if is_native is not None else (lambda path: _is_native_executable(path, recorded=recorded))
    if claude is not None and admitted(claude):
        return
    digest = None
    if claude is not None:
        try:
            hasher = hashlib.sha256()
            with open(os.path.realpath(claude), "rb") as handle:
                while chunk := handle.read(1 << 22):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        except OSError:
            digest = None
    text = ("the sandbox's Unix-socket blocking needs a verified native claude build (built in: "
            f"{', '.join(VERIFIED_NATIVE_BUILDS)}); {claude or 'claude'}"
            + (f" (sha256 {digest})" if digest else "")
            + " is not one. Run `workflow-controller verify-claude` to check this build and admit it")
    if record_note:
        text += f". Note: {record_note}"
    raise TesterUnavailable(text, evidence={"claude": claude, "sha256": digest})


def static_probe(*, which: Callable[[str], str | None] = shutil.which, run: Callable[..., Any] = subprocess.run,
                 is_native: Callable[[str], bool] | None = None, recorded: Iterable[str] = (),
                 record_note: str | None = None, claude: str | None = None) -> None:
    """The part of the launch probe that needs no model: :func:`host_probe`, then
    :func:`identity_check` of ``claude`` (the binary the launch will execute;
    ``which("claude")`` when not given)."""
    host_probe(which=which, run=run)
    identity_check(claude if claude is not None else which("claude"), is_native=is_native, recorded=recorded,
                   record_note=record_note)


def result_event_text(event: Mapping[str, Any]) -> str:
    """Everything a Claude ``result`` event says: its ``result`` text and its
    ``errors`` list (a refusal to start is reported only in the latter)."""
    errors = event.get("errors")
    parts = [str(event.get("result") or "")]
    if isinstance(errors, list):
        parts += [item for item in errors if isinstance(item, str)]
    return "\n".join(part for part in parts if part)


#: The most of the tester's final message kept in the job record and the stop text.
MAX_TESTER_MESSAGE = 600


def tester_message_excerpt(text: str) -> str:
    """A bounded excerpt of the tester's final message (its ``result`` text):
    whitespace collapsed, cut to :data:`MAX_TESTER_MESSAGE` characters."""
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_TESTER_MESSAGE else flat[:MAX_TESTER_MESSAGE - 3] + "..."


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
    are existing files of the clone, ``writable`` the name of a writable
    location (the clone holds a link to its external folder), ``stand_in_home`` a path in a stand-in home
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
    "delete": "REFUSED", "writable": "WROTE", "link": "REFUSED", "output": "WROTE", "home": "REFUSED", "tmp": "REFUSED",
    "vartmp": "REFUSED", "tcp": "BLOCKED", "unix": "BLOCKED",
}
_PROBE_LINE_RE = re.compile(r"^PROBE (?P<name>[a-z]+) (?P<result>[A-Z]+)$", re.MULTILINE)


def probe_command(targets: ProbeTargets, *, marker: str) -> str:
    """The one shell command the probe turn runs (D10): by absolute path, a
    write to a tracked file, the checklist, a file in ``.git``, a new file
    beside a tracked one, a rename over and a deletion of a tracked file, a
    write to the writable location (through its link) and an attempt to replace
    that link by a directory, a write to the output folder, a write to a
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
        f"if rm -f {q(str(clone / targets.writable))} 2>/dev/null && mkdir {q(str(clone / targets.writable))} 2>/dev/null; "
        f'then echo "PROBE link WROTE"; else echo "PROBE link REFUSED"; fi',
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


# ---------------------------------------------------------------------------
# The sandbox and the binding of the tested bytes (CP3, D3, D5).
# ---------------------------------------------------------------------------

#: The sandbox lives at ``<runtime root>/functional/<job id>/``.
SANDBOX_AREA = "functional"
#: The checklist's file, relative to the repository root.
CHECKLIST_PATH = "docs/ACTIVE_MILESTONE.md"
#: The system directories after the sandbox's ``bin/`` on the tester's ``PATH``.
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
#: The stand-ins in ``bin/`` (``controller.sandbox_fakes``).
FAKE_PROGRAMS: tuple[str, ...] = ("gh", "claude", "codex", "ssh")
#: Files that steer Git; an unlisted one is an extra file wherever it is (D5).
_STEERING_NAMES = frozenset({".gitignore", ".gitattributes"})
#: The cause of each ``functional_tester_unavailable`` (D7).
UNAVAILABLE_NO_BINARY = "no_binary"
UNAVAILABLE_ROLE_REFUSED = "role_refused"
UNAVAILABLE_ENFORCEMENT = "enforcement"
UNAVAILABLE_CLONE_MISMATCH = "clone_mismatch"
#: A walk reports at most this many differences.
MAX_DIFFERENCES = 50
_LOOSE_OBJECT_RE = re.compile(r"^objects/[0-9a-f]{2}/[0-9a-f]{38,62}$")


def unavailable(cause: str, message: str, **evidence: Any) -> TesterUnavailable:
    return TesterUnavailable(message, evidence={"cause": cause, **evidence})


@dataclasses.dataclass(frozen=True)
class ManifestEntry:
    """One tracked path of the candidate: ``mode`` as Git states it, the
    ``size`` and ``digest`` (sha256) of the blob, for a symlink its ``target``
    (and the digest of the target text), and the Git ``blob`` id."""

    mode: str
    size: int
    digest: str
    blob: str
    target: str | None = None


@dataclasses.dataclass(frozen=True)
class Candidate:
    """What the tester is handed (D5): the head, the manifest of every tracked
    path read from the **real** repository's tree, the root ``.gitignore`` and
    the checklist's blob id. Built before the run; nothing in the clone is
    consulted for it."""

    repo_root: Path
    head: str
    branch: str
    manifest: Mapping[str, ManifestEntry]
    root_gitignore: str
    checklist_path: str
    checklist_blob: str

    def read_text(self, rel: str) -> str | None:
        """The text of the tracked file ``rel`` at the head (``None`` for no
        such file or one that is not UTF-8)."""
        entry = self.manifest.get(rel)
        if entry is None or entry.target is not None:
            return None
        data = _git(self.repo_root, ["cat-file", "blob", entry.blob])
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None


def _git_environment() -> dict[str, str]:
    """The environment of every Git command the Controller runs for the
    sandbox: no system or global configuration, no prompt, only local
    transport, and the command-scope entries of the query's Git (hooks and
    fsmonitor off, ``workflow_contract._GIT_PINNED_CONFIG``, not a second
    list)."""
    base = {"PATH": SYSTEM_PATH, "HOME": "/nonexistent", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
            "GIT_OPTIONAL_LOCKS": "0"}
    return workflow_contract._with_git_config(base, workflow_contract._GIT_PINNED_CONFIG, {})


def _git(cwd: Path, args: Sequence[str], *, stdin: bytes | None = None, env: Mapping[str, str] | None = None) -> bytes:
    argv = ["git", *args]
    try:
        done = subprocess.run(argv, cwd=cwd, env=dict(env or _git_environment()), input=stdin,
                              capture_output=True, check=False, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise unavailable(UNAVAILABLE_ENFORCEMENT, f"git could not be run for the sandbox: {exc}") from exc
    if done.returncode != 0:
        raise unavailable(UNAVAILABLE_ENFORCEMENT,
                          f"`git {' '.join(args[:3])}` failed for the sandbox: "
                          + done.stderr.decode("utf-8", "replace").strip()[:300])
    return done.stdout


def _read_batch(raw: bytes, count: int) -> list[bytes]:
    """The contents ``git cat-file --batch`` printed, in request order."""
    out: list[bytes] = []
    position = 0
    for _ in range(count):
        newline = raw.index(b"\n", position)
        header = raw[position:newline].split()
        size = int(header[2])
        out.append(raw[newline + 1:newline + 1 + size])
        position = newline + 1 + size + 1
    return out


def capture_candidate(repo_root: Path, head: str, *, branch: str | None = None,
                      checklist_path: str = CHECKLIST_PATH) -> Candidate:
    """Capture the candidate from the real repository's tree at ``head``: a
    ``git ls-tree -r`` and a ``cat-file --batch`` under the hardened
    environment, the sha256 of every blob (of the target text for a symlink),
    the root ``.gitignore`` and the checklist blob, which is compared with the
    blob ``git rev-parse <head>:<checklist>`` names. A submodule (a gitlink)
    is not supported."""
    listing = _git(repo_root, ["ls-tree", "-r", "-z", "--full-tree", head])
    rows: list[tuple[str, str, str]] = []
    for record in filter(None, listing.split(b"\0")):
        meta, _, name = record.partition(b"\t")
        mode, kind, blob = meta.decode().split()
        if kind != "blob":
            raise unavailable(UNAVAILABLE_ENFORCEMENT, f"the tree holds {kind} entry {name.decode('utf-8', 'replace')!r}"
                              ", which the sandbox does not support (a submodule)")
        rows.append((mode, blob, os.fsdecode(name)))
    contents = _read_batch(_git(repo_root, ["cat-file", "--batch"],
                                stdin="".join(f"{blob}\n" for _, blob, _ in rows).encode()), len(rows)) if rows else []
    manifest: dict[str, ManifestEntry] = {}
    for (mode, blob, name), data in zip(rows, contents):
        target = os.fsdecode(data) if mode == "120000" else None
        manifest[name] = ManifestEntry(mode, len(data), sha256_bytes(data), blob, target)
    entry = manifest.get(checklist_path)
    if entry is None:
        raise unavailable(UNAVAILABLE_ENFORCEMENT, f"{checklist_path} is not tracked at {head}")
    named = _git(repo_root, ["rev-parse", f"{head}:{checklist_path}"]).decode().strip()
    if named != entry.blob:
        raise unavailable(UNAVAILABLE_ENFORCEMENT, f"the manifest's checklist blob {entry.blob} is not {named}")
    gitignore = ""
    if ".gitignore" in manifest:
        gitignore = _read_batch(_git(repo_root, ["cat-file", "--batch"], stdin=f"{manifest['.gitignore'].blob}\n".encode()),
                                1)[0].decode("utf-8", "replace")
    return Candidate(repo_root, head, branch or "functional-test", manifest, gitignore, checklist_path, entry.blob)


def candidate_flows(candidate: Candidate) -> list[Flow]:
    """The flows of the candidate's checklist (D4), read from the real
    repository's blob; a link inside the section is followed to a tracked file
    below the repository root only."""
    text = candidate.read_text(candidate.checklist_path)
    if text is None:
        raise ChecklistUnrecognised(f"{candidate.checklist_path} cannot be read at {candidate.head}",
                                    evidence={"section": SECTION_TITLE, "reason": "unreadable"})

    def read_file(link: str) -> str | None:
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(candidate.checklist_path), link.partition("#")[0]))
        if posixpath.isabs(joined) or joined == ".." or joined.startswith("../"):
            return None
        return candidate.read_text(joined)

    return read_flows(text, read_file=read_file)


# --- walking a tree without trusting it ---------------------------------------


def _scan(root: Path, *, skip_top: str | None = None) -> dict[str, tuple]:
    """Every entry below ``root`` as ``path -> descriptor``, by ``lstat`` and
    without following a symlink: ``("file", executable, size, sha256)``,
    ``("link", target)`` or ``("special",)`` (a socket, FIFO or device).
    Directories are traversed and not listed. ``skip_top`` names a top-level
    entry that is left out."""
    found: dict[str, tuple] = {}
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, rel = stack.pop()
        try:
            with os.scandir(directory) as scanned:
                listing = sorted(scanned, key=lambda e: e.name)
        except OSError as exc:
            found[rel or "."] = ("unreadable", str(exc))
            continue
        for item in listing:
            if not rel and item.name == skip_top:
                continue
            child = f"{rel}/{item.name}" if rel else item.name
            mode = item.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                found[child] = ("link", os.readlink(item.path))
            elif stat.S_ISDIR(mode):
                stack.append((Path(item.path), child))
            elif stat.S_ISREG(mode):
                try:
                    digest = sha256_file(Path(item.path))
                except (OSError, ValueError):
                    found[child] = ("special",)
                    continue
                found[child] = ("file", bool(mode & 0o111), item.stat(follow_symlinks=False).st_size, digest)
            else:
                found[child] = ("special",)
    return found


def _describe(entry: ManifestEntry) -> tuple:
    if entry.target is not None:
        return ("link", entry.target)
    return ("file", entry.mode == "100755", entry.size, entry.digest)


def _ignored_paths(clone: Path, git_dir: Path, paths: Sequence[str]) -> set[str]:
    """Which of ``paths`` the clone's tracked ``.gitignore`` files ignore,
    asked of Git run on the Controller-owned ``git_dir`` (a scratch bare
    repository, never the clone's) with the clone as the work tree and a
    private index: none of the clone's own configuration, index, exclude file
    or attributes is read."""
    if not paths:
        return set()
    env = {**_git_environment(), "GIT_INDEX_FILE": str(git_dir / "private-index")}
    argv = ["git", f"--git-dir={git_dir}", f"--work-tree={clone}", "-c", f"core.excludesFile={os.devnull}",
            "check-ignore", "--no-index", "-z", "--stdin"]
    done = subprocess.run(argv, cwd=git_dir, env=env, input=b"\0".join(os.fsencode(p) for p in paths) + b"\0",
                          capture_output=True, check=False, timeout=600)
    if done.returncode not in (0, 1):
        raise unavailable(UNAVAILABLE_ENFORCEMENT, "git check-ignore failed on the Controller's own git dir: "
                          + done.stderr.decode("utf-8", "replace").strip()[:300])
    return {os.fsdecode(item) for item in done.stdout.split(b"\0") if item}


def walk_differences(clone: Path, candidate: Candidate, git_dir: Path,
                     links: Mapping[str, Path] | None = None) -> list[str]:
    """How the clone's worktree differs from the candidate (D5): a tracked
    path missing or different in mode, size or sha256 (a symlink by its
    target), a socket, FIFO or device anywhere, an unlisted ``.gitignore`` or
    ``.gitattributes`` file, and any extra file the candidate's ignore rules do
    not ignore. The clone's ``.git`` is not part of this walk. Nothing in the
    clone is opened but regular files for hashing, and no Git runs in it.

    ``links`` maps each writable location to its external folder (D3): the
    Controller's own link ``<clone>/<name>`` to that folder is accepted and
    nothing else is; a missing, retargeted or replaced link is a difference,
    and any other symlink is an extra file as before."""
    seen = _scan(clone, skip_top=".git")
    differences: list[str] = []
    for name, target in sorted((links or {}).items()):
        seen.pop(name, None)
        try:
            intact = stat.S_ISLNK(os.lstat(clone / name).st_mode) and os.readlink(clone / name) == str(target)
        except OSError:
            intact = False
        if not intact:
            differences.append(f"link changed: {name}")
    for path, entry in sorted(candidate.manifest.items()):
        have = seen.pop(path, None)
        if have is None:
            differences.append(f"missing: {path}")
        elif have != _describe(entry):
            differences.append(f"differs: {path}")
    extras: list[str] = []
    for path, have in sorted(seen.items()):
        if have[0] in ("special", "unreadable"):
            differences.append(f"{'special file' if have[0] == 'special' else 'unreadable'}: {path}")
        elif posixpath.basename(path) in _STEERING_NAMES:
            differences.append(f"unlisted {posixpath.basename(path)}: {path}")
        else:
            extras.append(path)
    ignored = _ignored_paths(clone, git_dir, extras)
    differences += [f"extra file: {path}" for path in extras if path not in ignored]
    return differences[:MAX_DIFFERENCES]


def _git_dir_state(git_dir: Path) -> dict[str, tuple]:
    try:
        if not stat.S_ISDIR(os.lstat(git_dir).st_mode):
            return {".": ("not-a-directory",)}
    except OSError as exc:
        return {".": ("unreadable", str(exc))}
    return _scan(git_dir)


def refs_of(git_dir: Path) -> dict[str, str]:
    """The clone's ``HEAD``, ``packed-refs`` and every file under ``refs/`` as
    plain text (no Git command, D5)."""
    found: dict[str, str] = {}
    for name in ("HEAD", "packed-refs"):
        try:
            found[name] = sha256_file(git_dir / name)
        except (OSError, ValueError):
            pass
    for path, entry in _scan(git_dir / "refs").items():
        found[f"refs/{path}"] = entry[3] if entry[0] == "file" else repr(entry)
    return found


def _git_dir_allowed(path: str, before: tuple | None, after: tuple | None) -> bool:
    if path == "index" or path == "FETCH_HEAD" or path.startswith("logs/"):
        return after is None or after[0] == "file"
    return before is None and after is not None and after[0] == "file" and bool(_LOOSE_OBJECT_RE.match(path))


def git_dir_differences(before: Mapping[str, tuple], after: Mapping[str, tuple]) -> list[str]:
    """Changes under the clone's ``.git`` beyond the expected ones: the only
    allowed are the ``index``, files under ``logs/``, ``FETCH_HEAD`` and new
    loose objects (existing object files byte-identical). A changed
    ``config``, ``hooks/``, ``info/``, ``HEAD``, ``packed-refs``, ``refs/``
    entry, ``shallow`` or any other file shows the tester tried to steer
    Git (D5, I1)."""
    out = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old != new and not _git_dir_allowed(path, old, new):
            out.append(f".git/{path}: " + ("added" if old is None else "removed" if new is None else "changed"))
    return out[:MAX_DIFFERENCES]


# --- the sandbox ---------------------------------------------------------------


@dataclasses.dataclass
class Sandbox:
    """The folder ``<runtime root>/functional/<job id>/`` and what was
    recorded about it before the tester started."""

    runtime_root: Path
    job_id: str
    work_item_id: str
    base: Path
    candidate: Candidate
    writable: tuple[str, ...]
    refused: tuple[RefusedPattern, ...]
    git_state: dict[str, tuple]
    refs: dict[str, str]
    tmp_dir: Path | None = None  # the short scratch ``TMPDIR`` (see :func:`make_short_tmp`)

    @property
    def clone(self) -> Path:
        return self.base / "clone"

    @property
    def origin(self) -> Path:
        return self.base / "origin.git"

    @property
    def check_dir(self) -> Path:
        return self.base / "check.git"

    @property
    def bin_dir(self) -> Path:
        return self.base / "bin"

    @property
    def out(self) -> Path:
        return self.base / "out"

    @property
    def home(self) -> Path:
        return self.base / "home"

    @property
    def tmp(self) -> Path:
        return self.tmp_dir if self.tmp_dir is not None else self.base / "tmp"

    @property
    def writable_dir(self) -> Path:
        """The folder of the external writable locations (D2)."""
        return self.base / "writable"

    @property
    def writable_links(self) -> dict[str, Path]:
        """Each writable location's name to its external folder (D3)."""
        return {name: self.writable_dir / name for name in self.writable}

    @property
    def fake_logs(self) -> Path:
        return self.base / "fake-logs"

    @property
    def settings_path(self) -> Path:
        return self.base / "settings.json"

    @property
    def scratch_dirs(self) -> list[Path]:
        """The scratch locations the tester may write (D3): ``HOME`` (which
        holds every ``XDG_*`` directory), ``TMPDIR``, the fakes' logs and the
        scratch origin, so a push from a copy of the clone reaches nothing real."""
        return [self.home, self.tmp, self.fake_logs, self.origin]

    def settings(self) -> dict[str, Any]:
        return tester_settings(clone_dir=self.clone, output_dir=self.out, scratch_dirs=self.scratch_dirs,
                               writable_dir=self.writable_dir, writable=self.writable)

    def environment(self) -> dict[str, str]:
        """The sandbox's part of the tester's environment (D3): a ``PATH`` of
        ``bin/`` and the system directories, the scratch directories, no
        bytecode, only local Git transport and no SSH."""
        home = self.home
        return {
            "PATH": f"{self.bin_dir}:{SYSTEM_PATH}", "HOME": str(home), "TMPDIR": str(self.tmp),
            "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_STATE_HOME": str(home / ".local/state"), "XDG_CACHE_HOME": str(home / ".cache"),
            "PYTHONDONTWRITEBYTECODE": "1", "GIT_ALLOW_PROTOCOL": "file", "GIT_SSH_COMMAND": "false",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def rel(self, *parts: str) -> str:
        return "/".join([SANDBOX_AREA, self.job_id, *parts])


def launcher_script(name: str, *, python: str, package_parent: Path, log: Path) -> bytes:
    """The ``bin/<name>`` launcher the Controller writes: ``#!/bin/sh`` then
    ``exec <python> -P -m controller.sandbox_fakes.<name> "$@"`` with
    ``PYTHONPATH`` at the directory that holds the running ``controller``
    package (``-P``, so the clone the tester runs in is never put on the path)."""
    return (f"#!/bin/sh\nFUNCTIONAL_FAKE_LOG={shlex.quote(str(log))} PYTHONPATH={shlex.quote(str(package_parent))} "
            f"PYTHONDONTWRITEBYTECODE=1 exec {shlex.quote(python)} -P -m controller.sandbox_fakes.{name} \"$@\"\n"
            ).encode()


def write_launchers(runtime_root: Path, sandbox_rel: str, fake_logs: Path, *,
                    python: str | None = None, package_parent: Path | None = None) -> None:
    """Write the four launchers through the runtime's write API and set mode
    0755 ourselves (a wheel may not keep the mode of a file)."""
    python = python or sys.executable
    package_parent = package_parent or Path(__file__).resolve().parent.parent
    for name in FAKE_PROGRAMS:
        script = launcher_script(name, python=python, package_parent=package_parent, log=fake_logs / f"{name}.jsonl")
        path = runtime.write_bytes(runtime_root, f"{sandbox_rel}/bin/{name}", script)
        os.chmod(path, 0o755)


def read_fake_calls(fake_logs: Path) -> dict[str, list[dict]]:
    """The calls the fakes logged, by program. Each log is read without
    following a symlink and only if it is a regular file; a line that is not a
    JSON object is skipped. The logs are for the gate message only."""
    calls: dict[str, list[dict]] = {}
    for name in FAKE_PROGRAMS:
        path = fake_logs / f"{name}.jsonl"
        try:
            descriptor = _open_regular(path)
        except (OSError, ValueError):
            continue
        try:
            data = os.read(descriptor, MAX_RESULTS_BYTES)
        finally:
            os.close(descriptor)
        records = []
        for line in data.decode("utf-8", "replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
        if records:
            calls[name] = records
    return calls


def kept_sandboxes(runtime_root: Path, work_item_id: str) -> list[str]:
    """The job ids of the sandboxes kept for ``work_item_id`` after a breach."""
    base = runtime_root / SANDBOX_AREA
    try:
        listing = sorted(item for item in base.iterdir() if item.is_dir())
    except OSError:
        return []
    kept = []
    for item in listing:
        try:
            if (item / "ITEM").read_text() == work_item_id:
                kept.append(item.name)
        except OSError:
            continue
    return kept


def remove_short_tmp(base: Path) -> None:
    """Remove the short scratch ``TMPDIR`` recorded in ``base / TMP_RECORD``
    (a sandbox folder), if it names a ``wcf-`` folder directly under an
    absolute directory. The record was written by :func:`make_short_tmp`
    inside the contained sandbox folder, so removal does not depend on the
    environment of the process that removes it (``XDG_RUNTIME_DIR`` may have
    changed since the allocation). Nothing else is ever removed through it."""
    try:
        recorded = Path(os.fsdecode((base / TMP_RECORD).read_bytes()).strip())
    except (OSError, ValueError):
        return
    if recorded.is_absolute() and recorded.name.startswith(TMP_PREFIX) and recorded.parent != recorded:
        runtime.remove_tree(recorded.parent, recorded.name)


def remove_sandbox(runtime_root: Path, job_id: str) -> None:
    remove_short_tmp(runtime_root / SANDBOX_AREA / job_id)
    runtime.remove_tree(runtime_root, f"{SANDBOX_AREA}/{job_id}")


def sweep_kept(runtime_root: Path, work_item_id: str) -> list[str]:
    """Remove every sandbox kept for the item (the step does this when the
    item's next functional job starts, so at most one is kept per item)."""
    removed = kept_sandboxes(runtime_root, work_item_id)
    for job_id in removed:
        remove_sandbox(runtime_root, job_id)
    return removed


#: Claude Code's sandbox puts two bridge sockets in the tester's ``TMPDIR``:
#: ``claude-socks-<16 hex>.sock`` is the longer name. A Unix socket path is
#: limited to 107 bytes plus the terminating NUL.
SOCKET_NAME_LENGTH = len("claude-socks-") + 16 + len(".sock")
SOCKET_PATH_LIMIT = 107
TMP_PREFIX = "wcf-"
TMP_RECORD = "TMPDIR"


def short_tmp_roots() -> list[Path]:
    """Where a tester's short scratch ``TMPDIR`` may live: ``$XDG_RUNTIME_DIR``
    (when set to an absolute directory), then ``/tmp``."""
    roots = []
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isabs(runtime_dir) and os.path.isdir(runtime_dir):
        roots.append(Path(runtime_dir))
    roots.append(Path("/tmp"))
    return roots


def longest_socket_path(tmp: Path) -> int:
    """The length in bytes (as the kernel counts it, filesystem-encoded) of the
    longest bridge-socket path Claude creates in ``tmp``."""
    return len(os.fsencode(tmp)) + 1 + SOCKET_NAME_LENGTH


def make_short_tmp(runtime_root: Path, job_id: str) -> Path:
    """Create the tester's private scratch ``TMPDIR`` (mode 0700) under the
    first of :func:`short_tmp_roots` where Claude's bridge sockets fit the
    Unix-socket path limit, and record it in the sandbox's ``TMP_RECORD`` file so removing
    the sandbox removes it. The runtime folder is too deep for that limit at
    its default place, so the scratch folder does not live inside it. Raises
    ``functional_tester_unavailable`` when no root is short enough."""
    problems = []
    for root in short_tmp_roots():
        try:
            tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=root))
        except OSError as exc:
            problems.append(f"{root}: {exc.strerror or exc}")
            continue
        if longest_socket_path(tmp) > SOCKET_PATH_LIMIT:
            runtime.remove_tree(root, tmp.name)
            problems.append(f"{root}: a socket path would be {longest_socket_path(tmp)} bytes")
            continue
        try:
            runtime.write_bytes(runtime_root, f"{SANDBOX_AREA}/{job_id}/{TMP_RECORD}", os.fsencode(tmp))
        except BaseException:
            runtime.remove_tree(root, tmp.name)
            raise
        return tmp
    raise unavailable(UNAVAILABLE_ENFORCEMENT,
                      f"no short folder for the tester's TMPDIR: Claude's bridge sockets need a path of at most "
                      f"{SOCKET_PATH_LIMIT} bytes ({'; '.join(problems)})")


def _plain(cwd: Path, *args: str) -> None:
    _git(cwd, list(args))


def build_sandbox(candidate: Candidate, *, runtime_root: Path, job_id: str, work_item_id: str,
                  python: str | None = None, package_parent: Path | None = None) -> Sandbox:
    """Build the sandbox of D3 and verify the fresh clone against the
    manifest: a ``git clone --no-hardlinks`` of the real repository at the
    head (no autocrlf, no filter driver), a scratch bare origin the branch is
    pushed to, the launchers, the scratch directories, the writable locations
    D5 derives from the root ``.gitignore``, the run settings, and a record of
    the clone's ``.git`` and refs. A checkout conversion, or anything else the
    manifest disagrees with, ends at ``functional_tester_unavailable`` here,
    before any tester starts (O2). The folder is removed if building fails."""
    base = runtime_root / SANDBOX_AREA / job_id
    runtime.assert_contained(runtime_root, base)
    if base.exists():
        raise unavailable(UNAVAILABLE_ENFORCEMENT, f"the sandbox {base} already exists")
    base.mkdir(parents=True, mode=0o700)
    try:
        runtime.write_bytes(runtime_root, f"{SANDBOX_AREA}/{job_id}/ITEM", work_item_id.encode())
        tmp_dir = make_short_tmp(runtime_root, job_id)
        for name in ("out", "home", "fake-logs", "bin", "home/.config", "home/.local/share", "home/.local/state",
                     "home/.cache"):
            (base / name).mkdir(parents=True, exist_ok=True)
        _plain(base, "init", "-q", "--bare", "origin.git")
        _plain(base, "init", "-q", "--bare", "check.git")
        _plain(base, "clone", "-q", "--no-hardlinks", "--no-checkout", "-c", "core.autocrlf=false",
               str(candidate.repo_root), "clone")
        clone = base / "clone"
        _plain(clone, "checkout", "-q", "-B", candidate.branch, candidate.head)
        _plain(clone, "remote", "remove", "origin")
        _plain(clone, "remote", "add", "origin", str(base / "origin.git"))
        _plain(clone, "push", "-q", "-u", "origin", candidate.branch)
        writable, refused = derive_writable_locations(candidate.root_gitignore, candidate.manifest)
        make_writable_links(clone, base / "writable", writable)
        exclude_writable_links(runtime_root, f"{SANDBOX_AREA}/{job_id}/clone/.git", writable)
        sandbox = Sandbox(runtime_root, job_id, work_item_id, base, candidate, writable, refused,
                          _git_dir_state(clone / ".git"), refs_of(clone / ".git"), tmp_dir)
        write_launchers(runtime_root, f"{SANDBOX_AREA}/{job_id}", sandbox.fake_logs,
                        python=python, package_parent=package_parent)
        runtime.write_bytes(runtime_root, sandbox.rel("settings.json"),
                            json.dumps(sandbox.settings(), indent=2).encode())
        differences = walk_differences(clone, candidate, sandbox.check_dir, sandbox.writable_links)
        if differences:
            raise unavailable(UNAVAILABLE_CLONE_MISMATCH,
                              f"the fresh clone does not match the manifest (a checkout conversion?): {differences[0]}",
                              differences=differences[:10])
        return sandbox
    except BaseException:
        remove_sandbox(runtime_root, job_id)
        raise


def check_bound(sandbox: Sandbox) -> list[str]:
    """After the run: whether the clone still is the candidate (D5). Empty
    means bound. The worktree is walked in Python against the manifest, the
    clone's ``HEAD`` and refs are compared as plain files with those captured,
    and ``.git`` may differ only by the index, ``logs/``, ``FETCH_HEAD`` and
    new loose objects. No Git command runs in the clone."""
    clone = sandbox.clone
    refs_now = refs_of(clone / ".git")
    problems = [f"clone ref {name} changed" for name in sorted(set(sandbox.refs) | set(refs_now))
                if sandbox.refs.get(name) != refs_now.get(name)]
    moved = {p.split(" ")[2] for p in problems}
    now = _git_dir_state(clone / ".git")
    problems += [d for d in git_dir_differences(sandbox.git_state, now)
                 if not any(d.startswith(f".git/{name}:") for name in moved)]
    problems += walk_differences(clone, sandbox.candidate, sandbox.check_dir, sandbox.writable_links)
    return problems[:MAX_DIFFERENCES]


# ---------------------------------------------------------------------------
# The step's records and stops (D7, D9).
# ---------------------------------------------------------------------------

#: ``selected_action.command`` of a tester job record (a Controller step, not a slash command).
JOB_COMMAND = "functional.test"
#: The ``outcome`` a tester job record's ``functional`` block carries besides :data:`OUTCOMES`.
OUTCOME_UNAVAILABLE = "unavailable"
OUTCOME_BREACH = "isolation_breach"
OUTCOME_UNBOUND = "tested_bytes_unbound"
OUTCOME_INVALID = "results_invalid"
OUTCOME_REFUSED = "recording_refused"
OUTCOME_LIMIT = "limit"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_ERROR = "error"
OUTCOME_INTERRUPTED = "interrupted"
OUTCOME_STALE = "stale"

#: The setting that switches the step off and the remedy the gates name.
SETTING_AUTOMATED = "functional.automated"
MANUAL_REMEDY = (
    "test the head by hand and record one `functional_evidence` result per flow with the Workflow's protocol script; "
    f"`settings set {SETTING_AUTOMATED} false` keeps the stop for a person so this step does not run the tester again"
)


def step_applies(document: Mapping[str, Any]) -> bool:
    """Whether the Workflow's answer is the gate this step acts at (D1): an
    ``external_gate`` for :data:`ACTION_ID` satisfied by :data:`SATISFIED_BY`
    whose policy object names an **automatic** acceptance gate. ``document``
    is the answer (``ProtocolInfo.document``)."""
    policy = document.get("policy")
    return (document.get("satisfied_by") == SATISFIED_BY and isinstance(policy, Mapping)
            and policy.get("gate") == "acceptance" and policy.get("mode") == "automatic")


def refused_text(refused: Iterable[Mapping[str, Any]]) -> str:
    """The writable-location patterns the derivation refused, for a gate message."""
    listed = [f"{item['pattern']} ({item['reason']})" for item in refused]
    if not listed:
        return ""
    return " The patterns of the repository's .gitignore that the sandbox does not make writable: " + "; ".join(listed) + "."


def gate_text(code: str, **facts: Any) -> str:
    """The words of the stop ``code`` (D7): what the step did, why it stopped
    and the remedy. ``facts`` holds what the code names (``detail``, ``head``,
    ``flows``, ``job_id``, ``sandbox``, ``refused``, ``rounds``, ``recorded``)."""
    detail = str(facts.get("detail") or "")
    flows = ", ".join(facts.get("flows") or ())
    sandbox = facts.get("sandbox")
    kept = (f" The sandbox is kept at {sandbox} for inspection and is removed when the next functional run starts."
            if sandbox else "")
    if code == GATE_CHECKLIST_UNRECOGNISED:
        return ("the functional review checklist has no flow the Controller can recognise "
                f"({detail or 'no flow found'}): no tester was started and nothing was recorded. The section "
                f"'{SECTION_TITLE}' of docs/ACTIVE_MILESTONE.md must carry its flows as table rows whose first cell is a "
                "short label, as bold labelled paragraphs (`**A. ...`), as headings that start with a label or `Flow`, "
                f"or as a numbered list; or set `{SETTING_AUTOMATED}` false to keep the stop for a person")
    if code == GATE_TESTER_UNAVAILABLE:
        return (f"the functional tester cannot run here ({detail}): nothing was started and nothing was recorded. "
                f"Install what is missing, or {MANUAL_REMEDY}")
    if code == GATE_RESULTS_INVALID:
        return (f"the tester's results cannot be used ({detail}): nothing was recorded.{kept} Run again, or "
                f"{MANUAL_REMEDY}")
    if code == GATE_ISOLATION_BREACH:
        return (f"the tester run {facts.get('job_id')} reached beyond its sandbox ({detail}): nothing was recorded."
                f"{kept} A person must look at what changed before anything is tested again")
    if code == GATE_TESTED_BYTES_UNBOUND:
        return (f"the tested clone no longer matches the candidate at {facts.get('head')} ({detail}): nothing was "
                f"recorded, because the evidence would not be about the candidate.{kept} Run again, or {MANUAL_REMEDY}")
    if code == GATE_RECORDING_REFUSED:
        return (f"the Workflow refused a flow's result ({detail}); results accepted before it stand "
                f"({facts.get('recorded') or 'none'}). Run again, or {MANUAL_REMEDY}")
    if code == GATE_FLOWS_NOT_RUNNABLE:
        return (f"the flows {flows} cannot run in the sandbox (no network, no real GitHub, no real model, no tool "
                "outside the sandbox PATH): their results are recorded as failed with the reason `environment`, no "
                f"findings were written and the applier was not started ({detail}).{refused_text(facts.get('refused') or ())} "
                "Run those flows by hand and record them, or change the checklist so they run offline")
    if code == GATE_ROUNDS_EXHAUSTED:
        return (f"the tester has failed {facts.get('rounds')} times for this work item (the bound is {ROUND_BOUND}): "
                f"no further tester run is started. To go on, {MANUAL_REMEDY}")
    if code == GATE_UNCHANGED:
        return ("the tester already ran at this head and recorded every flow, yet the Workflow still needs functional "
                f"evidence ({detail}). That is a requirement the checklist cannot satisfy (for example required flows "
                "this release does not read); no tester run is repeated")
    raise ValueError(f"no stop is named {code!r}")
