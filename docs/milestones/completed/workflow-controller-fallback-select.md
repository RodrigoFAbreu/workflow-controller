# H9b: select a role's fallback chain entry before a worker is spawned (Revision 2)

Work item: `workflow-controller-fallback-select`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `c80100bee65611a4772a6a63be0c8cec7dbb0f2b` ("docs: link the H9b issue from the roadmap row and the next action (#203)"), the tip of `main`, passed explicitly as `/milestone-plan c80100b…`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H9b, next after H9a (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#202](https://github.com/RodrigoFAbreu/workflow-controller/issues/202).
Released baseline: `workflow-controller 1.22.2`.
Pull request title: `feat: select a role's fallback chain entry before a worker is spawned (H9b, #202)`

## Goal

H9a made a role's fallback chain loadable and resolvable (`RoutingOptions.chain(role)`) but nothing reads it. This milestone makes the worker launch **choose** an entry: it walks the chain in order, before a worker is spawned and never after, skips an entry that is unavailable or held by a limit, launches the first entry that is neither, records which entry it chose and what it skipped, and, when no entry can be launched, behaves exactly as a single-entry role does today.

## The definition of "unavailable or held by a limit" (the open question of the row)

The row asks for a narrow, testable definition built from detection that exists. Three facts exist, and only some of them can support a choice.

- **Held by a limit = the usage gate holds the entry's provider.** `usage.admit` evaluates one provider's account against two windows, `five_hour` and `weekly` (`usage.WINDOWS`), plus the run and repository caps of that provider. The Claude reading maps only `five_hour` and `seven_day` (`usage._CLAUDE_WINDOW_KEYS`); a model-specific window name such as an Opus-only weekly limit is not read, and the mid-job fact of C8b (`usage_limit`, `rate_limit_type`, `window`) carries the stream's `rateLimitType` string but is acted on only after a worker has already run. So the existing detection can say "the Claude account is held until T" and "the Codex account is held until T"; it **cannot** say "this model is held, that one is not". The plan therefore does not pretend otherwise: a hold of provider P holds **every** entry whose harness runs on provider P, and a later entry on the same account is never tried because of a hold (D3).
- **Unavailable = the entry's launcher cannot be resolved.** The only pre-spawn check the worker launch makes today is `worker_launch.for_harness(route.harness)`, which raises `HarnessRoleError` for a harness that is not registered, does not declare the worker role, or declares it with no launcher registered. That is the whole of "unavailable" for a worker. A binary or build check (`verify-claude`, the build identity) is **not** made for a worker launch (`harness.CLAUDE_CODE.build_verification` admits the functional-tester launch only), so it is not part of this definition; the tester's own use of the chain, with its build check, is C10g-3. A model refused at load never reaches the chain (H9a, D4).
- **A worker that fails after it started is never retried on another entry.** A job whose worker ran and failed, or was interrupted by a usage limit (C8b), ends as it does today; the next `step` or `run` re-enters the boundary and selects from entry 0 again. That is a new job, not a mid-job change.

A held **account** (every entry on the held provider) stays today's behaviour: the hold is returned, and the caller waits for the reset and re-enters the boundary.

## Decisions

Each decision states one principle.

- **D1. Selection happens once, before the record exists, at the one launch call site.** It replaces the two lines of `_execute_step_locked` that resolve the launcher and call the usage gate, and only when the role has a chain. A role without a chain (`len(chain) == 1`: every role whose configuration names no `fallback`) takes the existing lines unchanged, so its behaviour, records and tests are byte-identical. A role whose configuration already names a `fallback` (it loads since H9a) is a chain role: its records gain the `selection` block, and the release text says so. Nothing after the record is written re-selects: `resume`, the drain path and the C8b interruption read the recorded `worker_route`.
- **D2. The selection is a pure function over injected probes.** `route_select.select(chain, unavailable, admit)` takes the resolved chain, a function that says whether an entry's launcher is unavailable (an error or `None`) and a function that admits an entry against the budget (a token, or a `Hold`), and returns a `Selection`. It reads no file, clock or registry itself, so the limit-fact, fake-clock and every-held cases are table tests.
- **D3. A hold skips the whole provider, once.** Entries are tried in order. An entry whose launcher is unavailable is skipped (cause `launcher_unavailable`). Otherwise its provider (`usage_provider(harness_name)`, derived from the adapter's `family` as the review job derives it at `job.py:6959`: `anthropic` is `claude`, any other family is `codex`; no second map to keep equal to the registry, and an adapter added by a patched registry never trips it; a harness name `harness.get` does not know raises its `UnknownHarnessError`, the registry's own refusal, rather than a new error) is admitted through the gate; a `Hold` skips the entry (cause `usage_held`) and marks the provider held, and every later entry of a held provider is skipped with the same cause **without a second admission**, because the readings are account-wide and a different model's forecast is not a model-specific limit. A later entry of another provider is admitted normally. The first admitted entry is chosen and holds the only reservation; a skipped entry writes none.
- **D4. When no entry can be launched, today's outcome is returned.** If any entry was held, the result is a hold, and the hold reported is the one with the earliest `resume_at` among the waitable ones (the caller re-enters the boundary after the wait and reselects, so the earliest end of any held account is the right wake-up), else the first hold (a cap is not waitable). Two waitable holds with equal `resume_at` resolve to the earlier chain position. A waitable hold always carries a `resume_at` (the budget returns `waitable` only with one); should a hold report `waitable` with `resume_at None`, it is treated as not waitable. For a single entry that is the one hold, as today. If no entry was held (every launcher unavailable), the **first** entry's `HarnessRoleError` is raised, exactly what `for_harness` raises today. No new status, exit code or gate: the existing `UsageHold` path and its exit codes carry the result.
- **D5. The record gains one optional, additive block; no existing field changes.** `worker_route.selection` is written only when the role has a chain: `{"position": <index of the chosen entry>, "length": <entries in the chain>, "skipped": [{"position": <index>, "harness": <name>, "cause": "launcher_unavailable"|"usage_held"}, ...]}`. `worker_route` stays the chosen entry's `to_record()`, so every reader of `role`, `model`, `effort`, `harness`, `sources` is unchanged and a chain whose first entry is chosen differs from a no-chain record only by the block. The job `SCHEMA_VERSION` stays 1 (the existing rule for additive blocks: readers tolerate absence). The closed set of causes is the two above.
- **D6. Telemetry reads the block; it also stops hard-coding the harness.** `telemetry.dimensions` gains `chain_position` and `chain_skipped` (the causes in order) **only** for a record with a `selection` block, so every existing record and every golden stays unchanged. For a **worker-kind** record (not a courier review record, `is_courier_job`) it also reads `harness` from `worker_route` and falls back to the worker adapter's name, a correction a second worker harness needs (a fallback to another harness would otherwise be reported as `claude-code`); every worker record written today carries `claude-code` or no `harness`, so its value is identical. A courier (Controller external-review) record keeps today's hard-coded value: its `worker_route.harness` names the reviewer (`codex` for a Codex review), and reporting it is H11's decision, not this milestone's, so no existing row's harness changes. H11 reads these.
- **D7. The independence and family rules hold for the chosen entry by construction.** H9a counts every chain entry in `review_mode.worker_families` and in `mixed_lane_reason`, so any entry the selection can choose was already part of the family verdict that allowed the review mode. Selection adds no family check; a test asserts that the chosen entry's family is always in `worker_families`.
- **D8. No new setting, option, gate code or exit code.** A role without a chain is the "off" state. The reviewer and tester launch sites (`_run_review_job`, `_run_functional`) are untouched; the tester's fallback is C10g-3, which calls `route_select`.
- **D9. Released as `feat:`, and the release is honest about its reach.** The pull request is a `feat:` as the roadmap row says (it activates the chain and adds a recorded block), a minor release. With the harness registry as shipped only `claude-code` takes the worker role and all its entries share one provider, so a chain of Claude entries records `position 0` and, when the Claude account is held, waits exactly as today; a later entry is chosen only once a second worker harness exists (H4). The guides say so plainly. Tests use a patched registry with a second worker harness, as H2, H9a and H10 did.

## Out of scope

The Codex tester's own fallback and its build check (C10g-3), a Codex worker (H4a to H4g), any change to the usage-limit detection, the budget or the C8b interruption, model-specific limit detection (named as a limit of the present detection above, not worked around), a retry after a worker started, the review modes, `harness.models`, any new setting, option or gate.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The pure selection and its tests | - | 2 | 1 |
| CP2 | Selection at the launch call site, the record block and telemetry | CP1 | 3 | 1 |
| CP3 | The guides and the roadmap | CP2 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The pure selection and its tests

**Files:** `controller/route_select.py`, `tests/test_route_select.py`

D2 to D4, D7. A new module `controller/route_select.py`: the constants `CAUSE_LAUNCHER_UNAVAILABLE = "launcher_unavailable"` and `CAUSE_USAGE_HELD = "usage_held"` (the closed set); `usage_provider(harness_name)` deriving the provider from the adapter's `family` (D3), raising the registry's `UnknownHarnessError` for an unknown name; a frozen `Selection` dataclass (`outcome` one of `chosen`, `held`, `unavailable`; `position`, `route`, `token`, `skipped` as a tuple of `(position, harness, cause)`, `hold`, `error`); and `select(chain, unavailable, admit)`, which implements D3 and D4 in one loop with no I/O. `unavailable(entry)` returns an exception or `None`; `admit(entry, provider)` returns a token (a string or `None` when the budget is disabled) or a hold object (anything with `resume_at` and `waitable`, so `usage.Hold` and `job.UsageHold` both fit). The module imports only `controller.harness`, `controller.routing` and `controller.errors`.

Tests, in the new `tests/test_route_select.py`, none needing a sandbox or a real `claude`:
- a one-entry chain is chosen at position 0 with nothing skipped, and `admit` is called once with that entry;
- the first entry admitted is chosen and later entries are never probed;
- the first entry's provider held and a second entry of another provider admitted: the second is chosen, `skipped` names position 0 with cause `usage_held`, and exactly one admission is a go;
- the first entry's provider held and a second entry of the **same** provider: the second is skipped with `usage_held` and `admit` is **not** called for it (D3), then the result is `held`;
- the first entry's launcher unavailable: skipped with `launcher_unavailable` with no admission for it, the second chosen;
- every entry held, with fake-clock `resume_at` values: the reported hold is the earliest waitable one; with only caps (not waitable) the first hold; with one waitable and one cap the waitable one;
- every launcher unavailable: outcome `unavailable` carrying the first entry's error;
- two waitable holds with equal `resume_at`: the earlier chain position's hold is reported; a hold that is `waitable` with `resume_at None` is treated as not waitable;
- a mix (first unavailable, second held, third unavailable): outcome `held` with the second's hold;
- the budget disabled (`admit` returns `None` for a go): the first available entry is chosen with `token` `None`;
- `usage_provider` returns a `usage.PROVIDERS` member for every registered adapter, matches the review job's derivation for each, and an adapter added by a patched registry does not raise; an unknown name raises `UnknownHarnessError`;
- the family rule (D7), with a patched registry that has a second worker harness: whichever entry `select` chooses, its harness's family is in `review_mode.worker_families`.

The run list: `tests.test_route_select`, `tests.test_routing_chain` (unedited), `tests.test_harness`, all green.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- Selection at the launch call site, the record block and telemetry

**Files:** `controller/job.py`, `controller/telemetry.py`, `tests/test_route_select_job.py`

D1, D4 to D6, D8. In `controller/job.py`, `_execute_step_locked`: after `_worker_route`, when the role's chain (`routing.chain(role)`; the role is the first route's) has more than one entry, call `route_select.select` with an `unavailable` probe that wraps `worker_launch.for_harness`, and an `admit` probe that calls `usage_gate(entry, provider)` (a `None` gate admits every entry with a `None` token); on `chosen` use the chosen entry as `route`, its launcher and its token; on `held` return `_as_usage_hold` of the reported hold exactly as today; on `unavailable` raise the carried error. With no chain the two existing lines run untouched. The record's `worker_route` is built from the chosen route and gains the `selection` block of D5 only for a chain role. The implementation checks every later use of `route` in the function and in `_launch_job` (the disallowed tools, the model and effort, the usage start block, the `resume` and drain paths) and keeps each reading the chosen entry or the recorded `worker_route`, so nothing re-selects. In `controller/telemetry.py`, `dimensions` adds the two keys of D6 only when the block is present and reads the harness from `worker_route`.

