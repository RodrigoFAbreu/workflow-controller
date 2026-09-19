"""Tests for the read-only target-repository Workflow state reader
(capability 2, ``controller.target_state``).

``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``'s CP3 section names these
cases: a fixture-built repo with a well-formed ``WORKFLOW_STATE.json``
reads cleanly; a missing state file, invalid JSON, a non-object payload,
an absent/wrong ``schema_version``, a ``work_items`` key/id mismatch, an
``active_work_item_id`` naming an absent item, and an unknown ``phase``
each refuse with their own named error; ``active_work_item_id`` selection
and its ``--work-item``/single-non-terminal/``NoWorkItemYet``/ambiguous
fallbacks; ``registry_complete``'s three outcomes (``None``, ``True``/
``False``, ``MalformedTargetRegistryError``); ``incomplete_children``'s
reverse lookup; the known-phase set's two-directional equality against the
installed reference release's own ``KNOWN_PHASES`` (revision 64: twenty
members, including the ``AMENDING_PLAN``/``AWAITING_LOCAL_IMPLEMENTATION_
REVIEW``/``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` widening); and
the read-only AST-scan proof that this module can never write.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import target_state  # noqa: E402
from controller.target_state import NoWorkItemYet  # noqa: E402
from controller.errors import (  # noqa: E402
    AmbiguousWorkItemError,
    MalformedTargetRegistryError,
    MalformedWorkflowStateError,
    MissingWorkflowStateError,
    UnknownPhaseError,
)
from tests import fixtures  # noqa: E402

_TARGET_STATE_SOURCE = REPO_ROOT / "controller" / "target_state.py"


def _minimal_work_item(**overrides) -> dict:
    base = {
        "work_item_id": "wi-1",
        "work_item_type": "product",
        "work_item_kind": "product",
        "governing_workflow_version": "2.1",
        "phase": "IMPLEMENTING",
        "plan_revision": 3,
        "implementation_revision": None,
        "functional_review_round": None,
        "base_commit": "deadbeef",
        "reviewed_implementation_head": None,
        "current_checkpoint_id": "CP2",
        "last_completed_checkpoint_id": "CP1",
        "checkpoints": {
            "CP1": {"status": "COMPLETE", "start_commit": "aaa"},
            "CP2": {"status": "IN_PROGRESS", "start_commit": "bbb"},
        },
        "current_bundle_id": None,
        "plan_approval": {"status": "CURRENT"},
        "technical_approval": None,
        "functional_acceptance_status": None,
        "plan_review_stages": {},
        "parent_work_item_id": None,
        "state_revision": 7,
    }
    base.update(overrides)
    return base


def _state(work_items: dict, *, active_work_item_id=None, schema_version=1) -> dict:
    return {
        "schema_version": schema_version,
        "active_work_item_id": active_work_item_id,
        "work_items": work_items,
    }


class ReadHappyPathTest(unittest.TestCase):
    def test_well_formed_state_reads_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item()
            fixtures.write_workflow_state(root, _state({"wi-1": wi}, active_work_item_id="wi-1"))
            fixtures.write_workflow_config(
                root, {"schema_version": 1, "default_workflow_version": "2.1",
                       "supported_versions": ["1", "2.1"]},
            )
            managed_repo = fixtures.build_target_managed_repository(root)
            snapshot = target_state.read(managed_repo)
            self.assertEqual(snapshot.schema_version, 1)
            self.assertEqual(snapshot.active_work_item_id, "wi-1")
            self.assertEqual(snapshot.default_workflow_version, "2.1")
            self.assertEqual(snapshot.raw_path, root / "docs/ai-workflow/WORKFLOW_STATE.json")
            view = snapshot.work_items["wi-1"]
            self.assertEqual(view.phase, "IMPLEMENTING")
            self.assertEqual(view.current_checkpoint_id, "CP2")
            self.assertEqual(view.last_completed_checkpoint_id, "CP1")
            self.assertEqual(view.state_revision, 7)
            self.assertIsNone(view.registry_complete)  # no registry_path declared
            self.assertEqual(view.incomplete_children, ())

    def test_missing_workflow_config_yields_none_default_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixtures.write_workflow_state(root, _state({}, active_work_item_id=None))
            managed_repo = fixtures.build_target_managed_repository(root)
            snapshot = target_state.read(managed_repo)
            self.assertIsNone(snapshot.default_workflow_version)

    def test_real_repository_own_workflow_state_reads_cleanly(self) -> None:
        """This repository is itself a real, currently-managed Workflow
        repository -- its own WORKFLOW_STATE.json should always read
        without refusal."""
        managed_repo = fixtures.build_target_managed_repository(REPO_ROOT)
        snapshot = target_state.read(managed_repo)
        self.assertEqual(snapshot.schema_version, 1)
        self.assertIn(snapshot.active_work_item_id, snapshot.work_items)


class MissingStateTest(unittest.TestCase):
    def test_no_state_file_refuses_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.mkdir(exist_ok=True)
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MissingWorkflowStateError) as ctx:
                target_state.read(managed_repo)
            self.assertEqual(ctx.exception.evidence["root"], str(root))


class MalformedStateTest(unittest.TestCase):
    def test_invalid_json_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixtures.write_workflow_state_raw(root, "{not valid json at all")
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedWorkflowStateError):
                target_state.read(managed_repo)

    def test_non_object_json_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixtures.write_workflow_state_raw(root, "[1, 2, 3]")
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedWorkflowStateError):
                target_state.read(managed_repo)

    def test_missing_schema_version_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = _state({})
            del state["schema_version"]
            fixtures.write_workflow_state(root, state)
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedWorkflowStateError):
                target_state.read(managed_repo)

    def test_wrong_schema_version_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixtures.write_workflow_state(root, _state({}, schema_version=2))
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedWorkflowStateError) as ctx:
                target_state.read(managed_repo)
            self.assertEqual(ctx.exception.evidence["schema_version"], 2)

    def test_work_item_key_id_mismatch_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(work_item_id="not-the-key")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedWorkflowStateError) as ctx:
                target_state.read(managed_repo)
            self.assertEqual(ctx.exception.evidence["key"], "wi-1")
            self.assertEqual(ctx.exception.evidence["declared_work_item_id"], "not-the-key")

    def test_active_work_item_id_naming_absent_item_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item()
            fixtures.write_workflow_state(
                root, _state({"wi-1": wi}, active_work_item_id="does-not-exist"),
            )
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedWorkflowStateError) as ctx:
                target_state.read(managed_repo)
            self.assertEqual(ctx.exception.evidence["active_work_item_id"], "does-not-exist")

    def test_work_items_not_an_object_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = _state({})
            state["work_items"] = ["not", "an", "object"]
            fixtures.write_workflow_state(root, state)
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedWorkflowStateError):
                target_state.read(managed_repo)


class UnknownPhaseTest(unittest.TestCase):
    def test_phase_outside_known_set_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(phase="SOME_MADE_UP_PHASE")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}, active_work_item_id="wi-1"))
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(UnknownPhaseError) as ctx:
                target_state.read(managed_repo)
            self.assertEqual(ctx.exception.evidence["phase"], "SOME_MADE_UP_PHASE")

    def test_legacy_ready_is_a_known_non_terminal_phase(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(phase="LEGACY_READY")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}, active_work_item_id="wi-1"))
            managed_repo = fixtures.build_target_managed_repository(root)
            snapshot = target_state.read(managed_repo)
            self.assertEqual(snapshot.work_items["wi-1"].phase, "LEGACY_READY")
            self.assertNotIn("LEGACY_READY", target_state.TERMINAL_PHASES)


class KnownPhaseSetEqualityTest(unittest.TestCase):
    def test_equals_frozen_workflow_v2_3_1_known_phases(self) -> None:
        scripts_dir = REPO_ROOT / "scripts"
        sys.path.insert(0, str(scripts_dir))
        import workflow_state as real_workflow_state  # noqa: PLC0415

        self.assertEqual(target_state.KNOWN_PHASES, real_workflow_state.KNOWN_PHASES)
        self.assertEqual(len(target_state.KNOWN_PHASES), 20)

    def test_terminal_phases_equal_frozen_workflow_v2_3_1(self) -> None:
        scripts_dir = REPO_ROOT / "scripts"
        sys.path.insert(0, str(scripts_dir))
        import workflow_state as real_workflow_state  # noqa: PLC0415

        self.assertEqual(target_state.TERMINAL_PHASES, real_workflow_state.TERMINAL_PHASES)


class SelectWorkItemTest(unittest.TestCase):
    def _snapshot(self, work_items: dict, *, active_work_item_id=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixtures.write_workflow_state(
                root, _state(work_items, active_work_item_id=active_work_item_id),
            )
            managed_repo = fixtures.build_target_managed_repository(root)
            return target_state.read(managed_repo)

    def test_explicit_override_wins_over_active(self) -> None:
        snapshot = self._snapshot(
            {"a": _minimal_work_item(work_item_id="a"), "b": _minimal_work_item(work_item_id="b")},
            active_work_item_id="a",
        )
        view = target_state.select_work_item(snapshot, work_item_id="b")
        self.assertEqual(view.work_item_id, "b")

    def test_explicit_override_absent_refuses_ambiguous(self) -> None:
        snapshot = self._snapshot({"a": _minimal_work_item(work_item_id="a")})
        with self.assertRaises(AmbiguousWorkItemError) as ctx:
            target_state.select_work_item(snapshot, work_item_id="does-not-exist")
        self.assertEqual(ctx.exception.evidence["requested"], "does-not-exist")
        self.assertEqual(ctx.exception.evidence["candidates"], ["a"])

    def test_falls_back_to_active_work_item_id(self) -> None:
        snapshot = self._snapshot(
            {"a": _minimal_work_item(work_item_id="a"), "b": _minimal_work_item(work_item_id="b")},
            active_work_item_id="b",
        )
        view = target_state.select_work_item(snapshot)
        self.assertEqual(view.work_item_id, "b")

    def test_falls_back_to_sole_non_terminal_item(self) -> None:
        snapshot = self._snapshot({
            "a": _minimal_work_item(work_item_id="a", phase="MILESTONE_COMPLETE"),
            "b": _minimal_work_item(work_item_id="b", phase="IMPLEMENTING"),
        })
        view = target_state.select_work_item(snapshot)
        self.assertEqual(view.work_item_id, "b")

    def test_multiple_non_terminal_candidates_refuses_ambiguous(self) -> None:
        snapshot = self._snapshot({
            "a": _minimal_work_item(work_item_id="a", phase="IMPLEMENTING"),
            "b": _minimal_work_item(work_item_id="b", phase="PLANNING"),
        })
        with self.assertRaises(AmbiguousWorkItemError) as ctx:
            target_state.select_work_item(snapshot)
        self.assertEqual(ctx.exception.evidence["candidates"], ["a", "b"])

    def test_zero_non_terminal_candidates_returns_no_work_item_yet(self) -> None:
        """Revision 63 (B2, REQ-40): zero non-terminal candidates with no
        explicit id is not ambiguous -- there is no candidate to be
        ambiguous among -- so this returns the NoWorkItemYet sentinel
        rather than raising."""
        snapshot = self._snapshot({
            "a": _minimal_work_item(work_item_id="a", phase="MILESTONE_COMPLETE"),
        })
        self.assertIs(target_state.select_work_item(snapshot), NoWorkItemYet)

    def test_zero_work_items_at_all_returns_no_work_item_yet(self) -> None:
        """The bootstrap case a fresh, disposable managed repository starts
        in: no work_items entries at all."""
        snapshot = self._snapshot({})
        self.assertIs(target_state.select_work_item(snapshot), NoWorkItemYet)

    def test_explicit_work_item_id_absent_still_refuses_ambiguous_even_with_zero_candidates(
        self,
    ) -> None:
        """An explicit --work-item naming an id absent from work_items is
        never a bootstrap trigger, even when the snapshot has zero
        non-terminal work items -- the Controller never treats an
        operator's explicit, wrong name as an invitation to invent one."""
        snapshot = self._snapshot({})
        with self.assertRaises(AmbiguousWorkItemError) as ctx:
            target_state.select_work_item(snapshot, work_item_id="does-not-exist")
        self.assertEqual(ctx.exception.evidence["requested"], "does-not-exist")


