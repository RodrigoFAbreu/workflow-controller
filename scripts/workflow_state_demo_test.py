#!/usr/bin/env python3
"""Explicit integration/demonstration test for workflow_state.py against
this repository's real state, mirroring
workflow_fingerprint_demo_test.py's split from the hermetic suite.

Not part of the hermetic unit suite (workflow_state_test.py); treated as
an opt-in integration check, not wired into CI, for the same reason
workflow_fingerprint_demo_test.py is CI-exempt (GPT-R9-002): it depends on
a specific historical base commit, so an unrelated later product PR would
otherwise fail it purely because the diff from that fixed base grows.

Run: python3 scripts/workflow_state_demo_test.py
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

import workflow_fingerprint as fingerprint
import workflow_state as ws

BASE_COMMIT = "162154d3e5e10eb65e109833acae4b4fb01fc5d6"
WORK_ITEM_ID = "workflow-v2-1-core"

# `workflow-v2-1-core`'s own `/accept-milestone` commit -- its `base_commit`
# reached `MILESTONE_COMPLETE`. Every real-repository test in this file that
# exists to validate a fact about `workflow-v2-1-core`'s own now-closed
# history is anchored here, never at live `"HEAD"`/the working tree: that
# history stopped moving the instant the item completed, so a fixed anchor
# stays green permanently regardless of what a later, concurrent work item
# (e.g. `workflow-v2-3`) commits on top of it (revision 4/5/6, round 3/4/5
# `local_model_plan_review` B1).
WORKFLOW_V2_1_CORE_COMPLETION_COMMIT = "27f051eba897d77c742ead8b160ed519c0671ee4"


def _blob_at_commit(repo_root: Path, commit: str, rel_path: str) -> str:
    """The blob SHA `rel_path` had at `commit`, never the live worktree --
    so a manifest fixed at a commit and its own verification loop are
    always compared from the same source (revision 5, round 4 B2/I1)."""
    return subprocess.run(
        ["git", "rev-parse", f"{commit}:{rel_path}"], cwd=repo_root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _repo_root() -> Path:
    return Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    )


def _parse_checkpoint_registry_table(repo_root: Path) -> list[dict]:
    """Parses the '## Checkpoint registry' Markdown table in
    WORKFLOW_V2_PLAN.md into one dict per row, over exactly the five
    columns the table carries a value for besides id/Driven-by: name,
    depends_on_cell (raw, un-mapped), complexity_cell (raw, un-mapped),
    session_target. Used by item 72's conformance test (WF8c clause (r))
    and its own teeth-check below."""
    plan_path = repo_root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
    lines = plan_path.read_text().splitlines()
    section_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("## Checkpoint registry")), None,
    )
    assert section_idx is not None, "WORKFLOW_V2_PLAN.md has no '## Checkpoint registry' section"
    header_idx = next(
        (i for i in range(section_idx, len(lines)) if lines[i].startswith("| ID |")), None,
    )
    assert header_idx is not None, "no '| ID | ...' table header found under 'Checkpoint registry'"
    separator_idx = header_idx + 1
    assert lines[separator_idx].startswith("|---"), (
        f"expected a Markdown table separator row after the header, got: {lines[separator_idx]!r}"
    )
    rows = []
    i = separator_idx + 1
    while i < len(lines) and lines[i].startswith("|"):
        cells = lines[i].split("|")
        assert len(cells) == 8, (
            f"line {i + 1}: expected the 6-column '| ID | Name | Depends on | "
            f"Complexity | Session target | Driven by |' shape (7 pipes), got "
            f"{len(cells) - 1} pipes -- a cell may contain an unescaped '|': {lines[i]!r}"
        )
        rows.append({
            "id": cells[1].strip(),
            "name": cells[2].strip(),
            "depends_on_cell": cells[3].strip(),
            "complexity_cell": cells[4].strip(),
            "session_target": cells[5].strip(),
        })
        i += 1
    assert rows, "no data rows parsed from the Checkpoint registry table"
    return rows


def _registry_row_mismatches(row: dict, entry: dict) -> list[tuple[str, str, object, object]]:
    """Compares one parsed table row against its registry JSON checkpoint
    entry over the five fields WF8c clause (r) names, each under its own
    stated mapping rule. Returns (checkpoint_id, field, table_value,
    json_value) for every field that diverges; completion_obligations is
    deliberately not compared -- the table carries no column for it."""
    mismatches = []

    if row["name"] != entry["name"]:
        mismatches.append((row["id"], "name", row["name"], entry["name"]))

    if row["depends_on_cell"] == "none":
        table_depends_on = []
    else:
        table_depends_on = [d.strip() for d in row["depends_on_cell"].split(",")]
    if table_depends_on != entry["depends_on"]:
        mismatches.append((row["id"], "depends_on", table_depends_on, entry["depends_on"]))

    complexity_match = re.match(r"^(\d+)", row["complexity_cell"])
    assert complexity_match, f"{row['id']}: Complexity cell {row['complexity_cell']!r} has no leading integer"
    table_complexity = int(complexity_match.group(1))
    if table_complexity != entry["complexity"]:
        mismatches.append((row["id"], "complexity", table_complexity, entry["complexity"]))

    if row["session_target"] != entry["session_target"]:
        mismatches.append((row["id"], "session_target", row["session_target"], entry["session_target"]))

    return mismatches


_WORKFLOW_TRAILER_LOOKALIKE_RE = re.compile(r"^(Workflow-[A-Za-z-]+):\s*(.*)$")
_TRAILER_SHAPED_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:\s*\S")


def _message_paragraphs(body: str) -> list[list[str]]:
    """Splits a commit message into blank-line-delimited paragraphs, each a
    list of its non-empty lines -- the same block-shaped grouping Git's own
    trailer machinery reasons about, needed so the mechanical guard below
    can tell an actual trailer-block-shaped paragraph (every line `key:
    value`) from ordinary hard-wrapped prose that merely happens to start a
    line with something matching `Workflow-[A-Za-z-]+:` (e.g. a sentence
    discussing trailer names)."""
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.strip() == "":
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(current)
    return paragraphs


# OPUS-R129-001: the exact, independently-verified set of commits in
# `162154d3..HEAD` that write a `Workflow-[A-Za-z-]+:` line, inside an
# otherwise trailer-block-shaped paragraph, that Git's own
# `interpret-trailers --parse` does not recognize as a trailer (a blank
# line before further prose -- typically `Co-Authored-By:`/`Claude-Session:`
# -- pushes the Workflow-* paragraph out of the message's own last
# paragraph, which is all `interpret-trailers` ever reads). History
# rewriting is not available here (several of these SHAs are pinned into
# `WORKFLOW_STATE.json`/approved bundles as ordinary commit references),
# so these are permanently, individually grandfathered -- explicitly named,
# not silently ignored -- while `.claude/commands/bootstrap-workflow-v2.md`
# step 6 and `.claude/commands/milestone-implement.md` step 1f now both
# state the final-paragraph requirement so no *new* commit joins this set.
# Recomputed and pinned at the OPUS-R129-001 remediation round; the set
# below is exactly what `_workflow_trailer_lookalike_violations` finds at
# `162154d3..4a769fd` (the last pre-fix commit in this work item's own
# history).
_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS = frozenset({
    "0104100afca8dc4a6464f89c85dfc80b14c740c2",
    "0b3745465b45059a1540b5e8b717044cda515682",
    "11796086602f4443163dc581453bb2b49f431e6f",
    "28952ebfad5b8a6633c21b7aab843170b9d3d303",
    "2cc89da38eac15109642f3288fe4c3dc0f893e2b",
    "31a00a6dedbc21047bd579e91b1f1f22b75c4624",
    "3887e1e99e6e18618c970ec1ef2b2739e648981f",
    "3ef33f01c34129f137344115eb5ab673f01af079",
    "4a769fdfbff8824bafbed034549e6971f33aab17",
    "5d447ee040e3098eac6664654b10adefcc654b00",
    "6228d9d1fbcf654ff30d2d6de650d6004f30270e",
    "6348c22baf230a0bde76d3f32a28ff4104202397",
    "6af29f3404b714c7a1cb8ea79fff91973dcb8094",
    "78f1e808e1792046d328ba9b87765607fd10a716",
    "7acbd0a40f028d112aa655a1fa67713292ee325e",
    "81e5b15213969d118061a03d143899572fb47175",
    "900b655e5d43c4756f61d664887b43f318633daf",
    "98d423fc899fc5b63a43ac1d98cbf8013bf46e05",
    "a2949bfb1a0e1d242bb3897206339376d5492cf4",
    "a70bf9353ae47c0647fb5f1b451362211c1a3fab",
    "a8af94020687ad5a4320663b7b456f6ce27f4212",
    "ac6d1522dbbadebd51b66261087a5c6814daddd8",
    "bb28744c232ede2f2ebd64eeef69e32a996e3608",
    "be4636a25ce215dc03c80d5953f8f211b8b42f89",
    "be99257a9b0a7d4f38b16618e24f8721b94a023e",
    "c132185f79fc31ba299033a0f4c6136fa9fb9123",
    "c6fb46c55916d2c8134dc3b2c6101c6fbd02b1c0",
    "c75215f9e11d1e6a258eff0b3942ee98091f2024",
    "cd473410a6d5a3dee45478737a74f37dfdbd9417",
    "d18d939d59752063aedea17b94694cc1dac2fd16",
    "d70d500d71a2644eb02736a355ebe1e34b5472b0",
    "decd2752e665f5af5660975cb2f3e3d26c058890",
    "e321f1d961e0c99e1ca59b90ceb45141f375d8d8",
    "e3be7321a7ca06a8cda55f9fa667551dc1001a0f",
    "e5bed2324b2a13bf6fd37c0108249e5602e4b0b2",
    "ef9bc012099b0302a24c6367eae891ae5e66f142",
    "f2f47f2298c071a53370f5edcbd74455320c92eb",
    "fac2bac3a6daee56a8393c50b1bcc6cfc64fced9",
    "fb7cc3eafebcc232b79644abd021fe17b48e161f",
    "fd0eb73e5d1895a24e018b44526f7e7dbf400e5f",
    # `workflow-v2-1-core`'s own `/accept-milestone` commit (this item's own
    # `base_commit`) -- `/approve-review` step 6.4, unlike
    # `milestone-implement.md`/`bootstrap-workflow-v2.md`, carries no
    # "trailers must be the message's own final paragraph" requirement, so
    # this pre-existing violation is not this work item's own regression
    # (revision 13, round 11 I1).
    "27f051eba897d77c742ead8b160ed519c0671ee4",
    # `workflow-v2-3`'s own `/accept-milestone` commit (`workflow-v2-3-
    # followups`'s own `base_commit`) -- `accept-milestone.md`'s commit
    # step carries the same missing-final-paragraph trailer gap CP1 fixes
    # in `approve-review.md` step 6.4; fixing `accept-milestone.md` itself
    # is explicitly declined for this milestone (`workflow-v2-3-followups`
    # plan, "Executing this plan"), so this pre-existing violation is not
    # this work item's own regression (`LPR-R3-B01`/`LPR-R3-I01`).
    "fb134ac4f7cdabb7861d170bb61331bb8d9f5a14",
    # `workflow-v2-3-followups`'s own `/accept-milestone` commit -- the
    # same `accept-milestone.md` gap recurring a third time, verified with
    # `git log -1 --format=%B d271d89 | git interpret-trailers --parse`
    # (only `Co-Authored-By:`/`Claude-Session:` come out; `Workflow-Work-
    # Item:` does not). This is not a new defect: `accept-milestone.md`
    # still carries no "trailers must be the message's own final
    # paragraph" requirement -- unlike `milestone-implement.md`/
    # `bootstrap-workflow-v2.md`/`approve-review.md` step 6.4, which
    # `workflow-v2-3-followups` CP1 fixed -- fixing `accept-milestone.md`
    # itself was explicitly, by name, declined for that same milestone
    # (`WORKFLOW_V2_3_FOLLOWUPS_PLAN.md`'s "Executing this plan": "the
    # same disposition as item 2 #8/item 3 #7 ... recorded here, by name,
    # so it is not rediscovered as a surprise"). No production discovery/
    # provenance mechanism reads this commit's `Workflow-Work-Item`
    # trailer: `_discover_trailer_commits` (`workflow_state.py`) is only
    # ever called for `Workflow-Checkpoint`/`Workflow-Plan-Approval`/
    # `Workflow-Technical-Approval`/`Workflow-Scoped-Remediation-
    # Acceptance`/`Workflow-Functional-Checklist`/`Workflow-Bundle-
    # Generation-Record` trailer pairs, none of which this
    # `MILESTONE_COMPLETE` commit carries or needs to be discoverable by
    # -- `/accept-milestone` is a terminal, one-way transition with no
    # later command that re-discovers its own commit via trailer search.
    # `accept-milestone.md` step 6 now states the same final-paragraph
    # requirement (`OPUS-R129-001`, baseline-freeze verification cleanup,
    # 2026-08-24 -- landed in the same commit as this comment's own
    # update), so this gap does not recur for any future `/accept-
    # milestone` invocation. This SHA remains permanently grandfathered:
    # it predates that fix and history is not rewritten.
    "d271d89249a6f7b8c45684e1a235ca518e53e95d",
})


def _workflow_trailer_lookalike_violations(repo_root: Path, commits: list[str]) -> dict[str, list[str]]:
    """For each commit, the `Workflow-*: value` lines that appear inside an
    otherwise trailer-block-shaped paragraph (every non-empty line in that
    paragraph matches `key: value`) but do not come out of `git
    interpret-trailers --parse` -- `_commit_trailers`'s exact mechanism.
    Returns `{commit: [description, ...]}` for violating commits only."""
    violations: dict[str, list[str]] = {}
    for commit in commits:
        body = subprocess.run(
            ["git", "log", "-1", "--format=%B", commit],
            cwd=repo_root, check=True, capture_output=True, text=True,
        ).stdout
        lookalikes = [
            m.groups()
            for paragraph in _message_paragraphs(body)
            if all(_TRAILER_SHAPED_LINE_RE.match(line) for line in paragraph)
            for line in paragraph
            if (m := _WORKFLOW_TRAILER_LOOKALIKE_RE.match(line))
        ]
        if not lookalikes:
            continue
        parsed = ws._commit_trailers(repo_root, commit)
        bad = [
            f"{key}: {value!r} does not parse as a trailer (parsed: {parsed})"
            for key, value in lookalikes if parsed.get(key) != value
        ]
        if bad:
            violations[commit] = bad
    return violations


class TestAgainstRealRepository(unittest.TestCase):
    def test_the_bootstrap_driver_is_unreachable_and_fail_closed_at_live_head(self):
        """Convergence pass 12, ledger `O33` (external Optional `N5`).

        `/bootstrap-workflow-v2` is past its own stated retirement
        condition -- "retired -- deleted -- only at this work item's own
        `MILESTONE_COMPLETE`" -- and `workflow-v2-1-core` reached
        `MILESTONE_COMPLETE`. The disposition is to leave it in place
        rather than revive or redesign it, which is only defensible if
        it is genuinely unreachable *and* fail-closed. Until this pass
        that was asserted in a source comment in
        `workflow_integration_test.py`'s census and executed nowhere -- a
        claim, not evidence, which is precisely the substitution class
        this campaign is auditing for.

        So: run it. The command's hardcoded target work item is read from
        the live state file, and every writer its two branches would need
        is called against that live entry."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        work_item = state["work_items"][WORK_ITEM_ID]
        registry = json.loads(
            (repo_root / "docs/ai-workflow/registry/workflow-v2-1-core-registry.json").read_text()
        )

        # The command file still exists and is still hardcoded to this item
        # -- so "unreachable" has to be about behaviour, not absence.
        command = (repo_root / ".claude" / "commands" / "bootstrap-workflow-v2.md").read_text()
        self.assertIn("Work item: `workflow-v2-1-core` (hardcoded, never an argument)", command)
        self.assertIn("It is retired — deleted — only at this work item's own", command)

        # 1. Terminal, by its own registry as well as by its phase field.
        self.assertEqual(work_item["phase"], "MILESTONE_COMPLETE")
        is_terminal, outstanding = ws.resolve_own_registry_completion_status(repo_root, work_item)
        self.assertTrue(is_terminal)
        self.assertIsNone(outstanding)

        # 2. Its per-invocation branch ("implements exactly one checkpoint")
        #    can select nothing.
        self.assertIsNone(ws.select_next_checkpoint(work_item, registry))

        # 3. And its terminal wrap-up branch is refused, not merely idle:
        #    both writers that branch would call reject this phase by name.
        with self.assertRaises(ws.IllegalSelfReviewEntryPhaseError):
            ws.enter_self_reviewing_implementation(
                state, WORK_ITEM_ID, registry, "2026-01-01T00:00:00Z",
            )
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError):
            ws.record_bundle_generation(
                state, WORK_ITEM_ID, stage="implementation", head="0" * 40,
                now="2026-01-01T00:00:00Z", outcome="ordinary",
            )

    def test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set(self):
        """OPUS-R129-001's own mechanical guard, required acceptance
        criterion 2: every commit in `base..HEAD` whose message text
        contains a `Workflow-[A-Za-z-]+:` line inside a trailer-block-
        shaped paragraph must have that line actually parse as a Git
        trailer via `git interpret-trailers --parse` -- the exact
        mechanism `_commit_trailers`/`discover_checkpoint_commits` use. A
        line that looks like a trailer but isn't (because a blank line and
        further prose -- typically `Co-Authored-By:`/`Claude-Session:` --
        follows it, so Git treats only the message's actual last paragraph
        as trailers) is exactly `4a769fd`'s own defect class: the
        checkpoint- or approval-completing commit becomes permanently
        undiscoverable by the scoped trailer search, with no signal at
        commit time.

        History rewriting is not available (several of the already-
        violating SHAs are pinned into `WORKFLOW_STATE.json`/approved
        bundles), so this cannot assert zero violations outright -- the
        pre-fix violations are individually grandfathered by exact SHA
        above, not silently excluded by a blanket rule. What this test
        actually guards is *recurrence*: no commit outside that fixed,
        named set may violate this property, now that
        `.claude/commands/bootstrap-workflow-v2.md` step 6 and
        `.claude/commands/milestone-implement.md` step 1f both state the
        final-paragraph requirement explicitly. The equality assertion
        (not merely subset) also catches the opposite drift: a
        grandfathered SHA silently ceasing to reproduce would mean this
        constant itself has gone stale."""
        repo_root = _repo_root()
        commits = [
            line for line in subprocess.run(
                ["git", "log", "--format=%H", f"{BASE_COMMIT}..HEAD"],
                cwd=repo_root, check=True, capture_output=True, text=True,
            ).stdout.splitlines() if line
        ]
        self.assertTrue(commits, f"expected at least one commit in {BASE_COMMIT}..HEAD")
        violations = _workflow_trailer_lookalike_violations(repo_root, commits)
        observed = set(violations)
        new_violations = observed - _GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS
        self.assertEqual(
            new_violations, set(),
            f"{len(new_violations)} commit(s) beyond the grandfathered pre-fix set write a "
            f"Workflow-*: line Git does not parse as a trailer -- it must be the message's "
            f"own final paragraph: {[(c, violations[c]) for c in sorted(new_violations)]}",
        )
        healed = _GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS - observed
        self.assertEqual(
            healed, set(),
            f"{len(healed)} grandfathered SHA(s) no longer reproduce a violation -- "
            f"history is immutable, so this means the constant itself is stale (a rebase, "
            f"or a base-commit change): {sorted(healed)}",
        )

    def test_wf0_and_wf1a_are_discovered_via_real_trailer_search(self):
        repo_root = _repo_root()
        discovered = ws.discover_checkpoint_commits(repo_root, WORK_ITEM_ID, BASE_COMMIT)
        self.assertIn("WF0", discovered)
        self.assertEqual(discovered["WF0"], "8f76175348d0f63e61f6c1a9997b5004a27430fe")
        # WF1a's own commit is what this test suite is committed inside of.
        self.assertIn("WF1a", discovered)

    def test_all_eighteen_checkpoints_resolve_with_wf8b_canonical(self):
        """Item 39's real-repository proof: `WF8b` carries the
        `Workflow-Checkpoint: WF8b` trailer on sixteen distinct commits
        (this work item's own continued-scope WF8b round left many
        commits along the way, all first-parent ancestors of `HEAD`), so
        filter (1) alone cannot disambiguate it -- only the role-specific
        verification tie-break (`_checkpoint_commit_claims_complete`) can,
        by finding the one commit whose own committed
        `WORKFLOW_STATE.json` records `WF8b` as `COMPLETE` for
        `workflow-v2-1-core`. All registered checkpoints resolve to
        exactly one commit each (eighteen since `WF8c` joined the
        registry; OPUS-R129-001's fix is what lets `WF8c` itself resolve
        here too, previously masked by `AmbiguousCheckpointTrailerError`
        raising before this test's own assertions were ever reached), and
        `WF8b` resolves to `f37c4e0`, the real completion commit
        (`feat(wf8b): S17 -- restore active pointer, remove dry-run
        entries, complete WF8b`)."""
        repo_root = _repo_root()
        discovered = ws.discover_checkpoint_commits(repo_root, WORK_ITEM_ID, BASE_COMMIT)
        registry = json.loads((repo_root / "docs/ai-workflow/registry/workflow-v2-1-core-registry.json").read_text())
        registered_ids = [entry["id"] for entry in registry["checkpoints"]]
        self.assertEqual(len(registered_ids), 18)
        for checkpoint_id in registered_ids:
            self.assertIn(checkpoint_id, discovered, f"{checkpoint_id} did not resolve")
        self.assertEqual(discovered["WF8b"], "f37c4e04f2358c8d1d5dec333b43538e2f087d15")

    def test_real_config_file_is_schema_valid(self):
        repo_root = _repo_root()
        config = ws.load_config(repo_root)
        ws.validate_config(config)

    def test_real_registry_is_topologically_ordered(self):
        repo_root = _repo_root()
        registry = json.loads((repo_root / "docs/ai-workflow/registry/workflow-v2-1-core-registry.json").read_text())
        ws.validate_registry_topological_order(registry)

    def test_072_generated_markdown_registry_view_row_order_matches_json_array_order(self):
        """Missing-test item 72 (OPUS-R10-009), D-Selection point 3: the
        'Checkpoint registry' Markdown table in WORKFLOW_V2_PLAN.md is a
        generated, human-readable view of the registry JSON and must
        never drift from it -- a hand-edit, a regeneration bug, or a
        readability reordering of the view could otherwise silently break
        rule 2's determinism guarantee. Parses the real table's rows and
        the real registry JSON's checkpoints array, asserting id order
        matches (the original item 72 scope) and, per WF8c clause (r)
        (OPUS-R113-001/-002, restating GPT-R112-001), extends the check
        into a full-field comparison over exactly the five columns the
        table carries: id, name, depends_on, complexity, session_target.
        completion_obligations is deliberately excluded -- the table has
        no column for it, so there is nothing on the view side to compare
        against; it remains registry-only metadata for
        WFO-LEDGER-COVERAGE/WFR-68's obligation-resolution machinery."""
        repo_root = _repo_root()
        rows = _parse_checkpoint_registry_table(repo_root)
        registry = json.loads((repo_root / "docs/ai-workflow/registry/workflow-v2-1-core-registry.json").read_text())
        json_checkpoints = registry["checkpoints"]

        row_ids = [row["id"] for row in rows]
        json_ids = [entry["id"] for entry in json_checkpoints]
        self.assertEqual(
            row_ids, json_ids,
            "the Checkpoint registry Markdown table's row order has drifted from "
            "the registry JSON's checkpoints array order",
        )

        registry_by_id = {entry["id"]: entry for entry in json_checkpoints}
        mismatches = []
        for row in rows:
            mismatches.extend(_registry_row_mismatches(row, registry_by_id[row["id"]]))
        self.assertEqual(
            mismatches, [],
            f"{len(mismatches)} field(s) diverged between the Checkpoint registry "
            f"Markdown table and the registry JSON (checkpoint, field, table value, "
            f"json value): {mismatches}",
        )

    def test_072_full_field_comparison_catches_a_real_divergence_not_vacuously_true(self):
        """The positive test above proves nothing if a table/JSON mismatch
        can never actually be detected. Runs the real
        _registry_row_mismatches comparison used by the positive test
        against the real WF0 table row paired with a deliberately
        tampered copy of the real WF0 registry entry, once per compared
        field, and confirms each tamper is individually flagged."""
        repo_root = _repo_root()
        rows = _parse_checkpoint_registry_table(repo_root)
        wf0_row = next(row for row in rows if row["id"] == "WF0")
        registry = json.loads((repo_root / "docs/ai-workflow/registry/workflow-v2-1-core-registry.json").read_text())
        wf0_entry = next(e for e in registry["checkpoints"] if e["id"] == "WF0")

        self.assertEqual(_registry_row_mismatches(wf0_row, wf0_entry), [])

        tampered = dict(wf0_entry, name=wf0_entry["name"] + " tampered")
        fields = {m[1] for m in _registry_row_mismatches(wf0_row, tampered)}
        self.assertEqual(fields, {"name"})

        tampered = dict(wf0_entry, depends_on=["WF4a-i"])
        fields = {m[1] for m in _registry_row_mismatches(wf0_row, tampered)}
        self.assertEqual(fields, {"depends_on"})

        tampered = dict(wf0_entry, complexity=wf0_entry["complexity"] + 1)
        fields = {m[1] for m in _registry_row_mismatches(wf0_row, tampered)}
        self.assertEqual(fields, {"complexity"})

        tampered = dict(wf0_entry, session_target=wf0_entry["session_target"] + "0")
        fields = {m[1] for m in _registry_row_mismatches(wf0_row, tampered)}
        self.assertEqual(fields, {"session_target"})

    def test_real_registry_and_mapping_have_full_bidirectional_coverage(self):
        repo_root = _repo_root()
        registry = json.loads((repo_root / "docs/ai-workflow/registry/workflow-v2-1-core-registry.json").read_text())
        mapping = json.loads((repo_root / "docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json").read_text())
        ws.validate_registry_mapping_coverage(registry, mapping)

    def test_real_state_file_is_schema_valid_against_the_real_registry(self):
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        registry = json.loads((repo_root / "docs/ai-workflow/registry/workflow-v2-1-core-registry.json").read_text())
        ws.validate_state(state, registry=registry)
        # GPT-R31-003: also exercise the whole-state plan-revision-mirror
        # check against every real work item's own registry_path, not
        # only workflow-v2-1-core's. Also serves as GPT-R33-002's required
        # "all existing repository work items pass the tracked-path check"
        # case: every real work item's registry_path is a Git-tracked
        # file in this actual repository, so this call only stays green
        # under the stricter tracked-file validator if that remains true.
        ws.validate_state(state, registry=registry, repo_root=repo_root)

    def test_real_state_file_checkpoint_completions_are_reachable(self):
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        work_item = state["work_items"][WORK_ITEM_ID]
        ws.verify_checkpoint_completions(work_item, repo_root, work_item["base_commit"])

    def test_real_state_file_current_plan_approval_is_discoverable_and_matches_manifest(self):
        """`plan_approval.approved_review_content_id` was legitimately
        advanced past WF0's original approval by 2da0b66 ("remediate stale
        plan_approval after Milestone 8 merge") -- a later commit that is
        not WF0 and carries no Workflow-Checkpoint trailer of its own. The
        durable invariant is discoverability via the generalized
        approval-trailer search (WF4a-iii), not literal identity with
        WF0's commit: this asserts the current identifier resolves to
        exactly one reachable Workflow-Plan-Approval commit for
        workflow-v2-1-core (discover_plan_approval_commit raises on
        genuine ambiguity rather than returning one), and that commit's
        own trailer and tree content match the state file's approval
        metadata and manifest."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        work_item = state["work_items"][WORK_ITEM_ID]
        approval = work_item["plan_approval"]

        discovered = ws.discover_plan_approval_commit(
            repo_root, WORK_ITEM_ID, approval["approved_review_content_id"], BASE_COMMIT,
        )
        self.assertIsNotNone(
            discovered,
            "current approved_review_content_id is not reachable via the "
            "generalized Workflow-Plan-Approval trailer search",
        )

        body = subprocess.run(
            ["git", "log", "-1", "--format=%B", discovered],
            cwd=repo_root, check=True, capture_output=True, text=True,
        ).stdout
        self.assertIn(f"Workflow-Plan-Approval: {approval['approved_review_content_id']}", body)
        self.assertIn(f"Workflow-Work-Item: {WORK_ITEM_ID}", body)

        # Finding B: the full `reviewed_bundle_id` is not a normative
        # commit-trailer/body requirement (Revision 80 specifies neither a
        # bundle-id trailer nor the full id in free-text prose -- prose
        # may legitimately truncate it, as this repository's real approval
        # commit does). The durable source of truth is the approval
        # commit's own *committed* WORKFLOW_STATE.json, not its free-form
        # message -- verify against that instead.
        committed_state = ws._read_json_at_commit_or_empty(
            repo_root, discovered, "docs/ai-workflow/WORKFLOW_STATE.json",
        )
        committed_approval = committed_state["work_items"][WORK_ITEM_ID]["plan_approval"]
        self.assertEqual(committed_approval["reviewed_bundle_id"], approval["reviewed_bundle_id"])

        for entry in approval["review_content_manifest"]:
            blob = subprocess.run(
                ["git", "rev-parse", f"{discovered}:{entry['path']}"],
                cwd=repo_root, check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(
                blob, entry["blob"],
                f"{entry['path']} blob at the discovered approval commit {discovered} "
                "does not match plan_approval.review_content_manifest",
            )

    def test_reviewed_implementation_head_equals_the_generation_records_own_first_parent(self):
        """OPUS-R129-002: retires this test's own pre-`WF8B-003` claim
        (`GPT-R31-001`: `record_bundle_generation`'s own state write must
        not itself be a separate git commit). `D-Approval-Commits`
        revision 28 inverted that rule: the durability commit is now
        mandatory and must land *before* generation, so by the time a
        bundle is actually generated, `reviewed_implementation_head` is
        already durable inside a dedicated `Workflow-Bundle-Generation-
        Record: <work_item_id>/<implementation_revision>` commit -- which
        is itself the *child* of the commit it records, never equal to
        live HEAD. The old assertion (`reviewed_implementation_head ==
        live HEAD`) is therefore false on every round under the current
        contract and would stay red forever.

        The real invariant does not require the generation-record commit
        to *be* live HEAD -- ordinary remediation commits legitimately
        land on top of it during `APPLYING_REVIEW_FEEDBACK`, before the
        next bundle is generated, and this test must not go stale again
        the same way the retired one did the moment that happens. What
        must hold, permanently, once a generation-record commit for this
        `implementation_revision` exists anywhere in `base..HEAD`: that
        commit is discoverable, and its own recorded
        `reviewed_implementation_head` equals that same commit's own
        first parent -- the commit that was actually reviewed, never the
        durability commit recording it."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        item = state["work_items"][WORK_ITEM_ID]
        live_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertIsNotNone(
            item["reviewed_implementation_head"],
            "no bundle has ever been generated for this work item yet",
        )
        record_commit = ws.discover_current_bundle_generation_record_commit(
            repo_root, WORK_ITEM_ID, item["base_commit"], live_head, item["implementation_revision"],
        )
        self.assertIsNotNone(
            record_commit,
            f"no Workflow-Bundle-Generation-Record commit found for "
            f"{WORK_ITEM_ID}/{item['implementation_revision']} in base..HEAD",
        )
        first_parent = subprocess.run(
            ["git", "rev-parse", f"{record_commit}^"], cwd=repo_root, check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(
            item["reviewed_implementation_head"], first_parent,
            "reviewed_implementation_head must equal the generation-record commit's own "
            "first parent -- the commit that was actually reviewed, never the durability "
            "commit recording it",
        )

    def test_historical_wf0_plan_approval_remains_independently_discoverable(self):
        """WF0's own original Workflow-Plan-Approval identifier was
        superseded (not invalidated) by the later remediation commit
        above -- it must remain independently discoverable under its own
        identifier, without requiring it to equal the current
        plan_approval.approved_review_content_id (mirrors
        test_wf0_and_wf1a_are_discovered_via_real_trailer_search's
        checkpoint-trailer counterpart, for the approval-trailer
        search)."""
        repo_root = _repo_root()
        all_approvals = ws.discover_approval_commits(
            repo_root, "Workflow-Plan-Approval", WORK_ITEM_ID, BASE_COMMIT,
        )
        original_wf0_review_content_id = (
            "e85533a91d40cd43d6066f0435e90d7b3a94e64e50d58aceec3e4363e60dd030"
        )
        self.assertEqual(
            all_approvals.get(original_wf0_review_content_id),
            "8f76175348d0f63e61f6c1a9997b5004a27430fe",
        )

    def test_real_implementing_entry_is_reachable_at_current_head(self):
        """D-Approval-Commits' full IMPLEMENTING entry condition, exercised
        against this work item's own real history: the plan-approval
        commit (WF0) is a first-parent ancestor of current HEAD, and the
        plan-stage projection is unchanged by every checkpoint commit
        since (missing-test item 9, real-repository half)."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        work_item = state["work_items"][WORK_ITEM_ID]
        self.assertTrue(ws.implementing_entry_reachable(
            repo_root, work_item, work_item["base_commit"], head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT
        ))

    def test_real_plan_approval_is_current(self):
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        work_item = state["work_items"][WORK_ITEM_ID]
        self.assertTrue(
            ws.approval_is_current(
                repo_root, work_item, stage="plan", base_commit=work_item["base_commit"],
                head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
            )
        )

    def test_real_implementation_stage_classification_has_no_unclassified_dirty_path(self):
        """any_protected_path_dirty fails closed on an unclassified dirty
        path -- run here against whatever this working tree's real
        uncommitted state happens to be, proving the real
        artifact-declarations file classifies it either way (missing-test
        item 16, real-repository half; the hermetic half is
        TestProtectedPathDirty in workflow_state_test.py)."""
        repo_root = _repo_root()
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            ws.fingerprint.load_implementation_stage_classification(
                repo_root, fingerprint.artifacts_path_for_work_item("workflow-v2-1-core"),
            )
        )
        ws.any_protected_path_dirty(
            repo_root, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
        )  # must not raise

    def test_real_active_work_item_implementation_stage_changed_set_classifies_exhaustively(self):
        """`workflow-v2-3`'s own CP1 missing-test item (revision 9, round 8
        missing tests): the *changed-since-base_commit* set (not the
        dirty-only set the test above covers), classified against
        whichever item is *currently* active's own artifacts declaration
        (`artifacts_path_for_work_item(active_work_item_id)`, never the
        hardcoded `workflow-v2-1-core` default) -- so a future work item's
        own under-widened implementation-stage declaration fails this
        suite immediately, the way B1's own defect (revision 3) should
        have been caught at generation time rather than at external
        review. Deliberately live `"HEAD"`-scoped: this checks the
        *currently active* item's own still-moving footprint, unlike the
        seven tests fixed at `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` above,
        which validate a fact about `workflow-v2-1-core`'s own closed
        history instead.

        `active_work_item_id` is a legitimate, documented `null` value
        (`workflow_state.py`'s own `validate_state` invariant only
        constrains it when *not* `None`) when the repository is quiescent
        between work items -- e.g. right after a `MILESTONE_COMPLETE`
        acceptance resets it, as `workflow-v2-3`'s own did. There is no
        "currently active item" for this test's assertion to mean anything
        about in that state, so it skips rather than either crashing on a
        `None` dict key or fabricating a fake active item / mutating
        `WORKFLOW_STATE.json` to force one (baseline-freeze verification
        cleanup, 2026-08-24)."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        active_work_item_id = state["active_work_item_id"]
        if active_work_item_id is None:
            self.skipTest(
                "active_work_item_id is null -- the repository is legitimately "
                "quiescent (no live work item) right now, so there is no "
                "currently active item's changed-set to classify"
            )
        work_item = state["work_items"][active_work_item_id]
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            fingerprint.load_implementation_stage_classification(
                repo_root, artifacts_path=fingerprint.artifacts_path_for_work_item(active_work_item_id)
            )
        )
        changed = sorted(
            fingerprint._changed_tracked_paths_between(repo_root, work_item["base_commit"], "HEAD")
        )
        for path in changed:
            with self.subTest(path=path):
                try:
                    fingerprint.classify_path_implementation_stage(
                        path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
                    )
                except fingerprint.UnclassifiedPathError:
                    self.fail(
                        f"{path} changed since {active_work_item_id!r}'s own base_commit "
                        f"{work_item['base_commit']} but its own implementation-stage "
                        f"classifier does not recognize it as protected or excluded"
                    )

    def test_real_active_work_item_plan_stage_changed_set_classifies_exhaustively(self):
        """Plan-stage counterpart of the test immediately above (revision
        9, round 8 missing tests): the currently active item's own live
        `plan_stage` declaration (`resolve_plan_stage_metadata`), exercised
        against the same changed-since-base_commit set. Neither of the
        other new tests in this file exercises this: the active-work-item
        test above is implementation-stage only, and the standalone
        assertion below is pinned to `workflow-v2-1-core`'s own frozen
        constants and closed history range.

        Same `active_work_item_id is None` skip as the test immediately
        above, for the same reason: a quiescent repository with no active
        work item has no "currently active item" for this assertion to
        mean anything about (baseline-freeze verification cleanup,
        2026-08-24)."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        active_work_item_id = state["active_work_item_id"]
        if active_work_item_id is None:
            self.skipTest(
                "active_work_item_id is null -- the repository is legitimately "
                "quiescent (no live work item) right now, so there is no "
                "currently active item's changed-set to classify"
            )
        work_item = state["work_items"][active_work_item_id]
        metadata = fingerprint.resolve_plan_stage_metadata(repo_root, active_work_item_id)
        changed = sorted(
            fingerprint._changed_tracked_paths_between(repo_root, work_item["base_commit"], "HEAD")
        )
        for path in changed:
            with self.subTest(path=path):
                try:
                    fingerprint.classify_path(
                        path, metadata.protected_paths, metadata.excluded_paths, metadata.excluded_prefixes
                    )
                except fingerprint.UnclassifiedPathError:
                    self.fail(
                        f"{path} changed since {active_work_item_id!r}'s own base_commit "
                        f"{work_item['base_commit']} but its own plan-stage classifier "
                        f"does not recognize it as protected or excluded"
                    )

    def test_real_workflow_v2_1_core_plan_stage_classification_gap_is_closed_at_completion_commit(self):
        """The direct, standalone test of exactly B1's own failure class
        (revision 4, missing tests item 2): asserts
        `assert_all_changed_paths_classified_commit` raises nothing for
        `workflow-v2-1-core`'s own closed `162154d3..27f051eb` history,
        against the frozen `PLAN_STAGE_*` constants -- naming the
        classification gap directly as its own assertion rather than only
        as a precondition buried inside a digest computation."""
        repo_root = _repo_root()
        fingerprint.assert_all_changed_paths_classified_commit(
            repo_root, BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
            protected=fingerprint.PLAN_STAGE_PROTECTED,
            excluded_paths=fingerprint.PLAN_STAGE_EXCLUDED_PATHS,
            excluded_prefixes=fingerprint.PLAN_STAGE_EXCLUDED_PREFIXES,
        )  # must not raise

    def test_real_state_file_no_non_terminal_work_item_holds_a_legacy_cased_plan_review_stage_key(self):
        """O3 (`workflow-v2-3-followups` round-1 implementation review,
        folded into continued scope): CP3's own deprecation condition --
        "no live non-terminal work item holds a legacy-cased
        `plan_review_stages` key" -- was previously verified once, by
        hand, in that round's own `TEST_RESULTS.md`, and by nothing
        thereafter. This asserts it directly against the live state file,
        so a legacy-cased ledger re-entering the file (hand edit,
        restored backup, imported legacy item) fails this test rather
        than silently going unnoticed."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        for work_item_id, work_item in state["work_items"].items():
            if work_item.get("phase") in ws.TERMINAL_PHASES:
                continue
            stages = work_item.get("plan_review_stages")
            if not stages:
                continue
            for key in stages:
                if key == "review_content_id":
                    continue
                with self.subTest(work_item_id=work_item_id, key=key):
                    self.assertEqual(
                        ws._normalize_plan_review_stage_key(key), key,
                        f"{work_item_id!r}'s plan_review_stages holds legacy-cased key {key!r} "
                        f"while non-terminal (phase {work_item.get('phase')!r}) -- CP3's "
                        f"deprecation condition no longer holds",
                    )


