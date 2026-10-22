#!/usr/bin/env python3
# state_writer: false
"""The structured backlog file: parser, schema, selection, edit and page
generator (workflow-2.17.0, `backlog-file`, D-Format, D-Select, D-Accept,
D-Generate).

A Workflow-managed repository can declare that its roadmap is a backlog file
(a strict YAML subset, see `docs/ai-workflow/BACKLOG_FILE.md`) from which the
Workflow picks the next item, sets an accepted item `done` and regenerates the
roadmap page. This module is the library half: it reads nothing but the text
and paths it is given, writes nothing, and never imports another Workflow
module (INV-5: the script can be copied out of a repository).

The parser accepts exactly the subset D-Format lists and refuses everything
else with `line N: what`, so every accepted file has one meaning in any YAML
parser (INV-6).

Stdlib-only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

VERSION = 1
MAX_BYTES = 256 * 1024
MAX_LINES = 2000
MAX_LINE_CHARS = 1000
MAX_SHOWN = 10

ITEM_KEYS = ("id", "title", "status", "order", "depends_on", "size", "detail", "release")
_INT_KEYS = {"order", "size"}
_LIST_KEYS = {"depends_on"}
STATUSES = ("idea", "next", "in-progress", "done")
_STATUS_WORDS = {"idea": "Idea", "next": "Next", "in-progress": "In progress", "done": "Done"}

DETAIL_PREFIX = "docs/items/"
GENERATED_PREFIX = "<!-- Generated from "

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_INT_RE = re.compile(r"^(0|[1-9][0-9]{0,5})$")
_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$")
_DETAIL_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n"}
_PLAIN_FORBIDDEN = "[]{},"

_NEXT_CHECK = ("fix those lines as `docs/ai-workflow/BACKLOG_FILE.md` says, "
               "then run `python3 scripts/workflow_backlog.py check`")


class BacklogError(Exception):
    """A refusal carrying a stable D-Messages code, what is wrong and the one
    next step."""

    def __init__(self, code: str, message: str, next_step: str, violations: list | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step
        self.violations = violations or []


class BacklogFormatError(Exception):
    """The text breaks D-Format; `line` is 1-based."""

    def __init__(self, line: int, what: str):
        super().__init__(f"line {line}: {what}")
        self.line = line
        self.what = what


# --------------------------------------------------------------------------
# Parser (D-Format rules 1 to 8)
# --------------------------------------------------------------------------


def _bad_char(ch: str) -> bool:
    o = ord(ch)
    if o == 0x0A or 0x20 <= o <= 0x7E:
        return False
    if 0xA0 <= o <= 0xD7FF:
        return o in (0x2028, 0x2029)
    if 0xE000 <= o <= 0xFFFD:
        return o == 0xFEFF
    return not 0x10000 <= o <= 0x10FFFF


def _char_name(ch: str) -> str:
    if ch == "\r":
        return "a carriage return (LF line ends only)"
    if ch == "\t":
        return "a tab character"
    if ch == "﻿":
        return "U+FEFF (byte-order mark)"
    return f"the character U+{ord(ch):04X}"


def parse_backlog_bytes(raw: bytes) -> tuple[dict, dict]:
    """`parse_backlog` for bytes: refuses a BOM and invalid UTF-8 with the line."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise BacklogFormatError(1, "a byte-order mark is not allowed")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BacklogFormatError(raw.count(b"\n", 0, exc.start) + 1, "the file is not valid UTF-8") from None
    return parse_backlog(text)


def _scan_quoted(s: str, pos: int, line: int) -> tuple[str, int]:
    quote = s[pos]
    out: list[str] = []
    i = pos + 1
    while i < len(s):
        c = s[i]
        if quote == '"':
            if c == "\\":
                nxt = s[i + 1:i + 2]
                if nxt in ("\\", '"'):
                    out.append(nxt)
                    i += 2
                    continue
                raise BacklogFormatError(line, 'unknown escape in a double-quoted string (only \\\\ and \\" exist)')
            if c == '"':
                return "".join(out), i + 1
        else:
            if c == "'":
                if s[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), i + 1
        out.append(c)
        i += 1
    raise BacklogFormatError(line, "unterminated quoted string")


def _check_plain(value: str, line: int, *, flow: bool = False) -> str:
    if value == "":
        raise BacklogFormatError(line, "empty value (an empty value is `null` in YAML)")
    if value != value.strip(" "):
        raise BacklogFormatError(line, "a space where none is allowed (exactly one space before a comment, none inside a flow list around the commas)")
    first = value[0]
    if first in "&*!|>{%@`":
        names = {"&": "an anchor", "*": "an alias", "!": "a tag", "|": "a block scalar", ">": "a block scalar",
                 "{": "a flow mapping"}
        what = names.get(first, f"the indicator `{first}`")
        raise BacklogFormatError(line, f"{what} is not allowed; quote the value")
    if not first.isalpha():
        raise BacklogFormatError(line, "a plain value must start with a letter; quote it")
    if value.lower() in _RESERVED:
        raise BacklogFormatError(line, f"`{value}` is a reserved word in YAML; quote it")
    if ": " in value or value.endswith(":"):
        raise BacklogFormatError(line, "a plain value cannot contain `: `; quote it")
    if " #" in value:
        raise BacklogFormatError(line, "a plain value cannot contain ` #`; quote it")
    for ch in _PLAIN_FORBIDDEN:
        if ch in value:
            raise BacklogFormatError(line, f"a plain value cannot contain `{ch}`; quote it")
    if flow:
        for ch in ":?":
            if ch in value:
                raise BacklogFormatError(line, f"a plain entry of a flow list cannot contain `{ch}`; quote it")
    return value


def _check_tail(tail: str, line: int) -> None:
    if tail == "":
        return
    if tail.startswith(" #"):
        return
    raise BacklogFormatError(line, "unexpected text after the value (a comment is exactly one space then `#`)")


def _parse_scalar(s: str, line: int) -> str:
    """A string scalar followed by nothing or a trailing comment."""
    if s[0] in "\"'":
        value, end = _scan_quoted(s, 0, line)
        _check_tail(s[end:], line)
        return value
    if s[0] == "[":
        raise BacklogFormatError(line, "a flow list is only allowed for `depends_on`")
    idx = s.find(" #")
    return _check_plain(s if idx < 0 else s[:idx], line)


def _parse_int(s: str, line: int) -> int:
    if s[0] in "\"'":
        raise BacklogFormatError(line, "a quoted number is not allowed here; write plain digits")
    idx = s.find(" #")
    token = s if idx < 0 else s[:idx]
    if not _INT_RE.match(token):
        raise BacklogFormatError(line, "expected plain decimal digits (no sign, no leading zero, at most six digits)")
    return int(token)


