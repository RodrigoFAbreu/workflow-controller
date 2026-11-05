# C10e: A command that verifies a new `claude` build for the functional tester

Item id: `workflow-controller-c10e`.

## Roadmap row (moved verbatim)

A command that verifies a new `claude` build for the functional tester: it runs the live sandbox enforcement probes against that binary and, only when every probe holds, records its digest in the Controller's state (never the settings file); the launch admits the built-in list plus the recorded digests, so a Claude update needs no release ([#113](https://github.com/RodrigoFAbreu/workflow-controller/issues/113); owner decision 2026-10-22: right after C2b) (accepted 2026-10-23 by gate policy; released as 1.19.0 from PR #128, squash `2375b51`: `workflow-controller verify-claude`; the probe and the launch use one resolved binary, #114; follow-ups [81-82](../roadmap-archive.md#known-follow-ups-carried-forward), issues #132 and #133)

Needs: C10c

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
