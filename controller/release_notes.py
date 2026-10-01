"""Release notes that follow the milestone (``workflow-controller-settings-
and-telemetry`` CP4, Design D).

One module for the notes block, so its writer and its readers cannot drift
apart (D.2):

- :func:`extract_section`, the milestone narrative's notes section, which
  readiness reads at the acceptance commit;
- :func:`render_block`, the start marker binding the notes to the work item
  and the SHA-256 of their bytes, the notes, and the end marker;
- :func:`notes_problem` and :func:`paragraph_problem`, the I8 checks:
  every paragraph parsed on its own by ``git interpret-trailers --parse``,
  the length, the line rules and at least one non-blank line;
- :func:`parse_message`, which finds the blocks of one commit message
  (D.3 step 2), and :func:`resolve`, which picks the block each work item
  uses across a release range, checks its digest and renders the text
  (D.3 steps 3-6).

Readiness (:mod:`controller.milestone_branch`), the release
(:mod:`controller.release_txn`) and ``tools/release.py notes-block`` call
it. Nothing here reads a tree, a narrative of another commit or the forge.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Callable, Sequence

from . import gitrepo, repo_policy

#: Every Controller marker starts with this text; the notes must not hold it.
CONTROLLER_MARKER_TEXT = "<!-- workflow-controller:"
#: A **marker line** starts with these bytes (D.3 step 2). They are 39
#: characters, and GitHub's wrap breaks a line only at a space after them.
MARKER_LINE_PREFIX = "<!-- workflow-controller: release-notes"
_MARKER_LINE_PREFIX_BYTES = MARKER_LINE_PREFIX.encode("ascii")
START_MARKER = MARKER_LINE_PREFIX + " work_item={work_item_id} sha256={digest} -->"
#: 47 characters: never wrapped.
END_MARKER = MARKER_LINE_PREFIX + " end -->"

#: I8's line rule: GitHub's squash leaves a line of at most 72 characters
#: alone, and 72 bytes of UTF-8 are never more than 72 characters.
MAX_LINE_BYTES = 72
#: GitHub's limit on a pull request body, in characters.
MAX_BODY_CHARS = 65536

_START_RE = re.compile(re.escape(MARKER_LINE_PREFIX)
                       + r" work_item=(?P<work_item>\S+) sha256=(?P<digest>\S+) -->")
_WORK_ITEM_TOKEN_RE = re.compile(r"(?:^|\s)work_item=(?P<work_item>\S+)")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


def digest(notes: str) -> str:
    """The lowercase hexadecimal SHA-256 of ``notes``' UTF-8 bytes."""
    return hashlib.sha256(notes.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The section and the block (D.2).
# ---------------------------------------------------------------------------


def trim(text: str) -> str:
    """``text``'s lines with the outer blank (whitespace-only) lines removed,
    joined by ``\\n`` with no final line break."""
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def extract_section(text: str, heading: str) -> str | None:
    """The section under the first line that is exactly ``## <heading>`` (a
    final carriage return aside), up to the next line starting with ``## ``
    or the end of ``text``, with outer blank lines trimmed. ``None`` when no
    line is the heading; ``""`` when the section is empty."""
    lines = text.split("\n")
    # A CRLF file's heading still matches, so its notes are found and then
    # refused for their carriage returns (I8), never silently absent.
    start = next((index for index, line in enumerate(lines) if line.removesuffix("\r") == f"## {heading}"), None)
    if start is None:
        return None
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
               len(lines))
    return trim("\n".join(lines[start + 1:end]))


def start_marker(work_item_id: str, notes: str) -> str:
    return START_MARKER.format(work_item_id=work_item_id, digest=digest(notes))


def render_block(work_item_id: str, notes: str) -> str:
    """The notes block readiness writes above the Controller's lines: the
    start marker, the notes, the end marker, with no final line break."""
    return f"{start_marker(work_item_id, notes)}\n{notes}\n{END_MARKER}"


# ---------------------------------------------------------------------------
# The I8 checks.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NotesProblem:
    """Why notes or a body fail I8: ``rule`` names the check, ``line`` the
    1-based notes line (``None`` for a paragraph or the whole text),
    ``excerpt`` the offending line's or paragraph's first characters."""

    rule: str
    line: int | None
    excerpt: str
    remedy: str

    def describe(self) -> str:
        where = f"line {self.line} " if self.line is not None else ""
        return f"{where}{self.excerpt!r}: {self.rule}; {self.remedy}"


_WRAP = f"wrap the section at {MAX_LINE_BYTES} columns or remove the text"


