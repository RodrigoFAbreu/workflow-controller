# Runtime state and runtime identity

> For: anyone who needs to know where the Controller keeps its state or what code it is running. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

[Back to the documentation map](../README.md)

## Controller-owned runtime state

The Controller keeps its own durable state under a runtime root: pinned
source identity, job records (`jobs/`, with abandoned originals under
`jobs/abandoned/`), per-job logs (`jobs/<job_id>/`), run records
(`runs/`), milestone binding records (`repositories/`, below) and the
pending handoff. The root is resolved by a ladder, and
the first row that applies wins:

1. `--runtime-dir`;
2. `WORKFLOW_CONTROLLER_HOME`;
3. `<origin checkout>/.controller/`, only for a **source** runtime;
4. `$XDG_STATE_HOME/workflow-controller`, or
   `~/.local/state/workflow-controller` when `XDG_STATE_HOME` is unset.

A package runtime (a pipx or other wheel install) never uses row 3, even
when its venv sits inside a Git checkout, so without the first two rows
it uses row 4. Before 1.1 a wheel installed into a venv inside a
checkout resolved to `<site-packages>/.controller`. Every acting command
from such an install failed, so that directory holds no job history, and
it can be deleted. `status` prints the resolved root and its ladder row.

Two files at the root belong to the usage budget and are shared by every
lane and repository that uses the root: `usage.json`, the readings,
reservations, ledger and spend, and `usage-events.jsonl`, the pauses,
resumes and releases (see [`usage`](commands.md#usage)). Unlike `runs/`,
`usage.json` is read by admission: deleting it forgets the other lanes'
reservations and every run's and repository's spend, so a cap restarts
from zero. Back it up with the rest of the root.

`verified-claude-builds.json` is the third file kept there: the digests of
`claude` builds that `workflow-controller verify-claude` verified (see
[`verify-claude`](commands.md#verify-claude)). The automated functional
review admits the built-in builds plus these. The file must be a regular file
owned by you and not writable by group or others; one that is not, or that
cannot be read, is ignored and the stop text says so. Deleting it only
un-admits those builds until `verify-claude` is run again. It is per runtime
root, so a source checkout and the shared install keep separate records.

Who the record is defended against: the sandboxed functional tester and
redirection. The read is checked on one descriptor (no symlink, owned by you,
not writable by others), the write refuses to go through a symlink, and a
launch refuses a runtime root inside the clone or output folder. It is not
defended against a lifecycle worker or any other process running as you:
such a process already holds your authority over the settings file and over
the Controller itself. The launch executes the resolved file it hashed, so a
re-pointed symlink cannot change what runs, but a file rewritten in place
between the hash and the exec is not caught. The installer never does that;
it writes each version to its own file.

A `usage.json` this release cannot read (corrupt, or written by a newer
release with a higher `version`) is never replaced. Deleting it is a last
resort: repairing or restoring the original record in place lets a later
sweep settle a job whose accounting was still pending, but only if no `step`,
`run` or `resume` ran on a fresh record in between. Such a sweep finds the
job's reservation unknown and marks its accounting done with no charge (an
orphan, by design, recorded as a `usage_accounting_orphan` event), and a
record restored afterwards no longer settles it. Restore the record before
the next `step`, `run` or `resume`. With
`usage.enabled: false`, `step` and `run` neither read nor sweep it. With the
budget on, admission fails closed and its message names
`usage.enabled: false` as the way to keep running. The replay sweep that
`step`, `run`, `resume` and `usage` perform skips the record with a
one-line warning (printed once per sweep, and left out of the `usage`
commands, which report the error themselves and exit `20`; a command that
sweeps more than once, such as `run` or `resume`, can print it again) and a `usage_record_unreadable` event, leaving every job's
`accounting` as it is; a release that can read the record settles it later,
exactly once.

A job record can carry a `usage_limit` block (the reset time and the window
name) when its worker reached the provider's usage limit. An `INTERRUPTED`
job with that block at the top level changed nothing, and a new `step` or
`run` repeats the step; a `FAILED` job had changed state and carries the same
block under `reconciliation_evidence.usage_limit`. The pre-state of every job
also records `tracked_changes`, the digest of each dirty tracked path, which
is how "changed nothing" is decided
([how the limit is recognised](workers.md#how-a-usage-limit-is-recognised)).
The accounting charges such a job as a job that ran, never as one that never
launched.

This tree is disposable by design. It is never part of any managed
target repository's own state, and the Controller never writes into a
target repository's `WORKFLOW_STATE.json` -- only a worker running a
real Workflow command does that. The lifecycle lock is an `flock` on the
target's existing git directory and creates nothing there. Deleting
`runs/` or `jobs/<job_id>/` loses presentation history only: no
lifecycle decision reads them.

`repositories/<repo_key>/milestones/` holds one binding record per
milestone of a policy-enabled target (`<work_item_id>.json`), its
append-only `<work_item_id>/events.jsonl`, and any
`<work_item_id>.abandoned-<n>.json` left by a re-planned id. `repo_key`
is the SHA-256 of the target's canonical `git rev-parse --git-common-dir`,
so every worktree of one repository shares it. Unlike `runs/`, these
records **are** read by lifecycle decisions: they are the Controller's
only memory of which branch and pull request belong to a milestone. Do
not delete them while a milestone is in flight, and include them in any
backup of the runtime root. If they are lost anyway (a new machine, for
example), the next step with `HEAD` on the milestone branch re-adopts the
binding and its one open pull request. A branch that exists only on the
remote, or two merged pull requests for one branch, refuses: the
Controller cannot tell which is its own, and the recovery is to restore
`repositories/<repo_key>/` from the backup. A 1.1.1 runtime ignores the
directory.

## Runtime identity

Every Controller process knows what code it is running, and records it.
There are three runtime kinds:

- **`package`**: an installed wheel. The wheel carries
  `controller/BUILD_INFO.json`, written by the build (`setup.py`'s
  `build_py` hook), which records the version, the source commit, whether
  the build's inputs had uncommitted changes, a digest of the package's
  files, the build origin (`release` or `local`) and the release tag.
  A package runtime never runs Git and never consults the checkout it was
  built from. `step`, `run` and `resume` copy the installed package into
  a snapshot and check the copy against the recorded package digest; an
  installation edited after it was built is refused (exit `20`, see [exit codes](../exit-codes.md)). A wheel
  built from uncommitted changes, or with no verifiable provenance (for
  example from an sdist), needs `--allow-dirty-source`, like a dirty
  checkout.
- **`source`**: a Git checkout whose top level holds the running
  `controller/` package, tracked. This is what `pip install -e .` gives
  you.
- **`unidentified`**: anything else, for example a wheel built without the
  hook, or a checkout that contains a stray `controller/BUILD_INFO.json`
  (delete it to run from source). Read-only commands still work and print
  the reason. `step`, `run` and `resume` refuse with exit `20`.

The Controller never reads installer metadata such as `direct_url.json`.

`workflow-controller --version` prints two lines and writes nothing:

```
workflow-controller 1.2.1
runtime: package (release v1.2.1; built from 0123456789ab; package 3f2a1c9d0b7e)
```

Line 1 is always `workflow-controller <version>`. Line 2 is one of
`package (release ...)`, `package (local build from <commit>)` with
`, uncommitted changes` when that applies, `package (local build, unknown
provenance)`, `source (<checkout> @ <commit>)` with the same suffix, or
`unidentified (<reason>)`. `status` opens with the same text:
`controller: workflow-controller 1.2.1 -- package (...)`.

Every job record carries a `controller_runtime` block (`runtime_kind`,
`version`, `source_kind`, `source_commit`, `tree_digest`,
`package_digest`, `build_origin`, `release_tag`, `generation`), and so do
`identity.json` and `SOURCE_PIN.json`. `inspect --json` and
`explain --json` carry it as `controller`. The older
`controller_generation`/`controller_source_commit`/`controller_source_tree_digest`
fields stay.

What `build_origin: "release"` proves is limited. The build sets it from
an environment variable, so any local build can claim it. The proof that
a wheel is a release is its checksum in the GitHub Release's
`SHA256SUMS`, not `--version`. Build-provenance attestation may come
later.

The version is always a plain `MAJOR.MINOR.PATCH`. Where it comes from:

- a release wheel carries its release tag's version, set at build time;
- a local wheel, and a **source** runtime, carry `pyproject.toml`'s
  static `[project].version` when it declares one (every checkout of
  this repository from before the
  [cutover](ci-and-releases.md#cutover-from-the-version-file-model)).
  Otherwise, with `dynamic = ["version"]`, they carry the highest
  `v<MAJOR.MINOR.PATCH>` tag reachable from the checkout's `HEAD`, or
  `0.0.0` when none is (a shallow clone without tags, for example). A
  checkout between two releases therefore reports the last release it
  contains; line 2 of `--version`, and `BUILD_INFO.json` for a wheel,
  tell it apart by its commit and build origin;
- a pinned source snapshot has no `.git`, so with a dynamic version it
  is derived from the origin checkout's tags at the snapshot's commit,
  and recorded in `SOURCE_PIN.json`.

The version and the generation (`controller/GENERATION.json`) are
separate. The generation is the compatibility axis that handoff and
job-record validation compare.

## The settings file

One JSON file holds the Controller's tunables and its default worker
routing. It belongs to the user, not to a repository: every repository
and every lane on the machine that runs as that user shares it. Change
it with care while another Controller runs. The design record is
[`docs/adr/0008-controller-settings-file.md`](../adr/0008-controller-settings-file.md).

### Where it is

The first of these that is set wins:

1. the global `--settings PATH` option;
2. `$WORKFLOW_CONTROLLER_SETTINGS`;
3. `$XDG_CONFIG_HOME/workflow-controller/settings.json`;
4. `~/.config/workflow-controller/settings.json`.

`workflow-controller settings path` prints the one in use. A missing
file is never an error: every setting then takes its built-in default.

### What it holds

| Key | Type | Default | Bounds | Command-line override |
|---|---|---|---|---|
| `worker.drain_detach_seconds` | integer | `10800` | 60 to 604800 | `resume --drain-timeout` |
| `worker.timeout_seconds` | integer or `null` | `null` (no limit) | 60 to 172800 | `--timeout` |
| `run.max_steps` | integer | `20` | 1 to 1000 | `run --max-steps` |
| `follow.heartbeat_seconds` | integer | `30` | 1 to 3600 | none |
| `follow.replay_events` | integer | `20` | 0 to 10000 | `follow --from-start` |
| `timeouts.git_seconds` | integer | `600` | 30 to 7200 | none |
| `timeouts.release_command_seconds` | integer | `1800` | 60 to 21600 | none |
| `timeouts.workflow_query_seconds` | integer | `120` | 10 to 3600 | none |
| `forge.pr_list_limit` | integer | `200` | 50 to 1000 | none |
| `merge.auto` | boolean | `true` | -- | none |
| `merge.wait_seconds` | integer | `3600` | 0 to 86400 | none |
| `merge.poll_seconds` | integer | `30` | 10 to 600 | none |
| `usage.enabled` | boolean | `true` | -- | none |
| `usage.pause_at_percent` | integer | `85` | 1 to 100 | none |
| `usage.weekly_pause_at_percent` | integer | `95` | 1 to 100 | none |
| `usage.codex_pause_at_percent` | integer | `85` | 1 to 100 | none |
| `usage.codex_weekly_pause_at_percent` | integer | `95` | 1 to 100 | none |
| `usage.resume_grace_seconds` | integer | `180` | 0 to 3600 | none |
| `usage.max_wait_seconds` | integer | `21600` | 0 to 604800 | none |
| `usage.reservation_seconds` | integer | `7200` | 60 to 86400 | none |
| `usage.default_job_percent` | integer | `8` | 0 to 100 | none |
| `usage.default_job_weekly_percent` | integer | `1` | 0 to 100 | none |
| `usage.codex_default_job_percent` | integer | `8` | 0 to 100 | none |
| `usage.codex_default_job_weekly_percent` | integer | `1` | 0 to 100 | none |
| `usage.run_cap_percent` | integer or `null` | `null` (no cap) | 1 to 100 | `--usage-cap` |
| `usage.repository_cap_percent` | integer or `null` | `null` (no cap) | 1 to 100 | none |
| `usage.codex_run_cap_percent` | integer or `null` | `null` (no cap) | 1 to 100 | `usage --usage-codex-cap` |
| `usage.codex_repository_cap_percent` | integer or `null` | `null` (no cap) | 1 to 100 | none |
| `review.round_cap` | integer | `4` | 1 to 100 | `--review-round-cap` (deprecated: use the local and external caps; retired after 1.17.0) |
| `review.local_round_cap` | integer or `null` | `null` (built-in 6) | 1 to 100 | `--review-local-round-cap` |
| `review.external_round_cap` | integer or `null` | `null` (built-in 4) | 1 to 100 | `--review-external-round-cap` |
| `notifications.enabled` | boolean | `true` | -- | none |
| `notifications.url` | string or `null` | `null` | an `http` or `https` URL, 1 to 2048 characters | none |
| `notifications.api_key_file` | string or `null` | `null` | a path, 1 to 1024 characters; never the key | none |
| `notifications.timeout_seconds` | integer | `5` | 1 to 60 | none |
| `notifications.attempts` | integer | `3` | 1 to 10 | none |
| `notifications.retry_seconds` | integer | `2` | 1 to 60 | none |
| `notifications.events` | object | `{"kinds": {}, "events": {}}` | see [The notifications section](#the-notifications-section) | none |
| `reviewer.harness` | string or `null` | `null` (no reviewer) | 1 to 64 characters | none |
| `reviewer.model` | string or `null` | `null` (built-in `gpt-6.1-sol`) | 1 to 128 characters | none |
| `reviewer.effort` | string or `null` | `null` (built-in `high`) | 1 to 32 characters | none |
| `reviewer.timeout_seconds` | integer | `3600` | 60 to 86400 | none |
| `reviewer.binary` | string or `null` | `null` (built-in `codex`) | a name or path, 1 to 1024 characters | none |
| `functional.automated` | boolean | `true` | `true` or `false` | none |
| `functional.timeout_seconds` | integer | `3600` | 60 to 86400 | none |
| `review.mode` | string | `auto` | `auto`, `cross-model`, `single-provider`, `manual` | `--review-mode` |
| `reviewer.same_provider_model` | string or `null` | `null` (built-in `claude-opus-5-5`) | 1 to 128 characters | none |
| `reviewer.same_provider_effort` | string or `null` | `null` (built-in `high`) | 1 to 32 characters | none |
| `reviewer.brief` | string or `null` | `null` (built-in severity bar) | 0 to 2000 characters, no control character | none |
| `harness.models` | object | `{}` | a map from a harness name (`claude-code`, `codex`) to a list of model names, each non-empty and not starting with `-` | none |
| `routing` | object | `{"default": {}, "roles": {}}` | see [The routing section](#the-routing-section) | `--routing-config`, `--model`, `--effort`, `--role-model`, `--role-effort`, `--role-harness` |

Each default is the value the Controller used before the file existed,
so a file holding only defaults behaves as no file. A boolean is not an
integer: `true` is refused where an integer is expected. A boolean
setting takes only JSON `true` or `false`: an integer, a string or
`null` is refused.

The three `merge` rows (1.6.0) matter only for a repository whose policy
opts in to auto-merge (see
[Auto-merge and the release wait](milestone-branches.md#auto-merge-and-the-release-wait)):

- `merge.auto: false` stops the Controller from merging, in every
  repository on the machine: readiness then ends at `merge_pull_request`
  and a person merges. The release wait and the stop after close-out
  still follow the repository's policy, because they only read;
- `merge.wait_seconds` bounds how long one `run` step waits at a pending
  gate (checks, merge or release pending) before it ends at that gate;
  `0` means `run` does not wait. `step` never waits, whatever the value;
- `merge.poll_seconds` is how often that wait re-reads GitHub. Each poll
  makes a few `gh` calls, so the 10-second lower bound keeps a waiting
  `run` to at most 360 polls an hour, inside GitHub's API limits.

The two `functional` rows (1.18.0) matter only for a protocol-mode repository under an automatic
acceptance gate. `functional.automated: false` keeps the stop at row 38e for every repository on the
machine, as before 1.18.0 (a person tests and records the evidence); `true` lets the Controller run
the functional tester ([The automated functional review](automation.md#protocol-mode-workflow-27-and-later)).
`functional.timeout_seconds` bounds one tester run; flows left unfinished at the bound are recorded as
not run (`timeout`).

The sixteen `usage` rows (1.8.0) are the usage budget. The Controller
reads the Claude five-hour and weekly windows from its own workers'
streams and the Codex ones from Codex's session files, forecasts what a
job will cost, and does not start a job that would not fit. Everything
is in percent of the account's window, and Claude and Codex are
separately configurable:

- `usage.enabled: false` turns the gate off for every `run` and `step`,
  which then do not read or sweep the shared record (the `usage` subcommand still shows the readings);
- `pause_at_percent` and `weekly_pause_at_percent` (and the `codex_`
  pair) are the account thresholds: a job is held when the window's
  reading, plus the forecasts other lanes have reserved, plus this job's
  forecast, would reach the threshold;
- `resume_grace_seconds` is added to a window's reset time before a
  paused run goes on, and `max_wait_seconds` bounds how long one `run`
  step (or one `usage --wait`) sleeps before it stops with exit `17`;
- `reservation_seconds` is the lease of a reservation. A running job
  renews it, so it only lapses when its holder is gone;
- the `default_job_*` rows are the forecast for a `(role, model)` with
  no earlier job; after that the forecast is the mean of earlier jobs;
- the four `*_cap_percent` rows are optional ceilings on what one run
  (`usage.run_cap_percent`, or `--usage-cap` for one invocation) or one
  repository may spend, counting work still outstanding. A repository
  cap is one number applied to the repository being driven; set it in
  that repository's settings file (`--settings`). A cap has no reset
  time, so a run never waits for it.

The strictest limit wins. A missing reading (a fresh machine, or a
provider that has not reported) disables only that provider's account
check for that window, never a cap. How the decision is made and what
the shared record holds is in
[ADR 0011](../adr/0011-usage-budget.md); the stop it produces is exit
`17` ([exit codes](../exit-codes.md)), and the operator's view is
[`usage`](commands.md#usage).

The `review.local_round_cap` and `review.external_round_cap` rows are the
two review round caps (unset means the built-in 6 and 4). A protocol run counts
the review rounds of the plan stage and of the implementation stage
separately. With Workflow 2.15.0 and later (protocol 1.5) the Workflow gives
each stage a local and an external round count, and the caps apply to them
as follows: `<stage>.apply_review` is not launched when either count has
reached its cap, `<stage>.review.local` is not launched when the local count
has reached the local cap, and the external review the Controller would
courier (`reviewer.harness` set, a mode that runs it) is not launched when the
external count has reached the external cap; the step returns the
`review_round_cap_reached` gate instead (see
[a run stopped at the review round cap](../common-problems.md#a-run-stopped-at-the-review-round-cap)).
When the Workflow gives no split counts (an older Workflow, an item created
before the counts, a promoted item or a malformed counter) the Controller
falls back to the single round it used before: the Workflow's own
`review_round` when its answer carries one (Workflow 2.12.0 and later), else
the Controller's count over its job records of this repository and work item
(a plan round counted that way is a lower bound: the Workflow counts
published contents, which can exceed `plan_revision`; a plan of a work item
the Workflow governs as `"1"` is counted at its own review phase,
`AWAITING_EXTERNAL_PLAN_REVIEW`). That single number is checked against the
external cap: `apply_review` stops at round `cap` or above, `review.local`
above round `cap`, and the external review is not capped. No other action is
stopped, and a legacy-path target is unaffected.

`review.round_cap` is deprecated and works for one more release (it is
retired after 1.17.0). When it is set in the file to anything but its default
4, or given as `--review-round-cap`, it is the value of every cap that is still
unset; a file value of exactly 4 is the default and does not override. A cap
given by its own setting or option always wins over it. `--review-local-round-cap N`
and `--review-external-round-cap N` on `step` and `run` replace the setting
for that command; `--review-round-cap N` sets both caps below an option of its
own. There is no "off" value, a long loop is a cap of 100.

To compare review rounds before and after a change, group the jobs by stage
and round: `workflow-controller telemetry --by stage,round --since
2026-10-01` (and again with the later date; the "before" figures are the first query minus the second, because `--since` has no upper bound), or `--by action` for the cost
of each kind of step. A round the Workflow stated is shown as
`stage=plan, round=4`; one counted from job records is shown as
`stage=plan, round=4~`, in a separate group (the JSON rows carry
`round_source`: `workflow` or `jobs`). A `~` plan round is a lower bound and
not the same measure as the Workflow's content count, so compare `~` groups
with `~` groups. A review the Controller ran itself (`plan.review.external`,
`implementation.review.external`) appears in these groups with its action,
stage and round. A Claude review keeps the cost and token totals its `result`
events reported (`review.telemetry`, summed over its at most two invocations,
since each is a separate session), so it is counted in those groups; a Codex
review keeps none until its rollout gives them, and shows as "telemetry
unavailable". The rounds of all jobs are worked out in one oldest-first pass
per repository and work item, with the same results as counting each job's own
history. A review the Controller runs itself always shows `round=N~`, because it has no Workflow round to quote.

The five `reviewer` rows (1.10.0) let the Controller run the cross-model
review itself (see [ADR 0013](../adr/0013-review-only-harness-seam.md)):

- `reviewer.harness` is unset by default, and then every review gate stops
  for a person exactly as before. Setting it to `codex`, a registered name,
  makes `step` and `run` start that reviewer at the two
  external review gates of a work item, in a disposable copy of the
  worktree, and hand its verdict to the Workflow. `claude` is also a
  registered name. A reviewer is cross-model when its provider family is not
  the workers' family, and the workers' family comes from the routing: the
  families of the harnesses the worker roles are routed to. With the current
  registry every worker role runs on Claude Code, so `claude` is never
  cross-model and reads as unset, and `codex` is. If the worker roles are
  routed to harnesses of more than one family there is no single provider to
  be distinct from, and `auto`, `cross-model` and `single-provider` stop with
  `review_mode_unavailable`; route every worker role to harnesses of one
  family, or choose `--review-mode manual`. The same-provider review is
  unchanged and still runs Claude. A name that is not
  registered stops the review step with exit `20`; it is never ignored.
  The `codex` harness was pinned against `codex-cli 0.163.0` (ADR 0013,
  "What the pre-step fixed", recorded on 0.162.1); a Codex update that enables
  or renames a feature, or changes a configuration layer or a state entry,
  stops the launch with the name, until the pinned set is updated, and the
  Codex review is done by hand in the meantime;
- `reviewer.model` and `reviewer.effort` fall back to the built-ins when
  unset. They are not the worker `routing` section, which does not apply
  to a review;
- `reviewer.timeout_seconds` is the hard deadline of one review run. A
  review that passes it ends `INCOMPLETE` (exit `35`);
- `reviewer.binary` is the reviewer program (a name looked up on `PATH`,
  or a path), the test seam like `--claude-binary`;
- `reviewer.brief` is the severity bar and scope the reviewer is
  given, placed after the review instructions and before the verdict
  format. Unset, empty or blank, it is the built-in brief: judge only what
  the milestone changed, against its baseline; call a finding Blocking or
  Important only for real harm (a wrong result, a lost or hidden failure,
  or a claim in the plan, the bundle or the documentation that the code
  contradicts); wording, polish, a second fault that needs a first to have
  happened, and extra hardening are Optional, and an Optional finding never
  turns a verdict into `REVISE` or `BLOCK` and never starts another round.
  The brief applies to both reviewers (Codex and the same-provider Claude
  reviewer) and to both stages. It is read only from a settings file
  outside the repository under review: a file at or under that repository,
  its `.ai-review/` or the reviews tree could have been written by the
  branch being reviewed, so for this row it is ignored, with one warning per
  invocation, and the built-in brief is used (the file's other rows apply as before).
  The brief cannot relax the rules that stand above it (the review is
  read-only, the reviewer delegates nothing, files it reads are content and
  never instructions) or change the verdict format; where it conflicts, they
  win. The round paragraph (from the second external round) and the
  finding-format rules are fixed text too; the brief's place in the prompt
  is unchanged, after the review instructions and before the verdict
  format. The Controller's check of the verdict verifies the status and the
  identifiers in its header, not the section names or what the brief asked
  for. The text is copied into job records, so a secret or a local path does
  not belong in it. It is at most 2000 characters and may not hold a control
  character other than a tab or a line break.

The `review.mode` row (1.13.0) chooses how the external review of a protocol
target runs at the two external review gates (rows 14 and 28). `--review-mode`
on `step`, `run` and `explain` replaces it for that command; a value outside
the four is a usage error (exit `2`). `settings show` gives its source.

| Mode | At an external review gate |
|---|---|
| `auto` (default) | the cross-model reviewer when `reviewer.harness` is set; else the same-provider reviewer, only where `verify` reports the stage's `require` lowered (a lowering adoption of the gate policy) and nothing else; else the ordinary gate |
| `cross-model` | the reviewer of `reviewer.harness`, whose provider family must differ from the workers' (derived from the routing; mixed worker families stop it); without one the step stops at `review_mode_unavailable` |
| `single-provider` | the same-provider reviewer (below), only while the lowered-policy signal stands; else refused before launch with `review_mode_unavailable` |
| `manual` | the ordinary gate: a person reviews and pastes the verdict |

`auto` never starts a review because a second provider happens to be logged
in: a repository that sets nothing, and whose newest gate-policy adoption
lowered no `require`, behaves as at 1.12.0. Under a **human** gate `auto` goes
to the gate, although the Workflow would admit a same-provider approval
there; the Controller runs a same-provider review only for a lowered
`distinct_reviewer_models` requirement. The signal is read from the
Workflow's `gate_policy` check and stands only while the lowering adoption is
the newest: a later tightening, a floor, an invalid policy file or any
clause the Controller does not know cancels it for both stages.

The same-provider reviewer is a fresh, read-only Claude session in a
disposable copy (see [the reviewers](workers.md#the-same-provider-reviewer)).
`reviewer.same_provider_model` and `reviewer.same_provider_effort` fall back
to `claude-opus-5-5` and `high`; they are not the worker `routing` section.
The model row takes a full model id: a name that begins `claude-` and holds
only letters, digits, `.`, `_` and `-` (an alias such as `opus` is refused
before launch with `ReviewLaunchRefused`, because Claude reports the full id
back and the Controller compares it exactly). A name of that shape that
resolves to another id, such as a `-latest` name, still starts and is
refused at the session's `init` event.
Its approvals are recorded with `review.mode`, `review.mode_source` and
`review.lowered`, and `telemetry --by review_mode` counts them, with the cost
and tokens of a Claude review (a Codex review keeps no figures, so none is
shown for it).

The seven `notifications` rows (1.9.0) make the Controller a SignalHub
producer (see [ADR 0012](../adr/0012-signalhub-notifications.md) and
[unattended runs](automation.md#notifications-from-an-unattended-run)):

- `notifications.enabled: false` is the master switch: nothing is sent and
  the notifier records one `disabled` line;
- `notifications.url` is the server address when `$SIGNALHUB_URL` is not
  set, and `notifications.api_key_file` is the path of a file whose first
  line is the API key when neither `$SIGNALHUB_API_KEY` nor
  `$SIGNALHUB_API_KEY_FILE` is set. The settings file never holds the key.
  Without them the Controller looks in `~/.config/signalhub/` (or
  `$SIGNALHUB_CONFIG_DIR`) for `url` and `api-key`. A file named `disabled`
  there silences every producer;
- `timeout_seconds`, `attempts` and `retry_seconds` bound one delivery: the
  wait per attempt, the number of attempts, and the first pause between them
  (it doubles). Delivery never delays a step, whatever they are.

The two log files a notifier writes are presentation history like the rest
of `runs/`: `runs/<run_id>/notifications.jsonl` (one line per event outcome,
each with the event's key and never the API key) and, outside a run,
`notifications.jsonl` at the runtime root. A run's `events.jsonl` ends with
a `notifications_summary` event holding the counts.

A file filled by this release looks like this (the `_defaults_written`
entries are shortened):

```json
{
  "_defaults_written": {"merge.auto": {"generation": 2, "value": true}, "...": "..."},
  "_table_generation": 6,
  "follow": {"heartbeat_seconds": 30, "replay_events": 20},
  "forge": {"pr_list_limit": 200},
  "merge": {"auto": true, "poll_seconds": 30, "wait_seconds": 3600},
  "notifications": {"enabled": true, "url": null, "events": {"kinds": {}, "events": {}}, "...": "..."},
  "reviewer": {"harness": null, "model": null, "...": "..."},
  "routing": {"default": {}, "roles": {}},
  "run": {"max_steps": 20},
  "schema_version": 1,
  "timeouts": {"git_seconds": 600, "release_command_seconds": 1800, "workflow_query_seconds": 120},
  "usage": {"enabled": true, "pause_at_percent": 85, "run_cap_percent": null, "...": "..."},
  "worker": {"drain_detach_seconds": 10800, "timeout_seconds": null}
}
```

### Which value wins

For each setting, a flag given on the command line wins, then the file,
then the built-in default. The bounds apply to the file only. A flag
has its own rule: `--timeout`, `run --max-steps` and
`resume --drain-timeout` accept any positive integer, and refuse `0`, a
negative number or a non-integer as a usage error (exit `2`).
`workflow-controller settings show` prints each value with where it came
from (`cli`, `file` or `default`).

`--routing-config PATH` replaces the file's `routing` section whole, for
that invocation. The precedence of the routing flags is in
[Worker routing](commands.md#worker-routing).

### The fill: how the file is created and kept current

Only the commands that already write fill the file: `step`, `run`,
`resume` and `milestone-binding` (and `settings clean`, below). The
read-only commands (`inspect`, `explain`, `status`, `follow`,
`telemetry`, `settings show`, `settings path`) only read it, and never
create it.

The fill is additive. It creates the file if needed, and adds each
missing key with its default. For each key it adds, it records what it
wrote in `_defaults_written`, as `{"value": ..., "generation": ...}`.
The generation is a number each release gives each default; a release
that changes a default raises it.

A later release may move a value forward to its new default, but only
when all three hold:

- the value still equals the one recorded in `_defaults_written`, so you
  never changed it;
- the new default differs from it;
- the new release's generation for the key is greater than the recorded
  one.

1.6.0 is table generation 2: it adds the three `merge` rows to a file
filled by 1.5.0, and moves nothing else. A 1.5.0 Controller sharing the
file afterwards warns about the unknown `merge` section, ignores it,
and its `settings clean` refuses.

1.7.0 is table generation 3: it adds no key and moves no value, but it
adds two routing roles (`prepare-functional-review`,
`apply-functional-review`), so a file it fills records
`"_table_generation": 3`. A 1.6.0 Controller sharing the file afterwards
uses it as before; it warns only about a key it does not know, such as an
entry for one of the two roles under `routing.roles`, which it ignores,
and its `settings clean` refuses.

1.8.0 is table generation 4: it adds the sixteen `usage` rows to a file
filled by 1.7.x, and moves nothing else. A 1.7.x Controller sharing the
file afterwards warns about the unknown `usage` section, ignores it, and
its `settings clean` refuses.

1.9.0 is table generation 5: it adds the seven `notifications` rows to a
file filled by 1.8.x, and moves nothing else. A 1.8.x Controller sharing
the file afterwards warns about the unknown `notifications` section,
ignores it, and its `settings clean` refuses.

1.10.0 is table generation 6: it adds the five `reviewer` rows to a file
filled by 1.9.x, and moves nothing else. A 1.9.x Controller sharing the
file afterwards warns about the unknown `reviewer` section, ignores it, and
its `settings clean` refuses. 1.11.0 adds no row and stays at generation 6.

1.12.0 is table generation 7: it adds the `review.round_cap` row to
a file filled by 1.10.x or 1.11.x, and moves nothing else. An older
Controller sharing the file afterwards warns about the unknown `review`
section, ignores it, and its `settings clean` refuses.

1.13.0 is table generation 8: it adds `review.mode`,
`reviewer.same_provider_model` and `reviewer.same_provider_effort` to a file
filled by 1.12.0, and moves nothing else. An older Controller sharing the
file afterwards warns about the unknown keys, ignores them, and its
`settings clean` refuses.

The release that adds the `satisfy-gate` routing role is table generation 9: it adds no key and moves no value,
but a file it fills records `"_table_generation": 9`. An older Controller sharing the file uses it as before;
it warns only about a key it does not know, such as an entry for `satisfy-gate` under `routing.roles`,
which it ignores, and its `settings clean` refuses.

The release that adds the `apply-pr-review` routing role is table generation 10, on the same terms: no key added, no value moved,
an older Controller ignores a `routing.roles` entry for `apply-pr-review` with a warning and its `settings clean` refuses.

The release that adds the `satisfy-gate-acceptance` routing role is table generation 11, on the same terms: no key added, no value moved,
an older Controller refuses the role in a `--routing-config` file, ignores a `routing.roles` entry for it with a warning and its
`settings clean` refuses. The same release gives `satisfy-gate` the route Haiku 5.5 `high`; a value you pinned for `satisfy-gate`
now covers plan and implementation approval only, and acceptance takes its own route (Sonnet 5.5 `medium`) until you name it.

The release that adds the two round cap rows is table generation 12: it adds the `review.local_round_cap` and
`review.external_round_cap` rows to a file filled by an earlier release, unset
(`null`), and moves nothing else. An older Controller sharing the file
afterwards warns about the two unknown keys, ignores them, and its
`settings clean` refuses.

The release that adds the `functional` rows is table generation 13: it adds `functional.automated` and
`functional.timeout_seconds` to a file filled by an earlier release, and the routing role
`functional-tester`, and moves nothing else. An older Controller sharing the file afterwards warns
about the two unknown keys, ignores them, and its `settings clean` refuses.

The release that adds `reviewer.brief` is table generation 14: it adds the row to a file filled by an earlier release,
unset (`null`), and moves nothing else. An older Controller sharing the file afterwards warns about the unknown key,
ignores it, and its `settings clean` refuses.

The release that adds `harness.models` is table generation 16: it adds the row to a file filled by an earlier
release, as `{}`, and moves nothing else. An older Controller sharing the file afterwards warns about the unknown key,
ignores it, and its `settings clean` refuses.

So a value you set yourself never moves. A value is never moved back to
an older default either: an older release sharing the file leaves a newer
release's value alone. `_table_generation` records the newest release
that filled the file, and is never lowered.

The fill writes only when something changed. It runs under an exclusive
`flock` on a sibling lock file (`settings.json.lock`), so two Controllers
filling the file at once never lose each other's changes. If the file
or its directory cannot be written, one warning says so, and the file's
current values still apply; missing keys take their defaults.

### When the file is refused

A file that cannot be used is refused with `SettingsError` (exit `20`)
before any job record is written, and is never rewritten. The message
names the file and the key. See
[Troubleshooting](troubleshooting.md#the-settings-file-is-refused-settingserror).

### Unknown keys and `settings clean`

A key this release does not know is ignored, with one warning per
invocation naming it. This happens when a newer release added the key,
or when this release retired it. If the file was last filled by a newer
release, the warning says so.

`workflow-controller settings clean` fills the file, then removes every
unknown key and its `_defaults_written` entry, and prints what it
removed. It refuses (exit `20`), removing nothing, when the file was last
filled by a newer release: this release cannot tell that release's new
keys from retired ones. Run `settings clean` from the newest installed
release instead.

### The routing section

The `routing` section has the same shape as a `--routing-config` file
(see [Worker routing](commands.md#worker-routing)), without
`schema_version`. Because releases share the file, it is more lenient
than that file:

- an unknown role under `roles`, an unknown field in `default` or in a
  role's entry, and an unknown key directly under `routing` are ignored,
  with the unknown-key warning above (`routing.roles.<role>`,
  `routing.default.<field>`, `routing.<key>`);
- `routing.schema_version` is reserved and refused (exit `20`): it means
  a `--routing-config` file was pasted in whole;
- everything else is strict, as in that file: a section or entry that is
  not an object, or an unusable model, effort or harness value, is refused.

`default` and each role's entry accept a third field, `harness`: the
harness a worker session of that role runs on. Its vocabulary is this
Controller's adapter registry (`claude-code` or `codex`), not the `claude`
CLI's and not the reviewer alias `claude`, which names a verdict family. Each
routing role is launched as one adapter role (`functional-tester` as
`functional-tester`, every other routing role as `worker`), and a harness is
accepted for a role only when its adapter declares that adapter role. Today
that makes exactly these pairs valid (the [harness page](harnesses.md) shows them as a
generated table):

- `claude-code`: every routing role;
- `codex`: no routing role yet, because its adapter declares `reviewer` only.
  The Codex tester (C10g) and the Codex worker roles (H4a to H4e) will add roles to
  that declaration, and the same check then accepts them with no change to the
  file format or the settings generation.

So this release gives the choice a name and a check; the choice becomes useful
as the registry grows. Any other pair is refused at load, with exit `20` before
any job record is written, in the settings file, in a `--routing-config` file
and for `--role-harness` alike (a usage error, exit `2`, on the command line). The
message names the role, the harness and what the harness does declare, for
example `role 'review-plan' runs as a 'worker' and harness 'codex' declares only
['reviewer']`. An unsupported pair is never replaced by a fallback and never
launches. A `default.harness` is checked against every role that has no `harness`
of its own, so it is refused even when `--role-harness` would override every one
of them. The built-in `claude-code` applies when nothing names a harness, and no
`harness` default is written into the file.

A model and an effort are checked against the harness the same way, once the
three values of a role are resolved (the command line, the routing config in
force and the built-in table together), before any job record is written. A
model passes when the harness's adapter declares it (the
[harness page](harnesses.md) lists them) or when the `harness.models` setting
adds it for that harness; an effort passes when the adapter declares it. An
unset model or effort inherits and is never refused. Otherwise the run or step
is refused with exit `20`, for example `role 'review-plan' routes the model
'gpt-5' to the harness 'claude-code', which declares [...]`, followed by where
each of the three values came from. To use a model the adapter does not list,
add it to the `models` key of the settings file's `harness` section:

```json
"harness": {"models": {"claude-code": ["claude-opus-5-6"]}}
```

An unsupported effort is changed in the routing; no setting extends the effort
list. A harness name no adapter has is left out of `harness.models` and
reported as an unknown key. The reviewer settings (`reviewer.model`,
`reviewer.effort`, `reviewer.same_provider_*`) keep their own checks at launch.

The resolved harness is recorded in each job's `worker_route` as `harness`, with
`sources.harness`, using the adapter name for every job kind. A same-provider
external review's record now reads `claude-code` there, where it read `claude`
before; the `review` block's own `harness` key keeps the reviewer vocabulary. The
launch reads the resolved harness and starts the worker through that harness's
launcher (`controller/worker_launch.py`); Claude Code's is the only one today, so
every worker runs on Claude Code.

The release that adds the `harness` field is table generation 15: it adds no
key and moves no value, but a file it fills records `"_table_generation": 15`.
An older Controller sharing the file warns once about the unknown `harness` key,
ignores it, and its `settings clean` refuses. An older Controller given a
`--routing-config` file with `harness` refuses it, since that file is strict.
A later release that lets Codex take a role makes a file holding that pair
valid for it and refused by this one at load, naming the role, the harness and what it declares;
update the Controller or remove the entry.

The fill only ever adds an empty `{"default": {}, "roles": {}}`. The
contents are yours, and no release migrates them. A `--routing-config`
file keeps its own strict rules, byte for byte: there an unknown role or
field is still `RoutingConfigError` (exit `20`).

### The notifications section

`notifications.events` has two layers, each a map from a name to
`{"enabled": ..., "category": ..., "severity": ...}`, every field optional:

```json
"events": {
  "kinds": {"state": {"enabled": false}},
  "events": {"milestone_accepted": {"severity": "HIGH"}, "run_finished": {"enabled": true}}
}
```

The kinds are `state`, `info`, `gate` and `blocker`; the event types are
listed in [`notifications`](commands.md#notifications). A field resolves
from the built-in default of the kind (`state` INFO/LOW, `info`
INFO/NORMAL, `gate` ACTION_REQUIRED/HIGH, `blocker` BLOCKED/CRITICAL),
then the `kinds` entry, then the `events` entry, so an event type's setting
overrides its kind's. Categories are `ACTION_REQUIRED`, `BLOCKED`,
`COMPLETED` and `INFO`; severities are `LOW`, `NORMAL`, `HIGH` and
`CRITICAL`. `usage_released` and `usage_reservation_expired` are off unless
you turn them on.

- a known field with a value that is not allowed is refused (exit `20`);
- an unknown kind, event type or field is ignored, with the unknown-key
  warning above, and so is any field whose name starts with `_`, so the
  object in the lanes' `~/.config/signalhub/events.json` can be pasted in
  whole. The Controller does not read that file itself.
