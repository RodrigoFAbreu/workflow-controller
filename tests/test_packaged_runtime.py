"""Packaged-runtime regression suite (`workflow-controller-release-runtime-
observability` CP3).

Every case runs a real, non-editable install of a wheel built from a
disposable committed clone of this repository (``tests.fixtures.
build_checkout``), exactly as an operator's ``pipx install`` would run it:
the installed ``workflow-controller`` console script, in its own venv, as a
subprocess. The worker is ``tests/fake_claude.py`` and the Workflow Manager
is the offline stub, so nothing here needs a network.

Each test class builds its own wheel with host setuptools and
``--no-build-isolation``. The module skips, naming the missing piece, when
``tests.fixtures.wheel_build_prerequisite()`` reports the build prerequisite
missing, unless ``CONTROLLER_REQUIRE_PACKAGING_TESTS=1`` makes that a
failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import buildinfo, gitrepo, identity, milestone_branch as mb, repo_policy, runtime  # noqa: E402
from controller.cli import EXIT_FAIL_CLOSED, EXIT_HANDOFF_PENDING, EXIT_OK  # noqa: E402
from tests import fake_gh, fixtures  # noqa: E402
from tests import test_lifecycle_orchestration as lifecycle  # noqa: E402
from tests.test_milestone_branch import policy  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
WI = "wi-1"
STATE_REL = Path("docs") / "ai-workflow" / "WORKFLOW_STATE.json"

#: The version a generation-2 upgrade wheel is built at (case 8).
UPGRADE_VERSION = "1.99.0"
#: The version a post-install commit moves the clone to (case 4).
MOVED_VERSION = "9.9.9"


def _write_generation(checkout: Path, generation: int) -> None:
    (checkout / "controller" / "GENERATION.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}) + "\n"
    )


def _write_version(checkout: Path, value: str) -> None:
    """Move ``checkout``'s static ``[project].version`` (every
    ``fixtures.build_checkout`` clone declares one) to ``value``."""
    path = checkout / "pyproject.toml"
    text = path.read_text()
    replaced = text.replace(f'version = "{fixtures.CONTROLLER_VERSION}"', f'version = "{value}"', 1)
    assert replaced != text, "pyproject.toml does not declare the static version as expected"
    path.write_text(replaced)


def _only_wheel(out_dir: Path) -> Path:
    wheels = sorted(out_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def _site_packages(venv_dir: Path) -> Path:
    candidates = sorted(venv_dir.glob("lib/python3*/site-packages"))
    assert len(candidates) == 1, candidates
    return candidates[0]


def _base_env() -> dict:
    """The host environment minus everything that could steer the runtime
    under test: a runtime-root override, a leaked exec handoff, an inherited
    ``PYTHONPATH`` (which would import this repository's own package), Git's
    own overrides, and a release tag meant for a build."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_") and not key.startswith("FAKE_CLAUDE_")
        and key not in {
            "PYTHONPATH", "WORKFLOW_CONTROLLER_HOME", "WORKFLOW_CONTROLLER_RELEASE_TAG",
            identity.EXEC_HANDOFF_ENV,
        }
    }
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def _build_planning_target(root: Path) -> Path:
    """A committed ``"2.1"`` ``PLANNING`` target: ``step`` launches
    ``/milestone-plan`` for it, the minimal automatic worker launch."""
    fixtures.build_target_git_repo(root)
    (root / "README.md").write_text("packaged-runtime fixture\n")
    (root / ".gitignore").write_text(".ai-review/\n")
    fixtures.write_installation_manifest(root)
    base = fixtures.commit_all(root, "initial")
    fixtures.write_workflow_state(root, {
        "schema_version": 1,
        "active_work_item_id": WI,
        "work_items": {WI: {
            "work_item_type": "product",
            "work_item_kind": "product",
            "work_item_id": WI,
            "governing_workflow_version": "2.1",
            "phase": "PLANNING",
            "plan_revision": 1,
            "implementation_revision": None,
            "state_revision": 1,
            "checkpoints": {},
            "current_bundle_id": None,
            "last_completed_checkpoint_id": None,
            "base_commit": base,
            "parent_work_item_id": None,
        }},
    })
    fixtures.commit_all(root, "Seed the work item")
    return root