def _excerpt(text: str) -> str:
    return text[:40]


def notes_problem(notes: str) -> NotesProblem | None:
    """The first line rule ``notes`` break (I8): at least one non-blank
    line, no Controller marker text, no tab or carriage return, and every
    line at most :data:`MAX_LINE_BYTES` bytes of UTF-8, not ending in a
    space."""
    if not notes.strip():
        return NotesProblem("the notes hold no non-blank line", None, notes[:40], "write the notes")
    for number, line in enumerate(notes.split("\n"), start=1):
        if CONTROLLER_MARKER_TEXT in line:
            return NotesProblem(f"the notes contain the Controller marker text {CONTROLLER_MARKER_TEXT!r}",
                                number, _excerpt(line), "remove the text")
        if "\t" in line:
            return NotesProblem("the line holds a tab", number, _excerpt(line), "replace the tab with spaces")
        if "\r" in line:
            return NotesProblem("the line holds a carriage return (CRLF line endings)", number, _excerpt(line),
                                "use LF line endings")
        size = len(line.encode("utf-8"))
        if size > MAX_LINE_BYTES:
            return NotesProblem(f"the line holds {size} bytes of UTF-8, over the limit of {MAX_LINE_BYTES}",
                                number, _excerpt(line), _WRAP)
        if line.endswith(" "):
            return NotesProblem("the line ends in a space", number, _excerpt(line), "remove the trailing space")
    return None


def paragraphs(text: str) -> list[str]:
    """``text``'s paragraphs: runs of non-blank lines."""
    found, current = [], []
    for line in text.split("\n"):
        if line.strip():
            current.append(line)
        elif current:
            found.append("\n".join(current))
            current = []
    if current:
        found.append("\n".join(current))
    return found


TrailerParser = Callable[[str], str]


def paragraph_problem(text: str, *, parse: TrailerParser = gitrepo.parse_trailers) -> NotesProblem | None:
    """The first I8 paragraph rule ``text`` (a whole body, or a block)
    breaks: at most :data:`MAX_BODY_CHARS` characters, and every paragraph,
    parsed on its own after a blank line, no trailer block."""
    if len(text) > MAX_BODY_CHARS:
        return NotesProblem(f"the text holds {len(text)} characters, over GitHub's limit of {MAX_BODY_CHARS}",
                            None, _excerpt(text), "shorten the notes")
    for paragraph in paragraphs(text):
        if parse(paragraph).strip():
            first = paragraph.split("\n", 1)[0]
            return NotesProblem("the paragraph parses as a Git trailer block", None, _excerpt(first),
                                "reword the paragraph or join it to its neighbour")
    return None


def block_problem(work_item_id: str, notes: str, *,
                  parse: TrailerParser = gitrepo.parse_trailers) -> NotesProblem | None:
    """Every D.2 check of a block ``tools/release.py notes-block`` prints:
    the work-item id, the line rules, then the paragraph rules over the
    rendered block."""
    if not repo_policy.WORK_ITEM_ID_RE.fullmatch(work_item_id):
        return NotesProblem(f"the work-item id does not match {repo_policy.WORK_ITEM_ID_RE.pattern}", None,
                            _excerpt(work_item_id), "name a valid work-item id")
    return notes_problem(notes) or paragraph_problem(render_block(work_item_id, notes), parse=parse)


# ---------------------------------------------------------------------------
# The commit-message parser (D.3 step 2).
# ---------------------------------------------------------------------------


class NotUtf8Error(ValueError):
    """A message holding a marker line is not valid UTF-8."""


@dataclasses.dataclass(frozen=True)
class Block:
    """One work item's notes block in one message. ``problem`` is ``None``
    for a well-formed block, else why it is a **damaged block** (its notes
    and digest may then be ``None``)."""

    work_item_id: str
    digest: str | None
    notes: str | None
    problem: str | None = None

    @property
    def damaged(self) -> bool:
        return self.problem is not None


@dataclasses.dataclass(frozen=True)
class ParsedMessage:
    """The blocks of one message, in message order, at most one per work
    item, and the malformed markers that name no work item."""

    blocks: tuple[Block, ...] = ()
    unattributable: tuple[str, ...] = ()


def has_marker_line(raw: bytes) -> bool:
    return any(line.startswith(_MARKER_LINE_PREFIX_BYTES) for line in raw.split(b"\n"))


def _is_marker_line(line: str) -> bool:
    return line.startswith(MARKER_LINE_PREFIX)


def _work_item_token(text: str) -> str | None:
    match = _WORK_ITEM_TOKEN_RE.search(text)
    if match is None or not repo_policy.WORK_ITEM_ID_RE.fullmatch(match["work_item"]):
        return None
    return match["work_item"]


