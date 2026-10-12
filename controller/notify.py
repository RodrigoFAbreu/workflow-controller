"""SignalHub notifications (``workflow-controller-signalhub-notifications``
CP1, ``docs/ai-workflow/CONTROLLER_SIGNALHUB_NOTIFICATIONS_PLAN.md`` D1-D8).

The Controller is a SignalHub producer: it turns what it already records
into a closed table of event types (:data:`EVENT_TABLE`), resolves each
type's enabled/category/severity from the settings file's
``notifications.events`` mapping over the lanes' built-in defaults
(:func:`resolve`), builds a deterministic request body and an
``Idempotency-Key`` from the occurrence's identity (:func:`build_event`,
:func:`idempotency_key`) and hands it to a bounded, asynchronous sender
(:class:`Notifier`) that can never block, fail, delay or change the
lifecycle: one daemon thread, a queue of 256, a bounded retry, a circuit
breaker, one outcome line per event in ``notifications.jsonl``.

Delivery never steers the lifecycle: nothing here raises into a caller,
nothing here takes a lifecycle lock or writes a job record, the state file
or the run's ``events.jsonl``. There is no persistent outbox (D6). Standard
library only. The API key is read once per process, held in memory and sent
only as ``Authorization: Bearer``; it is never written to any log line.

Imports ``controller.runtime`` and ``controller.errors`` only (never
``settings``, which imports this module to validate its mapping row).
Every path, clock and transport is a parameter, so no test touches the real
home, the network or a real SignalHub; with no notifier installed
:func:`emit` is a no-op.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from controller import runtime

# ---------------------------------------------------------------------------
# The closed event table (D2) and the kind/category/severity vocabulary (D3).
# ---------------------------------------------------------------------------

STATE, INFO, GATE, BLOCKER = "state", "info", "gate", "blocker"
KINDS = (STATE, INFO, GATE, BLOCKER)

CATEGORIES = ("ACTION_REQUIRED", "BLOCKED", "COMPLETED", "INFO")
SEVERITIES = ("LOW", "NORMAL", "HIGH", "CRITICAL")

#: The built-in (category, severity) of each kind: the lanes' own mapping.
KIND_DEFAULTS: Mapping[str, tuple[str, str]] = {
    STATE: ("INFO", "LOW"),
    INFO: ("INFO", "NORMAL"),
    GATE: ("ACTION_REQUIRED", "HIGH"),
    BLOCKER: ("BLOCKED", "CRITICAL"),
}

#: Each Controller event type and its kind (D2). The names reuse the lanes'
#: own where one exists, so an ``events.json`` entry ports unchanged.
EVENT_TABLE: Mapping[str, str] = {
    "step_done": STATE,
    "usage_paused": STATE,
    "usage_resumed": STATE,
    "usage_released": STATE,
    "usage_reservation_expired": STATE,
    "run_finished": STATE,
    "milestone_started": INFO,
    "milestone_planned": INFO,
    "approval_recorded": INFO,
    "milestone_accepted": INFO,
    "pr_merged": INFO,
    "release_published": INFO,
    "user_gate": GATE,
    "lane_blocked": BLOCKER,
}

#: Event types that are off unless the operator turns them on (O7:
#: ``usage_released`` is written for every job, so on by default every step
#: would send two notices).
DEFAULT_DISABLED = frozenset({"usage_released", "usage_reservation_expired"})

ENTRY_FIELDS = ("enabled", "category", "severity")
SECTION_KEYS = ("kinds", "events")

LANE = "workflow-controller"
DEFAULT_CONTEXT = "workflow-controller"

TITLE_LIMIT = 200
MESSAGE_LIMIT = 4000
CONTEXT_LIMIT = 200
KEY_LIMIT = 128
LINK_LIMIT = 2000

QUEUE_LIMIT = 256
BREAKER_FAILURES = 3
BREAKER_SECONDS = 300.0
#: A flush waits at most ``timeout_seconds`` plus this (D5); after an
#: interrupt it waits at most :data:`INTERRUPTED_FLUSH_SECONDS`.
FLUSH_MARGIN_SECONDS = 5.0
INTERRUPTED_FLUSH_SECONDS = 2.0

# Outcome statuses (D6).
SENT, DUPLICATE, CONFLICT, REJECTED, FAILED = "sent", "duplicate", "conflict", "rejected", "failed"
SKIPPED, DROPPED, NOT_CONFIGURED, DISABLED = "skipped", "dropped", "not_configured", "disabled"
OUTCOMES = (SENT, DUPLICATE, CONFLICT, REJECTED, FAILED, SKIPPED, DROPPED, NOT_CONFIGURED, DISABLED)

NOTIFICATIONS_LOG = "notifications.jsonl"

_CONTEXT_BAD = re.compile(r"[^A-Za-z0-9._:/-]")
_KEY_BAD = re.compile(r"[^A-Za-z0-9._:-]")


class EventsMappingError(ValueError):
    """A known field of ``notifications.events`` has a bad value. ``path``
    is the dotted path of the offending field; ``settings`` turns it into a
    ``SettingsError`` (exit 20)."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"notifications.events.{path}: {message}" if path else f"notifications.events: {message}")
        self.path = path
        self.reason = message