def _planning_worker_env(root: Path, invocations: Path) -> dict[str, str]:
    """The fake worker performs ``/milestone-plan``'s observable effect --
    the phase edit to ``AWAITING_LOCAL_PLAN_REVIEW`` and a coherent plan
    ``MANIFEST.md`` -- and counts itself in ``invocations``."""
    state_path = root / STATE_REL
    state = json.loads(state_path.read_text())
    state["work_items"][WI]["phase"] = "AWAITING_LOCAL_PLAN_REVIEW"
    env = {
        "FAKE_CLAUDE_WRITE_PATH": str(state_path),
        "FAKE_CLAUDE_WRITE_TEXT": json.dumps(state, indent=2) + "\n",
        "FAKE_CLAUDE_INVOCATIONS_FILE": str(invocations),
    }
    env.update(fixtures.fake_worker_plan_manifest_env(root, WI, 1))
    return env


def _build_policy_target(tmp: Path) -> tuple[Path, Path]:
    """``(origin, clone)``: a bare origin whose ``main`` carries the real
    Workflow 2.5.1 tree, the reference policy and an empty Workflow state,
    and a clone of it on ``main`` -- ``tests.test_trunk_orchestration_e2e``'s
    policy-enabled target."""
    seed = fixtures.build_workflow_line_fixture(tmp / "seed", workflow_version="2.5.1")
    fixtures.run(["git", "branch", "-q", "-M", "main"], cwd=seed)
    (seed / ".gitignore").write_text(".ai-review/\n")
    (seed / "README.md").write_text("packaged-runtime policy fixture\n")
    (seed / repo_policy.POLICY_PATH).parent.mkdir(parents=True, exist_ok=True)
    (seed / repo_policy.POLICY_PATH).write_text(json.dumps(policy(), indent=2) + "\n")
    fixtures.write_workflow_state(seed, {"schema_version": 1, "active_work_item_id": None, "work_items": {}})
    fixtures.commit_all(seed, "Add the policy and the Workflow state")
    origin = tmp / "origin.git"
    fixtures.git_init(origin, "--bare", "--initial-branch=main")
    fixtures.run(["git", "push", "-q", str(origin), "main"], cwd=seed)
    clone = tmp / "clone"
    fixtures.git_clone(origin, clone)
    fixtures.run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=clone)
    fixtures.run(["git", "config", "user.name", "Controller Tests"], cwd=clone)
    return origin, clone.resolve()


