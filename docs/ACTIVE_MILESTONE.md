# Active Milestone

## Status

**Implementing.** `workflow-controller-usage-budget` (`docs/ROADMAP.md` step C8, section 11.6): the
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
| CP4 the `usage` subcommand and the manual-worker gate | not started |
| CP5 documentation and roadmap | not started |

CP1 added `controller/usage.py` (a leaf below `job`, `observe` and `cli`), `runtime.usage_lock`,
`errors.UsageRecordError`, a golden stream (`tests/golden/claude_stream_usage_limit_rejected.jsonl`,
sanitised from a real `rejected` stream) and `tests/test_usage.py`.

CP2 added the sixteen `usage.*` rows (table generation 4, `TABLE_GENERATION = 4`; the generation invariant is
now `>=`), `run --usage-cap` and `step --usage-cap` (`usage --usage-codex-cap` arrives with the `usage`
subcommand in CP4), and regenerated `tests/golden/no_policy_lifecycle.json` (only the new `controller_settings`
keys moved, pinned by `tests/test_trunk_preflight.py`). The full suite fails only the two Git 2.56 trailer tests
that fail on the base too.

CP3 added `UsageHold`, the `usage_gate` argument and the `usage` job-record block in `controller/job.py`
(bind before publish, renewal ticker, end figures in the `COMPLETED` write, the replay sweep in
`_execute_step_locked` and `resume`, the `_reattach` accounting), the gate builder, the waiting `run` loop and
exit 17 in `controller/cli.py`, the `observe` rendering, the `CODEX_HOME` test redirection and the exit-17 rows
in `docs/exit-codes.md` and ADR 0001. The no-policy golden gained each job's `usage` block. The full suite
fails only the two Git 2.56 trailer tests.

## Next action

`/milestone-implement workflow-controller-usage-budget` runs the next checkpoint, one per invocation.
