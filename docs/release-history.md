# Release history

> For: anyone who wants to know what each Controller release changed. Last checked with: Controller 1.7.0; Workflow 2.6.0, 2.7.0 and 2.8.0.

One line per release, newest first. Release notes exist from 1.3.0 and are
linked. Which Workflow each release admits is on the
[compatibility page](compatibility.md).

| Release | What changed | Notes |
|---|---|---|
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
