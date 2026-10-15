# Active Milestone

## Status

**Awaiting functional review (implementation revision 7, technically approved).** `workflow-controller-codex-reviewer` (`docs/ROADMAP.md` step C7) runs the cross-model review
with a Codex reviewer the Controller starts itself, opt-in through `reviewer.harness`. Plan revision 15 was
approved by policy (commit `3d304d3`); the base commit is `dea2125`. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_REVIEWER_PLAN.md`.

| Checkpoint | State |
| --- | --- |
| CP1 settings rows, `record_external_result`, `rm -f` note | committed (`f880306`) |
| CP2 Codex harness | committed (`f9a14f2`); the D12 pre-step was recorded 2026-10-15 and the harness is registered (implementation review round 2 fixes) |
| CP3 review job | committed (`078879e`) |
| CP4 lifecycle scenarios | committed (`1bd2639`) |
| CP5 documentation and full suite | committed |

CP1 changes:
- `controller/settings.py`: the five `reviewer.*` rows (`harness`, `model`, `effort`, `timeout_seconds`,
  `binary`), `TABLE_GENERATION` 6. Nothing reads them yet; no `cli_flag` is wired (a flag with no reader would be
  dead, so CP3 adds any it needs).
- `controller/protocol.py`: `record_external_result` and `ExternalResult`, validated against the vendored schema;
  `not_applicable` and `refused` are returned, every other refusal raises.
- `controller/worker.py` and `tests/harness_contract/capture.py`: the `command rm -f` / `command cp -f` sentence
  in `WORKER_LIFECYCLE_NOTE` (follow-up 24). The recorded `*.meta.json` captures are historical recordings of
  runs made before the sentence and are left as recorded.
- `tests/golden/no_policy_lifecycle.json` regenerated; `tests/test_trunk_preflight.py` removes the reviewer rows
  and the added sentence and still matches the 1.5.0 base hash (R2).

**CP1 acceptance condition (D2) met:** the Workflow side answered *yes, with conditions* on 2026-10-15
(workflow#28, `https://github.com/RodrigoFAbreu/workflow/issues/28`). The answer and its five conditions are
recorded verbatim in ADR 0013 decision 2 with the code that meets each (the checkpoint record in
`WORKFLOW_STATE.json` holds only `status` and `start_commit`, so this section is the CP1 evidence). The pending
follow-up is the protocol paragraph in W8 / Workflow 2.12.0; the issue stays open until then.

CP2 changes:
- `controller/review.py`: the review-only seam. `ReviewHarness` and the closed registry `HARNESSES`; the exact
  verdict header check (`expected_headers`, `verify_verdict`, one label table, the stage revision included); the
  disposable copy (`build_copy`: no `.git`, its own one-commit repository, project-local Codex discovery removed,
  removed on every path by `settle_job_dir`, which keeps only `sessions/`); the real-repository guard
  (`snapshot_repository`, `compare_snapshots`); the enforced boundary (`argv` as the only command-line
  constructor and `check_argv`, `child_environment` as an exact allowlist, `build_home`/`check_home` for the
  Controller-written minimal Codex home and its startup-load check, `check_outside_layers`, `check_features`,
  `check_mcp_list`, `check_effective`); the credential copy and its locked, digest-guarded `copy_back_credentials`;
  `run_bounded` (own session, deadline, group kill); and `launch_review`, which runs it all, re-checking the
  boundary before each of the two invocations. Nothing calls it.
- `controller/runtime.py`: `copy_tree`, `remove_tree`, `write_private_bytes`, `replace_external_file` (the
  write-containment scan exempts this module); `controller/errors.py`: `ReviewLaunchRefusedError`.
- `tests/fake_codex.py` (scenario-file driven, because the child environment is an allowlist; composes real TOML
  layers, merges `-c mcp_servers={}` instead of replacing, runs only what the composed configuration names, orphan
  watchdog with a ready file), `tests/test_fake_codex_contract.py`, `tests/test_review.py` (71 tests), two
  `LEFTOVER_INVENTORY` rows, and `tests/harness_contract/codex/` with the one capture that spends no allowance.

