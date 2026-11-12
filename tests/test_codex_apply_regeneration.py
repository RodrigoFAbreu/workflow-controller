"""`workflow-controller-h4e3b` CP3: the guarded generator run after a Codex apply role's landing, proved adversarially
through the job (D8).

Every case drives ``job.execute_step`` with the real carry, the real landing, the real recorder, the real guard and the
real generator on the fixture repositories of ``tests/test_job_codex_apply.py`` and the scripted fake ``codex``. The
worker's own work (and its plants) is the ``after`` hook. A plant is one function writing files into a directory: the
managed tree for the plan role (which makes no commit, so the plant sits in the tree), the worker's clone, committed,
for the implementation role (a landed plant). Each plant asserts the **marker** is never written (nothing of the
managed tree ran), the ending (the gate with the cause, or a completed regeneration), that the generator was not
started on a refusal, that no armour line is in the record and that nothing of the managed repository changed except
what a regeneration writes. ``GENERATOR_CAUSE_CASES`` maps every member of ``generator_run.CAUSES`` to a test, and a
test fails when a cause has none.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import generator_run, job  # noqa: E402
from controller import workflow_contract  # noqa: E402
from controller.errors import WorkflowQueryError  # noqa: E402
from tests import test_job_codex_apply as base  # noqa: E402
from tests.test_codex_apply_safety import Safety  # noqa: E402

ITEM = base.ITEM
STATE = base.STATE
WID = base.WID
_git = base._git

CANARY = "CANARY-7f3a9c1e-never-copied-into-a-bundle"
CACHE_TAG = sys.implementation.cache_tag
SO_NAME = "_speedups.cpython-313-x86_64-linux-gnu.so"

#: Every cause of the guarded run, with the test that proves it through the job (a test fails when one has no case).
GENERATOR_CAUSE_CASES = {
    "surface_landing_touched": ("ImplRegenerationTest", "test_a_landed_path_on_the_surface_ends_at_the_gate"),
    "surface_script_modified": ("PlanRegenerationTest", "test_a_plant_in_the_tree_ends_at_the_gate_or_runs_nothing"),
    "surface_foreign_entry": ("PlanRegenerationTest", "test_a_plant_in_the_tree_ends_at_the_gate_or_runs_nothing"),
    "surface_unreadable": ("PlanRegenerationTest", "test_an_unreadable_record_and_an_unmanaged_generator_end_at_the_gate"),
    "generator_not_managed": ("PlanRegenerationTest", "test_an_unreadable_record_and_an_unmanaged_generator_end_at_the_gate"),
    "git_not_isolated": ("PlanRegenerationTest", "test_a_failed_or_slow_generator_and_a_git_that_is_not_isolated_end_at_the_gate"),
    "generator_failed": ("PlanRegenerationTest", "test_a_failed_or_slow_generator_and_a_git_that_is_not_isolated_end_at_the_gate"),
    "generator_timed_out": ("PlanRegenerationTest", "test_a_failed_or_slow_generator_and_a_git_that_is_not_isolated_end_at_the_gate"),
    "copy_source_unsafe": ("PlanRegenerationTest", "test_a_context_list_naming_a_file_outside_the_repository_is_refused"),
    "surface_changed_during_run": ("PlanRegenerationTest", "test_a_context_list_rewritten_during_the_run_is_not_trusted"),
}

Plant = Callable[[Path, Path, Path], None]  # (directory to write into, marker file, the managed tree)


# ---------------------------------------------------------------------------
# The plants. Each writes into ``base`` files that would run ``marker`` if the interpreter loaded them.
# ---------------------------------------------------------------------------


def _source(marker: Path) -> str:
    return f"open({str(marker)!r}, 'a').write('ran\\n')\n"


def _write(base_dir: Path, rel: str, text: str) -> None:
    path = base_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def sitecustomize_in_scripts(base_dir: Path, marker: Path, managed: Path) -> None:
    _write(base_dir, "scripts/sitecustomize.py", _source(marker))


def secrets_in_scripts(base_dir: Path, marker: Path, managed: Path) -> None:
    _write(base_dir, "scripts/secrets.py", _source(marker))


def sourceless_pyc(base_dir: Path, marker: Path, managed: Path) -> None:
    scratch = base_dir.parent / f"{base_dir.name}-scratch.py"
    scratch.write_text(_source(marker))
    py_compile.compile(str(scratch), cfile=str(base_dir / "scripts" / "workflow_x.pyc"), doraise=True)


def package_directory(base_dir: Path, marker: Path, managed: Path) -> None:
    _write(base_dir, "scripts/json/__init__.py", _source(marker))


def extension_named_file(base_dir: Path, marker: Path, managed: Path) -> None:
    _write(base_dir, f"scripts/{SO_NAME}", _source(marker))


def forged_pyc(base_dir: Path, marker: Path, managed: Path) -> None:
    """A bytecode file for a managed module that Python would load in place of its source (an unchecked-hash header
    matches whatever the source is)."""
    scratch = base_dir.parent / f"{base_dir.name}-forged.py"
    scratch.write_text(_source(marker))
    cache = base_dir / "scripts" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(scratch), cfile=str(cache / f"workflow_state.{CACHE_TAG}.pyc"), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)


def link_for_the_generator(base_dir: Path, marker: Path, managed: Path) -> None:
    script = base_dir / "scripts" / "prepare-ai-review.sh"
    if not script.exists():  # a staged landing: the bytes are the managed tree's
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_bytes((managed / "scripts" / "prepare-ai-review.sh").read_bytes())
    twin = base_dir.parent / f"{base_dir.name}-twin.sh"
    twin.write_bytes(script.read_bytes() + b"\n" + f"echo ran >> {marker}\n".encode())
    script.unlink()
    script.symlink_to(twin)


def root_sitecustomize(base_dir: Path, marker: Path, managed: Path) -> None:
    _write(base_dir, "sitecustomize.py", _source(marker))


def root_secrets(base_dir: Path, marker: Path, managed: Path) -> None:
    _write(base_dir, "secrets.py", _source(marker))


def root_json(base_dir: Path, marker: Path, managed: Path) -> None:
    _write(base_dir, "json.py", _source(marker))


#: name -> (plant, cause when the plant sits in the managed tree, cause when it is landed). ``None`` is a completed
#: regeneration with the marker unwritten: the interpreter never loads the plant. ``reconcile_invalid`` is the plan
#: role's root-level plant: the Workflow's reconcile refuses the unclassified path before the guard is reached.
#: ``unlandable`` is the implementation role's: the Workflow's own generation step, which a landing needs for a valid
#: record, refuses an unclassified root-level path in the worker's clone, so there is no landing to test.
PLANTS = {
    "scripts_sitecustomize": (sitecustomize_in_scripts, "surface_foreign_entry", "surface_landing_touched"),
    "scripts_secrets": (secrets_in_scripts, "surface_foreign_entry", "surface_landing_touched"),
    "sourceless_pyc": (sourceless_pyc, "surface_foreign_entry", "surface_landing_touched"),
    "package_directory": (package_directory, "surface_foreign_entry", "surface_landing_touched"),
    "extension_named_file": (extension_named_file, "surface_foreign_entry", "surface_landing_touched"),
    "link_for_the_generator": (link_for_the_generator, "surface_script_modified", "surface_landing_touched"),
    "forged_pyc": (forged_pyc, None, "surface_landing_touched"),
    "root_sitecustomize": (root_sitecustomize, "reconcile_invalid", "unlandable"),
    "root_secrets": (root_secrets, "reconcile_invalid", "unlandable"),
    "root_json": (root_json, "reconcile_invalid", "unlandable"),
}


#: Run in the worker's clone just before the worker's own fix commit: copies the staged files into the clone and adds
#: them (ignored ones included), so the planted files are part of the landed range, while the Workflow code the
#: worker's own steps run never imports them (a plant under ``scripts/`` would shadow a module of the test's helper).
LAND_STAGED = r'''
import os, shutil
stage = Path({stage!r})
for dirpath, dirnames, filenames in os.walk(stage):
    for name in filenames + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
        src = Path(dirpath) / name
        dst = Path.cwd() / src.relative_to(stage)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        if src.is_symlink():
            dst.symlink_to(os.readlink(src))
        else:
            shutil.copyfile(src, dst)
        git("add", "-f", "--", str(dst.relative_to(Path.cwd())))
'''


def impl_work_landing(stage: Path, flows_link: Path | None) -> str:
    """The implementation role's worker steps with ``stage`` landed in the same commit as the fix, and, when
    ``flows_link`` is given, the fixed file replaced by a link to it."""
    code = base.IMPL_WORK.replace('git("commit", "-m", "fix the finding")',
                                  LAND_STAGED.format(stage=str(stage)) + 'git("commit", "-m", "fix the finding")')
    if flows_link is not None:
        code = code.replace('flows.write_text("the fixed flow\\n")',
                            f'flows.symlink_to({str(flows_link)!r})')
        assert str(flows_link) in code
    return code


class Regeneration(Safety):
    """Helpers shared by both roles. ``GUARD_CAUSE`` is ``None``: the guard and the generator are the real ones."""

    GUARD_CAUSE = None
    ARGUMENT: str

    @property
    def marker(self) -> Path:
        return self.tmp / "marker"

    def worked_with(self, *plants: Plant, flows_link: Path | None = None) -> None:
        """The worker's work plus ``plants``: written into the managed tree for the plan role (no commit, so the tree
        check is its whole guard) and landed in the worker's commit for the implementation role."""
        self.worked()
        if self.ARGUMENT == "plan":
            previous = self.after

            def after(clone: Path) -> None:
                for plant in plants:
                    plant(self.root, self.marker, self.root)
                previous(clone)

            self.after = after
            return
        stage = self.tmp / "stage"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        for plant in plants:
            plant(stage, self.marker, self.root)
        code = impl_work_landing(stage, flows_link)
        self.after = lambda clone: base.fixtures.run_workflow_python(clone, code)

    def again(self) -> None:
        """A fresh fixture for the next sub-case of a test."""
        self.setUp()
        for name in ("jobs", "worker"):
            shutil.rmtree(self.runtime / name, ignore_errors=True)
        self.marker.unlink(missing_ok=True)

    def spy(self):
        return mock.patch.object(workflow_contract, "run_in_place", wraps=workflow_contract.run_in_place)

    def assert_next_stage_passes(self) -> None:
        kind = "plan" if self.ARGUMENT == "plan" else "implementation"
        base.fixtures.run_workflow_python(self.root, f"""
entry = ws.json.loads((Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"]["wi-1"]
manifest = Path.cwd() / fingerprint.resolve_bundle_dir(Path.cwd(), "wi-1", stage={kind!r}) / "MANIFEST.md"
fingerprint.assert_local_generation_matches(Path.cwd(), manifest)
if {kind!r} == "plan":
    ws.validate_local_plan_review_preconditions_bound(Path.cwd(), entry)
else:
    ws.validate_local_implementation_review_preconditions(entry)
    ws.verify_implementation_review_bundle(Path.cwd(), "wi-1")
""")

    def assert_refused(self, record: dict, cause: str, spy) -> None:
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        found = self.evidence(record)
        self.assertEqual(found["code"], job.CARRY_BUNDLE_GATE_CODE)
        self.assertEqual(found["regeneration"]["cause"], cause, found["regeneration"])
        self.assertEqual(record["regeneration"]["stage"], "refused")
        self.assertIn(f"did not regenerate the bundle ({cause}", record["human_gate_pending"]["what_is_required"])
        self.assertTrue(job.validate_record(record, managed_repo=self.target, identity=base.FAKE_IDENTITY).valid)
        self.assert_carry_ended(record)

    def assert_regenerated(self, record: dict, spy) -> None:
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(record["regeneration"]["stage"], "finished")
        self.assertEqual(record["regeneration"]["argument"], self.ARGUMENT)
        self.assertEqual(spy.call_count, 1)
        self.assertFalse(record.get("human_gate_pending"))
        self.assertTrue(job.validate_record(record, managed_repo=self.target, identity=base.FAKE_IDENTITY).valid)
        self.assert_next_stage_passes()

    def run_plant(self, name: str, *, landed: bool) -> None:
        plant, tree_cause, landed_cause = PLANTS[name]
        cause = landed_cause if landed else tree_cause
        if cause == "unlandable":
            return  # nothing to land: the Workflow's generation step refuses an unclassified root-level path
        self.worked_with(plant)
        with self.spy() as spy:
            record = self.step()
        self.assertFalse(self.marker.exists(), f"{name}: the Controller ran something of the managed tree")
        shutil.rmtree(self.root / "scripts" / "__pycache__", ignore_errors=True)  # the test's own interpreter
        if cause == "reconcile_invalid":
            # the Workflow's own reconcile refuses an unclassified path before any guard (the plan role only)
            self.assertEqual(spy.call_count, 0)
            self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
            self.assertEqual(self.evidence(record)["reason"], job.RECONCILE_INVALID_REASON)
        elif cause is None:
            self.assert_regenerated(record, spy)
        else:
            self.assertEqual(spy.call_count, 0, f"{name}: the generator was started")
            self.assert_refused(record, cause, spy)
        self.no_armour(record)

    # -- the contexts -------------------------------------------------------------------------------------------

    def canary_file(self) -> Path:
        path = self.tmp / "outside" / "canary.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(CANARY + "\n")
        return path

    def context_list(self) -> Path:
        """The list the generator copies from: the plan inputs' for the plan role, the live bundle's for the other."""
        folder = "plan-inputs" if self.ARGUMENT == "plan" else "current"
        return self.root / ITEM / folder / "CONTEXT_FILES.txt"

    def link_to(self, target: Path) -> str:
        """A repository-relative link to ``target`` in the (ignored) item directory; returns the path as a list line."""
        link = self.root / ITEM / "canary-link"  # not under a carried directory, which would refuse a link itself
        link.symlink_to(target)
        return str(link.relative_to(self.root))

    def write_context(self, *lines: str) -> None:
        path = self.context_list()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")

    def stage_context(self, make_lines: Callable[[], list[str]]) -> None:
        """The list the generator will read, written before the step and after the job's own record: a change while the
        worker runs is the carry's refusal. The implementation role's list is a file of the verified bundle, so a
        change to it is caught before the worker is launched (see :meth:`canary_ends_at_the_gate`)."""
        if not self.recorded:
            self.record()
        self.write_context(*make_lines())

    def assert_canary_nowhere(self) -> None:
        """No file of the bundle, and nothing in its archive, holds the canary's bytes."""
        for path in sorted((self.root / ITEM).rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            self.assertNotIn(CANARY.encode(), data, str(path))
            if path.name.endswith((".tar.gz", ".tgz")):
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    for member in archive:
                        extracted = archive.extractfile(member) if member.isfile() else None
                        if extracted:
                            self.assertNotIn(CANARY.encode(), extracted.read(), f"{path}:{member.name}")

    def canary_ends_at_the_gate(self, reason: str, line_for: Callable[[Path], str], *, via: str = "list") -> None:
        canary = self.canary_file()
        self.worked()
        self.stage_context(lambda: [line_for(canary)])
        with self.spy() as spy:
            record = self.step()
        self.assertFalse(self.marker.exists())
        self.assertEqual(spy.call_count, 0, "the generator was started")
        if self.ARGUMENT == "plan":
            self.assert_refused(record, "copy_source_unsafe", spy)
            regeneration = self.evidence(record)["regeneration"]
            self.assertIn(f"({reason})", regeneration["detail"])
        else:
            # the implementation role's list is a file of the verified bundle: the change is refused before the launch
            self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
            self.assertIn("bundle_unverified", record["human_gate_pending"]["what_is_required"])
            self.assertEqual(self.launched, [])
        self.assert_canary_nowhere()
        self.no_armour(record)

    # -- both roles -----------------------------------------------------------------------------------------------

    def test_a_clean_landing_is_regenerated_and_hands_the_next_stage_its_preconditions(self) -> None:
        self.worked()
        before_refs = _git(self.root, "for-each-ref")
        with self.spy() as spy:
            record = self.step()
        self.assert_regenerated(record, spy)
        self.assertEqual(_git(self.root, "diff", "--cached", "--name-only"), "")
        self.assertEqual(_git(self.root, "ls-files", "--others", "--exclude-standard"), "")
        self.assertEqual(_git(self.root, "for-each-ref"), before_refs) if self.ARGUMENT == "plan" else None
        self.no_armour(record)
        self.assertFalse(self.marker.exists())

    def test_a_poisoned_environment_runs_nothing(self) -> None:
        poison = self.tmp / "poison"
        poison.mkdir()
        shell_marker = self.marker
        (poison / "sitecustomize.py").write_text(_source(shell_marker))
        (poison / "startup.py").write_text(_source(shell_marker))
        (poison / "env.sh").write_text(f"echo ran >> {shell_marker}\n")
        hostile = poison / "bin"
        hostile.mkdir()
        (hostile / "python3").write_text(f"#!/bin/sh\necho ran >> {shell_marker}\nexit 1\n")
        (hostile / "python3").chmod(0o755)
        environment = {"PYTHONPATH": str(poison), "PYTHONSTARTUP": str(poison / "startup.py"),
                       "BASH_ENV": str(poison / "env.sh"), "ENV": str(poison / "env.sh"),
                       "PATH": f"{hostile}{os.pathsep}{os.environ['PATH']}"}
        real = workflow_contract.run_in_place

        def poisoned(*args, **kw):
            with mock.patch.dict(os.environ, environment):
                return real(*args, **kw)

        self.worked()
        with mock.patch.object(workflow_contract, "run_in_place", side_effect=poisoned) as spy:
            record = self.step()
        self.assertFalse(self.marker.exists(), "the generator loaded something of the poisoned environment")
        self.assert_regenerated(record, spy)

    def test_an_absolute_traversal_and_link_line_each_end_at_the_gate_before_the_generator(self) -> None:
        for name, reason, line in (
            ("absolute", "absolute", lambda canary: str(canary)),
            ("traversal", "traversal", lambda canary: os.path.relpath(canary, self.root)),
            ("link", "link", lambda canary: self.link_to(canary)),
        ):
            with self.subTest(case=name):
                self.again()
                self.canary_ends_at_the_gate(reason, line)

    def test_a_context_list_rewritten_during_the_run_is_not_trusted(self) -> None:
        canary = self.canary_file()
        self.worked()
        real = workflow_contract.run_in_place

        def rewrite_then_run(*args, **kw):
            self.write_context(str(canary))
            return real(*args, **kw)

        with mock.patch.object(workflow_contract, "run_in_place", side_effect=rewrite_then_run) as spy:
            record = self.step()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        regeneration = self.evidence(record)["regeneration"]
        self.assertEqual(regeneration["cause"], "surface_changed_during_run")
        self.assertIn("copy_source_unsafe", regeneration["detail"])
        self.assertFalse(self.marker.exists())
        self.no_armour(record)

    def test_an_unreadable_record_and_an_unmanaged_generator_end_at_the_gate(self) -> None:
        real = generator_run._read_regular

        def unreadable(root, rel):
            if rel == generator_run.protocol.INSTALLATION_RECORD:
                raise OSError("the record is a link")
            return real(root, rel)

        def absent(root, rel):
            if rel == generator_run.protocol.INSTALLATION_RECORD:
                raise FileNotFoundError(rel)
            return real(root, rel)

        for cause, replacement in (("surface_unreadable", unreadable), ("generator_not_managed", absent)):
            with self.subTest(cause=cause):
                self.again()
                self.worked()
                with self.spy() as spy, mock.patch.object(generator_run, "_read_regular", replacement):
                    record = self.step()
                self.assertEqual(spy.call_count, 0)
                self.assert_refused(record, cause, spy)

    def test_a_failed_or_slow_generator_and_a_git_that_is_not_isolated_end_at_the_gate(self) -> None:
        real = workflow_contract.run_in_place

        def failing(root, argv, **kw):
            raise WorkflowQueryError("the generator exited 3", evidence={
                "reason": "query_failed", "stdout_tail": "", "stderr_tail": "its own refusal"})

        def not_isolated(root, argv, **kw):
            raise WorkflowQueryError("git is not isolated", evidence={"reason": "query_git_not_isolated"})

        for cause, replacement in (("generator_failed", failing), ("git_not_isolated", not_isolated)):
            with self.subTest(cause=cause):
                self.again()
                self.worked()
                with mock.patch.object(workflow_contract, "run_in_place", side_effect=replacement):
                    record = self.step()
                self.assert_refused(record, cause, None)
        with self.subTest(cause="generator_timed_out"):
            self.again()
            self.worked()
            with mock.patch.object(workflow_contract, "run_in_place", side_effect=lambda root, argv, **kw: real(
                    root, ["sleep", "30"], **{**kw, "timeout": 0.3})):
                record = self.step()
            self.assert_refused(record, "generator_timed_out", None)


class PlanRegenerationTest(Regeneration, base.PlanCase):
    """The plan role makes no commit: a plant sits in the managed tree, and the tree check is the whole guard."""

    ARGUMENT = "plan"

    def test_a_plant_in_the_tree_ends_at_the_gate_or_runs_nothing(self) -> None:
        for name in PLANTS:
            with self.subTest(plant=name):
                self.again()
                self.run_plant(name, landed=False)

    def test_a_clean_context_list_of_repository_files_is_regenerated(self) -> None:
        self.worked()
        self.stage_context(lambda: ["docs/UX_FLOWS.md", "docs/missing-file.md"])
        with self.spy() as spy:
            record = self.step()
        self.assert_regenerated(record, spy)

    def test_a_context_list_naming_a_file_outside_the_repository_is_refused(self) -> None:
        self.test_an_absolute_traversal_and_link_line_each_end_at_the_gate_before_the_generator()


class ImplRegenerationTest(Regeneration, base.ImplCase):
    """The implementation role commits: a plant is landed, and the landing's diff and the tree are both checked."""

    ARGUMENT = "post-fix"

    def test_a_landed_path_on_the_surface_ends_at_the_gate(self) -> None:
        for name, (_plant, _tree, landed) in PLANTS.items():
            with self.subTest(plant=name):
                self.again()
                self.run_plant(name, landed=True)

    @unittest.expectedFailure
    def test_an_edited_managed_script_with_its_digest_edited_to_match_is_refused_by_the_landing_alone(self) -> None:
        """KNOWN DEFECT (found by this test, not fixed by this item): the Controller's own protocol queries run the
        managed ``scripts/`` from a private copy of bytes verified against the installation record, and a worker's
        landing can edit both. The first query (``describe``) of the next resolution therefore runs the edited script
        before the guard's landing check is reached, so the marker is written. Closing it needs the landing check
        before every protocol query of the job, which is more than CP3's tests."""
        marker = self.marker

        def edit(base_dir: Path, _marker: Path, managed: Path) -> None:
            script = base_dir / "scripts" / "workflow_state.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text((managed / "scripts" / "workflow_state.py").read_text() + "\n" + _source(marker))
            record = base_dir / ".workflow-manager" / "installation.json"
            record.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads((managed / ".workflow-manager" / "installation.json").read_text())
            data["managed"]["scripts/workflow_state.py"]["sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
            record.write_text(json.dumps(data, indent=2) + "\n")

        self.worked_with(edit)
        with self.spy() as spy:
            record = self.step()
        self.assertFalse(marker.exists())
        self.assertEqual(spy.call_count, 0)
        self.assert_refused(record, "surface_landing_touched", spy)
        self.assertIn("workflow_state.py", self.evidence(record)["regeneration"]["detail"])
        generator_run.check_tree(self.root)  # the tree check alone passes: only the landing's diff refuses it

    def test_a_changed_file_link_landed_by_the_worker_is_refused_and_never_followed(self) -> None:
        canary = self.canary_file()

        self.worked_with(flows_link=canary)
        with self.spy() as spy:
            record = self.step()
        self.assertEqual(spy.call_count, 0)
        self.assert_refused(record, "copy_source_unsafe", spy)
        self.assertIn("(link)", self.evidence(record)["regeneration"]["detail"])
        self.assert_canary_nowhere()

    def test_a_local_link_exposed_by_a_landing_that_removes_the_ignore_rule_never_reaches_the_generator(self) -> None:
        """The landing refuses a repository that holds any uncommitted path, an ignored link included, so a link that
        a landing would expose as untracked never gets as far as D5a's candidate list (which has its own unit test)."""
        canary = self.canary_file()

        self.worked()
        previous = self.after

        def after(clone: Path) -> None:
            previous(clone)
            # a local link that appears in the repository while the worker runs, ignored by the fixture's rule
            (self.root / "exposed.pyc").symlink_to(canary)

        self.after = after
        with self.spy() as spy:
            record = self.step()
        self.assertEqual(spy.call_count, 0)
        self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
        found = self.evidence(record)
        self.assertEqual((found["code"], found["cause"]), (job.CODEX_LANDING_REFUSED_CODE, "managed_tree_not_clean"))
        self.assertEqual(found["landing"]["dirty"], ["exposed.pyc"])
        self.assert_canary_nowhere()
        self.assertFalse(self.marker.exists())


class CauseCoverageTest(unittest.TestCase):
    def test_every_cause_has_a_case_and_every_case_is_a_cause(self) -> None:
        self.assertEqual(set(GENERATOR_CAUSE_CASES), set(generator_run.CAUSES))
        for cause, (klass, method) in GENERATOR_CAUSE_CASES.items():
            self.assertTrue(callable(getattr(globals().get(klass), method, None)), f"{cause}: {klass}.{method}")

    def test_every_plant_names_a_cause_of_the_run(self) -> None:
        for name, (_plant, tree, landed) in PLANTS.items():
            for cause in (tree, landed):
                self.assertTrue(cause in (None, "reconcile_invalid", "unlandable") or cause in generator_run.CAUSES, f"{name}: {cause}")

    def test_a_cause_added_without_a_case_fails(self) -> None:
        with mock.patch.object(generator_run, "CAUSES", (*generator_run.CAUSES, "surface_new_cause")):
            with self.assertRaises(AssertionError):
                self.assertEqual(set(GENERATOR_CAUSE_CASES), set(generator_run.CAUSES))


if __name__ == "__main__":
    unittest.main()