class _PackagedRuntimeCase(unittest.TestCase):
    """One wheel per class, built in ``setUpClass`` from a committed
    generation-1 clone at ``cls.clone``."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._class_tmp = tempfile.TemporaryDirectory()
        cls.class_tmp = Path(cls._class_tmp.name)
        missing = fixtures.wheel_build_prerequisite()
        if missing is not None:
            cls._class_tmp.cleanup()
            if os.environ.get(fixtures.REQUIRE_PACKAGING_TESTS_ENV) == "1":
                raise AssertionError(f"wheel-build prerequisite missing: {missing}")
            raise unittest.SkipTest(f"wheel-build prerequisite missing: {missing}")
        cls.clone = fixtures.build_checkout(cls.class_tmp / "clone", generation=1)
        cls.clone_head = fixtures.current_head(cls.clone)
        cls.wheel = cls.build(cls.clone, cls.class_tmp / "dist")
        with zipfile.ZipFile(cls.wheel) as zf:
            cls.wheel_build_info = json.loads(zf.read(f"controller/{buildinfo.BUILD_INFO_NAME}"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._class_tmp.cleanup()
        super().tearDownClass()

    @classmethod
    def build(cls, source: Path, out_dir: Path) -> Path:
        result = fixtures.build_wheel(source, out_dir, env=_base_env(), check=False)
        if result.returncode != 0:
            raise AssertionError(f"wheel build failed:\n{result.stdout}\n{result.stderr}")
        return _only_wheel(out_dir)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.xdg = self.tmp / "xdg-state"
        self.xdg_runtime = (self.xdg / "workflow-controller").resolve()
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp / "workflow-manager")
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        self._targets = 0

    # --- running the installed console script --------------------------------------

    def controller(self, script: Path, *args: str, env: dict | None = None,
                   runtime_dir: Path | None = None) -> subprocess.CompletedProcess:
        full_env = _base_env()
        full_env["XDG_STATE_HOME"] = str(self.xdg)
        full_env.update(env or {})
        argv = [str(script)]
        if runtime_dir is not None:
            argv += ["--runtime-dir", str(runtime_dir)]
        argv += ["--workflow-manager", str(self.stub_manager), "--claude-binary", str(FAKE_CLAUDE),
                 "--timeout", "60", *args]
        return subprocess.run(argv, cwd=self.cwd, env=full_env, capture_output=True, text=True,
                              timeout=180, check=False)

    def new_target(self) -> Path:
        self._targets += 1
        return _build_planning_target(self.tmp / f"target-{self._targets}")

    def step(self, script: Path, *global_args: str, runtime_dir: Path | None = None,
             expect: int | None = EXIT_OK) -> tuple[subprocess.CompletedProcess, Path, Path]:
        """``step`` against a fresh ``PLANNING`` target whose worker performs
        its transition. Returns the result, the target and the worker's
        invocation counter file."""
        target = self.new_target()
        invocations = self.tmp / f"invocations-{self._targets}"
        result = self.controller(script, *global_args, "step", str(target),
                                 env=_planning_worker_env(target, invocations), runtime_dir=runtime_dir)
        if expect is not None:
            self.assertEqual(result.returncode, expect, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        return result, target, invocations

    def install(self, name: str = "venv", wheel: Path | None = None) -> Path:
        return fixtures.wheel_install(wheel or self.wheel, self.tmp / name)

    # --- reading what it wrote --------------------------------------------------

    @staticmethod
    def job_records(runtime_root: Path) -> list[dict]:
        jobs = runtime_root / "jobs"
        paths = sorted(jobs.glob("*.json"), key=lambda p: p.stat().st_mtime_ns) if jobs.is_dir() else []
        return [json.loads(p.read_text()) for p in paths]

    def only_job_record(self, runtime_root: Path) -> dict:
        records = self.job_records(runtime_root)
        self.assertEqual(len(records), 1, records)
        return records[0]

    @staticmethod
    def worker_count(invocations: Path) -> int:
        return len(invocations.read_text().splitlines()) if invocations.exists() else 0

    def assert_package_runtime(self, block: dict, *, pinned: bool = True) -> None:
        """``block`` (a ``controller_runtime``) names this class's wheel. A
        read-only command runs unpinned, straight from ``site-packages``."""
        self.assertEqual(block["runtime_kind"], identity.RUNTIME_KIND_PACKAGE)
        self.assertEqual(block["source_kind"],
                         identity.SOURCE_KIND_PACKAGE if pinned else identity.SOURCE_KIND_UNPINNED)
        self.assertEqual(block["version"], fixtures.CONTROLLER_VERSION)
        self.assertEqual(block["source_commit"], self.clone_head)
        self.assertEqual(block["package_digest"], self.wheel_build_info["package_digest"])
        self.assertEqual(block["build_origin"], buildinfo.BUILD_ORIGIN_LOCAL)
        # An unpinned identity resolves no generation, as for a source runtime.
        self.assertEqual(block["generation"], 1 if pinned else None)

    def assert_finished_package_job(self, runtime_root: Path, target: Path, invocations: Path) -> dict:
        record = self.only_job_record(runtime_root)
        self.assertEqual(record["status"], "FINISHED", record.get("reconciliation_evidence"))
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["target_repo"], str(target.resolve()))
        self.assertEqual(record["controller_source_commit"], self.clone_head)
        self.assert_package_runtime(record["controller_runtime"])
        self.assertEqual(self.worker_count(invocations), 1)
        return record

    def assert_version_output(self, script: Path) -> None:
        result = self.controller(script, "--version")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2, result.stdout)
        self.assertEqual(lines[0], f"workflow-controller {fixtures.CONTROLLER_VERSION}")
        self.assertEqual(lines[1], f"runtime: package (local build from {self.clone_head[:12]})")


class ReproducedDefectTest(_PackagedRuntimeCase):
    """Case 1: the base commit's ``git archive failed`` exit 20 is gone."""

    def test_non_editable_install_launches_a_worker(self) -> None:
        script = self.install()
        result, target, invocations = self.step(script)
        self.assertNotIn("git archive", result.stderr)
        self.assertNotIn("git archive", result.stdout)
        record = self.assert_finished_package_job(self.xdg_runtime, target, invocations)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")


