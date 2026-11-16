"""Tests for ``controller.model_discovery`` (``workflow-controller-cm1`` CP1).

Hermetic: no test runs a real ``codex``; the runner and the resolver are
parameters.
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import model_discovery as md, review, routing, runtime  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402

NOW = datetime.datetime(2026, 11, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)
CODEX_BINARY = Path("/opt/codex/bin/codex")


def answer(*entries) -> bytes:
    return json.dumps({"models": [
        {"slug": slug, "visibility": visibility} for slug, visibility in entries
    ]}).encode()


GOOD = answer(("gpt-6.1-sol", "list"), ("gpt-6.1-terra", "list"), ("gpt-reserve", "hide"))


def result(stdout: bytes = GOOD, exit_code: int | None = 0, timed_out: bool = False) -> review.RunResult:
    return review.RunResult(exit_code, timed_out, stdout, b"", 1, 1)


class Runner:
    def __init__(self, outcome=None) -> None:
        self.outcome = result() if outcome is None else outcome
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": list(command), **kwargs})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def resolver(name: str) -> Path:
    return CODEX_BINARY


def unresolvable(name: str) -> Path:
    raise ReviewLaunchRefusedError(f"{name} cannot be resolved", evidence={})


class LookupTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cache = self.root / "model-catalog" / "codex.json"

    def look(self, runner: Runner | None = None, *, resolve=resolver, now=NOW, allow_run=True, environ=None):
        runner = Runner() if runner is None else runner
        record = md.lookup("codex", self.root, now=now, run=runner, resolve=resolve, allow_run=allow_run,
                           environ={"HOME": "/h", "CODEX_HOME": "/c"} if environ is None else environ)
        return record, runner

    def write_cache(self, **fields) -> None:
        body = {"schema_version": 1, "fetched_at": "2026-11-16T11:00:00Z", "models": ["cached-model"]}
        body.update(fields)
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps(body))

    def test_only_listed_models_are_kept_and_cached(self) -> None:
        record, runner = self.look()
        self.assertEqual(record.models, ("gpt-6.1-sol", "gpt-6.1-terra"))
        self.assertEqual(record.source, routing.SOURCE_DISCOVERED)
        self.assertEqual(record.fetched_at, "2026-11-16T12:00:00Z")
        self.assertEqual(runner.calls[0]["command"], [str(CODEX_BINARY), "debug", "models"])
        self.assertEqual(runner.calls[0]["cwd"], self.root)
        self.assertEqual(json.loads(self.cache.read_text()), {
            "schema_version": 1, "fetched_at": "2026-11-16T12:00:00Z", "models": ["gpt-6.1-sol", "gpt-6.1-terra"]})

    def test_unreadable_answers_are_unavailable(self) -> None:
        for name, raw in (
            ("not json", b"nope"), ("not utf-8", b"\xff\xfe"), ("not an object", b"[]"), ("no list", b"{}"),
            ("bad slug", answer(("-flag", "list"))), ("empty slug", answer(("", "list"))),
            ("empty list", answer(("gpt-reserve", "hide"))), ("oversize", b" " * (md.MAX_ANSWER_BYTES + 1)),
            ("entry not an object", json.dumps({"models": ["x"]}).encode()),
            ("deeply nested", b"[" * 200000),
        ):
            with self.subTest(name):
                record, _ = self.look(Runner(result(raw)))
                self.assertEqual(record.source, routing.SOURCE_UNAVAILABLE)
                self.assertTrue(record.reason)
                self.assertEqual(record.models, ())
                self.assertFalse(self.cache.exists())

    def test_failures_are_unavailable_with_a_reason(self) -> None:
        for name, runner, resolve in (
            ("unresolvable", Runner(), unresolvable),
            ("spawn OSError", Runner(PermissionError("EACCES")), resolver),
            ("missing cwd", Runner(FileNotFoundError("no dir")), resolver),
            ("non-zero exit", Runner(result(exit_code=2)), resolver),
            ("timeout", Runner(result(exit_code=None, timed_out=True)), resolver),
        ):
            with self.subTest(name):
                record, _ = self.look(runner, resolve=resolve)
                self.assertEqual(record.source, routing.SOURCE_UNAVAILABLE)
                self.assertTrue(record.reason)

    def test_a_fresh_cache_runs_nothing(self) -> None:
        self.write_cache()
        record, runner = self.look()
        self.assertEqual((record.models, record.source), (("cached-model",), routing.SOURCE_CACHE))
        self.assertEqual(runner.calls, [])

    def test_a_stale_cache_runs_the_lookup_and_is_rewritten(self) -> None:
        self.write_cache(fetched_at="2026-11-14T11:00:00Z")
        record, runner = self.look()
        self.assertEqual(record.source, routing.SOURCE_DISCOVERED)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(json.loads(self.cache.read_text())["fetched_at"], "2026-11-16T12:00:00Z")

    def test_a_failed_lookup_with_a_stale_cache_answers_stale(self) -> None:
        self.write_cache(fetched_at="2026-11-14T11:00:00Z")
        record, _ = self.look(Runner(result(exit_code=1)))
        self.assertEqual((record.models, record.source), (("cached-model",), routing.SOURCE_STALE))
        self.assertEqual(record.fetched_at, "2026-11-14T11:00:00Z")
        self.assertIn("exited with 1", record.reason)

    def test_a_deeply_nested_answer_with_a_stale_cache_answers_stale(self) -> None:
        self.write_cache(fetched_at="2026-11-14T11:00:00Z")
        record, _ = self.look(Runner(result(b"[" * 200000)))
        self.assertEqual((record.models, record.source), (("cached-model",), routing.SOURCE_STALE))

    def test_an_invalid_cache_counts_as_absent(self) -> None:
        for name, write in (
            ("not json", lambda: self._raw(b"{")),
            ("not utf-8", lambda: self._raw(b"\xff\xfe\xfd")),
            ("not an object", lambda: self._raw(b"[]")),
            ("wrong schema", lambda: self.write_cache(schema_version=2)),
            ("bad fetched_at", lambda: self.write_cache(fetched_at="yesterday")),
            ("fetched_at not a string", lambda: self.write_cache(fetched_at=5)),
            ("bad slug", lambda: self.write_cache(models=["ok", "-bad"])),
            ("empty models", lambda: self.write_cache(models=[])),
            ("models not a list", lambda: self.write_cache(models="x")),
            ("deeply nested", lambda: self._raw(b"[" * 200000)),
        ):
            with self.subTest(name):
                write()
                record, runner = self.look()
                self.assertEqual(record.source, routing.SOURCE_DISCOVERED)
                self.assertEqual(len(runner.calls), 1)
                self.assertEqual(json.loads(self.cache.read_text())["models"], ["gpt-6.1-sol", "gpt-6.1-terra"])

    def _raw(self, data: bytes) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(data)

    def test_a_fetched_at_after_now_is_stale(self) -> None:
        self.write_cache(fetched_at="2026-11-16T13:00:00Z")
        record, runner = self.look()
        self.assertEqual(record.source, routing.SOURCE_DISCOVERED)
        self.assertEqual(len(runner.calls), 1)
        self.write_cache(fetched_at="2026-11-16T13:00:00Z")
        record, _ = self.look(Runner(result(exit_code=1)))
        self.assertEqual(record.source, routing.SOURCE_STALE)

    def test_the_environment_is_minimal(self) -> None:
        _, runner = self.look(environ={"HOME": "/h", "CODEX_HOME": "/c", "SECRET": "x", "OPENAI_API_KEY": "k",
                                       md.OWNERSHIP_VAR: "outer"})
        env = runner.calls[0]["env"]
        self.assertEqual(set(env), {"PATH", "HOME", "CODEX_HOME", md.OWNERSHIP_VAR})
        self.assertEqual((env["HOME"], env["CODEX_HOME"]), ("/h", "/c"))
        self.assertTrue(env["PATH"].startswith("/opt/codex/bin:"))
        self.assertTrue(env["PATH"].endswith(review.SYSTEM_PATH))
        self.assertRegex(env[md.OWNERSHIP_VAR], r"^outer:model-lookup-\d+-[0-9a-f]{16}$")

    def test_a_missing_home_variable_is_left_out(self) -> None:
        _, runner = self.look(environ={})
        env = runner.calls[0]["env"]
        self.assertEqual(set(env), {"PATH", md.OWNERSHIP_VAR})
        self.assertRegex(env[md.OWNERSHIP_VAR], r"^model-lookup-\d+-[0-9a-f]{16}$")

    def test_two_lookups_get_different_tags(self) -> None:
        tags = []
        for _ in range(2):
            self.cache.unlink(missing_ok=True)
            _, runner = self.look(environ={md.OWNERSHIP_VAR: "outer"})
            tags.append(runner.calls[0]["env"][md.OWNERSHIP_VAR])
        self.assertNotEqual(*tags)
        self.assertTrue(all(t.startswith("outer:model-lookup-") for t in tags))

    def test_allow_run_false_runs_and_writes_nothing(self) -> None:
        record, runner = self.look(allow_run=False)
        self.assertEqual((record.source, runner.calls), (routing.SOURCE_NOT_LOOKED_UP, []))
        self.assertFalse(self.cache.exists())
        self.write_cache()
        record, runner = self.look(allow_run=False)
        self.assertEqual((record.source, runner.calls), (routing.SOURCE_CACHE, []))
        self.write_cache(fetched_at="2026-11-01T00:00:00Z")
        before = self.cache.read_text()
        record, runner = self.look(allow_run=False)
        self.assertEqual((record.source, record.models, runner.calls), (routing.SOURCE_STALE, ("cached-model",), []))
        self.assertEqual(self.cache.read_text(), before)
        self._raw(b"junk")
        record, _ = self.look(allow_run=False)
        self.assertEqual(record.source, routing.SOURCE_NOT_LOOKED_UP)

    def test_an_unwritable_cache_does_not_raise(self) -> None:
        with mock.patch.object(runtime, "write_json_best_effort", return_value=False) as write:
            record, _ = self.look()
        self.assertEqual(record.source, routing.SOURCE_DISCOVERED)
        write.assert_called_once()
        (self.root / "model-catalog").write_text("a file, not a directory")
        record, _ = self.look()
        self.assertEqual(record.source, routing.SOURCE_DISCOVERED)

    def test_a_harness_without_a_lookup_is_unsupported(self) -> None:
        runner = Runner()
        record = md.lookup("claude-code", self.root, now=NOW, run=runner, resolve=resolver)
        self.assertEqual((record.source, record.models, runner.calls), (routing.SOURCE_UNSUPPORTED, (), []))
        self.assertEqual(set(md.LOOKUPS), {"codex"})


if __name__ == "__main__":
    unittest.main()
