"""The whole-catalogue determinism check (``workflow-controller-ci-time`` D2).

Two complete runs of every registered scenario against one HEAD agree on every
normalised summary, and every record passes the Workflow's real ingestion. The
class sits in a module of its own so that CI can place it by module: it runs in
``main.yml`` after the merge and gates the release, not on every pull request
(``tools/test_shards.py``'s ``CI_PLACEMENT``). A pull request keeps the
three-flow subset in ``tests/test_run_scenarios.py``. A local ``unittest
discover`` still runs this module.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_run_scenarios as base
from tests.scenarios import kit


class CatalogueDeterminismTest(base.CloneCase):
    """D9, whole catalogue: two complete runs against one HEAD agree on every summary."""

    @classmethod
    def setUpClass(cls) -> None:
        # The base class skips whenever the sandbox is unavailable, and a run that only skips exits 0. Under CI an
        # unavailable sandbox must fail the run instead (as the scenario atoms do), locally it still skips.
        decision, reason = kit.isolation_gate(kit.probe_isolation(), os.environ)
        if decision == "skip":
            raise unittest.SkipTest(reason)
        if decision == "fail":
            raise AssertionError(reason)
        super().setUpClass()

    def complete_run(self) -> dict:
        folder = Path(tempfile.mkdtemp(prefix="run-scenarios-cat-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = self.runner("run", output=folder, module=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return self.records(folder)

    def test_two_complete_runs_agree_on_every_normalised_summary(self) -> None:
        records = self.complete_run()
        # D4: every record of every registered flow and variant passes the Workflow's real ingestion, not only the
        # selftest records IngestionTest feeds it.
        self.assert_ingested(records)
        first = {k: v["summary"] for k, v in records.items()}
        second = {k: v["summary"] for k, v in self.complete_run().items()}
        self.assertGreaterEqual(len(first), 15)
        self.assertFalse([name for name in first if name.startswith("selftest")])
        self.assertEqual(first, second)


class IsolationGateTest(unittest.TestCase):
    """The class cannot pass by skipping on CI: an unavailable sandbox fails there and skips only locally."""

    UNAVAILABLE = kit.Probe(False, "unshare", "no user namespaces")

    def set_up_class(self, env: dict) -> None:
        with mock.patch.object(kit, "probe_isolation", return_value=self.UNAVAILABLE), \
                mock.patch.dict(os.environ, env, clear=True):
            CatalogueDeterminismTest.setUpClass()

    def test_an_unavailable_sandbox_fails_under_ci(self) -> None:
        with self.assertRaises(AssertionError) as caught:
            self.set_up_class({"CI": "true"})
        self.assertIn("unshare", str(caught.exception))

    def test_an_unavailable_sandbox_skips_outside_ci(self) -> None:
        with self.assertRaises(unittest.SkipTest):
            self.set_up_class({})


if __name__ == "__main__":
    unittest.main()
