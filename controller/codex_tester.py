"""The Codex tester's run (C10g-3b2a).

Given a :class:`controller.codex_bridge.Bridge`, runs the launch controls and then ``codex exec`` on the tester's
prompt under a deadline, and turns what happened into an outcome (``ran``, ``timeout``, ``limit`` or ``failed``)
with the tester's final message. It is the Codex counterpart of the Claude launch's ``_claude_run``. The
reviewer's launch (``review._codex_attempt``) is bound to the reviewer's layout and verdict file, so it is not
reused; the bounded run, the rollout scan and the controls are. Nothing calls this module yet: the gate, the
before/after comparison of the real repository and the refusal mapping are C10g-3b2b (see
``docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import codex_bridge, codex_sandbox, functional_review, review, runtime, usage

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
        result["subagents"] = review.scan_rollouts(layout.codex_home).subagents

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
                    "subagents": [], "credentials": None, "telemetry": None, "exit_code": None}
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
