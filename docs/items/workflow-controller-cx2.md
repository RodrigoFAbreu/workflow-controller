# CX2: review follow-ups of the Codex worker item H4f

Item id: `workflow-controller-cx2`.

Fix the open review follow-ups of the H4f item (the milestone-implement role in a private Codex clone). Source: [#344](https://github.com/RodrigoFAbreu/workflow-controller/issues/344). Complexity: low to medium. None is reachable today through the normal landing; all are defence in depth or missing operator guidance.

## Findings

- `gitrepo.filter_disabling_config` misses filters defined in `config.worktree` (confirmed on git 2.56): add a second `git config --worktree --includes --name-only --list -z` read, tolerating a repository without `extensions.worktreeConfig`, with a planted-driver test.
- State value legality: D4 limits which state fields a checkpoint may change, not their values (`phase`, `checkpoints` and the checkpoint ids); add a cheap pre-landing value check or document that legality is `reconcile`'s and what the operator does after an `invalid` ending.
- The probe and the surface check assume the managed repository ignores `.ai-review/`: name the assumption on the harnesses guide, or have the probe ignore `.ai-review/runtime/`.
- An absent amendment witness skips the Workflow's cross-worktree bootstrap conflict check: port the divergence refusal or name the case on the harnesses guide.

## Out of scope

The H4e items' follow-ups (CX1) and any new role routing.

## Dispositions

All four findings of [#344](https://github.com/RodrigoFAbreu/workflow-controller/issues/344) are closed:

1. **`config.worktree` filters: code.** `gitrepo.filter_disabling_config` reads `git config --worktree` as well as `--local` and unions the filter names, tolerating only the multiple-worktrees refusal of a repository without `extensions.worktreeConfig` (CP1).
2. **State value legality: words.** The harnesses guide and common problems say that the Workflow's `reconcile` judges the values and what to do after `reconcile_invalid` (CP2).
3. **The `.ai-review/` assumption: words.** Both pages name it and its remedy (CP2).
4. **The absent amendment witness: words.** Both pages name the case and its remedy (CP2).

Code for items 2 to 4 would each add a mechanism (a value check, a probe ignore rule, a ported cross-worktree check); each can be its own item if the owner wants it.
