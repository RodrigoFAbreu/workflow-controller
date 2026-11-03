# Active Milestone

## Status

**Implemented, awaiting implementation review.** `workflow-controller-cleanup-3c` (CU3c, issues
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

Next action: the implementation review of CU3c.

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

Item `workflow-controller-checklist-cross-shape` (FL2, issue [#271](https://github.com/RodrigoFAbreu/workflow-controller/issues/271)),
implementation revision 1. The change is a pure parser (`controller/functional_review.py`) plus the words of one gate text, so every
flow is offline and unit-level: no network, `gh`, `claude`, `codex` or `ssh`, and no flow writes inside the product clone. Run from
the product clone with `TMPDIR` set to a writable scratch directory, `python3 -B` and no `PYTHONPATH=.`. Flows 2 to 6 read text with
`python3 -B -c` and create no folder. Flow 7 runs real lifecycle scenarios in folders under `/tmp` (about two minutes) and is
operator-run when the sandbox is tight. The history measurement of D6 needs the full git history and is operator-run.

Setup for flows 2 to 6: each prints either the flows read or the refusal; `H` below is the heading `## Functional review checklist`.

| # | Flow | Command | Expected |
|---|---|---|---|
| 1 | Cross-shape tests (CP1) | `python3 -B -m unittest tests.test_functional_review` | All 90 tests pass, including the `CrossShapeTest` class (heading before and after a bold flow, a group heading, the second case of the issue, one refusal per deciding shape, the forms that are not refused, the evidence, precedence, and the historical checklists as fixtures). |
| 2 | First case of the issue is refused (R1) | `python3 -B -c "import controller.functional_review as fr; fr.read_flows('## Functional review checklist\n\n### A. One\ngo\n\n**B. Two.** x\n')"` | Raises `ChecklistUnrecognised` (code `checklist_unrecognised`): the bold shape decided 1 flow; the heading `A` another shape recognised would not run. Evidence: `shape` `bold`, `recognised` 1, `total` 2, `unrecognised` `['A']`, `dropped` `[{'shape': 'heading', 'label': 'A'}]`. It no longer reads the one flow `B`. |
| 3 | Position rule, both sides (D1, D7) | Same as flow 2 with `\n\n### Notes\nz\n` appended; then the bold flow first and the heading after: `'## Functional review checklist\n\n**B. Two.** x\n\n### A. One\ngo\n'` | Each is refused naming `A` with `shape` `bold`: a heading ended by a later heading, or placed after the bold flow, is not inside the flow and would not run. |
| 4 | Second case of the issue (R1) | `python3 -B -c "import controller.functional_review as fr; fr.read_flows('## Functional review checklist\n\n### A. Login\n\nrun the cases:\n\n| # | Case | Expected |\n|---|---|---|\n| 1 | go | ok |\n| 2 | stop | ok |\n\n### C. Read\n\nread the page aloud and check it.\n')"` | Refused naming `C` only (`unrecognised` `['C']`, `dropped` `[{'shape': 'heading', 'label': 'C'}]`): heading `A` holds the rows of the table that decided, so it is accepted; the table-less `C` would not run. |
| 5 | Nested and setup items are not refused (R2) | `python3 -B -c "import controller.functional_review as fr; H='## Functional review checklist\n\n'; print([f.label for f in fr.read_flows(H+'**A. One.** run it.\n\n1. nested step\n2. another\n\n**B. Two.** x\n')]); print([f.label for f in fr.read_flows(H+'### Setup\n\n1. a\n2. b\n\n**A. One.** run\n\n**B. Two.** x\n')])"` | Prints `['A', 'B']` twice: a numbered list inside a bold flow, and a numbered list under `### Setup`, are accepted; flow ids and order are the bold shape's, as before. |
| 6 | Historical checklists read as before (R3) | `python3 -B -c "import controller.functional_review as fr; t=open('docs/milestones/completed/workflow-controller-documentation-reorganisation.md').read(); print([f.label for f in fr.read_flows(t)])"` | Nine flows `A`..`I` (the heading shape with nested tables); no refusal. If the file lacks a recognised checklist section, note the error text. |
| 7 | Gate text and lifecycle (CP2) | `python3 -B -m unittest tests.test_functional_lifecycle` (operator-run) | Passes: a checklist where the bold shape decides and a heading flow lies outside it stops at `checklist_unrecognised` before any job, sandbox or reservation exists; the gate text begins "the functional review checklist is only partly recognised", names the heading's label, and states that a flow another shape recognised must lie inside a flow of the shape used (or hold a table's rows). |
| 8 | Neighbours, containment, documents (R4) | `python3 -B -m unittest tests.test_package_structure tests.test_write_containment tests.test_protocol_job`, then `python3 -B tools/check_docs.py` | All pass and `check_docs.py` exits 0; `docs/guide/automation.md` states the rule once (accounted for by starting inside a flow, or holding the rows of the table that decided; the non-flow regions; the evidence `dropped`), `docs/common-problems.md` and `docs/guide/troubleshooting.md` refer to it, and `docs/ROADMAP.md` marks FL2 done (1.27.4). |
| 9 | History measurement (D6, operator-run) | Rerun the one-off measurement over every revision of `docs/ACTIVE_MILESTONE.md`, `docs/milestones/completed/`, the Manager checklists and the vendored fixtures, in a `mktemp -d` copy | No checklist the base read completely is newly refused or read differently (CP1's report recorded 392 texts, 64 distinct sections, 63 read on the base, none newly refused). |

**Known limitations / out of scope.** No setting, option, exit code, gate or result contract changes; the refusal keeps its code,
shape list and remedy, and gains the `dropped` evidence. The label grammar, the order of the four shapes, `flow-N` ids and the
tester's prompt are unchanged. A numbered list in the prose of a heading that holds a table's rows, a heading that groups bold
flows, and a numbered setup list under a differently worded heading (`**Preparation.**`) are refused by design (D1, D7, Risks); the
remedy is bullets, `### Setup`, or one shape per section. The numbered deciding shape has no cross-shape candidate. The change is
a `fix:` patch release (1.27.4). The full suite is not a flow (`test_functional_sandbox` has a known CI flake, #206;
`tests/test_codex_tester.py` has a pre-existing shared-tag collision).

## Next action

Manual functional review of FL2 (the checklist above), then `/accept-milestone`.
