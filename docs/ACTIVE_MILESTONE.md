# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-settings-and-telemetry` (`docs/ROADMAP.md` step C3, sections
1.4, 8 and 11.1.2). The plan is `docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md`,
revision 14, approved at `b0e2f9a` (`EXTERNAL_APPROVE`, review content id `db313bbe`). The base
commit is `a47e695`. Governing workflow version `2.2`, lifecycle authority Workflow 2.6.0. Pull
request title
`feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches`
(1.5.0).

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-ci-reliability.md`.

All seven checkpoints are complete, and the implementation has technical approval (`2683558`,
`EXTERNAL_APPROVE` of bundle `6cc2ab3b` after three review rounds). The functional review
checklist is below.

## Goal

Four things, each opt-in or behaviour-preserving by default:
- **Settings file v1 (1.4).** One user-level JSON file holds every operational tunable and the
  routing defaults. The Controller fills in missing settings, records which defaults it wrote and
  at which generation, and moves an untouched value only forward. Unknown keys are warned about,
  and `settings clean` removes them, but never a newer release's.
- **Telemetry v0 (8).** Each job records its session's totals over every `result` event, and a
  read-only `telemetry` command summarises them, deriving older jobs from `worker.stdout`.
- **Release notes follow the milestone (11.1.2).** Readiness puts the milestone's notes section
  into the pull request body, bound by a marker carrying the work-item id and a digest, and the
  release publishes the verified blocks from the squash commits of its range, or refuses.
- **The four open 1.4 patches**: parsing resume hints, a truthful manual-external gate,
  relaunch-bound tests, and a useful `status` with `--json`.

## Checkpoint progress

| Checkpoint | Status | Notes |
|---|---|---|
| CP1 The settings file | Complete | See below |
| CP2 The settings wired in | Complete | See below |
| CP3 Telemetry v0 | Complete | See below |
| CP4 Release notes follow the milestone | Complete | See below |
| CP5 The hints parse and the manual-external gate tells the truth | Complete | See below |
| CP6 Relaunch-bound tests and `status` | Complete | See below |
| CP7 Documentation and full verification | Complete | See below |

### CP1 -- the settings file

- `controller/settings.py` (new, after `routing` in the dependency order):
  - `TABLE`, the closed table of `Setting(key, type, default, minimum, maximum, cli_flag,
    generation)` (A.3), and `TABLE_GENERATION = 1`. Every default equals the constant it will
    replace.
  - `resolve_path`: `--settings`, `$WORKFLOW_CONTROLLER_SETTINGS`,
    `$XDG_CONFIG_HOME/workflow-controller/settings.json`,
    `~/.config/workflow-controller/settings.json`, each made absolute (A.1).
  - Validation (I2) refuses with `SettingsError` (exit 20), naming the path and the dotted key, and
    never rewrites the file: not UTF-8 or JSON, a duplicate key, `schema_version` other than 1, a
    section that is not an object, a wrong type (a boolean is not an integer) or out-of-bounds
    value, a malformed `_table_generation` or `_defaults_written` (not `{value, generation}` with a
    positive integer generation, or an entry for a key the file does not hold), and a malformed
    routing section. An absent `schema_version` is filled in as 1, like any missing key.
  - `load` (read-only), `fill` and `clean`. The fill and `clean` run their whole
    read-modify-write under the sibling lock `settings.json.lock`, re-reading the bytes there, and
    write only when the content changes (I4). The fill adds each missing setting with
    `_defaults_written[key] = {value, generation}`, moves an untouched value forward only when this
    release's generation for the key is greater than the recorded one, and never lowers
    `_table_generation`. When it cannot write, one warning says so, and the file's values still
    apply. `clean` refuses (exit 20, nothing removed) a file whose `_table_generation` is newer
    than this release's; otherwise it fills, then removes every unknown key and its bookkeeping.
  - Unknown keys (top level, inside a section, and in the routing section) are ignored with one
    warning per invocation, which says when the file was last filled by a newer release.
  - `resolve` gives `EffectiveSettings`: each value and its source (`cli`, `file`, `default`), and
    the routing in force (`--routing-config` replaces the section whole, I1). CP2 wires it into
    every command.
- `controller/routing.py`: `validate_routing_mapping(data, *, path, where, require_schema_version,
  unknown)`, the one validator. `parse_routing_config` calls it strictly with `where=""`, so
  `--routing-config` keeps its messages and evidence, and every `tests/test_routing.py` case
  passes unchanged. The settings section uses `where="routing."`, no `schema_version` (reserved
  there, and refused) and `unknown="ignore"` (unknown roles, route fields and keys directly under
  `routing` are left out and reported). `SECTION_KEYS` names `default` and `roles`.
- `controller/runtime.py`: `settings_target`, `settings_lock` (an `flock` on the sibling lock
  file), `read_settings_bytes` and `write_settings_atomically` (`write_json` contained against the
  file's own directory, symlinks resolved first, so a symlinked file stays a symlink).
- `controller/errors.py`: `SettingsError` (`SETTINGS_ERROR`).
- `controller/cli.py`: the global `--settings PATH`, passed on absolute across the re-exec; the
  `settings show|path|clean` subcommand (A.6), dispatched before pinning, like `follow`, since it
  touches only the settings file. `show` prints each effective value with its source (`--timeout`
  and `--routing-config` are the global overrides it can see) and writes nothing; `--json` is
  honoured.
- `tests/__init__.py` (I5): at package import, `XDG_CONFIG_HOME` points at a fresh temporary
  directory and `WORKFLOW_CONTROLLER_SETTINGS` is removed. One side effect for developers: Git
  reads `$XDG_CONFIG_HOME/git/config`, so a Git identity kept only there is not seen by the tests
  (this machine and CI use `~/.gitconfig`).
- `tests/test_settings.py` (new, 41 tests): the pinned table and the compatibility rule checked
  against every generation's bounds and type; the location order and the re-exec argv; every
  refusal, for `load`, `fill` and `clean`, with the file left unchanged; the fill (create,
  additive, write only on change, never lowering the generation, the unwritable-directory warning
  with the file's values still applied); the unknown-key warning; `clean`; the two-release test
  (N+1 moves the untouched value and adds a key; N never moves it back, warns, and its `clean`
  refuses and keeps N+1's key; an operator-set value never moves); the shared-file routing test
  (N loads with exit 0 and one warning naming `routing.roles.new-role`, the extra field and
  `routing.budgets`, resolves the known role as before, and its `clean` refuses; N+1 routes the
  new role; a refused value and a non-object `roles` still exit 20; `--routing-config` with the
  unknown role still raises `RoutingConfigError`); the routing parse tests; the lock tests (four
  concurrent fills; the fill and `clean` each re-read under a lock the test holds while it edits
  the file; a `clean` racing a fill keeps both changes); and the I5 guard (this process is
  isolated; a child started with `WORKFLOW_CONTROLLER_SETTINGS` naming a sentinel runs `settings
  clean`, `settings show` and a fill under an audit hook that fails on any open of
  `~/.config/workflow-controller/`, the sentinel or the parent's `XDG_CONFIG_HOME`; an invalid
  sentinel stays byte-identical and a missing one is never created; a direct `python3 -m unittest
  tests.test_settings...` run is isolated too).
- `tests/test_package_structure.py`: `settings` in the dependency order.
- Verified: `tests.test_settings`, `tests.test_routing`, `tests.test_cli`, `tests.test_runtime`,
  `tests.test_write_containment` and `tests.test_package_structure` pass; the full sharded suite
  (`python3 tools/run_tests.py`, in the foreground, without `PYTHONPATH` or `FORCE_COLOR`, under a
  reaping-subreaper wrapper since this session is a Controller-launched worker) passed: 2545 tests
  in 6 shards, exit 0. `~/.config/workflow-controller/` did not exist before or after the runs (I5).

### CP2 -- the settings wired in

- `controller/cli.py`: `_apply_settings` resolves the settings once per invocation, in `main`'s
  `_dispatch`, after the re-exec and the `identity.json` write and before any command runs (a
  refusal leaves the ordinary dispatch footprint and no job record); `follow` resolves them
  read-only before its own dispatch. A writing command (`step`, `run`, `resume`,
  `milestone-binding`) fills the file first; the read-only ones only load it (A.4). The result is
  put on `args.effective_settings`, and `settings.apply_process_defaults` is called there, its
  only production call, before the `--follow` renderer thread starts. A namespace that did not
  come through `main` (a direct call in a test) gets the built-in defaults under its own options
  and reads no file.
  - `--timeout`, `run --max-steps` (now `default=None`) and the new `resume --drain-timeout
    SECONDS` take `_positive_int`: `0`, a negative number or a non-integer exits 2 (I1's behaviour
    change). The table's bounds still apply only to the file.
  - Passed explicitly: the worker timeout and the drain bound to `job.execute_step`, the drain
    bound to `job.resume` (`--drain-timeout` beats the file for that re-attach only), `max_steps`
    to the run loop and the run record, and the heartbeat and replay count to `observe.follow_run`/
    `follow_job` (and the heartbeat to `--follow`'s renderer).
  - Routing: `_routing_options` takes the routing in force from the settings (the
    `--routing-config` file replaces the section whole, I1), with `config_source` mapped from its
    source.
  - The launched job record carries the optional `controller_settings` block (`path`, the
    `sha256` of the bytes read, every effective `values` entry and its `sources`). No-launch
    records (`GATE_BLOCKED`, `DECLINED`, `HANDOFF_PENDING`) do not carry it.
- `controller/worker.py`: `launch` and `reattach` take `drain_detach_seconds` (`None`: the
  constant, still 10800 and the setting's default); `DrainDetached` carries the bound applied.
- `controller/job.py`: `execute_step(drain_detach_seconds=, controller_settings=)`,
  `resume(drain_detach_seconds=)` through phase 1's re-attach; the detach records
  `drain_detach_seconds` next to `drain_detached_at`, and `applied_drain_bound(record)` (the
  recorded bound, else the constant for an older record) is what the detach error prints.
  `worker_route` gains `config_source`.
- `controller/observe.py`: the follower takes `heartbeat_seconds` and `replay_events` (`0` replays
  nothing); the activity line's "detached after" reads `job.applied_drain_bound(record)`, so
  `status` and `follow` in a later invocation print the bound that was applied.
- `controller/routing.py`: `RoutingOptions.config_source` (`settings`, `routing-config`, `none`;
  derived when omitted).
- Leaf values (`controller/gitrepo.py`, `forge.py`, `release_txn.py`, `workflow_contract.py`):
  each keeps its constant as the built-in default and gains a process-wide value read at call time
  (`gitrepo.timeout_seconds()`, `forge.pr_list_limit()`, `release_txn.command_timeout_seconds()`,
  `workflow_contract.query_timeout_seconds()`). `subprocess_runner`'s `timeout` defaults to
  `None`, read when the runner runs, so `_DEFAULT_RUNNER` and a `GhForge` built at import apply
  the value set later. `settings.apply_process_defaults` sets them; `settings.process_defaults`
  is the tests' context manager, which restores them.
- `tests/golden/no_policy_lifecycle.json`: regenerated, deliberately, for the two additive
  fields every launched job now carries (`controller_settings`, `worker_route.config_source`);
  the generator names a `--settings` file inside each case directory so the path normalises. The
  diff is additions only.
- Tests: `tests/test_cli.py` `SettingsWiringTest` (the timeout, drain, max-steps, follow and
  routing rows from the file to their consumers, each CLI flag beating the file, the real job
  record's `controller_settings` and `config_source`, an invalid file exiting 20 before any job
  record and unrewritten, the exit-2 flags); `tests/test_settings.py` `ProcessDefaultsTest` (the
  four leaf rows read at call time, including a runner and a forge built before the value is set,
  `main` applying the file's values, and an AST pin that `apply_process_defaults` has the one
  production caller); `tests/test_observe.py` (the heartbeat and replay parameters, and a recorded
  non-default bound printed by the presenter and the heartbeat while an older record prints the
  constant); `tests/test_resume.py`'s drain-detach tests now get their one-second bound only
  through the settings path (the constant is no longer patched), asserting the recorded bound and
  the message; the `execute_step` signature pin, the route-record and `--max-steps` default tests
  updated.
- Verified: the touched modules' tests pass; the full sharded suite (`python3 tools/run_tests.py`,
  in the foreground, without `PYTHONPATH` or `FORCE_COLOR`) passed: 2563 tests in 6 shards, exit
  0. `generate_no_policy_lifecycle.py --check` and `generate_external_implementation_review_decisions.py
  --check` are current; `generate_plan_stage_decisions.py --check` reports the 2.5.1 golden's
  documented permitted difference, the same at `a47e695` in a clean clone, which
  `tests.test_golden_plan_stage_decisions` reverts before comparing (it passes). `~/.config/workflow-controller/` does not exist (I5).

### CP3 -- telemetry v0

- `controller/worker_stream.py`: `session_telemetry(results)`, pure and never raising, reduces the
  `result` events to the session totals (Design C): `turns`, `duration_ms` and the four `usage`
  token counts summed; `cost_usd`, `duration_api_ms` and every `modelUsage` field (per model,
  per field) the maximum over all results, never the last result's (I7). A result with non-zero
  `usage` whose cost, API time and per-model figures equal the previous result's adds a
  `cumulative_not_advanced` problem naming its `result_index`. A contribution that is absent or
  not a finite number makes its figure `null` with a `missing_field`/`invalid_field` problem; no
  result gives `null` figures and `no_result` (I6).
- `controller/telemetry.py` (new; imports neither `job` nor `observe`): building a block
  (`read_worker_stdout`, `session_block`, `dimensions`, `failed_block`), deriving one for a
  record written before telemetry from its `worker.stdout` and `events.jsonl`
  (`derive_block`, `"derived": true`, `no_stream` when the stream is gone, nothing written back),
  and the reading side: `rows` (filters `--work-item`, `--run`, `--since`, the target), `groups`
  (`--by role|model|role,model`; totals and per-job means over the jobs carrying each figure, a
  `telemetry_unavailable` count for `failed` blocks and sessions with no result), the text
  rendering, and the presentation clause (`summary_text`, `last_finished`, `last_job_text`).
- `controller/job.py`: `_telemetry_block(record, streams, ...)` is the failure boundary: every
  read and computation inside one `try`/`except Exception`, a failure giving `{"version": 1,
  "failed": true, "problems": [{"kind": "telemetry_failed", ...}]}`; `KeyboardInterrupt` and
  `SystemExit` pass. Both completion paths (`_launch_job` and `_reattach`) call it after `result`
  is known and before the one `COMPLETED` write, whose other fields never read it, and add the
  block as `telemetry` plus a `telemetry` summary (totals, or `"failed"`) in the `completed`
  event's details. Wall times: `job_seconds` from `created_at` to completion; `worker_seconds`
  from the `on_spawn` flush (on re-attach the `worker_spawned` event, `null` without it) to the
  exit: the drain's `direct_child_exited_at` when the group outlived the worker, else completion
  live and the stdout's last write on re-attach. Dimensions: `role`, `model`, `effort`,
  `harness: "claude-code"`, `workflow_version`, `controller_version`.
- `controller/cli.py`: the read-only `telemetry [--run ID] [--since ISO] [--by ...] [repo]`
  subcommand (`--work-item` and `--json` global), dispatched before pinning like `follow`; a bad
  `--since` exits 2. `status` prints `last job telemetry: <id> (<status>, <work item>): cost ...,
  job N s, worker N s` for the newest job with a block, and `inspect` the same for its target
  (JSON `last_job_telemetry`, omitted when none) -- named apart from `explain`'s existing
  `last job` diagnosis. A `failed` block prints `telemetry unavailable`.
- `controller/observe.py`: `follow`'s `COMPLETED` line gains `; session: <totals>` when the event
  carries a summary (`telemetry unavailable` for a failed one; an older event is unchanged).
- `tests/golden/no_policy_lifecycle.json`: regenerated, deliberately, additions only: each
  completed job's `telemetry` block (the fake's results carry no `usage`, `modelUsage` or
  `duration_api_ms`, so those figures are `null` with their problems) and each `after` inspect's
  `last_job_telemetry`; the generator makes `job_seconds`/`worker_seconds` volatile and
  normalises `controller_version` like `version`.
- Tests: `tests/test_worker_stream.py` `SessionTelemetryTest` (the two-result contract fixture by
  hand: 127 + 8 = 135 turns, the summed tokens, cost 24.2492376, API time 2719274 and
  `claude-opus-5-5` output 326896 as the maximum, one `cumulative_not_advanced` at index 1; a
  lower later result keeps the maximum for cost, API time and each per-model field; four results;
  malformed input; junk never raising); `tests/test_job.py` `TelemetryCompletionTest` (a
  four-result `FAKE_CLAUDE_TURNS` session through `execute_step`; the failure boundary with
  `session_telemetry` and the stream read each raising, leaving status, outcome, the `worker`
  block, step 7's phase and verdict and the events unchanged; `KeyboardInterrupt` not swallowed)
  and `ReattachTelemetryTest` (the re-attach path's block, wall times, a missing spawn event, and
  the same failure boundary); `tests/test_telemetry.py` (derivation, filters, grouping, the
  command's text and `--json` and that it writes nothing, and `status`, `inspect` and `follow`
  printing figures or `telemetry unavailable`). Pinned key sets updated in
  `test_job.LaunchPathTest`, `test_trunk_preflight.ObservationTest`, the package order and
  `test_observation_equivalence`'s normalisation (wall times).
- Verified: the touched modules' tests pass; the full sharded suite (`python3 tools/run_tests.py`,
  in the foreground under a reaping subreaper, without `PYTHONPATH` or `FORCE_COLOR`) passed: 2593
  tests in 6 shards, exact coverage, exit 0. `generate_no_policy_lifecycle.py --check` and
  `generate_external_implementation_review_decisions.py --check` are current;
  `generate_plan_stage_decisions.py --check` differs exactly as at HEAD (CP2's note). Run against
  the live runtime root, `telemetry --by role --since 2026-09-30` summarised 110 jobs, all derived,
  in 0.4 s. `~/.config/workflow-controller/` does not exist (I5).

### CP4 -- release notes follow the milestone

- `controller/release_notes.py` (new, after `gitrepo` in the dependency order), the one module
  for the block: `extract_section` (the section under `## <heading>` up to the next `## ` line,
  outer blank lines trimmed; a CRLF heading still matches, so its notes are refused for the
  carriage return rather than silently absent), `render_block` (start marker with
  `work_item=<id> sha256=<digest>`, notes, end marker), the I8 checks (`notes_problem`: a
  non-blank line, no Controller marker text, no tab or CR, at most 72 bytes of UTF-8 per line, no
  trailing space; `paragraph_problem`: at most 65536 characters and every paragraph parsed on its
  own; `block_problem` for `notes-block`), `parse_message` (marker lines only, line-start
  anchored; UTF-8 required only for a message holding one; a start marker joined with its
  continuation lines up to `-->`; explicit pairing, so a damaged start consumes its end; empty
  notes, a bad `sha256=` token, a missing end and two blocks of one work item in one message are
  damaged blocks of that work item; an orphan end or a marker with no valid `work_item=` is
  unattributable) and `resolve` (newest block per work item wins, older ones named superseded,
  digest check, `### <id>` headings in commit then message order when several, refusals
  `missing`/`unreadable`/`unverified` with `supersedable` false for the two no block can clear).
