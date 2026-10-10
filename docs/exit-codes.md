# Exit codes

> For: anyone who runs the Controller by hand or from a script. Last checked with: Controller 1.7.0; Workflow 2.6.0, 2.7.0 and 2.8.0.

The Controller is built to be driven by an outer supervisor, so its exit
status is part of its contract. This is the one table of them. The normative
copy, which the Controller's own tests compare with the code, is
[ADR 0001, "Exit codes"](adr/0001-controller-generation-1-architecture.md#exit-codes).

| Code | Meaning | What to do |
|---|---|---|
| 0 | the requested work completed and nothing is pending | nothing |
| 1 | `usage --renew` or `--release` named a token the usage record does not know (`--renew` also refuses one already accounted); nothing was changed | check the token the `usage --reserve` call printed |
| 2 | the command line did not parse | put global options such as `--work-item` before the subcommand and the repository last: `workflow-controller --work-item <id> explain <repo>` |
| 10 | stopped cleanly at a [gate stop](glossary.md#gate-stop), waiting for you | the normal end of a run. Run `workflow-controller explain <repo>`, do what it says, then run again |
| 15 | the next action is valid but not automated, so it was reported and not launched | do it yourself; `explain` names the phase and the command |
| 16 | `run` reached `--max-steps` with work still outstanding | run again, or raise `--max-steps` |
| 17 | stopped before starting because of the usage budget; nothing was started (`usage --check` and `--wait` use it for a hold too) | a timed pause: run again after the resume time the message names, or let `run` wait for it; a run or repository cap: raise the cap (`usage.run_cap_percent`, `usage.repository_cap_percent`, `--usage-cap`), start a new run, or wait for the window. See [common problems](common-problems.md#the-command-exited-17-and-nothing-was-started) |
| 20 | a fail-closed refusal: an unmanaged or drifted repository, a malformed state file, an unreconciled job file, a bad routing file, an unusable settings file, a changed Workflow release, or a failed Workflow query or protocol operation | read the message; for a pending job run `workflow-controller resume <repo>` |
| 30 | a worker ran and failed its expected outcome | `explain` shows what was checked; fix it and run again, or finish the step by hand |
| 35 | a worker ran and stopped without completing its action, leaving nothing durable to verify | `explain` and the job's log show why |
| 40 | the Controller itself was interrupted, or `resume` marked a record as interrupted | `workflow-controller resume <repo>` |
| 45 | the worktree is held: another Controller, or a worker that may still be running, holds its lock, so nothing was launched | wait, or follow the message; the hold is not a failure |
| 50 | a generation handoff is pending: a newer Controller generation is installed and the running one stopped on purpose | run again with the new version |
| 130 | you pressed Ctrl-C during `step`, `run` or `resume` (while `run` waited for checks, a merge or a release it prints one line; otherwise you get a traceback); Ctrl-C in `follow` is exit 0 and the run is unaffected; the shell reports 130 as 128 plus the interrupt signal | `workflow-controller resume <repo>` if a job was left running |

Exit 10 and exit 50 are successes of the design, not errors, and exit 45 is a
wait. An outer script should treat them differently from 0 and from 20.

The quick fix for each stop is in [common problems](common-problems.md); the
situations behind them are in the [troubleshooting guide](guide/troubleshooting.md). Terms are in the
[glossary](glossary.md).
