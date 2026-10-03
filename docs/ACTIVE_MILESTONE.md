# Active Milestone

## In progress: `workflow-controller-orchestration-protocol-v1` (ROADMAP step C9, release 1.7.0)

Plan revision 11 is approved (`8b29d66`, amendment 0: the artifacts declaration and the checkpoint anchors only; the design is unchanged, so each checkpoint is revalidated against its anchored section). Implementation runs one checkpoint per session;
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for which are `COMPLETE`. The section
below describes the previous milestone (C4) and is kept until this one is accepted.

- **CP1, the protocol client** -- `controller/protocol.py` runs one Workflow Orchestration Protocol
  operation (`--protocol-major 1`, `--repo-root`) from a private copy of the Workflow's own script
  set, taken from the installation record's `managed` map (`scripts/<name>.py`, not `_test`), with a
  path-to-digest map derived afresh for every call and the ADR 0006 Git isolation that
  `workflow_contract.run_in_private_copy` now shares. `controller/protocol_schema.py` validates every
  envelope and result against the vendored 2.7.0 schema (`controller/protocol_schema.json`, sha256
  pinned) with a stdlib validator for the schema's keyword subset: an unsupported schema keyword is
  refused at load, and for documents `additionalProperties: false` and the minor-extensible enums are
  open, so a protocol 1.1 answer validates. `WORKFLOW_PROTOCOL_FAILED` and
  `WORKFLOW_PROTOCOL_UNSUPPORTED` (exit 20) are the new refusals. `tests/workflow_releases/2.7.0/`
  vendors the published archive (`tools/workflow_releases.py sync --archive-sha256`), including the
  protocol script, its sibling `workflow_test_harness.py` and the schema.
  Tests: `tests/test_protocol.py` (including a round trip against the real 2.7.0 scripts),
  `tests/test_protocol_schema.py`.
  Revalidated against plan revision 11 (the CP1 section is unchanged by the amendment): the 69
  protocol, schema and package-structure tests pass.
- **CP2, admission by capability** -- `managed_repo.inspect`'s version gate is two-way: a release with
  a `RELEASE_CONTRACTS` entry (2.5.1, 2.6.0) is legacy mode exactly as before; any other release is
  protocol mode when the installation record's `managed` map lists `scripts/workflow_protocol.py` and
  `describe` answers protocol major 1, else refused (`no_protocol` for a release newer than the
  supported lines, `unsupported_protocol_major`, the unchanged `outside_supported_line` /
  `unvalidated_release`). `ManagedRepository` gains `target_protocol` and `script_digests`;
  `protocol.identity()` is the one identity function (the release `describe` reports and the managed
  script digest map, derived afresh; a non-Workflow `scripts/extra.py` never enters it). The job pin,
  the per-decision re-check and `WORKFLOW_RELEASE_CHANGED` are CP4's.
  Tests: `tests/test_managed_repo.py` (`ProtocolAdmissionTest`, `ProtocolIdentityTest`); the 2.7.0
  case in `test_managed_repo` and `test_cli` now expects `no_protocol` on purpose.

- **CP3, decisions from `next-action`** -- `controller/protocol_decision.py` turns the Workflow's own
  `next-action` answer into the existing `Decision` (plus a `protocol` attribute carrying the answer as
  received, its row, state identity and release). `automatic` with a known action id, a worker role the
  Controller knows and a non-`user_only` worker launches; `human_gate`/`external_gate` and `blocked` are
  gates carrying the Workflow's reason, remedy and alternatives; `complete` is the no-action outcome;
  `validation` and any unknown disposition, action id or worker role are blocked gates
  (`workflow_unknown_*`). `PROTOCOL_ACTIONS` names the twelve automatic ids with a command token and a
  route key; the worker's command is rendered from it (`/<command> <work_item_id>`, plus the Controller's
  own base for `plan.start`), and an `invocation` that differs from the rendering is blocked
  `workflow_invocation_mismatch`. Two routing roles, `prepare-functional-review` (inherit) and
  `apply-functional-review` (Opus), join `routing.ROLE_ROUTES`/`ROLES` only (not `ROLE_BY_COMMAND_STEM`),
  and `settings.TABLE_GENERATION` is 3. 1.6.0's committed-state gate applies in its own scope only.
  A protocol target never enters `evidence.decide` (`job._execute_step_locked`, `cli explain`); its
  pre-state reads no lifecycle file. `explain` shows the row, disposition and action (additive).
  **Interim, removed by CP5:** `job._protocol_launch_unavailable` declines every protocol launch, because
  nothing verifies a protocol job's outcome until CP5's protocol branch of `_verify_transition`; the job
  record's `protocol` block and the currency checks are CP4's.
  The equivalence comparison (`tests/golden/generate_protocol_vs_legacy_differences.py`, table
  `tests/golden/protocol_vs_legacy_differences.json`) runs 58 fixture repositories through 1.6.0 and the
  protocol and holds exactly the seven observable differences D1-D7, each asserted by name.
  Tests: `tests/test_protocol_decision.py`, `tests/test_protocol_equivalence.py`; the pins changed on
  purpose are `tests/test_routing.py` (the role set) and `tests/test_settings.py` (`V1_ROLES`,
  `TABLE_GENERATION == 3`, plus a file filled by this release read by a 1.6.0-shaped release).
  `tests/test_job.py`'s `worker_result_prose_feeds_only_the_report` now skips `protocol.py`, whose
  `Envelope.result` is the protocol's answer (it failed at CP1/CP2). Full suite: 2939 tests pass.

