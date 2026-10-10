# Active Milestone

## Status

**Self-reviewed; awaiting the local implementation review.** `workflow-controller-usage-budget` (`docs/ROADMAP.md` step C8, section 11.6): the
Controller reads the Claude and Codex usage windows itself, forecasts a job, admits it atomically against
the readings and the other lanes' reservations, and pauses a run before a limit. Plan:
`docs/ai-workflow/CONTROLLER_USAGE_BUDGET_PLAN.md` (revision 11, approved by policy at `03ddec0`; base
commit `f516e76`; pull request title `feat: a usage budget that pauses a run before a Claude or Codex limit`).
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for the checkpoint states.

| checkpoint | state |
| --- | --- |
| CP1 `controller/usage.py` (readers, shared record, merge, admission, forecast, decision) | complete, verified by `tests/test_usage.py` |
| CP2 settings generation 4 and the `--usage-cap` flags | complete, verified by `tests/test_settings.py` (`UsageRowsTest`), the regenerated no-policy golden and the full suite |
| CP3 the gate, the reservation and the run loop (exit 17) | complete, verified by `tests/test_usage_gate.py` (38 tests), the regenerated no-policy golden and the full suite |
| CP4 the `usage` subcommand and the manual-worker gate | complete, verified by `tests/test_usage_command.py` (57 tests) and the full suite |
| CP5 documentation and roadmap | complete, verified by `tools/check_docs.py` and the full suite (3291 tests; only the two Git 2.56 trailer failures) |

CP1 added `controller/usage.py` (a leaf below `job`, `observe` and `cli`), `runtime.usage_lock`,
`errors.UsageRecordError`, a golden stream (`tests/golden/claude_stream_usage_limit_rejected.jsonl`,
sanitised from a real `rejected` stream) and `tests/test_usage.py`.

CP2 added the sixteen `usage.*` rows (table generation 4, `TABLE_GENERATION = 4`; the generation invariant is
now `>=`), `run --usage-cap` and `step --usage-cap` (`usage --usage-codex-cap` arrived with the `usage`
subcommand in CP4), and regenerated `tests/golden/no_policy_lifecycle.json` (only the new `controller_settings`
keys moved, pinned by `tests/test_trunk_preflight.py`). The full suite fails only the two Git 2.56 trailer tests
that fail on the base too.

CP3 added `UsageHold`, the `usage_gate` argument and the `usage` job-record block in `controller/job.py`
(bind before publish, renewal ticker, end figures in the `COMPLETED` write, the replay sweep in
`_execute_step_locked` and `resume`, the `_reattach` accounting), the gate builder, the waiting `run` loop and
exit 17 in `controller/cli.py`, the `observe` rendering, the `CODEX_HOME` test redirection and the exit-17 rows
in `docs/exit-codes.md` and ADR 0001. The no-policy golden gained each job's `usage` block. The full suite
fails only the two Git 2.56 trailer tests.

CP4 added the `usage` subcommand to `controller/cli.py` (dispatched before pinning like `follow`: it writes only
the shared usage record and `usage-events.jsonl`): the view, `--check`, `--wait` (re-evaluating after every
wait, bounded by `usage.max_wait_seconds`), `--reserve`, `--renew`, `--release` with `--outcome` and
`--stream`, the role/model/repository/run context and the `--usage-cap`/`--usage-codex-cap` flags. `usage.admit`
gained `reserve=False` for a check. Exit status 1 (`usage --renew`/`--release` of an unknown token) is on the exit-code
page and in ADR 0001.

CP5 documented the budget: the sixteen `usage` rows and the usage files in `docs/guide/runtime.md`, the `usage` command in `docs/guide/commands.md`, `docs/run.md` and `docs/common-problems.md` (exit 17, timed pause versus cap), the exit-17 row in `docs/exit-codes.md`, `docs/adr/0011-usage-budget.md` and its README row, and `docs/ROADMAP.md` (C8 complete; C8b and C8c added with sections 11.6.1 and 11.6.2; C7 and C5 amended; "Next" names C5).

