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

## Functional review checklist

All four checkpoints are complete; the implementation review is approved. Test the behaviour below, then write
findings to `.ai-review/workflow-controller-round-caps/feedback/FUNCTIONAL_REVIEW.md`. Use a scratch repository and a
scratch settings file, never your real Controller state (`$C` is this checkout).

**Setup.** `export PYTHONPATH=$C`, then run `python3 -P -m controller ...` from the branch
`milestone/workflow-controller-round-caps`. Use a throwaway `--runtime-dir`/home for the settings file (or
`settings path` to see which file is read) so the fill does not touch your own. No feature flags or seed data are needed.

**Flows and expected results.**

1. **Defaults.** `settings show` -> `review.local_round_cap` 6 and `review.external_round_cap` 4, source `default`;
   `review.round_cap` 4, its row marked deprecated ("retired after 1.17.0").
2. **Settings file fill.** Run any command that fills the file from an older generation file (generation 11) ->
   generation becomes 12 and exactly the two new rows are added; nothing else moves.
3. **Options and bounds.** `step --help` and `run --help` list `--review-local-round-cap` and
   `--review-external-round-cap`; the deprecated `--review-round-cap` help says it sets both. `0` and `101` are refused
   for each option; `1` and `100` are accepted.
4. **Precedence.** With `review.round_cap: 7` in the file and nothing else set, `settings show` (or `explain`) gives
   local 7 and external 7; adding `review.local_round_cap: 3` leaves external 7. `--review-round-cap 5` on the command
   sets both for that command; a specific option beats it. A file value of exactly 4 for `review.round_cap` does NOT
   override (local stays 6) -- documented limitation.
5. **Split-count stop (apply).** On a work item whose Workflow (2.15.0, protocol 1.5) reports `plan_local`/`plan_external`
   counts: local 6 and external 1 -> `step` stops `GATE_BLOCKED`, exit 10, reason `review_round_cap_reached`, text names
   the stage, `local`, count 6, cap 6, "given by the Workflow as split counts". Local 2 and external 4 -> stops naming
   the `external` cap. Local 5 and external 3 -> does not stop.
6. **External review stop.** With a configured reviewer (`reviewer.harness` set, review mode not `manual`) and an
   external count of 4 -> the cap gate stops before any review job (no job record, no usage reservation, no harness
   call); `explain` shows the same gate. External 3 launches the review. With `review.mode` `manual` or no reviewer the
   ordinary external gate stands instead.
7. **Fallback.** Against an older Workflow with no split counts (e.g. the vendored 2.12.0): behaviour is as before, the
   single round is checked against the external cap, and the gate text says "the Workflow gave no split counts" and
   names the single number and its source (`given by the Workflow` or `counted from the Controller's job records`).
8. **Way on.** `run --review-external-round-cap 6` (or `--review-local-round-cap`) continues past the stop for that
   command only; the next plain `run` stops again.
9. **One record, one notification.** Each stop writes one `GATE_BLOCKED` record and sends one owner notification, exit
   code 10 as before; `telemetry`'s round column keeps the single per-stage number.
10. **Docs.** `docs/run.md#the-review-round-cap`, `docs/common-problems.md#a-run-stopped-at-the-review-round-cap`,
    `docs/guide/runtime.md` (settings table, generation 12), `docs/guide/commands.md`, `docs/compatibility.md` and
    `docs/exit-codes.md` read correctly and match flows 1-9; `python3 tools/check_docs.py` passes.

**Known limitations / out of scope.**

- The split case is exercised end to end only at the job level (injected snapshots); the lifecycle tests stay on the
  fallback because Workflow 2.15.0 is not vendored yet (ROADMAP follow-up 60).
- `review.round_cap` and `--review-round-cap` are deprecated, retired after 1.17.0 (follow-up 59); a file value of
  exactly 4 cannot be told from the default.
- Counts are recorded verdicts: a `BLOCK`, a manual approval or a gate-policy satisfaction records no round.
- The whole suite shows two known Git 2.56 trailer-rule failures and a `generate_plan_stage_decisions.py --check` diff,
  identical on base `d6b9dfc`; not part of this milestone.
- No change to `telemetry`, the exit code, the reason code or the Workflow itself.
