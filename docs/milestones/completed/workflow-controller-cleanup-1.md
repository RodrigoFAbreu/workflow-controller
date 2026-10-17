# CU1: the first clean-up milestone (`workflow-controller-cleanup-1`)

This is the record of milestone `workflow-controller-cleanup-1` (`docs/ROADMAP.md` step CU1), archived when it was
accepted on 2026-10-17. Plan: `docs/ai-workflow/CONTROLLER_CLEANUP_1_PLAN.md`, revision 3, approved by policy at
`4d29850`; base commit `f60250f`. The sections up to "Known limitations and out of scope" are the active-milestone
page as it stood during the functional review; the later sections were added at acceptance.

## Issues

CU1 fixes sixteen GitHub issues labelled `review-follow-up` or `bug`: #55, #56, #57, #58, #59, #62, #63, #64, #65,
#66, #67, #71, #72, #73, #74 and #75. Its pull request (#77, title `fix: close the sixteen review follow-ups and known
defects of CU1`) closes them when it merges. The checkpoints below name which issues each one fixed.

## Implementation

Plan revision 3 is approved (`4d29850`). Implementation is complete (CP1-CP6) and technical approval is recorded
(`08cffc8`, implementation revision 4).
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

## Acceptance

Accepted **by gate policy, not by a person**, on 2026-10-17: `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`). Every checkpoint (CP1-CP6) is `COMPLETE`, the
technical approval `08cffc8` (implementation revision 4) is `CURRENT`, flows flow-1 to flow-10 passed, and the
Workflow's own GitHub query found pull request #77 at head `5e6b90a` with every check passing and no standing
objection. The pull request title is `fix:`, so it releases the patch release 1.13.1 when it merges. No
`docs/releases/1.13.1.md` was written by the milestone; the release notes come in the docs pull request after the
release.

The lane paused for a PC restart on 2026-10-17, between recording the functional evidence and this acceptance;
nothing was lost.

## Review history

- **Plan.** Revision 3 was approved by policy at `4d29850` after two Codex plan rounds: round 1 (revision 2)
  `REVISE` on the scope of the step-id rule, round 2 (revision 3) `APPROVE`. A local review ran before each.
- **Implementation.** Technical approval `08cffc8` (implementation revision 4) after three Codex implementation
  rounds, inside the round cap of 4. Round 1 (revision 1) `REVISE` with I1-I3: #62 was still flaky, #75 lost a
  review's cost when init validation refused the run, and #57 had no protocol-mode live and `resume` runs. Round 2
  (revision 3) `REVISE`: one worker test still depended on timing, and the cost was still lost when the init event
  was missing. Round 3 (revision 4) `APPROVE`. A local review ran before each Codex round. This was the lane's first
  run on Controller 1.13.0, installed in the shared pipx at the external-review gate on 2026-10-17.
- **Functional.** Round 1, an independent Codex tester with fakes only: flows 1-7 passed; flow 9 raised one
  Optional documentation finding (this page still said CU1 was in progress), fixed by `4faea2a` without making the
  technical approval stale; flows 8 and 10 were blocked by the tester's sandbox (sockets denied); both de-flaked
  worker tests passed 20 of 20 under load. Report:
  `.ai-review/archive/workflow-controller-cleanup-1/functional-tester-round1-RESULTS.md`. Round 2, an operator
  re-test outside the sandbox (`functional-review-round2-PASSED.md`): `tools/workflow_releases.py check` and all four
  golden `--check` runs pass; the whole suite ran 4,183 tests with exact coverage, and only the two known Git 2.56
  trailer tests failed. Checklist evidence commit `5e6b90a`.

## Minor review findings

Every Optional finding of the milestone's reviews, with its disposition (owner rule, 2026-10-16: acceptance never
waits for a fix). Follow-ups are GitHub issues labelled `review-follow-up` and are worked off in clean-up
milestones (CU2 next). Sources are under `.ai-review/archive/workflow-controller-cleanup-1/`.

| # | Source | Finding | Disposition | Justification |
| --- | --- | --- | --- | --- |
| 1 | local plan rev2 O1 | #73 changes the recorded sha256 of `tools_permissions.txt`, and the README repeats the "symlink not exercised" sentence | fixed | 898a7d6: `tests/harness_contract/claude/README.md:22` hash `bbbbbc44...` equals the file's sha256 at HEAD; the symlink facts are now settled (`tools_permissions.txt:31-33`, README:23-24 and 66); the old open-fact sentence is gone |
| 2 | local plan rev2 O2 | `docs/guide/development.md:3` header lists Workflow 2.6.0, 2.7.0 and 2.9.0 only | fixed | `docs/guide/development.md:3` now says "Workflow 2.6.0, 2.7.0, 2.9.0 and 2.12.0" |
| 3 | local plan rev3 O1 | The artifacts declaration says `docs/README.md` is not edited, but CP1 rewords its step ids | dropped | Wording only: the exclusion is still correct (`registry/workflow-controller-cleanup-1-artifacts.json:36` still has the old reason). The file records the approved plan, so we do not change it after approval. CP1 (9bf820c) did reword the ids: `docs/README.md` no longer has C8b/C10 |
| 4 | local impl rev1 O1 | The `is_courier_job` docstring says a courier job has no figures | fixed | baeed89: `controller/telemetry.py:173-176` now says a Claude review keeps its own `review.telemetry`, which `record_block` reads |
| 5 | local impl rev1 O2 | The one-pass rounds test uses `current_round` as its oracle, and `current_round` is built on the same `RoundCounter` | dropped | `PlanCountTest` and `ImplementationCountTest` (`tests/test_review_rounds.py:59,98`) still pin `current_round`'s results to fixed values, and the reviewer confirmed the two are equivalent. An inline copy of the old algorithm would only duplicate code |
| 6 | local impl rev1 O3 | `add_figures` takes its token keys from `figures` only, so a key that only `total` has is dropped | follow-up | Not fixed (`controller/review.py:1662`). Harmless today because both sides have the same keys, but it disagrees with the function's own docstring. [#79](https://github.com/RodrigoFAbreu/workflow-controller/issues/79) |
| 7 | local impl rev3 (1) | `ReviewLaunchRefused` in common-problems says a refusal at the retry's re-check always leaves cost and tokens in `review.telemetry`, which is wrong for Codex | follow-up | Not fixed (`docs/common-problems.md:84`). A Codex `Attempt` carries no `figures` (`controller/review.py:1372`, default `None` at 1317). [#78](https://github.com/RodrigoFAbreu/workflow-controller/issues/78) |
| 8 | local impl rev4 O1 + external impl rev4 O1 (duplicate) | The bundle's `TEST_RESULTS.md` is still headed "implementation revision 3" | dropped | The file is gitignored (`.gitignore:8`) and rebuilt for each milestone. No evidence is missing: `IMPLEMENTATION_SUMMARY.md` records the full-suite result after the fixes, and functional round 2 reran the suite |
| 9 | external plan rev2 O1 | The #55 rule must also name `tests/test_review_round_lifecycle.py` as an exception | fixed | 9bf820c: `docs/guide/development.md:372-373` and `tests/test_workflow_releases.py:4-8` name both modules |
| 10 | external plan rev2 O2 | #75: add up the costs of both Claude attempts instead of taking the maximum, and add a retry test | fixed | `add_figures` sums the attempts (`controller/review.py:1648-1662`, used at 1791 and 1796). The test is `test_the_cost_and_tokens_of_each_attempt_are_summed` (`tests/test_review_claude.py:405`, 2 results, $0.02) |
| 11 | functional round 1, finding 1 | `docs/ACTIVE_MILESTONE.md` still said CU1 was in progress or being planned | fixed | 4faea2a: `docs/ACTIVE_MILESTONE.md:3-6` says implementation is complete and the milestone is in functional review; line 162-163 gives C10 as the next step |
