"""The Controller's command-line surface (capability 10).

CP1 provided the skeleton: argument parsing for the full six-command
surface, the pinned-execution guard and the materialise-and-re-exec
mechanism that makes every non-read-only command run from an immutable
snapshot, and a fully working ``status`` command, with ``inspect``,
``explain``, ``step``, ``run`` and ``resume`` present in the command table
but stubbed. CP2-CP8 wired ``run`` (and, through it, the decision engine,
job execution, durable resume's own reconciliation module, and the
generation-handoff primitive). CP9 completes the surface: ``inspect``,
``explain``, ``step`` and ``resume`` are wired to their real behaviour
below, and the exit-code table (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``,
"Exit codes") is now implemented in full, including ``40`` (``resume``
reconciling a record to ``INTERRUPTED``).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from controller import (
    evidence, handoff, identity, job, lock, managed_repo, milestone_branch, observe, protocol_decision, routing,
    runtime, settings, target_state, telemetry, usage, worker,
)
from controller.decision import Decision, decide_no_work_item, phase_to_wire
from controller.errors import ControllerError, LifecycleWorkerActiveError, SourceSnapshotError

#: The three read-only commands. Positive guard: everything not in this set
#: requires a pinned identity (`source_kind != "unpinned"`), by construction
#: rather than by a denylist a future command could be added without
#: updating. ``settings`` is dispatched before pinning, like ``follow``, and
#: touches only the user settings file, never a target or the runtime root.
READ_ONLY_COMMANDS = frozenset({"inspect", "explain", "status", "follow", "telemetry", "usage"})
ALL_COMMANDS = frozenset({
    "inspect", "explain", "status", "step", "run", "resume", "follow", "milestone-binding", "settings",
    "telemetry", "usage",
})

#: The full exit-code contract (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``,
#: "Exit codes"). ``2`` is argparse's own default usage-error code and is
#: never returned explicitly by this module.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE = 10
EXIT_DECLINED = 15
EXIT_MAX_STEPS = 16
#: `workflow-controller-usage-budget` CP3 (D10): stopped before starting a
#: job because of the usage budget -- a timed pause (run again after the
#: resume time) or an exhausted run or repository cap. Nothing was started.
EXIT_USAGE_PAUSED = 17
#: `usage --renew`/`--release` named a token the shared record does not know
#: (or, for `--renew`, one already accounted): nothing was changed.
EXIT_TOKEN_UNKNOWN = 1
EXIT_FAIL_CLOSED = 20
EXIT_WORKER_FAILED = 30
EXIT_INCOMPLETE = 35
EXIT_INTERRUPTED = 40
#: `workflow-controller-automatic-lifecycle-orchestration` CP5: another
#: Controller or a previous worker holds the target worktree (its lifecycle
#: lock is held), or `resume` left a record alone because its recorded
#: worker may still run. Nothing was launched or reconciled.
EXIT_WORKER_ACTIVE = 45
EXIT_HANDOFF_PENDING = 50
#: Ctrl-C in a waiting `run`: what an uncaught SIGINT's ``KeyboardInterrupt``
#: exits with, now without the traceback.
SIGINT_EXIT_STATUS = 130

#: Test-support surface (CP8): `--pause-file` is inert unless this
#: environment variable is also set to exactly `"1"`, so an ordinary
#: invocation -- including one that passes the flag by accident or by
#: copy-paste -- cannot pause.
TEST_HOOKS_ENV = "WORKFLOW_CONTROLLER_TEST_HOOKS"

_PAUSE_POLL_SECONDS = 0.05

#: The longest single sleep of a usage pause, so Ctrl-C and the clock are
#: honoured promptly (usage-budget CP3, D7).
USAGE_WAIT_SLICE_SECONDS = 1.0


def _usage_sleep(seconds: float) -> None:
    """One slice of a usage pause; tests replace it (with
    ``job.usage_now``) to wait on an injected clock."""
    time.sleep(seconds)

#: The run record the current ``step``/``run`` created
#: (``workflow-controller-release-runtime-observability`` CP5).
#: ``cmd_step``/``cmd_run`` register it and never close it: the final exit
#: code is only known in :func:`main`, whose one ``finally`` closes it.
_open_run: job.RunRecord | None = None

#: ``step --follow``/``run --follow``'s renderer thread and its stop event
#: (release-runtime-observability CP6), started by :func:`_start_follower`
#: and stopped by :func:`main`.
_follower: tuple[threading.Thread, threading.Event] | None = None

#: The bounded join :func:`main` gives the renderer thread on completion.
FOLLOWER_JOIN_SECONDS = 2.0


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def require_pinned_execution() -> None:
    """The asserting mechanism: every non-read-only command function calls
    this on entry. In normal operation it never fires -- ``cli.main``'s own
    materialise-and-re-exec step (the *acting* mechanism) has already
    pinned the process by the time a command function is dispatched. It
    exists so a future code path that reaches dispatch without that step
    stops instead of running unpinned, and it is reachable in tests by
    calling a command function directly against an unpinned identity."""
    ident = identity.current()
    if ident.source_kind == identity.SOURCE_KIND_UNPINNED:
        raise SourceSnapshotError(
            "this command must run from a pinned, immutable Controller source snapshot",
            evidence={"raised_by": "cli.main", "source_kind": ident.source_kind},
        )


def _strip_option(argv: list[str], option: str) -> list[str]:
    """Drop any caller-supplied ``option``/``option=X`` from the argv the
    re-exec is about to build -- the resolved absolute value this process
    just computed supersedes it, and leaving both would make the child's
    value depend on argparse's last-wins ordering."""
    out: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == option:
            skip_next = True
            continue
        if token.startswith(f"{option}="):
            continue
        out.append(token)
    return out


def _strip_runtime_dir(argv: list[str]) -> list[str]:
    return _strip_option(argv, "--runtime-dir")


