# W1: move this repository to Workflow 2.29.3 (Revision 3)

Work item: `workflow-controller-w1`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `956b14f15c1e614cdcb17bd30277afbd1578335f` ("fix: make OrphanWatchdogTest wait for the stub's readiness and explain the Ctrl-C test's failure under load (FX1, #246, #342) (#347)"), the tip of `main`, passed explicitly as `/milestone-plan 956b14f15c1e614cdcb17bd30277afbd1578335f`.
Lifecycle authority: this repository's installed Workflow 2.27.0, automatic gates; the move itself is run by the operator, with no job running, before CP1's worker is launched (D2).
Roadmap slot: backlog item `workflow-controller-w1` (`docs/backlog.yaml` order 86; owner request 2026-11-13, "upgrade the workflow when possible"). Its description is `docs/items/workflow-controller-w1.md`. It depends on nothing.
Released baseline: the latest tag. The change touches no Controller code, so the squash commit is a `chore:` and releases nothing.
Pull request title: `chore: move this repository to Workflow 2.29.3 (W1)`

## Goal

Move this repository from Workflow 2.27.0 to 2.29.3, the way the 2.27.0 move (PR #311) did: Workflow Manager replaces the managed files and rewrites the installation record, the Controller admits the release by capability (protocol 1.5, unchanged), and the compatibility page records it. One mechanism: the Workflow move. Adopting the new commands in the Controller's own steps and any protocol change are out of scope.

## What exists

- `workflow-manager --release-version 2.29.3 update --dry-run .` (Manager 1.18.1, release discovered from `github:RodrigoFAbreu/workflow`, verified by its own checksums; the Manager has no pin for it) says it would update eleven managed files and leave the rest identical: `.claude/commands/accept-milestone.md`, `.claude/commands/apply-pr-review.md`, `.claude/commands/satisfy-gate.md`, `docs/ai-workflow/ORCHESTRATION_PROTOCOL.md`, `docs/ai-workflow/REVIEW_PROTOCOL.md`, `docs/ai-workflow/audit/WORKFLOW_DEFECT_LEDGER.md`, `scripts/workflow_gate_policy_test.py`, `scripts/workflow_integration_test.py`, `scripts/workflow_protocol.py`, `scripts/workflow_protocol_test.py` and `scripts/workflow_state.py`; `.workflow-manager/installation.json` is rewritten.
- 2.28.0 to 2.29.3 add the plain commands `--write-findings-record`, `--commit-gate-policy-floor` and `--finding-dispositions` and harden `--acceptance-gate` and `--satisfy-acceptance`. The Manager's notes say protocol 1.5 stays and no Controller change is needed; the compatibility page's "Workflow moves" entries do not yet name these releases.
- This repository's own tests read vendored trees (`tests/workflow_releases/`) except the review-round tests, which read the installed `scripts/`; the 2.27.0 move vendored nothing.

## Decisions

- **D1. The Manager moves the files, never by hand.** The move is `workflow-manager --release-version 2.29.3 update .` followed by `workflow-manager verify .` and `workflow-controller inspect .` (run as `PYTHONPATH=<repo> python3 -m controller inspect .`, since the installed Controller may be older). Nothing is edited in a managed file; a Manager refusal (exit 2) or a `verify` that is not clean stops the operator's sequence (D2) with the output recorded, and no file is repaired by hand.
- **D2. The operator runs the move, with no job running, before CP1's worker starts.** The phase is the one `docs/update.md`'s subsection "In the middle of a milestone" proves for an in-place update: `IMPLEMENTING`, between checkpoints (the section's general rule, "between milestones, with no work item in flight", is not what is followed; this item's own run is new evidence for that proven phase, recorded there in one sentence by CP2). `docs/update.md` ("Move a repository to a newer Workflow") says never to move a repository while a worker runs, and a job is verified under its record's `target_workflow_version` and the sha256 of every managed `scripts/` file (`controller/job.py`), so a worker that ran `update` inside CP1 would end `FAILED workflow_release_changed` (exit 30). So no worker runs it. The sequence, once the plan is approved and the item is at `IMPLEMENTING` with no checkpoint started and no `run`/`step` executing (`workflow-controller status` shows nothing active):
  0. **Stop the lane at that point.** The plan gate is automatic, so a default `workflow-controller run` would launch `/satisfy-gate plan` and go straight on to the `/milestone-implement` worker (`docs/run.md` step 2), starting CP1 under 2.27.0. So from the external plan approval on, the operator never uses a bare `run`: they drive the item with `workflow-controller step .` one step at a time, and run `workflow-controller explain .` before each step. The operator stops, and does not run the step, when `explain` shows that the next action is the `/milestone-implement` worker (item at `IMPLEMENTING`, no checkpoint started, `status` showing nothing active). `run --max-steps 1 .` is the same one step, and either form ends before the next action is chosen. The move (steps 1 to 2) is done at that stop.
  1. Check the Controller that drives the run before any file is moved. It is the installed `workflow-controller` (1.29.0 at planning time; the latest tag is `v1.31.1`). `docs/compatibility.md` says Controller 1.7.0 and later admit by capability any release whose `workflow_protocol.py` answers protocol major 1, and 2.29.3 keeps protocol 1.5, so 1.29.0 admits it; this is read from that page and the Manager's dry-run, because `inspect` cannot be run against 2.29.3 before the move. After the move (step 2), run the installed `workflow-controller inspect .` and `PYTHONPATH=<repo> python3 -m controller inspect .` as the confirmation; if the installed one refuses 2.29.3, roll back by checkout (below, before CP1 starts), install a Controller that admits it (`docs/update.md`, "Update the Controller") and repeat the move.
  2. Run D1's `update`, `verify` and `inspect`. The tree is dirty at this point by design: `WORKFLOW_STATE.json`, the plan, registry, mapping and artifacts files, and the item's `.ai-review/` bookkeeping are this item's own uncommitted lifecycle writes, so the Manager's `[warning] dirty-tree` and "No undo command" are accepted for this move. The dry-run lists no migration, and the `update` writes none: 2.29.3's `workflow_state.py` reads this item's 2.27.0-era state entry unchanged (the state schema is not in the update's changed-file list beyond the script itself, and CP1 confirms it by running the Workflow's own checkpoint start).
  3. Only then launch CP1's worker (`run`). Its job record is decided under 2.29.3 from its first byte, so nothing straddles the release change. The worker's checkpoint start and completion transactions run on 2.29.3's `workflow_state.py` on a state file 2.29.3's gate commands have already read. If the checkpoint start refuses the dirty managed paths, stop and report the refusal; no file is repaired by hand.
  
  Rollback, only before CP1 starts: `git checkout -- <the twelve paths>` restores 2.27.0 byte for byte, and the state file (still written by 2.27.0's scripts) is untouched by the move, so the Controller admits the restored release as before. After CP1 has started, do not roll back by checkout: stop, and withdraw or amend through the lifecycle.

  Recovery if CP1's worker launches before the move anyway (a bare `run` or a mis-timed `step`): CP1's checkpoint is `IN_PROGRESS` and the move cannot be done inside it. The worker's own dirty-set and `verify` checks fail against the unmoved 2.27.0 tree, so it commits nothing and stops without completing CP1. Then the operator returns to "between checkpoints" with the user-only `/release-checkpoint-start workflow-controller-w1 CP1` (Workflow 2.19.0; installed here, `.claude/commands/`, and not used by any automatic step), which releases the checkpoint start without a rollback by checkout, confirms with `status`/`explain` that the item is at `IMPLEMENTING` with no checkpoint started and nothing running, and only then runs steps 1 to 3. If `/release-checkpoint-start` refuses for the item's state, stop and report its refusal: the item is then withdrawn or amended through the lifecycle, never repaired by hand.
  
  The changed paths are all under classified-excluded prefixes (`.claude/commands/`, `scripts/`, `docs/ai-workflow/`, `.workflow-manager/`) at both stages, so the move does not move this item's approvals. The item page's "not while a run is active" is followed: the move happens with no run active.
- **D4. CP1 needs an in-place implementer.** CP1's only paths are under `scripts/`, `.claude/` and `.workflow-manager/`, which a Codex implementer's landing refuses as `surface_touched` (`controller/codex_worker_landing.py` `SURFACE_DIRECTORIES`). The ignored local `.controller/routing.json` routes `milestone-implement` to Claude today; keep it there for this item, so a later routing change does not fail CP1.
- **D3. Nothing is released.** A `chore:` title maps to no release in `.workflow-controller/policy.json`.

## Checkpoints

<!-- CP1 -->
### CP1: confirm the repository on the moved 2.29.3 files

The operator's move (D2) is already done when this checkpoint's worker starts. The worker changes no managed file. It checks that the dirty set is exactly the twelve paths of "What exists" plus this item's own lifecycle writes (`docs/ai-workflow/WORKFLOW_STATE.json`, `docs/ai-workflow/CONTROLLER_W1_PLAN.md`, the registry, mapping and artifacts files, and ignored `.ai-review/` and `.controller/` bookkeeping), and nothing else (`git status`; `docs/ACTIVE_MILESTONE.md`, which `/milestone-implement` step 1e updates for a product item at every checkpoint, is part of the allowed set), that `workflow-manager verify .` reports the installation matches 2.29.3, that `inspect` prints the new release with no refusal, and that the full test run (`python3 tools/run_tests.py`) and `python3 tools/check_docs.py` pass or fail only on the known environmental `tests.test_fake_codex_contract` cases. CP1's checkpoint commit (`/milestone-implement` step 1f) is the commit that carries the twelve Manager-written paths: they are the operator's move that this checkpoint confirms and lands, so the worker does not treat the checkpoint as having no changes, and does not treat those files as unrelated working-tree changes (the step 1e stop condition). Nothing is committed beyond what the workflow's own commit rules authorize.

**Files:** `.workflow-manager/installation.json`, `.claude/commands/accept-milestone.md`, `.claude/commands/apply-pr-review.md`, `.claude/commands/satisfy-gate.md`, `docs/ai-workflow/ORCHESTRATION_PROTOCOL.md`, `docs/ai-workflow/REVIEW_PROTOCOL.md`, `docs/ai-workflow/audit/WORKFLOW_DEFECT_LEDGER.md`, `scripts/workflow_gate_policy_test.py`, `scripts/workflow_integration_test.py`, `scripts/workflow_protocol.py`, `scripts/workflow_protocol_test.py`, `scripts/workflow_state.py`
<!-- /CP1 -->

<!-- CP2 -->
### CP2: record the move in the compatibility page and the item page

`docs/compatibility.md`: in the "Which Controller admits which Workflow" prose (the per-release entries, lines 34-36) add 2.28.0 to 2.29.3 after 2.27.0 in the list of releases that keep protocol 1.5; in the "This repository itself runs ..." paragraph (line 57) change 2.27.0 to 2.29.3 and keep the earlier releases in order, rewording its "since a `chore:` update pull request" phrasing, because this move lands through a work item's own pull request; and update the page's "Last checked with" header (line 3). Extend the entries with 2.28.0 to 2.29.3 (protocol 1.5 kept; what they add). `docs/items/workflow-controller-w1.md`: replace "Not yet planned" with the status, the plan link and the verification. `docs/update.md`: add one sentence to the subsection "In the middle of a milestone" (lines 65-70, "`IMPLEMENTING`, between checkpoints") recording that this repository's own W1 move ran at that phase with the lane stopped by `step`; change anything else in it only if the move showed a step it gets wrong. `python3 tools/check_docs.py` passes.

**Files:** `docs/compatibility.md`, `docs/items/workflow-controller-w1.md`, `docs/update.md`
<!-- /CP2 -->

## Open decisions

None of `docs/TECHNICAL_DECISIONS.md`'s "Open decision" rows is touched.

## Requirement map

| id | requirement | checkpoints |
| --- | --- | --- |
| R1 | The repository's installed Workflow is 2.29.3 through the Manager, `verify` and `inspect` clean, the suite and docs check passing | CP1 |
| R2 | The compatibility page and the item page record the move and the releases between | CP2 |

## Checkpoints table

The table below is generated from the registry; never edit it by hand.
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Confirm the repository on the moved 2.29.3 files | - | 2 | 1 |
| CP2 | Record the move in the compatibility and item pages | CP1 | 1 | 1 |
