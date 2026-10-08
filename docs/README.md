# Documentation map

> For: anyone looking for the right page. Last checked with: Controller 1.7.0; Workflow 2.6.0, 2.7.0 and 2.8.0.

Where to find what. Start with the [project README](../README.md) if you are
new: it explains how Workflow, Workflow Manager and the Controller fit
together and has a five-minute quick start.

## I want to ...

| I want to ... | Page |
|---|---|
| install the Controller, set up the settings file, uninstall it | [Install](install.md) |
| run it on a repository: a first run, `step` versus `run`, following, gate stops | [Run](run.md) |
| upgrade or roll back the Controller, or move a repository to a newer Workflow | [Update](update.md) |
| know which Workflow releases a Controller release accepts | [Compatibility](compatibility.md) |
| fix a run that stopped | [Common problems](common-problems.md) |
| know what an exit status means and what to do | [Exit codes](exit-codes.md) |
| look up a term (work item, phase, checkpoint, approval gate, ...) | [Glossary](glossary.md) |
| see what each release changed | [Release history](release-history.md) |

## Reference guides

These go deeper than the pages above; a task page links to the guide that
explains why.

| Guide | Read it for |
|---|---|
| [How it works](guide/how-it-works.md) | how Workflow, Workflow Manager and the Controller fit together, and a milestone end to end; the [lifecycle diagram](ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg) draws the lifecycle |
| [Commands, options and worker routing](guide/commands.md) | every command and global option, the `run` loop, which model and effort each worker gets |
| [What the Controller automates, and how it stays safe](guide/automation.md) | the safety model, the rule that decides what is launched, the phase table, implementation-review apply rounds |
| [Workers: lifecycle, recovery and observation](guide/workers.md) | how workers run and end, background work, the lifecycle lock, `resume`, job records and their recovery, `follow` |
| [Runtime state and runtime identity](guide/runtime.md) | the settings file, where the Controller keeps its state, and how it knows and records what code it is running |
| [Milestone branches and pull requests](guide/milestone-branches.md) | the branch and pull request per milestone, readiness gates, the release notes in the pull request body, merge and close-out, auto-merge and the release wait, stuck milestones |
| [Troubleshooting](guide/troubleshooting.md) | the long account of every stop and the situations behind it |

For maintainers of this repository:

| Guide | Read it for |
|---|---|
| [Continuous integration and releases](guide/ci-and-releases.md) | the CI workflows, how a release is made, release notes from the milestones, and this repository's GitHub settings |
| [Development and the test runner](guide/development.md) | working from a checkout, installing from one, running tests, the sharded test runner, the documentation checks |

