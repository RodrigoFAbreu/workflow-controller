# Archived milestone narrative — `workflow-controller-gen1-correctness-hardening`

Archived verbatim from `docs/ACTIVE_MILESTONE.md` on 2026-09-20, as part of
`/accept-milestone`'s own step 5, at this work item's own acceptance. This
file's content below this notice is unedited from the version
`docs/ACTIVE_MILESTONE.md` carried immediately before this acceptance
(functional review round: checklist evidence commit `8dd47203c2e407ec14ea7a5f4071a34a24d44d4a`,
overall **PASS**, recorded at `.ai-review/feedback/FUNCTIONAL_REVIEW.md`).

**Not archived here, deliberately** (same reasoning
`docs/milestones/completed/workflow-controller-generation-1.md` already
states for its own milestone): `docs/ai-workflow/CONTROLLER_GEN1_HARDENING_PLAN.md`,
its registry (`docs/ai-workflow/registry/workflow-controller-gen1-correctness-hardening-registry.json`),
its requirements mapping
(`docs/ai-workflow/requirements/workflow-controller-gen1-correctness-hardening-mapping.json`),
and its artifacts declaration
(`docs/ai-workflow/registry/workflow-controller-gen1-correctness-hardening-artifacts.json`)
all remain at their original paths, unmoved and unmodified — `docs/ai-workflow/WORKFLOW_STATE.json`'s
own `work_items["workflow-controller-gen1-correctness-hardening"]` entry
still declares these exact paths as its `plan_path`/`registry_path`/
`mapping_path`, and `plan_approval.review_content_manifest` pins the plan
document's blob at this same path, so moving any of them would make that
historical approval record's own manifest unresolvable. Separately,
`docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md`
(an implementation-stage-protected deliverable, not a plan-stage document)
also stays in place: `tests/test_checklist_corrections.py` reads it live,
by its exact current path, at test time. The
`workflow-controller-gen1-correctness-hardening` entry in
`docs/ai-workflow/WORKFLOW_STATE.json` (`work_items` map, phase
`MILESTONE_COMPLETE`) and the full Git history of its approvals are
likewise untouched by this archival.

---

# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-gen1-correctness-hardening`'s
plan (revision 2) was approved (`EXTERNAL_APPROVE`) on 2026-09-20, all four
checkpoints below completed, implementation revision 4 was approved
(`EXTERNAL_APPROVE`, commit `d611c38`) on 2026-09-20, and the work item
entered `AWAITING_FUNCTIONAL_REVIEW`. It is a narrow, post-Gen1 correctness
and operator-contract hardening pass — it does not redesign Controller
architecture and does not include Generation 2 features. The completed
`workflow-controller-generation-1` entry in `docs/ai-workflow/WORKFLOW_STATE.json`
is unchanged by this milestone; there is no `docs/ROADMAP.md` in this
repository to update. See "Functional review checklist" below for the
manual testing gate.

