"""Throwaway C2 functional-review probe (never merged): always fails."""
import unittest


class RerunProbeTest(unittest.TestCase):
    def test_always_fails(self):
        self.fail("probe: always fails")