class VersionEqualsArtifactTest(_PackagedRuntimeCase):
    """Case 6: ``--version`` line 1, ``METADATA``'s ``Version:``,
    ``BUILD_INFO.version`` and ``tools/release.py version`` agree."""

    def test_version_metadata_and_build_info_agree(self) -> None:
        script = self.install()
        self.assert_version_output(script)
        site = _site_packages(self.tmp / "venv")
        metadata_paths = list(site.glob("workflow_controller-*.dist-info/METADATA"))
        self.assertEqual(len(metadata_paths), 1, metadata_paths)
        metadata_versions = [line.split(":", 1)[1].strip()
                             for line in metadata_paths[0].read_text().splitlines()
                             if line.startswith("Version:")]
        installed_build = json.loads((site / "controller" / buildinfo.BUILD_INFO_NAME).read_text())
        self.assertEqual(metadata_versions, [fixtures.CONTROLLER_VERSION])
        self.assertEqual(installed_build["version"], fixtures.CONTROLLER_VERSION)
        self.assertEqual(installed_build, self.wheel_build_info)
        # The wheel was built from a checkout carrying this repository's own
        # pyproject.toml, at the version tools/release.py reports.
        self.assertEqual((self.clone / "pyproject.toml").read_text(), fixtures.pyproject_text())
        released = fixtures.run([sys.executable, str(fixtures.REPO_ROOT / "tools" / "release.py"), "version"],
                                env=_base_env(), check=False)
        self.assertEqual((released.returncode, released.stdout), (0, f"{fixtures.CONTROLLER_VERSION}\n"),
                         released.stderr)


class TamperedInstallTest(_PackagedRuntimeCase):
    """Case 7: an installed byte edited after install refuses before any
    worker launches."""

    def test_an_edited_installed_byte_refuses_with_exit_20(self) -> None:
        script = self.install()
        module = _site_packages(self.tmp / "venv") / "controller" / "cli.py"
        data = bytearray(module.read_bytes())
        # Byte 3 is the first character of the module docstring, so the
        # edited module still imports.
        data[3] = ord("X") if data[3] != ord("X") else ord("Y")
        module.write_bytes(bytes(data))

        result, _target, invocations = self.step(script, expect=EXIT_FAIL_CLOSED)
        self.assertIn("package digest", result.stderr)
        self.assertIn(self.wheel_build_info["package_digest"], result.stderr)
        self.assertEqual(self.worker_count(invocations), 0)
        self.assertEqual(self.job_records(self.xdg_runtime), [])