class NoPhaseSentinelTest(unittest.TestCase):
    """revision 64 (round 63's B2): NO_PHASE is the single, is-comparable,
    never-None sentinel; NO_PHASE_WIRE is its one reserved durable form,
    chosen so it can never collide with a real Workflow phase name."""

    def test_no_phase_is_not_a_known_phase_or_none(self) -> None:
        self.assertNotIn(target_state.NO_PHASE, target_state.KNOWN_PHASES)
        self.assertIsNotNone(target_state.NO_PHASE)

    def test_no_phase_wire_is_the_reserved_literal_and_not_a_known_phase(self) -> None:
        self.assertEqual(target_state.NO_PHASE_WIRE, "__NO_PHASE__")
        self.assertNotIn(target_state.NO_PHASE_WIRE, target_state.KNOWN_PHASES)

    def test_no_work_item_yet_is_a_singleton_and_not_a_work_item_view(self) -> None:
        self.assertIs(NoWorkItemYet, target_state.NoWorkItemYet)
        self.assertNotIsInstance(NoWorkItemYet, target_state.WorkItemView)


class IncompleteChildrenTest(unittest.TestCase):
    def test_reverse_lookup_finds_non_terminal_children_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parent = _minimal_work_item(work_item_id="parent")
            child_open = _minimal_work_item(
                work_item_id="child-open", parent_work_item_id="parent", phase="IMPLEMENTING",
            )
            child_done = _minimal_work_item(
                work_item_id="child-done", parent_work_item_id="parent",
                phase="MILESTONE_COMPLETE",
            )
            fixtures.write_workflow_state(root, _state({
                "parent": parent, "child-open": child_open, "child-done": child_done,
            }))
            managed_repo = fixtures.build_target_managed_repository(root)
            snapshot = target_state.read(managed_repo)
            self.assertEqual(snapshot.work_items["parent"].incomplete_children, ("child-open",))
            self.assertEqual(snapshot.work_items["child-open"].incomplete_children, ())


