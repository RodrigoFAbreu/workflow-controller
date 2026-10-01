"""Tests for telemetry v0's reading side (``controller.telemetry``,
``workflow-controller-settings-and-telemetry`` CP3, Design C).

Covers the derivation of a record written before telemetry from its
``worker.stdout``, the rows and their filters, the grouping, the
``telemetry`` command's text and ``--json`` output, and the presentation of
a block -- figures, or ``telemetry unavailable`` for a ``failed`` one -- in
``status``, ``inspect``, ``follow`` and ``telemetry``.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from controller import cli, job, observe, runtime, telemetry, worker_stream  # noqa: E402
from tests import fake_claude, fixtures  # noqa: E402
from tests.test_cli import FAKE_IDENTITY, _Args, _build_managed_target  # noqa: E402


def _result(turns: int, cost: float, api: int, model: str = "claude-test") -> dict:
    return fake_claude.result_event(
        num_turns=turns, total_cost_usd=cost, duration_api_ms=api, duration_ms=1000 * turns,
        usage={"input_tokens": turns, "output_tokens": 2 * turns, "cache_creation_input_tokens": 3,
               "cache_read_input_tokens": 4},
        modelUsage={model: {"inputTokens": turns, "outputTokens": 2 * turns, "cacheCreationInputTokens": 3,
                            "cacheReadInputTokens": 4, "costUSD": cost}})


def _block(**figures) -> dict:
    """A recorded block: a session plus wall times and dimensions."""
    block = worker_stream.session_telemetry([_result(figures.pop("turns", 2), figures.pop("cost", 1.0),
                                                     figures.pop("api", 1000))])
    block.update(job_seconds=60, worker_seconds=50, role="review-plan", model="opus", effort="high",
                 harness="claude-code", workflow_version="2.6.0", controller_version="1.5.0")
    block.update(figures)
    return block


FAILED = telemetry.failed_block(RuntimeError("injected"))


class _RuntimeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir()
        self.repo = fixtures.build_target_git_repo(self.tmp_root / "repo").resolve()

    def job(self, job_id: str, *, created_at: str = "2026-01-01T00:00:00Z", **fields) -> dict:
        record = {"job_id": job_id, "target_repo": str(self.repo), "status": job.STATUS_FINISHED,
                  "created_at": created_at, "work_item_id": "wi-1", "run_id": "r-1",
                  "target_workflow_version": "2.6.0", "controller_runtime": {"version": "1.4.2"},
                  "worker_route": {"role": "review-plan", "model": "opus", "effort": "high"}, **fields}
        runtime.write_json(self.runtime_root, f"jobs/{job_id}.json", record)
        return record

    def legacy_job(self, job_id: str, results: list[dict], events: list[tuple[str, str]], **fields) -> dict:
        """A record written before telemetry, with its stream and events."""
        directory = self.runtime_root / "jobs" / job_id
        directory.mkdir(parents=True)
        (directory / "worker.stdout").write_text("".join(json.dumps(r) + "\n" for r in results))
        for seq, (name, at) in enumerate(events, 1):
            runtime.append_jsonl(self.runtime_root, f"jobs/{job_id}/events.jsonl",
                                 {"v": 1, "seq": seq, "at": at, "job_id": job_id, "event": name})
        streams = {"stdout_path": str(directory / "worker.stdout"), "stderr_path": str(directory / "worker.stderr"),
                   "events_path": str(directory / "events.jsonl")}
        return self.job(job_id, worker_streams=streams, worker={"num_turns": 1}, **fields)

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["--runtime-dir", str(self.runtime_root), *argv])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()


class DerivationTest(_RuntimeCase):
    def test_a_record_without_telemetry_is_derived_from_its_stream(self) -> None:
        record = self.legacy_job("j-old", [_result(5, 1.0, 100), _result(3, 2.5, 300)], [
            ("planned", "2026-01-01T00:00:00Z"), ("worker_spawned", "2026-01-01T00:00:05Z"),
            ("completed", "2026-01-01T00:02:00Z")])
        before = (self.runtime_root / "jobs" / "j-old.json").read_bytes()
        block = telemetry.record_block(record)
        self.assertTrue(block["derived"])
        self.assertEqual((block["results"], block["turns"], block["cost_usd"], block["duration_api_ms"]),
                         (2, 8, 2.5, 300))
        self.assertEqual((block["job_seconds"], block["worker_seconds"]), (120, 115))
        self.assertEqual((block["role"], block["controller_version"]), ("review-plan", "1.4.2"))
        # Nothing is written back.
        self.assertEqual((self.runtime_root / "jobs" / "j-old.json").read_bytes(), before)

    def test_a_drained_worker_ends_at_worker_exited(self) -> None:
        record = self.legacy_job("j-drain", [_result(1, 1.0, 10)], [
            ("worker_spawned", "2026-01-01T00:00:00Z"), ("worker_exited", "2026-01-01T00:00:40Z"),
            ("completed", "2026-01-01T00:01:00Z")])
        self.assertEqual(telemetry.record_block(record)["worker_seconds"], 40)

    def test_a_job_with_no_stream_contributes_null_figures(self) -> None:
        record = self.job("j-gone", worker_streams={"stdout_path": str(self.tmp_root / "missing")})
        block = telemetry.record_block(record)
        self.assertTrue(block["derived"])
        self.assertEqual((block["results"], block["turns"], block["cost_usd"]), (0, None, None))
        self.assertEqual(block["problems"], [{"kind": "no_stream", "path": str(self.tmp_root / "missing")}])
        self.assertTrue(telemetry.unavailable(block))

    def test_a_recorded_block_is_read_as_is_and_no_worker_means_no_block(self) -> None:
        self.assertEqual(telemetry.record_block(self.job("j-new", telemetry=_block())), _block())
        self.assertIsNone(telemetry.record_block(self.job("j-gate", status=job.STATUS_GATE_BLOCKED)))

    def test_a_derivation_failure_is_a_failed_block(self) -> None:
        record = self.legacy_job("j-x", [_result(1, 1.0, 10)], [])
        with unittest.mock.patch.object(telemetry.worker_stream, "session_telemetry", side_effect=TypeError("x")):
            block = telemetry.record_block(record)
        self.assertEqual((block["failed"], block["derived"]), (True, True))


class GroupingTest(_RuntimeCase):
    def setUp(self) -> None:
        super().setUp()
        self.job("j-1", telemetry=_block(turns=2, cost=1.0), created_at="2026-01-01T00:00:00Z")
        self.job("j-2", telemetry=_block(turns=4, cost=3.0, model="sonnet"), created_at="2026-01-02T00:00:00Z",
                 run_id="r-2")
        self.job("j-3", telemetry=dict(FAILED), created_at="2026-01-03T00:00:00Z", work_item_id="wi-2")
        self.job("j-4", telemetry=_block(turns=6, cost=5.0, role="review-implementation"),
                 created_at="2026-01-04T00:00:00Z")

    def test_filters(self) -> None:
        records = observe.list_jobs(self.runtime_root)
        ids = lambda **kw: [r["job_id"] for r in telemetry.rows(records, **kw)]  # noqa: E731
        self.assertEqual(ids(), ["j-1", "j-2", "j-3", "j-4"])
        self.assertEqual(ids(work_item="wi-2"), ["j-3"])
        self.assertEqual(ids(run="r-2"), ["j-2"])
        self.assertEqual(ids(since=telemetry.parse_since("2026-01-03")), ["j-3", "j-4"])
        self.assertEqual(ids(target_repo="/elsewhere"), [])

    def test_groups_total_and_mean_and_count_the_unavailable(self) -> None:
        selected = telemetry.rows(observe.list_jobs(self.runtime_root))
        [everything] = telemetry.groups(selected)
        self.assertEqual((everything["jobs"], everything["telemetry_unavailable"]), (4, 1))
        self.assertEqual(everything["cost_usd"], {"total": 9.0, "mean": 3.0, "jobs": 3})
        self.assertEqual(everything["turns"], {"total": 12, "mean": 4.0, "jobs": 3})
        by_model = {g["key"]["model"]: g for g in telemetry.groups(selected, "model")}
        self.assertEqual(sorted(by_model), ["opus", "sonnet"])
        self.assertEqual(by_model["opus"]["cost_usd"]["total"], 6.0)
        self.assertEqual(by_model["opus"]["telemetry_unavailable"], 1)
        keys = [g["key"] for g in telemetry.groups(selected, "role,model")]
        self.assertEqual(keys, [{"role": "review-implementation", "model": "opus"},
                                {"role": "review-plan", "model": "opus"},
                                {"role": "review-plan", "model": "sonnet"}])

    def test_the_command_json(self) -> None:
        code, out, err = self.cli("--json", "telemetry", "--by", "model", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK, err)
        payload = json.loads(out)
        self.assertEqual(payload["by"], "model")
        self.assertEqual([r["job_id"] for r in payload["rows"]], ["j-1", "j-2", "j-3", "j-4"])
        self.assertEqual([r["unavailable"] for r in payload["rows"]], [False, False, True, False])
        self.assertEqual([g["key"] for g in payload["groups"]], [{"model": "opus"}, {"model": "sonnet"}])
        code, out, _err = self.cli("--json", "--work-item", "wi-2", "telemetry")
        self.assertEqual([r["job_id"] for r in json.loads(out)["rows"]], ["j-3"])

    def test_the_command_text(self) -> None:
        code, out, err = self.cli("telemetry", "--by", "role", "--since", "2026-01-02T00:00:00Z")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertEqual(out.splitlines()[0], "jobs: 3, grouped by role")
        self.assertIn("role=review-plan: 2 job(s), telemetry unavailable 1", out)
        self.assertIn("  cost $3.00 (mean $3.00)", out)
        self.assertIn("role=review-implementation: 1 job(s), telemetry unavailable 0", out)

    def test_a_missing_model_or_role_reads_as_a_word_in_text_and_null_in_json(self) -> None:
        """Functional review F1: a route that inherits the model groups as
        ``model=inherit`` (a job with no route as ``role=none``), never
        Python's ``None``; ``--json`` keeps ``null``."""
        self.job("j-5", telemetry=_block(turns=1, cost=1.0, role="milestone-plan", model=None),
                 created_at="2026-01-05T00:00:00Z")
        self.job("j-6", telemetry=_block(turns=1, cost=1.0, role=None, model=None),
                 created_at="2026-01-06T00:00:00Z")
        code, out, err = self.cli("telemetry", "--by", "role,model", "--since", "2026-01-05")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("role=milestone-plan, model=inherit: 1 job(s), telemetry unavailable 0", out)
        self.assertIn("role=none, model=inherit: 1 job(s), telemetry unavailable 0", out)
        self.assertNotIn("None", out)
        code, out, err = self.cli("--json", "telemetry", "--by", "role,model", "--since", "2026-01-05")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertEqual([g["key"] for g in json.loads(out)["groups"]],
                         [{"role": None, "model": None}, {"role": "milestone-plan", "model": None}])

    def test_the_command_writes_nothing_and_refuses_a_bad_since(self) -> None:
        before = sorted(p.relative_to(self.runtime_root) for p in self.runtime_root.rglob("*"))
        self.assertEqual(self.cli("telemetry")[0], cli.EXIT_OK)
        self.assertEqual(sorted(p.relative_to(self.runtime_root) for p in self.runtime_root.rglob("*")), before)
        code, _out, err = self.cli("telemetry", "--since", "yesterday")
        self.assertEqual(code, 2)
        self.assertIn("not a UTC date or time", err)


