# ADR 0010: Admission by capability, and decisions and outcomes from the Workflow's own protocol

Status: accepted (2026-10-03). See
`docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md` for the full
design record (work item `workflow-controller-orchestration-protocol-v1`,
`docs/ROADMAP.md` step C9, section 1.7). The code ships in 1.7.0. This document
records how the Controller admits a Workflow release by what it can do, where a
protocol target's decisions and outcomes come from, and what stays the
Controller's own. It amends [ADR 0006](0006-workflow-release-admission-and-per-release-contracts.md):
for a release with no entry in `workflow_contract.RELEASE_CONTRACTS` the pinned
per-release contract is replaced by the protocol; for 2.5.1 and 2.6.0 ADR 0006
stands unchanged. It adds no exit code: the two new errors,
`WORKFLOW_PROTOCOL_FAILED` and `WORKFLOW_PROTOCOL_UNSUPPORTED`, exit `20` like
`WORKFLOW_QUERY_FAILED`, and the table in
[ADR 0001](0001-controller-generation-1-architecture.md) stays the normative
exit-code contract. The operator's view is in
[the automation guide](../guide/automation.md) and
[the installation guide](../guide/installation.md).

## Context

Through 1.6.0 the Controller re-derived what the Workflow means. It copied the
phase set, mapped phases to commands (`decision.AUTOMATIC_TRIPLES`), kept
eighteen expected-outcome rows naming Workflow writers (`job.EXPECTED_OUTCOMES`),
read feedback, bundle manifests and commit trailers itself (`evidence.py`), and
admitted a Workflow only by exact release plus two pinned script digests
(ADR 0006). Every Workflow release therefore needed a Controller milestone.
Workflow 2.7.0 publishes Orchestration Protocol v1
(`scripts/workflow_protocol.py`): `describe`, `verify`, `next-action`,
`reconcile`, `record-external-result` and `resolve-artifact`. The Workflow owns
what the lifecycle means; the orchestrator owns how it is run.

## Decisions

### Admission by capability, in two ways

`managed_repo.inspect` has a two-way version gate. A release with a
`RELEASE_CONTRACTS` entry (2.5.1, 2.6.0) is **legacy mode**, checked and driven
exactly as in 1.6.0. Any other release is **protocol mode** when the
installation record's `managed` map lists `scripts/workflow_protocol.py` and
its `describe` answers protocol major `1`. Otherwise it is refused with
`UnsupportedWorkflowVersionError`: `no_protocol` for a release newer than every
supported line that ships no protocol script, `unsupported_protocol_major` for
a protocol that does not speak major 1, and the unchanged
`outside_supported_line` / `unvalidated_release` for a release inside the old
lines. A release the Controller never ran is therefore driven when it speaks
the protocol, including a later minor release that adds a module the protocol
imports, because the private copy below is the Workflow's own script set.
`describe`'s action ids that the Controller does not know are an `inspect`
advisory, never a refusal. Each step's protocol preflight is the Workflow's
`verify`: an unhealthy answer is a `workflow_unhealthy` gate, not a refusal
of the repository, since a failing `state_valid` can be transient.

What still couples the Controller to a Workflow release is only the closed
action-id table (`protocol_decision.PROTOCOL_ACTIONS`: an unknown id is
blocked, not run), `target_state.read`'s read of `WORKFLOW_STATE.json` for item
selection and `status`, and the protocol major.

### The protocol runs from a private copy, with no pinned digest

The Controller still executes repository-resident Workflow code, which is why
ADR 0006 pins two digests. For the protocol it pins none, since a pin per
release would put an exact-release list back into the Controller. It relies on
what already stands, and records what it ran:

- Workflow Manager's `verify` of the installation, part of `inspect`;
- a private copy of the Workflow's own script set: the exact `scripts/<name>.py`
  keys of the installation record's `managed` map, never a `_test` file, a
  directory listing or a glob, run under the ADR 0006 Git isolation, now shared
  by both modes (`workflow_contract.run_in_private_copy`). A managed symlink or
  missing file is refused by name; a script that is not Workflow's own is
  neither copied nor digested;
- an identity derived afresh for every call: the release `describe` reports and
  the path-to-digest map of the managed scripts (`protocol.identity`), recorded
  in each job so a run is auditable.

Every call passes `--protocol-major 1`. Every envelope and result is validated
against the vendored schema (`controller/protocol_schema.json`, sha256 pinned,
shipped as package data) by a stdlib validator for the schema's keyword subset.
The validator refuses a schema keyword outside that subset at load, and treats
`additionalProperties: false` and the minor-extensible enums as open for
documents, so a later minor protocol version validates. An unknown major,
action id, disposition, error code or an invalid envelope fails closed.

### Decisions come from `next-action`

For a protocol target the next action, its gates and its reasons come from the
Workflow's `next-action`, never from `evidence.decide`. `automatic` with a known
action id, a worker role the Controller knows and a non-`user_only` worker
launches. `human_gate`, `external_gate` and `blocked` are gates that carry the
Workflow's reason, remedy and alternatives. `complete` is the no-action
outcome. `validation` and any unknown disposition, action id or worker role are
blocked gates (`workflow_unknown_*`); gate policy is a later milestone (C10).