class RegistryCompleteTest(unittest.TestCase):
    def test_no_registry_path_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item()
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            managed_repo = fixtures.build_target_managed_repository(root)
            snapshot = target_state.read(managed_repo)
            self.assertIsNone(snapshot.work_items["wi-1"].registry_complete)

    def test_all_checkpoints_complete_is_true(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(
                registry_path="registry.json",
                checkpoints={
                    "CP1": {"status": "COMPLETE", "start_commit": "a"},
                    "CP2": {"status": "COMPLETE", "start_commit": "b"},
                },
            )
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            fixtures.write_target_registry(root, "registry.json", {
                "work_item_id": "wi-1",
                "checkpoints": [{"id": "CP1"}, {"id": "CP2"}],
            })
            managed_repo = fixtures.build_target_managed_repository(root)
            snapshot = target_state.read(managed_repo)
            self.assertTrue(snapshot.work_items["wi-1"].registry_complete)

    def test_outstanding_checkpoint_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(
                registry_path="registry.json",
                checkpoints={"CP1": {"status": "COMPLETE", "start_commit": "a"}},
            )
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            fixtures.write_target_registry(root, "registry.json", {
                "work_item_id": "wi-1",
                "checkpoints": [{"id": "CP1"}, {"id": "CP2"}],
            })
            managed_repo = fixtures.build_target_managed_repository(root)
            snapshot = target_state.read(managed_repo)
            self.assertFalse(snapshot.work_items["wi-1"].registry_complete)

    def test_missing_registry_file_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(registry_path="does-not-exist.json")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedTargetRegistryError):
                target_state.read(managed_repo)

    def test_unparseable_registry_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(registry_path="registry.json")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            (root / "registry.json").write_text("{not valid json")
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedTargetRegistryError):
                target_state.read(managed_repo)

    def test_cross_linked_registry_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(registry_path="registry.json")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            fixtures.write_target_registry(root, "registry.json", {
                "work_item_id": "some-other-work-item",
                "checkpoints": [{"id": "CP1"}],
            })
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedTargetRegistryError) as ctx:
                target_state.read(managed_repo)
            self.assertEqual(ctx.exception.evidence["declared_work_item_id"], "some-other-work-item")

    def test_registry_path_escaping_repository_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            wi = _minimal_work_item(registry_path="../outside.json")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            (Path(td) / "outside.json").write_text(json.dumps({
                "work_item_id": "wi-1", "checkpoints": [{"id": "CP1"}],
            }))
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedTargetRegistryError):
                target_state.read(managed_repo)

    def test_empty_checkpoints_array_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wi = _minimal_work_item(registry_path="registry.json")
            fixtures.write_workflow_state(root, _state({"wi-1": wi}))
            fixtures.write_target_registry(root, "registry.json", {
                "work_item_id": "wi-1", "checkpoints": [],
            })
            managed_repo = fixtures.build_target_managed_repository(root)
            with self.assertRaises(MalformedTargetRegistryError):
                target_state.read(managed_repo)

    def test_this_repository_own_registry_reads_as_a_boolean(self) -> None:
        """Integration case: this repository's own real registry file for
        its active work item, read the same way a real inspection would."""
        managed_repo = fixtures.build_target_managed_repository(REPO_ROOT)
        snapshot = target_state.read(managed_repo)
        active = target_state.select_work_item(snapshot)
        self.assertIsInstance(active.registry_complete, bool)


