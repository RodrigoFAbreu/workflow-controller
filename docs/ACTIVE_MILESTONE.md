# Active Milestone

## Status

**Self-reviewed; awaiting the local implementation review.** `workflow-controller-usage-budget` (`docs/ROADMAP.md` step C8, section 11.6): the
Controller reads the Claude and Codex usage windows itself, forecasts a job, admits it atomically against
the readings and the other lanes' reservations, and pauses a run before a limit. Plan:
`docs/ai-workflow/CONTROLLER_USAGE_BUDGET_PLAN.md` (revision 11, approved by policy at `03ddec0`; base
commit `f516e76`; pull request title `feat: a usage budget that pauses a run before a Claude or Codex limit`).
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for the checkpoint states.

| checkpoint | state |
| --- | --- |
| CP1 `controller/usage.py` (readers, shared record, merge, admission, forecast, decision) | complete, verified by `tests/test_usage.py` |
| CP2 settings generation 4 and the `--usage-cap` flags | complete, verified by `tests/test_settings.py` (`UsageRowsTest`), the regenerated no-policy golden and the full suite |
| CP3 the gate, the reservation and the run loop (exit 17) | complete, verified by `tests/test_usage_gate.py` (38 tests), the regenerated no-policy golden and the full suite |
| CP4 the `usage` subcommand and the manual-worker gate | complete, verified by `tests/test_usage_command.py` (57 tests) and the full suite |
| CP5 documentation and roadmap | complete, verified by `tools/check_docs.py` and the full suite (3291 tests; only the two Git 2.56 trailer failures) |

CP1 added `controller/usage.py` (a leaf below `job`, `observe` and `cli`), `runtime.usage_lock`,
`errors.UsageRecordError`, a golden stream (`tests/golden/claude_stream_usage_limit_rejected.jsonl`,
sanitised from a real `rejected` stream) and `tests/test_usage.py`.

CP2 added the sixteen `usage.*` rows (table generation 4, `TABLE_GENERATION = 4`; the generation invariant is
now `>=`), `run --usage-cap` and `step --usage-cap` (`usage --usage-codex-cap` arrived with the `usage`
subcommand in CP4), and regenerated `tests/golden/no_policy_lifecycle.json` (only the new `controller_settings`
keys moved, pinned by `tests/test_trunk_preflight.py`). The full suite fails only the two Git 2.56 trailer tests
that fail on the base too.

CP3 added `UsageHold`, the `usage_gate` argument and the `usage` job-record block in `controller/job.py`
(bind before publish, renewal ticker, end figures in the `COMPLETED` write, the replay sweep in
`_execute_step_locked` and `resume`, the `_reattach` accounting), the gate builder, the waiting `run` loop and
exit 17 in `controller/cli.py`, the `observe` rendering, the `CODEX_HOME` test redirection and the exit-17 rows
in `docs/exit-codes.md` and ADR 0001. The no-policy golden gained each job's `usage` block. The full suite
fails only the two Git 2.56 trailer tests.

CP4 added the `usage` subcommand to `controller/cli.py` (dispatched before pinning like `follow`: it writes only
the shared usage record and `usage-events.jsonl`): the view, `--check`, `--wait` (re-evaluating after every
wait, bounded by `usage.max_wait_seconds`), `--reserve`, `--renew`, `--release` with `--outcome` and
`--stream`, the role/model/repository/run context and the `--usage-cap`/`--usage-codex-cap` flags. `usage.admit`
gained `reserve=False` for a check. Exit status 1 (`usage --renew`/`--release` of an unknown token) is on the exit-code
page and in ADR 0001.

CP5 documented the budget: the sixteen `usage` rows and the usage files in `docs/guide/runtime.md`, the `usage` command in `docs/guide/commands.md`, `docs/run.md` and `docs/common-problems.md` (exit 17, timed pause versus cap), the exit-17 row in `docs/exit-codes.md`, `docs/adr/0011-usage-budget.md` and its README row, and `docs/ROADMAP.md` (C8 complete; C8b and C8c added with sections 11.6.1 and 11.6.2; C7 and C5 amended; "Next" names C5).

Self-review (`SELF_REVIEWING_IMPLEMENTATION`, 2026-10-10): the full diff from `f516e76` was reviewed against D1-D13.
No Blocking or Important findings. One Optional finding was fixed: `docs/guide/commands.md` and the `cmd_usage`
docstring said `usage` writes only the shared record and `usage-events.jsonl`, but the replay sweep it runs first
(plan D6, "on a `usage` read") also marks a terminal job record a crash left unaccounted `accounting: "done"`. The
text now says so. Not changed, by design: a Ctrl-C in the pure record-building lines between the gate and
`bind_job` leaves an unbound reservation. That window is no wider than the one inside `usage.admit` itself, and
the lapse and seven-day abandonment backstop covers both.

Full verification: `python3 tools/check_docs.py` exit 0; `python3 tools/run_tests.py` (under a reaping subreaper,
without `FORCE_COLOR`): 3300 tests in 8 shards, coverage exact, 2 failures. Both are the Git 2.56 trailer-rule
tests (follow-up 12), which also fail on the base.

## Next action

The implementation review bundle (revision 1) is generated; the local implementation review comes next.
