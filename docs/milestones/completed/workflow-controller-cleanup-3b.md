# Clean-up, part 2: `verify-claude` diagnostics and small gaps (Revision 1)

Work item: `workflow-controller-cleanup-3b`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `cf62d04b1eec56dadec8265cb4e43618296b444e` ("chore: move this repository to Workflow 2.18.7 (#147)"), the tip of `main`, passed explicitly as `/milestone-plan cf62d04…`.
Lifecycle authority: this repository's installed Workflow **2.18.7**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` "Next, in order" item 1, CU3b (the second of the three parts of CU3).
Released baseline: `workflow-controller 1.19.1`. The change alters the behaviour of one command, so the squash commit is a `fix:` change and releases a patch (1.19.2).
Pull request title: `fix: verify-claude names a host write before a sandbox error, takes --claude-binary without claude on PATH and exits 20 on a setup error (#132, #133)`

## Goal

Close two open `review-follow-up` issues in full: [#132](https://github.com/RodrigoFAbreu/workflow-controller/issues/132) (a sandbox-start error hides a simultaneous host write, and the `verify-claude` tests fail under a deep `TMPDIR`) and [#133](https://github.com/RodrigoFAbreu/workflow-controller/issues/133) (`--claude-binary` still needs a `claude` on `PATH`, an `OSError` while the probe run is built escapes as a traceback, and the development guide is wrong about the live tests). All three code points sit in `verify-claude`; no new mechanism is added, and every fix keeps the command fail-closed: nothing is recorded in any of these cases.

The `TMPDIR` point of #132 is a production defect, not only a test one: `_Run` creates the host-listener folder with `tempfile.mkdtemp(prefix="wcv-")`, which follows the ambient `TMPDIR`, so a deep `TMPDIR` makes the real command die with `AF_UNIX path too long` (a traceback).

## Decisions

Each decision states one principle.

- **D1. A host effect outranks a missing probe (#132, point 1).** In `controller/claude_build.py`, `need_sandbox` no longer raises on its own. When a tool result says the sandbox could not start, `boundary` and `retry` first run the forbidden-effect checks (`host_effects(..., permitted=False)`, which names a write to the clone, to the host paths, a listener hit or a replaced link), and raise that failure (not inconclusive) if one is found; only when none is found do they raise the existing inconclusive "nothing was probed" error. The permitted-write check is not run on that path, because a build whose sandbox did not start made no permitted write, and "the permitted write did not happen" would restate the sandbox error as a second cause. In `retry` the existing `target.exists()` check also comes before the sandbox check. A host write is therefore never reported as "nothing was probed"; a sandbox error with no host effect is reported exactly as today.
- **D2. The host-listener folder is short whatever the ambient `TMPDIR` is (#132, point 2).** `short_tmp` takes the folder prefix and what the folder is for as arguments (defaults `fr.TMP_PREFIX` and "the probes' TMPDIR"), so its refusal names the folder it was making ("no short folder for the host listener: ..." for the listener, unchanged for the probes' `TMPDIR`), and `_Run` creates its listener folder with `short_tmp("wcv-", "the host listener")` instead of `tempfile.mkdtemp(prefix="wcv-")`, so the Unix-socket path of `host.sock` is checked against the same limit, under the same roots, as Claude's bridge sockets. A failure to make the listener folder is now `fr.TesterUnavailable`, which `verify_build` already turns into `BuildNotVerified("host", ...)`, instead of a bare `OSError`; the test that exercises it (`test_a_failure_after_the_tmpdir_is_allocated_releases_it_and_records_nothing`, which makes `mkdtemp(prefix="wcv-")` raise `ENOSPC`) therefore changes in CP1, the checkpoint that changes its behaviour, to expect `BuildNotVerified` with probe `host` and the `wcf-*` folder released. It stays a different folder from the probes' `TMPDIR` (the sandbox must not be able to reach the listener through a scratch folder it may write to). The tests then need no change of their own fake host: they pass under a deep `TMPDIR` because the product no longer depends on it, and one regression test runs `verify_build` with `tempfile.tempdir` set deeper than the socket limit and requires a pass.
- **D3. A failure to set the run up is a refusal, not a traceback (#133, point 2).** `verify_build` turns an `OSError` raised while the run state is built (`_Run(...)`) or prepared (`state.prepare(...)`: the clone files, the output folder, the credential copy, the listener bind) into `BuildNotVerified("host", ...)` naming the operation's error, in the same way it already turns `fr.TesterUnavailable`; the `finally` block still removes everything that was created. Exit 20 and no record, as the documented contract says. The listener folder's own `OSError` is already a refusal after D2 (above) and is not a D3 site; D3 covers the sites only the wrapper catches: the clone file writes and the output folder in `prepare`, and the listener `bind` in `Host(...)`. One accepted gap: when `Host(...)` raises after its Unix socket is bound (for example on the TCP bind), that socket's descriptor is released at garbage collection rather than by `cleanup`; the folder is still removed, so nothing is left on disk, and the case is not worth a new close path in this patch.
- **D4. `--claude-binary` is resolved first and `PATH` is only compared (#133, point 1).** `cmd_verify_claude` resolves `--claude-binary` when given and does not require a `claude` on `PATH`. The `PATH` build is looked up only to print the existing note; when none is found, the note says that a launch finds no `claude` on `PATH`, so functional review cannot start until a `claude` is on `PATH` (for example by linking the verified build there). The note must not point at `--claude-binary` as a way to select the tester: for a launch that option selects the lifecycle worker only (`controller/cli.py:2054`, `controller/worker.py:1335`), while the functional tester always launches the default `claude` name (`_functional_options` in `controller/cli.py:1464` fixes `binary` to `review.CLAUDE.default_binary`; `controller/job.py:7553`), and no setting selects another binary, so the launch behaviour stays unchanged. Without `--claude-binary` and without a `claude` on `PATH`, the refusal is the one that exists today.
- **D5. The development guide and the live tests agree (#133, point 3).** The live helper in `tests/test_functional_enforcement.py` skips a probe when preparation refuses with the `credentials` probe (a missing `.credentials.json`), as it already skips on `ModelUnreachable`, so the guide's sentence is true; the guide's sentence is made exact: the probes skip when there is no `.credentials.json` (the `missing_component` probe, which makes no model call, skips too, because `prepare` refuses before any probe), and a run with a skip does not prove the enforcement.
- **D6. The documentation and the issues follow the change.** `docs/guide/commands.md` (`verify-claude`) states that `--claude-binary` needs no `claude` on `PATH` and that a setup failure exits 20; `docs/guide/development.md` carries D5; `docs/ROADMAP.md` marks CU3b done and drops follow-ups 81 and 82; `docs/ACTIVE_MILESTONE.md` is updated at acceptance. The release note, the release history row and the compatibility line belong to the docs pull request after the 1.19.2 release, as for every earlier release. The pull request closes #132 and #133.

## Out of scope

Usage and settings tidy-ups (CU3c), the Codex counterpart `verify-codex` (H7), a change to which probes run or to what they judge, the `LiveEnforcementTests` base folder (live-only, not a defect), and any change to the recorded-builds file.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | A host effect outranks a sandbox error, and the listener folder is short | - | 2 | 1 |
| CP2 | A setup failure is exit 20, and --claude-binary stands alone | CP1 | 2 | 1 |
| CP3 | The live tests skip without credentials; the documentation, the roadmap | CP2 | 1 | 1 |

<!-- CP1 -->
### CP1 -- A host effect outranks a sandbox error, and the listener folder is short (#132)

**Files:** `controller/claude_build.py`, `tests/test_claude_build.py`

D1, D2. `need_sandbox` becomes a check that returns whether the sandbox failed (or takes the forbidden-effect check as a step before it raises); `boundary` and `retry` order the checks as D1 says. `short_tmp` takes a prefix; `_Run` uses it for the listener folder and `cleanup` still removes it. Tests: a fake build that reports the sandbox-start error and also has a host effect is reported as that effect, not inconclusive, with exactly that one problem -- the boundary row's fake creates a new file `clone/probe-new-file` (reported once, by `host_effects`; a write to a tracked clone file is not used, because `Watcher._poll` reports it on every poll that sees it, so its problem count races: 1 in 14 runs, 2 in 1 of 15, measured), and the retry row's fake writes to the host path (that row raises one message from the `target.exists()` check or the host-effect check, with no `problems` evidence, so it checks the message, not `evidence["problems"]`); a sandbox error alone is still inconclusive with the existing text; a `verify_build` run with `tempfile.tempdir` deeper than the socket limit passes; the tmpdir-release test is updated as D2 says (`BuildNotVerified` probe `host`, `wcf-*` released, no record), and a refusal test checks that the message names the host listener when only that folder cannot be made.

Done when: the new tests pass and the host-write rows fail against the old order (checked once locally); the updated tmpdir-release test passes; `python3 -m unittest tests.test_claude_build` passes with `TMPDIR` set to a 110-character folder (44 errors before, none after), and under `python3 tools/run_tests.py` the tmpdir-release test no longer errors on the socket path.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- A setup failure is exit 20, and `--claude-binary` stands alone (#133)

**Files:** `controller/claude_build.py`, `controller/cli.py`, `tests/test_claude_build.py`

D3, D4. `verify_build` wraps the `OSError` of `_Run(...)` and `state.prepare(...)` as `BuildNotVerified("host", ...)`; `cmd_verify_claude` resolves the named binary first and treats a missing `PATH` build as the note of D4. Tests: an `OSError` (`ENOSPC`) from the clone file writes, from the output folder, and from the listener `bind` (`socket.bind` in `Host(...)`) each gives `BuildNotVerified` probe `host`, no record and nothing left behind; through the command, `verify-claude --claude-binary <fake>` with an empty `PATH` runs and prints the note, and the test asserts the note's text: it says no `claude` is on `PATH` and a `claude` must be put there, and it does not offer `--claude-binary` as a way to select the functional tester, the same run without `--claude-binary` refuses as before, and a setup `OSError` exits 20 with no traceback.  The command-level tests are the existing command class of `tests/test_claude_build.py`.

Done when: the new tests pass and each fails against the base commit `cf62d04` (checked once locally, where the `OSError` escapes as a traceback); the existing `verify-claude` command tests (the command class of `tests/test_claude_build.py`) pass, and `python3 -m unittest tests.test_claude_build` is green at the end of each checkpoint.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The live tests skip without credentials; the documentation, the roadmap (#133)

**Files:** `tests/test_functional_enforcement.py`, `docs/guide/development.md`, `docs/guide/commands.md`, `docs/ROADMAP.md`

D5, D6. The live helper skips on the `credentials` refusal; the guide sentence and the `verify-claude` command page change as D6 says; the roadmap marks CU3b done and drops follow-ups 81 and 82. The pull request text names #132 and #133 as closed.

Done when: `python3 tools/check_docs.py` passes; with no `.credentials.json` in a scratch `CLAUDE_CONFIG_DIR` and `CONTROLLER_LIVE_CLAUDE_TESTS=1`, the live probe tests skip instead of erroring (checked locally with a fake `claude` first on `PATH`, or by calling the helper with the refusal patched in if no `claude` is installed); the full suite is green but for the known Git 2.56 trailer tests; every point of the two issues is changed or named as already covered, with the covering test.
<!-- /CP3 -->
