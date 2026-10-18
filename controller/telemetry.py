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

from controller import harness, review_rounds, worker_stream

HARNESS = harness.worker_adapter().name

#: The ``--by`` groupings the ``telemetry`` command accepts.
GROUPINGS = {
    "role": ("role",), "model": ("model",), "role,model": ("role", "model"),
    "action": ("action",), "stage": ("stage",), "stage,round": ("stage", "round"),
    "work_item,action": ("work_item_id", "action"),
    "review_mode": ("review_mode", "lowered"),
}

#: The mode a review job written before ``review.mode`` existed ran under:
#: the only reviewer then was the distinct-provider one (review-modes D7).
LEGACY_REVIEW_MODE = "cross-model"

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


def review_columns(record: Mapping) -> tuple[str | None, bool]:
    """``(review_mode, lowered)`` of a review job (review-modes D7): the recorded
    ``review.mode`` and ``review.lowered``; a review record without the keys reads as
    ``cross-model``, never ``None``. A job that is not a review has no mode."""
    block = record.get("review")
    if not (record.get("kind") == "review" and isinstance(block, Mapping)):
        return None, False
    mode = block.get("mode")
    return (mode if isinstance(mode, str) else LEGACY_REVIEW_MODE), block.get("lowered") is True


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


def is_courier_job(record: Mapping) -> bool:
    """Whether ``record`` is a Controller external-review (courier) job:
    ``kind`` ``review`` with a ``review.action_id``. It stores no ``protocol``
    block and no worker streams, so it has no stream figures (a Claude review
    keeps its own ``review.telemetry``, which ``record_block`` reads), but it
    is a review round and ``telemetry`` reports its action, stage and round."""
    block = record.get("review")
    return record.get("kind") == "review" and isinstance(block, Mapping) \
        and isinstance(block.get("action_id"), str)


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
    """``record``'s telemetry: its own block, else the figures a review job
    kept, else a derived one for a worker job, else ``None``."""
    block = record.get("telemetry")
    if isinstance(block, Mapping):
        return dict(block)
    if is_courier_job(record):
        kept = record["review"].get("telemetry")  # a Claude review's cost and tokens (#75)
        return dict(kept) if isinstance(kept, Mapping) else None
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


def money_text(value: Any) -> str:
    """``cost $11.66``, or ``cost unknown`` when ``value`` is no number."""
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
    parts = [money_text(summary.get("cost_usd"))]
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


def last_job_entry(record: Mapping) -> dict:
    """The ``last_job_telemetry`` object ``inspect --json`` and ``status
    --json`` print for :func:`last_finished`'s record."""
    return {"job_id": record.get("job_id"), "status": record.get("status"),
            "work_item_id": record.get("work_item_id"),
            "telemetry": completed_summary(record["telemetry"])}


def last_job_text(record: Mapping) -> str:
    """``status``'s and ``inspect``'s ``last job telemetry:`` line; a job
    with no work item reads ``none``, as the ``jobs:`` lines say it."""
    return (f"last job telemetry: {record.get('job_id')} ({record.get('status')}, "
            f"{record.get('work_item_id') or 'none'}): {summary_text(record.get('telemetry'))}")


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


def _action_id(record: Mapping) -> str | None:
    for key in ("protocol", "review"):
        block = record.get(key)
        action = block.get("action_id") if isinstance(block, Mapping) else None
        if isinstance(action, str):
            return action
    return None


def _round_columns(records: list[Mapping]) -> dict[int, tuple[str | None, str, int | None, str | None]]:
    """``id(record) -> (action, stage, round, round_source)`` for every
    record in ``records`` (all of them, unfiltered, so a filter never
    changes a job's round). The round is the one stored in the job's own
    decision snapshot, else ``review_rounds.RoundCounter``'s count over the
    records up to and including that job, oldest first, kept as a running
    count per (repository, work item) so the records are read once: the
    same count and the same evidence the cap gate used for it. Only the
    ``plan`` and ``implementation`` stages have a round."""
    ordered = sorted((r for r in records if isinstance(r, Mapping)), key=review_rounds.record_key)
    counters: dict[tuple, review_rounds.RoundCounter] = {}
    columns = {}
    for record in ordered:
        key = (record.get("target_repo"), record.get("work_item_id"))
        counter = counters.setdefault(key, review_rounds.RoundCounter())
        pre_state = review_rounds.counted_pre_state(record, *key)
        if pre_state is not None:
            counter.observe(pre_state)
        action = _action_id(record)
        stage = review_rounds.stage_of(action)
        round_number = source = None
        if stage in (review_rounds.STAGE_PLAN, review_rounds.STAGE_IMPLEMENTATION):
            block = record.get("protocol")
            document = block.get("decision") if isinstance(block, Mapping) else None
            snapshot = document.get("snapshot") if isinstance(document, Mapping) else None
            round_number = review_rounds.stored_round(snapshot, stage)
            if round_number is not None:
                source = review_rounds.SOURCE_WORKFLOW
            else:
                round_number, source = counter.count(stage, snapshot), review_rounds.SOURCE_JOBS
        columns[id(record)] = (action, stage, round_number, source)
    return columns


