# state_writer: false
"""The Workflow's one fixed GitHub query and the single parser of its output
(workflow-2.8.0, `gate-policy-and-reopening`, D-GP-Trust and D-GP-Invalidation).

CI results and pull-request facts that satisfy a gate come only from GitHub,
queried by the Workflow itself with the one argv below. Nothing in a
repository file, a policy or the environment can alter that argv, and the `gh`
that runs is resolved to an absolute path that is refused inside the
repository, a worktree, the temporary directory or a world-writable
directory. An orchestrator's reported fact goes through the same parser
(`parse_forge_raw`) but is only ever tighten-only (it can trigger this query,
never decide).

Stdlib-only; it never imports another Workflow module and writes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

#: A full page of results is undecidable: `gh pr list` truncates at `--limit`,
#: so a green match could be taken as unique while a red one is omitted.
FORGE_PR_LIST_LIMIT = 200
FORGE_QUERY_NAME = "pr-list-v1"
FORGE_TIMEOUT_SECONDS = 30
_JSON_FIELDS = "number,url,state,headRefOid,reviewDecision,reviews,statusCheckRollup"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REMOTE_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$")
_PR_STATES = {"OPEN": "open", "CLOSED": "closed", "MERGED": "merged"}
_DECISIONS = {"APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"}
_FAILED_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
_PENDING_STATUSES = {"QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"}


class ForgeError(Exception):
    """A refusal that names a stable `code`; nothing is stored when one is raised."""
    code = "forge_error"


class ForgeUnavailableError(ForgeError):
    """`gh` is missing, unauthenticated, failed, timed out or printed non-JSON."""
    code = "forge_unavailable"


class ForgeUndecidableError(ForgeError):
    """The answer cannot decide: two open pull requests, a full page, or an
    unsafe `gh` location."""
    code = "forge_undecidable"


class ForgeProvenanceRequiredError(ForgeError):
    code = "forge_provenance_required"


class ForgeDigestMismatchError(ForgeError):
    code = "forge_digest_mismatch"


class ForgeRepositoryMismatchError(ForgeError):
    code = "forge_repository_mismatch"


class ForgeUnparseableError(ForgeError):
    code = "forge_unparseable"


def gh_argv(repository: str, queried_commit: str, gh_path: str = "gh") -> list[str]:
    """The one fixed query, as an argv (`--limit` explicit, no shell)."""
    return [gh_path, "pr", "list", "--repo", repository, "--state", "all", "--search", queried_commit,
            "--limit", str(FORGE_PR_LIST_LIMIT), "--json", _JSON_FIELDS]


def _git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)
    except OSError as exc:
        raise ForgeUnavailableError(f"git could not run: {exc}") from exc
    if result.returncode != 0:
        raise ForgeUnavailableError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def origin_repository(repo_root: Path) -> str:
    """`<owner>/<name>` of `origin`; it must be a `github.com` repository."""
    url = _git(repo_root, "remote", "get-url", "origin").strip()
    match = _REMOTE_RE.match(url)
    if not match or not _REPOSITORY_RE.match(match.group("repo")):
        raise ForgeUnavailableError(f"origin {url!r} is not a github.com repository")
    return match.group("repo")


def _under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _world_writable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & 0o002)
    except OSError:
        return True


def _temp_roots() -> list[Path]:
    roots = {tempfile.gettempdir(), os.environ.get("TMPDIR") or "/tmp", "/tmp", "/var/tmp", "/dev/shm"}
    return [Path(os.path.realpath(root)) for root in roots if root]


def _worktree_roots(repo_root: Path) -> list[Path]:
    roots = [Path(os.path.realpath(repo_root))]
    try:
        listing = _git(repo_root, "worktree", "list", "--porcelain")
    except ForgeError:
        return roots
    for line in listing.splitlines():
        if line.startswith("worktree "):
            roots.append(Path(os.path.realpath(line[len("worktree "):])))
    return roots


def resolve_gh(repo_root: Path) -> dict:
    """`{path, sha256}` of the first `gh` on `PATH`, symlinks resolved. A `gh`
    that is not found is `forge_unavailable`; one whose resolved executable or
    directory is inside the repository, any worktree, the temporary directory
    or a world-writable directory (or is itself world-writable) is
    `forge_undecidable`, naming the path. The executable is never run here."""
    import shutil

    found = shutil.which("gh")
    if found is None:
        raise ForgeUnavailableError("gh is not installed (not found on PATH)")
    resolved = Path(os.path.realpath(found))
    directory = resolved.parent
    for label, roots in (("the repository or a worktree", _worktree_roots(Path(repo_root))),
                         ("the temporary directory", _temp_roots())):
        if any(_under(resolved, root) for root in roots):
            raise ForgeUndecidableError(f"the resolved gh {str(resolved)!r} is inside {label}")
    if _world_writable(directory) or _world_writable(resolved):
        raise ForgeUndecidableError(
            f"the resolved gh {str(resolved)!r} is world-writable or in a world-writable directory")
    return {"path": str(resolved), "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()}


def _default_run(argv: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise ForgeUnavailableError(f"gh timed out after {timeout}s") from exc
    except OSError as exc:
        raise ForgeUnavailableError(f"gh could not run: {exc}") from exc
    if result.returncode != 0:
        raise ForgeUnavailableError(f"gh exited {result.returncode}: {result.stderr.strip()[:300]}")
    return result.stdout


def _checks(rollup) -> dict:
    """`{state, failing}`: `failure` when any check failed, else `pending` when
    any is unfinished or none reported (fail closed), else `success`."""
    failing: list[str] = []
    pending = False
    entries = rollup if isinstance(rollup, list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ForgeUnparseableError("a statusCheckRollup entry is not an object")
        name = str(entry.get("name") or entry.get("context") or "unnamed")
        conclusion = str(entry.get("conclusion") or entry.get("state") or "").upper()
        status = str(entry.get("status") or "").upper()
        if conclusion in _FAILED_CONCLUSIONS or conclusion in {"FAILURE", "ERROR"}:
            failing.append(name)
        elif status in _PENDING_STATUSES or conclusion in _PENDING_STATUSES or conclusion == "EXPECTED":
            pending = True
        elif not conclusion or status not in {"", "COMPLETED"}:
            pending = True  # an entry with no conclusion, or an unfinished status, is not a pass
    if failing:
        state = "failure"
    elif pending or not entries:
        state = "pending"
    else:
        state = "success"
    return {"state": state, "failing": sorted(set(failing))}


def _derive(record: dict) -> dict:
    state = _PR_STATES.get(str(record.get("state")))
    head = record.get("headRefOid")
    if state is None or not isinstance(head, str) or not _COMMIT_RE.match(head):
        raise ForgeUnparseableError("a pull request record has no usable state or headRefOid")
    number, url = record.get("number"), record.get("url")
    if not isinstance(number, int) or isinstance(number, bool) or not isinstance(url, str):
        raise ForgeUnparseableError("a pull request record has no number or url")
    reviews = record.get("reviews") or []
    if not isinstance(reviews, list):
        raise ForgeUnparseableError("reviews is not a list")
    requested = [r for r in reviews if isinstance(r, dict) and r.get("state") == "CHANGES_REQUESTED"]
    findings = requested[-1].get("body") if requested else None
    decision = record.get("reviewDecision") or None
    if decision is not None and decision not in _DECISIONS:
        raise ForgeUnparseableError(f"unknown reviewDecision {decision!r}")
    # The latest review that carries the decision, so a later COMMENTED review
    # neither makes an approval of an earlier head current nor re-keys a
    # changes request; with no such review, the latest review of any kind.
    carrying = [r for r in reviews if isinstance(r, dict) and r.get("state") == decision]
    latest = carrying[-1] if carrying else (reviews[-1] if reviews else None)
    reviewed_head = review_id = None
    if isinstance(latest, dict):
        commit = latest.get("commit") or {}
        reviewed_head = commit.get("oid") if isinstance(commit, dict) else None
        review_id = latest.get("id")
    # `review_decision` stays as GitHub reports it: a decision on an earlier head is
    # not softened here (D-GP-Acceptance requirement 5); the consumers that need a
    # current decision compare `reviewed_head` with `head` themselves.
    return {
        "pr": {"number": number, "url": url}, "state": state, "head": head, "review_decision": decision,
        "reviewed_head": reviewed_head, "checks": _checks(record.get("statusCheckRollup")),
        "review_id": review_id, "findings": findings if decision == "CHANGES_REQUESTED" else None,
    }


NO_PULL_REQUEST = {"pr": None, "state": "none", "head": None, "review_decision": None, "reviewed_head": None,
                   "checks": {"state": "pending", "failing": []}, "review_id": None, "findings": None}


def parse_forge_raw(raw: str, repository: str, queried_commit: str) -> dict:
    """The single parser both sources use: derives the fact from the verbatim
    stdout of the fixed query. A full page (200 or more records) and more than
    one open pull request are `forge_undecidable`; unparseable input is
    `forge_unparseable`. With no open record the newest closed or merged one
    stands; with none at all the fact is `state: none`. Nothing the reporter
    derived is read."""
    if not _REPOSITORY_RE.match(repository or ""):
        raise ForgeRepositoryMismatchError(f"repository {repository!r} is not <owner>/<name>")
    if not _COMMIT_RE.match(queried_commit or ""):
        raise ForgeUnparseableError(f"queried_commit {queried_commit!r} is not 40 hex")
    try:
        records = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ForgeUnparseableError(f"the query output is not JSON: {exc}") from exc
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ForgeUnparseableError("the query output is not a list of objects")
    if len(records) >= FORGE_PR_LIST_LIMIT:
        raise ForgeUndecidableError(
            f"the answer is a full page ({len(records)} >= {FORGE_PR_LIST_LIMIT}); more pull requests may match")
    opened = [r for r in records if r.get("state") == "OPEN"]
    if len(opened) > 1:
        raise ForgeUndecidableError(
            f"{len(opened)} open pull requests carry {queried_commit}: {sorted(r.get('number') for r in opened)}")
    if opened:
        return _derive(opened[0])
    if not records:
        return json.loads(json.dumps(NO_PULL_REQUEST))
    numbered = [r for r in records if isinstance(r.get("number"), int)]
    if len(numbered) != len(records):
        raise ForgeUnparseableError("a pull request record has no number")
    return _derive(max(numbered, key=lambda r: r["number"]))


def query_forge_pr_facts(repo_root: Path, queried_commit: str, *, run=_default_run, resolve=None) -> dict:
    """Runs the fixed query for `queried_commit` and returns `{facts, raw,
    raw_sha256, repository, queried_commit, gh_path, gh_sha256}`. `run(argv,
    timeout) -> stdout` is the test seam (the module's own default runs the
    absolute path, no shell, 30 seconds); `resolve` defaults to `resolve_gh`.
    Writes nothing and raises a `ForgeError` that names its code."""
    repository = origin_repository(Path(repo_root))
    gh = (resolve or resolve_gh)(Path(repo_root))
    raw = run(gh_argv(repository, queried_commit, gh["path"]), FORGE_TIMEOUT_SECONDS)
    facts = parse_forge_raw(raw, repository, queried_commit)
    return {"facts": facts, "raw": raw, "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "repository": repository, "queried_commit": queried_commit,
            "gh_path": gh["path"], "gh_sha256": gh["sha256"]}
