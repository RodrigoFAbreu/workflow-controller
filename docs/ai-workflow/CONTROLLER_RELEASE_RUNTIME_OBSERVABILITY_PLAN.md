# Controller release runtime and live worker observability (Revision 6)

Work item: `workflow-controller-release-runtime-observability`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `16c3fb4670bfeb9c7ce82aaff13f5a643a3f2400` (the acceptance commit of
`workflow-controller-automatic-lifecycle-orchestration`)
Lifecycle authority: installed Workflow 2.5.1 (`.claude/commands/`, `scripts/workflow_state.py`,
`scripts/workflow_fingerprint.py`, `scripts/prepare-ai-review.sh`) and each target work item's own
`governing_workflow_version`.

## Goal

This milestone turns the Workflow Controller into an isolated, versioned runtime that can be
released. It also adds live observation of the workers the Controller launches. Lifecycle
semantics do not change. Specifically:

1. **One release identity.** A single semantic version source drives the wheel metadata,
   `workflow-controller --version` and every recorded runtime identity. Every wheel carries an
   explicit build identity, `controller/BUILD_INFO.json`, holding the version, source commit,
   dirty flag, package digest, build origin and release tag.
2. **Packaged runtime isolation.** A non-editable wheel or pipx install runs every command,
   including worker launch. It needs no Git and no source checkout, and it never reads
   `direct_url.json`. It still executes from an immutable, content-verified snapshot, exactly as a
   source runtime does. The snapshot is extracted from the installed package, not with
   `git archive`.
3. **CI and release pipeline.** Controller shards and the frozen conformance suites run as
   parallel, hermetic matrix jobs. A tag-triggered release is gated on that validation. It
   verifies the tag, the version and the wheel, refuses a version that is already published, and
   publishes an immutable GitHub Release carrying the wheel.
4. **Live worker observability.** Workers emit `stream-json`, written straight into durable
   per-job logs. The Controller also appends its own lifecycle events. `step --follow`,
   `run --follow` and a zero-write `follow` command render those logs live.
5. **Observation changes no outcome.** Observation reads durable logs that are always written,
   whether anyone watches or not. Watching or not watching produces the same durable lifecycle
   results, and a follower that detaches cannot affect the worker.

## Non-goals

Carried verbatim from the milestone scope:
- Workflow Manager `.workflow-manager/installation.json` migration hardening;
- Workflow `/apply-plan-review` publication-ordering changes;
- broader shared-feedback-storage redesign;
- multi-harness/agent portability;
- interactive steering of a running worker;
- multi-work-item/concurrent execution redesign beyond what is required to safely observe the
  existing single-worker lifecycle model.

Also out of scope:
- any edit to `scripts/`, `.claude/commands/` or `.github/workflows/workflow-conformance.yml`.
  These are frozen Workflow 2.5.1 release content installed and tracked by Workflow Manager, and
  editing them would drift the managed installation;
- publishing to PyPI or any package index. The release channel is a GitHub Release asset;
- pushing tags or creating releases during this milestone. `CLAUDE.md` forbids pushing. The first
  real release is a user action after acceptance, following the documented procedure;
- log retention and pruning of the new per-job logs;
- changing reconciliation to use the newly durable worker output. `resume` keeps today's rules
  exactly (see Design, "What streaming does not change");
- the follow-ups carried from `workflow-controller-automatic-lifecycle-orchestration` (its
  optional findings O1-O3). Roadmap §1.4 folds its follow-ups in "where appropriate"; only the
  status/active-job item is folded in here (the `status` `active:` section). The others are
  deferred, as `docs/ACTIVE_MILESTONE.md` already records.

## Investigation

### The packaged-runtime failure, reproduced

To reproduce, build a wheel from `HEAD` (`pip wheel --no-deps --no-build-isolation .`) and
install it non-editably into a fresh venv. Then run
`XDG_STATE_HOME=<tmp> workflow-controller step <dir>`. It exits `20`:

```
error: git archive failed: fatal: not a git repository (or any parent up to mount point /)
```

`status` from the same install works (ladder row 4, XDG). The mechanism:

- `identity.pin()` resolves `source_root = Path(__file__).resolve().parent.parent`, which is
  `site-packages/` for a wheel install. Finding no `SOURCE_PIN.json` there, it returns an
  `"unpinned"` identity with `origin_source_root = site-packages`.
- `cli._dispatch` then calls `identity.materialise(site-packages, ...)` for every non-read-only
  command. The dirty check itself (`git status --porcelain`) fails in a non-repository, but it
  only asks "is stdout non-empty", so the tree reads as clean. `_extract_clean` then runs
  `git archive HEAD -- controller pyproject.toml` and fails. `step`, `run` and `resume` all fail
  this way before inspecting the target. `README.md` ("Installation") documents this as a known
  limitation.

### Two latent packaging defects found while planning

1. **`controller/GENERATION.json` is not in the wheel.** `pyproject.toml` declares
   `packages = ["controller"]` and no package data, so setuptools ships only `*.py`. The wheel
   listing confirms it: 15 modules, no JSON. Even with a git-free materialiser, a packaged
   runtime could not resolve its generation number. `handoff.detect()` and
   `validate_record`'s `controller_generation` comparison both need it.
2. **Runtime-root ladder row 3 misfires for a venv inside a checkout.**
   `runtime.resolve_runtime_root` uses `<origin>/.controller` whenever
   `git -C <origin> rev-parse --git-dir` succeeds. A wheel installed into `<checkout>/.venv` has
   `origin = <checkout>/.venv/lib/python3.X/site-packages`, which Git reports as inside the
   checkout's work tree. The runtime root would then be
   `site-packages/.controller`. Source mode has the same trap: Git would also answer from there,
   and `git archive HEAD -- controller` would archive an unrelated path.

### What "identity" is today

`ControllerIdentity` has three pinned kinds: `commit` (a clean `git archive` snapshot), `worktree`
(a dirty tree copied under `--allow-dirty-source`) and `unpinned` (the pre-re-exec parent).
Every non-read-only command re-execs into `<runtime_root>/source/<tree_digest>/` with `-P -B`
and `PYTHONPATH=<snapshot>`. Job records carry `controller_generation`,
`controller_source_commit` and `controller_source_tree_digest`. Nothing records a version:
`pyproject.toml`'s `1.0.1` is never read at runtime. `handoff.detect()` compares the pinned
generation with `git show HEAD:controller/GENERATION.json` in the origin checkout.

### Worker output today

`worker.launch` runs `claude -p <task> --output-format json --permission-mode <mode> ...` with
`stdout=PIPE, stderr=PIPE` and waits in `communicate()`. Nothing is visible until the worker
exits. Job step 6 then writes `jobs/<job_id>/worker.std{out,err}`. The JSON result object yields
`session_id`, `is_error` and eight optional fields. Two more properties follow from the pipe:

- because stdout is a pipe held by the Controller, an orphaned worker (after Ctrl-C) gets
  `EPIPE` on its next stdout write;
- its output is lost either way.

The installed CLI (`claude 2.1.281`) refuses `--output-format stream-json` in print mode without
`--verbose`. This was checked with a zero-spend invocation, which fails before any request:
`Error: When using --print, --output-format=stream-json requires --verbose`.

### CI today

- `.github/workflows/workflow-conformance.yml` runs seven frozen suites sequentially in one job.
  It is Workflow Manager-managed (`.workflow-manager/installation.json` tracks its digest).
- `.github/workflows/controller-tests.yml` runs the whole Controller suite sequentially after
  `pip install -e .`.

Neither workflow sets concurrency. No release workflow exists, and there are no tags.

### Test and tooling constraints

- The Controller and its tests are stdlib-only (`dependencies = []`). No YAML parser is
  available to tests.
- `tests/test_package_structure.py` pins several properties:
  - `controller/__init__.py`'s eager import list;
  - the dependency order;
  - that there are no `importlib.resources` reads;
  - the allowlist of file reads rooted at `Path(__file__)`.
  New modules and new reads rooted at `__file__` must update these deliberately.
- `tests/test_plan_document_consistency.py` parses every README/ADR invocation line with the live
  parser. It also matches the ADR exit-code table against the CLI constants. This milestone adds
  no exit code.

### Baseline test state

At the base commit, `python3 -m unittest discover -s tests -t . -v` and the seven conformance
suites pass. The previous milestone's `CP9` recorded this, and this plan's bundle
`TEST_RESULTS.md` re-records the Controller suite at the base commit. CP1 re-runs the full suite
before its first edit and records the result in its checkpoint notes. A red baseline stops the
checkpoint.

### Artifact declaration

`docs/ai-workflow/registry/workflow-controller-release-runtime-observability-artifacts.json`
starts from `generate_artifacts_declarations(..., work_item_type="product")`. It is then adjusted
the same way the previous milestone's was, plus this milestone's new territory:

- plan stage: `pyproject.toml` and `setup.py` are excluded paths, and `controller/`, `tests/` and
  `tools/` are excluded prefixes. They are implementation content, never plan design.
- implementation stage:
  - protected paths: `pyproject.toml`, `setup.py`, `README.md` (moved out of the default
    exclusion), `.github/workflows/validate.yml`, `ci.yml`, `release.yml` and
    `controller-tests.yml`. `controller-tests.yml` is protected so that its deletion is a
    reviewed fact.
  - protected prefixes: `controller/`, `tests/` and `tools/`. `docs/adr/` is inherited.
  - `.github/` stays an excluded prefix, so the managed `workflow-conformance.yml` never stales
    `technical_approval`. The four workflow paths are protected by exact path, which
    `classify_path_implementation_stage` checks first.

## Design

### Release identity

#### Version source

`controller/version.py` holds exactly one assignment, `__version__ = "1.1.0"`.

- `pyproject.toml` declares `dynamic = ["version"]` and
  `[tool.setuptools.dynamic] version = {attr = "controller.version.__version__"}`. setuptools
  evaluates a literal assignment statically, without importing the package.
- The value must match `^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`, which is plain
  `MAJOR.MINOR.PATCH`. It is also a valid PEP 440 version, so the wheel's `Version:` and the tag
  `v<version>` are the same string. Pre-release versions are out of scope (see Scope judgments).
- `1.1.0` follows `1.0.1`: this milestone adds backward-compatible surface (new commands, flags
  and additive record fields) and changes no existing contract.

`controller/GENERATION.json` stays. It is not a version. It is the compatibility axis that
generation handoff and `validate_record` compare. A MAJOR bump does not imply a generation bump,
and a generation bump does not require a MAJOR bump, although the ADR recommends pairing them.

#### Build identity: `controller/BUILD_INFO.json`

A new dependency-free module, `controller/buildinfo.py`, imports nothing from the package and
comes first in the dependency order. It defines:

- `BUILD_INFO_NAME = "BUILD_INFO.json"`;
- `compute_package_digest(package_dir)`: SHA-256 over the canonical JSON of
  `{<POSIX path relative to package_dir>: <sha256 hex of bytes>}` for every regular file under
  `package_dir`. It excludes `BUILD_INFO.json` at the root, any `__pycache__/` directory and any
  `*.pyc`. It ignores mode bits, so pip's installed file modes cannot move it. This is deliberately
  separate from `identity.compute_tree_digest`, which hashes modes and names the snapshot
  directory.
- `validate_build_info(obj, *, expected_version)`: returns the typed record or raises
  `ValueError` naming the first violation. The schema:

  ```json
  {
    "schema_version": 1,
    "name": "workflow-controller",
    "version": "1.1.0",
    "source_commit": "<40-hex> | null",
    "source_dirty": false,
    "package_digest": "<64-hex>",
    "build_origin": "release | local",
    "release_tag": "v1.1.0 | null"
  }
  ```

  - `source_dirty` is `true`, `false` or `null`. `null` means the build could not determine its
    provenance.
  - `build_origin == "release"` requires all of: `release_tag == "v" + version`, a non-null
    `source_commit` and `source_dirty == false`.
  - There is deliberately no timestamp, so identical inputs produce identical build info.
- `SEMVER_RE`, `tag_for_version(v)` and `version_for_tag(t)`.

The new `setup.py` holds only a `build_py` subclass and `setup(cmdclass=...)`. All metadata stays
in `pyproject.toml`. When `self.editable_mode` is true, the hook returns before doing anything,
exactly as setuptools' own `build_py.run` does (setuptools 84 returns early for
`editable_mode`). It never raises in that mode: `editable_wheel._safely_run` would swallow the
error with only a deprecation warning. It never writes anywhere except `build_lib`. Before the standard copy, the hook sets
`self.force = True`. setuptools' `Command.copy_file` passes `update=not self.force` and
`build_py` preserves mtimes, so without it a source file whose content changed while its mtime did
not advance past the previous copy's (restored from an archive, or copied with `cp -p`) would keep
its old bytes in `build_lib` and be hashed into `package_digest` under a commit that does not
contain them. After the standard copy, the hook:

1. loads `controller/buildinfo.py` and `controller/version.py` by file path
   (`importlib.util.spec_from_file_location`). It never imports `controller/__init__.py`.
2. derives provenance from the source directory:
   - `git rev-parse --show-toplevel` must equal the source directory, or provenance is unknown;
   - `source_commit` is `git rev-parse HEAD`;
   - `source_dirty` is whether `git status --porcelain -- controller pyproject.toml setup.py`
     produces output. This is the same scoping idea as `identity._is_dirty`, widened to the build
     inputs.
   - Outside a Git work tree (for example a build from an sdist), both are `null`.
   - A dirty tree records `source_commit` as `HEAD` together with `source_dirty: true`. The
     runtime never treats that commit as an identity (see "Gating").
3. sets `build_origin`. It is `"release"` iff `WORKFLOW_CONTROLLER_RELEASE_TAG` is set. The hook
   then enforces the release invariants above, and a violation fails the build with a message.
   Otherwise it is `"local"`.
4. refuses a stale build directory. `pip wheel .` builds in-tree through `build/lib`, and a
   module deleted or renamed since an earlier local build would otherwise stay in
   `build_lib/controller`, be shipped, and be hashed into `package_digest` under a commit that
   does not contain it. The expected file set is this run's own
   `self.get_outputs(include_bytecode=False)` under `build_lib/controller`: the modules plus
   the declared package data. Any other regular file under `build_lib/controller`, ignoring
   `__pycache__/`, `*.pyc` and a previous `BUILD_INFO.json`, fails the build. The message names
   the stray files and says to delete `build/`. The hook deletes nothing. CI's fresh checkouts
   never hit this.
5. computes `package_digest` over `build_lib/controller` and writes
   `build_lib/controller/BUILD_INFO.json`.

The file exists only inside built artifacts. It is never written into the source tree, and
`.gitignore` gains `controller/BUILD_INFO.json` as a guard. An editable install maps the package
to the checkout, where the file does not exist, so an editable install is a source runtime by
construction. A stray copy of the file in a checkout is not trusted: see `resolve_runtime`'s
ambiguity rule below.

`[tool.setuptools.package-data] controller = ["GENERATION.json"]` makes the generation file ship.
`BUILD_INFO.json` is added by the hook, so it needs no package-data entry.

#### `workflow-controller --version`

This is a top-level flag with a custom `argparse.Action`. The version text is computed only when
the flag is used, so `build_parser()` stays side-effect free for the invocation-line tests. It
prints two lines and exits `0`:

```
workflow-controller 1.1.0
runtime: package (release v1.1.0; built from 0123456789ab; package 3f2a1c9d0b7e)
```

- A local build shows `package (local build from 0123456789ab)`, with `, uncommitted changes` or
  `, unknown provenance` appended where applicable.
- A source runtime shows `source (<checkout> @ 0123456789ab)` or
  `source (<checkout> @ 0123456789ab, uncommitted changes)`.
- Anything else shows `unidentified (<reason>)`.

Line 1 is exactly `workflow-controller <controller.version.__version__>`, which is the contract
tests and the release pipeline assert. `--version` resolves no runtime root, writes nothing,
materialises nothing and needs no subcommand.

The flag lands in two steps. CP1 prints line 1 only, because `describe_runtime` does not exist
yet. CP2 adds line 2, and its tests assert the two-line form for each runtime kind.

### Runtime identity

#### Runtime kinds

`identity.resolve_runtime(code_root)` classifies the code that is actually running.
`code_root` is `Path(__file__).resolve().parent.parent`. The order is:

1. **Pinned snapshot.** `code_root/SOURCE_PIN.json` exists. This is today's branch: the pin
   carries the kind that was recorded at materialisation.
2. **`package`.** `code_root/controller/BUILD_INFO.json` exists and `code_root/.git` does not
   (the ambiguity rule below is checked first). The file must validate against
   `controller.version.__version__`. A malformed file or a version mismatch yields
   `unidentified` with the reason.
