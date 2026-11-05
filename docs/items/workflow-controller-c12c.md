# C12c: Self-recovery, part 2

Item id: `workflow-controller-c12c`.

## Roadmap row (moved verbatim)

Complexity: medium. Self-recovery, part 2: survive a message from another session (follow-up 9)

Needs: C12b

Section: [11.10](../roadmap-archive.md#1110-self-recovery), follow-ups 8, 9 and 14

## Planning notes

Added at A2b from the issue [#27](https://github.com/RodrigoFAbreu/workflow-controller/issues/27) (follow-up 9 and follow-up 14 in the archive). The issue's fixes, any of: launch workers so they take no cross-session message; or classify a mid-turn `command_lifecycle` start whose command completed before the terminal result as benign, verifying the expected phase and reporting success with a warning instead of `FAILED`; and record the classifier's `reason`, secondary reasons and lifecycle anomalies in the `completed` event and the run log line. The archive's follow-up 14 gives the order: (1) merge `"crossSessionInbound": "refuse"` into the `--settings` JSON the Controller passes (not `--bare`); (2) when the outcome is ambiguous only because of a mid-turn lifecycle event, check whether the expected phase was reached and finish the job if so; (3) record the diagnosis.
