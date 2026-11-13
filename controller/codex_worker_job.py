"""The pure record helpers for a Codex worker run (H4c1).

What the job layer needs to record a :class:`controller.worker.WorkerResult` that :func:`controller.codex_worker
.launch_codex_worker` returned: the options of the launch, the text of the job's two stream files and the builder of
the derived ``usage_record``. Each is a function of its arguments; none writes a file, starts a process or imports
:mod:`controller.job`, which keeps every write and the helpers it already has (see
``docs/ai-workflow/CONTROLLER_CODEX_WORKER_JOB_RECORD_PLAN.md``, D3).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from . import usage, usage_record, worker


def launch_options(*, runtime_root: Path, job_id: str, operator_home: Path, cwd: Path, timeout: float | None,
                   on_spawn: Callable[[int, int], None], route: Any,
                   workflow_metadata: bool = False) -> dict[str, Any]:
    """The eight options of the launch (D4) and no other: the Claude-only ones (stream files, binary, inherited
    descriptors, anchor, drain and state callbacks, tool and permission settings) are never passed to the Codex
    launcher, which refuses each of them. ``model`` and ``effort`` are the route's, as routed. A ninth option,
    ``workflow_metadata`` (``workflow-controller-h4f`` D4b), is present only when true, which the job makes it for the
    implementer route alone, so every other route passes exactly the eight."""
    options = {"runtime_root": runtime_root, "job_id": job_id, "operator_home": operator_home, "cwd": cwd,
               "timeout": timeout, "on_spawn": on_spawn, "model": route.model, "effort": route.effort}
    if workflow_metadata:
        options["workflow_metadata"] = True
    return options


def stream_texts(result: worker.WorkerResult) -> tuple[str, str]:
    """``(stdout, stderr)``: the text each of the job's two stream files receives after the run. The launcher already
    decoded both, so the stream file holds exactly what ``codex exec`` said."""
    return result.stdout or "", result.stderr or ""


def usage_builder(result: worker.WorkerResult, route: Any) -> Callable[[], usage_record.UsageRecord]:
    """The zero-argument builder of the ``derived`` usage record (D7): the rollouts the launcher read, the route's model
    and the launcher's wall time. A run with no rollout gives the ``unknown`` record. A build that raises propagates
    to the caller's failure boundary (``job._usage_fields``), which this module does not know."""
    diagnosis = result.stream_diagnosis or {}
    rollouts = list(diagnosis.get("usage_rollouts") or [])

    def build() -> usage_record.UsageRecord:
        return usage_record.from_codex_rollouts(rollouts, model=route.model, wall_ms=result.duration_ms)

    return build


def limit_fact(readings: Iterable[Mapping] | None) -> dict[str, Any] | None:
    """The ``usage_limit`` fact of a run whose job-home readings show the shape of a limit (H4d, D3): a window at 100
    percent or more with a finite ``resets_at``. Of several such windows the one with the latest ``resets_at`` is taken,
    the weekly one on a tie. ``None`` for no such window (a reset time is never invented). ``rate_limit_type`` is null:
    the readings do not carry Codex's ``rate_limit_reached_type``."""
    best: Mapping | None = None
    for reading in readings or ():
        percent, resets_at = reading.get("percent"), reading.get("resets_at")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v - v == 0 for v in (percent, resets_at)):
            continue
        if percent < 100:
            continue
        if best is None or (resets_at, reading.get("window") == usage.WEEKLY) > (
                best["resets_at"], best.get("window") == usage.WEEKLY):
            best = reading
    if best is None:
        return None
    return {"resets_at": best["resets_at"], "window": best.get("window"), "rate_limit_type": None}
