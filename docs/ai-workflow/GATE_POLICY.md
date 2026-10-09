# Gate Policy and Reopening

The operator guide for Workflow 2.8.0's gate policy: which approvals the
Workflow satisfies from evidence, which stay with a person, how to change
that, and how a completed or nearly completed item is reopened when its pull
request turns red. The normative text for the orchestrator side is
`ORCHESTRATION_PROTOCOL.md` (protocol `1.1`); the states and commands are
`MILESTONE_WORKFLOW.md` and `WORKFLOW_V2_1_OPERATOR_REFERENCE.md`.

**The default is different, on purpose.** Updating a repository to 2.8.0
switches its gates to automatic unless it turns human approval on. With no
`docs/ai-workflow/GATE_POLICY.json` the plan approval, the technical approval
and the milestone acceptance are each satisfied by the Workflow from their
evidence, and a gate whose evidence is not there is `blocked` with the unmet
requirement named, never passed. The update changes no state file by itself.

**The one-line way back to human gates:**

```json
{"schema_version": 1, "human_approval": true}
```

in `docs/ai-workflow/GATE_POLICY.json`. Every gate is then a person's, exactly
as in 2.7.0 (`/approve-review`, `/accept-milestone`), and `next-action`
decisions equal 2.7.0's apart from the version fields and the deltas listed in
the compatibility notes of `ORCHESTRATION_PROTOCOL.md`. **Turning human
approval on is the stronger mode** (see "Trust boundary").

## The policy file

`GATE_POLICY.json` is repository-local state like `WORKFLOW_CONFIG.json`:
never shipped, never overwritten by an update. It is a closed schema (an
unknown key is refused) and every key is optional:

```json
{
  "schema_version": 1,
  "human_approval": false,
  "gates": {
    "plan_approval":      {"human": true, "require": ["distinct_reviewer_models"]},
    "technical_approval": {"require": ["distinct_reviewer_models"]},
    "acceptance":         {"human": false, "require_ci": true,
                           "requires_pr_approved": false,
                           "required_flows": ["migration-suite", "e2e-suite"]}
  },
  "pr_review": {"enabled": true, "reopen_on": ["changes_requested", "checks_failed"]}
}
```

- `human_approval` (default `false`) turns all three gates human when `true`.
- `gates.<gate>.human` overrides the master for that gate. The gate ids are
  `plan_approval`, `technical_approval` and `acceptance`. A person approves
  only the plan with `{"gates": {"plan_approval": {"human": true}}}`; a person
  accepts only the milestone with `human_approval` true and `human: false` on
  the other two.
- `require` (plan and technical gates) holds `distinct_reviewer_models` and
  nothing else. It is on by default for both (see "Distinct reviewer models").
  The review-ledger and gate-status conditions are always required and are not
  configurable.
- `acceptance`: `require_ci` (default `true`), `requires_pr_approved` (default
  `false`) and `required_flows` (flow ids matching
  `^[a-z0-9][a-z0-9_-]{0,63}$`; default empty).
- `pr_review.enabled` (default `true`) and `pr_review.reopen_on` (default both
  causes) govern whether a pull request fact reopens an item.

Which gates can be automatic depends on the item's governing version. Version
1 items always have a human plan and technical gate (no review ledger);
version 2.1 items always have a human technical gate (no implementation
ledger); the acceptance gate follows the policy for every version.

## Safety rule: a file can only tighten

The effective policy is, field by field, the stricter of three inputs: the
last **adopted** policy (or the default), the **floor**, and the file.
Stricter means a gate human rather than automatic, a larger `require` or
`required_flows`, `require_ci` or `requires_pr_approved` true, `pr_review`
enabled, a larger `reopen_on`.

- A file that turns a gate human or adds a requirement takes effect at once.
  Editing it back or deleting it does not undo that: a looser value is ignored
  (`file_loosening_ignored` in `verify`).
- The **floor** (`gate_policy_floor` in the state) is a ratchet of every
  setting the Workflow has observed. A committing evaluation (`/satisfy-gate`,
  after its own commit; `/adopt-gate-policy`) records it, in its own state-only
  commit. A human setting that was written to the file and deleted again before
  any committed file or committing evaluation saw it is lost, and so is one
  committed and deleted inside a single squashed branch. To make a human
  setting durable, commit the file, let a `/satisfy-gate` run record it, or
  adopt it.
