# H8: a harness declares its models and efforts, and routing refuses a pair it does not support (Revision 2)

Work item: `workflow-controller-harness-models`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `798ba8632d889569757c51f98c6d49ca56197589` ("chore: move this repository to Workflow 2.20.0 (#172)"), the tip of `main`, passed explicitly as `/milestone-plan 798ba86…`.
Lifecycle authority: this repository's installed Workflow **2.20.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H8, the next item of the harness-neutral roles epic (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action".
Released baseline: `workflow-controller 1.21.1`. The change adds a setting and a refusal at load, so the squash commit is a `feat:` change and releases `1.22.0`.
Pull request title: `feat: a harness declares its models and efforts, and routing refuses a pair it does not support (H8)`

## Goal

Today a model or an effort in a routing entry is checked only for being a non-empty string that does not start with `-`; the `claude` CLI is the authority, and it silently ignores an unknown `--effort`. The harness of H1 is checked against the role, but nothing checks the model or the effort against the harness. Once a second harness can take a worker role, a Claude model routed to it would fail at launch, in the middle of a run.

This milestone makes each adapter declare the models and efforts it supports, refuses a route whose model or effort the chosen harness does not support **before any job record is written**, and lets the operator add a model through a setting without waiting for a release.

One concern only: **validating the resolved model and effort against the resolved harness, and the setting that extends the model list**. Out of scope: the launch seam (H3), a per-harness built-in model (H4), fallback chains (H9), the reviewer settings (`reviewer.model`, `reviewer.effort`, `reviewer.same_provider_*`, which keep their own launch-time checks), extending the effort list by setting, and any change to which harness and role pairs are accepted. No Codex worker is added, so with today's registry the new refusal can fire only for a model or effort that `claude-code` does not declare.

## Decisions

Each decision states one principle.

