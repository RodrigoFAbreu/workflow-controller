"""`workflow-controller-codex-worker-sandbox` CP3: the live proof of the Codex worker sandbox (D8).

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on CI. Two runs of the identical probe command
(``controller.codex_worker_sandbox.probe_command``) under the ``worker`` permissions profile on a scratch clone:

(a) ``codex sandbox -P worker``: no model, no spend; it exercises the sandbox implementation and the profile;
(b) ``codex exec`` with a real model, one short turn: the proof that ``exec`` *applies* the profile.

Both are skipped, decided before any run and with the reason, only when ``codex`` or ``bwrap`` is missing or the
operator's Codex home has no ``auth.json``. A ``codex exec`` turn that fails or returns no probe output is a
failure. After each run the test also prints the finding for the nested read-only ``.git/hooks`` and ``.git/config``
(did the write happen, and did the git guard digest see it), which is what the capture file records.
``python3 -B -m unittest tests.test_codex_worker_sandbox_live -v``
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_worker_sandbox as cws, review  # noqa: E402
from tests import fixtures  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
CANARY_ENV = "CONTROLLER_LIVE_CANARY"
CANARY_VALUE = "canary-value-must-not-reach-the-sandbox"
SANDBOX_TIMEOUT = 180
EXEC_TIMEOUT = 600


def operator_codex_home() -> Path:
    """``CONTROLLER_LIVE_CODEX_HOME`` when set, else ``~/.codex``. ``CODEX_HOME`` is deliberately not read: the
    Controller's own tests point it at a throwaway folder."""
    return Path(os.environ.get("CONTROLLER_LIVE_CODEX_HOME") or Path.home() / ".codex")


def skip_reason() -> str | None:
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex worker sandbox tests"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


