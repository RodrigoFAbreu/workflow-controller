# Active Milestone

## Status

**In progress.** `workflow-controller-cleanup-3b` (CU3b: `verify-claude` diagnostics and small gaps, closes issues #132
and #133), plan revision 1, in `docs/ai-workflow/CONTROLLER_CLEANUP_3B_PLAN.md`. Checkpoints:

- CP1 (a host effect outranks a sandbox error, and the listener folder is short): complete. `python3 -m unittest
  tests.test_claude_build` passes, also with `TMPDIR` set to a 110-character folder; the new host-effect and deep-`TMPDIR`
  tests fail against the base commit.
- CP2 (a setup failure is exit 20, and `--claude-binary` stands alone): complete. `python3 -m unittest
  tests.test_claude_build` passes; the setup-`OSError` rows (clone files, output folder, listener bind, through the
  command) and the `--claude-binary`-with-empty-`PATH` note test fail against the base commit.
- CP3 (the live tests skip without credentials; the documentation, the roadmap): complete. With an empty scratch
  `CLAUDE_CONFIG_DIR` and `CONTROLLER_LIVE_CLAUDE_TESTS=1`, the five live tests skip instead of erroring;
  `python3 tools/check_docs.py` passes.

## Next action

`/milestone-implement workflow-controller-cleanup-3b` to enter self-review and prepare the implementation bundle.

## Functional review checklist

Work item `workflow-controller-cleanup-3b`, implementation revision 1 (closes #132 and #133). Everything below runs
offline in the tester's sandbox: no `gh`, no real `claude`/`codex`/`ssh`, no network, the clone read-only. Run commands
from the clone root with `PYTHONDONTWRITEBYTECODE=1` and write only to `$TMPDIR`/`$HOME`. Set `S=$(mktemp -d)` first.

**Setup.** No build or install. Use `python3 -B -m unittest ...` with `PYTHONPATH` unset (never `PYTHONPATH=.`). No
credentials, flags or seeded data are needed. For flows 3-4 make a stand-in binary:
`printf '#!/bin/sh\nexit 1\n' > $S/claude && chmod +x $S/claude`.

| # | Flow | Expected |
| --- | --- | --- |
| 1 | `python3 -B -m unittest tests.test_claude_build` | All tests pass, including the host-write-before-sandbox-error rows, the setup-`OSError` rows and the `--claude-binary` empty-`PATH` test. |
| 2 | Same command with `TMPDIR` set to a 110-character folder under `$S` (`mkdir -p`), `PYTHONPATH` unset | Passes: the host-listener folder stays short whatever `TMPDIR` is (#132 point 2). |
| 3 | `env -i PATH=/usr/bin:/bin HOME=$S PYTHONPATH=$PWD python3 -B -m controller verify-claude --claude-binary $S/claude` (no `claude` on `PATH`) | No traceback and no "claude not found" refusal. Prints `claude: <path>` and `digest: <sha256>` then `note: a launch finds no claude on PATH, so functional review cannot start ...`. The stand-in is not a real build, so the probes then refuse and the exit is nonzero (20 expected); nothing is recorded. |
| 4 | Flow 3 with `--claude-binary $S/does-not-exist` | A named refusal (binary not found), no traceback. |
| 5 | `python3 -B -m unittest tests.test_functional_enforcement` with `CLAUDE_CONFIG_DIR=$S/empty` (an empty folder) and `CONTROLLER_LIVE_CLAUDE_TESTS=1` | The five live tests are reported as skipped, none errors (#133 point 3). |
| 6 | `python3 -B tools/check_docs.py` | Passes. Read `docs/guide/commands.md` (`verify-claude`) and `docs/guide/development.md`: they state that `--claude-binary` needs no `claude` on `PATH`, that a setup failure exits 20 as the `host` probe, and that the live tests skip without credentials. |
| 7 | Read `docs/ROADMAP.md` | CU3b is marked done and follow-ups 81 and 82 are gone. |

**Known limitations / out of scope.** The real probes need a real `claude` with credentials and cannot run in the
sandbox, so the exit-0 path and a real host-write-with-sandbox-error run are covered by unit tests only. A full-disk or
bind failure (exit 20 as `host`) is exercised through the tests' injected `OSError`, not live. Out of scope: usage and
settings tidy-ups (CU3c), `verify-codex` (H7), any change to which probes run or what they judge.
