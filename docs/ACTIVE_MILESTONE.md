# Active Milestone

## Status

**Implemented, awaiting review.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3). The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Next action

`/milestone-plan` for C10g-3b2b (the Codex tester's surroundings, [#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)), after this item is accepted.

## Functional review checklist

Item `workflow-controller-codex-tester-run` (C10g-3b2a, #228), implementation revision 1. Every flow is offline and
unit-level; none needs a network, a pull request, a real `gh`, `claude`, `codex` or `ssh`, or a write inside the clone.

**Setup.** No build or install. Run from the repository root with `TMPDIR` set to a scratch folder you can write to
(the tests create temporary folders; a flow that cannot get one is `environment`, operator-run). Run each module on its
own. Do not pass `PYTHONPATH=.`.

**Test data.** None to seed; the tests build their own scratch repositories and use `tests/fake_codex.py`.

| # | Flow | Command | Expected |
|---|------|---------|----------|
| 1 | The run: controls, launch, outcome precedence | `python3 -m unittest tests.test_codex_tester -v` | All pass. Outcomes `ran`, `timeout`, `limit`, `failed` by that precedence; a zero exit with an absent, empty or whitespace `-o` file is `failed`, never `ran`; a control mismatch or `ReviewLaunchRefusedError` propagates and starts no `codex exec`. |
| 2 | Bridge carries digest, model, effort | `python3 -m unittest tests.test_codex_bridge tests.test_codex_sandbox -v` | All pass. `Built` records `credential_digest`, `model` and `effort`; a bridge built with a routed model or effort rebuilds to its own `argv`. |
| 3 | Fake Codex contract | `python3 -m unittest tests.test_fake_codex_contract -v` | All pass. The new `message` key writes the tester's final message to the `-o` file; the pinned shapes are unchanged. |
| 4 | Credential copy is returned and removed on every path | In `tests.test_codex_tester` output, find the copy-back and removal tests (ran, timeout, limit, failed, refusal) | Each path removes the copy; a rotated `auth.json` is copied back; an empty digest copies nothing. |
| 5 | Result shape matches the Claude launch | Read `run_tester` in `controller/codex_tester.py` against the tests | Dict has `outcome`, `detail`, `tester_message`, `readings`, `first_readings` (empty), `subagents`, `credentials`, `telemetry` (`None`), `exit_code`. |
| 6 | Nothing calls the run | `grep -rn run_tester controller scripts tools --include=*.py` | Only `controller/codex_tester.py` (and `controller/__init__.py` references, if any); no command, setting, option or exit code reaches it. |
| 7 | Untouched surfaces | `git diff 6faebb0..HEAD --stat -- controller/review.py controller/harness.py controller/cli.py` | Empty. |
| 8 | Docs | `python3 -B tools/check_docs.py` | Passes; guide and roadmap record the 3b2a/3b2b split and say nothing calls `run_tester`. |
| 9 | Wording fixes | `python3 -m unittest tests.test_codex_sandbox -v` and read the last `codex_bridge.SANDBOX_LIMITS` entry | Reworded as described in CP3; the prompt test asserts the read roots. |

**Optional, operator-run.** `tests/test_codex_tester_live.py` (`CONTROLLER_LIVE_CODEX_TESTS=1`) costs one model turn on
your credentials; expected outcome `ran` with a final message. It is not a flow.

**Known limitations / out of scope.** The real `codex exec` output on a tester prompt is only measured by the live test.
A link write under the Codex profile is unmeasured. The `is_verified` gate, real-repository snapshots, binding check,
refusal mapping to `functional_tester_unavailable`, role declaration (C10g-3b2b, #231), routing, fallback and telemetry
(C10g-3c) are not part of this item. The full suite is not a flow (`test_functional_sandbox` flake, #206). No release:
the change is a `chore:`.
