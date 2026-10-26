# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-harness-table` (H6, issue #166), implementation revision 2,
technical approval recorded by policy. Plan: `docs/ai-workflow/CONTROLLER_HARNESS_TABLE_PLAN.md` (revision 3).

## Next action

Run the checklist below, then `/accept-milestone`. Findings go to the feedback file named by
`/prepare-functional-review`, then `/apply-functional-review`.

## Functional review checklist

Scope: a documentation page whose two tables are generated from the adapter registry, a generator
(`tools/harness_table.py`) and a documentation check that the page is current. Nothing the installed Controller does
changes. Every flow runs offline; none needs `gh`, `claude`, `codex` or `ssh`.

### Setup

- Work in a checkout of the milestone branch. The clone may be read-only: flows that write use a scratch copy.
- `S=$(mktemp -d); mkdir -p $S/docs/guide; cp docs/guide/harnesses.md $S/docs/guide/` (the scratch root for flows B-D).
- Run the Controller as `PYTHONPATH=$PWD python3 -P -m controller ...` (flow F only).
- No test data to seed.

### Flows

**A. The page is current.** `python3 tools/harness_table.py --check`; `python3 tools/check_docs.py`.
Expected: both print nothing and exit 0.

**B. A stale page is reported.** Edit the scratch copy (e.g. flip a `yes` in the `claude-code` row), then
`python3 tools/harness_table.py --check --root $S`.
Expected: exit 1; one line naming `docs/guide/harnesses.md`, the first differing line of the region, and
`python3 tools/harness_table.py --write`.

**C. Regenerate.** `python3 tools/harness_table.py --write --root $S`, then `--check --root $S`.
Expected: write exits 0 and rewrites only the text between the two `harness-table` markers (the prose outside is
unchanged: `diff` the scratch copy with `docs/guide/harnesses.md`); `--check` now exits 0. A second `--write` changes nothing.

**D. Markers are required.** In the scratch copy delete the begin marker (then try: end marker, markers reversed, a
marker twice), and run `--write --root $S` and `--check --root $S`.
Expected: each refuses with a one-line message naming the page and exits non-zero; the file is not modified by `--write`.

**E. Print without writing.** `python3 tools/harness_table.py` (no flag).
Expected: the two tables and the legend on stdout, ending with one newline; identical to the region between the markers
in `docs/guide/harnesses.md`; the page is not touched (`git status` clean).

**F. The tables match what is accepted.** Read the second table: `claude-code` is `yes` for every routing role,
`codex` is `no` for every one. Confirm one refusal:
`PYTHONPATH=$PWD python3 -P -m controller --role-harness review-plan=codex status`.
Expected: usage error naming the role, its `worker` adapter role and that `codex` declares only `['reviewer']`
(exit 2). The same pair in a settings or routing file is refused with exit 20 (stated on the page; automated in the tests).

**G. The check is part of the documentation check.** In a scratch copy of the whole repository (not the clone), edit one
cell of the table by hand and run `python3 tools/check_docs.py`.
Expected: exit non-zero with the mismatch line naming `python3 tools/harness_table.py --write`.

**H. The page reads well.** Open `docs/guide/harnesses.md`: header and back link present; "Reading the tables" explains
`reviewer.harness`, `Runs as`, `yes`/`no`; the legend lists `worker`, `reviewer`, `functional-tester`; the links
(`runtime.md#the-routing-section`, `commands.md#worker-routing`, `development.md#adding-a-harness-or-a-git-host`)
resolve. Check that `docs/README.md` has a row for the page, and that `workers.md`, `runtime.md` and `development.md`
(documentation checks, adding a harness, adding a page) mention it accurately.

**I. Automated evidence.** `python3 -m unittest tests.test_harness_table tests.test_docs`.
Expected: green (75 tests at technical approval). The registry-change test (an added Codex role flips exactly one
cell) is the proof that a later adapter change updates the page by regeneration alone.

### Known limitations / out of scope

- No model or effort column (H8), no launch-seam change (H3), no fallbacks (H9), no Codex worker or tester.
- The registry is not changed: `codex` declares only `reviewer`, so its routing column is `no` today.
- No pre-commit hook; the currency check runs in the documentation check and the test suite.
- A milestone-level close-out updates `docs/ROADMAP.md` and this file; the squash commit is `docs:` and releases nothing.
