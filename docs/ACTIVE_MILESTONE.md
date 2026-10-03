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
- CP4 guide reorganisation, CP5 entry points and verification: not started.

Earlier milestones' narratives are archived under `docs/milestones/completed/`. The last one,
`workflow-controller-orchestration-protocol-v1` (1.7.0), is released.
