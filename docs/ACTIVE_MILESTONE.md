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
- **Self-review (SELF_REVIEWING_IMPLEMENTATION).** No Blocking findings. Important: no test pinned that
  `_no_progress_gate` hands the advisories to `gate_for` (plan CP2's "warn plus the no-progress gate");
  `tests.test_protocol_job.LoopGuardTest` adds it. Wording: `docs/common-problems.md`'s `warn` entry no longer
  puts "For example" straight after "Upgrade to 1.7.1", where it read as an example of upgrading.

## Functional review checklist (`workflow-controller-protocol-warn-status`, Controller 1.7.1)

Use scratch copies only. Never touch `~/.config/workflow-controller/`, the shared pipx install or this
working tree's state. Findings go to `.ai-review/workflow-controller-protocol-warn-status/feedback/FUNCTIONAL_REVIEW.md`.

**Setup.**
```
S=$(mktemp -d)
git clone -q /home/rodrigo/Workspace/workflow-controller $S/ctl && git -C $S/ctl checkout -q milestone/workflow-controller-protocol-warn-status
git clone -q /home/rodrigo/Workspace/workflow-controller $S/repo     # the managed repository to drive
export XDG_CONFIG_HOME=$S/config HOME_SCRATCH=$S                      # keeps the user settings out of the test
C="env PYTHONPATH=$S/ctl python3 -m controller"                       # run from $S/ctl; no PYTHONPATH=. in tests
```
Test data: in `$S/repo`, update to Workflow 2.9.0 with `workflow-manager --release-version 2.9.0 update .` and commit.
Add and commit `docs/ai-workflow/GATE_POLICY.json` containing exactly `{"schema_version": 1, "human_approval": true}`
(an unadopted policy, which the real `verify` reports as `warn`). Keep the shared 1.7.0 install as the "before" control.

**Flow 1: a `warn` check is advisory.**
1. `$C explain $S/repo`. Expect exit 0 and a decision (next action `/milestone-implement` or the item's own), with
   `verify gate_policy: warn: ...` among the decision's evidence lines, in the Workflow's order.
2. Control: the shared 1.7.0 `workflow-controller explain $S/repo` exits 20 with "protocol verify gave an answer outside
   the protocol: ... 'warn' is not one of ['pass', 'fail', 'skip']".
3. `$C step $S/repo` (dry or with the usual scratch stubbing): not refused for the `warn`. The job record keeps the
   advisory line.
4. Make a check `fail` in the scratch repo (for example delete a managed script): `explain` shows the unhealthy gate
   listing the failing checks first, then the `warn` checks.
5. An undefined status (edit the scratch copy of the answer, or the stub, to say `"status": "maybe"`) still refuses, exit 20.
6. With a repeated no-progress step, the no-progress gate's evidence also carries the `verify ...: warn:` line.

**Flow 2: vendored Workflow 2.9.0 schema (protocol 1.2).**
1. `sha256sum $S/ctl/controller/protocol_schema.json $S/ctl/tests/protocol_schemas/workflow-2.9.0.schema.json`: the
   published copy matches the schema's recorded digest (`c203f2b2...`), and the vendored file is byte for byte the 2.9.0 one.
2. `python3 -m unittest tests.test_protocol_schema tests.test_protocol` in `$S/ctl`: OK.
3. The eight new action ids are catalogue members (grep `protocol_schema.json`); `$C inspect $S/repo` against the 2.9.0
   scratch repo prints no "does not know" advisory for them.

**Flow 3: `inspect` and `explain` advisory lines.**
1. `$C inspect $S/repo`: the advisory about unknown action ids names only the four ids this Controller cannot launch
   (`acceptance.satisfy`, `implementation.satisfy`, `plan.satisfy`, `pr.apply_review`) if the Workflow lists them, plus any
   invented id; it is silent for the four gates it never launches. `inspect --json` carries `unknown_action_ids`.
2. Add a made-up id to a stub `describe` answer in the scratch repo: it is listed; nothing is refused.
3. `$C explain $S/repo` lines read `verify <check>: warn: <detail>`; there is no `--work-item` flag (the active item resolves itself).

**Flow 4: documentation.**
1. `python3 tools/check_docs.py` in `$S/ctl`: exit 0; `python3 -m unittest tests.test_docs`: OK.
2. Read `docs/run.md` (Human gates), `docs/common-problems.md` (the `warn` entry and the lowered-gate entry),
   `docs/compatibility.md`, `docs/glossary.md`, `docs/guide/troubleshooting.md`. Each header reads "Controller 1.7.1;
   Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0". Each says 1.7.1 treats `warn` as advisory and 1.7.0 refuses with exit 20; an
   undefined status is still refused; links and anchors resolve (the run.md `#steps` link included).

**Known limitations / out of scope.** The Controller still cannot launch the four automatic action ids; it only advises.
No moving of this repository to Workflow 2.9.0 (a later step). Two tests fail locally only under Git 2.56
(`SquashReleaseNotesTest.test_a_trailer_paragraph_refuses_with_no_edit`, `TrailerRuleTest.test_a_bare_url_and_a_one_line_note_are_refused`;
ROADMAP follow-up 12). The last full run (3073 tests, those two failures) is in the bundle's `TEST_RESULTS.md`.
The release (1.7.1, tag, pipx install) is not part of this review.

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