3. **`source`.** Evaluated only when `code_root/.git` exists, as a directory or as a gitfile
   (`os.path.lexists`, no Git call). A checkout's top level always has one. A `site-packages`
   directory never has one, even inside a checkout, so a package tree never reaches a Git call
   here. When the precondition holds, `git -C code_root rev-parse --show-toplevel` must equal
   `code_root`, and `git -C code_root ls-files --error-unmatch controller/__init__.py` must
   succeed. The package really is the tracked `controller/` of that checkout. Any `OSError` from
   launching `git`, including `FileNotFoundError` when no `git` is on `PATH`, means "not
   `source`". It is never propagated: these two probes catch it themselves, because
   `identity._run_git` is a bare `subprocess.run` that does not.
4. **`unidentified`.** Anything else. Examples: a wheel built by a tool without the hook, a copied
   tree, or a site-packages directory inside a checkout that has no build info.

**Ambiguity is fail-closed, and decided without Git.** When `code_root/controller/BUILD_INFO.json`
exists **and** `code_root/.git` exists (the same `os.path.lexists` test as step 3's
precondition), the result is `unidentified` with the reason
`source checkout <code_root> contains controller/BUILD_INFO.json; delete it to run from source`,
whatever the file's contents and whatever step 3's probes would answer. The rule runs before
step 2's validation and makes no Git call, so it cannot fall through to `package` when `git` is
missing from `PATH`, a probe fails to launch, or a probe answers "no" (for example `controller/`
untracked in that repository). A `site-packages` code root never has `.git`, so no real package
runtime reaches this rule. Such a code root is a checkout that also carries a
`controller/BUILD_INFO.json`. The file is gitignored, so a copied file, a `build_py --inplace` or
a hook bug could put it there. Neither kind wins. If `package` won, the checkout would run from ladder row 4 and take its
approved generation from the worktree's `controller/GENERATION.json`, so an uncommitted
generation edit would trigger exit `50`, which source mode deliberately forbids
(`handoff._read_approved_generation`). If `source` won, a file the runtime claims never to trust
would sit silently in the tree. Acting commands refuse with exit `20` (see "Materialisation by
kind"). Read-only commands still run and print the reason.

The Controller never reads `direct_url.json` or any other installer metadata. Only the running
package's own files and, for `source`, Git itself decide the kind.

**Git calls during resolution.** Step 3's two read-only probes are the only Git calls
`resolve_runtime` can make, and they run only when `code_root/.git` exists. They decide only
whether the kind is `source`; they never contribute to a recorded identity. So a `package` or
`unidentified` runtime whose `code_root` has no `.git` makes no Git call at all, from
resolution through materialisation, the unpinned identity and handoff. With no `git` on `PATH`,
a package runtime works unchanged, and a checkout resolves to `unidentified`: through the
ambiguity rule, which needs no Git, when it carries a `controller/BUILD_INFO.json`, and
otherwise through step 4 (the reason names the missing `git`). A checkout then refuses acting commands with exit `20`, where today it
would fail with a `FileNotFoundError` traceback.

`ControllerIdentity` gains:

- `runtime_kind` (`"source" | "package" | "unidentified"`);
- `version` (always `controller.version.__version__`);
- `build` (the validated BUILD_INFO dict, or `None`).

`source_kind` gains a fourth member, `"package"`, for a snapshot extracted from an installed
package. `commit`, `worktree` and `unpinned` keep their meanings.

#### The unpinned identity by kind

`identity.pin()`'s unpinned branch runs before materialisation and on every read-only command.
It also dispatches on `resolve_runtime(code_root)`, because its recorded `source_commit` reaches
`identity.json` through `cli._write_identity_record`:

- **`source`.** Unchanged: `git rev-parse HEAD` in `code_root`.
- **`package`.** `source_commit` is `build.source_commit` when `build.source_dirty is False`, and
  `None` otherwise. No Git command runs against `code_root`, because a `site-packages` code root
  has no `.git` and so never reaches `resolve_runtime`'s step-3 probes. Today's branch runs
  `git rev-parse HEAD` whenever `_is_git_repository(source_root)` succeeds
  (`controller/identity.py`, the `else` branch of `pin()`). For a wheel in `<checkout>/.venv`,
  `source_root` is `site-packages`, which Git reports as inside the checkout, so `status`,
  `inspect` and `explain` would record the *checkout's* `HEAD` as the package's commit.
- **`unidentified`.** `source_commit` is `None`, and no Git command runs beyond step 3's probes,
  which ran only if `code_root/.git` exists.

#### Materialisation by kind

`identity.materialise` dispatches on the unpinned identity's `runtime_kind`:

- **`source`.** Unchanged: `git archive` or dirty copy, `DirtyControllerSourceError` without
  `--allow-dirty-source`, and the `HEAD`-then-worktree generation read.
- **`package`.**
  1. Copy `<code_root>/controller/` into the temporary directory, as `controller/`. The copy
     skips `__pycache__/` and `*.pyc` and keeps regular files only. A symlink or special file is
     a `SourceSnapshotError`.
  2. Recompute `compute_package_digest` on the **copy** and compare it with
     `build.package_digest`. The copy is checked, not the installed tree, so a concurrent
     `pipx install --force` cannot change the bytes after the check. A mismatch is a
     `SourceSnapshotError` (`raised_by: "materialise"`) naming both digests. Causes include an
     installation modified after build, or a partial upgrade.
  3. Read the generation from the copy's `controller/GENERATION.json`, with
     `generation_source: "package"`. A missing or malformed file is a refusal, as today.
  4. Record `source_kind: "package"`. `source_commit` is `build.source_commit` when
     `build.source_dirty is False`, and `None` otherwise, mirroring `worktree`.
  5. Publish to `source/<tree_digest>/` through the existing reuse and verification path.
- **`unidentified`** (including the ambiguity case above). A `SourceSnapshotError`
  (`raised_by: "materialise"`) with the resolution reason, telling the operator to install a wheel built by this project or to run from a
  checkout. Exit `20`.

`SOURCE_PIN.json` gains `runtime_kind`, `version` and `build`. A pin without `runtime_kind` can
only have been written by an earlier Controller for its own snapshot. It reads as `source`.

#### Gating

A package whose `build.source_dirty` is not `false` (`true` or `null`) is not identified by a
commit. `materialise` refuses it with `DirtyControllerSourceError` unless `--allow-dirty-source`
is passed, which is exactly the rule a dirty checkout already follows. The message names the
cause: "built from uncommitted changes" or "built without verifiable source provenance". Any
wheel built from a clean commit, local or released, runs without the flag.

#### Runtime-root ladder

`resolve_runtime_root` gains `runtime_kind`. Row 3 (`<origin checkout>/.controller`) applies
only to `source`. A `package` runtime without `--runtime-dir` or `WORKFLOW_CONTROLLER_HOME`
resolves to row 4 (`$XDG_STATE_HOME/workflow-controller` or `~/.local/state/workflow-controller`).
So does an `unidentified` one, whose read-only commands still work.
This fixes the venv-inside-checkout misfire. For an ordinary pipx install, the chosen root is the
same as today.

#### Generation handoff by kind

`handoff.detect(identity, origin_root)` dispatches on `identity.runtime_kind`:

- **`source`.** Unchanged: the approved generation is `HEAD:controller/GENERATION.json` in the
  origin checkout.
- **`package`.** The approved generation is the *currently installed* package's
  `<origin_root>/controller/GENERATION.json`. `origin_root` is the site-packages directory the
  snapshot was extracted from, recorded in the pin as `origin_source_root`. Installing a package
  is this runtime's approval act, so a `pipx install --force` of a newer generation mid-run stops
  the run at the next orchestration boundary with exit `50`, as a newer committed generation does
  for a source runtime. The comparison has the same three outcomes as today: newer means a
  handoff, equal means continue, and older raises `GenerationHandoffPendingError`.
  - A missing or unreadable installed package, for example after an uninstall, fails closed with
    `SourceSnapshotError` (`raised_by: "detect"`), as an unreadable `HEAD` does today.
  - `handoff.json`'s `running`/`approved` blocks gain an additive `version` field. `commit` is
    the build's `source_commit`.

The source checkout is never consulted by a `package` runtime. That is what makes a package
runtime's behaviour and reported identity independent of the checkout it was built from (R4).

#### Where the identity is recorded

One helper, `identity.runtime_record(ident)`, produces the `controller_runtime` block:

```json
{
  "runtime_kind": "package",
  "version": "1.1.0",
  "source_kind": "package",
  "source_commit": "<40-hex>|null",
  "tree_digest": "<snapshot digest>",
  "package_digest": "<64-hex>|null",
  "build_origin": "release|local|null",
  "release_tag": "v1.1.0|null",
  "generation": 1
}
```

It is written into:

- `SOURCE_PIN.json` (the `build` dict is carried whole there);
- `identity.json`;
- every job record, through `job._identity_block`, beside the existing
  `controller_generation`/`controller_source_commit`/`controller_source_tree_digest`. Those stay
  for compatibility.
- `handoff.json` (`version` only).

`validate_record` reads none of the new fields, and a record without them stays valid.

#### `status` and diagnostics

`status` gains a first line describing the **running** process. It is the same text as
`--version` line 2, prefixed with the version:

```
controller: workflow-controller 1.1.0 -- package (release v1.1.0; built from 0123456789ab; package 3f2a1c9d0b7e)
```

The existing lines follow unchanged (`pinned identity` from the prior `identity.json`, `jobs`,
`handoff`, `runtime root`). The active runs/jobs section is described in CP6. `explain`/`inspect`
`--json` output gains a `controller` object with the same block.

### Streaming worker output

#### Launch

`worker.launch` gains two required keyword arguments, `stdout_path` and `stderr_path`. These are
files the caller has already created, empty, inside the runtime root. The argv becomes:

```
claude -p <task> --output-format stream-json --verbose --permission-mode <mode>
       [--model M] [--effort E] [--disallowedTools a,b]
```

The flags are added in that order, with the disallow list still last. **The worker writes its
own stdout and stderr straight into those files.** `launch` opens each with
`O_WRONLY | O_APPEND` and passes the descriptors to `Popen` as `stdout=`/`stderr=`, then closes
its own copies after spawn. There is no pipe, so:

- the durable log is the tee, by construction. A follower reads the same bytes the Controller
  later parses;
- nothing the Controller or a follower does can apply back-pressure to the worker or signal it.
  An orphaned worker keeps writing after a Controller Ctrl-C, where today it would hit `EPIPE`;
- `communicate(timeout)` is replaced by the wait described next. A timeout in phase 1 keeps
  today's path (`_kill_process_group`, then reap the direct child). Phase 2 has its own kill,
  described below, because today's helper cannot work once the leader is reaped.

**When the worker has returned control.** Today, `Popen.communicate()` returns only after EOF on
both pipes, which means after *every* holder of the worker's stdout and stderr has closed them.
Only then does it reap the child. `DEFAULT_WORKER_TIMEOUT`'s comment in `controller/job.py`
("Time is not termination") names that moment as the worker returning control. With files instead
of pipes there is no EOF, and a bare `wait()` would return as soon as the direct child exits.
Verification would then run while a descendant still touches the target. So `launch` waits in two
phases, under one `--timeout` budget:

1. `proc.wait()` for the direct child;
2. a **group drain**: poll every 0.2 s until no live process remains in the worker's process group
   (`pgid == proc.pid`). This uses the same `/proc` readers as `assess_worker_liveness`, and a
   zombie counts as gone.

**Ending the group in phase 2.** Phase 1's `proc.wait()` has already reaped the direct child,
so `os.getpgid(proc.pid)` raises `ProcessLookupError`, and today's `_kill_process_group` would
return silently without signalling anything. Phase 2 therefore never calls
`_kill_process_group` or `os.getpgid`. It ends the group with a new helper,
`_kill_drained_group(pgid)`, which `launch` calls with `pgid=proc.pid`. It calls
`os.killpg(pgid, SIGKILL)` directly and tolerates `ProcessLookupError` (the group already
empty). The group id is `proc.pid` by
`start_new_session=True`, and Linux never reuses a pid that still names a process group, as
`_proc_scan`'s own comment notes, so the signal cannot reach an unrelated group. The remaining
members are not the Controller's children, so it cannot reap them. After the kill, `launch`
polls the same group scan every 0.05 s, for at most `_DRAIN_KILL_SETTLE_SECONDS` (2 s, a module
constant), until it reports the group empty. If the group is still reported non-empty after that
bound (for example under the `killpg` form, which cannot tell a zombie from a running process,
or under the `/proc` form while a member is still in uninterruptible sleep), `launch` does not
wait further. The phase-2 kill is used on both phase-2 paths: the `--timeout`
budget expiring, and an `on_group_drain` exception.

**What "empty" means under each process-test form.** The drain uses `worker.process_test`:
- **`/proc` form:** `not live` ends the drain, `live` keeps waiting. The `member_scan` answer
  names the members.
- **`killpg` form** (`_proc_numbers_own_namespace` is false, or `/proc` has no answer): only
  `not live` (`killpg_no_such_group`) ends the drain. `possibly live` keeps waiting, bounded only
  by `--timeout`. That is the conservative reading and matches `assess_worker_liveness`, which
  never treats `possibly live` as gone. A zombie member that its new parent has not yet reaped
  keeps this form waiting until it is reaped.

**Classification of a phase-2 timeout.** `launch` sets a local `timed_out` flag whenever it
kills on the budget, in either phase, and passes it to `_classify(returncode, parsed, *,
timed_out)`. `_classify` checks `timed_out` first and returns `INTERRUPTED`; the rest of the
order is unchanged. `WorkerResult.returncode`, and so the record's `worker.exit_code`, hold the
direct child's real exit code. So `INTERRUPTED` with `exit_code: 0` is a documented, valid
combination: the worker exited cleanly, but its process group outlived the budget. In phase 1,
`returncode` is negative after the kill, so the result is `INTERRUPTED` either way, as today.
An `on_group_drain` exception does not return a `WorkerResult`: the group is killed as above and
the exception propagates, exactly like `on_spawn`'s.

The new semantics, stated exactly: control returns when the direct child has exited **and** its
process group is empty.
- A descendant that stays in the group keeps the Controller waiting, whether or not it holds
  stdout. That is at least as strict as today.
- A same-group descendant that does **not** hold the worker's stdout or stderr is the case where
  this is stricter. An example is a background process the worker's tools left behind. Today
  `communicate()` returns when the worker exits and the job completes. If that descendant
  inherited the lifecycle-lock descriptor, the next `step`/`run` then exits `45`. After this
  plan, `launch` does not return until that process exits. `DEFAULT_WORKER_TIMEOUT` is `None`
  (`controller/job.py`), so by default that wait is unbounded. With `--timeout`, the budget
  expires, the whole group gets `SIGKILL` through `_kill_drained_group`, and the job ends
  `INTERRUPTED`. Either
  way no new exit code is involved. The wait itself is kept, consistent with "Time is not
  termination" and with `assess_worker_liveness`'s `member_scan` verdict, which already treats
  such a process as the worker still being active. It is made visible instead (below).
- A descendant that left the group (`setsid`) is no longer waited for, even if it still holds the
  log files. Today it was waited for only if it held a pipe end. This is the one narrowing, and it
  is accepted: a process that detaches itself from the worker's session is outside the worker
  model the lifecycle lock and the liveness verdicts already use.
- The resulting exit codes: the job verifies and ends as it would today. If that detached
  descendant also inherited the lifecycle-lock descriptor, the next `step`/`run` finds the lock
  held and exits `45` (`LifecycleWorkerActiveError`), where today the Controller would have kept
  waiting. `resume` reports it through the existing exit-45 path. No new exit code is involved.

**The drain is visible.** `launch` gains an optional `on_group_drain(pid, remaining_pids)`
callback. It is called once, when phase 1 ends and the group still has live members. It has
`on_spawn`'s contract: an exception from it ends the whole group (the phase-2 kill above, then
the bounded empty-group wait) and propagates. `remaining_pids` is the `member_scan` answer's
member list under the `/proc` form. Under the `killpg` form there is no member list, so it is
the empty list, and the record and the drain line say `unknown` for the count. The job's callback:
1. persists the record with an additive
   `"worker_group_drain": {"direct_child_exited_at": "<UTC>", "remaining_pids": [...]}`
   (authoritative, like the `on_spawn` flush; from CP5 this `_persist` names the event
   `worker_exited`, whose append is best-effort like every event);
2. writes one stderr line,
   `worker pid P exited; waiting for N process(es) still in its process group: <pids>`
   (`waiting for an unknown number of processes still in its process group` under the `killpg`
   form). An
   `OSError` writing this line is ignored, so a closed stderr cannot kill the group.

