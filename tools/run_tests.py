#!/usr/bin/env python3
"""Run the Controller's tests in duration-balanced parallel shards. Stdlib only.

One inventory and one planner (``tools/test_shards.py``) serve local runs
and CI. Every command runs exactly the selection it names, proves at run time
that each selected test ran exactly once, and never retries a failed test.

Local runs:

- ``run_tests.py [names...]``: plan the selection (all of it without names)
  for the ``local`` profile and run every shard concurrently, each in its own
  session with its own ``TMPDIR`` and ``XDG_STATE_HOME``, then print one
  summary and update the local timing cache;
- ``--serial``: the same selection as one shard, in canonical order, through
  the same executor and environment shaping (the reference run);
- ``--plan-only``: print the plan and exit;
- ``--replay <results>/plan.json [--shard i]``: re-run a recorded plan, or one
  shard of it, exactly.

Building blocks (used by the local runner and by CI):

- ``exec-shard``: run one shard, given either a recorded ``--plan`` or the
  planning inputs with ``--expect-digest``; a shard that does not pass
  prints its failing ids with traceback tails, and its log, to stderr;
- ``plan``: compute and write a plan (``--github-output`` also hands its
  shard indexes, count and digest to a GitHub Actions job's outputs);
- ``aggregate``: verdicts, coverage and summary for a results directory;
- ``timings merge --into FILE DIR...``: fold results into a timing profile,
  the explicit way to refresh the committed ``tools/test_timings.json``.

Exit status: ``0`` every shard passed with exact coverage; ``1`` a test
failure, error or unexpected success, or a fixture error; ``2`` refused,
crashed, not run, a coverage violation, or a usage error; ``130``
interrupted.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import test_shards as ts

RUN_TESTS_PY = Path(__file__).resolve()
SUBCOMMANDS = ("exec-shard", "plan", "aggregate", "timings")
#: How long an interrupted shard gets between SIGTERM and SIGKILL.
INTERRUPT_GRACE_SECONDS = 5.0
POLL_SECONDS = 0.05


class UsageError(Exception):
    """The command line is inconsistent; exit 2."""


def _error(message: str) -> int:
    print(f"run_tests: error: {message}", file=sys.stderr)
    return ts.EXIT_REFUSED


def _warn(message: str) -> None:
    print(f"run_tests: warning: {message}", file=sys.stderr)


def _add_planning_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("names", nargs="*",
                        help="unittest dotted names, 'conformance' or 'conformance:<file>'; "
                             "none selects everything")
    parser.add_argument("--target-seconds", type=float, dest="target_shard_seconds")
    parser.add_argument("--min-shards", type=int)
    parser.add_argument("--max-shards", type=int)
    parser.add_argument("--shards", type=int, help="pin the shard count")


def _add_repo_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, default=ts.REPO_ROOT, help=argparse.SUPPRESS)


def _planning_given(args) -> list[str]:
    given = [f"--{name.replace('_', '-')}" for name in ("target_shard_seconds", "min_shards",
                                                        "max_shards", "shards")
             if getattr(args, name, None) is not None]
    if getattr(args, "names", None):
        given.append("test names")
    return given


def compute_plan(repo_root: Path, profile: str, names, *, ci_placement: bool = False,
                 target_shard_seconds=None, min_shards=None, max_shards=None,
                 shards=None) -> tuple[dict, ts.Inventory, dict[str, float]]:
    """The plan ``plan`` and ``exec-shard`` compute from the planning inputs,
    with the inventory and each selected atom's estimate."""
    inventory = ts.build_inventory(repo_root)
    selection = inventory.select(names, ci_placement=ci_placement)
    parameters = ts.profile_parameters(profile, target_shard_seconds=target_shard_seconds,
                                       min_shards=min_shards, max_shards=max_shards,
                                       shards=shards)
    timings = ts.timings_for(profile, repo_root)
    plan = ts.build_plan(selection, parameters, timings, repo_root)
    estimates = ts.estimate_atoms(selection.atoms, [loaded for _, loaded in timings])
    return plan, inventory, estimates


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ts.PlanError(f"cannot read the plan {path}: {exc}")
    try:
        return ts.validate_plan(plan)
    except ts.PlanError as exc:
        raise ts.PlanError(f"{path}: {exc}")


