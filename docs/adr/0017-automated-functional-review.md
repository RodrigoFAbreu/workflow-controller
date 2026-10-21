# ADR 0017: Run the functional review in a Controller-built sandbox

Status: accepted (2026-10-19). Builds on
[ADR 0013](0013-review-only-harness-seam.md) (the disposable-copy review
machinery and the courier that hands a result to the Workflow) and
[ADR 0016](0016-pull-request-report-and-apply.md) (a Controller step reports
evidence with `record-external-result`). The code is
`controller/functional_review.py`, `controller/sandbox_fakes/`,
`controller/review.py` (the `functional` run kind) and `controller/job.py`; the
operator's view is "The automated functional review" in the
[automation guide](../guide/automation.md#protocol-mode-workflow-27-and-later).

## Context

Under an automatic acceptance gate the Controller still stopped at the
Workflow's row 38e (`functional.evidence.external`) for a person to run the
checklist and record one `functional_evidence` result per flow. Earlier
milestones showed what an unisolated tester can do: it can push to the real
origin, call the real `gh` and edit the product to make a flow pass.

## Decision

1. **The Workflow decides; the Controller runs and reports.** The step acts
   only at row 38e under an automatic acceptance gate (`functional.automated`
   true). Rows 37 and 38 keep their launches. The Controller reads no gate
   policy, so a policy's `required_flows` are not supported: a checklist that
   cannot satisfy the Workflow ends at `functional_unchanged`.
2. **The tester is a job on the review machinery, with enforced isolation.**
   A `functional` run kind of `controller/review.py` starts Claude Code with
   its own shell sandbox required (bubblewrap and `socat`), `Bash` as the only
   tool, no unsandboxed retry and a write allowlist of the output folder, the
   scratch directories and locations derived from the repository's
   `.gitignore`. The pinned CLI's enforcement is proved by tests that run under
   the real settings (`tests/test_functional_enforcement.py`), not assumed.
3. **The sandbox is built by the Controller.** A `--no-hardlinks` clone of the
   branch whose `origin` is a scratch bare repository; launcher scripts for a
   fake `gh`, `claude`, `codex` and `ssh` that log and refuse; scratch `HOME`,
   `XDG_*` and `TMPDIR`; `GIT_ALLOW_PROTOCOL=file`, `GIT_SSH_COMMAND=false`.
   Afterwards the Controller compares the real repository's snapshot, the job's
   private Claude home and a path origin's refs, and checks the clone against a
   Controller-owned manifest of the candidate it handed over with no Git run in
   the clone. A difference is an `isolation_breach` or `tested_bytes_unbound`
   and records nothing.
4. **One result per flow, at the head the sandbox ran.** `RESULTS.json` is
   validated strictly; a failure carries `reason` `defect` or `environment`.
   Each entry becomes a `functional_evidence` record bound to the head, the
   checklist blob and the log's digest.
5. **A defect becomes findings through the door a person uses.** The findings
   file is written at the Workflow's path under a header naming head,
   checklist and run, so identical failure text on a later head is not skipped
   as already applied. The tester's failures are bounded at three per work item.
6. **A person is asked only for a fault.** `environment`-only failures,
   missing sandbox components, unreadable checklists, unusable results, a
   breach, a refused record and the exhausted bound each stop with a named
   gate and a remedy; `functional.automated` false restores the old stop.

## Consequences

- An acceptance whose evidence is a clean checklist run needs no person.
- What the tester executed is its own trust boundary: a copy edited under its
  scratch `TMPDIR` that passes is not detected. The candidate stays
  read-only and unchanged, and the record names the run for an audit.
- Pull-request flows cannot run in the sandbox: the production forge checks
  (`controller/forge.py`, `scripts/workflow_forge.py`) stay unrelaxed, so such a
  flow is `environment` and ends at `flows_not_runnable`.
- Only Claude Code declares the tester role; a Codex tester is a follow-up.
- The vendored Workflow tree is 2.12.0; a newer Workflow's row 38e is follow-up
  work.
