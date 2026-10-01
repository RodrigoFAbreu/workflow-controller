"""Telemetry v0 (``workflow-controller-settings-and-telemetry`` CP3, plan
Design C).

The job record's ``telemetry`` block is built from the worker's own
``worker.stdout`` by :func:`controller.worker_stream.session_telemetry`
(session totals over every ``result``), plus the wall times and the
dimensions section 8 keeps separate. ``controller.job`` writes it on both
completion paths through ``job._telemetry_block``, the failure boundary
(I6): nothing here ever steers a job's status, outcome or reconciliation.

This module also reads the records back for the read-only ``telemetry``
command and the presentation lines of ``inspect``, ``status`` and
``follow``. A record written before this release has no block: the same
figures are derived from its persisted ``worker.stdout``, marked
``"derived": true``, and nothing is written back. It reads files only, and
imports neither ``controller.job`` nor ``controller.observe`` (which
import it).
"""

from __future__ import annotations

import datetime
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from controller import worker_stream

HARNESS = "claude-code"

#: The ``--by`` groupings the ``telemetry`` command accepts.
GROUPINGS = {"role": ("role",), "model": ("model",), "role,model": ("role", "model")}

PROBLEM_TELEMETRY_FAILED = "telemetry_failed"
PROBLEM_NO_STREAM = "no_stream"

_TOKEN_KEYS = ("input", "output", "cache_creation", "cache_read")


# ---------------------------------------------------------------------------
# Building a block
# ---------------------------------------------------------------------------


def read_worker_stdout(path: str | os.PathLike) -> bytes:
    """The worker's persisted stdout, as bytes."""
    with open(path, "rb") as fh:
        return fh.read()


def session_block(data: bytes) -> dict:
    """The session figures of one complete stream (``worker.stdout``)."""
    stream = worker_stream.read_stream(data)
    return worker_stream.session_telemetry([entry["event"] for entry in stream.results])


def dimensions(record: Mapping) -> dict:
    """The dimensions section 8 keeps separate, from the record: the route
    the worker ran with, the harness, the target's Workflow release and the
    Controller version."""
    route = record.get("worker_route") if isinstance(record.get("worker_route"), Mapping) else {}
    runtime = record.get("controller_runtime") if isinstance(record.get("controller_runtime"), Mapping) else {}
    return {
        "role": route.get("role"),
        "model": route.get("model"),
        "effort": route.get("effort"),
        "harness": HARNESS,
        "workflow_version": record.get("target_workflow_version"),
        "controller_version": runtime.get("version"),
    }


def failed_block(exc: BaseException) -> dict:
    """The block of a telemetry computation that raised: no figures at
    all, one ``telemetry_failed`` problem."""
    return {
        "version": worker_stream.TELEMETRY_VERSION,
        "failed": True,
        "problems": [{"kind": PROBLEM_TELEMETRY_FAILED, "error": f"{type(exc).__name__}: {exc}"}],
    }


