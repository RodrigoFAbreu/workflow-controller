"""Tests for managed-repository inspection (capability 1,
``controller.managed_repo``).

``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``'s CP2 section names these
cases: a fixture-built managed repository verifies clean; a bare
``git init`` directory refuses unmanaged; a truncated/invalid manifest,
and one whose ``schema_version`` is absent or wrong, refuse malformed; a
stub ``workflow-manager`` that fails refuses drifted, carrying its output
as evidence; a missing executable refuses unavailable; an unsupported
``workflow_version`` refuses unsupported-version; the ``(workflow_version,
profile)`` pair gets its own admit/refuse/ordering cases; the
``verify``-vs-``status`` asymmetry gets its own case; and one test runs
against the real installed ``workflow-manager``, skipped when absent.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import managed_repo  # noqa: E402
from controller.errors import (  # noqa: E402
    DriftedInstallationError,
    MalformedInstallationManifestError,
    NotARepositoryError,
    UnmanagedRepositoryError,
    UnsupportedInstallProfileError,
    UnsupportedWorkflowVersionError,
    WorkflowManagerUnavailableError,
)
from tests import fixtures  # noqa: E402

REAL_WORKFLOW_MANAGER = shutil.which("workflow-manager")


class CleanManagedRepositoryTest(unittest.TestCase):
    def test_stub_manager_admits_a_clean_managed_repository(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            result = managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(result.root, repo.resolve())
            self.assertEqual(result.workflow_version, "2.3.1")
            self.assertEqual(result.profile, "full")
            self.assertEqual(result.verify["returncode"], 0)
            self.assertEqual(result.status["returncode"], 0)
            self.assertIsInstance(result.manifest, dict)

    @unittest.skipUnless(REAL_WORKFLOW_MANAGER, "no real workflow-manager installed")
    def test_real_workflow_manager_admits_this_repository(self) -> None:
        # This repository is itself a real, currently-clean Workflow
        # v2.3.1 (full profile) managed repository -- the disposable
        # integration fixture every other case in this file avoids
        # needing.
        result = managed_repo.inspect(fixtures.REPO_ROOT)
        self.assertEqual(result.workflow_version, "2.3.1")
        self.assertEqual(result.profile, "full")
        self.assertEqual(result.verify["returncode"], 0)
        self.assertEqual(result.status["returncode"], 0)


class UnmanagedRepositoryTest(unittest.TestCase):
    def test_bare_git_init_refuses_unmanaged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_bare_git_repo(Path(td) / "repo")
            with self.assertRaises(UnmanagedRepositoryError) as ctx:
                managed_repo.inspect(repo)
            self.assertEqual(ctx.exception.evidence["root"], str(repo.resolve()))

    def test_non_repository_refuses_not_a_repository(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plain_dir = Path(td) / "not-a-repo"
            plain_dir.mkdir()
            with self.assertRaises(NotARepositoryError):
                managed_repo.inspect(plain_dir)


class MalformedManifestTest(unittest.TestCase):
    def test_truncated_manifest_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_bare_git_repo(Path(td) / "repo")
            manifest_dir = repo / ".workflow-manager"
            manifest_dir.mkdir()
            (manifest_dir / "installation.json").write_text("{not valid json")
            with self.assertRaises(MalformedInstallationManifestError):
                managed_repo.inspect(repo)

    def test_schema_version_absent_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_bare_git_repo(Path(td) / "repo")
            fixtures.write_installation_manifest(repo, include_schema_version=False)
            with self.assertRaises(MalformedInstallationManifestError) as ctx:
                managed_repo.inspect(repo)
            self.assertIsNone(ctx.exception.evidence["schema_version"])

    def test_schema_version_wrong_value_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_bare_git_repo(Path(td) / "repo")
            fixtures.write_installation_manifest(repo, schema_version=2)
            with self.assertRaises(MalformedInstallationManifestError) as ctx:
                managed_repo.inspect(repo)
            self.assertEqual(ctx.exception.evidence["schema_version"], 2)

    def test_missing_workflow_version_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_bare_git_repo(Path(td) / "repo")
            fixtures.write_installation_manifest(repo, include_workflow_version=False)
            with self.assertRaises(MalformedInstallationManifestError):
                managed_repo.inspect(repo)

    def test_missing_profile_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_bare_git_repo(Path(td) / "repo")
            fixtures.write_installation_manifest(repo, include_profile=False)
            with self.assertRaises(MalformedInstallationManifestError):
                managed_repo.inspect(repo)


class WorkflowManagerUnavailableTest(unittest.TestCase):
    def test_missing_executable_refuses_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo")
            missing = Path(td) / "does-not-exist"
            with self.assertRaises(WorkflowManagerUnavailableError):
                managed_repo.inspect(repo, manager_bin=str(missing))

    def test_env_var_missing_executable_refuses_unavailable(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo")
            missing = Path(td) / "also-does-not-exist"
            old = os.environ.get(managed_repo.MANAGER_ENV)
            os.environ[managed_repo.MANAGER_ENV] = str(missing)
            try:
                with self.assertRaises(WorkflowManagerUnavailableError):
                    managed_repo.inspect(repo)
            finally:
                if old is None:
                    os.environ.pop(managed_repo.MANAGER_ENV, None)
                else:
                    os.environ[managed_repo.MANAGER_ENV] = old


class DriftedInstallationTest(unittest.TestCase):
    def test_stub_manager_nonzero_both_refuses_drifted_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo")
            stub = fixtures.write_stub_workflow_manager(
                Path(td) / "workflow-manager",
                verify_exit=2, status_exit=1,
                verify_stdout="verify: drift detected",
                status_stdout="workflow 2.3.1 (full profile) -- drifted",
            )
            with self.assertRaises(DriftedInstallationError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["verify"]["returncode"], 2)
            self.assertIn("drift detected", evidence["verify"]["stdout"])
            self.assertEqual(evidence["status"]["returncode"], 1)
            self.assertIn("drifted", evidence["status"]["stdout"])

    def test_asymmetric_status_zero_verify_nonzero_still_refuses_drifted(self) -> None:
        """The measured asymmetry: `status` exits 0 even for an unmanaged
        or drifted installation, so only `verify`'s exit code is an
        admission signal -- but a non-zero `verify` must still refuse even
        when `status` alone looks clean."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo")
            stub = fixtures.write_stub_workflow_manager(
                Path(td) / "workflow-manager", verify_exit=2, status_exit=0,
            )
            with self.assertRaises(DriftedInstallationError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(ctx.exception.evidence["verify"]["returncode"], 2)
            self.assertEqual(ctx.exception.evidence["status"]["returncode"], 0)


class UnsupportedWorkflowVersionTest(unittest.TestCase):
    def test_unsupported_version_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.4.0")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(ctx.exception.evidence["observed_workflow_version"], "2.4.0")
            self.assertIn("2.3.1", ctx.exception.evidence["supported_workflow_versions"])


class InstallProfilePairTest(unittest.TestCase):
    def test_runtime_profile_at_2_3_1_is_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", profile="runtime")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            result = managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(result.profile, "runtime")

    @unittest.skipUnless(REAL_WORKFLOW_MANAGER, "no real workflow-manager installed")
    def test_real_runtime_profile_bootstrap_installs_all_fifteen_command_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "runtime-repo"
            repo.mkdir()
            fixtures.run(["git", "init", "-q"], cwd=repo)
            fixtures.run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=repo)
            fixtures.run(["git", "config", "user.name", "Controller Tests"], cwd=repo)
            fixtures.run(["git", "commit", "-q", "--allow-empty", "-m", "root"], cwd=repo)
            bootstrap = fixtures.run(
                [REAL_WORKFLOW_MANAGER, "bootstrap", "--profile", "runtime", str(repo)],
                check=False,
            )
            if bootstrap.returncode != 0:
                self.skipTest(
                    f"real workflow-manager bootstrap --profile runtime failed: "
                    f"{bootstrap.stderr.strip()}"
                )
            result = managed_repo.inspect(repo)
            self.assertEqual(result.profile, "runtime")
            command_files = sorted((repo / ".claude" / "commands").glob("*.md"))
            self.assertEqual(len(command_files), 15)

    def test_unknown_profile_at_2_3_1_refuses_naming_observed_and_supported_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", profile="mystery")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedInstallProfileError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["observed_profile"], "mystery")
            self.assertEqual(evidence["observed_workflow_version"], "2.3.1")
            self.assertIn(["2.3.1", "full"], [list(p) for p in evidence["supported_installations"]])

    def test_unsupported_profile_refusal_precedes_any_workflow_state_read(self) -> None:
        """Points the fixture at a repository whose WORKFLOW_STATE.json is
        deliberately unparseable. `managed_repo.inspect` never reads
        Workflow state at all, so the profile refusal must surface --
        never anything that would come from reading that file."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_bare_git_repo(Path(td) / "repo")
            fixtures.write_installation_manifest(repo, profile="mystery")
            state_dir = repo / "docs" / "ai-workflow"
            state_dir.mkdir(parents=True)
            (state_dir / "WORKFLOW_STATE.json").write_text("{not valid json at all")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedInstallProfileError):
                managed_repo.inspect(repo, manager_bin=str(stub))


if __name__ == "__main__":
    unittest.main()
