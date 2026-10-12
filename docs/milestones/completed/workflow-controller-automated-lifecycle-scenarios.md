# Active Milestone

## In progress: automated lifecycle scenarios (`workflow-controller-automated-lifecycle-scenarios`, step C6)

Checkpoint **CP1** (the scenario kit) is complete. `tests/scenarios/` holds the registry, the kit and the sandbox
setup phase; `tests/test_scenario_kit.py` pins them with stand-ins. Verified: the two-phase sandbox (user, network
and mount namespaces, no PID namespace, a capability drop to the invoker's uid) cuts off the network and every real
location through a shell, a git hook, a `python -E` child and a re-exec; an unavailable sandbox fails closed; the
checkout snapshot is content-sensitive; the kill-points sit at the primitives the write inventory measured.
Measured against the plan: the milestone binding is published by `runtime.create_json`, and a kill *before* the
job record that names a spawned worker (`job-before`) leaves a stdin anchor that `resume` cannot reconcile (exit
45). The plan's oracle for that variant does not hold; no plan amendment was made (see CP3).

Checkpoint **CP2** (the runner) is complete. `tools/run_scenarios.py` has `list`, `run` and `check`: `run` writes
`<flow_id>.json` records in the protocol's `functional_evidence` shape plus `index.json` bound to `HEAD`, refuses
(exit 2, nothing written) a tree whose code differs from `HEAD` and an unavailable sandbox, and exits 1 after
writing a `failed` record for a failing scenario. `--degraded-diagnostic` writes reports that are not evidence.
`tests/test_run_scenarios.py` runs a clone's copy of the runner against the selftest scenarios and feeds each
record through the real `ingest_functional_evidence` (stored `head` and recomputed identity asserted separately);
`tests/test_scenarios.py` generates one `TestCase` class per catalogue scenario (none until CP3) and checks the
planner inventory. The registry function is now `catalogued()`, because `catalogue` is the submodule that
registers the scenarios.

Checkpoint **CP3** (scenarios 1-5) is complete. `tests/scenarios/lifecycle.py` registers ten flows over the existing
`Lifecycle` and `_E2ECase` models, run inside the sandbox with the scratch `bin/` fakes: `full-lifecycle-to-merge`,
`human-gates-stop-and-resume` (plan approval, technical approval and the acceptance track, each stopping with exit
10 and zero workers, then resuming after the simulated user act), `worker-failure-fails-closed` (non-zero exit,
timeout, unverified effect: exit 30, a `FAILED` record, no phase advance, one worker per `run`), `trunk-drift` and
the six `kill-and-resume-<point>` variants. `tests/test_scenarios_negative.py` breaks each claim and requires an
assertion failure. Measured oracles for scenario 4: `binding-*` and `run-*` are pre-spawn (no worker, no job record,
lock free, `resume` exits 0 and changes nothing); `job-after` leaves a `LAUNCHED` record and the killed Controller's
stdin anchor holding the lock until `resume` reconciles it to `FINISHED`; `job-before` deviates from the approved plan's
oracle (no amendment was made): `resume` exits 45 until the scenario ends the anchor, then reconciles. Every variant's continuation `run` reaches
the bound step's end state (exit 10, `BRANCH_BOUND`, each worker command run exactly once). Two kit fixes came out
of this: `tempfile.tempdir` was cached before `TMPDIR` pointed at the scratch tree (case directories landed in the
real `/tmp`), and a per-shard `TMPDIR` that lacks `workflow-controller-tests` made the plan cover the temporary
directory read-only.

Checkpoint **CP4** (scenarios 6-10) is complete. `tests/scenarios/features.py` registers
`squash-merge-and-release-classification` (the squash lifecycle over a policy with the release enabled and a published
`v1.0.0`: a `docs:` commit classifies `NO_CHANGE`, the `feat:` squash `RELEASE_DUE` 1.1.0),
`notifications-never-steer` (the fake SignalHub up, answering 500, slow and down: identical exit codes and normalised
job records, the up case's ordered events with distinct `Idempotency-Key`s, the key in no file or output),
`usage-pause-and-resume` (admission only: a timed hold and a run-cap hold exit 17 with no worker and no job record and
no sleep; after the injected clock passes the reset a second `run` launches all six workers), `read-only-commands-
change-nothing` (`status`, `explain`, `inspect`, `follow` and `status --json` before each of the eleven steps of the
whole lifecycle; only `identity.json` may be added under the runtime root) and `settings-file-effect` (observation
writes nothing; `settings clean`, `step` and a malformed file each with an exact footprint). Measured while writing
them: `status` takes no repository, `follow` writes nothing at all (not even `identity.json`), and the settings lock
file persists, so only a first use shows it as added. Each scenario has a mutation check in
`tests/test_scenarios_negative.py`, and the timings are in `tools/test_timings.json`. 

Checkpoint **CP5** (documentation and the full run) is complete. `docs/guide/development.md` has a "Lifecycle
scenarios" section (what a scenario is, running the catalogue, the evidence record, the sandbox and the enforced-versus-
detected split, what a `passed` record proves and the uncovered mid-job usage path, adding a scenario),
`docs/guide/automation.md` a short paragraph, and `docs/ROADMAP.md` marks step C6 complete. A whole-catalogue
determinism test (`CatalogueDeterminismTest`) runs the complete catalogue twice and compares every normalised summary.
`controller/` is byte-unchanged and no release is cut. Verification results are in the checkpoint's final report and
in the implementation bundle.

Checkpoint **CP6** (run the scenarios fully protected on CI) is complete (amendment, plan revision 10). Measured on CI:
`ubuntu-latest` sets `kernel.apparmor_restrict_unprivileged_userns=1`, so the sandbox probe failed and every scenario
atom failed red. The `tests` job in `tools/ci_workflows.py` gains a step, before the shard, that runs
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` behind an existence guard; `validate.yml` is regenerated,
`tests/test_ci_workflows.py` pins the step, and `docs/guide/development.md` says why. The kit fixes found while
diagnosing it are committed with their tests (`955cb6d`): the worker's result pipe is read while the worker runs (a
failure message over 64 KiB used to hang for 600 s), the degraded leak scan no longer flags its own scratch, and the
scratch tmpfs is mounted after the read-only binds. Two complete `python3 tools/run_scenarios.py run` catalogue runs on
that `HEAD` (namespace mode, 15 scenarios, about 37 s each against the 120 s budget) differ only in `ran_at`. The full
suite passes except the two known Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`), which
fail locally since Git 2.56 and are unrelated. That the pull request's CI shards run the atoms (not skipped) is the remaining evidence, seen
when the branch is pushed.

