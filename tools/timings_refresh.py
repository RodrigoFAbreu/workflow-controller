#!/usr/bin/env python3
"""Refresh the committed shard timings from recent pull-request runs. Stdlib only.

``tools/test_timings.json`` is advisory input to the planner; it drifts as tests are
added, and ``CommittedTimingsTest`` fails once too many placed tests are untimed. CI2
refreshed it by hand from the artifacts of three pull-request runs. This file is that
procedure as a tool, run by the scheduled ``timings-refresh.yml`` (CI2d, D6):

``collect``
    List the newest successful ``ci.yml`` runs of event ``pull_request`` (never ``main``:
    a reused ``main`` run duplicates the pull-request run's records), download the
    ``timings-ci-attempt-<a>`` artifact of at most ``--runs`` of them, and drop a shard
    record whose ``(plan_digest, shard)`` was already taken (newest run first, so the
    newest record is the one kept). Writes ``dirs.txt`` (one directory per line, each
    holding ``plan.json`` and its records) beside the downloads, **oldest run first**:
    the merger's EWMA weighs a later observation more, so the order is chronological.
``plan-change``
    Run ``run_tests.py timings merge`` into a copy of the committed file, print the
    untimed share of the CI-placed tests before and after, and write the merged file to
    ``--output`` only when it differs. Exit 0; ``changed=true|false`` goes to
    ``GITHUB_OUTPUT``.
``propose``
    Commit the changed file on the bot-owned branch ``ci/timings-refresh``, push it with
    ``--force-with-lease`` and open the pull request, or update the open one. It needs
    ``TIMINGS_REFRESH_TOKEN``: a pull request opened with the default token starts no
    ``pull_request`` workflow, so the required checks would never run. Without the secret
    it fails with a message naming it and opens nothing.

``gh`` and ``git`` are reached through a ``runner`` argument, so the tests pass a fake and
never touch the network.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
TIMINGS_ARTIFACT_RE = re.compile(r"timings-ci-attempt-(\d+)")
DEFAULT_RUNS = 3
BRANCH = "ci/timings-refresh"
BASE_BRANCH = "main"
TITLE = "ci: refresh the shard timings (automated)"
TOKEN_VARIABLE = "TIMINGS_REFRESH_TOKEN"
DIRS_FILE = "dirs.txt"
TIMINGS_PATH = "tools/test_timings.json"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"

TOKEN_MISSING = (f"{TOKEN_VARIABLE} is not set: create the repository secret (a personal access token "
                 "or GitHub App token with contents and pull-requests write) under Settings, Secrets "
                 "and variables, Actions. A pull request opened with the default GITHUB_TOKEN starts "
                 "no pull_request workflow, so its required checks would never run; nothing was "
                 "pushed and no pull request was opened.")

Runner = Callable[[Sequence[str], Mapping[str, str] | None], str]


class RefreshError(Exception):
    """A reason the refresh stops; the message is the reason."""


def _load_test_shards():
    name = "test_shards"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("test_shards.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def subprocess_runner(argv: Sequence[str], env: Mapping[str, str] | None = None) -> str:
    """Run ``argv`` and return its stdout; a failure raises ``RefreshError``."""
    merged = dict(os.environ)
    merged.update(env or {})
    done = subprocess.run(list(argv), capture_output=True, text=True, env=merged, check=False)
    if done.returncode != 0:
        raise RefreshError(f"{' '.join(argv[:3])} failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def _api(runner: Runner, path: str) -> object:
    return json.loads(runner(["gh", "api", path], None) or "null")


def eligible_runs(runs: object, repo: str) -> list[dict]:
    """The successful ``ci.yml`` pull-request runs of ``repo`` in ``runs`` (the API's
    ``workflow_runs`` reply), newest first. A ``main`` run, a run of another workflow,
    a fork's run and an unfinished or failed run are never taken."""
    listed = (runs or {}).get("workflow_runs", []) if isinstance(runs, dict) else []
    good = [r for r in listed
            if r.get("event") == "pull_request" and r.get("conclusion") == "success"
            and r.get("status") == "completed" and r.get("path") == CI_WORKFLOW_PATH
            and (r.get("head_repository") or {}).get("full_name") == repo]
    return sorted(good, key=lambda r: (r.get("run_number", 0), r.get("run_attempt", 0)), reverse=True)


