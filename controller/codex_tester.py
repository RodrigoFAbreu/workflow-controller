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

from collections.abc import Callable, Sequence
from pathlib import Path

from . import codex_bridge, codex_sandbox, functional_review, review

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


def run_tester(bridge: codex_bridge.Bridge, flows: Sequence[functional_review.Flow], *, binary: Path,
               timeout: float, system_dir: Path = review.SYSTEM_LAYER_DIR,
               on_spawn: Callable[[int, int], None] | None = None,
               run: Callable[..., review.RunResult] | None = None) -> dict:
    """Run the controls, then ``codex exec`` on the tester's prompt (standard input) in the clone, and return the
    outcome. ``run`` is the seam of the controls only; the launch itself always uses :func:`review.run_bounded`."""
    _check_controls(bridge, binary, system_dir, run)
    built = bridge.built
    prompt = codex_bridge.prompt(bridge, flows, timeout_seconds=int(timeout))
    result = review.run_bounded(list(built.argv), cwd=bridge.clone, env=built.environment, timeout=timeout,
                                stdin_text=prompt, on_spawn=on_spawn)
    scan = review.scan_rollouts(built.layout.codex_home)
    message = _message(built.layout)
    outcome, detail = classify(result, scan, message, timeout)
    return {"outcome": outcome, "detail": detail, "tester_message": functional_review.tester_message_excerpt(message),
            "exit_code": result.exit_code}