The heartbeat and `status`'s `active:` section read that record field. While a `LAUNCHED` job
carries it, they say `worker exited; waiting on process group: N process(es) at drain start
(<pids>)` instead of `worker running: pid P`. The list is captured once, when the drain starts,
and is not rescanned, because a follower may run in another pid namespace and must stay
read-only, so the wording labels it "at drain start". `validate_record` and reconciliation do not
read the field.

After the worker exits, `launch` reads both files (UTF-8, `errors="replace"`, exactly today's
decoding) into `WorkerResult.stdout`/`.stderr`.

#### Parsing and classification

`_parse_worker_stdout` is replaced by `_parse_worker_stream(stdout)`, which is strict:

- the text is split on `\n`, with one trailing `\r` stripped per line. Only a single final empty
  segment is ignored;
- every other line must parse, by itself, as one JSON **object**. An empty line, a non-JSON line
  or a non-object line makes the stream undecidable;
- exactly one event must have `"type": "result"`, and it must be the last line;
- the result event is returned. Anything else returns `None`.

`_classify` keeps today's order: `INTERRUPTED`, then `FAILURE`, `AMBIGUOUS`, `SUCCESS`. Its only
change is the `timed_out` flag, checked first, as "Launch" describes. So an
undecidable stream is `AMBIGUOUS`, exactly as a malformed single JSON body is today. The result
event carries the same `session_id`, `is_error`, `subtype`, `result`, `num_turns`,
`total_cost_usd`, `duration_ms`, `permission_denials`, `stop_reason` and `terminal_reason` fields
as `--output-format json`. `_REQUIRED_FIELDS`/`_OPTIONAL_FIELDS`, `WorkerResult` and
`_worker_dict` are unchanged, and `raw_json` is the result event.

#### Job execution

In `_execute_step_locked`, after the `PLANNED` flush:

1. create `jobs/<job_id>/worker.stdout` and `worker.stderr` through a new
   `runtime.create_log_file(runtime_root, rel)`. It is containment-checked, uses
   `O_CREAT | O_EXCL | O_WRONLY` with mode `0o600`, and returns the path. An `OSError` from
   creation is wrapped in the existing `WorkerLaunchError`, a `ControllerError` whose `message`
   and `evidence` the `WorkerNotStarted` record reads. A containment failure is already a
   `RuntimeContainmentError`, which is also a `ControllerError`. Either way it is handled exactly
   like today's launch failure: the record goes terminal `FAILED` with `WorkerNotStarted`, and
   `main()`'s `ControllerError` handler exits `20`. A raw `OSError` would bypass that handler and
   exit `1` with a traceback. No worker exists.
2. the `LAUNCHED` flush gains
   `"worker_streams": {"format": "stream-json", "stdout_path": ..., "stderr_path": ..., "events_path": ...}`,
   so a follower can find the logs before the worker produces output.
3. `launch(..., stdout_path=..., stderr_path=...)`.
4. step 6 no longer writes the streams. `_write_worker_streams` is deleted, and
   `worker.stdout_path`/`stderr_path` in the `COMPLETED` record name the same files.

#### What streaming does not change

- `resume`'s reconciliation table, `validate_record`, the liveness verdicts, `--abandon` and every
  `ExpectedOutcome`/postcondition read exactly what they read today. None of them reads worker
  stdout.
- `reconciliation_evidence.worker_stdout` for `INCOMPLETE` stays `result.stdout`, now the stream
  text. It is unreachable for every Generation 1 row, as documented at `_INCOMPLETE_EFFECT_PHASES`.
- The apply relaunch bound's view (`last_launched_apply_job_view`) reads record fields only.
- Worker routing, permission mode, task text, `pass_fds` (lifecycle lock) and `on_spawn` are
  unchanged.

### Durable Controller lifecycle events

Two append-only logs are added. Both are written whether or not anyone follows. Neither is read
by any lifecycle decision.

- **Job events: `jobs/<job_id>/events.jsonl`.** `job._persist` gains an `event` argument, and
  every call site names one:
  - `planned`, `launched`, `worker_spawned` (pid/pgid), `worker_exited` (pid and the group
    members still being drained, only when the drain is non-empty), `completed` (outcome, exit
    code) and
    `finished`/`failed`/`incomplete` (with `observed_phase_after` and `transition_verified`);
  - `gate_blocked`/`declined`/`handoff_pending` (with the gate or decline reason);
  - `worker_not_started`;
  - the `resume` reconciliations (`reconciled` with the new status and code);
  - `--abandon` (`abandoned`).

  Each line has the form
  `{"v": 1, "seq": n, "at": "<UTC>", "job_id": ..., "event": ..., ...details}`.

  **Where `seq` comes from.** The job record gains an additive integer field, `event_seq`. Each
  `_persist(..., event=...)` increments it on the record it is about to write, writes the record
  (the authoritative, fsynced write), and then appends the event carrying that value as `seq`.
  `resume` and `--abandon` already read the job record they reconcile, so they continue the
  sequence across processes without reading `jobs/<id>/events.jsonl`. The log is never read by
  any writer. A lost best-effort append shows up as a gap in `seq`, never as a repeat. A record
  without `event_seq` (from before this milestone) starts at `1`. `validate_record` does not read
  the field.
- **Run records: `runs/<run_id>.json` and `runs/<run_id>/events.jsonl`.** `step` and `run`,
  which are the commands that can launch workers, create one run record at entry. The record
  holds:
  - `schema_version`, `run_id`, `command`, `target_repo` and `max_steps`:
    - `command` is the subcommand name only (`"step"` or `"run"`), never argv. Argv would carry
      `--follow` and break guarantee 3 of "Observation is presentation-only";
    - `target_repo` is the same canonical root job records use (`managed_repo.root`), so
      `follow`'s target check compares like with like. A `<repo>` given as a subdirectory or
      through a symlink resolves to the same value;
  - `controller_process`: `worker.capture_worker_process(os.getpid())`, reused as-is;
  - `controller_runtime`;
  - `state` (`running`, then `ended` or `interrupted`), `exit_code`, `job_ids` and
    `current_job_id`;
  - `started_at`/`updated_at`/`ended_at`.

  Run-log `seq` is a per-run counter held by the one process that owns the run. No other process
  appends to a run log.

  The events log records `run_started`, `step_started` (n), `job_started` (job_id),
  `job_ended` (job_id, status), `handoff_detected`, `no_action` (decision summary) and
  `run_ended` (exit code). `execute_step` gains an optional `run_id`. It is recorded as an
  additive `run_id` field on the job record, and job-level `planned`/final events are mirrored
  into the run log, so a run follower learns the new job id while that job runs.

  **One closing site.** The exit codes `20` and `45` are assigned in `cli.main()`'s `except`
  clauses, outside `cmd_step`/`cmd_run`, so a `finally` inside those functions has no final code
  to record. Instead:
  - `cmd_step`/`cmd_run` register the run they create in a module-level slot in `cli`
    (`_open_run`) and never close it themselves;
  - `main()` computes the final code once (the `_dispatch` return value or the mapped
    `except` clause, unchanged), and then, in one `finally`, closes any registered run with that
    code: `state: "ended"`, `exit_code`, `ended_at`, and a `run_ended` event;
  - on `KeyboardInterrupt`, which `main()` does not map today, the same site records
    `state: "interrupted"`, `exit_code: null`, `current_job_id` kept, and a `run_interrupted`
    event. The interrupt then propagates exactly as today;
  - closing is best-effort, like every run-record write.

  A Controller killed with `SIGKILL` leaves `state: running`, which the follower's liveness check
  handles (CP6). An interrupted run whose worker is still `active` is handed over to the job
  follower (see `follow_run`).
- **What the records deliberately omit.** Whether the run is being followed. Recording it would
  make followed and unfollowed durable state differ.

**The `worker_spawned` event must never raise.** `worker.launch` treats an exception from
`on_spawn` as fatal: it kills and reaps the worker group. The event is appended inside
`on_spawn`, so it goes through the best-effort wrapper below. A test forces that append to fail
and asserts the worker still runs to completion.

**Write semantics.** Event appends go through a new
`runtime.append_jsonl(runtime_root, rel, obj)`. It is containment-checked and uses
`O_APPEND | O_CREAT` with mode `0o600`, one `write()` per line and no `fsync`. The run record
uses `runtime.write_json` (atomic). Both are best-effort. The wrapper catches `Exception`, not
only `OSError`: `append_jsonl` is containment-checked, and `RuntimeContainmentError` is a
`ControllerError`, not an `OSError`; serialising event details can raise `TypeError` on a
non-JSON value. It never catches `BaseException`, so `KeyboardInterrupt` and `SystemExit` still
propagate. Any caught failure is reported once per process on stderr
(`workflow-controller: warning: could not write <path>: ...`) and never raises into the
lifecycle. The job record (`jobs/<job_id>.json`, atomic and fsynced as today) stays the
only authority. The two paths that are not best-effort are worker-stream file creation (above)
and the job record itself. Both already stop the lifecycle on failure.

`resume` and `pending_reconciliation_jobs` scan `jobs/*.json` non-recursively, so neither ever
sees `runs/` or `jobs/<id>/`. `_classify_jobs` (handoff) is unchanged. A test pins that none of
them, and neither does `abandon`, reads anything under `runs/` or `jobs/<id>/`. With `seq` taken
from the job record, that holds with no exception.

### Observation surface

#### `controller/observe.py`

This is a new module, late in the dependency order: it depends on `runtime`, `worker` (liveness)
and `lock` (probe), and nothing depends on it except `cli`. It provides:

- **`Tail(path)`.** An offset-based reader that polls every 0.2 s and yields only complete
  lines, holding back a trailing partial line. A missing file yields nothing until it appears.
  It never opens anything for writing.
- **`normalise(source, line)`.** Maps a raw line to a presentation event (see the table below).
  It never raises. An unparseable or unknown line becomes `{"kind": "raw"}` or
  `{"kind": "unknown", "type": ...}`.
- **`render_text(event)`/`render_json(event)`.** Text rendering is one line or a short block,
  prefixed with local time and a source column. JSON rendering is one normalised object per line.
- **`follow_run(runtime_root, run_id, sink, *, from_start, stop)`** and
  **`follow_job(runtime_root, job_id, sink, ...)`.** These multiplex:
  - the run's events;
  - each job's `events.jsonl`;
  - the job's `worker.stdout`, and `worker.stderr` rendered as `stderr` lines.

  `follow_run` ends when the run record reads `ended` and every log is drained. It also ends when
  the recorded Controller process is gone, after printing
  `controller process is gone; run record was not closed`. The Controller check is pid-level:
  the recorded pid must still exist with the recorded `start_ticks`, in the same boot and pid
  namespace, and must not be a zombie. It uses the same `/proc` readers as
  `worker.assess_worker_liveness`, not that function's process-group verdict. The Controller's
  own process group can contain unrelated pipeline members, such as `| tee`, which would keep a
  group-level check `active` after the Controller died. An `unverifiable` Controller (another
  host or pid namespace) keeps following, with a one-time warning, until the record ends or the
  follower is interrupted.

  A run whose record reads `interrupted` is drained first. If its `current_job_id`'s worker is
  still `active` (`assess_worker_liveness`, read-only), `follow_run` prints
  `run <run_id> was interrupted; worker for job <job_id> is still running; following the job`
  and continues as `follow_job` for that job. Otherwise it ends, printing
  `run <run_id> was interrupted`.

  `follow_job` ends, after draining every log, when either the job record is terminal or the
  record is non-terminal and its worker is no longer `active`. In the second case it prints
  `worker exited; job <job_id> awaits resume`. An `unverifiable` worker keeps it following, with
  a one-time warning.
- **Heartbeat.** After 30 s without a new event while the job is `LAUNCHED`, it prints
  `worker running: pid P, elapsed M:SS, last event Ns ago`. Once the record carries
  `worker_group_drain`, it prints
  `worker exited; waiting on process group: N process(es) at drain start (<pids>), elapsed M:SS`
  instead. The
  interval is a module constant that tests override.

| Source line | Presentation |
|---|---|
| stream `system`/`init` | `worker session <id> model <m> permission <mode>` |
| stream `assistant` text block | the text, verbatim, wrapped |
| stream `assistant` `tool_use` | `tool <Name>: <summary>`. The summary is `command` for Bash, `file_path` for Read/Edit/Write, `pattern` for Grep/Glob, otherwise compact JSON truncated to 200 chars |
| stream `user` `tool_result` | `result <Name> ok/error: <first 20 lines>` plus `... (k more lines)` |
| stream `assistant` `thinking`/`redacted_thinking` block | **omitted**: never rendered, not even as a marker |
| stream `result` | `worker result: <subtype>, is_error, turns, cost, duration` |
| stderr line | `stderr: <line>` |
| job/run events | `job <id> LAUNCHED (/cmd, role, model/effort)`, `job <id> FINISHED (A -> B verified)`, `gate: ...`, `run ended: exit N`, and so on |
| legacy `worker.stdout` (a single JSON object, from a record older than this milestone) | rendered as its `worker result` line, so old jobs replay |

Hidden chain-of-thought is never requested: the Controller passes no thinking-related flag. The
renderer also drops any thinking block the CLI emits. The raw logs stay verbatim, as evidence
(see Scope judgments).

#### `follow` command

```
workflow-controller [--runtime-dir D] [--json] follow [--job JOB_ID | --run RUN_ID] [--from-start] [<repo>]
```

`<repo>` defaults to `.`, matching the roadmap's `workflow-controller follow` usage. It is
resolved to the same canonical root as a job record's `target_repo`.

- **Selection, when neither id is given:**
  1. the newest run record for `<repo>` whose `state` is `running` and whose Controller is not
     `inactive`;
  2. otherwise, a non-terminal job record for `<repo>` whose recorded worker is `active` (an
     orphan after Ctrl-C);
  3. otherwise, it prints
     `nothing active for <repo>; last run <run_id> ended with exit <n>; replay: workflow-controller --runtime-dir D follow --run <run_id> --from-start <repo>`
     (or `no runs recorded`) and exits `0`.
- **With an explicit id,** it follows that run or job, live or finished. The record's
  `target_repo` must equal the resolved `<repo>`, otherwise it is refused.
- **Output.** By default, it replays the last 20 presentation events of the current job before
  going live. `--from-start` replays everything. `--json` emits normalised events.
- **Exit codes** (no new code):
  - `0` when the followed run or job ends, or there is nothing to follow;
  - `20` for an unknown id, a target mismatch or an unreadable record. This is a `ControllerError`
    through the existing handler;
  - `2` for usage errors.

  `follow` does not mirror the followed run's exit code. It prints that code in its final line.
