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

**In implementation.** `workflow-controller-composed-config-check` (C10g-3a, issue
[#221](https://github.com/RodrigoFAbreu/workflow-controller/issues/221)): CP1 (`check_effective`) and CP2 (`check_composed`,
`KNOWN_LIMITS` narrowed to `composition_window`, guide and roadmap) and the self-review fixes (an empty table or
list below a compared key is a set leaf, as in the reviewer's check) are implemented. Nothing calls the check until
C10g-3b.
The plan is `docs/ai-workflow/CONTROLLER_COMPOSED_CONFIGURATION_CHECK_PLAN.md`.

## Next action

`/milestone-plan` for C10g-3b (the Codex functional-tester launch, [#222](https://github.com/RodrigoFAbreu/workflow-controller/issues/222)).

## Functional review checklist

Work item `workflow-controller-composed-config-check` (C10g-3a, #221), implementation revision 2. The change adds
`check_effective`, `effective_policy`, `compose_argv`, `check_compose_argv` and `check_composed` to
`controller/codex_sandbox.py`, narrows `KNOWN_LIMITS` to `composition_window`, and records the measurement in
`tests/harness_contract/codex/tester_compose.txt`. Nothing calls the check: no role is declared, `check_launch` and
`verify-codex` are unchanged (C10g-3b wires it).

**Tester limits.** The tester runs offline: no network, no host socket, `gh`/`claude`/`codex`/`ssh` are refusing
stand-ins, `PATH` is the sandbox `bin/` plus system directories, and the clone is read-only. Write only to the output
folder, the scratch `HOME`, `XDG_*` and `TMPDIR`; run Python with `PYTHONDONTWRITEBYTECODE=1` and `python3 -B`, with
`TMPDIR` set to the scratch area (the tests create temporary folders). Every flow below is offline except F6, which
needs the real `codex`, `bwrap` and a Codex login: the tester marks it `environment` and the operator runs it.

**Setup.** Check out `milestone/workflow-controller-composed-config-check`. No build, flag or data is needed.

**Flows (each from the repository root).**

1. F1, sandbox tests. `python3 -B -m unittest tests.test_codex_sandbox`. Expected: 62 tests, `OK`, including
   `CheckEffectiveTests` (a composed result from the tester's own profile passes; each changed security-relevant leaf,
   foreign origin, managed or unknown layer and enabled project layer is refused naming the leaf, value and origin),
   `CheckComposedTests` (clean answer passes, `check_home` runs after the compose and before the comparison, a failed,
   timed-out, empty or error answer is a refusal with `check="effective"`) and the `KNOWN_LIMITS` tests
   (`composition_window` is the only entry).
2. F2, neighbours unchanged. `python3 -B -m unittest tests.test_codex_verify tests.test_codex_verify_command
   tests.test_harness_table tests.test_fake_codex_contract`. Expected: 26 + 14 + 19 + 20 tests, `OK`.
3. F3, reviewer and launch paths untouched. `git diff 2faaf53..HEAD --stat -- controller/review.py controller/codex_verify.py
   controller/cli.py controller/harness.py`. Expected: empty.
4. F4, nothing calls the check. `grep -rn "check_composed\|check_effective" controller/ | grep -v "controller/review.py"`.
   Expected: hits only in `controller/codex_sandbox.py` (definitions, docstrings), none in a launch path.
5. F5, docs. Read the composed-configuration paragraph of `docs/guide/harnesses.md`, the C10g-3a row and item 2 of
   `docs/ROADMAP.md`, and `tests/harness_contract/codex/README.md` (the `tester_compose.txt` row); run
   `python3 -B tools/check_docs.py`. Expected: exit 0; the check, its separation from `check_launch`, "no launch path
   calls it yet" and the one remaining limit `composition_window` are described consistently; the roadmap marks C10g-3a
   complete and C10g-3b next.
6. F6, live flow (environment-limited; operator-run). `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -m unittest
   tests.test_codex_sandbox_live -v`. Offline expected: 3 tests, skipped with a reason. Operator run expected: a real
   tester job passes `check_composed`, and with one `-c` item replaced it refuses naming that value and its
   `sessionFlags` origin. No model request is made by the new test.

**Known limits / out of scope.** The tester launch, role declaration, routing and fallback are C10g-3b and C10g-3c; the
Codex worker is H4a-H4g. `composition_window` remains: the configuration is read by `app-server` just before the launch,
so a layer that changes in that window, or a setting only `exec` composes differently, is not seen (the reviewer's control
makes the same assumption). The full suite has two known Git 2.56 trailer failures unrelated to this change. The change is
a `chore:` with no release.
