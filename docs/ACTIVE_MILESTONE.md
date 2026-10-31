# Active Milestone

## Status

**Complete.** `workflow-controller-codex-tester-routing` (C10g-3c1, issue
[#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223)) is implemented (CP1 to CP3), awaiting review and
acceptance. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_ROUTING_PLAN.md`. Routing accepts `codex` for the
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

## Next action

Review and acceptance of C10g-3c1, then `/milestone-plan` for C10g-3c2 (the Codex tester's fallback selection, telemetry dimensions and the operator-run Codex round).

## Functional review checklist

Item `workflow-controller-codex-tester-routing` (C10g-3c1, #223), implementation revision 1. Every flow is offline and
runs against fakes and a scratch origin; nothing needs the network, a real `codex`, `claude`, `gh` or `ssh`.

**Setup.** Work in the repository checkout. Set `TMPDIR` to a writable scratch folder (the tests create temporary
folders; the product clone is read-only to the tester). Do not set `PYTHONPATH=.`. Run each test module on its own, not
the whole suite (`test_functional_sandbox` has a known CI flake, #206). Do not write inside the clone (no `build`
folder is recreated).

**Test data.** None to seed; the tests build temporary repositories, a scratch bare origin, a fake Codex launcher and a
recording usage gate.

**Flows and expected results.**

1. `python3 -B -m unittest tests.test_routing tests.test_routing_chain tests.test_harness_table` -- expected `OK` (59, 23
   and 19 tests): `functional-tester` with `codex` is accepted by a role entry, the default, `--role-harness` and a
   fallback entry; `review-plan` and worker roles still refuse `codex`; a harness-only Codex tester names the model
   `claude-sonnet-5-5` in its `RoutingConfigError`, a model-only one names the effort `medium`, harness plus
   `gpt-6.1-sol` plus `high` resolves; a Claude-only configuration resolves as before; `routing.NOT_YET_ROUTABLE` is gone;
   the table's Codex tester cell says yes.
2. `python3 -B -m unittest tests.test_functional_codex_route` -- expected `OK` (9 tests): for a Codex route the usage
   gate is asked for the Codex provider, `functional.harness` is `codex`, `captured_digest` is the Codex `auth.json`
   digest, the launch gets the route's model, effort and the `codex` binary; a launch `functional_tester_unavailable`
   ends at that gate with `not_started`; options lacking the Codex entry write exactly one `GATE_BLOCKED` record with
   cause `no_binary`, no usage gate call and no tester record; `_reconcile_functional` copies back the Codex credential
   (and leaves `credentials` `None` with no `auth.json`).
3. `python3 -B -m unittest tests.test_functional_lifecycle tests.test_functional_review` -- expected `OK` (4 and 57
   tests; the lifecycle module takes about four minutes): the Claude path is unchanged, its launch call keeps today's
   keyword arguments, and the checklist-writer note states the limits both testers share.
4. `python3 -B -m unittest tests.test_settings tests.test_review_mode tests.test_codex_tester_launch tests.test_docs` --
   expected `OK` (98, 42, 22 and 59 tests): settings validators accept the Codex tester; a Codex tester changes neither
   `worker_families` nor the reviewer choice (a Codex reviewer plus Codex tester is accepted); the launch tests pass
   without the removed `UnchangedTest`.
5. `python3 -B -m unittest tests.test_cli` -- expected `OK`: `_functional_options` carries both harness homes and
   binaries (`claude-code` and `codex`; 156 tests), and no setting, option or exit code was added.
6. `python3 -B tools/harness_table.py --check` and `python3 -B tools/check_docs.py` -- expected exit 0 each.
7. `grep -rn NOT_YET_ROUTABLE controller docs/guide` -- expected no output; `grep -rln launch_codex_functional controller`
   -- expected `controller/codex_tester.py` and `controller/job.py`.
8. Read `docs/guide/harnesses.md`, `commands.md`, `workers.md`, `runtime.md`, `automation.md` and
   `docs/compatibility.md`: they say how to route the tester to Codex (harness, `gpt-6.1-sol` and `high` or `xhigh`, all
   three), that `verify-codex` must have recorded the build first, and no longer say routing refuses the Codex tester or
   "until C10g-3c". `docs/ROADMAP.md` marks C10g-3c1 complete as a `feat:` and keeps C10g-3c2 as the next row.

**Known limitations / out of scope.** No live flow: the one operator-run Codex functional round
(`CONTROLLER_LIVE_CODEX_TESTS=1`, skipped here) is C10g-3c2, as are fallback-chain selection before the spawn, the
telemetry dimensions and the Codex usage figures (a Codex tester's usage record is empty, D7). Selection still takes the
first chain entry. Two Git 2.56 trailer tests fail locally for a known, unrelated reason. No new setting, option, exit
code or gate; the PR is a `feat:`, a minor release.
