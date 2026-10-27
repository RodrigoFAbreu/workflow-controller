# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-reviewer-family` (H2, issue
[#185](https://github.com/RodrigoFAbreu/workflow-controller/issues/185)) is implemented (CP1, CP2) and approved at the
implementation stage by gate policy. The review mode now reads the workers' provider family from the routing
(`review_mode.worker_families`) instead of the constant `WORKER_FAMILY`. With today's registry no outcome changes, so the
squash commit is `chore:` and releases nothing. The plan is `docs/ai-workflow/CONTROLLER_REVIEWER_FAMILY_PLAN.md`.

## Functional review checklist

Everything below runs offline in the tester's sandbox: no network, no real `gh`, `claude`, `codex` or `ssh`, a read-only
clone, writes only to the output folder, scratch `HOME`/`XDG_*` and `TMPDIR`. No flow needs a pull request or a forge call.
Run Python with `PYTHONDONTWRITEBYTECODE=1` so nothing is written inside the clone.

### Setup

- From the repository root, no build step. Do not set `PYTHONPATH=.` for the test runs.
- No data to seed. The flows use the built-in routing and a patched registry inside the tests.

### Flows

1. **Derived family, default routing.** Run
   `PYTHONDONTWRITEBYTECODE=1 python3 -c "from controller import review_mode as r; print(sorted(r.worker_families()))"`.
   Expected: `['anthropic']`.
2. **A Claude reviewer is still not cross-model today.**
   `python3 -c "from controller import review_mode as r; print(r.distinct_harness({'harness':'claude'}), r.distinct_harness({'harness':'codex'}))"`.
   Expected: `None codex` (same outcomes as before the change).
3. **Routing is honoured with `--role-harness`.**
   `python3 -c "from controller import review_mode as r, routing as o; print(sorted(r.worker_families(o.NO_OVERRIDES)))"`.
   Expected: `['anthropic']`; every worker role can only take `claude-code`, so no routing the registry accepts changes it.
4. **Mixed families stop in a named way.**
   `python3 -c "from controller import review_mode as r; c=r.choose('cross-model', {'harness':'codex'}, 'plan', None, frozenset({'anthropic','openai'})); print(c.kind, c.reason)"`.
   Expected: `refused`, with a reason naming `anthropic, openai`, saying there is no single provider to be distinct from, and
   offering `--review-mode manual`. The same call with `'manual'` as the mode returns the ordinary gate, never a refusal.
5. **Equivalence and patched-registry cases.**
   `python3 -m unittest tests.test_review_mode tests.test_harness tests.test_review_claude tests.test_review_job tests.test_review_same_provider`.
   Expected: all pass, including the D5 equivalence test, the patched-registry cross-model/mixed-family cases and the
   source scan that no module under `controller/` reads `WORKER_FAMILY`.
6. **No stale constant.** `grep -rn "WORKER_FAMILY" controller/`. Expected: no output.
7. **`explain` still works.** In a scratch managed repository (scratch origin, fake `gh`), at an external review gate run
   `python3 -m controller explain --review-mode cross-model` and again with `--role-harness` set to an invalid pair.
   Expected: the review-mode lines are printed as before; a route that `step` would refuse (exit 20) does not make `explain`
   fail, and the report falls back to the built-in routing. If no repository at an external review gate is available in the
   sandbox, `tests.test_review_job` (the `review_mode_report` case) covers the threading; mark the flow
   `environment`.
8. **Docs.** `python3 tools/check_docs.py` and `python3 tools/harness_table.py --check` exit 0. Read the `reviewer.harness`
   rows and mode table in `docs/guide/runtime.md` and the family sentence in `docs/guide/harnesses.md`: they say `claude` is a
   registered reviewer harness, cross-model only when the routed workers are of another family (never with the current
   registry), that the workers' family comes from the routing, that mixed families stop with `review_mode_unavailable`, and
   that the same-provider review still runs Claude. Row H2 of `docs/ROADMAP.md` is marked done and H3 is named next.
9. **Full suite.** `python3 tests/run_tests.py` (or the project's usual runner). Expected: green. Known local-only failures
   unrelated to this change: the two Git 2.56 trailer tests and `plan_stage_decisions.json --check` drift.

### Known limitations and out of scope

- No non-Anthropic worker harness exists yet (H4), so the cross-model-Claude case and the mixed-family refusal are reachable
  only through a patched registry in tests.
- The same-provider review still runs Claude (`SAME_PROVIDER_HARNESS`); making it follow the routing belongs to H4.
- Not changed: review modes, the gate policy, settings, exit codes, gate codes, reviewer launch, the verdict format, the
  launch seam (H3), the fallback chain (H9), routing independence (H10).

## Next action

Test the checklist and write findings to the feedback path printed by `/prepare-functional-review`; then
`/accept-milestone` (all checkpoints are `COMPLETE`) or `/apply-functional-review`.
