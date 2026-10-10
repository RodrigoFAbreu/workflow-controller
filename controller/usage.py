"""The usage budget (``workflow-controller-usage-budget`` CP1, plan
``docs/ai-workflow/CONTROLLER_USAGE_BUDGET_PLAN.md``).

A leaf module: it reads how much of the Claude (five-hour and seven-day) and
Codex (primary and secondary) windows is used, keeps one account-wide record
under the runtime root that every lane and repository shares, forecasts a job
from earlier jobs of the same ``(role, model)``, admits a job atomically
against the readings *and* against what other lanes have already reserved,
accounts for the job when it ends, and decides, strictest limit first,
whether a job may start.

It imports :mod:`controller.runtime` and :mod:`controller.errors` and the
standard library, and never :mod:`controller.job`, :mod:`controller.observe`,
:mod:`controller.cli` or :mod:`controller.telemetry`, which import it. Every
path (the runtime root, the Codex home) is a parameter, and every time is an
epoch in seconds passed in as ``now``: nothing here reads the real clock
except through a default the callers always override in tests.

The shared record ``<runtime_root>/usage.json`` is changed only under
``runtime.usage_lock`` and published by one ``runtime.write_json``, so every
change is all-or-nothing. See the plan's D2-D6 for the contract each
function implements.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import secrets
import statistics
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from controller import runtime
from controller.errors import UsageRecordError

PROVIDER_CLAUDE = "claude"
PROVIDER_CODEX = "codex"
PROVIDERS = (PROVIDER_CLAUDE, PROVIDER_CODEX)

FIVE_HOUR = "five_hour"
WEEKLY = "weekly"
WINDOWS = (FIVE_HOUR, WEEKLY)

#: Window lengths, used only to advance a reservation's window when no
#: reading names the new one.
WINDOW_SECONDS = {FIVE_HOUR: 5 * 3600, WEEKLY: 7 * 24 * 3600}

#: Two ``resets_at`` this close are one window: the Codex samples show the
#: weekly one ending ``4075`` then ``4074`` inside a single window.
WINDOW_JITTER_SECONDS = 120

USAGE_FILE = "usage.json"
EVENTS_FILE = "usage-events.jsonl"
RECORD_VERSION = 2

LEDGER_MAX_ENTRIES = 1000
LEDGER_MAX_AGE_SECONDS = 30 * 86400
SETTLED_MAX_AGE_SECONDS = 90 * 86400
RUN_SPEND_MAX_AGE_SECONDS = 90 * 86400
ABANDON_AFTER_SECONDS = 7 * 86400
FORECAST_SAMPLE = 20

#: How much of the end of a Codex session file is scanned for ``rate_limits``.
CODEX_TAIL_BYTES = 1 << 20
CODEX_FILES_SCANNED = 3
_CODEX_WINDOW_MINUTES = {300: FIVE_HOUR, 10080: WEEKLY}
_CLAUDE_WINDOW_KEYS = {"five_hour": FIVE_HOUR, "seven_day": WEEKLY}

GO = "go"

#: ``complete``'s outcomes.
OUTCOME_OK = "ok"
OUTCOME_NOT_STARTED = "not_started"

#: ``renew``'s results.
RENEWED = "renewed"
REVIVED = "revived"
UNKNOWN = "unknown"

#: ``complete``'s statuses.
COMPLETE_CHARGED = "charged"
COMPLETE_NOT_STARTED = "not_started"
COMPLETE_NOOP = "noop"


# ---------------------------------------------------------------------------
# Limits: the settings one provider is evaluated against
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Limits:
    """The settings of D4 for one provider. ``usage.codex_*`` rows fill the
    Codex instance and the plain rows the Claude one; ``enabled``, the grace,
    the wait bound and the lease are shared."""

    provider: str = PROVIDER_CLAUDE
    enabled: bool = True
    pause_at_percent: float = 85
    weekly_pause_at_percent: float = 95
    resume_grace_seconds: int = 180
    max_wait_seconds: int = 21600
    reservation_seconds: int = 7200
    default_job_percent: float = 8
    default_job_weekly_percent: float = 1
    run_cap_percent: float | None = None
    repository_cap_percent: float | None = None


def limits_from_values(values: Mapping[str, Any], provider: str, *, run_cap: float | None = None) -> Limits:
    """Build the provider's :class:`Limits` from a mapping of ``usage.*``
    setting keys (missing keys keep the defaults of D4). ``run_cap``, when
    not ``None``, is the invocation's cap flag and overrides the setting."""
    prefix = "usage." if provider == PROVIDER_CLAUDE else "usage.codex_"
    defaults = Limits(provider=provider)

    def pick(name: str, default: Any, *, shared: bool = False) -> Any:
        value = values.get(("usage." if shared else prefix) + name)
        return default if value is None else value

    cap = pick("run_cap_percent", None)
    return Limits(
        provider=provider,
        enabled=bool(pick("enabled", True, shared=True)),
        pause_at_percent=pick("pause_at_percent", defaults.pause_at_percent),
        weekly_pause_at_percent=pick("weekly_pause_at_percent", defaults.weekly_pause_at_percent),
        resume_grace_seconds=pick("resume_grace_seconds", defaults.resume_grace_seconds, shared=True),
        max_wait_seconds=pick("max_wait_seconds", defaults.max_wait_seconds, shared=True),
        reservation_seconds=pick("reservation_seconds", defaults.reservation_seconds, shared=True),
        default_job_percent=pick("default_job_percent", defaults.default_job_percent),
        default_job_weekly_percent=pick("default_job_weekly_percent", defaults.default_job_weekly_percent),
        run_cap_percent=run_cap if run_cap is not None else cap,
        repository_cap_percent=pick("repository_cap_percent", None),
    )


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------


