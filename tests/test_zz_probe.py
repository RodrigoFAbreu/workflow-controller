"""Throwaway C2 functional-review probe (never merged): fails on CI attempt 1 only."""
import os
import unittest


class RerunProbeTest(unittest.TestCase):
    def test_fails_on_the_first_attempt_only(self):
        self.assertNotEqual(os.environ.get("GITHUB_RUN_ATTEMPT"), "1",
                            "probe: fails on attempt 1 only")