- **D1. An adapter declares what it supports.** `HarnessAdapter` gains two fields, `models: tuple[str, ...]` and `efforts: tuple[str, ...]`, both non-empty for every registered adapter. `claude-code` declares the four full ids this repository already names (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`) and the three aliases the CLI accepts and the tests and docs already use (`opus`, `sonnet`, `haiku`), and the efforts `low`, `medium`, `high`, `xhigh`, `max`. `codex` declares `gpt-6.1-sol` and the efforts observed in `tests/harness_contract/codex/` (the implementer reads the captured contract and declares exactly the values it shows, naming the file in the field's comment). The registry stays plain declarations: `controller/harness.py` still imports only `controller.errors`, and a test pins `harness.CLAUDE_CODE.efforts` to `review.CLAUDE_EFFORTS` and the default model and effort of each launch object to the declarations, the existing way of tying a launch literal to the registry.
- **D2. One function judges a route.** `routing.check_route_values(role, harness_name, model, effort, extra_models)` returns `None` or raises `ValueError` naming the role, the harness, the offending field and value, and the values the harness declares. `None` for the model or the effort means "inherit" and is never refused. A model passes when it is in the adapter's `models` or in `extra_models[harness_name]`; an effort passes when it is in the adapter's `efforts`. It is pure and reads the registry through the module attribute at call time, so a test that patches `harness.ADAPTERS` changes what it accepts.
- **D3. The check runs once, on the resolved routes, before any record is written.** `RoutingOptions` gains a field `harness_models: Mapping[str, tuple[str, ...]]` (default empty). `__post_init__`, after its existing checks, resolves every role in `ROLES` and calls `check_route_values` on the result; a refusal is `RoutingConfigError` (exit 20, the class every malformed routing configuration already is), whose message adds where each of the three values came from (`ResolvedRoute.model_source`, `effort_source`, `harness_source`, and for a built-in default the words "the built-in default") and, for a model, names the setting that adds one (`harness.models`). The check sees the command line, the routing config in force and the built-in table together, which is the only place all three meet; `validate_routing_mapping` and `load_routing_config` are not changed, because a `--routing-config` file is read before the settings that may extend the model list. A built-in default model on a harness that does not declare it is refused like any other value: the operator then names a model with the role's harness, which is the right failure until H4 gives a harness its own built-in routes.
- **D4. The setting adds a model, never an effort, and has no option.** A new settings row `harness.models` (type `harness-models`, default `{}`, no CLI flag) maps a registered harness name to a list of non-empty model strings, each passing `routing.check_value`. A value that is not an object of lists of strings is refused at load (exit 20), the way a malformed `notifications.events` is; a harness name no registered adapter has is left out and reported as an unknown key by the same mechanism that reports an unknown routing key, so a file shared with a newer release stays usable. The effective value reaches `RoutingOptions.harness_models` in `cli._routing_options`. The table generation rises to 16 and its comment names the row.
- **D5. The refusal is a documented tightening, not a silent one.** A routing file or settings file that names a model or an effort the harness does not declare, valid until now, is refused at the next `run` or `step` with exit 20 and the message of D3. The notice is a committed deliverable of CP3, not something left to the release step: the repository's publication notes are the fixed string `workflow-controller {tag}` (`.workflow-controller/policy.json`, no `{release_notes}`), so the published release carries no milestone text. CP3 writes a section "Upgrading to 1.22.0" in `docs/compatibility.md` that states the newly refused configurations (a model the harness does not declare, an effort it does not declare), the exit code 20 and the message, and the remedy (add the model to the `harness.models` entry of the settings file; there is no command, so the section shows the JSON entry to add; an unsupported effort is changed in the routing). `docs/common-problems.md` gains the matching entry. The per-release page `docs/releases/1.22.0.md` is written, as for every release, by the docs pull request after the release, from that section; the milestone does not depend on it. The lane's own routing is verified as D8 says.
- **D6. The generated table shows what is declared.** The first table of `docs/guide/harnesses.md` (H6) gains a `Models` and an `Efforts` column read from the adapters; the entries of `harness.models` are the operator's and are not in the table. The page's prose says that a model outside the list is added with the setting.
- **D7. Tests that used a placeholder model say what they mean.** Tests that route `m`, `other`, `gpt` and similar strings through `RoutingOptions` or the command line now use a declared id, because the placeholder would be refused. Where a test is about the placeholder itself (a value passes through unchanged), it uses a declared alias. The migration scope is fixed here so it can be reviewed. Models: the strings `m`, `other`, `gpt` and `x` passed as a model in `tests/test_routing.py`, `tests/test_settings.py`, `tests/test_worker.py`, `tests/test_cli.py` and `tests/test_job.py` become `sonnet`, `haiku` and `opus`, keeping two values that a test treats as different different (`m` to `sonnet`, `other` to `haiku`, `gpt` and `x` to `opus`, adjusted only where one test needs a third distinct value). Efforts: `e`, `x`, `e-cli`, `e-role` and `e-config-default` (`tests/test_job.py` lines near 1051, `tests/test_routing.py`, `tests/test_worker.py`, `tests/test_lifecycle_orchestration.py`) become `low`, `medium`, `high` and `max`, again one per distinct source (`e-cli` to `low`, `e-role` to `high`, `e-config-default` to `max`), so a test that tells the command line, the role and the default apart still can. `tests/test_settings.py` rows that merely hold a string in a settings value are not routed through `RoutingOptions` and stay. The implementer lists in the checkpoint's commit message any case outside this inventory. No test is deleted and no assertion on a launched command line changes except its value.

- **D8. The lane's routing is tested from a committed copy and verified once from the real file.** `.controller/` is gitignored, so no test reads it. `tests/fixtures/lane_routing.json` is a committed copy of the lane's routing as of this plan (the same default, thirteen role entries, and values as `.controller/routing.json` at the base commit). The automated test loads the fixture with `routing.load_routing_config` and proves it passes the check for every role; it needs no operator-local file. The real file is verified separately, once, by the implementer in CP1: a one-line `python3 -c` that loads `.controller/routing.json` through `routing.load_routing_config`, builds `RoutingOptions` from it and prints `ok`, and, because the copy can drift, compares the fixture byte for byte with the real file (`cmp`). The command and its output are recorded in the CP1 self-review record. If the lane file later changes, the fixture is a historical sample; the lane is protected by the refusal itself, which names the value.

## Out of scope

Everything listed under Goal, plus a model catalogue fetched from a CLI, validating the reviewer settings, and a per-model effort table.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The declarations, the check and the refusal at load | - | 2 | 1 |
| CP2 | The `harness.models` setting and its wiring | CP1 | 2 | 1 |
| CP3 | The table columns, the pages and the roadmap | CP2 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The declarations, the check and the refusal at load

**Files:** `controller/harness.py`, `controller/routing.py`, `tests/fixtures/lane_routing.json`, `tests/test_harness.py`, `tests/test_routing.py`, `tests/test_cli.py`, `tests/test_job.py`, `tests/test_worker.py`, `tests/test_lifecycle_orchestration.py`, `tests/test_settings.py`

D1, D2, D3, D7 and D8. `controller/harness.py`: the two fields and the declarations. `controller/routing.py`: `check_route_values`, the `harness_models` field of `RoutingOptions` and the check in `__post_init__`; the module docstring's list of what is unvalidated is corrected (model and effort are validated against the harness, and the `claude` CLI is no longer the only authority). The test files that pass placeholder models are migrated as D7 says; the inventory of D7 names them, and any other test file the migration touches is named in the checkpoint's commit message.

Tests, in `tests/test_harness.py` and `tests/test_routing.py`:
- every registered adapter has non-empty `models` and `efforts`; `harness.CLAUDE_CODE.efforts` equals `review.CLAUDE_EFFORTS`; the Codex launch defaults (`review.DEFAULT_MODEL`, `review.DEFAULT_EFFORT`) are declared by the Codex adapter and the Claude defaults by the Claude adapter;
- `check_route_values` accepts every declared model and effort, accepts `None` for each, accepts a model only in `extra_models` for that harness and not for another, and refuses an undeclared model and an undeclared effort with a message naming the role, harness, field and value;
- with `harness.ADAPTERS` patched so that `codex` declares the worker role, a route to `codex` with the built-in Claude model is refused by `RoutingOptions(...)`, naming "the built-in default" as the source, and the same route with a Codex model and effort is accepted;
- `RoutingOptions` refuses a bad model from `--model`, from `--role-model`, from a config role entry and from a config default, and each message names its source; it refuses a bad effort the same way; a role with `inherit` (no model) is never refused;
- `RoutingOptions(harness_models={"claude-code": ("claude-opus-5-6",)})` accepts that model and `NO_OVERRIDES` still builds;
- the built-in table and the committed fixture `tests/fixtures/lane_routing.json` pass the check for every role (no test reads `.controller/routing.json`; a clean checkout runs it unchanged);
- the refusal is a `RoutingConfigError` with exit code 20 through `cli.main` for a run with `--model nonsense`, and no job record is written.

Done when: `python3 -m unittest tests.test_harness tests.test_routing` is green; the new tests fail against the base commit (checked once locally by stashing the source edits); the full suite is green; the one-time check of the real `.controller/routing.json` and the `cmp` against the fixture (D8) are recorded in the self-review record.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The `harness.models` setting and its wiring

**Files:** `controller/settings.py`, `controller/cli.py`, `tests/test_settings.py`, `tests/test_cli.py`

D4 and D5. `controller/settings.py`: the type constant, the row, the validator (strict on shape, tolerant of an unknown harness name, reported like the other unknown keys), the default fill, `settings show`/`settings clean` behaviour for the row following the `notifications.events` pattern, and `TABLE_GENERATION = 16` with the comment. `controller/cli.py`: `_routing_options` passes `effective.values["harness.models"]` as `harness_models`.

Tests: a file without the row reads the default `{}` and a filled file holds it; a valid row loads and reaches `RoutingOptions`; a row that is not an object, a list entry that is not a string, an empty string and a value starting with `-` are each refused with exit 20 naming the key; an unknown harness name is left out and reported as an unknown key and `settings clean` removes it; a run whose routing names a model only the row declares is accepted, and the same run without the row is refused (exit 20, the message naming `harness.models`); a file filled by generation 15 gains the row on the next fill and keeps every other value; `settings show` prints the row.

Done when: `python3 -m unittest tests.test_settings tests.test_cli` is green; the settings golden or table-generation tests, if any, are updated with the new generation; the full suite is green.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The table columns, the pages and the roadmap

**Files:** `tools/harness_table.py`, `tests/test_harness_table.py`, `docs/guide/harnesses.md`, `docs/guide/runtime.md`, `docs/guide/commands.md`, `docs/compatibility.md`, `docs/common-problems.md`, `docs/README.md`, `docs/ROADMAP.md`

D6 and D5. `tools/harness_table.py` renders the `Models` and `Efforts` columns of the first table from the adapters, and `tests/test_harness_table.py` follows (a patched registry with an added model changes exactly that cell). `docs/guide/harnesses.md` is regenerated with `python3 tools/harness_table.py --write` and its prose gains the sentence on `harness.models`. `docs/guide/runtime.md`: the settings table row for `harness.models`, and the routing section states that a model and an effort are checked against the harness at load, with the refusal's wording and the fix. `docs/guide/commands.md`: the `--model`, `--role-model`, `--effort` and `--role-effort` text no longer says the value is unvalidated, and names exit 20 for an unsupported pair. `docs/compatibility.md`: the settings table generation becomes 16 and the section "Upgrading to 1.22.0" of D5 is written (newly refused models and efforts, exit 20, the `harness.models` entry to add). `docs/common-problems.md`: the entry for the refusal, linking that section. `docs/README.md`: the harness page row mentions models and efforts. `docs/ROADMAP.md` marks H8 done and names H7 as the next item of the epic; `docs/ACTIVE_MILESTONE.md` is written by the Workflow's own close-out, not by hand. `docs/releases/1.22.0.md` is written by the docs pull request after the release, from the compatibility section; it is not part of this milestone's acceptance.

Done when: `python3 tools/harness_table.py --check` and `python3 tools/check_docs.py` exit 0; the full suite is green; every point of the roadmap row is changed or named as already covered.
<!-- /CP3 -->
