# C10g-2a: the Codex build digest, the verified-build record and is_verified (Revision 7)

Work item: `workflow-controller-verify-codex`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `6ded1d8e6646473578b03c2ae8310dbe72b45458` ("chore: move this repository to Workflow 2.23.0 (#216)"), the tip of `main`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row C10g-2 (renamed C10g-2a by this item), second slice of the Codex tester ([#131](https://github.com/RodrigoFAbreu/workflow-controller/issues/131)). Issue [#214](https://github.com/RodrigoFAbreu/workflow-controller/issues/214).
Released baseline: `workflow-controller 1.24.0`. Nothing operator-visible is added (no command, setting, option, exit code or output; the new module has no caller outside tests), so the squash commit is a `chore:` and makes no release.
Pull request title: `chore: Codex build digest and verified-build record (C10g-2a, #214)`

**Scope change (owner decision 2026-10-28).** Revisions 1 to 6 planned the whole of C10g-2 and did not converge in six local review rounds. C10g-2 is split. This revision plans only C10g-2a: the build digest, the record and `is_verified`, offline. Moved to C10g-2b ([#217](https://github.com/RodrigoFAbreu/workflow-controller/issues/217)): the `verify-codex` command, the probes and `verify_build`, the shared live harness, the host listeners and their socket, the host-side git checks, the operator's Codex home and the run folder (revision 6's D4 to D8 and CP2/CP3). The digest design of revision 6 (D1) is kept, with the review findings it had not yet answered (I6, O7, O8 of round 6) settled below.

## Goal

C10g-3 will refuse to launch a Codex tester on a build nobody verified. That needs two things this item builds: a digest that names exactly the Codex build a launch would run, and a record of the digests that were verified, with a helper that answers "was this build verified?" without running anything. C10g-2b's `verify-codex` will write the record; this item only provides the record's reader and writer. Nothing calls `is_verified` outside tests, no launch changes, the Codex reviewer (`reviewer.harness=codex`, pinned to 0.163.0 with its own checks) never consults the record, and `verify-claude` and its record are untouched.

## What exists

- `controller/claude_build.py` is the model for the record: `RECORD_NAME`, `read_verified`, `record_verified`, `file_digest`, over `runtime.read_owned_file`, `runtime.write_private_json_nofollow` and `runtime.builds_lock`.
- `review.resolve_binary` returns the launcher path with its symlink kept, because that directory holds the `node` an npm `#!/usr/bin/env node` launcher needs; `review.child_environment` builds the launch `PATH` from it (the launcher's directory, an absolute `#!` interpreter's directory, then `review.SYSTEM_PATH`) and passes exactly `review.ENV_ALLOWLIST`. `codex_sandbox.package_root` finds a resolved entry point's package root.
- An npm install is not one file. On this machine `codex` is `~/.nvm/versions/node/v24.14.0/bin/codex`, a symlink to `lib/node_modules/@openai/codex/bin/codex.js`; that launcher calls `createRequire(import.meta.url).resolve("@openai/codex-linux-x64/package.json")` (falling back to its own package's `vendor/`) and spawns the native `vendor/<triple>/bin/codex` it finds there. The package lists the platform packages under `optionalDependencies` and has no `dependencies`.

## Decisions

Each decision states one principle.

- **D1. A build is identified by every file the launch can run as Codex, found the way Node finds it in the launch environment.** `codex_build.build_digest(binary, *, system_path=review.SYSTEM_PATH)` takes the launcher path `review.resolve_binary` returns. It resolves that path once (`os.path.realpath`) to the entry point and reads only under what that resolution found; the launch keeps executing the unresolved launcher path, so the digest and the run describe the same install and a re-pointed symlink changes the digest the next time it is computed. It returns a SHA-256 over a canonical listing of these roots, or `None` when the layout cannot be bound:
  - **The entry point inside a package** (`codex_sandbox.package_root` found a `package.json` below the nearest enclosing `node_modules`, the C10g-1 rule that never reaches a stray `package.json` higher up): every file under the package root; and, for each name in that `package.json`'s `optionalDependencies`, the dependency's package root, listed under a `dep:<name>/` prefix. A `package.json` that declares `dependencies` or `peerDependencies` gives `None`, because the launcher's tree is then larger than what is hashed.
  - **Otherwise**: the entry point alone. A `#!` script outside a package gives `None`, because what it runs is not known (a package manager's shell shim, for example).
  - **Where a dependency is found** is Node's CommonJS search from the entry point's directory, the one `createRequire` uses: `<dir>/node_modules/<name>` for that directory and each ancestor, nearest first, skipping a directory itself named `node_modules` (Node never searches `…/node_modules/node_modules`); then Node's global folder `<prefix>/lib/node/<name>`, where `<prefix>` is the parent of the directory of the resolved interpreter. The interpreter is an absolute `#!` line's path, or, for `#!/usr/bin/env <name>`, the first `<name>` in the launcher's directory and then in `system_path`, the `PATH` order `review.child_environment` builds; an `env` option (a word starting with `-`) or an interpreter found nowhere gives `None`; an entry point that is not a `#!` script has no interpreter, and the global folder is not searched. A candidate counts when `<candidate>/package.json` is a file, as `require.resolve("<name>/package.json")` requires. Node's other global folders cannot apply to a launch: `NODE_PATH` is not in `review.ENV_ALLOWLIST`, and `$HOME/.node_modules` and `$HOME/.node_libraries` are under the job's stand-in home, which the Controller creates for each launch. A dependency found nowhere is skipped (another platform's package is normally absent); the launcher's fallback, its own `vendor/`, is inside the package root and already hashed.
  - **A found dependency root that is a symlink** (a pnpm layout, where `node_modules/<name>` points into `.pnpm/`) is followed to its real path, as Node does, and hashed from there under `dep:<name>/`; where that symlink sits inside the package root, the package root's listing records it by its target text and does not refuse it.
  - **The listing.** A fixed version prefix, then one line per regular file or symlink of each root, in sorted order: its path relative to its root (with the `dep:<name>/` prefix for a dependency), its executable bit and its content's SHA-256; a symlink is listed by its target text and, unless it is a found dependency root, gives `None` when it resolves outside its own root. An unreadable file or directory, a special file (FIFO, socket, device) or more than 20000 entries in all gives `None`; nothing is truncated. Absolute paths are not listed, so the same install copied elsewhere has the same digest.
  - **The interpreter is outside the digest** unless it lies inside a hashed root. It is host software, like `bwrap` and `/usr`: it only runs the launcher, which is hashed, and the boundary is enforced by the native executable, which is hashed; an operator's Node update does not un-verify every build. Which dependency the interpreter's global folder supplies is part of the identity, because the package found there is hashed.
  - The digest is computed from disk on every call and never cached.
- **D2. The record is a second file with the same trust boundary as the first, and `verify-claude`'s code does not change.** `<runtime root>/verified-codex-builds.json` holds `{"schema_version": 1, "builds": {<sha256>: {"version", "verified_at", "controller_version", "probes"}}}`. `codex_build.py` carries its own `RECORD_NAME`, `read_verified(runtime_root)` and `record_verified(runtime_root, digest, entry)` over the shared `runtime` primitives, with `claude_build`'s rules: one `O_NOFOLLOW` descriptor, a regular file owned by the effective user with no group or world write bit, created `0600`, never written through a symlink; a missing record is empty with no reason, and an unreadable, symlinked, foreign-owned, group- or world-writable, malformed or wrong-schema record is empty with the reason it was ignored. `record_verified` refuses a value that is not a SHA-256 digest and merges under the lock; the entry's fields are the caller's (C10g-2b). The one change outside the new module is additive: `runtime.builds_lock(runtime_root, name=BUILDS_LOCK_NAME)`, which the Codex record calls with `verified-codex-builds.lock`; the default keeps `claude_build` exactly as it behaves. The settings file never holds a digest and is never read or written. `claude_build` is not generalised because its reader and writer name their file as a module constant that its tests assert, so a shared helper would edit what this item promises not to touch.
- **D3. `is_verified` reads; it never verifies.** `codex_build.is_verified(runtime_root, binary)` returns `Verified(ok, digest, reason)`: it computes `build_digest(binary)` (D1) and `ok` is true only when that digest is in a trustworthy record (D2). It writes nothing, starts no process and spends no model turn. When `ok` is false the reason says which case it is: the build could not be bound (no digest), there is no record, the record was ignored (with D2's reason), or this digest is not recorded. C10g-3 will call it at launch and refuse an unverified build with the remedy `workflow-controller verify-codex` (C10g-2b); in this item nothing outside tests calls it, and `HarnessAdapter.build_verification` for `codex` stays `None`.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The Codex build digest | - | 2 | 1 |
| CP2 | The verified-build record, is_verified, the guide and the roadmap | CP1 | 2 | 1 |

<!-- CP1 -->
### CP1 -- The Codex build digest (#214)

**Files:** `controller/codex_build.py`, `controller/__init__.py`, `tests/test_codex_build.py`, `tests/test_package_structure.py`

D1. A new module `controller/codex_build.py` with `build_digest`; the module is listed in `controller/__init__.py` and in the package-structure test. Tests build fake install trees in a temporary folder and pass `system_path` pointing into it; no `node` or `codex` is run.

Done when `BuildDigestTests` in `tests/test_codex_build.py` show, with fake trees:
- **npm global layout** (launcher symlink in `<prefix>/bin`, interpreter only on `system_path`, the platform package nested in the package's `node_modules`): the digest changes when the nested native executable, the launcher, an executable bit or the set of files changes, and is identical for two copies of the tree at different paths;
- **nvm layout** (interpreter beside the launcher symlink) with the platform package hoisted to an enclosing `node_modules` outside the package root: found by the ancestor search, and the digest changes when that native executable changes; with the same package in both places, the nearer one is hashed;
- **a global-folder layout** (the platform package only in `<prefix>/lib/node/<name>` of the resolved interpreter, reached through an interpreter symlink): found, and the digest changes when that native executable changes;
- **a pnpm layout** (the dependency root a symlink into `.pnpm/`, once beside the package and once inside its own `node_modules`): followed, not refused, and hashed from its real path; the digest changes when the real native executable changes;
- **a stray `package.json`**: one above the nearest `node_modules`, or beside a non-npm single file with no `node_modules` above it, is never used; and the search never looks in a `…/node_modules/node_modules` directory;
- **the interpreter**: changing an interpreter outside every root leaves the digest unchanged; an interpreter inside the package root is hashed with it;
- **refusals** (`None`): a `#!` script outside a package, a `dependencies` entry, an `env` option, an interpreter found nowhere, a symlink leaving its root, an unreadable file, a special file and more than 20000 entries; a listed dependency found nowhere is skipped, not refused;
- a non-script single file hashes that file.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The verified-build record, `is_verified`, the guide and the roadmap (#214)

**Files:** `controller/codex_build.py`, `controller/runtime.py`, `tests/test_codex_build.py`, `docs/guide/harnesses.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D2, D3. `RECORD_NAME`, `read_verified`, `record_verified` and `is_verified` in `controller/codex_build.py`; the lock-name parameter of `runtime.builds_lock`. Documentation, in place:
- `docs/guide/harnesses.md`, in "The Codex tester sandbox": the build digest (what it covers, that the interpreter is outside it) and the record `verified-codex-builds.json`, which nothing writes until `verify-codex` (C10g-2b) and no launch reads until C10g-3. The "until `verify-codex` ships" sentences stay true and stay. `docs/guide/runtime.md` gains the file in C10g-2b, when something writes it.
- `docs/ROADMAP.md`: the C10g-2 row becomes C10g-2a, linking #214 and saying what is built; a new C10g-2b row (`verify-codex`, the command and its probes, verify-on-first-use cached by the digest; depends on C10g-2a; [#217](https://github.com/RodrigoFAbreu/workflow-controller/issues/217)); C10g-3 and H4b depend on C10g-2b in place of C10g-2 (C10g-3 on both halves); "Next, in order" item 2 names C10g-2a done and C10g-2b next. The completed H7 and RB3 rows are historical and are left as written.
- `docs/ACTIVE_MILESTONE.md` "Next action": `/milestone-plan` for C10g-2b (#217) once this item is merged.

Done when `RecordTests` and `IsVerifiedTests` in `tests/test_codex_build.py` show: a recorded build is verified and the same build edited afterwards is not (the reason says the digest is not recorded); a build that cannot be bound is not verified and says so; a missing record is empty with no reason; a symlinked, foreign-owned, group-writable, malformed or wrong-schema record admits nothing and says it was ignored; `record_verified` merges two entries under the lock, refuses a non-digest, refuses to write through a symlink and leaves its target untouched, and creates the file `0600`; `is_verified` writes nothing in the runtime root; no test touches `verified-claude-builds.json` or its lock; and `tests/test_claude_build.py` passes unchanged. `python3 tools/check_docs.py` passes, and the suite is green but for the known Git 2.56 trailer tests.
<!-- /CP2 -->

## Requirements

| id | requirement | checkpoints |
|---|---|---|
| R1 | A Codex build's digest covers every file the launch can run as Codex: the launcher's package and each dependency package found where Node's resolution in the launch environment finds it; a layout it cannot bind gives no digest; it is recomputed from disk on every use (#214) | CP1 |
| R2 | The record of verified Codex builds is `verified-codex-builds.json` in the runtime root with `claude_build`'s schema and trust rules and its own lock, never in the settings file, and `verify-claude`, its record and its tests are unchanged (#214) | CP2 |
| R3 | `is_verified` answers from the record and a fresh digest alone, writes nothing, starts no process, says why when it answers no, and has no caller outside tests (#214) | CP2 |
| R4 | The roadmap and the harnesses guide describe the split and what is built; `check_docs` and the suite are green but for the known tests (#214) | CP2 |

## Functional review

Every flow is offline and unit-level, runnable in the tester's sandbox: the named test classes above, run one module at a time. There is no live flow in this item, and the full suite is not a flow.

## Risks

- **Hashing a whole package.** The installed package is about 400 MB; the digest reads it once per call (a second or two). A tree over the cap is refused, not truncated.
- **A layout this rule does not know** (a new package manager, a launcher that resolves differently) gives `None` or hashes a tree that is not the one run. The first fails closed; the second is bounded by the platform package being the one `codex.js` names, and C10g-2b's probes then run the configuration they authorise.
- **The window between the check and the launch.** A build replaced after `is_verified` and before exec runs unverified; the principal the record defends against is the sandboxed tester, which cannot write any install tree, as for `verify-claude`.
- **Out of scope:** everything moved to C10g-2b (above); declaring the tester role, the launch wiring, `check_effective` and the fallback (C10g-3); the C10g-1 hardening notes on #174; any change to the Codex reviewer's pin or checks.