class _CheckoutGoneCase(_PackagedRuntimeCase):
    """Cases 2 and 3: the clone the wheel was built from is no longer where
    it was. Every command still works, with the build's identity."""

    def remove_checkout(self) -> None:
        raise NotImplementedError

    def test_every_command_works_without_the_checkout(self) -> None:
        script = self.install()
        self.remove_checkout()
        self.assertFalse(self.clone.exists())

        self.assert_version_output(script)

        inspected = self.controller(script, "--json", "inspect", str(self.new_target()))
        self.assertEqual(inspected.returncode, EXIT_OK, inspected.stderr)
        self.assert_package_runtime(json.loads(inspected.stdout)["controller"], pinned=False)

        status = self.controller(script, "status")
        self.assertEqual(status.returncode, EXIT_OK, status.stderr)
        self.assertEqual(
            status.stdout.splitlines()[0],
            f"controller: workflow-controller {fixtures.CONTROLLER_VERSION} -- "
            f"package (local build from {self.clone_head[:12]})",
        )

        _result, target, invocations = self.step(script)
        self.assert_finished_package_job(self.xdg_runtime, target, invocations)
        identity_record = runtime.read_json(self.xdg_runtime / "identity.json")
        self.assert_package_runtime(identity_record["controller_runtime"])

        self.assert_branch_and_pr_work(script)

    def assert_branch_and_pr_work(self, script: Path) -> None:
        """A policy-enabled target: the bind step, then the branch-side step
        that pushes the branch and opens the Draft PR (the plan's
        "Compatibility and migration", R16)."""
        case_dir = self.tmp / "policy"
        origin, root = _build_policy_target(case_dir)
        trunk_tip = fixtures.current_head(root)
        gh_env = fixtures.fake_gh_env(case_dir, origin=origin)
        branch = f"milestone/{WI}"
        key = mb.repo_key(gitrepo.common_dir(root))
        lc = lifecycle.Lifecycle(case_dir, root, trunk_tip, phase="AWAITING_LOCAL_PLAN_REVIEW", checkpoints={},
                                 plan_approval=None, plan_review_stages=None)
        env = {name: gh_env[name] for name in ("PATH", "FAKE_GH_STATE", "FAKE_GH_ORIGIN", "FAKE_GH_LOG",
                                               "FAKE_GH_FAIL")}
        env.update(FAKE_CLAUDE_SCRIPT=str(lc.script_path), FAKE_CLAUDE_INVOCATIONS_FILE=str(lc.processes_file))

        def step(command: str) -> dict:
            fixtures.write_worker_script(lc.script_path, lc.script)
            result = self.controller(script, "step", str(root), env=env)
            self.assertEqual(result.returncode, EXIT_OK, f"stdout={result.stdout!r} stderr={result.stderr!r}")
            record = self.job_records(self.xdg_runtime)[-1]
            self.assertEqual(record["status"], "FINISHED", record.get("reconciliation_evidence"))
            self.assertEqual(record["selected_action"]["command"], command)
            self.assertEqual(record["branch_binding"]["branch"], branch)
            self.assert_package_runtime(record["controller_runtime"])
            return mb.read_record(self.xdg_runtime, key, WI)

        # /milestone-plan's effect, uncommitted on the trunk: the trunk start.
        registry = {"schema_version": 1, "work_item_id": WI, "plan_revision": 1,
                    "checkpoints": [{"id": cid, "name": cid, "depends_on": [], "complexity": 1,
                                     "session_target": 1} for cid in lifecycle.CHECKPOINT_IDS]}
        lc.perform([
            fixtures.script_write(f"docs/plans/{WI}.md", f"# The plan of {WI}\n"),
            fixtures.script_write(fixtures.registry_rel_path(WI), json.dumps(registry, indent=2) + "\n"),
            fixtures.script_write(f".ai-review/{WI}/current/MANIFEST.md",
                                  fixtures.build_plan_manifest_text(WI, 1, bundle_id="a" * 64)),
            fixtures.script_write(lifecycle.STATE_REL, lc.state_text()),
        ])

        # The bind step: /review-plan's local APPROVE runs on the new branch.
        lc.entry.update(phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", plan_review_stages={
            "review_content_id": "c" * 64,
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "a" * 64, "verdict": "APPROVE", "round": 1,
                                        "completed_at": lifecycle.COMPLETED_AT},
            "MANUAL_EXTERNAL_PLAN_REVIEW": None})
        lc.add(f"/review-plan {WI}", [
            fixtures.script_write(f".ai-review/{WI}/feedback/REVIEW_FEEDBACK.md", fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id="a" * 64,
                reviewed_base_commit=trunk_tip, work_item=WI)),
            lc.write_state(),
        ])
        bound = step(f"/review-plan {WI}")
        self.assertEqual((bound["state"], bound["branch_point"]), (mb.BRANCH_BOUND, trunk_tip))
        self.assertEqual(gitrepo.head_state(root).branch, branch)

        # /approve-review plan, then the step that pushes and opens the Draft PR.
        lc.sync()
        lc.entry.update(phase=lifecycle.IMPLEMENTING, plan_approval={"status": "CURRENT"})
        (root / lifecycle.STATE_REL).write_text(lc.state_text())
        approval = fixtures.commit_all(root, fixtures.trailer_message(
            f"Approve plan for {WI}", ("Workflow-Plan-Approval", "ab" * 32), ("Workflow-Work-Item", WI)))
        lc.add(lifecycle.MILESTONE_IMPLEMENT, lc.implement("CP1"))
        opened = step(lifecycle.MILESTONE_IMPLEMENT)
        self.assertEqual((opened["state"], opened["pr"]["number"]), (mb.PR_OPEN, 1))
        [pr] = fake_gh.read_state(Path(gh_env["FAKE_GH_STATE"]))["prs"]
        self.assertTrue(pr["isDraft"])
        self.assertEqual((pr["headRefName"], pr["baseRefName"]), (branch, "main"))
        pushed = fixtures.run(["git", "--git-dir", str(origin), "rev-parse", f"refs/heads/{branch}"]).stdout.strip()
        self.assertEqual(pushed, approval)


class CheckoutAbsentTest(_CheckoutGoneCase):
    """Case 2."""

    def remove_checkout(self) -> None:
        shutil.rmtree(self.clone)


class CheckoutRenamedTest(_CheckoutGoneCase):
    """Case 3."""

    def remove_checkout(self) -> None:
        os.rename(self.clone, self.class_tmp / "clone-renamed")


