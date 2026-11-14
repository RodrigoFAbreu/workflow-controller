# NS1: run the test runner's shards in an isolated PID namespace, #329 (Revision 2)

Work item: `workflow-controller-ns1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `1ffbde28b8309130033a0258f33f1c3034de5c37` ("chore: move this repository to Workflow 2.29.3 (W1) (#348)"), the tip of `main`, passed explicitly as `/milestone-plan 1ffbde28b8309130033a0258f33f1c3034de5c37`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: backlog item `workflow-controller-ns1` (`docs/backlog.yaml` order 87, the item `python3 scripts/workflow_backlog.py next` returns). Its description is `docs/items/workflow-controller-ns1.md`. It depends on nothing.
Released baseline: the latest tag. Everything changed is under `tools/`, `tests/` and `docs/` (outside the wheel), so the squash commit is a `chore:` and releases no new version.
Pull request title: `chore: run the test runner's shards in an isolated PID namespace so a stray kill(-1) reaches only the run (NS1, #329)`

## Goal

A test or a worker command that sends `kill(-1, SIGKILL)` (or `killpg(1, ...)`) must reach only the test run, never the desktop session (the crashes of 2026-11-09; PR #328 fixed the Controller's own signalling, not a test's). One mechanism: where the Controller's test commands run. `tools/run_tests.py`, the documented entry point for every test selection (`python3 -m unittest` names are accepted as selection names), starts each shard inside `unshare --user --map-current-user --pid --fork --kill-child --mount-proc`, so a signal to every process reaches only that namespace.

## What exists

- `tools/run_tests.py` `run_shards` starts each shard as `python3 tools/run_tests.py exec-shard ...` through `subprocess.Popen(..., start_new_session=True)` and, after the shard exits, finds leftovers by the `run_id/shard` marker (`ts.scan_marked_processes`, which reads `/proc/*/environ` of the host) and kills them. CI runs `exec-shard` directly and does not use `run_shards`.
- Verified on this host (util-linux 2.42.4): `unshare --user --map-current-user --pid --fork --mount-proc sh -c 'id -u; kill -9 -1'` keeps uid 1000, mounts `/proc` for the namespace, and `kill -1` finds no process. `--map-current-user` (not `-r`) keeps the invoking uid, so a root-in-namespace write to an unwritable test root (the artifact in the issue, `tests.test_runtime.EnsureRuntimeRootTest.test_unwritable_root_is_a_named_refusal`) is not introduced. The remaining artifact is that the namespace's init is the first child, which does not reap orphaned zombies (`tests.test_review.FakeLaunchTest.test_a_hung_review_is_killed_at_the_deadline_with_its_descendants`).
- `controller.runtime.signalable_group` already refuses group 1 for the Controller's own signals (PR #328). It is out of scope.

## Decisions

- **D1. One wrapper, in the runner.** `run_shards` builds the shard command through one function that prefixes the namespace launcher. The probe (`unshare ... true`, bounded, once per run) decides whether the host allows unprivileged user namespaces. The probe runs the builder's own argv with a trivial command (every D1 flag, including `--kill-child` and `--map-current-user`, which need util-linux 2.32 and 2.38), so an older `unshare` that lacks one flag fails the probe and falls back or refuses by D2; it never fails a shard.
- **D2. Strictness is one setting with three values** (`--namespace auto|require|off`, default from `CONTROLLER_TEST_NAMESPACE`, else `auto`): `auto` wraps when the probe succeeds and otherwise runs unwrapped and prints one notice naming the probe's reason; `require` refuses a run when the probe fails (exit via the runner's usage error); `off` never wraps. **Owner decision O1 (flagged, not finalized here): the default.** This plan recommends `auto` (a host without user namespaces, for example a hardened runner, can still run the suite) and ships `require` for anyone who wants the refusal.
- **D3. CI is unchanged.** CI calls `exec-shard` directly, on disposable runners, and stays inside its time budget. Only `run_shards` (local runs, `--serial` included) wraps.
- **D4. Namespace artifacts are fixed from evidence, in the tests.** A wrapped full run is read; each failure that the namespace causes is fixed where it is (a zombie-aware liveness check in the test, not a reaper in the runner, because a blanket `waitpid(-1)` would steal the runner's own children). A failure the namespace does not cause is not touched.
- **D5. Not in this item.** A refusal of a bare `python3 -m unittest` for Controller workers (the "refuse unwrapped" half) needs a per-repository disallow rule, since `routing.worker_disallowed_tools` is shared by every managed repository; and the guard harness that makes `os.kill` refuse pid 1 or lower. Both stay open as the owner's second decision in the item page (O2), and the docs tell workers to use `tools/run_tests.py`.

- **D6. Ctrl-C on a wrapped shard is a direct SIGKILL of its group (review round 1, I1).** The executor is PID 1 of the namespace, and a signal from outside the namespace to its init is dropped unless the init has a handler (`pid_namespaces(7)`; reproduced: SIGTERM to a wrapped `unshare` group left it running, only SIGKILL ended it). `exec-shard` has a SIGTERM handler only while a conformance lane runs (`_stop_lane_on_signals`), so the graceful path would wait the whole `INTERRUPT_GRACE_SECONDS` and end in SIGKILL anyway. Decision: `exec-shard` is **not** changed. For a wrapped shard `run_shards` sends SIGKILL to the shard's process group on interrupt, with no grace wait; SIGKILL of `unshare` ends the executor through `--kill-child`, and the kernel kills every other process of the namespace when its init dies, so nothing outlives it. The lane's graceful `lane.stop()` does not run on that path, which is acceptable because nothing can outlive the namespace. An unwrapped shard (`off`, the `auto` fallback) keeps the SIGTERM-then-SIGKILL path unchanged.
- **D7. The post-exit leak scan is weaker under the wrapper, by design (review round 1, I3).** When the namespace init exits, the kernel kills every other process of the namespace, so `finish()`'s `scan_marked_processes` finds nothing after a wrapped shard exits: a process that left the group, started after the executor's own scan, or survived a killed executor is killed silently instead of being reported and failing the shard. Accepted: locally the cleanup is stronger and the leak signal weaker, and CI (unwrapped) keeps the full signal and is where a leak fails a run. The executor's own in-namespace scan still runs and still reports. The guide page records this in ADR 0005's terms. The scan itself is unchanged and still works while a shard runs (host `/proc` reads the environ of a process in a child user namespace of the same user), which is what CP1 tests.
- **D8. A third namespace artifact: the lock probe answers `unknown` outside the init PID namespace (review round 1, I2).** `controller/lock.py` (lines 288 and 336) answers `unknown`, never `free`, when the reader is not in the init PID namespace, deliberately, because `/proc/locks` hides an entry whose taker cannot be seen. Every wrapped local run is outside the init namespace, so: `tests/test_scenario_kit.py` `KillPointTest` (asserts `lock-after-kill == "free"`, line 305; the scenario sandbox does not unshare PID, so its probe runs in the shard's namespace) is made namespace-aware, expecting `free` in the init namespace and `unknown` elsewhere (via `controller.lock.read_pid_namespace`); the `skipUnless(_INIT_NS, ...)` tests in `tests/test_observe.py` and the `unknown` branches in `tests/test_cli.py` and `tests/test_lock.py` already adapt and stay. Coverage shift, recorded on the guide page: wrapped local runs skip the init-namespace `free` tests; CI, which is unwrapped, runs them. The scenario kit's own nested `unshare --user --map-root-user` inside the shard's user namespace is checked by CP2's wrapped run. Any further file the wrapped run shows must change is added by a plan amendment, not silently.

## Checkpoints

<!-- CP1 -->
### CP1: the namespace wrapper and its strictness setting

Add the probe, the argv builder and the `--namespace` option (with `CONTROLLER_TEST_NAMESPACE`) to the runner, and route `run_shards` through the builder; `exec-shard` itself is unchanged. The probe result is computed once per run and printed in the run's header (`namespace: isolated` or `namespace: none (<reason>)`). Unit tests with a fake launcher: the argv carries every flag of D1 and the shard command unchanged after it; `auto` falls back with the notice, `require` refuses with the probe's reason, `off` never probes; `--serial` and `--replay` take the same path; the probe runs the builder's own argv, so a launcher that rejects one flag (a fake that fails on `--map-current-user`) fails the probe and not a shard (O1); the leak scan finds a marked process started inside a namespace **while the shard still runs** (D7; not after exit, when the kernel has already killed it); and, per D6, a live Ctrl-C test: a wrapped shard whose executor ignores SIGTERM ends within less than `INTERRUPT_GRACE_SECONDS` on SIGINT to the runner and leaves no process, next to the existing Ctrl-C tests in `tests/test_run_tests.py`, which are kept and pass unwrapped (`--namespace off`) and wrapped. A second test pins D7: a marked process left in a wrapped shard is gone after the shard exits and the post-exit scan reports nothing.

**Files:** `tools/run_tests.py`, `tools/test_shards.py`, `tests/test_run_tests.py`, `tests/test_test_shards.py`
<!-- /CP1 -->

<!-- CP2 -->
### CP2: a live proof and the namespace artifacts

A live test, skipped (naming the probe's reason) where the probe fails, starts a sacrificial sleeper outside, runs a shard-shaped child inside the wrapper that sends `kill(-1, SIGKILL)` to everything it can reach, and shows the sleeper survives and the child's own tree ends. Then run the full selection wrapped (`python3 tools/run_tests.py`) and read every failure: fix each one the namespace causes (the known ones are the unwritable-root test, expected unaffected under `--map-current-user`, the hung-review test's zombie, fixed with a zombie-aware check, and D8's `KillPointTest` lock assertion, made namespace-aware; the nested `unshare --user --map-root-user` of the scenario kit inside the shard is checked here), and record the others in the item page if they are not the namespace's. Known environmental failures (`tests.test_fake_codex_contract`) are not touched.

**Files:** `tests/test_run_tests.py`, `tests/test_review.py`, `tests/test_runtime.py`, `tests/test_scenario_kit.py`
<!-- /CP2 -->

<!-- CP3 -->
### CP3: the pages

`docs/guide/development.md` (the test runner section: the setting, the three values, the notice, why CI is unwrapped, bare `python3 -m unittest` is not wrapped, the Ctrl-C behaviour of D6, the weaker post-exit leak signal of D7 in ADR 0005's terms, and the coverage shift of D8: wrapped local runs skip the init-namespace lock tests and CI runs them), the item page (status, the two owner decisions O1 and O2, verification, what was found in CP2, the D6-D8 consequences), and `CLAUDE.md` (one line: run tests through `tools/run_tests.py`).

**Files:** `docs/guide/development.md`, `docs/items/workflow-controller-ns1.md`, `CLAUDE.md`
<!-- /CP3 -->

## Open decisions

None of `docs/TECHNICAL_DECISIONS.md`'s "Open decision" rows is touched. Two scope questions are the owner's and this plan does not finalize them: O1 (the default strictness, recommendation `auto`) and O2 (whether to add the worker-side refusal and the guard harness, recommendation: later, one item each).

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | Every shard of a local run starts inside an isolated PID namespace, with a setting for the strictness and a noticed fallback (#329) | CP1 |
| R2 | A stray `kill(-1)` in a test is shown to reach only the namespace, the interrupt and leak-scan behaviour is pinned (D6, D7), and the namespace's artifacts (D8 included) are fixed from a wrapped full run | CP1, CP2 |
| R3 | The pages and the item record the setting, the limits (D6-D8, the coverage shift) and the two open owner decisions | CP3 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The namespace wrapper and its strictness setting | - | 3 | 1 |
| CP2 | A live proof and the namespace artifacts | CP1 | 3 | 1 |
| CP3 | The pages | CP2 | 1 | 1 |
