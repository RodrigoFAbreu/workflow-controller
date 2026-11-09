# H4e2b: route the two local review roles to Codex (Revision 2)

Work item: `workflow-controller-h4e2b`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `8ae70961dcb8fa358102fb76b3033aad5658defb` ("ci: measure parallel test workers inside a shard and adopt them only on the measurements (CI1c, #317) (#325)"), the tip of `main`, passed explicitly as `/milestone-plan 8ae70961dcb8fa358102fb76b3033aad5658defb`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-h4e2b` (`docs/backlog.yaml` order 80; split from H4e2 on 2026-11-06, [#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315); parent tracking issue [#312](https://github.com/RodrigoFAbreu/workflow-controller/issues/312)). Its description is `docs/items/workflow-controller-h4e2b.md`. It depends on H4e2 (done, PR #319).
Released baseline: `workflow-controller 1.27.10`. After this item an operator can name `codex` as the harness of `review-plan` and `review-implementation`, so the squash commit is a `feat:` and releases a minor version. The default is unchanged: no role is routed to Codex unless the operator's routing says so.
Pull request title: `feat: route the review-plan and review-implementation roles to Codex, read-only in a private clone, with safety tests, a live proof and a cost table (H4e2b, #315)`

## Goal

One mechanism: **routing**. H4e2 built the recorder (`controller/review_recorder.py`) and the read-only run (`controller/codex_review_role.py`, `run_review` and `recover`) and left both dark. This item lets the operator's routing select them for the two roles `review-plan` and `review-implementation`, wires the job record around them, proves the routed path adversarially, adds an opt-in live proof and a cost table, and updates the pages. It adds no new way to record a verdict, no new sandbox rule and no new setting: it uses the existing `harness` field of a role's route.

## What exists

- Routing names a harness per role (`routing.ResolvedRoute.harness`). A pair is accepted when the harness adapter declares the adapter role the routing role runs as (`routing.ADAPTER_ROLE`, `routing.check_role_harness`, `controller/routing.py:303-319`). Both local review roles run as `worker` today, and the `codex` adapter declares only `reviewer` and `functional-tester` (`controller/harness.py:79`), so `codex` for these roles is refused at configuration time.
- `worker_launch.for_harness(name)` returns the launcher of a harness that declares `worker`; `LAUNCHERS` holds only the Claude launcher (`controller/worker_launch.py`). `codex_worker.CodexLauncher` exists and is deliberately not registered.
- `job.execute_step` resolves the route and the launcher (a role with a fallback chain goes through `route_select.select`, probing each entry with `job._launcher_error`), admits the usage gate (`usage_gate(route, provider=usage.PROVIDER_CODEX)` for a Codex launcher), writes the job record in two flushes, launches, then judges the outcome: a protocol job through `_protocol_resolution`, the Workflow's own `reconcile`. A Codex worker branches in `_run_codex_worker` and lands commits (H4e).
- H4e2's `run_review(managed_repo, *, runtime_root, job_id, operator_home, stage, work_item_id, base, model, effort, state_identity, timeout, brief, on_progress, launch, launch_options, clock)` returns an `Ending(code, cause, worker, evidence)` and removes its clone on every ending; `recover(runtime_root, job_id, block, path)` ends a record holding the `codex_review` block on `resume`, `abandon` and drift. Its endings are `CodexReviewNotStarted`, `CodexReviewWorkerFailed`, `CodexReviewRefused`, `ReviewStateIdentityChanged`, `workflow_release_changed`, `CodexReviewRecordRefused`, `CodexReviewRecordPending` and `CodexReviewRecorded`, plus `CodexReviewNotRecorded`, `CodexReviewRecordInterrupted` and `OperatorAbandoned` from `recover`.
- The tables of `docs/guide/harnesses.md` are generated from the registry by `tools/harness_table.py` (`--write`, `--check`), and `tests/test_harness_table.py` pins them.
- `controller/prices.json` prices `gpt-6.1-sol` and the Claude models; the job record's `usage_record` is derived from the Codex rollouts (H4c1, H4d).

## Decisions

Each decision states one principle.

<!-- CP1 -->
- **D1. A role is routable to a harness when the harness declares the adapter role the role runs as, and the two review roles get their own adapter role, a launcher entry of their own and a place in the independence check.** `harness` gains the adapter role `local-reviewer`. `routing.ADAPTER_ROLE` maps exactly `review-plan` and `review-implementation` to it; every other routing role keeps its adapter role. Both adapters declare it.
  - *The registry.* `worker_launch.for_harness(name, role=harness.WORKER)` resolves a launcher for the given adapter role; the registry is keyed by `(adapter name, adapter role)` and holds one entry per adapter declaring a role. `(claude-code, local-reviewer)` is the registered Claude launcher, as today. `(codex, local-reviewer)` is a new `worker_launch.CodexReviewLauncher`, a subclass of `codex_worker.CodexLauncher` whose `launch` raises (it never spawns: the job's review branch of D3 runs `run_review`, so nothing launches a review through it). It exists so the route is admitted like any Codex route, and it is not registered under `(codex, worker)`, so a worker role still cannot be routed to `codex`. The test that pins the registry to the declarations covers both roles; a harness without the role, or declaring it with no entry, refuses as `HarnessRoleError` as now.
  - *Admission on the Codex provider, on both paths.* On the single-route path `_is_codex_launcher(launcher)` is an `isinstance(..., codex_worker.CodexLauncher)` test (`controller/job.py:8676`) and is true for `CodexReviewLauncher`, so `usage_gate(route, provider=usage.PROVIDER_CODEX)` is asked; on the chain path `route_select.select` chooses the provider by the entry's harness name, `codex`, and gives the same answer. Both are tested. The review branch of D3 is taken before the Codex worker's branch (`_run_codex_worker`) and is keyed on the role and the harness, never on the launcher's type alone, so a review route never reaches the worker branch; a test pins that order.
  - *The chain probe.* `job._launcher_error` takes the entry's adapter role and skips an entry that is unregistered or undeclared for it, like any other unlaunchable entry. It does not check the Codex build: a build the review refuses is found in `run_review`, ends `CodexReviewNotStarted` (D3's table) and does not fall through to the next chain entry. The plan claims no more than that.
  - *The independence check.* `review_mode.worker_families` counts the roles whose adapter role is `worker` **or** `local-reviewer` (`controller/review_mode.py:111-120`, changed in CP1), so the two review roles stay among the roles whose families the single-provider check compares, exactly as they were when they ran as `worker`. `mixed_lane_reason` and `LOCAL_REVIEW_ROLE` need no change: the local review role is still among the counted roles. With `review-plan` on `codex`, every other role on `claude-code` and `reviewer.harness` `codex`, the families are `{anthropic, openai}`, `distinct_harness` is not given a single provider, and the mixed-lane stop fires and names the collision, as it does before this item; a test through `review_mode.choose` and `mixed_lane_reason` pins that configuration (codex local, codex external) for both stages, and the existing `worker_families` tests pass unchanged.
- **D2. Routing is opt-in and changes nothing by default.** With no routing config, no setting and no `--role-harness`, both roles resolve to `claude-code` and every Claude job record is byte-identical to today's (the existing job tests pass unchanged). Selecting `codex` takes a model and an effort the adapter lists (`harness.CODEX`: `gpt-6.1-sol`, `high` or `xhigh`); an unset or Claude-only value is refused by the existing `check_route_values`, with the message it gives today, and a test pins that refusal for these two roles. This item adds no setting, no flag, no exit code and no gate.
<!-- /CP1 -->

<!-- CP2 -->
- **D3. A routed review runs `run_review` in place of the launch, inside the job's own record, and the job status is a function of the ending.** After the route and the usage admission (the Codex provider, as H4d does for a Codex launcher), `execute_step` takes a branch when the route's role is one of the two and its harness is `codex`. The branch needs a protocol decision (`decision.protocol`, whose `state_identity` is the one `run_review` takes; the stage follows from the role: `plan` for `review-plan`, `implementation` for `review-implementation`); a decision without one is `WorkerNotStarted` with its own reason. The base is the pre-step target head; the brief is the `reviewer.brief` setting, read as the external review reads it, so the Codex reviewer sees the same added instructions on either stage. The record gets the `codex_review` block through `run_review`'s `on_progress` (`preparing`, `recording`, `recorded`), persisted with the record's usual write; the branch writes `worker_outcome`, `usage` and `usage_record` from the Codex rollouts when a turn ran. The mapping, one row per ending of `run_review`, each with one test (D4 gives the rows of the recovery endings):

  | ending | job status and what the record says |
  | --- | --- |
  | `CodexReviewRecorded` | worker outcome success; the Workflow's `reconcile` judges the phase as for any protocol job (`FINISHED` when the phase moved as the row expects) |
  | `CodexReviewRecordPending` | as `CodexReviewRecorded` (the state moved to `APPLYING_REVIEW_FEEDBACK`); the evidence names `recorder_commit_pending` and says the next role commits the entry |
  | `CodexReviewNotStarted` | `FAILED` with `WorkerNotStarted` and the ending's cause; no `codex` was started and nothing is left to resume |
  | `CodexReviewWorkerFailed` | worker outcome failure with the launcher's subtype; `usage_limit` keeps its fact so the existing wait path (`usage_limit_of`, `INTERRUPTED` when nothing moved) applies; `sandbox_breach` is `FAILED` |
  | `CodexReviewRefused` | `FAILED`, the deny-set cause in the evidence, nothing recorded, and no field of the record holds any line of an armoured block |
  | `ReviewStateIdentityChanged` | `FAILED`, nothing recorded; the next run decides again |
  | `workflow_release_changed` | `FAILED`, nothing recorded; the record carries the reason `WORKFLOW_RELEASE_CHANGED_REASON` |
  | `CodexReviewRecordRefused` | `FAILED`, the Workflow's class as cause, the Workflow's state named as the truth where the runner raised; the composed verdict that `run_review` already wrote to `reviews/<job id>-verdict.md` under the runtime root (`controller/codex_review_role.py:508`) is the one kept: the branch does not write a second copy, it sets that file's mode to 0600 and names its path and SHA-256 in the evidence, so the operator can record it by hand when the phase guard still allows it; the file is removed with the job's other runtime leftovers, as `reviews/` is |

  The managed repository is never written by the branch itself: the only writes there are the recorder's (D1 of H4e2). No landing runs, and `codex_worker_landing` is not imported by the new code.
- **D4. Every recovery site of a record holding the `codex_review` block calls `recover`, every ending of `recover` has a job status, and nothing records again.** The block, not the adapter's name, identifies the record (`job._is_codex_review_record`, true for a record carrying a `codex_review` mapping, kept apart from `_is_codex_worker_record`). `resume` of a `LAUNCHED` record, `abandon`, a drifted Workflow release (`_resume_drifted_protocol`) and the post-spawn exception handler each call `codex_review_role.recover`, and leave no clone. The paths are `resume`, `abandon` and `drift` (`RECOVERY_PATHS`, unchanged): the exception handler passes `resume`, because a handler that runs after a spawn is a record that stopped part-way, which is what `resume` judges by the block's stage; `recover` is not changed for it. A record whose block has an unknown stage ends as `CodexReviewRecoveryError` names it. A review record never reaches `_reconcile_codex_worker`. The rows of the recovery endings:

  | ending (stage of the block) | job status and what the record says |
  | --- | --- |
  | `CodexReviewNotRecorded` (`preparing`) | `INTERRUPTED`: the worker may have run and nothing was recorded; the evidence carries `recover`'s message and the next decision reviews again |
  | `CodexReviewRecordInterrupted` (`recording`) | the Workflow's state is the truth: the job takes `reconcile`'s judgement of the phase (`FINISHED` when the phase moved as the role expects, `INTERRUPTED` when it did not), with the recorder's own evidence not re-run and nothing recorded again |
  | `CodexReviewRecorded`, `CodexReviewRecordPending`, `ReviewStateIdentityChanged`, `workflow_release_changed`, `CodexReviewRecordRefused` (`recorded`) | as D3's row of the same ending, taken from the block's `result` |
  | `OperatorAbandoned` (`abandon`) | the job's own abandoned outcome (`OPERATOR_ABANDONED_CODE`), as `abandon` writes it for any record; no clone is left |
  | `workflow_release_changed` (`drift`) | `FAILED` with `WORKFLOW_RELEASE_CHANGED_REASON`, as D3's row |
<!-- /CP2 -->

<!-- CP3 -->
- **D5. The routed path is proved adversarially through the job, and every refusal has a test.** The tests drive `execute_step` with the real `run_review`, the real recorder on fixture repositories and the fake `codex` of `tests/fake_codex.py` (as `tests/test_job_codex_worker.py` does), and for each case assert one thing: the managed repository's tree digest, refs, index and `WORKFLOW_STATE.json` are unchanged (except for the recorded case, where only the feedback file and the state change), the clone is gone, the job record has the status D3 gives and holds no armoured line. The cases:
  - credentials: an input named like a credential, and an armour line in the bundle, are refused before any `codex` starts (`credential_input`); an armour line in the final message, and one in `stderr` of a failed worker, are `credential_in_verdict` with the four fields emptied;
  - a symlink or a special file among the inputs (`special_file`), a `.git` component or a gitlink (`submodule`), a path that escapes (`path_escape`) and an input over a bound (`input_over_bound`);
  - hooks: a managed repository with a `hook.*` configuration key is refused by H4e's own check (`managed_hooks_configured`, `codex_worker_tree.check_managed`), before any `codex` starts; a managed `core.hooksPath` is not refused but neutralised: the clone is made with `-c core.hooksPath=/dev/null` and has it set locally (`codex_worker_tree.py:172,184`), and the test asserts the clone's `core.hooksPath` is `/dev/null` and that a managed hook script did not run; and a `codex` that writes `.git/hooks/post-merge` in the clone ends `sandbox_breach`;
  - a crafted ref: a `codex` that creates a branch or tag, moves `HEAD` or edits an input in the clone ends `read_only_violated`, and no ref reaches the managed repository;
  - a forged header or a second `Status:` line in the final message is `verdict_unparsed`, and a `review-plan` run never changes the external stage's ledger;
  - the remaining causes of the deny set (`verdict_over_bound`, `verdict_unparsed`) and each ending of D3's table.
  A test fails when a cause in `codex_review_role.CAUSES` has no case here, so a cause added later cannot go untested.
<!-- /CP3 -->

<!-- CP4 -->
- **D6. The live proof is opt-in, operator-run and spends one Codex turn per stage.** `tests/test_codex_review_live.py` runs a real `codex exec` review of the plan stage and of the implementation stage through `execute_step` on a scratch repository with a bound bundle, under `CONTROLLER_LIVE_CODEX_TESTS=1`, skipped when `CI` is set (as `tests/test_codex_worker_job_live.py` is). It asserts the verdict is recorded, the managed tree outside the feedback file and the state is unchanged, the clone is gone and the job record's `usage_record` is present. Its measured result goes in `tests/harness_contract/codex/review_role.txt`, recorded by the operator's run, not produced in CI and not required for the squash.
- **D7. The cost table states what a review loop costs on each harness, from measured tokens and the price table, never from an estimate.** `docs/guide/harnesses.md` gets a table of one local review round per stage: the input, cached-input and output tokens the live proof measured for Codex, the cost they give at `controller/prices.json`'s `gpt-6.1-sol` rates, and the cost of the same token counts at the Claude reviewer's rates, both labelled as API list-price equivalents (as the price file says). Beside it, the number of rounds a stage can take (the local cap of six, or the setting) multiplies the per-round figure to the worst case of a loop, and the weekly-hold arithmetic is given as a formula over the Codex provider's own reading (`usage.PROVIDER_CODEX`, held by the existing `weekly_pause_at_percent`), with the measured percent of one job where the live proof had a reading, and `not measured` with the reason where it had none. A figure the proof did not measure is never filled in.
- **D8. The pages say what is now selectable.** `docs/guide/harnesses.md`'s tables are regenerated (`python3 tools/harness_table.py --write`): both adapters declare `local-reviewer`, and the `codex` column is `yes` for the two roles. Its H4e2 section drops "routed to no role", gains the routing, the status table of D3 and the cost table; `docs/guide/workers.md`'s Codex paragraph links it, and `docs/items/workflow-controller-h4e2b.md` records the decisions and the live-proof status. `python3 tools/check_docs.py` passes.
<!-- /CP4 -->

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | `review-plan` and `review-implementation` can be routed to `codex` through the existing `harness` field, behind their own adapter role, with the default and every Claude record unchanged (#315) | CP1 |
| R2 | A routed review runs read-only through `run_review` inside the job's record, with one job status per ending and one test each, usage admitted on the Codex provider (#315) | CP2 |
| R3 | Every recovery site of a `codex_review` record calls `recover`, records nothing again and leaves no clone (#315) | CP2 |
| R4 | The routed path is tested adversarially through the job: credentials, symlink, submodule, hook, crafted ref, forged header, and a test for every cause of the deny set and every refusal (#315) | CP3 |
| R5 | An opt-in operator live proof of both stages exists and records its measured result (#315) | CP4 |
| R6 | The cost table and the pages state what is selectable, what a review loop costs and how it meets the weekly hold, from measured figures only (#315) | CP4 |

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The adapter role and routing of the two review roles | - | 3 | 1 |
| CP2 | The job branch, the status of each ending and the recovery sites | CP1 | 4 | 1 |
| CP3 | The adversarial tests of the routed path | CP2 | 3 | 1 |
| CP4 | The live proof, the cost table and the pages | CP3 | 2 | 1 |

<!-- CP1 -->
### CP1 -- The adapter role and routing of the two review roles

**Files:** `controller/harness.py`, `controller/routing.py`, `controller/worker_launch.py`, `controller/review_mode.py`, `controller/job.py` (the probe's role argument only), `tests/test_harness.py`, `tests/test_routing.py`, `tests/test_routing_chain.py`, `tests/test_worker_launch.py`, `tests/test_review_mode.py`, `tests/test_review_independence.py`, `tests/test_harness_table.py`, `tests/test_package_structure.py`

D1 and D2.

Done when:
- `harness.LOCAL_REVIEWER` exists and both adapters declare it; `routing.ADAPTER_ROLE` maps exactly the two review roles to it and the test that holds the mapping equal to the routing roles still passes. `--role-harness review-plan=codex` and a routing config naming `codex` for either role are accepted (with a listed model and effort), and `codex` for any other worker role is refused exactly as before.
- The launcher registry is keyed by `(adapter, role)` with one entry per adapter declaring each role (`CodexReviewLauncher` for `(codex, local-reviewer)`, whose `launch` raises), a test pins it, `for_harness` refuses a harness without the role, and `codex` is still refused for the `worker` role. With no routing, both roles resolve to `claude-code` and the routed record is the same as before (the existing routing, chain and job tests pass unchanged).
- A Claude-only model or no effort for a `codex` route of these roles is refused by `check_route_values`, with a test.
- A fallback chain over a review role can name `codex`; an entry that is unregistered or undeclared for the role is skipped by the probe and the next entry is chosen (the probe checks no build).
- `review_mode.worker_families` counts the `worker` and `local-reviewer` roles; a test through `review_mode.choose` and `mixed_lane_reason` covers `review-plan` and `review-implementation` on `codex` with `reviewer.harness` `codex` (the mixed-lane stop fires and names the collision) and the existing `worker_families` tests pass unchanged.
- The harness-table test passes against the regenerated tables.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The job branch, the status of each ending and the recovery sites

**Files:** `controller/job.py`, `controller/worker_launch.py`, `controller/codex_review_role.py`, `tests/test_job_codex_review.py`, `tests/test_job_codex_worker.py`, `tests/test_job_codex_worker_resume.py`, `tests/test_job_codex_worker_usage.py`, `tests/test_package_structure.py`

D3 and D4.

Done when, with the fake `codex` and the real recorder on fixture repositories:
- A routed `review-plan` at `AWAITING_LOCAL_PLAN_REVIEW` and a routed `review-implementation` at `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` end `FINISHED` with the verdict, the state and the feedback written, the `codex_review` block in the record, a `usage_record` derived from the rollouts, no clone left and no landing run. A decision with no protocol block is `WorkerNotStarted` with its own reason and nothing spawned.
- One test per row of D3's table, each asserting the status, the record's evidence, the managed repository's state and the clone's absence; the `usage_limit` case takes the existing wait path, and the `CodexReviewRecordRefused` case keeps the one verdict file at `reviews/<job id>-verdict.md` with mode 0600 and the digest in the evidence, with no second copy.
- The usage gate is asked with the Codex provider for a routed review on the single-route path and on the chain path, and unchanged for a Claude route; the review branch is taken before the Codex worker's; a hold leaves no record.
- One test per recovery site (`resume`, `abandon`, drift, a post-spawn exception) for each stage block (`preparing`, `recording`, `recorded`), and a job-status test for each of `CodexReviewNotRecorded`, `CodexReviewRecordInterrupted` and `OperatorAbandoned`: `recover` ends the record as D4's table says, removes the clone, records nothing (the recorder is a spy) and names no clone; a review record never reaches the worker reconciler.
- The existing Codex-worker job modules (`tests.test_job_codex_worker*`) pass unchanged.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The adversarial tests of the routed path

**Files:** `tests/test_codex_review_safety.py`, `tests/fake_codex.py`, `tests/test_job_codex_review.py`, `tests/test_package_structure.py`

D5.

Done when every case D5 lists has a test through `execute_step`, each asserting the managed repository unchanged (digest, refs, index, state), the clone gone and the record's status; the armour tests build the marker by concatenation, assert both directions (prose naming the markers is recorded, a real block is refused) and assert that no field of the job record or the returned result holds a line of the block; a test fails when a member of `codex_review_role.CAUSES` has no case; and the Claude-routed job tests are unchanged.
<!-- /CP3 -->

<!-- CP4 -->
### CP4 -- The live proof, the cost table and the pages

**Files:** `tests/test_codex_review_live.py`, `tests/harness_contract/codex/review_role.txt`, `docs/guide/harnesses.md`, `docs/guide/workers.md`, `docs/guide/development.md`, `docs/items/workflow-controller-h4e2b.md`

D6, D7 and D8.

Done when:
- `tests/test_codex_review_live.py` collects, and skips with `CONTROLLER_LIVE_CODEX_TESTS` unset or `CI` set, offline; its flow and the command that runs it are on `docs/guide/development.md`. The measured result file exists with the operator's result when the operator has run it, and otherwise the item page says it was not run and why; the squash does not wait for it.
- The cost table of D7 is on `docs/guide/harnesses.md`, every figure from the measurements or `not measured` with the reason, with the formula and the price file's date.
- `python3 tools/harness_table.py --check` and `python3 tools/check_docs.py` pass; the pages say which roles can be routed to `codex`, the status of each ending and that the default is unchanged; the full suite is green but for the known environmental `fake_codex_contract` tests.
<!-- /CP4 -->

## Functional review

Offline flows, run from the product clone with `TMPDIR` set to a writable scratch directory, one module at a time, with `python3 -B` and without `PYTHONPATH=.`: `tests.test_harness`, `tests.test_routing`, `tests.test_routing_chain`, `tests.test_worker_launch`, `tests.test_harness_table`, `tests.test_job_codex_review`, `tests.test_codex_review_safety`, `tests.test_codex_review_role`, `tests.test_review_recorder`, then `tests.test_job_codex_worker*` (unchanged), `tests.test_package_structure`; and `python3 -B tools/harness_table.py --check` and `python3 -B tools/check_docs.py`. The live flow (`tests.test_codex_review_live`) is operator-run and spends Codex usage; it is not part of the offline flows.

## Risks

- **A Codex reviewer's verdict is a dishonest-verdict risk the in-place reviewer has too.** The role cannot write state, and the external stage still decides; the safety tests prove the role cannot reach the ledger, a ref or a credential, not that its judgement is right.
- **Local and external reviews on one provider.** When both stages run on Codex, the existing independence check (the mixed-lane stop of `review_mode`) is what judges it; this item adds no new warning. D1 keeps the two review roles in `worker_families`, so the stop answers as before for a Codex local stage, and a CP1 test pins the codex-local / codex-external configuration. A warning would be a second mechanism and its own item.
- **The cost table is only as good as one live run.** It reports the measured tokens of the proof, labelled as one sample; a loop's real cost varies with the bundle's size.
- **A Workflow release that changes a writer.** The recorder's interface probe (H4e2) fails closed before any spend; a routed job then ends `FAILED` with `CodexReviewNotStarted` and `recorder_interface_changed`, with a test in CP2.
- **Out of scope:** `apply-plan-review` and `apply-implementation-review` on Codex (H4e3), `milestone-implement` (H4f), a default harness for these roles, the options UI and the Manager's use.

## Open decisions

This repository has no `docs/TECHNICAL_DECISIONS.md`, so no open decision row there is touched. Three choices are the plan's own and are stated so the reviewers can overrule them: the adapter role is a new name (`local-reviewer`) rather than a second meaning of `reviewer`, which is the external verdict's role; the item is a `feat:` and releases, because an operator can now select the route; and no review-mode warning is added. One follow-up stays the owner's call and nothing here waits on it: whether the live proof's result should gate the release or only be recorded.