class TestLegacyImportAgainstRealMilestone8(unittest.TestCase):
    """WF-M8a's own real-repository check: Milestone 8's actual, already-
    integrated history satisfies D-Legacy's branch-reconciliation
    precondition, and its backfilled approved_review_content_id (recorded
    in docs/ai-workflow/WORKFLOW_STATE.json's work_items["milestone-8"])
    reproduces exactly from milestone-8-artifacts.json's declarations *as
    WF-M8a originally authored them* (commit WF_M8A_COMMIT below), scoped
    to Milestone 8's own base_commit..reviewed_content_commit -- never
    workflow-v2-1-core's own base_commit or artifacts file. WF-M8b later
    widens this same file's excluded sets for its own, separate,
    non-hash-based promotion-time freshness check
    (any_protected_path_changed_since) -- that widening deliberately
    changes what recomputing from the file's *current* content would
    produce (the full classification config is embedded in the hash), so
    this test pins the exact historical commit rather than reading the
    live file, to keep proving the stored ID's provenance without being
    broken by that later, legitimate widening."""

    BASE_COMMIT = "2d09ec02252848124d0e1accfbefb57dc8561872"
    REVIEWED_CONTENT_COMMIT = "dc4381a348c114ec4967174c4e6a76ac00b1a537"
    ARTIFACTS_PATH = "docs/ai-workflow/registry/milestone-8-artifacts.json"
    WF_M8A_COMMIT = "2baf7bcfdd12006335e65f7fbae41e5d1fa4a8e3"

    def test_reviewed_commit_is_reachable_and_active_milestone_confirms_acceptance(self):
        repo_root = _repo_root()
        ws.verify_legacy_branch_reconciliation(
            repo_root, reviewed_content_commit=self.REVIEWED_CONTENT_COMMIT,
            required_active_milestone_substring="accepted and closed",
        )  # must not raise

    def test_backfilled_review_content_id_reproduces_from_declarations_as_originally_authored(self):
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        milestone_8 = state["work_items"]["milestone-8"]
        approval = milestone_8["technical_approval"]
        self.assertEqual(approval["basis"], "LEGACY_V1")
        self.assertEqual(approval["reviewed_content_commit"], self.REVIEWED_CONTENT_COMMIT)
        original_declarations = json.loads(
            subprocess.run(
                ["git", "show", f"{self.WF_M8A_COMMIT}:{self.ARTIFACTS_PATH}"],
                cwd=repo_root, check=True, capture_output=True, text=True,
            ).stdout
        )
        recomputed, _ = fingerprint.compute_review_content_id_implementation_stage_at_commit(
            repo_root, milestone_8["base_commit"], self.REVIEWED_CONTENT_COMMIT,
            "product", "milestone-8",
            original_declarations["protected_paths"], original_declarations["protected_prefixes"],
            original_declarations["excluded_paths"], original_declarations["excluded_prefixes"],
        )
        self.assertEqual(recomputed, approval["approved_review_content_id"])

    def test_milestone_8_entry_is_dormant_not_active(self):
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        self.assertEqual(state["work_items"]["milestone-8"]["phase"], "LEGACY_READY")
        self.assertNotEqual(state.get("active_work_item_id"), "milestone-8")


