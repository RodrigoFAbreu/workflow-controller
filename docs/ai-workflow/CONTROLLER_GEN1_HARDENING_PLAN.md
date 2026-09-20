# Workflow Controller — Gen1 Correctness Hardening (Revision 2)

Execution/reference plan for work item
`workflow-controller-gen1-correctness-hardening`.
Governed by `governing_workflow_version: "2.1"` — the two-stage
local-then-manual-external plan-review protocol applies to this item's own
plan-stage approval (`docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`).

Source of requirements: `docs/ACTIVE_MILESTONE.md`'s "Required capabilities
(authoritative scope)", "Explicitly out of scope" and "Acceptance criteria"
sections, fixed before planning and not re-derived here. `docs/ROADMAP.md`
does not exist in this repository; there is no milestone ordering document
to consult beyond `ACTIVE_MILESTONE.md`. `docs/TECHNICAL_DECISIONS.md` does
not exist in this repository either — step 5's "Open decision" check has
nothing to check against.

- `work_item_id`: `workflow-controller-gen1-correctness-hardening`
- `work_item_type` / `work_item_kind`: `product` / `product`
- `base_commit`: `398caa13f26233b338ca1573a9dc4b3a576a8e50` — "Accept
  milestone for workflow-controller-generation-1", current `main` HEAD at
  planning time
- `plan_path`: `docs/ai-workflow/CONTROLLER_GEN1_HARDENING_PLAN.md` (this
  file)
- `registry_path`: `docs/ai-workflow/registry/workflow-controller-gen1-correctness-hardening-registry.json`
- `mapping_path`: `docs/ai-workflow/requirements/workflow-controller-gen1-correctness-hardening-mapping.json`

## What is being built

Nothing new. This is a narrow, four-checkpoint correctness and
operator-contract hardening pass over the already-completed
`workflow-controller-generation-1` deliverable (`controller/`, `tests/`,
`pyproject.toml`). Every checkpoint corrects a place where the Controller's
actual behavior, its own tests, or its own documentation disagree with each
other or with the real Workflow artifacts it reads — found during Gen1's
own advisory implementation review (`.ai-review/advisory/opus-revision4-advisory-review.md`)
and functional-review acceptance (`.ai-review/advisory/FUNCTIONAL_REVIEW_pre_round5.md`,
`.ai-review/feedback/FUNCTIONAL_REVIEW.md`), but not gated on at the time —
the advisory round was not the authoritative gate for its own findings, and
the functional-review findings were classified as checklist-accuracy notes,
not Controller defects, because the checklist's expectation was wrong, not
the code. No Controller architecture changes, no Generation 2 features, no
new orchestration capability, no concurrency, no provider/model
abstraction, no usage-aware scheduling, and no change to the completed
`workflow-controller-generation-1` work item's own plan/registry/mapping/
artifacts-declaration files, which stay untouched throughout.

