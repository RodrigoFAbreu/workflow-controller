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

from controller import managed_repo, workflow_contract  # noqa: E402
from controller.errors import UnsupportedInstallProfileError, UnsupportedWorkflowVersionError  # noqa: E402
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


def _admit_installation(root: Path) -> dict:
    """The non-Manager steps of ``managed_repo.inspect``, in its order: the
    record is read, the version gate runs (the legacy arm for a release in
    ``RELEASE_CONTRACTS``, the capability arm for any other), then the
    profile check. Returns the record; raises the production refusal."""
    manifest = managed_repo._read_manifest(root)
    release = manifest["workflow_version"]
    if release in workflow_contract.RELEASE_CONTRACTS:
        managed_repo._check_workflow_version(release, root)
    else:
        managed_repo._check_protocol_admission(release, root, manifest)
    if manifest["profile"] not in managed_repo.SUPPORTED_PROFILES:
        raise UnsupportedInstallProfileError(
            f"{root} runs profile {manifest['profile']!r}, outside "
            f"{sorted(managed_repo.SUPPORTED_PROFILES)}",
            evidence={"observed_profile": manifest["profile"]},
        )
    return manifest


def _installation_problems(root: Path, manifest: dict) -> list[str]:
    """Every way ``root``'s Workflow files differ from what ``manifest``'s
    ``managed`` map records for them, each naming the path."""
    managed = manifest["managed"]
    problems = []
    for rel_path, entry in sorted(managed.items()):
        installed = root / rel_path
        if not installed.is_file():
            problems.append(f"{rel_path}: missing")
            continue
        if hashlib.sha256(installed.read_bytes()).hexdigest() != entry["sha256"]:
            problems.append(f"{rel_path}: digest differs from the installation record")
        if bool(installed.stat().st_mode & 0o100) != entry["executable"]:
            problems.append(f"{rel_path}: mode differs from the installation record")
    commands_dir = root / ".claude" / "commands"
    installed_commands = {path.relative_to(root).as_posix() for path in commands_dir.glob("*.md")}
    recorded_commands = {path for path in managed if path.startswith(".claude/commands/")}
    for rel_path in sorted(installed_commands - recorded_commands):
        problems.append(f"{rel_path}: installed but not in the installation record")
    return problems


class InstalledReleaseTest(unittest.TestCase):
    """This repository's own installed Workflow: admitted the way the
    Controller admits any managed repository, and equal to what its
    ``installation.json`` records. The release is read from that record, never
    written here as a literal and never matched to a vendored tree, so a
    Workflow move needs no change here."""

    def test_the_installed_release_is_admitted(self) -> None:
        _admit_installation(fixtures.REPO_ROOT)

    def test_every_vendored_tree_is_validated_or_ships_the_protocol(self) -> None:
        for release in _vendored_releases():
            with self.subTest(release=release):
                self.assertTrue(_is_admitted(release))

    def test_a_tree_that_is_neither_is_not_admitted(self) -> None:
        self.assertFalse(_is_admitted("9.9.9", files={"scripts/workflow_state.py": {}}))

    def test_the_installed_files_equal_the_installation_record(self) -> None:
        manifest = managed_repo._read_manifest(fixtures.REPO_ROOT)
        self.assertEqual([], _installation_problems(fixtures.REPO_ROOT, manifest))


class InstalledReleaseRefusalTest(unittest.TestCase):
    """The two checks above on a disposable installation, so each refusal is
    shown to fire (the real installation is always valid)."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "repo"
        fixtures.git_init(self.root)
        fixtures.install_workflow_release(self.root, "2.7.0")
        fixtures.commit_all(self.root, "install")

    def test_an_untouched_installation_is_admitted_and_matches_its_record(self) -> None:
        manifest = _admit_installation(self.root)
        self.assertEqual([], _installation_problems(self.root, manifest))

    def test_a_modified_file_is_reported_by_name(self) -> None:
        _append(self.root / "scripts" / "workflow_state.py", b"# edited\n")
        problems = _installation_problems(self.root, managed_repo._read_manifest(self.root))
        self.assertEqual(["scripts/workflow_state.py: digest differs from the installation record"], problems)

    def test_a_missing_file_is_reported_by_name(self) -> None:
        (self.root / ".claude" / "commands" / "review-plan.md").unlink()
        problems = _installation_problems(self.root, managed_repo._read_manifest(self.root))
        self.assertEqual([".claude/commands/review-plan.md: missing"], problems)

    def test_an_extra_command_file_is_reported_by_name(self) -> None:
        (self.root / ".claude" / "commands" / "stray.md").write_text("x\n")
        problems = _installation_problems(self.root, managed_repo._read_manifest(self.root))
        self.assertEqual([".claude/commands/stray.md: installed but not in the installation record"], problems)

    def test_a_changed_mode_is_reported_by_name(self) -> None:
        script = self.root / "scripts" / "workflow_state.py"
        script.chmod(script.stat().st_mode ^ 0o100)
        problems = _installation_problems(self.root, managed_repo._read_manifest(self.root))
        self.assertEqual(["scripts/workflow_state.py: mode differs from the installation record"], problems)

    def test_a_release_without_the_protocol_script_is_refused(self) -> None:
        fixtures.write_installation_manifest(self.root, workflow_version="9.9.9",
                                             managed={"scripts/workflow_state.py": {}})
        with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
            _admit_installation(self.root)
        self.assertEqual("no_protocol", ctx.exception.evidence["reason"])

    def test_protocol_major_2_is_refused(self) -> None:
        script = self.root / "scripts" / "workflow_protocol.py"
        script.write_text(script.read_text().replace("PROTOCOL_MAJOR = 1", "PROTOCOL_MAJOR = 2"))
        with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
            _admit_installation(self.root)
        self.assertEqual("unsupported_protocol_major", ctx.exception.evidence["reason"])

    def test_an_unsupported_profile_is_refused(self) -> None:
        record = self.root / ".workflow-manager" / "installation.json"
        data = json.loads(record.read_text())
        data["profile"] = "minimal-nonexistent"
        record.write_text(json.dumps(data))
        with self.assertRaises(UnsupportedInstallProfileError):
            _admit_installation(self.root)


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
