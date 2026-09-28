# Controller / Workflow 2.6 compatibility integration: admit 2.6.0 beside 2.5.1, minimally (Revision 7)

Work item: `workflow-controller-workflow-2-6-integration`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `2296ad63b35521684d6e421cebbb7c1c49b76551` ("Merge pull request #4 from
RodrigoFAbreu/docs/reorganize-documentation"), the tip of `origin/main` when this plan was written,
passed explicitly as the operator directed. The previous milestone was accepted at `17abcad`; the
commits after it are its merge (`8bf2ce5`), the 1.2.1 release (`2282592`, `f474fbe`) and the
documentation reorganisation (`9070527`, `fa28c12`, `2296ad6`). None of them is this milestone's
work.
Lifecycle authority: this repository's installed Workflow **2.5.1** (`.claude/commands/`,
`scripts/workflow_state.py`, `scripts/workflow_fingerprint.py`, `scripts/prepare-ai-review.sh`).
This milestone runs, and is accepted, under 2.5.1 (Decision 2).
Roadmap slot: `docs/ROADMAP.md` section 1.6, step 1 of "At a glance".
Released baseline preserved: `workflow-controller 1.2.1` (`v1.2.1`). This milestone ships as
**1.3.0** (Decision 3).

## Goal

Let the Controller drive a target repository that runs the released **Workflow 2.6.0**, while
every 2.5.1 target keeps exactly today's behaviour. Do it from the released 2.6.0 contract, as
measured below, and keep it minimal:

1. admit 2.6.0 beside 2.5.1, and prove every Workflow-derived inventory and every golden decision
   suite against **each** admitted release;
2. for 2.6+ targets, stop duplicating Workflow's feedback-path resolver: ask Workflow
   (`scripts/workflow_fingerprint.py --resolve-feedback-path <id>`);
3. for 2.6+ targets, stop guessing whether a plan-review bundle is stale: ask Workflow
   (`scripts/workflow_state.py --plan-review-publication-status <id>`);
4. answer the trunk plan's open questions E1-E5 from the released contract, and keep the
   fail-closed `integration_required` behaviour because 2.6.0 provides no base-moving transition;
5. prove a 2.5.1 → 2.6.0 migration, including in-flight bound milestones;
6. settle how this repository's own Workflow installation moves to 2.6.0;
7. release the result as 1.3.0, with release notes.

## Non-goals

- Re-anchoring Workflow-internal expected-outcome and state-writer checks beyond what 2.6.0
  actually breaks. Orchestration Protocol v1 (ROADMAP 1.7) replaces them. Only the rows 2.6.0
  measurably changed (Design D) get a per-release declaration.