Self-review (`SELF_REVIEWING_IMPLEMENTATION`, 2026-10-10): the full diff from `f516e76` was reviewed against D1-D13.
No Blocking or Important findings. One Optional finding was fixed: `docs/guide/commands.md` and the `cmd_usage`
docstring said `usage` writes only the shared record and `usage-events.jsonl`, but the replay sweep it runs first
(plan D6, "on a `usage` read") also marks a terminal job record a crash left unaccounted `accounting: "done"`. The
text now says so. Not changed, by design: a Ctrl-C in the pure record-building lines between the gate and
`bind_job` leaves an unbound reservation. That window is no wider than the one inside `usage.admit` itself, and
the lapse and seven-day abandonment backstop covers both.

Full verification: `python3 tools/check_docs.py` exit 0; `python3 tools/run_tests.py` (under a reaping subreaper,
without `FORCE_COLOR`): 3300 tests in 8 shards, coverage exact, 2 failures. Both are the Git 2.56 trailer-rule
tests (follow-up 12), which also fail on the base.

## Functional review checklist

### Functional review round 2 (implementation revision 15)

Round 1 ran F0-F9 at revision 13 (evidence `320b77b`); all flows passed and the six findings
(`.ai-review/archive/workflow-controller-usage-budget/functional-review-round1.md`) were bounded. They were fixed in
`a03307a` (revisions 14 and 15, the latter saying the unreadable-record warning is printed once per sweep). This round
re-tests each fix. Every fixed behaviour has an explicit step marked **[round 1 Fn]** below; run those first, then the
remaining flows (all of F0-F9 keep their ids).

- **F1 (defect)**: an unnamed run/repository cap is no longer evaluated, even below the 8% forecast. Re-test: **F3
  step 8** (and **F3 step 9**, the named counterpart). `step` and `run` name both scopes, so **F5** still holds.
- **F2 (docs)**: a repeated `--outcome not_started` release exits 1 (the token was forgotten). Re-test: **F6**.
- **F3 (docs)**: deleting a damaged record leaves its pending jobs unaccounted; repair or restore it in place to
  settle them. Re-test: **F7** last-but-one bullet and **F9** (`common-problems.md`, `runtime.md`).
- **F4 (usability)**: the "accounting is skipped" warning is not printed by `usage`, `usage --json` or `usage --check`
  (they report the error and exit 20) and is printed once per `resume` sweep. Re-test: **F7**.
- **F5 (checklist wording)**: F1's `no reading` case needs a fresh runtime root; F7 now says which commands print the
  warning. Re-test: **F1**, **F7**.
- **F6 (optional)**: a five-hour-only reading prints `no reading: claude weekly` on every check; correct. See **F4**.
- No finding was deferred to a remediation child.

Milestone `workflow-controller-usage-budget` (C8), implementation revision 15. Every flow runs in scratch
clones and scratch directories only; nothing touches `~/.config/workflow-controller`,
`~/.local/state/workflow-controller`, `~/.codex`, the real repository's runtime root, or a real `claude`, `codex`
or `gh`. Each flow below was run once by the preparer against this branch (the round-1 re-tests again at revision 15); the expected output is what it printed
(times, epochs and paths differ). `F0` is the setup every other flow depends on.

### Setup (F0)

```bash
set -u                                                 # an unset variable aborts instead of expanding to nothing
SRC=/home/rodrigo/Workspace/workflow-controller        # read-only source; never run step/run in it
W=$(mktemp -d)                                         # everything lives under $W
git clone -q --branch milestone/workflow-controller-usage-budget "$SRC" "$W/clone"
git -C "$W/clone" checkout -q "$(git -C "$SRC" rev-parse HEAD)"
export W CLONE=$W/clone
# a scratch bare origin for the clone, so nothing can push to the real remote
git init -q --bare "$W/origin.git" && git -C "$CLONE" remote set-url origin "$W/origin.git"
# a fake gh first on PATH (the flows never reach the forge; this is a guard)
mkdir -p "$W/bin" && printf '#!/bin/sh\nexec python3 %s/tests/fake_gh.py "$@"\n' "$CLONE" > "$W/bin/gh" && chmod +x "$W/bin/gh"
# guard stubs: a scratch claude and codex that refuse, so a mistake can never reach the real binaries
for b in claude codex; do printf '#!/bin/sh\necho "scratch guard: real %s must not run" >&2\nexit 99\n' "$b" > "$W/bin/$b"; chmod +x "$W/bin/$b"; done
export PATH=$W/bin:$PATH      # the scratch directory comes first
```