class CheckoutModifiedTest(_PackagedRuntimeCase):
    """Case 4: the clone moves on after install -- a new version, a new
    generation and a changed module, committed. The install does not."""

    def test_the_install_keeps_its_built_identity(self) -> None:
        script = self.install()
        _write_version(self.clone, MOVED_VERSION)
        _write_generation(self.clone, 2)
        cli_py = self.clone / "controller" / "cli.py"
        cli_py.write_text(cli_py.read_text() + "# changed after install\n")
        moved_head = fixtures.commit_all(self.clone, "move on after install")
        self.assertNotEqual(moved_head, self.clone_head)

        self.assert_version_output(script)

        result, target, invocations = self.step(script)
        self.assertNotEqual(result.returncode, EXIT_HANDOFF_PENDING)
        self.assert_finished_package_job(self.xdg_runtime, target, invocations)
        self.assertIsNone(runtime.read_json(self.xdg_runtime / "handoff.json"))
        self.assertFalse((self.clone / ".controller").exists())


class VenvInsideCheckoutTest(_PackagedRuntimeCase):
    """Case 5: a venv at ``<clone>/.venv`` is inside the clone's Git work
    tree, but the runtime is still the package, and ladder row 3 never
    fires."""

    def test_package_identity_and_xdg_root_inside_a_checkout(self) -> None:
        venv_dir = self.clone / ".venv"
        script = fixtures.wheel_install(self.wheel, venv_dir)
        self.addCleanup(shutil.rmtree, venv_dir, True)
        site = _site_packages(venv_dir)
        inside = fixtures.run(["git", "-C", str(site), "rev-parse", "--show-toplevel"], check=False)
        self.assertEqual(inside.returncode, 0, "the venv is not inside the clone's work tree")

        version_py = self.clone / "controller" / "version.py"
        version_py.write_text(version_py.read_text() + "# a later commit\n")
        fixtures.run(["git", "add", "--", "controller/version.py"], cwd=self.clone)
        fixtures.run(["git", "commit", "-q", "-m", "a later commit"], cwd=self.clone)
        new_head = fixtures.current_head(self.clone)
        self.assertNotEqual(new_head, self.clone_head)

        status = self.controller(script, "status")
        self.assertEqual(status.returncode, EXIT_OK, status.stderr)
        self.assertIn(f"no Controller runtime state at {self.xdg_runtime} (ladder row {runtime.LADDER_XDG_STATE})",
                      status.stdout)
        after_status = runtime.read_json(self.xdg_runtime / "identity.json")
        self.assertEqual(after_status["source_commit"], self.clone_head)
        self.assert_package_runtime(after_status["controller_runtime"], pinned=False)

        (self.xdg_runtime / "identity.json").unlink()
        inspected = self.controller(script, "inspect", str(self.new_target()))
        self.assertEqual(inspected.returncode, EXIT_OK, inspected.stderr)
        after_inspect = runtime.read_json(self.xdg_runtime / "identity.json")
        self.assertEqual(after_inspect["source_commit"], self.clone_head)
        self.assert_package_runtime(after_inspect["controller_runtime"], pinned=False)

        _result, target, invocations = self.step(script)
        self.assert_finished_package_job(self.xdg_runtime, target, invocations)

        explicit = self.tmp / "explicit-runtime"
        _result, target, invocations = self.step(script, runtime_dir=explicit)
        self.assert_finished_package_job(explicit, target, invocations)

        self.assertFalse((site / ".controller").exists())
        self.assertFalse((self.clone / ".controller").exists())
        self.assertEqual(list(venv_dir.rglob(".controller")), [])


