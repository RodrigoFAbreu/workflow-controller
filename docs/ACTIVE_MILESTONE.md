# Active Milestone

## Status

**Complete.** `workflow-controller-codex-tester-sandbox` (C10g-1, issue
[#210](https://github.com/RodrigoFAbreu/workflow-controller/issues/210)) is implemented (CP1 to CP3). `controller/codex_sandbox.py`
builds the Codex tester's restricted-read permissions profile, home, environment and `codex exec` argv, with its own
pre-launch controls, and opt-in live probes on `codex-cli 0.163.0` (`CONTROLLER_LIVE_CODEX_TESTS=1`) show `codex exec`
applies the profile: the tester cannot write the clone or anything outside its scratch area, read the operator's home,
environment or credential copy, push, or reach the network. Nothing is wired; the composed-configuration check is the
one open limit and C10g-3 builds it before wiring. The change is `chore:` with no release. The measured record is
`tests/harness_contract/codex/tester_sandbox.txt`; the plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_SANDBOX_PLAN.md`.

## Next action

`/milestone-plan` for C10g-2b (`verify-codex`, [#217](https://github.com/RodrigoFAbreu/workflow-controller/issues/217)), once C10g-2a ([#214](https://github.com/RodrigoFAbreu/workflow-controller/issues/214): the Codex build digest, the verified-build record and `is_verified`) is merged.

## Functional review checklist

Work item `workflow-controller-codex-tester-sandbox` (C10g-1, #210). The change adds one module,
`controller/codex_sandbox.py`, that nothing calls yet, so every flow is offline and uses no forge.

**Tester limits.** The tester runs offline: no network, no host socket, `gh`/`claude`/`codex`/`ssh` are refusing
stand-ins, `PATH` is the sandbox `bin/` plus system directories, and the clone is read-only. Write only to the output
folder, the scratch `HOME`, `XDG_*` and `TMPDIR`. Run Python with `PYTHONDONTWRITEBYTECODE=1` and `python3 -B` so no
`__pycache__` lands in the clone. The live flows (F5, F6) need the real `codex`, `bwrap` and network, which the tester
sandbox lacks; they are operator-run, and the tester marks them `environment`, not `defect`.

**Setup.** Check out `milestone/workflow-controller-codex-tester-sandbox`. No build, flag or data is needed. Live flows
also need `codex-cli 0.163.0`, `bwrap` and `~/.codex/auth.json`, and set `CONTROLLER_LIVE_CODEX_TESTS=1`.

**Flows (each from the repository root).**

1. F1, module tests. `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_codex_sandbox tests.test_package_structure`.
   Expected: 49 tests, `OK`.
2. F2, live tests skip by default. `python3 -B -m unittest tests.test_codex_sandbox_live -v` with the switch unset.
   Expected: every test skipped with a reason; none fail.
3. F3, profile content. In a scratch dir, build a layout and print `codex_sandbox.config_toml` (see `build` and
   `TesterLayout` in `controller/codex_sandbox.py`). Expected: `default_permissions = "tester"`, `network enabled = false`,
   the clone and install trees `read`, only `scratch` `write`, and nothing of the real home listed.
4. F4, overlap refusal. Call `codex_sandbox.check_roots` (or `readable_roots` with `extra=`) with `/`, the operator's
   home, `<base>`, and `codex_home` as readable roots. Expected: each is refused with `ReviewLaunchRefusedError` naming
   the offending root.
5. F5, argv contract. Build `codex_sandbox.argv(...)` and run `check_argv` on it, then on variants that add `-s`, an
   extra `-c`, or drop a pinned item. Expected: the clean argv passes; each variant is refused. The argv carries no
   `-s workspace-write` and does carry `default_permissions="tester"`.
6. F6, live probes (operator only). `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_sandbox_live -v`.
   Expected: both the `codex sandbox -P tester` run and the `codex exec` run judge clean. The clone and anything outside
   scratch are unwritable, the home canary, environment canary and `auth.json` are unreadable, `git push` fails,
   `/tmp` writes stay off the host, and TCP and Unix-socket connects fail. Compare with
   `tests/harness_contract/codex/tester_sandbox.txt`.
7. F7, nothing wired. `grep -rn codex_sandbox controller/ | grep -v "^controller/codex_sandbox.py"`. Expected: only the
   `controller/__init__.py` module listing; no launch path imports it.
8. F8, docs. Read `docs/guide/harnesses.md` (new paragraph) and `docs/guide/development.md`; run
   `python3 -B tools/check_docs.py`. Expected: the paragraph names the sandbox, its probes and the open composed-config
   limit (built in C10g-3); `check_docs` passes.

**Known limits / out of scope.** Nothing is wired: no role is declared and no launch path calls the module. The
composed-configuration check (`check_effective` sibling) is not built; it is the first `KNOWN_LIMITS` entry and C10g-3
closes it before wiring. `verify-codex` and the digest cache are C10g-2. `/usr`, `/etc` and the Codex and Controller
install trees stay readable, `/tmp` writes go to a private mount, and the stand-in `gh` refuses only by name. The change
is `chore:` with no release.
