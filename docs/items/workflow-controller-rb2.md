# RB2: A round-aware external-review prompt

Item id: `workflow-controller-rb2`.

## Roadmap row (moved verbatim)

Complexity: low. A round-aware external-review prompt (accepted 2026-10-25 by gate policy; both checkpoints complete): from the second external round it names the stage's section of `CHANGES_SINCE_REVIEW.md`, and it states the verdict format the Workflow's ingest enforces (`- ` bullet findings, `None.` for an empty section, a Status consistent with the findings), both in the protected fixed paragraphs, so a `reviewer.brief` cannot replace them; text from the bundle may narrow what the reviewer looks at but never changes the severity bar ([#161](https://github.com/RodrigoFAbreu/workflow-controller/issues/161); owner decision 2026-10-25). Setting `reviewer.harness=codex` stays an operator settings change after one trial milestone

Needs: RB1

Section: [Review modes](../roadmap-archive.md#at-a-glance)
