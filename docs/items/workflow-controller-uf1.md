# UF1: a `usage --refresh` that re-reads both the Codex and the Claude limit

Item id: `workflow-controller-uf1`.

Not yet planned. Owner decision 2026-11-19. Priority: high. Complexity: low to medium.

## Why

After the Codex weekly reset the Controller kept the stale stored 99% reading until a fresh Codex session was recorded (the operator home sessions are read). The lane restarted twice and stopped again at "codex weekly usage 99% ... reaches the 99% threshold" although the limit had reset. A tiny `codex exec` call refreshed it. The Manager lane saw the same.

## Scope

One mechanism: a `workflow-controller usage --refresh` option that re-reads both the Codex limit and the Claude limit by a cheap probe, instead of waiting for the next recorded session. The owner: "it should re-read both the codex and the claude limit after reset".

- It shows what it read and when.
- The watchdog and a restart can call it before deciding that a hold still applies. A stored reading older than the stated reset time must not keep a pause alive.
- It is read-only apart from refreshing the stored reading.
- It is documented in [the runtime guide](../guide/runtime.md) (usage section) and [Common problems](../common-problems.md).

The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
