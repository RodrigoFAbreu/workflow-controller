# Active Milestone

## Status

**Complete.** `workflow-controller-scenario-base` (SC1, issue
[#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the scenario tests now measure from the checkout instead of an old pinned base commit, and a
sandbox-free guard checks that every file the fixture commits is classified. Only tests and one development-guide
paragraph changed; the squash commit is `test:`, so nothing is released. The plan is archived at
`docs/milestones/completed/workflow-controller-scenario-base.md`.

## In progress: `workflow-controller-worker-launch` (H3)

Plan: `docs/ai-workflow/CONTROLLER_WORKER_LAUNCH_PLAN.md`. CP1 (the launcher interface, the Claude Code launcher and the
call site in `controller/job.py`) and CP2 (the development and harness guides, the runtime guide sentence and the roadmap row)
are implemented and verified; the full verification and the implementation review bundle are next.

## Next action

`workflow-controller-worker-launch` is waiting for its implementation review. After it, H10 is next in the
harness-neutral roles epic in `docs/ROADMAP.md`; run `/milestone-plan` for it.

## Functional review checklist

`workflow-controller-worker-launch` (H3), implementation revision 1. Every flow is offline and runs without a real
`claude`, `codex`, `ssh` or `gh`; none writes inside the clone (Python may write `__pycache__` only if allowed; set
`PYTHONDONTWRITEBYTECODE=1`). Run from the repository root.

**Setup.** No build or install step, no feature flag, no data to seed. Use `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest ...`.
Do not set `PYTHONPATH=.`.

**Flows.**

1. Launcher unit tests: `python3 -m unittest -v tests.test_worker_launch`.
   Expected: 11 tests, all `ok`. They cover the call-time lookup (patching `worker.launch` after the launcher is built still
   reaches the patch), the sixteen keywords reaching `worker.launch` unchanged, the registry equal to the adapters that declare
   the worker role, `claude-code` and `claude` resolving to the same launcher, Codex refused with `HarnessRoleError`, an adapter
   with the role and no launcher refused, the seam used by `_launch_job`/`_execute_step_locked`, and the import discipline.
2. Generated harness table unchanged: `python3 tools/harness_table.py --check`. Expected: exit 0, no output about drift.
3. Routed job path: `python3 -m unittest -v tests.test_job.CtrlCTest.test_the_interrupt_names_the_worker_and_propagates`.
   Expected: `ok`; `execute_step` still reaches the patched `worker.launch` through the launcher.
4. Package structure: `python3 -m unittest tests.test_package_structure tests.test_harness`. Expected: all pass (`worker_launch`
   registered in `controller/__init__.py` and in `DEPENDENCY_ORDER`).
5. Docs: `python3 tools/check_docs.py`. Expected: exit 0. Read the new sentences in `docs/guide/development.md` ("Adding a
   harness or a git host") and `docs/guide/harnesses.md`, and row H3 of `docs/ROADMAP.md` (marked complete): they should match the
   behaviour above.
6. Inspect the seam: `git diff cdd300d..HEAD -- controller/job.py`. Expected: only the import, the launcher resolved from
   `route.harness` before the usage gate, the `launcher` keyword forwarded, the `launcher` parameter, and `launcher.launch(`
   replacing `worker.launch(`.

**Known limitations / out of scope.** No second launcher: Codex declares no worker role, so a Codex-routed worker is still
refused (H4a to H4g). The review launch, the functional tester launch, `claude_build.py` and `controller/worker.py` are unchanged.
No user-visible change, so no release (`chore:`). The full suite is the operator's rerun; two Git 2.56 trailer tests fail
locally and are known. No real `claude` end-to-end run is part of this review.