def _write_plan(path: Path, plan: dict) -> None:
    ts.write_json_atomic(path, plan)


def describe_plan(plan: dict, estimates: dict[str, float] | None = None) -> str:
    """The ``--plan-only`` report: shards, estimates, the largest atom and
    the digest, plus a note for any atom longer than the target (decision
    D4's signal to revisit splitting classes)."""
    lines = [f"plan {plan['plan_digest']}",
             f"profile {plan['profile']}, parameters {json.dumps(plan['parameters'])}, "
             f"timing source {json.dumps(plan['timing_source'])}",
             f"{len(plan['selected_ids'])} selected tests in {plan['shard_count']} shards",
             ts.exclusive_registry_line(plan)]
    for shard in plan["shards"]:
        alone = f" ({ts.EXCLUSIVE}: runs alone, after the others)" if ts.is_exclusive(shard) \
            else ""
        lines.append(f"  shard {shard['index']}{alone}: {len(shard['test_ids'])} tests, "
                     f"{len(shard['atoms'])} atoms, estimate {shard['estimate_seconds']:.1f} s")
    if estimates:
        key = max(estimates, key=lambda atom: (estimates[atom], atom))
        lines.append(f"largest atom: {key} (estimate {estimates[key]:.1f} s)")
        target = plan["parameters"]["target_shard_seconds"]
        longer = sorted(atom for atom, seconds in estimates.items() if seconds > target)
        if longer:
            lines.append(f"note: {len(longer)} atoms are estimated above the "
                         f"{target} s target: " + ", ".join(longer))
    return "\n".join(lines)


# -- exec-shard -----------------------------------------------------------------------------


def cmd_exec_shard(args) -> int:
    planning = _planning_given(args) + (["--profile"] if args.profile else []) + (
        ["--ci-placement"] if args.ci_placement else [])
    if args.plan is not None and (planning or args.expect_digest):
        raise UsageError("exec-shard takes either --plan or the planning inputs with "
                         "--expect-digest, not both (got --plan with "
                         + ", ".join(planning + (["--expect-digest"] if args.expect_digest
                                                 else [])) + ")")
    if args.plan is None and not args.expect_digest:
        raise UsageError("exec-shard needs --plan, or the planning inputs with --expect-digest")
    repo_root = args.repo_root.resolve()
    # As `python -m unittest` from the repository root: the tests import from
    # it, and SIGINT is at its default even when the runner was started with
    # it ignored (a shell's `&`), so the Controllers the tests spawn inherit
    # a default disposition.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.path[0] = str(repo_root)
    os.chdir(repo_root)
    if args.plan is not None:
        plan = load_plan(args.plan)
    else:
        if not args.profile:
            raise UsageError("exec-shard with --expect-digest needs --profile")
        plan, _, _ = compute_plan(repo_root, args.profile, args.names,
                               ci_placement=args.ci_placement,
                               target_shard_seconds=args.target_shard_seconds,
                               min_shards=args.min_shards, max_shards=args.max_shards,
                               shards=args.shards)
        if plan["plan_digest"] != args.expect_digest:
            return _error(f"the recomputed plan_digest {plan['plan_digest']} is not the "
                          f"expected {args.expect_digest}; refusing to run")
    if not 0 <= args.shard < plan["shard_count"]:
        raise UsageError(f"--shard {args.shard} is outside the plan's "
                         f"{plan['shard_count']} shards")
    # This executor's own marker, under the one it inherited (see
    # ts.SHARD_MARKER_ENV): its descendants carry it, its ancestors never do.
    inherited = os.environ.get(ts.SHARD_MARKER_ENV) or f"{plan['plan_digest'][:16]}/{args.shard}"
    os.environ[ts.SHARD_MARKER_ENV] = f"{inherited}/{os.getpid()}"
    record, status = ts.execute_shard(plan, args.shard, args.results_dir, repo_root,
                                      argv=[sys.executable, *sys.argv])
    if status != ts.EXIT_PASS:
        sys.stderr.write(ts.shard_verdict(record, status, args.results_dir))
    return status


