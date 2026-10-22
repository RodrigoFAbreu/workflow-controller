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
see below); omitting it writes the flat compatibility bundle layout
(`.ai-review/current/`) directly under `.ai-review/`. (Where feedback is
placed never depends on this argument — see "Feedback directory" below.) For the `plan` stage, the script's own final step also
writes `MANIFEST.md` (`scripts/workflow_fingerprint.py --write-manifest`,
the same CLI entry point every other invocation uses, never a
reimplementation) — no separate manual step is needed.

The script is deterministic and safe to rerun: it always regenerates the
git-derived files from current repository state, and leaves author-written
files untouched if they already exist (it only creates empty stubs for
missing ones, so the bundle structure stays stable at every stage). At the
`plan` stage (workflow-2.6.0) the author-written files are read from
`<plan_inputs_dir>` instead and the bundle is assembled in a staging
directory — see "Plan-stage staging generation and `<plan_inputs_dir>`"
below.

### Bundle location: `.ai-review/<work_item_id>/` layout, with a compatibility fallback

The canonical layout is per-work-item:

```text
.ai-review/<work_item_id>/
├── current/            # the bundle currently under review (see structure below)
├── feedback/            # external feedback, placed here by the reviewer/user
├── plan-inputs/         # plan stage only (workflow-2.6.0): the author-written inputs, <plan_inputs_dir>
└── review-bundle.tar.gz # archive of current/
```

For any work item that has never had this layout created yet, commands
read the flat compatibility bundle path instead: `.ai-review/current/`
directly under `.ai-review/` (no work-item subdirectory). The resolution
rule (implemented in `scripts/workflow_fingerprint.py`'s
`resolve_bundle_dir`, not left to prose alone) is: prefer the scoped
layout once this work item is on it, else fall back to the flat path.

#### Feedback directory (`D-Feedback-Layout`, workflow-2.6.0)

`<feedback_dir>` — wherever any command, document or tool says it — is
exactly what `workflow_fingerprint.resolve_feedback_dir(repo_root,
work_item_id)` returns. That one function is the only resolver; nothing
restates or reimplements it. It is stage-agnostic (no stage argument at
any stage) and is keyed on a durable fact, the work item's own
`feedback_layout` field in the worktree's
`docs/ai-workflow/WORKFLOW_STATE.json`:

- **`feedback_layout: "scoped"`** — written once, at creation, by
  `route_work_item`'s fresh-id branch and
  `create_remediation_child_work_item`, for every work item created under
  `2.6.0` or later; never written on resume, never changed, never
  back-filled. `<feedback_dir>` is `.ai-review/<work_item_id>/feedback/`
  **by construction**: no existence gate, so two scoped items never share
  a path and no other item's feedback file — completed or live — is ever
  consulted.
- **Legacy** — the entry has no `feedback_layout` field, there is no entry
  (a `"1"`-governed item, which carries none by construction), or there is
  no state file (a pre-activation repository). The unchanged pre-`2.6.0`
  rule applies: `.ai-review/<work_item_id>/feedback/` if that directory
  already exists, else the flat, shared `.ai-review/feedback/`. An active
  legacy item therefore keeps finding its unconsumed flat file across the
  update; it is never moved.
- **Refused** — a state file that exists but is a symlink, is not JSON, or
  whose top level, `work_items` or entry is not an object
  (`FeedbackLayoutUndecidableError`); a `feedback_layout` value other than
  `"scoped"`, `null` included (`UnknownFeedbackLayoutError`). Never a
  fallback to the legacy rule.

Every feedback **writer** creates the resolved directory first through
`workflow_fingerprint.ensure_feedback_dir(repo_root, work_item_id)` —
`/review-plan`, both `/review-implementation` writers,
`/prepare-functional-review` and the functional-review consumed marker
(`mark_functional_review_consumed`). It creates only the resolved
directory, so it never flips a legacy flat item onto the scoped path. The
functional-review consumed marker (`FUNCTIONAL_REVIEW.consumed`) lives in
the same directory, so it is per item for scoped items. Commands that ask
an operator to paste feedback (`/record-manual-plan-review`,
`/record-manual-implementation-review`, and the gate reports) print the
exact resolved path.

**Supported contract for external tools.** Controller, or any other tool
that needs to locate a work item's feedback, runs:

```bash
python3 scripts/workflow_fingerprint.py --resolve-feedback-path <work-item-id>
```

which prints one JSON object and writes nothing:

```json
{"feedback_dir": "...", "functional_review_path": "...", "layout": "scoped", "review_feedback_path": "...", "work_item_id": "..."}
```

`layout` is `"scoped"`, `"legacy-scoped"` or `"legacy-flat"`; every path is
POSIX and repo-root-relative. It is built from `resolve_feedback_dir`
itself — never a second implementation — and a tool must consume it rather
than copy the scoped-else-flat rule, which is wrong for every scoped item.

**Ownership guards.** A scoped item's directory is private, so its writers
never meet another item's file. For a legacy item resolving flat,
`assert_feedback_not_owned_by_other_work_item(existing, work_item_id=...,
state=<parsed WORKFLOW_STATE.json>)` still refuses a file whose
`Work item:` names another item — except that an owner whose own entry is
at a terminal phase (`MILESTONE_COMPLETE`) is non-blocking, since terminal
state proves no consumer remains. The new writer replaces that file whole,
with its own binding fields; it is never reinterpreted as the writer's. A
non-terminal owner, or an owner absent from state, still refuses, and a
scoped writer is never relaxed. `/record-manual-plan-review` and
`/record-manual-implementation-review` additionally refuse a pasted file
whose `Work item:` is present and names another item
(`assert_manual_feedback_names_work_item`); a file without that field is
still bound by the hard `review_content_id` check.

