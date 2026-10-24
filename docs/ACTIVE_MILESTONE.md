# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-cleanup-3a` (CU3a, implementation revision 4; closes issues
#100, #118, #125, #138 and #143 and only refers to #140) is implemented on its milestone branch, both implementation
review stages approved. The change touches tests, test helpers and the roadmap only, so there is no release. The plan
is `docs/ai-workflow/CONTROLLER_CLEANUP_3A_PLAN.md`.

## Functional review checklist

**Setup.** No build or install step and no feature flag. Run from the repository root on the milestone branch with
`PYTHONDONTWRITEBYTECODE=1` and without `PYTHONPATH=.`. Every flow below is offline: no `gh`, `claude`, `codex` or
network is needed, and nothing writes inside the clone (scratch space is under `TMPDIR`).

**Test data.** None to seed; each test builds its own scratch folders.

**Flows.**

1. *Hang-child pid file is written whole (#100, D1).*
   `python3 -m unittest tests.test_fake_claude_contract.HangChildPidFileTest -v`.
   Expected: both tests pass (the file replaces a sentinel as a new inode holding an integer; a reader that sees the
   path always reads an integer) and no zombie child is left.
2. *Bounded executor wait (#138, D2).*
   `python3 -m unittest tests.test_test_shards.ConformanceLaneExecutionTest -v`.
   Expected: all cases pass in `SIGINT`, `SIGTERM` and `exit` modes, well inside the 180-second bound.
3. *No zombie after a forced stop (#143, D3).*
   `python3 -m unittest tests.test_catalogue_determinism.RunBoundedTest -v`.
   Expected: pass, including the unresponsive-reaper case (reaper return code 137 or -9, never 0) and the
   no-new-zombie check after each case.
4. *Fake tester resolves `..` through links (#140 point 2, D4).*
   `python3 -m unittest tests.test_functional_sandbox -v`.
   Expected: pass; `clone/build/back/../src/app.py` with `back -> clone/src` is denied, and allowed when `back` is a
   plain folder.
5. *The two link rows are separate causes (#140 point 3, D5).*
   `python3 -m unittest tests.test_claude_build -v`.
   Expected: `link_replaced` reports only "replaced or removed", `link_in_clone` only "did not happen". Known
   limitation: one test in this module errors under the deep runner `TMPDIR` (socket path over 107 bytes); it also
   fails on `main` and is not part of this milestone.
6. *Small tidy-ups (#118, #125, D6).*
   `python3 -m unittest tests.test_functional_review tests.test_protocol_job tests.test_protocol_lifecycle tests.test_ci_workflows`.
   Expected: pass. Read the diff (`git diff 8a5ac30..HEAD -- tests`) for the import order, the `log_dir` assertion, the
   `wait_seconds` snapshot key, the `refused_validation_run` comment and the folded loop.
7. *Docs and whole suite (D7).*
   `python3 tools/check_docs.py`, then `python3 tests/run_tests.py` (or the usual full-suite command).
   Expected: docs check passes; suite green except the two known Git 2.56 trailer tests. `docs/ROADMAP.md` marks
   CU3a done and drops the closed issues from the follow-up list.

**Known limitations / out of scope.** Nothing under `controller/` changed (`runtime.remove_tree` is C10k, so #140
stays open); `verify-claude` is CU3b; usage and settings tidy-ups are CU3c; CI workflow files are untouched. Zombies
under a parent outside the test process stay out of reach of the new check.

## Next action

Test the checklist, then write any findings to the feedback file named in the preparation message, or run
`/accept-milestone`.
