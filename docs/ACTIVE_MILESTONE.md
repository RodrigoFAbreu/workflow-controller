# Active Milestone

## In progress

`workflow-controller-gate-policy` (`docs/ROADMAP.md` step C10a, plan revision 5, plan approved by policy at `1729f63`)
is being implemented on branch `milestone/workflow-controller-gate-policy`. Checkpoints complete: **CP1** (the
`satisfy-gate` role, the three `.satisfy` action ids and the strict pairing, `e70715f`) and **CP2** (a satisfied gate
passes in a run and a refused one stops: `approval_recorded` only when the phase moved, and four lifecycle scenarios on
the vendored Workflow 2.12.0 -- rows 14a, 28a, 38h, the no-progress stop, the 38f stop and the 38d non-recovery).
**CP3** (a rule per enum: `protocol_schema.ENUM_RULES` names all twenty locations, `OPEN_ENUMS` is derived from it,
a verify status a minor release adds is an advisory line, and an unlisted reconcile class or record stage ends naming
the value as `workflow_unknown_value`; measured differences from plan D6: the error code is `blocked`, `satisfied_by`
is `advisory`, and the functional-evidence status, verdict and slot are `ignored`, since the Controller reads no
decision from them). **CP4** (a gate the Workflow answers with the disposition `blocked` sends `lane_blocked`; human and external
gates keep `user_gate`). **CP5** (the roadmap rows C10a-C10c and follow-ups 17 and 26, the guides, the compatibility and problem pages, and ADR 0015; `check_docs` passes and the whole suite ran 4,205 tests with exact coverage, only the two known Git 2.56 trailer tests failing; `plan_stage_decisions.json --check` differs at the pre-milestone base too). All checkpoints are complete. **Self-review** (the whole milestone diff): `tests/test_protocol_lifecycle.py` defined three of the CP2 scenarios twice in `SatisfyGateLifecycleTest`, so the first copies never ran; the duplicates are removed (no test lost, a scan finds no other duplicate method). The automation guide and ADR 0015 called an unlisted reconcile class or record stage a gate; they now say what happens: the job ends `FAILED` (`workflow_protocol_failed`) with the evidence reason `workflow_unknown_value`, and only a value read before a launch is a blocked gate. `derive_job_event`'s docstring names the `lane_blocked` case. The plan's Risks promised CP2's scenario under a milestone-branch binding; none runs with one. The route test now pins the validator's tools under a binding (the sub-agent and branch-guard tools disallowed, `git commit` not); the post-step branch check is the same for every role. The record of the previous milestone follows unchanged.

## Status