The command a worker receives is **rendered by the Controller** from
`PROTOCOL_ACTIONS` (`/<command> <work_item_id>`, plus the Controller's own base
for `plan.start`), never taken from the protocol's `invocation`, and an
`invocation` that differs from the rendering is blocked
`workflow_invocation_mismatch` before any launch. 1.6.0's committed-state gate
keeps its own scope (implementation phases, launch decisions, `COMPLETE` and
`SELF_REVIEWING` facts) for a protocol target. The decision is bound to a state
identity: before the `PLANNED` write and again immediately before the spawn the
Controller asks again (`next-action --expect-state-identity`, or an equal
no-item re-call for `plan.start`). A stale answer re-decides, at most three
times per step (`decision_unstable`), and a stale answer at the spawn is a
terminal `FAILED` `decision_stale_at_launch` with no worker.

### Outcomes come from `reconcile`

A protocol job's outcome is the Workflow's `reconcile` on the stored decision,
on the launch path and on `resume` alike. `progress` and `gate_reached` verify;
a successful `no_progress` ends `FINISHED` with `progress: "none"`; `invalid`
is `FAILED` `reconcile_invalid` with the reasons verbatim. A generic
`no_progress_repeated` guard keyed on the work item and action id replaces the
apply-relaunch bound. The release identity is re-derived first on both paths: a
changed release or managed-script digest map is a terminal `FAILED`
`workflow_release_changed`, with `reconcile` never called. A worker or
checkpoint that edits a managed `scripts/workflow_*.py` therefore ends every
such job that way, matching the legacy pin behaviour; it is not a defect. A
non-Workflow `scripts/extra.py` never enters the identity. `resume` of a
drifted installation (`managed_repo.inspect_for_resume`) ends a pending protocol
job whose managed-file digest map differs, comparing manifest and file bytes
with no script run, and re-raises the drift in every other case.

One Controller fact stays for completion: after a `progress` for
`implementation.checkpoint`, the committed-state check of 1.6.0 still requires
the checkpoint's completion at `HEAD` (`completion_not_committed_at_head`),
because the protocol's own proof (trailer commits, descent from the start
commit) is weaker.

### The protocol is authoritative where it differs from 1.6.0

Seven decision differences are observable and are held, by name, in the
checked-in table `tests/golden/protocol_vs_legacy_differences.json`:
D1 `REVISING_PLAN` with no applicable `REVISE` launches `/milestone-plan`, not
`/apply-plan-review`; D2 a `"1"` item at a phase the protocol blocks is a gate
where 1.6.0 launched; D3 `AMENDING_PLAN` `/milestone-plan` is automatic; D4
`APPLYING_REVIEW_FEEDBACK` on `"1"`/`"2.1"` items is automatic; and three launches
1.6.0 never made: D5 a `"1"`/`"2.1"` external implementation review with a
current `REVISE`, `BLOCK` or pin is applied, D6 the functional checklist is
prepared and committed, D7 the operator's functional-review findings are
applied. D6 and D7 add two routing roles, `prepare-functional-review` (inherits
the session's model) and `apply-functional-review` (Opus), to
`routing.ROLE_ROUTES` and `routing.ROLES` only, not
`ROLE_BY_COMMAND_STEM`, and `settings.TABLE_GENERATION` is 3.

### What stays the Controller's, and what stays unchanged

The repository preflight, branches and pull requests, worker spawning and
supervision, routing and telemetry, the lifecycle lock and the generic
execution facts stay the Controller's. 2.5.1 and 2.6.0 behave byte for byte as
in 1.6.0: a protocol job's record gains an optional `protocol` block, a legacy
record, `inspect`, `status` and `explain` output gain no key, and every existing
golden and the no-policy lifecycle golden are unchanged.
`record-external-result` and `resolve-artifact` are not used: a pasted manual
verdict is still recorded by the automatic `/record-manual-*` worker action.

## Consequences

- A later Workflow release that speaks protocol major 1 needs no Controller
  change unless it adds an action id; it is blocked, not run, until the table
  names it.
- The two new roles can be set under `routing.roles` in the settings file, and
  the table generation is 3. A 1.6.0 Controller sharing a file this release
  filled ignores a role it does not know, and its `settings clean` refuses a
  file of a newer generation.
- The Controller still parses `WORKFLOW_STATE.json` (`target_state.read`). Making
  `next-action`'s snapshot the only source, and retiring the legacy path once
  2.5.1 and 2.6.0 leave use, is the follow-up milestone.

## Alternatives rejected

- **Exact releases plus the protocol.** It would keep C9 from delivering its
  point: every release would again need a Controller milestone.
- **A digest pin per protocol release.** It puts an exact-release list back
  into the Controller; the recorded identity is compared instead.
- **Taking the command from the protocol's `invocation`.** The protocol's
  contract is to dispatch on `action.id` and `arguments`; the Controller
  renders its own command and blocks a mismatch.
- **Accepting the protocol's weaker completion proof.** Durability at `HEAD`
  stays a Controller fact.
- **Retiring the legacy path in this milestone.** 2.5.1 and 2.6.0 repositories
  are still in use.
