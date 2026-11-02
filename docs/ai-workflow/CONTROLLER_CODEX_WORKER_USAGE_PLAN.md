# H4d: admit a Codex worker job against the Codex usage budget, account it and hold it on its usage limit, for a fixture role (Revision 2)

Work item: `workflow-controller-codex-worker-usage`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `c1c769822cf85f264f2e4c1e02e1f96bf061e6f5` ("docs: link the H4d issue from the roadmap row and the next action (#266) (#267)"), the tip of `main`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H4d (issue [#266](https://github.com/RodrigoFAbreu/workflow-controller/issues/266)), step 4 of the Codex worker in seven (H4a to H4g). It depends on H4c1 (done).
Released baseline: `workflow-controller 1.27.2`. A path no operator command reaches: the `codex` harness still declares no worker role, no setting selects it and no real launcher is registered, so the squash commit is a `chore:` and there is no release, as for H4a to H4c2.
Pull request title: `chore: admit, account and hold a Codex worker job against the Codex usage budget behind the H3 seam for a fixture role (H4d, #266)`

## Goal

Give a Codex worker job the usage governance a Claude worker job has: the admission before the spawn, the reservation and its accounting, and a hold when the provider's usage limit is reached mid-job, all on the **Codex** provider (`usage.PROVIDER_CODEX`, the `usage.codex_*` thresholds that already exist). The Claude worker path, the tester and the reviewer are unchanged. Nothing real reaches the branch.

## What exists

- `execute_step` (`controller/job.py`) admits a worker with `usage_gate(route)` (the Claude provider) only when the launcher is not Codex's: `if usage_gate is not None and not _is_codex_launcher(launcher)`. A Codex launcher takes no reservation (H4c1, D7).
- A fallback chain already admits each entry through `_chain_admit`, which passes the entry's provider (`route_select.usage_provider`), so a chain that selects a Codex entry under a gate arrives with a Codex token. `_run_codex_worker` then refuses it (`record.get("usage") is not None` raises `WorkerLaunchError`), and `tests/test_job_codex_worker.py` pins both behaviours.
- The CLI gate (`cli._usage_gate`) already serves the Codex provider for the review and the tester: the Codex thresholds, and fresh Codex readings from the operator's home and every retained review home on every attempt. `usage.complete` merges the end readings into the shared record, so a finished job's allowance reaches the next admission.
- The Codex review and tester end their `usage` block with `_codex_end_block(block, readings, first)`; the Claude worker's comes from its stream (`_usage_end_block`). The Codex worker's launcher already returns the job's own home readings in `stream_diagnosis["readings"]`, and `usage_builder` derives the `usage_record` from the rollouts (H4c1).
- A Claude worker that reaches the limit mid-job ends with `stream_diagnosis.usage_limit` (`resets_at`, `window`, `rate_limit_type`). `usage_limit_of`, `_limit_moved_nothing` and `_usage_limit_interrupted` turn that into an `INTERRUPTED` job that changed nothing, and `run` (`_run_steps`) waits for the reset through `cli._mid_job_hold` and repeats the step (a third limited attempt in a row stops the run). `step` never waits (D7 of the usage budget): it exits 17 (`EXIT_USAGE_PAUSED`) with `_interrupted_job_line`, which names the reset. The hold is built with `provider=usage.PROVIDER_CLAUDE`, and only the `usage_paused` event carries `hold.provider`; the three mid-job stop lines (`USAGE_LIMIT_STOP_PREFIX`) name no provider.
- The Codex launcher classifies Codex's own limit record as `SUBTYPE_LIMIT` (a `FAILURE`), with no structured fact; H4c1 pinned that as "a failure with the limit subtype and no `usage_limit` fact".

## Decisions

Each decision states one principle.

- **D1. The Codex worker is admitted on the Codex provider, by the launcher object, before any record exists.** In `execute_step` a Codex launcher calls `usage_gate(route, provider=usage.PROVIDER_CODEX)`; a Claude launcher keeps the unchanged `usage_gate(route)` call, so every existing gate double and test is untouched. A hold returns through `_as_usage_hold` before the job id exists: no record, no reservation, no spawn, the existing exit 17 and `UsageHold` handling. The refusal in `_run_codex_worker` of a record that carries `usage` is deleted: a chain that selects the Codex entry now carries its Codex token, and the same branch accounts it.
- **D2. The reservation is the job's, accounted from the readings of the job's own Codex home.** The shared code binds the token to the job and writes the `usage` start block as for any worker. For a Codex launcher `_launch_job` writes the end block with `_usage_end_fields` over `stream_diagnosis["readings"]` (first readings empty, as the tester's), puts the same two dicts in `usage_end`, and the existing end of `execute_step` charges it, so the block has the Claude worker's shape. The reservation is renewed while the process runs by the same `_usage_ticker`, started in `on_spawn` and stopped in a `finally`. A run that fails after the spawn leaves `accounting: open` and is charged as an `unknown` delta at the forecast; a run that never started releases with no charge; both are the existing rules.
- **D3. A Codex limit reached mid-job is held and repeated, never resumed.** A Claude job that reaches the limit is a terminal `INTERRUPTED` record and a new `step` repeats it; `resume` has nothing to continue, and a Codex record is never re-attached to (H4c2). The Codex job does the same. When Codex's own limit record classified the run (`SUBTYPE_LIMIT`) and the job's readings show exactly the shape of a limit (a window at 100 percent or more with a finite `resets_at`; of several such windows the one with the latest `resets_at` is taken, and on a tie the weekly window), the launcher adds `stream_diagnosis["usage_limit"] = {resets_at, window, rate_limit_type: null}`. The shared `usage_limit_of`, the three-part "changed nothing" test, `run`'s wait loop and `step`'s exit 17 then apply unchanged. When no such window can be read the run stays an ordinary failure, as in H4c1: a reset time is never invented. A run with a breach is `sandbox_breach` and carries no fact.
- **D4. The pause event names the provider that is limited.** `cli._mid_job_hold` takes the provider from the record: `usage.PROVIDER_CLAUDE` when `worker_route.harness` is absent or not a string (a record with no route, as `tests.test_usage_limit_mid_job` passes), else `route_select.usage_provider(harness)`; a harness the registry does not know raises its `UnknownHarnessError` unchanged (a launched record always names a registered one, and the hold never guesses a provider). So the `usage_paused` event, the only place `hold.provider` is written, names Codex for a Codex job. The three mid-job stop lines name no provider today and keep their text, so the Claude lines are byte-identical. Nothing else in the loop changes: no new setting, option, exit code or gate.
- **D5. The `usage` command and the budget pauses show a Codex worker as they show any Codex job.** A running Codex worker is a reservation with `provider: codex`, a finished one a ledger entry of that provider, and a hold is `usage_paused` with the Codex provider and the `usage.codex_*` caps (`usage_hold_message`). No view code changes; tests pin it.
- **D6. Tests use the fake Codex and one opt-in live test.** The offline tests drive `execute_step` with the H4c1 fixture adapter, the `CodexLauncher` subclass and the existing fake `codex`; every new test takes its own job id, so no ownership tag is shared across parallel shards (the open flake #259), and waits on readiness signals, not sleeps. The two H4c1 tests that pin "a supplied gate is not called" and "a chain selecting Codex under a gate is refused" are rewritten to the new behaviour; the Claude, tester and reviewer tests are not edited. One live test (`CONTROLLER_LIVE_CODEX_TESTS=1`, skipped by default and on CI) runs the fixture role under a real gate and checks the admission and the `usage` block.
- **D7. Release type.** `chore:`: no real role, setting, option, exit code or gate reaches the change. If review finds one, the title becomes `feat:` or `fix:` before approval.

## Checkpoints

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Admission and end accounting of a Codex worker job | - | 3 | 1 |
| CP2 | A Codex usage limit reached mid-job | CP1 | 3 | 1 |
| CP3 | The opt-in live proof, the guides and the roadmap | CP2 | 2 | 1 |


<!-- CP1 -->
### CP1. Admission and end accounting of a Codex worker job

**Files:** `controller/job.py`, `tests/test_job_codex_worker.py`, `tests/test_job_codex_worker_usage.py`, `tests/process_fixtures.py`

D1 and D2. The Codex launcher takes `usage_gate(route, provider=usage.PROVIDER_CODEX)` where it took none; the guard in `_run_codex_worker` is removed; the ticker, the end block and `usage_end` are wired in the Codex branches of `_run_codex_worker` and `_launch_job`.

Done when `tests/test_job_codex_worker_usage.py` shows, with a gate double and the fake Codex: a Codex job asks the gate once with provider `codex` and a Claude job still with the route alone; a hold returns a `UsageHold` of provider `codex` with no record, no reservation and no process; an admitted job's record carries a `usage` block bound to its job id whose end figures come from the job's own home and whose `accounting` is `done` with one ledger entry of provider `codex`; the block has the keys of a Claude worker's; a job that never started (a refused tree or build) is released with no charge; a run that fails after the spawn is charged as `unknown`; a chain selecting the Codex entry carries a token and is accounted; the reservation is renewed while the process runs; an orphaned Codex record with a reservation is settled by `resume`; and a second admission after a finished Codex job sees the percent its end readings merged into the shared record. The two rewritten tests in `tests/test_job_codex_worker.py` pass, `tests/process_fixtures.py` has a `LEFTOVER_INVENTORY` row for each new test file that starts a process, and `tests.test_job`, `tests.test_usage_gate` and `tests.test_usage_record_job` pass unedited.
<!-- /CP1 -->

<!-- CP2 -->
### CP2. A Codex usage limit reached mid-job

**Files:** `controller/codex_worker.py`, `controller/codex_worker_job.py`, `controller/cli.py`, `tests/test_codex_worker.py`, `tests/test_codex_worker_job.py`, `tests/test_job_codex_worker.py`, `tests/test_job_codex_worker_limit.py`, `tests/process_fixtures.py`

D3 and D4. `codex_worker_job.limit_fact(readings)` is the pure derivation; `launch_codex_worker` adds the fact to the diagnosis for `SUBTYPE_LIMIT` (and drops it on a breach); `cli._mid_job_hold` derives the provider from the record's routed harness (Claude when none).

Done when the tests show: `limit_fact` returns the window at 100 percent or more with the latest `resets_at` (the weekly one on a tie), and `None` for no such window, a non-finite `resets_at` or empty readings; a limit run through the launcher carries the fact and a limit run with no derivable window carries none (the H4c1 test is updated to say which); a limit run with a breach carries none; a limited job that changed nothing is `INTERRUPTED` with `usage_limit`, accounted, and `resume` has nothing to do with it; a limited job that moved the phase is `FAILED` naming the limit; `run` (through the CLI with a fake clock) waits for the Codex reset, repeats the step, emits a `usage_paused` event naming provider `codex`, and stops at the third limited attempt with the unchanged stop line; `step` for the same job exits 17 with the stop line naming the reset and waits for nothing; and `_mid_job_hold` gives provider `claude` for a record with no `worker_route` or a Claude harness, `codex` for the Codex harness, and raises `UnknownHarnessError` for an unregistered one. The Claude mid-job tests (`tests.test_usage_limit_mid_job`) pass unedited.
<!-- /CP2 -->

<!-- CP3 -->
### CP3. The opt-in live proof, the guides and the roadmap

**Files:** `tests/test_codex_worker_usage_live.py`, `tests/harness_contract/codex/worker_usage.txt`, `tests/process_fixtures.py`, `docs/guide/harnesses.md`, `docs/guide/automation.md`, `docs/guide/workers.md`, `docs/guide/development.md`, `docs/ROADMAP.md`

D5 to D7. The live test runs the fixture role once under a real gate, checks the admission (provider `codex`, a token, a reservation) and the finished `usage` block, and records the measured run; the guides state the admission, the accounting and the mid-job hold in place, and the roadmap marks H4d done (`chore:`, no release) with H4e still needing the owner decision.

Done when the live test is skipped by default and on CI with its reason, its measured run is in `tests/harness_contract/codex/worker_usage.txt`, `docs/guide/development.md` lists the command, `python3 tools/check_docs.py` passes, and the full suite passes.
<!-- /CP3 -->

## Requirements

| id | requirement | checkpoints |
|---|---|---|
| R1 | A Codex worker job is admitted on the Codex provider before any record exists: `usage_gate(route, provider=codex)` for a Codex launcher while a Claude launcher keeps the unchanged call; a hold returns `UsageHold` with no record, reservation or process (exit 17 reused); a fallback chain selecting the Codex entry carries its token (#266) | CP1 |
| R2 | A Codex worker job's reservation is bound to the job, renewed while it runs and accounted from the readings of the job's own Codex home, so the `usage` block has the Claude worker's shape; a never-started job is released with no charge and a run failing after the spawn is charged as unknown (#266) | CP1 |
| R3 | A Codex usage limit reached mid-job is held and repeated, never resumed: the launcher derives `usage_limit` from the job's readings only when Codex's own limit record classified the run, no reset time is invented, a breach carries none, and the existing INTERRUPTED rule and `run`'s wait loop apply unchanged, and `step` exits 17 without waiting (#266) | CP2 |
| R4 | The mid-job hold and its pause event name the provider that is limited (Codex for a Codex job; Claude for a record with no routed harness; the stop lines keep their text) and the `usage` command and budget pauses show a Codex worker as any Codex job, with no new setting, option, exit code or gate (#266) | CP2, CP3 |
| R5 | Nothing real reaches the branch and the Claude worker, the tester and the reviewer are unchanged with their tests: no real launcher key, Codex worker role, setting, option, exit code or gate; one opt-in live test records the admission and the usage block, and the guides and roadmap state the facts in place (#266) | CP1, CP2, CP3 |

## Functional review

Every flow is offline and unit-level except the live flow. Run from the product clone with `TMPDIR` set to a writable scratch directory, one module at a time, `python3 -B` and no `PYTHONPATH=.`: `tests.test_job_codex_worker_usage`, `tests.test_job_codex_worker_limit`, `tests.test_codex_worker`, then `tests.test_job_codex_worker`. The tester's sandbox has a read-only `/tmp` and no user namespaces, so the job and process modules that create folders there are environment-limited and operator-run; the live flow is operator-run. The checklist is written at the functional-review step.

## Risks

- **The worker's own home is not read at the next admission.** A job's Codex home lives under its job folder, not under `reviews/`, so the next admission sees the allowance the worker spent only through `usage.complete`'s merge of the end readings into the shared record (D2). That is the same path a Claude worker's stream takes; a test pins that a second admission after a finished job sees the new percent (listed in CP1's done-when).
- **The limit derivation is proven only against the fake.** `tests/harness_contract/codex/stderr_shapes.txt` records that no real Codex usage-limit event was ever captured and that `rate_limit_reached_type` was null in every capture, so D3's 100-percent reading may never appear against the real binary. The fallback is the safe one (an ordinary failure, no invented reset), and the opt-in test cannot provoke a limit. The fact keeps `rate_limit_type: null`: `usage.read_codex_home` readings do not carry `rate_limit_reached_type`, and widening them is out of scope.
- **A limit with no readable window.** D3 keeps the run an ordinary failure rather than guess a reset; the cost is a failed job where a hold would have been kinder, and only for a worker that reports a limit with no figure.
- **A wrong reset time.** The third limited attempt in a row stops the run (existing rule), so a bad `resets_at` cannot loop.
- **`job.py` is large.** The new logic is one provider argument, the removal of one guard and a few lines in two Codex-only branches; the Claude lines are guarded by `_is_codex_launcher` and the unchanged `tests.test_job` and `tests.test_usage_*` are the proof.
- **The fake Codex can drift from the real one.** The captures under `tests/harness_contract/codex/` feed the fake, and the opt-in test is the only real check.
- **Out of scope:** routing review roles to Codex (H4e), `milestone-implement` on Codex (H4f), the remaining roles (H4g), the worktree preparer and how a Codex worker's commits land (an owner decision before H4e), declaring the worker role on `CODEX`, a real launcher key, any setting, option, exit code or gate, and any change to the Claude worker, the tester or the reviewer.

## Open decisions

None finalised here. Holding a Codex limit through the existing mid-job rule (D3) is this plan's decision for review, not an "Open decision" row of `docs/TECHNICAL_DECISIONS.md`.
