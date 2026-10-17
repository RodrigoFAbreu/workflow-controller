# Clean up the review follow-ups and known defects (Revision 3)

Work item: `workflow-controller-cleanup-1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `f60250f1e54d5fef4b51b6e5e3ff94c8157d7c48` ("docs: 1.13.0 is released, and correct its upgrade advice (#76)"), the tip of `main`, passed explicitly as `/milestone-plan f60250f…`.
Lifecycle authority: this repository's installed Workflow **2.12.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` step **CU1**, first of "Next, in order" (CU1, then C10, C12, C11 and O1-O7).
Released baseline: `workflow-controller 1.13.0`. The changes fix defects and close test and documentation gaps; none adds a setting, command or exit code, so the squash commit is a `fix:` and releases the patch release **1.13.1**.
Pull request title: `fix: close the sixteen review follow-ups and known defects of CU1`

## Goal

Sixteen open GitHub issues are minor review findings (label `review-follow-up`) or small defects (label `bug`): #55-#59, #62-#67 and #71-#75. None blocked an acceptance; together they are debt that grows with every milestone. This milestone fixes all sixteen in one pull request, whose body closes each with `Closes #n`. After it `review-follow-up` and `bug` hold no issue that existed at its start, so the next clean-up (CU2) starts from the findings of later milestones only.

Nothing is dropped and nothing is deferred without a reason; the two items with a real choice are decided below (D2, D3).

## Decisions

Each decision states one principle.

