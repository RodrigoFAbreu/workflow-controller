# Active Milestone

## In progress: `workflow-controller-protocol-warn-status` (ROADMAP step C9b)

Plan revision 2 approved. Implementing under Workflow 2.6.0, one checkpoint per invocation.
- **CP1 COMPLETE** -- Workflow 2.9.0's schema (protocol 1.2, sha256 `c203f2b2...`) vendored byte for byte with a
  published copy at `tests/protocol_schemas/workflow-2.9.0.schema.json`; the eight new action ids are catalogue
  members; `protocol_decision.UNLAUNCHED_AUTOMATIC_ACTION_IDS` keeps `inspect` advising about the four automatic ids
  this Controller cannot launch. Verified: `tests.test_protocol_schema`, `tests.test_protocol`,
  `tests.test_protocol_decision`, `tests.test_cli`, `tests.test_protocol_lifecycle`, `tests.test_docs` pass.
- **CP2 COMPLETE** -- a `warn` check is advisory. `Verify.warnings`; `protocol_decision.preflight` returns
  `(gate, advisories)` with `health_gate` as a wrapper; `decide_after_preflight` is the one call `explain` and
  `job._decide_protocol_current` make; an `advisories` keyword on `decide`/`from_answer`/`gate_for`,
  `_unstable_gate` and `_no_progress_gate` puts the `verify <id>: warn: <detail>` lines in every standing
  decision's evidence. The unhealthy gate lists the `warn` checks after the failing ones. An unknown status
  still refuses (exit 20). Verified: the CP2 acceptance modules (398 tests), `tests.test_protocol_equivalence`,
  `tests.test_observation_equivalence`, `tests.test_write_containment` and `tests.test_docs` pass.
- **CP3 COMPLETE** -- documentation says what 1.7.1 does. Edited: `docs/run.md`, `docs/common-problems.md`,
  `docs/compatibility.md` (2.9.0 and 1.7.1), `docs/glossary.md`, `docs/guide/troubleshooting.md`; their headers and
  `tests/test_docs.py` HEADER read "Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0". D1's wording notes
  applied ("some causes"; `warn` beside the not-admitted exit 20; the adoption-timing advice linked).
  `docs/guide/commands.md` and `docs/guide/automation.md` agree with D5 and are unchanged.
  **Real-release check (2026-10-09).** Scratch `git clone` of this branch (1431d15), updated to Workflow 2.9.0 with
  `workflow-manager --release-version 2.9.0 update .`, plus a committed, unadopted
  `docs/ai-workflow/GATE_POLICY.json` containing exactly `{"schema_version": 1, "human_approval": true}`. The real
  `verify` (protocol 1.2) answered `healthy: true` with `gate_policy` status `warn`, detail "...is not adopted and
  tightens the policy; it takes effect now ...". `explain .` (the plan's `--work-item` flag does not exist; the
  active item resolves by itself): (a) this branch exited 0 and showed `verify gate_policy: warn: ...` in the
  decision's evidence, next action `/milestone-implement`; (b) the shared 1.7.0 install exited 20 with
  "protocol verify gave an answer outside the protocol: $.result.checks[7].status: 'warn' is not one of ['pass',
  'fail', 'skip']".

The sections below describe the previous milestone and are superseded as CP3 lands.

## Status

**Complete.** `workflow-controller-documentation-reorganisation` (`docs/ROADMAP.md` step D1, section
11.8) reached `MILESTONE_COMPLETE` on 2026-10-09. The documentation is reorganised:
- `README.md` and `docs/README.md` are the entry points;
- three task pages: `docs/install.md`, `docs/run.md` and `docs/update.md`;
- five reference pages: `docs/compatibility.md`, `docs/common-problems.md`, `docs/exit-codes.md`,
  `docs/glossary.md` and `docs/release-history.md`;
- trimmed guides under `docs/guide/`;
- two offline documentation checks run in CI (`tools/check_docs.py`, `tests/test_docs.py`).

No Controller behaviour changed.

The user read the pages and accepted the milestone after functional review round 7 (implementation
revision 20). That round was checked against evidence commit `12695902a5065c4c6f7aeb7a51de6d00a2502c98` and came back clean.
- Plan revision 5 was approved at `5d6bcc3`. The base commit is `914d8f4`.
- Technical approval `3532b11` is `CURRENT`. It is an `EXTERNAL_APPROVE` of bundle `9c8e3b6e`, with
  review content id `99f6be0c` and reviewed implementation head `42df66e`.
- Functional review rounds 1 to 6 each found reader-facing problems. Each was fixed as a bounded
  documentation change and reviewed again by both implementation-review stages. The later rounds
  covered the Workflow 2.8.0 gates: adopting a gate policy, the evidence gates and the `warn`
  refusal.

All registry checkpoints are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth
record of this transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section
11.8 and step D1 of "At a glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-documentation-reorganisation.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge.** The milestone branch is on PR #25, titled
  `docs: reorganise the guides, add task pages and two offline documentation checks`. A `docs:`
  pull request releases nothing. Once every check passes, the PR is squash-merged without editing
  the commit message.
- **The post-D1 docs pull request.** It holds:
  - the optional wording notes from functional review round 7 and the last external review;
  - a roadmap entry for accepting the protocol's `warn` check status;
  - the follow-ups noted during D1: the Git 2.56 trailer rule (two tests fail locally only), and
    the selection of a dormant legacy work item.
- **Moving this repository to Workflow 2.9.0, with automatic gates**, after C9b (see Next action).
  Controller 1.7.0 admits 2.9.0, but refuses whenever `verify` reports `warn`.
- Carried over, unchanged: the deferred items of the earlier milestones, listed in their
  acceptance commits.

## Next action

`/milestone-plan` for step C9b (`docs/ROADMAP.md`, "At a glance" and follow-up 11). It accepts the
protocol's `warn` check status as advisory, vendors the Workflow 2.9.0 schema (protocol 1.2) and
documents 2.9.0, and is released as a patch. The user chose this order on 2026-10-09:
1. C9b, under Workflow 2.6.0 with human gates;
2. then this repository moves to Workflow 2.9.0, with automatic gates;
3. then C8 (usage budget, section 11.6), followed by C5, C6, C7, C10 and C11.
