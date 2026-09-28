# ADR 0006: Workflow release admission and per-release contracts

Status: accepted. See
`docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md` for the full
design record (work item `workflow-controller-workflow-2-6-integration`,
`docs/ROADMAP.md` section 1.6). It shipped as Controller 1.3.0. This document
records how a Workflow release is admitted, what the Controller consumes from
each admitted release, and the answers to the trunk plan's open questions
E1-E5. It adds no exit code: the new error codes `WORKFLOW_QUERY_FAILED` and
`WORKFLOW_RELEASE_CHANGED` both exit `20`, and the table in
[ADR 0001](0001-controller-generation-1-architecture.md) stays the normative
exit-code contract, unchanged.

It widens one statement of two earlier ADRs, and each carries a one-line
pointer here: [ADR 0001](0001-controller-generation-1-architecture.md)'s
"frozen v2.5.1", and [ADR 0003](0003-trunk-branch-pr-release-orchestration.md)'s
"drift is detected, never integrated, under Workflow 2.5.1", which now holds
for 2.5.1 and 2.6.0 alike.

## Context

Through 1.2.1 the Controller admitted exactly one Workflow release, 2.5.1.
Workflow 2.6.0 was then released by Workflow Manager. Measured against the
released payload (Workflow Manager commit `136c417`), 2.6.0 keeps the phase
set, the state schema, the governing versions, the seventeen command files
and the user-only set, the bundle generator's argv and the implementation-stage
provenance rules. It changes three things the Controller consumed:

- **Feedback storage.** An item created under 2.6.0 is stamped
  `feedback_layout: "scoped"`, and its feedback lives in
  `.ai-review/<id>/feedback/` whether or not that directory exists yet. The
  Controller's own rule ("the scoped directory if it exists, else the flat
  one") answered the flat path for a freshly stamped item, so it was wrong
  for 2.6.0 by construction, not merely duplicated.
- **Plan-review publication.** For `"2.1"`/`"2.2"` items, `publish_plan_revision`
  no longer moves the phase; a new `bind_plan_review_bundle` is the only
  writer of `AWAITING_LOCAL_PLAN_REVIEW`, and the old transition writer is
  retired. Measured against the Controller's own declared writers, property 5
  went from no violation on 2.5.1 to 14 on 2.6.0.
- **Two read-only queries** documented as a Controller contract:
  `scripts/workflow_fingerprint.py --resolve-feedback-path <id>` and
  `scripts/workflow_state.py --plan-review-publication-status <id>`.

The Controller's tests also read this repository's own installed Workflow as
"the" Workflow, so updating this repository's installation would have moved
every Workflow-derived test to the new release at once.

## Decisions

### Admission by exact release

- A target is admitted only when Workflow Manager verifies its installation
  and its `.workflow-manager/installation.json` names an exact member of
  `controller.managed_repo.VALIDATED_WORKFLOW_RELEASES`, now `{2.5.1, 2.6.0}`.
  `SUPPORTED_WORKFLOW_LINES` (`{2.5, 2.6}`) is only a pre-filter that lets a
  refusal say "outside the supported lines" rather than "not validated"
  (`UNSUPPORTED_WORKFLOW_VERSION`, reasons `outside_supported_line` and
  `unvalidated_release`). So 2.6.1 and 2.5.0 are refused as unvalidated, and
  2.7.0 as outside the lines.
- The refusal evidence key `supported_workflow_line` (a string) became
  `supported_workflow_lines` (a sorted list). Keeping the old key with a
  made-up single value would have been false.
- `REFERENCE_WORKFLOW_RELEASE` stays `2.5.1`: the fixture default and the
  release of the unchanged baseline goldens. The derivation pin and the
  admission gate are separate mechanisms.
- Growing the set is a deliberate act in a reviewed plan, never automatic
  from a matching major/minor: vendor the release, re-run the per-release
  suites against it, give it a contract, then add it by name.