**D12 pre-step recorded (2026-10-15).** The operator ran it against `codex-cli 0.162.1` (seven real runs); the
captures are under `tests/harness_contract/codex/` with their digests, `review.PRE_STEP_CAPTURED` is true and
`review.HARNESSES` registers Codex. The implementation review (round 2) corrected the first CP2 build against them:
`code_mode_host` is an allowed tool (the reviewer had none), the project is marked untrusted and the bundled skills
are off (otherwise Codex rewrites the minimal home), control (b) is the app-server `config/read` command driven by
`compose_config` and judges origins, the argv contract is complete, and a limit is read from the rollouts. The
captured-invocation contract test is written (`test_review_argv_is_the_captured_recommended_invocation`).

CP3 changes:
- `controller/job.py`: the review job. `execute_step` takes `reviewer` (None while `reviewer.harness` is unset: every
  decision, record, route and exit code is unchanged) and, at an `external_gate` whose action id is
  `plan.review.external` / `implementation.review.external` **and** whose snapshot phase is the matching
  `AWAITING_MANUAL_EXTERNAL_*` phase (`_review_gate`; rows 21, 31a and 35 stay gates), runs `_run_review_job`
  instead of recording `GATE_BLOCKED`. An unknown or unregistered harness refuses before anything is recorded. The
  record is kind `review`, job `schema_version` 2 (`REVIEW_SCHEMA_VERSION`; a 1.9.0 reader refuses it at
  `validate_record` case 1), with `worker_route.harness`, a `review` block and the Codex `usage` block. Launch
  intent (`review.launched`, `ownership_tag`) is flushed before the spawn; `worker_process` after it. The Codex
  group holds no lifecycle-lock descriptor. Outcomes: an exact verdict is handed to `protocol.record_external_result`
  (`--run-ref codex:<job id>`) after an `expect_state_identity` check, FINISHED; the Workflow's `refused` /
  `not_applicable`, a header mismatch after the one retry and a failure without limit text are FAILED (gate open);
  a limit, timeout, empty run, changed repository or moved state are INCOMPLETE (exit 35); Ctrl-C kills the group,
  ends the record INCOMPLETE, completes the reservation by the measured delta and propagates. A boundary refusal
  before the exec spawn is FAILED and released. `never_launched` has a review case (launch intent decides);
  `validate_record` has the review-kind case; `derive_job_event` names the open gate and the manual route for a failed
  or incomplete review; no `user_gate` is sent for a gate the Controller runs.
- `resume` / `resume --abandon`: the review-job branch `_reconcile_review` (phase 2, before any `reconcile`): ends the
  group by recorded identity and ownership tag, copies the credentials back, removes the copy, writes the record
  `INTERRUPTED` (`INCOMPLETE` past the deadline); the sweep settles it at the forecast, a never-started one is
  released. `review.sweep_retained` removes retained `sessions/` once their windows have reset.
- `controller/cli.py`: `_usage_gate` takes a provider; a Codex admission passes fresh readings from the operator's
  home merged with every retained review home (`usage.read_codex_homes`) on every attempt; the Codex hold message
  names the provider and the `usage.codex_*` settings; `_reviewer_options` passes the `reviewer.*` settings.
- `controller/protocol.py`: `resolve_artifact`; `controller/review.py`: `launch_review(out=)`, `retained_codex_homes`,
  `sweep_retained`; `controller/__init__.py` and the dependency order name `review`.
- `tests/test_review_job.py` (51 tests): the discriminator, the happy path on both stages against a real 2.9.0
  Workflow, outcome mapping, usage, record shape, replays with a real tagged process group (with and without the
  `worker_process` flush), credentials on the resume branch, the real `launch_review` against `tests/fake_codex.py`.

Full suite: 3806 tests, only the two known Git 2.56 trailer tests fail (follow-up, unrelated).

