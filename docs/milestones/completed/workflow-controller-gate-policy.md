# C10a: gate-policy validations (`workflow-controller-gate-policy`)

This is the record of milestone `workflow-controller-gate-policy` (`docs/ROADMAP.md` step C10a, the first of the
three gate-policy steps), archived when it was accepted on 2026-10-17. Plan:
`docs/ai-workflow/CONTROLLER_GATE_POLICY_PLAN.md`, revision 5, approved by policy at `1729f63`; base commit `07d0d6d`.
Pull request #81, title `feat: launch the Workflow's gate-policy validations so a satisfied gate passes without a
person`. The sections up to "Known limitations and out of scope" are the active-milestone page as it stood during
the functional review; the later sections were added at acceptance.

## Scope

Under automatic gates the Workflow answers rows 14a, 28a and 38h with the disposition `validation` and the action ids
`plan.satisfy`, `implementation.satisfy` and `acceptance.satisfy`. Before C10a the Controller could not launch them,
so every automatic gate still stopped for a person. C10a launches the Workflow's `/satisfy-gate` for all three, stops
a run whose validation is refused, accepts a value a protocol 1.x minor release adds (advisory or blocked, never
refused), and sends a blocker, not a request, when the Workflow calls a stop blocked. It closes follow-up 17 and part
of follow-up 26. Rows 38d-38g (the pull request defect loop and the automated functional review) are left to C10b and
C10c.

## Implementation

Plan revision 5 is approved (`1729f63`). Implementation is complete (CP1-CP5) and technical approval is recorded
(`cfc8b55`, implementation revision 6).
- **CP1 complete** (`e70715f`): the `satisfy-gate` routing role, the three `.satisfy` action ids and the strict pairing
  of a `validation` answer with them.
- **CP2 complete**: a satisfied gate passes in a run and a refused one stops. `approval_recorded` is sent only when
  the phase moved. Four lifecycle scenarios run on the vendored Workflow 2.12.0: rows 14a, 28a and 38h, the
  no-progress stop, the 38f stop and the 38d non-recovery.
- **CP3 complete**: a rule per enum. `protocol_schema.ENUM_RULES` names all twenty locations and `OPEN_ENUMS` is derived
  from it. A verify status a minor release adds is an advisory line, and an unlisted reconcile class or record stage
  ends the job naming the value as `workflow_unknown_value`. Measured differences from plan D6: the error code is
  `blocked`, `satisfied_by` is `advisory`, and the functional-evidence status, verdict and slot are `ignored`, since the
  Controller reads no decision from them.
- **CP4 complete**: a gate the Workflow answers with the disposition `blocked` sends `lane_blocked`; human and external
  gates keep `user_gate`.
- **CP5 complete**: the roadmap rows C10a-C10c and follow-ups 17 and 26, the guides, the compatibility and problem
  pages, and ADR 0015. `check_docs` passes and the whole suite ran 4,205 tests with exact coverage, only the two known
  Git 2.56 trailer tests failing; `plan_stage_decisions.json --check` differs at the pre-milestone base too.
- **Self-review** (the whole milestone diff): `tests/test_protocol_lifecycle.py` defined three of the CP2 scenarios twice
  in `SatisfyGateLifecycleTest`, so the first copies never ran; the duplicates were removed (no test lost, and a scan
  finds no other duplicate method). The automation guide and ADR 0015 called an unlisted reconcile class or record stage
  a gate; they now say the job ends `FAILED` (`workflow_protocol_failed`) with the evidence reason
  `workflow_unknown_value`, and only a value read before a launch is a blocked gate. `derive_job_event`'s docstring names
  the `lane_blocked` case. The plan's Risks promised CP2's scenario under a milestone-branch binding, and none ran with
  one: the route test now pins the validator's tools under a binding (the sub-agent and branch-guard tools disallowed,
  `git commit` not); the post-step branch check is the same for every role.

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

## Acceptance

Accepted **by gate policy, not by a person**, on 2026-10-17: `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`). Every checkpoint (CP1-CP5) is `COMPLETE`, the
technical approval `cfc8b55` (implementation revision 6) is `CURRENT`, flows flow-1 to flow-9 passed, and the
Workflow's own GitHub query found pull request #81 at head `396e18d` with every check passing and no standing
objection. The pull request title is `feat:`, so it releases the minor release 1.14.0 when it merges. No
`docs/releases/1.14.0.md` was written by the milestone; the release notes come in the docs pull request after the
release.

## Review history

- **Plan.** Revision 5 was approved by policy at `1729f63` after two Codex plan rounds. Round 1 (revision 3)
  `REVISE`: the artifact declaration left `controller/`, `tests/` and the docs unclassified (also filed as Workflow
  issue [RodrigoFAbreu/workflow#40](https://github.com/RodrigoFAbreu/workflow/issues/40)), `ENUM_RULES` would have
  closed the seven enums that were already open, and a fresh `pr_review_result` recovery would have hit the
  unlaunchable row 38d. Round 2 (revision 5) `APPROVE`. A local review ran before each. The plan stage reached the
  round cap of 4, and the owner raised the cap to 6 for C10a only.
- **Implementation.** Technical approval `cfc8b55` (implementation revision 6) after four Codex implementation
  rounds. Round 1 (revision 2) `REVISE`: the lifecycle scenario used human approvals instead of the policy builders,
  and `explain` did not show a refused validation's unmet requirement. Round 2 (revision 3) `REVISE`: the requirement
  was lost after a long report preamble and after the first refusal. Round 3 (revision 4) `REVISE`: it was lost when
  the preamble said "unmet" early or the report had no marker. Round 4 (revision 6) `APPROVE`, at the raised cap of 6.
  A local review ran before each Codex round. The lane installed Controller 1.13.1 in the shared pipx at the first
  external-review gate on 2026-10-17.
- **Functional.** Round 1, an independent Codex tester with fakes only: flows 1-5 and 7 passed; flows 6, 8 and 9 were
  blocked by the tester's sandbox (sockets denied, a separate PID namespace, no network); no findings. Report:
  `.ai-review/archive/workflow-controller-gate-policy/functional-tester-round1-RESULTS.md`. Round 2, an operator
  re-test outside the sandbox (`functional-review-round2-PASSED.md`), passed flows 6, 8 and 9: the 157 notification
  and settings tests pass; three golden generators pass with `--check`, and `plan_stage_decisions` differs on the base
  commit too (the known difference); the whole suite ran 4,207 tests with exact coverage, and only the two known Git
  2.56 trailer tests and the load flake #82 failed. Checklist evidence commit `396e18d`.

## Minor review findings

Every Optional finding of the milestone's reviews, with its disposition (owner rule, 2026-10-16: acceptance never
waits for a fix). Follow-ups are GitHub issues labelled `review-follow-up` and are worked off in clean-up
milestones (CU2 next). Sources are under `.ai-review/archive/workflow-controller-gate-policy/` (`L-` is
`local/local-review-`); the round-1 local implementation verdict was not archived, so its three Optional findings
come from the round-2 verdict's Summary and O3.

| # | Source | Finding | Disposition | Justification |
| --- | --- | --- | --- | --- |
| 1 | L-before-codex-plan-rev3 O1 | CP2 scenario did not run the technical-approval leg (row 28a) in a `run` | fixed | `tests/test_protocol_lifecycle.py:621-635` runs `implementation.satisfy` in the run and counts 3 `approval_recorded` (2b79c22, 5b35f07) |
| 2 | L-before-codex-plan-rev3 O2 | Deriving `OPEN_ENUMS` from `ENUM_RULES` would close the 7 enums that were already open | fixed | `ENUM_RULES` classifies every enum and `PREVIOUSLY_OPEN_ENUMS` pins the 7 (`controller/protocol_schema.py:56-85`, `tests/test_protocol_schema.py:230,238-239`; 070409f) |
| 3 | external-plan-review-rev3 O1 | Row 38e belongs to C10c, not C10b | fixed | Plan D7 (`docs/ai-workflow/CONTROLLER_GATE_POLICY_PLAN.md:28`, approved 1729f63) and `docs/ROADMAP.md:80` give 38e to C10c (a6daf2c) |
| 4 | L-before-codex-plan-rev5 O1 | CP1's artifact sentence does not list `docs/adr/` | dropped | Wording only: the declarations already covered `docs/adr/` and ADR 0015 landed under them (a6daf2c); approved plans are not edited afterwards |
| 5 | L-before-codex-plan-rev5 O2 | The direct `satisfy_acceptance_gate` call runs in the test process, so a fake `gh` on the worker's PATH is not enough | fixed | The fixture passes `run=`/`resolve=` (`tests/test_protocol_lifecycle.py:478-496`, 2b79c22); the summary records this as a deviation |
| 6 | Impl round 1 O1 (per L-before-codex-impl-rev2 Summary) | `docs/run.md` gave no by-hand remedy once the checks turn green | fixed | `docs/run.md:59` names `/satisfy-gate acceptance <id>` (590835b) |
| 7 | Impl round 1 O2 (per L-before-codex-impl-rev2 Summary) | Summary's "Known limitations" did not list the CP3 departure | fixed | The summary now lists it (unknown reconcile class ends the job `FAILED`). It is a gitignored bundle file, so there is no commit to cite |
| 8 | Impl round 1 O3 = L-before-codex-impl-rev2 O3 | The `/satisfy-gate` `gate_reached` notification case is pinned on a hand-made record, not on a real 2.12.0 answer | dropped | The event checks whether the phase moved, not the class (`controller/job.py:5058`). The hand-made test covers moved + `gate_reached` (`tests/test_notifications_wiring.py:72-89`). Real 2.12.0 runs cover the three approvals (classed `progress`, measured with a scratch probe) and the refused `gate_reached` acceptance (`tests/test_protocol_lifecycle.py:702-714`) |
| 9 | L-before-codex-impl-rev2 O1, L-impl-rev5-REVISE O3 | `TEST_RESULTS.md` still says "revision 1" in its heading and has no rev-2 or rev-5 section; the summary has no round-1 section | dropped | Gitignored bundle files, rebuilt each milestone. No evidence is missing: the summary records the rev-5 full run and `TEST_RESULTS.md` has a rev-6 section |
| 10 | L-before-codex-impl-rev4 O3 | The rev-4 full run used `unittest discover`, not `tools/run_tests.py` | dropped | Later rounds used `tools/run_tests.py`, which showed exact coverage (4207 tests planned, all ran; rev-6 section). Bundle file only |
| 11 | L-before-codex-impl-rev2 O2 | A `validation` launch's evidence line says `(automatic)`, not `(validation)` | follow-up | [#83](https://github.com/RodrigoFAbreu/workflow-controller/issues/83) |
| 12 | L-before-codex-impl-rev3 O1, L-before-codex-impl-rev4 O1 | The report cut keeps the start of the text, then cuts at the first "unmet", so a long preamble hides the requirement | fixed | `_worker_report` now cuts from the last `unmet:`, else from the named requirement (`controller/job.py:5809-5831`); three report shapes tested (`tests/test_protocol_lifecycle.py:651-665`; b4fc2be, 8a28f2f) |
| 13 | L-impl-rev5-REVISE O2, L-before-codex-impl-rev6 O1, external-implementation-review-rev6 O1 | A later `unmet:` after the requirement list makes the cut drop the list | follow-up | [#84](https://github.com/RodrigoFAbreu/workflow-controller/issues/84) (also `bug`). Reproduced at HEAD: the report shrinks to `'unmet: see above.'` |
| 14 | L-before-codex-impl-rev3 O2 | The Controller 1.7.1 sentence in `docs/compatibility.md` still lists the three `.satisfy` ids as ones it cannot launch, without saying later releases launch them | follow-up | [#85](https://github.com/RodrigoFAbreu/workflow-controller/issues/85). Still present at `docs/compatibility.md:34-36` |
| 15 | L-before-codex-impl-rev4 O2 | `_protocol_job_class` docstring describes 4 returned values; the function returns 5 | follow-up | [#86](https://github.com/RodrigoFAbreu/workflow-controller/issues/86). Still present at `controller/job.py:5775-5783` |
| 16 | L-impl-rev5-REVISE O1 | `_POLICY_REQUIREMENTS` is a hand copy of the Workflow's `REQUIRE_VALUES` | fixed | A comment names `REQUIRE_VALUES` and says to keep the two in step (`controller/job.py:5804-5806`, d141a7d). The finding offered a comment or a test; no comparison test was added |
| 17 | L-before-codex-impl-rev6 O2 | `docs/guide/automation.md` does not say that once the loop guard applies, JSON has no `last_worker_report` (the report is in `gate.what_is_required`) | follow-up | [#87](https://github.com/RodrigoFAbreu/workflow-controller/issues/87). Still present at `docs/guide/automation.md:387-390`. `controller/cli.py:1091-1093` adds the field only while the decision is automatic |

The functional review raised no findings. One defect was found while running, not in a review:
[#82](https://github.com/RodrigoFAbreu/workflow-controller/issues/82) (label `bug`), a test
(`test_killed_and_broken_pipe_followers_leave_the_worker_and_exit_code_alone`) that flakes with exit -14 (`SIGALRM`)
under load. It is test only, and it is filed for CU2.
