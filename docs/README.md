# Documentation map

Where to find what, and who maintains it. Start with the
[project README](../README.md) if you are new.

## Using the Controller

| Guide | Read it for |
|---|---|
| [Concepts](guide/concepts.md) | how Workflow, Workflow Manager and the Controller fit together, a milestone end to end, and a glossary |
| [Installing, upgrading and rolling back](guide/installation.md) | requirements, installing a release, building from a checkout, upgrades and rollbacks |
| [Commands, options and worker routing](guide/commands.md) | every command and global option, the `run` loop, which model and effort each worker gets |
| [What the Controller automates, and how it stays safe](guide/automation.md) | the safety model, the rule that decides what is launched, the phase table, implementation-review apply rounds |
| [Workers: lifecycle, recovery and observation](guide/workers.md) | how workers run and end, background work, the lifecycle lock, `resume`, job records and their recovery, `follow` |
| [Runtime state and runtime identity](guide/runtime.md) | where the Controller keeps its state, and how it knows and records what code it is running |
| [Milestone branches and pull requests](guide/milestone-branches.md) | the branch and pull request per milestone, readiness gates, merge and close-out, stuck milestones |
| [Continuous integration and releases](guide/ci-and-releases.md) | the CI workflows, how a release is made, and this repository's GitHub settings |
| [Development and the test runner](guide/development.md) | working from a checkout, running tests, the sharded test runner |
| [Troubleshooting](guide/troubleshooting.md) | exit codes and the usual situations |

These guides are maintained by hand in this repository.

## Releases

| Release notes | Read it for |
|---|---|
| [1.4.1](releases/1.4.1.md) | the Controller collects every finished child process it holds as a subreaper, in every state (the zombie leak), and test repositories without Git's automatic maintenance |
| [1.4.0](releases/1.4.0.md) | squash merges, release versions derived from Conventional Commit pull request titles and the release tags, `MERGED_SQUASHED`, and the cutover |
| [1.3.0](releases/1.3.0.md) | Workflow 2.6.0 admitted beside 2.5.1, the new error codes and gates, and the 1.2.1 changes that shipped without notes |

Releases 1.3.0, 1.4.0 and 1.4.1 have their notes in `releases/`. The intent
from 1.4.0 on is that a milestone's notes are its pull request body, which
becomes the squash commit body and the GitHub release notes; the Controller
does not do that yet ([roadmap 11.1.2](ROADMAP.md#1112-release-notes-follow-the-milestone)),
so until then each release's notes are added to `releases/` after it is published. The
GitHub release itself carries the wheel and `SHA256SUMS`
([Releasing](guide/ci-and-releases.md#releasing)).

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

Also part of the Workflow's record: `ai-workflow/registry/` and
`ai-workflow/requirements/` (each milestone's checkpoints and requirement
mappings), and [`ACTIVE_MILESTONE.md`](ACTIVE_MILESTONE.md), the current
work item's narrative, which Workflow commands write.

## The Workflow process (installed, do not edit)

These files are installed and kept byte-identical by Workflow Manager, at the
Workflow release recorded in `.workflow-manager/installation.json`. A local
edit shows up as drift and is refused; change them upstream, in the Workflow
Manager repository.

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
