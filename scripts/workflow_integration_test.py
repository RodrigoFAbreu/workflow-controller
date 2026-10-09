#!/usr/bin/env python3
"""Cross-checkpoint integration fixtures, dual-mode command-text
conformance, and documentation-consistency lints for `WF8a-ii` ("WF8a-i,
part 2"), consuming `workflow_test_harness.py`'s documented fixture
format throughout rather than re-deriving `workflow_state.py`/
`workflow_fingerprint.py` internals.

This module covers exactly the checkpoint's own named scope:

- missing-test item 17: a simulated plan->implement->approve pass creates
  exactly the three authorized commit-trailer kinds and no others;
- missing-test item 58: `select_next_checkpoint`'s determinism holds at
  every point along a full checkpoint walk, not only at one fixed state
  (the single-state case is already covered by `workflow_state_test.py`'s
  own `test_determinism_across_independent_calls`, missing-test item 28 --
  not duplicated here);
- missing-test items 59/60/77: the bootstrap command's own static-text
  conformance (it is a prompt file, not executable code -- see each test
  class's docstring for why a text-conformance technique is the honest
  substitute for a behavioral test here);
- the dual-mode command-text enumeration (`D-Self-Governance`): every
  command this milestone modifies that carries an explicit
  `governing_workflow_version`-keyed two-branch structure actually states
  it in its `.md` file, and the two `"2.1"`-only commands
  (`/review-plan`, `/record-manual-plan-review`) state their own clean
  refusal for a `"1"` item;
- missing-test item 90: golden-hash drift detection for the modified
  commands' v1-governed behavior (`WFR-26`), including a real pre-`v2.1`
  base-commit comparison for `/milestone-plan` and `/apply-plan-review`
  where the base commit is honestly comparable (see
  `TestGoldenV1BehaviorAgainstPreV21BaseCommit`'s docstring for the exact
  scope and its limits);
- missing-test item 107: a bundle-completeness lint for language
  instructing a protected-path correction after approval;
- missing-test item 108: a documentation-consistency lint proving items
  85/96 and the D-Plan-Review-Stages transition table state the same
  `/review-plan` write set;
- `GPT-R11-009`: the two-stage plan-review ledger exercised end to end
  against a real `ScratchRepo`, including a real committed plan-doc edit
  invalidating both stages (the integration-level half of `WFR-38` --
  `workflow_state_test.py` already covers the dict-level transition logic
  for both of these; not duplicated here, only chained against real Git
  history and a real recomputed `review_content_id`).

Stdlib-only. Run: python3 scripts/workflow_integration_test.py
"""

from __future__ import annotations

import ast
import base64
import hashlib
import html
import inspect
import itertools
import inspect
import json
import os
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import workflow_fingerprint as fingerprint
import workflow_state as ws
import workflow_test_harness as h


def _repo_root() -> Path:
    return Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    )


def _run(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def _command_text(filename: str) -> str:
    return (_repo_root() / ".claude" / "commands" / filename).read_text()


def _commit_trailer_keys(repo_root: Path, commit: str) -> set[str]:
    body = _run(["git", "log", "-1", "--format=%B", commit], cwd=repo_root)
    parsed = subprocess.run(
        ["git", "interpret-trailers", "--parse"], cwd=repo_root,
        input=body, capture_output=True, text=True, check=True,
    ).stdout
    keys = set()
    for line in parsed.splitlines():
        if ":" in line:
            keys.add(line.split(":", 1)[0].strip())
    return keys


# ---------------------------------------------------------------------------
# Missing-test item 17: a full plan -> implement -> approve pass creates
# exactly the three authorized commit-trailer kinds and no others.
# ---------------------------------------------------------------------------


class TestFullPassAuthorizedCommitKinds(unittest.TestCase):
    """Missing-test item 17: simulates a minimal plan->implement->approve
    pass end to end against a real `ScratchRepo`, using the real
    production functions (`transition_checkpoint_in_progress`/
    `complete_checkpoint`/`apply_plan_approval`/`apply_technical_approval`)
    for the state half and `repo.commit_files` (this checkpoint's own
    small extension to the shared harness) for the Git half, so each
    simulated commit genuinely carries both a trailer and the same
    commit's `WORKFLOW_STATE.json` update side by side, exactly as
    `D3`/`OPUS-R6-005` describes. Asserts the resulting commit
    trailer-*key* set -- discovered via the real
    `discover_checkpoint_commits`/`discover_approval_commits` search, not
    merely inspected by hand -- is exactly the three D-Commit-Provenance/
    D-Approval-Commits kinds and nothing else.

    `Workflow-Work-Item` is deliberately excluded from the counted key
    set: it is the universal scoping companion trailer every one of these
    commits also carries (required by `_discover_trailer_commits` to
    scope the search to this one work item), not itself a distinct
    "commit kind" the way `Workflow-Checkpoint`/`Workflow-Plan-Approval`/
    `Workflow-Technical-Approval` are -- see D-Commit-Provenance's own
    trailer-pair language. Excluding it here is a deliberate, documented
    scope choice, not an oversight.
    """

    def test_plan_implement_approve_pass_uses_exactly_three_trailer_kinds(self):
        with h.ScratchRepo() as repo:
            registry = h.base_registry(checkpoints=[
                {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
                {"id": "B", "name": "b", "depends_on": ["A"], "complexity": 1, "session_target": "1"},
            ])
            state = h.base_state(wi=h.base_work_item(
                work_item_id="wi", phase="AWAITING_PLAN_APPROVAL", checkpoints={},
            ))
            state_path = "docs/ai-workflow/WORKFLOW_STATE.json"

            # 1. Plan-approval commit.
            plan_record = ws.build_approval_record(
                basis="USER_OVERRIDE", stage="plan", user_confirmation="wi plan",
                reviewed_bundle_id="bundle-plan-1", approved_review_content_id="plan-content-1",
                review_content_manifest=[], now="t0",
            )
            state = ws.apply_plan_approval(state, "wi", plan_record, now="t0")
            plan_commit = repo.commit_files(
                "plan approved", {state_path: json.dumps(state)},
                trailers={"Workflow-Plan-Approval": "plan-content-1", "Workflow-Work-Item": "wi"},
            )

            # 2. Checkpoint A.
            state = ws.transition_checkpoint_in_progress(state, "wi", "A", plan_commit, now="t1")
            state = ws.complete_checkpoint(state, "wi", "A", registry, now="t2", repo_root=repo.root)
            ck_a_commit = repo.commit_files(
                "checkpoint A", {state_path: json.dumps(state), "src/a.txt": "a\n"},
                trailers={"Workflow-Checkpoint": "A", "Workflow-Work-Item": "wi"},
            )

            # 3. Checkpoint B -- the last one, so this also flips phase to
            # SELF_REVIEWING_IMPLEMENTATION as part of the same write.
            state = ws.transition_checkpoint_in_progress(state, "wi", "B", ck_a_commit, now="t3")
            state = ws.complete_checkpoint(state, "wi", "B", registry, now="t4", repo_root=repo.root)
            ck_b_commit = repo.commit_files(
                "checkpoint B", {state_path: json.dumps(state), "src/b.txt": "b\n"},
                trailers={"Workflow-Checkpoint": "B", "Workflow-Work-Item": "wi"},
            )
            self.assertEqual(state["work_items"]["wi"]["phase"], "SELF_REVIEWING_IMPLEMENTATION")

            # 4. Technical-approval commit.
            impl_record = ws.build_approval_record(
                basis="USER_OVERRIDE", stage="implementation", user_confirmation="wi implementation",
                reviewed_bundle_id="bundle-impl-1", approved_review_content_id="impl-content-1",
                review_content_manifest=[], reviewed_content_commit=ck_b_commit, now="t5",
            )
            state = ws.apply_technical_approval(state, "wi", impl_record, now="t5")
            tech_commit = repo.commit_files(
                "technical approved", {state_path: json.dumps(state)},
                trailers={"Workflow-Technical-Approval": "impl-content-1", "Workflow-Work-Item": "wi"},
            )

            # Sanity: the resulting state is itself schema-valid and
            # dependency-consistent against the registry it was driven by.
            ws.validate_state(state, registry=registry)

            # Discover every commit kind via the real production search --
            # never inspected by hand -- proving the simulated pass is
            # actually reachable the way a real session's would be.
            self.assertEqual(
                ws.discover_checkpoint_commits(repo.root, "wi", repo.base),
                {"A": ck_a_commit, "B": ck_b_commit},
            )
            self.assertEqual(
                ws.discover_approval_commits(repo.root, "Workflow-Plan-Approval", "wi", repo.base),
                {"plan-content-1": plan_commit},
            )
            self.assertEqual(
                ws.discover_approval_commits(repo.root, "Workflow-Technical-Approval", "wi", repo.base),
                {"impl-content-1": tech_commit},
            )

            commits = [line for line in _run(
                ["git", "log", "--format=%H", f"{repo.base}..{repo.head()}"], cwd=repo.root,
            ).splitlines() if line]
            self.assertEqual(len(commits), 4, "expected exactly one commit per lifecycle step")

            all_keys: set[str] = set()
            for commit in commits:
                all_keys |= _commit_trailer_keys(repo.root, commit)
            all_keys.discard("Workflow-Work-Item")
            self.assertEqual(
                all_keys,
                {"Workflow-Checkpoint", "Workflow-Plan-Approval", "Workflow-Technical-Approval"},
            )


# ---------------------------------------------------------------------------
# Missing-test item 58: select_next_checkpoint's determinism holds at every
# point along a full checkpoint walk (item 28 already covers one fixed
# state -- workflow_state_test.py's own test_determinism_across_independent_calls).
# ---------------------------------------------------------------------------


class TestTemplateDefaultCreatesTwoPointTwoItem(unittest.TestCase):
    def test_item_created_under_the_2_2_template_is_2_2_and_default_config_is_unchanged(self):
        template = {"schema_version": 1, "default_workflow_version": "2.2",
                    "supported_versions": ["1", "2.1", "2.2"]}
        ws.validate_config(template)
        state = ws.route_work_item(
            {"schema_version": 1, "active_work_item_id": None, "work_items": {}},
            template, work_item_id="wi", work_item_type="process", work_item_kind="process",
            plan_path="p", registry_path="r", plan_revision=1, now="t",
        )
        self.assertEqual(state["work_items"]["wi"]["governing_workflow_version"], "2.2")
        self.assertEqual(ws.default_config()["default_workflow_version"], "1")


class TestSelectNextCheckpointDeterminismAlongFullWalk(unittest.TestCase):
    """Missing-test item 58: "at every point in the registry order, exactly
    one command can select and implement the next incomplete checkpoint."

    `workflow_state_test.py`'s `test_determinism_across_independent_calls`
    (missing-test item 28, confirmed present via
    `grep -n "missing-test item 28" scripts/workflow_state_test.py` before
    writing this) already proves `select_next_checkpoint` is pure and
    repeatable at one fixed, hand-picked state. This sweeps every state
    along a real four-checkpoint dependency graph, from empty to fully
    complete, re-asserting agreement between two independently
    constructed state copies at each step -- so a regression that only
    breaks determinism partway through a real sequence (e.g. at a
    fan-in/fan-out point) would still be caught, not just at the one
    state item 28 happens to use."""

    def test_agreement_holds_at_every_point_along_a_diamond_shaped_walk(self):
        registry = h.base_registry(checkpoints=[
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
            {"id": "B", "name": "b", "depends_on": ["A"], "complexity": 1, "session_target": "1"},
            {"id": "C", "name": "c", "depends_on": ["A"], "complexity": 1, "session_target": "1"},
            {"id": "D", "name": "d", "depends_on": ["B", "C"], "complexity": 1, "session_target": "1"},
        ])
        completed: dict[str, dict] = {}
        order: list[str] = []
        for _ in range(len(registry["checkpoints"]) + 1):
            wi_a = h.base_work_item(current_checkpoint_id=None, checkpoints=dict(completed))
            wi_b = h.base_work_item(current_checkpoint_id=None, checkpoints=dict(completed))
            result_a = ws.select_next_checkpoint(wi_a, registry)
            result_b = ws.select_next_checkpoint(wi_b, registry)
            self.assertEqual(result_a, result_b, f"disagreement after completing {order}")
            if result_a is None:
                break
            completed[result_a] = {"status": "COMPLETE"}
            order.append(result_a)
        self.assertEqual(order, ["A", "B", "C", "D"])


# ---------------------------------------------------------------------------
# Missing-test items 59/60/77: the bootstrap command's own static-text
# conformance. bootstrap-workflow-v2.md is a *prompt* file, not executable
# code, so "verified by a conformance test" here means parsing its real
# text and asserting the stated invariants are actually present -- the
# same technique workflow_state_demo_test.py's own item-72 test already
# uses for the checkpoint-registry Markdown table (confirmed via
# `grep -n "item 72" -A 40 scripts/workflow_state_demo_test.py` before
# writing this), matched here but placed in the hermetic suite since
# parsing .claude/commands/*.md content is deterministic/stdlib-only and
# not tied to a moving historical base commit.
# ---------------------------------------------------------------------------


class TestBootstrapCommandStaticConformance(unittest.TestCase):
    def setUp(self):
        self.text = _command_text("bootstrap-workflow-v2.md")

    def test_item_59_retired_only_at_this_work_items_own_milestone_complete(self):
        self.assertIn(
            "It is retired — deleted — only at this work item's own\n`MILESTONE_COMPLETE`.",
            self.text,
        )

    def test_item_60_milestone_implement_never_invoked_for_this_work_item(self):
        self.assertIn(
            "`/milestone-implement` is never\ninvoked for this work item at any point.",
            self.text,
        )

    def test_item_60_never_reads_active_milestone_or_roadmap(self):
        self.assertIn("It never reads\n`docs/ACTIVE_MILESTONE.md` or `docs/ROADMAP.md`;", self.text)

    def test_item_60_work_item_id_is_hardcoded_never_an_argument(self):
        self.assertIn("Work item: `workflow-v2-1-core` (hardcoded, never an argument).", self.text)

    def test_item_77_stops_after_exactly_one_checkpoint(self):
        self.assertIn(
            "**Stop immediately** — never loop, never continue to the next\n   checkpoint in the same invocation",
            self.text,
        )

    def test_item_77_own_conformance_coverage_is_acknowledged_in_the_file_itself(self):
        """The command file's own closing paragraph names this exact
        checkpoint (WF8a-ii) as owning its conformance test -- a
        cross-check that the invariant this test class asserts is the one
        the file itself expects to be tested."""
        self.assertIn(
            "It is not exempt from\nconformance coverage (`WF8a-ii` owns a dedicated test asserting it stops\n"
            "after exactly one checkpoint and never reads\ndocs/ACTIVE_MILESTONE.md",
            self.text.replace("`docs/ACTIVE_MILESTONE.md`", "docs/ACTIVE_MILESTONE.md"),
        )

    def test_exempt_from_dual_mode_branching_by_its_own_stated_design(self):
        """Confirms the exemption is *stated*, not merely assumed by this
        suite -- this command is deliberately not covered by
        TestDualModeBranchConformance below."""
        self.assertIn(
            "This command is exempt from `D-Self-Governance`'s dual-mode branching\nrequirement",
            self.text,
        )
        self.assertNotIn('governing_workflow_version: "2.1"', self.text)


# ---------------------------------------------------------------------------
# D-Self-Governance's dual-mode command enumeration: every command this
# milestone modifies that carries an explicit governing_workflow_version-
# keyed two-branch structure actually states it in its .md file.
# ---------------------------------------------------------------------------

# Commands whose dual-mode structure is the literal bold-marker pattern
# `**`governing_workflow_version: "1"`**` / `**`governing_workflow_version:
# "2.1"`**`, each branch followed by materially different step text,
# confirmed present by directly reading each file before writing this list.
_BOLD_MARKER_DUAL_MODE_COMMANDS = (
    "milestone-plan.md",
    "milestone-implement.md",
    "approve-review.md",
    "apply-plan-review.md",
)

# Commands whose "0. **Dual-mode branch**" step states both versions run
# every numbered step *identically*, differing only in the exit-target
# naming named elsewhere in the same step -- a real, but textually
# different, two-branch expression than the bold-marker commands above
# (confirmed by directly reading each file: neither states the fuller
# `governing_workflow_version: "1"` phrase, only the shorthand `"1"`/
# `"2.1"` quoted-version-string pair).
_UNIFIED_DUAL_MODE_COMMANDS = (
    "accept-milestone.md",
    "apply-implementation-review.md",
)


class TestDualModeBranchConformance(unittest.TestCase):
    def test_each_bold_marker_command_states_both_governing_version_branches(self):
        for filename in _BOLD_MARKER_DUAL_MODE_COMMANDS:
            with self.subTest(filename=filename):
                text = _command_text(filename)
                self.assertIn('governing_workflow_version: "1"', text, filename)
                self.assertIn('governing_workflow_version: "2.1"', text, filename)
                self.assertIn("**Dual-mode branch**", text, filename)

    def test_each_unified_command_states_a_dual_mode_step_naming_both_versions(self):
        for filename in _UNIFIED_DUAL_MODE_COMMANDS:
            with self.subTest(filename=filename):
                text = _command_text(filename)
                self.assertIn("**Dual-mode branch**", text, filename)
                self.assertIn('`"1"`', text, filename)
                self.assertIn('`"2.1"`', text, filename)
                self.assertIn("governing_workflow_version", text, filename)

    def test_prepare_functional_review_expresses_dual_mode_via_legacy_ready_adoption(self):
        """`/prepare-functional-review` is named in D-Self-Governance's
        enumeration ("WF-M8b's selector-argument and adoption extension,
        and WF4c"), but -- confirmed by reading the file directly rather
        than assuming the same bold-marker pattern applies uniformly --
        it does not branch its main steps on a literal
        `governing_workflow_version: "1"` / `"2.1"` pair the way the
        commands above do. Its own two-branch structure is phase-keyed
        (`0a`: is the resolved target `LEGACY_READY`, or not) and its
        *effect*, on the adoption path, is exactly what changes
        `governing_workflow_version` from `"1"` to `"2.1"` for that work
        item -- a materially different but still real two-branch
        structure, documented here rather than forced into the other
        commands' shape."""
        text = _command_text("prepare-functional-review.md")
        self.assertIn("`LEGACY_READY` adoption scan, before any version branching", text)
        self.assertIn(
            "If the resolved target's `phase` is\n    **not** `LEGACY_READY`, skip straight to step 1 with that target.",
            text,
        )
        self.assertIn('`governing_workflow_version` has transitioned\n       `"1"` → `"2.1"`', text)

    def test_bundle_scripts_are_deliberately_version_agnostic(self):
        """The bundle scripts (WF5) are part of D-Self-Governance's full
        command roster, but `scripts/prepare-ai-review.sh` itself contains
        no `governing_workflow_version` awareness at all -- confirmed by a
        direct grep before writing this test. This is by design, not a
        gap: bundle generation mechanics are identical for either
        governing version (only the *gate-reachability* check that reads
        a generated bundle differs by version, and that branch already
        lives in, and is already covered by,
        `approve-review.md`'s own step-0 dual-mode text -- the
        "approve-review fingerprint wiring" half of the roster). This test
        asserts the negative fact directly rather than silently assuming
        it."""
        script_text = (_repo_root() / "scripts" / "prepare-ai-review.sh").read_text()
        self.assertNotIn("governing_workflow_version", script_text)
        approve_review_text = _command_text("approve-review.md")
        self.assertIn("plan_approval_gate_reachable", approve_review_text)
        self.assertIn('governing_workflow_version: "2.1"', approve_review_text)


class TestReviewImplementationCommandStaticConformance(unittest.TestCase):
    """`workflow-v2-3` CP1's own conformance coverage for the new
    `/review-implementation` command, mirroring
    `TestBootstrapCommandStaticConformance`'s/
    `TestVersion21OnlyCommandsRefuseCleanlyForV1`'s pattern of asserting
    key invariant sentences are actually present in the file's real text,
    rather than merely described in this plan."""

    def setUp(self):
        self.text = _command_text("review-implementation.md")

    def test_frontmatter_has_description_and_argument_hint(self):
        self.assertIn("description:", self.text)
        self.assertIn("argument-hint:", self.text)
        # workflow-2.5.0: reclassified from `false` to `true` -- this
        # command is now the authoritative LOCAL_MODEL_IMPLEMENTATION_REVIEW
        # stage writer for a "2.2" item at
        # AWAITING_LOCAL_IMPLEMENTATION_REVIEW, so it belongs in
        # `discover_state_writers`' writer census. The declaration
        # vocabulary is the closed set `true`/`false`/`"publisher"` (item
        # 357) -- a "conditional" value is unparseable and fails closed, so
        # the branch-dependence is documented in `description:` and the
        # command body instead, never in the machine-read value.
        self.assertIn("state_writer: true", self.text)
        self.assertIn("review-subject: bundle", self.text)

    def test_states_model_independence(self):
        self.assertIn(
            "Implements a model-independent\n**review role**, not a specific model: "
            "nothing in this contract, in the\nreport it produces, or in any check it "
            "performs names a model — running it\nfrom any capable Claude model produces "
            "the same behavior.",
            self.text,
        )

    def test_states_the_report_only_constraint(self):
        """Narrowed, `workflow-v2-3-followups` `CP2` (REQ-5): the command
        no longer claims to write nothing -- it states the new, narrower
        invariant instead (writes `REVIEW_FEEDBACK.md`; still never writes
        `WORKFLOW_STATE.json`, never approves, never advances `phase`)."""
        self.assertIn(
            "**Writes `<feedback_dir>/REVIEW_FEEDBACK.md`; nothing else.** This command\n"
            "writes the current `<feedback_dir>/REVIEW_FEEDBACK.md` (step 7, once every\n"
            "guard there passes) but never writes\n"
            "`docs/ai-workflow/WORKFLOW_STATE.json`, never edits source/test/plan/\n"
            "registry/mapping/bundle content, never approves a stage, and never\n"
            "advances `phase`.",
            self.text,
        )

    def test_phase_guard_names_the_exact_required_phase(self):
        self.assertIn(
            "2. **Phase guard**: if the resolved item's `phase` is not exactly\n"
            "   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, refuse cleanly, naming the\n"
            "   actual phase.",
            self.text,
        )

    def test_states_it_writes_nothing(self):
        """Narrowed, `workflow-v2-3-followups` `CP2` (REQ-5): the command
        no longer states "Report only -- writes nothing." anywhere -- it
        now writes `<feedback_dir>/REVIEW_FEEDBACK.md` once its own
        pre-write guards pass (step 7), and step 8's own closing text says
        so."""
        self.assertNotIn("Report only", self.text)
        self.assertNotIn("writes nothing", self.text)
        self.assertIn(
            "8. **Report and stop.** On a successful write, state plainly that\n"
            "   `<feedback_dir>/REVIEW_FEEDBACK.md` was written and is now the\n"
            "   authoritative round for `/apply-implementation-review`/`/approve-review\n"
            "   implementation` to act on",
            self.text,
        )

    def test_states_plan_conformance_read(self):
        """`workflow-v2-3-followups` `CP2` (REQ-6, item 4's deferred
        follow-up): step 3's read list names `PLAN.md`/`plan_path`, and
        step 5 gains a plan-conformance search arm mirroring
        `/review-plan` step 6 -- a further method on this class, not a
        new fixture, per `LPR-R2-O02`'s correction of the original
        (inapplicable) precedent citation."""
        self.assertIn(
            "3. **Read**: `<bundle_dir>/PLAN.md` and the item's own `plan_path`",
            self.text,
        )
        self.assertIn(
            "**Also independently verify the implementation against what the\n"
            "   approved plan (`PLAN.md`/`plan_path`, read in step 3) actually\n"
            "   specified**",
            self.text,
        )

    def test_step_six_and_seven_no_longer_describe_manual_installation(self):
        """`LPR-R3-I02`/`LPR-R4-I02`: the provenance text this command's
        own step 6 and (what was) step 7 carried -- describing the
        operator hand-copying this report into `REVIEW_FEEDBACK.md` as an
        optional installation step -- no longer appears anywhere,
        including the frontmatter `description:` line, which no longer
        reads "Report-only" (`LPR-R4-I02`'s own `assertNotIn`, added to
        this same method rather than a new fixture)."""
        self.assertNotIn("hand-copy this report", self.text)
        self.assertNotIn(
            "if the user chooses to hand-copy this report into", self.text,
        )
        self.assertNotIn("Report-only", self.text)
        self.assertIn(
            "since satisfying\n"
            "   `workflow_fingerprint.parse_review_feedback_binding_fields`/\n"
            "   `assert_feedback_matches_bundle` is a hard precondition of this\n"
            "   command's own write in step 7 below, not a convenience for a\n"
            "   hypothetical hand-copy.",
            self.text,
        )

    def test_write_step_names_the_ownership_guard(self):
        """`GPT-FUP-R6-I01`, revised `LPR-R7-B01`: a further method
        proving the command's own prose actually instructs the fix, not
        only that `workflow_fingerprint.assert_feedback_not_owned_by_other_work_item`
        exists and behaves correctly in isolation -- named, and stated to
        run immediately before the write against the unmodified
        `resolve_feedback_dir(repo_root, work_item_id)` path."""
        self.assertIn("assert_feedback_not_owned_by_other_work_item", self.text)
        self.assertIn(
            "ownership guard runs immediately before the write, against this same\n"
            "     unmodified `resolve_feedback_dir(repo_root, work_item_id)` path.",
            self.text,
        )

    def test_refusal_path_states_report_printing_per_guard_and_recovery(self):
        """`LPR-R10-B01`/`LPR-R11-I01`/`LPR-R11-I02`/`LPR-R12-I01` (round-10
        through round-12 local plan review): the refusal-path prose is
        stated per guard, not as one "either guard" sentence, and the
        recovery text is keyed on a live A reaching the terminal phase
        `MILESTONE_COMPLETE`, not on a fixed two-phase consumption list --
        including that hand-creating a scoped feedback directory is not an
        endorsed remedy."""
        self.assertIn(
            "**On a `BundleRejectedError` here, suppress the\n"
            "     composed report entirely**",
            self.text,
        )
        self.assertIn(
            "**On a `FeedbackOwnedByOtherWorkItemError` here, still print the\n"
            "     composed report in full**",
            self.text,
        )
        self.assertIn(
            "hand-creating a scoped\n"
            "     `.ai-review/<work_item_id>/feedback/` directory is",
            self.text,
        )
        self.assertIn("**not** an endorsed", self.text)
        self.assertIn(
            "Only once a live A's `phase`\n"
            "     independently reaches `MILESTONE_COMPLETE` may the operator delete the",
            self.text,
        )

    def test_names_artifacts_path_for_work_item(self):
        """I3/revision 2's own missing-test gap: a regression back to
        `load_implementation_stage_classification`'s default argument
        (silently resolving `workflow-v2-1-core`'s artifacts file instead
        of the resolved work item's own) fails this cheap textual
        check."""
        self.assertIn("artifacts_path_for_work_item", self.text)

    def test_names_missing_required_bundle_file_error(self):
        """Revision 6/7 I2/O1's own missing-test gap: a regression that
        silently drops the absent-manifest clean-refusal wording fails
        this check."""
        self.assertIn("MissingRequiredBundleFileError", self.text)

    def test_the_2_2_authoritative_branchs_reviewer_role_and_ledger_key_match_the_code_constant(self):
        """workflow-2.5.0 REVISE round 2, Missing-tests item 3: nothing
        previously bound this command file's prose to the constant its own
        `A6` write set actually uses -- the exact divergence class the
        self-review found once already (`LOCAL_IMPLEMENTATION_REVIEW` in
        code vs `LOCAL_MODEL_IMPLEMENTATION_REVIEW` in every document) would
        still be invisible to `workflow_integration_test.py` today without
        this. `workflow_acceptance_matrix_test.py`'s real end-to-end suite
        calls `workflow_state` functions directly, never executing this
        Markdown contract, so a hand-edited `Reviewer role:` line here (or
        in `A6`'s own ledger-key reference) would pass everything else in
        this file silently."""
        self.assertIn(
            f"`Reviewer role: {ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW}`", self.text,
        )
        self.assertIn(
            'workflow_state.record_local_implementation_review(state,\n'
            '      work_item_id, verdict="APPROVE"',
            self.text,
        )
        # The A6 ledger-key/Reviewer-role identity claim itself, restated in
        # the file's own prose -- proven equal to the real constant, not
        # merely equal to itself.
        self.assertEqual(ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW, "LOCAL_MODEL_IMPLEMENTATION_REVIEW")
        self.assertIn(
            f"`{ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW}` ledger fields (the `Reviewer role:`\n"
            "      string above and the ledger's own canonical key are deliberately the\n"
            "      same one name",
            self.text,
        )


class TestReviewFunctionalCommandStaticConformance(unittest.TestCase):
    """`workflow-v2-3` CP2's own conformance coverage for the new
    `/review-functional` command, mirroring
    `TestReviewImplementationCommandStaticConformance`'s pattern of
    asserting key invariant sentences are actually present in the file's
    real text, rather than merely described in this plan. Also pins
    revision 10's own round-9 I1/I2 fixes (`GPT-R9-001`/`GPT-R9-002`) as
    two textual-presence checks, per the plan's "Missing tests?" bullet."""

    def setUp(self):
        self.text = _command_text("review-functional.md")

    def test_frontmatter_has_description_and_argument_hint(self):
        self.assertIn("description:", self.text)
        self.assertIn("argument-hint:", self.text)
        self.assertIn("state_writer: false", self.text)
        self.assertIn("review-subject: bundle", self.text)

    def test_states_model_independence(self):
        self.assertIn(
            "Implements a model-independent\n**review role**, not a specific model: "
            "nothing in this contract, in the\nreport it produces, or in any check it "
            "performs names a model — running it\nfrom any capable Claude model produces "
            "the same behavior.",
            self.text,
        )

    def test_states_the_report_only_constraint(self):
        self.assertIn(
            "**Review and report only.** This command never writes\n"
            "`docs/ACTIVE_MILESTONE.md`, `<feedback_dir>/FUNCTIONAL_REVIEW.md`, or\n"
            "`docs/ai-workflow/WORKFLOW_STATE.json`, never fixes findings, and never\n"
            "advances `phase`.",
            self.text,
        )

    def test_phase_guard_names_the_exact_required_phase(self):
        self.assertIn(
            "2. **Phase guard**: if the resolved item's `phase` is not exactly\n"
            "   `AWAITING_FUNCTIONAL_REVIEW`, refuse cleanly, naming the actual phase.",
            self.text,
        )

    def test_states_it_writes_nothing(self):
        self.assertIn("**Report only — writes nothing.**", self.text)

    def test_single_coherent_untracked_item_policy(self):
        """Revision 10, round 9 I1 fix (`GPT-R9-001`): the file must state
        step 1's single clean refusal for an untracked work item and must
        not also tell the reviewer to read the checklist directly for that
        same case -- the two-branch contradiction the round found in
        revision 9's own text. Mirrors `/review-implementation` step 1
        exactly, per the round's own decision."""
        self.assertIn(
            "Refuse cleanly, naming the\n   problem, if neither resolves to an existing "
            "`work_items` entry",
            self.text,
        )
        self.assertNotIn("read the checklist", self.text)

    def test_functional_acceptance_wording_is_work_item_neutral(self):
        """Revision 10, round 9 I2 fix (`GPT-R9-002`): the file's
        functional-acceptance wording must name the checklist's own
        required flows, work-item-neutrally, rather than assuming every
        work item's functional review is an Android-app walkthrough."""
        self.assertIn("checklist's required functional flows", self.text)
        self.assertNotIn("the Android app", self.text)


class TestVersion21OnlyCommandsRefuseCleanlyForV1(unittest.TestCase):
    """`/review-plan` and `/record-manual-plan-review` are `"2.1"`-only,
    with no v1 counterpart at all -- D-Self-Governance's enumeration says
    each "refuses cleanly" for a `"1"`-governed item or when no `"2.1"`
    item is resolvable, rather than branching. Confirms each command's
    `.md` file actually states this refusal, naming the reason, rather
    than merely relying on `workflow_state.py`'s own exception (already
    covered at the unit level by `workflow_state_test.py`'s
    `TestRecordLocalPlanReview`/`TestRecordManualPlanReview` classes)."""

    #: workflow-2.5.0: widened from a bare `not "2.1"` check to
    #: `TWO_STAGE_PLAN_REVIEW_VERSIONS` membership (D-Implementation-
    #: Review-Version-Activation's inheritance rule) -- both review-plan.md
    #: and record-manual-plan-review.md share this exact wording up to
    #: "naming the actual version", after which each file's own reason
    #: text diverges.
    _V1_REFUSAL_CONDITION_TEXT = (
        "if the resolved item's\n   `governing_workflow_version` is not a "
        "member of\n   `workflow_state.TWO_STAGE_PLAN_REVIEW_VERSIONS` "
        '(`"2.1"`/`"2.2"`,\n   widened workflow-2.5.0 from a bare `"2.1"` '
        "check), refuse cleanly,\n   naming the actual version"
    )

    def test_review_plan_states_its_own_v1_refusal_condition_and_reason(self):
        text = _command_text("review-plan.md")
        self.assertIn("**Governing-version guard**", text)
        self.assertIn(self._V1_REFUSAL_CONDITION_TEXT, text)
        self.assertIn("no local-review stage to run", text)
        self.assertIn("WrongGoverningVersionForPlanReviewStageError", text)

    def test_record_manual_plan_review_states_its_own_v1_refusal_condition_and_reason(self):
        text = _command_text("record-manual-plan-review.md")
        self.assertIn("**Governing-version guard**", text)
        self.assertIn(self._V1_REFUSAL_CONDITION_TEXT, text)
        self.assertIn("WrongGoverningVersionForPlanReviewStageError", text)

    def test_both_commands_resolve_work_item_id_the_same_way_before_the_guard_runs(self):
        """Both files resolve a target work item (named argument, or
        active_work_item_id) as their own step 1, before the version
        guard in step 2 -- so "when no '2.1' item is resolvable" is the
        same governing-version guard firing on a resolved-but-wrong-
        version (or, transitively, a nonexistent) item, not a second,
        separately documented refusal path. Documented here as the
        reading this suite uses, rather than assumed silently."""
        for filename in ("review-plan.md", "record-manual-plan-review.md"):
            text = _command_text(filename)
            self.assertIn("**Resolve the work item**", text, filename)
            self.assertIn("active_work_item_id", text, filename)


# ---------------------------------------------------------------------------
# Missing-test item 90 / WFR-26: golden-output drift detection for the
# modified commands' v1-governed behavior.
# ---------------------------------------------------------------------------

# A broad, self-referential drift detector (WFR-26: "golden-output test
# per modified command") over every command file this milestone's dual-
# mode enumeration names, plus the bootstrap command and the bundle
# script. This proves "unchanged since this test was written," never
# "correct" or "behaviorally equivalent to pre-v2.1 prose" -- that
# stronger claim is reserved for TestGoldenV1BehaviorAgainstPreV21BaseCommit
# below, for the two files where a real pre-v2.1 copy is actually
# available to diff against.
_GOLDEN_COMMAND_FILE_SHA256 = {
    # workflow-2.8.0 (gate-policy-and-reopening, CP2): `approve-review.md`
    # gains the pointer to `/satisfy-gate`; `milestone-plan.md`,
    # `apply-plan-review.md`, `milestone-implement.md` and
    # `apply-implementation-review.md` gain the sentence asking the reviewer for
    # `Reviewer model:` when the gate requires distinct models;
    # `review-plan.md`, `review-implementation.md` and
    # `record-manual-plan-review.md` gain the `Reviewer model:` line and the audit
    # keys; `apply-functional-review.md` names `/satisfy-gate` in the child
    # sequence -- intentional content changes, not regressions.
    # Updated by WFO-STATE-SERIALIZATION (item 354/357): every writer
    # command file below gained a `state_writer: true` frontmatter
    # declaration and a "State-writer discipline" paragraph naming
    # `workflow_state.state_transaction`/`state_lock` -- an intentional
    # content change, not a regression.
    #
    # The ten entries below that predate WF8c item (h) were further
    # updated by that item, part 1 (`WFR-67`): each gained a
    # `review-subject:` frontmatter declaration, and every declared
    # consumer among them gained its own
    # `workflow_fingerprint.assert_bundle_not_rejected(...)` call site(s)
    # at the points `WFR-67`'s own text names -- intentional content
    # change, not a regression. The two entries below that were created
    # after WF8c item (h) (`review-implementation.md`/`review-functional.md`,
    # workflow-v2-3 CP1/CP2) were never "further updated" by it -- they
    # were authored from the start with their own `review-subject:`
    # declarations, so their own comments below say "first recorded
    # hash," not "updated."
    # prepare-functional-review.md updated by the salvage audit's final
    # clean-slate pass (repair `R10`, ledger row `I7`): step 3a gains the
    # unchanged-checklist branch, without which a round that needs no
    # checklist change has no discoverable evidence at all.
    # Updated once more by the clean-slate re-audit's second finding
    # (repair `R3` extension, ledger row `B7`): step 3 now states the one
    # classification judgment the template makes rather than defers, and
    # what a work item whose deliverable is a workflow design document
    # must do about it.
    # Updated again by the salvage audit's clean-slate re-audit (repair
    # `R8`, ledger row `B6`): `milestone-plan.md` step 3 gains the
    # intent-to-add staging step `workflow_state.py` already assumed
    # existed, and `apply-plan-review.md` step 5 points at it.
    # Updated by the Workflow v2.x salvage audit (repair `R3`/`R5`,
    # ledger rows `B4`, `I3`, `I4`, `O4`): `milestone-plan.md` step 3 now
    # passes the required `work_item_type` to
    # `generate_artifacts_declarations` and step 6 names the complete
    # author-written input set the generator hard-requires (dropping the
    # stale "author PLAN.md" instruction WFR-67 superseded, adding
    # TEST_RESULTS.md's two mandatory marker lines);
    # `apply-plan-review.md` steps 3/5, `milestone-implement.md` step 4
    # and `apply-implementation-review.md` step 7 carry the matching
    # corrections. Intentional content change, not a regression.
    # Updated by the Workflow v2.x convergence campaign (ledger row
    # `B9`, finding `A9`): the five files below gained the explicit
    # `[work-item-id]` targeting contract a `<parent-id>-remediation-<n>`
    # child needs -- `milestone-plan.md` an `argument-hint` plus the
    # step-0 explicit-target branch and the two-argument resolution
    # rule; `apply-plan-review.md`/`apply-implementation-review.md`/
    # `accept-milestone.md` an `argument-hint` plus step-0 target
    # resolution (`accept-milestone.md` also step 2a's
    # `UnsatisfiedCompletionObligationError` stop and step 2b's
    # remediation-child bookkeeping branch); `apply-functional-review.md`
    # an `argument-hint` plus the corrected broad-branch child sequence.
    # Intentional content change, not a regression.
    # Updated by the workflow system audit's convergence repair `I1`
    # (first-plan-bundle bundle-directory resolution): the five plan-stage
    # entries below now name `resolve_bundle_dir(repo_root, work_item_id,
    # stage="plan")` explicitly, and state why the `stage="plan"` argument
    # is load-bearing rather than decorative -- without it the resolver's
    # compatibility branch answers the flat `.ai-review/current/` for a
    # work item whose scoped directory does not exist yet (a first plan
    # bundle, a first `/milestone-plan <child-id>` on a remediation child,
    # or a regeneration after `withdraw_bundle` quarantined `current/`),
    # while `prepare-ai-review.sh` writes and validates the scoped one.
    # `approve-review.md` runs at both stages, so its own paragraph states
    # the stage-dependent form. Intentional content change, not a
    # regression.
    # Updated by the workflow system audit's convergence pass 12 (ledger
    # row `O31`, external Optional `N3`): the five generation drivers below
    # now point at `docs/ai-workflow/REVIEW_PROTOCOL.md`'s new "Computing
    # `review_content_id`" section for the value each of them already told
    # the author to state. Before this, not one driver named any
    # computation at all -- and the generation *refuses* on a wrong digest
    # rather than warning, so "state the correct value" was an instruction
    # with no procedure. The algorithm is written once, in the protocol;
    # each command carries a reference, never a restatement. Intentional
    # content change, not a regression.
    # milestone-plan.md further updated, D-Feedback-Layout (workflow-2.6.0,
    # CP3): the preamble's <feedback_dir> definition now states scoped-by-
    # construction for a feedback_layout: "scoped" item (legacy rule
    # otherwise) and step 7 prints the exact resolved feedback path --
    # intentional content change.
    # milestone-plan.md further updated, D-Plan-Review-Bundle-Binding
    # (workflow-2.6.0, CP4): the [2.1] plan-review entry (row-1 refusal,
    # ready-phase withdrawal, marker, status), step 3 loses its publish, step
    # 5's publication point, <plan_inputs_dir>, and step 6's bind --
    # intentional content change, not a regression.
    # milestone-plan.md further updated, D-Plan-Approval-Closure
    # (workflow-2.6.0, CP5): step 3's staging note names the approval
    # commit's widened member set (every declared protected path).
    # Implementation review round 2 (I1): step 3's intent-to-add staging is
    # `git --literal-pathspecs add -N` -- intentional.
    # milestone-plan.md further updated, D-Consumed-History (workflow-2.7.0,
    # CP2, v2.6.0-001): the withdrawal report names the durable
    # consumed_plan_review_content_ids history -- intentional content change.
    "milestone-plan.md": "e3f1c979871fe65b1f6d20aedabb2e6afe797b40af19944a314a96ee247e6ed3",
    # milestone-implement.md further updated, OPUS-R129-001: step 1f's
    # checkpoint-completion commit instruction now states explicitly that
    # the Workflow-Checkpoint/Workflow-Work-Item trailer must be the
    # commit message's own final paragraph, after any Co-Authored-By:/
    # Claude-Session: lines, never before them -- intentional content
    # change (the mechanical fix for 4a769fd's own defect class).
    #
    # milestone-implement.md further updated, baseline-tag verification
    # cleanup (OPUS-R129-001): step 4's bundle-generation-record commit
    # instruction now also states the same "trailers must be the commit
    # message's own final paragraph" requirement step 1f already states,
    # closing the gap a fresh Opus review found at this second trailer-
    # write site in an already-corrected file -- intentional content
    # change.
    #
    # milestone-implement.md further updated, D-Repo-Global-Lifecycle
    # (workflow-2.6.0, CP6): step 1d names claim_checkpoint's lifecycle
    # refusals and their remedies (the amendment witness checked under the
    # repository-global lifecycle lock before the local phase check), step
    # 1c routes adopt_claim's to them, and step 1d states that
    # claim_checkpoint returns the claim record whose owner_token field is
    # the token -- intentional content change.
    # milestone-implement.md further updated, workflow-2.7.0
    # (ORCHESTRATION_PROTOCOL_V1_PLAN.md, CP4): step 1a calls
    # implementing_entry_status and reports its cause and remedy --
    # intentional content change.
    # workflow-2.8.0 (gate-policy-and-reopening, CP1): step 4's generation-
    # record commit stages item-scoped (stage_scoped_state), and the
    # checkpoint (1f) and self-review (2) commits call
    # assert_gate_policy_fields_unchanged_or_tightened after landing --
    # intentional content change.
    "milestone-implement.md": "09d20a57b37590fe632b60149f0ce13aa04d6b796a8671909db5dc3dd13bd128",
    # approve-review.md (WF8c item (c), same-content bundle-generation
    # republication idempotency; further updated WF8c item (b): the
    # trailing caveat naming the dedicated /recover-implementation-provenance
    # command as "not yet built" is corrected now that it exists) --
    # intentional content change.
    #
    # approve-review.md further updated, WF8c items 347/348/352: the
    # plan-stage steps 4a-6b replaced with the full failure-atomicity
    # transaction (journal, guard, index-pinned state write, target-scoped
    # materialize, amend recovery), the "interim scope guard" refusing
    # workflow-v2-1-core's own plan-stage approvals until the Bootstrap
    # plan-approval procedure is withdrawn, and new steps 4b/4c/6c/6d --
    # intentional content change.
    #
    # approve-review.md further updated, workflow-v2-3-followups CP1
    # (LPR-R1-B01/LPR-R3-B01/LPR-R3-B02): steps 5 and 6.1 merged into one
    # guarded window (new ordinary step label "step-5-stage-and-pin") that
    # stages every non-state plan-approval member -- ordinary members and
    # the conditional fifth member alike -- in a single call, fixing the
    # fifth-member DirtyIndexBeforeStagingError; step 6a's amend-recovery
    # text reworded to name the merged step; step 6.4's commit instruction
    # gained the "trailers must be the commit message's own final
    # paragraph" sentence -- intentional content change.
    #
    # approve-review.md further updated, workflow-v2-3-followups REVISE
    # round 1 (self-discovered during this item's own /accept-milestone
    # pre-flight): step 4 gained the explicit review_content_manifest
    # extraction requirement (projection["review_content_manifest"], never
    # the whole projection object) -- intentional content change, the fix
    # for the defect that produced two malformed approval records.
    #
    # approve-review.md further updated, workflow-v2-3-followups REVISE
    # round 2 (I1, external cross-model review): the step-4a1 note's
    # `record_bundle_generation`'s own two entry phases" claim corrected
    # to the stage-aware, now-three-phase contract round 2's own record_
    # bundle_generation widening introduced -- intentional content change,
    # a documentation-only correction with no behavioral effect. Worded to
    # explain the REJECTED-bundle guard by citation (WFR-67) rather than by
    # naming assert_bundle_not_rejected inline, so this file's own prose
    # never perturbs test_every_non_exempt_file_calls_the_shared_assertion_
    # the_expected_number_of_times's exact-count check of its two real call
    # sites.
    #
    # approve-review.md further updated, baseline-tag verification cleanup
    # (OPUS-R129-001): the implementation stage's technical-approval commit
    # instruction now also states the same "trailers must be the commit
    # message's own final paragraph" requirement step 6.4 (the plan-stage
    # commit, same step) already states, closing the gap a fresh Opus
    # review found at this second trailer-write site in an already-
    # corrected file -- intentional content change.
    # approve-review.md further updated by the workflow system audit's
    # convergence repair, optional finding 3: step 4b gains the explicit
    # takeover target check -- the plan-approval journal is a single,
    # repository-wide object, so an interrupted transaction belonging to
    # another work item must be reported and refused rather than taken
    # over and completed under this invocation's target.
    # `take_over_plan_approval_transaction` now takes that resolved target
    # as a required argument and refuses a mismatch in production.
    # Intentional content change, not a regression.
    # Updated by the workflow system audit's convergence pass 12 (ledger
    # row `I21`): step 6's implementation-stage paragraph now names the
    # post-commit verification set that stage never had --
    # `validate_technical_approval_commit` (which had no production caller
    # at all) and `verify_post_approval_manifest_match` -- applied to the
    # commit the invocation just created, never retroactively to
    # discovered history. Intentional content change, not a regression.
    # Updated by `workflow-2.4.0` CP3 (`D-Plan-Amendment-4`): step 4c now
    # reads pre_registry/pre_plan_text (via load_pre_amendment_snapshot,
    # open-amendment only) and post_registry/post_plan_text (via a fresh
    # resolve_plan_stage_metadata call and a working-tree read) and forwards
    # all four into open_plan_approval_journal. Intentional content change,
    # not a regression.
    # Updated by `workflow-2.4.0`'s round-6 implementation-review fix
    # (`IMPL6-B1`): step 7 now instructs reporting
    # `amendment_history[-1]["reconciliation_outcome"]`, by id, whenever
    # this invocation's own plan-stage approval resolved an amendment --
    # the reconciliation-outcome report `D-Plan-Amendment-4`'s own prose
    # requires. Intentional content change, not a regression.
    # Updated by `workflow-2.5.0`'s own SELF_REVIEWING_IMPLEMENTATION pass:
    # the implementation-stage ledger-check paragraph now names the stage by
    # its one canonical name, `LOCAL_MODEL_IMPLEMENTATION_REVIEW` -- the same
    # string the ledger key, the `Reviewer role:` line and every normative
    # document already use -- instead of the short-form variant the code
    # originally shipped. Intentional content change, not a regression.
    # Updated again, round 7's `I1` fix: `technical_approval_gate_reachable`'s
    # three "2.2" widening parameters are now required (no longer defaulted
    # to `None`), closing the fail-open gap a caller that omitted
    # `governing_workflow_version` could hit; step 0's and step 1's prose
    # now state that this command passes the work item's real
    # `governing_workflow_version`/`implementation_review_stages`/current
    # `review_content_id` to that call unconditionally, for every governing
    # version, rather than only on a `"2.2"` branch. Intentional content
    # change, not a regression.
    # approve-review.md further updated, D-Feedback-Layout (workflow-2.6.0,
    # CP3): the preamble now states resolve_feedback_dir's feedback_layout-
    # keyed resolution (scoped by construction, legacy rule otherwise) --
    # intentional content change.
    # approve-review.md further updated, D-Plan-Review-Bundle-Binding
    # (workflow-2.6.0, CP4): step 2's bound-bundle reader at the plan stage --
    # intentional content change, not a regression.
    # approve-review.md further updated, D-Plan-Approval-Closure
    # (workflow-2.6.0, CP5): step 4a's fresh member set (declared protected
    # paths plus removals, freshness per member kind, the git-mv remedy),
    # step 5's in-window re-resolution, step 6.3a's write-tree proof, step
    # 6a's committed-truth verification and amend gate, and step 6d's
    # narrowed closing check.
    # approve-review.md further updated, D-Repo-Global-Lifecycle
    # (workflow-2.6.0, CP6): section 5.6's entry table under step 4b, the
    # new step 4d reservation, step 5's first_commit staging entry, 6a1's
    # held check and amend_recovery staging, step 6b's token capture and
    # release, and the new step 6c1 advance before 6d -- intentional
    # content change, pinned row by row by
    # TestApproveReviewLifecycleEntryTable. Implementation review round 1:
    # step 0's two-stage plan branch states the AWAITING_PLAN_APPROVAL-only
    # gate (apply_plan_approval's PlanApprovalPhaseError) -- intentional.
    # Implementation review round 2 (I1): step 4a reads the staged diff
    # NUL-delimited, step 6.3 calls assert_staged_path_set_within, and step
    # 6d's member-dirty check is literal -- intentional.
    # approve-review.md further updated, workflow-2.7.0
    # (ORCHESTRATION_PROTOCOL_V1_PLAN.md, CP4): step 1's gate check calls
    # plan_approval_gate_status/technical_approval_gate_status on the
    # state re-read after the BLOCK pin, reporting the first cause in the
    # wrapper's order; step 2's generation and bundle-bound checks run
    # inside it -- intentional content change.
    # workflow-2.8.0 (gate-policy-and-reopening, CP1): the technical-
    # approval commit stages item-scoped (stage_scoped_state); the plan-
    # approval commit keeps its whole-file pin and
    # verify_plan_approval_commit applies the gate-policy content check --
    # intentional content change.
    "approve-review.md": "e760ecb36c98e271ad483af4d3b3a0bcb6469749f333742bbb993c9e93a7fb21",
    # accept-milestone.md updated, baseline-freeze correctness fix
    # (OPUS-R129-001): step 6's completion-commit instruction now states
    # the same "trailers must be the commit message's own final paragraph"
    # requirement milestone-implement.md/bootstrap-workflow-v2.md/
    # approve-review.md already state -- the fix for the defect class every
    # real /accept-milestone completion commit to date reproduced
    # (27f051e/fb134ac/d271d89, all individually grandfathered in
    # workflow_state_demo_test.py) -- intentional content change.
    #
    # accept-milestone.md further updated, dead-contract cleanup (ledger
    # `I10`): step 2a's two refusal branches pointed the operator at
    # /accept-scoped-remediation, a command whose own gate had no producer
    # in any supported lifecycle. Both now name the three supported ways
    # forward instead (finish the checkpoint with /milestone-implement, or
    # route a functional finding through /apply-functional-review's
    # bounded/broad branches) -- intentional content change.
    #
    # accept-milestone.md further updated, Orchestration Protocol v1
    # (workflow-2.7.0, CP6, v2.6.0-003): step 2a no longer tells the operator
    # to finish an outstanding checkpoint with /milestone-implement, which
    # cannot start one at AWAITING_FUNCTIONAL_REVIEW; it says no 2.6.0
    # command completes one there, and keeps the /apply-functional-review
    # routing -- intentional content change.
    #
    # accept-milestone.md further updated, workflow-2.8.0 (gate-policy-and-
    # reopening, CP4): a pointer to /satisfy-gate acceptance, step 2a's
    # requires_pr_approved pre-flight (only when the option is set) and step 5
    # pinned to copy, never move (LPR-R2-003) -- intentional content change.
    #
    # accept-milestone.md further updated, workflow-2.8.0 (gate-policy-and-
    # reopening, CP5): step 5 states what a re-acceptance of a reopened item
    # does (one archive copy and one roadmap row, `acceptance_satisfaction`
    # overwritten, `completion_obligations_accepted` kept) -- intentional
    # content change.
    # accept-milestone.md further updated, workflow-2.9.0 (legacy-retire-and-
    # default-version, CP5): step 2a's non-terminal-registry bullet names the
    # user-only /resume-implementation for a 2.1/2.2 item and drops the wrong
    # "legacy promotion" origin -- intentional content change, outside every
    # "1"-inert span.
    "accept-milestone.md": "ace07f7d4ccbe3660ef477651b11562502513bd8e55aefab5deff3ec0994d1c8",
    # prepare-functional-review.md further updated, baseline-portability
    # correctness fix (OPUS-R129-001): step 3a's checklist-evidence
    # provenance commit instruction now states the same "trailers must be
    # the commit message's own final paragraph" requirement
    # milestone-implement.md/bootstrap-workflow-v2.md/approve-review.md/
    # accept-milestone.md already state -- intentional content change.
    #
    # prepare-functional-review.md further updated, dead-contract cleanup
    # (ledger `I10`): step 3a's rationale and step 4's operator
    # instruction both described the retired /accept-scoped-remediation's
    # confirmation guard as the consumer of the checklist-evidence commit.
    # The commit itself stays -- step 4 reports its identity and
    # /review-functional reads it back -- but the guidance now names only
    # supported next steps -- intentional content change.
    # prepare-functional-review.md further updated, D-Feedback-Layout
    # (workflow-2.6.0, CP3): the preamble states feedback_layout-keyed
    # resolution, and step 4 calls ensure_feedback_dir and prints the exact
    # resolved FUNCTIONAL_REVIEW.md path -- intentional content change.
    "prepare-functional-review.md": "cddd822a7fe1b1c6a47596ab510baefd418f2dea4ed755f1445a3aa33adab65f",
    # apply-plan-review.md/bootstrap-workflow-v2.md (D-Plan-Revision-Publication,
    # WFR-65): intentional content change, publish_plan_revision wiring.
    # apply-plan-review.md further updated, convergence pass 12 (ledger row
    # `I22`): step 5 now names the `render_registry_markdown` re-embed the
    # plan document's generated checkpoint table owes whenever the registry
    # is regenerated. Intentional content change, not a regression.
    # apply-plan-review.md further updated, D-Feedback-Layout
    # (workflow-2.6.0, CP3): the preamble's <feedback_dir> definition now
    # states feedback_layout-keyed resolution and step 1 prints the exact
    # resolved path when REVIEW_FEEDBACK.md is absent -- intentional content
    # change.
    # apply-plan-review.md further updated, D-Plan-Review-Bundle-Binding
    # (workflow-2.6.0, CP4): the [2.1] entry, step 1's REVISE-only acceptance
    # and durable feedback check, step 5's publish on every round after
    # staging, <plan_inputs_dir>, step 6 restated "1"-only, and step 7' as
    # verify-plus-bind with 7'.2's regeneration removed -- intentional content
    # change, not a regression. Implementation review round 1: step 5 states
    # that a legacy-marked item must advance plan_revision -- intentional.
    # apply-plan-review.md further updated, workflow-2.7.0
    # (ORCHESTRATION_PROTOCOL_V1_PLAN.md, CP4): step 1 binds through
    # assert_apply_review_feedback_binding (D-Apply-Binding): a two-stage
    # REVISE stating a review_content_id is bound by content --
    # intentional content change.
    "apply-plan-review.md": "17c7ea54b5d98cd5267dccbe42df5b4e37ed8a1fc8be3762d34aa9b2db5a4658",
    # apply-implementation-review.md (WF8c item (c)): step 7's
    # record_bundle_generation call site widened to first resolve the
    # outcome (resolve_bundle_generation_outcome) and write the matching
    # ordinary/recovered-role trailer set -- intentional content change.
    # apply-implementation-review.md further updated, baseline-portability
    # correctness fix (OPUS-R129-001): step 7's post-fix
    # bundle-generation-record commit instruction now states the same
    # "trailers must be the commit message's own final paragraph"
    # requirement milestone-implement.md/bootstrap-workflow-v2.md/
    # approve-review.md/accept-milestone.md already state -- intentional
    # content change.
    # apply-implementation-review.md further updated, round-4 implementation
    # review fix (I3): the preamble's "workflow-2.5.0, "2.2"-governed items
    # only: skip this call too" wording is replaced with a phase-conditional
    # rule ("skip this call whenever phase already equals
    # APPLYING_REVIEW_FEEDBACK", version-independent) so a "2.2" item that
    # reaches the terminal AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW phase
    # (both implementation-review stages already APPROVEd, then a late fix
    # is committed) can still call enter_applying_review_feedback and
    # re-enter APPLYING_REVIEW_FEEDBACK -- the version-keyed wording left
    # that item with no in-band escape. Step 0's own dual-mode enumeration
    # updated to match. Intentional content change, not a regression.
    # apply-implementation-review.md further updated, D-Feedback-Layout
    # (workflow-2.6.0, CP3): the preamble states feedback_layout-keyed
    # resolution and step 1 prints the exact resolved path when
    # REVIEW_FEEDBACK.md is absent -- intentional content change.
    # apply-implementation-review.md further updated, workflow-2.7.0
    # (ORCHESTRATION_PROTOCOL_V1_PLAN.md, CP4): step 1 binds through
    # assert_apply_review_feedback_binding (D-Apply-Binding): a 2.2 REVISE
    # stating a review_content_id is bound by content -- intentional
    # content change.
    # workflow-2.8.0 (gate-policy-and-reopening, CP1): the generation-record
    # commit stages item-scoped (stage_scoped_state) -- intentional content
    # change.
    "apply-implementation-review.md": "deaf93154185971241690ee4b5e0c8ec9b21a765a957e5cae78f1a66627a76ba",
    # review-plan.md/record-manual-plan-review.md further updated,
    # workflow-v2-3-followups CP3 (REQ-8/-9): the `Reviewer role:` template
    # literal, the round-computation prose, the exact-match-expectation
    # prose, and every other `local_model_plan_review`/
    # `manual_external_plan_review` mention repointed to the canonical
    # `LOCAL_MODEL_PLAN_REVIEW`/`MANUAL_EXTERNAL_PLAN_REVIEW` casing (the
    # legacy casing is still stated as accepted where the command genuinely
    # tolerates it) -- intentional content change.
    # Updated by convergence pass 12 (ledger row `O31`): the two plan-review
    # consumers below named no computation at all for the plan-stage
    # `review_content_id` they recompute; both now reference
    # `REVIEW_PROTOCOL.md`'s "Computing `review_content_id`". Intentional
    # content change, not a regression.
    #
    # review-plan.md further updated, workflow-2.5.0 REVISE round 2 (I3):
    # step 8's write set gains the same assert_feedback_not_owned_by_other_
    # work_item ownership guard /review-implementation's advisory branch
    # step 7 already runs immediately before its own write -- this command
    # had never had it, and its write is exactly as capable of destroying
    # another work item's unconsumed feedback at the same scoped-else-flat
    # `resolve_feedback_dir` path -- intentional content change, not a
    # regression.
    # review-plan.md further updated, D-Feedback-Layout (workflow-2.6.0,
    # CP3): preamble states feedback_layout-keyed resolution; step 8's
    # ownership guard passes state= (bounded legacy-writer terminal-owner
    # relaxation) and calls ensure_feedback_dir before the write; step 9
    # prints the exact resolved paste path -- intentional content change.
    # review-plan.md further updated, D-Plan-Review-Bundle-Binding
    # (workflow-2.6.0, CP4): the bound-bundle reader
    # (validate_local_plan_review_preconditions_bound) and the CONSUMED write
    # on REVISE -- intentional content change, not a regression.
    # review-plan.md further updated, D-Feedback-Label (workflow-2.7.0,
    # CP1, v2.6.0-002): step 7 names the pinned Reviewed review_content_id:
    # label, before the first ## section -- intentional content change.
    # review-plan.md further updated, D-Consumed-History (workflow-2.7.0,
    # CP2, v2.6.0-001): REVISE also adds the id to the durable
    # consumed_plan_review_content_ids history -- intentional content change.
    "review-plan.md": "61cb286719b6f310c61f9304324d6994f74dc9c443661178b81c051e05d27e0d",
    # record-manual-plan-review.md further updated, D-Feedback-Layout
    # (workflow-2.6.0, CP3): preamble states feedback_layout-keyed
    # resolution; step 4 prints the exact resolved paste path and adds the
    # assert_manual_feedback_names_work_item foreign-Work-item refusal
    # before any state write -- intentional content change.
    # record-manual-plan-review.md further updated, D-Plan-Review-Bundle-
    # Binding (workflow-2.6.0, CP4): the bound-bundle reader
    # (assert_plan_review_bundle_bound) and the CONSUMED write on REVISE --
    # intentional content change, not a regression.
    # record-manual-plan-review.md further updated, D-Feedback-Label
    # (workflow-2.7.0, CP1, v2.6.0-002): step 4 names the pinned
    # Reviewed review_content_id: label and the header-before-## rule --
    # intentional content change.
    # record-manual-plan-review.md further updated, D-OP-External
    # (workflow-2.7.0, CP5): steps 2-7 are one call to the shared
    # ingest_manual_review_verdict (two_stage_only=True), which holds
    # state_lock through the publication; the required header fields,
    # Round: and the absent-bundle-id advisory -- intentional content change.
    "record-manual-plan-review.md": "f5647bdb1af514a4c89d0fdf9e6bfe098009d48ee6f124c0bf26684f2870b58f",
    # bootstrap-workflow-v2.md (WF8c scope clauses (l)/(p)/(q), GPT-R108-002/
    # OPUS-R109-004): the driver-range text made checkpoint-agnostic
    # (OPUS-R102-009), a NO_CHECKPOINT terminal-wrap-up branch added to step
    # 3, and step 6 bound explicitly to complete_checkpoint(...) -- WF8c's
    # own first invocation, intentional content change.
    #
    # bootstrap-workflow-v2.md further updated, WF8c item 352: a new step 0
    # (ownership-aware and guard-aware plan-approval transaction
    # precondition, reading plan_approval_takeover_evidence before step 1)
    # and step 2's durability guard rebound from the bare
    # plan_approval.approved_review_content_id equality to
    # implementing_entry_reachable -- intentional content change.
    #
    # bootstrap-workflow-v2.md further updated, OPUS-R129-001: step 6's
    # checkpoint-completion commit instruction now states explicitly that
    # the Workflow-Checkpoint/Workflow-Work-Item trailer must be the
    # commit message's own final paragraph, after any Co-Authored-By:/
    # Claude-Session: lines, never before them -- intentional content
    # change (the mechanical fix for 4a769fd's own defect class).
    #
    # bootstrap-workflow-v2.md further updated, baseline-tag verification
    # cleanup (OPUS-R129-001): the NO_CHECKPOINT terminal-wrap-up branch's
    # bundle-generation-record commit instruction (step 3) now also states
    # the same "trailers must be the commit message's own final paragraph"
    # requirement step 6 already states, closing the gap a fresh Opus
    # review found at this second trailer-write site in an already-
    # corrected file -- intentional content change.
    "bootstrap-workflow-v2.md": "01d5873ba807e78f0d0618eb944246783cb3827fe3e31ad253e0158f879e5cae",
    # review-implementation.md: new, workflow-v2-3 CP1 -- the first
    # recorded hash, not a change.
    #
    # review-implementation.md further updated, GPT-IR1-001 (round 1
    # implementation-review remediation): corrected the false claim that
    # an excluded-only concurrent commit surfaces as a digest mismatch
    # before the HEAD difference -- intentional content change.
    #
    # review-implementation.md further updated, workflow-v2-3-followups CP2
    # (REQ-3/REQ-4/REQ-6/REQ-17/REQ-20/REQ-21): step 3 gained a PLAN.md/
    # plan_path read; step 5 gained a plan-conformance search arm; step 7 is
    # new -- a second assert_bundle_not_rejected call plus the new
    # assert_feedback_not_owned_by_other_work_item ownership guard,
    # immediately before an unconditional write of
    # <feedback_dir>/REVIEW_FEEDBACK.md; step 8 (was step 7) no longer
    # states the command writes nothing; the frontmatter description: line
    # and the step 6/step 7 provenance text no longer describe a manual
    # hand-copy installation -- intentional content change, not a
    # regression.
    #
    # review-implementation.md further updated, workflow-v2-3-followups
    # REVISE round 1 (O5): step 7 gained a self-check on the composed
    # report text -- parse_review_feedback_binding_fields/
    # assert_feedback_matches_bundle against step 4's own recomputed
    # values, immediately before the write -- so step 6's "hard
    # precondition" wording is now actually enforced -- intentional
    # content change.
    #
    # review-implementation.md further updated, workflow-2.5.0 CP11/CP13:
    # the "2.2" authoritative LOCAL_MODEL_IMPLEMENTATION_REVIEW branch and
    # its State-writer discipline paragraph, with the frontmatter
    # declaration reclassified `false` -> `true` (CP13: CP11 had written an
    # unparseable `conditional` value, which `discover_state_writers` fails
    # closed on -- see that paragraph's own "Why the frontmatter declares
    # state_writer: true" note) -- intentional content change.
    #
    # review-implementation.md further updated by `workflow-2.5.0`'s own
    # SELF_REVIEWING_IMPLEMENTATION pass: step A6's parenthetical no longer
    # reconciles two names for the local stage -- the ledger key and the
    # `Reviewer role:` string are now the identical
    # `LOCAL_MODEL_IMPLEMENTATION_REVIEW`, as the approved plan and every
    # normative document already declared -- intentional content change.
    #
    # review-implementation.md further updated, workflow-2.5.0 REVISE round 2
    # (I3): the "2.2" authoritative branch's A6 write set gains the same
    # assert_feedback_not_owned_by_other_work_item ownership guard the
    # "1"/"2.1" advisory branch's own step 7 already runs immediately before
    # its write -- A6's write is exactly as capable of destroying another
    # work item's unconsumed feedback at the same scoped-else-flat path, and
    # the advisory branch above stays byte-unchanged -- intentional content
    # change, not a regression.
    # review-implementation.md further updated, D-Feedback-Layout
    # (workflow-2.6.0, CP3): preamble states feedback_layout-keyed
    # resolution; step 7 and A6 ownership guards pass state= (bounded
    # legacy-writer terminal-owner relaxation) and call ensure_feedback_dir
    # before the write; step 7's recovery text scopes the hand-created-dir
    # warning to legacy items; A7 prints the exact resolved paste path --
    # intentional content change.
    # review-implementation.md further updated, D-Feedback-Label
    # (workflow-2.7.0, CP1, v2.6.0-002): step 6 and A5 write the pinned
    # Reviewed review_content_id: label, before the first ## section --
    # intentional content change.
    # review-implementation.md further updated, workflow-2.7.0
    # (ORCHESTRATION_PROTOCOL_V1_PLAN.md, CP4): step 4's bundle check is
    # verify_implementation_review_bundle -- intentional content change.
    "review-implementation.md": "656538d4033c9a086adf1c0b387f15d18ee5a5b95af782a6267a712018d04060",
    # review-functional.md: new, workflow-v2-3 CP2 -- the first recorded
    # hash, not a change.
    #
    # review-functional.md updated, dead-contract cleanup (ledger `I10`):
    # its two references to the acceptance gates named
    # /accept-milestone`/`/accept-scoped-remediation` as a pair; the second
    # is retired, so both now name /accept-milestone alone -- intentional
    # content change.
    # review-functional.md further updated, D-Feedback-Layout
    # (workflow-2.6.0, CP3): the preamble now states resolve_feedback_dir's
    # feedback_layout-keyed resolution (scoped by construction, legacy rule
    # otherwise) -- intentional content change.
    "review-functional.md": "e65e4218d1a7c7870b37ca36e5a2e3c0b7264a685a4a1f8bd7a4904b5e4a24f9",
    # apply-functional-review.md (O2, workflow-v2-3-followups REVISE round
    # 2, external cross-model review): this roster's own scope was fixed
    # to "every command file workflow-v2-1-core's own dual-mode
    # enumeration names, plus the bootstrap command and the bundle
    # script" (see this file's own comment above `_GOLDEN_COMMAND_FILE_
    # SHA256`) -- a specific, historically-bounded list, never literally
    # "every command file any milestone modifies." apply-functional-
    # review.md predates that list but was never added to it; round 2's
    # own record_bundle_generation fix (Defect 2) modified it for real,
    # exposing the gap. Added here as a deliberate, bounded widening --
    # first recorded hash, not a change -- rather than left as a blind
    # spot for a file this repository's remediation flow now actively
    # edits. Three further command files remain outside this roster,
    # left there deliberately rather than silently swept in by this same
    # widening: accept-scoped-remediation.md (since deleted outright,
    # ledger I10), prepare-review.md, and
    # recover-implementation-provenance.md (the last of which this same
    # round also edited, for I1) -- none was ever part of the roster's
    # own original scope, and none is a drift-detection gap this
    # revision's own changes newly exposed the way apply-functional-
    # review.md's was.
    #
    # apply-functional-review.md further updated, same round (O3): step 1
    # gained the already-applied refusal
    # (assert_functional_review_not_already_consumed), and both of this
    # command's own exit points (the bounded branch's step 5, and the
    # normal step 7) gained the mark_functional_review_consumed call --
    # intentional content change.
    #
    # apply-functional-review.md further updated, baseline-portability
    # correctness fix (OPUS-R129-001): the bounded branch's post-fix
    # bundle-generation-record commit instruction now states the same
    # "trailers must be the commit message's own final paragraph"
    # requirement milestone-implement.md/bootstrap-workflow-v2.md/
    # approve-review.md/accept-milestone.md already state -- intentional
    # content change.
    # apply-functional-review.md further updated by the workflow system
    # audit's convergence pass 11 (ledger `I19`): the preamble now resolves
    # `<bundle_dir>` through `workflow_fingerprint.resolve_bundle_dir` (the
    # compatibility form, not the plan stage's scoped-by-construction one),
    # and the bounded-code-change branch names the two author-written
    # generation preconditions it drives -- `IMPLEMENTATION_SUMMARY.md`'s
    # `implementation_revision: <N>` line and `REVIEW_REQUEST.md`'s
    # `review_content_id: <hex>` line -- with the assertions that enforce
    # them, what each outcome does to the counter, and the two different
    # consequences (withdrawal vs outright refusal). Intentional content
    # change, not a regression.
    # Updated by `workflow-2.4.0` CP3 (`D-Plan-Amendment-1`): the broad-
    # remediation branch's sanctioned child sequence now names
    # `/request-plan-amendment <child-id>` as an addendum a child may use
    # once it reaches `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION`, on the
    # same terms as its parent. Intentional content change, not a
    # regression.
    # apply-functional-review.md further updated, D-Feedback-Layout
    # (workflow-2.6.0, CP3): the preamble states feedback_layout-keyed
    # resolution and step 1 prints the exact resolved path when
    # FUNCTIONAL_REVIEW.md is absent -- intentional content change.
    # apply-functional-review.md further updated, D-Plan-Review-Bundle-Binding
    # (workflow-2.6.0, CP4): a two-stage remediation child's publish is
    # mirror-only and its bind writes AWAITING_LOCAL_PLAN_REVIEW --
    # intentional content change, not a regression.
    # workflow-2.8.0 (gate-policy-and-reopening, CP1): the generation-record
    # commit stages item-scoped (stage_scoped_state) -- intentional content
    # change.
    # workflow-2.8.0 (gate-policy-and-reopening, CP4): a closing cross-reference
    # to /satisfy-gate acceptance -- intentional content change.
    # workflow-2.8.0 (gate-policy-and-reopening, CP5): the remediation child
    # sequence names /apply-pr-review <child-id> for a child whose pull
    # request turns red -- intentional content change.
    # workflow-2.9.0 (legacy-retire-and-default-version, CP5): step 0's "An entry
    # exists" bullet names the user-only /resume-implementation for an outstanding
    # checkpoint at this phase -- intentional content change, outside every
    # "1"-inert span.
    "apply-functional-review.md": "2e1518c67823d41b3ae2a1776c5f0e95931a45d2728cf68dcd0983d94d45ead2",
}


class TestPlanApprovalGuardCarriesNoWorkItemLiteral(unittest.TestCase):
    """Salvage audit `O8`: the plan-approval mutation guard is
    repository-scoped -- one fixed path, one holder at a time, whichever
    work item is being approved -- so its body must not assert a
    work-item identity it does not know. It used to hardcode
    `"work_item_id": "workflow-v2-1-core"`, which was simply false for
    any other item's plan approval."""

    def test_guard_body_names_no_work_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _run(["git", "init", "-q"], cwd=root)
            _run(["git", "config", "user.email", "t@e.st"], cwd=root)
            _run(["git", "config", "user.name", "t"], cwd=root)
            (root / "a.txt").write_text("a\n")
            _run(["git", "add", "-A"], cwd=root)
            _run(["git", "commit", "-q", "-m", "base"], cwd=root)
            lease = ws.acquire_plan_approval_guard(
                root, holder_owner_token="t0" * 16, step="step-5-stage-and-pin", now="t1",
            )
            try:
                held = ws.read_plan_approval_guard(root)
                self.assertNotIn("work_item_id", held)
                self.assertNotIn("workflow-v2-1-core", json.dumps(held))
                # The identity a caller actually needs comes from the
                # journal, which is per-item.
                self.assertEqual(
                    set(held),
                    {"lease_id", "holder_owner_token", "stage", "step", "step_class",
                     "acquired_at"},
                )
            finally:
                ws.release_plan_approval_guard(root, lease)


# ---------------------------------------------------------------------------
# The generation-driving command census (workflow system audit, convergence
# pass 11, ledger row `I19`).
#
# `TestGenerationCommandsNameTheCompleteAuthorInputSet` below used to name
# four commands by hand and call that the population of commands that drive
# `scripts/prepare-ai-review.sh`. It was wrong: eight command files instruct
# their own run of the generator, and the one the hand-written list omitted
# (`/apply-functional-review`) was the one whose bounded-fix branch named no
# author-written precondition at all. A census that is a hand-maintained
# literal cannot notice its own omissions, so the population is now
# *discovered* from the command corpus and the literal below is checked
# against it -- a ninth driver, or a renamed one, fails this suite instead of
# quietly falling outside it.
#
# Two regexes, because "mentions the generator" and "drives the generator"
# are genuinely different populations and both matter: a command may name the
# script in a diagnostic it tells the operator to report (`/review-implementation`)
# or in an explanation of a resolver's compatibility branch (`/approve-review`)
# without ever running it. The run regex requires the imperative form -- a
# `run`/`rerun` word immediately followed by the backticked `./scripts/...`
# invocation -- which is exactly how all eight drivers phrase their own step
# and is not how either mention-only command phrases its reference.
# ---------------------------------------------------------------------------

_GENERATOR_MENTION_RE = re.compile(r"prepare-ai-review\.sh")
# The stage token is captured, not merely stepped over (convergence pass 12,
# ledger `O29`): the obligations below are *derived* from the stage a command
# actually names in its own invocation line, so no second hand-maintained
# command-name list decides which drivers owe which authoring contract. The
# stage alternative admits a `<placeholder>` (which may contain spaces, as in
# `<that stage>`) as well as a literal `post-fix`-shaped token.
_GENERATOR_RUN_RE = re.compile(
    r"\b(?:[Rr]un|[Rr]erun)\b\s*`\./scripts/prepare-ai-review\.sh"
    r"\s+(?:<[^>\n]*>|\S+)\s+(<[^>\n]*>|[a-z][a-z-]*)"
)


def _command_filenames() -> list[str]:
    return sorted(p.name for p in (_repo_root() / ".claude" / "commands").glob("*.md"))


def _commands_matching(pattern: "re.Pattern[str]") -> set[str]:
    return {name for name in _command_filenames() if pattern.search(_command_text(name))}


def _declared_generation_stages(text: str) -> set[str]:
    """Every stage token this command's own generator-run instructions
    name, read out of the invocation line itself rather than declared
    beside the filename. A command with more than one run instruction
    contributes all of them."""
    return {match.group(1) for match in _GENERATOR_RUN_RE.finditer(text)}


#: The stages whose round carries an `implementation_revision` that can move
#: between generations, so both author-written marker lines have to be
#: refreshed immediately before the run. `plan` is the only generation stage
#: that is not one of these (`functional-review` writes no manifest at all
#: and no command drives it; `prepare-review.md` can still name it through
#: its `<stage>` placeholder, which is handled below).
_MOVING_COUNTER_STAGES = frozenset({"implementation", "post-fix"})


def _carries_moving_counter_obligation(stages: "set[str]") -> bool:
    """Whether a driver naming `stages` owes the moving-counter authoring
    contract specifically. A literal `implementation`/`post-fix` obviously
    does, and so does a `<placeholder>` stage -- the caller
    (`/prepare-review`) or a previous round's `MANIFEST.md`
    (`/recover-implementation-provenance`) resolves those at run time and
    can resolve them to either moving stage, so the derivation is
    default-deny for them too."""
    return any(
        stage in _MOVING_COUNTER_STAGES or stage.startswith("<") for stage in stages
    )


#: Which contract `kind`s are admissible for a driver naming a given stage.
#: This is the anti-mis-declaration half: a driver whose invocation line
#: runs `post-fix` cannot claim the plan stage's (weaker, different)
#: marker-line contract, and vice versa. The two exemption kinds are
#: admissible at any stage -- they carry their own evidence checks instead.
_CONTRACT_KINDS_BY_STAGE = {
    "plan": frozenset({"plan-marker-step", "protocol-reference", "pinned-round"}),
    "moving": frozenset({"moving-counter-step", "protocol-reference", "pinned-round"}),
}


def _admissible_contract_kinds(stages: "set[str]") -> "frozenset[str]":
    """Every `kind` a driver naming `stages` could legitimately declare.
    A `<placeholder>` stage is both at once, so only the two exemption
    kinds -- the ones that hold whatever the placeholder resolves to --
    remain admissible for it."""
    admissible = None
    for stage in stages:
        key = "moving" if stage in _MOVING_COUNTER_STAGES else "plan"
        allowed = _CONTRACT_KINDS_BY_STAGE[key]
        if stage.startswith("<"):
            allowed = _CONTRACT_KINDS_BY_STAGE["plan"] & _CONTRACT_KINDS_BY_STAGE["moving"]
        admissible = allowed if admissible is None else (admissible & allowed)
    return admissible if admissible is not None else frozenset()


# filename -> the stage token set its own invocation lines name. Not trusted
# prose: `test_every_declared_stage_matches_the_commands_own_invocation_line`
# checks each entry against `_declared_generation_stages` over the live file,
# so a driver cannot be declared `plan` while running `post-fix`.
_GENERATION_DRIVING_COMMANDS = {
    "milestone-plan.md": {"plan"},
    "apply-plan-review.md": {"plan"},
    "milestone-implement.md": {"implementation"},
    "apply-implementation-review.md": {"post-fix"},
    "apply-functional-review.md": {"post-fix"},
    # Regenerates at whatever stage the existing MANIFEST.md records --
    # recovery changes which commit the content is measured at, never what
    # kind of round it is.
    "recover-implementation-provenance.md": {"<that stage>"},
    # Ad-hoc, outside the milestone gates: the caller names the stage.
    "prepare-review.md": {"<stage>"},
    # One-time bootstrap driver, retained as an accepted Optional. Its
    # hardcoded target work item is complete, so it is unreachable at
    # current HEAD and fails closed rather than merely idling -- executed,
    # not claimed here, by `workflow_state_demo_test.py`'s
    # `test_the_bootstrap_driver_is_unreachable_and_fail_closed_at_live_head`
    # (ledger `O33`).
    "bootstrap-workflow-v2.md": {"implementation"},
}

# Commands that name the generator without ever running it. Recorded
# explicitly, with the reason, so the discovery test above can prove the two
# populations partition the corpus rather than merely overlapping it.
_GENERATOR_MENTION_ONLY_COMMANDS = {
    "approve-review.md": (
        "explains why the technical/implementation stage keeps the "
        "scoped-else-flat resolver rule -- the bundle it reads may have been "
        "generated without the script's optional [work-item-id] argument"
    ),
    "review-implementation.md": (
        "report-only: names the omitted-id invocation as the documented cause "
        "of a MissingRequiredBundleFileError refusal, and tells the *user* to "
        "regenerate scoped; it never generates anything itself"
    ),
    "request-plan-amendment.md": (
        "workflow-2.4.0, I-R14-2: its own 'what happens next' prose names "
        "prepare-ai-review.sh to explain that AMENDMENT_DIFF.patch will "
        "appear in the next plan-stage bundle while this amendment stays "
        "open -- deliberately phrased without a run/rerun-plus-backticked-"
        "invocation construction, so it never generates anything itself"
    ),
}

# How each generation-driving command discharges its own author-written
# generation preconditions. **Every** driver needs an entry: silence is a
# violation, never an exemption (convergence pass 12, ledger `O29`).
#
# The previous shape was a second hand-maintained command-name list sitting
# behind the mechanically discovered census, naming only the three
# implementation/post-fix drivers. A driver simply absent from it conformed
# while naming no marker line at all -- and the same hole existed on the
# plan side, where the two live drivers' contracts were asserted by two
# hand-written `assertIn` tests naming those two files, so a *third* plan
# driver would have been checked by nothing.
#
# Every stage this generator accepts has author-written preconditions that
# fail the generation when unmet, so there is no stage for which "no
# contract" is the right answer:
#
# - `plan`: `assert_test_results_consistent_with_plan_review_request`'s
#   `stage: plan (revision N)` / `head: <sha>` pair in `TEST_RESULTS.md`
#   (withdrawal), plus `assert_review_request_states_review_content_id`
#   (refusal);
# - `implementation`/`post-fix`: `assert_stage_completeness`'s
#   `implementation_revision: <N>` line in `IMPLEMENTATION_SUMMARY.md`
#   (withdrawal), plus the same `review_content_id` refusal.
#
# `kind` selects the discipline, and is itself checked against the stage the
# command's own invocation line names (`_admissible_contract_kinds`), so a
# `post-fix` driver cannot claim the plan stage's different contract:
#
# - "moving-counter-step": the named step block states the
#   implementation/post-fix marker lines and the assertions that enforce
#   them. `review_request_contract` records which of the two sanctioned
#   forms the command uses to state the `REVIEW_REQUEST.md` half:
#     - "explicit": the step block names the `review_content_id: <hex>` line
#       and the assertion that enforces it;
#     - "protocol-reference": the step block says to author REVIEW_REQUEST.md
#       *per* `docs/ai-workflow/REVIEW_PROTOCOL.md`, whose "Author-written
#       files" section states the same requirement.
#   `states_revision_behaviour` marks the drivers whose round can resolve
#   either generation outcome and which therefore have to say what the
#   counter does in each.
# - "plan-marker-step": the plan-stage counterpart -- the named step block
#   states `TEST_RESULTS.md`'s two marker lines and the assertion that
#   enforces them.
# - "protocol-reference": the whole command routes its bundle-input authoring
#   through `docs/ai-workflow/REVIEW_PROTOCOL.md` rather than restating the
#   contract inline -- the two drivers outside the milestone gates, which is
#   also why they are the two whose stage is a placeholder or unreachable.
#   The evidence for that claim is checked, not assumed, on both the command
#   and the protocol document.
# - "pinned-round": the round's `implementation_revision` cannot move at all,
#   by the command's own contract, so both author-written lines stay valid
#   untouched. The evidence for *that* claim is checked too.
_GENERATION_AUTHORING_CONTRACTS = {
    "milestone-plan.md": {"kind": "plan-marker-step", "step": "6"},
    "apply-plan-review.md": {"kind": "plan-marker-step", "step": "5"},
    "milestone-implement.md": {
        "kind": "moving-counter-step",
        "step": "4",
        "review_request_contract": "protocol-reference",
        "states_revision_behaviour": False,
    },
    "apply-implementation-review.md": {
        "kind": "moving-counter-step",
        "step": "7",
        "review_request_contract": "explicit",
        "states_revision_behaviour": True,
    },
    "apply-functional-review.md": {
        # The bounded-code-change branch lives inside the "[state-tracked
        # items only] Step 4, replaced" block, which `_extract_numbered_steps`
        # returns as part of step 4 (its sub-items are indented, so they are
        # not top-level numbered steps).
        "kind": "moving-counter-step",
        "step": "4",
        "review_request_contract": "explicit",
        "states_revision_behaviour": True,
    },
    "recover-implementation-provenance.md": {"kind": "pinned-round"},
    "prepare-review.md": {"kind": "protocol-reference"},
    "bootstrap-workflow-v2.md": {"kind": "protocol-reference"},
}


def _generation_authoring_violations(
    filename: str, text: str, contracts: "dict | None" = None,
) -> list[str]:
    """Every author-written generation precondition `filename` must name,
    as a list of human-readable violations -- empty means the contract is
    complete. Deliberately a pure function of the command text rather than
    a bag of `assertIn` calls, so the identical checker can run against the
    live file *and* against a deliberately reverted copy of it
    (`TestRevertingTheAuthoringInstructionsFailsConformance`). A conformance
    assertion nobody has ever seen fail is a claim, not evidence.

    `contracts` defaults to `_GENERATION_AUTHORING_CONTRACTS`; the
    synthetic-driver control arm passes its own table instead, so the
    planted driver never has to be written into the live corpus every
    other test in this suite reads."""
    violations: list[str] = []
    if "resolve_bundle_dir" not in text:
        violations.append(
            f"{filename}: never resolves <bundle_dir> through "
            f"workflow_fingerprint.resolve_bundle_dir, so the half that authors "
            f"the bundle's input files and the half that generates it can "
            f"disagree about which directory they mean"
        )
    stages = _declared_generation_stages(text)
    if not stages:
        return violations
    if contracts is None:
        contracts = _GENERATION_AUTHORING_CONTRACTS
    spec = contracts.get(filename)
    if spec is None:
        # Default-deny (ledger `O29`). Every stage this generator accepts
        # has author-written preconditions that fail the generation when
        # unmet, so a driver with no declared contract is a driver nothing
        # holds to any of them.
        violations.append(
            f"{filename}: runs the generator at stage(s) {sorted(stages)} but "
            f"declares no entry in _GENERATION_AUTHORING_CONTRACTS -- so none of "
            f"its author-written generation preconditions (`TEST_RESULTS.md`'s "
            f"`stage: plan (revision N)`/`head: <sha>` pair at the plan stage, "
            f"`IMPLEMENTATION_SUMMARY.md`'s `implementation_revision: <N>` line "
            f"at the implementation/post-fix stages, `REVIEW_REQUEST.md`'s "
            f"`review_content_id: <hex>` line at every stage) is required of it "
            f"by anything"
        )
        return violations
    admissible = _admissible_contract_kinds(stages)
    if spec["kind"] not in admissible:
        # The anti-mis-declaration half: the declared discipline has to be
        # one the command's own stage can actually owe.
        violations.append(
            f"{filename}: declares the {spec['kind']!r} authoring contract, which "
            f"is not admissible for a driver running the generator at stage(s) "
            f"{sorted(stages)} -- admissible kinds are {sorted(admissible)}"
        )
        return violations
    if spec["kind"] == "pinned-round":
        # The exemption's own evidence, checked rather than assumed: the
        # command must state that its round is pinned, which is what makes
        # both author-written lines stay valid untouched.
        for needed in ("never a new revision", "recovery does not change what kind of round"):
            if needed not in text:
                violations.append(
                    f"{filename}: claims the pinned-round exemption from the "
                    f"moving-counter authoring contract but never states "
                    f"{needed!r}"
                )
        return violations
    if spec["kind"] == "protocol-reference":
        for needed in ("REVIEW_REQUEST.md", "docs/ai-workflow/REVIEW_PROTOCOL.md"):
            if needed not in text:
                violations.append(
                    f"{filename}: routes bundle-input authoring through the review "
                    f"protocol instead of restating it, but never names {needed!r}"
                )
        return violations
    block = _extract_numbered_steps(text).get(spec["step"], "")
    # Both step-form disciplines owe this one (convergence pass 12, ledger
    # `O31`): a driver that tells the author to state a `review_content_id`
    # has to say where the value comes from. Before this repair not one of
    # the five in-gate drivers named any computation at all, so "state the
    # correct digest" was an instruction with no procedure -- and the
    # generation *refuses* outright on a wrong one, it does not warn. The
    # reference is to the single shared contract; no command restates the
    # algorithm. Whitespace-normalized, since every driver line-wraps the
    # phrase differently.
    normalized_block = " ".join(block.split())
    for needed in ('"Computing `review_content_id`"',
                   "docs/ai-workflow/REVIEW_PROTOCOL.md"):
        if needed not in normalized_block:
            violations.append(
                f"{filename} step {spec['step']}: tells the author to state a "
                f"`review_content_id` but never names {needed} -- the one place "
                f"the canonical computation for this stage is written down"
            )
    if spec["kind"] == "plan-marker-step":
        # The plan stage's own author-written generation precondition, held
        # to the same standard as its implementation-stage counterpart
        # below: the marker lines, the assertion that enforces them, and the
        # consequence of a stale one.
        for needed in ("TEST_RESULTS.md", "stage: plan (revision N)", "head: <sha>",
                       "assert_test_results_consistent_with_plan_review_request"):
            if needed not in block:
                violations.append(
                    f"{filename} step {spec['step']}: never names {needed!r}, part of "
                    f"the plan stage's own author-written generation precondition"
                )
        if "withdraw" not in block.lower():
            violations.append(
                f"{filename} step {spec['step']}: never states the consequence of a "
                f"stale TEST_RESULTS.md -- withdrawal and quarantine, not a warning"
            )
        # Ledger `I22`: a plan driver that regenerates the registry owes the
        # plan document's generated checkpoint table too. `/milestone-plan`
        # step 3 calls that table "never hand-edited"; nothing detects a
        # stale one, so a driver that regenerates the registry without
        # saying to re-embed publishes a document the registry contradicts.
        if "generate_registry" in block and "render_registry_markdown" not in block:
            violations.append(
                f"{filename} step {spec['step']}: regenerates the registry "
                f"(`generate_registry`) but never names "
                f"`render_registry_markdown`, so the plan document's own "
                f"generated checkpoint table is left stating the previous "
                f"revision's checkpoints"
            )
        return violations
    if "implementation_revision: <N>" not in block:
        violations.append(
            f"{filename} step {spec['step']}: never names the "
            f"`implementation_revision: <N>` line IMPLEMENTATION_SUMMARY.md must state"
        )
    if "assert_stage_completeness" not in block:
        violations.append(
            f"{filename} step {spec['step']}: never names assert_stage_completeness, "
            f"the check that enforces that line"
        )
    if "withdraw" not in block.lower():
        violations.append(
            f"{filename} step {spec['step']}: never states the consequence of a stale "
            f"line -- withdrawal and quarantine, not a warning"
        )
    if spec["review_request_contract"] == "explicit":
        if "review_content_id: <hex>" not in block:
            violations.append(
                f"{filename} step {spec['step']}: never names the "
                f"`review_content_id: <hex>` line REVIEW_REQUEST.md must state for "
                f"this round"
            )
        if "assert_review_request_states_review_content_id" not in block:
            violations.append(
                f"{filename} step {spec['step']}: never names "
                f"assert_review_request_states_review_content_id, the check that "
                f"enforces that line"
            )
    else:
        if "REVIEW_REQUEST.md` per" not in block:
            violations.append(
                f"{filename} step {spec['step']}: never routes REVIEW_REQUEST.md "
                f"authoring through docs/ai-workflow/REVIEW_PROTOCOL.md"
            )
        if "REVIEW_PROTOCOL.md" not in block:
            violations.append(
                f"{filename} step {spec['step']}: names no protocol document for the "
                f"REVIEW_REQUEST.md contract"
            )
    if spec["states_revision_behaviour"]:
        if "unchanged*" not in block:
            violations.append(
                f"{filename} step {spec['step']}: never says the counter stays "
                f"*unchanged* for a `same_content` round"
            )
        if '`"ordinary"`' not in block or '`"same_content"`' not in block:
            violations.append(
                f"{filename} step {spec['step']}: does not name both generation "
                f"outcomes when stating what the counter does"
            )
    return violations


class TestGenerationCommandsNameTheCompleteAuthorInputSet(unittest.TestCase):
    """Salvage audit `I3`/`I4`/`O4` (repair `R5`), extended by the
    workflow system audit's convergence pass 11 (ledger `I19`).
    `finalize_bundle_generation` hard-requires two author-written marker
    lines and *withdraws* the bundle when either is missing or stale --
    `assert_test_results_consistent_with_plan_review_request`'s
    `stage: plan (revision N)`/`head: <sha>` pair at the plan stage, and
    `assert_stage_completeness`'s `implementation_revision: <N>` line at
    the implementation/post-fix stages. A third, earlier precondition,
    `assert_review_request_states_review_content_id`, runs inside
    `--write-manifest` and *refuses* the generation outright. None of the
    commands that drive a generation named them, so a run following a
    command exactly published nothing and quarantined `current/`.
    Separately (`O4`), two commands still instructed authoring
    `<bundle_dir>/PLAN.md`, which `WFR-67` made a generator-derived
    artifact.

    `R5`'s original pass covered four commands, and the class docstring
    called those four "the commands that drive a generation". The census
    above proves that population is eight, and pass 11 closed the one
    genuine contract gap the miscount hid
    (`/apply-functional-review`'s bounded-fix branch, ledger `I19`).

    These are text-conformance assertions over the command files
    themselves -- the same mechanism this suite already uses for every
    other command-vs-Python claim. The behavioural half (that a bundle
    missing either marker really is withdrawn, and that a stale
    `review_content_id` refuses first) is proven end to end by
    `workflow_acceptance_matrix_test.py` rows A4, B6, C9 and C10."""

    def test_milestone_plan_step6_names_test_results_and_its_marker_lines(self):
        step6 = _extract_numbered_steps(_command_text("milestone-plan.md"))["6"]
        self.assertIn("TEST_RESULTS.md", step6)
        self.assertIn("stage: plan (revision N)", step6)
        self.assertIn("head: <sha>", step6)
        self.assertIn("assert_test_results_consistent_with_plan_review_request", step6)

    def test_milestone_plan_step6_no_longer_instructs_authoring_plan_md(self):
        step6 = _extract_numbered_steps(_command_text("milestone-plan.md"))["6"]
        self.assertNotIn("write/refresh `<bundle_dir>/PLAN.md`", step6)
        self.assertIn("WFR-67", step6)

    def test_apply_plan_review_step5_names_test_results_and_its_marker_lines(self):
        step5 = _extract_numbered_steps(_command_text("apply-plan-review.md"))["5"]
        self.assertIn("TEST_RESULTS.md", step5)
        self.assertIn("stage: plan (revision N)", step5)
        self.assertIn("head: <sha>", step5)

    def test_apply_plan_review_step3_routes_edits_to_the_authoritative_plan(self):
        step3 = _extract_numbered_steps(_command_text("apply-plan-review.md"))["3"]
        self.assertIn("never to", step3)
        self.assertIn("`<bundle_dir>/PLAN.md`, which since `WFR-67`", step3)
        self.assertIn("WFR-67", step3)

    def test_milestone_implement_step4_names_the_implementation_revision_line(self):
        step4 = _extract_numbered_steps(_command_text("milestone-implement.md"))["4"]
        self.assertIn("implementation_revision: <N>", step4)
        self.assertIn("assert_stage_completeness", step4)

    def test_apply_implementation_review_step7_names_the_revision_refresh(self):
        step7 = _extract_numbered_steps(_command_text("apply-implementation-review.md"))["7"]
        self.assertIn("implementation_revision: <N>", step7)
        self.assertIn("assert_stage_completeness", step7)
        # The same_content outcome deliberately does not advance it.
        self.assertIn("unchanged*", step7)

    def test_prepare_functional_review_step3a_handles_an_unchanged_checklist(self):
        """Salvage audit `I7`: a round that legitimately needs no
        checklist change still needs its own round-scoped evidence, and
        a plain `git commit -- <path>` on an unchanged file fails."""
        text = _command_text("prepare-functional-review.md")
        self.assertIn("Unchanged checklist, new round", text)
        self.assertIn("--allow-empty", text)
        self.assertIn("nothing to commit, working tree clean", text)
        # The consequence named is now a live one: ledger `I10` retired
        # `/accept-scoped-remediation`, whose permanently-refusing guard
        # this branch originally cited.
        self.assertNotIn("accept-scoped-remediation", text)
        self.assertIn("/review-functional", text)
        # And the exception is scoped: no other command may use it.
        for filename in ("milestone-implement.md", "apply-implementation-review.md",
                         "apply-functional-review.md", "approve-review.md",
                         "accept-milestone.md",
                         "recover-implementation-provenance.md", "milestone-plan.md"):
            self.assertNotIn("--allow-empty", _command_text(filename), filename)

    def test_milestone_plan_step3_names_the_intent_to_add_staging_step(self):
        """Salvage audit `B6`: `workflow_state.py` twice documents
        "`/milestone-plan`'s own staging step" (in
        `DirtyIndexBeforeStagingError` and
        `stage_plan_approval_commit_paths`), but no such step existed, so a
        freshly created work item's four untracked plan-stage files made
        `resolve_plan_stage_metadata` refuse before any bundle content was
        written."""
        step3 = _extract_numbered_steps(_command_text("milestone-plan.md"))["3"]
        self.assertIn("git add -N", step3)
        self.assertIn("intent-to-add", step3)
        self.assertIn("resolve_plan_stage_metadata", step3)
        self.assertIn("InvalidPlanStageMetadataPathError", step3)
        self.assertIn("stage_plan_approval_commit_paths", step3)

    def test_apply_plan_review_step5_points_at_the_same_staging_step(self):
        step5 = _extract_numbered_steps(_command_text("apply-plan-review.md"))["5"]
        self.assertIn("add -N", step5)
        self.assertIn("resolve_plan_stage_metadata", step5)

    def test_milestone_plan_step3_passes_work_item_type_to_the_generator(self):
        step3 = _extract_numbered_steps(_command_text("milestone-plan.md"))["3"]
        self.assertIn("generate_artifacts_declarations", step3)
        self.assertIn("work_item_type=", step3)
        self.assertIn("implementation_stage", step3)

    # --- the census itself (convergence pass 11, ledger `I19`) ---

    def test_the_declared_census_is_exactly_the_commands_that_run_the_generator(self):
        """Mechanical, not hand-maintained: the population is discovered
        from the command corpus and compared against the declared literal.
        A ninth driver, a renamed file, or a driver whose imperative run
        instruction is deleted all fail here."""
        self.assertEqual(
            _commands_matching(_GENERATOR_RUN_RE),
            set(_GENERATION_DRIVING_COMMANDS),
        )

    def test_every_declared_stage_matches_the_commands_own_invocation_line(self):
        """The census's stage column is checked against the file, not
        trusted (ledger `O29`). Since the moving-counter obligation is
        derived from this column, a driver declared `plan` while its own
        invocation line runs `post-fix` would be exempted by a lie; here
        the declaration is only ever a restatement of what the command
        actually says."""
        for filename, declared in sorted(_GENERATION_DRIVING_COMMANDS.items()):
            with self.subTest(filename=filename):
                self.assertEqual(
                    _declared_generation_stages(_command_text(filename)), declared,
                )

    def test_every_driver_declares_an_authoring_contract(self):
        """The property ledger row `O29` exists to guarantee. Every stage
        this generator accepts has author-written preconditions that fail
        the generation when unmet, so every driver owes a declared
        discipline and nothing declares one it is not a driver for.
        Neither side is hand-maintained against the other: the left is the
        mechanically discovered census."""
        self.assertEqual(
            set(_GENERATION_DRIVING_COMMANDS), set(_GENERATION_AUTHORING_CONTRACTS),
        )

    def test_the_declared_contract_kind_is_admissible_for_the_declared_stage(self):
        """The derivation is discriminating, not vacuous: the marker-line
        discipline a driver declares has to be one its own stage can owe.
        A `post-fix` driver claiming the plan stage's contract -- or the
        reverse -- fails here rather than being checked against the wrong
        marker lines."""
        for filename, spec in sorted(_GENERATION_AUTHORING_CONTRACTS.items()):
            with self.subTest(filename=filename):
                stages = _declared_generation_stages(_command_text(filename))
                self.assertIn(spec["kind"], _admissible_contract_kinds(stages))
        # And the two disciplines really do partition the five in-gate
        # drivers by stage, so the admissibility rule is doing work.
        by_kind: "dict[str, set[str]]" = {}
        for filename, spec in _GENERATION_AUTHORING_CONTRACTS.items():
            by_kind.setdefault(spec["kind"], set()).add(filename)
        self.assertEqual(
            by_kind["plan-marker-step"], {"milestone-plan.md", "apply-plan-review.md"},
        )
        self.assertEqual(
            by_kind["moving-counter-step"],
            {"milestone-implement.md", "apply-implementation-review.md",
             "apply-functional-review.md"},
        )

    def test_the_moving_counter_derivation_still_separates_the_stages(self):
        """`_carries_moving_counter_obligation` itself, asserted directly:
        the two plan drivers are outside it, the three in-gate
        implementation/post-fix drivers are inside it, and both
        placeholder-stage drivers are inside it because a placeholder can
        resolve to a moving stage at run time."""
        derived = {
            filename for filename in _GENERATION_DRIVING_COMMANDS
            if _carries_moving_counter_obligation(
                _declared_generation_stages(_command_text(filename))
            )
        }
        self.assertEqual(
            set(_GENERATION_DRIVING_COMMANDS) - derived,
            {"milestone-plan.md", "apply-plan-review.md"},
        )

    def test_every_command_mentioning_the_generator_is_classified(self):
        """The two populations must partition the mentions, so a command
        that names the script in a way the run regex does not match cannot
        simply fall outside the census unnoticed -- it lands in neither
        set and fails here."""
        mentions = _commands_matching(_GENERATOR_MENTION_RE)
        classified = set(_GENERATION_DRIVING_COMMANDS) | set(_GENERATOR_MENTION_ONLY_COMMANDS)
        self.assertEqual(mentions - classified, set(), "unclassified generator mention")
        self.assertEqual(classified - mentions, set(), "classified command names no generator")
        self.assertEqual(
            set(_GENERATION_DRIVING_COMMANDS) & set(_GENERATOR_MENTION_ONLY_COMMANDS), set(),
        )

    def test_no_mention_only_command_instructs_its_own_generation(self):
        """The negative half of the partition, asserted directly rather
        than inferred from the regex that produced it: none of the three
        report-only commands tells *itself* to run the generator."""
        for filename in _GENERATOR_MENTION_ONLY_COMMANDS:
            with self.subTest(filename=filename):
                self.assertIsNone(_GENERATOR_RUN_RE.search(_command_text(filename)))

    def test_every_generation_driving_command_states_its_full_authoring_contract(self):
        """`_generation_authoring_violations` in full, over every driver:
        `<bundle_dir>` resolution for all eight, and -- for the three whose
        `implementation_revision` can move between rounds -- both marker
        lines, the assertions that enforce them, and the consequence."""
        for filename in sorted(_GENERATION_DRIVING_COMMANDS):
            with self.subTest(filename=filename):
                self.assertEqual(
                    _generation_authoring_violations(filename, _command_text(filename)), [],
                )

    def test_apply_functional_review_names_the_bounded_fix_generation_contract(self):
        """Ledger `I19` head-on. The bounded-code-change branch drives a
        `post-fix` generation and, before this repair, named none of its
        author-written preconditions: no `<bundle_dir>`, no
        `review_content_id`, no `implementation_revision`. A run following
        it exactly died in `ReviewContentIdMismatchError`, then -- once
        only that was corrected -- had its bundle withdrawn and
        `current/` quarantined by `assert_stage_completeness`."""
        text = _command_text("apply-functional-review.md")
        self.assertIn("`workflow_fingerprint.resolve_bundle_dir`", text)
        step4 = _extract_numbered_steps(text)["4"]
        self.assertIn("`<bundle_dir>/IMPLEMENTATION_SUMMARY.md`", step4)
        self.assertIn("implementation_revision: <N>", step4)
        self.assertIn("assert_stage_completeness", step4)
        self.assertIn("`<bundle_dir>/REVIEW_REQUEST.md`", step4)
        self.assertIn("review_content_id: <hex>", step4)
        self.assertIn("assert_review_request_states_review_content_id", step4)
        # Withdrawal for the revision line, refusal for the digest -- two
        # different failure modes, both stated, neither a silent fix-up.
        self.assertIn("*withdraws* the bundle, quarantining `current/`", step4)
        self.assertIn("*refuses*", step4)
        self.assertIn("silently fixed up on the author's behalf", step4)
        # And the resolution is the compatibility form, not the plan
        # stage's scoped-by-construction one (convergence repair `I1`).
        self.assertIn("`resolve_bundle_dir(repo_root, work_item_id)`", text)
        self.assertIn('never\nthe plan stage\'s `stage="plan"` form', text)

    def test_the_pinned_round_driver_states_why_it_refreshes_nothing(self):
        """`/recover-implementation-provenance` drives a generation too,
        but its round is pinned by contract: `apply_implementation_
        provenance_recovery` never advances `implementation_revision`, and
        the interval it validates is excluded-only, so the implementation-
        stage `review_content_id` recomputes to the value the bundle
        already states. Both author-written lines therefore stay valid
        untouched -- which is a real precondition claim, and is stated
        rather than left as folklore."""
        step6 = _extract_numbered_steps(
            _command_text("recover-implementation-provenance.md")
        )["6"]
        self.assertIn("recovery does not change what kind of round", step6)
        step5 = _extract_numbered_steps(
            _command_text("recover-implementation-provenance.md")
        )["5"]
        self.assertIn("never a new revision", step5)

    def test_the_ad_hoc_and_bootstrap_drivers_route_authoring_through_the_protocol(self):
        """The two drivers outside the milestone gates author their bundle
        inputs `per docs/ai-workflow/REVIEW_PROTOCOL.md`, whose
        "Author-written files" section states both marker-line
        requirements."""
        for filename in ("prepare-review.md", "bootstrap-workflow-v2.md"):
            with self.subTest(filename=filename):
                text = _command_text(filename)
                self.assertIn("REVIEW_REQUEST.md", text)
                self.assertIn("docs/ai-workflow/REVIEW_PROTOCOL.md", text)
        protocol = (_repo_root() / "docs" / "ai-workflow" / "REVIEW_PROTOCOL.md").read_text()
        self.assertIn("Must state `review_content_id: <hex>` as a plain labelled line", protocol)
        self.assertIn("`implementation_revision: <N>` matching the work item", protocol)


class TestRevertingTheAuthoringInstructionsFailsConformance(unittest.TestCase):
    """The census above is only worth as much as its checker, and a
    conformance assertion nobody has ever watched fail is a claim rather
    than evidence -- exactly how ledger `I19` reached a fresh independent
    review with this suite green.

    So the repair's own two hunks are reverted mechanically, in memory,
    out of the *current* file (never a hardcoded pre-repair copy that
    would rot the moment the file changes again), and
    `_generation_authoring_violations` is run against the result. The
    mutation is asserted to have actually changed something first, so a
    hunk that moves or is reworded can never degrade this regression into
    a no-op that "passes"."""

    @staticmethod
    def _revert_the_authoring_instructions(text: str) -> str:
        """`/apply-functional-review` as it stood before convergence pass
        11: `<feedback_dir>` resolved alone, and the bounded branch going
        straight from its durability commit to the generator run."""
        preamble = (
            "`<bundle_dir>`/`<feedback_dir>` below resolve per\n"
            "`docs/ai-workflow/REVIEW_PROTOCOL.md`'s \"Bundle location\"\n"
            "(`workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir`)."
        )
        if preamble not in text:
            raise AssertionError(
                "the <bundle_dir> preamble hunk this regression reverts is no longer "
                "present verbatim -- update this mutation rather than letting it no-op"
            )
        start = text.index(preamble)
        end = text.index("\n\n", start)
        text = text[:start] + (
            "`<feedback_dir>` below resolves per\n"
            "`docs/ai-workflow/REVIEW_PROTOCOL.md`'s \"Bundle location\"\n"
            "(`workflow_fingerprint.resolve_feedback_dir`)."
        ) + text[end:]

        pattern = re.compile(
            r"     round\.\n     \*\*Then refresh .*?\n     \[work_item_id\]`\.\n",
            re.DOTALL,
        )
        mutated, count = pattern.subn(
            "     round. Then run `./scripts/prepare-ai-review.sh <base-sha> post-fix\n"
            "     [work_item_id]`.\n",
            text,
        )
        if count != 1:
            raise AssertionError(
                f"the bounded-branch authoring hunk this regression reverts matched "
                f"{count} times, expected exactly 1 -- update this mutation rather "
                f"than letting it no-op"
            )
        return mutated

    def test_the_mutation_actually_changes_the_command(self):
        original = _command_text("apply-functional-review.md")
        reverted = self._revert_the_authoring_instructions(original)
        self.assertNotEqual(original, reverted)
        self.assertLess(len(reverted), len(original))
        # And it is a *targeted* revert: everything else the bounded
        # branch says survives it.
        for kept in ("resolve_bundle_generation_outcome", "record_bundle_generation",
                     "assert_bundle_not_rejected", "Workflow-Bundle-Generation-Record",
                     "mark_technical_approval_stale"):
            self.assertIn(kept, reverted, kept)

    def test_the_reverted_command_fails_the_generation_authoring_conformance(self):
        reverted = self._revert_the_authoring_instructions(
            _command_text("apply-functional-review.md")
        )
        violations = _generation_authoring_violations("apply-functional-review.md", reverted)
        joined = "\n".join(violations)
        self.assertNotEqual(violations, [], "the reverted command must not conform")
        # Every precondition ledger `I19` found missing is named, so this
        # regression fails for the right reasons rather than merely
        # failing.
        self.assertIn("resolve_bundle_dir", joined)
        self.assertIn("implementation_revision: <N>", joined)
        self.assertIn("assert_stage_completeness", joined)
        self.assertIn("review_content_id: <hex>", joined)
        self.assertIn("assert_review_request_states_review_content_id", joined)
        self.assertIn("withdrawal and quarantine", joined)

    def test_removing_the_registry_table_re_embed_fails_conformance(self):
        """Ledger `I22`'s control arm. `/apply-plan-review` step 5 gained
        the `render_registry_markdown` re-embed; strip it back out of the
        live file, in memory, and the checker must name the loss. Before
        the repair the step regenerated the registry and said nothing
        about the plan document's own generated table, which is how the
        acceptance matrix's own helper -- which called it all along --
        kept every plan-revision row green over the gap."""
        original = _command_text("apply-plan-review.md")
        self.assertIn("render_registry_markdown", original)
        stripped = original.replace("render_registry_markdown", "some_other_helper")
        self.assertNotEqual(stripped, original)
        violations = _generation_authoring_violations("apply-plan-review.md", stripped)
        self.assertIn("render_registry_markdown", "\n".join(violations))
        # And the live file conforms, with the same checker.
        self.assertEqual(
            _generation_authoring_violations("apply-plan-review.md", original), [],
        )

    def test_removing_the_canonical_computation_pointer_fails_conformance(self):
        """Convergence pass 12, ledger `O31`'s own control arm. The five
        in-gate drivers each gained one reference to
        `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Computing
        `review_content_id`". Strip that reference back out of the *live*
        file, in memory, and the checker must name the loss -- for every
        one of the five, so the check cannot be silently satisfied by some
        other sentence that happens to mention the protocol. The pattern
        tolerates each driver's own line wrapping rather than hardcoding
        one of them, so a reflow can never degrade this arm into a no-op."""
        pointer = re.compile(r'"Computing\s+`review_content_id`"')
        checked = 0
        for filename in sorted(_GENERATION_AUTHORING_CONTRACTS):
            spec = _GENERATION_AUTHORING_CONTRACTS[filename]
            if spec["kind"] not in ("plan-marker-step", "moving-counter-step"):
                continue
            with self.subTest(filename=filename):
                original = _command_text(filename)
                stripped, count = pointer.subn('"elsewhere"', original)
                self.assertGreaterEqual(
                    count, 1,
                    f"{filename} no longer carries the pointer this arm removes -- "
                    f"update the mutation rather than letting it no-op",
                )
                violations = _generation_authoring_violations(filename, stripped)
                self.assertIn(
                    '"Computing `review_content_id`"', "\n".join(violations),
                    f"{filename}: stripping the pointer produced {violations!r}",
                )
                checked += 1
        # All five in-gate drivers, never a subset that happens to be
        # covered while another silently drops the reference.
        self.assertEqual(checked, 5)

    def test_the_live_command_conforms(self):
        """The control: the same checker, the same command, unreverted."""
        self.assertEqual(
            _generation_authoring_violations(
                "apply-functional-review.md", _command_text("apply-functional-review.md"),
            ),
            [],
        )


class TestASyntheticGenerationDriverCannotEscapeTheAuthoringContract(unittest.TestCase):
    """Convergence pass 12's synthetic-driver control arm (ledger `O29`).

    Before the repair, `_MOVING_COUNTER_DRIVERS` was a *second*
    hand-maintained command-name list sitting behind the mechanically
    discovered census. A new implementation/post-fix driver that entered
    the census -- which the discovery test forces -- still escaped every
    marker-line obligation simply by not appearing in that second list,
    and `_generation_authoring_violations` returned `[]` for it. That is
    exactly the class of drift the census itself was introduced to end,
    reproduced one level down.

    These arms plant synthetic command *text* (never a file in
    `.claude/commands/`, which would perturb the live corpus every other
    test reads) and run the real checker over it."""

    # A driver that names `resolve_bundle_dir` -- the one obligation the
    # pre-repair checker applied to every driver -- and nothing else.
    SYNTHETIC_POST_FIX = (
        "---\n"
        "description: A later driver for some bounded post-fix round.\n"
        "---\n\n"
        "`<bundle_dir>` resolves per `workflow_fingerprint.resolve_bundle_dir`.\n\n"
        "1. Do the bounded fix and commit it.\n"
        "2. Then run `./scripts/prepare-ai-review.sh <base-sha> post-fix [work_item_id]`.\n"
        "3. Report readiness and stop.\n"
    )

    def test_the_synthetic_driver_is_discovered_as_a_generation_driver(self):
        """The premise: it really does read as a driver, at a real moving
        stage. Without this the arms below would prove nothing."""
        self.assertTrue(_GENERATOR_RUN_RE.search(self.SYNTHETIC_POST_FIX))
        stages = _declared_generation_stages(self.SYNTHETIC_POST_FIX)
        self.assertEqual(stages, {"post-fix"})
        self.assertTrue(_carries_moving_counter_obligation(stages))

    def test_a_synthetic_driver_with_no_declared_contract_fails_conformance(self):
        """The repair itself: silence is a violation, not an exemption."""
        violations = _generation_authoring_violations(
            "synthetic-post-fix-driver.md", self.SYNTHETIC_POST_FIX,
        )
        self.assertNotEqual(violations, [])
        joined = "\n".join(violations)
        self.assertIn("_GENERATION_AUTHORING_CONTRACTS", joined)
        self.assertIn("implementation_revision: <N>", joined)
        self.assertIn("review_content_id: <hex>", joined)

    def test_a_synthetic_driver_declaring_the_step_contract_is_held_to_it(self):
        """Declaring `moving-counter-step` does not buy silence either:
        the named step block is then checked for every marker line, so a
        driver cannot conform by pointing at a step that says nothing."""
        contracts = dict(_GENERATION_AUTHORING_CONTRACTS)
        contracts["synthetic-post-fix-driver.md"] = {
            "kind": "moving-counter-step",
            "step": "2",
            "review_request_contract": "explicit",
            "states_revision_behaviour": True,
        }
        violations = _generation_authoring_violations(
            "synthetic-post-fix-driver.md", self.SYNTHETIC_POST_FIX, contracts,
        )
        joined = "\n".join(violations)
        self.assertIn("assert_stage_completeness", joined)
        self.assertIn("assert_review_request_states_review_content_id", joined)
        self.assertIn("withdrawal and quarantine", joined)

    def test_a_synthetic_driver_cannot_buy_the_pinned_round_exemption_unearned(self):
        """The exemptions carry checked evidence. Claiming the pinned
        round without the command ever saying its revision cannot move is
        a violation naming the missing statement."""
        contracts = dict(_GENERATION_AUTHORING_CONTRACTS)
        contracts["synthetic-post-fix-driver.md"] = {"kind": "pinned-round"}
        violations = _generation_authoring_violations(
            "synthetic-post-fix-driver.md", self.SYNTHETIC_POST_FIX, contracts,
        )
        joined = "\n".join(violations)
        self.assertIn("pinned-round exemption", joined)
        self.assertIn("never a new revision", joined)

    def test_a_synthetic_driver_cannot_buy_the_protocol_exemption_unearned(self):
        contracts = dict(_GENERATION_AUTHORING_CONTRACTS)
        contracts["synthetic-post-fix-driver.md"] = {"kind": "protocol-reference"}
        violations = _generation_authoring_violations(
            "synthetic-post-fix-driver.md", self.SYNTHETIC_POST_FIX, contracts,
        )
        joined = "\n".join(violations)
        self.assertIn("REVIEW_PROTOCOL.md", joined)

    def test_a_synthetic_plan_stage_driver_owes_the_plan_marker_contract(self):
        """The plan side of the same hole. Before the repair the plan
        stage's own author-written precondition
        (`assert_test_results_consistent_with_plan_review_request`'s two
        marker lines) was asserted by two hand-written tests naming the
        two live plan drivers by filename, so a *third* plan driver was
        checked by nothing at all -- ledger row `O29` one stage over."""
        plan_driver = self.SYNTHETIC_POST_FIX.replace(
            "<base-sha> post-fix [work_item_id]", "<base-sha> plan <work_item_id>",
        )
        self.assertEqual(_declared_generation_stages(plan_driver), {"plan"})
        self.assertFalse(_carries_moving_counter_obligation({"plan"}))
        violations = _generation_authoring_violations(
            "synthetic-plan-driver.md", plan_driver,
        )
        self.assertNotEqual(violations, [])
        self.assertIn("_GENERATION_AUTHORING_CONTRACTS", "\n".join(violations))

    def test_a_synthetic_plan_driver_declaring_the_step_contract_is_held_to_it(self):
        plan_driver = self.SYNTHETIC_POST_FIX.replace(
            "<base-sha> post-fix [work_item_id]", "<base-sha> plan <work_item_id>",
        )
        contracts = dict(_GENERATION_AUTHORING_CONTRACTS)
        contracts["synthetic-plan-driver.md"] = {"kind": "plan-marker-step", "step": "2"}
        joined = "\n".join(_generation_authoring_violations(
            "synthetic-plan-driver.md", plan_driver, contracts,
        ))
        self.assertIn("stage: plan (revision N)", joined)
        self.assertIn("head: <sha>", joined)
        self.assertIn("assert_test_results_consistent_with_plan_review_request", joined)
        self.assertIn("withdrawal and quarantine", joined)

    def test_a_driver_cannot_declare_a_contract_its_stage_does_not_owe(self):
        """Mis-declaration is refused rather than silently checking the
        wrong marker lines -- otherwise a `post-fix` driver could conform
        by satisfying the plan stage's contract, and vice versa."""
        plan_driver = self.SYNTHETIC_POST_FIX.replace(
            "<base-sha> post-fix [work_item_id]", "<base-sha> plan <work_item_id>",
        )
        contracts = dict(_GENERATION_AUTHORING_CONTRACTS)
        contracts["synthetic-plan-driver.md"] = {
            "kind": "moving-counter-step", "step": "2",
            "review_request_contract": "explicit", "states_revision_behaviour": True,
        }
        joined = "\n".join(_generation_authoring_violations(
            "synthetic-plan-driver.md", plan_driver, contracts,
        ))
        self.assertIn("not admissible", joined)

        contracts = dict(_GENERATION_AUTHORING_CONTRACTS)
        contracts["synthetic-post-fix-driver.md"] = {"kind": "plan-marker-step", "step": "2"}
        joined = "\n".join(_generation_authoring_violations(
            "synthetic-post-fix-driver.md", self.SYNTHETIC_POST_FIX, contracts,
        ))
        self.assertIn("not admissible", joined)

    def test_a_synthetic_driver_at_a_placeholder_stage_still_owes_the_contract(self):
        """`<stage>`/`<that stage>` resolve at run time and can resolve to
        a moving stage, so the derivation is default-deny for them too."""
        placeholder_driver = self.SYNTHETIC_POST_FIX.replace(
            "<base-sha> post-fix [work_item_id]", "<base-sha> <stage> [work_item_id]",
        )
        self.assertEqual(_declared_generation_stages(placeholder_driver), {"<stage>"})
        self.assertNotEqual(
            _generation_authoring_violations(
                "synthetic-placeholder-driver.md", placeholder_driver,
            ),
            [],
        )


# ---------------------------------------------------------------------------
# The acceptance matrix's own helpers, audited against the commands they
# stand in for (workflow system audit, convergence pass 12, ledger `C12`).
#
# `workflow_acceptance_matrix_test.py`'s `Item` class exists to drive each
# command "exactly the way the owning `.claude/commands/*.md` file says to
# drive it". When a helper calls a production entry point the owning command
# never names, one of two things is true and both are bad:
#
# - the helper does *more* than the command, so an incomplete command looks
#   complete (ledger `I19`: the helper refreshed both marker lines for a
#   bounded functional fix whose documentation named neither; ledger `I22`:
#   the helper re-embedded the plan document's registry table and the command
#   never said to);
# - the helper is *stricter* than the command, so a supported branch of the
#   command is unreachable in the suite (ledger `O35`:
#   `assert_feedback_matches_bundle` one step ahead of the basis decision made
#   `USER_OVERRIDE` unexecutable).
#
# Both were found by running this audit by hand once. Running it here makes it
# a standing property instead of a discovery.
#
# The owner mapping is keyed on `Item`'s own existing section comments, so a
# new section cannot be added without being classified: the discovered set and
# the declared set must be equal.
# ---------------------------------------------------------------------------

_MATRIX_SECTION_RE = re.compile(r"^    # -{4,} (.+?) -{4,}$", re.MULTILINE)

_MATRIX_HELPER_SECTION_OWNERS = {
    # Sections that stand in for no command at all: repository plumbing, and
    # the reviewer's/user's own inputs, which no command authors.
    "plumbing": (),
    "seeding": (),
    "review feedback": (),
    # Sections that drive one or more commands.
    "/milestone-plan": ("milestone-plan.md",),
    "plan-stage bundle": ("milestone-plan.md",),
    "/review-plan + /record-manual-plan-review": (
        "review-plan.md", "record-manual-plan-review.md",
    ),
    "/approve-review plan": ("approve-review.md",),
    "/apply-plan-review": ("apply-plan-review.md",),
    # workflow-2.6.0 (D-Plan-Review-Bundle-Binding): the only sanctioned
    # route from `IMPLEMENTING` back into plan review.
    "/request-plan-amendment + amended /milestone-plan": (
        "request-plan-amendment.md", "milestone-plan.md",
    ),
    "/milestone-implement": ("milestone-implement.md",),
    "/milestone-implement wrap-up": ("milestone-implement.md",),
    "bundle generation": (
        "milestone-implement.md", "apply-implementation-review.md",
        "apply-functional-review.md",
    ),
    "/apply-implementation-review": ("apply-implementation-review.md",),
    "/recover-implementation-provenance": ("recover-implementation-provenance.md",),
    "/approve-review implementation": ("approve-review.md",),
    "functional review": ("prepare-functional-review.md", "apply-functional-review.md"),
    "/accept-milestone": ("accept-milestone.md",),
}

#: Calls an owning command legitimately does not name by symbol, each with
#: the reason. Deliberately tiny: an entry here is a claim that the command
#: discharges the obligation some other way, and each is checked.
_MATRIX_HELPER_DISCHARGED_BY_REFERENCE = {
    "compute_review_content_id_plan_stage_for_work_item": (
        "the plan-stage `review_content_id` recipe: commands reference "
        "`REVIEW_PROTOCOL.md`'s \"Computing `review_content_id`\" rather than "
        "restating the entry point (ledger `O31`), so the symbol lives in the "
        "protocol, not in the command"
    ),
    "assert_feedback_matches_bundle": (
        "`/approve-review` step 2 deliberately does not assert here -- a "
        "missing or mismatched binding field is \"not fatal to reading the "
        "file (an `EXTERNAL_APPROVE` basis simply becomes unreachable, per "
        "step 3), but report the mismatch naming both values\". The helper "
        "observes and reports it and lets `resolve_approval_basis` decide, "
        "which is what makes `USER_OVERRIDE` reachable at all (ledger `O35`)"
    ),
}


def _matrix_source() -> str:
    return (_repo_root() / "scripts" / "workflow_acceptance_matrix_test.py").read_text()


def _matrix_item_helper_calls() -> "dict[str, set[str]]":
    """`{section title: production entry points the section's helpers call}`,
    for `Item` only. Sections come from the class's own section comments;
    calls come from the AST, so a rename or a reflow cannot hide one."""
    source = _matrix_source()
    tree = ast.parse(source)
    item = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Item"
    )
    boundaries = [
        (m.start(), m.group(1)) for m in _MATRIX_SECTION_RE.finditer(source)
        if item.lineno <= source.count("\n", 0, m.start()) + 1 <= (item.end_lineno or 10 ** 9)
    ]
    lines = source.splitlines(keepends=True)
    offsets = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line)

    def section_for(lineno: int) -> "str | None":
        start = offsets[lineno - 1]
        current = None
        for pos, title in boundaries:
            if pos < start:
                current = title
            else:
                break
        return current

    calls: "dict[str, set[str]]" = {title: set() for _, title in boundaries}
    for fn in item.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        title = section_for(fn.lineno)
        if title is None:
            continue
        for node in ast.walk(fn):
            # Only actual invocations: an exception class named in an
            # `except` clause is a *reaction* to a call, not a step the
            # command instructs, and pulling it in would put class names
            # into a table about behaviour.
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in ("ws", "fingerprint")
                and not func.attr.isupper()
                and not func.attr.startswith("_")
            ):
                calls[title].add(func.attr)
    return calls


def _matrix_helper_gaps(calls: "set[str]", owner_text: str) -> "set[str]":
    """The calls in `calls` that `owner_text` neither names nor has a
    recorded discharge for -- the checker as a pure function, so the
    control arm below can run it against a deliberately mutated command
    text instead of only against the live one."""
    return {
        call for call in calls
        if call not in owner_text
        and call not in _MATRIX_HELPER_DISCHARGED_BY_REFERENCE
    }


class TestMatrixHelpersDoNotOutrunTheirCommands(unittest.TestCase):
    """Ledger `C12`. See the census note above for why this exists."""

    def test_the_audit_catches_the_two_gaps_it_was_written_from(self):
        """The control arm. Strip each repaired mention back out of the
        *live* command text, in memory, and the checker must report the
        call the helper performs -- for `I22`'s registry-table re-embed and
        `I21`'s technical-approval commit validation alike. Without this,
        the audit above is an assertion nobody has watched fail."""
        sections = _matrix_item_helper_calls()
        for symbol, section, command in (
            ("render_registry_markdown", "/apply-plan-review", "apply-plan-review.md"),
            ("validate_technical_approval_commit", "/approve-review implementation",
             "approve-review.md"),
        ):
            with self.subTest(symbol=symbol):
                calls = sections[section]
                self.assertIn(
                    symbol, calls,
                    f"the helper no longer calls {symbol!r} -- update this arm "
                    f"rather than letting it no-op",
                )
                live = _command_text(command)
                self.assertIn(symbol, live)
                self.assertEqual(_matrix_helper_gaps(calls, live), set())
                stripped = live.replace(symbol, "some_other_helper")
                self.assertEqual(_matrix_helper_gaps(calls, stripped), {symbol})

    def test_every_helper_section_is_classified(self):
        """The partition guard: a new `Item` section cannot slip in
        unaudited, and a declared section that no longer exists cannot rot
        into a dead entry."""
        discovered = set(_matrix_item_helper_calls())
        self.assertEqual(discovered, set(_MATRIX_HELPER_SECTION_OWNERS))

    def test_every_helper_call_is_named_by_an_owning_command(self):
        protocol = (
            _repo_root() / "docs" / "ai-workflow" / "REVIEW_PROTOCOL.md"
        ).read_text()
        for title, calls in sorted(_matrix_item_helper_calls().items()):
            owners = _MATRIX_HELPER_SECTION_OWNERS[title]
            if not owners:
                continue
            owner_text = "\n".join(_command_text(name) for name in owners)
            self.assertEqual(
                _matrix_helper_gaps(calls, owner_text), set(),
                f"{title}: helper calls no owning command names and no "
                f"recorded discharge covers",
            )
            for call in sorted(calls):
                with self.subTest(section=title, call=call):
                    if call in owner_text:
                        continue
                    reason = _MATRIX_HELPER_DISCHARGED_BY_REFERENCE.get(call)
                    self.assertIsNotNone(
                        reason,
                        f"{title}: the helper calls {call!r}, which none of "
                        f"{list(owners)} names -- either the command is missing "
                        f"a step the helper performs (ledger `I19`/`I22`) or the "
                        f"helper is stricter than the command (ledger `O35`). "
                        f"Fix the command, or record the discharge with its "
                        f"reason in _MATRIX_HELPER_DISCHARGED_BY_REFERENCE.",
                    )
                    # A discharge is a claim, so check it.
                    self.assertIn(call, protocol + owner_text)

    def test_the_discharge_list_stays_minimal_and_used(self):
        """Every recorded discharge must still be needed by some section --
        otherwise it is a stale exemption sitting ready to excuse the next
        genuine gap."""
        used = set()
        for title, calls in _matrix_item_helper_calls().items():
            owners = _MATRIX_HELPER_SECTION_OWNERS[title]
            if not owners:
                continue
            owner_text = "\n".join(_command_text(name) for name in owners)
            used |= {c for c in calls if c not in owner_text}
        self.assertEqual(used, set(_MATRIX_HELPER_DISCHARGED_BY_REFERENCE))


class TestGoldenCommandFileHashes(unittest.TestCase):
    """WFR-26's broad half: one golden sha256 per modified command file.
    A change here is not itself a failure of correctness -- it is a
    prompt to re-review whether the change was intended and, if so, to
    update the recorded hash -- exactly the "unchanged since this test
    was written" caveat `WORKFLOW_V2_PLAN.md`'s own missing-test item 90
    asks this suite to document explicitly."""

    def test_every_roster_command_file_matches_its_recorded_hash(self):
        for filename, expected in _GOLDEN_COMMAND_FILE_SHA256.items():
            with self.subTest(filename=filename):
                actual = hashlib.sha256(_command_text(filename).encode()).hexdigest()
                self.assertEqual(
                    actual, expected,
                    f"{filename} content changed since this golden hash was recorded -- "
                    f"if intentional, update _GOLDEN_COMMAND_FILE_SHA256",
                )


class TestReconciliationOutcomeReportingConformance(unittest.TestCase):
    """workflow-2.4.0's round-6 implementation-review fix (`IMPL6-B1`):
    `D-Plan-Amendment-4`'s own closing requirement --

        Reconciliation's outcome (retained / needs-revalidation / dropped,
        by id, including which flips came from the dependency-closure
        pass) is included in `/approve-review plan`'s own output

    -- names an operator-visible report that only a prior round's audit
    caught as unimplemented despite the requirement appearing verbatim in
    the shipped `WORKFLOW_V2_PLAN.md`. This is the "single test going red"
    that finding's own "Architecture and maintainability concerns" section
    says nothing previously bound the design paragraph to the command
    text; this class is that binding, mirroring `TestGoldenCommandFileHashes`'s
    pinned-literal shape rather than trusting prose alone again."""

    def test_approve_review_step_7_instructs_reporting_the_reconciliation_outcome(self):
        text = _command_text("approve-review.md")
        self.assertIn("reconciliation_outcome", text)
        self.assertIn("needs_revalidation_dependency", text)


class TestPlanApprovalCommitTrailerFinalParagraphConformance(unittest.TestCase):
    """`workflow-v2-3-followups` CP1 (`LPR-R3-B01`, round-3 local plan
    review): `approve-review.md` step 6.4's commit instruction must state
    the same "trailers must be the commit message's own final paragraph"
    requirement `milestone-implement.md`/`bootstrap-workflow-v2.md` already
    state verbatim -- without it, a plausible commit-message shape makes
    the approval commit's own trailers unparseable by `git
    interpret-trailers --parse`, `discover_plan_approval_commit`'s exact
    mechanism. No existing test asserted this for any of the three files
    before this checkpoint; this is new coverage for all three, not just
    `approve-review.md`."""

    def test_approve_review_states_the_final_paragraph_requirement(self):
        text = _command_text("approve-review.md")
        self.assertIn(
            "**These\n     two lines must be the commit message's own final paragraph** — after\n"
            "     any `Co-Authored-By:`/`Claude-Session:` lines, never before them",
            text,
        )
        self.assertIn("discover_plan_approval_commit", text)

    def test_milestone_implement_states_the_final_paragraph_requirement(self):
        text = _command_text("milestone-implement.md")
        self.assertIn(
            "**These\n      two lines must be the commit message's final paragraph** -- after\n"
            "      any `Co-Authored-By:`/`Claude-Session:` lines, never before them",
            text,
        )

    def test_bootstrap_workflow_v2_states_the_final_paragraph_requirement(self):
        text = _command_text("bootstrap-workflow-v2.md")
        self.assertIn(
            "**These two lines must be the commit message's final\n   paragraph** — after any `Co-Authored-By:`/`Claude-Session:` lines, never\n"
            "   before them",
            text,
        )

    def test_accept_milestone_states_the_final_paragraph_requirement(self):
        """Baseline-freeze correctness fix: `accept-milestone.md` step 6
        never stated this rule at all -- every real `/accept-milestone`
        completion commit to date (`27f051e`/`fb134ac`/`d271d89`, all
        individually grandfathered in `workflow_state_demo_test.py`'s
        `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`) reproduced
        the same defect class as a result. This closes the gap those three
        grandfathered entries' own comments name explicitly as still open."""
        text = _command_text("accept-milestone.md")
        self.assertIn(
            "**This line must be part of the commit\n   message's own final paragraph** -- after any `Co-Authored-By:`/\n"
            "   `Claude-Session:` lines, never in an earlier paragraph separated from\n"
            "   them by a blank line",
            text,
        )
        self.assertIn("OPUS-R129-001", text)

    def test_prepare_functional_review_states_the_final_paragraph_requirement(self):
        """Baseline-portability correctness fix: `prepare-functional-review.md`
        step 3a never stated this rule at all for its own
        `Workflow-Functional-Checklist`/`Workflow-Work-Item` checklist-
        evidence provenance commit -- the identity step 4 reports to the
        operator and `/review-functional` reads back."""
        text = _command_text("prepare-functional-review.md")
        self.assertIn(
            "**These two lines must be the commit message's own final\n"
            "         paragraph** — after any `Co-Authored-By:`/`Claude-Session:` lines,\n"
            "         never before them",
            text,
        )
        self.assertIn("OPUS-R129-001", text)
        self.assertIn("discover_current_functional_checklist_evidence", text)

    def test_apply_functional_review_states_the_final_paragraph_requirement(self):
        """Baseline-portability correctness fix: `apply-functional-review.md`'s
        bounded-branch post-fix bundle-generation-record commit never stated
        this rule at all."""
        text = _command_text("apply-functional-review.md")
        self.assertIn(
            "**These trailer lines\n"
            "     must be the commit message's own final paragraph** — after any\n"
            "     `Co-Authored-By:`/`Claude-Session:` lines, never before them",
            text,
        )
        self.assertIn("OPUS-R129-001", text)
        self.assertIn("discover_current_bundle_generation_record_commit", text)

    def test_apply_implementation_review_states_the_final_paragraph_requirement(self):
        """Baseline-portability correctness fix: `apply-implementation-review.md`
        step 7's post-fix bundle-generation-record commit never stated this
        rule at all."""
        text = _command_text("apply-implementation-review.md")
        self.assertIn(
            "**These trailer lines must be the commit\n"
            "   message's own final paragraph** — after any `Co-Authored-By:`/\n"
            "   `Claude-Session:` lines, never before them",
            text,
        )
        self.assertIn("OPUS-R129-001", text)
        self.assertIn("discover_current_bundle_generation_record_commit", text)

    def test_recover_implementation_provenance_states_the_final_paragraph_requirement(self):
        """Baseline-portability correctness fix:
        `recover-implementation-provenance.md` step 5's recovery commit
        (the three-trailer `Workflow-Bundle-Generation-Record`/
        `Workflow-Work-Item`/`Workflow-Supersedes` set) never stated this
        rule at all."""
        text = _command_text("recover-implementation-provenance.md")
        self.assertIn(
            "**These three trailer\n"
            "   lines must be the commit message's own final paragraph** — after any\n"
            "   `Co-Authored-By:`/`Claude-Session:` lines, never before them",
            text,
        )
        self.assertIn("OPUS-R129-001", text)
        self.assertIn("discover_current_bundle_generation_record_commit", text)

    def test_milestone_implement_bundle_generation_states_the_final_paragraph_requirement(self):
        """Baseline-tag verification cleanup: a fresh Opus review found
        that `milestone-implement.md` step 4's bundle-generation-record
        commit instruction never stated this rule at all -- unlike step
        1f's own checkpoint-completion commit in the same file, which
        OPUS-R129-001 already covered. Same trailer family
        `apply-implementation-review.md`/`apply-functional-review.md`
        already state it for."""
        text = _command_text("milestone-implement.md")
        self.assertIn(
            "**These two trailer lines must be the commit message's own final\n"
            "     paragraph** -- after any `Co-Authored-By:`/`Claude-Session:` lines,\n"
            "     never before them",
            text,
        )
        self.assertIn("OPUS-R129-001", text)
        self.assertIn("discover_current_bundle_generation_record_commit", text)

    def test_bootstrap_workflow_v2_bundle_generation_states_the_final_paragraph_requirement(self):
        """Baseline-tag verification cleanup: a fresh Opus review found
        that `bootstrap-workflow-v2.md`'s NO_CHECKPOINT terminal-wrap-up
        branch's bundle-generation-record commit instruction (step 3)
        never stated this rule at all -- unlike step 6's own
        checkpoint-completion commit in the same file, which
        OPUS-R129-001 already covered."""
        text = _command_text("bootstrap-workflow-v2.md")
        self.assertIn(
            "**These two trailer lines must be the commit message's own final\n"
            "     paragraph** — after any `Co-Authored-By:`/`Claude-Session:` lines,\n"
            "     never before them",
            text,
        )
        self.assertIn("OPUS-R129-001", text)
        self.assertIn("discover_current_bundle_generation_record_commit", text)

    def test_approve_review_names_the_implementation_stage_post_commit_checks(self):
        """Convergence pass 12, ledger `I21`. The plan stage has step 6a's
        post-commit verification set; the implementation stage had none,
        and `validate_technical_approval_commit` -- implemented,
        documented, unit-tested -- had no production caller anywhere. The
        command must now name both checks, say they apply to the commit
        this invocation just created rather than to discovered history,
        and say what to do when one fails."""
        text = _command_text("approve-review.md")
        self.assertIn("workflow_state.validate_technical_approval_commit(repo_root", text)
        self.assertIn('stage="implementation", base_commit=base_commit', text)
        self.assertIn("only to the commit this invocation just created", text)
        self.assertIn("never retroactively to discovered history", text)
        self.assertIn("MalformedTechnicalApprovalCommitError", text)
        # And the two grandfathered SHAs are named, so the forward-only
        # scope is a recorded fact rather than an unexplained choice.
        self.assertIn("9fd3c72", text)
        self.assertIn("ae51770", text)

    def test_approve_review_technical_approval_states_the_final_paragraph_requirement(self):
        """Baseline-tag verification cleanup: a fresh Opus review found
        that `approve-review.md`'s implementation-stage technical-approval
        commit instruction never stated this rule at all -- unlike step
        6.4's own plan-stage approval commit in the same file, which the
        earlier `workflow-v2-3-followups` round already covered."""
        text = _command_text("approve-review.md")
        self.assertIn(
            "**These two trailer lines must also be\n"
            "   the commit message's own final paragraph** — after any\n"
            "   `Co-Authored-By:`/`Claude-Session:` lines, never before them",
            text,
        )
        self.assertIn("OPUS-R129-001", text)
        self.assertIn("discover_technical_approval_commit", text)


class TestAcceptMilestoneCompletionCommitTrailerShape(unittest.TestCase):
    """Baseline-freeze correctness fix, required acceptance criterion 3:
    constructs the `/accept-milestone` completion commit's own message shape
    -- a `Workflow-Work-Item: <work_item_id>` trailer alongside
    `Co-Authored-By:`/`Claude-Session:` metadata -- in both the pre-fix
    layout every real completion commit to date used
    (`27f051e`/`fb134ac`/`d271d89`) and the corrected layout
    `accept-milestone.md` step 6 now requires, and confirms via the real
    `git interpret-trailers --parse` mechanism
    (`workflow_state._commit_trailers`, `discover_checkpoint_commits`'s own
    parsing primitive) which one actually yields a recognized
    `Workflow-Work-Item` trailer. Uses a real `ScratchRepo` commit, not a
    string-parsing simulation, so this exercises Git's own trailer
    machinery rather than a reimplementation of it."""

    _SUBJECT = "docs(demo-item): accept milestone (real /accept-milestone, MILESTONE_COMPLETE)"
    _NARRATIVE = (
        'User confirmation: "I confirm acceptance of demo-item." validated by '
        "workflow_state.validate_user_confirmation."
    )
    _COAUTHOR_LINES = (
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_demo"
    )

    def _commit_with_body(self, repo: h.ScratchRepo, body: str) -> str:
        (repo.root / "demo.txt").write_text(body)
        subprocess.run(["git", "add", "demo.txt"], cwd=repo.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", body], cwd=repo.root, check=True, capture_output=True)
        return repo.head()

    def test_pre_fix_layout_silently_drops_the_trailer(self):
        """The exact shape `27f051e`/`fb134ac`/`d271d89` all share: the
        Workflow-Work-Item paragraph precedes Co-Authored-By/Claude-Session,
        separated by a blank line, so it is not part of the message's own
        last paragraph and git interpret-trailers --parse never sees it."""
        with h.ScratchRepo() as repo:
            body = (
                f"{self._SUBJECT}\n\n{self._NARRATIVE}\n\n"
                "Workflow-Work-Item: demo-item\n\n"
                f"{self._COAUTHOR_LINES}\n"
            )
            commit = self._commit_with_body(repo, body)
            trailers = ws._commit_trailers(repo.root, commit)
            self.assertNotIn("Workflow-Work-Item", trailers)

    def test_final_paragraph_layout_is_recognized_as_a_trailer(self):
        """The layout accept-milestone.md step 6 now requires: the
        Workflow-Work-Item line inside the same final trailer paragraph as
        Co-Authored-By/Claude-Session, positioned after them."""
        with h.ScratchRepo() as repo:
            body = (
                f"{self._SUBJECT}\n\n{self._NARRATIVE}\n\n"
                f"{self._COAUTHOR_LINES}\n"
                "Workflow-Work-Item: demo-item\n"
            )
            commit = self._commit_with_body(repo, body)
            trailers = ws._commit_trailers(repo.root, commit)
            self.assertEqual(trailers.get("Workflow-Work-Item"), "demo-item")


class TestBaselinePortabilityCommandsCommitTrailerShape(unittest.TestCase):
    """Baseline-portability correctness fix, the same required proof
    `TestAcceptMilestoneCompletionCommitTrailerShape` establishes for
    `accept-milestone.md`, generalized here across the distinct
    Workflow-* trailer families the commands fixed in this same round
    write (`prepare-functional-review.md`, `apply-functional-review.md`,
    `apply-implementation-review.md`, `recover-implementation-provenance.md`;
    `accept-scoped-remediation.md` was a fifth until ledger `I10` retired
    it, taking its `Workflow-Scoped-Remediation-Acceptance` family with it):
    constructs both the pre-fix layout (the trailer paragraph separated from
    `Co-Authored-By:`/`Claude-Session:` by a blank line) and the corrected
    layout (the trailer paragraph positioned after them, as the message's
    own final paragraph) against a real `ScratchRepo` commit for each
    family, and confirms via the real `git interpret-trailers --parse`
    mechanism (`workflow_state._commit_trailers`, the exact primitive
    `discover_current_functional_checklist_evidence`/
    `discover_current_bundle_generation_record_commit` both build on) which
    layout actually yields a recognized trailer. One trailer family is
    shared by three of the five commands
    (`Workflow-Bundle-Generation-Record`/`Workflow-Work-Item`, optionally
    with `Workflow-Supersedes`) since `_discover_trailer_commits` parses it
    identically regardless of which command wrote a given commit -- the
    shape proof does not need to repeat per command once per family is
    covered.

    Baseline-tag verification cleanup (`OPUS-R129-001`): extended with the
    `Workflow-Technical-Approval`/`Workflow-Work-Item` family, the one
    genuinely new family among the three trailer-write sites this round
    fixes (`milestone-implement.md` step 4, `bootstrap-workflow-v2.md`'s
    NO_CHECKPOINT branch, `approve-review.md`'s implementation-stage
    technical-approval commit) -- the other two sites both write
    `Workflow-Bundle-Generation-Record`/`Workflow-Work-Item`, already
    proven above."""

    _SUBJECT = "chore(demo-item): record trailer, revision 1"
    _NARRATIVE = "Demonstration commit for the trailer-shape proof."
    _COAUTHOR_LINES = (
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_demo"
    )

    def _commit_with_body(self, repo: h.ScratchRepo, body: str) -> str:
        (repo.root / "demo.txt").write_text(body)
        subprocess.run(["git", "add", "demo.txt"], cwd=repo.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", body], cwd=repo.root, check=True, capture_output=True)
        return repo.head()

    def _assert_pre_fix_layout_silently_drops_the_trailer(self, trailer_lines: str, trailer_keys: list[str]):
        with h.ScratchRepo() as repo:
            body = (
                f"{self._SUBJECT}\n\n{self._NARRATIVE}\n\n"
                f"{trailer_lines}\n\n"
                f"{self._COAUTHOR_LINES}\n"
            )
            commit = self._commit_with_body(repo, body)
            trailers = ws._commit_trailers(repo.root, commit)
            for key in trailer_keys:
                self.assertNotIn(key, trailers)

    def _assert_final_paragraph_layout_is_recognized(self, trailer_lines: str, expected: dict[str, str]):
        with h.ScratchRepo() as repo:
            body = (
                f"{self._SUBJECT}\n\n{self._NARRATIVE}\n\n"
                f"{self._COAUTHOR_LINES}\n"
                f"{trailer_lines}\n"
            )
            commit = self._commit_with_body(repo, body)
            trailers = ws._commit_trailers(repo.root, commit)
            for key, value in expected.items():
                self.assertEqual(trailers.get(key), value)

    def test_functional_checklist_pre_fix_layout_silently_drops_the_trailer(self):
        """`prepare-functional-review.md` step 3a's own trailer pair."""
        self._assert_pre_fix_layout_silently_drops_the_trailer(
            "Workflow-Functional-Checklist: demo-item/4/abc123\n"
            "Workflow-Work-Item: demo-item",
            ["Workflow-Functional-Checklist", "Workflow-Work-Item"],
        )

    def test_functional_checklist_final_paragraph_layout_is_recognized(self):
        self._assert_final_paragraph_layout_is_recognized(
            "Workflow-Functional-Checklist: demo-item/4/abc123\n"
            "Workflow-Work-Item: demo-item",
            {"Workflow-Functional-Checklist": "demo-item/4/abc123", "Workflow-Work-Item": "demo-item"},
        )

    def test_bundle_generation_record_pre_fix_layout_silently_drops_the_trailer(self):
        """The trailer pair `apply-functional-review.md`'s bounded branch and
        `apply-implementation-review.md` step 7 both write for the
        `"ordinary"` outcome."""
        self._assert_pre_fix_layout_silently_drops_the_trailer(
            "Workflow-Bundle-Generation-Record: demo-item/4\n"
            "Workflow-Work-Item: demo-item",
            ["Workflow-Bundle-Generation-Record", "Workflow-Work-Item"],
        )

    def test_bundle_generation_record_final_paragraph_layout_is_recognized(self):
        self._assert_final_paragraph_layout_is_recognized(
            "Workflow-Bundle-Generation-Record: demo-item/4\n"
            "Workflow-Work-Item: demo-item",
            {"Workflow-Bundle-Generation-Record": "demo-item/4", "Workflow-Work-Item": "demo-item"},
        )

    def test_bundle_generation_record_with_supersedes_pre_fix_layout_silently_drops_the_trailer(self):
        """The three-trailer set `recover-implementation-provenance.md`
        step 5 writes (and the `"same_content"` outcome branch in
        `apply-functional-review.md`/`apply-implementation-review.md`)."""
        self._assert_pre_fix_layout_silently_drops_the_trailer(
            "Workflow-Bundle-Generation-Record: demo-item/4\n"
            "Workflow-Work-Item: demo-item\n"
            "Workflow-Supersedes: 0123456789abcdef0123456789abcdef01234567",
            ["Workflow-Bundle-Generation-Record", "Workflow-Work-Item", "Workflow-Supersedes"],
        )

    def test_bundle_generation_record_with_supersedes_final_paragraph_layout_is_recognized(self):
        self._assert_final_paragraph_layout_is_recognized(
            "Workflow-Bundle-Generation-Record: demo-item/4\n"
            "Workflow-Work-Item: demo-item\n"
            "Workflow-Supersedes: 0123456789abcdef0123456789abcdef01234567",
            {
                "Workflow-Bundle-Generation-Record": "demo-item/4",
                "Workflow-Work-Item": "demo-item",
                "Workflow-Supersedes": "0123456789abcdef0123456789abcdef01234567",
            },
        )

    def test_technical_approval_pre_fix_layout_silently_drops_the_trailer(self):
        """`approve-review.md`'s implementation-stage technical-approval
        commit's own trailer pair -- baseline-tag verification cleanup, the
        one distinct trailer family among the three closed-gap sites this
        round fixes that had no prior `ScratchRepo`-based shape proof (the
        other two sites share the `Workflow-Bundle-Generation-Record`
        family already proven above)."""
        self._assert_pre_fix_layout_silently_drops_the_trailer(
            "Workflow-Technical-Approval: "
            "33139aaf7e637fc32dfd86a31f73c6153e32d2dc0a4ff8c4fa46ee2afe131a96\n"
            "Workflow-Work-Item: demo-item",
            ["Workflow-Technical-Approval", "Workflow-Work-Item"],
        )

    def test_technical_approval_final_paragraph_layout_is_recognized(self):
        self._assert_final_paragraph_layout_is_recognized(
            "Workflow-Technical-Approval: "
            "33139aaf7e637fc32dfd86a31f73c6153e32d2dc0a4ff8c4fa46ee2afe131a96\n"
            "Workflow-Work-Item: demo-item",
            {
                "Workflow-Technical-Approval": "33139aaf7e637fc32dfd86a31f73c6153e32d2dc0a4ff8c4fa46ee2afe131a96",
                "Workflow-Work-Item": "demo-item",
            },
        )


class TestAssertLocalGenerationMatchesCallSiteConformance(unittest.TestCase):
    """Item 342 (`GPT-R63-001`; narrowed, revision 82, `OPUS-R102-001` --
    `WF8c` scope clause (k)): `assert_local_generation_matches` has
    exactly three live call sites in this repository today --
    `/approve-review`'s, `/review-plan`'s, and (`workflow-v2-3` CP1)
    `/review-implementation`'s own repository-local staleness checks, all
    three at the permissive `require_metadata=False` default. Item 342's
    own original text (revision 47) expected a third,
    `require_metadata=True` caller -- `D-Approval-Commits`' current-round
    bundle-publication binding check -- but the atomic/staged
    bundle-publication redesign that caller belonged to was superseded,
    revision 82, before ever being built; `require_metadata=True`'s own
    correctness is exercised directly instead
    (`workflow_fingerprint_test.py`'s `TestGenerationDiagnosticMetadata`
    strict-mode tests), not through a caller that does not exist. This
    assertion fails if a future change adds a call site not in
    `EXPECTED_CALL_SITES`, so that change cannot land without a human
    deciding whether `WFR-17`/`D-Bundle-Manifest` need updating too.
    workflow-2.7.0 adds a fourth, `workflow_state.py`'s two gate wrappers
    (`plan_approval_gate_status`/`technical_approval_gate_status`), which
    run `/approve-review`'s check on its behalf and are repository-local
    by construction."""

    EXPECTED_CALL_SITES = frozenset({
        Path(".claude/commands/approve-review.md"),
        Path(".claude/commands/review-plan.md"),
        Path(".claude/commands/review-implementation.md"),
        # workflow-2.7.0 (`D-OP-Next`, `LPR-R1-003`): the two repository-aware
        # gate wrappers run `/approve-review`'s generation check as their first
        # cause; both are repository-local by construction (`WFR-17` holds).
        # CP5 adds `ingest_manual_review_verdict` in the same file, the
        # record-manual commands' generation check (`LPR-R2-002`).
        Path("scripts/workflow_state.py"),
    })

    _CALL_RE = re.compile(r"assert_local_generation_matches\(")

    def test_exactly_the_expected_live_permissive_callers_exist(self):
        repo_root = _repo_root()
        found: set[Path] = set()
        for path in sorted((repo_root / ".claude" / "commands").glob("*.md")):
            if self._CALL_RE.search(path.read_text()):
                found.add(path.relative_to(repo_root))
        for path in sorted((repo_root / "scripts").glob("*.py")):
            # Excludes workflow_fingerprint.py itself (the function's own
            # definition, not a caller) and every *_test.py/*_demo_test.py
            # (exercises, not production call sites) -- suffix-matched, so
            # workflow_test_harness.py (production code whose name merely
            # contains "test") is not wrongly excluded from the scan.
            if path.name == "workflow_fingerprint.py" or path.stem.endswith("_test"):
                continue
            if self._CALL_RE.search(path.read_text()):
                found.add(path.relative_to(repo_root))
        self.assertEqual(found, set(self.EXPECTED_CALL_SITES))

    def test_no_external_review_or_archive_consumption_path_calls_it(self):
        """`prepare-ai-review.sh` generates bundles consumed by both a
        local approval command and an external reviewer's extracted
        archive -- it must never call the repository-local-only check
        itself (`WFR-17`)."""
        repo_root = _repo_root()
        script_text = (repo_root / "scripts" / "prepare-ai-review.sh").read_text()
        self.assertNotIn("assert_local_generation_matches", script_text)


class TestGenerationDiagnosticMetadataCallerWordingConformance(unittest.TestCase):
    """Item 343 (`GPT-R64-002`, `WF8c` scope clause (k)): active
    caller-facing documentation must agree with the three-live-caller
    reality item 342 proves, using non-exclusive wording rather than
    naming `/approve-review` as the sole repository-local consumer.
    `/review-implementation` (`workflow-v2-3` CP1) is the third live
    caller (`GPT-IR1-002`)."""

    def test_review_protocol_names_all_live_callers(self):
        repo_root = _repo_root()
        text = (repo_root / "docs" / "ai-workflow" / "REVIEW_PROTOCOL.md").read_text()
        match = re.search(
            r"### Generation diagnostic metadata.*?(?=\n### )", text, re.DOTALL,
        )
        self.assertIsNotNone(match, "expected a 'Generation diagnostic metadata' section")
        section = match.group(0)
        self.assertIn("/approve-review", section)
        self.assertIn("/review-plan", section)
        self.assertIn("/review-implementation", section)

    def test_worktree_or_head_mismatch_docstring_is_non_exclusive(self):
        repo_root = _repo_root()
        text = (repo_root / "scripts" / "workflow_fingerprint.py").read_text()
        match = re.search(r"class WorktreeOrHeadMismatchError.*?\"\"\"(.*?)\"\"\"", text, re.DOTALL)
        self.assertIsNotNone(match)
        docstring = match.group(1)
        self.assertIn("/approve-review", docstring)
        self.assertIn("/review-plan", docstring)
        self.assertIn("/review-implementation", docstring)

    def test_assert_local_generation_matches_docstring_is_non_exclusive(self):
        repo_root = _repo_root()
        text = (repo_root / "scripts" / "workflow_fingerprint.py").read_text()
        match = re.search(
            r"def assert_local_generation_matches\(.*?\"\"\"(.*?)\"\"\"", text, re.DOTALL,
        )
        self.assertIsNotNone(match)
        docstring = match.group(1)
        self.assertIn("/approve-review", docstring)
        self.assertIn("/review-plan", docstring)
        self.assertIn("/review-implementation", docstring)


class TestDemoTestNoLiveAnchorStaticConformance(unittest.TestCase):
    """`workflow-v2-3`'s own CP1 missing-test item (revision 5/6/7/8,
    round 4-7 missing tests/B1): a real-repository test anchored at
    `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` is only actually fixed if every
    call site feeding it a `head`/`commit` argument is fixed too -- five
    functions across both `_demo_test.py` files
    (`workflow_state.approval_is_current`,
    `workflow_state.implementing_entry_reachable`,
    `workflow_fingerprint.compute_review_content_id_plan_stage_at_commit`,
    `..._at_commit_for_work_item`,
    `compute_review_content_id_implementation_stage_at_commit`) each carry
    one parameter (`head` or `commit`, resolved from the real signature via
    `inspect.signature(fn).bind_partial(...)`, never a hand-maintained
    positional-index map -- the index a function's anchor sits at differs
    per function and has drifted out of this document's own prose four
    rounds running) that must never resolve to live `"HEAD"` -- neither by
    omission (the parameter's own default) nor by an explicit literal
    `"HEAD"` string. Gate is this test passing, not a hand-checked list:
    hand enumeration of this exact call set came up short three consecutive
    review rounds (five offenders found -> six -> seven), and a fourth time
    at the property-statement level itself. No allowlist: every real call
    site into these five functions in either `_demo_test.py` file is
    scanned, and the one call this property could never apply to
    (`load_implementation_stage_classification`/`any_protected_path_dirty`
    in `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`,
    and the two active-work-item-scoped tests' own deliberately live
    `_changed_tracked_paths_between` calls) matches none of the five
    scanned names, so it is never flagged in the first place and needs no
    exemption."""

    _TARGET_FUNCTIONS = {
        "approval_is_current": (ws.approval_is_current, "head"),
        "implementing_entry_reachable": (ws.implementing_entry_reachable, "head"),
        "compute_review_content_id_plan_stage_at_commit": (
            fingerprint.compute_review_content_id_plan_stage_at_commit, "commit",
        ),
        "compute_review_content_id_plan_stage_at_commit_for_work_item": (
            fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item, "commit",
        ),
        "compute_review_content_id_implementation_stage_at_commit": (
            fingerprint.compute_review_content_id_implementation_stage_at_commit, "commit",
        ),
    }

    @classmethod
    def _scan(cls, source_text: str) -> tuple[list[str], int]:
        """Returns `(flagged_descriptions, total_real_call_site_count)`.
        A call site is "real" if its function name matches one of the five
        scanned names (via attribute access, e.g. `ws.approval_is_current(...)`
        or `wf.compute_review_content_id_plan_stage_at_commit(...)` -- the
        only calling convention either `_demo_test.py` file uses for these
        functions); it is "flagged" if the anchor parameter's bound value
        (resolved via `inspect.signature(fn).bind_partial(...)`, positional
        or keyword, never a hand-maintained index) is omitted entirely
        (the parameter's own live-`"HEAD"` default) or is present as the
        literal constant string `"HEAD"`."""
        tree = ast.parse(source_text)
        flagged: list[str] = []
        total = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            name = func.attr
            if name not in cls._TARGET_FUNCTIONS:
                continue
            real_fn, anchor_param = cls._TARGET_FUNCTIONS[name]
            if any(isinstance(a, ast.Starred) for a in node.args):
                continue
            if any(kw.arg is None for kw in node.keywords):
                continue
            sig = inspect.signature(real_fn)
            try:
                bound = sig.bind_partial(*node.args, **{kw.arg: kw.value for kw in node.keywords})
            except TypeError:
                continue
            total += 1
            if anchor_param not in bound.arguments:
                flagged.append(f"{name}(...) at line {node.lineno}: {anchor_param!r} omitted (live default)")
                continue
            value = bound.arguments[anchor_param]
            if isinstance(value, ast.Constant) and value.value == "HEAD":
                flagged.append(f"{name}(...) at line {node.lineno}: {anchor_param}=\"HEAD\" (live literal)")
        return flagged, total

    def test_negative_control_flags_one_synthetic_fixture_per_scanned_function_plus_explicit_head(self):
        """A silently-broken callee-matcher (wrong attribute/name
        resolution, a missed `ws.`/`fingerprint.` prefix, a module-alias
        change) must not pass vacuously by finding nothing -- and a scan
        that resolves any single function's anchor slot incorrectly must
        fail this control rather than pass it (revision 8, round 7 B1/
        missing tests)."""
        fixture = "\n".join([
            "ws.approval_is_current(repo_root, work_item, stage='plan', base_commit=base_commit)",
            "ws.implementing_entry_reachable(repo_root, work_item, base_commit)",
            "wf.compute_review_content_id_plan_stage_at_commit(repo_root, base, "
            "work_item_type='process', work_item_id='x', plan_revision=1, protected=p, "
            "excluded_paths=e, excluded_prefixes=x)",
            "wf.compute_review_content_id_plan_stage_at_commit_for_work_item(repo_root, 'x')",
            "wf.compute_review_content_id_implementation_stage_at_commit(repo_root, base, "
            "work_item_type='process', work_item_id='x', protected_paths=p, "
            "protected_prefixes=pp, excluded_paths=e, excluded_prefixes=x)",
            "ws.approval_is_current(repo_root, work_item, stage='plan', base_commit=base_commit, "
            "head=\"HEAD\")",
        ])
        flagged, total = self._scan(fixture)
        self.assertEqual(total, 6, flagged)
        self.assertEqual(len(flagged), 6, flagged)

    def test_negative_control_passes_a_fixed_commit_fixture(self):
        fixture = "\n".join([
            "ws.approval_is_current(repo_root, work_item, stage='plan', base_commit=base_commit, "
            "head=FIXED_COMMIT)",
            "ws.implementing_entry_reachable(repo_root, work_item, base_commit, head=FIXED_COMMIT)",
            "wf.compute_review_content_id_plan_stage_at_commit(repo_root, base, FIXED_COMMIT, "
            "work_item_type='process', work_item_id='x', plan_revision=1, protected=p, "
            "excluded_paths=e, excluded_prefixes=x)",
            "wf.compute_review_content_id_plan_stage_at_commit_for_work_item(repo_root, 'x', "
            "FIXED_COMMIT)",
            "wf.compute_review_content_id_implementation_stage_at_commit(repo_root, base, "
            "FIXED_COMMIT, work_item_type='process', work_item_id='x', protected_paths=p, "
            "protected_prefixes=pp, excluded_paths=e, excluded_prefixes=x)",
        ])
        flagged, total = self._scan(fixture)
        self.assertEqual(total, 5, flagged)
        self.assertEqual(flagged, [])

    def test_real_demo_test_files_have_no_live_anchor_call_site(self):
        repo_root = _repo_root()
        all_flagged: list[str] = []
        total = 0
        for relpath in ("scripts/workflow_fingerprint_demo_test.py", "scripts/workflow_state_demo_test.py"):
            text = (repo_root / relpath).read_text()
            flagged, count = self._scan(text)
            total += count
            all_flagged.extend(f"{relpath}: {item}" for item in flagged)
        self.assertGreater(total, 0, "expected at least one real call site into the five scanned functions")
        self.assertEqual(all_flagged, [])


def _extract_numbered_steps(text: str) -> dict[str, str]:
    """Splits a command file's body into `{step number: full block text}`,
    where a block runs from a line starting `N. ` up to (not including)
    the next such line. Used only to isolate the v1-relevant step blocks
    for comparison below -- not a general Markdown parser."""
    steps: dict[str, str] = {}
    current_num: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = re.match(r"^(\d+)\.\s", line)
        if match:
            if current_num is not None:
                steps[current_num] = "".join(current_lines)
            current_num = match.group(1)
            current_lines = [line]
        elif current_num is not None:
            current_lines.append(line)
    if current_num is not None:
        steps[current_num] = "".join(current_lines)
    return steps


def _strip_bracketed_2_1_bullets(block: str) -> str:
    """Removes any `   - **[2.1]**` bulleted sub-item (and its indented
    continuation lines) from a step block, leaving the v1-relevant text
    that was already there before this milestone interleaved its own
    additive `[2.1]` sub-steps."""
    out: list[str] = []
    skipping = False
    for line in block.splitlines(keepends=True):
        if re.match(r"^   - \*\*\[2\.1\]\*\*", line):
            skipping = True
            continue
        if skipping:
            if line.strip() == "" or line.startswith("     "):
                continue
            skipping = False
        out.append(line)
    return "".join(out)


def _normalize_v1_path_variables(text: str) -> str:
    """Reverses this milestone's purely cosmetic `<bundle_dir>`/
    `<feedback_dir>`/`[work_item_id]` template-variable introduction
    (WF5, per-work-item bundle relayout with a flat-layout fallback) back
    to the literal paths a v1 item always resolves them to, so the result
    is comparable against the real pre-v2.1 base-commit text."""
    text = text.replace("<bundle_dir>", ".ai-review/current")
    text = text.replace("<feedback_dir>", ".ai-review/feedback")
    text = text.replace(" [work_item_id]", "")
    return text


class TestGoldenV1BehaviorAgainstPreV21BaseCommit(unittest.TestCase):
    """Missing-test item 90's stronger half: for `/milestone-plan` and
    `/apply-plan-review`, a genuine pre-`v2.1` copy of each command file
    *is* available (`git show <base_commit>:<path>`, `base_commit`
    `162154d3e5e10eb65e109833acae4b4fb01fc5d6`, from `WORKFLOW_STATE.json`'s
    own `work_items["workflow-v2-1-core"].base_commit` -- confirmed
    fetchable before writing this test), so this goes beyond a
    self-referential hash: it verifies the *current* file's v1-relevant
    step text is structurally equal to that real base-commit text, modulo
    two purely cosmetic, already-documented substitutions (`<bundle_dir>`/
    `<feedback_dir>` templating and an added optional `[work_item_id]`
    argument to `prepare-ai-review.sh`, both WF5) and the additive
    `[2.1]`-tagged sub-steps this milestone interleaved.

    `/milestone-plan`'s step 6 is a **third, genuine (not cosmetic)**
    delta, added by `D-Fingerprint-Generalization` (Revision 21,
    `WF8B-S1-001`): `prepare-ai-review.sh`'s work-item-id argument for the
    plan stage becomes `<work_item_id>` (required), not `[work_item_id]`
    (optional) -- this is a real behavior change for a `"1"`-governed item
    too (`workflow-v2-1-core` itself now always passes its own id), not
    reversible by `_normalize_v1_path_variables`'s cosmetic substitution.
    Step 6 is therefore excluded from the byte-equality hash below (same
    pattern `/apply-plan-review`'s own step-1 `WFR-03` addition uses) and
    separately asserted present, unconditional, and stated as required.

    The base-commit text is embedded here as a **literal sha256
    constant** rather than fetched via `git show` at test time, since a
    shallow CI checkout (this repository's `actions/checkout@v4` step
    uses the default `fetch-depth: 1`) would not have that historical
    commit reachable at all -- embedding the already-verified-equal
    normalized text's hash keeps this test hermetic and CI-safe while
    still being a real base-commit comparison, not a self-referential one
    invented after the fact. The comparison was performed once, directly
    against a real `git show` of that commit, before this constant was
    recorded (documented in this checkpoint's own report).

    `/apply-plan-review`'s v1 path is **not** fully byte-identical to the
    base commit: step 1 gained a genuine, version-unconditional addition
    (`WFR-03`'s feedback binding-field validation, from `WF5` -- a
    different checkpoint, not `WF4a-ii`/`WF4a-iv`) and step 7 gained a
    deliberate, disclosed exit-target renaming (`WF4a-ii`, "the
    exit-target naming" the checkpoint's own scope note names explicitly).
    Both are legitimate, already-documented deltas -- this test therefore
    only claims byte-equality for `/apply-plan-review`'s steps 2-6, and
    separately confirms step 1's real addition is present and applies
    unconditionally (not gated behind a `"2.1"`-only block) rather than
    silently ignoring it.

    `WF8c` item (h), part 1 (`WFR-67`) adds a fourth genuine, real,
    version-unconditional delta to each file, following the identical
    `WFR-03` pattern above: `/milestone-plan`'s step 7 and
    `/apply-plan-review`'s step 3 each gain a
    `workflow_fingerprint.assert_bundle_not_rejected(...)` call --
    `REJECTED`-bundle refusal applies to every governing version alike,
    nothing about it is `"2.1"`-specific. Both steps are therefore
    excluded from the byte-equality comparisons below (`/milestone-plan`'s
    joined set narrows from `"123457"` to `"12345"`; `/apply-plan-review`'s
    from `"2346"` to `"246"`), the remaining steps were independently
    re-verified byte-identical to a fresh `git show` of the real base
    commit before the two hash constants below were recomputed, and each
    addition's presence and unconditional placement is separately asserted
    below, mirroring `test_apply_plan_review_step1_wfr03_addition_is_present_and_version_unconditional`.
    """

    def test_milestone_plan_v1_steps_equal_pre_v21_base_commit_modulo_known_renames(self):
        current = _command_text("milestone-plan.md")
        steps = _extract_numbered_steps(current)
        # Step 6 excluded: D-Fingerprint-Generalization's required
        # <work_item_id> argument is a genuine v1-visible behavior change,
        # asserted separately below, not folded into this byte-equality
        # comparison (see this class's own docstring). Step 7 excluded:
        # WFR-67's assert_bundle_not_rejected addition (WF8c item (h),
        # part 1), same reasoning, asserted separately below.
        normalized = "".join(
            _normalize_v1_path_variables(_strip_bracketed_2_1_bullets(steps[n]))
            for n in "12345"
        )
        self.assertEqual(
            hashlib.sha256(normalized.encode()).hexdigest(),
            "e70115cd0afe40d04d92c1895623f2500d7d210ca5912966a740a04ef3728fb3",
        )

    def test_milestone_plan_step7_wfr67_addition_is_present_and_version_unconditional(self):
        current = _command_text("milestone-plan.md")
        steps = _extract_numbered_steps(current)
        step7 = steps["7"]
        self.assertIn("assert_bundle_not_rejected", step7)
        self.assertIn("WFR-67", step7)
        # The addition lives in step 7 itself, outside both the step-0
        # dual-mode block and any "[2.1]"-tagged sub-step -- it applies to
        # both governing versions equally, exactly as WFR-67's own scope
        # (bundle-integrity, not gated by governing_workflow_version)
        # intends.
        step0 = steps.get("0", "")
        self.assertNotIn("WFR-67", step0)

    def test_milestone_plan_step6_requires_work_item_id_for_plan_stage(self):
        current = _command_text("milestone-plan.md")
        steps = _extract_numbered_steps(current)
        step6 = steps["6"]
        self.assertIn("prepare-ai-review.sh <base-sha> plan <work_item_id>", step6)
        self.assertNotIn("[work_item_id]", step6)
        self.assertIn("required", step6)

    def test_apply_plan_review_v1_steps_2_to_6_equal_pre_v21_base_commit_modulo_known_renames(self):
        # Step 5 excluded: D-Fingerprint-Generalization's required
        # <work_item_id> argument is a genuine v1-visible behavior change
        # here too (same reasoning as milestone-plan.md's step 6 above),
        # asserted separately below rather than folded into this
        # byte-equality comparison. Step 3 excluded: WFR-67's
        # assert_bundle_not_rejected addition (WF8c item (h), part 1),
        # same reasoning, asserted separately below.
        current = _command_text("apply-plan-review.md")
        steps = _extract_numbered_steps(current)
        normalized = "".join(_normalize_v1_path_variables(steps[n]) for n in "246")
        self.assertEqual(
            hashlib.sha256(normalized.encode()).hexdigest(),
            "1af695495d148c5568e0030222fb4bae32327cfdcee2db64b934c2196aa18854",
        )

    def test_apply_plan_review_step3_wfr67_addition_is_present_and_version_unconditional(self):
        current = _command_text("apply-plan-review.md")
        steps = _extract_numbered_steps(current)
        step3 = steps["3"]
        self.assertIn("assert_bundle_not_rejected", step3)
        self.assertIn("WFR-67", step3)
        step0 = steps.get("0", "")
        self.assertNotIn("WFR-67", step0)

    def test_apply_plan_review_step5_requires_work_item_id_for_plan_stage(self):
        current = _command_text("apply-plan-review.md")
        steps = _extract_numbered_steps(current)
        step5 = steps["5"]
        self.assertIn("prepare-ai-review.sh <base-sha> plan <work_item_id>", step5)
        self.assertNotIn("[work_item_id]", step5)
        self.assertIn("required", step5)

    def test_apply_plan_review_step1_wfr03_addition_is_present_and_version_unconditional(self):
        current = _command_text("apply-plan-review.md")
        steps = _extract_numbered_steps(current)
        step1 = steps["1"]
        self.assertIn("Validate its binding\n   fields", step1)
        self.assertIn("WFR-03", step1)
        # The addition lives in step 1 itself, outside both the step-0
        # dual-mode block and the "2.1"-only step 7' block -- i.e. it
        # applies to both governing versions equally, exactly as WF5's
        # own scope (a general REVIEW_PROTOCOL.md hardening, not gated by
        # governing_workflow_version) intended.
        step0 = steps.get("0", "")
        self.assertNotIn("WFR-03", step0)


class TestReviewPlanRefusalGoldenHash(unittest.TestCase):
    """`/review-plan` has no v1 branch at all (it refuses cleanly
    instead), so item 90's second half ("`/review-plan` invoked against a
    `"1"` item refuses cleanly, naming why") is verified by
    `TestVersion21OnlyCommandsRefuseCleanlyForV1` above; this adds the
    golden-hash drift guard for that exact refusal text specifically
    (narrower than the whole-file hash in `TestGoldenCommandFileHashes`,
    so a future edit to the refusal wording itself is caught even if
    unrelated parts of the file also change)."""

    def test_governing_version_guard_step_text_is_unchanged(self):
        text = _command_text("review-plan.md")
        steps_by_marker = text.split("2. **Governing-version guard**:", 1)
        self.assertEqual(len(steps_by_marker), 2, "review-plan.md's step-2 marker text has changed")
        guard_text = steps_by_marker[1].split("3. **Phase guard**", 1)[0]
        # workflow-2.5.0: widened from a bare `!= "2.1"` check to
        # `TWO_STAGE_PLAN_REVIEW_VERSIONS` membership (D-Implementation-
        # Review-Version-Activation's inheritance rule) -- an intentional
        # content change, not a regression.
        self.assertEqual(
            hashlib.sha256(guard_text.encode()).hexdigest(),
            "f5b22d30b2ee49998b9daceccb968242c5a22a9cbdf412d65e46eec15844d04d",
        )
        self.assertIn(
            'not a member of\n   `workflow_state.TWO_STAGE_PLAN_REVIEW_VERSIONS`',
            guard_text,
        )


class TestReviewPlanStep8OwnershipGuardIsPresent(unittest.TestCase):
    """workflow-2.5.0 REVISE round 3, Missing-tests item (O4): the prose
    binding this round added for `review-implementation.md`'s `A6`
    (`test_the_2_2_authoritative_branchs_reviewer_role_and_ledger_key_match_
    the_code_constant` above) has no `/review-plan` twin. `review-plan.md`
    step 8's own ownership guard is pinned only by
    `TestGoldenCommandFileHashes`' whole-file hash, which moves on *any*
    edit -- recording that the file changed, never that the guard itself is
    still in it. This closes that gap with a direct check over the shipped
    file, mirroring this class' own narrower-than-whole-file style.

    round 4's O4: the first check is a regex tolerant of whitespace/wrapping
    around the call's own arguments -- pinning the guard's presence and its
    exact arguments, never the surrounding line-wrap/indentation a harmless
    prose reflow could otherwise break for a reason unrelated to the guard.
    The ordering assertion below (matched on the bare function name, already
    robust) is unchanged and remains the part carrying this test's real
    value."""

    # Re-pointed, D-Feedback-Layout (workflow-2.6.0, CP3): the guard call
    # now also passes `state=<the parsed docs/ai-workflow/WORKFLOW_STATE.json>`
    # (the bounded legacy-writer terminal-owner relaxation needs it), so the
    # pin names that argument too rather than the pre-2.6.0 two-argument form.
    _GUARD_CALL_RE = re.compile(
        r"assert_feedback_not_owned_by_other_work_item\(\s*"
        r"existing_content,\s*work_item_id=work_item_id,\s*"
        r"state=<the parsed\s+docs/ai-workflow/WORKFLOW_STATE\.json>\s*\)"
    )

    def test_step_8_calls_the_ownership_guard_before_the_first_write(self):
        text = _command_text("review-plan.md")
        step8 = text.split("8. **Write set, exact.**", 1)[1]
        self.assertRegex(step8, self._GUARD_CALL_RE)
        # The guard must run before the verdict-branch write set below it,
        # not after -- the same ordering property I3 (round 2) pinned for
        # `review-implementation.md`'s `A6`.
        guard_pos = step8.index("assert_feedback_not_owned_by_other_work_item")
        first_write_pos = step8.index("`APPROVE`: `REVIEW_FEEDBACK.md`")
        self.assertLess(guard_pos, first_write_pos)


class TestApplyImplementationReviewStep0SkipIsPhaseConditional(unittest.TestCase):
    """workflow-2.5.0 REVISE round 5, Missing-tests item 1: I3's own prose
    binding -- that step 0's `enter_applying_review_feedback` skip is keyed
    on the item's own `phase`, never on its `governing_workflow_version` --
    was pinned only by `TestGoldenCommandFileHashes`' whole-file hash for
    `apply-implementation-review.md`, which moves on *any* edit, recording
    that the file changed, never that the rule itself is still stated
    correctly. Round 5's own B1 found the shipped design document
    (`WORKFLOW_V2_PLAN.md`) had gone stale on exactly this rule while the
    command file itself stayed correct -- pinning the command file's own
    prose directly, the same way `TestReviewPlanStep8OwnershipGuardIsPresent`
    pins its own guard, is the mechanism that would have caught the
    divergence at the design-document layer by forcing an editor back
    through this test rather than leaving it to whole-file hash movement
    alone."""

    _PHASE_NOT_VERSION_RE = re.compile(
        r"skip\s+is\s+conditional\s+on\s+the\s+item's\s+own\s+current\s+"
        r"`phase`,\s+never\s+on\s+its\s+`governing_workflow_version`"
    )
    _SKIP_CONDITION_RE = re.compile(
        r"skip\s+this\s+call\s+whenever\s+`phase`\s+already\s+equals\s+"
        r"`APPLYING_REVIEW_FEEDBACK`"
    )

    def test_step_0_states_the_skip_is_phase_conditional_not_version_conditional(self):
        text = _command_text("apply-implementation-review.md")
        self.assertRegex(text, self._PHASE_NOT_VERSION_RE)
        self.assertRegex(text, self._SKIP_CONDITION_RE)


# ---------------------------------------------------------------------------
# Missing-test item 107: a bundle-completeness lint for language
# instructing a protected-path correction after approval.
# ---------------------------------------------------------------------------


_POST_APPROVAL_TIMING_CUES = (
    "after approval", "after this is approved", "once approved",
    "post-approval", "after the plan is approved", "after approve-review",
    "after it is approved", "after being approved",
)
_CORRECTION_ACTION_CUES = ("fix", "correct", "update", "edit", "modify", "change")
_NEGATION_CUES = ("never", "must not", "do not", "don't", "should not", "shouldn't", "cannot", "won't", "will not")


def find_post_approval_protected_path_corrections(
    text: str, protected_path_names: frozenset[str],
) -> list[str]:
    """A text heuristic (not a proof -- documented limits below) for
    missing-test item 107: flags sentences that co-occur (a) a
    post-approval timing cue, (b) a correction-action verb, and (c) a
    reference to a named protected path or the generic phrase "protected
    path"/"protected file". A sentence containing a negation cue anywhere
    (e.g. "never correct... after approval") is treated as *stating the
    rule*, not violating it, and is excluded.

    Known limitations, stated plainly:
    - sentence splitting is a naive regex on `.`/`!`/`?`/newlines -- an
      instruction spread across multiple sentences ("Do this. It must
      happen after approval.") is not caught;
    - the negation exclusion is sentence-wide, not scope-limited to the
      action verb -- a sentence with an unrelated negation elsewhere
      could suppress a real hit (a false negative in the safe direction);
    - it is keyword co-occurrence, not natural-language understanding --
      a creatively-worded instruction that avoids every listed cue is not
      caught, and this function makes no claim otherwise.
    """
    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    findings = []
    for sentence in sentences:
        lower = sentence.lower()
        if any(cue in lower for cue in _NEGATION_CUES):
            continue
        has_timing = any(cue in lower for cue in _POST_APPROVAL_TIMING_CUES)
        has_action = any(re.search(rf"\b{re.escape(cue)}\b", lower) for cue in _CORRECTION_ACTION_CUES)
        mentions_protected = (
            any(path.lower() in lower for path in protected_path_names)
            or "protected path" in lower or "protected file" in lower
        )
        if has_timing and has_action and mentions_protected:
            findings.append(sentence.strip())
    return findings


class TestBundleProtectedPathCorrectionLint(unittest.TestCase):
    """Missing-test item 107 (`OPUS-R14-003`): same class of check as
    WF5's own `assert_stage_completeness` (confirmed present via
    `git show d18d939 --stat` and a grep for `assert_stage_completeness`
    in `workflow_fingerprint.py` before writing this), extended here to a
    content lint over a bundle's `PLAN.md`/`IMPLEMENTATION_SUMMARY.md`."""

    def test_clean_bundle_content_has_no_findings(self):
        clean_plan = (
            "## Checkpoint WF9\n\nImplements the widget loader. "
            "No further action is required after approval.\n"
        )
        self.assertEqual(
            find_post_approval_protected_path_corrections(clean_plan, fingerprint.PLAN_STAGE_PROTECTED), [],
        )

    def test_an_injected_post_approval_protected_path_instruction_is_flagged(self):
        violating_summary = (
            "## Implementation notes\n\n"
            "After the plan is approved, update docs/ai-workflow/WORKFLOW_V2_PLAN.md "
            "to correct the checkpoint count.\n"
        )
        findings = find_post_approval_protected_path_corrections(
            violating_summary, fingerprint.PLAN_STAGE_PROTECTED,
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("WORKFLOW_V2_PLAN.md", findings[0])

    def test_a_negated_statement_of_the_rule_itself_is_not_flagged(self):
        """The sentence states the rule ("never correct... after
        approval"), not a violation of it -- the negation exclusion
        exists precisely so this class of sentence, which a naive
        co-occurrence check would misfire on, reads as compliant."""
        rule_statement = (
            "Reviewers must never correct docs/ai-workflow/WORKFLOW_V2_PLAN.md "
            "after approval; open a new remediation item instead.\n"
        )
        self.assertEqual(
            find_post_approval_protected_path_corrections(rule_statement, fingerprint.PLAN_STAGE_PROTECTED), [],
        )

    def test_generic_protected_path_phrase_is_also_detected(self):
        violating = "Once approved, edit the protected path to fix the typo.\n"
        findings = find_post_approval_protected_path_corrections(violating, fingerprint.PLAN_STAGE_PROTECTED)
        self.assertEqual(len(findings), 1)


# ---------------------------------------------------------------------------
# Missing-test item 108: a documentation-consistency lint proving items
# 85/96 and the D-Plan-Review-Stages transition table state the same
# /review-plan write set.
# ---------------------------------------------------------------------------


def _extract_numbered_plan_doc_item(plan_text: str, item_number: int) -> str:
    match = re.search(rf"^{item_number}\.\s.*$", plan_text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"WORKFLOW_V2_PLAN.md has no line starting '{item_number}. '")
    return match.group(0)


def _write_set_tokens_from_item_clause(clause: str, *, verdict: str) -> frozenset[str]:
    """Extracts the {REVIEW_FEEDBACK.md, ledger, phase_transition}
    write-target vocabulary from one clause of item 85/96's prose. The
    `phase transition` token is gated by a literal `(for `REVISE` only)`
    qualifier, since both items state it that way for the shared
    REVISE/BLOCK clause."""
    tokens = set()
    if "REVIEW_FEEDBACK.md" in clause:
        tokens.add("REVIEW_FEEDBACK.md")
    if "ledger" in clause:
        tokens.add("ledger")
    if "phase transition" in clause:
        if "for `REVISE` only" in clause:
            if verdict == "REVISE":
                tokens.add("phase_transition")
        else:
            tokens.add("phase_transition")
    return frozenset(tokens)


def _write_set_by_verdict_from_plan_item(item_text: str) -> dict[str, frozenset[str]]:
    approve_match = re.search(
        r"for (?:an? )?`APPROVE`,?\s*(?:verdict\s*)?(?:is exactly\s*)?(.+?);", item_text,
    )
    revise_block_match = re.search(r"for `REVISE`/`BLOCK`,?\s*(?:it is )?(.+?)(?:;|—)", item_text)
    if approve_match is None or revise_block_match is None:
        raise AssertionError(f"could not locate APPROVE / REVISE-BLOCK clauses in: {item_text!r}")
    approve_clause = approve_match.group(1)
    revise_block_clause = revise_block_match.group(1)
    return {
        "APPROVE": _write_set_tokens_from_item_clause(approve_clause, verdict="APPROVE"),
        "REVISE": _write_set_tokens_from_item_clause(revise_block_clause, verdict="REVISE"),
        "BLOCK": _write_set_tokens_from_item_clause(revise_block_clause, verdict="BLOCK"),
    }


def _write_set_by_verdict_from_transition_table(plan_text: str) -> dict[str, frozenset[str]]:
    """Extracts the same {REVIEW_FEEDBACK.md, ledger, phase_transition}
    vocabulary from the D-Plan-Review-Stages transition table's three
    `/review-plan` rows (`Current state == AWAITING_LOCAL_PLAN_REVIEW`).

    `REVIEW_FEEDBACK.md` is treated as an always-present baseline across
    all three rows, sourced separately from the state's own "Artifacts"
    bullet (`Artifacts`: `REVIEW_FEEDBACK.md` ...") rather than from each
    table cell -- the table's own cells do not restate it per row by
    design (it is written regardless of verdict, stated once for the
    whole state), so a literal per-cell substring search would otherwise
    under-count the APPROVE row. `ledger`/`phase_transition` presence is
    read from each row's own action cell: `ledger` from "record local
    stage"/"ledger entry" language, explicitly absent on "no ledger
    write"; `phase_transition` from an arrow (`→`) to a different state,
    explicitly absent on "no transition"."""
    if "Artifacts**: `REVIEW_FEEDBACK.md` (role: `local_model_plan_review`)" not in plan_text:
        raise AssertionError(
            "AWAITING_LOCAL_PLAN_REVIEW's Artifacts bullet no longer states REVIEW_FEEDBACK.md "
            "as its baseline artifact -- the table-side heuristic's baseline assumption is stale"
        )

    row_re = re.compile(
        r"\|\s*`AWAITING_LOCAL_PLAN_REVIEW`\s*\|\s*`(APPROVE|REVISE|BLOCK)`\s*\|\s*`/review-plan`\s*\|\s*([^|]+)\|"
    )
    rows = {verdict: action for verdict, action in row_re.findall(plan_text)}
    if set(rows) != {"APPROVE", "REVISE", "BLOCK"}:
        raise AssertionError(f"expected exactly 3 /review-plan transition-table rows, found: {sorted(rows)}")

    result = {}
    for verdict, action_cell in rows.items():
        tokens = {"REVIEW_FEEDBACK.md"}
        if "no ledger write" not in action_cell and (
            "record local stage" in action_cell or "ledger entry" in action_cell or "ledger fields" in action_cell
        ):
            tokens.add("ledger")
        if "no transition" not in action_cell and "→" in action_cell:
            tokens.add("phase_transition")
        result[verdict] = frozenset(tokens)
    return result


class TestReviewPlanWriteSetConsistencyLint(unittest.TestCase):
    """Missing-test item 108: items 85 and 96 (both long single-paragraph
    entries in `WORKFLOW_V2_PLAN.md`'s "Missing tests" section, located
    via `^85\\.`/`^96\\.` and read in full before writing this) and the
    D-Plan-Review-Stages verdict/state transition table's three
    `/review-plan` rows must describe the same write set per verdict. A
    future edit to any one of the three that silently drifts from the
    other two is caught here -- this is a documentation-consistency lint
    over this repository's own real plan doc (deterministic, stdlib-only,
    not tied to a moving base commit -- hence hermetic, unlike the
    `_demo_test.py` suites)."""

    def setUp(self):
        self.plan_text = (_repo_root() / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").read_text()

    def test_item_85_and_item_96_state_the_same_write_set_per_verdict(self):
        item85 = _extract_numbered_plan_doc_item(self.plan_text, 85)
        item96 = _extract_numbered_plan_doc_item(self.plan_text, 96)
        write_sets_85 = _write_set_by_verdict_from_plan_item(item85)
        write_sets_96 = _write_set_by_verdict_from_plan_item(item96)
        self.assertEqual(write_sets_85, write_sets_96)
        # Sanity: the extraction itself found real content, not empty sets
        # for every verdict (which would make the equality assertion
        # vacuous).
        self.assertTrue(any(write_sets_85.values()))

    def test_transition_table_agrees_with_items_85_and_96(self):
        item96 = _extract_numbered_plan_doc_item(self.plan_text, 96)
        write_sets_prose = _write_set_by_verdict_from_plan_item(item96)
        write_sets_table = _write_set_by_verdict_from_transition_table(self.plan_text)
        self.assertEqual(write_sets_prose, write_sets_table)

    def test_ledger_write_is_scoped_to_approve_only_across_all_three_sources(self):
        """A targeted regression guard for the specific property GPT-R12-
        001/002 fixed: only APPROVE ever writes the ledger, in every one
        of the three sources."""
        item96 = _extract_numbered_plan_doc_item(self.plan_text, 96)
        for source_name, write_sets in (
            ("item 96", _write_set_by_verdict_from_plan_item(item96)),
            ("transition table", _write_set_by_verdict_from_transition_table(self.plan_text)),
        ):
            self.assertIn("ledger", write_sets["APPROVE"], source_name)
            self.assertNotIn("ledger", write_sets["REVISE"], source_name)
            self.assertNotIn("ledger", write_sets["BLOCK"], source_name)


def _strip_trailing_citation_parenthetical(cell: str) -> str:
    """Strips a single trailing `(corrected/extended/new OPUS-R.../GPT-
    R...)` historical-citation parenthetical from a table cell, if
    present -- balance-aware (scanning backward from the end so an
    earlier, substantive parenthetical elsewhere in the same cell, e.g.
    `WFR-47`'s "(`WORKFLOW_STATE.json` and `<work_item_id>-artifacts.json`)",
    is never touched). Missing-test item 166's own stated normalization."""
    cell = cell.rstrip()
    if not cell.endswith(")"):
        return cell
    depth = 0
    for i in range(len(cell) - 1, -1, -1):
        ch = cell[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                inner = cell[i + 1 : -1]
                if re.match(r"(?i)^(corrected|extended|new)\b.*(OPUS-R|GPT-R)", inner):
                    return cell[:i].rstrip()
                return cell
    return cell


def _normalize_requirement_cell(cell: str) -> str:
    """Item 166's exact normalization: backtick markup, `**` bold markup,
    and a trailing citation parenthetical stripped; em dash normalized to
    `--`; case-insensitive; whitespace collapsed."""
    cell = _strip_trailing_citation_parenthetical(cell)
    cell = cell.replace("`", "").replace("**", "")
    cell = cell.replace("—", "--")
    cell = re.sub(r"\s+", " ", cell).strip()
    return cell.lower()


def _normalize_json_description(description: str) -> str:
    return re.sub(r"\s+", " ", description).strip().lower()


class TestRequirementsMappingTableConformance(unittest.TestCase):
    """Missing-test item 166 (`OPUS-R27-001`, `OPUS-R28-003`, ownership
    reassigned to `WF8b` by `GPT-R29-003`): a grep-based conformance test
    over `docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json`
    and the Requirements traceability table asserts every `WFR-*`
    requirement's JSON `description` matches its rendered table row's
    Requirement column, under the normalization the table's own
    introductory prose states -- the ongoing regression guard that keeps
    the two synced going forward, not the one-time data sync itself
    (already performed, revision 21). The Checkpoint column is out of
    scope by the same prose (it may cite a design-doc section instead of
    or alongside a registry checkpoint id)."""

    def setUp(self):
        self.plan_text = (_repo_root() / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").read_text()
        self.mapping = json.loads(
            (_repo_root() / "docs" / "ai-workflow" / "requirements"
             / "workflow-v2-1-core-mapping.json").read_text()
        )

    def _table_rows(self) -> dict[str, str]:
        rows = re.findall(r"^\| (WFR-\d+) \|(.*)\|.*\|.*\|$", self.plan_text, re.MULTILINE)
        return {req_id: requirement_cell for req_id, requirement_cell in rows}

    def test_every_wfr_row_description_matches_json_exactly(self):
        table_rows = self._table_rows()
        requirements = self.mapping["requirements"]
        # Sanity: the extraction found a non-empty set of rows whose count
        # matches the JSON side exactly (item 166, `WF8c` clause (f),
        # `OPUS-R113-001`: a hardcoded row count here went stale every time
        # a WFR was added -- 60 at revision 27, 69 as of this revision --
        # so this derives the expected size from `requirements` itself
        # instead of a literal that needs bumping forever). Both sides
        # non-empty guards the per-row loop below against passing vacuously
        # on two empty sets, which the set-equality check alone could not
        # rule out.
        self.assertGreater(len(requirements), 0)
        self.assertEqual(len(table_rows), len(requirements))
        self.assertEqual(set(table_rows), set(requirements))
        mismatches = []
        for req_id, table_cell in table_rows.items():
            table_normalized = _normalize_requirement_cell(table_cell)
            json_normalized = _normalize_json_description(requirements[req_id]["description"])
            if table_normalized != json_normalized:
                mismatches.append((req_id, table_normalized, json_normalized))
        self.assertEqual(
            mismatches, [],
            f"{len(mismatches)} WFR row(s) diverged from their JSON description: "
            f"{[m[0] for m in mismatches]}",
        )

    def test_normalization_catches_a_real_divergence_not_vacuously_true(self):
        """The positive test above proves nothing if the normalization is
        so loose it can never fail. Confirms it actually distinguishes a
        genuinely different description from the real WFR-01 row."""
        table_cell = self._table_rows()["WFR-01"]
        real_json_description = self.mapping["requirements"]["WFR-01"]["description"]
        tampered_description = real_json_description + " and something else entirely"
        self.assertNotEqual(
            _normalize_requirement_cell(table_cell),
            _normalize_json_description(tampered_description),
        )

    def test_trailing_citation_parenthetical_is_stripped_but_substantive_one_is_not(self):
        """WFR-47's own real table cell exercises both halves of the
        normalization rule at once: a substantive parenthetical
        mid-sentence (naming its two authoritative sources) must survive,
        while the trailing `(corrected ...)` citation parenthetical must
        not."""
        cell = self._table_rows()["WFR-47"]
        normalized = _normalize_requirement_cell(cell)
        self.assertIn("(workflow_state.json and <work_item_id>-artifacts.json)", normalized)
        self.assertNotIn("corrected", normalized)
        self.assertNotIn("opus-r25-002", normalized)


# ---------------------------------------------------------------------------
# GPT-R11-009: the two-stage plan-review ledger, exercised end to end
# against a real ScratchRepo. workflow_state_test.py's own
# TestRecordLocalPlanReview/TestRecordManualPlanReview classes already
# prove the dict-level transition logic (verified via
# `grep -n "transition_to_awaiting_local_plan_review" scripts/workflow_state_test.py`
# before writing this) -- not duplicated here; this chains the same
# writers against a real committed plan-doc tree and a genuinely
# recomputed review_content_id, matching what /review-plan and
# /record-manual-plan-review actually operate on.
# ---------------------------------------------------------------------------


class TestTwoStagePlanReviewIntegration(unittest.TestCase):
    def test_local_then_manual_approve_reaches_awaiting_plan_approval(self):
        with h.ScratchRepo() as repo:
            repo.write_plan_docs(work_item_id="wi")
            repo.commit_plan_docs_as_base()
            protected = h.plan_stage_protected_paths("wi")
            review_content_id, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi",
                plan_revision=1, protected=protected,
                excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            state = h.base_state(wi=h.base_work_item(
                work_item_id="wi", governing_workflow_version="2.1",
                phase="AWAITING_LOCAL_PLAN_REVIEW",
            ))

            state = ws.record_local_plan_review(
                state, "wi", verdict="APPROVE", bundle_id="b1",
                review_content_id=review_content_id, round=1, now="t1",
            )
            self.assertEqual(state["work_items"]["wi"]["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

            state = ws.record_manual_plan_review(
                state, "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id=review_content_id,
                feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id=review_content_id,
            )
            item = state["work_items"]["wi"]
            self.assertEqual(item["phase"], "AWAITING_PLAN_APPROVAL")
            self.assertTrue(ws.plan_approval_gate_reachable(
                latest_round_status="APPROVE", governing_workflow_version="2.1",
                plan_review_stages=item["plan_review_stages"],
                current_review_content_id=review_content_id,
            ))

    def test_local_then_manual_approve_reaches_awaiting_plan_approval_for_a_product_item(self):
        """R5: the product-typed counterpart of
        `test_local_then_manual_approve_reaches_awaiting_plan_approval`
        above, computing `review_content_id` via the resolver-based
        `compute_review_content_id_plan_stage_for_work_item` -- the same
        function `/review-plan`'s own step 5 ("recompute fresh... the
        plan-stage `review_content_id`") actually calls, unlike the raw
        `compute_review_content_id_plan_stage` the process-item test above
        uses, which never calls `resolve_plan_stage_metadata` and so would
        not demonstrate this defect's fix. This fails with
        `PlanStageNotApplicableError` against today's unfixed code (the
        resolver call is reached first, before any ledger function runs)
        and passes once `CP1`'s gate widening lands."""
        with h.ScratchRepo() as repo:
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            repo.write_plan_docs(work_item_id="prod-item")
            repo.write_workflow_state(
                active_work_item_id="prod-item",
                **{"prod-item": ws.default_work_item(
                    work_item_id="prod-item", work_item_type="product", work_item_kind="product",
                    plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                    registry_path="docs/ai-workflow/registry/prod-item-registry.json",
                    mapping_path="docs/ai-workflow/requirements/prod-item-mapping.json",
                    base_commit=repo.base, governing_workflow_version="2.1",
                    plan_revision=1, last_transition="t0",
                )},
            )
            repo.commit_plan_docs_as_base()

            review_content_id, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "prod-item",
            )

            state = h.base_state(**{"prod-item": h.base_work_item(
                work_item_id="prod-item", work_item_type="product",
                governing_workflow_version="2.1",
                phase="AWAITING_LOCAL_PLAN_REVIEW",
            )})

            state = ws.record_local_plan_review(
                state, "prod-item", verdict="APPROVE", bundle_id="b1",
                review_content_id=review_content_id, round=1, now="t1",
            )
            self.assertEqual(
                state["work_items"]["prod-item"]["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
            )

            state = ws.record_manual_plan_review(
                state, "prod-item", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id=review_content_id,
                feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id=review_content_id,
            )
            item = state["work_items"]["prod-item"]
            self.assertEqual(item["phase"], "AWAITING_PLAN_APPROVAL")
            self.assertTrue(ws.plan_approval_gate_reachable(
                latest_round_status="APPROVE", governing_workflow_version="2.1",
                plan_review_stages=item["plan_review_stages"],
                current_review_content_id=review_content_id,
            ))

    def test_a_real_committed_plan_edit_after_local_approval_invalidates_the_stage(self):
        """WFR-38's integration half: an actual committed edit to a
        protected plan document -- not a dict field mutation -- changes
        the real recomputed `review_content_id`, so the local-stage
        ledger entry recorded against the old id no longer satisfies
        `plan_approval_gate_reachable`, and -- since workflow-2.6.0, which
        retires `transition_to_awaiting_local_plan_review` -- the sole path
        back to `AWAITING_LOCAL_PLAN_REVIEW` is a `REVISE`, an edit, a
        publish of the edited content and `bind_plan_review_bundle`."""
        with h.ScratchRepo() as repo:
            repo.write_plan_docs(work_item_id="wi")
            repo.commit_plan_docs_as_base()
            protected = h.plan_stage_protected_paths("wi")
            old_id, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi",
                plan_revision=1, protected=protected,
                excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            state = h.base_state(wi=h.base_work_item(
                work_item_id="wi", governing_workflow_version="2.1",
                phase="AWAITING_LOCAL_PLAN_REVIEW",
            ))
            state = ws.record_local_plan_review(
                state, "wi", verdict="APPROVE", bundle_id="b1",
                review_content_id=old_id, round=1, now="t1",
            )
            self.assertEqual(state["work_items"]["wi"]["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text("plan v2\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "revise plan"], cwd=repo.root)

            new_id, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi",
                plan_revision=1, protected=protected,
                excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            self.assertNotEqual(old_id, new_id)

            item = state["work_items"]["wi"]
            self.assertFalse(ws.plan_approval_gate_reachable(
                latest_round_status="APPROVE", governing_workflow_version="2.1",
                plan_review_stages=item["plan_review_stages"],
                current_review_content_id=new_id,
            ))

            with self.assertRaises(ws.PlanReviewWriterRetiredError):
                ws.transition_to_awaiting_local_plan_review(state, "wi", now="t3")
            # A manual-external REVISE consumes the reviewed content; the
            # edited content is published and bound (its bundle's identity
            # is stubbed here -- the binding's verification is covered by
            # TestPlanReviewBundleBinding).
            state["work_items"]["wi"]["phase"] = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
            state = ws.record_manual_plan_review(
                state, "wi", verdict="REVISE", bundle_id="b1", round=1, now="t2",
                current_review_content_id=old_id, feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id=old_id,
            )
            self.assertEqual(state["work_items"]["wi"]["plan_review_binding"]["consumed"]["review_content_id"], old_id)
            plan_revision = state["work_items"]["wi"]["plan_revision"]
            state = ws.publish_plan_revision(state, "wi", plan_revision, "t3", review_content_id=new_id)
            state = ws.bind_plan_review_bundle(state, "wi", binding={
                "review_content_id": new_id, "bundle_id": "c" * 64, "plan_revision": plan_revision,
            }, now="t3")
            self.assertEqual(state["work_items"]["wi"]["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

            # And the stale ledger's own review_content_id still names the
            # old, now-superseded id -- never explicitly cleared.
            self.assertEqual(
                state["work_items"]["wi"]["plan_review_stages"]["review_content_id"], old_id,
            )


class TestPlanStageApprovalCommitMembership(unittest.TestCase):
    """WF8b evidence (real `/approve-review plan v2-1-dry-run` S5
    execution, 2026-08-12): the installed command's literal four-member
    plan-stage commit set omits `<work_item_id>-artifacts.json`, so
    `verify_post_approval_manifest_match` raises
    `MissingWorkItemArtifactsDeclarationError` *after* the state write and
    the commit, for any `"process"` work item whose declaration was never
    previously committed -- not only `workflow-v2-1-core`'s own case
    `D-Approval-Commits`' bootstrap procedure already covers. Exercises
    `workflow_fingerprint.resolve_plan_stage_approval_commit_paths` and
    `workflow_state.{stage_plan_approval_commit_paths,
    verify_staged_blob_sha256, verify_committed_blob_sha256,
    rollback_plan_approval_write}` end to end against real `ScratchRepo`
    git repositories -- generic, never hardcoded to a specific
    `work_item_id`."""

    def _commit_base_without_artifacts(self, repo: h.ScratchRepo, work_item_id: str) -> None:
        """Commits the plan/registry/mapping triad (plus a `.gitignore`
        excluding `.ai-review/`, mirroring this real repository's own --
        bundle-directory content must never itself become an unclassified
        changed path) but deliberately leaves
        `<work_item_id>-artifacts.json` untracked -- the exact real shape
        `v2-1-dry-run` was in (never committed, present only in the
        working tree) when S5 reproduced the defect."""
        (repo.root / ".gitignore").write_text(".ai-review/\n")
        paths = [
            ".gitignore",
            "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
            "docs/TECHNICAL_DECISIONS.md",
            f"docs/ai-workflow/registry/{work_item_id}-registry.json",
            f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
        ]
        _run(["git", "add", "--", *paths], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "settle plan docs, artifacts declaration pending"], cwd=repo.root)
        repo.base = repo.head()

    def _seed_bundle_capture(self, repo: h.ScratchRepo, work_item_id: str, artifacts_rel: str) -> Path:
        """Writes `<bundle_dir>/files/<artifacts_rel>` with the working
        tree's *current* bytes -- the same "final copies of changed
        files" `scripts/prepare-ai-review.sh` always captures, fresh
        (matching) by construction. Returns the captured file's path so a
        test can mutate it to simulate staleness."""
        bundle_dir = repo.root / ".ai-review" / work_item_id / "current"
        captured = bundle_dir / "files" / artifacts_rel
        captured.parent.mkdir(parents=True, exist_ok=True)
        captured.write_bytes((repo.root / artifacts_rel).read_bytes())
        return captured

    def _write_state(self, repo: h.ScratchRepo, work_item_id: str, governing_workflow_version: str) -> dict:
        work_item = h.base_work_item(
            work_item_id=work_item_id, governing_workflow_version=governing_workflow_version,
            phase="AWAITING_PLAN_APPROVAL", plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
            mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
            base_commit=repo.base,
        )
        repo.write_workflow_state(**{work_item_id: work_item})
        return work_item

    def test_declaration_never_committed_resolves_five_member_set(self):
        """The real defect's exact fixture: reproduces
        `MissingWorkItemArtifactsDeclarationError` on the *old* four-member
        commit, and proves the resolved five-member set fixes it."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
            self._seed_bundle_capture(repo, wi, artifacts_rel)

            plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
            )
            self.assertEqual(
                set(plan.paths),
                {
                    "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                    f"docs/ai-workflow/registry/{wi}-registry.json",
                    f"docs/ai-workflow/requirements/{wi}-mapping.json",
                    "docs/ai-workflow/WORKFLOW_STATE.json",
                    artifacts_rel,
                    # workflow-2.6.0 (`D-Plan-Approval-Closure`): every
                    # declared protected path is a member -- this fixture's
                    # declaration also protects the audit and decisions
                    # documents (re-pointed from 2.5.1's fixed four/five).
                    "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
                    "docs/TECHNICAL_DECISIONS.md",
                },
            )
            self.assertEqual(plan.artifacts_declaration_path, artifacts_rel)
            expected_sha = hashlib.sha256((repo.root / artifacts_rel).read_bytes()).hexdigest()
            self.assertEqual(plan.artifacts_declaration_sha256, expected_sha)

            protected = h.plan_stage_protected_paths(wi)
            review_content_id, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id=wi,
                plan_revision=1, protected=protected,
                excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )

            # --- regression guard: the OLD four-member commit (the
            # installed command's literal step-6 set, explicitly staged --
            # never `git add -A`, which would sweep the pending
            # declaration in and defeat the point of this fixture) really
            # does reproduce MissingWorkItemArtifactsDeclarationError
            # post-commit, exactly as the real S5 execution did ---
            base_four = (
                "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                f"docs/ai-workflow/registry/{wi}-registry.json",
                f"docs/ai-workflow/requirements/{wi}-mapping.json",
                "docs/ai-workflow/WORKFLOW_STATE.json",
            )
            _run(["git", "add", "--", *base_four], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "old four-member commit"], cwd=repo.root)
            old_commit = repo.head()
            with self.assertRaises(fingerprint.MissingWorkItemArtifactsDeclarationError):
                fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
                    repo.root, wi, old_commit, base=repo.base,
                )
            _run(["git", "reset", "--hard", repo.base], cwd=repo.root)
            self._write_state(repo, wi, "2.1")  # write_workflow_state doesn't survive the hard reset

            # --- the fixed five-member commit succeeds end to end ---
            ws.stage_plan_approval_commit_paths(repo.root, plan.paths)
            ws.verify_staged_blob_sha256(repo.root, plan.artifacts_declaration_path, plan.artifacts_declaration_sha256)
            _run(["git", "commit", "-q", "-m", "plan approval (five-member)"], cwd=repo.root)
            new_commit = repo.head()
            ws.assert_committed_path_set_matches(repo.root, new_commit, plan.paths)
            ws.verify_committed_blob_sha256(
                repo.root, new_commit, plan.artifacts_declaration_path, plan.artifacts_declaration_sha256,
            )

            record = ws.build_approval_record(
                basis="EXTERNAL_APPROVE", stage="plan", user_confirmation=f"I confirm plan approval for {wi}, plan stage.",
                now="t1", reviewed_bundle_id="b1", approved_review_content_id=review_content_id,
                review_content_manifest=[{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
            )
            work_item = {**h.base_work_item(work_item_id=wi, governing_workflow_version="2.1"), "plan_approval": record}
            ws.verify_post_approval_manifest_match(repo.root, work_item, stage="plan", base_commit=repo.base, commit=new_commit)

            self.assertTrue(ws.approval_is_current(repo.root, work_item, stage="plan", base_commit=repo.base, head=new_commit))

    def test_declaration_already_committed_and_unchanged_resolves_four_member_set(self):
        """The ordinary, steady-state round -- every round after a work
        item's first -- gains no gratuitous fifth member. Also the shape
        `workflow-v2-1-core`'s own already-committed declaration is in on
        every real round since its WFR-63 fix, proving this generic
        resolver reproduces that already-approved bootstrap behavior
        rather than regressing it."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            repo.commit_plan_docs_as_base()  # commits the artifacts declaration too
            self._write_state(repo, wi, "1")

            plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
            )
            self.assertEqual(
                set(plan.paths),
                {
                    "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                    f"docs/ai-workflow/registry/{wi}-registry.json",
                    f"docs/ai-workflow/requirements/{wi}-mapping.json",
                    "docs/ai-workflow/WORKFLOW_STATE.json",
                    # workflow-2.6.0 (`D-Plan-Approval-Closure`): every
                    # declared protected path is a member -- this fixture's
                    # declaration also protects the audit and decisions
                    # documents (re-pointed from 2.5.1's fixed four/five).
                    "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
                    "docs/TECHNICAL_DECISIONS.md",
                },
            )
            self.assertIsNone(plan.artifacts_declaration_path)
            self.assertIsNone(plan.artifacts_declaration_sha256)

    def test_stale_declaration_between_preflight_and_commit_refuses_before_any_mutation(self):
        """Condition 2 (freshness): a pending declaration whose bytes
        moved on again after the bundle was generated must refuse -- no
        staging, no commit, no state write -- rather than commit bytes no
        reviewer ever saw."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
            captured = self._seed_bundle_capture(repo, wi, artifacts_rel)
            # the bundle captured an OLDER version than what's in the
            # working tree right now (edited again after bundle generation).
            captured.write_text("stale bundle-captured bytes\n")

            status_before = _run(["git", "status", "--porcelain"], cwd=repo.root)
            with self.assertRaises(fingerprint.StaleArtifactsDeclarationError):
                fingerprint.resolve_plan_stage_approval_commit_paths(
                    repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
                )
            status_after = _run(["git", "status", "--porcelain"], cwd=repo.root)
            self.assertEqual(status_before, status_after)  # no mutation of any kind

    def test_declaration_required_but_missing_from_worktree_fails_before_any_mutation(self):
        """A `"process"` work item's plan-stage recomputation always
        requires its own declaration to exist somewhere -- if it is
        genuinely absent (never written at all, not merely uncommitted),
        the pre-existing `resolve_plan_stage_metadata` check this
        function reuses already fails closed, and this function never
        masks that with a different, more permissive error."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            (repo.root / f"docs/ai-workflow/registry/{wi}-artifacts.json").unlink()
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")

            with self.assertRaises(fingerprint.MissingWorkItemArtifactsDeclarationError):
                fingerprint.resolve_plan_stage_approval_commit_paths(
                    repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
                )

    def test_unexpected_changed_staged_path_refuses_before_commit(self):
        """A pre-existing, unrelated staged path with real changed content
        (this work item's own leftover or a concurrent work item's write,
        D1) must be caught and refused, never silently absorbed by a
        later pathspec-free `git commit`."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
            self._seed_bundle_capture(repo, wi, artifacts_rel)
            plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
            )

            (repo.root / "unrelated.txt").write_text("surprise\n")
            _run(["git", "add", "unrelated.txt"], cwd=repo.root)

            # Caught by the pre-staging index-isolation precondition --
            # DirtyIndexBeforeStagingError, not the post-staging diff
            # assertion, since the index is already dirty before this
            # call's own `git add` runs at all.
            with self.assertRaises(ws.DirtyIndexBeforeStagingError):
                ws.stage_plan_approval_commit_paths(repo.root, plan.paths)
            # nothing was committed
            self.assertEqual(repo.head(), repo.base)

    def test_restaging_unrelated_unchanged_content_is_a_true_no_op_not_a_gap(self):
        """Corrects an earlier (wrong) assumption made and disproven while
        stress-testing this fix: re-staging an unrelated path whose
        content is byte-identical to `HEAD` produces an index entry
        indistinguishable from `HEAD`'s own tree -- there is no separate
        Git-level state to detect here at all, so `git diff --cached
        HEAD` correctly reports nothing, neither before nor after. This
        is not a security gap; it is confirmation that the pre-staging
        precondition and the post-staging assertion are each checking a
        real, distinguishable condition, not papering over one that
        cannot occur."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
            self._seed_bundle_capture(repo, wi, artifacts_rel)
            plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
            )

            _run(["git", "add", "docs/ai-workflow/WORKFLOW_V2_AUDIT.md"], cwd=repo.root)
            self.assertEqual(
                _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root).strip(), "",
            )
            # Both checks correctly see nothing to refuse -- there is
            # genuinely nothing there.
            ws.stage_plan_approval_commit_paths(repo.root, plan.paths)

    def test_precondition_does_not_reject_this_work_items_own_pending_intent_to_add_paths(self):
        """Safety proof for the real defect this whole fix targets: the
        real `v2-1-dry-run` repository state that reproduced
        `MissingWorkItemArtifactsDeclarationError` had its own genuinely
        new (never committed) plan/registry/mapping/declaration paths
        sitting in the index as `git add -N` intent-to-add entries
        (empty-blob placeholders) -- `/milestone-plan`'s own documented
        staging step (S1's real evidence, `docs/ai-workflow/dry-run/
        WF8B_SCENARIOS.md`), run long before `/approve-review` ever
        executes. `git diff --cached --name-only HEAD` does not report
        intent-to-add entries at all (verified directly below), so the
        new pre-staging `DirtyIndexBeforeStagingError` precondition must
        not mistake this work item's own legitimate pending paths for an
        unrelated dirty index -- and this call's own subsequent `git add`
        must still upgrade every one of them from an empty-blob
        placeholder to its real content, exactly the four-or-five-member
        commit the resolved plan calls for."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            _run(["git", "add", ".gitignore"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "gitignore"], cwd=repo.root)
            repo.base = repo.head()
            repo.write_plan_docs(work_item_id=wi)  # plan/registry/mapping/artifacts all genuinely new
            self._write_state(repo, wi, "2.1")
            artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"

            # Mark every plan-stage file intent-to-add -- /milestone-plan's
            # own real staging step -- before the bundle even exists.
            intent_to_add_paths = (
                "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                f"docs/ai-workflow/registry/{wi}-registry.json",
                f"docs/ai-workflow/requirements/{wi}-mapping.json",
                artifacts_rel,
            )
            _run(["git", "add", "-N", "--", *intent_to_add_paths], cwd=repo.root)
            self.assertEqual(
                _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root).strip(), "",
            )

            self._seed_bundle_capture(repo, wi, artifacts_rel)
            plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
            )
            self.assertEqual(plan.artifacts_declaration_path, artifacts_rel)  # never committed -> pending

            # Must not raise DirtyIndexBeforeStagingError, and must
            # upgrade every intent-to-add placeholder to real content.
            ws.stage_plan_approval_commit_paths(repo.root, plan.paths)
            staged = set(_run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root).splitlines())
            self.assertEqual(staged, set(plan.paths))

    def test_wrong_staged_content_for_conditional_member_is_caught_before_commit(self):
        """A race between pinning the fifth member's digest and staging
        it (a hook, a concurrent edit) must be caught rather than let the
        commit silently carry bytes that were never freshness-checked."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
            self._seed_bundle_capture(repo, wi, artifacts_rel)
            plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
            )

            (repo.root / artifacts_rel).write_text("tampered after pinning\n")
            ws.stage_plan_approval_commit_paths(repo.root, plan.paths)  # stages the tampered bytes
            with self.assertRaises(ws.StagedBlobMismatchError):
                ws.verify_staged_blob_sha256(
                    repo.root, plan.artifacts_declaration_path, plan.artifacts_declaration_sha256,
                )

    def test_committed_path_set_mismatch_from_a_hook_editing_after_staging_is_caught(self):
        """A structural, post-commit-only failure mode the pre-commit
        staging checks cannot see by construction: a pre-commit hook
        that edits and re-stages an extra file *after*
        `stage_plan_approval_commit_paths` already verified the index,
        but before `git commit` writes the final tree, produces a commit
        whose own changed-path set is wider than what was verified.
        `assert_committed_path_set_matches` is the only one of this
        fix's checks positioned to catch it."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
            self._seed_bundle_capture(repo, wi, artifacts_rel)
            plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
            )
            ws.stage_plan_approval_commit_paths(repo.root, plan.paths)

            # Simulate a hook: stage one more, unrelated file directly via
            # Git, bypassing this fix's own staging function entirely --
            # exactly what a real pre-commit hook does.
            (repo.root / "hook-added.txt").write_text("added by a hook\n")
            _run(["git", "add", "hook-added.txt"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "commit widened by a hook"], cwd=repo.root)
            new_commit = repo.head()

            with self.assertRaises(ws.CommittedPathSetMismatchError):
                ws.assert_committed_path_set_matches(repo.root, new_commit, plan.paths)

    def test_rollback_before_commit_boundary_restores_state_bytes_exactly(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            state_path = Path("docs/ai-workflow/WORKFLOW_STATE.json")
            pre_write_bytes = (repo.root / state_path).read_bytes()

            (repo.root / state_path).write_bytes(pre_write_bytes + b"\n")  # simulate the state write
            head_before = repo.head()

            ws.rollback_plan_approval_write(repo.root, state_path, pre_write_bytes, commit_created=False)

            self.assertEqual((repo.root / state_path).read_bytes(), pre_write_bytes)
            self.assertEqual(repo.head(), head_before)  # no commit existed, none created

    def test_rollback_after_commit_boundary_undoes_exactly_one_commit(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            repo.write_plan_docs(work_item_id=wi)
            self._commit_base_without_artifacts(repo, wi)
            self._write_state(repo, wi, "2.1")
            state_path = Path("docs/ai-workflow/WORKFLOW_STATE.json")
            pre_write_bytes = (repo.root / state_path).read_bytes()
            head_before_write = repo.head()

            (repo.root / state_path).write_bytes(pre_write_bytes + b"\n")  # the state write
            _run(["git", "add", "--", str(state_path)], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "partial approval commit"], cwd=repo.root)
            self.assertNotEqual(repo.head(), head_before_write)

            ws.rollback_plan_approval_write(repo.root, state_path, pre_write_bytes, commit_created=True)

            self.assertEqual(repo.head(), head_before_write)  # exactly one commit undone
            self.assertEqual((repo.root / state_path).read_bytes(), pre_write_bytes)

    def test_governing_version_does_not_change_resolution_generic_across_v1_and_v21(self):
        """The resolver keys on `work_item_type` membership in
        `WORK_ITEM_TYPES` (`resolve_plan_stage_metadata`'s own existing
        rule), never on `governing_workflow_version` -- a `"1"` item (like
        `workflow-v2-1-core` itself) and a `"2.1"` item both go through
        the identical conditional-fifth-member logic."""
        for governing_workflow_version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=governing_workflow_version):
                with h.ScratchRepo() as repo:
                    wi = "wi"
                    repo.write_plan_docs(work_item_id=wi)
                    self._commit_base_without_artifacts(repo, wi)
                    self._write_state(repo, wi, governing_workflow_version)
                    artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
                    self._seed_bundle_capture(repo, wi, artifacts_rel)

                    plan = fingerprint.resolve_plan_stage_approval_commit_paths(
                        repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
                    )
                    self.assertEqual(plan.artifacts_declaration_path, artifacts_rel)


class TestPlanApprovalFailureAtomicityTransaction(unittest.TestCase):
    """WF8c (g), part 1 (`WFR-63`, missing-test items 349/350): the durable
    `PLAN_APPROVAL_JOURNAL`, the ownership assertion, the three-way outcome
    classifier, and the index-only rollback, exercised against real
    `ScratchRepo` git history end to end. Not yet wired into
    `.claude/commands/approve-review.md` -- these tests exercise
    `workflow_state.py`'s new library functions directly, the same way
    `TestPlanStageApprovalCommitMembership` above exercises the
    conditional-fifth-member mechanics it composes with."""

    def _setup(
        self, repo: h.ScratchRepo, wi: str, *, work_item_type: str = "process",
    ) -> tuple[dict, dict, str, fingerprint.PlanApprovalCommitPlan]:
        """Settles a clean, already-committed four-member plan-stage
        fixture (no pending fifth member -- kept simple; the fifth-member
        interaction is `resolve_plan_stage_approval_commit_paths`'s own
        already-tested concern, not this transaction's), writes
        `AWAITING_PLAN_APPROVAL` state, and returns
        `(pre_state, record, review_content_id, plan)`. `work_item_type`
        defaults to `"process"` (every existing caller unaffected)."""
        repo.write_plan_docs(work_item_id=wi)
        repo.commit_plan_docs_as_base()
        work_item = h.base_work_item(
            work_item_id=wi, work_item_type=work_item_type, work_item_kind=work_item_type,
            governing_workflow_version="1", phase="AWAITING_PLAN_APPROVAL",
            plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            registry_path=f"docs/ai-workflow/registry/{wi}-registry.json",
            mapping_path=f"docs/ai-workflow/requirements/{wi}-mapping.json",
            base_commit=repo.base,
        )
        repo.write_workflow_state(**{wi: work_item})
        pre_state = json.loads((repo.root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())

        plan = fingerprint.resolve_plan_stage_approval_commit_paths(
            repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
        )
        self.assertIsNone(plan.artifacts_declaration_path)  # confirms the simple four-member case

        protected = h.plan_stage_protected_paths(wi)
        review_content_id, _ = fingerprint.compute_review_content_id_plan_stage(
            repo.root, repo.base, work_item_type=work_item_type, work_item_id=wi,
            plan_revision=1, protected=protected,
            excluded_paths=h.plan_stage_excluded_paths(),
            excluded_prefixes=h.plan_stage_excluded_prefixes(),
        )
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan",
            user_confirmation=f"I confirm plan approval for {wi}, plan stage.", now="t1",
            reviewed_bundle_id="b1", approved_review_content_id=review_content_id,
            review_content_manifest=[{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
        )
        return pre_state, record, review_content_id, plan

    def _open_journal(self, repo: h.ScratchRepo, wi: str, pre_state: dict, record: dict,
                       review_content_id: str, plan: fingerprint.PlanApprovalCommitPlan) -> dict:
        return ws.open_plan_approval_journal(
            repo.root, work_item_id=wi, base_commit=repo.base, pre_state=pre_state,
            record=record, approval_now="t1", expected_bundle_id="b1",
            expected_review_content_id=review_content_id, applicable_paths=plan.paths,
            fifth_member_applies=plan.artifacts_declaration_path is not None,
            fifth_member_sha256=plan.artifacts_declaration_sha256,
            user_confirmation=f"I confirm plan approval for {wi}, plan stage.",
            quiescence_authorization=f"quiescence authorized for {wi} plan",
        )

    def _commit_plan_approval(self, repo: h.ScratchRepo, wi: str, plan, review_content_id: str) -> str:
        ws.stage_plan_approval_commit_paths(repo.root, plan.paths)
        body = f"plan approval\n\nWorkflow-Plan-Approval: {review_content_id}\nWorkflow-Work-Item: {wi}"
        _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
        return repo.head()

    # -- open/read/close -----------------------------------------------

    def test_open_journal_captures_expected_identity_and_state_bytes(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            head_before = repo.head()

            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            self.assertEqual(journal["schema_version"], 3)
            self.assertRegex(journal["owner_token"], r"^[0-9a-f]{32}$")
            self.assertEqual(journal["takeover_count"], 0)
            self.assertEqual(journal["previous_owner_tokens"], [])
            self.assertEqual(journal["work_item_id"], wi)
            self.assertEqual(journal["stage"], "plan")
            self.assertEqual(journal["pre_procedure_head"], head_before)
            self.assertEqual(journal["base_commit"], repo.base)
            self.assertEqual(journal["expected_review_content_id"], review_content_id)
            self.assertEqual(sorted(journal["applicable_paths"]), sorted(plan.paths))
            self.assertFalse(journal["fifth_member_applies"])
            self.assertIsNone(journal["fifth_member_sha256"])

            pre_bytes = base64.b64decode(journal["pre_procedure_state_b64"])
            self.assertEqual(pre_bytes, ws._serialize_state(pre_state))
            self.assertEqual(journal["pre_procedure_state_sha256"], hashlib.sha256(pre_bytes).hexdigest())

            expected_post_state = ws.apply_plan_approval(pre_state, wi, record, "t1")
            post_bytes = base64.b64decode(journal["expected_post_state_b64"])
            self.assertEqual(post_bytes, ws._serialize_state(expected_post_state))
            self.assertEqual(json.loads(post_bytes)["work_items"][wi]["phase"], "IMPLEMENTING")
            self.assertEqual(journal["expected_post_state_sha256"], hashlib.sha256(post_bytes).hexdigest())

            # Read back independently agrees.
            reread = ws.read_plan_approval_journal(repo.root)
            self.assertEqual(reread, journal)

    def test_journal_is_gitignored_and_invisible_to_git_status(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            _run(["git", "add", ".gitignore"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "gitignore"], cwd=repo.root)
            status_before = _run(["git", "status", "--porcelain"], cwd=repo.root)

            self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            status_after = _run(["git", "status", "--porcelain"], cwd=repo.root)
            self.assertEqual(status_after, status_before)

    def test_second_open_refuses_without_disturbing_the_first(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            first = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            with self.assertRaises(ws.PlanApprovalTransactionInProgressError):
                self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            self.assertEqual(ws.read_plan_approval_journal(repo.root), first)

    def test_read_journal_returns_none_when_absent(self):
        with h.ScratchRepo() as repo:
            self.assertIsNone(ws.read_plan_approval_journal(repo.root))

    def test_read_journal_rejects_wrong_schema_version(self):
        with h.ScratchRepo() as repo:
            path = ws.plan_approval_journal_path(repo.root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"schema_version": 1}))
            with self.assertRaises(ws.PlanApprovalJournalUnavailableError):
                ws.read_plan_approval_journal(repo.root)

    def test_read_journal_rejects_missing_fields(self):
        with h.ScratchRepo() as repo:
            path = ws.plan_approval_journal_path(repo.root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"schema_version": 3, "owner_token": "a" * 32}))
            with self.assertRaises(ws.PlanApprovalJournalUnavailableError):
                ws.read_plan_approval_journal(repo.root)

    def test_close_journal_is_idempotent(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            ws.close_plan_approval_journal(repo.root)
            self.assertIsNone(ws.read_plan_approval_journal(repo.root))
            ws.close_plan_approval_journal(repo.root)  # no error on an already-absent journal

    # -- ownership --------------------------------------------------------

    def test_assert_owner_succeeds_for_correct_token_and_fails_for_wrong_token(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            reread = ws.assert_plan_approval_journal_owner(repo.root, journal["owner_token"])
            self.assertEqual(reread, journal)

            with self.assertRaises(ws.PlanApprovalOwnershipError):
                ws.assert_plan_approval_journal_owner(repo.root, "0" * 32)

    def test_assert_owner_raises_when_no_transaction_open(self):
        with h.ScratchRepo() as repo:
            with self.assertRaises(ws.NoPlanApprovalTransactionError):
                ws.assert_plan_approval_journal_owner(repo.root, "0" * 32)

    # -- classifier ---------------------------------------------------

    def test_classify_not_committed_when_nothing_happened(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED,
            )

    def test_classify_committed_when_the_matching_commit_lands_directly_on_pre_procedure_head(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            self._commit_plan_approval(repo, wi, plan, review_content_id)

            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_COMMITTED,
            )

    def test_classify_ambiguous_when_an_unrelated_commit_lands_between_journal_open_and_the_approval_commit(self):
        """Real provenance concern: if another commit slips in between
        this transaction's own pinned `pre_procedure_head` and the
        eventual approval commit, the approval commit's first parent no
        longer matches what the journal pinned -- a concurrent writer
        may have interleaved, so this must never be silently treated as
        this transaction's own success."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            (repo.root / "unrelated.txt").write_text("interleaved commit\n")
            _run(["git", "add", "unrelated.txt"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "an unrelated commit lands first"], cwd=repo.root)

            self._commit_plan_approval(repo, wi, plan, review_content_id)

            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_AMBIGUOUS,
            )

    def test_classify_ambiguous_when_head_moved_but_no_matching_commit_exists(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            (repo.root / "unrelated.txt").write_text("head moved, no approval commit\n")
            _run(["git", "add", "unrelated.txt"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "unrelated"], cwd=repo.root)

            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_AMBIGUOUS,
            )

    def test_classify_ambiguous_when_the_trailer_search_itself_is_ambiguous(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            for subject in ("approve-a", "approve-b"):
                repo.commit(subject, trailers={
                    "Workflow-Plan-Approval": review_content_id, "Workflow-Work-Item": wi,
                })

            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_AMBIGUOUS,
            )

    # -- rollback -------------------------------------------------------

    def test_rollback_unstages_and_closes_the_journal_when_not_committed(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            head_before = repo.head()

            # Simulate a failure partway through staging (item 350's
            # "rollback after a staged-set/blob failure" scenario).
            ws.stage_plan_approval_commit_paths(repo.root, plan.paths)
            self.assertNotEqual(
                _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root).strip(), "",
            )

            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED,
            )
            ws.rollback_plan_approval_transaction(repo.root, owner_token=journal["owner_token"])

            self.assertEqual(
                _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root).strip(), "",
            )
            self.assertEqual(repo.head(), head_before)
            self.assertIsNone(ws.read_plan_approval_journal(repo.root))

    def test_rollback_refuses_for_the_wrong_owner_token(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            with self.assertRaises(ws.PlanApprovalOwnershipError):
                ws.rollback_plan_approval_transaction(repo.root, owner_token="0" * 32)
            # journal survives an unauthorized rollback attempt
            self.assertEqual(ws.read_plan_approval_journal(repo.root), journal)

    def test_rollback_refuses_when_live_state_already_carries_the_transactions_own_identity(self):
        """The invariant-violation safety net: if the live
        `WORKFLOW_STATE.json` already shows this exact transaction's
        post-approval identity under `phase: IMPLEMENTING` -- e.g. a
        step-8b-equivalent materialization ran out of band -- rollback
        must refuse rather than silently discard what may be the only
        record that the approval actually took effect."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            post_state = ws.apply_plan_approval(pre_state, wi, record, "t1")
            (repo.root / "docs/ai-workflow/WORKFLOW_STATE.json").write_text(
                json.dumps(post_state, indent=2),
            )

            with self.assertRaises(ws.PlanApprovalRollbackInvariantViolationError):
                ws.rollback_plan_approval_transaction(repo.root, owner_token=journal["owner_token"])
            # journal is left in place, not closed
            self.assertIsNotNone(ws.read_plan_approval_journal(repo.root))

    def test_rollback_verification_failure_when_head_has_moved_leaves_the_journal_in_place(self):
        """Defense in depth against calling rollback without classifying
        first: if `HEAD` has genuinely moved past `pre_procedure_head`
        (e.g. a concurrent commit landed), a bare `git reset --mixed
        HEAD` cannot and must not be reported as having restored the
        pre-transaction state."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

            (repo.root / "unrelated.txt").write_text("a concurrent commit\n")
            _run(["git", "add", "unrelated.txt"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "concurrent"], cwd=repo.root)

            with self.assertRaises(ws.PlanApprovalRollbackVerificationError):
                ws.rollback_plan_approval_transaction(repo.root, owner_token=journal["owner_token"])
            # journal is left in place, not closed
            self.assertIsNotNone(ws.read_plan_approval_journal(repo.root))

    def test_rollback_also_closes_the_owner_progress_record(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=journal["owner_token"], step="step-5-declaration-pin", now="t2",
            ):
                pass
            self.assertIsNotNone(ws.read_plan_approval_owner_progress(repo.root, journal["owner_token"]))

            ws.rollback_plan_approval_transaction(repo.root, owner_token=journal["owner_token"])

            self.assertIsNone(ws.read_plan_approval_owner_progress(repo.root, journal["owner_token"]))

    def test_journal_read_rejects_malformed_takeover_count_and_previous_owner_tokens(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            path = ws.plan_approval_journal_path(repo.root)

            bad = dict(journal)
            bad["takeover_count"] = "0"  # a string, not an int
            path.write_text(json.dumps(bad))
            with self.assertRaises(ws.PlanApprovalJournalUnavailableError):
                ws.read_plan_approval_journal(repo.root)

            bad = dict(journal)
            bad["previous_owner_tokens"] = "none"  # not a list
            path.write_text(json.dumps(bad))
            with self.assertRaises(ws.PlanApprovalJournalUnavailableError):
                ws.read_plan_approval_journal(repo.root)


class TestPlanApprovalMutationGuardAndTakeover(unittest.TestCase):
    """WF8c (g), part 2 (`D-Approval-Commits`' "Owner progress record" /
    "Transaction mutation/handoff guard" / "Refuse by default; takeover is
    explicit", revision 57-59): the fixed-shape guarded-mutation window,
    the owner progress record, and the explicit-takeover contract,
    exercised directly against part 1's journal against real `ScratchRepo`
    git history. Mirrors `D-Checkpoint-Ownership`'s own already-tested
    guard/takeover test shapes (see the class above this one's peers in
    `workflow_state_test.py`) rather than inventing new coverage
    technique."""

    def _setup(self, repo: h.ScratchRepo, wi: str = "wi"):
        return TestPlanApprovalFailureAtomicityTransaction._setup(self, repo, wi)

    def _open_journal(self, repo: h.ScratchRepo, wi: str, *args):
        return TestPlanApprovalFailureAtomicityTransaction._open_journal(self, repo, wi, *args)

    def _open(self, repo: h.ScratchRepo, wi: str = "wi") -> dict:
        pre_state, record, review_content_id, plan = self._setup(repo, wi)
        return self._open_journal(repo, wi, pre_state, record, review_content_id, plan)

    # -- step classification ---------------------------------------------

    def test_step_classification_is_exhaustive_and_refuses_an_unknown_step(self):
        for step in ws.PLAN_APPROVAL_DESTRUCTIVE_STEPS:
            self.assertEqual(ws.plan_approval_step_class(step), ws.DESTRUCTIVE)
        for step in ws.PLAN_APPROVAL_ORDINARY_STEPS:
            self.assertEqual(ws.plan_approval_step_class(step), ws.ORDINARY)
        self.assertTrue(ws.PLAN_APPROVAL_DESTRUCTIVE_STEPS.isdisjoint(ws.PLAN_APPROVAL_ORDINARY_STEPS))
        with self.assertRaises(ws.PlanApprovalGuardUnavailableError):
            ws.plan_approval_step_class("step-does-not-exist")

    # -- guard publish/read/release ---------------------------------------

    def test_guard_publish_read_release_round_trip(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))

            lease = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=journal["owner_token"],
                step="step-5-declaration-pin", now="t2",
            )
            self.assertRegex(lease["lease_id"], r"^[0-9a-f]{32}$")
            self.assertEqual(lease["holder_owner_token"], journal["owner_token"])
            self.assertEqual(lease["step"], "step-5-declaration-pin")
            self.assertEqual(lease["step_class"], ws.ORDINARY)
            self.assertEqual(ws.read_plan_approval_guard(repo.root), lease)

            ws.release_plan_approval_guard(repo.root, lease)
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            ws.release_plan_approval_guard(repo.root, lease)  # idempotent

    def test_guard_is_gitignored_and_invisible_to_git_status(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            _run(["git", "add", ".gitignore"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "gitignore"], cwd=repo.root)
            status_before = _run(["git", "status", "--porcelain"], cwd=repo.root)

            lease = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=journal["owner_token"],
                step="step-5-declaration-pin", now="t2",
            )
            self.assertEqual(_run(["git", "status", "--porcelain"], cwd=repo.root), status_before)
            ws.release_plan_approval_guard(repo.root, lease)

    def test_owner_reclaims_its_own_leftover_guard_and_retries_once(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            stale = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=journal["owner_token"],
                step="step-5-declaration-pin", now="t2",
            )
            # Simulate an interrupted earlier step: the guard is left
            # behind, never released.
            fresh = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=journal["owner_token"],
                step="step-6.2-stage-ordinary", now="t3",
            )
            self.assertNotEqual(fresh["lease_id"], stale["lease_id"])
            self.assertEqual(ws.read_plan_approval_guard(repo.root), fresh)

    def test_acquire_guard_refuses_when_held_under_the_current_epoch_by_a_different_token(self):
        """Defense in depth for `role="owner"`: a live guard whose
        `holder_owner_token` equals the journal's own current epoch but
        does *not* equal the token this call presents is never this
        session's own leftover -- refuse rather than reclaim."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            live = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=journal["owner_token"],
                step="step-5-declaration-pin", now="t2",
            )
            with self.assertRaises(ws.PlanApprovalGuardHeldError):
                ws.acquire_plan_approval_guard(
                    repo.root, holder_owner_token="0" * 32,
                    step="step-6.2-stage-ordinary", now="t3",
                )
            self.assertEqual(ws.read_plan_approval_guard(repo.root), live)

    # -- guarded_mutation fixed window -------------------------------------

    def test_guarded_mutation_happy_path_mutates_advances_progress_and_releases(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            calls = []
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=journal["owner_token"], step="step-5-declaration-pin", now="t2",
            ) as lease:
                calls.append(lease["step"])
                self.assertEqual(ws.read_plan_approval_guard(repo.root)["lease_id"], lease["lease_id"])

            self.assertEqual(calls, ["step-5-declaration-pin"])
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            progress = ws.read_plan_approval_owner_progress(repo.root, journal["owner_token"])
            self.assertEqual(progress["step"], "step-5-declaration-pin")
            self.assertEqual(progress["step_seq"], 1)
            self.assertEqual(progress["updated_at"], "t2")

    def test_guarded_mutation_releases_guard_without_advancing_progress_when_body_raises(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)

            class _Boom(Exception):
                pass

            with self.assertRaises(_Boom):
                with ws.plan_approval_guarded_mutation(
                    repo.root, owner_token=journal["owner_token"], step="step-5-declaration-pin", now="t2",
                ):
                    raise _Boom()

            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            self.assertIsNone(ws.read_plan_approval_owner_progress(repo.root, journal["owner_token"]))

    def test_guarded_mutation_releases_guard_without_advancing_progress_when_owner_assertion_fails(self):
        """A displaced owner still holding its old token: guard
        acquisition succeeds trivially (no guard is held), but
        `assert_owner` inside the window fails because a takeover already
        rotated the journal -- the guard must still be released and no
        progress record written under the stale token."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            old_token = journal["owner_token"]
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            new_token = ws.take_over_plan_approval_transaction(
                repo.root, work_item_id="wi", now="t2", evidence=evidence,
                user_authorization=ws.plan_approval_takeover_authorization_literal(evidence),
            )
            self.assertNotEqual(new_token, old_token)

            with self.assertRaises(ws.PlanApprovalOwnershipError):
                with ws.plan_approval_guarded_mutation(
                    repo.root, owner_token=old_token, step="step-5-declaration-pin", now="t3",
                ):
                    self.fail("must not reach the mutation body under a rotated-away token")

            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            self.assertIsNone(ws.read_plan_approval_owner_progress(repo.root, old_token))

    def test_owner_progress_step_seq_is_monotonic_across_multiple_guarded_mutations(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            owner_token = journal["owner_token"]
            for i, step in enumerate(
                ("step-5-declaration-pin", "step-6.1b-state-pin", "step-6.2-stage-ordinary"), start=1,
            ):
                with ws.plan_approval_guarded_mutation(
                    repo.root, owner_token=owner_token, step=step, now=f"t{i}",
                ):
                    pass
                progress = ws.read_plan_approval_owner_progress(repo.root, owner_token)
                self.assertEqual(progress["step_seq"], i)
                self.assertEqual(progress["step"], step)

    # -- takeover -----------------------------------------------------------

    def test_takeover_refuses_when_no_transaction_open(self):
        with h.ScratchRepo() as repo:
            with self.assertRaises(ws.NoPlanApprovalTransactionError):
                ws.take_over_plan_approval_transaction(
                    repo.root, work_item_id="wi", now="t1", user_authorization="whatever",
                )

    def test_takeover_refuses_a_journal_belonging_to_a_different_work_item(self):
        """Convergence repair, optional finding 3. The plan-approval
        journal is a single, repository-wide object, so
        `/approve-review B plan` observes an interrupted transaction
        belonging to work item A exactly as readily as one of its own --
        and every step from `6a` onward then drives A's pinned record,
        A's paths and A's expected post-state under B's invocation. The
        authorization literal names A, but naming A is not the same as
        checking that the operator meant A: before this repair nothing
        compared the two, and the takeover succeeded. It must fail closed,
        before the claim, the guard and the rotation -- and before any
        journal closure or materialization."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo, wi="wi")
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            literal = ws.plan_approval_takeover_authorization_literal(evidence)
            with self.assertRaises(ws.PlanApprovalTakeoverWorkItemMismatchError) as ctx:
                ws.take_over_plan_approval_transaction(
                    repo.root, work_item_id="some-other-item", now="t2",
                    evidence=evidence, user_authorization=literal,
                )
            message = str(ctx.exception)
            self.assertIn("wi", message)
            self.assertIn("some-other-item", message)
            # Nothing was mutated: same owner, no rotation, no claim left
            # behind, and the transaction is still takeable by its own
            # rightful target.
            unchanged = ws.read_plan_approval_journal(repo.root)
            self.assertEqual(unchanged["owner_token"], journal["owner_token"])
            self.assertEqual(unchanged["takeover_count"], 0)
            runtime_dir = (repo.root / ws.PLAN_APPROVAL_JOURNAL_PATH).parent
            self.assertEqual(
                sorted(q.name for q in runtime_dir.glob("PLAN_APPROVAL_JOURNAL.claim.*")), [],
            )
            new_token = ws.take_over_plan_approval_transaction(
                repo.root, work_item_id="wi", now="t3",
                evidence=evidence, user_authorization=literal,
            )
            self.assertNotEqual(new_token, journal["owner_token"])

    def test_takeover_work_item_mismatch_is_a_refusal_subclass(self):
        """It is the same refusal, at the same point, as every other
        takeover refusal -- not a new takeover mode -- so a caller already
        catching `PlanApprovalTakeoverRefusedError` catches this too."""
        self.assertTrue(issubclass(
            ws.PlanApprovalTakeoverWorkItemMismatchError, ws.PlanApprovalTakeoverRefusedError,
        ))

    def test_takeover_requires_the_exact_authorization_literal(self):
        with h.ScratchRepo() as repo:
            self._open(repo)
            with self.assertRaises(ws.PlanApprovalTakeoverRefusedError):
                ws.take_over_plan_approval_transaction(
                    repo.root, work_item_id="wi", now="t2", user_authorization="not the right literal",
                )
            # nothing mutated -- the journal's owner_token is unchanged
            self.assertIsNotNone(ws.read_plan_approval_journal(repo.root))

    def test_takeover_happy_path_rotates_token_and_records_history(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            old_token = journal["owner_token"]
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            self.assertEqual(evidence["owner_token"], old_token)
            self.assertIsNone(evidence["progress"])
            self.assertIsNone(evidence["guard"])
            literal = ws.plan_approval_takeover_authorization_literal(evidence)
            self.assertIn(old_token, literal)
            self.assertIn("step_seq none", literal)

            new_token = ws.take_over_plan_approval_transaction(
                repo.root, work_item_id="wi", now="t2", evidence=evidence, user_authorization=literal,
            )

            reread = ws.read_plan_approval_journal(repo.root)
            self.assertEqual(reread["owner_token"], new_token)
            self.assertEqual(reread["takeover_count"], 1)
            self.assertEqual(reread["previous_owner_tokens"], [old_token])
            # every other field carried over byte-identically
            for key in reread:
                if key in ("owner_token", "takeover_count", "previous_owner_tokens"):
                    continue
                self.assertEqual(reread[key], journal[key], key)
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))  # released at 5a

    def test_displaced_owners_next_assertion_fails_after_takeover(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            old_token = journal["owner_token"]
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            ws.take_over_plan_approval_transaction(
                repo.root, work_item_id="wi", now="t2", evidence=evidence,
                user_authorization=ws.plan_approval_takeover_authorization_literal(evidence),
            )
            with self.assertRaises(ws.PlanApprovalOwnershipError):
                ws.assert_plan_approval_journal_owner(repo.root, old_token)

    def test_takeover_refuses_when_progress_advanced_between_observe_and_claim(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            owner_token = journal["owner_token"]
            evidence = ws.plan_approval_takeover_evidence(repo.root)  # observes: no progress yet
            literal = ws.plan_approval_takeover_authorization_literal(evidence)

            # The "owner" makes real progress before the takeover claims.
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-5-declaration-pin", now="t2",
            ):
                pass

            with self.assertRaises(ws.PlanApprovalTakeoverRefusedError):
                ws.take_over_plan_approval_transaction(
                    repo.root, work_item_id="wi", now="t3", evidence=evidence, user_authorization=literal,
                )
            # nothing mutated -- still the original owner
            self.assertEqual(ws.read_plan_approval_journal(repo.root)["owner_token"], owner_token)

    def test_takeover_refuses_when_guard_is_destructive(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            owner_token = journal["owner_token"]
            ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token, step="step-6.5-commit", now="t2",
            )
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            self.assertEqual(evidence["guard"]["step_class"], ws.DESTRUCTIVE)
            literal = ws.plan_approval_takeover_authorization_literal(evidence)

            with self.assertRaises(ws.PlanApprovalTakeoverRefusedError):
                ws.take_over_plan_approval_transaction(
                    repo.root, work_item_id="wi", now="t3", evidence=evidence, user_authorization=literal,
                    guard_release_authorization=ws.plan_approval_guard_release_authorization_literal(
                        evidence["guard"],
                    ),
                )
            # the destructive guard survives untouched
            self.assertEqual(
                ws.read_plan_approval_guard(repo.root)["step"], "step-6.5-commit",
            )
            self.assertEqual(ws.read_plan_approval_journal(repo.root)["owner_token"], owner_token)

    def test_takeover_authorized_break_of_an_ordinary_abandoned_guard(self):
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            owner_token = journal["owner_token"]
            abandoned = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token, step="step-5-declaration-pin", now="t2",
            )
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            self.assertEqual(evidence["guard"], abandoned)
            literal = ws.plan_approval_takeover_authorization_literal(evidence)
            guard_release = ws.plan_approval_guard_release_authorization_literal(abandoned)

            new_token = ws.take_over_plan_approval_transaction(
                repo.root, work_item_id="wi", now="t3", evidence=evidence, user_authorization=literal,
                guard_release_authorization=guard_release,
            )

            self.assertEqual(ws.read_plan_approval_journal(repo.root)["owner_token"], new_token)
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))  # released at 5a

    def test_takeover_refuses_ordinary_guard_release_when_lease_id_does_not_match(self):
        """A changed `lease_id` proves the owner released and re-acquired
        between observation and takeover -- i.e. is demonstrably live."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            owner_token = journal["owner_token"]
            first = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token, step="step-5-declaration-pin", now="t2",
            )
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            literal = ws.plan_approval_takeover_authorization_literal(evidence)
            guard_release = ws.plan_approval_guard_release_authorization_literal(first)

            ws.release_plan_approval_guard(repo.root, first)
            second = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token, step="step-6.2-stage-ordinary", now="t3",
            )
            self.assertNotEqual(second["lease_id"], first["lease_id"])

            with self.assertRaises(ws.PlanApprovalTakeoverRefusedError):
                ws.take_over_plan_approval_transaction(
                    repo.root, work_item_id="wi", now="t4", evidence=evidence, user_authorization=literal,
                    guard_release_authorization=guard_release,
                )
            self.assertEqual(ws.read_plan_approval_guard(repo.root), second)

    def test_a_stale_takeover_against_an_already_superseded_epoch_refuses(self):
        """Two takeover attempts built from the same (now-stale)
        evidence: the first succeeds and rotates the token; the second,
        replaying the same authorization for the epoch it superseded,
        must refuse rather than silently rotating again or double-
        counting `takeover_count`."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            literal = ws.plan_approval_takeover_authorization_literal(evidence)

            first_new_token = ws.take_over_plan_approval_transaction(
                repo.root, work_item_id="wi", now="t2", evidence=evidence, user_authorization=literal,
            )
            with self.assertRaises(ws.PlanApprovalTakeoverInProgressError):
                ws.take_over_plan_approval_transaction(
                    repo.root, work_item_id="wi", now="t3", evidence=evidence, user_authorization=literal,
                )
            self.assertEqual(ws.read_plan_approval_journal(repo.root)["owner_token"], first_new_token)
            self.assertEqual(ws.read_plan_approval_journal(repo.root)["takeover_count"], 1)

    def test_a_dead_claim_from_a_crashed_same_epoch_takeover_is_cleared_and_retried(self):
        """A takeover attempt that died between step 3 (claim) and step 5
        (rotate) leaves a claim link behind while the journal still
        carries the same token -- provably having mutated nothing. A
        fresh takeover of that same epoch clears it and proceeds rather
        than refusing forever."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            owner_token = journal["owner_token"]
            full_journal_path = ws.plan_approval_journal_path(repo.root)
            dead_claim = full_journal_path.parent / f"PLAN_APPROVAL_JOURNAL.claim.{owner_token}"
            os.link(full_journal_path, dead_claim)
            self.assertTrue(dead_claim.exists())

            evidence = ws.plan_approval_takeover_evidence(repo.root)
            new_token = ws.take_over_plan_approval_transaction(
                repo.root, work_item_id="wi", now="t2", evidence=evidence,
                user_authorization=ws.plan_approval_takeover_authorization_literal(evidence),
            )
            self.assertEqual(ws.read_plan_approval_journal(repo.root)["owner_token"], new_token)

    def test_takeover_evidence_reports_absent_transaction(self):
        with h.ScratchRepo() as repo:
            evidence = ws.plan_approval_takeover_evidence(repo.root)
            self.assertIsNone(evidence["journal"])
            self.assertIsNone(evidence["owner_token"])
            self.assertIsNone(evidence["progress"])
            self.assertIsNone(evidence["guard"])
            self.assertIsNone(evidence["outcome"])

    # -- WF8c item 352: /bootstrap-workflow-v2's own step-0 precondition ---

    def test_takeover_evidence_reports_a_fresh_unstarted_transaction_for_step_0(self):
        """`/bootstrap-workflow-v2`'s own step 0 (`WF8c` item 352) reads
        exactly `plan_approval_takeover_evidence` before ever calling
        `state-sync`; a transaction just opened, before any guarded
        mutation has run, must report a real `owner_token`,
        `outcome == NOT_COMMITTED` (nothing committed, HEAD unmoved),
        `progress is None` (no step completed yet), and `guard is None`
        (not currently held) -- exactly the four values step 0's own text
        names. Read-only: confirmed via an explicit before/after
        `git status --short` comparison, since step 0 must never mutate
        anything itself, only observe and (when a journal exists) stop."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            before = _run(["git", "status", "--short"], cwd=repo.root)

            evidence = ws.plan_approval_takeover_evidence(repo.root)

            after = _run(["git", "status", "--short"], cwd=repo.root)
            self.assertEqual(before, after)
            self.assertIsNotNone(evidence["journal"])
            self.assertEqual(evidence["owner_token"], journal["owner_token"])
            self.assertEqual(evidence["outcome"], ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED)
            self.assertIsNone(evidence["progress"])
            self.assertIsNone(evidence["guard"])

    def test_takeover_evidence_reports_last_completed_step_and_held_guard_for_step_0(self):
        """A transaction interrupted mid-flight -- one guarded mutation
        already completed, a second one's guard left held (simulating a
        crash inside that step's own mutation body) -- is exactly what a
        real abandoned transaction looks like to a fresh session's step 0.
        `plan_approval_takeover_evidence` must surface both facts: the
        last-*completed* step (never a step merely started), and the
        currently-held guard's own step/class, so step 0's report names
        both without step 0 itself acquiring or releasing anything."""
        with h.ScratchRepo() as repo:
            journal = self._open(repo)
            owner_token = journal["owner_token"]
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-5-declaration-pin", now="t2",
            ):
                pass
            # A second guarded step starts but never completes (crash
            # inside the mutation body, left holding the guard).
            ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token,
                step="step-6.1b-state-pin", now="t3",
            )

            evidence = ws.plan_approval_takeover_evidence(repo.root)

            self.assertEqual(evidence["owner_token"], owner_token)
            self.assertEqual(evidence["outcome"], ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED)
            self.assertEqual(evidence["progress"]["step"], "step-5-declaration-pin")
            self.assertEqual(evidence["guard"]["step"], "step-6.1b-state-pin")
            self.assertEqual(evidence["guard"]["step_class"], ws.ORDINARY)


_STATE_PATH = Path("docs/ai-workflow/WORKFLOW_STATE.json")


class TestPlanApprovalStateBlobPinAndMaterialize(unittest.TestCase):
    """WF8c (g), part 3 (`WFR-63`): the index-pinned-blob writer for
    `WORKFLOW_STATE.json` (step-6.1b-state-pin) and step 8b's
    materialization, exercised directly against real `ScratchRepo` git
    history the same way parts 1 and 2 exercise the journal and the
    guard/takeover contract -- not yet wired into a guarded-mutation
    caller or `.claude/commands/approve-review.md`.

    `TestPlanApprovalFailureAtomicityTransaction._setup` leaves
    `WORKFLOW_STATE.json` written but *uncommitted* (parts 1/2 never
    needed it at `HEAD`, since the journal captures `pre_state` as a
    plain dict). This part's own primitives read the file's mode *at
    `HEAD`*, matching this repository's own real invariant --
    `WORKFLOW_STATE.json` is always already tracked before any approval
    round begins -- so `_setup` below commits it first, before the
    journal opens (its own `pre_procedure_head` must be pinned after
    that commit, not before, or the classifier would see a spurious
    concurrent commit)."""

    def _setup(self, repo: h.ScratchRepo, wi: str, *, work_item_type: str = "process"):
        pre_state, record, review_content_id, plan = TestPlanApprovalFailureAtomicityTransaction._setup(
            self, repo, wi, work_item_type=work_item_type,
        )
        _run(["git", "add", str(_STATE_PATH)], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "seed workflow state"], cwd=repo.root)
        return pre_state, record, review_content_id, plan

    def _open_journal(self, repo: h.ScratchRepo, wi: str, *args):
        return TestPlanApprovalFailureAtomicityTransaction._open_journal(self, repo, wi, *args)

    def _expected_bytes(self, journal: dict) -> bytes:
        return base64.b64decode(journal["expected_post_state_b64"])

    def _commit_pinned_state(
        self, repo: h.ScratchRepo, wi: str, plan, journal: dict, review_content_id: str,
    ) -> str:
        """Stages `plan.paths` minus `_STATE_PATH` via the ordinary `git
        add` mechanism, pins the state blob directly into the index,
        verifies both, and creates the approval commit -- a full
        end-to-end pass through this part's own primitives (not
        necessarily the eventual production step ordering, which remains
        a follow-up session's scope to fix in place)."""
        ordinary_paths = tuple(p for p in plan.paths if p != str(_STATE_PATH))
        ws.stage_plan_approval_commit_paths(repo.root, ordinary_paths)
        ws.pin_plan_approval_state_blob(repo.root, self._expected_bytes(journal))
        ws.verify_staged_plan_approval_state_blob(repo.root, journal["expected_post_state_sha256"])
        body = f"plan approval\n\nWorkflow-Plan-Approval: {review_content_id}\nWorkflow-Work-Item: {wi}"
        _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
        return repo.head()

    # -- pin ----------------------------------------------------------

    def test_pin_stages_the_expected_bytes_without_touching_the_working_tree(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            pre_bytes = (repo.root / _STATE_PATH).read_bytes()
            expected_bytes = self._expected_bytes(journal)
            self.assertNotEqual(pre_bytes, expected_bytes)

            blob_sha = ws.pin_plan_approval_state_blob(repo.root, expected_bytes)

            self.assertIn(len(blob_sha), (40, 64))
            self.assertRegex(blob_sha, r"^[0-9a-f]+$")
            # working tree is untouched
            self.assertEqual((repo.root / _STATE_PATH).read_bytes(), pre_bytes)
            # the index carries the new content instead
            staged = subprocess.run(
                ["git", "show", f":{_STATE_PATH}"], cwd=repo.root, capture_output=True, check=True,
            ).stdout
            self.assertEqual(staged, expected_bytes)
            # both a staged diff (index vs HEAD) and an unstaged diff (worktree vs index) exist
            self.assertIn(
                str(_STATE_PATH),
                _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root).splitlines(),
            )
            self.assertIn(
                str(_STATE_PATH), _run(["git", "diff", "--name-only"], cwd=repo.root).splitlines(),
            )

    def test_pin_precondition_refuses_when_state_path_already_staged_and_dirty(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            (repo.root / _STATE_PATH).write_text(
                (repo.root / _STATE_PATH).read_text() + "\n",
            )
            _run(["git", "add", str(_STATE_PATH)], cwd=repo.root)

            with self.assertRaises(ws.DirtyIndexBeforeStagingError):
                ws.pin_plan_approval_state_blob(repo.root, self._expected_bytes(journal))

    def test_pin_defaults_to_mode_100644_when_state_path_absent_at_head(self):
        """`workflow-2.5.0` CP9 (`v2.3.1-003`,
        `docs/defects/v2.3.1-003-plan-approval-requires-precommitted-state-file.md`):
        a `state_path` with no entry at `HEAD` at all -- this repository's
        own genuinely-first plan approval, before `WORKFLOW_STATE.json` has
        ever been committed -- no longer raises
        `PlanApprovalStateBlobUnavailableError` unconditionally. It falls
        back to mode `100644`, the mode every other tracked path this
        Workflow ever commits already uses, and the pin succeeds exactly as
        it would have if that mode had been read from `HEAD`."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            absent_path = Path("docs/ai-workflow/DOES_NOT_EXIST.json")
            expected_bytes = self._expected_bytes(journal)
            # confirms this exercises the fallback branch, not a
            # coincidental match against an existing HEAD entry
            self.assertIsNone(ws._blob_mode_and_sha_at_commit(repo.root, "HEAD", str(absent_path)))

            blob_sha = ws.pin_plan_approval_state_blob(
                repo.root, expected_bytes, state_path=absent_path,
            )

            self.assertIn(len(blob_sha), (40, 64))
            mode, staged_sha, _stage_num, _path = _run(
                ["git", "ls-files", "--stage", "--", str(absent_path)], cwd=repo.root,
            ).split()
            self.assertEqual(mode, "100644")
            self.assertEqual(staged_sha, blob_sha)
            staged_content = subprocess.run(
                ["git", "show", f":{absent_path}"], cwd=repo.root, capture_output=True, check=True,
            ).stdout
            self.assertEqual(staged_content, expected_bytes)

    # -- verify staged --------------------------------------------------

    def test_verify_staged_state_blob_passes_then_fails_after_a_foreign_restage(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            ws.pin_plan_approval_state_blob(repo.root, self._expected_bytes(journal))

            ws.verify_staged_plan_approval_state_blob(repo.root, journal["expected_post_state_sha256"])

            # A foreign restage over the same path (e.g. a concurrent writer) must be
            # caught rather than trusted on the earlier pin's word alone.
            tampered_sha = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=repo.root,
                input=b'{"tampered": true}\n', capture_output=True, check=True,
            ).stdout.decode("ascii").strip()
            _run(
                ["git", "update-index", "--cacheinfo", f"100644,{tampered_sha},{_STATE_PATH}"],
                cwd=repo.root,
            )

            with self.assertRaises(ws.StagedStateBlobMismatchError):
                ws.verify_staged_plan_approval_state_blob(repo.root, journal["expected_post_state_sha256"])

    # -- end to end: pin, commit, verify committed, materialize ---------

    def test_end_to_end_pin_commit_materialize_matches_apply_plan_approval(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            pre_bytes = (repo.root / _STATE_PATH).read_bytes()

            commit = self._commit_pinned_state(repo, wi, plan, journal, review_content_id)

            # working tree is still untouched immediately after the commit
            self.assertEqual((repo.root / _STATE_PATH).read_bytes(), pre_bytes)
            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal), ws.PLAN_APPROVAL_OUTCOME_COMMITTED,
            )
            ws.verify_committed_plan_approval_state_blob(
                repo.root, commit, journal["expected_post_state_sha256"],
            )

            ws.materialize_plan_approval_state(repo.root, commit, journal["expected_post_state_sha256"])

            expected_bytes = self._expected_bytes(journal)
            self.assertEqual((repo.root / _STATE_PATH).read_bytes(), expected_bytes)
            self.assertEqual(json.loads(expected_bytes)["work_items"][wi]["phase"], "IMPLEMENTING")
            # the working tree now matches HEAD/the index exactly for this path
            self.assertEqual(
                _run(["git", "status", "--porcelain", "--", str(_STATE_PATH)], cwd=repo.root), "",
            )

    def test_end_to_end_pin_commit_materialize_matches_apply_plan_approval_for_a_product_item(self):
        """R6: the `/approve-review plan`-equivalent staging/commit/manifest
        binding succeeds for a legitimate `work_item_type="product"` item.
        `_setup`'s inner call
        (`TestPlanApprovalFailureAtomicityTransaction._setup`) calls
        `fingerprint.resolve_plan_stage_approval_commit_paths` directly --
        the exact function this defect currently breaks for a product
        item. Fails with `PlanStageNotApplicableError` against today's
        code (the resolver call in the inner `_setup` is reached first)
        and passes once the gate is widened."""
        with h.ScratchRepo() as repo:
            wi = "prod-item"
            pre_state, record, review_content_id, plan = self._setup(repo, wi, work_item_type="product")
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            pre_bytes = (repo.root / _STATE_PATH).read_bytes()

            commit = self._commit_pinned_state(repo, wi, plan, journal, review_content_id)

            self.assertEqual((repo.root / _STATE_PATH).read_bytes(), pre_bytes)
            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal), ws.PLAN_APPROVAL_OUTCOME_COMMITTED,
            )
            ws.verify_committed_plan_approval_state_blob(
                repo.root, commit, journal["expected_post_state_sha256"],
            )

            ws.materialize_plan_approval_state(repo.root, commit, journal["expected_post_state_sha256"])

            expected_bytes = self._expected_bytes(journal)
            self.assertEqual((repo.root / _STATE_PATH).read_bytes(), expected_bytes)
            self.assertEqual(json.loads(expected_bytes)["work_items"][wi]["phase"], "IMPLEMENTING")
            self.assertEqual(
                _run(["git", "status", "--porcelain", "--", str(_STATE_PATH)], cwd=repo.root), "",
            )

    def test_verify_committed_state_blob_raises_on_mismatch(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            commit = self._commit_pinned_state(repo, wi, plan, journal, review_content_id)

            with self.assertRaises(ws.CommittedStateBlobMismatchError):
                ws.verify_committed_plan_approval_state_blob(repo.root, commit, "0" * 64)

    # -- materialize ------------------------------------------------------

    def test_materialize_refuses_unverified_committed_content_and_leaves_working_tree_untouched(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            pre_bytes = (repo.root / _STATE_PATH).read_bytes()
            commit = self._commit_pinned_state(repo, wi, plan, journal, review_content_id)

            with self.assertRaises(ws.CommittedStateBlobMismatchError):
                ws.materialize_plan_approval_state(repo.root, commit, "0" * 64)

            self.assertEqual((repo.root / _STATE_PATH).read_bytes(), pre_bytes)

    def test_materialize_leaves_no_stray_temp_file(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            commit = self._commit_pinned_state(repo, wi, plan, journal, review_content_id)

            ws.materialize_plan_approval_state(repo.root, commit, journal["expected_post_state_sha256"])

            leftovers = list((repo.root / _STATE_PATH.parent).glob(f".{_STATE_PATH.name}-*.tmp"))
            self.assertEqual(leftovers, [])

    def test_materialize_is_byte_identical_to_git_show_at_commit(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            commit = self._commit_pinned_state(repo, wi, plan, journal, review_content_id)

            ws.materialize_plan_approval_state(repo.root, commit, journal["expected_post_state_sha256"])

            committed = subprocess.run(
                ["git", "show", f"{commit}:{_STATE_PATH}"], cwd=repo.root,
                capture_output=True, check=True,
            ).stdout
            self.assertEqual((repo.root / _STATE_PATH).read_bytes(), committed)


class TestPlanApprovalMaterializeTargetScopedClassification(unittest.TestCase):
    """`WF8c` item 348(hh)/(mm): the target-scoped three-way
    classification and the whole-file freshness re-check together are
    what make step 8b safe against a *different* work item's own
    legitimate concurrent state write -- the property that makes
    `materialize_plan_approval_state`'s existing whole-file overwrite
    (part 3, already tested for the single-work-item case) safe to call
    from the permanent `/approve-review` command, where other work
    items' uncommitted writes between journal-open and materialization
    are the ordinary case, not an edge case."""

    def _two_item_setup(self, repo: h.ScratchRepo, wi: str, other_wi: str):
        """Seeds a `WORKFLOW_STATE.json` declaring *two* work items and
        commits it, then opens a plan-approval journal for `wi` alone --
        `other_wi`'s own entry is never touched by anything this class
        exercises, only read back to confirm it survives."""
        repo.write_plan_docs(work_item_id=wi)
        repo.commit_plan_docs_as_base()
        work_item = h.base_work_item(
            work_item_id=wi, governing_workflow_version="1", phase="AWAITING_PLAN_APPROVAL",
            plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            registry_path=f"docs/ai-workflow/registry/{wi}-registry.json",
            mapping_path=f"docs/ai-workflow/requirements/{wi}-mapping.json",
            base_commit=repo.base,
        )
        other_work_item = h.base_work_item(
            work_item_id=other_wi, governing_workflow_version="1", phase="IMPLEMENTING",
            plan_path=f"docs/ai-workflow/{other_wi}-plan.md",
            registry_path=f"docs/ai-workflow/registry/{other_wi}-registry.json",
            mapping_path=f"docs/ai-workflow/requirements/{other_wi}-mapping.json",
            base_commit=repo.base,
        )
        repo.write_workflow_state(**{wi: work_item, other_wi: other_work_item})
        # write_workflow_state's own fixture serialization is compact
        # JSON, not the canonical form `_publish_state_file` always uses
        # in production (item 353) -- re-canonicalize on disk so this
        # fixture's real bytes actually match what
        # plan_approval_state_matches_pre_transaction's sha256 re-read
        # will observe, exactly as a real, already-committed
        # WORKFLOW_STATE.json always is.
        pre_state = json.loads((repo.root / _STATE_PATH).read_text())
        (repo.root / _STATE_PATH).write_bytes(ws._serialize_state(pre_state))
        _run(["git", "add", str(_STATE_PATH)], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "seed workflow state"], cwd=repo.root)

        plan = fingerprint.resolve_plan_stage_approval_commit_paths(repo.root, wi, _STATE_PATH)
        protected = h.plan_stage_protected_paths(wi)
        review_content_id, _ = fingerprint.compute_review_content_id_plan_stage(
            repo.root, repo.base, work_item_type="process", work_item_id=wi,
            plan_revision=1, protected=protected,
            excluded_paths=h.plan_stage_excluded_paths(),
            excluded_prefixes=h.plan_stage_excluded_prefixes(),
        )
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan",
            user_confirmation=f"I confirm plan approval for {wi}, plan stage.", now="t1",
            reviewed_bundle_id="b1", approved_review_content_id=review_content_id,
            review_content_manifest=[{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
        )
        journal = ws.open_plan_approval_journal(
            repo.root, work_item_id=wi, base_commit=repo.base, pre_state=pre_state,
            record=record, approval_now="t1", expected_bundle_id="b1",
            expected_review_content_id=review_content_id, applicable_paths=plan.paths,
            fifth_member_applies=plan.artifacts_declaration_path is not None,
            fifth_member_sha256=plan.artifacts_declaration_sha256,
            user_confirmation=f"I confirm plan approval for {wi}, plan stage.",
            quiescence_authorization="not required: see plan_approval_state_matches_pre_transaction",
        )
        post_state = ws.apply_plan_approval(pre_state, wi, record, "t1")
        return pre_state, post_state, journal, other_work_item

    def test_classify_write_when_current_still_matches_pre_state(self):
        with h.ScratchRepo() as repo:
            pre_state, post_state, journal, _ = self._two_item_setup(repo, "wi", "other-wi")
            self.assertEqual(
                ws.classify_plan_approval_materialize_target(repo.root, "wi", pre_state, post_state),
                ws.PLAN_APPROVAL_MATERIALIZE_WRITE,
            )
            self.assertTrue(
                ws.plan_approval_state_matches_pre_transaction(
                    repo.root, journal["pre_procedure_state_sha256"],
                ),
            )

    def test_classify_noop_when_already_materialized(self):
        with h.ScratchRepo() as repo:
            pre_state, post_state, journal, _ = self._two_item_setup(repo, "wi", "other-wi")
            (repo.root / _STATE_PATH).write_text(json.dumps(post_state))
            self.assertEqual(
                ws.classify_plan_approval_materialize_target(repo.root, "wi", pre_state, post_state),
                ws.PLAN_APPROVAL_MATERIALIZE_NOOP,
            )

    def test_classify_raises_when_this_work_items_own_entry_diverges(self):
        with h.ScratchRepo() as repo:
            pre_state, post_state, journal, _ = self._two_item_setup(repo, "wi", "other-wi")
            tampered = json.loads((repo.root / _STATE_PATH).read_text())
            tampered["work_items"]["wi"]["phase"] = "SOMETHING_ELSE_ENTIRELY"
            (repo.root / _STATE_PATH).write_text(json.dumps(tampered))
            with self.assertRaises(ws.PlanApprovalMaterializeDivergentEntryError):
                ws.classify_plan_approval_materialize_target(repo.root, "wi", pre_state, post_state)

    def test_a_different_work_items_concurrent_write_is_never_discarded(self):
        """The decisive case item 348(hh)/(mm) exist for: `other-wi`'s
        own entry is legitimately updated (a real concurrent
        `state_transaction`-style write, uncommitted) after journal-open
        but before materialization. `wi`'s own target-scoped
        classification is unaffected (still `WRITE`, since `wi`'s own
        entry never changed) -- but the whole-file freshness re-check
        correctly detects the change and refuses, so the caller never
        reaches `materialize_plan_approval_state`'s own unconditional
        whole-file overwrite, which would otherwise silently revert
        `other-wi` back to its pre-transaction value."""
        with h.ScratchRepo() as repo:
            pre_state, post_state, journal, other_work_item = self._two_item_setup(
                repo, "wi", "other-wi",
            )
            current = json.loads((repo.root / _STATE_PATH).read_text())
            current["work_items"]["other-wi"]["phase"] = "AWAITING_FUNCTIONAL_REVIEW"
            current["work_items"]["other-wi"]["state_revision"] = (
                other_work_item.get("state_revision", 1) + 1
            )
            (repo.root / _STATE_PATH).write_text(json.dumps(current))

            # wi's own classification is unaffected by other-wi's write.
            self.assertEqual(
                ws.classify_plan_approval_materialize_target(repo.root, "wi", pre_state, post_state),
                ws.PLAN_APPROVAL_MATERIALIZE_WRITE,
            )
            # But the whole-file freshness re-check catches it and the
            # caller must refuse rather than call materialize_plan_approval_state.
            self.assertFalse(
                ws.plan_approval_state_matches_pre_transaction(
                    repo.root, journal["pre_procedure_state_sha256"],
                ),
            )
            # Confirming what an unconditional overwrite *would* have
            # discarded, had the freshness check not been run first.
            self.assertNotEqual(
                json.loads((repo.root / _STATE_PATH).read_text())["work_items"]["other-wi"]["phase"],
                post_state["work_items"].get("other-wi", {}).get("phase"),
            )


class TestPlanApprovalPermanentSiteEndToEnd(unittest.TestCase):
    """`WF8c` items 347/348: `.claude/commands/approve-review.md`'s own
    new plan-stage steps 4b-6d, composed and executed exactly in the
    order that file's own prose describes -- proof the whole failure-
    atomicity transaction is actually implementable end to end at the
    permanent site, not merely that each of its primitives works in
    isolation (parts 1-3's own tests, item 348's own predecessor). Serves
    as item 348's own evidence: the "Bootstrap plan-approval procedure"
    was, by its own design, never automated code to test directly (`WF8c`
    item 348's own docstring) -- this class is that same live checklist,
    exercised as a real, composed procedure against real `ScratchRepo`
    git history instead."""

    def _setup(self, repo: h.ScratchRepo, wi: str):
        """Mirrors `TestPlanApprovalStateBlobPinAndMaterialize._setup`
        (`WORKFLOW_STATE.json` committed before the journal opens, since
        this part's own primitives read the file's mode *at* `HEAD`) but
        additionally re-canonicalizes the fixture's bytes first --
        `write_workflow_state`'s own compact-JSON form is not what a
        real, already-committed `WORKFLOW_STATE.json` ever looks like
        (item 353 requires the canonical form from every production
        writer), and `plan_approval_state_matches_pre_transaction`'s
        fresh, whole-file byte comparison is the first primitive in this
        whole transaction to actually depend on that being true on disk,
        not only in `_serialize_state`'s own re-derivation."""
        pre_state, record, review_content_id, plan = TestPlanApprovalFailureAtomicityTransaction._setup(
            self, repo, wi,
        )
        (repo.root / _STATE_PATH).write_bytes(ws._serialize_state(pre_state))
        (repo.root / ".gitignore").write_text(".ai-review/\n")
        _run(["git", "add", str(_STATE_PATH), ".gitignore"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "seed workflow state"], cwd=repo.root)
        return pre_state, record, review_content_id, plan

    def _open_journal(self, repo: h.ScratchRepo, wi: str, *args):
        return TestPlanApprovalFailureAtomicityTransaction._open_journal(self, repo, wi, *args)

    def test_end_to_end_happy_path_matches_the_new_permanent_site_procedure(self):
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            owner_token = journal["owner_token"]
            post_state = ws.apply_plan_approval(pre_state, wi, record, "t1")

            # Step 5 (merged staging-and-pin): no fifth member in this
            # fixture, so the single call covers the three ordinary
            # members only and no pin follows -- confirms the merge left
            # the simple four-member case's behavior unchanged.
            self.assertIsNone(plan.artifacts_declaration_path)
            ordinary_paths = tuple(p for p in plan.paths if p != str(_STATE_PATH))
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-5-stage-and-pin", now="t2",
            ):
                ws.stage_plan_approval_commit_paths(repo.root, ordinary_paths)

            # Step 6.2: 6.1a compare-and-swap, then pin the state blob.
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.1b-state-pin", now="t3",
            ):
                self.assertTrue(
                    ws.plan_approval_state_matches_pre_transaction(
                        repo.root, journal["pre_procedure_state_sha256"],
                    ),
                )
                ws.pin_plan_approval_state_blob(
                    repo.root, base64.b64decode(journal["expected_post_state_b64"]),
                )
                ws.verify_staged_plan_approval_state_blob(
                    repo.root, journal["expected_post_state_sha256"],
                )

            # Step 6.3: staged-set assertion.
            staged = _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root)
            staged_paths = {line for line in staged.splitlines() if line}
            self.assertTrue(staged_paths.issubset(set(journal["applicable_paths"])))

            # Step 6.4: the commit.
            body = f"plan approval\n\nWorkflow-Plan-Approval: {review_content_id}\nWorkflow-Work-Item: {wi}"
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.5-commit", now="t4",
            ):
                _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
            commit = repo.head()

            # Step 6a: classify -> COMMITTED; the post-commit verification set.
            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_COMMITTED,
            )
            ws.verify_post_approval_manifest_match(
                repo.root, post_state["work_items"][wi], stage="plan",
                base_commit=repo.base, commit=commit,
            )
            ws.assert_committed_path_set_matches(repo.root, commit, journal["applicable_paths"])
            ws.verify_committed_plan_approval_state_blob(
                repo.root, commit, journal["expected_post_state_sha256"],
            )

            # Step 6c: materialize.
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-8b-materialize", now="t5",
            ):
                target = ws.classify_plan_approval_materialize_target(repo.root, wi, pre_state, post_state)
                self.assertEqual(target, ws.PLAN_APPROVAL_MATERIALIZE_WRITE)
                self.assertTrue(
                    ws.plan_approval_state_matches_pre_transaction(
                        repo.root, journal["pre_procedure_state_sha256"],
                    ),
                )
                ws.materialize_plan_approval_state(repo.root, commit, journal["expected_post_state_sha256"])

            # Step 6d: close the journal.
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-8a-close-journal", now="t6",
            ):
                ws.close_plan_approval_journal(repo.root)

            self.assertIsNone(ws.read_plan_approval_journal(repo.root))
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            final = json.loads((repo.root / _STATE_PATH).read_text())
            self.assertEqual(final, post_state)
            self.assertEqual(_run(["git", "status", "--porcelain"], cwd=repo.root), "")

    def _setup_with_fifth_member(self, repo: h.ScratchRepo, wi: str):
        """The real defect's exact fixture, reused for this class's own
        real-choreography exercise: the artifacts declaration is pending
        (never committed) and fresh in the bundle capture, so
        `resolve_plan_stage_approval_commit_paths` resolves the genuine
        five-member set step 5's merged staging-and-pin window must now
        handle in one call (`workflow-v2-3-followups` CP1,
        `LPR-R1-B01`)."""
        membership = TestPlanStageApprovalCommitMembership()
        repo.write_plan_docs(work_item_id=wi)
        membership._commit_base_without_artifacts(repo, wi)
        work_item = h.base_work_item(
            work_item_id=wi, governing_workflow_version="2.1", phase="AWAITING_PLAN_APPROVAL",
            plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            registry_path=f"docs/ai-workflow/registry/{wi}-registry.json",
            mapping_path=f"docs/ai-workflow/requirements/{wi}-mapping.json",
            base_commit=repo.base,
        )
        repo.write_workflow_state(**{wi: work_item})
        # Canonicalize and commit the state file before the journal opens
        # -- mirrors `_setup` above: `plan_approval_state_matches_pre_
        # transaction`'s fresh, whole-file byte comparison depends on the
        # live bytes matching `_serialize_state`'s canonical form, and the
        # merged step's own state-pin sub-step (6.2) needs a stable `HEAD`
        # to diff its own new pin against. Only the fifth member --
        # `<wi>-artifacts.json` -- stays genuinely pending.
        pre_state = json.loads((repo.root / _STATE_PATH).read_text())
        (repo.root / _STATE_PATH).write_bytes(ws._serialize_state(pre_state))
        _run(["git", "add", str(_STATE_PATH)], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "seed workflow state"], cwd=repo.root)
        artifacts_rel = f"docs/ai-workflow/registry/{wi}-artifacts.json"
        membership._seed_bundle_capture(repo, wi, artifacts_rel)

        plan = fingerprint.resolve_plan_stage_approval_commit_paths(
            repo.root, wi, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
        )
        self.assertIsNotNone(plan.artifacts_declaration_path)  # confirms the five-member case

        protected = h.plan_stage_protected_paths(wi)
        review_content_id, _ = fingerprint.compute_review_content_id_plan_stage(
            repo.root, repo.base, work_item_type="process", work_item_id=wi,
            plan_revision=1, protected=protected,
            excluded_paths=h.plan_stage_excluded_paths(),
            excluded_prefixes=h.plan_stage_excluded_prefixes(),
        )
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan",
            user_confirmation=f"I confirm plan approval for {wi}, plan stage.", now="t1",
            reviewed_bundle_id="b1", approved_review_content_id=review_content_id,
            review_content_manifest=[{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
        )
        return pre_state, record, review_content_id, plan

    def test_five_member_fixture_succeeds_through_the_real_merged_step_5(self):
        """The exact gap `workflow-v2-3-followups` CP1 closes and
        `WORKFLOW_V2_3_FOLLOWUPS.md` confirmed no test covered: a genuine
        five-member fixture (fifth member pending and fresh) driven
        through `approve-review.md`'s own real, merged step 5 -- one call
        to `stage_plan_approval_commit_paths` over all four non-state
        members, then the fifth-member pin, both inside the same
        `"step-5-stage-and-pin"` guarded window -- proving the documented
        sequence now succeeds end to end, all the way through to a
        committed, materialized, journal-closed outcome. The *old*
        two-call split is proven broken by
        `TestPlanStageApprovalCommitMembership`'s own low-level fixture
        already; this test proves the *new* one-call choreography works,
        not merely that the underlying primitive accepts an arbitrary
        path tuple."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup_with_fifth_member(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            owner_token = journal["owner_token"]
            post_state = ws.apply_plan_approval(pre_state, wi, record, "t1")

            # Step 5 (merged): one call over every non-state member,
            # ordinary members plus the fifth, then pin the fifth --
            # exactly approve-review.md's own documented call shape.
            fifth = plan.artifacts_declaration_path
            self.assertIsNotNone(fifth)
            ordinary_paths = tuple(p for p in plan.paths if p not in (str(_STATE_PATH), fifth))
            # workflow-2.6.0 (`D-Plan-Approval-Closure`): the plan doc,
            # registry and mapping plus the fixture's two further declared
            # protected paths (re-pointed from 2.5.1's fixed three).
            self.assertEqual(len(ordinary_paths), 5)
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-5-stage-and-pin", now="t2",
            ):
                ws.stage_plan_approval_commit_paths(repo.root, ordinary_paths + (fifth,))
                ws.verify_staged_blob_sha256(repo.root, fifth, plan.artifacts_declaration_sha256)

            # The fifth member is the only one of the four with real
            # changed content at this fixture's HEAD (the three ordinary
            # members were already settled by `_commit_base_without_
            # artifacts`) -- a subset check, matching
            # `stage_plan_approval_commit_paths`'s own post-staging
            # assertion, not exact equality (a byte-identical member
            # legitimately produces no diff entry).
            staged = _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root)
            staged_paths = {line for line in staged.splitlines() if line}
            self.assertIn(fifth, staged_paths)
            self.assertTrue(staged_paths.issubset(set(ordinary_paths) | {fifth}))

            # Step 6.2: 6.1a compare-and-swap, then pin the state blob.
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.1b-state-pin", now="t3",
            ):
                self.assertTrue(
                    ws.plan_approval_state_matches_pre_transaction(
                        repo.root, journal["pre_procedure_state_sha256"],
                    ),
                )
                ws.pin_plan_approval_state_blob(
                    repo.root, base64.b64decode(journal["expected_post_state_b64"]),
                )
                ws.verify_staged_plan_approval_state_blob(
                    repo.root, journal["expected_post_state_sha256"],
                )

            # Step 6.3: staged-set assertion.
            staged = _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo.root)
            staged_paths = {line for line in staged.splitlines() if line}
            self.assertTrue(staged_paths.issubset(set(journal["applicable_paths"])))

            # Step 6.4: the commit.
            body = f"plan approval\n\nWorkflow-Plan-Approval: {review_content_id}\nWorkflow-Work-Item: {wi}"
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.5-commit", now="t4",
            ):
                _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
            commit = repo.head()

            # Step 6a: classify -> COMMITTED; the post-commit verification
            # set, fifth-member blob check included.
            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_COMMITTED,
            )
            ws.verify_post_approval_manifest_match(
                repo.root, post_state["work_items"][wi], stage="plan",
                base_commit=repo.base, commit=commit,
            )
            ws.assert_committed_path_set_matches(repo.root, commit, journal["applicable_paths"])
            ws.verify_committed_plan_approval_state_blob(
                repo.root, commit, journal["expected_post_state_sha256"],
            )
            ws.verify_committed_blob_sha256(repo.root, commit, fifth, plan.artifacts_declaration_sha256)

            # Step 6c: materialize; step 6d: close the journal.
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-8b-materialize", now="t5",
            ):
                target = ws.classify_plan_approval_materialize_target(repo.root, wi, pre_state, post_state)
                self.assertEqual(target, ws.PLAN_APPROVAL_MATERIALIZE_WRITE)
                ws.materialize_plan_approval_state(repo.root, commit, journal["expected_post_state_sha256"])
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-8a-close-journal", now="t6",
            ):
                ws.close_plan_approval_journal(repo.root)

            self.assertIsNone(ws.read_plan_approval_journal(repo.root))
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            final = json.loads((repo.root / _STATE_PATH).read_text())
            self.assertEqual(final, post_state)
            self.assertEqual(_run(["git", "status", "--porcelain"], cwd=repo.root), "")

    def test_merged_step_5_still_refuses_on_unrelated_dirty_index(self):
        """The merge does not weaken the pre-staging precondition: unrelated
        content already staged with real changed content before the merged
        window's own `stage_plan_approval_commit_paths` call runs still
        raises `DirtyIndexBeforeStagingError`, and the guard releases
        without advancing progress -- step 6b's rollback then resets it
        cleanly, and a retry (with the unrelated content removed) succeeds
        through the same merged step."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup_with_fifth_member(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            owner_token = journal["owner_token"]

            status_before = _run(["git", "status", "--porcelain"], cwd=repo.root)
            (repo.root / "unrelated.txt").write_text("unrelated concurrent content\n")
            _run(["git", "add", "unrelated.txt"], cwd=repo.root)

            fifth = plan.artifacts_declaration_path
            ordinary_paths = tuple(p for p in plan.paths if p not in (str(_STATE_PATH), fifth))
            with self.assertRaises(ws.DirtyIndexBeforeStagingError):
                with ws.plan_approval_guarded_mutation(
                    repo.root, owner_token=owner_token, step="step-5-stage-and-pin", now="t2",
                ):
                    ws.stage_plan_approval_commit_paths(repo.root, ordinary_paths + (fifth,))

            # The guard released without advancing progress -- no
            # progress record for this step exists yet.
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            progress = ws.read_plan_approval_owner_progress(repo.root, owner_token)
            self.assertTrue(progress is None or progress.get("step") != "step-5-stage-and-pin")

            # Step 6b's rollback resets the whole index back to HEAD --
            # both this attempt's own unrelated stage and (a no-op, since
            # the call above raised before staging anything of its own)
            # any partial staging from the merged call.
            lease = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token, step="rollback-index-reset", now="t3",
            )
            try:
                ws.rollback_plan_approval_transaction(repo.root, owner_token=owner_token)
            finally:
                ws.release_plan_approval_guard(repo.root, lease)
            self.assertEqual(repo.head(), journal["pre_procedure_head"])
            status_after = _run(["git", "status", "--porcelain"], cwd=repo.root)
            expected = set(status_before.splitlines()) | {"?? unrelated.txt"}
            self.assertEqual(set(status_after.splitlines()), expected)

    def test_new_step_label_classifies_ordinary_and_acquires_the_guard(self):
        """`plan_approval_step_class("step-5-stage-and-pin")` returns
        `"ordinary"` and `plan_approval_guarded_mutation` successfully
        acquires the guard under it -- the existing generic
        `TestPlanApprovalFailureAtomicityTransaction`-sibling set-iteration
        test covers the frozensets' exhaustiveness generically but proves
        nothing about this specific, newly-added label (`LPR-R3-B02`)."""
        self.assertEqual(ws.plan_approval_step_class("step-5-stage-and-pin"), ws.ORDINARY)
        with h.ScratchRepo() as repo:
            wi = "wi"
            journal = self._open_journal(repo, wi, *self._setup(repo, wi))
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=journal["owner_token"], step="step-5-stage-and-pin", now="t2",
            ):
                pass
            progress = ws.read_plan_approval_owner_progress(repo.root, journal["owner_token"])
            self.assertEqual(progress["step"], "step-5-stage-and-pin")

    def test_not_committed_outcome_runs_the_step_6b_rollback_pattern(self):
        """Nothing staged, nothing committed -- classify must find
        `NOT_COMMITTED`, and step 6b's own guard-wrapped (not
        `plan_approval_guarded_mutation`) rollback call must leave no
        journal, no guard, and no owner-progress orphan behind."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            owner_token = journal["owner_token"]

            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED,
            )
            lease = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token, step="rollback-index-reset", now="t2",
            )
            try:
                ws.rollback_plan_approval_transaction(repo.root, owner_token=owner_token)
            finally:
                ws.release_plan_approval_guard(repo.root, lease)

            self.assertIsNone(ws.read_plan_approval_journal(repo.root))
            self.assertIsNone(ws.read_plan_approval_guard(repo.root))
            self.assertIsNone(ws.read_plan_approval_owner_progress(repo.root, owner_token))
            self.assertEqual(repo.head(), journal["pre_procedure_head"])
            self.assertEqual(_run(["git", "status", "--porcelain"], cwd=repo.root), "")

    def test_step_6_2_compare_and_swap_catches_staleness_before_any_commit(self):
        """A legitimate concurrent write lands on
        `docs/ai-workflow/WORKFLOW_STATE.json` between journal-open and
        step 6.2 -- the 6.1a compare-and-swap must detect it and refuse
        *before* pinning a post-state derived from superseded bytes;
        step 6b's rollback then leaves the concurrent write's own bytes
        completely untouched (rollback never writes the working tree)."""
        with h.ScratchRepo() as repo:
            wi = "wi"
            pre_state, record, review_content_id, plan = self._setup(repo, wi)
            journal = self._open_journal(repo, wi, pre_state, record, review_content_id, plan)
            owner_token = journal["owner_token"]

            # A concurrent, legitimate state_transaction-shaped write.
            concurrent = json.loads((repo.root / _STATE_PATH).read_text())
            concurrent["work_items"][wi]["some_unrelated_field"] = "touched by another writer"
            (repo.root / _STATE_PATH).write_bytes(ws._serialize_state(concurrent))

            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.2-stage-ordinary", now="t2",
            ):
                ordinary_paths = tuple(p for p in plan.paths if p != str(_STATE_PATH))
                ws.stage_plan_approval_commit_paths(repo.root, ordinary_paths)

            fresh = ws.plan_approval_state_matches_pre_transaction(
                repo.root, journal["pre_procedure_state_sha256"],
            )
            self.assertFalse(fresh)

            lease = ws.acquire_plan_approval_guard(
                repo.root, holder_owner_token=owner_token, step="rollback-index-reset", now="t3",
            )
            try:
                ws.rollback_plan_approval_transaction(repo.root, owner_token=owner_token)
            finally:
                ws.release_plan_approval_guard(repo.root, lease)

            self.assertIsNone(ws.read_plan_approval_journal(repo.root))
            # The concurrent write survives untouched -- rollback resets
            # only the index, never the working tree.
            self.assertEqual(json.loads((repo.root / _STATE_PATH).read_text()), concurrent)

    def test_step_6c_refuses_rather_than_discard_a_different_work_items_write(self):
        """Full-stack version of
        `TestPlanApprovalMaterializeTargetScopedClassification`'s own
        decisive case: after this work item's commit lands, a different
        work item's own entry is legitimately updated before step 6c
        runs. The caller (this test, standing in for the command's own
        step 6c prose) must observe `WRITE` from the target-scoped
        classifier but `False` from the freshness re-check, and must
        therefore never call `materialize_plan_approval_state` at all --
        proving the two checks compose correctly to prevent exactly the
        silent-discard failure mode item 348(hh)/(mm) exist to close."""
        with h.ScratchRepo() as repo:
            wi, other_wi = "wi", "other-wi"
            pre_state, post_state, journal, other_work_item = (
                TestPlanApprovalMaterializeTargetScopedClassification._two_item_setup(
                    self, repo, wi, other_wi,
                )
            )
            owner_token = journal["owner_token"]

            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.2-stage-ordinary", now="t2",
            ):
                ordinary_paths = tuple(
                    p for p in journal["applicable_paths"] if p != str(_STATE_PATH)
                )
                ws.stage_plan_approval_commit_paths(repo.root, ordinary_paths)
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.1b-state-pin", now="t3",
            ):
                ws.pin_plan_approval_state_blob(
                    repo.root, base64.b64decode(journal["expected_post_state_b64"]),
                )
            body = f"plan approval\n\nWorkflow-Plan-Approval: {journal['expected_review_content_id']}\nWorkflow-Work-Item: {wi}"
            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-6.5-commit", now="t4",
            ):
                _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
            commit = _run(["git", "rev-parse", "HEAD"], cwd=repo.root).strip()
            self.assertEqual(
                ws.classify_plan_approval_outcome(repo.root, journal),
                ws.PLAN_APPROVAL_OUTCOME_COMMITTED,
            )

            # A different work item's own entry is legitimately updated
            # in the working tree before step 6c runs.
            current = json.loads((repo.root / _STATE_PATH).read_text())
            current["work_items"][other_wi]["phase"] = "AWAITING_FUNCTIONAL_REVIEW"
            (repo.root / _STATE_PATH).write_bytes(ws._serialize_state(current))

            with ws.plan_approval_guarded_mutation(
                repo.root, owner_token=owner_token, step="step-8b-materialize", now="t5",
            ):
                target = ws.classify_plan_approval_materialize_target(repo.root, wi, pre_state, post_state)
                self.assertEqual(target, ws.PLAN_APPROVAL_MATERIALIZE_WRITE)
                fresh = ws.plan_approval_state_matches_pre_transaction(
                    repo.root, journal["pre_procedure_state_sha256"],
                )
                self.assertFalse(fresh)
                # The command's own prose stops here without writing --
                # confirm what materialize_plan_approval_state *would*
                # have discarded, had it been called anyway.
                would_be_written = json.loads(base64.b64decode(journal["expected_post_state_b64"]))
                self.assertNotEqual(
                    would_be_written["work_items"][other_wi]["phase"],
                    current["work_items"][other_wi]["phase"],
                )

            # The journal is still open (6c refused, 6d never ran) and
            # the durable commit is untouched -- both survive for a
            # later reconciliation + re-run.
            self.assertIsNotNone(ws.read_plan_approval_journal(repo.root))
            self.assertEqual(
                _run(["git", "rev-parse", "HEAD"], cwd=repo.root).strip(), commit,
            )


class TestRetiredScopedRemediationLeavesNoLiveSurface(unittest.TestCase):
    """Ledger `I10`'s mechanical closure. `/accept-scoped-remediation` was
    documented as part of the supported operator contract while its own
    entry precondition -- `AWAITING_FUNCTIONAL_REVIEW` with a non-terminal
    own registry -- had no producer in any supported lifecycle, so it could
    only ever refuse. It is retired rather than made reachable, and this
    class is what keeps the retirement honest: an executable contract
    (`.claude/commands/**`) must not name it at all, no runtime message may
    send an operator to it, no helper may survive with it as its only
    caller, and every doc that still names it must say it was retired.

    Deliberately *not* asserted: absence from the historical record.
    `docs/ai-workflow/WORKFLOW_V2_PLAN.md`, the requirements ledgers, the
    registry evidence files, `WORKFLOW_STATE.json`'s approval manifests and
    the dry-run findings all record what was designed and approved at the
    time; rewriting them would falsify provenance, and none of them is a
    surface an operator is routed through."""

    LIVE_DOCS = (
        "CLAUDE.md",
        "docs/ai-workflow/MILESTONE_WORKFLOW.md",
        "docs/ai-workflow/REVIEW_PROTOCOL.md",
        "docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md",
    )

    RETIRED_SYMBOLS = (
        "discover_scoped_remediation_commits",
        "build_scoped_remediation_live_snapshot",
        "verify_functional_checklist_evidence",
        "parse_scoped_remediation_confirmation_binding_fields",
        "resolve_scoped_remediation_round",
        "build_scoped_remediation_live_fields",
        "apply_scoped_remediation_acceptance",
        "scoped_remediation_gate_reachable",
        "SCOPED_REMEDIATION_ACCEPTANCE_FIELDS",
        "NoExistingRound",
        "ExactReplay",
        "ConflictingDuplicate",
        "MalformedAcceptanceRecord",
        "AmbiguousHistory",
        "AmbiguousScopedRemediationTrailerError",
        "MissingFunctionalChecklistEvidenceError",
        "StaleFunctionalChecklistConfirmationError",
        "MalformedFunctionalChecklistEvidenceError",
        "DirtyFunctionalChecklistPathError",
        "ScopedRemediationLiveValueChangedError",
    )

    SURVIVING_SHARED_PRIMITIVES = (
        # Reached by /prepare-functional-review and /review-functional.
        "discover_functional_checklist_commits",
        "discover_current_functional_checklist_evidence",
        "NonFirstParentFunctionalChecklistEvidenceError",
        "AmbiguousFunctionalChecklistTrailerError",
        "FUNCTIONAL_CHECKLIST_PATH",
        # Reached by /accept-milestone and complete_work_item.
        "registry_completion_status",
        "resolve_own_registry_completion_status",
        "milestone_complete_gate_reachable",
        "IncompleteOwnCheckpointsError",
        # The remediation-child mechanism, untouched.
        "create_remediation_child_work_item",
        "incomplete_children",
        "IncompleteChildWorkItemError",
    )

    def test_the_command_file_is_gone(self):
        self.assertFalse(
            (_repo_root() / ".claude" / "commands" / "accept-scoped-remediation.md").exists()
        )

    def test_no_command_file_names_the_retired_command(self):
        """The executable contract carries no trace of it -- not even a
        retirement note, which belongs in the docs, not in a command an
        agent executes step by step."""
        for path in sorted((_repo_root() / ".claude" / "commands").glob("*.md")):
            self.assertNotIn("accept-scoped-remediation", path.read_text(), path.name)
            self.assertNotIn("scoped_remediation", path.read_text(), path.name)

    def test_live_docs_only_name_it_as_retired(self):
        """An operator doc may still explain what happened to it -- that is
        how someone who remembers the command finds out why it is gone --
        but every mention must sit in a paragraph that says so."""
        for rel_path in self.LIVE_DOCS:
            text = (_repo_root() / rel_path).read_text()
            for para in text.split("\n\n"):
                if "accept-scoped-remediation" not in para:
                    continue
                self.assertTrue(
                    "retire" in para.lower(),
                    f"{rel_path}: a paragraph names /accept-scoped-remediation without "
                    f"saying it was retired:\n{para}",
                )

    def test_the_historical_status_note_carries_a_dated_correction(self):
        """`docs/ACTIVE_MILESTONE.md` is the exception to the paragraph
        rule above, and deliberately so: its 2026-08-04 status note is a
        dated record of what was true then, and rewriting it would falsify
        the record. It is left verbatim and immediately followed by a
        dated correction, so the two read as history plus current truth
        rather than as stale live guidance.

        `workflow-2.5.0` CP9 (`v2.3.1-001`,
        `docs/defects/v2.3.1-001-host-history-coupled-tests.md`): the note
        this test looks for is RepFlow's own dated milestone history, never
        part of any Workflow release's distributed content. A repository
        that has never carried it (any freshly bootstrapped target) has
        neither the note nor its correction, and `next(...)` used to raise
        `StopIteration` there instead of a clean pass/fail. The portable
        form the defect record itself states is adopted verbatim: look for
        the note with a default of `None` and skip cleanly when it is
        absent, rather than treating "no host history at all" the same as
        "host history present but malformed." A repository that does carry
        the note (this one; the frozen conformance fixture) is unaffected
        -- the assertions below still run, and still fail exactly as
        before when the note is present but its correction is missing or
        malformed."""
        text = (_repo_root() / "docs/ACTIVE_MILESTONE.md").read_text()
        paragraphs = text.split("\n\n")
        note = next(
            (i for i, p in enumerate(paragraphs)
             if p.startswith("**Status note (2026-08-04)**")),
            None,
        )
        if note is None:
            self.skipTest("no host status note in this repository")
        correction = paragraphs[note + 1]
        self.assertTrue(correction.startswith("**Correction (2026-08-26)**"), correction[:80])
        self.assertIn("retired", correction)
        self.assertIn("/milestone-implement", correction)
        self.assertIn("/apply-functional-review", correction)

    def test_no_live_doc_routes_an_operator_to_it(self):
        for rel_path in self.LIVE_DOCS + ("docs/ACTIVE_MILESTONE.md",):
            text = (_repo_root() / rel_path).read_text()
            for phrase in ("use `/accept-scoped-remediation`", "run `/accept-scoped-remediation`",
                           "points you at\n  `/accept-scoped-remediation`",
                           "point at `/accept-scoped-remediation`",
                           "`/accept-scoped-remediation` is\nthe correct",
                           "`/accept-scoped-remediation` is the correct"):
                self.assertNotIn(phrase, text, f"{rel_path}: {phrase!r}")

    def test_no_orphaned_helper_survives(self):
        for symbol in self.RETIRED_SYMBOLS:
            self.assertFalse(hasattr(ws, symbol), f"workflow_state.{symbol} still exists")
        self.assertNotIn("scoped_remediation", ws.APPROVAL_STAGES)

    def test_shared_primitives_are_untouched(self):
        """The retirement is surgical: everything the supported bounded
        functional-fix and remediation-child paths reach is still here."""
        for symbol in self.SURVIVING_SHARED_PRIMITIVES:
            self.assertTrue(hasattr(ws, symbol), f"workflow_state.{symbol} was removed")

    def test_prepare_functional_review_still_writes_checklist_evidence(self):
        """`I7`'s unchanged-checklist branch and the evidence commit it
        guards are supported machinery, not scoped-remediation machinery,
        and survive the retirement intact."""
        text = _command_text("prepare-functional-review.md")
        self.assertIn("Workflow-Functional-Checklist:", text)
        self.assertIn("discover_current_functional_checklist_evidence", text)
        self.assertIn("--allow-empty", text)

    def test_accept_milestone_names_only_supported_ways_forward(self):
        text = _command_text("accept-milestone.md")
        self.assertIn("/milestone-implement", text)
        self.assertIn("/apply-functional-review", text)
        self.assertIn("remediation-<n>", text)

    def test_the_operator_reference_command_count_matches_reality(self):
        """The reference sectioned 14 commands and said so; one of them was
        `/accept-scoped-remediation`. Removing that section left 13, plus a
        second, older gap: `workflow-v2-3` added `/review-implementation`
        and `/review-functional` without ever sectioning them here. Both
        gaps are now closed -- `workflow-2.4.0` CP3 added a sixteenth,
        `/request-plan-amendment` -- and `workflow-2.5.0` CP4 added a
        seventeenth, `/record-manual-implementation-review`, and
        `workflow-2.8.0` CP1 an eighteenth, `/adopt-gate-policy`, CP2 a
        nineteenth, `/satisfy-gate`, and CP5 a twentieth, `/apply-pr-review` --
        the reference sections all 20 live commands -- and this test derives
        the expected count from the real files rather than hand-maintaining
        a number that can go stale again."""
        text = (_repo_root() / "docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md").read_text()
        sections = re.findall(r"(?m)^### `/([a-z0-9-]+)", text)
        on_disk = sorted(p.stem for p in (_repo_root() / ".claude" / "commands").glob("*.md"))
        self.assertEqual(len(on_disk), 22)
        self.assertNotIn("accept-scoped-remediation", sections)
        self.assertNotIn("accept-scoped-remediation", on_disk)
        # Every live command has exactly one section, and vice versa.
        self.assertEqual(sorted(sections), on_disk)
        self.assertIn("review-functional", sections)
        self.assertIn("review-implementation", sections)


# ---------------------------------------------------------------------------
# Work-item targeting contract (Workflow v2.x convergence campaign, ledger
# row `B9`, independent-review finding `A9`).
#
# `D1` calls `active_work_item_id` "a resume-focus pointer, not an execution
# lock" and treats several live non-terminal work items as the normal case.
# `create_remediation_child_work_item` relies on exactly that: it
# deliberately does *not* repoint the pointer, so a
# `<parent-id>-remediation-<n>` child is **never** the active item while its
# parent is open. Every command on that child's lifecycle therefore has to
# accept an explicit work-item id, or the child cannot be planned, reviewed
# or closed at all -- and since `complete_work_item` refuses a parent with an
# incomplete child, the parent would then be permanently unacceptable too.
#
# Before this campaign, `/milestone-plan` (`[base-sha]` only),
# `/apply-plan-review`, `/apply-implementation-review` and
# `/accept-milestone` accepted no work-item id, while
# `/apply-functional-review`'s broad branch nonetheless told the operator to
# run `/milestone-plan <child-id>`. The tests below are what keep that from
# regressing.
# ---------------------------------------------------------------------------


def _phase_writing_state_functions() -> set[str]:
    """Every `workflow_state.py` function that persists a `phase` value,
    found by scanning the module source for `def` boundaries and phase
    assignments. Deliberately a plain line scan rather than
    `workflow_state_test.py`'s AST census of the same fact: two
    independent derivations catch a mistake in either one, where a single
    shared helper would be silently wrong in both."""
    functions: set[str] = set()
    current: str | None = None
    for line in (_repo_root() / "scripts" / "workflow_state.py").read_text().splitlines():
        match = re.match(r"^def (\w+)", line)
        if match:
            current = match.group(1)
        if current and (re.search(r'\["phase"\]\s*=', line)
                        or re.search(r'^\s*"phase":\s*"', line)):
            functions.add(current)
    return functions


def _argument_hint(filename: str) -> str | None:
    """The `argument-hint:` frontmatter value of a command file, or `None`
    when it declares none."""
    text = _command_text(filename)
    if not text.startswith("---"):
        return None
    frontmatter = text.split("---", 2)[1]
    for line in frontmatter.splitlines():
        if line.startswith("argument-hint:"):
            return line.split(":", 1)[1].strip().strip('"')
    return None


def _commands_naming_a_phase_writer() -> set[str]:
    writers = _phase_writing_state_functions()
    named = set()
    for path in sorted((_repo_root() / ".claude" / "commands").glob("*.md")):
        text = path.read_text()
        if any(re.search(r"\b" + writer + r"\b", text) for writer in writers):
            named.add(path.name)
    return named


#: `/bootstrap-workflow-v2` is the one phase-advancing command that must
#: *not* take a work-item id: its own file states the target is hardcoded to
#: `workflow-v2-1-core` and the command is deleted when that item completes.
#: Asserted rather than assumed by
#: `test_the_single_exempt_command_says_why_it_is_exempt` below.
_TARGETING_EXEMPT_COMMANDS = frozenset({"bootstrap-workflow-v2.md"})

#: Phase-advancing commands that take a work-item id (so the targeting rule
#: above still binds them) but are not steps of a remediation child's own
#: lifecycle: `/retire-legacy-work-item` (workflow-2.9.0) closes a dormant
#: `LEGACY_READY` item, and a remediation child is never a legacy item.
_CHILD_SEQUENCE_EXEMPT_COMMANDS = frozenset({"retire-legacy-work-item.md", "resume-implementation.md"})


class TestWorkItemTargetingContract(unittest.TestCase):

    def test_every_command_naming_a_phase_writer_accepts_a_work_item_id(self):
        """The derived rule: if a command's own steps name a function that
        persists `phase`, that command drives a specific work item, so it
        must be able to be pointed at one that is not the active item."""
        offenders = []
        for filename in sorted(_commands_naming_a_phase_writer() - _TARGETING_EXEMPT_COMMANDS):
            hint = _argument_hint(filename)
            if hint is None or "work-item-id" not in hint:
                offenders.append((filename, hint))
        self.assertEqual(offenders, [], f"phase-advancing commands with no work-item-id argument: {offenders}")

    def test_the_derivation_actually_finds_the_lifecycle_commands(self):
        """A guard on the guard: if the derivation above silently stopped
        matching (a renamed writer, a reworded command file), the test
        above would pass vacuously. These five are the ones the campaign
        repaired, so their presence in the derived set is what makes that
        test meaningful."""
        derived = _commands_naming_a_phase_writer()
        for filename in ("milestone-plan.md", "apply-plan-review.md",
                         "apply-implementation-review.md",
                         "apply-functional-review.md", "accept-milestone.md"):
            self.assertIn(filename, derived)
        self.assertGreaterEqual(len(_phase_writing_state_functions()), 10)

    def test_the_single_exempt_command_says_why_it_is_exempt(self):
        for filename in _TARGETING_EXEMPT_COMMANDS:
            text = _command_text(filename)
            self.assertIn("hardcoded", text, filename)
            self.assertIsNone(_argument_hint(filename), filename)

    def test_milestone_plan_states_the_work_item_and_base_sha_resolution_rule(self):
        text = _command_text("milestone-plan.md")
        self.assertEqual(_argument_hint("milestone-plan.md"), "[work-item-id] [base-sha]")
        # Resolution is by lookup against real state, never by token shape,
        # and the historical single-base-sha form still works.
        self.assertIn("a key of `work_items` selects that work item", text)
        self.assertIn("`/milestone-plan <base-sha>` form working", text)
        self.assertIn("`<work-item-id> <base-sha>`, in that order", text)
        # The explicit-target branch lives in step 0, alongside the other
        # target/version resolution, so steps 1-5's v1-comparable text is
        # untouched (TestGoldenV1BehaviorAgainstPreV21BaseCommit).
        steps = _extract_numbered_steps(text)
        self.assertIn("Explicitly selected target", steps["0"])
        self.assertNotIn("Explicitly selected target", steps["1"])
        self.assertIn("remediation child", steps["0"])
        # It must not silently steal focus from the parent.
        self.assertIn("is **not** repointed here", steps["0"])

    def test_the_broad_remediation_branch_only_names_targetable_commands(self):
        """The instruction an operator actually receives. Every command it
        names with `<child-id>` must be able to accept one -- this is the
        exact contradiction `A9` found (`/milestone-plan <child-id>` against
        a `[base-sha]`-only contract)."""
        named = _broad_branch_child_commands()
        self.assertNotEqual(named, set())
        for command in sorted(named):
            filename = f"{command}.md"
            self.assertTrue(
                (_repo_root() / ".claude" / "commands" / filename).exists(),
                f"the broad branch names /{command}, which does not exist",
            )
            hint = _argument_hint(filename)
            self.assertIsNotNone(hint, f"/{command} takes no arguments at all")
            self.assertIn("work-item-id", hint, f"/{command}: {hint!r}")

    def test_the_broad_remediation_branch_covers_the_whole_child_lifecycle(self):
        """Completeness, derived rather than listed: a child runs the
        ordinary cycle, so every phase-advancing command except the
        exempt bootstrap driver has to appear in the sequence the broad
        branch hands the operator."""
        expected = {
            filename[:-3] for filename in
            _commands_naming_a_phase_writer() - _TARGETING_EXEMPT_COMMANDS - _CHILD_SEQUENCE_EXEMPT_COMMANDS
        }
        missing = expected - _broad_branch_child_commands()
        self.assertEqual(missing, set(), f"child sequence omits: {sorted(missing)}")

    def test_the_broad_branch_states_the_real_child_entry_phase(self):
        """Diagram finding `B1`: the child does *not* enter at
        `AWAITING_EXTERNAL_PLAN_REVIEW` under the current default. That is
        `publish_plan_revision`'s `"1"` branch; a `"2.1"`-governed child --
        which is what `create_remediation_child_work_item` produces under
        this repository's own config default -- enters at
        `AWAITING_LOCAL_PLAN_REVIEW`."""
        text = _command_text("apply-functional-review.md")
        self.assertIn("enters review at\n     `AWAITING_LOCAL_PLAN_REVIEW` when its\n"
                      "     `governing_workflow_version` is `\"2.1\"`", text.replace(
                          "The child enters review at", "enters review at"))
        self.assertIn("`AWAITING_EXTERNAL_PLAN_REVIEW` when it is `\"1\"`", text)
        self.assertIn("publish_plan_revision", text)

    def test_accept_milestone_documents_the_completion_obligation_refusal(self):
        """Finding `A6`: `complete_work_item` raises
        `UnsatisfiedCompletionObligationError` independently of the child
        and own-checkpoint blocks, and the command that calls it never
        said so."""
        text = _command_text("accept-milestone.md")
        self.assertIn("UnsatisfiedCompletionObligationError", text)
        self.assertIn("D-Completion-Obligations", text)
        self.assertIn("None of these four stops", text)
        self.assertNotIn("None of these three stops", text)

    def test_accept_milestone_has_a_remediation_child_bookkeeping_branch(self):
        """A remediation child is not a roadmap milestone: it was never
        listed there and has no milestone summary to archive, so the
        roadmap/archive steps cannot apply to it verbatim."""
        text = _command_text("accept-milestone.md")
        self.assertIn("Remediation-child bookkeeping branch", text)
        self.assertIn("skip steps 3, 5 and 7", text)
        self.assertIn("`parent_work_item_id` is non-null", text)

    def test_accept_milestone_never_derives_the_target_from_its_own_confirmation(self):
        """The user-only guard checks the confirmation *against* a resolved
        id; deriving the id from the confirmation would make it
        self-satisfying."""
        text = _command_text("accept-milestone.md")
        self.assertIn("never infer the id\n   from step 1's confirmation text", text)


def _broad_branch_child_commands() -> set[str]:
    """The command names `/apply-functional-review`'s broad branch tells an
    operator to run against `<child-id>`, parsed out of the instruction
    itself rather than restated here."""
    text = _command_text("apply-functional-review.md")
    branch = text.split("**Broad/multi-finding remediation**", 1)[1]
    collapsed = re.sub(r"\s+", " ", branch)
    return set(re.findall(r"/([a-z0-9-]+)(?: plan| implementation)? <child-id>", collapsed))


# ---------------------------------------------------------------------------
# The operator reference, checked against the code it describes rather than
# against itself (convergence campaign, ledger rows `I11`-`I13`). Every
# claim below was wrong in `b0b5ba2` and is now derived.
# ---------------------------------------------------------------------------


_OPERATOR_REFERENCE = "docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md"


def _operator_reference_text() -> str:
    return (_repo_root() / _OPERATOR_REFERENCE).read_text()


def _reference_phase_table(heading: str) -> dict[str, str]:
    r"""The `| \`PHASE\` | ... |` rows of one of the two phase tables in the
    reference's "Phases" section, as `{phase: rest-of-row}`."""
    text = _operator_reference_text().split(heading, 1)[1]
    rows: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        match = re.match(r"^`([A-Z_]+)`", cells[0])
        if match:
            rows[match.group(1)] = " | ".join(cells[1:])
    return rows


def _declared_state_writer_false() -> set[str]:
    """Command stems whose frontmatter declares `state_writer: false`."""
    stems = set()
    for path in sorted((_repo_root() / ".claude" / "commands").glob("*.md")):
        frontmatter = path.read_text().split("---", 2)[1]
        for line in frontmatter.splitlines():
            if line.strip() == "state_writer: false":
                stems.add(path.stem)
    return stems


class TestOperatorReferenceMatchesReality(unittest.TestCase):

    def test_the_persisted_phase_table_is_exactly_the_writer_census(self):
        """Finding `A1`: the reference listed all 17 `KNOWN_PHASES` as one
        undifferentiated set and presented `AWAITING_TECHNICAL_APPROVAL` as
        a live persisted gate. Both tables are now derived facts and are
        checked as such."""
        writers = _phase_writing_state_functions()
        written = set()
        for line in (_repo_root() / "scripts" / "workflow_state.py").read_text().splitlines():
            for match in re.finditer(r'\["phase"\]\s*=\s*"([A-Z_]+)"', line):
                written.add(match.group(1))
            for match in re.finditer(r'^\s*"phase":\s*"([A-Z_]+)"', line):
                written.add(match.group(1))
        # `publish_plan_revision` writes through a local name (asserted by
        # workflow_state_test.TestPersistedPhaseWriterCensus); since
        # workflow-2.6.0 only its `"1"` constant remains, and
        # `bind_plan_review_bundle` writes `AWAITING_LOCAL_PLAN_REVIEW` as a
        # literal -- the set below stays a superset either way.
        written |= {"AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_EXTERNAL_PLAN_REVIEW"}
        # workflow-2.5.0: `record_bundle_generation` writes through a call
        # to `bundle_generation_target_phase(...)`, not a literal or a
        # local name this regex census follows -- the same kind of
        # indirection as `publish_plan_revision`'s own two constants
        # above, resolved the identical way (asserted by
        # workflow_state_completion_obligations_test.TestNeverPersistedPhaseVocabulary's
        # own Call-branch AST resolution).
        written |= {"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}
        self.assertEqual(set(_reference_phase_table("**Persisted phases**")), written)
        self.assertEqual(
            set(_reference_phase_table("**Declared but never written**")),
            ws.KNOWN_PHASES - written,
        )
        self.assertGreater(len(writers), 10)

    def test_every_persisted_phase_row_names_its_real_writer(self):
        rows = _reference_phase_table("**Persisted phases**")
        expected = {
            "PLANNING": ["default_work_item"],
            "AWAITING_EXTERNAL_PLAN_REVIEW": ["publish_plan_revision"],
            # workflow-2.6.0 (D-Plan-Review-Bundle-Binding): the bind is the
            # sole writer; the withdrawal is the verdict-free exit.
            "AWAITING_LOCAL_PLAN_REVIEW": ["bind_plan_review_bundle"],
            "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": ["record_local_plan_review"],
            "REVISING_PLAN": ["record_local_plan_review", "record_manual_plan_review",
                              "withdraw_plan_review"],
            "AWAITING_PLAN_APPROVAL": ["record_manual_plan_review"],
            "IMPLEMENTING": ["apply_plan_approval"],
            "SELF_REVIEWING_IMPLEMENTATION": ["complete_checkpoint",
                                              "enter_self_reviewing_implementation"],
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW": ["record_bundle_generation"],
            "APPLYING_REVIEW_FEEDBACK": ["enter_applying_review_feedback"],
            "AWAITING_FUNCTIONAL_REVIEW": ["apply_technical_approval",
                                           "promote_legacy_work_item"],
            "MILESTONE_COMPLETE": ["complete_work_item"],
            "LEGACY_READY": ["import_legacy_work_item"],
            "AMENDING_PLAN": ["request_plan_amendment", "withdraw_plan_review"],
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW": ["record_bundle_generation"],
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": ["record_local_implementation_review"],
        }
        for phase, names in expected.items():
            for name in names:
                self.assertIn(name, rows[phase], f"{phase} row omits {name}")

    def test_the_happy_path_does_not_route_an_approve_through_the_revise_command(self):
        """Finding `A2`: the happy path had implementation-review `APPROVE`
        go through `/apply-implementation-review`, whose step 7 republishes
        the bundle and invalidates the very `APPROVE` being acted on."""
        text = _operator_reference_text()
        section = text.split("## Typical workflow", 1)[1].split("\n## ", 1)[0]
        happy = section.split("```", 2)[1]
        self.assertNotIn("/apply-implementation-review", happy)
        self.assertIn("/approve-review implementation (you)→ AWAITING_FUNCTIONAL_REVIEW", happy)
        self.assertNotIn("AWAITING_TECHNICAL_APPROVAL", happy)
        # ...and the prose right after it says why, rather than leaving the
        # omission to be read as an oversight.
        self.assertIn("is the `REVISE` path, not the `APPROVE` path", section)
        # And the harm is stated where the command is described.
        self.assertIn("FeedbackBundleMismatchError", text)
        self.assertIn("USER_OVERRIDE", text)

    def test_the_state_writer_false_claim_matches_the_frontmatter(self):
        """Finding `A3`: `/prepare-review` was called "the only command with
        `state_writer: false`". There were three -- `workflow-2.5.0`
        reclassified `/review-implementation`'s own frontmatter from
        `false` to `true` (it is the authoritative
        `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage writer for a `"2.2"` item
        at `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`), so exactly two
        remain."""
        stems = _declared_state_writer_false()
        self.assertEqual(stems, {"prepare-review", "review-functional"})
        text = _operator_reference_text()
        self.assertNotIn("The only command with\n  `state_writer: false`", text)
        self.assertIn(
            "One of the two commands\n  unconditionally declaring `state_writer: false`",
            text,
        )
        for stem in sorted(stems - {"prepare-review"}):
            self.assertIn(f"`/{stem}`", text)

    def test_the_canonical_reviewer_roles_are_presented_as_canonical(self):
        """Finding `A4`: the lowercase spellings are legacy compatibility
        values `_normalize_plan_review_stage_key` maps *to* the canonical
        ones -- never what a reviewer should write."""
        self.assertEqual(ws.LOCAL_MODEL_PLAN_REVIEW, "LOCAL_MODEL_PLAN_REVIEW")
        self.assertEqual(ws.MANUAL_EXTERNAL_PLAN_REVIEW, "MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(
            ws._normalize_plan_review_stage_key("manual_external_plan_review"),
            ws.MANUAL_EXTERNAL_PLAN_REVIEW,
        )
        text = _operator_reference_text()
        self.assertNotIn("`Reviewer role: manual_external_plan_review` **exactly**", text)
        self.assertIn("`Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW`", text)
        self.assertIn("`Reviewer role:\n  LOCAL_MODEL_PLAN_REVIEW`", text)
        self.assertIn("legacy compatibility values", text)
        # Every remaining lowercase mention must sit in a paragraph that
        # says it is legacy, exactly like the retirement-note rule.
        for para in text.split("\n\n"):
            if "manual_external_plan_review" not in para and "local_model_plan_review" not in para:
                continue
            self.assertTrue(
                "legacy" in para.lower(),
                f"a paragraph uses a lowercase reviewer role without calling it legacy:\n{para}",
            )

    def test_apply_functional_review_is_not_documented_as_entering_an_unwritten_phase(self):
        """Finding `A5`: `FIXING_FUNCTIONAL_FINDINGS` is declared and never
        written, so "enters `FIXING_FUNCTIONAL_FINDINGS`" was a phase an
        operator could never observe."""
        text = _operator_reference_text()
        self.assertNotIn("enters\n  `FIXING_FUNCTIONAL_FINDINGS`", text)
        self.assertIn("nothing writes that phase", text)
        self.assertIn("BundleGenerationRequiresStaleTechnicalApprovalError", text)

    def test_accept_milestone_refusals_include_the_completion_obligations(self):
        """Finding `A6`, from the reference's side."""
        text = _operator_reference_text()
        section = text.split("### `/accept-milestone", 1)[1].split("\n### ", 1)[0]
        self.assertIn("UnsatisfiedCompletionObligationError", section)
        for obligation_id in sorted(ws.COMPLETION_OBLIGATION_CONFORMANCE):
            self.assertIn(obligation_id, section)

    def test_the_plan_review_workflow_provenance_claim_is_true(self):
        """Finding `A7`: the discrepancy list asserted that
        `PLAN_REVIEW_WORKFLOW.md` documents `Reviewer role:`. It does not
        mention it at all."""
        plan_review = (_repo_root() / "docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md").read_text()
        milestone = (_repo_root() / "docs/ai-workflow/MILESTONE_WORKFLOW.md").read_text()
        self.assertNotIn("Reviewer role", plan_review)
        self.assertIn("Reviewer role", milestone)
        text = _operator_reference_text()
        self.assertIn("`PLAN_REVIEW_WORKFLOW.md` does **not**", text)

    def test_the_command_inventory_claim_states_the_real_guarantee(self):
        """Finding `A8`: "not hand-maintained" was too strong. The set
        equality is derived; the count is a hardcoded tripwire."""
        text = _operator_reference_text()
        self.assertNotIn("not\nhand-maintained", text)
        self.assertIn("assertEqual(len(on_disk), 16)", text)
        self.assertIn("hardcoded tripwire", text)
        # And the claim it makes about the derivation is itself true.
        sections = re.findall(r"(?m)^### `/([a-z0-9-]+)", text)
        on_disk = sorted(p.stem for p in (_repo_root() / ".claude" / "commands").glob("*.md"))
        self.assertEqual(sorted(sections), on_disk)

    def test_every_command_section_heading_states_the_declared_arguments(self):
        """The reference's `### /<name> <args>` heading and the command's own
        `argument-hint` are two statements of the same contract, and `A9`
        was a case of them disagreeing silently."""
        headings = dict(re.findall(r"(?m)^### `/([a-z0-9-]+)([^`]*)`",
                                   _operator_reference_text()))
        mismatched = []
        for path in sorted((_repo_root() / ".claude" / "commands").glob("*.md")):
            hint = _argument_hint(path.name) or ""
            heading = headings.get(path.stem, "<<no section>>").strip()
            if heading != hint:
                mismatched.append((path.stem, hint, heading))
        self.assertEqual(mismatched, [])

    def test_the_v1_plan_approval_gate_is_not_described_as_a_phase(self):
        """Re-audit finding `O20`: `record_manual_plan_review` is the only
        writer of `AWAITING_PLAN_APPROVAL` and refuses a `"1"` item
        outright, so a `"1"` item never occupies that phase -- yet the
        reference showed it as that version's next state."""
        source = inspect.getsource(ws.record_manual_plan_review)
        self.assertIn("validate_manual_plan_review_preconditions", source)
        # workflow-2.5.0: widened from a bare `!= "2.1"` literal to
        # `TWO_STAGE_PLAN_REVIEW_VERSIONS` membership (D-Implementation-
        # Review-Version-Activation's inheritance rule -- a `"2.2"` item
        # runs the identical two-stage plan-review protocol a `"2.1"` item
        # does), so the substring this test pins moves with it.
        self.assertIn(
            "not in TWO_STAGE_PLAN_REVIEW_VERSIONS",
            inspect.getsource(ws._require_v2_1_plan_review),
        )
        text = _operator_reference_text()
        self.assertNotIn("`/apply-plan-review` → `AWAITING_PLAN_APPROVAL`", text)
        self.assertIn("a `\"1\"` item never occupies that phase", text)

    def test_prepare_functional_review_is_not_credited_with_guards_it_lacks(self):
        """Re-audit finding `O19`."""
        command = _command_text("prepare-functional-review.md")
        self.assertNotIn("technical_approval.status", command)
        text = _operator_reference_text()
        self.assertNotIn("**Expects**: `technical_approval.status == CURRENT`", text)
        self.assertIn("It has **no** phase\n  guard and **no** `technical_approval` "
                      "precondition of its own", text)

    #: Names the reference uses that are `WORKFLOW_STATE.json`/registry
    #: fields, config keys, bundle-identity values or literal enum values
    #: -- data, not callables, so they resolve nowhere in the two modules.
    #: Anything else in backticks that looks like an identifier has to be a
    #: real symbol.
    NON_SYMBOL_NAMES = frozenset({
        "active_work_item_id", "base_commit", "bundle_id",
        "default_workflow_version", "generation_head",
        "governing_workflow_version", "implementation_revision",
        "last_transition", "local_model_plan_review",
        "manual_external_plan_review", "mapping_path", "parent_work_item_id",
        "plan_approval", "plan_path", "plan_review_stages", "registry_path",
        "review_content_id", "reviewed_implementation_head", "same_content",
        "state_revision", "technical_approval", "work_item_id",
        # workflow-2.9.0, `/retire-legacy-work-item`: a state field name and a
        # stable refusal code, not code symbols.
        "current_checkpoint_id", "reopen_retired_legacy_item", "registry_incomplete",
        # workflow-2.9.0, `/resume-implementation`: the technical approval's status and a stable
        # refusal-class prefix, not code symbols.
        "technical_approval.status",
        "work_item_kind", "work_item_type", "work_items", "worktree_root",
        "test_the_operator_reference_command_count_matches_reality",
        # workflow-2.4.0, D-Plan-Amendment-3: amendment_history entry/
        # top-level state field names, not code symbols.
        "amendment_base_commit", "amendment_history",
        "pre_amendment_approval_commit",
        # workflow-2.5.0, D-Implementation-Review-Stages: the implementation-
        # side mirror of `plan_review_stages`, a state field name, not a
        # callable; `user_confirmation` is `plan_approval`'s own existing
        # field, first referenced by name in this release's reference text.
        "implementation_review_stages", "user_confirmation",
        # workflow-2.7.0, D-Consumed-History (v2.6.0-001): the durable
        # consumed history, a work-item field name, not a callable.
        "consumed_plan_review_content_ids",
        # workflow-2.7.0, Orchestration Protocol v1: next-action's
        # disposition values, literal enum values, not callables.
        "external_gate", "human_gate",
        # workflow-2.8.0, D-GP-Policy: the two optional top-level state field
        # names, not callables.
        "gate_policy_adoption", "gate_policy_floor",
        # workflow-2.8.0 CP2, D-GP-Gates: a `require` entry, the requirement
        # id that audits the ledger, and the approval record's evidence key,
        # not callables.
        "distinct_reviewer_models", "review_evidence_audited", "policy_evidence",
        # workflow-2.8.0 CP5, D-GP-Reopen: the pull-request fact's cause names,
        # the policy keys, the refusal codes and the two key sets, literal
        # values and field names, not callables.
        "changes_requested", "checks_failed", "content_changed", "pr_merged",
        "pr_review", "reopen_on", "reopen_plan_archived", "reopened_for",
        "workflow_gh",
    })

    def test_every_code_symbol_the_reference_names_actually_exists(self):
        """A reference that names a helper which does not exist sends an
        operator (or an agent) looking for it. This caught
        `normalize_plan_review_stage_keys`, which is not a function --
        `normalize_plan_review_stages` is the read-site helper and
        `migrate_plan_review_stage_keys` is the one-time migration."""
        text = _operator_reference_text()
        names = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*(?:Error|_[a-z0-9_]+))`", text))
        names |= set(re.findall(r"`([A-Z][A-Za-z0-9]*Error)`", text))
        names |= set(re.findall(r"`([a-z_][a-z0-9_]{4,})\(", text))
        unresolved = sorted(
            name for name in names
            if name not in self.NON_SYMBOL_NAMES
            and not hasattr(ws, name) and not hasattr(fingerprint, name)
        )
        self.assertEqual(unresolved, [])
        # And the allowlist stays honest: nothing in it may become a symbol
        # without being reclassified.
        shadowed = sorted(n for n in self.NON_SYMBOL_NAMES
                          if hasattr(ws, n) or hasattr(fingerprint, n))
        self.assertEqual(shadowed, [])

    def test_the_enter_the_state_preamble_convention_is_documented_correctly(self):
        """Re-audit finding `O22`: eleven command files open with an
        `Enter ...` line naming a phase -- inherited v1 wording that
        does not mean the command writes `X` -- which is the root of the
        `FIXING_FUNCTIONAL_FINDINGS` and `AWAITING_TECHNICAL_APPROVAL`
        confusion. The reference's three-way table is checked here against
        what each file actually calls."""
        writers = {}
        for line in (_repo_root() / "scripts" / "workflow_state.py").read_text().splitlines():
            match = re.match(r"^def (\w+)", line)
            if match:
                current = match.group(1)
            for m in re.finditer(r'\["phase"\]\s*=\s*"([A-Z_]+)"', line):
                writers.setdefault(m.group(1), set()).add(current)
            for m in re.finditer(r'^\s*"phase":\s*"([A-Z_]+)"', line):
                writers.setdefault(m.group(1), set()).add(current)

        # Every `Enter ...` preamble, in all three phrasings the command
        # files use -- matching only the narrow "Enter the `X` state" form
        # silently missed `/approve-review` and `/record-manual-plan-review`.
        preambles = {}
        for path in sorted((_repo_root() / ".claude" / "commands").glob("*.md")):
            for line in path.read_text().splitlines():
                if line.startswith("Enter "):
                    preambles[path.stem] = re.findall(r"`([A-Z_]+)`", line)
                    break
        self.assertEqual(sorted(preambles), [
            "accept-milestone", "apply-functional-review",
            "apply-implementation-review", "apply-plan-review",
            "approve-review", "milestone-implement", "milestone-plan",
            "prepare-functional-review", "record-manual-implementation-review",
            "record-manual-plan-review",
            "request-plan-amendment", "review-plan",
        ])
        # Every command with such a preamble appears in the reference's table.
        table = _operator_reference_text().split(
            '### "Enter the `X` state" in a command file', 1)[1].split("\n\nIf you", 1)[0]
        for stem in preambles:
            self.assertIn(f"`/{stem}`", table, stem)
        # `/approve-review`'s preamble names a phase nothing writes.
        self.assertIn("AWAITING_TECHNICAL_APPROVAL", preambles["approve-review"])
        # The one that names a phase nothing writes.
        self.assertEqual(preambles["apply-functional-review"], ["FIXING_FUNCTIONAL_FINDINGS"])
        self.assertNotIn("FIXING_FUNCTIONAL_FINDINGS", writers)
        text = _operator_reference_text()
        self.assertIn('### "Enter the `X` state" in a command file', text)
        self.assertIn("does **not** mean the command writes\nthat phase", text)
        self.assertIn("It names a **gate**, not a phase", text)
        self.assertIn("which nothing writes at all", text)
        self.assertIn("never a command's opening line", text)

    def test_the_v1_plan_lane_persists_only_three_phases(self):
        """Re-audit finding `O22`'s second half: `REVISING_PLAN` shares
        `AWAITING_PLAN_APPROVAL`'s version scoping -- both are written only
        by the two-stage verdict commands, which refuse a `"1"` item."""
        for name in ("record_local_plan_review", "record_manual_plan_review"):
            source = inspect.getsource(getattr(ws, name))
            self.assertIn("validate_", source)
        for name in ("validate_local_plan_review_preconditions",
                     "validate_manual_plan_review_preconditions"):
            self.assertIn("_require_v2_1_plan_review", inspect.getsource(getattr(ws, name)))
        text = _operator_reference_text()
        self.assertIn("| `REVISING_PLAN` (2.1) |", text)
        self.assertIn("whole plan lane persists exactly three phases", text)

    def test_the_remediation_child_section_names_every_lifecycle_command(self):
        """The operator-facing counterpart of
        `TestWorkItemTargetingContract`: the reference's own child sequence
        must cover the same derived command set the runtime instruction
        does."""
        text = _operator_reference_text()
        section = text.split("## Remediation children", 1)[1]
        named = set(re.findall(r"/([a-z0-9-]+)(?: plan| implementation)? <child-id>", section))
        expected = {
            filename[:-3] for filename in
            _commands_naming_a_phase_writer() - _TARGETING_EXEMPT_COMMANDS - _CHILD_SEQUENCE_EXEMPT_COMMANDS
        }
        self.assertEqual(expected - named, set())
        self.assertIn("never `active_work_item_id`", section)
        self.assertIn("IncompleteChildWorkItemError", section)


def _doc_text(name: str) -> str:
    """A shipped document with Markdown emphasis removed and whitespace
    collapsed, so a sentence wrapped across lines reads as one."""
    text = (_repo_root() / "docs" / "ai-workflow" / name).read_text()
    return " ".join(text.replace("**", "").split())


class TestGatePolicyDocuments(unittest.TestCase):
    """workflow-2.8.0 CP7: the operator guide (`GATE_POLICY.md`) and the
    protocol specification state the trust boundary, the threat model and the
    reopening residuals in the plan's words, and offer no CI-produced
    evidence."""

    GUIDE = "GATE_POLICY.md"
    PROTOCOL = "ORCHESTRATION_PROTOCOL.md"

    TRUST_BOUNDARY = (
        "CI and pull-request facts that satisfy a gate come from GitHub",
        "only tightens",
        "a gate that cannot decide blocks",
        "Review verdicts and functional evidence are trusted from the orchestrator",
        "The Workflow guarantees binding, freshness and audit",
        "does not guarantee provenance",
        "Turning human approval on is the stronger mode",
    )

    def test_the_guide_and_the_protocol_state_the_trust_boundary_and_the_stronger_mode(self):
        guide = _doc_text(self.GUIDE)
        protocol = _doc_text(self.PROTOCOL)
        # The guide's wording differs slightly from the protocol's in the
        # first sentence only; each document states every other sentence.
        for sentence in self.TRUST_BOUNDARY[1:]:
            with self.subTest(sentence=sentence, doc="guide"):
                self.assertIn(sentence.lower(), guide.lower())
            with self.subTest(sentence=sentence, doc="protocol"):
                self.assertIn(sentence.lower(), protocol.lower())
        for text in (guide, protocol):
            self.assertIn("CI and pull-request facts", text)
            self.assertIn("come from GitHub", text)
            self.assertIn("Turning human approval on is the stronger mode", text)
        self.assertIn("verdict hash", guide)
        self.assertIn("bundle id", guide)
        self.assertIn("run reference", guide)
        self.assertIn("verdict hash", protocol)
        self.assertIn("bundle and content ids", protocol)
        self.assertIn("run reference", protocol)

    def test_the_guide_carries_the_named_threat_model_with_its_three_statements(self):
        raw = (_repo_root() / "docs" / "ai-workflow" / self.GUIDE).read_text()
        self.assertEqual(len(re.findall(r"^## Threat model$", raw, re.M)), 1)
        section = " ".join(raw.split("\n## Threat model", 1)[1].split("\n## ", 1)[0].replace("**", "").split())
        for label in ("What is guaranteed.", "What is not guaranteed.", "The safeguards that remain"):
            self.assertIn(label, section)
        self.assertLess(section.index("What is guaranteed."), section.index("What is not guaranteed."))
        self.assertLess(section.index("What is not guaranteed."), section.index("The safeguards that remain"))
        self.assertIn("deliberately forges a commit, a trailer or the state", section)
        self.assertIn("replaces a system program", section)
        self.assertIn("No signed commits and no GitHub-side adoption are provided", section)
        # the resolution rule for `gh` and what it records
        for fragment in ("absolute path", "inside the repository", "its worktrees", "temporary directory",
                         "world-writable", "records the resolved path and the executable's sha256"):
            self.assertIn(fragment, section)
        # the gate-lowering event and where it is reported
        guide = _doc_text(self.GUIDE)
        self.assertIn("gate-lowering event", guide)
        for place in ("`verify`", "`next-action`", "audit record"):
            self.assertIn(place, guide)
        self.assertIn("stronger mode", section)
        protocol = _doc_text(self.PROTOCOL)
        self.assertIn("D-GP-ThreatModel", protocol)
        self.assertIn("D-GP-Trust", protocol)
        self.assertIn("GATE_POLICY.md", protocol)

    def test_neither_document_offers_ci_produced_functional_or_review_evidence(self):
        for name in (self.GUIDE, self.PROTOCOL):
            text = _doc_text(name)
            self.assertIn("produced in CI is not accepted, and no policy option offers it", text, name)
            for match in re.finditer(r"(?i)produced in CI|CI-produced|CI result kind|ci_result", text):
                tail = text[match.start():match.end() + 80]
                self.assertTrue(
                    re.search(r"(?i)not accepted|no CI result kind|There is no|No CI-produced", text[max(0, match.start() - 60):match.end() + 80]),
                    f"{name}: {tail!r}",
                )

    def test_the_guide_states_both_reopening_residuals_the_second_with_its_human_acceptance_case(self):
        text = _doc_text(self.GUIDE)
        # first residual: enabling a cause never reopens a completed item from a stored fact
        self.assertIn("Enabling a reopening cause never reopens a completed item from a stored fact alone", text)
        self.assertIn("re-queries GitHub first", text)
        # second residual
        self.assertIn("a same-head red reopen with no orchestrator report is never remediated", text)
        self.assertIn("at `MILESTONE_COMPLETE`", text)
        self.assertIn("under human acceptance without `requires_pr_approved`, at `AWAITING_FUNCTIONAL_REVIEW`", text)
        self.assertIn("where no Workflow query runs", text)
        self.assertIn("A human-acceptance repository must have an orchestrator report it to have it acted on", text)
        # a reopened item is surfaced only by naming it, and keeps its completed narrative state
        self.assertIn("A reopened item is surfaced only by naming it", text)
        self.assertIn("/apply-pr-review", text)
        self.assertIn("--work-item", text)
        self.assertIn("the roadmap row stays complete and `docs/ACTIVE_MILESTONE.md` stays cleared", text)

    def test_the_guide_states_the_toggles_the_floor_and_the_single_subscription_paths(self):
        text = _doc_text(self.GUIDE)
        raw = (_repo_root() / "docs" / "ai-workflow" / self.GUIDE).read_text()
        self.assertIn('{"schema_version": 1, "human_approval": true}', raw)
        self.assertIn("one-line way back to human gates", text)
        self.assertIn("The default is different, on purpose", text)
        for fragment in ("provenance_failed", "a new `/adopt-gate-policy` commit", "gate_policy_floor",
                         "Automatic acceptance needs an open pull request first",
                         "`distinct_reviewer_models` is on by default",
                         "Record a second family at ingest", "Turn that gate human",
                         "Adopt a policy without the requirement", "before the stage's bundle is generated",
                         "/recover-implementation-provenance", "permanent `warn`", "gate_lowering",
                         "anthropic/...", "openai/...", "declared and unverified",
                         "requires_pr_approved", "Order for"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_the_protocol_states_completion_the_default_and_the_consumer_bound(self):
        text = _doc_text(self.PROTOCOL)
        self.assertNotIn("No automatic action has an edge to `MILESTONE_COMPLETE`", text)
        self.assertIn("`acceptance.satisfy` is a `validation` action", text)
        self.assertIn("its arrival there is classed `progress`", text)
        self.assertIn("`/accept-milestone` stays the writer only for a human gate", text)
        self.assertIn("the default switches the gates to automatic", text)
        self.assertIn('`"human_approval": true`, which is the one-line way back', text)
        self.assertIn("consecutive `no_progress` results of the **same** action id at an **unchanged** `state_identity`".replace("**", ""), text)
        # the edge table carries every .satisfy forward and same-phase edge and pr.apply_review's completed-item edges
        raw = (_repo_root() / "docs" / "ai-workflow" / self.PROTOCOL).read_text()
        for row in (
            "| `plan.satisfy` | `AWAITING_PLAN_APPROVAL` | `IMPLEMENTING` | `2.1`, `2.2` |",
            "| `plan.satisfy` | `AWAITING_PLAN_APPROVAL` | `AWAITING_PLAN_APPROVAL` | `2.1`, `2.2` |",
            "| `implementation.satisfy` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `AWAITING_FUNCTIONAL_REVIEW` | `2.2` |",
            "| `implementation.satisfy` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` |",
            "| `acceptance.satisfy` | `AWAITING_FUNCTIONAL_REVIEW` | `MILESTONE_COMPLETE` | `1`, `2.1`, `2.2` |",
            "| `acceptance.satisfy` | `AWAITING_FUNCTIONAL_REVIEW` | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` |",
            "| `pr.apply_review` | `MILESTONE_COMPLETE` | `AWAITING_FUNCTIONAL_REVIEW` | `1`, `2.1`, `2.2` |",
            "| `pr.apply_review` | `MILESTONE_COMPLETE` | `MILESTONE_COMPLETE` | `1`, `2.1`, `2.2` |",
        ):
            with self.subTest(row=row):
                self.assertIn(row, raw)

    def test_the_other_gate_documents_point_at_the_guide(self):
        for name in ("MILESTONE_WORKFLOW.md", "WORKFLOW_V2_1_OPERATOR_REFERENCE.md", "REVIEW_PROTOCOL.md",
                     self.PROTOCOL):
            with self.subTest(doc=name):
                self.assertIn("GATE_POLICY.md", _doc_text(name))
        self.assertIn("automatic by default", _doc_text("WORKFLOW_V2_1_OPERATOR_REFERENCE.md"))
        self.assertIn("By default they are automatic", _doc_text("MILESTONE_WORKFLOW.md"))

    def test_the_new_and_edited_documents_pass_the_installed_documentation_sweeps(self):
        docs = _repo_root() / "docs" / "ai-workflow"
        names = (self.GUIDE, self.PROTOCOL, "MILESTONE_WORKFLOW.md", "WORKFLOW_V2_1_OPERATOR_REFERENCE.md",
                 "REVIEW_PROTOCOL.md")
        texts = {str(docs / name): (docs / name).read_text() for name in names}
        findings = ws.sweep_governing_version_enumeration(texts)
        self.assertEqual(findings, [], [repr(f) for f in findings])
        claims = ws.sweep_applying_review_feedback_version_claims(texts)
        self.assertEqual(claims, [], [repr(f) for f in claims])
        # the new document quotes no governing-version list at all
        guide = texts[str(docs / self.GUIDE)]
        self.assertNotRegex(guide, r'"1"\s*(?:,|/)\s*"2\.1"|"2\.1"\s*(?:,|/)\s*"2\.2"')


# ---------------------------------------------------------------------------
# The lifecycle diagram, checked against the code and against itself
# (convergence campaign, ledger rows `B12`/`O18`).
#
# `docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg` carries the
# diagram twice: as the drawio `mxfile` in the `<svg content="...">`
# attribute, and as hand-authored SVG primitives in the body. Nothing kept
# the two in agreement, and nothing kept either in agreement with
# `workflow_state.py` -- which is how the diagram came to show
# `AWAITING_TECHNICAL_APPROVAL` as a live persisted gate, `recovered` as a
# `record_bundle_generation` outcome, a duplicated edge, two missing edges,
# and a note painted over a live box.
# ---------------------------------------------------------------------------


_DIAGRAM = "docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg"


def _diagram_source() -> str:
    return (_repo_root() / _DIAGRAM).read_text()


def _diagram_model():
    """`{id: (x, y, w, h, value)}` for vertices and `{id: [(x, y), ...]}` for
    edges, read from the embedded drawio model."""
    source = _diagram_source()
    content = html.unescape(re.search(r'\scontent="([^"]*)"', source).group(1))
    root = ET.fromstring(content)
    vertices, edges = {}, {}
    for cell in root.findall(".//mxCell"):
        geometry = cell.find("mxGeometry")
        if cell.get("vertex") == "1":
            vertices[cell.get("id")] = (
                float(geometry.get("x")), float(geometry.get("y")),
                float(geometry.get("width")), float(geometry.get("height")),
                cell.get("value") or "", cell.get("style") or "",
            )
        elif cell.get("edge") == "1":
            points = []
            source_point = geometry.find('mxPoint[@as="sourcePoint"]')
            target_point = geometry.find('mxPoint[@as="targetPoint"]')
            waypoints = geometry.find('Array[@as="points"]')
            points.append((float(source_point.get("x")), float(source_point.get("y"))))
            if waypoints is not None:
                points += [(float(p.get("x")), float(p.get("y")))
                           for p in waypoints.findall("mxPoint")]
            points.append((float(target_point.get("x")), float(target_point.get("y"))))
            edges[cell.get("id")] = (points, cell.get("value") or "")
    return vertices, edges


def _diagram_body_shapes():
    """`(x, y, w, h)` for every `<rect>`/`<polygon>` drawn in the SVG body,
    excluding the page background and the white label backings."""
    source = _diagram_source()
    body = source[source.index('">', source.index('content="')) + 2:]
    shapes = []
    for m in re.finditer(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                         r'height="([\d.]+)"([^>]*)/>', body):
        if 'opacity="0.92"' in m.group(5) or m.group(1) == "0":
            continue
        shapes.append(tuple(float(m.group(i)) for i in (1, 2, 3, 4)))
    for m in re.finditer(r'<polygon points="([^"]+)"', body):
        pts = [(float(a), float(b)) for a, b in
               re.findall(r'([-\d.]+),([-\d.]+)', m.group(1))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        shapes.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    return shapes


def _diagram_body_paths():
    source = _diagram_source()
    body = source[source.index('">', source.index('content="')) + 2:]
    paths = []
    for m in re.finditer(r'<path d="(M [^"]+)" fill="none"', body):
        paths.append([(float(a), float(b)) for a, b in
                      re.findall(r'([-\d.]+),([-\d.]+)', m.group(1))])
    return paths


def _diagram_text() -> str:
    """All rendered text in the diagram body, whitespace-normalized. A line
    that ends mid-word at a wrap (`/review-` + `implementation`) is rejoined,
    so a command name split across two `<text>` elements still reads as one
    token."""
    source = _diagram_source()
    body = source[source.index('">', source.index('content="')) + 2:]
    chunks = [html.unescape(c) for c in re.findall(r'<text[^>]*>(.*?)</text>', body, re.S)]
    out = ""
    for chunk in chunks:
        if out.endswith("-") and chunk[:1].islower():
            out += chunk
        else:
            out = (out + " " + chunk) if out else chunk
    return out


class TestLifecycleDiagramMatchesTheModelItCarries(unittest.TestCase):

    def test_every_model_vertex_is_drawn_at_the_same_geometry(self):
        vertices, _ = _diagram_model()
        drawn = {tuple(round(v, 1) for v in s) for s in _diagram_body_shapes()}
        missing = []
        for vid, (x, y, w, h, _value, style) in sorted(vertices.items()):
            if style.startswith("text;"):
                continue           # caption vertices draw a <text>, no box
            if (round(x, 1), round(y, 1), round(w, 1), round(h, 1)) not in drawn:
                missing.append(vid)
        self.assertEqual(missing, [])

    def test_every_model_edge_is_drawn_with_the_same_polyline(self):
        _, edges = _diagram_model()
        drawn = {tuple(tuple(round(v, 1) for v in p) for p in path)
                 for path in _diagram_body_paths()}
        missing = [eid for eid, (points, _) in sorted(edges.items())
                   if tuple(tuple(round(v, 1) for v in p) for p in points) not in drawn]
        self.assertEqual(missing, [])

    def test_the_body_draws_nothing_the_model_does_not_declare(self):
        vertices, edges = _diagram_model()
        declared_boxes = {(round(x, 1), round(y, 1), round(w, 1), round(h, 1))
                          for x, y, w, h, _v, style in vertices.values()
                          if not style.startswith("text;")}
        extra = [s for s in _diagram_body_shapes()
                 if tuple(round(v, 1) for v in s) not in declared_boxes]
        self.assertEqual(extra, [])
        self.assertEqual(len(_diagram_body_paths()), len(edges))

    def test_no_duplicate_edges(self):
        """`C3`: the all-checkpoints-complete transition was drawn twice,
        once unlabelled, so the same arrow rendered on top of itself."""
        _, edges = _diagram_model()
        seen = {}
        for eid, (points, _label) in edges.items():
            key = tuple(points)
            self.assertNotIn(key, seen, f"{eid} duplicates {seen.get(key)}")
            seen[key] = eid


class TestLifecycleDiagramLayout(unittest.TestCase):
    """`C1`, `C2`, `C5`: what the diagram *looks like* is part of what it
    says. A note painted over a live box, an arrow running underneath the
    note column, or a label sitting on top of a box are all defects, and
    all three were present."""

    #: The only edge crossings the layout accepts, each between a solid grey
    #: edge and a dashed red one, so the two are never confusable. Both are
    #: topologically forced: three edges leave lane 2 leftwards at
    #: interleaved heights.
    ALLOWED_CROSSINGS = {("e31", "e35"), ("e33", "e35")}

    def _boxes(self):
        vertices, _ = _diagram_model()
        return {vid: (x, y, x + w, y + h)
                for vid, (x, y, w, h, _v, style) in vertices.items()
                if not style.startswith("text;")}

    def test_no_box_overlaps_another(self):
        boxes = self._boxes()
        overlaps = []
        for (a, ra), (b, rb) in itertools.combinations(sorted(boxes.items()), 2):
            if (min(ra[2], rb[2]) - max(ra[0], rb[0]) > 0
                    and min(ra[3], rb[3]) - max(ra[1], rb[1]) > 0):
                overlaps.append((a, b))
        self.assertEqual(overlaps, [])

    def test_no_edge_segment_runs_underneath_a_box(self):
        boxes = self._boxes()
        _, edges = _diagram_model()
        hidden = []
        for eid, (points, _label) in sorted(edges.items()):
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                for bid, (bx0, by0, bx1, by1) in boxes.items():
                    if x0 == x1 and bx0 + 1 < x0 < bx1 - 1:
                        lo, hi = sorted((y0, y1))
                        if lo < by1 - 1 and hi > by0 + 1:
                            hidden.append((eid, bid))
                    elif y0 == y1 and by0 + 1 < y0 < by1 - 1:
                        lo, hi = sorted((x0, x1))
                        if lo < bx1 - 1 and hi > bx0 + 1:
                            hidden.append((eid, bid))
        self.assertEqual(hidden, [])

    def test_no_edge_label_sits_on_top_of_a_box(self):
        source = _diagram_source()
        body = source[source.index('">', source.index('content="')) + 2:]
        boxes = self._boxes()
        collisions = []
        for m in re.finditer(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                             r'height="13.0" fill="#ffffff" opacity="0.92"/>'
                             r'<text[^>]*>([^<]*)</text>', body):
            lx0, ly0, lw = (float(m.group(i)) for i in (1, 2, 3))
            lx1, ly1 = lx0 + lw, ly0 + 13
            for bid, (bx0, by0, bx1, by1) in boxes.items():
                if lx0 < bx1 - 1 and lx1 > bx0 + 1 and ly0 < by1 - 1 and ly1 > by0 + 1:
                    collisions.append((m.group(4), bid))
        self.assertEqual(collisions, [])

    def test_edge_crossings_are_limited_to_the_documented_set(self):
        _, edges = _diagram_model()

        def crosses(s1, s2):
            (ax0, ay0), (ax1, ay1) = s1
            (bx0, by0), (bx1, by1) = s2
            if ax0 == ax1 and by0 == by1:
                return (min(bx0, bx1) < ax0 < max(bx0, bx1)
                        and min(ay0, ay1) < by0 < max(ay0, ay1))
            if ay0 == ay1 and bx0 == bx1:
                return crosses(s2, s1)
            return False

        found = set()
        for (e1, (p1, _)), (e2, (p2, _)) in itertools.combinations(sorted(edges.items()), 2):
            for s1 in zip(p1, p1[1:]):
                for s2 in zip(p2, p2[1:]):
                    if crosses(s1, s2):
                        found.add(tuple(sorted((e1, e2))))
        self.assertEqual(found, self.ALLOWED_CROSSINGS)

    def test_nothing_is_drawn_outside_the_canvas(self):
        source = _diagram_source()
        m = re.search(r'<svg [^>]*width="([\d.]+)" height="([\d.]+)"', source)
        width, height = float(m.group(1)), float(m.group(2))
        for x, y, w, h in _diagram_body_shapes():
            self.assertTrue(0 <= x and 0 <= y and x + w <= width and y + h <= height,
                            f"box {(x, y, w, h)} outside {width}x{height}")
        for path in _diagram_body_paths():
            for x, y in path:
                self.assertTrue(0 <= x <= width and 0 <= y <= height, f"point {(x, y)}")


class TestLifecycleDiagramMatchesTheCode(unittest.TestCase):

    def test_every_phase_the_diagram_draws_as_a_box_has_a_writer(self):
        """`B4`/`B5`: a solid box in this diagram is a place a work item
        really sits. The four phases nothing writes must not be drawn as
        boxes at all."""
        vertices, _ = _diagram_model()
        writers = _phase_writing_state_functions()
        self.assertGreater(len(writers), 10)
        written = set()
        for line in (_repo_root() / "scripts" / "workflow_state.py").read_text().splitlines():
            written |= set(re.findall(r'\["phase"\]\s*=\s*"([A-Z_]+)"', line))
            written |= set(re.findall(r'^\s*"phase":\s*"([A-Z_]+)"', line))
        written |= {"AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_EXTERNAL_PLAN_REVIEW"}
        drawn_as_box = set()
        for _vid, (_x, _y, _w, _h, value, style) in vertices.items():
            if style.startswith("text;") or "dashed=1" in style:
                continue           # notes are not phase boxes
            drawn_as_box |= {p for p in ws.KNOWN_PHASES if p in value}
        self.assertTrue(drawn_as_box)
        self.assertEqual(drawn_as_box - written, set())

    def test_the_unwritten_phases_are_named_only_in_their_own_marked_note(self):
        vertices, _ = _diagram_model()
        unwritten = {"SELF_REVIEWING_PLAN", "AWAITING_TECHNICAL_APPROVAL",
                     "FIXING_FUNCTIONAL_FINDINGS", "AWAITING_USER_ACCEPTANCE"}
        marker_note = next(v for v in vertices.values() if "DECLARED, NEVER WRITTEN" in v[4])
        for phase in unwritten:
            self.assertIn(phase, marker_note[4], phase)
        # The marker note is visually distinct from every ordinary note.
        self.assertIn("dashPattern=2 3", marker_note[5])
        others = [vid for vid, v in vertices.items()
                  if "DECLARED, NEVER WRITTEN" not in v[4]
                  and any(p in v[4] for p in unwritten)]
        self.assertEqual(others, [])

    def test_the_diagram_states_the_real_bundle_generation_outcomes(self):
        """`B3`: `recovered` was drawn as a third
        `record_bundle_generation` outcome. The function takes two, and
        recovery is a separate operation."""
        source = inspect.getsource(ws.record_bundle_generation)
        self.assertIn('outcome not in ("ordinary", "same_content")', source)
        text = _diagram_text()
        self.assertIn("record_bundle_generation takes exactly two", text)
        self.assertIn("Recovery is a different operation, not a third outcome", text)
        self.assertIn("apply_implementation_provenance_recovery", text)

    def test_the_diagram_attributes_both_self_review_writers_correctly(self):
        """`B2`: the note credited the wrap-up writer with the ordinary
        last-checkpoint case, which `complete_checkpoint` owns."""
        text = _diagram_text()
        self.assertIn("SELF_REVIEWING_IMPLEMENTATION has two sanctioned writers", text)
        self.assertIn("complete_checkpoint, when the checkpoint it completes is the last one",
                      text)
        self.assertIn("enter_self_reviewing_implementation", text)

    def test_the_diagram_states_the_real_remediation_child_entry_phase(self):
        """`B1`."""
        text = _diagram_text()
        self.assertIn("AWAITING_LOCAL_PLAN_REVIEW for a “2.1” child", text)
        self.assertIn("AWAITING_EXTERNAL_PLAN_REVIEW for a “1” one", text)
        self.assertIn("never active_work_item_id", text)

    def test_the_diagram_represents_implementation_review_block(self):
        """`B6`: the plan lane showed `BLOCK`, the implementation lane did
        not, although it is supported, durable, and consequential."""
        text = _diagram_text()
        self.assertIn("BLOCK at implementation review", text)
        self.assertIn("record_technical_review_block_pin", text)
        self.assertTrue(hasattr(ws, "record_technical_review_block_pin"))
        self.assertIn("not a phase transition", text)

    #: Diagram tokens that are `WORKFLOW_STATE.json`/config fields or
    #: literal values rather than callables (same rule as the operator
    #: reference's own allowlist).
    NON_SYMBOL_TOKENS = frozenset({
        "work_item_id", "work_items", "base_commit", "bundle_id",
        "review_content_id", "reviewed_implementation_head",
        "implementation_revision", "generation_head", "active_work_item_id",
        "parent_work_item_id", "technical_approval", "plan_approval",
        "governing_workflow_version", "same_content", "plan_revision",
        "registry_path", "state_revision", "default_workflow_version",
        "workflow_acceptance_matrix_test",
    })

    def test_every_identifier_the_diagram_names_actually_exists(self):
        """The same resolution guard the operator reference gets: a
        diagram naming a helper that does not exist is as misleading as a
        document doing it."""
        text = _diagram_text()
        names = set(re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", text))
        names |= set(re.findall(r"\b([A-Z][A-Za-z0-9]*Error)\b", text))
        unresolved = sorted(
            name for name in names
            if name not in self.NON_SYMBOL_TOKENS and name not in ws.KNOWN_PHASES
            and not hasattr(ws, name) and not hasattr(fingerprint, name)
        )
        self.assertEqual(unresolved, [])
        self.assertGreater(len(names), 10)

    def test_every_command_the_diagram_names_exists(self):
        on_disk = {p.stem for p in (_repo_root() / ".claude" / "commands").glob("*.md")}
        named = set(re.findall(r"/([a-z][a-z0-9-]{4,})", _diagram_text()))
        unknown = {c for c in named if c not in on_disk}
        # Only the retired command may be named, and only in the retired banner.
        self.assertEqual(unknown, {"accept-scoped-remediation"})
        self.assertIn("HISTORICAL / RETIRED", _diagram_text())
        self.assertTrue(named & on_disk)


def _approve_review_step_blocks(text: str) -> tuple[list[str], dict[str, str]]:
    """`approve-review.md`'s top-level steps in file order -- numbered and
    lettered alike (`4b.`, `4d.`, `6c1.`) -- as `(labels, {label: block})`.
    A block runs from its own label line to the next label line; indented
    lines (lists, the entry table) never start a block."""
    labels: list[str] = []
    blocks: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = re.match(r"^(\d+[a-z]?\d*)\.\s", line)
        if match:
            if current is not None:
                blocks[current] = "".join(lines)
            current, lines = match.group(1), [line]
            labels.append(current)
        elif current is not None:
            lines.append(line)
    if current is not None:
        blocks[current] = "".join(lines)
    return labels, blocks


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


class TestApproveReviewLifecycleEntryTable(unittest.TestCase):
    """CP6 test 29 (workflow-2.6.0, `D-Repo-Global-Lifecycle`): the entry
    table in the plan's section 5.6 is the command. Asserted row by row
    over the live `approve-review.md`: 4d follows 4c and is named nowhere
    else, 4b still skips 4c-6 after a takeover, 6a1 names the held check
    and the `amend_recovery` mode, 6b captures the tokens before calling
    `rollback_plan_approval_transaction`, and the advance precedes 6d."""

    @classmethod
    def setUpClass(cls):
        cls.text = _command_text("approve-review.md")
        cls.labels, cls.blocks = _approve_review_step_blocks(cls.text)

    def _follows(self, earlier: str, later: str) -> None:
        self.assertEqual(self.labels.index(later), self.labels.index(earlier) + 1,
                         f"{later} must directly follow {earlier}: {self.labels}")

    def test_4d_directly_follows_4c_and_the_reservation_is_named_nowhere_else(self):
        self._follows("4c", "4d")
        self._follows("4d", "5")
        self.assertIn("reserve_amendment_resolution", self.blocks["4d"])
        elsewhere = [label for label, block in self.blocks.items()
                     if label != "4d" and "reserve_amendment_resolution" in block]
        self.assertEqual(elsewhere, [])
        self.assertIn("run step 6b's rollback", _squash(self.blocks["4d"]))

    def test_4b_still_skips_4c_through_6_after_a_takeover(self):
        block = _squash(self.blocks["4b"])
        self.assertIn("skipping straight past steps 4c-6", block)
        self.assertIn("never reaches step 4d or first-commit staging", block)

    def test_step_5_stages_in_first_commit_mode(self):
        block = _squash(self.blocks["5"])
        self.assertIn("stage_plan_approval_members(repo_root, journal, "
                      "mode=workflow_state.PLAN_APPROVAL_STAGING_FIRST_COMMIT)", block)
        self.assertIn("AmendmentResolutionHeldError", block)

    def test_6a1_names_the_held_check_and_the_amend_recovery_mode(self):
        block = _squash(self.blocks["6a"])
        held = block.index("assert_amendment_resolution_held(repo_root, work_item_id, journal)")
        self.assertIn("before the `step-7b-amend-stage` window opens", block)
        self.assertLess(held, block.index("mode=workflow_state.PLAN_APPROVAL_STAGING_AMEND_RECOVERY"))
        self.assertIn("resolution_held=proof", block)
        self.assertNotIn("reserve_amendment_resolution", block)

    def test_6b_captures_the_tokens_before_the_rollback_and_releases_after_it(self):
        block = _squash(self.blocks["6b"])
        captured = block.index("journal_tokens = [journal[\"owner_token\"], "
                               "*journal[\"previous_owner_tokens\"]]")
        rollback = block.index("rollback_plan_approval_transaction")
        release = block.index("release_amendment_resolution(repo_root, work_item_id, journal_tokens)")
        self.assertLess(captured, rollback)
        self.assertLess(block.index("rollback_plan_approval_transaction(repo_root,"), release)

    def test_the_advance_precedes_6d(self):
        self._follows("6c", "6c1")
        self._follows("6c1", "6d")
        self.assertIn("advance_amendment_witness(repo_root, work_item_id, journal=journal, "
                      "commit=<the verified commit>)", _squash(self.blocks["6c1"]))
        self.assertIn("reached only after step 6c1 succeeds", _squash(self.blocks["6d"]))
        elsewhere = [label for label, block in self.blocks.items()
                     if label != "6c1" and "advance_amendment_witness" in block]
        self.assertEqual(elsewhere, [])

    def test_the_entry_table_rows_are_the_plans(self):
        rows = re.findall(r"^\s*\| ([^|]+?) \|", self.blocks["4b"], re.MULTILINE)
        entries = [row for row in rows if row not in ("Entry", "---")]
        self.assertEqual(entries, ["4b", "4c", "4d", "5, 6.x", "6a", "6a1", "6b",
                                   "6c → 6c1 → 6d", "6a `AMBIGUOUS`"])


if __name__ == "__main__":
    unittest.main()
