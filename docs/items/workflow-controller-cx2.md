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

1. **`config.worktree` filters: code.** `gitrepo.filter_disabling_config` reads `git config --worktree` as well as `--local` and unions the filter names. It tolerates two `--worktree` refusals, which mean no worktree scope: a linked worktree without `extensions.worktreeConfig` (CP1), and the extension on with no `config.worktree` written (found at the self-review). Every other failure raises.
2. **State value legality: words.** The harnesses guide and common problems say that the Workflow's `reconcile` judges the values and what to do after `reconcile_invalid` (CP2).
3. **The `.ai-review/` assumption: words.** Both pages name it and its remedy (CP2).
4. **The absent amendment witness: words.** Both pages name the case and its remedy (CP2).

Code for items 2 to 4 would each add a mechanism (a value check, a probe ignore rule, a ported cross-worktree check); each can be its own item if the owner wants it.

## Deviations from the plan

- **D1:** the plan tolerated only the multiple-worktrees refusal; the self-review added the extension-on, no-`config.worktree` refusal (Git's own `status` treats a missing file as nothing set). The pull request description states it too.
- **D2:** the plan said to read "the clone's commits" after `reconcile_invalid`. The landing removes a clean clone before the Workflow's `reconcile` runs, so both pages say to read the landed commits in the managed repository (`git log <base>..HEAD -- docs/ai-workflow/WORKFLOW_STATE.json`, `<base>` the job record's `codex_worker.clone.base`).