## Status

**Complete.** `workflow-controller-auto-merge-release-wait` (`docs/ROADMAP.md` step C4, section
11.3) reached `MILESTONE_COMPLETE` on 2026-10-03. When a repository opts in
(`milestone_branches.pull_request.auto_merge`) and the operator's settings have not turned it off
(`merge.auto`), the Controller now finishes an accepted milestone by itself:
- **it merges**: one head-bound `gh pr merge --squash --match-head-commit <A>` per attempt at the
  acceptance commit, never GitHub's own auto-merge request; a draft, a later commit, a red check or
  a conflict stops it at a gate that names the exit (`merge_pending`, `merge_held`);
- **it waits for the release**: it classifies the squash commit read-only, waits for the
  publishing workflow, records the published release or stops at `release_failed`
  (`release_pending`, `release_failed`);
- **it closes out and stops**, instead of planning the next milestone in the same run;
- **`run` waits without a worker**: the pending gates are polled every `merge.poll_seconds` for up
  to `merge.wait_seconds` per step; `status`, `status --json` and `inspect` show the merge and the
  release (`docs/adr/0009-auto-merge-and-release-wait.md`).

A policy without the key behaves exactly as 1.5.0 (plan I1).

The user accepted it in functional review round 3 (implementation revision 11), against checklist
evidence commit `5f95a08d66d09509f789084bf6deb4149ee0521a`.
- Plan revision 7 was approved at `a06aeb3` (`EXTERNAL_APPROVE`, review content id `fcdfd33b`).
  Decision 9 deliberately departs from the roadmap's wording: the Controller sends its own
  head-bound squash merge rather than enabling GitHub's auto-merge, which could merge a later
  push. Decision 2 (stop after close-out when opted in) was raised at approval.
- Implementation revisions 1-5 were revised after local and Codex review rounds; the fixes include
  deciding an accepted merge before the pull request's lagging reads (`5689d82`), a fresh merge
  record on `--new-pr` (`c8dae39`), deciding a lost merge reply from the trunk rather than refusal
  text (`4fd9bbe`) and adopting a trunk squash only when its content is the acceptance commit's
  (`a595551`). Both stages approved revision 6 (technical approval `3d875a7`).
