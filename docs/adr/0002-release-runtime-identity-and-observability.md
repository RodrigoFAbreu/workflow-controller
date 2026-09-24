# ADR 0002: Release runtime identity and worker observability

Status: accepted. See
`docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md` for the
full design record (work item `workflow-controller-release-runtime-observability`,
version `1.1.0`). This document records the decisions and the alternatives
they rejected. It changes no exit code: the table in
[ADR 0001](0001-controller-generation-1-architecture.md) stays the
normative exit-code contract, unchanged.

## Context

Before this release the Controller could only launch workers from its own
Git checkout. `step`, `run` and `resume` snapshot the running Controller's
source with `git archive`, so a wheel installed with pipx refused every
acting command (`git archive failed`, exit `20`), and the wheel did not
even ship `controller/GENERATION.json`. The Controller had no version
source beyond a static `pyproject.toml` value, no build identity and no
release pipeline. Workers ran `claude -p --output-format json`, so their
progress was invisible until they returned, often after many minutes.

The two Generation 1 invariants hold unchanged: the Controller never
writes Workflow lifecycle state, and it never crosses a human gate.

## Decisions

### Version and build identity

- **One version source.** `controller/version.py` holds
  `__version__ = "1.1.0"`, read by `pyproject.toml` as its dynamic
  version. Tags are strictly `vMAJOR.MINOR.PATCH`. Pre-releases were
  rejected: semver's `-rc.1` and PEP 440's `rc1` spell them differently,
  so a tag and a wheel version would stop being the same string.
- **Version and generation stay separate.** `controller/GENERATION.json`
  is the compatibility axis that handoff and `validate_record` compare. A
  MAJOR bump does not imply a generation bump, or the reverse, although
  pairing them is recommended. Merging the two was rejected because a
  compatible feature release would otherwise force a handoff.
- **`controller/BUILD_INFO.json`** is written only into built artifacts,
  by a `build_py` hook in `setup.py`. It records `version`,
  `source_commit`, `source_dirty` (`null` when provenance is unknown),
  `package_digest`, `build_origin` (`release` or `local`) and
  `release_tag`, and deliberately no timestamp. `setup.py` was chosen
  over a `pyproject`-only `cmdclass` reference because it is the stable,
  documented extension point. The hook does nothing for an editable
  install, forces fresh copies, and refuses a stale `build/` rather than
  hash files the commit does not contain.
- **`package_digest`** hashes file contents and relative paths, not
  modes, so pip's installed modes cannot move it. It is separate from the
  snapshot's `tree_digest`.
- **`build_origin: "release"` is a claim, not a proof.** It comes from an
  environment variable at build time. Release provenance is the wheel's
  checksum in the GitHub Release's `SHA256SUMS`. Build-provenance
  attestation is a possible later item.

### Runtime kinds and their resolution order

`identity.resolve_runtime(code_root)` classifies the running code, in
this order:

1. a pinned snapshot (`SOURCE_PIN.json`) carries the kind recorded when
   it was materialised; a pin without `runtime_kind` reads as `source`;
2. **ambiguity first:** a `code_root` holding both
   `controller/BUILD_INFO.json` and `.git` is `unidentified`, decided
   without Git;
3. **`package`:** a valid `BUILD_INFO.json` and no `.git`;
4. **`source`:** a `.git` whose top level is `code_root` and which tracks
   `controller/__init__.py`;
5. **`unidentified`:** anything else.

Rejected: reading `direct_url.json` or any installer metadata. Only the
package's own files and, for a checkout, Git decide the kind. Letting
either kind win the ambiguous case was rejected too. If `package` won, a
checkout would read its approved generation from the worktree, so an
uncommitted generation edit could trigger a handoff. If `source` won, a
file the runtime claims never to trust would sit silently in the tree.

A package or unidentified runtime with no `.git` makes no Git call at
all, from resolution through materialisation, identity and handoff.

### The package snapshot

A package runtime still materialises a snapshot. It copies the installed
`controller/` (regular files only; a symlink or special file is refused),
checks the **copy** against `package_digest`, reads the generation from
the copy, and re-executes from `source/<tree_digest>/` like a source
runtime.