# ---------------------------------------------------------------------------
# Per-event resolution (D3).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Resolved:
    event_type: str
    kind: str
    enabled: bool
    category: str
    severity: str
    #: Which layer set each of the three fields last, for ``notifications``
    #: (D10): ``default``, ``kind`` or ``event``.
    sources: Mapping[str, str] = field(default_factory=dict)


def validate_events_mapping(value: Any) -> tuple[dict, list[str]]:
    """Validate the ``notifications.events`` row. Returns ``(clean,
    ignored)``: ``clean`` holds only known kinds, known event types and
    known fields with good values (``category``/``severity`` are
    case-insensitive and stored upper-case); ``ignored`` lists the dotted
    paths of unknown kinds, event types and fields, which the caller warns
    about once. A field whose name starts with ``_`` (the lanes' ``_help``,
    ``_kind``, ``_what``) is ignored silently, at every level. A known field
    with a bad value raises :class:`EventsMappingError`."""
    if value is None:
        return {"kinds": {}, "events": {}}, []
    if not isinstance(value, Mapping):
        raise EventsMappingError("", "must be an object")
    clean: dict = {"kinds": {}, "events": {}}
    ignored: list[str] = []
    for section, names in (("kinds", KINDS), ("events", tuple(EVENT_TABLE))):
        raw = value.get(section)
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise EventsMappingError(section, "must be an object")
        for name, entry in raw.items():
            if isinstance(name, str) and name.startswith("_"):
                continue
            path = f"{section}.{name}"
            if name not in names:
                ignored.append(path)
                continue
            if not isinstance(entry, Mapping):
                raise EventsMappingError(path, "must be an object")
            clean[section][name] = _validate_entry(path, entry, ignored)
    for key in value:
        if isinstance(key, str) and (key.startswith("_") or key in SECTION_KEYS):
            continue
        ignored.append(str(key))
    return clean, ignored


def _validate_entry(path: str, entry: Mapping, ignored: list[str]) -> dict:
    out: dict = {}
    for name, item in entry.items():
        if isinstance(name, str) and name.startswith("_"):
            continue
        if name == "enabled":
            if not isinstance(item, bool):
                raise EventsMappingError(f"{path}.enabled", "must be true or false")
            out["enabled"] = item
        elif name in ("category", "severity"):
            allowed = CATEGORIES if name == "category" else SEVERITIES
            if not isinstance(item, str) or item.upper() not in allowed:
                raise EventsMappingError(f"{path}.{name}", f"must be one of {', '.join(allowed)}")
            out[name] = item.upper()
        else:
            ignored.append(f"{path}.{name}")
    return out


def resolve(event_type: str, events_config: Mapping | None = None) -> Resolved:
    """Built-in kind default, then the file's kind entry, then the file's
    event entry, field by field (D3). ``events_config`` is a mapping already
    through :func:`validate_events_mapping`."""
    kind = EVENT_TABLE[event_type]
    category, severity = KIND_DEFAULTS[kind]
    enabled = event_type not in DEFAULT_DISABLED
    sources = {"enabled": "default", "category": "default", "severity": "default"}
    config = events_config or {}
    for layer, entry in (
        ("kind", (config.get("kinds") or {}).get(kind) or {}),
        ("event", (config.get("events") or {}).get(event_type) or {}),
    ):
        if "enabled" in entry:
            enabled, sources["enabled"] = entry["enabled"], layer
        if "category" in entry:
            category, sources["category"] = entry["category"], layer
        if "severity" in entry:
            severity, sources["severity"] = entry["severity"], layer
    return Resolved(event_type, kind, enabled, category, severity, sources)