def _parse_flow_list(s: str, line: int) -> list[str]:
    if s[0] != "[":
        raise BacklogFormatError(line, "`depends_on` takes `[]`, a one-line list `[a, b]`, or a block list")
    if s.startswith("[]"):
        _check_tail(s[2:], line)
        return []
    out: list[str] = []
    i = 1
    while True:
        if i >= len(s):
            raise BacklogFormatError(line, "unterminated flow list")
        if s[i] in "\"'":
            value, i = _scan_quoted(s, i, line)
        else:
            j = i
            while j < len(s) and s[j] not in ",]":
                j += 1
            if j >= len(s):
                raise BacklogFormatError(line, "unterminated flow list")
            value = _check_plain(s[i:j], line, flow=True)
            i = j
        out.append(value)
        if i >= len(s):
            raise BacklogFormatError(line, "unterminated flow list")
        if s[i] == "]":
            _check_tail(s[i + 1:], line)
            return out
        if s[i] != ",":
            raise BacklogFormatError(line, "expected `,` or `]` in the flow list")
        if s[i + 1:i + 2] != " ":
            raise BacklogFormatError(line, "a comma in a flow list is followed by exactly one space")
        i += 2
        if s[i:i + 1] in ("", "]", " ", ","):
            raise BacklogFormatError(line, "an empty entry in a flow list is not allowed")


def _split_key(body: str, line: int) -> tuple[str, str | None]:
    """`key: value` -> (key, value text) with exactly one space; None when the
    value is empty (a block opener, possibly with a comment)."""
    m = _KEY_RE.match(body)
    if not m:
        raise BacklogFormatError(line, "expected `key: value`")
    key, rest = m.groups()
    if rest == "" or rest.startswith(" #"):
        return key, None
    if rest[0] == " " and len(rest) > 1 and rest[1] != " ":
        return key, rest[1:]
    raise BacklogFormatError(line, "exactly one space is required after `:`")


_EMPTY_ITEMS = "`items:` has no item (write `items: []` for an empty backlog)"


def parse_backlog(text: str) -> tuple[dict, dict]:
    """Parse `text` under D-Format. Returns `(data, lines)`: `data` is
    `{"version": int, "items": [ {key: value} ]}` and `lines` maps `version`
    and `items` to their lines and `item` to one `{key: line}` per item
    (`_item` is the item's first line). Raises `BacklogFormatError`."""
    if len(text.encode("utf-8", "surrogatepass")) > MAX_BYTES:
        raise BacklogFormatError(1, f"the file is over {MAX_BYTES // 1024} KiB")
    if text.startswith("﻿"):
        raise BacklogFormatError(1, "a byte-order mark is not allowed")
    rows = text.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    if len(rows) > MAX_LINES:
        raise BacklogFormatError(MAX_LINES + 1, f"the file is over {MAX_LINES} lines")

    for number, row in enumerate(rows, 1):
        if len(row) > MAX_LINE_CHARS:
            raise BacklogFormatError(number, f"the line is over {MAX_LINE_CHARS} characters")
        for ch in row:
            if _bad_char(ch):
                raise BacklogFormatError(number, f"{_char_name(ch)} is not allowed")
        if row.endswith(" "):
            raise BacklogFormatError(number, "the line ends in a space")

    data: dict = {"items": []}
    lines: dict = {"item": []}
    items_open = False
    cur: dict | None = None
    cur_lines: dict | None = None
    pending: tuple[dict, dict, int] | None = None  # depends_on block: item, lines, opener line
    pending_entries = 0

    def close_pending() -> None:
        nonlocal pending
        if pending is not None and pending_entries == 0:
            raise BacklogFormatError(pending[2], "`depends_on:` needs a block list or `[]` (an empty value is `null` in YAML)")
        pending = None

    for number, row in enumerate(rows, 1):
        stripped = row.lstrip(" ")
        if stripped == "" or stripped.startswith("#"):
            continue
        indent = len(row) - len(stripped)

        if pending is not None and indent == 6 and row[6:].startswith("- "):
            body = row[8:]
            if body == "" or body.startswith(" "):
                raise BacklogFormatError(number, "exactly one space is required after `-`")
            pending[0].setdefault("depends_on", []).append(_parse_scalar(body, number))
            pending_entries += 1
            continue
        close_pending()

        if indent == 0:
            if row.startswith("---") or row.startswith("..."):
                raise BacklogFormatError(number, "document markers are not allowed")
            if row.startswith("%"):
                raise BacklogFormatError(number, "directives are not allowed")
            key, value = _split_key(row, number)
            if key not in ("version", "items"):
                raise BacklogFormatError(number, f"unknown top-level key `{key}` (only `version` and `items`)")
            if key in lines:
                raise BacklogFormatError(number, f"`{key}` is repeated")
            if items_open and not data["items"]:
                raise BacklogFormatError(lines["items"], _EMPTY_ITEMS)
            lines[key] = number
            cur = cur_lines = None
            items_open = False
            if key == "version":
                if value is None:
                    raise BacklogFormatError(number, "`version` needs the value 1")
                data["version"] = _parse_int(value, number)
            elif value is None:
                items_open = True
            else:
                if _parse_flow_list(value, number):
                    raise BacklogFormatError(number, "`items:` takes a block list or `[]`")
            continue

        if indent == 2:
            if not row.startswith("  - "):
                raise BacklogFormatError(number, "wrong indentation (an item starts with `  - `)")
            if not items_open:
                raise BacklogFormatError(number, "an item outside `items:`")
            body = row[4:]
            if body == "" or body.startswith(" "):
                raise BacklogFormatError(number, "exactly one space is required after `-`")
            cur = {}
            cur_lines = {"_item": number}
            data["items"].append(cur)
            lines["item"].append(cur_lines)
        elif indent == 4 and cur is not None:
            body = row[4:]
            if body.startswith("- "):
                raise BacklogFormatError(number, "wrong indentation (a list entry is at six spaces, under `depends_on:`)")
        elif indent == 6 and row[6:].startswith("- "):
            raise BacklogFormatError(number, "a list entry outside a `depends_on:` block")
        else:
            raise BacklogFormatError(number, "wrong indentation")

        key, value = _split_key(body, number)
        if key not in ITEM_KEYS:
            raise BacklogFormatError(number, f"unknown item key `{key}`")
        if key in cur_lines:
            raise BacklogFormatError(number, f"`{key}` is repeated in this item")
        cur_lines[key] = number
        if key in _LIST_KEYS:
            if value is None:
                pending = (cur, cur_lines, number)
                pending_entries = 0
                cur["depends_on"] = []
            else:
                cur["depends_on"] = _parse_flow_list(value, number)
            continue
        if value is None:
            raise BacklogFormatError(number, "empty value (an empty value is `null` in YAML)")
        cur[key] = _parse_int(value, number) if key in _INT_KEYS else _parse_scalar(value, number)

    close_pending()
    last = max(len(rows), 1)
    for key in ("version", "items"):
        if key not in lines:
            raise BacklogFormatError(last, f"the top-level key `{key}` is missing")
    if items_open and not data["items"]:
        raise BacklogFormatError(lines["items"], _EMPTY_ITEMS)
    return data, lines


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def path_problem(repo_root, rel) -> str | None:
    """D-Declare's path rule: repository-relative with `/`, no empty, `.` or
    `..` segment, no control character, not under `.git/` or `.ai-review/`, no
    existing component a symbolic link, and every existing component spelled
    exactly as its directory entry. Returns the problem, or None."""
    if not isinstance(rel, str) or rel == "":
        return "the path is empty"
    if rel.startswith("/"):
        return "the path must be repository-relative (no leading `/`)"
    if "\\" in rel:
        return "the path must use `/` separators (no backslash)"
    for ch in rel:
        if ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F:
            return "the path contains a control character"
    parts = rel.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return "the path has an empty, `.` or `..` segment"
    if parts[0].casefold() in (".git", ".ai-review"):
        return f"the path is under `{parts[0]}/`"
    current = Path(repo_root)
    for part in parts:
        try:
            mode = os.lstat(current / part).st_mode
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"the path cannot be inspected ({exc.strerror})"
        if stat.S_ISLNK(mode):
            return f"`{part}` is a symbolic link; name the real path"
        try:
            names = os.listdir(current)
        except OSError as exc:
            return f"the path cannot be inspected ({exc.strerror})"
        if part not in names:
            return f"`{part}` differs in case from the name on disk"
        current = current / part
    return None


