# Controller child-process reaping: collect every finished child on every supervision tick (Revision 8)

Work item: `workflow-controller-child-process-reaping`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `6a24f64a50462d7b82c13fda1ce45b253c7fc65b` ("docs: add C1b, reaping every child
process, ahead of C2 (#10)"), the tip of `main` when this plan was written, passed explicitly as
`/milestone-plan 6a24f64…` (after a squash merge the next item is planned from `main`'s head with
the base passed explicitly, `docs/guide/milestone-branches.md`). The previous milestone
(`workflow-controller-squash-merge-tag-versioning`) was accepted at `394c221`, merged by PR #8
(`f77ff06`) and released as 1.4.0 by the cutover PR #9 (`ba3a615`). PR #10 added this step to the
roadmap. Neither #9 nor #10 is this milestone's work.
Lifecycle authority: this repository's installed Workflow **2.6.0**.
Driving Controller: the installed **1.4.0** package (`workflow-controller --version`: `package
(release v1.4.0; built from ba3a615d27f7)`).
Roadmap slot: `docs/ROADMAP.md` step **C1b** of "At a glance", section **11.1.1 Reaping every
child process**.
Released baseline preserved: `workflow-controller 1.4.0` (`v1.4.0`). This milestone ships as the
patch release **1.4.1**, derived by `main.yml` from the `fix:` pull request title below.
Pull request title: `fix: reap every finished child process the Controller holds as a subreaper`

## Goal

On 2026-09-29 every Claude Code process on the host aborted twice within ten minutes: the per-user
process limit (125,849, threads included) was full of zombie `git` processes held by the two
lanes' Controllers. Both lanes now run one step per Controller process (`--max-steps 1`) so that
each Controller exits, and its zombies are reaped by its parent, after every step. This milestone
removes the cause:

1. **The Controller collects every finished child it holds, on every supervision tick, in every
   worker state** (`STARTING`, `RUNNING`, `WAITING`, `ENDING`, `DRAINING`), each by its own pid,
   never by `waitpid(-1)`, and never the worker, its anchor or any child the process itself spawned
   in its own session (every child the Controller or its embedder spawns and may wait for). An
   orphan in the Controller's own session (left by one of its same-session subprocesses) is
   collected too, once the sweep has seen it as a descendant of one of those subprocesses. It does
   the same as the last act of every launch (after the launch has given up the subreaper), and
   between launches a background reaper keeps collecting the recorded children that are still
   running until none is left, so a long gap between steps holds no zombie.
2. **A regression test** makes a fake worker orphan hundreds of short-lived processes while it is
   `RUNNING` (not only `WAITING`), and asserts that the zombie count under the Controller stays
   within a bound derived from the tick each state runs at, and is zero after the job. The existing orphan-reap tests keep passing.
3. **Test hygiene.** Every throwaway Git repository this repository's tests create turns off Git's
   automatic maintenance (`maintenance.auto=false`, `gc.auto=0`), through one shared fixture, with
   a guard test that keeps it that way.

Afterwards (operator steps, not code): 1.4.1 is released by the squash merge, installed between
Workflow Manager milestones (shared lane plan), and both lanes drop `--max-steps 1`.

## Non-goals

- **No change to ownership.** What the job owns, waits for, publishes in `worker_state` and ends
  is unchanged (I3). Collecting a zombie is not owning it; the reaping sweep never publishes.
- **No new job-record, run-record or `status` field.** A count of reaped orphans would be
  telemetry, which is C3's (Decision 2).
- **No change to `resume`/`reattach`.** A reattaching Controller is not the worker's ancestor and
  never marks itself a subreaper, so nothing is re-parented to it (`docs/guide/workers.md`,
  "Restart: `resume` re-attaches").
- **No change to how the Controller runs Git in the target repository.** Its own Git calls keep
  their pinned environments. Only the tests' throwaway repositories change configuration.
- **No leak check in the test runner.** Workflow Manager's M1b adds one to its own suite; here the
  regression test and the orphan count in CP3's verification cover it (Decision 4).
- **No workaround removal in this repository's code.** `--max-steps 1` and the watchdog live in
  the lanes' own scripts, outside this repository.

## Investigation: what the code does today (measured at `6a24f64`)

### The subreaper and the adoption record

- `launch` (`controller/worker.py` ~979) enters `_child_subreaper()`, which sets
  `PR_SET_CHILD_SUBREAPER` for the launch's duration and restores the previous value when the last
  launch in the process ends. It then calls `_reap_adopted()` and takes `baseline =
  _own_children()` (every current child, by a full `/proc` scan on `ppid`).
- `_ADOPTED` (~547) is the process-wide record of adopted children, `pid -> start_ticks`.
  `_reap_adopted()` (~550) calls `waitpid(pid, WNOHANG)` for each recorded pid only, never
  `waitpid(-1)`, so `Popen` keeps the worker's and the anchor's exit statuses.
- A child is **recorded** only by `_Ownership.scan()` (~740: `ppid == me`, not in the baseline,
  recorded before the zombie check, so zombies are recorded too) and by
  `_reap_killed_children()` (~592, after a kill).

### When the scan and the reap run

- `_Supervision.run()` (~1086) loops `_consume` → `_decide` → `_wait_for_exit(interval)`, with
  `interval` 0.05 s while the stream is active and 0.2 s otherwise.
- `_decide()` returns early while a turn is open (`RUNNING`). The ownership scan runs only on a
  state transition (`_transition` → `_scan_and_publish`) and, while `WAITING`, every
  `_OWNERSHIP_SCAN_SECONDS` (1 s) or when what the worker waits on changes.
- `_reap_adopted()` runs only at the start of a launch, inside `_reap_killed_children`, in the
  `DRAINING` loop (~1402) and in `_finish` (~1423). **Never while `RUNNING` or `WAITING`.**
- Between two launches nothing runs on a tick at all. `cmd_run` can stay at its orchestration
  boundary for as long as a pause file exists (`controller/cli.py` `_await_pause_file`, ~988-996,
  called at ~1014), and an embedder that calls `launch` may do anything for any time between
  launches. A recorded child that is still running when a launch ends and exits during such a gap
  stays a zombie until the next reap, however late that is.

### Why zombies accumulate

1. A worker's foreground command (a test run) makes throwaway repositories and commits in them.
   Git 2.55 starts detached background maintenance after a commit; its parent exits at once, so
   the maintenance process is orphaned and re-parented to the nearest subreaper: the Controller.
2. It finishes within milliseconds and becomes a zombie child of the Controller.
3. While `RUNNING`, nothing records it and nothing reaps. A scan at the next transition records
   it, but it is reaped only when the job drains or ends. A job that runs for hours accumulates
   every one: about 1,000 per test run in this repository, and 42,158 in one Workflow Manager run
   (`docs/ROADMAP.md` 11.1.1).
4. Worse, an orphan re-parented after the job's last scan is never recorded at all. At the next
   launch it is in that launch's `baseline`, which every scan excludes, so it stays a zombie until
   the Controller exits. That is why one step per Controller process (`--max-steps 1`) works around
   the leak.

### What the kernel offers

- `/proc/<pid>/task/<tid>/children` lists the direct children of one thread
  (`CONFIG_PROC_CHILDREN`, present on this host's 7.2 kernel and on the GitHub runners' kernels).
  A re-parented orphan is attached to one live thread of the subreaper, so every thread's file is
  read. The file is a snapshot and can miss a child that is being re-parented while it is read;
  the next tick sees it.
- Where the file does not exist, the full `/proc` scan by `ppid` (`_own_children`'s method) gives
  the same answer at a higher cost.

### Which children are orphans, and which the process waits for

- The tick interval is not always 0.05 s. `_poll_interval` (`controller/worker.py` ~1100-1107)
  uses `_ACTIVE_POLL_SECONDS` (0.05) only while stream bytes arrived within the last
  `_ACTIVE_STREAM_SECONDS` (1.0 s), and `_SUPERVISE_POLL_SECONDS` (0.2) otherwise. A long Bash
  tool call is silent on the stream, so a `RUNNING` worker is swept every 0.2 s from about one
  second into the call; `WAITING` is swept every 0.2 s; the `DRAINING` loop sleeps
  `_DRAIN_POLL_SECONDS` (0.2, ~280) per iteration (~1400-1402).
- The worker and the anchor are the only children the Controller starts in a new session
  (`start_new_session=True`, `controller/worker.py` ~842 and ~990; no other `start_new_session`,
  `setsid` or `preexec_fn` in `controller/`). Every other child the Controller spawns (its Git
  calls, `subprocess.run` in `controller/`) stays in the Controller's own session.
- An orphan the sweep must collect is a descendant of the worker. The worker is a session
  leader, and a process can never join another session: `setsid` makes a new one and `setpgid`
  moves only within the caller's session. So **no descendant of the worker is ever in the
  Controller's session**, whether it stayed in the worker's session or `setsid`-ed (as Git's
  detached maintenance does).
- The process may be waiting for other children while a launch supervises. `/proc` cannot tell
  them apart from orphans by `ppid`, but it can by session:
  - `tests/test_lifecycle_orchestration.py` ~2100-2127: the `on_state_change` hook starts
    `child_step` (a `subprocess.Popen`, ~1761-1772, no new session) during `WAITING`, a second
    thread waits on it, and the test asserts `returncode == cli.EXIT_WORKER_ACTIVE`;
  - `tests/test_job.py` ~1625-1650 (`InProcessConcurrencyTest`), `tests/test_worker.py` ~820,
    `tests/test_cli.py` ~1962 and ~2551, `tests/test_integration_disposable_repo.py` ~2393 run
    `launch` on a background thread while another thread keeps working, including the
    Controller's own `subprocess.run` Git calls from a second `execute_step`.
  All of those children are in the process's own session. The session rule below excludes them
  without any change to those tests **only if every path that records into or reaps from
  `_ADOPTED` applies it**. At `6a24f64` two paths other than the new sweep write `_ADOPTED`
  without any session or launch check (`_Ownership.scan`, ~739-742, which runs while `WAITING`
  and records the `child_step` of the first site above; `_reap_killed_children`, ~586-592), and
  `_reap_adopted` (~550-561) reaps every recorded pid unconditionally. The rule is therefore
  applied by the reaper itself (Design B.2), not only by the sweep.
- Excluding the **whole** session is too broad. A same-session child the process spawned
  (a Controller Git call, a callback's `Popen`) can itself start a descendant that does not
  `setsid` and outlives it. That descendant is re-parented to the subreaper, stays in the
  Controller's session, and no `Popen` in the process owns it, so nothing ever waits for it. The
  current ownership scan already records such a child (`_Ownership.scan`, ~738-742: `ppid ==
  me`, outside the baseline). The kernel keeps no record of a process's original parent (`stat`
  field 4 is the current parent; re-parenting sets `exit_signal` to `SIGCHLD`, the value a plain
  `fork` also has), so `/proc` alone cannot tell such an orphan from a child the process spawned
  itself. The only evidence is ancestry that was **observed**: a same-session process seen as a
  descendant of one of the process's same-session children was born to that child, not to the
  process, so once it is re-parented no `Popen` here can be waiting for it (Design B.2,
  "Same-session orphans").
- `_ProcStat` (`controller/worker.py` ~1780-1801) parses `state`, `ppid`, `pgrp` and
  `start_ticks` only; the session (field 6, `rest[3]` after the last `)`) is not parsed yet.
  `_ProcStat` is constructed only by `_parse_stat` (no test builds one directly); the tests that
  fake `/proc` write whole `stat` lines, which already carry a field 6.
- The residual case is a child that another thread (or a callback) starts **in a new session**
  during a launch and waits for. In `controller/` only a second, concurrent `launch` in the same
  process does that (its worker and anchor; `_subreaper_users` is a refcount because launches
  may overlap). In `tests/`, the `start_new_session=True`/`os.setsid` sites a grep of `tests/`
  outside `tests/workflow_releases/` finds at `6a24f64` are `process_fixtures.spawn_sleeper`
  (~88, killed and waited in cleanup, return code unread), `process_fixtures`'s zombie builders
  (~275, ~284, run in a child interpreter), `harness_contract/capture.py` ~356, and test-local
  `Popen(..., start_new_session=True)` calls in `test_observe.py` ~385, `test_lock.py` ~121,
  `test_worker.py` ~1085, ~1155 and ~1802, `test_resume.py` ~2192 and ~2849,
  `test_run_tests.py`, `test_test_shards.py`, `test_cli.py` ~2175,
  `test_fake_claude_contract.py` and `test_integration_disposable_repo.py` ~2121
  (`tests/fake_claude.py`'s own sites run inside the worker and are not relevant). This list is
  informative only: CP2 re-runs the grep and checks **every site it finds**, not this list, for
  whether it runs during a launch in the same process and reads its child's exit status
  (Design B.2, "Explicit exclusions").
- The one Controller thread besides the supervising one, `--follow`'s renderer
  (`controller/cli.py` `_render_run`, which calls `observe.follow_run`), reads the run's and
  jobs' durable files only; I6 tests at run time that it starts no process.

## Invariants

- **I1. Reaping is by pid, never by wildcard.** No new code calls `waitpid` with a pid `<= 0` or
  `os.wait()`. The worker's and the anchor's exit statuses always reach their `Popen` objects.
- **I2. Never the worker, the anchor, the baseline, a child the process spawned in its own
  session or an explicit exclusion -- a property of the process's reaper, not only of the
  sweep.** No path in the process records into `_ADOPTED`, and `_reap_adopted` never calls
  `waitpid` on, the worker or the anchor of **any** launch in progress in the process, a child in
  the Controller's own session (`sid == os.getsid(0)`) whose `(pid, start_ticks)` is not in
  `_FOREIGN_BORN` (the same-session processes the sweep has observed as descendants of another
  child, Design B.2), or a pid registered with `worker.exclude_from_reaping`: the
  one predicate `_reap_excluded` (Design B.2) is applied by every `_ADOPTED` writer (the sweep,
  `_Ownership.scan`, `_reap_killed_children`) and again by `_reap_adopted` itself, against a
  fresh `stat` read, immediately before each `waitpid`, so a record made by any path, or made
  before a registration, never leads to a reap. The sweep also never records a pid in the
  launch's baseline. The worker is excluded the way
  `_Ownership.scan` excludes it (`controller/worker.py` ~739): by pid and start ticks, or by pid
  alone when its start ticks are `None` -- an unknown start-ticks value never lets the worker's
  pid be recorded. A baseline child is reaped only if an earlier launch recorded it, as today.
- **I3. Ownership rules are unchanged; earlier reaping can only shorten or skip a drain.** The
  rules for the owned set, `DRAINING`'s condition, `worker_state`'s fields and the publication
  signature are 1.4.0's. The sweep writes nothing to the job record and calls no callback. The one
  observable difference is that a finished orphan's `/proc` entry disappears sooner. Every check
  that reads `/proc` states already reads a zombie as gone (`_GONE_STATES`), so for those no
  verdict changes. The exception is the `killpg` form of the group-drain test: there a zombie
  group member reads "possibly live" and keeps the drain waiting (`_group_members`,
  `controller/worker.py` ~303-312). An orphan that stayed in the worker's process group (no
  `setsid`) and is a zombie when the worker exits therefore makes 1.4.0 publish `DRAINING`,
  where 1.4.1, having reaped it a tick earlier, may publish no drain at all or end it sooner.
  Earlier reaping can only shorten or skip a drain, never lengthen it or add one, because
  removing a zombie never makes any liveness check read "live". Apart from that case, the same
  process tree produces the same publications. The golden transcripts and the existing
  worker-state tests pass unchanged.
- **I4. Bounded, cheap, every tick.** The sweep runs once per loop iteration of `_Supervision.run`
  and of the `DRAINING` loop. With the `children` files it reads one small file per thread plus
  one `stat` per child, plus one `stat` per recorded pid in `_reap_adopted` (B.2), plus the
  `children` files and `stat` lines of the process's same-session subtree (B.2, "Same-session
  orphans"; the worker's subtree is never in it), plus one `stat` per `_FOREIGN_BORN` entry the
  sweep did not just list (its pruning pass, B.2), so its cost is
  proportional to the Controller's own children, not to the host's process count. Without them it falls back to the full `/proc` scan at most once per
  `_OWNERSHIP_SCAN_SECONDS`. The sweep adds no wakeups: it runs at the interval each state
  already uses (0.05 s while the stream is active, 0.2 s otherwise and in the drain), so the
  zombies held between two sweeps are bounded by the orphan rate times 0.2 s.
- **I5. Nothing escapes between launches.** On every exit path of a launch (a result,
  `DrainDetached`, an exception), **after** the launch's `_child_subreaper()` has released its
  hold (so, for the last holder, after `PR_SET_CHILD_SUBREAPER` is reset), the launch runs one
  final sweep that records every child, alive or not, that the sweep's full exclusion list does
  not skip: not in its baseline, not a worker or anchor of any launch still in progress
  (`_LAUNCH_CHILDREN`, this launch's own entries included until after this sweep), not in the
  Controller's own session unless it is in `_FOREIGN_BORN`, and not in `_REAP_EXCLUDED` (Design
  B.2 and B.4). The ordering is what makes this complete: once the reset is done no new
  orphan can be re-parented to this process, and every earlier one is already its child, so this
  one sweep sees all of them. While another launch in the process still holds the subreaper
  (`_subreaper_users > 0`), orphans can still arrive after this launch's final sweep; that
  launch's per-tick sweeps and its own final sweep cover them. A recorded child stays the
  Controller's child until the Controller reaps it, so its pid cannot be reused, and the between-launch
  reaper (Design B.5) collects it within `_IDLE_REAP_SECONDS` (0.2 s) of its exit, however long
  the gap to the next launch is.
  (If the process was already a subreaper before its first launch, `_subreaper_previous == 1`,
  the reset leaves it one; orphans arriving after the last launch are then the concern of
  whatever made it a subreaper, as in 1.4.0.)
- **I6. The process's reaper never takes an exit status the process waits for.** Every child
  the process starts without a new session is in its own session and is never reaped (I2, applied
  at `waitpid` time), whichever thread or callback started it and whichever path recorded it, so
  a concurrent `subprocess.run`, `Popen.wait` or `communicate` always gets its child's real
  status. The only same-session children the reaper takes are identities in `_FOREIGN_BORN`:
  processes the sweep saw, alive or zombie, as a descendant of another child and **not** as a
  child of the process. An identity is admitted only when the one `stat` read that supplies its
  start ticks and session also shows `ppid` equal to the descendant being walked (LPR5-01), so a
  pid listed in a `children` file and reused, before its `stat` is read, by a child this process
  forked (whose `ppid` is the process) is never admitted. Only a process's parent can wait for
  it, and a `Popen` in this process owns only a process this process forked, so no waiter in the
  process can hold such an identity; `start_ticks` in the key keeps a reused pid from inheriting
  the entry. A same-session
  child never seen that way is excluded, so an unobserved orphan can at worst be left uncollected
  (Design B.2, "Same-session orphans"), never reaped from under a waiter. The Controller starts children in a new session only as a launch's worker and anchor;
  each is spawned under `_SPAWN_LOCK` and entered in `_LAUNCH_CHILDREN` before that lock is
  released, and every record-and-reap takes the same lock, so there is no instant at which such a
  child exists, is unregistered and can be reaped. The precondition left to an embedder or test is
  exactly this: a child that it starts **in a new session** while a launch supervises in the same
  process, and whose exit status it reads, must be started through
  `worker.exclude_from_reaping(spawn)` (which spawns and registers it under the same lock) and
  waited for inside that block (documented in `docs/guide/workers.md`). The follower thread
  starts no process at all (asserted at run time, Design C).
- **I7. The tests' repositories never run automatic maintenance**, whatever the ambient
  environment: the setting is written into each repository's own config, not passed through the
  environment that several tests deliberately replace.
- **I8. No zombie waits for a launch.** Whenever `_ADOPTED` still holds a recorded child after a
  launch's final sweep, the between-launch reaper (Design B.5) runs until `_ADOPTED` is empty, so a
  recorded child that exits between launches is collected within one `_IDLE_REAP_SECONDS` tick,
  whatever the caller does in the meantime. It reaps only through `_reap_adopted` (I1, I2) and
  starts no process. The test-support stop (`_reset_reaping_for_tests`, Design B.5) keeps this
  true between tests: it never ends the reaper while leaving a live recorded child behind.

## Design

### A. Test hygiene: one fixture for throwaway repositories (CP1)

- `tests/fixtures.py` gains:
  - `QUIET_MAINTENANCE = {"maintenance.auto": "false", "gc.auto": "0"}`;
  - `git_init(path, *options, cwd=None)`: `git init -q *options path`, then `git -C path config
    <key> <value>` for each key (in a bare repository too);
  - `git_clone(source, dest, *options, cwd=None)`: `git clone -q -c maintenance.auto=false -c
    gc.auto=0 *options source dest` (`clone -c` writes the keys into the new repository's config).
- Every `git init` and `git clone` in `tests/` goes through them: the sites listed below, the
  repositories `fixtures.build_checkout` and the other fixture builders create, and any other
  site the guard test finds (for example in `tests/process_fixtures.py`). A `git worktree add` shares its repository's
  config and needs nothing.
- **Guard test** (`tests/test_fixtures_git_hygiene.py`, new): an AST scan of every `tests/**/*.py`
  file except those under `tests/workflow_releases/` fails on any repository-creating site that
  is not inside `fixtures.git_init` or `fixtures.git_clone`. The matching rules, exactly:
  1. **Token sequences.** Each list or tuple literal, and each call's positional-argument list,
     is a sequence of elements. When an element is the string constant `"git"`, its
     **subcommand** is the first later element of the same sequence that is not an option token.
     Option tokens are: an element whose text (a string constant, or an f-string's leading
     literal part) starts with `-`, and the single element that directly follows `-c`, `-C`,
     `--git-dir`, `--work-tree` or `--namespace`. The site is flagged when the subcommand is the
     constant `"init"` or `"clone"`. A subcommand that is not a string constant (a name, a
     starred expression) is not flagged at that site; the call that supplies the literal is
     caught by rule 2 or 1 instead.
  2. **Helpers.** A call whose callee's name (a bare name or the last attribute) is `git`, `_git`
     or ends in `_git` is read as if its positional arguments followed an implicit `"git"`, and
     rule 1 applies.
  3. **Shell strings.** Every string constant or f-string in the module that is not a
     docstring (a call argument, a list, tuple, set or dict element or value, an assignment, a
     default) is flagged when its text -- for an f-string, its literal parts joined with a
     placeholder for each value -- matches
     `\bgit(\s+(-c|-C|--git-dir|--work-tree|--namespace)\s+\S+|\s+-\S+)*\s+(init|clone)\b`: only
     the five options of rule 1 consume a following word, and any other option consumes none.
     This covers `os.system(...)`, `subprocess.run(..., shell=True)`, `bash -c` payloads,
     `fake_claude` step dictionaries such as `{"step": "bash", "command": "git init …"}`, and a
     command bound to a name before use. Docstrings and comments are never flagged
     (`tests/test_managed_repo.py:6` mentions ``git init`` in a docstring). A message string that
     merely mentions the words is reworded; at `6a24f64` this rule, run over `tests/` outside
     `tests/workflow_releases/`, matches only `tests/test_runtime.py:39` and `:53`.
  **The `tests/workflow_releases/` exclusion.** Those trees are vendored Workflow releases that
  `tests/test_workflow_releases.py` (~126-146) requires to be byte-identical to the release, and
  they are the Workflow's own code, which the non-goals put out of scope. They contain
  repository-creating calls (for example `tests/workflow_releases/2.6.0/scripts/workflow_state.py`
  ~11736, `["git", "-c", f"core.hooksPath=…", "clone", …]`, which rule 1 flags), so the exclusion
  is required, not incidental. It is by exact path prefix `tests/workflow_releases/`, nothing
  else.
  **Self-tests of the guard**, on synthetic sources: `subprocess.run(["git", "init", p])`,
  `["git", "-c", f"x={v}", "clone", a, b]`, `_git("init", "-q", p)`, `os.system("git init -q
  x")`, `os.system(f"git init -q {p}")` and a `["bash", "-c", f"cd {d} && git clone {a} b"]`
  payload, a dict value `{"command": "git init -q x"}` and a name-bound `cmd = "git clone a b"`
  are each flagged; `["git", "log", "--grep", "clone"]`, `"git --no-pager log --grep clone"`
  and a docstring mentioning `git init` are not; a file placed under `tests/workflow_releases/` is skipped, and the same
  file under `tests/workflow_releases_x/` or `tests/other/workflow_releases/` is not.
  A second test makes one repository each way, with `GIT_CONFIG_*` cleared from the environment,
  and reads back both keys with `git config --local --get`.
- Measured sites at `6a24f64` (to be re-counted by the implementer; the guard test is the
  authority): `git init` in `fixtures.py` (6), `process_fixtures.py`, `test_packaged_runtime.py`,
  `test_runtime.py` (2, as `os.system(f"git init -q {…}")` at ~39 and ~53; rewritten as
  `fixtures.git_init` calls),
  `test_gitrepo.py`, `test_integration_disposable_repo.py` (3), `test_release_tools.py`,
  `test_managed_repo.py`, `test_pull_request_lifecycle.py`, `test_trunk_orchestration_e2e.py`,
  `test_workflow_contract.py` (2), `test_workflow_release_migration.py` (2); `git clone` in
  `test_milestone_branch.py` (2), `test_trunk_preflight.py` (2), `test_release_txn.py`,
  `test_gitrepo.py`, `test_job.py`, `test_pull_request_lifecycle.py`, `test_packaged_runtime.py`,
  `test_trunk_orchestration_e2e.py` (2), `test_workflow_release_migration.py`.
- No test asserts the full contents of a throwaway repository's config; the Controller's
  preflight (`workflow_contract._git_config_entries`) reads every entry but refuses only filter,
  hook and fsmonitor keys, so the two added keys change no verdict. The full suite confirms it.

### B. Reaping every finished child (CP2)

All in `controller/worker.py`, plus two calls in `controller/cli.py`.

1. **Reading the children.** `_direct_children(pid=None) -> set[int] | None`: the union of
   `<proc>/<pid>/task/<tid>/children` over every `tid` in `<proc>/<pid>/task` (with `<proc>` the
   existing `_proc_root()` seam and `<pid>` `os.getpid()` by default), or `None` when the
   `children` file does not exist (`CONFIG_PROC_CHILDREN` off; a patchable seam). A thread that
   exits while it is read is skipped; for another `pid`, a process that exited gives the empty
   set.
   **The session field.** `_ProcStat` gains `session: int`, parsed by `_parse_stat` from field 6
   (`rest[3]`, after the last `)`; its docstring names it). It has no default, so every
   constructor states it, and it is declared before `ppid: int = 0` (`controller/worker.py`
   ~1785), since a dataclass rejects a field without a default after one with a default
   (LPR5-04); `_parse_stat` is the only one in `controller/` and no test builds a
   `_ProcStat` directly. The new predicate tests write fake `stat` lines whose field 6 is set
   explicitly to `os.getsid(0)` or to another value; the existing `/proc` fixtures already write
   a field 6 and keep their meaning, since no existing path reads it.
2. **The sweep.** `_collect_children(exclude, baseline, *, force_full=False)`:
   - takes the children from `_direct_children()`; on `None`, from the full `/proc` scan by `ppid`
     (`_own_children`), but only when `force_full` or at least `_OWNERSHIP_SCAN_SECONDS` have
     passed since the last full scan (a module-level timestamp; I4);
   - first updates `_FOREIGN_BORN` (below, "Same-session orphans") from the children it just
     listed;
   - skips every pid in `baseline`; every identity in `exclude` (this launch's worker
     `(pid, start_ticks)` and anchor pid) and in the process-wide `_LAUNCH_CHILDREN` (the worker
     and anchor identities of every launch in progress, so two overlapping launches never
     record each other's); every child whose session id (`_ProcStat.session`) equals
     `os.getsid(0)` **unless** its `(pid, start_ticks)` is in `_FOREIGN_BORN`; and every pid in
     `_REAP_EXCLUDED`. The worker rule is `_Ownership.scan`'s: a child with a worker's pid is
     skipped when that worker's recorded start ticks are `None`, or equal the child's; only a
     child with a worker's pid and *different, known* start ticks (a reused pid) is treated as
     an ordinary child (I2);
   - a child whose `stat` cannot be read (it was reaped between the listing and the read, or is
     being re-parented) is skipped this tick and seen on the next; its session is unknown, so it
     is never recorded blind;
   - records each remaining child in `_ADOPTED` with `setdefault(pid, start_ticks)`;
   - then calls `_reap_adopted()`.
   It returns nothing and publishes nothing (I3).

   **Same-session orphans** (external plan review, round 1, finding 1). A process-wide map
   `_FOREIGN_BORN: dict[int, int]` (`pid -> start_ticks`) holds the same-session processes the
   sweep has seen as a descendant of one of the process's children rather than as a child of
   the process itself. On each sweep, under `_SPAWN_LOCK`, for every listed child `c` whose
   session is `os.getsid(0)` (a child the process spawned, or an already foreign-born orphan),
   it walks `c`'s descendants through `_direct_children(c)` (recursively, only through
   descendants whose `stat` session is still `os.getsid(0)`: a `setsid`-ed descendant is not
   excluded by the session rule in the first place and needs no entry) and adds each one's
   `(pid, start_ticks)` with `setdefault` -- **only when the same `stat` read that supplies its
   start ticks and session also shows `ppid` equal to the parent being walked** (LPR5-01). The
   listing and the `stat` are two reads: a listed descendant can exit, be reaped by its parent
   and have its pid reused, in between, by a child another thread of this process forks with a
   plain `subprocess.Popen` (which does not take `_SPAWN_LOCK`); that child's `ppid` is the
   process, not the walked parent, so the check rejects it. A descendant whose `ppid` does not
   match is skipped like one whose `stat` cannot be read; one genuinely re-parented between the
   two reads is then simply unobserved, the residual below. (The full-scan fallback reads
   `ppid`, start ticks and session from one `stat` line and has no such window.) The walk skips
   a descendant whose `stat` cannot be read. Such an identity was born to `c` or below it, never to the process: only its parent can
   wait for it, so once it is re-parented to the process no `Popen` here owns it (I6). An entry
   is dropped when a `stat` read of its pid finds it gone, or finds different start ticks (the
   pid was reused), and when `_reap_adopted` reaps it; an entry whose process is still a
   descendant elsewhere stays until then. **The pruning pass** (LPR5-03): each sweep, under
   `_SPAWN_LOCK`, after the walk, re-reads the `stat` of every `_FOREIGN_BORN` entry the walk
   and the listing did not just see and drops those gone or with different start ticks (an
   unreadable `stat` keeps the entry for the next sweep). An entry whose process was reaped by
   its original parent and is never listed again is thus dropped on the next sweep, so the map
   never holds more than the live and not-yet-collected foreign-born identities (I4). The walk is limited to the process's same-session
   subtree, which the worker's subtree never belongs to (Investigation), so its cost is that
   subtree's size, typically zero to a few processes per tick (I4); where `_direct_children`
   returns `None` the walk uses the rate-limited full-scan listing's `ppid` links instead.
   **Residual, by design.** A same-session descendant that is started and orphaned between two
   sweeps is never seen as a descendant, so it is excluded like a child the process spawned and
   is left for the process's own parent to collect when the process exits, as in 1.4.0. The rule
   errs this way deliberately: the alternative is reaping a same-session child the process might
   be waiting for, which silently turns a failed `subprocess.run` into returncode 0 (Decision 6).
   Git's own detached maintenance never falls in it: `daemonize` calls `setsid`, so it is never
   in the Controller's session.

   **One predicate, applied where the `waitpid` happens** (LPR3-01). The process-wide rules above
   (a worker or anchor in `_LAUNCH_CHILDREN` by the worker rule's identity, a `stat` session equal
   to `os.getsid(0)` without a matching `_FOREIGN_BORN` entry, a pid in `_REAP_EXCLUDED`) are one
   function, `_reap_excluded(pid, stat)`.
   It is applied in three places, each holding `_SPAWN_LOCK` (below):
   - by every writer of `_ADOPTED`: the sweep, `_Ownership.scan`'s `_ADOPTED.setdefault` (~742)
     and `_reap_killed_children`'s (~592) now go through one helper, `_record_adopted(pid, stat)`,
     that records nothing the predicate excludes. `_Ownership.scan`'s own `adopted` flag and its
     owned-set logic (`source = "adopted"`) are unchanged (I3): only the `_ADOPTED` write is
     filtered;
   - by `_reap_adopted` itself, before each `waitpid(pid, WNOHANG)`: it re-reads the recorded
     pid's `stat` (a zombie's `stat` still carries its session field) and, if the predicate
     excludes it, drops the record without waiting. A `stat` that does not exist (the pid was
     reaped elsewhere) drops the record; one that exists but cannot be read keeps the record and
     skips it this time. This also covers a record made before a registration, and a recorded pid
     reaped elsewhere and reused by a child the process waits for;
   - the baseline rule stays the sweep's alone, as today.
   A future recording site therefore cannot reintroduce the status theft: `_reap_adopted` is the
   only place that calls `waitpid` on a recorded pid, and it checks every pid itself.

   **No spawn-to-registration window** (LPR3-02). A module-level `_SPAWN_LOCK`
   (`threading.RLock`, re-entrant because the sweep's record step calls `_reap_adopted`, which
   takes it too) is held by every record-and-reap step (`_record_adopted`'s callers' record step
   and all of `_reap_adopted`), which call no callback and wait for nothing while they hold it,
   and by every
   spawn of a new-session child that must be protected, **across** its `subprocess.Popen` and its
   registration: `launch` holds it around the worker's `Popen` plus the worker pid's entry into
   `_LAUNCH_CHILDREN` and the holder (B.4), and around `_spawn_anchor` plus the anchor pid's
   entry. Another thread's sweep cannot record or reap such a child between its fork and its
   registration, because it cannot take the lock during that interval; after the lock is
   released the child is registered and the predicate excludes it at record and reap time. The
   lock is held only for a `Popen` (milliseconds) or one record-and-reap step, never across a
   wait.

   **Explicit exclusions.** `worker.exclude_from_reaping(spawn)` is a context manager entered
   **before** the spawn: it takes `_SPAWN_LOCK`, calls `spawn()` (a zero-argument callable that
   returns a `subprocess.Popen`), adds the child's pid to the process-wide `_REAP_EXCLUDED` (with
   a count, so nested use works), releases the lock and yields the `Popen`; on exit it removes the
   entry. The caller waits for the child inside the block:
   `with worker.exclude_from_reaping(lambda: subprocess.Popen(args, start_new_session=True)) as
   child: child.wait()`. It is the only way for code outside `launch` to protect a new-session
   child it waits for (I6). `_LAUNCH_CHILDREN` is filled by `launch` itself (B.4) and needs no
   caller. CP2 re-runs the Investigation's grep and wraps in `exclude_from_reaping` each site it
   finds that can run during a launch in the same process and reads its child's exit status; at
   `6a24f64` none is expected to (each either runs outside any launch or ignores the status), and
   the checkpoint notes record the result per site.
3. **Every tick.** `_Supervision.run()` calls `self._collect()` (the sweep with this launch's
   exclusions and baseline) once per loop iteration, after `_consume()` and before the exit check,
   so `STARTING`, `RUNNING`, `WAITING` and `ENDING` are all covered. The `DRAINING` loop calls it
   in place of its bare `_reap_adopted()`. `_finish`, `_detach` and `_interrupt` call it once more
   (with `force_full=True`).
4. **After the subreaper is given up.** `launch` registers the final-sweep callback on its
   `ExitStack` immediately **before** entering `_child_subreaper()`, so on unwind (the stack is
   LIFO) it runs right **after** `_child_subreaper`'s `finally` has released this launch's hold
   and, for the last holder, reset `PR_SET_CHILD_SUBREAPER` (`controller/worker.py` ~536-541), on
   every exit path. The callback reads its exclusions from a small mutable holder that `launch`
   fills as they come to exist, in this exact order:
   - the baseline, right after `baseline = _own_children()`;
   - the worker's **pid**, right after `subprocess.Popen` returns and before
     `capture_worker_process(proc.pid)` (~1004), with start ticks `None` (pid-only exclusion by
     I2's `None` rule), so a failure in the capture itself can never let the worker be recorded;
     the `Popen` and this entry happen under one hold of `_SPAWN_LOCK` (B.2);
   - the worker's start ticks, once `capture_worker_process` returns;
   - the anchor's pid, right after `_spawn_anchor` returns, also under one hold of
     `_SPAWN_LOCK` together with the `_spawn_anchor` call.
   The same identities go into `_LAUNCH_CHILDREN` at the same points, and the final-sweep
   callback removes this launch's entries **after** its sweep. (Every exit path between the
   `Popen` and `on_spawn` already waits on the worker, ~1009 and ~1026, so the exclusion only
   matters for the time the worker is alive or not yet waited on.) It calls the sweep with
   `force_full=True` (I5). A launch that fails before taking its
   baseline has recorded nothing and started nothing, so its callback sweeps nothing. The
   existing `_reap_adopted()` at the start of a launch stays.
5. **Between launches, between steps and at the end of a run.** A public
   `worker.reap_adopted_children()` wraps `_reap_adopted()`. `cli.cmd_run` calls it after every
   `_run_one_step`, and `cmd_run` and `cmd_step` call it in a `finally` as they return. It only
   waits on recorded pids, so it needs no exclusions.
   **The between-launch reaper** (external plan review, round 1, finding 2). One `_reap_adopted()`
   leaves a still-running recorded child in place, and nothing else runs on a tick until the next
   launch: a paused `cmd_run` (`_await_pause_file`) or an embedder can hold that gap open
   indefinitely. So the final-sweep callback (B.4), after its sweep, calls
   `_ensure_idle_reaper()`: if `_ADOPTED` is non-empty and no idle reaper thread is running (a
   module-level reference checked and set under `_SPAWN_LOCK`), it starts one daemon thread,
   `workflow-controller-reaper`, that loops `_reap_adopted()` every `_IDLE_REAP_SECONDS` (0.2 s,
   the drain's tick) and exits as soon as `_ADOPTED` is empty after a reap. **The exit handshake
   is atomic** (LPR5-02): each iteration takes `_SPAWN_LOCK` once and, in that one hold, calls
   `_reap_adopted()` (the lock is re-entrant), checks whether `_ADOPTED` is empty and, if it is,
   clears the module-level reference and returns; it sleeps only after releasing the lock.
   `_ensure_idle_reaper` checks `_ADOPTED` and the reference and starts the thread in one hold of
   the same lock, and every `_ADOPTED` writer holds it. So a final sweep that records a child
   either runs before the thread's check (which then sees `_ADOPTED` non-empty and keeps
   looping) or after its clear (so it sees no reference and starts a new thread): there is no
   state in which a recorded child waits with no reaper running (I8).
   **Test isolation** (LPR5-02). The thread is process-wide and outlives the launch that started
   it, so it can run into later tests in the same interpreter. `worker._stop_idle_reaper(timeout)`,
   a test-support function, sets a module-level stop `threading.Event` that the loop checks in
   the same hold as its emptiness check (exiting and clearing the reference the same way), joins
   the thread, and clears the event; it leaves `_ADOPTED` as it is. It is test-only: while it
   runs, `_ensure_idle_reaper` still sees the reference and starts nothing, which only a test can
   observe. Stopping the thread alone could strand a real recorded child that is still running
   (external plan review, round 2, finding 2), so it is never used alone: the test-support
   function `worker._reset_reaping_for_tests(timeout)` calls it and then, in one hold of
   `_SPAWN_LOCK`, settles every `_ADOPTED` record against the **real** `/proc/<pid>/stat` (read
   directly from `/proc`, never through the patchable `_proc_root()` seam, so a planted record
   cannot pass for a real one):
   - a record whose real `stat` shows a child of the process (`ppid == os.getpid()`) with the
     recorded start ticks is this process's own unreaped child (the start ticks rule out a reused
     pid). It is first put through `_reap_excluded(pid, real_stat)` (LPR7-02): a record may have
     been planted by a test rather than made by `_record_adopted`, so it was never vetted, and
     it may be a child a waiter in the process still owns (a same-session `Popen` child outside
     `_FOREIGN_BORN`, a child under `exclude_from_reaping`, another launch's worker or anchor).
     An excluded record is dropped with no signal and no `waitpid`. Otherwise, if it is not yet
     a zombie it is sent `SIGKILL`, and it is then collected with `os.waitpid(pid, 0)` -- by
     that specific pid, which the predicate has just vetted against the real `stat`, and which
     returns promptly since the child is dead -- so it can never become a zombie with no reaper
     to collect it. The predicate runs before `_FOREIGN_BORN` is cleared below, so a real
     foreign-born orphan is still admitted;
   - every other record -- no `stat` (gone, reaped elsewhere), different start ticks (a reused
     pid) or a `ppid` that is not the process (a planted pid) -- is dropped with no signal and no
     `waitpid`;
   - `_ADOPTED` and `_FOREIGN_BORN` are then cleared (a `_FOREIGN_BORN` entry only admits a pid
     to recording, which the next sweep re-establishes from observation).
   It returns the pids it killed. `_LAUNCH_CHILDREN` and `_REAP_EXCLUDED` are not touched: they
   are scoped by `launch` and `exclude_from_reaping`, and are empty between tests.
   `tests/fixtures.py` gains `isolate_idle_reaper(case)`: it waits up to 2 s for a
   running reaper to end by itself (so a real recorded child left by an earlier test that
   finishes in that time is collected normally), then calls `_reset_reaping_for_tests`, and
   registers the same two steps plus an assertion that no thread named
   `workflow-controller-reaper` is alive and `_ADOPTED` is empty as a cleanup
   (`case.addCleanup`). Registered from `setUp` before any patch, that cleanup runs after every
   patch and spy the test installs has been undone (cleanups run last-in first-out), so the
   real `/proc` read and the real `os.waitpid` are the ones in effect. A test's own live orphans
   are thus killed and collected at its own cleanup, never inherited by the next test; no state
   is left in which a live recorded child has no reaper (I8 holds across tests as well). Every `ChildReapingTest`, every test that patches the `/proc` seams or
   plants pids in `_ADOPTED` or `_FOREIGN_BORN`, and the `cli` reaping tests call it from
   `setUp` before any patch or spy is installed. A later launch's own sweeps may run while it is still
   running; both go through `_reap_adopted` under `_SPAWN_LOCK` and wait by pid only, so
   neither can take the other's result, and whichever reaps a pid first drops its record. It
   records nothing, walks nothing, starts no process and calls no callback; since `_ADOPTED`
   holds only children recorded while the process was a subreaper, and every such child is
   eventually collected, the thread always ends. It is a daemon thread, so it never delays
   interpreter exit; `cmd_run`'s and `cmd_step`'s `finally` reap stays for the common case of
   children already finished. `cmd_resume` is not changed: it never launches a worker
   (`controller/cli.py` ~1067-1069) and a re-attaching Controller records nothing, so it has
   nothing to reap.
6. **What stays unchanged.** `_Ownership.scan`'s owned-set logic and its `adopted` flag (its
   `_ADOPTED` write now goes through `_record_adopted`, B.2), `_reap_killed_children`'s kill-settle
   loop (its `_ADOPTED` write likewise), `_own_children` as the baseline, the drain's conditions,
   the anchor, `reattach`, and every published field.

Order of cost: a Controller holding a handful of live children reads two or three small files
and a few `stat` lines per tick. A finished orphan is collected within one tick of its exit:
0.05 s while the stream is active, 0.2 s otherwise and in the drain, so the zombies held at any
moment are at most the orphan rate times 0.2 s (I4).

### C. Tests for the reaping (CP2)

- **Fake worker.** `tests/fake_claude.py` gains `{"step": "orphan_burst", "count", "spacing",
  "lifetime", "detach"}`: inside the open turn (one Bash tool call, so the Controller sees `RUNNING`), it
  double-forks `count` times, `spacing` seconds apart; each intermediate exits at once and each
  grandchild calls `setsid`, lives `lifetime` seconds and exits, as Git's detached maintenance
  (`daemonize`: fork, `setsid`) does. The tool call
  returns after the last grandchild has exited, and the turn then ends normally. With
  `"detach": true` the burst runs in a `setsid` descendant that outlives the worker (the
  `DRAINING` variant). The fake's contract test covers the new step.
- **The regression tests** (`tests/test_worker.py`, `ChildReapingTest`, `_SupervisedCase`s). One
  burst shape for every state: `count=150`, `spacing=0.02`, `lifetime=0.01`, so the burst lasts
  at least 3 s and produces one orphan per 0.02 s. A sampler thread in the test (reading only; it
  starts nothing) counts, every 20 ms, the zombie children of the test process that are not in
  the launch's baseline. The bound is derived from the tick each state actually runs at
  (Investigation, "Which children are orphans"):

  | variant | how the state is reached | sweep tick during the burst | expected peak per tick | bound |
  | --- | --- | --- | --- | --- |
  | `RUNNING` | the burst inside one Bash tool call | 0.05 s for the first ~1 s, then 0.2 s (stream idle) | ~3, then ~10 (0.2 / 0.02) | 30 |
  | `WAITING` | the burst from a `bash_bg` step's background command | 0.2 s (stream idle) | ~10 | 30 |
  | `DRAINING` | the burst from a `detach` descendant after the worker exits | 0.2 s (`_DRAIN_POLL_SECONDS`) plus the group scan | ~10-11 | 30 |

  Assertions, in each variant:
  - **the bound**: at most **30** zombies per sample, three times the expected per-tick peak,
    with at most **5** samples (100 ms) allowed above it. A stalled tick (one extra 0.2 s) raises
    the peak to about 20, still under the bound; the allowance absorbs a longer main-thread
    stall on a loaded 4-vCPU runner (the sampler also takes the GIL);
  - **the sampling is real**: at least **75** samples (half of the ~150 a 3 s burst allows at
    20 ms) are taken while the burst runs, so a starved sampler cannot pass by not looking;
  - the published states during the burst are the variant's state (`RUNNING` and no `WAITING`
    for the first; `WAITING`; `DRAINING`); after `launch` returns there is no zombie child left;
    the result is `SUCCESS`.
  Against a broken sweep the count only grows: it passes 30 about 0.6 s into the burst, so for
  the remaining 2.4 s or more about 120 samples exceed the bound, far more than 5. The `RUNNING`
  and `WAITING` tests, run against `6a24f64`'s `worker.py`, must fail (recorded in the checkpoint
  notes as the red runs): 1.4.0 never reaps while `RUNNING` or `WAITING` (Investigation), so while
  `WAITING` its 1 s scan records the orphans but nothing collects them. The `DRAINING` test is
  expected to fail there too -- the drain loop reaps every 0.2 s but new orphans are recorded
  only at the 1 s `next_scan`, so up to about 50 zombies build up between records -- and the
  notes record what was observed.
