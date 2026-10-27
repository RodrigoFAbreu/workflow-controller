# Active Milestone

## Status

**Implemented, awaiting review.** `workflow-controller-harness-telemetry` (H11, issue
[#207](https://github.com/RodrigoFAbreu/workflow-controller/issues/207)): `telemetry --by harness`, `role,harness` and
`role,harness,model` group the jobs by the harness they ran on, every row carries a `harness` key, and a courier review
record reads its route's harness (so a Codex review group shows its jobs with no figures). CP1 (the groupings, the row
key, the courier correction) and CP2 (the command and worker guides, the roadmap row) are complete. The change is `feat:`.
The text below is the previous milestone's record until acceptance replaces it.

**Complete.** `workflow-controller-fallback-select` (H9b, issue
[#202](https://github.com/RodrigoFAbreu/workflow-controller/issues/202)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): a fallback chain's later entry is now used when the first cannot launch or its provider is held
by a limit. `controller/route_select.py` walks the chain before a worker is spawned (never mid-job); a hold skips every
later entry of that provider; when none can launch the earliest waitable hold or the first entry's error is returned as
today. `worker_route.selection` and the telemetry dimensions `chain_position` and `chain_skipped` record the choice. With
the shipped harnesses only `claude-code` takes the worker role, so a chain cannot yet change the route (H4). The change is
`feat:`. The plan is archived at `docs/milestones/completed/workflow-controller-fallback-select.md`.

## Next action

`workflow-controller-harness-telemetry` (H11) is in implementation review. After it, the next item of the harness-neutral
roles epic in `docs/ROADMAP.md` is the Codex tester (C10g-1 to C10g-3).

## Functional review checklist

Work item `workflow-controller-harness-telemetry` (H11), implementation revision 1. Every flow is offline and unit level:
no `claude`, `codex`, `ssh`, network or real `gh` is needed, and nothing is written inside the clone.

**Setup.** Run from the clone root with the system `python3`. Do not set `PYTHONPATH`. Point `TMPDIR` at a scratch folder.

**Test data.** None to seed. The tests build their own job records (worker, courier review, functional review, recorded
telemetry block) in a scratch runtime folder.

| # | Flow | Command | Expected |
| --- | --- | --- | --- |
| 1 | The groupings and the row key | `python3 -m unittest tests.test_telemetry_harness -v` | All pass: a worker row reads its route's harness, or the worker adapter's when the record has no route; a functional review row reads `claude-code`; a courier review row reads its route's harness (`codex`); a recorded block that names a harness wins; `--by harness` counts Codex reviews as `telemetry unavailable`; `role,harness` and `role,harness,model` group correctly; text and `--json` output carry the harness; `--help` lists the groupings and an unknown one is a usage error (exit 2). |
| 2 | The courier correction | `python3 -m unittest tests.test_route_select_job -v` | All pass, including `test_a_courier_review_record_reads_its_routes_harness` (the former hard-coded `claude-code` case now expects `codex`). |
| 3 | Row keys and existing telemetry | `python3 -m unittest tests.test_telemetry` | All pass; the job row's new keys now include `harness`. |
| 4 | Nothing else moved | `python3 -m unittest tests.test_routing_chain tests.test_usage_gate tests.test_worker_launch tests.test_job tests.test_harness` | All pass. |
| 5 | The command help | `python3 -m controller telemetry --help` (runtime folder under the scratch `TMPDIR` if prompted) | The `--by` help lists `harness`, `role,harness` and `role,harness,model` beside the older groupings. |
| 6 | Docs | `python3 tools/check_docs.py`, then read the `--by` bullet of "telemetry" in `docs/guide/commands.md`, the dimensions paragraph of "Telemetry" in `docs/guide/workers.md` and the H11 row of `docs/ROADMAP.md` | The check passes; the pages agree with each other and with the behaviour above (a worker's harness is its route's, a review's the reviewer that ran it, a record without a route reads `claude-code`, a Codex group has no figures). |

**Known limitations, out of scope.** With the harness registry as shipped only `claude-code` takes the worker role, so
real worker groups show one harness; a `codex` group appears only for external reviews, which carry no figures. The Codex
tester (C10g-1 to C10g-3) and the Codex worker (H4a to H4g) are separate items.