def _detail_problem(repo_root, rel) -> str | None:
    if not isinstance(rel, str) or rel == "":
        return "`detail` is empty"
    if not _DETAIL_CHARS_RE.match(rel):
        return "`detail` may contain only letters, digits and `. _ / -`"
    problem = path_problem(repo_root, rel)
    if problem:
        return f"`detail`: {problem}"
    if not rel.startswith(DETAIL_PREFIX) or len(rel) == len(DETAIL_PREFIX):
        return f"`detail` must be a file under `{DETAIL_PREFIX}`"
    try:
        mode = os.lstat(Path(repo_root) / rel).st_mode
    except OSError:
        return f"`detail` file `{rel}` does not exist"
    if not stat.S_ISREG(mode):
        return f"`detail` `{rel}` is not a regular file"
    return None


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def _find_cycle(items: list[dict]) -> list[str] | None:
    graph = {it["id"]: [d for d in it.get("depends_on", []) if isinstance(d, str)]
             for it in items if isinstance(it.get("id"), str)}
    state: dict[str, int] = {}
    stack: list[str] = []
    # Iterative depth-first search (a long chain must not exhaust the interpreter's
    # recursion limit): each frame is a node and its next dependency to try.
    for root in graph:
        if root in state:
            continue
        state[root] = 1
        stack.append(root)
        frames = [(root, iter(graph[root]))]
        while frames:
            node, deps = frames[-1]
            for dep in deps:
                if dep not in graph:
                    continue
                if state.get(dep) == 1:
                    return stack[stack.index(dep):] + [dep]
                if dep not in state:
                    state[dep] = 1
                    stack.append(dep)
                    frames.append((dep, iter(graph[dep])))
                    break
            else:
                frames.pop()
                stack.pop()
                state[node] = 2
    return None


def validate_backlog(data: dict, lines: dict, repo_root) -> None:
    """The schema of D-Format, applied after parsing. Collects every violation
    of one pass; raises `BacklogError("backlog_invalid")` listing at most ten."""
    found: list[tuple[int, str]] = []
    item_lines = lines.get("item", [])

    def add(line: int, what: str) -> None:
        found.append((line, what))

    if data.get("version") != VERSION:
        add(lines.get("version", 1), f"`version` must be {VERSION}")
    items = data.get("items", [])
    ids: dict[str, int] = {}
    orders: dict[int, int] = {}
    for item, il in zip(items, item_lines):
        start = il["_item"]

        def at(key: str) -> int:
            return il.get(key, start)

        for required in ("id", "title", "status", "order"):
            if required not in item:
                add(start, f"the item has no `{required}`")
        item_id = item.get("id")
        if item_id is not None:
            if not _ID_RE.match(item_id):
                add(at("id"), f"`id` `{item_id}` must match ^[a-z0-9][a-z0-9_-]{{0,63}}$")
            elif item_id in ids:
                add(at("id"), f"`id` `{item_id}` repeats line {ids[item_id]}")
            else:
                ids[item_id] = at("id")
        title = item.get("title")
        if title is not None and not 1 <= len(title) <= 200:
            add(at("title"), "`title` must be 1 to 200 characters")
        status = item.get("status")
        if status is not None and status not in STATUSES:
            add(at("status"), f"`status` must be one of {', '.join(STATUSES)}")
        order = item.get("order")
        if order is not None:
            if not 1 <= order <= 999999:
                add(at("order"), "`order` must be 1 to 999999")
            elif order in orders:
                add(at("order"), f"`order` {order} repeats line {orders[order]}")
            else:
                orders[order] = at("order")
        size = item.get("size")
        if size is not None and not 1 <= size <= 99:
            add(at("size"), "`size` must be 1 to 99")
        if "detail" in item:
            problem = _detail_problem(repo_root, item["detail"])
            if problem:
                add(at("detail"), problem)
        if "release" in item:
            if status != "done":
                add(at("release"), "`release` is only allowed on a `done` item")
            if not _RELEASE_RE.match(item["release"]):
                add(at("release"), "`release` must be 1 to 40 characters from [A-Za-z0-9._+-], starting alphanumeric")

    for item, il in zip(items, item_lines):
        seen: set[str] = set()
        for dep in item.get("depends_on", []):
            line = il.get("depends_on", il["_item"])
            if dep == item.get("id"):
                add(line, f"`{dep}` depends on itself")
            elif dep not in ids:
                add(line, f"`depends_on` names `{dep}`, which is not an item")
            if dep in seen:
                add(line, f"`depends_on` lists `{dep}` twice")
            seen.add(dep)
    cycle = _find_cycle(items)
    if cycle:
        first = next((il for it, il in zip(items, item_lines) if it.get("id") == cycle[0]), None)
        add((first or {}).get("depends_on", (first or {}).get("_item", 1)), "dependency cycle: " + " -> ".join(cycle))

    if not found:
        return
    found.sort(key=lambda entry: entry[0])
    shown = found[:MAX_SHOWN]
    message = "\n".join(f"line {line}: {what}" for line, what in shown)
    if len(found) > len(shown):
        message += f"\n... and {len(found) - len(shown)} more"
    raise BacklogError("backlog_invalid", message, _NEXT_CHECK, violations=found)


