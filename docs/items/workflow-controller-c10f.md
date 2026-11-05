# C10f: The sandbox layout for Claude Code 2.1.304 and later

Item id: `workflow-controller-c10f`.

## Roadmap row (moved verbatim)

Complexity: medium. The sandbox layout for Claude Code 2.1.304 and later: those builds block writes to an allowed folder below a denied path, so `verify-claude` refuses them with `boundary: writable: REFUSED`; lay the tester's folders out so no allowed write sits below a denied path, then verify a current build ([#129](https://github.com/RodrigoFAbreu/workflow-controller/issues/129); owner decision 2026-10-23: right after C10e) (accepted 2026-10-24 by gate policy; released as 1.19.1 from PR #137, squash `8f1b053`: the writable folders live outside the clone behind links, with a `link` probe and a fake tester that applies the deny-above-wins rule; follow-ups [83-84](../roadmap-archive.md#known-follow-ups-carried-forward), issues #138 and #140)

Needs: C10e

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
