# H9a: a fallback chain per role in the routing configuration, validated and resolved (Revision 2)

Work item: `workflow-controller-fallback-chain`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `d1b6982dbc3ea85f836f0c20748d68df7f44b3b3` ("docs: link the H9 issue from the roadmap row and the next action (#198)"), the tip of `main`, passed explicitly as `/milestone-plan d1b6982…`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H9, next after H10 (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#197](https://github.com/RodrigoFAbreu/workflow-controller/issues/197).
Released baseline: `workflow-controller 1.22.2`.
Pull request title: `chore: a fallback chain per role in the routing configuration, validated and resolved (H9a, #197)`

## Goal

A routing entry for a role may list, after its own route, an ordered list of further `{harness, model, effort}` routes, the role's **fallback chain**. This milestone is only the **shape**: the key is accepted in the `--routing-config` file and the settings file's `routing` section, every entry of every chain is validated when the routing is loaded, and one resolver returns the whole chain. Nothing yet chooses a later entry; the first entry behaves exactly as a role's single route does today.

## The split of the roadmap row (the first job of this plan)

Row H9 asks for the chain **and** for trying its entries "when the first is unavailable or held by a limit". The second half changes what happens at launch time under a usage limit, which is the riskiest code in the Controller (`controller/job.py`, C8 and C8b). Done together they would make one large, hard-to-review milestone, so the row is split into two releasable mechanisms and **only the first is done here**:

- **H9a (this plan)**: the chain in the routing configuration, its validation over every entry, the resolver, and the family count over every entry. No launch, record, telemetry or limit code changes.
- **H9b (a later milestone, its own plan)**: the selection: which entry is used, when ("unavailable or held by a limit" defined narrowly and testably from the C8/C8b detection), chosen before a worker is spawned, never mid-job, recorded in the job record and telemetry (H11 reads it), and what happens when every entry is held. H9b is a `feat:` and releases the chain together with its selection.

H9b is not smaller than H9a: it needs H9a's shape to exist and its own review of the limit code, so H9a goes first.

## Decisions

Each decision states one principle.

- **D1. A chain is an additive `fallback` key of a role entry, never a changed shape.** `roles.<role>.fallback` is a non-empty list of objects; the role entry's own `harness`, `model` and `effort` stay the first route. Every file valid today has no `fallback` and loads, resolves and writes back exactly as before. The key is also the forward-compatible form: under the settings file's `ignore` rule an older release leaves an unknown key out of an entry with its usual warning, where a list in place of the entry would make the file unusable for it.
- **D2. A fallback entry is complete enough to be checked alone.** It must name `harness` and `model`; `effort` is optional (omitted means the harness's own default, the same as `inherit` for the first route). Nothing is filled in from `default` or the built-in table, because a model inherited from the Claude built-in on another harness would be refused anyway, and a value that comes from somewhere else is exactly what a reader cannot see. Unknown keys inside a fallback entry are refused, in the settings section too, where the parent entry's unknown fields are ignored. The reason is stated here and not borrowed: an entry is a route, so a field this release does not know could change which route the entry means (a later release's per-route option), and running the wrong route is worse than a refusal; and the settings walkers (`_routing_unknown`, `_lookup`, `_drop`, `settings clean`) address dict keys only and cannot name or remove a list item. The cost is a downgrade hazard, recorded in the runtime guide (CP2): a later release that adds a field to a fallback entry writes a shared settings file this release refuses with a `SettingsError` naming the key. A first-route field is unaffected, and so is a file with no `fallback`. An entry identical to the role entry's own first route as the configuration writes it is allowed (a repeated route is harmless and command-line options can change entry 0, so only the repeated fallback entry, which can never differ, is refused).
- **D3. Only a role entry carries a chain.** `default.fallback` is refused with a message that says so: a chain in `default` would have to be valid for every role, and the roles do not run as the same adapter role (`functional-tester` against `worker`).
- **D4. Every entry is validated at load, with the checks the first route already gets.** At parse time each entry's `harness` is a registered harness that declares the role's adapter role (`check_role_harness`, H1) and its values pass `check_value`. When a `RoutingOptions` is built, each entry's model and effort are judged against the harness that would run it (`check_route_values`, H8, with the `harness.models` setting), so a bad second entry is refused at load, naming the role and the entry's position, not at a fallback that never comes.
- **D5. The resolver returns the chain; entry 0 is `resolve(role)`.** `RoutingOptions.chain(role)` returns a tuple of `ResolvedRoute`: entry 0 is exactly `resolve(role)` (so every consumer of `resolve` is unchanged), and each later entry carries the new source `config-chain` for the fields the entry names (an omitted effort is `inherit`). Precedence is unchanged for entry 0 (`--role-*`, `--model`/`--effort`, role entry, `default`, built-in) and is **extended in one stated way**: the command-line options override entry 0 only and never touch a chain's later entries, which come from the configuration alone.
- **D6. The family rule counts every entry.** `review_mode.worker_families` is the set of families the worker roles can run on; a fallback entry is a route a role can take, so it joins the set (the independence rules of H2 and H10 are therefore applied over every entry, as the issue asks). With the registry as shipped only `claude-code` takes worker roles, so no chain that loads can change a family set and nothing observable changes; tests use a patched registry, as H2 and H10 did. `mixed_lane_reason` names a local review role's fallback entries when it has any and judges the pair's family verdict over every entry of that role's chain (the pair is of one family when any entry is of `reviewer.harness`'s family, and distinct only when none is), so the stop's text stays true.
- **D7. Nothing is launched, recorded or measured differently.** `worker_route`, the job record, telemetry, `explain` and the launch path are untouched; the chain is not read by any of them. H9b adds the record fields.
- **D8. The settings table generation rises to 17.** A route key under `routing.roles.<role>` is a table change (`controller/settings.py`'s rule), so `TABLE_GENERATION` becomes 17, `routing_mapping` writes a role's chain back, and the one test that pins the number is updated.
- **D9. Released as `chore:`, with no release.** The key is accepted and validated but nothing uses it yet, so no behaviour a user can observe changes. Releasing a key that does nothing would be worse than waiting one pull request for H9b, which is `feat:` and ships the chain with its selection. The pull request says `Refs #197` and does not close it; the runtime guide states plainly that the chain is not yet used to select a route.

## Out of scope

The selection of an entry and everything it needs (a limit or unavailability test, the job-record and telemetry fields, the every-entry-held behaviour: H9b); the Codex tester's own fallback (C10g-3, which uses H9b); a Codex worker (H4a to H4g); changing the usage-limit detection; the review modes; a new setting or command-line option; a chain in `default` (D3); command-line options for a chain.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The chain shape, validation, resolver, family count and tests | - | 3 | 1 |
| CP2 | The guides and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The chain shape, validation, resolver, family count and tests

**Files:** `controller/routing.py`, `controller/review_mode.py`, `controller/settings.py`, `tests/test_routing_chain.py`, `tests/test_settings.py`

D1 to D8. In `controller/routing.py`: a constant `FALLBACK_KEY = "fallback"` and the source `SOURCE_CONFIG_CHAIN = "config-chain"` (added to `SOURCES`); `RoutingConfig` gains `chains: Mapping[str, tuple[Mapping[str, str], ...]]` (default empty, role to its fallback entries). `_Validator` reads and removes `fallback` from a role entry before the existing key check, with a method that checks the list (non-empty list of objects; each with `harness` and `model`, optional `effort`, no other key; `check_role_harness` for the role, `check_value` for the values; an identical repeated entry refused) and refuses `default.fallback` (D3). Every refusal is a `RoutingConfigError` naming the location (`roles.<role>.fallback[<n>].<field>`, counted from 1 after the first route) and, in the settings section, carrying the dotted `key`. `RoutingOptions.chain(role)` and the per-entry `check_route_values` loop in `__post_init__` (the refusal names the role and the entry's position and carries it in the evidence). In `controller/review_mode.py`: `worker_families` counts every chain entry and `mixed_lane_reason` appends the local role's fallback harnesses and families when it has any. In `controller/settings.py`: `routing_mapping` writes `fallback` back, `TABLE_GENERATION` is 17 with its comment line. The implementation reads every place in `controller/settings.py` that walks `routing.roles` entries (the unknown-key readings, `settings clean`, the effective-settings display) and keeps each correct for the new key; in particular `_routing_unknown`'s dotted-role reading treats `fallback` as a known role-level key, so a role literally named `review-plan.fallback` beside a real `roles.review-plan.fallback` is reported as unknown without ever listing the real chain, and `settings clean` leaves the chain in place. An unknown key inside a fallback entry is refused with a `SettingsError` carrying the dotted key `routing.roles.<role>.fallback[<n>].<key>` (D2).

Tests, all in the new `tests/test_routing_chain.py` except the one pin, none needing a sandbox or a real `claude`:
- nothing that loads today changes: for the built-in routing, `tests/fixtures/lane_routing.json` and a settings `routing` section with no `fallback`, `chain(role)` has exactly one entry equal to `resolve(role)` for every role, `chains` is empty, `routing_mapping` of the parsed config equals the mapping it was parsed from, and `review_mode.worker_families` is unchanged;
- a chain loads: a Claude-only chain (first route `claude-opus-5-5`, a fallback entry `claude-code` with `claude-sonnet-5-5` and no effort) parses from a `--routing-config` file and from a settings section, `chain(role)` returns both entries in order with the sources of D5, `routing_mapping` writes it back, and a two-family chain loads with a patched registry that has a second worker harness;
- a bad entry is refused at load naming the role and the position: an unregistered harness, a harness that does not declare the role's adapter role, a model the harness does not declare (refused by `RoutingOptions`, with the `harness.models` setting accepting it again), an effort it does not declare, a missing `harness`, a missing `model`, an unknown key, an empty list, a non-list, a non-object entry, a repeated fallback entry, an unknown key inside a fallback entry (from a `--routing-config` file and from the settings section, where the message names `routing.roles.<role>.fallback[<n>].<key>`), and `default.fallback`; a fallback entry identical to the role entry's own first route loads; for a refusal in the second of three entries the message and the evidence name entry 2;
- the settings walkers: a settings file whose `routing.roles.review-plan` has a `fallback` loads with no unknown-key warning and `settings clean` leaves it unchanged; with an unknown role named `review-plan.fallback` beside it, only the unknown role is reported and removed, and the chain survives;
- precedence: `--role-model`, `--role-effort`, `--role-harness`, `--model` and `--effort` change entry 0 of `chain(role)` and leave every later entry as the configuration wrote it;
- the family count: with a patched registry, a Claude-first chain with a fallback entry of the second family makes `worker_families` hold both families, a local review role whose second-family fallback is of `reviewer.harness`'s family makes `mixed_lane_reason` say the pair is of one family, `review_mode.choose` returns the mixed-family `refused` choice for `auto`, `cross-model` and `single-provider` and the ordinary gate for `manual`, and `mixed_lane_reason` names the fallback entry; with the shipped registry nothing changes;
- `tests/test_settings.py` pins `TABLE_GENERATION` at 17 (its only edit), and the shipped-table rows and their generations are unchanged.

The run list: `tests.test_routing_chain`, `tests.test_routing` (unedited), `tests.test_settings`, `tests.test_review_mode` (unedited), `tests.test_review_independence` (unedited), `tests.test_harness`, all green. The full suite is the operator's rerun; two Git 2.56 trailer tests fail locally and are known.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guides and the roadmap

**Files:** `docs/guide/runtime.md`, `docs/guide/commands.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D1, D2, D5, D8, D9. `docs/guide/commands.md`, "Worker routing": the `fallback` key of a role entry, its entry fields and the extended precedence (the command-line options change entry 0 only). `docs/guide/runtime.md`, "The routing section": the same key in the settings file, the refusal of `default.fallback`, the table generation 17 note on the terms of the earlier ones (an older release ignores `routing.roles.<role>.fallback` with its warning and refuses it in a `--routing-config` file), and the downgrade hazard of D2 (a later release that adds a field to a fallback entry makes this release refuse a shared settings file, naming the key), and one sentence that the chain is validated and counted in the worker families but **not yet used to select a route**, which H9b adds. `docs/ROADMAP.md`: row H9 becomes two rows, H9a (complete once accepted, with this change's summary and the issue link) and H9b (the selection, `feat:`, depends on H9a, with the narrow definition of "unavailable or held by a limit" as its open question); C10g-3's dependency on H9 reads H9b; the "Next action" paragraph points at H9b. `docs/ACTIVE_MILESTONE.md` records the milestone as the acceptance step does.
<!-- /CP2 -->

## Functional review notes

Every flow is unit level and runs in the tester's sandbox with no `claude`: the unchanged-routing, chain-loads, bad-entry, precedence and family-count tests of `tests.test_routing_chain`, and the unedited `tests.test_routing` and `tests.test_review_mode`. The full suite is not a flow, and no flow needs a scenario sandbox.