def effective_table(events_config: Mapping | None = None) -> list[Resolved]:
    """Every event type resolved, in table order (the ``notifications``
    command's rows)."""
    return [resolve(name, events_config) for name in EVENT_TABLE]


# ---------------------------------------------------------------------------
# Identity, idempotency key, body (D4, D12).
# ---------------------------------------------------------------------------

def sanitize_context(text: str) -> str:
    """SignalHub's ``context`` rule: letters, digits and ``. _ : / -``,
    starting with a letter or digit, at most 200 characters. Other
    characters become ``-``, leading non-alphanumerics are stripped (a
    checkout directory ``.worktree`` gives ``worktree``), and an empty
    result is ``workflow-controller``."""
    cleaned = _CONTEXT_BAD.sub("-", text or "")
    cleaned = re.sub(r"^[^A-Za-z0-9]+", "", cleaned)[:CONTEXT_LIMIT]
    return cleaned or DEFAULT_CONTEXT


def repository_name(repo_root: str | os.PathLike) -> str:
    """The repository directory name as D12 sanitises it: the value the
    body's ``context`` and ``metadata.repository`` and every key's
    repository component carry."""
    return sanitize_context(Path(repo_root).name)


def context_for(repository: str, work_item: str | None = None) -> str:
    return sanitize_context(f"{repository}/{work_item}" if work_item else repository)


def identity(*parts: object) -> str:
    """A stable identity hash over ``parts`` (SHA-256 hex), for the
    identities D4 builds from several fields."""
    return hashlib.sha256("|".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def lifecycle_gate_identity(work_item: str, phase: str, state_revision: object, code: str) -> str:
    """A lifecycle gate (D4): work item + phase + ``state_revision`` + gate
    code, hashed, so a ``step`` meeting the same unchanged gate again gets
    the same key."""
    return identity("lifecycle-gate", work_item, phase, state_revision, code)


def branch_gate_identity(work_item: str, binding_generation: object, code: str, occurrence: object) -> str:
    """A branch gate (D4): work item + ``binding_generation`` + gate code +
    the gate's occurrence component (chosen per gate by the caller)."""
    return identity("branch-gate", work_item, binding_generation, code, occurrence)


def usage_identity(event: str, at: object, provider: object, token: object, run_id: object, repository: str) -> str:
    """A usage-events line (D4): ``event|at|provider|token|run_id|repository``."""
    return identity(event, at, provider, token, run_id, repository)


def usage_paused_identity(provider: object, window: object, repository: str, occurrence: object,
                          run_id: object = None) -> str:
    """A ``usage_paused`` hold (D4): never the line's ``at`` or ``reason``.
    ``occurrence`` is the chain's first ``resume_at`` (timed) or the cap
    hold's occurrence; ``run_id`` joins the identity for ``run_cap`` only."""
    return identity("usage_paused", provider, window, repository, occurrence, run_id if window == "run_cap" else None)


def idempotency_key(event_type: str, repository: str, ident: str) -> str:
    """``wc-<event type>-<repository>-<identity>``, limited to
    ``[A-Za-z0-9._:-]`` and 128 characters (a longer identity is replaced by
    its SHA-256)."""
    body = _KEY_BAD.sub("-", f"{repository}-{ident}")
    key = f"wc-{event_type}-{body}"
    if len(key) > KEY_LIMIT:
        key = f"wc-{event_type}-{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
    return key


@dataclass(frozen=True)
class Event:
    event_type: str
    key: str
    body: Mapping[str, Any]


def build_event(event_type: str, *, repository: str, ident: str, title: str,
                events_config: Mapping | None = None, work_item: str | None = None,
                message: str | None = None, link: str | None = None,
                occurred_at: str | None = None, metadata: Mapping[str, Any] | None = None) -> Event:
    """Build the request body and key of one event. The body is a
    deterministic function of the arguments (D4 b): ``title``, ``message``,
    ``metadata`` and ``link`` must carry no job id, run id, clock or other
    per-invocation value, and ``occurred_at`` (when given) is the
    occurrence's own recorded time. Category and severity come from
    :func:`resolve`."""
    resolved = resolve(event_type, events_config)
    meta: dict[str, Any] = {
        "lane": LANE, "repository": repository, "kind": resolved.kind, "event_type": event_type,
    }
    if work_item:
        meta["work_item"] = work_item
    meta.update(metadata or {})
    body: dict[str, Any] = {
        "category": resolved.category,
        "severity": resolved.severity,
        "title": (title or event_type)[:TITLE_LIMIT].strip() or event_type,
        "context": context_for(repository, work_item),
        "metadata": meta,
    }
    if message:
        body["message"] = message[:MESSAGE_LIMIT]
    if link and len(link) <= LINK_LIMIT and link.startswith(("http://", "https://")):
        body["link"] = link
    if occurred_at:
        body["occurredAt"] = occurred_at
    return Event(event_type, idempotency_key(event_type, repository, ident), body)


# ---------------------------------------------------------------------------
# Credentials (D7).
# ---------------------------------------------------------------------------

@dataclass
class Credentials:
    url: str | None = None
    url_source: str | None = None
    key: str | None = field(default=None, repr=False)
    key_source: str | None = None
    #: The kill switch file exists.
    disabled: bool = False
    disabled_path: str | None = None
    #: Where it looked, for the ``not_configured`` line and ``notifications``.
    tried: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)


