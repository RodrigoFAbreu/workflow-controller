# Planning rules

> The order of the work is `docs/backlog.yaml`; the generated [roadmap](ROADMAP.md) lists it. The numbered "Next, in order" list below is a frozen record, not the current order. The text below is moved unchanged from the frozen [roadmap archive](roadmap-archive.md), and "the table below" in it means the tables kept there.

## Next, in order and the sizing rules

**Next, in order** (owner decisions, 2026-10-14, 2026-10-15, 2026-10-16, 2026-10-18, 2026-10-22 and 2026-10-23; each item carries its complexity label in the table below):
1. RB3 ([#168](https://github.com/RodrigoFAbreu/workflow-controller/issues/168)), done (the pin moved to `codex-cli 0.163.0`; NF2, the `checks_wait` event, is done, [#170](https://github.com/RodrigoFAbreu/workflow-controller/issues/170)); the operator's trial of `reviewer.harness=codex` (2026-10-25) was refused at launch by the 0.163.0 drift, which RB3 fixed, and the shared setting is unchanged (RB2, the round-aware external-review prompt, is done, [#161](https://github.com/RodrigoFAbreu/workflow-controller/issues/161); RB1, the configurable reviewer brief, is done, [#150](https://github.com/RodrigoFAbreu/workflow-controller/issues/150));
2. TK1 ([#247](https://github.com/RodrigoFAbreu/workflow-controller/issues/247), the user-only scan fix, done) and NF3 ([#244](https://github.com/RodrigoFAbreu/workflow-controller/issues/244), the false ACTION_REQUIRED alerts, done) went first (owner-facing safety and noise, 2026-11-01), then the harness-neutral roles epic, owner decision 2026-10-24: H1, H2, H3, H6, H7, H8, H9a, H9b, H10, H11 and SC1 ([#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188), the scenario-test fix) are done, C10g-1 is done, C10g-2a, C10g-2b, C10g-3a, C10g-3b, C10g-3b2a, C10g-3b2b, C10g-3c1 and C10g-3c2 (the Codex tester's fallback selection, telemetry dimensions and the operator-run Codex round) are done, H4a (the command renderer), H4b1 (the worker's sandbox and its live proof) and H4b2 (the launcher, fixture role only) are done, and H4c to H4g (the Codex worker) remain;
3. FL1 ([#227](https://github.com/RodrigoFAbreu/workflow-controller/issues/227), the checklist recogniser, done), FL2 ([#271](https://github.com/RodrigoFAbreu/workflow-controller/issues/271), the cross-shape drop, done), FL3 ([#288](https://github.com/RodrigoFAbreu/workflow-controller/issues/288), bold `Flow N` headers, done), CU3c, C10h (#115), C10i (#112), C10j (#116) and C10k (the `remove_tree` point of #140) are done;
4. A2a (declare the backlog, write the file, replace the page with the generated one, add the CI check) and A2b (the prose migration); A2c is dropped, folded into them (owner decision, 2026-11-05; workflow#48 shipped in Workflow 2.17.0);
5. C10d-a and C10d-b, the post-acceptance defect loop (C10c is released as 1.18.0 and C10e as 1.19.0; C10 is three steps, each released on its own);
6. CL1a to CL1e, retention of finished work-item files, one mechanism per item (#130; the same rule in the Workflow and the Manager);
7. C12b to C12d, then C11;
8. the orchestrator series O1-O7.

A3 and A4 follow their dependencies in the table below (A5 is replaced by the harness-neutral roles epic).

Clean-up milestones slot in between these every few milestones, or sooner when the issue count grows
(owner decision, 2026-10-16). **Limit (owner decision, 2026-10-23): more than 8 open `review-follow-up` issues in this
repository means a clean-up milestone is next; the count is checked at the start of every milestone.** Sizing rule (owner decision, 2026-10-23, the
same as the Workflow's and the Manager's roadmaps): an item is sized by what its reviewers must prove, not by its diff; every
item is releasable alone, of low to medium complexity, with at most one new mechanism (a lock, a record, a command, a
protocol row), and its row says `Complexity: low`, `medium` or `high`; concurrency, a trust or security record and a
launch path are separate items, and wording, documentation and command surface is its own low item; an item that needs two
mechanisms is split before its first review, and at the first repeated finding class, not after a second pause; Optional
findings are recorded and triaged, never a reason for another review round; changes with no behaviour change ship as
`ci:`, `chore:` or `docs:`. CU2, which took twelve issues (#78 and #79, CU1's follow-ups; #82-#87 and #91,
C10a and the lane's own work; #93-#95, C10b's follow-ups), was released as 1.16.0 (accepted 2026-10-18, PR #99). One
Optional finding of CU2 is kept as issue #100 for a later clean-up milestone. #88, the two review round caps,
stayed out of it: it needs the Workflow to count local and external rounds separately (workflow#43), which
Workflow 2.15.0 does; this repository runs 2.15.0 since 2026-10-19, so #88 is step C13b, right after A1.

**Adapters and backlog (owner decision, 2026-10-18).** The goal is a Controller that does not depend on the tools
around it, so the owner can change harness, git host or backlog tool without rewriting it. Every outside tool is
reached through one small interface with a registry, declared capabilities and a safe fallback. A harness adapter
(Claude Code and Codex today) declares the roles it supports, whether it can enforce single-agent and tool
restrictions and how it reports usage, and it fails closed on a version it does not know. A git-host adapter wraps
what the Controller does with git and `gh` (GitHub first, another host later); only the Controller process uses it,
never a worker.

**Harness-neutral roles (owner decision, 2026-10-24).** Any step can run on either harness (Claude Code or Codex), and the
model of each role is chosen separately. Today the worker roles (plan, implement, apply, the local reviews, `satisfy-gate`,
prepare, record) and the functional tester run on Claude Code only; Codex runs the two external review gates, and only
when `reviewer.harness` is set; the model and effort of each role are already chosen per role (`routing`). The epic below
makes the harness a per-role choice, one mechanism per item and each releasable alone: H1 routing names a harness; H6 a
role-by-harness table generated from the registry; H8 the supported models and efforts of each harness; H7 build
verification declared by each adapter, so no harness is pinned to one version; H2 Claude as an external reviewer; H3 the
worker launch behind the adapter; H10 the independence check in the mixed-lane stop; H9a and H9b a fallback chain per role; H11 the harness in
telemetry; the Codex tester (C10g-1 to C10g-3c2 with C10g-3b2a, C10g-3b2b and C10g-3c1, not repeated here); then the Codex worker in seven steps (H4a to H4g, H4a done), each
with its own safety proof before a role is enabled. It replaces item A5 (its content is kept: the shared command files and
the refusal of user-only commands are in H4a, the safety review in H4e and H4f).

Every adapter converts its harness output into one usage record: harness, provider, model, the token counts, wall
time and turns, with a quality flag (`reported`, `derived` or `unknown`) and a cost worked out once from a versioned
price table. Job records, `telemetry` and the usage budget read only that record.

The backlog moves out of the prose roadmap into a file, `docs/backlog.yaml`, with one entry per item (id, status,
order, dependencies, size and a link to the item's page). `docs/ROADMAP.md` is then generated from it, and a CI check
fails when the page is out of date. A board is a view of that file, never its source: the file adapter always works.
Items are refined into increments that can each be released alone, split when they are above about five
checkpoints, and the success of that is measured as cost per shipped outcome. Who may send commands from a board is a
setting in the Controller's own settings file with levels `admin`, `maintainer` and `viewer`, and the default is
closed. The steps are A1-A5 in the table below.

**Where this is heading: a kanban loop.** The Controller takes the next open roadmap item, plans
it, implements it, reviews it, tests it, merges it and releases it as a new increment. It then
starts the next item, until the roadmap is empty. In the end no human gate is left:
- plan approval and technical approval pass on the evidence of the local review plus an automated
  cross-model review;
- `/accept-milestone` passes when the automated functional review finds nothing;
- the pull request merges itself when its required checks are green;
- the release runs from `main`.

Usage limits are tracked, so a run never starts work it cannot finish before a limit resets.

**After the kanban loop: a person only for escalation.** Once C11 runs the loop, the orchestrator
series (O1-O7, [11.11](#1111-the-orchestrator-a-person-only-for-escalation)) takes over the judgement
calls an orchestrating session still makes by hand: why a run stopped, whether to retry, what to do
with a finding, what comes next. It acts only within a closed list of allowed actions and asks the
user for everything else (user decisions, 2026-10-09 and 2026-10-10).

**Two lanes run in parallel**, one milestone at a time in each: this repository, and Workflow
Manager together with the new `workflow` repository. The lanes meet where a Controller step needs a
Workflow release: C9 needed Workflow 2.7 (W1), C10 needs 2.8 (W2) and C13 needed 2.12 (W8).

## Deferred directions

**Deferred** because they do not unlock that operating model: concurrency and multi-worktree (6),
other forges (7.6), hot-reloadable routing (8, except what O7 needs), and assurance tiers.
A hosted platform in the user's homelab is a direction still being refined, not a step
([11.12](#1112-direction-a-hosted-platform-not-a-step-yet)). RepFlow (3, 4) migrates once, directly to a protocol-capable Workflow.

## 11.11 The orchestrator: a person only for escalation

**Steps O1-O7, after C11** (user decisions, 2026-10-09 and 2026-10-10). Three layers, each with one
job:

- **The Controller** follows the Workflow's rules step by step and makes no judgement calls. When it
  cannot continue it stops and says why.
- **The orchestrator** makes the judgement calls: why a run stopped, whether a retry is safe, what to
  do with a finding, what comes next. It chooses only from a closed list of allowed actions, within
  limits, and logs every decision with its reason.
- **The user** decides only escalations: what the orchestrator may not decide, or is unsure about.

**How one escalation works.**
1. *Stop.* The Controller writes an escalation record: what happened, the evidence (logs, verdicts,
   state), the allowed options with their effect, and a severity.
2. *Decide.* The Controller launches a short orchestrator worker (on demand; nothing runs between
   escalations). It acts, if the action is allowed and within its limits, or forwards the escalation
   to the user with a recommendation.
3. *Ask.* A forwarded question reaches the phone (SignalHub), a web page and the terminal at once.
   The first valid answer wins; the other channels show it as answered.
4. *Resume.* The Controller continues from the recorded decision.

**Authority, cautious to start.** The limits live in a policy file in the repository, so every change
is reviewable; the user loosens them over time.

| The orchestrator decides alone | It always asks the user |
|---|---|
| Retry a network or usage-limit failure, up to a limit | Scope: splitting a milestone, dropping or adding a requirement |
| Clear a stuck run, ending only its own processes by exact pid | A cross-model review that blocks |
| Re-run a flaky CI job once | A review still not converging after a set number of rounds |
| File a follow-up into the roadmap | Going over the cost or usage budget |
| Pick the next roadmap item in the order the user set | Anything touching security, credentials or permissions |
| | Changes to its own rules, and the same failure twice |

Defaults: with no answer it waits and reminds, and never decides on a timeout (other repositories keep
running); three retries for network and limit failures; a review counts as not converging after four
rounds.

**Talking to the orchestrator.** Each escalation records the session id of the orchestrator worker
that raised it. Its link copies `claude --resume <id>`, which continues that conversation in the CLI,
in that repository, with its full context: the way to ask questions, challenge a recommendation or
refine an idea in a short loop. A full chat inside the web page is not planned: it would rebuild much
of Claude Code, and a page that can run tools on the machine is a security surface; Claude Code's own
remote access covers the phone (user decision, 2026-10-10).

**Dependencies outside this repository.** O2's phone answers need a reply path in SignalHub. The
SignalHub lane has the request, together with user-based producers and subscriptions (2026-10-10).
O6 needs the Manager lane's M3.

## 11.12 Direction: a hosted platform (not a step yet)

**An idea to refine, not a roadmap item** (user, 2026-10-10). Nothing here is planned until the idea
is refined and the user turns it into steps.

The idea: the Controller runs as a hosted service in the user's homelab. A person logs in to a
Controller page, sets up their Claude and Codex subscriptions (API keys and other providers much
later), and then sees every run and lane, starts new ones and manages each run's settings. Work
continues while their own computer is off. Several people could use it, each with their own runs.

The homelab today is a switch, a desktop and a Raspberry Pi 5. Later it gains compute nodes and a
networking board acting as a firewall in front of the homelab network, with VLANs to keep things
contained.

What is known so far (2026-10-10):
- **It fits a homelab.** The models run at the providers, so a worker node runs only the harness,
  git and the repository's own tests and builds. A Pi suits the control side (the page, login, the
  database, the event store, the job queue). Workers want x86 nodes with plenty of memory: Android
  builds need 8 to 16 GB per run and run poorly on ARM.
- **The Workflow changes less than it seems.** The Controller already works inside a checkout,
  makes branches, commits, opens pull requests and merges. New: a job queue and scheduler across
  nodes, a run's state surviving a worker restart, several worktrees at once (6), and the forge
  adapter (7.6).
- **The hard parts are isolation and credentials, not compute.** An agent running commands on a
  cloned repository is running untrusted code, and a repository can carry instructions aimed at the
  agent. Each run needs its own sandbox (a container, or better a lightweight virtual machine),
  network access only to what it needs, and no sight of another user's credentials or files. Each
  user's logins and repository access are stored encrypted and handed only to that user's runs; a
  GitHub App installed per repository is the likely route to GitHub.
- **Subscription terms come first.** Subscription plans are generally meant for individual use.
  Running other people's subscription logins on a shared server needs checking against the
  providers' terms before any multi-user work; API keys are the clearly supported route for a
  hosted service, so the order (subscriptions first, API keys later) may need reversing once other
  people use it.
- **Access from outside** goes through a tunnel or a VPN with a proper login, not open ports.

A possible path, to refine:

| Stage | What it gives |
|---|---|
| 1. One user, always on | The Controller and the orchestrator run on an always-on homelab node, so work continues with the desktop off. Mostly setup. |
| 2. The control room | O7 for the user's own runs: see everything, start runs, change settings, answer escalations. |
| 3. Sandboxed workers | Each run in its own container or virtual machine, a job queue, several worker nodes. |
| 4. Several users | Accounts, encrypted credentials per user, a GitHub App, per-user budgets (C8's levels), hardened isolation. |
| 5. API keys and other providers | Builds on harness portability (5). |

The roadmap already builds towards it: the orchestrator series (11.11), concurrency (6), the forge
adapter (7.6) and harness portability (5).

## Roadmap Principles

1. **Workflow remains authoritative.**
   Controller automates the lifecycle; it does not replace Workflow guarantees.

2. **Fail closed.**
   Ambiguous state, stale artifacts, unknown worker ownership, or unverifiable runtime identity must not silently advance the lifecycle.

3. **Durable state over worker prose.**
   Repository state, Workflow state, committed history, review ledgers, bundles, and job records are authoritative.

4. **One correctness model across runtimes.**
   Source execution, wheel installs, different agent harnesses, and observability modes must reconcile to the same lifecycle semantics.

5. **Gate ownership is explicit and policy-driven.**
   Controller must not hard-code today's manual gates. Workflow declares whether the current requirement is automatic, validation-driven, external, or human-owned. Human-only actions such as PR merge remain human-owned unless repository policy is deliberately changed.

6. **Public protocols, not implementation details.**
   Controller may depend on versioned Workflow, harness, forge and observation contracts, but not on copied artifact-path rules, internal Workflow helper names, provider-specific event formats, or forge-specific lifecycle semantics.

7. **External systems report facts; Workflow owns lifecycle meaning; Controller owns orchestration.**
   Harnesses report execution, forges report repository/PR facts, and Workflow decides what those facts mean for lifecycle validity and evidence freshness.

8. **Observation is passive by default.**
   CLI/web dashboards and metrics read durable events and telemetry without consuming model quota or changing execution.

9. **Dogfood continuously.**
   Workflow Controller should operate on its own repository and on RepFlow as realistic integration targets.

10. **Prefer bounded milestones.**
    Correctness and migration safety are easier to review when changes are scoped and independently accepted.
