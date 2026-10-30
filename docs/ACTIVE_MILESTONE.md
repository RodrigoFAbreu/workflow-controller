# Active Milestone

## Status

**Complete.** `workflow-controller-composed-config-check` (C10g-3a, issue
[#221](https://github.com/RodrigoFAbreu/workflow-controller/issues/221)) is implemented (CP1, CP2, the self-review
fixes and the functional review) and accepted by policy.
`controller/codex_sandbox.py` gains `check_effective` and `check_composed`: the configuration Codex composes from the user,
project and managed layers is compared with the pinned tester profile, which narrows `KNOWN_LIMITS` to
`composition_window`. Nothing calls the check until C10g-3b. The change is a `chore:` with no release. The plan is
`docs/ai-workflow/CONTROLLER_COMPOSED_CONFIGURATION_CHECK_PLAN.md`.

## In progress

`workflow-controller-codex-tester-bridge` (C10g-3b, issue [#222](https://github.com/RodrigoFAbreu/workflow-controller/issues/222)): CP1 and CP2 complete (`clone_candidate` and `bound_differences` extracted in `controller/functional_review.py`, `tester_prompt` gains `limits`; `controller/codex_bridge.py` with `Bridge`, `build_bridge`, `check_bound`, `SANDBOX_LIMITS` and `prompt`; `tests/test_codex_bridge.py`; `docs/guide/harnesses.md` and `docs/ROADMAP.md` updated) and the self-review (one style fix) are done; the implementation is in review. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_BRIDGE_PLAN.md`.

## Next action

`/milestone-plan` for C10g-3b2 (the Codex tester's launch and results, [#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)), after this item is accepted.

## Functional review checklist

Work item `workflow-controller-codex-tester-bridge` (C10g-3b, #222), implementation revision 1. The change adds
`controller/codex_bridge.py` (`Bridge`, `build_bridge`, `check_bound`, `SANDBOX_LIMITS`, `prompt`), extracts
`clone_candidate` and `bound_differences` from `build_sandbox` and `check_bound` in `controller/functional_review.py`
(behaviour preserved) and gives `tester_prompt` a `limits` keyword (default unchanged). Nothing calls the bridge: no
launch path, role, setting or exit code changes (C10g-3b2 launches, C10g-3c routes).

**Tester limits.** The tester runs offline: no network, no host socket, `gh`/`claude`/`codex`/`ssh` are refusing
stand-ins, `PATH` is the sandbox `bin/` plus system directories, and the clone is read-only. Write only to the output
folder and the scratch `HOME`, `XDG_*` and `TMPDIR`; run Python with `PYTHONDONTWRITEBYTECODE=1` and `python3 -B`, with
`TMPDIR` set to the scratch area (the tests create temporary folders). Every flow below is offline. No flow needs a pull
request, the real `gh`, `claude`, `codex` or `ssh`.

**Setup.** Check out `milestone/workflow-controller-codex-tester-bridge`. No build, flag or data is needed.

**Flows (each from the repository root).**

1. F1, bridge tests. `python3 -B -m unittest tests.test_codex_bridge`. Expected: 17 tests, `OK` (clone equals the
   manifest, scratch origin and output folder inside the scratch area, `check.git` and `ITEM` outside it, writable
   locations linked and hidden from Git, a refused pattern listed with its reason, `clone_mismatch` removes the folder,
   `check_bound` reports each edit, the prompt carries every limit and none of the Claude-only wording, and the profile
   has the clone read-only and the scratch area as the only `write` entry).
2. F2, shared extractions leave the Claude tester unchanged. `python3 -B -m unittest tests.test_functional_review
   tests.test_functional_sandbox tests.test_codex_sandbox tests.test_package_structure`. Expected: 57 + 79 + 62 + 13
   tests, `OK`, including the test that `tester_prompt` without `limits` returns the text it returned before.
3. F3, untouched paths. `git diff 92155e8..HEAD --stat -- controller/review.py controller/codex_sandbox.py
   controller/harness.py controller/cli.py`. Expected: empty.
4. F4, nothing calls the bridge. `grep -rn "codex_bridge" controller/ | grep -v "^controller/codex_bridge.py"`.
   Expected: hits only in `controller/__init__.py` (the eager import list and `__all__`).
5. F5, bridge build and check by hand. In a scratch directory create a small Git repository, capture a `Candidate`
   with `functional_review.capture_candidate`, call `codex_bridge.build_bridge(...)` with a scratch `runtime_root` and
   `operator_home` (the tests show the arguments), print `codex_bridge.check_bound(bridge)` (expected `[]`), edit a tracked file in `bridge.built.layout.clone` (a plain scratch run; the real sandbox makes it read-only) and expect one difference naming the file, then print `codex_bridge.prompt(bridge, flows,
   timeout_seconds=600)` and read it. Expected: the clone, output folder, head, checklist blob and flows appear, and the
   limits say reads are limited, the clone is read-only, writes go to the scratch area only, and a refused link write is
   `environment`.
6. F6, docs. Read the Codex tester bridge paragraph of `docs/guide/harnesses.md` and the C10g-3b row of
   `docs/ROADMAP.md`; run `python3 -B tools/check_docs.py`. Expected: exit 0; the bridge, "no launch path calls it yet"
   and C10g-3b2 as the launch are described consistently, and the roadmap marks C10g-3b complete and C10g-3b2 next.

**Known limits / out of scope.** The Codex launch, result collection and refusal mapping are C10g-3b2 (#228); routing
and fallback are C10g-3c. Whether Codex honours a write through a writable-location link is not measured here (the
prompt makes a refused write `environment`; the C10g-3b2 live probe measures it). The full suite has two known Git 2.56
trailer failures unrelated to this change. The change is a `chore:` with no release.