**Complete.** `workflow-controller-cleanup-1` (`docs/ROADMAP.md` step CU1, the first clean-up milestone) reached
`MILESTONE_COMPLETE` on 2026-10-17. It fixed the sixteen GitHub issues labelled `review-follow-up` or `bug` (#55-#59,
#62-#67 and #71-#75): documentation and `check_docs` rules, gaps in the test-leftover guard and the usage-limit
tests, two flaky worker tests, the `"1"` plan round count, a full model id for the Claude reviewer, the review's own
harness named in recovery messages, one-pass `telemetry` rounds with a Claude review's cost and tokens kept, and
Workflow 2.12.0 vendored for the tests. Its `fix:` pull request (#77) merged on 2026-10-17 (squash `f46999d`) and
closed the sixteen issues, and it is released as the patch release **1.13.1**. The release notes are
`docs/releases/1.13.1.md`.

The milestone was accepted **by gate policy, not by a person**. `/satisfy-gate acceptance` found every
acceptance requirement met (policy source `default`, digest `bae758b4f4ce`).
- Plan revision 3 was approved by policy at `4d29850` after two Codex plan rounds (`REVISE` on the scope of the
  step-id rule, then `APPROVE`). The base commit is `f60250f`.
- Technical approval `08cffc8` (implementation revision 4) was recorded by policy and is `CURRENT`, after three
  Codex implementation rounds inside the round cap of 4 (`REVISE` with I1-I3, `REVISE`, then `APPROVE`).
- Functional review round 1 (an independent Codex tester with fakes only) passed flows 1-7 and raised one Optional
  documentation finding on flow 9, fixed by `4faea2a`; flows 8 and 10 were blocked by its sandbox. Round 2, an
  operator re-test outside the sandbox, passed flows 8-10 (the whole suite: 4,183 tests, exact coverage, only the
  two known Git 2.56 trailer tests failing). Checklist evidence commit `5e6b90a`.
- The Workflow's own GitHub query found pull request #77 at head `5e6b90a` with every check passing and no
  standing objection.

All registry checkpoints (CP1-CP6) are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record
of this transition, including its ten functional evidence records and its `acceptance_satisfaction` evidence, and
`active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step CU1 complete. The full milestone narrative is
archived at `docs/milestones/completed/workflow-controller-cleanup-1.md`, with the issues it fixes, the review
history and every minor review finding's disposition; the functional reports are
`.ai-review/archive/workflow-controller-cleanup-1/functional-tester-round1-RESULTS.md` and
`functional-review-round2-PASSED.md`.

Deferred follow-ups, not conditions of acceptance:
- **Minor review findings:** of eleven Optional review items, six were fixed, three dropped and two kept as
  follow-ups, GitHub issues #78 and #79 (label `review-follow-up`), worked off in CU2.
- **Known limitations kept out of scope:** a real Claude review consumes account usage, so the flows used fake
  reviewers only. The two Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`) fail locally
  and are unrelated; the bare 2.5.1 `plan_stage_decisions` golden differs on the base commit as well.

## Next action

`/milestone-plan` for C10, gate policy (`docs/ROADMAP.md`, "Next, in order"). After it come C12, C11 and the
orchestrator series O1-O7; CU2, the next clean-up milestone (#78 and #79 so far), slots in every few milestones or
sooner when the issue count grows.

## Functional review checklist

Scope: implementation revision 6 (technical approval recorded at `cfc8b55`; the fixes of revisions 1-5 are included).
Run every flow in a scratch checkout or scratch repository, never in this one, and never against the real `origin` or
`gh` (use a scratch bare `origin` and a fake `gh` on `PATH`). A real `/satisfy-gate` worker consumes account usage, so
the flows use fake workers only.

### Setup

- Run from this checkout with `PYTHONPATH=<this checkout>` (`python3 -m controller ...`, see `docs/guide/development.md`).
  Leave `FORCE_COLOR` unset. Pass `--routing-config` before the subcommand if a routing file is used.
- Test data: the lifecycle fixtures of `tests/test_protocol_lifecycle.py` (`SatisfyGateLifecycleTest`) build a scratch
  repository on the vendored Workflow 2.12.0 (`tests/workflow_releases/2.12.0`) with fake workers, a fake `gh` and a
  scratch bare origin.
- Known unrelated local failures: the two Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`)
  and the bare 2.5.1 `plan_stage_decisions.json --check` difference.

### Flows and expected results

1. **Satisfy-gate launches (CP1).** Under automatic gates, real 2.12.0 answers for rows 14a, 28a and 38h launch
   `/satisfy-gate plan|implementation|acceptance <id>` on the new `satisfy-gate` role (Opus, `xhigh`; the `Skill`,
   `Agent` and `Workflow` tools disallowed). A `validation` answer for another action id, a `user_only` worker, a role
   other than `validator`, a changed invocation or an unknown id is blocked with its code and launches nothing;
   `pr.apply_review` is still blocked as unlaunchable. `--role-model satisfy-gate=...` and the settings file accept the role.
2. **A satisfied gate passes (CP2).** `python3 -m unittest tests.test_protocol_lifecycle` (the `SatisfyGateLifecycleTest`
   scenarios): a run goes from plan review to `IMPLEMENTING` and from functional evidence to `MILESTONE_COMPLETE` with no
   stop; `approval_recorded` is sent only when the phase moved (also for a step `reconcile` classes `gate_reached`), and
   `milestone_accepted` for acceptance.
3. **A refused gate stops (CP2).** A fake validator that refuses leaves the phase unchanged, sends `step_done` only, and
   the run stops after two refusals with `no_progress_repeated`; `explain` names the unmet requirement (including after
   the first refusal and past a long preamble).
4. **Row 38f / 38d stops (CP2, D7).** With the fake `gh` reporting pending checks, acceptance stops at row 38f with
   `user_gate`; turning the checks green changes nothing until `satisfy_acceptance_gate` is run by hand; a reported green
   `pr_review_result` leads to row 38d, blocked as unlaunchable, `user_gate`. `docs/guide/runtime.md` says so.
5. **Open enums (CP3).** `protocol_schema.ENUM_RULES` names all twenty locations; a document with a value a minor release
   adds is accepted (advisory line for a verify status; step ends in a gate naming `workflow_unknown_value` for a reconcile
   class or record stage), a protocol major change is still refused (exit 20), and the seven earlier exemptions stay open.
6. **Blocker vs request (CP4).** A gate whose Workflow disposition is `blocked` sends `lane_blocked` ("Blocked: ...");
   `human_gate`, `external_gate`, a legacy target and the Controller's own gates keep `user_gate`; re-deriving from the
   job record gives the same identity.
7. **Documents (CP5).** `python3 tools/check_docs.py` exits 0; `docs/ROADMAP.md` shows C10a/C10b/C10c, follow-up 17 done,
   follow-up 26 partly done; guides (`commands`, `runtime`, `automation`, `how-it-works`, `troubleshooting`), `compatibility`,
   `common-problems`, `run` and ADR 0015 agree with the behaviour and keep no "until C10" sentence.
8. **Settings and golden.** The settings table generation is 9 and lists `satisfy-gate`; the golden generators pass with
   `--check` (never run one without it).
9. **Whole suite.** `python3 tools/run_tests.py` finishes with exact coverage and only the known failures above.

### Known limitations and out of scope

- Rows 38e-38g and 38d under an automatic gate still stop with `user_gate`; C10b (`pr.apply_review`, 38d/38f/38g) and
  C10c (automated functional review, 38e) own them.
- The Controller's own vendored schema is not re-vendored to 2.12.0 here; the fourteenth enum (`review_round.current`)
  is classified by the step that moves it.
- A real `/satisfy-gate` worker and a real GitHub query are not exercised.