def round_label(round_number: int | None, source: str | None) -> str | None:
    """The round as ``stage,round`` groups it: ``3`` for the Workflow's own
    number, ``3~`` for the Controller's count from job records (a lower
    bound for the plan stage), never merged."""
    if round_number is None:
        return None
    return f"{round_number}~" if source == review_rounds.SOURCE_JOBS else str(round_number)


def rows(records: Iterable[Mapping], *, work_item: str | None = None, run: str | None = None,
         since: datetime.datetime | None = None, target_repo: str | None = None) -> list[dict]:
    """One row per worker job matching every given filter, oldest first."""
    records = list(records)
    columns = _round_columns(records)
    selected = []
    for record in records:
        if not (is_worker_job(record) or is_courier_job(record)):
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
        action, stage, round_number, source = columns.get(id(record), (None, review_rounds.STAGE_NONE, None, None))
        review_mode, lowered = review_columns(record)
        selected.append({
            "job_id": record.get("job_id"),
            "created_at": record.get("created_at"),
            "status": record.get("status"),
            "target_repo": record.get("target_repo"),
            "work_item_id": record.get("work_item_id"),
            "run_id": record.get("run_id"),
            "role": block.get("role", route["role"]),
            "model": block.get("model", route["model"]),
            "action": action,
            "stage": stage,
            "round": round_number,
            "round_source": source,
            "review_mode": review_mode,
            "lowered": lowered,
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


def _group_value(row: Mapping, key: str) -> Any:
    if key == "round":
        return round_label(row.get("round"), row.get("round_source"))
    return row.get(key)


def _round_order(label: str | None) -> tuple:
    if label is None:
        return (0, 0, False)
    return (1, int(label.rstrip("~")), label.endswith("~"))


def groups(selected: Iterable[Mapping], by: str | None = None) -> list[dict]:
    """``selected`` grouped by ``by`` (a :data:`GROUPINGS` key; ``None`` is
    one group of every row). Each group has its ``jobs``, the ``telemetry
    unavailable`` count, and per figure the total and the per-job mean over
    the jobs that carry it."""
    keys = GROUPINGS[by] if by else ()
    buckets: dict[tuple, list[Mapping]] = {}
    for row in selected:
        buckets.setdefault(tuple(_group_value(row, key) for key in keys), []).append(row)

    def order(group_key: tuple) -> tuple:
        # A round sorts by number, then the Workflow's own before the count.
        return tuple(_round_order(v) if key == "round" else str(v) for key, v in zip(keys, group_key))

    result = []
    for group_key in sorted(buckets, key=order):
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


#: How a group label names a missing value: a route with no model
#: inherits the harness's model; a job with no route has no role. JSON
#: output keeps ``null``.
_GROUP_LABEL_MISSING = {"role": "none", "model": "inherit", "action": "none", "round": "none",
                        "work_item_id": "none", "review_mode": "none"}

#: The label a group key prints under, where it differs from the row key.
_GROUP_LABEL_NAME = {"work_item_id": "work_item"}


def render_text(grouped: list[Mapping], by: str | None, *, job_count: int) -> list[str]:
    """The command's text output: one block per group, totals and per-job
    means."""
    lines = [f"jobs: {job_count}" + (f", grouped by {by}" if by else "")]
    for group in grouped:
        label = ", ".join(f"{_GROUP_LABEL_NAME.get(k, k)}={_GROUP_LABEL_MISSING[k] if v is None else v}"
                          for k, v in group["key"].items()) or "all jobs"
        lines.append(f"{label}: {group['jobs']} job(s), telemetry unavailable {group['telemetry_unavailable']}")
        lines.append(f"  turns {_figure_text(group, 'turns', lambda v: f'{v:,.0f}')}")
        lines.append(f"  tokens {_figure_text(group, 'tokens', lambda v: f'{v:,.0f}')}")
        lines.append(f"  cost {_figure_text(group, 'cost_usd', lambda v: f'${v:,.2f}')}")
        lines.append(f"  API time {_figure_text(group, 'duration_api_ms', lambda v: f'{v / 1000:,.0f} s')}")
        lines.append(f"  job wall time {_figure_text(group, 'job_seconds', lambda v: f'{v:,.0f} s')}")
        lines.append(f"  worker wall time {_figure_text(group, 'worker_seconds', lambda v: f'{v:,.0f} s')}")
    return lines