- **Children the process waits for keep their status** (I6). A launch in `WAITING` whose
  `on_state_change` callback starts `subprocess.Popen([sys.executable, "-c", "import time,
  sys; time.sleep(1); sys.exit(45)"])` once, and a second thread that waits on it: across at
  least 20 ticks of sweeping, including the `WAITING` ownership scans that see the child, the
  thread reads `returncode == 45`. A second case starts the child as
  `worker.exclude_from_reaping(lambda: subprocess.Popen([...], start_new_session=True))` and
  waits inside the block, and reads 45 too; the same child spawned without the exclusion is
  recorded (a unit-level assertion on `_ADOPTED`, not on the racy status). A third runs two
  overlapping launches on two threads and asserts that neither records the other's worker or
  anchor into `_ADOPTED` (through the sweep or through `_Ownership.scan`) and both return
  `SUCCESS` with their workers' real exit statuses.
- **Every `_ADOPTED` writer and the reaper apply the predicate** (LPR3-01), unit tests with the
  `/proc` seams patched: `_Ownership.scan` and `_reap_killed_children` each see a zombie child in
  the process's own session and a zombie that is another launch's worker (in
  `_LAUNCH_CHILDREN`) and a pid in `_REAP_EXCLUDED`; none is recorded, and an `os.waitpid` spy
  sees no `waitpid` on any of them across a following `_reap_adopted()`. A pid planted in
  `_ADOPTED` directly (a stand-in for any future recording site) whose `stat` shows the
  process's own session, or which is in `_LAUNCH_CHILDREN` or `_REAP_EXCLUDED`, is dropped by
  `_reap_adopted` without a `waitpid`.