#### Plan-stage staging generation and `<plan_inputs_dir>` (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0)

`<plan_inputs_dir>` — wherever any command or document says it — is exactly
what `workflow_fingerprint.resolve_plan_review_inputs_dir(repo_root,
work_item_id)` returns: `.ai-review/<work_item_id>/plan-inputs/`, a sibling
of `current/`. At the `plan` stage the four author-written files
(`REVIEW_REQUEST.md`, `TEST_RESULTS.md`, `CONTEXT_FILES.txt`,
`IMPLEMENTATION_SUMMARY.md`) are written there, never into `<bundle_dir>`.
`/milestone-plan` step 6 and `/apply-plan-review` step 5 name it.

`scripts/prepare-ai-review.sh`'s plan stage assembles the bundle in
`.ai-review/<work_item_id>/current.staging-<token>/current/`, captures the
plan-stage pin into a sibling `.ai-review/<work_item_id>/.pin.staging-<token>/`,
and writes a staging archive (and, for an open amendment, a staging
`AMENDMENT_DIFF.patch`). It copies each author file byte-for-byte from
`<plan_inputs_dir>` into the staging bundle; a file missing there is seeded
from `current/<file>` if that exists (read-only — the migration path for an
author who has not yet moved), else stubbed empty as before. Every closing
check, including `assert_review_request_states_review_content_id` and
`assert_test_results_consistent_with_plan_review_request`, reads the staging
copy. The copy is byte-exact, so `bundle_id` is computed over exactly the
bytes an in-place generation would have hashed. Only once
`finalize_staged_plan_bundle_generation`'s closing checks (the same checks
`finalize_bundle_generation` runs in place) succeed are the pin, `current/`, the archive
`AMENDMENT_DIFF.patch` and `CHANGES_SINCE_REVIEW.md` renamed into place, and any `REJECTED` marker
cleared. So, in the real command order (refresh the inputs, then
generate), nothing writes `current/` until that final rename.