### One contract per release

- `controller.workflow_contract.RELEASE_CONTRACTS` holds one entry per
  measured release, and every admitted release has one (a test pins it). A
  contract names where two facts come from:

  | Release | Feedback path | Plan-review bundle current? | Query script digests |
  |---|---|---|---|
  | 2.5.1 | the Controller's own rule, unchanged | revision coherence, unchanged | none |
  | 2.6.0 | `--resolve-feedback-path` | `--plan-review-publication-status` | sha256 of `workflow_state.py` and `workflow_fingerprint.py` |

- The contract is chosen from the release `inspect` admitted and bound once
  per decision, per pre-state capture and per verification. It is passed
  explicitly; no call site can fall back to the 2.5.1 rule by omission.
- **The installed release is re-checked before every decision and pinned per
  job.** Right after the repository preflight (which can switch to a trunk
  on another release at close-out), and before any gate is recorded or any
  decision is made, the installed release must still be the admitted one.
  Otherwise the step refuses with `WORKFLOW_RELEASE_CHANGED`, whose evidence
  says what the preflight itself already did. A job verifies, and `resume`
  reconciles, under the release its record carries
  (`target_workflow_version`). A release that changed, or that cannot be
  established because the manifest is missing or unreadable, makes the job
  a terminal `FAILED` with reason `workflow_release_changed`.

### Workflow's queries are the single authority for 2.6 and later

- For a 2.6.0 target, the feedback path and the plan-review publication
  status come from Workflow's queries only. The Controller keeps no second
  rule, never repairs an answer, and never reads `plan_review_binding`
  itself; it reads the status query's `status`, `row`, `remedy`, `detail`
  and `advisory`. A query that fails is never answered by the 2.5.1 rule: at
  decision time it is a refusal (`WORKFLOW_QUERY_FAILED`, exit `20`), and
  during verification it is a terminal `FAILED` job with reason
  `workflow_query_failed`, on the launch and the `resume` path alike, with
  no pending job left behind.
- **A query executes only the admitted release's bytes.** The Controller
  reads `scripts/workflow_state.py` and `scripts/workflow_fingerprint.py`
  from the target once each, checks their sha256 against the contract (a
  mismatch executes nothing: `query_script_modified`), writes exactly those
  bytes into a private temporary directory it owns, and runs
  `sys.executable -B -E -s <private dir>/<script>` there, with `cwd` the
  target, stdin closed and a timeout. The target's `scripts/` directory is
  never on the query's `sys.path`, so a planted `scripts/uuid.py` or `.pyc`
  never runs. The digests live in the Controller, not in the target's
  manifest, which a modified target could rewrite together with the scripts.
- **The query's Git is isolated too.** The admitted scripts run
  `git hash-object`, `git diff --name-only` and `git ls-files` in the
  target, and Git runs what the target's configuration names: a clean filter
  a `filter=` attribute selects, the hooks `git diff`'s index refresh fires,
  an fsmonitor. The implementation review reproduced a clean filter running
  during the status query. So every Git command of a query inherits an
  environment with a private copy of the target's index (the target is never
  written, `.git` included), no transport (`GIT_ALLOW_PROTOCOL` empty), and
  command-scope settings (`GIT_CONFIG_COUNT`, which override every file):
  `core.hooksPath` is `/dev/null`, fsmonitor, split-index writes and
  signature checks are off, every filter driver any configuration file
  defines has empty `clean`/`smudge`/`process`, and every configured hook is
  disabled. The Controller reads the configuration back and requires each
  setting to be in force. What it cannot switch off refuses the query before
  it runs (`query_git_not_isolated`): a `hook.<name>.command` in the
  target's own configuration (a Git that runs configured hooks may not honour
  disabling them), a populated submodule (its Git reads its own
  configuration), or a Git that does not apply the settings (before 2.31).
  Diff drivers, signature programs and transports stay configured but idle:
  the 2.6.0 queries never ask for a patch, a log, a checkout or a fetch, and
  admitting a later release re-checks that.