def _reexec(*, snapshot_dir: Path, runtime_root: Path, argv: list[str],
            exec_depth_seen: int, source_kind: str, source_commit: str | None,
            settings_path: str | None = None) -> None:
    """Replace this process with a fresh interpreter importing exactly the
    snapshot: ``-P`` suppresses the working-directory `sys.path` entry
    `python -m` would otherwise prepend (the exact defect that made
    revision 32's exec loop), and ``-B`` stops the import from writing
    `__pycache__` into the snapshot, which would otherwise change its own
    digest on its very first execution. ``--settings``, when given, is
    passed on made absolute (settings-and-telemetry CP1, A.1). Never
    returns."""
    settings_argv = [] if settings_path is None else ["--settings", str(settings.resolve_path(settings_path))]
    child_argv = [
        sys.executable, "-P", "-B", "-m", "controller",
        "--runtime-dir", str(runtime_root), *settings_argv,
        *_strip_option(_strip_runtime_dir(argv), "--settings"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(snapshot_dir)
    env[identity.EXEC_HANDOFF_ENV] = json.dumps({
        "exec_depth": exec_depth_seen + 1,
        "source_kind": source_kind,
        "source_commit": source_commit,
    })
    os.execve(sys.executable, child_argv, env)  # noqa: S606 -- the whole mechanism


def _write_identity_record(runtime_root: Path, ident: identity.ControllerIdentity,
                            *, exec_depth: int) -> None:
    record = {
        "schema_version": 1,
        "generation": ident.generation,
        "source_root": str(ident.source_root),
        "origin_source_root": str(ident.origin_source_root) if ident.origin_source_root else None,
        "source_kind": ident.source_kind,
        "source_commit": ident.source_commit,
        "tree_digest": ident.tree_digest,
        "generation_source": ident.generation_source,
        "pinned_at": ident.pinned_at,
        "controller_runtime": identity.runtime_record(ident),
        "interpreter_executable": sys.executable,
        "interpreter_safe_path": bool(sys.flags.safe_path),
        "interpreter_sys_path": list(sys.path),
        "exec_depth": exec_depth,
    }
    runtime.write_json(runtime_root, "identity.json", record)


def _route_value(text: str) -> str:
    """``--model``/``--effort``'s ``type=``: a non-empty value that does not
    begin with ``-`` (``routing.check_value``); anything else is a usage
    error (exit 2)."""
    try:
        return routing.check_value(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _positive_int(text: str) -> int:
    """``--timeout``/``--max-steps``/``--drain-timeout``'s ``type=``: a
    positive integer (settings-and-telemetry CP2, I1); ``0``, a negative
    number or a non-integer is a usage error (exit 2). The table's bounds
    apply to the settings file's values, not to these flags."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid positive integer: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, not {value}")
    return value


def _usage_percent(text: str) -> int:
    """``--usage-cap``'s ``type=``: an integer from 1 to 100, the bounds of
    the ``usage.*_cap_percent`` rows; anything else is a usage error
    (exit 2)."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid percentage: {text!r}") from None
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError(f"must be from 1 to 100, not {value}")
    return value


def _role_assignment(text: str) -> tuple[str, str]:
    """``--role-model``/``--role-effort``'s ``type=``: ``ROLE=VALUE`` with
    ``ROLE`` one of ``routing.ROLES``. A missing ``=``, an unknown role or
    an unusable value is a usage error (exit 2), before anything runs."""
    try:
        return routing.parse_role_assignment(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


class _RoleAssignmentAction(argparse.Action):
    """Collects repeated ``ROLE=VALUE`` assignments into a ``{role: value}``
    dict. Assigning one role twice is ambiguous, so it is a usage error
    (exit 2), never a silent last-wins."""

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        role, value = values
        assignments = dict(getattr(namespace, self.dest, None) or {})
        if role in assignments:
            raise argparse.ArgumentError(self, f"role {role!r} is assigned more than once")
        assignments[role] = value
        setattr(namespace, self.dest, assignments)


def version_text(ident: identity.ControllerIdentity | None = None) -> str:
    """``--version``'s line 1: exactly ``workflow-controller <version>``,
    the contract tests and the release pipeline assert. The version is the
    resolved runtime's -- ``ident``'s, else this process's pin's -- and
    ``unknown`` when this process's identity cannot be resolved (``--version``
    reports, it never fails on this)."""
    if ident is None:
        try:
            ident = identity.pin()
        except ControllerError:
            return f"workflow-controller {identity.UNKNOWN_VERSION}"
    return f"workflow-controller {ident.version}"


def _describe_running_runtime() -> str:
    """``describe_runtime`` for this process, or ``unidentified`` naming
    why its identity could not be resolved (a tampered snapshot, say) --
    ``--version`` and ``status`` report, they never fail on this."""
    try:
        return identity.describe_runtime(identity.pin())
    except ControllerError as exc:
        return f"unidentified ({exc.message})"


class _VersionAction(argparse.Action):
    """``--version``: computes its text only when the flag is used, so
    ``build_parser()`` stays side-effect free. Resolves no runtime root,
    writes nothing and needs no subcommand."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        print(version_text())
        print(f"runtime: {_describe_running_runtime()}")
        parser.exit(EXIT_OK)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow-controller")
    parser.add_argument("--version", action=_VersionAction,
                        help="print the Controller version and exit")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--work-item", default=None)
    parser.add_argument("--workflow-manager", default=None)
    parser.add_argument("--claude-binary", default=None)
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--timeout", type=_positive_int, default=None,
                        help="seconds a launched worker may run (default: the worker.timeout_seconds setting)")
    # Role-based worker routing (automatic-lifecycle-orchestration CP6). The
    # per-role options beat the global ones, which beat the config file's
    # role entry, then its default, then the built-in route.
    parser.add_argument("--model", type=_route_value, default=None,
                        help="model for every launched worker, unless a --role-model overrides it")
    parser.add_argument("--effort", type=_route_value, default=None,
                        help="effort for every launched worker, unless a --role-effort overrides it")
    parser.add_argument("--role-model", metavar="ROLE=MODEL", type=_role_assignment,
                        action=_RoleAssignmentAction, default=None,
                        help="model for one role's workers (repeatable, once per role)")
    parser.add_argument("--role-effort", metavar="ROLE=EFFORT", type=_role_assignment,
                        action=_RoleAssignmentAction, default=None,
                        help="effort for one role's workers (repeatable, once per role)")
    parser.add_argument("--routing-config", metavar="PATH", default=None,
                        help="JSON routing config: {\"schema_version\": 1, \"default\": {...}, \"roles\": {...}}")
    parser.add_argument("--settings", metavar="PATH", default=None,
                        help="the user settings file (default: $WORKFLOW_CONTROLLER_SETTINGS, else "
                             "$XDG_CONFIG_HOME/workflow-controller/settings.json, else "
                             "~/.config/workflow-controller/settings.json)")
    parser.add_argument("--allow-dirty-source", action="store_true", default=False)
    parser.add_argument("--json", action="store_true", default=False)

    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_p = subparsers.add_parser("inspect")
    inspect_p.add_argument("repo")

    explain_p = subparsers.add_parser("explain")
    explain_p.add_argument("repo")

    step_p = subparsers.add_parser("step")
    step_p.add_argument("repo")
    step_p.add_argument("--follow", action="store_true", default=False,
                        help="render the run's events and worker output on stderr while it runs")
    step_p.add_argument("--usage-cap", type=_usage_percent, default=None, metavar="PERCENT",
                        help="cap this run's Claude usage at PERCENT of the account window "
                             "(default: the usage.run_cap_percent setting, none)")

    run_p = subparsers.add_parser("run")
    run_p.add_argument("repo")
    run_p.add_argument("--follow", action="store_true", default=False,
                       help="render the run's events and worker output on stderr while it runs")
    run_p.add_argument("--max-steps", type=_positive_int, default=None,
                       help="stop after this many steps (default: the run.max_steps setting, 20)")
    run_p.add_argument("--usage-cap", type=_usage_percent, default=None, metavar="PERCENT",
                       help="cap this run's Claude usage at PERCENT of the account window "
                            "(default: the usage.run_cap_percent setting, none)")
    run_p.add_argument("--pause-file", default=None)

    resume_p = subparsers.add_parser("resume")
    resume_p.add_argument(
        "--abandon", metavar="JOB_ID", default=None,
        help="mark the pending job file JOB_ID terminal instead of reconciling (the operator "
             "disposition for a job file `resume` cannot reconcile)",
    )
    resume_p.add_argument(
        "--acknowledge-unverifiable-worker", action="store_true", default=False,
        help="with --abandon only: state that a worker whose liveness cannot be verified here is gone",
    )
    resume_p.add_argument(
        "--drain-timeout", metavar="SECONDS", type=_positive_int, default=None,
        help="bound this re-attach drain of a worker's owned processes (default: the "
             "worker.drain_detach_seconds setting)",
    )
    resume_p.add_argument("repo")

    subparsers.add_parser("status")

    # trunk-branch-pr-release-orchestration CP8 (TBR-R4-001): the operator
    # acknowledgement of a refusal-state binding record. `--work-item` (a
    # global option) is required; `main` refuses its absence as a usage
    # error.
    binding_p = subparsers.add_parser(
        "milestone-binding", help="acknowledge a milestone binding that is in a refusal state")
    disposition = binding_p.add_mutually_exclusive_group(required=True)
    disposition.add_argument("--new-pr", action="store_true", default=False,
                             help="rebind to the same branch; the next branch commit opens a new pull request")
    disposition.add_argument("--abandon", action="store_true", default=False,
                             help="retire the binding; the milestone is planned again from the trunk")
    binding_p.add_argument("repo")

    follow_p = subparsers.add_parser(
        "follow", help="follow a run or job's events and worker output, read-only")
    follow_target = follow_p.add_mutually_exclusive_group()
    follow_target.add_argument("--job", metavar="JOB_ID", default=None)
    follow_target.add_argument("--run", metavar="RUN_ID", default=None)
    follow_p.add_argument("--from-start", action="store_true", default=False,
                          help="replay every event, not only the last follow.replay_events (default 20)")
    follow_p.add_argument("repo", nargs="?", default=".")

    # settings-and-telemetry CP1 (A.6): the user settings file.
    settings_p = subparsers.add_parser("settings", help="show, locate or clean the user settings file")
    settings_actions = settings_p.add_subparsers(dest="settings_action", required=True)
    settings_actions.add_parser("show", help="print each effective setting and its source (cli, file, default)")
    settings_actions.add_parser("path", help="print the settings file's path")
    settings_actions.add_parser(
        "clean", help="fill the settings file, then remove the keys this release does not know")

    # settings-and-telemetry CP3 (Design C): the read-only telemetry
    # summary. `--work-item` and `--json` are the global options.
    telemetry_p = subparsers.add_parser(
        "telemetry", help="summarise the recorded jobs' cost, tokens and time, read-only")
    telemetry_p.add_argument("--run", metavar="RUN_ID", default=None, help="only this run's jobs")
    telemetry_p.add_argument("--since", metavar="ISO", type=_since, default=None,
                             help="only jobs created at or after this UTC date or time "
                                  "(YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)")
    telemetry_p.add_argument("--by", choices=sorted(telemetry.GROUPINGS), default=None, metavar="GROUPING",
                             help="group the jobs: role, model, or role,model for both (default: one group)")
    telemetry_p.add_argument("repo", nargs="?", default=None,
                             help="only this target repository's jobs (default: every target)")

    # workflow-controller-usage-budget CP4 (D8): the usage budget's view and
    # the manual workers' gate. `--model` and `--json` are also global
    # options, so this subcommand's own dests are `usage_model` and (when
    # given after the subcommand) `json`, never overriding a global value.
    usage_p = subparsers.add_parser(
        "usage", help="show the usage budget, or gate a manual worker on it (check, wait, reserve, renew, release)")
    usage_p.add_argument("--provider", choices=["claude", "codex", "all"], default=None,
                         help="the provider to show or gate (default: all to show, claude to gate)")
    usage_p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                         help="print one JSON object (also accepted before the subcommand)")
    usage_mode = usage_p.add_mutually_exclusive_group()
    usage_mode.add_argument("--check", action="store_true", default=False,
                            help="evaluate the budget once: exit 17 on a hold, else 0 (reserves nothing "
                                 "unless --reserve)")
    usage_mode.add_argument("--wait", action="store_true", default=False,
                            help="wait while the budget holds, re-evaluating after each wait; exit 0 only on "
                                 "go, 17 on a cap or when usage.max_wait_seconds is exhausted")
    usage_p.add_argument("--reserve", action="store_true", default=False,
                         help="with --check/--wait: reserve the forecast on go and print the token")
    usage_p.add_argument("--role", default=None, metavar="ROLE", help="the worker's role (selects the forecast)")
    usage_p.add_argument("--model", dest="usage_model", type=_route_value, default=None, metavar="MODEL",
                         help="the worker's model (selects the forecast)")
    usage_p.add_argument("--repo", default=None, metavar="PATH",
                         help="the target repository (selects the repository cap and its spend)")
    usage_p.add_argument("--run-id", default=None, metavar="ID",
                         help="the run (selects the run cap and its spend)")
    usage_p.add_argument("--renew", default=None, metavar="TOKEN",
                         help="extend a reservation's lease, reviving a lapsed one")
    usage_p.add_argument("--release", default=None, metavar="TOKEN",
                         help="account a reservation and release it")
    usage_p.add_argument("--outcome", choices=["ok", "not_started"], default="ok",
                         help="with --release: not_started releases without charging (default: ok)")
    usage_p.add_argument("--stream", default=None, metavar="PATH",
                         help="with --release: measure the worker's stream-json output")
    usage_p.add_argument("--usage-cap", type=_usage_percent, default=None, metavar="PERCENT",
                         help="cap the run's Claude usage at PERCENT (default: the usage.run_cap_percent setting)")
    usage_p.add_argument("--usage-codex-cap", type=_usage_percent, default=None, metavar="PERCENT",
                         help="cap the run's Codex usage at PERCENT (default: the usage.codex_run_cap_percent "
                              "setting)")

    return parser


def _validate_usage_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """The ``usage`` option combinations argparse cannot express (a usage
    error, exit 2, before anything runs)."""
    gate = args.check or args.wait or args.reserve
    if args.renew is not None and args.release is not None:
        parser.error("usage: --renew and --release are mutually exclusive")
    if (args.renew is not None or args.release is not None) and gate:
        parser.error("usage: --renew/--release cannot be combined with --check, --wait or --reserve")
    if args.reserve and not (args.check or args.wait):
        parser.error("usage: --reserve needs --check or --wait")
    if (args.stream is not None or args.outcome != "ok") and args.release is None:
        parser.error("usage: --stream and --outcome are accepted only with --release")
    if gate and args.provider == "all":
        parser.error("usage: --check/--wait gate one provider; pass --provider claude or codex")


def _since(value: str) -> datetime.datetime:
    """``telemetry --since``: a bad value is argparse's usage error (exit 2)."""
    try:
        return telemetry.parse_since(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def cmd_status(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
               *, pre_existing: dict) -> int:
    """Controller-owned view: pinned identity, job records, pending
    handoff -- reported from what was durably present when this process
    *started*, since this same process writes a fresh ``identity.json``
    immediately before this body runs and that record must not be mistaken
    for evidence of prior work.

    The first line always describes the *running* process, the same text
    as ``--version``'s line 2. ``--json`` (settings-and-telemetry CP6,
    E.4) prints the same fields as one object."""
    view = _status_view(runtime_root, ident, pre_existing)
    if getattr(args, "json", False):
        print(json.dumps(view, sort_keys=True))
        return EXIT_OK
    print(f"controller: {view['controller']}")
    if not view["runtime_state"]:
        print(f"no Controller runtime state at {runtime_root} (ladder row {view['ladder_row']})")
        return EXIT_OK

    prev = view["pinned_identity"]
    if prev is not None:
        print(f"pinned identity: source_kind={prev.get('source_kind')} "
              f"source_commit={prev.get('source_commit')} generation={prev.get('generation')} "
              f"tree_digest={prev.get('tree_digest')}")
    else:
        print("pinned identity: none recorded yet")

    # Settings-and-telemetry CP6 (E.4): the count, then the newest jobs,
    # one per line.
    jobs = view["jobs"]
    print(f"jobs: {jobs['count']} recorded" if jobs["count"] else "jobs: none")
    for entry in jobs["recent"]:
        print(f"  {observe.job_summary_text(entry)}")

    print(f"handoff: pending ({view['handoff']})" if view["handoff"] is not None else "handoff: none")

    _print_active(view["active"])

    # Settings-and-telemetry CP3: the newest job with a telemetry block --
    # its cost and wall time -- only when one exists.
    last = view["last_job_telemetry"]
    if last is not None:
        print(telemetry.last_job_text(last))

    # CP8 (trunk-branch-pr-release-orchestration): one line per binding
    # record, only when one exists.
    for line in milestone_branch.binding_lines(runtime_root):
        print(line)

    print(f"runtime root: {runtime_root} (ladder row {view['ladder_row']})")
    return EXIT_OK


def _status_view(runtime_root: Path, ident: identity.ControllerIdentity, pre_existing: dict) -> dict:
    """Everything ``status`` reports, as one JSON-ready object. The jobs
    are those recorded when this process started (``pre_existing``)."""
    view = {
        "controller": f"{version_text(ident)} -- {identity.describe_runtime(ident)}",
        "runtime_root": str(runtime_root),
        "ladder_row": pre_existing["ladder_row"],
        "runtime_state": bool(pre_existing["had_any_state"]),
    }
    if not view["runtime_state"]:
        return view
    recorded = set(pre_existing["job_ids"])
    records = [record for record in observe.list_jobs(runtime_root) if record["job_id"] in recorded]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last = telemetry.last_finished(records)
    view.update({
        "pinned_identity": pre_existing["identity"],
        "jobs": {"count": len(recorded), "recent": observe.recent_jobs(records, now=now)},
        "handoff": pre_existing["handoff"],
        "active": _active_view(runtime_root),
        "last_job_telemetry": None if last is None else telemetry.last_job_entry(last),
        "bindings": milestone_branch.binding_entries(runtime_root),
    })
    return view


def _active_view(runtime_root: Path) -> dict:
    """``status``'s ``active`` section (release-runtime-observability
    CP6): every ``running`` run with its Controller's liveness and every
    non-terminal job with its worker's (read-only), across every target in
    this runtime root, each with the command that follows it. Settings-
    and-telemetry CP6 adds each one's start time, and a job's command and
    work item."""
    runs = [{
        "run_id": run.get("run_id"), "command": run.get("command"), "target_repo": run.get("target_repo"),
        "started_at": run.get("started_at"),
        "controller_pid": (run.get("controller_process") or {}).get("pid"), "liveness": liveness,
        "follow": job.follow_command(runtime_root, run.get("target_repo")),
    } for run, liveness in observe.active_runs(runtime_root)]
    jobs = [{
        "job_id": record.get("job_id"), "status": record.get("status"), "command": observe.job_command(record),
        "work_item_id": record.get("work_item_id"), "target_repo": record.get("target_repo"),
        "started_at": record.get("created_at"), "liveness": liveness,
        "activity": _job_activity_text(runtime_root, record, liveness),
        "follow": job.follow_command(runtime_root, record.get("target_repo")),
    } for record, liveness in observe.active_jobs(runtime_root)]
    return {"runs": runs, "jobs": jobs}


def _print_active(active: dict) -> None:
    if not active["runs"] and not active["jobs"]:
        print("active: none")
        return
    print("active:")
    for run in active["runs"]:
        print(f"  run {run['run_id']} ({run['command']}, target {run['target_repo']}, started {run['started_at']}): "
              f"controller pid {run['controller_pid']} {run['liveness']}")
        print(f"    follow: {run['follow']}")
    for entry in active["jobs"]:
        print(f"  job {entry['job_id']} ({entry['status']}, {entry['command'] or 'no command'}, "
              f"work item {entry['work_item_id'] or 'none'}, target {entry['target_repo']}, "
              f"started {entry['started_at']}): {entry['activity']}")
        print(f"    follow: {entry['follow']}")


def _job_activity_text(runtime_root: Path, record: dict, liveness: str | None) -> str:
    """One non-terminal job's activity: :func:`observe.job_activity`'s line
    for a record carrying ``worker_state`` (worker-lifecycle-ownership CP7),
    else the release-runtime-observability text, unchanged."""
    activity = observe.job_activity(record, runtime_root)
    if activity is not None:
        return activity["text"]
    process = record.get("worker_process") or {}
    return observe.drain_text(record) or (
        "no worker recorded" if liveness is None else f"worker pid {process.get('pid')} {liveness}")


def _target_jobs(runtime_root: Path, target: managed_repo.ManagedRepository) -> list[dict]:
    """``inspect``'s ``jobs`` block (worker-lifecycle-ownership CP7): one
    entry per non-terminal job for ``target``, read-only."""
    entries = []
    for record, liveness in observe.active_jobs(runtime_root):
        if record.get("target_repo") != str(target.root):
            continue
        activity = observe.job_activity(record, runtime_root)
        entry = {"job_id": record.get("job_id"), "status": record.get("status")}
        if activity is None:
            entry["text"] = _job_activity_text(runtime_root, record, liveness)
        else:
            entry.update(text=activity["text"], **observe.activity_fields(activity))
        entries.append(entry)
    return entries


def _print_target_jobs(entries: list[dict]) -> None:
    if not entries:
        return
    print("jobs:")
    for entry in entries:
        print(f"  job {entry['job_id']} ({entry['status']}): {entry['text']}")


def _print_last_telemetry(record: dict | None) -> None:
    if record is not None:
        print(telemetry.last_job_text(record))


def cmd_telemetry(args: argparse.Namespace) -> int:
    """``telemetry`` (settings-and-telemetry CP3, Design C): the recorded
    jobs' turns, tokens, cost, API time and wall time, as totals and
    per-job means, grouped by ``--by``; ``--json`` prints the rows and the
    groups. Dispatched before pinning like ``follow``: it reads the job
    records (deriving the figures of a record written before telemetry
    from its ``worker.stdout``) and writes nothing."""
    runtime_root = _follow_runtime_root(args)
    target_repo = (str(managed_repo._resolve_repository_root(Path(args.repo)))
                   if args.repo is not None else None)
    selected = telemetry.rows(observe.list_jobs(runtime_root), work_item=args.work_item, run=args.run,
                              since=args.since, target_repo=target_repo)
    grouped = telemetry.groups(selected, args.by)
    if args.json:
        print(json.dumps({"by": args.by, "rows": selected, "groups": grouped}, sort_keys=True))
        return EXIT_OK
    for line in telemetry.render_text(grouped, args.by, job_count=len(selected)):
        print(line)
    return EXIT_OK


def _follow_runtime_root(args: argparse.Namespace) -> Path:
    """The runtime root ``follow`` reads: the one ``step`` would use, found
    read-only -- the running code's kind from ``identity.resolve_runtime``
    (or a snapshot's own ``SOURCE_PIN.json``), never ``pin()``,
    materialisation or any write."""
    return _follow_runtime_root_row(args)[0]


def _follow_runtime_root_row(args: argparse.Namespace) -> tuple[Path, int]:
    """:func:`_follow_runtime_root` with the ladder row that produced it."""
    code_root = Path(identity.__file__).resolve().parent.parent
    pin = runtime.read_json(code_root / identity._SOURCE_PIN_NAME) if (
        code_root / identity._SOURCE_PIN_NAME).is_file() else None
    if pin is not None:
        origin = Path(pin["origin_source_root"]) if pin.get("origin_source_root") else code_root
        kind = pin.get("runtime_kind", identity.RUNTIME_KIND_SOURCE)
    else:
        origin, kind = code_root, identity.resolve_runtime(code_root).runtime_kind
    return runtime.resolve_runtime_root(
        runtime_dir=args.runtime_dir, origin_source_root=origin, runtime_kind=kind,
    )


def _stdout_sink(text: str) -> None:
    print(text, flush=True)


def cmd_follow(args: argparse.Namespace) -> int:
    """``follow`` (release-runtime-observability CP6): print a run's or a
    job's events and worker output as they are written, read-only.

    Dispatched before runtime-root creation, pinning, materialisation and
    the ``identity.json`` write: it writes nothing, takes no lock and sends
    no signal. With ``--run``/``--job`` it follows that record, live or
    finished, refusing (exit 20) an unknown id or a record for another
    target. Otherwise it follows the newest running run for ``<repo>``, or
    an orphaned job whose worker is still active, or says there is nothing
    to follow (exit 0). It never mirrors the followed run's exit code.

    A stdout whose reader has gone (``follow | head``) ends the follow
    like Ctrl-C does, with exit 0 and no traceback: the operator stopped
    reading, and nothing else is affected."""
    try:
        return _follow(args)
    except BrokenPipeError:
        # Nothing more can reach the reader. Dropping `sys.stdout` stops the
        # interpreter's final flush of the unwritten buffer from raising
        # again at exit (Python's exit 120).
        sys.stdout = None
        return EXIT_OK


def _follow(args: argparse.Namespace) -> int:
    runtime_root = _follow_runtime_root(args)
    target_repo = str(managed_repo._resolve_repository_root(Path(args.repo)))
    kind, record_id = ("run", args.run) if args.run else ("job", args.job) if args.job else (None, None)
    if kind is None:
        selected = observe.select_active(runtime_root, target_repo)
        if selected is None:
            last = observe.last_run(runtime_root, target_repo)
            if last is None:
                _stdout_sink(f"nothing active for {target_repo}; no runs recorded")
            else:
                _stdout_sink(f"nothing active for {target_repo}; last run {last['run_id']} "
                             f"{_run_outcome(last)}; replay: workflow-controller --runtime-dir {runtime_root} "
                             f"follow --run {last['run_id']} --from-start {target_repo}")
            return EXIT_OK
        kind, record_id = selected
    record = (observe.read_run if kind == "run" else observe.read_job)(runtime_root, record_id)
    if record is None:
        raise ControllerError(
            f"no readable {kind} record {record_id!r} under {runtime_root}",
            evidence={"kind": kind, "id": record_id, "runtime_root": str(runtime_root)},
        )
    if record.get("target_repo") != target_repo:
        raise ControllerError(
            f"{kind} {record_id} belongs to target {record.get('target_repo')!r}, not {target_repo}",
            evidence={"kind": kind, "id": record_id, "target_repo": record.get("target_repo")},
        )
    follow = observe.follow_run if kind == "run" else observe.follow_job
    try:
        effective = _effective(args)
        follow(runtime_root, record_id, _stdout_sink, from_start=args.from_start, json_output=args.json,
               heartbeat_seconds=effective["follow.heartbeat_seconds"],
               replay_events=effective["follow.replay_events"])
    except KeyboardInterrupt:
        pass  # the operator stopped following; nothing else is affected
    return EXIT_OK


def _run_outcome(run: dict) -> str:
    if run.get("state") == job.RUN_STATE_ENDED:
        return f"ended with exit {run.get('exit_code')}"
    if run.get("state") == job.RUN_STATE_INTERRUPTED:
        return "was interrupted"
    return "was not closed"


def _start_follower(args: argparse.Namespace, runtime_root: Path, run_id: str) -> None:
    """The one place ``--follow`` is read. When set, start a daemon thread
    rendering run ``run_id`` from its durable logs onto a private duplicate
    of stderr (:class:`observe.FdSink`). The lifecycle calls are the same
    either way; the thread's failures stay in the thread."""
    global _follower
    if not getattr(args, "follow", False):
        return
    try:
        fd = os.dup(2)
    except OSError:
        return
    sink = observe.FdSink(fd)
    stop = threading.Event()
    heartbeat_seconds = _effective(args)["follow.heartbeat_seconds"]
    thread = threading.Thread(target=_render_run, args=(runtime_root, run_id, sink, stop, heartbeat_seconds),
                              name="workflow-controller-follow", daemon=True)
    thread.start()
    _follower = (thread, stop)


def _render_run(runtime_root: Path, run_id: str, sink: observe.FdSink, stop: threading.Event,
                heartbeat_seconds: float | None = None) -> None:
    """The renderer thread's body. Any exception disables rendering for the
    rest of the process (one note, if the descriptor still works) and is
    never propagated."""
    try:
        observe.follow_run(runtime_root, run_id, sink, from_start=True, stop=stop,
                           heartbeat_seconds=heartbeat_seconds)
    except Exception as exc:  # noqa: BLE001 -- a renderer failure never reaches the lifecycle
        sink.fail(exc)
    finally:
        try:
            os.close(sink.fd)
        except OSError:
            pass


def _stop_follower(*, join: bool) -> None:
    """Tell the renderer thread to drain and stop; give it
    :data:`FOLLOWER_JOIN_SECONDS` when ``join``. A thread still running
    after that is abandoned (it is a daemon)."""
    global _follower
    follower, _follower = _follower, None
    if follower is None:
        return
    thread, stop = follower
    stop.set()
    if join:
        thread.join(FOLLOWER_JOIN_SECONDS)


def _inspect_target(args: argparse.Namespace) -> managed_repo.ManagedRepository:
    """The five-check managed-repository read every non-``status`` command
    performs first (capability 1) -- factored once so ``inspect``,
    ``explain``, ``step``, ``run`` and ``resume`` all fail the same way on
    an unmanaged/drifted/unsupported target."""
    return managed_repo.inspect(args.repo, manager_bin=args.workflow_manager)


def _work_item_payload(work_item: target_state.WorkItemView) -> dict:
    return {
        "work_item_id": work_item.work_item_id,
        "work_item_type": work_item.work_item_type,
        "work_item_kind": work_item.work_item_kind,
        "governing_workflow_version": work_item.governing_workflow_version,
        "phase": work_item.phase,
        "plan_revision": work_item.plan_revision,
        "implementation_revision": work_item.implementation_revision,
        "functional_review_round": work_item.functional_review_round,
        "current_checkpoint_id": work_item.current_checkpoint_id,
        "last_completed_checkpoint_id": work_item.last_completed_checkpoint_id,
        "registry_complete": work_item.registry_complete,
        "incomplete_children": list(work_item.incomplete_children),
    }


def cmd_inspect(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``inspect``: managed-repo verification + Workflow state summary;
    read-only (capabilities 1 and 2)."""
    target = _inspect_target(args)
    snapshot = target_state.read(target)
    work_item = target_state.select_work_item(snapshot, work_item_id=args.work_item)

    # CP5: the non-acquiring lifecycle-lock probe -- `unknown` never means
    # `free`, and `step` decides by acquiring, never by this report.
    lock_state = lock.probe_lifecycle_lock(target.root)
    # worker-lifecycle-ownership CP7: the target's non-terminal jobs and
    # what each is doing, beside the lock; omitted when there is none.
    jobs = _target_jobs(runtime_root, target)
    jobs_block = {"jobs": jobs} if jobs else {}
    # Settings-and-telemetry CP3: the target's newest job with a telemetry
    # block, omitted -- never `null` -- when there is none.
    last = telemetry.last_finished(observe.list_jobs(runtime_root), target_repo=str(target.root))
    if last is not None:
        jobs_block["last_job_telemetry"] = telemetry.last_job_entry(last)
    # CP8 (trunk-branch-pr-release-orchestration): `repository_policy` and
    # `milestone_branch`, from local Git and the binding records only, each
    # omitted -- never `null` -- when it does not apply (I1, I10).
    branch_blocks = milestone_branch.observation(_branch_context(target, runtime_root))

    if work_item is target_state.NoWorkItemYet:
        # revision 63/64's B2 bootstrap sentinel (CONTROLLER_GEN1_PLAN.md's
        # "NoWorkItemYet CLI dispatch"): there is no WorkItemView to build a
        # payload from -- report the bootstrap state directly instead.
        if args.json:
            print(json.dumps({
                "repository": _repository_block(target),
                "work_item": None,
                "lifecycle_lock": lock_state,
                **jobs_block,
                "controller": identity.runtime_record(ident),
                **branch_blocks,
            }))
            return EXIT_OK
        _print_repository_line(target)
        print(f"lifecycle lock: {lock_state}")
        _print_target_jobs(jobs)
        _print_last_telemetry(last)
        print("work item: none -- no non-terminal work item exists and none was explicitly named")
        _print_branch_blocks(branch_blocks)
        return EXIT_OK

    if args.json:
        print(json.dumps({
            "repository": _repository_block(target),
            "work_item": _work_item_payload(work_item),
            "lifecycle_lock": lock_state,
            **jobs_block,
            "controller": identity.runtime_record(ident),
            **branch_blocks,
        }))
        return EXIT_OK

    _print_repository_line(target)
    print(f"lifecycle lock: {lock_state}")
    _print_target_jobs(jobs)
    _print_last_telemetry(last)
    print(f"work item: {work_item.work_item_id} "
          f"(type={work_item.work_item_type} kind={work_item.work_item_kind} "
          f"governing_workflow_version={work_item.governing_workflow_version})")
    print(f"phase: {work_item.phase}")
    print(f"plan_revision={work_item.plan_revision} "
          f"implementation_revision={work_item.implementation_revision} "
          f"functional_review_round={work_item.functional_review_round}")
    print(f"current_checkpoint_id={work_item.current_checkpoint_id} "
          f"last_completed_checkpoint_id={work_item.last_completed_checkpoint_id} "
          f"registry_complete={work_item.registry_complete}")
    if work_item.incomplete_children:
        print(f"incomplete children: {', '.join(work_item.incomplete_children)}")
    _print_branch_blocks(branch_blocks)
    return EXIT_OK


def _repository_block(target: managed_repo.ManagedRepository) -> dict:
    """``inspect``'s ``repository`` object. A protocol target (orchestration-
    protocol-v1 F) adds ``workflow_mode``, ``target_protocol``, the
    managed-script digest map and the C.3 advisory ``unknown_action_ids``;
    a legacy target's object gains no key."""
    block = {"root": str(target.root), "workflow_version": target.workflow_version, "profile": target.profile}
    if target.target_protocol is not None:
        block["workflow_mode"] = "protocol"
        block["target_protocol"] = dict(target.target_protocol)
        block["script_digests"] = dict(target.script_digests or {})
        block["unknown_action_ids"] = protocol_decision.unknown_action_ids(target.protocol_action_ids)
    return block


def _print_repository_line(target: managed_repo.ManagedRepository) -> None:
    print(f"repository: {target.root} (Workflow {target.workflow_version}, profile {target.profile})")
    if target.target_protocol is not None:
        print(f"workflow mode: protocol (protocol {target.target_protocol['version']}, "
              f"{len(target.script_digests or {})} managed scripts)")
        unknown = protocol_decision.unknown_action_ids(target.protocol_action_ids)
        if unknown:
            print(f"advisory: the Workflow lists action ids this Controller release cannot launch: "
                  f"{', '.join(unknown)}; each is blocked if it becomes the next action")


def _branch_context(target: managed_repo.ManagedRepository, runtime_root: Path, *,
                    auto_merge: bool = True) -> milestone_branch.Context:
    return milestone_branch.Context(repo_root=target.root, runtime_root=runtime_root, auto_merge=auto_merge)


def _print_branch_blocks(blocks: dict) -> None:
    """``inspect``'s text form of the CP8 blocks: one line each, printed
    only when the block is present."""
    policy = blocks.get("repository_policy")
    if policy is not None:
        print(f"repository policy: {policy['path']} ({policy['source']}, sha256 {policy['sha256']}) "
              f"milestone_branches={policy['milestone_branches_enabled']} release={policy['release_enabled']} "
              f"trunk={policy['trunk']['remote']}/{policy['trunk']['branch']} forge={policy['forge_repository']}")
    binding = blocks.get("milestone_branch")
    if binding is not None:
        pr = binding["pr"]
        pr_text = "no pull request" if pr is None else (
            f"pull request #{pr['number']} {pr['url']}{' (draft)' if pr['draft'] else ''}")
        observed = binding["last_observation"]
        drift = "not observed yet" if observed is None else (
            f"{'fresh' if observed['fresh'] else 'behind'} ({observed['behind']} behind the trunk), "
            f"observed {observed['observed_at']}")
        print(f"milestone branch: {binding['branch']} for {binding['work_item_id']} ({binding['state']}), "
              f"branch point {binding['branch_point']}, {pr_text}, {drift}"
              f"{milestone_branch.merge_release_text(binding)}")


def cmd_explain(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``explain``: the next-action decision with full evidence, and, at a
    gate, exactly what a human must do (capability 3). Read-only -- it
    only ever calls :func:`controller.evidence.decide`, never
    :func:`controller.job.execute_step`, so no job record is written and
    no worker is launched."""
    target = _inspect_target(args)
    # CP5: the job files `step`/`run` would refuse on, and the lifecycle
    # lock, are reported ahead of the decision -- for a work item and for
    # the no-work-item bootstrap alike -- through the same read-only scan
    # the refusal uses. Neither changes the exit code.
    pending = job.pending_reconciliation_jobs(runtime_root, target, ident)
    lock_state = lock.probe_lifecycle_lock(target.root)
    # worker-lifecycle-ownership CP7: each pending job's activity, and the
    # newest terminal job's stream diagnosis when it has one to explain.
    pending_details = [_pending_job_details(runtime_root, target, entry) for entry in pending]
    last_job = _last_job_diagnosis(runtime_root, target)
    last_job_block = {} if last_job is None else {"last_job": last_job}
    if not args.json:
        _print_pending_jobs(pending, pending_details)
        _print_last_job(last_job)
        print(f"lifecycle lock: {lock_state}")
    snapshot = target_state.read(target)
    work_item = target_state.select_work_item(snapshot, work_item_id=args.work_item)
    # CP8 (trunk-branch-pr-release-orchestration): the preflight outcome the
    # next step would take, from local Git and the records only (no fetch,
    # no `gh`); `None`, and the key omitted, when no policy or binding
    # applies (I1, I10). Its predicted trunk-start base is the bootstrap's
    # base, as `step` passes the passed trunk start's (squash-merge Design F).
    # `merge.auto` decides whether a READY auto-merge binding predicts the
    # merge (auto-merge-release-wait A.2, F).
    branch_ctx = _branch_context(target, runtime_root, auto_merge=_effective(args)["merge.auto"])
    preflight = milestone_branch.predict(branch_ctx, requested_work_item_id=args.work_item)
    preflight_block = {} if preflight is None else {"repository_preflight": preflight}
    # The same job-history read `job.execute_step` makes (automatic-
    # lifecycle-orchestration CP4B), so `explain` and `step` see the same
    # apply relaunch bound. It never raises on a job file and writes nothing.
    base = None if preflight is None else preflight["base"]
    if target.target_protocol is not None:
        # A protocol target is decided by the Workflow's own `next-action`
        # (orchestration-protocol-v1 C.1), read-only like `evidence.decide`.
        decision, advisories = protocol_decision.decide_after_preflight(target, work_item, base=base)
        if decision.protocol is not None and decision.automatic:
            # The loop guard `step` applies to the decision that stands.
            decision = job._no_progress_gate(runtime_root, target, work_item, decision,
                                             advisories=advisories) or decision
    elif work_item is target_state.NoWorkItemYet:
        decision = decide_no_work_item(target, base=base)
    else:
        decision = evidence.decide(
            target, snapshot, work_item,
            last_apply_job=job.last_launched_apply_job_view(runtime_root, target.root, work_item.work_item_id),
        )

    if args.json:
        gate = decision.gate
        print(json.dumps({
            "observed_phase": phase_to_wire(decision.observed_phase),
            "evidence": list(decision.evidence),
            "automatic": decision.automatic,
            "declined": decision.declined,
            "action": decision.action.command if decision.action is not None else None,
            "task_addendum": decision.action.task_addendum if decision.action is not None else None,
            "reason": decision.reason,
            "gate": None if gate is None else {
                "repository": gate.repository,
                "work_item_id": gate.work_item_id,
                "phase": gate.phase,
                "what_is_required": gate.what_is_required,
                "artifact_path": gate.artifact_path,
                "safe_resume_command": gate.safe_resume_command,
            },
            "pending_jobs": [{**entry.to_dict(), **details} for entry, details in zip(pending, pending_details)],
            **last_job_block,
            "lifecycle_lock": lock_state,
            "controller": identity.runtime_record(ident),
            **preflight_block,
            **({} if decision.protocol is None else {"protocol": _protocol_block(decision.protocol)}),
        }))
        return EXIT_OK

    if preflight is not None:
        gate_text = "" if preflight["gate"] is None else f" ({preflight['gate']})"
        print(f"repository preflight: {preflight['action']}{gate_text} -- {preflight['detail']}")
    print(f"phase: {decision.observed_phase}")
    if decision.protocol is not None:
        _print_protocol(decision.protocol)
    for line in decision.evidence:
        print(f"  evidence: {line}")
    print(f"reason: {decision.reason}")
    if decision.gate is not None:
        gate = decision.gate
        print(f"human gate -- what is required: {gate.what_is_required}")
        if gate.artifact_path is not None:
            print(f"  artifact: {gate.artifact_path}")
        print(f"  safe resume command: {gate.safe_resume_command}")
    elif decision.automatic:
        print(f"next automatic action: {decision.action.command}")
        if decision.action.task_addendum is not None:
            print(f"  task addendum: {decision.action.task_addendum}")
    elif decision.declined:
        print(f"declined by this generation (automation-safe, but not automated here): "
              f"{decision.action.command}")
    else:
        print("no action at this phase (e.g. LEGACY_READY or MILESTONE_COMPLETE)")
    return EXIT_OK


def _protocol_block(info: Any) -> dict:
    """``explain``'s ``protocol`` block for a protocol target (additive: a
    legacy target's output has no such key): the Workflow's row, disposition
    and action id, the identity the decision was made at, and the
    alternatives' invocations."""
    return {
        "release": info.release,
        "protocol_version": info.protocol_version,
        "row": info.row,
        "disposition": info.disposition,
        "action_id": info.action_id,
        "state_identity": info.state_identity,
        "route": info.route,
        "alternatives": list(info.alternatives),
    }


def _print_protocol(info: Any) -> None:
    print(f"workflow mode: protocol (Workflow {info.release}, protocol {info.protocol_version})")
    action = "none" if info.action_id is None else info.action_id
    print(f"  protocol row {info.row}: disposition {info.disposition}, action {action}")
    if info.alternatives:
        print(f"  alternatives: {', '.join(info.alternatives)}")


def _print_pending_jobs(pending: list, details: list[dict] | None = None) -> None:
    """``explain``'s pending report (CP5): each job file ``step``/``run``
    refuse on, with the command that clears it -- and, for a record carrying
    ``worker_state`` (worker-lifecycle-ownership CP7), its activity and its
    stream diagnosis."""
    if not pending:
        return
    print(f"pending job files: {len(pending)} -- `step`/`run` refuse until each is cleared")
    for entry, extra in zip(pending, details or [{}] * len(pending)):
        print(f"  pending job: {entry.job_id} ({entry.status or 'unreadable'}) -- {entry.reason}; "
              f"clear it with: {entry.clearing_command}")
        if extra.get("activity_text"):
            print(f"    activity: {extra['activity_text']}")
        for line in observe.diagnosis_lines(extra["stream_diagnosis"]) if extra.get("stream_diagnosis") else []:
            print(f"    {line}")


def _pending_job_details(runtime_root: Path, target: managed_repo.ManagedRepository, entry: job.PendingJob) -> dict:
    """A pending job's CP7 fields for ``explain`` (worker-lifecycle-
    ownership, plan G): ``activity``, ``worker_state``, ``waiting_on``,
    ``owned_processes`` (and the rest of :func:`observe.activity_fields`),
    plus ``stream_diagnosis`` for a ``COMPLETED`` record -- all omitted for
    a record without ``worker_state``, whose report is unchanged."""
    record = observe.read_job(runtime_root, entry.job_id)
    if record is None or record.get("target_repo") != str(target.root):
        return {}
    activity = observe.job_activity(record, runtime_root)
    if activity is None:
        return {}
    details = {**observe.activity_fields(activity), "activity_text": activity["text"]}
    diagnosis = observe.stream_diagnosis(record) if record.get("status") == job.STATUS_COMPLETED else None
    if diagnosis is not None:
        details["stream_diagnosis"] = diagnosis
    return details


def _last_job_diagnosis(runtime_root: Path, target: managed_repo.ManagedRepository) -> dict | None:
    """The newest job for ``target``, when it is terminal and its stream
    diagnosis has something to explain (:func:`observe.stream_diagnosis`)."""
    records = [r for r in observe.list_jobs(runtime_root) if r.get("target_repo") == str(target.root)]
    if not records or records[-1].get("status") not in job.TERMINAL_STATUSES:
        return None
    diagnosis = observe.stream_diagnosis(records[-1])
    if diagnosis is None:
        return None
    return {"job_id": records[-1].get("job_id"), "status": records[-1].get("status"), "stream_diagnosis": diagnosis}


def _print_last_job(last_job: dict | None) -> None:
    if last_job is None:
        return
    print(f"last job: {last_job['job_id']} ({last_job['status']})")
    for line in observe.diagnosis_lines(last_job["stream_diagnosis"]):
        print(f"  {line}")


#: ``EffectiveSettings.sources["routing"]`` -> ``worker_route.config_source``.
_ROUTING_CONFIG_SOURCES = {
    settings.SOURCE_CLI: routing.CONFIG_SOURCE_ROUTING_CONFIG,
    settings.SOURCE_FILE: routing.CONFIG_SOURCE_SETTINGS,
    settings.SOURCE_DEFAULT: routing.CONFIG_SOURCE_NONE,
}


def _routing_options(args: argparse.Namespace) -> routing.RoutingOptions:
    """The operator's routing overrides (CP6) over the routing config in
    force (settings-and-telemetry CP2, I1): the ``--routing-config`` file
    when given, which replaces the settings file's ``routing`` section
    whole, else that section. Both were parsed with the settings, before
    any job record is written, so a malformed one is exit 20 and nothing
    runs. Read with ``getattr`` defaults, as ``cmd_resume`` reads
    ``--abandon``, so a caller's hand-built namespace without these options
    routes by the built-in table."""
    effective = _effective(args)
    return routing.RoutingOptions(
        cli_model=getattr(args, "model", None),
        cli_effort=getattr(args, "effort", None),
        cli_role_models=getattr(args, "role_model", None) or {},
        cli_role_efforts=getattr(args, "role_effort", None) or {},
        config=effective.routing_config,
        config_source=_ROUTING_CONFIG_SOURCES[effective.sources[settings.ROUTING_KEY]],
    )


class _UsagePause:
    """One ``run``'s usage-pause state (usage-budget CP3, D7): whether an
    earlier attempt was held (so the next admission that passes announces
    ``usage_resumed``), and how long this step has already waited."""

    def __init__(self) -> None:
        self.held = False
        self.waited = 0.0


class UsageWaitInterrupted(milestone_branch.WaitInterrupted):
    """Ctrl-C during a usage pause: a ``KeyboardInterrupt`` like the merge
    wait's, printed as one line. No job record exists."""

    def message(self) -> str:
        return (f"interrupted while paused for the usage budget (the pause would have ended at {self.deadline}); "
                "nothing was started")


def _usage_event_fields(*, provider: str | None, repository: str, run_id: str | None,
                        hold: Any = None, token: str | None = None) -> dict:
    """The fields D8 gives every usage event."""
    return {
        "provider": provider, "window": getattr(hold, "window", None), "percent": getattr(hold, "percent", None),
        "forecast": getattr(hold, "forecast", None), "resume_at": getattr(hold, "resume_at", None),
        "reason": getattr(hold, "reason", None), "token": token, "repository": repository, "run_id": run_id,
    }


def _usage_gate(runtime_root: Path, target: managed_repo.ManagedRepository, run: job.RunRecord | None,
                effective: settings.EffectiveSettings, pause: _UsagePause | None):
    """``execute_step``'s ``usage_gate`` from the settings (usage-budget
    CP3, D7): ``usage.admit`` for a Claude job of the route's role and
    model, in this repository and run, against the Claude limits. ``None``
    when ``usage.enabled`` is false. After an earlier hold in this run, the
    first admission that passes emits ``usage_resumed`` -- never a wake-up
    alone."""
    limits = usage.limits_from_values(effective.values, usage.PROVIDER_CLAUDE)
    if not limits.enabled:
        return None
    repository = str(target.root)
    run_id = None if run is None else run.run_id

    def gate(route: routing.ResolvedRoute) -> Any:
        admission = usage.admit(runtime_root, provider=usage.PROVIDER_CLAUDE, role=route.role, model=route.model,
                                repository=repository, run_id=run_id, limits=limits, now=job.usage_now())
        if admission.hold is not None:
            return admission.hold
        if pause is not None and pause.held:
            pause.held = False
            fields = _usage_event_fields(provider=usage.PROVIDER_CLAUDE, repository=repository, run_id=run_id,
                                         token=admission.token)
            if run is not None:
                run.event("usage_resumed", **fields)
            usage.append_event(runtime_root, "usage_resumed", now=job.usage_now(), **fields)
        return admission.token

    return gate


def _local_time(epoch: float) -> str:
    """``epoch`` in local time, with the epoch itself (D7, D10); a stored
    figure beyond the calendar shows as the bare epoch."""
    return observe._epoch_text(epoch)


def usage_hold_message(hold: job.UsageHold, *, max_wait_seconds: int | None = None) -> str:
    """The one stderr line of exit 17 (D10): a timed pause names its resume
    time; a run cap says no reset lifts it; a repository cap says a new run
    does not clear it and names the window reset. Outstanding work is named
    when it is part of what holds a cap."""
    head = f"workflow-controller: stopped for the usage budget, nothing was started: {hold.reason}"
    if hold.window == "run_cap":
        text = f"{head} -- no reset will lift this; raise usage.run_cap_percent or start a new run"
    elif hold.window == "repository_cap":
        when = (f"run again after the window resets at {_local_time(hold.resets_at)} (the spending then falls)"
                if hold.resets_at is not None else
                "run again after the five-hour window resets (the spending then falls)")
        text = (f"{head} -- a new run does not clear this repository's spending, which is counted per five-hour "
                f"window; {when}, or raise usage.repository_cap_percent")
    elif hold.resume_at is not None:
        text = f"{head} -- a timed pause: run again after the resume time {_local_time(hold.resume_at)}"
        if max_wait_seconds is not None:
            text += f" (waiting for it would exceed usage.max_wait_seconds, {max_wait_seconds} s)"
    else:
        text = f"{head} -- the window has no reset left to wait for; run again once a newer reading is recorded"
    if hold.outstanding:
        text += "; outstanding work (reservations of jobs not yet accounted) is part of what holds the cap"
    return text


def _usage_paused(runtime_root: Path, run: job.RunRecord | None, target: managed_repo.ManagedRepository,
                  hold: job.UsageHold, *, waiting_until: float | None = None) -> None:
    """The ``usage_paused`` event (D8, D9), in the run's log -- with the
    branch preflight's events of the attempt -- and in
    ``usage-events.jsonl``."""
    fields = _usage_event_fields(provider=hold.provider, repository=str(target.root),
                                 run_id=None if run is None else run.run_id, hold=hold)
    if run is not None:
        run.event("usage_paused", **fields, waitable=hold.waitable, waiting_until=waiting_until,
                  preflight_events=list(hold.preflight_events))
    usage.append_event(runtime_root, "usage_paused", now=job.usage_now(), **fields)


def _usage_wait(args: argparse.Namespace, runtime_root: Path, run: job.RunRecord,
                target: managed_repo.ManagedRepository, hold: job.UsageHold, pause: _UsagePause) -> bool:
    """``run``'s pause (D7): for a waitable hold whose resume time plus
    ``usage.resume_grace_seconds`` keeps this step's cumulative wait within
    ``usage.max_wait_seconds``, emit ``usage_paused`` and sleep to it in
    short interruptible slices, with no lock held, and return ``True`` (the
    loop re-enters the whole boundary). Otherwise -- a cap, a longer wait --
    emit ``usage_paused``, print the exit-17 line and return ``False``."""
    limits = usage.limits_from_values(_effective(args).values, usage.PROVIDER_CLAUDE)
    now = job.usage_now()
    if hold.waitable and hold.resume_at is not None:
        until = hold.resume_at + limits.resume_grace_seconds
        if pause.waited + max(0.0, until - now) <= limits.max_wait_seconds:
            _usage_paused(runtime_root, run, target, hold, waiting_until=until)
            pause.held = True
            try:
                while (remaining := until - job.usage_now()) > 0:
                    _usage_sleep(min(remaining, USAGE_WAIT_SLICE_SECONDS))
            except KeyboardInterrupt:
                raise UsageWaitInterrupted("usage_paused", _local_time(until)) from None
            pause.waited += job.usage_now() - now
            return True
        _usage_paused(runtime_root, run, target, hold)
        print(usage_hold_message(hold, max_wait_seconds=limits.max_wait_seconds), file=sys.stderr)
        return False
    _usage_paused(runtime_root, run, target, hold)
    print(usage_hold_message(hold), file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# `usage` (workflow-controller-usage-budget CP4, D8): the view and the manual
# workers' gate
# ---------------------------------------------------------------------------


def _codex_home() -> Path:
    """The Codex home the Codex readings come from: ``$CODEX_HOME``, else
    ``~/.codex`` (D3)."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _usage_providers(args: argparse.Namespace, *, gate: bool) -> list[str]:
    """The providers a ``usage`` call looks at: the ``--provider`` given,
    else every provider to show and Claude to gate (D8)."""
    if args.provider in usage.PROVIDERS:
        return [args.provider]
    return [usage.PROVIDER_CLAUDE] if gate else list(usage.PROVIDERS)


def _usage_fresh_readings(provider: str) -> list[dict]:
    """The readings a provider's own files hold now: Codex's rollouts; none
    for Claude, whose readings arrive with worker streams (D2, D3)."""
    return usage.read_codex_home(_codex_home()) if provider == usage.PROVIDER_CODEX else []


def _usage_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _usage_print_stderr(text: str) -> None:
    print(text, file=sys.stderr)


def _usage_context(args: argparse.Namespace) -> dict:
    """The repository and run a ``usage`` call evaluates caps for. An
    absent ``--repo`` or ``--run-id`` leaves that cap unevaluated, and the
    output says so (D8)."""
    repository = None
    if args.repo is not None:
        repository = str(managed_repo._resolve_repository_root(Path(args.repo)))
    return {"repository": repository, "run_id": args.run_id}


def _usage_view(record: dict, provider: str, limits: usage.Limits, args: argparse.Namespace, context: dict,
                now: float) -> dict:
    """One provider's part of the ``usage`` view: each window's percent,
    reset and reading age, the live and lapsed reservations, the forecast per
    ``(role, model)`` in the ledger and the spend the caps see."""
    readings = usage.current_readings(record, provider)
    windows: dict = {}
    for window in usage.WINDOWS:
        reading = readings.get(window)
        stamp = None if reading is None else usage.observed_stamp(reading)
        windows[window] = None if reading is None else {
            "percent": usage.window_percent(reading, now), "stored_percent": reading["percent"],
            "resets_at": reading["resets_at"], "observed_at": stamp,
            "age_seconds": None if stamp is None else max(0.0, now - stamp), "source": reading.get("source"),
        }

    def reservation(token: str, entry: dict, state: str) -> dict:
        return {"token": token, "state": state, "role": entry.get("role"), "model": entry.get("model"),
                "repository": entry.get("repository"), "run_id": entry.get("run_id"), "job_id": entry.get("job_id"),
                "expires_at": entry.get("expires_at"), "five_hour": entry[usage.FIVE_HOUR]["amount"],
                "weekly": entry[usage.WEEKLY]["amount"]}

    reservations = [reservation(t, r, "live") for t, r in record["reservations"].items() if r["provider"] == provider]
    reservations += [reservation(t, r, "lapsed") for t, r in record["lapsed"].items() if r["provider"] == provider]
    groups = sorted({(e.get("role"), e.get("model")) for e in record["ledger"] if e["provider"] == provider},
                    key=lambda g: (str(g[0]), str(g[1])))
    forecasts = []
    for role, model in groups:
        five, weekly = usage.forecast(record, provider, role, model, limits)
        samples = sum(1 for e in record["ledger"] if e["provider"] == provider and e.get("role") == role
                      and e.get("model") == model and e.get("five_hour") is not None)
        forecasts.append({"role": role, "model": model, "five_hour": five, "weekly": weekly, "samples": samples})
    default = usage.forecast(record, provider, args.role, args.usage_model, limits)
    return {
        "windows": windows, "reservations": reservations, "forecasts": forecasts,
        "thresholds": {usage.FIVE_HOUR: limits.pause_at_percent, usage.WEEKLY: limits.weekly_pause_at_percent},
        "forecast": {usage.FIVE_HOUR: default[0], usage.WEEKLY: default[1]},
        "run": None if context["run_id"] is None else {
            "run_id": context["run_id"], "cap": limits.run_cap_percent,
            "spent": usage.run_spent(record, provider, context["run_id"]),
            "outstanding": usage.run_outstanding(record, provider, context["run_id"])},
        "repository": None if context["repository"] is None else {
            "repository": context["repository"], "cap": limits.repository_cap_percent,
            "spent": usage.repository_spent(record, provider, context["repository"], now),
            "outstanding": usage.repository_outstanding(record, provider, context["repository"], now)},
    }


def _usage_view_text(view: dict, provider: str) -> list[str]:
    lines = [f"{provider}:"]
    for window in usage.WINDOWS:
        w = view["windows"][window]
        label = "five-hour" if window == usage.FIVE_HOUR else "weekly"
        threshold = view["thresholds"][window]
        if w is None:
            lines.append(f"  {label}: no reading (threshold {threshold}%)")
            continue
        age = "at an unknown time" if w["age_seconds"] is None else f"{int(w['age_seconds'])} s ago"
        lines.append(f"  {label}: {w['percent']:.0f}% (threshold {threshold}%), resets {_local_time(w['resets_at'])}, "
                     f"read {age} from {w['source']}")
    live = [r for r in view["reservations"] if r["state"] == "live"]
    lapsed = [r for r in view["reservations"] if r["state"] == "lapsed"]
    lines.append(f"  reservations: {len(live)} live, {len(lapsed)} lapsed")
    for r in view["reservations"]:
        lines.append(f"    {r['token'][:8]} {r['state']} {r['role'] or '-'}/{r['model'] or '-'} "
                     f"{r['five_hour']:.0f}% five-hour, {r['weekly']:.0f}% weekly, repository {r['repository'] or '-'}, "
                     f"run {r['run_id'] or '-'}")
    if view["forecasts"]:
        lines.append("  forecast per role/model:")
        for f in view["forecasts"]:
            lines.append(f"    {f['role'] or '-'}/{f['model'] or '-'}: {f['five_hour']:.1f}% five-hour, "
                         f"{f['weekly']:.1f}% weekly from {f['samples']} measured jobs")
    else:
        lines.append("  forecast: the provider default (no jobs recorded)")
    for key, label in (("run", "run"), ("repository", "repository")):
        part = view[key]
        if part is None:
            lines.append(f"  {label} cap: not evaluated (no --{'run-id' if key == 'run' else 'repo'})")
        else:
            cap = "none" if part["cap"] is None else f"{part['cap']}%"
            lines.append(f"  {label} cap {cap}: spent {part['spent']:.0f}%, outstanding {part['outstanding']:.0f}%")
    return lines


def _usage_show(args: argparse.Namespace, runtime_root: Path, values: dict) -> int:
    """``usage`` with no mode: each window's percent, reset, reading age, the
    reservations and the forecasts. Its writes are the Codex readings it
    merges into the shared record and the replay sweep's ``accounting:
    "done"`` on a terminal job record a crash left unaccounted."""
    context = _usage_context(args)
    now = job.usage_now()
    job._settle_usage(runtime_root)
    views: dict = {}
    for provider in _usage_providers(args, gate=False):
        fresh = _usage_fresh_readings(provider)
        if fresh:
            usage.record_reading(runtime_root, fresh)
        record = usage.snapshot(runtime_root, now=now)
        views[provider] = _usage_view(record, provider, usage.limits_from_values(values, provider), args, context, now)
    if _usage_json(args):
        print(json.dumps({"at": now, "providers": views}, sort_keys=True))
        return EXIT_OK
    for provider, view in views.items():
        for line in _usage_view_text(view, provider):
            print(line)
    return EXIT_OK


def _usage_manual_event(runtime_root: Path, name: str, provider: str, context: dict, *, hold: Any = None,
                        token: str | None = None) -> None:
    """A manual worker's event in ``usage-events.jsonl`` (D8)."""
    usage.append_event(runtime_root, name, now=job.usage_now(), **_usage_event_fields(
        provider=provider, repository=context["repository"], run_id=context["run_id"], hold=hold, token=token))


def _usage_gate_output(args: argparse.Namespace, provider: str, admission: usage.Admission | None,
                       context: dict, *, hold: usage.Hold | None, waited: float,
                       max_wait_seconds: int | None = None, disabled: bool = False) -> int:
    """Print the outcome of a ``--check``/``--wait`` and return its exit
    code: 0 on go, 17 on a hold. With ``--reserve`` in text mode the token is
    the only line on stdout, so a script can capture it; everything else goes
    to stderr."""
    token = None if admission is None else admission.token
    notes = [] if admission is None else list(admission.notes)
    forecast = None if admission is None else {usage.FIVE_HOUR: admission.forecast[0],
                                               usage.WEEKLY: admission.forecast[1]}
    notes.append("repository cap not evaluated (no --repo)" if context["repository"] is None else
                 f"repository cap evaluated for {context['repository']}")
    notes.append("run cap not evaluated (no --run-id)" if context["run_id"] is None else
                 f"run cap evaluated for run {context['run_id']}")
    if disabled:
        notes.insert(0, "usage.enabled is false: nothing was evaluated or reserved")
    code = EXIT_OK if hold is None else EXIT_USAGE_PAUSED
    if _usage_json(args):
        print(json.dumps({
            "provider": provider, "decision": "go" if hold is None else "hold", "token": token, "forecast": forecast,
            "waited_seconds": waited, "notes": notes, "hold": None if hold is None else dataclasses.asdict(hold),
            "message": None if hold is None else usage_hold_message(hold, max_wait_seconds=max_wait_seconds),
        }, sort_keys=True))
        return code
    for note in notes:
        _usage_print_stderr(f"workflow-controller: usage: {note}")
    if hold is not None:
        _usage_print_stderr(usage_hold_message(hold, max_wait_seconds=max_wait_seconds))
    elif token is not None:
        print(token)
    elif not disabled:
        print("go")
    return code


def _usage_check_or_wait(args: argparse.Namespace, runtime_root: Path, values: dict) -> int:
    """``usage --check`` and ``--wait`` (D8): evaluate the budget for one
    provider, once or in a bounded loop that re-evaluates after every wait
    (another lane may have taken the headroom while this one slept), and
    reserve the forecast on go with ``--reserve``. ``usage_resumed`` is
    appended only after the final ``go``."""
    provider = _usage_providers(args, gate=True)[0]
    limits = usage.limits_from_values(values, provider)
    context = _usage_context(args)
    if not limits.enabled:
        return _usage_gate_output(args, provider, None, context, hold=None, waited=0.0, disabled=True)
    started = job.usage_now()
    paused = False
    while True:
        job._settle_usage(runtime_root)
        now = job.usage_now()
        admission = usage.admit(
            runtime_root, provider=provider, role=args.role, model=args.usage_model,
            repository=context["repository"], run_id=context["run_id"], limits=limits, now=now,
            readings=_usage_fresh_readings(provider), reserve=args.reserve)
        hold = admission.hold
        if hold is None:
            if paused:
                _usage_manual_event(runtime_root, "usage_resumed", provider, context, token=admission.token)
            return _usage_gate_output(args, provider, admission, context, hold=None,
                                      waited=job.usage_now() - started)
        waited = now - started
        if not (args.wait and hold.waitable and hold.resume_at is not None):
            _usage_manual_event(runtime_root, "usage_paused", provider, context, hold=hold)
            return _usage_gate_output(args, provider, admission, context, hold=hold, waited=waited)
        until = hold.resume_at + limits.resume_grace_seconds
        if waited + max(0.0, until - now) > limits.max_wait_seconds:
            _usage_manual_event(runtime_root, "usage_paused", provider, context, hold=hold)
            return _usage_gate_output(args, provider, admission, context, hold=hold, waited=waited,
                                      max_wait_seconds=limits.max_wait_seconds)
        _usage_manual_event(runtime_root, "usage_paused", provider, context, hold=hold)
        paused = True
        _usage_print_stderr(f"workflow-controller: usage: held ({hold.reason}); waiting until {_local_time(until)}")
        try:
            while (remaining := until - job.usage_now()) > 0:
                _usage_sleep(min(remaining, USAGE_WAIT_SLICE_SECONDS))
        except KeyboardInterrupt:
            raise UsageWaitInterrupted("usage_paused", _local_time(until)) from None


def _usage_renew(args: argparse.Namespace, runtime_root: Path, values: dict) -> int:
    """``usage --renew TOKEN``: extend a live reservation, revive a lapsed
    one; an unknown or already accounted token is exit 1 and changes
    nothing (D6)."""
    limits = usage.limits_from_values(values, usage.PROVIDER_CLAUDE)
    result = usage.renew(runtime_root, args.renew, reservation_seconds=limits.reservation_seconds,
                         now=job.usage_now())
    if result == usage.UNKNOWN:
        message = (f"the token {args.renew} is unknown or already accounted; nothing was renewed, and "
                   "`usage --release` cannot account an unknown manual token or further work behind an "
                   "already-settled token")
        if _usage_json(args):
            print(json.dumps({"token": args.renew, "result": result, "message": message}, sort_keys=True))
        else:
            print(f"error: {message}", file=sys.stderr)
        return EXIT_TOKEN_UNKNOWN
    if _usage_json(args):
        print(json.dumps({"token": args.renew, "result": result}, sort_keys=True))
    else:
        print(result)
    return EXIT_OK


def _usage_release(args: argparse.Namespace, runtime_root: Path, values: dict) -> int:
    """``usage --release TOKEN [--outcome ok|not_started] [--stream PATH]``
    (D8): account the manual worker's delta, spend and ledger entry once
    (``usage.complete``) and append ``usage_released``. The end readings are
    the worker's stream (``--stream``, first event as the start comparison,
    last as the end) or the readings stored now; a Claude release with none
    newer is an ``unknown`` delta charged at least the forecast. Idempotent:
    an already accounted token changes nothing and exits 0; an unknown one
    exits 1."""
    now = job.usage_now()
    if args.stream is not None and not Path(args.stream).is_file():
        print(f"error: --stream {args.stream} is not a readable file; nothing was accounted", file=sys.stderr)
        return EXIT_USAGE
    before = usage.snapshot(runtime_root, now=now)
    held = before["reservations"].get(args.release) or before["lapsed"].get(args.release)
    provider = None if held is None else held["provider"]
    if args.stream is not None and provider not in (None, usage.PROVIDER_CLAUDE):
        print(f"error: --stream reads a Claude stream and the token {args.release} is a {provider} reservation; "
              f"nothing was accounted", file=sys.stderr)
        return EXIT_USAGE
    stream_start = None
    if args.outcome == usage.OUTCOME_NOT_STARTED:
        end = None
    elif args.stream is not None:
        first, last = usage.read_claude_stream_span(Path(args.stream))
        stream_start, end = list(first.values()), list(last.values())
    else:
        fresh = _usage_fresh_readings(provider) if provider is not None else []
        if fresh:
            usage.record_reading(runtime_root, fresh)
        end = list(usage.current_readings(usage.snapshot(runtime_root, now=now), provider).values()) \
            if provider is not None else None
    result = usage.complete(runtime_root, args.release, end, args.outcome, now=now, stream_start=stream_start)
    if result.status == usage.COMPLETE_NOOP:
        known = result.reason == "already_settled"
        message = ("already accounted; nothing changed" if known else
                   f"the token {args.release} is unknown; nothing was accounted")
        if _usage_json(args):
            print(json.dumps({"token": args.release, "status": result.status, "reason": result.reason,
                              "message": message}, sort_keys=True))
        else:
            print(message if known else f"error: {message}", file=sys.stderr if not known else sys.stdout)
        return EXIT_OK if known else EXIT_TOKEN_UNKNOWN
    entry = result.entry
    context = {"repository": held.get("repository") if held else None, "run_id": held.get("run_id") if held else None}
    fields = _usage_event_fields(provider=provider, repository=context["repository"], run_id=context["run_id"],
                                 token=args.release)
    fields["reason"] = result.status if entry is None else entry["delta"]
    # `percent` is the charge, `forecast` what the reservation held, `reason`
    # the delta kind (or the release outcome when nothing was charged).
    fields["percent"] = None if entry is None else entry["charged"]
    fields["forecast"] = None if entry is None else entry["reserved_five_hour"]
    usage.append_event(runtime_root, "usage_released", now=job.usage_now(), **fields)
    if _usage_json(args):
        print(json.dumps({"token": args.release, "status": result.status, "entry": entry}, sort_keys=True))
    elif entry is None:
        print("released; nothing charged")
    else:
        print(f"released; {entry['delta']} delta, charged {entry['charged']:.1f}%")
    return EXIT_OK


def cmd_usage(args: argparse.Namespace) -> int:
    """``usage`` (workflow-controller-usage-budget CP4, D8): the budget's
    view, and the gate manual workers (lane scripts, a Codex session) use to
    share the Controller's forecast-and-reservation accounting. Dispatched
    before pinning like ``follow``: it writes only the shared usage record
    and ``usage-events.jsonl`` in the runtime root, apart from the replay
    sweep (``job._settle_usage``) its view and gate run first, which marks a
    terminal job record a crash left unaccounted ``accounting: "done"``."""
    runtime_root, row = _follow_runtime_root_row(args)
    runtime.ensure_runtime_root(runtime_root, ladder_row=row)
    values = _effective(args).values
    if args.renew is not None:
        return _usage_renew(args, runtime_root, values)
    if args.release is not None:
        return _usage_release(args, runtime_root, values)
    if args.check or args.wait:
        return _usage_check_or_wait(args, runtime_root, values)
    return _usage_show(args, runtime_root, values)


def _run_one_step(
    args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
    target: managed_repo.ManagedRepository, routing_options: routing.RoutingOptions = routing.NO_OVERRIDES,
    *, wait: bool = False, usage_pause: _UsagePause | None = None,
) -> tuple[int, dict | Decision | job.UsageHold | None]:
    """One orchestration boundary: a generation-handoff check, then (at
    most) one job execution. Shared by ``step`` (one call) and ``run``'s
    own loop body (repeated until a stop condition) -- both `step`'s "stop
    after exactly one action" and `run`'s own boundary discipline (CP8:
    handoff detection runs "after a job finishes, before the next one
    starts, never mid-job") are this same check, run once per call.

    Returns ``(exit_code, result)``, where ``result`` is ``None`` for a
    handoff, a :class:`~controller.decision.Decision` for the no-action
    class, or a ``JobRecord`` otherwise.

    With a run open (CP5), a detected handoff and a no-action decision are
    recorded in its log, and the job it launches carries its ``run_id``.

    ``wait`` (auto-merge-release-wait I6): ``run`` passes ``True``, so the
    step may wait up to ``merge.wait_seconds`` on a pending merge gate;
    ``step`` never waits.

    The usage gate (usage-budget CP3, D7) admits the job from the settings;
    a hold is ``(EXIT_USAGE_PAUSED, hold)`` with nothing recorded, and the
    caller decides whether to wait (``usage_pause`` is ``run``'s state)."""
    run = _open_run
    origin_source_root = ident.origin_source_root or ident.source_root
    pending = handoff.detect(ident, origin_source_root)
    if pending is not None:
        jobs_complete, jobs_open = _classify_jobs(runtime_root)
        handoff.write_handoff_record(
            runtime_root, pending,
            jobs_complete=jobs_complete, jobs_open=jobs_open,
            next_generation_command=_next_generation_command(runtime_root, args.repo),
            now=_now(),
        )
        if run is not None:
            run.event("handoff_detected")
        return EXIT_HANDOFF_PENDING, None

    effective = _effective(args)
    result = job.execute_step(
        target,
        work_item_id=args.work_item,
        identity=ident,
        runtime=runtime_root,
        permission_mode=args.permission_mode or job.DEFAULT_PERMISSION_MODE,
        timeout=effective["worker.timeout_seconds"],
        claude_bin=args.claude_binary,
        routing=routing_options,
        run_id=None if run is None else run.run_id,
        drain_detach_seconds=effective["worker.drain_detach_seconds"],
        controller_settings=_controller_settings_block(effective),
        auto_merge=effective["merge.auto"],
        wait_seconds=effective["merge.wait_seconds"] if wait else 0,
        poll_seconds=effective["merge.poll_seconds"],
        on_wait=None if run is None else (lambda code, deadline: run.event("waiting", gate=code, deadline=deadline)),
        usage_gate=_usage_gate(runtime_root, target, run, effective, usage_pause),
    )

    if isinstance(result, job.UsageHold):
        # Usage budget (D7): refused before any record existed.
        return EXIT_USAGE_PAUSED, result

    if isinstance(result, Decision):
        # LEGACY_READY / MILESTONE_COMPLETE, or a close-out after a release
        # wait: nothing ran, nothing is pending.
        if run is not None:
            released = ({"release": result.evidence[0]} if result.reason == job.REASON_CLOSED_OUT_RELEASED
                        else {})
            run.event("no_action", observed_phase=phase_to_wire(result.observed_phase), reason=result.reason,
                      **released)
        return EXIT_OK, result

    # `execute_step`'s own `status` field, mapped to the exit-code table
    # (`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`, "Exit codes"). `FINISHED`
    # maps to `EXIT_OK` here too -- the distinction between "finished, keep
    # going" (`run`) and "finished, stop" (`step`) is the caller's own loop
    # structure, never a different exit code for the same status.
    status_exit_codes = {
        job.STATUS_GATE_BLOCKED: EXIT_GATE,
        job.STATUS_DECLINED: EXIT_DECLINED,
        job.STATUS_HANDOFF_PENDING: EXIT_HANDOFF_PENDING,
        job.STATUS_FAILED: EXIT_WORKER_FAILED,
        job.STATUS_INCOMPLETE: EXIT_INCOMPLETE,
        job.STATUS_FINISHED: EXIT_OK,
    }
    return status_exit_codes[result["status"]], result


def cmd_step(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``step``: execute exactly one automatic action, validate the
    transition, stop (capabilities 5, 7, 8)."""
    require_pinned_execution()
    routing_options = _routing_options(args)
    target = _inspect_target(args)
    run = _start_run("step", runtime_root, ident, target, max_steps=None)
    _start_follower(args, runtime_root, run.run_id)
    run.event("step_started", n=1)
    try:
        exit_code, result = _run_one_step(args, runtime_root, ident, target, routing_options)
        if isinstance(result, job.UsageHold):
            # `step` never waits (D7): the event and the exit-17 line.
            _usage_paused(runtime_root, run, target, result)
            print(usage_hold_message(result), file=sys.stderr)
    finally:
        # Every finished child the launch recorded is collected before the
        # command returns (the between-launch reaper keeps collecting the
        # ones still running).
        worker.reap_adopted_children()
    return exit_code


def _start_run(command: str, runtime_root: Path, ident: identity.ControllerIdentity,
               target: managed_repo.ManagedRepository, *, max_steps: int | None) -> job.RunRecord:
    """Create this invocation's run record and register it in
    :data:`_open_run` for :func:`main` to close. ``command`` is the
    subcommand name, never argv; ``target_repo`` is the canonical root job
    records carry."""
    global _open_run
    if _open_run is not None:
        _open_run.discard()  # never closed (a direct call, not through `main`)
    _open_run = job.open_run(runtime_root, command=command, target_repo=str(target.root),
                             max_steps=max_steps, ident=ident)
    return _open_run


def _classify_jobs(runtime_root: Path) -> tuple[list[str], list[str]]:
    """The job ids under ``<runtime_root>/jobs/*.json``, partitioned by
    ``controller.job``'s own closed terminal/non-terminal status
    enumeration -- ``handoff.write_handoff_record``'s
    ``jobs_complete``/``jobs_open`` lists. In the ordinary case this runs
    at CP6's orchestration boundary, strictly between jobs, so
    ``jobs_open`` is normally empty; it is computed properly regardless,
    rather than assumed empty, so a record left non-terminal by an
    out-of-band crash is still reported rather than silently dropped."""
    jobs_dir = runtime_root / "jobs"
    complete: list[str] = []
    open_: list[str] = []
    if jobs_dir.is_dir():
        for path in sorted(jobs_dir.glob("*.json")):
            record = runtime.read_json(path) or {}
            status = record.get("status")
            if status in job.TERMINAL_STATUSES:
                complete.append(path.stem)
            else:
                open_.append(path.stem)
    return complete, open_


def _next_generation_command(runtime_root: Path, repo: str) -> str:
    """The exact command to start the next generation: the same runtime
    root (so the new generation's own run picks up this one's durable job
    history) and the same target repository."""
    return f"workflow-controller --runtime-dir {runtime_root} run {repo}"


def _await_pause_file(pause_file: str | None) -> None:
    """Block at the orchestration boundary while ``pause_file`` exists --
    test-support surface only, inert unless ``WORKFLOW_CONTROLLER_TEST_HOOKS``
    is set to exactly ``"1"``."""
    if not pause_file or os.environ.get(TEST_HOOKS_ENV) != "1":
        return
    pause_path = Path(pause_file)
    while pause_path.exists():
        time.sleep(_PAUSE_POLL_SECONDS)


def cmd_run(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    require_pinned_execution()

    if args.pause_file and os.environ.get(TEST_HOOKS_ENV) != "1":
        print(
            "--pause-file is test-support surface, inert unless "
            f"{TEST_HOOKS_ENV}=1 is also set",
            file=sys.stderr,
        )

    routing_options = _routing_options(args)
    target = _inspect_target(args)
    run = _start_run("run", runtime_root, ident, target, max_steps=_effective(args)["run.max_steps"])
    _start_follower(args, runtime_root, run.run_id)

    try:
        return _run_steps(args, runtime_root, ident, target, routing_options, run)
    finally:
        worker.reap_adopted_children()


def _run_steps(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
               target: managed_repo.ManagedRepository, routing_options: routing.RoutingOptions,
               run: job.RunRecord) -> int:
    """``cmd_run``'s loop: one step per orchestration boundary, until a
    step does not finish or ``run.max_steps`` (``--max-steps``, else the
    settings file, else 20) is reached."""
    max_steps = _effective(args)["run.max_steps"]
    steps_run = 0
    usage_pause = _UsagePause()
    while steps_run < max_steps:
        # The orchestration boundary: checked once before every job this
        # loop starts, including the first -- never mid-job. Both halves
        # (the test-support pause and the real handoff detection, inside
        # `_run_one_step`) are checked here, together, per the plan's own
        # "the run loop checks for the file only at the same orchestration
        # boundary where detect() runs".
        _await_pause_file(args.pause_file)

        run.event("step_started", n=steps_run + 1)
        exit_code, result = _run_one_step(args, runtime_root, ident, target, routing_options, wait=True,
                                          usage_pause=usage_pause)
        if isinstance(result, job.UsageHold):
            # Usage budget (D7): a held attempt consumes no step. After the
            # wait the loop re-enters the whole boundary -- the pause file,
            # handoff detection, the lock, fresh state, a fresh decision and
            # a fresh admission; a cap or a longer wait stops with 17.
            if _usage_wait(args, runtime_root, run, target, result, usage_pause):
                continue
            return exit_code
        steps_run += 1
        usage_pause.waited = 0.0
        worker.reap_adopted_children()

        if isinstance(result, dict) and result["status"] == job.STATUS_FINISHED:
            # This checkpoint/action is done -- loop back to the
            # orchestration boundary and decide the next one.
            continue

        # Every other outcome stops the loop: the no-action class
        # (`result` is a `Decision`, exit 0), a handoff (`result` is
        # `None`, exit 50), or one of `GATE_BLOCKED`/`DECLINED`/
        # `HANDOFF_PENDING`/`FAILED`/`INCOMPLETE` (a `dict`, its own exit
        # code -- `HANDOFF_PENDING` here is a `handoff.json` this loop did
        # not itself write, e.g. left over from a prior invocation, since
        # `_run_one_step`'s own handoff check already caught the ordinary
        # case first).
        return exit_code

    return EXIT_MAX_STEPS


def _abandon_summary(record: dict, runtime_root: Path) -> str:
    evidence_block = record.get("reconciliation_evidence") or {}
    job_id = record.get("job_id", "<unknown>")
    if "original" in evidence_block:
        return (
            f"{job_id}: {record.get('status')} ({evidence_block.get('code')}) -- the file was not a job "
            f"record this Controller can read, so its target could not be read and it was abandoned for "
            f"every target; its original bytes are at {runtime_root / evidence_block['original']}"
        )
    line = (
        f"{job_id}: {record.get('status')} ({evidence_block.get('code')}; was "
        f"{evidence_block.get('abandoned_status')}; validity={evidence_block.get('validity')})"
    )
    liveness = record.get("worker_liveness")
    if isinstance(liveness, dict):
        acknowledged = " (acknowledged)" if liveness.get("acknowledged") else ""
        line += f"; recorded worker liveness: {liveness.get('verdict')}{acknowledged}"
    return line


def cmd_resume(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``resume``: reconcile non-terminal job records, then report
    (capability 7). Never launches a worker -- ``controller.job.resume``
    contains no call to ``controller.worker.launch`` at all.

    ``resume --abandon JOB_ID [--acknowledge-unverifiable-worker]`` (CP5)
    instead marks that one pending job file terminal
    (:func:`controller.job.abandon`) and reconciles nothing else. Exit 45
    when ``resume`` left a record alone because its recorded worker is
    ``active`` or ``unverifiable``, or (worker-lifecycle-ownership CP5) a
    process its job owns is still running; also when another Controller is
    attached to a job (its supervisor lock is held), or a re-attached drain
    detached again (``OwnedWorkDetachedError``)."""
    require_pinned_execution()
    abandon_job_id = getattr(args, "abandon", None)
    # Orchestration-protocol-v1 E.2: only a plain `resume` tolerates a drifted
    # installation (and then only to end a protocol job whose managed scripts
    # changed); `--abandon` keeps the strict check.
    target = (_inspect_target(args) if abandon_job_id is not None
              else managed_repo.inspect_for_resume(args.repo, manager_bin=args.workflow_manager))
    if abandon_job_id is not None:
        abandoned = job.abandon(
            target, identity=ident, runtime=runtime_root, job_id=abandon_job_id,
            acknowledge_unverifiable_worker=getattr(args, "acknowledge_unverifiable_worker", False),
        )
        if args.json:
            print(json.dumps(abandoned))
        else:
            print(_abandon_summary(abandoned, runtime_root))
        return EXIT_OK

    records = job.resume(target, identity=ident, runtime=runtime_root,
                         drain_detach_seconds=_effective(args)["worker.drain_detach_seconds"])

    if args.json:
        # `O1` (MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 2): `job.py`'s
        # own docstring for `resume` states `reconciled_this_call` is "an
        # in-memory-only marker ... never part of the persisted job-record
        # schema and never read back on a later invocation" -- `--json`
        # must report the same durable shape `jobs/<job_id>.json` holds,
        # not a superset of it, so the ephemeral marker is stripped from a
        # copy used for serialisation only; the exit-code computation below
        # still reads `reconciled_this_call` off the original `records`
        # list, unaffected. `resume_marked` (a real field `resume` may add
        # to a *terminal* record) is left alone -- only
        # `reconciled_this_call` is stripped, per this finding's fix.
        json_records = [
            {k: v for k, v in record.items() if k != "reconciled_this_call"}
            for record in records
        ]
        print(json.dumps(json_records))
    else:
        if not records:
            print(f"no job records for {target.root}")
        for record in records:
            job_id = record.get("job_id", "<unknown>")
            status = record.get("status", "<unknown>")
            marked = record.get("resume_marked")
            if marked is not None:
                print(f"{job_id}: {status} (resume_marked={marked['outcome']}: {marked['reason']})")
            else:
                print(f"{job_id}: {status}")

    # Exit 40 means "the Controller itself was interrupted, or `resume`
    # reconciled a record to `INTERRUPTED`" (CONTROLLER_GEN1_PLAN.md's exit
    # code table) -- the *event*, not the mere presence of INTERRUPTED
    # status anywhere in job history. `job.resume` marks every record it
    # actually reconciled this call with `reconciled_this_call: True`; a
    # record returned unchanged because it was *already* terminal
    # (`status in TERMINAL_STATUSES`, including a historical INTERRUPTED
    # from an earlier invocation) never carries that marker, so it cannot
    # by itself trigger exit 40 here (fixes `I2`,
    # MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 1: this previously fired
    # on any historical terminal INTERRUPTED record, forever, regardless of
    # whether this invocation reconciled anything).
    any_interrupted = any(
        record.get("status") == job.STATUS_INTERRUPTED and record.get("reconciled_this_call")
        for record in records
    )
    # CP5: a record left `LAUNCHED` because its recorded worker is `active`
    # or `unverifiable` -- nothing about it was reconciled, and a worker may
    # still hold the worktree -- takes precedence over exit 40.
    any_worker_held = any(
        (record.get("resume_marked") or {}).get("outcome") in job.RESUME_HELD_OUTCOMES
        for record in records
    )
    if any_worker_held:
        print(f"follow it: {job.follow_command(runtime_root, target.root)}", file=sys.stderr)
        return EXIT_WORKER_ACTIVE
    return EXIT_INTERRUPTED if any_interrupted else EXIT_OK


def cmd_milestone_binding(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``milestone-binding --new-pr``/``--abandon`` (trunk-branch-pr-release-
    orchestration CP8, TBR-R4-001): acknowledge a binding record of
    ``--work-item`` in a refusal state (or, for ``--abandon``, a plan-stage
    one), under the lifecycle lock. Exit 0, or 20 when a check refuses and
    nothing is written. Never launches a worker, writes a job record, or
    touches a ref or a pull request."""
    require_pinned_execution()
    target = _inspect_target(args)
    disposition = milestone_branch.NEW_PR if args.new_pr else milestone_branch.ABANDON
    record = job.acknowledge_milestone_binding(target, runtime=runtime_root, work_item_id=args.work_item,
                                               disposition=disposition)
    if args.json:
        print(json.dumps(record))
    else:
        print(f"milestone-binding --{disposition}: the {record['branch']} binding of {record['work_item_id']} "
              f"is now {record['state']}")
    return EXIT_OK


_DISPATCH = {
    "inspect": cmd_inspect,
    "explain": cmd_explain,
    "step": cmd_step,
    "run": cmd_run,
    "resume": cmd_resume,
    "milestone-binding": cmd_milestone_binding,
    # "status" is dispatched specially: it needs the pre-existing-state
    # snapshot captured before this run's own identity.json write.
}


def _capture_pre_existing_state(runtime_root: Path) -> dict:
    had_any = runtime_root.is_dir() and any(runtime_root.iterdir())
    prior_identity = runtime.read_json(runtime_root / "identity.json")
    handoff = runtime.read_json(runtime_root / "handoff.json")
    jobs_dir = runtime_root / "jobs"
    job_ids = [p.stem for p in jobs_dir.glob("*.json")] if jobs_dir.is_dir() else []
    return {
        "had_any_state": had_any,
        "identity": prior_identity,
        "handoff": handoff,
        "job_ids": job_ids,
    }


def _settings_cli_overrides(args: argparse.Namespace) -> tuple[dict, routing.RoutingConfig | None]:
    """The settings the options given on this command line override (I1):
    ``--timeout``, ``run --max-steps``, ``resume --drain-timeout``,
    ``--usage-cap``, ``usage --usage-codex-cap`` and ``--routing-config``,
    the latter parsed strictly as today. Read with ``getattr`` defaults: each is only on its own subcommand."""
    config_path = getattr(args, "routing_config", None)
    cli_routing = None if config_path is None else routing.load_routing_config(config_path)
    return {
        "worker.timeout_seconds": getattr(args, "timeout", None),
        "run.max_steps": getattr(args, "max_steps", None),
        "worker.drain_detach_seconds": getattr(args, "drain_timeout", None),
        "usage.run_cap_percent": getattr(args, "usage_cap", None),
        "usage.codex_run_cap_percent": getattr(args, "usage_codex_cap", None),
    }, cli_routing


def _resolve_settings(args: argparse.Namespace, *, write: bool | None) -> settings.EffectiveSettings:
    """The settings in force for this invocation (settings-and-telemetry
    CP2, Design B): the file filled first by a writing command, only loaded
    by a read-only one (A.4), an invalid one refused (exit 20, I2), and the
    command line's overrides applied (I1). ``write`` ``None`` reads no file
    at all: the built-in defaults under the overrides."""
    cli_values, cli_routing = _settings_cli_overrides(args)
    path = settings.resolve_path(getattr(args, "settings", None))
    if write is None:
        loaded = settings.empty(path)
    else:
        loaded = settings.fill(path) if write else settings.load(path)
    return settings.resolve(loaded, cli=cli_values, cli_routing=cli_routing)


def _apply_settings(args: argparse.Namespace, *, write: bool) -> settings.EffectiveSettings:
    """:func:`main`'s one settings resolution, after the re-exec and before
    dispatch: the resolved settings are put on ``args`` for the commands
    (``args.effective_settings``), and the process-wide leaf values are set
    -- the only production call of :func:`settings.apply_process_defaults`,
    made before any thread starts (the ``--follow`` renderer starts in
    dispatch)."""
    effective = _resolve_settings(args, write=write)
    settings.apply_process_defaults(effective)
    args.effective_settings = effective
    return effective


def _effective(args: argparse.Namespace) -> settings.EffectiveSettings:
    """The settings :func:`_apply_settings` put on ``args``. A namespace
    that did not come through :func:`main` (a direct call) is no
    invocation: it gets the built-in defaults under its own options, and no
    file is read."""
    effective = getattr(args, "effective_settings", None)
    return effective if effective is not None else _resolve_settings(args, write=None)


def _controller_settings_block(effective: settings.EffectiveSettings) -> dict:
    """The job record's optional ``controller_settings`` block (CP2): the
    file's path and the SHA-256 of the bytes read (``null`` with no file),
    and each effective value with its source."""
    return {"path": str(effective.path), "sha256": effective.sha256,
            "values": dict(effective.values), "sources": dict(effective.sources)}


def cmd_settings(args: argparse.Namespace) -> int:
    """``settings show|path|clean`` (settings-and-telemetry CP1, A.6).

    Dispatched before pinning and the runtime root, like ``follow``: it
    touches only the settings file. ``show`` and ``path`` write nothing;
    ``clean`` fills the file, then removes the keys this release does not
    know, or refuses (exit 20) a file last filled by a newer release."""
    path = settings.resolve_path(args.settings)
    if args.settings_action == "path":
        print(json.dumps({"path": str(path)}) if args.json else path)
        return EXIT_OK
    if args.settings_action == "clean":
        _loaded, removed = settings.clean(path)
        if args.json:
            print(json.dumps({"path": str(path), "removed": removed}, sort_keys=True))
        elif removed:
            print(f"removed from {path}: {', '.join(removed)}")
        else:
            print(f"nothing to remove from {path}")
        return EXIT_OK
    cli_values, cli_routing = _settings_cli_overrides(args)
    loaded = settings.load(path)
    effective = settings.resolve(loaded, cli=cli_values, cli_routing=cli_routing)
    if args.json:
        print(json.dumps({
            "path": str(path), "exists": loaded.exists, "sha256": loaded.sha256,
            "values": dict(effective.values), "sources": dict(effective.sources),
        }, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"settings file: {path}" + ("" if loaded.exists else " (not created yet)"))
    for key in effective.values:
        print(f"{key} = {json.dumps(effective.values[key], sort_keys=True)} ({effective.sources[key]})")
    return EXIT_OK


def _dispatch(args: argparse.Namespace, argv: list[str]) -> int:
    command = args.command
    if command == "follow":
        # Before pinning, runtime-root creation, materialisation and the
        # identity write: `follow` only reads, the settings file included.
        _apply_settings(args, write=False)
        return cmd_follow(args)
    if command == "settings":
        return cmd_settings(args)
    if command == "telemetry":
        # Read-only like `follow`: the settings file is only validated.
        _apply_settings(args, write=False)
        return cmd_telemetry(args)
    if command == "usage":
        # Read-only like `follow` apart from the shared usage record (and
        # the replay sweep's `accounting` mark on a job record a crash left
        # unaccounted): the settings file is only validated and the source is
        # not re-execed.
        _apply_settings(args, write=False)
        return cmd_usage(args)
    ident = identity.pin()

    handoff = identity.read_exec_handoff()
    exec_depth = handoff.get("exec_depth", 0) if handoff else 0
    os.environ.pop(identity.EXEC_HANDOFF_ENV, None)

    origin = ident.origin_source_root or ident.source_root
    runtime_root, ladder_row = runtime.resolve_runtime_root(
        runtime_dir=args.runtime_dir, origin_source_root=origin, runtime_kind=ident.runtime_kind,
    )

    read_only = command in READ_ONLY_COMMANDS
    runtime.ensure_runtime_root(runtime_root, ladder_row=ladder_row)

    if ident.source_kind == identity.SOURCE_KIND_UNPINNED and not read_only:
        if exec_depth >= 1:
            raise SourceSnapshotError(
                "already re-execed once from an unpinned source and reached the unpinned "
                "branch again -- refusing a second exec rather than looping",
                evidence={"raised_by": "cli.main", "exec_depth": exec_depth},
            )
        # An `unidentified` runtime refuses here (exit 20): `materialise`
        # dispatches on the kind this process resolved.
        snapshot_dir = identity.materialise(
            ident.source_root, runtime_root, allow_dirty=args.allow_dirty_source,
            resolution=ident.resolution(),
        )
        # The handoff must carry the *snapshot's own* source_kind/source_commit
        # (as materialise() actually determined and published), never the
        # parent's own "unpinned" identity fields -- those are always
        # source_kind="unpinned" and would disagree with a "worktree" snapshot,
        # tripping pin()'s own cross-check in the child on every dirty run.
        snapshot_pin = runtime.read_json(snapshot_dir / "SOURCE_PIN.json")
        _reexec(
            snapshot_dir=snapshot_dir, runtime_root=runtime_root, argv=argv,
            exec_depth_seen=exec_depth, source_kind=snapshot_pin["source_kind"],
            source_commit=snapshot_pin.get("source_commit"), settings_path=getattr(args, "settings", None),
        )
        raise AssertionError("os.execve returned, which should be impossible")

    pre_existing = _capture_pre_existing_state(runtime_root)
    pre_existing["ladder_row"] = ladder_row

    _write_identity_record(runtime_root, ident, exec_depth=exec_depth)

    # Settings-and-telemetry CP2: resolved once, after the re-exec (the
    # unpinned branch above never returns) and before anything is
    # dispatched -- a refusal (exit 20) leaves the ordinary dispatch
    # footprint, `identity.json` and no job record. A writing command fills
    # the file first (A.4).
    _apply_settings(args, write=not read_only)

    if command == "status":
        return cmd_status(args, runtime_root, ident, pre_existing=pre_existing)

    if not read_only:
        require_pinned_execution()

    return _DISPATCH[command](args, runtime_root, ident)


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if args.command == "resume" and args.acknowledge_unverifiable_worker and args.abandon is None:
        parser.error("--acknowledge-unverifiable-worker is accepted only with --abandon JOB_ID")
    if args.command == "milestone-binding" and args.work_item is None:
        parser.error("milestone-binding requires --work-item <id>")
    if args.command == "usage":
        _validate_usage_args(parser, args)
    global _open_run
    if _open_run is not None:
        _open_run.discard()  # only a run this invocation creates is closed below
    _open_run = None
    _stop_follower(join=False)
    exit_code: int | None = None
    interrupted = False
    try:
        try:
            exit_code = _dispatch(args, raw_argv)
        except LifecycleWorkerActiveError as exc:
            # CP5: its own clause, *before* the blanket handler below, so a
            # held worktree exits 45, never 20. `LifecycleLockError` is a
            # sibling, not a subclass, and reaches the blanket clause.
            print(f"error: {exc.message}", file=sys.stderr)
            exit_code = EXIT_WORKER_ACTIVE
        except ControllerError as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            exit_code = EXIT_FAIL_CLOSED
        return exit_code
    except milestone_branch.WaitInterrupted as exc:
        # Ctrl-C in a `run`'s wait for checks, the merge or the release: the
        # run record reads `interrupted` as for any Ctrl-C; one line, not a
        # traceback (the exit status is the shell's 128 + SIGINT).
        interrupted = True
        print(f"error: {exc.message()}", file=sys.stderr)
        return SIGINT_EXIT_STATUS
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        _close_open_run(exit_code, interrupted=interrupted)
        _stop_follower(join=not interrupted)


def _close_open_run(exit_code: int | None, *, interrupted: bool) -> None:
    """The one closing site for the run ``cmd_step``/``cmd_run``
    registered (release-runtime-observability CP5): ``ended`` with the
    code :func:`main` computed, or ``interrupted`` on ``KeyboardInterrupt``
    (``exit_code`` ``null``, ``current_job_id`` kept). Any other exception
    leaves the record ``running``, as a killed Controller would. Closing is
    best-effort and never raises into the exit path."""
    global _open_run
    run, _open_run = _open_run, None
    if run is None:
        return
    if interrupted:
        run.interrupt()
    elif exit_code is not None:
        run.close(exit_code)
    else:
        run.discard()