**Revised `WFR-67` semantics at the plan stage — a deliberate revision**
(`LPR-R3-002`; `WORKFLOW_V2_PLAN.md`'s `D-Plan-Review-Bundle-Binding`).
`WFR-67` requires that a failed closing binding assertion leave no
review-ready artifact. A failed plan-stage generation's artifacts exist
only in the staging directory, the staging pin and the staging archive, and
the failure path removes exactly those (2.11.0: a new or unbound plan revision whose pinned bytes lack
a registry checkpoint's `<!-- CPn -->`/`<!-- /CPn -->` anchor pair fails the
plan-stage generation this way, through `assert_plan_checkpoint_anchors`): it
**does not** call
`withdraw_bundle` on `current/` and **writes no `REJECTED` marker**. The
previous `current/`, its archive and `.pin` are left byte-identical. That
previous bundle is review-ready only for its own content, and only while it
is still the one the work item's `plan_review_binding` record says is
`BOUND` — which every plan-stage reader checks by `review_content_id`
(`assert_plan_review_bundle_bound`) — so a failed generation never makes
the readers refuse a still-bound previous bundle, and never lets them
accept unpublished content. A `REJECTED` marker written by `2.5.1`, or by
an implementation-stage withdrawal, keeps its existing meaning:
`assert_bundle_not_rejected` still refuses while it exists, and the next
successful generation clears it. The pair of final renames is not atomic:
a crash between them leaves a `current/` whose manifest, directory and
archive disagree, which the bind verifier (`verify_plan_review_bundle`)
refuses with `PlanReviewBundleUnverifiedError` — regenerate. Leftover
`current.staging-*`/`.pin.staging-*` directories are read by nothing and
removed by the next generation.

**Stage scope.** The `implementation` and `post-fix` stages are unchanged:
in-place generation, in-place author files under `<bundle_dir>`, and
`withdraw_bundle` (quarantine plus a `REJECTED` marker) on a failed closing
assertion, exactly as before.

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
stage-agnostic and every non-plan stage's bundle is still flat too). That
still holds for legacy items; a scoped item's feedback is scoped by
construction ("Feedback directory" above).

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
`resolve_feedback_dir` is deliberately untouched by this rule (its own
rule is "Feedback directory" above).

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
files a human places there as raw proposal material for the agent to read,
never review-bundle content. `CONTEXT_FILES.txt` must never list a path
under `.ai-review/source/`; `scripts/prepare-ai-review.sh` skips (and
warns on) any such entry as defense in depth.

### Bundle structure

At the `plan` stage this tree is first assembled under
`.ai-review/<work_item_id>/current.staging-<token>/current/` and renamed onto
`current/` only after a successful generation ("Plan-stage staging
generation" above); its author-written files are byte-exact copies of
`<plan_inputs_dir>`'s.

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

**`workflow-2.4.0`, `D-Plan-Amendment-5`**: `AMENDMENT_DIFF.patch`, when
present, is a **sibling** of `<bundle_dir>` itself, at
`.ai-review/<work_item_id>/AMENDMENT_DIFF.patch` — never a member of the
tree above, and never hashed into `bundle_id`/`review_content_id`, both of
which walk only `<bundle_dir>`. `scripts/prepare-ai-review.sh` writes it
during a plan-stage generation for a work item whose `amendment_history`'s
last entry is still open (`resolved_at_plan_revision` still `null`),
reviewer convenience only, and deletes any stale copy once no amendment is
open. The manual-external-review archive (`review-bundle.tar.gz`) bundles
it alongside `current/` when present, so both the local and the
manual-external reviewer see the identical convenience diff.

**`workflow-2.12.0`, `D-Changes`**: `CHANGES_SINCE_REVIEW.md` is a second
such sibling, at `.ai-review/<work_item_id>/CHANGES_SINCE_REVIEW.md`, outside
`bundle_id` and `review_content_id` and bundled into `review-bundle.tar.gz`
when present. `scripts/prepare-ai-review.sh` regenerates it on every ordinary
generation, at the plan stage and at the implementation and post-fix stages,
and deletes it when there is nothing to compare with (a first round, or a
previous round that cannot be identified). It holds a preamble (`work_item_id:`, `stage:`, this
generation's `review_content_id:` and `revision:`) and, from 2.13.0, one
section per review stage (`D-Keyed`), with these exact headings in this order:
`## LOCAL_MODEL_PLAN_REVIEW` then `## MANUAL_EXTERNAL_PLAN_REVIEW` at the plan
stage, `## LOCAL_MODEL_IMPLEMENTATION_REVIEW` then
`## MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` at the implementation stages. A
section with a basis holds `previous_review_content_id:`, `previous_revision:`
(and, at the implementation stage, `previous_reviewed_implementation_head:`),
`### Previous findings` and `### Changes` (a fenced unified diff of the
stage's protected content, or `(no change to the protected content)`). A
section without a basis is the single line `not available (<reason>): review
the whole bundle.`, `<reason>` being `no previous review of this stage is
recorded` or `the previous round cannot be identified`; the reviewer reads the
section named for its role, and a role whose section is not available reviews
the whole bundle. The file is written when at least one section has a basis
and deleted otherwise; each section is built and fails on its own. The
*local* section is the 2.12.0 content: the previous findings are the text of
`REVIEW_FEEDBACK.md` when its header names the previous `review_content_id`,
else "not available". The *manual* section compares against the stage
baseline (`D-Baseline`, below): its basis holds when a valid baseline exists
for the role and its `review_content_id` differs from this generation's, its
findings are the baseline's stored verdict text (never `REVIEW_FEEDBACK.md`),
at the plan stage the diff is the baseline's pin against the staging pin, and
at the implementation stages it is the baseline's reviewed head against the
working tree over the union of the recorded and the current protected
classifications; for a newly protected path that union diff also shows the edits made while
the path was excluded, which is the safe direction. When the baseline's `review_content_id` equals this generation's, the round is
identified and there is no change since it, and the section reads "cannot be
identified". A manual round with no valid baseline (the first one after
updating to 2.13.0, or after `review-baselines/` was deleted) is a full review.
The file stays outside `bundle_id` and `review_content_id` and is a reviewer
aid, never a gate: since every `APPROVE` is a full review, a wrong baseline can
cost review efficiency, never approval. At the plan stage the
previous round is the outgoing `current/` and `.pin`, used only when its
`review_content_id` was reviewed and consumed; a wrapper-only regeneration of
unchanged content carries the live file (both sections) forward when it names
the unchanged `review_content_id`. At the implementation stages it is the previous round's
`reviewed_implementation_head` against the working tree; a same-content
republication leaves it alone. The file is built by
`workflow_fingerprint.write_changes_since_review`, which never fails a
generation: on any error it deletes the file and prints one warning.

**`workflow-2.13.0`, `D-Baseline`**: the manual review stage keeps a *stage
baseline*, a gitignored record of what that stage last reviewed, at
`.ai-review/<work_item_id>/review-baselines/<ROLE>/` (next to `current/`, never
inside it; `<ROLE>` is `MANUAL_EXTERNAL_PLAN_REVIEW` or
`MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`). `workflow_fingerprint.record_stage_baseline`
writes it, called by the one manual ingest (`ingest_manual_review_verdict`, so
by `/record-manual-*-review` and the protocol's `record-external-result` alike)
inside the same state transaction, for a `REVISE` or an `APPROVE` verdict; a
`BLOCK`, a refused verdict and a feedback-only row record nothing. Each such
verdict replaces the role's record wholesale (the old one is removed, a sibling
directory is renamed in, a record with a higher `state_revision` is never
replaced). It holds `BASELINE.json` (`schema_version`, `state_revision`,
`work_item_id`, `role`, `verdict`, `review_content_id`, `bundle_id`, `revision`,
`reviewed_implementation_head`, `classification`, `recorded_at`), `VERDICT.md`
(the verdict exactly as stored in `REVIEW_FEEDBACK.md`) and, at the plan stage,
`pin/` (a copy of the item's `.pin`). `workflow_fingerprint.read_stage_baseline`
returns the record only when it is consistent with itself and the repository:
the key set and types, the item and role, `VERDICT.md`'s header, the content id
recomputed from the copied `pin/` (plan stage) or from the reachable recorded
head (implementation stage) with the record's own revision and classification,
and the id being known to the item's state (at the plan stage `classification.protected_paths`
is a sorted list, the plan-stage metadata carrying no reasons; at the implementation stage it is
an object of path to reason; at the implementation stage a `REVISE` record is known to the
item's state when its revision is at most `implementation_revision`, and an `APPROVE` record
when the manual ledger entry's `bundle_id` equals the record's); anything else is `None`, the
explicit full-review fallback.

The baseline is a reviewer aid, never a gate: it is outside `bundle_id` and
`review_content_id`, and every `APPROVE` is a full review, so a wrong baseline
costs review efficiency and can never let unreviewed content be approved. In
supported operation it is the latest accepted manual verdict of its stage.
Outside that guarantee, stated as limits and not detected: (a) a deliberately
edited or restored baseline file (these local files are under the operator's
control); (b) a downgrade below `2.13.0` followed by a manual review and a
re-upgrade, so delete `.ai-review/<work_item_id>/review-baselines/` when
downgrading. The first manual round after the first update to `2.13.0` has no
baseline and is a full review.

A reviewer who finds the file next to `current/`, with a preamble naming this
bundle's `review_content_id`, may review only the changes and check that each
previous finding is resolved, declaring `Review scope: changes` (valid on a
`REVISE` or a `BLOCK`). Any `APPROVE` is a full review of the whole bundle.

**`workflow-2.6.0`** (`v2.4.0-003`): the patch is anchored at the **working
tree** — `git diff --no-renames <amendment_base_commit> -- <pathspec>` —
because the amended plan stays uncommitted until `/approve-review plan`
commits it; the `<amendment_base_commit>..HEAD` form earlier releases
wrote was therefore always empty. It shows the uncommitted amendment
itself, and new untracked protected files as `new file` (the generator's
own intent-to-add, restored on exit). The pathspec is the sorted union of
the `plan_stage.protected_paths` declared in `<work_item_id>-artifacts.json`
at `amendment_base_commit` (empty when absent there), the ones declared
now, and `<work_item_id>-artifacts.json` itself: a path dropped from the
declaration and removed from the worktree appears as `deleted file`, a
rename as that deletion plus a `new file`, and every declaration change
appears in the `<work_item_id>-artifacts.json` hunk (a dropped path left
unchanged in the worktree has no hunk of its own). A leading `#` comment
block names `work_item_id`, `amendment_id`, `amendment_base_commit`,
`plan_revision` and this bundle's `review_content_id`; `git apply` ignores
it, so the patch applies against `amendment_base_commit`. It is written
after `MANIFEST.md`, so for every currently declared path it describes the
same bytes `review_content_id` hashes. It remains convenience only:
nothing checks it, and it is hashed into neither identity.

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
  **`workflow-2.4.0`, `D-Plan-Amendment-5`**: at the `plan` stage, the
  author appends one fixed line, unconditionally — never gated on whether
  this generation's own work item currently has an open amendment: "
  `AMENDMENT_DIFF.patch` (if present in this archive) is reviewer
  convenience only: it sits outside `bundle_dir`/`bundle_id`/
  `review_content_id` and is not covered by the Reviewed bundle ID above."
  No validator enforces this line's presence — author-written prose like
  every other field on this list.
  **`workflow-2.12.0`, `D-Changes`**: the request is also worded
  conditionally about `CHANGES_SINCE_REVIEW.md`, because it is written before
  the generator decides whether that file exists: "If
  `CHANGES_SINCE_REVIEW.md` is present next to `current/` and its preamble
  names this bundle's `review_content_id`, you may review only the changes
  and check each previous finding is resolved, declaring `Review scope:
  changes` in the section named for your role (a role whose section is not
  available reviews the whole bundle); any `APPROVE` is a full review of the
  whole bundle. The file is reviewer convenience only, outside `bundle_id` and
  `review_content_id`."
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
  `finalize_bundle_generation`) and is never published — at the `plan`
  stage the failed staging generation is discarded and the previous
  `current/` left intact (no withdrawal, no `REJECTED` marker; "Plan-stage
  staging generation" above).
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
`REVIEW_REQUEST.md`'s `review_content_id: <hex>` line (in `<plan_inputs_dir>`
at the `plan` stage, in `<bundle_dir>` otherwise) before
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

**Classification, and its one release-derived fallback.** Both stages
classify every changed or untracked path against the work item's own
declared sets: declared protected first, then declared excluded, and
otherwise `UnclassifiedPathError` — fail closed. Since workflow-2.6.0
(`D-Tooling-Ambient-Classification`) there is exactly one exception to
that last step, `workflow_fingerprint.TOOLING_AMBIENT_EXCLUDED_PATHS`,
which names the single exact path `.workflow-manager/installation.json`.
`classify_path` and `classify_path_implementation_stage` consult it **only
after every declared classification has failed, immediately before the
raise**, and treat that path as excluded. So a declaration that names the
path still decides (protected stays protected); the constant is never
hashed into either stage's projection, which carries only the declared
sets; no digest `2.5.1` could compute changes; and a digest that raised
only because a committed `workflow_manager update` rewrote the
installation record now returns, equal to the recorded approval. It is an
exact path, not a prefix: `.workflow-manager/installation.json.tmp`, every
other `.workflow-manager/*` path and every other undeclared path still
raise. It exists for artifact declarations authored before
`.workflow-manager/` was declared (`v2.4.0-001`); a new declaration should
still classify `.workflow-manager/` itself.

### What the script does NOT do

- It does not judge relevance — `CONTEXT_FILES.txt` is a human/agent
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
- specific areas the reviewer should challenge;
- where the stage's gate is `automatic` and the effective gate policy lists
  `distinct_reviewer_models` in its `require` (workflow-2.8.0; the default
  policy does), a request that the reviewer state `Reviewer model:
  <vendor>/<model>` in the verdict's header block (below). Under a human gate
  the request asks for nothing new.

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

(`.ai-review/<work_item_id>/feedback/` for every work item created under
`2.6.0` or later; a legacy item may still resolve the flat compatibility
`.ai-review/feedback/` — see "Feedback directory" above. Print the exact
path with `python3 scripts/workflow_fingerprint.py --resolve-feedback-path
<work-item-id>`.)

Required structure:

```markdown
# Review Decision

Status: APPROVE | REVISE | BLOCK

Reviewed bundle ID: <the exact bundle_id this feedback reviewed>
Reviewed base commit: <the exact base_commit this feedback reviewed>
Work item: <the exact work_item_id this feedback reviewed>
Reviewed review_content_id: <the exact review_content_id this feedback reviewed>

## Blocking findings

## Important findings

## Optional findings

## Missing tests

## Architecture and maintainability concerns

## Migration and data-integrity concerns

## Usability concerns

## Required acceptance criteria
```

**Severity and verdict shape** (workflow-2.12.0). Blocking: the content is
wrong or unsafe and must change. Important: it must change before approval,
though it is not wrong in itself. Optional: a suggestion. A `REVISE` names at
least one Blocking or Important finding; a round with only Optional findings
is an `APPROVE`, with the findings recorded under `## Optional findings`. An
`APPROVE` lists no Blocking finding, and every `APPROVE` is a full review of
the whole bundle: a header line `Review scope: full|changes` is optional, and
`Review scope: changes` is valid on a `REVISE` or a `BLOCK` only. An
`APPROVE`'s header has exactly one `Review scope: full` line or none; any
other or conflicting value is refused. A finding is a list item
or a `### ` heading in its section; write `None.` in a section with no
finding (leading, trailing and wrapping punctuation or symbols on `None` are
ignored, and an entry made only of punctuation or symbols is also a
placeholder). The shape check
refuses an `APPROVE` that lists a Blocking finding; it does not refuse an
`APPROVE` whose Important section holds an entry. By the severity meaning that
is the reviewer's own contradiction: the reviewer should reissue it as a
`REVISE` (or move the entry to Optional), and an Important entry on an
accepted `APPROVE` stays recorded in the verdict as written. A verdict that breaks these rules is refused as malformed
(`workflow_fingerprint.assert_verdict_well_formed`, `MalformedVerdictError`)
before it becomes state, and is reissued, never converted: the local reviewers
check their own text before writing it, and the manual ingest
(`ingest_manual_review_verdict`, so `record-external-result` too) refuses it
with nothing written. A verdict already recorded is applied as before.

**Optional findings are recorded** (workflow-2.14.0, `D-Parse`, `D-Ledger`).
The writer of every `APPROVE` or `REVISE` of a two-stage item (the local
reviewers and the manual ingest, plan and implementation stages) also records
each finding under `## Optional findings` in the item's `optional_findings`
state, in the verdict's own transaction. An entry is a top-level list item or a
`### ` heading (a nested or sub-bullet line is body; a placeholder is skipped).
Its identity is the whole entry as written, label included, whitespace
collapsed; the key is `F-` plus 12 hex characters of its sha256. Identical
entries merge across verdicts into one entry with several references; two
identical entries inside one verdict are two findings. Declared limits: (a) a
finding under another heading is not an Optional finding; (b) a re-raise
changed by any non-whitespace character is a new key; (c) a verdict that no
writer received records nothing; (d) two findings with the same twelve hex
characters would merge.

The three binding fields (`Reviewed bundle ID:`, `Reviewed base commit:`,
`Work item:`) are required on every ordinary review round, not only WF0's
one-time bootstrap check (`OPUS-R14-002`, generalized here), and they keep
their meaning: the bundle the reviewer reviewed. Feedback missing any of
the three, or whose values disagree with the bundle actually being
approved against, is rejected naming both the feedback's own value and the
current one (`workflow_fingerprint.parse_review_feedback_binding_fields`/
`assert_feedback_matches_bundle`, `WFR-03`) — never applied at face value.

**A two-stage `REVISE` is applied by its `review_content_id`**
(workflow-2.7.0, `D-Apply-Binding`). `/apply-plan-review` and
`/apply-implementation-review` bind the feedback they apply through
`workflow_state.assert_apply_review_feedback_binding`. For a two-stage
`REVISE` that states a `review_content_id` (the plan stage at `"2.1"`/`"2.2"`,
the implementation stage at `"2.2"`), the binding is by content: the
stated `review_content_id` must be the content under application (at the
plan stage the consumed one) and the one the reviewed bundle's own
`MANIFEST.md` records, and that manifest must name this work item, its
`base_commit` and this stage. `Reviewed bundle ID:` and `Reviewed base
commit:` are then advisory — a value that differs, for example after a
wrapper-only regeneration, is reported, never refused — exactly as the
two-stage ingests already treat the bundle id. A stated
`review_content_id` that is not the reviewed content refuses, whatever the
bundle fields say. Every other verdict (`"1"`, `"2.1"` implementation
rounds, a legacy marker, a verdict that states no `review_content_id`) is
bound by the three binding fields, as above. No command and no remedy ever
rewrites a reviewer's binding fields: they attest to what the reviewer
reviewed.

`Reviewer model:` (workflow-2.8.0, optional) declares the model that reviewed,
as `<vendor>/<model>` (for example `anthropic/claude-opus-5-5`), in the header
block. It is written by `/review-plan` and `/review-implementation`, and
requested of a manual reviewer, **only when the stage's gate is `automatic` and
the effective gate policy lists `distinct_reviewer_models`**; otherwise the
file is exactly 2.7.0's. The family is the value up to the first `/`, `:` or
space, lowercased, so `anthropic/opus` and `anthropic/sonnet` are one family; a
bare model id still parses, as its own family. The line is read from the
header block only (`workflow_fingerprint.parse_feedback_reviewer_model`): the
same text quoted in the body declares nothing, and two different values in the
header parse as none. The value is declared and unverified. An `APPROVE`
ingest that states none, or states the family already recorded for the other
stage, is refused while the stage is open (`DistinctReviewerModelsRequiredError`).
A person with a single reviewer subscription records a second declared family,
turns that gate human, or adopts a policy without the requirement before the
stage's bundle is generated; `GATE_POLICY.md` ("Distinct reviewer models")
states the three paths. Under the default policy the stage's gate is
automatic, so an approving verdict is not a person's approval: the Workflow
satisfies the gate from the recorded verdicts (`/satisfy-gate`), which are
trusted from whoever reports them and are bound by bundle and content id
(`GATE_POLICY.md`, "Trust boundary"). Turning human approval on is the stronger
mode.

`Reviewed review_content_id:` is the one pinned label for the reviewed
`review_content_id` (workflow-2.7.0, `v2.6.0-002`). It is **required** on
every two-stage stage verdict — the plan stage at `"2.1"` and `"2.2"`, the
implementation stage at `"2.2"` — and optional for a `"1"` verdict and for
the advisory `/review-implementation` at `"1"`/`"2.1"`. `/review-plan` and
`/review-implementation` write exactly this label. The legacy alias
`Reviewed review content ID:` and the bare `review_content_id:` are still
accepted, case-insensitively; two labels stating different values parse as
no value at all.

The header fields come before the first `## ` section. The
`review_content_id` is read from the **header block** only — every line
before the first `## ` heading that follows a field line (`key: value`) —
so a verdict that opens with `## Review Decision` and states its fields
under it still has them in its header, and a finding that quotes another
ID cannot disturb the parse (`workflow_fingerprint.parse_feedback_review_content_id`).
`Status:`, `Reviewer role:` and the three binding fields keep their
whole-file scan. Compatibility note: a verdict that states its
`review_content_id` only after the first `## ` section was accepted by
2.6.0 and parses as absent from 2.7.0 on; it is the only field read more
strictly than in 2.6.0. `workflow_fingerprint.parse_review_feedback_header`
is the single parser that returns every field.

User functional-testing feedback is placed at:

```text
<feedback_dir>/FUNCTIONAL_REVIEW.md
```

(Free-form findings; no binding-field requirement, since functional review
has no `bundle_id`/`review_content_id` of its own to bind against —
`/apply-functional-review` classifies each finding.)

**The agent must validate feedback, never apply it blindly.** Every blocking and
important finding must end up either resolved, or explicitly rejected in the
plan/summary with repository evidence (file, line, test, or doc reference)
and a clear explanation.

### Finding taxonomy and circuit breaker (`D-Review-Finding-Taxonomy-and-Circuit-Breaker`)

**Advisory tag, never parser-enforced.** Every Blocking/Important finding
may carry a `[substantive]` or `[apparatus]` tag: `[substantive]` names a
defect in the design or implementation actually being reviewed;
`[apparatus]` names a defect confined to bundle metadata, a declarations-
file justification string, a stale table embed, or similar supporting
material that does not itself indicate the reviewed content is wrong. The
tag is recommended prose, not a required field — a missing, malformed, or
ambiguous tag is **never rejected**; `WFR-03`'s binding-field strictness
(`parse_review_feedback_binding_fields`/`assert_feedback_matches_bundle`)
governs only the three binding fields above and is not extended to this
tag, so no live work item's existing feedback shape breaks on update. An
untagged or ambiguously-tagged Blocking/Important finding is instead
treated, conservatively, as `[substantive]` for every purpose below —
silence can never manufacture an apparatus-only streak. Optional findings
are unaffected either way.

**No weakening of resolution requirements.** The rule immediately above —
every Blocking/Important finding ends up resolved or explicitly rejected
with repository evidence — is completely unchanged by this tag, and
unchanged for an untagged finding too. `[apparatus]` never means "may be
ignored"; it means only "does not by itself cast doubt on the substantive
design or implementation."

**Bounded, advisory circuit-breaker signal.** When two consecutive `REVISE`
rounds for the **same local-model review stage** — `LOCAL_MODEL_PLAN_REVIEW`
or `LOCAL_MODEL_IMPLEMENTATION_REVIEW`, distinguished by
`REVIEW_FEEDBACK.md`'s own `Reviewer role:` line, never by
`<feedback_dir>/REVIEW_FEEDBACK.md`'s path (which stays stage-agnostic and
shared by both stage protocols) — have carried **no** `[substantive]`
Blocking/Important finding (every one `[apparatus]`; an untagged finding
counts as `[substantive]`, per above), the reviewer's own next report
states this explicitly (for example, "2 consecutive apparatus-only
rounds") as a visible diminishing-returns signal an operator can act on —
requesting a lighter confirmation pass, or accepting the standing
substantive verdict — rather than treating every apparatus fix as
resetting the convergence clock to zero. The bound is fixed at **2**: the
same reviewing command reads the immediately-prior `REVIEW_FEEDBACK.md`
before overwriting it with its own, which is exactly enough visibility for
a bound of 2 and no more — neither `record_local_plan_review`'s nor its
implementation-stage mirror's `REVISE` branch writes any durable,
finding-classification-bearing ledger entry, so no reviewer can observe
more than one prior round beyond its own.

This signal is deliberately advisory prose in the reviewer's own report,
never a new `docs/ai-workflow/WORKFLOW_STATE.json` field or a
phase-machinery gate: it changes no existing work item's behavior on
update (no governing-version bump), never itself approves anything, and
`/approve-review` and its `EXTERNAL_APPROVE`/local-plus-manual-ledger bases
are completely unaffected.

**Scoped to the local-model loop only — no corresponding claim for
consecutive manual-external rounds.** The signal above is checkable only
because the *same* reviewing command reads the immediately-prior
`REVIEW_FEEDBACK.md` before overwriting it — true for two consecutive
`LOCAL_MODEL_PLAN_REVIEW`/`LOCAL_MODEL_IMPLEMENTATION_REVIEW` rounds, since
`/review-plan`/`/review-implementation` run before the file is replaced. It
does **not** hold across two consecutive manual-external rounds: a manual
`REVISE` is consumed by `/apply-*-review`, and no path re-enters
manual-external review without a fresh local pass first, which overwrites
the same stage-agnostic path with its own `REVIEW_FEEDBACK.md` before the
next manual reviewer ever sees the prior one. Neither
`record_local_plan_review`'s/`record_manual_plan_review`'s (nor their
implementation-stage mirrors') `REVISE` branch writes any durable,
finding-classification-bearing ledger entry, and the review bundle itself
never includes `feedback/`. This document makes **no** corresponding
recoverability claim for two consecutive manual-external rounds: recovering
that signal would need a minimal durable per-round history this addition
deliberately does not add. Manual-external convergence stays operator
judgment, as it already is today.

## Local reviewer commands (operator ergonomics)

Two commands (`workflow-v2-3`) give an operator a repository-local, second
opinion before handing a stage to its real gate: `/review-implementation`
(usable while a `"1"`/`"2.1"` work item sits at
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, or a `"2.2"` item sits at
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW` — see below) and `/review-functional`
(usable while a work item sits at `AWAITING_FUNCTIONAL_REVIEW`). Both
implement a model-independent review role — nothing about either command's
contract, checks, or report format names a specific model.
`/review-functional` never writes `docs/ai-workflow/WORKFLOW_STATE.json` or
`docs/ACTIVE_MILESTONE.md`, approves a stage, applies a finding, or advances
`phase`, for any work item. **`/review-implementation` is dual-mode
(`workflow-2.5.0`, `D-Implementation-Review-Stages`), not uniformly
advisory**: for a `"1"`/`"2.1"` item its existing behavior is byte-for-byte
unchanged — it adds no new lifecycle state or ledger stage, and never writes
`WORKFLOW_STATE.json` or advances `phase`. For a `"2.2"` item, it *is* the
authoritative `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage writer: phase-guarded
to `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, it writes the
`implementation_review_stages` ledger and does advance `phase` on `APPROVE`/
`REVISE` — see `docs/ai-workflow/MILESTONE_WORKFLOW.md`'s
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW` section and
`docs/ai-workflow/IMPLEMENTATION_REVIEW_WORKFLOW.md` for the full "2.2"
mechanism. This does not add a new hard gate either way (the terminal
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` gate is reused, not duplicated —
`docs/ai-workflow/MILESTONE_WORKFLOW.md`'s "Hard gates summary"). Their write
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

### No bundle regeneration between the local and manual-external implementation-review stages

`workflow-2.5.0`, `§2.3` point 1 (`REQ-4`). For a `"2.2"` item, the same
bundle -- the same `bundle_id`, and the same implementation-stage
`review_content_id` -- that `LOCAL_MODEL_IMPLEMENTATION_REVIEW` approved is
exactly what the manual-external reviewer is handed, and exactly what
`/record-manual-implementation-review` ingests. Nothing regenerates the
bundle, and nothing recomputes a fresh `review_content_id` to check the
manual round against, in between `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`'s
own `APPROVE` and `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`'s own
ingestion. This mirrors the plan-review side (`D-Plan-Review-Stages`),
which already has this property structurally: `LOCAL_MODEL_PLAN_REVIEW`'s
`APPROVE` and `MANUAL_EXTERNAL_PLAN_REVIEW`'s ingestion are bound to the
same `review_content_id` the same way, with no bundle-refresh step of its
own between them either.

It is easy to accidentally regress this by adding a bundle-refresh step
where none belongs -- so the property is enforced mechanically, not only
by omission: `record_manual_implementation_review`'s own precondition
(`validate_manual_implementation_review_preconditions`,
`scripts/workflow_state.py`) hard-blocks the moment the manual round's own
`review_content_id` disagrees with the value
`implementation_review_stages["review_content_id"]` already recorded when
`LOCAL_MODEL_IMPLEMENTATION_REVIEW` approved -- with `StaleReviewContentIdError`
when the disagreement is between the reviewer's own feedback-carried
`review_content_id` and the current recomputed value, and with
`MissingLocalApprovalForManualImplementationStageError` when the operator's
recomputed-fresh value agrees with the feedback but neither matches the
ledger's own recorded `review_content_id` (the bundle regenerated after
`LOCAL_MODEL_IMPLEMENTATION_REVIEW`'s own approval, so there is no current
local approval for the content actually being ingested). Either way there
is no fallback path that silently re-approves different content under the
old local-approval ledger entry. The advisory-only `bundle_id` check
(`check_manual_stage_bundle_id_advisory`) is deliberately weaker (a warning,
not a block) since a reviewer-facing wrapper artifact's own `bundle_id` can
legitimately differ in shape from the recomputed one without the
underlying protected content having changed; `review_content_id` is the
one binding identity this rule is stated over. `workflow-2.5.0` CP5's own
disposable-repo fixture
(`scripts/workflow_state_test.py`'s
`TestNoBundleRegenerationBetweenImplementationReviewStages`) demonstrates
both halves of this against a real Git history: the ordinary carry-through
succeeds unchanged, and a simulated regeneration between the two stages is
hard-blocked rather than silently ingested.

### Which review-stage writes are committed (`workflow-2.9.1`)

A review stage persists its verdict in `WORKFLOW_STATE.json`. Whether that
write is committed on its own depends on what reads the history next, so the
rule is written down once, here, and the command texts point at it:

| Write | Stage | Committed on its own? | Why |
| --- | --- | --- | --- |
| `REVISE` (a `"2.2"` item: `/review-implementation` or `/record-manual-implementation-review`) | implementation | **Yes, alone, before any fix commit** | `/apply-implementation-review`'s fix commits and its post-fix generation-record commit would otherwise sweep the write up, and a generation record must always transition `phase` (`OPUS-R101-001`); a swept-up write shows no change and is refused |
| Entry into `APPLYING_REVIEW_FEEDBACK` by `/apply-implementation-review` step 0 (`enter_applying_review_feedback`, from the terminal `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, every governing version) | implementation | **Yes, alone, before any fix commit** (an uncommitted `APPROVE` ledger rides in with it) | the same dead end: the post-fix record would otherwise absorb the move and show a net no-change `phase` (a recovered `"2.2"` HEAD already at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is the one exception -- its generation would move to `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` -- but the entry is committed the same way, keeping the rule uniform) |
| `APPROVE` (`/review-implementation` or `/record-manual-implementation-review`) | implementation | **No** | `/approve-review implementation` or `/satisfy-gate implementation` takes it; a commit of its own puts `HEAD` past the bundle's `generation_head` and both gates refuse with `bundle_generation_mismatch` |
| `BLOCK` | either | nothing to commit | no state is written |
| any write | plan | **No** | there is no generation record at the plan stage; the plan-approval commit takes the whole working-tree state, and a commit of its own raises `bundle_generation_mismatch` at the plan-approval gate |

The committed writes go through one idempotent helper,
`workflow_state.commit_pending_applying_review_feedback_entry(repo_root,
work_item_id, attribution=())`. It stages only the target work item's entry
(foreign work-item residue stays in the working tree), commits it with the
subject `chore(workflow): record entry into APPLYING_REVIEW_FEEDBACK for
<id>` and a `Workflow-Work-Item` trailer as the final paragraph, and returns
the commit SHA, or `None` when nothing is pending (the write is already
committed, or none was made). The review commands call it after a `REVISE`
write; `/apply-implementation-review` step 1 calls it again as a safety net,
so a crash between the write and the commit, or between the staging and the
commit, is repaired and exactly one entry commit ever exists. It refuses
with `ReviewStageWriteNotCommittableError` for an uncommitted `APPROVE` (the
row above says to leave it) and for any field outside
`REVIEW_STAGE_WRITE_COMMIT_FIELDS`, for example `reviewed_implementation_head`
or a persisted but uncommitted `technical_review_block_pins` entry. It
raises `DirtyIndexBeforeStagingError` when another path, or a different
state-file blob, is already staged; unstage that unrelated content, then
re-run. For a leftover pin, `/apply-implementation-review` step 1 stops on the
refusal. The remedy is to stage the state file with
`workflow_state.stage_scoped_state(repo_root, <id>)` (when it returns `False`,
stage exactly `docs/ai-workflow/WORKFLOW_STATE.json`) and commit it with a
`Workflow-Work-Item` trailer as the final paragraph; never stage the whole
file while another work item holds uncommitted state. The helper then returns
`None` and the post-fix generation proceeds. When a pin and another field are
both left, resolve the other field first (it is not a pure review-stage
entry) and commit the pin only afterwards.

The read side is unchanged and strict: `bundle_generation_mismatch` and the
provenance-interval check do not tolerate a state-only commit past the
generation record, and the Orchestration Protocol version is unchanged.

### A generation record is the last commit, and its trailer paragraph (`workflow-2.9.2`)

**Last commit.** A generation record is the last commit of the invocation that
writes it. Every commit a round needs (the fix, the narrative and checklist
update, a stale or review-stage state write) lands before the record. The
read side refuses anything after it with `bundle_generation_mismatch`, because
`HEAD` has moved past the bundle's `generation_head`, and only
`/recover-implementation-provenance` repairs that, at the cost of a new bundle
id and a fresh review round. The bounded branch of `/apply-functional-review`
is the example: it commits the stale `technical_approval` write alone before
the first edit, then each fix, then the "no code change" fixes and the
functional-review checklist in `docs/ACTIVE_MILESTONE.md`, and only then
regenerates the bundle once and commits the record. Nothing is committed after
it. In a mixed round, the new remediation child's entry in
`WORKFLOW_STATE.json` stays uncommitted: the child's own plan-approval commit
(`/approve-review plan <child-id>` or `/satisfy-gate plan <child-id>`) first
commits it, and the scoped staging keeps it out of every commit of the
parent's round, the record included. `/milestone-implement`, `/apply-implementation-review`, `/apply-pr-review`
and `/recover-implementation-provenance` already end on their record.

**Trailer paragraph.** Every command that writes a `Workflow-*` trailer (the
`OPUS-R129-001` rule) puts those trailers in the commit message's final
paragraph, and nothing else is in that paragraph. Git parses only the last
paragraph as trailers. Any `Co-Authored-By:` or `Claude-Session:` lines go in
their own paragraph before it, separated by a blank line. For a generation
record the rule is enforced: the record's trailer set is checked exactly, so a
`Co-Authored-By:` line in the trailer paragraph is refused
(`MalformedBundleGenerationRecordCommitError`). While the refused commit is still
the unpublished `HEAD`, rewrite its message; a later recovery cannot supersede
it.

The read side stays strict, the Orchestration Protocol version is unchanged
(1.2), and no state field or commit shape is added. A project on 2.9.1 keeps
the old order and must follow it by hand: commit the stale `technical_approval`
write alone before the first fix edit, commit the checklist before the record,
and keep `Co-Authored-By:` out of the trailer paragraph. Downgrading to
2.9.1 loses nothing but the corrected command text.

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
  the agent to read, not part of any bundle (`WFR-16`).