@dataclasses.dataclass
class _Candidate:
    """A non-end marker line and its continuation lines (``first`` to
    ``last``), the start marker's joined text, and its parse."""

    first: int
    last: int
    text: str
    work_item_id: str | None
    digest: str | None
    problem: str | None


def _candidate(lines: Sequence[str], index: int) -> _Candidate:
    """The candidate opened at marker line ``index``: the line joined with
    its continuation lines up to the first ``-->``, every run of whitespace
    one space. A blank line or another marker line ends the search."""
    last = index
    while "-->" not in lines[last]:
        following = last + 1
        if following >= len(lines) or not lines[following].strip() or _is_marker_line(lines[following]):
            break
        last = following
    text = " ".join(" ".join(lines[index:last + 1]).split())
    work_item = _work_item_token(text)
    end = lines[last]
    if "-->" not in end:
        return _Candidate(index, last, text, work_item, None, "the start marker has no closing -->")
    if not end.endswith("-->") or end.index("-->") != len(end) - 3:
        return _Candidate(index, last, text, work_item, None, "the start marker's --> does not end its line")
    match = _START_RE.fullmatch(text)
    if match is None:
        return _Candidate(index, last, text, work_item, None, f"the start marker {text!r} is not well formed")
    if not repo_policy.WORK_ITEM_ID_RE.fullmatch(match["work_item"]):
        return _Candidate(index, last, text, None, None, f"the start marker's work_item= token "
                          f"{match['work_item']!r} is not a work-item id")
    if not _DIGEST_RE.fullmatch(match["digest"]):
        return _Candidate(index, last, text, work_item, None, f"the start marker's sha256= token "
                          f"{match['digest']!r} is not 64 lowercase hexadecimal digits")
    return _Candidate(index, last, text, match["work_item"], match["digest"], None)


def parse_message(raw: bytes) -> ParsedMessage:
    """The notes blocks of one commit message (D.3 step 2). Only marker
    lines count; a message with none contributes nothing, whatever its
    encoding. One that holds a marker line and is not valid UTF-8 raises
    :class:`NotUtf8Error`.

    Every marker line that is not :data:`END_MARKER` opens a candidate,
    which takes the first end marker after it unless another candidate
    opens first. A candidate whose start marker is malformed, which has no
    end marker, or whose notes hold no non-blank line is damaged; it
    belongs to its work item when it names a valid ``work_item=`` token,
    and is unattributable otherwise, as is an end marker no candidate
    takes. Two blocks of one work item in one message are one damaged
    block."""
    if not has_marker_line(raw):
        return ParsedMessage()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NotUtf8Error(f"the message holds a marker line and is not valid UTF-8 ({exc})") from None
    lines = text.split("\n")
    found: list[tuple[str | None, Block | None, str | None]] = []  # (work item, block, unattributable)
    open_candidate: _Candidate | None = None

    def close(candidate: _Candidate, end: int | None) -> None:
        problem = candidate.problem
        notes = None
        if end is None:
            problem = problem or "the start marker has no end marker"
        else:
            notes = "\n".join(lines[candidate.last + 1:end])
            if problem is None and not notes.strip():
                problem = "the block's notes are empty"
        if candidate.work_item_id is None:
            found.append((None, None, f"{candidate.text!r}: {problem}"))
            return
        found.append((candidate.work_item_id,
                      Block(candidate.work_item_id, candidate.digest if problem is None else None,
                            notes if problem is None else None, problem), None))

    index = 0
    while index < len(lines):
        line = lines[index]
        if not _is_marker_line(line):
            index += 1
            continue
        if line == END_MARKER:
            if open_candidate is not None:
                close(open_candidate, index)
                open_candidate = None
            else:
                found.append((None, None, f"{END_MARKER!r} on line {index + 1} ends no block"))
            index += 1
            continue
        if open_candidate is not None:
            close(open_candidate, None)
        open_candidate = _candidate(lines, index)
        index = open_candidate.last + 1
    if open_candidate is not None:
        close(open_candidate, None)

    blocks: dict[str, Block] = {}
    for work_item, block, _ in found:
        if work_item is None:
            continue
        if work_item in blocks:
            blocks[work_item] = Block(work_item, None, None,
                                      "the message holds more than one block for the work item")
        else:
            blocks[work_item] = block
    return ParsedMessage(tuple(blocks.values()),
                         tuple(problem for work_item, _, problem in found if work_item is None))