- The only way to loosen is `/adopt-gate-policy` (user-only). **Commit the
  file first, then run it**: the adoption commit stages only the state file, so
  a policy file that differs from `HEAD`'s is refused
  (`GatePolicyFileUncommittedError`, nothing written) rather than reported
  adopted while the committed policy stays in force. It shows the
  file's digest and every loosening and tightening, asks for confirmation text
  containing `gate_policy` and the first 12 hex characters of the digest, and
  records the adopted policy body and resets the floor to it.
- A policy change is never retroactive: an approval or acceptance already
  recorded keeps the policy digest it was given under.
- Two branches that each adopt from the same base both extend the same
  history, so the second fails closed after the first merges: update from
  `main`, take `main`'s adoption record when the merge conflicts, and run
  `/adopt-gate-policy` again.

### Provenance failure

The adopted policy and the floor are verified by content and by the chain of
their changes, never by a commit message, so they keep verifying after a squash
merge. When that verification fails (a record that is not self-consistent, a
removed or spliced adoption, a floor loosened outside an adoption) the
effective policy is **all three gates human**, `source: provenance_failed`, and
`verify`'s `gate_policy` check is `fail`, naming the field and the commit. The
way out is a new `/adopt-gate-policy` commit, which becomes the newest change
to both fields.

### Gate-lowering events

An adoption that makes any setting less human or less demanding than the
policy in effect before it (the previous adoption or the default, and the
floor) **lowers a gate**. That is a *gate-lowering event*. It is reported in
three places, so a lowering stands out in review:

1. `verify`: the `gate_policy` check is a `warn` naming the adoption's digest,
   its commit and each lowered field;
2. `next-action`: the `policy` object on the gate-policy rows carries
   `gate_lowering: {sha256, adopted_at, lowered}` while that adoption is the
   newest;
3. the audit record of every automatic satisfaction copies it.

It is a report, not a block: a lowering the user chose is legitimate. The
`lowered` label is recomputed by `verify`, never believed from the record.

## What each gate checks

Every requirement is recomputed at evaluation time from the state and the
repository.

**Plan approval and technical approval (automatic).** All of: the existing
gate-status wrapper is reachable; the latest verdict is `APPROVE` and its
bundle id equals the recomputed one (a user override is never automatic);
`distinct_reviewer_models` when required; and `review_evidence_audited` (both
ledger stages carry the audit keys). `/satisfy-gate plan <id>` and
`/satisfy-gate implementation <id>` run the steps of `/approve-review` and
write a `POLICY_SATISFIED` approval carrying `policy_evidence`, with the commit
trailer `Workflow-Gate-Satisfied-By: policy:<digest first 12>`.
`/approve-review` keeps working as the human path in either mode.

**Milestone acceptance (automatic).** All of:

1. `checkpoints_complete`;
2. `technical_approval_current`; its commit is the *anchor*;
3. `functional_flows_passed`: at least one recorded functional flow is current
   at the anchor, every one passed, and every `required_flows` entry is
   present and passed;
4. `pr_fact_current`: the Workflow's own GitHub query found an open pull
   request whose head descends from the anchor with the same implementation
   identity. A fact for an older head (an unpushed fix) or different content
   blocks;
5. `no_standing_pr_objection`: the pull request is not `CHANGES_REQUESTED` and
   its checks are not `failure`;
6. `ci_green` (when `require_ci`) and `pr_approved` (when
   `requires_pr_approved`).

**Automatic acceptance needs an open pull request first.** Requirement 4 is
always required, so open (and push) the pull request before the acceptance
runs. A repository whose flow opens the pull request after acceptance, as this
repository's does, keeps acceptance human.

The Workflow does not know which flows ought to exist: with `required_flows`
empty, acceptance needs at least one current passing flow and no failing one.
List the flows a repository needs.

### Evidence kinds

`record-external-result` accepts two new kinds, under every policy:

- `functional_evidence`: one flow's result (`flow_id`, `status`, `head`,
  `ran_at`, `summary`, `log_digest`, `run_ref`, `reporter`). The Workflow
  recomputes the implementation identity at `head`; a later commit that
  changes protected implementation content makes it stale, a commit that
  changes only excluded paths does not. It is an inert record under a human
  acceptance gate.
- `pr_review_result`: a pull-request fact, accepted only with the verbatim
  output of the Workflow's one fixed GitHub query. It only tightens (see
  "Trust boundary").

