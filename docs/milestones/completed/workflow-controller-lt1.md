# LT1: workers run the selected tests per checkpoint and leave the full suite to CI (Revision 4)

Work item: `workflow-controller-lt1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `47abee70dd9a329ac55ef86eefb93ec9f4a6ba87` ("chore: run the test runner's shards in an isolated PID namespace so a stray kill(-1) reaches only the run (NS1, #329) (#352)"), the tip of `main`, passed explicitly as `/milestone-plan 47abee70dd9a329ac55ef86eefb93ec9f4a6ba87`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-lt1` (`docs/backlog.yaml` order 88, the item `python3 scripts/workflow_backlog.py next` returns; owner decision 2026-11-14, priority high). Its description is `docs/items/workflow-controller-lt1.md`. It depends on nothing.
Released baseline: the latest tag. Everything changed is `CLAUDE.md`, `docs/` and `tests/` (outside the wheel), so the squash commit is a `chore:` and releases no new version.
Pull request title: `chore: workers run the selected tests per checkpoint and leave the full suite to CI (LT1)`

## Goal

A Controller worker runs, at each checkpoint, only the tests selected for what that checkpoint changed, and leaves the full suite to CI, which runs it on every pull request (about 19 minutes) with one exception: `tests.test_catalogue_determinism` and `tests.test_scenarios_negative` are placed after the merge (`tools/test_shards.py` `CI_PLACEMENT`, `MAIN`; `.github/workflows/main.yml`), where they gate the release and not the pull request. Today the repository's own verification policy tells a worker the opposite ("Full-suite verification uses the sharded default, `python3 tools/run_tests.py`"), so workers run 8 shards plus scenario children at every checkpoint, sometimes two lanes at once (load about 12 on 16 cores). One mechanism: the **selection rule**, written once where a worker reads it and pinned by a test. No new command, lock or record.

## What exists

- `docs/guide/development.md` "Verification policy" says full-suite verification is the sharded default. This is the sentence a worker follows. It also holds the reuse rule (evidence is reused while the implementation revision and the test inventory hold) and the serial-run rule, both kept.
- `CLAUDE.md` "Running tests" says to run tests through `python3 tools/run_tests.py [names]` (NS1) and names no selection.
- The Workflow's `milestone-implement` command already says "run the narrowest relevant check (single test class/method, not the full suite)". It is a Workflow-managed file (`.claude/commands/`), so this repository does not edit it. The gap is the local policy above and the plans' own verification wording, which name the full suite.
- Two more Workflow-managed steps ask for a full run, and the repository policy has to bind them because they cannot be edited here. `.claude/commands/milestone-implement.md` step 3 says "Run the full required verification for the milestone (narrow checks are not sufficient at this point)", with the template's Gradle commands, which do not apply to this repository; today the local "Verification policy" is what defines that verification. `.claude/commands/apply-implementation-review.md` step 5 says "Rerun the relevant narrow tests for each fix, then the full suite used in `/milestone-implement` step 3 before closing this pass". Each fix pass changes the implementation revision, so the kept reuse rule voids the earlier full run and step 5 would call for another one per pass (D3 settles this).
- `tools/run_tests.py [names...]` already runs exactly the named modules, classes or methods with the coverage proof, so no runner change is needed.
- `tools/ci_changes.py` computes one fact, `docs` or `code`, from the changed paths (CI1a): a pull request that changes only documentation skips the test shards. It does not map a changed file to test modules, so "the selection" cannot be that function's output; it is the modules the plan names (D1).
- No `controller/` module runs the test suite: the Controller's checkpoint verification is the Workflow's state, claim and commit checks, not tests, so no Controller code changes (D5).
- The cost of narrow checks is known: H4f's five checkpoints missed eleven regressions and four repository-wide guard failures that the self-review's full run found. The guards (`tests.test_package_structure`, `tests.test_write_containment`, `tests.test_docs`, `tests.test_ci_changes`) read the whole tree, so a checkpoint that adds or moves a file must run them (D2).

## Decisions

