# Active Milestone

## Status

**In progress.** `workflow-controller-automated-functional-review` (C10c, `docs/ROADMAP.md`) makes the Controller run
the functional review itself at row 38e: an isolated sandbox, an independent tester, one `functional_evidence` result
per checklist flow, and findings for the applier when a flow fails. Plan revision 8 was approved by policy at
`2480ee9`; the base commit is `1599a4d`. The plan is
`docs/ai-workflow/CONTROLLER_AUTOMATED_FUNCTIONAL_REVIEW_PLAN.md`. `docs/ai-workflow/WORKFLOW_STATE.json` is the
ground-truth record of the phase and of each checkpoint.

| Checkpoint | State |
| --- | --- |
| CP1 The role, the settings and the pure parts | complete: the `functional-tester` role (Sonnet 5.5, medium, single-agent), the rows `functional.automated` and `functional.timeout_seconds` (table generation 13), and `controller/functional_review.py` (checklist reader and the four flow shapes, `RESULTS.json` validator, "not run" entries, evidence input, findings text, writable-location derivation, prompt). Checked with `tests.test_functional_review`, `tests.test_routing`, `tests.test_settings`, `tests.test_trunk_preflight`, `tests.test_package_structure` and `tools/check_docs.py`; the no-policy golden was regenerated for the two rows. The whole suite (`tools/run_tests.py`, 4414 tests) fails only the two Git 2.56 trailer tests that fail on `main` too. |
| CP2 The enforcement spike on the pinned Claude CLI | complete: `controller/functional_review.py` gained `tester_settings` (required sandbox, no unsandboxed retry, no excluded command, no domain or Unix socket, write allowlist of output, scratch and derived writable locations, deny list of the clone root and `.git`), `tester_flags` (`Bash` only), `static_probe`, `launch_refusal`, and the probe command and judge. `tests/test_functional_enforcement.py` ran on the pinned CLI (Claude Code 2.1.303, bubblewrap and socat present, none skipped): writes to a tracked file, the checklist, `.git`, a new file beside one, a rename over and a deletion of a tracked file, a stand-in home, `/tmp` and `/var/tmp` refused; a TCP connection and a host Unix-socket connection blocked (the host listeners saw nothing); the writable location below the denied clone root and the output folder writable; tracked bytes unchanged at every poll; only `Bash` in the tool set; no unsandboxed retry or file-tool write reached the host; a launch without `bwrap` and `socat` refused by the CLI before any model call (`functional_tester_unavailable`). `/dev/shm` is a private mount inside the sandbox (the write succeeds there and never reaches the host). Finding: the precedence of D5 holds only when the run's working directory is the output folder; from a directory above the clone the CLI's working-directory grant outranks the allow (pinned by a test, and stated in `tester_settings`). The boundary can be enforced, so the plan needs no amendment. |
| CP3 The sandbox and the tester run | complete: `controller/functional_review.py` gained the candidate (`capture_candidate`: manifest of every tracked path from the real repository's tree, checklist blob compared with `rev-parse`), the sandbox (`build_sandbox`: `--no-hardlinks` clone with no autocrlf, scratch bare origin the branch is pushed to, four launcher scripts the Controller writes with mode 0755, scratch directories, derived writable locations, run settings, fresh-clone check against the manifest), the post-run binding (`check_bound`: `lstat`/no-follow Python walk, ignore rules asked of Git on a Controller-owned git dir, `.git` change rules, HEAD and refs as plain files, no Git in the clone) and the fakes' log reader; `controller/sandbox_fakes/` holds the four refuse-and-log stand-ins (`pyproject.toml` lists the subpackage). `controller/review.py` gained the `functional` run kind (`functional_argv`/`check_functional_argv`, `check_functional_settings`, `launch_functional`, `settle_functional`; the Claude attempt was split so both kinds share it; `JobLayout` takes an `area`); `harness.py` declares the `functional-tester` role for Claude Code. Tests: `tests/test_functional_sandbox.py` (57, on `tests/fake_functional_tester.py`, which emulates the CLI's sandbox from the settings file and can switch the emulation off to show each detection layer alone), the packaging checks in `tests/test_package_structure.py` and `tests/test_packaged_runtime.py` (wheel holds the stand-ins; launchers run from a copy of the wheel's contents). One live run of `launch_functional` on the real CLI (Claude Code 2.1.303, Sonnet 5.5, low effort, a two-flow checklist) ended `ran` with no breach and a bound clone, recorded the fake `gh` call and wrote a valid `RESULTS.json` (flow 2 failed as `environment`). Whole suite (`tools/run_tests.py`, 4495 tests): only the two Git 2.56 trailer tests fail, as on `main`. The launch probe at every start is the static probe plus the CLI's own `failIfUnavailable` refusal; the full model probe stays CP2's test. |
| CP4 The step at row 38e | complete: `job.py` gained `_run_functional` (the step), `_functional_plan` (the stops that come before a run, shared with `explain`), `functional_forecast`, the tester job record (kind `functional_test`, schema 2, a `functional` block; `validate_record`, `resume`, `--abandon` and `never_launched` know it) and `execute_step(functional=...)`; `protocol.py` takes the `functional_evidence` kind (`ExternalResult.flow_id`, `.identity`); `protocol_decision.py` gives the row-37 launch the sandbox-limits note; `review_rounds.py` counts tester runs that ended `findings` (`functional_rounds`) and finds the last run; `cli.py` passes `functional.*` to every step and `explain` shows the forecast; `functional_review.py` holds the stop texts. `unchanged` and the notification of a blocker are the record's `functional.gate`. Checked with `tests.test_protocol_job` (each outcome and stop of D7 on real row 38e answers of the vendored 2.12.0, a fake tester standing in for the sandbox), `tests.test_protocol`, `tests.test_protocol_decision` and `tests.test_cli`. |
| CP5 Lifecycle scenarios and the isolation tests | complete: `tests/test_functional_lifecycle.py` writes each flow once against a `Judge` (`unittest` in the suite, the kit in the catalogue) on a real vendored 2.12.0 Workflow driven through `execute_step`: the pass path to acceptance by policy; a failing flow, the applier's bounded fix (real post-fix regeneration, both review stages and the technical approval again), the retest at the new head and acceptance; the identical-text retest reaching the applier twice (I3); a no-code-change disposition stopping at row 38i; `required_flows` the checklist does not cover giving `functional_unchanged` and one `lane_blocked`; the round bound ending at `functional_rounds_exhausted`; `functional.automated` false; unrunnable flows (`flows_not_runnable`, no findings, no applier); an unreadable checklist (`checklist_unrecognised` before any job or reservation); and, on the real `review.launch_functional` over the fake CLI, an honest run, edit-and-restore of source and checklist refused at the first write, an unbound `TMPDIR` copy recorded with the clone unchanged (the stated limit of D5), a differing clone (`tested_bytes_unbound`, nothing recorded) and a nested session (breach). `tests/scenarios/lifecycle.py` registers four catalogue scenarios over the same bodies (catalogue 20 -> 24, each with a mutation in `tests/test_scenarios_negative.py`); `mktemp` joined the kit's allowed tools for `prepare-ai-review.sh`. The real-launch flow found a defect, fixed in `controller/job.py`: the tester record put the sandbox `Path` into the job record, which is not JSON serialisable (the fake tester returned a string). The no-policy golden needed no regeneration (`--check` passes). |
| CP6 Documentation, roadmap and the whole suite | complete: the step, the sandbox and its stated limits (pull-request flows unsupported, a copy edited under the tester's `TMPDIR` not detected), the two `functional` settings (table generation 13) and the `functional-tester` role are documented in place in `docs/guide/automation.md` ("The automated functional review"), `runtime.md`, `commands.md`, `workers.md`, `troubleshooting.md`, `docs/common-problems.md`, `docs/run.md`, `docs/install.md` (`bubblewrap` and `socat`), `docs/compatibility.md`, the glossary and the README; [ADR 0017](adr/0017-automated-functional-review.md) records D2 and D3; `docs/ROADMAP.md` marks C10c implemented, follow-up 26 done and adds follow-ups 61-66 (Codex tester, `required_flows`, a tester-failure cap setting, a sandbox-side forge model, extra sandbox tools, a newer vendored Workflow). `python3 tools/check_docs.py` passes. |

## Next action

Every checkpoint is complete; implementation revision 8 passed both implementation-review stages and the technical
approval. The manual functional review is the open gate: run the checklist below, then place findings in the
feedback folder's `FUNCTIONAL_REVIEW.md`, or run `/accept-milestone` when testing is clean.

## Functional review checklist

**Setup.** Work in a fresh clone of the milestone branch at the committed head. Install nothing global; run the
Controller as `PYTHONPATH=<clone> python3 -P -m controller`. `bubblewrap` (`bwrap`) and `socat` must be on `PATH`
and a pinned `claude` CLI must be installed. Every flow below runs offline against throwaway repositories under a
`mktemp -d` directory; none needs a network, a real remote or a real `gh`.

**Test data.** A scratch repository managed by the vendored Workflow with a work item at the functional-review gate
and a short committed checklist of two flows, one that passes and one that fails (for example a command that exits
non-zero).

**A. The unit tests for the feature pass.** Run
`python3 -m unittest tests.test_functional_review tests.test_functional_sandbox tests.test_functional_lifecycle tests.test_protocol_job`
from the clone root. Expect `OK` with nothing skipped for the sandbox tests when `bwrap` and `socat` are present.

**B. The checklist is read as flows.** Run `python3 -P -m controller explain` in the scratch repository at the gate.
Expect a forecast that names the tester, the flow ids (`flow-1`, `flow-2`), the checklist blob and the timeout, and
no job or reservation created.

**C. A passing run reaches acceptance.** With a checklist whose flows all pass, run one step at row 38e. Expect one
`functional_evidence` result per flow, the work item accepted by policy, and a tester job record of kind
`functional_test` ending `ran`.

**D. A failing flow produces findings.** With the failing flow, run one step. Expect `FUNCTIONAL_REVIEW.md` in the
feedback folder, headed with the tested head, checklist blob and run, quoting the failing flow's log; the next step
goes to `/apply-functional-review`, not to acceptance.

**E. The sandbox refuses writes.** Read `RESULTS.json` and the fake `gh`/`git push` logs of the run in C or D.
Expect the clone's tracked files, the checklist and `.git` byte-identical to the tested head, and the fake remote
calls recorded, not performed.

**F. Settings switch it off.** Set `functional.automated` to `false` in the settings file and run a step. Expect a
stop for the manual gate with no tester job, sandbox or usage reservation.

**G. No sandbox tools means a clean refusal.** Run a step with `bwrap` hidden from `PATH`. Expect the stop
`functional_tester_unavailable` before any model call, and no job left `LAUNCHED`.

**H. An unreadable checklist stops early.** Replace the checklist section with prose that has no flows. Expect
`checklist_unrecognised` naming the four accepted flow shapes, before any job, sandbox or reservation exists.

**Known limitations.** Pull-request flows are unsupported in the sandbox; a copy edited under the tester's own
`TMPDIR` is not detected (stated limit of D5); a Codex tester, `required_flows` coverage tooling, a tester-failure
cap setting and a sandbox-side forge model are roadmap follow-ups 61-66. Two Git 2.56 trailer tests fail on `main`
too and are out of scope. Out of scope: the real forge, releases and the installed pipx copy.
