# Harness contract fixtures

The measured `claude` stream-json contract that the Controller's worker lifecycle ownership
(`workflow-controller-worker-lifecycle-ownership`, plan
`docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md`) relies on. Measured against
Claude Code `2.1.282`, the installed `claude`, on 2026-09-25.

Every fixture is one `<name>.jsonl` (the redacted stream, one event per line) and one
`<name>.meta.json` (how it was captured). They are evidence: they are adopted byte for byte and
never re-captured, regenerated, edited or deleted to fit a design. A later re-measurement for a
new `claude` version is a separate, deliberate act outside that milestone. The SHA-256 of every
file is recorded below, and `tests/test_fake_claude_contract.py` checks it, so any change to a
fixture is a visible test failure, never a silent re-baseline.

- `capture.py` captures them. It is never run by the test suite: it launches the real, installed
  `claude` (network, real spend). `python3 tests/harness_contract/capture.py probe <name>...`
  runs probes; `... import-job <job_dir> <name>` redacts a real job's `worker.stdout`. Its probe
  definitions stay as they were when the fixtures were captured; CP1 added only P12's two.
- `p12_admission.py` holds P12's admission (`admit`) and gate (`gate_deviations`) decisions, kept
  apart from the capture script (plan CP1, round 13's I1).
- Line numbers in the plan and below are 0-based stream lines.

## Sources

The seven imported job streams are real Controller jobs' `worker.stdout` from the package-runtime
root (`~/.local/state/workflow-controller/jobs/`), print mode (`-p <task>`, stdin `/dev/null`),
redacted by `capture.py import-job`: structural events are kept verbatim (`system/*`, `result`,
`tool_use`/`tool_result` names and ids); assistant text, tool inputs' strings and tool outputs are
placeholders, so no repository content is copied.

| fixture | source | what it pins |
| --- | --- | --- |
| `job_12a9f268_applying_review_feedback` | job `20260925T113445Z-12a9f268` | an `APPLYING_REVIEW_FEEDBACK` worker whose background verification was killed at exit |
| `job_2857a730_two_results` | job `20260924T221915Z-2857a730` | two `result` events, the second a background subagent's hand-back (`origin.kind: peer`) |
| `job_5d4a976a_implementing` | job `20260925T085148Z-5d4a976a` | an `IMPLEMENTING` worker; a mid-turn completion, a Monitor killed at exit followed by one more turn, then the background suite killed |
| `job_6082a90b_self_reviewing` | job `20260925T113603Z-6082a90b` | a `SELF_REVIEWING_IMPLEMENTATION` worker whose full suite was killed at exit |
| `job_66988e17_self_reviewing` | job `20260925T111936Z-66988e17` | two background tasks killed at exit, in start order |
| `job_8b244f42_implementing` | job `20260925T100548Z-8b244f42` | parallel background tasks and a pending `ScheduleWakeup {delaySeconds: 1200}` at exit |
| `job_cb43fe49_self_reviewing` | job `20260925T115439Z-cb43fe49` | a `SELF_REVIEWING_IMPLEMENTATION` worker's background task killed at exit |
| `p1_print_mode_background_bash` | probe P1 (print mode, production model) | print mode ends the session at the first idle turn and kills the open task |
| `p2_escaped_descendants` | probe P2 (print mode, production model) | an orphaned `setsid` descendant survives the kill, keeps the ownership tag in its `environ`, does not hold the lock descriptor |
| `p2_escaped_descendants_subreaper` | probe P2 (capture process a child subreaper) | the orphan is adopted by the subreaper |
| `p3_streaming_background_bash` | probe P3 | streaming input keeps the session alive across a background task; its completion starts a turn (`origin.kind: task-notification`) |
| `p4_monitor` | probe P4 (production model) | a Monitor is a `local_bash` task; each event starts a turn |
| `p5_p11_wakeup_fires` | probes P5/P11 | two wakeup fires, each enclosed in one `command_lifecycle` `started`/`completed` pair, with no `origin` and no opening `user` event (amendment 0) |
| `p6_p10_slash_command` | probes P6/P10 | a slash command sent as the stream-json user message expands, with `--append-system-prompt` |
| `p8_task_stop` | probe P8 | a worker-initiated `TaskStop` mid-turn (`killed`/`stopped`), no notification turn |
| `p8_monitor_timeout` | probe P8 | a Monitor reaching its own timeout (`killed`/`stopped`) between turns, then a turn |
| `p9_wakeup_cancel` | probe P9 | `ScheduleWakeup {stop: true}` after a pending wakeup: `cancelledWakeups: 1`, no fire, no `command_lifecycle` event; line 8 is the harness rejecting line 7's call for leaving out `noop` |
| `p11_subagent_handback` | probe P11 (multi-agent route) | the turn after a background subagent's hand-back (`origin.kind: task-notification`) |
| `p12_stop_inside_fire_single` | probe P12 (CP1) | a stop inside ALPHA's fire turn: `cancelledWakeups: 0` |
| `p12_stop_inside_fire_nested` | probe P12 (CP1) | inside ALPHA's fire turn, BRAVO scheduled then a stop: `cancelledWakeups: 1` |

The probes ran with the production argv of the plan's design A (streaming input,
`--permission-mode auto`, effort `xhigh`, `--append-system-prompt` with the worker lifecycle
note, the full disallow list), overriding only the model: haiku, or the production model where
haiku lacks `--permission-mode auto` (P1, P2, P4; see `capture.py`). Each `.meta.json` records the
exact argv, prompt, exit status and per-line arrival offsets.