These pages are maintained by hand in this repository, and
`python3 tools/check_docs.py` keeps their links and shown commands true
([Documentation checks](guide/development.md#documentation-checks)).

## Releases

| Release notes | Read it for |
|---|---|
| [1.7.0](releases/1.7.0.md) | the Controller on Workflow Orchestration Protocol v1: admission by capability, decisions from `next-action`, outcomes from `reconcile`, fail-closed gates and drift-tolerant resume, with 2.5.1/2.6.0 targets unchanged |
| [1.6.0](releases/1.6.0.md) | auto-merge after acceptance (opt-in policy key `auto_merge`; one head-bound squash merge of the accepted commit, never GitHub's auto-merge), the wait for the release, close-out then stop, and `run`'s bounded wait |
| [1.5.0](releases/1.5.0.md) | the settings file, telemetry v0 (tokens, cache, cost and time per job) and the `telemetry` command, release notes that follow the milestone, and the 1.4 cleanup patches |
| [1.4.2](releases/1.4.2.md) | the recorded command line fills once readable, the tests wait for the right signal, a re-run of failed CI jobs counts, and a leaked process fails the run |
| [1.4.1](releases/1.4.1.md) | the Controller collects every finished child process it holds as a subreaper, in every state (the zombie leak), and test repositories without Git's automatic maintenance |
| [1.4.0](releases/1.4.0.md) | squash merges, release versions derived from Conventional Commit pull request titles and the release tags, `MERGED_SQUASHED`, and the cutover |
| [1.3.0](releases/1.3.0.md) | Workflow 2.6.0 admitted beside 2.5.1, the new error codes and gates, and the 1.2.1 changes that shipped without notes |

Every release, with one line each and the ones before 1.3.0, is in the
[release history](release-history.md). The GitHub release itself carries the
wheel and `SHA256SUMS` ([Releasing](guide/ci-and-releases.md#releasing)); how a
milestone's notes reach the release is in
[Release notes from the milestones](guide/ci-and-releases.md#release-notes-from-the-milestones).

## Direction

- [Roadmap](ROADMAP.md): what is done, what comes next, and why.

## Design decisions

Architecture Decision Records. Each one records decisions that are meant to
stay stable; the code and the guides must agree with them.

| ADR | Decides |
|---|---|
| [0001](adr/0001-controller-generation-1-architecture.md) | Generation 1 architecture, the CLI interface and the normative exit codes |
| [0002](adr/0002-release-runtime-identity-and-observability.md) | release packaging, runtime identity and worker observation |
| [0003](adr/0003-trunk-branch-pr-release-orchestration.md) | milestone branches, pull requests and the release transaction |
| [0004](adr/0004-worker-lifecycle-ownership.md) | how a worker's lifetime and the processes it owns are decided, and the harness limitations left open |
| [0005](adr/0005-adaptive-test-sharding.md) | the test inventory, planner and sharded runner |
| [0006](adr/0006-workflow-release-admission-and-per-release-contracts.md) | which Workflow releases are admitted, what the Controller consumes from each, and why trunk integration stays manual |
| [0007](adr/0007-tag-derived-versions-and-squash-merges.md) | Conventional Commit pull request titles, tag-derived versions, squash close-out and the cutover |
| [0008](adr/0008-controller-settings-file.md) | the user-level settings file: location, precedence, fill and forward-only migration, and which values are settings |
| [0009](adr/0009-auto-merge-and-release-wait.md) | when and how the Controller merges an accepted milestone pull request (one head-bound squash merge, never GitHub's auto-merge request), the release wait, the stop after close-out, and what stays human |
| [0010](adr/0010-orchestration-protocol-admission-by-capability.md) | admission of a Workflow release by capability (protocol major 1), the protocol's decisions and outcomes, and what stays the Controller's; amends 0006 |

Some ADR contents are checked by tests (for example ADR 0001's exit-code
table), so edit them with care.

## History: plans and completed milestones

Every milestone was planned, reviewed and accepted through the Workflow. These
documents are the reviewed record. They describe the design as it was
accepted and are **not** updated afterwards, so where they disagree with the
guides, the guides and the code are current.

| Milestone | Plan | Narrative |
|---|---|---|
| Generation 1 | [plan](ai-workflow/CONTROLLER_GEN1_PLAN.md) | [narrative](milestones/completed/workflow-controller-generation-1.md) |
| Gen 1 correctness hardening | [plan](ai-workflow/CONTROLLER_GEN1_HARDENING_PLAN.md) | [narrative](milestones/completed/workflow-controller-gen1-correctness-hardening.md) |
| Protocol 2.2 compatibility | [plan](ai-workflow/CONTROLLER_GEN1_PROTOCOL_2_2_COMPAT_PLAN.md) | [narrative](milestones/completed/workflow-controller-protocol-2-2-compatibility.md) |
| Worker execution hardening | [plan](ai-workflow/CONTROLLER_WORKER_EXECUTION_HARDENING_PLAN.md) | [narrative](milestones/completed/workflow-controller-worker-execution-hardening.md) |
| Automatic lifecycle orchestration | [plan](ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md) | [narrative](milestones/completed/workflow-controller-automatic-lifecycle-orchestration.md) |
| Release runtime and observability (1.1) | [plan](ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md) | [narrative](milestones/completed/workflow-controller-release-runtime-observability.md) |
| Trunk branch, PR and release orchestration | [plan](ai-workflow/CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md) | [narrative](milestones/completed/workflow-controller-trunk-branch-pr-release-orchestration.md) |
| Worker lifecycle ownership | [plan](ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md) | [narrative](milestones/completed/workflow-controller-worker-lifecycle-ownership.md) |
| Adaptive test sharding | [plan](ai-workflow/CONTROLLER_ADAPTIVE_TEST_SHARDING_PLAN.md) | [narrative](milestones/completed/workflow-controller-adaptive-test-sharding.md) |
| Workflow 2.6 integration (1.3.0) | [plan](ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md) | [narrative](milestones/completed/workflow-controller-workflow-2-6-integration.md) |
| Squash merges and tag-derived versions (1.4.0) | [plan](ai-workflow/CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md) | [narrative](milestones/completed/workflow-controller-squash-merge-tag-versioning.md) |
| Child-process reaping (1.4.1) | [plan](ai-workflow/CONTROLLER_CHILD_PROCESS_REAPING_PLAN.md) | [narrative](milestones/completed/workflow-controller-child-process-reaping.md) |
| CI reliability (1.4.2) | [plan](ai-workflow/CONTROLLER_CI_RELIABILITY_PLAN.md) | [narrative](milestones/completed/workflow-controller-ci-reliability.md) |
| Settings and telemetry (1.5.0) | [plan](ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md) | [narrative](milestones/completed/workflow-controller-settings-and-telemetry.md) |
| Auto-merge and the release wait (1.6.0) | [plan](ai-workflow/CONTROLLER_AUTO_MERGE_RELEASE_WAIT_PLAN.md) | [narrative](milestones/completed/workflow-controller-auto-merge-release-wait.md) |
| Orchestration protocol v1 (1.7.0) | [plan](ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md) | [narrative](milestones/completed/workflow-controller-orchestration-protocol-v1.md) |

Also part of the Workflow's record: `ai-workflow/registry/` and
`ai-workflow/requirements/` (each milestone's checkpoints and requirement
mappings), and [`ACTIVE_MILESTONE.md`](ACTIVE_MILESTONE.md), the current
work item's narrative, which Workflow commands write.

## The Workflow process (installed, do not edit)

These files are installed and kept byte-identical by Workflow Manager, at the
Workflow release recorded in `.workflow-manager/installation.json`. A local
edit shows up as drift and is refused; change them upstream, in the
[Workflow](https://github.com/RodrigoFAbreu/workflow#readme) repository.

| Document | Read it for |
|---|---|
| [`ai-workflow/MILESTONE_WORKFLOW.md`](ai-workflow/MILESTONE_WORKFLOW.md) | the milestone state machine and its hard gates |
| [`ai-workflow/PLAN_REVIEW_WORKFLOW.md`](ai-workflow/PLAN_REVIEW_WORKFLOW.md) | the two-stage plan review |
| [`ai-workflow/IMPLEMENTATION_REVIEW_WORKFLOW.md`](ai-workflow/IMPLEMENTATION_REVIEW_WORKFLOW.md) | the two-stage implementation review |
| [`ai-workflow/REVIEW_PROTOCOL.md`](ai-workflow/REVIEW_PROTOCOL.md) | review bundles and the feedback format |
| [`ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md`](ai-workflow/WORKFLOW_V2_1_OPERATOR_REFERENCE.md) | the phase and command reference |
| `ai-workflow/WORKFLOW_V2_*`, `ai-workflow/audit/` | the Workflow's own design and audit history |

The live Workflow state is `ai-workflow/WORKFLOW_STATE.json` (with
`ai-workflow/WORKFLOW_CONFIG.json`). Only Workflow commands change it.
