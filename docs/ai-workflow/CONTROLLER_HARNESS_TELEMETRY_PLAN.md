# H11: the harness in telemetry (Revision 2)

Work item: `workflow-controller-harness-telemetry`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `d4916eb3f7410f233f497f1548a97a6e80f42e44` ("docs: link the H11 issue from the roadmap row and the next action (#207) (#208)"), the tip of `main`, passed explicitly as `/milestone-plan d4916eb…`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H11, next after H9b (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#207](https://github.com/RodrigoFAbreu/workflow-controller/issues/207).
Released baseline: `workflow-controller 1.23.0`.
Pull request title: `feat: group telemetry by harness (H11, #207)`

## Goal

`telemetry --by` can group by role, model and review mode but not by the harness a job ran on, so cost, turns and outcome cannot be compared per harness. This milestone adds the groupings `harness`, `role,harness` and `role,harness,model`, puts `harness` on every telemetry row, and makes the value the harness that actually ran the job, for a worker job and for a Controller-run external review alike.

## What the harness value is today, per kind of job

`telemetry.dimensions` already derives a `harness` for every job record, and the telemetry block of a finished worker keeps it. No row carries it and no grouping reads it. The values are:

- **A worker record** (`worker_route.harness`, written by H1 and chosen by H9b's selection): that string, so a fallback to a second harness reads as that harness.
- **A worker record with no `worker_route.harness`** (written before H1): the worker adapter's name, `claude-code`, which is what ran it.
- **A functional-review (tester) record** (`kind` `functional`): `job.py` copies the tester session's `telemetry` block to the job's top level, so it is a telemetry row. That block carries no `harness`, so D2 falls to the dimensions, which read `worker_route.harness` (`claude-code`, the only adapter declaring the tester role). No change is needed; a test case pins it.
- **A courier record** (a Controller-run external review, `kind` `review` with `review.action_id`): `dimensions` ignores its route and answers the worker adapter's name. That is wrong for a Codex review, and it is the one gap in what the issue asks for. H9b deliberately left it (its D6) for this milestone. The record already carries the right fact: `job.py` writes a review job's `worker_route` from a `ResolvedRoute` whose `harness` is `adapters.get(harness.name).name`, so `codex` for a Codex review and `claude-code` for a Claude one. Nothing needs adding to the job record.
- **A courier record with no `worker_route.harness`** (written before H1): the worker adapter's name, as today. A Codex review from before H1 therefore still reads `claude-code`; it is the existing fallback the issue tells the plan to keep, the placeholder is not invented, and the guide says so.

## Decisions

- **D1. One mechanism: three groupings over a `harness` row key.** `GROUPINGS` gains `harness`, `role,harness` and `role,harness,model`, in the form of the existing entries (`("harness",)`, `("role", "harness")`, `("role", "harness", "model")`). `rows` adds a `harness` key to each row. The `--by` choices of `controller/cli.py` are built from `GROUPINGS`, so the CLI accepts them with no change to its argument list; only its help string names them. No new setting, option or exit code, and the command stays read-only.
- **D2. The row's harness is read the way its role and model are.** `block.get("harness", route["harness"])` for a worker row, as `role` and `model` do today: the recorded block wins, the dimensions answer when it has none. A courier row reads `dimensions`, which D3 corrects. `dimensions` stays the one place that derives a harness.
- **D3. A courier record's harness is its route's.** `telemetry.dimensions` drops the `not is_courier_job(record)` condition, so every record reads `worker_route.harness` when it is a string and the worker adapter's name otherwise. This is the H9b D6 follow-through the issue names ("the harness for a review job must be the harness that actually ran it"). It changes no stored block: a Claude review's `review.telemetry` is read as it was written, and a Codex review has none. The only value that moves is a Codex courier row's `harness`, and no row carried that key before, so no existing output changes.
- **D4. The "no figures" count is per group and unchanged.** `groups` already counts `telemetry_unavailable` per bucket, so a Codex group (a Codex review has no figures) shows its jobs and `telemetry unavailable` equal to its jobs, with `-` for every figure, and a Claude group beside it totals only the jobs that carry them. A test pins both for a mixed set.
- **D5. Labels and JSON.** The group label prints `harness=claude-code` and so on; a row always has a harness, so `_GROUP_LABEL_MISSING` needs no entry for it. `--json` carries `by`, the new `harness` row key, and the groups' `key` with `harness` in it. A record whose route names a harness outside the registry still groups under that string; the grouping reads recorded facts, never the registry.
- **D6. Existing groupings and their output are unchanged; two existing assertions move with the change.** The text output of the existing groupings stays byte-pinned and `tests.test_telemetry` keeps every assertion except one: `test_text_and_json_of_the_existing_groupings_are_unchanged` (`tests/test_telemetry.py:316-321`) pins the set of new `--json` row keys, which gains `"harness"` (an additive change to that pinned set). The second is in `tests/test_route_select_job.py`: `test_a_courier_review_record_keeps_the_hard_coded_harness` (lines 168-171) asserts the exception D3 removes, so it is inverted and renamed (`test_a_courier_review_record_reads_its_routes_harness`) to assert `dimensions(record)["harness"] == "codex"`, the H9b D6 follow-through. The new `harness` key in `--json` rows is tolerated by readers (additive; no job schema change, `SCHEMA_VERSION` stays 1).
- **D7. Released as `feat:`, and the title says why.** The change adds a user-visible grouping to a command and a key to its `--json` rows, and corrects what a Codex review row reports, so under the repository's title rules it is a `feat:` and a minor release. The shipped harnesses are two (`claude-code` and `codex`), and only a Controller-run Codex review can differ from `claude-code` until a second worker harness exists (H4), so on today's data the new groupings separate Claude reviews and workers from Codex reviews. The guide says so.

## Out of scope

The Codex tester (C10g), a Codex worker (H4), a change to how the harness is derived beyond D3, the usage budget, `harness.models`, any new setting, option, gate or exit code, any change to the job record or its schema version, and recovering the harness of a courier record written before H1.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The harness groupings, the row key and the courier correction | - | 2 | 1 |
| CP2 | The guides and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The harness groupings, the row key and the courier correction

**Files:** `controller/telemetry.py`, `controller/cli.py`, `tests/test_telemetry_harness.py`, `tests/test_telemetry.py`, `tests/test_route_select_job.py`

D1 to D6. In `tests/test_telemetry.py` only the expected key set of `test_text_and_json_of_the_existing_groupings_are_unchanged` gains `"harness"`; in `tests/test_route_select_job.py` only the courier test named in D6 is inverted and renamed. In `controller/telemetry.py`: the three `GROUPINGS` entries; `rows` adds `"harness": block.get("harness", route["harness"])` beside `role` and `model`; `dimensions` drops the courier exception (D3) and its comment says a courier record's route names its reviewer. In `controller/cli.py` only the `--by` help text names the three groupings.

Tests, in the new `tests/test_telemetry_harness.py` (it builds job records with the helpers `tests.test_telemetry` uses, or its own equivalents, in a temporary runtime root; no `claude`, `codex`, network or sandbox):
- a worker record whose `worker_route.harness` is `claude-code`, a worker record whose route names a second harness, and a worker record with no route: the rows read `claude-code`, the second harness and `claude-code`;
- a functional-review record (`kind` `functional`, a top-level `telemetry` block with no `harness`, route `claude-code`): its row reads `claude-code`;
- a courier record whose `worker_route.harness` is `codex`: its row reads `codex` (D3); a courier record whose route names `claude-code` reads `claude-code`; a courier record with no route reads `claude-code`;
- `--by harness`, `--by role,harness` and `--by role,harness,model`: the group keys, their order and their job counts for a mixed set; a group's `telemetry_unavailable` is its Codex review jobs (no figures) and its figures total only the jobs that carry them (D4);
- the text output prints `harness=codex` style labels and `--json` carries `by`, the row `harness` key and the group keys (D5);
- a recorded block that already names a harness wins over the dimensions (D2);
- the usage text of `telemetry --help` lists the new groupings, and an unknown grouping is still argparse's usage error (exit `2`).

The run list: `tests.test_telemetry_harness`, `tests.test_telemetry` (one assertion widened, D6), `tests.test_route_select_job` (one test inverted, D6), `tests.test_cli`, all green. The full suite is the operator's rerun.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guides and the roadmap

**Files:** `docs/guide/commands.md`, `docs/guide/workers.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D3 to D5, D7. `docs/guide/commands.md`, "`telemetry`": the three groupings in the `--by` paragraph, the `harness` row key in the `--json` paragraph, and what a Codex review group shows (no figures). `docs/guide/workers.md`, "Telemetry": `harness` is no longer described as always `claude-code` but as the harness the job ran on (a worker's route, a review's reviewer), with the pre-H1 courier limit of the section above. `docs/guide/runtime.md` does not change: it shows `telemetry --by stage,round` and `--by review_mode` as examples only and lists no groupings. `docs/ROADMAP.md`: row H11 complete with a summary and the issue link, the "Next action" paragraph pointing at the next item of the epic. `docs/ACTIVE_MILESTONE.md` records the milestone as the acceptance step does. `python3 tools/check_docs.py` passes.
<!-- /CP2 -->

## Functional review notes

Every flow is unit level and runs in the tester's sandbox with no `claude` or `codex`: the cases of `tests.test_telemetry_harness`, `tests.test_telemetry` and `tests.test_route_select_job` with their two D6 edits, and `python3 tools/check_docs.py`. The full suite is not a flow and no flow needs a scenario sandbox.
