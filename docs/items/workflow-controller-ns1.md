# NS1: run the test runner's shards in an isolated PID namespace

Item id: `workflow-controller-ns1`.

Status: implementing (2026-11-14); CP1 and CP2 are complete and CP3 records the pages. Plan: [CONTROLLER_NS1_PLAN.md](../ai-workflow/CONTROLLER_NS1_PLAN.md) (revision 3). Source: [#329](https://github.com/RodrigoFAbreu/workflow-controller/issues/329), a follow-up to [PR #328](https://github.com/RodrigoFAbreu/workflow-controller/pull/328). Priority: medium-high.

## Background

On 2026-11-09 `killpg(1, SIGKILL)` crashed the desktop session. PR #328 refuses an unsafe group id at every place the Controller signals a process group, but it does not stop a test or worker command from sending a signal that ends the session by another route; a worker ran such a test on the host four times before anyone saw it.

## Scope

One mechanism: where the runner's tests run. `tools/run_tests.py` starts each namespace-safe shard inside `unshare --user --map-current-user --pid --fork --kill-child --mount-proc`, so a stray `kill(-1)` reaches only that namespace. `--map-current-user` keeps the invoking uid, so the root-in-namespace artifact of the issue (`EnsureRuntimeRootTest.test_unwritable_root_is_a_named_refusal`) is not introduced. The setting and its limits are on [Development and the test runner](../guide/development.md#the-test-runner) ("Namespace").

- `--namespace auto|require|off` (default `$CONTROLLER_TEST_NAMESPACE`, else `auto`): `auto` wraps when a probe succeeds and otherwise runs bare with one notice; `require` refuses; `off` never wraps.
- A live proof, `NamespaceKillProofTest`: a `kill(-1, SIGKILL)` inside the wrapper ends its own tree and spares a sleeper outside.

## What CP2 found

A wrapped and a bare full run (6621 tests, 8 shards) showed the namespace makes tests in 14 modules fail or skip: 34 failures and 44 skips wrapped, 5 failures and 39 skips bare. The causes are the lock probe answering `unknown` outside the init PID namespace (`controller/lock.py`, deliberately), `/proc` and zombie assumptions (the namespace's init does not reap), and one golden that regenerates the scenario catalogue. The owner chose to split (2026-11-14, plan amendment 0, D9): ship the wrapper and the proof now, wrap only namespace-safe shards, and list the other modules in `tools/test_shards.NAMESPACE_UNSAFE_MODULES`.

## Consequences (D6-D9)

- **D6, Ctrl-C.** A wrapped shard's group gets SIGKILL at once (a signal from outside to the namespace's init is dropped unless it has a handler); an unwrapped shard keeps SIGTERM then SIGKILL.
- **D7, leak signal.** The post-exit scan of a wrapped shard finds nothing, because the kernel kills the rest of the namespace when its init exits: cleanup is stronger, the leak report weaker. CI is unwrapped and keeps the full signal.
- **D8, coverage shift.** The init-namespace `free` lock tests skip when wrapped, so the modules holding them are listed as unsafe and run bare; CI runs them.
- **D9, unwrapped shards.** A shard that holds a listed module runs bare as a whole, with a notice; `require` refuses such a selection. The planner spreads modules over shards, so a **full or `--serial` local run wraps no shard** until the follow-up empties the list. A narrow selection (a test file, a module) is fully wrapped, which is what a worker runs.

## Decisions for the owner

- **O1, the default.** The plan recommends `auto` (a host without user namespaces can still run the suite) and ships `require` for anyone who wants the refusal. Not finalised by the plan.
- **O2, a second layer.** Whether to add the worker-side refusal of a bare `python3 -m unittest` (it needs a per-repository disallow rule, since `routing.worker_disallowed_tools` is shared by every managed repository) and a guard harness that makes `os.kill` and `os.killpg` refuse pid or group 1 or lower. Recommendation: later, one item each. The guide and `CLAUDE.md` tell workers to use `tools/run_tests.py` meanwhile.

## Follow-up

A backlog item that makes the suite namespace-clean (a zombie-aware liveness check in the tests, the lock tests made namespace-aware, the golden fixed) and empties `NAMESPACE_UNSAFE_MODULES`. It is added by a separate documentation change, not by this item's commits; until it lands the full run is unwrapped.

## Verification

CP1 and CP2: `python3 tools/run_tests.py tests.test_run_tests tests.test_test_shards`, with `--namespace off` and wrapped (`--namespace require`), and `tests.test_fake_claude_contract`. CP3: `python3 tools/check_docs.py`.

## Out of scope

Signalling inside the Controller itself (done in PR #328), the worker-side refusal and the guard harness (O2), and making the whole suite namespace-clean (the follow-up).
