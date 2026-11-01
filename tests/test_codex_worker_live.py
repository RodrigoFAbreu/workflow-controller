"""`workflow-controller-codex-worker-launch` CP3: the live run of the Codex worker launcher (D8).

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on CI, with its reason. It costs one model turn on the
operator's credentials: it records the real build as verified in a scratch runtime root, prepares a scratch clone at the
layout's worktree path with a trivial command file, and runs ``launch_codex_worker`` with a real ``codex``. It asserts a
``SUCCESS`` result with a final message, a commit in the clone, a guard digest changed by that commit only and the
credential copy removed. Skipped, with the reason and before any run, when ``codex`` or ``bwrap`` is missing or the
operator's Codex home has no ``auth.json``. The output is the capture ``tests/harness_contract/codex/worker_launch.txt``.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_worker_live -v``
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

from controller import codex_build, command_files, codex_worker as cw, codex_worker_sandbox as cws, review, worker  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_codex_worker_sandbox_live import git, operator_codex_home  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
RUN_TIMEOUT = 600
COMMAND = """---
description: Live proof command
state_writer: false
---
In the current directory, append the line `live` to the file `src.txt`, then run `git add src.txt` and
`git commit -m "live: append"`. Reply with the single word `done`.
"""


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex worker launch is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex worker launch (one model turn)"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


class LiveCommandRendersTest(unittest.TestCase):
    """Offline: the live proof's exact command text must pass the launcher's renderer."""

    def test_the_live_command_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "live-proof.md").write_text(COMMAND)
            rendered = command_files.render_command("live-proof", "", Path(tmp))
        self.assertIn("src.txt", str(rendered))


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveWorkerLaunchTest(unittest.TestCase):
    def test_a_real_codex_worker_turn_commits_in_the_clone(self) -> None:
        # Not under /tmp: Codex refuses to create its helper binaries there (tester_sandbox.txt).
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="codex-worker-launch-live-", dir=cache)).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        commands = root / "commands"
        commands.mkdir()
        (commands / "live-proof.md").write_text(COMMAND)
        layout = cws.WorkerLayout(runtime, "live1")
        layout.worktree.mkdir(parents=True)
        fixtures.git_init(layout.worktree)
        (layout.worktree / "src.txt").write_text("original\n")
        git("add", "src.txt", cwd=layout.worktree)
        git("commit", "-q", "-m", "init", cwd=layout.worktree)
        before_head = git("rev-parse", "HEAD", cwd=layout.worktree)
        before_guard = cws.guard_digest(layout.worktree)
        binary = Path(review.resolve_binary("codex"))
        digest = codex_build.build_digest(binary)
        self.assertTrue(digest, "the Codex build cannot be bound to a digest")
        codex_build.record_verified(runtime, digest, {"note": "live test"})

        result = cw.launch_codex_worker(
            "/live-proof", runtime_root=runtime, job_id="live1", operator_home=operator_codex_home(),
            cwd=layout.worktree, timeout=RUN_TIMEOUT, commands_dir=commands)

        print(f"\n--- outcome: {result.outcome} ({result.subtype}), exit {result.returncode}, "
              f"{result.duration_ms} ms ---\n{result.result}\n--- diagnosis ---\n{result.stream_diagnosis}")
        self.assertEqual(result.outcome, worker.SUCCESS, result.stream_diagnosis)
        self.assertTrue(result.result.strip(), "the worker returned no final message")
        self.assertEqual(result.stream_diagnosis["breach"], [])
        self.assertNotEqual(git("rev-parse", "HEAD", cwd=layout.worktree), before_head, "no commit in the clone")
        self.assertIn("live", (layout.worktree / "src.txt").read_text())
        cws.check_guard(before_guard, cws.guard_digest(layout.worktree))
        self.assertEqual(cws.nested_git_entries(layout.worktree), [])
        self.assertFalse((layout.codex_home / review.CODEX.credential_file).exists(), "the credential copy remains")


if __name__ == "__main__":
    unittest.main()
