"""``controller.protocol_decision`` (workflow-controller-orchestration-protocol-v1
CP3, plan Design C): a decision from the Workflow's own ``next-action``, the
``PROTOCOL_ACTIONS`` table, the two new routing roles, the rendered worker
command, the committed-state gate and the static I8 check.

The equivalence comparison against 1.6.0 (plan C.7, differences D1-D7) is
``tests/test_protocol_equivalence.py``.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, decision, job, managed_repo, protocol, protocol_decision, routing, target_state, worker  # noqa: E402
from controller.errors import WorkflowProtocolFailedError  # noqa: E402
from controller.identity import SOURCE_KIND_COMMIT, ControllerIdentity  # noqa: E402
from tests import fixtures  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
CONTROLLER_DIR = Path(__file__).resolve().parent.parent / "controller"

IDENTITY = ControllerIdentity(
    generation=7, source_root=Path("/fake/source"), origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_COMMIT, source_commit="a" * 40, tree_digest="b" * 64, generation_source="head",
    pinned_at="2024-01-01T00:00:00Z", version="1.7.0",
)

#: Every ``claude`` flag that would resume, continue or fork an earlier session.
SESSION_REUSE_FLAGS = frozenset({"--resume", "-r", "--continue", "-c", "--fork-session", "--session-id"})

#: What ``describe`` reports for the vendored 2.7.0 release.
TARGET_PROTOCOL = {"major": 1, "version": "1.0", "release": "2.7.0"}


def _action(action_id: str, work_item: str | None = "wi-1", *, role: str = "planner", user_only: bool = False,
            invocation: str | None = "", arguments: dict | None = None) -> dict:
    args = arguments if arguments is not None else ({} if work_item is None else {"work_item_id": work_item})
    command = protocol_decision.PROTOCOL_ACTIONS.get(action_id)
    if invocation == "":
        invocation = (f"/{command.command}" + ("" if work_item is None or action_id == "plan.start"
                                              else f" {work_item}")) if command else None
    return {"id": action_id, "arguments": args, "invocation": invocation,
            "worker": {"role": role, "fresh_session": False, "independent_of": [], "user_only": user_only},
            "allowed_results": ["progress", "gate_reached", "no_progress"]}


def _result(*, row: str = "7", disposition: str = "automatic", action: dict | None = None,
            alternatives: list | None = None, phase: str | None = "PLANNING", work_item: str = "wi-1",
            satisfied_by: str | None = None, code: str = "some_reason", text: str = "the reason",
            remedy: str | None = None) -> dict:
    basis = None if phase is None else {
        "checkpoints": {}, "head": "1" * 40, "phase": phase, "state_identity": "2" * 64, "state_revision": 1,
        "work_item_id": work_item}
    return {"action": action, "alternatives": alternatives or [], "basis": basis, "disposition": disposition,
            "reason": {"code": code, "remedy": remedy, "text": text}, "row": row, "satisfied_by": satisfied_by,
            "snapshot": {"governing_workflow_version": "2.2"} if phase else {"work_item_ids": []}}


def _stub(root: Path) -> SimpleNamespace:
    return SimpleNamespace(root=root, target_protocol=TARGET_PROTOCOL, script_digests=fixtures.admitted_script_digests(root))


def _item(work_item_id: str = "wi-1", phase: str = "PLANNING") -> SimpleNamespace:
    return SimpleNamespace(work_item_id=work_item_id, phase=phase, checkpoints={})


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "target"
        self.root.mkdir()
        self.repo = _stub(self.root)

    def decide(self, result: dict, *, item=None, base: str | None = None) -> decision.Decision:
        item = _item(phase=(result["basis"] or {}).get("phase", "PLANNING")) if item is None else item
        return protocol_decision.from_answer(self.repo, item, protocol.parse_decision(result), base=base)


class DispositionTest(_Case):
    def test_an_automatic_known_action_launches_its_rendered_command(self) -> None:
        got = self.decide(_result(action=_action("plan.author")))
        self.assertTrue(got.automatic)
        self.assertEqual((got.gate, got.declined), (None, False))
        self.assertEqual(got.action.command, "/milestone-plan wi-1")
        self.assertIsNone(got.action.task_addendum)
        self.assertEqual(got.observed_phase, "PLANNING")
        self.assertEqual(got.protocol.route, "milestone-plan")
        self.assertEqual((got.protocol.row, got.protocol.disposition, got.protocol.action_id), ("7", "automatic", "plan.author"))
        self.assertEqual(got.protocol.release, "2.7.0")
        self.assertEqual(got.protocol.state_identity, "2" * 64)

    def test_the_decision_document_is_the_result_as_received(self) -> None:
        result = _result(action=_action("plan.author"))
        got = self.decide(result)
        self.assertEqual(json.dumps(got.protocol.document, sort_keys=True), json.dumps(result, sort_keys=True))

    def test_human_and_external_gates_are_gates_that_launch_nothing(self) -> None:
        for disposition, action in (
            ("human_gate", _action("plan.approve", role="user", user_only=True, invocation="/approve-review plan wi-1")),
            ("external_gate", _action("plan.review.external", role="external", invocation=None)),
        ):
            with self.subTest(disposition=disposition):
                got = self.decide(_result(disposition=disposition, action=action, text="a person is needed",
                                          remedy="do the thing", satisfied_by="external_review"))
                self.assertFalse(got.automatic)
                self.assertFalse(got.declined)
                self.assertIsNone(got.action)
                self.assertIn("a person is needed", got.gate.what_is_required)
                self.assertIn("do the thing", got.gate.what_is_required)
                self.assertIn("external_review", got.gate.what_is_required)
        self.assertEqual(self.decide(_result(disposition="human_gate", action=_action(
            "plan.approve", role="user", user_only=True, invocation="/approve-review plan wi-1"))
        ).gate.safe_resume_command, "/approve-review plan wi-1")

    def test_an_external_gate_with_an_unknown_satisfied_by_is_a_gate(self) -> None:
        got = self.decide(_result(disposition="external_gate", action=_action(
            "plan.review.external", role="external", invocation=None), satisfied_by="a_future_evidence_kind"))
        self.assertIsNotNone(got.gate)
        self.assertFalse(got.automatic)

    def test_a_gate_with_no_invocation_resumes_through_its_first_alternative_then_explain(self) -> None:
        alternative = _action("functional.review.advisory", role="independent_reviewer",
                              invocation="/review-functional wi-1")
        got = self.decide(_result(disposition="external_gate", action=_action(
            "plan.review.external", role="external", invocation=None), alternatives=[alternative]))
        self.assertEqual(got.gate.safe_resume_command, "/review-functional wi-1")
        self.assertIn("alternatives: functional.review.advisory (/review-functional wi-1)", got.gate.what_is_required)
        bare = self.decide(_result(disposition="external_gate", action=_action(
            "plan.review.external", role="external", invocation=None)))
        self.assertIn("workflow-controller --work-item wi-1 explain", bare.gate.safe_resume_command)

    def test_blocked_is_a_gate_naming_the_reason_code_text_remedy_and_alternatives(self) -> None:
        alternative = _action("plan.author", invocation="/milestone-plan wi-1")
        got = self.decide(_result(row="4", disposition="blocked", code="phase_not_legal", text="not legal here",
                                  remedy="amend the plan", alternatives=[alternative]))
        self.assertFalse(got.automatic)
        self.assertIsNone(got.action)
        text = got.gate.what_is_required
        for part in ("phase_not_legal", "not legal here", "amend the plan", "plan.author (/milestone-plan wi-1)"):
            self.assertIn(part, text)
        self.assertEqual(got.protocol.alternatives, ("/milestone-plan wi-1",))

    def test_a_blocked_decision_never_launches_its_alternative(self) -> None:
        got = self.decide(_result(disposition="blocked", alternatives=[_action("plan.author")]))
        self.assertFalse(got.automatic)
        self.assertIsNone(got.action)

    def test_complete_is_the_existing_no_action_outcome(self) -> None:
        got = self.decide(_result(row="40", disposition="complete", phase="MILESTONE_COMPLETE", code="milestone_complete"))
        self.assertEqual((got.action, got.automatic, got.gate, got.declined), (None, False, None, False))
        self.assertEqual(got.observed_phase, "MILESTONE_COMPLETE")

    def test_validation_and_any_unknown_disposition_are_blocked(self) -> None:
        for disposition in ("validation", "a_future_disposition"):
            with self.subTest(disposition=disposition):
                got = self.decide(_result(disposition=disposition, action=_action("plan.author")))
                self.assertFalse(got.automatic)
                self.assertIsNone(got.action)
                self.assertIn(protocol_decision.UNKNOWN_DISPOSITION, got.gate.what_is_required)
                self.assertIn(disposition, got.gate.what_is_required)

    def test_an_answer_for_another_work_item_is_a_protocol_failure(self) -> None:
        with self.assertRaises(WorkflowProtocolFailedError) as raised:
            self.decide(_result(work_item="other", action=_action("plan.author", "other")), item=_item("wi-1"))
        self.assertEqual(raised.exception.evidence["reason"], "protocol_decision_invalid")
        with self.assertRaises(WorkflowProtocolFailedError):
            self.decide(_result(phase=None, action=_action("plan.start", None)), item=_item("wi-1"))

    def test_an_action_naming_another_work_item_is_a_protocol_failure(self) -> None:
        with self.assertRaises(WorkflowProtocolFailedError):
            self.decide(_result(action=_action("plan.author", "wi-1", arguments={"work_item_id": "other"})))


class ActionTableTest(_Case):
    def test_the_table_has_the_twelve_automatic_ids(self) -> None:
        self.assertEqual(set(protocol_decision.PROTOCOL_ACTIONS), {
            "plan.start", "plan.author", "plan.apply_review", "plan.review.local", "plan.record_external",
            "implementation.checkpoint", "implementation.self_review", "implementation.review.local",
            "implementation.record_external", "implementation.apply_review", "functional.prepare",
            "functional.apply_findings"})

    def test_every_id_launches_its_command_with_its_route(self) -> None:
        roles = {"plan.start": "planner", "plan.apply_review": "applier", "plan.review.local": "independent_reviewer",
                 "implementation.self_review": "self_reviewer", "implementation.review.local": "independent_reviewer",
                 "implementation.apply_review": "applier", "plan.record_external": "applier",
                 "implementation.record_external": "applier", "functional.apply_findings": "applier",
                 "implementation.checkpoint": "implementer", "functional.prepare": "implementer"}
        for action_id, entry in protocol_decision.PROTOCOL_ACTIONS.items():
            with self.subTest(action=action_id):
                item = None if action_id == "plan.start" else "wi-1"
                answer = _result(action=_action(action_id, item, role=roles.get(action_id, "planner")),
                                 phase=None if item is None else "IMPLEMENTING")
                got = self.decide(answer, item=target_state.NoWorkItemYet if item is None else _item(phase="PLANNING"))
                self.assertTrue(got.automatic, got.gate)
                self.assertEqual(got.action.command, f"/{entry.command}" + ("" if item is None else " wi-1"))
                self.assertEqual(got.protocol.route, entry.route)

    def test_the_table_agrees_with_what_describe_lists_and_names_no_user_only_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "target"
            fixtures.seed_workflow_item(root, "2.7.0", "route", work_item_id="demo")
            listed = set(protocol.describe(root).capabilities["action_ids"])
        self.assertLessEqual(set(protocol_decision.PROTOCOL_ACTIONS), listed)
        scripts = fixtures.workflow_release_tree("2.7.0") / "scripts" / "workflow_protocol.py"
        catalogue = _catalogue_actions(scripts)
        user_only = {action_id for action_id, spec in catalogue.items() if spec["user_only"]}
        self.assertTrue(user_only)
        self.assertFalse(user_only & set(protocol_decision.PROTOCOL_ACTIONS))
        for action_id in protocol_decision.PROTOCOL_ACTIONS:
            self.assertEqual(catalogue[action_id]["command"], protocol_decision.PROTOCOL_ACTIONS[action_id].command)
            self.assertIn(catalogue[action_id]["role"], protocol_decision.AUTOMATIC_WORKER_ROLES)
        # What the table leaves out is exactly the automatic ids that stay blocked.
        left_out = {action_id for action_id, spec in catalogue.items()
                    if spec["command"] is not None and not spec["user_only"]} - set(protocol_decision.PROTOCOL_ACTIONS)
        self.assertEqual(left_out, {"plan.withdraw", "implementation.recover_provenance", "functional.review.advisory"})

    def test_inspect_advises_of_listed_ids_this_release_does_not_know_and_never_refuses(self) -> None:
        # Plan C.3: a gate id of the vendored catalogue is known (never launched
        # by design); only an id outside both the table and the catalogue is news.
        listed = [*protocol_decision.PROTOCOL_ACTIONS, "implementation.approve", "forge.open_pr", "gate.policy"]
        self.assertEqual(protocol_decision.unknown_action_ids(listed), ["forge.open_pr", "gate.policy"])
        self.assertEqual(protocol_decision.unknown_action_ids(None), [])
        target = SimpleNamespace(root=Path("/r"), workflow_version="2.7.1", profile="full",
                                 target_protocol=TARGET_PROTOCOL, script_digests={}, protocol_action_ids=tuple(listed))
        self.assertEqual(cli._repository_block(target)["unknown_action_ids"], ["forge.open_pr", "gate.policy"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._print_repository_line(target)
        self.assertIn("advisory: the Workflow lists action ids this Controller release cannot launch: "
                      "forge.open_pr, gate.policy", out.getvalue())

    def test_a_2_9_0_listing_advises_of_exactly_the_four_unlaunched_automatic_ids(self) -> None:
        listed = sorted(protocol.KNOWN_ACTION_IDS)
        self.assertEqual(protocol_decision.unknown_action_ids(listed),
                         ["acceptance.satisfy", "implementation.satisfy", "plan.satisfy", "pr.apply_review"])
        self.assertEqual(protocol_decision.unknown_action_ids([*listed, "future.action"]),
                         ["acceptance.satisfy", "future.action", "implementation.satisfy", "plan.satisfy",
                          "pr.apply_review"])

    def test_an_automatic_id_the_table_lacks_is_blocked(self) -> None:
        got = self.decide(_result(action=_action("plan.withdraw", invocation="/milestone-plan wi-1")))
        self.assertFalse(got.automatic)
        self.assertIn(protocol_decision.UNKNOWN_ACTION, got.gate.what_is_required)
        self.assertIn("plan.withdraw", got.gate.what_is_required)

    def test_an_unknown_worker_role_on_an_automatic_decision_is_blocked(self) -> None:
        got = self.decide(_result(action=_action("plan.author", role="a_future_role")))
        self.assertFalse(got.automatic)
        self.assertIn(protocol_decision.UNKNOWN_WORKER_ROLE, got.gate.what_is_required)
        self.assertIn("a_future_role", got.gate.what_is_required)

    def test_a_user_only_automatic_decision_is_blocked(self) -> None:
        got = self.decide(_result(action=_action("plan.author", user_only=True)))
        self.assertFalse(got.automatic)
        self.assertIn(protocol_decision.USER_ONLY_ACTION, got.gate.what_is_required)

    def test_an_automatic_decision_without_an_action_is_blocked(self) -> None:
        got = self.decide(_result(action=None))
        self.assertFalse(got.automatic)
        self.assertIn(protocol_decision.UNKNOWN_ACTION, got.gate.what_is_required)

    def test_every_route_key_is_a_routing_role(self) -> None:
        for action_id, entry in protocol_decision.PROTOCOL_ACTIONS.items():
            with self.subTest(action=action_id):
                self.assertIn(entry.route, routing.ROLES)

    def test_the_two_new_roles_are_routed_and_the_legacy_command_map_is_untouched(self) -> None:
        self.assertIs(routing.ROLE_ROUTES["prepare-functional-review"], routing._INHERIT)
        self.assertIs(routing.ROLE_ROUTES["apply-functional-review"], routing._OPUS)
        self.assertFalse(routing.ROLE_ROUTES["apply-functional-review"].single_agent)
        self.assertEqual(set(routing.ROLE_BY_COMMAND_STEM), set(decision.SELECTED_COMMANDS))
        self.assertFalse({"prepare-functional-review", "apply-functional-review"} & set(routing.ROLE_BY_COMMAND_STEM))
        self.assertFalse({"prepare-functional-review", "apply-functional-review"} & set(routing.ROLE_BY_COMMAND_STEM.values()))
        self.assertIn("prepare-functional-review", decision.DELIBERATELY_NOT_SELECTED_COMMANDS)
        self.assertIn("apply-functional-review", decision.DELIBERATELY_NOT_SELECTED_COMMANDS)

    def test_a_protocol_job_is_launched_as_a_fresh_session(self) -> None:
        for action_id, entry in protocol_decision.PROTOCOL_ACTIONS.items():
            with self.subTest(action=action_id):
                resolved = routing.resolve_route(entry.route)
                self.assertTrue(resolved.to_record()["fresh_session"])
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "diag.json"
            streams = {key: str(Path(td) / key) for key in ("stdout_path", "stderr_path")}
            for path in streams.values():
                Path(path).touch()
            with mock.patch.dict(os.environ, {"FAKE_CLAUDE_DIAG_FILE": str(marker)}):
                worker.launch("/apply-functional-review wi-1", cwd=td, permission_mode="auto", timeout=10,
                              claude_bin=str(FAKE_CLAUDE), **streams)
            argv = json.loads(marker.read_text())["argv"]
        self.assertFalse(set(argv) & SESSION_REUSE_FLAGS)


def _catalogue_actions(script: Path) -> dict:
    """The action catalogue of the vendored ``workflow_protocol.py``
    (``ACTIONS``: id to command, role and ``user_only``), read from its
    source without importing it."""
    tree = ast.parse(script.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "ACTIONS" for t in node.targets):
            actions = {}
            for key, call in zip(node.value.keys, node.value.values):
                values = [a.value if isinstance(a, ast.Constant) else None for a in call.args]
                user_only = any(k.arg == "user_only" and k.value.value for k in call.keywords)
                actions[key.value] = {"command": values[0], "role": values[2], "user_only": user_only}
            return actions
    raise AssertionError("ACTIONS not found")


class InvocationRenderingTest(_Case):
    def test_a_matching_invocation_is_launched_and_a_differing_one_is_blocked_naming_both_texts(self) -> None:
        mismatching = _action("plan.author", invocation="/milestone-plan wi-1 --extra")
        got = self.decide(_result(action=mismatching))
        self.assertFalse(got.automatic)
        self.assertIsNone(got.action)
        text = got.gate.what_is_required
        for part in (protocol_decision.INVOCATION_MISMATCH, "plan.author", "/milestone-plan wi-1 --extra",
                     "'/milestone-plan wi-1'", "2.7.0"):
            self.assertIn(part, text)

    def test_a_missing_invocation_on_an_automatic_action_is_a_mismatch(self) -> None:
        got = self.decide(_result(action=_action("plan.author", invocation=None)))
        self.assertFalse(got.automatic)
        self.assertIn(protocol_decision.INVOCATION_MISMATCH, got.gate.what_is_required)

    def test_the_command_is_rendered_from_the_table_not_from_the_invocation(self) -> None:
        got = self.decide(_result(action=_action("plan.author")))
        self.assertEqual(got.action.command, protocol_decision.rendered_command("plan.author", "wi-1"))

    def test_plan_start_has_no_work_item_and_the_controller_appends_its_base(self) -> None:
        start = _result(row="1", phase=None, action=_action("plan.start", None), code="plan_start")
        bare = self.decide(start, item=target_state.NoWorkItemYet)
        self.assertTrue(bare.automatic)
        self.assertEqual(bare.action.command, "/milestone-plan")
        self.assertIs(bare.observed_phase, decision.NO_PHASE)
        based = self.decide(start, item=target_state.NoWorkItemYet, base="c" * 40)
        self.assertTrue(based.automatic)
        self.assertEqual(based.action.command, f"/milestone-plan {'c' * 40}")
        # The appended base is never a mismatch, and the document is untouched by it.
        self.assertEqual(based.protocol.document, start)

    def test_plan_start_blocked_is_a_gate_without_a_work_item(self) -> None:
        got = self.decide(_result(row="1a", phase=None, disposition="blocked", code="plan_start_not_tracked",
                                  text="state is not tracked"), item=target_state.NoWorkItemYet)
        self.assertFalse(got.automatic)
        self.assertEqual(got.gate.work_item_id, "")
        self.assertIs(got.observed_phase, decision.NO_PHASE)

    def test_the_base_is_never_appended_to_another_action(self) -> None:
        self.assertEqual(protocol_decision.rendered_command("plan.author", "wi-1", base="c" * 40), "/milestone-plan wi-1")


class CommittedStateGateTest(unittest.TestCase):
    """Plan C.5: 1.6.0's committed-state gate, in exactly its scope."""

    def _target(self, phase: str, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = fixtures.build_implementation_target(Path(tmp.name), phase=phase, **kwargs)
        item = target_state.select_work_item(target_state.read(repo), work_item_id="wi-1")
        return repo, item, _stub(repo.root)

    def _launch(self, stub, item, action_id: str = "implementation.checkpoint", phase: str = "IMPLEMENTING"):
        result = _result(row="23", phase=phase, action=_action(action_id, role="implementer"))
        return protocol_decision.from_answer(stub, item, protocol.parse_decision(result))

    def test_a_committed_state_launches(self) -> None:
        _repo, item, stub = self._target("IMPLEMENTING", checkpoint_ids=("CP1", "CP2"),
                                         checkpoints={"CP1": {"status": "COMPLETE", "start_commit": "a" * 40},
                                                      "CP2": {"status": "PENDING", "start_commit": None}})
        self.assertTrue(self._launch(stub, item).automatic)

    def test_an_uncommitted_complete_checkpoint_gates_at_an_implementation_phase(self) -> None:
        repo, _item, stub = self._target("IMPLEMENTING", checkpoint_ids=("CP1", "CP2"),
                                         checkpoints={"CP1": {"status": "COMPLETE", "start_commit": "a" * 40},
                                                      "CP2": {"status": "IN_PROGRESS", "start_commit": "a" * 40}})
        fixtures.update_workflow_state(repo.root, "wi-1", checkpoints={
            "CP1": {"status": "COMPLETE", "start_commit": "a" * 40},
            "CP2": {"status": "COMPLETE", "start_commit": "a" * 40}})
        item = target_state.select_work_item(target_state.read(repo), work_item_id="wi-1")
        got = self._launch(stub, item)
        self.assertFalse(got.automatic)
        self.assertIn("CP2", got.gate.what_is_required)
        self.assertIn("HEAD", got.gate.what_is_required)
        self.assertEqual(got.protocol.disposition, "automatic")

    def test_an_uncommitted_self_reviewing_phase_gates(self) -> None:
        repo, _item, stub = self._target("IMPLEMENTING")
        fixtures.update_workflow_state(repo.root, "wi-1", phase="SELF_REVIEWING_IMPLEMENTATION")
        item = target_state.select_work_item(target_state.read(repo), work_item_id="wi-1")
        got = self._launch(stub, item, "implementation.self_review", "SELF_REVIEWING_IMPLEMENTATION")
        self.assertFalse(got.automatic)
        self.assertIn("SELF_REVIEWING_IMPLEMENTATION", got.gate.what_is_required)

    def test_an_uncommitted_in_progress_checkpoint_is_the_dirty_resume_state_and_launches(self) -> None:
        repo, _item, stub = self._target("IMPLEMENTING", checkpoint_ids=("CP1",),
                                         checkpoints={"CP1": {"status": "PENDING", "start_commit": None}})
        fixtures.update_workflow_state(repo.root, "wi-1", current_checkpoint_id="CP1",
                                       checkpoints={"CP1": {"status": "IN_PROGRESS", "start_commit": "a" * 40}})
        item = target_state.select_work_item(target_state.read(repo), work_item_id="wi-1")
        self.assertTrue(self._launch(stub, item).automatic)

    def test_a_plan_stage_phase_with_uncommitted_state_launches(self) -> None:
        repo, _item, stub = self._target("AWAITING_LOCAL_PLAN_REVIEW", checkpoint_ids=("CP1",),
                                         checkpoints={"CP1": {"status": "PENDING", "start_commit": None}})
        fixtures.update_workflow_state(repo.root, "wi-1", checkpoints={"CP1": {"status": "COMPLETE", "start_commit": "a" * 40}},
                                       plan_revision=2)
        item = target_state.select_work_item(target_state.read(repo), work_item_id="wi-1")
        result = _result(row="12", phase="AWAITING_LOCAL_PLAN_REVIEW",
                         action=_action("plan.review.local", role="independent_reviewer"))
        self.assertTrue(protocol_decision.from_answer(stub, item, protocol.parse_decision(result)).automatic)

    def test_a_gate_decision_is_not_flagged(self) -> None:
        repo, _item, stub = self._target("IMPLEMENTING", checkpoint_ids=("CP1",),
                                         checkpoints={"CP1": {"status": "IN_PROGRESS", "start_commit": "a" * 40}})
        fixtures.update_workflow_state(repo.root, "wi-1", checkpoints={"CP1": {"status": "COMPLETE", "start_commit": "a" * 40}})
        item = target_state.select_work_item(target_state.read(repo), work_item_id="wi-1")
        result = _result(row="22", phase="IMPLEMENTING", disposition="blocked", code="plan_not_approved")
        got = protocol_decision.from_answer(stub, item, protocol.parse_decision(result))
        self.assertIn("plan_not_approved", got.gate.what_is_required)
        self.assertNotIn("durably record", got.gate.what_is_required)


class StaticTest(unittest.TestCase):
    """Invariant I8: the protocol path reads no lifecycle file. The one
    allow-listed call is the committed-state fact."""

    FORBIDDEN_TEXT = (".ai-review", "REVIEW_FEEDBACK", "FUNCTIONAL_REVIEW", "MANIFEST", "LEDGER", "interpret-trailers",
                      "Workflow-Checkpoint", "feedback_dir", "bundle_dir")
    ALLOWED_EVIDENCE_CALLS = {"uncommitted_implementation_state"}

    def test_the_module_names_no_lifecycle_file_and_reads_no_file(self) -> None:
        source = (CONTROLLER_DIR / "protocol_decision.py").read_text()
        tree = ast.parse(source)
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for fragment in self.FORBIDDEN_TEXT:
            self.assertFalse([s for s in strings if fragment in s], fragment)
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertFalse(calls & {"open", "read_text", "read_bytes", "iterdir", "glob", "rglob", "exists", "is_file"})
        evidence_calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                          and n.func.value.id == "evidence"}
        self.assertEqual(evidence_calls, self.ALLOWED_EVIDENCE_CALLS)
        imported = {alias.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "controller"
                    for alias in n.names}
        self.assertEqual(imported, {"evidence", "protocol", "target_state"})