class ReadOnlySourceScanTest(unittest.TestCase):
    """The structural proof CP3's plan section requires: `target_state.py`
    can never write. AST-walks the *live* module source -- a later edit
    that introduces a write is caught here directly."""

    _WRITE_CALL_SUFFIXES = {
        "write_text", "write_bytes", "replace", "rename", "remove",
        "copy", "copy2", "copyfile", "copytree", "move", "rmtree",
    }
    _WRITE_MODE_CHARS = set("wax+")

    @staticmethod
    def _dotted_name(node: ast.AST):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = ReadOnlySourceScanTest._dotted_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return None

    def test_no_writing_call_anywhere_in_the_module(self) -> None:
        tree = ast.parse(_TARGET_STATE_SOURCE.read_text(), filename=str(_TARGET_STATE_SOURCE))
        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = self._dotted_name(node.func)
            if callee is None:
                continue
            attr = callee.rsplit(".", 1)[-1]

            if attr == "open" or callee == "open":
                args = list(node.args)
                mode_arg = args[1] if len(args) > 1 else None
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode_arg = kw.value
                if mode_arg is None:
                    continue  # default mode "r" -- not a violation
                if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                    if self._WRITE_MODE_CHARS & set(mode_arg.value):
                        violations.append(f"line {node.lineno}: open(..., mode={mode_arg.value!r})")
                else:
                    violations.append(
                        f"line {node.lineno}: open(...) with a non-literal mode argument"
                    )
                continue

            if attr in self._WRITE_CALL_SUFFIXES:
                violations.append(f"line {node.lineno}: {callee}(...)")

        self.assertEqual(violations, [])

    def test_no_write_function_is_defined(self) -> None:
        tree = ast.parse(_TARGET_STATE_SOURCE.read_text(), filename=str(_TARGET_STATE_SOURCE))
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in defined:
            self.assertNotIn("write", name.lower())

    def test_scanner_flags_a_synthetic_write_call(self) -> None:
        source = "def f():\n    Path('x').write_text('y')\n"
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = self._dotted_name(node.func)
                if callee and callee.rsplit(".", 1)[-1] in self._WRITE_CALL_SUFFIXES:
                    found = True
        self.assertTrue(found)


if __name__ == "__main__":
    unittest.main()
