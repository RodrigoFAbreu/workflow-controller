# CX2: review follow-ups of the Codex worker item H4f (Revision 2)

Work item: `workflow-controller-cx2`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `2eeb5d4ae366f931de6ed5ee3bcd77b766fbd7c4` ("fix: close the open review follow-ups of the Codex worker items H4e2, H4e3 and H4e3b (CX1) (#362)"), the tip of `main`, passed explicitly as `/milestone-plan 2eeb5d4ae366f931de6ed5ee3bcd77b766fbd7c4`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-cx2` (`docs/backlog.yaml` order 91, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-cx2.md`. It depends on nothing.
Released baseline: the latest tag. The change edits `controller/` (a defence-in-depth fix), so the squash commit is a `fix:` and releases a patch version.
Pull request title: `fix: close the open review follow-ups of the Codex worker item H4f (CX2)`

## Goal

The accepted H4f item (the `milestone-implement` role in a private Codex clone) left four Optional review findings, kept as [#344](https://github.com/RodrigoFAbreu/workflow-controller/issues/344). None is reachable through the normal landing, which compares the clone's configuration before it runs anything. After this item one is closed in code (a filter defined in `config.worktree`), and the other three are closed with words on the pages, because the code option for each adds a mechanism (a value check, a probe ignore rule, a ported cross-worktree check) that the sizing rule keeps for its own item.

## What exists

- **The filter enumeration.** `controller/gitrepo.py` `filter_disabling_config` reads `git config --local --includes --name-only --list -z` and returns the `-c filter.<name>.{process,clean,smudge,required}` overrides that `controller/codex_worker_landing.py` passes to its status probe of a kept clone. `--local` reads only `.git/config` (and what it includes). With `extensions.worktreeConfig=true` Git also reads `config.worktree`, so a driver set there is not named and a racy-stat status could still run it. `tests/test_gitrepo.py` has the enumeration tests.
- **The state-value gap.** The checkpoint role's `state_touched` check limits which state fields a checkpoint commit may change, not their values (`phase`, `checkpoints`, the checkpoint ids). The Workflow's `reconcile` is what judges the landed state; an `invalid` answer ends the job at `reconcile_invalid`.
- **The `.ai-review/` assumption.** The checkpoint invocation in a clone writes `.ai-review/runtime/` files. The unfinished-work probe and the surface check count them as changes only when the managed repository does not ignore `.ai-review/` (this repository does, `.gitignore`).
- **The absent witness.** `controller/checkpoint_claims.py` treats an absent amendment witness as "no amendment"; the Workflow instead bootstraps one by scanning every worktree and refuses on divergent `amendment_history` entries, which a clone (one worktree) cannot see.
- **The pages.** `docs/guide/harnesses.md` ("A Codex worker's private clone and its landing") and `docs/common-problems.md` (the Codex checkpoint refusals).

## Decisions

- **D1. Name the drivers of every scope Git reads.** `filter_disabling_config` adds a second read, `git config --worktree --includes --name-only --list -z`, and unions its filter names with the first. Without `extensions.worktreeConfig` the outcome depends on the number of worktrees, not the Git version (checked on git 2.56.1): in a repository with one worktree `--worktree` exits 0 and lists `.git/config` again, so the union is a no-op; in a repository with a linked worktree (a Codex worker can run `git worktree add` in its clone) it exits 128 with `fatal: --worktree cannot be used with multiple working trees unless the config extension worktreeConfig is enabled`. The second read is therefore a `_probe(..., absent=128, absent_stderr="--worktree cannot be used with multiple working trees")`: exactly that refusal means no worktree scope (an empty list), and every other failure raises as the first read does, so the read stays fail-closed. One principle: every filter name Git can resolve is switched off. The docstring says both reads.
- **D2. State value legality is `reconcile`'s; say so.** The plan takes the documentation option: a pre-landing value check would duplicate the Workflow's judgement and track its phase table. The pages say that the checkpoint profile bounds the fields, not their values, that the Workflow's `reconcile` judges the landed state, and what the operator does after a `reconcile_invalid` ending (read the clone's commits, restore the state from the base with the Workflow's own commands or `git`, run again).
- **D3. The `.ai-review/` assumption is named.** `docs/guide/harnesses.md` says the probe and the surface check assume the managed repository ignores `.ai-review/`, what a repository that does not will see (a clean run kept as `unfinished_work`), and the remedy (ignore `.ai-review/`, as the Workflow's installer does).
- **D4. The absent-witness case is named.** The harnesses guide says that a clone's absent amendment witness skips the Workflow's cross-worktree bootstrap conflict check, that the Controller's `managed_amendment_*` reads run in the managed repository before the clone is made, and that a repository whose worktrees disagree on `amendment_history` is reconciled by the operator with the Workflow before a Codex checkpoint is routed.
- **D5. Tests are hermetic.** Every new test builds a scratch repository, uses a planted filter that only touches a marker file and no network, and passes the repository-wide guards.

## Checkpoints

<!-- CP1 -->
### CP1: the filter enumeration reads the worktree configuration

`controller/gitrepo.py` `filter_disabling_config`: the second read and the union of D1, and the docstring. Tests in `tests/test_gitrepo.py` (and one in `tests/test_codex_worker_landing.py`, below): with `extensions.worktreeConfig=true` a driver set by `git config --worktree` is named; the existing cases are unchanged; a single-worktree repository without the extension gives the same options as today and does not raise; a repository with a linked worktree and no extension gives the `--local`-only options without raising; any other failure of the second read (a malformed `config.worktree`) raises; a `tests/test_codex_worker_landing.py` case where `unfinished_work` reads a clone whose `config.worktree` defines a planted clean filter on a racy-stat file keeps its marker absent; a planted `--worktree` clean filter does not run under the disabling set on a racy-stat status (a marker file stays absent) and does run without it.

**Files:** `controller/gitrepo.py`, `tests/test_gitrepo.py`, `tests/test_codex_worker_landing.py`

**Tests:** `tests.test_gitrepo`, `tests.test_codex_worker_landing`, `tests.test_write_containment`, `tests.test_package_structure`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: the pages and the item record

`docs/guide/harnesses.md` and `docs/common-problems.md` carry the D2, D3 and D4 wording; `docs/items/workflow-controller-cx2.md` records the four dispositions, with the issue [#344](https://github.com/RodrigoFAbreu/workflow-controller/issues/344).

**Files:** `docs/guide/harnesses.md`, `docs/common-problems.md`, `docs/items/workflow-controller-cx2.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-cx2`.
<!-- /CP2 -->

## Open decisions

None of the repository's decision-record "Open decision" rows is touched. One scope question is the owner's and this plan does not finalize it: O1, whether the state value check, the probe's `.ai-review/runtime/` ignore and the ported bootstrap check should be code instead of the words D2 to D4 choose. Each is a new mechanism and can be its own item.

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | The filter enumeration names drivers defined in `config.worktree`, tolerating only the multiple-worktrees refusal of a repository without `extensions.worktreeConfig`, proved by a planted-driver test (#344 item 1) | CP1 |
| R2 | The pages say that state value legality is `reconcile`'s, name the `.ai-review/` assumption and the absent-witness case, and the item page records the four dispositions (#344 items 2 to 4) | CP2 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The filter enumeration reads the worktree configuration | - | 2 | 1 |
| CP2 | The pages and the item record | CP1 | 1 | 1 |
