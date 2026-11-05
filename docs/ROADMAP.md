# Roadmap

<!-- Generated from docs/backlog.yaml by scripts/workflow_backlog.py. Do not edit by hand: edit the backlog file and run `python3 scripts/workflow_backlog.py generate`. -->

| Order | Item | Status | Size | Depends on |
| --- | --- | --- | --- | --- |
| 1 | Squash merges, with the release version derived from a Conventional Commit pull request title `workflow-controller-c1` | Done | - | - |
| 2 | Reap every child process `workflow-controller-c1b` | Done | - | - |
| 3 | CI reliability `workflow-controller-c2` | Done | - | - |
| 4 | Settings file v1, the 1.4 cleanup patches, telemetry v0 `workflow-controller-c3` | Done | - | - |
| 5 | Auto-merge after acceptance `workflow-controller-c4` | Done | - | workflow-controller-c1, workflow-controller-c1b, workflow-controller-c2 |
| 6 | The Controller on Orchestration Protocol v1 `workflow-controller-c9` | Done | - | - |
| 7 | Protocol 1.1 and 1.2 `workflow-controller-c9b` | Done | - | - |
| 8 | Move this repository to the latest compatible Workflow `workflow-controller-c9c` | Done | - | workflow-controller-c9b |
| 9 | The installed-release test made version-agnostic `workflow-controller-c9d` | Done | - | workflow-controller-c9c |
| 10 | Documentation `workflow-controller-d1` | Done | - | - |
| 11 | Usage budget `workflow-controller-c8` | Done | - | workflow-controller-c3 |
| 12 | A usage limit reached mid-job `workflow-controller-c8b` | Done | - | workflow-controller-c8 |
| 13 | SignalHub notifications `workflow-controller-c5` | Done | - | workflow-controller-c3, workflow-controller-c8 |
| 14 | Automated lifecycle scenarios `workflow-controller-c6` | Done | - | - |
| 15 | A review-only harness seam with a Codex reviewer `workflow-controller-c7` | Done | - | workflow-controller-c8 |
| 16 | Review convergence `workflow-controller-c13` | Done | - | workflow-controller-c7 |
| 17 | Review modes `workflow-controller-c14` | Done | - | workflow-controller-c13 |
| 18 | Clean-up: work through the open issues labelled \`review-follow-up\` `workflow-controller-cu1` | Done | - | workflow-controller-c14 |
| 19 | Gate-policy validations `workflow-controller-c10a` | Done | - | workflow-controller-c6 |
| 20 | The pull request defect loop `workflow-controller-c10b` | Done | - | workflow-controller-c10a |
| 21 | Clean-up: the open \`review-follow-up\` and small \`bug\` issues gathered since CU1, in one milestone: #78, #79, #82-#87, #91 `workflow-controller-cu2` | Done | - | workflow-controller-c10b |
| 22 | Harness and git-host adapters `workflow-controller-a1` | Done | - | workflow-controller-cu2 |
| 23 | Two review round caps per stage, local and external `workflow-controller-c13b` | Done | - | workflow-controller-c13, workflow-controller-a1 |
| 24 | The automated functional review `workflow-controller-c10c` | Done | - | workflow-controller-c10b |
| 25 | CI time and flakiness `workflow-controller-c2b` | Done | - | - |
| 26 | A command that verifies a new \`claude\` build for the functional tester `workflow-controller-c10e` | Done | - | workflow-controller-c10c |
| 27 | The sandbox layout for Claude Code 2.1.304 and later `workflow-controller-c10f` | Done | - | workflow-controller-c10e |
| 28 | The functional tester on Codex without a per-release version block, step 1 `workflow-controller-c10g-1` | Done | - | workflow-controller-c10f |
| 29 | The Codex build digest `workflow-controller-c10g-2a` | Done | - | workflow-controller-c10g-1 |
| 30 | \`verify-codex\`, the command and its probes, with verify-on-first-use cached by the build's digest `workflow-controller-c10g-2b` | Done | - | workflow-controller-c10g-2a |
| 31 | The tester's composed-configuration check `workflow-controller-c10g-3a` | Done | - | workflow-controller-c10g-2a, workflow-controller-c10g-2b |
| 32 | The sandbox bridge `workflow-controller-c10g-3b` | Done | - | workflow-controller-c10g-3a |
| 33 | The Codex tester's run `workflow-controller-c10g-3b2a` | Done | - | workflow-controller-c10g-3b |
| 34 | The Codex tester's surroundings `workflow-controller-c10g-3b2b` | Done | - | workflow-controller-c10g-3b2a |
| 35 | The Codex tester chosen through H1's per-role routing, so there is no separate tester-harness setting `workflow-controller-c10g-3c1` | Done | - | workflow-controller-c10g-3b2b, workflow-controller-h1, workflow-controller-h9b |
| 36 | The Codex tester's fallback to the other harness along H9's chain before the spawn and its selection-time causes, the telemetry dimensions and Codex usage readings for a functional job, and one `workflow-controller-c10g-3c2` | Done | - | workflow-controller-c10g-3c1 |
| 37 | Routing names a harness `workflow-controller-h1` | Done | - | workflow-controller-a1 |
| 38 | A role-by-harness table in the documentation, generated from the adapter registry with a check that it is current `workflow-controller-h6` | Done | - | workflow-controller-a1 |
| 39 | Build verification declared by the adapter `workflow-controller-h7` | Done | - | workflow-controller-c10e, workflow-controller-h1 |
| 40 | Supported models per harness `workflow-controller-h8` | Done | - | workflow-controller-h1 |
| 41 | The chain of a fallback per role, the shape only `workflow-controller-h9a` | Done | - | workflow-controller-h1, workflow-controller-h3 |
| 42 | The selection `workflow-controller-h9b` | Done | - | workflow-controller-h9a |
| 43 | The independence rule, read as the Workflow's \`distinct_reviewer_models\` pair `workflow-controller-h10` | Done | - | workflow-controller-h1 |
| 44 | The harness in telemetry `workflow-controller-h11` | Done | - | workflow-controller-h1 |
| 45 | Claude as an external reviewer without lowering the policy `workflow-controller-h2` | Done | - | workflow-controller-h1 |
| 46 | The worker launch behind the adapter `workflow-controller-h3` | Done | - | workflow-controller-h1 |
| 47 | Codex worker, step 1 `workflow-controller-h4a` | Done | - | workflow-controller-h3 |
| 48 | Codex worker, step 2a `workflow-controller-h4b1` | Done | - | workflow-controller-h4a, workflow-controller-c10g-2b |
| 49 | Codex worker, step 2b `workflow-controller-h4b2` | Done | - | workflow-controller-h4b1 |
| 50 | Codex worker, step 3a `workflow-controller-h4c1` | Done | - | workflow-controller-h4b2 |
| 51 | Codex worker, step 3b `workflow-controller-h4c2` | Done | - | workflow-controller-h4c1 |
| 52 | Codex worker, step 4 `workflow-controller-h4d` | Done | - | workflow-controller-h4c1 |
| 53 | Clean-up, part 1 `workflow-controller-cu3a` | Done | - | workflow-controller-c10f |
| 54 | Clean-up, part 2 `workflow-controller-cu3b` | Done | - | workflow-controller-cu3a |
| 55 | Notifications: review and functional-review waits are info events, not user_gate `workflow-controller-nf1` | Done | - | workflow-controller-cu3b |
| 56 | A configurable external-reviewer brief `workflow-controller-rb1` | Done | - | workflow-controller-nf1 |
| 57 | A round-aware external-review prompt `workflow-controller-rb2` | Done | - | workflow-controller-rb1 |
| 58 | Notifications: a spent checks_pending wait is an info event, not lane_blocked `workflow-controller-nf2` | Done | - | workflow-controller-nf1 |
| 59 | The user-only command scan catches a name followed by punctuation or behind = `workflow-controller-tk1` | Done | - | workflow-controller-h4a |
| 60 | Notifications: user_gate only for a stop that needs a person `workflow-controller-nf3` | Done | - | workflow-controller-nf2 |
| 61 | The Controller-run Codex review accepts the installed codex-cli again `workflow-controller-rb3` | Done | - | workflow-controller-rb2 |
| 62 | Scenario tests no longer error on a machine whose sandbox probe passes `workflow-controller-sc1` | Done | - | - |
| 63 | The functional checklist recogniser accepts the label forms real checklists use `workflow-controller-fl1` | Done | - | workflow-controller-nf3 |
| 64 | The functional checklist reader refuses a flow another shape recognised outside every flow `workflow-controller-fl2` | Done | - | workflow-controller-fl1 |
| 65 | The checklist reader reads bold Flow K headers as flows `workflow-controller-fl3` | Done | - | workflow-controller-fl2 |
| 66 | Clean-up, part 3 `workflow-controller-cu3c` | Done | - | workflow-controller-cu3b |
| 67 | The Claude functional tester's sandbox denies reads of its credential copy, its account files and your Claude home, a \`file://\` origin is checked and the tester's summary is bounded `workflow-controller-c10h` | Done | - | workflow-controller-c10f |
| 68 | Check sandbox availability before reserving usage and marking the tester job launched `workflow-controller-c10i` | Done | - | workflow-controller-c10f |
| 69 | Functional checklist parsing `workflow-controller-c10j` | Done | - | workflow-controller-c10f |
| 70 | \`runtime.remove_tree\` refuses a path whose parent chain holds a symbolic link `workflow-controller-c10k` | Done | - | workflow-controller-c10f |
| 71 | Test leftovers end with their test `workflow-controller-c12a` | Done | - | - |
| 72 | The backlog, declare-first `workflow-controller-a2a` | In progress | - | workflow-controller-a1 |
| 73 | The prose migration `workflow-controller-a2b` | Next | - | workflow-controller-a2a |
| 74 | Codex worker, step 5 `workflow-controller-h4e` | Next | - | workflow-controller-h4c2, workflow-controller-h4d, workflow-controller-h1 |
| 75 | Codex worker, step 6 `workflow-controller-h4f` | Next | - | workflow-controller-h4e |
| 76 | Codex worker, step 7 `workflow-controller-h4g` | Next | - | workflow-controller-h4f |
| 77 | The post-acceptance defect loop at the merge flow's \`checks_failing\` stop `workflow-controller-c10d-a` | Next | - | workflow-controller-c10c |
| 78 | The same report at a \`CHANGES_REQUESTED\` decision `workflow-controller-c10d-b` | Next | - | workflow-controller-c10d-a |
| 79 | Retention of finished work-item files `workflow-controller-cl1a` | Next | - | - |
| 80 | A \`verify\` check that warns and then fails above a limit `workflow-controller-cl1b` | Next | - | workflow-controller-cl1a |
| 81 | A safe prune command `workflow-controller-cl1c` | Next | - | workflow-controller-cl1b |
| 82 | A notification `workflow-controller-cl1d` | Next | - | workflow-controller-cl1b |
| 83 | An opt-in automatic cleanup after acceptance, off by default `workflow-controller-cl1e` | Next | - | workflow-controller-cl1c |
| 84 | Self-recovery, part 1 `workflow-controller-c12b` | Next | - | workflow-controller-c10d-a, workflow-controller-c10d-b |
| 85 | Self-recovery, part 2 `workflow-controller-c12c` | Next | - | workflow-controller-c12b |
| 86 | Self-recovery, part 3 `workflow-controller-c12d` | Next | - | workflow-controller-c12c |
| 87 | Per-repository caps and per-lane budget files `workflow-controller-c8c` | Next | - | workflow-controller-c8 |
| 88 | The kanban runner `workflow-controller-c11` | Idea | - | workflow-controller-c8, workflow-controller-c10d-a, workflow-controller-c10d-b, workflow-controller-c12b, workflow-controller-c12c, workflow-controller-c12d |
| 89 | Backlog refinement, the measurement part `workflow-controller-a3` | Idea | - | workflow-controller-a1, workflow-controller-a2a, workflow-controller-a2b |
| 90 | The tracker interface `workflow-controller-a4` | Idea | - | workflow-controller-a2a, workflow-controller-a2b, workflow-controller-c11 |
| 91 | Escalation record `workflow-controller-o1` | Idea | - | - |
| 92 | Answer channel `workflow-controller-o2` | Idea | - | - |
| 93 | The decider `workflow-controller-o3` | Idea | - | - |
| 94 | Roadmap intake and progress reports `workflow-controller-o4` | Idea | - | - |
| 95 | Upgrades: install the Controller's own new releases safely `workflow-controller-o5` | Idea | - | - |
| 96 | Many repositories `workflow-controller-o6` | Idea | - | - |
| 97 | The control room `workflow-controller-o7` | Idea | - | - |