Rules for every script and shell below: start with `set -u`, `export` every variable a child process needs (`W`,
`CLONE`, `RT`, `T`, `ST`, `CODEX_HOME`, `PATH`; an unexported variable once let a first script reach the real
`claude` and `codex`), and keep `$W/bin` first on `PATH`. Check with `command -v claude codex gh` (all three under
`$W/bin`) before the first flow. `ctl` passes `--claude-binary` explicitly, so the Controller itself never uses the stubs.

Three helper files (copy them verbatim). `env.sh` isolates the environment and defines `ctl`; `harness.py` builds a
managed scratch target (a `PLANNING` work item and a stub `workflow-manager`, using the repository's own test
fixtures) and prints its path; `seed.py` writes Claude readings into a runtime root and a Codex rollout into
`$CODEX_HOME`, using the real clock.

```bash
cat > "$W/env.sh" <<'EOS'
export XDG_CONFIG_HOME=$W/cfg XDG_STATE_HOME=$W/state XDG_CACHE_HOME=$W/cache CODEX_HOME=$W/codex
export PYTHONPATH=$CLONE RT=${RT:-$W/rt} T=$W/t/target ST=$W/cfg/workflow-controller/settings.json
mkdir -p "$(dirname "$ST")"
ctl() { python3 -m controller --runtime-dir "$RT" --workflow-manager "$W/t/workflow-manager" \
        --claude-binary "$CLONE/tests/fake_claude.py" "$@"; }
EOS
cat > "$W/harness.py" <<'EOS'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests import fixtures, test_job
dest = Path(sys.argv[2]); dest.mkdir(parents=True, exist_ok=True)
repo = test_job._build_target(dest, phase="PLANNING", governing_workflow_version="2.1")
fixtures.write_stub_workflow_manager(dest / "workflow-manager")
print(repo.root)
EOS
cat > "$W/seed.py" <<'EOS'
"""claude RT FIVE_PCT FIVE_RESET_IN_S [WEEKLY_PCT WEEKLY_RESET_IN_S] | codex HOME PRIMARY SECONDARY [F_IN W_IN]"""
import sys, time, json
from pathlib import Path
from controller import usage
kind, a = sys.argv[1], sys.argv[2:]
now = time.time()
if kind == "claude":
    rt, five, five_in, *rest = a
    rs = [usage.make_reading("claude", usage.FIVE_HOUR, float(five), now + float(five_in), now, "scratch")]
    if rest:
        rs.append(usage.make_reading("claude", usage.WEEKLY, float(rest[0]), now + float(rest[1]), now, "scratch"))
    usage.record_reading(rt, rs)
else:
    home, p, s, *rest = a
    f_in, w_in = (float(rest[0]) if rest else 3600), (float(rest[1]) if len(rest) > 1 else 7 * 86400)
    path = Path(home) / "sessions/2026/10/10/rollout-a.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "type": "event_msg",
        "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": float(p), "window_minutes": 300, "resets_at": int(now + f_in)},
            "secondary": {"used_percent": float(s), "window_minutes": 10080, "resets_at": int(now + w_in)}}}}) + "\n")
EOS
. "$W/env.sh"; (cd "$CLONE" && python3 "$W/harness.py" "$CLONE" "$W/t")   # prints $W/t/target
```

Every flow starts a shell with `export W CLONE; . "$W/env.sh"`; where a flow needs its own runtime root it sets
`RT=$W/rtN` first (re-source `env.sh` or just `export RT=...`; `ctl` reads `$RT`). Remove `$ST` (the scratch
settings file) at the start of a flow unless it says otherwise. Use bash; `S="python3 $W/seed.py"` below. A worker launched
by `step`/`run` is the repository's `tests/fake_claude.py`, which changes no workflow state, so a step that
**starts** the worker ends with exit **30** (worker failed its postcondition); that exit, with a job record and
`usage.accounting: "done"`, is the "the gate let it through" result. Run `step`/`run` only against `$T`, always
with `</dev/null`.