- **No spawn-to-registration window** (LPR3-02), unit tests: with `subprocess.Popen` patched so
  that, while it runs, the children seam already reports the new-session child and a second
  thread attempts a sweep and a `_reap_adopted()`, (a) inside `exclude_from_reaping(spawn)` and
  (b) for `launch`'s worker and anchor spawns, the second thread blocks on `_SPAWN_LOCK` until
  the registration is made, then records nothing and reaps nothing for that pid, and the child's
  exit status reaches its `Popen`. A pid recorded **before** its registration (planted) is
  dropped at reap time once registered, with no `waitpid`.
- **Between launches.** Two launches in one process: the first leaves a recorded orphan alive past
  its end (a grandchild with `lifetime` longer than the launch); after it exits, the next
  `reap_adopted_children()` (or the second launch's start) reaps it, and it is never in the second
  launch's baseline unrecorded (I5). A `cli` test drives `cmd_run` over two steps with `launch`
  patched to leave such an orphan, and asserts no zombie child remains after the first step.
- **A recorded child that exits during a paused boundary** (I8; external plan review, round 1,
  missing test 2; synchronised, round 2, finding 1). A `cli` test runs `cmd_run` with
  `WORKFLOW_CONTROLLER_TEST_HOOKS=1` and a `--pause-file` that the first step's patched `launch`
  creates on its way out. That `launch`, inside the real `_child_subreaper()`, double-forks an
  orphan (the grandchild calls `setsid`, as Git's `daemonize` does) that **cannot exit until
  the test creates a release file** (it polls for the file every 10 ms, then exits), waits until
  the orphan is its child, records it through the real final sweep (`force_full=True`, after the
  subreaper reset) and `_ensure_idle_reaper()`, and returns. No elapsed time decides the order:
  `_await_pause_file` is wrapped so that on each entry it counts the entry and, **only if the
  pause file exists at that entry** (`Path(pause_file).exists()`), sets an `Event` (recording
  the entry's number) before calling the original; the test waits on that `Event` (failing
  after 10 s). `cmd_run` calls `_await_pause_file` at the boundary before **every** step,
  including the first (`controller/cli.py` ~1016-1022), and at that first entry the pause file
  does not exist yet -- the first step's patched `launch` creates it on its way out -- so the
  first entry never signals (local plan review, round 7, LPR7-01). The pause file is created
  only by that `launch`, so the signalling entry is the boundary after step 1, reached only
  once `launch` has returned and `cmd_run`'s per-step reap has run; the test asserts, as a
  self-check, that the signalling entry's number is 2. At that point -- after the per-step
  reap, with `cmd_run` blocked in the pause -- the test asserts the required starting
  state: the orphan's pid is in `_ADOPTED`, its real `stat` shows it alive (state not `Z`) with
  the test process as its parent, and a `workflow-controller-reaper` thread is alive. Only then
  does it create the release file, wait for the orphan to exit, and assert that within 0.5 s
  (`_IDLE_REAP_SECONDS` plus margin) its pid is no longer a zombie child of the test process,
  that `_ADOPTED` is empty and the `workflow-controller-reaper` thread has ended; it then removes
  the pause file and the run completes. A regression that reaps the orphan early fails the
  starting-state assertion instead of passing without exercising the idle reaper. Unit tests of the idle reaper: it
  is started only when `_ADOPTED` is non-empty after a final sweep, at most one runs at a time,
  it exits once `_ADOPTED` is empty, and an `os.waitpid` spy sees only recorded positive pids.
  **The exit handshake** (LPR5-02): with `_reap_adopted` wrapped so the reaper thread signals and
  then blocks inside its exit iteration (after emptying `_ADOPTED`, still holding
  `_SPAWN_LOCK`), a second thread runs a final sweep that records a planted child; it blocks on
  the lock until the reaper has cleared its reference and exited, then starts a new reaper, and
  the test asserts that at every point after the sweep either a `workflow-controller-reaper`
  thread is alive or `_ADOPTED` is empty; the mirror order (the sweep records first) leaves the
  running thread looping. **The isolation guard** (LPR5-02; external plan review, round 2,
  finding 2): `isolate_idle_reaper` stops a reaper left running by a previous test before the
  next test's patches take effect, without stranding a live recorded child. A unit test starts
  a reaper over two records -- a real live child of the test process that blocks until killed
  (recorded with its real start ticks) and a planted pid whose real `stat` is absent or not a
  child of the process -- with an `os.waitpid` spy that forwards to the real call; it calls the
  helper and asserts: no reaper thread is alive; the real child was killed and collected
  (`_reset_reaping_for_tests` returned its pid, the spy saw exactly one `waitpid` on it, and
  its `/proc/<pid>` entry is gone, so it is not a zombie); the planted pid saw no `waitpid` and
  no signal; `_ADOPTED` and `_FOREIGN_BORN` are empty. A second case plants a record whose pid
  is a real child of the process with **different** start ticks (a reused pid) and asserts it
  is dropped with no signal and no `waitpid`. A third case (LPR7-02) plants a record of a real
  live same-session child of the test process (a plain `subprocess.Popen`, no new session, not
  in `_FOREIGN_BORN`) with its **real** start ticks and asserts `_reset_reaping_for_tests`
  drops it with no signal and no `waitpid` (the child is still alive afterwards and the test
  kills and waits for it itself), and does not return its pid. And each reaping test class's cleanup asserts
  that no `workflow-controller-reaper` thread survives it and `_ADOPTED` is empty.
- **An adopted same-session grandchild is reaped; its waited-for parent keeps its status**
  (I2, I6; external plan review, round 1, missing test 1; synchronised, round 2, finding 1).
  A launch supervises on a background thread; its fake worker is held in `WAITING` by a
  `bash_bg` step whose `command` polls for a launch-release file, so the launch -- and the
  subreaper -- last until the test ends them. The test thread starts `parent =
  subprocess.Popen([sys.executable, "-c", ...], stdout=PIPE)` with no new session, so in the
  test process's session. Every step that an elapsed time used to order is gated on a release
  file the test creates only after it has observed the required state (each wait fails the
  test after 10 s):
  1. the parent starts a grandchild (no `setsid`) that polls for a grandchild-release file and
     then exits, prints the grandchild's pid on its stdout, and polls for a parent-release file;
  2. the test reads the pid and waits until `(pid, start_ticks)` -- the start ticks read from
     the grandchild's real `stat` -- is in `_FOREIGN_BORN`: a sweep has observed the grandchild
     as `parent`'s descendant while `parent` is still alive. Only then does it create the
     parent-release file; the parent exits with 45 and the test's `parent.wait()` returns;
  3. the test waits until the grandchild's pid is in `_ADOPTED` (re-parented to the test
     process and recorded by a later sweep) and asserts that its real `stat` shows it alive, not
     a zombie, with the test process as its parent. Only then does it create the
     grandchild-release file;
  4. it asserts that within 0.5 s of the grandchild's exit its pid is no longer a zombie child
     of the test process, then creates the launch-release file and joins the launch.
  Assertions: `parent.returncode == 45`; the grandchild passed through `_FOREIGN_BORN` and
  `_ADOPTED` in that order and was collected before the launch returned; the launch's result is
  `SUCCESS`. A sweep that never observes the grandchild as a descendant fails step 2's wait
  rather than letting the parent exit early and the test pass by timing. Unit tests with the seams
  patched: a same-session child never seen as a descendant is not recorded and a planted record
  of it is dropped without a `waitpid`; one seen as a descendant is recorded and reaped; an entry
  whose pid reappears with different start ticks is dropped and does not make the new process
  reapable; the walk does not descend into a `setsid`-ed descendant's subtree; **the pid-reuse
  admission case** (LPR5-01): the `children` file of a walked same-session child `c` lists pid
  `p`, but `p`'s `stat` shows `ppid == os.getpid()` (a child the process forked, reusing the
  pid), and `p` is not admitted to `_FOREIGN_BORN`, not recorded, and a following
  `_reap_adopted()` makes no `waitpid` on it; the same `p` with `ppid == c` is admitted; **the
  pruning pass** (LPR5-03): an entry whose pid's `stat` is gone, or shows different start
  ticks, and which no listing shows, is dropped on the next sweep, and one whose `stat` cannot
  be read is kept.
