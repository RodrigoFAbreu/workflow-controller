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
VENDORED = REPO_ROOT / "tests" / "workflow_releases" / "2.7.0" / "docs" / "ai-workflow" / "orchestration-protocol-v1.schema.json"
#: The sha256 of Workflow 2.7.0's published schema.
SCHEMA_SHA256 = "aba31a7afdfa94246f1f28db5619a6ebe4b4ff06a923e6d00cc9102c2d59fc5a"

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

    def test_a_strict_enum_still_fails(self) -> None:
        bad = copy.deepcopy(DECISION)
        bad["action"]["allowed_results"] = ["progress", "rubbish"]
        with self.assertRaisesRegex(SchemaViolation, "rubbish"):
            self.validate(envelope(result=bad))
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
        with self.assertRaises(SchemaViolation):
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


if __name__ == "__main__":
    unittest.main()