- **D1. The plan names the selection.** Every checkpoint's section of a plan ends with a `**Tests:**` paragraph listing, in backticks, the test modules (or classes) the worker runs at that checkpoint: those that cover the changed code and the tests the checkpoint adds. The worker runs `python3 tools/run_tests.py <those names>`, and the coverage proof of the runner applies. A checkpoint with no code or test change (documentation only, as `tools/ci_changes.py` classifies it) runs `python3 tools/check_docs.py`. A worker that finds the paragraph missing runs the modules of the files it changed plus the D2 guards and readers, and says so in `docs/ACTIVE_MILESTONE.md`; it never falls back to the full suite.
- **D2. The repository-wide guards ride along when the tree changes.** A checkpoint that adds, moves or removes a file under `controller/`, `tools/`, `tests/` or `docs/` also runs `tests.test_package_structure`, `tests.test_write_containment` and `tests.test_docs`; one that changes `tools/ci_changes.py` or a path it classifies also runs `tests.test_ci_changes`. A checkpoint that edits a path `tools/ci_changes.py` keeps as code because a test reads it (its `NOT_DOCUMENTATION` table) also runs the readers that table names; for any `docs/guide/` edit that is at least `tests.test_plan_document_consistency`, which parses every guide's invocation lines against the live CLI parser. The rule, not each plan author's memory, supplies that selection. This is the rule's answer to the H4f finding, stated once.
- **D3. At most one local full run, at the self-review, never at a checkpoint and never in a review-fix pass.** The rewritten "Verification policy" states what the two managed steps mean in this repository: "the full required verification" of `/milestone-implement` step 3 is the self-review's single run below (or `python3 tools/check_docs.py` when the diff is documentation only), and "the full suite used in step 3" of `/apply-implementation-review` step 5 is not rerun in a fix pass: a fix pass runs its fixes' selections plus D2 and leaves the full suite to CI on the pushed pull request. The self-review of the whole diff (before the implementation bundle) may run `python3 tools/run_tests.py` once, when the diff touches shared code; a diff `tools/ci_changes.py` classifies as `docs` needs none, and otherwise the checkpoint selections plus D2 decide whether shared code was touched. The existing reuse rule applies: a full run whose implementation revision and test inventory still hold is not repeated. CI runs the full suite on the pull request, except those two post-merge modules (D7), and is the gate for everything else.
- **D4. Two full suites never run at once: a documented rule, not a lock.** Before a full run a worker checks that no other `tools/run_tests.py` run is live with `pgrep -af '^[^ ]*python[0-9.]*( -[^ ]+)* [^ ]*tools/run_tests\.py'` (exit 0 means a run is live; exit 1 means none) and waits or asks the orchestrator if one is. The pattern allows interpreter options (`python3 -u tools/run_tests.py` matches; checked in this session) and is anchored to an interpreter running the runner on purpose: the unanchored `pgrep -af 'tools/run_tests.py'` matches the caller's own `zsh -c` wrapper, whose argv contains the pattern, so it always reports a live run. A lock would be a second mechanism, a concurrency record of its own; the sizing rule makes it a separate item, offered as owner decision O1.
- **D5. No Controller code changes.** The worker's system note (`WORKER_LIFECYCLE_NOTE`) is part of the production argv that `tests/harness_contract/` pins, applies to every role, and says nothing about tests; the rule belongs in the repository's own instructions, which `CLAUDE.md` loads into every worker. The one test that guards the rule is documentation-level (D6).
- **D6. The rule is pinned.** A test in `tests/test_docs.py` reads `CLAUDE.md` and `docs/guide/development.md` and fails when either loses the rule's marker sentences, when the "Verification policy" again calls the full suite the checkpoint verification, when it loses D4's exact anchored `pgrep` command (or gains the unanchored one), when it loses the sentence routing guide edits to `tests.test_plan_document_consistency`, when it loses the sentence of D3 that says what `/milestone-implement` step 3's "full required verification" and `/apply-implementation-review` step 5's "full suite" mean here (so a later edit cannot quietly restore a full run per fix pass), when it loses the sentence of D7 that names `tests.test_catalogue_determinism` and `tests.test_scenarios_negative` as running after the merge (so the guide cannot go back to promising that the pull request runs everything), or when a module the guard list names does not exist. The marker is a fixed phrase per page, so a later rewording is a one-line test change, not a silent drift.
- **D7. The trade-off is recorded, not hidden.** A regression outside the changed area surfaces in CI on the pull request instead of at the checkpoint, with one named exception: pull request validation runs with `--ci-placement`, which removes `tests.test_catalogue_determinism` and `tests.test_scenarios_negative` (`tools/test_shards.py:146-153`); they run in `main.yml` after the merge and gate the release. A regression in an unselected area that only one of those two modules detects therefore passes the pull request, can reach `main`, and surfaces after the merge as a failed release gate; a local full run would have caught it earlier, and this plan accepts that cost without expanding CI coverage. A change that touches the scenario catalogue or its runner selects those two modules explicitly in its `**Tests:**` paragraph, since the pull request will not run them. `tests.test_ci_changes` guards the docs-versus-code decision that gates the shards, and the D2 guards cover the repository-wide checks. The item page and the guide say so.

