# Release history

> For: anyone who wants to know what each Controller release changed. Last checked with: Controller 1.14.0; Workflow 2.6.0, 2.7.0, 2.8.0, 2.9.0, 2.10.0, 2.11.0, 2.12.0 and 2.13.0.

One line per release, newest first. Release notes exist from 1.3.0 and are
linked. Which Workflow each release admits is on the
[compatibility page](compatibility.md).

| Release | What changed | Notes |
|---|---|---|
| 1.14.0 | Under an automatic gate the Controller launches the Workflow's `/satisfy-gate` itself (the `satisfy-gate` role; settings generation 9), so a satisfied gate passes without a person and a refused one stops naming the unmet requirement; a value a protocol 1.x minor release adds is advisory, ignored or a stop, never a refusal; a stop the Workflow calls blocked sends `lane_blocked` | [1.14.0](releases/1.14.0.md) |
| 1.13.1 | A patch release that fixes sixteen review follow-ups and known defects: a model alias for the Claude reviewer is refused before launch, a Claude review's cost and tokens are kept for `telemetry`, recovery messages name the review's harness, and the plan round cap works on `"1"`-governed work items (no new setting) | [1.13.1](releases/1.13.1.md) |
| 1.13.0 | The owner chooses how the external review runs (`review.mode`, `--review-mode`; settings generation 8): a fresh Claude reviewer under a lowered gate policy, so a one-provider user is not blocked, with the `review_mode_unavailable` gate | [1.13.0](releases/1.13.0.md) |
| 1.12.0 | A protocol review loop stops at a round cap per stage (`review.round_cap`, default 4, `--review-round-cap`; settings generation 7) with the `review_round_cap_reached` gate, and `telemetry` groups jobs by action, stage and round | [1.12.0](releases/1.12.0.md) |
| 1.11.0 | A worker that reaches the usage limit mid-job having changed nothing is recorded `INTERRUPTED`; `run` waits for the reset and repeats the step (exit 17 for `step`; no new setting) | [1.11.0](releases/1.11.0.md) |
| 1.10.0 | Opt-in: the Controller runs the cross-model review itself with a Codex reviewer in a disposable copy and hands the verdict to the Workflow (new `reviewer` settings, settings generation 6) | [1.10.0](releases/1.10.0.md) |
| 1.9.0 | The Controller pushes its events to SignalHub as configurable, idempotent notifications (new `notifications` command, settings generation 5) | [1.9.0](releases/1.9.0.md) |
| 1.8.0 | A usage budget: the Controller reads the Claude and Codex windows, forecasts each job, shares reservations between lanes and pauses a run before a limit (new `usage` command, settings generation 4, exit code 17) | [1.8.0](releases/1.8.0.md) |
| 1.7.1 | A Workflow check with status `warn` is advisory instead of a refusal, and the Workflow 2.9.0 protocol schema is vendored | [1.7.1](releases/1.7.1.md) |
| 1.7.0 | The Controller drives a Workflow that ships the Orchestration Protocol (2.7.0 first), admitted by capability, and its jobs are judged by the Workflow's own reconcile | [1.7.0](releases/1.7.0.md) |
| 1.6.0 | A repository can let the Controller merge an accepted milestone and wait for the release | [1.6.0](releases/1.6.0.md) |
| 1.5.0 | One shared settings file for every tunable, per-job telemetry, and release notes written by the milestone | [1.5.0](releases/1.5.0.md) |
| 1.4.2 | Fixes for known timing flakes in the tests, and a re-run of failed checks now counts | [1.4.2](releases/1.4.2.md) |
| 1.4.1 | The Controller collects every finished child process it holds, so long runs no longer leave zombies | [1.4.1](releases/1.4.1.md) |
| 1.4.0 | Squash-merged pull requests, with the release version taken from the pull request title and the Git tag as the only version authority | [1.4.0](releases/1.4.0.md) |
| 1.3.0 | Drives a target on Workflow 2.6.0 as well as 2.5.1 | [1.3.0](releases/1.3.0.md) |
| 1.2.1 | A faster, balanced test runner shared by CI and local runs, and a longer bound for a worker's background processes to finish | none |
| 1.2.0 | Milestone branches, pull requests and automatic releases; the first release the release pipeline published itself; and safer ownership of a worker's lifecycle | none |
| 1.1.1 | The first release published at all, with an isolated runtime, a wheel with checksums and live observation of workers; it ships what 1.1.0 was meant to | none |

Version 1.1.0 was tagged but its release validation failed before anything was published, so it is recorded as abandoned.
