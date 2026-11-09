# Active Milestone

## Status

**In progress.** `workflow-controller-h4e2b` (H4e2b, issue [#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315)):
plan `docs/ai-workflow/CONTROLLER_H4E2B_PLAN.md` (revision 2), four checkpoints. CP1 is complete: `harness.LOCAL_REVIEWER` is
the adapter role of `review-plan` and `review-implementation` (`routing.ADAPTER_ROLE`, `routing.LOCAL_REVIEW_ROLES`), both
adapters declare it, and `worker_launch.LAUNCHERS` is keyed by `(adapter, role)` with `CodexReviewLauncher` (whose `launch`
raises) for `(codex, local-reviewer)`, so `codex` is routable for the two review roles and still refused for every worker
role. `review_mode.worker_families` counts both roles, and the chain probe takes the entry's adapter role. The default
is unchanged. Verified with the narrow modules of the plan's CP1 list. CP2 is complete: `job._launch_job` takes a review branch
(`job._run_codex_review`) before the Codex worker's when the launcher is a `CodexReviewLauncher`; it runs
`codex_review_role.run_review` inside the job's record (the `codex_review` block through `on_progress`, `worker_process` on the
spawn, `worker_outcome`, `usage` and `usage_record` from the rollouts) and `job._codex_review_fields` maps each ending to its
status (D3's table; the Workflow's `reconcile` judges a recorded ending). `resume` (`_reconcile_codex_review`), `abandon`, the
drift ending and the post-spawn exception handler call `codex_review_role.recover`, record nothing again and leave no clone; a
review record never reaches the worker reconciler. A refused record keeps the one verdict file at mode 0600 (new primitive
`runtime.restrict_to_owner`, in `runtime.py` where the write-containment scan wants it) and names its path and SHA-256.
`tests/test_job_codex_review.py` (46 tests, both stages) covers every row of both tables; the Codex-worker job modules pass
unchanged. CP3 (the adversarial tests of the routed path) is next.

## Previous milestone (CI1c)

**Complete.** `workflow-controller-ci1c` (CI1c, issue
[#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317)): plan `docs/ai-workflow/CONTROLLER_CI1C_PLAN.md`
(revision 5), three checkpoints. CP1 is complete: the measurements are in `docs/items/workflow-controller-ci1c.md`. They were
repeated in review round 2 with the atoms divided by the planner's own `estimate_atoms` and with the 1-worker baseline
interleaved with every worker setting (the earlier ones are kept apart as superseded). Two workers cut the wall of shards 0 and
4 by 28.6% and 35.9% at +16.7% and +16.0% CPU, but one run failed in the first round (a killed worker's process group was
still there after 15 s), and D3 ends a setting at its first failure. Three workers also failed one run and missed the CPU limit
(+29.7% on shard 0, +41.1% on shard 4). No setting passes D4, so CP2 changes no file and the negative result stands; the
reading of D4(d) no longer decides it. The patch, wrapper, driver and set-up are on
`docs/items/workflow-controller-ci1c-reproduction.md`. No source file changed.

CP2 is recorded as not needed. CP3 is complete: the guides and the item page record the negative result and the D5
decisions; no code or workflow file changed. `python3 tools/check_docs.py` passes.

The milestone is implemented, functionally reviewed and accepted by policy (PR #325, checks green at the approved head).

Next action: `/milestone-plan` for `workflow-controller-h4e2b` (Codex worker, step 5c: route the two review roles, safety tests and live proof), the item `python3 scripts/workflow_backlog.py next` returns.

## Previous milestone (CI1b)

**Complete.** `workflow-controller-ci1b` (CI1b, issue
[#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317)): the plan is `docs/ai-workflow/CONTROLLER_CI1B_PLAN.md`
(revision 5), three checkpoints, all complete. CP1 split six multi-test classes along their test methods into fourteen, with
every moved body identical. CP2 refreshed `tools/test_timings.json` from three CI artifacts, added the 200 s ratchet and kept
`CI_MAX_SHARDS` at 6. CP3 updated `docs/guide/ci-and-releases.md`, `docs/guide/development.md` and the item page with the
measurements: longest-shard estimates 1439 s (6 shards), 1288 s (7), 1233 s (8); 7 is recommended and needs the owner's yes.
Verification: `tools/check_docs.py` passes; the full suite's only failures are the three known environmental
`fake_codex_contract` tests (the fake reads `~/.codex` config).
The self-review corrected the guide and the item page, which said an eighth shard buys nothing (at eight the
`acceptance_matrix` atom runs alone at 1120 s and the longest shard is 1233 s, 55 s below seven); the item page said
three artifacts were merged, but the third (run 42446936171 on `main`) is byte for byte the second, so the timings fold two;
it now records the owner's answer (asked on SignalHub during CP2, no answer within 30 minutes, so 6 stays) and the measured
extra CPU time of the repeated snapshot drives (12 more drives, about 126 s locally, about 200 s of CI CPU). Three class
definitions got their second blank line back. The D2 comparison was re-run at the head: every moved body is identical,
except the class name in the two `ReportNotificationTest` bodies, and each sibling keeps the old method order.

The milestone is implemented, functionally reviewed and accepted by policy (PR #323, checks green at the approved head).

Next action: `/milestone-plan` for `workflow-controller-ci1c` (CI: measure parallel test workers inside a shard), the item `python3 scripts/workflow_backlog.py next` returns.

## Previous milestone (CI1a)

**Complete.** `workflow-controller-ci1a` (CI1a, issue
[#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317)): a pull request that changes only documentation
skips the plan and the six test shards. The plan is `docs/ai-workflow/CONTROLLER_CI1A_PLAN.md` (revision 6). A `changes` job
runs `tools/ci_changes.py detect`, which classifies the merge's changed paths against a fixed documentation set (anything
else, or any doubt, is code); a `docs` job runs `tools/check_docs.py` and the two documentation test modules on every pull
request; `tests-result` ends with `tools/ci_changes.py require`, which fails closed unless `changes` and `docs` succeeded and
`code` is `true` or `false`. A guard test finds every reader of the documentation set by an AST scan and keeps the set honest.
The workflow files stay generated by `tools/ci_workflows.py`; `docs/guide/ci-and-releases.md`, `docs/guide/development.md`
and `README.md` describe the change. The functional review found one defect (F1, the item page was protected in the artifact
declaration), fixed in a bounded round. The live proof is the first documentation-only pull request after the merge (D8).
The milestone is implemented, functionally reviewed and accepted by policy (PR #321, checks green at the approved head).

## Previous milestone (H4e2)

**Complete.** `workflow-controller-h4e2` (H4e2, issue
[#312](https://github.com/RodrigoFAbreu/workflow-controller/issues/312)): the review recorder and the read-only run of the
two review roles, both dark (no role is routed to Codex). The plan is `docs/ai-workflow/CONTROLLER_H4E2_PLAN.md` (revision
12), two checkpoints. CP1 is complete: `controller/review_recorder.py` records a local review verdict through the Workflow's
own writers. A small driver program runs through `run_in_private_copy` beside the digest-checked script set; `check` makes the
command's guards, recomputes `bundle_id` and `review_content_id`, probes the signatures of the Workflow functions it calls and
returns the header values, gate context, round and script digests while writing nothing; `record` takes the path of a composed
verdict file, checks the state identity and the digests, repeats the guards and makes the command's write set (the feedback
file, then `record_local_plan_review` or `record_local_implementation_review` with the audit keys and the verdict text, and
for an implementation `REVISE` the Workflow's own commit of that state). A refusal is a `RecorderResult` with one cause of
`CAUSES`; the runner's own errors propagate. `run_in_private_copy` gains `real_index=False`: with `True` it uses the target's
own index and switches off every hook event a commit fires, each setting checked like the rest. The tests run the Workflow
scripts this repository has installed and compare the state and feedback with the in-place command's function sequence byte
for byte. CP2 is complete: `controller/codex_review_role.py` has `run_review`, which runs one local review role as a Codex
worker in H4e's private clone made with `prepare(read_only=True)` (a dirty managed tree is admitted and its dirty paths
returned), on a copy of the bound bundle, the verified changes file and the previous feedback, with an input manifest and
nothing copied back. `launch_codex_worker` gains `prompt=`, `review.build_local_prompt` asks for a `Status:` line and the
eight sections and no file, and the run reads the final message from `last-message.md` as bytes, composes the header from
the recorder's `check` values (`Reviewer model: openai/<model>` exactly when the gate requires distinct models) and hands it
to the recorder. The deny set (`CAUSES`: `special_file`, `submodule`, `credential_input`, `path_escape`, `input_over_bound`,
`verdict_over_bound`, `verdict_unparsed`, `credential_in_verdict`, `read_only_violated`) and D5's endings each have a test,
as does each `recover` ending; the fake `codex` gains `message_bytes`. `docs/guide/harnesses.md` has a section on the
recorder and the run, and `docs/guide/workers.md` links it. Both checkpoints are implemented; no role is routed to Codex
until H4e2b. The self-review fixed six things: the guide's link to the H4e2b page, a page this branch does not hold, failed
`tools/check_docs.py` in any clean clone (it passed only beside an uncommitted copy of `main`'s page) and now names #315; the
local prompt now says an `APPROVE` never states `Review scope: changes`, which the Workflow would refuse after the turn was paid
for; `after_check` reads an input only while it can still be the manifest's file and names an unreadable one
`read_only_violated` instead of raising; the verdict body stays byte for byte the role's (it was normalised by line splitting);
a release change while the inputs resolve is a `CodexReviewNotStarted`; and the `recorded` progress block carries the recorder's
values. The full suite then found three tests the checkpoints had broken, none of them a behaviour change: a query test's stub
of `_copy_and_execute` did not take `real_index`, the process inventory had no row for the recorder fixture's bundle generation,
and the guard on readers of a worker's prose did not know the armour scan, which now sits in one named function it lists.

Both checkpoints are implemented; the milestone is functionally reviewed and accepted by policy.

Next action: `/milestone-plan` for `workflow-controller-h4f` (Codex worker, step 6), the item `python3 scripts/workflow_backlog.py next` returns.

## Previous milestone (H4e leftovers)

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

## Earlier milestone

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

Item `workflow-controller-ci1c` (CI1c, issue [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317)),
implementation revision 3, plan `docs/ai-workflow/CONTROLLER_CI1C_PLAN.md` (revision 5). The item is an investigation with a
negative result: parallel test workers inside a shard were measured and not adopted. No source, test or workflow file changed
since the base commit `065ca1e`; the change is documentation only (`docs/guide/ci-and-releases.md`, `docs/guide/development.md`,
`docs/items/workflow-controller-ci1c.md`, the new `docs/items/workflow-controller-ci1c-reproduction.md`). There is no
user-visible command. Every flow runs offline: no network, no real `gh` (the stand-in refuses and logs), `claude`, `codex` or
`ssh`, a `PATH` of the sandbox's `bin/` and the system directories, and no write inside the read-only product clone. Run from
the product clone with `python3 -B` and no `PYTHONPATH=.`; flows write only to the scratch `TMPDIR`.

Setup: `export TMPDIR=$(mktemp -d)`. No data to seed.

**Flow 1: No code changed.**
Run `git diff --stat 065ca1e HEAD -- . ':!docs'`.
Expected: no output (nothing outside `docs/` changed, so no test, tool or workflow file).

**Flow 2: The plan is unchanged.**
Run `python3 -B tools/run_tests.py plan --profile ci --ci-placement --output $TMPDIR/p.json`.
Expected: rc 0 and `plan_digest` `1663e78f3cccedc713b19925f4cfce5eeefb24645f0a2e7e43c1f78e7651bcb1`, `shard_count` 6 (the digest
the reproduction page requires).

**Flow 3: Generated workflows and documentation.**
Run `python3 -B tools/ci_workflows.py --check; echo rc=$?` and `python3 -B tools/check_docs.py; echo rc=$?`.
Expected: `rc=0` each (links, anchors such as `#measurements-cp1`, and the task-page commands all resolve).

**Flow 4: The guides state the negative result.**
Read the paragraph after the runner table in `docs/guide/ci-and-releases.md` ("Parallel workers inside a shard...") and the
**Workers** bullet in `docs/guide/development.md`.
Expected: both say workers were measured and not used; 2 workers cut the wall 29% and 36% but a run failed (a killed worker's
process group still present after 15 s), 3 workers failed a run and raised CPU by 30% and 41%, over the 25% limit; the conformance
lane bounds the longest shard; both link to the item page's measurements.

**Flow 5: The item page is consistent.**
Read `docs/items/workflow-controller-ci1c.md` (Measurements, Superseded measurements, D5, CP2, CP3).
Expected: the table matches the guides (shard 0: 2 workers -28.6% wall / +16.7% CPU with 1 failed run; 3 workers -26.4% / +29.7%;
shard 4: 2 workers -35.9% / +16.0%; 3 workers -50.1% / +41.1% with 1 failed run); the earlier measurements are labelled superseded,
not the result; CP2 is "not needed"; test selection is not built and a larger runner waits for the owner.

**Flow 6: The reproduction page is self-sufficient.**
Read `docs/items/workflow-controller-ci1c-reproduction.md`.
Expected: the set-up, the patch (scratch clone only, never committed), `runone.py`, the drivers and `CI1C_WORKERS` are given; the
digest in the set-up equals Flow 2's.

**Known limitations / out of scope.** The measurements were made on a 16-thread desktop pinned to four threads, so only ratios
transfer to CI; the shard-count question and the second and third candidates (test selection, a larger runner) are not part of
this item; tracing the process-group assertion would be a new item. The full suite is not a flow: three `fake_codex_contract`
tests fail in a sandbox that reads `~/.codex` config (environmental) and `test_functional_sandbox` has a known CI flake (#206).

## Next action

Functional review of `workflow-controller-ci1c` (see the checklist above), then `/accept-milestone`.
