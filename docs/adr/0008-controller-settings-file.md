# ADR 0008: The Controller settings file

Status: accepted (2026-10-01). See
`docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md` for the full
design record (work item `workflow-controller-settings-and-telemetry`,
`docs/ROADMAP.md` step C3, section 1.4). The code ships in 1.5.0. This
document records where the settings file lives, how its values are chosen, how
the Controller fills and migrates it, and which values are settings and which
stay constants. It adds no exit code: a settings refusal is a
`ControllerError` and exits 20, as the table in
[ADR 0001](0001-controller-generation-1-architecture.md) already says. The
operator's view is in [the runtime guide](../guide/runtime.md).

## Context

Through 1.4.2 every operational tunable was a module constant: the drain
detach bound, the Git, release-command and Workflow-query timeouts, the run's
step limit, the follower's heartbeat and replay count, and the pull-request
list limit. Changing one meant editing the installed package. Routing came
only from `--routing-config`, a file each lane passed on every invocation.

The Controller runs from one shared install for every repository and both
lanes. Several Controller releases can be installed side by side, and an older
one can still drive a milestone after a newer one is installed. Any shared
file has to stay readable by both.

## Decisions

### One user-level file

- **Location.** The first of these that is set: the global `--settings PATH`
  option (made absolute, and passed on across the re-exec),
  `$WORKFLOW_CONTROLLER_SETTINGS`,
  `$XDG_CONFIG_HOME/workflow-controller/settings.json`, and
  `~/.config/workflow-controller/settings.json`.
- **One file per machine and operator, not per repository.** The tunables are
  properties of the machine and the operator. The file is shared by every
  repository and lane that uses the install. Nothing is ever written into a
  repository's tree: that would be a tracked-tree change.
- **Format.** JSON in `write_json`'s canonical form, `schema_version: 1`, with
  one object per section (`worker`, `run`, `follow`, `timeouts`, `forge`,
  `routing`) and two bookkeeping keys, `_defaults_written` and
  `_table_generation`.

### Precedence

A value comes from the first of: the command-line flag that overrides it, the
file, the built-in default. `settings show` prints each value with its source
(`cli`, `file` or `default`). A flag's override applies to that invocation
only; the file is never rewritten to match a flag. `--routing-config` replaces
the file's `routing` section whole, and keeps its own strict parse byte for
byte, so the lanes' existing invocations behave as before.

`--timeout`, `run --max-steps` and `resume --drain-timeout` refuse zero, a
negative number or a non-integer with exit 2. The table's bounds apply to the
file only.

### Strict validation, never a rewrite

Every command loads and validates the file. A file that is not UTF-8 JSON,
has a duplicate key, a `schema_version` other than 1, a section that is not an
object, a wrong type (a boolean is not an integer), a value out of bounds, a
malformed `_table_generation` or `_defaults_written`, or a malformed routing
section is refused with `SettingsError` (exit 20), which names the path and
the dotted key. The Controller never repairs or rewrites a file it refuses.

### The fill and the forward-only migration

- **Only writing commands fill.** `step`, `run`, `resume`, `milestone-binding`
  and `settings clean` add every missing setting with its default before they
  run. The read-only commands (`inspect`, `explain`, `status`, `follow`,
  `telemetry`, `settings show`, `settings path`) never write the file: they use
  the default for a missing key, and for a missing file. Observation stays
  passive (roadmap principle 8), and the file still migrates on the first real
  step after an upgrade.
- **Each default written is recorded.** The fill records
  `_defaults_written[key] = {value, generation}`, where `generation` is the
  table generation that last set that key's default.
- **An untouched value moves forward only.** It moves to a newer default only
  when the stored value still equals the recorded one (the operator never
  changed it) and this release's generation for the key is greater than the
  recorded generation. A value the operator set never moves. A release with
  an older or equal generation leaves the value alone, so two releases sharing
  the file never undo each other. `_table_generation` is never lowered.
- **The compatibility rule.** A release never moves a default outside the
  bounds any earlier generation gave the key, and never changes a key's type.
  A change of type or range retires the key and adds a new one. Otherwise an
  older release still installed would refuse a file the operator never
  edited. A test keeps every generation's bounds and type and checks each
  default against all of them.
- **One writer at a time.** The fill and `clean` run their whole
  read-modify-write under an exclusive lock on the sibling file
  `settings.json.lock`, re-reading the bytes there, and write only when the
  content changes. A file that cannot be written is still used, with one
  warning.

### Unknown keys

An unknown key is ignored with one warning per invocation. When the file was
last filled by a newer release (its `_table_generation` is greater than this
release's), the warning says so. `settings clean` removes unknown keys, but
refuses, removing nothing, a file filled by a newer release: this release
cannot tell that release's keys from retired ones.

The `routing` section follows the same rule. An unknown role, route field or
key directly under `routing` is ignored with the warning, so a newer release
that adds a role does not lock an older one out of the shared file.
`routing.schema_version` is reserved and refused: it means a
`--routing-config` file was pasted in whole. Everything else in the section
stays strict. Both sources share one validator,
`routing.validate_routing_mapping`, so a known role resolves to the same route
through either.

### The settings/constant boundary

A value is a setting when an operator can reasonably need a different value
on their machine and a wrong value cannot corrupt state: a time bound, a
count, a list limit, a route. A value stays a constant when it is part of a
contract: an exit code, a schema version, a file name, a lock order, a
Workflow phase, a gate code. v1's table is closed and pinned by a test (the
key, type, default, bounds, CLI flag and generation of every row). A release
that adds or retires a key raises `TABLE_GENERATION`.

The leaf modules keep their constants as the built-in defaults and read the
process-wide value at call time. `settings.apply_process_defaults`, called
once per invocation before any thread starts, is the only production writer
of those values. A launched job records the settings it ran with (the
`controller_settings` block: path, the file's SHA-256, every value and its
source).

## Alternatives rejected

- **A per-repository settings layer.** It adds a precedence level and a new
  location, for tunables that belong to the machine. If per-repository
  routing is ever needed, a read-only
  `<runtime>/repositories/<key>/settings.json` layer between the flags and the
  user file can be added; it would never be filled.
- **Filling the file from every command.** `status` and `follow` would then
  write outside the runtime root.
- **Refusing unknown keys.** A newer release's keys would lock an older
  release, possibly the one driving a milestone, out of the shared file.
- **Moving every untouched value to the current default.** Two releases
  sharing the file would undo each other's defaults on every step.
- **Removing `--routing-config`.** The lanes pass it today. It stays, and
  replaces the section whole; deprecating it is left for later.