## P12 -- a stop inside a fire turn (CP1)

Captured on 2026-09-25 (`captured_at` 19:05:24Z), both probes in parallel, with `capture.py
probe p12_stop_inside_fire_single p12_stop_inside_fire_nested`. Both definitions carry
`"linger": 200`; `PROBE-DONE` is asked for only in the fire turn's reply, so the window ran 200 s
from the fire turn's `result` (`done_at` 123.7 s and 124.7 s, stdin closed at 323.9 s and
324.7 s, exit 0).

The prompts, as run (also in each `.meta.json`):

- single: "This is a wakeup contract test. Make each tool call named below exactly once, with
  exactly the arguments given and no other argument. Never retry a call and never change its
  arguments, whatever a tool returns. Step 1 (now): call the ScheduleWakeup tool once with
  exactly: delaySeconds 60, prompt 'P12 fire ALPHA', reason 'contract probe alpha', noop false.
  Call no other tool in this turn. Then reply with exactly WAITING-ALPHA and end your turn. Step
  2: when you are woken with 'P12 fire ALPHA', call the ScheduleWakeup tool once with exactly:
  stop true. Call no other tool in that turn. Then reply with exactly: PROBE-DONE In any other
  turn, call no tool at all and reply with exactly: PROBE-EXTRA"
- nested: the same, with step 2 "first call the ScheduleWakeup tool once with exactly:
  delaySeconds 60, prompt 'P12 fire BRAVO', reason 'contract probe bravo', noop false; then call
  the ScheduleWakeup tool once with exactly: stop true."

**Recaptured attempts (model mistakes):** none. Each probe's first capture was `ADOPT`, so no
capture was discarded and neither probe was run a second time.

**Adopted captures (harness observations):**

| probe | `admit` | cut point | `gate_deviations` | observation |
| --- | --- | --- | --- | --- |
| `p12_stop_inside_fire_single` | `ADOPT` | call 2 (named sequence complete) | `[]` | one bracket (lines 11 and 22) around one turn (12-21); the stop (line 16) answered at line 17 `{stopped: true, cancelledWakeups: 0}`, text "there was no pending wakeup to cancel"; nothing after `completed(X)` |
| `p12_stop_inside_fire_nested` | `ADOPT` | call 3 (named sequence complete) | `[]` | one bracket (lines 11 and 24) around one turn (12-23); BRAVO (line 16) scheduled "in 117s" (line 17), the stop (line 18) answered at line 19 `{stopped: true, cancelledWakeups: 1}`; BRAVO never fired and nothing follows `completed(X)` |