CP4 changes:
- `tests/scenarios/review.py` (catalogue scenarios 11-12; `inventory.CATALOGUE_MODULES` and `catalogue.py` name it):
  a `World` over a real vendored 2.9.0 Workflow at a `AWAITING_MANUAL_EXTERNAL_*_REVIEW` gate, `reviewer.harness`
  `codex`, the real `review.launch_review` against `tests/fake_codex.py` written into the scratch `bin/`, the registry
  patched, the usage clock injected and a scratch operator Codex home.
  - `scenario-codex-review-plan-stage`: a usage hold at admission starts nothing (exit 17); a verdict whose revision
    label is missing is retried once then `FAILED` (exit 30), nothing ingested; a review that writes into the real
    repository is voided (exit 35); the exact verdict is ingested with the whole boundary pinned from the fake's log
    (sandbox, approval, network, web search, provider pin, `notify`, features, environment, minimal home, no inherited
    server/hook/notify run, launch intent flushed before the spawn, copy removed, credential copied back); and a
    boundary sweep of direct launches (outside layer, managed provider, MCP server, unlisted feature, startup-loaded
    entry) refuses each by its named check and starts no review.
  - `scenario-codex-review-implementation-stage-limit`: a limit ends `INCOMPLETE` (exit 35) with a `lane_blocked`
    notification and the reservation charged by the measured delta; with the shared reading forgotten, a second `run` is
    held (exit 17, no new job) by the retained rollout alone; after the reset a third `run` is re-admitted and the
    Workflow records the verdict; an operator file changed meanwhile is never overwritten.
- `tests/test_scenarios_negative.py`: `MUTATIONS_REVIEW`, 21 mutations (each patches one product check, and the scenario
  must end in an assertion). `tests/test_scenarios.py`: 17 flow ids. `tests/scenarios/kit.py`: `tar` and `gzip` join
  `ALLOWED_TOOLS` (`prepare-ai-review.sh` archives the bundle; the sandbox `PATH` had neither).

Verified: `tests.test_scenarios`, `tests.test_scenarios_negative`, `tests.test_scenario_kit` (110 tests).

CP5 changes:
- `docs/adr/0013-review-only-harness-seam.md` (new; records the obligation-5 reading, which the Workflow side has
  not yet confirmed, so the ADR marks it open), `docs/README.md` (ADR map).
- `docs/guide/runtime.md` (the five `reviewer.*` rows, generation 6), `docs/run.md` (letting the Controller run the
  external review), `docs/common-problems.md` (a review that did not finish; the `rm -i` hang),
  `docs/compatibility.md` (the D15 downgrade note: a runtime holding a review record cannot be read by 1.9.0;
  `resume` it with 1.10.0 first; the `command rm -f` / `command cp -f` note), `docs/ROADMAP.md` (C7 complete,
  follow-up 24 fixed, what C10 reuses; `excluded` as workflow bookkeeping).
- `docs/release-history.md` and `docs/releases/1.10.0.md` are written after the release, as for earlier releases.

Verified: `tools/check_docs.py` clean; golden `--check` clean for three of four generators (`plan_stage_decisions`
differs on a clean HEAD as well, so it predates CP5 and is not caused by it); full suite 3821 tests, only the two
known Git 2.56 trailer tests fail.

Self-review (`SELF_REVIEWING_IMPLEMENTATION`, the whole diff from `dea2125`):
- `controller/review.py` `snapshot_repository`: the status filter that drops the job's own directory also dropped any
  untracked path that was only a string prefix of it (an untracked `r` beside a runtime directory `run/...`), so a
  status change could go unseen there; the file manifest still caught it. The collapsed-parent clause now requires a
  directory entry. `tests/test_review.py` `GuardTest` pins both sides (it fails without the fix).
- No other defect found in `review.py`, the review job and resume branch of `job.py`, `cli.py`, `protocol.py`,
  `runtime.py`, `settings.py` or `usage.py`.
- Still open, and neither one can be closed from inside the implementation: the D2 acceptance condition (the Workflow
  side's confirmation of the obligation-5 reading, which ADR 0013 marks open) and the D12 operator pre-step (the
  harness stays unregistered until it is recorded).

Full verification after the fix: `tools/check_docs.py` clean; golden `--check` clean for three of four generators
(`plan_stage_decisions` differs on base `dea2125` as well); `tools/run_tests.py` coverage exact, only the two known
Git 2.56 trailer tests fail.

## Next action

The self-review and the full verification are done. The next step is the implementation-review bundle (revision 1)
and the local implementation review. The `codex` harness still cannot be registered until the operator records the
D12 pre-step under `tests/harness_contract/codex/` and sets `PRE_STEP_CAPTURED`.

