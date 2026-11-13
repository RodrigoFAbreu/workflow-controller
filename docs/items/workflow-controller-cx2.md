# CX2: review follow-ups of the Codex worker item H4f

Item id: `workflow-controller-cx2`.

Not yet planned. Fix the open review follow-ups of the H4f item (the milestone-implement role in a private Codex clone). Source: [#344](https://github.com/RodrigoFAbreu/workflow-controller/issues/344). Complexity: low to medium. None is reachable today through the normal landing; all are defence in depth or missing operator guidance.

## Findings

- `gitrepo.filter_disabling_config` misses filters defined in `config.worktree` (confirmed on git 2.56): add a second `git config --worktree --includes --name-only --list -z` read, tolerating a repository without `extensions.worktreeConfig`, with a planted-driver test.
- State value legality: D4 limits which state fields a checkpoint may change, not their values (`phase`, `checkpoints` and the checkpoint ids); add a cheap pre-landing value check or document that legality is `reconcile`'s and what the operator does after an `invalid` ending.
- The probe and the surface check assume the managed repository ignores `.ai-review/`: name the assumption on the harnesses guide, or have the probe ignore `.ai-review/runtime/`.
- An absent amendment witness skips the Workflow's cross-worktree bootstrap conflict check: port the divergence refusal or name the case on the harnesses guide.

## Out of scope

The H4e items' follow-ups (CX1) and any new role routing.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