- Any protocol, gate-policy, harness, squash-merge/versioning or ROADMAP 1.4 work.
- Wiring `gitrepo.merge_trunk` into any lifecycle path (E2-E4 are negative, Design F).
- Changing Workflow or Workflow Manager. Both are consumed as released.
- Updating this repository's own Workflow installation inside this milestone (Decision 2).
- Changing the release policy (the GitHub release body stays the policy's template, Decision 4).
- A Controller-side refusal of unsafe mid-flight Workflow updates. The guide documents which
  phases are safe; Workflow Manager's own update preflight is ROADMAP work in that repository.

## Investigation: the released 2.6.0 contract

Measured from `workflow-manager` at accepted commit `136c417`
(`distribution/workflow/2.6.0/`; `git diff 136c417 HEAD -- distribution/workflow/2.6.0
src/workflow_manager` is empty). `P5`/`P6` below are the 2.5.1/2.6.0 payloads.

### What is unchanged

- The phase set: the same twenty phases, `MILESTONE_COMPLETE` the only terminal one
  (`P5 workflow_state.py:321-380` equals `P6 :342-401`).
- `WORKFLOW_STATE.json` `schema_version` 1; `default_work_item`'s key set; governing versions
  (`TWO_STAGE_PLAN_REVIEW_VERSIONS = {"2.1","2.2"}`, no new version).
- The seventeen command files (13 changed in content, none added or removed). The user-only set
  (`approve-review`, `accept-milestone`, `recover-implementation-provenance`,
  `request-plan-amendment`; `worker.USER_ONLY_COMMANDS`) is the same.
- The bundle generator's argv (`prepare-ai-review.sh <base-sha> <stage> [work-item-id]`), the
  bundle directory `.ai-review/<id>/current/`, `MANIFEST.md` fields, the REJECTED marker path.
- Implementation-stage provenance: `_classify_generation_record_interval` is byte-identical
  (`P5 :11720-11870` = `P6 :14177-14327`).
- `.github/workflows/workflow-conformance.yml` and the seven conformance suite file names.

### What changed that the Controller consumes

1. **Scoped feedback storage.** A work item created under 2.6.0 is stamped
   `feedback_layout: "scoped"` at creation only (`route_work_item` fresh branch `P6 :10058-10061`,
   remediation child `:12677-12680`; never back-filled). Resolution
   (`workflow_fingerprint.resolve_feedback_layout`, `:2141-2173`) reads the **working-tree**
   state file:
   - `scoped`: stamped; `.ai-review/<id>/feedback`, **whether or not the directory exists**;
   - `legacy-scoped`: no stamp (or no entry, or no state file) and `.ai-review/<id>/feedback/`
     exists; same path;
   - `legacy-flat`: no stamp and that directory does not exist; `.ai-review/feedback`.

   The Controller's copy (`controller/evidence.py:170-194`, "scoped directory if it exists, else
   flat") answers **flat** for a freshly stamped 2.6.0 item whose feedback directory has not been
   created yet, so it is wrong for 2.6.0 by construction, not just duplicated.
2. **The query `--resolve-feedback-path <id>`** (`P6 workflow_fingerprint.py:4417-4438`,
   documented as a Controller contract in `P6 docs/ai-workflow/REVIEW_PROTOCOL.md:68-125`):
   one line of sorted-key JSON, exit 0:
   `{"feedback_dir", "functional_review_path", "layout", "review_feedback_path",
   "work_item_id"}`, repo-relative POSIX paths. Repo root is `git rev-parse --show-toplevel` of
   the current directory. It writes nothing and takes no lock. An unknown id resolves by the legacy
   rule (exit 0). A malformed id, an undecidable state file or an unknown `feedback_layout` value
   (including `null`) exits 1 with a traceback on stderr and nothing on stdout.
3. **Plan-review publication and binding.** For `"2.1"`/`"2.2"` items:
   - `publish_plan_revision` no longer moves the phase; it records a `PUBLISHED`
     `plan_review_binding`;
   - `bind_plan_review_bundle` (`P6 :15314-15397`) is the only writer of
     `AWAITING_LOCAL_PLAN_REVIEW`, from `PLANNING`, `REVISING_PLAN` or `AMENDING_PLAN`, after the
     bundle verifies;
   - `transition_to_awaiting_local_plan_review` is retired (raises `PlanReviewWriterRetiredError`);
   - `/milestone-plan <explicit id>` at a plan-review-ready phase
     (`PLAN_REVIEW_READY_PHASES = {AWAITING_LOCAL_PLAN_REVIEW,
     AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW, AWAITING_PLAN_APPROVAL}`) **withdraws** the item,
     discarding both review stages;
   - a failed generation leaves the item at a non-ready phase with a `PUBLISHED` record, and the
     remedy is re-running the same command with the explicit id;
   - `apply_plan_approval` requires `AWAITING_PLAN_APPROVAL`.

   `"1"`-governed items are unchanged.
4. **The query `--plan-review-publication-status <id>`** (`P6 workflow_state.py:17256-17280`,
   table `plan_review_publication_status` `:15582-15703`; documented in
   `P6 PLAN_REVIEW_WORKFLOW.md:86-130` and `WORKFLOW_V2_1_OPERATOR_REFERENCE.md:128`). It reads
   the working-tree state, the registry, the fresh plan-stage `review_content_id` and the bundle.
   It writes nothing and takes no state lock. Output: `{work_item_id, phase, row, status, remedy}`
   plus per-row `fresh_review_content_id`, `bundle_id`, `bundle_verifies`, `advisory`, `detail`.
   Exit 0 for every status row:

   | row | status | meaning |
   |---|---|---|
   | 1 | `NOT_PLAN_STAGE` | outside the plan stage |
   | 2, 3 | `BOUND` | ready phase, bound content equals fresh content, bundle verifies (3 = a 2.5.1-created item with no record whose bundle verifies) |
   | 4a | `CONTENT_DRIFTED` | ready phase, the worktree no longer matches the bound content |
   | 4b | `BUNDLE_UNVERIFIED` | content unchanged; bundle missing, withdrawn, REJECTED, mixed or stale |
   | 4c | `LEGACY_UNVERIFIED` | 2.5.1-created item, no record, bundle does not verify |
   | 5 | `LEGACY_UNMARKED` | 2.5.1-created item mid-round (`REVISING_PLAN`/`AMENDING_PLAN`); the next command writes the marker |
   | 7, 10 | `NEEDS_EDIT` | normal authoring path |
   | 8 | `NEEDS_REVISION` | mirror behind the registry |
   | 9 | `PUBLISHED_UNBOUND` | publication in progress, recoverable by re-running the command with the explicit id |
   | 11 | `EDIT_IN_PROGRESS` | normal authoring path |

   Rows 4d (ready phase with a non-`BOUND` record) and 6 (non-ready phase with a `BOUND` record)
   exit 1 with `{"error": "PlanReviewBindingInconsistentError", "message": ...}` on stdout.
   Anything else (unknown id, missing state, `"1"`-governed item, malformed record) exits 1 with a
   traceback. The script's only `__main__` is this query, and running it bare exits 2.
5. **Import requirement.** `P6 workflow_state.py:193` imports its sibling `workflow_fingerprint`,
   so it cannot run under `python -P` (the script directory must stay on `sys.path`).
6. **`.workflow-manager/installation.json`** is now classified as tooling-ambient (excluded) at
   both stages when no declaration classifies it (`P6 workflow_fingerprint.py:1440-1457`,
   `:1810-1839`). This repository's declarations already exclude `.workflow-manager/`, so this
   changes nothing here.
7. **Other 2.6.0 changes the Controller does not need to consume**:
   - plan-approval commit closure (the member set is every declared plan-stage protected path);
   - the cross-worktree amendment/lifecycle lock under
     `<git-common-dir>/ai-workflow/checkpoint-claims/` (per work item; distinct from the
     Controller's flock on the git directory, `controller/lock.py:63-83`);
   - the `AMENDMENT_DIFF.patch` working-tree anchor;
   - plan author files moving to `.ai-review/<id>/plan-inputs/`;
   - transient `current.staging-<token>/` directories.

   The first two happen inside user-only or worker-run commands. The Controller decides nothing
   from any of these files. The one place its advice touches them is the plan-stage recovery
   steps (`evidence._plan_bundle_recovery_steps`, which name the author files and the
   `current.rejected-*` quarantine through `evidence._newest_quarantine_dir`), used by the
   REJECTED-marker gate and by the stale-plan-bundle gate. Under the 2.6.0 contract they name the
   author-input directory the 2.6.0 generator actually reads (Design D).

### Open residual v2.6.0-001

`plan_review_binding.consumed` is a single slot, so content that was dual-approved, withdrawn,
then restored after a detour can re-bind and re-enter review
(`workflow-manager docs/defects/v2.6.0-001-*.md`). 2.6.0 closes the dangerous half (approval
requires `AWAITING_PLAN_APPROVAL`, which only a fresh manual `APPROVE` writes). For the Controller
this means one rule, stated as invariant I5: it never infers "never reviewed" or "already
reviewed" from `plan_review_binding`, and reads only the query's `status`. It never dispatches
`/milestone-plan` at a ready phase (I4), so it never starts the withdrawal detour itself.

### How Workflow Manager moves a target to 2.6.0

`workflow_manager update <target>` (optionally `--release-version 2.6.0`;
`src/workflow_manager/install.py:428-519`):
- refuses drifted managed files or path collisions;
- replaces the managed payload (29 files overlay-replaced in 2.6.0) and rewrites
  `.workflow-manager/installation.json` (`workflow_version: "2.6.0"`);
- commits nothing;
- **never reads `WORKFLOW_STATE.json`, never checks for in-flight work items and migrates nothing**
  (no `feedback_layout` back-fill, no feedback move).

2.6.0's scripts handle 2.5.1-created items through their legacy branches:
- feedback: legacy layouts;
- ready phases: rows 3/4c;
- mid-round: row 5's marker.

Two things are unsupported:
- a plan-approval journal mid-flight ("finish or abandon under 2.5.1 before updating");
- re-planning at `IMPLEMENTING`, which 2.5.1 allowed silently and 2.6.0 refuses in favour of
  `/request-plan-amendment`.

The 2.6.0 lifecycle lock's lag probe reads the `installation.json` at HEAD of **every registered
worktree**, so a linked worktree whose branch predates the update counts as lagging. The
Controller itself creates no linked worktrees (`gitrepo.py:326` only lists them).

### What 2.6.0 breaks in the Controller as it stands (measured)

Running the Controller's own checkers against the 2.6.0 payload in memory:

- **Property 5** (`job.property_declaration_against_artifact_violations`) returns `[]` on 2.5.1
  and **14 violations** on 2.6.0:
  - the four `/milestone-plan` rows (PLANNING `"1"`/`"2.1"`/`"2.2"` and `NO_PHASE`) declare
    `publish_plan_revision`, but step 6 now makes three further durable-write calls after it
    (`verify_plan_review_bundle`, `state_transaction`, `bind_plan_review_bundle`,
    `P6 milestone-plan.md:443-445`), which is 12 violations;
  - both `REVISING_PLAN` `/apply-plan-review` rows declare the retired
    `transition_to_awaiting_local_plan_review`.

  Every other row passes unchanged.
- **Feedback resolution** is wrong for stamped items (above).
- **`fixtures.build_workflow_line_fixture`** copies only `scripts/workflow_state.py`, which under
  2.6.0 cannot import without `workflow_fingerprint.py`.
- **`tests/test_managed_repo.py:56-63`** asserts that this repository runs 2.5.1, and several
  modules read this repository's own `.claude/commands/` and `scripts/` as "the" Workflow
  (`test_decision`, `test_evidence`, `test_job_validation`, `test_resume`, `test_target_state`,
  `test_packaged_runtime`, `test_trunk_orchestration_e2e`). Updating this repository's
  installation would silently move all of them to 2.6.0 at once. CI has neither
  `workflow-manager` nor the Manager's `distribution/` tree.
- **The expected-outcome end states do not change:** every 2.x plan-stage command still ends at
  `AWAITING_LOCAL_PLAN_REVIEW`, and implementation rows 12-18 pass unchanged.

## The answers to E1-E5 (trunk plan, `CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md:228-242`)

| | Question | Answer from released 2.6.0 | Evidence |
|---|---|---|---|
| E1 | Does Workflow record a work item's branch or integration base? | **No.** Only `base_commit` and (after an amendment request) `amendment_base_commit`. Branch identity exists only outside the state file, in the amendment witness (`requester_branch`, ...) and claim records (worktree paths, no branch). | `P6 ws:9956`, `:13571`, `:6684-6690`, `:5473-5488` |
| E2 | Is there a legal transition that moves a work item's base after an integration merge? | **No.** `base_commit` is an immutable declaration fact (`WorkItemDeclarationFactConflictError`); its only writers are creation, the remediation child and legacy import. It is hashed into both stages' `review_content_id`. | `P6 ws:10066-10078`, `:12676`, `:16135`; `wf:1607`, `:1951` |
| E3 | Do provenance intervals accept a history-preserving integration merge? | **No.** Unchanged from 2.5.1: any commit with more than one parent raises `NonFirstParentProvenanceIntervalError`, and no trailer or record admits one. | `P6 ws:14177-14327`, `:14253-14258` |
| E4 | In which phases is integration legal? | **None.** 2.6.0's only "merge" wording is operator remedies for the amendment lock ("merge the resolved amendment first"). No phase or transition governs integration. | `P6 ws:7643`, `:7847`, `:7856`; `milestone-implement.md:197-208` |
| E5 | How do `WORKFLOW_STATE.json` and the narrative documents merge when two milestone branches land? | **Not addressed.** Still one state file with one `active_work_item_id` and one `docs/ACTIVE_MILESTONE.md`; no merge driver. The new lock and witness coordinate one item across worktrees, not two items' branches. | `P6 ws:210`, `:271`, `:10085-10090` |

**Consequence (item 4).** `gitrepo.merge_trunk` stays unwired. The `integration_required`
readiness gate and the documented manual merge ("Create a merge commit") remain the Controller's
contract for 2.5.1 **and** 2.6.0 targets. The gate text, the guides and ADR 0003 now name both
releases. The Controller does not grow a competing base or review model (unchanged invariant).

**The narrow Workflow follow-up this names**, for Workflow Manager to plan (it belongs with the
Workflow 2.7 protocol release, ROADMAP 1.7, beside `reconcile`, and is not part of this
milestone). It has five parts:
1. a durable per-work-item integration record (branch and integration base), answering E1;
2. one sanctioned transition that, given a history-preserving merge of the trunk into the
   milestone branch, moves the item's base to the merge's trunk parent, stales exactly the
   approvals whose `review_content_id` moved (`technical_approval` always, since the base is
   hashed; `plan_approval` only if a plan-stage protected path changed), and re-enters the
   implementation-review stage (E2);
3. provenance-interval acceptance of exactly that merge commit, identified by a trailer the
   transition writes, for example `Workflow-Trunk-Integration: <work-item-id>` (E3);
4. a stated set of legal phases, at least `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` and the
   post-technical-approval phases through `AWAITING_FUNCTIONAL_REVIEW`, each of which re-enters
   review (E4);
5. a state model in which two items' branches merge without conflict: per-item state files, or
   a merge rule for `work_items` and the narrative document (E5; overlaps ROADMAP 6.1/6.2).

When such a release exists, a later Controller milestone binds `merge_trunk` to it. This plan
records the follow-up here, in ADR 0006, and in ROADMAP 1.6's completion note (Open question 1).

## Invariants

- **I1. 2.5.1 behaviour is byte-for-byte unchanged.** For a target whose installed release is
  2.5.1, every decision, gate, job record, worker argv, CLI output and exit code is identical to
  1.2.1's. The 2.5.1 contract never runs a Workflow script. The only new work on a 2.5.1 target
  is a plain read of `.workflow-manager/installation.json` before each decision and before each
  verification (I3). It changes nothing unless the installed release changed after admission,
  and then the target is no longer a 2.5.1 target. Proof:
  - the existing plan-stage and external-implementation-review goldens stay byte-identical (their
    generators run with `--release 2.5.1 --check`);
  - the no-policy lifecycle golden stays byte-identical;
  - every existing test passes unchanged except the ones this plan names as changed.
- **I2. One authority per fact.** For a 2.6+ target the feedback path and the plan-review
  publication status come from Workflow's queries only. The Controller keeps no second rule for
  them and never repairs or second-guesses an answer. A query failure is never a fallback to the
  2.5.1 rule. It fails closed (Design C, "Query failures"):
  - at decision time it is a refusal (exit 20), with no worker launched;
  - whenever job verification runs, at launch or at `resume`, it records a `FAILED` job carrying
    the query evidence, through one new catch point that both paths share, and leaves no pending
    job. So does an installed release that verification cannot establish (I3). A CLI `resume`
    refuses at `inspect` before any verification while the installation manifest is unreadable,
    and the record stays as it was until the manifest is restored (Design C).
- **I3. The contract is chosen by the installed release, re-checked before every decision, and
  pinned per job.** The contract is selected from `ManagedRepository.workflow_version`, the
  release `managed_repo.inspect` admitted. `run` inspects once and reuses that
  `ManagedRepository` for every step (`cli.py:1009`), so admission alone does not cover later
  steps. Therefore:
  - **before every decision** (every step of a `run`, and `step`), immediately after the
    repository preflight returns (it can switch to trunk at close-out), the installed release is
    re-read and must equal the admitted one. This is before a preflight gate is recorded and
    before the step's pre-state capture or `decide`, all of which can resolve the feedback path.
    A mismatch, or a manifest that cannot be read, refuses before any decision or launch
    (`WorkflowReleaseChangedError`, code `WORKFLOW_RELEASE_CHANGED`, exit 20). The refusal names
    what the preflight itself did (a completed action, a returned gate that was not recorded, and
    any close-out it completed), so a completed close-out is not mistaken for a failed one;
  - a job verifies, and `resume` reconciles, under the release recorded in its job record at
    launch (`target_workflow_version`, already written, `job.py:3731`). Whenever verification
    runs and the installed release changed during the job, or cannot be established,
    verification fails closed (reason `workflow_release_changed`) and records a terminal
    `FAILED` job.
- **I4. No automatic `/milestone-plan` at a plan-review-ready phase.** It would withdraw the
  item under 2.6.0. Today's table already never does it. The property becomes a pinned test.
- **I5. The Controller never interprets `plan_review_binding`**, including `consumed` (v2.6.0-001).
  It reads the publication-status query's `status`, `row`, `remedy`, `detail` and `advisory` only.
- **I6. A query executes only the admitted release's bytes, and no other module from the
  target.** A query runs only after `managed_repo.inspect` has admitted the target (Workflow
  Manager `verify` and `status` exit 0). Admission alone does not cover the bytes that run later:
  a worker may have run since admission, and job verification deliberately does not re-inspect
  (`job.py:55-70`). So, **for every query subprocess**, the Controller:
  1. reads `scripts/workflow_state.py` and its only sibling import
     `scripts/workflow_fingerprint.py` from the target, once each;
  2. checks that the sha256 of exactly those bytes equals the digests the contract carries for
     that release (Design B). On a mismatch, or a missing or unreadable file, it refuses with
     `WorkflowQueryError` (`reason: "query_script_modified"`) and executes nothing;
  3. writes exactly those bytes into a fresh private temporary directory it owns, and runs the
     query from there.

  The target's `scripts/` directory is therefore never on the query's `sys.path`. Running
  `scripts/<script>.py` in place would put it first (`sys.path[0]`), ahead of the standard
  library, so a planted `scripts/uuid.py` or `scripts/secrets.py` (imported by
  `workflow_fingerprint.py:216` and `workflow_state.py:183`) would run with both digests
  matching; the local review reproduced this, and so did this revision. Hashing the bytes that
  are copied also removes any window between the check and the execution. The release half is
  I3. The query runs:
  - with `cwd` = the target root. Both scripts find the repository through `git rev-parse
    --show-toplevel` of the current directory, and neither locates resources through
    `__file__` (checked in both 2.6.0 scripts);
  - as `sys.executable -B -E -s <private-dir>/<script>.py ...`. There is no `-P`, because the
    sibling import needs the script's own directory, which is now the private one and holds only
    the two verified files. `-E` ignores any `PYTHONPATH`, and `-s` the user site directory. `-B`
    writes no bytecode, not even into the private directory;
  - with stdin closed and a timeout.

  The query writes nothing to the target. The Controller's only write is the private copy, made
  through the containment-checked `runtime.write_bytes` inside a `tempfile.TemporaryDirectory`
  (the pattern `release_txn.py:293` and `worker.py:812` already use), and removed when the query
  ends, whatever its outcome.
- **I7. Tests never depend on this repository's installed Workflow release.** Workflow-derived
  checks read vendored per-release trees (Design A). This repository's installed tree is checked
  only for equality with the vendored tree of the release it declares.
- **Unchanged:** I2/I3 of the trunk plan (never rewrite, never merge to trunk). `merge_trunk`
  stays unwired; `target_state` stays read-only; mutating git subcommands stay in `gitrepo.py`;
  the write-containment rule holds (queries are subprocess reads).

## Design

### A. Vendored release trees and per-release inventories (CP1)

**Trees.** `tests/workflow_releases/<release>/` for `2.5.1` and `2.6.0`, each holding exactly the
subset the Controller's checks and disposable-repository tests need, at their target-relative
paths:
- `.claude/commands/*.md` (the seventeen command files);
- `scripts/workflow_state.py`, `scripts/workflow_fingerprint.py`;
- `scripts/prepare-ai-review.sh` (executable bit kept).

A `RELEASE.json` beside them records:
- `workflow_version`;
- the Manager source (`distribution/workflow/<release>/payload`) and the Manager commit it was
  taken from (`136c417` for 2.6.0; the 2.5.1 payload is unchanged at the same commit);
- per-file `sha256`, equal to the Manager manifest's `sha256` for that `target_path`;
- the executable flag.

About 2.6 MB in total. The directories have no `__init__.py`, and no vendored file matches
`test_*.py`, so neither `unittest discover` nor `tools/test_shards.py` picks them up. CP1 confirms
that no static scan in `tests/` walks into them (write containment, no-rewrite and package-structure
scans read `controller/` only; confirmed by running the full suite).

**Tool.** `tools/workflow_releases.py`:
- `sync <release> --from <manager-distribution-dir>` copies the subset and writes `RELEASE.json`
  from that release's `manifest.json`;
- `check` verifies every vendored file against `RELEASE.json`, and that every admitted release
  (`managed_repo.VALIDATED_WORKFLOW_RELEASES`) has a tree;
- stdlib only, and it writes only under `tests/workflow_releases/`.

**Tests** (`tests/test_workflow_releases.py`):
- `check` passes;
- **this repository's installed release is admitted, and its installed command files and three
  scripts are byte-identical to the vendored tree of that release.** Today that is 2.5.1. After
  the post-release update (Decision 2) the same test checks 2.6.0 with no code change;
- **the real-Manager admission test of this repository is made release-agnostic.**
  `tests/test_managed_repo.py:56-63` (`test_real_workflow_manager_admits_this_repository`)
  asserts `workflow_version == "2.5.1"` today. It runs whenever `workflow-manager` is on `PATH`
  (`REAL_WORKFLOW_MANAGER`, `:39`), which it is on the development machine, so CP6's self-update
  dry run would fail it. It instead asserts that the inspected release is a member of
  `VALIDATED_WORKFLOW_RELEASES` and equals the release the equality test above checks, read from
  this repository's `installation.json`, never a literal. No other test asserts this
  repository's own release: the CI-workflow test compares the managed conformance workflow with
  the manifest's own hash, the conformance-family tests read the managed workflow file, and the
  CLI `inspect` tests use the stub-Manager fixture;
- when a Workflow Manager distribution is available locally (`WORKFLOW_MANAGER_DISTRIBUTION`, or
  the sibling checkout), the recorded hashes equal the Manager manifest's. Skipped in CI, like the
  existing real-Manager tests.

**Fixtures.** `tests/fixtures.py` gains
`install_workflow_release(root, release, *, profile="full")`: copies the vendored tree into a
target and writes its `installation.json` through the existing `write_installation_manifest`.
`copy_real_commands_dir`, `build_workflow_line_fixture` (now copying both scripts) and
`write_stub_workflow_manager` take a `release` argument, defaulting to
`managed_repo.REFERENCE_WORKFLOW_RELEASE` (2.5.1), so every existing call site behaves as before.
The tests that read this repository's own `.claude/commands/` or `scripts/` as "the" Workflow
(`test_decision`, `test_evidence`, `test_job_validation`, `test_resume`, `test_target_state`,
`test_packaged_runtime`, `test_trunk_orchestration_e2e`, and the fallback copy in
`test_integration_disposable_repo`) read the vendored tree instead. `build_target_managed_repository`'s
placeholder `workflow_version="2.3.1"` becomes the reference release: the contract lookup
(Design B) is strict, and no golden contains `2.3.1` (checked).

**One phase list.** `target_state.KNOWN_PHASES`/`TERMINAL_PHASES` become re-exports of
`decision`'s (`target_state` already imports `decision`, so `DEPENDENCY_ORDER` is unaffected).
The comments stay on the single definition. `KnownPhaseSetEqualityTest` compares that set with
each admitted release's `workflow_state.KNOWN_PHASES`, read in a **subprocess per release**
(`sys.executable -B -E -s -c ...` with the vendored `scripts/` directory as `cwd`), because both
releases share module names and an in-process import would collide in `sys.modules`. The
hand-copied set in `test_decision.py` stays as the independent third witness.

**Per-release inventories.** Each inventory test iterates `sorted(VALIDATED_WORKFLOW_RELEASES)`
with `subTest(release=...)`, against that release's vendored tree:
- phase set;
- command-file partition (`decision.classify_command_files`);
- user-only derivation;
- property 5 (`job.property_declaration_against_artifact_violations(tree, expected_outcomes_for(release))`,
  Design D).

In CP1 the set is still `{"2.5.1"}`, and CP5 extends it. CP2-CP4 exercise 2.6.0 explicitly in
their own tests before admission.

### B. The Workflow contract module (CP2)

A new module `controller/workflow_contract.py`, placed in `DEPENDENCY_ORDER` directly after
`identity` and before `decision`. It imports `errors` only and is added to `controller/__init__.py`
in the same position.

```python
@dataclasses.dataclass(frozen=True)
class WorkflowContract:
    release: str
    feedback_path_source: str          # "controller_rule" | "workflow_query"
    plan_review_publication_source: str  # "revision_coherence" | "workflow_query"
    query_script_sha256: Mapping[str, str] | None  # target path -> sha256; None when no query runs

RELEASE_CONTRACTS: Mapping[str, WorkflowContract]  # exactly "2.5.1" and "2.6.0"
def contract_for(release: str) -> WorkflowContract  # KeyError-free: unknown -> UnsupportedWorkflowVersionError
```

**Script digests (I6).** A contract that runs queries carries the sha256 of
`scripts/workflow_state.py` and `scripts/workflow_fingerprint.py` for its release: the Workflow
Manager manifest's digests, which the vendored `RELEASE.json` records (Design A). The 2.5.1
contract carries `None`, since it runs no query. So admission by exact release also means
execution of exactly the admitted bytes, and admitting a future release adds its digests in the
same reviewed change.

**Admission stays in `managed_repo`.** Its `VALIDATED_WORKFLOW_RELEASES` stays an explicit
literal (a deliberate act per release, as its docstring requires). A test asserts
`VALIDATED_WORKFLOW_RELEASES <= set(RELEASE_CONTRACTS)`, so an admitted release always has a
contract. 2.6.0 has a contract from CP2 but is admitted only in CP5.

**Runner.** `_run_query(root, contract, script, args, *, timeout=120)`:
1. **Read and digest check.** It reads the target's `scripts/workflow_state.py` and
   `scripts/workflow_fingerprint.py` once each and compares the sha256 of those bytes with
   `contract.query_script_sha256`. A mismatch, or a missing or unreadable file, raises
   `WorkflowQueryError` with `reason: "query_script_modified"`, naming the path and both
   digests, and nothing is executed.
2. **Private copy.** Inside `tempfile.TemporaryDirectory(prefix="workflow-controller-query-")`,
   it writes exactly the bytes it hashed, under their own names, through
   `runtime.write_bytes(<private-dir>, name, data)`. Nothing else is in the directory, and it is
   removed when the query ends, on every path (success, error and timeout). A failure to create
   the directory, to write either file (`runtime.write_bytes` raises `OSError` or its own
   `RuntimeContainmentError`, `runtime.py:131-176`) or to remove the directory raises
   `WorkflowQueryError` with `reason: "query_private_copy_failed"`. Nothing is executed after a
   creation or write failure, and a removal failure discards the answer.
3. **Execution** (the private runner hook below replaces only this step):
   - argv `[sys.executable, "-B", "-E", "-s", f"{private_dir}/{script}", *args]`;
   - `cwd=root`, `stdin=DEVNULL`, captured text output, `check=False`.

Revision 2's `-X pycache_prefix=<os.devnull>` is dropped. It existed only so that a planted
`scripts/__pycache__/*.pyc` in the target could not run instead of the verified source, and the
target's `scripts/` directory is no longer on `sys.path` at all. Dropping it also lets the
interpreter use the standard library's own bytecode cache instead of recompiling every imported
standard-library module on each query (measured below).

`OSError`, timeout, an unexpected exit code or unparseable stdout raise a new
`WorkflowQueryError` (a `ControllerError`, code `WORKFLOW_QUERY_FAILED`, exit 20). Its evidence
carries a `reason`, the argv, cwd, return code and the stdout/stderr tails. The error is added to
`errors.py`, and `test_plan_document_consistency`'s ADR 0001 exit-code check is kept green (exit
20 is already the fail-closed code).

**Every failure in steps 1-3 is mapped, so nothing but a `WorkflowQueryError` leaves the
runner:**
- step 1's missing, unreadable or modified script (`query_script_modified`);
- step 2's directory creation, private write or removal (`query_private_copy_failed`);
- step 3's `OSError`, timeout, exit code or output.

This matters most during verification. There the call runs after the `COMPLETED` flush, and
`_row_clauses_failure` catches only `WorkflowQueryError` around the clauses (Design C). Any other
exception from the runner would escape it and leave the job pending, which is the trap R15 closes.
A full or unwritable temporary directory is one example.

**`resolve_feedback_path(root, work_item_id) -> FeedbackPath`** (`layout`, `feedback_dir`,
`review_feedback_path`, `functional_review_path`, all repo-relative). The output is validated
strictly; a violation is a `WorkflowQueryError` (contract drift is detected, never absorbed):
- the key set is exactly the documented five;
- `work_item_id` echoes the argument;
- `layout` is one of the three;
- `feedback_dir` is `.ai-review/<id>/feedback` for `scoped`/`legacy-scoped` and
  `.ai-review/feedback` for `legacy-flat`;
- the two file paths are `feedback_dir` plus `REVIEW_FEEDBACK.md`/`FUNCTIONAL_REVIEW.md`;
- the paths are relative POSIX paths with no `..`.

**`plan_review_publication_status(root, work_item_id) -> PublicationStatus | PublicationRefusal`**:
- exit 0: the output is validated and returned as a `PublicationStatus`. The checks are:
  - the five always-present keys are there. The per-row keys are optional:
    `fresh_review_content_id` is absent from row 4c and `null` in row 4a when the fresh id is
    unreadable (`P6 ws:15616-15621`, `:15646-15647`), and neither is a violation;
  - `status` is one of the ten documented values;
  - `row` is a **string** from the documented set (`"1"`, `"2"`, `"3"`, `"4a"`-`"4c"`, `"5"`,
    `"7"`-`"11"`), as written by `_row` (`P6 ws:15603-15605`);
  - the row-to-status pairing matches the table above;
  - `work_item_id` is echoed;
- exit 1 with a parseable `{"error": "PlanReviewBindingInconsistentError", "message"}` on stdout
  (rows 4d/6) is returned as a `PublicationRefusal`, not raised, so a decision can gate with
  Workflow's own message;
- every other outcome raises `WorkflowQueryError`.

**Cost.** One query reads, hashes and copies about 1.15 MB of script source (the 0.92 MB
`workflow_state.py` and the 0.23 MB `workflow_fingerprint.py`), and compiles the two scripts;
standard-library modules load from their own bytecode cache. Measured once each on a copy of
this repository with the 2.6.0 scripts run from a private copy, execution only:
`--resolve-feedback-path` 0.04 s and `--plan-review-publication-status` 0.09 s, against 0.22 s
for the latter with revision 2's `pycache_prefix`. CP2 measures the wall time of each query,
read, digest check and copy included, on a disposable repository (recorded in the checkpoint
commit). A decision or a job verification runs **at most one** feedback query and one status
query per work item (a per-call memo; nothing cached across calls).

**The answers seam.** Evidence and job code never call the runners directly. They call a
`WorkflowAnswers` provider with two methods, `feedback_path(root, id)` and
`publication_status(root, id)`, and only `workflow_query` contracts ever call it.
- **Production** always uses `QueryAnswers`, the real runners with the per-call memo.
  `workflow_contract.bind(contract)` returns a frozen `BoundContract(contract, answers)`, where
  `answers` is a fresh `QueryAnswers` for a `workflow_query` contract and `None` for
  `controller_rule`. It is bound only inside `evidence.decide`, the step's pre-state capture and
  job verification (Design C, "How the contract reaches the clauses"), and no production caller
  can pass another provider. This is pinned by a test that `decide`'s and `execute_step`'s public
  signatures take no provider.
- **Test injection** goes only through a private module-level hook, which `bind` consults, used
  solely by the golden generators (Design C).

**Tests** (`tests/test_workflow_contract.py`). These are disposable git repositories with the
vendored 2.6.0 tree installed and real scripts run, with no fakes of Workflow's output, except
one test that injects malformed outputs through the private runner hook to cover each
validation clause. The hook replaces only the execution step, so the read, the digest check and
the private copy still run first. They cover:
- the contract's script digests equal the vendored `RELEASE.json` digests for both scripts, for
  every contract that runs queries, and are `None` for the 2.5.1 contract;
- a managed script modified after admission (one byte appended to either script): the query is
  refused with `reason: "query_script_modified"`, and the script never runs (the modification
  would write a sentinel file if executed, and none appears);
- **a planted standard-library shadow is not executed by either query.** A
  `scripts/uuid.py` (imported by `workflow_fingerprint.py`) and a `scripts/secrets.py` (imported
  by `workflow_state.py`), each of which would write a sentinel file if imported, are planted in
  the target with both scripts unmodified. `--resolve-feedback-path` and
  `--plan-review-publication-status` each succeed with the correct answer, and no sentinel
  appears;
- a planted `scripts/__pycache__/workflow_fingerprint.*.pyc` whose recorded mtime and size match
  the source is not executed, and no `__pycache__` is written into the target;
- the private directory is gone after a successful query, a failing query and a timed-out one;
- a failed private copy: creating the `TemporaryDirectory`, `runtime.write_bytes` (once with
  `OSError(ENOSPC)`, once with `RuntimeContainmentError`) and removing the directory are each
  made to fail. Each case raises `WorkflowQueryError` with `reason: "query_private_copy_failed"`,
  no other exception type leaves the runner, and nothing is executed after a creation or write
  failure. The failures are injected by patching. An unwritable `TMPDIR` alone does not trigger
  one: `tempfile` silently falls back to the next candidate directory, `/tmp` (measured);
- all three feedback layouts, including the stamped item with no directory yet;
- an unknown id;
- an unknown `feedback_layout` and an undecidable state file (exit 1 → `WorkflowQueryError`);
- the status rows the Controller acts on (2/3 `BOUND`, 4a, 4b, 4c, 5, 9) and one refusal row (4d),
  produced by driving the real 2.5.1/2.6.0 scripts and the real generator as
  `test_integration_disposable_repo.py:1225-1260` already does for 2.5.1;
- a `"1"`-governed item (exit 1 traceback → `WorkflowQueryError`);
- a `PYTHONPATH` in the environment that would shadow the scripts (ignored because of `-E`);
- the timeout path, via an injected runner.

### C. Release-aware feedback resolution (CP3)

**Resolver.** `evidence.resolve_feedback_dir(root, work_item_id, bound)`, where `bound` is a
`BoundContract` (Design B):
- `controller_rule` (2.5.1): today's function body, byte-for-byte;
- `workflow_query` (2.6.0): `bound.answers.feedback_path(root, work_item_id).feedback_dir`.

The derived helpers (`functional_review_findings_path`, `functional_review_consumed_marker_path`,
`functional_review_findings_consumed`) take the `BoundContract` too. The consumed marker keeps its
Workflow-defined name next to `FUNCTIONAL_REVIEW.md`, since 2.6.0 does not return it
(`P6 wf:2224-2238`, name unchanged).

**Threading.** The contract always comes from the admitted `ManagedRepository`. The only later
reads of the manifest are the two release re-checks below, which compare and never select:
- **before every decision**, `job._execute_step_locked` re-reads the installed release
  immediately after `milestone_branch.repository_preflight` returns, whether it returned a
  `Gate` or a `Proceed`. That is before `_branch_gate_record`, before the state re-read and before
  `_capture_pre_state` and `decide`: the pre-state capture resolves the feedback path
  (`_functional_review_consumed_blob`, `job.py:347`), both for a preflight gate's record
  (`:4143`) and for the step (`:4323`). This is a new
  `managed_repo.installed_workflow_version(root)`: a plain file read through the existing
  `_read_manifest` parser, not a Manager call. A value other than `managed_repo.workflow_version`
  raises `WorkflowReleaseChangedError` (new in `errors.py`: a `ControllerError`, code
  `WORKFLOW_RELEASE_CHANGED`, exit 20). So does a read that cannot establish the installed
  release, meaning either of the parser's own errors: `UnmanagedRepositoryError` for a missing
  manifest, or `MalformedInstallationManifestError` for one that is unreadable, not JSON, not an
  object or schema-invalid (`_read_manifest` already maps `OSError` and decode errors to it,
  `managed_repo.py:145-187`). Then `installed` is `null` and the parser error's `code` and
  `message` are under `manifest_error`. The refusal is the same whatever the cause, so the
  preflight's own record is never lost behind a bare manifest error. The evidence is
  `{"admitted", "installed", "preflight_action", "preflight_gate", "preflight_events"}`, plus
  `manifest_error` when `installed` is `null`:
  - `preflight_action` is the `Proceed.action` the preflight took (`none`, `bound`, ...,
    `closed_out`; `milestone_branch.py:193-203`), or `gate` for a gate;
  - `preflight_gate` is the gate's `code` for `gate`, else `null`;
  - `preflight_events` lists, in order, the binding events the preflight wrote in this step
    (`milestone_branch._event`, the one event writer, which `_write` calls, `:349-359`). The step
    passes a fresh list through a new `milestone_branch.Context.events` field (default `None`,
    so no other caller changes, and a plain list append, not a file write). This is what keeps a
    close-out on record when the preflight then returns another outcome. `_after_close`
    (`:968-975`) returns `_on_trunk`'s gate or action unchanged unless that is `none` or
    `trunk_start`, and a trunk-side close-out (`_close_trunk_step3`) returns nothing of its own.

  The message states what happened, and in every case that only the decision was refused:
  - for `gate`: the preflight returned that gate and it was deliberately **not** recorded, since
    the check runs before `_branch_gate_record`; the next invocation rediscovers it;
  - for any other action than `none`: that action completed and was recorded;
  - when `preflight_events` contains `closed`: a milestone close-out completed and was recorded,
    whatever the preflight returned after it.

  Nothing is decided or launched, and no job record is written, as for every decision-time
  `ControllerError` today. The check sits after the preflight because close-out switches to trunk
  inside the step (`milestone_branch.py:960`), and trunk may carry a different release (M3);
- `evidence.decide(managed_repo, ...)` binds the contract once, as
  `bind(contract_for(managed_repo.workflow_version))`, and passes the `BoundContract` to every
  handler (the nine call sites the survey lists, `evidence.py:1290-2795`);
- `job.execute_step` takes the contract at launch from the same `ManagedRepository`, and the job
  record already stores `target_workflow_version`. The step's pre-state capture
  (`_capture_pre_state`, including `_functional_review_consumed_blob`) runs before `decide` under
  that contract, so a query failure there is a decision-time failure (below);
- verification, the postconditions and the predicates that read feedback
  (`_block_feedback_bound_to_pre_state_bundle`, `_feedback_verdict_failure`,
  `_postcondition_manual_implementation_*`) and, from CP4, the plan-stage postcondition use the
  **recorded** release's contract;
- before any verification clause runs, the job re-reads the installed release through the same
  `installed_workflow_version`. A mismatch with the recorded release, or a read that cannot
  establish the installed release, fails verification closed with reason
  `workflow_release_changed` (I3). This check sits in the same shared helper as the query catch
  below, so launch and `resume` apply it identically;
- `resume` reconciles under the job record's release. A pre-1.3.0 job record always carries
  `target_workflow_version: "2.5.1"`, because no other release was ever admitted. A missing or
  unknown value fails closed.

**How the contract reaches the clauses: explicitly, never bound into the table.**
- `_row_clauses_failure` binds the recorded release's contract once per verification and passes
  that `BoundContract` to every predicate, `predicate_detail` and postcondition as one added
  positional argument. `PredicateFn`, `PredicateDetailFn` and `PostconditionFn`
  (`job.py:446-460`) gain the parameter. Every clause function takes it, and the ones that read
  no Workflow answer ignore it.
- `expected_outcomes_for(release)` substitutes writer declarations only (Design D) and binds
  nothing. The table's clauses therefore stay release-independent, and `AUTOMATIC_TRIPLES` and
  the property checks are unaffected.
- The existing tests that call a clause or `_row_clauses_failure` directly pass the 2.5.1
  binding. That is about forty call sites in `test_job_validation`, `test_job`, `test_resume` and
  `test_lifecycle_orchestration`, a mechanical change with no assertion changed (I1's named
  exception).

**Query failures (I2).** At decision time the existing path suffices. Verification needs one new
catch point, because today's catches do not cover the query calls.

- **At decision time** (`_capture_pre_state`, `evidence.decide`, `explain`), a
  `WorkflowQueryError` propagates. The CLI's `ControllerError` mapping makes it exit 20, before
  any worker is launched and with no job record written.
- **During verification the existing catches do not apply.** `job.py:629-632`, `:652` and `:683`
  wrap only `target_state.read`. The feedback reads (`evidence.resolve_feedback_dir` at
  `job.py:556`, `:892`, `:1011`, `:1033`) are outside any `try`. So is
  `_postcondition_plan_bundle_coherent`'s `plan_bundle_coherence` call (`:662`), where CP4's
  status query goes. `_row_clauses_failure` (`:1738-1785`) calls every predicate and
  postcondition unguarded, and so do its callers. Left as it is, a query failure would propagate
  out of `_launch_job` after the `COMPLETED` flush. `NON_TERMINAL_STATUSES` counts `COMPLETED`,
  so `pending_reconciliation_jobs` (`:2862`) would report the job and
  `_refuse_pending_reconciliation` (`:2925`) would refuse every later `step`. `resume` would
  re-run the same clauses and raise again for as long as the failure lasts, which is permanent
  for `query_script_modified`.
- **The new catch point is `_row_clauses_failure`**, the helper both paths already share
  (`_verify_transition` at launch; `_row2_verified` at `resume`, `:2249`, for both
  `_reconcile_launched` and `_reconcile_completed`):
  - it takes the job's recorded release, and first re-reads the installed release. A mismatch
    returns reason `workflow_release_changed`. So does a read that cannot establish the installed
    release: the read is inside the helper's own guard, and the parser's two errors
    (`UnmanagedRepositoryError`, `MalformedInstallationManifestError`, as at decision time) are
    caught there. Neither is a `WorkflowQueryError`, so without this a missing or damaged
    manifest, for example one observed mid-replacement during a Workflow Manager update, would
    escape after the `COMPLETED` flush and leave the job pending, the very trap this catch point
    removes. The invariant is that whenever verification runs and cannot establish the installed
    release, it records a terminal `FAILED` job and leaves no pending job. At launch that covers a
    manifest the worker removed or damaged. At `resume`, verification runs only after the CLI's
    `inspect` (`cli.py:1079`, `_inspect_target` at `:569-574`), which parses the same manifest
    through `_read_manifest` and refuses first while it is still missing or damaged (exit 20,
    `UNMANAGED_REPOSITORY` or `MALFORMED_INSTALLATION_MANIFEST`). The job then correctly stays
    pending, since `step` refuses at `inspect` for the same reason, and it reconciles normally once
    the manifest is restored. So the `resume`-path branch of this catch is reached only when the
    manifest becomes unreadable after `inspect`;
  - it evaluates the predicate and postcondition clauses inside one
    `try: ... except WorkflowQueryError`. A caught error returns reason
    `workflow_query_failed`;
  - it gains a fourth return element, `workflow_error`, set only for these two reasons, holding
    the error's `code` (`WORKFLOW_RELEASE_CHANGED` or `WORKFLOW_QUERY_FAILED`), `message` and
    full `evidence`. For a query that is the query's `reason`, argv, cwd, return code and output
    tails, or the digest mismatch. For a release it is `{"recorded", "installed"}`, where an
    unreadable manifest gives `installed: null` and adds `manifest_error` (the parser error's
    `code` and `message`). Its other returns are unchanged, so a 2.5.1 job, which never queries,
    verifies exactly as today (I1);
  - `_transition_not_observed_evidence` gains an optional `workflow_error` key, present only on
    these two reasons.

  The bool-returning predicates keep their signatures. The exception carries the evidence to the
  one place that records it, so no predicate needs a second channel.
- **Each caller records `FAILED` for both new reasons:**
  - at launch, `_verify_transition`'s unverified result already records `FAILED`;
  - at `resume`, `_reconcile_completed`'s unverified branch already records `FAILED`;
  - `_reconcile_launched` checks the two new reasons first, and records `FAILED` with that
    evidence ahead of its `INTERRUPTED` and `UnreconcilableJobError` branches, because an
    unanswerable query means the transition cannot be judged either way.

  A `FAILED` record is terminal, so no pending job is left. The next `step` is not refused with
  `PendingJobReconciliationError`. If the failure persists, that step refuses on its own with exit
  20: through its decision-time query, its release re-check, or, for a manifest that is still
  missing or damaged, `managed_repo.inspect` itself. The failed job stops the `run` loop, as every failed job does today, and
  `resume` never relaunches.

**Goldens.** `tests/golden/generate_plan_stage_decisions.py` and
`generate_external_implementation_review_decisions.py` gain `--release`, defaulting to `2.5.1`:
- `--release 2.5.1` writes and checks the **existing files, byte-unchanged** (I1);
- `--release 2.6.0` writes `plan_stage_decisions.2.6.0.json` and
  `external_implementation_review_decisions.2.6.0.json`. The goldens pin the **Controller's
  decision logic under the 2.6.0 contract**, so Workflow's two answers are inputs, injected through
  the answers seam (Design B) as recorded values:
  - each existing scenario runs once per feedback layout (`scoped`, `legacy-scoped`,
    `legacy-flat`), with its feedback files written at that layout's path;
  - at the plan-review-ready phases, each scenario also runs once per publication-status class the
    Controller distinguishes (`BOUND` with and without `advisory`, `CONTENT_DRIFTED`,
    `BUNDLE_UNVERIFIED`, `LEGACY_UNVERIFIED`, a refusal, and one unexpected status);
  - at the non-ready phases, it runs once for an ordinary status, once for a refusal and once
    for an unexpected status (N4).

  The real queries are not bypassed in coverage. CP2 runs them against the real 2.6.0 scripts,
  CP4 runs every status and gate row end to end against real 2.6.0 bundles, and M1-M5 run the
  whole path (Decision 11).

The golden tests iterate the admitted releases, so the 2.6.0 files become active at CP5. CP3 adds
a direct test that checks the 2.6.0 files with `--check` before admission. CP4 regenerates the
2.6.0 plan-stage golden once, deliberately, and its commit states why. The no-policy lifecycle
golden (`no_policy_lifecycle.json`) is a trunk-policy invariance golden, not a decision golden,
and it embeds the manifest version. It stays 2.5.1-anchored and byte-unchanged (Decision 6).

### D. Plan-review publication status (CP4)

This applies only when the contract's `plan_review_publication_source` is `workflow_query` and
the item is `"2.1"`/`"2.2"`-governed. `"1"`-governed items, and every 2.5.1 target, keep
`plan_bundle_coherence` exactly as today.

**Ready phases, before any dispatch.** The phases are `AWAITING_LOCAL_PLAN_REVIEW`,
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` (today's `PLAN_BUNDLE_CONSUMING_PHASES`) and
`AWAITING_PLAN_APPROVAL`. At the first two, the status query replaces `plan_bundle_coherence` as
the check. `_plan_bundle_recovery_steps` stays, for the rows whose remedy regenerates the bundle
(S2 below). At `AWAITING_PLAN_APPROVAL` the check is new, because 2.6.0's
approval requires bound content; 2.5.1 targets still have no bundle check there. `HumanGate`
has no id field, so the gates below are named by their builders in `evidence`, and the goldens
pin every gate field.

There is exactly one outcome per (ready phase × status class):

| status class | `AWAITING_LOCAL_PLAN_REVIEW` | `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `AWAITING_PLAN_APPROVAL` |
|---|---|---|---|
| **S1** `BOUND` (rows 2, 3) | the existing phase handler, unchanged | the existing phase handler, unchanged | the existing `/approve-review plan <id>` gate, unchanged |
| **S2** `CONTENT_DRIFTED`, `BUNDLE_UNVERIFIED`, `LEGACY_UNVERIFIED` (rows 4a-4c) | stale-plan-bundle gate | stale-plan-bundle gate | stale-plan-bundle gate |
| **S3** a `PublicationRefusal` (row 4d) | `plan_review_binding_inconsistent` gate | `plan_review_binding_inconsistent` gate | `plan_review_binding_inconsistent` gate |
| **S4** any other documented status (rows 1, 5, 7-11), or an echoed `phase` other than the one the Controller read | `unexpected_plan_review_status` gate | `unexpected_plan_review_status` gate | `unexpected_plan_review_status` gate |
| **S5** a `WorkflowQueryError` | refusal, exit 20 (Design C) | refusal, exit 20 | refusal, exit 20 |

- **S1:** `row`, and `advisory` if present, are appended to the decision evidence.
- **S2:** this is the existing stale-plan-bundle gate (`evidence._stale_plan_bundle_gate`), so the
  guides that describe it stay valid. Under the query contract:
  - the evidence adds `row` and `status`, and `fresh_review_content_id` only when the answer
    carries a non-null value: always for row 4b, for row 4a only when the fresh id is readable,
    and never for row 4c, whose answer has no such key (Design B). `what_is_required` quotes
    Workflow's `remedy` and `detail` verbatim, for every S2 row;
  - **rows 4b and 4c, whose remedy regenerates the bundle**
    (`./scripts/prepare-ai-review.sh <base> plan <id>`, `P6 ws:15617-15618`, `:15652-15653`):
    `safe_resume_command` is today's `_plan_bundle_recovery_steps`, ending in that same generator
    command. That is: restore `CONTEXT_FILES.txt` from the newest `current.rejected-*`
    quarantine when the author files are absent; write or refresh `REVIEW_REQUEST.md` with the
    fresh `review_content_id`; write or refresh `TEST_RESULTS.md` with the published revision
    and the current `head`. Workflow's bare remedy cannot succeed when the author files are
    absent, which is the ordinary residue of a completed 2.5.1 withdrawal at a ready phase.
    2.5.1's `publish_plan_revision` moves the item to `AWAITING_LOCAL_PLAN_REVIEW` before
    generation, and a completed `withdraw_bundle` removes its own REJECTED marker
    (`scripts/workflow_fingerprint.py:2388-2450`), so the status is 4c and the REJECTED gate does
    not fire. `seed_plan_review_inputs` then stubs every author file empty (`P6 wf:2984-3009`),
    and the generator refuses with `MissingReviewContentIdStatementError` (`P6 wf:3877`;
    reproduced by the local review). Even with the author files present, the generator refuses a
    `TEST_RESULTS.md`
    whose `head:` is not this generation's HEAD (`P6 wf:3281-3311`), so the refresh steps stay
    too. The rule is keyed on the row, never on parsing the remedy text;
  - **the steps name the directory the 2.6.0 generator reads.** The generator takes each author
    file from `.ai-review/<id>/plan-inputs/<file>` when that file exists, else from
    `.ai-review/<id>/current/<file>` (`P6 wf:2921`, `:2984-3009`). So under the query contract
    the author-input directory is `plan-inputs/` when that directory exists, else `current/`.
    The "absent" test and every step's path use it. Under the 2.5.1 contract the directory stays
    `current/`, byte-identical (I1);
  - **row 4a, whose remedy restores the bound bytes** (no generator runs): the gate is a
    **no-restore diagnostic gate**, keyed on the row. Its `safe_resume_command` is
    `workflow-controller --work-item <id> explain <repository>` (Decision 12). The Controller
    authors no restore step. It never uses Workflow's remedy text as the command either: that text
    is `"<restore>; or <withdraw> and take the normal path"` (`P6 ws:15632-15647`), so it carries
    the withdrawal too.
    - **What reaches row 4a.** For each protected path Workflow hashes existence, Git mode and
      blob (`_snapshot_worktree`, `P6 wf:1498-1516`):
      - a symbolic link is hashed as mode `120000` and its target string, so replacing a file with
        a link reaches row 4a even when the link's target holds the bound bytes;
      - the executable bit counts only when `core.fileMode` is true, so a mode-only change
        reaches row 4a in a repository where it is (Git's default);
      - an absent path makes the fresh id unreadable (`AbsentProtectedPathError`).

      The item's own artifacts declarations file, `docs/ai-workflow/registry/<id>-artifacts.json`,
      is not a protected path, but its three plan-stage classification sets are hashed into the
      plan-stage `review_content_id` (`P6 wf:1567-1614`). An edit to them after binding also
      reaches row 4a, and Workflow's remedy does not mention it, so restoring the protected paths
      alone would not clear the row.

      The plan document, registry and mapping are checked earlier, while the item's metadata is
      resolved. Each must be a tracked regular file with no symbolic link at any path component
      (`_validate_plan_stage_metadata_path`, `P6 wf:872-897`, called at `:996-999`). The
      declarations file must be a regular file that is not a link (`_path_exists_at_source`,
      `:739-747`, checked at `:1001-1004`). So deleting any of those four, or replacing one with
      a link, makes the query itself fail (exit 1). That is S5's refusal, not row 4a. Only a
      further declared protected path reaches row 4a by being deleted or replaced with a link.
    - **Why the Controller offers no restore** (revision 7; revisions 4-6 offered one). A restore
      is safe only if its source holds the bound state and every write lands on the path that was
      checked. The Controller can establish neither:
      - *The source.* Workflow selects row 4a as soon as the fresh id differs from the bound one.
        It then runs its bundle verifier, which compares the manifest's, `current/`'s and the
        archive's `bundle_id` (`P6 ws:15535-15548`), but it keeps the result only under the
        private key `_error` (`:15630-15647`), and the CLI drops every private key
        (`:17270-17276`). So the answer never says whether `.ai-review/<id>/current/files/` still
        holds the bound state. The generator writes those copies (`P6 prepare-ai-review.sh:480-518`,
        then `refresh_files_copy_from_pin`, `P6 wf:2535-2547`), and nothing guards them
        afterwards. A copy deleted after binding looks like a path that was unchanged from
        `base_commit`, and a copy whose bytes or mode were changed looks like the bound state.
        Telling them apart would mean re-implementing Workflow's bundle verification in the
        Controller.
      - *The path.* A step that the gate prints runs later, outside the Controller, and nothing
        holds a path's shape across that gap (`explain` is read-only, `cli.py:697-702`). If a
        parent directory is replaced by a link in between, `cp`, `rm` and the write all follow
        it, possibly out of the repository, whatever was checked while the gate was built.
        Checking each component at execution time needs a Controller command that writes to the
        target's working tree, and this plan adds none ("the Controller writes nothing new to
        targets", Migration / data-integrity notes).
    - **`what_is_required`** is built from the answer and the item id alone. The gate reads no
      file of the target to build it. It:
      - quotes Workflow's `remedy` and `detail` verbatim;
      - states that the Controller offers no restore, because it cannot establish that
        `.ai-review/<id>/current/files/` still holds the bound state, and cannot keep a path's
        shape from changing between a printed step and its execution;
      - names what the bound state covers: the existence, mode and bytes of each path listed
        under `## Protected paths` in `.ai-review/<id>/current/MANIFEST.md`, and the plan-stage
        classification sets of `docs/ai-workflow/registry/<id>-artifacts.json`;
      - states that a restore overwrites the working tree's post-binding edits, which may exist
        nowhere else, so they should be kept first and re-applied at the next editing phase;
      - states that the withdrawal alternative discards both recorded review stages;
      - says that a human decides, and that the next Controller step reads the status again,
        which is row 2 (`BOUND`) once the working tree matches the bound state.
    - **Why the gate warns about post-binding edits.** Plan-stage protected paths stay
      uncommitted until plan approval (this item's own three are index-only `A` entries), so
      their post-binding edits exist only in the working tree, and a restore overwrites them. The
      operator cannot skip the restore either. 2.6.0's `/review-plan`,
      `/record-manual-plan-review` and `/approve-review plan` all refuse row 4a
      (`assert_plan_review_bundle_bound`, `P6 ws:15715-15745`). An operator who starts editing the
      plan at `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, before recording the verdict, must restore
      first and re-apply the edits at the next editing phase (`REVISING_PLAN` after a `REVISE`).
    - Following the gate changes nothing in the working tree, in `.ai-review/` or behind any
      link;
  - Workflow's alternative in each remedy, "withdraw with `/milestone-plan <id>`", discards both
    review stages (`P6 ws:15076-15077`). It appears only inside the quoted remedy in
    `what_is_required`, which adds that consequence. It is never part of any
    `safe_resume_command`, and the Controller never dispatches it (I4).

  At `AWAITING_PLAN_APPROVAL` this gate *replaces* the approval gate; the approval gate does not
  carry the remedy instead. 2.6.0's `/approve-review plan` refuses rows 4a-4d
  (`assert_plan_review_bundle_bound`, `P6 approve-review.md:259-274`), so offering it would name
  a command that cannot succeed. One gate per status class also keeps the 2.6.0 golden,
  `automation.md` and `troubleshooting.md` to a single rule.
- **S3:** a human gate quoting Workflow's message; fail closed.
- **S4:** a human gate naming the phase, row and status as a Workflow contract change; fail
  closed. 2.6.0 never returns these rows at a ready phase.
- The S3 and S4 gates, like N2 and N4 below, have no automatic continuation, so their
  `safe_resume_command` is the same `explain` command as row 4a's no-restore gate (Decision 12).

**The REJECTED-marker gate keeps running first, for both releases.** It reads Workflow's own
marker and guesses nothing. For a 2.6.0 target, its plan-stage branch (`evidence.py:2548-2555`,
`_plan_bundle_recovery_steps`) is reachable only through a marker that 2.5.1 left behind:
- 2.6.0's plan-stage generation assembles into staging and writes no REJECTED marker
  (`P6 milestone-plan.md` step 6);
- a successful 2.6.0 generation clears any leftover marker (`P6 wf:3081`, in
  `finalize_staged_plan_bundle_generation`).

Under the query contract its steps use the same author-input directory rule as S2: `plan-inputs/`
when it exists, else `current/`. After a 2.5.1 withdrawal `plan-inputs/` does not exist, so the
steps name `current/`, exactly as today, and the generator reads the files written there
(`prepare-ai-review.sh:407-423`). The steps are kept rather than replaced by the status query's
remedy, which does not say which author files to write. A leftover marker needs a 2.5.1
withdrawal that did not complete: a completed one removes its own marker. M1's variant M1b covers
this case, and M1c covers the completed withdrawal that reaches S2 instead (Design F).

**Under the query contract, for a `"2.1"`/`"2.2"` item, that plan-stage branch covers all three
ready phases.** Today it covers only `PLAN_BUNDLE_CONSUMING_PHASES` (`evidence.py:2548`), so a
marker at `AWAITING_PLAN_APPROVAL` falls through to the last branch, whose `safe_resume_command`
is the bare generator (`:2572-2578`, `_regeneration_command` at `:1963-1966`). That is exactly
the recovery S2 established can fail when the author files are absent. Without the fix the same
residue would get two recoveries: with no marker, S2's author-file steps; with a marker, the bare
generator. So, under the query contract, `_rejected_marker_gate` takes its plan-stage branch
(the clause, then `_plan_bundle_recovery_steps` with the author-input directory rule above) at
`AWAITING_PLAN_APPROVAL` too.
- A 2.5.1 target and a `"1"`-governed item keep the last branch at that phase, byte-identical
  (I1, and Design D's scope).
- The state is rare but reachable. 2.5.1's generator has no phase gate, and a 2.5.1 generation
  run at `AWAITING_PLAN_APPROVAL` whose closing checks fail withdraws the bundle there. Only a
  crash between the quarantine rename and the marker removal leaves the marker, as for M1b.
- M1's variant M1d covers it (Design F), and proves that the offered continuation succeeds.

**Non-ready plan phases** (`PLANNING`, `REVISING_PLAN`, `AMENDING_PLAN`). The Controller's
automatic command there already passes the explicit id (`decision.py:664`, `:678`, `:830`), which
is exactly 2.6.0's remedy for `PUBLISHED_UNBOUND` (row 9). So, at each of the three phases:
- **N1** a `PublicationStatus` with a non-ready row (5, 7-11) whose echoed `phase` is the one the
  Controller read → dispatch as today, with `row` and `status` in the evidence;
- **N2** a `PublicationRefusal` (row 6) → the `plan_review_binding_inconsistent` gate, fail
  closed;
- **N3** a `WorkflowQueryError` → refusal, exit 20;
- **N4** any other documented status (rows 1, 2, 3, 4a-4c), or an echoed `phase` other than the
  one the Controller read → the `unexpected_plan_review_status` gate, fail closed. This mirrors
  S4, so neither side dispatches on an answer that belongs to the other phase class. 2.6.0 never
  produces these answers at a non-ready phase; this is contract-drift detection only.

A brand-new `PLANNING` item whose registry is not on disk yet reports `NEEDS_EDIT` (row 7), which
is the normal path.

**Postconditions.** For the five 2.x plan-stage rows (PLANNING `"2.1"`/`"2.2"`, `NO_PHASE`,
REVISING_PLAN `"2.1"`/`"2.2"`), `_postcondition_plan_bundle_coherent` becomes, under the query
contract: phase `AWAITING_LOCAL_PLAN_REVIEW` **and** status `BOUND`. `to_any_of` is unchanged. A
query failure while checking it is caught by `_row_clauses_failure` and records a `FAILED` job
with reason `workflow_query_failed` (Design C, "Query failures").

A 2.6.0 worker whose generation failed stops at `PLANNING`/`REVISING_PLAN`. That fails
verification as `phase_not_in_to_any_of`, exactly as a 2.5.1 worker that never reached the gate
does today, and the failed job is reported and stops the `run` loop exactly as today. The next
Controller step the operator starts then sees row 9 and dispatches the same explicit-id command,
which is Workflow's documented recovery. This adds no new automatic retry, and CP4 tests that a
failed plan-stage job is not relaunched within the same `run`.

**Per-release writer declarations.** `job.expected_outcomes_for(release)` returns
`EXPECTED_OUTCOMES` (the 2.5.1 table, unchanged) with the 2.6.0 writer declarations substituted
for exactly the rows property 5 flags:
- the PLANNING `"2.1"`/`"2.2"` rows and `NO_PHASE`: `bind_plan_review_bundle` in
  `milestone-plan.md` step 6's `[2.1]` bind bullet (`P6 milestone-plan.md:440-445`), the sole
  writer of `AWAITING_LOCAL_PLAN_REVIEW`;
- the PLANNING `"1"` row: it keeps `publish_plan_revision`, which still writes
  `AWAITING_EXTERNAL_PLAN_REVIEW` for a `"1"` item. Its `trailing_calls` allowlist names the
  step-6 `[2.1]`-only calls it now precedes (`verify_plan_review_bundle`, `state_transaction`,
  `bind_plan_review_bundle`), each with the reason "a `[2.1]` bullet a `"1"` item never reaches".
  This is the same mechanism rows 12/13 already use (`_STEP_1F_TRAILING_CALLS`);
- both REVISING_PLAN rows: `bind_plan_review_bundle` in `apply-plan-review.md` step 7′.

The phases, predicates and triples are unchanged, so `decision.AUTOMATIC_TRIPLES` still equals the
table's keys. Property 5 runs per release against that release's vendored commands (Design A),
and returns `[]` for both.

**Other changes.**
- The false comment at `job.py:579-581` ("no Workflow writer ever sets `current_bundle_id`") is
  corrected: 2.6.0's bind writes it, and the Controller still does not read it.
- **I4 test:** no row of `AUTOMATIC_TRIPLES` and no decision path dispatches `/milestone-plan`
  from a phase in 2.6.0's `PLAN_REVIEW_READY_PHASES`. The check asserts both the table and a
  `decide` sweep over every ready phase.

### E. Admission (CP5)

- `SUPPORTED_WORKFLOW_LINE = "2.5"` becomes `SUPPORTED_WORKFLOW_LINES = frozenset({"2.5", "2.6"})`
  (the pre-filter), and `VALIDATED_WORKFLOW_RELEASES = frozenset({"2.5.1", "2.6.0"})`, stated in
  the docstring with what was measured (this plan's Investigation, and CP1-CP4's per-release
  suites).
- `REFERENCE_WORKFLOW_RELEASE` stays `"2.5.1"`: it is the fixture default and the release of the
  unchanged baseline goldens.
- The refusal evidence key `supported_workflow_line` (a string) becomes `supported_workflow_lines`
  (a sorted list). This is a visible change to the JSON of an `UNSUPPORTED_WORKFLOW_VERSION`
  refusal, and the release notes say so (Decision 5).
- The Controller-owned `integration_required` gate text (`milestone_branch.py:1150`) says
  "Workflow 2.5.1 and 2.6.0 have no transition that moves a work item's base". The abandon text
  (`:1693`) and comments are updated the same way.
- **Tests:**
  - `test_2_6_0_refuses_outside_supported_line` is inverted to 2.6.0 admitted;
  - 2.6.1 and 2.5.0 are refused as `unvalidated_release`, and 2.7.0 as `outside_supported_line`;
  - the `inspect` output tests in `test_cli.py` gain a 2.6.0 case;
  - the stub Manager prints the requested release.
- From CP5 on, every per-release inventory (Design A) and golden (Design C) runs against both
  releases.

### F. Migration 2.5.1 → 2.6.0 (CP5)

**Module.** `tests/test_workflow_release_migration.py`, runnable in CI. It uses disposable
repositories built from the vendored trees, the real 2.5.1 then 2.6.0 scripts and generator,
`fake_gh` for the forge, and the fake worker where a dispatch runs.

**The update step.** It reproduces the Controller-relevant subset of Workflow Manager's write
set. The Manager overlay-replaces 29 managed files, including documentation and conformance
tests; the simulation replaces only the vendored ones:
- replace every vendored managed file (the command files and the three scripts) with the 2.6.0
  bytes;
- rewrite `installation.json` to 2.6.0;
- commit, as the operator would, since the Manager commits nothing.

The local-only variant below uses the real Manager and carries the claim that the full write
set behaves the same.

| | Scenario | Asserted |
|---|---|---|
| M1 | Policy active; milestone bound with branch and Draft PR; item at `AWAITING_LOCAL_PLAN_REVIEW` with a real 2.5.1-generated plan bundle. The milestone branch is updated to 2.6.0 in place. | `inspect` admits 2.6.0. The feedback query answers the same directory as the 2.5.1 rule did before the update (`legacy-flat`, and `legacy-scoped` in a variant with the scoped directory present). Status is `BOUND` row 3. The decision (action or gate) equals the pre-update decision. The binding record is byte-unchanged and branch sync and PR verification proceed. A local `REVISE`, recorded by the real 2.6.0 `record_local_plan_review` (what the dispatched `/review-plan <id>` worker writes), then moves the item to `REVISING_PLAN` with a non-legacy `CONSUMED` record: status `NEEDS_EDIT` (row 10), and the Controller dispatches `/apply-plan-review <id>`. |
| M1a | As M1, but a local `REVISE` recorded under 2.5.1 moves the item to `REVISING_PLAN` **before** the update. | After the update: status `LEGACY_UNMARKED` (row 5), and the Controller dispatches `/apply-plan-review <id>`. The fake worker writes the state that the real 2.6.0 entry `state_transaction` produces (`ensure_plan_review_binding_marker`, computed by the test with the real script), leaving the phase at `REVISING_PLAN`. The status is then `EDIT_IN_PROGRESS` (row 11). The job fails verification as `phase_not_in_to_any_of` and stops the `run`, and the next invocation dispatches `/apply-plan-review <id>` again. |
| M1b | As M1, but a 2.5.1 withdrawal left a REJECTED marker and a `current.rejected-*` quarantine at `AWAITING_LOCAL_PLAN_REVIEW` before the update. This state arises only from a crash between the quarantine rename and the marker removal, because a completed 2.5.1 `withdraw_bundle` removes its own marker (`scripts/workflow_fingerprint.py:2388-2450`). The fixture therefore runs the real 2.5.1 `withdraw_bundle` and then plants the marker directly, with the content it holds at that step. It is never built from a completed withdrawal alone. | After the update the REJECTED-marker gate fires ahead of the status query, with today's recovery steps naming `current/` (no `plan-inputs/` exists). Once those steps are carried out with the real 2.6.0 generator, the marker is gone and the status is `BOUND` (row 3). |
| M1c | As M1, but a **completed** 2.5.1 withdrawal (the real `withdraw_bundle`: quarantine present, no marker, no `current/`) at `AWAITING_LOCAL_PLAN_REVIEW` before the update. | After the update the status is `LEGACY_UNVERIFIED` (row 4c), and the Controller stops at the stale-plan-bundle gate (S2). Its `safe_resume_command` is the author-file steps, step 0 restoring from the quarantine, then the generator command. On a separate copy of the same state, running Workflow's bare remedy alone fails (the real 2.6.0 generator exits non-zero with `MissingReviewContentIdStatementError`), which pins why the steps are kept. Carrying out the gate's steps exactly, with the real 2.6.0 generator, gives exit 0 and status `BOUND` (row 3). |
| M1d | As M1b, but at `AWAITING_PLAN_APPROVAL`. Under 2.5.1, the real `record_local_plan_review` and `record_manual_plan_review` each record an `APPROVE`, then the fixture runs the real 2.5.1 `withdraw_bundle` and plants the marker, as M1b does. | After the update the REJECTED-marker gate fires ahead of the status query with the plan-stage branch: the marker-clearing clause, then the author-file steps naming `current/` and the generator command. Its `safe_resume_command` is not the bare generator. On a separate copy of the same state, the bare generator alone fails (the real 2.6.0 generator exits non-zero), which pins why the branch is extended. Carrying out the gate's steps exactly, with the real 2.6.0 generator, gives exit 0, and then: the marker is gone; the status is `BOUND` (row 3); the real 2.6.0 `assert_plan_review_bundle_bound`, `/approve-review plan` step 2's own bundle check (`P6 ws:15715-15745`), accepts the item; and the Controller's decision is the unchanged `/approve-review plan <id>` gate. |
| M2 | As M1, but at `IMPLEMENTING` after plan approval and one completed checkpoint under 2.5.1. | 2.6.0 admitted. The Controller decides `/milestone-implement <id>`. The real 2.6.0 `claim_checkpoint` succeeds in the disposable repository (the new lifecycle lock under the git common dir). Implementation-stage feedback resolves by the legacy rule. |
| M3 | Trunk is updated to 2.6.0 while a bound milestone's branch stays on 2.5.1, and the milestone is later accepted. | On the branch the Controller uses the 2.5.1 contract (the branch's manifest). At readiness the gate is `integration_required`, unchanged. After the documented manual merge (a merge commit on trunk, through `fake_gh`), close-out converges. The close-out switches to the 2.6.0 trunk inside the step, so the same step then refuses with `WORKFLOW_RELEASE_CHANGED` (I3) after the close-out is recorded, and decides nothing under the branch's 2.5.1 contract. The refusal's evidence carries `preflight_action: "closed_out"` and a `preflight_events` list containing `closed`, and its message states that the close-out completed and was recorded. The next invocation's fresh `inspect` on trunk admits 2.6.0. |
| M4 | After the update, a new work item is created by the real 2.6.0 `route_work_item`. | The item is stamped `scoped`. Before `.ai-review/<id>/feedback/` exists, the Controller resolves the scoped path, where the 2.5.1 rule would have said flat (the defect this replaces). A feedback file written there drives the expected decision. |
| M5 | A job launched under 2.5.1; the installation is changed to 2.6.0 before verification. | Verification records a `FAILED` job with reason `workflow_release_changed`. In a variant where the process dies before verification, `resume` records the same `FAILED` outcome without re-dispatching. Neither leaves a pending job. |

M1's statuses were reproduced against the released 2.6.0 scripts on a copy of this repository,
with this work item's own 2.5.1-generated plan bundle:
- row 3 at the ready phase;
- row 10 after a 2.6.0 `REVISE`;
- row 5 after a 2.5.1 `REVISE`;
- row 11 after the real `ensure_plan_review_binding_marker` entry write, which leaves a
  `CONSUMED` record with `legacy: true`, so row 10 does not match.

**Local-only variant.** In `tests/test_integration_disposable_repo.py`, skipped when
`workflow-manager` is absent, as its siblings are: a real
`workflow-manager --release-version 2.5.1 bootstrap`, driven to M1's state, then a real
`workflow-manager --release-version 2.6.0 update`, `verify`, and M1's assertions. This proves that
the Manager's full write set gives the same results as the CI simulation's subset.

**Not supported, and documented in the guide rather than tested as working:**
- updating with a plan-approval journal in flight (Workflow says finish or abandon under 2.5.1
  first);
- expecting `/milestone-plan` at `IMPLEMENTING` to re-plan (2.6.0 routes to
  `/request-plan-amendment`, which is user-only in the Controller already).

### G. Documentation and release (CP6)

**Guides** (`docs/guide/`; CLAUDE.md: keep them current with behaviour):
- `concepts.md` or `installation.md`: which Workflow releases are supported. Also how to move a
  target to a new Workflow release through Workflow Manager, best between milestones, and, if in
  flight, only at the phases M1/M2 prove, never with a plan-approval journal in flight.
- `automation.md`: "At the 2.5.1 reference release" becomes per-release. The PLANNING and
  REVISING rows note the 2.6.0 bind step and the row-9 recovery. There is a short section on the
  two queries and the new gate `plan_review_binding_inconsistent`.
- `troubleshooting.md`:
  - `WORKFLOW_QUERY_FAILED`, including `query_script_modified` and `query_private_copy_failed`,
    at a decision (exit 20) and at verification (a `FAILED` job with reason
    `workflow_query_failed`);
  - the stale-plan-bundle gate under 2.6.0, including at `AWAITING_PLAN_APPROVAL`. For rows
    4b/4c, follow its `safe_resume_command`: the author-file steps, in the author-input directory
    it names (`plan-inputs/` or `current/`), then the generator. For row 4a the Controller offers
    no restore. The gate's command is `workflow-controller --work-item <id> explain
    <repository>`, which changes nothing, and a human decides. The entry says why no restore is
    offered: the Controller cannot establish that `current/files/` still holds the bound state,
    and cannot keep a path from changing between a printed step and its execution. It says what
    the bound state covers: the existence, mode and bytes of the protected paths the bundle's
    `MANIFEST.md` lists, and the classification sets of the item's artifacts declarations file.
    It also says that a restore overwrites post-binding edits that may exist nowhere else, so
    they are kept first and re-applied at the next editing phase. Workflow's "withdraw"
    alternative, quoted in `what_is_required`, discards both review stages and is never the
    `safe_resume_command`. The REJECTED-marker gate at a ready phase uses the same author-file
    steps, `AWAITING_PLAN_APPROVAL` included;
  - `WORKFLOW_RELEASE_CHANGED` before a decision (re-run, and a fresh `inspect` admits the
    installed release), including `installed: null` for a manifest that cannot be read. The
    refusal says what the preflight did: a completed action, which was recorded; a returned
    gate, which was not recorded and is rediscovered on the next invocation; and, whenever
    `preflight_events` contains `closed`, a completed close-out. Only the decision was refused.
    Also `workflow_release_changed` at verification, including an unreadable manifest, which is
    a terminal `FAILED` job;
  - the admitted-release refusal text.
- `milestone-branches.md`: the `integration_required` section names 2.5.1 and 2.6.0, and points
  to the named Workflow follow-up. The same goes for `ci-and-releases.md:180-183, 203-207`.
- `development.md`: the vendored release trees, `tools/workflow_releases.py`, and how to admit a
  future release (sync, run the per-release suites, add it to `VALIDATED_WORKFLOW_RELEASES` in a
  reviewed plan).

**ADRs.** A new **ADR 0006, "Workflow release admission and per-release contracts"**, records:
- admission by exact release;
- the contract table;
- queries as the single authority for 2.6+;
- tests on vendored trees;
- the E1-E5 answers and the named follow-up;
- target updates between milestones.

ADR 0001 (Context, "frozen v2.5.1") and ADR 0003 (the drift bullets) each get a one-line
pointer: "Admission widened to 2.6.0 by ADR 0006 (1.3.0)". `docs/README.md`'s ADR table gains
0006.

**Release notes.** `docs/releases/1.3.0.md` (new; `docs/README.md` gains a "Release notes" row).
It lists:
- 2.6.0 admitted;
- feedback resolution and plan-publication status from Workflow for 2.6+ targets;
- the new error codes (`WORKFLOW_QUERY_FAILED`, `WORKFLOW_RELEASE_CHANGED`) and gates;
- `workflow_release_changed`, and queries executing only the admitted release's script bytes,
  from a private copy;
- the refusal-evidence key rename;
- 2.5.1 unchanged.

It also carries the two behaviour changes that shipped in 1.2.1 without notes, which
`docs/ROADMAP.md` 1.2.1 assigned to "the next release's notes":
- ownership `source` follows the current basis;
- the 10800 s drain detach bound.

**Version.** `pyproject.toml` `version = "1.3.0"` (Decision 3). This is an explicit acceptance
criterion. `tools/release.py classify` at the milestone head must report `RELEASE_DUE` for 1.3.0
against the last release, `v1.2.1`.

**Roadmap.** `docs/ROADMAP.md` is **not** edited during implementation (operator direction:
"At a glance" already carries the agreed order). At acceptance, `/accept-milestone` marks 1.6
complete, updates "Where things stand" (Controller 1.3.0, admits 2.5.1 and 2.6.0), and the 1.6
completion note names the Workflow follow-up (Open question 1).

**Self-update dry run (Decision 2 evidence, not committed).** In a scratch clone of the CP6 head:
1. `workflow-manager --release-version 2.6.0 update <clone>`, then `workflow-manager verify
   <clone>`;
2. `python3 tools/run_tests.py` in the clone, with `workflow-manager` on `PATH`. This runs the
   Controller suites, including the real-Manager admission test of the clone
   (`test_real_workflow_manager_admits_this_repository`, release-agnostic since CP1), and the
   conformance family, which now runs the 2.6.0 suites;
3. `python3 -m controller inspect <clone>` from the clone.

All must pass, and the outputs are recorded in CP6's commit message. This proves the
post-release update is a Manager-output-only change.

## Checkpoints

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Vendored release trees, one phase list, per-release inventories: hash-pinned Workflow 2.5.1 and 2.6.0 subsets under tests/workflow_releases/ with tools/workflow_releases.py sync/check, release-parameterised fixtures, this repository's installed tree equal to the vendored tree of its declared release and its real-Manager admission test release-agnostic, target_state re-exporting decision's phase list, and the phase-set, command-partition, user-only and property-5 inventories iterating every admitted release | - | 2 | 1 |
| CP2 | Workflow contract module and query clients: controller/workflow_contract.py with the per-release contract table carrying the query scripts' sha256 digests, strict runners and parsers for --resolve-feedback-path and --plan-review-publication-status (the two scripts read once, digest-checked and run from a private copy of exactly those bytes with sys.executable -B -E -s, cwd target root, stdin closed, timeout, exact output validation, PublicationRefusal for rows 4d/6, every read, private-copy and execution failure a WorkflowQueryError exit 20), tested against the real vendored 2.6.0 scripts for every layout, acted-on status row, error, modified script and planted standard-library shadow, not yet wired | CP1 | 2 | 1 |
| CP3 | Release-aware feedback resolution: evidence.resolve_feedback_dir keeps the 2.5.1 rule byte-for-byte and asks Workflow for 2.6.0, the bound contract threaded explicitly from the admitted ManagedRepository through decide, job launch, verification and resume (the installed release re-checked right after the repository preflight with WORKFLOW_RELEASE_CHANGED naming the preflight's action, any unrecorded gate and any completed close-out, an unreadable installation manifest treated as a changed release, verification pinned to the job record's target_workflow_version, and release changes, unreadable manifests and query failures during verification caught once in _row_clauses_failure so launch and resume both record a FAILED job with the evidence and leave no pending job), and the plan-stage and external-implementation-review goldens gaining a release dimension with the 2.5.1 files byte-unchanged | CP2 | 3 | 1 |
| CP4 | Plan-review publication status and per-release writer declarations: for 2.6.0 two-stage items one outcome per ready phase and status class (the stale-bundle gate at all three ready phases, keeping the author-file steps in the directory the 2.6.0 generator reads ahead of Workflow's regenerate remedy and, for row 4a, a no-restore diagnostic gate whose parser-valid explain command changes nothing, since the Controller can neither establish that current/files holds the bound state nor hold a path's shape until a printed step runs, with no safe_resume_command ever naming the withdrawal; the REJECTED-marker gate's author-file steps at all three ready phases; the binding-inconsistent and unexpected-status gates), one outcome per non-ready phase and status class including the unexpected-status gate, and the plan-stage postconditions consume --plan-review-publication-status instead of revision coherence; expected_outcomes_for(release) substitutes the 2.6.0 bind writer for the PLANNING, NO_PHASE and REVISING_PLAN rows so property 5 is clean for both releases; no automatic /milestone-plan at a plan-review-ready phase | CP3 | 3 | 1 |
| CP5 | Admission of 2.6.0 and 2.5.1-to-2.6.0 migration: SUPPORTED_WORKFLOW_LINES and VALIDATED_WORKFLOW_RELEASES admit 2.6.0 beside 2.5.1, version fixtures and refusal tests updated, every per-release inventory and decision golden active for both releases; migration scenarios M1-M5 in CI (plan-review-ready bound milestones updated in place with mid-revision, leftover-REJECTED-marker at a review phase and at plan approval, and completed-withdrawal variants, implementing bound milestones, trunk-updated drift keeping integration_required, a new scoped item, a job straddling an update) plus a local real-Workflow-Manager variant; E1-E5 answered, merge_trunk left unwired, gate text naming both releases | CP4 | 3 | 1 |
| CP6 | Documentation, release 1.3.0 and full verification (terminal): docs/guide updates, ADR 0006 with pointers from ADRs 0001 and 0003, docs/releases/1.3.0.md, pyproject.toml version 1.3.0 with release classify RELEASE_DUE, the self-update dry run of this repository to Workflow 2.6.0 in a scratch clone, and the full sharded, packaged-runtime, CI-workflow and golden verification | CP5 | 2 | 1 |

### CP1 -- vendored release trees, one phase list, per-release inventories

Design A. Files:
- `tests/workflow_releases/{2.5.1,2.6.0}/**`, `tools/workflow_releases.py`,
  `tests/test_workflow_releases.py`;
- `tests/fixtures.py`, `controller/target_state.py`, `controller/decision.py` (comments only);
- the eight test modules that read this repository's tree;
- `tests/test_managed_repo.py` (the release-agnostic real-Manager admission test).

Tests: the new module; `test_decision`, `test_target_state`, `test_job_validation` (property 5
per release), `test_evidence`, `test_resume`, `test_packaged_runtime`,
`test_trunk_orchestration_e2e`, `test_managed_repo` (with `workflow-manager` on `PATH`); the full
sharded run.

Exit: every existing test passes, the goldens are byte-unchanged, and no test asserts this
repository's installed release as a literal.

### CP2 -- the Workflow contract module and the two query clients

Design B. Files: `controller/workflow_contract.py`, `controller/__init__.py`, `controller/errors.py`,
`tests/test_package_structure.py` (`DEPENDENCY_ORDER`), `tests/test_workflow_contract.py`.

Exit: every row and error listed in Design B is covered against the real vendored 2.6.0 scripts,
including the digest refusal with nothing executed, the planted standard-library shadows and the
planted `.pyc` not executed, and the private directory removed on every path. The query wall
time, read, digest check and copy included, is measured and recorded, and nothing is wired yet.

### CP3 -- release-aware feedback resolution

Design C. Files: `controller/evidence.py`, `controller/job.py` (launch, the release re-check
before every decision, verification, resume contract, the clause signatures),
`controller/workflow_contract.py` (`bind`, `BoundContract`), `controller/managed_repo.py`
(`installed_workflow_version`), `controller/errors.py` (`WorkflowReleaseChangedError`),
`controller/milestone_branch.py` (the `Context.events` field and its append in `_event`),
`controller/cli.py` (only if threading needs it), the two golden generators and their 2.6.0
golden files, `tests/test_evidence.py`, `tests/test_job.py`, `tests/test_resume.py`,
`tests/test_cli.py`, and the direct clause-call sites in `tests/test_job_validation.py` and
`tests/test_lifecycle_orchestration.py` (the 2.5.1 binding argument only).

Tests, beyond the goldens:
- **a release changed between two steps of one `run`.** The run is held at the orchestration
  boundary with the existing test-support pause file, `installation.json` is changed, and the
  run is released. The second step refuses with `WORKFLOW_RELEASE_CHANGED` before any decision,
  its evidence names `preflight_action`, and the fake worker's invocation counter shows exactly
  one launch. A second case changes the release while the preflight returns a gate: the step
  refuses before the gate is recorded, with `preflight_action: "gate"`, the gate's code in
  `preflight_gate`, and a message saying that the gate was not recorded. A third case removes
  the manifest, and a fourth makes it malformed, instead of changing it: the same refusal, with
  `installed: null` and the parser error's code under `manifest_error`. A fifth case closes out
  a milestone, after which the same preflight returns an outcome other than `closed_out`, a gate
  or another action (arranged through a second binding record or an unbound work item on the
  trunk). The refusal's `preflight_events` contains `closed`, and its message states both that
  the close-out completed and what the returned outcome was;
- **a query failure at decision time**, including in the step's pre-state capture: exit 20, no
  launch, no job record;
- **the level of the `resume` cases.** Every `resume` case below calls `job.resume` directly,
  with a `ManagedRepository` inspected before the failure is arranged. The CLI's `resume`
  inspects first (`cli.py:1079`) and refuses there while the manifest is missing or damaged, so
  at the CLI level those cases would test `inspect`, not verification. The CLI-level behaviour
  has its own case below;
- **a query failure from every query-reading predicate and postcondition**, at launch and at
  `resume` (from a `COMPLETED` record and from a `LAUNCHED` one). These are
  `_predicate_row3_block_feedback_current`, `_predicate_local_implementation_block_current`,
  each row that reads feedback through `_feedback_verdict_failure`, and
  `_postcondition_manual_implementation_approve_recorded`/`_revise_recorded`. Each case ends:
  - in a `FAILED` job with reason `workflow_query_failed`, whose `workflow_error` carries
    `WORKFLOW_QUERY_FAILED` and the query's evidence. One case per path uses
    `query_script_modified`, with the fake worker altering a managed script;
  - with `pending_reconciliation_jobs` empty, and a following `step` not refused with
    `PendingJobReconciliationError`;
  - with no relaunch;
- **a failed private copy during verification**, at launch and at `resume` from a `COMPLETED`
  record. The runner's private-copy write is made to fail with `OSError(ENOSPC)`, armed only
  after the worker has completed. It ends in a terminal `FAILED` job with reason
  `workflow_query_failed`, whose `workflow_error` evidence carries
  `reason: "query_private_copy_failed"`, and it leaves no pending job;
- **M5's verification rule**: the same three paths with the installed release changed end in
  `FAILED` with reason `workflow_release_changed` and leave no pending job;
- **an installation manifest that cannot be read at verification.** The fake worker removes the
  manifest in one case, and in others replaces it with non-JSON bytes or with
  `schema_version: 2`. Each runs on the three paths: at launch, immediately after the worker
  completes; and at `resume` from a `COMPLETED` and from a `LAUNCHED` record, with the manifest
  removed or damaged after the `inspect` whose `ManagedRepository` `job.resume` receives. Each
  ends:
  - in a terminal `FAILED` job with reason `workflow_release_changed`, whose `workflow_error`
    evidence has `installed: null` and `manifest_error` carrying `UNMANAGED_REPOSITORY` or
    `MALFORMED_INSTALLATION_MANIFEST`;
  - with `pending_reconciliation_jobs` empty. Once the manifest is restored, a following `step`
    is not refused with `PendingJobReconciliationError`;
  - with no relaunch;
- **CLI `resume` while the manifest is still unreadable**, from a `COMPLETED` and from a
  `LAUNCHED` record. It exits 20 at `inspect` (`UNMANAGED_REPOSITORY` or
  `MALFORMED_INSTALLATION_MANIFEST`), and the job record is byte-unchanged and still pending.
  Once the manifest is restored, the next CLI `resume` reconciles the record as it would have
  with the manifest intact.

Exit: the 2.5.1 goldens are byte-identical (`--release 2.5.1 --check`), the 2.6.0 goldens are
generated and checked, and the tests above pass.

### CP4 -- plan-review publication status and per-release writer declarations

Design D. Files: `controller/evidence.py` (the gate builders), `controller/job.py`,
`controller/decision.py` (only if a shared gate helper is needed), `tests/test_evidence.py`,
`tests/test_job_validation.py`, `tests/test_decision.py`, and the 2.6.0 plan-stage golden
(regenerated once, deliberately).

Exit:
- property 5 returns `[]` for both releases, and the I4 test passes;
- each of the fifteen ready-phase cells of Design D's table is asserted: S1-S5 at
  `AWAITING_LOCAL_PLAN_REVIEW`, at `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` and at
  `AWAITING_PLAN_APPROVAL`, the last including S2 replacing the approval gate;
- each of the twelve non-ready cells is asserted: N1-N4 at `PLANNING`, at `REVISING_PLAN` and at
  `AMENDING_PLAN`;
- every cell Workflow can produce is driven with real 2.6.0 bundles and scripts: S1 (rows 2 and
  3), S2 (4a, 4b, 4c), S3 (4d), N1 (rows 5, 7, 8, 9, 10 and 11, each at a phase where Workflow
  produces it) and N2 (row 6). The cells real 2.6.0 scripts never produce (S4, N4) and the query
  failures (S5, N3) are asserted through the answers seam and the private runner hook. The one
  exception is S5 with the plan document deleted or replaced by a link, which is driven with the
  real script (below);
- S2's `safe_resume_command` is asserted per row:
  - for 4b and 4c, the author-file steps and then the generator, naming `plan-inputs/` when that
    directory exists and `current/` when it does not, with and without the author files present.
    The steps are carried out with the real 2.6.0 generator, and the status becomes `BOUND`;
  - for 4a, exactly the no-restore gate. It is built from a real 2.6.0 bound bundle in each of
    the following cases, and the real query reads each one as row 4a:
    - the plan document's bytes edited;
    - an entry added to the declarations file's `plan_stage.excluded_paths`, which changes no
      protected path;
    - the plan document's owner-execute bit set with no byte change, in a repository whose
      `core.fileMode` is true;
    - a fourth plan-stage protected path, which the fixture item declares, committed at
      `base_commit` and unchanged since, deleted. The fresh id is unreadable;
    - that fourth path replaced by a symbolic link to a file outside the repository that holds its
      bound bytes. Workflow hashes the link as mode `120000`;
    - the plan document's bytes edited and its `current/files/` copy then deleted; in a second
      case the copy given other bytes, and in a third its owner-execute bit flipped. The fixture's
      plan document is new since `base_commit`, so it differed from the base when it was bound.
      (With the working tree unchanged, a damaged copy alone is row 4b, not 4a.)
    - the plan document's bytes edited and the gate rendered, and only then a parent directory of
      the fourth protected path replaced by a symbolic link to a directory outside the repository
      that holds a copy of its contents, before the advertised command is executed.

    In each case `safe_resume_command` is exactly
    `workflow-controller --work-item <id> explain <repository>`, and `cli.build_parser()` parses
    it to the `explain` subcommand with that work item and repository. The exact rendered text is
    then executed through `cli.main` on the same repository. It exits 0 and prints the same gate,
    except in the last case: there the real query either reads row 4a again or fails, because the
    link is a changed path that no classification set covers
    (`assert_all_changed_paths_classified_worktree`, `P6 wf:1598-1600`). The test pins the
    measured outcome, the same gate or S5's refusal with exit 20. In every case, a snapshot taken
    with `lstat` before the command and after it is identical: it covers the working tree,
    `.ai-review/<id>/`, and every link, link target and outside directory that the case creates
    (entries, file bytes and modes, link target strings). `what_is_required` makes every
    statement Design D lists. The S3/S4 gate cases assert the same command;
  - the row-4a gate is a function of the answer and the item id alone. Through the answers seam,
    with a row-4a answer, it is byte-identical whether `.ai-review/<id>/current/` is present or
    removed;
  - the plan document deleted, and in a second case replaced by a symbolic link, after binding.
    The real query fails (exit 1), because the plan document is validated while the item's
    metadata is resolved. The step refuses with exit 20 (S5) and writes nothing;
  - for every row, it never contains `/milestone-plan`, while `what_is_required` quotes
    Workflow's complete remedy and `detail`;
  - the row-4c gate is built from the real script's answer, which has no
    `fresh_review_content_id`, and its evidence omits that key. The row-4a case with an
    unreadable fresh id omits it too;
- the REJECTED-marker gate under the query contract, for a `"2.2"` item, takes its plan-stage
  branch (the clause, then the author-file steps) at each of the three ready phases,
  `AWAITING_PLAN_APPROVAL` included. Under the 2.5.1 contract, and for a `"1"` item, it keeps
  today's last branch at `AWAITING_PLAN_APPROVAL`, byte-identical. M1d (CP5) carries the
  approval-phase recovery out with the real generator;
- a query failure in the plan-stage postcondition ends in a `FAILED` job with reason
  `workflow_query_failed` and no pending job, at launch and at `resume` (Design C's test,
  extended to the postcondition this checkpoint adds).

### CP5 -- admission of 2.6.0 and 2.5.1 → 2.6.0 migration

Designs E and F, and the E1-E5 consequence. Files: `controller/managed_repo.py`,
`controller/milestone_branch.py` (gate and abandon text), `controller/errors.py` (docstring),
`tests/test_managed_repo.py`, `tests/test_cli.py`, `tests/fixtures.py`,
`tests/test_workflow_release_migration.py`, `tests/test_integration_disposable_repo.py`
(local-only variant).

Exit:
- M1-M5, with M1's variants M1a, M1b, M1c and M1d, pass in the sharded run;
- the local-only variant passes on this machine with the real Workflow Manager;
- every per-release inventory and golden runs for both releases.

### CP6 -- documentation, release 1.3.0 and full verification (terminal)

Design G. Files: `docs/guide/*.md` (as listed), `docs/adr/0006-*.md`, `docs/adr/0001-*.md`,
`docs/adr/0003-*.md`, `docs/README.md`, `docs/releases/1.3.0.md`, `pyproject.toml`.

Verification (see below), plus the self-update dry run.

## Decisions for the reviewer and the user

This repository has no `docs/TECHNICAL_DECISIONS.md`, so there are no "Open decision" rows to check
against. The choices below are made in this plan and flagged rather than silently finalised.

1. **Item 4: `merge_trunk` stays unwired, and the follow-up is named.** E1-E5 are all negative in
   the released 2.6.0 (table above), so there is no released base-moving transition to bind to.
   The fail-closed `integration_required` gate and the documented manual merge stay the contract
   for both admitted releases. The narrow Workflow follow-up is named above: an integration record,
   a base-moving transition, merge-admitting provenance, legal phases, and a mergeable state model.
2. **Item 6: this repository's own update to Workflow 2.6.0 is a separate change after the 1.3.0
   release, not part of this milestone.** The reasons:
   - The Controller that drives this milestone is the **installed 1.2.1 release**, which refuses
     2.6.0 (`UNSUPPORTED_WORKFLOW_VERSION`, exit 20). Updating inside the milestone would strand
     its own remaining lifecycle (reviews, functional review, readiness, close-out), and admission
     only takes effect for the driving Controller once 1.3.0 is published and installed.
   - Workflow Manager's `update` checks no in-flight work item. It would replace `scripts/` and
     `.claude/commands/` under this item after its plan was reviewed and approved against 2.5.1
     commands, and a plan-approval journal mid-flight is explicitly unsupported by 2.6.0.
   - A mid-milestone update on the milestone branch would also make this milestone's own PR carry
     the Workflow update, mixing a Manager-owned change into the Controller release.
   - ROADMAP 1.6 already says "between milestones", and Design A (I7) makes the update require no
     Controller code change.

   **Procedure** (documented in `docs/guide/`; each commit and PR authorised by the user at that
   time). After the 1.3.0 PR is merged, the release is published and 1.3.0 is installed, with no
   active work item on `main`:
   1. branch `chore/workflow-2.6.0`;
   2. `workflow-manager --release-version 2.6.0 update .` and `workflow-manager verify .`;
   3. `python3 tools/run_tests.py`, and `workflow-controller inspect .` admits 2.6.0;
   4. commit only the Manager's output, open a PR, and merge it.

   There is no version bump, so the merge classifies `NO_CHANGE` and publishes nothing. CP6's dry
   run proves this in advance. The next milestone (step 2 of "At a glance") then runs under
   Workflow 2.6.0.
3. **Item 7: release 1.3.0 (minor).** New capability (a second admitted Workflow release), with
   2.5.1 behaviour unchanged. The one visible JSON change (Decision 5) is additive in meaning and
   limited to a refusal path, so it is not a major bump. The bump is in CP6, and "`pyproject.toml`
   is 1.3.0 and `classify` reports `RELEASE_DUE`" is an explicit acceptance criterion.
4. **Release notes are a committed file, and the GitHub release body stays the policy template**
   (`"workflow-controller {tag}"`). Rendering notes into the release is a release-policy change,
   which belongs with the versioning work (step 2), not here.
5. **Refusal evidence key `supported_workflow_line` → `supported_workflow_lines`** (a list). The
   alternative, keeping the old key with a made-up single value, would be false. This is listed
   in the release notes.
6. **The no-policy lifecycle golden stays 2.5.1-only.** It pins trunk-policy invariance (trunk
   plan I1), not Workflow decisions. The decision goldens and all inventories run per release.
7. **Vendoring about 2.6 MB of released Workflow bytes into `tests/`.** CI cannot reach Workflow
   Manager, and I7 requires tests independent of this repository's own installation. The bytes
   are hash-pinned to the Manager manifests, and nothing else from the payloads is vendored.
8. **Query execution: `sys.executable -B -E -s` on a private copy of the digest-checked bytes,
   not `python3` from `PATH`, not `-P`, and not the target's `scripts/` in place.** This gives:
   - the same interpreter as the Controller;
   - no environment leakage;
   - no `__pycache__` written into the target, and none of the target's read;
   - the sibling import 2.6.0 needs, from a directory holding only the two verified files;
   - no module from the target's `scripts/` directory on `sys.path`, so no standard-library name
     can be shadowed;
   - execution of exactly the bytes that were hashed, with no window between check and execution
     (I6).

   The digests live in the Controller's contract table, not in the target's installation
   manifest, which a modified target could rewrite together with the scripts. The alternative
   the local review also measured, `-I -c` with a launcher that appends the target's `scripts/`
   to the end of `sys.path` and calls `runpy.run_path`, writes nothing. It was not chosen
   because the interpreter re-reads the files after the Controller's check, and a name the
   standard library lacks would still resolve from the target.
9. **Row 9 (`PUBLISHED_UNBOUND`) is recovered by the existing automatic explicit-id command**, not
   by a new gate, because that is exactly Workflow's documented remedy. Rows 4a-4c and every refusal
   stop at human gates that quote Workflow's remedy in `what_is_required`. The
   `safe_resume_command` is always Controller-authored and never includes Workflow's withdrawal:
   for 4b and 4c the author-file steps ahead of the regenerate remedy, and for 4a a valid
   `explain` command (Decision 12) at a no-restore diagnostic gate (Design D, S2). No gate this
   plan adds changes the working tree. The Controller cannot establish that a row-4a restore
   source still holds the bound state, because Workflow's bundle-verification result is not in
   the answer. Nor can it keep a path's shape from changing between a printed step and its
   execution. A restore it could vouch for would need a new Controller command that writes to
   target working trees and re-implements Workflow's bundle verification, which is outside this
   minimal plan. The gate says what the bound state covers, that a restore overwrites
   post-binding edits, and that a human decides.
10. **Base `2296ad6`** (tip of `origin/main`, operator-supplied) rather than the previous acceptance
    commit `17abcad`.
11. **The 2.6.0 decision goldens inject Workflow's answers instead of generating real bundles for
    each case.** The plan-stage golden has 592 cases today. Real bound bundles for each (scenario,
    phase, version, status) would need a real generator run per case, and synthetic manifests
    never verify under 2.6.0, so every ready-phase case would collapse to one gate. Recorded
    answers pin the Controller's full decision table instead: every scenario against every layout
    and every status class. Real-script coverage lives in CP2 (the queries), CP4 (each status/gate
    row with real bundles) and CP5 (M1-M5). The seam is test-only by construction (Design B).
12. **The gates this plan adds render a valid `explain` command. The existing helper is left
    unchanged.** `_explain_command` (`evidence.py:1484-1485`) renders
    `workflow-controller explain --work-item <id>`, which the parser rejects with exit 2:
    `--work-item` is a global option that must precede the subcommand, and `explain` requires
    the repository (`cli.py:239-273`). `docs/ROADMAP.md:81-84` already records this as a known
    follow-up. Every gate that uses the helper today is reachable at 2.5.1, so fixing it here
    would change 2.5.1 gate text (I1); the fix belongs to that follow-up. The gates added here
    render `workflow-controller --work-item <id> explain <repository>` through a new helper
    instead. Those gates are row 4a's gate, `plan_review_binding_inconsistent` and
    `unexpected_plan_review_status`. The form is the one `milestone_branch.py:519` already uses
    for `milestone-binding`. CP4 parses the exact rendered text with `cli.build_parser()` and
    runs it through `cli.main`.

## Open questions

1. **Where to record the Workflow follow-up beyond this plan and ADR 0006.** The operator directed
   that `docs/ROADMAP.md` be edited only to mark 1.6 complete. This plan puts the follow-up in the
   1.6 completion note. Should it also be added to section 1.7's scope (Workflow 2.7, step 5 of "At
   a glance"), or filed in the Workflow Manager repository instead?
2. **Should the Controller refuse a mid-flight Workflow release change at unsafe phases** (for
   example a plan-approval journal in flight) instead of only documenting it? This plan documents
   it and relies on Workflow's own refusals, which keeps scope minimal. A Controller refusal would
   be a small addition to CP5 if wanted.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-workflow-2-6-integration-artifacts.json` starts from
`generate_artifacts_declarations(..., work_item_type="product")` and is fitted to this plan's
footprint, as the previous Controller milestones' declarations were.

**Plan stage.**
- Protected: this plan, its registry and its mapping.
- It inherits the template exclusions and adds, as excluded implementation content:
  `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `pyproject.toml`, `setup.py`,
  `docs/guide/`, `docs/releases/`, `docs/README.md`.

**Implementation stage.**
- Protected prefixes: `controller/`, `tests/` (including the vendored trees), `tools/`,
  `.workflow-controller/`, `docs/guide/`, `docs/releases/`, and the inherited `docs/adr/`.
- Protected paths: `README.md`, `docs/README.md`, `pyproject.toml`, `setup.py`, the three
  rendered `.github/workflows/` files (`validate.yml`, `ci.yml`, `main.yml`), and the artifacts
  file itself.
- The inherited product-template entries stay.

`docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md` and `docs/ai-workflow/` stay excluded, as narrative
and bookkeeping.

Two **untracked** files sit in this checkout's root: `SHA256SUMS` and
`workflow_controller-1.2.1-py3-none-any.whl`, operator-downloaded 1.2.1 release assets. The
plan-stage digest classifies the working tree, and refused on them. They are excluded by exact path
at both stages, with that reason. They are not part of the repository, this milestone never
commits them, and they are left where they are. `scripts/`, `.claude/commands/` and `.workflow-manager/` keep their inherited
exclusion. This milestone never edits this repository's installed Workflow (Decision 2).

## Verification

**Per checkpoint.** Each checkpoint runs:
- its own new and changed modules directly (`python3 -m unittest tests.<module>`);
- the full selection through the sharded default, `python3 tools/run_tests.py`, in the
  foreground, never with a plain `&`, and never with `PYTHONPATH=.`.

The serial runner (`--serial`) is reference evidence only, and is not rerun when equivalent
evidence exists for the same revision and inventory.

**Additionally:**
- CP1 and CP5 run `python3 tools/workflow_releases.py check`;
- CP3-CP5 run each golden generator with `--check` for every admitted release;
- CP5 runs the local-only real-Manager variant.

**CP6, the terminal verification:**
- the sharded full selection (Controller and conformance families);
- `CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime`;
- `python3 tools/ci_workflows.py --check`;
- every golden `--check` for both releases;
- `python3 tools/workflow_releases.py check`;
- `python3 tools/release.py classify` (expected `RELEASE_DUE`, 1.3.0);
- the self-update dry run.

Nothing needs the real `claude`. Package-index access is needed, as it already is at the base
commit (`fixtures.editable_install`). New test modules get default timing estimates until the
committed profile is refreshed, which is not part of this milestone.

**Implementation review bundles.** From CP1 on, each implementation bundle's `DIFF.patch` and
`files/` carry about 2.6 MB of vendored Workflow source. Every implementation round's
`REVIEW_REQUEST.md` states that the vendored trees under `tests/workflow_releases/` are verified
by hash rather than read line by line:
- `tools/workflow_releases.py check` against `RELEASE.json`;
- the Manager-manifest equality test, run locally;
- the contract digest test (Design B).

It points the reviewers at the Controller changes instead.

**Acceptance criteria:**
1. `VALIDATED_WORKFLOW_RELEASES == {"2.5.1", "2.6.0"}`, and every per-release inventory and
   decision golden passes for both.
2. For a 2.5.1 target, behaviour is byte-identical (I1 evidence: the unchanged goldens and the
   unchanged existing tests).
3. For a 2.6.0 target, feedback paths come from `--resolve-feedback-path` for all three layouts,
   and plan-bundle readiness comes from `--plan-review-publication-status`.
4. M1-M5 pass in CI, with M1's statuses matching the released 2.6.0 table (row 3 at the ready
   phase, row 10 after a 2.6.0 `REVISE`, row 5 in M1a, then row 11 after the entry marker, row
   4c in M1c), and the real-Manager variant passes locally.
5. E1-E5 are answered in ADR 0006 and the guides, `integration_required` is unchanged, and
   `merge_trunk` is unwired.
6. **`pyproject.toml` is `1.3.0`, `docs/releases/1.3.0.md` exists, and `classify` reports
   `RELEASE_DUE`.**
7. The guides and ADRs agree with the code, and the self-update dry run is green with
   `workflow-manager` on `PATH`, including `test_real_workflow_manager_admits_this_repository`.
8. Every 2.6.0 query executes a private copy of exactly the bytes whose digests it checked, and no
   module from the target's `scripts/` directory other than those two scripts can execute during
   a query (CP2's planted-shadow test). Every decision and verification is preceded by the
   installed-release check (I3, I6).
9. Design D states one outcome per (plan phase × status class), ready (S1-S5) and non-ready
   (N1-N4), and the 2.6.0 plan-stage golden and CP4's tests pin exactly that table.
10. Whenever verification runs, on the launch and the `resume` path alike, every query failure
    (a failed private copy included) and every release change records a `FAILED` job with its
    evidence and leaves no pending job. So does an installed release that verification cannot
    establish (a missing or malformed installation manifest). A CLI `resume` while the manifest
    is still unreadable refuses at `inspect` (exit 20) and leaves the record untouched until the
    manifest is restored.
11. At the stale-plan-bundle gate (S2), for a 2.5.1-created item whose author files are absent,
    the gate's `safe_resume_command` succeeds when followed. M1c proves it with the real 2.6.0
    generator. Under the query contract the REJECTED-marker gate gives the same author-file
    recovery at all three ready phases, and M1d proves it at `AWAITING_PLAN_APPROVAL`.
12. No `safe_resume_command` names Workflow's withdrawal, and none that this plan adds changes the
    working tree. Row 4a's gate offers no restore. Its `safe_resume_command`, like that of every
    other gate this plan adds with no automatic continuation, is
    `workflow-controller --work-item <id> explain <repository>`, which the Controller's parser
    accepts and which exits 0. Its `what_is_required` quotes Workflow's remedy and `detail`, says
    why the Controller offers no restore, names the declarations file beside the protected paths,
    and warns that a restore overwrites post-binding edits. The advertised command is executed on
    real 2.6.0 bound bundles in every CP4 row-4a case: a byte edit, a declarations edit, a
    mode-only change, a deleted protected path, a protected path replaced by a link, a deleted,
    byte-modified or mode-modified `current/files/` copy of a path that differed from
    `base_commit`, and a parent directory replaced by a link after the gate is rendered. In every
    case it leaves the working tree, `.ai-review/<id>/` and every link target unchanged.

## Migration / data-integrity notes

- **Controller runtime records:** nothing is migrated. Job records already carry
  `target_workflow_version`, and every existing record says `2.5.1`.
- **Operator work at row 4a:** the Controller offers no restore at row 4a, for a 2.6.0-created
  item and for a 2.5.1-created item that reaches row 4a after migration alike. Its gate changes
  nothing, so following it loses no uncommitted plan edit, deletion or mode change, and it can
  neither restore content that was not bound nor write through a link. The operator restores the
  bound state, or withdraws, by hand. The gate says what the bound state covers, and that a
  restore overwrites post-binding edits that may exist nowhere else, so they are kept first.
- **Target repositories:** the Controller writes nothing new to targets. The queries are
  read-only subprocesses run with `-B` from a private copy in a temporary directory the
  Controller removes, so they leave nothing in the target, `__pycache__` included.
- **Rollback:** reinstalling 1.2.1 restores 2.5.1-only admission. A target already updated to
  2.6.0 is then refused, fail-closed, as it is today.

## Plan review decisions

### Revision 2 (local-model plan review, round 1, `REVISE`)

All nine findings were validated against the repository and the 2.6.0 payload (Workflow Manager
`distribution/workflow/2.6.0`), and all were accepted. None is rejected.

- **LP-R1-001 (Important), accepted.** Released 2.6.0's `record_local_plan_review(...,
  verdict="REVISE")` writes a non-legacy `CONSUMED` record from its own `review_content_id`
  (`P6 ws:14878-14883`, `_write_consumed_plan_review_binding` at `:15245`). Row 5 requires
  `record is None` (`:15665`). Reproduced on scratch copies of this repository, with this work
  item's own 2.5.1-generated bundle and the three 2.6.0 scripts:
  - row 3 at `AWAITING_LOCAL_PLAN_REVIEW`;
  - row 10 `NEEDS_EDIT` after a 2.6.0 `REVISE`;
  - row 5 `LEGACY_UNMARKED` for the item as it stands (at `REVISING_PLAN` after a 2.5.1 `REVISE`);
  - row 11 `EDIT_IN_PROGRESS` after the real `ensure_plan_review_binding_marker` (a `legacy: true`
    record).

  M1 now asserts row 10. The new variant M1a covers the genuine row-5 case with its dispatch and
  post-marker status. Acceptance criterion 4 pins the statuses.
- **LP-R1-002 (Important), accepted, first option, plus the bytecode cache.** Confirmed:
  `cmd_run` inspects once (`cli.py:1009`) and passes the same target to every
  `_run_one_step`, and job verification deliberately does not re-inspect (`job.py:55-70`).
  The design now has three parts:
  - I3 and Design C re-read the installed release before every decision (new
    `WorkflowReleaseChangedError`, `WORKFLOW_RELEASE_CHANGED`) and before verification;
  - I6 and Design B check the two scripts' sha256 against digests carried in
    `RELEASE_CONTRACTS` before every query;
  - `-X pycache_prefix=<os.devnull>` was added. A scratch measurement under Python 3.14.7 showed
    that `-B` alone still *executes* a planted `.pyc` whose recorded mtime and size match the
    source, which would have defeated the digest check.

  The re-check is placed after the repository preflight, because close-out switches to trunk
  inside a step (`milestone_branch.py:960`). M3 therefore now expects `WORKFLOW_RELEASE_CHANGED`
  after a close-out onto a 2.6.0 trunk. CP2 and CP3 gain the requested tests (a modified script,
  and a release changed between two steps of one `run`).
- **LP-R1-003 (Important), accepted.** `tests/test_managed_repo.py:56-63` asserts `"2.5.1"`, and
  `workflow-manager` resolves to `/home/rodrigo/.local/bin/workflow-manager`. CP1 makes the test
  release-agnostic and lists the file. A search of `tests/` for other assertions of this
  repository's own release found none:
  - `test_ci_workflows.py:143` compares against the manifest's own hash;
  - the `test_cli.py` `inspect` tests use the stub Manager.

  The dry run and acceptance criterion 7 name the test.
- **LP-R1-004 (Important), accepted, the stale-plan-bundle gate.** The revision-1 table and the
  paragraph after it gave different outcomes for `AWAITING_PLAN_APPROVAL`. Design D now has one
  cell per (ready phase × status class), S1-S5, plus N1-N3 at the non-ready phases. At
  `AWAITING_PLAN_APPROVAL`, 4a-4c go to the stale-plan-bundle gate, which replaces the approval
  gate, because 2.6.0's approval refuses unbound content. CP4's exit criteria enumerate every
  cell.

  `HumanGate` has no id field (`decision.py:514-530`), so "gate id" is replaced by the builder
  name (`evidence._stale_plan_bundle_gate`, `evidence.py:2075`).
- **LP-R1-005 (Optional), accepted, stated explicitly and kept.** `evidence.py:2548-2555` routes
  the plan-stage REJECTED gate through `_plan_bundle_recovery_steps`. In 2.6.0:
  - plan-stage generation writes no marker (`P6 milestone-plan.md` step 6);
  - a successful generation clears a leftover one (`P6 wf:3081`);
  - `seed_plan_review_inputs` falls back to `current/` (`P6 wf:2984-3009`).

  So today's steps still work for a leftover 2.5.1 marker. Design D says so, the Investigation's
  "2.5.1-only advice" sentence is corrected, and M1b covers the case.
- **LP-R1-006 (Optional), accepted.** "four" is now "five".
- **LP-R1-007 (Optional), accepted.** Design F now says "the Controller-relevant subset", and the
  real-Manager variant carries the full write set.
- **LP-R1-008 (Optional), accepted.** Design C, "Query failures", and I2:
  - decision time: exit 20 through the existing CLI mapping (`execute_step` has no catch);
  - verification: "not satisfied", so a `FAILED` job with the query evidence, as
    `job.py:629-632`, `:652` and `:683` already do;
  - `resume`: the same outcome.

  CP3 tests each point.
- **LP-R1-009 (Optional), accepted.** The Verification section now requires each implementation
  `REVIEW_REQUEST.md` to state that the vendored trees are verified by hash.

The registry is regenerated at revision 2 with the same six checkpoints, the same dependencies
and the same sizes. The names of CP1-CP5 are updated to match the design changes above. The
mapping gains R14 ("a query executes only the admitted release's bytes, under the admitted
release"), owned by CP2 and CP3. No other requirement changed.

### Revision 3 (local-model plan review, round 2, `REVISE`)

All six findings were validated against the repository and the 2.6.0 payload (Workflow Manager
`distribution/workflow/2.6.0`, unchanged since `136c417`), and all were accepted. None is
rejected.

- **LP-R2-001 (Important), accepted, option (a): a private copy of the verified bytes.**
  Confirmed: without `-P`, the executed script's directory is `sys.path[0]`, and
  `workflow_fingerprint.py:216`/`workflow_state.py:183` import `uuid`/`secrets` at module level.
  Reproduced on a clone of this repository with the three 2.6.0 scripts and a planted
  `scripts/uuid.py`: with revision 2's argv, `--resolve-feedback-path` exited 0 with the correct
  answer and the planted module wrote its sentinel. With the two scripts copied into a private
  temporary directory and run from there (`cwd` the clone), both queries answered correctly (row
  5 `LEGACY_UNMARKED`, matching this item), no sentinel appeared, and nothing was written into
  the private directory. Neither script locates resources through `__file__`, and the only lazy
  import in either is `argparse`. I6, Design B's runner, Decision 8 and acceptance criterion 8
  are rewritten. The copy is written through `runtime.write_bytes` inside a
  `tempfile.TemporaryDirectory`, the pattern `release_txn.py:293` and `worker.py:812` already
  use, so write containment holds. `-X pycache_prefix` is dropped, with the reason in Design B,
  and the measured cost is recorded there. CP2 gains the planted-shadow test for both queries
  and the private-directory cleanup test. Option (b) is recorded in Decision 8 as not chosen.
- **LP-R2-002 (Important), accepted.** Confirmed: `job.py:629-632`, `:652` and `:683` wrap only
  `target_state.read`. The feedback reads at `:556`, `:892`, `:1011` and `:1033`, and
  `plan_bundle_coherence` at `:662`, are outside any `try`, and `_row_clauses_failure`
  (`:1738-1785`) calls the clauses unguarded. "There is no new handling path" and the citation
  are deleted. Design C, "Query failures", now names one catch point, `_row_clauses_failure`,
  which both `_verify_transition` and `_row2_verified` (`:2249`) already share. It returns
  `workflow_query_failed` with the error under a new `workflow_error` evidence key. The
  verification-time release check moves into the same helper (`workflow_release_changed`), so
  launch and `resume` apply both rules identically. Each caller records `FAILED`.
  `_reconcile_launched` checks the two new reasons ahead of its `INTERRUPTED` and unreconcilable
  branches. CP3's test now covers every query-reading predicate and postcondition, at launch
  and at `resume` from `COMPLETED` and `LAUNCHED` records, each ending `FAILED` with no pending
  job and a following `step` not refused with `PendingJobReconciliationError`
  (`errors.py:541`). CP4 extends it to the plan-stage postcondition. The new acceptance
  criterion 10 and requirement R15 state it.
- **LP-R2-003 (Important), accepted.** Confirmed:
  - 2.5.1's completed `withdraw_bundle` removes its own marker
    (`scripts/workflow_fingerprint.py:2388-2450`);
  - 2.6.0's 4c and 4b remedies name the bare generator (`P6 ws:15617-15618`, `:15652-15653`);
  - `seed_plan_review_inputs` stubs a missing author file empty (`P6 wf:2984-3009`), and the
    closing check then raises `MissingReviewContentIdStatementError` (`P6 wf:3877`).

  The generator also requires `TEST_RESULTS.md`'s `head:` to equal this generation's HEAD
  (`P6 wf:3281-3311`), so a present-but-stale author file fails too. S2 therefore keeps today's
  `_plan_bundle_recovery_steps` as the `safe_resume_command` for rows 4b and 4c, ahead of the
  generator command Workflow's remedy names. That remedy and `detail` are quoted in the gate
  text. This is a slight superset of the requested "when the author files are absent", because
  the refresh steps are needed whenever HEAD moved. Row 4a's remedy runs no generator and stays
  Workflow's. Under the query contract the steps name `plan-inputs/` when it exists, else
  `current/` (`P6 wf:2921`, `:2984-3009`). The REJECTED-marker gate uses the same rule, which
  names `current/` in every leftover-2.5.1-marker case, as today. The new M1c covers the
  completed withdrawal and runs the gate's steps with the real 2.6.0 generator. M1b now says
  that its marker is planted after a real `withdraw_bundle`, because only an interrupted
  withdrawal leaves one. The new acceptance criterion 11 and requirement R16 state it.
- **LP-R2-004 (Optional), accepted, as an N4 cell.** Design D gains N4 (rows 1, 2, 3 and 4a-4c,
  or an echoed phase other than the one read, at a non-ready phase → the
  `unexpected_plan_review_status` gate), mirroring S4. The parser stays generic. CP4 asserts
  twelve non-ready cells, and the 2.6.0 golden adds one unexpected status per non-ready phase.
- **LP-R2-005 (Optional), accepted.** `_functional_review_consumed_blob` is called only from
  `_capture_pre_state` (`job.py:347`), which runs before `decide` both for the step (`:4323`)
  and for a preflight gate's record (`:4143`). It is removed from Design C's verification
  readers and named as decision-time. So that the pre-state capture is also covered, the release
  re-check moves to immediately after the preflight returns, before a preflight gate is
  recorded. This applies to I3, Design C and CP3's test.
- **LP-R2-006 (Optional), accepted.** `WorkflowReleaseChangedError`'s evidence gains
  `preflight_action` (`Proceed.action`, `milestone_branch.py:193-203`, or `gate`). Its message
  states that a non-`none` action completed and was recorded. M3 asserts `closed_out`, and the
  troubleshooting entry says the close-out succeeded.

The registry is regenerated at revision 3 with the same six checkpoints, the same dependencies
and the same sizes. The names of CP2-CP5 are updated to match the changes above. In the mapping,
R14's description now names the private copy. It gains R15 (verification-time query failures
and release changes record `FAILED` with no pending job, owned by CP3 and CP4) and R16 (the S2
gate's steps succeed for a 2.5.1-created item whose author files are absent, owned by CP4 and
CP5). No other requirement changed.

### Revision 4 (manual external plan review, round 3, `REVISE`)

All six findings (three Important, three Optional; no Blocking) were validated against the
repository and the 2.6.0 payload (Workflow Manager `distribution/workflow/2.6.0`, unchanged since
`136c417`). All were accepted, and none is rejected.

- **EXT-R3-I1 (Important), accepted, the preferred resolution.** Confirmed: row 4a's remedy is
  `f"{restore}; or {withdraw} and take the normal path"` (`P6 ws:15632-15647`), and
  `_plan_review_remedy_withdraw` says the withdrawal "discards both recorded stages"
  (`:15076-15077`). Revision 3's "quoted verbatim as the `safe_resume_command`" and "never the
  `safe_resume_command`" therefore contradicted each other. Row 4a's `safe_resume_command` is
  now a Controller-authored, restore-only step keyed on the row, and Workflow's complete remedy
  and `detail` stay in `what_is_required`. Two facts, both measured, shape the step:
  - `current/files/` is not a safe blanket source. This item's own bundle holds
    `WORKFLOW_STATE.json`, the artifacts file and five context files there beside the three
    protected paths, so the step restores only the paths `MANIFEST.md` lists under
    `## Protected paths`;
  - the generator copies only paths changed from `base_commit`, and leaves an unchanged
    protected path out of `files/` (`P6 prepare-ai-review.sh:468-519`), so such a path is
    restored from `git show <base_commit>:<path>`.

  With no `current/MANIFEST.md` there is no restore source, and the step is the `explain`
  command. (Revision 6 corrects that command's syntax, EXT-R5-I2.) If `current/` no longer
  holds the bound bytes, row 4a and the same gate recur.
  (Revision 4 also said here that following the step never discards anything. That was false,
  because the restore overwrites uncommitted post-binding edits; revision 5 corrects it,
  LP-R4-001.) Design D, Decision 9, the troubleshooting entry, CP4's
  exit and acceptance criterion 12 state it, and requirement R17 records it. (Revision 7
  replaces the restore step with a no-restore gate, EXT-R6-I1/-I2.)
- **EXT-R3-I2 (Important), accepted, one reason family.** Confirmed: `_read_manifest`
  (`managed_repo.py:145-187`) raises `UnmanagedRepositoryError` for a missing manifest and
  `MalformedInstallationManifestError` for an unreadable, non-JSON, non-object or schema-invalid
  one, and neither is a `WorkflowQueryError`. Revision 3's catch would have let either escape
  after the `COMPLETED` flush. The verification re-check in `_row_clauses_failure` now catches
  both, and returns `workflow_release_changed` with `installed: null` and a `manifest_error`
  (`code`, `message`) in the evidence. The three callers then record `FAILED` exactly as for a
  mismatch, so no new branch is needed in `_reconcile_launched`. The decision-time re-check
  normalises the same two errors into `WorkflowReleaseChangedError`, so that a completed
  preflight action is still named when the manifest is damaged. I2, I3, Design C, CP3's tests
  (missing, non-JSON and schema-invalid manifests, at launch and at `resume` from `COMPLETED`
  and `LAUNCHED`), acceptance criterion 10 and requirement R15 are extended.
- **EXT-R3-I3 (Important), accepted, extend the branch.** Confirmed: `_rejected_marker_gate`'s
  plan-stage branch tests `PLAN_BUNDLE_CONSUMING_PHASES` (`evidence.py:2548`), so a marker at
  `AWAITING_PLAN_APPROVAL` reaches the last branch, whose `safe_resume_command` is the bare
  generator (`:2572-2578`). The state is not impossible under 2.5.1, since its generator has no
  phase gate (`scripts/prepare-ai-review.sh`). Under the query contract, for `"2.1"`/`"2.2"`
  items, the plan-stage branch now covers all three ready phases. A 2.5.1 target and a `"1"`
  item keep today's branch (I1). The new variant M1d builds the approval-phase residue with the
  real 2.5.1 functions (`record_local_plan_review`, `record_manual_plan_review`,
  `withdraw_bundle`, then the planted marker). It proves that the bare generator fails, and that
  the gate's steps succeed with the real 2.6.0 generator. After them the status is row 3, the
  real `assert_plan_review_bundle_bound` (`/approve-review plan` step 2's check) accepts the
  item, and the Controller offers the unchanged approval gate. CP4 asserts the branch at each
  ready phase. Acceptance criterion 11 and requirement R16 are extended.
- **EXT-R3-O1 (Optional), accepted, both halves.** Confirmed: the re-check runs before
  `_branch_gate_record`, so a gate is never recorded, and `_after_close`
  (`milestone_branch.py:968-975`) returns `_on_trunk`'s gate or action unchanged, while a
  trunk-side close-out (`_close_trunk_step3`) returns nothing of its own. The evidence gains
  `preflight_gate` and `preflight_events`, and the message distinguishes a completed action from
  a returned gate that was not recorded, and names any completed close-out. `preflight_events`
  comes from `milestone_branch._event`, the one event writer, through a new
  `Context.events` list (default `None`). That covers both close-out sides with no new file
  read, so I1's "only a plain read of `installation.json`" still holds. `Gate` and `Proceed`
  are unchanged. CP3 tests the gate case and a close-out followed by another outcome, and M3
  asserts `closed` in `preflight_events`.
- **EXT-R3-O2 (Optional), accepted.** Confirmed: row 4c's answer has no
  `fresh_review_content_id` (`P6 ws:15616-15621`), and row 4a's is `None` when the fresh id is
  unreadable (`:15646-15647`). Design B's parser treats the per-row keys as optional, and S2 adds
  the key to the evidence only when it has a non-null value. CP4 builds the row-4c gate from the
  real script without the key.
- **EXT-R3-O3 (Optional), accepted, explicit threading.** Design C, "How the contract reaches
  the clauses", states the choice:
  - `_row_clauses_failure` binds the recorded release's contract once per verification and
    passes the `BoundContract` to every clause as one added positional argument
    (`PredicateFn`/`PredicateDetailFn`/`PostconditionFn`, `job.py:446-460`);
  - `expected_outcomes_for(release)` substitutes writer declarations only and binds nothing.

  A contract bound into the table would put a per-verification memo inside a table that is
  also used for `AUTOMATIC_TRIPLES` and the property checks. About forty direct clause calls in
  four test modules gain the 2.5.1 binding argument, and CP3 names those modules.

The registry is regenerated at revision 4 with the same six checkpoints, the same dependencies
and the same sizes. The names of CP3-CP5 are updated to match the changes above. In the
mapping, R15 now includes an installed release that cannot be read, R16 now includes the
REJECTED-marker gate at all three ready phases, and R17 is new: no `safe_resume_command` names
Workflow's withdrawal, and row 4a's is a restore-only step, owned by CP4. No other requirement
changed.

### Revision 5 (local-model plan review, round 4, `REVISE`)

All three findings (one Important, two Optional; no Blocking) were validated against the
repository and the 2.6.0 payload (Workflow Manager `distribution/workflow/2.6.0`, unchanged since
`136c417`). All were accepted, and none is rejected.

- **LP-R4-001 (Important), accepted, preservation rather than text-only advice.** Confirmed:
  - row 4a fires exactly when `fresh is None or fresh != bound_id` (`P6 ws:15630-15647`);
  - this item's three plan-stage protected paths are index-only `A` entries (`git status
    --short`), so a post-binding edit exists only in the working tree;
  - 2.6.0's `/record-manual-plan-review` calls `assert_plan_review_bundle_bound`
    (`P6 record-manual-plan-review.md:88-100`), which raises `ReviewedContentDriftError` for
    row 4a (`P6 ws:15715-15745`). So an operator who starts editing at
    `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` has to restore before recording the verdict.

  Revision 4's restore-only step therefore destroyed those edits, while the plan said twice that
  it could not. Row 4a's step is now preserve-then-restore. Its step 0 copies every path of the
  restore set that exists in the working tree to `.ai-review/<id>/drifted-<UTC timestamp>/<path>`.
  That location is outside `current/`, so `bundle_id` is unaffected. It is gitignored
  (`.gitignore:8`), so no classification sees it. Its prefix is none of those Workflow's
  leftover-staging cleanup removes (`P6 wf:2898`, `:2953-2971`). The operator fills in the
  timestamp, as the 4b/4c steps already have them fill in the HEAD, so a second follow never
  overwrites the first copy. `what_is_required` says in one place what the restore replaces and
  where it is kept. The "cannot discard anything" claim is removed from Design D, and revision 4's
  entry above is annotated. Decision 9, the troubleshooting entry, CP4's row-4a test (the
  preserved copy equals the pre-restore edit, and the status returns to `BOUND`), acceptance
  criterion 12 and R17 are updated. The Migration / data-integrity notes gain an operator-work
  line. The step stays advice, as every `safe_resume_command` is. The Controller performs none of
  it, so "the Controller writes nothing new to targets" still holds. (Revision 7 removes the
  step; the gate now warns that a restore overwrites these edits, EXT-R6-I1/-I2.)
- **LP-R4-002 (Optional), accepted, the first option: the declarations file joins the restore
  set.** Confirmed:
  - `compute_review_content_id_plan_stage` hashes `protected_paths`, `excluded_paths` and
    `excluded_prefixes` into the projection (`P6 wf:1567-1614`);
  - `resolve_plan_stage_metadata` loads them from `artifacts_path_for_work_item(<id>)`
    (`P6 wf:901-908`, `:1001-1034`), which is `docs/ai-workflow/registry/<id>-artifacts.json`;
  - this item's `current/files/` holds that file.

  Naming the cause without restoring it would leave the same gate recurring with no
  Controller-authored way out, so the file is restored like the protected paths, from `files/`
  or `git show <base_commit>:<path>`, and preserved first. It is the projection's only other
  working-tree input. Row 4a's `null` fresh id comes only from an absent protected path or a
  `(Revision N)` title mismatch (`compute_fresh_plan_review_content_id`, `P6 ws:15476-15488`), and
  the restore repairs both. CP4 adds a second row-4a case, a classification-only edit to
  `plan_stage.excluded_paths`, which the step clears.
- **LP-R4-003 (Optional), accepted, both halves.**
  - Confirmed: CLI `resume` calls `_inspect_target` first (`cli.py:1079`, `:569-574`), and
    `managed_repo.inspect` reads the manifest through `_read_manifest` before it runs the
    Manager. CP3 now states that every `resume` case calls `job.resume` with a
    `ManagedRepository` inspected before the failure is arranged, and that the manifest is removed
    or damaged after that `inspect`. A new CLI-level case pins exit 20 at `inspect`, the record
    byte-unchanged and pending, then normal reconciliation after the manifest is restored. I2,
    I3, Design C and acceptance criterion 10 are scoped to "whenever verification runs", and
    Design C says why the job correctly stays pending at the CLI.
  - Confirmed: `runtime.write_bytes` goes through `_atomic_write` (`runtime.py:131-176`), which
    can raise `OSError` or `RuntimeContainmentError`, and neither is a `WorkflowQueryError`.
    Design B's runner now maps every failure of steps 1-3: directory creation, the private
    writes and the directory's removal (`query_private_copy_failed`), as well as step 1's and
    step 3's existing reasons. So nothing but a `WorkflowQueryError` leaves it. CP2 injects
    each step-2 failure by patching. An unwritable `TMPDIR` alone cannot trigger one:
    `tempfile.gettempdir()` fell back to `/tmp` when `TMPDIR` named a mode-500 directory
    (measured). CP3 adds a verification-time private-copy failure at launch and at `resume`,
    which ends `FAILED` with `workflow_query_failed` and no pending job. The troubleshooting
    entry names the new reason.

The registry is regenerated at revision 5 with the same six checkpoints, the same dependencies
and the same sizes. The names of CP2 (every runner failure a `WorkflowQueryError`) and CP4 (the
preserve-then-restore row-4a step) are updated. In the mapping, R15 now includes a failed private
copy and is scoped to verification that runs, with the CLI `resume` refusal at `inspect`. R17
now requires row 4a's step to preserve the post-binding edits of its restore set (the protected
paths and the declarations file) before restoring. No other requirement changed.

### Revision 6 (manual external plan review, round 5, `REVISE`)

Both findings (two Important; no Blocking, no Optional) were validated against the repository and
the 2.6.0 payload (Workflow Manager `distribution/workflow/2.6.0`, unchanged since `136c417`).
Both were accepted, and none is rejected.

- **EXT-R5-I1 (Important), accepted: a filesystem-state model, with a no-restore gate for the
  shapes it cannot restore.** Confirmed:
  - Workflow hashes existence, Git mode and blob per protected path. A symlink is hashed as
    `120000` and its target string, and the executable bit counts when `core.fileMode` is true
    (`_snapshot_worktree`, `P6 wf:1498-1516`);
  - revision 5's step 0 copied only paths that exist, and so recorded no deletion. Its restore,
    by `cp` or `git show`, keeps the destination's mode, so a mode-only drift recurs. A plain
    copy follows a link at the source, and a plain write follows one at the destination.

  Three further facts, read from the payload, shape the fix:
  - the bound state is always a regular file, because the 2.6.0 pin refuses an absent,
    symlinked or non-regular protected path before anything is written
    (`capture_plan_stage_pin`, `P6 wf:2478-2533`). So a symlink never has to be restored, only
    removed;
  - the bound mode is recoverable. A `files/` copy carries the generation-time owner-execute
    bit (the generator's `cp`, `P6 prepare-ai-review.sh:497`), and `bundle_id` hashes it
    (`P6 wf:3618`). A path with no copy was unchanged from `base_commit`, mode included, so
    `git ls-tree` gives its mode;
  - `resolve_plan_stage_metadata` validates the plan document, registry and mapping as tracked
    regular files with no link at any component (`_validate_plan_stage_metadata_path`,
    `P6 wf:872-897`). It also requires the declarations file to be a regular file that is not a
    link (`:739-747`). Deleting any of the four, or replacing one with a link, therefore makes the
    query fail (exit 1, S5's refusal). Only a further declared protected path reaches row 4a by
    being deleted or replaced with a link.

  The row-4a and S5 shapes above were measured with the real 2.6.0
  `compute_fresh_plan_review_content_id` on a disposable copy of this repository, and so were the
  `cp` and `rm` behaviours (`TEST_RESULTS.md`, revision 6).

  Design D now defines row 4a's continuation over existence, mode and bytes:
  - the Controller reads each restore-set path's shape with `lstat` on the path and on every
    parent component inside the repository, following no link;
  - only `file` (a regular file reached through real directories) and `absent` are
    restorable. Every path also needs a source;
  - step 0 writes `STATE.json`, each path's existence and mode with deletions included, and
    copies each present file with its mode (`cp -P -p`);
  - step 1 removes the path (`rm -f`, which never touches a link's target), writes the source
    bytes and sets the owner-execute bit to the source's mode.

  Any other shape (a link at the path or at a parent, a non-regular file, an `lstat` failure, a
  path with no source) and a missing `current/MANIFEST.md` get a deliberate no-restore gate,
  which changes nothing. Decision 9, the troubleshooting entry, CP4's tests, acceptance
  criterion 12, the Migration / data-integrity notes and R17 are updated. CP4 now follows the
  step with real 2.6.0 bundles in four cases, adding a mode-only drift and a deletion to the
  two existing ones. It drives the no-restore gate with a real symlinked fourth protected path,
  and it drives the remaining shapes through the answers seam. It also shows that deleting the
  plan document, or replacing it with a link, is S5's refusal with nothing written. (Revision 7
  keeps only the no-restore gate, for every row-4a drift, EXT-R6-I1/-I2.)
- **EXT-R5-I2 (Important), accepted: a valid command, without changing the existing helper.**
  Confirmed: `_explain_command` (`evidence.py:1484-1485`) renders
  `workflow-controller explain --work-item <id>`, and `cli.build_parser()` rejects it with exit
  2 ("unrecognized arguments: --work-item"; `cli.py:239-273`). The form
  `workflow-controller --work-item <id> explain <repository>` parses. The known follow-up
  (`docs/ROADMAP.md:81-84`) stays where it is. Every gate that uses the helper today is
  reachable at 2.5.1, so changing it would break I1. Decision 12 (new) instead gives the gates
  this plan adds a new helper, in the form `milestone_branch.py:519` already uses:
  - row 4a's no-restore gate;
  - `plan_review_binding_inconsistent` (S3/N2);
  - `unexpected_plan_review_status` (S4/N4).

  Design D states the command for each. CP4 parses the exact rendered text with
  `cli.build_parser()` and runs it through `cli.main` (exit 0, the same gate).

The registry is regenerated at revision 6 with the same six checkpoints, the same dependencies
and the same sizes. CP4's name is updated: its row-4a step preserves existence, mode and bytes,
and a no-restore gate with a valid `explain` command covers the drift the step cannot restore. In
the mapping, R17 now requires that model, and that no-restore gate and command. No other
requirement changed.

### Revision 7 (manual external plan review, round 6, `REVISE`)

Both findings (two Important; no Blocking, no Optional) were validated against the repository and
the 2.6.0 payload (Workflow Manager `distribution/workflow/2.6.0`, unchanged since `136c417`).
Both were accepted, and none is rejected. Both are resolved by the alternative the review names
for each: row 4a offers no mutating continuation at all. This follows the review's architecture
concern, which says the step has outgrown a static shell string. It also avoids the other option,
a Controller command that writes to target working trees, which this minimal plan does not add.

- **EXT-R6-I1 (Important), accepted: no restore, because the source cannot be established.**
  Confirmed:
  - `plan_review_publication_status` returns row 4a as soon as `fresh is None or fresh !=
    bound_id`. It runs `verify_plan_review_bundle` only to keep the result under the private key
    `_error` (`P6 ws:15630-15647`). `_plan_review_publication_status_cli` prints only the keys
    without a leading `_` (`:17270-17276`);
  - the verifier is what compares the manifest's, `current/`'s and the archive's `bundle_id`
    (`:15535-15548`);
  - `prepare-ai-review.sh` writes the `files/` copies from the diff (`P6 :480-518`), and
    `refresh_files_copy_from_pin` then rewrites only the bytes of copies that exist
    (`P6 wf:2535-2547`). Nothing guards a copy after binding.

  So revision 6's source rule could restore the base version for a changed path whose copy was
  deleted, and unverified bytes or mode for a copy that was modified. The only way the Controller
  could tell these cases apart is to re-implement Workflow's bundle verification.
- **EXT-R6-I2 (Important), accepted: no restore, because the path cannot be held.** Confirmed:
  `cmd_explain` is read-only (`cli.py:697-702`) and only prints the command (`:772-777`), so the
  `lstat` checks made while the gate was built hold nothing when the operator runs the step. A
  `cp -P` or `rm -f` keeps its no-follow behaviour only at the final component. Git also follows
  a linked parent: `git hash-object -- d/f`, with `d` a link to a directory outside the
  repository, hashes the outside file (measured). Checking each component at execution time
  needs a Controller command that writes to the target.

Design D's row 4a is now a no-restore diagnostic gate. Its `safe_resume_command` is revision 6's
parser-valid `workflow-controller --work-item <id> explain <repository>`. Its `what_is_required`
is built from the answer and the item id alone, so no file of the target is read for it. It
quotes the remedy and `detail`, and says why no restore is offered. It names what the bound state
covers, the declarations file included (revision 5's LP-R4-002, now a statement instead of a
restore). It warns that a restore overwrites post-binding edits (LP-R4-001, likewise), and says a
human decides. Revision 6's restore set, source rule, observed shapes, preserve step and
`drifted-<UTC timestamp>/` directory are removed. Decision 9, the troubleshooting entry,
acceptance criterion 12, the Migration / data-integrity notes and R17 are updated.

CP4 now covers the review's missing tests, with the real 2.6.0 query and the exact advertised
command executed through `cli.main`:
- a changed-since-base plan document drifted, with its `current/files/` copy deleted,
  byte-modified or mode-modified;
- a parent directory replaced by a link after the gate is rendered.

In each case, and in revision 6's byte, declarations, mode-only, deletion and symbolic-link cases,
an `lstat` snapshot of the working tree, `.ai-review/<id>/` and every link target and outside
directory is unchanged. For the parent-link case the real query may instead fail on the
unclassified link (`assert_all_changed_paths_classified_worktree`, `P6 wf:1598-1600`), and the test
pins the measured outcome. Revision 6's parser-and-execution test of the `explain` command is
kept, and CP4 adds a test that the gate is byte-identical with `current/` present or removed.

The registry is regenerated at revision 7 with the same six checkpoints, the same dependencies
and the same sizes. CP4's name is updated: row 4a gets a no-restore gate whose `explain` command
changes nothing. In the mapping, R17 now requires that gate and its non-mutation tests. No other
requirement changed.
