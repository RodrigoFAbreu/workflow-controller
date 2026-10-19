"""The ordinary test suite runs every registered lifecycle scenario once
(``workflow-controller-automated-lifecycle-scenarios`` D3, D6).

The shard planner's atom is a ``TestCase`` class (or a whole module that has
module fixtures), so a generated *method* is not an atom. This module therefore
generates **one separately named class per scenario variant**, no module
fixtures, and ``ScenarioInventoryTest`` asserts every registered variant has its
own planner key.

If the sandbox is unavailable no scenario runs: locally each atom is skipped
with the reason naming the failed step, and under ``CI`` it fails with the same
text, so a green CI run always means the namespace enforcement was exercised.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.scenarios import inventory, kit

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Read from the scenario modules' source: importing them here would run their top-level code on the host, before the
#: isolation gate below could apply.
FLOW_IDS = inventory.catalogue_flow_ids()
MAX_CI_SHARDS = 4


def class_name(flow_id: str) -> str:
    """``scenario-kill-and-resume-job-before`` -> ``Scenario_scenario_kill_and_resume_job_before``."""
    return "Scenario_" + re.sub(r"[^0-9A-Za-z]+", "_", flow_id)


def scenario_class(flow_id: str) -> type:
    """A ``TestCase`` class that runs ``flow_id`` once, in its own sandbox process."""

    def test_scenario(self) -> None:
        decision, reason = kit.isolation_gate(kit.probe_isolation(), os.environ)
        if decision == "skip":
            self.skipTest(reason)
        if decision == "fail":
            self.fail(reason)
        outcome = kit.run_scenario(flow_id)
        self.assertEqual(outcome.problems, [], outcome.result)
        self.assertTrue(outcome.unchanged, "the real checkout changed")
        self.assertEqual(outcome.result.get("status"), "passed", outcome.result.get("message") or outcome.log)

    name = class_name(flow_id)
    return type(name, (unittest.TestCase,), {"test_scenario": test_scenario, "__module__": __name__,
                                             "__doc__": f"The lifecycle scenario {flow_id}."})


for _flow_id in FLOW_IDS:
    globals()[class_name(_flow_id)] = scenario_class(_flow_id)


class GeneratedClassTest(unittest.TestCase):
    """What a generated atom does with the probe and with an outcome (D3, D6)."""

    def run_class(self, probe, outcome=None, env=None):
        cls = scenario_class("scenario-example")
        result = unittest.TestResult()
        with mock.patch.object(kit, "probe_isolation", return_value=probe), \
                mock.patch.object(kit, "run_scenario", return_value=outcome) as run, \
                mock.patch.dict(os.environ, env or {}, clear=False):
            if not (env or {}).get("CI"):
                os.environ.pop("CI", None)
            unittest.defaultTestLoader.loadTestsFromTestCase(cls).run(result)
        return result, run

    def outcome(self, status="passed", problems=()):
        return kit.Outcome("scenario-example", "namespace", {"status": status}, "", "0" * 40, True, list(problems), 0.1)

    def test_each_atom_is_a_class_named_after_its_flow_id(self) -> None:
        cls = scenario_class("scenario-kill-and-resume-job-before")
        self.assertEqual(cls.__name__, "Scenario_scenario_kill_and_resume_job_before")
        self.assertEqual(cls.__module__, __name__)

    def test_unavailable_isolation_skips_locally_and_runs_nothing(self) -> None:
        result, run = self.run_class(kit.Probe(False, "setup", "no namespaces"))
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("'setup'", result.skipped[0][1])
        run.assert_not_called()

    def test_unavailable_isolation_fails_under_ci(self) -> None:
        result, run = self.run_class(kit.Probe(False, "user-namespace", "denied"), env={"CI": "true"})
        self.assertEqual(len(result.failures), 1)
        self.assertIn("'user-namespace'", result.failures[0][1])
        run.assert_not_called()

    def test_a_passing_outcome_passes_and_a_failing_one_fails(self) -> None:
        good, _ = self.run_class(kit.Probe(True, None, ""), self.outcome())
        self.assertTrue(good.wasSuccessful())
        bad, _ = self.run_class(kit.Probe(True, None, ""), self.outcome("failed"))
        self.assertEqual(len(bad.failures), 1)
        leaky, _ = self.run_class(kit.Probe(True, None, ""), self.outcome(problems=["leaked"]))
        self.assertEqual(len(leaky.failures), 1)


class ImportTimeConfinementTest(unittest.TestCase):
    """Loading the suite wrappers executes no scenario module (I1): their atoms come from the source, not from an import."""

    def test_importing_the_wrappers_imports_no_scenario_module(self) -> None:
        code = ("import sys; import tests.test_scenarios, tests.test_scenarios_negative; "
                "bad = [m for m in ('tests.scenarios.catalogue', 'tests.scenarios.lifecycle', 'tests.scenarios.features') "
                "if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
        done = subprocess.run([sys.executable, "-B", "-c", code], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_scenario_module_top_level_statement_does_not_run_when_the_wrappers_load(self) -> None:
        """A marker written at the top of a copy of ``features`` stays unwritten while the wrappers load."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ran"
            shadow = Path(tmp) / "scenarios"
            shadow.mkdir()
            for name in inventory.CATALOGUE_MODULES:
                source = (inventory.SCENARIOS_DIR / f"{name}.py").read_text()
                (shadow / f"{name}.py").write_text(f"open({str(marker)!r}, 'w').close()\n" + source)
            self.assertEqual(inventory.catalogue_flow_ids(shadow), FLOW_IDS)
            self.assertFalse(marker.exists())

    def test_the_source_ids_equal_the_sandboxed_discovery(self) -> None:
        decision, reason = kit.isolation_gate(kit.probe_isolation(), os.environ)
        if decision == "skip":
            self.skipTest(reason)
        if decision == "fail":
            self.fail(reason)
        self.assertEqual([s.flow_id for s in kit.list_scenarios("")], FLOW_IDS)

    def test_the_source_ids_match_the_registered_shape_without_isolation(self) -> None:
        from tests.scenarios import FLOW_ID_RE
        self.assertEqual(len(FLOW_IDS), len(set(FLOW_IDS)))
        self.assertEqual(len(FLOW_IDS), 24)
        for flow_id in FLOW_IDS:
            self.assertRegex(flow_id, FLOW_ID_RE)


