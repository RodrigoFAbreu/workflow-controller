# ADR 0015: Launch gate-policy validations, and keep a protocol minor release from refusing the answer

Status: accepted (2026-10-17). Builds on
[ADR 0010](0010-orchestration-protocol-admission-by-capability.md) (a
Workflow is admitted by the protocol it speaks). The code is
`controller/protocol_decision.py`, `controller/protocol_schema.py` and
`controller/job.py`; the operator's view is
[Protocol mode](../guide/automation.md#protocol-mode-workflow-27-and-later).

## Context

From Workflow 2.8 the three approvals follow a gate policy, and the Workflow
answers an automatic gate with the disposition `validation` and an action id
ending in `.satisfy`. Controllers up to 1.13.1 did not know that
disposition, so a run stopped at every approval and a person ran
`/satisfy-gate` by hand. Separately, a closed enum in the vendored schema
refused a whole answer when a protocol 1.x minor release added a value, such
as the `warn` check status of protocol 1.1, although a minor release only
adds.

## Decision

1. **The Workflow decides and the Controller only launches.** A `validation`
   answer is launched when its action id is one of `plan.satisfy`,
   `implementation.satisfy` or `acceptance.satisfy`, the table pairs that id
   with the disposition given, the worker is not `user_only` and its role is
   `validator`. Anything else is blocked as an unknown value is
   (`workflow_unknown_action`, `workflow_unknown_worker_role`). The
   Controller reads no `GATE_POLICY.json` and evaluates no requirement; that
   is `/satisfy-gate`'s own check.
2. **The validator is a role of its own**, `satisfy-gate`: Opus at `xhigh`,
   single-agent, because it runs a transaction that commits and must not
   delegate it. It joins the settings file's role names (table generation 9).
   `pr.apply_review` stays unlaunched until its own step.
3. **A refused validation is a stop, not an approval.** Nothing is written and
   the phase is unchanged; the step is recorded as no progress, so the loop
   guard blocks the third launch. `approval_recorded` is sent only when the
   job's phase moved, not by the reconcile class, since a real approval can
   be classed `gate_reached`.
4. **A rule per enum.** `protocol_schema.ENUM_RULES` classifies all twenty
   enum locations as `identity` (only the protocol name), `advisory`,
   `blocked` (the step ends at a gate naming the value,
   `workflow_unknown_value`) or `ignored`. The open set is derived from it,
   and a test over the vendored schema fails when a location has no rule.
5. **A stop that needs no person sends no request.** A `GATE_BLOCKED` job
   whose recorded disposition is `blocked` sends `lane_blocked`, not
   `user_gate`; human and external gates keep `user_gate`.

## Consequences

- A run passes plan approval, technical approval and, when its requirements
  are met, acceptance without a person. Acceptance still stops at the
  functional evidence (row 38e) and pull request rows (38d, 38f, 38g) until
  the steps that own them land; the 38f stop is cleared by running
  `/satisfy-gate acceptance <id>` by hand.
- A Workflow minor release that adds an enum value is no longer a Controller
  release. A new enum location with no rule fails the schema test, so it
  cannot be missed.
- The measured use of each field decided its rule; a field the Controller
  later reads for a decision must change its rule in the same change.