- **Unit tests of the sweep** with the `/proc` seams patched: children from several threads are
  merged; the worker (by identity), the anchor, baseline pids, a child in the process's own
  session outside `_FOREIGN_BORN`, a pid in `_LAUNCH_CHILDREN` and a pid under `exclude_from_reaping` are never
  recorded; a recorded zombie outside those rules is reaped; a pid whose `stat` is unreadable is skipped that tick
  and recorded on the next once readable; `exclude_from_reaping` nests and removes its entry on
  exit; a child with the
  worker's pid is never recorded when the worker's start ticks are `None`, and is recorded when
  its start ticks are known and differ (I2); with the `children` file missing, the full scan is
  used and rate-limited; an `os.waitpid` spy sees only positive pids (I1); for process trees with
  no zombie member in the worker's process group, the published signature of a supervised run
  with and without the sweep is identical (I3).
- **The final sweep's ordering** (I5), a unit test with the subreaper seams
  (`_set_child_subreaper`) patched: the patched reset records that it ran, and a child that
  appears in the patched children seam only once the reset has run (a stand-in for an orphan
  re-parented just before it) is recorded by the final sweep; the test also asserts that the
  sweep ran after the reset, on the normal, `DrainDetached` and exception exit paths. A
  `capture_worker_process` patched to raise leaves the worker's pid excluded from that final
  sweep (LPR2-04's order).
- **I6.** A test runs `observe.follow_run` over a recorded run with jobs (the existing
  `tests.test_observe` fixtures), on a thread, with `subprocess.Popen`, `os.fork`,
  `os.posix_spawn` and `os.system` patched to fail the test if called, and asserts that the run
  renders completely.
- **Existing tests** keep passing unchanged, in particular `OwnershipTest`,
  `OwnershipProvenanceTest`, `AnchorTest`, `GroupDrainTest`, `InterruptedTest`,
  `LaunchOnSpawnTest`, `LivenessZombieTest`, the golden transcripts and `tests.test_resume`.

### D. Documentation and full verification (CP3)

- `docs/guide/workers.md`: under "Owned processes, the daemon list and the drain bound", a short
  paragraph: while it supervises a worker, the Controller collects every finished child it holds
  as a subreaper on every tick, by pid; collecting a zombie is not owning it; the same happens
  between launches, whatever the gap. It never collects a child it spawned in its own session,
  so an embedder's or a callback's ordinary subprocesses keep their exit statuses; an orphan
  such a subprocess leaves behind in that session is collected once the Controller has seen it
  as that subprocess's descendant; a child that an
  embedder starts in a new session while a launch runs in the same process, and whose status
  it reads, must be spawned through `worker.exclude_from_reaping(spawn)` -- entered before the
  spawn, with a callable that returns the `Popen` -- and waited for inside that block (I6).
- `docs/adr/0004-worker-lifecycle-ownership.md`: a dated "Amendment (1.4.1)" note after the
  paragraph that says the Controller "reaps only its own adopted zombies by specific pid": it now
  records and reaps them on every tick, in every state, and before it gives up the subreaper; the
  decision itself is unchanged.
- `docs/guide/development.md`: a "Throwaway Git repositories" rule: create them with
  `fixtures.git_init`/`fixtures.git_clone`; the guard test enforces it; why.
- The pull request body is the 1.4.1 release notes (no `docs/releases/` file, per
  `docs/README.md`): the leak, the fix, the test hygiene, and the operator step (install 1.4.1
  between Workflow Manager milestones, then drop `--max-steps 1`).
- The orphan measurement (Verification) is recorded in the checkpoint notes and the test results.

## Checkpoints

<!-- registry table: generated by workflow_state.render_registry_markdown, never hand-edited -->

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Test hygiene: tests/fixtures.py git_init and git_clone writing maintenance.auto=false and gc.auto=0 into each throwaway repository's own config, every git init and git clone in tests/ routed through them, an AST guard test (list, helper and shell-string forms, tests/workflow_releases/ excluded) that no other site creates a repository, and a read-back test with GIT_CONFIG_* cleared | - | 2 | 1 |
| CP2 | Reaping every finished child in every state: controller/worker.py reading /proc/<pid>/task/<tid>/children (full /proc fallback, rate-limited), a session field in _ProcStat, recording every child except any launch's worker and anchor, the baseline, children the process spawned in its own session (same-session orphans collected once observed as another child's descendants, _FOREIGN_BORN) and explicit exclusions (exclude_from_reaping) and reaping recorded pids on every supervision tick, in the drain, at finish, detach and interrupt and after the subreaper is given up; a between-launch reaper thread while recorded children remain; cli reaping between steps and at the end of run and step; fake_claude orphan_burst; the RUNNING, WAITING and DRAINING regression tests with per-state tick-derived bounds with the red run at 6a24f64, the between-launches, paused-boundary, same-session-grandchild, final-sweep ordering and sweep unit tests, the waited-for-child exit-status tests, the observe.py no-spawn test, ownership and published state unchanged | - | 3 | 1 |
| CP3 | Documentation and full verification (terminal): docs/guide/workers.md reaping paragraph, docs/guide/development.md throwaway-repository rule, ADR 0004 amendment note, the orphan measurement at 6a24f64 and at the head under a counting subreaper, the unchanged-path check, the 1.4.1 notes as the pull request body, and the full sharded suite | CP1, CP2 | 1 | 1 |

