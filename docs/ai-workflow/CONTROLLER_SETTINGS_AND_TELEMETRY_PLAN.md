# Controller settings file v1, telemetry v0, release notes from the milestone, and the 1.4 cleanup patches (Revision 14)

Work item: `workflow-controller-settings-and-telemetry`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `a47e6955cd4aebdee5482ba7cc2c788c2eb057c3` ("docs: 1.4.2 release notes (#15)"), the
tip of `main` when this plan was written, passed explicitly as `/milestone-plan a47e695…` (after a
squash merge the next item is planned from `main`'s head with the base passed explicitly,
`docs/guide/milestone-branches.md`). The previous milestone (`workflow-controller-ci-reliability`)
was accepted at `9ed62ef`, squash-merged by PR #13 (`0de0fd5`) and released as 1.4.2. PR #15
added the 1.4.2 notes. Neither is this milestone's work.
Lifecycle authority: this repository's installed Workflow **2.6.0**.
Driving Controller: the installed **1.4.1** package (`workflow-controller --version`: `package
(release v1.4.1; built from e8cd8f9b944e)`). 1.4.2 goes into the shared install only between
Workflow Manager milestones (shared lane plan). Nothing here depends on which of the two drives
this milestone, with one exception: the driving Controller parses this branch's committed
`.workflow-controller/policy.json`, so this milestone does **not** change that file (Design D, I9).
Roadmap slot: `docs/ROADMAP.md` step **C3** of "At a glance": sections **1.4** (the four open
patches and the settings file), **8** (telemetry v0) and **11.1.2** (release notes follow the
milestone).
Released baseline preserved: `workflow-controller 1.4.2` (`v1.4.2`). This milestone ships as the
minor release **1.5.0**, derived by `main.yml` from the `feat:` pull request title below.
Pull request title: `feat: a settings file, telemetry v0, release notes from the milestone and the 1.4 cleanup patches`

## Goal

Three later steps need things that do not exist yet:
- C4 (auto-merge) and C5 (notifications) need somewhere to put their switches;
- C8 (usage budget) needs the cost of past jobs per role and model;
- every release since 1.4.0 has needed its notes added to `docs/releases/` by hand.

This milestone delivers four things.

1. **Settings file v1 (1.4).** One user-level JSON file holds every operational tunable and the
   routing defaults. The Controller fills in each missing setting with its default, so a new
   setting migrates itself. It records which defaults it wrote, and at which default generation,
   so a later release can move an untouched value to a newer default and an older release never
   moves it back. It warns about unknown keys, and a `settings clean` command removes them, but
   never the keys of a newer release sharing the file. The drain detach bound (10800 s) becomes a setting, and `resume` gains
   `--drain-timeout`. Each job records the effective values it ran with.
2. **Telemetry v0 (8).** Each job records the session's totals:
   - tokens: input, output, cache creation and cache read;
   - cost and API time;
   - wall time: the job, and the worker from spawn to exit;
   - turns;
   - a per-model breakdown.

   All of them are computed from every `result` event of the session, not only the last one. A
   read-only `telemetry` command summarises jobs by role and model. It can also derive the same
   figures for jobs recorded before this release, from their persisted `worker.stdout`.
3. **Release notes follow the milestone (11.1.2).**
   - At readiness, the milestone's `## Release notes` section becomes the pull request body,
     above the Controller's own lines. The squash commit carries it.
   - Readiness binds the notes it validated to the milestone: the body's notes marker carries
     the work-item id and the SHA-256 of the section's bytes.
   - **The release text comes only from the squash commit body** (the user's decision,
     2026-09-30, Decision 11). The release reads the notes block that readiness wrote into the
     pull request body from the message of each commit of the release range that carries one,
     and publishes it only when its digest matches. It reads no milestone narrative and no
     file of any tree.
   - An opted-in release whose notes are missing (no commit of the range carries a notes
     block, or the only block holds empty notes) or cannot be read or verified is refused
     before anything is created, and the refusal names the fix: the operator supplies the
     notes in a later trunk commit's message, or, for the two refusals no block can clear,
     drops the placeholder for that release. A resumed release reuses its tag's message only
     after recomputing it from the tag's range and the current template. No release publishes
     silently empty notes.
   - Readiness refuses a body with a paragraph that parses as a Git trailer block, at run time
     and not only in a test (I8 of the C1 plan, checked paragraph by paragraph). It also
     refuses a notes line that GitHub could rewrap in the squash commit (over 72 bytes of
     UTF-8, or holding a tab or a carriage return), so the commit carries the notes byte for
     byte.
   - A milestone with no notes section keeps today's body. In an opted-in repository its
     release then refuses until the operator supplies notes.
   - All of this is opt-in through the repository policy. This repository opts in with a
     follow-up cutover pull request once 1.5.0 is installed (Decision 4).
4. **The four open 1.4 patches.**
   - The `--work-item` resume hints that do not parse are corrected.
   - The manual-external review gate stops advertising a stale or missing `review_content_id`.
   - Explicit relaunch-bound tests cover `OperatorAbandoned` and `UnreconcilableJobError`.
   - `status` presents active and recent jobs usefully and supports `--json`.

## Non-goals

- **No per-repository settings layer.** v1 has one user-level file. It is shared by every
  repository and every lane on this machine, and a repository-local layer is Decision 2's
  alternative. The Controller never writes a file inside a target repository's tracked tree: a
  tracked-tree change breaks trunk start and the worker invariants.
- **No harness-contract or safety constant becomes a setting** (I3). These stay fixed:
  - the wakeup grace and command-lifecycle grace (300 s) and the settle bound (305 s);
  - the skew (5 s) and the wakeup range (60 to 3600 s);
  - the anchor orphan and poll bounds;
  - the recognised-daemon list;
  - the supervisor poll intervals;
  - exit codes and schema versions.
- **No hot reload.** Settings are read once per invocation. A `run` re-reads nothing between
  steps; section 8's hot reload stays deferred.
- **No auto-merge, review or notification switches.** C4 and C5 add their own settings on top of
  v1's mechanism.
- **No usage forecasting and no limit tracking.** That is C8. Telemetry v0 records the data and
  summarises it.
- **No change to `.workflow-controller/policy.json`, `pyproject.toml`, `setup.py` or any
  `.github/workflows/` file** (I9).
- **No telemetry export and no telemetry store other than the job records.** No second
  authoritative file exists that could disagree with the records.
- **No milestone-narrative scanning at release** (the user's decision, 2026-09-30, Decision
  11). Revisions 2 to 11 grew one, and it is deferred; it can return as a later roadmap item.
  The release does not:
  - collect the narratives the release range added;
  - detect or recover a squash commit that lost the pull request body;
  - detect documentation backfills, or read backfill declarations;
  - match paths or headings against any version of the release policy;
  - handle non-ASCII narrative paths for any of these.

  What this costs: a milestone whose squash commit lost the pull request body is caught only
  when no commit of the range carries a notes block, because the release then refuses as
  missing. In a range where another milestone's block is present, the release publishes that
  block without the lost milestone's notes. The guide states this, and that the squash
  message must carry the pull request body (CP7).

## Investigation: what happens today (measured at `a47e695`)

### Tunables

The operational constants are fixed in code today.

| Constant | Value | Where | Override today |
|---|---|---|---|
| `DRAIN_DETACH_SECONDS` | 10800 | `controller/worker.py:425`; used at `:1789` and in the resume re-attach drain (`controller/job.py:3556,3685`) | none. The comment says "interim constant", ROADMAP 1.4 |
| `DEFAULT_WORKER_TIMEOUT` | none | `controller/job.py:219`, `:4899` | global `--timeout` (step and run only) |
| `run --max-steps` | 20 | `controller/cli.py:285` | the flag |
| `observe.HEARTBEAT_SECONDS` | 30 | `controller/observe.py:53` | none |
| `observe.REPLAY_EVENTS` | 20 | `controller/observe.py:57` | `follow --from-start` replays all |
| `gitrepo.DEFAULT_TIMEOUT_SECONDS` | 600 | `controller/gitrepo.py:35`; the forge's `gh` calls share it | a `timeout=` keyword only |
| `release_txn.COMMAND_TIMEOUT_SECONDS` | 1800 | `controller/release_txn.py:73` | none |
| `workflow_contract.QUERY_TIMEOUT_SECONDS` | 120 | `controller/workflow_contract.py:92` | a keyword only |
| `forge.PR_LIST_LIMIT` | 200 | `controller/forge.py:43`. A full page is undecidable (`:245-248`) | none |

Routing comes only from the global `--routing-config PATH` (`controller/cli.py:263`, parsed by
`controller/routing.py` `load_routing_config`), plus the `--model`, `--effort`, `--role-model` and
`--role-effort` flags. The precedence is role-cli, cli, config-role, config-default, then the
built-in `ROLE_ROUTES` (`routing.py:9-18`). `parse_routing_config` refuses an unknown top-level
key, an unknown role name (`routing.py:299-302`) and an unknown route field (`:258-262`), with
`RoutingConfigError`. The route *values* are free strings (`check_value`, `:208`): their
vocabulary is the `claude` CLI's. Neither `controller/` nor its tests read
`XDG_CONFIG_HOME` today. The only XDG use is the runtime root under `XDG_STATE_HOME`
(`controller/runtime.py:50-86`).

`tests/test_write_containment.py` accepts a file write under `controller/` only in `runtime.py`,
through `runtime.write_json`/`write_bytes`, or after an `assert_contained` in the same function.

### Telemetry

- **The argv.** `controller/worker.py:469-492` runs the worker as
  `claude -p --input-format stream-json --output-format stream-json --verbose …`. Its stdout is
  persisted verbatim at `<runtime>/jobs/<job_id>/worker.stdout`.
- **What the parser reads.** `controller/worker_stream.py` `_result` (`:485-490`) keeps every
  `result` event in `stream.results`, and the classifier takes only the **last** one
  (`worker_stream.py:707`). The fields kept from it are listed at `worker.py:184-192`: its
  `num_turns`, `total_cost_usd` and `duration_ms`. It never reads `usage`, `modelUsage` or
  `duration_api_ms`.
- **What the record carries.** The job record's `worker` block (`job.py:4059-4080`, written at
  `:5000-5009` and on the re-attach path `:3655-3664`) carries those three last-result values.

**How the figures behave across several results.** Measured on the live runtime root
(`~/.local/state/workflow-controller`, 852 jobs); in the last 40 streams, 3 had more than one
result.

| Field | Across the results of one session |
|---|---|
| `total_cost_usd`, `duration_api_ms`, `modelUsage.*` | cumulative, with one measured exception below |
| `usage.*`, `num_turns`, `duration_ms` | this turn only |

**The exception (review round 1, R1-001).** A result produced by a subagent handback
(`origin.kind: "peer"`) can repeat the previous result's cumulative figures unchanged although it
carries its own non-zero `usage` and `num_turns`. `job_2857a730_two_results.jsonl`'s second result
(`result_index: 1`, `num_turns` 8, `usage.output_tokens` 10729) repeats the first result's
`total_cost_usd` 24.2492376, `duration_api_ms` 2719274 and
`modelUsage["claude-opus-5-5"].outputTokens` 326896. 3 of the 48 multi-result streams in the live
runtime root do the same, each on a `peer` result: `20260924T211904Z-73845184`,
`20260924T221915Z-2857a730` and `20260924T233103Z-bad842f1`. So the cumulative figures never
decrease, but they do not always advance, and nothing guarantees the last result carries the
largest value. I7 takes the maximum.

For example, job `20260930T121740Z-2e7e7496` had four results:
- cost 8.05 → 9.80 → 10.76 → 11.66;
- `num_turns` 107 / 18 / 11 / 8;
- `duration_ms` 999k / 149k / 71k / 102k.

Its record says 8 turns and 102 s. The session was 144 turns and about 1.32 M ms. Only the
record's cost happens to be right. `modelUsage` can exceed the summed `usage` even with one
result (output tokens 10104 against 7894), because subagent usage is counted there
(`subagent_stats`).

The captured contract fixtures in `tests/harness_contract/` carry full `usage`, `modelUsage`
and `duration_api_ms`, and `job_2857a730_two_results.jsonl` has two results. `tests/fake_claude.py`
`result_event(**overrides)` and the `FAKE_CLAUDE_TURNS` `end_turn` step inject result fields.

### Release notes

- **The body.** In squash mode the pull request body is `squash_body`
  (`controller/milestone_branch.py:1286-1295`): one milestone line, the "Accepted at" line at
  readiness, and `PR_MARKER`. `_sync_for_readiness` (`:1310-1347`) overwrites any other body
  with it.
- **The trailer rule.** I8 of the C1 plan (no body line parses as a Git trailer) is enforced
  only by tests (`tests/test_pull_request_lifecycle.py:1256`,
  `tests/test_integration_disposable_repo.py:1597`).
- **The release job's checkout.** `main.yml`'s publish job checks out with `fetch-depth: 0`
  (`.github/workflows/main.yml:114-116`), so every commit of the release range, with its
  message, is available locally to `release_txn`. Its token is `permissions: contents: write` (`:108-111`),
  which leaves `pull-requests` at `none`.
- **The release.** `release_txn.publish` (`controller/release_txn.py:683,719-722`) renders the
  policy's `release.publication.notes` (placeholders `{version}`, `{tag}`, `{commit}`,
  `controller/repo_policy.py:205`) as the annotated tag message and the GitHub release notes.
  The tag is created with `git tag -a -m <message>` (`controller/gitrepo.py:569-579`), whose
  default cleanup mode `strip` removes every line that starts with `#` and trailing whitespace
  (measured with Git 2.55: a message holding `### item-a` and `# h1` lines is stored without
  them; `--cleanup=verbatim` stores it byte for byte). Today's one-line template is unaffected,
  but a Markdown heading in the notes would be dropped from the tag and kept in the release.
  This repository's policy gives `"workflow-controller {tag}"`. `main.yml` runs
  `tools/release.py publish`, which calls `release_txn`.
- **No Controller code reads the milestone narrative today.** The notes were written into its
  "Pull request body" section (for example `docs/milestones/completed/workflow-controller-ci-reliability.md:435`)
  and then copied into `docs/releases/` by hand.
- **GitHub wraps long lines of the squash commit body, and only those.** The body of `0de0fd5`
  has the Controller's lines wrapped at 72 columns. `PR_MARKER` is split across two lines, and
  GitHub appends a `---------` rule and the `Co-authored-by:` trailers. Revision 12 measured
  what the wrap does, against `squash_body` (`controller/milestone_branch.py:1286-1295`), the
  text GitHub was given:
  - it breaks a line longer than 72 characters greedily, at spaces only. `squash_body`'s
    114-character first line became lines of 58, 63 and 20 characters in `0de0fd5`, and
    `PR_MARKER` was split before its closing `-->` (`0de0fd5`), or after its
    `workflow-controller:` token (`e8cd8f9`), never inside a token;
  - it keeps every existing line break, and it never joins lines. The 20-character remainder
    `workflow-controller.` was not joined to the next line, `Accepted at …`, although the two
    fit in 72;
  - no line of the last six first-parent commit bodies on `main` (`a47e695`, `0de0fd5`,
    `93b82de`, `e8cd8f9`, `6a24f64`, `ba3a615`) is longer than 72 characters, and lines of
    exactly 72 are kept;
  - a greedy wrap at 72 columns, at spaces, of each line of `squash_body`'s output for those
    two milestones reproduces the body of `0de0fd5` and of `e8cd8f9` byte for byte, up to
    GitHub's appended `---------` rule.

  So a notes block whose every line holds at most 72 characters is carried byte for byte, and
  a whitespace-free token, such as a work-item id or a 64-digit hexadecimal digest, survives
  the wrap. A marker line longer than 72 is found again by treating every run of whitespace as
  one space (review round 4, R4-002). GitHub documents none of this, so the release checks
  the digest and refuses on a mismatch (D.3).
- **Policy parsing.** The policy is parsed strictly (`repo_policy.parse_policy`, `:470`), and
  unknown keys and placeholders are refused. The driving Controller reads the milestone branch's
  committed policy at every branch step (`milestone_branch.py:583,1067,1250,1518`).

### The four 1.4 patches

They were recorded as follow-ups in ROADMAP 0 ("Known follow-ups carried forward", `:98-109`)
and in the automatic-lifecycle-orchestration acceptance
(`docs/milestones/completed/workflow-controller-automatic-lifecycle-orchestration.md:1463-1481`).

1. **Hints that do not parse.** `--work-item` is a global option (`cli.py:245`), and `explain`
   takes only `repo` (`:266-267`). So `workflow-controller explain --work-item X` exits 2. It is
   still emitted at:
   - `controller/decision.py:850`;
   - `controller/evidence.py:1527-1528` (`_explain_command`, used at `:1734,2731,2957,3068,3245`);
   - `controller/evidence.py:1969`.

   The correct, quoted form already exists as `evidence._explain_gate_command` (`:2245-2254`). The
   `status`/`inspect` resume hint (`observe.py:967`, `job.py:3071`) drops `--runtime-dir`,
   which the `follow` hint includes.