- `controller/gitrepo.py`: `commit_message` and `tag_message` (raw bytes after the object
  header; a lightweight tag is `None`), `parse_trailers` (`git interpret-trailers --parse` on stdin
  with every `GIT_*` variable removed, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, in
  a private temporary directory under a `GIT_CEILING_DIRECTORIES` ceiling), and
  `create_annotated_tag` now passing `--no-sign --cleanup=verbatim` with the one final newline
  Git's default cleanup adds, so this repository's tag messages are byte-identical to before
  (measured: default cleanup stores `M\n`, verbatim stores `M`).
- `controller/repo_policy.py`: optional `milestone_branches.pull_request.release_notes {path,
  heading}` (`ReleaseNotes`; the path admits only `{work_item_id}`, any number of times, and must
  render to a normalised relative path; the heading is a non-empty single line) and the
  `{release_notes}` placeholder, admitted in `release.publication.notes` only
  (`Release.uses_release_notes`). This repository's `policy.json` is byte-unchanged (I9).
- `controller/milestone_branch.py`: readiness reads the section at `a` (`git show`, never the
  working tree) from the path and heading of the binding's policy snapshot, puts the block above
  the Controller's lines, and checks the whole rendered body (I8) before any edit; a failure is
  the new `release_notes_invalid` gate (in `GATE_CODES` and `decision.BRANCH_GATE_TEXTS`), which
  edits nothing. Its exit is to merge on GitHub and supply the notes at release, since a commit
  after `a` stops readiness at `post_acceptance_commits`. The `pr_edited` and `ready` events
  carry `release_notes: absent|empty|included` when the snapshot configures notes (unchanged
  otherwise).
- `controller/release_txn.py`: `Classification.release_range` on `RELEASE_DUE` only, from the
  shared base-tag helper `_highest_tag` (both triggers, `INVALID_TRANSITION` and the resume);
  `range_notes` reads every first-parent commit message of the range as bytes and resolves the
  blocks, refusing with `ReleaseTransactionError` before the tag, each refusal naming its fix
  (supply the notes in a later trunk commit, by the merged pull request's block or
  `tools/release.py notes-block`; rerun for a failed Git read; the policy opt-out for the two
  unclearable ones, and after the supplied notes for the rest). Opted-in `RESUME` with no release
  reuses the tag's message only when the notes recomputed over the tag's own range under the
  current template equal it byte for byte (else `unverified tag`, fix: create that release by
  hand); a lightweight tag or a failed or non-UTF-8 read refuses before `create_release`.
  `PublishOutcome.notes` names the outcome (`included ...; superseded ...` or `reused from tag`).
- `tools/release.py`: `notes-block --work-item ID FILE` (trims outer blank lines, refuses an
  empty file, a file of blank lines and every D.2 check), and `publish` prints the notes
  outcome.
