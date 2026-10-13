# Active Milestone

## Status

**Implementing.** `workflow-controller-codex-reviewer` (`docs/ROADMAP.md` step C7) runs the cross-model review
with a Codex reviewer the Controller starts itself, opt-in through `reviewer.harness`. Plan revision 15 was
approved by policy (commit `3d304d3`); the base commit is `dea2125`. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_REVIEWER_PLAN.md`.

| Checkpoint | State |
| --- | --- |
| CP1 settings rows, `record_external_result`, `rm -f` note | implemented, awaiting its commit |
| CP2 Codex harness | not started |
| CP3 review job | not started |
| CP4 lifecycle scenarios | not started |
| CP5 documentation and full suite | not started |

CP1 changes:
- `controller/settings.py`: the five `reviewer.*` rows (`harness`, `model`, `effort`, `timeout_seconds`,
  `binary`), `TABLE_GENERATION` 6. Nothing reads them yet; no `cli_flag` is wired (a flag with no reader would be
  dead, so CP3 adds any it needs).
- `controller/protocol.py`: `record_external_result` and `ExternalResult`, validated against the vendored schema;
  `not_applicable` and `refused` are returned, every other refusal raises.
- `controller/worker.py` and `tests/harness_contract/capture.py`: the `command rm -f` / `command cp -f` sentence
  in `WORKER_LIFECYCLE_NOTE` (follow-up 24). The recorded `*.meta.json` captures are historical recordings of
  runs made before the sentence and are left as recorded.
- `tests/golden/no_policy_lifecycle.json` regenerated; `tests/test_trunk_preflight.py` removes the reviewer rows
  and the added sentence and still matches the 1.5.0 base hash (R2).

**Open acceptance condition for CP1 (D2):** the Workflow side has not yet confirmed the obligation-5 reading. It
is an issue on the Workflow repository whose answer is recorded in ADR 0013; CP1 does not pass without it.

## Next action

Commit CP1, then invoke `/milestone-implement` again for CP2.