def _verify(healthy: bool, *checks: tuple[str, str, str]) -> protocol.Verify:
    return protocol.Verify(healthy, tuple(protocol.Check(*check) for check in checks))


class WarnAdvisoryTest(_Case):
    """A ``warn`` check is advisory (warn-status plan D1, D4): shown and
    recorded, never a gate and never a change to ``healthy``."""

    WARN = ("gate_policy", "warn", "policy not adopted")

    def _preflight(self, health: protocol.Verify):
        with mock.patch.object(protocol, "verify", return_value=health):
            return protocol_decision.preflight(self.repo, _item())

    def test_warn_on_a_healthy_repository_is_no_gate_and_one_advisory(self) -> None:
        gate, advisories = self._preflight(_verify(True, ("state_valid", "pass", "ok"), self.WARN))
        self.assertIsNone(gate)
        self.assertEqual(advisories, ("verify gate_policy: warn: policy not adopted",))

    def test_decide_after_preflight_carries_the_advisory_in_the_decision_evidence(self) -> None:
        answer = protocol.parse_decision(_result(action=_action("plan.author")))
        with mock.patch.object(protocol, "verify", return_value=_verify(True, self.WARN)), \
                mock.patch.object(protocol, "next_action", return_value=answer):
            got, advisories = protocol_decision.decide_after_preflight(self.repo, _item())
        self.assertTrue(got.automatic)
        self.assertIn("verify gate_policy: warn: policy not adopted", got.evidence)
        self.assertEqual(advisories, ("verify gate_policy: warn: policy not adopted",))

    def test_a_step_and_explain_both_decide_through_the_shared_entry_point(self) -> None:
        # D4: one preflight per step, through `decide_after_preflight`, for the job path as for `explain`.
        stub = protocol_decision.Decision(
            observed_phase=None, evidence=(), action=None, automatic=False, gate=None, declined=False, reason="x")
        with mock.patch.object(protocol_decision, "decide_after_preflight", return_value=(stub, ())) as shared, \
                mock.patch.object(protocol_decision, "preflight") as direct, \
                mock.patch.object(protocol_decision, "decide") as plain:
            got = job._decide_protocol_current(
                self.repo, runtime=Path("."), snapshot=None, work_item=_item(), work_item_id=None,
                pre_state={}, base=None)
        self.assertIs(got[0], stub)
        shared.assert_called_once()
        direct.assert_not_called()
        plain.assert_not_called()
        src = Path(cli.__file__).read_text(encoding="utf-8")
        self.assertIn("protocol_decision.decide_after_preflight(target, work_item, base=base)", src)

    def test_without_a_warn_the_evidence_is_unchanged(self) -> None:
        answer = protocol.parse_decision(_result(action=_action("plan.author")))
        plain = protocol_decision.from_answer(self.repo, _item(), answer)
        with mock.patch.object(protocol, "verify", return_value=_verify(True, ("state_valid", "pass", "ok"))), \
                mock.patch.object(protocol, "next_action", return_value=answer):
            got, advisories = protocol_decision.decide_after_preflight(self.repo, _item())
        self.assertEqual((advisories, got.evidence), ((), plain.evidence))

    def test_the_unhealthy_gate_lists_the_failing_then_the_warn_checks(self) -> None:
        gate, advisories = self._preflight(_verify(
            False, ("state_valid", "fail", "bad state"), self.WARN, ("other", "warn", "second")))
        what = gate.gate.what_is_required
        self.assertIn("(state_valid: bad state; gate_policy: warn: policy not adopted; other: warn: second)", what)
        self.assertEqual(len(advisories), 2)
        self.assertEqual(gate.evidence[-2:], advisories)
        self.assertEqual(health_gate_text(self.repo, _verify(False, ("state_valid", "fail", "bad state"))),
                         "(state_valid: bad state)")

    def test_unhealthy_with_only_warn_checks_reads_without_empty_parentheses(self) -> None:
        gate, _ = self._preflight(_verify(False, ("state_valid", "pass", "ok"), self.WARN))
        what = gate.gate.what_is_required
        self.assertIn("unhealthy (gate_policy: warn: policy not adopted)", what)
        self.assertNotIn("()", what)

    def test_unhealthy_with_nothing_to_name_has_a_readable_gate(self) -> None:
        gate, advisories = self._preflight(_verify(False, ("state_valid", "pass", "ok")))
        self.assertEqual(advisories, ())
        self.assertNotIn("()", gate.gate.what_is_required)
        self.assertIn("no check names a cause", gate.gate.what_is_required)

    def test_health_gate_is_the_preflight_gate_alone(self) -> None:
        with mock.patch.object(protocol, "verify", return_value=_verify(True, self.WARN)):
            self.assertIsNone(protocol_decision.health_gate(self.repo, _item()))

    def test_every_standing_decision_constructor_carries_the_advisories(self) -> None:
        advisories = ("verify gate_policy: warn: policy not adopted",)
        for disposition, action in (
            ("human_gate", _action("plan.approve", role="user", user_only=True, invocation="/approve-review plan wi-1")),
            ("blocked", None), ("complete", None),
        ):
            with self.subTest(disposition=disposition):
                got = protocol_decision.from_answer(
                    self.repo, _item(), protocol.parse_decision(_result(disposition=disposition, action=action)),
                    advisories=advisories)
                self.assertEqual(got.evidence[-1], advisories[0])
        automatic = protocol_decision.from_answer(
            self.repo, _item(), protocol.parse_decision(_result(action=_action("plan.author"))), advisories=advisories)
        stale = protocol_decision.gate_for(self.repo, _item(), automatic, protocol_decision.DECISION_UNSTABLE, "text",
                                           advisories=advisories)
        self.assertEqual(stale.evidence[-1], advisories[0])
        guard = job._unstable_gate(self.repo, _item(), automatic, ["a", "b", "c"], advisories)
        self.assertEqual((guard.protocol.row, guard.evidence[-1]), (automatic.protocol.row, advisories[0]))

    def test_explain_shows_the_advisory_in_text_and_json(self) -> None:
        from tests.test_cli import _Args
        with tempfile.TemporaryDirectory() as td:
            root, stub = JobWiringTest()._target(td, with_item=True)
            runtime = Path(td) / "runtime"
            runtime.mkdir()
            warn = protocol.Verify(True, (protocol.Check("gate_policy", "warn", "policy not adopted"),))
            for as_json in (False, True):
                out = io.StringIO()
                with mock.patch.object(protocol, "verify", return_value=warn), contextlib.redirect_stdout(out):
                    code = cli.cmd_explain(_Args(str(root), workflow_manager=str(stub), json_out=as_json), runtime,
                                           IDENTITY)
                self.assertEqual(code, cli.EXIT_OK)
                self.assertIn("verify gate_policy: warn: policy not adopted", out.getvalue())


