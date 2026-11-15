# TS1: let the functional tester sandbox run the PID-namespace launcher (unshare)

Item id: `workflow-controller-ts1`.

Not yet planned. Owner decision 2026-11-15. Complexity: low to medium. Priority: medium. Depends on [LT1](workflow-controller-lt1.md) only for ordering.

## Why

The Controller's functional tester sandbox forbids `unshare` ("Operation not permitted"), so every checklist flow that needs the PID-namespace launcher (`tools/run_tests.py --namespace require`, `NamespaceKillProofTest`, the wrap probe) fails in the automated functional round for an environmental reason. An operator must then rerun those flows by hand on the host and record them with `python3 scripts/workflow_protocol.py record-external-result --kind functional_evidence`. Seen on [NS1](workflow-controller-ns1.md): flows 1, 2, 4 and 6 failed this way. On w1 the tester also lacked `workflow-manager` on its `PATH`.

## Scope

One mechanism, chosen in planning:

- allow the tester sandbox to run the unprivileged user-namespace launcher (`unshare -Urpf --mount-proc`) with the same read-only and no-network protections; or
- give the tester a fake namespace launcher; or
- mark namespace flows as operator-run in checklists from the start.

Also consider putting `workflow-manager` on the tester `PATH`.

## Decision for the owner

Whether `unshare` should be allowed in the tester at all is open. It is a security question: an unprivileged user namespace inside the tester.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