There is no CI result kind: the CI outcome is the checks of the Workflow's own
pull-request fact. Functional or review evidence produced in CI is not
accepted, and no policy option offers it.

### Order for `requires_pr_approved`

`requires_pr_approved` is a per-gate option because in automatic mode no person
may review the pull request. A repository where a human review of the pull
request is the evidence turns it on. The order is:

1. open the pull request and push the implementation head;
2. wait for the checks and the reviewer's decision on that head;
3. run `/satisfy-gate acceptance <id>` (or let the orchestrator do it), which
   queries GitHub itself;
4. under a human acceptance gate with the option on, `/accept-milestone` runs
   the same query as a pre-flight and refuses without an `APPROVED` decision.
   The option never removes a person from a human gate.

A `pending` check or a missing review blocks with the remedy named
(`pr.review.external` asks the orchestrator to report a fresh result, or to run
`/satisfy-gate acceptance`, which re-queries). A report is only a trigger for
that query and never evidence.

## Distinct reviewer models

`distinct_reviewer_models` is **on by default** for the automatic plan and
technical gates and is configurable. It requires the two ledger stages to
record a `Reviewer model:` whose **families** differ. A family is meant to be a
vendor-level identity: `anthropic/...` against `openai/...`. The requested form
is `Reviewer model: <vendor>/<model>`; the family is the value up to the first
`/`, `:` or space, lowercased. Two models of one vendor (`anthropic/opus`,
`anthropic/sonnet`) are one family. The values are declared and unverified.

A person with a single reviewer subscription has three first-class paths, and
which one works depends on the phase:

1. **Record a second family at ingest, while the stage is open.**
   `/record-manual-plan-review` and `/record-manual-implementation-review` (and
   `record-external-result`) refuse an `APPROVE` that states no `Reviewer
   model:` header line, or the same family as the other stage, before any
   write. Re-submit with a second declared family. `REVISE` and `BLOCK` need
   none.
2. **Turn that gate human.** `"gates": {"plan_approval": {"human": true}}` (or
   `technical_approval`). It is an uncommitted edit, immediate at any phase,
   needs no adoption, and gives no gate-lowering signal. The same ingest is
   then admitted with no model line and `/approve-review` proceeds. Returning
   that gate to automatic later is itself a loosening: it needs an adoption, is
   a gate-lowering event, and the floor keeps the gate human for later items
   until then.