Every advisory-review finding this plan does *not* carry forward was
independently re-verified against the current, completed Gen1
implementation before being dropped — not preserved merely because it was
once mentioned (`docs/ACTIVE_MILESTONE.md`'s own instruction). Four of the
five "remaining advisory findings" the outer scoping pass initially listed
turned out to be already fixed and already covered by regression tests in
the completed implementation:

- **JSON/internal-marker exposure** (round-2 `O1`) — fixed at commit
  `824128b`; `controller/cli.py`'s `cmd_resume` strips
  `reconciled_this_call` from the `--json` serialization while the exit-40
  computation still reads the original records; regression test
  `tests/test_cli.py::test_json_output_strips_reconciled_this_call_marker`.
- **Exact bootstrap key-set-difference verification** (round-2 `O2`) —
  fixed at commit `99bb875`; `tests/test_integration_disposable_repo.py`
  asserts on `post_run_work_item_keys - pre_run_work_item_keys`, an exact
  set difference, not a bare count.
- **Test/config isolation** (round-2 `O3`) — fixed at commit `90ef194`; the
  live-fixture's no-`workflow-manager`-binary fallback now writes a real
  bootstrap-template `WORKFLOW_CONFIG.json` instead of carrying this
  repository's own operational one into the disposable target. No advisory
  anywhere in the reviewed sources flags broader isolation gaps (real
  `HOME`, `~/.claude`, arbitrary environment variables); the real-binary/
  real-environment integration tests use them by design, never as a
  defect, so nothing further is in scope here.
- **Overly broad `NO_PHASE` guarding** (round-2 `O4`) — fixed at commit
  `3483efd`; `controller/job.py`'s `property_table_violations` now gates
  its second check on `eo.predicate is None` rather than reusing the first
  check's broader `NO_PHASE` exemption.

Only **reviewer-role normalization** (`opus-revision4-advisory-review.md`'s
own `O4`, distinct from round-2's `O4` above despite the shared label) is
still live in the current code, and it is this plan's CP3.

## Checkpoints

<!-- BEGIN GENERATED REGISTRY TABLE — docs/ai-workflow/registry/workflow-controller-gen1-correctness-hardening-registry.json is the sole source of truth; this table is workflow_state.render_registry_markdown(registry)'s output, never hand-edited. -->

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Guard identity.py's snapshot-write paths under the runtime containment invariant, with package-wide write-path regression coverage | - | 3 | 1 |
| CP2 | Correct CP4B's manual-plan-review content-id label to match the real Workflow artifact contract, with real-artifact-bound regression coverage | - | 2 | 1 |
| CP3 | Normalize the two Reviewer-role readers (evidence.py / job.py) to agree, with a discriminating regression test | - | 1 | 1 |
| CP4 | Author and test this milestone's own committed checklist-corrections deliverable, closing the operator-contract/functional-review-checklist inaccuracies found during Gen1 acceptance | - | 3 | 1 |

<!-- END GENERATED REGISTRY TABLE -->

All four checkpoints are mutually independent (`depends_on: []` throughout)
and may be implemented in any order, or in parallel across sessions. Three
of the four touch entirely disjoint files; CP2 and CP3 share one file each
touches for a different purpose, so revision 2 states the constraint that
keeps them order-independent explicitly rather than asserting file
disjointness that does not hold (plan review round 1, finding I5):

- CP1 touches `controller/identity.py`, `controller/runtime.py` (adding the
  public wrapper), and a new/generalized package-wide scan test.
- CP2 touches `controller/evidence.py`, `tests/fixtures.py` (rewriting
  `build_review_feedback_text`/`write_review_feedback`'s emitted label),
  and `tests/test_evidence.py`.
- CP3 touches `controller/job.py` and a regression test (`tests/test_job.py`
  or `tests/test_job_validation.py`, wherever row 3's predicate is
  currently exercised). CP3's own regression test must build its feedback
  text input directly (a local literal or helper private to that test
  module), never through `tests/fixtures.py`'s `build_review_feedback_text`/
  `write_review_feedback` — CP2 rewrites exactly those functions in the
  same revision window, so a CP3 test built on them would either depend on
  CP2 having already landed or silently duplicate CP2's own fixture change.
  This is the one place the two checkpoints would otherwise share a file
  for different reasons; stating the constraint here keeps `depends_on: []`
  true rather than merely declared.
- CP4 touches a new committed file this milestone's own implementation
  stage protects — `docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md`
  (see CP4's own "Fix" section for why this is now a real, implementation-time
  deliverable rather than a documentation-only checkpoint) — and a new
  regression test in `tests/`. Disjoint from CP1-CP3's own files.

### CP1 — snapshot-materialisation containment invariant

**Problem.** `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md` (Gen1's own,
now-frozen plan) and `controller/runtime.py`'s docstrings both state, as a
structural invariant, that every durable write in the package — including
snapshot materialisation — is confined by `runtime`'s containment guard.
`controller/identity.py` performs five durable-write sites that never call
into that guard:

- `materialise`'s first-materialisation `(tmp_dir / "SOURCE_PIN.json").write_text(...)`
  (`identity.py:330`);
- `_publish_source_pin`'s reuse-path `open(tmp, "wb")` + `fsync` +
  `os.replace(tmp, dest / "SOURCE_PIN.json")` (`identity.py:266-270`);
- `materialise`'s `os.replace(tmp_dir, dest)`, publishing the snapshot
  directory itself (`identity.py:333`);
- `_extract_clean`'s `tar -x -C <dest>` subprocess call (`identity.py:214-216`)
  — `git archive` itself (`identity.py:205-207`, via `_run_git_binary`)
  writes nothing to disk; it only produces a tar stream on its own stdout,
  which `_extract_clean` captures in memory and pipes to `tar`'s stdin.
  `tar` is the sole process that writes, and the paths it extracts are
  `HEAD`'s own committed tree, scoped to `_SNAPSHOT_DIRS`/`_SNAPSHOT_FILES`
  by `git archive`'s own pathspec argument (plan review round 1, finding O1);
- `_extract_dirty`'s per-file `target = dest / rel` + `shutil.copy2(src,
  target)` (`identity.py:238-240`), where `rel` comes from `git ls-files`
  output.

Containment holds today only by path-construction convention
(`dest = runtime_root / "source" / <64-hex tree digest>`), not because it
is checked — the claim in the plan and in `runtime.py` is false as
implemented, and nothing tests it: the only write-scope scanner in the
suite (`tests/test_target_state.py`'s `ReadOnlySourceScanTest`) covers
`target_state.py` alone.

**Fix.** Expose the existing guard as a thin public wrapper,
`runtime.assert_contained(root, path)`, over the existing
`runtime._assert_contained` (both parameter names generalized — the
private helper already takes any `(root, full_path)` pair, not only
`runtime_root`; nothing about its behavior is runtime-root-specific). Call
it at every durable-write site named above, choosing the *narrowest*
already-in-scope root at each site rather than routing every call through
`runtime_root` — a requirement `_extract_clean`, `_extract_dirty` and
`_publish_source_pin` do not receive as a parameter today, so guarding
against it would force a signature change these functions do not
otherwise need (plan review round 1, finding I3). Instead:

1. **`materialise`** (the one function that does own `runtime_root`):
   right after computing `tmp_dir = source_dir / f".materialise-{token}.tmp"`
   and before branching on `dirty`, add
   `runtime.assert_contained(source_dir, tmp_dir)`. This one call, being
   textually earlier in `materialise`'s own body than every later use of
   `tmp_dir`, dominates: the dispatch into `_extract_clean`/`_extract_dirty`
   (bounding the directory either extraction path writes into, at the
   point the destination is decided — see item 4 below for why this is
   the containment property available for `_extract_clean`'s own write),
   `(tmp_dir / "SOURCE_PIN.json").write_text(...)`, and the `finally`
   block's `shutil.rmtree(tmp_dir, ignore_errors=True)` (`identity.py:337`
   — newly in scope; see REQ-2's write-form set below, finding I2). Then,
   right after `dest = source_dir / tree_digest` is computed, add a second
   call, `runtime.assert_contained(source_dir, dest)`, which dominates
   `os.replace(tmp_dir, dest)` and the `_publish_source_pin(source_dir,
   dest, pin_body)` call on the reuse path. No signature change:
   `materialise` already owns `source_dir`, `tmp_dir` and `dest` as
   locals.
2. **`_extract_dirty(origin, dest)`**: for each `rel`, after computing
   `target = dest / rel` and before `shutil.copy2(src, target)`, add
   `runtime.assert_contained(dest, target)`. `dest` is already this
   function's own second parameter — no signature change. This is the
   site-specific choice REQ-1 needs: `rel` is derived from `git ls-files`
   output, so the meaningful containment root for `target` is the
   extraction directory (`dest`) itself, not `runtime_root` — guarding
   against `runtime_root` would accept any escape that stayed inside the
   wider runtime tree but outside `dest`, which is not the property this
   site exists to prove (plan review round 1, finding I3).
3. **`_publish_source_pin(source_dir, dest, pin_body)`**: right after
   `tmp = source_dir / f".{_SOURCE_PIN_NAME}.{token}.tmp"` is computed and
   before the `try:` block, add `runtime.assert_contained(source_dir,
   tmp)`; right before `os.replace(tmp, dest / _SOURCE_PIN_NAME)`, add
   `runtime.assert_contained(source_dir, dest)`. The first call dominates
   both `open(tmp, "wb")` and the `finally` block's `tmp.unlink(missing_ok=True)`
   (`identity.py:272` — newly in scope, finding I2). No signature change:
   `source_dir` and `dest` are already this function's own first two
   parameters. `_publish_source_pin`'s deliberate placement of `tmp`
   *outside* the digest-named snapshot directory but *inside* `source_dir`
   is unchanged and still passes both new guards (confirmed:
   `source_dir = runtime_root / "source"`, `identity.py:292`, so
   `source_dir` is itself inside `runtime_root` — the guard narrows the
   root, it does not weaken the property).
4. **`_extract_clean(origin, dest)`**: no guard call is added inside this
   function. Its one write is `subprocess.run(["tar", "-x", "-C",
   str(dest)], ...)` — a subprocess invocation, not a direct Python write
   call, and REQ-2's AST scanner (below) cannot see inside it or attribute
   a same-function guard call to it no matter where one is placed, since
   the scanner's domination rule is syntactic and scoped to the writing
   function's own body. This site's containment is instead the directory
   guard `materialise` already places on `tmp_dir` (item 1) before
   dispatching to `_extract_clean` at all — a real, verified property
   (the directory `tar` extracts into is confirmed contained before
   extraction begins), just not one attributable to `_extract_clean`
   itself by an AST walk. REQ-2 states this as a named, justified
   exemption rather than leaving the gap implicit (plan review round 1,
   finding I1).

This keeps every one of CP1's five original sites, plus the two REQ-2
widening catches guarded below, purely additive: no function anywhere in
`identity.py` gains, loses, or changes a parameter, so this requires no
amendment to Gen1's own frozen plan text.

**Regression coverage.** Generalize `ReadOnlySourceScanTest`'s AST-walk
pattern from one module (`target_state.py`) to every module under
`controller/`, adopting its own existing, broader write-form set
literally rather than CP1's narrower prose enumeration above (plan review
round 1, finding I2) — `{write_text, write_bytes, replace, rename,
remove, copy, copy2, copyfile, copytree, move, rmtree}` plus `unlink`
(`identity.py:272`'s `tmp.unlink(missing_ok=True)` is the same class of
destructive write and belongs in the set an honest widening produces),
plus the existing `open(..., mode)` write-mode-character check unchanged.
For every matching call site, require it to be either:

- inside `controller/runtime.py`, or
- a call whose dotted callee is `runtime.<name>` (e.g.
  `controller/job.py:1617,1620`'s `runtime.write_bytes(...)`) — a call
  into `runtime.py`'s own already-containment-checked public write API
  (`_atomic_write` calls `_assert_contained` internally before writing),
  treated as safe on the same basis as the first bullet rather than
  requiring a redundant local guard at every one of its call sites, or
- dominated, in the same function, before the write, by a call to
  `runtime.assert_contained`/`runtime._assert_contained`.

A `subprocess`/`os.system`-mediated write (currently exactly one in
`controller/`: `_extract_clean`'s `tar` invocation) is not itself a
recognized write-call form and is never flagged or cleared by this
scanner — stated explicitly as the scanner's actual, limited boundary
(REQ-2), not left for a reader to discover the hard way (plan review
round 1, finding I1). Running this test against the current, unfixed
`identity.py` must fail on all seven sites named in "Problem" and "Fix"
above (the original five plus `identity.py:272`/`:337`); after the fix,
it must pass, and no other module under `controller/` gains a new
violation (confirmed by direct inspection ahead of implementation: no
write-call-suffix match exists anywhere in `controller/` today outside
`identity.py` and the two exempted `runtime.write_bytes` calls in
`job.py`).

Include three synthetic instantiations, each a source string parsed via
`ast.parse` — mirroring `ReadOnlySourceScanTest.test_scanner_flags_a_synthetic_write_call`'s
own existing pattern exactly, never a real module mutated at test time
(plan review round 1, finding M2) — so the negative case cannot depend on
a real module's current, incidental shape: (a) a write call
(`Path('x').write_text('y')`, a form already in the scanner's recognised
set — finding M2) with no preceding containment call must be flagged;
(b) the same call, preceded in the same synthetic function by a
`runtime.assert_contained(...)` call, must not be flagged; (c) a bare
`runtime.write_bytes(...)` call with no local guard must not be flagged,
proving the `runtime.<name>` exemption is real and scoped to that one
dotted-name pattern, not "any guard-free call slips through."

### CP2 — manual-plan-review content-id label mismatch

**Problem.** `controller/evidence.py`'s `read_feedback_fields` reads the
manual-plan-review verdict's content-id field as:

```python
"reviewed_content_id": read_labelled_line(text, "Reviewed content ID:"),
```

and `evaluate_manual_stage_admissibility` treats equality on that value as
a **hard** clause: a failure means the Controller launches nothing at
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`. The real, installed Workflow
contract writes this field under a different label —
`Reviewed review content ID:` — confirmed three independent ways:
`.claude/commands/review-implementation.md:252` writes that literal for the
sibling implementation-review stage; the real
`.ai-review/feedback/REVIEW_FEEDBACK.md` artifact in this repository uses
it (`Reviewed review content ID: 60d9eecf…`); and
`docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json` documents
the binding-field set by that exact name.
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s feedback template names no
content-id literal at all, so it cannot corroborate the code's spelling
either. `read_labelled_line` is an exact-prefix match, so a real verdict
written in the real label parses as `reviewed_content_id: None`, the hard
clause fails, and the automatic row can never fire for a verdict a real
reviewer wrote correctly.

`tests/fixtures.py`'s `build_review_feedback_text` synthesizes
`f"Reviewed content ID: {reviewed_content_id}"` — the same wrong literal
the code reads — so every existing admissibility test is self-confirming
against a fixture that agrees with the recogniser by construction, never
against the real artifact shape.

**Fix.** Change `controller/evidence.py`'s `read_feedback_fields` to read
`"Reviewed review content ID:"`. Change `tests/fixtures.py`'s
`build_review_feedback_text`/`write_review_feedback` to emit the corrected
label, so existing fixture-based tests continue to exercise the
recogniser's real, intended behavior rather than a stale one.

**Regression coverage.** Add a test whose input is built from the real
artifact shape rather than the fixture that reproduces the recogniser's
own literal — either derived textually from
`.claude/commands/review-implementation.md`'s own step-6 template, or a
literal copy of the real `.ai-review/feedback/REVIEW_FEEDBACK.md` content
in this repository — and assert `read_feedback_fields` resolves
`reviewed_content_id` correctly from it. This is the extraction step that
was never performed for this field; it must fail against the pre-fix
recogniser and pass against the post-fix one.

**Scope of the "real artifact" confirmation (plan review round 1, finding
I4).** `read_feedback_fields`'s `reviewed_content_id` is consumed by
`evaluate_manual_stage_admissibility`, reached only from
`_decide_awaiting_manual_external_plan_review` — the **plan**-review
stage handler (`controller/evidence.py:592`). All three sources this
checkpoint cites in "Problem" describe the sibling **implementation**-review
stage or a different layer (`.claude/commands/review-implementation.md`'s
own literal; the real `.ai-review/feedback/REVIEW_FEEDBACK.md` artifact
in this repository, which is itself an implementation-stage verdict; and
`workflow-v2-1-core-mapping.json`'s description of the binding-field set,
which documents an amended requirement, not installed plan-stage
behavior). No plan-stage-shaped `REVIEW_FEEDBACK.md` existed anywhere in
this repository or its Git history at the time this plan was written — the
one now produced by this work item's own round-1 local-model plan review
(`.ai-review/feedback/REVIEW_FEEDBACK.md`, `Reviewer role:
LOCAL_MODEL_PLAN_REVIEW`, `Reviewed review content ID:` label) is the
first. The fix's direction is still correct — the currently-read literal
matches nothing either stage has ever produced, so correcting it to the
real label cannot regress anything — but this plan states plainly that the
plan-stage spelling is *inferred* from the implementation-stage sibling's
literal, not confirmed against a plan-stage artifact. REQ-4's regression
test must therefore be bound to a plan-stage-shaped input (a `Reviewer
role: MANUAL_EXTERNAL_PLAN_REVIEW` line, exercising the plan-stage
admissibility path `_decide_awaiting_manual_external_plan_review` actually
calls), not only to the implementation-stage artifact this checkpoint
names — binding the new test to the wrong stage's evidence would repeat
the original defect's shape: proving the recogniser against evidence from
a stage it does not serve.

### CP3 — reviewer-role normalization consistency

**Problem.** `controller/evidence.py`'s `_normalize_role` strips whitespace
and upper-cases before comparing a `Reviewer role:` value (accepting
CP4B's documented legacy lowercase spelling). `controller/job.py`'s
`_predicate_row3_block_feedback_current` compares the same field exactly,
unnormalized: `feedback.get("reviewer_role") == "LOCAL_MODEL_PLAN_REVIEW"`.
Two readers of the same artifact field in the same repository disagree
about what counts as a match. Not currently exploitable — the only writer
of the local-stage role line writes the canonical spelling, and the
divergent case is gated out before the predicate is reachable — but it is
a real, reproducible inconsistency, not a hypothetical one.

**Fix.** Route `job.py`'s comparison through the same normalization
`evidence._normalize_role` already applies (either by importing and
calling it, or by an equivalent local normalization applied identically),
so both readers agree on every input.

**Regression coverage.** Add a test that feeds a role line in a spelling
that previously discriminated between the two readers (e.g. the legacy
lowercase spelling, or one with incidental whitespace) through both
`evidence._normalize_role`-based comparisons and `job.py`'s row-3
predicate, and asserts they now agree. `_predicate_row3_block_feedback_current`
reads its input from disk via `evidence.read_feedback_fields(root,
feedback_dir)` (`controller/job.py:432-434`), not from an in-memory
value — so the test must write a real feedback file to a temporary
`feedback_dir` and exercise the predicate through that real read path,
not compare `evidence._normalize_role`'s and a bare string's output as a
pure unit-level comparison of two normalization functions, which would
not actually discriminate between the two readers CP3 fixes (plan review
round 1, finding O2). Build the feedback text for this test directly
(a local literal or a helper private to this test module) rather than
through `tests/fixtures.py`'s `build_review_feedback_text`/
`write_review_feedback` — CP2 rewrites exactly those functions in this
same revision window, and CP3's own regression test must not depend on
either their pre-fix or post-fix label to stay genuinely
implementation-order-independent (plan review round 1, finding I5; see
also the checkpoint-independence note above).

### CP4 — operator-contract and functional-review-checklist documentation accuracy

**Problem.** Gen1's own functional-review acceptance round
(`.ai-review/feedback/FUNCTIONAL_REVIEW.md`) found and confirmed four
checklist-accuracy issues, all filed as checklist problems rather than
Controller defects (the Controller's actual, tested, documented behavior
was correct in every case):

1. The checklist's `--json` examples place the flag *after* the
   subcommand (`inspect . --json`, `explain . --json`), which is not a
   valid invocation — `--json` is a global, top-level-only option and must
   precede the subcommand (`--json inspect .`). This is already correct in
   `README.md` and in Gen1's own `CONTROLLER_GEN1_PLAN.md`; only the
   checklist examples had it backwards.
2. The checklist states `explain` exits `10`. `cmd_explain` unconditionally
   returns `EXIT_OK` (`0`) in every branch — `10` (`EXIT_GATE`) is only
   ever produced by `job.STATUS_GATE_BLOCKED`, reachable from `step`/`run`
   alone. `tests/test_cli.py::ExplainCommandTest` already asserts this.
3. The checklist claims `inspect --json`'s payload "carries the full
   work-item payload (phase, checkpoints, approval state)". The actual
   payload (`controller/cli.py`'s `cmd_inspect`) wraps a `repository`
   object (`root`/`workflow_version`/`profile`) alongside a `work_item`
   object built by `_work_item_payload` — twelve fields
   (`work_item_id`, `work_item_type`, `work_item_kind`,
   `governing_workflow_version`, `phase`, `plan_revision`,
   `implementation_revision`, `functional_review_round`,
   `current_checkpoint_id`, `last_completed_checkpoint_id`,
   `registry_complete`, `incomplete_children`) — of which no field is
   named `plan_approval`, `technical_approval`,
   `functional_acceptance_status` or `blocking_decisions`. Describing the
   payload as carrying exactly "`phase` plus the checkpoint-related
   fields" is itself inaccurate: eight of the twelve `work_item` fields
   are neither (`work_item_id`, `work_item_type`, `work_item_kind`,
   `governing_workflow_version`, `plan_revision`, `implementation_revision`,
   `functional_review_round`, `incomplete_children`), and the wrapping
   `repository` object is not mentioned at all (plan review round 1,
   finding B2) — the correct, checkable claim is the negative one: the
   payload carries none of the four named approval-state fields, not an
   exhaustive positive enumeration of what it does carry.
4. The checklist's Flow 3 (`explain`, the functional-review gate) never
   documents the precondition case where `.ai-review/feedback/FUNCTIONAL_REVIEW.md`
   already exists, unconsumed, before testing begins.
   `controller/evidence.py`'s `AWAITING_FUNCTIONAL_REVIEW` handling already
   has a distinct, tested, automatic sub-case for this
   (`tests/test_evidence.py::AwaitingFunctionalReviewTest::test_unconsumed_findings_are_automatic`)
   — the code is correct; Gen1's checklist simply never told a tester to
   check for it, and its tester had to improvise an undocumented manual
   precondition check.

**Why this checkpoint needed re-scoping (plan review round 1, finding
B1).** Revision 1 specified CP4's deliverable as prose *inside* this
milestone's own future functional-review checklist, authored later, at
`AWAITING_FUNCTIONAL_REVIEW`, by `/prepare-functional-review` — after
CP1-CP3 already land and this item's own registry is already complete.
That placed CP4's only deliverable after the checkpoint meant to produce
it: `/milestone-implement`'s own per-checkpoint invocation would find no
`controller/`/`tests/`/protected-path change to make and no commit to
carry `Workflow-Checkpoint: CP4`, `complete_checkpoint` would mark it
`COMPLETE` vacuously, and nothing at `AWAITING_FUNCTIONAL_REVIEW` re-reads
this frozen plan to inject CP4's four corrections into the checklist
`/prepare-functional-review` authors from scratch — reproducing the exact
failure mode this checkpoint exists to prevent, with no gate anywhere
that could have caught it.

**Fix.** CP4 now authors a real, committed, implementation-time
deliverable — a checklist-corrections source file,
`docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md`,
written during this work item's own `IMPLEMENTING` phase, in CP4's own
checkpoint invocation, committed with `Workflow-Checkpoint: CP4`. This
path is moved into this work item's own
`implementation_stage.protected_paths` in
`docs/ai-workflow/registry/workflow-controller-gen1-correctness-hardening-artifacts.json`
by this same plan revision (rather than deferred to a later
`SELF_REVIEWING_PLAN` step — nothing gates doing it now, and doing it now
lets this plan review round confirm the deliverable path is already
protected, exactly the mechanism `workflow-v2-1-core-artifacts.json` uses
for `MILESTONE_WORKFLOW.md`/`REVIEW_PROTOCOL.md`), so `technical_approval`
binds it and the implementation-stage reviewer reads it as reviewed
content, not excluded bookkeeping. `/prepare-functional-review` step 3
explicitly permits linking the checklist to "a short file from
[`docs/ACTIVE_MILESTONE.md`]" — this milestone's own future functional
review links this file from there rather than re-deriving its content.

The file states, plainly and checkably:

1. every `--json` example precedes the subcommand it names (`--json
   inspect .`, never `inspect . --json`);
2. `explain` exits `0` (`EXIT_OK`) in every branch, never `10`;
3. `inspect --json`'s payload carries none of `plan_approval`,
   `technical_approval`, `functional_acceptance_status`,
   `blocking_decisions` — stated as the negative claim only (see
   "Problem" item 3 above for why a positive exhaustive list is the wrong
   shape here);
4. the `AWAITING_FUNCTIONAL_REVIEW` precondition-check step: before
   assuming the "checklist current, no findings yet" branch, confirm
   whether an unconsumed `.ai-review/feedback/FUNCTIONAL_REVIEW.md`
   already exists — citing
   `tests/test_evidence.py::AwaitingFunctionalReviewTest::test_unconsumed_findings_are_automatic`
   as the automatic sub-case's own regression coverage.

**Regression coverage (plan review round 1, finding M1).** Three of the
four items above are mechanically checkable, closing the gap review-request
challenge 4 raised in revision 1 and turning CP4 into a checkpoint with
real, automated, gate-bound evidence rather than a prose-only one. A new
test file, `tests/test_checklist_corrections.py`:

1. **`--json` placement.** Regex over the checklist-corrections file's own
   text: no occurrence of `--json` may appear after a subcommand token
   (`inspect`/`explain`/`step`/`run`/`resume`/`status`) on the same
   example line. Fails if a future edit reintroduces the backwards form.
2. **`explain`'s exit code.** Two assertions: the checklist-corrections
   file states the exit code as `0`; and, independently of the file's
   prose, an AST walk over `controller/cli.py`'s `cmd_explain` — the same
   idiom CP1's own scanner uses — asserts every `Return` node in that
   function returns `EXIT_OK`, catching drift at the source rather than
   only in the checklist (exactly the mechanism review-request challenge
   4 and finding M1 ask for).
3. **`inspect --json` payload description.** Two assertions: the
   checklist-corrections file names all four excluded fields; and,
   independently, a `types.SimpleNamespace` stub carrying the twelve
   attributes `_work_item_payload` reads is passed to
   `controller.cli._work_item_payload` directly, and the four excluded
   field names are asserted absent from the real returned dict's keys —
   bound to the actual function, not to a hand-maintained field list, so
   this is the one assertion that would have caught finding B2 itself
   (revision 1's own inaccurate positive claim) had it existed then, and
   the one M1 calls "the strongest argument for making CP4 a real,
   testable checkpoint."
4. **Flow 3 precondition (prose-only, per M1).** A presence check only:
   the checklist-corrections file's text references both
   `FUNCTIONAL_REVIEW.md` and the unconsumed-findings precondition by
   name. The underlying judgment — whether the described step is
   adequate for a human tester — is not mechanically checkable and
   remains this work item's own future functional-review round's
   concern, exactly as Gen1's was for the original four issues.

Run against the checklist-corrections file's pre-CP4 (nonexistent) state,
tests 1-3 must fail (no file to read, or, for 2's second assertion and
3's second assertion, pass trivially since they check `cli.py` directly
and `cli.py` is already correct today — CP4 changes documentation, not
`cli.py`); after CP4 lands, all four must pass.

## Testing requirements

At minimum, automated tests must cover, beyond the specific regression
tests named per checkpoint above:

1. the package-wide write-path scan (CP1/REQ-2) passes cleanly against the
   post-fix `controller/` tree, fails against each of the three synthetic
   instantiations that must fail, and does not fail against the two that
   must not (M2);
2. the real-artifact-bound, plan-stage-shaped manual-plan-review
   content-id test (CP2/REQ-4) passes against the post-fix recogniser;
3. the reviewer-role-normalization discriminating test (CP3/REQ-6),
   exercised through the real on-disk read path, passes;
4. CP4's checklist-corrections tests (REQ-7/8/9,
   `tests/test_checklist_corrections.py`) pass;
5. the full Controller suite (`python3 -m unittest discover -s tests -t .`)
   and all seven frozen Workflow conformance suites under `scripts/`
   (`workflow_fingerprint_test.py`, `workflow_state_test.py`,
   `workflow_test_harness_test.py`, `workflow_integration_test.py`,
   `workflow_acceptance_matrix_test.py`,
   `workflow_state_completion_obligations_test.py`,
   `workflow_fingerprint_generalization_test.py`) remain green, with no
   regressions introduced by CP1–CP3's changes to `identity.py`,
   `runtime.py`, `evidence.py`, `job.py`, or their fixtures.

No new live-worker (`CONTROLLER_LIVE_WORKER=1`) run is required unless
CP1's fix changes `identity.py` in a way that changes
`compute_tree_digest`'s output for the reviewed source (it does not — the
fix adds containment assertions around existing writes, it does not change
what gets written) — if it turns out to, the opt-in live disposable-repo
test must be re-run once and fresh evidence recorded, since that fixture's
own `_expected_controller_source_tree_digest` calls `identity.materialise`
directly.

## Migration / data-integrity notes

None. No schema, format, or persisted-shape change anywhere in this
milestone: CP1 adds assertions around existing writes without changing
their content; CP2 changes which text label is read, not any persisted
Controller-owned record shape; CP3 changes a comparison, not a record
shape; CP4 adds a new committed documentation file and a regression test
over existing, unchanged `controller/cli.py` behavior — no CLI output
shape, exit code, or persisted record changes.

## Plan review round 1 — disposition (`LOCAL_MODEL_PLAN_REVIEW`)

Round 1 reviewed bundle `1878dca3…b89a5` at plan revision 1 and returned
`REVISE` with 2 Blocking, 5 Important, 2 Optional and 2 Missing-test
findings. Every finding was re-validated against this repository, not
taken on the review's premise alone — the review's own "What was
independently verified and confirmed accurate" section additionally
reproduced every factual claim revision 1 made about the current Gen1
code and found it accurate, and confirmed the four dropped advisory
findings were correctly dropped. **All eleven findings are accepted;
none is rejected.**

| Finding | Disposition | Where |
|---|---|---|
| **B1** — CP4 had no implementation-time deliverable, so no gate ever evaluates REQ-7/8/9 | **Accepted.** Verified: `/milestone-implement` step 1f requires a per-checkpoint commit; `docs/ACTIVE_MILESTONE.md` is excluded at both stages; nothing re-reads the plan at `AWAITING_FUNCTIONAL_REVIEW` | CP4 rewritten around a new committed file, `docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md`, moved into `implementation_stage.protected_paths` in this revision |
| **B2** — CP4 item 3 / REQ-9 describe `inspect --json`'s payload inaccurately | **Accepted.** Verified directly: `_work_item_payload` returns twelve fields, only three checkpoint-related, wrapped under a `repository`+`work_item` object `cmd_inspect` builds | CP4 item 3 restated as the negative claim only; REQ-9 reworded to match; `docs/ACTIVE_MILESTONE.md` scope item 4 corrected the same way |
| **I1** — CP1's scanner cannot see the subprocess-mediated `tar` write | **Accepted.** Verified: `subprocess.run` is not a recognised write-call form; no same-function guard can make the scanner attribute one to it | CP1's "Fix" item 4 states the exemption explicitly; REQ-2's write-form set names `subprocess`-mediated writes as out of scope by construction |
| **I2** — CP1's two write-form statements contradicted each other, and the broader one fails CP1's own unfixed sites | **Accepted.** Verified: `_WRITE_CALL_SUFFIXES` (`tests/test_target_state.py:481-484`) flags `identity.py:337`'s `shutil.rmtree` and `identity.py:272`'s `tmp.unlink` (the latter added to the set), neither in revision 1's fix list | CP1 adopts the existing broader set plus `unlink`; the fix list now guards both newly-caught sites |
| **I3** — CP1 named no containment root per site, and `runtime_root` is out of scope at three of the five | **Accepted.** Verified: `_extract_clean`/`_extract_dirty`/`_publish_source_pin` all take `dest` (and `_publish_source_pin` also `source_dir`), never `runtime_root` | CP1's "Fix" section names the root per site (`source_dir` or `dest`, never `runtime_root`); zero signature changes required, stronger than revision 1's implied need for one |
| **I4** — CP2's "confirmed three independent ways" does not cover the plan-review stage the fix actually governs | **Accepted.** Verified: `evaluate_manual_stage_admissibility` is reached only from the plan-stage handler; all three cited sources are implementation-stage or a different layer; no plan-stage `REVIEW_FEEDBACK.md` existed anywhere before this work item's own round 1 | CP2 states the plan-stage literal is inferred, not confirmed; REQ-4's test bound to a plan-stage-shaped (`MANUAL_EXTERNAL_PLAN_REVIEW`) input |
| **I5** — the mutual-independence claim is false for CP2/CP3 | **Accepted.** Verified: CP2 rewrites `tests/fixtures.py`'s `build_review_feedback_text`; CP3 names `tests/test_job_validation.py`, a consumer of it | Independence paragraph corrected; CP3 given an explicit constraint not to depend on that fixture |
| **O1** — `_extract_clean`'s write-site description conflates `git archive` (writes nothing) with `tar` (the actual writer) | **Accepted** | CP1's "Problem" list corrected with the `_run_git_binary` distinction |
| **O2** — CP3's regression test must exercise the real on-disk read path, not a pure unit comparison | **Accepted.** Verified: `_predicate_row3_block_feedback_current` reads via `evidence.read_feedback_fields(root, feedback_dir)` from disk | CP3's "Regression coverage" states the on-disk requirement |
| **M1** — CP4's three mechanically-checkable facts deserve an automated assertion | **Accepted** — this is also the resolution B1 required | `tests/test_checklist_corrections.py`, three of four items automated |
| **M2** — CP1's negative instantiation needs its synthetic site specified | **Accepted** | CP1's "Regression coverage" specifies three synthetic, `ast.parse`-based instantiations, mirroring the existing `test_scanner_flags_a_synthetic_write_call` pattern, never a real module mutated at test time |

One consequence of the B1/I1/I2/I3 fixes, found while resolving them and
not itself a separate round-1 finding: making the scanner package-wide
(REQ-2) would have produced two new false positives at
`controller/job.py:1617,1620`'s `runtime.write_bytes(...)` calls — a
dotted-callee suffix match against an already-containment-checked call
into `runtime.py`'s own public API. The scanner's exemption list now
names `runtime.<name>` calls explicitly (CP1's "Regression coverage"),
confirmed by direct inspection that no other module under `controller/`
contains a write-call-suffix match today outside `identity.py` and these
two calls.
