# C10g-3a: the Codex tester's composed-configuration check (Revision 1)

Work item: `workflow-controller-composed-config-check`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `2faaf53445612976e547f78ac8d0cd9f826cc866` ("docs: release notes for Controller 1.25.0 (C10g-2b) (#225)"), the tip of `main`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row C10g-3a, fourth slice of the Codex tester ([#131](https://github.com/RodrigoFAbreu/workflow-controller/issues/131)). Issue [#221](https://github.com/RodrigoFAbreu/workflow-controller/issues/221).
Released baseline: `workflow-controller 1.25.0`. Nothing user-visible is added (a library check nothing calls yet, a guide paragraph and a roadmap row), so the squash commit is a `chore:` and there is no release, as for C10g-1 and C10g-2a.
Pull request title: `chore: the Codex tester's composed-configuration check (C10g-3a, #221)`

## Goal

The tester's sandbox (C10g-1) checks the home's own `config.toml` (`check_home`), the launch argv (`check_argv`), the layers outside the home and the MCP-server and feature listings. It does not check the configuration Codex ends up using once it composes the session flags, the user home and the managed layers: `codex_sandbox.KNOWN_LIMITS` names this as `composed_configuration`. A managed or inherited layer could still change `sandbox_mode`, `default_permissions`, a `projects` trust entry, `notify`, `model_provider` or the permissions profile itself. This item adds the check, `check_effective`, and its runner, `check_composed`, which read the composed configuration of the tester's own home and flags and refuse a launch when any security-relevant value differs from the pinned tester profile. It is the tester's counterpart of the reviewer's control (b) in `controller/review.py`. Nothing is wired: no role is declared, no launch path calls it (C10g-3b), `verify-codex` is unchanged, and the reviewer's launch, controls and tests are unchanged.

## Measurement (codex-cli 0.163.0, 2026-10-30, no model call, no spend)

Measured with a real tester job built by `codex_sandbox.build` in a scratch runtime root, then the reviewer's composing command pointed at it: `codex app-server --strict-config <the exec's --disable and -c items> --listen stdio://`, stdin `initialize`, `initialized`, `config/read` with `includeLayers` and the clone as `cwd`, held open until the answer (as `review.compose_config` does; a stdin closed at once exits before answering). Recorded in `tests/harness_contract/codex/tester_compose.txt`.

- **The command exists and answers for the tester.** `config/read` returned the whole composed `config`, `origins` (leaf to layer) and `layers`. The layers were `sessionFlags` (the `-c` items), `user` (the tester home's `config.toml`) and `system` (`/etc/codex/config.toml`, empty on this host), highest precedence first. It needs no credential and exited 0 with no model request.
- **Every tester value is reported.** `default_permissions = "tester"` (origins `sessionFlags` and `user`); `permissions.tester` as a table whose filesystem entries and `network.enabled = false` carry origin `user`, with unset siblings reported as `null` (`description`, `extends`, `workspace_roots`, `glob_scan_max_depth`, the network proxy fields); `sandbox_mode = null`; `approval_policy = "never"`; `projects = {<clone>: {trust_level: "untrusted"}}`; `shell_environment_policy` with `inherit = "none"`, the pinned `set` table and `null` for its other leaves; `notify = []`; `model_provider = "openai"`; `web_search = "disabled"`; `project_root_markers = [".git"]`; `skills.bundled.enabled = false`; `mcp_servers = {}`; `hooks = null`; every `features.<name>` for the disabled set `false`.
- **It writes Codex state into the home.** `installation_id`, `*.sqlite` state databases, `sessions/` and `tmp/` appeared; `check_home` accepted the home afterwards, so the runner calls `check_home` once more after it, as `check_launch` does after the listings.
- **A managed layer was not measured.** Creating one needs a file under `/etc/codex` (root) or a macOS MDM profile, neither available here. The managed layer shapes are the ones `tests/harness_contract/codex/compose.txt` records from the schema (`mdm`, `enterpriseManaged`, `legacyManagedConfigTomlFromFile`, `legacyManagedConfigTomlFromMdm`), and the offline tests use fixtures of those shapes. The measured layer list shows no layer beyond the three above.
- **No other command was needed.** `codex doctor` and `codex debug` were found in 0.162.1 to print no per-key sources (`compose.txt`) and were not tried again: `config/read` already gives values with their origins, so the narrower "MCP servers and features only" fallback is not needed.

## What exists

- `controller/review.py`: `compose_config` (the JSON-RPC run, reviewer's argv), `check_effective` and `effective_policy` (the reviewer's policy, including `sandbox_mode = "workspace-write"`), and the shared parts: `_check_layers`, `flatten`, `_lookup`, `_origin`, `ORIGIN_ALLOWED`, `MANAGED_APPROVED_LEAVES`, `FORBIDDEN_EFFECTIVE_KEYS`, `INSTRUCTION_KEYS`, `_answered`, `run_bounded`. Unchanged by this item.
- `controller/codex_sandbox.py`: `profile_toml`, `config_toml`, `command_environment`, `required_overrides`, `argv`, `check_argv`, `check_home`, `check_launch` and `KNOWN_LIMITS`.

## Decisions

Each decision states one principle.

- **D1. The check reads what Codex composes and compares it with what the Controller wrote, never with a second list.** The expected values are built from the layout and the roots by the same functions that write `config.toml` and the argv (`profile_toml`, `command_environment`, `required_overrides`), so a change to the profile cannot leave the check behind. `effective_policy(layout, roots, *, model, effort)` returns: `model`, `model_reasoning_effort`, `service_tier`, `approval_policy = "never"`, `default_permissions = "tester"`, `sandbox_mode` unset, `sandbox_workspace_write` unset, `projects` (the clone, `untrusted`), `shell_environment_policy` (`inherit = "none"` and the `set` table), `permissions` (the parsed `profile_toml`), and the reviewer's scalar table (`model_provider`, `web_search`, `notify`, `mcp_servers`, `hooks`, `project_root_markers`, `skills.bundled.enabled`) taken from `review.EFFECTIVE_POLICY` without its `sandbox_mode` entry.
- **D2. A leaf that Codex reports as `null` is unset, and the comparison is leaf by leaf.** The measured configuration fills unset siblings with `null`, so a table is compared by the leaves that have a value on either side; an unexpected leaf is a refusal naming it, its value and its origin layer, as the reviewer's check does (`the composed configuration sets <leaf> to <value>, expected <value>`).
- **D3. Layers and origins follow the reviewer's rules, with the tester's own keys allowed.** The layer check is `review._check_layers` (an unknown layer type, a file-backed layer outside the home or a managed layer carrying configuration, an enabled project layer, or a user layer that is not this home's `config.toml`, each refuses). Every leaf's origin must be a type of `review.ORIGIN_ALLOWED` (`packagedDefaults`, `sessionFlags`, `user`). The keys no layer other than the packaged defaults may set are `review.FORBIDDEN_EFFECTIVE_KEYS` without `permissions` and `default_permissions`, which the tester itself sets and D1 compares in full; the instruction-bearing keys (`review.INSTRUCTION_KEYS`) must have a judgeable origin. Every feature the sandbox disables (`review.DISABLED_FEATURES`) that the composed `features` table reports must be `false`; a feature it does not report is not refused (the `features list` control already covers the listing).
- **D4. The runner composes with the tester's own command, and the home is checked again after it.** `compose_argv(layout, *, binary, model, effort)` builds `app-server --strict-config`, the `--disable` set, the tester's `-c` set (`required_overrides`) and `--listen stdio://`; `check_compose_argv` refuses a command line that is not exactly what the constructor returns for its own binary, or that carries a banned flag. `check_composed(layout, config_text, binary, env, *, run, timeout, ...)` composes through the `run` seam (the same one `check_launch` uses, with the stdin and hold-open arguments `review.run_bounded` takes), then calls `check_home`, then `check_effective`. A run that fails, times out, prints no answer or answers with an error is a refusal with `check="effective"`.
- **D5. The composed check is a launch control of its own; it is not part of `check_launch` and `verify-codex` is unchanged.** `check_launch` stays as C10g-1 and C10g-2b use it, so the probe run and its tests need no `app-server` answer from the fake Codex. The configuration is a fact about the moment of launch (a managed layer can appear after a build was verified), which is where the check belongs: C10g-3b calls `check_launch` and `check_composed` before each tester launch. This item does not wire either.
- **D6. The limit is narrowed truthfully, not removed.** `config/read` is composed by `app-server`, a separate process started just before the launch, with the same home, environment and `-c` items; a layer that changes in that window, or a setting that only the `exec` command composes differently, is not seen. This is the same assumption the reviewer's control (b) makes, now stated. `KNOWN_LIMITS` replaces `composed_configuration` with `composition_window` carrying that sentence; the profile header comment (`config_toml`), the docstring of `check_launch`, `docs/guide/harnesses.md` and the `Limit` test say it. The reviewer's text is not edited.
- **D7. Release type.** The title rules need a `feat:` only for an operator-visible change. This item adds a function nothing calls and corrects a guide paragraph about an internal limit, so it is a `chore:` with no release.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The effective-configuration check | - | 2 | 1 |
| CP2 | The runner, the limit and the documents | CP1 | 2 | 1 |

<!-- CP1 -->
### CP1 -- The effective-configuration check (#221)

**Files:** `controller/codex_sandbox.py`, `tests/test_codex_sandbox.py`, `tests/harness_contract/codex/tester_compose.txt`, `tests/harness_contract/codex/README.md`

D1 to D3 and the argv half of D4, as a pure function. In `controller/codex_sandbox.py`: `effective_policy`, `check_effective(composed, layout, roots, *, model, effort)`, `compose_argv` and `check_compose_argv`. `tests/harness_contract/codex/tester_compose.txt` records the measurement above (the argv, the layer list, the origins of the tester's keys, the state the run wrote) and gets its row in the contract README with its SHA-256.

Done when `CheckEffectiveTests` in `tests/test_codex_sandbox.py` show, offline: a composed result built from the tester's own `profile_toml` and pinned values (with `null` siblings, three layers and origins as measured) passes; and each of these is refused, naming the leaf, its value and its origin layer: a changed `sandbox_mode`, `default_permissions`, `approval_policy`, `notify`, `model_provider`, `web_search`, `project_root_markers`, `model`, `model_reasoning_effort` and `service_tier`, a `projects` trust entry changed or added, a changed `shell_environment_policy` `inherit` or `set` value, a changed, added or removed filesystem entry or a `network.enabled = true` in `permissions`, a set `sandbox_workspace_write`, an MCP server or a hook, an enabled disabled-feature, a leaf whose origin is `mdm`, `enterpriseManaged`, `system` or a legacy managed type, a managed layer carrying any configuration, an unknown layer type, an enabled project layer, a user layer that is not the tester home's `config.toml`, an instruction-bearing key set by a layer, `model_providers` or `openai_base_url` set outside the packaged defaults, and a composed value that is not a `config/read` result; `compose_argv` equals the constructor's output for the same inputs and `check_compose_argv` refuses an omitted `--disable`, an omitted or added `-c`, a missing `--strict-config` or `--listen stdio://`, `-s`, `-p` and `--add-dir`. `tests.test_codex_sandbox` runs unchanged otherwise, and the reviewer's own tests are untouched.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The runner, the limit and the documents (#221)

**Files:** `controller/codex_sandbox.py`, `tests/test_codex_sandbox.py`, `tests/test_codex_sandbox_live.py`, `docs/guide/harnesses.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

The runner half of D4, D5 and D6. In `controller/codex_sandbox.py`: `check_composed`, the JSON-RPC exchange (a private helper that mirrors `review.compose_config` for the tester's argv and imports its stdin helper), `KNOWN_LIMITS` with `composition_window` in place of `composed_configuration`, the `config_toml` header and the `check_launch` docstring. `tests/test_codex_sandbox_live.py` gains one opt-in test (skipped unless `CONTROLLER_LIVE_CODEX_TESTS=1` and the real `codex` is present): a real tester job passes `check_composed`, and with one of the tester's `-c` items replaced, so the real Codex composes a different value, `check_composed` refuses naming it and its `sessionFlags` origin. Documents, in place: `docs/guide/harnesses.md` says the check exists, where it is called (not yet) and the remaining limit; `docs/ROADMAP.md` marks C10g-3a complete and C10g-3b next; `docs/ACTIVE_MILESTONE.md` points to `/milestone-plan` for C10g-3b.

Done when `CheckComposedTests` in `tests/test_codex_sandbox.py` show, offline with a fake runner printing the JSON-RPC answer: a clean answer passes and `check_home` runs after the composing command and before the comparison; a failed, timed-out, empty or error answer is a refusal with `check="effective"`; a refusal by `check_effective` is not swallowed; the command run is exactly `compose_argv`'s, in the clone, with the tester's environment; `check_launch` is unchanged (it never runs the composing command, and its own tests pass unchanged); `KnownLimitsTests` show `composition_window` is the only entry, is named in the profile header, and says the `app-server` window; the reviewer's `_overrides`, `argv`, `check_effective` and `compose_config` are not edited (`git diff` of `controller/review.py` is empty). The live test is skipped offline with a reason. `python3 tools/check_docs.py` passes, and the suite is green but for the known Git 2.56 trailer tests.
<!-- /CP2 -->

## Requirements

| id | requirement | checkpoints |
|---|---|---|
| R1 | The tester's composed configuration, read with the reviewer's composing command under the tester's home and flags, is compared with values built from the same functions that write the profile and the argv, leaf by leaf with `null` as unset, and any security-relevant difference, from any layer, is a refusal naming the leaf, its value and its origin (#221) | CP1 |
| R2 | The layers and origins of the composed result follow the reviewer's rules (unknown, file-backed or managed layers carrying configuration, an enabled project layer, a foreign user layer or a disallowed origin refuse), with the tester's own `permissions` and `default_permissions` compared in full (#221) | CP1 |
| R3 | `check_composed` runs the composing command through the `run` seam with a command line equal to the constructor's, checks the home again afterwards, and refuses a run that fails, times out or answers nothing; it is a separate control, not part of `check_launch`, and nothing calls it yet (#221) | CP2 |
| R4 | `KNOWN_LIMITS` states the narrower remaining limit, the profile header, the docstrings and the guide say it, the measurement is recorded, one opt-in live test runs the check against the real Codex, and the reviewer, `verify-codex` and the launch paths are unchanged (#221) | CP1, CP2 |

## Functional review

Every flow is offline and unit-level, run one module at a time (`tests.test_codex_sandbox`, `tests.test_codex_verify`, `tests.test_codex_verify_command`, `tests.test_harness_table`), plus `python3 -B tools/check_docs.py` and `git diff <base>..HEAD --stat -- controller/review.py controller/codex_verify.py controller/cli.py` (expected empty). The tests create temporary folders, so the tester runs them with `TMPDIR` set to its scratch area (its `/tmp` is read-only). There is at most one live flow, operator-run and marked environment-limited: `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -m unittest tests.test_codex_sandbox_live`, which needs the real `codex`, `bwrap` and an `auth.json` and makes no model request for the new test. The full suite is not a flow.

## Risks

- **A managed layer was not measured.** The layer rules are the reviewer's and are tested with fixtures of the recorded shapes; the first real managed layer is a refusal by construction (any configuration in it refuses), so a shape drift fails closed.
- **A Codex release that changes the composed shape.** The pinned version is unchanged (`review.PINNED_VERSION`); a different build is refused earlier. A new key in `permissions.tester` or `shell_environment_policy` with a value is a refusal naming it, which is the intended fail-closed behaviour; a new `null` leaf is unset and passes.
- **The check-to-launch window.** Stated as `composition_window` (D6); not closed here.
- **The composing command writes state into the home.** Measured, and `check_home` accepts it; the runner checks the home afterwards, so a surprise entry is a refusal.
- **Out of scope:** the tester launch, declaring the role, routing and fallback (C10g-3b, C10g-3c), the Codex worker (H4a to H4g), the reviewer's pin or checks, and any change to `verify-codex`.
