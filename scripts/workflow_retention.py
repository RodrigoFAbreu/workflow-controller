#!/usr/bin/env python3
# state_writer: false
"""The retention report (workflow-2.20.0, `retention-report`, D-Surface,
D-Finished, D-Report, D-Output, D-Writes).

Read-only: which work items of this repository are finished by the rule of
`docs/RETENTION.md`, how large each one's untracked `.ai-review/<id>/` folder
is, and whether an archive of it exists outside the repository and is older
than the time to live.

    python3 -I scripts/workflow_retention.py report [--json] [--archive-root DIR]
                                                    [--ttl-days N] [--repo-root DIR]

It writes nothing: no archive, no removal, no state write, no stored fact. An
item is finished only when a fresh pull-request query says `merged`; every other
outcome, a failed query included, puts it in `not_finished` with a reason, and
an item that is not at `MILESTONE_COMPLETE` is never queried.

Bytecode. Run as a script (the form above) the module sets
`sys.dont_write_bytecode` before it imports `workflow_forge`, so no `__pycache__`
is written. When imported for `build_report`, the caller must set
`sys.dont_write_bytecode = True` (or run with `-B`, or set
`PYTHONDONTWRITEBYTECODE`) before the first import of this module: the cache
file is written while the import compiles the module, before any assignment
the module makes. An import without it may leave `__pycache__` in the scripts
directory, by Python's doing and not by a run of the report.

Stdlib-only plus `workflow_forge`.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402

import workflow_forge  # noqa: E402

SCHEMA_VERSION = 1
KEEP_RECENT = 3
DEFAULT_TTL_DAYS = 30
MAX_TTL_DAYS = 3650
COMPLETE = "MILESTONE_COMPLETE"
STATE_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"
ARCHIVE_SUFFIX = ".tar.zst"
DEFAULT_ARCHIVE_ROOT = "~/.local/share/workflow-archive"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class RetentionError(Exception):
    """A refusal carrying a stable code, what is wrong and the one next step."""

    def __init__(self, code: str, message: str, next_step: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RetentionError("usage", message, "run `python3 scripts/workflow_retention.py report --help`")


def _iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_state(repo_root: Path) -> dict:
    path = repo_root / STATE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RetentionError("state_missing", f"{STATE_PATH} does not exist in {repo_root}",
                             "run it in a Workflow repository, or pass --repo-root") from None
    except OSError as exc:
        raise RetentionError("state_invalid", f"{STATE_PATH} cannot be read: {exc}",
                             "repair the state file") from None
    try:
        state = json.loads(text)
    except ValueError as exc:
        raise RetentionError("state_invalid", f"{STATE_PATH} is not JSON: {exc}", "repair the state file") from None
    if not isinstance(state, dict) or not isinstance(state.get("work_items"), dict):
        raise RetentionError("state_invalid", f"{STATE_PATH} has no work_items object", "repair the state file")
    return state


def _anchor(item: dict) -> str | None:
    """The commit `/apply-pr-review` queries (`workflow_gate_policy.anchor_of`'s
    own two-field condition, without its review-identity computation)."""
    approval = item.get("technical_approval")
    commit = None
    if isinstance(approval, dict) and approval.get("reviewed_content_commit") \
            and approval.get("approved_review_content_id"):
        commit = approval["reviewed_content_commit"]
    elif item.get("reviewed_implementation_head"):
        commit = item["reviewed_implementation_head"]
    return commit if isinstance(commit, str) and _COMMIT_RE.match(commit) else None


def _tracked_counts(repo_root: Path) -> dict[str, int]:
    """Paths under `.ai-review/<id>/` that Git tracks, per id (one call)."""
    try:
        result = subprocess.run(["git", "ls-files", "-z", "--", ".ai-review"], cwd=repo_root,
                                capture_output=True, text=True)
    except OSError as exc:
        raise RetentionError("repository_unknown", f"git could not run: {exc}", "install Git") from None
    if result.returncode != 0:
        raise RetentionError("repository_unknown", f"git ls-files failed: {result.stderr.strip()}",
                             "run it inside a Git repository, or pass --repo-root")
    counts: dict[str, int] = {}
    for path in result.stdout.split("\0"):
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == ".ai-review":
            counts[parts[1]] = counts.get(parts[1], 0) + 1
    return counts


def _unreadable(errors: dict[str, str], path, exc: OSError) -> None:
    """Records a read failure that is not plain absence, once per path."""
    errors.setdefault(str(path), f"{type(exc).__name__}: {exc.strerror or exc}")


def _absent(exc: OSError) -> bool:
    return isinstance(exc, (FileNotFoundError, NotADirectoryError))


def _review_folder(repo_root: Path, item_id: str, tracked: dict[str, int], errors: dict[str, str]) -> dict:
    folder = repo_root / ".ai-review" / item_id
    files = size = 0
    present = False
    complete = True
    try:
        present = stat.S_ISDIR(os.lstat(folder).st_mode)
    except OSError as exc:
        if not _absent(exc):
            _unreadable(errors, folder, exc)
            complete = False
    if present:
        def walk_failed(exc: OSError) -> None:
            nonlocal complete
            _unreadable(errors, exc.filename or folder, exc)
            complete = False

        for directory, _, names in os.walk(folder, onerror=walk_failed, followlinks=False):
            for name in names:
                full = os.path.join(directory, name)
                try:
                    info = os.lstat(full)
                except OSError as exc:
                    if not _absent(exc):
                        _unreadable(errors, full, exc)
                        complete = False
                    continue
                if stat.S_ISREG(info.st_mode):
                    files += 1
                    size += info.st_size
    return {"present": present, "complete": complete, "files": files, "bytes": size,
            "tracked_files": tracked.get(item_id, 0)}


def _archive_fact(path: Path, now: datetime.datetime, ttl_days: int, errors: dict[str, str]) -> dict:
    """`readable` is false when the archive could not be looked at, which is not
    the same as absent: its `present` then says nothing."""
    absent = {"present": False, "readable": True, "bytes": None, "modified": None, "age_days": None,
              "prunable": False}
    try:
        info = os.lstat(path)
    except OSError as exc:
        if not _absent(exc):
            _unreadable(errors, path, exc)
            return {**absent, "readable": False}
        return absent
    if not stat.S_ISREG(info.st_mode):
        return absent
    modified = datetime.datetime.fromtimestamp(info.st_mtime, datetime.timezone.utc)
    age = max(0, int((now - modified).total_seconds() // 86400))
    return {"present": True, "readable": True, "bytes": info.st_size, "modified": _iso(modified), "age_days": age,
            "prunable": age > ttl_days}


def _query(repo_root: Path, commit: str, run, resolve) -> dict | str:
    """The fresh facts, or the `query_failed:<code>` reason."""
    kwargs = {}
    if run is not None:
        kwargs["run"] = run
    if resolve is not None:
        kwargs["resolve"] = resolve
    try:
        return workflow_forge.query_forge_pr_facts(repo_root, commit, **kwargs)["facts"]
    except workflow_forge.ForgeError as exc:
        return f"query_failed:{exc.code}"
    except Exception:  # noqa: BLE001 - one failed query must not stop the others
        return "query_failed:unexpected"


def build_report(repo_root, *, archive_root, ttl_days: int = DEFAULT_TTL_DAYS, now: datetime.datetime,
                 run=None, resolve=None) -> dict:
    repo_root = Path(repo_root)
    state = _load_state(repo_root)
    try:
        repository = workflow_forge.origin_repository(repo_root)
    except workflow_forge.ForgeError as exc:
        raise RetentionError("repository_unknown", str(exc),
                             "add a github.com `origin` remote, or pass --repo-root") from None
    archive_dir = Path(archive_root).expanduser() / repository.split("/", 1)[1]
    items = state["work_items"]

    def phase_of(key):
        entry = items.get(key)
        return entry.get("phase") if isinstance(entry, dict) else None

    children: dict[str, list] = {}
    for key, entry in items.items():
        parent = entry.get("parent_work_item_id") if isinstance(entry, dict) else None
        if isinstance(parent, str):
            children.setdefault(parent, []).append(key)

    not_finished: list[dict] = []
    candidates: list[tuple[str, str, dict]] = []

    def reject(key, entry, reason):
        phase = entry.get("phase") if isinstance(entry, dict) else None
        not_finished.append({"work_item_id": key, "phase": phase if isinstance(phase, str) else None,
                             "reason": reason})

    for key, entry in items.items():
        if not _ID_RE.match(key):
            reject(key, entry, "invalid_id")
        elif not isinstance(entry, dict):
            reject(key, entry, "invalid_entry")
        elif entry.get("phase") != COMPLETE:
            reject(key, entry, "open_phase")
        else:
            parent = entry.get("parent_work_item_id")
            if (parent is not None and phase_of(parent) != COMPLETE) \
                    or any(phase_of(child) != COMPLETE for child in children.get(key, ())):
                reject(key, entry, "family_open")
            elif _anchor(entry) is None:
                reject(key, entry, "no_anchor")
            else:
                last = entry.get("last_transition")
                candidates.append((last if isinstance(last, str) else "", key, entry))

    candidates.sort(key=lambda c: c[1])
    candidates.sort(key=lambda c: c[0], reverse=True)
    merged: list[tuple[str, str, dict, dict]] = []
    for last, key, entry in candidates:
        facts = _query(repo_root, _anchor(entry), run, resolve)
        if isinstance(facts, str):
            reject(key, entry, facts)
        elif facts.get("state") == "merged":
            merged.append((last, key, entry, facts))
        elif facts.get("state") == "none":
            reject(key, entry, "no_pull_request")
        else:
            reject(key, entry, f"pr_not_merged:{facts.get('state')}")

    tracked = _tracked_counts(repo_root)
    errors: dict[str, str] = {}

    def listing(last, key, facts):
        pr = facts.get("pr") if isinstance(facts.get("pr"), dict) else {}
        return {"work_item_id": key, "last_transition": last or None,
                "pull_request": {"number": pr.get("number"), "url": pr.get("url")},
                "review_folder": _review_folder(repo_root, key, tracked, errors)}

    kept_recent = [listing(last, key, facts) for last, key, _, facts in merged[:KEEP_RECENT]]
    finished = []
    for last, key, _, facts in merged[KEEP_RECENT:]:
        record = listing(last, key, facts)
        record["archive"] = _archive_fact(archive_dir / f"{key}{ARCHIVE_SUFFIX}", now, ttl_days, errors)
        finished.append(record)

    classes = {record["work_item_id"]: "finished" for record in finished}
    classes.update({record["work_item_id"]: "recent" for record in kept_recent})
    classes.update({record["work_item_id"]: "open" for record in not_finished})
    archives = []
    names: list[str] = []
    try:
        if stat.S_ISDIR(os.lstat(archive_dir).st_mode):
            names = os.listdir(archive_dir)
    except OSError as exc:
        if not _absent(exc):
            _unreadable(errors, archive_dir, exc)
    for name in names:
        if name.endswith(ARCHIVE_SUFFIX) and name != ARCHIVE_SUFFIX:
            fact = _archive_fact(archive_dir / name, now, ttl_days, errors)
            fact.pop("readable")
            if not fact.pop("present"):
                continue
            stem = name[:-len(ARCHIVE_SUFFIX)]
            archives.append({"name": name, "work_item_id": stem, **fact, "item": classes.get(stem, "other")})
    archives.sort(key=lambda a: a["name"])
    not_finished.sort(key=lambda r: r["work_item_id"])

    return {
        "schema_version": SCHEMA_VERSION, "kind": "retention-report", "generated_at": _iso(now),
        "repository": repository, "archive_dir": str(archive_dir), "ttl_days": ttl_days,
        "keep_recent": KEEP_RECENT, "finished": finished, "kept_recent": kept_recent,
        "not_finished": not_finished, "archives": archives, "complete": not errors,
        "read_errors": [{"path": path, "error": errors[path]} for path in sorted(errors)],
        "totals": {"finished": len(finished),
                   "finished_folder_bytes": sum(r["review_folder"]["bytes"] for r in finished),
                   "archives": len(archives), "prunable": sum(1 for a in archives if a["prunable"])},
    }


def _size(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{count} B"


def render_text(report: dict) -> str:
    lines = [f"Retention report for {report['repository']} ({report['generated_at']})",
             f"Archive directory: {report['archive_dir']}",
             f"Time to live: {report['ttl_days']} days; the {report['keep_recent']} most recent merged items are kept",
             "", f"Finished ({len(report['finished'])}):"]
    for record in report["finished"]:
        folder, archive = record["review_folder"], record["archive"]
        if archive["present"]:
            state = f"archive {_size(archive['bytes'])}, {archive['age_days']} days old" \
                    + (", prunable" if archive["prunable"] else "")
        else:
            state = "no archive"
        shown = f"{_size(folder['bytes'])} in {folder['files']} files" if folder["present"] else "no review folder"
        lines.append(f"  {record['work_item_id']}: {shown}; {state}")
        if folder["tracked_files"]:
            lines.append(f"    {folder['tracked_files']} tracked file(s) under .ai-review/{record['work_item_id']}/")
    if not report["finished"]:
        lines.append("  none")
    kept = ", ".join(r["work_item_id"] for r in report["kept_recent"]) or "none"
    lines += ["", f"Kept as recent ({len(report['kept_recent'])}): {kept}", "",
              f"Not finished ({len(report['not_finished'])}):"]
    reasons: dict[str, int] = {}
    for record in report["not_finished"]:
        reasons[record["reason"]] = reasons.get(record["reason"], 0) + 1
    lines += [f"  {reason}: {count}" for reason, count in sorted(reasons.items())] or ["  none"]
    prunable = [a for a in report["archives"] if a["prunable"]]
    lines += ["", f"Archives ({len(report['archives'])}), prunable ({len(prunable)}):"]
    lines += [f"  {a['name']}: {_size(a['bytes'])}, {a['age_days']} days old, {a['item']}" for a in prunable] \
        or ["  none prunable"]
    if not report["complete"]:
        lines += ["", f"INCOMPLETE: {len(report['read_errors'])} path(s) could not be read; the sizes, archives and "
                      "absences above are not reliable for them:"]
        lines += [f"  {e['path']}: {e['error']}" for e in report["read_errors"]]
    return "\n".join(lines) + "\n"


def _parse(argv) -> argparse.Namespace:
    parser = _Parser(prog="workflow_retention.py", description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", parser_class=_Parser)
    report = commands.add_parser("report", help="print the retention report (writes nothing)")
    report.add_argument("--json", action="store_true", help="print one JSON object")
    report.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT)
    report.add_argument("--ttl-days", default=str(DEFAULT_TTL_DAYS))
    report.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    if args.command != "report":
        raise RetentionError("usage", "a command is required", "run `workflow_retention.py report`")
    if not re.fullmatch(r"[0-9]{1,5}", args.ttl_days) or not 1 <= int(args.ttl_days) <= MAX_TTL_DAYS:
        raise RetentionError("usage", f"--ttl-days must be an integer from 1 to {MAX_TTL_DAYS}, got {args.ttl_days!r}",
                             "pass --ttl-days 30")
    args.ttl_days = int(args.ttl_days)
    return args


def main(argv=None, *, run=None, resolve=None, now: datetime.datetime | None = None) -> int:
    try:
        args = _parse(sys.argv[1:] if argv is None else list(argv))
        moment = now or datetime.datetime.now(datetime.timezone.utc)
        report = build_report(args.repo_root, archive_root=args.archive_root, ttl_days=args.ttl_days,
                              now=moment, run=run, resolve=resolve)
    except RetentionError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        print(f"next step: {exc.next_step}", file=sys.stderr)
        return 2
    if args.json:
        sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_text(report))
    if not report["complete"]:
        print(f"warning: incomplete: {len(report['read_errors'])} path(s) could not be read "
              "(see read_errors); a missing size or archive may be unreadable, not absent", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