# -- plan / aggregate / timings -------------------------------------------------------------


def cmd_plan(args) -> int:
    plan, _, _ = compute_plan(args.repo_root.resolve(), args.profile, args.names,
                           ci_placement=args.ci_placement,
                           target_shard_seconds=args.target_shard_seconds,
                           min_shards=args.min_shards, max_shards=args.max_shards,
                           shards=args.shards)
    github_output = os.environ.get("GITHUB_OUTPUT") if args.github_output else None
    if args.github_output and not github_output:
        raise UsageError("--github-output needs $GITHUB_OUTPUT (it is set inside a GitHub "
                         "Actions step)")
    if args.output is not None:
        _write_plan(args.output, plan)
    shards = list(range(plan["shard_count"]))
    print(json.dumps({"plan_digest": plan["plan_digest"], "shard_count": plan["shard_count"],
                      "shards": shards}, sort_keys=True))
    if github_output:
        # The tests job's matrix is fromJSON(shards); its shard jobs recompute
        # the plan and refuse to run unless their digest equals this one.
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"shards={json.dumps(shards, separators=(',', ':'))}\n"
                         f"count={plan['shard_count']}\n"
                         f"digest={plan['plan_digest']}\n")
    return 0


def _publish_summary(summary: str, results_dir: Path) -> None:
    (Path(results_dir) / "SUMMARY.md").write_text(summary, encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary, end="")


def cmd_aggregate(args) -> int:
    if not Path(args.plan).exists():
        # In CI: the plan job failed or was cancelled, so no shard ran either.
        return _error(f"there is no plan at {args.plan}: the plan was never produced, so "
                      f"nothing ran and nothing can pass")
    plan = load_plan(args.plan)
    records, notes = ts.load_results(plan, args.results_dir)
    result = ts.aggregate(plan, records, args.results_dir, notes=notes)
    _publish_summary(result.summary, args.results_dir)
    return result.exit_status


def cmd_timings_merge(args) -> int:
    records = []
    for results_dir in args.results_dirs:
        plan = load_plan(Path(results_dir) / "plan.json")
        found, _ = ts.load_results(plan, results_dir)
        for index, record in sorted(found.items()):
            if record is None:
                continue
            if record["plan_digest"] != plan["plan_digest"]:
                raise ts.AggregateError(f"{ts.result_path(results_dir, index)} carries another "
                                        f"plan's digest")
            records.append(record)
    into = Path(args.into)
    if into.exists():
        try:
            existing = ts.parse_timings(into.read_bytes())
        except (OSError, ts.TimingError) as exc:
            raise ts.TimingError(f"{into} is not a valid timing file; refusing to merge into "
                                 f"it: {exc}")
    else:
        existing = ts.TimingProfile(profile=args.profile_name or ts.CI, atoms={})
    inventory = ts.build_inventory(args.repo_root.resolve())
    updated = ts.update_timings(existing, records, inventory,
                                profile_name=args.profile_name or existing.profile)
    ts.write_timings(into, updated)
    print(f"merged {len(records)} shard records into {into}: {len(updated.atoms)} atoms")
    return 0


# -- the local runner -----------------------------------------------------------------------


def _run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


class _Interrupt:
    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum, frame) -> None:
        self.requested = True


class _Shard:
    def __init__(self, index: int, process: subprocess.Popen, started: float) -> None:
        self.index = index
        self.process = process
        self.started = started
        self.log_offset = 0
        self.partial = b""


