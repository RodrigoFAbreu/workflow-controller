# Active Milestone

## In progress: CU1 (`workflow-controller-cleanup-1`)

Plan revision 3 is approved (`4d29850`). Implementation is complete (CP1-CP6) and technical approval is recorded
(`08cffc8`, implementation revision 4); the milestone is in functional review.
- **CP1 complete** (#55, #59, #64): the release-tree docstring and the development guide state which tests read the
  installed `scripts/`; `tools/check_docs.py` rejects a roadmap step id on the task pages (`STEP_ID_RE`,
  `STEP_ID_PAGES`; the guides are exempt) and the ids it found were reworded; `docs/guide/runtime.md` explains the
  before-and-after telemetry figures. Checked with `tests.test_docs`, `tests.test_workflow_releases` and
  `tools/check_docs.py`.
- **CP2 complete** (#56, #57, #58, #62, #66; tests only): the leftover guard counts a bare `check_call`/`check_output`/`run`
  imported from `subprocess`, a `timeout=` run whose argv is not written out as a list, and a shell FIFO read
  (`read x < fifo`), with an inventory row per new match; `process_fixtures.end_pid` (SIGCONT, then SIGKILL by pid) replaces
  `kill_group` in the four named cleanups. The unmerged-index usage-limit test gives every stage of each of the seven
  statuses a blob that really changes and runs each live and through `resume` (legacy mode); protocol mode checks the
  shared decision `_tracked_tree_unchanged` directly, because the Workflow blocks a step over a dirty extra path. A lost
  merge reply after a squash onto a moved trunk is tested. The two worker flakes were reproduced under `taskset` to
  2 CPUs with parallel copies (about 1 in 40 and 1 in 20) and no longer fail in 60 pinned runs each. `tests.test_review_rounds`
  pins the ready-phase copy against `evidence`, the installed `scripts/` and the vendored trees. Each changed guard
  was shown failing on its defect by restoring the defect in place (D7).
- **CP3 complete** (#67, #71, #72, #73, #74; review-code fixes): the plan round of a `"1"`-governed item is counted at
  `AWAITING_EXTERNAL_PLAN_REVIEW` (`review_rounds.PLAN_REVIEW_READY_PHASES` unchanged); `job._review_gate` is gone and
  its tests call `_external_review_stage` and `_choose_review`; `claude_argv` refuses a model that is not a full id
  (`CLAUDE_MODEL_ID_RE`, `check="model"`) before any launch; the three `_end_review_group` refusals and the reconciled
  record's message name the review's own harness; the 2026-10-17 symlink and outside-path probe is committed under
  `tests/harness_contract/claude/` (`symlink_probe.txt`, `stream_symlink_probe_haiku.jsonl`, argv reconstructed, no Opus
  repeat) and the two "not settled" lines became settled facts. `docs/guide/runtime.md` and `docs/common-problems.md`
  state the rules. Each new test was shown failing with the controller change reverted (D7). Checked with
  `tests.test_review_rounds`, `test_review_claude`, `test_review_job`, `test_review_same_provider`, `test_telemetry`,
  `test_docs`, `test_write_containment` and `tools/check_docs.py`.
- **CP4 complete** (#63, #75; telemetry): `review_rounds.RoundCounter` is the one count of the Controller's review rounds
  (`current_round` builds one over a list) and `telemetry._round_columns` keeps a running counter per repository and work
  item, so the rounds come from one oldest-first pass with the same results as counting each job's own history. A Claude
  review's `result` event cost and tokens are kept in `review.telemetry` (`review.result_figures`, summed over the
  review's at most two invocations by `review.add_figures`), the review row counts them, and `telemetry --by review_mode`
  shows a same-provider review's cost; a Codex review keeps none. The JSON gains no new row key. `docs/guide/runtime.md`
  and `commands.md` state both. The two new telemetry tests (rounds equal the per-prefix count; time ratio for 4x the
  records) and the figures test were shown failing on the old `telemetry.py` (D7). Checked with `test_telemetry`,
  `test_review_rounds`, `test_review_claude`, `test_review_job` (the touched classes and the whole file), `test_docs`,
  `test_write_containment`, `tools/check_docs.py` and the `review-mode-single-provider` scenario on a committed copy.
- **CP5 complete** (#65): Workflow 2.12.0 is vendored under `tests/workflow_releases/2.12.0/` from the published archive
  (`archive-sha256:0ac3baf76deb...`, as in `.workflow-manager/installation.json`); `tools/workflow_releases.py check` passes
  and every release-iterating test runs against it (the two skips are the Manager-manifest test with no local
  distribution and the old-Git live check). `CapLoop` takes a `release`, and the fixture plans carry a `<!-- CPn -->`
  anchor pair per checkpoint, which 2.12.0 requires and older releases ignore (the only break, and a mechanical one).
  `WorkflowGivenRoundTest` runs the plan round cap on 2.12.0: after a consumed `REVISE` the Workflow's own
  `snapshot.review_round.plan` is what the `review_round_cap_reached` gate names ("given by the Workflow") and what
  `telemetry` reports with source `workflow`. It was shown failing with `stored_round` ignoring the snapshot (D7).
  `docs/guide/development.md` lists 2.12.0 beside 2.7.0 and 2.9.0. Checked with the release, contract, migration,
  lifecycle, round, protocol, `test_docs`, `test_write_containment` and disposable-repository suites and `tools/check_docs.py`.
- **CP6 complete** (documentation and the whole suite): `docs/ROADMAP.md` marks CU1 complete, drops it from "Next, in
  order" and marks follow-ups 31-46 fixed in CU1; the runtime, common-problems and development guides already carry
  the CP1-CP5 behaviour. `tools/check_docs.py`, the golden `--check` forms (2.6.0 plan-stage and implementation-review,
  no-policy lifecycle, protocol-vs-legacy; the bare 2.5.1 plan-stage `--check` keeps its documented known difference)
  and the full serial suite were run; results are in `TEST_RESULTS.md` of the review bundle.

## Functional review checklist

Scope: implementation revision 4 (technical approval recorded at `08cffc8`; the review rounds' fixes for revision 1-3 are
included). CU1 is a clean-up milestone, so most of its checks are automated; the flows below confirm the behaviour a
user can see. Run every flow in a scratch checkout or scratch repository, never in this one, and never against the
real `origin` or `gh` (use a scratch bare `origin` and a fake `gh` on `PATH`).

### Setup

- Run from this checkout with `PYTHONPATH=<this checkout>` (`python3 -m controller ...`, see `docs/guide/development.md`) or
  install the head into a throwaway environment. Leave `FORCE_COLOR` unset.
- Test data: a scratch repository managed by the vendored Workflow (`tests/scenarios/review.py` builds the model world),
  in a plan or implementation external-review phase; a Claude-style fake reviewer that prints a stream with and
  without an init event; `tests/harness_contract/claude/stream_symlink_probe_haiku.jsonl` for the symlink probe.
- Known unrelated local failures: the two Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`)
  and the bare 2.5.1 plan-stage golden `--check`.

### Flows and expected results

1. **Documentation checks.** `python3 tools/check_docs.py` exits 0. Add a roadmap step id (for example `C5`) to
   `docs/run.md` and it is rejected by name; the guides are exempt; undo the edit. `docs/guide/development.md` and the
   release-tree docstring state which tests read the installed `scripts/`; `docs/guide/runtime.md` explains the
   before-and-after telemetry figures.
2. **Full model id.** `claude_argv` (or a `step` that would launch a Claude reviewer) with `reviewer.same_provider_model`
   set to an alias such as `opus` refuses with `check="model"` before any launch and names the full-id rule; a full id
   such as `claude-opus-5-5` launches the fake reviewer.
3. **Review harness named in recovery.** Force a refusal in `_end_review_group` (the three refusals) and the reconciled
   job record's message: each names the review's own harness (Claude or Codex), not a fixed one.
4. **Round counting.** For a `"1"`-governed item the plan round is counted at `AWAITING_EXTERNAL_PLAN_REVIEW`; a
   2.12.0-governed item after a consumed `REVISE` shows the Workflow's own round in `explain` and in `telemetry` with
   source `workflow`; the `review_round_cap_reached` gate says the number is "given by the Workflow".
5. **Claude review figures.** A same-provider review job record keeps the `result` event's cost and tokens (summed over
   at most two invocations), including when the init event is missing and when init validation refuses the run (the
   figures remain on the record, and the docs say the refusal follows a charged review). `telemetry --by review_mode`
   shows that cost for the same-provider row; a Codex review shows none; the JSON rows gain no new key.
6. **Telemetry rounds.** `telemetry` over a history of many records gives the same round columns as before the change
   and does not slow down quadratically (the automated test compares the time for 4x the records).
7. **Leftover guard and usage-limit tests (tests only).** `python3 -m unittest tests.test_write_containment
   tests.test_review_rounds` pass; the unmerged-index usage-limit test runs all seven statuses live and through `resume`;
   restoring one defect in place (for example the old `kill_group` use or the old round count) makes the matching test fail.
8. **Workflow 2.12.0 vendored.** `python3 tools/workflow_releases.py check` passes; `tests/workflow_releases/2.12.0/`
   exists; the release-iterating suites pass with the two stated skips; `docs/guide/development.md` lists 2.12.0.
9. **Roadmap.** `docs/ROADMAP.md` marks CU1 complete, drops it from "Next, in order" and marks follow-ups 31-46 fixed in
   CU1; `docs/ACTIVE_MILESTONE.md` has no stale claim.
10. **Whole suite.** `python3 tools/run_tests.py` (serial, under a reaping subreaper if run in a Controller-launched
    worker) finishes with exact coverage and only the known failures above; run the two flaky worker tests under
    `taskset -c 0,1` with parallel copies and see no failure.

Round 1 (independent tester, 2026-10-17): flows 1-7 pass; flows 8 and 10 were blocked by the tester's sandbox and are
re-run outside it in round 2; flow 9 found one Optional finding (this page still said "In progress ... under way" with
`/milestone-plan` as the next action), corrected here with no source or test change. Re-test flow 9, 8 and 10.

### Known limitations and out of scope

- A real Claude review consumes account usage; the flows use fake reviewers only.
- The two Git 2.56 trailer tests and the bare 2.5.1 plan-stage golden difference are unrelated to CU1.
- Orchestrator, C10, C12 and later steps are not part of this milestone.

The previous milestone's record follows.

## Status

**Complete.** `workflow-controller-review-modes` (`docs/ROADMAP.md` step C14, section 11.14) reached
`MILESTONE_COMPLETE` on 2026-10-17. The owner now chooses how the external review runs: the setting `review.mode`
(`auto`, `cross-model`, `single-provider`, `manual`) or `--review-mode` on `step`, `run` and `explain` for one
command. Under a gate policy that lowers the external review, `auto` runs a fresh same-provider (Claude) reviewer
(`reviewer.same_provider_model`, `reviewer.same_provider_effort`) inside a checked boundary, so a user with one
provider is never blocked at an external gate. A mode that cannot run stops at the new `review_mode_unavailable` gate
(exit 10) with the ways out. `explain` shows the mode and the choice; `telemetry --by review_mode` groups by mode. It
merged as pull request #70 (squash `2804656`), which released the minor release **1.13.0** (release notes
`docs/releases/1.13.0.md`).

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 5 was approved by policy at `4def744` after one Codex plan round (`APPROVE`, the first round). The
  base commit is `40f9cef`.
- Technical approval `18a1036` (implementation revision 3) was recorded by policy and is `CURRENT`: review content
  id `09d09207`, reviewed implementation head `68f7dc3`. Revision 2 was approved at `3cd336b` after two Codex
  implementation rounds (`REVISE` with I1-I4, then `APPROVE`); Codex round 3 approved revision 3 after the bounded
  documentation fix.
- Functional review round 1 (an independent Codex tester with fake reviewers only) passed nine of eleven flows and
  raised two Optional documentation findings (flow 1's command, flow 11's limit description), fixed by
  `d98c388`/`68f7dc3`. Round 2 passed every flow (flow-1 to flow-11), checked against evidence commit `9ba2f70`.
- The Workflow's own GitHub query found pull request #70 at head `9ba2f70` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its eleven functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C14 complete. The full milestone narrative is
archived at `docs/milestones/completed/workflow-controller-review-modes.md`, with the review history (including the
D9 operator pre-step and two later probes) and every minor review finding's disposition; the functional reports are
`.ai-review/archive/workflow-controller-review-modes/functional-tester-round1-RESULTS.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of fifteen Optional review items, ten were fixed, two dropped and three kept as follow-ups; two
  of the fixed ones also keep a follow-up, so there are five, GitHub issues #71-#75 (label `review-follow-up`; #72 also `bug`), worked off in CU1.
- **Known limitations kept out of scope:** the lowered-policy signal in the scenario is patched in (a real lowering
  adoption needs a real Workflow policy repository); a real Claude review consumes account usage. The two Git 2.56
  trailer tests (`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated; the
  `plan_stage_decisions` golden differs on the base commit as well.

## Next action

CU1 is in functional review (the checklist above). After it is accepted, `docs/ROADMAP.md` ("Next, in order") puts
C10 first, then C12, C11 and the orchestrator series O1-O7; `/milestone-plan` for C10 starts that step.
