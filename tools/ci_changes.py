#!/usr/bin/env python3
"""Decide whether a pull request changes only documentation. Stdlib only.

``tests.yml`` runs the plan and the test shards only when a pull request
changes something a test can read. This file is the decision (CI1a, #317);
``tools/ci_workflows.py`` wires it into the generated workflow.

Subcommands:

``detect``
    Writes ``code=true`` or ``code=false`` to ``GITHUB_OUTPUT`` (and prints
    it). ``false`` for three events (CI2d, #317), and only for these:

    * a ``pull_request`` whose merge ref changes paths that are all
      documentation;
    * a ``push`` whose pushed range ``before..HEAD`` changes paths that are
      all documentation (``before`` a real commit, ``after`` equal to
      ``HEAD``);
    * a Draft ``pull_request`` whose head holds no work item that *entered* a
      review-bound phase (``REVIEW_BOUND_PHASES``) relative to the base, so
      the shards run from the first head the acceptance gate can read.

    Everything else, and any doubt, is ``true``: a manual dispatch, a force
    push, a new branch, an unreadable payload, a ``HEAD`` that is not a merge
    commit, an empty or unreadable diff or state file, a failed ``git`` call.
``require``
    The exit rule of the aggregate job ``tests-result``: exit 0 only when the
    ``changes`` job and the ``docs`` job succeeded and ``code`` is exactly
    ``true`` or ``false``. An empty or unknown ``code`` (the ``changes`` job
    failed or was cancelled) fails.

``hide``
    Removes the documentation set from the working tree (CI2e, #322). It
    lists the tracked files ``is_documentation`` accepts, deletes them and
    marks each ``skip-worktree``, so ``git status`` stays clean. The ``tests``
    job runs it before the shards: a shard that passes without those files
    proves, by running, that no test it ran needs them. ``--dry-run`` lists
    the paths and touches nothing. It refuses outside a Git work tree. The
    runner checkout is disposable, so nothing restores the files; run it
    only in a scratch clone.

The documentation set below is a list of paths that no test the shards run
needs as input. The ``hide`` step proves it by running (CI2e, #322): the
shards run with the set absent, so a test that reads one fails there, and the
path then leaves the set rather than the test leaving the shards (D3).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

CODE, DOCS = "code", "docs"

#: Directories whose every file is documentation, each with its reason.
DOCS_PREFIXES: tuple[tuple[str, str], ...] = (
    ("docs/items/", "work-item pages: narrative records no test reads"),
    ("docs/releases/", "release notes: narrative records no test reads"),
)

#: The top-level pages ``docs/*.md``, except the files named below.
DOCS_TOP_LEVEL = re.compile(r"^docs/[^/]+\.md$")

#: Top-level pages that stay code, each with the reader that makes them so.
DOCS_TOP_LEVEL_EXCEPTIONS: tuple[tuple[str, str], ...] = (
    ("docs/ACTIVE_MILESTONE.md", "read and parsed by the Workflow scripts and the tests"),
    ("docs/roadmap-archive.md", "tests/test_functional_sandbox.py pins exact phrases of it"),
)

#: Single files that are documentation, each with its reason.
DOCS_FILES: tuple[tuple[str, str], ...] = (
    ("docs/backlog.yaml", "the backlog: read only by the documentation gate (tests.test_backlog)"),
)

#: What stays code although it looks like documentation, with the reader (the
#: classifier needs no entry for these: they are simply not in the set).
NOT_DOCUMENTATION: tuple[tuple[str, str], ...] = (
    ("docs/guide/", "tests/test_plan_document_consistency.py parses every guide; "
                    "tests/test_cli.py and tests/test_functional_sandbox.py read two of them"),
    ("docs/adr/", "tests/test_plan_document_consistency.py reads ADRs 0001 and 0002"),
    ("docs/ai-workflow/", "workflow state, plans and registries: tests and the Workflow scripts parse them"),
    ("docs/milestones/", "milestone narratives the Workflow scripts read"),
    ("README.md", "named by the documentation checks and the workflow tests"),
)


#: Work-item phases from which a Draft pull request runs the shards (D2): the
#: first head the acceptance gate's ``ci_green`` can read, at or before the
#: anchor commit. Review-stage writes stay uncommitted, so the committed phase
#: at the anchor is most often ``APPLYING_REVIEW_FEEDBACK``.
REVIEW_BOUND_PHASES = frozenset({
    "SELF_REVIEWING_IMPLEMENTATION",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK",
    "AWAITING_FUNCTIONAL_REVIEW",
    "FIXING_FUNCTIONAL_FINDINGS",
    "AWAITING_USER_ACCEPTANCE",
    "AWAITING_TECHNICAL_APPROVAL",
    "MILESTONE_COMPLETE",
})

#: The phases with no signal. Together with ``REVIEW_BOUND_PHASES`` they
#: partition ``workflow_state.KNOWN_PHASES`` (a test asserts it, so a new
#: phase cannot be skipped silently); no phase order is used.
EXCLUDED_PHASES = frozenset({
    "PLANNING",
    "SELF_REVIEWING_PLAN",
    "REVISING_PLAN",
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
    "AMENDING_PLAN",
    "IMPLEMENTING",
    "LEGACY_READY",
})

STATE_FILE = "docs/ai-workflow/WORKFLOW_STATE.json"
ZERO_ID = "0" * 40


def _normal(path: str) -> str | None:
    """The path as a repository-relative POSIX path, or ``None`` when it is
    not one (absolute, backslashes, ``..`` or empty segments, a NUL)."""
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return path


def is_documentation(path: str) -> bool:
    """Whether one repository-relative path is in the documentation set."""
    path = _normal(path)
    if path is None:
        return False
    if any(path.startswith(prefix) and len(path) > len(prefix) for prefix, _ in DOCS_PREFIXES):
        return True
    if path in {name for name, _ in DOCS_FILES}:
        return True
    if DOCS_TOP_LEVEL.match(path):
        return path not in {name for name, _ in DOCS_TOP_LEVEL_EXCEPTIONS}
    return False


def classify(paths: Sequence[str]) -> str:
    """``docs`` only for a non-empty list wholly inside the documentation set;
    ``code`` for everything else, including the empty list."""
    paths = list(paths)
    if paths and all(is_documentation(path) for path in paths):
        return DOCS
    return CODE


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------

class DetectError(Exception):
    """The diff cannot be trusted; the caller falls back to ``code``."""


def _git(args: list[str], cwd: Path | None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise DetectError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def changed_paths(cwd: Path | None = None, git: Callable[[list[str], Path | None], str] = _git,
                  base: str | None = None) -> list[str]:
    """Every path the change introduces, a rename counted under both names.
    Without ``base`` it is the merge commit ``HEAD`` over its first parent
    (the merge ref of a pull request; anything else is a
    :class:`DetectError`). With ``base`` it is the range ``base..HEAD`` (a
    push)."""
    if base is None:
        parents = git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd).split()
        if len(parents) < 3:
            raise DetectError("HEAD is not a merge commit")
        base = "HEAD^1"
    raw = git(["diff", "--name-only", "--no-renames", "-z", base, "HEAD"], cwd)
    return [name for name in raw.split("\0") if name]


def read_event(env: dict[str, str]) -> dict:
    """The event payload at ``GITHUB_EVENT_PATH``; ``{}`` when it is missing,
    unreadable or not a JSON object."""
    target = env.get("GITHUB_EVENT_PATH")
    if not target:
        return {}
    try:
        with open(target, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _phases(text: str) -> dict[str, str]:
    """``{work item id: phase}`` from a ``WORKFLOW_STATE.json`` text."""
    items = json.loads(text)["work_items"]
    if isinstance(items, dict):
        items = [{"work_item_id": key, **value} for key, value in items.items()]
    return {str(item["work_item_id"]): str(item.get("phase")) for item in items}


def review_bound_item(cwd: Path | None, git: Callable[[list[str], Path | None], str]) -> str | None:
    """The id of a work item the head holds at a review-bound phase while the
    base lacks it or holds it outside that set; ``None`` when there is none.
    Any unreadable state is a :class:`DetectError`."""
    parents = git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd).split()
    if len(parents) < 3:
        raise DetectError("HEAD is not a merge commit")
    try:
        head = _phases(git(["show", f"HEAD^2:{STATE_FILE}"], cwd))
        base = _phases(git(["show", f"HEAD^1:{STATE_FILE}"], cwd))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise DetectError(f"the state file is unreadable: {error!r}") from error
    for item, phase in sorted(head.items()):
        if phase in REVIEW_BOUND_PHASES and base.get(item) not in REVIEW_BOUND_PHASES:
            return item
    return None


def _decide_docs(paths: list[str], what: str) -> tuple[str, str]:
    if not paths:
        return "true", "the diff is empty: everything runs"
    if classify(paths) == DOCS:
        return "false", f"{what}: {len(paths)} changed path(s), all documentation"
    other = next(path for path in paths if not is_documentation(path))
    return "true", f"{other} is not documentation: everything runs"


def _detect_push(env: dict[str, str], cwd: Path | None, git) -> tuple[str, str]:
    event = read_event(env)
    before, after = event.get("before"), event.get("after")
    if not isinstance(before, str) or not isinstance(after, str) or not before or not after:
        return "true", "the push payload has no before/after: everything runs"
    if set(before) == {"0"}:
        return "true", "the push has no previous commit (a new branch): everything runs"
    try:
        head = git(["rev-parse", "--verify", "HEAD^{commit}"], cwd).strip()
        if head != after:
            return "true", "the pushed head is not the checked-out commit: everything runs"
        git(["rev-parse", "--verify", f"{before}^{{commit}}"], cwd)
        paths = changed_paths(cwd, git, base=before)
    except Exception as error:  # a force-push, a git failure: any doubt runs everything
        return "true", f"{error}: everything runs"
    return _decide_docs(paths, "push")


def detect(env: dict[str, str] | None = None, cwd: Path | None = None,
           git: Callable[[list[str], Path | None], str] = _git) -> tuple[str, str]:
    """``("true"|"false", reason)``; see the module docstring for the three
    events that can be ``false``."""
    env = os.environ if env is None else env
    event = env.get("GITHUB_EVENT_NAME", "")
    if event == "push":
        return _detect_push(env, cwd, git)
    if event != "pull_request":
        return "true", f"event {event or '(unset)'!r} is not a pull request or a push: everything runs"
    pull_request = read_event(env).get("pull_request")
    draft = isinstance(pull_request, dict) and pull_request.get("draft") is True
    if draft:
        try:
            item = review_bound_item(cwd, git)
        except Exception as error:  # any doubt runs everything
            return "true", f"{error}: everything runs"
        if item is None:
            return "false", "Draft pull request, no work item has reached a review-bound phase yet"
        return "true", f"Draft pull request, work item {item} reached a review-bound phase: everything runs"
    try:
        paths = changed_paths(cwd, git)
    except Exception as error:  # any doubt runs everything
        return "true", f"{error}: everything runs"
    return _decide_docs(paths, "pull request")


def _write_output(name: str, value: str, env: dict[str, str]) -> None:
    target = env.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _write_summary(text: str, env: dict[str, str]) -> None:
    target = env.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def cmd_detect(args: argparse.Namespace, env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    code, reason = detect(env)
    print(f"code={code} ({reason})")
    _write_output("code", code, env)
    _write_summary(f"Change detection: `code={code}` ({reason}).", env)
    return 0


# ---------------------------------------------------------------------------
# hide
# ---------------------------------------------------------------------------

#: Paths handed to one ``git update-index`` call, to stay under the argument limit.
HIDE_CHUNK = 200


def hidden_paths(cwd: Path | None = None, git: Callable[[list[str], Path | None], str] = _git) -> list[str]:
    """The tracked files of the work tree at ``cwd`` that are documentation.
    Raises :class:`DetectError` outside a Git work tree."""
    if git(["rev-parse", "--is-inside-work-tree"], cwd).strip() != "true":
        raise DetectError("not inside a Git work tree")
    raw = git(["ls-files", "-z"], cwd)
    return [name for name in raw.split("\0") if name and is_documentation(name)]


def cmd_hide(args: argparse.Namespace, cwd: Path | None = None) -> int:
    try:
        top = Path(_git(["rev-parse", "--show-toplevel"], cwd).strip())
        paths = hidden_paths(top)
    except DetectError as error:
        print(f"hide: {error}", file=sys.stderr)
        return 1
    if args.dry_run:
        for name in paths:
            print(name)
        print(f"would hide {len(paths)} documentation paths")
        return 0
    for start in range(0, len(paths), HIDE_CHUNK):
        _git(["update-index", "--skip-worktree", "--", *paths[start:start + HIDE_CHUNK]], top)
    for name in paths:
        target = top / name
        target.unlink(missing_ok=True)
        parent = target.parent
        while parent != top:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    print(f"hid {len(paths)} documentation paths")
    return 0


# ---------------------------------------------------------------------------
# require
# ---------------------------------------------------------------------------

def require(changes: str, docs: str, code: str) -> tuple[bool, str]:
    """The exit rule of ``tests-result``: ``(ok, message)``."""
    if changes != "success":
        return False, f"the changes job did not succeed ({changes or 'no result'})"
    if docs != "success":
        return False, f"the docs job did not succeed ({docs or 'no result'})"
    if code not in ("true", "false"):
        return False, f"the changes job produced no usable code output ({code!r})"
    return True, f"changes and docs succeeded, code={code}"


def cmd_require(args: argparse.Namespace) -> int:
    ok, message = require(args.changes, args.docs, args.code)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="write the code output for this pull request").set_defaults(func=cmd_detect)
    hide = sub.add_parser("hide", help="remove the documentation set from the work tree (scratch clones and CI only)")
    hide.add_argument("--dry-run", action="store_true", help="list the paths and touch nothing")
    hide.set_defaults(func=cmd_hide)
    req = sub.add_parser("require", help="fail unless the gate jobs succeeded and code is true or false")
    req.add_argument("--changes", default="", help="result of the changes job")
    req.add_argument("--docs", default="", help="result of the docs job")
    req.add_argument("--code", default="", help="the changes job's code output")
    req.set_defaults(func=cmd_require)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