# ---------------------------------------------------------------------------
# A release range (D.3 steps 3-6).
# ---------------------------------------------------------------------------

#: The outcomes of :func:`resolve`'s refusal.
MISSING = "missing"
UNREADABLE = "unreadable"
UNVERIFIED = "unverified"


@dataclasses.dataclass(frozen=True)
class UsedBlock:
    commit: str
    block: Block


@dataclasses.dataclass(frozen=True)
class Resolution:
    """The notes of a release range: ``text`` from the ``used`` blocks, and
    the ``superseded`` ``(commit, work item)`` blocks."""

    text: str
    used: tuple[UsedBlock, ...]
    superseded: tuple[tuple[str, str], ...]

    def summary(self) -> str:
        parts = [f"included {', '.join(f'{u.block.work_item_id} ({u.commit})' for u in self.used)}"]
        if self.superseded:
            parts.append("superseded " + ", ".join(f"{item} ({sha})" for sha, item in self.superseded))
        return "; ".join(parts)


@dataclasses.dataclass(frozen=True)
class Refusal:
    """Why a release range has no publishable notes: ``outcome`` is
    :data:`MISSING`, :data:`UNREADABLE` or :data:`UNVERIFIED`;
    ``supersedable`` is false for the two refusals no supplied block can
    clear (a marker naming no work item, a non-UTF-8 message holding a
    marker line)."""

    outcome: str
    problems: tuple[str, ...]
    supersedable: bool
    work_items: tuple[str, ...] = ()


class NotesRefused(Exception):
    def __init__(self, refusal: Refusal) -> None:
        super().__init__("; ".join(refusal.problems))
        self.refusal = refusal


def render_text(used: Sequence[UsedBlock]) -> str:
    """D.3 step 5: one block is its notes; several are each preceded by a
    ``### <work_item_id>`` line, separated by blank lines."""
    if len(used) == 1:
        return used[0].block.notes or ""
    return "\n\n".join(f"### {u.block.work_item_id}\n\n{u.block.notes}" for u in used)


def resolve(messages: Sequence[tuple[str, bytes]]) -> Resolution:
    """The notes of a release range, from ``(commit, message bytes)`` pairs
    oldest first. For each work item the block of the newest commit that
    carries one is used, and older ones are superseded. Raises
    :class:`NotesRefused` when no block is in the range (``missing``), a
    message holding a marker line is not UTF-8 (``unreadable``), or a used
    block is damaged, its digest does not match, or a marker names no work
    item (``unverified``)."""
    order: list[str] = []
    latest: dict[str, tuple[tuple[int, int], str, Block]] = {}
    superseded: list[tuple[str, str]] = []
    unattributable: list[str] = []
    not_utf8: list[str] = []
    for position, (sha, raw) in enumerate(messages):
        try:
            parsed = parse_message(raw)
        except NotUtf8Error as exc:
            not_utf8.append(f"commit {sha}: {exc}")
            continue
        unattributable.extend(f"commit {sha}: a release-notes marker that names no work item: {problem}"
                              for problem in parsed.unattributable)
        for index, block in enumerate(parsed.blocks):
            if block.work_item_id in latest:
                superseded.append((latest[block.work_item_id][1], block.work_item_id))
            else:
                order.append(block.work_item_id)
            latest[block.work_item_id] = ((position, index), sha, block)
    if not_utf8:
        raise NotesRefused(Refusal(UNREADABLE, tuple(not_utf8), supersedable=False))
    if unattributable:
        raise NotesRefused(Refusal(UNVERIFIED, tuple(unattributable), supersedable=False))
    if not latest:
        raise NotesRefused(Refusal(MISSING, ("no commit of the release range carries a release-notes block",),
                                   supersedable=True))
    problems, failed = [], []
    for work_item in order:
        _, sha, block = latest[work_item]
        if block.damaged:
            problems.append(f"commit {sha}: the block of {work_item} is damaged: {block.problem}")
            failed.append(work_item)
            continue
        actual = digest(block.notes)
        if actual != block.digest:
            problems.append(f"commit {sha}: the notes of {work_item} have SHA-256 {actual}, the marker "
                            f"records {block.digest}")
            failed.append(work_item)
    if problems:
        raise NotesRefused(Refusal(UNVERIFIED, tuple(problems), supersedable=True, work_items=tuple(failed)))
    # Commit order, and a commit's several blocks in message order.
    used = sorted((latest[item] for item in order), key=lambda entry: entry[0])
    used_blocks = tuple(UsedBlock(sha, block) for _, sha, block in used)
    return Resolution(render_text(used_blocks), used_blocks, tuple(superseded))
