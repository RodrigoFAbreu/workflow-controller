# Active Milestone

## In progress: `workflow-controller-workflow-2-9-0-move` (ROADMAP C9c)

Plan revision 3, base `e6ff30b`. Implementing one checkpoint per invocation.

- **CP1 complete.** `tests/workflow_releases/2.9.0/` is vendored from the published archive
  (`archive-sha256:0f0af156…905a`); the sync subset gained `GATE_POLICY_PATHS`
  (`scripts/workflow_forge.py`, `scripts/workflow_gate_policy.py`); the 2.5.1, 2.6.0 and 2.7.0
  trees are unchanged. `tools/workflow_releases.py check` exits 0 and
  `python3 -m unittest tests.test_workflow_releases` passes.
- **CP2 complete.** `InstalledReleaseTest` admits the installed release by capability (legacy-validated,
  or its vendored tree lists `scripts/workflow_protocol.py`) with a converse test for a tree that is
  neither; `ProtocolAdmissionTest.test_every_vendored_protocol_release_is_runnable` installs 2.7.0 and
  2.9.0 into seeded disposable repositories and checks `describe` major 1, protocol-mode admission and a
  healthy `verify` (every check `pass`); the live-repository admission test accepts a legacy-validated
  release or a protocol result. `tests.test_workflow_releases`, `test_managed_repo`, `test_protocol` and
  `test_protocol_lifecycle` pass (the live-Manager test skips here; CP3's rehearsal runs it).
- **CP3 complete.** `docs/guide/development.md` and `docs/compatibility.md` state the per-release command
  count, the gate-policy scripts, capability admission and that this repository moves to 2.9.0 in a later
  `chore:` pull request; the roadmap C9c row and follow-up 15 name that update as the next action.
  Rehearsal (scratch clone of this branch, scratch bare origin, real Workflow Manager 1.5.0
  `--release-version 2.9.0 update`, committed): `tools/workflow_releases.py check` exit 0;
  `tests.test_workflow_releases` OK; complete CI selection `tools/run_tests.py --serial` ran 3087 tests
  including the nine installed conformance suites (all exit 0) and
  `test_real_workflow_manager_admits_this_repository` (ran, ok); the only failures are the two Git 2.56
  trailer tests (follow-up 12). The same update on `main` fails `InstalledReleaseTest` (reproducing
  PR #30). `explain` of the updated clone admits in protocol mode, exit 0. On this branch itself:
  `check_docs` exit 0 and the full serial selection fails only the same two tests.
- **Self-review (SELF_REVIEWING_IMPLEMENTATION).** No blocking findings. Fixed: `docs/compatibility.md`
  no longer puts the move sentence between the admitted releases and "Any other release is refused";
  the runnable-tree test asserts both 2.7.0 and 2.9.0 are among the protocol trees it installs (R3);
  the `is_vendored_path`, module and `workflow_release_tree` docstrings name the optional paths and
  are rewrapped.

---

## Status

**Complete.** `workflow-controller-protocol-warn-status` (`docs/ROADMAP.md` step C9b, follow-up 11)
reached `MILESTONE_COMPLETE` on 2026-10-09. Controller 1.7.1 treats a Workflow `verify` check with
status `warn` as advisory. The check is not refused: it is shown as a `verify <check>: warn: <detail>`
line in every decision's evidence, and the unhealthy gate lists it after the failing checks. An
undefined status is still refused with exit 20. The Workflow 2.9.0 protocol schema (protocol 1.2)
is vendored byte for byte. `inspect` advises about the four catalogue action ids this release cannot
launch. The task and reference pages document 2.9.0 and the 1.7.0 refusal.

The user accepted the milestone after functional review round 2 (implementation revision 4), which
was checked against evidence commit `58ecff2591a1f3e40135681c396fc6085e4ebdf5` and came back clean.
- Plan revision 2 was approved at `46f0fbb`. The base commit is `8cab96d`.
- Technical approval `8a78b0a` is `CURRENT`. It is an `EXTERNAL_APPROVE` of bundle `46feeec5`, with
  review content id `1ff6add7` and reviewed implementation head `975d10b`.
- External review round 1 asked for the job path to use the shared `decide_after_preflight`
  (revision 2). Functional review round 1 found an unisolated checklist setup, stale page headers and
  wording; these were fixed as a bounded change (revisions 3 and 4) and reviewed again by both
  implementation-review stages.

All registry checkpoints are `COMPLETE`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth
record of this transition, and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks step C9b
and follow-up 11 complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-protocol-warn-status.md`.

Deferred follow-ups, not conditions of acceptance:
- **Merge and release.** The milestone branch is on PR #28, titled
  `fix: accept the protocol's warn check status and vendor the Workflow 2.9.0 schema`. Once every
  check passes, the PR is squash-merged without editing the commit message, and the release
  workflow publishes 1.7.1.
- **The post-1.7.1 docs pull request.** It holds:
  - the 1.7.1 release notes (`docs/releases/1.7.1.md`);
  - the optional notes from functional review round 2: one sentence that an undefined status is
    still refused on four more pages, and a checklist setup that pushes `main` to the scratch origin;
  - "the latest compatible Workflow at the time of the move" wording;
  - a roadmap entry for workflow-controller#27 (a cross-session message fails a worker).

## Next action

`workflow-controller-workflow-2-9-0-move` (C9c) is in functional review (see the checklist below).
After a clean review, `/accept-milestone` records acceptance. The `chore:` update pull request that
moves this repository to Workflow 2.9.0 follows acceptance (D3). Then C9d, then C8.

## Functional review checklist

Work item `workflow-controller-workflow-2-9-0-move` (plan
`docs/ai-workflow/CONTROLLER_WORKFLOW_2_9_0_MOVE_PLAN.md`). The milestone vendors the Workflow 2.9.0
release tree under `tests/workflow_releases/2.9.0/`, extends `tools/workflow_releases.py` with the
gate-policy scripts, admits a protocol release by capability in `InstalledReleaseTest`
(`tests/test_workflow_releases.py`) and `tests/test_managed_repo.py`, adds a runnable-tree test, and
updates the docs. It changes nothing in `controller/`.

### Setup (isolation is required)

- Do everything in `mktemp -d` scratch directories. Never modify the real repository or
  `~/Workspace/workflow`, and never touch `~/.config/workflow-controller/`.
- Set `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` to scratch directories, except where
  the real Workflow Manager release cache is needed (the rehearsal in flow 3).
- Every clone must have its `origin` set to a scratch bare clone
  (`git remote set-url origin <scratch bare>`), so nothing can push to the real remote.
- If you use a Controller `step` or `run`, put `tests/fake_gh.py` first on `PATH` as `gh`, with
  `FAKE_GH_STATE` and `FAKE_GH_ORIGIN` set. Prefer `explain` or `inspect`, which do not push.
- The real `workflow-manager` and `workflow-controller` must be on `PATH`.

### Flows

1. **Release tree check.** Run `python3 tools/workflow_releases.py check`.
   Expected: exit 0, and 2.9.0 is listed with its gate-policy scripts.
2. **Targeted tests.** Run `python3 -m unittest tests.test_workflow_releases tests.test_managed_repo`
   in the repository. Expected: pass (apart from the Git 2.56 limitation below).
3. **Scratch rehearsal.** Clone the repository into a `mktemp` directory, point `origin` at a scratch
   bare clone, run `workflow-manager --release-version 2.9.0 update <clone>`, and commit the result.
   Then run `python3 -m unittest tests.test_workflow_releases tests.test_managed_repo` in the clone.
   Expected: the update succeeds and the tests pass, including the runnable-tree test.
4. **Same clone at `main`.** Check out `main` in a second scratch clone and run
   `python3 -m unittest tests.test_workflow_releases.InstalledReleaseTest`. Expected: it fails, because
   the installed release has no vendored tree there.
5. **Explain admits the clone.** Run
   `workflow-controller --routing-config /home/rodrigo/Workspace/workflow-controller/.controller/routing.json explain <clone from flow 3>`.
   Expected: the repository is admitted (no protocol refusal).
6. **A "neither" tree is rejected.** Hand-make a tree with no protocol script that is not a validated
   release, and pass it to the admission predicate used by `InstalledReleaseTest`. Expected: rejected.
7. **Docs.** Run `python3 tools/check_docs.py`. Expected: exit 0.

### Known limitations

- The actual update to 2.9.0 is a later `chore:` pull request (D3).
- C9d removes the vendored-tree requirement.
- Two tests fail locally on Git 2.56 (follow-up 12); they are unrelated to this milestone.