class TestLegacyPromotionAgainstRealMilestone8(unittest.TestCase):
    """WF-M8b's own real-repository check: `promote_legacy_work_item`
    succeeds against the real dormant `milestone-8` entry as of this
    commit -- both the branch-reconciliation re-check and the
    implementation-stage freshness recomputation (against milestone-8's
    own real `milestone-8-artifacts.json`, never `workflow-v2-1-core`'s)
    pass on real repository content. Exercised read-only: the returned
    state is never persisted back to `WORKFLOW_STATE.json`, so this test
    only proves adoption is currently reachable -- it is not itself an
    adoption."""

    ARTIFACTS_PATH = Path("docs/ai-workflow/registry/milestone-8-artifacts.json")

    def test_promotion_succeeds_against_the_real_dormant_entry(self):
        repo_root = _repo_root()
        state_path = repo_root / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        new_state = ws.promote_legacy_work_item(
            state, repo_root, work_item_id="milestone-8",
            required_active_milestone_substring="accepted and closed",
            artifacts_path=self.ARTIFACTS_PATH, now="demo-only, never persisted",
        )
        entry = new_state["work_items"]["milestone-8"]
        self.assertEqual(new_state["active_work_item_id"], "milestone-8")
        self.assertEqual(entry["governing_workflow_version"], "2.1")
        self.assertEqual(entry["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(entry["technical_approval"], state["work_items"]["milestone-8"]["technical_approval"])
        # Read-only: the real on-disk file is provably untouched.
        self.assertEqual(json.loads(state_path.read_text()), state)


class TestCheckpointOriginationAgainstRealRepository(unittest.TestCase):
    """WF8b's `D-Checkpoint-Ownership` origination-reference slice
    (`checkpoint_origination_provable`), checked read-only against this
    repository's own real, already-committed defect: the
    `workflow-v2-1-core` Revision-80 plan-approval commit (`8f8d878`)
    swept `v2-1-dry-run`'s dirty `S-CP3` `IN_PROGRESS` delta into `HEAD`
    as a side effect of staging the whole plan-stage protected surface,
    recorded in commit `25246a5`. This was the concrete case the S14/S15
    dry-run scenarios needed an explicit takeover for; S14/S15 have since
    run for real (2026-08-15) and carried `S-CP3` through to `COMPLETE`,
    releasing the claim the takeover published -- the origination defect
    itself remains permanently reproducible (it scans fixed history), but
    the claim- and status-dependent assertions below reflect that
    completion rather than the interrupted `IN_PROGRESS` state."""

    def test_v2_1_dry_run_s_cp3_origination_is_unprovable(self):
        repo_root = _repo_root()
        with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
            ws.checkpoint_origination_provable(repo_root, "v2-1-dry-run", "S-CP3")
        evidence = ctx.exception.evidence
        self.assertEqual(evidence["route"], "observed")
        self.assertEqual(evidence["status"], "IN_PROGRESS")
        self.assertEqual(evidence["commit"], "8f8d878c0985da96d9b462703b6c88ec5b3ab07b")

    def test_a_never_started_checkpoint_id_admits(self):
        repo_root = _repo_root()
        result = ws.checkpoint_origination_provable(
            repo_root, "workflow-v2-1-core", "NO-SUCH-CHECKPOINT-ID-EVER-USED",
        )
        self.assertEqual(result["decision"], "admit")

    def test_checkpoint_origination_provable_still_refuses_for_s_cp3_permanently(self):
        """`checkpoint_origination_provable` scans
        `origination_reference_commits`, a fixed slice of already-committed
        history -- so the real, already-committed defect (`8f8d878`, which
        recorded `v2-1-dry-run`'s `S-CP3` `IN_PROGRESS` as a side effect of
        an unrelated `workflow-v2-1-core` plan-approval commit) refuses
        unconditionally and permanently, regardless of `S-CP3`'s own
        *current* status: this repository's WF8b S15 session (2026-08-15)
        completed `S-CP3` for real (`Workflow-Checkpoint: S-CP3`,
        `Workflow-Work-Item: v2-1-dry-run`) and released the claim the
        prior session's takeover had published, and the refusal is
        unchanged by either fact, since this function only ever looks
        backward. Supersedes this test's prior form, which additionally
        asserted claim-record byte-identity across the call -- that
        assertion no longer applies now that no claim record exists at
        all (S-CP3 is `COMPLETE`, not `IN_PROGRESS`); see
        `test_adopt_claim_refuses_as_not_in_progress_once_s_cp3_is_complete`
        for the claim-side consequence. Read-only: verified by an explicit
        before/after `git status --short` comparison."""
        repo_root = _repo_root()
        before = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
            ws.checkpoint_origination_provable(repo_root, "v2-1-dry-run", "S-CP3")
        evidence = ctx.exception.evidence
        self.assertEqual(evidence["route"], "observed")
        self.assertEqual(evidence["commit"], "8f8d878c0985da96d9b462703b6c88ec5b3ab07b")
        after = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        self.assertEqual(before, after)

    def test_adopt_claim_refuses_as_not_in_progress_once_v2_1_dry_run_is_removed(self):
        """`adopt_claim`'s own first precondition -- this worktree's local
        `WORKFLOW_STATE.json` must record `checkpoint_id` `IN_PROGRESS` --
        is checked *before* `checkpoint_origination_provable` ever runs
        (`adopt_claim`'s own docstring, step 1 before step 3). S17
        (`f37c4e0`, 2026-08-15) removed `v2-1-dry-run`'s dry-run entries
        from `WORKFLOW_STATE.json` entirely -- it is no longer a
        `COMPLETE` entry, it is *absent* -- so that precondition now reads
        a `None` status rather than `COMPLETE`, and `adopt_claim` still
        refuses with `CheckpointNotInProgressLocallyError`, just with a
        different reported status. Supersedes this test's S15-era form
        (`... once s_cp3_is_complete`, asserting `"COMPLETE"` in the
        refusal message) -- that assertion is now false, not stale
        evidence to preserve, since S17's cleanup removed the item rather
        than leaving it completed. Read-only: verified by an explicit
        before/after `git status --short` comparison and by confirming no
        claim file exists before or after."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        self.assertNotIn("v2-1-dry-run", state["work_items"])
        claim_file = ws.claim_path(repo_root, "v2-1-dry-run")
        self.assertFalse(claim_file.exists())
        before = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        with self.assertRaises(ws.CheckpointNotInProgressLocallyError) as ctx:
            ws.adopt_claim(repo_root, "v2-1-dry-run", "S-CP3", now="demo")
        self.assertIn("status: None", str(ctx.exception))
        after = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        self.assertEqual(before, after)
        self.assertFalse(claim_file.exists())
        self.assertIsNone(ws.resolve_claim(repo_root, "v2-1-dry-run"))

    def test_no_stale_claim_survives_v2_1_dry_run_removal(self):
        """S17 removed `v2-1-dry-run` from live `work_items` entirely,
        which also means there is no `work_item` dict left to drive
        `select_next_checkpoint`/`resolve_checkpoint_ownership` against
        for this id anymore -- the claim-side read-only invariant that
        still applies is simply that no claim file was left behind by the
        removal (`release_checkpoint` already released S-CP3's claim
        during WF8b S15, before S17 removed the item itself), so there is
        nothing for a fresh session to adopt or resolve ownership of.
        Supersedes this test's S15-era form
        (`..._reaches_no_checkpoint_once_s_cp3_is_complete`, which read
        `state["work_items"]["v2-1-dry-run"]` and drove
        `resolve_checkpoint_ownership` against it) -- that lookup now
        raises `KeyError` since the entry no longer exists, so the
        checkpoint-selection assertions no longer apply; the claim/
        read-only assertions they were paired with do still apply and are
        preserved here. Read-only: verified by an explicit before/after
        `git status --short` comparison."""
        repo_root = _repo_root()
        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        self.assertNotIn("v2-1-dry-run", state["work_items"])
        claim_file = ws.claim_path(repo_root, "v2-1-dry-run")
        before = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        self.assertFalse(claim_file.exists())
        self.assertIsNone(ws.resolve_claim(repo_root, "v2-1-dry-run"))
        after = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        self.assertEqual(before, after)

    def test_bootstrap_driver_never_calls_this_slice(self):
        """Scope-boundary regression: `/bootstrap-workflow-v2` derives
        completion from commit trailers alone and never enters
        `[2.1 step 1]` (D-Checkpoint-Ownership's own "Scope boundaries"
        bullet), so this slice must stay unreachable from it -- confirmed
        structurally, not merely by convention. `workflow-v2-1-core`'s own
        `WF8b` checkpoint *is* observed `IN_PROGRESS` in committed history
        (five commits, per the plan's "Scope boundaries" text) and would
        itself refuse this check -- proving the point only if the
        bootstrap driver never reaches it."""
        repo_root = _repo_root()
        command_text = (repo_root / ".claude/commands/bootstrap-workflow-v2.md").read_text()
        self.assertNotIn("checkpoint_origination_provable", command_text)
        self.assertNotIn("resolve_checkpoint_ownership", command_text)
        with self.assertRaises(ws.CheckpointOriginationUnprovableError):
            ws.checkpoint_origination_provable(repo_root, "workflow-v2-1-core", "WF8b")

    def test_bootstrap_durability_guard_is_rebound_to_a_discovered_durable_commit(self):
        """`WF8c` item 352: `/bootstrap-workflow-v2`'s own step-2
        durability guard must no longer compare a freshly recomputed
        `review_content_id` against `plan_approval.approved_review_content_id`
        alone -- that bare form always agrees with itself regardless of
        whether any `Workflow-Plan-Approval` commit exists at all, since a
        working-tree-only state write and its own just-written record are
        definitionally equal. The installed command file's live text must
        call the same durable-commit-anchored check
        (`implementing_entry_reachable`, which additionally requires
        `discover_plan_approval_commit` to find a real commit and confirms
        it (or a checkpoint-commit descendant) is live `HEAD`) before it
        may treat the state record's word alone as sufficient -- and this
        repository's own real work item, at real `HEAD`, must actually
        satisfy that stronger check, not merely reference it in prose."""
        repo_root = _repo_root()
        command_text = (repo_root / ".claude/commands/bootstrap-workflow-v2.md").read_text()
        self.assertIn("implementing_entry_reachable", command_text)
        self.assertIn("discover_plan_approval_commit", command_text)

        state = json.loads((repo_root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
        work_item = state["work_items"][WORK_ITEM_ID]
        self.assertTrue(
            ws.implementing_entry_reachable(
                repo_root, work_item, work_item["base_commit"], head=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT
            ),
        )

    def test_bootstrap_step_0_journal_precondition_is_installed_and_ownership_guard_aware(self):
        """`WF8c` item 352's "cross-command obligation the journal
        creates": the installed command file must check for an open
        plan-approval transaction *before* step 1 runs, must never treat
        itself as authorized to resolve/replace/delete a journal it does
        not own (ownership-aware), and must also report a held mutation
        guard rather than treating an unheld journal token as the whole
        ownership picture (guard-aware, revision 58). Checked structurally
        against the installed text; the underlying read/classify/observe
        behavior itself is exercised against real `ScratchRepo` history by
        `workflow_integration_test.TestPlanApprovalMutationGuardAndTakeover
        .test_takeover_evidence_reports_a_fresh_unstarted_transaction_for_step_0`
        and its sibling held-guard test, both bound as this item's own
        evidence."""
        repo_root = _repo_root()
        command_text = (repo_root / ".claude/commands/bootstrap-workflow-v2.md").read_text()
        step_0_idx = command_text.index("0. **Plan-approval transaction precondition**")
        step_1_idx = command_text.index("1. **State-sync**")
        self.assertLess(step_0_idx, step_1_idx, "step 0 must precede step 1 in the installed text")
        step_0_text = " ".join(command_text[step_0_idx:step_1_idx].split())
        self.assertIn("plan_approval_takeover_evidence", step_0_text)
        self.assertIn("this invocation never holds its `owner_token`", step_0_text)
        self.assertIn("may not resolve, replace, or delete the journal itself", step_0_text)
        self.assertIn("evidence[\"guard\"]", step_0_text)
        self.assertIn("take_over_plan_approval_transaction", step_0_text)

        # This repository's own real transaction slot is not currently
        # open -- confirms step 0's own check has something real (an
        # absent journal, the ordinary case) to observe, not just prose.
        self.assertIsNone(ws.read_plan_approval_journal(repo_root))

    def test_approve_review_permanent_site_carries_the_full_failure_atomicity_transaction(self):
        """`WF8c` item 347: the installed `.claude/commands/approve-review.md`
        must state the unconditional (non-bootstrap) form of the failure-
        atomicity transaction -- verbatim-equivalent to `D-Approval-Commits`'
        own corrected text -- for every `"process"` work item, closing the
        bootstrap gap item 347 was tracking. Checked structurally: the old
        "does not carry the complete failure-atomicity transaction" scope
        note (present at every revision before this one) is gone, and the
        installed text names every one of the transaction's real primitives
        at the points its own steps 4b-6d describe them. Behavioral proof
        that the composed sequence these primitives describe actually works
        end to end lives in
        `workflow_integration_test.TestPlanApprovalPermanentSiteEndToEnd`,
        bound as this item's own evidence."""
        repo_root = _repo_root()
        command_text = (repo_root / ".claude/commands/approve-review.md").read_text()
        self.assertNotIn("does not carry the complete failure-atomicity transaction", command_text)
        self.assertNotIn("narrows, but does not close, item 347 itself", command_text)
        for name in (
            "plan_approval_takeover_evidence",
            "open_plan_approval_journal",
            "plan_approval_guarded_mutation",
            "plan_approval_state_matches_pre_transaction",
            "pin_plan_approval_state_blob",
            "classify_plan_approval_outcome",
            "rollback_plan_approval_transaction",
            "classify_plan_approval_materialize_target",
            "materialize_plan_approval_state",
            "close_plan_approval_journal",
        ):
            self.assertIn(name, command_text, f"{name} must be named in the installed procedure")

    def test_approve_review_refuses_workflow_v2_1_cores_own_plan_approval_until_retirement(self):
        """`WF8c` items 347/352's own retirement condition: until
        `docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s "Bootstrap plan-approval
        procedure" section is withdrawn (gated on a technical approval
        covering this exact work item's content, per the reconciliation
        table's `347-353` row), `workflow-v2-1-core`'s own plan-stage
        approvals must keep going through `/bootstrap-workflow-v2`'s own
        checklist, never this command -- every *other* work item is
        unaffected. Checked structurally against the installed text; and,
        since this repository's own live plan document still carries that
        section today, confirms the gate's own condition is presently
        true, not merely well-worded prose that never fires."""
        repo_root = _repo_root()
        command_text = (repo_root / ".claude/commands/approve-review.md").read_text()
        guard_idx = command_text.index("**Interim scope guard, `workflow-v2-1-core` only**")
        resolve_idx = command_text.index("Call `plan =")
        self.assertLess(guard_idx, resolve_idx, "the guard must run before member resolution")
        guard_text = " ".join(command_text[guard_idx:resolve_idx].split())
        self.assertIn('work_item_id == "workflow-v2-1-core"', guard_text)
        self.assertIn('stage == "plan"', guard_text)
        self.assertIn("Bootstrap plan-approval procedure", guard_text)
        self.assertIn("Every other work item", guard_text)

        plan_text = (repo_root / "docs/ai-workflow/WORKFLOW_V2_PLAN.md").read_text()
        self.assertIn("Bootstrap plan-approval procedure", plan_text)

    def test_identity_reference_admits_refuses_this_repositorys_own_reused_work_item_id(self):
        """WFR-66's identity-query enforcement against real history: the
        plan's own "one concrete instance" fact -- `workflow-v2-1-core`
        as a `work_item_id` has, obviously, appeared in every commit that
        ever touched its own `WORKFLOW_STATE.json` entry -- so a fresh
        attempt to *create* a work item under this same id must be
        permanently refused, on the identity-existence query alone,
        before `route_work_item` ever reaches the terminal-phase check.
        Read-only."""
        repo_root = _repo_root()
        before = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        with self.assertRaises(ws.WorkItemIdReusedError):
            ws.identity_reference_admits(repo_root, WORK_ITEM_ID)
        after = subprocess.run(
            ["git", "status", "--short"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout
        self.assertEqual(before, after)

    def test_identity_reference_admits_refuses_this_repositorys_own_wf8b_pair(self):
        """The same fact `checkpoint_origination_provable` already proves
        for `workflow-v2-1-core`/`WF8b` (observed `IN_PROGRESS` at five
        real commits) also makes it a decidably-observed pair for the
        *identity* query, so a future registry that tried to reintroduce
        a retired `WF8b` id would be refused the identical way."""
        repo_root = _repo_root()
        with self.assertRaises(ws.CheckpointIdReusedError):
            ws.identity_reference_admits(repo_root, WORK_ITEM_ID, "WF8b")

    def test_identity_reference_admits_admits_a_genuinely_unused_id(self):
        repo_root = _repo_root()
        result = ws.identity_reference_admits(repo_root, "an-id-never-used-anywhere-in-this-history")
        self.assertEqual(result["decision"], "admit")


class TestReconciliationTableLedgerStatusAgreement(unittest.TestCase):
    """WF8c (m)'s own "teeth check" against this repository's real
    content -- mirrors item 72's own registry/view agreement test's role
    in this file. Confirms `docs/ai-workflow/registry/workflow-v2-1-core-
    ledger-status.json` (generated via `parse_reconciliation_table`) is a
    faithful, total, duplicate-free transcription of the live plan's own
    '### Reconciliation table', and that `verify_wfo_ledger_coverage`
    (`WFR-68`'s own bound verifier, exercised against synthetic fixtures
    in `workflow_state_completion_obligations_test.py`) is now wired up
    and has real teeth against this repository's own content at `HEAD`."""

    def test_ledger_status_json_matches_the_live_reconciliation_table_exactly(self):
        repo_root = _repo_root()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        table = ws.parse_reconciliation_table(repo_root, head)

        expected_universe = {166} | set(range(167, 377))
        self.assertEqual(set(table.keys()), expected_universe)

        ledger_path = repo_root / ws.ledger_status_path_for_work_item(WORK_ITEM_ID)
        ledger = json.loads(ledger_path.read_text())
        entries = ledger["entries"]

        entry_items = [e["item"] for e in entries]
        self.assertEqual(len(entry_items), len(set(entry_items)), "duplicate item(s) in ledger-status.json")
        self.assertEqual(set(entry_items), expected_universe)

        for entry in entries:
            row = table[entry["item"]]
            self.assertEqual(
                (entry["status"], entry["owner_checkpoint"]), (row["status"], row["owner"]),
                f"item {entry['item']} disagrees between the plan table and ledger-status.json",
            )

    def test_ledger_status_json_is_now_bound_as_a_completion_obligation_conformance(self):
        """WF8c (m)'s remaining scope lands `verify_wfo_ledger_coverage`
        (properties (ii)-(iv), the four adversarial arms) and binds it --
        the standing guard from the part-1 session is updated in place,
        never deleted, to assert the new state rather than the old
        interim `UNKNOWN_OBLIGATION` one."""
        self.assertEqual(
            ws.COMPLETION_OBLIGATION_CONFORMANCE.get("WFO-LEDGER-COVERAGE"), "verify_wfo_ledger_coverage",
        )

    def test_real_ledger_status_json_now_passes_the_bound_verifier_at_head(self):
        """`GPT-R131-001`/`-002` closed the two remaining gaps between
        this repository's real 211-item ledger and `verify_wfo_ledger_
        coverage`'s pinned-commit re-execution: every real evidence id the
        verifier requires now resolves and executes under a real, pinned,
        detached worktree (`_materialize_pinned_worktree_at_commit`, not
        the prior `scripts/`-only scratch tree `_repo_root()`-style
        evidence could never run under), and the verifier no longer skips
        evidence validation for the 96 real `ABSENT`/`PARTIAL` `WF8c`-owned
        entries. `verify_wfo_ledger_coverage` against live `HEAD` therefore
        now derives `PASS` -- this replaces the prior round's own
        `test_real_ledger_status_json_does_not_yet_pass_the_bound_verifier`,
        which asserted the pre-fix `FAIL` as this obligation's "real teeth"
        proof; the assertion below is the same proof at the corrected
        outcome. `WFR-69`'s own pre-flight above already required this."""
        repo_root = _repo_root()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        result = ws.verify_wfo_ledger_coverage(repo_root, head)
        self.assertEqual(result["status"], "PASS", result.get("detail"))

    def test_every_real_evidence_id_the_final_wfo_gate_would_execute_resolves_under_the_pinned_runner(self):
        """`GPT-R131-001`'s own required regression: enumerates every
        evidence id the real 211-item ledger (`ledger-status.json`'s own
        `evidence` field, falling back to the companion `wf8c-evidence.
        json` entry for the same item, for every item that is not
        `SUPERSEDED`/`none` -- the identical resolution `verify_wfo_
        ledger_coverage` itself uses) and proves each is resolvable and
        executes green under the *actual pinned runner*
        (`_materialize_pinned_worktree_at_commit` /
        `_run_named_test_in_scratch`), not merely under `WFR-69`'s
        live-worktree pre-flight (`_load_and_run_named_test`) -- the two
        execution models this round found could disagree."""
        repo_root = _repo_root()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        ledger = json.loads(
            (repo_root / ws.ledger_status_path_for_work_item(WORK_ITEM_ID)).read_text()
        )
        companion = json.loads(
            (repo_root / ws.wf8c_evidence_path_for_work_item(WORK_ITEM_ID)).read_text()
        )
        companion_evidence = {
            e["item"]: e["evidence"] for e in companion["entries"] if e.get("evidence")
        }
        required_evidence: dict[str, list[int]] = {}
        for entry in ledger["entries"]:
            if entry["status"] == "SUPERSEDED" and entry["owner_checkpoint"] == "none":
                continue
            evidence = entry.get("evidence") or companion_evidence.get(entry["item"])
            self.assertTrue(evidence, f"item {entry['item']} has no evidence id to enumerate")
            required_evidence.setdefault(evidence, []).append(entry["item"])
        self.assertTrue(required_evidence, "expected at least one evidence id to enumerate")

        scratch_dir = ws._materialize_pinned_worktree_at_commit(repo_root, head)
        try:
            for evidence_id, items in sorted(required_evidence.items()):
                with self.subTest(evidence=evidence_id, items=items):
                    ok, detail = ws._run_named_test_in_scratch(scratch_dir, evidence_id)
                    self.assertTrue(
                        ok,
                        f"evidence {evidence_id!r} (items {items}) did not resolve/execute "
                        f"green under the pinned runner: {detail}",
                    )
        finally:
            ws._remove_pinned_worktree(repo_root, scratch_dir)


class TestCheckpointReachabilityConformanceLive(unittest.TestCase):
    """Item 355's own conformance test (WF8c clause (m), part 2), run
    against this repository's real, live content -- the "run against the
    live state and registry" claim item 355's own corrected-rule text
    makes for clauses (a)-(d) is exercised here, not merely asserted."""

    def test_holds_against_the_live_repository(self):
        repo_root = _repo_root()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        result = ws.verify_checkpoint_reachability_conformance(repo_root, head, WORK_ITEM_ID)
        self.assertEqual(result["status"], "PASS", result["detail"])


class TestReviewSubjectDeclarationsLive(unittest.TestCase):
    """`WFR-67`'s `review-subject:` header conformance (`WF8c` item (h),
    part 1), run against this repository's real fourteen command files at
    live `HEAD` -- proves the declaration half actually landed on every
    file the plan's own revision-80 text names, not only against synthetic
    fixtures. The roster was fifteen files until ledger `I10` deleted
    `.claude/commands/accept-scoped-remediation.md` (an exempt, `"none"`
    entry) with the retired command itself. As documented at
    `discover_review_subject_declarations`'s own
    docstring, the *value* each file carries here is a recorded,
    known-correct table (the eleven-consumer/three-exempt split:
    `workflow-v2-3`'s own `/review-implementation`, landed at that item's
    own CP1, is the seventh `bundle` consumer, and `/review-functional`,
    landed at CP2, is the eighth), not yet re-derived from each file's
    own prose against the three semantic disjuncts -- that derivation is
    separate, deferred `WF8c` scope. `recover-implementation-provenance.md`
    (added after `WFR-67`'s design was finalized, `WF8c` item (b)) is
    outside the roster and carries no declaration at all; its
    classification against `WFR-67`'s roster is deliberately left
    unassigned rather than resolved (see the roster comment above
    `REVIEW_SUBJECT_ROSTER` for why)."""

    EXPECTED = {
        ".claude/commands/accept-milestone.md": "none",
        ".claude/commands/apply-functional-review.md": "bundle",
        ".claude/commands/apply-implementation-review.md": "verdict",
        ".claude/commands/apply-plan-review.md": "verdict",
        ".claude/commands/approve-review.md": "bundle",
        ".claude/commands/bootstrap-workflow-v2.md": "none",
        ".claude/commands/milestone-implement.md": "bundle",
        ".claude/commands/milestone-plan.md": "bundle",
        ".claude/commands/prepare-functional-review.md": "none",
        ".claude/commands/prepare-review.md": "bundle",
        ".claude/commands/record-manual-plan-review.md": "verdict",
        ".claude/commands/review-functional.md": "bundle",
        ".claude/commands/review-implementation.md": "bundle",
        ".claude/commands/review-plan.md": "bundle",
    }

    # The "twice" consumers (existing pre-mutation refusal point + the
    # operation's own mutation guard) vs. the "once" report-only consumers
    # (revision 79: "the single assertion immediately preceding the report
    # IS the mutation-guard assertion"). `review-implementation.md` moved
    # from "once" to "twice" at `workflow-v2-3-followups` `CP2` (REQ-5,
    # `LPR-R1-I01`): it now writes `<feedback_dir>/REVIEW_FEEDBACK.md`
    # once its own pre-write guards -- including a second, immediately-
    # pre-write `assert_bundle_not_rejected` call -- pass, the same "real
    # write follows the guard" shape the other five "twice" consumers
    # already have; it is no longer a report-only consumer whose single
    # assertion doubles as its own mutation guard.
    EXPECTED_ASSERTION_COUNT = {
        ".claude/commands/apply-implementation-review.md": 2,
        ".claude/commands/apply-plan-review.md": 2,
        ".claude/commands/approve-review.md": 2,
        ".claude/commands/record-manual-plan-review.md": 2,
        ".claude/commands/review-plan.md": 2,
        ".claude/commands/review-implementation.md": 2,
        ".claude/commands/apply-functional-review.md": 1,
        ".claude/commands/milestone-implement.md": 1,
        ".claude/commands/milestone-plan.md": 1,
        ".claude/commands/prepare-review.md": 1,
        ".claude/commands/review-functional.md": 1,
    }

    def test_all_roster_command_files_declare_the_expected_value(self):
        repo_root = _repo_root()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        declarations = ws.discover_review_subject_declarations(repo_root, head)
        self.assertEqual(declarations, self.EXPECTED)

    def test_recover_implementation_provenance_has_no_declaration(self):
        repo_root = _repo_root()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        declarations = ws.discover_review_subject_declarations(repo_root, head)
        self.assertNotIn(".claude/commands/recover-implementation-provenance.md", declarations)

    def test_every_non_exempt_file_calls_the_shared_assertion_the_expected_number_of_times(self):
        repo_root = _repo_root()
        for rel_path, expected_count in self.EXPECTED_ASSERTION_COUNT.items():
            text = (repo_root / rel_path).read_text()
            actual = text.count("assert_bundle_not_rejected")
            self.assertEqual(
                actual, expected_count,
                f"{rel_path}: expected {expected_count} call(s) to "
                f"assert_bundle_not_rejected, found {actual}",
            )

    def test_every_exempt_file_never_calls_the_assertion(self):
        repo_root = _repo_root()
        exempt = [p for p, v in self.EXPECTED.items() if v == "none"]
        # Three, not four, since ledger `I10` deleted the exempt
        # `.claude/commands/accept-scoped-remediation.md` with its command.
        self.assertEqual(len(exempt), 3)
        for rel_path in exempt:
            text = (repo_root / rel_path).read_text()
            self.assertNotIn("assert_bundle_not_rejected", text)


if __name__ == "__main__":
    unittest.main()
