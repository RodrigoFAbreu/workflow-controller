# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository.

## Semi-autonomous workflow gates

Work follows the state machine in `docs/ai-workflow/MILESTONE_WORKFLOW.md`.
Claude works autonomously between gates but must stop and wait at every hard
gate that document names -- see its "Hard gates summary" for the current
count and list, which changes as the workflow evolves; do not hardcode a
count here.

Use the commands in `.claude/commands/` to drive each state. Do not skip a
gate because the diff looks small.

## Workflow documents

- Milestone state machine: `docs/ai-workflow/MILESTONE_WORKFLOW.md`
- Two-stage plan review: `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`
- Bundle mechanics and feedback format: `docs/ai-workflow/REVIEW_PROTOCOL.md`
- Phase/command reference: `docs/ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`
- Current work-item state: `docs/ai-workflow/WORKFLOW_STATE.json` (ground
  truth -- never inferred from plan text)
- Active work item narrative: `docs/ACTIVE_MILESTONE.md`

## Git restrictions

- Commits are normally prohibited. Only commit when a workflow command
  explicitly authorizes it after verification gates pass.
- Never push, merge, rebase, force-push, or open a pull request.
- Don't touch unrelated working-tree changes.

<!--
Sections above this marker are managed by workflow-manager and are replaced
on update. Add repository-specific guidance below it; it is never touched.
-->

<!-- workflow-manager:end -->

## Running tests

At a checkpoint run only the test modules the plan's `**Tests:**` paragraph
names, through `python3 tools/run_tests.py [names]`, which starts each
namespace-safe shard in an isolated PID namespace so a stray `kill(-1)` reaches
only the run (`docs/guide/development.md`, "The test runner"); never use a bare
`python3 -m unittest` for a signal-sending test, and add the repository-wide
guards when the tree changes (`docs/guide/development.md`, "Verification
policy"). The full suite is CI's on the pull request, except
`tests.test_catalogue_determinism` and `tests.test_scenarios_negative`, which
run only after the merge and can fail the release gate for an unselected
regression; run it locally at most once, at the self-review, and never two at
once. Here `/milestone-implement` step 3's "full required verification" is that
single self-review run, and `/apply-implementation-review` step 5's "full suite"
is not rerun in a fix pass, which runs its fixes' selections plus the guards.

## Controller documentation

- Documentation map (guides, ADRs, roadmap, history): `docs/README.md`
- Task and reference pages: `docs/install.md`, `docs/run.md`,
  `docs/update.md`, `docs/compatibility.md`, `docs/common-problems.md`,
  `docs/exit-codes.md`, `docs/glossary.md`, `docs/release-history.md`.
- Operator and developer guides: `docs/guide/` (`docs/guide/installation.md`
  is only a stub kept for old links). Keep the pages and guides current when
  a change alters behaviour they describe; plans and completed-milestone
  narratives are historical records and are not updated afterwards.
- `python3 tools/check_docs.py` checks links, anchors and the commands shown
  on the task pages (see `docs/guide/development.md`).
- A milestone plan declares its pull request title (see
  `docs/guide/milestone-branches.md`).
