"""Tests for ``controller.protocol_schema``
(``docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md``, A.2, CP1)."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import protocol, protocol_schema  # noqa: E402
from controller.protocol_schema import Schema, SchemaError, SchemaViolation  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED = REPO_ROOT / "tests" / "protocol_schemas" / "workflow-2.9.0.schema.json"
#: The sha256 of Workflow 2.9.0's published schema (protocol 1.2).
SCHEMA_SHA256 = "c203f2b263a3060be1e1f41d478bebd60c1a40302de9daecf7fe7cc97d5af52c"
NEW_ACTION_IDS = frozenset({
    "acceptance.satisfy", "functional.evidence.external", "implementation.resume", "implementation.satisfy",
    "legacy.retire", "plan.satisfy", "pr.apply_review", "pr.review.external"})

BASIS = {"work_item_id": "wi", "state_revision": 3, "state_identity": "a" * 64, "phase": "PLANNING",
         "head": "b" * 40, "checkpoints": {"CP1": "COMPLETE"}}
WORKER = {"role": "planner", "fresh_session": True, "independent_of": [], "user_only": False}
ACTION = {"id": "plan.author", "arguments": {"work_item_id": "wi"}, "invocation": "/milestone-plan wi",
          "worker": WORKER, "allowed_results": ["progress", "gate_reached"]}
DECISION = {"row": "7", "basis": BASIS, "snapshot": {"phase": "PLANNING"}, "disposition": "automatic",
            "action": ACTION, "satisfied_by": None, "alternatives": [],
            "reason": {"code": "plan_needed", "text": "a plan is needed", "remedy": None}}


def envelope(operation: str = "next-action", result: dict | None = None, **overrides) -> dict:
    body = {"protocol": {"name": "workflow-orchestration", "version": "1.0"}, "workflow_release": "2.7.0",
            "operation": operation, "ok": True, "result": copy.deepcopy(DECISION if result is None else result)}
    body.update(overrides)
    return body


def refusal(code: str = "stale_decision") -> dict:
    return {"protocol": {"name": "workflow-orchestration", "version": "1.0"}, "workflow_release": "2.7.0",
            "operation": "next-action", "ok": False,
            "error": {"code": code, "message": "m", "retryable": True, "native": None}}


class VendoredSchemaTest(unittest.TestCase):
    def test_the_package_copy_is_the_published_schema(self) -> None:
        data = protocol_schema.SCHEMA_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), SCHEMA_SHA256)
        self.assertEqual(data, VENDORED.read_bytes())

    def test_the_eight_protocol_1_2_action_ids_are_catalogue_members(self) -> None:
        self.assertLessEqual(NEW_ACTION_IDS, protocol.KNOWN_ACTION_IDS)

    def test_status_accepts_warn_and_an_unlisted_value_is_advisory(self) -> None:
        pointer = "#/$defs/results/properties/verify/properties/checks/items/properties/status"
        self.assertEqual(set(protocol_schema.SCHEMA.enum_at(pointer)), {"pass", "warn", "fail", "skip"})
        self.assertIn(pointer, protocol_schema.OPEN_ENUMS)
        self.assertEqual(protocol_schema.ENUM_RULES[pointer], protocol_schema.ADVISORY)

    def test_only_the_protocol_client_names_record_external_result(self) -> None:
        # Until workflow-controller-codex-reviewer CP1 no module did; now the
        # typed wrapper in protocol.py is the one place (obligation 8: every
        # other module goes through it).
        for path in (REPO_ROOT / "controller").glob("*.py"):
            with self.subTest(path.name):
                if path.name == "protocol_schema.py":
                    continue  # names the schema pointer of that operation's enums
                if path.name == "protocol.py":
                    self.assertIn("record-external-result", path.read_text())
                else:
                    self.assertNotIn("record-external-result", path.read_text())

    def test_the_schema_is_packaged(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text()
        self.assertIn("protocol_schema.json", text)

    def test_every_keyword_the_published_schema_uses_is_supported(self) -> None:
        # Loading checked it already; this names the subset the plan states.
        self.assertEqual(protocol_schema.VALIDATION_KEYWORDS | protocol_schema.ANNOTATION_KEYWORDS,
                         {"type", "required", "properties", "items", "$ref", "$defs", "enum",
                          "additionalProperties", "$schema", "$id", "title", "description"})


class SchemaLoadTest(unittest.TestCase):
    def test_a_keyword_outside_the_subset_is_rejected_at_load(self) -> None:
        for keyword in ("oneOf", "pattern", "minimum", "const", "format", "if"):
            with self.subTest(keyword):
                with self.assertRaisesRegex(SchemaError, keyword):
                    Schema({"type": "object", "properties": {"a": {keyword: 1}}})

    def test_a_nested_unsupported_keyword_is_rejected(self) -> None:
        with self.assertRaises(SchemaError):
            Schema({"$defs": {"x": {"items": {"anyOf": []}}}})

    def test_an_unresolvable_or_remote_ref_is_rejected(self) -> None:
        with self.assertRaises(SchemaError):
            Schema({"$ref": "#/$defs/missing"})
        with self.assertRaises(SchemaError):
            Schema({"$ref": "https://example.invalid/schema.json"})

    def test_an_unknown_type_name_is_rejected(self) -> None:
        with self.assertRaises(SchemaError):
            Schema({"type": "float"})

    def test_annotation_keywords_load_and_change_nothing(self) -> None:
        schema = Schema({"$schema": "x", "$id": "y", "title": "t", "description": "d", "type": "object"})
        schema.validate_envelope({})
        with self.assertRaises(SchemaViolation):
            schema.validate_envelope([])


class ValidationTest(unittest.TestCase):
    def validate(self, document: dict) -> None:
        protocol_schema.SCHEMA.validate_envelope(document)
        if document.get("ok") and isinstance(document.get("result"), dict):
            protocol_schema.SCHEMA.validate_result(document["operation"], document["result"])

    def test_a_published_shape_validates(self) -> None:
        self.validate(envelope())
        self.validate(refusal())

    def test_wrong_type_and_missing_required_fail(self) -> None:
        with self.assertRaisesRegex(SchemaViolation, r"\$\.ok"):
            self.validate(envelope(ok="yes"))
        broken = envelope()
        del broken["workflow_release"]
        with self.assertRaisesRegex(SchemaViolation, "workflow_release"):
            self.validate(broken)
        no_row = copy.deepcopy(DECISION)
        del no_row["row"]
        with self.assertRaisesRegex(SchemaViolation, "row"):
            self.validate(envelope(result=no_row))
        bad_state = copy.deepcopy(DECISION)
        bad_state["basis"]["state_revision"] = "3"
        with self.assertRaisesRegex(SchemaViolation, "state_revision"):
            self.validate(envelope(result=bad_state))

    def test_a_boolean_is_not_an_integer(self) -> None:
        bad = copy.deepcopy(DECISION)
        bad["basis"]["state_revision"] = True
        with self.assertRaises(SchemaViolation):
            self.validate(envelope(result=bad))

    def test_the_protocol_name_enum_still_fails(self) -> None:
        # Every other enum is open (`ENUM_RULES`); only a different protocol is refused.
        extended = copy.deepcopy(DECISION)
        extended["action"]["allowed_results"] = ["progress", "a_result_a_minor_adds"]
        self.validate(envelope(result=extended))
        with self.assertRaises(SchemaViolation):
            self.validate(envelope(protocol={"name": "other", "version": "1.0"}))

    def test_a_minor_extension_validates(self) -> None:
        """A protocol 1.1-shaped answer: an extra field everywhere a closed
        object appears, a new action id, also inside ``alternatives``."""
        extended = copy.deepcopy(DECISION)
        extended["new_top_level"] = {"anything": 1}
        extended["basis"]["extra"] = 1
        extended["action"]["id"] = "plan.future_action"
        extended["action"]["extra"] = [1]
        extended["action"]["worker"]["extra"] = True
        extended["alternatives"] = [dict(ACTION, id="another.future_action", extra="x")]
        document = envelope(result=extended)
        document["protocol"]["version"] = "1.1"
        document["added_envelope_field"] = 1
        self.validate(document)

    def test_the_open_enums_accept_an_unknown_value_but_not_a_wrong_type(self) -> None:
        unknown = copy.deepcopy(DECISION)
        unknown["disposition"] = "deferred"
        unknown["satisfied_by"] = "functional_evidence"
        unknown["action"]["worker"]["role"] = "robot"
        self.validate(envelope(result=unknown))
        self.validate(refusal("a_future_code"))
        wrong = copy.deepcopy(DECISION)
        wrong["disposition"] = 7
        with self.assertRaises(SchemaViolation):
            self.validate(envelope(result=wrong))

    def test_an_unknown_check_id_validates(self) -> None:
        result = {"healthy": True, "checks": [{"id": "future_check", "status": "pass", "detail": "ok"}]}
        self.validate(envelope("verify", result))
        result["checks"][0]["status"] = "maybe"
        self.validate(envelope("verify", result))

    def test_a_result_for_an_unlisted_operation_is_a_violation(self) -> None:
        with self.assertRaisesRegex(SchemaViolation, "no result"):
            protocol_schema.SCHEMA.validate_result("teleport", {})

    def test_an_external_gate_with_an_unknown_satisfied_by_is_a_gate(self) -> None:
        gate = copy.deepcopy(DECISION)
        gate.update(disposition="external_gate", satisfied_by="functional_evidence")
        self.validate(envelope(result=gate))
        decision = protocol.parse_decision(gate)
        self.assertEqual((decision.disposition, decision.satisfied_by), ("external_gate", "functional_evidence"))

    def test_the_decision_is_kept_as_received(self) -> None:
        extended = copy.deepcopy(DECISION)
        extended["added"] = {"x": [1, 2]}
        decision = protocol.parse_decision(extended)
        self.assertEqual(json.dumps(decision.raw, sort_keys=True), json.dumps(extended, sort_keys=True))


class EnumRulesTest(unittest.TestCase):
    """A closed enum in a protocol 1.x answer never refuses it
    (gate-policy plan D6, follow-up 17): every enum location has a stated rule."""

    @staticmethod
    def locations() -> dict:
        found: dict = {}

        def walk(node: dict, pointer: str) -> None:
            if "enum" in node:
                found[pointer] = tuple(node["enum"])
            for key in ("$defs", "properties"):
                for name, child in node.get(key, {}).items():
                    walk(child, f"{pointer}/{key}/{name}")
            if "items" in node:
                walk(node["items"], f"{pointer}/items")
            if isinstance(node.get("additionalProperties"), dict):
                walk(node["additionalProperties"], f"{pointer}/additionalProperties")

        walk(json.loads(protocol_schema.SCHEMA_PATH.read_text(encoding="utf-8")), "#")
        return found

    def test_every_enum_location_of_the_schema_has_a_rule_and_no_rule_is_stray(self) -> None:
        self.assertEqual(set(self.locations()), set(protocol_schema.ENUM_RULES))
        self.assertEqual(len(protocol_schema.ENUM_RULES), 20)

    def test_every_rule_is_one_of_the_four(self) -> None:
        self.assertLessEqual(set(protocol_schema.ENUM_RULES.values()), {
            protocol_schema.IDENTITY, protocol_schema.ADVISORY, protocol_schema.BLOCKED, protocol_schema.IGNORED})

    def test_the_seven_previously_open_enums_stay_open_by_name(self) -> None:
        self.assertEqual(len(protocol_schema.PREVIOUSLY_OPEN_ENUMS), 7)
        self.assertLessEqual(protocol_schema.PREVIOUSLY_OPEN_ENUMS, protocol_schema.OPEN_ENUMS)

    def test_only_the_protocol_name_is_closed(self) -> None:
        closed = set(protocol_schema.ENUM_RULES) - protocol_schema.OPEN_ENUMS
        self.assertEqual(closed, {"#/$defs/envelope/properties/protocol/properties/name"})

    def test_a_value_the_schema_does_not_list_is_accepted_at_every_open_enum(self) -> None:
        schema = protocol_schema.SCHEMA
        for pointer in sorted(protocol_schema.OPEN_ENUMS):
            with self.subTest(pointer):
                node = schema._resolve(pointer, "#")
                schema._validate("a_value_a_minor_release_adds", node, pointer, "$")

    def test_the_protocol_name_still_refuses_a_different_value(self) -> None:
        document = envelope()
        document["protocol"]["name"] = "another-protocol"
        with self.assertRaises(SchemaViolation):
            protocol_schema.SCHEMA.validate_envelope(document)

    def test_an_unknown_value_in_a_whole_answer_validates(self) -> None:
        document = envelope("verify", {"healthy": True, "checks": [
            {"id": "a_new_check", "status": "a_new_status", "detail": "d"}]})
        protocol_schema.SCHEMA.validate_envelope(document)
        protocol_schema.SCHEMA.validate_result("verify", document["result"])


if __name__ == "__main__":
    unittest.main()