2. **Manual-external gate.** `evidence._decide_awaiting_manual_external_implementation_review`
   (`:1797-1900`) prints `review_content_id {ledger.review_content_id} from the ledger` in the
   "no feedback" branch (`:1829-1850`). It never checks that the ledger is well formed, matches
   the bundle manifest's `review_content_id`, and holds a local-stage `APPROVE` for that id.
   Those checks run only after a verdict is pasted
   (`evaluate_manual_implementation_stage_admissibility`, `:1153-1190`). So after a failed
   local-review postcondition, the operator is told to send the bundle with a stale or `None`
   id, and learns otherwise only after the external review. The plan-stage gate
   (`_decide_awaiting_manual_external_plan_review`, `:1367-1396`) has the same shape.
3. **Relaunch-bound tests.** `evidence.relaunch_bound_applies` (`:3146-3158`) takes the last
   terminal apply job J (`job._launched_apply_job_view`, `job.py:2931-3015`). J binds when it is
   not `FINISHED` and its `pre_bundle_manifest_bundle_id` equals the manifest's. `resume
   --abandon` writes `FAILED`/`OperatorAbandoned` (`job.py:3924,3960`). An unreconcilable job is
   `FAILED`/`UnreconcilableJobError` (`:2660`). Both should bind. No test says so, and none
   covers an abandoned record that never reached `LAUNCHED`.
4. **`status`.**
   - `cmd_status` (`cli.py:327-369`) prints every job id ever recorded on one line
     (`:350-354`), with no status.
   - An active job's line (`:372-403`) has no work item, command or start time.
   - `status` ignores the global `--json`, which `inspect`, `explain` and `resume` honour.

## Invariants

- **I1. Precedence.** A setting's effective value is the first present of: its CLI flag, the
  settings file, the built-in default. "Present" means given on the command line: every CLI flag
  that overrides a setting is declared with `default=None` (today `run --max-steps` is declared
  with `default=20`, `controller/cli.py:285`, and would otherwise always win). The CLI flags are
  not checked against the table's bounds (the suite passes `--timeout 30`,
  `tests/test_cli.py:2356`). **One behaviour change** (review round 2, R2-007): today
  `--timeout` (`type=int, default=None`, `controller/cli.py:249`) and `--max-steps` (`type=int`,
  `:285`) accept `0` and negative numbers. From this release `--timeout`, `--max-steps` and the
  new `--drain-timeout` must be positive integers, refused by argparse with exit 2 like any bad
  flag. CP2 tests `--timeout 0`, `--max-steps 0` and `--drain-timeout 0`.
  The table's bounds apply to the file's values. `--routing-config`, when given, replaces the settings
  file's `routing` section as a whole. Inside the resolved routing, the existing
  role-cli > cli > config-role > config-default > built-in order is unchanged.
- **I2. Fail closed on a bad file, never on a missing one.** An invalid settings file is refused,
  by every command, with exit 20 (`SettingsError`, the usage/configuration class `RoutingConfigError` uses), before
  any job record is written, and it is not rewritten. Invalid means:
  - unparseable, or with duplicate keys;
  - a known key with the wrong type or out of its bounds;
  - a `schema_version` other than 1;
  - a malformed bookkeeping field (review round 2, R2-005): `_table_generation` that is not a
    positive integer, `_defaults_written` that is not an object, an entry of it that is not
    `{value, generation}` with a positive integer `generation`, or an entry for a key absent
    from the file.

  A missing file is created with the defaults by the first writing command (A.4). When the fill
  cannot write (an unwritable directory or file), it prints one warning on stderr and skips only
  the fill: the values of an existing valid file still apply, and only the missing keys take their
  built-in defaults.
- **I3. Only operational values are settings.** The closed table in Design A lists them, each
  with a type, a default and bounds. The constants the Non-goals list are not in it, and a test
  pins the table.
- **I4. The fill is additive, atomic, serialised and forward-only.** The Controller only adds
  missing keys and moves untouched values to a newer default (A.4). It never changes a value the
  operator set, never moves a value to an older release's default, and never removes a key except
  through `settings clean`, which never removes a key of a newer release (A.5). The whole
  read-modify-write, of the fill and of `clean`, runs under an exclusive lock on a sibling lock
  file: the file is re-read under the lock, and every comparison uses those bytes. It writes
  through `controller/runtime.py` (a temporary file, fsync, rename) and only when the content
  changes. So two Controllers filling at once, or a fill racing a `clean`, leave one well-formed
  file that loses neither's change. Readers take no lock; the rename makes each read see one
  whole version.
- **I5. The tests never touch the operator's file.** Every test process runs with
  `XDG_CONFIG_HOME` pointing at a temporary directory and with `WORKFLOW_CONTROLLER_SETTINGS`
  removed. Both are done in `tests/__init__.py` (empty today) at package import (review round 2,
  R2-006): that covers `tools/run_tests.py` and a direct `python3 -m unittest tests.…` run alike,
  and every subprocess environment built from `os.environ` (`tests/fixtures`,
  `test_packaged_runtime._base_env`) inherits it. `tools/run_tests.py` is not changed for it. A guard test fails if the real resolved path is read or written. It also sets
  `WORKFLOW_CONTROLLER_SETTINGS` to a sentinel path in the parent environment of a test run, and
  proves the sentinel is never read or created.
- **I6. Telemetry never steers the lifecycle.** A missing, malformed or partial `usage` or
  `modelUsage` produces `null` figures and a `problems` list. It never changes a job's
  status, outcome or reconciliation. The same holds when telemetry itself fails (review round
  4, R4-004): an exception raised while reading the stream, computing the figures or building
  the block never prevents, delays or changes the completion write. That write carries the
  status, outcome and reconciliation it would carry without telemetry, and a telemetry block
  holding only the failure (Design C, "The failure boundary").
- **I7. The telemetry figures are session totals.**
  - Tokens are summed over every `result`'s `usage`, and turns and durations over every result.
  - Cost, API time and each per-model figure are the **maximum** of that cumulative figure over
    all results. It equals the last result's value whenever the figures advance, and it never
    undercounts when a later result repeats or lowers them (the measured `peer` handback,
    Investigation).
  - A result with non-zero `usage` whose cost, API time and per-model figures all equal the
    previous result's adds a `cumulative_not_advanced` problem entry naming its `result_index`.
    It is reported, not corrected: the per-model figures may then undercount that result's own
    usage, and the summed `tokens` still include it.
  - The existing `worker` block keeps its last-result meaning unchanged.
- **I8. The release-notes body never breaks the squash commit.**
  - **Paragraph by paragraph.** Every paragraph of the rendered body (the notes block's
    paragraphs and the Controller's own) is parsed on its own by `git interpret-trailers
    --parse`, fed as a blank line followed by the paragraph, and each must print nothing. Git
    parses only a message's last paragraph, and a message's first paragraph is its subject, so
    one parse of the whole body would never reach the notes (it always ends with `PR_MARKER`),
    and a paragraph fed alone would be read as a subject. Both were measured: a body whose notes
    hold `Fixes: the thing` and `Breaking-Change: none` above the Controller's lines parses to
    nothing as a whole; the same two lines after a blank line parse as two trailers.
  - The paragraph check is stricter than the squash commit needs, since GitHub appends its own
    trailer paragraph. It is kept because the notes are also the release text and because a
    trailer-like paragraph would become the commit's trailer block if the Controller's lines ever
    moved. This is the C1 plan's "no line may parse as a Git trailer"
    (`CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md:584`) stated as Git actually applies it.
  - The body must fit GitHub's limit.
  - **The notes survive the squash commit byte for byte** (revision 12, Decision 11). The
    release reads the notes from the squash commit body (D.3), so every notes line must hold
    at most 72 bytes of UTF-8, which GitHub's wrap leaves alone however it counts a line's
    length (Investigation; review round 12, LPR12-O1), must not end in a space, and must not
    contain a tab or a carriage return. The notes must not contain the text `<!-- workflow-controller:`, so the
    only Controller markers in the body are the ones readiness writes.

  Otherwise readiness refuses with a gate that names the offending paragraph's or line's first
  characters, and edits nothing.
- **I9. This branch stays drivable by 1.4.1 and 1.4.2.** No committed file the driving Controller
  parses changes: `.workflow-controller/policy.json`, the plan's declared title format and the
  Workflow files. The new policy keys and placeholder are optional, and absent here until the
  cutover.
- **I10. Every hint parses.** Every command line the Controller prints for an operator to run
  parses under the live parser. A test extracts them from the gate builders and runs each through
  `build_parser().parse_args`.

## Design

### A. The settings file (CP1)

**A.1 Location.** The first of these that is set:
1. the global `--settings PATH` option, made absolute before the re-exec (`cli._reexec`);
2. `$WORKFLOW_CONTROLLER_SETTINGS`;
3. `$XDG_CONFIG_HOME/workflow-controller/settings.json`;
4. `~/.config/workflow-controller/settings.json`.

**A.2 Format.** JSON. `write_json`'s canonical form: sorted keys, two-space indent, trailing
newline.

```json
{
  "schema_version": 1,
  "worker": {"drain_detach_seconds": 10800, "timeout_seconds": null},
  "run": {"max_steps": 20},
  "follow": {"heartbeat_seconds": 30, "replay_events": 20},
  "timeouts": {"git_seconds": 600, "release_command_seconds": 1800, "workflow_query_seconds": 120},
  "forge": {"pr_list_limit": 200},
  "routing": {"default": {}, "roles": {}},
  "_defaults_written": {"worker.drain_detach_seconds": {"value": 10800, "generation": 1}, "...": "..."},
  "_table_generation": 1
}
```

**A.3 The table.** A new module `controller/settings.py` holds one closed table of `Setting(key,
type, default, minimum, maximum, cli_flag, generation)`, and a module constant
`TABLE_GENERATION`.
- A setting's `generation` is the integer of the release that last set its default: 1 for every
  v1 row. A later release that changes a default raises that row's `generation`.
- `TABLE_GENERATION` is the highest `generation` in the table, raised as well by any release that
  adds or retires a key. It only ever increases from release to release, and a test pins it
  together with the table.
- **The compatibility rule** (review round 2, R2-005). A release never moves a default outside
  the bounds that any earlier generation gave the key, and never changes a key's type. A change
  of type or of range is made by retiring the key and adding a new one. Without the rule, release
  N+1 could move an untouched value to a default that release N refuses (I2, exit 20), so N,
  possibly the driving Controller, would stop reading a file the operator never edited. The table
  test's docstring states the rule, and the test keeps, for each key, the bounds and type of
  every generation it has had (v1: one) and checks each default against all of them.

| Key | Default | Bounds | CLI override |
|---|---|---|---|
| `worker.drain_detach_seconds` | 10800 | 60 to 604800 | `resume --drain-timeout` (re-attach drain only) |
| `worker.timeout_seconds` | `null` (no limit) | `null`, or 60 to 172800 | global `--timeout` |
| `run.max_steps` | 20 | 1 to 1000 | `run --max-steps` |
| `follow.heartbeat_seconds` | 30 | 1 to 3600 | none |
| `follow.replay_events` | 20 | 0 to 10000 | `follow --from-start` (all) |
| `timeouts.git_seconds` | 600 | 30 to 7200 | none |
| `timeouts.release_command_seconds` | 1800 | 60 to 21600 | none |
| `timeouts.workflow_query_seconds` | 120 | 10 to 3600 | none |
| `forge.pr_list_limit` | 200 | 50 to 1000 | none |
| `routing` | `{"default": {}, "roles": {}}` | `routing.validate_routing_mapping`'s rules, with no `schema_version`, and unknown keys directly under `routing`, unknown role names and unknown route fields ignored (A.5) | `--routing-config`, `--model`, `--effort`, `--role-model`, `--role-effort` |

Booleans are not integers: `true` is refused where an integer is expected.

**A.4 The fill and migration.** Every command loads and validates the file (I2). The read-only
commands (`inspect`, `explain`, `status`, `follow`, `telemetry`, `settings show`, `settings path`)
never write it: they use the built-in default for a missing key, and a missing file (roadmap
principle 8, observation is passive). The commands that already write (`step`, `run`, `resume`,
`milestone-binding`) and `settings clean` fill it before dispatch. The fill takes the exclusive
lock first, and steps 1-5 all run under it (I4):
1. loads the file, re-reading its bytes under the lock;
2. validates it (I2);
3. adds each missing key with its default, and records `{value, generation}` in
   `_defaults_written[key]`;
4. moves a value to a newer default, **forward only**. This happens only when all three hold:
   - the stored value still equals `_defaults_written[key].value` (the operator never touched
     it);
   - the built-in default differs from it;
   - this release's `generation` for the key is **greater** than
     `_defaults_written[key].generation`.

   It then updates the value and both recorded fields. A release whose own generation is lower
   or equal leaves the value alone, so two releases sharing the file never undo each other. A
   v1 entry without a recorded generation cannot exist (v1 writes it from the start);
5. sets `_table_generation` to the maximum of its stored value and this release's
   `TABLE_GENERATION` (never lowering it), and writes the file only if something changed (I4).

`routing` is filled as an empty `default`/`roles` pair. Its contents are the operator's, and are
never migrated. v1 has no changed default. The migration rule is tested with two patched tables
standing in for two releases sharing one file (CP1).

**A.5 Unknown keys.**
- An unknown key, at the top level or inside a known section, is ignored with one stderr warning
  per invocation that names it. When the file's `_table_generation` is greater than this
  release's `TABLE_GENERATION`, the warning says the file was last filled by a newer Controller
  release, whose keys these probably are.
- `settings clean` runs under the lock (I4). It **refuses**, removing nothing, with a
  `SettingsError` (exit 20) when the file's `_table_generation` is greater than this release's
  `TABLE_GENERATION`: the file was last filled by a newer release, so this release cannot tell
  that release's keys from retired ones, and the operator's values for them would be lost. The
  message names both generations and says to run `settings clean` from the newest installed
  release. Otherwise it removes every unknown key and its `_defaults_written` entry, and prints
  what it removed.
- A key that a later release retires is an unknown key to that release, and to that release's
  `clean`. A release still in use that knows the key keeps reading it.
- **Inside `routing`** (review round 4, R4-003). The routing section follows the same rule.
  `--routing-config`'s own parse refuses an unknown role or route field (Investigation), and
  applying that to the shared file would let a newer release that adds a role make the file
  unusable to an older release still installed. The parse entry point is therefore split in two
  over one validator (review round 5, LPR5-001). `parse_routing_config` takes JSON **text**
  and refuses any `schema_version` other than 1, including an absent one
  (`controller/routing.py:271-294`); the settings section is an already-parsed mapping with no
  `schema_version`, so it cannot go through that function as it stands:
  - CP1 factors the field and role checks out of `parse_routing_config` into
    `routing.validate_routing_mapping(data, *, path, where, require_schema_version, unknown)`,
    which takes the parsed object. `where` is the dotted prefix put in front of every location
    it names (`""` for the file, `"routing."` for the section). It returns the
    `RoutingConfig` and, under `unknown="ignore"`, the list of dotted paths it left out. It
    raises `RoutingConfigError`, as today;
  - `parse_routing_config(text, *, path)` keeps its signature. It decodes the text with the
    duplicate-key hook as today and calls the validator with `require_schema_version=True`,
    `unknown="refuse"` and `where=""`. So `--routing-config` keeps today's behaviour byte for
    byte: the same refusals, messages and evidence, which the unchanged `tests/test_routing.py`
    cases pin;
  - `controller/settings.py` calls the validator on the file's `routing` value with
    `require_schema_version=False`, `unknown="ignore"` and `where="routing."`. A
    `schema_version` key directly under `routing` is reserved there, and refused (below). Settings converts a `RoutingConfigError` into a `SettingsError`
    (exit 20, I2, A.7): the message names the settings path and the dotted key
    (`routing.roles.implement.model`, say) and says what is wrong, and the evidence carries
    `path`, the dotted `key` and the validator's own evidence. It never surfaces as
    `RoutingConfigError` or with the routing config's "cannot be used" message.

  With `unknown="ignore"`:
  - an unknown role name under `roles`, an unknown field inside `default` or a role's entry, and
    an unknown key directly under `routing` (a sibling of `default` and `roles` that a newer
    release adds, review round 6, MPR6-O1) are left out of the parsed config and reported by the
    unknown-key warning above, by their dotted path (`routing.roles.<role>`,
    `routing.default.<field>`, `routing.<key>`);
  - `schema_version` directly under `routing` is the one reserved name there. It is the
    `--routing-config` file's own header, so its presence means that file was pasted in whole,
    and it is refused (below). No release ever adds a key of that name under `routing`;
  - everything else stays strict and refuses with `SettingsError` (exit 20, I2): a section or
    entry that is not an object, a known field whose value `check_value` refuses, and
    `routing.schema_version`;
  - a default-filled file, whose section is `{"default": {}, "roles": {}}`, loads with no
    warning and resolves every route exactly as no routing config does today;
  - the route values are free strings today, so a newer release's new model or effort name is
    never a key and never refused by an older release;
  - the known role names, route fields and keys directly under `routing` are part of the pinned
    table (I3): a release that adds one raises `TABLE_GENERATION`, so an older release's `clean` refuses the file instead of
    removing the newer role (above), and a newer release's `clean` removes a retired one.

  `--routing-config` keeps today's strict parse through `parse_routing_config`: its file is the
  operator's own and one release reads it at a time. That file's behaviour is unchanged. Both
  sources share the one validator, so a known role's valid entry resolves to the same route
  through either.

**A.6 The `settings` subcommand.**
- `workflow-controller settings show` prints each effective value with its source (`cli`,
  `file`, `default`), plus the file path. `--json` is honoured.
- `settings path` prints the path.
- `settings clean` rewrites the file without unknown keys, or refuses (A.5).
- `show` and `path` are read-only (A.4). `clean` fills, then removes.

**A.7 Errors.** `SettingsError` goes into `controller/errors.py` with exit 20, and its evidence
names the path and the key. The file primitives go into `controller/runtime.py`, as the
write-containment test requires:
- `read_settings_bytes`;
- `write_settings_atomically(path, obj)`, with the lock;
- the settings root is contained by `assert_contained` against its own directory.

### B. Wiring the settings in (CP2)

- `cli.main` resolves settings once, after the re-exec and before dispatch. It passes an
  immutable `EffectiveSettings` to the commands.
- **Values with an existing call path** are passed explicitly:
  - the drain bound: `worker.supervise`/`launch` take it as a parameter, replacing the module
    constant at `worker.py:1789`, and `job`'s re-attach drain takes it from `resume`;
  - the worker timeout;
  - `max_steps`;
  - the follow heartbeat and replay count.
- **Leaf timeouts** have no options path: `gitrepo`'s default, `release_txn`'s command timeout,
  `workflow_contract`'s query timeout and `forge`'s list limit. These keep their module constants
  as the built-in default. They read a process-wide value that `cli.main` sets once through
  `settings.apply_process_defaults(effective)`. Tests restore it with a context manager.
  - **Read at call time, not at definition** (review round 5, LPR5-002). Today two of them are
    bound before `cli.main` runs: `gitrepo._DEFAULT_RUNNER = subprocess_runner()` is built at
    import with `timeout=DEFAULT_TIMEOUT_SECONDS` as a default argument
    (`controller/gitrepo.py:38-53`), and `GhForge.__init__` builds its runner the same way
    (`controller/forge.py:169`). CP2 changes `subprocess_runner`'s `timeout` default to `None`,
    meaning "the process-wide value, read when `run` is called", so `_DEFAULT_RUNNER` and a
    `GhForge` built before `apply_process_defaults` both apply the value set later. An explicit
    `timeout=` keeps its meaning. `forge`'s list limit and the `release_txn` and
    `workflow_contract` timeouts are read at their call sites the same way, never copied into a
    default argument or a module-level name at import.
  `tools/release.py` does not read settings, so CI keeps the built-in values.
  - `cli.main` sets it exactly once, after resolving the settings and before any thread starts
    (the `follow` renderer thread, `cli.py:535`, starts later, in dispatch).
  - The tests' context manager is its only other writer. A test pins that `apply_process_defaults`
    is called from `cli.main` alone.
- **The drain bound on the record.** Each job that drains records the bound it used as
  `drain_detach_seconds`, next to `drain_detached_at`. Every message that prints the bound reads
  it from there: the `status`/`follow` activity line (`observe.py:1129`) and the detach errors on
  both paths (`job.py:3685` and the fresh-step path). A record without the field (an older
  record) falls back to `worker.DRAIN_DETACH_SECONDS`. So `status` or `follow` in another
  invocation prints the bound that was actually applied (review round 1, R1-007).
- **`resume --drain-timeout SECONDS`** overrides `worker.drain_detach_seconds` for that
  re-attach drain only (roadmap 1.4), with the same bounds.
- **Routing.** `_routing_options` (`cli.py:848-862`) builds the routing config from the
  settings' `routing` section, unless `--routing-config` is given (I1). One validator,
  `routing.validate_routing_mapping`, applies to both sources, except that the settings section
  carries no `schema_version` and ignores unknown keys directly under `routing`, unknown role
  names and unknown route fields with a warning (A.5, R4-003, LPR5-001, MPR6-O1, LPR7-O1). The
  job record's `worker_route.sources` labels are unchanged (`config-role`/`config-default` now
  also mean the settings file), and a new `worker_route.config_source` says `settings`, `routing-config` or `none`.
- **The job record** gains an optional `controller_settings` block:
  `{path, sha256 (of the file bytes read, or null), values: {key: effective value}, sources:
  {key: cli|file|default}}`. `SCHEMA_VERSION` stays 1. The block is optional, readers tolerate
  its absence, and 1.4.x never reads it.

### C. Telemetry v0 (CP3)

- **The computation.** A new pure function `worker_stream.session_telemetry(results)` returns:

  ```text
  {
    "version": 1, "results": <n>, "turns": <sum num_turns>,
    "duration_ms": <sum>,
    "duration_api_ms": <maximum over all results>,
    "cost_usd": <maximum of total_cost_usd over all results>,
    "tokens": {"input", "output", "cache_creation", "cache_read"}  (sums of usage.*),
    "models": {<model>: {"input", "output", "cache_creation", "cache_read", "cost_usd"}}
              (modelUsage: the maximum over all results, per model and per field),
    "problems": [...]
  }
  ```

  The three cumulative figures take the maximum, never the last result's value (I7, review
  round 2, R2-001). `problems` lists, among others, `cumulative_not_advanced` entries naming the
  `result_index` of a result with non-zero `usage` whose cumulative figures equal the previous
  result's (I7). A field that is absent or not a number contributes `null` to its figure, and a
  problem entry (I6).
- **The record.** Both completion paths (`job.py:5000-5009` and the re-attach path
  `:3655-3664`) add a `telemetry` block to the record. It holds the block above, plus:
  - the wall time: `job_seconds` from `created_at` to completion, and `worker_seconds` from the
    `on_spawn` flush to the worker's exit (`null` on the re-attach path when the
    `worker_spawned` event line is missing);
  - the dimensions section 8 asks to keep separate: `role`, `model`, `effort` (from
    `worker_route`), `harness: "claude-code"`, `workflow_version`
    (`target_workflow_version`) and `controller_version`.
- **The failure boundary** (review round 4, R4-004, I6). Both completion paths build the block
  through one helper, `job._telemetry_block(record, result, streams)`, which never raises an
  `Exception`:
  - it calls the reads and computations inside one `try`/`except Exception`. On a failure it
    returns `{"version": 1, "failed": true, "problems": [{"kind": "telemetry_failed", "error":
    "<exception class>: <message>"}]}`, with every figure absent, so readers treat it as a block
    with no figures. `KeyboardInterrupt` and `SystemExit` are not caught;
  - it runs after `result` is known and before the one completion `_persist`, whose other fields
    (`status`, `worker`, `worker_outcome`, the `completed` event and its details) are built as
    today and never read the block. So the persisted status, outcome and event are the same as
    without telemetry, and so is what step 7 reconciles from them. No second record write is
    added;
  - the `completed` event's details keep today's keys and gain only a `telemetry` summary
    copied from the returned block (its totals, or `"failed"`), which `follow`'s `completed`
    line prints. Since the helper never raises, building the details cannot fail either.

  The `telemetry` command and the presentation lines skip a `failed` block's figures and count
  the job under a `telemetry unavailable` total.
- **Old records.** `controller/telemetry.py` reads the records under `<runtime>/jobs/`. For a
  record without `telemetry`, it derives the same block from the record's
  `worker_streams.stdout_path` through `worker_stream`'s parser, marked `"derived": true`. A job
  with no stream contributes a row with `null` figures. Nothing is written back.
- **The `telemetry` subcommand** is read-only:
  - `workflow-controller telemetry [--work-item ID | --run ID] [--since ISO] [--by role|model|role,model]
    [repo]`;
  - it prints jobs, turns, tokens, cost, API time and wall time, as totals and per-job means,
    grouped as asked;
  - `--json` prints the rows and the groups;
  - the repository filter uses the record's `target_repo`.
- **Presentation.** `inspect` and `status` show a finished job's cost and wall time, where a
  telemetry block exists. `follow`'s `completed` line gains the session totals.

### D. Release notes follow the milestone (CP4)

**D.1 The policy.** Two optional additions, parsed strictly:
- `milestone_branches.pull_request.release_notes: {"path": "<template>", "heading": "<text>"}`.
  - `path` allows only the `{work_item_id}` placeholder, and must be a relative, normalised POSIX
    path.
  - `heading` is a non-empty single line.
  - Absent means no notes, and today's body.
  - The template's only consumer is readiness (D.2), which renders it with the bound work
    item and reads that one path. A template with no placeholder (a fixed notes file each
    milestone rewrites) or with the placeholder twice renders to one path like any other, so
    D.1 requires no particular shape (review round 11, LPR11-001). The release never reads
    the template (D.3).
- A new publication placeholder `{release_notes}`, allowed in `release.publication.notes` only.

**D.2 Readiness.** In squash mode, `_sync_for_readiness` takes the notes path template and
heading from the `release_notes` of the **binding's policy snapshot**, `binding_policy(record)`
(review round 9, LPR9-001). That is the plan's one rule for where a milestone's notes live.
Readiness already makes every squash-mode decision from the snapshot: `_squash`
(`controller/milestone_branch.py:1208-1211`) and `ready_requires_green_checks` (`:1410`). The
snapshot is the policy committed at the branch point, a trunk commit: trunk's `HEAD` at bind
and re-bind (`:1518`), or the branch point `p` at adoption (`:1863`, `:1905`). So a milestone
keeps the notes location it started with, as it keeps its merge mode
(`docs/guide/milestone-branches.md:20`), and a policy edit on the milestone's own branch does not
move it. The policy committed at `a` was the alternative. It is not taken because it breaks that
idiom and depends on how recently the branch was synced. A snapshot with no `release_notes`
means no notes, and today's body. Readiness reads the narrative at the acceptance commit `a`
(`git show a:<path>`, so it reads the committed tree, never the working tree). It takes the section under the line `## <heading>`, up to the next `## ` line or the end
of the file, with outer blank lines trimmed. The body becomes:

```text
<!-- workflow-controller: release-notes work_item=<id> sha256=<digest> -->
<notes>
<!-- workflow-controller: release-notes end -->

Milestone `<id>`, planned in `<plan>`, driven by workflow-controller.
Accepted at <a> on `<branch>`; merge with "Squash and merge".

<!-- workflow-controller: work_item=<id> -->
```

- `<digest>` is the lowercase hexadecimal SHA-256 of the section's UTF-8 bytes exactly as the
  extraction function returns them, the same bytes placed in the body. The start marker is the
  **binding** the release relies on (D.3, review round 4, R4-002): only readiness writes it, only
  after acceptance, and only for notes that passed the I8 check. Its tokens hold no whitespace,
  so GitHub's wrap of the squash commit body cannot split one (Investigation). The notes
  lines hold at most 72 bytes each (I8), so the squash commit carries the whole block,
  and the release reads it from there (D.3, Decision 11).
- When the file or the section is missing or empty, the body is today's body, unchanged, and
  carries no release-notes marker. Readiness records why in its event:
  `release_notes: absent|empty|included`. In a repository that renders `{release_notes}`,
  the release that covers such a milestone's squash commit refuses unless another commit of
  its range carries a notes block, and the operator then supplies the notes (D.3 step 6).
  Readiness does not refuse such a milestone: its body rules are unchanged (Decision 11). The
  guide tells the author to write the section, and how notes are supplied later (CP7).
- **One module for the block** (revision 12). A new `controller/release_notes.py` holds:
  - the section extraction;
  - the block renderer (start marker, notes, end marker);
  - the notes checks (I8: the per-paragraph trailer parse through the `gitrepo` reader below,
    the length and the line rules), and the rule that the notes hold at least one non-blank
    line (review round 13, MPR13-001). Readiness never meets an empty section there, since an
    empty section gives today's body with no marker (below); `tools/release.py notes-block`
    and the release's parser (D.3) do;
  - the commit-message parser D.3 uses.

  Readiness, `release_txn` and `tools/release.py notes-block` (D.3 step 6) all call it, so
  the writer and the reader of the block cannot drift apart.
- **I8 check.** Before any edit, readiness splits the rendered body into paragraphs (runs of
  non-blank lines) and runs `git interpret-trailers --parse` once per paragraph, on a blank line
  followed by that paragraph, through a new `gitrepo` reader. Each must print nothing.
  - **The parse reads no Git configuration** (review round 5, LPR5-003). `gitrepo`'s runner sets
    only `LC_ALL`, `GIT_TERMINAL_PROMPT` and `GH_PROMPT_DISABLED` (`controller/gitrepo.py:43-44`),
    and `trailer.separators` and `trailer.<token>.*` from any configuration change what parses
    as a trailer. Measured with Git 2.55: the paragraph `see=thing` parses to nothing by
    default, and to `see: thing` with `trailer.separators = :=` set in the global file, in the
    repository's own configuration, or through `GIT_CONFIG_COUNT`. So the reader runs
    `interpret-trailers` with every `GIT_*` variable removed from the environment, then
    `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null`, in a private empty temporary
    directory with `GIT_CEILING_DIRECTORIES` set to its parent, so no repository is found and no
    repository configuration is read. Under it each of the three settings above leaves
    `see=thing` parsing to nothing (measured). The paragraph is fed on stdin; the command needs
    no repository. It also checks the length (at most 65536 characters). A failure returns a
  new gate, `release_notes_invalid`. The gate names the path, the offending paragraph's first line
  and the limit, and says to reword the paragraph or join it to its neighbour. The pull request is not edited, and nothing moves to `READY`. Notes that merely
  contain a colon inside prose or a list pass, as Git's own rule decides (measured: a paragraph
  `- a list` / `Fixes: the thing` parses to nothing).
- **Two ordinary shapes are refused** (review round 2, R2-004, measured with Git 2.55): a
  one-line paragraph such as `Note: this release changes the default.`, and a paragraph that is
  only a bare URL (`https://example.com/x` parses as `https: //example.com/x`). `Upgrade note: …`
  (a space in the key), `**Breaking:** none`, a Markdown table and both Controller marker lines
  pass, the start marker with its work-item id and digest included, whole or reflowed across two
  lines (measured, Git 2.55). Every one of the 56 paragraphs of `docs/releases/1.3.0.md` to `1.4.2.md` passes, so the
  rule is kept. `docs/guide/milestone-branches.md` names the two shapes (CP7).
- **The line rules** (I8, revision 12). Every line of the notes holds at most 72 bytes of
  UTF-8, no line ends in a space, the notes contain no tab and no carriage return, and they do
  not contain the text `<!-- workflow-controller:`. The limit counts bytes, not characters
  (review round 12, LPR12-O1). The wrap was measured on ASCII lines only, and 72 bytes is
  never more than 72 code points, UTF-16 units or display columns, so the limit holds however
  GitHub counts. A line of 72 characters with accented letters is refused at readiness, where
  the author rewraps it, and never meets a digest mismatch at release. A tab or a carriage
  return (CRLF line endings) is refused for the same reason: GitHub's handling of either is
  not measured. A failure is the same `release_notes_invalid` gate. It
  names the path, the line number, the line's first characters and the rule, and says to wrap
  the section at 72 columns or remove the text. The 56 paragraphs of `docs/releases/1.3.0.md`
  to `1.4.2.md` are wrapped at up to 100 columns, so notes written in that style are refused
  until rewrapped; the guide says to wrap the section at 72 (CP7).
- The existing idempotence holds: an unchanged body is not re-edited, and an edit ends the step
  at `checks_pending`.

**D.3 The release.** When the notes template uses `{release_notes}`, `release_txn.publish`
resolves it before tagging, from the release commit's own history. The text comes **only from
the squash commit bodies** of the release range: the notes blocks readiness wrote into the
pull request bodies (D.2), which GitHub's squash merge carries into the commit messages (the
user's decision, 2026-09-30, Decision 11). The release reads no milestone narrative, no file
of any commit's tree and no policy version other than the one it already reads. It makes no
forge call (review round 1, R1-006: the publish job's token has `pull-requests: none`, and
the plan does not change `.github/workflows/`).

