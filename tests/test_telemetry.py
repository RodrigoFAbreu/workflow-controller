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
import os
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

    def test_the_by_usage_names_one_grouping_and_the_values_are_unchanged(self) -> None:
        """Functional review F5: the usage reads ``--by GROUPING``, not
        ``{model,role,role,model}``; the help lists the three values, and
        an unknown one is still a usage error."""
        # Plain help text whatever the session's FORCE_COLOR says.
        with unittest.mock.patch.dict(os.environ, {"PYTHON_COLORS": "0"}):
            code, out, _err = self.cli("telemetry", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--by GROUPING", out)
        self.assertNotIn("{model,role,role,model}", out)
        self.assertIn("role, model, or role,model", " ".join(out.split()))
        code, _out, err = self.cli("telemetry", "--by", "effort")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice: 'effort'", err)
        for by in sorted(telemetry.GROUPINGS):
            self.assertEqual(self.cli("telemetry", "--by", by)[0], cli.EXIT_OK, by)

    def test_the_command_writes_nothing_and_refuses_a_bad_since(self) -> None:
        before = sorted(p.relative_to(self.runtime_root) for p in self.runtime_root.rglob("*"))
        self.assertEqual(self.cli("telemetry")[0], cli.EXIT_OK)
        self.assertEqual(sorted(p.relative_to(self.runtime_root) for p in self.runtime_root.rglob("*")), before)
        code, _out, err = self.cli("telemetry", "--since", "yesterday")
        self.assertEqual(code, 2)
        self.assertIn("not a UTC date or time", err)


#: The text of an ungrouped run and of each pre-C13 grouping over
#: ``GroupingTest``'s fixture, captured before the round columns existed.
_PRE_C13_TEXT = {None: 'jobs: 4\n'
       'all jobs: 4 job(s), telemetry unavailable 1\n'
       '  turns 12 (mean 4)\n'
       '  tokens 57 (mean 19)\n'
       '  cost $9.00 (mean $3.00)\n'
       '  API time 3 s (mean 1 s)\n'
       '  job wall time 180 s (mean 60 s)\n'
       '  worker wall time 150 s (mean 50 s)\n',
 'model': 'jobs: 4, grouped by model\n'
          'model=opus: 3 job(s), telemetry unavailable 1\n'
          '  turns 8 (mean 4)\n'
          '  tokens 38 (mean 19)\n'
          '  cost $6.00 (mean $3.00)\n'
          '  API time 2 s (mean 1 s)\n'
          '  job wall time 120 s (mean 60 s)\n'
          '  worker wall time 100 s (mean 50 s)\n'
          'model=sonnet: 1 job(s), telemetry unavailable 0\n'
          '  turns 4 (mean 4)\n'
          '  tokens 19 (mean 19)\n'
          '  cost $3.00 (mean $3.00)\n'
          '  API time 1 s (mean 1 s)\n'
          '  job wall time 60 s (mean 60 s)\n'
          '  worker wall time 50 s (mean 50 s)\n',
 'role': 'jobs: 4, grouped by role\n'
         'role=review-implementation: 1 job(s), telemetry unavailable 0\n'
         '  turns 6 (mean 6)\n'
         '  tokens 25 (mean 25)\n'
         '  cost $5.00 (mean $5.00)\n'
         '  API time 1 s (mean 1 s)\n'
         '  job wall time 60 s (mean 60 s)\n'
         '  worker wall time 50 s (mean 50 s)\n'
         'role=review-plan: 3 job(s), telemetry unavailable 1\n'
         '  turns 6 (mean 3)\n'
         '  tokens 32 (mean 16)\n'
         '  cost $4.00 (mean $2.00)\n'
         '  API time 2 s (mean 1 s)\n'
         '  job wall time 120 s (mean 60 s)\n'
         '  worker wall time 100 s (mean 50 s)\n',
 'role,model': 'jobs: 4, grouped by role,model\n'
               'role=review-implementation, model=opus: 1 job(s), telemetry unavailable 0\n'
               '  turns 6 (mean 6)\n'
               '  tokens 25 (mean 25)\n'
               '  cost $5.00 (mean $5.00)\n'
               '  API time 1 s (mean 1 s)\n'
               '  job wall time 60 s (mean 60 s)\n'
               '  worker wall time 50 s (mean 50 s)\n'
               'role=review-plan, model=opus: 2 job(s), telemetry unavailable 1\n'
               '  turns 2 (mean 2)\n'
               '  tokens 13 (mean 13)\n'
               '  cost $1.00 (mean $1.00)\n'
               '  API time 1 s (mean 1 s)\n'
               '  job wall time 60 s (mean 60 s)\n'
               '  worker wall time 50 s (mean 50 s)\n'
               'role=review-plan, model=sonnet: 1 job(s), telemetry unavailable 0\n'
               '  turns 4 (mean 4)\n'
               '  tokens 19 (mean 19)\n'
               '  cost $3.00 (mean $3.00)\n'
               '  API time 1 s (mean 1 s)\n'
               '  job wall time 60 s (mean 60 s)\n'
               '  worker wall time 50 s (mean 50 s)\n'}


class RoundsTest(_RuntimeCase):
    """review-convergence CP3: action, stage, round and round_source per job,
    and the action / stage / stage,round / work_item,action groupings."""

    def protocol_job(self, job_id: str, action: str, minute: int, *, phase: str, plan_revision: int | None = 1,
                     implementation_revision: int | None = 1, stored: dict | None = None, **fields) -> dict:
        snapshot = {"phase": phase, "plan_revision": plan_revision, "implementation_revision": implementation_revision}
        if stored is not None:
            snapshot["review_round"] = stored
        return self.job(
            job_id, created_at=f"2026-01-01T00:{minute:02d}:00Z", telemetry=_block(turns=1, cost=1.0),
            pre_state={"phase": phase, "plan_revision": plan_revision,
                       "implementation_revision": implementation_revision},
            protocol={"action_id": action, "decision": {"snapshot": snapshot}}, **fields)

    def by_id(self, **kw) -> dict:
        return {r["job_id"]: r for r in telemetry.rows(observe.list_jobs(self.runtime_root), **kw)}

    def test_text_and_json_of_the_existing_groupings_are_unchanged(self) -> None:
        self.job("j-1", telemetry=_block(turns=2, cost=1.0), created_at="2026-01-01T00:00:00Z")
        self.job("j-2", telemetry=_block(turns=4, cost=3.0, model="sonnet"), created_at="2026-01-02T00:00:00Z",
                 run_id="r-2")
        self.job("j-3", telemetry=dict(FAILED), created_at="2026-01-03T00:00:00Z", work_item_id="wi-2")
        self.job("j-4", telemetry=_block(turns=6, cost=5.0, role="review-implementation"),
                 created_at="2026-01-04T00:00:00Z")
        for by in (None, "role", "model", "role,model"):
            argv = ["telemetry"] + (["--by", by] if by else [])
            self.assertEqual(self.cli(*argv)[1], _PRE_C13_TEXT[by], by)
        old_keys = {"job_id", "created_at", "status", "target_repo", "work_item_id", "run_id", "role", "model",
                    "derived", "unavailable", "telemetry"}
        payload = json.loads(self.cli("--json", "telemetry")[1])
        for row in payload["rows"]:
            self.assertEqual(set(row) - old_keys, {"action", "stage", "round", "round_source", "review_mode", "lowered"})
            self.assertEqual((row["action"], row["stage"], row["round"], row["round_source"]),
                             (None, "none", None, None))

    def test_a_stored_round_is_the_workflow_s_and_a_missing_one_is_counted(self) -> None:
        self.protocol_job("j-a", "implementation.apply_review", 1, phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                          implementation_revision=1)
        self.protocol_job("j-b", "implementation.review.external", 2, phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                          implementation_revision=2)
        self.protocol_job("j-c", "implementation.apply_review", 3, phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                          implementation_revision=2, stored={"implementation": 7})
        rows = self.by_id()
        self.assertEqual((rows["j-a"]["stage"], rows["j-a"]["round"], rows["j-a"]["round_source"]),
                         ("implementation", 1, "jobs"))
        self.assertEqual((rows["j-b"]["action"], rows["j-b"]["round"], rows["j-b"]["round_source"]),
                         ("implementation.review.external", 2, "jobs"))
        self.assertEqual((rows["j-c"]["round"], rows["j-c"]["round_source"]), (7, "workflow"))

    def courier_job(self, job_id: str, action: str, minute: int, *, phase: str, plan_revision: int | None = 1,
                    implementation_revision: int | None = 1, **review_fields) -> dict:
        """A Controller external-review job as ``job.py`` writes it: ``kind``
        ``review``, ``review.action_id``, ``pre_state``, and no ``protocol``,
        ``worker``, ``worker_streams`` or ``telemetry`` block."""
        return self.job(
            job_id, created_at=f"2026-01-01T00:{minute:02d}:00Z", kind="review",
            pre_state={"phase": phase, "plan_revision": plan_revision,
                       "implementation_revision": implementation_revision},
            review={"stage": "plan" if action.startswith("plan") else "implementation", "action_id": action,
                    "launched": True, "outcome": "ingested", **review_fields})

    def test_review_mode_groups_review_jobs_and_reads_a_legacy_record_as_cross_model(self) -> None:
        phase = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
        self.courier_job("c-old", "plan.review.external", 1, phase=phase)
        self.courier_job("c-auto", "plan.review.external", 2, phase=phase, mode="auto")
        self.courier_job("c-low", "plan.review.external", 3, phase=phase, mode="single-provider", lowered=True)
        self.courier_job("c-low2", "plan.review.external", 4, phase=phase, mode="auto", lowered=True)
        self.protocol_job("p-1", "plan.review.local", 5, phase="AWAITING_LOCAL_PLAN_REVIEW")
        selected = telemetry.rows(observe.list_jobs(self.runtime_root))
        self.assertEqual({r["job_id"]: (r["review_mode"], r["lowered"]) for r in selected}, {
            "c-old": ("cross-model", False), "c-auto": ("auto", False), "c-low": ("single-provider", True),
            "c-low2": ("auto", True), "p-1": (None, False)})
        self.assertEqual([(g["key"], g["jobs"]) for g in telemetry.groups(selected, "review_mode")], [
            ({"review_mode": None, "lowered": False}, 1),
            ({"review_mode": "auto", "lowered": False}, 1), ({"review_mode": "auto", "lowered": True}, 1),
            ({"review_mode": "cross-model", "lowered": False}, 1),
            ({"review_mode": "single-provider", "lowered": True}, 1)])
        code, out, err = self.cli("telemetry", "--by", "review_mode")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("review_mode=cross-model, lowered=False: 1 job(s)", out)
        self.assertIn("review_mode=none, lowered=False: 1 job(s)", out)

    def test_a_courier_review_job_reports_its_action_stage_and_round(self) -> None:
        plan_phase = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
        impl_phase = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
        self.courier_job("c-1", "plan.review.external", 1, phase=plan_phase, plan_revision=1)
        self.courier_job("c-2", "plan.review.external", 2, phase=plan_phase, plan_revision=2)
        self.courier_job("c-3", "implementation.review.external", 3, phase=impl_phase, implementation_revision=1)
        self.courier_job("c-4", "implementation.review.external", 4, phase=impl_phase, implementation_revision=2)
        self.protocol_job("p-1", "plan.review.local", 5, phase="AWAITING_LOCAL_PLAN_REVIEW", plan_revision=3)
        for record in observe.list_jobs(self.runtime_root)[:4]:
            self.assertFalse(telemetry.is_worker_job(record))
            self.assertTrue(telemetry.is_courier_job(record))
        selected = telemetry.rows(observe.list_jobs(self.runtime_root))
        self.assertEqual([(r["job_id"], r["action"], r["stage"], r["round"], r["round_source"], r["unavailable"])
                          for r in selected], [
            ("c-1", "plan.review.external", "plan", 1, "jobs", True),
            ("c-2", "plan.review.external", "plan", 2, "jobs", True),
            ("c-3", "implementation.review.external", "implementation", 1, "jobs", True),
            ("c-4", "implementation.review.external", "implementation", 2, "jobs", True),
            ("p-1", "plan.review.local", "plan", 3, "jobs", False)])
        self.assertEqual([(g["key"], g["jobs"]) for g in telemetry.groups(selected, "stage,round")], [
            ({"stage": "implementation", "round": "1~"}, 1), ({"stage": "implementation", "round": "2~"}, 1),
            ({"stage": "plan", "round": "1~"}, 1), ({"stage": "plan", "round": "2~"}, 1),
            ({"stage": "plan", "round": "3~"}, 1)])
        self.assertEqual([(g["key"], g["jobs"]) for g in telemetry.groups(selected, "action")], [
            ({"action": "implementation.review.external"}, 2), ({"action": "plan.review.external"}, 2),
            ({"action": "plan.review.local"}, 1)])
        # The JSON stays additive: the same row keys as any other job.
        payload = json.loads(self.cli("--json", "telemetry", "--by", "stage")[1])
        self.assertEqual({tuple(sorted(row)) for row in payload["rows"]}, {tuple(sorted(selected[0]))})

    def test_a_review_job_that_kept_figures_is_counted_and_one_that_did_not_is_unavailable(self) -> None:
        phase = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
        kept = {"version": worker_stream.TELEMETRY_VERSION, "results": 2, "cost_usd": 0.5,
                "tokens": {"input": 10, "output": 20, "cache_creation": 30, "cache_read": 40}}
        self.courier_job("c-claude", "plan.review.external", 1, phase=phase, mode="single-provider", telemetry=kept)
        self.courier_job("c-codex", "plan.review.external", 2, phase=phase, mode="cross-model")
        rows = self.by_id()
        self.assertEqual((rows["c-claude"]["unavailable"], rows["c-codex"]["unavailable"]), (False, True))
        self.assertEqual(rows["c-claude"]["telemetry"], kept)
        grouped = {g["key"]["review_mode"]: g for g in telemetry.groups(list(rows.values()), "review_mode")}
        self.assertEqual((grouped["single-provider"]["cost_usd"]["total"], grouped["single-provider"]["tokens"]["total"],
                          grouped["single-provider"]["telemetry_unavailable"]), (0.5, 100, 0))
        self.assertEqual((grouped["cross-model"]["cost_usd"]["total"], grouped["cross-model"]["telemetry_unavailable"]),
                         (None, 1))
        text = self.cli("telemetry", "--by", "review_mode")[1]
        self.assertIn("cost $0.50", text)

    def synthetic_records(self, count: int) -> list[dict]:
        """``count`` job records over two repositories and three work items: plan and implementation
        actions, revisions that repeat and rise, a stored round now and then, a blocked gate."""
        import random
        rng = random.Random(7)
        phases = ["AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_PLAN_APPROVAL", "AWAITING_EXTERNAL_PLAN_REVIEW",
                  "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "IMPLEMENTING"]
        actions = ["plan.review.local", "plan.apply_review", "implementation.review.local",
                   "implementation.apply_review", "functional.apply_findings", None]
        records = []
        for index in range(count):
            phase = rng.choice(phases)
            pre = {"phase": phase, "plan_revision": rng.choice([None, 1, 2, 3, 4]),
                   "implementation_revision": rng.choice([None, 1, 1, 2, 3]),
                   "governing_workflow_version": rng.choice(["1", "2.2", None])}
            snapshot = dict(pre, **({"review_round": {"plan": rng.randint(1, 5), "implementation": 2}}
                                    if rng.random() < 0.1 else {}))
            record = {"job_id": f"s-{index:05d}", "created_at": f"2026-01-01T00:00:{index % 60:02d}.{index // 60:04d}Z",
                      "target_repo": rng.choice(["/r/a", "/r/b"]), "work_item_id": rng.choice(["w1", "w2", None]),
                      "pre_state": pre, "status": rng.choice(["finished", "GATE_BLOCKED", "failed"])}
            action = rng.choice(actions)
            if action is not None:
                record["protocol"] = {"action_id": action, "decision": {"snapshot": snapshot}}
            records.append(record)
        return records

    def test_the_one_pass_rounds_equal_the_count_over_each_record_s_own_prefix(self) -> None:
        from controller import review_rounds
        records = self.synthetic_records(400)
        ordered = sorted(records, key=review_rounds.record_key)
        columns = telemetry._round_columns(records)
        checked = 0
        for index, record in enumerate(ordered):
            action = record.get("protocol", {}).get("action_id")
            stage = review_rounds.stage_of(action)
            if stage not in (review_rounds.STAGE_PLAN, review_rounds.STAGE_IMPLEMENTATION):
                self.assertEqual(columns[id(record)][2:], (None, None))
                continue
            snapshot = record["protocol"]["decision"]["snapshot"]
            expected = review_rounds.current_round(snapshot, stage, ordered[:index + 1],
                                                   target_repo=record["target_repo"], work_item_id=record["work_item_id"])
            self.assertEqual(columns[id(record)][2:], expected, record["job_id"])
            checked += 1
        self.assertGreater(checked, 100)

    def test_the_rounds_are_computed_in_one_pass_not_one_prefix_per_record(self) -> None:
        import time
        from controller import review_rounds

        def best(count: int) -> float:
            records = self.synthetic_records(count)
            for record in records:  # one repository and one work item: the worst case for a prefix rescan
                record["target_repo"], record["work_item_id"] = "/r/a", "w1"
            times = []
            for _ in range(3):
                start = time.perf_counter()
                telemetry._round_columns(records)
                times.append(time.perf_counter() - start)
            return min(times)

        small, large = best(500), best(2000)
        # a linear pass grows about 4x for 4x the records, a prefix rescan about 16x; 10x is the line between them
        self.assertLess(large / max(small, 1e-6), 10, (small, large))
        self.assertTrue(hasattr(review_rounds, "RoundCounter"))

    def test_same_content_applies_do_not_raise_the_derived_round_and_a_filter_does_not_change_it(self) -> None:
        phase = "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"
        self.protocol_job("j-1", "implementation.apply_review", 1, phase=phase, implementation_revision=3)
        self.protocol_job("j-2", "implementation.apply_review", 2, phase=phase, implementation_revision=3)
        self.protocol_job("j-3", "functional.apply_findings", 3, phase=phase, implementation_revision=3)
        self.protocol_job("j-4", "implementation.review.local", 4, phase=phase, implementation_revision=4)
        rows = self.by_id()
        self.assertEqual([rows[j]["round"] for j in ("j-1", "j-2", "j-4")], [1, 1, 2])
        self.assertEqual((rows["j-3"]["stage"], rows["j-3"]["round"]), ("functional", None))
        # The cap gate's round for the same lifecycle comes from the same function.
        from controller import review_rounds
        records = observe.list_jobs(self.runtime_root)
        snapshot = {"phase": phase, "plan_revision": 1, "implementation_revision": 4}
        self.assertEqual(review_rounds.current_round(snapshot, "implementation", records,
                                                     target_repo=str(self.repo), work_item_id="wi-1"),
                         (rows["j-4"]["round"], "jobs"))
        self.assertEqual(self.by_id(since=telemetry.parse_since("2026-01-01T00:04:00Z"))["j-4"]["round"], 2)

    def test_a_derived_plan_round_is_a_lower_bound_kept_apart_from_the_workflow_s(self) -> None:
        phase = "AWAITING_LOCAL_PLAN_REVIEW"
        for number, (job_id, revision) in enumerate((("p-1", 1), ("p-2", 2), ("p-3", 3), ("p-4", 4)), 1):
            self.protocol_job(job_id, "plan.review.external", number, phase=phase, plan_revision=revision)
        # A round that published new content without advancing plan_revision,
        # then one the Workflow numbered itself.
        self.protocol_job("p-5", "plan.apply_review", 5, phase=phase, plan_revision=4)
        self.protocol_job("p-6", "plan.review.external", 6, phase=phase, plan_revision=4, stored={"plan": 4})
        selected = telemetry.rows(observe.list_jobs(self.runtime_root))
        self.assertEqual([(r["job_id"], r["round"], r["round_source"]) for r in selected],
                         [("p-1", 1, "jobs"), ("p-2", 2, "jobs"), ("p-3", 3, "jobs"), ("p-4", 4, "jobs"),
                          ("p-5", 4, "jobs"), ("p-6", 4, "workflow")])
        keys = [g["key"] for g in telemetry.groups(selected, "stage,round")]
        self.assertEqual(keys, [{"stage": "plan", "round": "1~"}, {"stage": "plan", "round": "2~"},
                                {"stage": "plan", "round": "3~"}, {"stage": "plan", "round": "4"},
                                {"stage": "plan", "round": "4~"}])
        sizes = {g["key"]["round"]: g["jobs"] for g in telemetry.groups(selected, "stage,round")}
        self.assertEqual((sizes["4"], sizes["4~"]), (1, 2))

    def test_the_groupings_total_a_plan_loop_by_stage_round(self) -> None:
        phase = "AWAITING_LOCAL_PLAN_REVIEW"
        minute = 0
        for revision in range(1, 6):
            minute += 1
            self.protocol_job(f"r-{revision}", "plan.review.external", minute, phase=phase, plan_revision=revision)
            if revision < 5:
                minute += 1
                self.protocol_job(f"a-{revision}", "plan.apply_review", minute, phase=phase,
                                  plan_revision=revision)
        selected = telemetry.rows(observe.list_jobs(self.runtime_root))
        self.assertEqual(len(selected), 9)
        grouped = telemetry.groups(selected, "stage,round")
        self.assertEqual([(g["key"]["round"], g["jobs"], g["cost_usd"]["total"]) for g in grouped],
                         [("1~", 2, 2.0), ("2~", 2, 2.0), ("3~", 2, 2.0), ("4~", 2, 2.0), ("5~", 1, 1.0)])
        by_action = {g["key"]["action"]: g["jobs"] for g in telemetry.groups(selected, "action")}
        self.assertEqual(by_action, {"plan.apply_review": 4, "plan.review.external": 5})
        [by_stage] = telemetry.groups(selected, "stage")
        self.assertEqual((by_stage["key"], by_stage["jobs"]), ({"stage": "plan"}, 9))
        pairs = telemetry.groups(selected, "work_item,action")
        self.assertEqual([g["key"] for g in pairs], [
            {"work_item_id": "wi-1", "action": "plan.apply_review"},
            {"work_item_id": "wi-1", "action": "plan.review.external"}])

    def test_the_new_groupings_in_text_and_the_usage(self) -> None:
        self.protocol_job("j-1", "plan.review.external", 1, phase="AWAITING_LOCAL_PLAN_REVIEW")
        self.job("j-old", telemetry=_block(), created_at="2026-02-01T00:00:00Z")
        for by, expected in (("stage,round", "stage=plan, round=1~: 1 job(s)"),
                             ("stage,round", "stage=none, round=none: 1 job(s)"),
                             ("action", "action=none: 1 job(s)"),
                             ("work_item,action", "work_item=wi-1, action=plan.review.external: 1 job(s)")):
            code, out, err = self.cli("telemetry", "--by", by)
            self.assertEqual(code, cli.EXIT_OK, err)
            self.assertIn(expected, out)
        before = sorted(p.relative_to(self.runtime_root) for p in self.runtime_root.rglob("*"))
        record = (self.runtime_root / "jobs" / "j-1.json").read_bytes()
        self.cli("telemetry", "--by", "stage")
        self.assertEqual((self.runtime_root / "jobs" / "j-1.json").read_bytes(), record)
        self.assertEqual(sorted(p.relative_to(self.runtime_root) for p in self.runtime_root.rglob("*")), before)
        self.assertNotIn("None", self.cli("telemetry", "--by", "stage,round")[1])


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