### Flows

**F1 -- the `usage` view, text and JSON, from seeded readings.**
`RT=$W/rt1; $S claude $RT 40 3600 60 432000; $S codex $CODEX_HOME 30 20; ctl usage; echo $?`
Expect exit 0 and two blocks. `claude:` five-hour 40% (threshold 85%), weekly 60% (threshold 95%), each with a
local-time reset plus `epoch N`, `read N s ago from scratch`; `reservations: 0 live, 0 lapsed`; `forecast: the
provider default (no jobs recorded)`; `run cap: not evaluated (no --run-id)`; `repository cap: not evaluated (no
--repo)`. `codex:` five-hour 30%, weekly 20%, read from `$CODEX_HOME/sessions/2026/10/10/rollout-a.jsonl`.
Then `ctl --json usage | python3 -m json.tool` (also `ctl usage --json`): one object with `at` and
`providers.claude|codex`, each with `windows.five_hour|weekly` (`percent`, `resets_at`, `observed_at`,
`age_seconds`, `source`), `thresholds` (85/95), `forecast` (8.0/1.0), `reservations: []`, `run: null`,
`repository: null`. `ctl usage --provider codex` shows only the Codex block. With no Codex rollout and **a fresh runtime root** (**[round 1 F5]** `RT=$W/rt1n CODEX_HOME=$W/nocodex ctl usage
--provider codex`) both windows say `no reading` and the exit is still 0; on the earlier root `$W/rt1` the stored Codex
reading is still shown, which is correct.

**F2 -- settings generation 4: the sixteen rows and the flags.**
`ctl settings show | grep '^usage'` prints sixteen `usage.*` rows, all `(default)`: `enabled=true`,
`pause_at_percent=85`, `weekly_pause_at_percent=95`, `codex_pause_at_percent=85`,
`codex_weekly_pause_at_percent=95`, `resume_grace_seconds=180`, `max_wait_seconds=21600`,
`reservation_seconds=7200`, the four `*default_job*` rows (8, 1, 8, 1) and the four `*cap_percent` rows `null`.
`ctl settings path` prints `$ST`. `ctl step --help` and `ctl run --help` list `--usage-cap PERCENT`; `ctl usage
--help` lists `--usage-cap` and `--usage-codex-cap`. After a `step` has filled the file (F10),
`python3 -c 'import json,os;d=json.load(open(os.environ["ST"]));print(d["_table_generation"],"usage" in d)'`
prints `4 True`. A bad flag value is a usage error, exit 2 (`ctl usage --check --usage-cap 0` prints `argument --usage-cap: must be
from 1 to 100, not 0`); a bad value in the settings file (`echo '{"usage":{"pause_at_percent":0}}' > $ST; ctl
settings show`) is a settings-file error, exit 20 (remove $ST afterwards).

**F3 -- strictest wins across account, repository and run, Claude and Codex separately.**
`RT=$W/rt3`, seed as F1 (`claude 40 3600 60 432000`, `codex 30 20`). Each case below `echo ... > $ST` first;
`rm -f $ST` for the first. Expect exit 0 and `go` for the unconstrained `ctl usage --check` (Claude 40% + forecast
8%). Then, for each, the exact final stderr line is a `stopped for the usage budget, nothing was started: ...`
sentence and the exit is **17**:
1. `{"usage":{"pause_at_percent":45}}` then `ctl usage --check`: `claude five-hour usage 40% + reserved 0% +
   forecast 8% reaches the 45% threshold -- a timed pause: run again after the resume time <local> (epoch N)`.
2. `{"usage":{"weekly_pause_at_percent":60}}`: `claude weekly usage 60% + reserved 0% + forecast 1% reaches the
   60% threshold ...` with the weekly reset time.