def parse_and_validate(raw, repo_root, file: str = "the backlog file") -> tuple[dict, dict]:
    """Parse (`bytes` or `str`) and validate; every failure is a
    `BacklogError("backlog_invalid")` whose message names `file` and the lines."""
    try:
        data, lines = parse_backlog_bytes(raw) if isinstance(raw, bytes) else parse_backlog(raw)
    except BacklogFormatError as exc:
        raise BacklogError("backlog_invalid", f"{file}:{exc.line}: {exc.what}", _NEXT_CHECK,
                           violations=[(exc.line, exc.what)]) from None
    try:
        validate_backlog(data, lines, repo_root)
    except BacklogError as exc:
        exc.message = "\n".join(f"{file}:{ln}: {what}" for ln, what in exc.violations[:MAX_SHOWN])
        if len(exc.violations) > MAX_SHOWN:
            exc.message += f"\n... and {len(exc.violations) - MAX_SHOWN} more"
        exc.args = (exc.message,)
        raise
    return data, lines


# --------------------------------------------------------------------------
# Selection (D-Select)
# --------------------------------------------------------------------------


def _status_of(data: dict) -> dict[str, str]:
    return {it["id"]: it["status"] for it in data["items"]}


def _waiting_on(item: dict, status: dict[str, str]) -> list[str]:
    return [d for d in item.get("depends_on", []) if status.get(d) != "done"]


def _summary(item: dict) -> dict:
    return {
        "id": item["id"], "title": item["title"], "status": item["status"], "order": item["order"],
        "depends_on": list(item.get("depends_on", [])), "size": item.get("size"), "detail": item.get("detail"),
    }


def select_next(data: dict) -> dict:
    """D-Select: a ready `in-progress` item first (lowest `order`), else the
    ready `next` item with the lowest `order`; otherwise `no_ready_item`.
    Returns `{"item", "also_in_progress", "warnings"}`."""
    status = _status_of(data)
    ordered = sorted(data["items"], key=lambda it: it["order"])
    ready_progress = [it for it in ordered if it["status"] == "in-progress" and not _waiting_on(it, status)]
    warnings = [
        f"`{it['id']}` is in-progress but waits for {', '.join(_waiting_on(it, status))}"
        for it in ordered if it["status"] == "in-progress" and _waiting_on(it, status)
    ]
    chosen = also = None
    if ready_progress:
        chosen, also = ready_progress[0], [it["id"] for it in ready_progress[1:]]
    else:
        ready_next = [it for it in ordered if it["status"] == "next" and not _waiting_on(it, status)]
        if ready_next:
            chosen, also = ready_next[0], []
    if chosen is not None:
        return {"item": _summary(chosen), "also_in_progress": also, "warnings": warnings}
    candidates = [it for it in ordered if it["status"] in ("next", "in-progress")]
    next_step = "set an item to `next`, or finish what it waits for, then run `/milestone-plan` again"
    if not candidates:
        raise BacklogError("no_ready_item", "no item is `next` or `in-progress`", next_step)
    waiting = "; ".join(f"`{it['id']}` waits for {', '.join(_waiting_on(it, status))}" for it in candidates)
    raise BacklogError("no_ready_item", f"every `next` or `in-progress` item is waiting: {waiting}", next_step)


# --------------------------------------------------------------------------
# Edit (D-Accept)
# --------------------------------------------------------------------------


def _value_span(row: str, key: str) -> tuple[int, int]:
    """Start and end of the value token on a `key: value[ # comment]` line."""
    start = row.index(f"{key}: ") + len(key) + 2
    if row[start] in "\"'":
        _, end = _scan_quoted(row, start, 0)
        return start, end
    idx = row.find(" #", start)
    return start, len(row) if idx < 0 else idx


def set_status_text(text: str, data: dict, lines: dict, item_id: str, status: str,
                    release: str | None = None) -> str:
    """Return `text` with only `item_id`'s `status` line (and, when `release`
    is given, its `release` line) changed; every other byte, comments
    included, is kept. The result is re-parsed and must equal `data` with only
    those fields changed, else nothing is returned (`BacklogError`)."""
    index = next((i for i, it in enumerate(data["items"]) if it["id"] == item_id), None)
    if index is None:
        raise BacklogError("item_unknown", f"`{item_id}` is not an item of the backlog file",
                           "add an item with that id and status `in-progress`")
    if status not in STATUSES:
        raise BacklogError("backlog_invalid", f"`{status}` is not a status", _NEXT_CHECK)
    if release is not None and (status != "done" or not _RELEASE_RE.match(release)):
        raise BacklogError("backlog_invalid", "`release` is only allowed on a `done` item and must match [A-Za-z0-9][A-Za-z0-9._+-]{0,39}", _NEXT_CHECK)
    item, il = data["items"][index], lines["item"][index]
    if status != "done" and "release" in item:
        raise BacklogError("backlog_invalid", f"`{item_id}` has a `release`, which only a `done` item may carry", _NEXT_CHECK)

    expected = copy.deepcopy(data)
    expected["items"][index]["status"] = status
    rows = text.split("\n")
    changed = False
    if item["status"] != status:
        n = il["status"] - 1
        start, end = _value_span(rows[n], "status")
        rows[n] = rows[n][:start] + status + rows[n][end:]
        changed = True
    if release is not None:
        expected["items"][index]["release"] = release
        if item.get("release") != release:
            if "release" in il:
                n = il["release"] - 1
                start, end = _value_span(rows[n], "release")
                rows[n] = rows[n][:start] + f'"{release}"' + rows[n][end:]
            else:
                rows.insert(il["status"], f'    release: "{release}"')
            changed = True
    if not changed:
        return text
    new_text = "\n".join(rows)
    try:
        reparsed, _ = parse_backlog(new_text)
    except BacklogFormatError as exc:
        raise BacklogError("backlog_invalid", f"the edit would break the file at line {exc.line}: {exc.what}", _NEXT_CHECK) from None
    if reparsed != expected:
        raise BacklogError("backlog_invalid", "the edit would change more than `status` and `release`", _NEXT_CHECK)
    return new_text


