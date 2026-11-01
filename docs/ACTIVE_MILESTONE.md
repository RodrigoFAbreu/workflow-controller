# Active Milestone

## Status

**In progress.** `workflow-controller-codex-worker-launch` (H4b2, issue
[#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255)), plan
`docs/ai-workflow/CONTROLLER_CODEX_WORKER_LAUNCH_PLAN.md` (revision 4). CP1 is complete: `controller/codex_worker_sandbox.py`
also has the worker's composed-configuration check (`compose_argv`, `check_compose_argv`, `effective_policy`,
`check_effective`, `check_composed`) and the nested-repository refusal (`nested_git_entries`, `check_nested`), with
tests in `tests/test_codex_worker_sandbox.py`; the tester's modules are unchanged. Verified: `tests.test_codex_worker_sandbox`
(87), `tests.test_codex_sandbox`, `tests.test_write_containment` and `tests.test_package_structure` pass. CP2 is complete:
`controller/codex_worker.py` has `launch_codex_worker` (the order of D4), `classify`, the settling of D6 and
`CodexLauncher`; it is registered in `controller/__init__.py` and the dependency order, with a leftover-inventory row for
`tests/test_codex_worker.py` (35 tests, a fake Codex, no network). Nothing reaches it: `CODEX.roles` and
`worker_launch.LAUNCHERS` are unchanged. Two seams beyond D7's list, `binary_name` and `system_dir`, let a test point
the launch at the fake; `tests/test_command_files.py`'s wiring guard now names `codex_worker` as the one module that
imports `command_files`, which D4(a) requires (the plan listed that test as unchanged). Verified:
`tests.test_codex_worker`, `tests.test_codex_worker_sandbox`, `tests.test_package_structure`, `tests.test_codex_sandbox`,
`tests.test_codex_tester`, `tests.test_worker_launch`, `tests.test_harness`, `tests.test_command_files`,
`tests.test_write_containment` and `tests.test_fake_claude_contract` pass. CP3 is complete: `tests/test_codex_worker_live.py` (opt-in, skipped by default and on CI) runs one real launch; it was not
run in this session (it costs a model turn), so `tests/harness_contract/codex/worker_launch.txt` says *not yet measured* until
the operator runs it. The harness and worker guides, the development page and the roadmap state the launcher in place. The
self-review fixed one defect: the settling read `sys.exc_info()` to decide whether an exception was in flight, which also
reports an exception the launch's own caller is handling, so a failed credential-copy removal on a clean run was swallowed
when the launch was called from an `except` block; the launch now passes `in_flight` explicitly, and
`SettlingTest.test_an_exception_the_caller_is_handling_is_not_taken_for_one_in_flight` covers it. The full suite then
showed a latent cross-shard collision in the new tests: the classes of `tests/test_codex_worker.py` run in parallel shards
and three of them hang a fake under the same `...:job1` tag, so one deadline kill could end another shard's fake (shown by
staggering two of them by a second); each test now has its own inherited tag prefix and `tagged` matches the exact tag. The
same collision in the unchanged `tests/test_codex_tester.py` (every bridge's tag is `tester`; `OutcomeTest` and `SettleTest`
fail together when run concurrently) is pre-existing and outside this item (D1 leaves those tests unedited); it is reported
for a follow-up issue. It also mended a broken sentence in the workers guide. Next: the implementation review, then H4c (the job record, usage and reaping) once this item
is accepted.