## Functional review checklist

Scope: C7, the Controller-run Codex reviewer (`reviewer.harness`). Everything below is manual; the automated suite
(3821 tests, only the two known Git 2.56 trailer tests fail) and the lifecycle scenarios 11-12 already cover the
mechanics against `tests/fake_codex.py`.

**Setup**
- Check out `milestone/workflow-controller-codex-reviewer` and run the Controller from it
  (`PYTHONPATH=<repo> python3 -m controller ...`); use a scratch repository on Workflow 2.9.x, never this one.
- A working `codex` CLI (`codex-cli 0.162.1` was used for the D12 captures), logged in, with allowance left.
- Settings file: `reviewer.harness = "codex"`; optionally `reviewer.model`, `reviewer.effort`,
  `reviewer.timeout_seconds`, `reviewer.binary`.
- A scratch work item parked at `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` (and, for flow 4, the implementation
  equivalent).

**Flows and expected results**
1. *Unset is unchanged.* With `reviewer.harness` unset, `run` at an external review gate stops at the gate
   exactly as on 1.9.0 (gate event, no job of kind `review`).
2. *Happy path, plan stage.* With `harness = codex`, `run` starts a review job: a `review` job record
   (schema_version 2) with launch intent before spawn, the verdict header matching the bundle exactly, the verdict
   ingested via the Workflow (`--run-ref codex:<job id>`), job `FINISHED`, the disposable copy removed, only
   `sessions/` kept, the real repository untouched.
3. *Boundary.* The job record and `codex` log show: workspace-write limited to the disposable copy and its private tmp directory, approval never, network off, web search disabled, minimal Codex
   home (not your `~/.codex` config), no MCP servers, hooks or notify run. Your own `~/.codex` files are unchanged
   afterwards except refreshed credentials.
4. *Implementation stage.* Same as flow 2 at `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`.
5. *Bad header.* If the reviewer's verdict header is wrong it is retried once; after a second mismatch the job is
   `FAILED` (exit 30), nothing is ingested and the gate stays open for the manual route.
6. *Limit / timeout.* Lower `reviewer.timeout_seconds` to 60 (or use an exhausted allowance): the job ends
   `INCOMPLETE` (exit 35), reservation charged by the measured delta; a following `run` is held at admission
   (exit 17) until the reset, then re-admitted.
7. *Ctrl-C and resume.* Interrupt a running review: the Codex group is killed, the record ends `INCOMPLETE`;
   `resume` (and `resume --abandon`) settle the record and remove the copy, with no orphan `codex` process left.
8. *Refusals.* An unknown `reviewer.harness` value refuses before anything is recorded; a managed Codex
   configuration (managed provider, `model_catalog_json`, MCP server in an outer layer) refuses by its named check
   and starts no review.
9. *Other gates.* A gate that is not a manual external review (e.g. acceptance, rows 21/31a/35) still stops with
   `GATE_BLOCKED`.
10. *Docs.* `python3 tools/check_docs.py` is clean; `docs/run.md` (letting the Controller run the external review),
    `docs/common-problems.md` and `docs/compatibility.md` (D15 downgrade note) match what you saw.

**Functional review round 1** (independent Codex tester, 2026-10-15): flows 1-9 PASS; flow 10 two Optional documentation
findings, both fixed in the bounded round: `docs/common-problems.md` now keeps exit 20 for the unknown harness only and
lists launch-boundary refusals under exit 30 (`ReviewLaunchRefused`, `not_started`); flow 3 above now describes the
workspace-write boundary. No remediation child. Re-test: flow 10 (docs) after the next implementation review and technical approval.

**Known limitations / out of scope**
- Only Codex is registered; other harnesses are refused.
- A reviewer may spawn a sub-agent (Codex cannot remove the tool); the job record names the spawn and its tokens.
- The Workflow-side protocol paragraph is pending in Workflow 2.12.0 (workflow#28 stays open).
- Two Git 2.56 trailer tests fail locally (separate follow-up); `plan_stage_decisions` golden differs on base too.
- Release notes (`docs/releases/1.10.0.md`) are written after release.
