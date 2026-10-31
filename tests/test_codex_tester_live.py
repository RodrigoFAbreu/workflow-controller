"""`workflow-controller-codex-tester-run` CP3: the live run of the Codex tester (`controller.codex_tester`).

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on CI, with its reason. It costs one model turn on
the operator's credentials: it builds a bridge from a tiny checklist and runs ``run_tester`` with a real ``codex``,
asserting an outcome of ``ran`` and a final message. It does not require valid results (the operator-run functional
round is C10g-3c). Skipped, with the reason and before any run, when ``codex`` or ``bwrap`` is missing or the operator's
Codex home has no ``auth.json``. ``python3 -m unittest tests.test_codex_tester_live -v``
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_bridge as cb, codex_tester as ct, functional_review as fr, review  # noqa: E402
from tests.test_codex_sandbox_live import operator_codex_home  # noqa: E402
from tests.test_functional_sandbox import make_repo  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
RUN_TIMEOUT = 600
FLOWS = [fr.Flow("flow-1", "1", "Read README.md in the clone and report its first line.")]


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex tester run is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex tester run (one model turn)"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveTesterRunTest(unittest.TestCase):
    def test_a_real_codex_tester_turn_is_ran_with_a_final_message(self) -> None:
        # Not under /tmp: Codex refuses to create its helper binaries there (tester_sandbox.txt).
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="codex-live-run-", dir=cache)).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        repo = root / "repo"
        head = make_repo(repo, {"README.md": "live tester fixture\n"})
        binary = Path(review.resolve_binary("codex"))
        bridge = cb.build_bridge(fr.capture_candidate(repo, head), runtime_root=root / "runtime", job_id="live1",
                                 work_item_id="live-item", operator_home=operator_codex_home(), binary=binary,
                                 package_parent=REPO_ROOT)
        result = ct.run_tester(bridge, FLOWS, binary=binary, operator_home=operator_codex_home(),
                               timeout=RUN_TIMEOUT)
        print(f"\n--- outcome: {result['outcome']} ---\n{result['detail']}\n--- tester message ---\n"
              f"{result['tester_message']}")
        self.assertEqual(result["outcome"], "ran", result["detail"])
        self.assertTrue(result["tester_message"].strip(), "the tester returned no final message")


if __name__ == "__main__":
    unittest.main()