**Complete.** `workflow-controller-codex-worker-sandbox` (H4b1, issue
[#252](https://github.com/RodrigoFAbreu/workflow-controller/issues/252)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_WORKER_SANDBOX_PLAN.md`. CP1 is complete: `controller/codex_worker_sandbox.py` has
`WorkerLayout`, `check_worktree`, the `worker` profile (worktree and scratch writable, `.git/hooks` and `.git/config`
read, network off), the pinned `codex exec` line, `check_home`, `check_launch` and `build`, with
`tests/test_codex_worker_sandbox.py`. CP2 is complete: the module also has the worker probes (`probe_command`, `probe_prompt`,
`judge`, `sandbox_probe_argv`) and the git guard (`guard_digest`, `check_guard`), which detects any change under `.git`
other than what a commit or a plain `git gc` writes, and refuses a symlink, a special file or an alternates file there.
CP3 is complete: `tests/test_codex_worker_sandbox_live.py` (opt-in, skipped by default and on CI) ran the identical probe
command under `codex sandbox -P worker` and one `codex exec` turn on `codex-cli 0.163.0`, and both passed
(`tests/harness_contract/codex/worker_sandbox.txt`). The live run found a defect in CP1's profile: Codex keeps `.git`
read-only inside a writable root unless it is listed, so `git commit` failed on `index.lock`; the profile now lists the
worktree's `.git` as `write`, and the nested read-only `.git/hooks` and `.git/config` entries then held (a write to either
was refused). The harness and worker guides and the roadmap (H4b split into H4b1, done, and H4b2) state it in place.
Nothing calls the module; the change is a `chore:` with no release. The self-review's full suite found two hygiene
guards the new tests broke: `tests/test_codex_worker_sandbox.py` now creates its repositories through
`fixtures.git_init`/`git_clone`, and `tests/process_fixtures.py`'s `LEFTOVER_INVENTORY` has a row for each new test file.

Next action: `/milestone-plan` for H4b2 ([#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255), the Codex
worker launcher, which also closes the composed-configuration and nested-repository limits). The previous item is recorded below.

**Complete.** `workflow-controller-orchestrator-stops` (NF3, issue
[#244](https://github.com/RodrigoFAbreu/workflow-controller/issues/244)) is implemented (CP1 and CP2 and the functional review) and accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_ORCHESTRATOR_STOPS_PLAN.md`. A gate record
carries `protocol_gate` with `row`, `disposition`, `action_id`, `reason_code` and the Controller's own `code`, and
`derive_job_event` announces a stop the orchestrator handles as `orchestrator_wait` (a wait) or `lane_blocked` (a repair)
instead of `user_gate`; every other, unknown or legacy stop stays `user_gate`. The automation and commands guides, ADR 0012
and the roadmap state the rule in place. The self-review changed one test: the scenario
`scenario-codex-review-implementation-stage-limit` stops at a `gate_evidence_unmet` technical approval gate, so it now
expects `lane_blocked` there, not `user_gate` (D2). The change is a `fix:`, a patch release.

Next action: `/milestone-plan` for H4b ([#252](https://github.com/RodrigoFAbreu/workflow-controller/issues/252), the Codex worker launch), the next incomplete item on `docs/ROADMAP.md`. The previous item is recorded below.

**Complete.** `workflow-controller-user-only-scan` (TK1, issue
[#247](https://github.com/RodrigoFAbreu/workflow-controller/issues/247)) is implemented (CP1 and CP2). The plan is
`docs/ai-workflow/CONTROLLER_USER_ONLY_SCAN_PLAN.md`. `worker._task_tokens` splits a task on `=` and on every character a
name or path cannot contain, so the Claude launch scan and the Codex renderer find a user-only command name wrapped in `!`,
`?`, brackets, quotes or markup, or behind `=`; a name inside a longer word or path is still not refused. The workers guide
and the roadmap state the rule in place. The change is a `fix:`, a patch release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-command-render` (H4a, issue
[#242](https://github.com/RodrigoFAbreu/workflow-controller/issues/242)) is implemented (CP1 and CP2 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_COMMAND_RENDER_PLAN.md`. `controller/command_files.py` adds
`render_command`, a pure function that renders a shared command file and an argument string into a Codex prompt and
refuses the user-only commands (`tests/test_command_files.py`). Nothing calls it. The harness and worker guides and the roadmap state it in place; the change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-fallback` (C10g-3c2, issue
[#236](https://github.com/RodrigoFAbreu/workflow-controller/issues/236)) is implemented (CP1 to CP3 and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_FALLBACK_PLAN.md`. A `functional-tester` chain longer than one is walked before the
spawn (`codex_tester.selection_error`, `job._tester_probe`), a Codex tester has a `usage_record` from its rollouts and a
`telemetry` block, and `tests/test_codex_tester_live.py` has a second opt-in test through `_functional_launch`. The first
real round (31 October 2026: `gpt-6.1-sol`, effort `high`, `job.execute_step` on a scratch item at row 38e) ran to
`findings` with a correct report, credentials `unchanged`, the sandbox removed, 12,594 input, 46,080 cache-read, 1,061
output and 93 reasoning tokens in `usage_record` and a `telemetry` block with harness `codex`; no defect was found. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-routing` (C10g-3c1, issue
[#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223)) is implemented (CP1 to CP3 and the functional review) and
accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_ROUTING_PLAN.md`. Routing accepts `codex` for the
`functional-tester` role (the model `gpt-6.1-sol` and an effort `high` or `xhigh` named, no Codex defaults), and
`job._run_functional` runs the launch, usage provider, credential and record fields of the route's harness;
`cli._functional_options` carries both harness homes and binaries. The guides, the table and the roadmap state it in place. The
change is a `feat:`, a minor release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-surroundings` (C10g-3b2b, issue
[#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)) is implemented (CP1 to CP3 and the functional
review) and accepted by policy. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_SURROUNDINGS_PLAN.md`. Codex declares the
tester role with `verify-codex`, routing refuses the pair until C10g-3c, and `codex_tester.launch_codex_functional` adds the
build gate, the before/after snapshots, the binding check and the refusal mapping, with `tests/test_codex_tester_launch.py`;
the guides, the generated table and the roadmap state the new facts in place. Nothing calls the launch until C10g-3c; the
change is a `chore:` with no release. The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3, the self-review fixes and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Functional review checklist

Item `workflow-controller-codex-worker-sandbox` (H4b1, issue [#252](https://github.com/RodrigoFAbreu/workflow-controller/issues/252)),
implementation revision 1. Every flow is offline and unit-level (no real Claude, Codex, network or `gh`) except flow 8, which
is the opt-in live proof and is operator-run. Run from the product clone with `TMPDIR` set to a writable scratch directory,
one module at a time, `python3 -B` and no `PYTHONPATH=.`. The tester's sandbox has a read-only `/tmp` and a restricted
`PATH`: a flow that creates folders there or runs git (flows 1, 3) is `environment`-limited and operator-run.

| # | Flow | Command | Expected |
|---|---|---|---|
| 1 | Layout, profile, command line, build, probes, git guard (R1, R2) | `python3 -B -m unittest tests.test_codex_worker_sandbox` | Passes: the `worker` profile makes the worktree and scratch writable (the worktree's `.git` listed `write`, `.git/hooks` and `.git/config` read) with network off; the pinned `codex exec` line; `build` starts no process; the judge fails closed; the guard digest ignores what a commit or a plain `git gc` writes and detects any other `.git` change (new `commondir`, symlink, special file, `alternates`). |
| 2 | Module registered, nothing wired (R4) | `python3 -B -m unittest tests.test_package_structure tests.test_write_containment` | Passes: the module is in the package list; the write-containment scan accepts it. |
| 3 | Neighbouring modules unchanged (R1, R4) | `python3 -B -m unittest tests.test_codex_sandbox tests.test_codex_tester tests.test_worker_launch tests.test_harness` | Passes: the tester sandbox, the Claude launch and the harness declarations are unchanged; the Codex worker role is still undeclared. |
| 4 | Test-hygiene inventory | `python3 -B -m unittest tests.test_fake_claude_contract` | Passes: `LEFTOVER_INVENTORY` has a row for each new test file. |
| 5 | Live test skipped by default (R3) | `python3 -B -m unittest tests.test_codex_worker_sandbox_live -v` | Every test is reported `skipped` (opt-in variable unset); no process is started. |
| 6 | Documents (R3, R4) | `python3 -B tools/check_docs.py` | Exits 0; `docs/guide/harnesses.md` and `workers.md` state the profile, the measured boundary, the open composed-configuration limit and the H4b1/H4b2 split; the roadmap row is split. |
| 7 | Generated harness table | `python3 -B tools/harness_table.py --check` | Exits 0 (no drift; the worker sandbox adds no declaration). |
| 8 | Live proof (R3), operator-run | `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_worker_sandbox_live -v` | Needs real `codex` (0.163.0), `bwrap` and a recorded `verify-codex` build. Both the `codex sandbox -P worker` probe and the one `codex exec` turn pass; the output matches `tests/harness_contract/codex/worker_sandbox.txt` (edit, create, rename, delete and `git commit` inside the worktree succeed; writes to `.git/hooks` and `.git/config`, home, credential, canary, push, `gh`, network and sockets are refused). |

**Known limitations / out of scope.** Nothing calls the module: no launcher (H4b2,
[#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255)), no Codex worker role, setting, option, exit code or
gate. The composed-configuration check and the nested-repository limit for the worker profile are open and belong to H4b2.
The change is a `chore:` with no release. The full suite is not a flow (`test_functional_sandbox` has a known CI flake, #206).

## Next action

Manual functional review of H4b1 (the checklist above), then `/accept-milestone`; after it, `/milestone-plan` for H4b2 ([#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255), the Codex worker launcher).
