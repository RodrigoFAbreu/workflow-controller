# Active Milestone

## Status

**Implementing.** `workflow-controller-settings-and-telemetry` (`docs/ROADMAP.md` step C3, sections
1.4, 8 and 11.1.2). The plan is `docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md`,
revision 14, approved at `b0e2f9a` (`EXTERNAL_APPROVE`, review content id `db313bbe`). The base
commit is `a47e695`. Governing workflow version `2.2`, lifecycle authority Workflow 2.6.0. Pull
request title
`feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches`
(1.5.0).

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-ci-reliability.md`.

## Goal

Four things, each opt-in or behaviour-preserving by default:
- **Settings file v1 (1.4).** One user-level JSON file holds every operational tunable and the
  routing defaults. The Controller fills in missing settings, records which defaults it wrote and
  at which generation, and moves an untouched value only forward. Unknown keys are warned about,
  and `settings clean` removes them, but never a newer release's.
- **Telemetry v0 (8).** Each job records its session's totals over every `result` event, and a
  read-only `telemetry` command summarises them, deriving older jobs from `worker.stdout`.
- **Release notes follow the milestone (11.1.2).** Readiness puts the milestone's notes section
  into the pull request body, bound by a marker carrying the work-item id and a digest, and the
  release publishes the verified blocks from the squash commits of its range, or refuses.
- **The four open 1.4 patches**: parsing resume hints, a truthful manual-external gate,
  relaunch-bound tests, and a useful `status` with `--json`.

## Checkpoint progress

| Checkpoint | Status | Notes |
|---|---|---|
| CP1 The settings file | Complete | See below |
| CP2 The settings wired in | Not started | |
| CP3 Telemetry v0 | Not started | |
| CP4 Release notes follow the milestone | Not started | |
| CP5 The hints parse and the manual-external gate tells the truth | Not started | |
| CP6 Relaunch-bound tests and `status` | Not started | |
| CP7 Documentation and full verification | Not started | |

### CP1 -- the settings file

- `controller/settings.py` (new, after `routing` in the dependency order):
  - `TABLE`, the closed table of `Setting(key, type, default, minimum, maximum, cli_flag,
    generation)` (A.3), and `TABLE_GENERATION = 1`. Every default equals the constant it will
    replace.
  - `resolve_path`: `--settings`, `$WORKFLOW_CONTROLLER_SETTINGS`,
    `$XDG_CONFIG_HOME/workflow-controller/settings.json`,
    `~/.config/workflow-controller/settings.json`, each made absolute (A.1).
  - Validation (I2) refuses with `SettingsError` (exit 20), naming the path and the dotted key, and
    never rewrites the file: not UTF-8 or JSON, a duplicate key, `schema_version` other than 1, a
    section that is not an object, a wrong type (a boolean is not an integer) or out-of-bounds
    value, a malformed `_table_generation` or `_defaults_written` (not `{value, generation}` with a
    positive integer generation, or an entry for a key the file does not hold), and a malformed
    routing section. An absent `schema_version` is filled in as 1, like any missing key.
  - `load` (read-only), `fill` and `clean`. The fill and `clean` run their whole
    read-modify-write under the sibling lock `settings.json.lock`, re-reading the bytes there, and
    write only when the content changes (I4). The fill adds each missing setting with
    `_defaults_written[key] = {value, generation}`, moves an untouched value forward only when this
    release's generation for the key is greater than the recorded one, and never lowers
    `_table_generation`. When it cannot write, one warning says so, and the file's values still
    apply. `clean` refuses (exit 20, nothing removed) a file whose `_table_generation` is newer
    than this release's; otherwise it fills, then removes every unknown key and its bookkeeping.
  - Unknown keys (top level, inside a section, and in the routing section) are ignored with one
    warning per invocation, which says when the file was last filled by a newer release.
  - `resolve` gives `EffectiveSettings`: each value and its source (`cli`, `file`, `default`), and
    the routing in force (`--routing-config` replaces the section whole, I1). CP2 wires it into
    every command.
- `controller/routing.py`: `validate_routing_mapping(data, *, path, where, require_schema_version,
  unknown)`, the one validator. `parse_routing_config` calls it strictly with `where=""`, so
  `--routing-config` keeps its messages and evidence, and every `tests/test_routing.py` case
  passes unchanged. The settings section uses `where="routing."`, no `schema_version` (reserved
  there, and refused) and `unknown="ignore"` (unknown roles, route fields and keys directly under
  `routing` are left out and reported). `SECTION_KEYS` names `default` and `roles`.
- `controller/runtime.py`: `settings_target`, `settings_lock` (an `flock` on the sibling lock
  file), `read_settings_bytes` and `write_settings_atomically` (`write_json` contained against the
  file's own directory, symlinks resolved first, so a symlinked file stays a symlink).
- `controller/errors.py`: `SettingsError` (`SETTINGS_ERROR`).
- `controller/cli.py`: the global `--settings PATH`, passed on absolute across the re-exec; the
  `settings show|path|clean` subcommand (A.6), dispatched before pinning, like `follow`, since it
  touches only the settings file. `show` prints each effective value with its source (`--timeout`
  and `--routing-config` are the global overrides it can see) and writes nothing; `--json` is
  honoured.
- `tests/__init__.py` (I5): at package import, `XDG_CONFIG_HOME` points at a fresh temporary
  directory and `WORKFLOW_CONTROLLER_SETTINGS` is removed. One side effect for developers: Git
  reads `$XDG_CONFIG_HOME/git/config`, so a Git identity kept only there is not seen by the tests
  (this machine and CI use `~/.gitconfig`).
- `tests/test_settings.py` (new, 41 tests): the pinned table and the compatibility rule checked
  against every generation's bounds and type; the location order and the re-exec argv; every
  refusal, for `load`, `fill` and `clean`, with the file left unchanged; the fill (create,
  additive, write only on change, never lowering the generation, the unwritable-directory warning
  with the file's values still applied); the unknown-key warning; `clean`; the two-release test
  (N+1 moves the untouched value and adds a key; N never moves it back, warns, and its `clean`
  refuses and keeps N+1's key; an operator-set value never moves); the shared-file routing test
  (N loads with exit 0 and one warning naming `routing.roles.new-role`, the extra field and
  `routing.budgets`, resolves the known role as before, and its `clean` refuses; N+1 routes the
  new role; a refused value and a non-object `roles` still exit 20; `--routing-config` with the
  unknown role still raises `RoutingConfigError`); the routing parse tests; the lock tests (four
  concurrent fills; the fill and `clean` each re-read under a lock the test holds while it edits
  the file; a `clean` racing a fill keeps both changes); and the I5 guard (this process is
  isolated; a child started with `WORKFLOW_CONTROLLER_SETTINGS` naming a sentinel runs `settings
  clean`, `settings show` and a fill under an audit hook that fails on any open of
  `~/.config/workflow-controller/`, the sentinel or the parent's `XDG_CONFIG_HOME`; an invalid
  sentinel stays byte-identical and a missing one is never created; a direct `python3 -m unittest
  tests.test_settings...` run is isolated too).
- `tests/test_package_structure.py`: `settings` in the dependency order.
- Verified: `tests.test_settings`, `tests.test_routing`, `tests.test_cli`, `tests.test_runtime`,
  `tests.test_write_containment` and `tests.test_package_structure` pass; the full sharded suite
  (`python3 tools/run_tests.py`, in the foreground, without `PYTHONPATH` or `FORCE_COLOR`, under a
  reaping-subreaper wrapper since this session is a Controller-launched worker) passed: 2545 tests
  in 6 shards, exit 0. `~/.config/workflow-controller/` did not exist before or after the runs (I5).
