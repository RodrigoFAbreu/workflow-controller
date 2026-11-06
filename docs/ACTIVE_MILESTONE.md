# Active Milestone

## Status

**Complete.** `workflow-controller-h4e-leftovers` (H4e leftovers, issue
[#306](https://github.com/RodrigoFAbreu/workflow-controller/issues/306)). The plan is
`docs/ai-workflow/CONTROLLER_H4E_LEFTOVERS_PLAN.md` (revision 6). CP1 is complete: the landing and orphan test fixtures
give every test its own job id through one helper (`per_test_job_id` in `tests/test_job_codex_worker.py`: a `t-` prefix, the
test name's tail, a digest of the full test id and the pid), `LandingFixture` exposes `job_id`, the composed classes hand
their outer test id to the fixture before `setUp`, a unit check proves the ids legal and pairwise distinct, and a drifted
`resume` of a `LAUNCHED` protocol record has a test of its own. CP2 is complete: `controller/job.py` has `_with_kept_clone`,
which names the clone in the evidence only when a tree stands on disk, and calls it at each ending that keeps one (the
`LAUNCHED` and `COMPLETED` reconciliations, the one that raises, the drifted `resume`, the abandon, the live path's terminal
writes and both exits of a launch that never spawned); a `FINISHED` record gets a `CodexCloneKept` block; a refusal whose
clone is gone reports `clone_removed: true` with `clone: null`; the three pages say so. CP3 is complete: the two `FakeLaunchTest` failures of the full suite were not the sandbox. Two copies of the suite at once (`taskset -c 0,1`, two parallel runs) launched the same job ids (`job1`, `jobe`, ...), and the cleanup at the end of a launch kills every process whose environment carries the ownership tag, so each copy killed the other's fake `codex` (`-9` exits, `the composing command did not run`, `` `features list` did not run ``); one copy alone passes. `tests.test_review` and `tests.test_review_claude` now add the pid to every job id through `jid`, three copies together pass, and `docs/guide/development.md` says why a test that spawns tagged processes needs ids of its own. All three checkpoints are implemented. The self-review removed a `dataclasses.replace` from `_apply_codex_landing` that failed the write-containment scan, made a drifted `resume` and `--abandon` carry the "not on disk" message a `resume` refusal already had (D2 and the guide ask for it), and added the composed class `JobIdTest` had missed and the check that the fixture's `job_id` is the record's (CP1's done-when). The automated functional review found one more defect of the #306 class (flow 4): `RunnerTest.test_a_detached_descendant_is_killed_and_the_deadline_holds_with_an_inherited_pipe` set fixed ownership tags (`tTrue`, `tFalse`), so a parallel copy's cleanup killed its child before the timeout. It now adds the pid to its tags (bounded fix; three pinned copies of `tests.test_review` and of `RunnerTest` pass), and `docs/guide/development.md` names the case. Flow 4 needs re-testing; other tags in the suite were not changed by this round. Flow 4 was re-tested and passed; the milestone is implemented, functionally reviewed and accepted by policy.

Next action: `/milestone-plan` for `workflow-controller-h4e2` (Codex worker, step 5b: the four review roles on Codex), the item `python3 scripts/workflow_backlog.py next` returns.

## Previous milestone

**Complete.** `workflow-controller-h4e` (H4e, issue
[#303](https://github.com/RodrigoFAbreu/workflow-controller/issues/303)) prepares a private clone for a Codex worker and lands
its commits fast-forward behind the H3 seam, for a fixture role. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_WORKER_CLONE_PLAN.md` (revision 8). CP1 is complete: `controller/codex_worker_tree.py` has
`prepare` (D1, D2: a separate clone with no remote, no alternates and no hooks, the fixed `Codex worker` author, checked out
at the commit the Controller captured, refused with a named `CodexTreeError.cause` unless the managed branch is clean and
unmoved), `gitrepo` gains `untracked_files`, `checkout_branch_at`, `config_probe` and the `no_optional_locks` reads, and
`job._run_codex_worker` passes `pre_state["target_head"]` to the preparer seam, persists the `clone` block before the
launch and removes the tree when the worker never ran. CP2 is complete: `controller/codex_worker_landing.py` has `land`
(D3, D4: the checks in order, each refusal naming a `cause`, then a hardened fetch into `refs/controller/landing/<job id>` and
`merge --ff-only --no-overwrite-ignore`), `verify_move` (the one post-move check) and `decide`/`recover` (D7's deciding half,
which only reads); `gitrepo` gains `LANDING_CONFIG`, `landing_fetch`, `landing_merge`, `landing_delete_ref` (the one stated
exception to "never deletes a ref", admitted by exactly one spelling in `tests/test_no_rewrite_invariants.py`) and the clone
reads with the commit graph off. CP3 is complete: `controller/job.py` calls the `job.CODEX_LANDER` seam (default `land`) after the
`COMPLETED` write, writes the intent block before the fetch and the final `landing` block after, ends a refused landing
`FAILED` `CodexLandingRefused` in one write with the clone kept and named in `reconciliation_evidence.clone`, and settles the job
directory before the terminal write (the clone removed on `landed`, `nothing`, an ordinary failure and a launcher refusal before
the spawn, kept after a refusal or an exception once the worker was spawned). `resume`, a drifted `resume` and
`resume --abandon` share one applying part of D7's recovery helper (`_apply_codex_landing`: the final block, the landing ref
deleted, the tree removed only where its commits landed or there were none, `tree_left` when a removal fails), and a `LAUNCHED`
Codex record ends `INTERRUPTED` naming its clone. CP4 is complete: `tests/test_codex_worker_clone_live.py` is the opt-in live
proof (skipped by default and on CI with its reason; with `CONTROLLER_LIVE_CODEX_TESTS=1` a real `codex exec` made one plain
commit in the real preparer's clone and the real lander fast-forwarded it onto the managed branch with the `Codex worker`
author, the clone removed and no landing ref left; the job ended `FAILED` `phase_not_in_to_any_of` by the unchanged rules), its
measured result is `tests/harness_contract/codex/worker_clone.txt`, and the guides
(`docs/guide/harnesses.md`, `docs/guide/workers.md`, `docs/guide/development.md`) and `docs/common-problems.md` state the clone,
the landing, the record blocks, the cleanup, the crash windows and the operator's removal of a kept clone in place. The
item pages are corrected (no retention) and H4e2's page records the open question of the clean-tree requirement. All four
checkpoints are implemented. The self-review fixed a commit that removes or replaces a gitlink the base holds, which landed
because `gitrepo.commit_touches_submodule` read the old mode with its leading `:`; a `ref_left` block now names the real ref,
not a `<job id>` placeholder; a `landing_unreadable` block names the clone and `reconciliation_evidence.clone` is the path
derived from the job id when the `clone` block has no usable one; `extra_refs` and `managed_hooks_configured` name what they
found; and the guides say what a `.gitignore` edit does, which Git the hook suppression was measured on, and that
`landing_unreadable` deletes no ref.

The milestone is implemented, functionally reviewed and accepted by policy.

Next action: `/milestone-plan` for H4e2 (Codex worker, step 5b: the four review roles on Codex), the item `python3 scripts/workflow_backlog.py next` returns.

Previous item, now complete:

**Complete.** `workflow-controller-a2b` (A2b, issue
[#298](https://github.com/RodrigoFAbreu/workflow-controller/issues/298)) moves the roadmap prose to item pages and the rules to a
live page. The plan is `docs/ai-workflow/CONTROLLER_A2B_PROSE_MIGRATION_PLAN.md` (revision 3). CP1 is complete: the 72 done
items have a page `docs/items/<id>.md` holding their archive row verbatim (less the status marker and the link rewrite), each
named by a `detail` line in `docs/backlog.yaml`; `tools/check_docs.py` link-checks `docs/items/`, and `tests/test_backlog.py`
pins that every `detail` resolves to a regular file. CP2 is complete: the 25 open items and ideas have a page too (97 pages in
all, each row's text found in its page), CL1a to CL1e and C12b to C12d carry a "Planning notes" section taken from issues
#130 and #27 and the archive's follow-ups 8, 9 and 14, the one dangling title (C10g-3c2) is a complete phrase, and the comment on
line 2 of `docs/backlog.yaml` is reworded. CP3 is complete: `docs/planning-rules.md` holds the archive's rules text (the clean-up limit and sizing rule, the deferred directions, 11.11, 11.12 and the principles) unchanged, the archive's banner and `docs/README.md` link it, `docs/guide/development.md` and `docs/guide/milestone-branches.md` point at it, the five release notes (1.4.1 to 1.7.0) link the archive section, and the selection test in `tests/test_backlog.py` builds its two-ready-item case in a copy. The self-review corrected the C10g-3c2 page's heading to the completed title, linked the rules from each open item's page, kept the CL1d and CL1e notes to what issue #130 says, made the rules page say its numbered list is frozen, and added the item pages to `docs/guide/development.md` and `docs/README.md`.

Previous item, now complete:

**Complete.** `workflow-controller-a2a` (A2a, issue
[#294](https://github.com/RodrigoFAbreu/workflow-controller/issues/294)) declares the backlog file and replaces the roadmap page
with the generated one. The plan is `docs/ai-workflow/CONTROLLER_A2A_BACKLOG_PLAN.md` (revision 3). CP1 is complete:
`docs/backlog.yaml` lists the 97 items of the roadmap's Controller and orchestrator tables (71 done, A2a in progress, the rest
next or idea), and `tests/test_backlog.py` pins the file against the roadmap rows and the Workflow's selection rule. CP2 is
complete: `WORKFLOW_CONFIG.json` declares the backlog, the old roadmap is kept byte for byte in `docs/roadmap-archive.md`
under a frozen banner, `docs/ROADMAP.md` is the generated page, the six inbound links and the follow-up 86 test point at the
archive, and `tests/test_backlog.py` fails on a stale page. CP3 is complete: `docs/guide/development.md` explains the backlog,
`docs/README.md` lists the archive, and `docs/guide/milestone-branches.md` sends a split-off remainder to the backlog file.

The milestone is implemented, functionally reviewed and accepted by policy.

Next action: `/milestone-plan` for A2b ("The prose migration"), the next item `python3 scripts/workflow_backlog.py next` returns.


Previous item, now complete:

**Complete.** `workflow-controller-bold-flow-headers` (FL3, issue
[#288](https://github.com/RodrigoFAbreu/workflow-controller/issues/288)) makes the functional checklist reader read a bold
`**Flow K: Title.**` header as a flow and refuse a bold `Flow ...` line that no shape reads, instead of dropping it next to a
recognised flow. The plan is `docs/ai-workflow/CONTROLLER_BOLD_FLOW_HEADERS_PLAN.md` (revision 3). The reader change is in
`controller/functional_review.py`, with tests in `tests/test_functional_review.py`; `docs/guide/automation.md` and
`docs/common-problems.md` say what is read and refused.

The milestone is implemented, functionally reviewed and accepted by policy (release 1.27.10).

Next action: `/milestone-plan` for A2a ([#294](https://github.com/RodrigoFAbreu/workflow-controller/issues/294), declare the backlog file and replace the roadmap page with the generated one), then A2b, the next incomplete item in `docs/ROADMAP.md`.

Previous item, now complete:

**Complete.** `workflow-controller-remove-tree-links` (C10k, issue
[#140](https://github.com/RodrigoFAbreu/workflow-controller/issues/140)) makes `runtime.remove_tree` refuse a path below a
symbolic link. The plan is `docs/ai-workflow/CONTROLLER_REMOVE_TREE_LINKS_PLAN.md` (revision 2). CP1 is complete: the removal
walks the components above the last one and raises `RuntimeContainmentError` for a link or a `..` among them before anything is
removed; a Codex review of a worktree whose `.agents` is a link now fails at `build_copy` (record `FAILED`, `outcome: error`,
`not_started`). CP2 is complete: the roadmap marks C10k and follow-up 84 done, and `docs/common-problems.md` names the
linked-`.agents` refusal with its cause and remedy.

The milestone is implemented, functionally reviewed and accepted by policy (release 1.27.9).

Next action: `/milestone-plan` for FL3 ([#288](https://github.com/RodrigoFAbreu/workflow-controller/issues/288), the bold `Flow N` header drop in the checklist reader), then CL1a ([#130](https://github.com/RodrigoFAbreu/workflow-controller/issues/130), the retention rule for finished work-item files), the next incomplete item in `docs/ROADMAP.md`.

Previous item, now complete:

**Complete.** `workflow-controller-checklist-flow-ends` (C10j, issue
[#116](https://github.com/RodrigoFAbreu/workflow-controller/issues/116)) closes the gaps in how the functional checklist
recogniser finds where a flow ends. The plan is `docs/ai-workflow/CONTROLLER_CHECKLIST_FLOW_ENDS_PLAN.md` (revision 2). CP1 is
complete: a qualified flow header (`**Optional K. Title.**`, `**Then, 2. Run it again.**`) starts a flow and ends the one before
it, and text in a gap between two flows is refused instead of being read by no flow. CP2 is complete: a link above the
checklist's folder (`../tests/checklist.md`) is followed when the joined path stays inside the repository, and the
missing-section refusal lists the near-miss headings. CP3 is complete: `docs/guide/automation.md` and `docs/common-problems.md`
say what the recogniser reads and refuses, and the roadmap marks C10j done (1.27.8).

The milestone is implemented, functionally reviewed and accepted by policy (release 1.27.8).

Next action: `/milestone-plan` for C10k (the `remove_tree` point of [#140](https://github.com/RodrigoFAbreu/workflow-controller/issues/140)), the next incomplete item in `docs/ROADMAP.md`.

Previous item, now complete:

**Complete.** `workflow-controller-sandbox-precheck` (C10i, issue
[#112](https://github.com/RodrigoFAbreu/workflow-controller/issues/112)) checks the functional tester's availability before
usage is reserved. The plan is `docs/ai-workflow/CONTROLLER_SANDBOX_PRECHECK_PLAN.md` (revision 2). CP1 is complete:
`review.claude_selection_error` returns the refusal a launch would raise for the host causes that need no job and no sandbox
(the refused role, the unresolved binary and the static probe, each with a `cause`), `launch_functional` shares its helper so the
selection and the launch cannot drift apart, `_tester_probe` uses it for a Claude entry and `_run_functional` runs the probe
for a one-entry chain before the usage gate, so a host that cannot run the tester ends at the one `GATE_BLOCKED` record with no
reservation taken, and a Claude entry that fails the probe is skipped in a chain. CP2 is complete: `docs/common-problems.md`
and `docs/guide/automation.md` say the host causes are found before any usage is reserved and name the causes that remain
launch-time, and the roadmap marks C10i done (1.27.7).

The milestone is implemented, functionally reviewed and accepted by policy (release 1.27.7).

Next action: `/milestone-plan` for C10j ([#116](https://github.com/RodrigoFAbreu/workflow-controller/issues/116)), the next incomplete item in `docs/ROADMAP.md`.

Previous item, now complete:

**Complete.** `workflow-controller-tester-read-denies` (C10h, issue
[#115](https://github.com/RodrigoFAbreu/workflow-controller/issues/115)) closes the Claude functional tester's read path to
credentials, reads a `file://` origin and bounds the tester's summary. The plan is
`docs/ai-workflow/CONTROLLER_TESTER_READ_DENIES_PLAN.md` (revision 4). CP1 is complete: `review._local_origin_refs` reads a
`file://` origin as a local path (percent-decoded, the authority dropped as Git does; no path is an `OSError`, an `origin:`
breach). CP2 is complete: `functional_review.validate_results` cuts a summary to `SUMMARY_LIMIT` (2000) characters plus a
marker naming the omitted count. CP3 is complete: `functional_review.read_denies` builds the `denyRead` list (the job home's
`.credentials.json`, `.claude.json` and `backups`, the operator's Claude home and `$HOME/.claude.json`, each with its resolved
form when that differs), which `tester_settings`, the launch and `claude-build verify` all carry; the layout invariant covers
both deny lists, `check_functional_settings` requires the exact list, and a runtime root inside the operator's Claude home is
refused before the sandbox is built (`functional_tester_unavailable`; `BuildNotVerified("host", ...)` for verify). The live
probe (`CONTROLLER_LIVE_CLAUDE_TESTS=1`, Claude Code 2.1.310, 2026-11-03) passed: no canary or credential reached the shell,
and the readable job-home entries were `.last-cleanup`, `projects`, `session-env`, `sessions` and `shell-snapshots`. The
runtime-root credential copies are the remainder (roadmap follow-up 86).

The milestone is implemented, functionally reviewed and accepted by policy (release 1.27.6).

Next action: `/milestone-plan` for C10i ([#112](https://github.com/RodrigoFAbreu/workflow-controller/issues/112)), the next incomplete item in `docs/ROADMAP.md`.

Previous item, now complete:

**Complete.** `workflow-controller-cleanup-3c` (CU3c, issues
[#103](https://github.com/RodrigoFAbreu/workflow-controller/issues/103),
[#104](https://github.com/RodrigoFAbreu/workflow-controller/issues/104) and
[#108](https://github.com/RodrigoFAbreu/workflow-controller/issues/108)) fills the price table, tidies the usage record and
shows the round caps in force in `settings show`. The plan is `docs/ai-workflow/CONTROLLER_CLEANUP_3C_PLAN.md` (revision 5).
CP1 is complete: `controller/prices.json` is version 2 and holds the API list prices of `gpt-6.1-sol`, `claude-opus-5-5`,
`claude-sonnet-5-5` and `claude-haiku-5-5`, read from the providers' pages on 2026-11-03 (lower prompt-length tier; Claude's
shortest-duration cache write: five minutes for Claude, 30 minutes for OpenAI), a test requires a complete price for every default model, and the
guides label a cost as an API list-price equivalent. CP2 is complete: partly reported reasoning is `unknown`, a rollout that is
not a mapping is skipped, the `wall_ms` source is documented per job kind, and a retried Codex review is tested end to end.
CP3 is complete: `settings show` prints `review.<local|external>_round_cap in force = N (<origin>)` after the three stored
rows (and `round_caps_in_force` with `--json`) from `cli._round_caps_in_force`, the cap stop says "round N" ("local round N"
or "external round N" for a split count), and the roadmap marks CU3c done.

The milestone is implemented, functionally reviewed and accepted by policy (release 1.27.5).

Next action: `/milestone-plan` for C10h ([#115](https://github.com/RodrigoFAbreu/workflow-controller/issues/115)), the next incomplete item in `docs/ROADMAP.md`.

Previous item, now complete:

**Complete.** `workflow-controller-checklist-cross-shape` (FL2, issue
[#271](https://github.com/RodrigoFAbreu/workflow-controller/issues/271)) makes the functional checklist recogniser refuse a
flow that another shape recognised and the deciding shape would drop silently. The plan is
`docs/ai-workflow/CONTROLLER_CHECKLIST_CROSS_SHAPE_PLAN.md`. CP1 is complete: `_flows_of_lines` keeps what the bold, heading
and numbered finders found (also when the table decides) and, after FL1's checks, `_unaccounted` accepts an item only when it
starts inside a flow that was read, holds a row of the table that decided, or lies in a non-flow region (`_nonflow_regions`:
`Setup`, `Test data`, `Known limitations`, `Out of scope`, not `Flows`); otherwise `_dropped` raises `checklist_unrecognised`
with the evidence `shape`, `recognised`, `total`, `unrecognised` and `dropped`. `CrossShapeTest` pins both cases of the issue
and the position rule; the refusal tests fail on the base commit. The history measurement (D6, 392 texts, 64 distinct
sections, 63 read on the base) found no checklist newly refused or read differently. CP2 is complete: `gate_text` for `checklist_unrecognised` states that a flow another shape recognised must lie inside a flow
of the shape used (or hold a table's rows), `tests/test_functional_lifecycle.py` pins the stop for a heading beside a bold flow
(no job, sandbox or reservation), the automation guide states the rule, the two problem pages name the case and the roadmap
marks FL2 done (1.27.4).

The milestone is implemented, functionally reviewed and accepted by policy.

Previous item, now complete:

**Complete.** `workflow-controller-checklist-recogniser` (FL1, issue
[#227](https://github.com/RodrigoFAbreu/workflow-controller/issues/227)) makes the functional checklist recogniser read
`F1`, `3a`, `6.1` and `6b-split` style labels and refuse a partly recognised checklist. The plan is
`docs/ai-workflow/CONTROLLER_CHECKLIST_RECOGNISER_PLAN.md`. CP1 is complete: `controller/functional_review.py` accepts the D2
label in the four shapes (the table first cell, the bold and heading label, the numbered item, `### Flow 6.1`), each shape
returns its recognised items, their line spans and the items that look like a flow and are not, and `_flows_of_lines`
follows D7's four steps, so a shape that recognises some items and not others raises `checklist_unrecognised` with
`shape`, `recognised`, `total` and `unrecognised` (and `first_cells` when a table is seen and nothing is found) instead of
returning the partial list. `tests/test_functional_review.py` covers the grammar, completeness and shape choice, and the two
Manager checklists are fixtures. The history measurement (D6) found no new refusal; the checklists it reads differently are
`settings-and-telemetry` (16 flows to 29, `B1`..`B6` and the others read as their own flows), `adaptive-test-sharding` (13 to 17,
the rows `L1`..`L4` the old reader dropped) and `usage-limit-mid-job` (9 to 10, the row `7b`), none losing a line of text. CP2
is complete: `gate_text` for `checklist_unrecognised` says "a flow label" and opens "the functional review checklist is only
partly recognised" when the refusal carried `recognised` (`job._FunctionalPlan.partial`), `tests/test_functional_lifecycle.py`
asserts both leads, `docs/guide/automation.md` states the label with a table of examples and the refusal of a partly
recognised checklist, `docs/common-problems.md` and `docs/guide/troubleshooting.md` name the new case, and `docs/ROADMAP.md`
marks FL1 done (a `fix:` patch, 1.27.3, no new setting). The milestone is implemented, functionally reviewed and accepted by policy.

**Complete.** `workflow-controller-codex-worker-usage` (H4d, issue
[#266](https://github.com/RodrigoFAbreu/workflow-controller/issues/266)) admits a Codex worker job against the Codex usage
budget, accounts it and holds it on its usage limit, for a fixture role. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_WORKER_USAGE_PLAN.md`. CP1 is complete: `execute_step` admits a Codex launcher with
`usage_gate(route, provider=usage.PROVIDER_CODEX)` (a Claude launcher keeps the unchanged call), the refusal of a record
that carries `usage` is gone, `_run_codex_worker` renews the reservation with the same ticker, and `_launch_job` writes the
`usage` end block from the readings of the job's own Codex home, so the job is accounted as any worker;
`tests/test_job_codex_worker_usage.py` covers admission, a hold, accounting, a never-started job, a run failing after the
spawn, a chain selecting Codex, the ticker and `resume` settling an orphan. CP2 is complete: when Codex's own limit record
classified the run, `codex_worker_job.limit_fact` derives `stream_diagnosis.usage_limit` from the job's readings (a window at
100 percent or more, the latest `resets_at`, the weekly window on a tie; none, and so an ordinary failure, when no window
shows it; none on a breach), so the existing INTERRUPTED rule, `run`'s wait loop and `step`'s exit 17 apply unchanged, and
`cli._mid_job_hold` names the provider of the record's routed harness (Claude when none); `tests/test_job_codex_worker_limit.py`
covers it. CP3 is complete: `tests/test_codex_worker_usage_live.py` is the opt-in live proof (one real Codex turn through `job.execute_step` under a real gate over a scratch runtime; skipped by default and on CI, with its reason; the capture `tests/harness_contract/codex/worker_usage.txt` records the measured run: admitted on provider `codex` with a token and a reservation, finished with a `usage` block `done` and one ledger entry of provider `codex`), the harness, worker and development guides state the admission, the accounting and the mid-job hold, and the roadmap marks H4d done (`chore:`, no release; H4e still needs the owner decision on the tree). Nothing real reaches the branch. The milestone is implemented, functionally reviewed and accepted by policy.

Next action: `/milestone-plan` for FL1 ([#227](https://github.com/RodrigoFAbreu/workflow-controller/issues/227), the functional checklist recogniser), then CU3c (H4e still needs the owner decision on the Codex tree and how its commits land). The previous item is recorded below.

Previous item, now complete:

**Complete.** `workflow-controller-codex-worker-ownership` (H4c2, issue
[#263](https://github.com/RodrigoFAbreu/workflow-controller/issues/263)) owns, judges and reaps a Codex worker's process on
`resume` and `--abandon`. The plan is `docs/ai-workflow/CONTROLLER_CODEX_WORKER_OWNERSHIP_PLAN.md`. CP1 is complete:
`_run_codex_worker` persists a `codex_worker` block (operator home, captured credential digest, `lock_inherited: false`)
before the tree is prepared; `job._is_codex_worker_record` is the predicate; `resume`'s phase 1 never re-attaches to such a
record; and `_reconcile_codex_worker` ends the group and every tagged process, settles the credentials and hands the record
to the shared `LAUNCHED`/`COMPLETED` rows (under a drifted installation nothing changes). `tests/test_job_codex_worker_resume.py`
drives it with real test-owned processes (`process_fixtures.spawn_tagged_worker`). CP2 is complete: `resume --abandon` of a
Codex record follows Claude's rules (an `active` worker or a live owned process is refused naming `resume`, which ends a Codex
orphan; with the target root gone the refusal names the pid, pgid and by-hand end and then `resume --abandon`; an
`unverifiable` worker needs the acknowledgement; an `inactive` or acknowledged one is marked `FAILED` after the credentials are
settled), and `tests/test_job_codex_worker_interrupt.py` covers the interrupts before the spawn and a child Controller killed
with `SIGINT` (the launcher reaps) and `SIGKILL` (`resume` ends the orphan and its descendant). CP3 is complete:
`tests/test_codex_worker_ownership_live.py` is the opt-in live proof (a child Controller runs a real worker and is killed with
`SIGKILL`, then `resume` must leave no process of the job's tag; skipped by default and on CI, with its reason; the capture
`tests/harness_contract/codex/worker_ownership.txt` records the measured run of the self-review: the real worker and a second
tagged process survived the killed Controller, and `resume` ended both and reconciled the record `INTERRUPTED`), the harness, worker and development guides
state what `resume` and `--abandon` do with a Codex record and why it is not re-attached to, and the roadmap marks H4c2 done
(`chore:`, no release). Nothing real reaches the branch. The milestone is implemented, functionally reviewed and accepted by policy.

Next action: `/milestone-plan` for H4d ([#266](https://github.com/RodrigoFAbreu/workflow-controller/issues/266), usage windows and limit holds for Codex worker jobs), the next incomplete item on
`docs/ROADMAP.md`. The previous item is recorded below.

Previous item, now complete:

**Complete.** `workflow-controller-codex-worker-job-record` (H4c1, issue
[#260](https://github.com/RodrigoFAbreu/workflow-controller/issues/260)) records a Codex worker run as a job. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_WORKER_JOB_RECORD_PLAN.md`. CP1 is complete: `controller/codex_worker_job.py` has the pure
`launch_options`, `stream_texts` and `usage_builder`, with `tests/test_codex_worker_job.py`. CP2 is complete: `controller/job.py` has
the launcher-keyed Codex branch (`_is_codex_launcher`, `_run_codex_worker`, the usage-gate skip and the Codex completion fields),
the `CODEX_WORKTREE_PREPARER` (default `None`, so the branch refuses before the launch) and `CODEX_HOME_RESOLVER` seams, and
`runtime.fill_log_file` writes the two stream files; `tests/test_job_codex_worker.py` drives it through `execute_step` with the
fake Codex. CP3 is complete: `tests/test_codex_worker_job_live.py` is the opt-in live proof (its measured run is
`tests/harness_contract/codex/worker_job.txt`), and the harness, worker and development guides and the roadmap state the branch, its
limits (the tree and landing are undecided, ownership is H4c2) in place. Nothing real reaches the branch. The milestone is
implemented, functionally reviewed and accepted by policy.

Next action: `/milestone-plan` for H4c2 ([#263](https://github.com/RodrigoFAbreu/workflow-controller/issues/263), ownership, liveness and abandon; parent #260), the next incomplete item on
`docs/ROADMAP.md`. The previous item is recorded below.

**Complete.** `workflow-controller-codex-worker-launch` (H4b2, issue
[#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_WORKER_LAUNCH_PLAN.md`. `controller/codex_worker.py` has `launch_codex_worker` (headless
one-shot `codex exec` in the job's worktree under the H4b1 sandbox, on a build verified by `verify-codex`), `classify`, the
settling and `CodexLauncher`, with the worker's composed-configuration check and the nested-repository refusal in
`controller/codex_worker_sandbox.py`. It is registered for a fixture role only: `CODEX.roles` and `worker_launch.LAUNCHERS` are
unchanged. `tests/test_codex_worker_live.py` is opt-in and its measured run is recorded in
`tests/harness_contract/codex/worker_launch.txt`. The guides and the roadmap state the launcher in place; the change is a
`chore:` with no release. A cross-shard tag collision in `tests/test_codex_tester.py` is pre-existing and reported for a
follow-up issue.

Next action: `/milestone-plan` for H4c ([#260](https://github.com/RodrigoFAbreu/workflow-controller/issues/260), the Codex worker's job record, usage and reaping), the next incomplete item on `docs/ROADMAP.md`. The previous item is recorded below.

**Complete.** `workflow-controller-codex-worker-sandbox` (H4b1, issue
[#252](https://github.com/RodrigoFAbreu/workflow-controller/issues/252)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_WORKER_SANDBOX_PLAN.md`. CP1 is complete: `controller/codex_worker_sandbox.py` has
`WorkerLayout`, `check_worktree`, the `worker` profile (worktree and scratch writable, `.git/hooks` and `.git/config`
read, network off), the pinned `codex exec` line, `check_home`, `check_launch` and `build`, with
`tests/test_codex_worker_sandbox.py`. CP2 is complete: the module also has the worker probes (`probe_command`, `probe_prompt`,
`judge`, `sandbox_probe_argv`) and the git guard (`guard_digest`, `check_guard`), which detects any change under `.git`
other than what a commit or a plain `git gc` writes, and refuses a symlink, a special file or an alternates file there.
CP3 is complete: `tests/test_codex_worker_sandbox_live.py` (opt-in, skipped by default and on CI) ran the identical probe
command under `codex sandbox -P worker` and one `codex exec` turn on `codex-cli 0.163.0`, and both passed
(`tests/harness_contract/codex/worker_sandbox.txt`). The live run found a defect in CP1's profile: Codex keeps `.git`
read-only inside a writable root unless it is listed, so `git commit` failed on `index.lock`; the profile now lists the
worktree's `.git` as `write`, and the nested read-only `.git/hooks` and `.git/config` entries then held (a write to either
was refused). The harness and worker guides and the roadmap (H4b split into H4b1, done, and H4b2) state it in place.
Nothing calls the module; the change is a `chore:` with no release. The self-review's full suite found two hygiene
guards the new tests broke: `tests/test_codex_worker_sandbox.py` now creates its repositories through
`fixtures.git_init`/`git_clone`, and `tests/process_fixtures.py`'s `LEFTOVER_INVENTORY` has a row for each new test file.

Next action: `/milestone-plan` for H4b2 ([#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255), the Codex
worker launcher, which also closes the composed-configuration and nested-repository limits). The previous item is recorded below.

**Complete.** `workflow-controller-orchestrator-stops` (NF3, issue
[#244](https://github.com/RodrigoFAbreu/workflow-controller/issues/244)) is implemented (CP1 and CP2 and the functional review) and accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_ORCHESTRATOR_STOPS_PLAN.md`. A gate record
carries `protocol_gate` with `row`, `disposition`, `action_id`, `reason_code` and the Controller's own `code`, and
`derive_job_event` announces a stop the orchestrator handles as `orchestrator_wait` (a wait) or `lane_blocked` (a repair)
instead of `user_gate`; every other, unknown or legacy stop stays `user_gate`. The automation and commands guides, ADR 0012
and the roadmap state the rule in place. The self-review changed one test: the scenario
`scenario-codex-review-implementation-stage-limit` stops at a `gate_evidence_unmet` technical approval gate, so it now
expects `lane_blocked` there, not `user_gate` (D2). The change is a `fix:`, a patch release.

Next action: `/milestone-plan` for H4b ([#252](https://github.com/RodrigoFAbreu/workflow-controller/issues/252), the Codex worker launch), the next incomplete item on `docs/ROADMAP.md`. The previous item is recorded below.

**Complete.** `workflow-controller-user-only-scan` (TK1, issue
[#247](https://github.com/RodrigoFAbreu/workflow-controller/issues/247)) is implemented (CP1 and CP2). The plan is
`docs/ai-workflow/CONTROLLER_USER_ONLY_SCAN_PLAN.md`. `worker._task_tokens` splits a task on `=` and on every character a
name or path cannot contain, so the Claude launch scan and the Codex renderer find a user-only command name wrapped in `!`,
`?`, brackets, quotes or markup, or behind `=`; a name inside a longer word or path is still not refused. The workers guide
and the roadmap state the rule in place. The change is a `fix:`, a patch release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-command-render` (H4a, issue
[#242](https://github.com/RodrigoFAbreu/workflow-controller/issues/242)) is implemented (CP1 and CP2 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_COMMAND_RENDER_PLAN.md`. `controller/command_files.py` adds
`render_command`, a pure function that renders a shared command file and an argument string into a Codex prompt and
refuses the user-only commands (`tests/test_command_files.py`). Nothing calls it. The harness and worker guides and the roadmap state it in place; the change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-fallback` (C10g-3c2, issue
[#236](https://github.com/RodrigoFAbreu/workflow-controller/issues/236)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_FALLBACK_PLAN.md`. A `functional-tester` chain longer than one is walked before the
spawn (`codex_tester.selection_error`, `job._tester_probe`), a Codex tester has a `usage_record` from its rollouts and a
`telemetry` block, and `tests/test_codex_tester_live.py` has a second opt-in test through `_functional_launch`. The first
real round (31 October 2026: `gpt-6.1-sol`, effort `high`, `job.execute_step` on a scratch item at row 38e) ran to
`findings` with a correct report, credentials `unchanged`, the sandbox removed, 12,594 input, 46,080 cache-read, 1,061
output and 93 reasoning tokens in `usage_record` and a `telemetry` block with harness `codex`; no defect was found. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-routing` (C10g-3c1, issue
[#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223)) is implemented (CP1 to CP3 and the functional review) and
accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_ROUTING_PLAN.md`. Routing accepts `codex` for the
`functional-tester` role (the model `gpt-6.1-sol` and an effort `high` or `xhigh` named, no Codex defaults), and
`job._run_functional` runs the launch, usage provider, credential and record fields of the route's harness;
`cli._functional_options` carries both harness homes and binaries. The guides, the table and the roadmap state it in place. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-surroundings` (C10g-3b2b, issue
[#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)) is implemented (CP1 to CP3 and the functional
review) and accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_SURROUNDINGS_PLAN.md`. Codex declares the
tester role with `verify-codex`, routing refuses the pair until C10g-3c, and `codex_tester.launch_codex_functional` adds the
build gate, the before/after snapshots, the binding check and the refusal mapping, with `tests/test_codex_tester_launch.py`;
the guides, the generated table and the roadmap state the new facts in place. Nothing calls the launch until C10g-3c; the
change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3, the self-review fixes and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Functional review checklist

Item `workflow-controller-h4e-leftovers` (H4e leftovers, issue [#306](https://github.com/RodrigoFAbreu/workflow-controller/issues/306)),
implementation revision 2. The change makes the landing, orphan and review-launch tests use job ids of their own (so parallel
copies of the suite stop killing each other's fakes), names the kept Codex worker clone in every ending that leaves one on disk
(`_with_kept_clone` in `controller/job.py`; a `FINISHED` record gets a `CodexCloneKept` block; a refusal whose clone is gone
reports `clone_removed: true`, `clone: null`), and says so in `docs/guide/harnesses.md`, `docs/guide/workers.md`,
`docs/common-problems.md` and `docs/guide/development.md`. There is no user-visible command; the flows are the unit modules. Every
flow runs offline: no network, no real `gh` (the stand-in refuses and logs), `claude`, `codex` or `ssh`, a `PATH` of the
sandbox's `bin/` and the system directories, and no write inside the read-only product clone. Run from the product clone, one
module at a time, with `python3 -B` and no `PYTHONPATH=.`; they write only to the scratch `TMPDIR`.

Setup: `export TMPDIR=$(mktemp -d)`. No data to seed.

**Flow 1: The landing and the job seam.**
Run `python3 -B -m unittest tests.test_job_codex_worker_landing 2>&1 | tail -3` and the same for `tests.test_job_codex_worker`.
Expected: `Ran 28 tests` and `OK` for each (the fixture job ids are legal and pairwise distinct; the kept clone is named at each
ending).

**Flow 2: Resume, abandon and the drifted resume.**
Run `python3 -B -m unittest tests.test_job_codex_worker_resume 2>&1 | tail -3` (about 30 s).
Expected: `Ran 77 tests` and `OK` (a `LAUNCHED` record names its kept clone; a record whose tree is gone names none and says
`clone_removed: true`; a drifted `resume` and `--abandon` carry the "not on disk" message; a `FINISHED` record carries
`CodexCloneKept`).

**Flow 3: The review launch tests.**
Run `python3 -B -m unittest tests.test_review 2>&1 | tail -3` and `tests.test_review_claude`.
Expected: `Ran 127 tests` and `Ran 39 tests`, each `OK`.

**Flow 4: Parallel copies no longer kill each other's fakes (the #306 defect).**
Revision 2 re-tests this flow: the first functional review found `RunnerTest.test_a_detached_descendant_is_killed_and_the_deadline_holds_with_an_inherited_pipe`
still used fixed ownership tags (`tTrue`, `tFalse`); it now adds the pid to them. It runs inside `tests.test_review`, so the command below covers it.
Start three copies of Flow 3 together with a CPU pin and wait for all:
`for i in 1 2 3; do (TMPDIR=$(mktemp -d) taskset -c 0,1 python3 -B -m unittest tests.test_review tests.test_review_claude 2>&1 | tail -3) & done; wait`.
Expected: every copy prints `Ran 166 tests` and `OK` (no `-9` exits, no `the composing command did not run`, no `` `features list` did not run ``). If
`taskset` is missing from the sandbox's `PATH`, this flow is `environment`-limited and operator-run; any other failure is a finding.

**Flow 5: Documentation and backlog.**
Run `python3 -B tools/check_docs.py; echo rc=$?` and `python3 -B scripts/workflow_backlog.py check; echo rc=$?`.
Expected: `rc=0` each; the backlog line is `{"declared": true, "fresh": true, "roadmap": "docs/ROADMAP.md"}`.

**Flow 6: The guides state the new behaviour.**
Read the clone and landing sections of `docs/guide/harnesses.md`, `docs/guide/workers.md`, the "refused landing" entry of
`docs/common-problems.md` and the "Job ids in tests that spawn tagged processes" bullet of `docs/guide/development.md`.
Expected: every ending that keeps a clone names it (`reconciliation_evidence.clone`, or `CodexCloneKept` for a `FINISHED`
record); a clone not on disk is reported as `clone_removed: true`, `clone: null`; the one remaining window (a kill between the
terminal write and the naming write) is stated; the development guide says why such tests need ids of their own and how to check
(`taskset -c 0,1` with two or three copies).

**Known limitations / out of scope.** Routing any role to Codex (H4e2); `milestone-implement` on Codex (H4f); a retention or pruning
command for kept clones; the kill window between a terminal record and its second naming write (documented, not closed); any
change to the Claude worker, the tester or the reviewer. The full suite is not a flow (`test_functional_sandbox` has a known CI
flake, #206; the Git 2.56 trailer tests fail on main).

## Next action

`/milestone-plan` for CL1a ([#130](https://github.com/RodrigoFAbreu/workflow-controller/issues/130)), the next incomplete item in `docs/ROADMAP.md`.
