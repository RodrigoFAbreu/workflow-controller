# NS2: make the full test suite pass inside the PID namespace

Item id: `workflow-controller-ns2`.

Not yet planned. Make the whole test suite pass when run inside the isolated PID namespace, so a worker can wrap the full run. A follow-up of [NS1](workflow-controller-ns1.md). Complexity: medium. Depends on NS1 shipping.

## Background

Owner decision 2026-11-14: NS1 is split. NS1 ships the PID-namespace wrapper (`unshare -Urpf --mount-proc`) and the `kill(-1, SIGKILL)` live proof, and wraps only the runs that are namespace-safe. NS2 makes the full suite namespace-clean.

Under the wrapper about 35 tests failed in more than 9 modules:

- The lock probe reads `unknown` outside the init PID namespace: `test_scenario_kit` `KillPointTest`, the `kill_and_resume_*` scenarios in `test_scenarios`, `test_resume` `SupervisorProbeTest`, `OrphanWorkerTest` and `StartingRecordTest`, `test_lifecycle_orchestration` `LifecycleLockLifetimeTest`, `test_lock` `InheritedDescriptorTest`, `test_run_scenarios` `SubsetDeterminismTest`, `test_catalogue_determinism`, `test_trunk_preflight` `NoPolicyGoldenTest`.
- One golden, `test_protocol_lifecycle` `GoldensUnchangedTest` (`no_policy_lifecycle.json`), probably the same `lock:unknown` artifact.
- PID and `/proc` assumptions: `test_worker` `LivenessProcessTestRealTest`, `OwnershipTest`, `OwnershipProvenanceTest` and `LivenessZombieTest`; `test_fake_claude_contract` `HangChildPidFileTest` and `OrphanWatchdogTest`; `test_review` `FakeLaunchTest` (zombie).

## Scope

One mechanism, grouped by cause: one namespace-aware lock expectation, one zombie- and PID-aware helper, and a regenerated golden if it is the lock artifact. Then the worker may wrap the full run.

## Out of scope

The 3 known environmental `tests.test_fake_codex_contract` failures (the fake Codex reads the host's `~/.codex` config).

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
