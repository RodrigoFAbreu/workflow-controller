# Functional tester: check availability before reserving usage (Revision 2)

Work item: `workflow-controller-sandbox-precheck`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `4a91b261bb953b6aac97d0fe03b997066ad45bae` ("fix: the functional tester cannot read credentials, a file:// origin is checked and the summary is bounded (C10h, #115) (#279)"), the tip of `main`, passed explicitly as `/milestone-plan 4a91b26…`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row C10i (split out of CU3); the next item is C10j.
Released baseline: `workflow-controller 1.27.6`. The change moves a check earlier and adds no setting, option or exit code, so the squash commit is a `fix:` change and releases a patch (1.27.7).
Pull request title: `fix: the functional tester's availability is checked before usage is reserved (C10i, #112)`

## Goal

Close [#112](https://github.com/RodrigoFAbreu/workflow-controller/issues/112). When the host cannot run the functional tester (no `claude`, no `bwrap` or `socat`, no seccomp filter, a `claude` build that is not verified), the step ends at `functional_tester_unavailable`, but only after `_run_functional` (`controller/job.py`) has asked the usage budget for a reservation; the stop is the one `GATE_BLOCKED` record `unavailable_record` writes, and the reservation was taken first. The static probe runs inside `review.launch_functional`, after both. Nothing is spent and nothing is left running, but the reservation briefly takes headroom another lane may need, and the usage record shows a reservation for a tester that never started. The fix is to run the same probe first, so the stop is the one `GATE_BLOCKED` record, written with no reservation ever taken.

## Decisions

Each decision states one principle.

- **D1. What the host can decide up front is decided before anything is admitted or recorded.** `review` gains `claude_selection_error(binary_name, runtime_root)`, the Claude counterpart of `codex_tester.selection_error`: it returns the `TesterUnavailable` a launch would raise for the causes that need no job and no sandbox, or `None`, and every refusal it returns carries a `cause` in its `evidence`. Those causes are the refused role (`role_refused`), the unresolved binary (`no_binary`), and the static probe (`bwrap`, `socat`, seccomp and the build identity check, with the recorded digests of `claude-build`). The body is the one the launch already runs: `launch_functional` calls a shared helper for its own first steps (role, binary, real path, probe), so the selection and the launch cannot drift apart, for the cause as well as the check. The probe's own refusals (`functional_review.host_probe`, `identity_check`, `static_probe`) raise `TesterUnavailable` with no `cause` in `evidence` (`controller/functional_review.py:1275`, `:1319`), and `unavailable_record` reads `exc.evidence["cause"]` directly (`controller/job.py:7797`); the launch never needed it, because its handler reads only the message. So the shared helper wraps the probe call: a refusal without a `cause` is re-raised as `functional_review.unavailable(UNAVAILABLE_ENFORCEMENT, exc.message, **exc.evidence)`, keeping the message and the evidence and chained from the original, and one carrying a `cause` passes through unchanged. The launch and the selection therefore give the same cause (`enforcement`) for the same refusal, and a host without `bwrap` ends at the stop instead of raising `KeyError('cause')` out of `_run_functional`. The launch keeps making the calls itself, as `launch_codex_functional` does, so a host that changes between the selection and the launch still ends at the gate, with `not_started`.
- **D2. One probe serves a single entry and a chain, for both harnesses.** `_tester_probe` (the chain's selection-time probe, `controller/job.py`) uses `claude_selection_error` for a Claude entry instead of resolving the binary alone, and `_run_functional` runs the same probe for a one-entry chain, after `_functional_launch` and before `usage_gate`; a Codex route gets `codex_tester.selection_error` the same way. A one-entry chain that is unavailable ends through `unavailable_record` as before, with the probe's cause (`enforcement` for a probe refusal, per D1). In a longer chain a Claude entry whose host cannot run it (the role, the binary or the probe) is now skipped, as a Codex entry whose build is not verified already is, so the next entry is tried. The recorded skip cause stays `launcher_unavailable` (`controller/route_select.py:90`) for every entry; the probe's own text reaches the operator only as the error when every entry fails, so a skipped entry's reason does not appear when a later entry is chosen, and the documentation says so. Before this change the job reserved, started its record and ended at the gate with the next entry never tried. Causes that need a job or a sandbox (the clone not matching the manifest, no folder short enough for `TMPDIR`, a runtime root inside the operator's Claude home) still arise at launch and are named in the documentation as such.
- **D3. An unavailable tester is one stop record with no reservation.** Both exits of D2 return through `unavailable_record` and `_no_launch_record`, which persists one `jobs/<id>.json` (`controller/job.py:6043`, `:5144`) as before: a `GATE_BLOCKED` record that never held `STATUS_PLANNED` or `LAUNCHED`, with no `functional.launched`, no `functional.not_started` and no `usage` block, whose events are the single stop event. What changes is that the usage gate is never called, so the reservation history is empty. Tests show it with a gate that fails the test if called, and with the real usage store.
- **D4. The documentation follows the change.** `docs/common-problems.md` (the `functional_tester_unavailable` section) says the host causes are found before any usage is reserved; `docs/guide/automation.md` says the same where it lists the stop (`:498`) and states which causes remain launch-time (the clone not matching the manifest, no folder short enough for `TMPDIR`, a runtime root inside the operator's Claude home, and the refusals `build_sandbox` raises: a submodule, an untracked checklist, an existing sandbox folder), and rewrites the chain paragraph (`:550-561`), which this change makes false in three places: a Claude entry is also skipped for the role and the static probe, "Every other refusal happens after the spawn" no longer holds for the host causes, and "A chain of one selects nothing" now runs the probe; that paragraph also says the recorded skip cause stays `launcher_unavailable` and the probe's text appears only when every entry fails; the paragraph at `:550`, on a Codex route that ends at the stop, is checked as well; `docs/ROADMAP.md` marks C10i done and follow-up 67 resolved. The release note and release history belong to the docs pull request after the 1.27.7 release. The pull request closes #112.

## Out of scope

Reading the clone-mismatch and `TMPDIR` causes before admission (they need a job id and a built sandbox), moving the usage admission itself, and any change to the probe's checks or to `claude-build verify`.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The availability probe runs before usage is reserved | - | 2 | 1 |
| CP2 | The documentation and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The availability probe runs before usage is reserved

**Files:** `controller/review.py`, `controller/job.py`, `tests/test_functional_sandbox.py`, `tests/test_functional_lifecycle.py`, `tests/test_functional_select.py`, `tests/test_functional_codex_route.py`, `tests/test_protocol_job.py`, `tests/test_harness.py`

D1, D2, D3. The shared helper and `claude_selection_error` in `controller/review.py` (with the cause wrapping of D1); `_tester_probe` and the one-entry path of `_run_functional` in `controller/job.py`. Tests: `claude_selection_error` returns the `role_refused`, `no_binary`, and probe causes (a `which` without `bwrap`, a build not in the verified list) and `None` for an admitted host, and `launch_functional` raises the same causes through the helper, so a refusal gives the same cause at the selection and at the launch (`tests/test_functional_sandbox.py`); the probe seam raises the probe's real exception, `functional_review.static_probe` with a `which` that lacks `bwrap`, never `functional_review.unavailable(...)`, which carries a `cause` and would hide the crash B1 names; a job-level test that such a refusal ends at `functional_tester_unavailable` with `functional.cause` set (`enforcement`), for a one-entry chain and for a chain whose every entry fails; a one-entry chain on a host without `bwrap` ends there with the usage gate never called (a gate that fails the test if called, and the real usage store with an empty reservation history), one `GATE_BLOCKED` record that never held `PLANNED` or `LAUNCHED`, with no `functional.launched`, `functional.not_started` or `usage` block and the single stop event (`tests/test_functional_lifecycle.py`); the same for a Codex route whose build is not verified; in a chain, a Claude entry that fails the probe is skipped with cause `launcher_unavailable` and the next entry is chosen (`tests/test_functional_select.py`); a probe that passes at selection and fails at launch still ends at the gate with `not_started` (the existing launch-time behaviour, now pinned).

Seams. The tests that replace `review.launch_functional` with a fake (`tests/test_protocol_job.py`, `tests/test_functional_lifecycle.py`) also replace `review.claude_selection_error`, so they do not depend on the host's `bwrap`. `tests/test_functional_codex_route.py` routes to Codex, so its one-entry probe calls `codex_tester.selection_error`, which a Claude stub does not touch, and its fake `codex-bin` is neither resolved nor verified in `CodexLifecycle.step`: those tests also replace `codex_tester.selection_error` (answering `None`), and the tests that assert `no_binary` or an unverified build keep the real one. `tests/test_functional_select.py` runs through `CodexLifecycle.step` and relies on the real `_tester_probe` with a patched `resolve_binary` (`test_a_claude_entry_without_a_binary_falls_through_to_codex`, and the `no_binary` assertion at `:185`); it leaves `claude_selection_error` and `_tester_probe` real and fakes `functional_review.static_probe` instead, so the new skip-in-chain test exercises the probe.

`tests/test_harness.py:101-118` (`test_the_command_admits_the_functional_tester_launch_only`) pins that `recorded_digests` and `static_probe`/`identity_check` are reached only from `review.launch_functional`; D1 moves those calls into the shared helper, so the test is updated to pin the shared helper in `controller/review.py` as the only reader of the digests and the only caller of the probe, keeping the rule's meaning (nothing but the tester's checks reads the digests, and `job.py` reaches them only through `claude_selection_error`).

Done when: the new tests pass and the no-reservation and skip-in-chain tests fail against the base commit (checked once locally); the full suite is green but for the known Git 2.56 trailer tests.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The documentation and the roadmap

**Files:** `docs/common-problems.md`, `docs/guide/automation.md`, `docs/ROADMAP.md`

D4. The two pages and the roadmap follow D4; `docs/ACTIVE_MILESTONE.md` is updated at acceptance.

Done when: `python3 tools/check_docs.py` passes and the roadmap marks C10i done.
<!-- /CP2 -->