- Tests: `tests/test_release_notes.py` (new: extraction, line rules incl. 72/73 bytes and the
  accented 72-character line, the per-paragraph trailer rule with the real `git` -- the middle
  `Fixes:`/`Breaking-Change:` paragraph a whole-body parse accepts, bare URL, one-line `Note:` --
  and the ordinary shapes; the trailer parse unchanged by `trailer.separators = :=` from a global
  file, the repository's configuration, `GIT_CONFIG_COUNT` and `GIT_DIR`, each shown to change a
  plain `git`; the parser round trip as written and wrapped as GitHub wraps it, `0de0fd5`'s and
  `e8cd8f9`'s marker splits, the rule and co-author trailer appended; pairing, damage,
  anchoring, encodings; `resolve`; `notes-block`). `tests/test_repo_policy.py`
  `ReleaseNotesPolicyTest`; `tests/test_pull_request_lifecycle.py` `SquashReleaseNotesTest`
  (included with a `###` line, absent, empty, the trailer and line-rule and length refusals with
  no edit, not UTF-8, the snapshot's path and heading winning over `a`'s policy, a snapshot
  without notes, D.4's exact template, idempotence, the uncommitted section never read);
  `tests/test_release_txn.py` `ReleaseRangeTest` and `ReleaseNotesTransactionTest` (every
  publish case of the checkpoint, over disposable repositories with raw commit objects so a
  non-UTF-8 message survives, the fake forge recording that `release create` never ran on a
  refusal, and a run spy showing no tree read). `tests/test_integration_disposable_repo.py`
  needed no change: the disposable-repository publish coverage lives in `test_release_txn`.
- Verified: the touched modules' tests pass; the full sharded suite (`python3 tools/run_tests.py`,
  foreground, under a reaping subreaper, without `PYTHONPATH` or `FORCE_COLOR`) passed: 2657
  tests in 6 shards, exact coverage, exit 0. `generate_no_policy_lifecycle.py --check` and
  `generate_external_implementation_review_decisions.py --check` are current;
  `generate_plan_stage_decisions.py --check` differs exactly as at HEAD (CP2's note).
  `.workflow-controller/policy.json` is byte-unchanged from `a47e695`.

### CP5 -- the hints parse and the manual-external gate tells the truth

- Hints (E.1). `decision.explain_gate_command(root, id)` is now the one `explain` hint
  (`workflow-controller --work-item <id> explain <repo>`, both values `shlex`-quoted), defined in
  `decision.py` because `evidence.py` imports `decision` and not the other way round;
  `evidence._explain_gate_command` delegates to it. `evidence._explain_command` is deleted, and its
  five callers, `decision.py`'s plan-approval gate and `evidence.py`'s incomplete-children gate
  use the parsing form. `job._resume_command(root, runtime_root)` carries `--runtime-dir` when
  given one, as `follow_command` does; the activity hint `status`, `inspect`, `explain` and
  `follow` print passes it (`observe.resume_command(record, runtime_root)` in `job_activity`).
  The pending-job clearing commands and the `resume` error texts keep their form, beside their
  `--abandon` siblings. The I10 test found one more hint that did
  not parse: `decision.branch_human_gate`'s fallback `workflow-controller step` named no
  repository; it now names the gate's repository, quoted.
- I10 (`tests/test_hints_parse.py`, new): an AST scan of every string and f-string in
  `controller/` (docstrings aside) holding `workflow-controller <subcommand or option>` renders
  each interpolation from a table of sample values (every alternative, e.g. both `resume
  --abandon` forms and both `milestone-binding` dispositions; an unknown interpolation fails,
  so a new hint must be added to the table) and parses the command, up to its closing backtick
  or the end of the string, with `build_parser().parse_args`; the builders themselves are
  parsed too, with a repository path that needs quoting, and `explain --work-item` may appear
  nowhere in `controller/`.
- Manual-external gate (E.2). `evidence._manual_external_ledger_problem(stage, work_item,
  manifest)` runs in both manual-external handlers' "no feedback" branch, before today's gate:
  the ledger is well formed (`read_implementation_review_ledger`'s `malformed`; for the plan
  stage the new `_plan_ledger_malformed`, mirroring Workflow's `_validate_plan_review_stages`),
  its `review_content_id` is the bundle manifest's, and a local-stage `APPROVE` is recorded. The
  comparisons are shared with the admissibility evaluators, not copied: the implementation
  evaluator's "ledger review_content_id" and "local approval" clauses and the plan evaluator's
  "local approval" clause are now `_implementation_ledger_id_failure`,
  `_implementation_local_approval_failure` and `_plan_local_approval_failure`, used by both
  sides (the plan stage's ledger-to-manifest comparison is new: a plan verdict is matched
  against the ledger alone). A failed check gives a human gate at the same phase whose reason is
  `manual_external_ledger_incoherent`; its text starts "do not send <bundle> for external
  review", names the check, the manifest's id and the ledger's (or "none"), never "upload" or
  "from the ledger", and names the way out: the `/milestone-plan <id>` withdrawal for the plan
  stage, as text only (with the discards-both-stages, never-re-binds-unchanged clause; the safe
  resume command names the user's decision to withdraw, since Workflow 2.6.0's I4, pinned by
  `NoMilestonePlanAtPlanReviewReadyPhaseTest`, forbids `/milestone-plan` as a ready-phase gate's
  command), explicit user resolution of the Workflow state for the
  implementation stage (no Workflow command moves that phase back). Neither names
  `/review-plan`, `/review-implementation` or `explain`. A coherent ledger gives today's gate,
  byte for byte.
- `tests/golden/plan_stage_decisions.2.6.0.json` regenerated deliberately: its 58 changed
  cases are all `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` scenarios whose plan manifest is missing,
  foreign or states no `review_content_id` while the replayed publication status is `BOUND`;
  they now give the incoherent-ledger gate instead of offering that bundle. The reference
  release's fresh derivation is byte-identical before and after CP5.
- `tests/fixtures.build_plan_manifest_text` now writes `review_content_id` (default `c` * 64,
  the id every fixture ledger records), as the real generator does.
- Tests: `tests/test_evidence.py` `ManualExternalPlanLedgerIncoherentTest` and
  `ManualExternalImplementationLedgerIncoherentTest` (malformed ledgers of each shape, an id
  differing from the manifest, an absent plan ledger, no local `APPROVE`, each way-out text, and
  the coherent ledger's exact gate text); `test_lifecycle_orchestration`'s "local review
  records an APPROVE bound to other content" case, which pinned the old gate offering the
  ledger's wrong id, now expects the incoherent gate. The activity-hint pins in `test_observe`,
  `test_cli` and `test_resume` carry `--runtime-dir`. The pinned `explain --work-item` strings in
  `test_cli`, `test_job`, `test_evidence`, `test_integration_disposable_repo`,
  `test_lifecycle_orchestration` and `test_decision` now expect `explain_gate_command`.
- Verified: the touched modules' tests pass; the full sharded suite (`python3 tools/run_tests.py`,
  under a reaping subreaper, without `PYTHONPATH` or `FORCE_COLOR`) passed: 2671 tests in 6
  shards, exact coverage, exit 0. `generate_no_policy_lifecycle.py --check`,
  `generate_external_implementation_review_decisions.py --check` and
  `generate_plan_stage_decisions.py --release 2.6.0 --check` are current;
  `generate_plan_stage_decisions.py --check` differs exactly as at HEAD (CP2's note).
  `.workflow-controller/policy.json` is byte-unchanged from `a47e695`.

### CP6 -- relaunch-bound tests and `status`

- E.3, tests only; they found no defect, so `controller/job.py` and `controller/evidence.py` are
  unchanged:
  - `tests/test_job.py` `LastLaunchedApplyJobViewTest`: a seeded `FAILED` apply record with
    `reconciliation_evidence.code` `OperatorAbandoned` or `UnreconcilableJobError` is J, and
    `relaunch_bound_applies` holds against its bundle and not another; an abandoned record that
    never reached `LAUNCHED` (no `expected_transition`) is not J, and an earlier real attempt
    behind it still counts.
  - `ApplyingReviewFeedbackExecuteTest`: the same two records bind the gate through
    `execute_step` (`GATE_BLOCKED`, "job earlier-apply, ended FAILED", the explain hint), and the
    never-launched abandoned record does not (the apply launches).
  - `tests/test_lifecycle_orchestration.py` `AbandonedApplyRelaunchBoundTest`, end to end: a
    launched apply job (no-op worker, left `LAUNCHED` on disk as a lost Controller would),
    `resume --abandon` marks it `FAILED`/`OperatorAbandoned` (exit 0), and the next `step` stops
    at the relaunch-bound gate naming it, launches nothing, and its `safe_resume_command` parses
    with the live parser as `--work-item wi explain <repo>`.
- E.4, `status`:
  - `controller/cli.py`: `cmd_status` renders `_status_view`, one JSON-ready object, so the text
    and `--json` (the global flag, now honoured) carry the same fields: `controller`,
    `runtime_root`, `ladder_row`, `runtime_state` and, with runtime state, `pinned_identity`,
    `jobs` (`count`, `recent`), `handoff`, `active` (`runs`, `jobs`), `last_job_telemetry` and
    `bindings`. The jobs are still those recorded when the process started. The `jobs:` line is
    `jobs: <n> recorded` (`jobs: none` when there are none), followed by the 10 newest, newest
    first, one per line: `<id> <status> <command> (work item <id>): age 2 h` while active, `...:
    wall 1400 s, cost $11.66` once terminal (the cost only with telemetry figures). An active
    run's line gains `started <time>`; an active job's line gains its command, work item and
    `started <time>`. Every other line is unchanged.
  - `controller/observe.py`: `job_command`, `job_summary`, `recent_jobs` (`STATUS_RECENT_JOBS =
    10`), `job_summary_text` and `age_text` (`s` under 2 min, `min` under 2 h, `h` under 2 days,
    then `d`). The wall time is the telemetry block's `job_seconds`, else `created_at` to
    `updated_at`.
  - `controller/milestone_branch.py`: `binding_entries` (the bindings as objects);
    `binding_lines` renders them, unchanged.
  - `controller/telemetry.py`: `last_job_entry`, the `last_job_telemetry` object now shared by
    `inspect --json` and `status --json`; `_money` is public as `money_text`.
  - Tests: `tests/test_observe.py` `StatusJobSummaryTest`; `tests/test_cli.py` `StatusJobsTest`
    (the count and the ten newest, a job recorded after the process started not reported, the
    `--json` object, and `--json` with no runtime state) and `StatusActiveSectionTest`'s new
    line shapes; `JobActivitySurfacesTest`'s pinned active line updated.
- Verified: the touched tests pass; the full sharded suite (`python3 tools/run_tests.py`, under a
  reaping subreaper, without `PYTHONPATH` or `FORCE_COLOR`) passed: 2687 tests in 6 shards,
  exact coverage, exit 0. `generate_no_policy_lifecycle.py --check`,
  `generate_external_implementation_review_decisions.py --check` and
  `generate_plan_stage_decisions.py --release 2.6.0 --check` are current.
  `.workflow-controller/policy.json` is byte-unchanged from `a47e695`.

### CP7 -- documentation and full verification

- `docs/guide/runtime.md`: a new section, "The settings file": the location order, the table
  (rendered from `settings.TABLE`), precedence, the fill and forward-only migration, the lock,
  the refusals, unknown keys and `settings clean`, and the routing section. It says that the file
  is shared by every repository and lane on the machine.
- `docs/guide/commands.md`: `settings show|path|clean`, `telemetry`, `status` (its lines and
  `--json` keys), `resume --drain-timeout`, the global `--settings`, the exit-2 positive-integer
  flags, `WORKFLOW_CONTROLLER_SETTINGS`, and the routing levels now naming the settings section.
- `docs/guide/workers.md`: the drain bound, worker timeout and follow values as settings
  (`drain_detach_seconds` recorded and printed), the `COMPLETED` line's session summary, the
  resume hint's `--runtime-dir`, and a new "Telemetry" section.
- `docs/guide/milestone-branches.md`: a new section, "Release notes in the pull request body",
  and the `release_notes_invalid` gate (the refused shapes, the line rules, the 62-character id
  note, and the exit: mark ready, merge, supply the notes at release).
- `docs/guide/ci-and-releases.md`: a new subsection, "Release notes from the milestones", covering
  every Design F item: `{release_notes}`, the range read and digest check, the squash message
  setting and the measured wrap, the included and refused outcomes, the fixes and the opt-out,
  marker lines only, `--cleanup=verbatim`, supersession, the accepted lost-body gap, the binding
  snapshot, the `RESUME` tag check (fix: create that release by hand as a draft with the right
  notes, so the publish uploads the assets), empty blocks, no tag yet, and the cutover.
- `docs/guide/troubleshooting.md`: `SettingsError` and its warnings, the incoherent
  manual-external gate, a `release_notes_invalid` pointer; the drain and query-timeout entries no
  longer hard-code their bounds.
- `docs/README.md`: the release-notes rule as the policy opt-in, and ADR 0008 in the table.
- `docs/adr/0008-controller-settings-file.md` (new): location, precedence, validation, the fill
  and forward-only migration, unknown keys and the routing section, and the settings/constant
  boundary.
- This narrative's `## Release notes` section holds the 1.5.0 notes; it passes
  `release_notes.notes_problem` and, rendered as a block, `paragraph_problem`.
- Verified: the full sharded suite (`python3 tools/run_tests.py`, under a reaping subreaper,
  without `PYTHONPATH` or `FORCE_COLOR`) passed: 2687 tests in 6 shards, exact coverage, exit 0.
  `generate_no_policy_lifecycle.py --check`,
  `generate_external_implementation_review_decisions.py --check` and
  `generate_plan_stage_decisions.py --release 2.6.0 --check` are current;
  `generate_plan_stage_decisions.py --check` differs exactly as at `a47e695` (CP2's note) and
  `tests.test_golden_plan_stage_decisions` passes. `git diff a47e695 -- .workflow-controller/
  pyproject.toml setup.py .github/workflows/` is empty.

### Implementation review round 1 fixes

The manual external review of implementation revision 1 returned `REVISE` with two Important
findings, both reproduced before fixing:
- `4d87e48`: `settings clean` reported an unknown dotted role such as
  `routing.roles.review-plan.future` as removed but left it, because the path decoder read it as
  `review-plan`'s field. Unknown routing paths are now decoded against the section, every
  reading present is removed, and the path is reported once.
- `c5b00e7`: opted in, a publish that lost the tag-push race to a same-commit tag kept its own
  rendered notes and skipped verification. The race path now verifies the winning remote tag as
  a `RESUME` does (`gitrepo.remote_tag_message`, which writes no ref); a lightweight tag or other
  notes refuse before the release is created. The `create_annotated_tag` docstring wording
  (Optional) is fixed in the same commit; LIR1-O1 (exact end-marker matching) follows the
  approved plan and is unchanged.
- Verified: the full sharded suite passed (2691 tests, 6 shards, exact coverage, exit 0, under a
  reaping subreaper); the three golden `--check` runs and `tools/ci_workflows.py --check` exit 0.

### Implementation review round 2 fixes

The local review of implementation revision 2 returned `REVISE` with one Important finding,
reproduced before fixing:
- `e38d0d5`: LIR2-I1, the round 1 fix overcorrected. `settings clean` removed a known, valid
  value when an unknown dotted key shared its path (the role `review-plan.model` beside
  `review-plan`'s `model`; the routing key `default.effort` beside `default`'s `effort`). A reading
  of an unknown path is now removed only when its last segment is unknown at its own level.
  The reviewer's suggested `_defaults_written` check found the same collision: an unknown
  top-level key named `worker.timeout_seconds` dropped the known setting's record. Records are
  now matched by segments. The missing `CleanTest` cases are added.
- Verified: the full sharded suite passed (2693 tests, 6 shards, exact coverage, exit 0, under a
  reaping subreaper). `tools/ci_workflows.py --check` and two of the three golden `--check` runs
  exit 0. `tests/golden/generate_plan_stage_decisions.py --check` now reports its
  `AMENDING_PLAN` cases differ in this environment. It fails the same way at the milestone base
  `a47e695`, so this milestone did not cause it. `tests.test_golden_plan_stage_decisions` passes.

## Functional review checklist

Round 1, implementation revision 3: technical approval `2683558`, reviewed implementation head
`96bbded`, implementation bundle `6cc2ab3b`, PR #16 (draft) at `2683558`. Every expected result
below was measured on 2026-10-01 against `2683558` in a scratch directory (`/tmp/c3-fr`), by
running the commands exactly as written. Values that cannot repeat (commit ids made by
`git commit-tree`, job ids, pids, times) are shown as `<...>`.

Nothing here writes to this repository, its remote, GitHub, the user's settings file
(`~/.config/workflow-controller/`, which does not exist and must still not exist afterwards), the
installed Controller (1.4.2) or its runtime root (`~/.local/state/workflow-controller/`): every
command runs in `$S`, with `XDG_CONFIG_HOME` pointing into `$S`, an explicit `--runtime-dir`
inside `$S` and, where a file is the subject, an explicit `--settings`. The repository and the
live runtime root are only read (flows A, I and L). The only GitHub access is `gh pr checks 16`.

### Setup

Run each code block below as one non-interactive `bash` script (for example, save it to a file
and run `bash <file>`), in the order given: not in `zsh`, whose word splitting differs, and not
pasted line by line. Flow H needs G's `turns.json`, and K needs J's `notes.md` and `body.txt`.
Every block after setup step 1 starts with `. /tmp/c3-fr/fr.sh`, which unsets `FORCE_COLOR`,
`PYTHONPATH`, `WORKFLOW_CONTROLLER_SETTINGS`, `GH_TOKEN` and `GITHUB_OUTPUT`, points
`XDG_CONFIG_HOME` into `$S`, and turns Git's automatic maintenance off. Never set
`PYTHONPATH=.`, and run test suites in the foreground.

**Setup 1.** The scratch directory, a scratch bare origin pinned to the milestone base and head, a clone of
the head, and a local build of it in a throwaway virtual environment:

```bash
S=/tmp/c3-fr; rm -rf "$S"; mkdir -p "$S"; cd "$S"
cat > "$S/fr.sh" <<'XEOF'
unset FORCE_COLOR PYTHONPATH WORKFLOW_CONTROLLER_SETTINGS GH_TOKEN GITHUB_OUTPUT
export S=/tmp/c3-fr R=/home/rodrigo/Workspace/workflow-controller
export W=$S/venv/bin/workflow-controller XDG_CONFIG_HOME=$S/xdg
export H=268355881fb709f1395b2f96eb9d4dbe149bb01b BASE=a47e6955cd4aebdee5482ba7cc2c788c2eb057c3
export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=maintenance.auto GIT_CONFIG_VALUE_0=false GIT_CONFIG_KEY_1=gc.auto GIT_CONFIG_VALUE_1=0
TITLE='feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches (#16)'
WI=workflow-controller-settings-and-telemetry
# mktarget DIR: a Workflow-installed Git repository with no work item yet
mktarget() {
  git init -q -b main "$1" && git -C "$1" config user.email fr@example.invalid && git -C "$1" config user.name fr &&
  mkdir -p "$1/.workflow-manager" "$1/docs/ai-workflow" &&
  printf '{\n  "schema_version": 1,\n  "workflow_version": "2.6.0",\n  "profile": "full"\n}\n' > "$1/.workflow-manager/installation.json" &&
  printf '{\n  "schema_version": 1,\n  "active_work_item_id": null,\n  "work_items": {}\n}\n' > "$1/docs/ai-workflow/WORKFLOW_STATE.json" &&
  git -C "$1" add -A && git -C "$1" commit -qm initial
}
# lastjob RT: the newest job record under runtime root RT
lastjob() { ls -t "$1"/jobs/*.json | head -1; }
# fresh NAME: a release sandbox: a bare origin (real tags, main at BASE), a work clone of it
# (the current directory afterwards), and tests/fake_gh.py as gh with v1.1.1-v1.4.2 published
fresh() {
  X=$S/rel/$1; rm -rf "$X"; mkdir -p "$X/bin"
  git init -q --bare "$X/origin.git"
  git --git-dir="$X/origin.git" fetch -q --no-tags "$R" "+$BASE:refs/heads/main" "+refs/tags/v*:refs/tags/v*" "+$H:refs/heads/milestone"
  git clone -q "$X/origin.git" "$X/work"; git -C "$X/work" config user.name fr; git -C "$X/work" config user.email fr@example.invalid
  cp "$S/src/tests/fake_gh.py" "$X/bin/gh"; chmod +x "$X/bin/gh"
  python3 - "$X/gh-state.json" <<'EOF'
import json, sys
repo = "RodrigoFAbreu/workflow-controller"
rel = [{"tagName": t, "isDraft": False, "url": f"https://github.com/{repo}/releases/tag/{t}", "assets": [],
        "title": t, "notes": f"workflow-controller {t}"} for t in ("v1.1.1", "v1.2.0", "v1.2.1", "v1.3.0", "v1.4.0", "v1.4.1", "v1.4.2")]
json.dump({"repository": repo, "url": f"https://github.com/{repo}", "next_number": 17, "prs": [], "releases": rel},
          open(sys.argv[1], "w"), indent=2)
EOF
  export PATH="$X/bin:$PATH" FAKE_GH_STATE="$X/gh-state.json" FAKE_GH_ORIGIN="$X/origin.git" FAKE_GH_LOG="$X/gh.log" FAKE_GH_FAIL='{}'
  cd "$X/work"
}
# squash MSGFILE: the milestone's squash commit on the current commit, with that message
squash() { git commit-tree "$H^{tree}" -p "$(git rev-parse HEAD)" -F "$1"; }
# optin PARENT: a chore: commit on PARENT whose policy opts in to release notes (the cutover's two keys)
optin() {
  git checkout -q --detach "$1"
  python3 - <<'EOF'
p = ".workflow-controller/policy.json"; t = open(p).read()
t = t.replace('"merge_method": "squash"}', '"merge_method": "squash",\n                     "release_notes": {"path": "docs/milestones/completed/{work_item_id}.md", "heading": "Release notes"}}')
t = t.replace('"notes": "workflow-controller {tag}"}', '"notes": "workflow-controller {tag}\\n\\n{release_notes}"}')
open(p, "w").write(t)
EOF
  git commit -qam "chore: opt in to release notes from the milestones (#17)"; git rev-parse HEAD
}
# advance C: make C the sandbox origin's main and check it out
advance() { git push -q origin "$1:refs/heads/main" && git fetch -q origin && git checkout -q --detach "$1"; }
# release C: advance to C, build the wheel, publish; then report the tag and the release-create calls
release() {
  advance "$1"; RELEASE_VERSION=1.5.0 python3 tools/release.py build >/dev/null || return 9
  python3 tools/release.py publish --commit "$1"; echo "publish exit $?"
  echo "origin v1.5.0: $(git --git-dir="$FAKE_GH_ORIGIN" for-each-ref refs/tags/v1.5.0 --format='%(objecttype)' | grep . || echo absent); local v1.5.0: $(git tag -l v1.5.0 | grep . || echo absent); release create calls: $(grep -c '"release", "create"' "$FAKE_GH_LOG" 2>/dev/null || true)"
}
XEOF
. "$S/fr.sh"
git init -q --bare "$S/origin.git"
git --git-dir="$S/origin.git" fetch -q --no-tags "$R" "+$BASE:refs/heads/main" "+refs/tags/v*:refs/tags/v*" \
  "+$H:refs/heads/milestone/workflow-controller-settings-and-telemetry"
git clone -q "$S/origin.git" "$S/src" && git -C "$S/src" checkout -q --detach "$H"
python3 -m venv "$S/venv" && "$S/venv/bin/pip" -q install "$S/src"
git -C "$R" show "$H:docs/ACTIVE_MILESTONE.md" > "$S/narrative.md"
```

**Setup 2.** The offline stub Workflow Manager (the tests' `write_stub_workflow_manager`, Workflow 2.6.0), and
a reaping-subreaper wrapper for the unit-test runs. A Controller-launched session needs the
wrapper: its orphans otherwise stay zombies under the Controller and the reap assertions fail.

```bash
. /tmp/c3-fr/fr.sh
printf '%s\n' '#!/bin/sh' 'case "$1" in verify|status) echo "workflow 2.6.0 (full profile) -- clean"; exit 0;; *) echo "stub workflow-manager: unknown subcommand $1" >&2; exit 1;; esac' > "$S/wm"; chmod +x "$S/wm"
cat > "$S/reap.py" <<'EOF'
"""Run argv as a child under a reaping subreaper: orphans re-parented here are collected."""
import ctypes, os, sys
ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
pid = os.fork()
if pid == 0:
    os.execvp(sys.argv[1], sys.argv[1:])
status = 1
while True:
    try:
        reaped, st = os.waitpid(-1, 0)
    except ChildProcessError:
        break
    if reaped == pid:
        status = os.waitstatus_to_exitcode(st)
        break
sys.exit(status)
EOF
```

**Setup 3.** Two patched builds, never committed, each in its own throwaway environment: "N+1", a later
release whose table raises `follow.heartbeat_seconds`'s default to 45 at generation 2 and adds
`follow.tail_lines` (so `TABLE_GENERATION` is 2) for flow C; and "TF", whose
`worker_stream.session_telemetry` raises, for flow H. Both report `uncommitted changes`, so an
acting command needs `--allow-dirty-source`; the `settings` commands do not pin and need none.

```bash
. /tmp/c3-fr/fr.sh
for b in n1 tf; do git clone -q "$S/origin.git" "$S/$b" && git -C "$S/$b" checkout -q --detach "$H"; done
python3 - <<'EOF'
import pathlib
p = pathlib.Path("/tmp/c3-fr/n1/controller/settings.py"); t = p.read_text()
t = t.replace('Setting("follow.heartbeat_seconds", TYPE_INT, 30, 1, 3600, None, 1),',
              'Setting("follow.heartbeat_seconds", TYPE_INT, 45, 1, 3600, None, 2),\n'
              '    Setting("follow.tail_lines", TYPE_INT, 100, 1, 1000, None, 2),')
p.write_text(t.replace("\nTABLE_GENERATION = 1\n", "\nTABLE_GENERATION = 2\n"))
p = pathlib.Path("/tmp/c3-fr/tf/controller/worker_stream.py"); t = p.read_text()
head = "def session_telemetry(results: Iterable[dict]) -> dict:\n"
p.write_text(t.replace(head, head + '    raise RuntimeError("functional review: injected telemetry failure")\n', 1))
EOF
git -C "$S/n1" diff --stat; git -C "$S/tf" diff --stat
for b in n1 tf; do python3 -m venv "$S/venv-$b" && "$S/venv-$b/bin/pip" -q install "$S/$b"; done
```
Expected: `controller/settings.py | 5 +++--` and `controller/worker_stream.py | 1 +`.

No other test data is needed: each flow builds its own targets, and flow I copies four job
records from the live runtime root.

### Flows

**A. The local build, and nothing else changed.**

```bash
. /tmp/c3-fr/fr.sh; cd "$R"
"$W" --version
for c in inspect explain; do
  workflow-controller --runtime-dir "$S/rt-old" "$c" "$R" > "$S/$c-old.txt" 2>&1; e1=$?
  "$W" --runtime-dir "$S/rt-new" "$c" "$R" > "$S/$c-new.txt" 2>&1; e2=$?
  echo "$c: 1.4.2 exit $e1, local exit $e2"; cmp "$S/$c-old.txt" "$S/$c-new.txt" && echo "$c: byte-identical"
done
git diff --stat "$BASE" "$H" -- .workflow-controller/ pyproject.toml setup.py .github/; echo "policy/packaging/CI diff above (expect none)"
(cd "$S/src" && python3 tools/ci_workflows.py --check; echo "ci_workflows --check exit $?")
```
Expected:
- `workflow-controller 1.4.2` and `runtime: package (local build from 268355881fb7)`: the version
  stays tag-derived until the 1.5.0 tag exists;
- `inspect: 1.4.2 exit 0, local exit 0`, `inspect: byte-identical`, and the same for `explain`
  (both read this repository at the same moment, each with an empty scratch runtime root, so no
  job record or telemetry line differs). With `--json` the documents differ only in the
  `controller` identity block (`build_origin`, `package_digest`, `release_tag`, `source_commit`);
- no `git diff` output; `ci_workflows --check exit 0`.

**B. The settings file: location, `show`, `path`, `clean`, refusals.**

**B1.** Location and `show`/`path`, with no file:
```bash
. /tmp/c3-fr/fr.sh; cd "$S"
"$W" settings path
WORKFLOW_CONTROLLER_SETTINGS=$S/env-settings.json "$W" settings path
WORKFLOW_CONTROLLER_SETTINGS=$S/env-settings.json "$W" --settings rel/flag.json settings path
env -u XDG_CONFIG_HOME HOME="$S/home" "$W" settings path
"$W" settings show; echo "exit $?"; ls "$S/xdg" 2>&1
"$W" --timeout 3600 settings show | grep timeout_seconds
"$W" --json settings show | python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted(d), d['exists'], d['sha256'])"
```
Expected, in order: `/tmp/c3-fr/xdg/workflow-controller/settings.json`;
`/tmp/c3-fr/env-settings.json`; `/tmp/c3-fr/rel/flag.json` (the flag beats the variable and is
made absolute); `/tmp/c3-fr/home/.config/workflow-controller/settings.json`. Then
`settings file: /tmp/c3-fr/xdg/workflow-controller/settings.json (not created yet)` and ten
lines, each `(default)`: `worker.drain_detach_seconds = 10800`, `worker.timeout_seconds = null`,
`run.max_steps = 20`, `follow.heartbeat_seconds = 30`, `follow.replay_events = 20`,
`timeouts.git_seconds = 600`, `timeouts.release_command_seconds = 1800`,
`timeouts.workflow_query_seconds = 120`, `forge.pr_list_limit = 200`,
`routing = {"default": {}, "roles": {}}`; `exit 0`; `ls` reports no such file (show wrote
nothing). `worker.timeout_seconds = 3600 (cli)`. The JSON keys are
`['exists', 'path', 'sha256', 'sources', 'values'] False None`.

**B2.** `clean` creates and fills the file, and a second run changes nothing:
```bash
. /tmp/c3-fr/fr.sh
F=$S/xdg/workflow-controller/settings.json
"$W" settings clean; echo "exit $?"; ls -A "$S/xdg/workflow-controller"; sha256sum < "$F"
python3 -c "import json; d=json.load(open('$F')); print(sorted(d)); print(d['_table_generation'], d['_defaults_written']['run.max_steps'], d['worker'])"
"$W" settings clean; sha256sum < "$F"; "$W" settings show | head -2
```
Expected: `nothing to remove from /tmp/c3-fr/xdg/workflow-controller/settings.json`, `exit 0`;
`settings.json` and `settings.json.lock`; sha256 `aee573cae2323447c76d52427e01a6b3a04b4ebaf2991ba519ad143390767606  -`;
`['_defaults_written', '_table_generation', 'follow', 'forge', 'routing', 'run', 'schema_version', 'timeouts', 'worker']`,
`1 {'generation': 1, 'value': 20} {'drain_detach_seconds': 10800, 'timeout_seconds': None}`. The
file is canonical JSON (sorted keys, two-space indent, final newline) with all ten
`_defaults_written` entries at generation 1, as in `docs/guide/runtime.md`. The second `clean`
prints the same line and the hash is unchanged; `show` now says `(file)`.

**B3.** Every refusal exits 20, names the file and the dotted key, and leaves the file byte-unchanged,
for `show` (read-only) and for `clean` (which fills first):
```bash
. /tmp/c3-fr/fr.sh; mkdir -p "$S/bad"; cd "$S/bad"
printf '{"run": {"max_steps": 20,}' > notjson.json
printf '{"run": {"max_steps": 20}, "run": {"max_steps": 5}}' > dup.json
printf '{"schema_version": 2}' > schema2.json
printf '{"run": {"max_steps": true}}' > bool.json
printf '{"run": {"max_steps": 0}}' > zero.json
printf '{"worker": {"drain_detach_seconds": 59}}' > low.json
printf '{"worker": "x"}' > section.json
printf '{"_defaults_written": {"run.max_steps": {"value": 20, "generation": 0}}, "run": {"max_steps": 20}}' > dw.json
printf '{"routing": {"schema_version": 1, "default": {}, "roles": {}}}' > rsv.json
printf '{"routing": {"default": {}, "roles": []}}' > rroles.json
printf '{"routing": {"default": {"model": 5}, "roles": {}}}' > rmodel.json
printf '\xff\xfe{}' > notutf8.json
for f in notjson dup schema2 bool zero low section dw rsv rroles rmodel notutf8; do
  h1=$(sha256sum < $f.json); "$W" --settings $f.json settings show >/dev/null 2>$f.err; e=$?
  "$W" --settings $f.json settings clean >/dev/null 2>&1; e2=$?
  echo "$f: show exit $e, clean exit $e2, unchanged: $([ "$h1" = "$(sha256sum < $f.json)" ] && echo yes || echo NO)"; cat $f.err
done
```
Expected: every line `show exit 20, clean exit 20, unchanged: yes`, and each message starts
`error: the settings file /tmp/c3-fr/bad/<f>.json cannot be used:`, followed by:

| File | Message tail |
|---|---|
| `notjson` | `it is not JSON (Illegal trailing comma before end of object: line 1 column 25 (char 24))` |
| `dup` | `duplicate key(s) ['run']` |
| `schema2` | `schema_version must be 1, got 2` |
| `bool` | `run.max_steps must be an integer from 1 to 1000, got true` |
| `zero` | `run.max_steps must be an integer from 1 to 1000, got 0` |
| `low` | `worker.drain_detach_seconds must be an integer from 60 to 604800, got 59` |
| `section` | `worker must be an object, got "x"` |
| `dw` | `_defaults_written.run.max_steps must be {"value": ..., "generation": <positive integer>}, got {"value": 20, "generation": 0}` |
| `rsv` | `routing.schema_version is reserved: a routing section carries no schema_version (it looks like a --routing-config file pasted in whole; drop the key)` |
| `rroles` | `routing.roles must be an object, got []` |
| `rmodel` | `routing.default.model: expected a non-empty string, got 5` |
| `notutf8` | `it is not UTF-8 ('utf-8' codec can't decode byte 0xff in position 0: invalid start byte)` |

**B4.** Only the writing commands fill; a key the operator set has no `_defaults_written` record; an
unwritable directory warns once and the file's values still apply:
```bash
. /tmp/c3-fr/fr.sh; cd "$S"; mkdir -p plain part ro
"$W" --settings "$S/fill/inspect.json" --runtime-dir "$S/rt-b" inspect "$S/plain" >/dev/null 2>&1; echo "inspect exit $?"; ls "$S/fill" 2>&1
"$W" --settings "$S/fill/step.json" --runtime-dir "$S/rt-b" step "$S/plain" >/dev/null 2>&1; echo "step exit $?"; ls "$S/fill"
printf '{"run": {"max_steps": 9}}\n' > part/s.json
"$W" --settings part/s.json --runtime-dir "$S/rt-b" step "$S/plain" >/dev/null 2>&1
python3 -c "import json; d=json.load(open('part/s.json')); print(d['run'], d['_defaults_written'].get('run.max_steps'), len(d['_defaults_written']))"
printf '{"run": {"max_steps": 9}}\n' > ro/s.json; chmod 555 ro
"$W" --settings ro/s.json --runtime-dir "$S/rt-b" step "$S/plain"; echo "exit $?"; cat ro/s.json; chmod 755 ro
```
Expected: `inspect exit 20` (`plain` is not a Git repository) and `ls` reports
`/tmp/c3-fr/fill` missing; `step exit 20`, and `step.json`, `step.json.lock` exist (the fill
runs before dispatch). `{'max_steps': 9} None 9`. Then
`workflow-controller: warning: could not fill the settings file /tmp/c3-fr/ro/s.json ([Errno 13] Permission denied: '/tmp/c3-fr/ro/s.json.lock'); using its current values, and the built-in defaults for missing keys`,
the not-a-repository error, `exit 20`, and the file still `{"run": {"max_steps": 9}}`.

**B5.** Unknown keys warn once and `clean` removes them; a symlinked file stays a symlink:
```bash
. /tmp/c3-fr/fr.sh; U=$S/unk; mkdir -p "$U"
printf '{"bogus_top": 1, "follow": {"heartbeat_seconds": 30, "bogus": 2}, "routing": {"default": {"model": "m1", "future_field": "x"}, "roles": {"new-role": {"model": "x"}, "review-plan": {"effort": "max"}}, "budgets": {}}}\n' > "$U/s.json"
"$W" --settings "$U/s.json" settings show 2>&1 | grep -E "warning|routing ="; echo "exit ${PIPESTATUS[0]}"
"$W" --settings "$U/s.json" settings clean; echo "exit $?"
python3 -c "import json; d=json.load(open('$U/s.json')); print(d['routing'], d['follow'], 'bogus_top' in d)"
"$W" --settings "$U/s.json" settings show 2>&1 | grep -c warning
mkdir -p "$S/sym/real"; printf '{"run": {"max_steps": 9}}\n' > "$S/sym/real/s.json"; ln -s real/s.json "$S/sym/link.json"
"$W" --settings "$S/sym/link.json" settings clean; [ -L "$S/sym/link.json" ] && echo "still a symlink"
```
Expected: `` workflow-controller: warning: the settings file /tmp/c3-fr/unk/s.json has unknown key(s) bogus_top, follow.bogus, routing.budgets, routing.default.future_field, routing.roles.new-role; they are ignored (`workflow-controller settings clean` removes them) ``,
`routing = {"default": {"model": "m1"}, "roles": {"review-plan": {"effort": "max"}}} (file)`,
`exit 0`; `removed from /tmp/c3-fr/unk/s.json: bogus_top, follow.bogus, routing.budgets, routing.default.future_field, routing.roles.new-role`,
`exit 0`; `{'default': {'model': 'm1'}, 'roles': {'review-plan': {'effort': 'max'}}} {'heartbeat_seconds': 30, 'replay_events': 20} False`;
`0` warnings; `nothing to remove from /tmp/c3-fr/sym/link.json`, `still a symlink`.

**B6.** Optional, the lock: eight `settings clean` at once on a missing file.
```bash
. /tmp/c3-fr/fr.sh; mkdir -p "$S/conc"
for i in 1 2 3 4 5 6 7 8; do "$W" --settings "$S/conc/s.json" settings clean >/dev/null 2>&1 & done; wait
python3 -c "import json; d=json.load(open('$S/conc/s.json')); print(len(d['_defaults_written']), d['_table_generation'])"
```
Expected: `10 1`.

**C. Two releases share one file: the forward-only fill.** N is the local build, N+1 the patched
build from setup step 3.

```bash
. /tmp/c3-fr/fr.sh; W1=$S/venv-n1/bin/workflow-controller; F=$S/two/settings.json; mkdir -p "$S/two" "$S/plain"
"$W" --settings "$F" settings clean
python3 - "$F" <<'EOF'
import json, sys; p = sys.argv[1]; d = json.load(open(p)); d["run"]["max_steps"] = 7
json.dump(d, open(p, "w"), indent=2, sort_keys=True); open(p, "a").write("\n")
EOF
echo "--- N+1 fills"; "$W1" --settings "$F" settings clean; echo "exit $?"
python3 -c "import json; d=json.load(open('$F')); w=d['_defaults_written']; print(d['follow'], d['run'], d['_table_generation'], w['follow.heartbeat_seconds'], w['follow.tail_lines'], w['run.max_steps'])"
echo "--- N reads"; "$W" --settings "$F" settings show 2>&1 | grep -E "warning|max_steps|heartbeat"
h=$(sha256sum < "$F")
echo "--- N clean"; "$W" --settings "$F" settings clean; echo "exit $?"
echo "--- N fills"; "$W" --settings "$F" --runtime-dir "$S/rt-b" step "$S/plain" 2>&1 | head -1
[ "$h" = "$(sha256sum < "$F")" ] && echo "file unchanged by N"
```
Expected:
- N's first `clean` creates the file (`nothing to remove from /tmp/c3-fr/two/settings.json`);
- N+1: `--- N+1 fills`, the same line, `exit 0`; then
  `{'heartbeat_seconds': 45, 'replay_events': 20, 'tail_lines': 100} {'max_steps': 7} 2 {'generation': 2, 'value': 45} {'generation': 2, 'value': 100} {'generation': 1, 'value': 20}`:
  the untouched heartbeat moved forward to 45, the new key was added, `_table_generation` is 2,
  and the operator's `max_steps: 7` did not move (its record still says 20);
- N reads it with exit 0 and one warning:
  `workflow-controller: warning: the settings file /tmp/c3-fr/two/settings.json has unknown key(s) follow.tail_lines; they are ignored (the file was last filled by a newer Controller release (table generation 2, this release's is 1), whose keys these probably are)`,
  then `run.max_steps = 7 (file)` and `follow.heartbeat_seconds = 45 (file)`;
- N's `clean` refuses: `` error: the settings file /tmp/c3-fr/two/settings.json cannot be used: it was last filled by a newer Controller release (table generation 2, this release's is 1), so this release cannot tell that release's keys from retired ones; run `workflow-controller settings clean` from the newest installed release ``,
  `exit 20`;
- N's fill (a `step`, which then fails on `plain`) prints the same unknown-key warning, and
  `file unchanged by N`: N never moves 45 back to 30, nor lowers the generation.

**D. Routing in the file, and `--routing-config` still works.** Each `step` launches the bare
`/milestone-plan` on a fresh target with `tests/fake_claude.py` as `claude`; the unscripted
worker succeeds, the plan postcondition does not hold, so each exits 30 with a `FAILED` job.

```bash
. /tmp/c3-fr/fr.sh; D=$S/route; mkdir -p "$D"; mktarget "$D/t"
printf '{"routing": {"default": {"effort": "high"}, "roles": {"milestone-plan": {"model": "file-model"}}}}\n' > "$D/settings.json"
printf '{"schema_version": 1, "default": {"model": "rc-model"}}\n' > "$D/rc.json"
printf '{"schema_version": 1, "roles": {"new-role": {"model": "x"}}}\n' > "$D/rc-unknown.json"
B="--runtime-dir $D/rt --workflow-manager $S/wm --claude-binary $S/src/tests/fake_claude.py --settings $D/settings.json"
for extra in "" "--routing-config $D/rc.json" "--role-model milestone-plan=cli-model"; do
  echo "=== [$extra]"; FAKE_CLAUDE_DIAG_LOG=$D/argv.jsonl "$W" $B $extra step "$D/t" >/dev/null 2>&1; echo "exit $?"
  python3 -c "import json; d=json.load(open('$(lastjob "$D/rt")')); r=d['worker_route']; print(r['config_source'], r['model'], r['effort'], r['sources'])"
  tail -1 "$D/argv.jsonl" | python3 -c "import json,sys; a=json.loads(sys.stdin.read())['argv']; print([x for i, x in enumerate(a) if x in ('--model', '--effort') or a[i-1] in ('--model', '--effort')])"
done
n=$(ls "$D"/rt/jobs/*.json | wc -l)
"$W" $B --routing-config "$D/rc-unknown.json" step "$D/t"; echo "exit $?; jobs $n -> $(ls "$D"/rt/jobs/*.json | wc -l)"
"$W" --settings "$D/settings.json" --routing-config "$D/rc.json" settings show | grep routing
```
Expected:
- `[]`: `exit 30`, `settings file-model high {'effort': 'config-default', 'model': 'config-role'}`, `['--model', 'file-model', '--effort', 'high']`;
- `[--routing-config …/rc.json]`: `exit 30`, `routing-config rc-model None {'effort': 'inherit', 'model': 'config-default'}`, `['--model', 'rc-model']`: the file replaces the section whole, so the section's `high` is gone too;
- `[--role-model milestone-plan=cli-model]`: `exit 30`, `settings cli-model high {'effort': 'config-default', 'model': 'role-cli'}`, `['--model', 'cli-model', '--effort', 'high']`;
- `--routing-config` with an unknown role keeps its strict parse: `error: the routing config /tmp/c3-fr/route/rc-unknown.json cannot be used: unknown role(s) ['new-role']; known roles: apply-implementation-review, apply-plan-review, milestone-implement, milestone-implement-self-review, milestone-plan, record-manual-implementation-review, record-manual-plan-review, review-implementation, review-plan`, `exit 20; jobs 3 -> 3`;
- `routing = {"default": {"model": "rc-model"}, "roles": {}} (cli)`.

**E. The settings reach their consumers; the non-positive flags refuse.**

```bash
. /tmp/c3-fr/fr.sh; D=$S/wire; mkdir -p "$D"; mktarget "$D/t"
printf '{"run": {"max_steps": 3}, "worker": {"timeout_seconds": 600}, "follow": {"replay_events": 3}}\n' > "$D/settings.json"
B="--runtime-dir $D/rt --workflow-manager $S/wm --claude-binary $S/src/tests/fake_claude.py --settings $D/settings.json"
runmax() { python3 -c "import json,glob,os; r=sorted(glob.glob('$D/rt/runs/*.json'), key=os.path.getmtime)[-1]; print('run record max_steps', json.load(open(r))['max_steps'])"; }
jobset() { python3 -c "import json; cs=json.load(open('$(lastjob "$D/rt")'))['controller_settings']; print(cs['path'], {k: (cs['values'][k], cs['sources'][k]) for k in ('run.max_steps', 'worker.timeout_seconds', 'follow.replay_events', 'worker.drain_detach_seconds')})"; }
"$W" $B run "$D/t" >/dev/null 2>&1; echo "run exit $?"; runmax; jobset
"$W" $B --timeout 120 run --max-steps 2 "$D/t" >/dev/null 2>&1; echo "run exit $?"; runmax; jobset
id=$(basename "$(lastjob "$D/rt")" .json); "$W" $B follow --job "$id" "$D/t" | wc -l; "$W" $B follow --from-start --job "$id" "$D/t" | wc -l
for a in "--timeout 0 step" "--timeout -5 step" "--timeout abc step" "run --max-steps 0" "resume --drain-timeout 0" "resume --drain-timeout -1"; do
  "$W" $B $a "$D/t" >/dev/null 2>"$D/e"; echo "$a -> exit $?: $(tail -1 "$D/e")"; done
n=$(ls "$D"/rt/jobs/*.json | wc -l); printf '{"run": {"max_steps": 0}}\n' > "$D/settings.json"; h=$(sha256sum < "$D/settings.json")
"$W" $B step "$D/t"; echo "exit $?"; [ "$h" = "$(sha256sum < "$D/settings.json")" ] && echo "file not rewritten"; echo "jobs $n -> $(ls "$D"/rt/jobs/*.json | wc -l)"
```
Expected:
- `run exit 30`, `run record max_steps 3`, then `/tmp/c3-fr/wire/settings.json {'run.max_steps': (3, 'file'), 'worker.timeout_seconds': (600, 'file'), 'follow.replay_events': (3, 'file'), 'worker.drain_detach_seconds': (10800, 'file')}`
  (the fill added the drain key);
- `run exit 30`, `run record max_steps 2`, then `(2, 'cli')` and `(120, 'cli')` for the first
  two keys;
- `follow` replays `3` lines by default (`follow.replay_events`), `13` with `--from-start`;
- the six flags each exit 2: `workflow-controller: error: argument --timeout: must be a positive integer, not 0`,
  `… not -5`, `argument --timeout: invalid positive integer: 'abc'`,
  `workflow-controller run: error: argument --max-steps: must be a positive integer, not 0`,
  `workflow-controller resume: error: argument --drain-timeout: must be a positive integer, not 0`
  and `… not -1`;
- the invalid file: `error: the settings file /tmp/c3-fr/wire/settings.json cannot be used: run.max_steps must be an integer from 1 to 1000, got 0`,
  `exit 20`, `file not rewritten`, `jobs 2 -> 2` (no job record).

The leaf values (Git, release-command and Workflow-query timeouts, the pull-request list limit) and
the follow heartbeat have no observable shell effect within a few seconds; flow M's
`tests.test_settings.ProcessDefaultsTest` and `tests.test_observe` cover them.

**F. The drain bound from the file, and `resume --drain-timeout`.** About 70 s. The worker leaves
one `setsid` descendant (`fake-claude-orphan 600`) running after it exits.

```bash
. /tmp/c3-fr/fr.sh; D=$S/drain; mkdir -p "$D"; mktarget "$D/t"
printf '{"worker": {"drain_detach_seconds": 60}}\n' > "$D/settings.json"
B="--runtime-dir $D/rt --workflow-manager $S/wm --claude-binary $S/src/tests/fake_claude.py --settings $D/settings.json"
T='[[{"step": "bash_bg", "id": "a", "seconds": 0.2, "orphan": "setsid", "orphan_seconds": 600, "orphan_pid_file": "'$D'/orphan.pid"}, {"step": "end_turn"}], [{"step": "end_turn"}]]'
rec() { python3 -c "import json; d=json.load(open('$(lastjob "$D/rt")')); print(d['status'], (d.get('worker_state') or {}).get('state'), d.get('drain_detach_seconds'), 'telemetry results', (d.get('telemetry') or {}).get('results'))"; }
t0=$(date +%s); FAKE_CLAUDE_TURNS="$T" "$W" $B step "$D/t"; echo "exit $? after $(( $(date +%s) - t0 )) s"; rec
"$W" $B status | grep "detached after"
t0=$(date +%s); "$W" $B resume --drain-timeout 5 "$D/t"; echo "exit $? after $(( $(date +%s) - t0 )) s"; rec
"$W" $B status | grep "detached after"
hint=$("$W" $B status | grep -o 'workflow-controller --runtime-dir [^ ]* resume [^ ;]*' | head -1); echo "hint: $hint"
kill "$(cat "$D/orphan.pid")"; sleep 1
$(echo "$hint" | sed "s#^workflow-controller#$W --settings $D/settings.json --workflow-manager $S/wm#"); echo "exit $?"; rec
```
Expected:
- `` error: worker pid <p> exited, but 1 owned process(es) are still running after 60 s: <o> (fake-claude-orphan 600) -- job <id> stays held (LAUNCHED, DRAINING); either run `workflow-controller resume /tmp/c3-fr/drain/t` to re-attach and keep draining, or end them, then run it ``,
  `exit 45 after 61 s` (60 to 62), `LAUNCHED DRAINING 60 telemetry results None`;
- the status activity line contains `detached after 1:00 -- end them, then workflow-controller --runtime-dir /tmp/c3-fr/drain/rt resume /tmp/c3-fr/drain/t`;
- `resume --drain-timeout 5`: the same message with `after 5 s`, `exit 45 after 5 s`,
  `LAUNCHED DRAINING 5 …`; the status line now says `detached after 0:05` (the recorded bound,
  read back in a later invocation);
- `hint: workflow-controller --runtime-dir /tmp/c3-fr/drain/rt resume /tmp/c3-fr/drain/t`: the
  printed hint, run as printed (only the binary, `--settings` and the stub `--workflow-manager`
  put in front), parses and
  re-attaches: `<id>: FAILED`, `exit 0`, `FAILED ENDED 5 telemetry results 2` (the re-attach path
  records the telemetry block).

**G. Telemetry v0 on a real fake-worker job.** The fake plays four results (turn 0 starts three
background tasks, each completion starts one more turn), each carrying every figure.

```bash
. /tmp/c3-fr/fr.sh; J=$S/job; mkdir -p "$J"; mktarget "$J/t"
python3 - > "$J/turns.json" <<'EOF'
import json
def end(i, turns):
    cost = 1.5 * (i + 1)
    return {"step": "end_turn", "num_turns": turns, "duration_ms": 1000 * turns, "duration_api_ms": 500 * (i + 1),
            "total_cost_usd": cost,
            "usage": {"input_tokens": turns, "output_tokens": 10 * turns, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000},
            "modelUsage": {"claude-test": {"inputTokens": 2 * turns, "outputTokens": 20 * turns, "cacheCreationInputTokens": 200,
                                           "cacheReadInputTokens": 2000, "costUSD": cost}}}
print(json.dumps([[{"step": "bash_bg", "id": "a", "seconds": 0.2}, {"step": "bash_bg", "id": "b", "seconds": 0.5},
                   {"step": "bash_bg", "id": "c", "seconds": 0.8}, end(0, 107)], [end(1, 18)], [end(2, 11)], [end(3, 8)]]))
EOF
B="--runtime-dir $J/rt --workflow-manager $S/wm --claude-binary $S/src/tests/fake_claude.py --settings $J/settings.json"
FAKE_CLAUDE_TURNS="$(cat "$J/turns.json")" "$W" $B step "$J/t"; echo "step exit $?"
python3 -c "
import json; d=json.load(open('$(lastjob "$J/rt")')); t=d['telemetry']
print(d['status'], d['worker_outcome'], 'worker block (last result):', d['worker']['num_turns'], d['worker']['total_cost_usd'])
print({k: t[k] for k in ('results', 'turns', 'duration_ms', 'duration_api_ms', 'cost_usd', 'tokens', 'models', 'problems', 'role', 'harness', 'workflow_version', 'controller_version')})"
grep -h '"event": "completed"' "$J"/rt/jobs/*/events.jsonl | python3 -c "import json,sys; print(json.loads(sys.stdin.read())['telemetry'])"
"$W" $B telemetry; echo "exit $?"
"$W" $B telemetry --by role,model | head -2
"$W" $B --json telemetry | python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted(d), d['rows'][0]['derived'], d['groups'][0]['cost_usd'])"
"$W" $B telemetry --since 2030-01-01; "$W" $B telemetry --since yesterday; echo "exit $?"
"$W" $B status | grep -E "^jobs|^  2|last job telemetry"
"$W" $B --json status | python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted(d)); print(d['jobs']['count'], d['jobs']['recent'][0]['wall_seconds'], d['jobs']['recent'][0]['cost_usd'], d['last_job_telemetry']['telemetry']['cost_usd'])"
"$W" $B inspect "$J/t" | grep "last job telemetry"
id=$(basename "$(lastjob "$J/rt")" .json); "$W" $B follow --from-start --job "$id" "$J/t" | grep COMPLETED
cp -a "$J/rt" "$J/rt-copy"; workflow-controller --runtime-dir "$J/rt-copy" status | grep ^jobs; echo "1.4.2 status exit ${PIPESTATUS[0]}"
```
Expected:
- `step exit 30`; `FAILED SUCCESS worker block (last result): 8 6.0`;
- `{'results': 4, 'turns': 144, 'duration_ms': 144000, 'duration_api_ms': 2000, 'cost_usd': 6.0, 'tokens': {'cache_creation': 400, 'cache_read': 4000, 'input': 144, 'output': 1440}, 'models': {'claude-test': {'cache_creation': 200, 'cache_read': 2000, 'cost_usd': 6.0, 'input': 214, 'output': 2140}}, 'problems': [], 'role': 'milestone-plan', 'harness': 'claude-code', 'workflow_version': '2.6.0', 'controller_version': '1.4.2'}`:
  turns, `duration_ms` and tokens summed over the four results; cost, API time and the per-model
  figures the maximum (each result's running total), not the last result's alone;
- the `completed` event: `{'cost_usd': 6.0, 'duration_api_ms': 2000, 'job_seconds': <n>, 'results': 4, 'tokens': 5984, 'turns': 144, 'worker_seconds': <n>}`;
- `telemetry`: `jobs: 1`, `all jobs: 1 job(s), telemetry unavailable 0`, `turns 144 (mean 144)`,
  `tokens 5,984 (mean 5,984)`, `cost $6.00 (mean $6.00)`, `API time 2 s (mean 2 s)`,
  `job wall time <n> s (mean <n> s)`, `worker wall time <n> s (mean <n> s)`, `exit 0`;
- `jobs: 1, grouped by role,model` and `role=milestone-plan, model=None: 1 job(s), telemetry unavailable 0`;
- `['by', 'groups', 'rows'] False {'jobs': 1, 'mean': 6.0, 'total': 6.0}`;
- `jobs: 0`; then the usage error `workflow-controller telemetry: error: argument --since: not a UTC date or time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ): 'yesterday'`, `exit 2`;
- `status`: `jobs: 1 recorded`, `  <id> FAILED /milestone-plan (work item none): wall <n> s, cost $6.00`,
  `last job telemetry: <id> (FAILED, None): cost $6.00, 144 turns, 5,984 tokens, API 2 s, job <n> s, worker <n> s`;
- `['active', 'bindings', 'controller', 'handoff', 'jobs', 'ladder_row', 'last_job_telemetry', 'pinned_identity', 'runtime_root', 'runtime_state']`
  and `1 <n> 6.0 6.0`;
- `inspect` prints the same `last job telemetry:` line;
- `… COMPLETED (worker SUCCESS, exit 0); session: cost $6.00, 144 turns, 5,984 tokens, API 2 s, job <n> s, worker <n> s`;
- the installed 1.4.2 reads the new records: `jobs: <id>`, `1.4.2 status exit 0`.

**H. A telemetry failure leaves the outcome unchanged.** The same step with the TF build, whose
`session_telemetry` raises, beside the local build.

```bash
. /tmp/c3-fr/fr.sh; D=$S/tfail; mkdir -p "$D"; TURNS="$(cat "$S/job/turns.json")"
for b in good tf; do
  X="$W"; [ $b = tf ] && X="$S/venv-tf/bin/workflow-controller --allow-dirty-source"
  mktarget "$D/t-$b"; B="--runtime-dir $D/rt-$b --workflow-manager $S/wm --claude-binary $S/src/tests/fake_claude.py --settings $D/settings.json"
  FAKE_CLAUDE_TURNS="$TURNS" $X $B step "$D/t-$b" > "$D/$b.out" 2>&1; echo "$b: step exit $?"
  python3 -c "import json; d=json.load(open('$(lastjob "$D/rt-$b")')); print('$b:', d['status'], d['worker_outcome'], d['worker']['num_turns'], d['worker']['total_cost_usd'], json.dumps(d['telemetry'])[:160])"
  grep -h '"event": "completed"' "$D"/rt-$b/jobs/*/events.jsonl | python3 -c "import json,sys; print('$b: completed event', json.loads(sys.stdin.read())['telemetry'])"
  $X $B status | grep "last job telemetry"; $X $B telemetry | sed -n 2p
  $X $B follow --from-start --job "$(basename "$(lastjob "$D/rt-$b")" .json)" "$D/t-$b" | grep -o 'COMPLETED.*'
done
diff <(sed 's/rt-good/RT/g; s/t-good/T/g' "$D/good.out") <(sed 's/rt-tf/RT/g; s/t-tf/T/g' "$D/tf.out") && echo "step output identical"
```
Expected: both `step exit 30`; `good: FAILED SUCCESS 8 6.0 {"controller_version": …}` and
`tf: FAILED SUCCESS 8 6.0 {"failed": true, "problems": [{"error": "RuntimeError: functional review: injected telemetry failure", "kind": "telemetry_failed"}], "version": 1}`;
`good: completed event {… 'results': 4 …}`, `tf: completed event failed`; `tf`'s status line
`last job telemetry: <id> (FAILED, None): telemetry unavailable`, its summary
`all jobs: 1 job(s), telemetry unavailable 1`, and its `COMPLETED (worker SUCCESS, exit 0); session: telemetry unavailable`;
`step output identical` (status, outcome, the `worker` block, the exit code and the printed output
do not depend on telemetry).

**I. Jobs recorded before telemetry.** Copies of four real 1.4.x job records (three finished
workers and one `GATE_BLOCKED` record with no worker), with their stream paths pointed at the
copies. The live runtime root is only read.

```bash
. /tmp/c3-fr/fr.sh; O=$S/old; L=$HOME/.local/state/workflow-controller; mkdir -p "$O/rt/jobs"
for id in 20260930T204309Z-ed64b947 20260930T204333Z-5112719b 20260930T204549Z-db147454 20260930T204013Z-7e475e12; do
  sed "s#$L/#$O/rt/#g" "$L/jobs/$id.json" > "$O/rt/jobs/$id.json"; [ -d "$L/jobs/$id" ] && cp -a "$L/jobs/$id" "$O/rt/jobs/"; done
grep -l "$L" "$O"/rt/jobs/*.json; find "$O/rt" -type f | sort | xargs sha256sum > "$O/before.sha"
rows() { "$W" --runtime-dir "$O/rt" --settings "$O/s.json" --json telemetry | python3 -c "
import json,sys
for r in json.load(sys.stdin)['rows']:
    t = r['telemetry']; print(r['job_id'], r['role'], r['derived'], r['unavailable'], t['results'], t['turns'], t['cost_usd'], t['job_seconds'], [p['kind'] for p in t['problems']])"; }
rows; "$W" --runtime-dir "$O/rt" --settings "$O/s.json" telemetry | head -3
find "$O/rt" -type f | sort | xargs sha256sum | diff - "$O/before.sha" && echo "nothing written"; ls "$O"
rm "$O/rt/jobs/20260930T204333Z-5112719b/worker.stdout"; rows | sed -n 2p
```
Expected: no `grep -l` output (no path left pointing at the live root);
- `20260930T204309Z-ed64b947 apply-plan-review True False 1 22 1.1857674 156 []`
- `20260930T204333Z-5112719b record-manual-implementation-review True False 1 9 0.4543360000000001 45 []`
- `20260930T204549Z-db147454 review-plan True False 1 18 0.8403225999999999 131 []`
- `jobs: 3` (the `GATE_BLOCKED` record launched no worker), `all jobs: 3 job(s), telemetry unavailable 0`, `turns 49 (mean 16)`;
- `nothing written` and only `before.sha` and `rt` in `$O` (no settings file, no identity
  written by `telemetry`);
- with the stream gone: `20260930T204333Z-5112719b record-manual-implementation-review True True 0 None None 45 ['no_stream']`.

**J. Release notes: the readiness body and `notes-block`.** Readiness itself (the pull-request
edit) needs a policy-enabled lifecycle with a forge; it is exercised by
`SquashReleaseNotesTest` (flow M). Here the same functions render this milestone's own notes, read
from `$H:docs/ACTIVE_MILESTONE.md`, into the body readiness writes.

```bash
. /tmp/c3-fr/fr.sh; cd "$S"
"$S/venv/bin/python" - <<'EOF'
import hashlib
from controller import release_notes as rn, milestone_branch as mb
wid = "workflow-controller-settings-and-telemetry"
notes = rn.extract_section(open("/tmp/c3-fr/narrative.md", encoding="utf-8").read(), "Release notes")
open("/tmp/c3-fr/notes.md", "w").write(notes + "\n")
print("lines", len(notes.split("\n")), "| first:", notes.split("\n")[0], "| problem:", rn.notes_problem(notes))
body = mb.squash_body(wid, "docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md", accepted="a" * 40,
                   branch="milestone/" + wid, notes_block=rn.render_block(wid, notes))
open("/tmp/c3-fr/body.txt", "w").write(body)
lines = body.split("\n")
print(lines[0]); print(lines[len(notes.split("\n")) + 1])
print("digest is sha256(notes):", rn.digest(notes) == hashlib.sha256(notes.encode()).hexdigest(),
   "| body paragraph problem:", rn.paragraph_problem(body))
print("resolves to the notes:", rn.resolve([("Q", f"title (#16)\n\n{body}".encode())]).text == notes)
EOF
git interpret-trailers --parse < "$S/body.txt" | wc -l
mkdir -p "$S/nb"; cd "$S/nb"; printf '\n\nFixed the thing.\nSecond line.\n\n\n' > ok.txt
python3 - <<'EOF'
for name, text in {"l72": "x" * 72, "l73": "x" * 73, "acc72": "é" * 36, "acc37": "é" * 37, "trail": "Fixed the thing. ",
                   "marker": "See <!-- workflow-controller: release-notes end -->", "tab": "a\tb",
                   "trailer": "Intro.\n\nFixes: the thing\nBreaking-Change: none\n\nOutro.", "url": "Intro.\n\nhttps://example.com/x\n\nOutro.",
                   "blank": "\n  \n", "empty": None}.items():
    open(f"{name}.txt", "w").write("" if text is None else text + "\n")
EOF
for f in ok l72 l73 acc72 acc37 trail marker tab trailer url blank empty; do
  echo "=== $f"; python3 "$S/src/tools/release.py" notes-block --work-item wi-x $f.txt; echo "exit $?"; done
```
Expected:
- `lines 42 | first: ### Settings, telemetry and release notes from the milestone (1.5.0) | problem: None`;
- the body's first line `<!-- workflow-controller: release-notes work_item=workflow-controller-settings-and-telemetry sha256=680e387106204c05cb8228e6f3e77668022a7539b11d2d4d7bf1582042f6b4ef -->`,
  the block's last line `<!-- workflow-controller: release-notes end -->`, then the
  Controller's lines (`Milestone …`, `Accepted at …`, the work-item marker) below it;
- `digest is sha256(notes): True | body paragraph problem: None`, `resolves to the notes: True`;
  `git interpret-trailers --parse` prints nothing (`0`);
- `notes-block`:

  | File | Expected |
  |---|---|
  | `ok` | exit 0: `<!-- workflow-controller: release-notes work_item=wi-x sha256=4a86f652aa0c280f78248dfb40985c12eab7f6d7b2946541624a6fa4b6903867 -->`, `Fixed the thing.`, `Second line.`, the end marker (outer blank lines trimmed) |
  | `l72`, `acc72` | exit 0 (72 bytes; 36 `é` are 72 bytes) |
  | `l73` | exit 1: `release.py notes-block: refused: notes: l73.txt: line 1 'xxxx…': the line holds 73 bytes of UTF-8, over the limit of 72; wrap the section at 72 columns or remove the text` |
  | `acc37` | exit 1: `… the line holds 74 bytes of UTF-8, over the limit of 72; …` (37 characters) |
  | `trail` | exit 1: `… line 1 'Fixed the thing. ': the line ends in a space; wrap the section at 72 columns or remove the text` |
  | `marker` | exit 1: `… the notes contain the Controller marker text '<!-- workflow-controller:'; remove the text` |
  | `tab` | exit 1: `… line 1 'a\tb': the line holds a tab; …` |
  | `trailer` | exit 1: `… 'Fixes: the thing': the paragraph parses as a Git trailer block; reword the paragraph or join it to its neighbour` |
  | `url` | exit 1: `… 'https://example.com/x': the paragraph parses as a Git trailer block; …` |
  | `blank`, `empty` | exit 1: `release.py notes-block: refused: notes: <f>.txt holds no notes (it is empty or only blank lines)` |

**K. The release over a scratch origin with a fake `gh`.** Each case is a fresh sandbox
(`fresh`): `main` at `a47e695`, the real tags, and `v1.1.1`-`v1.4.2` published. The squash commit
`Q` carries the milestone tree and a squash message (title, blank line, body); `K` on top of it
is the cutover's `chore:` commit that opts in (`release_notes` and
`"notes": "workflow-controller {tag}\n\n{release_notes}"`), so the release commit is `K` and its
range `v1.4.2..K` holds `Q`. `release` builds the real wheel at 1.5.0 and runs
`tools/release.py publish`.

**K1.** Included:
```bash
. /tmp/c3-fr/fr.sh; fresh included >/dev/null
{ printf '%s\n\n' "$TITLE"; cat "$S/body.txt"; } > ../q.msg; Q=$(squash ../q.msg); git checkout -q --detach "$Q"; K=$(optin "$Q")
advance "$K"; python3 tools/release.py classify --commit "$K" | head -1; release "$K"
python3 - <<'EOF'
import json, os, subprocess
s = json.load(open(os.environ["FAKE_GH_STATE"])); r = [x for x in s["releases"] if x["tagName"] == "v1.5.0"][0]
msg = subprocess.run(["git", "--git-dir", os.environ["FAKE_GH_ORIGIN"], "cat-file", "tag", "v1.5.0"], capture_output=True, text=True).stdout.split("\n\n", 1)[1]
notes = open("/tmp/c3-fr/notes.md").read().removesuffix("\n")
print(r["isDraft"], [a["name"] for a in r["assets"]])
print("notes == template:", r["notes"] == "workflow-controller v1.5.0\n\n" + notes, "| tag message == notes + newline:", msg == r["notes"] + "\n")
EOF
```
Expected: `ok: RELEASE_DUE: 1.5.0 has no tag and no release` (on stderr) and `state=RELEASE_DUE`;
`ok: v1.5.0 at <K>: created (https://github.com/RodrigoFAbreu/workflow-controller/releases/tag/v1.5.0)`,
`ok: release notes: included workflow-controller-settings-and-telemetry (<Q>)`, `publish exit 0`,
`origin v1.5.0: tag; local v1.5.0: v1.5.0; release create calls: 1`;
`False ['workflow_controller-1.5.0-py3-none-any.whl', 'SHA256SUMS']` and
`notes == template: True | tag message == notes + newline: True`.

**K2.** Refused before any tag: no block, an empty block, an edited block.
```bash
. /tmp/c3-fr/fr.sh
"$S/venv/bin/python" - <<'EOF'
from controller import release_notes as rn, milestone_branch as mb
wid = "workflow-controller-settings-and-telemetry"; plan = "docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md"
kw = dict(accepted="a" * 40, branch="milestone/" + wid); notes = open("/tmp/c3-fr/notes.md").read().removesuffix("\n")
open("/tmp/c3-fr/body-none.txt", "w").write(mb.squash_body(wid, plan, **kw))
open("/tmp/c3-fr/body-empty.txt", "w").write(mb.squash_body(wid, plan, **kw, notes_block=rn.start_marker(wid, "") + "\n" + rn.END_MARKER))
open("/tmp/c3-fr/body-edited.txt", "w").write(mb.squash_body(wid, plan, **kw, notes_block=rn.render_block(wid, notes).replace("Telemetry v0.", "Telemetry v1.")))
EOF
for c in none empty edited; do echo "===== $c"; fresh $c >/dev/null
  { printf '%s\n\n' "$TITLE"; cat "$S/body-$c.txt"; } > ../q.msg; Q=$(squash ../q.msg); git checkout -q --detach "$Q"; K=$(optin "$Q"); release "$K"; done
```
Expected, each `publish exit 1` and `origin v1.5.0: absent; local v1.5.0: absent; release create calls: 0`:
- `none`: `` release.py publish: refused: RELEASE_TRANSACTION_REFUSED: the release notes of v1.5.0 are missing: no commit of the release range carries a release-notes block; fix: supply the notes in a later trunk commit, which the publish then releases, whose message carries a release-notes block for each milestone of the range: the merged pull request's block copied verbatim, or one printed by `python3 tools/release.py notes-block --work-item <id> <file>`; or opt out for this release: a trunk commit that removes {release_notes} from release.publication.notes in .workflow-controller/policy.json makes the publish render the fixed text ``;
- `empty`: `` … the release notes of v1.5.0 are unverified: commit <Q>: the block of workflow-controller-settings-and-telemetry is damaged: the block's notes are empty; fix: supply the notes in a later trunk commit … for workflow-controller-settings-and-telemetry: …; or opt out … ``;
- `edited`: `… are unverified: commit <Q>: the notes of workflow-controller-settings-and-telemetry have SHA-256 a897a2b46b0d6dcb97cc56f593f861e8a99dcb3fccb533c6aad0d3a30ebc5eb8, the marker records 680e387106204c05cb8228e6f3e77668022a7539b11d2d4d7bf1582042f6b4ef; fix: …`.

**K3.** The two fixes for missing notes: a later commit carrying a `notes-block`, or the opt-out.
```bash
. /tmp/c3-fr/fr.sh
echo "===== fix"; fresh fix >/dev/null; { printf '%s\n\n' "$TITLE"; cat "$S/body-none.txt"; } > ../q.msg; Q=$(squash ../q.msg); git checkout -q --detach "$Q"; K=$(optin "$Q"); release "$K" | tail -2
{ printf 'docs: supply the 1.5.0 release notes (#18)\n\n'; python3 tools/release.py notes-block --work-item "$WI" "$S/notes.md"; } > ../f.msg
git commit -q --allow-empty --cleanup=verbatim -F ../f.msg; Fx=$(git rev-parse HEAD); echo "F=$Fx"; release "$Fx"
echo "===== opt-out"; fresh optout >/dev/null; { printf '%s\n\n' "$TITLE"; cat "$S/body-none.txt"; } > ../q.msg; Q=$(squash ../q.msg); git checkout -q --detach "$Q"; K=$(optin "$Q"); release "$K" | tail -1
sed -i 's/"notes": "workflow-controller {tag}\\n\\n{release_notes}"/"notes": "workflow-controller {tag}"/' .workflow-controller/policy.json
git commit -qam "chore: opt out of release notes for 1.5.0 (#18)"; O=$(git rev-parse HEAD); release "$O"
git --git-dir="$FAKE_GH_ORIGIN" for-each-ref refs/tags/v1.5.0 --format='%(contents)'
```
Expected: `fix`: the missing refusal (`publish exit 1`, nothing tagged), then
`ok: v1.5.0 at <F>: created (…)`, `ok: release notes: included workflow-controller-settings-and-telemetry (<F>)`,
`publish exit 0`, `origin v1.5.0: tag; …; release create calls: 1`. `opt-out`: nothing tagged
after the first publish, then `ok: v1.5.0 at <O>: created (…)` with no `release notes` line,
`publish exit 0`, and the tag message `workflow-controller v1.5.0`.

**K4.** `RESUME` (a tag with no release) verifies the tag's message:
```bash
. /tmp/c3-fr/fr.sh
mkq() { { printf '%s\n\n' "$TITLE"; cat "$S/body.txt"; } > ../q.msg; Q=$(squash ../q.msg); git checkout -q --detach "$Q"; K=$(optin "$Q"); }
echo "===== the publish's own tag, release lost"; fresh r1 >/dev/null; mkq; release "$K" | tail -2
python3 -c "import json,os; p=os.environ['FAKE_GH_STATE']; s=json.load(open(p)); s['releases']=[r for r in s['releases'] if r['tagName'] not in ('v1.5.0',)]; json.dump(s, open(p,'w'))"
python3 tools/release.py classify --commit "$K" | head -1; release "$K"
echo "===== a hand-made tag with the old fixed text"; fresh r2 >/dev/null; mkq; advance "$K"
git tag -a v1.5.0 -m "workflow-controller v1.5.0" "$K"; git push -q origin v1.5.0; release "$K"
echo "===== a lightweight tag"; fresh r3 >/dev/null; mkq; advance "$K"; git tag v1.5.0 "$K"; git push -q origin v1.5.0; release "$K"
```
Expected:
- `ok: RESUME: v1.5.0 exists at <K> with no release` and `state=RESUME`; `ok: v1.5.0 at <K>: created (…)`,
  `ok: release notes: reused from tag v1.5.0 (included workflow-controller-settings-and-telemetry (<Q>))`,
  `publish exit 0`, `release create calls: 2`;
- hand-made tag: `release.py publish: refused: RELEASE_TRANSACTION_REFUSED: v1.5.0 is an unverified tag: its message is not the release notes recomputed for it under the current template (a tag created before the opt-in, under another template, or from a range whose blocks changed); first difference at line 2: the texts hold 1 and 44 lines; fix: create the release for v1.5.0 by hand, with the right notes`,
  `publish exit 1`, `release create calls: 0`;
- lightweight tag: `… the release notes of v1.5.0 are unverified: v1.5.0 is not an annotated tag, so it was not created by the publish; fix: create the release for v1.5.0 by hand, with the right notes`,
  `publish exit 1`, `origin v1.5.0: commit; …; release create calls: 0`.

**K5.** The concurrent tag: a `pre-push` hook in the work clone makes a rival clone push `v1.5.0` at
the same commit between the publish's remote check and its own push, once.
```bash
. /tmp/c3-fr/fr.sh
for variant in same old; do
  echo "===== rival tag, $variant message"; fresh race-$variant >/dev/null
  { printf '%s\n\n' "$TITLE"; cat "$S/body.txt"; } > ../q.msg; Q=$(squash ../q.msg); git checkout -q --detach "$Q"; K=$(optin "$Q"); advance "$K"
  git clone -q "$FAKE_GH_ORIGIN" ../rival; git -C ../rival config user.name rival; git -C ../rival config user.email rival@example.invalid
  if [ $variant = same ]; then { printf 'workflow-controller v1.5.0\n\n'; cat "$S/notes.md"; } > ../rival.msg; else printf 'workflow-controller v1.5.0\n' > ../rival.msg; fi
  printf '#!/bin/sh\n[ -e ../raced ] && exit 0\ntouch ../raced\ngit -C ../rival tag -a --cleanup=verbatim -F ../rival.msg v1.5.0 %s && git -C ../rival push -q origin v1.5.0\n' "$K" > .git/hooks/pre-push; chmod +x .git/hooks/pre-push
  release "$K"; git --git-dir="$FAKE_GH_ORIGIN" cat-file tag v1.5.0 | grep ^tagger | cut -d' ' -f2
done
```
Expected: `same`: `ok: v1.5.0 at <K>: created (…)`,
`ok: release notes: reused from tag v1.5.0 (included workflow-controller-settings-and-telemetry (<Q>))`,
`publish exit 0`, `release create calls: 1`, tagger `rival` (the winning tag, verified and
kept). `old`: the `unverified tag` refusal of step 4 (`first difference at line 2: the texts hold 1 and 44 lines`),
`publish exit 1`, `release create calls: 0`, tagger `rival`.

**L. The 1.4 patches.**

**L1.** The manual-external gate tells the truth, live on a scratch clone of this repository at
`d32b9ac` (the implementation bundle generation 3 record), with a copy of its bundle and the
Workflow state edited back to `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` with only the local
`APPROVE` recorded:
```bash
. /tmp/c3-fr/fr.sh; G=$S/gate; git clone -q "$S/origin.git" "$G/repo"; cd "$G/repo"
git checkout -q -b milestone/workflow-controller-settings-and-telemetry d32b9acc9579f75ae0cecae9013cbc787ac1720c
mkdir -p ".ai-review/$WI/feedback"; cp -a "$R/.ai-review/$WI/current" ".ai-review/$WI/"
sed -i "s#^worktree_root: .*#worktree_root: $G/repo#" ".ai-review/$WI/current/MANIFEST.md"
ledger() { python3 - "$1" <<'EOF'
import json, sys
p = "docs/ai-workflow/WORKFLOW_STATE.json"; s = json.load(open(p)); w = s["work_items"]["workflow-controller-settings-and-telemetry"]
w["phase"] = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
w["implementation_review_stages"] = {"review_content_id": sys.argv[1], "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {
    "bundle_id": "6cc2ab3b5214bbc63a651bc8053fda4d4e0d66b6f8cad18c92eb737aae33d0e1", "verdict": "APPROVE", "round": 3,
    "completed_at": "2026-10-01T08:01:20Z"}}
json.dump(s, open(p, "w"), indent=2); open(p, "a").write("\n")
EOF
}
ledger 01ee6cdb88885c96760e4b35d23edfade4789d7ce510f70948100cf91f2c32e4; "$W" --runtime-dir "$G/rt" explain "$G/repo" | grep -E "^reason|what is required|safe resume"
ledger 5af5569cd63244183285496c536ca197f6043b332ae1a77b14420c7e8e9c5636; "$W" --runtime-dir "$G/rt" explain "$G/repo" | grep -E "^reason|what is required|safe resume"; echo "exit ${PIPESTATUS[0]}"
workflow-controller --runtime-dir "$G/rt-old" explain "$G/repo" | grep "what is required" | cut -c1-300
```
Expected:
- coherent ledger (today's gate, unchanged): `reason: AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: no current-round REVIEW_FEEDBACK.md on file`,
  `human gate -- what is required: upload .ai-review/workflow-controller-settings-and-telemetry/current (bundle_id 6cc2ab3b…, review_content_id 01ee6cdb… from the ledger) to a manual external reviewer and paste the verdict into .ai-review/workflow-controller-settings-and-telemetry/feedback/REVIEW_FEEDBACK.md, declaring Reviewer role: MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`,
  `safe resume command: /record-manual-implementation-review workflow-controller-settings-and-telemetry`;
- the ledger's id from round 1: `reason: AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: manual_external_ledger_incoherent -- the ledger's review_content_id differs from the manifest's`,
  `human gate -- what is required: do not send .ai-review/workflow-controller-settings-and-telemetry/current for external review: the implementation_review_stages ledger is not coherent with the bundle -- the ledger's review_content_id differs from the manifest's (the ledger records '5af5569c…', MANIFEST.md states '01ee6cdb…'; …). No Workflow command moves AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW back (its only exits are /record-manual-implementation-review's verdicts), so the Workflow state needs explicit user resolution before any external review.`,
  `safe resume command: explicit user resolution of workflow-controller-settings-and-telemetry's Workflow state before any external review`,
  `exit 0`. No "upload", no "from the ledger";
- the installed 1.4.2 on the same state still offers the bundle with the wrong id:
  `human gate -- what is required: upload .ai-review/workflow-controller-settings-and-telemetry/current (bundle_id 6cc2ab3b…, review_content_id 5af5569c… from the ledger) to a manual external r`.

**L2.** `status` with no runtime state, as JSON:
```bash
. /tmp/c3-fr/fr.sh; "$W" --runtime-dir "$S/rt-empty" --json status
```
Expected: `{"controller": "workflow-controller 1.4.2 -- package (local build from 268355881fb7)", "ladder_row": 1, "runtime_root": "/tmp/c3-fr/rt-empty", "runtime_state": false}`.
The populated `status` (text and `--json`) is flow G; the printed resume hint with
`--runtime-dir` that parses is flow F.

**M. The automated evidence**, in `$S/src` under the reaping wrapper:

```bash
. /tmp/c3-fr/fr.sh; cd "$S/src"
python3 "$S/reap.py" python3 -m unittest tests.test_settings tests.test_routing tests.test_cli.SettingsWiringTest 2>&1 | tail -3
python3 "$S/reap.py" python3 -m unittest tests.test_worker_stream.SessionTelemetryTest tests.test_job.TelemetryCompletionTest tests.test_job.ReattachTelemetryTest tests.test_telemetry 2>&1 | tail -3
python3 "$S/reap.py" python3 -m unittest tests.test_release_notes tests.test_repo_policy.ReleaseNotesPolicyTest tests.test_pull_request_lifecycle.SquashReleaseNotesTest tests.test_release_txn.ReleaseRangeTest tests.test_release_txn.ReleaseNotesTransactionTest 2>&1 | tail -3
python3 "$S/reap.py" python3 -m unittest tests.test_hints_parse tests.test_evidence.ManualExternalPlanLedgerIncoherentTest tests.test_evidence.ManualExternalImplementationLedgerIncoherentTest tests.test_job.LastLaunchedApplyJobViewTest tests.test_job.ApplyingReviewFeedbackExecuteTest tests.test_lifecycle_orchestration.AbandonedApplyRelaunchBoundTest tests.test_observe.StatusJobSummaryTest tests.test_cli.StatusJobsTest tests.test_cli.StatusActiveSectionTest 2>&1 | tail -3
```
Expected: `Ran 85 tests` OK (settings: the table and compatibility rule, location, every refusal,
the fill, the two-release and shared-routing tests, the locks, the I5 isolation guard,
`ProcessDefaultsTest` for the leaf values); `Ran 30 tests` OK (telemetry: totals, the failure
boundary on both completion paths, derivation, the command); `Ran 66 tests` OK (release notes:
the line rules, the per-paragraph trailer parse under hostile Git configuration, readiness's
included/absent/empty/refused bodies with no edit, every publish case); `Ran 47 tests` OK (every
printed hint parses with the live parser, both incoherent-ledger stages and their way-out texts,
the relaunch-bound records including the end-to-end abandoned apply, `status`). The full suite
is PR #16's CI (flow O).

**N. It releases 1.5.0** (the classification over a scratch origin, as for 1.4.1 and 1.4.2):

```bash
. /tmp/c3-fr/fr.sh; fresh classify >/dev/null
python3 tools/release.py check-title "feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches"
Q=$(git commit-tree "$H^{tree}" -p origin/main -m "$TITLE"); advance "$Q"
python3 tools/release.py version; python3 tools/release.py classify --commit "$Q"; echo "exit $?"
```
Expected: `ok: feat → minor`; `1.4.2` (the highest reachable tag); then
`ok: RELEASE_DUE: 1.5.0 has no tag and no release`, `state=RELEASE_DUE`, `version=1.5.0`,
`tag=v1.5.0`, `commit=<Q>`, `exit 0`. This repository's policy does not opt in, so the real
release publishes the fixed text `workflow-controller v1.5.0` (flow K3's opt-out case shows that
text).

**O. PR #16.** `gh pr checks 16` (read-only). Expected: every check passes: `PR title`,
`validate / plan`, `validate / package`, `validate / tests (0)` to `(5)`, `validate / tests-result`
and `workflow-conformance`. The title is the plan's declared
`feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches`.

**P. The documentation agrees with B-N.** Read each and check it against the flows:
- `docs/guide/runtime.md`, "The settings file": the location order (B1), the table and its bounds
  (B3), "Which value wins" and the exit-2 flags (E), the fill and the forward-only rule (B4, C),
  the lock, "When the file is refused" (B3, E), unknown keys and `settings clean` (B5, C), the
  routing section (B5, D);
- `docs/guide/commands.md`: `status` (G, L2), `settings` (B), `telemetry` (G, I), "Worker routing"
  (D), the global `--settings`;
- `docs/guide/workers.md`: "The drain bound" (F), "Telemetry" (G, H, I);
- `docs/guide/milestone-branches.md`, "Release notes in the pull request body", and
  `docs/guide/ci-and-releases.md`, "Release notes from the milestones" (J, K: included or refused,
  the two fixes, the opt-out, `RESUME`, the race, `--cleanup=verbatim`), and its cutover paragraph;
- `docs/guide/troubleshooting.md`: `SettingsError` and the warnings (B), the incoherent
  manual-external gate (L1);
- `docs/adr/0008-controller-settings-file.md` (B, C, D); `docs/README.md`'s map lists ADR 0008;
- the `## Release notes` section below (the 1.5.0 notes): each claim matches a flow.

### Known limitations and out of scope

- No live Controller run on a real repository is part of this round; the flows drive the local
  build against disposable targets with the fake `claude` and the stub Workflow Manager.
- Readiness's pull-request edit with the notes block is shown by `SquashReleaseNotesTest` (fake
  forge) and by rendering the body (J), not on a live pull request. This repository does not opt
  in until the post-1.5.0 cutover pull request, so PR #16's body carries no block, and the 1.5.0
  release will publish the fixed text; 1.5.0's notes are copied into `docs/releases/` by hand.
- The leaf timeouts, the pull-request list limit and the follow heartbeat are verified by tests,
  not by a shell flow (E).
- `last job telemetry:` prints a job with no work item as `None` (`(FAILED, None)`), while the
  `jobs:` lines say `work item none`.
- `status` on a runtime root with no state writes `identity.json` there, as 1.4.2 does, although
  `commands.md` says it writes nothing there. This predates the milestone.
- The resume error text printed by a drain detach (F) names `workflow-controller resume <repo>`
  without `--runtime-dir`, by design (CP5 kept the error texts); the activity hint carries it.
- A Git identity kept only in `$XDG_CONFIG_HOME/git/config` is not seen by the test suite, which
  redirects `XDG_CONFIG_HOME` (the release notes' operator note).
- `tests/golden/generate_plan_stage_decisions.py --check` (without `--release`) reports its
  `AMENDING_PLAN` cases differ in this environment, as at the base `a47e695`;
  `tests.test_golden_plan_stage_decisions` passes.
- Settings v1 has no changed default, so the forward-only migration is shown with a patched build
  (C), as the tests do with patched tables.

### After testing

Findings go in `.ai-review/workflow-controller-settings-and-telemetry/feedback/FUNCTIONAL_REVIEW.md`.
`/apply-functional-review` routes each one: a bounded fix in this work item, or a
`workflow-controller-settings-and-telemetry-remediation-<n>` child for new or wider scope. When
testing is clean, `/accept-milestone` is the only acceptance command; every checkpoint is already
`COMPLETE`.

## Release notes

### Settings, telemetry and release notes from the milestone (1.5.0)

**A settings file.** Every operational tunable is now a setting in one
user-level JSON file, shared by every repository and lane on the
machine: the drain detach bound, the worker timeout, the run's step
limit, the follower's heartbeat and replay count, the Git,
release-command and Workflow-query timeouts, the pull-request list
limit, and the routing defaults. The file is found through `--settings`,
`$WORKFLOW_CONTROLLER_SETTINGS`, `$XDG_CONFIG_HOME` or `~/.config`, in
that order. A command-line flag beats the file, and the file beats the
built-in default. `step`, `run`, `resume` and `milestone-binding` fill
in missing settings, and move a value the operator never changed to a
newer default, forward only. Unknown keys are ignored with a warning. An
invalid file is refused with exit 20 and is never rewritten. New
commands: `settings show|path|clean` and `resume --drain-timeout`.
`--routing-config` still works, and replaces the file's routing section.

**Telemetry v0.** Each finished job records its session totals over
every result: turns, tokens, cost, API time and per-model usage, with
the job's and the worker's wall times. A telemetry failure never changes
a job's outcome. The read-only `telemetry` command summarises jobs by
work item, run, date, role or model, and derives older jobs from their
worker streams. `status`, `inspect` and `follow` print the figures.

**Release notes follow the milestone.** A repository can opt in through
its policy (`milestone_branches.pull_request.release_notes` and the
`{release_notes}` placeholder). Readiness then puts the milestone's
notes section into the pull request body, bound by a marker and a
digest, and the release publishes the verified notes of its range from
the squash commits, or refuses and names the fix. Notes lines are
limited to 72 bytes. `tools/release.py notes-block` supplies notes by
hand. This repository has not opted in yet.

**Smaller fixes.** Every printed `resume` and `explain` hint now parses,
and the resume hint carries `--runtime-dir`. A manual-external review
gate whose ledger does not match the bundle now says so, instead of
offering the bundle. `status` shows the job count, the ten newest jobs
and the active runs' and jobs' start times, and honours `--json`.

**Operator note.** Tags are now created with `--cleanup=verbatim`; their
messages are unchanged. The tests redirect `XDG_CONFIG_HOME`, so a Git
identity kept only in `$XDG_CONFIG_HOME/git/config` is not seen by them.