def make_reading(provider: str, window: str, percent: float, resets_at: float, observed_at: float,
                 source: str) -> dict:
    """The reading of D2: ``{provider, window, percent, resets_at,
    observed_at, source}``."""
    return {"provider": provider, "window": window, "percent": float(percent), "resets_at": resets_at,
            "observed_at": observed_at, "source": source}


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def same_window(a: float, b: float) -> bool:
    """Whether two ``resets_at`` name one window (within the jitter)."""
    return abs(a - b) <= WINDOW_JITTER_SECONDS


def merge_reading(stored: dict | None, incoming: dict) -> dict:
    """The deterministic merge of D2 (pure). The same window keeps the
    greater percent (usage within a window never falls), or on a tie the
    newer ``observed_at``, and takes the larger ``resets_at``; otherwise the
    reading of the later window wins whatever either ``observed_at``."""
    if stored is None:
        return dict(incoming)
    if same_window(stored["resets_at"], incoming["resets_at"]):
        if incoming["percent"] > stored["percent"] or (
            incoming["percent"] == stored["percent"] and incoming["observed_at"] > stored["observed_at"]
        ):
            winner = dict(incoming)
        else:
            winner = dict(stored)
        winner["resets_at"] = max(stored["resets_at"], incoming["resets_at"])
        return winner
    return dict(incoming if incoming["resets_at"] > stored["resets_at"] else stored)


def window_percent(reading: dict | None, now: float) -> float | None:
    """The reading's percent now: ``None`` without a reading, ``0.0`` once
    its window has reset."""
    if reading is None:
        return None
    if reading["resets_at"] <= now:
        return 0.0
    return reading["percent"]


def _by_window(readings: Mapping | Iterable | None) -> dict:
    """``{window: reading}`` from either that mapping or an iterable of
    readings (the later one of a window wins)."""
    if readings is None:
        return {}
    if isinstance(readings, Mapping):
        return {w: r for w, r in readings.items() if r is not None}
    out: dict = {}
    for reading in readings:
        out[reading["window"]] = reading
    return out


def _claude_event_readings(event: dict, observed_at: float, source: str) -> dict:
    """The readings one ``rate_limit_event`` carries, by window. A
    ``rejected`` status reads 100% for the window ``rateLimitType`` names and
    no other; the text of the stream is never consulted."""
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return {}
    out: dict = {}
    windows = info.get("unifiedWindows")
    if isinstance(windows, dict):
        for key, window in _CLAUDE_WINDOW_KEYS.items():
            entry = windows.get(key)
            if isinstance(entry, dict) and _is_number(entry.get("utilization")) and _is_number(entry.get("resetsAt")):
                out[window] = make_reading(PROVIDER_CLAUDE, window, round(100 * entry["utilization"], 6),
                                           entry["resetsAt"], observed_at, source)
    named = _CLAUDE_WINDOW_KEYS.get(info.get("rateLimitType"))
    if named and named not in out and _is_number(info.get("utilization")) and _is_number(info.get("resetsAt")):
        out[named] = make_reading(PROVIDER_CLAUDE, named, round(100 * info["utilization"], 6),
                                  info["resetsAt"], observed_at, source)
    if info.get("status") == "rejected" and named:
        resets = out[named]["resets_at"] if named in out else info.get("resetsAt")
        if _is_number(resets):
            out[named] = make_reading(PROVIDER_CLAUDE, named, 100.0, resets, observed_at, source)
    return out


