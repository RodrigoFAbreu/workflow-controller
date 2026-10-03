# The Controller on Workflow Orchestration Protocol v1: decisions first, then outcomes (Revision 10)

Work item: `workflow-controller-orchestration-protocol-v1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `97b85f04cb62e8f483f7e76bf68a280bdc8c9d4b` ("docs: 1.6.0 release notes and the new
Controller order (#20)"), the tip of `main` when this plan was written, passed explicitly as
`/milestone-plan 97b85f0…` (after a squash merge the next item is planned from `main`'s head with
the base passed explicitly, `docs/guide/milestone-branches.md`). The previous milestone
(`workflow-controller-auto-merge-release-wait`, C4) was accepted at `225998c`, squash-merged by
PR #18 (`f2ca24d`) and released as 1.6.0. PR #20 added the 1.6.0 notes and the new order. Neither is
this milestone's work.
Lifecycle authority: this repository's installed Workflow **2.6.0**. Nothing in this milestone
installs Workflow 2.7.0 here (decision 7).
Roadmap slot: `docs/ROADMAP.md` step **C9** of "At a glance", section **1.7**. The user put C9 first
after C4 on 2026-10-02 (Workflow 2.7.0, step W1, is published).
Released baseline preserved: `workflow-controller 1.6.0` (`v1.6.0`). This milestone ships as the
minor release **1.7.0**, derived by `main.yml` from the `feat:` pull request title below.
Pull request title: `feat: drive Workflow through Orchestration Protocol v1, decisions then outcomes`

## Goal

Through 1.6.0 the Controller re-derives what the Workflow means. It copies the phase set, maps
phases to commands (`decision.AUTOMATIC_TRIPLES`), keeps eighteen expected-outcome rows that name
Workflow writer calls and command-file line numbers (`job.EXPECTED_OUTCOMES`), reads feedback,
bundle manifests, the review ledger and commit trailers itself (`evidence.py`), and admits a
Workflow only by exact release plus pinned script digests (ADR 0006). Every Workflow release has
therefore needed a Controller milestone, and Workflow 2.7.0 is refused today.

Workflow 2.7.0 publishes Orchestration Protocol v1 (`scripts/workflow_protocol.py`,
`docs/ai-workflow/ORCHESTRATION_PROTOCOL.md`): `describe`, `verify`, `next-action`, `reconcile`,
`record-external-result` and `resolve-artifact`. The Workflow owns what the lifecycle means; the
orchestrator owns how it is run. This milestone makes the Controller an orchestrator in that sense,
in the order the roadmap names:

1. **Admission by capability.** A target whose Workflow answers `describe` with protocol major 1 is
   admitted through the protocol, whatever its release number. 2.5.1 and 2.6.0 keep their ADR 0006
   contracts, unchanged.
2. **Decisions first.** For a protocol target, the Controller's next action, its gates and its
   reasons come from `next-action` only. The decision is bound to a state identity, and the
   identity is checked again immediately before a worker is launched.
3. **Then outcomes.** For a protocol target, whether a finished worker made progress comes from
   `reconcile` only, replacing the expected-outcome rows for those targets, on the launch and the
   `resume` path alike.

The Controller keeps what is its own: the repository preflight, branches and pull requests, worker
spawning and supervision, routing and telemetry, the lifecycle lock, and the generic execution
facts (a worker exited, a worktree is clean). Nothing of the lifecycle's meaning is kept for a
protocol target, with one named exception: the *committed-state facts* of I8, which keep 1.6.0's
protection that a checkpoint completion is durable at `HEAD`.

## Non-goals

- Changing 2.5.1 or 2.6.0 behaviour. The legacy path (`decision.py`, `evidence.py`, the
  expected-outcome rows, `workflow_contract.py`) stays byte-for-byte as 1.6.0 for those releases.
  Retiring it is a later milestone, after those releases leave use (decision 5).
- `record-external-result` and `resolve-artifact`. A pasted manual verdict is still recorded by the
  `/record-manual-*` worker command the protocol names as an automatic action
  (`plan.record_external`, `implementation.record_external`). Ingesting a verdict directly belongs
  with C7's review harness and C10's evidence kinds (decision 4).
- Installing Workflow 2.7.0 in this repository or any other, and any change to the Workflow or the
  Manager (their lanes own them).
- Gate policy (`validation` disposition, automatic approvals, Workflow 2.8): C10. The Controller
  here treats an unknown disposition as blocked, and `validation` as not yet driven.
- Removing `target_state.read`. The Controller still reads `WORKFLOW_STATE.json` to select a work
  item and to show `status`; a Workflow state schema change would still reach it. This is the
  largest remaining coupling and is recorded as the first follow-up (decision 5).
- Concurrency across worktrees, notifications (C5), usage budget (C8).

## Investigation: what the protocol gives and what the Controller does today

Read at base `97b85f0`; the protocol was also run read-only against this repository's checkout
(`describe`, `next-action`, `verify` from the Workflow repository's `payload/scripts`), which
produced the envelopes below.

### The protocol (Workflow 2.7.0, protocol `1.0`)

- One stdlib script, run as a subprocess, one JSON envelope on stdout, exit `0` ok, `3` a refusal
  with a stable code, `2` invalid request, `1` internal error. Every read takes a shared lock on
  the state lock file and writes nothing; only `record-external-result` writes.
- `describe` reads nothing from the repository. Measured: `ok: true`, `workflow_release: 2.7.0`,
  protocol `1.0`, operations `describe, next-action, reconcile, record-external-result,
  resolve-artifact, verify`.
- `next-action --work-item ID [--expect-state-identity HEX]` returns a decision: `basis` (state
  identity, phase, head, checkpoint statuses), `snapshot`, catalogue `row`, `disposition`
  (`automatic`, `validation`, `human_gate`, `external_gate`, `blocked`, `complete`), `action`
  (`id`, `arguments`, rendered `invocation`, `worker` role/fresh_session/independent_of/user_only,
  `allowed_results`), `satisfied_by`, `alternatives`, `reason`. Measured on this repository's own
  new item (just routed, no registry yet): row `7`, `automatic`, `plan.author`.
- `reconcile --decision FILE` classifies what an `automatic` action did: `progress`,
  `gate_reached`, `no_progress`, or `invalid` with reasons (`state_invalid`, `illegal_edge`,
  `checkpoint_completion_unproven`, `bundle_rejected`, `plan_review_not_bound`,
  `result_not_allowed`), plus the new `next` decision. Completion is reported by `next-action`
  (row 40, `complete`), never by `reconcile`.
- `verify` reports `healthy` and seven named checks. Measured against this checkout it reported
  `healthy: false`: `state_valid` failed because the item was routed and its registry file did not
  exist yet, and `installation_release_matches` failed because the script bytes were 2.7.0 and this
  repository's installation record says 2.6.0. Both are the protocol being strict, not defects, and
  both shape the design (design A.3).
- The consumer obligations (spec section 8) are the contract this plan implements: pass
  `--protocol-major 1` on every call; validate every envelope and `result` against the schema and
  ignore unknown fields; fail closed on an unknown action id, disposition, error code, governing
  version or phase; dispatch on `action.id` and `arguments`, never on `invocation`; honour `worker`;
  run `automatic` actions only, never a gate's action or an alternative; check the identity before
  launching; hand the executed decision back to `reconcile` verbatim; stop at `complete`.
- The JSON Schema (`orchestration-protocol-v1.schema.json`, 14 KB) uses only `$schema`, `$id`,
  `title`, `description`, `$ref`, `$defs`, `type`, `enum`, `required`, `properties`, `items`,
  `additionalProperties`: a stdlib validator for that subset is about a hundred lines, and the Controller has no dependencies
  (`pyproject.toml`: `dependencies = []`).

### What the Controller does today (1.6.0)

- **Admission** (`managed_repo.py`): `VALIDATED_WORKFLOW_RELEASES = {2.5.1, 2.6.0}`, with
  `SUPPORTED_WORKFLOW_LINES` as a pre-filter; `inspect` runs five checks including Workflow
  Manager's `verify`. 2.7.0 is refused as `outside_supported_line`.
- **Contracts** (`workflow_contract.py`): one per release; for 2.6.0 two queries run from a private
  copy of the two scripts whose sha256 the Controller pins, with the Git isolation of ADR 0006
  (filters, hooks, fsmonitor, submodules refused). Timeout 120 s. Exit 20
  (`WORKFLOW_QUERY_FAILED`, `WORKFLOW_RELEASE_CHANGED`).
- **Decision** (`decision.py`, `evidence.py`): `evidence.decide` runs the rejected-bundle,
  publication-status, bundle-coherence and uncommitted-state gates, then per-phase handlers, then
  `decision.decide`. The result is a `Decision` (`action`, `automatic`, `gate`, `declined`,
  `reason`) that `job.execute_step` and `cli explain` consume.
- **Outcome** (`job.py`): `ExpectedOutcome` rows keyed by `(phase, governing version, command
  token)`; `_verify_transition` requires the worker outcome, a post-phase in `to_any_of`, a
  predicate and a postcondition, all read from disk; `_checkpoint_completion_failure` proves a
  same-phase checkpoint completion against the state committed at `HEAD`. `resume` reconciles a
  launched job under the release its record carries.
- **Manual verdicts**: the Controller writes no feedback. It launches the `/record-manual-*`
  worker after `evidence.evaluate_manual_*_admissibility`.
- **Pins**: goldens `plan_stage_decisions[.2.6.0].json`, `external_implementation_review_
  decisions[.2.6.0].json`, `no_policy_lifecycle.json`; suites `test_decision`, `test_evidence`
  (4.6 k lines), `test_job`, `test_workflow_contract`, `test_workflow_release_migration`,
  `test_workflow_releases`.

### Differences the protocol makes, measured against the catalogue

The protocol is authoritative for a protocol target even where it differs from 1.6.0's own choices.
The list below was taken from the published catalogue (`ORCHESTRATION_PROTOCOL.md` rows 4-16, 25-38)
against the 2.6.0 golden `plan_stage_decisions.2.6.0.json`, the implementation-stage handlers and the
functional-review handler. It is stated by *class* (phase, governing version, scenario family) and is
split in two (round 2, G1), because the comparison of C.7 can only measure what a decision shows:

**Observable decision differences** (the comparison measures each on C.7's dimensions: decision kind,
launch command token, `user_only`; each is asserted by name in CP3, listed in the guide and in
decision 3):

- **D1. `REVISING_PLAN` (`2.1`/`2.2`) with no applicable `REVISE`** (no feedback, an `APPROVE`, a
  withdrawal; golden scenarios `coherent_no_feedback`, `nothing_on_disk`, `coherent_local_approve`,
  `withdrawn_with_quarantine`): 1.6.0 launches `/apply-plan-review`; the protocol (row 9) launches
  `plan.author`, i.e. `/milestone-plan`. The command token changes.
- **D2. A `"1"` item at a phase where 1.6.0 launches and the protocol is `blocked`** (row 4 marks
  `REVISING_PLAN`, `SELF_REVIEWING_IMPLEMENTATION`, `AWAITING_LOCAL_PLAN_REVIEW`, ... illegal at
  `"1"`; row 6a, `v1_state_not_advanced`, blocks `PLANNING`, `AMENDING_PLAN` and `IMPLEMENTING`): a
  launch becomes a gate. Only the cases where 1.6.0 launches are observable; where 1.6.0 declines or
  gates, both sides are non-launch (N4).
- **D3. `AMENDING_PLAN` `/milestone-plan` is `automatic`** (`plan.author`, row 7) at `2.1`/`2.2`,
  which closes the follow-up that `run` declined it with exit 15 (ROADMAP carried-forward item).
- **D4. `APPLYING_REVIEW_FEEDBACK` at `1`/`2.1`** is `automatic` (`/apply-implementation-review`);
  1.6.0's static gate is more conservative (the protocol's own plan, OD-W1-8).
- **D5. `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` at `1`/`2.1` with a current `REVISE`, `BLOCK` or
  pin** (rows 31/32): the protocol makes `implementation.apply_review` (`/apply-implementation-
  review`) **automatic**. 1.6.0's `_decide_awaiting_external_implementation_review_legacy`
  (`controller/evidence.py:1683-1736`) is report-only for every case: `AUTOMATIC_TRIPLES`
  (`controller/decision.py:235-256`) has no `1`/`2.1` triple for this phase, and
  `tests/golden/external_implementation_review_decisions.json` pins all 13 cases `automatic: false`.
  A new launch: the Controller applies a `1`/`2.1` external implementation review on its own.
- **D6. `AWAITING_FUNCTIONAL_REVIEW` with no current checklist evidence** (row 37): the protocol
  makes `functional.prepare` (`/prepare-functional-review`) **automatic**. 1.6.0 gives a gate
  (`evidence.py:2077-2097`), and `DELIBERATELY_NOT_SELECTED_COMMANDS` (`decision.py:411-414`) says
  it "never launches it". A new launch: the Controller writes and commits the checklist unprompted.
- **D7. `AWAITING_FUNCTIONAL_REVIEW` with an unconsumed `FUNCTIONAL_REVIEW.md`** (row 38): the
  protocol makes `functional.apply_findings` (`/apply-functional-review`) **automatic**. In 1.6.0
  the dispatch rule declines it (`evidence.py:2116-2123`, `decision.py:407-410`). A new launch: the
  Controller applies the operator's findings with no prompt.

D5-D7 are the milestone's most visible behaviour changes (D1 and D3 are the others) and are called
out in decision 3, the release notes and `docs/guide/automation.md`. D6 and D7 also need two routing
roles that do not exist today (C.3).

**Non-observable or outcome-level notes** (not rows of the difference table; each is tested where
it lives, and the CP3 test asserts the table holds *only* the observable set above):

- **N1. Missing or stale bundle** (rows 25a/25b/16a/30a): `blocked` with a remedy, where 1.6.0 gives
  its own gate text. Non-launch on both sides. The Controller surfaces the protocol's reason text
  and alternatives verbatim in its gate (gate-text test, CP3).
- **N2. Completion durability** (E.3): the protocol's proof (trailer commits, descent from the
  recorded start commit) is weaker than 1.6.0's "completion committed at `HEAD`" check; the
  Controller keeps that check as a committed-state fact, so the observable difference is only in
  which layer names the failure. An outcome, not a decision: tested in CP5 (E.3).
- **N3. Gate versus blocked.** 1.6.0's `Decision` has no `blocked` kind: every non-launch is a
  `HumanGate`. The protocol's `blocked` and the gate dispositions both map to a `HumanGate` (C.2),
  so this is not observable; the comparison treats a legacy `HumanGate` as "gate or blocked" (C.7).
- **N4. `AWAITING_PLAN_APPROVAL` at `"1"` and the other illegal-at-`"1"` phases where 1.6.0 does not
  launch**: the plan-approval gate becomes `blocked`
  (`phase_not_legal_for_governing_version`). Non-launch on both sides; only the gate text changes
  (gate-text test, CP3).

## Invariants

- **I1 -- legacy targets are unchanged.** A target admitted through a 2.5.1 or 2.6.0 contract
  takes exactly the 1.6.0 code path. Every *behaviour* test and every golden passes unchanged, and
  the no-policy golden differs only where CP2/CP4 add a record field that is absent for legacy jobs
  (the new `protocol` block is written only for protocol jobs, so the golden is byte-identical).
  Exactly these pins change on purpose, because C.3 adds two routing roles (round 3, H2): the
  role-set assertions in `tests/test_routing.py` (`ROLES`, the `ROLE_ROUTES` keys) and
  `tests/test_settings.py` (`V1_ROLES`, and `TABLE_GENERATION == 2` becoming `3`). They pin the
  closed role set and the table generation, not legacy behaviour. `ROLE_BY_COMMAND_STEM` and
  `test_every_launchable_command_has_a_role` (`tests/test_routing.py:92`) are not touched.
- **I2 -- the Workflow decides, the Controller never overrides.** For a protocol target the
  Controller launches a worker only for a decision with disposition `automatic` and an action id it
  knows. It never launches a gate's action, an alternative, a `user_only` action, or an action id
  it does not know, and it never turns a `blocked` or gate decision into a launch. The existing
  `_assert_not_user_only` scan stays as a second, independent layer in front of every spawn.
- **I3 -- unknown is blocked.** An unknown protocol major, action id, disposition, error code,
  governing version or phase, or an envelope that fails the schema, is a gate or refusal, never an
  action and never success (consumer obligations 1-3).
- **I4 -- a decision is current when it launches.** Immediately before a protocol job's worker is
  spawned the Controller calls `next-action --expect-state-identity <basis.state_identity>`; a
  `stale_decision` re-decides (bounded, three times per step) and never launches the old decision.
- **I5 -- the executed decision is what is reconciled.** The job record stores the decision as
  received, byte for byte, and `reconcile` receives that stored document, on the launch path and on
  `resume`. The Controller never edits a decision between receiving and reconciling it.
- **I6 -- Workflow code is run isolated and recorded.** The Workflow's own script set -- the
  `scripts/<name>.py` entries (exactly one path segment, K3) of the installation record's `managed` map (`.workflow-manager/installation.json`)
  except `<name>` ending in `_test` -- is copied once each into a private directory the Controller owns (**one copy per operation**: every
  protocol call, including each `reconcile`, derives the identity afresh and runs from its own copy, K1), and
  `workflow_protocol.py` is run from that copy with `sys.executable -B -E -s` under ADR 0006's Git
  isolation, stdin closed and a timeout; the target's own `scripts/` is never on `sys.path`. The file
  set is **taken from the installation record, never from a list the Controller keeps and never from
  a directory listing** (round 3, H1; round 4, J1): the protocol script puts its own directory on
  `sys.path` and imports its siblings (2.7.0: `workflow_fingerprint`, `workflow_state`,
  `workflow_test_harness`), so a Workflow release that adds a module the protocol imports (protocol
  `1.1`'s `workflow_gate_policy.py`, `workflow_forge.py`) works with no Controller change, because
  the Manager's list changes with the release. `scripts/` is shared with the product, which may keep
  its own Python files there; a file the record does not list is never copied, digested, compared or
  refused, however a worker edits it. The sha256 of every copied file, as a path-to-digest map, and
  the reported `workflow_release` are recorded in the job record; **that map plus the release is the
  release identity**, and it is compared with the job record's before every call that judges that
  job's outcome (B.3, E.1), and with the invocation's admitted identity before every decision (B.3, M1): a worker never changes the code that classifies its own work (K1). A listed file that is missing or is not a regular file (a symlink, a directory)
  is refused, naming it; a file that is not listed is not examined. The Controller does not pin
  digests for protocol releases (decision 2).
- **I7 -- Controller-owned facts stay Controller-owned.** Branch and pull-request gates, the
  lifecycle lock, release and merge records, routing and telemetry are not asked of the protocol.
  The protocol is never asked about, and never told of, a branch.
- **I8 -- no lifecycle file is read by the protocol path, with one named exception.** The protocol
  path reads no `REVIEW_FEEDBACK.md`, manifest, ledger or trailer and hard-codes no `.ai-review/`
  path (a static test over the new modules pins it). The Workflow state the Controller still reads
  is `target_state.read`'s, for item selection and display (decision 5), and the **committed-state
  facts**: 1.6.0's `evidence.uncommitted_implementation_state` (a working-tree `COMPLETE`
  checkpoint, or `SELF_REVIEWING_IMPLEMENTATION`, that `HEAD` does not record), reused unchanged
  and applied only as C.5 and E.3 state. That is the only place a protocol target has the Controller
  interpret checkpoint fields; it is a Controller execution fact (a worker never leaves a
  completion uncommitted), not a lifecycle decision, and the static test allow-lists exactly that
  call.
- **I9 -- the exit-code table is unchanged.** Protocol failures use exit 20 with the new
  error codes `WORKFLOW_PROTOCOL_FAILED` and `WORKFLOW_PROTOCOL_UNSUPPORTED`; ADR 0001's table is
  still the contract.

## Design

### A. The protocol client (CP1)

A. 1 **`controller/protocol.py`** (new, after `workflow_contract` in the dependency order that
`tests/test_package_structure.py` asserts). It runs one operation and returns a typed result:
`run(root, operation, args, *, input_file=None, timeout) -> Envelope`. It always passes
`--protocol-major 1` and `--repo-root <root>`, takes the Controller's existing private-copy and Git
isolation (`workflow_contract._git_isolation`, `_PreflightGit`; refactored to be shared, not
copied), and reads stdout as exactly one JSON document.

A. 1a **The private copy is the Workflow's script set** (H1, J1). `protocol.private_copy(root,
managed)` takes the qualifying keys of the installation record's `managed` map (the Manager-owned
list, already read by `inspect`, sorted). **A key qualifies only when it is exactly `scripts/<name>.py`**
(one path segment; no `/` in `<name>`, no `..`, no absolute or empty segment) **and `<name>` does not end in
`_test`** (K3): the key is matched by that structural rule, never by a glob (`*` would cross `/`),
and a destination path is built only from a qualifying key, so a `scripts/pkg/mod.py` or traversal
key (`scripts/../x.py`) is never copied, never written outside the private directory, and never
digested (it stays inside the stated non-recursive limit). It reads each once from the target and
writes it into the private directory with the sha256 recorded: the map is
`{"scripts/<name>.py": "<sha256>"}`. Only listed files are touched: a product's own `scripts/extra.py`,
an untracked scratch file or a non-Workflow symlink is neither copied nor compared nor refused. A
listed file that is missing or not a regular file refuses, naming the path. If
`scripts/workflow_protocol.py` is not a qualifying key the target is not a protocol target (B.1); the same holds if `managed` is absent or not an object, reason `no_protocol` (`_read_manifest` does not validate the field, so the Controller does). The
copy is non-recursive: a future Workflow that ships `scripts/<pkg>/` has its package files listed by
the Manager under other paths and takes the failure path below until the Controller copies them (a
stated limit, not a silent one). A protocol script that fails to start because a module it imports is
not in the copied set is reported as `WORKFLOW_PROTOCOL_FAILED` whose detail **names the missing
module**. The Controller observes only a subprocess, not an exception (J2): the siblings are imported
at module level, before `main`'s error handler (`workflow_protocol.py:2365`), so the failure is exit
`1`, empty stdout and a traceback on stderr. The module is parsed from the last stderr line
(`ModuleNotFoundError: No module named '<x>'`, surfaced today through `_last_line(stderr)`); when the
line does not match, the detail is the bare failure plus the stderr tail. Nothing imports from the
target's `scripts/`: only the private directory is on the path.

A. 2 **Validation.** `controller/protocol_schema.py` is a vendored copy of the 2.7.0 schema
(package data, its sha256 pinned in a test) and a stdlib validator for the keyword subset above.
The subset is the validation keywords (`type`, `required`, `properties`, `items`, `$ref`, `$defs`,
`enum`, `additionalProperties`) plus the annotation keywords (`$schema`, `$id`, `title`,
`description`), which are accepted and ignored. A *schema* that uses any other keyword is rejected
at load, so a later vendored schema that needs more fails closed rather than passing unchecked.
Validation semantics for a *document* follow the spec's "a minor bump only adds things"
(section 1) and consumer obligations 2 and 3, and are fixed here:
- `type`, `required`, `items` and `$ref` are checked strictly;
- `additionalProperties: false` is treated as **open**: an unknown key is ignored, never rejected
  (obligation 2);
- an `enum` is checked strictly except for the sets a minor version may extend: `action.id`
  (also in `alternatives`), `error.code`, `decision.disposition`, `decision.satisfied_by`
  (display-only for the Controller; protocol `1.1` adds the `functional_evidence` and
  `pr_review_result` kinds), `worker.role`, the check ids and the `describe` capability lists. Those
  are treated as **open strings**, which the Controller then fails closed on in its own semantic
  layer (I3): an unknown action id or disposition is a `blocked` gate (C.2, C.3), an unknown error
  code is a refusal carrying that code, an unknown `satisfied_by` is shown verbatim in the gate
  and changes nothing, and an `automatic` decision whose `worker.role` is not one of the seven
  values the 2.7.0 schema lists is a `blocked` gate `workflow_unknown_worker_role` (the Controller
  dispatches on the action id, but obligation 3 says an unknown value is never run; a gate or an
  alternative carrying an unknown role is displayed, never launched). Every other
  enum (for example `reconcile` classes and the reasons the Controller maps) stays strict.
So a protocol `1.1` envelope with an added optional field or a new action id validates without a
Controller release (decision 1). Exit-code/envelope disagreement (exit `0` with `ok: false`),
an `ok: false` envelope without `error`, stdout that is not one document, and a `protocol.version`
whose major is not 1 are `WORKFLOW_PROTOCOL_FAILED`; a `unsupported_protocol` error is
`WORKFLOW_PROTOCOL_UNSUPPORTED`.

A. 3 **`describe` and `verify`.** `describe` is cheap and reads no state: it is the admission
probe (B). `verify` reads the state and the installation record. It is run at the preflight of a
step, and its result is a gate, not a refusal of the repository: a `healthy: false` answer becomes a
`workflow_unhealthy` gate that names each failing check and its detail verbatim, because a failing
`state_valid` can be transient (a planning worker interrupted after routing the item and before
writing its registry, measured above). `installation_release_matches` is expected healthy for a
real target, whose script bytes and record agree; the gate text says so.

A. 4 **Vendored fixture release.** `tests/workflow_releases/2.7.0/` holds the released payload in
the shape of 2.5.1/2.6.0 (`RELEASE.json` with digests, `scripts/`, command files), produced by
`tools/workflow_releases.py` from the published archive
(`workflow-2.7.0.tar.gz`, sha256 `c287323fbeb94a7ee487847fe0f42743ca6856bfb9ab79dd90f22457605ba06c`).
The fixtures' `reference` release stays `2.5.1` (`REFERENCE_WORKFLOW_RELEASE`): the goldens do not
move.

### B. Admission by capability (CP2)

B. 1 `managed_repo.inspect` keeps its five checks. The version gate becomes two-way:
  1. the installation record names a release with a `RELEASE_CONTRACTS` entry (2.5.1, 2.6.0):
     legacy mode, exactly as 1.6.0, `target_protocol = None`;
  2. otherwise, if `scripts/workflow_protocol.py` is a qualifying key of the installation record's `managed` map (A.1a), `describe` runs and answers `ok` with
     `supported_protocol_majors` containing `1`: protocol mode, `target_protocol = {major: 1,
     version, release}`;
  3. otherwise the 1.6.0 refusal (`outside_supported_line`/`unvalidated_release`), now with a third
     reason `no_protocol` when a release outside the validated set has no qualifying protocol script key (including a `managed` field that is absent or not an object); a listed protocol script that is missing or not a regular file is the A.1a refusal naming it, not `no_protocol`.
  A 2.6.x/2.5.x release outside the validated set is still refused as `unvalidated_release`
  (it has no protocol), so the exact-release set no longer admits anything new except through
  the protocol.
B. 2 The Manager's `verify`/`status` check (already part of `inspect`) remains the integrity gate
for the installation's files. The Controller adds no digest pin for protocol releases; it records
what it ran (I6): the Workflow's own script digests (the managed `scripts/*.py` entries) and the release. The previous `VALIDATED_WORKFLOW_RELEASES` set and its vendored per-release
suites stay as the legacy contract list.
B. 3 **The release is re-checked before every decision and pinned per job**, as ADR 0006 does (L1: CP2 delivers only the identity function, a pure derivation of `describe`'s `workflow_release` and the managed-file digest map from the installation record and the target's bytes, with its two direct tests; the job record's pin, the per-step comparison and the mid-job edit test are CP4's, where the record and the decisions exist; the reconcile-level outcomes are CP5's). **Two checks, two references (M1), as in 1.6.0:** the **per-step** re-check compares with the identity `inspect` derived for this invocation (CP2's identity function, the protocol analogue of `managed_repo.workflow_version`; 1.6.0's `_refuse_changed_release`, `controller/job.py:1947-1994`), runs right after the repository preflight and before any decision, and writes no job record; the **job record's** map is the reference only for that job's own verification and `resume` (below, E.1, E.2; the analogue of `_verification_contract`). A protocol-to-protocol release change between invocations (the Manager lane moving a target from 2.7.0 to 2.7.1) is therefore not refused for a fresh `run`: `inspect` derives the new identity, the job is launched and recorded under it, and only a `resume` of a job recorded under the old identity is `FAILED` `workflow_release_changed`. A protocol
job records `target_workflow_version`, the script digest map (A.1a, the managed `scripts/*.py` files only, J1) and the protocol
version; a step whose `describe` now reports a different release, or whose managed-file digest map
now differs from the identity `inspect` admitted for this invocation (a file edited, or a release changed, between `inspect` and the decision), refuses `WORKFLOW_RELEASE_CHANGED` (exit 20). **The same re-derivation runs before `reconcile`**, on the launch path exactly as on `resume` (K1): the protocol branch of
`_verify_transition` re-reads the installation record and the target's bytes, derives `describe`'s `workflow_release` and the managed-file digest map, and compares both with the job record's `workflow_release`/script digests; a difference is a terminal
`FAILED` `workflow_release_changed` and `reconcile` is **not** run (this is the protocol analogue of `_verification_contract`'s pin check, `controller/job.py:2043-2046`, since a protocol release has no pin and the job record's map is the only one). `reconcile` then runs from a fresh private copy whose digests equal the job record's map, identical by construction. `resume` reconciles under the
recorded release only (a changed release or managed-file digest, or a failing `describe`, is a terminal `FAILED` with reason
`workflow_release_changed`/`workflow_protocol_failed`, as for contracts today). **A drift-tolerant resume path (round-9 `RP9-3`):** the `resume` command calls
`inspect` first (`controller/cli.py:1292`), and `inspect` refuses with `DriftedInstallationError` when the Manager's `verify` fails, which is exactly what a worker's
edit of a managed script causes (`controller/managed_repo.py:326`); the pending record would then never be reconciled. `managed_repo.inspect_for_resume` therefore
wraps `inspect`: on `DriftedInstallationError` only (never another refusal), it resolves the repository root and reads the manifest without running the
Manager, and returns the repository flagged `installation_drifted`. `job.resume` over a flagged repository handles each pending record by kind: a record with a `protocol` block
compares **only the managed-file digest map** (round-10 `RP10-1`): it reads the installation record's `managed` map and hashes the target's bytes for those files, executing nothing, and **if that map differs from the record's, marks the record `FAILED` `workflow_release_changed` without running `reconcile`, `describe` or any Workflow script**
(writing only the record). `describe`'s `workflow_release` is deliberately not part of this comparison: it is a constant inside `scripts/workflow_protocol.py`, which is itself a key of the managed map, so running it would execute code whose installation has failed verification, and substituting the manifest's version would not establish the recorded release. The sound inference runs the other way: identical digests imply the identical script and therefore the identical release, and a differing digest is itself the evidence that the identity changed. If the digest map equals the record's, no safe evidence establishes an identity difference (the drift is in a file outside the map, or a different cause), and `resume` re-raises the original `DriftedInstallationError`, leaving the record
untouched; a legacy record is never touched under a flagged repository and `resume` re-raises the same refusal, exactly as 1.6.0 does. A fresh `run`/`step` never uses
this path: it keeps calling `inspect` and refuses a drifted installation. A product file in `scripts/` that the installation record does not list is
not part of the map, so editing it (a worker's checkpoint work) never changes it (J1).

### C. Decisions from `next-action` (CP3)

C. 1 **`controller/protocol_decision.py`** (new) turns one validated `next-action` result into the
existing `Decision` (so `job.execute_step`, `cli explain` and `status` keep one shape), plus a
`protocol` attribute (default `None`, added at the end of the dataclass) carrying the decision
document and its basis for the job record.

C. 2 **Mapping by disposition**, over a closed table in the Controller:

| protocol | Controller |
|---|---|
| `automatic`, known action id, `worker.user_only` false | `Decision.automatic` with the action's command (below) |
| `human_gate`, `external_gate` | `Decision.gate` (`HumanGate`): `what_is_required` from `reason.text` and `remedy`, `safe_resume_command` from `action.invocation` (display only) or the first alternative, plus the alternatives' invocations listed |
| `blocked` | a gate whose text is `reason.code`, `reason.text`, `reason.remedy` and the alternatives; never a launch |
| `complete` | the existing no-action outcome for `MILESTONE_COMPLETE` |
| `validation` or any unknown value | blocked gate `workflow_unknown_disposition` (I3) |

C. 3 **Action ids** are dispatched through one Controller-owned table `PROTOCOL_ACTIONS`:
`action id -> (command token, route key)`, for the twelve automatic ids. The route key is a
`routing.ROLES` member, assigned here and not derived from `role_for`'s `registry_complete` rule
(the protocol distinguishes the two implementation actions by id):

| action id | command token | route key (`routing.ROLES`) |
|---|---|---|
| `plan.start` | `milestone-plan` | `milestone-plan` |
| `plan.author` | `milestone-plan` | `milestone-plan` |
| `plan.apply_review` | `apply-plan-review` | `apply-plan-review` |
| `plan.review.local` | `review-plan` | `review-plan` |
| `plan.record_external` | `record-manual-plan-review` | `record-manual-plan-review` |
| `implementation.checkpoint` | `milestone-implement` | `milestone-implement` |
| `implementation.self_review` | `milestone-implement` | `milestone-implement-self-review` |
| `implementation.review.local` | `review-implementation` | `review-implementation` |
| `implementation.record_external` | `record-manual-implementation-review` | `record-manual-implementation-review` |
| `implementation.apply_review` | `apply-implementation-review` | `apply-implementation-review` |
| `functional.prepare` | `prepare-functional-review` | `prepare-functional-review` (**new**) |
| `functional.apply_findings` | `apply-functional-review` | `apply-functional-review` (**new**) |

**Two new routing roles** (G2; `routing.ROLE_ROUTES`/`ROLES` at `controller/routing.py:88-102` and
`ROLE_BY_COMMAND_STEM` at `:166-175` have no role for either command, and `ROLES` is the closed set
the routing config's `roles` object, `--role-model`/`--role-effort`, the settings file and telemetry
accept): `prepare-functional-review`, built-in route `_INHERIT` (it writes a checklist, as
`milestone-plan` and the `record-manual-*` commands do), and `apply-functional-review`, built-in
route `_OPUS` (it classifies and fixes findings, as `apply-plan-review` does). Neither is
single-agent. Both are added to `ROLE_ROUTES` (and so to `ROLES`) **only**, and to the routing-config,
settings and telemetry guides (CP3 for the code, CP7 for the guides). They are **not** added to
`ROLE_BY_COMMAND_STEM` (round 3, H2): that is the legacy command-to-role map, held equal to
`decision.SELECTED_COMMANDS` by `tests/test_routing.py:92`, and a legacy target never reaches either
role because no legacy decision launches either command (both stay in
`DELIBERATELY_NOT_SELECTED_COMMANDS`, a legacy-path table). Protocol jobs are routed by the
`PROTOCOL_ACTIONS` route key, not by a command stem. A CP3 test asserts every `PROTOCOL_ACTIONS`
route key is a member of `routing.ROLES`, so the two tables cannot drift.
**The settings consequence** (`controller/settings.py:109-111`: adding "a routing role" raises
`TABLE_GENERATION`): `TABLE_GENERATION` goes from 2 to **3**, and the first 1.7.0 command that fills
the settings file writes `_table_generation: 3` (`settings.py:490`). A 1.6.0 Controller reading that
shared file sees a generation above its own and treats the new roles as a newer release's keys
(`settings.py:445-447`), so the two releases can share the file during the rollout of decision 6; the
1.6.0 side is a refusal-or-warning of the kind that rule already defines, never a silent
misreading. CP3 pins the new generation and the effect on a 1.6.0-shaped reader (test, below); the
settings guide says it (CP7). The command text the worker receives is **rendered by the Controller** from `PROTOCOL_ACTIONS` and
the decision's `arguments`: `/<command token> <work_item_id>` (plus, for `plan.start` only, the
Controller's base, C.4). The decision's `action.invocation` is display-only (spec section 5.4,
obligation 4) and is never the worker's task. The Controller compares it with the rendering, *excluding* the
Controller-appended base of `plan.start` (C.4: the rendering is `/milestone-plan <base>`, the
protocol's `invocation` is `/milestone-plan`, and that appended argument is never a mismatch); when
they otherwise differ the decision is **not launched** (round-9 `RP9-1`): it is blocked with the
reason `workflow_invocation_mismatch`, naming the action id, both texts and the Workflow release,
and no job record and no worker exist. The protocol's `reconcile` rejects a stored decision whose action
rendering differs from its catalogue with `invalid_request` (`workflow_protocol.py:2052` of the
published 2.7.0), so a worker launched under such a decision would change the repository
before its result could be classified and the job could never be reconciled; the mismatch is a
Workflow release defect to report, not a command to run (dispatch on `action.id` still holds for
every decision that does launch). `explain` shows the blocked mismatch with both texts. The route (model/effort) is looked up from the action
id, not from parsing any text. An automatic id
that is not in the table is blocked (`workflow_unknown_action`, I3). `describe`'s `action_ids` is
compared with the table at admission: ids `describe` lists that the table lacks are reported as an
advisory in `inspect`, never a refusal (a minor protocol bump adds ids; they are blocked only if
they actually become the next action).
`worker.fresh_session` and `independent_of` are honoured by a CP3 test that every protocol job is
launched as a new worker session that resumes no earlier job's session; if the existing launcher
turns out to reuse a session anywhere, CP3 fixes that for protocol jobs rather than assuming it.
`user_only` actions are never in the table.

C. 4 **`plan.start`** has no work item: with no selectable item the Controller calls `next-action`
with no `--work-item`; rows 1/1a decide (`plan_start` automatic, or `plan_start_not_tracked`
blocked). The base commit argument (`/milestone-plan <base>`) that `decide_no_work_item` adds today
is appended by the Controller exactly as now (a Controller choice about a branch, not a lifecycle
fact); the decision's own `invocation` has none. A `plan.start` decision carries no `basis` (spec
section 5.4), so its currency is defined separately in D.2.

C. 5 **Controller-generic gates stay**, run before `next-action` and unchanged: the repository
preflight, branch and pull-request gates, and 1.6.0's committed-state gate in exactly 1.6.0's scope
(a named exception to I8): `evidence.uncommitted_implementation_state` is reused unchanged, applied
only at `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` (the phase read from the decision's `basis`),
only when the protocol decision is `automatic` (it would launch), and flagging only a working-tree
`COMPLETE` checkpoint, or a `SELF_REVIEWING_IMPLEMENTATION` phase, that `HEAD` does not record. It
does **not** flag an uncommitted `IN_PROGRESS` checkpoint (the dirty-resume state
`/milestone-implement` step 1c resolves) and it does not apply at any plan-stage phase, whose state
is legitimately uncommitted until `/approve-review plan` commits it. No byte comparison of
`WORKFLOW_STATE.json` is made.

C. 6 **Item selection** is still `target_state.select_work_item`; its id is passed as
`--work-item`. The decision's `basis.work_item_id` must equal it, else
`WORKFLOW_PROTOCOL_FAILED` (an answer for another item).

C. 7 **Equivalence evidence.** The comparison has defined dimensions and a defined scope:
- *Dimensions*: the decision kind (`launch`, or `non-launch`, where a legacy `HumanGate`/decline
  matches a protocol gate, `blocked` or `complete`), the command token for a launch, and the
  user-only property. Reason text is not compared.
- *Scope*: the golden scenarios that a real 2.7.0 fixture repository can reproduce, **plus**
  scenarios for the phases no golden covers (G1): `AWAITING_FUNCTIONAL_REVIEW` (no checklist
  evidence, current checklist and no findings, unconsumed findings) built as fixture repositories
  and compared against 1.6.0's `_decide_awaiting_functional_review`, and the `1`/`2.1`
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` cases of
  `tests/golden/external_implementation_review_decisions.json`. Excluded, and
  named in the test: the synthetic publication-status classes `refusal` and `unexpected`, which
  replay recorded Workflow answers a real repository cannot produce, and the `legacy-flat` and
  `legacy-scoped` layout cases the protocol does not distinguish from `scoped`. Each excluded class
  is covered instead by a protocol-side test that the catalogue row it reaches is asserted.
- *Rule*: for every in-scope scenario a fixture repository is built, `next-action` and 1.6.0's
  decision are compared, and every difference must belong to one of the **seven observable
  differences D1-D7** above, asserted by name and class (phase, version, scenario family). The
  non-observable notes N1-N4 are not rows of the table: each is tested where it lives (gate text in
  CP3, durability in CP5). The test writes the measured differences to a checked-in table
  (`tests/golden/protocol_vs_legacy_differences.json`) so a reviewer sees the full measured set, and
  asserts the table holds exactly D1-D7; a difference outside D1-D7 fails the test and is a plan amendment before it is
  accepted, not a silent addition.

### D. Stale check, record and loop guard (CP4)

D. 1 The job record gains an optional `protocol` block, written only for protocol jobs: `decision`
(the `next-action` result exactly as received), `envelope_digest`, `state_identity`,
`workflow_release`, `protocol_version`, `script_sha256` (the path-to-digest map of A.1a: the managed `scripts/*.py` files only), `reconcile` (filled later).
`_capture_pre_state` is unchanged (the Controller's own generic capture).
D. 2 **Two checks, one before the record and one before the spawn.** Both positions use one
*currency check*, defined per decision kind (G3):
- a decision with a `basis` (every item decision): `next-action --work-item <id>
  --expect-state-identity <basis.state_identity>`, and the answer must agree with the decision on
  `row`, `disposition`, `action.id` and `arguments`;
- a `plan.start` decision (rows 1/1a, no work item, no `basis`, so `--expect-state-identity` would be
  refused `invalid_request`, spec section 5.4): re-call `next-action` with no `--work-item` and
  require the same `row`, `disposition` and `action.id` and an equal `snapshot.work_item_ids`. A
  work item that appears between the decision and the launch changes `snapshot.work_item_ids`, so the
  decision is stale and the step re-decides, now with that item selected.
1. *Before the `PLANNED` write* (in `execute_step`, after the generic gates): a protocol job runs the
   currency check. On `stale_decision` (or, for `plan.start`, a changed answer) the step
   discards the decision and re-decides from the top of the protocol path, at most three times per
   step; the third failure is a gate `decision_unstable` naming the last two state identities, or, for `plan.start`, which has no `basis.state_identity`, the last two `snapshot.work_item_ids` values. No record
   is written for a decision that was not launched.
2. *Immediately before `worker.spawn`* (the last step of `_launch_job` before the spawn): the same
   currency check again. The identity covers the state only (spec section 4), so the answer is also
   compared with the decision being launched on `row`, `disposition`, `action.id` and `arguments`,
   all of which must be equal, and the **stored decision is the one checked** (I5). A
   `stale_decision`, a changed `plan.start` answer, or an answer that differs on any of those four
   fields at an unchanged identity (the feedback, the
   worktree or the functional review changed), takes the existing `worker_not_started` path of
   `_launch_job`: the record is terminal `FAILED` with `reconciliation_evidence.code`
   `decision_stale_at_launch` (the second answer's row, action id and identity recorded), no worker
   exists, it needs no `resume`, and the apply-relaunch bound never counts it. The step then
   reports the gate `decision_unstable`; the next `run`/`resume` re-decides from scratch. Nothing
   non-terminal is left for `resume` to reconcile.

D. 3 **Loop guard.** The 1.6.0 apply-relaunch bound is Workflow-specific and not used for protocol
targets. A generic guard replaces it, keyed on the item and the action id (for `plan.start`, which has no work item, the key is `(none, plan.start)`, so a repeated `no_progress` from it is guarded like any other pair) and *not* on the state identity
(a `no_progress` worker can still change the identity, for example a checkpoint moved to
`IN_PROGRESS`, or a `plan.author` that rewrites state without leaving `PLANNING`): when the two most
recent consecutive protocol jobs for the same `(work item, action id)` both ended `no_progress`, a
third launch of that pair is a gate `no_progress_repeated` naming both jobs. A job that made
`progress` or reached a gate resets the count, and the count is per pair, so another action does not
reset it. A `FAILED` job for the same pair (for example `decision_stale_at_launch` or
`reconcile_invalid`) is **skipped, not a break**: it is neither a `no_progress` nor a reset, so two
`no_progress` jobs with a `FAILED` one between them still count as consecutive. An **interrupted** job whose reconcile class was `no_progress` (E.1) is skipped the same way (M2): the action may not have run to its end, so it is neither counted nor a reset. Rationale: a failed
launch did not run the action, and a `FAILED` job that did run it is reported on its own; counting
or breaking on it would let a failure pattern either hide a stuck action or gate a healthy one. `run.max_steps` stays the outer bound.

### E. Outcomes from `reconcile` (CP5)

E. 1 After a protocol worker exits, `job._verify_transition`'s protocol branch **first re-derives the release identity and compares it with the job record's (B.3, K1); a difference is a terminal `FAILED` `workflow_release_changed` and `reconcile` is not run**, then runs `reconcile
--decision <stored decision>` from a private copy equal to the recorded map, and maps the class:

| worker outcome | reconcile class | job result |
|---|---|---|
| success or interrupted | `progress` | transition verified; the new `next` decision is stored in the record |
| success or interrupted | `gate_reached` | verified; the step then reports the gate |
| success | `no_progress` | verified-without-progress (round-9 `RP9-2`): a terminal `FINISHED` record whose `protocol` block carries `progress: "none"`, so no pending-job refusal (`PendingJobReconciliationError`) and no `resume` is needed before the next step, which re-decides (D.3 bounds it); `COMPLETED` is never written for it, since `COMPLETED` is non-terminal and `pending_reconciliation_jobs` holds the next step until `resume` |
| interrupted | `no_progress` | the existing interrupted-job handling |
| any | `invalid` | `FAILED`, reason `reconcile_invalid` (the class), the invalid reasons recorded verbatim |
| failed/timed out | not called | the existing failed-job handling (no state is trusted), unless the release identity changed (next row) |
| any, release identity changed | not called | `FAILED`, reason `workflow_release_changed`; **this row is evaluated first**, before the worker outcome is read, as `_verification_contract` runs first in `_verify_transition` today (L4) |

A reconcile envelope that fails validation, an unknown class, or `reconcile` itself failing, is a
terminal `FAILED` job with `workflow_protocol_failed` (an envelope or operation failure, as a
failing query is today); the class `invalid` is `reconcile_invalid`, a different reason, and
`status`, D.3 and the job record use exactly these two. `no_progress` is a legal outcome of an action whose `allowed_results` contains it; a class
the decision's `allowed_results` lacks is `invalid` by the protocol itself, so the Controller adds
no per-action table.
E. 2 **`resume`** reads the job's stored decision and runs the same reconcile for a launched job
(`_reconcile_launched`/`_reconcile_completed` gain a protocol branch); a job whose record has no
`protocol` block takes the legacy branch, so records from 1.6.0 resume unchanged.
E. 3 **Completion durability is a Controller fact.** 2.7.0's `prove_checkpoint_completions` resolves
a single trailer candidate without consulting the verify predicate and checks descent from the
recorded start commit; it does not require the completion to be committed in `WORKFLOW_STATE.json`
at `HEAD`, which `_checkpoint_completion_failure` (`job.py`) does. So `reconcile` can answer
`progress` for a worker that made a `Workflow-Checkpoint` trailer commit but left `COMPLETE` only in
the worktree state. The Controller therefore keeps one committed-state fact for
`implementation.checkpoint`: after a `progress` answer it runs
`evidence.uncommitted_implementation_state` (C.5, the named exception to I8); a non-empty result is a
terminal `FAILED` with reason `completion_not_committed_at_head` and the facts verbatim. The
protocol's `checkpoint_completion_unproven` stays the proof of the trailer chain. CP5's test names
all six 1.6.0 detail cases (`CHECKPOINT_PROGRESS_DETAILS`, `controller/job.py:762-768`) and asserts
for each the result on a 2.7.0 fixture:
- `completion_not_committed_at_head` and `last_completed_not_committed`, with a trailer commit: the
  protocol says `progress` and the Controller fact makes the job `FAILED`
  (`completion_not_committed_at_head`);
- `head_unchanged` and `no_newly_completed_checkpoint`: the protocol's class is asserted as
  measured (never `progress`);
- `pre_state_incomplete` and `state_unreadable`: Controller capture facts with no protocol
  counterpart. The protocol branch never consults them (the protocol reads the state itself, and a
  state it cannot read is `invalid`/`state_invalid`), so the test asserts that a protocol job with
  either shape is decided by `reconcile` alone, and that the two details are produced only on the
  legacy path.

E. 4 **Completion** is never read from `reconcile`: a protocol job that ends at a gate or
progress is followed by a new `next-action`, whose `complete` disposition ends the run.

### F. What the operator sees (CP3, CP5)

`inspect` shows `workflow_mode` (`legacy`/`protocol`), the protocol version and the managed-script digest
map (A.1a) for a protocol target; `explain` prints the row, the disposition, the action id and the
alternatives from the decision; `status` and a job's record show the reconcile class and invalid
reasons. All of it is additive to the pinned key sets. For a legacy target, `inspect` and `status`
JSON gain **no** key (`workflow_mode` and `target_protocol` are emitted only for a protocol target),
and a CP2 test pins the legacy output byte-identical.

### G. Documentation and full verification (CP6, CP7)

CP6 proves both modes end to end; CP7 adds ADR 0010 (amends ADR 0006: admission by capability, the
isolation without pins, decisions and outcomes from the protocol), `docs/guide/` updates
(`concepts`, `automation`, `installation`, `commands`, `troubleshooting`, `development` for "how a
Workflow release is admitted now"), `README.md`, `docs/README.md`, the release notes section, and
every golden generator with `--check`.

## Checkpoints

The registry (`docs/ai-workflow/registry/workflow-controller-orchestration-protocol-v1-registry.json`)
is the authority; this table is generated from it.

<!-- registry table: generated by workflow_state.render_registry_markdown, never hand-edited -->

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The protocol client: controller/protocol.py (run one operation with --protocol-major 1 and --repo-root, private copy of the Workflow's own script set (the exact scripts/<name>.py keys of the installation record's managed map, excluding names ending in _test; never a directory listing or a glob), with a path-to-digest map, under the shared ADR 0006 Git isolation, one-document stdout, exit-code/envelope agreement), controller/protocol_schema.py with the vendored 2.7.0 schema as package data and a stdlib validator for the schema's keyword subset that rejects a schema keyword outside it, treats additionalProperties:false and the minor-extensible enums (action ids, error codes, dispositions, satisfied_by, worker.role, check ids) as open for documents, and ignores annotation keywords, typed describe/verify/next-action/reconcile results, WORKFLOW_PROTOCOL_FAILED and WORKFLOW_PROTOCOL_UNSUPPORTED (exit 20), the isolation helpers in workflow_contract refactored to be shared, and tests/workflow_releases/2.7.0 vendored from the published archive; tests for the validator including a 1.1-shaped envelope with an extra field and a new action id validating and an external_gate with an unknown satisfied_by validating and yielding a gate, envelope handling, the isolation cases for the protocol script a managed-set copy where a non-Workflow scripts/extra.py or symlink is neither copied, digested nor refused while a managed symlink or missing managed file is refused naming it, a scripts/pkg/mod.py key and a traversal key in managed never copied or written outside the private directory, an absent or non-object managed field being no_protocol, a missing-module failure named from the last stderr line (bare failure plus stderr tail when it does not match), and a round trip against the real 2.7.0 scripts | - | 3 | 1 |
| CP2 | Admission by capability: managed_repo.inspect two-way version gate (a RELEASE_CONTRACTS release is legacy mode unchanged; otherwise a qualifying scripts/workflow_protocol.py key in the installation record's managed map plus a describe answering protocol major 1 is protocol mode; else the 1.6.0 refusal with the new reason no_protocol), target_protocol on the inspected repository, release and the managed-script digest map (the qualifying scripts/<name>.py entries of the installation record's managed map) derived afresh by one identity function (the per-job pin, the per-decision re-check and WORKFLOW_RELEASE_CHANGED are CP4's, the reconcile-level outcomes CP5's), inspect fields; tests for 2.5.1 and 2.6.0 unchanged, 2.7.0 admitted, a bumped-release stand-in admitted, a stand-in whose workflow_protocol.py imports an extra sibling module admitted with that module's digest recorded, a protocol script whose imported sibling is missing refused with a message naming the module, the identity function asserted on its own (a non-Workflow scripts/extra.py edited between two derivations leaving the map and release equal, an edited managed script changing the map), a non-Workflow symlink in scripts/ not blocking admission, a release without the script refused, protocol major 2 refused | CP1 | 3 | 1 |
| CP3 | Decisions from next-action: controller/protocol_decision.py turning a validated decision into the existing Decision (disposition table over automatic, human_gate, external_gate, blocked, complete and fail-closed for validation and unknown values), the PROTOCOL_ACTIONS table of the twelve automatic action ids with a route key per id, two new routing roles prepare-functional-review and apply-functional-review in routing.ROLE_ROUTES/ROLES only (not ROLE_BY_COMMAND_STEM, which stays equal to SELECTED_COMMANDS) with settings.TABLE_GENERATION raised to 3 and the pins that change on purpose named, an unknown worker.role on an automatic decision blocked, plan.start with no work item, worker command text rendered from the table not from invocation and a mismatching invocation blocked before any launch, the committed-state gate in exactly 1.6.0's scope (implementation phases, launch decisions, COMPLETE/SELF_REVIEWING facts only), a protocol target bypassing evidence.decide, explain and status output, the protocol attribute on Decision; tests for every catalogue row family against 2.7.0 fixtures, unknown values blocked, fresh-session launches, a plan-stage uncommitted-state launch and an IN_PROGRESS dirty-resume launch, an invocation mismatch blocked workflow_invocation_mismatch with no job record and no worker, a matching invocation carried through a fake worker to reconcile (plan.start base excluded), every PROTOCOL_ACTIONS route key a member of routing.ROLES, test_routing/test_settings pins updated on purpose with TABLE_GENERATION 3 and a settings file filled by this release read by a 1.6.0-shaped release (the _release helper) as the compatibility rule says, the equivalence comparison with defined dimensions and a scope that covers AWAITING_FUNCTIONAL_REVIEW and the 1/2.1 AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW golden, the checked-in table holding exactly the seven observable differences D1-D7 (including the new launches of implementation.apply_review, functional.prepare and functional.apply_findings) each asserted by name, the non-observable notes N1-N4 tested where they live, and a static test that the protocol path reads no lifecycle file | CP2 | 3 | 1 |
| CP4 | The stale check, the job record and the loop guard: the optional protocol block of the job record (decision as received, state identity, release, protocol version, script digests), the currency check (next-action --expect-state-identity for an item decision; a no-work-item re-call with equal row, disposition, action id and snapshot.work_item_ids for plan.start) before the PLANNED write (at most three re-decisions per step, the decision_unstable gate) and again immediately before the spawn with a terminal decision_stale_at_launch record when stale or when row/disposition/action id/arguments differ at an unchanged identity, the generic no_progress_repeated guard keyed on work item and action id (not identity) replacing the apply-relaunch bound for protocol targets, the record's release and managed-script digest pin (the reference for that job's own verification and resume) and the per-step re-check of the identity inspect admitted for the invocation with WORKFLOW_RELEASE_CHANGED (M1); tests for a managed script edited after launch refused at the next step, a changed describe release refused, a non-Workflow scripts/extra.py edit not refused, a plan.start launch passing both currency checks, a work item created between the decision and the launch re-deciding, a protocol-to-protocol release change between invocations (a fresh run admitted and launched under the new release, a resume of the old job FAILED workflow_release_changed), a FAILED job and an interrupted no_progress job each between two no_progress jobs skipped by the guard, a state change between decision and launch, three stale answers, same identity with a different action at the pre-spawn check, a stale abort after PLANNED then resume, a no-progress pair with a changed identity, a repeated no-progress pair, a progressing pair that does not trip it, and legacy record byte-identity | CP3 | 2 | 1 |
| CP5 | Outcomes from reconcile: the protocol branch of job._verify_transition and the class-to-result table over progress, gate_reached, no_progress and invalid (a successful no_progress job terminal FINISHED with progress none, so no resume is needed before the next step), reconcile on resume for launched and completed jobs from the stored decision, invalid reasons recorded verbatim, the release-identity re-check before reconcile on the launch path and on resume (a worker that edits a managed script ends FAILED workflow_release_changed with reconcile never called; a non-Workflow scripts/extra.py edit still reconciling), the committed-state completion fact (a trailer commit with COMPLETE only in the worktree state fails the job) and all six 1.6.0 detail cases (including pre_state_incomplete and state_unreadable, which have no protocol counterpart) each named and asserted on a 2.7.0 fixture; managed_repo.inspect_for_resume, a drift-tolerant resume path that on DriftedInstallationError alone marks a pending protocol job FAILED workflow_release_changed when its managed-file digest map differs, compared from the manifest and file bytes with no script executed (reconcile, describe and Workflow scripts never run), re-raises when the map is equal and for a legacy record, with an end-to-end CLI resume test over a modified managed script; tests for each class and invalid reason with a fake worker that does, partly does or does not do the action, a failed worker that also changed a managed script ending FAILED with the one reason workflow_release_changed, an interrupted worker, a failing reconcile, resume after a crash at each point and a 1.6.0 record resumed | CP4 | 3 | 1 |
| CP6 | Both modes end to end: a disposable-repository lifecycle on the vendored 2.7.0 through to complete in protocol mode with the fake worker, the same on 2.6.0 in legacy mode, a repository moved from 2.6.0 to 2.7.0 between steps, and every existing golden and the no-policy golden checked byte-identical | CP5 | 2 | 1 |
| CP7 | Documentation and full verification (terminal): ADR 0010 amending ADR 0006, docs/guide concepts, automation (naming the three new automatic launches), installation, commands, troubleshooting, development and the routing-config, settings (stating the TABLE_GENERATION 3 effect on a file shared with 1.6.0) and telemetry guides for the two new roles, README.md, docs/README.md, the 1.7.0 notes section checked by release_notes.notes_problem, every golden generator with --check, the full suite, the troubleshooting note that a target whose own work edits a managed scripts/workflow_*.py ends each job FAILED workflow_release_changed (not a defect), and the protected-path diff from the base | CP1, CP2, CP3, CP4, CP5, CP6 | 2 | 1 |

### CP1 -- the protocol client
`controller/protocol.py` (with the managed-script-set private copy of A.1a),
`controller/protocol_schema.py` with the vendored schema, the shared
isolation refactor of `workflow_contract`, error codes, and `tests/workflow_releases/2.7.0/`.
Tests: the validator on the schema's own examples and on each rejection (wrong type, missing
required, an unknown schema keyword at load), the open-document semantics of A.2 (a 1.1-shaped
envelope with an extra field and a new action id, also inside `alternatives`, validates; an unknown
disposition validates and later reaches the `workflow_unknown_disposition` gate; an `external_gate` with an
unknown `satisfied_by` validates and is a gate (G4); an `automatic` decision with an unknown
`worker.role` validates and is blocked `workflow_unknown_worker_role`; an unknown error
code is a refusal carrying that code; a missing required field and a wrong type still fail; the
annotation keywords load), envelope handling (exit/`ok` disagreement, two
documents, major 2, `unsupported_protocol`), the file set of A.1a (every managed `scripts/*.py`
copied and digested; a managed symlink or missing file refused naming it; a non-Workflow `scripts/extra.py` or symlink neither copied, digested nor refused; a `scripts/pkg/mod.py` key and a traversal key (`scripts/../x.py`) in `managed` never copied, digested or written outside the private directory, K3; an absent or non-object `managed` being `no_protocol`, K2; a missing imported sibling named from the last stderr line, with the bare-failure-plus-stderr-tail fallback when it does not match, J2), the
private-copy and Git-isolation cases the contract
tests already run, now for the protocol script, and a round trip against the real 2.7.0 scripts for
`describe`, `verify` and `next-action`. The legacy suites pass unchanged.

### CP2 -- admission by capability
`managed_repo.inspect` two-way gate and `target_protocol`; `describe`-based admission; release and
the identity function (release and managed-script digest map, derived afresh and
comparable); `inspect` fields. The per-job pin, the per-decision re-check and
`WORKFLOW_RELEASE_CHANGED` for a protocol target are CP4's (L1: they need the job record and the
decision path). Tests: 2.5.1/2.6.0 unchanged, 2.7.0 admitted, a 2.7.1 stand-in
(vendored copy with a bumped release string) admitted, a stand-in whose `workflow_protocol.py`
imports an extra sibling module admitted with that module's digest in the recorded map (H1), a release without a protocol script
refused `no_protocol`, a protocol major 2 refused, the identity function asserted on its own (a non-Workflow `scripts/extra.py` edited between two derivations leaves the map and release equal; an edited managed script changes the map), and a non-Workflow symlink in `scripts/` not blocking admission (J1). The mid-job edit and the per-decision re-check are CP4's tests of this function's wiring (L1) and the reconcile-level outcomes are CP5's (K1): CP2 depends only on CP1, and neither a job record nor a decision nor the reconcile branch exists before CP3-CP5.

### CP3 -- decisions from `next-action`
`controller/protocol_decision.py`, the disposition table, `PROTOCOL_ACTIONS` with a route key per
action id and the two new routing roles `prepare-functional-review` (`_INHERIT`) and
`apply-functional-review` (`_OPUS`) added to `routing.ROLE_ROUTES` only, with `TABLE_GENERATION` 3 (C.3),
worker command text rendered from the table (C.3), `plan.start`, the 1.6.0-scoped committed-state gate (C.5), the hook in
`evidence.decide`'s caller (a protocol target never enters `evidence.decide`), `explain`/`status`.
Tests: every catalogue row family against 2.7.0 fixtures, unknown values blocked, every
`PROTOCOL_ACTIONS` route key is a member of `routing.ROLES` (G2); `ROLE_BY_COMMAND_STEM` is untouched
and still equals `SELECTED_COMMANDS`; the on-purpose pin changes (`test_routing` role set,
`test_settings` `V1_ROLES`, `TABLE_GENERATION == 3`) and a settings file filled by this release read
by a 1.6.0-shaped release through the `_release` helper (H2), an `invocation` that differs from the table rendering is blocked `workflow_invocation_mismatch` with no job
record and no worker, `explain` showing both texts (the `plan.start` base is not a difference, G5), and a decision whose
`invocation` matches runs through worker completion to `reconcile` with the decision byte-identical to the stored one (RP9-1), a
plan-stage protocol step with uncommitted `WORKFLOW_STATE.json` launches, an `IN_PROGRESS` dirty
resume launches, an uncommitted `COMPLETE` checkpoint gates at an implementation phase, the
equivalence comparison with its defined dimensions, a scope that includes `AWAITING_FUNCTIONAL_
REVIEW` fixtures and the `1`/`2.1` `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` golden, and the checked-in
difference table holding exactly the seven observable differences D1-D7, each asserted by name
(including `REVISING_PLAN` with no feedback going from `/apply-plan-review` to `/milestone-plan`, and
the three new automatic launches `implementation.apply_review`, `functional.prepare` and
`functional.apply_findings`); N1-N4 are tested where they live, not in the table, a static test that the protocol path reads no lifecycle
file (I8) allow-listing only the committed-state call.

### CP4 -- the stale check, the record and the loop guard
Job record `protocol` block, the `--expect-state-identity` check before the `PLANNED` write with
bounded re-decide, the second check before the spawn with its terminal `decision_stale_at_launch`
record, the `no_progress_repeated` guard, the record's release and digest-map pin (the reference for that job's own verification and `resume`) and the per-step re-check against the identity `inspect` admitted for the invocation, run right after the repository preflight (B.3, L1, M1: `WORKFLOW_RELEASE_CHANGED`, exit 20). Tests: a managed script (`workflow_protocol.py`, `workflow_state.py`) edited between `inspect` and the decision refused `WORKFLOW_RELEASE_CHANGED`, a changed `describe` release refused the same way, a non-Workflow `scripts/extra.py` edit not refused (L1); a protocol target whose last job recorded release X, moved to a stand-in release Y (2.7.1, as in CP2) between invocations: a fresh `run` is admitted and launches under Y, while a `resume` of the X job is `FAILED` `workflow_release_changed` (M1); a `plan.start` launch passes both currency checks (D.2); a work item created between the
decision and the launch makes the decision stale and re-decides (G3); a `FAILED` job, and separately an interrupted `no_progress` job, between two
`no_progress` jobs for the same pair is skipped, so the guard still trips (D.3, G7, M2); a state change
between decision and launch (a hook between the two calls), three stale answers, the same identity with a different action at the
pre-spawn check (terminal `FAILED`, no worker, `resume` has nothing to reconcile), a stale abort
after the `PLANNED` write followed by `resume`, a repeated no-progress pair, a no-progress pair
whose state identity changes between the two jobs (the guard still trips), a progressing pair that
does not trip it, and the legacy record byte-identity.

### CP5 -- outcomes from `reconcile`
The protocol branch of `_verify_transition`, the release-identity re-check before `reconcile` (B.3, K1), the class-to-result table, `resume` for launched and
completed jobs, the committed-state completion fact (E.3) with the 1.6.0 detail cases each named and
asserted (all six `CHECKPOINT_PROGRESS_DETAILS`, E.3), including a trailer commit with `COMPLETE`
only in the worktree state. Tests (K1), on the launch path and on `resume`: a worker that edits a managed script (`workflow_protocol.py`, `workflow_state.py`) between the decision and reconcile ends `FAILED` `workflow_release_changed` with `reconcile` never called, and a non-Workflow `scripts/extra.py` edit still reconciles with no `workflow_release_changed`; a failed worker that also changed a managed script ends `FAILED` with the one reason `workflow_release_changed` (L4); an end-to-end CLI `resume` of a pending protocol job whose worker edited a managed script, with the Manager's `verify` reporting drift, marks the record `FAILED` `workflow_release_changed` with `reconcile` never called and no Workflow script run, a drifted installation whose managed-file digest map equals the record's re-raises `DriftedInstallationError` leaving the record untouched, a drifted-resume case in which the edited script's `workflow_release` constant differs from the manifest version and executing it would leave an observable effect (a marker file written at import) marks the record `FAILED` `workflow_release_changed` from the digest difference alone with the marker absent (no Workflow script ran) and only the record changed, and a pending legacy record under drift is still refused (RP9-3). A successful `no_progress` job ends terminal `FINISHED` with `progress: none`, the next step launches without `resume`, and the guard counts it (RP9-2). Tests: each class and each invalid
reason with a fake worker that does, partly does, or does not do the action, an interrupted
worker, a reconcile that fails, `resume` after a Controller crash at each point, and a 1.6.0 record
resumed.

### CP6 -- both modes end to end
A disposable-repository lifecycle on 2.7.0 with the fake worker through the whole lifecycle, in
protocol mode, ending at `complete`, plus the same on 2.6.0 in legacy mode, a repository upgraded
from 2.6.0 to 2.7.0 between steps (the release-changed refusal, then a fresh run), and the existing
goldens and no-policy golden checked byte-identical.

### CP7 -- documentation and full verification (terminal)
ADR 0010, the guides (including `automation.md` naming the three new automatic launches D5-D7, and
the routing-config, settings (the `TABLE_GENERATION` 3 effect on a settings file shared with 1.6.0) and telemetry guides for the two new roles), README and `docs/README.md`, the release notes section checked by
`release_notes.notes_problem`, every golden generator with `--check`, the full suite under the
reaping-subreaper wrapper, and the protected-path diff from the base. The troubleshooting guide says that a target whose own checkpoint work edits a managed `scripts/workflow_*.py` (a Workflow repository driven in protocol mode) ends every such job `FAILED` `workflow_release_changed`, which matches the legacy pin behaviour and is not a defect.

## Requirements

| Id | Requirement | Checkpoints |
|---|---|---|
| R1 | The Controller runs the protocol's operations from a private, isolated copy of the Workflow's own scripts (the installation record's managed list), validates every envelope against the vendored schema, and fails closed on an unknown major, action id, disposition, error code or an invalid envelope | CP1 |
| R2 | A target whose Workflow reports protocol major 1 is admitted whatever its release; 2.5.1 and 2.6.0 keep their contracts; the release and the managed-script digests are recorded and re-checked per decision and per job | CP2, CP4 |
| R3 | For a protocol target the Controller's next action, gate and reason come from `next-action`, with no lifecycle file read and the seven observable differences D1-D7 from 1.6.0 (three of them new automatic launches) documented and tested | CP3 |
| R4 | A protocol job launches only a current decision: the identity is checked immediately before the spawn, a stale decision is re-decided, and a repeated no-progress pair is gated | CP4 |
| R5 | For a protocol target the outcome of a worker comes from `reconcile`, on launch and `resume`, and an invalid result fails the job with the protocol's reasons | CP5 |
| R6 | Legacy targets behave exactly as 1.6.0; both modes complete a lifecycle end to end | CP2, CP6 |
| R7 | `inspect`, `explain` and `status` show the mode, the protocol decision and the reconcile class | CP2, CP3, CP5 |
| R8 | An ADR, the guides and the 1.7.0 notes describe the new admission and behaviour; the full suite and goldens pass | CP7 |

## Decisions for the reviewer and the user

1. **Admission by protocol capability, not by release.** Any Workflow whose `describe` answers
   protocol major 1 is admitted, whatever its release number; the exact-release set stays for the
   legacy contracts. This is what ROADMAP 1.7 asks ("exact Workflow releases may remain 'tested
   with' metadata, but should stop being the fundamental compatibility gate") and is what makes a
   later 2.7.x or 2.9.x need no Controller change, **including a release that adds a module the
   protocol imports** (protocol `1.1`: `workflow_gate_policy.py`, `workflow_forge.py`), because the
   private copy is the Workflow's own script set as the Manager lists it (I6, A.1a). What still couples the
   Controller to a Workflow release is only: the closed action-id table (`PROTOCOL_ACTIONS`, an
   unknown id is blocked, not run), the state-schema read of `target_state.read` (decision 5), and
   the protocol major. The cost is that a protocol-compatible Workflow
   release the Controller never ran is driven. The alternative, exact releases plus the protocol,
   keeps C9 from delivering its point. Recommended: capability.
2. **No digest pins for protocol releases.** The Controller still runs repository-resident
   Workflow code, which is why ADR 0006 pins two script digests. For the protocol it relies on what
   already stands: Workflow Manager's `verify` of the installation (part of `inspect`), the private
   copy and Git isolation, and a record of the managed-script digest map and the reported release in each job
   so a run is auditable (a product's own `scripts/*.py` is outside that map, J1). A pin per release would put an exact-release list back into the
   Controller. Recommended: no pin, record and compare.
3. **The protocol is authoritative where it differs from 1.6.0** (the seven observable decision
   differences D1-D7 in the Investigation, measured against the catalogue, the goldens and the
   functional-review handler, and re-measured by CP3's checked-in table; N1-N4 are notes, not
   decision differences). `REVISING_PLAN` with no applicable `REVISE` launches `/milestone-plan`
   instead of `/apply-plan-review` (D1); `"1"` items at several phases are `blocked` where 1.6.0
   launched (D2); `AMENDING_PLAN` `/milestone-plan` is automatic instead of declined with exit 15
   (D3); `APPLYING_REVIEW_FEEDBACK` on `1`/`2.1` items is automatic (D4). **Three launches 1.6.0
   never makes, which the user should agree to explicitly:** a `1`/`2.1` external implementation
   review with a current `REVISE`/`BLOCK`/pin is applied by the Controller on its own (D5, rows
   31/32); the functional checklist is prepared and committed without a prompt (D6, row 37); and
   the operator's functional-review findings are applied without a prompt (D7, row 38). D6 and D7
   add two routing roles (C.3) and are stated plainly in the release notes and
   `docs/guide/automation.md`. One protocol weakness is *not* accepted: completion durability stays
   a Controller fact (N2, E.3). Recommended: accept all of D1-D7.
4. **`record-external-result` is not used here.** Pasted manual verdicts are still recorded by the
   automatic `/record-manual-*` worker action the protocol names; a direct ingest belongs with C7 and
   C10. `resolve-artifact` is display-only and unused.
5. **`target_state.read` and the legacy path stay.** The Controller still parses
   `WORKFLOW_STATE.json` for item selection and `status`, so a state-schema change in a future
   Workflow still reaches it, and a legacy path serves 2.5.1/2.6.0. Making `next-action`'s snapshot
   the only source, and retiring the legacy path, is the follow-up milestone once Workflow
   repositories have moved to 2.7.0. Without that the Controller depends on Workflow's state
   schema but no longer on its lifecycle.
6. **Release 1.7.0**, a minor release from the `feat:` title. 1.7.0 goes into the shared install
   only between Workflow Manager milestones (shared lane plan). The Manager lane is told when C9
   ships; it then installs 2.7.0 into Controller-driven repositories.
7. **This repository stays on Workflow 2.6.0** during this milestone. Its own lifecycle runs under
   the installed 1.6.0/legacy path, and 2.7.0 is exercised only through the vendored fixture
   release. Moving this repository to 2.7.0 is the Manager lane's `workflow-manager update`, after
   1.7.0 is installed.

## Open questions

- None that block planning. The Workflow lane's plan for 2.8 adds protocol `1.1` (dispositions
  from policy, `functional_evidence` and `pr_review_result` kinds); C10 consumes it. Protocol
  `1.1` is additive, so nothing here changes for it (a new module it imports is copied with the
  rest of the managed script set, I6), and the closed disposition table blocks an
  unknown value until C10 handles `validation`.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-orchestration-protocol-v1-artifacts.json` follows the
previous Controller milestone's declaration, with this item's paths:

- plan stage: protected are this plan, its registry and its mapping; `controller/`, `tests/`,
  `tools/`, `docs/guide/`, `docs/adr/`, `docs/releases/`, `.workflow-controller/`, `docs/README.md`,
  `pyproject.toml` and `setup.py` are excluded as implementation content;
- implementation stage: protected are `controller/`, `tests/`, `tools/`, `docs/guide/`,
  `docs/adr/`, `docs/releases/`, `.workflow-controller/`, `.github/workflows/*.yml`, `docs/README.md`,
  `README.md`, `CLAUDE.md`, `pyproject.toml`, `setup.py` and this artifacts file itself; the plan,
  registry and mapping are excluded as plan-stage content, and the workflow's own bookkeeping is
  excluded. `pyproject.toml` is expected to change (package data for the vendored schema) and is
  protected so the change is reviewed.

## Verification

- CP1-CP6: each checkpoint's own tests plus the full suite (`python3 tools/run_tests.py` under the
  reaping-subreaper wrapper; no `PYTHONPATH=.`, no `FORCE_COLOR`).
- Goldens: `tests/golden/generate_*.py --check` (never `--help`), including
  `generate_plan_stage_decisions.py --release 2.6.0 --check`; none moves.
- Equivalence: the CP3 comparison names every difference; an unlisted one fails.
- CP7: the protected-path diff from the base, and `release_notes.notes_problem` on the narrative's
  notes section.
- Functional review (after technical approval): a disposable repository on the vendored 2.7.0
  through plan, implementation and acceptance with the fake worker; one real `describe`, `verify`
  and `next-action` against a throwaway checkout of the published 2.7.0 archive; and one repository
  upgraded 2.6.0 -> 2.7.0 between steps.

## Migration / data-integrity notes

- Records and policies written by 1.6.0 are read unchanged; the `protocol` block is an addition,
  absent on legacy jobs (I1). A 1.6.0 Controller reading a protocol job record ignores the block.
- Nothing is installed or changed in any target by this milestone. A target moves to protocol mode
  only by being on a Workflow that ships the protocol script (2.7.0 or later).
- Order of the rollout: 1.7.0 released, installed into the shared install between Manager
  milestones, the Manager lane told, then Controller-driven repositories updated to 2.7.0 by the
  Manager (not by this lane). Rolling a repository back to 2.6.0 returns it to the legacy path
  with no Controller change.
- A job started under protocol mode and resumed after the target's Workflow changed is a terminal
  `FAILED` (`workflow_release_changed`), never reconciled under another release.
