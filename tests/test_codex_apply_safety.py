"""`workflow-controller-h4e3` CP4: the routed apply path, proved adversarially through the job (D10).

Every case drives ``job.execute_step`` with the real carry, the real landing and the real recorder on the fixture
repositories of ``tests/test_job_codex_apply.py`` and the scripted fake ``codex``; the worker's own work (and its
misbehaviour) is the ``after`` hook, run in the clone once the fake run has ended. Each case asserts what the managed
repository keeps (its files, refs, index and state) except as the ending allows, the status D8 gives, and that no
credential or armour line sits in the record. ``CAUSE_CASES`` maps every member of ``codex_carry.CAUSES`` to a test, and
a test fails when a cause has none.
"""

from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_carry, codex_review_role, job, review_recorder  # noqa: E402
from controller import runtime as runtime_module  # noqa: E402
from controller.errors import WorkflowQueryError  # noqa: E402
from tests import test_job_codex_apply as base  # noqa: E402

ITEM = base.ITEM
STATE = base.STATE
WID = base.WID
_entry = base._entry
_git = base._git

#: Every cause of the carry, with the test that proves it through the job (a test fails when one has no case).
CAUSE_CASES = {
    "carry_foreign_dirt": ("PlanRefusalsTest", "test_foreign_dirt_of_each_kind_refuses_before_the_clone"),
    "carry_commit_pending_failed": ("ImplRefusalsTest", "test_a_failed_pending_commit_refuses_before_the_clone"),
    "carry_member_refused": ("PlanRefusalsTest", "test_a_link_or_special_file_at_a_member_is_refused_at_carry_out"),
    "carry_unexpected_commit": ("PlanRefusalsTest", "test_a_commit_or_a_ref_made_by_the_plan_role_is_refused"),
    "clone_git_changed": ("PlanRefusalsTest", "test_a_hook_written_into_the_clone_is_refused"),
    "carry_index_changed": ("PlanRefusalsTest", "test_an_added_or_dropped_intent_to_add_entry_is_refused"),
    "carry_out_of_set": ("PlanRefusalsTest", "test_a_write_outside_the_set_is_refused"),
    "carry_managed_changed": ("PlanBoundaryTest", "test_a_managed_member_changed_by_someone_else_is_refused_and_nothing_is_written"),
    "carry_state_foreign": ("PlanRefusalsTest", "test_a_state_change_outside_this_items_entry_is_refused"),
    "carry_clone_gone": ("PlanRefusalsTest", "test_a_deleted_member_and_a_gone_clone_are_refused"),
}

ARMOUR_LINE = "-----BEGIN OPENSSH PRIVATE KEY-----"


class Safety:
    """Helpers shared by the plan and the implementation role's cases."""

    def item_files(self) -> dict[str, bytes]:
        """Every file under the work item's ``.ai-review`` directory and the carried plan files, by path."""
        found: dict[str, bytes] = {}
        entry = _entry(self.root)
        for rel in (entry["plan_path"], entry["registry_path"]):
            found[rel] = (self.root / rel).read_bytes()
        for path in sorted((self.root / ITEM).rglob("*")):
            if path.is_file() and not path.is_symlink():
                found[str(path.relative_to(self.root))] = path.read_bytes()
        return found

    def no_armour(self, record: dict) -> None:
        text = json.dumps(record)
        self.assertNotIn("PRIVATE KEY", text)
        self.assertIsNone(codex_review_role.ARMOUR.search(text.encode()))

    def refused_after_the_worker(self, work, cause: str, *, detail: str | None = None) -> dict:
        """The worker misbehaves in the clone; the carry out refuses with ``cause``: nothing is written to the managed
        repository, the clone is kept and named, and the status is D8's ``FAILED`` carry refusal."""
        self.script(message="applied")
        self.after = work
        before, files = self.managed_snapshot(), self.item_files()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
        found = self.evidence(record)
        self.assertEqual((found["code"], found["cause"]), (job.CODEX_CARRY_REFUSED_CODE, cause), found)
        if detail is not None:
            self.assertEqual(found["evidence"]["detail"], detail)
        self.assertEqual(self.managed_snapshot(), before)
        self.assertEqual(self.item_files(), files)
        self.assertTrue(self.clone().exists())
        self.assertEqual(found["clone"], str(self.clone()))
        self.assertNotEqual(record["codex_carry"]["stage"], "done")
        self.no_armour(record)
        self.assertTrue(job.validate_record(record, managed_repo=self.target, identity=base.FAKE_IDENTITY).valid)
        return record


