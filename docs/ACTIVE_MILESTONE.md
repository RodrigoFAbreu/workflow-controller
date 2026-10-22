# Active Milestone

## Status

**In progress.** `workflow-controller-claude-build-verify` (C10e, `docs/ROADMAP.md`; issues
[#113](https://github.com/RodrigoFAbreu/workflow-controller/issues/113) and
[#114](https://github.com/RodrigoFAbreu/workflow-controller/issues/114)) has implemented all four checkpoints
(CP1-CP4); functional round 1 found one blocking defect (F1), fixed here, and awaits implementation review again.

- `workflow-controller verify-claude [--force]` runs the live sandbox enforcement probes against the `claude` a
  launch would run and records its digest in `verified-claude-builds.json` in the runtime root only when every probe
  holds and the build's bytes are unchanged by the run. It never touches the settings file.
- The static probe and the launch admit the built-in `VERIFIED_NATIVE_BUILDS` plus the recorded digests, and both
  examine and execute one resolved real path.
- The plan is `docs/ai-workflow/CONTROLLER_CLAUDE_BUILD_VERIFY_PLAN.md` (revision 3, approved at `e1e10e2`); the
  base commit is `679b34c`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of the phase.

The previous milestone, C2b (`workflow-controller-ci-time`), is archived at
`docs/milestones/completed/workflow-controller-ci-time.md`.

## Next action

Implementation review of C10e, then acceptance and the 1.19.0 release. Then A2, the backlog.

## Functional review checklist

Work item `workflow-controller-claude-build-verify` (C10e), implementation revision 4 (functional round 1 fix). The flows below run offline
from a read-only clone: the sandbox has no network, a refusing `gh`, `claude`, `codex` and `ssh`, and writes only go
to the output folder and the scratch `HOME`, `XDG_*` and `TMPDIR`. The live probes need a real `claude` and a model,
so they are out of the tester's reach (see the limitations); the flows exercise the command's contract around them.

**Setup.** `C=<product clone>`; `S=$(mktemp -d)` (scratch, outside the clone); `mkdir $S/bin $S/rt`; put a stand-in
`claude` on `PATH` (`printf '#!/bin/sh\necho refused >&2\nexit 1\n' > $S/bin/claude; chmod +x $S/bin/claude`);
`export PYTHONPATH=$C`; `PATH=$S/bin:/usr/bin:/bin`. Invoke as `python3 -P -m controller --runtime-dir $S/rt
--settings $S/settings.json <command>`. No test data is needed.

| # | Flow | Expected |
|---|---|---|
| 1 | `verify-claude --help` | Usage with `--force`; the text states the cost (up to two short model turns at low effort, existing credentials, usage budget not consulted), exit 0, "recorded in the runtime root, never in the settings file". |
| 2 | `verify-claude` with the shell-script stand-in `claude` | Exit 20. Output names the failing check (`identity: ... is not a readable native (ELF) claude build`), prints the resolved path, the digest and the cost line. No model call is made. |
| 3 | After flow 2: `ls -A $S $S/rt` | No `verified-claude-builds.json`, no `$S/settings.json` (never created), no leftover scratch folder; only the runtime files the Controller always writes. |
| 4 | Same as flow 2 with an existing `$S/settings.json` (`{}` or a partial file) | Exit 20 as before; the file is byte-identical afterwards (`sha256sum` before and after): not filled, not updated. |
| 5 | Put a symlink `$S/bin/claude2 -> $S/bin/claude` and run `--claude-binary $S/bin/claude2 verify-claude` (the global option goes before the subcommand) | Exit 20; the resolved path printed is the real file, not the link. If it differs from the `claude` on `PATH`, the output notes that a launch runs the `PATH` one. |
| 6 | Create `$S/rt/verified-claude-builds.json` as a symlink to a file in `$S`, as a group/world-writable file, and as malformed JSON; rerun flow 2 each time | Each is ignored (never admits a build), exit 20 as in flow 2; the record's target is never written. |
| 7 | `python3 -P -m controller --help` | `verify-claude` is listed; it is not among the read-only commands. |
| 8 | `python3 -m unittest tests.test_claude_build tests.test_cli` (from `$C`, `PYTHONPATH` unset; no `-P`, which hides `tests`) | Pass: the fake-`claude` cases cover record/admit, every broken probe failing closed, `build_changed`, symlink re-pointing, the trust boundary and the settings file untouched. |
| 9 | `python3 tools/check_docs.py` | Passes; `docs/guide/commands.md#verify-claude`, `install.md`, `compatibility.md`, `common-problems.md`, `exit-codes.md`, `guide/runtime.md` and `guide/automation.md` describe the command, exit 20 and the record consistently. |

**Functional round 1 (resolved).**
- F1 (blocking, defect, fixed in `controller/claude_build.py`): the probes ran with `TMPDIR` inside the runtime root, so
  at a normal root depth Claude's bridge sockets exceeded the 107-byte limit and every probe line was missing. The
  probes now use a short private `wcf-*` `TMPDIR` (as the functional launch does), removed with the run; no short root
  refuses as `host`. A tool result of `Sandbox is required but failed to initialize` is now one inconclusive cause.
  Regression tests: deep runtime root keeps the longest socket path in the limit; no short root refuses before any
  launch; a failed sandbox is inconclusive. **Re-test:** `verify-claude --force` against 2.1.303 at a normal-depth root
  records it; 2.1.308 should fail for its own reason (#129).
- F2 (optional, fixed): the flow 8 command above no longer uses `-P`.
- F3 (optional, fixed): the empty `verify-claude/` area folder is removed after a run when no other run uses it.

**Known limitations / out of scope.**
- Recording a digest needs a real `claude` and a model; the sandbox cannot do it. A person (or the opt-in
  `CONTROLLER_LIVE_CLAUDE_TESTS=1` live tests, run by the owner on 2.1.308) covers the live path. The implementation
  author reports that 2.1.308 fails the live boundary probe, which is a finding about that build, not about this
  milestone.
- A build file rewritten in place between the hash and the exec is not caught; the record defends against the
  sandboxed tester and redirection, not against a lifecycle worker running as the operator (stated in the guides).
- The credential-read denial and `file://` origin detection (#115) and running the probes in CI (option 2 of #113)
  are out of scope.
- Two tests fail on this host's Git 2.56 for a known, unrelated reason (bare URL no longer a trailer).
