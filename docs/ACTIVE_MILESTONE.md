# Active Milestone

## Status

**Implemented.** `workflow-controller-orchestrator-stops` (NF3, issue
[#244](https://github.com/RodrigoFAbreu/workflow-controller/issues/244)) and self-reviewed; it awaits the implementation
reviews and the functional review. The plan is `docs/ai-workflow/CONTROLLER_ORCHESTRATOR_STOPS_PLAN.md`. A gate record
carries `protocol_gate` with `row`, `disposition`, `action_id`, `reason_code` and the Controller's own `code`, and
`derive_job_event` announces a stop the orchestrator handles as `orchestrator_wait` (a wait) or `lane_blocked` (a repair)
instead of `user_gate`; every other, unknown or legacy stop stays `user_gate`. The automation and commands guides, ADR 0012
and the roadmap state the rule in place. The self-review changed one test: the scenario
`scenario-codex-review-implementation-stage-limit` stops at a `gate_evidence_unmet` technical approval gate, so it now
expects `lane_blocked` there, not `user_gate` (D2). The change is a `fix:`, a patch release.

Functional-review checklist (offline, unit-level; run one module at a time with `python3 -B`, a writable `TMPDIR`, no
`PYTHONPATH=.`): `tests/test_notifications_wiring.py` (a record per stop of the issue's table gives the named event, and
each real decision stays `user_gate`), `tests/test_notify.py` (`orchestrator_wait` is `INFO`/`LOW`, can be disabled and
validates in a settings file), `tests/test_protocol_job.py`, `tests/test_pr_report.py` and
`tests/test_functional_lifecycle.py` (the existing `review_wait`, `checks_wait` and `lane_blocked` cases pass unchanged),
and `python3 -B tools/check_docs.py`.

Next action: the implementation reviews of the bundle `/milestone-implement` prepared (local, then external). The next
item on the roadmap is H4b.

**Complete.** `workflow-controller-user-only-scan` (TK1, issue
[#247](https://github.com/RodrigoFAbreu/workflow-controller/issues/247)) is implemented (CP1 and CP2). The plan is
`docs/ai-workflow/CONTROLLER_USER_ONLY_SCAN_PLAN.md`. `worker._task_tokens` splits a task on `=` and on every character a
name or path cannot contain, so the Claude launch scan and the Codex renderer find a user-only command name wrapped in `!`,
`?`, brackets, quotes or markup, or behind `=`; a name inside a longer word or path is still not refused. The workers guide
and the roadmap state the rule in place. The change is a `fix:`, a patch release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-command-render` (H4a, issue
[#242](https://github.com/RodrigoFAbreu/workflow-controller/issues/242)) is implemented (CP1 and CP2 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_COMMAND_RENDER_PLAN.md`. `controller/command_files.py` adds
`render_command`, a pure function that renders a shared command file and an argument string into a Codex prompt and
refuses the user-only commands (`tests/test_command_files.py`). Nothing calls it. The harness and worker guides and the roadmap state it in place; the change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-fallback` (C10g-3c2, issue
[#236](https://github.com/RodrigoFAbreu/workflow-controller/issues/236)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_FALLBACK_PLAN.md`. A `functional-tester` chain longer than one is walked before the
spawn (`codex_tester.selection_error`, `job._tester_probe`), a Codex tester has a `usage_record` from its rollouts and a
`telemetry` block, and `tests/test_codex_tester_live.py` has a second opt-in test through `_functional_launch`. The first
real round (31 October 2026: `gpt-6.1-sol`, effort `high`, `job.execute_step` on a scratch item at row 38e) ran to
`findings` with a correct report, credentials `unchanged`, the sandbox removed, 12,594 input, 46,080 cache-read, 1,061
output and 93 reasoning tokens in `usage_record` and a `telemetry` block with harness `codex`; no defect was found. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-routing` (C10g-3c1, issue
[#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223)) is implemented (CP1 to CP3 and the functional review) and
accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_ROUTING_PLAN.md`. Routing accepts `codex` for the
`functional-tester` role (the model `gpt-6.1-sol` and an effort `high` or `xhigh` named, no Codex defaults), and
`job._run_functional` runs the launch, usage provider, credential and record fields of the route's harness;
`cli._functional_options` carries both harness homes and binaries. The guides, the table and the roadmap state it in place. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-surroundings` (C10g-3b2b, issue
[#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)) is implemented (CP1 to CP3 and the functional
review) and accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_SURROUNDINGS_PLAN.md`. Codex declares the
tester role with `verify-codex`, routing refuses the pair until C10g-3c, and `codex_tester.launch_codex_functional` adds the
build gate, the before/after snapshots, the binding check and the refusal mapping, with `tests/test_codex_tester_launch.py`;
the guides, the generated table and the roadmap state the new facts in place. Nothing calls the launch until C10g-3c; the
change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3, the self-review fixes and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Functional review checklist

Item `workflow-controller-user-only-scan`, implementation revision 1. Every flow is offline and unit-level (no real Claude,
Codex, network or `gh`). Run from the product clone with `TMPDIR` set to a writable scratch directory, one module at a
time, `python3 -B` and no `PYTHONPATH=.`. The tester's sandbox has a read-only `/tmp`: the flows that run `tests.test_worker`
or `tests.test_command_files` create folders there, so on that sandbox they are `environment`-limited and operator-run.

| # | Flow | Command | Expected |
|---|---|---|---|
| 1 | Claude launch scan (R1, R2) | `python3 -B -m unittest tests.test_worker` | Passes: each of the four `USER_ONLY_COMMANDS` is refused by `_assert_not_user_only` and by `launch` (no process started) in every spelling (`!`, `?`, brackets, quotes, `=`, markup, `~/name`); longer-word and path forms (`./name`, `../name`, `a/name`, `name.md`) are not refused; the fixed corpus agrees with the old rule on every entry. |
| 2 | Codex renderer (R1, R2) | `python3 -B -m unittest tests.test_command_files` | Passes: every user-only name derived from `.claude/commands/` (eleven) is refused in the same spellings, in the argument string and by the renderer's own scan alone; path forms are not refused. |
| 3 | Documents (R3) | `python3 -B tools/check_docs.py` | Exits 0; `docs/guide/workers.md` states the delimiter rule; `docs/ROADMAP.md` marks TK1 done. |

**Known limitations / out of scope.** The set of user-only names is unchanged; a name glued to letters is not seen; the
nested-invocation limit, NF3 and H4b are later items. No new setting, option, exit code or gate; the change is a `fix:`, a
patch release.

## Next action

`/milestone-plan` for NF3 ([#244](https://github.com/RodrigoFAbreu/workflow-controller/issues/244), the false ACTION_REQUIRED alerts), then H4b (the headless Codex launch behind the H3 seam); TK1 is implemented.