def _claude_source(source: bytes | str | os.PathLike, observed_at: float | None) -> tuple[bytes, float, str]:
    """``(bytes, observed_at, label)`` for a Claude stream given as a path
    (``observed_at`` defaults to its mtime), ``bytes`` or text."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), observed_at if observed_at is not None else time.time(), "claude-stream"
    if isinstance(source, str):
        return source.encode("utf-8"), observed_at if observed_at is not None else time.time(), "claude-stream"
    path = Path(source)
    data = path.read_bytes()
    return data, observed_at if observed_at is not None else path.stat().st_mtime, str(path)


def read_claude_stream_span(source: bytes | str | os.PathLike, *, observed_at: float | None = None) -> tuple[dict, dict]:
    """``(first, last)``: the first and the last reading of each window in a
    ``stream-json`` worker stream, each ``{window: reading}``. A line that is
    not JSON (a partial last line) is skipped."""
    data, stamp, label = _claude_source(source, observed_at)
    first: dict = {}
    last: dict = {}
    for raw in data.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
            continue
        for window, reading in _claude_event_readings(event, stamp, label).items():
            first.setdefault(window, reading)
            last[window] = reading
    return first, last


def read_claude_stream(source: bytes | str | os.PathLike, *, observed_at: float | None = None) -> list[dict]:
    """The last reading of each window in a Claude worker stream (path,
    ``bytes`` or text), in window order."""
    _, last = read_claude_stream_span(source, observed_at=observed_at)
    return [last[w] for w in WINDOWS if w in last]


def _parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = datetime.datetime.combine(parsed.date(), parsed.time(), tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def _codex_rate_limits(event: dict) -> dict | None:
    payload = event.get("payload")
    for holder in (payload, event):
        if isinstance(holder, dict) and isinstance(holder.get("rate_limits"), dict):
            return holder["rate_limits"]
    return None


def read_codex_home(codex_home: str | os.PathLike, *, max_files: int = CODEX_FILES_SCANNED) -> list[dict]:
    """The Codex readings under ``<codex_home>/sessions/*/*/*/rollout-*.jsonl``
    (D3): the ``rate_limits`` of the newest ``max_files`` files, ``primary``
    and ``secondary`` told apart by ``window_minutes`` (300 and 10080), merged
    by the D2 rule, so a fresher ``timestamp`` in an older file still counts.
    An absent home, a file with no ``rate_limits`` and a partial last line are
    no reading, never an error."""
    root = Path(codex_home) / "sessions"
    try:
        files = [p for p in root.glob("*/*/*/rollout-*.jsonl") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    candidates: list[dict] = []
    for path in files[:max_files]:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - CODEX_TAIL_BYTES))
                data = fh.read()
        except OSError:
            continue
        for raw in data.splitlines():
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            limits = _codex_rate_limits(event) if isinstance(event, dict) else None
            stamp = _parse_timestamp(event.get("timestamp")) if isinstance(event, dict) else None
            if limits is None or stamp is None:
                continue
            for entry in (limits.get("primary"), limits.get("secondary")):
                if not isinstance(entry, dict):
                    continue
                window = _CODEX_WINDOW_MINUTES.get(entry.get("window_minutes"))
                if window is None or not _is_number(entry.get("used_percent")) or not _is_number(entry.get("resets_at")):
                    continue
                candidates.append(make_reading(PROVIDER_CODEX, window, entry["used_percent"], entry["resets_at"],
                                               stamp, str(path)))
    merged: dict = {}
    for reading in sorted(candidates, key=lambda r: r["observed_at"]):
        merged[reading["window"]] = merge_reading(merged.get(reading["window"]), reading)
    return [merged[w] for w in WINDOWS if w in merged]


# ---------------------------------------------------------------------------
# The shared record
# ---------------------------------------------------------------------------


def _empty_record() -> dict:
    return {"version": RECORD_VERSION, "readings": {}, "reservations": {}, "lapsed": {}, "ledger": [],
            "settled": {}, "spend": {"run": {}, "repository": {}}}


def load_record(runtime_root: str | os.PathLike) -> dict:
    """The shared record (a fresh empty one when absent). A file that is not
    a JSON object of a version this release knows is an
    :class:`UsageRecordError`; it is never silently replaced."""
    path = Path(runtime_root) / USAGE_FILE
    try:
        raw = runtime.read_json(path)
    except (ValueError, OSError) as exc:
        raise UsageRecordError(f"the usage record at {path} cannot be read: {exc}",
                               evidence={"path": str(path)}) from exc
    if raw is None:
        return _empty_record()
    if not isinstance(raw, dict) or raw.get("version") != RECORD_VERSION:
        raise UsageRecordError(
            f"the usage record at {path} is not a version {RECORD_VERSION} object; repair or remove it",
            evidence={"path": str(path), "version": raw.get("version") if isinstance(raw, dict) else None})
    record = _empty_record()
    for key in record:
        if key in raw:
            record[key] = raw[key]
    record["spend"].setdefault("run", {})
    record["spend"].setdefault("repository", {})
    return record


def _save(runtime_root: str | os.PathLike, record: dict) -> None:
    runtime.write_json(Path(runtime_root), USAGE_FILE, record)


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat(timespec="seconds")


def append_event(runtime_root: str | os.PathLike, event: str, *, now: float, **fields: Any) -> bool:
    """Append one line to ``<runtime_root>/usage-events.jsonl`` (D8), best
    effort: ``{event, at, provider, window, percent, forecast, resume_at,
    reason, token, repository, run_id}`` with every field not given ``None``.
    Returns whether the line was written."""
    line = {"event": event, "at": _iso(now), "provider": None, "window": None, "percent": None,
            "forecast": None, "resume_at": None, "reason": None, "token": None, "repository": None,
            "run_id": None}
    line.update(fields)
    return runtime.append_jsonl_best_effort(Path(runtime_root), EVENTS_FILE, line)


def current_readings(record: dict, provider: str) -> dict:
    """``{window: reading}`` stored for ``provider``."""
    return dict(record["readings"].get(provider, {}))


def _merge_into(record: dict, readings: Iterable[dict]) -> None:
    for reading in readings:
        per_provider = record["readings"].setdefault(reading["provider"], {})
        per_provider[reading["window"]] = merge_reading(per_provider.get(reading["window"]), reading)


def record_reading(runtime_root: str | os.PathLike, readings: Iterable[dict]) -> dict:
    """Merge ``readings`` into the shared record under the lock (D2) and
    return the stored readings, ``{provider: {window: reading}}``."""
    readings = list(readings)
    with runtime.usage_lock(Path(runtime_root)):
        record = load_record(runtime_root)
        _merge_into(record, readings)
        _save(runtime_root, record)
        return {p: dict(w) for p, w in record["readings"].items()}


# ---------------------------------------------------------------------------
# Reservations: rollover, lease, lapse, abandonment, retention
# ---------------------------------------------------------------------------


def _new_window(record: dict, provider: str, window: str, old_resets_at: float | None, now: float) -> tuple[float, float | None]:
    """``(start percent, resets_at)`` of the window now current: the stored
    reading when it names a future window, else the old window advanced by
    its length. ``(0, None)`` when the reservation never knew a window."""
    reading = record["readings"].get(provider, {}).get(window)
    if reading is not None and reading["resets_at"] > now:
        return reading["percent"], reading["resets_at"]
    if old_resets_at is None:
        return 0.0, None
    step = WINDOW_SECONDS[window]
    resets = old_resets_at
    while resets <= now:
        resets += step
    return 0.0, resets


def _roll_over(record: dict, reservation: dict, now: float) -> None:
    """Re-base each component whose window has reset into the new window at
    the full forecast (D6). A reset is a rollover, never a release; the other
    component is untouched and the original ``baseline`` never moves."""
    rolled = False
    for window in WINDOWS:
        comp = reservation[window]
        if comp["window_resets_at"] is not None and comp["window_resets_at"] <= now:
            start, resets = _new_window(record, reservation["provider"], window, comp["window_resets_at"], now)
            comp["start"], comp["window_resets_at"] = start, resets
            rolled = True
    if rolled:
        reservation["rolled_over"] = reservation.get("rolled_over", 0) + 1


def _touch_run(record: dict, provider: str, run_id: str | None, now: float) -> None:
    if not run_id:
        return
    entry = record["spend"]["run"].setdefault(provider, {}).setdefault(run_id, {"charged": 0.0, "updated_at": now})
    entry["updated_at"] = now


def _outstanding_run_ids(record: dict, provider: str) -> set:
    return {r["run_id"] for section in ("reservations", "lapsed") for r in record[section].values()
            if r["provider"] == provider and r.get("run_id")}


def _charge_repository(record: dict, provider: str, repository: str | None, window_resets_at: float | None,
                       amount: float, now: float) -> None:
    """Add a repository charge to the current five-hour window's total (D6):
    a newer window replaces it, the same window adds, an older (delayed)
    writer is ignored. A completion that cannot place its window is placed in
    the window after ``now``."""
    if not repository:
        return
    if window_resets_at is None:
        window_resets_at = now + WINDOW_SECONDS[FIVE_HOUR]
    totals = record["spend"]["repository"].setdefault(provider, {})
    current = totals.get(repository)
    if current is None or current["window_resets_at"] <= now:
        if window_resets_at > now:
            totals[repository] = {"window_resets_at": window_resets_at, "charged": amount}
        return
    if same_window(current["window_resets_at"], window_resets_at):
        current["charged"] += amount
        current["window_resets_at"] = max(current["window_resets_at"], window_resets_at)
    elif window_resets_at > current["window_resets_at"]:
        totals[repository] = {"window_resets_at": window_resets_at, "charged": amount}


def _append_ledger(record: dict, entry: dict) -> None:
    record["ledger"].append(entry)


def _maintain(record: dict, now: float) -> list:
    """Bring the record up to ``now`` in memory: roll over live components
    whose window reset, lapse expired leases, abandon lapsed reservations
    never settled in seven days, and prune what retention allows. Returns the
    events to emit."""
    events: list = []
    for token, reservation in list(record["reservations"].items()):
        _roll_over(record, reservation, now)
        if reservation["expires_at"] <= now:
            del record["reservations"][token]
            record["lapsed"][token] = {**reservation, "reason": "expired", "lapsed_at": reservation["expires_at"]}
            events.append(("usage_reservation_expired", {"provider": reservation["provider"], "token": token,
                                                         "repository": reservation.get("repository"),
                                                         "run_id": reservation.get("run_id"),
                                                         "reason": "expired"}))
    for token, lapsed in list(record["lapsed"].items()):
        if now - lapsed["lapsed_at"] >= ABANDON_AFTER_SECONDS:
            amount = lapsed["five_hour"]["amount"]
            del record["lapsed"][token]
            _charge_run(record, lapsed["provider"], lapsed.get("run_id"), amount, now)
            _charge_repository(record, lapsed["provider"], lapsed.get("repository"),
                               lapsed["five_hour"]["window_resets_at"], amount, now)
            record["settled"][token] = now
            _append_ledger(record, _ledger_entry(
                token, lapsed, now, end_resets=None, five_hour=None, weekly=None, after_five=None, after_weekly=None,
                charged=amount, delta="abandoned"))
            events.append(("usage_reservation_abandoned", {"provider": lapsed["provider"], "token": token,
                                                           "repository": lapsed.get("repository"),
                                                           "run_id": lapsed.get("run_id"), "reason": "abandoned"}))
    _prune(record, now)
    return events


def _charge_run(record: dict, provider: str, run_id: str | None, amount: float, now: float) -> None:
    if not run_id:
        return
    entry = record["spend"]["run"].setdefault(provider, {}).setdefault(run_id, {"charged": 0.0, "updated_at": now})
    entry["charged"] += amount
    entry["updated_at"] = now


def _prune(record: dict, now: float) -> None:
    ledger = [e for e in record["ledger"] if now - e["finished_at"] <= LEDGER_MAX_AGE_SECONDS]
    record["ledger"] = ledger[-LEDGER_MAX_ENTRIES:]
    record["settled"] = {t: at for t, at in record["settled"].items() if now - at <= SETTLED_MAX_AGE_SECONDS}
    for provider, runs in list(record["spend"]["run"].items()):
        outstanding = _outstanding_run_ids(record, provider)
        for run_id, entry in list(runs.items()):
            if run_id not in outstanding and now - entry["updated_at"] > RUN_SPEND_MAX_AGE_SECONDS:
                del runs[run_id]
        if not runs:
            del record["spend"]["run"][provider]
    for provider, repos in list(record["spend"]["repository"].items()):
        for repository, entry in list(repos.items()):
            if entry["window_resets_at"] <= now:
                del repos[repository]
        if not repos:
            del record["spend"]["repository"][provider]


def _ledger_entry(token: str, reservation: dict, now: float, *, end_resets: float | None, five_hour: float | None,
                  weekly: float | None, after_five: float | None, after_weekly: float | None, charged: float,
                  delta: str) -> dict:
    baseline = reservation.get("baseline", {})
    return {
        "id": token, "provider": reservation["provider"], "role": reservation.get("role"),
        "model": reservation.get("model"), "repository": reservation.get("repository"),
        "run_id": reservation.get("run_id"), "started_at": reservation["created_at"], "finished_at": now,
        "start_window_resets_at": baseline.get(FIVE_HOUR, {}).get("window_resets_at"),
        "end_window_resets_at": end_resets, "five_hour": five_hour, "weekly": weekly,
        "five_hour_after_reset": after_five, "weekly_after_reset": after_weekly,
        "reserved_five_hour": reservation[FIVE_HOUR]["amount"], "reserved_weekly": reservation[WEEKLY]["amount"],
        "charged": charged, "delta": delta,
    }


def _emit(runtime_root: str | os.PathLike, events: Iterable, now: float) -> None:
    for name, fields in events:
        append_event(runtime_root, name, now=now, **fields)


# ---------------------------------------------------------------------------
# Forecast, spend, outstanding liabilities
# ---------------------------------------------------------------------------


def forecast(record: dict, provider: str, role: str | None, model: str | None, limits: Limits) -> tuple[float, float]:
    """``(five_hour, weekly)``: the mean of the last 20 measured ledger
    entries of ``(provider, role, model)`` for each window separately
    (``None`` figures ignored, so ``unknown``, ``after_reset`` and
    ``abandoned`` entries never enter), else the provider's defaults."""
    matching = [e for e in record["ledger"]
                if e["provider"] == provider and e.get("role") == role and e.get("model") == model]

    def mean(key: str, default: float) -> float:
        figures = [e[key] for e in matching if e.get(key) is not None][-FORECAST_SAMPLE:]
        return statistics.fmean(figures) if figures else float(default)

    return mean(FIVE_HOUR, limits.default_job_percent), mean(WEEKLY, limits.default_job_weekly_percent)