3. `{"usage":{"codex_pause_at_percent":35}}`: `ctl usage --check --provider codex` holds (`codex five-hour usage
   30% ... reaches the 35% threshold`, 17) while `ctl usage --check --provider claude` is `go` (0): the providers
   are independent.
4. `{"usage":{"run_cap_percent":5}}`: `ctl usage --check --run-id R1` holds (`the run's claude spend 0% +
   outstanding 0% + forecast 8% exceeds the run cap 5% -- no reset will lift this; raise usage.run_cap_percent or
   start a new run`, 17; stderr first says `run cap evaluated for run R1`).
5. `{"usage":{"repository_cap_percent":6}}`: `ctl usage --check --repo $T` holds (`the repository's claude spend
   ... exceeds the repository cap 6% -- a new run does not clear this repository's spending ... run again after
   the window resets at <local> (epoch N) (the spending then falls), or raise usage.repository_cap_percent`).
6. Flags beat the file for the run cap, in both directions: file `{"usage":{"run_cap_percent":50}}` and
   `ctl usage --check --run-id R1 --usage-cap 5` holds (cap 5%); file `{"usage":{"run_cap_percent":5}}` and
   `--usage-cap 50` is `go` (0). Same for Codex with `--usage-codex-cap` (`ctl usage --check --provider codex
   --run-id R1 --usage-codex-cap 5` holds, `... 50` is go) and the file rows `codex_run_cap_percent`,
   `codex_repository_cap_percent`, which do not affect the Claude check.
7. With several limits set at once (pause 45, run cap 7, repository cap 6) and both `--repo $T --run-id R1`
   named, a cap dominates a timed hold: the printed reason is a cap (never "a timed pause").
8. **[round 1 F1]** A cap counts only the scope you name, even below the forecast. Seed as above. With
   `{"usage":{"run_cap_percent":5}}` and no `--run-id`, `ctl usage --check` prints on stderr `run cap not evaluated
   (no --run-id)` (and `repository cap not evaluated (no --repo)`), then `go` on stdout, exit **0**; the same with a
   run cap of 7 (below the 8% forecast) and with `{"usage":{"repository_cap_percent":5}}` and no `--repo`. A run cap
   of 9 or more is likewise `go`. Nothing is held by a cap that is not evaluated.
9. **[round 1 F1]** The named counterpart still holds. `{"usage":{"run_cap_percent":7}}` and `ctl usage --check
   --run-id R1`: stderr `run cap evaluated for run R1`, exit **17**, `the run's claude spend 0% + outstanding 0% +
   forecast 8% exceeds the run cap 7%`. `{"usage":{"repository_cap_percent":5}}` and `ctl usage --check --repo $T`:
   `repository cap evaluated for <T>`, exit 17, `the repository's claude spend ... exceeds the repository cap 5%`.
   `step` and `run` name both scopes themselves, so `{"usage":{"run_cap_percent":5}}` and `ctl step $T </dev/null`
   exit **17** with the run-cap line and create no `jobs/` (also F5).

**F4 -- `usage --check` and `--wait`.**
`RT=$W/rt4; rm -f $ST; $S claude $RT 90 6` (90% used, resets in 6 s).
- **[round 1 F6]** A reading with only the five-hour window (`$S claude $RT 90 6` has no weekly figure) prints
  `no reading: claude weekly` on stderr on every check and `--wait`; that is correct and not a failure.
- `ctl usage --check; echo $?` prints the `claude five-hour usage 90% + reserved 0% + forecast 8% reaches the 85%
  threshold -- a timed pause ...` line and exits **17**; `ls $RT` shows `usage.json`, `usage.lock` and no `jobs/`.
  `ctl usage --check --reserve` held: no token on stdout.
- `time ctl usage --wait; echo $?`: sleeps about 6 s plus `usage.resume_grace_seconds` (default 180 s -- so first
  `echo '{"usage":{"resume_grace_seconds":1}}' > $ST`), then prints `go` and exits **0**. `tail -2
  $RT/usage-events.jsonl` shows `usage_paused` then `usage_resumed`.
