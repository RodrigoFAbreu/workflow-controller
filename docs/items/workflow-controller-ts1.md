# TS1: state that the functional tester sandbox refuses the PID-namespace launcher

Item id: `workflow-controller-ts1`.

Status: implemented on the milestone branch, awaiting review. Plan: [`CONTROLLER_TS1_PLAN.md`](../ai-workflow/CONTROLLER_TS1_PLAN.md). Owner decision 2026-11-15. Complexity: low to medium. Priority: medium. Depends on [LT1](workflow-controller-lt1.md) only for ordering.

## Why

The Controller's functional tester sandbox forbids `unshare` ("Operation not permitted"), so every checklist flow that needs the PID-namespace launcher (`tools/run_tests.py --namespace require`, `NamespaceKillProofTest`, the wrap probe) fails in the automated functional round for an environmental reason. An operator must then rerun those flows by hand on the host and record them with `python3 scripts/workflow_protocol.py record-external-result --kind functional_evidence`. Seen on [NS1](workflow-controller-ns1.md): flows 1, 2, 4 and 6 failed this way. On w1 the tester also lacked `workflow-manager` on its `PATH`.

## What shipped

One mechanism: the limit is **stated once** in the text both testers' prompts and the checklist writer's note share (`functional_review.SANDBOX_LIMITS`, repeated word for word in `codex_bridge.SANDBOX_LIMITS`). A flow that needs the launcher tries it once; if it is refused the flow is `failed` with reason `environment` and the message quoted, with no retry and no `defect`, and the run ends `environment_limited`. The `flows_not_runnable` stop names the launcher and says to record one `functional_evidence` result per flow with the Workflow's protocol script (the pages give the exact `record-external-result --kind functional_evidence` command). A namespace-isolation flow passes only on evidence the namespace was used. The pages say the same: [Automation](../guide/automation.md), [Harnesses](../guide/harnesses.md) and [Development](../guide/development.md) ("The test runner", "Namespace").

## Mechanisms chosen and rejected (D1)

- **Chosen: mark namespace flows as not runnable by the tester**, in its cheapest form (state the limit from the start).
- **Rejected: allow the launcher in the tester sandbox.** It widens the tester's attack surface (an unprivileged user namespace grants a capability set in that namespace and exposes the kernel's namespace code to a model-driven shell), is not configurable from the Controller, and is the owner's decision (O1).
- **Rejected: a fake launcher.** It either repeats the refusal (no gain) or runs the command unisolated, which makes a `kill(-1)` proof pass for the wrong reason or sends a real `kill(-1)` at the tester's own sandbox, the hazard [NS1](workflow-controller-ns1.md) removed.

## Decisions for the owner

- **O1.** Whether `unshare` should ever be allowed in a tester. Recommendation: no. If yes, its own item with a security review.
- **O2.** Whether `workflow-manager` goes on the tester `PATH`. Recommendation: its own item, with a proof of what the tool may read and write inside the sandbox.

## Verification

Per checkpoint, the plan's selected tests through `python3 tools/run_tests.py`, the repository-wide guards, `python3 tools/check_docs.py`, `python3 scripts/workflow_backlog.py check` and `preflight-accept workflow-controller-ts1`. The single full self-review run is recorded in the implementation bundle.