- Functional review round 1 (checklist `10a6efd`) found F1-F6 (gate texts, the guides, Ctrl-C in a
  waiting run, the fake `gh`'s merge texts against real `gh` in the optional live flow Q). They
  were fixed in revisions 7-8; revision 9 names a waiting run in exit 45 only when it holds the
  lifecycle lock (`c89f2ba`) and revision 10 tests it (`9efa17c`); technical approval `08dd5c1`.
- Functional review round 2 (checklist `15f7823`) found R2-F1: the F6 body rewording reached
  bindings without `auto_merge`, against I1. Fixed in `7f24c3a` (implementation revision 11); both
  stages approved it (technical approval `6e4e991`, `EXTERNAL_APPROVE` of bundle `0b2b25f3`,
  `CURRENT`). Functional review round 3 re-ran flows D, E, K, M and O, and it was clean.

All six registry checkpoints (`CP1`-`CP6`) are `COMPLETE`, and the registry declares no completion
obligations. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this transition,
and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 11.3 and step C4 of "At a
glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-auto-merge-release-wait.md`. It covers the goal,
checkpoint progress, the review-round fixes, functional review round 3's checklist and the 1.6.0
release notes.

This milestone's own deliverables remain live in the tree, unmoved (the archive file's own preface
says why):
- `docs/ai-workflow/CONTROLLER_AUTO_MERGE_RELEASE_WAIT_PLAN.md` and its registry, artifacts and
  mapping files, still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry declares;
- the changes to `controller/milestone_branch.py`, `cli.py`, `decision.py`, `forge.py`, `job.py`,
  `lock.py`, `observe.py`, `release_txn.py`, `repo_policy.py` and `settings.py`;
- the fake `gh`'s merge, merge-state, workflow-run and release model (`tests/fake_gh.py`), the
  regenerated `tests/golden/no_policy_lifecycle.json`, and the additions to `tests/test_cli.py`,
  `test_evidence.py`, `test_forge.py`, `test_lock.py`, `test_no_rewrite_invariants.py`,
  `test_observe.py`, `test_pull_request_lifecycle.py`, `test_release_txn.py`,
  `test_repo_policy.py`, `test_settings.py` and `test_trunk_preflight.py`;
- `docs/adr/0009-auto-merge-and-release-wait.md`; `README.md`; `docs/guide/milestone-branches.md`,
  `automation.md`, `ci-and-releases.md`, `commands.md`, `concepts.md`, `runtime.md` and
  `troubleshooting.md`, and `docs/README.md`.

`.workflow-controller/policy.json`, `pyproject.toml`, `setup.py` and `.github/workflows/` are
unchanged from the base `854d25c` (plan I9: this repository does not opt in yet).

Deferred follow-ups, not conditions of acceptance:
- **Release 1.6.0.** The milestone branch is on Draft PR #18, titled
  `feat: auto-merge an accepted milestone and wait for its release`. The pushed head is `5f95a08`;
  this acceptance commit is local only. This repository has not opted in to `auto_merge`, so the
  release goes the 1.5.0 way: the next Controller step pushes it and runs readiness. Once every
  check passes, the PR is marked ready and merged with "Squash and merge", without editing the
  commit message. `main.yml` then classifies `RELEASE_DUE` 1.6.0 from `v1.5.0` and publishes it,
  and the next Controller step closes the milestone out (`MERGED_SQUASHED` → `CLOSED`).
- **Install timing.** 1.6.0 goes into the shared install only between Workflow Manager milestones
  (shared lane plan), since the Manager lane's Controller runs from it.
- **The post-C4 docs pull request**:
  - `docs/releases/1.6.0.md` from the archived narrative's `## Release notes` section (release notes
    are still not turned on in `.workflow-controller/policy.json`);
  - the roadmap order the user set on 2026-10-02: C9, then C8, then C5, then C6, C7, C10 and C11.
    C8 before C7 means C8 tracks Claude's limits on its own and reads Codex's without the C7 seam,
    or defers the Codex part; C8's plan says which;
  - in `AMENDING_PLAN`, `run` declines `/milestone-plan` (exit 15) because no `ExpectedOutcome` is
    declared for (`AMENDING_PLAN`, `"2.2"`, `/milestone-plan`), reported by the Manager lane on
    2026-10-02; it sits next to the `explain` item already listed under "Known follow-ups carried
    forward";
  - two CI timing flakes in code this milestone did not touch, both passing on a re-run:
    `tests.test_resume.ReattachAfterControllerLossTest.test_r15` and
    `tests.test_worker.OwnershipTest.test_a_gated_escapee_is_published_as_group_then_as_tag`;
  - a CP3 supervise test can leak its fake worker (`tests/fake_claude.py`) under load, which left
    a Controller job draining for three hours on 2026-10-02.
- Opting this repository in to `auto_merge` (and to release notes) is a later small `chore:` pull
  request, after 1.6.0 is released and installed into the shared install.
- Left as the plan scoped them: the fix loop for a red pull request after acceptance and automatic
  acceptance (C10), integrating `main` into a milestone branch, merge mode and merge queues,
  publishing or retrying a release, and notifications (C5). A close-out from the trunk does not
  fast-forward `main`, as in 1.5.0.
- `tests/golden/generate_plan_stage_decisions.py --check` (without `--release`) reports that its
  `AMENDING_PLAN` cases differ in this environment. It does the same at the base, so this milestone
  did not change it, and `tests.test_golden_plan_stage_decisions` passes.
- Carried over, unchanged: the deferred items of the earlier milestones, listed in their
  acceptance commits.

**Next action:** release 1.6.0 as above, then merge the post-C4 docs pull request. After close-out,
run `/milestone-plan` for step C9 of "At a glance": the Controller on Orchestration Protocol v1
(section 1.7), which the user put next on 2026-10-02. Its dependency W1, Workflow 2.7.0, was
published on 2026-10-02. (The roadmap table still lists C5 next until the docs pull request
reorders it.) Plan it from `main`'s tip, with that base passed explicitly
(`/milestone-plan <main tip>`). `/milestone-plan` creates a fresh `work_items` entry and claims
`active_work_item_id`, ready for `PLANNING`.