Self-review: on the pull request's CI run for `e43a4e2`, the sysctl step succeeded and shards 0 and 2 (which run scenario
atoms) passed, but shards 1 and 3 failed. Shard 1's cause is reproduced in a shallow, detached checkout of the merge commit
(as `actions/checkout` makes): `IngestionTest` computed the Workflow identity from the work item's base commit, which a
depth-1 checkout lacks. The runner tests' clone now measures from the checkout's own `HEAD` when that base is missing.
Shard 3 passes in the same reproduction; its CI log is needed.

CI run 38286662623 for `fd3c3fe`: the shallow-checkout failure is gone and the scenario atoms run, but one test
failed: `SandboxEnforcementTest`'s `selftest-env` observed only `claude` resolving. The scenario asked
`sh -c "command -v claude gh workflow-manager curl"`; on the runner `/bin/sh` is dash, whose `command -v`
describes only its first operand (POSIX defines one), while here `/bin/sh` is bash, which describes every name.
The scenario now runs one `command -v` per name. No other multi-operand `command -v` or bash-only `sh` usage is in
the scenario kit or its tests.

Self-review of the milestone diff against plan revision 10 found three important defects, now fixed:
- D5's post-spawn handshake: the bootstrap's kill point wrote `<point>.reached` even when the worker's readiness
  file never appeared, so the Controller was killed in a state the oracle does not describe. It now writes
  `<point>.not-ready` instead and the scenario fails naming the readiness file (new kit test).
- D5.4/R7: the `run-true` mutation of the four pre-spawn kill-and-resume variants ended in an assertion that
  could never pass, so those mutation checks passed whatever the end-state comparison did. The stand-in result
  (`true`: exit 0, nothing changed) now goes through the same end-state comparison, which is what fails.
