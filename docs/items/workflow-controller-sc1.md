# SC1: Scenario tests no longer error on a machine whose sandbox probe passes

Item id: `workflow-controller-sc1`.

## Roadmap row (moved verbatim)

Complexity: low. Scenario tests (a `test:` change that releases nothing, [#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188)): three scenario tests error on any machine whose sandbox probe passes since `.claude/settings.json` was added (#171), because the Workflow's implementation-stage classification does not list the path; CI skips them, so it stays green. The scenario fixture now measures from the checkout's own `HEAD` instead of an old pinned base commit, and a classification guard that needs no sandbox runs on every machine, CI included, naming any fixture file the declaration does not cover; classifying `.claude/settings.json` in the Workflow's default declaration template belongs to the Workflow repository (found by the H2 functional round, 2026-10-27)

Needs: -

Section: [Clean-up milestones](../roadmap-archive.md#at-a-glance)