### CP1 -- test hygiene: throwaway repositories without automatic maintenance

- Files: `tests/fixtures.py`, every test module and fixture with a `git init` or `git clone`
  (including `tests/test_runtime.py`'s two `os.system` sites), `tests/test_fixtures_git_hygiene.py`
  (new). Nothing under `tests/workflow_releases/` changes.
- Done when: the guard test and the read-back test pass; the full sharded suite passes; the diff
  outside `tests/` is empty.
- Why first: every later full-suite run in this milestone then makes far fewer orphans under the
  lanes' Controller.

### CP2 -- reaping every finished child in every state

- Files: `controller/worker.py`, `controller/cli.py`, `tests/fake_claude.py`,
  `tests/test_fake_claude_contract.py`, `tests/fixtures.py` (`isolate_idle_reaper`),
  `tests/test_worker.py`, `tests/test_cli.py`.
- Done when: the tests of Design C pass; the regression test's red run against `6a24f64`'s
  `worker.py` is recorded; the golden generators pass with `--check`; the full sharded suite
  passes.

### CP3 -- documentation and full verification (terminal)

- Files: `docs/guide/workers.md`, `docs/guide/development.md`,
  `docs/adr/0004-worker-lifecycle-ownership.md`; `tools/test_timings.json` only if a removed or
  renamed test class makes `CommittedTimingsTest` fail (a new class needs no refresh).
- Done when: the full sharded suite passes; the orphan measurement below is recorded; the
  pull request body carries the 1.4.1 notes.

## Decisions for the reviewer and the user

1. **Per-repository configuration, not environment variables, for test hygiene.** The lanes'
   `GIT_CONFIG_COUNT` exports cut Workflow Manager's orphans from 42,158 to 36, but several of this
   repository's tests (and the Controller's own pinned Git environments) deliberately replace the
   environment, and an ambient `GIT_CONFIG_*` would also change what the environment-handling
   tests see. Writing the keys into each repository is independent of both. Recommended.