Rejected: executing `site-packages` in place. It is simpler, but a
`pipx install --force` mid-run would change the bytes a running
generation executes, which Generation 1 forbids. Checking the copy rather
than the installed tree means a concurrent reinstall cannot change the
bytes after the check.

### Gating

A package whose `source_dirty` is not `false` (built from uncommitted
changes, or without verifiable provenance) needs `--allow-dirty-source`,
the rule a dirty checkout already follows. An `unidentified` runtime's
acting commands refuse (exit `20`); its read-only commands work.
Rejected: always running any package, which would make a job record's
`controller_source_commit` unverifiable without saying so.

### The runtime-root ladder

Row 3 (`<origin checkout>/.controller`) applies only to `source`. A
package or unidentified runtime falls through to row 4
(`$XDG_STATE_HOME/workflow-controller`, else
`~/.local/state/workflow-controller`). This fixes a wheel installed into
a venv inside a checkout, which resolved to `site-packages/.controller`.

### Generation handoff by kind

- `source`: the approved generation is `HEAD:controller/GENERATION.json`
  in the origin checkout, unchanged.
- `package`: the approved generation is the **installed** package's
  `controller/GENERATION.json`, under the `site-packages` directory the
  snapshot was taken from (`origin_source_root`). Installing a package is
  this runtime's approval act. Newer means a handoff (exit `50`), equal
  means continue, older raises `GenerationHandoffPendingError`. A missing
  or unreadable installed package is a `SourceSnapshotError`
  (`raised_by: "detect"`, exit `20`). That includes the window in which
  `pipx install --force` recreates the venv, and a Python minor-version
  change that moves `site-packages`.

A package runtime never consults its source checkout, so its behaviour
and reported identity do not depend on that checkout existing.

### Where identity is recorded

`identity.runtime_record` produces one `controller_runtime` block
(`runtime_kind`, `version`, `source_kind`, `source_commit`,
`tree_digest`, `package_digest`, `build_origin`, `release_tag`,
`generation`). It is written into `SOURCE_PIN.json`, `identity.json` and
every job record; `handoff.json` gains `version`. `inspect`/`explain
--json` carry it as `controller`. `status` opens with the running
Controller's description, which is also `--version`'s second line.
Every addition is additive: `validate_record` reads none of it, and job
records stay `schema_version: 1`.

### Streaming worker output to files

Workers run `claude -p <task> --output-format stream-json --verbose`.
The Controller creates `jobs/<job_id>/worker.stdout` and `worker.stderr`
(mode `0o600`) before the spawn and hands them to the worker, which
writes its stream into them itself. There is no pipe through the
Controller.

- Control returns when the direct child has exited **and** its process
  group is empty, instead of at EOF on two pipes. A descendant that
  detached with `setsid` is no longer waited for; a same-group descendant
  that holds neither stream now is, visibly, bounded only by `--timeout`.
- An orphaned worker no longer gets `EPIPE` after a Controller Ctrl-C, so
  it runs to completion with its output preserved.
- Parsing is strict: every line must be one JSON object and exactly one
  `result` event must come last. Anything else is `AMBIGUOUS`, the same
  fail-closed rule the single-document parser applied. The four-outcome
  classification and every reconciliation input are unchanged.
- The raw logs are verbatim. Thinking blocks the CLI may emit are kept as
  evidence and never rendered. Filtering at write time was rejected
  because the durable log would then differ from what the worker produced.

Rejected: teeing through a Controller-owned pipe. A Controller that dies
would then take the worker's output with it.

### Lifecycle event logs

Two append-only logs, written whether or not anyone follows:

- `jobs/<job_id>/events.jsonl`, one line per job-record transition
  (`planned`, `launched`, `worker_spawned`, `worker_exited`,
  `completed`, `finished`/`failed`/`incomplete`, `gate_blocked`,
  `declined`, `handoff_pending`, `worker_not_started`, `reconciled`,
  `abandoned`). The sequence number comes from the job record's additive
  `event_seq`, so `resume` and `--abandon` continue it without reading
  the log;