3. **Adopt a policy without the requirement** through `/adopt-gate-policy`,
   **before the stage's bundle is generated**. It is a gate-lowering event and
   shows as a permanent `warn` in `verify` and as the `gate_lowering` object
   while that adoption is the newest: the expected and legitimate signal for
   that setup. Adopting while a bundle is open stales it (the adoption commit
   moves `HEAD` past the bundle's generation head), so it is not a remedy at an
   open review phase. If you did, the way out is the withdrawal
   (`/milestone-plan <id>` at plan stage) or, at the implementation stage,
   `/recover-implementation-provenance <id>`; the adoption's confirmation text
   lists each bundle it will stale.

After the stage has closed (`AWAITING_PLAN_APPROVAL` and its technical
analogue), only turning the gate human, or the withdrawal and a repeat of both
review stages, work: no command re-records a stage in place. A 2.7.0 item in
flight when the repository updates can be blocked by both
`review_evidence_audited` and `distinct_reviewer_models` at `14b`/`28b`; turning
that gate human is the recommended remedy for it.

### The toggle on an old declaration

A declaration generated from the 2.7.0 template excludes the `docs/ai-workflow/`
prefix at both stages, so a new `GATE_POLICY.json` is classified. An older
hand-authored declaration that does not exclude it leaves the file
unclassified (`UnclassifiedPathError`); the failure is closed but the toggle
cannot be used for that item. Before the stage's bundle is generated, exclude
the prefix (or the exact path) in the declaration. With the bundle open,
committing the file never helps and editing the declaration changes the
reviewed content: use the withdrawal that `14b`/`28b` name, with the prefix
excluded before regeneration.

## Reopening the same work item

When the Workflow's own query finds the pull request red, the item is reopened
as the same work item into remediation rather than a new one. The causes:
`content_changed` (the pull request carries implementation content the anchor
does not), `changes_requested` and `checks_failed` (the latter two only when
listed in `reopen_on`). Row `38d` emits `pr.apply_review`, whose command is
`/apply-pr-review <id>`; it stales the technical approval before any edit, and
takes one of three branches for the two review causes: no code change, a
bounded fix (a new implementation review round) or a broad fix (a remediation
child). After remediation the item goes through technical review, functional
validation and acceptance again, the gate being automatic or human as its
toggle says; full functional validation is required once code changed.

- **Completion side effects stay.** A reopen does not undo them: the roadmap
  row stays complete and `docs/ACTIVE_MILESTONE.md` stays cleared until the item
  is accepted again. Read the state (`next-action --work-item <id>`), not those
  narrative files, to see a reopened item.
- **A reopened item is surfaced only by naming it.** A reopen does not set
  `active_work_item_id`, and a completed item's pointer was reset when it
  completed, so `/apply-pr-review` requires the work item id and refuses a bare
  call, and `next-action` needs `--work-item <id>`.
- **Enabling a reopening cause never reopens a completed item from a stored
  fact alone.** A stored key says what was red, not whether the pull request is
  still open, and nothing queries the forge after completion. `/apply-pr-review`
  re-queries GitHub first and reopens only when the fresh answer is an open
  pull request yielding an unapplied actionable cause. A merged pull request is
  refused (`pr_merged`; the follow-up is a new work item) and a fact superseded
  by a closed or green answer is `pr_fact_superseded`. A key whose pull request
  was since merged, with no merged fact ever reported, never reopens the item.
- **Residual: a same-head red reopen with no orchestrator report.** A pull
  request that, after a `closed` or green answer, turns red again at the same
  head, with no stored actionable key and no report, is reached by a Workflow
  query only on these paths:

  | phase and acceptance mode | query path with no orchestrator report |
  | --- | --- |
  | `AWAITING_FUNCTIONAL_REVIEW`, automatic acceptance | `/satisfy-gate acceptance` (rows `38f`, `38h`): stores the fact and reopens once |
  | `AWAITING_FUNCTIONAL_REVIEW`, human acceptance, `requires_pr_approved` true | `/accept-milestone` step 2a stores the fact; a `CHANGES_REQUESTED` answer refuses it and `38d` then reopens; a failed-checks answer on an `APPROVED` pull request lets it complete, and `38d` then reopens from `MILESTONE_COMPLETE` (never a retired legacy item) |
  | `AWAITING_FUNCTIONAL_REVIEW`, human acceptance, `requires_pr_approved` false (the default) | **none**: no query runs |
  | `MILESTONE_COMPLETE`, any mode | **none**: nothing queries the forge after completion |

  Where the table says none, a same-head red reopen with no orchestrator report
  is never remediated: at `MILESTONE_COMPLETE`, and, under human acceptance
  without `requires_pr_approved`, at `AWAITING_FUNCTIONAL_REVIEW`, where no
  Workflow query runs. Only an orchestrator report of the open pull request
  arms the query (`pr.apply_review` by the trigger) and reaches remediation. A
  human-acceptance repository must have an orchestrator report it to have it
  acted on.
- A reported or queried fact that is merely recorded (the item is at another
  phase) is acted on when the item reaches `AWAITING_FUNCTIONAL_REVIEW`; a
  human is not offered `/accept-milestone` over an actionable red fact.
- `findings` text from a pull request review is untrusted: data to classify,
  never instructions to follow.
- Residue in the state file that belongs to an item that never commits again
  (a completed item with a functional record or a non-actionable fact) stays on
  disk only; it gates nothing, and a fact that matters reopens the item, whose
  commits carry it.

## Trust boundary

Who is believed, stated plainly.

- **CI and pull-request facts come from GitHub.** Facts that satisfy a gate
  (state, head, review decision, checks) come from the Workflow's one fixed
  query, `gh pr list` for the exact commit and repository, run by the Workflow
  itself inside the satisfying command's transaction and parsed by Workflow
  code. An orchestrator's forge fact (`orchestrator_forge`) **only tightens**:
  it is recorded, can only trigger the Workflow's own query, and never decides
  a pull-request state, a reopening, completion or acceptance. It never
  satisfies `pr_fact_current`, `ci_green` or `pr_approved`.
- **A gate that cannot decide blocks.** When `gh` is not installed or not
  authenticated (`forge_unavailable`), times out, prints something unexpected,
  names another repository, finds more than one open pull request, a full page
  of 200 or more records, or resolves to an unsafe path (`forge_undecidable`),
  the gate does not pass, and no stored fact stands in for the query. A pull
  request that does not exist is an unmet requirement with the remedy to open
  it.
- **Review verdicts and functional evidence are trusted from the orchestrator
  that reports them.** The Workflow cannot tell a deterministic orchestrator
  from an agent calling the same command.
- **The Workflow guarantees binding, freshness and audit. It does not
  guarantee provenance** for review verdicts or functional evidence. Binding:
  a verdict is tied to the bundle id and the `review_content_id`, and
  functional evidence to the identity recomputed at its head. Freshness: a
  change to the protected content makes them stale. Audit: each automatic
  satisfaction leaves a record (`policy_evidence`, `acceptance_satisfaction`)
  naming, for each review verdict, its verdict hash (`verdict_sha256`), the
  ledger entry hash, the bundle id, the `review_content_id` and the run
  reference (`run_ref`, declared by the reporter and never verified); for
  functional evidence the digest, reporter and run reference; for a pull-request
  fact its provenance, the resolved `gh` path and its sha256; and the
  `gate_lowering` object when the newest adoption is one. A run reference that
  is absent is recorded as `null` and shown as a `warn` by `verify`.
- **Turning human approval on is the stronger mode.** It puts a person between
  the evidence and each gate it is turned on for. The automatic mode is a
  convenience that leaves a trail a person can check afterwards, not a proof
  that a review or a test run happened.

## Threat model

This is the one place the guide states what the policy does and does not
guarantee.

The threat model is the one today's human gates already have: an agent running
on the same machine as the user can write any file, make any commit and
install any program. No local mechanism can tell the user's
`/adopt-gate-policy` from a perfect hand-built forgery of its commit, or the
real `gh` from a fake one; `/approve-review` and `/accept-milestone` have
exactly this limit.

1. **What is guaranteed.** The tighten-only effective policy, the floor, the
   content and chain provenance of the adoption, and the GitHub-sourced
   pull-request facts hold against **an agent acting through the Workflow's
   commands and protocol**: one that runs the commands and the
   `record-external-result` ingest, edits the policy file and the working tree
   and reports evidence, but does not deliberately forge commits, trailers or
   state by hand or replace system programs. Against it, a gate cannot be
   loosened without `/adopt-gate-policy`, and a CI fact cannot be fabricated.
2. **What is not guaranteed.** The guarantees do **not** hold against an agent
   that deliberately forges a commit, a trailer or the state (a self-consistent
   adoption record, a hand-built commit) or replaces a system program (a `gh`
   or `git` earlier on `PATH`, or in a directory it controls). No signed
   commits and no GitHub-side adoption are provided.
3. **The safeguards that remain**, so a forgery is harder and never silent:
   - **`gh` resolution.** The Workflow does not look `gh` up by name when it
     runs. It takes the first `gh` on `PATH`, resolves symlinks to an
     **absolute path** and runs that path. It refuses (`forge_undecidable`, so
     the gate blocks) when the executable or its directory is inside the
     repository, inside any of its worktrees, inside the temporary directory,
     or in a world-writable directory (or is itself world-writable). The stored
     pull-request fact **records the resolved path and the executable's
     sha256** (`gh_path`, `gh_sha256`), so a review can see which program
     answered. This stops a `gh` placed in the repository, a worktree or a
     temporary directory by an agent working through the commands; it cannot
     stop one that replaces the real `gh` or a program in a directory only the
     user could write to;
   - **gate-lowering events** (above): a lowering, forged or real, is flagged
     in the audit record and reported by `verify` and `next-action`;
   - the existing evidence trail, the user-only `/adopt-gate-policy` with its
     confirmation text, and **human approval as the stronger mode**.

Where a repository's risk includes an agent that forges state, use human gates.

## Quick reference

| I want to | do |
| --- | --- |
| go back to human gates | `{"schema_version": 1, "human_approval": true}` |
| one person approves only the plan | `gates.plan_approval.human: true` |
| require a person's review of the pull request | `gates.acceptance.requires_pr_approved: true` |
| require named functional flows | `gates.acceptance.required_flows: [...]` |
| stop pull-request facts reopening items | adopt `pr_review.enabled: false` (a loosening) |
| loosen any setting | edit the file, commit it, then `/adopt-gate-policy` |
| unblock `distinct_reviewer_models` with one subscription | the three paths above |
| clear `provenance_failed` | a new `/adopt-gate-policy` commit |