2. **No reaped-orphan counter in the job record.** Useful for telemetry, but it would add a
   record field and publications. Deferred to C3 (telemetry v0).
3. **Record-then-reap by pid, over reaping every zombie child directly.** Reaping a zombie child
   the moment it is seen would be simpler, but recording first keeps one reaping path
   (`_reap_adopted`), lets a still-running orphan recorded before the subreaper is released be
   collected later (I5), and keeps the exclusion rule (I2) in one predicate, `_reap_excluded`,
   checked again by that single reaping path before every `waitpid` (LPR3-01). Recommended.
4. **No leak check in `tools/run_tests.py`.** The regression test and CP3's measurement cover this
   repository; a runner-level check (as in Workflow Manager's M1b) can follow with C2 if wanted.
5. **Patch release.** The title is `fix:`, so `main.yml` releases **1.4.1** from `v1.4.0`.
6. **Same-session orphans by observed ancestry, over registering every waited-for child.** The
   kernel does not record a process's original parent (Investigation), so collecting a
   same-session orphan needs evidence the process gathers itself. The two ways are an allow list
   (collect only what was seen born to another process, `_FOREIGN_BORN`) or a deny list (collect
   every same-session child except those registered as waited-for). The deny list would make
   every unregistered `subprocess.run` -- in an embedder, a callback, or a concurrent
   `execute_step` on another thread (`tests/test_job.py` `InProcessConcurrencyTest`) -- liable
   to have its status taken and read as returncode 0, which hides failures. The allow list's
   failure mode is a same-session orphan born and orphaned within one tick staying uncollected,
   as every orphan was in 1.4.0; Git's maintenance, the incident's cause, is never in the
   session. Recommended.
7. **A background reaper between launches, over a reap in `cmd_run`'s pause loop.** Reaping in
   `_await_pause_file` would cover only `cmd_run`'s test-support pause; the gap between launches
   is also any embedder's time between calls. A daemon thread that exists only while `_ADOPTED`
   is non-empty covers every caller and ends by itself. Recommended.

`docs/TECHNICAL_DECISIONS.md` does not exist in this repository, so no "Open decision" row is
touched.

### Review round dispositions

**Revision 8** (`LOCAL_MODEL_PLAN_REVIEW` round 7, `REVISE`, reviewed revision 7, bundle
`a78bfe70…`, content `dc3c2ed8…`). Both findings were checked against `6a24f64` and revision 7's
text and accepted; none rejected. The checkpoints and requirements are unchanged.
- **LPR7-01** (the paused-boundary `Event` fires at the first boundary): accepted. Confirmed at
  `controller/cli.py` ~1016-1022: `cmd_run` calls `_await_pause_file(args.pause_file)` at the top
  of its loop, before every step including the first ("checked once before every job this loop
  starts, including the first"), and revision 7's wrapper set the `Event` on every entry, so it
  fired before step 1 ran. Design C now sets the `Event` only on an entry at which the pause
  file exists -- which the first entry never is, since only step 1's patched `launch` creates
  it -- and the test asserts the signalling entry is the second.
- **LPR7-02** (optional; `_reset_reaping_for_tests` can kill a planted record of a waited-for
  child): accepted. Confirmed that revision 7's Design B.5 justified the kill with "the predicate
  already vetted when it was recorded", which holds only for records made by `_record_adopted`,
  while Design C plants records in `_ADOPTED` directly. B.5 now applies `_reap_excluded` to the
  real `stat` before any signal (before `_FOREIGN_BORN` is cleared) and drops an excluded record
  untouched; the justification sentence is corrected and Design C adds the planted
  same-session-child case.

**Revision 7** (`MANUAL_EXTERNAL_PLAN_REVIEW` round 2, `REVISE`, reviewed revision 6, bundle
`f4100489…`, content `ff055574…`). Both findings were checked against revision 6's text and
accepted; none rejected. The checkpoints and requirements are unchanged.
- **Finding 1** (the paused-boundary and same-session regressions rely on elapsed time): accepted,
  both halves. Confirmed in revision 6's Design C: the paused-boundary orphan had a 1 s lifetime
  with nothing establishing it was still alive once `cmd_run` reached `_await_pause_file`, and the
  same-session parent slept 0.6 s with nothing establishing a sweep had seen the grandchild
  before it exited. Both tests now gate every exit on a release file the test creates only after
  it has asserted the required state: the paused-boundary test waits on an `Event` set by a
  wrapped `_await_pause_file` (`controller/cli.py` ~988, the pause loop), then asserts the
  orphan is recorded, alive and with a reaper thread running before releasing it; the
  same-session test waits for the grandchild's identity in `_FOREIGN_BORN` before releasing the
  parent, and for its pid in `_ADOPTED` (alive) before releasing the grandchild, with the launch
  held in `WAITING` by a `bash_bg` step's `command` (`tests/fake_claude.py`'s `bash_bg`
  accepts a `bash -c` string) until the test ends it.
- **Finding 2** (test isolation can strand a live recorded child): accepted. Confirmed that
  revision 6's `_stop_idle_reaper` left `_ADOPTED` untouched after at most 2 s of waiting. Design
  B.5 adds `worker._reset_reaping_for_tests`, which stops the thread and then, under
  `_SPAWN_LOCK` and against the real `/proc` (not the patchable seam), kills and collects by pid
  every record that is a real live child with the recorded start ticks and drops every other
  record without a signal or `waitpid`, then clears `_ADOPTED` and `_FOREIGN_BORN`;
  `isolate_idle_reaper` uses it in `setUp` and in a cleanup that runs after the test's own
  patches are undone. The isolation unit test is rewritten to check both kinds of record, and a
  reused-pid case is added; I8 states that the property holds across tests.

**Revision 6** (`LOCAL_MODEL_PLAN_REVIEW` round 5, `REVISE`, reviewed revision 5, bundle
`7c3c8897…`). Every finding was checked against `6a24f64` and accepted; none rejected.
- **LPR5-01** (the descendant walk can admit a child the process waits for): accepted.
  Confirmed that `_ProcStat` already carries `ppid` (`controller/worker.py` ~1785, parsed from
  field 4 in `_parse_stat`), so the check costs one comparison on a read the walk already makes.
  Design B.2 admits a descendant only when the same `stat` read shows `ppid` equal to the walked
  parent; I6 states it; the unit case is added (Design C, "An adopted same-session grandchild").
- **LPR5-02** (idle reaper's lifetime and exit handshake): accepted, both parts. Confirmed the
  precedent `unittest.mock.patch.dict(worker._ADOPTED)` at `tests/test_worker.py:2237` and that
  the planned between-launch, paused-boundary and same-session-grandchild tests leave recorded
  children running past a launch. Design B.5 states that the reap, the emptiness check and the
  reference clear happen in one `_SPAWN_LOCK` hold, and specifies `worker._stop_idle_reaper` and
  `tests/fixtures.py`'s `isolate_idle_reaper`, called from `setUp` by every reaping and
  seam-patching test; the handshake and isolation tests are added (Design C); CP2's files
  include `tests/fixtures.py`.
- **LPR5-03** (when `_FOREIGN_BORN` is pruned): accepted. Design B.2 names the pruning pass (each
  sweep re-reads the `stat` of every entry it did not just see, under `_SPAWN_LOCK`); I4 counts
  its cost; a unit case is added.
- **LPR5-04** (`session` field order): accepted. Confirmed `ppid: int = 0` is `_ProcStat`'s last
  field (~1785); Design B.1 declares `session` before it.

**Revision 5** (`MANUAL_EXTERNAL_PLAN_REVIEW` round 1, `REVISE`, reviewed revision 4, bundle
`69443a3f…`). Every finding was checked against `6a24f64` and accepted; none rejected.
- **Important 1** (same-session orphans excluded permanently): accepted. Confirmed that
  `_Ownership.scan` records any `ppid == me` child outside the baseline (`controller/worker.py`
  ~738-742), so a same-session orphan is recorded and, under revision 4's rule, never reaped;
  and that no `/proc` field distinguishes it from a child the process spawned (Investigation).
  The session rule is narrowed to an allow list of observed foreign-born identities,
  `_FOREIGN_BORN` (Design B.2, "Same-session orphans"; I2, I5, I6), with the residual stated and
  Decision 6 recording why a deny list was not chosen. The missing test is added (Design C, "An
  adopted same-session grandchild is reaped").
- **Important 2** (a live recorded child becomes a zombie during a paused boundary): accepted.
  Confirmed at `controller/cli.py` ~988-996 (`_await_pause_file` loops while the file exists)
  and ~1014 (called at every orchestration boundary). A between-launch reaper thread runs while
  `_ADOPTED` is non-empty (Design B.5, new I8, Decision 7); the missing test is added (Design C,
  "A recorded child that exits during a paused boundary").
- **Optional** (`_ProcStat` has no session field): accepted. Confirmed at ~1780-1801. Design B.1
  specifies the `session` field, its parse from field 6, and how the fixtures set it.

**Revision 4** (`LOCAL_MODEL_PLAN_REVIEW` round 3, `REVISE`, reviewed revision 3). Every finding
was checked against `6a24f64` and accepted; none rejected.
- **LPR3-01** (the session rule bypassed by the other `_ADOPTED` writers): accepted, both of the
  finding's options together. Confirmed at `controller/worker.py` ~739-742 (`_Ownership.scan`
  records any `ppid == me` child outside the baseline), ~586-592 (`_reap_killed_children`) and
  ~550-561 (`_reap_adopted` waits on every recorded pid). The exclusion rules are now one
  predicate, `_reap_excluded`, applied by every `_ADOPTED` writer through `_record_adopted` and
  again by `_reap_adopted` itself against a fresh `stat` before each `waitpid` (Design B.2); the
  owned-set logic is unchanged (I3, B.6). The Investigation's claim is corrected; I2 and I6 are
  restated as properties of the reaper; unit tests are added (Design C, "Every `_ADOPTED` writer
  and the reaper apply the predicate").
- **LPR3-02** (spawn-to-registration window): accepted, both of the finding's options: reap-time
  checking (a) and a process-wide `_SPAWN_LOCK` held across each protected spawn and its
  registration and by every record-and-reap step (b). Confirmed at ~990 (worker `Popen`) and
  ~842 (`_spawn_anchor`). Option (a) alone leaves a child that exits before registration
  reapable by another thread's tick; the lock closes that. `exclude_from_reaping` now takes the
  spawn callable and is entered before the spawn (Design B.2), so Design C's second I6 case is
  writable in the stated order; unit tests are added (Design C, "No spawn-to-registration
  window"); the guide documents that form (Design D).
- **LPR3-03** (incomplete new-session inventory): accepted. Confirmed the six added sites
  (`tests/test_worker.py` ~1085, ~1155, ~1802; `tests/test_resume.py` ~2192, ~2849;
  `tests/harness_contract/capture.py` ~356). They are added, and CP2's rule is now "every site a
  fresh grep finds", with the list informative only.
- **LPR3-04** (red-run expectation for `WAITING`): accepted. Confirmed at ~1177-1179 and
  ~1400-1405: 1.4.0 records while `WAITING` but never reaps there, and in the drain reaps every
  0.2 s but records only at the 1 s `next_scan`. `RUNNING` and `WAITING` must be red against
  `6a24f64`; `DRAINING` is expected red and recorded as observed.
- **LPR3-05** (I5 against B.4): accepted. I5 now names the full exclusion list.

**Revision 3** (`LOCAL_MODEL_PLAN_REVIEW` round 2, `REVISE`, reviewed revision 2). Every finding
was checked against `6a24f64` and accepted; none rejected.
- **LPR2-01** (bound assumes a 0.05 s tick): accepted, the first option (slow the burst).
  Confirmed at `controller/worker.py` ~376-378 and ~1100-1107 (`_poll_interval` drops to 0.2 s
  after 1 s without stream bytes) and ~280/~1400-1402 (the drain sleeps 0.2 s). The sweep stays
  on the existing ticks (I4 now says so); the burst is `count=150`, `spacing=0.02` (~10 orphans
  per 0.2 s tick), and Design C tabulates each variant's tick, expected peak and bound (30, three
  times the peak), with the minimum sample count raised to 75 to match the 3 s burst.
- **LPR2-02** (sweep takes statuses of children the process waits for): accepted, with a
  structural rule plus option (b) for the residue. Confirmed at
  `tests/test_lifecycle_orchestration.py` ~1761-1772 and ~2100-2127 and the threaded-launch sites
  the finding lists. A worker descendant can never be in the Controller's session (the worker is
  a session leader; `setsid`/`setpgid` cannot join another session), so the sweep skips every
  child in the process's own session: every such site is covered with no test change. The
  residue -- a new-session child started by another thread during a launch and waited for -- is
  handled by `_LAUNCH_CHILDREN` for overlapping launches and by `worker.exclude_from_reaping` for
  everything else; the test-side new-session sites are enumerated (Investigation) and checked in
  CP2. I2 and I6 are restated; tests are added (Design C, "Children the process waits for keep
  their status").
- **LPR2-03** (guard rule 3 too loose and too narrow): accepted, both halves. The optional
  argument now follows only the five options of rule 1, and rule 3 reads every non-docstring
  string constant or f-string (the widened rule matches only the two known `tests/test_runtime.py`
  sites at `6a24f64`); the requested self-tests are added.
- **LPR2-04** (when the worker's pid enters the holder): accepted. Design B.4 gives the order:
  the pid right after `Popen` returns, pid-only, then the start ticks once captured; a unit test
  covers a failing capture.

**Revision 2** (`LOCAL_MODEL_PLAN_REVIEW` round 1, `REVISE`, reviewed revision 1). Every finding
was checked against `6a24f64` and accepted; none rejected.
- **LPR1-01** (final sweep before the subreaper reset): accepted. Confirmed at
  `controller/worker.py` ~524-541 (`_child_subreaper`'s `finally` resets `prctl`) and ~976-978
  (`launch` enters it on its `ExitStack`). The callback is now registered before
  `_child_subreaper()` is entered, so it runs after the reset (Design B.4); I5 names that
  ordering and the refcount case; a unit test covers it (Design C, "The final sweep's ordering").
- **LPR1-02** (bound 40 above the ceiling of 30): accepted, the first option. The bound is 30, a
  fixed ceiling; a stall allowance of 5 samples above it (at most 10 with evidence) and a
  minimum sample count replace raising the bound (Design C, Open questions).
- **LPR1-03** (guard misses sites, vendored trees undecided): accepted. Confirmed
  `tests/test_runtime.py:39` and `:53` (`os.system(f"git init -q {…}")`) and
  `tests/workflow_releases/2.6.0/scripts/workflow_state.py:11736`. The guard's rules are stated
  exactly (subcommand after option tokens, helpers, shell strings), `tests/workflow_releases/` is
  excluded by exact prefix with the reason, the self-tests are listed, and the inventory and CP1's
  files include `tests/test_runtime.py`. A repository-wide search at `6a24f64` found no other
  string-form `git init`/`git clone` in `tests/` outside `tests/workflow_releases/`, other than
  the docstring at `tests/test_managed_repo.py:6`, which the guard does not read.
- **LPR1-04** (I3 wrong for the `killpg` form): accepted. Confirmed at `_group_members`'s
  docstring (`controller/worker.py` ~303-312). I3 now says earlier reaping can only shorten or
  skip a drain; the migration note and the published-signature unit test are scoped to match.
- **LPR1-05** (`cmd_resume`'s `finally` reap does nothing): accepted, dropped from Design B.5, with
  the reason (`controller/cli.py` ~1067-1069).
- **LPR1-06** (worker exclusion with `None` start ticks): accepted. Confirmed at
  `_Ownership.scan` (`controller/worker.py` ~739). I2 and Design B.2 state the rule; a unit test
  covers it.

## Open questions

None blocking. The regression tests' bound (30 zombies per sample, three times the expected
per-tick peak at the 0.2 s tick) is a ceiling and is never raised. If CI's 4-vCPU runners need more slack, the implementer may raise
the number of samples allowed above it from 5, with measured evidence, to at most 10 (200 ms).
A broken sweep still exceeds the bound in about 120 samples, so the `RUNNING` test stays red
against `6a24f64`.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-child-process-reaping-artifacts.json` starts from
`generate_artifacts_declarations(..., work_item_type="product")` and is fitted to this plan's
footprint the same way as the previous Controller milestone's declaration.

**Plan stage.**
- Protected: this plan, its registry and its mapping.
- It inherits the template exclusions and adds these as excluded implementation content:
  `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`, `docs/releases/`,
  `pyproject.toml`, `setup.py` and `docs/README.md`.

**Implementation stage.**
- Protected prefixes: `controller/`, `tests/`, `tools/`, `.workflow-controller/`, `docs/guide/`,
  `docs/releases/`, and the inherited `docs/adr/`.
- Protected paths: `README.md`, `docs/README.md`, `pyproject.toml`, `setup.py`, the four rendered
  workflows (`validate.yml`, `ci.yml`, `main.yml`, `pr-title.yml`), `CLAUDE.md` and the artifacts
  file itself. This milestone changes none of these paths except under `controller/`, `tests/`,
  `docs/guide/`, `docs/adr/` and possibly `tools/test_timings.json`; the rest stay protected so
  that any change would be reviewed.

`docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md` and `docs/ai-workflow/` stay excluded, as narrative
and bookkeeping. `.github/workflows/workflow-conformance.yml` stays under the inherited `.github/`
exclusion (the Workflow Manager owns it). `scripts/`, `.claude/commands/` and `.workflow-manager/`
keep their inherited exclusion; this milestone never edits this repository's installed Workflow.

## Verification

- **Per checkpoint.** The named test modules, then the full sharded run
  (`python3 tools/run_tests.py`) before each checkpoint commit. `PYTHONPATH=.` is never set.
- **Inside a Controller-launched worker.** The worker's own Controller is a subreaper, so orphans
  the tests make are re-parented to it and some existing orphan-reap tests fail for that reason
  alone. Such runs go through a reaping-subreaper wrapper (the wrapper marks itself
  `PR_SET_CHILD_SUBREAPER`, runs the suite, and reaps with `waitpid(-1)` in its own process), and
  the checkpoint notes say so.
- **CP2.** The red run of the regression test against `6a24f64`'s `controller/worker.py`, and
  every `tests/golden/generate_*.py --check` (without `--check` a generator rewrites its golden).
- **CP3, the orphan measurement.** The full sharded suite run under a counting subreaper wrapper
  with `GIT_CONFIG_*` cleared, once at `6a24f64` and once at the milestone head: the number of
  orphans re-parented to the wrapper, and how many were Git. Expected: from about 1,000 to a few
  dozen, none of them Git maintenance.
- **CP3.** `git diff 6a24f64 -- .workflow-controller/ pyproject.toml setup.py .github/` is empty.
- **CI.** PR CI on the milestone's Draft PR must be green before functional review. Review rounds
  read the PR's checks first.

## Migration / data-integrity notes

- No record, schema, policy or binding changes. Job and run records written by 1.4.1 have 1.4.0's
  format. For the same process tree they are byte-identical, except that under the `killpg` form
  of the group-drain test a zombie left in the worker's process group can make 1.4.0 publish a
  `DRAINING` (or a longer drain) that 1.4.1 skips or shortens (I3).
- A 1.4.0 Controller and a 1.4.1 Controller can reattach to each other's jobs: reattaching adopts
  nothing in either version.
- Adopters see only fewer zombie processes under a long-running Controller. The lanes' stopgap
  (`--max-steps 1`) keeps working with 1.4.1 and can be dropped once 1.4.1 is installed.
