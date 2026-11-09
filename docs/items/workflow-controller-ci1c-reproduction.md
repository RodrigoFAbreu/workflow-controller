# CI1c: reproducing the worker measurements

Part of [CI1c](workflow-controller-ci1c.md) (item id `workflow-controller-ci1c`). This page carries everything the
[measurements](workflow-controller-ci1c.md#measurements-cp1) were made with, so that any number in the table can be
reproduced from the repository alone: the experimental patch, the wrapper that reads CPU time, the drivers and the set-up.
Nothing here is part of the Controller; the patch is for a scratch clone only and is never committed.

## Set-up

1. Make a scratch directory with `mktemp -d` and a clone of base commit `065ca1e` inside it as `clone/`. Never use the
   working tree.
2. Plan the CI profile once, from inside the clone: `python3 tools/run_tests.py plan --profile ci --ci-placement` (write
   its JSON to `plan.json` next to `clone/`). The plan digest must match the one in the item page's plan.
3. Apply the patch below inside the clone (`git apply`).
4. Put `runone.py` and the drivers next to `clone/`. Edit nothing else.

CPU pinning: `taskset -c 0,1,8,9`, two physical cores and their SMT siblings, read from
`/sys/devices/system/cpu/cpuN/topology/thread_siblings_list` (here cpu0's siblings are `0,8` and cpu1's `1,9`). On another
machine set `CI1C_CPUS` to the two cores and their siblings.

The worker count is the environment variable **`CI1C_WORKERS`** (default 1). `runone.py` sets it for the run it starts, and
gives every run its own `TMPDIR` made with `mktemp` under `/tmp` and removed afterwards, so the runs share no scratch name.
The patch removes `CI1C_WORKERS` from its own environment before the tests run, because the tests call `execute_shard`
themselves and would otherwise fork workers inside a fake repository (this was the cause of the two discarded runs recorded
in the item page).

One run: `python3 runone.py <shard> <workers> <tag>`. It writes `runs/<tag>/summary.json` (wall, CPU from
`getrusage(RUSAGE_CHILDREN)`, failures, leaked processes, zombies, leftover `TMPDIR` entries) and the shard record.

## The patch (`tools/test_shards.py`, scratch clone only)

It divides the Controller atoms with the planner's own `assign_atoms`, **using each atom's estimated seconds from
`tools/test_timings.json`** (scaled per test when the recorded test count differs, and the profile's mean seconds per test
for an atom the profile does not know), as `estimate_atoms` does. An earlier version of this patch read the profile entry as
a number, but every entry is a dictionary, so every atom got the same estimate and the atoms were split by count; those runs
are discarded (see the item page).

```diff
diff --git a/tools/test_shards.py b/tools/test_shards.py
index 3da1022..c0e96c4 100644
--- a/tools/test_shards.py
+++ b/tools/test_shards.py
@@ -1916,6 +1916,41 @@ def execute_shard(plan: Mapping, index: int, results_dir: Path, repo_root: Path
         if units is not None:
             controller = [(key, suite) for key, suite in units if suite is not None]
             concurrent = plan["parameters"].get("conformance_lane", True)
+            _nw = int(os.environ.pop("CI1C_WORKERS", "1"))
+            _kids = []
+            if _nw > 1 and controller:
+                import pickle, tempfile as _tf
+                _prof = json.load(open(Path(repo_root) / "tools/test_timings.json"))["atoms"]
+                _mean = sum(v["seconds"] for v in _prof.values()) / max(1, sum(v["tests"] for v in _prof.values()))
+                _est = []
+                for k, su in controller:
+                    _n = su.countTestCases()
+                    _kn = _prof.get(k)
+                    _sec = (_kn["seconds"] if _kn["tests"] == _n else _kn["seconds"] / _kn["tests"] * _n) if _kn else _n * _mean
+                    _est.append(max(1, int(1000 * _sec)))
+                _parts = [p for p in assign_atoms(_est, min(_nw, len(controller))) if p]
+                for _i, _part in enumerate(_parts):
+                    _out = Path(results_dir) / f"worker-{index}-{_i}.pkl"
+                    _pid = os.fork()
+                    if _pid == 0:
+                        try:
+                            _wd = [_tf.mkdtemp(prefix="ci1c-w-") for _ in range(2)]
+                            os.environ["TMPDIR"], os.environ["XDG_STATE_HOME"] = _wd
+                            _tf.tempdir = _wd[0]
+                            _sub = [controller[j] for j in _part]
+                            with open(Path(results_dir) / f"worker-{index}-{_i}.log", "w") as _wl:
+                                _ao = {t.id(): k for k, su in _sub for t in su}
+                                _sq = _AtomSequence(_sub)
+                                _rn = unittest.TextTestRunner(stream=_wl, verbosity=2,
+                                    resultclass=partial(RecordingResult, planned=_ao))
+                                _r = _rn.run(_sq)
+                                _pl = [t for t in planned if t in _ao]
+                                _backfill(_r, _pl, _ao)
+                                pickle.dump((_r.wasSuccessful(), _r.entries, _r.fixture_errors,
+                                             _r.fixture_skips, _sq.seconds, _r.unmapped), open(_out, "wb"))
+                        finally:
+                            os._exit(0)
+                    _kids.append((_pid, _out))
             for stale in [*results_dir.glob(f"lane-{index}.*"), *results_dir.glob(f"lane-{index}-*")]:
                 stale.unlink(missing_ok=True)  # an aborted run's lane files
             lane = _ConformanceLane(
@@ -1925,7 +1960,21 @@ def execute_shard(plan: Mapping, index: int, results_dir: Path, repo_root: Path
             try:
                 if lane.keys:
                     lane.start()
-                if controller:
+                if _kids:
+                    import pickle
+                    ok = True
+                    for _pid, _out in _kids:
+                        os.waitpid(_pid, 0)
+                        if not _out.exists():
+                            problems.append(f"worker {_pid} died")
+                            ok = False
+                            continue
+                        _w, _e, _fe, _fs, _sec, _um = pickle.load(open(_out, "rb"))
+                        ok = ok and _w
+                        entries.update(_e); fixture_errors += _fe; fixture_skips += _fs
+                        atoms.update(_sec); problems += [f"unmapped unittest event: {x}" for x in _um]
+                    was_successful = ok
+                elif controller:
                     atom_of = {test.id(): key for key, suite in controller for test in suite}
                     sequence = _AtomSequence(controller)
                     runner = unittest.TextTestRunner(
```

## The wrapper (`runone.py`)

```python
import json, os, resource, subprocess, sys, shutil, tempfile, time
from pathlib import Path
S = Path(__file__).resolve().parent
clone = S / "clone"
CPUS = os.environ.get("CI1C_CPUS", "0,1,8,9")
def zombies():
    out = subprocess.run(["ps", "-eo", "stat"], capture_output=True, text=True).stdout.split()
    return sum(1 for x in out if x.startswith("Z"))
def run(shard, n, tag, plan=None, extra=()):
    runs = S / "runs"; runs.mkdir(exist_ok=True)
    dest = runs / tag
    if (dest / "done").exists():
        return
    shutil.rmtree(dest, ignore_errors=True)
    results = clone / "results"; shutil.rmtree(results, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="ci1c-tmp-", dir="/tmp")
    env = dict(os.environ, TMPDIR=tmp, CI1C_WORKERS=str(n))
    z0 = zombies()
    cmd = ["taskset", "-c", CPUS, "python3", "tools/run_tests.py", "exec-shard", "--plan", str(Path(plan).resolve() if plan else S / "plan.json"),
           "--shard", str(shard), "--results-dir", "results", "--run-attempt", "1", *extra]
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, cwd=clone, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.communicate()[0]
    _, status, ru = os.wait4(p.pid, 0) if False else (None, p.returncode, resource.getrusage(resource.RUSAGE_CHILDREN))
    wall = time.monotonic() - t0
    cpu = ru.ru_utime + ru.ru_stime
    dest.mkdir(parents=True)
    shutil.copytree(results, dest / "results", dirs_exist_ok=True)
    rec = json.load(open(results / f"shard-{shard}.json")) if (results / f"shard-{shard}.json").exists() else {}
    fails = [t["id"] for t in rec.get("tests", []) if t["outcome"] in ("fail", "error")]
    summary = dict(tag=tag, shard=shard, workers=n, exit=p.returncode, wall=round(wall, 1),
                   record_wall=rec.get("wall_seconds"), cpu=round(cpu, 1),
                   cpu_pct=round(100 * cpu / wall), failures=fails, leaked=rec.get("leaked_processes"),
                   zombies_before=z0, zombies_after=zombies(), tmp_left=len(os.listdir(tmp)))
    (dest / "summary.json").write_text(json.dumps(summary, indent=1))
    (dest / "stdout.txt").write_bytes(out)
    (dest / "done").write_text("1")
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(summary), flush=True)
if __name__ == "__main__":
    run(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], *( [Path(sys.argv[4])] if len(sys.argv) > 4 else []))
```

## The drivers

Stage 1 (three interleaved rounds of 1, 2 and 3 workers on shards 0 and 4; the 1-worker runs of this stage and of the next
are the baseline of the table):

```bash
#!/bin/bash
cd "$(dirname "$0")"
for r in 1 2 3; do for n in 1 2 3; do for s in 0 4; do
  python3 runone.py $s $n "s${s}-n${n}-r${r}" || echo "FAILED s${s}-n${n}-r${r}"
done; done; done
echo STAGE1-COMPLETE
```

Stage 2 (rounds 4 to 9 of 1 and 2 workers on shards 0 and 4, then three rounds on shards 1, 2, 3 and 5):

```bash
#!/bin/bash
cd "$(dirname "$0")"
for r in 4 5 6 7 8 9; do for s in 0 4; do for n in 1 2; do
  python3 runone.py $s $n "s${s}-n${n}-r${r}" || echo "FAILED s${s}-n${n}-r${r}"
done; done; done
for r in 1 2 3; do for s in 1 2 3 5; do for n in 1 2; do
  python3 runone.py $s $n "s${s}-n${n}-r${r}" || echo "FAILED s${s}-n${n}-r${r}"
done; done; done
echo STAGE2-COMPLETE
```

Re-measurement with the corrected split (3 interleaved rounds of 2 and 3 workers on shards 0 and 4), then the extension of
2 workers, the only setting that still passed:

```bash
#!/bin/bash
cd "$(dirname "$0")"
for r in 1 2 3; do for n in 2 3; do for s in 0 4; do
  python3 runone.py $s $n "s${s}-n${n}-r${r}" || echo "FAILED s${s}-n${n}-r${r}"
done; done; done
echo STAGE1B-COMPLETE
```

```bash
#!/bin/bash
cd "$(dirname "$0")"
for r in 4 5 6 7 8 9; do for s in 0 4; do
  python3 runone.py $s 2 "s${s}-n2-r${r}" || echo "FAILED s${s}-n2-r${r}"
done; done
for r in 1 2 3; do for s in 1 2 3 5; do
  python3 runone.py $s 2 "s${s}-n2-r${r}" || echo "FAILED s${s}-n2-r${r}"
done; done
echo STAGE2B-COMPLETE
```

`driver1.sh` and `driver2.sh` also start 2- and 3-worker runs; those were made with the first, count-based version of the patch and are discarded (see the item page). Only their 1-worker runs are used. The 1-worker runs are not repeated: with one worker the patch changes nothing, so they are the baseline for
both versions of the patch.
