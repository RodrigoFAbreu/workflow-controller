# Cap local and external review rounds separately (Revision 3)

Work item: `workflow-controller-round-caps`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `d6b9dfcff3ac96686cb9d67feb44377f8b0bf7cd` ("chore: move this repository to Workflow 2.15.0 (#106)"), the tip of `main`, passed explicitly as `/milestone-plan d6b9dfc…`.
Lifecycle authority: this repository's installed Workflow 2.15.0, automatic gates.
Roadmap slot: `docs/ROADMAP.md` step **C13b** (controller issue #88, follow-up 55).
Released baseline: `workflow-controller 1.16.1`. The change adds two settings and two options, so the squash commit is a `feat:`, which the committed policy maps to a **minor** release: merging it releases `workflow-controller 1.17.0` (`tools/release.py check-title` answers `ok: feat → minor`).
Pull request title: `feat: cap local and external review rounds separately`

## Goal

Today one cap, `review.round_cap` (default 4), is compared with the Workflow's single per-stage review round. Local and external rounds both count, so a plan that needs three local rounds and one external round stops at the cap and the operator raises it by hand (C10a did). The owner decided on 2026-10-18 to cap the two kinds on their own: **local 6, external 4**, named "local" and "external", never after a provider, because any harness can run either review.

The Workflow now counts them separately: protocol 1.5 (Workflow 2.15.0) adds `plan_local`, `plan_external`, `implementation_local` and `implementation_external` to the `next-action` snapshot's `review_round` object. This milestone reads them.

Out of scope: any change to `telemetry` (its round column keeps the single per-stage number), to the stop's exit code (10), its reason code or its one owner notification, and to the Workflow.

## Decisions

Each decision states one principle.

- **D1. Split counts when the Workflow gives both, today's rule when it does not.** For a stage whose two per-role counts are both integers in the snapshot, the cap rule uses them. Otherwise (an older Workflow, an item created before the counts, a promoted or feedback-only item, a malformed key) the rule is exactly today's: the single number (the Workflow's, else the Controller's own job count) against the **external** cap. The fallback never mixes the two sources, and the gate text says which one applied.
- **D2. A cap stops the action that would exceed it.** In split mode: `<stage>.apply_review` stops when the local count has reached the local cap or the external count has reached the external cap; `<stage>.review.local` stops when the local count has reached the local cap; `<stage>.review.external` stops when the external count has reached the external cap. The external review is an `external_gate` decision, so `_review_cap_gate` (which `_decide_protocol_current` reaches only for `automatic` decisions, and `explain` applies only under `decision.automatic`) never sees it. Its cap is checked where the step has already chosen to run a review: in `execute_step`, after `_choose_review` returns `choice.kind == "review"` and before `_run_review_job`, and in `explain` at the same point (where `review_mode_report` reports `choice` `review`). A count at the external cap there answers one `review_round_cap_reached` `GATE_BLOCKED` record (the same gate, text and owner notification as the apply stop; no review job, no usage reservation). When no reviewer would run anyway (`review.mode` `manual`, or `reviewer.harness` unset, so `choice.kind` is `gate`) nothing is checked and the ordinary external gate stands, because the cap limits what the Controller launches and a person may still record a manual review. The counts are recorded verdicts, so a count equal to its cap means the next review would be one more than allowed. In the fallback the capped actions and the comparisons are today's, unchanged (apply at the cap or above, local review above it, no external review).
- **D3. Two optional settings, one deprecated one.** `review.local_round_cap` and `review.external_round_cap` are optional integers from 1 to 100, unset by default. Unset means the built-in 6 and 4. `review.round_cap` keeps its row, default and bounds and works for one more release (its settings-table row and the `--review-round-cap` help text say "deprecated: use the local and external caps; retired after 1.17.0", and CP4 adds a ROADMAP follow-up that records the retirement): when it is set away from 4 (in the file) or given as `--review-round-cap`, it is the value of every cap that is still unset. A file value of exactly 4 is the default and cannot be told apart from it, so it does not override. A cap given by its own setting or option always wins over it. The options are `--review-local-round-cap N` and `--review-external-round-cap N` on `run` and `step`; `--review-round-cap` stays and sets both, below an option of its own.
- **D4. The table generation goes up.** Adding two rows raises the settings table generation from 11 to 12 and fills a file only with the two unset rows. An older Controller sharing the file warns about two unknown keys and ignores them, as for every earlier addition.
- **D5. The stop names the cap.** The gate text keeps its three ways on and names the stage, which cap fired (local or external), the count and the cap, and where the counts came from ("given by the Workflow as split counts", or, when either per-role field is `null`, "the Workflow gave no split counts" followed by the single number used; the Controller cannot tell an older Workflow, an item created before the counts, a promoted item and a malformed counter apart, so the text names no reason). `explain` prints the same gate. The stop stays one `GATE_BLOCKED` record and one owner notification, as today.
- **D6. Docs say what the code does.** The settings table, the run guide, the common-problems page, the commands guide, the compatibility page and the settings generation notes change in the same milestone; a plan or a completed-milestone narrative is not rewritten.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The two settings and the options | - | 2 | 1 |
| CP2 | The cap rule over split counts | CP1 | 3 | 1 |
| CP3 | The gate reads the rule | CP2 | 3 | 1 |
| CP4 | Documentation, roadmap and the whole suite | CP3 | 2 | 1 |

<!-- CP1 -->
### CP1 -- The two settings and the options

**Files:** `controller/settings.py`, `controller/cli.py`, `tests/test_settings.py`, `tests/test_cli.py`, `tests/test_trunk_preflight.py`

Add the two optional-integer rows (D3) with `TABLE_GENERATION` 12 and its comment, and the options `--review-local-round-cap` and `--review-external-round-cap` on `run` and `step` with the same 1 to 100 parser as `--review-round-cap`. `_settings_cli_overrides` passes both. A pure function in `controller/review_rounds.py` (CP2) turns the effective values and sources into the two caps; CP1 only makes the values reachable. Tests: the rows' bounds, the fill adding exactly the two rows at generation 12 and moving nothing else (`tests/test_trunk_preflight.py` pops the `review.round_cap` row from the recorded settings block and now sees two more rows), `settings show` printing both, the deprecation wording on the old row and option, the options' bounds and precedence over the file.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The cap rule over split counts

**Files:** `controller/review_rounds.py`, `tests/test_review_rounds.py`

`review_rounds.py` gets the split read of a snapshot (D1: both per-role integers, else none), `effective_caps` (D3: own setting, else a deprecated cap set away from 4 or given on the command line, else 6 and 4) and a cap decision for the split counts (D2) that returns which cap fired. The fallback functions are untouched, so their tests do not change. The external-review action joins the capped actions in split mode only, as a separate `EXTERNAL_REVIEW_ACTIONS` set that `_review_cap_gate` does not consult (it is checked per D2 in CP3). Tests: every row of D2 at, below and above each cap, one count null, the fallback equals today's decision for each action, and `effective_caps` for each way a cap can be given.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The gate reads the rule

**Files:** `controller/job.py`, `controller/cli.py`, `tests/test_protocol_job.py`, `tests/test_evidence.py`, `tests/test_protocol_decision.py`, `tests/test_review_round_lifecycle.py`, `tests/scenarios/lifecycle.py`, `tests/golden/generate_no_policy_lifecycle.py`, `tests/golden/no_policy_lifecycle.json`

`_review_cap_gate` takes the two caps and the effective-cap sources instead of one integer, calls the CP2 rule, and words the stop per D5; the call in `cli.py` and the `run`/`step` paths pass them (`tests/test_evidence.py` lists `execute_step`'s keyword names including `review_round_cap`, and `tests/test_protocol_decision.py` tests `explain` with the settings cap; both are edited for the new names). The external cap (D2) is checked in `execute_step` after `_choose_review` returns a `review` choice and before `_run_review_job`, through one shared helper that `explain` also calls where `review_mode_report` reports `review`; it builds the same `review_round_cap_reached` gate through `protocol_decision.gate_for`. Tests: a plan at external count 4 with a configured reviewer stops with the cap gate and launches no review job (no job record, no usage reservation, no harness call), and `explain` shows the same gate; the same count with `review.mode` `manual` or no `reviewer.harness` keeps the ordinary external gate; external 3 launches the review; an item with no split counts launches the external review as today; a plan at local 6 and external 1 stops an apply with a text naming the local cap; local 2 and external 4 stops with the external cap; local 5 and external 3 does not stop; an item with no split counts stops as before and the text says "the Workflow gave no split counts" and names the single number used. The split rule is tested end to end only at the job level: `tests/test_protocol_job.py` injects a `next-action` snapshot carrying the four per-role fields, because the lifecycle tests run a real vendored Workflow (`fixtures.seed_workflow_item(root, release, ...)` reads only `tests/workflow_releases/<release>/`, and the newest vendored tree, 2.12.0 with protocol 1.4, emits no `plan_local`/`plan_external`/`implementation_*` fields). `tests/test_review_round_lifecycle.py` and `tests/scenarios/lifecycle.py` therefore stay on the fallback, with the assertions on the stop text updated to the new wording; vendoring 2.15.0 (its tree, `tools/workflow_releases.py`, the per-release inventories, goldens and migration tests, and the `docs/guide/development.md` paragraph on which release each lifecycle test runs on) is out of this milestone and recorded as a ROADMAP follow-up in CP4 so the split case reaches the lifecycle tests when a later milestone vendors it; the no-policy golden is regenerated and checked with its `--check` mode.
<!-- /CP3 -->

<!-- CP4 -->
### CP4 -- Documentation, roadmap and the whole suite

**Files:** `docs/run.md`, `docs/common-problems.md`, `docs/exit-codes.md`, `docs/compatibility.md`, `docs/guide/runtime.md`, `docs/guide/commands.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

Update the settings table and the round-cap paragraph in the runtime guide (D3, D4, the generation 12 note), the run guide's round cap section, the common-problems entry (the gate text now names the cap), the commands guide's option text, the compatibility note (split counts need protocol 1.5, otherwise the fallback) and the exit-codes cross-reference. Mark C13b complete in the roadmap and follow-up 55 fixed, and add two follow-ups: one retires `review.round_cap` and `--review-round-cap` after 1.17.0, one vendors Workflow 2.15.0 under `tests/workflow_releases/` so the split-count case runs in the lifecycle tests. Run `tools/check_docs.py`, the golden `--check` modes and the whole suite.
<!-- /CP4 -->

## Requirements

- **R1.** Two settings and two options set the local and the external cap; unset they are 6 and 4; the deprecated `review.round_cap` and `--review-round-cap` still set both when given (D3, D4). Covered by CP1, CP2.
- **R2.** With both per-role counts given, the capped actions stop per D2 and the stop says which cap fired, the stage, the count and the cap (D2, D5). Covered by CP2, CP3.
- **R3.** Without split counts the behaviour and the decisions are today's, with the single number against the external cap, and the text says so (D1). Covered by CP2, CP3.
- **R4.** The documentation, the roadmap and the settings generation notes describe what the code does, and the whole suite and the docs check pass (D6). Covered by CP4.

## Risks

- **A deprecated cap of exactly 4 is not seen as set.** The file cannot tell it from the default, so a repository that set it to 4 gets 6 local; documented in D3 and in the runtime guide. The command-line option has no such gap.
- **Counts are recorded verdicts.** A `BLOCK`, a person's approval and a gate-policy satisfaction record no round, so the local count can be lower than the number of local review jobs; the gate text names the counts as the Workflow's own.
- **Mixed null.** One count given and the other null is treated as no split counts, not as zero (D1).
