# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-harness-verification` (H7, issue
[#182](https://github.com/RodrigoFAbreu/workflow-controller/issues/182)) is implemented (CP1 `404e0cd`, CP2 `425dbfa`) and
approved at the implementation stage. Plan: `docs/ai-workflow/CONTROLLER_HARNESS_VERIFICATION_PLAN.md`.

H7 makes a harness adapter declare how a build of its harness is verified (`build_verification`: `verify-claude` for
Claude Code, `None` for Codex) and relabels the recorded version as informational. It is a declaration plus documentation:
no launch behaviour changes.

## Functional review checklist

Every flow runs offline from a clone of this branch. Nothing needs the network, `gh`, `claude`, `codex` or `ssh`, and no
flow writes inside the clone (the unit tests and `tools/` checks write to `TMPDIR`; use a scratch `HOME`/`TMPDIR`).

### Setup

- Check out `milestone/workflow-controller-harness-verification` at the commit named in the checkpoint-evidence report.
- Python 3 only. No build, install, feature flag or seed data is needed.

### Flows

1. **The adapter declares its verification.**
   Run `python3 -I -c "from controller import harness as h; print({n: a.build_verification for n, a in h.ADAPTERS.items()})"`.
   Expected: `claude-code` is `verify-claude` and `codex` is `None`.
2. **The generated table carries the new columns.**
   Run `python3 tools/harness_table.py --check`; then open the first table under "The tables" in `docs/guide/harnesses.md`.
   Expected: exit 0; the columns after "Usage" are "Captured with", "Build verification", "Unknown version refused", in that
   order; the Claude row shows `` `verify-claude` `` and the Codex row shows `none: launch checks only`; the old
   "Pinned version" header is gone.
3. **The table is generated, not hand edited.**
   Change a cell in that table by hand in a scratch copy, then run `python3 tools/harness_table.py --check` there.
   Expected: non-zero exit naming the drift. (Do this in a scratch copy, never in the review clone.)
4. **The guide prose states the scope.**
   Read the paragraph under the routing section of `docs/guide/harnesses.md` and the harness table paragraph in
   `docs/guide/workers.md`.
   Expected: "Captured with" is described as informational and not compared at launch; `verify-claude` is said to admit the
   functional-tester launch only, while the worker and reviewer launches run any build on `PATH`; Codex is `none` until
   `verify-codex` ships; "Unknown version refused" keeps its meaning. The two guides agree.
5. **The developer guide tells a harness author what to declare.**
   Read "Adding a harness or a git host" step 1 and the pin-bump sentence in `docs/guide/development.md`.
   Expected: step 1 names the verification command (or `None`); a pin bump is said to change the informational version only.
6. **The roadmap is updated.**
   Read row H7 and the epic order in `docs/ROADMAP.md`.
   Expected: H7 is marked done in the form of the H6 and H8 rows, says nothing changed at launch, says `chore:` with no
   release; H2 is named as the next item; "Next, in order" no longer lists H7.
7. **Docs checks pass.** `python3 tools/check_docs.py`. Expected: exit 0, no broken link or anchor.
8. **The tests pass.** `python3 -m unittest tests.test_harness tests.test_harness_table`.
   Expected: 41 tests, OK. Optionally run the full suite with `python3 tools/run_tests.py` (see
   `docs/guide/development.md`); note the known local flakes in the maintainer notes before calling a failure a defect.
9. **No launch behaviour changed.**
   Run `git diff f4d34b17a7631d322ee13f2f01c39a508c552b36..HEAD --stat -- controller/`.
   Expected: only `controller/harness.py` changed; `review.py`, `claude_build.py`, `functional_review.py`, `worker.py` and
   `cli.py` show no diff.

### Known limitations and out of scope

- `verify-codex` and verify-on-first-use for Codex (C10g-2), the launch seam (H3), a Codex worker or tester, a version range
  or a build-digest cache for Codex, and any change to which builds are accepted.
- Nothing reads `build_verification` at launch; it is a declaration the table and guides report.
- The squash commit is `chore:`, so this milestone releases nothing.

## Next action

Test against the checklist above and write findings to
`.ai-review/workflow-controller-harness-verification/feedback/FUNCTIONAL_REVIEW.md`, then run `/accept-milestone`, or
`/apply-functional-review` if there are findings.