- **Runtime root.** Without `--runtime-dir`, `follow` must find the root `step` used. It resolves
  the runtime kind read-only through `identity.resolve_runtime(code_root)` (no write, no Git call
  except step 3's read-only probes) and passes it to `resolve_runtime_root`, so a source checkout
  lands on row 3 exactly as `step` does, and a package runtime on row 4.
- **Zero writes.** `follow` is dispatched before runtime-root creation, pinning, materialisation
  and the `identity.json` write, so it never re-execs. If the runtime root does not exist, that
  is "nothing to follow". It takes no lock, including no lifecycle lock. The lifecycle-lock probe
  it may use is `lock.probe_lifecycle_lock`, which is `/proc/locks`-based and never acquires.
  Its only file operations are reads.

#### `step --follow` / `run --follow`

`--follow` is a flag on the `step` and `run` subparsers only. `resume` never launches a worker,
so it has nothing to follow. When `resume` exits `45` because a worker is active, it prints the
`follow` command instead.

The flag is read in exactly one place, `cli._start_follower(args, runtime_root, run_id)`. That
function starts a daemon thread running `observe.follow_run(..., sink=<fd sink>)` after the run
record exists. The lifecycle code path is byte-for-byte the same function calls with the same
arguments. The thread reads the same durable logs any other follower reads.

- **Output goes to stderr,** so `--json` stdout stays machine-readable.
- **The renderer never touches `sys.stderr`.** If a daemon thread blocks inside
  `sys.stderr`'s `BufferedWriter.write`, for example because stderr is a pipe whose reader has
  stalled, it holds that writer's lock. A timed-out join then leaves the interpreter to finalise
  with the lock held, and CPython aborts with `Fatal Python error: _enter_buffered_busy`
  (`SIGABRT`). That changes the exit status. So the sink is a private descriptor,
  `os.dup(2)`, written with `os.write`. No Python I/O object or lock is involved, and a thread
  blocked in the `write` syscall does not hold the GIL, so it cannot stall finalisation.
  - Each write is at most `select.PIPE_BUF` bytes and is preceded by a `poll(POLLOUT)` wait of at
    most 1 s. A wait that expires disables rendering for the rest of the process.
  - When the descriptor is a FIFO, the renderer also keeps a reserve for the Controller's own
    stderr messages. Before each write, it reads the pipe's capacity (`F_GETPIPE_SZ`) and the
    unread byte count (`FIONREAD`). It disables itself if the write would leave less than
    16 KiB free. So a stalled reader can never cause the Controller's own `error:` or orphan
    lines to block where an unfollowed run would not.
  - Stalled rendering is not reported (the note would block too). A renderer that fails for any
    other reason makes one attempt to write a note through the same descriptor.
- **On command completion,** the thread is told to drain up to EOF of every log. It gets a bounded
  2 s join. A join that times out is abandoned, and `main()` returns the lifecycle's exit code
  normally.
- **Renderer failures stay in the thread.** `BrokenPipeError`, `OSError` and any other exception
  disable rendering for the rest of the process. The thread attempts one stderr note if stderr
  still works. The exception is never propagated, and the exit code never changes.
- **Ctrl-C** behaves as today: the Controller is interrupted, and the worker keeps running
  headless. The orphan message gains the `follow` command. The follower thread dies with the
  process.

#### Discovery and hints

- `status` adds an `active:` section listing, across every target in this runtime root:
  - each `running` run record with its Controller liveness;
  - each non-terminal job with its worker liveness (`assess_worker_liveness`, read-only). A job
    whose record carries `worker_group_drain` reads
    `worker exited; waiting on process group: N process(es) at drain start (<pids>)`;
  - the exact
    `workflow-controller --runtime-dir <root> follow <repo>` line for each.

  With nothing active it prints `active: none`.
- The `LifecycleWorkerActiveError` message (exit 45), `cmd_resume`'s exit-45 output and
  `_announce_orphaned_worker` each gain the same `follow` line. These are message text changes
  only.
- `_announce_orphaned_worker` also becomes drain-aware. A Ctrl-C during the group drain reaches
  `execute_step`'s existing `KeyboardInterrupt` handler after pid P has already exited, so
  today's "the worker (pid P, process group G) keeps running" would be wrong. The job's
  `on_group_drain` callback sets `spawned["drained"] = True` after its record flush, and the
  handler passes that flag. When it is set, the line reads
  `interrupted -- worker pid P exited; its process group G still has members running in their own
  session and holding the worktree <root>; wait for them, or end the group (kill -TERM -- -G),
  then run <resume command>`, followed by the same `follow` line. The advice,
  the exit path and the record are unchanged; only the wording differs.

### Observation is presentation-only

This property is enforced structurally, not only by convention:

1. **Nothing observational feeds a lifecycle input.** The only readers of the new logs and run
   records are `observe.py` and `status`. `job`, `evidence`, `decision`, `routing`, `worker` and
   `handoff` never import `observe`. The existing dependency-order test enforces this once
   `observe` is placed late in the order.
2. **`--follow` is read in one function.** An AST test asserts that the attribute `follow` is read
   only in `cli._start_follower`. `_routing_options`, `execute_step`'s arguments and the run
   record builder never see it.
3. **Always-on logging.** The event logs, run records and worker-stream files are written
   identically with or without a follower. A follower's presence is recorded nowhere.
4. **Followers are separate readers.** The `follow` command writes nothing, holds no lock and
   sends no signal. The in-process renderer is a daemon thread whose failures are contained.
5. **Equivalence test (CP7).** The same scripted lifecycle runs three ways:
   - unfollowed;
   - with `--follow`;
   - unfollowed, with an external `follow` attached mid-job and killed with `SIGKILL` mid-job.

   The test then compares job records, run records, target Workflow state and fake-worker argv and
   environment diagnostics. The comparison is normalised for ids, timestamps and pids, and they
   must be equal. Exit codes must be equal.

### CI and release

#### Workflow files are rendered from a model

Tests are stdlib-only, so they cannot parse YAML. `tools/ci_workflows.py` therefore holds the
three workflows as Python data (dicts and lists) plus a small deterministic emitter:

- mappings are emitted in insertion order;
- lists become block sequences;
- multi-line strings become `|` literal blocks;
- a string that is a plain identifier, a version or an expression is emitted bare only when it is
  YAML-safe by a conservative regex. Any other string is JSON-quoted, which is valid YAML
  double-quoted style.

`python3 tools/ci_workflows.py --write` writes `.github/workflows/{validate,ci,release}.yml`, and
`--check` exits non-zero if any committed file differs from the rendered model. Tests assert on
the model and assert that the committed bytes equal the render. The committed YAML is what
GitHub runs, and it is guaranteed to be the tested model.

#### `validate.yml` (`on: workflow_call` only, the single definition of "required validation")

| Job | Matrix | Steps |
|---|---|---|
| `controller` | `shard`: named shards, `fail-fast: false` | checkout; setup-python 3.12; `pip install -e .`; `python -m unittest <modules of shard> -v` |
| `conformance` | `suite`: the seven frozen suites, `fail-fast: false` | checkout; setup-python 3.12; `python3 <suite>` in `scripts/` |
| `package` | none | checkout (`fetch-depth: 0`); setup-python 3.12; `pip install "setuptools>=70.1"`; `python -m pip wheel --no-deps -w dist .`; `python3 tools/release.py verify-wheel dist/*.whl --local --commit "$(git rev-parse "$GITHUB_SHA^{commit}")"`; `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python -m unittest tests.test_packaged_runtime -v`; `pipx install dist/*.whl` then `workflow-controller --version`, with line 1 compared with `tools/release.py version` |

The `package` job peels `$GITHUB_SHA` to a commit exactly as `release.yml`'s `build` does. The
peel is a no-op for the commit a `pull_request` or `push: main` run carries. `release.yml` calls
this workflow as its gating `validate` job, so on an annotated-tag push an unpeeled
`$GITHUB_SHA` would make `validate` compare a tag object with the wheel's `source_commit` and
fail every such release before `build` runs. The two workflows therefore never disagree about
which commit is being released.

The `package` job pins `setuptools>=70.1`, the `--no-build-isolation` prerequisite that
`tests.fixtures.wheel_build_prerequisite()` checks, so the required run never depends on which
setuptools the resolver picks.

All jobs use `runs-on: ubuntu-latest` and `permissions: contents: read`. None uses a cache
action, a shared artifact or any secret. Each job is a fresh runner with its own checkout.

The Controller shards (the membership is illustrative; CP9 fixes it and the test enforces
coverage):

| Shard | Modules |
|---|---|
| `identity` | `test_identity test_runtime test_package_structure test_write_containment test_lock test_handoff` |
| `job` | `test_job test_job_validation` |
| `resume` | `test_resume` |
| `decision` | `test_evidence test_decision test_golden_plan_stage_decisions test_target_state test_managed_repo test_routing` |
| `cli` | `test_cli test_lifecycle_orchestration` |
| `worker` | `test_worker test_observe test_observation_equivalence` |
| `docs` | `test_plan_document_consistency test_checklist_corrections test_ci_workflows test_release_tools test_buildinfo` |

`tests.test_packaged_runtime` runs in `package`. `tests.test_integration_disposable_repo` is
excluded by name, with its reason in the model: it needs the live `claude` binary and real spend.
The coverage test discovers `tests/test_*.py` and requires that every module appears in exactly
one shard, in `package`, or in the named exclusion.

#### `ci.yml`

- **Triggers:** `push: branches: [main]` and `pull_request`, as today. A `branches` filter without
  a `tags` filter means tag pushes do not trigger it. Pushes to other branches are validated
  through their pull request, which avoids a duplicate push-plus-PR run per commit.
- **Concurrency:** `concurrency: {group: "${{ github.workflow }}-${{ github.ref }}", cancel-in-progress: true}`.
- **Job:** a single `validate` job, `uses: ./.github/workflows/validate.yml`.

#### `release.yml`

- **Trigger:** `push: tags: ["v*"]`.
- **Concurrency:** `concurrency: {group: "release-${{ github.ref }}", cancel-in-progress: false}`.
  A second run for the same tag queues rather than cancelling, and another tag's run is a
  different group, so it is never cancelled.
- **Permissions:** top-level `contents: read`.
- **Jobs:**
  1. **`validate`** is `uses: ./.github/workflows/validate.yml`. It is the same required
     validation as CI, with no duplicated definition.
  2. **`build`** (`needs: [validate]`):
     - checkout (`fetch-depth: 0`) and setup-python 3.12;
     - `python3 tools/release.py verify-tag "$GITHUB_REF_NAME"`;
     - resolve the release commit once, in a step with `id: peel`:
       `RELEASE_COMMIT=$(git rev-parse "$GITHUB_SHA^{commit}")`, which the step's `run:` writes both
       to `"$GITHUB_ENV"` (for `build`'s later steps) and as `release_commit=$RELEASE_COMMIT` to
       `"$GITHUB_OUTPUT"`. The job output is declared as
       `outputs: {release_commit: "${{ steps.peel.outputs.release_commit }}"}`, the documented
       way to export a step value; a `$GITHUB_ENV` value is never mapped into a job output. Peeling is a
       no-op when `$GITHUB_SHA` is already a commit, and turns a tag object into its commit
       otherwise, so an annotated tag push can never compare a tag object with
       `git rev-parse HEAD` or the peeled `ls-remote` line and burn a PATCH version on a
       systematic mismatch. CP9 also records, from GitHub's documentation, what `$GITHUB_SHA`
       holds for an annotated tag push; the peel is kept either way;
     - `git merge-base --is-ancestor "$RELEASE_COMMIT" origin/main`, so a release commit must be
       on `main`;
     - `WORKFLOW_CONTROLLER_RELEASE_TAG="$GITHUB_REF_NAME" python -m pip wheel --no-deps -w dist .`;
     - `python3 tools/release.py verify-wheel dist/*.whl --tag "$GITHUB_REF_NAME" --commit "$RELEASE_COMMIT"`;
     - a pipx smoke test (`pipx install dist/*.whl` and the `--version` line-1 check);
     - `python3 tools/release.py checksums dist`, which writes `SHA256SUMS`;
     - `actions/upload-artifact` with `name: dist` and `path: dist/`, the directory
       `pip wheel -w` and `checksums` write to. A single-directory `path` makes that directory the
       artifact root, so the artifact holds the wheel and `SHA256SUMS` with no `dist/` prefix.
  3. **`publish`** (`needs: [validate, build]`, `permissions: contents: write`, the only job with
     write permission). Its **job-level** `env` sets
     `GH_TOKEN: ${{ github.token }}` and
     `RELEASE_COMMIT: ${{ needs.build.outputs.release_commit }}`, so every step sees both and
     no step sets either again. `gh` does not pick up the workflow token in GitHub Actions
     unless `GH_TOKEN` (or `GITHUB_TOKEN`) is in its environment, and `check-unpublished` and
     both `gh release` forms need it. Setting it once at job level makes that true for every
     `gh` call by construction. The job's actions already receive `github.token` as their
     default `token` input, so this exposes nothing new. The steps:
     - `actions/checkout` (default depth). `tools/release.py` needs the repository, and so does
       `gh release create`'s repository context;
     - `actions/download-artifact` with `name: dist` and `path: dist`. A single named artifact
       is extracted directly into `path` (which otherwise defaults to `$GITHUB_WORKSPACE`), so the
       files land at `dist/<wheel>` and `dist/SHA256SUMS`, exactly where every later step's
       `dist/` globs look;
     - `python3 tools/release.py check-unpublished "$GITHUB_REF_NAME"`;
     - re-verify the downloaded wheel:
       `python3 tools/release.py verify-wheel dist/*.whl --tag "$GITHUB_REF_NAME" --commit "$RELEASE_COMMIT"`;
     - `python3 tools/release.py verify-tag-commit "$GITHUB_REF_NAME" "$RELEASE_COMMIT"`,
       immediately before the release is created (see "Immutability and duplicates");
     - `gh release create "$GITHUB_REF_NAME" dist/*.whl dist/SHA256SUMS --verify-tag --title ... --notes ...`.

The pipeline never uses `--clobber`, `gh release upload` or `gh release delete`. Under GitHub's
immutable releases, assets must be attached before publication. `gh release create` with asset
arguments is expected to create a draft, upload the assets and then publish. CP9 must confirm
this against the runner's `gh` version documentation and record the source. If it cannot be
confirmed, `publish` uses the explicit form instead:
`gh release create --draft ...assets`, then `verify-tag-commit` again, then
`gh release edit "$GITHUB_REF_NAME" --draft=false`. That form is scoped by a test to the draft the
same job just created. In either form, `gh release edit` is never applied to a published
release.

**Immutability and duplicates.**
- `check-unpublished` refuses when `gh release view <tag>` succeeds. It also refuses when that
  call fails for any reason other than "release not found", since an undecidable answer fails
  closed.
- A race between two runs for the same tag is closed by `gh release create` itself, which fails
  when a release for the tag already exists.
- A moved (force-pushed) tag re-triggers the workflow. That run hits the same refusal, because the
  release exists, so nothing is overwritten.
- A tag moved **while** its first run is still in progress is caught before publication. `build`
  builds `$RELEASE_COMMIT`, the commit the tag named when the run was triggered, but
  `gh release create --verify-tag` only checks that the tag exists and attaches the release to
  the tag's commit at publish time. Without a check, the first run would publish wheel A under a
  tag that now names commit B, and immutable releases would make that permanent. So `publish`
  runs `verify-tag-commit` immediately before `gh release create` and refuses unless the tag's
  current commit still equals `$RELEASE_COMMIT`. When the first run refuses, the second run, queued
  behind it for the moved tag, builds and publishes the tag's new commit normally. A residual window of seconds
  remains between that check and `gh release create`. Once the release is published, immutable
  releases close it. The README states this window.
- The README release procedure also tells the maintainer to enable GitHub's repository-level
  "immutable releases" setting. That setting makes a published release's assets and tag
  unchangeable, even by an admin, which is platform-level enforcement that a workflow cannot set
  for itself.

No job in any workflow sets `CONTROLLER_LIVE_WORKER` or invokes a real `claude`.

**Action pinning.** Every `uses:` in `release.yml` is pinned to a full 40-hex commit SHA, with the
tag it corresponds to in a trailing comment (`actions/checkout@<sha> # v4`). This is the file
whose `publish` job holds `contents: write`. `ci.yml` and `validate.yml` keep major tags, as the
existing workflows do. The model in `tools/ci_workflows.py` holds each action's SHA and tag in
one table, which CP9 fills in from each action's published tag.

#### `tools/release.py`

This is a stdlib-only script with `sys.path` set to the repository root, so it imports
`controller.buildinfo` and `controller.version` directly. Its subcommands:

- **`version`** prints `controller.version.__version__`.
- **`verify-tag TAG`** requires `TAG` to match `^v` + `SEMVER_RE` and equal `v<version>`. It also
  requires `pyproject.toml` (read with `tomllib`) to still declare
  `dynamic = ["version"]` with the `attr` pointing at `controller.version.__version__`, and to
  have no static `version`.
- **`verify-wheel WHEEL (--tag TAG --commit SHA | --local --commit SHA)`** opens the zip and checks:
  - the filename is `workflow_controller-<version>-py3-none-any.whl`;
  - `METADATA` `Version:` equals `<version>`;
  - `entry_points.txt` declares `workflow-controller = controller.cli:main`;
  - `controller/GENERATION.json` and `controller/BUILD_INFO.json` are present;
  - there is no `SOURCE_PIN.json`, `__pycache__` or `*.pyc`;
  - `BUILD_INFO` validates;
  - `package_digest` is recomputed from the wheel's `controller/` members, as bytes through the
    same canonical form, and equals the recorded value;
  - `source_commit == SHA`;
  - `source_dirty is False`;
  - `build_origin`/`release_tag` match the mode.
- **`check-unpublished TAG`** is described above. The `gh` runner is injectable for tests.
- **`verify-tag-commit TAG SHA`** runs
  `git ls-remote origin refs/tags/TAG refs/tags/TAG^{}`, which asks the remote directly, so no
  local tag or fetch can be stale. For an annotated tag it uses the peeled `^{}` line, and
  otherwise the direct line. It refuses with `tag moved` when that commit differs from `SHA`.
  It refuses as undecidable (fail-closed) on a non-zero exit, on no matching line, or on a
  malformed line. The Git runner is injectable for tests.
- **`checksums DIR`** writes `SHA256SUMS` in `sha256sum` format for every regular file in `DIR`
  except `SHA256SUMS` itself, which is excluded by name and overwritten. A rerun over a directory
  holding a previous `SHA256SUMS` therefore produces the same file, and `sha256sum -c` passes.

Every refusal exits `1` with one line naming the failed check.

## Scope judgments (for the reviewer to confirm or cut)

1. **Package runtimes still materialise a snapshot.** They copy the installed tree into
   `source/<digest>/` and re-exec, rather than executing `site-packages` in place. Executing in
   place would be simpler. The copy keeps the Gen-1 guarantee that a running generation never
   executes bytes that can change under it, and a `pipx install --force` mid-run would otherwise
   do exactly that.
2. **A package built from a dirty tree, or without provenance, needs `--allow-dirty-source`,**
   mirroring source mode. The alternative, always running any package, would make a job record's
   `controller_source_commit` unverifiable without saying so.
3. **`GENERATION.json` stays separate from the semver version** (see Design).
4. **`setup.py` is added for the `build_py` hook.** A `pyproject`-only `cmdclass` string would
   depend on how setuptools resolves project-local modules in isolated builds. `setup.py` is the
   stable, documented extension point.
5. **The CI YAML is rendered from a Python model.** This keeps the tests stdlib-only. The
   alternatives were JSON-flavoured YAML, parseable with `json` but unreadable, or a hand-rolled
   YAML parser in the tests, which would be fragile.
6. **`workflow-conformance.yml` is left untouched,** because it is Workflow Manager-managed. Its
   sequential job keeps running on `push: main`/PR next to the new matrix, which duplicates about
   one runner's worth of conformance time. Removing that duplication belongs to a Workflow
   Manager release.
7. **The Controller suite runs as seven named shards, not one job per module.** About 25 modules
   would mean about 33 jobs per push. The coverage test keeps the shards total.
8. **Worker stdout/stderr go straight to files, not through a Controller pipe.** Two behavioural
   side effects:
   - an orphaned worker no longer gets `EPIPE` after a Controller Ctrl-C, so it runs to
     completion with its output preserved;
   - "the worker returned control" becomes "the direct child exited and its process group is
     empty", instead of "EOF on both pipes". A descendant that detached with `setsid` is no longer
     waited for. A same-group descendant that holds neither stream is now waited for, visibly
     and bounded only by `--timeout` (see "When the worker has returned control").

   `resume` reconciliation is unchanged by either (see Design).
9. **Stream parsing is strict.** Any non-object line makes the result `AMBIGUOUS`, the same
   fail-closed rule the single-document parser applies today. The real CLI's stream is confirmed
   twice: by a committed real transcript at CP4, which is a parser golden fixture, and by a
   non-waivable minimal-spend check at CP11.
10. **Raw logs are verbatim.** If the CLI ever emits thinking blocks, they are kept in
    `worker.stdout` as evidence and never rendered. Filtering at write time was rejected because
    it would make the durable log differ from what the worker actually produced.
11. **Event-log and run-record writes are best-effort warnings.** If they could fail the
    lifecycle, observability could change outcomes. The job record stays the authority.
12. **`follow` exits `0` when the followed work ends,** whatever the job's outcome. It prints the
    run's exit code but does not mirror it, so its code means one thing.
13. **New log files are created `0o600`.** Tool output can contain secrets. Job records keep
    `0o644`.
14. **No log retention/pruning.**
15. **CI uses Python 3.12 only,** the `requires-python` floor.
16. **Release tags are strictly `vMAJOR.MINOR.PATCH`.** There are no pre-releases, since
    semver's `-rc.1` and PEP 440's `rc1` spell pre-releases differently.
17. **Actions in `release.yml` are pinned to commit SHAs,** because its `publish` job holds
    `contents: write`. `ci.yml` and `validate.yml` keep major tags, as the existing workflows do
    (round 1, O6).
18. **`1.1.0` is the version.**
19. **Release commits must be ancestors of `origin/main`.**

## Checkpoints

<!-- generated by workflow_state.render_registry_markdown -- do not hand-edit -->
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Single authoritative semantic version (controller/version.py) wired into pyproject as the dynamic version, package data so GENERATION.json ships in the wheel, the setup.py build_py hook that writes controller/BUILD_INFO.json (version, source commit, dirty flag, package digest, build origin, release tag) through the dependency-free controller/buildinfo.py, and workflow-controller --version | - | 3 | 1 |
| CP2 | Packaged runtime identity: runtime_kind source/package/unidentified resolution without direct_url.json, git-free materialisation of a package snapshot from the installed tree with package-digest verification, dirty or unknown-provenance build gating, runtime-root ladder row 3 restricted to source runtimes, package-mode generation handoff, and the controller_runtime block in SOURCE_PIN.json, identity.json, job records and status | CP1 | 5 | 1 |
| CP3 | Packaged-runtime regression suite: wheel built from a disposable clone, non-editable venv install, inspect/step/worker launch with the fake worker, source checkout absent, renamed and modified after install, version and job-record identity assertions, the reproduced git-archive failure, a venv inside a checkout, and retained editable/source execution | CP2 | 4 | 1 |
| CP4 | Streaming worker output: claude -p --output-format stream-json --verbose, worker stdout/stderr written by the worker directly into per-job files created before spawn, strict stream parsing that preserves the structured final result and the four-outcome classification, worker_streams in the LAUNCHED record, and a stream-emitting fake worker | CP2 | 4 | 1 |
| CP5 | Durable Controller lifecycle event logs: per-job events.jsonl appended at every job-record transition (including resume and abandon), run records runs/<run_id>.json with the Controller's own process identity and runs/<run_id>/events.jsonl for step/run boundaries, run_id linkage in job records, and best-effort write semantics that never alter a lifecycle outcome | CP4 | 3 | 1 |
| CP6 | Observation surface: controller/observe.py (offset tailer, event normaliser, text and JSON renderers that omit thinking blocks, heartbeat), the zero-write follow command with active run/job discovery, step/run --follow as a stderr renderer thread isolated from the lifecycle, status active runs/jobs and follow hints in resume exit 45, Ctrl-C and worker-active messages | CP5 | 4 | 1 |
| CP7 | Observation isolation and equivalence tests: followed versus unfollowed durable-result equivalence, attach to an active job from a second process, follower kill and broken pipe leaving the worker and exit code unaffected, follower zero-write proof, legacy json worker.stdout replay, and the unchanged lifecycle orchestration, locking and routing regression | CP6 | 4 | 1 |
| CP8 | Release tooling tools/release.py: tag/version verification, wheel verification (metadata, BUILD_INFO, package digest, required files, entry point), duplicate-release refusal through gh that fails closed on an undecidable answer, tag-to-built-commit verification immediately before publication, and SHA256SUMS, with unit tests | CP1 | 3 | 1 |
| CP9 | GitHub Actions: tools/ci_workflows.py model and deterministic YAML emitter, reusable validate.yml (Controller shard matrix, frozen conformance matrix, package job with pipx smoke, fail-fast false, ubuntu-latest), ci.yml with same-ref cancel-in-progress, release.yml gated on validation with non-cancelling concurrency and an immutable GitHub Release, removal of controller-tests.yml, and structural tests | CP3, CP8 | 4 | 1 |
| CP10 | Operator documentation: README install, verify, upgrade, rollback, development install, runtime identity, follow and release procedure; ADR 0002 for release runtime identity and observability; ACTIVE_MILESTONE narrative; README invocation lines parsed by the live parser | CP2, CP6, CP9 | 3 | 1 |
| CP11 | Full verification: Controller suite, frozen conformance suites, workflow render check, local wheel build and isolated pipx install drill including the reproduced base-commit failure and the removed-checkout case, and one live disposable-repository run --follow with a second-terminal attach and detach | CP7, CP10 | 3 | 1 |

Checkpoints are implemented in table order. CP8 depends only on CP1, but it follows CP7 so that
the runtime work lands before the pipeline that packages it.

<!-- CP1 -->
### CP1 -- version source, build identity, `--version`

**Files:**
- new `controller/version.py` and `controller/buildinfo.py`;
- `controller/__init__.py` (the import list gets `buildinfo`, `version` first);
- new `setup.py`;
- `pyproject.toml` (`dynamic`, `[tool.setuptools.dynamic]`, `[tool.setuptools.package-data]`);
- `.gitignore` (`controller/BUILD_INFO.json`, `dist/`);
- `controller/cli.py` (`--version` action);
- `tests/test_package_structure.py` (import list and order; `buildinfo`/`version` import nothing
  from the package);
- new `tests/test_buildinfo.py`.

**Tests (`tests/test_buildinfo.py`, `tests/test_cli.py`):**
- `__version__` matches `SEMVER_RE`. `pyproject.toml` declares the dynamic `attr` wiring and has
  no static `version`.
- `compute_package_digest`:
  - is stable under mode changes and `__pycache__`/`*.pyc` additions;
  - changes on any byte change, added file or removed file;
  - ignores `BUILD_INFO.json` at the root only.
- `validate_build_info` accepts the three origin shapes and rejects each named invariant
  violation, one test per rule:
  - a release without a tag;
  - a tag that does not match the version;
  - a release that is dirty or has no commit;
  - a bad digest shape;
  - a version mismatch;
  - an unknown `schema_version`.
- Hook, driven by building a wheel from a disposable committed clone
  (`pip wheel --no-deps --no-build-isolation`). The skip tests the real prerequisite: a
  `--no-build-isolation` wheel build needs setuptools ≥ 70.1, or an older setuptools plus the
  `wheel` package. Setuptools merely being importable is not enough. Without the prerequisite the
  tests skip, naming the missing piece, unless `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, which makes
  it a failure. One helper, `tests.fixtures.wheel_build_prerequisite()`, is shared with CP3.
  - the wheel contains `controller/GENERATION.json` and a valid `BUILD_INFO.json` with
    `source_commit` equal to the clone's `HEAD`, `source_dirty: false` and
    `build_origin: "local"`;
  - `package_digest` equals a recomputation over the wheel's members;
  - a dirty clone gives `source_dirty: true`;
  - a non-Git copy gives `null`/`null`;
  - `WORKFLOW_CONTROLLER_RELEASE_TAG=v<version>` on a clean clone gives `"release"`;
  - a mismatched tag, or a release tag on a dirty clone, fails the build;
  - the source tree has no `BUILD_INFO.json` after any build;
  - a stale build directory is refused: after one build, delete a module from the clone and
    commit, then rebuild without removing `build/`. The build fails naming the stray
    `build/lib/controller/<module>.py`, and no wheel is written. After `build/` is removed, the
    rebuild succeeds;
  - a changed source file is re-copied even when its mtime does not advance: after one build,
    change a module's bytes in the clone, commit, and set its mtime back to before the previous
    copy's (`os.utime`), then rebuild without removing `build/`. The wheel's copy of that module
    has the new bytes, and `package_digest` equals a recomputation over the wheel's members;
  - `pip install -e .` of a clone (under the same skip rule) leaves no `BUILD_INFO.json` anywhere
    under the clone or in the editable install's mapping. The hook's `editable_mode` early return
    is also unit-tested directly: a `build_py` instance with `editable_mode = True` writes
    nothing and raises nothing.
- `--version` (CP1 prints line 1 only; line 2 lands in CP2):
  - the output is exactly one line, `workflow-controller <__version__>`, from a source checkout
    and from an editable install;
  - exit `0`;
  - no runtime root is created and nothing is written (checked by running with `--runtime-dir`
    pointing at a nonexistent path);
  - it works with no subcommand;
  - `build_parser()` runs no subprocess (a patched `subprocess.run` must not be called).

**Narrowest verification:** `tests.test_buildinfo tests.test_package_structure tests.test_cli`,
then the full suite (the package import list changed).

<!-- CP2 -->
### CP2 -- packaged runtime identity

**Files:**
- `controller/identity.py`:
  - `resolve_runtime`, `runtime_kind`/`version`/`build` on `ControllerIdentity`;
  - `SOURCE_KIND_PACKAGE`;
  - `_extract_package`;
  - kind dispatch in `materialise` and in `pin()`'s unpinned branch (no Git for `package` or
    `unidentified`);
  - the source-plus-build-info ambiguity rule in `resolve_runtime`;
  - `runtime_record`;
  - `describe_runtime` for `--version`/`status`.
- `controller/runtime.py` (the `runtime_kind` parameter; row 3 source-only).
- `controller/handoff.py` (package-mode approved-generation read; `version` in the handoff record).
- `controller/cli.py`:
  - `_dispatch` passes the kind to the ladder and materialise;
  - an `unidentified` refusal for acting commands;
  - the identity-record block and the `status` first line;
  - the `controller` object in `inspect`/`explain` `--json`.
- `controller/job.py` (`_identity_block` adds `controller_runtime`).
- `controller/errors.py`: no new classes. Refusals use `SourceSnapshotError` and
  `DirtyControllerSourceError`, and `raised_by` stays set, which the existing scanner test
  enforces.
- `tests/test_package_structure.py`: the allowlisted `__file__`-rooted reads grow to include
  `identity.resolve_runtime` (the `BUILD_INFO.json` read) and `identity._extract_package`. Each
  allowlist entry gets a comment saying why it runs before any orchestration.
- `tests/test_identity.py`, `tests/test_runtime.py`, `tests/test_handoff.py`, `tests/test_cli.py`,
  `tests/test_job.py`.

**Tests (in-process, against constructed package trees: a directory holding `controller/` plus a
generated `BUILD_INFO.json`, no Git):**
- `resolve_runtime` gives:
  - `package` for a valid build info;
  - `unidentified` for a malformed one or a version mismatch;
  - `source` for a checkout;
  - `unidentified` for a non-Git copy without build info;
  - `unidentified` for `site-packages` inside a Git work tree whose top level is not `code_root`
    (the venv-inside-checkout case);
  - `package` for a package tree inside a Git work tree with `PATH` set to an empty directory,
    and `unidentified` (the reason names the missing `git`) for a committed checkout under the
    same empty `PATH`. Neither raises;
  - `source` is never probed when `code_root/.git` is absent: with a spy on `git` subprocesses,
    a package tree inside a Git work tree makes no Git call during `resolve_runtime`;
  - `unidentified`, with the named "delete it" reason, for a committed checkout with a planted
    `controller/BUILD_INFO.json`. This holds even when that file's digest and version are valid
    for the tree. `step` from that checkout exits `20` and launches nothing. `status` still runs
    and prints the reason;
  - the same planted, valid `BUILD_INFO.json` checkout with `PATH` set to an empty directory:
    `unidentified` with the same "delete it" reason (never `package`), with a spy showing no Git
    call made by the ambiguity rule, and `step` exits `20`. The same holds when `controller/` is
    untracked in that repository.
- `pin()`'s unpinned identity:
  - for a `package` tree placed inside a Git work tree, with a spy on `git` subprocesses:
    `source_commit` equals `build.source_commit`, and no `git` runs at all. Step 3's probes are
    not reached, because `code_root` has no `.git`;
  - the same with `PATH` set to an empty directory: no `FileNotFoundError`, same identity;
  - for a dirty or null-provenance build, `source_commit` is `None`;
  - for `unidentified`, `source_commit` is `None` and no `git` runs;
  - for `source`, unchanged.
- `materialise` for a package:
  - produces a snapshot whose `SOURCE_PIN.json` has `runtime_kind: package`,
    `source_kind: package`, the build's commit and `generation_source: package`;
  - runs without `git` on `PATH`: the test sets `PATH` to an empty directory;
  - refuses with named digests on a digest mismatch caused by editing an installed byte;
  - refuses on a symlink in the installed tree;
  - refuses a dirty or null-provenance build without `--allow-dirty-source` and accepts it with
    the flag, with `source_commit: None`;
  - reuses the snapshot directory on a second materialisation.
- `pin()` in a package snapshot: the cross-check against the exec handoff holds for
  `source_kind: package`.
- Ladder: a package runtime never selects row 3, even when the origin is inside a Git work tree.
  A source runtime still does.
- `handoff.detect` for a package:
  - equal generation gives `None`;
  - an installed newer generation gives a `Handoff` with version fields;
  - an older generation raises;
  - a missing installed tree gives `SourceSnapshotError(raised_by="detect")`;
  - the source checkout is never read (asserted by pointing the recorded origin at a tree with no
    `.git` and a spy on `git` subprocesses).
- The job record carries `controller_runtime` with every field. Records without it still pass
  `validate_record` and `resume` (fixture from the base version's record shape).
- `status` prints the `controller:` line first. The other lines are unchanged.
- `--version` now prints two lines. Line 2 is `describe_runtime`'s text for a `source`, a
  `package` (local and release build info) and an `unidentified` runtime.
- **Source regression:** every existing `test_identity` case (commit/worktree/unpinned, dirty
  refusal, re-exec depth guard, `-P -B`, `PYTHONPATH` stripping for workers) passes unchanged
  except for the additive fields.

**Narrowest verification:** `tests.test_identity tests.test_runtime tests.test_handoff
tests.test_cli tests.test_job tests.test_job_validation tests.test_resume
tests.test_package_structure`.

<!-- CP3 -->
### CP3 -- packaged-runtime regression suite

**Files:**
- new `tests/test_packaged_runtime.py`;
- `tests/fixtures.py`:
  - `build_checkout` also copies `setup.py`, since without it a fixture wheel has no
    `BUILD_INFO.json`;
  - `build_wheel(checkout, out_dir, env=None)` and
  `wheel_install(wheel, venv_dir)`, a non-editable `pip install --no-deps --no-index` returning
  the console-script path.

**Setup.** The module builds one wheel per test class from a `build_checkout(...)` clone
(committed, generation 1) with host setuptools and `--no-build-isolation`, so it needs no
network. It skips with a reason when `tests.fixtures.wheel_build_prerequisite()` (CP1) reports
the build prerequisite missing. With
`CONTROLLER_REQUIRE_PACKAGING_TESTS=1`, which CI's `package` job sets, a missing prerequisite is
a failure instead.

**Cases:**
1. **The reproduced defect.** A non-editable install runs `step` against a managed fixture repo
   (the stub Workflow Manager, `--claude-binary tests/fake_claude.py`, a `PLANNING` work item
   whose plan-stage action launches a worker):
   - it exits as the scripted outcome dictates, not `20`;
   - stderr never mentions `git archive`;
   - the job record exists with `worker_outcome` set.
2. **Checkout absent.** Build, install, then `shutil.rmtree` the clone. `--version`, `inspect`,
   `status` and a worker-launching `step` all work. The job record's `controller_runtime` shows
   `runtime_kind: package`, `version == __version__`, `source_commit ==` the clone's former
   `HEAD` and `package_digest ==` `BUILD_INFO`'s.
3. **Checkout renamed.** The same as case 2, after `os.rename` instead of deletion.
4. **Checkout modified after install.** Commit a change to the clone's `controller/version.py`
   (`9.9.9`), `GENERATION.json` (generation 2) and one module.
   - The installed `--version` still prints the built version.
   - `step` produces a job record with the original identity and no handoff (exit is not `50`).
   - The runtime root is the XDG path, not `<clone>/.controller`.
5. **Venv inside a checkout.** Create the venv at `<clone>/.venv`, install the wheel, then make
   a new commit in the clone so its `HEAD` differs from the build's commit. Run `status`,
   `inspect` and `step`.
   - The runtime root is XDG (or `--runtime-dir`), never `site-packages/.controller`, and the
     identity is `package`.
   - `identity.json` after `status` and after `inspect` records the build's `source_commit`,
     not the clone's new `HEAD`.
6. **Version equals artifact.** `--version` line 1, `METADATA` `Version:` and `BUILD_INFO.version`
   are all equal.
7. **Tampered install.** Edit one byte of an installed module. `step` exits `20` with the
   digest-mismatch message and launches no worker (the fake worker's invocation counter stays
   empty).
8. **Upgrade mid-run handoff.** Install generation 1 and start `run` with `--pause-file` (the
   test hooks). Reinstall a generation-2 wheel with `pip install --force-reinstall`, then release
   the pause. `run` exits `50`, and `handoff.json` names both versions.
9. **Editable/source retained.** `editable_install` of a clone gives `runtime_kind: source`,
   `source_kind: commit`, row 3 `.controller`. Dirty edits need `--allow-dirty-source` as before.

**Narrowest verification:** `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest
tests.test_packaged_runtime -v`.

<!-- CP4 -->
### CP4 -- streaming worker output

**Files:**
- `controller/worker.py` (argv; `stdout_path`/`stderr_path`; the two-phase wait with the group
  drain; the `on_group_drain` callback; `_parse_worker_stream`; docstrings);
- `controller/runtime.py` (`create_log_file`);
- `controller/job.py` (file creation before spawn, `worker_streams` in the `LAUNCHED` flush,
  removal of `_write_worker_streams`, the `WorkerNotStarted` path for creation failure, the
  `on_group_drain` callback writing `worker_group_drain` and the drain line);
- `tests/fake_claude.py`:
  - default output becomes a stream (`system/init`, an assistant text, a Bash `tool_use`, its
    `tool_result`, a `result`);
  - it refuses `stream-json` without `--verbose` with the real CLI's message and exit `1`;
  - `FAKE_CLAUDE_STDOUT` stays the exact-stdout override;
  - new `FAKE_CLAUDE_EVENT_DELAY` (seconds between events) and
    `FAKE_CLAUDE_PAUSE_AFTER_EVENTS_FILE` (emit N events, then wait for a file), for the
    observation tests;
  - new `FAKE_CLAUDE_DESCENDANT` (`group:<seconds>`, `group-closed:<seconds>` or
    `setsid:<seconds>`): before exiting, the fake forks a descendant that lives for that long.
    With `group:` it stays in the worker's process group and keeps stdout open. With
    `group-closed:` it stays in the group but closes stdout and stderr first. With `setsid:` it
    leaves the group and keeps stdout open;
- new `tests/golden/claude_stream_json_2.1.281.jsonl`: a real `claude -p --output-format
  stream-json --verbose` transcript. It is captured once at CP4 with the cheapest routing (a
  one-line prompt such as `Reply with the word ok.`, `--model haiku`, no tools needed) and a
  spend of cents. It is sanitised by replacing session, message and request ids and any
  user-specific paths with fixed placeholders, keeping every line's structure. The capture
  command and CLI version are recorded in a sibling `.md` note. This is the only real-CLI call
  in the implementation checkpoints, and it needs the live `claude` binary. If it cannot be made,
  CP4 stops and reports rather than substituting a hand-written transcript;
- `tests/test_worker.py`, `tests/test_job.py`, `tests/test_lifecycle_orchestration.py` and
  `tests/test_integration_disposable_repo.py` fixtures updated to the stream shape.

**Tests:**
- argv:
  - the exact order, `--verbose` present;
  - the disallow list still last;
  - no session-reuse flag;
  - `PYTHONPATH` still removed.
- Streams:
  - bytes reach `stdout_path` while the worker is still running: the fake pauses mid-stream and
    the test reads the file before releasing it;
  - stderr reaches `stderr_path`;
  - `WorkerResult.stdout` equals the file content.
- Classification table:
  - a well-formed stream gives `SUCCESS`;
  - `is_error: true` gives `FAILURE`;
  - a non-zero exit gives `FAILURE`;
  - each of these gives `AMBIGUOUS`: no result event, two result events, a result not last, a
    non-JSON line, a blank interior line, a non-object line, and a result missing `session_id`;
  - a signal gives `INTERRUPTED`;
  - a timeout gives `INTERRUPTED`, and the whole group is killed.
- `raw_json` is the result event. `_worker_dict` fields equal today's for the same result values.
- **Real-CLI golden:** `_parse_worker_stream` over the committed transcript returns its `result`
  event, and `_classify` with exit `0` gives `SUCCESS`, with `session_id`, `is_error` and
  `num_turns` read from it.
- Return of control:
  - with `FAKE_CLAUDE_DESCENDANT=group:2`, `launch` returns no earlier than the descendant's exit,
    and the job's verification runs after it (the record's `completed_at` is after the
    descendant's recorded exit time);
  - with `FAKE_CLAUDE_DESCENDANT=setsid:5`, `launch` returns once the direct child exits. The job
    completes as documented. When the descendant also inherited the lifecycle-lock descriptor, an
    immediate second `step` exits `45`;
  - `--timeout` expiring during the group drain ends the group through `_kill_drained_group`
    (never `os.getpgid`: a spy on `os.getpgid` is not called after phase 1) and gives
    `INTERRUPTED` with `returncode` equal to the direct child's real code;
  - `_classify` unit cases: `timed_out=True` gives `INTERRUPTED` for exit `0` with a well-formed
    stream; `timed_out=False` leaves the existing table unchanged;
  - under the `killpg` form (`_proc_numbers_own_namespace` patched false), the drain keeps
    waiting while `killpg(pgid, 0)` succeeds, ends on `ProcessLookupError`, and
    `on_group_drain` receives `remaining_pids == []` with the "unknown number" drain line;
  - with `FAKE_CLAUDE_DESCENDANT=group-closed:2`, a same-group descendant that closed stdout and
    stderr keeps `launch` waiting until it exits. Stderr has exactly one
    `worker pid P exited; waiting for 1 process(es) still in its process group: <pid>` line.
    The record carries `worker_group_drain` with that pid while still `LAUNCHED`;
  - the same with `group-closed:30` and `--timeout 3`: the group is killed, and after `launch`
    returns the descendant's pid is gone (`os.kill(pid, 0)` raises `ProcessLookupError`, or
    `/proc/<pid>/stat` shows a zombie). The record shows `worker_outcome: INTERRUPTED` with
    `worker.exit_code: 0`;
  - with no descendant, `on_group_drain` is never called and the record has no
    `worker_group_drain`;
  - `on_group_drain` raising ends the group and propagates, as `on_spawn` raising does. After
    `launch` raises, the descendant's pid is gone by the same assertion. A stderr that raises
    `OSError` on the drain line does not end the group.
- Job record: `worker_streams` is present at the first `LAUNCHED` flush, before spawn, with paths
  that exist and mode `0o600`. `worker.stdout_path` equals `worker_streams.stdout_path`. A
  creation failure (a pre-existing file, `O_EXCL`) gives a `FAILED` `WorkerNotStarted` record
  whose message and evidence come from the wrapping `WorkerLaunchError`, with no worker invoked.
  Through `main()`, the exit code is `20`, and stderr has one `error:` line and no traceback.
- Orphan: after the Controller process is killed mid-job, the fake worker completes and its full
  stream is in the file. The record stays `LAUNCHED` and reconciles exactly as before.

**Narrowest verification:** `tests.test_worker tests.test_job tests.test_resume
tests.test_lifecycle_orchestration`.

<!-- CP5 -->
### CP5 -- lifecycle event logs and run records

**Files:**
- `controller/runtime.py` (`append_jsonl`, best-effort wrapper, one-warning-per-process latch);
- `controller/job.py`:
  - `_persist(..., event=...)` at every call site: `execute_step`, `_no_launch_record`, the
    `resume` reconciliations and `abandon`;
  - `event_seq` on the job record, incremented by `_persist` before the record write;
  - optional `run_id` on `execute_step`, recorded on the job record, with mirrored run events;
- `controller/cli.py`:
  - run-record creation in `cmd_step`/`cmd_run`, registered in `_open_run`;
  - the single closing site in `main()` (mapped exit code, or `interrupted` on
    `KeyboardInterrupt`);
  - `step_started`/`no_action`/`handoff_detected` events in `_run_one_step`/`cmd_run`;
- `tests/test_job.py`, `tests/test_cli.py`, `tests/test_resume.py`, new `tests/test_observe.py`
  (log-shape part).

**Tests:**
- Each job path appends its named events in order, with strictly increasing `seq` equal to the
  record's `event_seq` at each write:
  - a `FINISHED` job;
  - a `FAILED` verification;
  - a gate;
  - a decline;
  - a pending handoff;
  - `WorkerNotStarted`;
  - `resume` reconciliation to `INTERRUPTED`;
  - `--abandon`.
- `seq` across processes: `execute_step` is interrupted in one process, `resume` reconciles in a
  second and `--abandon` runs in a third (on a second orphaned job). The job's log has strictly
  increasing `seq`, continuing from the record's `event_seq`, with no repeats.
- A record without `event_seq` (the base version's shape) gets `seq` `1` on its first new event
  and still passes `validate_record`.
- The final event agrees with the record's status.
- A run record has `state: running` during a paused job and `ended` with the exit code afterwards,
  for every `run`/`step` exit path. This includes a raised `ControllerError` (exit `20`) and exit
  `45`, and in each case `exit_code` equals the code `main()` returned. `job_ids` and
  `current_job_id` track the jobs.
- Ctrl-C with an orphan: `run` is interrupted with `SIGINT` while a fake worker is paused. The run
  record reads `state: "interrupted"`, `exit_code: null`, and `current_job_id` is the orphan's
  job. The worker is still running.
- `command` is `"step"`/`"run"` with and without `--follow`. `target_repo` equals the job
  records' `target_repo` when `<repo>` is given as a subdirectory and through a symlink.
- Best-effort, for each of `OSError`, `RuntimeContainmentError` and `TypeError` (a non-JSON
  detail) forced from `append_jsonl`, and for `OSError` forced from the run-record write: the
  lifecycle result, the job record and the exit code are identical to the unforced run. Exactly
  one warning is printed.
- `worker_exited`: a `group-closed:` descendant appends it after `worker_spawned` and before
  `completed`, with the record's `event_seq`. With that append forced to raise `OSError`, the
  job reaches the same status as unforced.
- `worker_spawned`: with that one append forced to raise `OSError`, and separately `TypeError`,
  the worker runs to completion, the job reaches the same status as unforced, and no
  kill-and-reap happened.
- `KeyboardInterrupt` raised from `append_jsonl` propagates; the wrapper does not swallow it.
- `resume`, `pending_reconciliation_jobs`, `_classify_jobs` and `abandon` never read anything
  under `runs/` or `jobs/<id>/`. This is asserted with a spy on `open`/`read_bytes` path
  arguments.
- No record or event contains a `follow` key.

**Narrowest verification:** `tests.test_job tests.test_cli tests.test_resume tests.test_observe
tests.test_write_containment`.

<!-- CP6 -->
### CP6 -- observation surface

**Files:**
- new `controller/observe.py`;
- `controller/__init__.py` and `tests/test_package_structure.py` (`observe` placed after `job`,
  before `cli`);
- `controller/cli.py`:
  - the `follow` subparser and `cmd_follow`, dispatched before runtime-root creation, pinning and
    the identity write;
  - `--follow` on `step`/`run`;
  - `_start_follower`;
  - the `status` `active:` section;
  - the hint lines;
- `controller/job.py` (hint text in `_announce_orphaned_worker`, and its drain-aware variant
  selected by the `spawned["drained"]` flag the `on_group_drain` callback sets);
- `controller/errors.py`: no new class. The `LifecycleWorkerActiveError` message text gains the
  hint.
- `tests/test_observe.py`, `tests/test_cli.py`.

**Tests:**
- `normalise`/`render_text` for every row of the presentation table:
  - thinking and redacted-thinking blocks produce no output at all;
  - truncation boundaries;
  - an unknown event type;
  - a non-JSON line;
  - a legacy single-JSON `worker.stdout`.
- `Tail` holds back a partial trailing line until its newline arrives, and tolerates the file
  appearing late.
- `follow` selection:
  - a running run is chosen;
  - with no run, an orphan job with an active worker is chosen;
  - otherwise the "nothing active" message and exit `0`;
  - `--job`/`--run` explicit selection;
  - `<repo>` omitted resolves to the working directory;
  - from a source checkout, a bare `follow <repo>` without `--runtime-dir` finds a run that
    `step` created there without `--runtime-dir` (ladder row 3, through the read-only
    `resolve_runtime`), and still writes nothing;
  - a target mismatch or unknown id exits `20`.
- `follow --run` on an `interrupted` run whose worker is still active hands over to the job, with
  the named line, and ends with `worker exited; job <id> awaits resume` once the worker exits. On
  an `interrupted` run whose worker is gone, it ends at once with `run <id> was interrupted`.
- A run whose Controller pid is gone (a `SIGKILL`ed Controller, `state` still `running`) ends the
  follow with the "run record was not closed" line.
- Heartbeat fires after the (overridden) silence interval. For a `LAUNCHED` record carrying
  `worker_group_drain` it reads `worker exited; waiting on process group: N process(es) at drain
  start ...`, never `worker running: pid P`.
- `--json` output is one parseable object per line.
- Zero writes: the runtime root's full tree listing, sizes and mtimes are identical before and
  after `follow` against a finished run. A nonexistent `--runtime-dir` is not created.
  `identity.json` is untouched.
- `status` `active:` lists the run and job with the exact follow command. It shows `active: none`
  when idle. A job whose record carries `worker_group_drain` shows the process-group wording.
- The exit-45 and orphan messages contain the follow command.
- Ctrl-C (`KeyboardInterrupt` raised from the fake worker's drain wait) after `on_group_drain`
  has run prints the drain-aware line: it names pid P as exited and group G as still having
  members, keeps the `kill -TERM -- -G` advice and the follow command, and never says "the
  worker (pid P, process group G) keeps running". A Ctrl-C before the drain still prints
  today's wording.
- The `--follow` attribute is read only in `cli._start_follower` (AST scan of
  `controller/*.py`).

**Narrowest verification:** `tests.test_observe tests.test_cli tests.test_package_structure`.

<!-- CP7 -->
### CP7 -- observation isolation and equivalence

**Files:** new `tests/test_observation_equivalence.py`, which reuses the scripted fake-worker
harness from `tests/test_lifecycle_orchestration.py` (factored into `tests/fixtures.py` if
needed).

**Tests:**
1. **Equivalence.** A scripted multi-step `run` (plan stage through a gate, plus one
   implementation checkpoint) is executed in three fresh, identical fixtures:
   - (a) plain;
   - (b) with `--follow`;
   - (c) plain, with a separate `follow` process attached after the first event and `SIGKILL`ed
     mid-job.

   The following must be equal:
   - exit codes;
   - the normalised job records (ids, timestamps, pids, paths and durations replaced by
     placeholders; everything else compared byte-for-byte as JSON);
   - the normalised run records and event logs;
   - target `WORKFLOW_STATE.json` and the Git log shape;
   - the fake worker's diagnostics (argv minus the task-independent paths, cwd, env keys,
     inherited fds count).
2. **Attach.** With a worker paused mid-stream, `workflow-controller follow <repo>` from a second
   process prints the already-emitted events and then the post-release ones, and exits `0` when
   the run ends.
3. **Detach.** A follower is `SIGKILL`ed and another gets `SIGPIPE` (stdout piped to a closed
   reader). The worker completes, the job is `FINISHED` and the run's exit code is unchanged.
4. **In-process renderer failure.** `run --follow` with stderr replaced by a pipe whose reader is
   closed immediately (`BrokenPipeError` on the first write): the exit code and durable results
   equal the plain run.
4a. **Stalled stderr.** `run --follow` with stderr connected to a pipe whose reader never reads,
   and a fake worker whose stream is larger than the pipe's capacity. The process terminates
   within a bound (30 s), is not killed by a signal (no `SIGABRT`), and its exit code and durable
   results equal the plain run with the same pipe. The same holds for a run ending in a
   `ControllerError`, whose `error:` line still reaches the pipe. That is the reserve rule.
5. **Final result retained.** After a streamed job, the record's `worker` block, `worker_outcome`
   and `transition_verified` equal those of a run whose fake emits the identical result event.
6. **Lifecycle regression.** `tests.test_lifecycle_orchestration`, `tests.test_lock`,
   `tests.test_routing` and `tests.test_resume` pass unchanged apart from the CP4 fixture shape.
   The golden plan-stage decision table is byte-identical.

**Narrowest verification:** `tests.test_observation_equivalence tests.test_lifecycle_orchestration
tests.test_lock tests.test_routing tests.test_resume tests.test_golden_plan_stage_decisions`.

<!-- CP8 -->
### CP8 -- release tooling

**Files:** new `tools/release.py`, new `tests/test_release_tools.py`.

**Tests:**
- `verify-tag`:
  - `v<version>` passes;
  - `v<other>`, `<version>` without the `v`, `v1.1`, `v1.1.0-rc.1`, `v01.1.0` and a trailing
    suffix each fail with the named check;
  - a `pyproject.toml` with a static `version`, or with the `attr` pointing elsewhere, fails.
- `verify-wheel`, against wheels built in-test (as in CP1) and zip files mutated in-test:
  - passes for a correct `--local` wheel;
  - passes for a release wheel built with the matching tag;
  - fails for each of these:
    - a wrong filename version;
    - a `METADATA` mismatch;
    - a missing `GENERATION.json`;
    - a missing or invalid `BUILD_INFO`;
    - an included `__pycache__`;
    - an included `SOURCE_PIN.json`;
    - a tampered module (digest mismatch);
    - a commit mismatch;
    - a dirty build;
    - a release-mode check on a local wheel, and the reverse;
    - a wrong entry point.
- `check-unpublished` with an injected `gh` runner:
  - "release not found" passes;
  - an existing release is refused as `already published`;
  - an auth, network or unknown error is refused as undecidable.
- `verify-tag-commit` with an injected Git runner:
  - a lightweight tag naming `SHA` passes;
  - an annotated tag whose peeled `^{}` line names `SHA` passes, even though its direct line
    names the tag object;
  - a tag naming another commit is refused as `tag moved`;
  - a non-zero exit, no matching line, or a malformed line is refused as undecidable.
- `checksums` output matches `hashlib` and the `sha256sum -c` format.
- `checksums` rerun over a directory that already holds a `SHA256SUMS` (stale or current) does
  not list `SHA256SUMS`, produces the same bytes as the first run, and `sha256sum -c` passes.

**Narrowest verification:** `tests.test_release_tools tests.test_buildinfo`.

<!-- CP9 -->
### CP9 -- GitHub Actions

**Files:**
- new `tools/ci_workflows.py`;
- new `.github/workflows/validate.yml`, `ci.yml` and `release.yml` (rendered);
- delete `.github/workflows/controller-tests.yml`;
- new `tests/test_ci_workflows.py`.

**Tests (`tests/test_ci_workflows.py`):**
- `--check` passes on the committed files. Editing one committed byte in a temporary copy makes
  it fail.
- Emitter:
  - round-trips representative structures to known-good text (golden strings);
  - quotes every string the safe regex rejects;
  - renders `on` as a key.
  - If PyYAML happens to be importable, the rendered text parses back to the model. That
    cross-check is skipped otherwise and never required.
- Coverage: the union of the shard modules, `package` and the named exclusion equals the
  discovered `tests/test_*.py`, with no module in two places.
- The conformance matrix equals the suites that the managed `workflow-conformance.yml` runs,
  extracted by a line regex over its `run: python3 <file>` lines. This keeps the two in sync with
  no YAML parser.
- Every matrix job has `strategy.fail-fast: false`. Every job has `runs-on: ubuntu-latest`.
- `ci.yml` concurrency group contains both `github.workflow` and `github.ref`, and
  `cancel-in-progress` is `true`. The push trigger is `branches: [main]` with no `tags` filter.
- `release.yml`:
  - the tag trigger is `v*`;
  - concurrency is `cancel-in-progress: false`;
  - `build.needs ⊇ {validate}` and `publish.needs ⊇ {validate, build}`;
  - `validate` calls `./.github/workflows/validate.yml`;
  - `publish` is the only job with `contents: write`;
  - `publish` runs `check-unpublished` before `gh release create` and passes `--verify-tag`;
  - `build` resolves `RELEASE_COMMIT` with `git rev-parse "$GITHUB_SHA^{commit}"` before any
    step that uses it and exports it as the job output `release_commit`; that peel step has
    `id: peel`, its `run:` writes `release_commit=` to `"$GITHUB_OUTPUT"` (and `RELEASE_COMMIT=` to
    `"$GITHUB_ENV"`), and `build.outputs.release_commit` equals
    `${{ steps.peel.outputs.release_commit }}` (no job output references `env.`); `publish` takes it from
    `needs.build.outputs.release_commit`; after that step, no release step names `$GITHUB_SHA`;
  - `publish`'s job-level `env` sets `GH_TOKEN` to `${{ github.token }}` and `RELEASE_COMMIT` to
    `${{ needs.build.outputs.release_commit }}`;
  - every step, in any rendered workflow, whose `run:` invokes `gh` or
    `tools/release.py check-unpublished` has `GH_TOKEN` in its effective environment (step
    `env` merged over job `env`);
  - every step whose `run:` references `$RELEASE_COMMIT` has it in its effective environment:
    in `build`, it comes after the peel step that writes it to `$GITHUB_ENV`; in `publish`, it
    is in the step or job `env`;
  - `publish`'s wheel re-verify passes `--commit "$RELEASE_COMMIT"`;
  - the CP9 notes record, with the documentation source, what `$GITHUB_SHA` holds for an
    annotated tag push;
  - `publish` runs `verify-tag-commit "$GITHUB_REF_NAME" "$RELEASE_COMMIT"` after
    `check-unpublished`, and it is the step immediately before `gh release create`. In the
    draft form it also runs immediately before `gh release edit --draft=false`;
  - `build` runs `verify-tag`, the `RELEASE_COMMIT` peel, the `origin/main` ancestry check,
    `verify-wheel` with `--tag` and `--commit "$RELEASE_COMMIT"` and the pipx smoke test, in that
    order;
  - `publish` has an `actions/checkout` step before its first `tools/release.py` step;
  - artifact contract: `build`'s `upload-artifact` and `publish`'s `download-artifact` set the
    same `name`; the upload `path`, normalised with no trailing slash, equals the `-w` directory
    of `build`'s `pip wheel` and the `checksums` directory argument; the download `path`,
    normalised the same way, equals the directory prefix of every file argument `publish`'s later
    steps glob (the `verify-wheel` wheel argument and every `gh release create` asset argument);
  - every `uses:` in `release.yml` matches `<owner>/<repo>@<40 hex>`;
- No `--commit` argument in any rendered workflow (`validate.yml` included) is a bare
  `"$GITHUB_SHA"`. `validate.yml`'s `package` job passes
  `--commit "$(git rev-parse "$GITHUB_SHA^{commit}")"`.
  - no step anywhere contains `--clobber`, `gh release upload` or `gh release delete`.
    `gh release edit` appears at most once, as `--draft=false` in `publish` after that job's own
    `gh release create --draft`.
- No job uses `actions/cache`, a secret other than `github.token` or `CONTROLLER_LIVE_WORKER`.
  `upload-artifact`/`download-artifact` appear only on release `build`/`publish`.
- `controller-tests.yml` no longer exists. `workflow-conformance.yml`'s bytes equal the managed
  digest in `.workflow-manager/installation.json`, so it is untouched.

**Narrowest verification:** `tests.test_ci_workflows` and `python3 tools/ci_workflows.py --check`.

<!-- CP10 -->
### CP10 -- operator documentation

**Files:** `README.md`, new `docs/adr/0002-release-runtime-identity-and-observability.md`,
`docs/ACTIVE_MILESTONE.md`.

**README:**
- **"Installation"** is rewritten:
  - release install: `pipx install <release wheel URL>`;
  - verification: download `SHA256SUMS` and run `sha256sum -c`, then `workflow-controller --version`;
  - **upgrade**: check `status` shows `active: none`, then `pipx install --force <new wheel URL>`.
    It explains the mid-run behaviour: running snapshots are unaffected, and a generation change
    stops `run` at the next boundary with exit `50`. It also names two fail-closed cases an
    operator should expect. `pipx install --force` recreates the venv, so a `run` crossing that
    window stops with `SourceSnapshotError` (exit `20`, `raised_by: "detect"`), not `50`. A
    Python minor-version change moves the `site-packages` path recorded as
    `origin_source_root`, with the same effect. In both cases, rerun `run`;
  - **rollback**: `pipx install --force <older wheel URL>`. It covers the generation-downgrade
    consequences: records written by a newer generation are refused as `StaleJobRecordError`
    until handled, and a running newer generation raises `GenerationHandoffPendingError` at its
    boundary;
  - **development install**: editable, a source runtime, `--allow-dirty-source`.
  - The old "Only a Controller installed from its own checkout ..." limitation paragraph is
    removed.
- **"Runtime identity":** source versus package, `BUILD_INFO.json`, what `--version`/`status`
  print, the `controller_runtime` job field, and the runtime-root ladder, including row 3 being
  source-only.
- **"Observing workers":** `step/run --follow`, `follow`, `status` `active:`, the log files and
  their `0o600` mode, thinking blocks never rendered, and the presentation-only guarantee.
- **"Releasing" (maintainer):**
  1. bump `controller/version.py` in a PR to `main`;
  2. after merge, tag `v<version>` on that `main` commit and push the tag;
  3. the release workflow validates, builds, verifies and publishes;
  4. enable the repository's immutable-releases setting once;
  5. a failed release is fixed by a new PATCH version, never by re-tagging.

  It states the moved-tag rule: a tag moved during its release run is refused by
  `verify-tag-commit`, apart from a seconds-long window before `gh release create`, which
  immutable releases close once the release is published.

  It also states what `build_origin: "release"` does and does not prove. The value is set by an
  environment variable at build time, so any local build can claim it. Release provenance is
  verified by `SHA256SUMS` from the GitHub Release, not by `--version`. Build-provenance
  attestation is a possible later item.

  CI and release concurrency behaviour are described.

**ADR 0002** records these decisions and their rejected alternatives (the scope-judgment list):
the kinds and their resolution order, the package snapshot, the gating, the ladder, the handoff
by kind, the build identity, streaming to files, the event logs, follow's zero-write contract,
and the presentation-only enforcement. ADR 0001's exit-code table is unchanged. ADR 0002 states
that `follow` uses only `0`/`2`/`20`.

**Tests:** `tests/test_plan_document_consistency.py` already parses every README and ADR
invocation line with the live parser. CP10 extends its ADR set to include ADR 0002 and adds a
check that the README names every `validate.yml` job.

**Narrowest verification:** `tests.test_plan_document_consistency tests.test_checklist_corrections`.

<!-- CP11 -->
### CP11 -- full verification

1. `python3 -m unittest discover -s tests -t . -v` with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`.
2. The seven conformance suites, from `scripts/`.
3. `python3 tools/ci_workflows.py --check`.
4. **Packaged drill with a real pipx**, isolated with `PIPX_HOME`/`PIPX_BIN_DIR` in a temporary
   directory, so the operator's own pipx installs are untouched:
   - at the **base commit**, build and `pipx install` a wheel, then run `step` on a fixture
     target. Record the `git archive failed` exit `20`;
   - at **HEAD**, build and install a wheel, move the clone away and run `--version`, `status`,
     `inspect`, and a `step` with the fake worker. Record the output and the job record's
     `controller_runtime`.
5. **Real-CLI stream check (minimal spend, not waivable before acceptance):** one
   `claude -p "Reply with the word ok." --output-format stream-json --verbose --model haiku`
   against the then-installed CLI. Its stdout must pass `_parse_worker_stream` and classify
   `SUCCESS`. Record the CLI version and the result. A failure stops CP11.
6. **Live drill (real spend, run once; the functional review may waive it):**
   - `run --follow` against a disposable managed repository at `PLANNING`, with the default
     routing;
   - `follow <repo>` in a second process, `SIGKILL`ed mid-worker, then re-attached;
   - confirm that:
     - the real CLI's stream passes the strict parser;
     - tool calls and results render;
     - no thinking block is rendered;
     - the job reconciles `FINISHED` or reaches the expected gate;
     - the durable logs replay with `follow --run <id> --from-start`.
7. **Record** results, the drill transcripts' key lines, and the per-shard timings from one local
   run of each shard's command, in the checkpoint notes.

## Plan review decisions

### Round 1 (`LOCAL_MODEL_PLAN_REVIEW`, `REVISE`, plan revision 1)

Every finding was checked against the base tree before it was applied. All sixteen are accepted.

| Finding | Evidence checked | Disposition |
|---|---|---|
| I1: unpinned `pin()` runs Git against a package's `source_root` | `controller/identity.py`, `pin()`'s `else` branch runs `git rev-parse HEAD` whenever `_is_git_repository(source_root)`; `cli._write_identity_record` records it | Accepted: "The unpinned identity by kind"; tests in CP2 and CP3 case 5 |
| I2: checkout with a stray `BUILD_INFO.json`; editable hook behaviour | `handoff._read_approved_generation` reads `HEAD` only; setuptools 84.0.0 `build_py.run` returns early on `editable_mode` (checked on this host) | Accepted: ambiguity is `unidentified` (fail-closed); the hook is a no-op in `editable_mode` and writes only under `build_lib`; tests in CP1 and CP2 |
| I3: where `seq` comes from across processes | CP5's no-read test and the "increasing `seq`" test cannot both hold with a log-derived or per-process counter | Accepted: `event_seq` on the job record; the no-read test is kept whole; cross-process test in CP5 |
| I4: best-effort catch is narrower than "never raises" | `RuntimeContainmentError(ControllerError)` in `controller/errors.py`; `worker.launch` kills and reaps on any `on_spawn` exception (`except BaseException`) | Accepted: the wrapper catches `Exception`, never `BaseException`; `TypeError` and containment cases in CP5 |
| I5: run-record exit code and Ctrl-C | `cli.main()` maps `LifecycleWorkerActiveError` to `45` and `ControllerError` to `20` outside the command functions; `KeyboardInterrupt` is unmapped (`job.py` re-raises it after `_announce_orphaned_worker`) | Accepted: one closing site in `main()`; `interrupted` state; `follow_run` hands over to the job; tests in CP5 and CP6 |
| I6: daemon renderer holding `sys.stderr`'s lock at shutdown | CPython's `_enter_buffered_busy` fatal error for a daemon thread holding a buffered writer's lock at finalisation | Accepted: private `os.dup(2)` sink with `os.write`, a bounded `poll`, and a 16 KiB FIFO reserve; CP7 test 4a |
| I7: `wait()` moves the "returned control" point | `controller/worker.py` `launch` uses `communicate()`; `DEFAULT_WORKER_TIMEOUT`'s comment in `controller/job.py` names it | Accepted: two-phase wait with a process-group drain; the `setsid` narrowing and the resulting exit `45` are named; tests in CP4 |
| I8: strict parser checked against the real CLI only by a waivable drill | `job._VERIFYING_WORKER_OUTCOMES = {"SUCCESS", "INTERRUPTED"}`, so a parser mismatch fails every job | Accepted: a committed real transcript at CP4 and a non-waivable minimal-spend check at CP11 step 5 |
| O1: `publish` has no checkout | The job runs `tools/release.py` | Accepted |
| O2: run record `command`/`target_repo` | Guarantee 3; `follow`'s target check | Accepted |
| O3: stream-file creation error type | `WorkerLaunchError(ControllerError)` exists in `controller/errors.py`; `main()` handles only `ControllerError` | Accepted: wrapped in `WorkerLaunchError` |
| O4: packaging-test skip prerequisite | A `--no-build-isolation` wheel build needs setuptools ≥ 70.1 or `wheel` | Accepted: shared prerequisite helper |
| O5: `build_origin: "release"` is self-asserted | Set from `WORKFLOW_CONTROLLER_RELEASE_TAG` | Accepted: README states that `SHA256SUMS` is the provenance check |
| O6: action pinning (answers unresolved question 2) | `release.yml` `publish` holds `contents: write` | Accepted: SHA pins in `release.yml`; scope judgment 17 revised |
| O7: carried follow-ups not named in Non-goals | `docs/ACTIVE_MILESTONE.md` names them; the plan did not | Accepted |
| O8: `follow`'s `<repo>` default | Roadmap's `workflow-controller follow` usage | Accepted: defaults to `.` |

None of these changes adds an exit code, edits `scripts/`, `.claude/commands/` or
`workflow-conformance.yml`, or changes the checkpoint set.

### Round 2 (`LOCAL_MODEL_PLAN_REVIEW`, `REVISE`, plan revision 2)

Every finding was checked against the base tree before it was applied. All eight are accepted.

| Finding | Evidence checked | Disposition |
|---|---|---|
| I1: the ambiguity rule runs Git against every package `code_root` | `controller/identity.py` `_run_git` is a bare `subprocess.run(["git", ...])` with no `OSError` handling (`controller/runtime.py`'s `_is_git_repository` does catch `OSError`, `identity`'s does not) | Accepted, option (a): step 3 is gated on `code_root/.git` existing, and any `OSError` launching `git` means "not `source`"; "Git calls during resolution" names the only Git calls; CP2 tests with `PATH` emptied and a no-Git spy |
| I2: the group drain can block silently with no default timeout | `controller/job.py` `DEFAULT_WORKER_TIMEOUT = None` | Accepted: the same-group, stream-closed case and its outcome are named; the `on_group_drain` callback records `worker_group_drain`, prints the drain line and (CP5) appends `worker_exited`; heartbeat and `status` wording; CP4, CP5 and CP6 tests with `FAKE_CLAUDE_DESCENDANT=group-closed:` |
| I3: a tag moved during its first release run publishes a mismatched wheel | `gh release create --verify-tag` checks only that the tag exists; `build` builds `$GITHUB_SHA` | Accepted: `tools/release.py verify-tag-commit` via `git ls-remote`, peeled for annotated tags, fail-closed; runs immediately before `gh release create` (and before `--draft=false` in the draft form); CP8 unit tests, CP9 ordering test, README window note; CP8's registry name and R8 name it |
| O1: CP1 `--version` line 2 undefined | `describe_runtime` is a CP2 file item | Accepted: CP1 prints line 1 only; CP2 adds line 2 and tests it per kind |
| O2: `package` job's `setuptools>=68` | Round 1 O4's prerequisite is setuptools ≥ 70.1 or `wheel` | Accepted: `setuptools>=70.1` |
| O3: stale `build/lib` modules shipped and hashed | setuptools builds in-tree through `build/lib` and never prunes it | Accepted: the hook refuses a `build_lib/controller` file set that differs from `get_outputs()`, deleting nothing; CP1 case |
| O4: `tests/fixtures/` beside `tests/fixtures.py` | `tests/golden/` already holds committed fixtures | Accepted: the transcript moves to `tests/golden/` |
| O5: README upgrade/rollback surprises | `SourceSnapshotError(raised_by="detect")` for a missing installed tree (Design, "Generation handoff by kind") | Accepted: the README names both fail-closed cases |

None of these changes adds an exit code, edits `scripts/`, `.claude/commands/` or
`workflow-conformance.yml`, or changes the checkpoint set, ids or dependencies.

### Round 3 (`LOCAL_MODEL_PLAN_REVIEW`, `REVISE`, plan revision 3)

Every finding was checked against the base tree before it was applied. All six are accepted.

| Finding | Evidence checked | Disposition |
|---|---|---|
| I1: a phase-2 timeout or `on_group_drain` failure neither kills the group nor yields `INTERRUPTED` | `controller/worker.py` `_kill_process_group` calls `os.getpgid(pid)` first and returns on `ProcessLookupError`, which a reaped leader always raises; `_classify` returns `INTERRUPTED` only for `returncode is None or < 0` | Accepted: phase 2 uses `_kill_drained_group` (`os.killpg(proc.pid, SIGKILL)` directly, never `getpgid`), then a bounded wait for the group scan to report empty ("reaped" dropped for non-children); a `timed_out` flag checked first in `_classify`; `INTERRUPTED` with the real `exit_code` (`0`) is documented; CP4 descendant-gone and classification tests |
| I2: a planted valid `BUILD_INFO.json` resolves to `package` when `git` is missing | Round 2's I1 fix makes any `OSError` launching `git` mean "not `source`", so the both-match rule never fires | Accepted: the ambiguity rule is Git-free (`BUILD_INFO.json` plus `code_root/.git` gives `unidentified`) and runs before step 2; CP2 test with `PATH` emptied and with `controller/` untracked |
| O1: stale content survives in `build/lib` | setuptools 84.0.0 `Command.copy_file` passes `update=not self.force` (checked on this host) | Accepted: the hook sets `self.force = True` before the copy; CP1 back-dated-file case |
| O2: drain decision under the `killpg` form; stale `remaining_pids` | `worker.process_test` falls back to `_killpg_form`, which answers only `not live`/`possibly live` and names no members | Accepted: `possibly live` keeps waiting, bounded by `--timeout`; `remaining_pids` is `[]` with "unknown" wording; heartbeat and `status` label the count "at drain start" (no rescan in the read-only follower); CP4 test |
| O3: bare `follow` must find `step`'s row-3 root | `resolve_runtime_root` row 3 depends on `runtime_kind` after CP2 | Accepted: `follow` resolves the kind read-only through `resolve_runtime(code_root)`; CP6 test |
| O4: `$GITHUB_SHA` for an annotated tag push | Both comparisons fail closed, but a systematic mismatch would burn a PATCH version | Accepted: `build` peels once to `RELEASE_COMMIT` (a no-op for a commit); CP9 records the documented `$GITHUB_SHA` value and tests the ordering. *Corrected in round 4 (I1):* as written in revision 4, "every comparison uses it" did not hold. `publish`'s wheel re-verify had no `RELEASE_COMMIT` in its environment, and `validate.yml`'s `package` job still compared against the unpeeled `$GITHUB_SHA`. Revision 5 peels in `validate.yml` too and sets `RELEASE_COMMIT` at `publish` job level |

None of these changes adds an exit code, edits `scripts/`, `.claude/commands/` or
`workflow-conformance.yml`, or changes the checkpoint set, ids or dependencies.

### Round 4 (`LOCAL_MODEL_PLAN_REVIEW`, `REVISE`, plan revision 4)

Every finding was checked against the revision-4 plan text and the base tree before it was
applied. All three findings are accepted (I1 in its three parts, O1 and O2).

| Finding | Evidence checked | Disposition |
|---|---|---|
| I1.1: `check-unpublished` has no `GH_TOKEN` | Revision 4's `release.yml` gave `GH_TOKEN` only to the `gh release create` step. `check-unpublished` shells out to `gh release view`, and the plan's own rule treats any failure other than "release not found" as undecidable. `gh` in GitHub Actions authenticates only through `GH_TOKEN`/`GITHUB_TOKEN` in its environment, so `publish` would refuse every run | Accepted: `GH_TOKEN: ${{ github.token }}` at `publish` **job** level, covering `check-unpublished`, `gh release create` and the draft form's `gh release edit`; CP9 effective-environment test for every `gh`/`check-unpublished` step |
| I1.2: `publish`'s wheel re-verify has no `RELEASE_COMMIT` | Revision 4 put `RELEASE_COMMIT` only in the `verify-tag-commit` step's `env`. The re-verify step needs `--commit`, and CP9 forbids `$GITHUB_SHA` after the peel, so `--commit "$RELEASE_COMMIT"` would expand empty and `verify-wheel` would refuse | Accepted: `RELEASE_COMMIT: ${{ needs.build.outputs.release_commit }}` at `publish` job level, and the re-verify is spelled out with `--tag` and `--commit "$RELEASE_COMMIT"`; CP9 effective-environment test for every `$RELEASE_COMMIT` step |
| I1.3: `validate.yml`'s `package` job compares with the unpeeled `$GITHUB_SHA` | Revision 4's `package` row passed `--commit "$GITHUB_SHA"`, and `release.yml` calls `validate.yml` as its gating job; CP9's "no `$GITHUB_SHA`" check scanned only `release.yml` | Accepted: `package` peels with `git rev-parse "$GITHUB_SHA^{commit}"` (a no-op for `pull_request`/`push: main`); CP9 test that no rendered workflow passes a bare `"$GITHUB_SHA"` as `--commit`; round 3's O4 row corrected above |
| O1: round-3 wording left over | "the whole group gets `SIGKILL` and is reaped" contradicted round 3's "reaped" removal; `_kill_drained_group(pgid)` was described as calling `os.killpg(proc.pid, ...)`; a `/proc`-form member in `D` state can also outlast the settle bound | Accepted: "through `_kill_drained_group`"; the helper takes `pgid`, called with `pgid=proc.pid`; the settle-bound parenthesis names both forms as examples |
| O2: Ctrl-C during the drain says the exited worker "keeps running" | `controller/job.py` `_announce_orphaned_worker` prints "the worker (pid P, process group G) keeps running", and `execute_step`'s `KeyboardInterrupt` handler calls it whenever `spawned["flushed"]` | Accepted: a drain-aware variant selected by a `spawned["drained"]` flag the `on_group_drain` callback sets ("Discovery and hints"); CP6 file item and test |

None of these changes adds an exit code, edits `scripts/`, `.claude/commands/` or
`workflow-conformance.yml`, or changes the checkpoint set, ids or dependencies.

### Round 5 (`LOCAL_MODEL_PLAN_REVIEW`, `REVISE`, plan revision 5)

Every finding was checked against the revision-5 plan text before it was applied. All three are
accepted.

| Finding | Evidence checked | Disposition |
|---|---|---|
| I1: `publish` downloads the artifact into the workspace root, but its steps read `dist/` | Revision 5 had a bare "`actions/upload-artifact` of `dist/`" in `build` and a bare `actions/download-artifact` in `publish`, whose `verify-wheel dist/*.whl` and `gh release create ... dist/*.whl dist/SHA256SUMS` glob `dist/`. Per the actions' READMEs, a single-directory upload `path` is the artifact root and a single named download extracts directly into `path`, default `$GITHUB_WORKSPACE`, so `dist/` would not exist and `verify-wheel` would refuse on every run. CP9's only artifact check named no `name`/`path` | Accepted: `name: dist`, upload `path: dist/`, download `path: dist`; `publish`'s `dist/` globs unchanged; CP9 artifact-contract test (same `name`; upload `path` equals the `pip wheel -w` and `checksums` directory; download `path` equals every later glob's directory prefix) |
| O1: `release_commit` job output wiring | Revision 5 said the peel is written "to `$GITHUB_ENV` and exported as the job output", naming no mechanism. A job output must be an expression; `${{ steps.<id>.outputs.<name> }}` fed by `$GITHUB_OUTPUT` is the documented form, and an empty output would make `publish` refuse | Accepted: peel step `id: peel` writes both `$GITHUB_ENV` and `$GITHUB_OUTPUT`; `outputs.release_commit: ${{ steps.peel.outputs.release_commit }}`; CP9 asserts the binding and the `$GITHUB_OUTPUT` write, and that no job output references `env.` |
| O2: `checksums` self-inclusion | Revision 5's `checksums DIR` hashed "every file in `DIR`", so a local rerun would list the previous `SHA256SUMS`, and `sha256sum -c` would fail on that line | Accepted: `SHA256SUMS` is excluded by name; CP8 rerun test |

None of these changes adds an exit code, edits `scripts/`, `.claude/commands/` or
`workflow-conformance.yml`, or changes the checkpoint set, ids or dependencies.

## Open decisions (`docs/TECHNICAL_DECISIONS.md`)

`docs/TECHNICAL_DECISIONS.md` does not exist in this repository, and neither do
`docs/DOMAIN_GLOSSARY.md` or `docs/UX_FLOWS.md`, so this plan finalizes no open-decision row.
The choices a reviewer should confirm explicitly are listed under "Scope judgments".

## Verification

Narrowest subsets, per checkpoint:

| Checkpoint | Modules |
|---|---|
| CP1 | `tests.test_buildinfo tests.test_package_structure tests.test_cli`, then the full suite |
| CP2 | `tests.test_identity tests.test_runtime tests.test_handoff tests.test_cli tests.test_job tests.test_job_validation tests.test_resume tests.test_package_structure` |
| CP3 | `tests.test_packaged_runtime` (with `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`) |
| CP4 | `tests.test_worker tests.test_job tests.test_resume tests.test_lifecycle_orchestration` |
| CP5 | `tests.test_job tests.test_cli tests.test_resume tests.test_observe tests.test_write_containment` |
| CP6 | `tests.test_observe tests.test_cli tests.test_package_structure` |
| CP7 | `tests.test_observation_equivalence tests.test_lifecycle_orchestration tests.test_lock tests.test_routing tests.test_resume tests.test_golden_plan_stage_decisions` |
| CP8 | `tests.test_release_tools tests.test_buildinfo` |
| CP9 | `tests.test_ci_workflows`, `python3 tools/ci_workflows.py --check` |
| CP10 | `tests.test_plan_document_consistency tests.test_checklist_corrections` |
| CP11 | the full run and the drills |

## Migration / data-integrity notes

- **No target-repository state changes shape.** The Controller still never writes
  `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` or any file in a target.
- **Job records stay `schema_version: 1` and grow additively:**
  - `controller_runtime`;
  - `worker_streams`;
  - `run_id`;
  - the `worker` block's paths now point at files the worker wrote itself.

  `validate_record` reads none of these. Records from earlier versions have no
  `controller_runtime`/`worker_streams`/`run_id`. They validate, reconcile and abandon exactly as
  before. `follow` replays their single-JSON `worker.stdout` as a result line.
- **`identity.json`, `SOURCE_PIN.json` and `handoff.json` grow additively.** A pin without
  `runtime_kind` reads as `source`.
- **Job records also gain `event_seq`** (additive, not read by `validate_record`). A record without
  it starts its sequence at `1`.
- **Job records also gain `worker_group_drain`,** only when a group drain was non-empty
  (additive; not read by `validate_record` or reconciliation). A `COMPLETED` record may now
  carry `worker_outcome: INTERRUPTED` with `worker.exit_code: 0` (a phase-2 timeout);
  `validate_record` already accepts any `exit_code` with `INTERRUPTED`.
- **A checkout carrying a stray `controller/BUILD_INFO.json`** is `unidentified` after this
  milestone. It could not have come from this project's build, which writes only under
  `build_lib`. Acting commands from it refuse with exit `20` and name the file to delete.
- **New runtime-root content:** `runs/` and `jobs/<job_id>/events.jsonl`. `resume` and the
  pending-reconciliation scan never read them. Deleting them loses presentation history only.
- **Runtime-root location.**
  - Unchanged for source runtimes.
  - Unchanged for an ordinary pipx install, which was already row 4.
  - A wheel installed into a venv inside a Git checkout moves from the misfiring
    `site-packages/.controller` to row 4. Every acting command from such an install failed at
    `git archive` before this milestone, so no job history lives there. At most, an `identity.json`
    from a read-only command remains. The README notes the old path, which can be deleted.
- **Version.** It moves from a static `1.0.1` in `pyproject.toml` to a dynamic `1.1.0` from
  `controller/version.py`.
- **CI.** `controller-tests.yml` is replaced by `ci.yml` plus `validate.yml`.
  `workflow-conformance.yml` is untouched. A branch-protection rule that names the old
  `controller-tests` check must be updated to the new `validate / controller (...)` checks. The
  README says so.
- **Rollback of this milestone.** Reinstalling a `1.0.x` Controller reads job records that carry
  the new fields and ignores them. It cannot launch workers from a wheel, which is the pre-existing
  limitation.