- The plan stage has one outcome per (phase class, status class): a bound
  bundle lets the phase's handler act; a drifted, unverified or legacy
  bundle stops at the stale-plan-bundle gate; a refusal or an answer outside
  the contract stops at a named gate (`plan_review_binding_inconsistent`,
  `unexpected_plan_review_status`); the non-ready rows dispatch as before.
  Row 9 (`PUBLISHED_UNBOUND`) is recovered by re-running the same explicit-id
  command, which is exactly Workflow's documented remedy.
- **No `safe_resume_command` names Workflow's withdrawal**, and none the
  2.6.0 contract adds changes the working tree. At row 4a (the working tree
  no longer matches the bound content) the Controller offers no restore:
  it cannot establish that the bundle's `current/files/` still holds the
  bound state, nor keep a path from changing between a printed step and its
  execution. That gate's command is a valid `explain`, and a human decides.
- **No automatic `/milestone-plan` at a plan-review-ready phase.** Under 2.6.0
  it withdraws the item, discarding both review stages. No decision selects
  it there (pinned by a test).
- Where a 2.6.0 command writes differently, the declaration is per release:
  `controller.job.expected_outcomes_for(release)` declares 2.6.0's bind
  writer for the six `/milestone-plan` and `/apply-plan-review` rows 2.6.0
  measurably changed, so property 5 is clean for both releases. The
  plan-stage postcondition under the 2.6.0 contract is Workflow's own
  status: the item at `AWAITING_LOCAL_PLAN_REVIEW` with its bundle `BOUND`.

### Tests run against vendored release trees

- The command files and the three scripts of every admitted release are
  vendored under `tests/workflow_releases/<release>/`, hash-pinned to the
  Workflow Manager manifest in `RELEASE.json`, and maintained only by
  `tools/workflow_releases.py` (`sync`, `check`). About 2.8 MB, because CI
  reaches neither Workflow Manager nor its distribution tree.
- Every Workflow-derived inventory (the phase set, the command partition,
  the user-only set, property 5) and every decision golden runs for each
  admitted release. No test depends on the release this repository has
  installed; that tree is only checked for equality with the vendored tree
  of the release it declares.
- The 2.5.1 behaviour is pinned byte-for-byte: the existing plan-stage,
  external-implementation-review and no-policy lifecycle goldens are
  unchanged. The 2.6.0 decision goldens replay Workflow's recorded answers
  through a test-only seam, so every scenario runs against every feedback
  layout and status class; real-script coverage lives in the query, gate
  and migration tests.
- The 2.5.1 -> 2.6.0 migration is proven in CI on disposable repositories
  with the real scripts of both releases: bound milestones updated in place
  at the plan-review phases, at plan approval and at `IMPLEMENTING`; a
  trunk updated under a milestone branch that stays on 2.5.1; a new
  `scoped` item; and a job straddling the update. A local-only variant runs
  the real Workflow Manager.

### E1-E5: the released contract has no integration transition

Measured from the released 2.6.0:

| | Question | Answer |
|---|---|---|
| E1 | Does Workflow record a work item's branch or integration base? | No. Only `base_commit` (and `amendment_base_commit` after an amendment request). |
| E2 | Is there a legal transition that moves a work item's base? | No. `base_commit` is an immutable declaration fact, hashed into both stages' `review_content_id`. |
| E3 | Do provenance intervals accept a history-preserving integration merge? | No. Any commit with more than one parent is refused, unchanged from 2.5.1. |
| E4 | In which phases is integration legal? | None. |
| E5 | How do the state file and the narrative merge across two milestone branches? | Not addressed: still one state file with one active item, and no merge driver. |

