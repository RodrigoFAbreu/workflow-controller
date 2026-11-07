"""The Codex worker launch (H4b2).

Launches one rendered command as a headless ``codex exec`` in the job's worktree, under the sandbox of
:mod:`controller.codex_worker_sandbox`, and returns the :class:`controller.worker.WorkerResult` the Claude launch
returns. :func:`launch_codex_worker` renders the command, refuses a build ``verify-codex`` has not admitted before it
starts or writes anything, runs the controls, runs ``codex exec`` under a deadline, compares the ``.git`` guard digest,
the nested-repository scan and the build around the run, copies the credential back and removes the copy on every path,
and maps what happened to one of the four outcomes (:func:`classify`). :class:`CodexLauncher` is the
:class:`controller.worker_launch.WorkerLauncher` of it. It is registered for a fixture role only: no setting, option or
gate selects it, ``harness.CODEX`` declares no worker role and ``worker_launch.LAUNCHERS`` does not list it. The ``prompt`` keyword of
:func:`launch_codex_worker` sends a given text on stdin in place of the rendered command (the read-only review run of
:mod:`controller.codex_review_role`, itself routed to no role; see
``docs/ai-workflow/CONTROLLER_CODEX_WORKER_LAUNCH_PLAN.md``).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import codex_build, codex_tester, codex_worker_job, codex_worker_sandbox, command_files, review, runtime, usage, worker
from .errors import ReviewLaunchRefusedError

#: ``subtype`` of a result, by cause. ``sandbox_breach`` is the one the plan fixes (D5).
SUBTYPE_SUCCESS = "success"
SUBTYPE_TIMEOUT = "timeout"
SUBTYPE_SIGNAL = "terminated_by_signal"
SUBTYPE_LIMIT = "usage_limit"
SUBTYPE_EXIT = "non_zero_exit"
SUBTYPE_NO_MESSAGE = "no_final_message"
SUBTYPE_BREACH = "sandbox_breach"

#: How much of the output a failure's ``detail`` carries.
DETAIL_CHARS = 400


def _message(layout: codex_worker_sandbox.WorkerLayout) -> str:
    """The worker's final message: the ``-o`` file's text; an absent, unreadable or blank file is no message."""
    try:
        return layout.last_message.read_text("utf-8", "replace").strip()
    except OSError:
        return ""


def _tail(result: review.RunResult) -> str:
    text = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
    return text[-DETAIL_CHARS:]


def classify(result: review.RunResult, scan: review.RolloutScan, message: str, timeout: float
             ) -> tuple[str, str, str]:
    """``(outcome, subtype, detail)`` by the Claude classifier's precedence (D5): the deadline, then termination by a
    signal (a negative exit code, no timeout), both ``INTERRUPTED``; then Codex's own usage-limit record, then a
    non-zero exit, both ``FAILURE``; then a zero exit with no final message, ``AMBIGUOUS``; else ``SUCCESS``."""
    if result.timed_out:
        return worker.INTERRUPTED, SUBTYPE_TIMEOUT, f"the worker did not finish within {timeout:g} seconds"
    if result.exit_code is not None and result.exit_code < 0:
        return worker.INTERRUPTED, SUBTYPE_SIGNAL, f"codex exec was terminated by signal {-result.exit_code}"
    if scan.limit_error:
        return worker.FAILURE, SUBTYPE_LIMIT, scan.limit_detail or "Codex reported a usage limit"
    if result.exit_code != 0:
        return worker.FAILURE, SUBTYPE_EXIT, _tail(result) or f"codex exec exited {result.exit_code}"
    if not message:
        return worker.AMBIGUOUS, SUBTYPE_NO_MESSAGE, "codex exec exited 0 but wrote no final message"
    return worker.SUCCESS, SUBTYPE_SUCCESS, ""


def _settle(diagnosis: dict, layout: codex_worker_sandbox.WorkerLayout, built: codex_worker_sandbox.Built | None,
            operator_home: Path, *, in_flight: bool) -> review.RolloutScan:
    """The steps of every path (D6): the readings, the sub-agent rollouts, the credential copy-back and the removal of
    the credential copy. With no ``built`` (``build`` raised) nothing is read or copied back and the copy is removed.
    Each step is guarded: one failing step never skips the next and never replaces the exception already
    propagating; with nothing in flight the first failure is raised. ``in_flight`` is passed by the caller, not read
    from ``sys.exc_info()``, which also reports an exception the launch's own caller is handling. Returns the rollout
    scan (empty when none)."""
    failures: list[Exception] = []
    scanned: list[review.RolloutScan] = []

    def readings() -> None:
        diagnosis["readings"] = usage.read_codex_home(layout.codex_home)

    def subagents() -> None:
        scan = review.scan_rollouts(layout.codex_home)
        scanned.append(scan)
        diagnosis["subagents"] = scan.subagents
        diagnosis["usage_rollouts"] = scan.rollouts

    def copy_back() -> None:
        if built is not None and built.credential_digest:
            diagnosis["credentials"] = review.copy_back_credentials(layout.job, operator_home, built.credential_digest,
                                                                    review.CODEX)

    def remove_copy() -> None:
        runtime.remove_tree(layout.runtime_root, layout.rel("codex-home", review.CODEX.credential_file))

    steps = (readings, subagents, copy_back, remove_copy) if built is not None else (remove_copy,)
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 -- recorded; raised below only when nothing else is in flight
            failures.append(exc)
    if failures and not in_flight:
        raise failures[0]
    return scanned[0] if scanned else review.RolloutScan()


def _parse_task(task: str) -> tuple[str, str]:
    """``(name, arguments)`` of a task that is ``/<name>`` with optional arguments; anything else is refused."""
    text = task.strip() if isinstance(task, str) else ""
    if not text.startswith("/") or len(text) < 2:
        raise review._refuse("the Codex worker's task must be a slash command (/<name> [arguments])", check="task",
                             entry=str(task)[:80])
    name, _, arguments = text[1:].partition(" ")
    return name, arguments.strip()


def _check_placement(layout: codex_worker_sandbox.WorkerLayout, cwd: Path, runtime_root: Path) -> None:
    """D4(d): ``cwd`` is the layout's worktree and a clean one, the runtime root lies outside it and the scratch area, and
    the job is fresh (no Codex home from an earlier run, whose rollouts or credential copy would be scanned)."""
    if Path(cwd) != layout.worktree:
        raise review._refuse(f"the working directory {cwd} is not the job's worktree {layout.worktree}", check="cwd",
                             entry=str(cwd))
    codex_worker_sandbox.check_worktree(layout.worktree)
    codex_worker_sandbox.check_nested(layout.worktree)
    root_real = Path(runtime_root).resolve()
    for folder in (layout.worktree, layout.scratch):
        if root_real == folder.resolve() or folder.resolve() in root_real.parents:
            raise review._refuse(f"the runtime root {root_real} lies inside the worker's {folder.name} folder, where "
                                 "the record of verified codex builds could be written by the worker",
                                 check="runtime_root", entry=str(root_real))
    if layout.codex_home.exists():
        raise review._refuse(f"the job {layout.job_id!r} already has a Codex home ({layout.codex_home}): a launch "
                             "takes a fresh job id", check="job", entry="codex-home")


def _breach(layout: codex_worker_sandbox.WorkerLayout, before: Mapping, runtime_root: Path, binary: Path,
            admitted: str | None, target: str, system_path: str) -> list[str]:
    """After the run (D4(j)): one entry per way the worktree or the build can no longer be trusted."""
    entries: list[str] = []
    try:
        codex_worker_sandbox.check_guard(before, codex_worker_sandbox.guard_digest(layout.worktree))
    except ReviewLaunchRefusedError as exc:
        entries.append(f"guard: {exc}")
    except OSError as exc:
        entries.append(f"guard: {exc}")
    try:
        codex_worker_sandbox.check_nested(layout.worktree)
    except ReviewLaunchRefusedError as exc:
        entries.append(f"nested: {exc}")
    except OSError as exc:
        entries.append(f"nested: {exc}")
    entries += codex_tester._build_breach(runtime_root, binary, admitted, target, system_path)
    return entries


def launch_codex_worker(task: str, *, runtime_root: Path, job_id: str, operator_home: Path, cwd: Path, timeout: float,
                        on_spawn: Callable[[int, int], None] | None = None, commands_dir: Path | None = None,
                        model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT,
                        binary_name: str = "codex", system_dir: Path = review.SYSTEM_LAYER_DIR,
                        run: Callable[..., review.RunResult] | None = None, system_path: str = review.SYSTEM_PATH,
                        real_home: Path | None = None, prompt: str | None = None) -> worker.WorkerResult:
    """Launch ``task`` (``/<name> [arguments]``) as a Codex worker in ``cwd`` and return the ``WorkerResult``. With
    ``prompt`` the task is still parsed (a malformed one is refused) but ``prompt`` is sent on stdin in place of the
    rendered command (``workflow-controller-h4e2`` D3); every other control is unchanged.

    In the order of D4: the command is rendered, the binary resolved (symlink kept), the build gate (nothing is written
    or started before it passes), the placement checks, the guard digest, then ``build`` and the controls inside the
    settling scope, the bounded ``exec``, the settling and the comparison. A refusal raises
    ``ReviewLaunchRefusedError`` (or the renderer's error) and no ``codex exec`` has started. ``run`` is the seam of the
    controls only; ``binary_name``, ``system_dir``, ``system_path`` and ``real_home`` are the seams of the binary, the
    outside layers, the build digest and the readable roots."""
    name, arguments = _parse_task(task)
    stdin_prompt = prompt if prompt is not None else command_files.render_command(
        name, arguments, Path(commands_dir) if commands_dir is not None else Path(cwd) / ".claude" / "commands").prompt
    binary = review.resolve_binary(binary_name)
    admitted = codex_build.is_verified(runtime_root, binary, system_path=system_path)
    if not admitted.ok:
        raise review._refuse(f"the Codex build is not verified ({admitted.reason}); run `workflow-controller "
                             "verify-codex` first", check="build", entry="verify-codex")
    target = os.path.realpath(binary)
    layout = codex_worker_sandbox.WorkerLayout(runtime_root, job_id)
    _check_placement(layout, Path(cwd), runtime_root)
    before = codex_worker_sandbox.guard_digest(layout.worktree)

    diagnosis: dict[str, Any] = {"harness": "codex", "readings": [], "subagents": [], "usage_rollouts": [],
                                 "credentials": None, "breach": [], "detail": ""}
    built: codex_worker_sandbox.Built | None = None
    ran: review.RunResult | None = None
    duration_ms = 0
    try:
        built = codex_worker_sandbox.build(runtime_root=runtime_root, job_id=job_id, operator_home=operator_home,
                                           binary=binary, ownership_tag=job_id, model=model, effort=effort,
                                           system_dir=system_dir, real_home=real_home)
        if tuple(codex_worker_sandbox.argv(layout, binary=binary, model=model, effort=effort)) != built.argv:
            raise review._refuse("the recorded command line does not reproduce from the launch's binary, model and "
                                 "effort", check="argv", entry="argv")
        codex_worker_sandbox.check_argv(built.argv, layout, model=model, effort=effort)
        codex_worker_sandbox.check_launch(layout, built.config_text, binary, built.environment, system_dir=system_dir,
                                          run=run)
        codex_worker_sandbox.check_composed(layout, built.config_text, built.roots, binary, built.environment,
                                            model=model, effort=effort, run=run)
        started = time.monotonic()
        ran = review.run_bounded(list(built.argv), cwd=layout.worktree, env=built.environment, timeout=timeout,
                                 stdin_text=stdin_prompt, on_spawn=on_spawn)
        duration_ms = int((time.monotonic() - started) * 1000)
    except BaseException:
        _settle(diagnosis, layout, built, Path(operator_home), in_flight=True)
        raise
    scan = _settle(diagnosis, layout, built, Path(operator_home), in_flight=False)
    assert ran is not None  # a raise above never reaches here
    message = _message(layout)
    outcome, subtype, detail = classify(ran, scan, message, timeout)
    breach = _breach(layout, before, Path(runtime_root), binary, admitted.digest, target, system_path)
    diagnosis.update(subtype=subtype, detail=detail, breach=breach)
    if breach:
        outcome, subtype = worker.FAILURE, SUBTYPE_BREACH
        diagnosis["subtype"] = subtype
    elif subtype == SUBTYPE_LIMIT:
        # H4d (D3): the structured fact only when Codex's own limit record classified the run and the readings show it.
        fact = codex_worker_job.limit_fact(diagnosis.get("readings"))
        if fact is not None:
            diagnosis["usage_limit"] = fact
    return worker.WorkerResult(
        outcome=outcome, returncode=ran.exit_code, session_id=None, is_error=outcome != worker.SUCCESS,
        subtype=subtype, terminal_reason=None, stop_reason=None, result=message, num_turns=None,
        permission_denials=None, total_cost_usd=None, duration_ms=duration_ms,
        stdout=ran.stdout.decode("utf-8", "replace"), stderr=ran.stderr.decode("utf-8", "replace"), raw_json=None,
        stream_diagnosis=diagnosis, owned_processes_seen=None)


class CodexLauncher:
    """The Codex worker's launcher behind the H3 seam: ``launch`` is :func:`launch_codex_worker`. Not registered in
    ``worker_launch.LAUNCHERS``; a fixture role reaches it only in a test."""

    def launch(self, task: str, **options: Any) -> worker.WorkerResult:
        return launch_codex_worker(task, **options)
