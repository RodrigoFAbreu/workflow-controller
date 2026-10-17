#!/usr/bin/env python3
"""Regenerate the ``gate_policy`` check captures D3 of
``workflow-controller-review-modes`` reads (``review_mode.lowered_fields``).

Each capture is the check the vendored Workflow's ``verify`` returns
(``workflow_gate_policy.verify_check``, the function ``verify`` calls) in a
scratch repository, never a hand-written string. Run from the repository root:

    python3 -I tests/verify_captures/generate.py [--check]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import workflow_gate_policy as g  # noqa: E402
import workflow_gate_policy_test as t  # noqa: E402

TIGHTEN_PLAN = {"schema_version": 1, "gates": {"plan_approval": {"require": [], "human": True},
                                               "technical_approval": {"require": []}}}
UNREFERENCED = {"verdict_sha256": "0" * 64, "run_ref": None}


def _check(repo: "t.PolicyRepo") -> dict:
    g.clear_caches()
    status, detail = g.verify_check(repo.root, repo.state())
    return {"id": "gate_policy", "status": status, "detail": detail}


def _lowering_only(repo):
    """The default policy's ``require`` lists, removed by an adoption."""
    repo.adopt(t.NO_REQUIRE)


def _plus_file_tightened(repo):
    _lowering_only(repo)
    repo.write_policy(TIGHTEN_PLAN)  # not committed, not adopted


def _plus_floor_and_reverted_file(repo):
    _lowering_only(repo)
    repo.commit_policy(TIGHTEN_PLAN, "tightening file")
    repo.record_floor()
    repo.commit_policy(t.NO_REQUIRE, "file reverted to the adopted body")


def _plus_invalid_file(repo):
    _lowering_only(repo)
    repo.write_policy("{ not json")


def _unreferenced(repo):
    _lowering_only(repo)
    state = repo.state()
    state["work_items"]["a"] = {"plan_review_stages": {"LOCAL_MODEL_PLAN_REVIEW": dict(UNREFERENCED)}}
    repo.write_state(state)


SCENARIOS = {
    "no_policy": lambda repo: None,
    "lowering_only": _lowering_only,
    "lowering_plus_file_tightened": _plus_file_tightened,
    "lowering_plus_floor_and_reverted_file": _plus_floor_and_reverted_file,
    "lowering_plus_invalid_file": _plus_invalid_file,
    "lowering_plus_unreferenced_runs": _unreferenced,
}


def _masked(text: str) -> str:
    """The text without the scratch repository's commit ids, which differ per run."""
    return re.sub(r"\(commit [0-9a-f]{12}\)", "(commit <commit>)", text)


def capture(name: str) -> dict:
    with t.PolicyRepo() as repo:
        SCENARIOS[name](repo)
        return _check(repo)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    bad = 0
    for name in SCENARIOS:
        path = HERE / f"{name}.json"
        text = json.dumps(capture(name), indent=2, sort_keys=True) + "\n"
        if check:
            if not path.exists() or _masked(path.read_text()) != _masked(text):
                print(f"{path.name} differs", file=sys.stderr)
                bad = 1
        else:
            path.write_text(text)
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
