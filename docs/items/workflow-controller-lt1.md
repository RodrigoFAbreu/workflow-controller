# LT1: workers run the selected tests per checkpoint and leave the full suite to CI

Item id: `workflow-controller-lt1`.

Status: implementing (2026-11-15); CP1 and CP2 are complete. Plan: [CONTROLLER_LT1_PLAN.md](../ai-workflow/CONTROLLER_LT1_PLAN.md) (revision 4). Owner decision 2026-11-14. Complexity: low to medium. Priority: high (owner).

## Why

Controller workers run the full suite (`python3 tools/run_tests.py`, 8 shards plus scenario children) at every checkpoint, and sometimes two lanes do so at once. That loads the owner's machine (load about 12 on 16 cores) while CI already runs the full suite on every pull request (about 19 minutes).

## Scope

One mechanism: the selection rule, written once where a worker reads it (`docs/guide/development.md` "Verification policy", `CLAUDE.md` "Running tests") and pinned by `tests.test_docs.VerificationPolicyTest`. The plan names the selection: each checkpoint ends with a `**Tests:**` paragraph, and the worker runs `python3 tools/run_tests.py <those names>`. `tools/ci_changes.py` computes only `docs` or `code`, so it does not map a changed file to modules. The repository-wide guards ride along when the tree changes. The full suite is CI's, with at most one local run at the self-review. Two full suites never run at once: a documented rule (an anchored `pgrep` check), not a lock.

No Controller code changes: the Controller's checkpoint verification is the Workflow's state, claim and commit checks, not tests, and the worker's lifecycle note is pinned by `tests/harness_contract/`. See [workers](../guide/workers.md#what-a-worker-runs-at-a-checkpoint).

## Owner decisions

- **O1.** Whether the documented rule between lanes becomes a lock. Recommendation: only if two lanes still collide after this item, as its own item.
- **O2.** Whether to add a helper that maps a changed path to the modules that read it. Recommendation: later, as one item.

## Trade-off

A regression outside the changed area surfaces later, in CI on the pull request, instead of at the checkpoint. One exception is worse: pull request validation runs with `--ci-placement`, which removes `tests.test_catalogue_determinism` and `tests.test_scenarios_negative`; they run after the merge and gate the release. A regression that only one of them detects can pass the pull request and surface after the merge as a failed release gate. This is accepted without expanding CI coverage, and a change that touches the scenario catalogue or its runner selects both modules in its `**Tests:**` paragraph. `tests.test_ci_changes` guards the docs-versus-code decision.

## Verification

CP1: `tests.test_docs`, `tests.test_plan_document_consistency`, `tests.test_ci_changes`, `tests.test_package_structure`, `tests.test_write_containment` and `python3 tools/check_docs.py`. CP2: `tests.test_docs`, `tests.test_plan_document_consistency` and `python3 tools/check_docs.py`.