Tests, in the new `tests/test_route_select_job.py`, using the existing fake-launch and fake-gate patterns of `tests.test_usage_gate` and `tests.test_worker_launch` and a patched registry with a second worker harness:
- a role without a chain: the record's `worker_route` has no `selection` key and equals the record written before this change (the existing `tests.test_job` and `tests.test_usage_gate` run unedited and green);
- a Claude-only chain with the account free: position 0, `selection.skipped` empty, the first entry's model launched;
- the Claude account held and a second-harness entry free: the second entry's launcher is called, the record's `worker_route` is the second entry's route, `selection.skipped` names position 0 with `usage_held`, one reservation exists and it is the second provider's;
- the Claude account held and a second Claude entry: no launch, a `UsageHold` result identical to the single-entry hold, no record, no reservation (D3);
- every entry held: the `UsageHold` of the earliest waitable resume; every launcher unavailable: the first entry's `HarnessRoleError` and no record;
- a worker that fails after it started on entry 0 is not retried: one launch call, and the next `step` selects from entry 0 again;
- `resume` of a launched record uses the recorded route and never calls `select`;
- telemetry: a record with a `selection` block reports `chain_position` and `chain_skipped`; a record without one reports exactly the dimensions it reports today, and the harness of a second-harness worker record is its own;
- a courier review record whose `worker_route.harness` is `codex` still reports `claude-code` through `telemetry.rows`/`dimensions` (D6), and a worker record without `worker_route.harness` reports the worker adapter's name. `tests.test_telemetry` unedited proves nothing about this (its `courier_job` writes no `worker_route`), so the case is a new one in `tests/test_route_select_job.py`.

