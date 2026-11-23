# UF1: a `usage --refresh` that re-reads both the Codex and the Claude limit

Item id: `workflow-controller-uf1`.

Status: implemented on the milestone branch (three checkpoints); plan: [CONTROLLER_UF1_PLAN.md](../ai-workflow/CONTROLLER_UF1_PLAN.md) (revision 8). The item stays `next` until acceptance. Owner decision 2026-11-19. Priority: high. Complexity: low to medium.

## Why

After the Codex weekly reset the Controller kept the stale stored 99% reading until a fresh Codex session was recorded (the operator home sessions are read). The lane restarted twice and stopped again at "codex weekly usage 99% ... reaches the 99% threshold" although the limit had reset. A tiny `codex exec` call refreshed it. The Manager lane saw the same.

## Scope

One mechanism: a `workflow-controller usage --refresh` option that re-reads both the Codex limit and the Claude limit by a cheap probe, instead of waiting for the next recorded session. The owner: "it should re-read both the codex and the claude limit after reset".

- It shows what it read and when.
- The watchdog and a restart can call it before deciding that a hold still applies. A stored reading older than the stated reset time must not keep a pause alive.
- It is read-only apart from refreshing the stored reading.
- It is documented in [the runtime guide](../guide/runtime.md) (usage section) and [Common problems](../common-problems.md).

The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Live check of the probes

Run at the end of CP1 on 2026-11-19 against the installed builds (claude 2.1.318, codex-cli 0.167.0), with `usage_probe.refresh(["claude", "codex"])` and a scratch runtime root. Both probes returned `ok`.

- **Claude.** The installed build accepted `--tools ""` and `--max-turns 1` under the pinned flag set and emitted a `rate_limit_event` (five-hour 49%, weekly 9%).
- **Codex.** The installed build accepted the pinned `--strict-config` set with `-s read-only --skip-git-repo-check -C <throwaway>`. Its compose command answered `config/read` with no refused layer, and a rollout carrying `rate_limits` was written in the private home under the D2 command line and home (five-hour 30%, weekly 9%).
- Both credential copies ended `unchanged`, and no probe layout remained.
