# Roadmap

<!-- Generated from docs/backlog.yaml by scripts/workflow_backlog.py. Do not edit by hand: edit the backlog file and run `python3 scripts/workflow_backlog.py generate`. -->

| Order | Item | Status | Size | Depends on |
| --- | --- | --- | --- | --- |
| 1 | [Squash merges, with the release version derived from a Conventional Commit pull request title](items/workflow-controller-c1.md) `workflow-controller-c1` | Done | - | - |
| 2 | [Reap every child process](items/workflow-controller-c1b.md) `workflow-controller-c1b` | Done | - | - |
| 3 | [CI reliability](items/workflow-controller-c2.md) `workflow-controller-c2` | Done | - | - |
| 4 | [Settings file v1, the 1.4 cleanup patches, telemetry v0](items/workflow-controller-c3.md) `workflow-controller-c3` | Done | - | - |
| 5 | [Auto-merge after acceptance](items/workflow-controller-c4.md) `workflow-controller-c4` | Done | - | workflow-controller-c1, workflow-controller-c1b, workflow-controller-c2 |
| 6 | [The Controller on Orchestration Protocol v1](items/workflow-controller-c9.md) `workflow-controller-c9` | Done | - | - |
| 7 | [Protocol 1.1 and 1.2](items/workflow-controller-c9b.md) `workflow-controller-c9b` | Done | - | - |
| 8 | [Move this repository to the latest compatible Workflow](items/workflow-controller-c9c.md) `workflow-controller-c9c` | Done | - | workflow-controller-c9b |
| 9 | [The installed-release test made version-agnostic](items/workflow-controller-c9d.md) `workflow-controller-c9d` | Done | - | workflow-controller-c9c |
| 10 | [Documentation](items/workflow-controller-d1.md) `workflow-controller-d1` | Done | - | - |
| 11 | [Usage budget](items/workflow-controller-c8.md) `workflow-controller-c8` | Done | - | workflow-controller-c3 |
| 12 | [A usage limit reached mid-job](items/workflow-controller-c8b.md) `workflow-controller-c8b` | Done | - | workflow-controller-c8 |
| 13 | [SignalHub notifications](items/workflow-controller-c5.md) `workflow-controller-c5` | Done | - | workflow-controller-c3, workflow-controller-c8 |
| 14 | [Automated lifecycle scenarios](items/workflow-controller-c6.md) `workflow-controller-c6` | Done | - | - |
| 15 | [A review-only harness seam with a Codex reviewer](items/workflow-controller-c7.md) `workflow-controller-c7` | Done | - | workflow-controller-c8 |
| 16 | [Review convergence](items/workflow-controller-c13.md) `workflow-controller-c13` | Done | - | workflow-controller-c7 |
| 17 | [Review modes](items/workflow-controller-c14.md) `workflow-controller-c14` | Done | - | workflow-controller-c13 |
| 18 | [Clean-up: work through the open issues labelled \`review-follow-up\`](items/workflow-controller-cu1.md) `workflow-controller-cu1` | Done | - | workflow-controller-c14 |
| 19 | [Gate-policy validations](items/workflow-controller-c10a.md) `workflow-controller-c10a` | Done | - | workflow-controller-c6 |
| 20 | [The pull request defect loop](items/workflow-controller-c10b.md) `workflow-controller-c10b` | Done | - | workflow-controller-c10a |
| 21 | [Clean-up: the open \`review-follow-up\` and small \`bug\` issues gathered since CU1, in one milestone: #78, #79, #82-#87, #91](items/workflow-controller-cu2.md) `workflow-controller-cu2` | Done | - | workflow-controller-c10b |
| 22 | [Harness and git-host adapters](items/workflow-controller-a1.md) `workflow-controller-a1` | Done | - | workflow-controller-cu2 |
| 23 | [Two review round caps per stage, local and external](items/workflow-controller-c13b.md) `workflow-controller-c13b` | Done | - | workflow-controller-c13, workflow-controller-a1 |
| 24 | [The automated functional review](items/workflow-controller-c10c.md) `workflow-controller-c10c` | Done | - | workflow-controller-c10b |
| 25 | [CI time and flakiness](items/workflow-controller-c2b.md) `workflow-controller-c2b` | Done | - | - |
| 26 | [A command that verifies a new \`claude\` build for the functional tester](items/workflow-controller-c10e.md) `workflow-controller-c10e` | Done | - | workflow-controller-c10c |
| 27 | [The sandbox layout for Claude Code 2.1.304 and later](items/workflow-controller-c10f.md) `workflow-controller-c10f` | Done | - | workflow-controller-c10e |
| 28 | [The functional tester on Codex without a per-release version block, step 1](items/workflow-controller-c10g-1.md) `workflow-controller-c10g-1` | Done | - | workflow-controller-c10f |
| 29 | [The Codex build digest](items/workflow-controller-c10g-2a.md) `workflow-controller-c10g-2a` | Done | - | workflow-controller-c10g-1 |
| 30 | [\`verify-codex\`, the command and its probes, with verify-on-first-use cached by the build's digest](items/workflow-controller-c10g-2b.md) `workflow-controller-c10g-2b` | Done | - | workflow-controller-c10g-2a |
| 31 | [The tester's composed-configuration check](items/workflow-controller-c10g-3a.md) `workflow-controller-c10g-3a` | Done | - | workflow-controller-c10g-2a, workflow-controller-c10g-2b |
| 32 | [The sandbox bridge](items/workflow-controller-c10g-3b.md) `workflow-controller-c10g-3b` | Done | - | workflow-controller-c10g-3a |
| 33 | [The Codex tester's run](items/workflow-controller-c10g-3b2a.md) `workflow-controller-c10g-3b2a` | Done | - | workflow-controller-c10g-3b |
| 34 | [The Codex tester's surroundings](items/workflow-controller-c10g-3b2b.md) `workflow-controller-c10g-3b2b` | Done | - | workflow-controller-c10g-3b2a |
| 35 | [The Codex tester chosen through H1's per-role routing, so there is no separate tester-harness setting](items/workflow-controller-c10g-3c1.md) `workflow-controller-c10g-3c1` | Done | - | workflow-controller-c10g-3b2b, workflow-controller-h1, workflow-controller-h9b |
| 36 | [The Codex tester's fallback to the other harness along H9's chain, its telemetry and usage readings, and one live functional round on Codex that proves it](items/workflow-controller-c10g-3c2.md) `workflow-controller-c10g-3c2` | Done | - | workflow-controller-c10g-3c1 |
| 37 | [Routing names a harness](items/workflow-controller-h1.md) `workflow-controller-h1` | Done | - | workflow-controller-a1 |
| 38 | [A role-by-harness table in the documentation, generated from the adapter registry with a check that it is current](items/workflow-controller-h6.md) `workflow-controller-h6` | Done | - | workflow-controller-a1 |
| 39 | [Build verification declared by the adapter](items/workflow-controller-h7.md) `workflow-controller-h7` | Done | - | workflow-controller-c10e, workflow-controller-h1 |
| 40 | [Supported models per harness](items/workflow-controller-h8.md) `workflow-controller-h8` | Done | - | workflow-controller-h1 |
| 41 | [The chain of a fallback per role, the shape only](items/workflow-controller-h9a.md) `workflow-controller-h9a` | Done | - | workflow-controller-h1, workflow-controller-h3 |
| 42 | [The selection](items/workflow-controller-h9b.md) `workflow-controller-h9b` | Done | - | workflow-controller-h9a |
| 43 | [The independence rule, read as the Workflow's \`distinct_reviewer_models\` pair](items/workflow-controller-h10.md) `workflow-controller-h10` | Done | - | workflow-controller-h1 |
| 44 | [The harness in telemetry](items/workflow-controller-h11.md) `workflow-controller-h11` | Done | - | workflow-controller-h1 |
| 45 | [Claude as an external reviewer without lowering the policy](items/workflow-controller-h2.md) `workflow-controller-h2` | Done | - | workflow-controller-h1 |
| 46 | [The worker launch behind the adapter](items/workflow-controller-h3.md) `workflow-controller-h3` | Done | - | workflow-controller-h1 |
| 47 | [Codex worker, step 1](items/workflow-controller-h4a.md) `workflow-controller-h4a` | Done | - | workflow-controller-h3 |
| 48 | [Codex worker, step 2a](items/workflow-controller-h4b1.md) `workflow-controller-h4b1` | Done | - | workflow-controller-h4a, workflow-controller-c10g-2b |
| 49 | [Codex worker, step 2b](items/workflow-controller-h4b2.md) `workflow-controller-h4b2` | Done | - | workflow-controller-h4b1 |
| 50 | [Codex worker, step 3a](items/workflow-controller-h4c1.md) `workflow-controller-h4c1` | Done | - | workflow-controller-h4b2 |
| 51 | [Codex worker, step 3b](items/workflow-controller-h4c2.md) `workflow-controller-h4c2` | Done | - | workflow-controller-h4c1 |
| 52 | [Codex worker, step 4](items/workflow-controller-h4d.md) `workflow-controller-h4d` | Done | - | workflow-controller-h4c1 |
| 53 | [Clean-up, part 1](items/workflow-controller-cu3a.md) `workflow-controller-cu3a` | Done | - | workflow-controller-c10f |
| 54 | [Clean-up, part 2](items/workflow-controller-cu3b.md) `workflow-controller-cu3b` | Done | - | workflow-controller-cu3a |
| 55 | [Notifications: review and functional-review waits are info events, not user_gate](items/workflow-controller-nf1.md) `workflow-controller-nf1` | Done | - | workflow-controller-cu3b |
| 56 | [A configurable external-reviewer brief](items/workflow-controller-rb1.md) `workflow-controller-rb1` | Done | - | workflow-controller-nf1 |
| 57 | [A round-aware external-review prompt](items/workflow-controller-rb2.md) `workflow-controller-rb2` | Done | - | workflow-controller-rb1 |
| 58 | [Notifications: a spent checks_pending wait is an info event, not lane_blocked](items/workflow-controller-nf2.md) `workflow-controller-nf2` | Done | - | workflow-controller-nf1 |
| 59 | [The user-only command scan catches a name followed by punctuation or behind =](items/workflow-controller-tk1.md) `workflow-controller-tk1` | Done | - | workflow-controller-h4a |
| 60 | [Notifications: user_gate only for a stop that needs a person](items/workflow-controller-nf3.md) `workflow-controller-nf3` | Done | - | workflow-controller-nf2 |
| 61 | [The Controller-run Codex review accepts the installed codex-cli again](items/workflow-controller-rb3.md) `workflow-controller-rb3` | Done | - | workflow-controller-rb2 |
| 62 | [Scenario tests no longer error on a machine whose sandbox probe passes](items/workflow-controller-sc1.md) `workflow-controller-sc1` | Done | - | - |
| 63 | [The functional checklist recogniser accepts the label forms real checklists use](items/workflow-controller-fl1.md) `workflow-controller-fl1` | Done | - | workflow-controller-nf3 |
| 64 | [The functional checklist reader refuses a flow another shape recognised outside every flow](items/workflow-controller-fl2.md) `workflow-controller-fl2` | Done | - | workflow-controller-fl1 |
| 65 | [The checklist reader reads bold Flow K headers as flows](items/workflow-controller-fl3.md) `workflow-controller-fl3` | Done | - | workflow-controller-fl2 |
| 66 | [Clean-up, part 3](items/workflow-controller-cu3c.md) `workflow-controller-cu3c` | Done | - | workflow-controller-cu3b |
| 67 | [The Claude functional tester's sandbox denies reads of its credential copy, its account files and your Claude home, a \`file://\` origin is checked and the tester's summary is bounded](items/workflow-controller-c10h.md) `workflow-controller-c10h` | Done | - | workflow-controller-c10f |
| 68 | [Check sandbox availability before reserving usage and marking the tester job launched](items/workflow-controller-c10i.md) `workflow-controller-c10i` | Done | - | workflow-controller-c10f |
| 69 | [Functional checklist parsing](items/workflow-controller-c10j.md) `workflow-controller-c10j` | Done | - | workflow-controller-c10f |
| 70 | [\`runtime.remove_tree\` refuses a path whose parent chain holds a symbolic link](items/workflow-controller-c10k.md) `workflow-controller-c10k` | Done | - | workflow-controller-c10f |
| 71 | [Test leftovers end with their test](items/workflow-controller-c12a.md) `workflow-controller-c12a` | Done | - | - |
| 72 | [The backlog, declare-first](items/workflow-controller-a2a.md) `workflow-controller-a2a` | Done | - | workflow-controller-a1 |
| 73 | [The prose migration](items/workflow-controller-a2b.md) `workflow-controller-a2b` | Done | - | workflow-controller-a2a |
| 74 | [Codex worker, step 5](items/workflow-controller-h4e.md) `workflow-controller-h4e` | Next | - | workflow-controller-h4c2, workflow-controller-h4d, workflow-controller-h1 |
| 75 | [Codex worker, step 6](items/workflow-controller-h4f.md) `workflow-controller-h4f` | Next | - | workflow-controller-h4e |
| 76 | [Codex worker, step 7](items/workflow-controller-h4g.md) `workflow-controller-h4g` | Next | - | workflow-controller-h4f |
| 77 | [The post-acceptance defect loop at the merge flow's \`checks_failing\` stop](items/workflow-controller-c10d-a.md) `workflow-controller-c10d-a` | Next | - | workflow-controller-c10c |
| 78 | [The same report at a \`CHANGES_REQUESTED\` decision](items/workflow-controller-c10d-b.md) `workflow-controller-c10d-b` | Next | - | workflow-controller-c10d-a |
| 79 | [Retention of finished work-item files](items/workflow-controller-cl1a.md) `workflow-controller-cl1a` | Next | - | - |
| 80 | [A \`verify\` check that warns and then fails above a limit](items/workflow-controller-cl1b.md) `workflow-controller-cl1b` | Next | - | workflow-controller-cl1a |
| 81 | [A safe prune command](items/workflow-controller-cl1c.md) `workflow-controller-cl1c` | Next | - | workflow-controller-cl1b |
| 82 | [A notification](items/workflow-controller-cl1d.md) `workflow-controller-cl1d` | Next | - | workflow-controller-cl1b |
| 83 | [An opt-in automatic cleanup after acceptance, off by default](items/workflow-controller-cl1e.md) `workflow-controller-cl1e` | Next | - | workflow-controller-cl1c |
| 84 | [Self-recovery, part 1](items/workflow-controller-c12b.md) `workflow-controller-c12b` | Next | - | workflow-controller-c10d-a, workflow-controller-c10d-b |
| 85 | [Self-recovery, part 2](items/workflow-controller-c12c.md) `workflow-controller-c12c` | Next | - | workflow-controller-c12b |
| 86 | [Self-recovery, part 3](items/workflow-controller-c12d.md) `workflow-controller-c12d` | Next | - | workflow-controller-c12c |
| 87 | [Per-repository caps and per-lane budget files](items/workflow-controller-c8c.md) `workflow-controller-c8c` | Next | - | workflow-controller-c8 |
| 88 | [The kanban runner](items/workflow-controller-c11.md) `workflow-controller-c11` | Idea | - | workflow-controller-c8, workflow-controller-c10d-a, workflow-controller-c10d-b, workflow-controller-c12b, workflow-controller-c12c, workflow-controller-c12d |
| 89 | [Backlog refinement, the measurement part](items/workflow-controller-a3.md) `workflow-controller-a3` | Idea | - | workflow-controller-a1, workflow-controller-a2a, workflow-controller-a2b |
| 90 | [The tracker interface](items/workflow-controller-a4.md) `workflow-controller-a4` | Idea | - | workflow-controller-a2a, workflow-controller-a2b, workflow-controller-c11 |
| 91 | [Escalation record](items/workflow-controller-o1.md) `workflow-controller-o1` | Idea | - | workflow-controller-c11 |
| 92 | [Answer channel](items/workflow-controller-o2.md) `workflow-controller-o2` | Idea | - | workflow-controller-o1 |
| 93 | [The decider](items/workflow-controller-o3.md) `workflow-controller-o3` | Idea | - | workflow-controller-o1, workflow-controller-o2 |
| 94 | [Roadmap intake and progress reports](items/workflow-controller-o4.md) `workflow-controller-o4` | Idea | - | workflow-controller-o3 |
| 95 | [Upgrades: install the Controller's own new releases safely](items/workflow-controller-o5.md) `workflow-controller-o5` | Idea | - | workflow-controller-o3 |
| 96 | [Many repositories](items/workflow-controller-o6.md) `workflow-controller-o6` | Idea | - | workflow-controller-o5 |
| 97 | [The control room](items/workflow-controller-o7.md) `workflow-controller-o7` | Idea | - | workflow-controller-o2, workflow-controller-o6 |
