"""`workflow-controller-h4e3` CP2: the carry of an apply role's working set into a Codex clone and back
(`controller.codex_carry`), D3 to D7.

Offline, over real Git repositories and the real clone the preparer makes; no job and no `codex`. The worker is
simulated by editing the clone. The recorder's state write is replaced by a recording fake here (its real behaviour is
proved in `tests/test_review_recorder.py`).
"""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_carry as carry, codex_worker_sandbox as ws, gitrepo, review_recorder  # noqa: E402
from controller import codex_worker_tree as tree  # noqa: E402
from tests import fixtures  # noqa: E402

WID = "wi-1"
STATE = "docs/ai-workflow/WORKFLOW_STATE.json"
PLAN = "docs/ai-workflow/PLAN.md"
REGISTRY = "docs/ai-workflow/registry/wi-1-registry.json"
MAPPING = "docs/ai-workflow/requirements/wi-1-mapping.json"
ARTIFACTS = "docs/ai-workflow/registry/wi-1-artifacts.json"
ITEM = f".ai-review/{WID}"
PLAN_MEMBERS = [f"{ITEM}/plan-inputs/REVIEW_REQUEST.md", f"{ITEM}/plan-inputs/TEST_RESULTS.md",
                f"{ITEM}/plan-inputs/CONTEXT_FILES.txt"]
IMPL_MEMBERS = [f"{ITEM}/current/{n}" for n in ("REVIEW_REQUEST.md", "IMPLEMENTATION_SUMMARY.md", "TEST_RESULTS.md",
                                                "CONTEXT_FILES.txt", "PLAN.md")]

#: The causes the tests below drive; a test fails when :data:`carry.CAUSES` has one that is not here.
COVERED: set[str] = set()


def git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=check)


def out(path: Path, *args: str) -> str:
    return git(path, *args).stdout.strip()