def config_dir(env: Mapping[str, str] | None = None, home: str | os.PathLike | None = None) -> Path:
    """``$SIGNALHUB_CONFIG_DIR`` if set (the override exists so the suite can
    redirect it), else ``~/.config/signalhub``."""
    env = os.environ if env is None else env
    override = env.get("SIGNALHUB_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(home if home is not None else Path.home()) / ".config" / "signalhub"


def _first_line(path: Path, *, check_mode: list[str] | None = None) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
        if check_mode is not None:
            if path.stat().st_mode & 0o077:
                check_mode.append(f"the key file {path} is readable by group or others")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.strip().splitlines()
    return lines[0].strip() or None if lines else None


def find_credentials(*, env: Mapping[str, str] | None = None, url_setting: str | None = None,
                     api_key_file_setting: str | None = None,
                     home: str | os.PathLike | None = None) -> Credentials:
    """URL: ``$SIGNALHUB_URL``, else ``notifications.url``, else the first
    line of ``<config dir>/url``. Key: ``$SIGNALHUB_API_KEY``, else the file
    named by ``$SIGNALHUB_API_KEY_FILE``, else the file named by
    ``notifications.api_key_file``, else ``<config dir>/api-key`` (D7). The
    kill switch ``<config dir>/disabled`` is honoured. The key is returned,
    never logged."""
    env = os.environ if env is None else env
    directory = config_dir(env, home)
    cred = Credentials()
    switch = directory / "disabled"
    if switch.exists():
        cred.disabled, cred.disabled_path = True, str(switch)
    value = env.get("SIGNALHUB_URL")
    cred.tried.append("$SIGNALHUB_URL")
    if value:
        cred.url, cred.url_source = value, "$SIGNALHUB_URL"
    else:
        cred.tried.append("notifications.url")
        if url_setting:
            cred.url, cred.url_source = url_setting, "notifications.url"
        else:
            path = directory / "url"
            cred.tried.append(str(path))
            line = _first_line(path)
            if line:
                cred.url, cred.url_source = line, str(path)
    key = env.get("SIGNALHUB_API_KEY")
    cred.tried.append("$SIGNALHUB_API_KEY")
    if key:
        cred.key, cred.key_source = key, "$SIGNALHUB_API_KEY"
        return cred
    candidates: list[tuple[str, Path]] = []
    if env.get("SIGNALHUB_API_KEY_FILE"):
        candidates.append(("$SIGNALHUB_API_KEY_FILE", Path(env["SIGNALHUB_API_KEY_FILE"])))
    if api_key_file_setting:
        candidates.append(("notifications.api_key_file", Path(os.path.expanduser(api_key_file_setting))))
    candidates.append((str(directory / "api-key"), directory / "api-key"))
    for label, path in candidates:
        shown = label if label == str(path) else f"{label} ({path})"
        cred.tried.append(shown)
        line = _first_line(path, check_mode=cred.warnings)
        if line:
            cred.key, cred.key_source = line, shown
            break
    return cred


def redact_url(url: str | None) -> str | None:
    """``url`` without any user-info (``user:pass@``)."""
    return re.sub(r"^(https?://)[^/@]*@", r"\1", url) if url else url


# ---------------------------------------------------------------------------
# The sender (D5, D6).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NotifierConfig:
    enabled: bool = True
    url: str | None = None
    api_key_file: str | None = None
    timeout_seconds: int = 5
    attempts: int = 3
    retry_seconds: int = 2
    events: Mapping | None = None


@dataclass
class _Job:
    event: Event
    run_id: str | None
    recovered: bool


#: ``opener(request, timeout) -> response`` (a context manager with
#: ``status`` and ``read()``); raises ``urllib.error.HTTPError`` /
#: ``URLError`` / ``OSError`` as ``urllib.request.urlopen`` does.
Opener = Callable[[urllib.request.Request, float], Any]


class Notifier:
    """The bounded asynchronous sender. Construct, :meth:`start` happens
    lazily on the first event; every method is safe to call from the
    lifecycle thread and none raises."""

    def __init__(self, config: NotifierConfig, *, runtime_root: str | os.PathLike,
                 credentials: Credentials | None = None, env: Mapping[str, str] | None = None,
                 home: str | os.PathLike | None = None,
                 opener: Opener | None = None, sleeper: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 clock: Callable[[], str] | None = None) -> None:
        self.config = config
        self.runtime_root = Path(runtime_root)
        self.credentials = credentials if credentials is not None else find_credentials(
            env=env, url_setting=config.url, api_key_file_setting=config.api_key_file, home=home)
        self._opener = opener or (lambda request, timeout: urllib.request.urlopen(request, timeout=timeout))
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._queue: queue.Queue[_Job] = queue.Queue(maxsize=QUEUE_LIMIT)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._counts: collections.Counter[str] = collections.Counter()
        self._inert_logged = False
        self._consecutive_exhausted = 0
        self._breaker_until = 0.0
        self._flush_deadline: float | None = None
        self._interrupted = False
        self._warned = False

    # -- state ------------------------------------------------------------

    @property
    def inert_reason(self) -> str | None:
        """``disabled`` (master switch or kill switch), ``not_configured``
        (no URL or no key), else ``None``."""
        if not self.config.enabled or self.credentials.disabled:
            return DISABLED
        if not self.credentials.configured:
            return NOT_CONFIGURED
        return None

    @property
    def active(self) -> bool:
        return self.inert_reason is None

    def counts(self) -> dict[str, int]:
        """Outcome counts so far, plus the events still undrained as
        ``pending`` (D5, D6)."""
        with self._lock:
            out = dict(self._counts)
        pending = self._queue.unfinished_tasks
        if pending:
            out["pending"] = pending
        return out

    # -- logging ----------------------------------------------------------

    def _log_path(self, run_id: str | None) -> str:
        return f"runs/{run_id}/{NOTIFICATIONS_LOG}" if run_id else NOTIFICATIONS_LOG

    def _append(self, run_id: str | None, line: dict) -> None:
        line = {"at": self._clock(), **line}
        runtime.append_jsonl_best_effort(self.runtime_root, self._log_path(run_id), line)

    def _record(self, job: _Job, status: str, **extra: Any) -> None:
        with self._lock:
            self._counts[status] += 1
        self._append(job.run_id, {"status": status, "event_type": job.event.event_type,
                                  "idempotency_key": job.event.key, **extra})

    def note_run_opened(self, run_id: str) -> bool:
        """Write the per-run ``notifier_active`` line (D6), only when the
        notifier is configured with a URL and a key and not disabled.
        Returns whether the line was written."""
        if not self.active:
            return False
        return runtime.append_jsonl_best_effort(
            self.runtime_root, self._log_path(run_id),
            {"at": self._clock(), "status": "notifier_active", "url": redact_url(self.credentials.url),
             "url_source": self.credentials.url_source, "key_source": self.credentials.key_source})

    def _log_inert(self, run_id: str | None) -> None:
        with self._lock:
            if self._inert_logged:
                return
            self._inert_logged = True
        reason = self.inert_reason
        if reason == DISABLED:
            where = (self.credentials.disabled_path if self.credentials.disabled
                     else "notifications.enabled is false")
            self._append(run_id, {"status": DISABLED, "detail": f"disabled: {where}"})
        else:
            self._append(run_id, {"status": NOT_CONFIGURED,
                                  "detail": "no SignalHub URL or API key; looked in: " + ", ".join(self.credentials.tried)})
        with self._lock:
            self._counts[reason or NOT_CONFIGURED] += 1

    # -- enqueue ----------------------------------------------------------

    def emit(self, event: Event, *, run_id: str | None = None, recovered: bool = False) -> None:
        """Queue ``event`` for sending and return. Never raises, never
        blocks: an inert notifier records one line and drops the rest, a key
        already enqueued in this process is recorded ``duplicate`` (with
        ``source: "process"``), a full queue drops the new event with one
        ``dropped`` line."""
        try:
            self._emit(event, run_id, recovered)
        except Exception:  # noqa: BLE001 -- notification can never steer the lifecycle
            pass

    def _emit(self, event: Event, run_id: str | None, recovered: bool) -> None:
        if self.inert_reason is not None:
            self._log_inert(run_id)
            return
        job = _Job(event, run_id, recovered)
        if not resolve(event.event_type, self.config.events).enabled:
            self._record(job, DISABLED, detail="the event type is disabled")
            return
        with self._lock:
            if event.key in self._seen:
                duplicate = True
            else:
                self._seen.add(event.key)
                duplicate = False
        if duplicate:
            self._record(job, DUPLICATE, source="process")
            return
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._record(job, DROPPED, detail="the notification queue is full")
            return
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="signalhub-sender", daemon=True)
            self._thread.start()

    # -- sending ----------------------------------------------------------

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._send_job(job)
            except Exception as exc:  # noqa: BLE001 -- recorded, the process is unaffected
                try:
                    self._record(job, FAILED, detail=f"internal error: {type(exc).__name__}")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self._queue.task_done()

    def _send_job(self, job: _Job) -> None:
        now = self._monotonic()
        if now < self._breaker_until:
            self._record(job, SKIPPED, detail="circuit breaker open")
            return
        status, detail, extra = self._attempts(job)
        if status == FAILED:
            self._consecutive_exhausted += 1
            if self._consecutive_exhausted >= BREAKER_FAILURES:
                self._breaker_until = self._monotonic() + BREAKER_SECONDS
                self._consecutive_exhausted = 0
        else:
            self._consecutive_exhausted = 0
        self._record(job, status, detail=detail, **extra)

    def _request(self, job: _Job) -> urllib.request.Request:
        cred = self.credentials
        request = urllib.request.Request(
            str(cred.url).rstrip("/") + "/api/v1/events",
            data=json.dumps(job.event.body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {cred.key}"})
        request.add_header("Idempotency-Key", job.event.key[:200])
        return request

    def _attempts(self, job: _Job) -> tuple[str, str, dict]:
        """Up to ``attempts`` tries, waiting ``retry_seconds x 2^n`` between
        them; network errors, timeouts, 429 and 5xx are retried."""
        attempts = max(1, self.config.attempts)
        detail = ""
        http_status: int | None = None
        for n in range(attempts):
            if n:
                self._sleeper(self.config.retry_seconds * (2 ** (n - 1)))
            try:
                with self._opener(self._request(job), self.config.timeout_seconds) as response:
                    http_status = getattr(response, "status", None) or response.getcode()
                    response.read()
                return SENT, f"HTTP {http_status}", {"http_status": http_status, "attempts": n + 1}
            except urllib.error.HTTPError as exc:
                http_status = exc.code
                detail = f"HTTP {exc.code}"
                exc.close()
                if exc.code == 422:
                    if job.recovered:
                        return DUPLICATE, detail, {"source": "server", "http_status": 422, "attempts": n + 1}
                    return CONFLICT, detail + ": the key was used for a different body", \
                        {"http_status": 422, "attempts": n + 1}
                if exc.code == 429 or exc.code >= 500:
                    continue
                return REJECTED, detail, {"http_status": exc.code, "attempts": n + 1}
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                detail = f"unreachable: {type(reason).__name__}"
        return FAILED, detail or "no answer", {"http_status": http_status, "attempts": attempts}

    # -- flush ------------------------------------------------------------

    def flush(self, timeout: float | None = None, *, interrupted: bool = False) -> bool:
        """Wait for the queue to drain. The first flush of a process fixes
        a process-wide deadline of ``timeout_seconds + 5`` s; a later flush
        waits only for what remains (D5). After an interrupt the wait is at
        most 2 s, and a second Ctrl-C abandons it. Writes no per-event line
        for what it did not drain. Returns whether the queue drained."""
        try:
            now = self._monotonic()
            if self._flush_deadline is None:
                limit = (self.config.timeout_seconds + FLUSH_MARGIN_SECONDS) if timeout is None else timeout
                self._flush_deadline = now + limit
            remaining = max(0.0, self._flush_deadline - now)
            if interrupted or self._interrupted:
                remaining = min(remaining, INTERRUPTED_FLUSH_SECONDS)
            end = now + remaining
            with self._queue.all_tasks_done:
                while self._queue.unfinished_tasks:
                    left = end - self._monotonic()
                    if left <= 0:
                        return False
                    self._queue.all_tasks_done.wait(min(left, 0.05))
            return True
        except KeyboardInterrupt:
            self._interrupted = True  # D5: later flushes keep the interrupted bound
            return False
        except Exception:  # noqa: BLE001
            return False


# ---------------------------------------------------------------------------
# The process-wide installed notifier.
# ---------------------------------------------------------------------------

_installed: Notifier | None = None


def install(config: NotifierConfig, *, runtime_root: str | os.PathLike, **kwargs: Any) -> Notifier:
    """Install the process-wide notifier (set once in ``cli.main``)."""
    global _installed
    _installed = Notifier(config, runtime_root=runtime_root, **kwargs)
    return _installed


def uninstall() -> None:
    global _installed
    _installed = None


def installed() -> Notifier | None:
    return _installed


def emit(event: Event, *, run_id: str | None = None, recovered: bool = False) -> None:
    """Queue ``event`` on the installed notifier; a no-op with none
    installed and never raises."""
    notifier = _installed
    if notifier is not None:
        notifier.emit(event, run_id=run_id, recovered=recovered)


def flush(timeout: float | None = None, *, interrupted: bool = False) -> bool:
    notifier = _installed
    return True if notifier is None else notifier.flush(timeout, interrupted=interrupted)


def note_run_opened(run_id: str) -> bool:
    notifier = _installed
    return False if notifier is None else notifier.note_run_opened(run_id)


def send(event_type: str, *, repository: str, ident: str, title: str, work_item: str | None = None,
         message: str | None = None, link: str | None = None, occurred_at: str | None = None,
         metadata: Mapping[str, Any] | None = None, run_id: str | None = None, recovered: bool = False) -> None:
    """Build and queue one event on the installed notifier (the callers' one
    entry): a no-op with none installed, and never raises -- notification
    can never steer the lifecycle."""
    notifier = _installed
    if notifier is None:
        return
    try:
        event = build_event(event_type, repository=repository, ident=ident, title=title,
                            events_config=notifier.config.events, work_item=work_item, message=message,
                            link=link, occurred_at=occurred_at, metadata=metadata)
        notifier.emit(event, run_id=run_id, recovered=recovered)
    except Exception:  # noqa: BLE001
        pass


def summary() -> dict[str, int]:
    notifier = _installed
    return {} if notifier is None else notifier.counts()


def warn_once(notifier: Notifier, stream: Any = None) -> None:
    """Print the credential warnings (a world- or group-readable key file)
    once per notifier."""
    if notifier._warned or not notifier.credentials.warnings:
        return
    notifier._warned = True
    out = stream or sys.stderr
    for text in notifier.credentials.warnings:
        try:
            print(f"workflow-controller: warning: {text}", file=out, flush=True)
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "Event", "Notifier", "NotifierConfig", "Resolved", "Credentials", "EventsMappingError",
    "EVENT_TABLE", "KINDS", "build_event", "resolve", "validate_events_mapping", "install", "emit", "send", "flush",
]
