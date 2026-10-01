# Active Milestone

## Status

**Implementing.** `workflow-controller-settings-and-telemetry` (`docs/ROADMAP.md` step C3, sections
1.4, 8 and 11.1.2). The plan is `docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md`,
revision 14, approved at `b0e2f9a` (`EXTERNAL_APPROVE`, review content id `db313bbe`). The base
commit is `a47e695`. Governing workflow version `2.2`, lifecycle authority Workflow 2.6.0. Pull
request title
`feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches`
(1.5.0).

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-ci-reliability.md`.

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
