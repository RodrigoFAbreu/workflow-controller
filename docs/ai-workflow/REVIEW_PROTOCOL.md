# Review Bundle Protocol

Authoritative reference for how review bundles are built, what they must
contain, how external feedback comes back in, and how to keep context usage
low across the milestone workflow. State transitions that use this protocol
are defined in `docs/ai-workflow/MILESTONE_WORKFLOW.md`.

## Generating a bundle

```bash
./scripts/prepare-ai-review.sh <base-sha> <stage> [work-item-id]
```

`<stage>` is one of: `plan`, `implementation`, `post-fix`,
`functional-review`.

`<base-sha>` is the commit the diff should be computed from — normally the
milestone's starting commit (the last commit before this milestone's work
began, e.g. the completion commit of the previous milestone). For the
`plan` stage, this must agree with the resolved work item's own declared
`base_commit` (`WORKFLOW_STATE.json`'s `work_items[<id>].base_commit`) —
the script refuses before generating any content on a genuine
disagreement, naming both (`D-Fingerprint-Generalization`).

The work-item-id argument is **required when `<stage>` is `plan`** — never
resolved from the live `active_work_item_id` for that stage, since
`MANIFEST.md`'s identity binding depends on it
(`D-Fingerprint-Generalization`). It remains optional for every other
stage (`implementation`/`post-fix`/`functional-review`): passing it writes
the bundle under the per-work-item layout (`.ai-review/<work-item-id>/`,
see below); omitting it writes the flat compatibility layout
(`.ai-review/current/`, `.ai-review/feedback/`) directly under
`.ai-review/`. For the `plan` stage, the script's own final step also
writes `MANIFEST.md` (`scripts/workflow_fingerprint.py --write-manifest`,
the same CLI entry point every other invocation uses, never a
reimplementation) — no separate manual step is needed.

The script is deterministic and safe to rerun: it always regenerates the
git-derived files from current repository state, and leaves author-written
files untouched if they already exist (it only creates empty stubs for
missing ones, so the bundle structure stays stable at every stage).

### Bundle location: `.ai-review/<work_item_id>/` layout, with a compatibility fallback

The canonical layout is per-work-item:

```text
.ai-review/<work_item_id>/
├── current/            # the bundle currently under review (see structure below)
├── feedback/            # external feedback, placed here by the reviewer/user
└── review-bundle.tar.gz # archive of current/
```

For any work item that has never had this layout created yet, commands
read the flat compatibility path instead: `.ai-review/current/` and
`.ai-review/feedback/` directly under `.ai-review/` (no work-item
subdirectory). The resolution rule (implemented in
`scripts/workflow_fingerprint.py`'s `resolve_bundle_dir`/
`resolve_feedback_dir`, not left to prose alone) is: prefer the scoped
layout once this work item is on it, else fall back to the flat path.
`feedback/` is stage-agnostic and always follows this same
scoped-else-flat rule, for every stage alike, keyed on
`.ai-review/<work_item_id>/feedback/`'s own existence.

"Is on the scoped layout" is decided, for the **bundle** directory, from
the work item's own root directory `.ai-review/<work_item_id>/` — never
from the transient existence of the `current/` inside it, which
`withdraw_bundle` renames to a `current.rejected-<token>/` sibling on
every quarantine. Gating on `current/` flipped an item that had
demonstrably been generating scoped bundles back onto the flat path for
its next round, while `prepare-ai-review.sh`, given the same
`[work-item-id]` argument as the round before, still wrote
`.ai-review/<work_item_id>/current/` — withdrawing the regenerated bundle
too, since the author-written stage document landed at the flat path and
the generator's own empty stub was what the closing completeness check
read. A work item that has never had a `.ai-review/<work_item_id>/`
directory created still resolves flat, so the genuinely flat-generated
legacy layout — what omitting `prepare-ai-review.sh`'s optional
`[work-item-id]` argument really does write — is unchanged.

For the **plan stage specifically**, the bundle directory is never the
flat fallback: the work-item-id argument is required (above), so
`ROOT_DIR`/`<bundle_dir>` always resolve to
`.ai-review/<work_item_id>/current/` by construction
(`D-Fingerprint-Generalization`). `workflow-v2-1-core`'s own historical
flat `.ai-review/current/`/`.ai-review/review-bundle.tar.gz` were
relocated to `.ai-review/workflow-v2-1-core/` as a one-time migration when
this rule landed; `.ai-review/feedback/` was deliberately left flat (it is
stage-agnostic and every non-plan stage's bundle is still flat too).

That plan-stage rule is **an argument to the resolver, not prose a caller
is trusted to remember**: a plan-stage caller passes
`resolve_bundle_dir(repo_root, work_item_id, stage="plan")`, which returns
`.ai-review/<work_item_id>/current` with no existence gate at all. The
compatibility gate above — even keyed on the work item's own root
directory — is correct only where the flat layout is a reachable
generation target. It is for `implementation`/`post-fix`/
`functional-review`, whose `work-item-id` argument to
`prepare-ai-review.sh` is optional and whose omission really does write
`.ai-review/current/`; it is not for `plan`, whose argument is required.
So for a work item's **very first** plan bundle — nothing under
`.ai-review/<work_item_id>/` yet, a brand-new milestone or the first
`/milestone-plan <child-id>` on a remediation child — a caller that omits
`stage="plan"` gets the flat path, and authors `REVIEW_REQUEST.md`/
`TEST_RESULTS.md`/`CONTEXT_FILES.txt` at `.ai-review/current/` while
`prepare-ai-review.sh` writes and validates
`.ai-review/<work_item_id>/current/`. That split is what made a first plan
bundle fail its first attempt and succeed on an identical second one (the
failed first generation having created the scoped directory the second
attempt then resolved). An unrecognized `stage` value raises
`InvalidBundleStageError` rather than falling through to the
compatibility branch, so a typo cannot silently reintroduce it.
`<feedback_dir>` takes no stage argument at any stage;
`resolve_feedback_dir` is deliberately untouched by this rule.

The implementation/post-fix stages keep a **narrower** version of the same
split, and it is accepted rather than closed (workflow system audit,
convergence pass 12, ledger row `O32`). Their `work-item-id` argument is
optional, so the resolver's existence gate is correct for them: an
omitted-id invocation really does write `.ai-review/current/`, and the
authoring and generating halves agree there. But an operator who *passes*
the optional id while `.ai-review/<work_item_id>/` does not exist gets the
split back — the resolver answers flat, the generator writes scoped. For a
tracked item this needs the scoped directory to be genuinely absent, which
past the plan approval means the gitignored, documented-disposable
`.ai-review/` tree was deleted between rounds; `withdraw_bundle`'s
quarantine does not cause it (the gate keys on the work item's own root
directory, not on `current/`).

What happens then is **fail-closed and self-healing, not destructive**: the
generation refuses at
`assert_review_request_states_review_content_id`, reading the generator's
own empty `REVIEW_REQUEST.md` stub in the scoped directory and naming that
exact path. Nothing is published, nothing is withdrawn, no `REJECTED`
marker is written, and the bundle authored at the flat path is left
untouched. The failed attempt has created `.ai-review/<work_item_id>/`, so
the resolver answers scoped from then on and an identical second attempt
succeeds. Two ways to avoid the first-attempt failure entirely: omit the
optional `[work-item-id]` (the flat form, which agrees with itself), or
author into `.ai-review/<work_item_id>/current/` directly when you intend
to pass it. This is deliberately not closed by changing the resolver: the
existence gate is what makes the omitted-id compatibility form work at
all, and no supported invocation is blocked here — only delayed by one
retry, with the error naming the directory to author into.

`.ai-review/` is entirely gitignored, including `.ai-review/source/` —
files a human places there as raw proposal material for Claude to read,
never review-bundle content. `CONTEXT_FILES.txt` must never list a path
under `.ai-review/source/`; `scripts/prepare-ai-review.sh` skips (and
warns on) any such entry as defense in depth.

### Bundle structure

```text
<bundle_dir>/                  # .ai-review/<work_item_id>/current/, or .ai-review/current/ (compatibility)
├── REVIEW_REQUEST.md          # author-written, see below
├── PLAN.md                    # author-written; plan-stage content
├── IMPLEMENTATION_SUMMARY.md  # author-written; implementation-stage content
├── TEST_RESULTS.md            # author-written; tests actually run
├── CHANGED_FILES.txt          # generated: metadata + diff --stat + name-status + git status
├── COMMITS.txt                # generated: git log base..HEAD
├── DIFF.patch                 # generated: full diff from base to working tree
├── CONTEXT_FILES.txt          # author-written: list of unchanged context files to include
├── MANIFEST.md                # generated (scripts/workflow_fingerprint.py --write-manifest only):
│                               # bundle_id, review_content_id, protected/excluded path lists,
│                               # and diagnostic-only worktree_root/generation_head (see below)
└── files/                     # generated: final copies of changed + listed context files
```

### Generation diagnostic metadata (`worktree_root`/`generation_head`)

`MANIFEST.md` records the absolute worktree root and HEAD SHA the bundle
was generated from, as plain diagnostic lines — never hashed into
`review_content_id`, and not part of any identity-bearing field contract.
This is **portability vs. local staleness, split by consumer**:

- A **repository-local command** — today `/approve-review`, `/review-plan`,
  and `/review-implementation`, all three calling this at its default,
  permissive `require_metadata=False` — runs inside a real, current
  worktree and can
  meaningfully ask "is this the same worktree and HEAD I'm sitting in
  right now": it calls
  `workflow_fingerprint.assert_local_generation_matches(...)` and stops,
  naming both values, on a mismatch. This is the actual first-party
  Milestone-8 incident (a stale bundle read from a different worktree) the
  mechanism exists to catch. A third, stricter `require_metadata=True`
  mode also exists on the same function (`GPT-R62-001`) for a caller that
  cannot tolerate a metadata-less legacy bundle at all, but no such caller
  is live in this repository today.
- An **external reviewer** receiving the bundle via the archive is, by
  design, outside the generating worktree — that is not staleness, it is
  the archive doing its job. External review validates the exact
  `bundle_id` and the reported Git metadata (base/head SHAs, branch)
  instead; it never checks `worktree_root`/`generation_head` for equality.

### Author-written files — what belongs in each

- **REVIEW_REQUEST.md** — the index. See "Review request format" below.
  Must state `review_content_id: <hex>` as a plain labelled line, agreeing
  with `MANIFEST.md` (`OPUS-R18-005`). The value is not a free-form claim:
  compute it with the canonical entry point named under "Computing
  `review_content_id`" below — the same computation the
  generator's own `--write-manifest` step performs, so a line written this
  way agrees with `MANIFEST.md` by construction rather than by care.
- **PLAN.md** — populated at the `plan` stage: the actual execution plan
  being reviewed. Leave empty (or omit updating it) at later stages. Must
  state the plan's current `(Revision N)` marker — a bundle whose `PLAN.md`
  disagrees with the authoritative plan document's own declared revision
  fails the stage-completeness check (`assert_stage_completeness`).
- **IMPLEMENTATION_SUMMARY.md** — populated at `implementation`/`post-fix`
  stages: what was built, checkpoint by checkpoint, and why. Must state
  `implementation_revision: <N>` matching the work item's current counter
  (`WORKFLOW_STATE.json`'s `implementation_revision`) — same
  stage-completeness discipline as `PLAN.md`'s revision marker.
- **TEST_RESULTS.md** — exact commands run and their real outcome. Never
  state a check passed unless it actually ran in this session. **At the
  `plan` stage**, must additionally open with a `stage: plan (revision N)`
  line (`N` matching the plan document's current revision) and a
  `head: <sha>` line (matching this generation's own HEAD) — a bundle
  whose `TEST_RESULTS.md` is missing, empty, names a different round, a
  different HEAD, or carries forward a previous implementation round's
  evidence unexamined fails the closing consistency check (item 272,
  `assert_test_results_consistent_with_plan_review_request`,
  `finalize_bundle_generation`) and is withdrawn rather than published.
- **CONTEXT_FILES.txt** — one repo-relative path per line, no comments. Only
  the files a reviewer genuinely needs beyond the diff itself (e.g. the ADR
  a decision follows, the domain glossary entry a rule depends on). The
  script copies every listed file into `files/` verbatim. Keep this short —
  it is not a dump of the whole `docs/` tree. Never list a path under
  `.ai-review/source/` (source-proposal material, not review content).

**One prohibition, across every author-written file** (`GPT-R9-001`,
documented by the workflow system audit's convergence pass 12, ledger row
`O37`): none of them may contain a line matching `bundle_id: <64 hex
chars>`. `MANIFEST.md` is the single schema-defined location for the
bundle's own identity, and that one field is normalized out of the digest
by construction; the same line anywhere else would be a claim about the
bundle that the identifier could not see, so `compute_bundle_id` fails
closed on it with `ForeignBundleIdFieldError`, naming the file and the
line numbers. It is an easy line to write by accident, because the
feedback protocol below asks the *reviewer* to quote the bundle id back
(`Reviewed bundle ID:`) — that file lives under `<feedback_dir>`, outside
the bundle, and is never hashed. State the bundle id in prose if a
`REVIEW_REQUEST.md` needs to mention it; do not give it that labelled
form. Nothing is published or withdrawn when this fires: the generation
refuses with `current/` intact.

### Computing `review_content_id`

Every generation driver tells the author to refresh
`<bundle_dir>/REVIEW_REQUEST.md`'s `review_content_id: <hex>` line before
running the generator, because
`assert_review_request_states_review_content_id` runs inside
`--write-manifest` and *refuses* the whole generation on a stale one
(nothing published, nothing withdrawn, and never a silent fix-up on the
author's behalf). **This section is the one place that says how to obtain
the value.** Commands reference it; none of them restate the algorithm,
and neither does this section — each recipe below is a call into the
single existing entry point for that stage, never a description of what
that entry point does.

- **Plan stage** —
  `workflow_fingerprint.compute_review_content_id_plan_stage_for_work_item(repo_root, work_item_id)`,
  whose first element is the digest. It resolves `work_item_type`,
  `plan_revision`, `base_commit` and all three classification sets from
  `WORKFLOW_STATE.json`/`<work_item_id>-artifacts.json` itself, so there is
  nothing further for a caller to supply or to get wrong.
- **Implementation / post-fix stages** —
  `workflow_state.approval_review_content_id(repo_root,
  stage="implementation", base_commit=<the work item's own base_commit>,
  head="HEAD", work_item_type=<the item's own type>,
  work_item_id=<the item's own id>,
  artifacts_path=workflow_fingerprint.artifacts_path_for_work_item(work_item_id))`.
  This wraps `load_implementation_stage_classification` and
  `compute_review_content_id_implementation_stage_at_commit` in one call —
  the same pair the generator's sole implementation-stage manifest writer
  uses, at the same commit-source anchor. Two things it is deliberately
  *not*: it is never the worktree-source
  `compute_review_content_id_implementation_stage` (no `commit` parameter
  at all, scoped instead to whatever happens to be dirty), which would not
  reproduce what `MANIFEST.md` records; and `artifacts_path` is never
  `load_implementation_stage_classification`'s own default, which resolves
  to `workflow-v2-1-core`'s file and would compute a different work item's
  classification. `/review-implementation` step 4 carries the long form of
  both cautions.

**Anchor.** Both recipes measure committed content at `HEAD`, so run them
*after* this round's durability commit (the
`Workflow-Bundle-Generation-Record` commit at the implementation/post-fix
stages) and before `scripts/prepare-ai-review.sh` — which is exactly where
every driver's own step places the refresh. Running them earlier states a
digest for a commit the generation will not be measured at.

### What the script does NOT do

- It does not judge relevance — `CONTEXT_FILES.txt` is a human/Claude
  decision.
- It does not include build outputs, `.gradle/`, APKs, generated binaries,
  secrets, or the full repository — only files that are part of the diff or
  explicitly listed as context.
- It does not commit anything or touch git state beyond reading it.

### Repairing an artifact declaration after an approval

`docs/ai-workflow/registry/<work_item_id>-artifacts.json` is not
identity-neutral, and the contrary claim that used to appear in
`workflow_fingerprint.py`'s docstrings and in review correspondence was
false (salvage audit `I9`, reproduced against this repository's own
history).

**What is true.** The declaration file's own *bytes* are excluded from
both content projections — it lives under `docs/ai-workflow/registry/`,
which both stages exclude. **What is also true, and is the part that was
missing:** each stage's `review_content_id` is a digest over that stage's
protected content **plus the classification sets themselves** —
`compute_review_content_id_plan_stage`'s projection carries
`sorted(protected_paths)`, `sorted(excluded_paths)` and
`sorted(excluded_prefixes)`, and its implementation-stage counterpart
carries the equivalent four. Those sets are read out of the declaration
file. So:

| Edit | Plan-stage `review_content_id` | Implementation-stage `review_content_id` |
|---|---|---|
| `plan_stage.*` | **changes** → `plan_approval` stales | unchanged |
| `implementation_stage.*` | unchanged | **changes** → `technical_approval` stales |
| a comment/justification string only | **changes** if it is a set *key*; unchanged if only the justification value | same rule |

This is the intended cryptographic contract, not a defect: *what is
excluded is a reviewed fact*. An exemption that made declaration edits
invisible to identity would let a session widen an exclusion and re-bless
the resulting digest with no gate having seen the classification change —
the exact attack `implementation_stage.protected_paths`' own
self-protection entry (`OPUS-R25-006`/`OPUS-R26-004`) exists to prevent.

**Therefore, the repair procedure is:**

1. **Before the stage's approval** — the ordinary case, and the one to
   prefer. `/milestone-plan` step 4 (`SELF_REVIEWING_PLAN`) exists
   precisely to confirm the generated declaration fits this item's own
   footprint *at both stages* before the bundle is generated. A
   declaration corrected here costs nothing: no approval exists yet.
2. **After the plan approval, `plan_stage` half.** The approval is stale
   the moment the edit is committed, and `implementing_entry_reachable`
   and `/accept-milestone`'s own registry-coverage check both refuse. Run
   the plan revision through its real gate: `/apply-plan-review` (or
   `/milestone-plan` again) to publish a new `plan_revision`, then the
   two-stage plan review, then `/approve-review plan`. Do **not**
   hand-edit `plan_approval`, and do not report the item as `CURRENT` on
   the strength of the stored `status` field — that field is a cache, and
   recomputation is the authority.
3. **After the technical approval, `implementation_stage` half.** Same
   shape one stage later: `mark_technical_approval_stale`, then a
   `post-fix` round through `/apply-implementation-review`, then
   `/approve-review implementation`.
4. **Never** widen an exclusion to make a path disappear from a
   projection in order to avoid a re-review. Widening the exclusion set
   *is itself* a change to the reviewed facts, and it moves the digest
   anyway.

**Reading the truth rather than the cache.** `plan_approval.status` /
`technical_approval.status` record what was true when written.
`workflow_state.approval_is_current(repo_root, work_item, stage=...,
base_commit=...)` recomputes; so does
`workflow_fingerprint.compute_review_content_id_plan_stage_for_work_item`
compared against `approved_review_content_id`. When the two disagree, the
recomputation wins and the stored `status` is stale bookkeeping, not
evidence.

## Review request format

`REVIEW_REQUEST.md` must state:

- review stage;
- objective;
- branch, base SHA, HEAD SHA;
- active milestone and checkpoint;
- summary of proposed or implemented behavior;
- architecture decisions;
- schema/migration implications;
- backup compatibility implications;
- UI/UX decisions;
- tests run and results;
- known limitations;
- unresolved questions;
- specific areas the reviewer should challenge.

It must **not** contain a `bundle_id: <64 hex chars>` line — see the
prohibition under "Author-written files" above.

Keep it concise — it points at `DIFF.patch` and `files/`, it does not repeat
their content.

For a **plan** review, there may be no production diff yet. Still include the
plan itself (`PLAN.md`) and directly relevant domain/architecture files via
`CONTEXT_FILES.txt`.

For an **implementation** review, `DIFF.patch` must be the real milestone
diff and `files/` must hold the final changed files, not a summary.

## Feedback protocol

External plan/implementation feedback is placed at:

```text
<feedback_dir>/REVIEW_FEEDBACK.md
```

(`.ai-review/<work_item_id>/feedback/`, or the flat compatibility
`.ai-review/feedback/` — see "Bundle location" above.)

Required structure:

```markdown
# Review Decision

Status: APPROVE | REVISE | BLOCK

Reviewed bundle ID: <the exact bundle_id this feedback reviewed>
Reviewed base commit: <the exact base_commit this feedback reviewed>
Work item: <the exact work_item_id this feedback reviewed>

## Blocking findings

## Important findings

## Optional findings

## Missing tests

## Architecture and maintainability concerns

## Migration and data-integrity concerns

## Usability concerns

## Required acceptance criteria
```

The three binding fields (`Reviewed bundle ID:`, `Reviewed base commit:`,
`Work item:`) are required on every ordinary review round, not only WF0's
one-time bootstrap check (`OPUS-R14-002`, generalized here). Feedback
missing any of the three, or whose values disagree with the bundle
actually being approved against, is rejected naming both the feedback's
own value and the current one
(`workflow_fingerprint.parse_review_feedback_binding_fields`/
`assert_feedback_matches_bundle`, `WFR-03`) — never applied at face value.

User functional-testing feedback is placed at:

```text
<feedback_dir>/FUNCTIONAL_REVIEW.md
```

(Free-form findings; no binding-field requirement, since functional review
has no `bundle_id`/`review_content_id` of its own to bind against —
`/apply-functional-review` classifies each finding.)

**Claude must validate feedback, never apply it blindly.** Every blocking and
important finding must end up either resolved, or explicitly rejected in the
plan/summary with repository evidence (file, line, test, or doc reference)
and a clear explanation.

## Local reviewer commands (operator ergonomics)

Two commands (`workflow-v2-3`) give an operator a repository-local, second
opinion before handing a stage to its real gate, without adding a new
lifecycle state or ledger stage: `/review-implementation` (usable while a
work item sits at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) and
`/review-functional` (usable while a work item sits at
`AWAITING_FUNCTIONAL_REVIEW`). Both implement a model-independent review
role — nothing about either command's contract, checks, or report format
names a specific model. Neither ever writes
`docs/ai-workflow/WORKFLOW_STATE.json` or `docs/ACTIVE_MILESTONE.md`,
approves a stage, applies a finding, or advances `phase`. Their write
behavior toward their own review-feedback artifact differs, though:
`/review-implementation` writes the current `<feedback_dir>/
REVIEW_FEEDBACK.md` once its own pre-write guards pass (see
`.claude/commands/review-implementation.md` step 7); `/review-functional`
remains strictly report-only and writes nothing, including
`<feedback_dir>/FUNCTIONAL_REVIEW.md` — unchanged by this milestone. There
is no authoritative round for `/review-functional`'s report to become, and
`<feedback_dir>/REVIEW_FEEDBACK.md` is not its destination: that report is a
checklist-completeness opinion, never a `Status:` verdict (see the next
paragraph), and the functional gate's own artifact is the user-written
`<feedback_dir>/FUNCTIONAL_REVIEW.md`, which only `/apply-functional-review`
ever acts on. An operator may use the report to revise that checklist by
hand — that choice is always the user's, never automatic.
`/review-implementation` works differently: once its own pre-write guards
pass, its write *is* the authoritative
`<feedback_dir>/REVIEW_FEEDBACK.md` round the moment it lands, with no
separate operator installation step. `/apply-implementation-review`,
`/apply-functional-review`, `/approve-review`, and `/accept-milestone`
remain the only commands that ever act on a real, recorded review round; neither local reviewer command changes any of
their behavior.

The two commands' reports are deliberately differently shaped, since they
review different-shaped artifacts: `/review-implementation`'s report follows
this file's own `REVIEW_FEEDBACK.md` structure (`Status: APPROVE | REVISE |
BLOCK`, the same binding fields an external round's feedback carries), since
an implementation bundle is exactly what that structure describes.
`/review-functional`'s report is a checklist-completeness/
evidence-reproducibility opinion instead — never a `Status:` verdict — since
there is no bundle or ledger stage at the functional-review gate for a
verdict to gate. This asymmetry is intentional, not an inconsistency between
an otherwise-matched pair of commands.

`/review-functional` also illustrates a narrower asymmetry worth noting
explicitly: unlike `/prepare-functional-review` (whose subject,
`FUNCTIONAL_REVIEW.md`, is a user-written checklist bound to no bundle, so a
work-item-scoped `REJECTED` marker can never reach it), `/review-functional`
does consult that marker before composing its report — a withdrawn work item
must not receive an advisory functional-review opinion either, even though
`/review-functional` itself touches no bundle at all.

## Context-efficiency rules

- Load only the active status (`docs/ACTIVE_MILESTONE.md`), `docs/ROADMAP.md`,
  the active execution guide, and files relevant to the current checkpoint.
- Load full milestone reference docs only when a detail is genuinely
  unresolved from the execution guide.
- Do not load completed milestone docs (`docs/milestones/completed/`) unless
  historical decisions are needed to understand the current change.
- Do not load `docs/agent-context/` during feature planning or
  implementation — it is a meta-analysis of the context system itself, not
  product or architecture context.
- Do not reread the same large file repeatedly within one session without a
  new reason to.
- Keep `docs/ACTIVE_MILESTONE.md` factual and concise — current state, not a
  chronological journal.
- Keep execution guides operational (steps, checks) and reference guides
  detailed but conditional (full rationale, invariants).
- Summarize command output in docs/bundles rather than pasting full logs.
- Use the git diff and review bundle as the review artifact — do not
  substitute a prose-only summary for the real diff.
- Compact the conversation or start a fresh session at milestone/review
  boundaries when the session has grown large.
- Never list a `.ai-review/source/` path in `CONTEXT_FILES.txt` or treat it
  as review content — it is raw proposal material a human places there for
  Claude to read, not part of any bundle (`WFR-16`).
