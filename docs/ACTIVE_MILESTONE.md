# Active Milestone

## Status

**Complete.** `workflow-controller-worker-launch` (H3, issue
[#191](https://github.com/RodrigoFAbreu/workflow-controller/issues/191)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the worker launch now sits behind the harness adapter. `controller/worker_launch.py` holds the
`WorkerLauncher` interface, the Claude Code launcher (which calls `worker.launch` unchanged) and the registry of launchers,
and `controller/job.py` resolves the launcher from the routed harness before the usage gate. Behaviour is unchanged, so the
squash commit is `chore:` and nothing is released. The plan is archived at
`docs/milestones/completed/workflow-controller-worker-launch.md`.

## In progress

`workflow-controller-reviewer-independence` (H10,
[#194](https://github.com/RodrigoFAbreu/workflow-controller/issues/194)), plan revision 3 approved. CP1 is complete: the
mixed-lane stop now names the stage's local review role, `reviewer.harness` and their families
(`review_mode.mixed_lane_reason`, used in `job._choose_review`), verified by `tests.test_review_independence`. CP2 is
complete: the guide, the troubleshooting page and the roadmap describe it, and the roadmap marks H10 complete and points at H9.

## Functional review checklist

Work item `workflow-controller-reviewer-independence` (H10), implementation revision 1. Everything below runs offline:
no network, no real `gh`, `claude`, `codex` or `ssh`, and nothing writes inside the product clone (use a scratch `TMPDIR`).

**Setup.** Check out the milestone branch at the implementation head. No build, flag or data is needed. Run from the clone
root with `PYTHONPATH` unset (see the Controller tests note: `PYTHONPATH=.` breaks an unrelated test).

**Test data.** None to seed. A mixed lane (worker roles on more than one provider family) is not reachable with the real
registry, because `codex` is only a reviewer there. Flows 1-3 therefore use the tests' own registry patch
(`tests.test_review_mode.codex_workers`), which makes `codex` also take the worker role and routes `review-plan` to it.

**Flows.**

1. *The new tests pass.* `python3 -m unittest tests.test_review_independence -v`.
   Expected: all 10 tests pass: unchanged one-family lanes, the text differs from `choose`'s only in the reason, `manual`
   never builds the text, each stage reports its own pair, an unset reviewer, an unregistered reviewer, the job stop
   launches nothing, `manual` reaches the ordinary gate, `explain` reports the text.
2. *The collision text, read by eye.* From the clone root run
   `python3 -c "import sys; sys.path.insert(0,'.'); from tests.test_review_mode import codex_workers, reviewer; from controller import review_mode as m, routing; p,o=codex_workers([routing.REVIEW_PLAN]); print(m.mixed_lane_reason(o, reviewer('codex'), 'plan')); print(); print(m.mixed_lane_reason(o, reviewer('codex'), 'implementation')); print(); print(m.mixed_lane_reason(o, None, 'plan')); print(); print(m.mixed_lane_reason(o, reviewer('nosuch'), 'plan'))"`.
   Expected, in order: (a) starts "the worker roles are routed to harnesses of more than one family (anthropic, openai)",
   names `review-plan runs on codex (openai)` and `reviewer.harness is codex (openai)`, says the two are of one family and that
   `distinct_reviewer_models` would refuse the approval; (b) for `implementation`, `review-implementation runs on claude-code
   (anthropic)` and "that pair is distinct and the stop comes from the other worker roles"; (c) `reviewer.harness is unset`
   with no pair verdict; (d) `reviewer.harness is nosuch (not a registered reviewer)` and no literal `None`. All four end with
   the remedy "Route every worker role ... or choose --review-mode manual".
3. *The step stops and launches nothing.* Covered by flow 1's `MixedLaneJobTest`; for a manual look run
   `python3 -m unittest tests.test_review_independence.MixedLaneJobTest -v` and confirm the three tests pass.
   Expected: status `GATE_BLOCKED`, reason `review_mode_unavailable` under `cross-model`, no review or worker launch; under
   `manual` the ordinary gate and no `review_mode_unavailable`.
4. *No regression in the neighbouring suites.* `python3 -m unittest tests.test_review_mode tests.test_review_job`.
   Expected: pass (a timing flake in `tests.test_worker` is known and unrelated).
5. *Docs match behaviour.* Read `docs/common-problems.md` ("A run stopped at review_mode_unavailable", three causes) and
   `docs/guide/runtime.md` (the mixed-family bullet and the mode table). Then `python3 tools/check_docs.py`.
   Expected: the pages describe the named pair as the flows above show, `manual` is stated as unaffected, the checker passes,
   and `docs/ROADMAP.md` marks H10 complete and points at H9.

**Out of scope / known limits.** The stop itself, its trigger and the exit code (`10`) are H2's and unchanged; only the text
changed. No release: the change is `chore:`-class wording plus docs. A real two-provider lane cannot be exercised offline;
the registry patch stands in for it. Two tests fail locally under Git 2.56 (trailer rule) and one golden drifts on main; both
predate this item.

## Next action

Test against the checklist above. Put findings in the feedback file, or run `/accept-milestone` if clean.