class PlanRefusalsTest(Safety, base.PlanCase):
    """D10's plan-role cases: what the worker may not do in the clone, one refusal each."""

    def with_work(self, extra):
        def work(clone: Path) -> None:
            self.plan_work(clone)
            extra(clone)

        return work

    def stray(self, clone: Path) -> Path:
        member = clone / ITEM / "plan-inputs" / "CONTEXT_FILES.txt"
        member.unlink(missing_ok=True)
        return member

    # -- a link or a special file at a member's path (carry_member_refused, detail kind) ---------------------

    def test_a_link_or_special_file_at_a_member_is_refused_at_carry_out(self) -> None:
        outside = self.tmp / "outside.txt"
        outside.write_text("outside the clone\n")

        def symlink(clone: Path) -> None:
            member = self.stray(clone)
            os.symlink(outside, member)

        def fifo(clone: Path) -> None:
            os.mkfifo(self.stray(clone))

        def dangling(clone: Path) -> None:
            os.symlink("nowhere", self.stray(clone))

        for name, change in (("symlink", symlink), ("fifo", fifo), ("dangling", dangling)):
            with self.subTest(kind=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.launched.clear()
                self.refused_after_the_worker(self.with_work(change), "carry_member_refused", detail="kind")
                self.assertEqual(outside.read_text(), "outside the clone\n")

    def test_a_hardlink_at_a_member_is_copied_never_written_through(self) -> None:
        """A member that is a hardlink to a file of the managed repository is read as bytes; the carry out replaces the
        managed member by a new file, so the linked file is never written through."""
        target = self.root / "README.md"
        target.write_text("the managed readme\n")
        base._git(self.root, "add", "README.md")
        base._git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "readme")
        before_head = _git(self.root, "rev-parse", "HEAD")

        def hardlink(clone: Path) -> None:
            member = self.stray(clone)
            os.link(target, member)

        self.script(message="applied")
        self.after = self.with_work(hardlink)
        record = self.step()
        self.assertEqual(target.read_text(), "the managed readme\n")
        self.assertEqual(_git(self.root, "rev-parse", "HEAD"), before_head)
        member = self.root / ITEM / "plan-inputs" / "CONTEXT_FILES.txt"
        if member.exists():
            self.assertNotEqual(member.stat().st_ino, target.stat().st_ino)
        self.assertIn(record["status"], (job.STATUS_GATE_BLOCKED, job.STATUS_FAILED))
        self.no_armour(record)

    def test_a_private_key_armour_line_in_a_member_is_refused_and_never_recorded(self) -> None:
        def armour(clone: Path) -> None:
            (clone / ITEM / "plan-inputs" / "TEST_RESULTS.md").write_text(f"results\n{ARMOUR_LINE}\nsecret\n")

        record = self.refused_after_the_worker(self.with_work(armour), "carry_member_refused", detail="armour")
        self.assertNotIn("secret", json.dumps(record))

    # -- writes outside the set ----------------------------------------------------------------------------

    def test_a_write_outside_the_set_is_refused(self) -> None:
        def stray(clone: Path) -> None:
            (clone / "stray.txt").write_text("outside\n")

        def tracked(clone: Path) -> None:
            with (clone / ".gitignore").open("a") as handle:
                handle.write("# a tracked change outside the set\n")

        def feedback(clone: Path) -> None:
            path = clone / ITEM / "feedback" / "REVIEW_FEEDBACK.md"
            path.write_text(path.read_text() + "\n- B2: the worker added a finding.\n")

        def new_in_item(clone: Path) -> None:
            (clone / ITEM / "plan-inputs" / "EXTRA.md").write_text("not a member\n")

        def credential_named(clone: Path) -> None:
            (clone / "auth.json").write_text("{}")

        for name, change in (("stray", stray), ("tracked", tracked), ("feedback", feedback), ("new_in_item", new_in_item),
                             ("credential_named", credential_named)):
            with self.subTest(change=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.refused_after_the_worker(self.with_work(change), "carry_out_of_set")

    # -- state ----------------------------------------------------------------------------------------------

    def test_a_state_change_outside_this_items_entry_is_refused(self) -> None:
        def edit(change):
            def work(clone: Path) -> None:
                path = clone / STATE
                document = json.loads(path.read_text())
                change(document)
                path.write_text(json.dumps(document, indent=2) + "\n")

            return work

        def other_entry(document: dict) -> None:
            document["work_items"]["other"] = json.loads(json.dumps(document["work_items"][WID]))

        def active(document: dict) -> None:
            document["active_work_item_id"] = "somebody-else"

        def top_level(document: dict) -> None:
            document["a_new_top_level_key"] = {"x": 1}

        def schema(document: dict) -> None:
            document["schema_version"] = 99

        for name, change in (("other_entry", other_entry), ("active", active), ("top_level", top_level),
                             ("schema", schema)):
            with self.subTest(change=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.refused_after_the_worker(self.with_work(edit(change)), "carry_state_foreign")

    def test_a_state_file_that_is_not_a_state_document_is_refused(self) -> None:
        def broken(clone: Path) -> None:
            self.plan_work(clone)
            (clone / STATE).write_text("not json")

        self.refused_after_the_worker(broken, "carry_state_foreign")

    # -- the index and the clone's own git ------------------------------------------------------------------

    def test_an_added_or_dropped_intent_to_add_entry_is_refused(self) -> None:
        def added(clone: Path) -> None:
            (clone / "extra-intent.txt").write_text("x\n")
            subprocess.run(["git", "add", "-N", "extra-intent.txt"], cwd=clone, check=True, capture_output=True)

        def dropped(clone: Path) -> None:
            subprocess.run(["git", "reset", "-q", "--", "docs/ai-workflow/registry/wi-1-artifacts.json"], cwd=clone,
                           check=True, capture_output=True)

        for name, change in (("added", added), ("dropped", dropped)):
            with self.subTest(change=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.refused_after_the_worker(self.with_work(change), "carry_index_changed")

    def test_a_commit_or_a_ref_made_by_the_plan_role_is_refused(self) -> None:
        git = ["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid"]

        def commit(clone: Path) -> None:
            subprocess.run([*git, "commit", "--allow-empty", "-m", "not allowed"], cwd=clone, check=True,
                           capture_output=True)

        def branch(clone: Path) -> None:
            subprocess.run([*git, "branch", "made-by-the-worker"], cwd=clone, check=True, capture_output=True)

        def tag(clone: Path) -> None:
            subprocess.run([*git, "tag", "made-by-the-worker"], cwd=clone, check=True, capture_output=True)

        for name, change in (("commit", commit), ("branch", branch), ("tag", tag)):
            with self.subTest(change=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.refused_after_the_worker(self.with_work(change), "carry_unexpected_commit")

    def test_a_hook_written_into_the_clone_is_refused(self) -> None:
        def hook(name: str):
            def work(clone: Path) -> None:
                hooks = clone / ".git" / "hooks"
                hooks.mkdir(exist_ok=True)
                (hooks / name).write_text("#!/bin/sh\necho owned\n")

            return work

        def config(clone: Path) -> None:
            with open(clone / ".git" / "config", "a") as handle:
                handle.write('[core]\n\tfsmonitor = "echo owned"\n')

        for name, change in (("post-merge", hook("post-merge")), ("pre-commit", hook("pre-commit")), ("config", config)):
            with self.subTest(change=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.refused_after_the_worker(self.with_work(change), "clone_git_changed")
        self.assertFalse((self.root / ".git" / "hooks" / "post-merge").exists())

    # -- a carried-out member deleted, the clone gone -------------------------------------------------------

    def test_a_deleted_member_and_a_gone_clone_are_refused(self) -> None:
        def deleted(clone: Path) -> None:
            (clone / ITEM / "plan-inputs" / "TEST_RESULTS.md").unlink()

        self.refused_after_the_worker(self.with_work(deleted), "carry_member_refused", detail="absent")
        self.setUp()
        shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
        shutil.rmtree(self.runtime / "worker", ignore_errors=True)
        self.script(message="applied")
        self.after = self.with_work(lambda clone: shutil.rmtree(clone))
        before = self.managed_snapshot()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["cause"], "carry_clone_gone")
        self.assertEqual(self.managed_snapshot(), before)
        self.no_armour(record)

    # -- the carry in, before any clone ---------------------------------------------------------------------

    def test_foreign_dirt_of_each_kind_refuses_before_the_clone(self) -> None:
        def tracked() -> None:
            with (self.root / ".gitignore").open("a") as handle:
                handle.write("# changed by someone else\n")

        def untracked() -> None:
            (self.root / "untracked.md").write_text("x\n")

        def intent() -> None:
            (self.root / "intent.md").write_text("x\n")
            _git(self.root, "add", "-N", "intent.md")

        def staged() -> None:
            (self.root / "staged.md").write_text("x\n")
            _git(self.root, "add", "staged.md")

        for name, change in (("tracked", tracked), ("untracked", untracked), ("intent", intent), ("staged", staged)):
            with self.subTest(kind=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.launched.clear()
                self.script(message="applied")
                change()
                before = self.managed_snapshot()
                record = self.step()
                # a tracked change reaches the carry (carry_foreign_dirt); a path the artifact declaration does not
                # classify is the Workflow's own condition, refused one layer earlier: either way nothing started
                found = json.dumps(record.get("reconciliation_evidence"))
                if name == "tracked":
                    self.assertEqual(record["status"], job.STATUS_FAILED)
                    self.assertEqual(record["reconciliation_evidence"]["code"], job.WORKER_NOT_STARTED_CODE)
                    self.assertIn("carry_foreign_dirt", found)
                else:
                    self.assertIn(record["status"], (job.STATUS_FAILED, job.STATUS_GATE_BLOCKED))
                    self.assertTrue("carry_foreign_dirt" in found or "condition_refused" in json.dumps(record), found)
                self.assertEqual(self.launched, [])
                self.assertFalse(self.clone().exists())
                self.assertEqual(self.managed_snapshot(), before)

    def test_a_member_that_cannot_be_carried_in_refuses_before_the_clone(self) -> None:
        feedback = self.root / ITEM / "feedback"
        outside = self.tmp / "outside.md"
        outside.write_text("x\n")

        def link() -> None:
            os.symlink(outside, feedback / "link.md")

        def special() -> None:
            os.mkfifo(feedback / "pipe.md")

        def armour() -> None:
            (feedback / "key.md").write_text(f"{ARMOUR_LINE}\n")

        def credential() -> None:
            (feedback / "auth.json").write_text("{}")

        for name, change in (("link", link), ("special", special), ("armour", armour), ("credential", credential)):
            with self.subTest(kind=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.launched.clear()
                self.script(message="applied")
                change()
                before = self.managed_snapshot()
                record = self.step()
                self.assertEqual(record["status"], job.STATUS_FAILED)
                self.assertEqual(self.evidence(record)["code"], job.WORKER_NOT_STARTED_CODE)
                self.assertIn("carry_member_refused", json.dumps(self.evidence(record)))
                self.assertEqual(self.launched, [])
                self.assertFalse(self.clone().exists())
                self.assertEqual(self.managed_snapshot(), before)
                self.no_armour(record)


class PlanBoundaryTest(Safety, base.PlanCase):
    """What the carry keeps, discards and ends at, and the boundary this item draws."""

    def test_derived_leftovers_in_the_clone_are_discarded(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            item = clone / ITEM
            (item / "current.staging-1").mkdir()
            (item / "current.staging-1" / "x.txt").write_text("x\n")
            (item / "current.old-1").mkdir()
            (item / "review-bundle.tar.gz.tmp").write_bytes(b"partial")
            (item / "AMENDMENT_DIFF.patch").write_text("a diff\n")
            (item / "CHANGES_SINCE_REVIEW.md").write_text("derived\n")

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        self.assert_carry_ended(record)
        names = {path.name for path in (self.root / ITEM).iterdir()}
        for leftover in ("current.staging-1", "current.old-1", "review-bundle.tar.gz.tmp", "AMENDMENT_DIFF.patch",
                         "CHANGES_SINCE_REVIEW.md"):
            self.assertNotIn(leftover, names)
        self.no_armour(record)

    def test_another_items_entry_changed_meanwhile_is_kept_and_the_job_ends_at_the_gate(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            path = self.root / STATE
            document = json.loads(path.read_text())
            document["work_items"]["other-item"] = json.loads(json.dumps(document["work_items"][WID]))
            other = document["work_items"]["other-item"]
            other["work_item_id"] = "other-item"
            for key in ("plan_path", "registry_path", "mapping_path"):
                other.pop(key, None)  # an item with no declared files of its own: none to be claimed twice
            path.write_text(json.dumps(document, indent=2) + "\n")

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertNotEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
        document = json.loads((self.root / STATE).read_text())
        self.assertIn("other-item", document["work_items"])  # the concurrent entry is kept ...
        self.assertEqual(document["work_items"][WID]["plan_revision"], 2)  # ... beside this item's carried entry
        self.assert_carry_ended(record)

    def test_this_items_entry_moved_meanwhile_is_refused_as_changed(self) -> None:
        seen: dict = {}

        def work(clone: Path) -> None:
            self.plan_work(clone)
            path = self.root / STATE
            document = json.loads(path.read_text())
            document["work_items"][WID]["state_revision"] += 7
            path.write_text(json.dumps(document, indent=2) + "\n")
            seen["state"] = path.read_bytes()
            seen["plan"] = (self.root / _entry(self.root)["plan_path"]).read_bytes()
            seen["registry"] = (self.root / _entry(self.root)["registry_path"]).read_bytes()

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["code"], job.CODEX_CARRY_REFUSED_CODE)
        self.assertEqual(self.evidence(record)["cause"], "carry_managed_changed")
        # refused before any write: the plan, the registry and the state file are the bytes the other party left
        self.assertEqual((self.root / STATE).read_bytes(), seen["state"])
        self.assertEqual((self.root / _entry(self.root)["plan_path"]).read_bytes(), seen["plan"])
        self.assertEqual((self.root / _entry(self.root)["registry_path"]).read_bytes(), seen["registry"])
        self.assertIn(STATE, record["reconciliation_evidence"]["codex_carry"]["not_written"])
        self.assertTrue(self.clone().exists())
        self.no_armour(record)

    def test_a_managed_member_changed_by_someone_else_is_refused_and_nothing_is_written(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            request = self.root / ITEM / "plan-inputs" / "REVIEW_REQUEST.md"
            request.write_text(request.read_text() + "edited by someone else\n")

        self.script(message="applied")
        self.after = work
        edited = (self.root / ITEM / "plan-inputs" / "REVIEW_REQUEST.md")
        state_before = (self.root / STATE).read_bytes()
        plan_before = (self.root / _entry(self.root)["plan_path"]).read_bytes()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["cause"], "carry_managed_changed")
        self.assertIn("edited by someone else", edited.read_text())
        self.assertEqual((self.root / STATE).read_bytes(), state_before)
        self.assertEqual((self.root / _entry(self.root)["plan_path"]).read_bytes(), plan_before)

    # -- a kill at each member boundary of the carry out ----------------------------------------------------

    def test_a_kill_at_each_member_boundary_of_the_carry_out_is_finished_by_resume(self) -> None:
        reference = None
        kills = 0
        for after in range(0, 8):
            self.setUp()
            shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
            shutil.rmtree(self.runtime / "worker", ignore_errors=True)
            self.script(message="applied")
            self.after = self.plan_work
            real = runtime_module.write_carried_file
            seen: list[str] = []

            def write(root, rel, data, _seen=seen, _after=after, **kw):
                if Path(root) == self.root:
                    if len(_seen) >= _after:
                        raise KeyboardInterrupt
                    _seen.append(rel)
                return real(root, rel, data, **kw)

            killed = False
            with mock.patch.object(runtime_module, "write_carried_file", write):
                try:
                    record = self.step()
                except KeyboardInterrupt:
                    killed = True
            if not killed:
                self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
                break
            kills += 1
            self.assertEqual(self.only_record()["codex_carry"]["stage"], "out")
            self.assertTrue(self.clone().exists())
            (resumed,) = self.resume()
            self.assertEqual(resumed["status"], job.STATUS_GATE_BLOCKED, resumed.get("reconciliation_evidence"))
            self.assertEqual(resumed["codex_carry"]["stage"], "done")
            self.assertFalse(self.clone().exists())
            self.assertEqual(_entry(self.root)["plan_revision"], 2)
            files = self.item_files()
            if reference is None:
                reference = files
            self.assertEqual({k: v for k, v in files.items() if k.endswith(("PLAN.md", "registry.json"))},
                             {k: v for k, v in reference.items() if k.endswith(("PLAN.md", "registry.json"))})
            self.no_armour(resumed)
        self.assertGreaterEqual(kills, 4)  # the plan, the registry and a member each saw a kill, and the state's own


class PlanRoleNoMarkerTest(Safety, base.PlanCase):
    def test_the_plan_role_runs_no_script_of_the_managed_tree(self) -> None:
        """The worker leaves a hook-like script in the clone; the Controller carries nothing out of it and executes
        nothing in the managed repository."""
        marker = self.tmp / "marker"

        def work(clone: Path) -> None:
            self.plan_work(clone)
            (clone / "scripts" / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["cause"], "carry_out_of_set")
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "scripts" / "sitecustomize.py").exists())


class ImplRefusalsTest(Safety, base.ImplCase):
    """D10's implementation-role cases."""

    def settled(self, record: dict) -> None:
        """Nothing was landed: the branch is where D4's commit left it and the managed tree is clean."""
        carry = record["codex_carry"]
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), carry["base_commit"])
        self.assertEqual(_git(self.root, "status", "--porcelain"), "")

    def refuses(self, work, cause: str, *, detail: str | None = None) -> dict:
        self.script(message="applied")
        self.after = lambda clone: (self.impl_work(clone), work(clone))
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
        found = self.evidence(record)
        self.assertEqual((found["code"], found["cause"]), (job.CODEX_CARRY_REFUSED_CODE, cause), found)
        if detail is not None:
            self.assertEqual(found["evidence"]["detail"], detail)
        self.settled(record)  # the landing runs after the checks: a refused carry out lands nothing
        self.assertTrue(self.clone().exists())
        self.no_armour(record)
        return record

    def test_a_failed_pending_commit_refuses_before_the_clone(self) -> None:
        for failure in (WorkflowQueryError("the committer failed"),):
            with self.subTest(failure=type(failure).__name__):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.launched.clear()
                self.script(message="applied")
                before = self.managed_snapshot()
                with mock.patch.object(review_recorder, "commit_pending", side_effect=failure):
                    record = self.step()
                self.assertEqual(record["status"], job.STATUS_FAILED)
                self.assertEqual(self.evidence(record)["code"], job.WORKER_NOT_STARTED_CODE)
                self.assertIn("carry_commit_pending_failed", json.dumps(self.evidence(record)))
                self.assertEqual(self.launched, [])
                self.assertFalse(self.clone().exists())
                self.assertEqual(self.managed_snapshot(), before)

    def test_a_generation_that_withdrew_current_is_refused_and_lands_nothing(self) -> None:
        """The worker's own generation in the clone withdrew ``current/`` (quarantined it beside the marker): a carried
        member is gone, so the carry out refuses before the landing."""
        def withdrew(clone: Path) -> None:
            item = clone / ITEM
            shutil.move(str(item / "current"), str(item / "current.rejected-1"))
            (item / "REJECTED").write_text("withdrawn\n")

        self.refuses(withdrew, "carry_member_refused", detail="absent")

    def test_a_link_or_special_file_at_a_member_is_refused_before_the_landing(self) -> None:
        for name in ("symlink", "fifo"):
            with self.subTest(kind=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)

                def change(clone: Path, name=name) -> None:
                    member = clone / ITEM / "current" / "CONTEXT_FILES.txt"
                    member.unlink(missing_ok=True)
                    if name == "symlink":
                        os.symlink("/etc/passwd", member)
                    else:
                        os.mkfifo(member)

                self.refuses(change, "carry_member_refused", detail="kind")

    def test_a_write_outside_the_set_is_refused_before_the_landing(self) -> None:
        def stray(clone: Path) -> None:
            (clone / ITEM / "feedback" / "REVIEW_FEEDBACK.md").write_text("rewritten by the worker\n")

        self.refuses(stray, "carry_out_of_set")

    def test_the_state_file_is_outside_the_implementation_roles_set(self) -> None:
        """The implementation role carries out no state file: any change to it in the clone is outside the set."""
        def foreign(clone: Path) -> None:
            path = clone / STATE
            document = json.loads(path.read_text())
            document["active_work_item_id"] = "somebody-else"
            path.write_text(json.dumps(document, indent=2) + "\n")

        self.refuses(foreign, "carry_out_of_set")

    def test_a_hook_written_into_the_clone_is_refused_before_the_landing(self) -> None:
        def hook(clone: Path) -> None:
            hooks = clone / ".git" / "hooks"
            hooks.mkdir(exist_ok=True)
            (hooks / "post-merge").write_text("#!/bin/sh\necho owned\n")

        self.refuses(hook, "clone_git_changed")
        self.assertFalse((self.root / ".git" / "hooks" / "post-merge").exists())

    def test_derived_leftovers_in_the_clone_are_discarded(self) -> None:
        def leftovers(clone: Path) -> None:
            item = clone / ITEM
            (item / "current.staging-1").mkdir()
            (item / "current.old-1").mkdir()
            (item / "review-bundle.tar.gz.tmp").write_bytes(b"partial")

        self.script(message="applied")
        self.after = lambda clone: (self.impl_work(clone), leftovers(clone))
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        names = {path.name for path in (self.root / ITEM).iterdir()}
        for name in ("current.staging-1", "current.old-1", "review-bundle.tar.gz.tmp"):
            self.assertNotIn(name, names)
        self.assert_carry_ended(record)


class ImplBoundaryTest(Safety, base.ImplCase):
    """The boundary the item draws: the Controller runs nothing of the managed tree."""

    def test_a_landed_site_hook_a_root_module_and_a_bytecode_file_run_nothing(self) -> None:
        marker = self.tmp / "marker"
        source = f"open({str(marker)!r}, 'a').write('ran\\n')\n"

        def plant(clone: Path) -> None:
            (clone / "scripts" / "sitecustomize.py").write_text(source)
            (clone / "secrets.py").write_text(source)
            pycache = clone / "scripts" / "__pycache__"
            pycache.mkdir(exist_ok=True)
            scratch = self.tmp / "planted.py"
            scratch.write_text(source)
            py_compile.compile(str(scratch), cfile=str(pycache / "planted.cpython-313.pyc"), doraise=True)
            subprocess.run(["git", "add", "-f", "scripts/sitecustomize.py", "secrets.py", "scripts/__pycache__"],
                           cwd=clone, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-q", "-m",
                            "plant"], cwd=clone, check=True, capture_output=True)

        def work(clone: Path) -> None:
            self.impl_work(clone)
            plant(clone)

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertFalse(marker.exists(), "the Controller ran something of the managed tree")
        self.assertIn(record["status"], (job.STATUS_GATE_BLOCKED, job.STATUS_FAILED))
        found = self.evidence(record)
        if record["status"] == job.STATUS_FAILED:
            self.assertIn(found.get("code"), (job.CODEX_LANDING_REFUSED_CODE, job.CODEX_CARRY_REFUSED_CODE), found)
        self.no_armour(record)
        self.assertFalse(marker.exists())

    def test_after_the_carried_out_ending_the_operators_regeneration_passes_the_next_preconditions(self) -> None:
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.evidence(record)["code"], job.CARRY_BUNDLE_GATE_CODE)
        self.regenerate_as_the_operator("post-fix")


class PlanOperatorTest(Safety, base.PlanCase):
    def test_after_the_carried_out_ending_the_operators_regeneration_passes_the_next_preconditions(self) -> None:
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.evidence(record)["code"], job.CARRY_BUNDLE_GATE_CODE)
        before = _entry(self.root)["current_bundle_id"]
        self.regenerate_as_the_operator("plan")
        self.assertEqual(_entry(self.root)["current_bundle_id"], before)  # kept as the clone wrote it

    def test_the_stale_bundle_variants_stay_failed_through_the_job(self) -> None:
        """Each condition of the narrow check, failing alone through the real reconcile answer."""
        def second_reason(clone: Path) -> None:
            self.plan_work(clone)
            (self.root / ITEM / "REJECTED").write_text("the generator's failure marker\n")

        def missing_manifest(clone: Path) -> None:
            self.plan_work(clone)
            (self.root / ITEM / "current" / "MANIFEST.md").unlink()

        def same_revision(clone: Path) -> None:
            self.plan_work(clone)
            manifest = self.root / ITEM / "current" / "MANIFEST.md"
            manifest.write_text(manifest.read_text().replace("plan_revision: 1", "plan_revision: 2"))

        for name, change in (("second_reason", second_reason), ("missing_manifest", missing_manifest),
                             ("same_revision", same_revision)):
            with self.subTest(variant=name):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.script(message="applied")
                self.after = change
                record = self.step()
                self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
                self.assertEqual(self.evidence(record)["reason"], job.RECONCILE_INVALID_REASON)
                self.no_armour(record)


class CauseCoverageTest(unittest.TestCase):
    def test_every_cause_has_a_case_and_every_case_is_a_cause(self) -> None:
        self.assertEqual(set(CAUSE_CASES), set(codex_carry.CAUSES))
        for cause, (klass, method) in CAUSE_CASES.items():
            self.assertTrue(callable(getattr(globals().get(klass), method, None)), f"{cause}: {klass}.{method}")

    def test_the_refusal_code_is_the_one_cause_a_record_names(self) -> None:
        self.assertNotIn(job.CARRY_BUNDLE_GATE_CODE, codex_carry.CAUSES)

    def test_a_cause_added_without_a_case_fails(self) -> None:
        with mock.patch.object(codex_carry, "CAUSES", (*codex_carry.CAUSES, "carry_new_cause")):
            with self.assertRaises(AssertionError):
                self.assertEqual(set(CAUSE_CASES), set(codex_carry.CAUSES))


if __name__ == "__main__":
    unittest.main()