class PresentationTest(_RuntimeCase):
    def status(self) -> str:
        pre_existing = cli._capture_pre_existing_state(self.runtime_root)
        pre_existing["ladder_row"] = 1
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_status(_Args(str(self.repo)), self.runtime_root, FAKE_IDENTITY, pre_existing=pre_existing)
        return out.getvalue()

    def test_summary_text(self) -> None:
        self.assertEqual(telemetry.summary_text(_block(turns=144, cost=11.66, api=1320000)),
                         "cost $11.66, 144 turns, 439 tokens, API 1320 s, job 60 s, worker 50 s")
        self.assertEqual(telemetry.summary_text(telemetry.completed_summary(_block(turns=2))),
                         "cost $1.00, 2 turns, 13 tokens, API 1 s, job 60 s, worker 50 s")
        for block in (FAILED, "failed", None, worker_stream.session_telemetry([])):
            self.assertEqual(telemetry.summary_text(block), "telemetry unavailable")

    def test_status_prints_the_newest_job_with_a_block(self) -> None:
        self.job("j-1", telemetry=_block(cost=2.0), created_at="2026-01-01T00:00:00Z")
        self.job("j-2", created_at="2026-01-02T00:00:00Z")  # no block: skipped
        self.assertIn("last job telemetry: j-1 (FINISHED, wi-1): cost $2.00, 2 turns", self.status())
        self.job("j-3", telemetry=dict(FAILED), created_at="2026-01-03T00:00:00Z")
        self.assertIn("last job telemetry: j-3 (FINISHED, wi-1): telemetry unavailable\n", self.status())

    def test_a_job_with_no_work_item_reads_none(self) -> None:
        """Functional review F1: ``(FAILED, none)``, as the ``jobs:`` lines
        say ``work item none``; ``--json`` keeps ``null``."""
        self.job("j-1", telemetry=dict(FAILED), status=job.STATUS_FAILED, work_item_id=None)
        text = self.status()
        self.assertIn("last job telemetry: j-1 (FAILED, none): telemetry unavailable\n", text)
        self.assertNotIn("None", text)
        last = telemetry.last_finished(observe.list_jobs(self.runtime_root))
        self.assertIsNone(telemetry.last_job_entry(last)["work_item_id"])

    def test_status_without_a_block_prints_no_line(self) -> None:
        self.job("j-1", created_at="2026-01-01T00:00:00Z")
        self.assertNotIn("last job telemetry", self.status())

    def test_follow_completed_line(self) -> None:
        base = {"event": "completed", "job_id": "j-1", "outcome": "SUCCESS", "exit_code": 0}
        self.assertEqual(observe._job_text(base), "job j-1 COMPLETED (worker SUCCESS, exit 0)")
        summary = telemetry.completed_summary(_block(turns=3, cost=1.25))
        self.assertEqual(observe._job_text({**base, "telemetry": summary}),
                         "job j-1 COMPLETED (worker SUCCESS, exit 0); session: cost $1.25, 3 turns, 16 tokens, "
                         "API 1 s, job 60 s, worker 50 s")
        self.assertEqual(observe._job_text({**base, "telemetry": "failed"}),
                         "job j-1 COMPLETED (worker SUCCESS, exit 0); session: telemetry unavailable")

    def test_last_finished_is_per_target(self) -> None:
        self.job("j-1", telemetry=_block(cost=4.0))
        self.job("j-other", telemetry=_block(cost=9.0), target_repo="/elsewhere", created_at="2026-02-01T00:00:00Z")
        self.job("j-2", telemetry=dict(FAILED), created_at="2026-01-05T00:00:00Z", status=job.STATUS_FAILED)
        last = telemetry.last_finished(observe.list_jobs(self.runtime_root), target_repo=str(self.repo))
        self.assertEqual(telemetry.last_job_text(last), "last job telemetry: j-2 (FAILED, wi-1): telemetry unavailable")
        self.assertEqual(telemetry.last_finished(observe.list_jobs(self.runtime_root))["job_id"], "j-other")