def run_spent(record: dict, provider: str, run_id: str | None) -> float:
    """What the run has been charged (never window-scoped)."""
    if not run_id:
        return 0.0
    return record["spend"]["run"].get(provider, {}).get(run_id, {}).get("charged", 0.0)


def repository_spent(record: dict, provider: str, repository: str | None, now: float) -> float:
    """What the repository has been charged in the current five-hour window."""
    if not repository:
        return 0.0
    entry = record["spend"]["repository"].get(provider, {}).get(repository)
    if entry is None or entry["window_resets_at"] <= now:
        return 0.0
    return entry["charged"]


def _all_reservations(record: dict) -> list:
    return list(record["reservations"].values()) + list(record["lapsed"].values())


def run_outstanding(record: dict, provider: str, run_id: str | None) -> float:
    """The five-hour forecasts of every live and lapsed reservation of the run."""
    if not run_id:
        return 0.0
    return sum(r[FIVE_HOUR]["amount"] for r in _all_reservations(record)
               if r["provider"] == provider and r.get("run_id") == run_id)


def repository_outstanding(record: dict, provider: str, repository: str | None, now: float) -> float:
    """The same for the repository, restricted to reservations whose
    five-hour window is the current one (a lapsed holder from an earlier
    window no longer weighs on this one)."""
    if not repository:
        return 0.0
    total = 0.0
    for r in _all_reservations(record):
        if r["provider"] != provider or r.get("repository") != repository:
            continue
        resets = r[FIVE_HOUR]["window_resets_at"]
        if resets is None or resets > now:
            total += r[FIVE_HOUR]["amount"]
    return total


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Hold:
    """A refusal to start a job. ``window`` is ``five_hour``, ``weekly``,
    ``run_cap`` or ``repository_cap``. A timed hold has a ``resume_at`` and
    is ``waitable``; a cap has neither (D5), though a repository cap carries
    the five-hour window's ``resets_at`` for the message alone."""

    provider: str
    window: str
    percent: float
    forecast: float
    resume_at: float | None
    reason: str
    waitable: bool
    resets_at: float | None = None
    outstanding: bool = False


