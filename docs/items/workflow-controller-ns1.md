# NS1: run worker test commands in an isolated PID namespace

Item id: `workflow-controller-ns1`.

Not yet planned. Run the commands a Controller worker uses to test code inside an isolated PID namespace. **Needs an owner decision on strictness: wrap automatically, or refuse an unwrapped test command.** Source: [#329](https://github.com/RodrigoFAbreu/workflow-controller/issues/329), a follow-up to [PR #328](https://github.com/RodrigoFAbreu/workflow-controller/pull/328). Complexity: low to medium. Priority: medium-high.

Follow-up: [NS2](workflow-controller-ns2.md) makes the full suite namespace-clean.

## Background

On 2026-11-09 `killpg(1, SIGKILL)` crashed the desktop session. PR #328 refuses an unsafe group id at every place the Controller signals a process group, but it does not stop a test or worker command from sending a signal that ends the session by another route; a worker ran such a test on the host four times before anyone saw it.

## Scope

One mechanism: where worker test commands run. `python3 -m unittest ...` and `tools/run_tests.py ...` run inside `unshare -Urpf --mount-proc` (verified on the host), so a stray `kill(-1)` reaches only processes inside it; or the worker's sandbox refuses an unwrapped test command. Namespace artifacts to handle: root inside the user namespace can write unwritable test roots (`EnsureRuntimeRootTest.test_unwritable_root_is_a_named_refusal`), and the namespace's init does not reap zombies (`FakeLaunchTest.test_a_hung_review_is_killed_at_the_deadline_with_its_descendants`).

## Decision for the owner

Strictness: wrap automatically, or refuse unwrapped. Also whether to add a second layer, a guard harness that makes `os.kill` and `os.killpg` refuse pid or group 1 or lower in every test run.

## Out of scope

Signalling inside the Controller itself (done in PR #328) and the guard harness unless the owner asks for it.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