def _stream_log(shard: _Shard, results_dir: Path, *, final: bool = False) -> None:
    try:
        with open(ts.log_path(results_dir, shard.index), "rb") as handle:
            handle.seek(shard.log_offset)
            data = handle.read()
    except OSError:
        return
    shard.log_offset += len(data)
    data = shard.partial + data
    lines = data.split(b"\n")
    shard.partial = b"" if final else lines.pop()
    for line in lines:
        if line or not final:
            print(f"[{shard.index}] {line.decode('utf-8', 'replace')}", flush=True)


def _record_leaks(results_dir: Path, index: int, leaks: list[dict]) -> None:
    """Add leaks the parent found after a shard exited to its record."""
    path = ts.result_path(results_dir, index)
    try:
        record = ts.load_shard_result(path)
    except ts.ResultRecordError:
        return
    known = {leak["pid"] for leak in record["leaked_processes"]}
    record["leaked_processes"] += [leak for leak in leaks if leak["pid"] not in known]
    ts.write_json_atomic(path, record)


def run_shards(plan: dict, plan_path: Path, results_dir: Path, repo_root: Path, *,
               indexes: list[int], jobs: int, run_id: str, isolated_pip_cache: bool,
               verbose: bool) -> tuple[set[int], dict[int, list[dict]], float]:
    """Run ``indexes`` of ``plan`` as ``exec-shard`` children, at most
    ``jobs`` at once, each in its own session; the exclusive shard starts
    only once no other shard is running, and nothing starts beside it.
    Returns the interrupted shards, the leaks the parent found after each
    shard exited (already killed), and the wall time. Ctrl-C (or SIGTERM)
    sends SIGTERM, then SIGKILL after ``INTERRUPT_GRACE_SECONDS``, to every
    running shard's group, and marks the running and queued shards
    interrupted."""
    interrupt = _Interrupt()
    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    queue = sorted(indexes, key=lambda i: (ts.is_exclusive(plan["shards"][i]), i))
    running: dict[int, _Shard] = {}
    interrupted: set[int] = set()
    leaks: dict[int, list[dict]] = {}
    start = time.monotonic()

    def finish(shard: _Shard) -> None:
        if verbose:
            _stream_log(shard, results_dir, final=True)
        found = ts.scan_marked_processes(f"{run_id}/{shard.index}")
        ts.kill_processes(found)
        if found:
            leaks[shard.index] = found
            _record_leaks(results_dir, shard.index, found)
        if not interrupt.requested:
            print(f"shard {shard.index} exited with status {shard.process.returncode} after "
                  f"{time.monotonic() - shard.started:.1f} s", flush=True)

    try:
        while (queue or running) and not interrupt.requested:
            while queue and len(running) < jobs and not interrupt.requested:
                if running and (ts.is_exclusive(plan["shards"][queue[0]]) or any(
                        ts.is_exclusive(plan["shards"][i]) for i in running)):
                    break
                index = queue.pop(0)
                env = ts.shard_environment(os.environ, results_dir, run_id, index,
                                           isolated_pip_cache=isolated_pip_cache)
                for name in ("TMPDIR", "XDG_STATE_HOME"):
                    Path(env[name]).mkdir(parents=True, exist_ok=True)
                with open(ts.log_path(results_dir, index), "ab") as log:
                    process = subprocess.Popen(
                        [sys.executable, str(RUN_TESTS_PY), "exec-shard", "--plan",
                         str(plan_path), "--shard", str(index), "--results-dir",
                         str(results_dir), "--repo-root", str(repo_root)],
                        cwd=repo_root, env=env, stdin=subprocess.DEVNULL, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True)
                running[index] = _Shard(index, process, time.monotonic())
            for index, shard in list(running.items()):
                if verbose:
                    _stream_log(shard, results_dir)
                if shard.process.poll() is not None:
                    del running[index]
                    finish(shard)
            time.sleep(POLL_SECONDS)
        if interrupt.requested:
            interrupted = set(running) | set(queue)
            for shard in running.values():
                _signal_group(shard.process, signal.SIGTERM)
            deadline = time.monotonic() + INTERRUPT_GRACE_SECONDS
            while any(s.process.poll() is None for s in running.values()) \
                    and time.monotonic() < deadline:
                time.sleep(POLL_SECONDS)
            for shard in running.values():
                _signal_group(shard.process, signal.SIGKILL)
                shard.process.wait()
                finish(shard)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return interrupted, leaks, time.monotonic() - start


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _update_local_timings(plan: dict, records: dict, inventory: ts.Inventory | None) -> None:
    """Fold this run's completed, passing atoms into the local profile.
    Advisory: a failure here is a warning, never the run's verdict."""
    if inventory is None:
        return
    usable = [record for record in records.values()
              if record is not None and record["plan_digest"] == plan["plan_digest"]]
    path = ts.local_timings_path()
    try:
        profile = ts.load_timings(path, warn=lambda message: None)
        ts.write_timings(path, ts.update_timings(profile, usable, inventory,
                                                 profile_name=ts.LOCAL))
    except OSError as exc:
        _warn(f"could not update the local timing cache {path}: {exc}")