def _label(window: str) -> str:
    return "five-hour" if window == FIVE_HOUR else "weekly"


def evaluate(provider: str, readings: Mapping, reservations: Iterable[dict], settings: Limits,
             forecast: tuple[float, float], run_spent: float, run_outstanding: float, repository_spent: float,
             repository_outstanding: float, now: float, *, notes: list | None = None) -> Hold | str:
    """The pure strictest-wins decision of D5: ``GO`` or a :class:`Hold`.

    A job is held when ``reading + reserved + forecast`` reaches a window's
    threshold, or ``spent + outstanding + forecast`` exceeds a cap. A cap is
    never waitable and dominates every timed hold; among timed holds the one
    with the latest resume time is reported. A *missing* reading disables only
    that window's account check (a note ``no reading`` is appended to
    ``notes``): a fresh machine must run."""
    five, weekly = forecast
    live = list(reservations)
    reserved = {FIVE_HOUR: sum(r[FIVE_HOUR]["amount"] for r in live),
                WEEKLY: sum(r[WEEKLY]["amount"] for r in live)}
    thresholds = {FIVE_HOUR: settings.pause_at_percent, WEEKLY: settings.weekly_pause_at_percent}
    forecasts = {FIVE_HOUR: five, WEEKLY: weekly}

    caps: list[Hold] = []
    if settings.run_cap_percent is not None:
        total = run_spent + run_outstanding + five
        if total > settings.run_cap_percent:
            caps.append(Hold(provider, "run_cap", run_spent + run_outstanding, five, None,
                             f"the run's {provider} spend {run_spent:.0f}% + outstanding {run_outstanding:.0f}% + "
                             f"forecast {five:.0f}% exceeds the run cap {settings.run_cap_percent}%",
                             False, outstanding=run_outstanding > 0))
    if settings.repository_cap_percent is not None:
        total = repository_spent + repository_outstanding + five
        if total > settings.repository_cap_percent:
            reading = readings.get(FIVE_HOUR)
            caps.append(Hold(provider, "repository_cap", repository_spent + repository_outstanding, five, None,
                             f"the repository's {provider} spend {repository_spent:.0f}% + outstanding "
                             f"{repository_outstanding:.0f}% + forecast {five:.0f}% exceeds the repository cap "
                             f"{settings.repository_cap_percent}%",
                             False, resets_at=reading["resets_at"] if reading else None,
                             outstanding=repository_outstanding > 0))
    if caps:
        return caps[0]

    timed: list[Hold] = []
    for window in WINDOWS:
        reading = readings.get(window)
        percent = window_percent(reading, now)
        if percent is None:
            if notes is not None:
                notes.append(f"no reading: {provider} {window}")
            continue
        if percent + reserved[window] + forecasts[window] >= thresholds[window]:
            resume_at = reading["resets_at"]
            waitable = resume_at > now
            timed.append(Hold(
                provider, window, percent, forecasts[window], resume_at if waitable else None,
                f"{provider} {_label(window)} usage {percent:.0f}% + reserved {reserved[window]:.0f}% + forecast "
                f"{forecasts[window]:.0f}% reaches the {thresholds[window]}% threshold"
                + ("" if waitable else " and the window has nothing left to wait for"),
                waitable))
    if not timed:
        return GO
    return max(timed, key=lambda h: (h.resume_at if h.resume_at is not None else float("-inf")))


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Admission:
    """The result of :func:`admit`: a ``token`` on go, else the ``hold``;
    the ``forecast`` used and any ``notes`` (``no reading: ...``)."""

    token: str | None
    hold: Hold | None
    forecast: tuple[float, float]
    notes: tuple = ()