- **CP1 — snapshot-materialisation containment invariant: COMPLETE.**
  `controller/runtime.py` now exposes a public `assert_contained(root,
  path)` wrapper over the existing `_assert_contained` guard.
  `controller/identity.py`'s five snapshot-write sites
  (`materialise`'s `tmp_dir`/`dest` guards, `_extract_dirty`'s per-file
  `target` guard, `_publish_source_pin`'s `tmp`/`dest` guards) now call
  it; `_extract_clean`'s subprocess-mediated `tar` write is exempted
  explicitly, its containment proved instead by the directory guard
  `materialise` places on `tmp_dir` before dispatching to it. New
  `tests/test_write_containment.py` generalizes
  `tests/test_target_state.py`'s `ReadOnlySourceScanTest` AST-walk
  pattern package-wide: every write-call site under `controller/` must be
  inside `runtime.py`, a call into its public write API
  (`runtime.<name>`), or dominated in the same function by a containment
  guard call. Confirmed red (all seven sites flagged) against the
  pre-fix `identity.py`, green post-fix, with the three synthetic
  positive/negative instantiations plan CP1 names.
- **CP2 — manual-plan-review content-id label mismatch: COMPLETE.**
  `controller/evidence.py`'s `read_feedback_fields` now reads the
  manual-plan-review verdict's content-id field from `Reviewed review
  content ID:`, matching the real, installed Workflow contract.
  `tests/fixtures.py`'s `build_review_feedback_text`/`write_review_feedback`
  emit the corrected label so existing fixture-based tests keep exercising
  the recogniser's real, intended behavior. New
  `tests/test_evidence.py::ReadFeedbackFieldsRealArtifactShapeTest` binds
  the regression to a literal, plan-stage-shaped (`Reviewer role:
  MANUAL_EXTERNAL_PLAN_REVIEW`) copy of the real
  `.ai-review/feedback/REVIEW_FEEDBACK.md` artifact this work item's own
  round-1 plan review produced, never to the fixture that reproduces the
  recogniser's own literal — confirmed to fail against the pre-fix label
  and pass against the post-fix one.
- **CP3 — reviewer-role normalization consistency: COMPLETE.**
  `controller/job.py`'s `_predicate_row3_block_feedback_current` now
  routes its `Reviewer role:` comparison through
  `evidence._normalize_role`, the same normalization
  `evaluate_manual_stage_admissibility` already applies, so both readers
  of the field agree on every input. New
  `tests/test_job.py::PredicateRow3RoleNormalizationTest` writes a real
  `REVIEW_FEEDBACK.md` (legacy lowercase `Reviewer role:` spelling) to a
  temporary feedback directory and exercises the predicate through
  `evidence.read_feedback_fields`'s real on-disk read path, not a bare
  in-memory string comparison — built as a local literal, independent of
  CP2's `tests/fixtures.py` rewrite.
- **CP4 — operator-contract and functional-review-checklist
  documentation accuracy: COMPLETE.** New, committed
  `docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md`
  (declared in `implementation_stage.protected_paths`, per plan review
  round 1's finding B1) records all four corrections: `--json`'s global,
  before-the-subcommand-only placement; `explain`'s `EXIT_OK`/`0`
  contract in every branch; `inspect --json`'s payload stated as the
  negative claim only (none of `plan_approval`, `technical_approval`,
  `functional_acceptance_status`, `blocking_decisions`); and Flow 3's
  unconsumed-`FUNCTIONAL_REVIEW.md` precondition. New
  `tests/test_checklist_corrections.py` automates three of the four
  (finding M1): a regex over the file's own examples, an AST walk
  asserting every `Return` in `controller/cli.py`'s `cmd_explain` is
  `EXIT_OK`, and a direct call into `controller.cli._work_item_payload`
  with a twelve-attribute stub proving the four excluded names are absent
  from the real returned keys. The fourth stays prose-only, checked by
  this work item's own future functional-review round.

## Milestone

`workflow-controller-gen1-correctness-hardening` — Controller Generation 1
correctness and operator-contract hardening.

Work-item facts fixed before planning:

- `work_item_id`: `workflow-controller-gen1-correctness-hardening`
- `work_item_type`: `product`
- `work_item_kind`: `product`
- `governing_workflow_version`: `2.1` (from `WORKFLOW_CONFIG.json`'s
  current `default_workflow_version`; gives the two-stage
  local-then-manual-external plan review, same as Gen1)
- `plan_path`: `docs/ai-workflow/CONTROLLER_GEN1_HARDENING_PLAN.md`
- `registry_path`: `docs/ai-workflow/registry/workflow-controller-gen1-correctness-hardening-registry.json`
- `mapping_path`: `docs/ai-workflow/requirements/workflow-controller-gen1-correctness-hardening-mapping.json`
- `base_commit`: `398caa13f26233b338ca1573a9dc4b3a576a8e50` — "Accept milestone
  for workflow-controller-generation-1", current `main` HEAD at planning
  time and `workflow-controller-generation-1`'s own completion commit

## Goal

Fix a small, closed set of correctness defects and documentation
inaccuracies that Gen1's own review process found but did not gate on —
either because they were raised in an advisory-only review round, or
because they were discovered during functional-review acceptance and filed
as checklist-accuracy notes rather than blocking findings. Nothing here
changes what the Controller does; every item corrects a place where the
Controller's actual behavior, its own tests, or its own documentation
disagree with each other or with the real Workflow artifacts it reads.

## Required capabilities (authoritative scope)

### 1. Snapshot-materialisation containment invariant (`controller/identity.py`)

`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md` and `controller/runtime.py`'s own
docstrings both state that every durable write in the package — including
snapshot materialisation — is confined by `runtime`'s containment guard
(`_assert_contained`/`RuntimeContainmentError`). This is false as
implemented: `controller/identity.py`'s snapshot-write paths (the
first-materialisation `SOURCE_PIN.json` write, `_publish_source_pin`'s
reuse-path pin rewrite, the `os.replace` that publishes the snapshot
directory, and the `_extract_clean`/`_extract_dirty` extraction writes)
never call into `runtime`'s guard at all — containment holds today only by
path-construction convention. Resolve this so the claim is true: either
route every durable write in `identity.py` through the runtime containment
guard (exposing it as a public wrapper, e.g. `runtime.assert_contained`,
reused rather than reinvented), or, if some write is deliberately exempt,
state that exemption explicitly in the plan and correct the now-overstated
claims in `runtime.py`'s docstrings. Add package-wide regression coverage:
generalize `tests/test_target_state.py`'s `ReadOnlySourceScanTest` pattern
from one module to every module in `controller/`, so every durable write
site anywhere in the package is proven to be either inside
`controller/runtime.py` or dominated by a containment assertion, with a
negative instantiation (a synthetic unguarded write site must be flagged).

### 2. CP4B manual-plan-review content-id label mismatch (`controller/evidence.py`)

`controller/evidence.py`'s `read_feedback_fields` reads a manual-plan-review
verdict's content-id field from a line labelled `Reviewed content ID:`, and
`evaluate_manual_stage_admissibility` treats equality on that value as a
**hard** clause — a failure blocks `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`'s
automatic row entirely. The real, installed Workflow contract writes this
field as `Reviewed review content ID:` instead (confirmed against
`.claude/commands/review-implementation.md`'s own literal, the real
`.ai-review/feedback/REVIEW_FEEDBACK.md` artifact in this repository, and
`docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json`'s own
description of the binding-field set) — `docs/ai-workflow/REVIEW_PROTOCOL.md`
names no literal for this field at all. `read_labelled_line`'s exact-prefix
match means a real verdict written in the real label never parses, and the
Controller's own test fixtures (`tests/fixtures.py`'s
`build_review_feedback_text`) synthesize the code's wrong label rather than
the real one, so the test suite is self-confirming and never catches this.
Fix the label the code reads, fix the fixture that was masking it, and add
a regression test bound to the real artifact shape (derived from the frozen
command files / the real feedback file in this repository), not to a
fixture that reproduces the recogniser's own literal.

### 3. Reviewer-role normalization consistency

Two readers of the same `Reviewer role:` field disagree about what counts
as a match: `controller/evidence.py`'s `_normalize_role` strips and
upper-cases before comparing (accepting CP4B's documented legacy
lowercase spelling), while `controller/job.py`'s
`_predicate_row3_block_feedback_current` compares the raw field value
exactly, unnormalized. Not currently exploitable (the only writer of the
local-stage role line writes the canonical spelling), but it is a real,
reproducible inconsistency between two readers of the same artifact field
in the same repository. Route `job.py`'s comparison through the same
normalization `evidence.py` already applies, and add a regression test
proving the two readers now agree on a case that would previously have
diverged (e.g. the legacy lowercase spelling).

### 4. Operator-contract and functional-review-checklist documentation accuracy

Gen1's own functional-review acceptance round found four checklist/operator-
contract inaccuracies — confirmed, in every case, to be the checklist prose
diverging from the Controller's actual, tested, documented behavior, never
a Controller defect:

- **`--json` ordering examples.** `--json` is deliberately a global,
  top-level-only option (accepted only *before* the subcommand — confirmed
  correct and already documented this way in `README.md`'s "Global
  options" paragraph and `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`'s "CLI
  surface" table). Any checklist or example that writes
  `<subcommand> --json` (flag after the subcommand) is wrong and exits `2`
  (`unrecognized arguments`); the correct form is `--json <subcommand>`.
- **`explain`'s exit-code expectation.** `cmd_explain` unconditionally
  returns `EXIT_OK` (`0`) in every branch, both text and `--json` — `10`
  (`EXIT_GATE`) is reachable only from `job.STATUS_GATE_BLOCKED`, which
  only `step`/`run` (commands that actually execute) can produce.
  `tests/test_cli.py`'s `ExplainCommandTest` already asserts `EXIT_OK` at a
  gate phase; this is the Controller's documented, tested contract. Any
  checklist stating `explain` exits `10` is wrong.
- **`inspect --json` payload description.** The actual `--json` payload
  (`controller/cli.py`'s `cmd_inspect`) wraps a `repository` object
  alongside a `work_item` object built by `_work_item_payload` — it
  carries no `plan_approval`/`technical_approval`/
  `functional_acceptance_status`/`blocking_decisions` fields at all. Any
  checklist claiming the payload carries "approval state" overstates it;
  any checklist claiming the payload carries exactly `phase` plus the
  checkpoint-related fields and nothing else also overstates it (eight
  other `work_item` fields and the whole `repository` object exist too,
  plan review round 1, finding B2) — state the negative claim only.
- **Flow 3 precondition when an unconsumed `FUNCTIONAL_REVIEW.md` already
  exists.** `controller/evidence.py`'s `AWAITING_FUNCTIONAL_REVIEW` handling
  already has a tested, automatic sub-case for "findings are present and
  unconsumed" (`tests/test_evidence.py::AwaitingFunctionalReviewTest::test_unconsumed_findings_are_automatic`)
  distinct from "checklist is current and no `FUNCTIONAL_REVIEW.md` exists
  yet". Gen1's own functional-review checklist never documented this
  precondition or branch at all — its tester had to improvise an
  undocumented manual precondition check before running Flow 3 to be sure
  which branch would fire. This is a documentation gap, not a code defect:
  the code already handles and tests the case correctly.

Plan revision 2 (findings B1/M1) turned this scope item into a real,
implementation-time deliverable rather than a standing requirement on a
not-yet-written checklist: the four corrections are recorded in the
committed, implementation-stage-protected
`docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md`, and
three of the four are mechanically asserted by
`tests/test_checklist_corrections.py` (REQ-7/8/9). This milestone's own
functional-review checklist (authored later, when this work item reaches
`AWAITING_FUNCTIONAL_REVIEW`) links that file rather than re-deriving its
content. Only the fourth item — whether Flow 3's precondition step reads
adequately for a human tester — stays prose-only, checked by the
functional-review round itself.

## Explicitly out of scope

Generation 2 features; new orchestration capabilities; concurrency;
provider/model abstraction; usage-aware scheduling; unrelated ergonomics
work; any redesign of Controller architecture; any change to the completed
`workflow-controller-generation-1` work item, its plan, registry, mapping,
or artifacts-declaration files, which stay unchanged.

## Acceptance criteria

This milestone is complete only when: `identity.py`'s snapshot-write paths
are provably contained (guard reused, not reinvented) and a package-wide
regression scanner proves it, red without the fix and green with it; the
manual-plan-review content-id recogniser accepts the real Workflow
artifact's label, proven against real-artifact-shaped input, not a
self-confirming fixture; the two `Reviewer role:` readers agree, proven by
a discriminating regression test; the four operator-contract/checklist
inaccuracies are corrected in this milestone's own operator-facing
documentation and its own functional-review checklist, once authored;
automated tests are green (the full Controller suite plus the frozen
Workflow conformance suites); the final fresh review returns 0 Blocking /
0 Important; Workflow milestone acceptance completes; the repository is
clean.

## Functional review checklist

Manual functional review for `workflow-controller-gen1-correctness-hardening`
(implementation revision 4, technical approval recorded at commit
`d611c38`, `review_content_id`
`e573ea614749dc93841d14cbf3ce9ca37a39e4b5f089c1d40aeb77f2a1da380a`).
Findings go to `.ai-review/feedback/FUNCTIONAL_REVIEW.md`
(`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location").

This milestone changes no CLI-visible behavior on its own — CP1-CP3 are
internal correctness fixes (containment guard reuse, a manual-review
label fix, a reviewer-role-normalization consistency fix) with no new or
changed user-facing command surface. CP4 corrects four pieces of
*documentation* accuracy against behavior that was already correct. This
checklist therefore does two things: (a) a full regression pass proving
the existing CLI flows still behave exactly as before, since CP1-CP3
touch shared write/read paths those flows exercise; and (b) direct
verification of each of CP4's four corrected claims, per
`docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md`
(linked rather than repeated here, per `/prepare-functional-review`'s own
instruction).

### Precondition check — read this before Flow 3

`.ai-review/feedback/FUNCTIONAL_REVIEW.md` currently holds **stale content
from the already-accepted `workflow-controller-generation-1` round**, not
this work item — the feedback directory is a flat, work-item-unscoped
path (`workflow_fingerprint.resolve_feedback_dir` falls back to
`.ai-review/feedback/` whenever no `.ai-review/<work_item_id>/feedback/`
directory exists, which is the case here), so old findings persist across
work items. Its `FUNCTIONAL_REVIEW.consumed` marker's recorded hash
(`88e35533025f473016dccc8e33fc584c6e7a7565`) does **not** match the file's
current blob (`0bf14d01ed1c92d3bc91c3c8d52bbaedfc971dcc`), so the
Controller's own "unconsumed findings" check
(`controller.evidence.functional_review_findings_consumed`) will read this
leftover file as live, unconsumed findings for *this* work item and route
`explain`'s automatic decision toward "apply functional review findings"
instead of the fresh-checklist branch this round actually needs.

Before running Flow 3: open `.ai-review/feedback/FUNCTIONAL_REVIEW.md` and
confirm by hand that its content is the old Gen1 round (its own header
reads "Functional review — workflow-controller-generation-1"). If so,
archive or remove that stale file (and its `.consumed` marker) before
testing Flow 3, or expect and record `explain`'s automatic-branch behavior
as the *stale-data* artifact it is rather than as a finding against this
round. This is an environmental/tooling gap in the shared flat feedback
layout, not a defect introduced by this milestone's changes — note it for
the record, but it does not by itself block acceptance of this round.

**Post-hoc note (recorded at acceptance, 2026-09-20):** at the time this
round's functional review actually ran, no such stale file existed on
disk — `.ai-review/feedback/` held only an unrelated implementation-review
artifact (`REVIEW_FEEDBACK.md`). The precondition above described a
state that no longer held by execution time; the tester recorded this
discrepancy in `.ai-review/feedback/FUNCTIONAL_REVIEW.md` itself and
proceeded, since no stale/unconsumed file was actually present. See that
file's own "Precondition check" section for the full account.

### Setup

1. From a checkout of this exact repository at commit `d611c38` (or later,
   with a clean working tree):
   ```bash
   pip install -e .
   ```
   (skip if already installed from the Gen1 round; confirm with
   `workflow-controller status`).
2. No feature flags or seeded data are needed — every flow below is
   read-only against this repository's own live Workflow state.

### Test data

This repository's own checkout is the target (`.`) for every flow except
5, which needs any directory with no `.workflow-manager/` (a scratch
`mkdir` is enough).

### Flows

1. **`status`** — `workflow-controller status`.
   Expected: exits `0`; prints the resolved runtime root and pinned
   Controller source identity. Unchanged from the Gen1 round — confirms
   CP1-CP3's internal changes did not disturb this flow.

2. **`inspect` against this repository (`--json` before the subcommand)**
   — `workflow-controller --json inspect .` and
   `workflow-controller inspect .` (text).
   Expected: exits `0`; reports this repository as managed, the active
   work item `workflow-controller-gen1-correctness-hardening`, and its
   current phase `AWAITING_FUNCTIONAL_REVIEW`. **CP4 item 3**: in the
   `--json` payload's `work_item` object, confirm none of
   `plan_approval`, `technical_approval`, `functional_acceptance_status`,
   or `blocking_decisions` appear as keys.

3. **`inspect` with `--json` misplaced after the subcommand (CP4 item 1)**
   — `workflow-controller inspect . --json`.
   Expected: exits `2` (`unrecognized arguments`) — `--json` is a global,
   top-level-only option and is **not** accepted after the subcommand.
   Confirms the corrected checklist examples (`--json inspect .`, not
   `inspect . --json`) are the only valid form.

4. **`explain` against this repository (CP4 item 2)** —
   `workflow-controller explain .` and `--json explain .`.
   Expected: exits **`0`** (`EXIT_OK`) in both forms — never `10`. The
   report names the observed phase `AWAITING_FUNCTIONAL_REVIEW`, states
   plainly that this is a manual gate requiring a human to test and either
   approve or file findings, and names the safe resume command (this
   checklist, then `/accept-milestone` once testing is clean). It must
   **not** launch a worker or write a Controller job record
   (`workflow-controller status` immediately afterward should show no new
   job record from this call). See the precondition check above before
   running this flow.

5. **Unmanaged-repository refusal** — `workflow-controller inspect <a
   directory with no .workflow-manager/>`.
   Expected: exits `20`, with a clear `UnmanagedRepositoryError`-style
   message naming the path — no stack trace, no silent success. Unchanged
   from the Gen1 round.

6. **`resume` against this repository** — `workflow-controller resume .`.
   Expected: exits `0` (no non-terminal job records to reconcile) and
   never attempts to launch a worker. Unchanged from the Gen1 round.

### Expected results summary

| Flow | Exit code | Launches a worker? |
|---|---|---|
| 1 `status` | 0 | No |
| 2 `inspect .` (`--json` before subcommand, and text) | 0 | No |
| 3 `inspect . --json` (misplaced flag, CP4 item 1) | 2 | No |
| 4 `explain .` (text and `--json`, CP4 item 2) | 0 | No |
| 5 `inspect <unmanaged>` | 20 | No |
| 6 `resume .` | 0 | No |

### Known limitations / out of scope for this milestone

- Generation-2 features, broad Controller lifecycle automation, and
  everything else listed under "Explicitly out of scope" above — do not
  file findings against their absence.
- CP1-CP3 have no CLI-observable surface of their own; they are proven by
  this milestone's automated regression suite (`tests/test_write_containment.py`,
  `tests/test_evidence.py::ReadFeedbackFieldsRealArtifactShapeTest`,
  `tests/test_job.py::PredicateRow3RoleNormalizationTest`), not by this
  manual checklist. This checklist's own flows 1, 5, and 6 are the
  regression evidence that those internal changes didn't break anything
  user-visible.
- CP4 item 4 (the Flow-3-precondition documentation gap) is checked by
  this checklist's own "Precondition check" section above, not by a
  separate flow — there is no CLI output that exercises it directly
  beyond what Flow 4 already does once the stale file is handled.
- The stale `.ai-review/feedback/FUNCTIONAL_REVIEW.md` described in the
  precondition check is a pre-existing environmental artifact of the flat
  feedback-directory layout, not a defect this milestone introduced or is
  scoped to fix.