def _results_dir(requested: Path | None, run_id: str) -> Path:
    if requested is None:
        results_dir = Path(tempfile.gettempdir()) / "workflow-controller-tests" / run_id
    else:
        results_dir = requested.resolve()
        if results_dir.exists() and (any(results_dir.glob("shard-*.json"))
                                     or (results_dir / "plan.json").exists()):
            raise UsageError(f"--results-dir {results_dir} already holds a run's results")
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir


def cmd_run(args) -> int:
    repo_root = args.repo_root.resolve()
    cpus = os.cpu_count() or 1
    inventory = estimates = None
    if args.shard is not None and args.replay is None:
        raise UsageError("--shard needs --replay")
    if args.replay is not None:
        if _planning_given(args) or args.serial:
            raise UsageError("--replay runs a recorded plan exactly; it takes no test names, "
                             "--serial or planning options")
        plan = load_plan(args.replay)
        if args.shard is not None and not 0 <= args.shard < plan["shard_count"]:
            raise UsageError(f"--shard {args.shard} is outside the plan's "
                             f"{plan['shard_count']} shards")
    else:
        if args.serial and _planning_given(args) != (["test names"] if args.names else []):
            raise UsageError("--serial runs one shard; it takes no shard-count options")
        plan, inventory, estimates = compute_plan(
            repo_root, ts.LOCAL, args.names, target_shard_seconds=args.target_shard_seconds,
            min_shards=args.min_shards, max_shards=args.max_shards,
            shards=1 if args.serial else args.shards)
    if args.plan_only:
        print(describe_plan(plan, estimates))
        return 0
    indexes = [args.shard] if args.shard is not None else list(range(plan["shard_count"]))
    jobs = args.jobs if args.jobs is not None else len(indexes)
    if jobs < 1:
        raise UsageError("--jobs must be at least 1")
    if min(jobs, len(indexes)) > cpus:
        _warn(f"running {min(jobs, len(indexes))} shards at once on {cpus} CPUs; the "
              f"measured-safe default is one shard per CPU")
    run_id = _run_id()
    results_dir = _results_dir(args.results_dir, run_id)
    plan_path = results_dir / "plan.json"
    _write_plan(plan_path, plan)
    print(f"{len(plan['selected_ids'])} selected tests, {plan['shard_count']} shards "
          f"(plan {plan['plan_digest'][:12]}); results in {results_dir}", flush=True)
    interrupted, leaks, wall = run_shards(
        plan, plan_path, results_dir, repo_root, indexes=indexes, jobs=jobs, run_id=run_id,
        isolated_pip_cache=args.isolated_pip_cache, verbose=args.verbose)
    records, notes = ts.load_results(plan, results_dir, indexes)
    for index, found in leaks.items():
        if records.get(index) is None:
            notes[index] = (notes.get(index, "") + f"; leaked {len(found)} processes").lstrip("; ")
    result = ts.aggregate(plan, records, results_dir, shards=indexes, interrupted=interrupted,
                          notes=notes, wall_seconds=wall)
    _publish_summary(result.summary, results_dir)
    if inventory is None:
        try:
            inventory = ts.build_inventory(repo_root)
        except ts.InventoryError as exc:
            _warn(f"not updating the local timing cache: {exc}")
    _update_local_timings(plan, records, inventory)
    return result.exit_status