def git(*args: str, cwd: Path) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveWorkerSandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        # Not under /tmp: Codex refuses to create its helper binaries there (tester_sandbox.txt).
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="codex-worker-live-", dir=cache)).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.runtime = self.root / "runtime"
        self.binary = Path(review.resolve_binary("codex"))
        self.marker = "live-" + uuid.uuid4().hex[:12]
        self.ophome = self.root / "ophome"
        self.ophome.mkdir()
        (self.ophome / "canary").write_text("canary\n")
        self.origin = self.root / "origin.git"
        fixtures.git_init(self.origin, "--bare")
        self.tcp = socket.socket()
        self.tcp.bind(("127.0.0.1", 0))
        self.tcp.listen(8)
        self.sock_path = self.root / "s.sock"
        self.unix = socket.socket(socket.AF_UNIX)
        self.unix.bind(str(self.sock_path))
        self.unix.listen(8)
        for listener in (self.tcp, self.unix):
            self.addCleanup(listener.close)

    def prepare(self, job_id: str, *, prompt: bool = False) -> tuple[cws.Built, str, dict]:
        layout = cws.WorkerLayout(self.runtime, job_id)
        layout.worktree.mkdir(parents=True)
        fixtures.git_init(layout.worktree)
        (layout.worktree / "src.txt").write_text("original\n")
        git("add", "src.txt", cwd=layout.worktree)
        git("commit", "-q", "-m", "init", cwd=layout.worktree)
        cws.check_worktree(layout.worktree)
        targets = cws.ProbeTargets(
            worktree=layout.worktree, scratch=layout.scratch, sibling=layout.base / "sibling",
            stand_in_home=self.ophome, canary_file=self.ophome / "canary", canary_env=CANARY_ENV,
            credential=layout.codex_home / "auth.json", push_origin=self.origin,
            tcp_port=self.tcp.getsockname()[1], socket_path=self.sock_path)
        command = cws.probe_command(targets, marker=self.marker)
        built = cws.build(runtime_root=self.runtime, job_id=job_id, operator_home=operator_codex_home(),
                          binary=self.binary, package_parent=REPO_ROOT, extra_readable=[self.origin],
                          inherited={**os.environ, CANARY_ENV: CANARY_VALUE},
                          prompt=cws.probe_prompt(command) if prompt else None)
        return built, command, {"before": cws.guard_digest(layout.worktree), "head": git("rev-parse", "HEAD",
                                                                                         cwd=layout.worktree)}

    def assert_host_effects(self, built: cws.Built, output: str, baseline: dict) -> None:
        layout = built.layout
        tree = layout.worktree
        self.assertEqual(cws.judge(output), [], output)
        self.assertEqual((tree / "src.txt").read_text(), "original\nprobe\n", "the tracked file was edited")
        self.assertTrue((tree / cws.PROBE_NEW_FILE).exists(), "the new file exists in the worktree")
        self.assertTrue((tree / cws.PROBE_RENAME_TARGET).exists())
        self.assertFalse((tree / cws.PROBE_RENAME_SOURCE).exists())
        self.assertFalse((tree / cws.PROBE_DELETE_FILE).exists())
        self.assertNotEqual(git("rev-parse", "HEAD", cwd=tree), baseline["head"], "a commit exists in the worktree")
        self.assertIn("probe", git("log", "-1", "--format=%s", cwd=tree))
        self.assertFalse(layout.base.joinpath("sibling").exists())
        self.assertFalse((self.ophome / "probe").exists())
        self.assertFalse(Path("/tmp", self.marker).exists())
        self.assertFalse(Path("/var/tmp", self.marker).exists())
        self.assertEqual(git("for-each-ref", cwd=self.origin), "")
        self.assertTrue((layout.scratch / "probe").exists())
        gh_log = layout.fake_logs / "gh.jsonl"
        self.assertTrue(gh_log.is_file() and gh_log.read_text().strip(), "the fake gh log holds the call")
        self.assertIn(str(Path(sys.executable).resolve()), (layout.bin / "gh").read_text())

    def report_git_finding(self, built: cws.Built, output: str, baseline: dict) -> None:
        """D4: record which line held. The informational ``hooks`` and ``gitconfig`` results and the guard verdict."""
        tree = built.layout.worktree
        results = dict(re.findall(r"^PROBE (hooks|gitconfig) (\w+)$", output, re.MULTILINE))
        after = cws.guard_digest(tree)
        try:
            cws.check_guard(baseline["before"], after)
            guard = "accepted"
        except Exception as error:  # the refusal is the finding, not a failure of the test
            guard = f"refused: {error}"
        hook = tree / ".git" / "hooks" / ("probe-" + self.marker)
        print(f"\nfinding: hooks={results.get('hooks')} (file exists: {hook.exists()}), "
              f"gitconfig={results.get('gitconfig')} (comment present: "
              f"{('# probe-' + self.marker) in (tree / '.git' / 'config').read_text()}), guard {guard}")
        if results.get("hooks") == "WROTE" or results.get("gitconfig") == "WROTE":
            self.assertRaises(Exception, cws.check_guard, baseline["before"], after)
        else:
            cws.check_guard(baseline["before"], after)

    def test_modelless_sandbox_run_holds_every_boundary(self) -> None:
        built, command, baseline = self.prepare("sandbox1")
        cws.check_launch(built.layout, built.config_text, self.binary, built.environment)
        run = subprocess.run(cws.sandbox_probe_argv(built.layout, binary=self.binary, command=command),
                             cwd=built.layout.worktree, env=dict(built.environment), capture_output=True, text=True,
                             timeout=SANDBOX_TIMEOUT)
        print("\n--- codex sandbox output ---\n" + run.stdout + run.stderr[-800:])
        self.assert_host_effects(built, run.stdout, baseline)
        self.report_git_finding(built, run.stdout, baseline)

    def test_codex_exec_applies_the_profile(self) -> None:
        built, _command, baseline = self.prepare("exec1", prompt=True)
        cws.check_launch(built.layout, built.config_text, self.binary, built.environment)
        run = subprocess.run(list(built.argv), cwd=built.layout.worktree, env=dict(built.environment),
                             capture_output=True, text=True, timeout=EXEC_TIMEOUT, input="")
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        message = built.layout.last_message
        self.assertTrue(message.is_file(), "the exec turn returned no output")
        output = message.read_text()
        print("\n--- codex exec final message ---\n" + output)
        self.assert_host_effects(built, output, baseline)
        self.report_git_finding(built, output, baseline)


if __name__ == "__main__":
    unittest.main()
