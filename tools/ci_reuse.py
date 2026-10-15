#!/usr/bin/env python3
"""Reuse a pull request's validated results on ``main``. Stdlib only.

A squash (or merge-commit) merge creates a new commit on ``main`` whose
*tree* equals the tree of the pull request's merge ref whenever ``main`` did
not move after the pull request's last CI run. For that tree the required
validation already ran, so ``main.yml`` may skip the shards and re-verify the
recorded results instead (ADR 0013). This file is the decision and the
verification; ``tools/ci_workflows.py`` wires them into the workflows.

``CI_MAIN_REUSE`` (a repository variable) is the switch:

- unset or ``report``: the decision is computed and written to the job
  summary; the full validation still runs, exactly as before;
- ``on``: when every check passes, ``main.yml`` passes the pull request run's
  id to ``validate.yml``, which skips the shards;
- ``off``, or any other value (treated as ``off``, and said so): nothing is
  looked up and nothing changes.

Subcommands (each reads its inputs from flags or the environment, and
writes ``GITHUB_OUTPUT`` / ``GITHUB_STEP_SUMMARY`` when GitHub sets them):

``find``
    The merged pull request, its latest successful ``CI`` run on its final
    head, and the facts about both, written to ``--facts``.
``decide``
    Compare the recorded tree and plan digest (from the downloaded
    ``timings-ci-attempt-<a>`` artifact) with this commit's, log the report
    and, in mode ``on`` with every check passing, output ``run-id``.
``verify``
    Fail closed (exit 1) unless the recorded tree and plan digest equal this
    run's. Used by ``validate.yml``'s ``tests-result`` when it reuses a run.

There is no network access in the tests: ``find_candidate`` takes the API
reader as an argument.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ON, REPORT, OFF = "on", "report", "off"
VARIABLE = "CI_MAIN_REUSE"
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
TIMINGS_DIR_RE = re.compile(r"timings-ci-attempt-(\d+)")
TREE_FILE = "tree.txt"
PLAN_FILE = "plan.json"


class ReuseError(Exception):
    """A reason the results cannot be reused; the message is the reason."""


def parse_mode(raw: str | None) -> tuple[str, str]:
    """``(mode, note)`` for the variable's raw value; ``note`` is empty
    unless the value was not understood and is treated as ``off``."""
    value = (raw or "").strip()
    if value in ("", REPORT):
        return REPORT, ""
    if value in (ON, OFF):
        return value, ""
    return OFF, f"{VARIABLE}={value!r} is not on, report or off; treated as off"


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------

def find_candidate(get: Callable[[str], object], repo: str, sha: str, event: str,
                   ref: str) -> dict:
    """The facts about ``sha``'s pull request run, as a dict. ``candidate``
    is the run id to reuse, or ``None`` with ``reason``. ``get`` maps an API
    path (``/repos/...``) to decoded JSON."""
    facts: dict = {"candidate": None, "sha": sha, "reason": ""}
    if event != "push":
        facts["reason"] = f"the event is {event}, not a push"
        return facts
    if ref != "refs/heads/main":
        facts["reason"] = f"the ref is {ref}, not refs/heads/main"
        return facts
    pulls = [p for p in get(f"/repos/{repo}/commits/{sha}/pulls") or []
             if p.get("merged_at") and p.get("merge_commit_sha") == sha
             and (p.get("base") or {}).get("ref") == "main"]
    if len(pulls) != 1:
        facts["reason"] = (f"{len(pulls)} merged pull requests have {sha[:12]} as their merge "
                           "commit (expected exactly 1)")
        return facts
    pull = pulls[0]
    facts["pr"] = pull["number"]
    head_repo = ((pull.get("head") or {}).get("repo") or {}).get("full_name")
    if head_repo != repo:
        facts["reason"] = f"pull request #{pull['number']} comes from {head_repo}, not {repo}"
        return facts
    head_sha = pull["head"]["sha"]
    facts["pr_head"] = head_sha
    runs = get(f"/repos/{repo}/actions/workflows/ci.yml/runs?head_sha={head_sha}"
               "&event=pull_request&status=success&per_page=100")
    good = [r for r in (runs or {}).get("workflow_runs", [])
            if r.get("head_sha") == head_sha and r.get("event") == "pull_request"
            and r.get("conclusion") == "success" and r.get("status") == "completed"
            and r.get("path") == CI_WORKFLOW_PATH
            and ((r.get("head_repository") or {}).get("full_name")) == repo]
    if not good:
        facts["reason"] = (f"pull request #{pull['number']} has no successful CI run on its "
                           f"final head {head_sha[:12]}")
        return facts
    run = max(good, key=lambda r: (r.get("run_number", 0), r.get("run_attempt", 0)))
    facts["candidate"] = run["id"]
    facts["run_attempt"] = run.get("run_attempt")
    return facts


# ---------------------------------------------------------------------------
# recorded results
# ---------------------------------------------------------------------------

@dataclass
class Recorded:
    directory: Path
    attempt: int
    tree: str
    plan_digest: str


def read_recorded(reused_dir: Path) -> Recorded:
    """The highest-attempt ``timings-ci-attempt-<a>`` directory under
    ``reused_dir``, with its recorded tree and plan digest."""
    reused_dir = Path(reused_dir)
    found = []
    if reused_dir.is_dir():
        for path in reused_dir.iterdir():
            match = TIMINGS_DIR_RE.fullmatch(path.name)
            if match and path.is_dir():
                found.append((int(match.group(1)), path))
    if not found:
        raise ReuseError("the pull request run has no timings-ci-attempt-<a> artifact")
    attempt, directory = max(found)
    try:
        tree = (directory / TREE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        raise ReuseError(f"{directory.name} records no tree ({TREE_FILE}); it predates reuse")
    try:
        digest = json.loads((directory / PLAN_FILE).read_text(encoding="utf-8"))["plan_digest"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ReuseError(f"{directory.name} has no readable {PLAN_FILE} with a plan_digest")
    if not re.fullmatch(r"[0-9a-f]{40,64}", tree) or not isinstance(digest, str) or not digest:
        raise ReuseError(f"{directory.name} records a malformed tree or plan digest")
    return Recorded(directory, attempt, tree, digest)


@dataclass
class Comparison:
    recorded: Recorded | None = None
    tree_equal: bool | None = None
    digest_equal: bool | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.reason and self.tree_equal is True and self.digest_equal is True


def compare(reused_dir: Path, tree: str, plan_digest: str) -> Comparison:
    """Fail-closed comparison of the recorded tree and plan digest with this
    commit's. ``reason`` is empty only when both are equal."""
    result = Comparison()
    try:
        result.recorded = read_recorded(reused_dir)
    except ReuseError as exc:
        result.reason = str(exc)
        return result
    result.tree_equal = result.recorded.tree == tree
    result.digest_equal = result.recorded.plan_digest == plan_digest
    if not result.tree_equal:
        result.reason = "the recorded tree differs from this commit's tree (the base moved)"
    elif not result.digest_equal:
        result.reason = "the recorded plan digest differs from the plan computed here"
    return result