A release covers every first-parent commit since the base tag, not only the commit being
released (review round 2, R2-002). `classify` bumps from `_range_bump(ctx, base_commit,
commit)` over `gitrepo.first_parent_subjects(base_commit..commit)`
(`controller/release_txn.py:346-366,433-455`). The released commit is often not the
milestone's squash commit:
- `main.yml`'s `concurrency: {group: main-release, cancel-in-progress: false}` (`:28-30`) keeps
  only the newest pending run, so a `docs:` merge (such as the release-notes pull requests #12
  and #15) landing while the milestone's run waits replaces that run;
- a failed publish that is rerun later, or two milestones merged before one release, do the
  same.

So the notes are read from every commit message of the range, not from the released commit's
alone. They are computed for a `RELEASE_DUE` publish only, the one path that creates the tag.
A `RESUME` publish reuses the notes the tag already carries (below). For `RELEASE_DUE`:
1. **The range.** It walks the same range `classify` already computed. `classify` returns no
   base today: `Classification` has no base field (`controller/release_txn.py:110-129`), and
   `base_commit` is a local variable of `classify` (`:438`) (review round 3, R3-002). CP4 adds
   one optional field, `Classification.release_range: tuple[str | None, str] | None = None`,
   the pair `(base_commit, commit)`. `classify` sets it on its `RELEASE_DUE` result only and
   leaves it `None` in every other state:
   - with the Conventional Commit trigger, it is the exact pair `_range_bump(ctx, base_commit,
     commit)` walked (`:443-444`). A `RELEASE_DUE` result always has a settled or abandoned base
     there, so `_range_bump` has always run;
   - with the committed-version trigger, which computes no range today, the base is the highest
     matching ancestor tag. That selection is factored out of the Conventional Commit branch
     (`:436-438`) and the `INVALID_TRANSITION` check (`:512-516`) into one helper that both
     triggers and that check use, so the two triggers cannot pick different bases.

   D.3 then walks `gitrepo.first_parent_subjects(base_commit, commit)` over that pair and
   computes no second range. With no base tag (`base_commit` is `None`), the range is the
   release commit's whole first-parent chain, as for the bump. In a repository that turns
   `release_notes` on before its first release tag, the first release therefore publishes the
   notes block of every commit its history holds (steps 2-5), each under its work-item id. That
   is intended, and `docs/guide/ci-and-releases.md` says so (review round 3, R3-003).
2. **The blocks** (review round 4, R4-002; review round 12, LPR12-001). For each commit of
   the range, oldest first, it reads the commit's full message as bytes (`git cat-file commit
   <sha>`, the bytes after the header's blank line, through a new `gitrepo` reader).
   `release_notes.parse_message` (D.2's module) then finds the notes blocks. It reads
   **marker lines** only: a line whose first bytes are `<!-- workflow-controller:
   release-notes`. Text that mentions the marker anywhere else in a line, such as a
   backticked quote in a pull request body that describes this feature, is not a marker and is
   ignored. Readiness writes every marker at the start of a line, and GitHub's wrap breaks a
   line only at a space after its first 39 characters, so the anchoring loses nothing
   readiness writes:
   - **the encoding**: the marker text is ASCII, so the parser first searches the raw bytes
     for a marker line. A message with no marker line contributes nothing, whatever its
     encoding, and never refuses. A message that holds a marker line is decoded as UTF-8, and
     one that is not valid UTF-8 is a refusal (step 6);
   - a **start marker** is a marker line, joined with its continuation lines up to the first
     `-->`, that reads D.2's `<!-- workflow-controller: release-notes work_item=<id>
     sha256=<digest> -->` with every run of whitespace treated as one space, since
     GitHub breaks a marker line longer than 72 characters at a space (Investigation). Its
     `work_item=` token must match `repo_policy.WORK_ITEM_ID_RE` in full
     (`^[a-z0-9][a-z0-9_-]{0,63}$`, `controller/repo_policy.py:47`), and its `sha256=` token
     must be 64 lowercase hexadecimal digits (review round 5, LPR5-004). The id becomes a
     `### <id>` line of the published text (step 5), so it is checked before anything uses it;
   - the start marker's `-->` must end its line. The **end marker** is the next line that is
     exactly `<!-- workflow-controller: release-notes end -->`, which is 47 characters and
     never wrapped;
   - the block's **notes** are the text strictly between the line break that ends the start
     marker and the line break that begins the end marker's line. That is exactly the
     `<notes>` readiness placed in the body, since readiness writes `<start>\n<notes>\n<end>`
     and GitHub changes no line of at most 72 characters (Investigation, I8);
   - **pairing** (review round 13, MPR13-O1). The parser reads the marker lines in order.
     Every marker line that is not an end marker opens a **candidate** block, well formed or
     not, and the candidate takes the first end marker after it, provided no other
     non-end marker line comes first. So a damaged start marker consumes its own end line,
     and that end line is never reported a second time as an unmatched end marker. A
     candidate with no end marker before the next candidate or the end of the message has
     none;
   - a candidate whose start marker is not well formed, or which has no end marker, or whose
     notes hold no non-blank line (MPR13-001), is a **malformed marker**, and so is an end
     marker that no candidate takes. Readiness never writes one: it writes a marker only for a
     non-empty section, and the notes cannot contain the marker text (I8), so a malformed
     marker comes only from a hand-written or damaged message. When the candidate still
     names a `work_item=` token that matches the pattern (a start marker with a bad `sha256=`
     token, with no end marker, or with empty notes), it is a **damaged block** of that work
     item, which step 3 treats like a block. Any other malformed marker (an end marker that no
     candidate takes, a start marker whose `work_item=` token is missing or fails the
     pattern, or any other variant) cannot be attributed to a work item, and is a refusal
     (step 6). An empty block is therefore never **included**, even when its digest is the
     valid SHA-256 of the empty string.

   One commit may carry several blocks, for distinct work items: a commit the operator writes
   to supply the notes of two milestones (step 6). Two blocks for one work item in one commit
   are ambiguous: together they are one damaged block of that work item, which step 3 treats
   like any damaged block, so a later valid block for that work item supersedes them (review
   round 12, LPR12-O2).
3. **Which block counts.** For each work item, the block (or damaged block) of the **newest**
   commit of the range that carries one is used. An older one for the same work item is
   **superseded**, whatever it holds, and the output names it with its commit. This is how a
   later trunk commit supplies or replaces a work item's notes (step 6). Readiness writes one
   block per milestone, so a range with no operator-supplied block has at most one per work
   item. A used damaged block is a refusal (step 6).
4. **The digest.** For each block used, the SHA-256 of its notes' UTF-8 bytes must equal the
   marker's `sha256=`. Equality proves the commit carries the bytes readiness validated (or
   that `tools/release.py notes-block` rendered, step 6). A mismatch is a refusal: the squash
   message was edited in GitHub's merge dialog, a notes line was rewrapped, or GitHub's wrap
   differs from what was measured (a non-ASCII line's length, say). An edit to the milestone's
   narrative after readiness never reaches the notes: the release does not read narratives.
5. **The text.** It renders the used blocks in the commit order of their commits, a commit's
   several blocks in message order. When there is exactly one, it is the notes as they stand.
   When there are several, each is preceded by a `### <work_item_id>` line and they are
   separated by blank lines.
6. **The outcomes**, each named in the publish output (review round 4, R4-001):
   - **included**: at least one block is used, every used block is a block and not a damaged
     one (so its notes hold a non-blank line, step 2), and every used block's digest matches.
     The output names each included work item and each superseded block;
   - **refused**, in every other case. There is no **absent** outcome: no opted-in release
     publishes empty notes (Decision 11). The refusals are:
     - **missing**: no commit of the range carries a notes block. This covers a milestone that
       shipped with no notes section, a squash commit whose message lost the pull request body,
       and a release with no milestone in its range;
     - **unreadable**: a Git command of step 2 fails (the message names the command and its
       exit status), or a message that holds a marker line is not valid UTF-8;
     - **unverified**: a used damaged block (two blocks for one work item in one commit, and a
       block with empty notes, included), a malformed marker that names no work item, or a
       used block whose digest
       does not match
       (the message names the commit, the work item and both digests).

     `publish` raises `ReleaseTransactionError`, like its other refusals, **before**
     `create_annotated_tag`, so no tag and no release is created.

   **The fix: the operator supplies the notes.** Every refusal message names it, except the
   two below that a supplied block cannot clear. The range's
   commits are immutable, so the notes are supplied by a later trunk commit, which the publish
   then releases (it is the new release commit, and the range grows to include it, since no tag
   was created). Its message carries a notes block for each work item whose notes are missing
   or failed:
   - for a milestone whose pull request body held a block, that block copied verbatim from the
     merged pull request, whose body GitHub keeps as readiness wrote it;
   - otherwise, notes the operator writes, rendered as a block by a new `python3
     tools/release.py notes-block --work-item <id> <file>`. It reads the notes from the file,
     trims outer blank lines as D.2's extraction does, applies D.2's checks (I8: trailer
     paragraphs, the length and the line rules, and at least one non-blank line), and prints
     the block with its digest, or refuses naming the failed check. An empty file, or one
     holding only blank lines, is refused (review round 13, MPR13-001). A release with no
     milestone in its range uses a work-item id of the operator's choosing for its block.

   In this repository that commit is the squash merge of a `docs:` pull request whose body is
   the block, or several blocks. A `docs:` title publishes nothing by itself, and its merge
   becomes the release commit of the pending release (`main.yml`'s concurrency, above). The
   block supersedes a failed block of the same work item (step 3).

   **What a supplied block cannot clear** (review round 12, LPR12-001). A block supersedes only
   a block or a damaged block of its own work item, so two refusals name no work item a block
   could supersede:
   - a malformed marker line that names no work item;
   - a message that holds a marker line and is not valid UTF-8.

   Their messages name the policy's opt-out below as the fix, for the one release whose range
   holds them, and not "supply the notes". A failed Git read refuses as **unreadable** and
   clears on the rerun, which its message names. Every other refusal (**missing**, a used
   damaged block, a digest mismatch) clears with a supplied block. Marker text quoted
   mid-line, and a message with no marker line in any encoding, never refuse. A pull request
   or commit body may quote the marker in prose; it must never start a line with it outside
   readiness's block (CP7's guide). The policy's own opt-out stays
   available as an explicit choice, never a silent one: a trunk commit that removes
   `{release_notes}` from `release.publication.notes` makes the publish render the policy's
   fixed text, because the publish reads the policy committed at its checked-out release commit
   (`tools/release.py:280-294`, `committed_policy(repo_root, "HEAD")`). Every refusal message
   names it: as the fix for the two refusals above, and after the supplied notes for the
   others. Every refusal clears for the release that hit it, and none persists
   into later releases: the next release's range starts at the tag this one creates.

The rendered text is stripped of trailing whitespace. It is both the annotated tag message and
the release notes, as today. So that the two stay identical when the notes hold Markdown
headings (the `### <work_item_id>` lines of step 5, or a heading inside a section),
`gitrepo.create_annotated_tag` passes `--cleanup=verbatim` (Investigation, "The release"). For
every template without a `#`-leading line or trailing whitespace, which includes this
repository's, the stored tag message is unchanged.

**`RESUME` (review round 3, R3-001).** `main.yml`'s publish job also runs for `RESUME`
(`:106`), whose target is the existing tag's commit (`release_txn.py:139-141`). There
`classify`'s base is that tag itself, whose release is missing or a draft, so `settled` is
false and no range is computed (`:436-444`): a range recomputed here would be `target..target`,
which is empty. The case is real: a first run that pushed the tag and then failed before
`create_release` (a failed `gh` call, a cancelled job) is resumed by the next run through the
`found is None` branch (`:714-722`), which renders the notes afresh. So, when the notes template
uses `{release_notes}` and the state is `RESUME`:
- with no release (`found is None`), the created release's notes are the existing annotated
  tag's message, read from the tag object `classify`'s own tag fetch brought in (`git cat-file
  tag <tag>`, the text after the header's blank line, through a new `gitrepo` reader). The
  publish output names the outcome **reused from tag**, with the tag's name;
- **the tag's message is verified first** (review round 13, MPR13-002). A tag message does not
  show which policy rendered it. The publish reads the policy committed at its checked-out
  commit (`tools/release.py:280-294`), and on `RESUME` that commit may be newer than the tag:
  `classify` enters `RESUME` for an unsettled ancestor tag (`controller/release_txn.py:509-511`).
  So a tag created while the policy did not use `{release_notes}`, followed by a trunk commit
  that opts in, would otherwise publish the old fixed text as if it carried verified notes.
  Before `create_release`, the publish therefore recomputes the notes the tag should carry:
  - the range is `(base, tag_commit)`, where `base` is the highest matching tag whose commit is
    an ancestor of the tag's commit and whose version is below the tag's, picked by the same
    base-tag helper as step 1 (`None` when there is none). It is the range a `RELEASE_DUE`
    publish of that commit walked, as long as no tag below it has been added or deleted since;
  - steps 2-6 run over it unchanged, and their refusals refuse the resumed release, with the
    same messages and fixes;
  - the current template is rendered with those notes and the tag's own version, tag and
    commit (`_values`, `controller/release_txn.py:167-168`; every publication placeholder is a
    pure function of them), and trailing whitespace is stripped as for a new tag;
  - the result must equal the tag's message byte for byte. Equality proves that the tag holds
    verified notes rendered by the template the release now uses, so the tag and the release
    cannot disagree. Any difference is **refused** as **unverified tag**: a tag created before
    the opt-in, under another template, or from a range whose blocks changed. The message
    names the tag and the first differing line, and names the fix: the operator creates the
    release for that tag with the right notes. The publish never edits or re-creates a pushed
    tag;
- a tag that is not an annotated tag object, a failed read, or a message that is not valid
  UTF-8 is **refused** (review round 4, R4-001; Decision 11): `publish` raises
  `ReleaseTransactionError` before `create_release`, naming the tag, the command and its exit
  status, and creates nothing. The pushed tag stays as it is. A rerun clears a transient
  failure. Otherwise the message names the fix: the operator supplies the notes, by creating
  the release for that tag with them (a tag that is not annotated was not created by this
  code). Once that release exists and is not a draft, the next run finds the tag settled and
  creates nothing. It never renders empty notes;
- a draft being resumed keeps its notes, as today (`_complete_draft`). The publish never
  creates a draft (`create_release` passes `--verify-tag` and publishes directly,
  `controller/forge.py:346-351`), so a draft is the operator's, and its notes are the
  operator's choice.

A template that does not use `{release_notes}` renders on `RESUME`, and on `RELEASE_DUE`,
exactly as today. The pull request body still carries the notes (D.2), so the squash commit's
message records them in the history, but such a release never parses that message.

**D.4 This repository.** `.workflow-controller/policy.json` is not changed here (I9). Once 1.5.0
is installed in the shared install, a cutover pull request (`chore:`, no release) sets:
- `release_notes: {"path": "docs/milestones/completed/{work_item_id}.md", "heading": "Release notes"}`;
- `notes: "workflow-controller {tag}\n\n{release_notes}"`.

C4 (1.6.0) is then the first release whose notes are automatic. The cutover lands between
milestones (C4 is planned only after 1.5.0 is merged and released), so no binding straddles it.
A milestone bound before a repository turns `release_notes` on carries no marker (D.2), so a
release whose range holds no other notes block is refused as missing until the operator
supplies its notes (D.3 step 6); CP7's guide says so. From the cutover on, a milestone's
`## Release notes` section is wrapped at 72 columns (I8). This milestone's own notes are
its narrative's `## Release notes` section, added to `docs/releases/1.5.0.md` by the follow-up
docs pull request, as before.

### E. The four 1.4 patches (CP5, CP6)

- **E.1 Hints (CP5).**
  - `_explain_command` is deleted. Every caller, plus `decision.py:850` and `evidence.py:1969`,
    uses `_explain_gate_command(root, work_item_id)` (`workflow-controller --work-item <id>
    explain <repo>`, quoted with `shlex`).
  - The resume hint (`observe.py:967`, `job.py:3071`) includes `--runtime-dir` exactly when
    `follow_command` does.
  - I10's test parses every hint.
  - The pinned strings in `tests/test_cli.py`, `test_job.py`, `test_evidence.py`,
    `test_integration_disposable_repo.py`, `test_lifecycle_orchestration.py` and
    `test_decision.py` are updated.
- **E.2 Manual-external gate coherence (CP5).** Before the "no feedback" branch builds its
  gate, a new `evidence._manual_external_ledger_problem(stage, work_item, manifest)` checks, for
  the implementation stage:
  - the ledger is not malformed;
  - its `review_content_id` equals the bundle manifest's;
  - a local-stage `APPROVE` is recorded for that id.

  It checks the same for the plan stage against the `plan_review_stages` ledger and the plan
  bundle's manifest. It reuses the comparisons that
  `evaluate_manual_*_stage_admissibility` already make. It does not re-implement them.

  If any check fails, the gate is still a human gate at the same phase, but its reason is
  `manual_external_ledger_incoherent`. Its text:
  - names the failed check, the manifest's id and the ledger's id (or "none");
  - says not to send the bundle for external review;
  - names, as text, the way out that the Workflow's own transitions accept at the phase where
    the gate fires (review rounds 1 and 2, R1-008 and R2-003). Rerunning the local review
    stage is **not** one: `/review-plan` refuses outside `AWAITING_LOCAL_PLAN_REVIEW`
    (`WrongPhaseForPlanReviewStageError`), and `/review-implementation` writes the ledger only
    in its `"2.2"` authoritative branch at `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`
    (`.claude/commands/review-implementation.md:371-375`); at the manual-external phase it runs
    the advisory branch, which never writes `WORKFLOW_STATE.json`. So, for every check:
    - **plan stage** (`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`): withdraw with `/milestone-plan
      <id>`. Its row-1 entry admits that ready phase and its withdrawal returns the item to
      `REVISING_PLAN` (`.claude/commands/milestone-plan.md:117-150`), from which the plan is
      republished and re-enters `AWAITING_LOCAL_PLAN_REVIEW` for a fresh local pass. The text
      adds, in one clause, that the withdrawal discards both recorded plan-review stages and
      consumes the withdrawn content, which can never re-bind unchanged: only an edit plus
      regeneration can (`milestone-plan.md:156-160`, review round 3, R3-004);
    - **implementation stage** (`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`): no Workflow
      command moves the phase back (`docs/ai-workflow/MILESTONE_WORKFLOW.md:405-437`: the only
      exits are `/record-manual-implementation-review`'s verdicts, and
      `/recover-implementation-provenance` never changes `phase`), so the text says that the
      Workflow state needs explicit user resolution before any external review.
    It does not name `explain`, which would only derive the same gate again. CP5's tests pin
    each stage's text.

  It never prints the stale id as the id to use. The Controller repairs nothing; Workflow owns the
  ledger. A coherent ledger produces today's gate, byte for byte.
- **E.3 Relaunch-bound tests (CP6), tests only.**
  - Seeded apply-job records with `code` `OperatorAbandoned` and `UnreconcilableJobError` bind the
    bound, both in `job.last_launched_apply_job_view` and in the gate.
  - An abandoned record that never reached `LAUNCHED` (no `expected_transition`) is not J.
  - An end-to-end case in `tests/test_lifecycle_orchestration.py`: a launched apply job, then
    `resume --abandon`, then the next `step` stops at the relaunch-bound gate, with a hint that
    parses.

  If a test finds a defect, the fix is made in this checkpoint and the checkpoint notes say so.
- **E.4 `status` (CP6).**
  - The `jobs:` line becomes `jobs: <n> recorded`, followed by the 10 most recent, one per line:
    id, status, command, work item, and age (or wall time and cost when finished).
  - An active job's line gains its work item, command and start time.
  - A run's line gains its start time.
  - `status` honours `--json`, with the same fields plus the runtime root, identity and
    bindings.
  - The rest of the text output is unchanged.

### F. Documentation and full verification (CP7, terminal)

- `docs/guide/runtime.md`: the settings file (location, table, fill, migration, clean, and the
  routing section's unknown roles and fields, A.5). It also says that the file is shared by
  every repository and lane on the machine.
- `docs/guide/commands.md`: the `settings`, `telemetry`, `status --json` and `resume
  --drain-timeout` commands, and the `--settings` option.
- `docs/guide/workers.md`: the drain bound is a setting. Telemetry is described here.
- `docs/guide/milestone-branches.md`: the body with notes, and `release_notes_invalid`, naming
  the two refused shapes and the line rules: wrap the notes section at 72 bytes (72 columns
  of ASCII, fewer characters on a line with accented letters), no trailing space, no tab or
  carriage return, no Controller marker text (D.2, I8, LPR12-O1). It also says that a
  work-item id longer than 62 characters makes the marker's `work_item=` token longer than
  72 characters, one token whose GitHub wrap is not measured (review round 12, LPR12-O4).
- `docs/guide/ci-and-releases.md`: `{release_notes}`; that the release text comes only from
  the notes blocks of the squash commit messages of the release range, verified against their
  digests (D.3, R4-002, Decision 11); that the squash commit message must carry the pull
  request body (GitHub's squash message setting), and the measured wrap that makes the 72-column
  rule necessary; the **included** and **refused** outcomes, with no **absent** one; the fix
  for a refusal, the operator supplying the notes in a later trunk commit (the merged pull
  request's block copied verbatim, or `tools/release.py notes-block`), and the policy's
  explicit opt-out; that only marker lines are read, so a body may quote the marker mid-line
  but must never start a line with it outside readiness's block, and that such a line naming
  no work item, or a non-UTF-8 message holding one, refuses with the opt-out as its only fix
  (LPR12-001); that an operator commit made with `git commit` rather than a squash merge needs
  `--cleanup=verbatim`, since Git's default cleanup strips `#`-leading lines and so changes the
  notes' digest (LPR12-O3); that a newer block supersedes an older one for the same work item; that a
  milestone with no notes section makes its release refuse unless notes are supplied; that a
  squash commit that lost its body is not detected when another block is in the range
  (Non-goals); that readiness takes the notes location from the binding's policy snapshot, so
  a milestone bound before the opt-in carries no marker (LPR9-001); that a resumed release
  reuses the tag's message only when it equals the notes recomputed for the tag, and what to
  do when the tag cannot be read or differs (create that release by hand with the right
  notes), notably for a tag created before the opt-in (MPR13-002); that an empty notes block
  is never included (MPR13-001); that a repository
  with no release tag yet publishes every block its history holds (R3-003); and the cutover.
- `docs/README.md`: the release-notes rule, stated as the policy opt-in.
- `docs/guide/troubleshooting.md`: `SettingsError` and the incoherent manual-external gate.
- A new ADR, `docs/adr/0008-controller-settings-file.md`: the location, precedence, fill and
  migration, and the settings/constant boundary.
- The milestone narrative carries a `## Release notes` section (the 1.5.0 notes).
- The goldens pass `--check`, and the full sharded suite passes.

## Checkpoints

<!-- registry table: generated by workflow_state.render_registry_markdown, never hand-edited -->

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The settings file: controller/settings.py's closed table (type, default, bounds, CLI flag, default generation), location order (--settings, WORKFLOW_CONTROLLER_SETTINGS, XDG_CONFIG_HOME, ~/.config), strict validation refusing with SettingsError exit 20 (including malformed _table_generation/_defaults_written), the additive fill with _defaults_written {value, generation} and _table_generation and the forward-only untouched-value migration under the table compatibility rule (no default outside an earlier generation's bounds, no type change), the whole read-modify-write of the fill and of clean under the lock through runtime.py writing only on change, unknown-key warnings (the settings file's routing section ignoring unknown role names, route fields and keys directly under routing (routing.schema_version reserved and refused) with a warning, one validate_routing_mapping shared with --routing-config, the section with no schema_version and unknown=ignore, its refusals SettingsError at dotted keys, --routing-config byte-for-byte strict), settings show|path|clean with clean refusing a file filled by a newer release, the two-release, shared-file routing and clean-racing-fill tests, and the I5 isolation guard in tests/__init__.py (XDG_CONFIG_HOME redirected, WORKFLOW_CONTROLLER_SETTINGS removed, sentinel never touched) | - | 3 | 1 |
| CP2 | The settings wired in: EffectiveSettings resolved once in cli.main after the re-exec; CLI overrides declared default=None and --timeout/--max-steps/--drain-timeout refusing non-positive values with exit 2; the drain detach bound, worker timeout, max_steps and follow heartbeat/replay passed explicitly; the git, release-command, workflow-query timeouts and PR list limit through apply_process_defaults set once before any thread; resume --drain-timeout; the applied drain bound recorded as drain_detach_seconds and read by every message that prints it; routing from the settings section unless --routing-config replaces it (I1), worker_route.config_source; the optional controller_settings job-record block; one test per table row | CP1 | 3 | 1 |
| CP3 | Telemetry v0: worker_stream.session_telemetry over every result (usage and turns/duration summed, cost, API time and modelUsage (per model and per field) as the maximum over all results, never the last result's, a cumulative_not_advanced problem when a result's cumulative figures did not change), the telemetry job-record block on both completion paths with wall times and the role/model/effort/harness/workflow/controller dimensions, null figures plus problems for malformed input and a failure boundary (job._telemetry_block catching every Exception into a failed block, so an injected telemetry failure leaves status, outcome, events and reconciliation unchanged) (I6), controller/telemetry.py deriving old records from worker.stdout, the read-only telemetry command with --work-item/--run/--since/--by and --json, and the cost/time lines in inspect, status and follow | - | 3 | 1 |
| CP4 | Release notes follow the milestone: optional policy keys milestone_branches.pull_request.release_notes {path, heading} and the {release_notes} publication placeholder; controller/release_notes.py shared by readiness, the release and tools/release.py notes-block (which refuses empty notes); readiness reads the notes section, at the path and heading of the binding's policy snapshot, at the acceptance commit into a block above the Controller's lines, refusing release_notes_invalid with no edit on any paragraph that parses as a trailer block (git interpret-trailers --parse per paragraph, no Git configuration), an over-long body, a notes line over 72 UTF-8 bytes, ending in a space or holding a tab or carriage return, or Controller marker text in the notes; the start marker binding the notes with the work-item id and the SHA-256 of the validated section; release_txn rendering {release_notes} on RELEASE_DUE only from the notes blocks on marker lines (a line starting with the marker) in the commit messages of classify's release range (Classification.release_range), each verified against its marker's digest, the newest block of a work item superseding older ones, in commit order under ### <work_item_id> when several, with no tree, narrative or policy-history read and no forge call; on RESUME with no release from the existing annotated tag's message, only after recomputing the notes over the tag's own range (the shared base-tag helper) and rendering the current template, which must equal the message byte for byte, a tag created before the opt-in refused as unverified tag; tags created with --cleanup=verbatim; refused before any tag or release is created when no block is in the range, a Git read fails, a message holding a marker line is not UTF-8, a used block is damaged (two blocks for one work item in one commit, a block with empty notes, and a start marker with no end marker included; a damaged start marker consumes its end line) or its digest mismatches, or a marker line names no work item, each refusal naming a fix that clears it (the operator supplies the notes in a later trunk commit, through the pull request's block or tools/release.py notes-block; a rerun; or, for the two refusals no block can clear, the policy's opt-out); marker text quoted mid-line and messages with no marker line never refuse; this repository's policy.json byte-unchanged | - | 3 | 1 |
| CP5 | The hints parse and the manual-external gate tells the truth: _explain_command removed in favour of _explain_gate_command at every caller plus decision.py:850 and evidence.py:1969, the resume hint carrying --runtime-dir like follow's, the I10 test parsing every printed hint, the pinned test strings updated; _manual_external_ledger_problem for both stages (malformed ledger, id differing from the manifest, no local APPROVE) giving a manual_external_ledger_incoherent gate that never offers the stale id and names a way out the Workflow accepts at that phase (the /milestone-plan withdrawal for the plan stage, explicit user resolution for the implementation stage), a coherent ledger giving today's gate text | - | 2 | 1 |
| CP6 | Relaunch-bound tests and status: seeded OperatorAbandoned and UnreconcilableJobError apply records bind the bound in the view and the gate, an abandoned never-LAUNCHED record is not J, an end-to-end launch/resume --abandon/step case; status prints a job count and the ten most recent jobs, active jobs and runs with work item, command and start time, and honours --json | CP5 | 2 | 1 |
| CP7 | Documentation and full verification (terminal): docs/guide runtime, commands, workers, milestone-branches, ci-and-releases and troubleshooting, docs/README.md's release-notes rule, ADR 0008 for the settings file, the milestone narrative's Release notes section, goldens --check, the full sharded suite, and an empty diff from a47e695 for .workflow-controller/, pyproject.toml, setup.py and .github/workflows/ | CP1, CP2, CP3, CP4, CP5, CP6 | 1 | 1 |

### CP1 -- the settings file

- Files: `controller/settings.py` (new), `controller/runtime.py`, `controller/errors.py`,
  `controller/cli.py`, `controller/routing.py` (`validate_routing_mapping`, A.5),
  `tests/test_settings.py` (new), `tests/test_routing.py`, `tests/__init__.py`
  for I5, and
  `tests/test_write_containment.py` only if the new primitives need to be listed.
- Done when:
  - the tests of Design A pass: location order, validation refusals, fill, the unknown-key
    warning, `clean`, concurrent fills leaving one valid file, and the unwritable-directory
    warning (with an existing valid file's values still applied, I2);
  - the two-release test passes: two patched tables standing in for releases N and N+1 (N+1
    raises one default's generation and adds a key) share one file in alternation. N+1 moves the
    untouched value; N never moves it back; N's `settings clean` refuses and keeps N+1's key and
    its value;
  - the shared-file routing test passes (review round 4, R4-003): release N+1's patched table
    adds a role, and the file's `routing.roles` holds an entry for it and a known role's entry
    with an extra route field, and a new key directly under `routing` (review round 6,
    MPR6-O1). Release N loads the file with exit 0, warns once for `routing.roles.<new role>`,
    the extra field and `routing.<new key>`, and resolves the known role's route as
    before; N's `clean` refuses and keeps the entry; N+1 uses it. A known field with a value
    `check_value` refuses, and a non-object `routing.roles`, still exit 20. `--routing-config`
    with the same unknown role still refuses with `RoutingConfigError`, as today;
  - the routing parse tests pass (review round 5, LPR5-001): a default-filled settings file,
    `"routing": {"default": {}, "roles": {}}` included, loads with exit 0 and no warning; a
    malformed routing entry in the settings file (a known field `check_value` refuses, and a
    `schema_version` under `routing`) refuses as `SettingsError` with exit 20, naming the
    settings path and the dotted key, never as `RoutingConfigError`; a known role's valid entry
    resolves to the same route through the settings section and through `--routing-config`;
    and every existing `tests/test_routing.py` case passes unchanged;
  - a `clean` racing a fill (two processes, the fill adding a key and `clean` removing an unknown
    one) ends with both changes applied, and the file re-read under the lock (I4);
  - the I5 guard passes, including the `WORKFLOW_CONTROLLER_SETTINGS` sentinel never read or
    created, and a direct `python3 -m unittest tests.test_settings` run is isolated too;
  - the table test checks each default against every generation's bounds and type (the A.3
    compatibility rule), and the malformed `_table_generation`/`_defaults_written` shapes are
    refused with exit 20 (I2);
  - the full sharded suite passes.

### CP2 -- the settings wired in

- Files: `controller/cli.py`, `controller/job.py`, `controller/worker.py`, `controller/observe.py`,
  `controller/gitrepo.py`, `controller/forge.py`, `controller/release_txn.py`,
  `controller/workflow_contract.py`, `controller/routing.py`, `controller/settings.py`, and tests
  in `tests/test_settings.py`, `test_cli.py`, `test_job.py`, `test_worker.py`, `test_routing.py`,
  `test_resume.py`, `test_observe.py`.
- Done when:
  - each setting provably reaches its consumer, through a test per row of the table;
  - `resume --drain-timeout` bounds the re-attach drain;
  - `status` and `follow`, run in a different invocation from the drain, print a non-default
    bound from the record's `drain_detach_seconds`, and an older record without it prints the
    constant;
  - `run --max-steps` omitted takes the file's value, and the flag beats it (I1);
  - `--timeout 0`, `--max-steps 0` and `resume --drain-timeout 0` exit 2 (I1's behaviour
    change);
  - routing from settings and from `--routing-config` follows I1;
  - the leaf values are read at call time (review round 5, LPR5-002): with `gitrepo` imported
    and a `GhForge` constructed **before** `apply_process_defaults` sets a non-default
    `timeouts.git_seconds`, both `gitrepo._DEFAULT_RUNNER` and that forge's runner pass the new
    value to `subprocess.run`; the same for `forge.pr_list_limit`, the release-command and the
    workflow-query timeouts;
  - `controller_settings` is recorded;
  - the full sharded suite passes.

### CP3 -- telemetry v0

- Files: `controller/worker_stream.py`, `controller/job.py`, `controller/telemetry.py` (new),
  `controller/cli.py`, `controller/observe.py`, and tests in `tests/test_worker_stream.py`,
  `test_telemetry.py` (new), `test_job.py`, `test_observe.py`, `test_cli.py`.
- Done when these pass:
  - the two-result contract fixture's totals, checked by hand: turns 127 + 8 = 135, the summed
    `usage` tokens, and cost 24.2492376, API time 2719274 and `claude-opus-5-5` output tokens
    326896 taken as the maximum; one `cumulative_not_advanced` problem naming `result_index` 1;
  - a result whose cumulative figures are lower than an earlier result's, where the maximum is
    kept, for cost, API time and each per-model field (Design C);
  - a four-result `FAKE_CLAUDE_TURNS` session;
  - malformed usage (I6);
  - both completion paths;
  - the failure boundary (review round 4, R4-004), on both completion paths:
    `session_telemetry` and the stream read each patched to raise. The job's `status`,
    `worker`, `worker_outcome`, the `completed` event's existing details, and the phase and
    transition step 7 reconciles are equal to those of the same job with telemetry working; the
    record's block is `failed` with one `telemetry_failed` problem; and `status`, `inspect`,
    `follow` and `telemetry` print it as unavailable without failing. A `KeyboardInterrupt` raised
    there is not swallowed;
  - derivation for a record without `telemetry`;
  - the grouping and the `--json` output.

  The full sharded suite also passes.

### CP4 -- release notes follow the milestone

- Files: `controller/release_notes.py` (new: the extraction, the block renderer, the notes
  checks and the message parser, D.2), `controller/repo_policy.py`,
  `controller/milestone_branch.py`, `controller/release_txn.py` (`Classification.release_range`
  and the shared base-tag helper, D.3), `controller/gitrepo.py` (the trailer check, the commit
  and tag message readers and `create_annotated_tag`'s `--cleanup=verbatim`),
  `tools/release.py` (`notes-block`, D.3 step 6), and tests in `tests/test_repo_policy.py`,
  `test_release_notes.py` (new), `test_pull_request_lifecycle.py`, `test_release_txn.py`,
  `test_release_tools.py` (`tools/release.py`),
  `test_integration_disposable_repo.py`.
- Done when:
  - the policy tests pass: new keys accepted, placeholders confined, a bad path refused; a
    notes path with no `{work_item_id}` (`NOTES.md`) and one with two
    (`docs/{work_item_id}/{work_item_id}.md`) both accepted, as D.1 states (LPR11-001);
  - the readiness tests pass: absent, empty and included notes; the trailer refusal with no edit,
    for notes whose middle paragraph is `Fixes: the thing` / `Breaking-Change: none` above the
    Controller's lines (a body a whole-body parse accepts); a bare-URL paragraph and a one-line
    `Note: …` paragraph refused; notes with a colon in prose or a list, `Upgrade note: …`,
    `**Breaking:** none` and a Markdown table accepted; the length refusal; idempotence;
  - the line-rule tests pass (I8, revision 12): a notes line of exactly 72 bytes accepted,
    one of 73 refused, a line of 72 characters holding an accented letter (over 72 bytes)
    refused (LPR12-O1), a line ending in a space refused, a tab and a carriage return (CRLF
    line endings) each refused, and notes containing
    `<!-- workflow-controller:` refused, each with `release_notes_invalid` naming the line and
    no edit;
  - readiness reads `release_notes` from the binding's policy snapshot (review round 9,
    LPR9-001): with the snapshot's path and heading different from those committed at `a`, the
    body carries the section under the snapshot's, and a snapshot with no `release_notes` gives
    today's body although `a`'s policy has one. One readiness test uses D.4's exact template,
    `docs/milestones/completed/{work_item_id}.md` with `Release notes`;
  - the trailer check ignores Git configuration (review round 5, LPR5-003): with
    `trailer.separators = :=` set in a global file, in the repository's configuration and
    through `GIT_CONFIG_COUNT`, in turn, a notes paragraph `see=thing` is still accepted, as it
    is with no configuration;
  - the readiness body's start marker carries the work item and the digest of the section's
    bytes, and passes the I8 check;
  - the round trip: `release_notes.parse_message` applied to a readiness body, both as written
    and wrapped as GitHub wraps it (every line over 72 characters broken greedily at spaces,
    `0de0fd5`'s and `e8cd8f9`'s shapes, `PR_MARKER` and the start marker split), with
    GitHub's `---------` rule and `Co-authored-by:` trailer appended, returns the notes bytes
    readiness placed, whose digest matches;
  - the publish tests pass:
    - notes included from a marked squash commit whose message is wrapped as above;
    - a release whose last commit is a `docs:` commit after the marked squash commit (the notes
      are still included);
    - two milestones in one range (both included, in commit order, each under its work-item id);
    - no base tag (the whole first-parent chain): every block of the chain included;
    - **missing**: a range with no block (a squash commit of a milestone with no notes
      section; a squash commit whose message is only the title, the body lost) refused with no
      tag and no release, the message naming the fix; then a later `docs:` commit whose message
      carries the merged pull request's block copied verbatim publishes the notes
      (**included**);
    - a block printed by `tools/release.py notes-block` for notes the operator wrote, in a
      later commit, publishes them; `notes-block` refuses notes that fail a D.2 check, naming
      it, and refuses an empty file and a file of blank lines (MPR13-001);
    - **empty notes** (MPR13-001): a hand-written block whose notes are empty, with the valid
      SHA-256 of the empty string, is the only block in the range and is refused as a damaged
      block, with no tag and no release; a later commit's non-empty block for that work item
      supersedes it and publishes;
    - **pairing** (MPR13-O1): a start marker with a bad `sha256=` token followed by its end
      marker is one damaged block, its end line is not reported as unmatched, and a later
      valid block for that work item supersedes it and publishes; a start marker with no end
      marker before the next start marker is damaged, and the next block parses normally;
    - **superseded**: a squash commit whose block's notes were edited in the message (digest
      mismatch) refused; a later commit carrying a valid block for the same work item
      publishes the later block, the output naming the superseded one;
    - a later commit carrying blocks for two work items is accepted; two blocks for one work
      item in one commit refused, and a later commit's valid block for that work item
      supersedes them and publishes (LPR12-O2);
    - the refusals (review round 4, R4-001), each raising `ReleaseTransactionError` with **no
      tag created locally or pushed and no release created**: a failing `git cat-file commit`,
      with its command and exit status; a message holding a marker line that is not valid
      UTF-8; a digest mismatch;
      a malformed marker, including a `work_item=` token holding `/` or `..`, a `sha256=`
      token that is not 64 lowercase hexadecimal digits (LPR5-004), a start marker with no end
      marker, and an end marker with no start marker;
    - a damaged block (a bad `sha256=` token with a valid `work_item=`) superseded by a later
      valid block publishes; a malformed marker that names no work item is not superseded;
    - the anchoring (review round 12, LPR12-001): a range whose `docs:` commit body quotes the
      marker mid-line in backticks, `work_item=<id>` included, publishes the milestone's block
      (**included**); a non-UTF-8 message with no marker line in the range does not refuse; a
      non-UTF-8 message holding a marker line refuses; the refusals for a marker line that
      names no work item and for that non-UTF-8 message name the policy's opt-out as the fix,
      and every other refusal names the supplied notes (or the rerun for a failed Git read);
    - a trunk commit dropping `{release_notes}` after a refusal releases with the fixed text;
    - a draft resumed;
    - a tag pushed with no release, then `publish` resumed (`RESUME`): the created release's
      notes equal the tag's message, and the output names **reused from tag**; the same
      resumed from a later trunk commit (a `docs:` commit after the tag) also reuses it;
    - **a tag that predates the opt-in** (MPR13-002): a tag created and pushed under a policy
      whose notes template has no `{release_notes}`, with no release, then a trunk commit that
      opts in, then `publish` (`RESUME`): refused as **unverified tag** before
      `create_release`, which the fake forge records was never called, the message naming the
      tag and the fix; a tag whose message was rendered under a different opted-in template is
      refused the same way; a refusal of the recomputed range (no block) refuses the resume;
    - on `RESUME` with no release, a lightweight tag and a failing tag read are both refused
      before `create_release`, which the fake forge records was never called, the message
      naming the fix;
    - notes with two work items, whose `### <work_item_id>` lines survive in the tag message byte
      for byte;
    - the publish reads no tree: a release whose range also edits a marked milestone's
      narrative publishes the block's bytes, and no `git show <sha>:<path>` is run (a run spy);
  - the classify tests pass: `release_range` is `(base_commit, commit)` on every `RELEASE_DUE`
    result under both triggers (with `None` as the base when no tag is reachable) and `None` in
    every other state, and the existing classification table is otherwise unchanged;
  - `.workflow-controller/policy.json` is byte-unchanged;
  - the full sharded suite passes.

### CP5 -- the hints parse and the manual-external gate tells the truth

- Files: `controller/evidence.py`, `controller/decision.py`, `controller/observe.py`,
  `controller/job.py`, and the pinned tests listed in E.1, `tests/test_evidence.py`.
- Done when:
  - I10's test passes;
  - the incoherent-ledger tests pass for both stages: malformed, mismatched id, and no local
    `APPROVE`, each showing no stale id as the id to use; the plan-stage text names the
    `/milestone-plan <id>` withdrawal, the implementation-stage text names explicit user
    resolution of the Workflow state, and neither names `/review-plan` or
    `/review-implementation` (E.2);
  - a coherent ledger gives today's gate text;
  - the full sharded suite passes.

### CP6 -- relaunch-bound tests and `status`

- Files: `tests/test_job.py`, `tests/test_evidence.py`, `tests/test_lifecycle_orchestration.py`,
  `controller/cli.py`, `controller/observe.py`, `tests/test_cli.py`, `tests/test_observe.py`.
- Done when:
  - E.3's tests pass;
  - E.4's text and `--json` tests pass;
  - the full sharded suite passes.

### CP7 -- documentation and full verification (terminal)

- Files: the guides named in Design F, `docs/README.md`, `docs/adr/0008-controller-settings-file.md`.
  `README.md` is changed only if it names a command whose usage changed.
- Done when:
  - the docs match the code;
  - the milestone narrative has its `## Release notes` section;
  - the goldens pass `--check`;
  - the full sharded suite passes;
  - `git diff a47e695 -- .workflow-controller/ pyproject.toml setup.py .github/workflows/` is
    empty.

## Decisions for the reviewer and the user

1. **One milestone, not four.** C3 bundles four independent pieces, and the roadmap names them as
   one step. They share the release and the documentation pass. The pieces touch mostly separate
   files, so the checkpoints can be reviewed independently. Recommended. The alternative is to
   split C3 into C3a (settings and telemetry) and C3b (release notes and patches), with two
   releases.
2. **A user-level file only.** The Controller runs from one shared install for every repository
   and both lanes. Its tunables are properties of the machine and the operator, not of a
   repository. A per-repository layer adds a precedence level and a new location. Writing into a
   repository's tree is out of the question: it is a tracked-tree change. Recommended. The
   alternative, if the user wants per-repository routing, is a read-only
   `<runtime>/repositories/<key>/settings.json` layer between the CLI and the user file, which is
   never filled.
3. **Routing moves into the settings file, and `--routing-config` stays.** The lanes pass
   `--routing-config .controller/routing.json` today. Keeping the flag, with a whole-section
   replacement, means nothing breaks. Its contents can be moved into the settings file whenever
   the operator likes. Recommended. Deprecating the flag is left for later.
4. **The policy opt-in and a later cutover, rather than changing this repository's policy here.**
   The driving Controller (1.4.1) parses this branch's committed policy at every branch step, and
   refuses unknown keys and placeholders. Changing it here would stop this milestone's own
   lifecycle before readiness. This is the same reason C1 used a cutover. Recommended. The cost
   is that 1.5.0's notes are copied by hand one last time.
5. **The release reads the notes from the squash commit bodies of the release range.**
   (Revised in round 1, R1-006, and in revision 12 by the user's decision, Decision 11.) The
   roadmap says "the squash commit's body", and that is now the design. Revision 1 read the
   merged pull request's body instead, claiming the release job's token could read it. That
   claim was not verified: the publish job's token is `contents: write` only
   (`main.yml:108-111`), which sets `pull-requests` to `none`, and this plan does not change
   `.github/workflows/`. Revisions 2 to 11 read the milestone narrative from the marked commit's
   tree, because GitHub wraps the squash commit body and a list or table would be mangled.
   Revision 12 measured the wrap (Investigation): it breaks only lines longer than 72
   characters, at spaces, and keeps every existing line break. So readiness refuses a notes
   line over 72 bytes (I8; bytes since review round 12, LPR12-O1), the squash commit then carries the block byte for byte, and
   the release reads it there, checked against the marker's digest. Round 2 (R2-002) widened
   the read from the released commit alone to every commit of the release range; round 4
   (R4-002) bound the notes to readiness's marker and digest. Both stay. The release needs no
   forge read, no new permission, no tree read and no policy history. The cost is the 72-column
   rule for the notes section, and a measured but undocumented GitHub behaviour, which the
   digest guards: a change in it refuses the release, it never publishes altered notes.
   Recommended, and the user's decision.
6. **Missing, unreadable or unverified notes always refuse an opted-in release.** (Revised in
   round 4, R4-001, and in revision 12 by the user's decision, Decision 11.) When no commit of
   the range carries a notes block, or a block cannot be read or verified (a failed Git
   command, a message holding a marker line that is not UTF-8, a malformed marker, a digest
   mismatch, a block with empty notes, or on `RESUME` a tag message that cannot be read or
   that differs from the notes recomputed for the tag), the publish refuses before
   creating anything (D.3), and names the fix: the operator supplies the notes. Two refusals
   name no work item a supplied block could supersede, a malformed marker line that names no
   work item and a non-UTF-8 message holding a marker line, so their messages name the
   policy's explicit opt-out for that release instead (review round 12, LPR12-001). Only
   marker lines are read: marker text quoted mid-line, and a message with no marker line in
   any encoding, never refuse. Revision 4 published with a louder warning
   instead. That warning arrived after the release existed, and made the missing notes durable
   in the tag, which is never rewritten. Revisions 5 to 11 published with empty notes when the
   notes were provably absent, which needed the narrative scanning; Decision 11 drops both.
   A refusal leaves `main`'s release pending but repairable: a rerun for a transient failure,
   or a trunk commit that supplies the notes (a block, which supersedes an older one for the
   same work item) or, as an explicit choice, drops the placeholder, the only repair for the
   two refusals no block can clear. Every refusal clears for
   the release that hit it, and none persists into later releases. Recommended, and the user's
   decision.
7. **Telemetry lives in the job records, with no ledger.** One authority, and the command
   derives old jobs from their streams. C8 can add an index later if reading every record gets
   slow (852 records today). Recommended.
8. **The `worker` block keeps its last-result meaning.** Changing `num_turns`/`duration_ms`
   to session totals would silently change what 1.4.x readers and existing tests mean by them.
   The new `telemetry` block carries the totals. Recommended.
9. **Minor release.** The title is `feat:`, so `main.yml` releases **1.5.0** from `v1.4.2`.
10. **Only writing commands fill the file.** The roadmap says the Controller writes every missing
    setting. Doing it from `status` or `follow` would make an observation command write outside
    the runtime root, against roadmap principle 8. The commands that already write fill it, so the
    file still migrates on the first real step after an upgrade. Recommended. The alternative is to
    fill on every command.
11. **The user's decision (2026-09-30): a simplified release-notes design.** After local
    review round 11, the user decided:
    - the GitHub release text comes **only** from the release's squash commit body: the
      milestone's notes section that readiness wrote into the pull request body. The readiness
      body and its marker and trailer rules stay (D.2, I8);
    - if that notes section is missing or cannot be read for an opted-in policy, including on
      `RESUME` with an unreadable tag message, the release **refuses** before publishing and
      names the fix: the operator supplies the notes. No silent empty notes (D.3 step 6,
      Decision 6);
    - the milestone-narrative scanning is **dropped entirely** and deferred (Non-goals). That
      means no collecting of the narratives added in the release range, no lost-squash-body
      recovery, no backfill detection, no path, heading or template matching against the
      release policy or its history, and no non-ASCII path handling for it;
    - everything else in C3 stays as it was: the settings file v1, telemetry v0, the 1.4
      patches, and the parts of the release-notes design the simplified path still needs.

    How revision 12 applies it:
    - **Kept**: D.1's policy keys; D.2's readiness body, its snapshot rule (LPR9-001), the
      start marker with the work-item id and digest, the trailer check (I8) and its isolation
      from Git configuration (LPR5-003); the release range (`Classification.release_range`,
      R2-002, R3-002); the marker's token checks (LPR5-004) and whitespace-normalised matching
      (R4-002); the refusal before any tag or release (R4-001); `RESUME` reusing the tag's
      message (R3-001); `--cleanup=verbatim`; the multi-milestone `### <id>` rendering; the
      cutover (D.4).
    - **Removed**: reading the narrative from the marked commit's tree, the template set over
      every committed policy version and its narrow reader, `git log --full-history` on the
      policy, the added-path listing (`git diff-tree -z`), the unbound-narrative check, the
      backfill declaration (`release-notes-excluded`), the **absent** outcome, the
      branch-point assumption (LPR10-O1), and every CP4 test, guide item and functional-review
      item that existed only for them.
    - **Added**, because reading the commit body needs them: the 72-column, trailing-whitespace
      and no-marker-text line rules at readiness (I8), measured against GitHub's wrap
      (Investigation); the block parser in one module shared by readiness and the release
      (`controller/release_notes.py`); the rule that the newest block of a work item
      supersedes an older one, so a later trunk commit can supply the notes; and `tools/release.py
      notes-block`, which renders an operator's notes as a checked block.

### Review round 1 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

Every finding was checked against the repository at `a47e695` and accepted. None is rejected.
- **R1-001**: reproduced from the fixture (`num_turns` 8, `usage.output_tokens` 10729, cumulative
  figures equal to result 0's). Investigation records the exception; I7 takes the maximum and
  adds `cumulative_not_advanced`; Design C's `session_telemetry` output takes the maximum too
  (completed in round 2, R2-001); CP3 checks the fixture's exact figures.
- **R1-002**: reproduced (`git interpret-trailers --parse` prints nothing for the whole body; a
  lone paragraph is read as the subject, so the per-paragraph parse feeds a blank line first).
  Option (a), per paragraph: I8, D.2, the Goal and CP4.
- **R1-003**: forward-only migration by per-key default `generation` and `_table_generation`
  (A.2-A.5); `clean` refuses on a file filled by a newer release; CP1's two-release test.
- **R1-004**: A.4 and `clean` run their whole read-modify-write under the lock (I4); CP1 races
  `clean` against a fill.
- **R1-005**: I5 removes `WORKFLOW_CONTROLLER_SETTINGS` in the shared setup, and the guard uses a
  sentinel.
- **R1-006**: the token claim was unverified; the design no longer needs the forge (D.3,
  Decisions 5 and 6), and the failure kinds are distinct and each tested (CP4).
- **R1-007**: the record's `drain_detach_seconds` (B, CP2).
- **R1-008**: the incoherent-ledger gate names a way out per stage that the Workflow accepts at
  the gate's phase (E.2, CP5; corrected in round 2, R2-003, since rerunning the local review is
  refused there).
- **R1-009**: I1 states the `default=None` flags and that the table's bounds apply to the file
  only, with argparse exit 2 for a non-positive flag (the suite passes `--timeout 30`), stated as
  a behaviour change in round 2 (R2-007).
- **R1-010**: I2's wording.
- **R1-011**: the last-result read is `controller/worker_stream.py:707`; `worker.py:184-192` holds
  the field list. Corrected in the Investigation.
- The architecture note on `apply_process_defaults`: B states it is set once before any thread,
  and pins its single caller.

### Review round 2 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

Every finding was checked against the repository at `a47e695` and accepted. None is rejected.
- **R2-001**: Design C's block said `<last>` for API time, cost and `modelUsage`, against I7.
  It now says the maximum over all results (per model and per field), and lists
  `cumulative_not_advanced` among the problems. CP3 adds the per-model maximum case.
- **R2-002**: confirmed in `release_txn.py:346-366,433-455` (the bump covers
  `first_parent_subjects(base_commit..commit)`) and `main.yml:28-30` (`concurrency:
  main-release`, `cancel-in-progress: false`). D.3 now collects the narratives each commit of
  `classify`'s range **added** (`--diff-filter=A`), in commit order, each under its work-item id
  when there are several, and states the no-base-tag case. "Exactly one matches" is gone from the
  outcomes. CP4 adds the `docs:`-after-squash, two-milestone, modify-only and no-base-tag tests,
  and the functional review a `docs:`-after-squash range. Round 3 revised how the range is
  obtained: `classify` returns it as `Classification.release_range` on `RELEASE_DUE` (R3-002),
  so D.3 still computes no second range; `RESUME` reuses the tag's message instead of a range
  (R3-001); and the no-base-tag case is stated for adopters too (R3-003). Round 4 replaced the
  added-path selection with the readiness marker and its digest, keeping the added-path listing
  as a diagnostic only (R4-002).
- **R2-003**: confirmed: `/review-plan` refuses outside `AWAITING_LOCAL_PLAN_REVIEW`, and
  `/review-implementation` writes the ledger only at `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`
  (`.claude/commands/review-implementation.md:371-375`). E.2 now names the `/milestone-plan
  <id>` withdrawal for the plan stage (`.claude/commands/milestone-plan.md:117-150`) and
  explicit user resolution for the implementation stage, where no command moves the phase back
  (`MILESTONE_WORKFLOW.md:405-437`). CP5 pins both texts.
- **R2-004**: reproduced with Git 2.55 (`Note: this release changes the default.` and
  `https://example.com/x` each parse as a trailer after a blank line), and the 56 paragraphs of
  `docs/releases/1.3.0.md` to `1.4.2.md` all pass. The rule is kept; D.2 names the two shapes and
  the gate says to reword or join the paragraph; CP4 tests both; CP7's guide names them.
- **R2-005**: the first suggestion, a table compatibility rule (A.3), pinned by the table
  test. It is preferred to having N ignore a newer default, which would make N silently apply a
  different value from the file's. I2 now lists the malformed bookkeeping shapes; CP1 tests them.
- **R2-006**: I5's setup is `tests/__init__.py`; CP1's files and the Artifact declaration are
  updated.
- **R2-007**: confirmed at `controller/cli.py:249,285`. I1 states the positivity check as a
  behaviour change; CP2 tests the three flags at `0`.

### Review round 3 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

Every finding was checked against the repository at `a47e695` and accepted. None is rejected.
- **R3-001**: confirmed. `main.yml:106` publishes `RESUME`; its target is the tag's commit
  (`release_txn.py:139-141`); `classify`'s base is then the resumed tag, unsettled, so no range
  is computed (`:436-444`); and the `found is None` branch renders the notes afresh
  (`:714-722`). Option (a): on `RESUME` with no release, D.3 uses the existing annotated tag's
  message as the release notes, with the **reused from tag** outcome, and a non-annotated tag
  or failed read is the `UNAVAILABLE` warning, never a silent **absent** (round 4, R4-001,
  made that case a refusal before `create_release`). Checking option (a)
  found a further defect, fixed in the same place: `git tag -a -m` strips `#`-leading lines
  (measured, Git 2.55), so the `### <work_item_id>` lines would have been missing from the tag
  message while present in the release. `create_annotated_tag` now passes `--cleanup=verbatim`
  (Investigation, D.3, Migration). CP4 adds the resumed-publish, lightweight-tag and
  heading-survival tests; the functional review adds an interrupted-then-resumed publish.
- **R3-002**: confirmed (`Classification` at `release_txn.py:110-129` has no base field;
  `base_commit` is local at `:438`). CP4 adds `Classification.release_range`, set on
  `RELEASE_DUE` only, and factors the highest-ancestor-tag selection into one helper so the
  committed-version trigger, which computes no range today, gets the same base. CP4's files and
  classify tests name it.
- **R3-003**: D.3 and CP7's `docs/guide/ci-and-releases.md` state that a repository with no
  release tag yet collects every matching narrative its history added, as intended.
- **R3-004**: E.2's plan-stage text adds that the withdrawal discards both recorded stages and
  that the withdrawn content can never re-bind unchanged (`milestone-plan.md:156-160`).
- The R2-002 resolution above is annotated with round 3's changes, so it stays accurate.

### Review round 4 (MANUAL_EXTERNAL_PLAN_REVIEW, `REVISE`) -- resolutions

The manual external review of revision 4 raised four Important findings. Each was checked
against the repository at `a47e695` and accepted. None is rejected. Revision 5 numbers them
R4-001 to R4-004 in the order the feedback lists them.
- **R4-001** (unreadable notes published): confirmed in revision 4's D.3 step 5 and its
  `RESUME` rule, which rendered an empty `{release_notes}` after a Git failure or an unreadable
  tag and published. `publish` creates the tag before the release
  (`controller/release_txn.py:680-705`), and a tag is never rewritten, so the missing notes
  became durable. D.3 now refuses with `ReleaseTransactionError` before `create_annotated_tag`
  (`RELEASE_DUE`) or `create_release` (`RESUME`), and keeps **absent** only for a range whose
  every commit message was read and holds no marker. Decision 6 is revised and names the ways
  out; CP4 tests each refusal with nothing created.
- **R4-002** (notes not bound to an accepted milestone or its validated bytes): confirmed:
  revision 4 took any narrative a commit of the range added, and read it at the release commit.
  D.2's start marker now carries the work-item id and the SHA-256 of the validated section. D.3
  publishes a narrative only through such a marker, reads it from the marked commit's tree, and
  checks the digest, so a backfill binds nothing and a later edit is never published. The
  marker survives GitHub's reflow: the body of `0de0fd5` shows the reflow breaking only at
  spaces, and the new marker passes `git interpret-trailers --parse` whole and reflowed
  (measured, Git 2.55). The added-path listing stays as a diagnostic that names unbound
  narratives. CP4 adds the backfill, post-readiness-edit and reflowed-message tests.
- **R4-003** (routing breaks the unknown-key rule): confirmed at `controller/routing.py:258-262`
  (unknown route field) and `:299-302` (unknown role), both refused by `parse_routing_config`.
  A.5 now parses the settings file's routing section with `unknown="ignore"`, warning by dotted
  path; `--routing-config` keeps the strict parse. Route values are free strings (`:208`), so
  only keys needed the change. Role names and route fields join the pinned table, so adding
  one raises `TABLE_GENERATION` and older releases' `clean` refuses. CP1 adds the two-generation
  routing test.
- **R4-004** (no telemetry failure boundary): confirmed: both completion paths
  (`controller/job.py:5000-5009`, `:3655-3664`) build the record and persist it in one step,
  and revision 4 specified no guard. Design C adds `job._telemetry_block`, which catches every
  `Exception` into a `failed` block with a `telemetry_failed` problem; I6 states the boundary;
  CP3 injects failures on both paths and compares status, outcome, events and reconciliation.

### Review round 5 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

The local review of revision 5 raised one Important and three Optional findings. Each was
checked against the repository at `a47e695` and accepted. None is rejected.
- **LPR5-001** (the settings routing section handed to a parser that refuses it): confirmed.
  `parse_routing_config` takes text (`controller/routing.py:271`, `json.loads` at `:284`),
  refuses an absent `schema_version` (`:291-294`), and raises `RoutingConfigError` with "the
  routing config {path} cannot be used" (`:246-250`). A.5 now names the mechanism: the field and
  role checks move into `routing.validate_routing_mapping`, which takes the parsed mapping,
  `require_schema_version` and `unknown=`. `parse_routing_config` calls it strictly and keeps
  `--routing-config` byte for byte; the settings section calls it with no `schema_version` and
  `unknown="ignore"`, and its refusals become `SettingsError` naming the settings path and the
  dotted key. A.3's row, B's routing item and CP1's name are updated. CP1 adds the
  default-filled load, the `SettingsError` refusal and the same-route-through-both-sources test.
- **LPR5-002** (leaf timeouts bound at import): confirmed at `controller/gitrepo.py:38-53`
  (`_DEFAULT_RUNNER` built at import with the timeout as a default argument) and
  `controller/forge.py:169`. B now requires the values to be read at call time
  (`subprocess_runner`'s `timeout` defaulting to `None`, meaning the process-wide value), and
  CP2 tests a runner and a forge built before `apply_process_defaults`.
- **LPR5-003** (the trailer check inherits Git configuration): confirmed:
  `subprocess_runner` sets only `LC_ALL`, `GIT_TERMINAL_PROMPT` and `GH_PROMPT_DISABLED`
  (`controller/gitrepo.py:43-44`), and a global, repository or `GIT_CONFIG_COUNT`
  `trailer.separators = :=` makes `see=thing` parse as `see: thing` (measured, Git 2.55). D.2
  now runs the parse with every `GIT_*` variable removed, `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_CONFIG_GLOBAL=/dev/null` and no discoverable repository, under which all three parse to
  nothing (measured). CP4 tests the three sources.
- **LPR5-004** (the marker's work-item id rendered into a path unchecked): accepted. D.3 step 2
  now requires the `work_item=` token to match `repo_policy.WORK_ITEM_ID_RE`
  (`controller/repo_policy.py:47`, no `/` or `.`) and the `sha256=` token to be 64 lowercase
  hexadecimal digits, before step 3 renders a path; anything else is a malformed marker, a
  refusal. CP4 tests both.

### Review round 6 (MANUAL_EXTERNAL_PLAN_REVIEW, `REVISE`) -- resolutions

The manual external review of revision 6 raised one Important and one Optional finding. Both
were checked against the plan and the repository at `a47e695` and accepted. None is rejected.
- **MPR6-001** (an unmarked milestone published with empty notes): confirmed in revision 6's
  D.3 steps 4 and 6. Step 4 listed a narrative the range added with no marker only as a
  diagnostic, and step 6 called a range with no marker authoritatively absent and published it,
  so a squash commit whose message lost the pull request body published its milestone with no
  notes, durably in the tag. Nothing in the history distinguishes that commit from a backfill.
  Step 4 is now a check: an unbound narrative refuses unless a backfill declaration
  (`release-notes-excluded work_item=<id>`, D.3 step 2) in a trunk commit of the range names
  it. The ways out are a later trunk commit carrying the pull request's own readiness marker
  (the notes then publish, digest-verified) or a declaration, or dropping the placeholder.
  Decision 6, the Goal, CP4's name and tests, CP7's guide item, R9, the functional review and
  the migration notes are updated. CP4 adds the lost-body refusal and its marker recovery,
  the declared and undeclared backfill, and the no-base-tag chain with an unmarked narrative.
- **MPR6-O1** (a new key directly under `routing` refused by an older release): confirmed in
  revision 6's A.5, which refused every key under `routing` other than `default` and `roles`,
  so a newer release adding one would make the shared file unusable to an older one, the case
  R4-003 fixed for roles and fields. That key is now ignored with a warning like any other
  unknown key there, and is part of the pinned table. `routing.schema_version` alone stays
  refused, reserved as the `--routing-config` file's header. CP1's shared-file routing test and
  R2 are extended.

### Review round 7 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

The local review of revision 7 raised one Important and two Optional findings. All three were
checked against the plan and the repository at `a47e695` and accepted. None is rejected.
- **LPR7-001** (a milestone with no notes section refused at release): confirmed in revision 7's
  D.3 step 4, which called every unmarked added narrative unbound whatever it held, against D.2's
  `absent`/`empty` bodies and Decision 6. None of the 13 narratives in
  `docs/milestones/completed/` has a `## Release notes` section, and each milestone's squash
  commit adds its own (`0de0fd5`, `e8cd8f9`), so every post-cutover release without notes, and
  an adopter's first release, would have needed a declaration. Step 4 now runs D.2's extraction
  on the narrative at the adding commit's tree: no section or an empty one is **without notes**,
  named in the output and needing no declaration; only a non-empty section is unbound; a failed
  read refuses. The Goal, D.3 steps 1 and 6, Decision 6, R9, the CP4 name and tests, CP7's
  guide item and the migration notes are updated.
- **LPR7-O1** (A.3 and B named only unknown roles and route fields): the `routing` row and B's
  routing item now also name unknown keys directly under `routing`, as A.5 does.
- **LPR7-O2** (a lost body on a commit that only modifies a narrative): the plan takes the
  documented-assumption route. D.3 step 4 states that the check assumes an accepted milestone
  adds its narrative, with this repository's evidence, and CP7's guide item states it. Extending
  the check to modified paths would make every later edit to an old narrative that touches its
  section need a declaration, the ceremony LPR7-001 removes.

### Review round 8 (MANUAL_EXTERNAL_PLAN_REVIEW, `REVISE`) -- resolutions

The manual external review of revision 8 raised two Important findings and one Optional one,
the three Optional findings of local round 8's `APPROVE` (LPR8-O1 to O3) raised again. All
three were checked against the plan and the repository at `a47e695` and accepted. None is
rejected.
- **MPR8-001** (a notes path or heading changed inside the release range): confirmed in
  revision 8. D.2 reads the policy committed at the acceptance, and D.3 steps 3 and 4 used the
  release commit's. A narrative added under a path the release's template no longer matches was
  never listed, and one whose section sits under a renamed heading read as without notes, so a
  squash commit that lost its body published with empty notes. D.1 permits both changes.
  Step 4 now matches added paths against every `release_notes` path and heading committed at
  the base and at each commit of the range, and calls a narrative without notes only when it has
  none under every heading; step 3 reads each marked narrative under its own commit's policy;
  an unreadable or unparsable policy of the range refuses. (Revision 10 corrects this
  resolution's premise: readiness reads the binding's snapshot, which can predate the range.
  The set now spans every committed version of the policy, and step 3 lets the digest choose
  the template; see review round 9.) Decision 6 records the rejected
  alternative, refusing any range whose policy changed. The Goal, step 6, R9, the CP4 name and
  tests, CP7's guide item and the functional review are updated.
- **MPR8-002** (a non-ASCII template path C-quoted by `git diff-tree`): confirmed. D.1 allows any
  normalised POSIX path, and `git diff-tree --name-only` quotes a path with non-ASCII bytes
  under the default `core.quotePath` (measured, Git 2.55), so the added path never matched the
  template. Step 4 now lists the paths with `-z`, splits on NUL and decodes each as UTF-8. The
  alternative, restricting the policy path to ASCII, would refuse a legitimate layout. CP4 tests
  a non-ASCII template with a lost body.
- **MPR8-O1** (a path whose `{work_item_id}` segment fails the id pattern): step 4 now defines it
  as not a narrative, skipped and named in the output: readiness never runs for such an id
  (`controller/milestone_branch.py:307`), and step 2 refuses it in a marker.
  CP4 tests it.

### Review round 9 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

Local round 9 reviewed revision 9. It raised one Important finding and two Optional ones. All
three were checked against the repository at `a47e695` and accepted. None is rejected.
- **LPR9-001** (D.2 names no policy, and the one readiness uses can fall outside step 4's set):
  confirmed. `_squash` (`controller/milestone_branch.py:1208-1211`) and the green-checks gate
  (`:1410`) read `binding_policy(record)` (`:392-401`). That is the snapshot written at bind
  (`:1518`, `:1523`) and adoption (`:1863`, `:1905`, `:1923`), documented at
  `docs/guide/milestone-branches.md:20`. Revision 9's premise, "the policy at the acceptance",
  was stated nowhere in D.2. It did not hold for a snapshot older than the base tag, which
  gave a silent omission, and step 3's squash-commit policy gave a spurious refusal. The plan
  now has one rule: D.2 reads the snapshot. D.3 step 4's set spans every committed version of
  the policy (`git log --full-history` on the policy file), which provably includes every
  snapshot. Step 3 verifies a marker under whichever template of that set matches its digest.
  Decision 6, D.4, step 6, R8/R9, CP4's name and tests, CP7's guide item and the functional
  review are updated.
- **LPR9-O1** (strict parse of historic policies): accepted. Step 4 reads only the
  `release_notes` sub-object with D.1's validator. A historic version refuses only when it is
  not JSON, or when that sub-object (or a key on the way to it) is present and malformed. The
  guide names the one residual refusal and its way out.
- **LPR9-O2** (no base commit): resolved by LPR9-001's change. The set no longer depends on the
  base, and step 4 says so. CP4 tests it.

### Review round 10 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

Local round 10 reviewed revision 10. It raised one Important finding and two Optional ones. All
three were checked against the repository at `a47e695`. None is rejected; LPR10-O1's suggested
bind-time check is deferred with a reason.
- **LPR10-001** (the history-wide set makes the narrow read's refusal permanent): confirmed. The
  snapshot is taken through `read_committed_policy` at bind, re-bind and adoption
  (`controller/milestone_branch.py:1518`, `:1813`, `:1863`, `:1905`), which parses strictly and
  refuses (`:1906`), and readiness re-parses it (`:392-401`). A version the release cannot read
  was therefore never a snapshot. Taken: the first way. Step 4 skips such a version and names it,
  and takes `path` and `heading` from any `release_notes` object whose both are strings, without
  D.1's validator, which covers a later, stricter validator. A failed Git read still refuses.
  Step 6's refused list, its closing paragraph, Decision 6, R9, the CP4 name and tests and CP7's
  guide item are updated; CP4's test is the two-release history the review asked for.
- **LPR10-O1** (the proof assumes a branch point on the published trunk): confirmed
  (`:1605-1608`, `:1782`, `:1813`, `:1837-1840`, `:1856-1857`). The assumption is stated beside
  the proof in step 4, with its fail-closed and silent consequences, and CP7's guide names it.
  The bind-time check is not added here: it changes bind's refusals, outside this milestone's
  release-notes scope, and step 4 records it as a follow-up.
- **LPR10-O2** (the `--full-history` test describes a case a binding cannot snapshot): accepted.
  The CP4 test is reworded as a check that `--full-history` lists a reverted side-branch version.

### Review round 11 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) and the user's decision -- resolutions

Local round 11 reviewed revision 11. It raised one Important finding and one Optional one, with
missing tests and an architecture note. On 2026-09-30, after the round, the user decided to
simplify the release-notes design (Decision 11). Revision 12 applies that decision. A finding
that concerns only the dropped narrative scanning is resolved by the decision, not designed
around. A finding that still applies to the simplified path is resolved on its merits. Every
finding was checked against the plan and the repository at `a47e695`. None is rejected.
- **LPR11-001** (the narrow reader's shape filter is stricter than D.1): the premise is
  confirmed. `repo_policy.template_placeholders` (`controller/repo_policy.py:88-98`) refuses
  unknown and disallowed placeholders but requires none, so D.1 admits `NOTES.md` and
  `docs/{work_item_id}/{work_item_id}.md`, which revision 11's filter skipped. Its "whole
  segment" wording also excluded D.4's own template. **Resolved by the user's decision.** The
  narrow reader, its shape filter, the history-wide template set and step 4's matching existed
  only for the narrative scanning, and revision 12 removes them. The release no longer reads
  any notes template (D.3), so D.1's template has one consumer, readiness, which renders the
  snapshot's template with the bound work item and reads that one path. No second consumer
  remains that could disagree with D.1. The part that still bears on the simplified path is
  what D.1 accepts. D.1 now says that any shape it admits renders to one path, and CP4's
  policy tests pin the two templates the finding names as accepted. One readiness test uses
  D.4's exact template.
- **LPR11-O1** (a template that never matches the narrative's real location): **resolved by
  the user's decision.** It concerned matching added paths against templates, which is
  dropped. On the simplified path a narrative that is not at the snapshot's path gives
  readiness's `absent` body, and the release then refuses as missing unless notes are
  supplied (D.2, D.3 step 6). That is a refusal, not a silent omission.
- **Missing tests**: the D.1 tests are in CP4 (above). The publish tests with D.4's template
  and a lost-body narrative concerned the dropped scanning. The simplified path's lost-body
  case is CP4's **missing** test: a squash commit whose message is only its title is refused,
  and a later commit carrying the pull request's block publishes it.
- **Architecture note** (one shape rule shared by D.1's validator and the reader): the reader
  is gone. The same concern, a writer and a reader that drift apart, applies to the block, and
  `controller/release_notes.py` holds both its renderer and its parser (D.2). CP4's round-trip
  test runs the parser on a body readiness rendered, wrapped as GitHub wraps it.
- **Migration concern** (a policy D.1 accepts makes a lost-body narrative publish as absent):
  **resolved by the user's decision.** There is no **absent** outcome. A range with no notes
  block refuses (D.3 step 6). The residual case, a lost body in a range where another
  milestone's block is present, is stated in Non-goals as deferred.

**Earlier findings that concerned only the narrative scanning** are resolved by the same
decision. Their subsections above stay as the record of those rounds:
- **MPR6-001** (an unmarked milestone published with empty notes): the backfill declaration
  and the unbound-narrative check are removed. The **absent** outcome that caused the finding
  is removed too, so a range with no block refuses (D.3 step 6).
- **LPR7-001** and **LPR7-O2** (a milestone with no notes section; a lost body on a modified
  narrative): no narrative is read at release. A milestone with no notes section gives a
  **missing** refusal unless another block is in the range, or notes are supplied. This is
  the user's "no silent empty notes".
- **MPR8-001**, **MPR8-002** and **MPR8-O1** (the notes policy changed inside the range; a
  non-ASCII template path; a path whose segment is not a work-item id): no path or heading is
  matched at release, and `git diff-tree` is not run.
- **LPR9-001**: its readiness half stays (D.2 reads the binding's snapshot). Its release half,
  the template set and the digest choosing a template, is removed.
- **LPR9-O1**, **LPR9-O2**, **LPR10-001**, **LPR10-O1** and **LPR10-O2** (the narrow lenient
  read, the base-independent set, skipped policy versions, the branch-point assumption,
  `--full-history`): no policy history is read at release.
- **R2-002**, **R3-003** and **R4-002** stay on their merits, restated for blocks. The release
  still walks every commit of the range, and with no base tag the whole first-parent chain. It
  still binds the notes through readiness's marker and digest. It now reads the bytes from the
  commit message and not from the commit's tree, which the measured wrap and I8's 72-column
  rule make exact.

### Review round 12 (LOCAL_MODEL_PLAN_REVIEW, `REVISE`) -- resolutions

Local round 12 reviewed revision 12. It found the simplified design sound, re-verified the
measured wrap on four more pull request bodies, and raised one Important finding and four
Optional ones. Revision 13 accepts all five. Every finding was checked against the plan and the
repository at `a47e695`; none is rejected.
- **LPR12-001** (marker text anywhere refuses, and the named fix fails): the premise is
  confirmed. Revision 12's step 2 matched "any other text beginning `<!-- workflow-controller:
  release-notes`" anywhere in a message, and decoded every message as UTF-8, so a pull request
  body quoting the marker format, or a non-UTF-8 message unrelated to notes, gave a refusal
  that no supplied block could supersede, while its message named "supply the notes". The
  plausible first trigger is real: the D.4 cutover pull request and the 1.5.0 notes `docs:`
  pull request both describe this feature and both land in 1.6.0's range. **Accepted.** D.3
  step 2 reads marker lines only (a line whose first bytes are the marker text) and searches
  the raw bytes first, requiring UTF-8 only from a message that holds a marker line. Step 6
  lists the two refusals a supplied block cannot clear, a marker line that names no work item
  and a non-UTF-8 message holding a marker line, and their messages name the policy's opt-out
  as the fix. Decision 6, the CP4 name and tests, R9 and CP7's guide item say the same. The
  anchoring loses nothing readiness writes: every marker starts a line, and the marker text is
  39 characters with no break before its end. CP4 adds the reviewer's three tests and checks
  each refusal's named fix.
- **LPR12-O1** (count the 72-column limit conservatively): **accepted.** The limit is now 72
  bytes of UTF-8 (I8, D.2's line rules, the Goal, CP4's name, R8), which is never more than any
  plausible count of GitHub's, and tabs and carriage returns are refused. A line that would
  otherwise reach release with an unmeasured length is refused at readiness, and
  `tools/release.py notes-block` applies the same rule, so a supplied block cannot fail the same
  way. CP4's line-rule tests cover a 72-character line with an accented letter, a tab and CRLF
  line endings.
- **LPR12-O2** (two blocks for one work item in one commit): **accepted.** Together they are
  one damaged block of that work item (D.3 step 2), so a later valid block supersedes them,
  like any damaged block. CP4 tests the superseded case.
- **LPR12-O3** (`git commit` strips `#` lines): **accepted.** CP7's `ci-and-releases.md` item
  says that an operator commit made with `git commit` needs `--cleanup=verbatim`. The
  functional review's readiness case now carries a `#`-leading heading line, which checks the
  parser and the verbatim tag locally. GitHub's squash of such a line is measured only by the
  first real release whose notes hold one. If GitHub changed the line, the digest refuses that
  release, and it never publishes altered notes.
- **LPR12-O4** (a `work_item=` token over 72 characters): **accepted** as a guide note.
  `milestone-branches.md` says that an id longer than 62 characters gives a token whose
  wrap is not measured. No id in this repository comes close. A readiness check is not added:
  `PR_MARKER` has the same exposure today, and the digest still refuses rather than publishing.
- **Missing tests**: the LPR12-001 and LPR12-O2 tests are in CP4 (above).

### Review round 13 (MANUAL_EXTERNAL_PLAN_REVIEW, `REVISE`) -- resolutions

The external round reviewed revision 13 (bundle `3d8c895c`, content id `d20c9c8d`), after
local round 13's `APPROVE`. It accepted the simplified range, digest and supersession rules,
and raised two Important findings and one Optional one. Revision 14 accepts all three. Each
was checked against the plan and the repository at `a47e695`; none is rejected.
- **MPR13-001** (an empty operator block counts as included): confirmed. Revision 13's
  `notes-block` applied the trailer, length and line checks, none of which needs a line, and
  step 6 counted any block with a matching digest as **included**, so an empty file gave a
  block whose digest is the valid SHA-256 of the empty string, and the release published the
  template's fixed text with no notes. Readiness itself never writes such a block: an empty
  section gives today's body with no marker (D.2). **Accepted.** The shared notes checks
  require at least one non-blank line (D.2), `notes-block` refuses an empty or blank-only
  file (D.3 step 6), and the parser treats a block with empty notes as a damaged block of its
  work item (D.3 step 2), so it is never included and a later non-empty block supersedes it.
  The Goal, Decision 6, the CP4 name, R9, CP7's guide item and the functional review say so;
  CP4 adds the reviewer's empty-file test and the empty hand-written block.
- **MPR13-002** (`RESUME` trusts any annotated tag): confirmed. The publish reads the policy
  committed at its checked-out commit (`tools/release.py:280-294`, `committed_policy(repo_root,
  "HEAD")`, called from `cmd_publish` at `:354`), and `classify` returns `RESUME` for an
  unsettled ancestor tag reached from a newer commit (`controller/release_txn.py:509-511`). A
  tag created before a trunk commit that opts in therefore held the old fixed message, which
  revision 13 published as if it carried verified notes. **Accepted**, with the reviewer's
  second option, verification rather than a blanket refusal, so the transient-failure resume
  R3-001 kept still works: before `create_release` the publish recomputes the notes over the
  tag's own range (the shared base-tag helper, applied to the tag's commit with a lower
  version), renders the current template with the tag's version, tag and commit, and requires
  byte equality with the tag's message; any difference refuses as **unverified tag** and names
  the fix, creating the release for that tag by hand. A blanket refusal of every opted-in
  `RESUME` was the alternative; it is not taken because a cancelled job between the tag push
  and `create_release` is the case `RESUME` exists for. A template change between the tag and
  the resume also refuses, which fails closed. The Goal, Decision 6, the CP4 name, R9, CP7's
  guide item, the functional review and the migration notes say so; CP4 adds the reviewer's
  tag-before-opt-in test, a differently templated tag and a resume from a later commit. While
  checking it, revision 13's note that a resumed draft keeps its notes was confirmed and made
  precise: the publish never creates a draft (`controller/forge.py:346-351`), so a draft and
  its notes are the operator's.
- **MPR13-O1** (does a damaged start marker consume its end marker?): **accepted**; it is
  also local round 13's LPR13-O1. D.3 step 2 now pairs marker lines explicitly: every non-end
  marker line opens a candidate that takes the first end marker before the next candidate, so
  a damaged start marker consumes its own end line, which is never reported again as an
  unmatched end marker, and a later valid block supersedes the damage. An end marker no
  candidate takes stays an unattributable refusal. CP4 tests both.
- **Missing tests**: the empty-file `notes-block` case and the `RESUME` case where the tag
  predates the opt-in are in CP4 (above).

## Open questions

None blocking. The open "Open decision" check of `/milestone-plan` step 5 has nothing to check:
this repository has no `docs/TECHNICAL_DECISIONS.md`, and the decisions this plan makes are
listed above for the user.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-settings-and-telemetry-artifacts.json` starts
from `generate_artifacts_declarations(..., work_item_type="product")`. It is fitted to this
plan's footprint the same way as the previous Controller milestone's declaration.

**Plan stage.**
- Protected: this plan, its registry and its mapping.
- Inherits the template exclusions. Adds these as excluded implementation content: `controller/`,
  `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`, `docs/releases/`, `pyproject.toml`,
  `setup.py` and `docs/README.md`.

**Implementation stage.**
- Protected prefixes: `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`,
  `docs/releases/` and the inherited `docs/adr/`.
- Protected paths: `README.md`, `docs/README.md`, `pyproject.toml`, `setup.py`, the four rendered
  workflows (`validate.yml`, `ci.yml`, `main.yml`, `pr-title.yml`), `CLAUDE.md` and the
  artifacts file itself.
- This milestone changes paths under `controller/`, `tests/`, `tools/` (`tools/release.py`'s
  `notes-block`, D.3 step 6; I5's environment is set in `tests/__init__.py`, not in
  `tools/run_tests.py`), `docs/guide/` and `docs/adr/`, and `docs/README.md`.

`docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md` and `docs/ai-workflow/` stay excluded, as narrative
and bookkeeping. `.github/workflows/workflow-conformance.yml` stays under the inherited `.github/`
exclusion (the Workflow Manager owns it). `scripts/`, `.claude/commands/` and `.workflow-manager/`
keep their inherited exclusion. This milestone never edits this repository's installed Workflow.

## Verification

- **Per checkpoint.** Run the named test modules, then the full sharded run
  (`python3 tools/run_tests.py`), before each checkpoint commit.
  - `PYTHONPATH=.` is never set.
  - `FORCE_COLOR` is unset.
  - The suite runs in the foreground.
- **Inside a Controller-launched worker.** The runs go through a reaping-subreaper wrapper, as
  in C1b and C2, and the checkpoint notes say so.
- **No real settings file is touched.** Each checkpoint records that
  `~/.config/workflow-controller/settings.json` did not exist before the run, or had the same
  bytes before and after it (I5).
- **Real data.** CP3 runs `telemetry` read-only against the live runtime root. The notes record
  that job `20260930T121740Z-2e7e7496` shows 144 turns and a cost of 11.66, and that
  `20260924T221915Z-2857a730` reports its `cumulative_not_advanced` problem.
- **CI.** The milestone's Draft PR must be green before functional review, and review rounds
  read the PR's checks first.
- **Functional review.** It runs in a disposable repository and a scratch settings path:
  - the settings fill, migration and clean;
  - a bad settings file refused;
  - `--routing-config` precedence;
  - `resume --drain-timeout`;
  - `telemetry` over the live runtime root (read-only);
  - `status` and `status --json`;
  - an incoherent manual-external ledger;
  - every printed hint executed;
  - readiness with notes (a `#`-leading heading line among them), with a trailer-like
    paragraph and with a notes line over 72 bytes, against a local fake forge;
  - a range whose `docs:` commit body quotes the marker mid-line: the publish includes the
    milestone's notes;
  - publish rendering `{release_notes}` from a squash commit whose message carries the block,
    wrapped as GitHub wraps it, and from a range where a `docs:` commit follows it, in a
    disposable repository;
  - a publish interrupted after the tag push and before the release, then resumed: the release
    carries the tag's message; and a tag pushed before the opt-in, resumed after it: the
    publish refuses and creates no release;
  - `tools/release.py notes-block` over an empty file refuses;
  - a range whose squash commit lost the pull request body, and one whose block's notes were
    edited in the message: each publish refuses, names the fix and creates no tag; then a
    later commit carrying the pull request's block (the first case) or a `tools/release.py
    notes-block` block (the second) publishes the notes;
  - a settings file carrying a routing role unknown to the installed release: every command
    still runs, with the warning;
  - a job whose telemetry is made to fail completes with its normal outcome;
  - two patched releases sharing one settings file.

  No throwaway pull request on GitHub is needed.

## Migration / data-integrity notes

- **The settings file.** It is new, and the first writing command of 1.5.0 creates it with the
  defaults.
  1.4.x ignores it. Uninstalling 1.5.0 leaves it in place, harmlessly.
- **Job records.** They gain the optional `controller_settings` and `telemetry` blocks and
  `worker_route.config_source`. `SCHEMA_VERSION` stays 1. 1.4.x Controllers ignore unknown
  fields, and the two versions can re-attach to each other's jobs.
- **Behaviour with no settings file edits.** Every default equals today's constant, so a 1.5.0
  run with the created file behaves as 1.4.2 does. The exceptions are the new `status` layout,
  the corrected hints and the manual-external gate text.
- **Release refusals.** With the policy opt-in, a release whose range carries no notes block,
  or whose notes cannot be read or verified, is refused before its tag exists, and the refusal
  names the fix (D.3, Decision 11). Without the opt-in nothing changes. A repository that opts
  in is never left with a tag whose notes are silently empty. A milestone whose squash commit
  lost its body is not detected when another milestone's block is in the same range
  (Non-goals).
- **A shared settings file across releases.** The newest release's routing roles and fields,
  and its other keys, are ignored with a warning by an older release (A.5), so the file stays
  usable to every release from 1.5.0 on.
- **Tag messages.** New annotated tags are created with `--cleanup=verbatim` (D.3). A template
  with no `#`-leading line and no trailing whitespace, such as this repository's, yields the same
  tag message as before. Existing tags are never rewritten. Without the opt-in a resumed release renders its
  template afresh, as today (`controller/release_txn.py:714-722`); with it, it reuses its tag's
  message, and only a message equal to the notes
  recomputed for the tag (D.3, MPR13-002), so a tag created before a repository opts in is
  refused on resume, and the operator creates that one release by hand.
- **The policy.** The new keys are optional. A policy without them behaves as today. A policy
  with them is refused by 1.4.x, which is why this repository's cutover waits for the install.