def _component(reading: dict | None, amount: float, now: float) -> dict:
    percent = window_percent(reading, now)
    return {"amount": amount, "start": percent, "window_resets_at": reading["resets_at"] if reading else None}


def admit(runtime_root: str | os.PathLike, *, provider: str, role: str | None, model: str | None,
          repository: str | None, run_id: str | None, limits: Limits, now: float,
          readings: Iterable[dict] = (), reserve: bool = True) -> Admission:
    """Evaluate D5 and, on go, write the reservation, all in one critical
    section, so two lanes cannot both pass on the same headroom. ``readings``
    are merged first. On a hold nothing is written (apart from the
    maintenance the record owed anyway). ``reserve=False`` is the check of
    ``usage --check``: a go reserves nothing and carries no token."""
    with runtime.usage_lock(Path(runtime_root)):
        record = load_record(runtime_root)
        _merge_into(record, list(readings))
        events = _maintain(record, now)
        fc = forecast(record, provider, role, model, limits)
        stored = current_readings(record, provider)
        notes: list = []
        result = evaluate(
            provider, stored, [r for r in record["reservations"].values() if r["provider"] == provider], limits, fc,
            run_spent(record, provider, run_id), run_outstanding(record, provider, run_id),
            repository_spent(record, provider, repository, now),
            repository_outstanding(record, provider, repository, now), now, notes=notes)
        if result == GO and not reserve:
            admission = Admission(None, None, fc, tuple(notes))
        elif result == GO:
            token = secrets.token_hex(16)
            five = _component(stored.get(FIVE_HOUR), fc[0], now)
            weekly = _component(stored.get(WEEKLY), fc[1], now)
            record["reservations"][token] = {
                "provider": provider, "role": role, "model": model, "repository": repository, "run_id": run_id,
                "job_id": None, "created_at": now, "renewed_at": now,
                "expires_at": now + limits.reservation_seconds, "rolled_over": 0,
                FIVE_HOUR: five, WEEKLY: weekly,
                "baseline": {FIVE_HOUR: {"start": five["start"], "window_resets_at": five["window_resets_at"]},
                             WEEKLY: {"start": weekly["start"], "window_resets_at": weekly["window_resets_at"]}},
            }
            _touch_run(record, provider, run_id, now)
            admission = Admission(token, None, fc, tuple(notes))
        else:
            admission = Admission(None, result, fc, tuple(notes))
        _save(runtime_root, record)
        _emit(runtime_root, events, now)
    return admission


def bind_job(runtime_root: str | os.PathLike, token: str, job_id: str, *, now: float) -> bool:
    """Write ``job_id`` into the reservation (live or lapsed) durably, before
    the job record is published, so the sweep can always reach a record that
    exists. Returns whether the token was found."""
    with runtime.usage_lock(Path(runtime_root)):
        record = load_record(runtime_root)
        events = _maintain(record, now)
        target = record["reservations"].get(token) or record["lapsed"].get(token)
        if target is not None:
            target["job_id"] = job_id
        _save(runtime_root, record)
        _emit(runtime_root, events, now)
    return target is not None


def renew(runtime_root: str | os.PathLike, token: str, *, reservation_seconds: float, now: float) -> str:
    """Extend a live reservation's lease (``RENEWED``), or revive a lapsed
    one with a fresh lease and its components re-based (``REVIVED``): the
    holder is demonstrably alive and the lapse was the clock's. A revival does
    not re-run the decision. An unknown or already settled token revives
    nothing (``UNKNOWN``). Any renewal refreshes the run's activity."""
    with runtime.usage_lock(Path(runtime_root)):
        record = load_record(runtime_root)
        events = _maintain(record, now)
        result = UNKNOWN
        reservation = record["reservations"].get(token)
        if reservation is not None:
            result = RENEWED
        elif token in record["lapsed"] and token not in record["settled"]:
            reservation = record["lapsed"].pop(token)
            reservation.pop("reason", None)
            reservation.pop("lapsed_at", None)
            record["reservations"][token] = reservation
            result = REVIVED
        if reservation is not None:
            reservation["renewed_at"] = now
            reservation["expires_at"] = now + reservation_seconds
            _roll_over(record, reservation, now)
            _touch_run(record, reservation["provider"], reservation.get("run_id"), now)
        _save(runtime_root, record)
        _emit(runtime_root, events, now)
    return result


