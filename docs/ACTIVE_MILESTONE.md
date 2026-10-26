# Active Milestone

## Status

**In implementation review.** `workflow-controller-harness-models` (H8) is implemented from
`docs/ai-workflow/CONTROLLER_HARNESS_MODELS_PLAN.md` (revision 2, plan approved by gate policy); every checkpoint is
complete and the self-review findings are applied.

- CP1 -- the declarations, the check and the refusal at load: complete. `HarnessAdapter` declares `models` and
  `efforts`; `routing.check_route_values` judges a resolved route and `RoutingOptions` refuses an unsupported pair with
  exit 20 before any job record is written. D8 check of the real `.controller/routing.json`: loaded through
  `routing.load_routing_config` and built into `RoutingOptions`, it printed `ok`, and `cmp` found it identical to
  `tests/fixtures/lane_routing.json`.
- CP2 -- the `harness.models` setting: complete. Settings row `harness.models` (type `harness-models`, default `{}`, no flag, table generation 16) is strict on shape, leaves out and reports an unknown harness name, and reaches `RoutingOptions.harness_models` through `cli._routing_options`.
- CP3 -- the table columns, the pages and the roadmap: complete. The harness table gains `Models` and `Efforts` columns; runtime, commands, compatibility ("Upgrading to 1.22.0"), common-problems, README and the H8 roadmap row are current.

## Next action

The implementation review of implementation revision 1 (local, then external).

## Functional review checklist

Work item `workflow-controller-harness-models` (H8), implementation revision 1. Every flow runs offline: no network,
no `gh`, no real `claude`/`codex`/`ssh`, nothing written inside the product clone. All writes go to `$TMPDIR`.

### Setup

Run from the product clone; `P` is the clone, `W` a scratch directory (a plain `git init` repo is enough -- the
refusal fires before the repository is inspected, and a value that passes ends at the "not a Workflow-managed
repository" error, which is the expected "accepted" outcome here).

```sh
P=$PWD; W=$(mktemp -d); cd "$W" && git init -q . && git commit -q --allow-empty -m init
C() { PYTHONPATH=$P python3 -P -m controller --runtime-dir "$W/rt" --claude-binary /bin/false "$@"; }
echo '{}' > empty.json
```

Options go before the subcommand (`C --model x step "$W"`, not `step --model x`).

### Test data

```sh
echo '{"schema_version":1,"default":{"model":"gpt-5","effort":"low"}}' > route.json
echo '{"harness":{"models":{"claude-code":["gpt-5"]}}}'                > extra.json
echo '{"harness":{"models":{"claude-code":"x"}}}'                     > badshape.json
echo '{"harness":{"models":{"nope":["y"]}}}'                          > unknown.json
```

### Flows

| # | Flow | Command | Expected |
| --- | --- | --- | --- |
| 1 | Unsupported model on the command line | `C --settings empty.json --model nonsense step "$W"; echo rc=$?` | Exit 20. Message `role '...' routes the model 'nonsense' to the harness 'claude-code', which declares [...seven ids...]`, `model from the cli value`, ends with `add a model the harness accepts with the harness.models setting`. |
| 2 | Unsupported effort | `C --settings empty.json --effort turbo step "$W"; echo rc=$?` | Exit 20. Message names the effort `'turbo'` and `['low', 'medium', 'high', 'xhigh', 'max']`, `effort from the cli value`; no `harness.models` hint (an effort cannot be added). |
| 3 | Per-role model | `C --settings empty.json --role-model review-plan=nonsense step "$W"` | Exit 20 naming `role 'review-plan'` and `role-cli value`. |
| 4 | Routing config value | `C --settings empty.json --routing-config route.json step "$W"` | Exit 20 naming `gpt-5` and `config-default value` for model and effort sources. |
| 5 | Declared values pass | `C --settings empty.json --model opus --effort high step "$W"` | No routing refusal; ends with `has no .workflow-manager/installation.json -- not a Workflow-managed repository`. |
| 6 | Setting extends the models | `C --settings extra.json --routing-config route.json step "$W"` | Flow 4's config is now accepted: same "not a Workflow-managed repository" ending. The effort `low` is declared, so only the model needed the row. |
| 7 | Setting is per harness | Change `extra.json` to `{"harness":{"models":{"codex":["gpt-5"]}}}` and rerun flow 6. | Refused again (exit 20): a `codex` entry does not add a model for `claude-code`. |
| 8 | No job record on refusal | After flows 1-4: `ls "$W/rt"` | Only `identity.json`, `notifications.jsonl`, `source` (no job or run records). |
| 9 | Row shape is strict | `C --settings badshape.json settings show; echo rc=$?` | Error `harness.models.claude-code must be a list of model strings, got "x"`, exit 20. Also try a list holding `""` and one holding `"-x"`: each refused naming the key. |
| 10 | Unknown harness name | `C --settings unknown.json settings show`, then `C --settings unknown.json settings clean` (on a copy) | `show` succeeds with a warning `unknown key(s) harness.models.nope; they are ignored` and `harness.models = {} (file)`; `clean` prints `removed from ...: harness.models.nope`. |
| 11 | Default and show | `C --settings empty.json settings show \| grep harness.models` | `harness.models = {} (default)`. |
| 12 | Fill keeps values | `settings show` does not write, so this is covered by `tests.test_settings` (a generation-15 file gains the row, other values kept); to see it by hand, run any command that fills defaults against a copy of an existing settings file and `grep -A1 '"harness"'` the copy. | The copy gains `"harness": {"models": {}}` and `_table_generation` 16; every other value is unchanged. |
| 13 | Harness table | `python3 tools/harness_table.py --check; echo rc=$?` and read `docs/guide/harnesses.md` | rc 0. First table has `Models` and `Efforts` columns: `claude-code` seven ids / five efforts, `codex` `gpt-6.1-sol` / `high`, `xhigh`. Prose says a model outside the list is added with `harness.models`. |
| 14 | Docs | `python3 tools/check_docs.py; echo rc=$?`; read `docs/compatibility.md` "Upgrading to 1.22.0", `docs/common-problems.md`, `docs/guide/runtime.md` | rc 0. The section lists the newly refused configurations, exit 20, the message, and the JSON entry to add; the common-problems entry links to it; `commands.md` no longer calls the values unvalidated. |
| 15 | Lane routing still passes | `PYTHONPATH=$PWD python3 -P -c "from controller import routing; c=routing.load_routing_config('tests/fixtures/lane_routing.json'); routing.RoutingOptions(config=c, config_source='routing-config'); print('ok')"` | Prints `ok` (every role of the committed lane copy is accepted). |
| 16 | Automated tests | `python3 -m unittest tests.test_harness tests.test_routing tests.test_settings tests.test_harness_table` (full suite: `python3 tools/run_tests.py` if the sandbox allows) | Green. Known unrelated failures (see notes below) are not H8. |

### Known limitations / out of scope

- No Codex worker role exists, so with today's registry only a model or effort `claude-code` does not declare can be
  refused; the Codex-routed refusal is exercised by tests that patch the registry.
- The reviewer settings (`reviewer.model`, `reviewer.effort`) keep their own launch-time checks; they are not covered here.
- No setting extends the effort list; a per-harness built-in model (H4) and fallback chains (H9) are later items.
- A routing file valid until 1.21.x that names an undeclared model or effort is now refused (documented tightening, D5).
- `tests/test_claude_build` may error under the deep-TMPDIR runner (socket path length) and `plan_stage_decisions.json --check` drifts on main too; neither is H8.