Both counts are the ones the plan's settlement rule assumes (B, "A stop inside a bracket"): the
wakeup being fired is no longer held by the harness from its `started(X)` on. Both gate lists are
empty, so CP1's gate passes and no plan amendment is triggered. P12 also measures a stop with no
wakeup pending, which the plan listed as unmeasured: it succeeds with `cancelledWakeups: 0`.

## Digests

SHA-256 of every fixture file, checked by `tests/test_fake_claude_contract.py`.

| file | sha256 |
| --- | --- |
| `job_12a9f268_applying_review_feedback.jsonl` | `48544056d70b8a6658113b965b86be0f56a48789fc765db3ede44111b521c179` |
| `job_12a9f268_applying_review_feedback.meta.json` | `45d656e553c3280dfbb5c64deb26fab975bf20f15f68edd4c1ee46ad087d6c9f` |
| `job_2857a730_two_results.jsonl` | `f6b02f4f76ee7684659efe4ddc46d56fefb7b6880dbdfa916e4af7bdbaf47ed1` |
| `job_2857a730_two_results.meta.json` | `9021db67613cfd039c0f9c273d55968308f225c9c215e6b590aee19127e69a56` |
| `job_5d4a976a_implementing.jsonl` | `753481efce58b964f11bc967428f3a2da4603267255a3b73746fb9a2dc97ffcb` |
| `job_5d4a976a_implementing.meta.json` | `11e20ecfa86bbc4b8f305d5223771ff114532d3ae13194e3343eb0891b9ecc3d` |
| `job_6082a90b_self_reviewing.jsonl` | `afbf8e75f1ce4ef3e971ca3b8c16b6bbe9caacf3b5e037727c4a712ae9fa5db8` |
| `job_6082a90b_self_reviewing.meta.json` | `f01b1bb51aa0628e8c6a01503913e7ad73705efbb67e6ac55ef108bed8bdc25a` |
| `job_66988e17_self_reviewing.jsonl` | `b699329d85b15ef3e6db1483c3fc280c762ae859ba744429aec7b1d8a9fc7838` |
| `job_66988e17_self_reviewing.meta.json` | `bce6f1de2b0b5270af10e7656a42d4562697e2cee160335ba353689f0af3f876` |
| `job_8b244f42_implementing.jsonl` | `803d658bd45f0ec1126c5739d7cc47dba9ce178f796709945e2fa68971ce5d99` |
| `job_8b244f42_implementing.meta.json` | `d036da76df385d89b4fb42f04e1d17ca172c31660a8cc1eafba2f16149fee1ec` |
| `job_cb43fe49_self_reviewing.jsonl` | `c2f220bd9899406e84cb22306cdb1946adc756cdf9c3a86e3c1caf3792a6e5c3` |
| `job_cb43fe49_self_reviewing.meta.json` | `f33fed0690ad023d95b4583daf17c2cfa295472d71c911946f61b83674422512` |
| `p11_subagent_handback.jsonl` | `683a8e4c57f94685ba1b9593d566a2280dcccbccc073d0682f7577317280a081` |
| `p11_subagent_handback.meta.json` | `5f88b82c7000e5ba58a5f1ff90d9470f09764420533b1cf0bdbe5a0d10cde121` |
| `p12_stop_inside_fire_nested.jsonl` | `e78a92e34340dcbf5ae3ae033fb5333e17682e06f6a67db1f20e97709f3757c3` |
| `p12_stop_inside_fire_nested.meta.json` | `8775abe5149697877df090ffd3177c52185908787762a5820d054c4c9294ce36` |
| `p12_stop_inside_fire_single.jsonl` | `61b010f99f52d93168c09de17f87a00a82a23be99032fc8925afbcd2811d60cd` |
| `p12_stop_inside_fire_single.meta.json` | `0dbff26d08fa4db57e5fc366ac8a5a915f5813474e5f6a651d673a6aaf40a123` |
| `p1_print_mode_background_bash.jsonl` | `5c1afe8564fe00db27cc680a002f251c30e3786040a7b9731a8c8231c66f4d7f` |
| `p1_print_mode_background_bash.meta.json` | `1c28502d76767fb739f692fd1a84610b1044732ba7235044018c54eef44ebb19` |
| `p2_escaped_descendants.jsonl` | `f36a082a69960358ab1768c71b4f901d11bf61fdf068346eaac9fa77a8156953` |
| `p2_escaped_descendants.meta.json` | `52879001eaf6d27ef9532110a46d48491176d878ed37b770671d0792a4b57047` |
| `p2_escaped_descendants_subreaper.jsonl` | `ab2e4c7ea65653a31a457cc60179f9bb214e07f4410f1795dd8e52d044231532` |
| `p2_escaped_descendants_subreaper.meta.json` | `88e91c0c04b9d8032f0058b2adfc2ff8bf5a9b75097bb0a196035e679c3371a3` |
| `p3_streaming_background_bash.jsonl` | `8406ed3e9345836313a1dc57603218930d6288225cef9feb2a02320b9dcbeaab` |
| `p3_streaming_background_bash.meta.json` | `a2b33808cc894a6b1e5d8d5ea7d6db215542e6a7a1736e664432aa37f93a2ed3` |
| `p4_monitor.jsonl` | `51402e84b005ce188dd730b42a58af9638076b16bf720b7ac7b878f6362b9fbe` |
| `p4_monitor.meta.json` | `90d1eac9baec49b71609f0edf8e84e68a5f14808fee4dc29b08cbae1f417260d` |
| `p5_p11_wakeup_fires.jsonl` | `3040f40430e015660474e8b934ece932998b623d57dbbddb38459f5366e37b5b` |
| `p5_p11_wakeup_fires.meta.json` | `5a01d8875a6fd15575de7a8abd8a55a1cd80fd6d19fabe63ddef54efef05e74d` |
| `p6_p10_slash_command.jsonl` | `24c82e9365dc096c1aeb9e36ffbe2e3ff205cc628f19627065d211b994e0d728` |
| `p6_p10_slash_command.meta.json` | `dac83c2766f43ea9f668c76532b70e141fdc2563cd7720ccd1927e49bdf75629` |
| `p8_monitor_timeout.jsonl` | `a3382305f79a91fbf81ef77c9af733147bdfd8a2abdf9319f102cdb04ecb7d1a` |
| `p8_monitor_timeout.meta.json` | `163ce52393ba96346dd47e8fc15bda2b927e1897a882f3601de3581fdf83bb09` |
| `p8_task_stop.jsonl` | `5cb9cd3dbf5e5cde44b88f4c753d1a14140e34838afc767a71cc55435d7bea7b` |
| `p8_task_stop.meta.json` | `62ae715b0f5f66b4553a4948b1ff55b88dd75bcca5d08ab27be8b3d1a92546b3` |
| `p9_wakeup_cancel.jsonl` | `9836617049ecd2a6dd6beddf017900ba4b8cc309d2843bd57d2c10fe71b913db` |
| `p9_wakeup_cancel.meta.json` | `6c2e0312c2810e47ba096208b6413a52bae4fdbc9036f17e4d596317d17a2b7b` |

## Replay through the fake harness

`tests/test_fake_claude_contract.py` replays every fixture through `tests/fake_claude.py`'s
streaming-input mode (`FAKE_CLAUDE_TURNS`, a turn script translated from the fixture) and compares
the projected event sequences: `(type, subtype)`, plus `state` for `command_lifecycle` and
`is_error` for a `user` `tool_result`, after dropping the six non-structural kinds
(`system/thinking_tokens`, `rate_limit_event`, `system/task_progress`, `tool_progress`,
`system/commands_changed`, `system/vcs_state_changed`) from both sides.

One fixture is outside the fake's model and is not replayed: `job_2857a730_two_results`. Its
background subagents stream their own events interleaved with the session's (365 events with a
`parent_tool_use_id`, subagent-owned tasks, `SendMessage`), and its last two `result` events
follow each other with no turn between them (lines 1550-1551, the second a `peer` hand-back). The
fake plays a subagent as a hand-back turn (`subagent_handback`), which is what the Controller
owns; the fixture still takes part in every other assertion (digest, no `command_lifecycle`
event), and CP2 runs the stream state machine over it.