# -- argument parsing -----------------------------------------------------------------------


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_tests.py", description="Run the Controller's tests in balanced shards.",
        epilog="Subcommands: exec-shard, plan, aggregate, timings merge (each takes --help).")
    _add_planning_options(parser)
    parser.add_argument("--serial", action="store_true",
                        help="run the selection as one shard, in canonical order")
    parser.add_argument("--plan-only", action="store_true", help="print the plan and exit")
    parser.add_argument("--replay", type=Path, metavar="PLAN",
                        help="re-run a recorded plan.json exactly")
    parser.add_argument("--shard", type=int, help="with --replay, run only this shard")
    parser.add_argument("--jobs", type=int, help="run at most this many shards at once")
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--isolated-pip-cache", action="store_true",
                        help="give each shard its own cold PIP_CACHE_DIR")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="stream each shard's per-test lines, prefixed with [i]")
    _add_repo_root(parser)
    return parser


def _exec_shard_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_tests.py exec-shard",
                                     description="Run one shard of a plan.")
    _add_planning_options(parser)
    parser.add_argument("--plan", type=Path, help="a recorded plan.json")
    parser.add_argument("--profile", choices=(ts.LOCAL, ts.CI))
    parser.add_argument("--ci-placement", action="store_true")
    parser.add_argument("--expect-digest")
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    _add_repo_root(parser)
    return parser


def _plan_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_tests.py plan",
                                     description="Compute a plan and print its digest.")
    _add_planning_options(parser)
    parser.add_argument("--profile", choices=(ts.LOCAL, ts.CI), default=ts.LOCAL)
    parser.add_argument("--ci-placement", action="store_true")
    parser.add_argument("--output", type=Path, help="write plan.json here")
    parser.add_argument("--github-output", action="store_true",
                        help="also append shards=, count= and digest= to $GITHUB_OUTPUT")
    _add_repo_root(parser)
    return parser


def _aggregate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_tests.py aggregate",
                                     description="Aggregate a results directory.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    _add_repo_root(parser)
    return parser


def _timings_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_tests.py timings",
                                     description="Maintain timing profiles.")
    sub = parser.add_subparsers(dest="action", required=True)
    merge = sub.add_parser("merge", help="fold results directories into a timing file")
    merge.add_argument("--into", type=Path, required=True)
    merge.add_argument("--profile-name", help="the profile name to record (default: keep)")
    merge.add_argument("results_dirs", nargs="+", type=Path, metavar="RESULTS_DIR")
    _add_repo_root(merge)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv and argv[0] in SUBCOMMANDS else None
    try:
        if command == "exec-shard":
            return cmd_exec_shard(_exec_shard_parser().parse_intermixed_args(argv[1:]))
        if command == "plan":
            return cmd_plan(_plan_parser().parse_intermixed_args(argv[1:]))
        if command == "aggregate":
            return cmd_aggregate(_aggregate_parser().parse_args(argv[1:]))
        if command == "timings":
            return cmd_timings_merge(_timings_parser().parse_args(argv[1:]))
        return cmd_run(_run_parser().parse_intermixed_args(argv))
    except (UsageError, ts.InventoryError, ts.SelectionError, ts.PlanError,
            ts.ResultRecordError, ts.AggregateError, ts.TimingError) as exc:
        return _error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