- `RT=$W/rt4b; echo '{"usage":{"max_wait_seconds":3,"resume_grace_seconds":1}}' > $ST; $S claude $RT 90 300;
  ctl usage --wait; echo $?`: returns at once, exit **17**, message ends `(waiting for it would exceed
  usage.max_wait_seconds, 3 s)`.
- `--wait` gives up on a cap, it never sleeps: `echo '{"usage":{"run_cap_percent":5}}' > $ST; ctl usage --wait
  --run-id R1` exits 17 immediately.
- With `{"usage":{"enabled":false}}` in `$ST`, `ctl step $T </dev/null` does not read or sweep the record (a held
  state does not hold it); `ctl usage` still shows the readings.

**F5 -- a step held by the budget exits 17 before any job record exists; a run waits, then continues.**
`RT=$W/rt5; echo '{"usage":{"resume_grace_seconds":1}}' > $ST; $S claude $RT 90 5`.
- `ctl step $T </dev/null; echo $?`: the same `stopped for the usage budget, nothing was started: ...` line,
  exit **17**; `ls $RT/jobs` fails (no such directory) -- no job record, nothing half finished.
- `RT=$W/rt5b; $S claude $RT 90 6; time ctl run --max-steps 1 $T </dev/null; echo $?`: no output while it
  waits (about 7 s), then the fake worker runs and the exit is **30**. The run's `events.jsonl`
  (`cat $RT/runs/*/events.jsonl`, key `event`) reads, in order: `run_started, step_started, usage_paused,
  step_started, usage_resumed, job_started, job_ended, run_ended`. The pause does not count as a step
  (`--max-steps 1` still allowed the one job), and `ls $RT/jobs` shows one record whose `usage.accounting` is
  `done` and `usage.charged` is `8.0`.
- A cap stops `run` as well, with no wait: `echo '{"usage":{"run_cap_percent":5}}' > $ST; RT=$W/rt5c; $S claude
  $RT 10 3600; ctl run --max-steps 1 $T </dev/null; echo $?` prints the run-cap line, exits **17**, no `jobs/`.
- `ctl step --usage-cap 5 $T </dev/null` behaves like the file setting (the run-cap line, exit 17, no `jobs/`).

**F6 -- reservation and release, idempotent.**
`RT=$W/rt6; rm -f $ST; $S claude $RT 40 3600 60 432000`.
- `TOK=$(ctl usage --check --reserve --role review --model opus --repo $T --run-id R2 2>/dev/null); echo $TOK`
  prints a 32-hex token as the only stdout line, exit 0. `ctl usage --provider claude` now shows `reservations: 1
  live` and a line `<token8> live review/opus 8% five-hour, 1% weekly, repository <T>, run R2`.
- `ctl usage --renew $TOK; echo $?` prints a renewed message, exit 0.
- Two parallel reserves never both take the last headroom: with `{"usage":{"pause_at_percent":55}}` (40% + 8% fits
  once, not twice) two `ctl usage --check --reserve` in a row give a token then exit 17.
- Release without charge: `ctl usage --release $TOK --outcome not_started; echo $?` prints `released; nothing
  charged`, exit 0; the reservation is gone from the view and nothing is spent. **[round 1 F2]** A second release of that
  token with `--outcome not_started` exits **1** (`error: the token ... is unknown; nothing was accounted`): a
  `not_started` release forgets the token, so the repeat is an unknown token, and `docs/guide/commands.md` and
  `docs/exit-codes.md` now say so. So do `--release deadbeef` and a `--renew` of a released token. (Repeating a
  release that carried a measurement or `ok` is the idempotent case, below: exit 0.)
