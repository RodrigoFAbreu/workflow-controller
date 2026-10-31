# Active Milestone

## Status

**In progress.** `workflow-controller-codex-tester-surroundings` (C10g-3b2b, issue
[#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)): CP1 complete (Codex declares the tester role
with `verify-codex`, routing refuses the pair until C10g-3c, the three carried findings fixed); CP2 complete (`codex_tester.launch_codex_functional`: the build gate, the
before/after snapshots, the binding check and the refusal mapping, with `tests/test_codex_tester_launch.py`); CP3 complete (the guides, the generated
table and the roadmap state the new facts in place). Nothing calls the launch until C10g-3c; the change is a `chore:` with no release.
The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3, the self-review fixes and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Next action

`/milestone-plan` for C10g-3c ([#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223), the Codex tester chosen through routing), after this item is accepted.

## Functional review checklist

Item `workflow-controller-codex-tester-surroundings` (C10g-3b2b, #231), implementation revision 3. Every flow is offline
and runs against the fake Codex and a scratch origin; nothing needs the network, a real `codex`, `claude`, `gh` or `ssh`.

**Setup.** Work in the repository checkout. Set `TMPDIR` to a writable scratch folder (the tests create temporary
folders). Do not set `PYTHONPATH=.`. Run each test module on its own, not the whole suite (`test_functional_sandbox` has a
known CI flake, #206).

**Test data.** None to seed; the tests build a temporary repository with a scratch bare origin and a fake Codex launcher.

**Flows and expected results.**

1. `python3 -B -m unittest tests.test_codex_tester_launch` -- expected `OK` (24 tests): an unverified build (no record,
   untrusted record, unrecorded digest, unbindable layout) raises `functional_tester_unavailable` with cause
   `enforcement`, names `verify-codex`, starts no process and leaves no folder under `<runtime>/functional/`; a missing
   binary is `no_binary`; a clean run returns every key (incl. `subagents`, `exit_code`), `breach == []`,
   `unbound == []`, `keep` false and the sandbox still present; a changed worktree, `HEAD`, ref or local-origin ref is a
   `breach`; a removed record, changed digest or re-pointed launcher is a `codex build:` breach; a pre-existing job
   folder is refused and left alone; a failing sandbox removal does not replace the launch's own exception.
2. `python3 -B -m unittest tests.test_codex_tester tests.test_codex_bridge tests.test_codex_sandbox` -- expected `OK`:
   `build_bridge(prompt=...)` is refused; `Built` records its binary and `run_tester` refuses a different one before any
   `codex exec`; the live test is skipped with `CI` set and says so.
3. `python3 -B -m unittest tests.test_harness tests.test_routing tests.test_harness_table tests.test_functional_sandbox tests.test_docs`
   -- expected `OK`: Codex declares `reviewer` and `functional-tester`, its `build_verification` is `verify-codex`, and
   `functional-tester` with `codex` is still refused by routing (role entry, default, `--role-harness`, fallback chain,
   settings validators) with the message naming C10g-3c; `review-plan` and worker roles keep the old refusal.
4. `python3 -B tools/harness_table.py --check` and `python3 -B tools/check_docs.py` -- expected exit 0.
5. `git diff 01c67ac..HEAD --stat -- controller/review.py controller/job.py controller/functional_review.py` -- expected
   empty output.
6. `grep -rl launch_codex_functional . --include='*.py'` -- expected only `controller/codex_tester.py` and
   `tests/test_codex_tester_launch.py` (nothing calls it).
7. Read `docs/guide/harnesses.md`, `commands.md`, `workers.md`, `runtime.md`, `automation.md`: no text still says "nothing
   consults the record until C10g-3" or that Codex declares only the reviewer role; `docs/ROADMAP.md` marks C10g-3b2b
   complete as a `chore:` with no release and keeps C10g-3c next.

**Known limitations / out of scope.** No live flow: the one opt-in live test is unchanged apart from its `CI` guard, and
the operator-run Codex functional round is C10g-3c (`CONTROLLER_LIVE_CODEX_TESTS=1`). Routing selection, the fallback
chain, telemetry dimensions and the call site in `job.py` are C10g-3c. Any test that starts the real `app-server` or
builds a deep path is an `environment` limit, operator-run. Two Git 2.56 trailer tests fail locally for a known,
unrelated reason. No command, setting, option, exit code or gate changes; the PR is a `chore:` with no release.
