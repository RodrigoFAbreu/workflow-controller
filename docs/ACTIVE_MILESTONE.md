# Active Milestone

## Status

**Complete.** `workflow-controller-codex-command-render` (H4a, issue
[#242](https://github.com/RodrigoFAbreu/workflow-controller/issues/242)) is implemented (CP1 and CP2 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_COMMAND_RENDER_PLAN.md`. `controller/command_files.py` adds
`render_command`, a pure function that renders a shared command file and an argument string into a Codex prompt and
refuses the user-only commands (`tests/test_command_files.py`). Nothing calls it. The harness and worker guides and the roadmap state it in place; the change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-fallback` (C10g-3c2, issue
[#236](https://github.com/RodrigoFAbreu/workflow-controller/issues/236)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_FALLBACK_PLAN.md`. A `functional-tester` chain longer than one is walked before the
spawn (`codex_tester.selection_error`, `job._tester_probe`), a Codex tester has a `usage_record` from its rollouts and a
`telemetry` block, and `tests/test_codex_tester_live.py` has a second opt-in test through `_functional_launch`. The first
real round (31 October 2026: `gpt-6.1-sol`, effort `high`, `job.execute_step` on a scratch item at row 38e) ran to
`findings` with a correct report, credentials `unchanged`, the sandbox removed, 12,594 input, 46,080 cache-read, 1,061
output and 93 reasoning tokens in `usage_record` and a `telemetry` block with harness `codex`; no defect was found. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-routing` (C10g-3c1, issue
[#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223)) is implemented (CP1 to CP3 and the functional review) and
accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_ROUTING_PLAN.md`. Routing accepts `codex` for the
`functional-tester` role (the model `gpt-6.1-sol` and an effort `high` or `xhigh` named, no Codex defaults), and
`job._run_functional` runs the launch, usage provider, credential and record fields of the route's harness;
`cli._functional_options` carries both harness homes and binaries. The guides, the table and the roadmap state it in place. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-surroundings` (C10g-3b2b, issue
[#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)) is implemented (CP1 to CP3 and the functional
review) and accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_SURROUNDINGS_PLAN.md`. Codex declares the
tester role with `verify-codex`, routing refuses the pair until C10g-3c, and `codex_tester.launch_codex_functional` adds the
build gate, the before/after snapshots, the binding check and the refusal mapping, with `tests/test_codex_tester_launch.py`;
the guides, the generated table and the roadmap state the new facts in place. Nothing calls the launch until C10g-3c; the
change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3, the self-review fixes and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Functional review checklist

Item `workflow-controller-codex-command-render`, implementation revision 2. Every flow is offline and unit-level (fakes
and fixture files only: no network, no real `gh`/`claude`/`codex`/`ssh`; nothing launches Codex). Run from the product
clone with `TMPDIR` set to a writable scratch directory, one module at a time, `python3 -B` and no `PYTHONPATH=.`. The
product clone is not written to.

**Setup.** `export TMPDIR=<scratch dir>`. No feature flag or data to seed; the tests write their own fixture command files
to a temporary directory and read this repository's `.claude/commands/`.

| # | Flow | Command | Expected |
|---|---|---|---|
| 1 | Renderer over fixtures (R1, R3) | `python3 -B -m unittest tests.test_command_files` | 30 tests pass: the body is returned unchanged apart from `$ARGUMENTS`, which is replaced once per occurrence, verbatim (spaces, quotes, newline, a `$ARGUMENTS` of its own, empty string); CRLF bodies keep their line endings; non-empty arguments for a hint-less command or a body without the token are `arguments_not_accepted`; each malformed-frontmatter case is refused with the line named; unknown keys are ignored; a bad name or absent file is `unknown_command`, an absent directory is `MissingCommandsDirectoryError`, an unreadable file is refused. |
| 2 | User-only refusals (R2) | same module, tests `*user_only*`, `*flag*`, `*arguments_naming*`, `*launch_scan*` (or read the module's output above) | `disable-model-invocation` spelled `true`, `"true"` and `'true'` is refused as `UserOnlyCommandError` (`"false"` is not); a file with only the confirmation guard, and a malformed file a recogniser claims, are refused; an argument string naming any of the eleven user-only commands (backticked and sentence-final forms included) is refused, one naming an allowed command is not. |
| 3 | Real command directory (R2, R3) | same module, `test_every_file_is_rendered_or_refused_for_the_contracts_reason` and its neighbours | Every file in `.claude/commands/` is rendered or refused; the refused set is exactly the nine commands `docs/ai-workflow/COMMAND_FILES.md` lists plus `recover-implementation-provenance` and `satisfy-gate`; every rendered command has a bool `state_writer`; hinted commands render with a sample argument; no prompt names the model or the harness tool. |
| 4 | Nothing is wired in (R4) | same module, `test_codex_still_declares_no_worker_role_and_launchers_are_unchanged` and `test_nothing_in_controller_imports_the_module_but_the_package_init`; then `python3 -B -m unittest tests.test_package_structure tests.test_harness tests.test_worker_launch` | All pass: `harness.CODEX.roles` has no worker role, `worker_launch.LAUNCHERS` keys are unchanged, only `controller/__init__.py` imports `command_files`; the package registration and dependency order tests accept the new module. |
| 5 | Neighbouring modules unchanged | `python3 -B -m unittest tests.test_decision tests.test_write_containment` | All pass (70 and 10 tests): the write-containment scan accepts the new module (no `.replace()` call). |
| 6 | Documents (R4) | `python3 -B tools/check_docs.py` and `python3 -B tools/harness_table.py --check` | Both exit 0; `docs/guide/harnesses.md` says where the renderer sits and that nothing calls it; `docs/guide/workers.md` states the eleven-name refused set (two more than the contract's nine), that a Codex `/satisfy-gate` needs a deliberate rule change, and that the body's confirmation check and H4b's launch policy, not the renderer, stop a nested invocation. |
| 7 | Hand render (optional) | `python3 -B -c "from pathlib import Path; from controller import command_files as c; print(c.render_command('milestone-plan', '', Path('.claude/commands')).prompt[:400])"` | Prints the start of the shared command body; `render_command('approve-review', '', ...)` raises `UserOnlyCommandError`. |

**Known limitations / out of scope.** Nothing calls `render_command`: the Codex launch (H4b), job records (H4c), usage
windows (H4d), Codex review roles (H4e), `milestone-implement` on Codex (H4f) and the remaining roles (H4g) are later
items. The renderer cannot stop a Codex agent from running a user-only command named in an allowed body (the body's
confirmation check and H4b's launch policy do; the guide says so). `/satisfy-gate` and `/recover-implementation-provenance`
are refused although the contract does not list them (fail closed). A flow that creates temporary folders is
`environment` when the tester's `/tmp` is read-only. The full suite is not a flow (`test_functional_sandbox` has a known CI
flake, #206). No new setting, option, exit code or gate; the change is a `chore:` with no release.

## Next action

`/milestone-plan` for TK1 (the user-only scan fix, [#247](https://github.com/RodrigoFAbreu/workflow-controller/issues/247)), then NF3 ([#244](https://github.com/RodrigoFAbreu/workflow-controller/issues/244)), then H4b (the headless Codex launch behind the H3 seam); H4a is accepted.