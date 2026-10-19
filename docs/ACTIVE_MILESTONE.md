# Active Milestone

## Status

**Complete.** `workflow-controller-cleanup-2` (CU2, `docs/ROADMAP.md`) reached `MILESTONE_COMPLETE` on 2026-10-18. It
fixes the twelve follow-up issues #78, #79, #82-#87, #91 and #93-#95 in one pull request (#99, squash `6b3c54b`,
merged 2026-10-18), released as the minor release **1.16.0**, the latest release. `/satisfy-gate` is now routed by
stage: Haiku 5.5 `high` for plan and technical approval, Sonnet 5.5 `medium` for acceptance (#91). The release notes
are `docs/releases/1.16.0.md`.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 8 was approved by policy at `b4f3a07` after eight local and four Codex plan rounds. The base commit is
  `4f1e127`.
- Technical approval `260b07d` (implementation revision 2) was recorded by policy and is `CURRENT`, after two local and
  two Codex implementation rounds.
- Functional review round 1 (an independent Codex tester, an operator re-test and a real-model run of flow 10) passed
  all ten flows. Checklist evidence commit `a3a7da7`.
- The Workflow's own GitHub query found pull request #99 at head `a3a7da7` with every check passing and no standing
  objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step CU2 complete. The full
narrative is archived at `docs/milestones/completed/workflow-controller-cleanup-2.md`; the Optional findings are in
`docs/milestones/completed/workflow-controller-cleanup-2-findings.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of eleven Optional review items, eight were fixed, two dropped and one kept as a follow-up,
  GitHub issue #100 (label `review-follow-up`), for a later clean-up milestone.
- **#88** (the two review round caps) waits for a Workflow release that counts local and external rounds separately
  (workflow#43).

## Next action

`workflow-controller-adapters-1` (A1, `docs/ROADMAP.md`) is `IMPLEMENTING` (plan revision 5, base `859eda5`). Checkpoints
CP1 (the harness adapter registry, `controller/harness.py`), CP2 (the git host adapter registry, `controller/forge.py`
`FORGES`), CP3 (the usage record, `controller/usage_record.py`, with the versioned `controller/prices.json`, which
ships with no model prices until one is taken from a published price list), CP4 (the record written beside `telemetry`
on both worker completion paths and by every review job's terminal write in which an attempt ran, `usage_record_failed`
on a failed build) and CP5 (the guides and the roadmap; the no-policy golden gains only the `usage_record` key; the git
host adapter builds `GhForge` at call time, so a patched `forge.GhForge` still reaches `Context.forge`) are complete.
Every registry checkpoint is `COMPLETE`; the next `/milestone-implement` invocation enters self-review.
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth.

## Functional review checklist

Findings go to `.ai-review/workflow-controller-adapters-1/feedback/FUNCTIONAL_REVIEW.md`. This milestone is a refactor
plus one additive record: nothing the Controller decides, prints or launches may change. The only output change is the new
`usage_record` key in `resume --json` (plan D6).

### Setup

- Branch `milestone/workflow-controller-adapters-1`, clean tree. Run the tests from the checkout; do not set
  `PYTHONPATH=.` (it breaks the decoy test).
- Test data: none to seed. All flows use the unit suite and the checkout.

### Flows

1. **Harness adapter registry (R1).** `python3 -m unittest tests.test_harness`. Expected: pass. `claude-code` and `claude`
   resolve to one adapter; asking `codex` for the worker role raises `HarnessRoleError` naming the harness, the role and the
   declared roles; an unknown name raises `UnknownHarnessError` with the registered names; the derived tables
   (`review_mode.HARNESS_FAMILY`, `telemetry.HARNESS`) equal the old literals.
2. **Git host registry (R2).** `python3 -m unittest tests.test_forge_adapter tests.test_forge`. Expected: pass;
   `set(FORGES) == repo_policy.FORGE_KINDS`; an unregistered `forge_kind` raises `ForgeKindUnregisteredError`; a
   `forge_factory` still wins.
3. **Usage record and converters (R3).** `python3 -m unittest tests.test_usage_record`. Expected: pass. Claude figures equal
   the telemetry block's for every captured stream; a Codex rollout gives `derived` figures; nothing is invented for a
   stream with no result; `controller/prices.json` ships with no model prices, so a Codex cost is `null`/`unknown`.
4. **Record written at job end (R4).** `python3 -m unittest tests.test_usage_record_job`. Expected: pass. A worker record
   carries `usage_record` exactly when it carries the `telemetry` block (this includes a job reconciled FAILED after the
   worker's result was read, e.g. exit 7, and a worker that launched but exited without emitting a result; a job that fails
   outside the completion path, such as a failure to launch, has neither), and every review end in
   which an attempt ran carries it; a review that ended before any attempt carries none; an injected converter failure leaves the job identical to a control run except for
   `usage_record_failed`.
5. **Nothing else changed (D1).** `git diff 859eda5 HEAD --stat -- tests/` lists new test files and only registration edits
   to existing ones (`test_package_structure.py` and the small fixture additions); read each edited existing test and
   confirm no expectation changed. Run `python3 -m unittest tests.test_review tests.test_review_claude tests.test_review_mode
   tests.test_telemetry tests.test_worker_stream tests.test_fake_codex_contract tests.test_no_rewrite_invariants`.
   Expected: pass.
6. **`resume --json` (D6).** In `tests/test_usage_record_job.py`, find the `resume --json` cases. Expected: a terminal record
   with `usage_record` / `usage_record_failed` prints the key; one without it prints the pre-milestone object; exit codes
   identical.
7. **Package data.** Build or inspect the wheel layout (`python3 -m unittest tests.test_package_structure`). Expected:
   `controller/prices.json` is in the package data and loads from an installed layout; the new modules sit at the positions
   in D1a.
8. **Documents (R5).** `python3 tools/check_docs.py` passes. Read the adapter and usage-record sections of
   `docs/guide/workers.md`, `docs/guide/how-it-works.md` and `docs/guide/development.md` (how to add a harness and a git
   host; price-table maintenance). Expected: each matches the code; `docs/ROADMAP.md` does **not** yet mark A1 complete
   (that happens at acceptance).
9. **Whole suite and goldens.** Run the full suite and each `tests/golden/generate_*.py --check` (never without `--check`).
   Expected: green except the two known Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`);
   the no-policy golden differs from base only by the `usage_record` key.

### Known limitations

- No model prices are in the table yet, so a derived cost is `null`; a Claude cost is the harness-reported one.
- No consumer reads the usage record (A3 and the router do later); `telemetry`, `inspect`, `status` and `follow` still read
  the telemetry block.
- Codex as a worker, a second git host and routing are out of scope (A5, later adapters).
- The merge of the `refactor:` title releases 1.16.1 after acceptance; release and notes are post-merge steps.

### Functional review round 1

- Finding 1 (documentation and test, bounded): the rule for a FAILED worker is now stated in the guides and in flow 4; a test covers a non-zero exit after a result. Re-test flow 4 and flow 8.
