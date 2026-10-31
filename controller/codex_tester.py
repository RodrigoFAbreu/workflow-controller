"""The Codex tester's run (C10g-3b2a).

Given a :class:`controller.codex_bridge.Bridge`, runs the launch controls and then ``codex exec`` on the tester's
prompt under a deadline, and turns what happened into an outcome (``ran``, ``timeout``, ``limit`` or ``failed``)
with the tester's final message. It is the Codex counterpart of the Claude launch's ``_claude_run``. The
reviewer's launch (``review._codex_attempt``) is bound to the reviewer's layout and verdict file, so it is not
reused; the bounded run, the rollout scan and the controls are. :func:`launch_codex_functional` (C10g-3b2b)
adds what ``review.launch_functional`` does around its ``_claude_run``: the build gate, the before/after
comparison of the real repository, the binding check and the refusal mapping. Routing accepts the Codex tester
(C10g-3c1) and ``job._run_functional`` calls the launch for a route whose harness is ``codex``; the chain's
fallback is chosen before the spawn by :func:`selection_error` (C10g-3c2; see ``docs/ai-workflow/CONTROLLER_CODEX_TESTER_ROUTING_PLAN.md``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import codex_bridge, codex_build, codex_sandbox, functional_review, review, runtime, usage
from . import harness as harness_registry
from .errors import ControllerError, ReviewLaunchRefusedError

#: The outcomes of a run (D3).
RAN, TIMEOUT, LIMIT, FAILED = "ran", "timeout", "limit", "failed"

#: How much of the output a failure's ``detail`` carries.
DETAIL_CHARS = 400


def _check_controls(bridge: codex_bridge.Bridge, binary: Path, system_dir: Path,
                    run: Callable[..., review.RunResult] | None) -> None:
    """The controls, in order, before every launch (D4). The model and effort are the bridge's own. A refusal
    propagates and nothing has been started by ``exec``."""
    built = bridge.built
    layout = built.layout
    if built.binary and built.binary != str(binary):
        raise review._refuse("the launch binary differs from the one the bridge was built with",
                             check="binary", entry="binary")
    command = codex_sandbox.argv(layout, binary=binary, model=built.model, effort=built.effort)
    if tuple(command) != built.argv:
        raise review._refuse("the recorded command line does not reproduce from the bridge's binary, model and effort",
                             check="argv", entry="argv")
    codex_sandbox.check_argv(built.argv, layout, model=built.model, effort=built.effort)
    codex_sandbox.check_launch(layout, built.config_text, binary, built.environment, system_dir=system_dir, run=run)
    codex_sandbox.check_composed(layout, built.config_text, built.roots, binary, built.environment,
                                 model=built.model, effort=built.effort, run=run)


def _message(layout: codex_sandbox.TesterLayout) -> str:
    """The tester's final message: the ``-o`` file's text; an absent, unreadable or blank file is no message (D2)."""
    try:
        return layout.verdict.read_text("utf-8", "replace").strip()
    except OSError:
        return ""


def _tail(result: review.RunResult) -> str:
    text = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
    return text[-DETAIL_CHARS:]


def classify(result: review.RunResult, scan: review.RolloutScan, message: str, timeout: float) -> tuple[str, str]:
    """``(outcome, detail)`` by precedence (D3): the deadline, Codex's own limit record, a non-zero exit, a zero
    exit with no final message, else ``ran``."""
    if result.timed_out:
        return TIMEOUT, f"the tester did not finish within {timeout:g} seconds"
    if scan.limit_error:
        return LIMIT, scan.limit_detail or "Codex reported a usage limit"
    if result.exit_code != 0:
        return FAILED, _tail(result) or f"codex exec exited {result.exit_code}"
    if not message:
        return FAILED, "codex exec exited 0 but wrote no final message"
    return RAN, ""


def _settle(result: dict, bridge: codex_bridge.Bridge, operator_home: Path) -> None:
    """The steps of every path (D5): the readings, the sub-agent rollouts, the credential copy-back and the removal
    of the credential copy. Each is guarded: one failing step never skips the next and never replaces the exception
    already propagating; with nothing in flight the first failure is raised."""
    built = bridge.built
    layout = built.layout
    failures: list[Exception] = []

    def readings() -> None:
        result["readings"] = usage.read_codex_home(layout.codex_home)

    def subagents() -> None:
        scan = review.scan_rollouts(layout.codex_home)
        result["subagents"] = scan.subagents
        result["usage_rollouts"] = scan.rollouts

    def copy_back() -> None:
        if built.credential_digest:
            result["credentials"] = review.copy_back_credentials(layout.job, operator_home, built.credential_digest,
                                                                 review.CODEX)

    def remove_copy() -> None:
        runtime.remove_tree(layout.runtime_root, layout.rel("codex-home", review.CODEX.credential_file))

    for step in (readings, subagents, copy_back, remove_copy):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 -- recorded; raised below only when nothing else is in flight
            failures.append(exc)
    if failures and sys.exc_info()[0] is None:
        raise failures[0]


def run_tester(bridge: codex_bridge.Bridge, flows: Sequence[functional_review.Flow], *, binary: Path,
               operator_home: Path, timeout: float, system_dir: Path = review.SYSTEM_LAYER_DIR,
               on_spawn: Callable[[int, int], None] | None = None,
               run: Callable[..., review.RunResult] | None = None) -> dict:
    """Run the controls, then ``codex exec`` on the tester's prompt (standard input) in the clone, and return the
    result of the Claude launch's shape (D6). ``run`` is the seam of the controls only; the launch itself always uses
    :func:`review.run_bounded`. The credential copy is copied back and removed on every path, a refusal included."""
    result: dict = {"outcome": FAILED, "detail": "", "tester_message": "", "readings": [], "first_readings": [],
                    "subagents": [], "usage_rollouts": [], "credentials": None, "telemetry": None,
                    "exit_code": None}
    try:
        _check_controls(bridge, binary, system_dir, run)
        built = bridge.built
        prompt = codex_bridge.prompt(bridge, flows, timeout_seconds=int(timeout))
        ran = review.run_bounded(list(built.argv), cwd=bridge.clone, env=built.environment, timeout=timeout,
                                 stdin_text=prompt, on_spawn=on_spawn)
        scan = review.scan_rollouts(built.layout.codex_home)
        message = _message(built.layout)
        outcome, detail = classify(ran, scan, message, timeout)
        result.update(outcome=outcome, detail=detail, tester_message=functional_review.tester_message_excerpt(message),
                      exit_code=ran.exit_code)
    finally:
        _settle(result, bridge, operator_home)
    return result


def _build_breach(runtime_root: Path, binary: Path, admitted: str | None, target: str,
                  system_path: str) -> list[str]:
    """After the run (D4): ``codex build:`` entries when the build is not the one the gate admitted. The fresh digest
    and the launcher's resolved target are compared with the admitted ones, as well as the record's verdict."""
    fresh = codex_build.is_verified(runtime_root, binary, system_path=system_path)
    entries: list[str] = []
    if not fresh.ok:
        entries.append(f"codex build: {fresh.reason}")
    elif fresh.digest != admitted:
        entries.append("codex build: the build's digest changed during the run")
    if os.path.realpath(binary) != target:
        entries.append(f"codex build: the launcher now resolves to {os.path.realpath(binary)}, not {target}")
    return entries


def _origin_breach(repo_root: Path, before: str | None, error: str | None) -> list[str]:
    if error is not None:
        return [f"origin: {error}"]
    try:
        return ["origin refs"] if review._local_origin_refs(repo_root) != before else []
    except OSError as exc:
        return [f"origin: {exc}"]


def selection_error(operator_home: Path, binary_name: str, runtime_root: Path, *,
                    system_path: str = review.SYSTEM_PATH) -> functional_review.TesterUnavailable | None:
    """The reason a Codex entry cannot be chosen before anything is started (C10g-3c2 D2, D3), or ``None``: the
    binary does not resolve (``no_binary``), the operator's Codex home has no ``auth.json`` or the build is not
    verified (``enforcement``). The same calls :func:`launch_codex_functional` makes, which still makes them
    itself, so a change between the selection and the launch ends at the gate as before."""
    try:
        binary = review.resolve_binary(binary_name)
    except ReviewLaunchRefusedError as exc:
        return functional_review.unavailable(functional_review.UNAVAILABLE_NO_BINARY, str(exc))
    login = Path(operator_home) / review.CODEX.credential_file
    if not login.is_file():
        return functional_review.unavailable(
            functional_review.UNAVAILABLE_ENFORCEMENT,
            f"the Codex home has no {review.CODEX.credential_file} ({login}); log in to Codex first")
    admitted = codex_build.is_verified(runtime_root, binary, system_path=system_path)
    if not admitted.ok:
        return functional_review.unavailable(
            functional_review.UNAVAILABLE_ENFORCEMENT,
            f"the Codex build is not verified ({admitted.reason}); run `workflow-controller verify-codex` first")
    return None


def launch_codex_functional(*, candidate: functional_review.Candidate, flows: Sequence[functional_review.Flow],
                            runtime_root: Path, job_id: str, work_item_id: str, operator_home: Path,
                            timeout: float, model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT,
                            binary_name: str = "codex", system_dir: Path = review.SYSTEM_LAYER_DIR,
                            inherited_env: Mapping[str, str] | None = None,
                            on_spawn: Callable[[int, int], None] | None = None, out: dict | None = None,
                            run: Callable[..., review.RunResult] | None = None,
                            system_path: str = review.SYSTEM_PATH, real_home: Path | None = None) -> dict:
    """Run the functional tester as Codex end to end and return what ``review.launch_functional`` returns for the
    Claude tester (D1), plus ``subagents`` and ``exit_code``. It records, validates and decides nothing.

    In order (D2): the role, the binary (symlink kept), the build gate (``verify-codex``'s record must admit the
    build; nothing is written or started before it passes), the kept-sandbox sweep, the runtime-root containment
    check, the snapshots, the bridge, the run, the snapshots again, the binding check and the fake-call log. Every
    refusal is ``functional_tester_unavailable`` and the sandbox is removed on every exception after the bridge was
    built; on a clean run it is left for the caller and ``review.settle_functional``. ``run`` is the seam of the
    launch controls; ``system_path`` and ``real_home`` are the seams of the build digest and the readable roots."""
    result: dict = out if out is not None else {}
    result.update({"outcome": FAILED, "breach": [], "unbound": [], "fake_calls": {}, "output_dir": None,
                   "sandbox": None, "keep": False, "credentials": None, "detail": None, "readings": [],
                   "first_readings": [], "subagents": [], "usage_rollouts": [], "exit_code": None})
    try:
        harness_registry.for_role("codex", harness_registry.FUNCTIONAL_TESTER)
    except ControllerError as exc:
        raise functional_review.unavailable(functional_review.UNAVAILABLE_ROLE_REFUSED, str(exc)) from exc
    try:
        binary = review.resolve_binary(binary_name)
    except ReviewLaunchRefusedError as exc:
        raise functional_review.unavailable(functional_review.UNAVAILABLE_NO_BINARY, str(exc)) from exc
    admitted = codex_build.is_verified(runtime_root, binary, system_path=system_path)
    if not admitted.ok:
        raise functional_review.unavailable(
            functional_review.UNAVAILABLE_ENFORCEMENT,
            f"the Codex build is not verified ({admitted.reason}); run `workflow-controller verify-codex` first")
    target = os.path.realpath(binary)
    functional_review.sweep_kept(runtime_root, work_item_id)
    planned = codex_sandbox.TesterLayout(runtime_root, job_id)
    root_real = Path(runtime_root).resolve()
    for folder in (planned.clone, planned.scratch / "out"):
        if root_real == folder.resolve() or folder.resolve() in root_real.parents:
            raise functional_review.unavailable(
                functional_review.UNAVAILABLE_ENFORCEMENT,
                f"the runtime root {root_real} lies inside the tester's {folder.name} folder, "
                "where the record of verified codex builds could be written by the tester")
    repo_root = candidate.repo_root
    try:
        own = [str(root_real.relative_to(repo_root.resolve()))]
    except ValueError:
        own = []  # the sandbox is not part of the worktree being guarded
    before = review.snapshot_repository(repo_root, exclude=own)
    try:
        origin_before, origin_error = review._local_origin_refs(repo_root), None
    except OSError as exc:
        origin_before, origin_error = None, str(exc)
    try:
        bridge = codex_bridge.build_bridge(candidate, runtime_root=runtime_root, job_id=job_id,
                                           work_item_id=work_item_id, operator_home=operator_home, binary=binary,
                                           model=model, effort=effort, system_dir=system_dir,
                                           inherited=inherited_env, real_home=real_home)
    except ReviewLaunchRefusedError as exc:
        raise functional_review.unavailable(functional_review.UNAVAILABLE_ENFORCEMENT, str(exc)) from exc
    result.update(sandbox=bridge.layout.base, output_dir=bridge.out, head=candidate.head,
                  checklist_blob=candidate.checklist_blob, writable=list(bridge.writable),
                  refused=[{"pattern": item.pattern, "reason": item.reason} for item in bridge.refused])
    try:
        try:
            result.update(run_tester(bridge, flows, binary=binary, operator_home=operator_home, timeout=timeout,
                                     system_dir=system_dir, on_spawn=on_spawn, run=run))
        except ReviewLaunchRefusedError as exc:
            raise functional_review.unavailable(functional_review.UNAVAILABLE_ENFORCEMENT, str(exc)) from exc
        breach = review.compare_snapshots(before, review.snapshot_repository(repo_root, exclude=own))
        breach += _origin_breach(repo_root, origin_before, origin_error)
        breach += _build_breach(runtime_root, binary, admitted.digest, target, system_path)
        result["breach"] = breach
        result["unbound"] = codex_bridge.check_bound(bridge)
        result["fake_calls"] = functional_review.read_fake_calls(bridge.layout.fake_logs)
        result["keep"] = bool(breach or result["unbound"])
    except BaseException:
        try:
            functional_review.remove_sandbox(runtime_root, job_id)
        except Exception:  # noqa: BLE001 -- the original failure is the one to raise
            pass
        raise
    return result