The run list: `tests.test_route_select_job`, `tests.test_route_select`, `tests.test_job`, `tests.test_usage_gate`, `tests.test_usage_limit_mid_job`, `tests.test_telemetry`, `tests.test_worker_launch` (the last four unedited), all green. The full suite is the operator's rerun.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The guides and the roadmap

**Files:** `docs/guide/commands.md`, `docs/guide/runtime.md`, `docs/guide/workers.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D3, D4, D5, D9. `docs/guide/commands.md`, "Worker routing": the chain is now used, the order, what skips an entry (a launcher that cannot be resolved; a held provider, once for all its entries), that selection happens before the worker starts and never after, and what happens when every entry is held (the existing wait). `docs/guide/runtime.md`, "The routing section": the same, and the plain statement of D9 (with the shipped harnesses a chain cannot change the route; a model-specific limit is not detected, so a same-account entry is never a fallback for a hold). `docs/guide/workers.md`: the `worker_route.selection` block and the two telemetry dimensions. `docs/ROADMAP.md`: row H9b complete with this change's summary and the issue link, row C10g-3's dependency unchanged, the "Next action" paragraph pointing at the next item of the epic. `docs/ACTIVE_MILESTONE.md` records the milestone as the acceptance step does.
<!-- /CP3 -->

## Functional review notes

Every flow is unit level and runs in the tester's sandbox with no `claude`: the selection table cases of `tests.test_route_select`, the call-site cases of `tests.test_route_select_job`, and the unedited `tests.test_usage_gate`, `tests.test_telemetry` and `tests.test_routing_chain`. The full suite is not a flow, and no flow needs a scenario sandbox. Two Git 2.56 trailer tests were fixed on main (#201), so a full-suite flow is green again.