- `runs/<run_id>.json` and `runs/<run_id>/events.jsonl` for each `step`
  or `run`: the Controller's own process identity, its runtime, its
  state (`running`, `ended`, `interrupted`), exit code and jobs. `main()`
  closes the run once, with the final exit code.

Every event and run-record write is best-effort: a failure prints one
warning per process and never changes a lifecycle outcome. The job
record stays the only authority. Rejected: failing the lifecycle on a
lost event, which would let observability change outcomes. There is no
retention or pruning.

### `follow`'s zero-write contract

`workflow-controller follow [--job ID | --run ID] [--from-start] [<repo>]`
is dispatched before pinning, runtime-root creation, materialisation and
the `identity.json` write. It resolves the runtime root read-only, the
same way `step` does, takes no lock (the lifecycle-lock report reads
`/proc/locks` and never acquires), sends no signal and only reads files.
A missing runtime root means there is nothing to follow.

**`follow` uses only exit codes `0`, `2` and `20`:**

| Code | When |
|---|---|
| `0` | the followed run or job ended, there was nothing to follow, or Ctrl-C |
| `2` | usage error |
| `20` | an unknown id, another target's record, or an unreadable record |

It does not mirror the followed run's exit code, so its own code means
one thing; it prints the run's code in its final line.

Open item, recorded rather than decided: a `follow` whose stdout reader
has gone away currently dies of an unhandled `BrokenPipeError` (Python's
exit `120` with a traceback). The followed worker and run are
unaffected, which the isolation tests assert, but the code is outside
the three above.

### Presentation-only enforcement

Observation must never change an outcome. This is enforced
structurally:

1. no lifecycle module (`job`, `evidence`, `decision`, `routing`,
   `worker`, `handoff`) imports `observe`, and the dependency-order test
   pins it;
2. an AST test pins that `args.follow` is read only in
   `cli._start_follower`, which starts the renderer thread;
3. the logs are written identically with or without a follower, and a
   follower's presence is recorded nowhere (a run record's `command` is
   the subcommand name, never argv);
4. `follow` is a separate read-only process. The in-process `--follow`
   renderer is a daemon thread writing to its own `os.dup(2)` with
   `os.write`, in chunks of at most `PIPE_BUF` after a bounded
   `poll(POLLOUT)`, keeping a 16 KiB reserve in a stderr pipe for the
   Controller's own messages. It disables itself rather than block, and
   its failures stay in the thread. Writing through `sys.stderr` was
   rejected: a thread blocked inside its buffered writer holds the lock
   at interpreter finalisation, and CPython aborts;
5. an equivalence test runs one scripted lifecycle unfollowed, with
   `--follow`, and with an external `follow` attached and `SIGKILL`ed
   mid-job, and requires equal exit codes and equal normalised durable
   results.

### CI and release

- The three workflow files are rendered from a Python model in
  `tools/ci_workflows.py`, because the tests are stdlib-only and cannot
  parse YAML. JSON-flavoured YAML and a hand-written YAML parser were
  rejected. `--check` and a test fail on any drift.
- `validate.yml` is the one definition of required validation (the
  Controller suite in seven named shards, the seven frozen conformance
  suites, and the wheel build and packaged-runtime tests), called by both
  `ci.yml` and `release.yml`. One job per test module was rejected as
  about 33 jobs per push.
- `ci.yml` cancels an older run on the same ref. `release.yml` never
  cancels: runs for one tag queue.
- A release refuses a tag that disagrees with the version, a commit not
  on `main`, a wheel that fails verification, a tag that already has a
  release (an undecidable lookup also refuses), and a tag moved since
  the build. It then publishes an immutable GitHub Release with the wheel
  and `SHA256SUMS`. Actions in `release.yml` are pinned to commit SHAs
  because its `publish` job can write; `ci.yml` and `validate.yml` keep
  major tags.
- `workflow-conformance.yml` is managed by the Workflow Manager and left
  untouched, at the cost of running conformance twice per push.

## Consequences

- Operators install and upgrade with pipx; a checkout is needed only for
  development.
- Every job record says exactly which Controller build produced it.
- A running generation still never executes bytes that can change under
  it.
- A worker's progress is visible live and replayable later, with no
  effect on what the Controller decides.
