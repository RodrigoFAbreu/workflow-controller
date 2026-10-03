# Runtime state and runtime identity

> For: anyone who needs to know where the Controller keeps its state or what code it is running. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

[Back to the documentation map](../README.md)

## Controller-owned runtime state

The Controller keeps its own durable state under a runtime root: pinned
source identity, job records (`jobs/`, with abandoned originals under
`jobs/abandoned/`), per-job logs (`jobs/<job_id>/`), run records
(`runs/`), milestone binding records (`repositories/`, below) and the
pending handoff. The root is resolved by a ladder, and
the first row that applies wins:

1. `--runtime-dir`;
2. `WORKFLOW_CONTROLLER_HOME`;
3. `<origin checkout>/.controller/`, only for a **source** runtime;
4. `$XDG_STATE_HOME/workflow-controller`, or
   `~/.local/state/workflow-controller` when `XDG_STATE_HOME` is unset.

A package runtime (a pipx or other wheel install) never uses row 3, even
when its venv sits inside a Git checkout, so without the first two rows
it uses row 4. Before 1.1 a wheel installed into a venv inside a
checkout resolved to `<site-packages>/.controller`. Every acting command
from such an install failed, so that directory holds no job history, and
it can be deleted. `status` prints the resolved root and its ladder row.

This tree is disposable by design. It is never part of any managed
target repository's own state, and the Controller never writes into a
target repository's `WORKFLOW_STATE.json` -- only a worker running a
real Workflow command does that. The lifecycle lock is an `flock` on the
target's existing git directory and creates nothing there. Deleting
`runs/` or `jobs/<job_id>/` loses presentation history only: no
lifecycle decision reads them.

`repositories/<repo_key>/milestones/` holds one binding record per
milestone of a policy-enabled target (`<work_item_id>.json`), its
append-only `<work_item_id>/events.jsonl`, and any
`<work_item_id>.abandoned-<n>.json` left by a re-planned id. `repo_key`
is the SHA-256 of the target's canonical `git rev-parse --git-common-dir`,
so every worktree of one repository shares it. Unlike `runs/`, these
records **are** read by lifecycle decisions: they are the Controller's
only memory of which branch and pull request belong to a milestone. Do
not delete them while a milestone is in flight, and include them in any
backup of the runtime root. If they are lost anyway (a new machine, for
example), the next step with `HEAD` on the milestone branch re-adopts the
binding and its one open pull request. A branch that exists only on the
remote, or two merged pull requests for one branch, refuses: the
Controller cannot tell which is its own, and the recovery is to restore
`repositories/<repo_key>/` from the backup. A 1.1.1 runtime ignores the
directory.

## Runtime identity

Every Controller process knows what code it is running, and records it.
There are three runtime kinds:

- **`package`**: an installed wheel. The wheel carries
  `controller/BUILD_INFO.json`, written by the build (`setup.py`'s
  `build_py` hook), which records the version, the source commit, whether
  the build's inputs had uncommitted changes, a digest of the package's
  files, the build origin (`release` or `local`) and the release tag.
  A package runtime never runs Git and never consults the checkout it was
  built from. `step`, `run` and `resume` copy the installed package into
  a snapshot and check the copy against the recorded package digest; an
  installation edited after it was built is refused (exit `20`). A wheel
  built from uncommitted changes, or with no verifiable provenance (for
  example from an sdist), needs `--allow-dirty-source`, like a dirty
  checkout.
- **`source`**: a Git checkout whose top level holds the running
  `controller/` package, tracked. This is what `pip install -e .` gives
  you.
- **`unidentified`**: anything else, for example a wheel built without the
  hook, or a checkout that contains a stray `controller/BUILD_INFO.json`
  (delete it to run from source). Read-only commands still work and print
  the reason. `step`, `run` and `resume` refuse with exit `20`.

The Controller never reads installer metadata such as `direct_url.json`.

`workflow-controller --version` prints two lines and writes nothing:

```
workflow-controller 1.2.1
runtime: package (release v1.2.1; built from 0123456789ab; package 3f2a1c9d0b7e)
```

Line 1 is always `workflow-controller <version>`. Line 2 is one of
`package (release ...)`, `package (local build from <commit>)` with
`, uncommitted changes` when that applies, `package (local build, unknown
provenance)`, `source (<checkout> @ <commit>)` with the same suffix, or
`unidentified (<reason>)`. `status` opens with the same text:
`controller: workflow-controller 1.2.1 -- package (...)`.

Every job record carries a `controller_runtime` block (`runtime_kind`,
`version`, `source_kind`, `source_commit`, `tree_digest`,
`package_digest`, `build_origin`, `release_tag`, `generation`), and so do
`identity.json` and `SOURCE_PIN.json`. `inspect --json` and
`explain --json` carry it as `controller`. The older
`controller_generation`/`controller_source_commit`/`controller_source_tree_digest`
fields stay.

