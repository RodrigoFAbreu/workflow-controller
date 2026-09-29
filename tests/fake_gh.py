#!/usr/bin/env python3
"""A hermetic, offline stand-in for the real ``gh`` binary, used by the
forge, release and milestone-branch tests (``workflow-controller-trunk-
branch-pr-release-orchestration`` CP3).

``tests/fixtures.py``'s ``fake_gh_env`` puts this script first on ``PATH``
as ``gh``. Like ``fake_claude.py`` it is driven by environment variables;
unlike it, it keeps state between invocations -- pull requests and releases
live in a JSON state file, and branches and tags are read, through Git, from
the disposable bare origin. It models only the ``gh`` surface
``controller.forge`` uses, and the GitHub behaviours the Controller's
decisions depend on:

- ``pr create`` fails, as GitHub does, when the head has no commit beyond
  the base in the origin (``git rev-list --count <base>..<head>`` is 0), and
  when an open PR with the same head and base already exists;
- ``pr list --head`` filters by head branch *name* over every stored PR,
  closed and merged ones included;
- an open PR's ``headRefOid`` follows its head branch in the origin;
- ``pr checks`` reproduces ``gh``'s exit codes: 8 with JSON when a check is
  pending, 1 with JSON when one failed, and 1 with the "no checks reported"
  message and no JSON when the PR has none (``"checks": null``);
- ``pr edit`` sets the stored ``title``/``body``; a closed or merged PR
  cannot be edited;
- ``release create --verify-tag`` fails when the origin lacks the tag, and
  ``release upload``/``release download`` never overwrite.

There is no ``pr merge``: any command outside the modelled surface exits 1.
A human merge is simulated by editing the state file directly.

Environment variables:

``FAKE_GH_STATE`` (required)
    The JSON state file: ``repository`` (``OWNER/NAME``), ``url``,
    ``next_number``, ``prs`` and ``releases`` (see :func:`initial_state`).
    Release assets are stored beside it, under ``assets/<tag>/``.
``FAKE_GH_ORIGIN``
    The bare origin repository branches and tags are read from.
``FAKE_GH_LOG``
    One JSON line (the argv after ``gh``) is appended per invocation, before
    anything else -- including invocations that then fail.
``FAKE_GH_FAIL``
    A JSON object mapping ``"<group> <subcommand>"`` (or ``"*"``) to a
    failure: ``auth``, ``network``, ``server_error``, ``malformed`` (exit 0
    with non-JSON output), ``malformed:<exit>`` (that exit, non-JSON output
    and no stderr message).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PR_JSON_FIELDS = ("number", "state", "isDraft", "headRefName", "headRefOid", "baseRefName",
                  "isCrossRepository", "url", "mergedAt", "mergeCommit", "title", "body")
#: ``gh pr checks``'s exit code for "some checks are still pending".
GH_EXIT_PENDING = 8

_VALUE_FLAGS = {"--repo": "repo", "-R": "repo", "--json": "json", "--head": "head", "-H": "head",
                "--base": "base", "-B": "base", "--state": "state", "-s": "state",
                "--limit": "limit", "-L": "limit", "--title": "title", "-t": "title",
                "--body": "body", "-b": "body", "--notes": "notes", "-n": "notes",
                "--dir": "dir", "-D": "dir"}
_BOOL_FLAGS = {"--draft": "draft", "-d": "draft", "--verify-tag": "verify_tag", "--clobber": "clobber"}


def initial_state(repository: str) -> dict:
    return {"repository": repository, "url": f"https://github.com/{repository}",
            "next_number": 1, "prs": [], "releases": []}


def read_state(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def write_state(path: Path, state: dict) -> None:
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)


def invocations(log_path: Path) -> list[list[str]]:
    path = Path(log_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class _Exit(Exception):
    def __init__(self, code: int, stderr: str = "", stdout: str = "") -> None:
        super().__init__(code)
        self.code, self.stderr, self.stdout = code, stderr, stdout


def _parse(args: list[str]) -> tuple[list[str], dict]:
    positional, opts = [], {}
    i = 0
    while i < len(args):
        arg = args[i]
        name, eq, inline = arg.partition("=")
        if name in _VALUE_FLAGS:
            if eq:
                opts[_VALUE_FLAGS[name]] = inline
            else:
                if i + 1 >= len(args):
                    raise _Exit(2, f"flag needs an argument: {arg}\n")
                opts[_VALUE_FLAGS[name]] = args[i + 1]
                i += 1
        elif name in _BOOL_FLAGS:
            opts[_BOOL_FLAGS[name]] = (inline != "false") if eq else True
        elif arg.startswith("-"):
            raise _Exit(2, f"unknown flag: {arg}\n")
        else:
            positional.append(arg)
        i += 1
    return positional, opts


def _git(origin: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "--git-dir", origin, *args], capture_output=True, text=True, check=False)


def _origin_commit(ref: str) -> str | None:
    origin = os.environ.get("FAKE_GH_ORIGIN")
    if not origin:
        return None
    result = _git(origin, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def _select(record: dict, fields: str | None) -> dict:
    if fields is None:
        raise _Exit(1, "fake gh: only --json output is modelled\n")
    out = {}
    for field in fields.split(","):
        if field not in record:
            raise _Exit(1, f'Unknown JSON field: "{field}"\n')
        out[field] = record[field]
    return out


def _require_repo(state: dict, opts: dict) -> None:
    repo = opts.get("repo")
    if repo is None:
        raise _Exit(2, "fake gh: --repo is required (the Controller must never infer the repository)\n")
    if repo.lower() != state["repository"].lower():
        raise _Exit(1, f"GraphQL: Could not resolve to a Repository with the name '{repo}'. (repository)\n")


def _pr_view(pr: dict) -> dict:
    view = {key: pr[key] for key in PR_JSON_FIELDS}
    if pr["state"] == "OPEN":
        head = _origin_commit(f"refs/heads/{pr['headRefName']}")
        if head is not None:
            view["headRefOid"] = head
    return view


def _find_pr(state: dict, number: str) -> dict:
    for pr in state["prs"]:
        if str(pr["number"]) == number:
            return pr
    raise _Exit(1, f"GraphQL: Could not resolve to a PullRequest with the number of {number}. (repository.pullRequest)\n")


def _find_release(state: dict, tag: str) -> dict:
    for release in state["releases"]:
        if release["tagName"] == tag:
            return release
    raise _Exit(1, "release not found\n")


def _assets_dir(tag: str) -> Path:
    return Path(os.environ["FAKE_GH_STATE"]).parent / "assets" / tag


def _add_assets(release: dict, files: list[str], *, clobber: bool) -> None:
    for file in files:
        src = Path(file)
        if not src.is_file():
            raise _Exit(1, f"open {file}: no such file or directory\n")
        if any(a["name"] == src.name for a in release["assets"]) and not clobber:
            raise _Exit(1, f"asset under the same name already exists: [{src.name}]\n")
        dest = _assets_dir(release["tagName"]) / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        release["assets"] = [a for a in release["assets"] if a["name"] != src.name]
        release["assets"].append({"name": src.name, "size": src.stat().st_size, "state": "uploaded"})


def _run(argv: list[str], state: dict) -> tuple[str, bool]:
    """``(stdout, state_changed)``; raises :class:`_Exit` for a failure."""
    group, sub = (argv + ["", ""])[:2]
    positional, opts = _parse(argv[2:])

    if (group, sub) == ("repo", "view"):
        if opts.get("repo"):
            raise _Exit(1, "unknown flag: --repo\n")
        if positional != [state["repository"]]:
            raise _Exit(1, f"GraphQL: Could not resolve to a Repository with the name '{positional}'.\n")
        record = {"nameWithOwner": state["repository"], "url": state["url"]}
        return json.dumps(_select(record, opts.get("json"))) + "\n", False

    if group not in ("pr", "release") or not sub:
        raise _Exit(1, f"fake gh: unsupported command: {' '.join(argv[:2])}\n")
    _require_repo(state, opts)

    if (group, sub) == ("pr", "list"):
        if opts.get("state") != "all" or "head" not in opts:
            raise _Exit(1, "fake gh: only pr list --head <branch> --state all is modelled\n")
        limit = int(opts.get("limit", 30))
        prs = [_pr_view(pr) for pr in state["prs"] if pr["headRefName"] == opts["head"]]
        prs.sort(key=lambda pr: -pr["number"])
        return json.dumps([_select(pr, opts.get("json")) for pr in prs[:limit]]) + "\n", False

    if (group, sub) == ("pr", "view"):
        return json.dumps(_select(_pr_view(_find_pr(state, positional[0])), opts.get("json"))) + "\n", False

    if (group, sub) == ("pr", "create"):
        head, base = opts.get("head"), opts.get("base")
        if not head or not base or "title" not in opts or "body" not in opts:
            raise _Exit(1, "fake gh: pr create needs --head, --base, --title and --body\n")
        head_oid = _origin_commit(f"refs/heads/{head}")
        if head_oid is None or _origin_commit(f"refs/heads/{base}") is None:
            raise _Exit(1, f"pull request create failed: GraphQL: Head sha can't be blank, Base sha "
                           f"can't be blank, No commits between {base} and {head} (createPullRequest)\n")
        for pr in state["prs"]:
            if pr["state"] == "OPEN" and pr["headRefName"] == head and pr["baseRefName"] == base:
                raise _Exit(1, f'a pull request for branch "{head}" into branch "{base}" already '
                               f'exists:\n{pr["url"]}\n')
        count = _git(os.environ["FAKE_GH_ORIGIN"], "rev-list", "--count", f"refs/heads/{base}..refs/heads/{head}")
        if count.returncode != 0 or int(count.stdout.strip()) == 0:
            raise _Exit(1, f"pull request create failed: GraphQL: No commits between {base} and "
                           f"{head} (createPullRequest)\n")
        number = state["next_number"]
        state["next_number"] = number + 1
        url = f"{state['url']}/pull/{number}"
        state["prs"].append({
            "number": number, "state": "OPEN", "isDraft": bool(opts.get("draft")),
            "headRefName": head, "headRefOid": head_oid, "baseRefName": base,
            "isCrossRepository": False, "url": url, "mergedAt": None, "mergeCommit": None,
            "title": opts["title"], "body": opts["body"], "checks": None,
        })
        return url + "\n", True

    if (group, sub) == ("pr", "ready"):
        pr = _find_pr(state, positional[0])
        if pr["state"] != "OPEN":
            raise _Exit(1, f"Pull request #{pr['number']} is closed. Only draft pull requests can be marked as \"ready for review\"\n")
        pr["isDraft"] = False
        return "", True

    if (group, sub) == ("pr", "edit"):
        pr = _find_pr(state, positional[0])
        if "title" not in opts and "body" not in opts:
            raise _Exit(1, "fake gh: pr edit needs --title or --body\n")
        if pr["state"] != "OPEN":
            raise _Exit(1, f"Pull request #{pr['number']} is {pr['state'].lower()}\n")
        for field in ("title", "body"):
            if field in opts:
                pr[field] = opts[field]
        return pr["url"] + "\n", True

    if (group, sub) == ("pr", "checks"):
        pr = _find_pr(state, positional[0])
        if pr.get("checks") is None:
            raise _Exit(1, f"no checks reported on the '{pr['headRefName']}' branch\n")
        checks = [_select(check, opts.get("json")) for check in pr["checks"]]
        buckets = {check.get("bucket") for check in pr["checks"]}
        code = 1 if "fail" in buckets else GH_EXIT_PENDING if "pending" in buckets else 0
        raise _Exit(code, "", json.dumps(checks) + "\n")

    if (group, sub) == ("release", "view"):
        release = _find_release(state, positional[0])
        return json.dumps(_select(release, opts.get("json"))) + "\n", False

    if (group, sub) == ("release", "create"):
        tag, files = positional[0], positional[1:]
        if not opts.get("verify_tag"):
            raise _Exit(1, "fake gh: release create without --verify-tag is not modelled\n")
        if _origin_commit(f"refs/tags/{tag}") is None:
            raise _Exit(1, f"tag {tag} doesn't exist in the repo {state['repository']}, aborting due "
                           f"to --verify-tag flag\n")
        if any(r["tagName"] == tag for r in state["releases"]):
            raise _Exit(1, f"a release with the same tag name already exists: {tag}\n")
        release = {"tagName": tag, "isDraft": bool(opts.get("draft")),
                   "url": f"{state['url']}/releases/tag/{tag}", "assets": [],
                   "title": opts.get("title"), "notes": opts.get("notes")}
        _add_assets(release, files, clobber=False)
        state["releases"].append(release)
        return release["url"] + "\n", True

    if (group, sub) == ("release", "upload"):
        release = _find_release(state, positional[0])
        _add_assets(release, positional[1:], clobber=bool(opts.get("clobber")))
        return "", True

    if (group, sub) == ("release", "edit"):
        release = _find_release(state, positional[0])
        if "draft" in opts:
            release["isDraft"] = opts["draft"]
        return release["url"] + "\n", True

    if (group, sub) == ("release", "download"):
        release = _find_release(state, positional[0])
        dest = Path(opts.get("dir", "."))
        dest.mkdir(parents=True, exist_ok=True)
        for asset in release["assets"]:
            target = dest / asset["name"]
            if target.exists() and not opts.get("clobber"):
                raise _Exit(1, f"{target} already exists (use `--clobber` to overwrite file or "
                               f"`--skip-existing` to skip file)\n")
            shutil.copyfile(_assets_dir(release["tagName"]) / asset["name"], target)
        return "", False

    raise _Exit(1, f"fake gh: unsupported command: {group} {sub}\n")


_FAILURES = {
    "auth": (4, "To get started with GitHub CLI, please run:  gh auth login\n"
                "Alternatively, populate the GH_TOKEN environment variable with a GitHub API "
                "authentication token.\n"),
    "network": (1, "error connecting to api.github.com\ncheck your internet connection or "
                   "https://githubstatus.com\n"),
    "server_error": (1, "HTTP 500: Internal Server Error (https://api.github.com/graphql)\n"),
}


def _injected_failure(argv: list[str]) -> None:
    table = json.loads(os.environ.get("FAKE_GH_FAIL") or "{}")
    kind = table.get(" ".join(argv[:2]), table.get("*"))
    if kind is None:
        return
    if kind in _FAILURES:
        raise _Exit(*_FAILURES[kind])
    if kind == "malformed":
        raise _Exit(0, "", "<html>not json</html>\n")
    if kind.startswith("malformed:"):
        raise _Exit(int(kind.split(":", 1)[1]), "", "<html>not json</html>\n")
    raise _Exit(2, f"fake gh: unknown FAKE_GH_FAIL kind {kind!r}\n")


def main(argv: list[str]) -> int:
    log = os.environ.get("FAKE_GH_LOG")
    if log:
        with open(log, "a") as handle:
            handle.write(json.dumps(argv) + "\n")
    state_path = Path(os.environ["FAKE_GH_STATE"])
    try:
        _injected_failure(argv)
        state = read_state(state_path)
        stdout, changed = _run(argv, state)
        if changed:
            write_state(state_path, state)
    except _Exit as exc:
        sys.stdout.write(exc.stdout)
        sys.stderr.write(exc.stderr)
        return exc.code
    sys.stdout.write(stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
