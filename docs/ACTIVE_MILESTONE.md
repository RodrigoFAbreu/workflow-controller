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
| CP3 The sandbox and the tester run | pending |
| CP4 The step at row 38e | pending |
| CP5 Lifecycle scenarios and the isolation tests | pending |
| CP6 Documentation, roadmap and the whole suite | pending |

## Next action

Run `/milestone-implement workflow-controller-automated-functional-review` for the next checkpoint (one checkpoint per
invocation).
