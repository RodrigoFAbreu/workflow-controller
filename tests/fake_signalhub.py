"""A fake SignalHub for the notification tests
(``workflow-controller-signalhub-notifications`` CP1).

An ``http.server`` on ``127.0.0.1:0`` in a thread. It records every request
(method, path, ``Authorization``, ``Idempotency-Key``, body) and answers
from a script, one scripted response per request (200 with an id, 429, 500,
400, 401, a delayed response, a connection reset). Unscripted requests
behave like the real server's idempotent publishing contract
(``SignalHub/docs/architecture.md``, "Idempotent publishing"): a key not
seen stores the event and answers ``201``; a used key with an **identical**
body answers ``200`` and stores nothing; a used key with a **different**
body answers ``422`` and stores nothing. It also applies SignalHub's
``context`` rule (letters, digits and ``. _ : / -``, starting with a letter
or digit, 1-200) and answers ``400`` for a body that breaks it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTEXT_RULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


class Step:
    """One scripted response: ``status`` (``None`` resets the connection),
    an optional ``delay`` before answering."""

    def __init__(self, status: int | None, *, delay: float = 0.0, body: dict | None = None) -> None:
        self.status, self.delay, self.body = status, delay, body


class FakeSignalHub:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.events: dict[str, dict] = {}
        self.script: list[Step] = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: D401 -- silence the test run
                pass

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                outer._handle(self, raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # -- lifecycle --------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "FakeSignalHub":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "FakeSignalHub":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- scripting --------------------------------------------------------

    def queue(self, *steps: Step | int | None) -> None:
        """Script the next responses, one per request."""
        with self.lock:
            self.script.extend(s if isinstance(s, Step) else Step(s) for s in steps)

    # -- recorded view ----------------------------------------------------

    def keys(self) -> list[str | None]:
        return [r["idempotency_key"] for r in self.requests]

    # -- the request ------------------------------------------------------

    def _handle(self, handler: BaseHTTPRequestHandler, raw: bytes) -> None:
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = None
        record = {
            "method": handler.command, "path": handler.path,
            "authorization": handler.headers.get("Authorization"),
            "idempotency_key": handler.headers.get("Idempotency-Key"),
            "content_type": handler.headers.get("Content-Type"),
            "body": body, "raw": raw,
        }
        with self.lock:
            self.requests.append(record)
            step = self.script.pop(0) if self.script else None
        if step is not None:
            if step.delay:
                time.sleep(step.delay)
            if step.status is None:
                handler.connection.close()
                return
            self._answer(handler, step.status, step.body or {"id": "scripted"})
            return
        self._answer(handler, *self._default(record))

    def _default(self, record: dict) -> tuple[int, dict]:
        if record["authorization"] is None or not record["authorization"].startswith("Bearer "):
            return 401, {"title": "Unauthorized", "violations": []}
        body = record["body"]
        if not isinstance(body, dict):
            return 400, {"title": "Bad Request", "violations": [{"field": "", "message": "malformed JSON"}]}
        context = body.get("context")
        if context is not None and not (isinstance(context, str) and CONTEXT_RULE.match(context)):
            return 400, {"title": "Bad Request", "violations": [{"field": "context", "message": "invalid"}]}
        if not body.get("title"):
            return 400, {"title": "Bad Request", "violations": [{"field": "title", "message": "required"}]}
        key = record["idempotency_key"]
        with self.lock:
            if key and key in self.events:
                if self.events[key] == body:
                    return 200, {"id": "stored-" + key}
                return 422, {"title": "Unprocessable Entity", "violations": []}
            if key:
                self.events[key] = body
        return 201, {"id": "new-" + (key or str(len(self.requests)))}

    @staticmethod
    def _answer(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        try:
            handler.end_headers()
            handler.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # the client gave up (a timeout test)
            pass