def newest_timings_artifact(artifacts: object) -> dict | None:
    """The live ``timings-ci-attempt-<a>`` artifact with the highest attempt, if any."""
    listed = (artifacts or {}).get("artifacts", []) if isinstance(artifacts, dict) else []
    found = [(int(m.group(1)), a) for a in listed
             if not a.get("expired") and (m := TIMINGS_ARTIFACT_RE.fullmatch(a.get("name", "")))]
    return max(found, key=lambda pair: pair[0])[1] if found else None


def _records(directory: Path) -> list[Path]:
    return sorted(directory.glob("shard-*.json"))


def _identity(path: Path) -> tuple[str, int] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return str(record["plan_digest"]), int(record["shard"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def collect(runner: Runner, repo: str, output_dir: Path, runs: int = DEFAULT_RUNS) -> list[Path]:
    """Download the timings artifacts of the newest eligible runs, at most ``runs`` of
    them, and return the directories that still hold a record after the duplicates (a
    ``(plan_digest, shard)`` already taken) are dropped, newest run first. ``dirs.txt``
    lists the same directories oldest first, the order ``run_tests.py timings merge``
    requires (``update_timings`` folds its records oldest first)."""
    if runs < 1:
        raise RefreshError("--runs must be at least 1")
    output_dir = Path(output_dir)
    listing = _api(runner, f"/repos/{repo}/actions/workflows/ci.yml/runs"
                           "?event=pull_request&status=success&per_page=50")
    taken: set[tuple[str, int]] = set()
    kept: list[Path] = []
    fetched = 0
    for run in eligible_runs(listing, repo):
        if fetched >= runs:
            break
        artifact = newest_timings_artifact(_api(runner, f"/repos/{repo}/actions/runs/{run['id']}"
                                                        "/artifacts?per_page=100"))
        if artifact is None:
            continue
        directory = output_dir / f"run-{run['id']}"
        runner(["gh", "run", "download", str(run["id"]), "--repo", repo,
                "--name", artifact["name"], "--dir", str(directory)], None)
        fetched += 1
        for path in _records(directory):
            identity = _identity(path)
            if identity is None or identity in taken:
                path.unlink()
            else:
                taken.add(identity)
        if _records(directory):
            kept.append(directory)
        else:
            shutil.rmtree(directory, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / DIRS_FILE).write_text("".join(f"{d}\n" for d in reversed(kept)), encoding="utf-8")
    return kept


# ---------------------------------------------------------------------------
# plan-change
# ---------------------------------------------------------------------------

def untimed_share(timings: Path, repo_root: Path) -> tuple[int, int]:
    """``(untimed, total)`` tests over the atoms the CI plan places on a shard."""
    ts = _load_test_shards()
    profile = ts.parse_timings(Path(timings).read_bytes())
    atoms = ts.build_inventory(Path(repo_root)).select((), ci_placement=True).atoms
    total = sum(len(a.test_ids) for a in atoms)
    return sum(len(a.test_ids) for a in atoms if a.key not in profile.atoms), total


def default_merge(into: Path, directories: Sequence[Path], repo_root: Path) -> None:
    runner = Path(repo_root) / "tools" / "run_tests.py"
    subprocess_runner([sys.executable, str(runner), "timings", "merge", "--into", str(into),
                       "--repo-root", str(repo_root), *map(str, directories)])


def _share_text(label: str, share: tuple[int, int]) -> str:
    untimed, total = share
    percent = 100 * untimed / total if total else 0.0
    return f"{label}: {untimed} of {total} CI-placed tests untimed ({percent:.1f} percent)"


def plan_change(timings: Path, directories: Sequence[Path], output: Path, repo_root: Path = REPO_ROOT,
                *, merge: Callable[[Path, Sequence[Path], Path], None] = default_merge,
                share: Callable[[Path, Path], tuple[int, int]] = untimed_share) -> tuple[bool, list[str]]:
    """Merge ``directories`` into a copy of ``timings``; ``(changed, report lines)``.
    ``output`` is written (with the merged bytes) only when they differ."""
    timings = Path(timings)
    lines: list[str] = []
    if not directories:
        return False, ["no shard records to merge: the committed timings are unchanged"]
    original = timings.read_bytes()
    with tempfile.TemporaryDirectory(prefix="timings-refresh-") as scratch:
        merged = Path(scratch) / timings.name
        merged.write_bytes(original)
        merge(merged, directories, Path(repo_root))
        updated = merged.read_bytes()
        lines.append(_share_text("before", share(timings, Path(repo_root))))
        lines.append(_share_text("after", share(merged, Path(repo_root))))
    changed = updated != original
    if changed:
        Path(output).write_bytes(updated)
    lines.append("the merge changed the timings" if changed else "the merge left the timings unchanged")
    return changed, lines


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

def _auth_env(token: str) -> dict[str, str]:
    """Lend git the token for one command, through the environment only; nothing is
    written to the checkout's configuration."""
    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {header}", "GH_TOKEN": token}


def propose(runner: Runner, repo: str, environ: Mapping[str, str], body: str,
            path: str = TIMINGS_PATH) -> str:
    """Push ``path`` on ``BRANCH`` and open (or update) the one refresh pull request.
    Returns ``opened`` or ``updated``."""
    token = environ.get(TOKEN_VARIABLE, "")
    if not token:
        raise RefreshError(TOKEN_MISSING)
    env = _auth_env(token)
    runner(["git", "checkout", "-B", BRANCH], None)
    runner(["git", "add", "--", path], None)
    runner(["git", "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
            "commit", "--message", TITLE], None)
    try:
        runner(["git", "fetch", "origin", BRANCH], env)
    except RefreshError:
        pass  # no earlier branch: the lease then expects none
    runner(["git", "push", f"--force-with-lease={BRANCH}", "origin", f"{BRANCH}:{BRANCH}"], env)
    listed = json.loads(runner(["gh", "pr", "list", "--repo", repo, "--head", BRANCH, "--state", "open",
                                "--json", "number"], env) or "[]")
    if listed:
        runner(["gh", "pr", "edit", str(listed[0]["number"]), "--repo", repo, "--title", TITLE,
                "--body", body], env)
        return "updated"
    runner(["gh", "pr", "create", "--repo", repo, "--head", BRANCH, "--base", BASE_BRANCH,
            "--title", TITLE, "--body", body], env)
    return "opened"


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def _output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _repo(args) -> str:
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise RefreshError("--repo or GITHUB_REPOSITORY is required")
    return repo


def main(argv: list[str] | None = None, *, runner: Runner = subprocess_runner,
         environ: Mapping[str, str] | None = None, merge=default_merge, share=untimed_share) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("collect", help="download the timings artifacts of recent pull-request runs")
    one.add_argument("--repo")
    one.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    one.add_argument("--output-dir", type=Path, required=True)
    two = sub.add_parser("plan-change", help="merge into a copy and report whether the file changes")
    two.add_argument("--timings", type=Path, default=REPO_ROOT / TIMINGS_PATH)
    two.add_argument("--output", type=Path, required=True)
    two.add_argument("--dirs-file", type=Path, required=True, help="the dirs.txt collect wrote")
    two.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    three = sub.add_parser("propose", help="push the branch and open or update the pull request")
    three.add_argument("--repo")
    three.add_argument("--body", default="Automated refresh of tools/test_timings.json from recent "
                                         "pull-request runs (tools/timings_refresh.py).")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        if args.command == "collect":
            kept = collect(runner, _repo(args), args.output_dir, args.runs)
            print(f"collected {len(kept)} run(s) with shard records into {args.output_dir}")
            _output("count", str(len(kept)))
        elif args.command == "plan-change":
            dirs = [Path(line) for line in args.dirs_file.read_text(encoding="utf-8").split("\n") if line]
            changed, lines = plan_change(args.timings, dirs, args.output, args.repo_root,
                                           merge=merge, share=share)
            print("\n".join(lines))
            _output("changed", "true" if changed else "false")
        else:
            print(propose(runner, _repo(args), environ, args.body))
    except (RefreshError, OSError, ValueError) as exc:
        print(f"timings_refresh: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