# ---------------------------------------------------------------------------
# decision and report
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    mode: str
    reuse: bool
    reason: str
    lines: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return "\n".join(self.lines) + "\n"


def decide(mode: str, note: str, facts: dict | None, comparison: Comparison | None,
           tree: str, plan_digest: str | None) -> Decision:
    """The decision and its report. ``reuse`` is true only when every check
    passed; whether it takes effect depends on ``mode``."""
    lines = [f"### CI reuse ({VARIABLE}: {mode})", ""]
    if note:
        lines += [f"- {note}", ""]
    if mode == OFF:
        lines.append("- reuse is off: the full validation runs, as before.")
        return Decision(mode, False, "reuse is off", lines)
    facts = facts or {}
    lines.append(f"- this commit: `{facts.get('sha', '?')}`, tree `{tree}`")
    if "pr" in facts:
        lines.append(f"- merged pull request: #{facts['pr']}, final head `{facts.get('pr_head', '?')}`")
    reason = facts.get("reason", "")
    if facts.get("candidate") is None:
        reason = reason or "no candidate pull request run"
        lines.append("- candidate pull request run: none")
    else:
        lines.append(f"- candidate pull request run: {facts['candidate']} "
                     f"(attempt {facts.get('run_attempt')})")
        if comparison is None:
            reason = reason or "the candidate was not compared"
        else:
            if comparison.recorded is not None:
                lines.append(f"- recorded tree: `{comparison.recorded.tree}` "
                             f"({'equal' if comparison.tree_equal else 'DIFFERENT'})")
                lines.append(f"- recorded plan digest: `{comparison.recorded.plan_digest}`, "
                             f"this plan: `{plan_digest}` "
                             f"({'equal' if comparison.digest_equal else 'DIFFERENT'})")
            reason = comparison.reason
    reuse = not reason
    if reuse:
        verdict = "reuse: the shards are skipped" if mode == ON else "would reuse"
        lines += ["", f"**Decision: {verdict}.**"]
        if mode == REPORT:
            lines.append("The full validation still runs; compare it with the pull request run.")
    else:
        lines += ["", f"**Decision: {'do not reuse' if mode == ON else 'would not reuse'}: "
                      f"{reason}.**"]
        if mode == ON:
            lines.append("The full validation runs.")
    return Decision(mode, reuse, reason, lines)


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def _github_get(path: str):
    token = os.environ.get("GH_TOKEN", "")
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(base + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _publish(text: str) -> None:
    print(text, end="")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def _git_tree() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD^{tree}"], check=True, capture_output=True,
                          text=True).stdout.strip()