def parse_time(value: Any) -> datetime.datetime | None:
    """A recorded UTC time (``%Y-%m-%dT%H:%M:%SZ``) as an aware datetime,
    else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(f"{value}+0000", "%Y-%m-%dT%H:%M:%SZ%z")
    except ValueError:
        return None


def seconds_between(start: Any, end: Any) -> int | None:
    """Whole seconds from ``start`` to ``end`` (recorded UTC times), or
    ``None`` when either is missing, unparseable, or the interval is
    negative."""
    begin, finish = parse_time(start), parse_time(end)
    if begin is None or finish is None or finish < begin:
        return None
    return int((finish - begin).total_seconds())


def mtime_time(path: str | os.PathLike) -> str:
    """``path``'s modification time as a recorded UTC time."""
    moment = datetime.datetime.fromtimestamp(os.stat(path).st_mtime, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_events(path: str | os.PathLike) -> list[dict]:
    """The job's ``events.jsonl``, one object per readable line; a missing
    file is no events."""
    try:
        with open(path, "rb") as fh:
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def first_event_time(events: Iterable[Mapping], name: str) -> str | None:
    """The ``at`` of the first ``name`` event, or ``None``."""
    for event in events:
        if event.get("event") == name:
            at = event.get("at")
            return at if isinstance(at, str) else None
    return None


# ---------------------------------------------------------------------------
# Reading records back
# ---------------------------------------------------------------------------


def is_worker_job(record: Mapping) -> bool:
    """Whether ``record`` launched a worker (so has, or could derive, a
    telemetry block). A job that never launched one has nothing to count."""
    return any(isinstance(record.get(key), Mapping) for key in ("telemetry", "worker_streams", "worker"))


def _stdout_path(record: Mapping) -> str | None:
    for key in ("worker_streams", "worker"):
        block = record.get(key)
        if isinstance(block, Mapping) and isinstance(block.get("stdout_path"), str):
            return block["stdout_path"]
    return None


def _events_path(record: Mapping, stdout_path: str | None) -> Path | None:
    streams = record.get("worker_streams")
    if isinstance(streams, Mapping) and isinstance(streams.get("events_path"), str):
        return Path(streams["events_path"])
    return Path(stdout_path).parent / "events.jsonl" if stdout_path else None


def derive_block(record: Mapping) -> dict:
    """The block of a record written before telemetry existed, derived
    from its persisted ``worker.stdout`` and ``events.jsonl``, marked
    ``"derived": true``. The wall times come from the events: the job from
    ``created_at`` to the ``completed`` event, the worker from
    ``worker_spawned`` to ``worker_exited`` (its process group then still
    draining) or else ``completed``. A job with no readable stream has
    ``None`` figures and a ``no_stream`` problem. Never raises an
    ``Exception``: a failure gives a ``failed`` block, still derived."""
    try:
        stdout_path = _stdout_path(record)
        try:
            block = session_block(read_worker_stdout(stdout_path)) if stdout_path else None
        except OSError:
            block = None
        if block is None:
            block = worker_stream.session_telemetry([])
            block["problems"] = [{"kind": PROBLEM_NO_STREAM, "path": stdout_path}]
        events_path = _events_path(record, stdout_path)
        events = read_events(events_path) if events_path else []
        completed = first_event_time(events, "completed")
        exited = first_event_time(events, "worker_exited") or completed
        block.update(
            job_seconds=seconds_between(record.get("created_at"), completed),
            worker_seconds=seconds_between(first_event_time(events, "worker_spawned"), exited),
            **dimensions(record),
        )
    except Exception as exc:  # the same boundary as the recorded block's
        block = failed_block(exc)
    block["derived"] = True
    return block


def record_block(record: Mapping) -> dict | None:
    """``record``'s telemetry: its own block, else a derived one for a
    worker job, else ``None``."""
    block = record.get("telemetry")
    if isinstance(block, Mapping):
        return dict(block)
    return derive_block(record) if is_worker_job(record) else None


def unavailable(block: Mapping | None) -> bool:
    """Whether ``block`` carries no figures: a ``failed`` block, or a
    session with no result (no stream included)."""
    if not isinstance(block, Mapping) or block.get("failed"):
        return True
    return not isinstance(block.get("results"), int) or block["results"] == 0


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def token_total(block: Mapping) -> int | None:
    tokens = block.get("tokens") if isinstance(block.get("tokens"), Mapping) else {}
    values = [tokens.get(key) for key in _TOKEN_KEYS]
    return sum(values) if all(_number(value) for value in values) else None


def completed_summary(block: Mapping) -> dict | str:
    """The ``completed`` event's ``telemetry`` detail: the block's totals,
    or ``"failed"``."""
    if block.get("failed"):
        return "failed"
    return {key: block.get(key) for key in (
        "results", "turns", "cost_usd", "duration_api_ms", "job_seconds", "worker_seconds")} | {
        "tokens": token_total(block)}


def _money(value: Any) -> str:
    return f"cost ${value:.2f}" if _number(value) else "cost unknown"


def summary_text(summary: Mapping | str | None) -> str:
    """One clause for a block or a ``completed_summary``: ``cost $11.66,
    144 turns, 1,234,567 tokens, API 1320 s, job 1400 s, worker 1350 s``,
    or ``telemetry unavailable``. Figures that are ``None`` are left
    out."""
    if not isinstance(summary, Mapping) or summary.get("failed") or summary.get("results") in (None, 0):
        return "telemetry unavailable"
    tokens = summary.get("tokens")
    tokens = token_total(summary) if isinstance(tokens, Mapping) else tokens
    parts = [_money(summary.get("cost_usd"))]
    if _number(summary.get("turns")):
        parts.append(f"{summary['turns']} turns")
    if _number(tokens):
        parts.append(f"{tokens:,} tokens")
    if _number(summary.get("duration_api_ms")):
        parts.append(f"API {round(summary['duration_api_ms'] / 1000)} s")
    for key, label in (("job_seconds", "job"), ("worker_seconds", "worker")):
        if _number(summary.get(key)):
            parts.append(f"{label} {summary[key]} s")
    return ", ".join(parts)


def last_finished(records: Iterable[Mapping], *, target_repo: str | None = None) -> Mapping | None:
    """The newest record (by ``created_at``) carrying its own telemetry
    block, optionally for one target -- what ``inspect`` and ``status``
    present."""
    candidates = [r for r in records if isinstance(r.get("telemetry"), Mapping)
                  and (target_repo is None or r.get("target_repo") == target_repo)]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (str(r.get("created_at")), str(r.get("job_id"))))


def last_job_text(record: Mapping) -> str:
    return (f"last job telemetry: {record.get('job_id')} ({record.get('status')}, {record.get('work_item_id')}): "
            f"{summary_text(record.get('telemetry'))}")