So `gitrepo.merge_trunk` stays unwired. The `integration_required` readiness
gate and the documented manual merge ("Create a merge commit") remain the
contract for 2.5.1 and 2.6.0 targets, and the Controller grows no competing
base or review model. The narrow Workflow follow-up, for Workflow Manager to
plan with the Workflow 2.7 protocol release (ROADMAP 1.7), has five parts:

1. a durable per-work-item integration record: branch and integration base
   (E1);
2. one sanctioned transition that, given a history-preserving merge of the
   trunk into the milestone branch, moves the item's base to the merge's
   trunk parent, stales exactly the approvals whose `review_content_id`
   moved, and re-enters implementation review (E2);
3. provenance-interval acceptance of exactly that merge commit, identified by
   a trailer the transition writes (E3);
4. a stated set of legal phases, each of which re-enters review (E4);
5. a state model in which two items' branches merge without conflict (E5).

When such a release exists, a later Controller milestone binds `merge_trunk`
to it.

### Targets move between releases between milestones

- Workflow Manager's `update` replaces the managed files and rewrites the
  installation manifest; it commits nothing, reads no Workflow state and
  migrates nothing. 2.6.0's own scripts handle 2.5.1-created items through
  their legacy branches. So the Controller migrates nothing either: its job
  records already carry `target_workflow_version`.
- Updating a target is best done between milestones. In flight, the
  migration is proven at the plan-review phases, at plan approval and at
  `IMPLEMENTING` between checkpoints; it is unsupported with a plan-approval
  journal in flight, and a job running across the update fails closed. The
  guides say how ([Installing](../guide/installation.md#moving-a-target-to-another-workflow-release)).
  The Controller does not itself refuse an update at an unsafe phase; that
  would be a Workflow Manager preflight.
- This repository's own installation moves to 2.6.0 in a separate change
  after the 1.3.0 release, not inside the milestone that admitted it: the
  Controller driving that milestone was the installed 1.2.1, which refuses
  2.6.0, and an update under an in-flight item would have replaced the
  commands its plan was approved against. The update is a Manager-output-only
  change with no version bump, and it needs no Controller code change,
  because no test reads this repository's installed release.

## Alternatives rejected

- **Keeping the Controller's own feedback and staleness rules for 2.6.0.**
  The feedback rule is wrong for stamped items by construction, and the
  staleness rule cannot see 2.6.0's binding record without interpreting it,
  which would make the Controller a second authority on a Workflow fact.
- **Falling back to the 2.5.1 rule when a query fails.** A fallback answers a
  different question than the one Workflow was asked, silently. Failing
  closed is the Controller's rule everywhere else.
- **Running the query scripts in place, or with `-P`.** In place, the
  target's `scripts/` directory comes first on `sys.path`, so a planted
  module shadowing the standard library runs with both digests matching.
  `-P` breaks 2.6.0's sibling import. A launcher that appends the target's
  `scripts/` to `sys.path` was also measured and rejected: the interpreter
  re-reads the files after the Controller's check.
- **Admitting by line (`2.6.x`).** A patch release can change any of the
  facts above; admission stays by exact, measured release.
- **Updating this repository's installation inside the milestone.** Rejected
  for the reasons under "Targets move between releases between milestones".
- **Generating real bundles for every 2.6.0 golden case.** Synthetic
  manifests never verify under 2.6.0, so every ready-phase case would have
  collapsed to one gate; recorded answers pin the full decision table
  instead.

## Consequences

- A 2.5.1 target behaves exactly as under 1.2.1. The only new work is a
  plain read of the installation manifest before each decision and each
  verification.
- A 2.6.0 target costs one query process per fact per decision: about
  43 ms for the feedback path and 105 ms for the publication status,
  measured.
- Admitting a future release is a vendoring, measuring and contract step,
  described in [Development](../guide/development.md#workflow-release-trees).
- Rolling back to 1.2.1 restores 2.5.1-only admission; a target already on
  2.6.0 is then refused, fail-closed.
- Integration of a moved trunk stays manual until Workflow ships the
  follow-up above.