def health_gate_text(repo, health: protocol.Verify) -> str:
    with mock.patch.object(protocol, "verify", return_value=health):
        gate, _ = protocol_decision.preflight(repo, _item())
    return gate.gate.what_is_required[gate.gate.what_is_required.index("("):].split(")")[0] + ")"


class JobWiringTest(unittest.TestCase):
    """A protocol target reaches ``protocol_decision``, never
    ``evidence.decide``; a launched protocol job's outcome is read from the
    Workflow's ``reconcile`` (CP5)."""

    def _target(self, td: str, *, with_item: bool) -> tuple[Path, Path]:
        root = Path(td) / "repo"
        if with_item:
            fixtures.seed_workflow_item(root, "2.7.0", "publish", work_item_id="demo")
        else:
            fixtures.git_init(root)
            fixtures.install_workflow_release(root, "2.7.0")
            docs = root / "docs" / "ai-workflow"
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "WORKFLOW_CONFIG.json").write_text(json.dumps(
                {"schema_version": 1, "default_workflow_version": "2.2", "supported_versions": ["1", "2.1", "2.2"]}))
            (docs / "WORKFLOW_STATE.json").write_text(json.dumps(
                {"schema_version": 1, "active_work_item_id": None, "work_items": {}}))
            fixtures.commit_all(root, "seed")
        stub = fixtures.write_stub_workflow_manager(Path(td) / "workflow-manager", release="2.7.0")
        return root, stub

    def _step(self, td: str, root: Path, stub: Path, answer: dict | None = None) -> tuple[dict, Path, Path]:
        runtime = Path(td) / "runtime"
        runtime.mkdir()
        diag = Path(td) / "diag.json"
        target = managed_repo.inspect(root, manager_bin=str(stub))
        self.assertIsNotNone(target.target_protocol)
        patches = [mock.patch.dict(os.environ, {"FAKE_CLAUDE_DIAG_FILE": str(diag)}),
                   mock.patch.object(job.evidence, "decide", side_effect=AssertionError("evidence.decide ran"))]
        if answer is not None:
            patches.append(mock.patch.object(protocol, "next_action", return_value=protocol.parse_decision(answer)))
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            record = job.execute_step(target, identity=IDENTITY, runtime=runtime, claude_bin=str(FAKE_CLAUDE))
        return record, diag, runtime

    def test_a_protocol_step_decides_through_the_workflow_and_reconciles_its_worker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, stub = self._target(td, with_item=True)
            received = protocol.next_action(root, "demo")
            record, diag, _runtime = self._step(td, root, stub)
            self.assertEqual(record["status"], job.STATUS_FINISHED)
            self.assertEqual(record["protocol"]["progress"], "none")
            self.assertEqual(record["selected_action"]["command"], "/milestone-plan demo")
            self.assertTrue(diag.exists())
            # No lifecycle file was read for the pre-state (I8).
            self.assertFalse(record["pre_state"]["bundle_manifest_readable"])
            self.assertEqual(record["pre_state"]["phase"], "PLANNING")
            # The decision's rendering matches the catalogue: the Workflow's own reconcile accepts the document.
            document = Path(td) / "decision.json"
            document.write_text(json.dumps(received.raw))
            self.assertEqual(protocol.reconcile(root, document, "demo").outcome, "no_progress")

    def test_plan_start_is_the_bare_command_and_its_document_reconciles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, stub = self._target(td, with_item=False)
            received = protocol.next_action(root, None)
            record, diag, _runtime = self._step(td, root, stub)
            self.assertEqual(received.action.id, "plan.start")
            self.assertEqual(record["status"], job.STATUS_FINISHED)
            self.assertEqual(record["selected_action"]["command"], "/milestone-plan")
            self.assertTrue(diag.exists())
            document = Path(td) / "decision.json"
            document.write_text(json.dumps(received.raw))
            self.assertEqual(protocol.reconcile(root, document).outcome, "no_progress")

    def test_a_blocked_mismatch_records_a_gate_and_spawns_no_worker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, stub = self._target(td, with_item=True)
            bad = json.loads(json.dumps(protocol.next_action(root, "demo").raw))
            bad["action"]["invocation"] = "/milestone-plan demo --now"
            record, diag, runtime = self._step(td, root, stub, bad)
            self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
            self.assertIn(protocol_decision.INVOCATION_MISMATCH, record["human_gate_pending"]["what_is_required"])
            self.assertFalse(diag.exists())
            self.assertNotIn("worker", record)
            # Only the one no-launch record: no PLANNED/LAUNCHED job exists.
            statuses = [json.loads(p.read_text())["status"] for p in (runtime / "jobs").glob("*.json")]
            self.assertEqual(statuses, [job.STATUS_GATE_BLOCKED])

    def test_an_unhealthy_verify_is_a_gate_naming_each_failing_check_and_launches_nothing(self) -> None:
        # Plan A.3: `verify` is the step's protocol preflight, and its answer
        # is a gate, never a refusal of the repository.
        from tests.test_cli import _Args
        with tempfile.TemporaryDirectory() as td:
            root, stub = self._target(td, with_item=True)
            # The installation record names a release these scripts are not.
            record_path = root / protocol.INSTALLATION_RECORD
            installation = json.loads(record_path.read_text())
            installation["workflow_version"] = "2.7.9"
            record_path.write_text(json.dumps(installation))
            fixtures.commit_all(root, "name another release")
            health = protocol.verify(root)
            self.assertFalse(health.healthy)
            failing = [check for check in health.checks if check.status == "fail"]
            self.assertEqual([check.id for check in failing], ["installation_release_matches"])
            with mock.patch.object(protocol, "next_action", side_effect=AssertionError("next-action ran")):
                record, diag, runtime = self._step(td, root, stub)
                self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
                what = record["human_gate_pending"]["what_is_required"]
                self.assertTrue(what.startswith(protocol_decision.WORKFLOW_UNHEALTHY), what)
                for check in failing:
                    self.assertIn(f"{check.id}: {check.detail}", what)
                self.assertFalse(diag.exists())
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.cmd_explain(_Args(str(root), workflow_manager=str(stub), json_out=True), runtime,
                                           IDENTITY)
            self.assertEqual(code, cli.EXIT_OK)
            self.assertIn(protocol_decision.WORKFLOW_UNHEALTHY, out.getvalue())

    def test_a_warn_check_does_not_stop_a_step_and_is_recorded_in_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, stub = self._target(td, with_item=True)
            warn = protocol.Verify(True, (protocol.Check("gate_policy", "warn", "policy not adopted"),))
            with mock.patch.object(protocol, "verify", return_value=warn):
                record, diag, _runtime = self._step(td, root, stub)
            self.assertEqual(record["status"], job.STATUS_FINISHED)
            self.assertTrue(diag.exists())
            self.assertIn("verify gate_policy: warn: policy not adopted", record["selected_action"]["evidence"])

    def test_a_protocol_worker_is_routed_by_its_route_key(self) -> None:
        for action_id, entry in protocol_decision.PROTOCOL_ACTIONS.items():
            with self.subTest(action=action_id):
                item = None if action_id == "plan.start" else "wi-1"
                answer = _result(action=_action(action_id, item), phase=None if item is None else "PLANNING")
                got = protocol_decision.from_answer(
                    _stub(Path("/nonexistent")), target_state.NoWorkItemYet if item is None else _item(),
                    protocol.parse_decision(answer))
                route = job._worker_route(got, _item(), routing.NO_OVERRIDES)
                self.assertEqual(route.role, entry.route)

    def test_explain_shows_the_workflows_row_disposition_and_action(self) -> None:
        from tests.test_cli import _Args
        with tempfile.TemporaryDirectory() as td:
            root, stub = self._target(td, with_item=True)
            runtime = Path(td) / "runtime"
            runtime.mkdir()
            for as_json in (False, True):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.cmd_explain(_Args(str(root), workflow_manager=str(stub), json_out=as_json), runtime, IDENTITY)
                self.assertEqual(code, cli.EXIT_OK)
                if as_json:
                    block = json.loads(out.getvalue())["protocol"]
                    self.assertEqual((block["row"], block["disposition"], block["action_id"], block["release"]),
                                     ("7", "automatic", "plan.author", "2.7.0"))
                    self.assertEqual(block["route"], "milestone-plan")
                    self.assertEqual(json.loads(out.getvalue())["action"], "/milestone-plan demo")
                else:
                    self.assertIn("workflow mode: protocol (Workflow 2.7.0, protocol 1.0)", out.getvalue())
                    self.assertIn("protocol row 7: disposition automatic, action plan.author", out.getvalue())
                    self.assertIn("next automatic action: /milestone-plan demo", out.getvalue())

    def test_explain_applies_the_loop_guard_step_applies(self) -> None:
        from tests.test_cli import _Args
        from tests.test_protocol_job import _write_job
        with tempfile.TemporaryDirectory() as td:
            root, stub = self._target(td, with_item=True)
            runtime = Path(td) / "runtime"
            runtime.mkdir()
            for n in (1, 2):
                _write_job(runtime, root, n, action="plan.author", work_item="demo",
                           status=job.STATUS_FINISHED, reconcile="no_progress")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.cmd_explain(_Args(str(root), workflow_manager=str(stub), json_out=True), runtime, IDENTITY)
            self.assertEqual(code, cli.EXIT_OK)
            self.assertIn(protocol_decision.NO_PROGRESS_REPEATED, out.getvalue())

    def test_a_legacy_explain_has_no_protocol_key(self) -> None:
        from tests.test_cli import _Args
        with tempfile.TemporaryDirectory() as td:
            repo = fixtures.build_implementation_target(Path(td), phase="IMPLEMENTING", checkpoint_ids=("CP1",)).root
            stub = fixtures.write_stub_workflow_manager(
                Path(td) / "workflow-manager", release=fixtures.REFERENCE_WORKFLOW_RELEASE)
            runtime = Path(td) / "runtime"
            runtime.mkdir()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.cmd_explain(_Args(str(repo), workflow_manager=str(stub), json_out=True), runtime, IDENTITY)
            self.assertNotIn("protocol", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()
