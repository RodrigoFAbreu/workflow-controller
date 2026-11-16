# AO1: opt this repository in to the Controller's auto-merge (Revision 2)

Work item: `workflow-controller-ao1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `7eb15d2eae784f550873b291443d26ea1f73f227` ("feat: accept the models the Codex CLI lists, looked up when a route names an unlisted one (CM1) (#368)"), the tip of `main`, passed explicitly as `/milestone-plan 7eb15d2eae784f550873b291443d26ea1f73f227`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ao1` (`docs/backlog.yaml` order 93, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ao1.md`. It depends on nothing.
Released baseline: the latest tag. The change edits the committed policy and pages only, so the squash commit is a `chore:` and publishes no release.
Pull request title: `chore: opt this repository in to the Controller's auto-merge (AO1)`

## Goal

The Controller's opt-in auto-merge (release 1.6.0, ADR 0009) squash-merges an accepted milestone pull request itself, waits for the release workflow, closes the milestone out and stops. This repository does not opt in, so every pull request waits for a human. After this item the committed policy opts in, so a milestone bound after the change merges without a human.

This item builds no mechanism: it is one committed configuration change, the pages that say the repository does not opt in, and a test that keeps the opt-in coherent with the workflow files.

## What exists

- **The policy.** `.workflow-controller/policy.json` has `milestone_branches.pull_request` with `draft: true`, `ready_requires_green_checks: true` and `merge_method: "squash"`. `controller/repo_policy.py` accepts the optional `auto_merge` (boolean, default false) and `release_workflow` (default `main.yml`), and refuses `auto_merge: true` unless `merge_method` is `squash` and `ready_requires_green_checks` is true. Both are already set.
- **The release workflow.** `main.yml` is retired (PR #357); `ci.yml` is the one trunk entry, and its `build-publish` job calls `build-publish.yml` in the same run, so the release is published by a `ci.yml` run. The default `main.yml` would make the release wait look for a workflow that no longer exists.
- **The binding.** A milestone's policy is read from the snapshot taken when its branch was bound, so a milestone bound before the change never auto-merges (the item page's caveat). The operator switch `merge.auto` in the settings file defaults to true.
- **The pages.** `docs/guide/ci-and-releases.md`, Repository settings, says "This repository's policy does not opt in yet; the opt-in is a `chore:` pull request after 1.6.0 is installed".
- **The ruleset.** "Main Protection" requires `validate / plan`, `validate / tests-result`, `validate / package`, `workflow-conformance` and `PR title`; the Controller merges only after it has seen every check green, so the ruleset does not refuse its merge.

## Decisions

- **D1. Two keys, no more.** Add `"auto_merge": true` and `"release_workflow": "ci.yml"` to the `pull_request` block. Nothing else in the policy changes; `release.enabled` and the trigger stay as they are.
- **D2. A `chore:` item publishes no release, and the wait already settles that.** The change types map `chore` to `none`, so the milestone's squash commit releases nothing. The release wait already handles a commit whose release classification is `NO_CHANGE`: it settles with the event `release_settled` (`docs/guide/milestone-branches.md`, "The release wait"). This item changes none of that code and records no observation of it: this milestone's own pull request is not auto-merged (D3), so no release wait runs for it. The live check of the opt-in belongs to the first later milestone (D3), recorded outside this item.
- **D3. The opt-in cannot take effect on this milestone.** The policy is read from the binding snapshot taken before CP1 lands, so this milestone's own pull request waits for a human and is merged by the owner under their standing rule. The first milestone bound after AO1 is merged is the verification the item asks for: the functional review of AO1 checks that the committed policy parses with the two keys (the test of CP1), and the pages say the first later milestone is the live check.
- **D4. A test keeps the opt-in coherent, and two existing tests change.** A new test in `tests/test_repo_policy.py` reads the repository's own committed policy through `repo_policy.parse_policy`, asserts `auto_merge` is true, and asserts that the `release_workflow` it names is one of the workflow models of `tools/ci_workflows.py` and that the model's `build-publish` job calls `./.github/workflows/build-publish.yml`. It reads the models the way `tests/test_ci_workflows.py` does (the module is loaded from `tools/ci_workflows.py`; the repository is stdlib-only, so no YAML parser and no substring match on the file). The existing parser already refuses a missing `squash` or `ready_requires_green_checks`, so the test copies none of those rules. Two existing tests pin the committed policy and change with D1:
  - `test_absent_means_off_and_main_yml` keeps its fixture half (the parser default for a policy without the keys: `auto_merge` false, `release_workflow` `main.yml`). Its last two lines, which assert that the committed policy has the defaults, move into the new test as the opposite assertion.
  - `test_the_reference_file_is_one_plans_exact_content` compares the committed policy with the pre-cutover or post-cutover plan block after removing the two AO1 keys (`auto_merge`, `release_workflow`) from the committed `pull_request` block, and asserts that those two keys are exactly `true` and `"ci.yml"`. The historical plan documents are not edited. The rule stated: the committed policy is one of the two plan blocks plus exactly the AO1 opt-in.
- **D5. Pages describe the present state.** The sentence that the repository does not opt in is replaced by what the policy now holds. History stays in the item page and the roadmap archive, which are not rewritten.

## Checkpoints

<!-- CP1 -->
### CP1: the policy opt-in and its test

Add `"auto_merge": true` and `"release_workflow": "ci.yml"` to `.workflow-controller/policy.json`'s `pull_request` block (D1). Add the test of D4 to `tests/test_repo_policy.py`: the committed policy parses, `milestone_branches.auto_merge` is true, `release_workflow` is `ci.yml`, and the `ci.yml` model has a `build-publish` job calling `build-publish.yml`. Change the two existing tests named in D4 (`test_absent_means_off_and_main_yml`, `test_the_reference_file_is_one_plans_exact_content`). Run the modules in the **Tests:** paragraph, which read the same file, to confirm nothing else depends on the absent keys.

**Files:** `.workflow-controller/policy.json`, `tests/test_repo_policy.py`

**Tests:** `tests.test_repo_policy`, `tests.test_ci_workflows`, `tests.test_version_authority`, `tests.test_milestone_branch`. All four are run: `tests.test_milestone_branch` reads the committed policy through the milestone-branch code.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: the pages and the item record

`docs/guide/ci-and-releases.md` (Repository settings: replace "This repository's policy does not opt in yet" with the present opt-in and the `ci.yml` release workflow, and drop any remaining statement that `main.yml` is the release workflow), `docs/guide/milestone-branches.md` (the reference-configuration paragraph, if it says the repository does not opt in; the generic example near line 386 with `"release_workflow": "main.yml"` stays as it is, because it shows the parser's default, not this repository's policy, so the `main.yml` sweep above does not touch it), and `docs/items/workflow-controller-ao1.md` (status, plan link, what was done, that a milestone bound before the change never auto-merges, and the first later milestone as the live check of D3).

**Files:** `docs/guide/ci-and-releases.md`, `docs/guide/milestone-branches.md`, `docs/items/workflow-controller-ao1.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `python3 scripts/workflow_backlog.py preflight-accept workflow-controller-ao1`.
<!-- /CP2 -->

## Open decisions

None of the repository's decision-record "Open decision" rows is touched. This plan opens no scope question: whether the release wait should skip a milestone whose change type releases nothing is answered by the documented `NO_CHANGE` settlement (D2).

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | The committed policy sets `auto_merge: true` and `release_workflow: "ci.yml"`, and a test keeps the named workflow real and publishing | CP1 |
| R2 | The pages and the item record state the opt-in, the `ci.yml` release workflow and the caveat that a milestone bound before the change never auto-merges | CP2 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The policy opt-in and its test | - | 1 | 1 |
| CP2 | The pages and the item record | CP1 | 1 | 1 |
