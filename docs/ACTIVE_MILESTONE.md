# Active Milestone

## Status

**Complete.** `workflow-controller-automated-lifecycle-scenarios` (`docs/ROADMAP.md` step C6, section 11.5)
reached `MILESTONE_COMPLETE` on 2026-10-12. Whole lifecycle paths now run as automated scenarios: ten scenarios
(fifteen flows, the kill-and-resume path having six variants) build disposable repositories with a scratch origin
and fake `claude`, `gh`, `workflow-manager` and SignalHub, run inside a namespace sandbox with no model usage, and
write `functional_evidence` records through `tools/run_scenarios.py` (`list`, `run`, `check`). Each scenario has a
mutation check (`tests/test_scenarios_negative.py`), and the CI `tests` job lifts the runner's AppArmor
restriction on unprivileged user namespaces so the scenario atoms run there fully protected. `controller/` is
byte-unchanged. The milestone is a `test:` change and releases nothing.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`). Functional review round 1
(implementation revision 8), checked against evidence commit `cdf8870`, passed every flow (flow-1 to flow-11,
flow 7 partly by kit tests and code reading) with no findings. An independent tester on
`anthropic/claude-sonnet-5-5` ran the flows.
- Plan revision 10 (amendment 0, requested by the user: run the scenarios fully protected on CI) was approved by
  policy at `191eaac`. The base commit is `651f7da`.
- Technical approval `90706ee` was recorded by policy and is `CURRENT`: review content id `60430a8a`,
  reviewed implementation head `2597dee`.
- The Workflow's own GitHub query found pull request #43 at head `cdf8870` with every check passing (CI run
  38317047808, Workflow conformance, PR title) and no standing objection.

All registry checkpoints (CP1-CP6) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of this transition, including its eleven `functional_evidence` records and its
`acceptance_satisfaction` evidence, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C6
complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-automated-lifecycle-scenarios.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge.** Pull request #43, titled `test: run whole lifecycle paths as scenarios that produce functional
  evidence`, is merged once every check passes; it releases nothing.
- **The `job-before` oracle deviation.** A kill before the job record that names a spawned worker leaves a
  stdin anchor that `resume` cannot reconcile (exit 45) until the scenario ends the anchor; the approved
  plan's oracle for that variant does not hold, and no plan amendment was made.
- **Codex optional findings** (see
  `.ai-review/archive/workflow-controller-automated-lifecycle-scenarios/external-implementation-review-rev8-APPROVE.md`
  and the earlier rounds): O2 (`$WORKFLOW_CONTROLLER_SETTINGS` and some missing locations lack mount
  protection), O4 (the namespace watchdog permits `timeout + 15`; degraded workers lack an independent deadline
  after parent death), O6 (most negative checks accept generic assertion failures), O7 (registry tests import
  selftest scenario code on the host), O8 (ignored inputs, special Git index flags and external
  committed-symlink targets can evade provenance checks), O9 (flow id `index` collides with `index.json`) and
  O10 (configured Git clean filters can run during dirty checks).
- **Optional self-review items not applied:** extra pipe copies in `pass_fds`, unlisted real locations under a
  system directory, two small error-path leaks, stale records left in a reused `--output`, and the
  Git-metadata test's loose `update-ref` check.
- **Known limitations kept out of scope:** a mid-job usage limit is not covered by a scenario (admission only;
  C8b), and the two Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`) fail locally
  and are unrelated.

## Next action

1. Pull request #43 merges (a `test:` pull request, no release).
2. `/milestone-plan` for C7, the next step in `docs/ROADMAP.md`'s order (C7, C10, C12, C11, then O1-O7).
