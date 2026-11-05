# C12d: Self-recovery, part 3

Item id: `workflow-controller-c12d`.

## Roadmap row (moved verbatim)

Complexity: medium. Self-recovery, part 3: retry network failures instead of handing the run back (follow-up 14)

Needs: C12c

Section: [11.10](../roadmap-archive.md#1110-self-recovery), follow-ups 8, 9 and 14

## Planning notes

Added at A2b from [11.10](../roadmap-archive.md#1110-self-recovery): the case is a failed name lookup or an unreachable GitHub (`git ls-remote` exit 128, 2026-10-09); the Controller waits and retries with a backoff instead of ending the run. Anything else still stops. (The row cites follow-up 14, which the archive's follow-up list describes as the cross-session message of C12c; this part is the network retry only.)

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