class RenewalTicker:
    """A daemon thread calling ``renew()`` every ``interval_seconds``.

    ``wait`` is an ``Event.wait``-shaped callable (``wait(seconds) -> stopped``)
    so tests step the ticker without sleeping; by default it waits on the
    ticker's own stop event. ``start`` is idempotent, ``stop`` joins (for at
    most ``join_timeout`` seconds) and is safe to call twice. A ``renew`` that
    raises is reported to ``on_error`` and the ticker keeps running: a renewal
    never fails the job."""

    def __init__(self, renew: Callable[[], object], interval_seconds: float,
                 wait: Callable[[float], bool] | None = None, *,
                 on_error: Callable[[BaseException], None] | None = None, join_timeout: float = 5.0) -> None:
        self._renew = renew
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._wait = wait if wait is not None else self._stop.wait
        self._on_error = on_error
        self._join_timeout = join_timeout
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="usage-renewal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._wait(self._interval) or self._stop.is_set():
                return
            try:
                self._renew()
            except Exception as exc:  # noqa: BLE001 -- a renewal never fails the job
                if self._on_error is not None:
                    try:
                        self._on_error(exc)
                    except Exception:  # noqa: BLE001
                        pass


def renewal_interval(reservation_seconds: float) -> float:
    """``min(60, reservation_seconds / 4)`` (D6)."""
    return min(60.0, reservation_seconds / 4)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CompleteResult:
    """What :func:`complete` did: ``status`` is ``charged``, ``not_started``
    or ``noop`` (``reason`` then says ``already_settled`` or
    ``unknown_token``); ``entry`` is the ledger entry of a charge."""

    status: str
    entry: dict | None = None
    reason: str | None = None


def _end_readings_from_block(block: Mapping | None) -> dict:
    """End readings built from the figures a job record's ``usage`` block
    carries (``five_hour_end``, ``weekly_end``, ``end_window_resets_at``,
    ``end_observed_at``); only used when the caller passes no readings."""
    if not block:
        return {}
    out: dict = {}
    observed = block.get("end_observed_at")
    for window, figure in ((FIVE_HOUR, "five_hour_end"), (WEEKLY, "weekly_end")):
        value = block.get(figure)
        if not _is_number(value):
            continue
        resets = block.get("end_window_resets_at") if window == FIVE_HOUR else None
        out[window] = {"provider": None, "window": window, "percent": float(value), "resets_at": resets,
                       "observed_at": observed, "source": "job-record"}
    return out


def _window_delta(start: float | None, start_resets: float | None, end: dict | None, created_at: float,
                  lower_start: float | None) -> tuple[str, float | None, float | None]:
    """``(kind, measured, after_reset)`` for one window (D6, Deltas). The
    end reading must be of the same window and strictly newer than the
    reservation to be ``measured``; one from a later window is
    ``after_reset``; anything else is ``unknown``."""
    if start is None or end is None:
        return "unknown", None, None
    if lower_start is not None and lower_start < start:
        start = lower_start
    end_resets = end.get("resets_at")
    if end_resets is None:
        same = end["percent"] >= start
        later = not same
    else:
        if start_resets is None:
            return "unknown", None, None
        same = same_window(start_resets, end_resets)
        later = end_resets > start_resets and not same
    if same:
        observed = end.get("observed_at")
        if observed is None or observed > created_at:
            return "measured", max(0.0, end["percent"] - start), None
        return "unknown", None, None
    if later:
        return "after_reset", None, end["percent"]
    return "unknown", None, None