- D4: only the three selftest records went through the Workflow's real `ingest_functional_evidence`; the
  catalogue determinism test now ingests every catalogue record of its first complete run as well.
Also: the worker's fd 3/4 clean-up no longer shares one `suppress` with the status write, and a dead line in a
kit test is gone. Not applied (optional): extra pipe copies in `pass_fds`, unlisted real locations whose
parent is a system directory, two small error-path leaks, stale records left in a reused `--output`, and the
Git-metadata test's loose `update-ref` check.

## Functional review checklist

Implementation revision 8, technical approval by policy (`90706ee`), implementation head `2597dee`. This milestone
adds test-side code only (`tests/scenarios/`, `tools/run_scenarios.py`, docs, one CI step); `controller/` is
byte-unchanged and there is no release. Expected results were measured on this head.

**Setup.** A clean checkout of the branch (`git status` empty). Linux with unprivileged user namespaces (`unshare -Urn true`
exits 0). No data to seed and no feature flags: scenarios build their own disposable repositories, a scratch origin
and fake `claude`, `gh`, `workflow-manager` and SignalHub. Use a fresh output directory: `OUT=$(mktemp -d)/ev`.

**Flows.**

1. *Catalogue lists.* `python3 tools/run_scenarios.py list`. Expect 15 flow ids, `scenario-full-lifecycle-to-merge` first and
   `scenario-settings-file-effect` last (six `scenario-kill-and-resume-<point>` among them: binding-before/after,
   job-before/after, run-before/after).
2. *Full run.* `python3 tools/run_scenarios.py run --output $OUT`. Expect `ok` for all 15, `total` about 37 s against the
   120 s budget, exit 0.
3. *Evidence is well formed.* `ls $OUT` shows 15 `<flow_id>.json` plus `index.json`. Each record has `flow_id`, `status`
   (`passed`), `head` (= `git rev-parse HEAD`), `ran_at`, `summary`, `log_digest`, `run_ref`, `reporter`.
   `python3 tools/run_scenarios.py check --output $OUT` prints `ok`.
4. *Determinism.* Run step 2 again into a second directory. `diff -r` of the two differs only in `ran_at` (and `index.json`
   timestamps).
5. *Single scenario and failure exit.* `run scenario-trunk-drift --output $OUT2` runs only that flow (exit 0). An unknown
   id is refused with a named error and nothing is written.
6. *Refusal on a dirty tree.* Append a blank line to `tools/run_scenarios.py`, run `run --output $OUT3`. Expect exit 2, a
   message that the code differs from `HEAD`, and `$OUT3` empty or absent. Revert the edit (`git checkout tools/run_scenarios.py`).
7. *Sandbox fail-closed.* If you can, run with user namespaces disabled (or inspect `tests/scenarios/sandbox_setup.py`): without the
   sandbox `run` exits 2 and writes nothing; `--degraded-diagnostic` writes reports that are not evidence.
8. *Nothing real is touched.* After steps 2 and 4, `git status` is clean, `~/.config/workflow-controller` and the real
   runtime root show no new files, and no stray directories appeared in `/tmp` beyond your own `mktemp`.
9. *Mutation checks.* `python3 -m unittest tests.test_scenarios_negative` passes: every scenario claim, once broken, makes
   the scenario fail.
10. *Documentation.* `docs/guide/development.md` "Lifecycle scenarios" matches what you saw in steps 1-7;
    `docs/ROADMAP.md` marks C6 complete; `python3 tools/check_docs.py` passes.
11. *CI.* `tools/ci_workflows.py` has the `apparmor_restrict_unprivileged_userns=0` step before the shard in the `tests`
    job, `validate.yml` is regenerated from it, and the pull request's shards run the scenario atoms (not skipped).

**Known limitations / out of scope.** Mid-job usage-limit handling is not covered by a scenario (admission only). The
two Git 2.56 trailer tests (`test_release_notes`, `test_pull_request_lifecycle`) fail locally and are unrelated. `job-before`
deviates from the approved plan's oracle (`resume` exits 45 until the scenario ends the stdin anchor; no amendment was
made). Optional self-review items were not applied (extra pipe copies in `pass_fds`, unlisted real locations under a
system directory, two small error-path leaks, stale records in a reused `--output`, the loose `update-ref` check).
No release is cut; the pull request is not pushed from this session.
