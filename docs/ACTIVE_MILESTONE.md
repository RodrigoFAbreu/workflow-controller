# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-verify-codex` (C10g-2a, issue
[#214](https://github.com/RodrigoFAbreu/workflow-controller/issues/214)) is implemented (CP1, CP2 and the review-fix
round). `controller/codex_build.py` computes a SHA-256 build digest of a Codex install, keeps the verified-build record
`verified-codex-builds.json` and answers `is_verified` from the record and a fresh digest. Nothing calls it outside
tests; no command, setting, option or output changes. The change is `chore:` with no release. The plan is
`docs/ai-workflow/CONTROLLER_VERIFY_CODEX_PLAN.md`.

## Next action

Run the functional review below, then `/accept-milestone`. After the merge, `/milestone-plan` for C10g-2b (`verify-codex`, [#217](https://github.com/RodrigoFAbreu/workflow-controller/issues/217)).

## Functional review checklist

Work item `workflow-controller-verify-codex` (C10g-2a, #214). The change adds one module, `controller/codex_build.py`
(plus a lock-name parameter on `runtime.builds_lock`) that nothing calls yet, so every flow is offline and uses no forge,
`claude`, `codex` or `ssh`.

**Tester limits.** The tester runs offline: no network, no host socket, `gh`/`claude`/`codex`/`ssh` are refusing
stand-ins, `PATH` is the sandbox `bin/` plus system directories, and the clone is read-only. Write only to the output
folder, the scratch `HOME`, `XDG_*` and `TMPDIR`. Run Python with `PYTHONDONTWRITEBYTECODE=1` and `python3 -B` so no
`__pycache__` lands in the clone. There is no live flow in this item.

**Setup.** Check out `milestone/workflow-controller-verify-codex`. No build, flag or data is needed.

**Flows (each from the repository root).**

1. F1, module tests. `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_codex_build tests.test_package_structure tests.test_claude_build`.
   Expected: 79 tests, `OK` (`tests.test_codex_build` alone is 23).
2. F2, digest on a fake tree. In a scratch dir under `TMPDIR`, build an npm-style tree: `lib/node_modules/@openai/codex/`
   with `package.json` (`optionalDependencies` naming `@openai/codex-linux-x64`), `bin/codex.js` starting `#!/usr/bin/env node`,
   a nested `node_modules/@openai/codex-linux-x64/{package.json,vendor/bin/codex}`, a `bin/codex` symlink to the launcher,
   and an executable `node` beside it. Call `codex_build.build_digest(Path(".../bin/codex"), system_path=...)` (PYTHONPATH the
   clone). Expected: a 64-hex digest; the same value for a copy of the tree at another path; a different value after
   editing the native `codex` file, the launcher or an executable bit; the interpreter `node` changing leaves it unchanged.
3. F3, refusals. On variants of F2's tree: add a `dependencies` entry to `package.json`; replace the launcher with a bare
   `#!` script outside any package; use `#!/usr/bin/env -S node`; add a symlink inside the package pointing outside it.
   Expected: `build_digest` returns `None` for each; a missing platform package alone is skipped and still gives a digest.
4. F4, record round-trip. With a scratch runtime root, `record_verified(root, digest, {"version": "x", "verified_at": "t",
   "controller_version": "c", "probes": {}})`, then `read_verified(root)` and `is_verified(root, binary)`. Expected:
   the file is `verified-codex-builds.json`, mode `0600`; `read_verified` returns the entry with no reason;
   `is_verified(...).ok` is true; after editing the native file it is false with a "not recorded" reason. `record_verified`
   with a non-SHA-256 string raises; two entries recorded in turn both remain.
5. F5, record trust. In the scratch runtime root make the record a symlink, then group-writable (`chmod 664`), then
   malformed JSON, then `schema_version: 2`. Expected: `read_verified` returns no builds and a reason each time;
   `is_verified(...).ok` is false and says the record was ignored; `record_verified` never writes through the symlink
   (its target is unchanged). `is_verified` creates nothing in the runtime root.
6. F6, Claude record untouched. `git diff 6ded1d8e..HEAD -- controller/claude_build.py tests/test_claude_build.py`.
   Expected: empty. `git diff 6ded1d8e..HEAD -- controller/runtime.py` shows only the `builds_lock(runtime_root, name=...)`
   change with the default unchanged.
7. F7, nothing wired. `grep -rn codex_build controller/ | grep -v "^controller/codex_build.py"`. Expected: only the
   `controller/__init__.py` listing; no launch path imports it.
8. F8, docs. Read the new paragraph in `docs/guide/harnesses.md` ("The Codex tester sandbox") and the C10g-2a/2b rows in
   `docs/ROADMAP.md`; run `python3 -B tools/check_docs.py`. Expected: the paragraph names the digest, the interpreter
   exclusion, the record and that nothing writes it until `verify-codex` (C10g-2b); the roadmap splits C10g-2a (done) and
   C10g-2b (#217); `check_docs` passes.

**Known limits / out of scope.** Nothing writes or consults the record outside tests: `verify-codex`, its probes and the
digest cache are C10g-2b (#217); declaring the tester role and the launch wiring are C10g-3. The interpreter (`node`) is
outside the digest unless it lies inside a hashed root. A layout the rule does not know gives no digest. The digest reads
the whole package (about 400 MB for a real install), so it takes a second or two. The change is `chore:` with no release.