- Release with a measured stream: reserve a new token, then build a stream whose rate-limit event matches the
  seeded window and release with it:
  ```bash
  R5=$(python3 -c "import json,os;print(json.load(open(os.environ['RT']+'/usage.json'))['readings']['claude']['five_hour']['resets_at'])")
  RW=$(python3 -c "import json,os;print(json.load(open(os.environ['RT']+'/usage.json'))['readings']['claude']['weekly']['resets_at'])")
  TOK=$(ctl usage --check --reserve --role review --model opus --repo $T --run-id R3 2>/dev/null); sleep 1
  python3 -c "import sys;sys.path.insert(0,'$CLONE');from tests import fake_claude as f;open('$W/s.jsonl','w').write(f.stream_text([f.rate_limit(52.0,$R5,61.0,$RW),*f.default_events()]))"
  ctl usage --release $TOK --stream $W/s.jsonl; echo $?
  ```
  Expect `released; measured delta, charged 12.0%`, exit 0. Repeating the same release prints `already accounted;
  nothing changed`, exit **0**, and charges nothing again. `ctl usage --provider claude --repo $T --run-id R3`
  now shows five-hour 52%, weekly 61%, `review/opus: 12.0% five-hour, 1.0% weekly from 1 measured jobs`, `run cap
  none: spent 12%, outstanding 0%`, `repository cap none: spent 12%, outstanding 0%`. If the stream's window reset
  differs from the reserved window, the result is `unknown delta, charged 8.0%` (the forecast), not an error.
  `$RT/usage-events.jsonl` has a `usage_released` line (`reason` `measured` or `unknown`) and a
  `usage_complete_already_settled` line for the repeat.
- A Codex reservation refuses `--stream`: `T2=$(ctl usage --check --reserve --provider codex 2>/dev/null); ctl
  usage --release $T2 --stream $W/s.jsonl; echo $?` exits **2**.

**F7 -- a damaged or unreadable usage record.**
`RT=$W/rt7; rm -f $ST; $S claude $RT 10 3600; printf '{"version": 2, "garbage"' > $RT/usage.json; sha256sum
$RT/usage.json` (note the hash).
- **[round 1 F4]** `ctl usage`, `ctl --json usage` (also `ctl usage --json`) and `ctl usage --check` each print
  exactly one stderr line, `error: the usage record at <RT>/usage.json cannot be read: ...; repair or remove it, or
  set usage.enabled=false to run without the budget`, and exit **20**; they print **no** `usage accounting is
  skipped` line (the command itself fails, so nothing is "skipped"). `ctl step $T </dev/null` and `ctl run
  --max-steps 1 $T </dev/null` print the one-line `workflow-controller: the usage record cannot be read, so usage
  accounting is skipped (...)` warning first, then the same `error:` line, exit **20**. No job record is created by
  `step` here (`ls $RT/jobs` fails), and `sha256sum` shows the file **unchanged** after all of them.
- The job outcome survives damage that happens while a job runs: `RT=$W/rt7b; $S claude $RT 10 3600;
  FAKE_CLAUDE_WRITE_PATH=$RT/usage.json FAKE_CLAUDE_WRITE_TEXT='{"version": 2, "garbage"' ctl step $T
  </dev/null; echo $?`. The fake worker overwrites the record while the job runs. Expect the warning line on stderr,
  exit **30** (the worker outcome, not a usage error; the warning appears once), one job record with `status` `FAILED` and
  `usage.accounting` `pending` (left unaccounted), the bytes of `usage.json` exactly what the worker wrote, and
  `$RT/usage-events.jsonl` containing a `usage_record_unreadable` line. `ctl usage` then exits 20 as above.
- **[round 1 F4]** `ctl resume $T </dev/null` on that root (`ctl resume`'s exit is 0, it prints the existing job as
  `FAILED`) prints the skip warning **exactly once** for the whole sweep (`ctl resume $T </dev/null 2>&1 >/dev/null |
  grep -c 'accounting is skipped'` prints `1`). A further `ctl run --max-steps 1 $T` keeps the job's outcome too: the
  warning is printed, the record's status does not change, `usage.json` is still byte-identical (`sha256sum`).
- `usage.enabled:false` is the documented way through: `echo '{"usage":{"enabled":false}}' > $ST; ctl step $T
  </dev/null; echo $?` runs the job (exit 30) without reading the record, which stays untouched.
- **[round 1 F3]** Deleting a damaged record does not settle a job it left `pending`: after `rm $RT/usage.json`,
  `ctl usage` creates a fresh record and the F7b job stays `FAILED` with `usage.accounting` `pending` and no charge;
  restoring the original bytes (or repairing the file in place) is what lets the replay settle it. This is what
  `docs/common-problems.md` and `docs/guide/runtime.md` now say (check them in F9).