## Checkpoints

<!-- CP1 -->
### CP1: the selection rule in the worker's instructions, and its guard

Rewrite `docs/guide/development.md` "Verification policy" to D1-D4 (keep the reuse rule, the serial-run rule and the CI-evidence rule); replace `CLAUDE.md` "Running tests" with the same rule in three sentences (run the modules the plan names through `tools/run_tests.py`; the guards of D2; the full suite is CI's (except `tests.test_catalogue_determinism` and `tests.test_scenarios_negative`, which run only after the merge and can fail the release gate for an unselected regression), with at most one local run at the self-review and never two at once; and what the two managed steps' "full" verification means here, D3); put `CLAUDE.md` under the technical approval by moving that exact path from `implementation_stage.excluded_paths` to `implementation_stage.protected_paths` in `docs/ai-workflow/registry/workflow-controller-lt1-artifacts.json`, since this item's deliverable includes it and the D6 markers alone would not catch a rewording that keeps them but changes the rule; add `VerificationPolicyTest` to `tests/test_docs.py` (D6), which also pins D4's exact command, the guide-reader sentence the D3 meaning sentence, and the D7 statement that those two modules run after the merge.

**Files:** `CLAUDE.md`, `docs/guide/development.md`, `tests/test_docs.py`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency`, `tests.test_ci_changes`, `tests.test_package_structure`, `tests.test_write_containment`, and `python3 tools/check_docs.py`.
<!-- /CP1 -->

<!-- CP2 -->
### CP2: the pages and the item record

`docs/guide/workers.md` gets one short paragraph for the operator of a lane: what a worker runs at a checkpoint, that the full suite is CI's on the pull request apart from the two modules that run after the merge (D7), and the D4 rule between lanes. `docs/items/workflow-controller-lt1.md` records the status, corrects its Scope paragraph (it now says the selection is "the one `tools/ci_changes.py` computes, or the test modules the plan names" and that the Controller's checkpoint verification changes; D1 and D5 settle both the other way: the plan names the modules, no Controller code changes), the plan link, the owner decisions O1 (a lock) and O2 (a later helper that maps changed files to modules), the D7 trade-off (including the two post-merge modules, so the item page and `docs/guide/workers.md` do not say the pull request runs everything) and the verification. `docs/guide/development.md` "Documentation checks" or "Design records" gains no new section; a link from the plan-writing guidance (`docs/guide/milestone-branches.md`, where a plan declares its title) says each checkpoint declares `**Tests:**`.

**Files:** `docs/guide/workers.md`, `docs/guide/milestone-branches.md`, `docs/items/workflow-controller-lt1.md`

**Tests:** `tests.test_docs`, `tests.test_plan_document_consistency` and `python3 tools/check_docs.py`.
<!-- /CP2 -->

## Open decisions

None of `docs/TECHNICAL_DECISIONS.md`'s "Open decision" rows is touched. Two scope questions are the owner's and this plan does not finalize them: O1 (whether D4's documented rule becomes a lock, recommendation: only if two lanes still collide after this item, as its own item) and O2 (whether to add a helper that maps a changed path to the modules that read it, recommendation: later, one item; `tools/ci_changes.py` computes only `docs` or `code`).

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | A worker's checkpoint verification runs only the tests the plan names for that checkpoint (plus the D2 guards), never the full suite | CP1, CP2 |
| R2 | The full suite is CI's: at most one local run, at the self-review, and two full runs never overlap (a documented rule); a review-fix pass runs its selections plus D2, not the full suite | CP1, CP2 |
| R3 | The rule is pinned by a test and recorded with its trade-off and its two open decisions | CP1, CP2 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The selection rule in the worker instructions and its guard | - | 2 | 1 |
| CP2 | The pages and the item record | CP1 | 1 | 1 |
