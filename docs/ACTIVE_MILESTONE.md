# Active Milestone

## Status

**Implementing.** `workflow-controller-gen1-correctness-hardening`'s plan
(revision 2) was approved (`EXTERNAL_APPROVE`) on 2026-09-20 and the work
item entered `IMPLEMENTING`. It is a narrow, post-Gen1 correctness and
operator-contract hardening pass — it does not redesign Controller
architecture and does not include Generation 2 features. The completed
`workflow-controller-generation-1` entry in `docs/ai-workflow/WORKFLOW_STATE.json`
is unchanged by this milestone; there is no `docs/ROADMAP.md` in this
repository to update.

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

This milestone's own functional-review checklist (authored later, when
this work item itself reaches `AWAITING_FUNCTIONAL_REVIEW`) must get all
four of these right from the start — this scope item is the standing
requirement that it does, not a one-time edit to Gen1's now-historical
checklist text. No regression test applies to prose; correctness here is
checked by the functional-review round itself.

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