def write(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return path


def state_doc(**extra) -> dict:
    return {"schema_version": 1, "active_work_item_id": WID,
            "work_items": {WID: {"work_item_id": WID, "phase": "REVISING_PLAN", "plan_path": PLAN,
                                 "registry_path": REGISTRY, "mapping_path": MAPPING},
                           "other": {"work_item_id": "other", "phase": "IMPLEMENTING"}}, **extra}


class Case(unittest.TestCase):
    ROLE = "apply-plan-review"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.root = self.tmp / "managed"
        fixtures.git_init(self.root, "--initial-branch=trunk")
        write(self.root, ".gitignore", ".ai-review/\n__pycache__/\n")
        write(self.root, STATE, json.dumps(state_doc(), indent=2) + "\n")
        for rel in (PLAN, REGISTRY, MAPPING):
            write(self.root, rel, f"{rel} committed\n")
        write(self.root, "src.txt", "one\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        self.head = out(self.root, "rev-parse", "HEAD")
        self.managed = SimpleNamespace(root=self.root)
        self.layout = ws.WorkerLayout(self.tmp / "runtime", "job1")
        self.persisted: list[dict] = []
        self.state_calls: list[dict] = []
        self.carry_state = mock.patch.object(review_recorder, "carry_state", side_effect=self._fake_carry_state)
        self.carry_state.start()
        self.addCleanup(self.carry_state.stop)
        self.seed_working_set()

    # -- the working set ------------------------------------------------------

    def seed_working_set(self) -> None:
        """The plan role's uncommitted work: a modified plan and state, an intent-to-add artifacts file, and the
        ignored `.ai-review` tree with generated and author-written files."""
        write(self.root, PLAN, "plan revised\n")
        doc = state_doc()
        doc["work_items"][WID]["phase"] = "AMENDING_PLAN"
        write(self.root, STATE, json.dumps(doc, indent=2) + "\n")
        write(self.root, ARTIFACTS, "{}\n")
        git(self.root, "add", "-N", ARTIFACTS)
        for rel, text in (("feedback/REVIEW_FEEDBACK.md", "feedback\n"), ("plan-inputs/REVIEW_REQUEST.md", "request\n"),
                          ("plan-inputs/TEST_RESULTS.md", "results\n"), ("plan-inputs/CONTEXT_FILES.txt", "ctx\n"),
                          ("current/MANIFEST.md", "manifest\n"), ("current/REVIEW_REQUEST.md", "cur request\n"),
                          ("current/IMPLEMENTATION_SUMMARY.md", "summary\n"), ("current/TEST_RESULTS.md", "cur res\n"),
                          ("current/CONTEXT_FILES.txt", "cur ctx\n"), ("current/PLAN.md", "cur plan\n"),
                          ("current/DIFF.patch", "diff\n"), ("current/files/a.txt", "a\n"),
                          (".pin/pinned.txt", "pin\n"), ("CHANGES_SINCE_REVIEW.md", "changes\n"),
                          ("review-bundle.tar.gz", "archive\n")):
            write(self.root, f"{ITEM}/{rel}", text)

    def make_clean(self) -> None:
        """The managed tree back at HEAD, the ignored `.ai-review` tree kept (the implementation role's start)."""
        git(self.root, "reset", "-q", "--hard")
        git(self.root, "clean", "-fdq")

    def set_for(self, role: str | None = None) -> carry.CarrySet:
        return carry.compute(self.root, WID, role or self.ROLE)

    def block(self, carry_set: carry.CarrySet | None = None) -> dict:
        return carry.start_block(carry_set or self.set_for(), pre_step_head=self.head, base_commit=self.head)

    def persist(self, block: dict) -> None:
        self.persisted.append(block)

    def go_in(self, carry_set: carry.CarrySet | None = None) -> tuple[carry.CarrySet, dict, tree.Prepared]:
        carry_set = carry_set or self.set_for()
        block = self.block(carry_set)
        prepared = carry.carry_in(self.managed, self.layout, carry_set, block, persist=self.persist)
        return carry_set, block, prepared

    def check(self, carry_set, block, prepared=None) -> carry.OutPlan:
        return carry.check_out(self.managed, self.layout, carry_set, block, prepared=prepared)

    def refused(self, cause: str, fn, *args, detail: str | None = None, **kwargs) -> carry.CarryError:
        with self.assertRaises(carry.CarryError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.cause, cause)
        self.assertEqual(caught.exception.evidence["cause"], cause)
        if detail is not None:
            self.assertEqual(caught.exception.evidence.get("detail"), detail)
        COVERED.add(cause)
        return caught.exception

    def _fake_carry_state(self, root, work_item_id, payload_path, *, digests=None, timeout=None):
        self.state_calls.append({"payload": json.loads(Path(payload_path).read_text()), "files": self.snapshot_members()})
        return review_recorder.RecorderResult(True, None, None, None, {"written": True})

    def snapshot_members(self) -> dict:
        return {rel: (self.root / rel).read_bytes() for rel in (PLAN, REGISTRY, STATE, *PLAN_MEMBERS)
                if (self.root / rel).is_file()}

    def managed_snapshot(self) -> dict:
        return {"head": out(self.root, "rev-parse", "HEAD"), "refs": out(self.root, "for-each-ref"),
                "status": out(self.root, "status", "--porcelain", "--untracked-files=all"),
                "files": {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                          if p.is_file() and ".git/" not in str(p) and "/.git" not in str(p.parent)}}

    # -- the worker ----------------------------------------------------------

    def edit(self, prepared: tree.Prepared, rel: str, text: str | bytes) -> None:
        write(prepared.path, rel, text)

    def clone_state(self, prepared: tree.Prepared) -> dict:
        return json.loads((prepared.path / STATE).read_text())

    def edit_state(self, prepared: tree.Prepared, mutate) -> None:
        doc = self.clone_state(prepared)
        mutate(doc)
        write(prepared.path, STATE, json.dumps(doc, indent=2) + "\n")


class SetTest(Case):
    def test_the_plan_role_set_is_derived_from_the_managed_state(self) -> None:
        carry_set = self.set_for()
        self.assertEqual(carry_set.named, (PLAN, REGISTRY, MAPPING, ARTIFACTS, STATE))
        self.assertEqual(list(carry_set.out_members), PLAN_MEMBERS)
        self.assertEqual(carry_set.item_root, ITEM)

    def test_the_implementation_role_names_no_file(self) -> None:
        carry_set = self.set_for("apply-implementation-review")
        self.assertEqual(carry_set.named, ())
        self.assertEqual(list(carry_set.out_members), IMPL_MEMBERS)

    def test_an_unknown_role_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            carry.compute(self.root, WID, "apply-nothing")

    def test_derived_paths(self) -> None:
        plan, impl = self.set_for(), self.set_for("apply-implementation-review")
        for rel in (".pin/x", ".pin.staging-1/x", ".pin.tmp", "current.staging-a/x", "current.old-b", "current.rejected-c",
                    "AMENDMENT_DIFF.patch", "CHANGES_SINCE_REVIEW.md", "review-bundle.tar.gz",
                    "review-bundle.tar.gz.tmp", "REJECTED", "review-baselines/X", "current/anything"):
            self.assertTrue(plan.is_derived(rel), rel)
        for rel in ("current/MANIFEST.md", "current/CHANGED_FILES.txt", "current/DIFF.patch", "current/COMMITS.txt",
                    "current/files/a.txt", ".pin/x", "current.staging-a"):
            self.assertTrue(impl.is_derived(rel), rel)
        for rel in ("current/REVIEW_REQUEST.md", "current/PLAN.md", "feedback/REVIEW_FEEDBACK.md",
                    "plan-inputs/TEST_RESULTS.md", "other.txt"):
            self.assertFalse(impl.is_derived(rel), rel)
        self.assertFalse(plan.is_derived("plan-inputs/REVIEW_REQUEST.md"))
        self.assertFalse(plan.is_derived("feedback/REVIEW_FEEDBACK.md"))

    def test_the_derived_list_is_the_workflows_own_names(self) -> None:
        sources = {"scripts/workflow_fingerprint.py": (REPO_ROOT / "scripts/workflow_fingerprint.py").read_bytes()}
        names = carry.workflow_names(sources)
        self.assertEqual(set(names["_LEFTOVER_STAGING_PREFIXES"]) - set(carry.ITEM_ROOT_DERIVED_PREFIXES), set())
        self.assertIn(names["CHANGES_SINCE_REVIEW_FILE"], carry.ITEM_ROOT_DERIVED)
        self.assertIn(names["STAGE_BASELINE_DIR"], carry.ITEM_ROOT_DERIVED)
        self.assertEqual(carry.workflow_names({}), {})
        self.assertEqual(carry.workflow_names({"scripts/workflow_fingerprint.py": b"def ("}), {})

    def test_a_name_the_workflow_adds_is_derived(self) -> None:
        source = b'_LEFTOVER_STAGING_PREFIXES = ("zz.staging-",)\nCHANGES_SINCE_REVIEW_FILE = "NEW_NAME.md"\n'
        carry_set = carry.compute(self.root, WID, self.ROLE, sources={"scripts/workflow_fingerprint.py": source})
        self.assertTrue(carry_set.is_derived("zz.staging-1/x"))
        self.assertTrue(carry_set.is_derived("NEW_NAME.md"))

    def test_every_name_the_generators_write_under_the_item_root_is_a_member_or_derived(self) -> None:
        """A name the generators write under `.ai-review/<work item>/` that is neither carried out nor derived fails
        here: the names are read from the Workflow's own scripts."""
        text = (REPO_ROOT / "scripts/workflow_fingerprint.py").read_text() + (
            REPO_ROOT / "scripts/prepare-ai-review.sh").read_text()
        written = set(re.findall(r'(?:item_root|root_dir|dest_root|outgoing_root)\s*/\s*"([^"{]+)"', text))
        written |= set(re.findall(r'\$ROOT_DIR/([A-Za-z0-9_.-]+)', text))
        plan, impl = self.set_for(), self.set_for("apply-implementation-review")
        carried_dirs = set(carry.IN_DIRS)
        unclassified = sorted(name for name in written
                              if name not in carried_dirs and not plan.is_derived(name) and not impl.is_derived(name)
                              and not name.endswith("/"))
        self.assertEqual(unclassified, [], "name(s) the generators write that the carry neither carries out nor "
                         "treats as derived")
        self.assertGreater(len(written), 3)

    def test_the_carried_out_members_are_the_regeneration_inputs(self) -> None:
        for stage, members in carry.OUT_MEMBERS.items():
            for member in members:
                self.assertFalse(carry.CarrySet(role="r", stage=stage, work_item_id=WID, named=(), item_root=ITEM,
                                                out_members=()).is_derived(member), member)


class SelfReviewRoleTest(Case):
    """`workflow-controller-h4g` CP3 (D4): the wrap-up role's carry is the implementation stage's, and nothing named."""

    SELF = "milestone-implement-self-review"

    def test_the_role_is_the_implementation_stage_with_the_apply_roles_members(self) -> None:
        self.assertEqual(carry.ROLES[self.SELF], "implementation")
        mine, apply = self.set_for(self.SELF), self.set_for("apply-implementation-review")
        self.assertEqual(mine.stage, "implementation")
        self.assertEqual(mine.named, ())
        self.assertEqual(list(mine.out_members), IMPL_MEMBERS)
        self.assertEqual((mine.out_members, mine.derived_names, mine.derived_prefixes, mine.item_root),
                         (apply.out_members, apply.derived_names, apply.derived_prefixes, apply.item_root))
        self.assertEqual(mine.role, self.SELF)

    def test_the_other_roles_are_unchanged(self) -> None:
        self.assertEqual(carry.ROLES["apply-plan-review"], "plan")
        self.assertEqual(carry.ROLES["apply-implementation-review"], "implementation")
        self.assertEqual(len(carry.ROLES), 3)

    def test_a_first_round_carries_the_plan_stage_bundle_in_and_needs_no_feedback(self) -> None:
        """`current/` holds the approved plan-stage bundle on a first self-review round; `feedback/` is absent."""
        self.make_clean()
        shutil.rmtree(self.root / ITEM / "feedback")
        for rel in ("CHANGED_FILES.txt", "COMMITS.txt"):
            write(self.root, f"{ITEM}/current/{rel}", "derived\n")
        _set, block, prepared = self.go_in(self.set_for(self.SELF))
        for rel in ("REVIEW_REQUEST.md", "PLAN.md", "CONTEXT_FILES.txt", "MANIFEST.md", "CHANGED_FILES.txt",
                    "DIFF.patch", "COMMITS.txt", "files/a.txt"):
            self.assertTrue((prepared.path / ITEM / "current" / rel).is_file(), rel)
        self.assertFalse((prepared.path / ITEM / "feedback").exists())
        self.assertEqual(block["intent_to_add"], [])
        self.assertEqual(out(prepared.path, "status", "--porcelain"), "")

    def test_a_dirty_managed_tree_is_foreign_dirt_for_the_carry_itself(self) -> None:
        """The cause the job reports is `managed_tree_not_clean`, from its own check before `carry_in` (CP4)."""
        self.make_clean()
        write(self.root, "src.txt", "changed\n")
        self.refused("carry_foreign_dirt", carry.check_dirty, self.root, self.set_for(self.SELF))

    def test_a_worker_modified_derived_file_is_never_carried_out(self) -> None:
        self.make_clean()
        carry_set, block, prepared = self.go_in(self.set_for(self.SELF))
        for rel in ("MANIFEST.md", "DIFF.patch", "CHANGED_FILES.txt", "COMMITS.txt", "files/a.txt", "files/new.txt"):
            self.edit(prepared, f"{ITEM}/current/{rel}", "forged by the worker\n")
        for rel in (".pin/pinned.txt", "review-bundle.tar.gz", "CHANGES_SINCE_REVIEW.md"):
            self.edit(prepared, f"{ITEM}/{rel}", "forged by the worker\n")
        plan = self.check(carry_set, block)
        self.assertEqual(plan.writes, ())
        before = self.managed_snapshot()
        carry.write_out(self.managed, self.layout, carry_set, block, plan)
        self.assertEqual(self.managed_snapshot(), before)

    def test_only_the_changed_out_members_are_written_and_the_state_is_not_carried(self) -> None:
        self.make_clean()
        carry_set, block, prepared = self.go_in(self.set_for(self.SELF))
        self.edit(prepared, IMPL_MEMBERS[1], "new summary\n")
        self.edit(prepared, IMPL_MEMBERS[4], "new plan\n")
        self.edit(prepared, f"{ITEM}/current/MANIFEST.md", "forged\n")
        plan = self.check(carry_set, block)
        self.assertEqual(sorted(w.rel for w in plan.writes), sorted([IMPL_MEMBERS[1], IMPL_MEMBERS[4]]))
        self.assertIsNone(plan.state)
        carry.write_out(self.managed, self.layout, carry_set, block, plan)
        self.assertEqual((self.root / IMPL_MEMBERS[1]).read_text(), "new summary\n")
        self.assertEqual((self.root / f"{ITEM}/current/MANIFEST.md").read_text(), "manifest\n")
        self.assertEqual(self.state_calls, [])

    def test_an_out_member_that_became_a_link_is_refused(self) -> None:
        self.make_clean()
        carry_set, block, prepared = self.go_in(self.set_for(self.SELF))
        member = prepared.path / IMPL_MEMBERS[0]
        member.unlink()
        member.symlink_to("/etc/hostname")
        self.refused("carry_member_refused", self.check, carry_set, block)


class DirtTest(Case):
    def test_named_dirt_is_accepted(self) -> None:
        self.assertEqual(carry.check_dirty(self.root, self.set_for()), sorted([ARTIFACTS, PLAN, STATE]))

    def test_a_tracked_change_outside_the_set_is_foreign_dirt(self) -> None:
        write(self.root, "src.txt", "changed\n")
        error = self.refused("carry_foreign_dirt", carry.check_dirty, self.root, self.set_for())
        self.assertEqual(error.evidence["paths"], ["src.txt"])

    def test_an_untracked_file_outside_the_set_is_foreign_dirt(self) -> None:
        write(self.root, "new.txt", "x\n")
        self.refused("carry_foreign_dirt", carry.check_dirty, self.root, self.set_for())

    def test_an_intent_to_add_path_outside_the_set_is_foreign_dirt(self) -> None:
        write(self.root, "added.txt", "x\n")
        git(self.root, "add", "-N", "added.txt")
        self.refused("carry_foreign_dirt", carry.check_dirty, self.root, self.set_for())

    def test_the_implementation_role_has_no_named_file_so_any_dirt_is_foreign(self) -> None:
        self.refused("carry_foreign_dirt", carry.check_dirty, self.root, self.set_for("apply-implementation-review"))

    def test_foreign_dirt_refuses_before_any_clone_is_made_and_writes_nothing(self) -> None:
        write(self.root, "src.txt", "changed\n")
        before = self.managed_snapshot()
        self.refused("carry_foreign_dirt", carry.carry_in, self.managed, self.layout, self.set_for(), self.block())
        self.assertFalse(self.layout.worktree.exists())
        self.assertEqual(self.managed_snapshot(), before)
        self.assertEqual(self.persisted, [])


class CarryInTest(Case):
    def test_the_clone_receives_the_set_and_the_intent_to_add_set(self) -> None:
        carry_set, block, prepared = self.go_in()
        clone = prepared.path
        self.assertEqual((clone / PLAN).read_text(), "plan revised\n")
        self.assertEqual((clone / STATE).read_bytes(), (self.root / STATE).read_bytes())
        self.assertEqual((clone / ARTIFACTS).read_text(), "{}\n")
        self.assertEqual(gitrepo.intent_to_add_paths(clone), [ARTIFACTS])
        self.assertEqual(block["intent_to_add"], [ARTIFACTS])
        self.assertEqual(out(clone, "rev-parse", "HEAD"), self.head)
        for rel in (f"{ITEM}/feedback/REVIEW_FEEDBACK.md", *PLAN_MEMBERS, f"{ITEM}/current/MANIFEST.md"):
            self.assertTrue((clone / rel).is_file(), rel)

    def test_derived_and_runtime_files_are_not_carried_in(self) -> None:
        write(self.root, ".ai-review/runtime/lock", "x")
        _set, _block, prepared = self.go_in()
        for rel in (f"{ITEM}/.pin", f"{ITEM}/CHANGES_SINCE_REVIEW.md", f"{ITEM}/review-bundle.tar.gz",
                    ".ai-review/runtime"):
            self.assertFalse((prepared.path / rel).exists(), rel)

    def test_the_block_records_the_stages_and_the_digests(self) -> None:
        carry_set, block, prepared = self.go_in()
        self.assertEqual([b["stage"] for b in self.persisted], ["preparing", "carried_in"])
        self.assertEqual(block["stage"], "carried_in")
        self.assertEqual(block["pre_step_head"], self.head)
        self.assertEqual(block["base_commit"], self.head)
        self.assertEqual(block["in_digests"][PLAN], carry._sha(b"plan revised\n"))
        self.assertEqual(block["clone"]["path"], str(prepared.path))
        self.assertEqual(block["clone"]["head"], self.head)

    def test_the_managed_repository_is_unchanged_by_the_carry_in(self) -> None:
        before = self.managed_snapshot()
        self.go_in()
        self.assertEqual(self.managed_snapshot(), before)

    def test_the_implementation_role_carries_in_the_bundle_inputs_only(self) -> None:
        self.make_clean()
        _set, block, prepared = self.go_in(self.set_for("apply-implementation-review"))
        self.assertTrue((prepared.path / f"{ITEM}/current/PLAN.md").is_file())
        self.assertEqual(block["intent_to_add"], [])
        self.assertEqual(out(prepared.path, "status", "--porcelain"), "")

    def test_a_member_refused_at_carry_in_leaves_no_clone(self) -> None:
        write(self.root, f"{ITEM}/feedback/auth.json", "{}")
        self.refused("carry_member_refused", carry.carry_in, self.managed, self.layout, self.set_for(), self.block(),
                     detail="name")
        self.assertFalse(self.layout.worktree.exists())

    def test_detail_name(self) -> None:
        write(self.root, f"{ITEM}/current/key.pem", "x")
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="name")

    def test_detail_kind_symlink(self) -> None:
        os.symlink("/etc/passwd", self.root / ITEM / "feedback" / "link")
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="kind")

    def test_detail_kind_special_file(self) -> None:
        os.mkfifo(self.root / ITEM / "feedback" / "fifo")
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="kind")

    def test_detail_kind_symlinked_directory(self) -> None:
        os.symlink(self.tmp, self.root / ITEM / "plan-inputs.real")
        (self.root / ITEM / "feedback").rename(self.root / ITEM / "feedback.real")
        os.symlink(self.root / ITEM / "feedback.real", self.root / ITEM / "feedback")
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="kind")

    def test_detail_git_component(self) -> None:
        write(self.root, f"{ITEM}/current/files/.git/config", "x")
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="git")

    def test_detail_gitlink(self) -> None:
        git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.head},{MAPPING}")
        (self.root / MAPPING).unlink()
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="gitlink")

    def test_detail_escape(self) -> None:
        doc = state_doc()
        doc["work_items"][WID]["registry_path"] = "../outside.json"
        write(self.root, STATE, json.dumps(doc))
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="escape")

    def test_detail_escape_symlinked_item_root(self) -> None:
        outside = self.tmp / "outside"
        write(outside, "feedback/notes.txt", "private notes outside the repo\n")
        shutil.rmtree(self.root / ITEM)
        os.symlink(outside, self.root / ITEM)
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="escape")

    def test_detail_escape_symlinked_ai_review_dir(self) -> None:
        outside = self.tmp / "outside2"
        write(outside, "x/plan.md", "outside plan\n")
        shutil.rmtree(self.root / ".ai-review")
        os.symlink(outside, self.root / ".ai-review")
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="escape")

    def test_detail_escape_symlinked_parent_of_a_named_file(self) -> None:
        carry_set = self.set_for()
        registry = next(rel for rel in carry_set.named if rel not in (PLAN, STATE) and "/" in rel)
        parent = registry.rsplit("/", 1)[0]
        outside = self.tmp / "outside3"
        write(outside, registry.rsplit("/", 1)[1], "outside\n")
        if parent == PLAN.rsplit("/", 1)[0]:
            self.skipTest("the named files share one directory")
        shutil.rmtree(self.root / parent)
        os.symlink(outside, self.root / parent)
        self.refused("carry_member_refused", carry.collect_in, self.root, carry_set, detail="escape")

    def test_a_member_in_the_runtime_root_is_refused(self) -> None:
        with self.assertRaises(carry.CarryError) as raised:
            carry.read_member(self.root, f"{ITEM}/feedback/note.md", runtime_root=self.root / ITEM)
        self.assertEqual(raised.exception.evidence["detail"], "escape")

    def test_detail_bound_file(self) -> None:
        write(self.root, f"{ITEM}/current/big.bin", b"\0" * (8 * 1024 * 1024 + 1))
        self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="bound")

    def test_detail_bound_total_and_count(self) -> None:
        with mock.patch.object(carry.codex_review_role, "MAX_INPUT_BYTES", 10):
            self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="bound")
        with mock.patch.object(carry.codex_review_role, "MAX_INPUT_PATHS", 2):
            self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="bound")

    def test_detail_armour(self) -> None:
        write(self.root, f"{ITEM}/feedback/note.md", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
        error = self.refused("carry_member_refused", carry.collect_in, self.root, self.set_for(), detail="armour")
        self.assertNotIn("abc", str(error.evidence))

    def test_a_member_is_written_with_o_nofollow_and_never_through_a_planted_link(self) -> None:
        target = self.tmp / "elsewhere"
        target.write_text("keep\n")
        carried = carry.collect_in(self.root, self.set_for())
        clone = self.tmp / "clone"
        (clone / "docs").mkdir(parents=True)
        os.symlink(target, clone / "docs" / "ai-workflow")
        with self.assertRaises(OSError):
            carry.runtime.write_carried_file(clone, PLAN, carried.files[PLAN][0], mode=0o644)
        self.assertEqual(target.read_text(), "keep\n")

    def test_an_absent_named_file_is_not_carried(self) -> None:
        (self.root / ARTIFACTS).unlink()
        git(self.root, "rm", "-q", "--cached", ARTIFACTS, check=False)
        carried = carry.collect_in(self.root, self.set_for())
        self.assertNotIn(ARTIFACTS, carried.files)

    def test_the_prepare_options_are_accepted_only_when_the_set_accounts_for_every_dirty_path(self) -> None:
        with self.assertRaises(tree.CodexTreeError) as caught:
            tree.prepare(self.managed, self.layout, self.head, allow_dirty=[PLAN])
        self.assertEqual(caught.exception.cause, "managed_tree_not_clean")
        self.assertFalse(self.layout.worktree.exists())
        with self.assertRaises(tree.CodexTreeError):
            tree.prepare(self.managed, self.layout, self.head)
        prepared = tree.prepare(self.managed, self.layout, self.head, allow_dirty=carry.compute(
            self.root, WID, self.ROLE).named)
        self.assertEqual(prepared.base, self.head)

    def test_a_clone_made_after_a_refusal_after_the_clone_is_removed(self) -> None:
        with mock.patch.object(carry.runtime, "write_carried_file", side_effect=OSError("disk")):
            self.refused("carry_member_refused", carry.carry_in, self.managed, self.layout, self.set_for(), self.block())
        self.assertFalse(self.layout.worktree.exists())
        self.assertFalse((self.layout.base / "carry").exists())


class CarryOutTest(Case):
    def test_an_unchanged_clone_carries_nothing_out(self) -> None:
        carry_set, block, prepared = self.go_in()
        plan = self.check(carry_set, block)
        self.assertEqual(plan.writes, ())
        self.assertIsNone(plan.state)

    def test_a_changed_member_is_written_atomically_and_only_it(self) -> None:
        carry_set, block, prepared = self.go_in()
        self.edit(prepared, PLAN, "plan by the worker\n")
        self.edit(prepared, PLAN_MEMBERS[0], "new request\n")
        self.edit(prepared, f"{ITEM}/plan-inputs/NEW_NOTE.txt", "x")
        # the unchanged member keeps its bytes and its inode
        untouched = self.root / REGISTRY
        inode = untouched.stat().st_ino
        with self.assertRaises(carry.CarryError):  # NEW_NOTE.txt is outside the set
            self.check(carry_set, block)
        (prepared.path / ITEM / "plan-inputs" / "NEW_NOTE.txt").unlink()
        plan = self.check(carry_set, block)
        self.assertEqual(sorted(w.rel for w in plan.writes), sorted([PLAN, PLAN_MEMBERS[0]]))
        index_before = (self.root / ".git" / "index").read_bytes()
        outcome = carry.write_out(self.managed, self.layout, carry_set, block, plan, persist=self.persist)
        self.assertEqual(sorted(outcome["written"]), sorted([PLAN, PLAN_MEMBERS[0]]))
        self.assertEqual((self.root / PLAN).read_text(), "plan by the worker\n")
        self.assertEqual((self.root / PLAN_MEMBERS[0]).read_text(), "new request\n")
        self.assertEqual(untouched.stat().st_ino, inode)
        self.assertEqual((self.root / ".git" / "index").read_bytes(), index_before)
        self.assertEqual(gitrepo.intent_to_add_paths(self.root), [ARTIFACTS])
        self.assertEqual([p.name for p in self.root.rglob(".*.tmp")], [])
        self.assertEqual(block["stage"], "done")
        self.assertEqual(self.persisted[-1]["stage"], "done")

    def test_the_state_file_is_carried_through_the_recorder_and_last(self) -> None:
        carry_set, block, prepared = self.go_in()
        self.edit(prepared, PLAN, "plan by the worker\n")
        self.edit_state(prepared, lambda d: d["work_items"][WID].update(phase="REVISING_PLAN", plan_revision=2))
        plan = self.check(carry_set, block)
        self.assertEqual(plan.state["after"]["plan_revision"], 2)
        self.assertEqual(plan.state["before"]["phase"], "AMENDING_PLAN")
        self.assertNotIn(STATE, [w.rel for w in plan.writes])
        outcome = carry.write_out(self.managed, self.layout, carry_set, block, plan)
        self.assertTrue(outcome["state_written"])
        self.assertEqual(len(self.state_calls), 1)
        self.assertEqual(self.state_calls[0]["files"][PLAN], b"plan by the worker\n")  # members first
        self.assertEqual(json.loads((self.root / STATE).read_text())["work_items"][WID]["phase"], "AMENDING_PLAN")

    def test_derived_leftovers_are_discarded_and_never_judged(self) -> None:
        carry_set, block, prepared = self.go_in()
        for rel in ("current.staging-x/a", "current.old-y/b", "review-bundle.tar.gz.tmp", "AMENDMENT_DIFF.patch",
                    "REJECTED", "current.rejected-z/q", ".pin.staging-1/f", "current/MANIFEST.md",
                    "CHANGES_SINCE_REVIEW.md", "review-baselines/MANUAL_EXTERNAL_PLAN_REVIEW"):
            self.edit(prepared, f"{ITEM}/{rel}", "worker\n")
        shutil_rm = prepared.path / ITEM / "current" / "files"
        if shutil_rm.exists():
            for child in shutil_rm.rglob("*"):
                if child.is_file():
                    child.unlink()
        plan = self.check(carry_set, block)
        self.assertEqual(plan.writes, ())
        before = self.managed_snapshot()
        carry.write_out(self.managed, self.layout, carry_set, block, plan)
        self.assertEqual(self.managed_snapshot(), before)

    def test_a_present_carried_out_member_gone_from_the_clone_is_refused_absent(self) -> None:
        carry_set, block, prepared = self.go_in()
        (prepared.path / PLAN_MEMBERS[1]).unlink()
        self.refused("carry_member_refused", self.check, carry_set, block, detail="absent")

    def test_a_member_absent_at_carry_in_that_the_worker_creates_is_a_changed_member(self) -> None:
        (self.root / PLAN_MEMBERS[2]).unlink()
        carry_set, block, prepared = self.go_in()
        self.assertIsNone(block["managed"][PLAN_MEMBERS[2]])
        self.edit(prepared, PLAN_MEMBERS[2], "created\n")
        plan = self.check(carry_set, block)
        self.assertEqual([w.rel for w in plan.writes], [PLAN_MEMBERS[2]])
        carry.write_out(self.managed, self.layout, carry_set, block, plan)
        self.assertEqual((self.root / PLAN_MEMBERS[2]).read_text(), "created\n")

    def test_the_causes_in_d6s_order(self) -> None:
        """Each check, with the earlier ones passing: one cause each."""
        carry_set, block, prepared = self.go_in()
        clone = prepared.path
        # carry_unexpected_commit: a commit in the plan role's clone
        write(clone, "worker.txt", "w\n")
        git(clone, "add", "worker.txt")
        git(clone, "commit", "-q", "-m", "worker")
        self.refused("carry_unexpected_commit", self.check, carry_set, block)
        git(clone, "reset", "-q", "--mixed", self.head)
        (clone / "worker.txt").unlink()
        git(clone, "add", "-N", ARTIFACTS)
        # ... and a ref
        git(clone, "branch", "extra")
        self.refused("carry_unexpected_commit", self.check, carry_set, block)
        git(clone, "branch", "-D", "extra")
        # clone_git_changed: the clone's configuration
        config = clone / ".git" / "config"
        original = config.read_bytes()
        config.write_bytes(original + b"[core]\n\tfsmonitor = /bin/true\n")
        self.refused("clone_git_changed", self.check, carry_set, block)
        config.write_bytes(original)
        hook = clone / ".git" / "hooks" / "post-merge"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\n")
        self.refused("clone_git_changed", self.check, carry_set, block)
        hook.unlink()
        self.assertEqual(self.check(carry_set, block).writes, ())

    def test_carry_index_changed(self) -> None:
        carry_set, block, prepared = self.go_in()
        write(prepared.path, "extra.txt", "x\n")
        git(prepared.path, "add", "-N", "extra.txt")
        self.refused("carry_index_changed", self.check, carry_set, block)
        git(prepared.path, "reset", "-q", "extra.txt")
        (prepared.path / "extra.txt").unlink()
        git(prepared.path, "add", ARTIFACTS)  # intent-to-add path made a real entry
        self.refused("carry_index_changed", self.check, carry_set, block)
        git(prepared.path, "rm", "-q", "--cached", ARTIFACTS)
        self.refused("carry_index_changed", self.check, carry_set, block)

    def test_carry_out_of_set_each_way(self) -> None:
        carry_set, block, prepared = self.go_in()
        clone = prepared.path
        cases = {
            "an untracked file": lambda: write(clone, "new.txt", "x\n"),
            "a tracked change": lambda: write(clone, "src.txt", "changed\n"),
            "feedback changed": lambda: write(clone, f"{ITEM}/feedback/REVIEW_FEEDBACK.md", "other\n"),
            "feedback added": lambda: write(clone, f"{ITEM}/feedback/EXTRA.md", "x\n"),
            "feedback deleted": lambda: (clone / ITEM / "feedback" / "REVIEW_FEEDBACK.md").unlink(),
            "another item path": lambda: write(clone, f"{ITEM}/stray.txt", "x\n"),
            "plan-inputs extra": lambda: write(clone, f"{ITEM}/plan-inputs/EXTRA.md", "x\n"),
            "a symlink in the set": lambda: os.symlink("/etc/passwd", clone / ITEM / "feedback" / "ln"),
        }
        for label, act in cases.items():
            with self.subTest(label):
                snapshot = self.managed_snapshot()
                act()
                self.refused("carry_out_of_set", self.check, carry_set, block)
                self.assertEqual(self.managed_snapshot(), snapshot)
                git(clone, "checkout", "-q", "--", "src.txt")
                for rel in ("new.txt", f"{ITEM}/stray.txt", f"{ITEM}/feedback/EXTRA.md", f"{ITEM}/plan-inputs/EXTRA.md",
                            f"{ITEM}/feedback/ln"):
                    (clone / rel).unlink(missing_ok=True)
                write(clone, f"{ITEM}/feedback/REVIEW_FEEDBACK.md", "feedback\n")

    def test_carry_member_refused_at_carry_out_each_detail(self) -> None:
        carry_set, block, prepared = self.go_in()
        member = prepared.path / PLAN_MEMBERS[0]
        for detail, act in (
                ("kind", lambda: (member.unlink(), os.symlink("/etc/passwd", member))),
                ("bound", lambda: (member.unlink(), write(prepared.path, PLAN_MEMBERS[0], b"\0" * (8 * 1024 * 1024 + 1)))),
                ("armour", lambda: (member.unlink(), write(prepared.path, PLAN_MEMBERS[0],
                                                             "-----BEGIN PRIVATE KEY-----\nzzz\n"))),
                ("kind", lambda: (member.unlink(), os.mkfifo(member)))):
            with self.subTest(detail):
                act()
                error = self.refused("carry_member_refused", self.check, carry_set, block, detail=detail)
                self.assertNotIn("zzz", str(error.evidence) + str(error))
                member.unlink()
                write(prepared.path, PLAN_MEMBERS[0], "request\n")

    def test_a_hardlink_at_a_member_path_is_carried_as_its_content_only(self) -> None:
        carry_set, block, prepared = self.go_in()
        member = prepared.path / PLAN_MEMBERS[0]
        secret = self.tmp / "secret.txt"
        secret.write_text("outside\n")
        member.unlink()
        os.link(secret, member)
        plan = self.check(carry_set, block)
        carry.write_out(self.managed, self.layout, carry_set, block, plan)
        managed_member = self.root / PLAN_MEMBERS[0]
        self.assertEqual(managed_member.read_text(), "outside\n")
        self.assertEqual(managed_member.stat().st_nlink, 1)

    def test_carry_managed_changed_for_a_member_the_operator_changed_meanwhile(self) -> None:
        carry_set, block, prepared = self.go_in()
        self.edit(prepared, PLAN, "plan by the worker\n")
        write(self.root, PLAN, "plan by the operator\n")
        self.refused("carry_managed_changed", self.check, carry_set, block)
        self.assertEqual((self.root / PLAN).read_text(), "plan by the operator\n")

    def test_carry_managed_changed_for_the_managed_intent_to_add_set(self) -> None:
        carry_set, block, prepared = self.go_in()
        git(self.root, "reset", "-q", ARTIFACTS)
        self.refused("carry_managed_changed", self.check, carry_set, block)

    def test_an_edit_between_collection_and_the_baseline_is_refused_and_survives(self) -> None:
        """H4E3-I1 (round 2): the baseline is the collected bytes, so a member edited after collection is never
        accepted as the carried-in state and never overwritten."""
        carry_set = self.set_for()
        block = self.block(carry_set)
        real = carry.collect_in

        def collect_then_edit(*args, **kwargs):
            carried = real(*args, **kwargs)
            write(self.root, PLAN, "plan by the operator, after collection\n")
            return carried

        with mock.patch.object(carry, "collect_in", side_effect=collect_then_edit):
            self.refused("carry_managed_changed", carry.carry_in, self.managed, self.layout, carry_set, block,
                         persist=self.persist)
        self.assertEqual((self.root / PLAN).read_text(), "plan by the operator, after collection\n")
        self.assertFalse((self.layout.runtime_root / self.layout.rel("tree")).exists())

    def test_this_items_state_entry_changed_meanwhile_is_refused_when_the_worker_left_state_alone(self) -> None:
        """H4E3-I2 (round 2): the entry guard does not depend on the worker having changed the state file."""
        carry_set, block, prepared = self.go_in()
        self.edit(prepared, PLAN_MEMBERS[0], "request by the worker\n")
        doc = json.loads((self.root / STATE).read_text())
        doc["work_items"][WID]["state_revision"] = 7
        write(self.root, STATE, json.dumps(doc, indent=2) + "\n")
        self.refused("carry_managed_changed", self.check, carry_set, block)
        self.assertEqual((self.root / PLAN_MEMBERS[0]).read_text(), "request\n")

    def test_another_items_state_entry_changed_meanwhile_is_not_a_refusal(self) -> None:
        carry_set, block, prepared = self.go_in()
        self.edit(prepared, PLAN_MEMBERS[0], "request by the worker\n")
        doc = json.loads((self.root / STATE).read_text())
        doc["work_items"]["other"]["phase"] = "DONE"
        write(self.root, STATE, json.dumps(doc, indent=2) + "\n")
        self.assertEqual([w.rel for w in self.check(carry_set, block).writes], [PLAN_MEMBERS[0]])

    def _rename_into_absent_artifacts(self) -> None:
        git(self.root, "reset", "-q", ARTIFACTS)
        (self.root / ARTIFACTS).unlink()

    def test_a_staged_rename_from_outside_the_set_is_carry_foreign_dirt_at_carry_in(self) -> None:
        """H4E3-I3 (round 2): the source of a staged rename is a changed path too."""
        self._rename_into_absent_artifacts()
        git(self.root, "mv", "src.txt", ARTIFACTS)
        carry_set = self.set_for()
        error = self.refused("carry_foreign_dirt", carry.carry_in, self.managed, self.layout, carry_set,
                             self.block(carry_set), persist=self.persist)
        self.assertIn("src.txt", error.evidence["paths"])

    def test_a_staged_rename_from_outside_the_set_is_carry_out_of_set_at_carry_out(self) -> None:
        self._rename_into_absent_artifacts()
        carry_set, block, prepared = self.go_in()
        git(prepared.path, "mv", "src.txt", ARTIFACTS)
        error = self.refused("carry_out_of_set", self.check, carry_set, block)
        self.assertIn("src.txt", error.evidence["paths"])

    def test_carry_state_foreign_each_way(self) -> None:
        carry_set, block, prepared = self.go_in()
        cases = {
            "another item changed": lambda d: d["work_items"]["other"].update(phase="DONE"),
            "another item added": lambda d: d["work_items"].update(new={"work_item_id": "new"}),
            "another item removed": lambda d: d["work_items"].pop("other"),
            "a top-level key added": lambda d: d.update(extra=1),
            "active item moved": lambda d: d.update(active_work_item_id="other"),
            "this item removed": lambda d: d["work_items"].pop(WID),
        }
        original = (prepared.path / STATE).read_bytes()
        for label, mutate in cases.items():
            with self.subTest(label):
                self.edit_state(prepared, mutate)
                self.refused("carry_state_foreign", self.check, carry_set, block)
                write(prepared.path, STATE, original)
        write(prepared.path, STATE, b"not json")
        self.refused("carry_state_foreign", self.check, carry_set, block)
        self.assertEqual(self.state_calls, [])

    def test_the_carried_in_copy_of_the_state_is_checked_against_its_digest(self) -> None:
        carry_set, block, prepared = self.go_in()
        self.edit_state(prepared, lambda d: d["work_items"][WID].update(plan_revision=3))
        path = self.layout.runtime_root / self.layout.rel("carry", "state_in.json")
        path.write_text("{}")
        self.refused("carry_state_foreign", self.check, carry_set, block)
        path.unlink()
        self.refused("carry_state_foreign", self.check, carry_set, block)

    def test_a_gone_clone_is_carry_clone_gone(self) -> None:
        carry_set, block, prepared = self.go_in()
        carry.remove_carry(self.layout)
        self.refused("carry_clone_gone", self.check, carry_set, block)

    def test_the_implementation_role_checks_clean_and_does_not_require_the_head_to_stay(self) -> None:
        self.make_clean()
        carry_set, block, prepared = self.go_in(self.set_for("apply-implementation-review"))
        clone = prepared.path
        write(clone, "worker.txt", "w\n")
        git(clone, "add", "worker.txt")
        git(clone, "commit", "-q", "-m", "worker")
        self.edit(prepared, IMPL_MEMBERS[1], "summary revision 2\n")
        plan = self.check(carry_set, block)
        self.assertEqual([w.rel for w in plan.writes], [IMPL_MEMBERS[1]])
        write(clone, "dirty.txt", "x\n")
        self.refused("carry_out_of_set", self.check, carry_set, block)

    def test_an_implementation_worker_whose_generation_withdrew_current_is_refused_absent(self) -> None:
        self.make_clean()
        carry_set, block, prepared = self.go_in(self.set_for("apply-implementation-review"))
        (prepared.path / ITEM / "current").rename(prepared.path / ITEM / "current.rejected-abc")
        self.refused("carry_member_refused", self.check, carry_set, block, detail="absent")


class RecoveryTest(Case):
    def _worker_edits(self, prepared) -> None:
        self.edit(prepared, PLAN, "plan by the worker\n")
        self.edit(prepared, PLAN_MEMBERS[0], "new request\n")
        self.edit_state(prepared, lambda d: d["work_items"][WID].update(plan_revision=2))

    def recover(self, carry_set, block, **kw):
        return carry.recover(self.managed, self.layout, carry_set, block, **{"worker_started": True, **kw})

    def test_preparing_removes_the_clone_and_writes_nothing(self) -> None:
        carry_set, block, prepared = self.go_in()
        block["stage"] = "preparing"
        before = self.managed_snapshot()
        self.assertEqual(self.recover(carry_set, block, worker_started=False)["action"], "not_started")
        self.assertFalse(self.layout.worktree.exists())
        self.assertEqual(self.managed_snapshot(), before)

    def test_carried_in_with_no_worker_process_is_not_started(self) -> None:
        carry_set, block, prepared = self.go_in()
        self._worker_edits(prepared)
        before = self.managed_snapshot()
        self.assertEqual(self.recover(carry_set, block, worker_started=False)["action"], "not_started")
        self.assertFalse(self.layout.worktree.exists())
        self.assertEqual(self.managed_snapshot(), before)
        self.assertEqual(self.state_calls, [])

    def test_carried_in_with_a_worker_process_carries_out_from_the_kept_clone(self) -> None:
        carry_set, block, prepared = self.go_in()
        self._worker_edits(prepared)
        outcome = self.recover(carry_set, block)
        self.assertEqual(outcome["action"], "carried_out")
        self.assertEqual(sorted(outcome["written"]), sorted([PLAN, PLAN_MEMBERS[0]]))
        self.assertTrue(outcome["state_written"])
        self.assertEqual(block["stage"], "done")

    def test_worker_ran_carries_out_the_same_way(self) -> None:
        carry_set, block, prepared = self.go_in()
        block["stage"] = "worker_ran"
        self._worker_edits(prepared)
        self.assertEqual(self.recover(carry_set, block)["action"], "carried_out")

    def test_a_refusal_in_the_recovery_carry_out_writes_nothing(self) -> None:
        carry_set, block, prepared = self.go_in()
        self._worker_edits(prepared)
        self.edit(prepared, "src.txt", "changed\n")
        before = self.managed_snapshot()
        self.refused("carry_out_of_set", self.recover, carry_set, block)
        self.assertEqual(self.managed_snapshot(), before)

    def test_done_writes_nothing(self) -> None:
        carry_set, block, prepared = self.go_in()
        block["stage"] = "done"
        before = self.managed_snapshot()
        self.assertEqual(self.recover(carry_set, block)["action"], "done")
        self.assertEqual(self.managed_snapshot(), before)

    def test_out_is_re_run_at_every_member_boundary(self) -> None:
        for boundary in range(0, 3):
            with self.subTest(boundary=boundary):
                self.setUp()
                carry_set, block, prepared = self.go_in()
                self._worker_edits(prepared)
                plan = self.check(carry_set, block)
                self.assertEqual(len(plan.writes), 2)
                # the kill: the block says `out`, and `boundary` of the members were written
                carry.write_out(self.managed, self.layout, carry_set, dict(block, stage="out", writes={
                    w.rel: {"sha256": w.sha256, "mode": w.mode} for w in plan.writes}), carry.OutPlan(
                        plan.writes[:boundary], None))
                block["stage"] = "out"
                block["writes"] = {w.rel: {"sha256": w.sha256, "mode": w.mode} for w in plan.writes}
                block["state"] = True
                self.state_calls.clear()
                outcome = self.recover(carry_set, block)
                self.assertEqual(len(outcome["written"]), 2 - boundary)
                self.assertEqual((self.root / PLAN).read_text(), "plan by the worker\n")
                self.assertEqual((self.root / PLAN_MEMBERS[0]).read_text(), "new request\n")
                self.assertEqual(len(self.state_calls), 1)
                again = self.recover(carry_set, block)
                self.assertEqual(again["action"], "done")

    def test_out_with_a_member_at_neither_digest_is_carry_managed_changed(self) -> None:
        carry_set, block, prepared = self.go_in()
        self._worker_edits(prepared)
        plan = self.check(carry_set, block)
        block["stage"] = "out"
        block["writes"] = {w.rel: {"sha256": w.sha256, "mode": w.mode} for w in plan.writes}
        block["state"] = True
        write(self.root, PLAN, "someone else\n")
        self.refused("carry_managed_changed", self.recover, carry_set, block)
        self.assertEqual((self.root / PLAN).read_text(), "someone else\n")

    def test_out_with_this_items_state_entry_changed_meanwhile_is_refused_before_any_write(self) -> None:
        """H4E3-I4: recovery at stage `out` runs the entry guard too, with the worker's state file unchanged."""
        carry_set, block, prepared = self.go_in()
        self.edit(prepared, PLAN_MEMBERS[0], "request by the worker\n")
        plan = self.check(carry_set, block)
        self.assertIsNone(plan.state)
        block["stage"] = "out"
        block["writes"] = {w.rel: {"sha256": w.sha256, "mode": w.mode} for w in plan.writes}
        block["state"] = False
        doc = json.loads((self.root / STATE).read_text())
        doc["work_items"][WID]["state_revision"] = 7
        write(self.root, STATE, json.dumps(doc, indent=2) + "\n")
        self.refused("carry_managed_changed", self.recover, carry_set, block)
        self.assertEqual((self.root / PLAN_MEMBERS[0]).read_text(), "request\n")
        self.assertTrue(carry.prepared_from_block(block).path.is_dir())

    def test_out_with_another_items_state_entry_changed_is_not_a_refusal(self) -> None:
        carry_set, block, prepared = self.go_in()
        self.edit(prepared, PLAN_MEMBERS[0], "request by the worker\n")
        plan = self.check(carry_set, block)
        block["stage"] = "out"
        block["writes"] = {w.rel: {"sha256": w.sha256, "mode": w.mode} for w in plan.writes}
        block["state"] = False
        doc = json.loads((self.root / STATE).read_text())
        doc["work_items"]["other"]["phase"] = "DONE"
        write(self.root, STATE, json.dumps(doc, indent=2) + "\n")
        self.assertEqual(self.recover(carry_set, block)["action"], "carried_out")

    def test_out_with_the_clone_gone_is_carry_clone_gone_naming_the_unwritten_members(self) -> None:
        carry_set, block, prepared = self.go_in()
        self._worker_edits(prepared)
        plan = self.check(carry_set, block)
        block["stage"] = "out"
        block["writes"] = {w.rel: {"sha256": w.sha256, "mode": w.mode} for w in plan.writes}
        carry.remove_carry(self.layout)
        error = self.refused("carry_clone_gone", self.recover, carry_set, block)
        self.assertEqual(sorted(error.evidence["not_written"]), sorted([PLAN, PLAN_MEMBERS[0]]))
        self.assertEqual((self.root / PLAN).read_text(), "plan revised\n")

    def test_a_clone_gone_before_the_end_is_carry_clone_gone(self) -> None:
        carry_set, block, prepared = self.go_in()
        carry.remove_carry(self.layout)
        self.refused("carry_clone_gone", self.recover, carry_set, block)

    def test_a_state_already_carried_is_a_no_op_for_the_recorder(self) -> None:
        carry_set, block, prepared = self.go_in()
        self._worker_edits(prepared)
        self.recover(carry_set, block)
        self.assertEqual(len(self.state_calls), 1)


class CommitPendingTest(Case):
    def _result(self, **kw) -> review_recorder.RecorderResult:
        return review_recorder.RecorderResult(kw.pop("ok", True), kw.pop("cause", None), None, kw.pop("message", None),
                                              kw)

    def test_nothing_to_commit_keeps_the_pre_step_head_as_the_carry_base(self) -> None:
        with mock.patch.object(review_recorder, "commit_pending", return_value=self._result(committed=None)):
            self.assertEqual(carry.commit_pending(self.root, WID, self.head), self.head)

    def test_a_commit_on_top_of_the_pre_step_head_becomes_the_carry_base(self) -> None:
        def commit(root, wid, digests=None):
            write(self.root, "src.txt", "pending\n")
            git(self.root, "commit", "-q", "-am", "pending entry")
            return self._result(committed=out(self.root, "rev-parse", "HEAD"))
        with mock.patch.object(review_recorder, "commit_pending", side_effect=commit):
            base = carry.commit_pending(self.root, WID, self.head)
        self.assertEqual(base, out(self.root, "rev-parse", "HEAD"))
        self.assertNotEqual(base, self.head)

    def test_a_refusal_of_the_workflow_is_carry_commit_pending_failed(self) -> None:
        with mock.patch.object(review_recorder, "commit_pending",
                               return_value=self._result(ok=False, cause="recorder_commit_pending", message="no")):
            self.refused("carry_commit_pending_failed", carry.commit_pending, self.root, WID, self.head)

    def test_a_runner_error_is_carry_commit_pending_failed(self) -> None:
        from controller.errors import WorkflowQueryError
        with mock.patch.object(review_recorder, "commit_pending", side_effect=WorkflowQueryError("x")):
            self.refused("carry_commit_pending_failed", carry.commit_pending, self.root, WID, self.head)

    def test_a_commit_that_is_not_a_child_of_the_pre_step_head_is_branch_moved(self) -> None:
        write(self.root, "src.txt", "other\n")
        git(self.root, "commit", "-q", "-am", "someone else")
        moved = out(self.root, "rev-parse", "HEAD")

        def commit(root, wid, digests=None):
            write(self.root, "src.txt", "pending\n")
            git(self.root, "commit", "-q", "-am", "pending entry")
            return self._result(committed=out(self.root, "rev-parse", "HEAD"))
        with mock.patch.object(review_recorder, "commit_pending", side_effect=commit):
            with self.assertRaises(carry.CarryError) as caught:
                carry.commit_pending(self.root, WID, self.head)
        self.assertEqual(caught.exception.cause, "carry_commit_pending_failed")
        self.assertEqual(caught.exception.evidence["detail"], "branch_moved")
        self.assertNotEqual(moved, self.head)

    def test_the_carry_base_is_what_prepare_clones_and_a_moved_branch_still_refuses(self) -> None:
        self.make_clean()
        write(self.root, "src.txt", "pending\n")
        git(self.root, "commit", "-q", "-am", "pending entry")
        base = out(self.root, "rev-parse", "HEAD")
        carry_set = self.set_for("apply-implementation-review")
        block = carry.start_block(carry_set, pre_step_head=self.head, base_commit=base)
        prepared = carry.carry_in(self.managed, self.layout, carry_set, block)
        self.assertEqual(out(prepared.path, "rev-parse", "HEAD"), base)
        self.assertEqual(block["base_commit"], base)
        self.assertEqual(block["pre_step_head"], self.head)
        carry.remove_carry(self.layout)
        write(self.root, "src.txt", "moved\n")
        git(self.root, "commit", "-q", "-am", "unrelated move")
        stale = carry.start_block(carry_set, pre_step_head=self.head, base_commit=base)
        with self.assertRaises(tree.CodexTreeError) as caught:
            carry.carry_in(self.managed, self.layout, carry_set, stale)
        self.assertEqual(caught.exception.cause, "managed_branch_moved")


class WorkflowFixtureCarryTest(unittest.TestCase):
    """The carried plan clone is accepted by the Workflow's own plan-stage generator and verifier, on a target the
    Workflow's own seed built (its plan files and state uncommitted, its bundle generated and bound)."""

    def test_the_carried_plan_clone_generates_and_verifies_a_plan_bundle(self) -> None:
        from tests import test_review_recorder as recorder
        area = Path(tempfile.mkdtemp(prefix="carry-wf-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(area, ignore_errors=True))
        root = area / "target"
        recorder.build_target(root, "plan")
        entry = recorder._entry(root)
        managed = SimpleNamespace(root=root)
        layout = ws.WorkerLayout(area / "runtime", "job1")
        dirty = carry.dirty_paths(root)
        carry_set = carry.compute(root, recorder.WID, "apply-plan-review")
        head = out(root, "rev-parse", "HEAD")
        block = carry.start_block(carry_set, pre_step_head=head, base_commit=head)
        try:
            prepared = carry.carry_in(managed, layout, carry_set, block)
        except carry.CarryError as exc:  # the seed leaves paths outside the set: say which
            self.fail(f"{exc} (dirty: {dirty})")
        clone = prepared.path
        result = subprocess.run(["./scripts/prepare-ai-review.sh", entry["base_commit"], "plan", recorder.WID], cwd=clone,
                                capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        fixtures.run_workflow_python(
            clone, f"ws.verify_plan_review_bundle(Path.cwd(), {recorder.WID!r})\n")
        plan = carry.check_out(managed, layout, carry_set, block)
        self.assertEqual(plan.state, None)
        self.assertEqual(plan.writes, ())


class ZzCoverageTest(unittest.TestCase):
    def test_every_cause_has_a_case(self) -> None:
        """Static, because the sharded run splits a module's tests across shards and a runtime ``COVERED`` set is
        partial there: every cause is named in a test module that drives it."""
        tests_dir = Path(__file__).resolve().parent
        text = "".join((tests_dir / name).read_text(encoding="utf-8")
                       for name in ("test_codex_carry.py", "test_job_codex_apply.py", "test_codex_apply_safety.py"))
        missing = [cause for cause in carry.CAUSES if f'"{cause}"' not in text and f"'{cause}'" not in text]
        self.assertEqual(missing, [], "a cause in codex_carry.CAUSES has no test case")
        self.assertEqual(len(carry.CAUSES), len(set(carry.CAUSES)))
        self.assertTrue(set(carry.MEMBER_DETAILS) >= {"name", "kind", "git", "gitlink", "escape", "bound", "armour",
                                                      "absent"})

    def test_the_stages_are_in_order(self) -> None:
        self.assertEqual(carry.STAGES_IN_ORDER, ("preparing", "carried_in", "worker_ran", "out", "done"))


if __name__ == "__main__":
    unittest.main()