class UpgradeHandoffTest(_PackagedRuntimeCase):
    """Case 8: installing a generation-2 wheel while a generation-1 ``run``
    is paused at the orchestration boundary stops that run with exit 50."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        _write_version(cls.clone, UPGRADE_VERSION)
        _write_generation(cls.clone, 2)
        cls.upgrade_head = fixtures.commit_all(cls.clone, "generation 2")
        cls.upgrade_wheel = cls.build(cls.clone, cls.class_tmp / "dist-2")

    def test_reinstalling_a_newer_generation_hands_off(self) -> None:
        script = self.install()
        target = self.tmp / "managed"
        fixtures.build_managed_repo(target)
        runtime_root = self.tmp / "runtime"
        sentinel = self.tmp / "pause.sentinel"
        sentinel.write_text("")
        invocations = self.tmp / "invocations"

        env = _base_env()
        env.update({"XDG_STATE_HOME": str(self.xdg), "WORKFLOW_CONTROLLER_TEST_HOOKS": "1",
                    "FAKE_CLAUDE_INVOCATIONS_FILE": str(invocations)})
        proc = subprocess.Popen(
            [str(script), "--runtime-dir", str(runtime_root), "--workflow-manager", str(self.stub_manager),
             "--claude-binary", str(FAKE_CLAUDE), "run", str(target), "--pause-file", str(sentinel)],
            cwd=self.cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 60
            record = None
            while record is None and time.monotonic() < deadline and proc.poll() is None:
                record = runtime.read_json(runtime_root / "identity.json")
                time.sleep(0.05)
            self.assertIsNotNone(record, "the paused run never wrote identity.json")
            self.assertEqual(record["generation"], 1)
            self.assert_package_runtime(record["controller_runtime"])

            fixtures.wheel_install(self.upgrade_wheel, self.tmp / "venv", reinstall=True)
            installed = json.loads(
                (_site_packages(self.tmp / "venv") / "controller" / "GENERATION.json").read_text())
            self.assertEqual(installed["generation"], 2)

            sentinel.unlink()
            stdout, stderr = proc.communicate(timeout=60)
        except BaseException:
            proc.kill()
            proc.communicate()
            raise

        self.assertEqual(proc.returncode, EXIT_HANDOFF_PENDING, f"stdout={stdout!r} stderr={stderr!r}")
        handoff_record = runtime.read_json(runtime_root / "handoff.json")
        self.assertEqual(handoff_record["running"],
                         {"generation": 1, "commit": self.clone_head, "version": fixtures.CONTROLLER_VERSION})
        self.assertEqual(handoff_record["approved"],
                         {"generation": 2, "commit": self.upgrade_head, "version": UPGRADE_VERSION})
        self.assertEqual(self.worker_count(invocations), 0)


class EditableSourceRetainedTest(_PackagedRuntimeCase):
    """Case 9: an editable install of a clone is still a ``source``
    runtime -- a commit snapshot, runtime root ``<clone>/.controller`` --
    and a dirty edit still needs ``--allow-dirty-source``."""

    def test_editable_install_runs_from_source(self) -> None:
        checkout = fixtures.build_checkout(self.tmp / "editable-clone", generation=1)
        head = fixtures.current_head(checkout)
        script = fixtures.editable_install(checkout, self.tmp / "editable-venv")
        row3 = (checkout / ".controller").resolve()

        _result, target, invocations = self.step(script)
        record = self.only_job_record(row3)
        self.assertEqual(record["status"], "FINISHED", record.get("reconciliation_evidence"))
        self.assertEqual(self.worker_count(invocations), 1)
        block = record["controller_runtime"]
        self.assertEqual(block["runtime_kind"], identity.RUNTIME_KIND_SOURCE)
        self.assertEqual(block["source_kind"], identity.SOURCE_KIND_COMMIT)
        self.assertEqual(block["source_commit"], head)
        self.assertIsNone(block["package_digest"])
        identity_record = runtime.read_json(row3 / "identity.json")
        self.assertEqual(identity_record["source_kind"], identity.SOURCE_KIND_COMMIT)
        self.assertFalse(self.xdg_runtime.exists())

        cli_py = checkout / "controller" / "cli.py"
        cli_py.write_text(cli_py.read_text() + "# an uncommitted edit\n")
        refused, _target, invocations = self.step(script, expect=EXIT_FAIL_CLOSED)
        self.assertIn("--allow-dirty-source", refused.stderr)
        self.assertEqual(self.worker_count(invocations), 0)

        _result, _target, invocations = self.step(script, "--allow-dirty-source")
        self.assertEqual(self.worker_count(invocations), 1)
        dirty_record = self.job_records(row3)[-1]
        self.assertEqual(dirty_record["status"], "FINISHED", dirty_record.get("reconciliation_evidence"))
        self.assertEqual(dirty_record["controller_runtime"]["runtime_kind"], identity.RUNTIME_KIND_SOURCE)
        self.assertEqual(dirty_record["controller_runtime"]["source_kind"], identity.SOURCE_KIND_WORKTREE)


del _PackagedRuntimeCase, _CheckoutGoneCase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