- A record written by a newer release is also never replaced: `printf '{"version": 3}' > $RT/usage.json`; `ctl
  usage` exits 20 with `... is not a version 2 object; repair or remove it ...` and the file is unchanged.

**F8 -- an older settings file (table generation 3) keeps working.**
`RT=$W/rt8; rm -f $ST; $S claude $RT 10 3600; ctl step $T </dev/null` (exit 30) fills `$ST` at generation 4. Then
rewrite it as a 1.7.x file:
```bash
python3 - <<'EOS'
import json, os
st = os.environ["ST"]; d = json.load(open(st))
d["_table_generation"] = 3; d.pop("usage", None)
dw = d["_defaults_written"]
d["_defaults_written"] = {k: v for k, v in dw.items() if not k.startswith("usage")} if isinstance(dw, dict) \
    else [k for k in dw if not k.startswith("usage")]
json.dump(d, open(st, "w"), indent=2)
EOS
```
`ctl settings show | grep -c '^usage.*(default)'` prints **16** (the rows resolve to defaults though the file has no
`usage` section); `ctl usage --check` prints `go`, exit 0; `ctl step $T </dev/null` exits 30 and the file is
afterwards at `_table_generation` 4 with a `usage` section holding the defaults (a value you set yourself, e.g.
`routing.*` or `run.max_steps`, is not moved). A user-set `usage.pause_at_percent` survives the fill.

**F9 -- documentation matches behaviour (read-only).**
In the clone: `python3 tools/check_docs.py` exits 0. Read `docs/guide/commands.md#usage`, `docs/run.md#pausing-for-usage-limits`,
`docs/guide/runtime.md` (usage rows, the shared record, "A `usage.json` this release cannot read"),
`docs/exit-codes.md` (rows 1 and 17), `docs/common-problems.md` (exit 17 and "The usage record cannot be read")
and `docs/adr/0011-usage-budget.md`; each statement you exercised in F1-F8 should be true. Note any that is not. Round 1 fixes: `commands.md` (a repeated
`not_started` release exits 1; the unnamed-cap rule), `common-problems.md` and `runtime.md` (deleting a damaged record
leaves its pending jobs unaccounted; repair or restore it in place), `runtime.md` (the warning is printed once per sweep).

### Known limitations and out of scope

- Recognising a usage-limit result *inside a worker stream* (a `rejected`/429 result mid-job) and waiting for it
  automatically is **C8b** and is not built: such a job fails (exit 30) as before; `common-problems.md` says so.
- The accumulator totals at the absolute numeric limit are an accepted optional follow-up (not a defect here).
- When `resume` re-attaches to a still-running job, the unreadable-record warning can be printed a second time (once
  for the sweep, once by the re-attach); an accepted optional follow-up, not a defect.
- The budget forecasts; it cannot stop a job that exceeds its forecast.
- Codex is gated only for workers you start yourself (`usage --provider codex ...`); the Controller does not launch
  Codex workers in this milestone.
- The `--stream` measurement reads Claude streams only (a Codex reservation refuses `--stream`).
- A reservation or run record in a deleted or replaced `usage.json` cannot be settled afterwards (the spend is
  forgotten by design, see `runtime.md`).
- Two Git 2.56 trailer-rule tests fail in the full suite on this branch and on its base (follow-up 12, unrelated).
- Automated evidence: `python3 tools/check_docs.py` exit 0; `python3 tools/run_tests.py`: 3300 tests, coverage
  exact, only those two failures (last full run at revision 15, by the local implementation review).

### Where to put findings

Write findings to `.ai-review/workflow-controller-usage-budget/feedback/FUNCTIONAL_REVIEW.md` (the resolved
feedback path), one entry per flow id above.

## Next action

The local implementation review is complete; the functional-review checklist above is ready for the independent tester. Findings go to `.ai-review/workflow-controller-usage-budget/feedback/FUNCTIONAL_REVIEW.md`; `/accept-milestone` follows a clean test and `/apply-functional-review` routes findings.
