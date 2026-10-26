# Compatibility

> For: anyone choosing a Controller or a Workflow release. Last checked with: Controller 1.19.1; Workflow 2.6.0, 2.7.0, 2.8.0, 2.9.0, 2.9.1, 2.9.2, 2.10.0, 2.11.0, 2.12.0, 2.13.0, 2.14.0, 2.15.0, 2.16.0, 2.18.2, 2.18.5, 2.18.6, 2.18.7, 2.19.1, 2.19.2 and 2.20.0.

This page is the one place that says which Controller release admits which
Workflow release. Other pages and other repositories link here instead of
repeating the table.

## Which Controller admits which Workflow

| Controller | Workflow releases admitted | How |
|---|---|---|
| 1.7.0 and later | 2.5.1 and 2.6.0; 2.7.0 and any later release that ships the protocol (2.8.0, 2.9.0, 2.9.1, 2.9.2, 2.10.0, 2.11.0, 2.12.0, 2.13.0, 2.14.0, 2.15.0, 2.16.0, 2.18.2 and 2.18.5 are checked; 1.7.1 is the first release that accepts a `warn` check) | 2.5.1 and 2.6.0 by exact release, in legacy mode; every other release by capability, in protocol mode |
| 1.3.0 to 1.6.x | 2.5.1 and 2.6.0 | by exact release |
| 1.2.1 and earlier | 2.5.1 | by exact release |

