# Active Milestone

## Status

**In progress.** `workflow-controller-documentation-reorganisation` (`docs/ROADMAP.md` step D1, section
11.8), plan revision 5 approved at `5d6bcc3`, base commit `914d8f4`. Pull request title:
`docs: reorganise the guides, add task pages and two offline documentation checks`. A `docs:` pull
request releases nothing. Plan: `docs/ai-workflow/CONTROLLER_DOCUMENTATION_REORGANISATION_PLAN.md`.
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status.

## Checkpoints

- **CP1 complete.** `tools/check_docs.py` (links and anchors with GitHub slugs, cross-repository link
  form and host allow-list, command and flag check against `controller.cli.build_parser()` through
  recording no-op terminating actions and a relaxed parser, page-header and internal-id assertions,
  exit-table, digest and installation-stub rules) and `tests/test_docs.py` (the repository is clean;
  every rule good and bad on synthetic trees; the parser must not run a process or resolve a runtime;
  agreement with `tests/test_plan_document_consistency.py` on what an invocation is). The page lists
  are constants with the checkpoint that activates each (`ACTIVE_THROUGH` is 1, so no user page is
  checked yet). `python3 tools/check_docs.py` is clean on the existing documentation.
- **CP2 complete.** The four reference pages with the user-page header: `docs/exit-codes.md` (one table of
  every exit status plus 130, equal to the `EXIT_*` constants and `SIGINT_EXIT_STATUS`, and to ADR 0001
  without 130), `docs/glossary.md` (the Workflow-generic terms, one heading each, then a separate Controller
  group, as agreed with the other lane), `docs/compatibility.md` (Controller to Workflow table and the two
  2.6.0 digests) and `docs/release-history.md` (1.1.1 to 1.7.0, linking the notes from 1.3.0).
  `ACTIVE_THROUGH` is now 2, so the four pages are header- and id-checked; the synthetic-tree tests pin
  `through=1` so they do not move with it. Links to `update.md` and `common-problems.md` are left out until
  those pages exist (the link check is ungated); the compatibility page points at the installation guide's
  section for moving a target until CP3.
- **CP3 complete.** The three task pages `docs/install.md`, `docs/run.md` and `docs/update.md` (header,
  goal, prerequisites, numbered steps, what you should see, if it fails) and the README quick start, which
  replaces its Install and First run sections. `ACTIVE_THROUGH` is now 3, so the pages are header-, id- and
  command-checked (README commands from this checkpoint). Manual run against a scratch Git repository
  (`git init`, empty commit, throwaway runtime root and settings file) with the checkout's Controller 1.7.0:
  `--version`, `status`, `settings path` and `settings show` succeed with the output the pages describe;
  `follow .` reports nothing active; `inspect`, `explain` and `step` on the unmanaged repository refuse with
  exit 20 and name the missing `.workflow-manager/installation.json`, as `run.md` says. A managed repository
  was not built, so `workflow-manager update` and the pipx and download commands were validated by the
  command check and `workflow-manager --help` only, not executed. The Controller has no usage-limit pause, so
  `run.md` documents `--max-steps` as the bound. The pages' "if it fails" links go to the troubleshooting
  guide until `common-problems.md` exists (CP4 repoints them).
- **CP4 complete.** `docs/guide/installation.md` is the three-heading stub (the six inbound links in
  `docs/adr/` and `docs/releases/` still resolve); its install-from-a-checkout text moved to
  `development.md`, the rest to the task and reference pages. `concepts.md` became `how-it-works.md` with
  its glossary left to `docs/glossary.md`. `docs/common-problems.md` is new. The task pages' "if it
  fails" links, `exit-codes.md`, `compatibility.md`, `run.md`, the README and the documentation map now
  point at the new pages. Troubleshooting and the commands guide link `exit-codes.md` instead of
  repeating the table, and the compatibility text there links `compatibility.md`. Cut: the "What changes
  from 1.6.0" section, this repository's cutover story and per-release sentences; the
  milestone-branches preamble has a title and an intro. Every guide carries the user-page header, and
  `ACTIVE_THROUGH` is now 4, so the guides and `common-problems.md` are header-, id- and (for
  `common-problems.md`) command-checked. `tools/check_docs.py` and the documentation tests are clean.
