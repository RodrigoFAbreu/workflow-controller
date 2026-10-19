# A1: harness and git host adapters, and the usage record (`workflow-controller-adapters-1`)

This is the record of milestone `workflow-controller-adapters-1` (`docs/ROADMAP.md` step A1), archived when it was
accepted on 2026-10-19. Plan: `docs/ai-workflow/CONTROLLER_ADAPTERS_1_PLAN.md`, revision 5, approved by policy at
`b3d5f08`; base commit `859eda5`. The checklist below is the active-milestone page as it stood during the functional
review; the later sections were added at acceptance.

## Functional review checklist

Findings go to `.ai-review/workflow-controller-adapters-1/feedback/FUNCTIONAL_REVIEW.md`. This milestone is a refactor
plus one additive record: nothing the Controller decides, prints or launches may change. The only output change is the new
`usage_record` key in `resume --json` (plan D6).

### Setup

- Branch `milestone/workflow-controller-adapters-1`, clean tree. Run the tests from the checkout; do not set
  `PYTHONPATH=.` (it breaks the decoy test).
- Test data: none to seed. All flows use the unit suite and the checkout.

### Flows

1. **Harness adapter registry (R1).** `python3 -m unittest tests.test_harness`. Expected: pass. `claude-code` and `claude`
   resolve to one adapter; asking `codex` for the worker role raises `HarnessRoleError` naming the harness, the role and the
   declared roles; an unknown name raises `UnknownHarnessError` with the registered names; the derived tables
   (`review_mode.HARNESS_FAMILY`, `telemetry.HARNESS`) equal the old literals.
2. **Git host registry (R2).** `python3 -m unittest tests.test_forge_adapter tests.test_forge`. Expected: pass;
   `set(FORGES) == repo_policy.FORGE_KINDS`; an unregistered `forge_kind` raises `ForgeKindUnregisteredError`; a
   `forge_factory` still wins.
3. **Usage record and converters (R3).** `python3 -m unittest tests.test_usage_record`. Expected: pass. Claude figures equal
   the telemetry block's for every captured stream; a Codex rollout gives `derived` figures; nothing is invented for a
   stream with no result; `controller/prices.json` ships with no model prices, so a Codex cost is `null`/`unknown`.
4. **Record written at job end (R4).** `python3 -m unittest tests.test_usage_record_job`. Expected: pass. A worker record
   carries `usage_record` exactly when it carries the `telemetry` block (this includes a job reconciled FAILED after the
   worker's result was read, e.g. exit 7, and a worker that launched but exited without emitting a result; a job that fails
   outside the completion path, such as a failure to launch, has neither), and every review end in
   which an attempt ran carries it; a review that ended before any attempt carries none; an injected converter failure leaves the job identical to a control run except for
   `usage_record_failed`.
5. **Nothing else changed (D1).** `git diff 859eda5 HEAD --stat -- tests/` lists new test files and only registration edits
   to existing ones (`test_package_structure.py` and the small fixture additions); read each edited existing test and
   confirm no expectation changed. Run `python3 -m unittest tests.test_review tests.test_review_claude tests.test_review_mode
   tests.test_telemetry tests.test_worker_stream tests.test_fake_codex_contract tests.test_no_rewrite_invariants`.
   Expected: pass.
6. **`resume --json` (D6).** In `tests/test_usage_record_job.py`, find the `resume --json` cases. Expected: a terminal record
   with `usage_record` / `usage_record_failed` prints the key; one without it prints the pre-milestone object; exit codes
   identical.
7. **Package data.** Build or inspect the wheel layout (`python3 -m unittest tests.test_package_structure`). Expected:
   `controller/prices.json` is in the package data and loads from an installed layout; the new modules sit at the positions
   in D1a.
8. **Documents (R5).** `python3 tools/check_docs.py` passes. Read the adapter and usage-record sections of
   `docs/guide/workers.md`, `docs/guide/how-it-works.md` and `docs/guide/development.md` (how to add a harness and a git
   host; price-table maintenance). Expected: each matches the code; `docs/ROADMAP.md` does **not** yet mark A1 complete
   (that happens at acceptance).
9. **Whole suite and goldens.** Run the full suite and each `tests/golden/generate_*.py --check` (never without `--check`).
   Expected: green except the two known Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`);
   the no-policy golden differs from base only by the `usage_record` key.

### Known limitations

- No model prices are in the table yet, so a derived cost is `null`; a Claude cost is the harness-reported one.
- No consumer reads the usage record (A3 and the router do later); `telemetry`, `inspect`, `status` and `follow` still read
  the telemetry block.
- Codex as a worker, a second git host and routing are out of scope (A5, later adapters).
- The merge of the `refactor:` title releases 1.16.1 after acceptance; release and notes are post-merge steps.

### Functional review round 1

- Finding 1 (documentation and test, bounded): the rule for a FAILED worker is now stated in the guides and in flow 4; a test covers a non-zero exit after a result. Re-test flow 4 and flow 8.

## Acceptance

Accepted **by gate policy, not by a person**, on 2026-10-19: `/satisfy-gate acceptance` found every acceptance
requirement met (policy source `default`, digest `bae758b4f4ce`). Every checkpoint (CP1-CP5) is `COMPLETE`, the
technical approval `2761ec0` (implementation revision 6) is `CURRENT`, flows flow-1 to flow-9 passed, and the
Workflow's own GitHub query found pull request #102 at head `4e8d287` with every check passing and no standing
objection. The pull request title is `refactor:`, so it releases the patch release 1.16.1 when it merges. The release
notes come in the docs pull request after the release.

## Review history

- **Plan.** Revision 5 was approved by policy at `b3d5f08` (local and Codex plan rounds; the findings were about import
  cycles, the forge factory order, the price-table reader, partial reasoning totals and Codex cache-write semantics).
- **Implementation.** Technical approval `2761ec0` (implementation revision 6) after six local rounds and five Codex
  rounds.
- **Functional.** Round 1: an independent Codex tester (report `functional-tester-round1-RESULTS.md`) found the rule for
  a FAILED worker missing from the guides; it was fixed. Round 2: the operator re-test at revision 6 passed flows 4, 8
  and 9 (`functional-review-round2-operator.md`) and the tester's results stood for the rest. Checklist evidence commit
  `4e8d287`.

## Minor review findings

Twenty-three Optional review items: thirteen fixed, four dropped (plan wording, and the generated `TEST_RESULTS.md`)
and six kept as follow-ups, all in issue #104 (partial reasoning totals, `wall_ms` meaning per job kind, a half-applied
rollout guard, an end-to-end retried-review test). The table with each reason is
`docs/milestones/completed/workflow-controller-adapters-1-findings.md`. Filling the price table is #103.
