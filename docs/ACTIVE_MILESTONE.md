# Active Milestone

## In progress: automated lifecycle scenarios (`workflow-controller-automated-lifecycle-scenarios`, step C6)

Checkpoint **CP1** (the scenario kit) is complete. `tests/scenarios/` holds the registry, the kit and the sandbox
setup phase; `tests/test_scenario_kit.py` pins them with stand-ins. Verified: the two-phase sandbox (user, network
and mount namespaces, no PID namespace, a capability drop to the invoker's uid) cuts off the network and every real
location through a shell, a git hook, a `python -E` child and a re-exec; an unavailable sandbox fails closed; the
checkout snapshot is content-sensitive; the kill-points sit at the primitives the write inventory measured.
Measured against the plan: the milestone binding is published by `runtime.create_json`, and a kill *before* the
job record that names a spawned worker (`job-before`) leaves a stdin anchor that `resume` cannot reconcile (exit
45), so scenario 4 needs a plan amendment for that variant.

Checkpoint **CP2** (the runner) is complete. `tools/run_scenarios.py` has `list`, `run` and `check`: `run` writes
`<flow_id>.json` records in the protocol's `functional_evidence` shape plus `index.json` bound to `HEAD`, refuses
(exit 2, nothing written) a tree whose code differs from `HEAD` and an unavailable sandbox, and exits 1 after
writing a `failed` record for a failing scenario. `--degraded-diagnostic` writes reports that are not evidence.
`tests/test_run_scenarios.py` runs a clone's copy of the runner against the selftest scenarios and feeds each
record through the real `ingest_functional_evidence` (stored `head` and recomputed identity asserted separately);
`tests/test_scenarios.py` generates one `TestCase` class per catalogue scenario (none until CP3) and checks the
planner inventory. The registry function is now `catalogued()`, because `catalogue` is the submodule that
registers the scenarios.

Checkpoint **CP3** (scenarios 1-5) is complete. `tests/scenarios/lifecycle.py` registers ten flows over the existing
`Lifecycle` and `_E2ECase` models, run inside the sandbox with the scratch `bin/` fakes: `full-lifecycle-to-merge`,
`human-gates-stop-and-resume` (plan approval, technical approval and the acceptance track, each stopping with exit
10 and zero workers, then resuming after the simulated user act), `worker-failure-fails-closed` (non-zero exit,
timeout, unverified effect: exit 30, a `FAILED` record, no phase advance, one worker per `run`), `trunk-drift` and
the six `kill-and-resume-<point>` variants. `tests/test_scenarios_negative.py` breaks each claim and requires an
assertion failure. Measured oracles for scenario 4: `binding-*` and `run-*` are pre-spawn (no worker, no job record,
lock free, `resume` exits 0 and changes nothing); `job-after` leaves a `LAUNCHED` record and the killed Controller's
stdin anchor holding the lock until `resume` reconciles it to `FINISHED`; `job-before` is the plan's amended case:
`resume` exits 45 until the scenario ends the anchor, then reconciles. Every variant's continuation `run` reaches
the bound step's end state (exit 10, `BRANCH_BOUND`, each worker command run exactly once). Two kit fixes came out
of this: `tempfile.tempdir` was cached before `TMPDIR` pointed at the scratch tree (case directories landed in the
real `/tmp`), and a per-shard `TMPDIR` that lacks `workflow-controller-tests` made the plan cover the temporary
directory read-only.

Checkpoint **CP4** (scenarios 6-10) is complete. `tests/scenarios/features.py` registers
`squash-merge-and-release-classification` (the squash lifecycle over a policy with the release enabled and a published
`v1.0.0`: a `docs:` commit classifies `NO_CHANGE`, the `feat:` squash `RELEASE_DUE` 1.1.0),
`notifications-never-steer` (the fake SignalHub up, answering 500, slow and down: identical exit codes and normalised
job records, the up case's ordered events with distinct `Idempotency-Key`s, the key in no file or output),
`usage-pause-and-resume` (admission only: a timed hold and a run-cap hold exit 17 with no worker and no job record and
no sleep; after the injected clock passes the reset a second `run` launches all six workers), `read-only-commands-
change-nothing` (`status`, `explain`, `inspect`, `follow` and `status --json` before each of the eleven steps of the
whole lifecycle; only `identity.json` may be added under the runtime root) and `settings-file-effect` (observation
writes nothing; `settings clean`, `step` and a malformed file each with an exact footprint). Measured while writing
them: `status` takes no repository, `follow` writes nothing at all (not even `identity.json`), and the settings lock
file persists, so only a first use shows it as added. Each scenario has a mutation check in
`tests/test_scenarios_negative.py`, and the timings are in `tools/test_timings.json`. 

Checkpoint **CP5** (documentation and the full run) is complete. `docs/guide/development.md` has a "Lifecycle
scenarios" section (what a scenario is, running the catalogue, the evidence record, the sandbox and the enforced-versus-
detected split, what a `passed` record proves and the uncovered mid-job usage path, adding a scenario),
`docs/guide/automation.md` a short paragraph, and `docs/ROADMAP.md` marks step C6 complete. A whole-catalogue
determinism test (`CatalogueDeterminismTest`) runs the complete catalogue twice and compares every normalised summary.
`controller/` is byte-unchanged and no release is cut. Verification results are in the checkpoint's final report and
in the implementation bundle.

The sections below describe the previous milestone and stay until the milestone is accepted.

## Status

**Complete.** `workflow-controller-signalhub-notifications` (`docs/ROADMAP.md` step C5, section 11.4)
reached `MILESTONE_COMPLETE` on 2026-10-12. The Controller is now a SignalHub producer: it sends 14 event
types (progress, human gates, blockers, merges, releases and the usage events of ADR 0011) to SignalHub's
`POST /api/v1/events` from the records it already keeps. Each type is configurable in
`notifications.events` (on/off, category, severity), every send is idempotent and runs off the lifecycle's
thread, delivery never changes a step's outcome, and `workflow-controller notifications [--test]` checks the
wiring. ADR `docs/adr/0012-signalhub-notifications.md` records the design. The milestone ships as the minor
release **1.9.0** when its `feat:` pull request merges.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`). Functional review round 1 found
three Minor issues, fixed by a bounded fix (`5a8233a`, `117449a`). Round 2 (implementation revision 7),
checked against evidence commit `fe70047`, passed every flow (flow-1 to flow-9). An independent tester on
`anthropic/claude-sonnet-5-5` ran the flows.
- Plan revision 16 was approved by policy at `8056cc3`. The base commit is `e0f400e`.
- Technical approval `9db833f` was recorded by policy and is `CURRENT`: review content id `a706b67c`,
  reviewed implementation head `117449a`.
- The Workflow's own GitHub query found pull request #41 at head `fe70047` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP5) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, including its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C5 complete. The full milestone
narrative is archived verbatim at `docs/milestones/completed/workflow-controller-signalhub-notifications.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge and release.** Pull request #41, titled `feat: push run events to SignalHub as configurable
  notifications`, is merged once every check passes; `main.yml` then releases 1.9.0.
- **Lane coordination after the release** (ROADMAP 11.4, plan D9). The lane orchestrators stop
  hand-sending the events the Controller now sends, run `resume` before `run --max-steps 1` after a killed
  process, and give every launch path a key (`notifications.api_key_file`, or `SIGNALHUB_API_KEY_FILE` in
  `start-run.sh`).
- **Two Minor observations from functional review round 2** (no change needed; see
  `.ai-review/archive/workflow-controller-signalhub-notifications/functional-review-round2-PASSED.md`):
  `--json` is a global option placed before the subcommand, and a plain `usage` lapses only job-bound
  reservations (an unbound one lapses on the next reserve, check, renew or release).
- **Known limitations kept out of scope:** no persistent outbox, no SignalHub changes, and no
  notifications for the Manager or Workflow repositories.

## Next action

1. Pull request #41 merges (a `feat:` pull request, releasing 1.9.0).
2. `/milestone-plan` for C6, the next step in `docs/ROADMAP.md`'s order (C6, C7, C10, C12, C11, then
   O1-O7).