- **D1. One checkpoint per kind of change, so a reviewer reads one kind at a time.** Documents and the docs check (CP1), tests only (CP2), review-code fixes (CP3), telemetry (CP4), the vendored Workflow 2.12.0 and the test that needs it (CP5), and the closing documentation and roadmap (CP6). The issues are independent of each other, so the order is by risk: the cheapest and safest first.
- **D2. The model the Claude reviewer is asked for is the model it will report, so a name that cannot match is refused before launch (#72).** The measured fact (2026-10-17, one real `claude -p` run): `--model haiku` makes the `init` event report `claude-haiku-5-5`. `check_claude_init` compares exactly and stays exact, because a looser rule would let a review run on a model other than the one the owner chose. `claude_argv` (the only constructor of the command line, called before the process starts) therefore accepts only a full model id, a name that begins `claude-` and holds only letters, digits, `.`, `_` and `-` (a full id as `init` reports it; a family-style or `-latest`-style name that has this shape but resolves to another id still runs and is refused at `init`, a residual case the runtime guide states); any other name, an alias such as `opus`, is refused with `ReviewLaunchRefused` (`check="model"`) naming the setting and the shape, before any usage is spent. `docs/guide/runtime.md` says the row takes a full model id. No setting is added and the default (`claude-opus-5-5`) is already one. If the id of a future Claude Code model fits no such shape, the rule is the one line to change, and a test pins it.
- **D3. A `"1"`-governed plan is counted at its own review phase, not by a rule that happens to give 1 (#67).** The plan round fallback counts revisions seen at a plan-review ready phase. For an item whose stored `pre_state.governing_workflow_version` is `"1"` the ready phase is `AWAITING_EXTERNAL_PLAN_REVIEW`, so that phase is counted for such an observation and no other. This is a rule in the counting function keyed by the observation's version; `review_rounds.PLAN_REVIEW_READY_PHASES` is not changed, because D4 pins it equal to the Workflow's set. A test checks both together: a `"1"` observation at `AWAITING_EXTERNAL_PLAN_REVIEW` is counted and the pin still passes. Items of any other version, and items with the Workflow's own `review_round`, are unchanged. The docs state the rule rather than the limitation.
- **D4. A test that compares two copies of one list compares them to the Workflow's own, not to each other (#66).** The `review_rounds` copy of the plan-review ready phases (the module imports no other Controller module) must equal `evidence.PLAN_REVIEW_READY_PHASES`, the installed Workflow's `workflow_state.PLAN_REVIEW_READY_PHASES` and that constant in every vendored tree whose `workflow_state.py` defines it (2.6.0, 2.7.0, 2.9.0; the loop is over `tests/workflow_releases/`, so the 2.12.0 tree CP5 adds is covered without a change here).
- **D4a. The guide states the rule the suite follows: Workflow-derived checks read vendored trees, with one named exception (review of round 1, I1).** The review-round tests in `tests/test_review_rounds.py` put this repository's installed `scripts/` on `sys.path` (the existing lower-bound test, and D4's pin), because they compare the Controller's copy to the Workflow's current behaviour and the lower-bound test needs `workflow_protocol._plan_review_round`, which no vendored tree before 2.12.0 has (2.7.0 and 2.9.0 carry `workflow_protocol.py` without it). Moving them to a vendored tree is not possible before CP5 and is not needed after it: the installed Workflow is exactly what the Controller will meet, and `InstalledReleaseTest` already pins that it is an admitted release. So the guide says: every other Workflow-derived check reads `tests/workflow_releases/<release>/`; `InstalledReleaseTest` reads the installation record; the review-round tests are the stated exception and read the installed `scripts/`. CP1 writes this sentence, CP2 adds the pin without changing it.
- **D5. Telemetry keeps what a Claude review reports, and says what a Codex review does not (#75).** The Claude `result` event's `total_cost_usd` and token totals are stored in the review job record in the form `_worker_dict` stores a worker's, and `telemetry` counts them, so `--by review_mode` compares what a same-provider review costs. A Codex review keeps no figures until its rollout gives them, and the docs say so. The telemetry round columns are computed in one oldest-first pass with running observations per (repository, work item), giving the same rounds as today (#63).
- **D6. A vendored real answer replaces a hand-made one only where the hand-made one cannot catch the defect (#65).** Workflow 2.12.0 is vendored with `tools/workflow_releases.py sync` from the published archive (provenance `archive-sha256:0ac3baf76deb…` as recorded in `.workflow-manager/installation.json`), and one reconciliation test runs the round cap at `plan.apply_review` after a consumed `REVISE` against a real protocol 1.4 answer and checks telemetry reports the same round. The existing hand-made snapshot tests stay.
- **D7. Every test fix must still fail on the defect it guards.** For the five test issues (#56, #57, #58, #62, #66) the milestone shows, by restoring the defect in a scratch copy, that the new or changed test fails without the fix. #62 is reproduced under CPU pinning (`taskset` to 2 CPUs with parallel copies, as for the earlier CI flakes) before the fix and run the same way after.
- **D8. A rule in the docs check is only as wide as its readers (review of round 2, I1).** The step-id check (#59) applies to the user pages outside `docs/guide/` only: the task pages, `README.md` and `docs/README.md`. `tools/check_docs.py` keeps the guides in `USER_PAGES` for the header and the existing `INTERNAL_ID_RES` checks, which stay as they are, so the step-id pattern is **not** added to `INTERNAL_ID_RES` (that would be applied to every page in `USER_PAGES`, guides included, and would reject `docs/guide/development.md`'s "(step C10)"). It is a separate `STEP_ID_RE` with its own page list, `STEP_ID_PAGES`, derived from `USER_PAGES` by dropping the entries under `docs/guide/`. The guides are for developers, who know the roadmap's ids, so `development.md`'s "(step C10)" stays, and the plan states that boundary so the check does not grow into the guides.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Stale and loose documents, and the step-id check: the installed-release wording, the user-page step id with a scoped check_docs rule, and the telemetry before-and-after steps | - | 1 | 1 |
| CP2 | Tests only: the test-leftover guard forms and exact-pid cleanup, all seven unmerged statuses live and through resume, the lost merge reply on a moved trunk, the two worker flakes, and the ready-phase pin | CP1 | 3 | 1 |
| CP3 | Review-code fixes: the round count of a "1"-governed plan, the unused _review_gate, a full model id for the Claude reviewer, recovery messages by harness, and the committed containment probe | CP2 | 3 | 1 |
| CP4 | Telemetry: review rounds in one pass, and the cost and tokens of a Claude review kept and counted | CP3 | 3 | 1 |
| CP5 | Vendor Workflow 2.12.0 and test the review round cap against its real protocol answer | CP4 | 3 | 1 |
| CP6 | Documentation, roadmap and the whole suite: CU1 complete, the guides agree, check_docs, golden checks and the full suite green | CP5 | 1 | 1 |

<!-- CP1 -->
### CP1 -- Stale and loose documents, and the step-id check (#55, #59, #64)

- #55: rewrite the module docstring of `tests/test_workflow_releases.py` (lines 4-8) to describe the installation-record check, and the two sentences of `docs/guide/development.md` (the "Workflow release trees" section) to state D4a's rule: the Workflow-derived checks read vendored trees, `InstalledReleaseTest` reads the installation, and the review-round tests in `tests/test_review_rounds.py` and `tests/test_review_round_lifecycle.py` (which also imports the installed `workflow_protocol` and calls `_plan_review_round`) are the named exception that read the installed `scripts/` (the docstring's "never this repository's own installed Workflow" and the guide's "never read" are the two sentences that are false today).
- #59: drop "(C9c)" from `docs/compatibility.md`. Add `STEP_ID_RE` and `STEP_ID_PAGES` (the user pages outside `docs/guide/`, D8) to `tools/check_docs.py`, with a check in `check_pages` that reports a step id on those pages; `INTERNAL_ID_RES` and the guide checks are unchanged. Tests in `tests/test_docs.py`: the same step-id text in `docs/compatibility.md` fails and in `docs/guide/development.md` passes, and the existing guide header and internal-id checks still fire on a guide (a `CP1` or a work-item id in a guide still fails). Any existing hit on a covered page is reworded; the check's own run is the list.
- #64: two sentences in `docs/guide/runtime.md` (the before-and-after telemetry steps): the "before" figures are the first query minus the second, and a review the Controller runs itself always shows `round=N~`.

Done when `python3 tools/check_docs.py` and the docs tests pass, with `docs/guide/development.md`'s "(step C10)" still in place.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- Tests only (#56, #57, #58, #62, #66)

- #56: `tests/process_fixtures.py` `_start_form` also counts `check_call`/`check_output`/`run` imported by name, classifies a `timeout=` run whose argv is not a literal instead of skipping it, and matches a shell FIFO read; the four `kill_group` cleanups named in the issue (`tests/test_resume.py`, `tests/test_cli.py`, `tests/test_fake_codex_contract.py`) become exact-pid cleanup (SIGCONT then SIGKILL by pid). The guard's own tests add one case per new form.
- #57: the unmerged-index usage-limit test gives each case a stage whose blob really changes, covers all seven unmerged statuses (DD, AU, UD, UA, DU, AA, UU), and runs each through `resume` as well as live, in legacy and protocol modes.
- #58: one test in `tests/test_pull_request_lifecycle.py` lands a trunk commit before the send, then sets `merge_reply_lost=True, merge_read_lag=2`, and asserts one send, `squash_commit` equal to the trunk tip, and a successful close-out.
- #62: the two worker tests wait on a readiness signal or on the worker's own state instead of a sampled field or a 0.3 s window (reproduce first, D7).
- #66: the pin of D4 in `tests/test_review_rounds.py`, reading the installed `scripts/` and the vendored trees as D4a states.

Done when each new or changed test is shown to fail without its fix (D7) and the full suite passes.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- Review-code fixes (#67, #71, #72, #73, #74)

- #67: D3 in `controller/review_rounds.py` with tests for a `"1"` item at rounds 1, 2 and 3 and an unchanged `"2.2"` item; one sentence each in `docs/guide/runtime.md` and `docs/common-problems.md`.
- #71: remove `_review_gate` from `controller/job.py`; `tests/test_review_job.py` calls `_external_review_stage`, and the "no reviewer" case moves to `_choose_review`.
- #72: D2 in `controller/review.py` `claude_argv`, with tests for an alias, a spaced name, a leading dash and a full id; `docs/guide/runtime.md` says the row takes a full model id.
- #74: the three `_end_review_group` refusals and the reconciled record's message use `_review_display(record)`; `docs/common-problems.md` (`ReviewLaunchRefused`) lists the Claude causes (argv, init, home, managed layers); a test recovers a Claude review and checks the text does not say "Codex".
- #73: commit the 2026-10-17 symlink and outside-path probe (prompt, stream, link layout). The probe directory `~/.cache/c14-prestep/symlink-probe/` holds only `prompt.txt` and `run1.jsonl`; the three-symlink layout is in `~/.cache/c14-symprobe/copy`, and the argv was never saved, so it is reconstructed from `claude_argv` with the model the stream's `init` reports (`claude-haiku-4-5-20251001`, `dontAsk`, tools Glob, Grep and Read) and recorded as reconstructed (the issue's optional Opus repeat is not run) under `tests/harness_contract/claude/`, move the two lines in `README.md` and `tools_permissions.txt` from "not settled" to the settled facts, and list the file in whatever inventory the contract tests keep.

Done when the tests above pass and `controller/` holds no remaining caller of `_review_gate`.
<!-- /CP3 -->

<!-- CP4 -->
### CP4 -- Telemetry (#63, #75)

- #63: D5's one-pass computation in `controller/telemetry.py` (`_round_columns`), pinned by the existing telemetry tests plus one that compares the one-pass rounds with the old result over a synthetic record set and bounds the time over a large one by a generous ratio, not a clock.
- #75: the Claude review job record keeps the result event's cost and token totals; `telemetry` counts them in the review rows and `--by review_mode`; a Codex review is unchanged. `launch_review` may start two Claude invocations, so the cost and the tokens are added across the attempts (not the maximum that `worker_stream.session_telemetry` takes within one session), with a regression test of a two-attempt review whose recorded cost and tokens are the sums. `docs/guide/runtime.md` loses the sentence that a review keeps no figures, for Claude, and keeps it for Codex.

Done when `telemetry --by review_mode` shows a cost for a same-provider review in the lifecycle scenario and the existing JSON keys are unchanged (new keys only).
<!-- /CP4 -->

<!-- CP5 -->
### CP5 -- Vendor Workflow 2.12.0 and test the round cap against it (#65)

- Vendor 2.12.0 under `tests/workflow_releases/2.12.0/` (D6); `python3 tools/workflow_releases.py check` passes; every test that iterates the vendored releases runs against it or is shown to skip it for a stated reason.
- One reconciliation test: the cap gate's round and source at `plan.apply_review` after a consumed `REVISE`, from `snapshot.review_round`, and the telemetry round equal to it.
- `docs/guide/development.md` lists 2.12.0 beside 2.7.0 and 2.9.0 as a checked protocol release.

Done when the new test passes against the vendored tree and fails when the snapshot's `review_round` is removed. If the vendored tree breaks release-iterating tests in ways that are not mechanical, this checkpoint stops and the plan is amended: #65 then stays an issue and the milestone closes the other fifteen.
<!-- /CP5 -->

<!-- CP6 -->
### CP6 -- Documentation, roadmap and the whole suite

`docs/ROADMAP.md` marks CU1 complete and moves the sixteen follow-up entries (items 31-46 of the known follow-ups) to done; CU2's rule stays. The runtime, common-problems and development guides agree with CP1-CP5. The `docs/ACTIVE_MILESTONE.md` narrative and the issue-closing PR body are written at acceptance, not here. `python3 tools/check_docs.py`, the golden `--check` forms and the full suite are green.
<!-- /CP6 -->

## Requirements

| id | requirement | checkpoints |
|---|---|---|
| R1 | The stale installed-release wording is corrected so the guide and docstring state D4a's rule truthfully, the user-page step id is removed and `check_docs` rejects a step id on a user page outside `docs/guide/` but not in a guide, with the existing guide checks still active, and the runtime guide's before-and-after telemetry steps and the `~` round are exact (#55, #59, #64) | CP1 |
| R2 | The test-leftover guard counts imported `check_call`/`check_output`/`run`, non-literal `timeout=` runs and FIFO reads, with exact-pid cleanup in the four named tests; the unmerged-index test covers all seven statuses live and through `resume`; a lost merge reply after a squash onto a moved trunk is tested; the two worker tests no longer flake under CPU pinning; the ready-phase copies are pinned (#56, #57, #58, #62, #66) | CP2 |
| R3 | A `"1"`-governed plan is counted at `AWAITING_EXTERNAL_PLAN_REVIEW`; `_review_gate` is gone; a non-full Claude model id is refused before launch and documented; recovery messages name the review's own harness; the symlink and outside-path probe is a committed capture (#67, #71, #72, #73, #74) | CP3 |
| R4 | Telemetry works out review rounds in one pass with the same results, and a Claude review's cost and tokens are kept, summed across attempts and counted (#63, #75) | CP4 |
| R5 | Workflow 2.12.0 is vendored and one test runs the round cap against its real protocol answer (#65) | CP5 |
| R6 | The roadmap and guides agree with the behaviour; `check_docs`, the golden `--check` forms and the full suite are green, and the pull request body closes all sixteen issues | CP6 |

## Risks

- **Vendoring 2.12.0 may break inventories (CP5).** Tests that iterate the vendored releases were written for four trees. CP5 measures first; a non-mechanical break stops the checkpoint and amends the plan rather than loosening a test.
- **A full-id rule may be too narrow (D2).** Claude Code could one day report an id outside `claude-[A-Za-z0-9._-]+`. The failure is a refusal before launch with a message naming the shape, never a paid review, and the rule is one pinned line.
- **CP2 changes tests that guard process hygiene.** A mistake there could hide a leftover. D7 requires each changed guard to be shown failing on the defect it guards.
- **Size.** Sixteen issues is the largest clean-up possible; each is small and independent, and the checkpoints are by kind. If CP2 or CP3 grows past one session it is split by amending the plan, not by dropping an issue.
- **No `docs/TECHNICAL_DECISIONS.md` "Open decision" row is touched** (the file does not exist in this repository).
- **Artifact declaration.** The generator's default template classifies none of `controller/`, `tests/`, `tools/` or the `docs/*.md` pages (known follow-up 10). The declaration is the previous milestone's reviewed classification with this item's ids and wording; every path the checkpoints name is classified.
