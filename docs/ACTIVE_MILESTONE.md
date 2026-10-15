# Active Milestone

## Status

**Implementing.** `workflow-controller-codex-reviewer` (`docs/ROADMAP.md` step C7) runs the cross-model review
with a Codex reviewer the Controller starts itself, opt-in through `reviewer.harness`. Plan revision 15 was
approved by policy (commit `3d304d3`); the base commit is `dea2125`. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_REVIEWER_PLAN.md`.

| Checkpoint | State |
| --- | --- |
| CP1 settings rows, `record_external_result`, `rm -f` note | committed (`f880306`) |
| CP2 Codex harness | implemented; the D12 operator pre-step is open, so the harness stays unregistered |
| CP3 review job | not started |
| CP4 lifecycle scenarios | not started |
| CP5 documentation and full suite | not started |

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

**Open acceptance condition for CP1 (D2):** the Workflow side has not yet confirmed the obligation-5 reading. It
is an issue on the Workflow repository whose answer is recorded in ADR 0013; CP1 does not pass without it.

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

**D12 pre-step is open, so CP2 stops at its boundary as the plan requires.** Not captured: the real
`codex exec` argv and rollout, the sandbox probe, the command that composes the configuration layers without
starting anything, the state entries Codex writes into an empty home, the `ENV_ALLOWLIST` the installed Codex needs
(the `PATH` directories included), and whether `--ignore-rules` and a system-layer relocation are accepted. The
constants that depend on them (`TOOL_ALLOWLIST`, `DISABLED_FEATURES`, `SERVICE_TIER`, `CODEX_STATE_ENTRIES`,
`CODEX_STARTUP_SUBENTRIES`, `COMPOSE_COMMAND`) are marked *provisional* in `controller/review.py`.
`review.PRE_STEP_CAPTURED` is false and `review.HARNESSES` is empty, so the harness cannot be selected; a test pins
both. The captured-invocation contract test and the sandbox-probe fixture are therefore not written.

## Next action

Commit CP2. CP3 cannot register the harness until the operator records the D12 pre-step under
`tests/harness_contract/codex/` and sets `PRE_STEP_CAPTURED`; CP3's own tests patch the registry. Then invoke
`/milestone-implement` again for CP3.
