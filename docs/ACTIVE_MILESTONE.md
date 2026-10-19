# Active Milestone

## Status

**In progress.** `workflow-controller-round-caps` (C13b, `docs/ROADMAP.md`; controller issue
[#88](https://github.com/RodrigoFAbreu/workflow-controller/issues/88)) caps local and external review rounds
separately: local 6, external 4. Plan revision 3 was approved by policy at `513c873`; the base commit is `d6b9dfc`.
The plan is `docs/ai-workflow/CONTROLLER_ROUND_CAPS_PLAN.md`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of the phase and of each checkpoint.

| Checkpoint | State |
| --- | --- |
| CP1 The two settings and the options | complete: `review.local_round_cap` and `review.external_round_cap` (table generation 12), `--review-local-round-cap` and `--review-external-round-cap` on `run` and `step`; the no-policy golden regenerated for the two rows. Checked with `tests.test_settings`, `tests.test_trunk_preflight`, `tests.test_cli`, `tests.test_docs` and `tools/check_docs.py`. |
| CP2 The cap rule over split counts | complete: `split_rounds`, `effective_caps`, `split_cap_decision` and `EXTERNAL_REVIEW_ACTIONS` in `controller/review_rounds.py`; the fallback functions are untouched. Checked with `tests.test_review_rounds` and `tests.test_write_containment`. |
| CP3 The gate reads the rule | complete: `_review_cap_gate` takes `RoundCaps` and words the stop per D5 (stage, which cap fired, count, cap, where the counts came from); `external_review_cap_gate` is checked in `execute_step` before a review job and by `explain` at the same point; `cli._round_caps` builds the caps from the settings. Checked with the cli, review-rounds, evidence, protocol, review-job, scenario and lifecycle suites, the no-policy golden `--check` and `tools/check_docs.py`. |
| CP4 Documentation, roadmap and the whole suite | complete: the settings table, round-cap paragraph and generation 12 note (`docs/guide/runtime.md`), the run guide, common-problems entry, commands guide, compatibility note and exit-codes cross-reference describe the two caps; ROADMAP marks C13b complete and follow-up 55 fixed, and adds follow-ups 59 (retire `review.round_cap` after 1.17.0) and 60 (vendor Workflow 2.15.0). `tools/check_docs.py` and the no-policy golden `--check` pass. The whole suite (`tools/run_tests.py`, 4359 tests) has two failures, both the known Git 2.56 trailer-rule tests (`test_release_notes.TrailerRuleTest.test_a_bare_url_and_a_one_line_note_are_refused`, `test_pull_request_lifecycle.SquashReleaseNotesTest.test_a_trailer_paragraph_refuses_with_no_edit`); `generate_plan_stage_decisions.py --check` also differs, and does so identically on the base commit d6b9dfc. |

## Next action

Run `/milestone-implement workflow-controller-round-caps` for the next checkpoint (one checkpoint per invocation).
