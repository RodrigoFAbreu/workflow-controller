# CX1: review follow-ups of the Codex worker items H4e2, H4e3 and H4e3b

Item id: `workflow-controller-cx1`.

Not yet planned. Fix the open review follow-ups of three accepted Codex worker items, real defects first. Sources: [#320](https://github.com/RodrigoFAbreu/workflow-controller/issues/320) (H4e2), [#339](https://github.com/RodrigoFAbreu/workflow-controller/issues/339) (H4e3), [#341](https://github.com/RodrigoFAbreu/workflow-controller/issues/341) (H4e3b). Complexity: medium; if the plan needs more than one mechanism, split by issue before its first review.

## Findings

**#320 (H4e2).**
- The recorder's commit can run a `gpg.program` from the target repository's configuration: run it with signing disabled and a clean config (a real defect).
- A failure to delete `last-message.md` is swallowed.
- Two branches are untested.
- `after_check` gaps: file mode not compared; ignored files and a symlinked `.ai-review` pass; a false refusal on attribute-normalised files.

**#339 (H4e3).**
- `codex_carry.not_written` at the check lists only the state file, not the plan or registry members; fill it from the planned writes, or reword the page.
- Extra `plan_stage.protected_paths` declared in an item's artifacts file are refused (`carry_foreign_dirt`, `carry_out_of_set`); add them to the named files (read from the managed artifacts file) or document it.
- Running again past the `carry_bundle_regeneration_pending` gate without regenerating gives a `FAILED` review job, not the gate; say so on the common-problems page and consider a test.

**#341 (H4e3b).**
- The `finished` block drops `ownership_tag` (a resume might miss a descendant that outlived the generator).
- `_interrupted_publication` accepts any `reconcile_invalid`; narrow it to the two manifest states.
- Exported bash functions (`BASH_FUNC_*%%`) are not removed from the generator's environment; drop them and add a poisoned-environment test.
- The `surface_landing_touched` gate text and `docs/common-problems.md` should warn that the landed `scripts/` also run on `explain` and later Controller steps, so the operator restores or verifies `scripts/` and `.workflow-manager/` first.

## Out of scope

H4f's follow-ups (CX2), routing any further role, and new Codex worker features.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