def complete(runtime_root: str | os.PathLike, token: str, end_readings: Mapping | Iterable | None, outcome: str,
             *, now: float, usage_block: Mapping | None = None, stream_start: Mapping | Iterable | None = None) -> CompleteResult:
    """Release a reservation exactly once (D6).

    The baseline comes from the live reservation, else the lapsed one, and
    from nothing else: with neither, the call is a recorded no-op (silent when
    the token is already ``settled``) and nothing is charged, whatever
    ``usage_block`` says. ``usage_block`` supplies only the end figures when
    ``end_readings`` is ``None``. ``outcome="not_started"`` releases with no
    ledger entry, spend or ``settled`` mark. Otherwise, in one write: the
    deltas, the ledger entry, the run and repository charges, ``settled`` and
    the removal of the reservation. A missing or not-newer end reading is an
    ``unknown`` delta charged at the forecast, never zero."""
    with runtime.usage_lock(Path(runtime_root)):
        record = load_record(runtime_root)
        events = _maintain(record, now)
        reservation = record["reservations"].get(token) or record["lapsed"].get(token)
        if reservation is None:
            reason = "already_settled" if token in record["settled"] else "unknown_token"
            _save(runtime_root, record)
            _emit(runtime_root, events, now)
            if reason == "unknown_token":
                append_event(runtime_root, "usage_complete_unknown_token", now=now, token=token)
            else:
                append_event(runtime_root, "usage_complete_already_settled", now=now, token=token)
            return CompleteResult(COMPLETE_NOOP, None, reason)
        record["reservations"].pop(token, None)
        record["lapsed"].pop(token, None)
        if outcome == OUTCOME_NOT_STARTED:
            _save(runtime_root, record)
            _emit(runtime_root, events, now)
            return CompleteResult(COMPLETE_NOT_STARTED)
        baseline = reservation["baseline"]
        end = _by_window(end_readings) if end_readings is not None else _end_readings_from_block(usage_block)
        first = _by_window(stream_start)
        provider = reservation["provider"]
        kinds: dict = {}
        for window in WINDOWS:
            lower = first.get(window)
            lower_percent = None
            if lower is not None and baseline[window]["window_resets_at"] is not None \
                    and same_window(lower["resets_at"], baseline[window]["window_resets_at"]):
                lower_percent = lower["percent"]
            kinds[window] = _window_delta(baseline[window]["start"], baseline[window]["window_resets_at"],
                                          end.get(window), reservation["created_at"], lower_percent)
        reserved_five = reservation[FIVE_HOUR]["amount"]
        kind, measured, after = kinds[FIVE_HOUR]
        if kind == "measured":
            charged = measured
        elif kind == "after_reset":
            charged = max(after, reserved_five)
        else:
            charged = reserved_five
        weekly_kind, weekly_measured, weekly_after = kinds[WEEKLY]
        end_five = end.get(FIVE_HOUR)
        end_resets = end_five.get("resets_at") if end_five else None
        entry = _ledger_entry(
            token, reservation, now, end_resets=end_resets,
            five_hour=measured if kind == "measured" else None,
            weekly=weekly_measured if weekly_kind == "measured" else None,
            after_five=after if kind == "after_reset" else None,
            after_weekly=weekly_after if weekly_kind == "after_reset" else None,
            charged=charged, delta=kind)
        _append_ledger(record, entry)
        _charge_run(record, provider, reservation.get("run_id"), charged, now)
        ended = end_resets
        if ended is None:
            stored = record["readings"].get(provider, {}).get(FIVE_HOUR)
            ended = stored["resets_at"] if stored and stored["resets_at"] > now \
                else baseline[FIVE_HOUR]["window_resets_at"]
        start_window = baseline[FIVE_HOUR]["window_resets_at"]
        if start_window is not None and ended is not None and same_window(start_window, ended):
            repo_amount = charged
        elif after is not None:
            repo_amount = after
        else:
            repo_amount = reserved_five
        _charge_repository(record, provider, reservation.get("repository"), ended, repo_amount, now)
        record["settled"][token] = now
        _merge_into(record, [r for r in end.values() if r.get("provider") and r.get("resets_at") is not None])
        _save(runtime_root, record)
        _emit(runtime_root, events, now)
    return CompleteResult(COMPLETE_CHARGED, entry)


# ---------------------------------------------------------------------------
# Crash-safe replay
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Settled:
    """One reservation the sweep settled: the caller marks the job record
    ``accounting: "done"``."""

    token: str
    job_id: str
    action: str


def token_state(runtime_root: str | os.PathLike, token: str, *, now: float) -> str:
    """``present`` (a live or lapsed reservation exists), ``settled`` or
    ``orphan`` (neither), for the ``resume`` dispatch (D6)."""
    with runtime.usage_lock(Path(runtime_root)):
        record = load_record(runtime_root)
    if token in record["reservations"] or token in record["lapsed"]:
        return "present"
    if token in record["settled"]:
        return "settled"
    return "orphan"


def settle_pending(runtime_root: str | os.PathLike, load_job: Callable[[str], Mapping | None],
                   never_launched: Callable[[Mapping], bool], *, now: float,
                   is_terminal: Callable[[Mapping], bool]) -> list[Settled]:
    """Walk the reservations and lapsed entries that carry a ``job_id`` and
    settle those whose job record is **terminal** (D6, What the sweep
    settles). ``load_job``, ``never_launched`` and ``is_terminal`` are
    injected so this module stays a leaf. A non-terminal record is left to
    supervision; a reservation bound to a job with no record is released
    ``not_started`` once lapsed and never while live. Idempotent: ``complete``
    is a no-op for a settled token."""
    with runtime.usage_lock(Path(runtime_root)):
        record = load_record(runtime_root)
    _maintain(record, now)
    bound = [(token, r["job_id"], token in record["lapsed"])
             for section in ("reservations", "lapsed") for token, r in record[section].items() if r.get("job_id")]
    settled: list[Settled] = []
    for token, job_id, lapsed in bound:
        job = load_job(job_id)
        if job is None:
            if lapsed:
                complete(runtime_root, token, None, OUTCOME_NOT_STARTED, now=now)
                settled.append(Settled(token, job_id, "not_started"))
            continue
        if not is_terminal(job):
            continue
        block = job.get("usage") or {}
        accounting = block.get("accounting", "open")
        if accounting == "done":
            continue
        if accounting == "pending":
            complete(runtime_root, token, None, OUTCOME_OK, now=now, usage_block=block)
            settled.append(Settled(token, job_id, "charged"))
        elif never_launched(job):
            complete(runtime_root, token, None, OUTCOME_NOT_STARTED, now=now)
            settled.append(Settled(token, job_id, "not_started"))
        else:
            complete(runtime_root, token, {}, OUTCOME_OK, now=now)
            settled.append(Settled(token, job_id, "unknown"))
    return settled


# ---------------------------------------------------------------------------
# Read-only view
# ---------------------------------------------------------------------------


def snapshot(runtime_root: str | os.PathLike, *, now: float) -> dict:
    """The shared record as of ``now`` (rolled over, lapsed and pruned in
    memory), without writing anything."""
    record = load_record(runtime_root)
    _maintain(record, now)
    return record