def _read_facts(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_find(args) -> int:
    mode, note = parse_mode(args.mode)
    facts: dict = {"sha": args.sha, "candidate": None, "reason": ""}
    if mode != OFF:
        try:
            facts = find_candidate(_github_get, args.repo, args.sha, args.event, args.ref)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Fail closed: any API trouble means no candidate, never a guess.
            facts["reason"] = f"the lookup failed ({type(exc).__name__}: {exc})"
    facts.update(mode=mode, note=note)
    Path(args.facts).parent.mkdir(parents=True, exist_ok=True)
    Path(args.facts).write_text(json.dumps(facts, indent=1), encoding="utf-8")
    _output("candidate", str(facts["candidate"] or ""))
    return 0


def cmd_decide(args) -> int:
    facts = _read_facts(args.facts)
    mode, note = facts.get("mode", OFF), facts.get("note", "")
    if not facts:
        mode, note = OFF, "the lookup left no facts; treated as off"
    tree, digest, comparison = "", None, None
    try:
        if mode != OFF:
            tree = _git_tree()
        if mode != OFF and facts.get("candidate") is not None:
            digest = json.loads(Path(args.plan).read_text(encoding="utf-8"))["plan_digest"]
            comparison = compare(args.reused_dir, tree, digest)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        comparison = Comparison(reason=f"the comparison failed ({type(exc).__name__}: {exc})")
    decision = decide(mode, note, facts, comparison, tree, digest)
    _publish(decision.summary())
    _output("mode", mode)
    _output("run-id", str(facts["candidate"]) if mode == ON and decision.reuse else "")
    return 0


def cmd_verify(args) -> int:
    try:
        tree = _git_tree()
        digest = json.loads(Path(args.plan).read_text(encoding="utf-8"))["plan_digest"]
        comparison = compare(args.reused_dir, tree, digest)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"cannot verify the reused results: {exc}", file=sys.stderr)
        return 1
    if not comparison.ok:
        _publish(f"### Reused results refused\n\n{comparison.reason}.\n")
        return 1
    recorded = comparison.recorded
    _publish(f"### Reused results\n\nTests were not re-run: pull request run results from "
             f"`{recorded.directory.name}` have tree `{recorded.tree}` and plan digest "
             f"`{recorded.plan_digest}`, both equal to this commit's.\n")
    _output("selected-dir", str(recorded.directory))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    find = sub.add_parser("find", help="find the merged pull request's successful CI run")
    find.add_argument("--repo", required=True)
    find.add_argument("--sha", required=True)
    find.add_argument("--event", required=True)
    find.add_argument("--ref", required=True)
    find.add_argument("--mode", default="", help=f"the raw {VARIABLE} value")
    find.add_argument("--facts", type=Path, required=True)
    find.set_defaults(func=cmd_find)
    decide_p = sub.add_parser("decide", help="compare, report and output the run id to reuse")
    decide_p.add_argument("--facts", type=Path, required=True)
    decide_p.add_argument("--reused-dir", type=Path, required=True)
    decide_p.add_argument("--plan", type=Path, required=True)
    decide_p.set_defaults(func=cmd_decide)
    verify = sub.add_parser("verify", help="fail unless the recorded tree and digest match")
    verify.add_argument("--reused-dir", type=Path, required=True)
    verify.add_argument("--plan", type=Path, required=True)
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