class InspectLastJobTest(unittest.TestCase):
    """``inspect``'s ``last_job``: the target's newest job with a telemetry
    block, omitted when there is none."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_root = Path(self._tmp.name)
        self.repo = _build_managed_target(tmp_root, phase="PLANNING")
        self.stub_manager = fixtures.write_stub_workflow_manager(tmp_root / "workflow-manager")
        self.runtime_root = tmp_root / "runtime"

    def inspect(self, *, json_out: bool = False) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.cmd_inspect(_Args(str(self.repo), workflow_manager=str(self.stub_manager), json_out=json_out),
                                   self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(code, cli.EXIT_OK)
        return out.getvalue()

    def test_last_job(self) -> None:
        self.assertNotIn("last job telemetry", self.inspect())
        self.assertNotIn("last_job_telemetry", json.loads(self.inspect(json_out=True)))
        runtime.write_json(self.runtime_root, "jobs/j-1.json", {
            "job_id": "j-1", "target_repo": str(self.repo.resolve()), "status": job.STATUS_FINISHED,
            "created_at": "2026-01-01T00:00:00Z", "work_item_id": "wi-1", "telemetry": _block(cost=4.0)})
        self.assertIn("last job telemetry: j-1 (FINISHED, wi-1): cost $4.00, 2 turns", self.inspect())
        payload = json.loads(self.inspect(json_out=True))
        self.assertEqual(payload["last_job_telemetry"], {
            "job_id": "j-1", "status": job.STATUS_FINISHED, "work_item_id": "wi-1",
            "telemetry": telemetry.completed_summary(_block(cost=4.0))})
        runtime.write_json(self.runtime_root, "jobs/j-2.json", {
            "job_id": "j-2", "target_repo": str(self.repo.resolve()), "status": job.STATUS_FAILED,
            "created_at": "2026-01-02T00:00:00Z", "work_item_id": "wi-1", "telemetry": dict(FAILED)})
        self.assertIn("last job telemetry: j-2 (FAILED, wi-1): telemetry unavailable", self.inspect())
        self.assertEqual(json.loads(self.inspect(json_out=True))["last_job_telemetry"]["telemetry"], "failed")

if __name__ == "__main__":
    unittest.main()
