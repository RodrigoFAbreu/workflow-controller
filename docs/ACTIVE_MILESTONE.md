# Active Milestone

## Status

**Complete.** `workflow-controller-codex-tester-fallback` (C10g-3c2, issue
[#236](https://github.com/RodrigoFAbreu/workflow-controller/issues/236)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_FALLBACK_PLAN.md`. A `functional-tester` chain longer than one is walked before the
spawn (`codex_tester.selection_error`, `job._tester_probe`), a Codex tester has a `usage_record` from its rollouts and a
`telemetry` block, and `tests/test_codex_tester_live.py` has a second opt-in test through `_functional_launch`. The first
real round (31 October 2026: `gpt-6.1-sol`, effort `high`, `job.execute_step` on a scratch item at row 38e) ran to
`findings` with a correct report, credentials `unchanged`, the sandbox removed, 12,594 input, 46,080 cache-read, 1,061
output and 93 reasoning tokens in `usage_record` and a `telemetry` block with harness `codex`; no defect was found. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-routing` (C10g-3c1, issue
[#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223)) is implemented (CP1 to CP3 and the functional review) and
accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_ROUTING_PLAN.md`. Routing accepts `codex` for the
`functional-tester` role (the model `gpt-6.1-sol` and an effort `high` or `xhigh` named, no Codex defaults), and
`job._run_functional` runs the launch, usage provider, credential and record fields of the route's harness;
`cli._functional_options` carries both harness homes and binaries. The guides, the table and the roadmap state it in place. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-surroundings` (C10g-3b2b, issue
[#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)) is implemented (CP1 to CP3 and the functional
review) and accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_SURROUNDINGS_PLAN.md`. Codex declares the
tester role with `verify-codex`, routing refuses the pair until C10g-3c, and `codex_tester.launch_codex_functional` adds the
build gate, the before/after snapshots, the binding check and the refusal mapping, with `tests/test_codex_tester_launch.py`;
the guides, the generated table and the roadmap state the new facts in place. Nothing calls the launch until C10g-3c; the
change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3, the self-review fixes and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Functional review checklist

Item `workflow-controller-codex-tester-fallback`, implementation revision 3. Every flow is offline and unit-level (fakes
only: no network, no real `gh`/`claude`/`codex`/`ssh`). Run from the product clone with `TMPDIR` set to a writable scratch
directory, one module at a time, `python3 -B` and no `PYTHONPATH=.`.

**Setup.** `export TMPDIR=<scratch dir>`. No feature flag or data to seed; the tests build their own fixtures.

| # | Flow | Command | Expected |
|---|---|---|---|
| 1 | Chain selection before the spawn (R1, R2, R3) | `python3 -B -m unittest tests.test_functional_select` | All pass: a one-entry chain takes today's path (no probe, no `selection` key); a longer chain skips an entry for a selection-time cause (missing Codex options, no binary, no `auth.json`, unverified build); all entries unavailable ends at `functional_tester_unavailable` with one `GATE_BLOCKED` record and no usage token; all held returns the usage hold; the chosen entry's `worker_route.selection` carries `position`, `length`, `skipped`; a started tester is never retried on the next entry; `worker_families` and the reviewer choice are unchanged. |
| 2 | Codex tester route and usage figures (R4) | `python3 -B -m unittest tests.test_functional_codex_route tests.test_codex_tester_launch tests.test_telemetry_harness` | All pass: a launched Codex tester has `usage_rollouts`, a `usage` record built from them and a `telemetry` block with harness `codex` (problem `no_stream`); a Claude tester's record is byte for byte unchanged and a Claude chain tester carries the selection dimensions in its telemetry (EXT-001). |
| 3 | Neighbouring modules unchanged | `python3 -B -m unittest tests.test_route_select tests.test_review_mode` | All pass. |
| 4 | Documents (R5) | `python3 -B tools/check_docs.py` and `python3 -B tools/harness_table.py --check` | Both exit 0; `docs/guide/automation.md`, `harnesses.md`, `runtime.md`, `workers.md` and `docs/compatibility.md` state the chain walk and the Codex tester's usage/telemetry in place. |
| 5 | Read the recorded first Codex round | Read the Status section above | 31 October 2026 round on `gpt-6.1-sol`, effort `high`: `findings`, credentials `unchanged`, sandbox removed, tokens in `usage_record`, telemetry harness `codex`, no defect. |
| 6 | Live Codex round (operator-run, optional) | `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_tester_live` | Needs real `codex`, `bwrap` and a recorded `verify-codex` build: outcome `ran`, no breach, sandbox removed, `usage_rollouts` non-empty. |

**Known limitations / out of scope.** `tests.test_functional_sandbox`, `tests.test_functional_lifecycle` and `tests.test_cli`
need user namespaces or a writable `/tmp` and are operator-run; `test_functional_sandbox` has a known CI flake (#206). The
full suite is not a flow. The Codex tester's telemetry row shows tokens/cost as unavailable (they are in its `usage`
record). No new setting, option, exit code or gate. The Codex worker (H4a to H4g) is out of scope.

## Next action

`/milestone-plan` for H4a (the Codex worker), the next incomplete milestone in `docs/ROADMAP.md`.