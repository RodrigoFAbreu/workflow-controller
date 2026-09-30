"""Throwaway C2 functional-review probe (never merged): leaks a process on CI attempt 1 only."""
import os
import subprocess
import unittest


class RerunProbeTest(unittest.TestCase):
    def test_leaks_a_process_on_the_first_attempt_only(self):
        if os.environ.get("GITHUB_RUN_ATTEMPT") == "1":
            subprocess.Popen(["setsid", "sleep", "300"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
