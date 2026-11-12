"""`workflow-controller-h4f` CP4 (D7): every plant a Codex checkpoint worker could make, through ``job.execute_step``.

The fake worker makes the plant **during** its run, in its clone, as commits (or as writes under the clone's ``.git``).
Each plant ends the job ``FAILED`` at the landing with the cause named, the managed repository's refs, index, tree and
state byte-for-byte unchanged and the clone kept; the clean cases land. A cause this item added with no case here
fails ``CausesCoveredTest``.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_worker_landing, job  # noqa: E402
from tests.test_codex_implement_job import CHECKPOINT_CODE, STATE, ImplementerEnvFixture  # noqa: E402
from tests.test_protocol_outcome import WID  # noqa: E402

#: The head of every plant's code: the clone's root, a committing helper and a state editor.
PLANT_PRELUDE = """
import json, os, subprocess
root = Path.cwd()
STATE = root / "docs/ai-workflow/WORKFLOW_STATE.json"
def git(*args):
    return subprocess.run(["git", "-c", "user.name=w", "-c", "user.email=w@w", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
def commit(message="plant"):
    git("add", "-A")
    git("commit", "-q", "-m", message)
def edit_state(change):
    state = json.loads(STATE.read_text())
    change(state)
    STATE.write_text(json.dumps(state, indent=2) + "\\n")
def write(path, text):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)
"""

#: name -> (the plant's code, the cause the landing names).
SURFACE_PLANTS = {
    "a_script_added": ('write("scripts/sitecustomize.py", "import os\\n"); commit()', "surface_touched"),
    "a_managed_script_and_the_digest_edited_together": (
        'p = root / "scripts/workflow_state.py"; p.write_text(p.read_text() + "\\n# x\\n")\n'
        'i = root / ".workflow-manager/installation.json"; i.write_text(i.read_text() + "\\n"); commit()',
        "surface_touched"),
    "the_installation_record_edited": (
        'i = root / ".workflow-manager/installation.json"; i.write_text(i.read_text() + "\\n"); commit()',
        "surface_touched"),
    "a_script_deleted": ('(root / "scripts/prepare-ai-review.sh").unlink(); commit()', "surface_touched"),
    "a_claude_settings_hook_added": (
        'write(".claude/settings.json", json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", '
        '"command": "touch /tmp/x"}]}]}})); commit()', "surface_touched"),
    "a_command_edited": (
        'p = root / ".claude/commands/milestone-implement.md"; p.write_text(p.read_text() + "\\nrun it\\n"); commit()',
        "surface_touched"),
    "a_codex_config_added": ('write(".codex/config.toml", "notify = [\\"x\\"]\\n"); commit()', "surface_touched"),
    "a_symlink_under_docs": ('os.symlink("/etc/passwd", root / "docs/link"); commit()', "surface_touched"),
    "an_executable_bit_flipped": ('os.chmod(root / "README.md", 0o755); commit()', "surface_touched"),
    "a_symlink_replacing_a_file": ('(root / "README.md").unlink(); os.symlink("x", root / "README.md"); commit()',
                                   "surface_touched"),
    "one_clean_and_one_touching_commit": (
        'write("notes.txt", "n\\n"); commit("clean")\nwrite("scripts/evil.py", "x = 1\\n"); commit("touching")',
        "surface_touched"),
}
STATE_PLANTS = {
    "plan_approval_edited": ('edit_state(lambda s: s["work_items"]["%s"]["plan_approval"].update(status="STALE")); '
                             'commit()' % WID, "state_touched"),
    "another_items_entry_added": ('edit_state(lambda s: s["work_items"].update(other={"phase": "IMPLEMENTING"})); '
                                  'commit()', "state_touched"),
    "a_gate_policy_field_edited": ('edit_state(lambda s: s["work_items"]["%s"].update(gate_policy_floor={"x": 1})); '
                                   'commit()' % WID, "state_touched"),
    "a_top_level_key_added": ('edit_state(lambda s: s.update(gate_policy_adoption={"adopted": True})); commit()',
                              "state_touched"),
    "the_state_file_unparseable": ('write("docs/ai-workflow/WORKFLOW_STATE.json", "{torn"); commit()',
                                   "state_touched"),
    "the_state_file_deleted": ('(root / "docs/ai-workflow/WORKFLOW_STATE.json").unlink(); commit()', "state_touched"),
    "this_items_entry_deleted": ('edit_state(lambda s: s["work_items"].pop("%s")); commit()' % WID, "state_touched"),
}


class PlantFixture(ImplementerEnvFixture):
    def plant(self, code: str) -> dict:
        self.script(message="done", workflow_python=[[PLANT_PRELUDE + code]])
        before = self.managed_snapshot()
        record = self.run_step()
        (execution,) = self.execs()
        self.assertEqual([r["exit"] for r in execution["ran"] if r["layer"] == "workflow_python"], [0], execution["ran"])
        self.assertEqual(self.managed_snapshot(), before)
        self.assertNotIn("-----BEGIN", json.dumps(record))
        self.assertNotIn("refresh_token", json.dumps(record))
        return record

    def assert_refusal(self, record: dict, cause: str) -> None:
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        block = record["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["cause"], block["moved"], block["clone_kept"]),
                         ("refused", cause, False, True))
        self.assertEqual(record["reconciliation_evidence"]["cause"], cause)
        self.assertTrue(self.clone_kept())
        self.assertEqual(self.branch_tip(), self.base)


def _plant_test(code: str, cause: str):
    def test(self: PlantFixture) -> None:
        self.assert_refusal(self.plant(code), cause)

    return test


class SurfacePlantTest(PlantFixture):
    pass


class StatePlantTest(PlantFixture):
    pass


for _name, (_code, _cause) in SURFACE_PLANTS.items():
    setattr(SurfacePlantTest, f"test_{_name}_is_refused_before_the_fast_forward", _plant_test(_code, _cause))
for _name, (_code, _cause) in STATE_PLANTS.items():
    setattr(StatePlantTest, f"test_{_name}_is_refused_before_the_fast_forward", _plant_test(_code, _cause))


class NetChangeTest(PlantFixture):
    def test_a_path_one_commit_adds_and_a_later_one_removes_lands(self) -> None:
        code = ('write("scripts/tmp.py", "x\\n"); commit("add")\ngit("rm", "-q", "scripts/tmp.py"); commit("remove")\n'
                'write("notes.txt", "n\\n"); commit("real")')
        record = self.plant_landing(code)
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")
        self.assertTrue((self.env.root / "notes.txt").exists())
        self.assertFalse((self.env.root / "scripts/tmp.py").exists())

    def test_a_new_executable_file_outside_the_surface_lands(self) -> None:
        record = self.plant_landing('write("tool.sh", "#!/bin/sh\\n"); os.chmod(root / "tool.sh", 0o755); commit()')
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")

    def plant_landing(self, code: str) -> dict:
        self.script(message="done", workflow_python=[[PLANT_PRELUDE + code]])
        return self.run_step()


class CheckpointStateTest(PlantFixture):
    def test_the_fields_a_checkpoint_writes_land_with_a_checkpoint_commit(self) -> None:
        self.script(message="done", workflow_python=[[CHECKPOINT_CODE, WID]])
        record = self.run_step()
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")

    def test_a_checkpoint_commit_that_also_edits_the_approval_is_refused_whole(self) -> None:
        # the checkpoint's own bookkeeping and commit, then a second commit that also edits the approval
        code = (CHECKPOINT_CODE + PLANT_PRELUDE + 'edit_state(lambda s: s["work_items"]["%s"]["plan_approval"]'
                '.update(status="STALE")); commit()' % WID)
        self.script(message="done", workflow_python=[[code, WID]])
        before = self.managed_snapshot()
        record = self.run_step()
        self.assert_refusal(record, "state_touched")
        self.assertEqual(self.managed_snapshot(), before)


class GuardBreachTest(PlantFixture):
    """The guard's metadata root is narrow: a hook, a ``config`` change, an alternates file or a link under the clone's
    ``.git`` is still a ``sandbox_breach`` with the flag on, and nothing lands."""

    PLANTS = {
        "a_symlink_under_ai_workflow": 'd = root / ".git/ai-workflow"; d.mkdir(exist_ok=True); os.symlink("/etc", d / "l")',
        "a_fifo_under_ai_workflow": 'd = root / ".git/ai-workflow"; d.mkdir(exist_ok=True); os.mkfifo(d / "f")',
        "a_hook": '(root / ".git/hooks/pre-commit").write_text("#!/bin/sh\\ntouch /tmp/x\\n")',
        "a_config_change": 'p = root / ".git/config"; p.write_text(p.read_text() + "[core]\\n\\tfsmonitor = x\\n")',
        "an_alternates_file": 'd = root / ".git/objects/info"; d.mkdir(parents=True, exist_ok=True); '
                              '(d / "alternates").write_text("/tmp\\n")',
    }

    def test_each_is_a_sandbox_breach_and_lands_nothing(self) -> None:
        for name, code in self.PLANTS.items():
            with self.subTest(plant=name):
                self.setUp_again()
                self.script(message="done", workflow_python=[[PLANT_PRELUDE + code + '\nwrite("notes.txt", "n\\n"); '
                                                              'commit()']])
                before = self.managed_snapshot()
                record = self.run_step()
                self.assert_refusal(record, "sandbox_breach")
                self.assertEqual(self.managed_snapshot(), before)

    def setUp_again(self) -> None:
        import shutil
        shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
        shutil.rmtree(self.job_dir(), ignore_errors=True)
        self.ids = 0


class CausesCoveredTest(unittest.TestCase):
    def test_every_cause_this_item_added_has_a_plant(self) -> None:
        covered = {cause for _, cause in (*SURFACE_PLANTS.values(), *STATE_PLANTS.values())}
        self.assertEqual({"surface_touched", "state_touched"} - covered, set())
        self.assertLessEqual({"surface_touched", "state_touched"}, set(codex_worker_landing.CAUSES))

    def test_every_new_cause_is_named_here(self) -> None:
        added_by_this_item = {"surface_touched", "state_touched"}
        h4e = {"sandbox_breach", "clone_git_changed", "extra_refs", "managed_branch_moved", "managed_tree_not_clean",
               "runtime_inside_managed", "managed_hooks_configured", "not_a_descendant", "merge_commit",
               "too_many_commits", "submodule", "fetch_failed", "tip_moved", "would_overwrite_ignored",
               "fast_forward_failed", "merge_left_tree_dirty", "landing_unverified", "landing_error",
               "landing_interrupted", "landing_unreadable"}
        self.assertEqual(set(codex_worker_landing.CAUSES) - h4e, added_by_this_item)


if __name__ == "__main__":
    unittest.main()
