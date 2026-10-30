# Active Milestone

## Status

**Complete.** `workflow-controller-verify-codex-command` (C10g-2b, issue
[#217](https://github.com/RodrigoFAbreu/workflow-controller/issues/217)) is implemented (CP1, CP2, the
self-review fixes and the functional review).
`controller/codex_verify.py` runs the C10g-1 probes (modelless `sandbox`, then one `exec` turn) in fresh jobs against
host fixtures; `workflow-controller verify-codex [--force] [--codex-binary PATH]` records the build's digest in
`verified-codex-builds.json` only when every probe held and the build was unchanged, and otherwise exits 20 with a named
refusal. Nothing launches on the record: `build_verification` for `codex` stays `none` until C10g-3. The change is a
`feat:` with a minor release. The plan is `docs/ai-workflow/CONTROLLER_VERIFY_CODEX_COMMAND_PLAN.md`.

## Next action

`/milestone-plan` for C10g-3a (the Codex tester's composed-configuration check, [#221](https://github.com/RodrigoFAbreu/workflow-controller/issues/221)).

## Functional review checklist

Work item `workflow-controller-verify-codex-command` (C10g-2b, #217). The change adds `controller/codex_verify.py` (the
probe run) and the `workflow-controller verify-codex [--force] [--codex-binary PATH]` command, with guide, exit-code and
roadmap text. Nothing launches on the record: `build_verification` for `codex` stays `none` until C10g-3.

**Tester limits.** The tester runs offline: no network, no host socket, `gh`/`claude`/`codex`/`ssh` are refusing
stand-ins, `PATH` is the sandbox `bin/` plus system directories, and the clone is read-only. Write only to the output
folder, the scratch `HOME`, `XDG_*` and `TMPDIR`. Run Python with `PYTHONDONTWRITEBYTECODE=1` and `python3 -B` so no
`__pycache__` lands in the clone. Every flow below is offline; the one live flow (F7) needs the real `codex`, `bwrap`
and Codex login, so the tester marks it `environment` and the operator runs it.

**Setup.** Check out `milestone/workflow-controller-verify-codex-command`. No build, flag or data is needed.

**Flows (each from the repository root).**

1. F1, probe-run tests. `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_codex_verify`.
   Expected: 26 tests, `OK` (a clean fake run passes both probes; each refusal names its probe; a changed or re-pointed
   build is `build_changed`; credentials are copied back and no credential copy remains).
2. F2, command tests. `python3 -B -m unittest tests.test_codex_verify_command`. Expected: 14 tests, `OK` (first run records,
   second says "already verified" and runs no probe, `--force` probes again, each refusal exits 20 and records nothing,
   the settings file is never created).
3. F3, wiring and tables. `python3 -B -m unittest tests.test_harness_table tests.test_package_structure tests.test_claude_build`.
   Expected: `OK` (19 + 13 tests plus the Claude record tests, unchanged).
4. F4, help and listing. `PYTHONPATH=. python3 -B -m controller verify-codex --help`. Expected: usage
   `verify-codex [-h] [--force] [--codex-binary PATH]`; the text states the cost (one short model turn on your Codex
   credentials, usage budget not consulted), that the record is in the runtime root and not the settings file, that
   nothing launches on the record yet, and exit codes 0 and 20.
5. F5, refusal without `codex`. With a scratch `TMPDIR` as runtime root and `PATH` limited to the sandbox `bin/` (the stand-in
   `codex` refuses): `PYTHONPATH=. python3 -B -m controller --runtime-dir $TMPDIR/rt verify-codex; echo $?`.
   Expected: exit 20 with a named `host` or `identity` refusal and no traceback; `$TMPDIR/rt` contains no
   `verified-codex-builds.json`. With `--codex-binary /nonexistent`: exit 20, `host:` refusal.
6. F6, unchanged neighbours. `git diff 70bb217e..HEAD --stat -- controller/claude_build.py controller/codex_build.py controller/codex_sandbox.py controller/review.py`.
   Expected: empty. `git diff 70bb217e..HEAD -- controller/harness.py` shows only a comment change (C10g-3, not
   `verify-codex`, changes `build_verification` for `codex`; the value stays `None`).
7. F7, live flow (environment-limited; operator-run). `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -m unittest tests.test_verify_codex_live -v`.
   Offline expected: 1 test, skipped with a reason. Operator run expected: both probes `pass`, the digest recorded, a second
   run prints "already verified", and the settings file is never created. Spends one short model turn.
8. F8, docs. Read the `verify-codex` section of `docs/guide/commands.md` and its row, `verified-codex-builds.json` in
   `docs/guide/runtime.md`, the notes in `docs/guide/harnesses.md` and `docs/guide/workers.md`, the exit-20 line of
   `docs/exit-codes.md` and the C10g-2b row of `docs/ROADMAP.md`; run `python3 -B tools/check_docs.py`. Expected: the
   command, its two probes, its cost, its refusal names and "nothing launches on the record until C10g-3" are described
   consistently; the roadmap marks C10g-2b complete; `check_docs` exits 0.

**Known limits / out of scope.** Nothing launches on the record: declaring the tester role, the launch wiring and the
`build_verification` value for `codex` are C10g-3; the Codex worker is H4a-H4g. The probes are C10g-1's and the digest and
record are C10g-2a's, unchanged. A build file rewritten in place between the hash and the exec is not caught. The
window between verification and launch is not defended. The full suite has two known Git 2.56 trailer failures unrelated
to this change. The change is a `feat:` with a minor release.
