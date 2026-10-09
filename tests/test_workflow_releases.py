"""Tests for the vendored Workflow release trees (``tools/workflow_releases.py``,
``docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md`` Design A).

Every Workflow-derived check reads ``tests/workflow_releases/<release>/``,
never this repository's own installed Workflow (plan invariant I7). This
module pins that the trees are what their ``RELEASE.json`` records, that
every admitted release has one, and that this repository's own installation
is an admitted release whose installed files equal its vendored tree. When a
Workflow Manager distribution is available locally, the recorded hashes are
also checked against the Manager's own manifests.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import managed_repo  # noqa: E402
from tests import fixtures  # noqa: E402

WORKFLOW_RELEASES_PY = fixtures.REPO_ROOT / "tools" / "workflow_releases.py"
_spec = importlib.util.spec_from_file_location("workflow_releases", WORKFLOW_RELEASES_PY)
workflow_releases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(workflow_releases)

#: A local Workflow Manager checkout's ``distribution/workflow`` directory:
#: ``WORKFLOW_MANAGER_DISTRIBUTION``, else the sibling checkout's. CI has
#: neither.
_DISTRIBUTION = Path(os.environ.get(
    "WORKFLOW_MANAGER_DISTRIBUTION",
    fixtures.REPO_ROOT.parent / "workflow-manager" / "distribution" / "workflow",
))

_COMMANDS = ("accept-milestone", "milestone-plan", "review-plan")


def _vendored_releases() -> list[str]:
    return sorted(p.name for p in fixtures.WORKFLOW_RELEASES_DIR.iterdir() if p.is_dir())


def _is_admitted(release: str, *, files: dict | None = None) -> bool:
    """Legacy-validated, or a release whose tree lists the protocol script."""
    if files is None:
        files = fixtures.workflow_release_files(release)
    return release in managed_repo.VALIDATED_WORKFLOW_RELEASES or "scripts/workflow_protocol.py" in files


def _append(path: Path, data: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(data)


def _copy_releases(root: Path) -> Path:
    """A copy of every vendored tree under ``root``, modes kept."""
    shutil.copytree(fixtures.WORKFLOW_RELEASES_DIR, root / workflow_releases.RELEASES_DIR)
    return root


class CheckTest(unittest.TestCase):
    def test_check_passes(self) -> None:
        self.assertEqual(workflow_releases.check(), [])
        result = subprocess.run([sys.executable, "-B", str(WORKFLOW_RELEASES_PY), "check"],
                                capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_every_admitted_release_has_a_vendored_tree(self) -> None:
        for release in sorted(managed_repo.VALIDATED_WORKFLOW_RELEASES):
            with self.subTest(release=release):
                self.assertEqual(workflow_releases.load_release(release)["workflow_version"], release)

    def test_each_tree_is_exactly_the_vendored_subset(self) -> None:
        for release in _vendored_releases():
            with self.subTest(release=release):
                files = fixtures.workflow_release_files(release)
                commands = [path for path in files if path.startswith(".claude/commands/")]
                expected = list(workflow_releases.VENDORED_SCRIPTS)
                if "scripts/workflow_protocol.py" in files:
                    # A release that ships the orchestration protocol (2.7.0).
                    expected += workflow_releases.PROTOCOL_PATHS
                if "scripts/workflow_gate_policy.py" in files:
                    # A release that ships the gate policy (2.8.0 on).
                    expected += workflow_releases.GATE_POLICY_PATHS
                # 2.8.0 added three command files, 2.9.0 two more.
                self.assertEqual(len(commands), 22 if "scripts/workflow_gate_policy.py" in files else 17)
                self.assertEqual(sorted(set(files) - set(commands)), sorted(expected))
                self.assertTrue(files["scripts/prepare-ai-review.sh"]["executable"])
                tree = fixtures.workflow_release_tree(release)
                self.assertTrue(os.access(tree / "scripts" / "prepare-ai-review.sh", os.X_OK))

    def test_no_vendored_file_is_discoverable_as_a_test(self) -> None:
        """``unittest discover`` and ``tools/test_shards.py`` pick up
        ``tests/test_*.py`` and packages only: the trees carry neither."""
        for path in fixtures.WORKFLOW_RELEASES_DIR.rglob("*"):
            self.assertNotEqual(path.name, "__init__.py", path)
            self.assertFalse(path.name.startswith("test_") and path.suffix == ".py", path)

    def test_a_modified_missing_or_extra_file_is_reported(self) -> None:
        release = sorted(managed_repo.VALIDATED_WORKFLOW_RELEASES)[0]
        cases = {
            "sha256": lambda tree: _append(tree / "scripts" / "workflow_state.py", b"\n"),
            "executable is False": lambda tree: (tree / "scripts" / "prepare-ai-review.sh").chmod(0o644),
            "missing or not a regular file": lambda tree: (tree / ".claude" / "commands" / "review-plan.md").unlink(),
            "is not a vendored file": lambda tree: (tree / "scripts" / "uuid.py").write_text("planted\n"),
        }
        for needle, damage in cases.items():
            with self.subTest(needle=needle), tempfile.TemporaryDirectory() as td:
                root = _copy_releases(Path(td))
                damage(workflow_releases.release_dir(release, root))
                problems = workflow_releases.check(root)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(needle, problems[0])
                self.assertTrue(problems[0].startswith(f"{release}: "), problems[0])

    def test_an_admitted_release_without_a_tree_is_reported(self) -> None:
        release = sorted(managed_repo.VALIDATED_WORKFLOW_RELEASES)[0]
        with tempfile.TemporaryDirectory() as td:
            root = _copy_releases(Path(td))
            shutil.rmtree(workflow_releases.release_dir(release, root))
            self.assertIn(f"admitted release {release} has no vendored tree under tests/workflow_releases",
                          workflow_releases.check(root))
            result = subprocess.run(
                [sys.executable, "-B", str(WORKFLOW_RELEASES_PY), "--root", str(root), "check"],
                capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(f"admitted release {release} has no vendored tree", result.stderr)


class InstalledReleaseTest(unittest.TestCase):
    """This repository's own installed Workflow: an admitted release, and
    byte-identical to that release's vendored tree. The release is read from
    its ``installation.json``, never written here as a literal, so the same
    test holds after the post-release update to another admitted release."""

    def setUp(self) -> None:
        manifest = fixtures.REPO_ROOT / ".workflow-manager" / "installation.json"
        self.release = json.loads(manifest.read_text())["workflow_version"]

    def test_the_installed_release_is_admitted(self) -> None:
        """A legacy release is admitted by exact validation; a protocol
        release by capability. The predicate checks the vendored listing
        only: protocol major 1 is pinned per vendored protocol release by
        ``ProtocolAdmissionTest.test_every_vendored_protocol_release_is_runnable``."""
        self.assertTrue(_is_admitted(self.release), f"{self.release} is neither validated nor a protocol release")

    def test_every_vendored_tree_is_validated_or_ships_the_protocol(self) -> None:
        for release in _vendored_releases():
            with self.subTest(release=release):
                self.assertTrue(_is_admitted(release))

    def test_a_tree_that_is_neither_is_not_admitted(self) -> None:
        self.assertFalse(_is_admitted("9.9.9", files={"scripts/workflow_state.py": {}}))

    def test_the_installed_files_equal_the_vendored_tree(self) -> None:
        tree = fixtures.workflow_release_tree(self.release)
        files = fixtures.workflow_release_files(self.release)
        installed_commands = {path.relative_to(fixtures.REPO_ROOT).as_posix()
                              for path in (fixtures.REPO_ROOT / ".claude" / "commands").glob("*.md")}
        self.assertEqual(installed_commands, {path for path in files if path.startswith(".claude/commands/")})
        for rel_path, entry in sorted(files.items()):
            with self.subTest(path=rel_path):
                installed = fixtures.REPO_ROOT / rel_path
                self.assertEqual(installed.read_bytes(), (tree / rel_path).read_bytes())
                self.assertEqual(bool(installed.stat().st_mode & 0o100), entry["executable"])


@unittest.skipUnless(_DISTRIBUTION.is_dir(),
                     "no local Workflow Manager distribution (WORKFLOW_MANAGER_DISTRIBUTION or the sibling checkout)")
class ManagerManifestTest(unittest.TestCase):
    def test_recorded_hashes_equal_the_manager_manifest(self) -> None:
        for release in _vendored_releases():
            with self.subTest(release=release):
                self.assertEqual(workflow_releases.manifest_mismatches(release, _DISTRIBUTION), [])


def _git(root: Path, *args: str) -> str:
    return fixtures.run(["git", *args], cwd=root).stdout.strip()


class SyncTest(unittest.TestCase):
    """``sync`` against a synthetic Workflow Manager checkout, so it runs in
    CI: the subset, the modes, ``RELEASE.json``, and every refusal."""

    RELEASE = "9.9.9"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = fixtures.build_bare_git_repo(Path(self._tmp.name) / "manager")
        self.distribution = self.manager / "distribution" / "workflow"
        self.out = Path(self._tmp.name) / "controller"
        artifacts = []
        payload = {f".claude/commands/{name}.md": (f"# {name}\n".encode(), False) for name in _COMMANDS}
        payload.update({
            "scripts/prepare-ai-review.sh": (b"#!/bin/sh\n", True),
            "scripts/workflow_fingerprint.py": (b"# fingerprint\n", True),
            "scripts/workflow_state.py": (b"# state\n", False),
            "scripts/workflow_state_test.py": (b"# a conformance suite, not vendored\n", False),
            "docs/ai-workflow/REVIEW_PROTOCOL.md": (b"# not vendored\n", False),
        })
        for target_path, (data, executable) in payload.items():
            location = f"payload/{target_path}"
            path = self.distribution / self.RELEASE / location
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            artifacts.append({"target_path": target_path, "location": location, "category": "distribution",
                              "sha256": hashlib.sha256(data).hexdigest(), "executable": executable})
        self.manifest_path = self.distribution / self.RELEASE / "manifest.json"
        self.manifest_path.write_text(json.dumps({"workflow_version": self.RELEASE, "artifacts": artifacts}))
        fixtures.run(["git", "add", "-A"], cwd=self.manager)
        fixtures.run(["git", "commit", "-q", "-m", "release"], cwd=self.manager)
        self.commit = _git(self.manager, "rev-parse", "HEAD")

    def _sync(self, **kwargs) -> Path:
        return workflow_releases.sync(self.RELEASE, self.distribution, root=self.out, **kwargs)

    def test_sync_vendors_exactly_the_subset_and_records_it(self) -> None:
        dest = self._sync()
        record = workflow_releases.load_release(self.RELEASE, self.out)
        self.assertEqual(record["workflow_version"], self.RELEASE)
        self.assertEqual(record["manager_commit"], self.commit)
        self.assertEqual(record["manager_source"], f"distribution/workflow/{self.RELEASE}/payload")
        self.assertEqual(sorted(record["files"]),
                         sorted([f".claude/commands/{name}.md" for name in _COMMANDS]
                                + list(workflow_releases.VENDORED_SCRIPTS)))
        self.assertFalse((dest / "scripts" / "workflow_state_test.py").exists())
        self.assertFalse((dest / "docs").exists())
        self.assertTrue(os.access(dest / "scripts" / "prepare-ai-review.sh", os.X_OK))
        self.assertFalse(os.access(dest / "scripts" / "workflow_state.py", os.X_OK))
        self.assertEqual(workflow_releases._check_tree(self.RELEASE, self.out), [])
        self.assertEqual(workflow_releases.manifest_mismatches(self.RELEASE, self.distribution, self.out), [])

    def test_sync_replaces_a_stale_tree(self) -> None:
        dest = self._sync()
        (dest / "scripts" / "stale.py").write_text("left over\n")
        self._sync()
        self.assertFalse((dest / "scripts" / "stale.py").exists())

    def test_an_explicit_manager_commit_is_recorded_in_full(self) -> None:
        self._sync(manager_commit=self.commit[:7])
        self.assertEqual(workflow_releases.load_release(self.RELEASE, self.out)["manager_commit"], self.commit)

    def test_a_distribution_that_differs_from_the_commit_is_refused(self) -> None:
        release_root = self.distribution / self.RELEASE
        cases = {
            "differs from commit": lambda: (release_root / "payload" / "scripts" / "workflow_state.py").write_text("x"),
            "uncommitted or untracked": lambda: (release_root / "payload" / "scripts" / "extra.py").write_text("x"),
        }
        for needle, damage in cases.items():
            with self.subTest(needle=needle):
                damage()
                with self.assertRaises(ValueError) as ctx:
                    self._sync()
                self.assertIn(needle, str(ctx.exception))
                self.assertFalse(workflow_releases.release_dir(self.RELEASE, self.out).exists())
                fixtures.run(["git", "checkout", "-q", "--", "."], cwd=self.manager)
                fixtures.run(["git", "clean", "-qfd"], cwd=self.manager)

    def test_a_payload_that_differs_from_its_manifest_hash_is_refused(self) -> None:
        (self.distribution / self.RELEASE / "payload" / "scripts" / "workflow_state.py").write_bytes(b"# edited\n")
        fixtures.run(["git", "commit", "-q", "-am", "edit"], cwd=self.manager)
        with self.assertRaises(ValueError) as ctx:
            self._sync()
        self.assertIn("differs from the manifest's", str(ctx.exception))
        self.assertFalse(workflow_releases.release_dir(self.RELEASE, self.out).exists())

    def test_a_release_unpacked_from_an_archive_records_the_archive_digest(self) -> None:
        # No Git provenance is asked of a tree unpacked from a published
        # archive; the archive's own digest is recorded instead.
        fixtures.run(["git", "rm", "-rq", "--cached", "."], cwd=self.manager)
        (self.distribution / self.RELEASE / "payload" / "scripts" / "uncommitted.txt").write_text("x")
        self._sync(archive_sha256="ab" * 32)
        record = workflow_releases.load_release(self.RELEASE, self.out)
        self.assertEqual(record["manager_commit"], "archive-sha256:" + "ab" * 32)
        self.assertEqual(record["manager_source"], f"workflow-{self.RELEASE}.tar.gz")

    def test_the_protocol_and_gate_policy_files_are_vendored_when_the_release_has_them(self) -> None:
        OPTIONAL = workflow_releases.PROTOCOL_PATHS + workflow_releases.GATE_POLICY_PATHS
        for target_path in OPTIONAL:
            data = f"# {target_path}\n".encode()
            location = f"payload/{target_path}"
            path = self.distribution / self.RELEASE / location
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest = json.loads(self.manifest_path.read_text())
            manifest["artifacts"].append({"target_path": target_path, "location": location,
                                          "category": "distribution", "sha256": hashlib.sha256(data).hexdigest(),
                                          "executable": False})
            self.manifest_path.write_text(json.dumps(manifest))
        fixtures.run(["git", "add", "-A"], cwd=self.manager)
        fixtures.run(["git", "commit", "-q", "-m", "optional"], cwd=self.manager)
        dest = self._sync()
        for target_path in OPTIONAL:
            self.assertTrue((dest / target_path).is_file(), target_path)
        self.assertEqual(workflow_releases._check_tree(self.RELEASE, self.out), [])

    def test_the_gate_policy_files_are_not_required_of_a_release_without_them(self) -> None:
        dest = self._sync()
        for target_path in workflow_releases.GATE_POLICY_PATHS:
            self.assertFalse((dest / target_path).exists(), target_path)
        self.assertEqual(workflow_releases._check_tree(self.RELEASE, self.out), [])

    def test_a_release_that_is_not_a_dotted_version_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            workflow_releases.sync("../escape", self.distribution, root=self.out)

    def test_a_vendored_tree_that_drifts_from_the_manager_manifest_is_reported(self) -> None:
        self._sync()
        manifest = json.loads(self.manifest_path.read_text())
        manifest["artifacts"] = [entry for entry in manifest["artifacts"]
                                 if entry["target_path"] != ".claude/commands/review-plan.md"]
        manifest["artifacts"][0]["executable"] = True
        self.manifest_path.write_text(json.dumps(manifest))
        problems = workflow_releases.manifest_mismatches(self.RELEASE, self.distribution, self.out)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn(".claude/commands/review-plan.md is vendored but not in the Manager manifest", problems[0])
        self.assertIn(".claude/commands/accept-milestone.md recorded", problems[1])


if __name__ == "__main__":
    unittest.main()
