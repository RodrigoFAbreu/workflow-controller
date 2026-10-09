# Compatibility

> For: anyone choosing a Controller or a Workflow release. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0.

This page is the one place that says which Controller release admits which
Workflow release. Other pages and other repositories link here instead of
repeating the table.

## Which Controller admits which Workflow

| Controller | Workflow releases admitted | How |
|---|---|---|
| 1.7.0 and later | 2.5.1 and 2.6.0; 2.7.0 and any later release that ships the protocol (2.8.0 and 2.9.0 are checked; 1.7.1 is the first release that accepts a `warn` check) | 2.5.1 and 2.6.0 by exact release, in legacy mode; every other release by capability, in protocol mode |
| 1.3.0 to 1.6.x | 2.5.1 and 2.6.0 | by exact release |
| 1.2.1 and earlier | 2.5.1 | by exact release |

Under 1.7.0 and later, a release is admitted by capability when its installation
record lists `scripts/workflow_protocol.py` and that script's `describe` answers
protocol major 1. Workflow 2.7.0 is the first release that does. Controller 1.7.0
admits 2.8.0 this way, with an advisory about action ids it does not know, and stops
at the 2.8 automatic gates (see [run](run.md)). Controllers 1.7.0 and 1.7.1 both admit
2.9.0, and 1.7.0 refuses it only when `verify` reports `warn`. Controller 1.7.1
accepts the Workflow's `warn` check status as advisory: `explain` shows it and nothing is
refused. The advisory lists the action ids it cannot launch (`acceptance.satisfy`,
`implementation.satisfy`, `plan.satisfy` and `pr.apply_review`) and any invented id, and
stays silent for the four gates it never launches. Controller 1.7.0 refuses `explain`,
`step` and `run` (exit 20) on a repository whose `verify` reports `warn`: for example an
unadopted gate policy file, or, while it is the newest adoption, one that lowers a gate.
Upgrade to 1.7.1. Any other release
is refused: 2.5.0 and 2.6.1 as not validated, a release older than the 2.5
line as outside the supported lines, a newer release with no protocol script as
`no_protocol`, and one whose protocol is not major 1 as
`unsupported_protocol_major`.

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

## Where the design is recorded

- [ADR 0006](adr/0006-workflow-release-admission-and-per-release-contracts.md):
  admission by exact release and per-release contracts.
- [ADR 0010](adr/0010-orchestration-protocol-admission-by-capability.md):
  admission by capability.
- [Moving a target to another Workflow release](update.md#move-a-repository-to-a-newer-workflow): the steps.
- [Release history](release-history.md) and the [glossary](glossary.md).