# ---------------------------------------------------------------------------
# The `telemetry` command
# ---------------------------------------------------------------------------


def parse_since(value: str) -> datetime.datetime:
    """``--since``: a UTC date (``2026-10-01``) or time
    (``2026-10-01T12:00:00Z``). Raises ``ValueError`` otherwise."""
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(f"{value}+0000", f"{fmt}%z")
        except ValueError:
            continue
    raise ValueError(f"not a UTC date or time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ): {value!r}")


def rows(records: Iterable[Mapping], *, work_item: str | None = None, run: str | None = None,
         since: datetime.datetime | None = None, target_repo: str | None = None) -> list[dict]:
    """One row per worker job matching every given filter, oldest first."""
    selected = []
    for record in records:
        if not is_worker_job(record):
            continue
        if work_item is not None and record.get("work_item_id") != work_item:
            continue
        if run is not None and record.get("run_id") != run:
            continue
        if target_repo is not None and record.get("target_repo") != target_repo:
            continue
        if since is not None:
            created = parse_time(record.get("created_at"))
            if created is None or created < since:
                continue
        block = record_block(record) or {}
        route = dimensions(record)
        selected.append({
            "job_id": record.get("job_id"),
            "created_at": record.get("created_at"),
            "status": record.get("status"),
            "target_repo": record.get("target_repo"),
            "work_item_id": record.get("work_item_id"),
            "run_id": record.get("run_id"),
            "role": block.get("role", route["role"]),
            "model": block.get("model", route["model"]),
            "derived": bool(block.get("derived")),
            "unavailable": unavailable(block),
            "telemetry": block,
        })
    return sorted(selected, key=lambda row: (str(row["created_at"]), str(row["job_id"])))


#: The figures a group totals: key -> how to read it from a block.
_GROUP_FIGURES = (
    ("turns", lambda b: b.get("turns")),
    ("tokens", token_total),
    ("cost_usd", lambda b: b.get("cost_usd")),
    ("duration_api_ms", lambda b: b.get("duration_api_ms")),
    ("job_seconds", lambda b: b.get("job_seconds")),
    ("worker_seconds", lambda b: b.get("worker_seconds")),
)


def groups(selected: Iterable[Mapping], by: str | None = None) -> list[dict]:
    """``selected`` grouped by ``by`` (a :data:`GROUPINGS` key; ``None`` is
    one group of every row). Each group has its ``jobs``, the ``telemetry
    unavailable`` count, and per figure the total and the per-job mean over
    the jobs that carry it."""
    keys = GROUPINGS[by] if by else ()
    buckets: dict[tuple, list[Mapping]] = {}
    for row in selected:
        buckets.setdefault(tuple(row.get(key) for key in keys), []).append(row)
    result = []
    for group_key in sorted(buckets, key=lambda k: tuple(str(v) for v in k)):
        members = buckets[group_key]
        group: dict = {"key": dict(zip(keys, group_key)), "jobs": len(members),
                       "telemetry_unavailable": sum(1 for row in members if row["unavailable"])}
        for name, read in _GROUP_FIGURES:
            values = []
            for row in members:
                if row["unavailable"]:
                    continue
                value = read(row["telemetry"])
                if _number(value):
                    values.append(value)
            total = sum(values) if values else None
            group[name] = {"total": total, "mean": total / len(values) if values else None,
                           "jobs": len(values)}
        result.append(group)
    return result


def _figure_text(group: Mapping, name: str, fmt) -> str:
    figure = group[name]
    if figure["total"] is None:
        return "-"
    return f"{fmt(figure['total'])} (mean {fmt(figure['mean'])})"


def render_text(grouped: list[Mapping], by: str | None, *, job_count: int) -> list[str]:
    """The command's text output: one block per group, totals and per-job
    means."""
    lines = [f"jobs: {job_count}" + (f", grouped by {by}" if by else "")]
    for group in grouped:
        label = ", ".join(f"{k}={v}" for k, v in group["key"].items()) or "all jobs"
        lines.append(f"{label}: {group['jobs']} job(s), telemetry unavailable {group['telemetry_unavailable']}")
        lines.append(f"  turns {_figure_text(group, 'turns', lambda v: f'{v:,.0f}')}")
        lines.append(f"  tokens {_figure_text(group, 'tokens', lambda v: f'{v:,.0f}')}")
        lines.append(f"  cost {_figure_text(group, 'cost_usd', lambda v: f'${v:,.2f}')}")
        lines.append(f"  API time {_figure_text(group, 'duration_api_ms', lambda v: f'{v / 1000:,.0f} s')}")
        lines.append(f"  job wall time {_figure_text(group, 'job_seconds', lambda v: f'{v:,.0f} s')}")
        lines.append(f"  worker wall time {_figure_text(group, 'worker_seconds', lambda v: f'{v:,.0f} s')}")
    return lines