What `build_origin: "release"` proves is limited. The build sets it from
an environment variable, so any local build can claim it. The proof that
a wheel is a release is its checksum in the GitHub Release's
`SHA256SUMS`, not `--version`. Build-provenance attestation may come
later.

The version is always a plain `MAJOR.MINOR.PATCH`. Where it comes from:

- a release wheel carries its release tag's version, set at build time;
- a local wheel, and a **source** runtime, carry `pyproject.toml`'s
  static `[project].version` when it declares one (every checkout of
  this repository from before the
  [cutover](ci-and-releases.md#cutover-from-the-version-file-model)).
  Otherwise, with `dynamic = ["version"]`, they carry the highest
  `v<MAJOR.MINOR.PATCH>` tag reachable from the checkout's `HEAD`, or
  `0.0.0` when none is (a shallow clone without tags, for example). A
  checkout between two releases therefore reports the last release it
  contains; line 2 of `--version`, and `BUILD_INFO.json` for a wheel,
  tell it apart by its commit and build origin;
- a pinned source snapshot has no `.git`, so with a dynamic version it
  is derived from the origin checkout's tags at the snapshot's commit,
  and recorded in `SOURCE_PIN.json`.

The version and the generation (`controller/GENERATION.json`) are
separate. The generation is the compatibility axis that handoff and
job-record validation compare.

## The settings file

One JSON file holds the Controller's tunables and its default worker
routing. It belongs to the user, not to a repository: every repository
and every lane on the machine that runs as that user shares it. Change
it with care while another Controller runs. The design record is
[`docs/adr/0008-controller-settings-file.md`](../adr/0008-controller-settings-file.md).

### Where it is

The first of these that is set wins:

1. the global `--settings PATH` option;
2. `$WORKFLOW_CONTROLLER_SETTINGS`;
3. `$XDG_CONFIG_HOME/workflow-controller/settings.json`;
4. `~/.config/workflow-controller/settings.json`.

`workflow-controller settings path` prints the one in use. A missing
file is never an error: every setting then takes its built-in default.

### What it holds

| Key | Type | Default | Bounds | Command-line override |
|---|---|---|---|---|
| `worker.drain_detach_seconds` | integer | `10800` | 60 to 604800 | `resume --drain-timeout` |
| `worker.timeout_seconds` | integer or `null` | `null` (no limit) | 60 to 172800 | `--timeout` |
| `run.max_steps` | integer | `20` | 1 to 1000 | `run --max-steps` |
| `follow.heartbeat_seconds` | integer | `30` | 1 to 3600 | none |
| `follow.replay_events` | integer | `20` | 0 to 10000 | `follow --from-start` |
| `timeouts.git_seconds` | integer | `600` | 30 to 7200 | none |
| `timeouts.release_command_seconds` | integer | `1800` | 60 to 21600 | none |
| `timeouts.workflow_query_seconds` | integer | `120` | 10 to 3600 | none |
| `forge.pr_list_limit` | integer | `200` | 50 to 1000 | none |
| `merge.auto` | boolean | `true` | -- | none |
| `merge.wait_seconds` | integer | `3600` | 0 to 86400 | none |
| `merge.poll_seconds` | integer | `30` | 10 to 600 | none |
| `routing` | object | `{"default": {}, "roles": {}}` | see [The routing section](#the-routing-section) | `--routing-config`, `--model`, `--effort`, `--role-model`, `--role-effort` |

Each default is the value the Controller used before the file existed,
so a file holding only defaults behaves as no file. A boolean is not an
integer: `true` is refused where an integer is expected. A boolean
setting takes only JSON `true` or `false`: an integer, a string or
`null` is refused.

The three `merge` rows (1.6.0) matter only for a repository whose policy
opts in to auto-merge (see
[Auto-merge and the release wait](milestone-branches.md#auto-merge-and-the-release-wait)):

- `merge.auto: false` stops the Controller from merging, in every
  repository on the machine: readiness then ends at `merge_pull_request`
  and a person merges. The release wait and the stop after close-out
  still follow the repository's policy, because they only read;
- `merge.wait_seconds` bounds how long one `run` step waits at a pending
  gate (checks, merge or release pending) before it ends at that gate;
  `0` means `run` does not wait. `step` never waits, whatever the value;
- `merge.poll_seconds` is how often that wait re-reads GitHub. Each poll
  makes a few `gh` calls, so the 10-second lower bound keeps a waiting
  `run` to at most 360 polls an hour, inside GitHub's API limits.

A file filled by this release looks like this (the `_defaults_written`
entries are shortened):

```json
{
  "_defaults_written": {"merge.auto": {"generation": 2, "value": true}, "...": "..."},
  "_table_generation": 3,
  "follow": {"heartbeat_seconds": 30, "replay_events": 20},
  "forge": {"pr_list_limit": 200},
  "merge": {"auto": true, "poll_seconds": 30, "wait_seconds": 3600},
  "routing": {"default": {}, "roles": {}},
  "run": {"max_steps": 20},
  "schema_version": 1,
  "timeouts": {"git_seconds": 600, "release_command_seconds": 1800, "workflow_query_seconds": 120},
  "worker": {"drain_detach_seconds": 10800, "timeout_seconds": null}
}
```

### Which value wins

For each setting, a flag given on the command line wins, then the file,
then the built-in default. The bounds apply to the file only. A flag
has its own rule: `--timeout`, `run --max-steps` and
`resume --drain-timeout` accept any positive integer, and refuse `0`, a
negative number or a non-integer as a usage error (exit `2`).
`workflow-controller settings show` prints each value with where it came
from (`cli`, `file` or `default`).

`--routing-config PATH` replaces the file's `routing` section whole, for
that invocation. The precedence of the routing flags is in
[Worker routing](commands.md#worker-routing).

### The fill: how the file is created and kept current

Only the commands that already write fill the file: `step`, `run`,
`resume` and `milestone-binding` (and `settings clean`, below). The
read-only commands (`inspect`, `explain`, `status`, `follow`,
`telemetry`, `settings show`, `settings path`) only read it, and never
create it.

The fill is additive. It creates the file if needed, and adds each
missing key with its default. For each key it adds, it records what it
wrote in `_defaults_written`, as `{"value": ..., "generation": ...}`.
The generation is a number each release gives each default; a release
that changes a default raises it.

A later release may move a value forward to its new default, but only
when all three hold:

- the value still equals the one recorded in `_defaults_written`, so you
  never changed it;
- the new default differs from it;
- the new release's generation for the key is greater than the recorded
  one.

1.6.0 is table generation 2: it adds the three `merge` rows to a file
filled by 1.5.0, and moves nothing else. A 1.5.0 Controller sharing the
file afterwards warns about the unknown `merge` section, ignores it,
and its `settings clean` refuses.

1.7.0 is table generation 3: it adds no key and moves no value, but it
adds two routing roles (`prepare-functional-review`,
`apply-functional-review`), so a file it fills records
`"_table_generation": 3`. A 1.6.0 Controller sharing the file afterwards
uses it as before; it warns only about a key it does not know, such as an
entry for one of the two roles under `routing.roles`, which it ignores,
and its `settings clean` refuses.

So a value you set yourself never moves. A value is never moved back to
an older default either: an older release sharing the file leaves a newer
release's value alone. `_table_generation` records the newest release
that filled the file, and is never lowered.

The fill writes only when something changed. It runs under an exclusive
`flock` on a sibling lock file (`settings.json.lock`), so two Controllers
filling the file at once never lose each other's changes. If the file
or its directory cannot be written, one warning says so, and the file's
current values still apply; missing keys take their defaults.

### When the file is refused

A file that cannot be used is refused with `SettingsError` (exit `20`)
before any job record is written, and is never rewritten. The message
names the file and the key. See
[Troubleshooting](troubleshooting.md#the-settings-file-is-refused-settingserror).

### Unknown keys and `settings clean`

A key this release does not know is ignored, with one warning per
invocation naming it. This happens when a newer release added the key,
or when this release retired it. If the file was last filled by a newer
release, the warning says so.

`workflow-controller settings clean` fills the file, then removes every
unknown key and its `_defaults_written` entry, and prints what it
removed. It refuses (exit `20`), removing nothing, when the file was last
filled by a newer release: this release cannot tell that release's new
keys from retired ones. Run `settings clean` from the newest installed
release instead.

### The routing section

The `routing` section has the same shape as a `--routing-config` file
(see [Worker routing](commands.md#worker-routing)), without
`schema_version`. Because releases share the file, it is more lenient
than that file:

- an unknown role under `roles`, an unknown field in `default` or in a
  role's entry, and an unknown key directly under `routing` are ignored,
  with the unknown-key warning above (`routing.roles.<role>`,
  `routing.default.<field>`, `routing.<key>`);
- `routing.schema_version` is reserved and refused (exit `20`): it means
  a `--routing-config` file was pasted in whole;
- everything else is strict, as in that file: a section or entry that is
  not an object, or an unusable model or effort value, is refused.

The fill only ever adds an empty `{"default": {}, "roles": {}}`. The
contents are yours, and no release migrates them. A `--routing-config`
file keeps its own strict rules, byte for byte: there an unknown role or
field is still `RoutingConfigError` (exit `20`).
