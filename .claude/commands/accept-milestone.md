---
description: Close out an explicitly accepted milestone and prepare the next one for planning.
argument-hint: "[work-item-id]"
disable-model-invocation: true
state_writer: true
review-subject: none
---

**State-writer discipline (D1, item 354):** every
`docs/ai-workflow/WORKFLOW_STATE.json` write this command performs --
everywhere a step below says "persist the returned state" -- is performed by
calling `workflow_state.state_transaction(repo_root, mutator)`, never by a
separate read-then-write: `state_transaction` holds
`.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`,
`fcntl.flock(LOCK_EX)`) across the complete re-read ->
apply-the-named-function -> canonical-serialize -> atomic-publish sequence in
one process invocation, so `mutator` is the exact transition function each
step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`),
applied to freshly re-read state rather than to a snapshot taken before the
lock was acquired.

Where the gate policy makes acceptance automatic and its evidence is met,
`/satisfy-gate acceptance` accepts the milestone from evidence in place of this
command (`workflow-2.8.0`); this command stays the human path in either mode.

Enter the `MILESTONE_COMPLETE` state of
`docs/ai-workflow/MILESTONE_WORKFLOW.md`. Only run this after the user has
explicitly accepted the milestone (`AWAITING_USER_ACCEPTANCE` exit
condition) — if that acceptance hasn't happened in this conversation, ask for
it before proceeding.

**This command is user-only by construction** (`docs/ai-workflow/WORKFLOW_V2_PLAN.md`
D2, resolves `OPUS-R6-010`), the same two mechanisms as `/approve-review`:
(1) `disable-model-invocation: true`, an additional layer where the harness
enforces it (the harness notes in `docs/ai-workflow/COMMAND_FILES.md` record
what is known per harness); (2) refuse to write anything unless the user's own
current-turn message supplies literal confirmation text naming the exact
`work_item_id` and the `acceptance` stage
(`workflow_state.validate_user_confirmation(text, work_item_id=..., stage="acceptance")`).
Never fabricate, infer, or carry over this text from a prior turn. If it is
missing, ask for it and stop — do not proceed on an inferred "yes." Mechanism
(2) is the control this command carries itself, under every harness; the flag
does not replace it. The agent must never invoke this command on the user's
behalf, including as a step of another command's execution, whatever the flag
says.

0. **Dual-mode branch** (Workflow v2.1, `WF4a-ii`, extended `WF4c`): resolve
   the target work item first -- the id named in `$ARGUMENTS`, or
   `active_work_item_id` from `docs/ai-workflow/WORKFLOW_STATE.json` if
   omitted. Refuse with a named error if neither resolves to an existing,
   non-terminal `work_items` entry -- never guess, and never infer the id
   from step 1's confirmation text (the confirmation is checked *against*
   the resolved id, so deriving one from the other would make the guard
   self-satisfying). The explicit id is what makes a work item that is
   *not* `active_work_item_id` acceptable at all (`D1`: the pointer is
   resume focus, not an execution lock) -- and a
   `<parent-id>-remediation-<n>` child never holds the pointer, so
   `/accept-milestone <child-id>` is the **only** way to close one, and
   therefore the only way its parent's own
   `IncompleteChildWorkItemError` block below ever clears. Then read that
   work item's `governing_workflow_version` from
   `docs/ai-workflow/WORKFLOW_STATE.json`. `"1"`, `"2.1"`, and
   (`workflow-2.5.0`) `"2.2"` items alike run steps 1-8 identically,
   including the new step 2a below —
   `AWAITING_USER_ACCEPTANCE`/`MILESTONE_COMPLETE` gain exactly one shared
   condition from `D-Functional-Remediation`: acceptance blocks while any
   work item names this one as its own `parent_work_item_id` and has not
   itself reached `MILESTONE_COMPLETE` — version-independent, since a
   remediation child can exist under either governing version. This step
   exists so the command's own dual-mode structure is explicit and
   testable per `D-Self-Governance`'s "every command this milestone
   modifies" enumeration.
1. Run the user-only guard: call
   `workflow_state.validate_user_confirmation(text, work_item_id=..., stage="acceptance")`
   against this turn's literal user text. `UserConfirmationRejectedError`
   stops the command here — report the concrete reason and ask for the
   missing/corrected confirmation; do not proceed without it.
2. Confirm final verification already passed (from the last
   `/milestone-implement` or `/apply-implementation-review` run); rerun only
   if the working tree changed since.
2a. **Terminal-reachability pre-flight, then parent-/own-checkpoint-
    completion block** (`D-Functional-Remediation`, `WF4c`, resolves
    `GPT-R9-016`; extended `D-Scoped-Remediation-Acceptance`, resolves
    `WF8B-002`, hardened `GPT-R37-001`): if this work item has a
    `docs/ai-workflow/WORKFLOW_STATE.json` entry:
    - First, an **advisory, user-facing pre-flight refusal** (not a
      security boundary — `complete_work_item` itself re-resolves and
      re-validates the authoritative registry independently below, so this
      is purely for a clearer early message): call
      `workflow_state.resolve_own_registry_completion_status(repo_root,
      work_item)` to get `(is_terminal, outstanding_checkpoint_id)`, then
      `workflow_state.milestone_complete_gate_reachable(phase=work_item["phase"],
      is_terminal=is_terminal)`. `False` stops here — report the actual
      `phase` and, if `not is_terminal`, name `outstanding_checkpoint_id`
      and what the next bullet says about it; do not proceed past
      this point.
    - **A non-terminal registry** (ledger
      `I10` of the Workflow v2.x defect ledger retired the non-terminal
      acceptance command that used to be named here; its
      gate had no producer in any supported lifecycle, so it can never be
      the answer): an outstanding checkpoint cannot be completed from
      `AWAITING_FUNCTIONAL_REVIEW`. `/milestone-implement` cannot start a
      checkpoint at this phase (`transition_checkpoint_in_progress` refuses
      outside `IMPLEMENTING`), and no command completes one here (defect
      `v2.6.0-003`): report the outstanding checkpoint and stop. For a
      `2.1`/`2.2` item name the user-only `/resume-implementation <id>`
      (workflow-2.9.0), which returns it to `IMPLEMENTING` with the
      technical approval marked `STALE`; never run it on the user's behalf.
      Ordinary flow never reaches this phase with a checkpoint outstanding;
      only a hand-constructed or hand-edited state does. If acceptance is being
      attempted early because of a functional-review finding, route that
      finding through `/apply-functional-review` instead — its bounded
      branch for a same-scope fix (which marks `technical_approval`
      `STALE` and returns the item to a fresh implementation-review
      round), or its broad branch for new or wider scope (which creates a
      `<parent-id>-remediation-<n>` child work item running its own full
      cycle). There is no command that records functional acceptance of a
      partial round.
    - **Optional-finding dispositions pre-flight** (workflow-2.14.0, `D-Gate`;
      applies to every item, a remediation child included; **runs before the
      `requires_pr_approved` pre-flight below**, so a refusal happens before any
      query or write): call
      `workflow_state.assert_finding_dispositions_recorded(repo_root, state,
      work_item_id)`. `UndispositionedFindingsError` stops here: report each
      listed finding (key, stage, role, round, text) and the next step it
      names -- record a disposition for each in
      `docs/ai-workflow/registry/<id>-dispositions.json` (`fixed`, `dropped`
      with a `reason`, or `follow-up` with a `link`; `python3
      scripts/workflow_state.py --finding-dispositions <id>` lists them and
      prints a skeleton), save the file, run this command again. A follow-up is
      a complete disposition: acceptance never waits for its fix.
      `InvalidFindingDispositionsError` stops here too, naming every problem in
      the file. The file is not committed on its own; step 6 stages it.
      `complete_work_item` re-checks the same condition below.
    - **Backlog pre-flight** (workflow-2.17.0, `D-Accept`; applies to every
      item, a remediation child excepted, which skips the roadmap step; runs
      **after the optional-finding dispositions pre-flight above and before
      the `requires_pr_approved` pre-flight below**, so a refusal happens
      before the `workflow_gh` fact is stored or `complete_work_item`
      writes anything, and `WORKFLOW_STATE.json` stays byte-identical):
      run `python3 scripts/workflow_backlog.py preflight-accept
      <work_item_id>`. It writes nothing. `{"declared": false}` means the
      repository declares no backlog: nothing more to do here, steps 3 to 7
      are today's. On a refusal (`declaration_invalid`, `backlog_missing`,
      `backlog_invalid`, `item_unknown`, `status_not_acceptable`,
      `roadmap_not_generated`, `generator_failed`, `declaration_conflict`),
      report stderr verbatim -- every diagnostic and the next step -- and
      stop: nothing has been written. `item_unknown` names the fix (add an
      item with this id and
      status `in-progress`); a refusal is never worked around by editing the
      generated page.
    - **`requires_pr_approved` pre-flight** (`workflow-2.8.0`, `D-GP-Acceptance`;
      added only when the effective policy sets `requires_pr_approved`, which is
      off by default, so a repository without it behaves as 2.7.0): call
      `workflow_state.assert_human_acceptance_pr_approved(repo_root,
      work_item_id, now=<now>)`. It runs the Workflow's own GitHub query, stores
      the `workflow_gh` fact it read (a successful query only, also when it then
      refuses), and refuses with `PullRequestNotApprovedError` unless that fact
      is current for the approved head and the pull request is approved. With
      `gh` unavailable it refuses (`forge_unavailable`) and writes nothing.
      Report the unmet requirements and stop. It returns `None`, having run and
      written nothing, when the option is off.
    - Otherwise, call `workflow_state.complete_work_item(state, work_item_id,
      now=<now>, repo_root=<repo_root>)` — note the parameter change: this
      no longer accepts a caller-supplied `registry` dict at all; it
      resolves and loads the item's own `registry_path` itself,
      authoritatively, from disk. `IncompleteChildWorkItemError` stops here
      — report every named still-incomplete child work item verbatim.
      `RegistryCoverageError` stops here — the item's own declared
      registry failed to resolve, read, parse, or declare the expected
      `work_item_id`; report the specific failure verbatim, this is a data-
      integrity defect, never silently treated as "nothing to check."
      `UndispositionedFindingsError` and `InvalidFindingDispositionsError`
      stop here as the pre-flight above says.
      `IncompleteOwnCheckpointsError` stops here — report the named
      outstanding checkpoint as the bullet above says. The exception's
      own message still carries 2.6.0's `/milestone-implement` advice,
      which cannot run at this phase.
      `UnsatisfiedCompletionObligationError` stops here too
      (`D-Completion-Obligations`, item 356(a)): every completion
      obligation the item's **own registry** declares must derive `PASS`,
      independently of and in addition to both checks above, so marking
      every checkpoint `COMPLETE` is not sufficient by itself. Report each
      outstanding obligation id, its classification, and — for a `FAIL` —
      the specific failing conformance assertions the message names, then
      stop; this is a real release gate to fix, never something to retry
      past. A registry that declares no obligations reaches this check
      vacuously satisfied.
      None of these four stops perform any `ROADMAP.md`/`ACTIVE_MILESTONE.md`
      update or completion commit. On success, persist the returned state
      (the work item's `phase` is now `MILESTONE_COMPLETE`;
      `active_work_item_id` resets to `null` if it pointed here) as part
      of the completion commit in step 6 — the original item's own
      registry/mapping/completed-checkpoint history is untouched by this
      call, by construction. A work item with no state entry (an ordinary
      `"1"` item that never got one) has no children and no registry by
      construction — skip this entire step.
2b. **Remediation-child bookkeeping branch**: a work item whose
    `parent_work_item_id` is non-null is a `<parent-id>-remediation-<n>`
    child, not a roadmap milestone — it was never listed in
    `docs/ROADMAP.md` and has no milestone summary to archive. For such an
    item, skip steps 3, 5 and 7, but run step 5a, and replace step 4 with a
    report rather than an edit: `workflow_state.FUNCTIONAL_CHECKLIST_PATH`
    is a single shared file (`docs/ACTIVE_MILESTONE.md`), and the child's
    own `/prepare-functional-review <child-id>` has already overwritten
    the parent's checklist there, so do **not** edit it in place. Report
    instead that the parent's next action is
    `/prepare-functional-review <parent-id>` — which rewrites the
    parent's checklist, notes that the findings deferred to this child are
    now resolved, and creates the parent's own fresh round-scoped evidence
    commit — followed by `/accept-milestone <parent-id>`. Steps 1, 2, 2a,
    6 and 8 apply unchanged. Every other work item runs steps 3-7 as
    written.
3. Update `docs/ROADMAP.md` to mark the milestone complete.
   **Backlog declared** (workflow-2.17.0, `D-Accept`; the pre-flight above
   answered `declared: true`): do not edit `docs/ROADMAP.md` or the declared
   page by hand. Run `python3 scripts/workflow_backlog.py accept
   <work_item_id>` (add `--release <version>` only when the milestone itself
   names a release). It sets the item `done`, regenerates the page and
   prints the two changed paths as JSON; the completion commit stages
   exactly those paths together with step 5a's. A re-accepted `done` item
   changes nothing but still regenerates. When `accept` refuses after the
   state completion (`generator_failed`, `backlog_invalid`,
   `roadmap_not_generated`, `declaration_invalid` or `declaration_conflict`:
   the pre-flight cannot catch a change made since), the work item is
   already `MILESTONE_COMPLETE` and this command cannot be re-run (step 0
   refuses a terminal item): report the refusal with its recovery and
   **write nothing else**. The recovery is one named step: fix the cause
   (the generator or the file; `accept` has restored both the file and the
   page), run `python3 scripts/workflow_backlog.py accept <work_item_id>` by
   hand (idempotent; it reads no Workflow state), then continue this
   command at step 4, then 5, 5a, 6 (the completion commit, trailer
   `Workflow-Work-Item: <work_item_id>`, staging the printed paths), 7 and 8.
   An undeclared repository runs the sentence above unchanged.
4. Update `docs/ACTIVE_MILESTONE.md`: move this milestone's summary into the
   factual "complete" state, clear the active plan section.
5. Archive this milestone's execution/reference plans to
   `docs/milestones/completed/` by **copying** them, never moving them
   (`LPR-R2-003`): the item's `plan_path`, registry and mapping stay where
   `WORKFLOW_STATE.json` names them, so a later reopen of the same work item can
   still read the plan. A re-acceptance updates, never duplicates, the archive
   copy and the roadmap row for the work item id.
   **A re-acceptance** (`workflow-2.8.0`, `D-GP-Reopen`: an item that
   `/apply-pr-review` reopened into remediation and that now completes again
   through this command or `/satisfy-gate acceptance`) finds the archive copy
   and the roadmap row already present: it overwrites the copy with the current
   plan and leaves the one roadmap row complete, so there is exactly one of
   each per work item id. `complete_work_item` is unchanged: an automatic
   re-acceptance overwrites `acceptance_satisfaction`, and
   `completion_obligations_accepted` is kept (not rewritten) when the item
   declares no completion obligations.
5a. **Write the findings record** (workflow-2.14.0, `D-Record`; every item,
   a remediation child included): call
   `workflow_state.write_finding_dispositions_record(repo_root, state,
   work_item_id)` on the completed state. It writes
   `docs/milestones/completed/<id>-findings.md` (every recorded Optional
   finding with its disposition, reason or link; a re-acceptance overwrites it)
   and returns the paths the completion commit stages: the record and the
   dispositions file when it exists. An item with no recorded finding returns
   nothing. A child's findings do not roll up into its parent's.
6. Create the final completion commit if verification/doc updates are not
   already committed, carrying a `Workflow-Work-Item: <work_item_id>`
   trailer (the established convention every real completion commit to
   date already follows). **This line must be the commit
   message's own final paragraph** and nothing else is in it: any
   `Co-Authored-By:`/`Claude-Session:` lines go in their own paragraph
   before it, separated by a blank line (`OPUS-R129-001`, the same rule
   `milestone-implement.md` step 1f, `bootstrap-workflow-v2.md` step 6,
   and `approve-review.md` step 6.4 already state): Git's `git
   interpret-trailers --parse` treats only the message's actual last
   paragraph as trailers, so a blank line after this line (e.g. one
   separating it from a following `Co-Authored-By:`/`Claude-Session:`
   block) silently discards it. No command currently re-discovers this
   trailer by search -- `/accept-milestone` is a terminal, one-way
   transition -- but the same layout rule still applies so the trailer is
   a genuine Git trailer rather than merely trailer-shaped text. The commit
   stages the paths step 5a returned (and, for a declared backlog, the two
   paths step 3's `accept` printed).
7. Set `docs/ACTIVE_MILESTONE.md`'s "Next action" to point at the next
   incomplete milestone in `docs/ROADMAP.md`, ready for `PLANNING`.
   **Backlog declared** (workflow-2.17.0): run `python3
   scripts/workflow_backlog.py next` (read-only) and set "Next action" to
   the item it returns, by id and title, ready for `PLANNING`, or to "no
   item is ready: <reason>" when it refuses. That is a report, not a
   failure; step 8 reports the same.
8. Report the milestone as complete and the next action as
   `/milestone-plan`. Do not begin implementing the next milestone in this
   command. (For a remediation child, step 2b's report replaces this
   one's "next action": the parent is what continues, not a new plan.)