- **CP5 complete.** `README.md` is rewritten around the pages: the user-page header, one paragraph on
  the Controller, the shared three-sentence "how the pieces fit" paragraph (each piece linking its
  repository's `#readme`, the same text the other two repositories will carry), the quick start, an
  "I want to ..." table of the task and reference pages and the lifecycle diagram (the shipped SVG
  until the Workflow repository's overview page exists), and short automation, milestone-branch,
  problem and development sections (the five-row exit table is replaced by links; the `validate.yml`
  job names and the documentation checks are under Development). `docs/README.md` carries the header,
  an "I want to ..." map, the reference and maintainer guides, and the release, ADR and history
  indexes (the history table now lists every completed milestone). `CLAUDE.md` names the task and
  reference pages, the installation stub and the documentation check. `development.md` gains
  "Documentation checks". `ACTIVE_THROUGH` is now 5, so every user page is checked. Verification:
  `python3 tools/check_docs.py` clean; `python3 -m unittest tests.test_docs
  tests.test_plan_document_consistency` 82 tests OK; `python3 tools/ci_workflows.py --check` clean;
  `python3 tools/run_tests.py` 3052 tests, 7 shards PASS, coverage exact; the diff from `914d8f4`
  touches nothing under `controller/`, `.github/`, `.workflow-controller/`, `docs/adr/`,
  `docs/releases/`, `pyproject.toml` or `setup.py`.

## Functional review checklist

Documentation-only milestone (`docs:`; nothing under `controller/` changed). Review as a first-time reader
would. Evidence below was measured on 2026-10-04 at implementation head `1f58853` (plus state commits) with the
checkout's Controller 1.7.0. Findings go to `.ai-review/workflow-controller-documentation-reorganisation/feedback/FUNCTIONAL_REVIEW.md`.

### Setup

Work from the repository root on branch `milestone/workflow-controller-documentation-reorganisation`. No build is needed.
For the command checks use a scratch repository and throwaway runtime and settings roots, never your real ones:

```bash
S=$(mktemp -d); C=$PWD
git init -q $S/repo && git -C $S/repo -c user.email=a@b -c user.name=x commit -q --allow-empty -m init
export PYTHONPATH=$C XDG_CONFIG_HOME=$S/cfg
cd $S/repo
ctl() { python3 -P -m controller --runtime-dir $S/rt "$@"; }
```

### A. The two automated checks (run from the repository root)

| # | Command | Expected (measured) |
|---|---|---|
| A1 | `python3 tools/check_docs.py` | no output, exit 0 |
| A2 | `python3 -m unittest tests.test_docs tests.test_plan_document_consistency` | `Ran 93 tests ... OK` |
| A3 | `git diff 914d8f4 --stat -- controller .github .workflow-controller docs/adr docs/releases pyproject.toml setup.py` | empty (no protected path touched) |

### B. Commands shown on the task pages (scratch repository, `ctl` from Setup)

The scratch repository is not Workflow-managed, so commands that need a managed repository refuse as `run.md` says.

| # | Command | Expected (measured) |
|---|---|---|
| B1 | `ctl --version` | `workflow-controller 1.7.0`, then a `runtime: source (...)` line, exit 0 |
| B2 | `ctl status` | `jobs: none`, `handoff: none`, `active: none`, a `runtime root: ... (ladder row 1)` line, exit 0. Note: `install.md` says a new machine prints `no Controller runtime state at <root> (ladder row 1)`; that text appears only when the runtime root does not exist yet (first run with a fresh `$S/rt`) |
| B3 | `ctl settings path` | `$S/cfg/workflow-controller/settings.json`, exit 0 |
| B4 | `ctl settings show` | `settings file: ... (not created yet)`, then `run.max_steps = 20 (default)` and the other keys, exit 0 |
| B5 | `ctl follow .` | `nothing active for <repo>; no runs recorded`, exit 0 |
| B6 | `ctl inspect .`, `ctl explain .`, `ctl step .`, `ctl run --max-steps 1 .` | each: `error: <repo> has no .workflow-manager/installation.json -- not a Workflow-managed repository`, exit 20 |
| B7 | `workflow-manager --help` | usage text, exit 0 |

Not executed (need a release download, pipx or a managed repository): the `curl`/`gh release download`, `sha256sum -c`,
`pipx install`, `pipx uninstall`, `workflow-manager update`/`verify` and `git switch -c chore/workflow-update` commands.
They are covered by `check_docs.py` (command and flag check) only; read them for correctness against the real tools.

### C. Read-through, as a first-time reader

For each page, check: header line ("For: ... Last checked with: ..."), the steps make sense in order, every link
works (also click a few anchors), and nothing refers to a page or section that no longer exists.

1. `README.md`: what the Controller is, how Workflow, Workflow Manager and the Controller fit together, quick start, "I want to ..." table. Can you get from zero to a first run using only this page and the pages it links?
2. `docs/README.md`: the map; every row leads to the page it names; the release and ADR tables list what exists.
3. `docs/install.md`, `docs/run.md`, `docs/update.md`: goal, prerequisites, numbered steps, "what you should see", "if it fails". Steps match B1-B7 where run.
4. `docs/common-problems.md`: each problem has a symptom, a fix and a link to the long account in `guide/troubleshooting.md`; anchors resolve.
5. `docs/glossary.md`: one heading per term; Workflow-generic terms first, then the Controller group; terms used on the task pages link here.
6. `docs/compatibility.md`: Controller-to-Workflow table, the two 2.6.0 digests; `docs/exit-codes.md`: one table (0, 2, 10, 15, 16, 20, 30, 35, 40, 45, 50, 130) and each "what to do" is actionable (compare with `python3 -c "import controller.cli as c; print([n for n in dir(c) if n.startswith('EXIT_')])"`).
7. `docs/release-history.md`: every release 1.1.1 to 1.7.0, one line each, notes linked from 1.3.0.
8. Reorganised guides under `docs/guide/` (`how-it-works.md`, `commands.md`, `automation.md`, `workers.md`, `runtime.md`, `milestone-branches.md`, `troubleshooting.md`, `ci-and-releases.md`, `development.md` including "Documentation checks"): headers present; no duplicated exit table; `installation.md` is only a three-heading stub whose old inbound links (ADRs, release notes) still land somewhere sensible.
9. `CLAUDE.md` names the task pages, the stub and the documentation check.

### Functional review round 1 (applied)

Findings 1 (install.md `status` row and `controller:` line), 3 (glossary links in update.md), 4 (`VERSION` in update.md), 5 (rollback below 1.7.0), 6 (gate-policy wording in run.md and glossary), 7 (exit 130 row), 8 (README route to Workflow Manager) and a one-sentence note for 9 (development.md) were fixed in documentation only, in one bounded commit. Finding 2 is a checklist wording note: B2's expected cell is the second-call output (the first `ctl status` on a fresh `$S/rt` prints the `no Controller runtime state` form; `install.md` now says the row depends on the root). Finding 9's `ACTIVE_THROUGH` gating in `tools/check_docs.py` is left as is (tool change, not wording). Re-test: install.md B2 line, update.md, run.md gate sentence, exit-codes 130 row, README step 2.

### Known limitations and out of scope

- Controller code, ADRs, release notes and CI are untouched; the lifecycle diagram is still the shipped SVG until the Workflow repository's overview page exists.
- Cross-repository links point at the other repositories' `#readme`; they are checked for form and host only, offline.
- The managed-repository, pipx and download commands were not executed (see B).
- A `docs:` pull request releases nothing.

## Self-review

No Blocking findings. Fixed: the glossary regained the "Pull request title" and "Runtime kind" terms and
the `reconcile` half of "Workflow mode" that the old guide's glossary carried; the exit-code page's row 20
names a failed protocol operation again; the settings-file fix on the common-problems page names the four
commands that create a fresh file; a miscount in the troubleshooting guide's exit-code section; and the
installation-stub rule now counts headings of every level below the title, with a test for an added `###`.
Verification after the fixes: `python3 tools/check_docs.py` clean; `python3 -m unittest tests.test_docs
tests.test_plan_document_consistency` 82 tests OK; `python3 tools/ci_workflows.py --check` clean;
`python3 tools/run_tests.py` 3052 tests in 7 shards PASS, coverage exact; the protected-path diff from
`914d8f4` is empty.

Earlier milestones' narratives are archived under `docs/milestones/completed/`. The last one,
`workflow-controller-orchestration-protocol-v1` (1.7.0), is released.