# --------------------------------------------------------------------------
# The page (D-Generate)
# --------------------------------------------------------------------------

_MD_SPECIAL = "\\|[]<>`"


def _escape_title(title: str) -> str:
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in title)


def render_roadmap(data: dict, file: str, roadmap: str) -> str:
    """The built-in page: deterministic, LF, no timestamps, rows by `order`."""
    if "-->" in file or "\n" in file:
        raise BacklogError("declaration_invalid", "the backlog `file` path cannot be written into the page's comment",
                           "rename the file in the `backlog` key")
    page_dir = posixpath.dirname(roadmap) or "."
    out = [
        "# Roadmap",
        "",
        f"{GENERATED_PREFIX}{file} by scripts/workflow_backlog.py. Do not edit by hand: edit the backlog file and run "
        "`python3 scripts/workflow_backlog.py generate`. -->",
        "",
        "| Order | Item | Status | Size | Depends on |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in sorted(data["items"], key=lambda it: it["order"]):
        title = _escape_title(item["title"])
        if item.get("detail"):
            cell = f"[{title}]({posixpath.relpath(item['detail'], page_dir)}) `{item['id']}`"
        else:
            cell = f"{title} `{item['id']}`"
        word = _STATUS_WORDS[item["status"]]
        if item["status"] == "done" and item.get("release"):
            word = f"Done ({item['release']})"
        size = str(item["size"]) if item.get("size") is not None else "-"
        deps = ", ".join(item.get("depends_on", [])) or "-"
        out.append(f"| {item['order']} | {cell} | {word} | {size} | {deps} |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# The declaration (D-Declare)
# --------------------------------------------------------------------------

CONFIG_REL = "docs/ai-workflow/WORKFLOW_CONFIG.json"
DEFAULT_ROADMAP = "docs/ROADMAP.md"
ARTIFACTS_DIR = "docs/ai-workflow/registry"
REPRESENTATIVE_ITEM_PAGE = DETAIL_PREFIX + "x.md"
GENERATOR_TIMEOUT = 120
_DECLARATION_KEYS = ("file", "roadmap", "generator")
_DECLARATION_NEXT = ("correct the key as `docs/ai-workflow/BACKLOG_FILE.md` says, "
                     "or remove it to leave the backlog")
_RECOVERY = ("then run `python3 scripts/workflow_backlog.py accept {id}` "
             "and continue the acceptance at step 4")
_MISSING = object()


def _declaration_error(message: str) -> BacklogError:
    return BacklogError("declaration_invalid", message, _DECLARATION_NEXT)


def _config_backlog(repo_root):
    """The raw `backlog` value of the config, or `_MISSING` when the config is
    absent, unreadable, unparsable, not an object or has no such key: all of
    those mean undeclared (the script judges nothing else about the file)."""
    try:
        config = json.loads((Path(repo_root) / CONFIG_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _MISSING
    if not isinstance(config, dict) or "backlog" not in config:
        return _MISSING
    return config["backlog"]


def load_declaration(repo_root) -> dict | None:
    """D-Declare: `None` when undeclared, else `{"file", "roadmap",
    "generator"}` (`generator` is `None` or an argument vector); any breach is
    `declaration_invalid`."""
    raw = _config_backlog(repo_root)
    if raw is _MISSING:
        return None
    if not isinstance(raw, dict):
        raise _declaration_error("`backlog` must be an object with `file`, and optionally `roadmap` and `generator`")
    unknown = sorted(set(raw) - set(_DECLARATION_KEYS))
    if unknown:
        raise _declaration_error(f"`backlog` has unknown key(s): {', '.join(unknown)}")
    if "file" not in raw:
        raise _declaration_error("`backlog.file` is required")
    file = raw["file"]
    roadmap = raw.get("roadmap", DEFAULT_ROADMAP)
    for name, value in (("file", file), ("roadmap", roadmap)):
        if not isinstance(value, str):
            raise _declaration_error(f"`backlog.{name}` must be a string path")
        problem = path_problem(repo_root, value)
        if problem:
            raise _declaration_error(f"`backlog.{name}`: {problem}")
    if file == roadmap:
        raise _declaration_error("`backlog.file` and `backlog.roadmap` must differ")
    generator = raw.get("generator")
    if "generator" in raw and (
        not isinstance(generator, list) or not generator
        or not all(isinstance(a, str) and a for a in generator)
    ):
        raise _declaration_error("`backlog.generator` must be a non-empty array of non-empty strings")
    return {"file": file, "roadmap": roadmap, "generator": generator}


def declared_paths(repo_root) -> list[str]:
    """D-Classify's never-raising reader: the declared `file` and `roadmap`,
    or an empty list when undeclared or invalid."""
    try:
        declaration = load_declaration(repo_root)
    except Exception:  # noqa: BLE001 -- never raises, by contract
        return []
    return [] if declaration is None else [declaration["file"], declaration["roadmap"]]


def protected_conflicts(paths, declaration) -> list[tuple[str, str]]:
    """D-Classify's shared predicate: the `(stage, path)` pairs among `paths`,
    plus, for any path under the item-page prefix, the exact protected paths and
    protected prefixes under that prefix, that an artifact `declaration` (its
    `plan_stage` and `implementation_stage` halves) classifies as *protected*.
    A protected entry wins over an excluded one, so such a path could not be
    edited by acceptance."""
    found = []
    under_pages = any(path.startswith(DETAIL_PREFIX) for path in paths)
    for stage in ("plan_stage", "implementation_stage"):
        half = declaration.get(stage) or {}
        exact = set(half.get("protected_paths") or ())
        prefixes = tuple(half.get("protected_prefixes") or ())
        for path in paths:
            if path in exact or (prefixes and path.startswith(prefixes)):
                found.append((stage, path))
        if under_pages:
            # An item page can be any file under the prefix, not only the representative one.
            found.extend((stage, path) for path in sorted(exact)
                         if path.startswith(DETAIL_PREFIX) and (stage, path) not in found)
            found.extend((stage, prefix) for prefix in prefixes
                         if prefix.startswith(DETAIL_PREFIX) and prefix != DETAIL_PREFIX
                         and (stage, prefix) not in found)
    return found


def _conflict_error(conflicts) -> BacklogError:
    named = ", ".join(f"`{path}` ({stage.replace('_', ' ')})" for stage, path in conflicts)
    return BacklogError(
        "declaration_conflict",
        f"the declared backlog paths are protected by the work item's artifact declaration: {named}",
        "move the path out of the protected area in the `backlog` key "
        "(`docs/backlog.yaml` and `docs/ROADMAP.md` are never protected), then retry",
    )


def check_declaration_conflict(repo_root, declaration, work_item_id: str) -> None:
    """`declaration_conflict` against the work item's existing
    `<id>-artifacts.json`; a work item with none (or an unreadable one) is not
    checked."""
    path = Path(repo_root) / ARTIFACTS_DIR / f"{work_item_id}-artifacts.json"
    try:
        artifacts = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(artifacts, dict):
        return
    conflicts = protected_conflicts(
        [declaration["file"], declaration["roadmap"], REPRESENTATIVE_ITEM_PAGE], artifacts)
    if conflicts:
        raise _conflict_error(conflicts)


# --------------------------------------------------------------------------
# Files, snapshots and the page (D-Accept, D-Generate)
# --------------------------------------------------------------------------

_GEN_LINE_RE = re.compile(rb"^<!-- backlog-sha256: ([0-9a-f]{64}) -->$")
_STAMPED_LINE_RE = re.compile(rb"^<!-- backlog-sha256: ([0-9a-f]{64}) page-sha256: ([0-9a-f]{64}) -->$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _abs(repo_root, rel) -> Path:
    return Path(repo_root) / rel


def _read_backlog(repo_root, declaration) -> tuple[bytes, dict, dict]:
    path = _abs(repo_root, declaration["file"])
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        mode = None
    if mode is None or not stat.S_ISREG(mode):
        raise BacklogError(
            "backlog_missing", f"the declared backlog file `{declaration['file']}` does not exist",
            "create the file (`docs/ai-workflow/BACKLOG_FILE.md` has a starter), or remove the key")
    raw = path.read_bytes()
    data, lines = parse_and_validate(raw, repo_root, declaration["file"])
    return raw, data, lines


def _publish_mode(path: Path) -> int:
    """The permission bits a published file carries: those of the regular file
    it replaces, else the process's usual default for a new file."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        info = None
    if info is not None and stat.S_ISREG(info.st_mode):
        return stat.S_IMODE(info.st_mode)
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


class _Snapshot(NamedTuple):
    data: bytes
    mode: int


def _write_atomic(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = _publish_mode(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".backlog-")
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _snapshot(path: Path, shown: str) -> _Snapshot | None:
    """The bytes and permission bits of a regular file, or `None` when nothing
    is there. Anything else at the path is refused before a restore could ever
    remove it."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise _declaration_error(f"`backlog.roadmap` `{shown}` exists and is not a regular file")
    return _Snapshot(path.read_bytes(), stat.S_IMODE(mode))


def _file_snapshot(path: Path, raw: bytes) -> _Snapshot:
    """The backlog file's already-read bytes with its permission bits."""
    return _Snapshot(raw, stat.S_IMODE(os.stat(path).st_mode))


def _clear(path: Path) -> None:
    """Remove whatever is at `path` (file, link or directory), if anything."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.unlink(path)
    except OSError:
        pass


def _restore(path: Path, snapshot: _Snapshot | None) -> None:
    if snapshot is None:
        _clear(path)
        return
    if os.path.lexists(path) and not (os.path.isfile(path) and not os.path.islink(path)):
        _clear(path)
    else:
        try:
            if path.read_bytes() == snapshot.data:
                if stat.S_IMODE(os.stat(path).st_mode) != snapshot.mode:
                    os.chmod(path, snapshot.mode)  # only the mode changed
                return  # untouched: a refusal before any mutation rewrites nothing
        except OSError:
            pass
    _write_atomic(path, snapshot.data, snapshot.mode)


def _split_lines(raw: bytes) -> list[bytes]:
    """Lines split on LF alone, terminators kept; a last line without an LF is
    a line."""
    parts = raw.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _marker_lines(raw: bytes) -> tuple[list[int], list[int], list[bytes]]:
    lines = _split_lines(raw)
    generator, stamped = [], []
    for index, line in enumerate(lines):
        body = line[:-1] if line.endswith(b"\n") else line
        if _GEN_LINE_RE.match(body):
            generator.append(index)
        elif _STAMPED_LINE_RE.match(body):
            stamped.append(index)
    return generator, stamped, lines


def _page_state(repo_root, declaration) -> bytes | None:
    """The page's bytes when it is a regular file, `None` when absent; a
    directory or other non-file at the path is `declaration_invalid`."""
    path = _abs(repo_root, declaration["roadmap"])
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise _declaration_error(f"`backlog.roadmap` `{declaration['roadmap']}` exists and is not a regular file")
    return path.read_bytes()


def _builtin_replaceable(page: bytes | None, declaration, replace: bool) -> None:
    if page is None or replace:
        return
    text = page.decode("utf-8", errors="replace")
    if not any(line.startswith(GENERATED_PREFIX) for line in text.split("\n")):
        raise BacklogError(
            "roadmap_not_generated",
            f"`{declaration['roadmap']}` exists and was not generated by the Workflow",
            "run `python3 scripts/workflow_backlog.py generate --replace` once "
            "(the old page stays in Git history), commit it, retry")


def _generator_failed(message: str) -> BacklogError:
    return BacklogError("generator_failed", message,
                        "fix the generator (the file and the page are restored), "
                        "run `python3 scripts/workflow_backlog.py accept <id>`, "
                        "and continue the acceptance at step 4")


def _run_declared_generator(repo_root, declaration, file_bytes: bytes) -> None:
    """Run the declared generator and enforce the freshness contract, then
    stamp the page. Raises `generator_failed`; restoring is the caller's."""
    digest = _sha(file_bytes)
    env = dict(os.environ, WORKFLOW_BACKLOG_SHA256=digest)
    try:
        done = subprocess.run(declaration["generator"], cwd=str(repo_root), env=env, shell=False,
                              capture_output=True, timeout=GENERATOR_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise _generator_failed(f"the declared generator timed out after {GENERATOR_TIMEOUT} s") from None
    except OSError as exc:
        raise _generator_failed(f"the declared generator could not run: {exc}") from None
    shown = (done.stdout + done.stderr).decode("utf-8", errors="replace").strip()
    if done.returncode != 0:
        raise _generator_failed(f"the declared generator exited {done.returncode}" + (f":\n{shown}" if shown else ""))
    try:
        file_path = _abs(repo_root, declaration["file"])
        unchanged = stat.S_ISREG(os.lstat(file_path).st_mode) and file_path.read_bytes() == file_bytes
    except OSError:
        unchanged = False
    if not unchanged:
        raise _generator_failed("the declared generator changed the backlog file")
    page_path = _abs(repo_root, declaration["roadmap"])
    try:
        mode = os.lstat(page_path).st_mode
    except FileNotFoundError:
        raise _generator_failed(f"the declared generator wrote no page at `{declaration['roadmap']}`") from None
    if not stat.S_ISREG(mode):
        raise _generator_failed(f"`{declaration['roadmap']}` is not a regular file after the generator ran")
    raw = page_path.read_bytes()
    generator_lines, stamped_lines, lines = _marker_lines(raw)
    if stamped_lines:
        raise _generator_failed("the page still carries a stamped `backlog-sha256` line: the generator must write "
                                "its own line, not copy the previous one forward")
    if len(generator_lines) != 1:
        raise _generator_failed("the page must contain exactly one line `<!-- backlog-sha256: <digest> -->` "
                                f"(found {len(generator_lines)}; a carriage-return line does not count)")
    index = generator_lines[0]
    line = lines[index]
    body = line[:-1] if line.endswith(b"\n") else line
    if _GEN_LINE_RE.match(body).group(1).decode() != digest:
        raise _generator_failed("the page's `backlog-sha256` line is not the digest of the current backlog file "
                                "(`WORKFLOW_BACKLOG_SHA256`)")
    page_digest = _sha(b"".join(lines[:index] + lines[index + 1:]))
    terminator = b"\n" if line.endswith(b"\n") else b""
    stamp = f"<!-- backlog-sha256: {digest} page-sha256: {page_digest} -->".encode() + terminator
    lines[index] = stamp
    _write_atomic(page_path, b"".join(lines))


def _generate_page(repo_root, declaration, data, file_bytes: bytes, *, replace: bool = False) -> str:
    """Write the page; returns `"declared"` or `"built-in"`."""
    if declaration["generator"]:
        _page_state(repo_root, declaration)
        _run_declared_generator(repo_root, declaration, file_bytes)
        return "declared"
    _builtin_replaceable(_page_state(repo_root, declaration), declaration, replace)
    text = render_roadmap(data, declaration["file"], declaration["roadmap"])
    _write_atomic(_abs(repo_root, declaration["roadmap"]), text.encode("utf-8"))
    return "built-in"


def _stale(message: str) -> BacklogError:
    return BacklogError("roadmap_stale", message, "run `python3 scripts/workflow_backlog.py generate` and commit the page")


def check_page(repo_root, declaration, data, file_bytes: bytes) -> None:
    """`check`: raises `roadmap_stale` unless the page is fresh. Runs no user
    code: a declared generator is verified from the Workflow's stamp."""
    page_path = _abs(repo_root, declaration["roadmap"])
    try:
        mode = os.lstat(page_path).st_mode
    except FileNotFoundError:
        raise _stale(f"`{declaration['roadmap']}` does not exist") from None
    if not stat.S_ISREG(mode):
        raise _stale(f"`{declaration['roadmap']}` is not a regular file")
    raw = page_path.read_bytes()
    if not declaration["generator"]:
        expected = render_roadmap(data, declaration["file"], declaration["roadmap"]).encode("utf-8")
        if raw != expected:
            have, want = raw.split(b"\n"), expected.split(b"\n")
            first = next((i + 1 for i in range(max(len(have), len(want)))
                          if i >= len(have) or i >= len(want) or have[i] != want[i]), 1)
            raise _stale(f"`{declaration['roadmap']}` differs from the generated page at line {first}")
        return
    generator_lines, stamped_lines, lines = _marker_lines(raw)
    if not stamped_lines:
        raise _stale(f"`{declaration['roadmap']}` has no stamped `backlog-sha256` line"
                     + (" (the marker is not stamped)" if generator_lines else ""))
    if len(stamped_lines) > 1 or generator_lines:
        raise _stale(f"`{declaration['roadmap']}` must carry exactly one stamped `backlog-sha256` line")
    index = stamped_lines[0]
    line = lines[index]
    body = line[:-1] if line.endswith(b"\n") else line
    file_digest, page_digest = (g.decode() for g in _STAMPED_LINE_RE.match(body).groups())
    if file_digest != _sha(file_bytes):
        raise _stale(f"the page was generated from a different `{declaration['file']}` (file digest mismatch)")
    if page_digest != _sha(b"".join(lines[:index] + lines[index + 1:])):
        raise _stale(f"`{declaration['roadmap']}` was edited after it was generated (page digest mismatch)")


# --------------------------------------------------------------------------
# The commands (D-Select, D-Accept, D-Generate)
# --------------------------------------------------------------------------


def _item(data: dict, item_id: str) -> dict:
    for item in data["items"]:
        if item["id"] == item_id:
            return item
    raise BacklogError(
        "item_unknown", f"`{item_id}` is not an item of the backlog file",
        "add an item with that id and status `in-progress` (an item in flight from before 2.17.0 also needs the "
        "backlog paths classified: `docs/ai-workflow/BACKLOG_FILE.md`, \"Items in flight at adoption\")")


def _acceptable(data: dict, item_id: str) -> dict:
    item = _item(data, item_id)
    if item["status"] == "idea":
        raise BacklogError("status_not_acceptable", f"`{item_id}` is an `idea` and cannot be accepted",
                           "set the item to `next`, then accept")
    return item


def _generator_resolvable(repo_root, declaration) -> None:
    argv0 = declaration["generator"][0]
    if "/" in argv0:
        candidate = _abs(repo_root, argv0)
        ok = candidate.is_file() and os.access(candidate, os.X_OK)
    else:
        ok = shutil.which(argv0) is not None
    if not ok:
        raise _generator_failed(f"the declared generator `{argv0}` cannot be found or executed")


def cmd_status(repo_root) -> dict:
    try:
        declaration = load_declaration(repo_root)
    except BacklogError as exc:
        return {"declared": True, "valid": False,
                "error": {"code": exc.code, "message": exc.message, "next_step": exc.next_step}}
    if declaration is None:
        return {"declared": False}
    out = {"declared": True, "file": declaration["file"], "roadmap": declaration["roadmap"],
           "generator": declaration["generator"]}
    try:
        raw, data, _ = _read_backlog(repo_root, declaration)
    except BacklogError as exc:
        out.update(valid=False, error={"code": exc.code, "message": exc.message, "next_step": exc.next_step})
        return out
    counts = {status: 0 for status in STATUSES}
    for item in data["items"]:
        counts[item["status"]] += 1
    out.update(valid=True, items=len(data["items"]), counts=counts)
    try:
        check_page(repo_root, declaration, data, raw)
        out["roadmap_fresh"] = True
    except BacklogError as exc:
        out.update(roadmap_fresh=False, roadmap_problem=exc.message)
    return out


def cmd_next(repo_root) -> dict:
    declaration = load_declaration(repo_root)
    if declaration is None:
        return {"declared": False}
    _, data, _ = _read_backlog(repo_root, declaration)
    return {"declared": True, "file": declaration["file"], **select_next(data)}


def cmd_preflight_accept(repo_root, item_id: str) -> dict:
    declaration = load_declaration(repo_root)
    if declaration is None:
        return {"declared": False}
    check_declaration_conflict(repo_root, declaration, item_id)
    raw, data, lines = _read_backlog(repo_root, declaration)
    item = _acceptable(data, item_id)
    set_status_text(raw.decode("utf-8"), data, lines, item_id, "done")
    if declaration["generator"]:
        try:
            _generator_resolvable(repo_root, declaration)
        except BacklogError as exc:
            exc.next_step = exc.next_step.replace("<id>", item_id)
            raise
        _page_state(repo_root, declaration)
    else:
        _builtin_replaceable(_page_state(repo_root, declaration), declaration, False)
    return {"declared": True, "ok": True, "item": item_id, "status": item["status"],
            "paths": [declaration["file"], declaration["roadmap"]]}


def _with_recovery(exc: BacklogError, item_id: str) -> BacklogError:
    if "continue the acceptance at step 4" not in exc.next_step:
        exc.next_step = f"{exc.next_step}, {_RECOVERY.format(id=item_id)}"
    exc.next_step = exc.next_step.replace("<id>", item_id)
    return exc


def cmd_accept(repo_root, item_id: str, release: str | None = None) -> dict:
    snapshots = None
    try:
        declaration = load_declaration(repo_root)
        if declaration is None:
            return {"declared": False}
        file_path, page_path = _abs(repo_root, declaration["file"]), _abs(repo_root, declaration["roadmap"])
        check_declaration_conflict(repo_root, declaration, item_id)
        raw, data, lines = _read_backlog(repo_root, declaration)
        _acceptable(data, item_id)
        new_text = set_status_text(raw.decode("utf-8"), data, lines, item_id, "done", release)
        new_bytes = new_text.encode("utf-8")
        new_data, _ = parse_and_validate(new_bytes, repo_root, declaration["file"])
        if not declaration["generator"]:
            _builtin_replaceable(_page_state(repo_root, declaration), declaration, False)
        snapshots = (_file_snapshot(file_path, raw), _snapshot(page_path, declaration["roadmap"]))
        if new_bytes != raw:
            _write_atomic(file_path, new_bytes)
        _generate_page(repo_root, declaration, new_data, new_bytes)
    except BacklogError as exc:
        if snapshots is not None:
            _restore(file_path, snapshots[0])
            _restore(page_path, snapshots[1])
        raise _with_recovery(exc, item_id) from None
    except BaseException:
        if snapshots is not None:
            _restore(file_path, snapshots[0])
            _restore(page_path, snapshots[1])
        raise
    out = {"declared": True, "item": item_id, "status": "done",
           "paths": [declaration["file"], declaration["roadmap"]]}
    if release is not None:
        out["release"] = release
    return out


def cmd_generate(repo_root, replace: bool = False) -> dict:
    declaration = load_declaration(repo_root)
    if declaration is None:
        return {"declared": False}
    raw, data, _ = _read_backlog(repo_root, declaration)
    file_path, page_path = _abs(repo_root, declaration["file"]), _abs(repo_root, declaration["roadmap"])
    file_snapshot = _file_snapshot(file_path, raw)
    snapshot = _snapshot(page_path, declaration["roadmap"])
    try:
        kind = _generate_page(repo_root, declaration, data, raw, replace=replace)
    except BaseException:
        _restore(file_path, file_snapshot)
        _restore(page_path, snapshot)
        raise
    return {"declared": True, "generated": declaration["roadmap"], "generator": kind}


def cmd_check(repo_root) -> dict:
    declaration = load_declaration(repo_root)
    if declaration is None:
        return {"declared": False}
    raw, data, _ = _read_backlog(repo_root, declaration)
    check_page(repo_root, declaration, data, raw)
    return {"declared": True, "fresh": True, "roadmap": declaration["roadmap"]}


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors are exit 2, like a refusal
        self.print_usage(sys.stderr)
        sys.stderr.write(f"error: usage: {message}\n")
        raise SystemExit(2)


def _release_arg(value: str) -> str:
    if not _RELEASE_RE.match(value):
        raise argparse.ArgumentTypeError("`--release` must match [A-Za-z0-9][A-Za-z0-9._+-]{0,39}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="workflow_backlog.py", description="The structured backlog file (workflow-2.17.0).")
    parser.add_argument("--repo-root", default=None, help="the repository root (default: the working directory's git top level, else the working directory)")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    sub.add_parser("status")
    sub.add_parser("next")
    sub.add_parser("check")
    pre = sub.add_parser("preflight-accept")
    pre.add_argument("work_item_id")
    acc = sub.add_parser("accept")
    acc.add_argument("work_item_id")
    acc.add_argument("--release", type=_release_arg, default=None)
    gen = sub.add_parser("generate")
    gen.add_argument("--replace", action="store_true")
    return parser


def _default_root() -> Path:
    """The working directory's git top level, else the working directory.

    Independent of where this file sits, so a copy made outside `scripts/` (the
    documented move back to 2.16.0) reads the repository's own declaration.
    """
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
        top = done.stdout.strip()
        if done.returncode == 0 and top:
            return Path(top)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.cwd()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root) if args.repo_root else _default_root()
    try:
        if args.command == "status":
            result = cmd_status(root)
        elif args.command == "next":
            result = cmd_next(root)
        elif args.command == "check":
            result = cmd_check(root)
        elif args.command == "preflight-accept":
            result = cmd_preflight_accept(root, args.work_item_id)
        elif args.command == "accept":
            result = cmd_accept(root, args.work_item_id, args.release)
        else:
            result = cmd_generate(root, args.replace)
    except BacklogError as exc:
        sys.stderr.write(f"error: {exc.code}: {exc.message}\nnext step: {exc.next_step}\n")
        return 1 if exc.code == "roadmap_stale" else 2
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
