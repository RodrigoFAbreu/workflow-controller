"""The GitHub boundary (``workflow-controller-trunk-branch-pr-release-
orchestration`` CP3, "GitHub boundary").

:class:`Forge` is the protocol the milestone-branch and release code talks
to; :class:`GhForge` is its one implementation, over the ``gh`` CLI. Every
call names the policy's repository explicitly (``--repo OWNER/NAME``; ``gh
repo view``, which has no ``--repo`` flag, takes it positionally), so a
checkout with several remotes can never resolve to an upstream or a fork.

There is deliberately **no** merge, close, delete or comment operation, and
no ``gh api`` (I3; ``tests/test_no_rewrite_invariants`` scans for the
spellings). A human merges every pull request. The one pull request edit,
:meth:`GhForge.edit_pr`, sets the title and body a squash merge carries to
the trunk (``workflow-controller-squash-merge-tag-versioning`` CP5).

Every read whose result cannot be classified -- a non-zero exit that is not
one of ``gh``'s documented outcomes, authentication or network failure, a
server error, a timeout, a missing ``gh``, or output that does not parse as
the documented shape -- raises :class:`~controller.errors.ForgeUndecidableError`
(I9). It is never read as "absent". A failed mutation is undecidable too:
the caller re-reads before deciding anything.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from .errors import ForgeError, ForgeUndecidableError
from .gitrepo import Runner, subprocess_runner

PR_FIELDS = ("number,state,isDraft,headRefName,headRefOid,baseRefName,isCrossRepository,url,"
             "mergedAt,mergeCommit,title,body")
RELEASE_FIELDS = "tagName,isDraft,assets,url"
CHECK_FIELDS = "name,state,bucket"

#: ``gh pr list`` truncates at ``--limit``; a full page is undecidable.
#: The built-in default of the ``forge.pr_list_limit`` setting.
PR_LIST_LIMIT = 200

#: The process-wide ``forge.pr_list_limit``, set once by ``cli.main``
#: through ``settings.apply_process_defaults`` (settings-and-telemetry
#: CP2); ``None`` is :data:`PR_LIST_LIMIT`.
process_pr_list_limit: int | None = None


def pr_list_limit() -> int:
    """The ``gh pr list --limit`` in force, read at call time."""
    return PR_LIST_LIMIT if process_pr_list_limit is None else process_pr_list_limit

#: The closed ``bucket`` set ``gh pr checks --json`` documents (``gh``
#: 2.101.0). Anything else is undecidable, never guessed into one of these.
CHECK_BUCKETS = frozenset({"pass", "fail", "pending", "skipping", "cancel"})
PR_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})

#: ``gh pr checks``'s exit code for "some checks are still pending".
GH_EXIT_CHECKS_PENDING = 8
_NO_CHECKS_MESSAGE = "no checks reported"
_RELEASE_NOT_FOUND_MESSAGE = "release not found"

_OID_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


@dataclasses.dataclass(frozen=True)
class RepositoryIdentity:
    name_with_owner: str
    url: str


@dataclasses.dataclass(frozen=True)
class PullRequest:
    number: int
    state: str  # OPEN, CLOSED or MERGED
    is_draft: bool
    head_ref: str
    head_oid: str
    base_ref: str
    is_cross_repository: bool
    url: str
    merged_at: str | None
    merge_commit: str | None
    title: str = ""
    body: str = ""


def same_text(a: str, b: str) -> bool:
    """Whether two pull request texts are the same once GitHub's own
    normalisations are undone: CRLF line ends, and trailing newlines."""
    return re.sub("\r\n", "\n", a).rstrip("\n") == re.sub("\r\n", "\n", b).rstrip("\n")


@dataclasses.dataclass(frozen=True)
class Check:
    name: str
    state: str
    bucket: str


@dataclasses.dataclass(frozen=True)
class Checks:
    """``outcome`` is ``"checks"`` (``checks`` holds each one) or
    ``"no_checks"`` (``gh`` reported none; ``checks`` is empty)."""

    outcome: str
    checks: tuple[Check, ...] = ()


@dataclasses.dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int


@dataclasses.dataclass(frozen=True)
class Release:
    tag: str
    is_draft: bool
    url: str
    assets: tuple[ReleaseAsset, ...]


class Forge(Protocol):
    repository: str

    def repository_identity(self) -> RepositoryIdentity: ...
    def list_prs(self, head: str) -> list[PullRequest]: ...
    def view_pr(self, number: int) -> PullRequest: ...
    def create_draft_pr(self, head: str, base: str, title: str, body: str) -> PullRequest: ...
    def mark_ready(self, number: int) -> None: ...
    def edit_pr(self, number: int, *, title: str | None = None, body: str | None = None) -> PullRequest: ...
    def pr_checks(self, number: int) -> Checks: ...
    def view_release(self, tag: str) -> Release | None: ...
    def create_release(self, tag: str, files: Sequence[Path], title: str, notes: str) -> None: ...
    def upload_assets(self, tag: str, files: Sequence[Path]) -> None: ...
    def publish_draft(self, tag: str) -> None: ...
    def download_assets(self, tag: str, directory: Path) -> None: ...


def _undecidable(message: str, argv: Sequence[str], result: subprocess.CompletedProcess | None = None,
                 **evidence: Any) -> ForgeUndecidableError:
    details: dict[str, Any] = {"argv": list(argv)}
    if result is not None:
        details.update(exit_code=result.returncode, stderr=_text(result.stderr).strip(),
                       stdout=_text(result.stdout)[:2000])
    details.update(evidence)
    return ForgeUndecidableError(message, evidence=details)


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def _field(obj: Any, key: str, kind: type | tuple[type, ...], argv: Sequence[str], *,
           nullable: bool = False) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise _undecidable(f"gh output lacks {key!r}", argv, record=obj)
    value = obj[key]
    if value is None and nullable:
        return None
    # ``bool`` is an ``int``; never accept one for the other.
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise _undecidable(f"gh output has {key!r} = {value!r}, not {kind}", argv, record=obj)
    return value


class GhForge:
    """The ``gh`` implementation of :class:`Forge` for ``repository``
    (``OWNER/NAME``). ``runner`` executes an argv whose first element is
    ``gh`` (default: the real one on ``PATH``)."""

    def __init__(self, repository: str, runner: Runner | None = None, *, gh: str = "gh") -> None:
        self.repository = repository
        self._runner = runner or subprocess_runner()
        self._gh = gh

    # -- plumbing ----------------------------------------------------------

    def _call(self, args: Sequence[str], *, repo_flag: bool = True) -> tuple[list[str], subprocess.CompletedProcess]:
        argv = [self._gh, *args, *(["--repo", self.repository] if repo_flag else [])]
        try:
            return argv, self._runner(argv)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _undecidable(f"gh could not run: {exc}", argv) from None

    def _ok(self, args: Sequence[str], **kw) -> tuple[list[str], str]:
        argv, result = self._call(args, **kw)
        if result.returncode != 0:
            raise _undecidable(f"gh {' '.join(args[:2])} failed (exit {result.returncode})",
                               argv, result)
        return argv, _text(result.stdout)

    @staticmethod
    def _json(argv: Sequence[str], text: str, kind: type) -> Any:
        try:
            value = json.loads(text)
        except ValueError:
            raise _undecidable("gh output is not JSON", argv, stdout=text[:2000]) from None
        if not isinstance(value, kind):
            raise _undecidable(f"gh output is not a JSON {kind.__name__}", argv, stdout=text[:2000])
        return value

    def _pr(self, argv: Sequence[str], obj: Any) -> PullRequest:
        state = _field(obj, "state", str, argv)
        head_oid = _field(obj, "headRefOid", str, argv)
        if state not in PR_STATES or not _OID_RE.match(head_oid):
            raise _undecidable("gh returned an unexpected pull request record", argv, record=obj)
        merge = _field(obj, "mergeCommit", dict, argv, nullable=True)
        merge_oid = None if merge is None else _field(merge, "oid", str, argv)
        if merge_oid is not None and not _OID_RE.match(merge_oid):
            raise _undecidable("gh returned an unexpected merge commit", argv, record=obj)
        return PullRequest(
            number=_field(obj, "number", int, argv), state=state,
            is_draft=_field(obj, "isDraft", bool, argv),
            head_ref=_field(obj, "headRefName", str, argv), head_oid=head_oid,
            base_ref=_field(obj, "baseRefName", str, argv),
            is_cross_repository=_field(obj, "isCrossRepository", bool, argv),
            url=_field(obj, "url", str, argv),
            merged_at=_field(obj, "mergedAt", str, argv, nullable=True) or None,
            merge_commit=merge_oid,
            title=_field(obj, "title", str, argv),
            body=_field(obj, "body", str, argv),
        )

    # -- repository --------------------------------------------------------

    def repository_identity(self) -> RepositoryIdentity:
        argv, out = self._ok(["repo", "view", self.repository, "--json", "nameWithOwner,url"],
                             repo_flag=False)
        obj = self._json(argv, out, dict)
        return RepositoryIdentity(_field(obj, "nameWithOwner", str, argv), _field(obj, "url", str, argv))

    def verify_repository(self) -> RepositoryIdentity:
        """:meth:`repository_identity`, refusing unless it is the policy's."""
        identity = self.repository_identity()
        if identity.name_with_owner.lower() != self.repository.lower():
            raise ForgeError(f"gh resolves {self.repository} to {identity.name_with_owner}",
                             evidence={"expected": self.repository,
                                       "actual": identity.name_with_owner, "url": identity.url})
        return identity

    # -- pull requests -----------------------------------------------------

    def list_prs(self, head: str) -> list[PullRequest]:
        """Every pull request, in any state, whose head branch is named
        ``head`` (``gh`` filters by name only). The base and repository are
        the caller's identity checks to make, not a filter here: a PR with
        the right head and the wrong base must still be seen."""
        limit = pr_list_limit()
        argv, out = self._ok(["pr", "list", "--head", head, "--state", "all", "--json", PR_FIELDS,
                              "--limit", str(limit)])
        records = self._json(argv, out, list)
        if len(records) >= limit:
            raise _undecidable(f"gh pr list returned a full page of {limit}; the list may "
                               f"be truncated", argv)
        prs = [self._pr(argv, record) for record in records]
        if any(pr.head_ref != head for pr in prs):
            raise _undecidable(f"gh pr list --head {head} returned another head", argv)
        return prs

    def view_pr(self, number: int) -> PullRequest:
        argv, out = self._ok(["pr", "view", str(number), "--json", PR_FIELDS])
        pr = self._pr(argv, self._json(argv, out, dict))
        if pr.number != number:
            raise _undecidable(f"gh pr view {number} returned #{pr.number}", argv)
        return pr

    def create_draft_pr(self, head: str, base: str, title: str, body: str) -> PullRequest:
        """Create a Draft PR from ``head`` into ``base`` and return it, as
        re-read with :meth:`view_pr` from the number in the returned URL."""
        argv, out = self._ok(["pr", "create", "--draft", "--head", head, "--base", base,
                              "--title", title, "--body", body])
        urls = [line.strip() for line in out.splitlines() if line.strip()]
        match = re.fullmatch(r"https://[^/\s]+/([^/\s]+/[^/\s]+)/pull/([0-9]+)", urls[-1]) if urls else None
        if match is None:
            raise _undecidable("gh pr create did not print a pull request URL", argv, stdout=out)
        if match.group(1).lower() != self.repository.lower():
            raise ForgeError(f"gh pr create made {urls[-1]}, outside {self.repository}",
                             evidence={"argv": argv, "url": urls[-1], "expected": self.repository})
        return self.view_pr(int(match.group(2)))

    def mark_ready(self, number: int) -> None:
        self._ok(["pr", "ready", str(number)])

    def edit_pr(self, number: int, *, title: str | None = None, body: str | None = None) -> PullRequest:
        """Set pull request ``number``'s title and/or body (``gh pr edit``),
        then re-read it with :meth:`view_pr` and refuse unless the re-read
        shows the new values. Returns the re-read pull request."""
        if title is None and body is None:
            raise ValueError("edit_pr needs a title or a body")
        args = ["pr", "edit", str(number)]
        if title is not None:
            args += ["--title", title]
        if body is not None:
            args += ["--body", body]
        argv, _ = self._ok(args)
        pr = self.view_pr(number)
        if (title is not None and pr.title != title) or (body is not None and not same_text(pr.body, body)):
            raise ForgeError(f"gh pr edit {number} did not take: the re-read pull request shows other values",
                             evidence={"argv": argv, "title": pr.title, "body": pr.body,
                                       "expected_title": title, "expected_body": body})
        return pr

    def pr_checks(self, number: int) -> Checks:
        """The PR's checks. ``gh`` reports normal outcomes through its exit
        code: 0 (all done), 8 (some pending) or 1 (some failed) with a JSON
        array are the checks; 1 with the "no checks reported" message and no
        JSON is ``no_checks``; anything else is undecidable."""
        argv, result = self._call(["pr", "checks", str(number), "--json", CHECK_FIELDS])
        out, err = _text(result.stdout), _text(result.stderr)
        records = None
        if out.strip():
            try:
                records = json.loads(out)
            except ValueError:
                records = None
        if result.returncode in (0, GH_EXIT_CHECKS_PENDING, 1) and isinstance(records, list):
            checks = []
            for record in records:
                check = Check(_field(record, "name", str, argv), _field(record, "state", str, argv),
                              _field(record, "bucket", str, argv))
                if check.bucket not in CHECK_BUCKETS:
                    raise _undecidable(f"check {check.name!r} has the undocumented bucket "
                                       f"{check.bucket!r}", argv, result)
                checks.append(check)
            return Checks("checks", tuple(checks))
        if result.returncode == 1 and not out.strip() and _NO_CHECKS_MESSAGE in err:
            return Checks("no_checks")
        raise _undecidable(f"gh pr checks {number} is undecidable (exit {result.returncode})", argv, result)

    # -- releases ----------------------------------------------------------

    def view_release(self, tag: str) -> Release | None:
        """The release for ``tag``, or ``None`` when ``gh`` says it does not
        exist."""
        argv, result = self._call(["release", "view", tag, "--json", RELEASE_FIELDS])
        if result.returncode == 1 and not _text(result.stdout).strip() \
                and _RELEASE_NOT_FOUND_MESSAGE in _text(result.stderr):
            return None
        if result.returncode != 0:
            raise _undecidable(f"gh release view {tag} failed (exit {result.returncode})", argv, result)
        obj = self._json(argv, _text(result.stdout), dict)
        assets = []
        for asset in _field(obj, "assets", list, argv):
            assets.append(ReleaseAsset(_field(asset, "name", str, argv), _field(asset, "size", int, argv)))
        release = Release(tag=_field(obj, "tagName", str, argv), is_draft=_field(obj, "isDraft", bool, argv),
                          url=_field(obj, "url", str, argv), assets=tuple(assets))
        if release.tag != tag:
            raise _undecidable(f"gh release view {tag} returned {release.tag}", argv)
        return release

    def create_release(self, tag: str, files: Sequence[Path], title: str, notes: str) -> None:
        """Create and publish the release for the existing remote tag
        ``tag`` with ``files`` attached (``--verify-tag``; never a draft,
        never ``--clobber``)."""
        self._ok(["release", "create", tag, *(str(f) for f in files), "--verify-tag",
                  "--title", title, "--notes", notes])

    def upload_assets(self, tag: str, files: Sequence[Path]) -> None:
        """Attach ``files`` to ``tag``'s release, which must be a draft.
        Never replaces an existing asset."""
        release = self.view_release(tag)
        if release is None or not release.is_draft:
            raise ForgeError(f"refusing to upload to {tag}: "
                             f"{'no such release' if release is None else 'it is not a draft'}",
                             evidence={"tag": tag, "exists": release is not None})
        self._ok(["release", "upload", tag, *(str(f) for f in files)])

    def publish_draft(self, tag: str) -> None:
        self._ok(["release", "edit", tag, "--draft=false"])

    def download_assets(self, tag: str, directory: Path) -> None:
        """Download every asset of ``tag``'s release into ``directory``;
        ``gh`` refuses to overwrite an existing file."""
        self._ok(["release", "download", tag, "--dir", str(directory)])
