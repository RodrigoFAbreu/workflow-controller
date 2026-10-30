# C10g-3b2a: the Codex tester's run (Revision 2)

Work item: `workflow-controller-codex-tester-run`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `6faebb053b268f892c8831976afb0d399f2b2748` ("chore: the Codex tester's sandbox bridge (C10g-3b, #222) (#230)"), the tip of `main`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row C10g-3b2, split here into C10g-3b2a (this item, issue [#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) and C10g-3b2b (issue [#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)), sixth slice of the Codex tester ([#131](https://github.com/RodrigoFAbreu/workflow-controller/issues/131)).
Released baseline: `workflow-controller 1.25.0`. Nothing user-visible is added (a module nothing calls yet, one field on `codex_sandbox.Built`, a guide paragraph and roadmap rows), so the squash commit is a `chore:` and there is no release, as for C10g-3a and C10g-3b.
Pull request title: `chore: the Codex tester's run (C10g-3b2a, #228)`

## Goal

C10g-3b built the Codex tester's folder from a candidate (`controller/codex_bridge.py`: `build_bridge`, `check_bound`, `prompt`) but nothing starts Codex in it. This item adds the run: given a `Bridge`, run the launch controls, start `codex exec` on the tester's prompt under a deadline, and turn what happened into an outcome (`ran`, `timeout`, `limit` or `failed`) with the tester's final message, the usage readings and the credential copy-back. It is the Codex counterpart of the `_claude_run` part of `review.launch_functional`. Nothing calls it: the gate on `codex_build.is_verified`, the before/after comparison of the real repository, the binding check, the mapping of refusals to `functional_tester_unavailable`, the sandbox removal and the role declaration are C10g-3b2b ([#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)), and routing and fallback are C10g-3c.

The issue asked for this split before the first review when the plan needs more than two mechanisms. It does: the run (this item) and the surroundings (3b2b) are each one mechanism, and 3b2b is filed as #231 with this plan.

## What exists

- `controller/codex_sandbox.py`: `build` returns `Built` (layout, `config_text`, `roots`, the pinned `argv` whose prompt is `-` (standard input), the child `environment`), but it discards the credential digest `review.build_home` returns, which the copy-back needs, and it records neither the `model` nor the `effort` it put in `argv` (`codex_bridge.build_bridge(**options)` forwards both). `check_launch` (outside layers, `mcp list`, `features list`, the home before and after), `check_composed` (the composed configuration against the profile) and `check_argv` are the controls that run before a launch.
- `controller/review.py`: `run_bounded` (own session, deadline, process-group and tag kill), `scan_rollouts` (a limit by `task_complete.error`, sub-agents), `copy_back_credentials`, `functional_review.tester_message_excerpt`. The Codex reviewer's `_codex_attempt` does the same sequence for the reviewer but is bound to a `JobLayout` tree, a verdict file under it and the reviewer's own checks, so it is not reused (D1).
- `controller/usage.py`: `read_codex_home` reads the readings a Codex home holds.
- `tests/fake_codex.py`: a scripted Codex whose `exec` writes rollouts, honours `-o`, can exit non-zero, hang, report a limit, rotate `auth.json` and write a sub-agent rollout. `tests/harness_contract/codex/` pins the real shapes (stdout is the final message only; stderr is the transcript and is never read for classification).

## Decisions

Each decision states one principle.

- **D1. A new module, the reviewer's launch untouched.** `controller/codex_tester.py` holds `run_tester`. It reuses `run_bounded`, `scan_rollouts`, `copy_back_credentials`, `codex_sandbox.check_launch`, `check_composed` and `check_argv` through their current signatures, and re-implements only the sequence, because `_codex_attempt` is bound to the reviewer's layout, verdict file and `_preflight`. `review.py`, `launch_review`, `launch_functional`, `settle_functional`, `verify-claude` and `verify-codex` are not changed.
- **D2. The final message is the `-o` file, never the transcript.** `codex_sandbox.argv` already passes `-o <layout.verdict>`; the tester's message is that file's text. Stderr is the whole transcript and is read for nothing but the tail of a failure's `detail`, as the Claude path does with its own output. No `--json` stream is added: the outcome needs only the exit code, the deadline, the rollouts (limit) and the message.
- **D3. Outcome by precedence, and a missing message is a failure.** `timeout` when the deadline passed; else `limit` when `scan_rollouts` reports `limit_error` (Codex's own `task_complete` record, never telemetry at 100 percent alone); else `failed` when the exit code is non-zero (`detail` is the last 400 characters of stdout and stderr) or when the run exited zero but wrote no final message (`detail` says so; an `-o` file that is absent, empty or only whitespace is no message); else `ran`. An unknown shape in a rollout is not a limit and not a pass: it falls to the exit code and the message. `RESULTS.json` is not read here: whether the output folder holds valid results is `functional_review`'s decision, unchanged.
- **D4. The controls run in order, before every launch, and a refusal propagates.** In order: the pinned command is rebuilt (`codex_sandbox.argv(layout, binary=..., model=built.model, effort=built.effort)`) and compared with `Built.argv`, then `check_argv(..., model=built.model, effort=built.effort)`, then `check_launch`, then `check_composed(..., model=built.model, effort=built.effort)`. The model and effort come from the bridge, never from `run_tester`'s arguments and never from the defaults: `Built` gains `model: str = review.DEFAULT_MODEL` and `effort: str = review.DEFAULT_EFFORT` (defaulted, like `credential_digest`, so every earlier construction stays valid) and `codex_sandbox.build` fills them with the values it was given, so a bridge built with a routed model or effort (H1, C10g-3c) rebuilds to its own `argv` and runs. A recorded value that does not reproduce `Built.argv` is a `ReviewLaunchRefusedError` before any `codex exec`. Any `ReviewLaunchRefusedError` propagates unchanged and starts nothing; mapping it to `functional_tester_unavailable` is 3b2b. The credential copy is still removed and copied back on that path (D5).
- **D5. The credential copy is returned and removed on every path.** `Built` gains `credential_digest: str = ""` (the value `build_home` returned; the default keeps every existing construction valid). `run_tester` reads the usage readings, then calls `review.copy_back_credentials(layout.job, operator_home, digest, review.CODEX)` and removes the credential copy, in a `finally`, recording a failure of one step without hiding another and raising it only when nothing else is in flight, as `launch_functional` does. An empty digest copies nothing back.
- **D6. The result is a dict of the Claude launch's shape.** `run_tester` returns `outcome`, `detail`, `tester_message` (through `tester_message_excerpt`), `readings`, `first_readings` (empty: Codex has no per-window first reading), `subagents`, `credentials`, `telemetry` (`None`: the run reports no cost figures) and the bounded run's `exit_code`, so 3b2b merges it into the dict `launch_functional` returns without a new step downstream.
- **D7. The fake is extended by one key.** `tests/fake_codex.py`'s `exec` scenario gains `message`: text written to the `-o` file instead of a review verdict, so a tester's final message can be scripted. Nothing else changes, and `tests/test_fake_codex_contract.py` keeps pinning the shapes.
- **D8. Release type.** `chore:`: no module is reachable from a command, setting, option or exit code, and `harness.CODEX` does not declare the tester role until 3b2b.

## Checkpoints

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The run: controls, launch and outcome | - | 2 | 1 |
| CP2 | Readings, the credential copy-back and the result dict | CP1 | 2 | 1 |
| CP3 | The opt-in live run, two carried wording fixes and the documents | CP2 | 1 | 1 |

<!-- CP1 -->
### CP1. The run: controls, launch and outcome

**Files:** `controller/codex_sandbox.py`, `controller/codex_tester.py`, `controller/__init__.py`, `tests/fake_codex.py`, `tests/test_codex_tester.py`, `tests/test_package_structure.py`

`Built.credential_digest` (D5), `Built.model` and `Built.effort` (D4) and `codex_sandbox.build` filling them. `controller/codex_tester.py` with `run_tester(bridge, flows, *, binary, operator_home, timeout, system_dir=..., on_spawn=None, run=None)` doing D4 (its model and effort are `bridge.built.model` and `bridge.built.effort`, not parameters) then the launch (`run_bounded` with the prompt from `codex_bridge.prompt` on standard input, `cwd` the clone) and the outcome of D3 and the message of D2, with the `run` seam for the controls so tests need not start `app-server`. The fake's `message` key (D7); `controller/__init__.py` and `tests/test_package_structure.py` register the module as the previous bridge item did.

Done when `tests/test_codex_tester.py` shows, against the fake Codex and a real bridge built from a temporary repository: a normal run is `ran` with the scripted message; an exit code 3 is `failed` with the output tail; a hang past the deadline is `timeout` and leaves no process of the job's tag; a limit rollout is `limit`; an exit of zero with no message, and with an `-o` file that is empty or only whitespace, is `failed` with the stated detail; a bridge built with a non-default model and effort runs (`ran`, and the fake's log shows the `exec` carrying them), and a `Built` whose recorded model or effort does not reproduce its `argv` is refused before any `codex exec`; each of an altered argv, a refused `check_launch` and a refused `check_composed` starts no `codex exec` (the fake's log shows none) and propagates; the prompt on standard input is the `codex_bridge.prompt` text.
<!-- /CP1 -->

<!-- CP2 -->
### CP2. Readings, the credential copy-back and the result dict

**Files:** `controller/codex_tester.py`, `tests/test_codex_tester.py`, `tests/test_codex_sandbox.py`

D5 and D6: the readings from `usage.read_codex_home`, the sub-agent rollouts, `copy_back_credentials` and the removal of the credential copy in the `finally`, and the result dict. `tests/test_codex_sandbox.py` shows `build` records the digest of the credential copy it wrote and the model and effort it was given (default and non-default), and every earlier `Built` construction still works.

Done when `tests/test_codex_tester.py` shows: a run that rotates `auth.json` copies it back (`copied`), one that does not reports `unchanged`, and an operator file changed meanwhile reports `operator-changed` without being written; the copy in the job's Codex home is gone after a run, after a refusal and after a deadline; a failure of the copy-back is raised only when nothing else is in flight; the readings and a sub-agent's tokens appear in the result; the result holds every key of D6; and `git diff 6faebb0..HEAD --stat -- controller/review.py controller/harness.py controller/cli.py` is empty.
<!-- /CP2 -->

<!-- CP3 -->
### CP3. The opt-in live run, two carried wording fixes and the documents

**Files:** `controller/codex_bridge.py`, `tests/test_codex_bridge.py`, `tests/test_codex_tester_live.py`, `docs/guide/harnesses.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

`tests/test_codex_tester_live.py`: one opt-in test (`CONTROLLER_LIVE_CODEX_TESTS=1`, skipped by default and on CI, with its reason; it costs one model turn on the operator's credentials) that builds a bridge from a tiny checklist and runs `run_tester` with a real `codex`, asserting an outcome of `ran` and a final message. It does not require valid results; the operator-run functional round is C10g-3c.

Carried from C10g-3b's review, where this item touches the same text (`codex_bridge.prompt` is what `run_tester` sends): the last entry of `codex_bridge.SANDBOX_LIMITS` is reworded ("`HOME` is not writable and not a readable location; a flow that needs a writable home sets `HOME` inside `TMPDIR`", F-421ee7f8907e, F-177195bdb82a), and the prompt test asserts that every `read` entry of `bridge.built.roots` is the clone, a system read directory, an install or interpreter root, the package directory or the sandbox's own files (F-74480bb786fa). The two `docs/guide/harnesses.md` lines that name C10g-3 for the role declaration and the launch gate (F-cef7722c3baa, F-ff4d8da7e743) are 3b2b's text and are retargeted there.

Documents, in place: `docs/guide/harnesses.md` retargets, in place, the two stale mentions of C10g-3b2 ("no launch path calls either yet (C10g-3b2 wires them)" and "C10g-3b2 launches it") to C10g-3b2a/C10g-3b2b, says `run_tester` exists, what it runs and returns, that nothing calls it, and that C10g-3b2b adds the gate and the surroundings; `docs/ROADMAP.md` retargets the C10g-3b2 mention at its line 84 ("C10g-1 to C10g-3c with C10g-3b2") to C10g-3b2a and C10g-3b2b, replaces the C10g-3b2 row by a complete C10g-3b2a row and a C10g-3b2b row (issue #231, depends on 3b2a, `Complexity: medium`), makes C10g-3c depend on 3b2b, and updates the order sentence at its line 43; `docs/ACTIVE_MILESTONE.md` points to `/milestone-plan` for C10g-3b2b.

Done when the prompt test passes with the new read assertion and the reworded limit, the live test is skipped by default and passes when enabled on a machine with `codex` and `bwrap`, `python3 tools/check_docs.py` passes, and the suite is green but for the known Git 2.56 trailer tests.
<!-- /CP3 -->

## Requirements

| id | requirement | checkpoints |
|---|---|---|
| R1 | Given a bridge, `run_tester` runs the launch controls in order and then `codex exec` on the tester's prompt under a deadline; a refused control starts nothing and propagates (#228) | CP1 |
| R2 | The outcome is `ran`, `timeout`, `limit` or `failed` by the stated precedence; the final message is the `-o` file; a missing message after a zero exit is a documented `failed`, never a silent pass (#228) | CP1 |
| R3 | The usage readings, the sub-agents and the credential copy-back are collected, and the credential copy is removed on every path; the result has the Claude launch's shape (#228) | CP2 |
| R4 | Nothing calls the run, no role is declared, and the reviewer's launch, the Claude tester, `verify-claude`, `verify-codex`, settings, options, exit codes and gates are unchanged; the guide and roadmap say so and record the split (#228, #231) | CP2, CP3 |
| R5 | One opt-in live test runs a real Codex tester turn; two carried wording fixes land where this item touches the text (#228) | CP3 |

## Functional review

Every flow is offline and unit-level, run one module at a time (`tests.test_codex_tester`, `tests.test_codex_bridge`, `tests.test_codex_sandbox`, `tests.test_fake_codex_contract`), plus `python3 -B tools/check_docs.py`, `git diff 6faebb0..HEAD --stat -- controller/review.py controller/harness.py controller/cli.py` (expected empty) and a search showing nothing outside `controller/codex_tester.py`, its tests and `controller/__init__.py` references `run_tester`. The tester's `/tmp` is read-only and these tests create temporary folders, so the tester runs them with `TMPDIR` set to its scratch area; a flow that cannot is `environment` and operator-run. The one live test is not a flow (it costs a model turn); the operator may run it once. The full suite is not a flow (`test_functional_sandbox` has a known CI flake, #206).

## Risks

- **The real `codex exec` output on a tester prompt is unmeasured.** The offline tests use the fake, whose shapes come from the pinned captures; the live test is the first real run. A drift shows as `failed`, never as a pass (D3).
- **`check_composed` starts `app-server` before each launch.** Tests use the `run` seam; the live test exercises the real one.
- **The window between the controls and the launch** is the one `codex_sandbox.KNOWN_LIMITS` already names.
- **A writable-location link write is unmeasured under the Codex profile.** The prompt says a refused write is `environment`; the live test and C10g-3c measure it.
- **Out of scope:** the `is_verified` gate, the real-repository snapshots, the binding check, the refusal mapping, sandbox removal and the role declaration (C10g-3b2b); routing, fallback, telemetry dimensions and the `sandbox_limits_note()` wording once either tester may run (C10g-3c); the Codex worker (H4a to H4g); any change to the reviewer, `verify-codex`, settings, options, exit codes or gates.