class ScenarioInventoryTest(unittest.TestCase):
    """Every registered variant has its own planner atom, within the CI shard ceiling."""

    @classmethod
    def setUpClass(cls) -> None:
        import sys
        tools = str(REPO_ROOT / "tools")
        sys.path.insert(0, tools)
        try:
            import test_shards as shards
        finally:
            sys.path.remove(tools)
        cls.shards = shards
        cls.inventory = shards.build_inventory(REPO_ROOT)

    def test_class_names_are_unique_and_valid(self) -> None:
        names = [class_name(flow_id) for flow_id in FLOW_IDS]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertTrue(name.isidentifier(), name)
            self.assertIs(getattr(__import__(__name__, fromlist=[name]), name).__module__, __name__)

    def test_each_registered_variant_is_its_own_atom(self) -> None:
        keys = {atom.key for atom in self.inventory.atoms}
        for flow_id in FLOW_IDS:
            self.assertIn(f"{__name__}.{class_name(flow_id)}", keys, flow_id)
        self.assertIn(f"{__name__}.ScenarioInventoryTest", keys)
        self.assertNotIn(__name__, keys, "the module must not be a single atom (no module fixtures)")

    def test_the_ci_plan_stays_within_four_shards(self) -> None:
        shards = self.shards
        selection = self.inventory.select([], ci_placement=True)
        params = shards.PlanParameters("ci", shards.CI_TARGET_SHARD_SECONDS, shards.DEFAULT_MIN_SHARDS,
                                       shards.CI_MAX_SHARDS)
        timings = [(REPO_ROOT / shards.CI_TIMINGS, shards.load_timings(REPO_ROOT / shards.CI_TIMINGS))]
        plan = shards.build_plan(selection, params, timings, REPO_ROOT)
        parallel = plan["shard_count"] - sum(1 for s in plan["shards"] if s.get("exclusive"))
        self.assertLessEqual(parallel, MAX_CI_SHARDS)


if __name__ == "__main__":
    unittest.main()