Under 1.7.0 and later, a release is admitted by capability when its installation
record lists `scripts/workflow_protocol.py` and that script's `describe` answers
protocol major 1. Workflow 2.7.0 is the first release that does. Controller 1.7.0
admits 2.8.0 this way, with an advisory about action ids it does not know. Controllers 1.7.0 to 1.13.1 stop
at the 2.8 automatic gates; later releases launch `/satisfy-gate` there (see [run](run.md)). Controllers 1.7.0 and 1.7.1 both admit
2.9.0, and 1.7.0 refuses it only when `verify` reports `warn`. Workflow 2.9.1 and 2.9.2
speak the same protocol (1.2) and are admitted the same way; Controller 1.8.0 was checked
against 2.9.2. Workflow 2.10.0 and 2.11.0 speak protocol 1.3 and are admitted the same way; Controller 1.9.0
was checked against 2.10.0, and Controllers 1.10.0 and 1.11.0 against 2.11.0. Workflow 2.12.0 speaks
protocol 1.4, which adds the review round to the next-action snapshot (an additive change), and is
admitted the same way; Controllers 1.11.0, 1.12.0, 1.13.0, 1.13.1, 1.14.0 and 1.15.0 were checked against it. Workflow 2.13.0 speaks the same
protocol (1.4) and is admitted the same way; Controllers 1.13.1, 1.14.0 and 1.15.0 were checked against it. Workflow
2.14.0 also speaks protocol 1.4 and is admitted the same way; Controllers 1.15.0, 1.16.0 and 1.16.1 were checked against it (1.16.0 and 1.16.1 speak the same protocol, 1.4; CU2 and A1 were accepted on it, and the conformance tests pass). Workflow
2.15.0 speaks protocol 1.5, a minor addition (the four per-role round counts in the next-action snapshot's
`review_round`, null for an item created before 2.15.0), and is admitted the same way; Controllers 1.16.0, 1.16.1 and 1.17.0
were checked against it (protocol mode, protocol 1.5). Workflow 2.16.0 keeps protocol 1.5 (it changes only the managed
`workflow-conformance` job, which now runs the acceptance suite in four shards with a coverage check) and is admitted
the same way; Controller 1.17.0 was checked against it. Workflow 2.18.2, 2.18.5, 2.18.6, 2.18.7, 2.19.1, 2.19.2 and 2.20.0 also keep protocol 1.5 (2.17.0 adds the
optional backlog file, 2.18.0 the harness-neutral command files, 2.18.1 to 2.18.7 are fixes and speed-ups: 2.18.4 shortens
the conformance job, 2.18.5 and 2.18.6 stop a live same-worktree owner reclaiming a takeover's guard, 2.18.7 adds a checkpoint-start release function with no command yet and ends the 2.18.6 fork-test orphan; 2.19.0 adds the user-only `/release-checkpoint-start` command, which no automatic step uses, 2.19.1 only adds test pins and 2.19.2 only fixes the accuracy of documents and two refusal messages, 2.20.0 adds the read-only retention report `scripts/workflow_retention.py report`) and are admitted the
same way; Controller 1.18.0 was checked against 2.18.2 and Controller 1.19.0 against 2.18.5 and Controller 1.19.1 against 2.18.6 and 2.18.7 (protocol mode, protocol 1.5). The functional tester's sandbox layout does not depend on a Claude build's write-precedence rule, so a build, 2.1.304 and later included, is admitted once `workflow-controller verify-claude` has recorded it. That layout keeps the tester's writable folders outside the clone, behind links the Controller makes, so no write allowance lies below a denied path (resolved through links too). Controller 1.12.0
reads that review round for its review round cap; on an older Workflow it counts the round from its own
job records instead. Controller 1.17.0, which adds the local and external round caps, reads the four per-role counts
(protocol 1.5) and applies each cap to its own count; without them (a Workflow before 2.15.0, or an item with
null counts) it falls back to that single round, checked against the external cap. Controller 1.18.0 and later run the functional review themselves at row 38e (`functional.evidence.external`) under an automatic acceptance gate; they were checked against the vendored Workflow 2.12.0 tree's row 38e answers and need `bubblewrap`, `socat` and a verified native `claude` build (1.18.0: Claude Code 2.1.303) on the host for the tester's sandbox (without them, or with a build that is neither built in nor recorded by `workflow-controller verify-claude`, the step stops at `functional_tester_unavailable`; the recorded digests are per runtime root). Earlier Controllers stop at row 38e. Controller 1.14.0 and later no longer refuse a value a protocol 1.x minor release adds to a closed enum of an answer: it is
advisory, ignored or a stop, by a rule per enum location (a blocked gate before any launch for an action id, worker role or disposition; a `FAILED` job with the evidence reason `workflow_unknown_value` for a reconcile class or record stage) (see
[automation](guide/automation.md#protocol-mode-workflow-27-and-later)); only a protocol major change is refused.
Controller 1.7.1
accepts the Workflow's `warn` check status as advisory: `explain` shows it and nothing is
refused. An undefined status is still refused with exit 20. The advisory lists the action ids it cannot launch (`acceptance.satisfy`,
`implementation.satisfy`, `plan.satisfy` and `pr.apply_review` for 1.7.1 itself; Controllers 1.14.0 and later launch the three `/satisfy-gate` gates and so list only `pr.apply_review`, and none after the pull request defect loop) and any invented id, and
stays silent for the four gates it never launches. Controller 1.7.0 refuses `explain`,
`step` and `run` (exit 20) on a repository whose `verify` reports `warn`: for example an
unadopted gate policy file, or, while it is the newest adoption, one that lowers a gate.
Upgrade to 1.7.1. Any other release
is refused: 2.5.0 and 2.6.1 as not validated, a release older than the 2.5
line as outside the supported lines, a newer release with no protocol script as
`no_protocol`, and one whose protocol is not major 1 as
`unsupported_protocol_major`.

This repository itself runs Workflow 2.20.0, with automatic gates, since a `chore:` update pull
request (2026-10-26). It ran 2.19.2 from 2026-10-25, 2.19.1 earlier that day, 2.18.7 from 2026-10-24, 2.18.6 earlier that day, 2.18.5 from 2026-10-23, 2.18.2 from 2026-10-22, 2.16.0 from 2026-10-19, 2.15.0 earlier that day, 2.14.0 from 2026-10-18, 2.13.0 earlier that day, 2.12.0 from 2026-10-16, 2.11.0 from 2026-10-15, 2.10.0 from 2026-10-13, 2.9.2 from 2026-10-11 and 2.9.0 from 2026-10-09, after the milestone
that vendored that release's test tree, and skipped 2.9.1.

## What admitted means

A target is admitted when Workflow Manager verifies its installation and the
release recorded in `.workflow-manager/installation.json` is one the Controller
admits. Admission decides what the Controller reads from the Workflow and what
it asks the Workflow's own queries. It does not change the Workflow's rules.

A target on 2.5.1 behaves under 1.3.0 and later exactly as it did under 1.2.1.

To see what a Controller makes of a repository, run:

```
workflow-controller inspect .
```

It prints the Workflow release it admitted, or why it refused.

## Pinned scripts for exact releases

For a release admitted by exact release that answers its own queries, the
Controller runs only the bytes of that release. It checks the SHA-256 of the two
scripts it reads before it runs anything, and refuses on a mismatch. Release
2.5.1 has no queries, so it has no digests. For 2.6.0 they are:

| Script | SHA-256 |
|---|---|
| `scripts/workflow_state.py` | `f57b8c36d15a3e7b50085712e74e9337a93196a1411fc279ee6d0a0dcc2f0688` |
| `scripts/workflow_fingerprint.py` | `7e7fd9706e431d4c8676c3fa3b47b5c60e12e17ea90c50484fd3ad0aaf0b6092` |

A test keeps this table equal to the digests in the Controller's code.

## Downgrading after a review job

With `reviewer.harness` set, 1.10.0 writes a review job record into the runtime's `jobs` directory with job `schema_version` 2. Controller 1.9.0 and earlier refuse such a record (`unknown_schema_version`) instead of misreading it, so a runtime that holds one cannot be read by 1.9.0. If a review job is not settled, `resume` it with 1.10.0 first, then downgrade. Records written by 1.9.0 (version 1) stay readable by 1.10.0. The settings file is table generation 6; a 1.9.x Controller sharing it warns about the unknown `reviewer` section and ignores it.

## Routing a harness

Since 1.21.0 a routing entry may name a `harness` and the command line gains
`--role-harness`. The settings file is table generation 15 (generation 16 since 1.22.0, below); a Controller before
1.21.0 sharing it warns once about the unknown `harness` key and ignores it, its
`settings clean` refuses, and a `--routing-config` file with `harness` is
refused by it. This release accepts `claude-code` for every routing role and
`codex` for none, and refuses any other pair at load (exit 20, before a job
record exists). A later release that lets Codex take a role makes a file holding
that pair valid for it and refused by 1.21.0: update the Controller or remove the
entry. A same-provider review record's `worker_route.harness` reads `claude-code`
since 1.21.0 (it read `claude`); a reader that matched the old value needs the
new one.

## Upgrading to 1.22.0

Each harness adapter now declares the models and efforts it supports, and a route
whose model or effort the chosen harness does not declare is refused at the next
`run` or `step`, before any job record is written. Until 1.21.x a routing file or
settings file could name any model or effort and the `claude` CLI was the
authority; these configurations are newly refused:

- a model the harness does not declare (for `claude-code`: anything but
  `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`,
  `opus`, `sonnet`, `haiku`), including the built-in default of a role routed to a
  harness that does not declare it;
- an effort the harness does not declare (for `claude-code`: anything but `low`,
  `medium`, `high`, `xhigh`, `max`).

The refusal is exit 20 with a message such as `role 'review-plan' routes the model
'gpt-5' to the harness 'claude-code', which declares [...]`, followed by the source
of the model, the effort and the harness. The fix for a model is to add it to the
`harness.models` entry of the settings file (there is no command):

```json
"harness.models": {"claude-code": ["your-model-name"]}
```

An unsupported effort is changed in the routing; no setting adds an effort. The
settings file is table generation 16: the fill adds `"harness.models": {}`, and a
Controller before 1.22.0 sharing the file warns about the unknown key, ignores it,
and its `settings clean` refuses.

## Shell aliases and worker commands

Since 1.10.0 a worker is told to use `command rm -f` and `command cp -f` in background commands, so an `rm -i` or `cp -i` alias in the operator's shell cannot make it wait for an answer. A script of your own that a worker runs needs the same.

## Where the design is recorded

- [ADR 0006](adr/0006-workflow-release-admission-and-per-release-contracts.md):
  admission by exact release and per-release contracts.
- [ADR 0010](adr/0010-orchestration-protocol-admission-by-capability.md):
  admission by capability.
- [ADR 0013](adr/0013-review-only-harness-seam.md): the review-only harness
  seam and the review job record.
- [Moving a target to another Workflow release](update.md#move-a-repository-to-a-newer-workflow): the steps.
- [Release history](release-history.md) and the [glossary](glossary.md).
