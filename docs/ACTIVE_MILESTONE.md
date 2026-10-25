# Active Milestone

## Status

**In progress.** `workflow-controller-reviewer-brief` (RB1, issue #150), plan revision 2 approved. CP1 (the
`reviewer.brief` setting, the prompt and the wiring) is complete and verified: the narrow modules, the golden check
and the full suite pass except two Git 2.56 trailer tests that also fail on the base commit
(`tests.test_release_notes.TrailerRuleTest`, `tests.test_pull_request_lifecycle.SquashReleaseNotesTest`). CP2 (the
guides and the roadmap) is complete: the guides describe `reviewer.brief`, and the roadmap marks RB1 done. Self-review
fixed two points: the ignored-brief warning is written once per invocation (it was once per reviewer helper and per
step, against D2's "one warning"), and the control-character refusal also covers the C1 range (`\x80`-`\x9f`).
Implementation revision 1 is in implementation review. The plan is `docs/ai-workflow/CONTROLLER_REVIEWER_BRIEF_PLAN.md`.

## Next action

The implementation review of revision 1 (local, then external).
