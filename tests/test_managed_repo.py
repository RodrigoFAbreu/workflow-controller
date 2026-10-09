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
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import managed_repo, protocol  # noqa: E402
from controller.errors import (  # noqa: E402
    DriftedInstallationError,
    MalformedInstallationManifestError,
    NotARepositoryError,
    UnmanagedRepositoryError,
    UnsupportedInstallProfileError,
    UnsupportedWorkflowVersionError,
    WorkflowManagerUnavailableError,
    WorkflowProtocolFailedError,
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
            self.assertEqual(result.workflow_version, "2.5.1")
            self.assertEqual(result.profile, "full")
            self.assertEqual(result.verify["returncode"], 0)
            self.assertEqual(result.status["returncode"], 0)
            self.assertIsInstance(result.manifest, dict)

    @unittest.skipUnless(REAL_WORKFLOW_MANAGER, "no real workflow-manager installed")
    def test_real_workflow_manager_admits_this_repository(self) -> None:
        # This repository is itself a real, currently-clean, full-profile
        # Workflow managed repository -- the disposable integration fixture
        # every other case in this file avoids needing. Its release is
        # whatever its own installation.json declares, never a literal:
        # an admitted one, and the one whose vendored tree
        # tests/test_workflow_releases.py checks its installed files against.
        declared = json.loads(
            (fixtures.REPO_ROOT / ".workflow-manager" / "installation.json").read_text(),
        )["workflow_version"]
        result = managed_repo.inspect(fixtures.REPO_ROOT)
        # Admitted by exact validation (a legacy release) or by capability
        # (a protocol release: ``target_protocol`` is set only then).
        self.assertTrue(result.workflow_version in managed_repo.VALIDATED_WORKFLOW_RELEASES
                        or result.target_protocol is not None)
        self.assertEqual(result.workflow_version, declared)
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
                status_stdout="workflow 2.5.1 (full profile) -- drifted",
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
    """The baseline predicate's own cases (revision 64, widened at
    revision 68, manual external round 67's ``I1``, ``REQ-T18B``, and for
    the admission of 2.6.0 in Controller 1.3.0): a bare line predicate, a
    bare closed set and the two-tier rule each agree on a different subset
    of them."""

    def test_supported_lines_and_validated_releases(self) -> None:
        self.assertEqual(managed_repo.SUPPORTED_WORKFLOW_LINES, frozenset({"2.5", "2.6"}))
        self.assertEqual(managed_repo.VALIDATED_WORKFLOW_RELEASES, frozenset({"2.5.1", "2.6.0"}))
        self.assertEqual(managed_repo.REFERENCE_WORKFLOW_RELEASE, "2.5.1")
        self.assertFalse(hasattr(managed_repo, "SUPPORTED_WORKFLOW_LINE"))

    def test_reference_release_2_5_1_is_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.5.1")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            result = managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(result.workflow_version, "2.5.1")

    def test_2_6_0_is_admitted(self) -> None:
        """Was ``test_2_6_0_refuses_outside_supported_line`` until 2.6.0 was
        admitted (Controller 1.3.0)."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.6.0")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager", release="2.6.0")
            result = managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(result.workflow_version, "2.6.0")
            self.assertEqual(result.profile, "full")
            self.assertIn("workflow 2.6.0", result.verify["stdout"])

    def test_2_7_0_without_a_protocol_script_refuses_no_protocol(self) -> None:
        """Was ``test_2_7_0_refuses_outside_supported_line``: a release newer
        than the supported lines is admitted only through the protocol."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.7.0")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["observed_workflow_version"], "2.7.0")
            self.assertEqual(evidence["supported_workflow_lines"], ["2.5", "2.6"])
            self.assertEqual(evidence["reason"], "no_protocol")
            self.assertNotIn("supported_workflow_line", evidence)

    def test_2_6_1_refuses_unvalidated(self) -> None:
        """Inside the newly supported 2.6 line, but never measured."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.6.1")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["observed_workflow_version"], "2.6.1")
            self.assertEqual(evidence["reason"], "unvalidated_release")
            self.assertEqual(evidence["supported_workflow_lines"], ["2.5", "2.6"])
            self.assertEqual(evidence["validated_workflow_releases"], ["2.5.1", "2.6.0"])
            self.assertIn("supported line '2.6'", str(ctx.exception))

    def test_2_4_0_refuses_outside_supported_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.4.0")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["observed_workflow_version"], "2.4.0")
            self.assertEqual(evidence["supported_workflow_lines"], ["2.5", "2.6"])
            self.assertEqual(evidence["reference_workflow_release"], "2.5.1")
            self.assertEqual(evidence["reason"], "outside_supported_line")

    def test_2_3_1_the_superseded_baseline_refuses_outside_supported_line(self) -> None:
        """The Controller no longer runs against the release it was
        designed on -- a stated, tested fact rather than a side effect."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.3.1")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(ctx.exception.evidence["reason"], "outside_supported_line")

    def test_non_dotted_version_refuses_outside_supported_line_never_prefix_matched(self) -> None:
        for bogus in ("latest", ""):
            with self.subTest(workflow_version=bogus):
                with tempfile.TemporaryDirectory() as td:
                    repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version=bogus)
                    stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
                    with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                        managed_repo.inspect(repo, manager_bin=str(stub))
                    self.assertEqual(ctx.exception.evidence["reason"], "outside_supported_line")

    def test_2_5_0_a_real_distributed_release_refuses_unvalidated(self) -> None:
        """Revision 64 asserted this admitted (no closed 2.5.1-only set
        would have that); revision 68 asserts it refused, by name, as the
        direct consequence of manual external round 67's `I1`."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version="2.5.0")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["observed_workflow_version"], "2.5.0")
            self.assertEqual(evidence["reason"], "unvalidated_release")
            self.assertEqual(evidence["validated_workflow_releases"], ["2.5.1", "2.6.0"])
            self.assertIn("supported line '2.5'", str(ctx.exception))

    def test_req_t18b_same_inventory_unvalidated_release_refuses_before_any_inventory_read(self) -> None:
        """`REQ-T18B`: a fixture whose ``.claude/commands/`` tree and
        ``scripts/workflow_state.py`` are byte-identical to the admitted
        (2.5.1) case, so the phase set, command-file partition and
        user-only set CP3/CP4 read are, by construction, identical --
        while ``installation.json`` alone declares an unvalidated
        ``2.5.2``, a release the Manager's own ``distribution/`` does not
        carry. This is the case a bare inventory-equality fallback would
        (wrongly) readmit; ``managed_repo.inspect`` never reads a command
        file or ``KNOWN_PHASES`` at all, so refusal here is by
        construction, not by an explicit ordering check."""
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_workflow_line_fixture(Path(td) / "repo", workflow_version="2.5.2")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["observed_workflow_version"], "2.5.2")
            self.assertEqual(evidence["reason"], "unvalidated_release")


class InstallProfilePairTest(unittest.TestCase):
    def test_runtime_profile_at_2_5_1_is_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", profile="runtime")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            result = managed_repo.inspect(repo, manager_bin=str(stub))
            self.assertEqual(result.profile, "runtime")

    @unittest.skipUnless(REAL_WORKFLOW_MANAGER, "no real workflow-manager installed")
    def test_real_runtime_profile_bootstrap_installs_all_seventeen_command_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "runtime-repo"
            repo.mkdir()
            fixtures.git_init(repo)
            fixtures.run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=repo)
            fixtures.run(["git", "config", "user.name", "Controller Tests"], cwd=repo)
            fixtures.run(["git", "commit", "-q", "--allow-empty", "-m", "root"], cwd=repo)
            bootstrap = fixtures.run(
                [REAL_WORKFLOW_MANAGER, "--release-version", managed_repo.REFERENCE_WORKFLOW_RELEASE,
                 "bootstrap", "--profile", "runtime", str(repo)],
                check=False,
            )
            if bootstrap.returncode != 0:
                self.skipTest(
                    f"real workflow-manager bootstrap --profile runtime failed: "
                    f"{bootstrap.stderr.strip()}"
                )
            result = managed_repo.inspect(repo)
            self.assertEqual(result.profile, "runtime")
            self.assertEqual(result.workflow_version, managed_repo.REFERENCE_WORKFLOW_RELEASE)
            command_files = sorted((repo / ".claude" / "commands").glob("*.md"))
            self.assertEqual(len(command_files), 17)

    def test_unknown_profile_at_2_5_1_refuses_naming_observed_and_supported_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_managed_repo(Path(td) / "repo", profile="mystery")
            stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
            with self.assertRaises(UnsupportedInstallProfileError) as ctx:
                managed_repo.inspect(repo, manager_bin=str(stub))
            evidence = ctx.exception.evidence
            self.assertEqual(evidence["observed_profile"], "mystery")
            self.assertEqual(evidence["observed_workflow_version"], "2.5.1")
            self.assertIn("full", evidence["supported_profiles"])
            self.assertIn("runtime", evidence["supported_profiles"])

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



class InstalledWorkflowVersionTest(unittest.TestCase):
    """``installed_workflow_version`` (workflow-2-6-integration CP3): a plain
    manifest read, never a Workflow Manager call, whose only failures are
    the parser's own two refusals."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_it_reads_the_declared_release_without_the_manager(self) -> None:
        fixtures.write_installation_manifest(self.root, workflow_version="2.6.0")
        with unittest.mock.patch.object(managed_repo, "_run", side_effect=AssertionError("the Manager ran")):
            self.assertEqual(managed_repo.installed_workflow_version(self.root), "2.6.0")

    def test_a_missing_or_malformed_manifest_is_the_parser_s_own_refusal(self) -> None:
        with self.assertRaises(UnmanagedRepositoryError):
            managed_repo.installed_workflow_version(self.root)
        fixtures.write_installation_manifest(self.root, schema_version=2)
        with self.assertRaises(MalformedInstallationManifestError):
            managed_repo.installed_workflow_version(self.root)

    def test_an_os_error_the_parser_does_not_map_is_a_malformed_manifest(self) -> None:
        with unittest.mock.patch.object(managed_repo, "_read_manifest",
                                        side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(MalformedInstallationManifestError) as caught:
                managed_repo.installed_workflow_version(self.root)
        self.assertIn("Permission denied", caught.exception.message)


if __name__ == "__main__":
    unittest.main()


def _protocol_target(td: str, *, release: str = "2.7.0", edit=None) -> tuple[Path, Path]:
    """A committed target carrying the vendored 2.7.0 release, its record
    listing the release's files; ``edit(root)`` may change it before the
    commit (the stub manager stands in for ``verify``, which would catch it)."""
    root = Path(td) / "repo"
    fixtures.git_init(root)
    fixtures.install_workflow_release(root, "2.7.0")
    if release != "2.7.0":
        record = root / ".workflow-manager" / "installation.json"
        record.write_text(record.read_text().replace('"workflow_version": "2.7.0"',
                                                     f'"workflow_version": "{release}"'))
        script = root / "scripts" / "workflow_protocol.py"
        script.write_text(script.read_text().replace('WORKFLOW_RELEASE = "2.7.0"',
                                                     f'WORKFLOW_RELEASE = "{release}"'))
    if edit is not None:
        edit(root)
    fixtures.commit_all(root, "install")
    stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager", release=release)
    return root, stub


class ProtocolAdmissionTest(unittest.TestCase):
    def test_2_7_0_is_admitted_in_protocol_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, stub = _protocol_target(td)
            result = managed_repo.inspect(root, manager_bin=str(stub))
            self.assertEqual(result.workflow_version, "2.7.0")
            self.assertEqual(result.target_protocol, {"major": 1, "version": "1.0", "release": "2.7.0"})
            self.assertIn("scripts/workflow_protocol.py", result.script_digests)
            self.assertIn("scripts/workflow_state.py", result.script_digests)
            self.assertNotIn("scripts/workflow_state_test.py", result.script_digests)

    def test_every_vendored_protocol_release_is_runnable(self) -> None:
        """Pins protocol major 1 per vendored protocol release: installed into
        a seeded disposable repository its ``describe`` supports major 1, the
        Controller admits it in protocol mode, and its ``verify`` is healthy."""
        releases = [release for release in sorted(p.name for p in fixtures.WORKFLOW_RELEASES_DIR.iterdir() if p.is_dir())
                    if "scripts/workflow_protocol.py" in fixtures.workflow_release_files(release)]
        self.assertLessEqual({"2.7.0", "2.9.0"}, set(releases))
        for release in releases:
            with self.subTest(release=release), tempfile.TemporaryDirectory() as td:
                root = Path(td) / "repo"
                fixtures.git_init(root)
                fixtures.install_workflow_release(root, release)
                ai_workflow = root / "docs" / "ai-workflow"
                ai_workflow.mkdir(parents=True, exist_ok=True)
                (ai_workflow / "WORKFLOW_CONFIG.json").write_text(json.dumps(
                    {"schema_version": 1, "default_workflow_version": "2.2",
                     "supported_versions": ["1", "2.1", "2.2"]}) + "\n")
                (ai_workflow / "WORKFLOW_STATE.json").write_text(json.dumps(
                    {"schema_version": 1, "active_work_item_id": None, "work_items": {}}) + "\n")
                fixtures.commit_all(root, "install")
                self.assertIn(1, protocol.describe(root).supported_protocol_majors)
                stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager", release=release)
                result = managed_repo.inspect(root, manager_bin=str(stub))
                self.assertEqual(result.workflow_version, release)
                self.assertEqual(result.target_protocol["major"], 1)
                verify = protocol.verify(root)
                self.assertTrue(verify.healthy, verify.checks)
                self.assertEqual({check.status for check in verify.checks}, {"pass"}, verify.checks)

    def test_legacy_releases_have_no_protocol_identity(self) -> None:
        for release in ("2.5.1", "2.6.0"):
            with self.subTest(release=release), tempfile.TemporaryDirectory() as td:
                repo = fixtures.build_managed_repo(Path(td) / "repo", workflow_version=release)
                stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager", release=release)
                result = managed_repo.inspect(repo, manager_bin=str(stub))
                self.assertIsNone(result.target_protocol)
                self.assertIsNone(result.script_digests)

    def test_a_bumped_release_stand_in_is_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, stub = _protocol_target(td, release="2.7.1")
            result = managed_repo.inspect(root, manager_bin=str(stub))
            self.assertEqual(result.workflow_version, "2.7.1")
            self.assertEqual(result.target_protocol["release"], "2.7.1")

    def test_an_extra_sibling_module_is_admitted_with_its_digest_recorded(self) -> None:
        def edit(root: Path) -> None:
            script = root / "scripts" / "workflow_protocol.py"
            script.write_text(script.read_text().replace("import workflow_state  # noqa: E402",
                                                         "import workflow_state  # noqa: E402\nimport workflow_extra  # noqa: E402"))
            (root / "scripts" / "workflow_extra.py").write_text("VALUE = 1\n")
            record = root / ".workflow-manager" / "installation.json"
            data = json.loads(record.read_text())
            data["managed"]["scripts/workflow_extra.py"] = {"sha256": "0" * 64, "executable": False}
            record.write_text(json.dumps(data))
        with tempfile.TemporaryDirectory() as td:
            root, stub = _protocol_target(td, edit=edit)
            result = managed_repo.inspect(root, manager_bin=str(stub))
            self.assertIn("scripts/workflow_extra.py", result.script_digests)

    def test_a_missing_sibling_module_is_refused_naming_it(self) -> None:
        def edit(root: Path) -> None:
            (root / "scripts" / "workflow_fingerprint.py").unlink()
            record = root / ".workflow-manager" / "installation.json"
            data = json.loads(record.read_text())
            del data["managed"]["scripts/workflow_fingerprint.py"]
            record.write_text(json.dumps(data))
        with tempfile.TemporaryDirectory() as td:
            root, stub = _protocol_target(td, edit=edit)
            with self.assertRaises(WorkflowProtocolFailedError) as ctx:
                managed_repo.inspect(root, manager_bin=str(stub))
            self.assertIn("workflow_fingerprint", str(ctx.exception))
            self.assertEqual(ctx.exception.evidence["missing_module"], "workflow_fingerprint")

    def test_a_non_workflow_symlink_in_scripts_does_not_block_admission(self) -> None:
        def edit(root: Path) -> None:
            (root / "scripts" / "extra.py").symlink_to("/etc/hostname")
        with tempfile.TemporaryDirectory() as td:
            root, stub = _protocol_target(td, edit=edit)
            result = managed_repo.inspect(root, manager_bin=str(stub))
            self.assertNotIn("scripts/extra.py", result.script_digests)

    def test_a_release_without_the_protocol_script_is_refused_no_protocol(self) -> None:
        for managed in (None, "text", {"scripts/workflow_state.py": {}}):
            with self.subTest(managed=managed), tempfile.TemporaryDirectory() as td:
                repo = fixtures.build_bare_git_repo(Path(td) / "repo")
                fixtures.write_installation_manifest(repo, workflow_version="2.7.0", managed=managed)
                fixtures.commit_all(repo, "m")
                stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager")
                with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                    managed_repo.inspect(repo, manager_bin=str(stub))
                self.assertEqual(ctx.exception.evidence["reason"], "no_protocol")

    def test_protocol_major_2_is_refused(self) -> None:
        def edit(root: Path) -> None:
            script = root / "scripts" / "workflow_protocol.py"
            script.write_text(script.read_text().replace("PROTOCOL_MAJOR = 1", "PROTOCOL_MAJOR = 2"))
        with tempfile.TemporaryDirectory() as td:
            root, stub = _protocol_target(td, edit=edit)
            with self.assertRaises(UnsupportedWorkflowVersionError) as ctx:
                managed_repo.inspect(root, manager_bin=str(stub))
            self.assertEqual(ctx.exception.evidence["reason"], "unsupported_protocol_major")

    def test_a_listed_protocol_script_that_is_a_symlink_is_refused_naming_it(self) -> None:
        def edit(root: Path) -> None:
            script = root / "scripts" / "workflow_protocol.py"
            script.rename(root / "scripts" / "elsewhere.txt")
            script.symlink_to("elsewhere.txt")
        with tempfile.TemporaryDirectory() as td:
            root, stub = _protocol_target(td, edit=edit)
            with self.assertRaises(WorkflowProtocolFailedError) as ctx:
                managed_repo.inspect(root, manager_bin=str(stub))
            self.assertEqual(ctx.exception.evidence["path"], "scripts/workflow_protocol.py")


class ProtocolIdentityTest(unittest.TestCase):
    def test_a_non_workflow_script_edit_leaves_the_identity_equal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, _stub = _protocol_target(td)
            (root / "scripts" / "extra.py").write_text("A = 1\n")
            first = protocol.identity(root)
            (root / "scripts" / "extra.py").write_text("A = 2\n")
            second = protocol.identity(root)
            self.assertEqual(first, second)
            self.assertEqual(first.release, "2.7.0")

    def test_an_edited_managed_script_changes_the_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, _stub = _protocol_target(td)
            first = protocol.identity(root)
            state = root / "scripts" / "workflow_state.py"
            state.write_text(state.read_text() + "\n# edited\n")
            second = protocol.identity(root)
            self.assertNotEqual(first, second)
            self.assertEqual(first.release, second.release)
            changed = {key for key in first.digests if first.digests[key] != second.digests[key]}
            self.assertEqual(changed, {"scripts/workflow_state.py"})
