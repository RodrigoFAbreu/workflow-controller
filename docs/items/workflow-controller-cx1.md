# CX1: review follow-ups of the Codex worker items H4e2, H4e3 and H4e3b

Item id: `workflow-controller-cx1`.

Fix the open review follow-ups of three accepted Codex worker items, real defects first. Sources: [#320](https://github.com/RodrigoFAbreu/workflow-controller/issues/320) (H4e2), [#339](https://github.com/RodrigoFAbreu/workflow-controller/issues/339) (H4e3), [#341](https://github.com/RodrigoFAbreu/workflow-controller/issues/341) (H4e3b). Complexity: medium. **Status: implemented on its milestone branch, awaiting review.** Plan: `docs/ai-workflow/CONTROLLER_CX1_PLAN.md`, four checkpoints. It adds no mechanism: every change tightens or words something that exists. Merging it closes #320, #339 and #341.

## What was built

- **#320.** The recorder's commit and every Workflow query run with `commit.gpgsign=false`, `tag.gpgsign=false` and `gpg.program=false`, so a target's `gpg.program` never runs (the real defect). A failed deletion of `last-message.md` is named once in the ending's evidence (`last_message_removal_error`). `after_check` compares the recorded input mode, refuses a symlinked `.ai-review`, lists ignored files and compares tracked paths entry by entry by type, executable bit and raw bytes (a CRLF/LF checkout difference passes). The `ident` and `working-tree-encoding` paths stay a declared false refusal, pinned by a test and worded on [common problems](../common-problems.md#a-codex-review-ended-read_only_violated-on-a-clone-the-worker-did-not-change). The two untested branches (a `.git` input component, a symlink replacing an input) have tests. A tracked file more than twice its blob's size is refused without being read (no conversion the comparison accepts can grow it more), so a worker's sparse giant is never read into memory, and the untracked and ignored paths come from `git ls-files --others`, which reads no content, in place of `status` (found at the self-review).
- **#341.** The guarded regeneration's `finished` block keeps `ownership_tag`. `_interrupted_publication` accepts, for the plan role only, just the two torn-manifest shapes (the implementation roles keep today's check). The generator's environment drops `BASH_FUNC_*`, `SHELLOPTS` and `BASHOPTS`. The `surface_landing_touched` gate text and the pages say that `explain` and every later Controller step also run the landed `scripts/`.
- **#339.** Documentation, as the issue offers: a refusal at the check writes nothing and `not_written` then names only the state file; an item whose artifacts file declares more plan-stage protected paths is refused and the operator applies its change by hand; a `run` past `carry_bundle_regeneration_pending` without regenerating is covered by one test. **Deviation from the plan's wording:** the plan expected that run to end as a `FAILED` review job. In the current Workflow the protocol answers first (`bundle_unverified`, or `bundle_generation_mismatch` for the implementation stage), so the job ends `GATE_BLOCKED` on that answer with no review launched and nothing recorded, which is stronger than the issue's claim. The test pins that, and the page says it and keeps the recorder's checks as the backstop.

Pages: [common problems](../common-problems.md) and [harnesses](../guide/harnesses.md).

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

## Planning notes

The plan is `docs/ai-workflow/CONTROLLER_CX1_PLAN.md`. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
