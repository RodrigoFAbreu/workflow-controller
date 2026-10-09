#!/usr/bin/env python3
"""Tests for `workflow_protocol.py`, Orchestration Protocol v1's core
(`ORCHESTRATION_PROTOCOL_V1_PLAN.md`, CP3): the envelope, protocol
versioning, error codes and the Workflow exception domain, state identity,
`describe`, `verify` and `resolve-artifact`.

Every response is checked against the shipped
`docs/ai-workflow/orchestration-protocol-v1.schema.json` with the minimal
validator below (`type`, `required`, `properties`, `additionalProperties`,
`enum`, `items`, `$ref`). Scratch repositories come from
`workflow_test_harness`.

Stdlib-only. Run: python3 scripts/workflow_protocol_test.py
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import workflow_fingerprint as fingerprint
import workflow_gate_policy as wgp
import workflow_protocol as wp
import workflow_state as ws
import workflow_test_harness as h

SCRIPT = Path(__file__).resolve().parent / "workflow_protocol.py"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "ai-workflow" / "orchestration-protocol-v1.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())

# ---------------------------------------------------------------------------
# The minimal JSON Schema checker
# ---------------------------------------------------------------------------

VALIDATING_KEYWORDS = frozenset({
    "type", "required", "properties", "additionalProperties", "enum", "items", "$ref",
})
ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "$defs", "title", "description"})

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _resolve_ref(ref: str) -> dict:
    if not ref.startswith("#/"):
        raise AssertionError(f"unsupported $ref {ref!r}")
    node = SCHEMA
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    """Every violation of `schema` by `value`, as `path: reason` strings."""
    if "$ref" in schema:
        return schema_errors(value, _resolve_ref(schema["$ref"]), path)
    errors: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            return [f"{path}: {value!r} is not of type {types}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required {key!r}")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors.extend(schema_errors(item, properties[key], f"{path}.{key}"))
            elif extra is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(extra, dict):
                errors.extend(schema_errors(item, extra, f"{path}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema["items"], f"{path}[{index}]"))
    return errors


def _keywords(node, found: set[str], *, in_properties: bool = False) -> None:
    if isinstance(node, dict):
        for key, child in node.items():
            if not in_properties:
                found.add(key)
            if key in ("properties", "$defs") and not in_properties:
                _keywords(child, found, in_properties=True)
            elif key in ("enum", "required", "title", "description", "$schema", "$id"):
                continue
            else:
                _keywords(child, found)
    elif isinstance(node, list):
        for child in node:
            _keywords(child, found)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def call(*argv: str) -> tuple[dict, int]:
    """Run an operation in-process and check the envelope and its result
    against the schema."""
    body, code = wp.run(list(argv))
    assert_valid(body)
    return body, code


def assert_valid(body: dict) -> None:
    errors = schema_errors(body, SCHEMA)
    if body.get("ok"):
        result_schema = SCHEMA["$defs"]["results"]["properties"][body["operation"]]
        errors += schema_errors(body["result"], result_schema, "$.result")
        if "error" in body:
            errors.append("$: ok envelope carries an error")
    else:
        if "error" not in body or "result" in body:
            errors.append("$: a refusal carries error and no result")
    if errors:
        raise AssertionError("schema violations:\n" + "\n".join(errors))


def write_state(repo: h.ScratchRepo, state: dict) -> Path:
    path = repo.root / ws.DEFAULT_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    return path


def empty_state() -> dict:
    return h.base_state()


def item(**overrides) -> dict:
    return h.base_work_item(state_revision=1, **overrides)


def checks_by_id(body: dict) -> dict:
    return {c["id"]: c for c in body["result"]["checks"]}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema(unittest.TestCase):
    def test_schema_uses_only_the_supported_keywords(self):
        found: set[str] = set()
        _keywords(SCHEMA, found)
        self.assertLessEqual(found, VALIDATING_KEYWORDS | ANNOTATION_KEYWORDS)

    def test_every_operation_has_a_result_schema(self):
        self.assertEqual(set(SCHEMA["$defs"]["results"]["properties"]), set(wp.OPERATIONS))

    def test_error_code_enum_equals_the_code_table(self):
        self.assertEqual(SCHEMA["$defs"]["error"]["properties"]["code"]["enum"], sorted(wp.ERROR_CODES))

    def test_artifact_kind_enum_equals_the_kind_table(self):
        kinds = SCHEMA["$defs"]["results"]["properties"]["resolve-artifact"]["properties"]["kind"]["enum"]
        self.assertEqual(kinds, sorted(wp.ARTIFACT_KINDS))

    def test_verify_check_enum_equals_the_check_ids(self):
        ids = SCHEMA["$defs"]["results"]["properties"]["verify"]["properties"]["checks"]["items"][
            "properties"]["id"]["enum"]
        self.assertEqual(ids, list(wp.VERIFY_CHECK_IDS))

    def test_the_checker_rejects_a_bad_envelope(self):
        body, _ = wp.run(["describe"])
        body = dict(body, extra=1)
        with self.assertRaises(AssertionError):
            assert_valid(body)


# ---------------------------------------------------------------------------
# Envelope, versioning, exit codes
# ---------------------------------------------------------------------------


class TestEnvelope(unittest.TestCase):
    def test_stdout_is_exactly_one_envelope(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state())
            out = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(repo.root), "verify"],
                capture_output=True, text=True,
            )
        self.assertEqual(out.returncode, wp.EXIT_OK, out.stderr)
        self.assertTrue(out.stdout.endswith("\n"))
        self.assertEqual(out.stdout.count("\n"), 1)
        body = json.loads(out.stdout)
        assert_valid(body)
        self.assertEqual(body["protocol"], {"name": "workflow-orchestration", "version": "1.2"})
        self.assertEqual(body["workflow_release"], wp.WORKFLOW_RELEASE)
        self.assertEqual(body["operation"], "verify")

    def test_unsupported_major_refuses_before_anything_is_read(self):
        with mock.patch.object(wp, "read_state_and_config", side_effect=AssertionError("read")), \
                mock.patch.dict(wp.OPERATIONS, {"describe": mock.Mock(side_effect=AssertionError("run"))}):
            body, code = call("--repo-root", "/nonexistent/repository", "--protocol-major", "2", "describe")
        self.assertEqual(code, wp.EXIT_REFUSED)
        self.assertEqual(body["error"]["code"], "unsupported_protocol")
        self.assertFalse(body["error"]["retryable"])
        self.assertIsNone(body["error"]["native"])

    def test_unsupported_major_through_the_cli_exits_3(self):
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "--protocol-major", "2", "describe"],
            capture_output=True, text=True,
        )
        self.assertEqual(out.returncode, 3)
        body = json.loads(out.stdout)
        assert_valid(body)
        self.assertEqual(body["error"]["code"], "unsupported_protocol")

    def test_supported_major_is_accepted(self):
        body, code = call("--protocol-major", "1", "describe")
        self.assertEqual(code, wp.EXIT_OK)
        self.assertTrue(body["ok"])

    def test_bad_argument_is_invalid_request_exit_2_as_an_envelope(self):
        for argv in (["bogus"], [], ["resolve-artifact", "--kind", "plan_document"],
                     ["--protocol-major", "one", "describe"], ["describe", "--unknown"]):
            out = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True)
            self.assertEqual(out.returncode, 2, argv)
            body = json.loads(out.stdout)
            assert_valid(body)
            self.assertEqual(body["error"]["code"], "invalid_request", argv)

    def test_missing_repo_root_is_invalid_request(self):
        body, code = call("--repo-root", "/nonexistent/repository", "verify")
        self.assertEqual(code, wp.EXIT_INVALID_REQUEST)
        self.assertEqual(body["error"]["code"], "invalid_request")

    def test_injected_unexpected_exception_is_internal_error_exit_1(self):
        with mock.patch.dict(wp.OPERATIONS, {"describe": mock.Mock(side_effect=RuntimeError("boom"))}), \
                mock.patch("sys.stderr"):
            body, code = call("describe")
        self.assertEqual(code, wp.EXIT_INTERNAL)
        self.assertEqual(body["error"]["code"], "internal_error")
        self.assertIsNone(body["error"]["native"])
        self.assertIn("boom", body["error"]["message"])


# ---------------------------------------------------------------------------
# The Workflow exception domain (D-OP-Errors, LPR-R4-001)
# ---------------------------------------------------------------------------


def workflow_exception_classes(module) -> list[type]:
    return [
        cls for _name, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, Exception) and cls.__module__ == module.__name__
    ]


def instantiate(cls: type) -> BaseException:
    exc = cls.__new__(cls)
    Exception.__init__(exc, "test")
    return exc


class LocalError(Exception):
    pass


class TestWorkflowExceptionDomain(unittest.TestCase):
    def setUp(self):
        self.classes = workflow_exception_classes(ws) + workflow_exception_classes(fingerprint)

    def test_the_enumeration_includes_the_intermediates_and_their_subclasses(self):
        names = {cls.__name__ for cls in self.classes}
        self.assertIn("LifecycleRefusalError", names)
        self.assertIn("PlanApprovalTakeoverRefusedError", names)
        self.assertIn("AmendmentInFlightError", names)
        self.assertTrue(any(
            issubclass(cls, ws.PlanApprovalTakeoverRefusedError) and cls is not ws.PlanApprovalTakeoverRefusedError
            for cls in self.classes))

    def test_every_workflow_class_is_in_the_domain(self):
        for cls in self.classes:
            self.assertTrue(wp.is_workflow_exception(instantiate(cls)), cls)

    def test_builtins_and_foreign_classes_are_not(self):
        for exc in (KeyError("k"), TypeError("t"), ValueError("v"), OSError("o"), LocalError("l")):
            self.assertFalse(wp.is_workflow_exception(exc), exc)

    def test_every_table_key_names_an_enumerated_class(self):
        names = {cls.__name__ for cls in self.classes}
        self.assertLessEqual(set(wp.WORKFLOW_EXCEPTION_CODES), names)

    def test_the_table_is_pinned(self):
        self.assertEqual(wp.WORKFLOW_EXCEPTION_CODES, {
            "InvalidWorkItemIdError": "invalid_request",
            "FeedbackLayoutUndecidableError": "state_unreadable",
            "UnknownFeedbackLayoutError": "state_invalid",
        })
        self.assertLessEqual(set(wp.WORKFLOW_EXCEPTION_CODES.values()), set(wp.ERROR_CODES))

    def test_every_unmapped_class_is_refused(self):
        for cls in self.classes:
            if cls.__name__ in wp.WORKFLOW_EXCEPTION_CODES:
                continue
            self.assertEqual(wp.code_for_workflow_exception(instantiate(cls)), "refused", cls)

    def test_a_class_added_by_a_later_release_is_refused(self):
        future = type("FutureRefusalError", (Exception,), {"__module__": ws.__name__})
        exc = future("later")
        self.assertTrue(wp.is_workflow_exception(exc))
        self.assertEqual(wp.code_for_workflow_exception(exc), "refused")

    def test_the_domain_follows_the_module_objects_not_literals(self):
        spec = importlib.util.spec_from_file_location(
            "renamed_workflow_state", Path(ws.__file__))
        renamed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renamed)
        exc = renamed.CorruptJsonError("x")
        self.assertEqual(type(exc).__module__, "renamed_workflow_state")
        self.assertFalse(wp.is_workflow_exception(exc))
        with mock.patch.object(wp, "workflow_state", renamed):
            self.assertTrue(wp.is_workflow_exception(exc))
            self.assertEqual(wp.code_for_workflow_exception(exc), "refused")

    def test_a_workflow_refusal_from_an_operation_carries_native(self):
        for raised, expected in ((ws.CorruptJsonError("registry"), "refused"),
                                 (fingerprint.FeedbackLayoutUndecidableError("undecidable"), "state_unreadable")):
            with h.ScratchRepo() as repo:
                write_state(repo, h.base_state(wi=item()))
                with mock.patch.object(fingerprint, "resolve_feedback_dir", side_effect=raised):
                    body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                                      "--work-item", "wi", "--kind", "review_feedback")
            self.assertEqual(code, wp.EXIT_REFUSED)
            self.assertEqual(body["error"]["code"], expected)
            self.assertEqual(body["error"]["native"],
                             {"exception": type(raised).__name__, "message": str(raised)})

    def test_an_invalid_state_value_is_state_invalid_with_native(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(feedback_layout="bogus")))
            body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                              "--work-item", "wi", "--kind", "review_feedback")
        self.assertEqual(code, wp.EXIT_REFUSED)
        self.assertEqual(body["error"]["code"], "state_invalid")
        self.assertEqual(body["error"]["native"]["exception"], "UnknownFeedbackLayoutError")

    def test_raw_builtin_from_workflow_code_is_internal_error(self):
        for raised in (KeyError("missing"), OSError("disk")):
            with h.ScratchRepo() as repo:
                write_state(repo, h.base_state(wi=item()))
                with mock.patch.object(ws, "validate_state", side_effect=raised), mock.patch("sys.stderr"):
                    body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                                      "--work-item", "wi", "--kind", "plan_document")
            self.assertEqual(code, wp.EXIT_INTERNAL, raised)
            self.assertEqual(body["error"]["code"], "internal_error", raised)

    def test_unreadable_lock_file_is_state_unreadable(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item()))
            lock = ws.state_lock_path(repo.root)
            lock.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(repo.root / "elsewhere", lock)
            body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                              "--work-item", "wi", "--kind", "plan_document")
        self.assertEqual(code, wp.EXIT_REFUSED)
        self.assertEqual(body["error"]["code"], "state_unreadable")
        self.assertFalse(body["error"]["retryable"])

    def test_missing_and_corrupt_state_are_state_unreadable(self):
        with h.ScratchRepo() as repo:
            body, _ = call("--repo-root", str(repo.root), "resolve-artifact",
                           "--work-item", "wi", "--kind", "plan_document")
            self.assertEqual(body["error"]["code"], "state_unreadable")
            (repo.root / ws.DEFAULT_STATE_PATH).parent.mkdir(parents=True)
            (repo.root / ws.DEFAULT_STATE_PATH).write_text("{not json")
            body, _ = call("--repo-root", str(repo.root), "resolve-artifact",
                           "--work-item", "wi", "--kind", "plan_document")
            self.assertEqual(body["error"]["code"], "state_unreadable")
            self.assertEqual(body["error"]["native"]["exception"], "CorruptJsonError")

    def test_invalid_state_is_state_invalid(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(phase="NOT_A_PHASE")))
            body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                              "--work-item", "wi", "--kind", "plan_document")
        self.assertEqual(code, wp.EXIT_REFUSED)
        self.assertEqual(body["error"]["code"], "state_invalid")
        self.assertIsNotNone(body["error"]["native"])

    def test_unknown_work_item(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state())
            body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                              "--work-item", "absent", "--kind", "plan_document")
        self.assertEqual(code, wp.EXIT_REFUSED)
        self.assertEqual(body["error"]["code"], "unknown_work_item")


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


class TestDescribe(unittest.TestCase):
    def test_lists_equal_the_module_tables(self):
        body, code = call("describe")
        self.assertEqual(code, wp.EXIT_OK)
        result = body["result"]
        self.assertEqual(result["workflow_release"], wp.WORKFLOW_RELEASE)
        self.assertEqual(result["protocol_version"], wp.PROTOCOL_VERSION)
        self.assertEqual(result["supported_protocol_majors"], [wp.PROTOCOL_MAJOR])
        self.assertEqual(result["supported_governing_versions"], sorted(wp.SUPPORTED_GOVERNING_VERSIONS))
        capabilities = result["capabilities"]
        self.assertEqual(capabilities["operations"], sorted(wp.OPERATIONS))
        self.assertEqual(capabilities["dispositions"], sorted(wp.DISPOSITIONS))
        self.assertEqual(capabilities["action_ids"], sorted(wp.ACTION_IDS))
        self.assertEqual(capabilities["artifact_kinds"], sorted(wp.ARTIFACT_KINDS))
        self.assertEqual(capabilities["external_result_kinds"], sorted(wp.EXTERNAL_RESULT_KINDS))
        self.assertEqual(capabilities["reserved_result_kinds"], sorted(wp.RESERVED_RESULT_KINDS))
        self.assertEqual(capabilities["error_codes"], sorted(wp.ERROR_CODES))

    def test_supported_governing_versions_follow_the_two_stage_set(self):
        self.assertEqual(
            sorted(wp.SUPPORTED_GOVERNING_VERSIONS),
            sorted({"1"} | ws.TWO_STAGE_PLAN_REVIEW_VERSIONS))

    def test_the_v1_vocabulary(self):
        self.assertEqual(wp.PROTOCOL_VERSION, "1.2")
        self.assertEqual(wp.PROTOCOL_MAJOR, 1)
        self.assertEqual(
            set(wp.OPERATIONS),
            {"describe", "verify", "resolve-artifact", "next-action", "reconcile", "record-external-result"})
        self.assertEqual(set(wp.EXTERNAL_RESULT_KINDS), {"plan_review_verdict", "implementation_review_verdict",
                                                         "functional_evidence", "pr_review_result"})
        self.assertEqual(set(wp.RESERVED_RESULT_KINDS), set(), "nothing is reserved in protocol 1.2")
        self.assertEqual(set(wp.VERDICT_RESULT_KINDS), {"plan_review_verdict", "implementation_review_verdict"})
        self.assertEqual(
            set(wp.DISPOSITIONS),
            {"automatic", "validation", "human_gate", "external_gate", "blocked", "complete"})
        self.assertEqual(
            {code for code, retryable in wp.ERROR_CODES.items() if retryable}, {"stale_decision"})

    def test_describe_reads_no_state(self):
        with h.ScratchRepo() as repo, \
                mock.patch.object(wp, "read_state_and_config", side_effect=AssertionError("read")):
            body, code = call("--repo-root", str(repo.root), "describe")
        self.assertEqual(code, wp.EXIT_OK)


# ---------------------------------------------------------------------------
# State identity and basis
# ---------------------------------------------------------------------------


class TestStateIdentity(unittest.TestCase):
    def test_stable_under_key_order(self):
        a = h.base_state(wi=item(phase="IMPLEMENTING", plan_revision=2))
        reordered = dict(reversed(list(a["work_items"]["wi"].items())))
        b = {"work_items": {"wi": reordered}, "active_work_item_id": None, "schema_version": 1}
        self.assertEqual(wp.state_identity("wi", a), wp.state_identity("wi", b))

    def test_changes_with_any_work_item_field(self):
        state = h.base_state(wi=item())
        before = wp.state_identity("wi", state)
        for key, value in (("phase", "SELF_REVIEWING_IMPLEMENTATION"), ("state_revision", 2),
                           ("checkpoints", {"CP1": {"status": "COMPLETE"}}), ("new_field", None)):
            changed = json.loads(json.dumps(state))
            changed["work_items"]["wi"][key] = value
            self.assertNotEqual(wp.state_identity("wi", changed), before, key)

    def test_unchanged_by_another_item_or_the_active_id(self):
        state = h.base_state(wi=item(), other=item(work_item_id="other"))
        before = wp.state_identity("wi", state)
        state["work_items"]["other"]["phase"] = "MILESTONE_COMPLETE"
        state["work_items"]["third"] = item(work_item_id="third")
        state["active_work_item_id"] = "other"
        self.assertEqual(wp.state_identity("wi", state), before)

    def test_is_sha256_of_the_canonical_projection(self):
        import hashlib
        state = h.base_state(wi=item())
        expected = hashlib.sha256(json.dumps(
            {"schema_version": 1, "work_item_id": "wi", "work_item": state["work_items"]["wi"]},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(wp.state_identity("wi", state), expected)

    def test_basis_shape(self):
        with h.ScratchRepo() as repo:
            state = h.base_state(wi=item(checkpoints={
                "CP2": {"status": "IN_PROGRESS"}, "CP1": {"status": "COMPLETE"}}))
            result = wp.basis(repo.root, state, "wi")
            self.assertEqual(result["head"], repo.head())
        self.assertEqual(result["checkpoints"], {"CP1": "COMPLETE", "CP2": "IN_PROGRESS"})
        self.assertEqual(result["state_identity"], wp.state_identity("wi", state))
        self.assertEqual(result["state_revision"], 1)
        self.assertEqual(result["phase"], "IMPLEMENTING")
        self.assertEqual(schema_errors(result, SCHEMA["$defs"]["basis"]), [])


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify(unittest.TestCase):
    def verify(self, repo: h.ScratchRepo) -> dict:
        body, code = call("--repo-root", str(repo.root), "verify")
        self.assertEqual(code, wp.EXIT_OK)
        self.assertTrue(body["ok"])
        self.assertEqual([c["id"] for c in body["result"]["checks"]], list(wp.VERIFY_CHECK_IDS))
        return body

    def assert_only_failure(self, body: dict, check_id: str) -> None:
        failed = [c["id"] for c in body["result"]["checks"] if c["status"] == "fail"]
        expected = [check_id]
        if check_id in wp.VERIFY_CHECK_IDS[:4]:
            expected.append("protocol_ready")
        self.assertEqual(failed, expected)
        self.assertFalse(body["result"]["healthy"])

    def test_healthy_on_a_fresh_scratch_repository(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state())
            body = self.verify(repo)
        self.assertTrue(body["result"]["healthy"])
        checks = checks_by_id(body)
        self.assertEqual(checks["installation_release_matches"]["status"], "skip")
        self.assertEqual(checks["protocol_ready"]["status"], "pass")

    def test_corrupt_state(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state()).write_text("{corrupt")
            body = self.verify(repo)
        checks = checks_by_id(body)
        self.assertEqual(checks["state_readable"]["status"], "fail")
        for check_id in ("state_valid", "config_valid", "active_item_resolvable",
                         "checkpoint_completions_provable"):
            self.assertEqual(checks[check_id]["status"], "skip", check_id)
        self.assertEqual(checks["protocol_ready"]["status"], "fail")
        self.assertFalse(body["result"]["healthy"])

    def test_missing_state(self):
        with h.ScratchRepo() as repo:
            body = self.verify(repo)
        self.assertEqual(checks_by_id(body)["state_readable"]["status"], "fail")

    def test_unknown_phase(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(phase="NOT_A_PHASE")))
            body = self.verify(repo)
        self.assert_only_failure(body, "state_valid")

    def test_corrupt_config(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state())
            (repo.root / ws.DEFAULT_CONFIG_PATH).write_text(json.dumps({"schema_version": 99}))
            body = self.verify(repo)
        self.assert_only_failure(body, "config_valid")

    def test_valid_config(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state())
            (repo.root / ws.DEFAULT_CONFIG_PATH).write_text(json.dumps(ws.default_config()))
            body = self.verify(repo)
        self.assertTrue(body["result"]["healthy"])

    def test_dangling_active_id(self):
        with h.ScratchRepo() as repo:
            state = h.base_state(wi=item())
            state["active_work_item_id"] = "absent"
            write_state(repo, state)
            body = self.verify(repo)
        self.assertEqual(checks_by_id(body)["active_item_resolvable"]["status"], "fail")
        self.assertEqual(checks_by_id(body)["protocol_ready"]["status"], "fail")

    def test_active_id_naming_a_terminal_item(self):
        with h.ScratchRepo() as repo:
            state = h.base_state(wi=item(phase="MILESTONE_COMPLETE"))
            state["active_work_item_id"] = "wi"
            write_state(repo, state)
            body = self.verify(repo)
        self.assertEqual(checks_by_id(body)["active_item_resolvable"]["status"], "fail")

    def test_complete_with_no_trailer_commit(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(
                base_commit=repo.base, checkpoints={"CP1": {"status": "COMPLETE"}})))
            body = self.verify(repo)
        self.assert_only_failure(body, "checkpoint_completions_provable")
        self.assertIn("CP1", checks_by_id(body)["checkpoint_completions_provable"]["detail"])

    def test_complete_with_its_trailer_commit(self):
        with h.ScratchRepo() as repo:
            base = repo.base
            repo.commit("cp1", trailers={"Workflow-Checkpoint": "CP1", "Workflow-Work-Item": "wi"})
            write_state(repo, h.base_state(wi=item(
                base_commit=base, checkpoints={"CP1": {"status": "COMPLETE"}})))
            body = self.verify(repo)
        self.assertTrue(body["result"]["healthy"])
        self.assertEqual(checks_by_id(body)["checkpoint_completions_provable"]["status"], "pass")

    def test_v1_item_past_its_implementation_entry_stays_healthy(self):
        """`v2.6.0-003` (a), workflow-2.9.0 CP4: the `"1"` implementation entry
        writes no checkpoint status, so check 5 passes vacuously."""
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(
                governing_workflow_version="1", phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                base_commit=repo.base, checkpoints={"CP1": {"status": "IN_PROGRESS"}})))
            body = self.verify(repo)
        self.assertTrue(body["result"]["healthy"])
        self.assertEqual(checks_by_id(body)["checkpoint_completions_provable"]["status"], "pass")

    def test_terminal_items_are_not_proven(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(
                phase="MILESTONE_COMPLETE", base_commit=repo.base,
                checkpoints={"CP1": {"status": "COMPLETE"}})))
            body = self.verify(repo)
        self.assertEqual(checks_by_id(body)["checkpoint_completions_provable"]["status"], "pass")

    def test_installation_record(self):
        for version, status in (("2.6.0", "fail"), (wp.WORKFLOW_RELEASE, "pass")):
            with h.ScratchRepo() as repo:
                write_state(repo, empty_state())
                record = repo.root / ws.INSTALLATION_RECORD_PATH
                record.parent.mkdir(parents=True)
                record.write_text(json.dumps({"schema_version": 1, "workflow_version": version}))
                body = self.verify(repo)
            self.assertEqual(checks_by_id(body)["installation_release_matches"]["status"], status, version)
            if status == "fail":
                self.assert_only_failure(body, "installation_release_matches")
            else:
                self.assertTrue(body["result"]["healthy"])

    def test_leaves_the_state_untouched(self):
        with h.ScratchRepo() as repo:
            path = write_state(repo, h.base_state(wi=item()))
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            self.verify(repo)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertFalse(ws.state_lock_path(repo.root).exists())


# ---------------------------------------------------------------------------
# resolve-artifact
# ---------------------------------------------------------------------------


class TestResolveArtifact(unittest.TestCase):
    def resolve(self, repo: h.ScratchRepo, work_item_id: str, kind: str) -> dict:
        body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                          "--work-item", work_item_id, "--kind", kind)
        self.assertEqual(code, wp.EXIT_OK, body)
        self.assertEqual(body["result"]["kind"], kind)
        return body["result"]

    def assert_matches_helpers(self, repo: h.ScratchRepo, work_item_id: str, *, plan_stage: bool) -> None:
        feedback_dir = fingerprint.resolve_feedback_dir(repo.root, work_item_id)
        expected = {
            "review_feedback": (feedback_dir / "REVIEW_FEEDBACK.md").as_posix(),
            "functional_review": (feedback_dir / "FUNCTIONAL_REVIEW.md").as_posix(),
            "review_bundle": fingerprint.resolve_bundle_dir(
                repo.root, work_item_id, stage="plan" if plan_stage else None).as_posix(),
            "plan_review_inputs": fingerprint.resolve_plan_review_inputs_dir(repo.root, work_item_id).as_posix(),
            "plan_document": "docs/plans/PLAN.md",
            "functional_checklist": ws.FUNCTIONAL_CHECKLIST_PATH,
        }
        self.assertEqual(set(expected), set(wp.ARTIFACT_KINDS))
        for kind, path in expected.items():
            result = self.resolve(repo, work_item_id, kind)
            self.assertEqual(result["path"], path, kind)
            self.assertEqual(result["exists"], (repo.root / path).exists(), kind)
            self.assertEqual(result["basis"]["work_item_id"], work_item_id)

    def test_scoped_item(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(feedback_layout="scoped", plan_path="docs/plans/PLAN.md")))
            self.assert_matches_helpers(repo, "wi", plan_stage=False)
            self.assertEqual(self.resolve(repo, "wi", "review_feedback")["path"],
                             ".ai-review/wi/feedback/REVIEW_FEEDBACK.md")

    def test_legacy_flat_item(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(plan_path="docs/plans/PLAN.md")))
            self.assert_matches_helpers(repo, "wi", plan_stage=False)
            self.assertEqual(self.resolve(repo, "wi", "review_feedback")["path"],
                             ".ai-review/feedback/REVIEW_FEEDBACK.md")
            self.assertEqual(self.resolve(repo, "wi", "review_bundle")["path"], ".ai-review/current")

    def test_plan_stage_phase(self):
        for phase in ("AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN"):
            with h.ScratchRepo() as repo:
                write_state(repo, h.base_state(wi=item(phase=phase, plan_path="docs/plans/PLAN.md")))
                self.assert_matches_helpers(repo, "wi", plan_stage=True)
                self.assertEqual(self.resolve(repo, "wi", "review_bundle")["path"], ".ai-review/wi/current")

    def test_exists_reflects_the_filesystem(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item(feedback_layout="scoped", plan_path="docs/plans/PLAN.md")))
            self.assertFalse(self.resolve(repo, "wi", "plan_document")["exists"])
            (repo.root / "docs" / "plans").mkdir(parents=True)
            (repo.root / "docs" / "plans" / "PLAN.md").write_text("plan\n")
            self.assertTrue(self.resolve(repo, "wi", "plan_document")["exists"])

    def test_no_plan_path(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item()))
            result = self.resolve(repo, "wi", "plan_document")
        self.assertIsNone(result["path"])
        self.assertFalse(result["exists"])

    def test_unknown_kind_is_invalid_request(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item()))
            body, code = call("--repo-root", str(repo.root), "resolve-artifact",
                              "--work-item", "wi", "--kind", "bundle_archive")
        self.assertEqual(code, wp.EXIT_INVALID_REQUEST)
        self.assertEqual(body["error"]["code"], "invalid_request")

    def test_leaves_the_state_untouched(self):
        with h.ScratchRepo() as repo:
            path = write_state(repo, h.base_state(wi=item(feedback_layout="scoped")))
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            for kind in wp.ARTIFACT_KINDS:
                self.resolve(repo, "wi", kind)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertFalse((repo.root / ".ai-review").exists())

    def test_reads_under_an_existing_lock_file(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=item()))
            with ws.state_lock(repo.root):
                pass
            self.assertTrue(ws.state_lock_path(repo.root).exists())
            self.assertIsNone(self.resolve(repo, "wi", "plan_document")["path"])


# ===========================================================================
# CP4: next-action and reconcile
# ===========================================================================

WI = "wi"
COMMANDS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "commands"

#: The catalogue's printed order (D-OP-Next's table, top to bottom).
PRINTED_ROW_ORDER = (
    "1", "1a", "2", "3", "4", "5", "6", "6a", "7", "7a", "8", "8a", "9", "10", "11", "11a", "12", "13",
    "14", "14a", "14b", "15", "16", "16a", "17", "18", "19", "20", "20a", "21", "22", "23", "24", "25", "25a",
    "25b", "26", "27", "28", "28a", "28b", "29", "30", "30a", "31", "31a", "32", "33", "34", "35", "35a", "36", "37", "38",
    "38a", "38b", "38c", "38d", "38e", "38f", "38g", "38h", "38i", "39", "40",
)


def next_action(repo: h.ScratchRepo, *extra: str) -> dict:
    """`next-action` in-process, schema-checked; every decision is also
    checked for the prose rules: each command its remedy or alternatives
    name is in exactly one of the row's `remedy_commands`/`refusing_commands`,
    and no remedy tells the operator to edit a verdict's binding fields."""
    body, code = call("--repo-root", str(repo.root), "next-action", *extra)
    assert code == 0, body
    result = body["result"]
    if "basis" in result and result["reason"]["code"] != "condition_refused":
        row = wp.ROWS_BY_ID[result["row"]]
        phase = result["snapshot"]["phase"]
        version = result["snapshot"]["governing_workflow_version"]
        named = prose_commands(result)
        remedies = set(row.remedy_commands_for(phase, version))
        refusing = set(row.refusing_commands)
        stray = {command for command in named if (command in remedies) == (command in refusing)}
        assert not stray, f"row {row.row_id} names {sorted(stray)} outside exactly one of its command fields"
        for text in (result["reason"]["remedy"] or "", result["reason"]["text"]):
            assert _EDIT_BINDING_FIELD_RE.search(text) is None, text
    return result


def verdict(status: str | None, *, rcid: str | None = None, bundle: str | None = None, base: str | None = None,
            work_item: str | None = WI, role: str | None = None, extra_body: str = "") -> str:
    lines = ["# Review Decision", ""]
    if status is not None:
        lines += [f"Status: {status}", ""]
    if role is not None:
        lines.append(f"Reviewer role: {role}")
    if bundle is not None:
        lines.append(f"Reviewed bundle ID: {bundle}")
    if base is not None:
        lines.append(f"Reviewed base commit: {base}")
    if work_item is not None:
        lines.append(f"Work item: {work_item}")
    if rcid is not None:
        lines.append(f"{fingerprint.FEEDBACK_REVIEW_CONTENT_ID_LABEL} {rcid}")
    lines += ["", "## Blocking findings", "", "None." + extra_body, ""]
    return "\n".join(lines)


def feedback_path(repo: h.ScratchRepo, name: str = "REVIEW_FEEDBACK.md") -> Path:
    return repo.root / ".ai-review" / WI / "feedback" / name


def write_feedback(repo: h.ScratchRepo, text: str, name: str = "REVIEW_FEEDBACK.md") -> None:
    path = feedback_path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def bundle_dir(repo: h.ScratchRepo) -> Path:
    return repo.root / ".ai-review" / WI / "current"


def current_bundle_id(repo: h.ScratchRepo) -> str:
    return fingerprint.compute_bundle_id(bundle_dir(repo))[0]


def edit_item(repo: h.ScratchRepo, **fields) -> dict:
    state = h.read_state(repo)
    state["work_items"][WI].update(fields)
    h.write_state(repo, state)
    return state


def mutate(repo: h.ScratchRepo, fn, *args, **kwargs) -> dict:
    state = fn(h.read_state(repo), WI, *args, **kwargs)
    h.write_state(repo, state)
    return state


def _demote_checkpoint(state: dict, work_item_id: str, checkpoint_id: str) -> dict:
    entry = state["work_items"][work_item_id]["checkpoints"][checkpoint_id]
    entry["status"] = "NEEDS_REVALIDATION"
    return state


def current_I(repo: h.ScratchRepo) -> str:
    return ws.approval_review_content_id(
        repo.root, stage="implementation", base_commit=h.read_state(repo)["work_items"][WI]["base_commit"],
        work_item_type="process", work_item_id=WI, head="HEAD",
        artifacts_path=fingerprint.artifacts_path_for_work_item(WI))


def reject_bundle(repo: h.ScratchRepo) -> None:
    marker = repo.root / fingerprint.resolve_rejected_marker_path(repo.root, WI)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("rejected by the test\n")


def minimal_item(phase: str, version: str, **overrides) -> dict:
    overrides.setdefault("base_commit", "0" * 40)
    overrides.setdefault("work_item_id", WI)
    return h.base_work_item(phase=phase, governing_workflow_version=version, **overrides)


# -- a bound two-stage plan item at each plan-review phase -----------------


def plan_item_at(repo: h.ScratchRepo, phase: str, version: str = "2.2") -> dict:
    """A two-stage item whose plan bundle is generated and bound, moved
    through the real ledger writers to `phase`. Returns
    `{"P": review_content_id, "B": bundle_id}`."""
    h.seed_bundle_item(repo, governing_workflow_version=version, phase="PLANNING")
    P, B = h.publish_and_bind_plan_bundle(repo)
    if phase in ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_PLAN_APPROVAL"):
        mutate(repo, ws.record_local_plan_review, verdict="APPROVE", bundle_id=B, review_content_id=P,
               round=1, now="t-local")
    if phase == "AWAITING_PLAN_APPROVAL":
        mutate(repo, ws.record_manual_plan_review, verdict="APPROVE", bundle_id=B, round=1, now="t-manual",
               current_review_content_id=P, feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
               feedback_review_content_id=P)
        write_feedback(repo, verdict("APPROVE", rcid=P, bundle=B, base=repo.base,
                                     role="MANUAL_EXTERNAL_PLAN_REVIEW"))
    if phase == "REVISING_PLAN":
        mutate(repo, ws.record_local_plan_review, verdict="REVISE", bundle_id=B, review_content_id=P,
               round=1, now="t-local")
    assert h.read_state(repo)["work_items"][WI]["phase"] == phase
    return {"P": P, "B": B}


def implementation_item_at(repo: h.ScratchRepo, version: str = "2.2", *, registry_checkpoints=None) -> dict:
    """An item whose first implementation bundle is generated from
    `SELF_REVIEWING_IMPLEMENTATION` (2.2: `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`;
    1/2.1: `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`). Returns `{"I", "B"}`."""
    h.seed_bundle_item(repo, governing_workflow_version=version, phase="SELF_REVIEWING_IMPLEMENTATION",
                       registry_checkpoints=registry_checkpoints)
    repo.commit("implement", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
    I = h.generate_implementation_bundle(repo)
    return {"I": I, "B": current_bundle_id(repo)}


def implementation_at_manual(repo: h.ScratchRepo) -> dict:
    ids = implementation_item_at(repo, "2.2")
    mutate(repo, ws.record_local_implementation_review, verdict="APPROVE", bundle_id=ids["B"],
           review_content_id=ids["I"], round=1, now="t-local")
    return ids


def implementation_at_external_2_2(repo: h.ScratchRepo) -> dict:
    ids = implementation_at_manual(repo)
    mutate(repo, ws.record_manual_implementation_review, verdict="APPROVE", bundle_id=ids["B"], round=1,
           now="t-manual", current_review_content_id=ids["I"], feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
           feedback_review_content_id=ids["I"])
    write_feedback(repo, verdict("APPROVE", rcid=ids["I"], bundle=ids["B"], base=repo.base,
                                 role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))
    return ids


def applying_2_2(repo: h.ScratchRepo) -> dict:
    """A `"2.2"` item at `APPLYING_REVIEW_FEEDBACK` after a local `REVISE`."""
    ids = implementation_item_at(repo, "2.2")
    mutate(repo, ws.record_local_implementation_review, verdict="REVISE", bundle_id=ids["B"],
           review_content_id=ids["I"], round=1, now="t-local")
    h.commit_state(repo, "record the local REVISE")
    return ids


# ---------------------------------------------------------------------------
# The catalogue's structure
# ---------------------------------------------------------------------------


class TestCatalogueStructure(unittest.TestCase):
    def test_the_catalogue_is_one_list_in_the_printed_order(self):
        self.assertIsInstance(wp.CATALOGUE, list)
        self.assertEqual(wp.ROW_IDS, PRINTED_ROW_ORDER)
        self.assertEqual(len(set(wp.ROW_IDS)), len(wp.ROW_IDS))

    def test_supported_governing_versions_are_the_two_stage_set_plus_1(self):
        self.assertEqual(sorted(wp.SUPPORTED_GOVERNING_VERSIONS),
                         sorted({"1"} | ws.TWO_STAGE_PLAN_REVIEW_VERSIONS))
        self.assertIn("2.2", wp.SUPPORTED_GOVERNING_VERSIONS)

    def test_every_phase_and_version_reaches_an_unconditional_row(self):
        """Totality, structurally: enumerated from `KNOWN_PHASES` and
        `SUPPORTED_GOVERNING_VERSIONS`, every pair has an explicit
        unconditional row, so nothing can fall through."""
        for phase in sorted(ws.KNOWN_PHASES):
            for version in wp.SUPPORTED_GOVERNING_VERSIONS:
                with self.subTest(phase=phase, version=version):
                    rows = [row for row in wp.CATALOGUE if not row.no_item and row.covers(phase, version)]
                    self.assertTrue(rows, "no row covers the pair")
                    self.assertTrue(any(row.unconditional for row in rows), [row.row_id for row in rows])

    def test_every_phase_and_version_decides_in_a_default_config_repository(self):
        """Totality, dynamically, in a repository whose config is
        `default_config()` (`["1","2.1"]`): the `2.2` rows are still
        enumerated, and every state ends at an explicit row that covers it."""
        self.assertEqual(ws.default_config()["supported_versions"], ["1", "2.1"])
        with h.ScratchRepo() as repo:
            config_path = repo.root / ws.DEFAULT_CONFIG_PATH
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(ws.default_config()))
            for phase in sorted(ws.KNOWN_PHASES):
                for version in wp.SUPPORTED_GOVERNING_VERSIONS:
                    with self.subTest(phase=phase, version=version):
                        write_state(repo, h.base_state(wi=minimal_item(phase, version, base_commit=repo.base)))
                        result = next_action(repo, "--work-item", WI)
                        self.assertTrue(wp.ROWS_BY_ID[result["row"]].covers(phase, version), result["row"])

    def test_condition_calls_cover_exactly_the_rows(self):
        self.assertEqual(set(wp.CONDITION_CALLS), set(wp.ROW_IDS))

    def test_condition_calls_are_well_formed(self):
        domain = {cls.__name__ for module in (ws, fingerprint) for cls in workflow_exception_classes(module)}
        for row_id, calls in wp.CONDITION_CALLS.items():
            for entry in calls:
                with self.subTest(row=row_id, function=entry["function"]):
                    self.assertIn(entry["kind"], ("guard", "acceptance", "value"))
                    self.assertTrue(callable(wp._fn(entry["function"])))
                    for name in entry["classes"]:
                        self.assertIn(name, domain)
                    if entry["kind"] in ("guard", "acceptance"):
                        self.assertTrue(entry["classes"], "a guard or acceptance call lists its classes")

    def test_rows_without_a_condition_have_an_empty_entry(self):
        for row_id in ("2", "3", "4", "6a", "7", "9", "12", "14", "21", "24", "26", "28", "36", "40"):
            self.assertEqual(wp.CONDITION_CALLS[row_id], [], row_id)

    def test_illegal_pairs_are_row_4s(self):
        row_4 = wp.ROWS_BY_ID["4"]
        self.assertIn(("REVISING_PLAN", "1"), row_4.pairs)
        self.assertIn(("SELF_REVIEWING_IMPLEMENTATION", "1"), row_4.pairs)
        self.assertIn(("AWAITING_EXTERNAL_PLAN_REVIEW", "2.2"), row_4.pairs)
        self.assertNotIn(("AWAITING_EXTERNAL_PLAN_REVIEW", "1"), row_4.pairs)


class TestActionsAndEdges(unittest.TestCase):
    def test_action_ids_are_the_action_table(self):
        self.assertEqual(wp.ACTION_IDS, tuple(sorted(wp.ACTIONS)))
        emitted = {row.action_id for row in wp.CATALOGUE if row.action_id}
        self.assertLessEqual(emitted, set(wp.ACTIONS))

    def test_user_only_actions_are_the_users_and_never_automatic(self):
        for action_id, spec in wp.ACTIONS.items():
            if spec["user_only"]:
                self.assertEqual(spec["role"], "user", action_id)
        for row in wp.CATALOGUE:
            if row.disposition == "automatic":
                self.assertFalse(wp.ACTIONS[row.action_id]["user_only"], row.row_id)

    def test_user_only_matches_the_command_files_flag(self):
        """Replaces the Controller's scan for `disable-model-invocation`;
        covers `implementation.recover_provenance`, which is not user-only."""
        for action_id, spec in wp.ACTIONS.items():
            if spec["command"] is None:
                continue
            with self.subTest(action=action_id):
                text = (COMMANDS_DIR / f"{spec['command']}.md").read_text()
                frontmatter = text.split("---", 2)[1]
                self.assertEqual("disable-model-invocation: true" in frontmatter, spec["user_only"])
        self.assertFalse(wp.ACTIONS["implementation.recover_provenance"]["user_only"])

    def test_every_automatic_row_has_an_edge_entry(self):
        automatic = {row.action_id for row in wp.CATALOGUE if row.disposition in ("automatic", "validation")}
        self.assertEqual(automatic, set(wp.EDGES))
        self.assertEqual(set(wp.AUTOMATIC_ACTION_IDS), automatic)

    def test_every_edge_names_known_phases_legal_for_its_version(self):
        for action_id, entry in wp.EDGES.items():
            for edge in entry["edges"]:
                for phase in (edge["from"], edge["to"]):
                    if phase is None:
                        continue
                    with self.subTest(action=action_id, edge=edge):
                        self.assertIn(phase, ws.KNOWN_PHASES)
                        for version in edge["versions"]:
                            self.assertTrue(wp.phase_legal_for_version(phase, version), (phase, version))

    def test_allowed_results_are_reconcile_classes_and_next_action_copies_them(self):
        for action_id, entry in wp.EDGES.items():
            self.assertLessEqual(set(entry["allowed_results"]), set(wp.RECONCILE_CLASSES) - {"invalid"})
            self.assertEqual(wp.render_action(action_id, WI)["allowed_results"], entry["allowed_results"])
        self.assertNotIn("gate_reached", wp.EDGES["implementation.checkpoint"]["allowed_results"])

    def test_functional_apply_findings_edges_follow_the_post_fix_target(self):
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            target = ws.bundle_generation_target_phase("post-fix", version)
            self.assertTrue(wp.edge_is_legal("functional.apply_findings", "AWAITING_FUNCTIONAL_REVIEW", target, version))


_EDIT_BINDING_FIELD_RE = re.compile(
    r"\b(edit|change|set|rewrite|update|replace)\b[^.;]*\b(Reviewed bundle ID|Reviewed base commit|Work item:)",
    re.IGNORECASE)


def assert_no_binding_field_edit(test: unittest.TestCase, result: dict) -> None:
    """No catalogue remedy tells the operator to edit a verdict's binding
    fields (`MPR-R9-001`)."""
    for text in (result["reason"]["remedy"] or "", result["reason"]["text"]):
        test.assertIsNone(_EDIT_BINDING_FIELD_RE.search(text), text)


def write_config(repo: h.ScratchRepo, default: str) -> None:
    path = repo.root / ws.DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "default_workflow_version": default,
                                "supported_versions": ["1", "2.1", "2.2"]}))


def move_head(repo: h.ScratchRepo, subject: str = "unrelated state-only commit") -> str:
    """An excluded-only commit (the state file alone): HEAD moves past the
    bundle's `generation_head`, the protected content does not."""
    state = h.read_state(repo)
    state["work_items"][WI]["last_transition"] = subject
    h.write_state(repo, state)
    return h.commit_state(repo, subject)


def regenerate_wrapper_only(repo: h.ScratchRepo) -> str:
    """A wrapper-only regeneration: the same content, a new `bundle_id`."""
    before = current_bundle_id(repo)
    (bundle_dir(repo) / "TEST_RESULTS.md").write_text(
        f"stage: plan (revision 1)\nhead: {repo.head()}\nwrapper-only rerun\n")
    h._run_generator(repo, "plan", WI)
    after = current_bundle_id(repo)
    assert before != after
    return after


class TestNoItemRows(unittest.TestCase):
    def test_two_stage_default_gives_plan_start(self):
        with h.ScratchRepo() as repo:
            write_config(repo, "2.2")
            write_state(repo, h.base_state())
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("1", "automatic", "plan.start"))
            self.assertEqual(result["action"]["invocation"], "/milestone-plan")
            self.assertNotIn("basis", result)
            self.assertEqual(result["snapshot"], {"work_item_ids": []})

    def test_a_v1_default_gives_row_1a_never_plan_start(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(other=minimal_item("MILESTONE_COMPLETE", "1", work_item_id="other")))
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                             ("1a", "blocked", "plan_start_not_tracked"))
            self.assertIsNone(result["action"])
            self.assertEqual(result["snapshot"], {"work_item_ids": ["other"]})

    def test_expect_state_identity_with_no_item_is_invalid_request(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state())
            body, code = call("--repo-root", str(repo.root), "next-action", "--expect-state-identity", "0" * 64)
            self.assertEqual((code, body["error"]["code"]), (2, "invalid_request"))


class TestFixedRows(unittest.TestCase):
    def decide_minimal(self, phase: str, version: str) -> dict:
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=minimal_item(phase, version, base_commit=repo.base)))
            return next_action(repo, "--work-item", WI)

    def test_vocabulary_only_phases_are_row_2(self):
        for phase in sorted(wp.VOCABULARY_ONLY_PHASES):
            result = self.decide_minimal(phase, "2.2")
            self.assertEqual((result["row"], result["reason"]["code"]), ("2", "invalid_state"))

    def test_legacy_ready_is_row_3(self):
        result = self.decide_minimal("LEGACY_READY", "1")
        self.assertEqual((result["row"], result["reason"]["code"]), ("3", "legacy_item_not_activated"))

    def test_legacy_ready_offers_only_a_user_only_retirement_at_every_version(self):
        """Protocol 1.2 (D-Retire-Protocol): row 3 stays `blocked`, never
        `automatic`, and reports `legacy.retire` as its one alternative."""
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            with self.subTest(version=version):
                result = self.decide_minimal("LEGACY_READY", version)
                self.assertEqual((result["row"], result["disposition"], result["action"]), ("3", "blocked", None))
                (alternative,) = result["alternatives"]
                self.assertEqual(alternative["id"], "legacy.retire")
                self.assertEqual(alternative["invocation"], f"/retire-legacy-work-item {WI}")
                self.assertEqual(alternative["worker"]["role"], "user")
                self.assertTrue(alternative["worker"]["user_only"])
                self.assertIn("/retire-legacy-work-item", result["reason"]["remedy"])

    def test_an_outstanding_checkpoint_at_the_functional_gate_is_row_38c_with_a_user_only_resume(self):
        """Protocol 1.2 (D-Fix-003 (b)): row 38c stays `blocked` and reports
        `implementation.resume` first among its alternatives."""
        for version in ("2.1", "2.2"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                functional_item(repo, version, complete=False)
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["action"]), ("38c", "blocked", None))
                self.assertEqual(result["reason"]["code"], "registry_incomplete")
                ids = [alternative["id"] for alternative in result["alternatives"]]
                self.assertEqual(ids, ["implementation.resume", "functional.apply_findings",
                                       "functional.review.advisory"])
                resume = result["alternatives"][0]
                self.assertEqual(resume["invocation"], f"/resume-implementation {WI}")
                self.assertEqual(resume["worker"]["role"], "user")
                self.assertTrue(resume["worker"]["user_only"])
                self.assertIn(f"/resume-implementation {WI}", result["reason"]["remedy"])
                self.assertNotIn("legacy promotion", result["reason"]["text"])
                row = next(r for r in wp.CATALOGUE if r.row_id == "38c")
                self.assertEqual(row.remedy_commands_for("AWAITING_FUNCTIONAL_REVIEW", version),
                                 ("resume-implementation", "apply-functional-review", "review-functional"))
                self.assertEqual(row.refusing_commands,
                                 ("milestone-implement", "request-plan-amendment", "accept-milestone"))

    def test_row_37_precedes_row_38c_and_a_committed_checklist_reaches_it(self):
        with h.ScratchRepo() as repo:
            functional_item(repo, "2.2", complete=False, evidence=False)
            self.assertEqual(next_action(repo)["row"], "37")
            commit_checklist_evidence(repo, 1)
            self.assertEqual(next_action(repo)["row"], "38c")

    def test_implementation_resume_is_user_only_never_automatic_and_has_no_edge(self):
        self.assertTrue(wp.ACTIONS["implementation.resume"]["user_only"])
        self.assertEqual(wp.ACTIONS["implementation.resume"]["role"], "user")
        self.assertNotIn("implementation.resume", wp.EDGES)
        self.assertNotIn("implementation.resume", wp.AUTOMATIC_ACTION_IDS)
        self.assertNotIn("implementation.resume", NEW_ACTION_IDS)
        self.assertEqual(set(NEW_ACTION_IDS) - set(wp.EDGES), {"functional.evidence.external", "pr.review.external"})

    def test_legacy_retire_is_user_only_and_has_no_edge(self):
        self.assertTrue(wp.ACTIONS["legacy.retire"]["user_only"])
        self.assertNotIn("legacy.retire", wp.EDGES)
        self.assertNotIn("legacy.retire", wp.AUTOMATIC_ACTION_IDS)

    def test_every_illegal_pair_is_row_4(self):
        for phase, versions in sorted(wp.ILLEGAL_PHASE_VERSIONS.items()):
            for version in sorted(versions):
                with self.subTest(phase=phase, version=version):
                    result = self.decide_minimal(phase, version)
                    self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                                     ("4", "blocked", "phase_not_legal_for_governing_version"))

    def test_milestone_complete_is_row_40(self):
        result = self.decide_minimal("MILESTONE_COMPLETE", "2.2")
        self.assertEqual((result["row"], result["disposition"], result["action"]), ("40", "complete", None))

    def test_v1_planning_amending_and_implementing_are_row_6a(self):
        for phase in ("PLANNING", "AMENDING_PLAN", "IMPLEMENTING"):
            with self.subTest(phase=phase), h.ScratchRepo() as repo:
                h.seed_bundle_item(repo, governing_workflow_version="1", phase=phase,
                                   registry_checkpoints=[{"id": "C1", "depends_on": []}])
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                                 ("6a", "blocked", "v1_state_not_advanced"))
                self.assertIsNone(result["action"])
                if phase == "IMPLEMENTING":
                    # workflow-2.9.0 CP4 (`B1`): reported blocked, naming the hand-run command
                    self.assertIn("/milestone-implement", result["reason"]["remedy"])
                    self.assertEqual(wp.ROWS_BY_ID["6a"].remedy_commands_for(phase, "1"), ("milestone-implement",))
                else:
                    self.assertIn("v2.6.0-003", result["reason"]["remedy"])
                    self.assertEqual(wp.ROWS_BY_ID["6a"].remedy_commands_for(phase, "1"), ("none_exists",))

    def test_v1_implementing_without_a_registry_is_the_same_blocked_row(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="IMPLEMENTING")
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                             ("6a", "blocked", "v1_state_not_advanced"))
            self.assertIsNone(result["action"])
            self.assertIn("/milestone-implement", result["reason"]["remedy"])

    def test_no_edge_legalizes_a_v1_move_out_of_implementing(self):
        """A `"1"` item at `IMPLEMENTING` is reported `blocked` (no action), so
        a state that later moved to review has no action to reconcile: no
        automatic action's edge starts at `IMPLEMENTING` for version `"1"`."""
        for action, spec in wp.EDGES.items():
            for edge in spec["edges"]:
                if edge["from"] == "IMPLEMENTING":
                    self.assertNotIn("1", edge["versions"], action)

    def test_v1_self_reviewing_implementation_is_row_4(self):
        self.assertEqual(self.decide_minimal("SELF_REVIEWING_IMPLEMENTATION", "1")["row"], "4")

    def test_an_unsupported_governing_version_is_not_applicable(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=minimal_item("PLANNING", "9.9", base_commit=repo.base)))
            body, code = call("--repo-root", str(repo.root), "next-action", "--work-item", WI)
            self.assertEqual((code, body["error"]["code"]), (3, "not_applicable"))


class TestTwoStagePlanRows(unittest.TestCase):
    def test_planning_is_plan_author(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="PLANNING")
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"], result["action"]["invocation"]),
                             ("7", "plan.author", f"/milestone-plan {WI}"))
            self.assertEqual(result["action"]["worker"]["role"], "planner")

    def test_local_review_is_row_12(self):
        for version in ("2.1", "2.2"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW", version)
                result = next_action(repo)
                self.assertEqual((result["row"], result["action"]["id"]), ("12", "plan.review.local"))
                self.assertEqual(result["action"]["worker"],
                                 {"role": "independent_reviewer", "fresh_session": True,
                                  "independent_of": ["planner"], "user_only": False})
                self.assertEqual(result["snapshot"]["plan_review_publication_status"], "BOUND")

    def test_an_inconsistent_binding_is_row_5(self):
        """Publication row 6 (a non-ready phase with a BOUND record) offers
        no withdrawal, since `/milestone-plan`'s writers refuse it too;
        publication row 4d (a ready phase without BOUND) does."""
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            edit_item(repo, phase="REVISING_PLAN")
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("5", "plan_review_binding_inconsistent"))
            self.assertEqual(result["alternatives"], [])
            with self.assertRaises(ws.PlanReviewBindingInconsistentError):
                ws.publish_plan_revision(h.read_state(repo), WI, 1, "t", review_content_id="e" * 64)
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "REVISING_PLAN")
            edit_item(repo, phase="AWAITING_LOCAL_PLAN_REVIEW")
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("5", "plan_review_binding_inconsistent"))
            self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.withdraw"])

    def test_a_rejected_bundle_is_row_6_at_every_review_and_apply_phase(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"], bundle=ids["B"], base=repo.base))
            self.assertEqual(next_action(repo)["row"], "8")
            reject_bundle(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("6", "bundle_rejected"))
            self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.withdraw"])
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            with self.subTest(version=version), h.ScratchRepo() as repo:
                write_state(repo, h.base_state(wi=minimal_item("APPLYING_REVIEW_FEEDBACK", version,
                                                               base_commit=repo.base, feedback_layout="scoped")))
                reject_bundle(repo)
                result = next_action(repo, "--work-item", WI)
                self.assertEqual((result["row"], result["alternatives"]), ("6", []))

    def test_content_drift_at_a_ready_phase_is_row_10(self):
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan.write_text(plan.read_text() + "drift\n")
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("10", "content_drifted"))
            self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.withdraw"])

    def test_a_current_block_is_review_resolve_block_at_both_review_phases(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            write_feedback(repo, verdict("BLOCK", rcid=ids["P"], bundle=ids["B"], base=repo.base,
                                         role="LOCAL_MODEL_PLAN_REVIEW"))
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("11", "human_gate", "review.resolve_block"))
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            write_feedback(repo, verdict("BLOCK", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW", work_item=None))
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"], result["reason"]["code"]),
                             ("11", "review.resolve_block", "review_blocked"))

    def test_manual_phase_without_and_with_an_unrecorded_verdict(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["satisfied_by"]),
                             ("14", "external_gate", "plan_review_verdict"))
            write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW", work_item=None))
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("13", "plan.record_external"))
            # A local verdict, or one for other content, is not an unrecorded manual verdict.
            write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="LOCAL_MODEL_PLAN_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "14")
            write_feedback(repo, verdict("APPROVE", rcid="e" * 64, role="MANUAL_EXTERNAL_PLAN_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "14")

    def test_a_wrapper_only_regeneration_keeps_rows_11_to_14(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            regenerate_wrapper_only(repo)
            self.assertEqual(next_action(repo)["row"], "12")
            write_feedback(repo, verdict("BLOCK", rcid=ids["P"], bundle=ids["B"], base=repo.base))
            self.assertEqual(next_action(repo)["row"], "11")
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            regenerate_wrapper_only(repo)
            self.assertEqual(next_action(repo)["row"], "14")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"], bundle=ids["B"],
                                         role="MANUAL_EXTERNAL_PLAN_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "13")
            write_feedback(repo, verdict("BLOCK", rcid=ids["P"], bundle=ids["B"],
                                         role="MANUAL_EXTERNAL_PLAN_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "11")

    def test_head_moved_past_generation_head_is_row_11a(self):
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            move_head(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("11a", "bundle_generation_mismatch"))
            self.assertIn(f"prepare-ai-review.sh {repo.base} plan {WI}", result["reason"]["remedy"])
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            move_head(repo)
            self.assertEqual(next_action(repo)["row"], "14", "no fb: row 14 is unaffected")
            write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "11a")

    def test_a_different_worktree_root_is_row_11a(self):
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            other = repo.root.parent / (repo.root.name + "-wt")
            h.git(repo, "worktree", "add", "-q", "--detach", str(other), "HEAD")
            try:
                shutil.copytree(repo.root / ".ai-review", other / ".ai-review")
                shutil.copy(repo.root / ws.DEFAULT_STATE_PATH, other / ws.DEFAULT_STATE_PATH)
                body, code = call("--repo-root", str(other), "next-action")
                self.assertEqual((body["result"]["row"], body["result"]["reason"]["code"]),
                                 ("11a", "bundle_generation_mismatch"))
            finally:
                subprocess.run(["git", "worktree", "remove", "--force", str(other)], cwd=repo.root,
                               capture_output=True)
                shutil.rmtree(other, ignore_errors=True)

    def test_the_plan_approval_gate(self):
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_PLAN_APPROVAL")
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("15", "human_gate", "plan.approve"))
            self.assertEqual(result["action"]["invocation"], f"/approve-review plan {WI}")
            self.assertEqual(result["action"]["worker"]["user_only"], True)

    def test_the_plan_gate_unreachable_causes_are_row_16(self):
        def check(mutation, cause):
            with h.ScratchRepo() as repo:
                ids = plan_item_at(repo, "AWAITING_PLAN_APPROVAL")
                mutation(repo, ids)
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                                 ("16", "blocked", cause))
                self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.withdraw"])

        check(lambda repo, ids: feedback_path(repo).unlink(), "no_review_round")
        check(lambda repo, ids: write_feedback(repo, verdict("BLOCK", rcid=ids["P"])), "review_blocked")
        check(lambda repo, ids: edit_item(repo, plan_review_stages=dict(
            h.read_state(repo)["work_items"][WI]["plan_review_stages"], review_content_id="d" * 64)),
            "review_ledger_stale")
        check(lambda repo, ids: move_head(repo), "bundle_generation_mismatch")


class TestRevisingPlanRows(unittest.TestCase):
    """Rows 7a, 8, 8a and 9, and the content binding (D-Apply-Binding)."""

    def apply_sequence(self, repo: h.ScratchRepo) -> dict:
        """`/apply-plan-review`'s step-0/step-1 guard sequence, before any
        write: the entry phase, the feedback file, the `REJECTED` marker, the
        acceptance rule, and the binding in `"bundle"` mode."""
        state = h.read_state(repo)
        work_item = state["work_items"][WI]
        ws.assert_plan_review_entry_phase(work_item, WI, command="/apply-plan-review")
        text = feedback_path(repo).read_text()
        fingerprint.assert_bundle_not_rejected(repo.root, WI)
        status = ws.plan_review_publication_status(repo.root, state, WI)["status"]
        mode = ws.assert_apply_plan_review_feedback(work_item, WI, feedback_content=text, publication_status=status)
        if mode == "bundle":
            return ws.assert_apply_review_feedback_binding(repo.root, work_item, WI, stage="plan",
                                                           feedback_content=text)
        return {"binding": "durable"}

    def test_a_recorded_revise_is_applied_whatever_its_bundle_fields_say(self):
        variants = {
            "full": lambda ids, repo: verdict("REVISE", rcid=ids["P"], bundle=ids["B"], base=repo.base),
            "no bundle fields": lambda ids, repo: verdict("REVISE", rcid=ids["P"], work_item=None),
            "stale bundle id": lambda ids, repo: verdict("REVISE", rcid=ids["P"], bundle="f" * 64, base=repo.base),
        }
        for version in ("2.1", "2.2"):
            for name, build in variants.items():
                with self.subTest(version=version, variant=name), h.ScratchRepo() as repo:
                    ids = plan_item_at(repo, "REVISING_PLAN", version)
                    write_feedback(repo, build(ids, repo))
                    result = next_action(repo)
                    self.assertEqual((result["row"], result["action"]["id"]), ("8", "plan.apply_review"))
                    binding = self.apply_sequence(repo)
                    self.assertEqual(binding["binding"], "content")
                    if name == "stale bundle id":
                        self.assertIn("advisory only", binding["advisory"])
                    else:
                        self.assertIsNone(binding["advisory"])

    def test_after_a_wrapper_only_regeneration_the_old_bundle_id_is_advisory(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            # The consumed round's bundle, regenerated wrapper-only before the REVISE was recorded.
            write_feedback(repo, verdict("REVISE", rcid=ids["P"], bundle=ids["B"], base=repo.base))
            (bundle_dir(repo) / "TEST_RESULTS.md").write_text("wrapper-only\n")
            # The manifest no longer matches the edited file: that is row 7a, not a binding refusal.
            self.assertEqual(next_action(repo)["row"], "7a")

    def test_a_legacy_marker_is_row_8_by_work_item(self):
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "REVISING_PLAN")
            state = h.read_state(repo)
            work_item = state["work_items"][WI]
            work_item["plan_review_binding"]["consumed"] = {"review_content_id": None, "plan_revision": 1, "legacy": True}
            work_item.pop(ws.CONSUMED_PLAN_REVIEW_CONTENT_IDS_KEY, None)
            h.write_state(repo, state)
            write_feedback(repo, verdict("REVISE"))
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("8", "plan.apply_review"))
            self.assertEqual(self.apply_sequence(repo)["binding"], "durable")

    def test_a_withdrawal_and_a_stale_verdict_are_row_9(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            mutate(repo, ws.withdraw_plan_review, "t-withdraw")
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("9", "plan.author"))
            # A prior round's REVISE (other content): FeedbackContentMismatchError.
            write_feedback(repo, verdict("REVISE", rcid="c" * 64, bundle=ids["B"], base=repo.base))
            self.assertEqual(next_action(repo)["row"], "9")
            # A local APPROVE left behind: FeedbackStatusNotApplicableError.
            write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="LOCAL_MODEL_PLAN_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "9")

    def test_published_unbound_with_feedback_for_other_content_is_row_9(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan.write_text(plan.read_text() + "edited after the REVISE\n")
            fresh, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(repo.root, WI)
            mutate(repo, ws.publish_plan_revision, 1, "t-publish", review_content_id=fresh)
            state = h.read_state(repo)
            self.assertEqual(ws.plan_review_publication_status(repo.root, state, WI)["status"], "PUBLISHED_UNBOUND")
            write_feedback(repo, verdict("REVISE", rcid="c" * 64))
            self.assertEqual(next_action(repo)["row"], "9")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
            self.assertEqual(next_action(repo)["row"], "8", "durable: the consumed content's REVISE applies")

    def test_the_forgery_is_refused_and_gives_row_9(self):
        """MPR-R9-001: bundle fields rewritten to B, the base and the id,
        over a review_content_id that is not the reviewed content."""
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            write_feedback(repo, verdict("REVISE", rcid="c" * 64, bundle=ids["B"], base=repo.base))
            with self.assertRaises(ws.FeedbackContentMismatchError):
                self.apply_sequence(repo)
            self.assertEqual(next_action(repo)["row"], "9")

    def test_a_bundle_of_other_content_is_row_7a(self):
        with h.ScratchRepo() as other:
            plan_item_at(other, "AWAITING_LOCAL_PLAN_REVIEW")
            plan = other.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan.write_text(plan.read_text() + "another revision\n")
            h.git(other, "add", "-A")
            h.git(other, "commit", "-q", "-m", "another revision")
            h.generate_plan_bundle(other)
            with h.ScratchRepo() as repo:
                ids = plan_item_at(repo, "REVISING_PLAN")
                write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
                shutil.rmtree(bundle_dir(repo))
                shutil.copytree(bundle_dir(other), bundle_dir(repo))
                with self.assertRaises(ws.ReviewBundleManifestMismatchError):
                    self.apply_sequence(repo)
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("7a", "bundle_unverified"))
                self.assertIn("ReviewBundleManifestMismatchError", result["reason"]["text"])
                self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.withdraw"])

    def test_a_missing_bundle_directory_is_row_7a(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
            shutil.rmtree(bundle_dir(repo))
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("7a", "bundle_unverified"))
            self.assertIn("MissingRequiredBundleFileError", result["reason"]["text"])

    def test_a_tampered_bundle_is_row_7a(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
            (bundle_dir(repo) / "PLAN.md").write_text("tampered\n")
            self.assertEqual(next_action(repo)["row"], "7a")

    def test_an_unbindable_revise_is_row_8a(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            for text in (verdict("REVISE"),  # names the item, no rcid, no bundle fields
                         verdict("REVISE", bundle="f" * 64, base=repo.base)):  # a bundle id that is not B
                with self.subTest(text=text):
                    write_feedback(repo, text)
                    result = next_action(repo)
                    self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                                     ("8a", "blocked", "review_feedback_unbound"))
                    self.assertIn(ids["B"], result["reason"]["text"])
                    self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.withdraw"])
                    assert_no_binding_field_edit(self, result)
                    with self.assertRaises((fingerprint.MissingFeedbackBindingFieldError,
                                            fingerprint.FeedbackBundleMismatchError)):
                        self.apply_sequence(repo)
            # A verdict that names no item and states no rcid is no applicable REVISE.
            write_feedback(repo, verdict("REVISE", work_item=None))
            self.assertEqual(next_action(repo)["row"], "9")

    def test_a_manifest_naming_no_work_item_selects_the_bundle_binding(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            manifest = bundle_dir(repo) / "MANIFEST.md"
            manifest.write_text("\n".join(line for line in manifest.read_text().splitlines()
                                          if not line.startswith("work_item_id:")) + "\n")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"], work_item=WI))
            state = h.read_state(repo)
            self.assertEqual(ws.apply_review_feedback_binding_selection(
                repo.root, state["work_items"][WI], WI, stage="plan",
                feedback_content=feedback_path(repo).read_text()), "bundle")
            self.assertEqual(next_action(repo)["row"], "8a")

    def test_an_unrecorded_revise_then_a_withdrawal_is_row_8(self):
        """MPR-R9-002: a manual REVISE of the bound content pasted but never
        recorded, then `/milestone-plan <id>`'s withdrawal."""
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW"))
            mutate(repo, ws.withdraw_plan_review, "t-withdraw")
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("8", "plan.apply_review"))
            self.assertEqual(self.apply_sequence(repo)["binding"], "content")
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            # /review-plan wrote its REVISE file, then its state write failed.
            write_feedback(repo, verdict("REVISE", rcid=ids["P"], bundle=ids["B"], base=repo.base,
                                         role="LOCAL_MODEL_PLAN_REVIEW"))
            mutate(repo, ws.withdraw_plan_review, "t-withdraw")
            self.assertEqual(next_action(repo)["row"], "8")
            self.apply_sequence(repo)


class TestV1PlanRound(unittest.TestCase):
    """Rows 16a to 21: a `"1"` item at `AWAITING_EXTERNAL_PLAN_REVIEW`."""

    def v1_plan(self, repo: h.ScratchRepo) -> str:
        h.seed_bundle_item(repo, governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW")
        h.generate_plan_bundle(repo)
        return current_bundle_id(repo)

    def apply_writer_sequence(self, repo: h.ScratchRepo) -> None:
        """`/apply-plan-review`'s `"1"` writers: an edit, then the plan
        bundle's regeneration (a new **B**)."""
        audit = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_AUDIT.md"
        audit.write_text(audit.read_text() + "applied finding\n")
        h.generate_plan_bundle(repo)

    def test_no_feedback_is_row_21(self):
        with h.ScratchRepo() as repo:
            self.v1_plan(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["satisfied_by"]),
                             ("21", "external_gate", "plan_review_verdict"))

    def test_a_current_revise_is_row_18_and_after_the_apply_row_20(self):
        with h.ScratchRepo() as repo:
            B = self.v1_plan(repo)
            write_feedback(repo, verdict("REVISE", bundle=B, base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("18", "plan.apply_review"))
            self.apply_writer_sequence(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("20", "human_gate", "plan.approve"))
            self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.review.external"])
            self.assertEqual(result["alternatives"][0]["satisfied_by"], "plan_review_verdict")

    def test_a_current_approve_is_row_19(self):
        with h.ScratchRepo() as repo:
            B = self.v1_plan(repo)
            write_feedback(repo, verdict("APPROVE", bundle=B, base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("19", "plan.approve"))
            self.assertTrue(result["action"]["worker"]["user_only"])

    def test_a_current_block_is_row_17_and_after_the_apply_row_21(self):
        with h.ScratchRepo() as repo:
            B = self.v1_plan(repo)
            write_feedback(repo, verdict("BLOCK", bundle=B, base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"],
                              result["reason"]["code"]), ("17", "automatic", "plan.apply_review", "review_blocked"))
            self.apply_writer_sequence(repo)
            self.assertEqual(next_action(repo)["row"], "21")

    def test_the_gate_unreachable_is_row_20a(self):
        with h.ScratchRepo() as repo:
            B = self.v1_plan(repo)
            write_feedback(repo, verdict("APPROVE", bundle=B, base=repo.base))
            move_head(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("20a", "bundle_generation_mismatch"))
            self.assertEqual([a["id"] for a in result["alternatives"]], ["plan.review.external"])

    def test_a_stale_feedback_reaches_20_or_21_through_the_acceptance_call(self):
        """`LPR-R5-001`: through real states, never `condition_refused`."""
        with h.ScratchRepo() as repo:
            self.v1_plan(repo)
            write_feedback(repo, verdict("REVISE", bundle="f" * 64, base=repo.base))
            self.assertEqual(next_action(repo)["row"], "20")
            write_feedback(repo, verdict("APPROVE", bundle="f" * 64, base=repo.base))
            self.assertEqual(next_action(repo)["row"], "21")
            write_feedback(repo, verdict("APPROVE", bundle=current_bundle_id(repo)))  # no base commit line
            self.assertEqual(next_action(repo)["row"], "21")

    def test_a_missing_plan_bundle_is_row_16a(self):
        with h.ScratchRepo() as repo:
            self.v1_plan(repo)
            shutil.rmtree(bundle_dir(repo))
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("16a", "bundle_unverified"))
            self.assertIn(f"prepare-ai-review.sh {repo.base} plan {WI}", result["reason"]["remedy"])


class TestImplementingRows(unittest.TestCase):
    CHECKPOINTS = [{"id": "C1", "depends_on": []}, {"id": "C2", "depends_on": ["C1"]}]

    def implementing(self, repo: h.ScratchRepo, version: str = "2.2", phase: str = "IMPLEMENTING") -> None:
        h.seed_bundle_item(repo, governing_workflow_version=version, phase=phase,
                           registry_checkpoints=self.CHECKPOINTS)
        h.approve_plan(repo)

    def test_an_incomplete_registry_is_implementation_checkpoint(self):
        for version in ("2.1", "2.2"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                self.implementing(repo, version)
                result = next_action(repo)
                self.assertEqual((result["row"], result["action"]["id"]), ("23", "implementation.checkpoint"))
                self.assertEqual(result["action"]["arguments"], {"work_item_id": WI, "checkpoint_id": "C1"})
                self.assertEqual(result["action"]["allowed_results"], ["progress", "no_progress"])
                self.assertEqual(result["snapshot"]["next_checkpoint_id"], "C1")
                self.assertIs(result["snapshot"]["registry_complete"], False)

    def test_a_complete_registry_and_self_reviewing_are_self_review(self):
        with h.ScratchRepo() as repo:
            self.implementing(repo)
            edit_item(repo, checkpoints={"C1": {"status": "COMPLETE", "start_commit": repo.base},
                                         "C2": {"status": "COMPLETE", "start_commit": repo.base}})
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"], result["action"]["worker"]["role"]),
                             ("24", "implementation.self_review", "self_reviewer"))
            edit_item(repo, phase="SELF_REVIEWING_IMPLEMENTATION")
            self.assertEqual(next_action(repo)["row"], "24")

    def test_the_implementing_entry_causes_are_row_22(self):
        for phase in ("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"):
            for version in ("2.1", "2.2"):
                with self.subTest(phase=phase, version=version):
                    with h.ScratchRepo() as repo:
                        self.implementing(repo, version, phase)
                        approval = h.read_state(repo)["work_items"][WI]["plan_approval"]
                        edit_item(repo, plan_approval=dict(approval, status="STALE"))
                        result = next_action(repo)
                        self.assertEqual((result["row"], result["reason"]["code"]), ("22", "plan_approval_not_current"))
                    with h.ScratchRepo() as repo:
                        self.implementing(repo, version, phase)
                        plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
                        plan.write_text(plan.read_text() + "edited after approval\n")
                        h.git(repo, "add", "-A")
                        h.git(repo, "commit", "-q", "-m", "edit the approved plan")
                        result = next_action(repo)
                        self.assertEqual((result["row"], result["reason"]["code"]), ("22", "plan_content_drifted"))
                        self.assertIn(f"/request-plan-amendment {WI}", result["reason"]["remedy"])
                    with h.ScratchRepo() as repo:
                        self.implementing(repo, version, phase)
                        state = h.read_state(repo)
                        h.git(repo, "checkout", "-q", "--detach", repo.base)
                        h.write_state(repo, state)
                        result = next_action(repo)
                        self.assertEqual((result["row"], result["reason"]["code"]),
                                         ("22", "plan_approval_commit_unreachable"))

    def test_implementing_entry_status_reports_the_same_causes(self):
        with h.ScratchRepo() as repo:
            self.implementing(repo)
            work_item = h.read_state(repo)["work_items"][WI]
            self.assertEqual(ws.implementing_entry_status(repo.root, work_item, repo.base),
                             {"reachable": True, "cause": None})
            self.assertTrue(ws.implementing_entry_reachable(repo.root, work_item, repo.base))
            stale = dict(work_item, plan_approval=dict(work_item["plan_approval"], status="STALE"))
            self.assertEqual(ws.implementing_entry_status(repo.root, stale, repo.base)["cause"],
                             "plan_approval_not_current")
            self.assertFalse(ws.implementing_entry_reachable(repo.root, stale, repo.base))


class TestImplementationReviewRows2_2(unittest.TestCase):
    def test_local_review(self):
        with h.ScratchRepo() as repo:
            implementation_item_at(repo, "2.2")
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("26", "implementation.review.local"))
            self.assertEqual(result["action"]["worker"]["independent_of"], ["implementer", "self_reviewer"])

    def test_a_current_block_is_review_resolve_block_at_both_phases(self):
        with h.ScratchRepo() as repo:
            ids = implementation_item_at(repo, "2.2")
            write_feedback(repo, verdict("BLOCK", rcid=ids["I"], bundle=ids["B"], base=repo.base,
                                         role="LOCAL_MODEL_IMPLEMENTATION_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "25")
        with h.ScratchRepo() as repo:
            ids = implementation_at_manual(repo)
            write_feedback(repo, verdict("BLOCK", rcid=ids["I"], role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                                         work_item=None))
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("25", "human_gate", "review.resolve_block"))

    def test_the_manual_phase(self):
        with h.ScratchRepo() as repo:
            ids = implementation_at_manual(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["satisfied_by"]), ("28", "implementation_review_verdict"))
            write_feedback(repo, verdict("APPROVE", rcid=ids["I"], role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))
            self.assertEqual(next_action(repo)["action"]["id"], "implementation.record_external")

    def test_head_moved_is_row_25a(self):
        with h.ScratchRepo() as repo:
            implementation_item_at(repo, "2.2")
            move_head(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("25a", "bundle_generation_mismatch"))
            self.assertEqual([a["id"] for a in result["alternatives"]], ["implementation.recover_provenance"])
        with h.ScratchRepo() as repo:
            ids = implementation_at_manual(repo)
            move_head(repo)
            self.assertEqual(next_action(repo)["row"], "28")
            write_feedback(repo, verdict("APPROVE", rcid=ids["I"], role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))
            self.assertEqual(next_action(repo)["row"], "25a")

    def test_bundle_integrity_is_row_25b(self):
        faults = {
            "missing directory": lambda repo: shutil.rmtree(bundle_dir(repo)),
            "required file removed": lambda repo: (bundle_dir(repo) / "MANIFEST.md").unlink(),
            "files differ from the manifest": lambda repo: (bundle_dir(repo) / "DIFF.patch").write_text("x\n"),
            "manifest content is not I": lambda repo: self.reclassify(repo),
        }
        for name, fault in faults.items():
            with self.subTest(fault=name), h.ScratchRepo() as repo:
                implementation_item_at(repo, "2.2")
                fault(repo)
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("25b", "bundle_unverified"))
                self.assertIn(f"prepare-ai-review.sh {repo.base} implementation {WI}", result["reason"]["remedy"])
                with self.assertRaises(ws.ImplementationReviewBundleUnverifiedError):
                    ws.verify_implementation_review_bundle(repo.root, WI)
            with self.subTest(fault=name, phase="manual"), h.ScratchRepo() as repo:
                ids = implementation_at_manual(repo)
                write_feedback(repo, verdict("APPROVE", rcid=ids["I"], role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))
                fault(repo)
                self.assertEqual(next_action(repo)["row"], "25b")
        with h.ScratchRepo() as repo:
            implementation_at_manual(repo)
            shutil.rmtree(bundle_dir(repo))
            self.assertEqual(next_action(repo)["row"], "25b", "no fb and no bundle: never row 28")

    @staticmethod
    def reclassify(repo: h.ScratchRepo) -> None:
        """Moves the implementation content out of the protected set: the
        current **I** moves, HEAD and the bundle do not."""
        path = repo.root / "docs" / "ai-workflow" / "registry" / f"{WI}-artifacts.json"
        declarations = json.loads(path.read_text())
        stage = declarations["implementation_stage"]
        del stage["protected_paths"][h.BUNDLE_ITEM_IMPLEMENTATION_PATH]
        stage["excluded_paths"][h.BUNDLE_ITEM_IMPLEMENTATION_PATH] = "reclassified by the test"
        path.write_text(json.dumps(declarations) + "\n")

    def test_the_remedy_names_post_fix_after_a_revise_round(self):
        with h.ScratchRepo() as repo:
            applying_2_2(repo)
            repo.commit("fix", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
            h.generate_implementation_bundle(repo, stage="post-fix")
            self.assertEqual(h.read_state(repo)["work_items"][WI]["implementation_revision"], 2)
            shutil.rmtree(bundle_dir(repo))
            result = next_action(repo)
            self.assertEqual(result["row"], "25b")
            self.assertIn(f"prepare-ai-review.sh {repo.base} post-fix {WI}", result["reason"]["remedy"])

    def test_the_technical_gate(self):
        with h.ScratchRepo() as repo:
            implementation_at_external_2_2(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("29", "human_gate", "implementation.approve"))

    def test_the_technical_gate_causes_are_row_30(self):
        def check(mutation, cause, alternatives=(), remedy=None):
            with self.subTest(cause=cause), h.ScratchRepo() as repo:
                ids = implementation_at_external_2_2(repo)
                mutation(repo, ids)
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("30", cause))
                self.assertEqual([a["id"] for a in result["alternatives"]], list(alternatives))
                if remedy:
                    self.assertIn(remedy(repo), result["reason"]["remedy"])

        check(lambda repo, ids: feedback_path(repo).unlink(), "no_review_round")
        check(lambda repo, ids: write_feedback(repo, verdict("BLOCK", rcid=ids["I"])), "review_blocked",
              ["implementation.apply_review"])
        check(lambda repo, ids: mutate(repo, ws.record_technical_review_block_pin, bundle_id=ids["B"],
                                       review_content_id=ids["I"], now="t-pin"),
              "review_block_pinned", ["implementation.apply_review"])
        check(lambda repo, ids: (repo.root / h.BUNDLE_ITEM_IMPLEMENTATION_PATH).write_text("dirty\n"),
              "protected_path_dirty")
        check(lambda repo, ids: edit_item(repo, reviewed_implementation_head=repo.base),
              "implementation_provenance_stale", ["implementation.recover_provenance"])
        check(lambda repo, ids: edit_item(repo, implementation_review_stages=dict(
            h.read_state(repo)["work_items"][WI]["implementation_review_stages"], review_content_id="d" * 64)),
            "review_ledger_stale")
        check(lambda repo, ids: move_head(repo), "bundle_generation_mismatch", ["implementation.recover_provenance"])
        check(lambda repo, ids: shutil.rmtree(bundle_dir(repo)), "bundle_unverified", (),
              lambda repo: f"prepare-ai-review.sh {repo.base} implementation {WI}")


class TestImplementationReviewRowsV1(unittest.TestCase):
    """Rows 30a to 35 at `"1"` and `"2.1"`."""

    def external(self, repo: h.ScratchRepo, version: str) -> dict:
        return implementation_item_at(repo, version)

    def pin_then_post_fix(self, repo: h.ScratchRepo, ids: dict) -> None:
        """`/apply-implementation-review`'s writers for a current `BLOCK`:
        the pin, the phase, a fix, then the post-fix republication."""
        mutate(repo, ws.record_technical_review_block_pin, bundle_id=ids["B"], review_content_id=ids["I"], now="t-pin")
        mutate(repo, ws.enter_applying_review_feedback, "t-apply")
        h.commit_state(repo, "pin and enter")
        repo.commit("fix the blocking finding", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
        h.generate_implementation_bundle(repo, stage="post-fix")

    def test_rows_by_version(self):
        for version in ("1", "2.1"):
            with self.subTest(version=version):
                with h.ScratchRepo() as repo:
                    self.external(repo, version)
                    result = next_action(repo)
                    self.assertEqual((result["row"], result["satisfied_by"]), ("35", "implementation_review_verdict"))
                    self.assertEqual([a["id"] for a in result["alternatives"]], ["implementation.review.local"])
                with h.ScratchRepo() as repo:
                    ids = self.external(repo, version)
                    write_feedback(repo, verdict("REVISE", bundle=ids["B"], base=repo.base))
                    self.assertEqual(next_action(repo)["row"], "32")
                    write_feedback(repo, verdict("APPROVE", bundle=ids["B"], base=repo.base))
                    result = next_action(repo)
                    self.assertEqual((result["row"], result["action"]["id"]), ("33", "implementation.approve"))
                    (repo.root / h.BUNDLE_ITEM_IMPLEMENTATION_PATH).write_text("dirty\n")
                    result = next_action(repo)
                    self.assertEqual((result["row"], result["reason"]["code"]), ("34", "protected_path_dirty"))
                    self.assertEqual([a["id"] for a in result["alternatives"]], ["implementation.recover_provenance"])
                with h.ScratchRepo() as repo:
                    ids = self.external(repo, version)
                    write_feedback(repo, verdict("APPROVE", bundle="f" * 64, base=repo.base))
                    self.assertEqual(next_action(repo)["row"], "35", "a stale fb is not current")

    def test_a_current_block_is_row_31_and_after_the_apply_row_35(self):
        for version in ("1", "2.1"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                ids = self.external(repo, version)
                write_feedback(repo, verdict("BLOCK", bundle=ids["B"], base=repo.base))
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["action"]["id"],
                                  result["reason"]["code"]),
                                 ("31", "automatic", "implementation.apply_review", "review_blocked"))
                self.pin_then_post_fix(repo, ids)
                self.assertEqual(next_action(repo)["row"], "35")

    def test_a_pinned_block_edited_to_revise_is_row_31(self):
        with h.ScratchRepo() as repo:
            ids = self.external(repo, "2.1")
            mutate(repo, ws.record_technical_review_block_pin, bundle_id=ids["B"], review_content_id=ids["I"], now="t")
            write_feedback(repo, verdict("REVISE", bundle=ids["B"], base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("31", "review_block_pinned"))

    def test_a_pinned_block_without_a_current_fb_is_row_31a(self):
        for version in ("1", "2.1"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                ids = self.external(repo, version)
                mutate(repo, ws.record_technical_review_block_pin, bundle_id=ids["B"], review_content_id=ids["I"],
                       now="t")
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                                 ("31a", "external_gate", "review_block_pinned"))
                write_feedback(repo, verdict("BLOCK", bundle=ids["B"], base=repo.base, work_item="other"))
                self.assertEqual(next_action(repo)["row"], "31a")
                write_feedback(repo, verdict("BLOCK", bundle=ids["B"], base=repo.base))
                self.assertEqual(next_action(repo)["row"], "31")

    def test_an_applied_revise_with_a_reachable_gate_offers_approval(self):
        with h.ScratchRepo() as repo:
            ids = self.external(repo, "2.1")
            write_feedback(repo, verdict("REVISE", bundle=ids["B"], base=repo.base))
            mutate(repo, ws.enter_applying_review_feedback, "t-apply")
            h.commit_state(repo, "enter")
            repo.commit("fix", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
            h.generate_implementation_bundle(repo, stage="post-fix")
            result = next_action(repo)
            self.assertEqual(result["row"], "35")
            self.assertEqual([a["id"] for a in result["alternatives"]],
                             ["implementation.review.local", "implementation.approve"])
            self.assertTrue(result["alternatives"][1]["worker"]["user_only"])

    def test_a_missing_bundle_is_row_30a(self):
        for version in ("1", "2.1"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                self.external(repo, version)
                shutil.rmtree(bundle_dir(repo))
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("30a", "bundle_unverified"))
                self.assertIn(f"prepare-ai-review.sh {repo.base} implementation {WI}", result["reason"]["remedy"])


class TestApplyingRows(unittest.TestCase):
    """Rows 35a and 36, and the implementation-stage content binding."""

    def apply_sequence(self, repo: h.ScratchRepo) -> dict:
        """`/apply-implementation-review` step 1: the feedback file, the
        `REJECTED` marker, then the binding."""
        state = h.read_state(repo)
        text = ws.read_review_feedback(repo.root, WI)
        if text is None:
            raise FileNotFoundError("no REVIEW_FEEDBACK.md")
        fingerprint.assert_bundle_not_rejected(repo.root, WI)
        return ws.assert_apply_review_feedback_binding(repo.root, state["work_items"][WI], WI,
                                                       stage="implementation", feedback_content=text)

    def test_content_bound_revise_variants_are_row_36(self):
        variants = {
            "no bundle fields": lambda ids, repo: verdict("REVISE", rcid=ids["I"], work_item=None),
            "stale bundle id": lambda ids, repo: verdict("REVISE", rcid=ids["I"], bundle="f" * 64, base=repo.base),
        }
        for name, build in variants.items():
            with self.subTest(variant=name), h.ScratchRepo() as repo:
                ids = applying_2_2(repo)
                write_feedback(repo, build(ids, repo))
                self.assertEqual(next_action(repo)["row"], "36")
                self.assertEqual(self.apply_sequence(repo)["binding"], "content")
                repo.commit("a fix the applier committed", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
                self.assertNotEqual(current_I(repo), ids["I"])
                result = next_action(repo)
                self.assertEqual((result["row"], result["action"]["id"]), ("36", "implementation.apply_review"))
                self.apply_sequence(repo)

    def test_the_forgery_is_refused_and_gives_row_35a(self):
        with h.ScratchRepo() as repo:
            ids = applying_2_2(repo)
            write_feedback(repo, verdict("REVISE", rcid="c" * 64, bundle=ids["B"], base=repo.base))
            with self.assertRaises(ws.FeedbackContentMismatchError):
                self.apply_sequence(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "review_feedback_not_current"))
            self.assertIn("FeedbackContentMismatchError", result["reason"]["text"])
            assert_no_binding_field_edit(self, result)

    def test_a_tampered_or_missing_bundle_is_row_35a_bundle_unverified(self):
        for fault in (lambda repo: (bundle_dir(repo) / "DIFF.patch").write_text("x\n"),
                      lambda repo: shutil.rmtree(bundle_dir(repo))):
            with h.ScratchRepo() as repo:
                ids = applying_2_2(repo)
                write_feedback(repo, verdict("REVISE", rcid=ids["I"]))
                fault(repo)
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "bundle_unverified"))
                with self.assertRaises((ws.ReviewBundleManifestMismatchError, fingerprint.MissingRequiredBundleFileError)):
                    self.apply_sequence(repo)

    def test_a_manifest_of_another_base_or_stage_is_refused_by_check_3(self):
        with h.ScratchRepo() as repo:
            ids = applying_2_2(repo)
            write_feedback(repo, verdict("REVISE", rcid=ids["I"]))
            edit_item(repo, base_commit=h.git(repo, "rev-parse", f"{repo.base}~1"))
            with self.assertRaisesRegex(ws.ReviewBundleManifestMismatchError, "base_commit"):
                self.apply_sequence(repo)
        with h.ScratchRepo() as plan_repo:
            plan_item_at(plan_repo, "AWAITING_LOCAL_PLAN_REVIEW")
            with h.ScratchRepo() as repo:
                ids = applying_2_2(repo)
                plan_manifest = (bundle_dir(plan_repo) / "MANIFEST.md").read_text()
                self.assertIn("stage: plan", plan_manifest)
                shutil.rmtree(bundle_dir(repo))
                shutil.copytree(bundle_dir(plan_repo), bundle_dir(repo))
                manifest = fingerprint.read_plan_stage_manifest_fields(bundle_dir(repo) / "MANIFEST.md")
                write_feedback(repo, verdict("REVISE", rcid=manifest["review_content_id"]))
                with self.assertRaisesRegex(ws.ReviewBundleManifestMismatchError, "stage|base_commit"):
                    self.apply_sequence(repo)
                self.assertEqual(next_action(repo)["row"], "35a")
        with h.ScratchRepo() as impl_repo:
            implementation_item_at(impl_repo, "2.2")
            with h.ScratchRepo() as repo:
                ids = plan_item_at(repo, "REVISING_PLAN")
                shutil.rmtree(bundle_dir(repo))
                shutil.copytree(bundle_dir(impl_repo), bundle_dir(repo))
                write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
                state = h.read_state(repo)
                with self.assertRaises(ws.ReviewBundleManifestMismatchError):
                    ws.assert_apply_review_feedback_binding(repo.root, state["work_items"][WI], WI, stage="plan",
                                                            feedback_content=feedback_path(repo).read_text())
                self.assertEqual(next_action(repo)["row"], "7a")

    def test_another_items_flat_bundle_is_refused_by_its_owner(self):
        """MPR-R10-001: item X on the flat layout, the shared
        `.ai-review/current/` holding item Y's bundle and Y's `REVISE` with
        no `Work item:`."""
        with h.ScratchRepo() as other:
            h.seed_bundle_item(other, "y", phase="SELF_REVIEWING_IMPLEMENTATION")
            other.commit("implement y", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
            h.generate_implementation_bundle(other, "y")
            y_bundle = other.root / ".ai-review" / "y" / "current"
            y_manifest = fingerprint.read_plan_stage_manifest_fields(y_bundle / "MANIFEST.md")
            with h.ScratchRepo() as repo:
                applying_2_2(repo)
                shutil.rmtree(repo.root / ".ai-review")
                state = h.read_state(repo)
                state["work_items"][WI].pop("feedback_layout")
                h.write_state(repo, state)
                shutil.copytree(y_bundle, repo.root / ".ai-review" / "current")
                y_text = verdict("REVISE", rcid=y_manifest["review_content_id"], work_item=None)
                flat_feedback = repo.root / ".ai-review" / "feedback" / "REVIEW_FEEDBACK.md"
                flat_feedback.parent.mkdir(parents=True)
                flat_feedback.write_text(y_text)
                self.assertEqual(fingerprint.resolve_bundle_dir(repo.root, WI), Path(".ai-review/current"))
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "bundle_unverified"))
                self.assertIn("another item", result["reason"]["text"])
                with self.assertRaises(ws.ReviewBundleManifestMismatchError):
                    self.apply_sequence(repo)

    def test_a_manifest_naming_no_work_item_takes_the_bundle_binding(self):
        with h.ScratchRepo() as repo:
            ids = applying_2_2(repo)
            manifest = bundle_dir(repo) / "MANIFEST.md"
            manifest.write_text("\n".join(line for line in manifest.read_text().splitlines()
                                          if not line.startswith("work_item_id:")) + "\n")
            write_feedback(repo, verdict("REVISE", rcid=ids["I"], work_item=None))
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "review_feedback_not_current"))
            with self.assertRaises(fingerprint.MissingFeedbackBindingFieldError):
                self.apply_sequence(repo)
            write_feedback(repo, verdict("REVISE", rcid=ids["I"], bundle=current_bundle_id(repo), base=repo.base))
            self.assertEqual(next_action(repo)["row"], "36")
            self.assertEqual(self.apply_sequence(repo)["binding"], "bundle")

    def test_the_apply_rows_preemptions_at_every_version(self):
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            with self.subTest(version=version), h.ScratchRepo() as repo:
                if version == "2.2":
                    ids = applying_2_2(repo)
                    good = verdict("REVISE", rcid=ids["I"], bundle=ids["B"], base=repo.base)
                else:
                    ids = implementation_item_at(repo, version)
                    good = verdict("REVISE", bundle=ids["B"], base=repo.base)
                    write_feedback(repo, good)
                    mutate(repo, ws.enter_applying_review_feedback, "t-apply")
                    feedback_path(repo).unlink()
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "review_feedback_missing"))
                self.assertIn(".ai-review/wi/feedback/REVIEW_FEEDBACK.md", result["reason"]["text"])
                write_feedback(repo, verdict("REVISE", bundle="f" * 64, base=repo.base, work_item="another"))
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "review_feedback_missing"))
                self.assertIn("'another'", result["reason"]["text"])
                feedback_path(repo).unlink()
                with self.assertRaises(FileNotFoundError):
                    self.apply_sequence(repo)
                write_feedback(repo, verdict("REVISE", bundle="f" * 64, base=repo.base))
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "review_feedback_not_current"))
                with self.assertRaises(fingerprint.FeedbackBundleMismatchError):
                    self.apply_sequence(repo)
                write_feedback(repo, verdict("REVISE", base=repo.base, bundle=None))
                self.assertEqual(next_action(repo)["reason"]["code"], "review_feedback_not_current")
                write_feedback(repo, good)
                result = next_action(repo)
                self.assertEqual((result["row"], result["action"]["id"]), ("36", "implementation.apply_review"))
                self.assertEqual(self.apply_sequence(repo)["binding"], "content" if version == "2.2" else "bundle")
                reject_bundle(repo)
                self.assertEqual(next_action(repo)["row"], "6")
                with self.assertRaises(fingerprint.BundleRejectedError):
                    self.apply_sequence(repo)
                (repo.root / fingerprint.resolve_rejected_marker_path(repo.root, WI)).unlink()
                shutil.rmtree(bundle_dir(repo))
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("35a", "bundle_unverified"))
                with self.assertRaises(fingerprint.MissingRequiredBundleFileError):
                    self.apply_sequence(repo)


CHECKPOINT_C1 = [{"id": "C1", "depends_on": []}]


def commit_checklist_evidence(repo: h.ScratchRepo, revision) -> str:
    """`/prepare-functional-review`'s checklist-evidence commit for `revision`."""
    path = repo.root / ws.FUNCTIONAL_CHECKLIST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Active milestone\n\n## Functional review checklist\n\n- exercise the flow\n")
    h.git(repo, "add", "--", ws.FUNCTIONAL_CHECKLIST_PATH)
    blob = h.git(repo, "hash-object", "--", ws.FUNCTIONAL_CHECKLIST_PATH)
    h.git(repo, "commit", "-q", "-m",
          f"checklist\n\nWorkflow-Functional-Checklist: {WI}/{revision}/{blob}\nWorkflow-Work-Item: {WI}")
    return blob


def functional_item(repo: h.ScratchRepo, version: str, *, complete: bool = True, evidence: bool = True) -> None:
    """An item at `AWAITING_FUNCTIONAL_REVIEW` with a covering `CURRENT`
    plan approval, its registry's one checkpoint `COMPLETE` (or not), and
    committed checklist evidence for its round."""
    h.seed_bundle_item(repo, governing_workflow_version=version, phase="AWAITING_FUNCTIONAL_REVIEW",
                       registry_checkpoints=CHECKPOINT_C1, implementation_revision=1)
    if complete:
        edit_item(repo, checkpoints={"C1": {"status": "COMPLETE", "start_commit": repo.base}})
    h.approve_plan(repo)
    if evidence:
        commit_checklist_evidence(repo, 1)


class TestFunctionalRows(unittest.TestCase):
    def test_no_evidence_is_functional_prepare(self):
        with h.ScratchRepo() as repo:
            functional_item(repo, "2.2", evidence=False)
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("37", "functional.prepare"))
            commit_checklist_evidence(repo, 1)
            (repo.root / ws.FUNCTIONAL_CHECKLIST_PATH).write_text("edited after its evidence commit\n")
            self.assertEqual(next_action(repo)["row"], "37", "the evidence blob is not the live checklist")

    def test_a_terminal_registry_is_the_functional_gate(self):
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            with self.subTest(version=version), h.ScratchRepo() as repo:
                functional_item(repo, version)
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                                 ("39", "human_gate", "functional.review"))
                self.assertEqual([a["id"] for a in result["alternatives"]],
                                 ["milestone.accept", "functional.apply_findings", "functional.review.advisory"])
                self.assertTrue(result["alternatives"][0]["worker"]["user_only"])

    def test_unconsumed_findings_are_row_38_and_consumed_ones_row_39(self):
        with h.ScratchRepo() as repo:
            functional_item(repo, "2.2")
            write_feedback(repo, "# Functional review\n\n- a finding\n", name="FUNCTIONAL_REVIEW.md")
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("38", "functional.apply_findings"))
            fingerprint.mark_functional_review_consumed(repo.root, WI)
            self.assertEqual(next_action(repo)["row"], "39")

    def test_the_registry_read_refusals_are_row_38a(self):
        for version in ("1", "2.2"):
            with self.subTest(version=version, cause="plan_content_drifted"), h.ScratchRepo() as repo:
                functional_item(repo, version)
                plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
                plan.write_text(plan.read_text() + "edited after approval\n")
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                                 ("38a", "blocked", "plan_content_drifted"))
                self.assertIn("restore the approved plan-stage bytes", result["reason"]["remedy"])
                self.assertNotIn("/request-plan-amendment", result["reason"]["remedy"])
                self.assertNotIn("milestone.accept", [a["id"] for a in result["alternatives"]])
            with self.subTest(version=version, cause="registry_unreadable"), h.ScratchRepo() as repo:
                functional_item(repo, version)
                registry = repo.root / "docs" / "ai-workflow" / "registry" / f"{WI}-registry.json"
                data = json.loads(registry.read_text())
                data["work_item_id"] = "another-item"
                registry.write_text(json.dumps(data) + "\n")
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("38a", "registry_unreadable"))

    def test_a_v1_incomplete_registry_is_row_38b(self):
        with h.ScratchRepo() as repo:
            functional_item(repo, "1", complete=False)
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                             ("38b", "blocked", "v1_state_not_advanced"))
            self.assertEqual([a["id"] for a in result["alternatives"]],
                             ["functional.apply_findings", "functional.review.advisory"])

    def test_a_two_stage_incomplete_registry_is_row_38c(self):
        for version in ("2.1", "2.2"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                functional_item(repo, version, complete=False)
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), ("38c", "registry_incomplete"))
                self.assertIn("v2.6.0-003", result["reason"]["text"])
                self.assertIn(f"/resume-implementation {WI}", result["reason"]["remedy"])
                self.assertNotIn("/request-plan-amendment", result["reason"]["remedy"])
                self.assertIn("resume-implementation", wp.ROWS_BY_ID["38c"].remedy_commands_for("AWAITING_FUNCTIONAL_REVIEW", version))
                self.assertNotIn("implementation.checkpoint", [a["id"] for a in result["alternatives"]])

    def test_a_promoted_legacy_item_with_an_incomplete_registry_is_row_38c(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="LEGACY_READY",
                               registry_checkpoints=CHECKPOINT_C1, implementation_revision=1)
            h.approve_plan(repo)
            milestone = repo.root / "docs" / "ACTIVE_MILESTONE.md"
            milestone.write_text("# wi\n\naccepted and closed\n")
            h.git(repo, "add", "-A")
            h.git(repo, "commit", "-q", "-m", "integrate the legacy branch")
            state = h.read_state(repo)
            state["work_items"][WI]["technical_approval"] = ws.build_approval_record(
                basis="LEGACY_V1", stage="implementation", user_confirmation="legacy import of wi implementation",
                now="t", approved_review_content_id="a" * 64, reviewed_content_commit=repo.head(),
                legacy_evidence={"rounds": 1})
            state = ws.promote_legacy_work_item(
                state, repo.root, work_item_id=WI, required_active_milestone_substring="accepted and closed",
                artifacts_path=fingerprint.artifacts_path_for_work_item(WI), now="t-promote")
            self.assertEqual((state["work_items"][WI]["governing_workflow_version"],
                              state["work_items"][WI]["phase"]), ("2.1", "AWAITING_FUNCTIONAL_REVIEW"))
            h.write_state(repo, state)
            commit_checklist_evidence(repo, 1)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("38c", "registry_incomplete"))

    def test_a_v1_terminal_or_registry_less_item_lists_milestone_accept(self):
        with h.ScratchRepo() as repo:
            functional_item(repo, "1")
            self.assertEqual(next_action(repo)["row"], "39")
            edit_item(repo, registry_path=None, checkpoints={})
            result = next_action(repo)
            self.assertEqual(result["row"], "39")
            self.assertIn("milestone.accept", [a["id"] for a in result["alternatives"]])


class TestRaisingConditions(unittest.TestCase):
    """`LPR-R4-002`, `LPR-R5-001`: a raising condition call."""

    def test_an_unmapped_workflow_exception_from_a_value_call_is_condition_refused(self):
        with h.ScratchRepo() as repo:
            functional_item(repo, "2.2", evidence=False)
            with mock.patch.object(ws, "discover_current_functional_checklist_evidence",
                                   side_effect=ws.CorruptJsonError("evidence store corrupt")):
                body, code = call("--repo-root", str(repo.root), "next-action")
            self.assertEqual((code, body["ok"]), (0, True))
            result = body["result"]
            self.assertEqual((result["row"], result["disposition"], result["action"], result["reason"]["code"]),
                             ("37", "blocked", None, "condition_refused"))
            self.assertEqual(result["reason"]["native"],
                             {"exception": "CorruptJsonError", "message": "evidence store corrupt"})
            self.assertIn("CorruptJsonError", result["reason"]["text"])

    def test_a_builtin_from_a_value_call_is_internal_error(self):
        with h.ScratchRepo() as repo:
            functional_item(repo, "2.2", evidence=False)
            with mock.patch.object(ws, "discover_current_functional_checklist_evidence", side_effect=KeyError("x")):
                body, code = call("--repo-root", str(repo.root), "next-action")
            self.assertEqual((code, body["error"]["code"]), (1, "internal_error"))

    def assert_acceptance(self, build, target, function, listed, *, falls_to: str, at: str):
        """Patched to raise each listed class, the acceptance call falls
        through; patched to raise an unlisted Workflow exception, it is
        `condition_refused` at its first row."""
        for cls in listed:
            with self.subTest(function=function, raised=cls.__name__), h.ScratchRepo() as repo:
                build(repo)
                with mock.patch.object(target, function, side_effect=cls("patched")):
                    self.assertEqual(next_action(repo)["row"], falls_to)
        with self.subTest(function=function, raised="unlisted"), h.ScratchRepo() as repo:
            build(repo)
            with mock.patch.object(target, function, side_effect=ws.CorruptJsonError("patched")):
                result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), (at, "condition_refused"))

    def test_the_apply_plan_acceptance(self):
        def build(repo):
            ids = plan_item_at(repo, "REVISING_PLAN")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
        self.assert_acceptance(build, ws, "assert_apply_plan_review_feedback",
                               [ws.FeedbackStatusNotApplicableError, ws.FeedbackNotForConsumedContentError],
                               falls_to="9", at="7a")
        self.assert_acceptance(build, ws, "assert_apply_review_feedback_binding",
                               [ws.FeedbackContentMismatchError], falls_to="9", at="7a")

    def test_the_bundle_binding_acceptance(self):
        def build(repo):
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW")
            h.generate_plan_bundle(repo)
            write_feedback(repo, verdict("REVISE", bundle=current_bundle_id(repo), base=repo.base))
        self.assert_acceptance(build, fingerprint, "assert_feedback_matches_bundle",
                               [fingerprint.FeedbackBundleMismatchError, fingerprint.MissingFeedbackBindingFieldError],
                               falls_to="20", at="17")

    def test_the_functional_review_acceptance(self):
        def build(repo):
            functional_item(repo, "2.2")
            write_feedback(repo, "# Functional review\n", name="FUNCTIONAL_REVIEW.md")
        self.assert_acceptance(build, fingerprint, "assert_functional_review_not_already_consumed",
                               [fingerprint.FunctionalReviewAlreadyAppliedError], falls_to="39", at="38")


# ---------------------------------------------------------------------------
# Every call a row reaches is declared (`LPR-R5-001`, `LPR-R6-003`)
# ---------------------------------------------------------------------------


def _scenarios() -> dict:
    """Builders for a spread of states across the catalogue."""
    def plan_revise(repo):
        ids = plan_item_at(repo, "REVISING_PLAN")
        write_feedback(repo, verdict("REVISE", rcid=ids["P"]))

    def plan_manual(repo):
        ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW"))

    def v1_plan(repo):
        h.seed_bundle_item(repo, governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW")
        h.generate_plan_bundle(repo)
        write_feedback(repo, verdict("APPROVE", bundle=current_bundle_id(repo), base=repo.base))

    def implementing(repo):
        h.seed_bundle_item(repo, phase="IMPLEMENTING", registry_checkpoints=CHECKPOINT_C1)
        h.approve_plan(repo)

    def impl_manual(repo):
        ids = implementation_at_manual(repo)
        write_feedback(repo, verdict("APPROVE", rcid=ids["I"], role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))

    def v1_external(repo):
        ids = implementation_item_at(repo, "2.1")
        write_feedback(repo, verdict("REVISE", bundle="f" * 64, base=repo.base))

    def applying(repo):
        ids = applying_2_2(repo)
        write_feedback(repo, verdict("REVISE", rcid=ids["I"]))

    def functional(repo):
        functional_item(repo, "2.2")
        write_feedback(repo, "# Functional review\n", name="FUNCTIONAL_REVIEW.md")
        fingerprint.mark_functional_review_consumed(repo.root, WI)

    return {
        "plan.local": lambda repo: plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW"),
        "plan.revise": plan_revise, "plan.manual": plan_manual,
        "plan.approval": lambda repo: plan_item_at(repo, "AWAITING_PLAN_APPROVAL"),
        "v1.plan": v1_plan, "implementing": implementing,
        "impl.local": lambda repo: implementation_item_at(repo, "2.2"),
        "impl.manual": impl_manual, "impl.external": implementation_at_external_2_2,
        "v1.external": v1_external, "applying": applying, "functional": functional,
        "no item": lambda repo: write_state(repo, h.base_state()),
    }


class TestDeclaredCalls(unittest.TestCase):
    def test_every_reached_condition_call_is_declared_by_its_row(self):
        """Each `CONDITION_CALLS` function is patched to a recorder. Every
        call a row makes goes through its declared entry (`_Context.call`
        refuses an undeclared one, so `ctx.reached` holds only declared
        pairs), and no listed function is ever called by a predicate outside
        such a call -- a bypass of the table."""
        names = sorted(wp._CONDITION_FUNCTION_MODULES)
        bypasses: list[tuple[str, str]] = []

        def evaluating_context():
            frame = inspect.currentframe()
            while frame is not None:
                for key in ("ctx", "self"):
                    candidate = frame.f_locals.get(key)
                    if isinstance(candidate, wp._Context):
                        return candidate
                frame = frame.f_back
            return None

        def recorder(name, original):
            def wrapped(*args, **kwargs):
                ctx = evaluating_context()
                if ctx is not None and ctx.row_id is not None and ctx.in_call is None:
                    bypasses.append((ctx.row_id, name))
                return original(*args, **kwargs)
            return wrapped

        for scenario, build in _scenarios().items():
            with self.subTest(scenario=scenario), h.ScratchRepo() as repo:
                build(repo)
                state = h.read_state(repo)
                patches = [mock.patch.object(wp._CONDITION_FUNCTION_MODULES[name], name,
                                             recorder(name, getattr(wp._CONDITION_FUNCTION_MODULES[name], name)))
                           for name in names]
                for patch in patches:
                    patch.start()
                try:
                    bypasses.clear()
                    _decision, ctx = wp.evaluate_catalogue(repo.root, state, state.get("active_work_item_id"))
                finally:
                    for patch in patches:
                        patch.stop()
                self.assertEqual(bypasses, [])
                for row_id, function in ctx.reached:
                    declared = {entry["function"] for entry in wp.CONDITION_CALLS[row_id]}
                    self.assertIn(function, declared, f"row {row_id} reached {function}")

    def test_an_undeclared_call_is_refused(self):
        ctx = wp._Context(Path("."), h.base_state(wi=minimal_item("PLANNING", "2.2")), WI)
        ctx.row_id = "7"
        with self.assertRaises(AssertionError):
            ctx.call("load_config", lambda: None)

    def test_a_value_reused_from_an_earlier_row_counts_for_every_row_that_reads_it(self):
        """Row 8 reuses row 5's `plan_review_publication_status`, and must
        still declare it."""
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
            state = h.read_state(repo)
            _decision, ctx = wp.evaluate_catalogue(repo.root, state, WI)
            rows_reading = {row for row, function in ctx.reached if function == "plan_review_publication_status"}
            self.assertEqual(rows_reading, {"5", "7a", "8"})


# ---------------------------------------------------------------------------
# Command agreement (LPR-R2-002, MPR-R11-001)
# ---------------------------------------------------------------------------


def _command_text(command: str) -> str:
    return (COMMANDS_DIR / f"{command}.md").read_text()


def _fb_text(repo: h.ScratchRepo) -> str | None:
    return ws.read_review_feedback(repo.root, WI)


def _guard_plan_author(repo, state):
    work_item = state["work_items"][WI]
    ws.assert_plan_review_entry_phase(work_item, WI, command="/milestone-plan")
    ws.load_config(repo.root)


def _guard_plan_apply_review(repo, state):
    work_item = state["work_items"][WI]
    two_stage = work_item["governing_workflow_version"] in ws.TWO_STAGE_PLAN_REVIEW_VERSIONS
    if two_stage:
        ws.assert_plan_review_entry_phase(work_item, WI, command="/apply-plan-review")
    text = _fb_text(repo)
    if text is None:
        raise FileNotFoundError("no REVIEW_FEEDBACK.md")
    fingerprint.assert_bundle_not_rejected(repo.root, WI)
    mode = "bundle"
    if two_stage:
        status = ws.plan_review_publication_status(repo.root, state, WI)["status"]
        mode = ws.assert_apply_plan_review_feedback(work_item, WI, feedback_content=text, publication_status=status)
    if mode == "bundle":
        return ws.assert_apply_review_feedback_binding(repo.root, work_item, WI, stage="plan", feedback_content=text)
    return None


def _guard_plan_review_local(repo, state):
    fingerprint.assert_local_generation_matches(repo.root, bundle_dir(repo) / "MANIFEST.md")
    fingerprint.assert_bundle_not_rejected(repo.root, WI)
    ws.assert_plan_review_bundle_bound(repo.root, WI, state=state)
    ws.validate_local_plan_review_preconditions(state["work_items"][WI])


def _guard_plan_record_external(repo, state):
    """The record-manual command's path since CP5: the shared ingest."""
    return ws.ingest_manual_review_verdict(
        repo.root, WI, stage="plan", verdict_text=_fb_text(repo), now="t", two_stage_only=True)


def _guard_implementing_entry(repo, state):
    work_item = state["work_items"][WI]
    status = ws.implementing_entry_status(repo.root, work_item, work_item["base_commit"])
    if not status["reachable"]:
        raise AssertionError(f"implementing entry refused: {status['cause']}")


def _guard_checkpoint(repo, state):
    _guard_implementing_entry(repo, state)
    registry = json.loads((repo.root / state["work_items"][WI]["registry_path"]).read_text())
    checkpoint_id = ws.select_next_checkpoint(state["work_items"][WI], registry)
    ws.transition_checkpoint_in_progress(state, WI, checkpoint_id, start_commit=repo.head(), now="t")


def _guard_self_review(repo, state):
    _guard_implementing_entry(repo, state)
    registry = json.loads((repo.root / state["work_items"][WI]["registry_path"]).read_text())
    ws.enter_self_reviewing_implementation(state, WI, registry, now="t")


def _guard_implementation_review_local(repo, state):
    ws.verify_implementation_review_bundle(repo.root, WI, state=state)
    fingerprint.assert_local_generation_matches(repo.root, bundle_dir(repo) / "MANIFEST.md")
    fingerprint.assert_bundle_not_rejected(repo.root, WI)
    ws.validate_local_implementation_review_preconditions(state["work_items"][WI])


def _guard_implementation_record_external(repo, state):
    return ws.ingest_manual_review_verdict(
        repo.root, WI, stage="implementation", verdict_text=_fb_text(repo), now="t", two_stage_only=True)


def _guard_implementation_apply_review(repo, state):
    if state["work_items"][WI]["phase"] != "APPLYING_REVIEW_FEEDBACK":
        ws.enter_applying_review_feedback(state, WI, now="t")
    text = _fb_text(repo)
    if text is None:
        raise FileNotFoundError("no REVIEW_FEEDBACK.md")
    fingerprint.assert_bundle_not_rejected(repo.root, WI)
    return ws.assert_apply_review_feedback_binding(repo.root, state["work_items"][WI], WI,
                                                   stage="implementation", feedback_content=text)


def _guard_functional_prepare(repo, state):
    if state["work_items"][WI]["phase"] != "AWAITING_FUNCTIONAL_REVIEW":
        raise AssertionError("not at the functional gate")


def _guard_functional_apply(repo, state):
    if not feedback_path(repo, "FUNCTIONAL_REVIEW.md").is_file():
        raise FileNotFoundError("no FUNCTIONAL_REVIEW.md")
    fingerprint.assert_functional_review_not_already_consumed(repo.root, WI)


#: Per automatic action: the command's pre-write guard sequence (as a
#: callable over the row's state), and the names the command document must
#: carry for it. `plan.record_external`/`implementation.record_external`
#: name the shared ingest from CP5, which rewrites those two commands; their
#: document check is CP5's.
def _guard_satisfy(stage):
    """`/satisfy-gate <stage>`'s record builder: refuses (`GateNotSatisfiableError`)
    unless the gate is automatic and every requirement is met."""
    def guard(repo, state):
        return ws.build_policy_approval_record(repo.root, state, WI, stage, now="t-sat")
    return guard


def _guard_satisfy_acceptance(repo, state):
    evaluation = wgp.evaluate_gate(repo.root, state, WI, "acceptance")
    assert evaluation["mode"] == "automatic" and (
        evaluation["satisfiable"] or evaluation["satisfiable_after_query"]), evaluation
    return evaluation


def _guard_pr_apply_review(repo, state):
    policy = wgp.effective_policy(repo.root, state)["policy"]
    assert wgp.pr_query_trigger(state, WI, policy) or wgp.actionable_pr_keys(repo.root, state, WI, policy)


COMMAND_GUARDS = {
    "plan.satisfy": (_guard_satisfy("plan"), ["build_policy_approval_record"]),
    "implementation.satisfy": (_guard_satisfy("implementation"), ["build_policy_approval_record"]),
    "acceptance.satisfy": (_guard_satisfy_acceptance, ["satisfy_acceptance_gate"]),
    "pr.apply_review": (_guard_pr_apply_review, ["begin_pr_review"]),
    "plan.author": (_guard_plan_author, ["assert_plan_review_entry_phase", "WORKFLOW_CONFIG.json", "route_work_item"]),
    "plan.apply_review": (_guard_plan_apply_review, [
        "assert_plan_review_entry_phase", "REVIEW_FEEDBACK.md", "assert_bundle_not_rejected",
        "assert_apply_plan_review_feedback", "assert_apply_review_feedback_binding"]),
    "plan.review.local": (_guard_plan_review_local, [
        "assert_local_generation_matches", "assert_bundle_not_rejected", "assert_plan_review_bundle_bound",
        "validate_local_plan_review_preconditions"]),
    "plan.record_external": (_guard_plan_record_external, ["ingest_manual_review_verdict", "two_stage_only=True"]),
    "implementation.checkpoint": (_guard_checkpoint, ["implementing_entry_status", "transition_checkpoint_in_progress"]),
    "implementation.self_review": (_guard_self_review, ["implementing_entry_status", "enter_self_reviewing_implementation"]),
    "implementation.review.local": (_guard_implementation_review_local, [
        "verify_implementation_review_bundle", "assert_local_generation_matches", "assert_bundle_not_rejected",
        "validate_local_implementation_review_preconditions"]),
    "implementation.record_external": (_guard_implementation_record_external, [
        "ingest_manual_review_verdict", "two_stage_only=True"]),
    "implementation.apply_review": (_guard_implementation_apply_review, [
        "REVIEW_FEEDBACK.md", "assert_bundle_not_rejected", "assert_apply_review_feedback_binding"]),
    "functional.prepare": (_guard_functional_prepare, ["AWAITING_FUNCTIONAL_REVIEW"]),
    "functional.apply_findings": (_guard_functional_apply, ["assert_functional_review_not_already_consumed"]),
}


def _automatic_scenarios() -> dict:
    """`(builder, expected row)` for automatic rows at each `gv` they cover."""
    def revise(variant):
        def build(repo, version):
            ids = plan_item_at(repo, "REVISING_PLAN", version)
            write_feedback(repo, {"plain": verdict("REVISE", rcid=ids["P"], bundle=ids["B"], base=repo.base),
                                  "no fields": verdict("REVISE", rcid=ids["P"], work_item=None),
                                  "stale id": verdict("REVISE", rcid=ids["P"], bundle="f" * 64)}[variant])
        return build

    def withdrawn(repo, version):
        plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW", version)
        mutate(repo, ws.withdraw_plan_review, "t")

    def manual(repo, version):
        ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", version)
        write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW"))

    def v1_plan(status):
        def build(repo, version):
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW")
            h.generate_plan_bundle(repo)
            write_feedback(repo, verdict(status, bundle=current_bundle_id(repo), base=repo.base))
        return build

    def implementing(complete):
        def build(repo, version):
            h.seed_bundle_item(repo, governing_workflow_version=version, phase="IMPLEMENTING",
                               registry_checkpoints=CHECKPOINT_C1)
            h.approve_plan(repo)
            if complete:
                edit_item(repo, checkpoints={"C1": {"status": "COMPLETE", "start_commit": repo.base}})
        return build

    def impl_manual(repo, version):
        ids = implementation_at_manual(repo)
        write_feedback(repo, verdict("APPROVE", rcid=ids["I"], role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))

    def applying(variant):
        def build(repo, version):
            if version == "2.2":
                ids = applying_2_2(repo)
                write_feedback(repo, {"plain": verdict("REVISE", rcid=ids["I"], bundle=ids["B"], base=repo.base),
                                      "no fields": verdict("REVISE", rcid=ids["I"], work_item=None),
                                      "stale id": verdict("REVISE", rcid=ids["I"], bundle="f" * 64)}[variant])
            else:
                ids = implementation_item_at(repo, version)
                write_feedback(repo, verdict("REVISE", bundle=ids["B"], base=repo.base))
                mutate(repo, ws.enter_applying_review_feedback, "t")
        return build

    def v1_external(status):
        def build(repo, version):
            ids = implementation_item_at(repo, version)
            write_feedback(repo, verdict(status, bundle=ids["B"], base=repo.base))
        return build

    def functional(findings):
        def build(repo, version):
            functional_item(repo, version, evidence=findings)
            if findings:
                write_feedback(repo, "# Functional review\n", name="FUNCTIONAL_REVIEW.md")
        return build

    two = ("2.1", "2.2")
    return [
        ("7", two, lambda repo, version: h.seed_bundle_item(repo, governing_workflow_version=version, phase="PLANNING")),
        ("9", two, withdrawn),
        ("8", two, revise("plain")), ("8", two, revise("no fields")), ("8", two, revise("stale id")),
        ("12", two, lambda repo, version: plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW", version)),
        ("13", two, manual),
        ("17", ("1",), v1_plan("BLOCK")), ("18", ("1",), v1_plan("REVISE")),
        ("23", two, implementing(False)), ("24", two, implementing(True)),
        ("26", ("2.2",), lambda repo, version: implementation_item_at(repo, "2.2")),
        ("27", ("2.2",), impl_manual),
        ("31", ("1", "2.1"), v1_external("BLOCK")), ("32", ("1", "2.1"), v1_external("REVISE")),
        ("36", ("1", "2.1", "2.2"), applying("plain")), ("36", ("2.2",), applying("no fields")),
        ("36", ("2.2",), applying("stale id")),
        ("37", ("1", "2.1", "2.2"), functional(False)), ("38", ("1", "2.1", "2.2"), functional(True)),
    ]


class TestCommandAgreement(unittest.TestCase):
    def test_every_automatic_action_has_a_guard_sequence(self):
        self.assertEqual(set(COMMAND_GUARDS), set(wp.EDGES) - {"plan.start"})

    def test_each_command_document_names_its_guards(self):
        for action_id, (_guard, names) in COMMAND_GUARDS.items():
            if names is None:
                continue
            text = _command_text(wp.ACTIONS[action_id]["command"])
            for name in names:
                with self.subTest(action=action_id, name=name):
                    self.assertIn(name, text)
        self.assertIn("route_work_item", _command_text("milestone-plan"))

    def test_neither_apply_command_binds_with_assert_feedback_matches_bundle_itself(self):
        for command in ("apply-plan-review", "apply-implementation-review"):
            step1 = _command_text(command).split("\n1. ", 1)[1].split("\n2. ", 1)[0]
            self.assertIn("workflow_state.assert_apply_review_feedback_binding(repo_root", step1)
            self.assertNotIn("parse_review_feedback_binding_fields`/", step1)

    def test_every_automatic_row_is_accepted_by_its_commands_guards(self):
        for row_id, versions, build in _automatic_scenarios():
            for version in versions:
                with self.subTest(row=row_id, version=version), h.ScratchRepo() as repo:
                    build(repo, version)
                    result = next_action(repo)
                    self.assertEqual((result["row"], result["disposition"]), (row_id, "automatic"))
                    guard, _names = COMMAND_GUARDS[result["action"]["id"]]
                    outcome = guard(repo, h.read_state(repo))
                    if row_id == "8" and isinstance(outcome, dict):
                        self.assertEqual(outcome["binding"], "content")
                    if row_id == "36" and isinstance(outcome, dict):
                        self.assertEqual(outcome["binding"], "content" if version == "2.2" else "bundle")


#: Each command's phase gate, evaluated on `(phase, gv)`.
def _accepts_milestone_plan(phase, version):
    try:
        ws.assert_plan_review_entry_phase(minimal_item(phase, version), WI, command="/milestone-plan")
        return version in ws.TWO_STAGE_PLAN_REVIEW_VERSIONS
    except ws.PlanReviewPhaseNotPlanStageError:
        return False


def _accepts_apply_implementation_review(phase, version):
    if phase == "APPLYING_REVIEW_FEEDBACK":
        return True
    try:
        ws.enter_applying_review_feedback(h.base_state(wi=minimal_item(phase, version)), WI, now="t")
    except Exception as exc:  # noqa: BLE001 -- the phase refusal
        if not wp.is_workflow_exception(exc):
            raise
        return False
    return True


COMMAND_PHASE_GATES = {
    "milestone-plan": _accepts_milestone_plan,
    "request-plan-amendment": lambda phase, version: phase in ws._AMENDMENT_REQUEST_ALLOWED_PHASES,
    "recover-implementation-provenance":
        lambda phase, version: phase in ws.bundle_generation_recovered_role_legal_committed_phases(version),
    "apply-implementation-review": _accepts_apply_implementation_review,
    "review-implementation": lambda phase, version: phase == "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW" or (
        version == "2.2" and phase == "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"),
    "approve-review": lambda phase, version: (
        phase == "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
        or (version in ws.TWO_STAGE_PLAN_REVIEW_VERSIONS and phase == "AWAITING_PLAN_APPROVAL")
        or (version == "1" and phase == "AWAITING_EXTERNAL_PLAN_REVIEW")),
    "apply-functional-review": lambda phase, version: phase == "AWAITING_FUNCTIONAL_REVIEW",
    "review-functional": lambda phase, version: phase == "AWAITING_FUNCTIONAL_REVIEW",
    "accept-milestone": lambda phase, version: phase == "AWAITING_FUNCTIONAL_REVIEW",
    "milestone-implement": lambda phase, version: phase in ws.CHECKPOINT_START_LEGAL_PHASES,
    "retire-legacy-work-item": lambda phase, version: phase == "LEGACY_READY",
    "resume-implementation": lambda phase, version: (
        phase == "AWAITING_FUNCTIONAL_REVIEW" and version in ws.TWO_STAGE_PLAN_REVIEW_VERSIONS),
}

_SLASH_COMMAND_RE = re.compile(r"(?<![\w./-])/([a-z][a-z-]+)\b")


def prose_commands(result: dict) -> set[str]:
    texts = [result["reason"]["text"], result["reason"]["remedy"] or ""]
    texts += [alternative["invocation"] or "" for alternative in result["alternatives"]]
    return {match.group(1) for text in texts for match in _SLASH_COMMAND_RE.finditer(text)} & set(COMMAND_PHASE_GATES)


class TestRemedyCommands(unittest.TestCase):
    def test_every_remedy_command_accepts_the_rows_phase(self):
        for row in wp.CATALOGUE:
            if row.no_item or row.disposition not in ("blocked", "human_gate", "external_gate"):
                continue
            for phase, version in sorted(row.pairs):
                for command in row.remedy_commands_for(phase, version):
                    if command == "none_exists":
                        continue
                    with self.subTest(row=row.row_id, phase=phase, version=version, command=command):
                        self.assertTrue(COMMAND_PHASE_GATES[command](phase, version))

    def test_no_command_is_both_a_remedy_and_a_refusal(self):
        for row in wp.CATALOGUE:
            for phase, version in row.pairs:
                self.assertFalse(set(row.remedy_commands_for(phase, version)) & set(row.refusing_commands), row.row_id)

    def test_the_rows_without_a_route_say_so(self):
        # Row 38c names `/resume-implementation` since 2.9.0, so it is no longer a "none exists" row.
        for row_id in ("6a", "38b"):
            row = wp.ROWS_BY_ID[row_id]
            phase, version = sorted(row.pairs)[0]
            self.assertIn("none_exists", row.remedy_commands_for(phase, version))

    def test_refusing_commands_refuse_the_rows_state(self):
        """Every command a row names only to say that it refuses does
        refuse that row's state."""
        def accept_milestone_refuses(repo, state):
            try:
                terminal, _ = ws.resolve_own_registry_completion_status(repo.root, state["work_items"][WI])
            except (ws.StalePlanApprovalRegistryReadError, ws.RegistryCoverageError):
                return True
            return not terminal

        def milestone_implement_refuses(repo, state):
            try:
                ws.transition_checkpoint_in_progress(state, WI, "C1", start_commit=repo.head(), now="t")
            except ws.IllegalCheckpointStartPhaseError:
                return True
            return False

        def amendment_refuses(repo, state):
            return state["work_items"][WI]["phase"] not in ws._AMENDMENT_REQUEST_ALLOWED_PHASES

        refusals = {"accept-milestone": accept_milestone_refuses, "milestone-implement": milestone_implement_refuses,
                    "request-plan-amendment": amendment_refuses}

        def drifted(repo):
            functional_item(repo, "2.2")
            plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan.write_text(plan.read_text() + "drift\n")

        cases = [("38a", drifted), ("38b", lambda repo: functional_item(repo, "1", complete=False)),
                 ("38c", lambda repo: functional_item(repo, "2.2", complete=False))]
        for row_id, build in cases:
            with h.ScratchRepo() as repo:
                build(repo)
                self.assertEqual(next_action(repo)["row"], row_id)
                for command in wp.ROWS_BY_ID[row_id].refusing_commands:
                    with self.subTest(row=row_id, command=command):
                        self.assertTrue(refusals[command](repo, h.read_state(repo)))


class TestWriterSequencesEndOnLegalEdges(unittest.TestCase):
    """For each automatic `(row, gv)`, the command's writer sequence on the
    row's state ends on one of the action's legal edges."""

    def assert_edge(self, action_id, before, after, version):
        self.assertTrue(wp.edge_is_legal(action_id, before, after, version), (action_id, before, after, version))

    def test_plan_rows(self):
        for version in ("2.1", "2.2"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                h.seed_bundle_item(repo, governing_workflow_version=version, phase="PLANNING")
                h.publish_and_bind_plan_bundle(repo)
                self.assert_edge("plan.author", "PLANNING", h.read_state(repo)["work_items"][WI]["phase"], version)
            with self.subTest(version=version, row="12 and 13"), h.ScratchRepo() as repo:
                ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW", version)
                for status, expected in (("APPROVE", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"), ("REVISE", "REVISING_PLAN")):
                    state = ws.record_local_plan_review(h.read_state(repo), WI, verdict=status, bundle_id=ids["B"],
                                                        review_content_id=ids["P"], round=1, now="t")
                    self.assert_edge("plan.review.local", "AWAITING_LOCAL_PLAN_REVIEW",
                                     state["work_items"][WI]["phase"], version)
                    self.assertEqual(state["work_items"][WI]["phase"], expected)
            with self.subTest(version=version, row="8"), h.ScratchRepo() as repo:
                plan_item_at(repo, "REVISING_PLAN", version)
                plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
                plan.write_text(plan.read_text() + "the applied finding\n")
                h.publish_and_bind_plan_bundle(repo)
                self.assert_edge("plan.apply_review", "REVISING_PLAN", h.read_state(repo)["work_items"][WI]["phase"],
                                 version)

    def test_implementation_rows(self):
        for version in ("2.1", "2.2"):
            with self.subTest(version=version, row="23"), h.ScratchRepo() as repo:
                h.seed_bundle_item(repo, governing_workflow_version=version, phase="IMPLEMENTING",
                                   registry_checkpoints=CHECKPOINT_C1)
                registry = json.loads((repo.root / "docs/ai-workflow/registry/wi-registry.json").read_text())
                state = ws.transition_checkpoint_in_progress(h.read_state(repo), WI, "C1", start_commit=repo.head(),
                                                             now="t")
                self.assert_edge("implementation.checkpoint", "IMPLEMENTING", state["work_items"][WI]["phase"], version)
                state = ws.complete_checkpoint(state, WI, "C1", registry, now="t", repo_root=repo.root)
                self.assert_edge("implementation.checkpoint", "IMPLEMENTING", state["work_items"][WI]["phase"], version)
                state = ws.record_bundle_generation(state, WI, stage="implementation", head=repo.head(), now="t")
                self.assert_edge("implementation.self_review", "IMPLEMENTING", state["work_items"][WI]["phase"], version)
        with h.ScratchRepo() as repo:
            ids = implementation_item_at(repo, "2.2")
            for status in ("APPROVE", "REVISE"):
                state = ws.record_local_implementation_review(h.read_state(repo), WI, verdict=status,
                                                              bundle_id=ids["B"], review_content_id=ids["I"],
                                                              round=1, now="t")
                self.assert_edge("implementation.review.local", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                                 state["work_items"][WI]["phase"], "2.2")
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            with self.subTest(version=version, row="36"):
                state = h.base_state(wi=minimal_item("APPLYING_REVIEW_FEEDBACK", version, implementation_revision=1))
                state = ws.record_bundle_generation(state, WI, stage="post-fix", head="a" * 40, now="t")
                self.assert_edge("implementation.apply_review", "APPLYING_REVIEW_FEEDBACK",
                                 state["work_items"][WI]["phase"], version)
        for version in ("1", "2.1"):
            state = ws.enter_applying_review_feedback(
                h.base_state(wi=minimal_item("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", version)), WI, now="t")
            self.assert_edge("implementation.apply_review", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                             state["work_items"][WI]["phase"], version)

    def test_a_v1_plan_apply_stays_at_its_phase(self):
        state = h.base_state(wi=minimal_item("AWAITING_EXTERNAL_PLAN_REVIEW", "1"))
        state = ws.publish_plan_revision(state, WI, 2, "t")
        self.assert_edge("plan.apply_review", "AWAITING_EXTERNAL_PLAN_REVIEW", state["work_items"][WI]["phase"], "1")


# ---------------------------------------------------------------------------
# Stale decisions and reconcile (D-OP-Identity, D-OP-Reconcile)
# ---------------------------------------------------------------------------


def reconcile(repo: h.ScratchRepo, decision: dict, *extra: str) -> tuple[dict, int]:
    path = repo.root.parent / (repo.root.name + "-decision.json")
    path.write_text(json.dumps(decision))
    try:
        body, code = call("--repo-root", str(repo.root), "reconcile", "--decision", str(path), *extra)
    finally:
        path.unlink()
    if body["ok"] and body["result"]["next"] is not None:
        errors = schema_errors(body["result"]["next"], SCHEMA["$defs"]["decision"], "$.result.next")
        assert not errors, errors
    return body, code


def reconciled(test: unittest.TestCase, repo: h.ScratchRepo, decision: dict) -> dict:
    body, code = reconcile(repo, decision)
    test.assertEqual(code, 0, body)
    result = body["result"]
    allowed = wp.EDGES[decision["action"]["id"]]["allowed_results"]
    if result["class"] != "invalid":
        test.assertIn(result["class"], allowed)
    return result


class TestStaleDecision(unittest.TestCase):
    def test_an_old_identity_is_stale_decision(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="PLANNING")
            identity = next_action(repo)["basis"]["state_identity"]
            self.assertEqual(next_action(repo, "--expect-state-identity", identity)["row"], "7")
            edit_item(repo, last_transition="moved on")
            body, code = call("--repo-root", str(repo.root), "next-action", "--expect-state-identity", identity)
            self.assertEqual((code, body["error"]["code"], body["error"]["retryable"]), (3, "stale_decision", True))


class TestReconcile(unittest.TestCase):
    def test_a_phase_change_is_progress(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="PLANNING")
            decision = next_action(repo)
            h.publish_and_bind_plan_bundle(repo)
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["from"]["phase"], result["to"]["phase"]),
                             ("progress", "PLANNING", "AWAITING_LOCAL_PLAN_REVIEW"))
            self.assertEqual(result["next"]["row"], "12")
            self.assertEqual(result["basis"]["state_identity"], result["to"]["state_identity"])

    def checkpoint_decision(self, repo: h.ScratchRepo) -> dict:
        h.seed_bundle_item(repo, phase="IMPLEMENTING", registry_checkpoints=[{"id": "C1", "depends_on": []},
                                                                            {"id": "C2", "depends_on": ["C1"]}])
        h.approve_plan(repo)
        decision = next_action(repo)
        self.assertEqual(decision["action"]["id"], "implementation.checkpoint")
        return decision

    def complete_c1(self, repo: h.ScratchRepo, *, commit: bool) -> None:
        registry = json.loads((repo.root / "docs/ai-workflow/registry/wi-registry.json").read_text())
        state = ws.transition_checkpoint_in_progress(h.read_state(repo), WI, "C1", start_commit=repo.head(), now="t")
        state = ws.complete_checkpoint(state, WI, "C1", registry, now="t", repo_root=repo.root)
        h.write_state(repo, state)
        if commit:
            (repo.root / h.BUNDLE_ITEM_IMPLEMENTATION_PATH).write_text("C1\n")
            h.git(repo, "add", "-A")
            h.git(repo, "commit", "-q", "-m", f"C1\n\nWorkflow-Checkpoint: C1\nWorkflow-Work-Item: {WI}")

    def test_same_phase_checkpoint_progress_needs_its_trailer_commit(self):
        with h.ScratchRepo() as repo:
            decision = self.checkpoint_decision(repo)
            self.complete_c1(repo, commit=True)
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["evidence"]["completed_checkpoints"]), ("progress", ["C1"]))
        with h.ScratchRepo() as repo:
            decision = self.checkpoint_decision(repo)
            self.complete_c1(repo, commit=False)
            result = reconciled(self, repo, decision)
            self.assertEqual(result["class"], "invalid")
            self.assertEqual([reason["code"] for reason in result["invalid_reasons"]],
                             ["checkpoint_completion_unproven"])

    def revalidate_c1(self, repo: h.ScratchRepo) -> dict:
        """The Workflow's sanctioned amendment revalidation: C1 is demoted,
        then re-run through the ordinary start, complete and trailer commit,
        leaving two `Workflow-Checkpoint: C1` commits. Returns the decision
        taken before C1 first completed."""
        decision = next_action(repo)
        self.complete_c1(repo, commit=True)
        mutate(repo, _demote_checkpoint, "C1")
        self.complete_c1(repo, commit=True)
        return decision

    def test_a_revalidated_checkpoint_is_progress(self):
        with h.ScratchRepo() as repo:
            self.checkpoint_decision(repo)
            decision = self.revalidate_c1(repo)
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["evidence"]["completed_checkpoints"]), ("progress", ["C1"]))

    def test_a_revalidated_checkpoint_is_proven_by_verify(self):
        with h.ScratchRepo() as repo:
            self.checkpoint_decision(repo)
            self.revalidate_c1(repo)
            body, code = call("--repo-root", str(repo.root), "verify")
        self.assertEqual(code, wp.EXIT_OK)
        self.assertTrue(body["result"]["healthy"])
        self.assertEqual(checks_by_id(body)["checkpoint_completions_provable"]["status"], "pass")

    def test_a_reopened_checkpoint_with_only_its_earlier_trailer_is_unproven(self):
        with h.ScratchRepo() as repo:
            self.checkpoint_decision(repo)
            decision = next_action(repo)
            self.complete_c1(repo, commit=True)
            mutate(repo, _demote_checkpoint, "C1")
            self.complete_c1(repo, commit=False)
            result = reconciled(self, repo, decision)
            body, code = call("--repo-root", str(repo.root), "verify")
        self.assertEqual(result["class"], "invalid")
        self.assertEqual([reason["code"] for reason in result["invalid_reasons"]],
                         ["checkpoint_completion_unproven"])
        self.assertFalse(body["result"]["healthy"])
        self.assertEqual(checks_by_id(body)["checkpoint_completions_provable"]["status"], "fail")

    def test_an_unrelated_second_trailer_is_still_ambiguous(self):
        with h.ScratchRepo() as repo:
            decision = self.checkpoint_decision(repo)
            self.complete_c1(repo, commit=True)
            (repo.root / h.BUNDLE_ITEM_IMPLEMENTATION_PATH).write_text("again\n")
            h.git(repo, "add", "-A")
            h.git(repo, "commit", "-q", "-m", f"C1 again\n\nWorkflow-Checkpoint: C1\nWorkflow-Work-Item: {WI}")
            result = reconciled(self, repo, decision)
        self.assertEqual(result["class"], "invalid")

    def test_an_unchanged_state_and_a_started_checkpoint_are_no_progress(self):
        with h.ScratchRepo() as repo:
            decision = self.checkpoint_decision(repo)
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["from"], result["to"]["state_identity"]),
                             ("no_progress", {"phase": "IMPLEMENTING",
                                              "state_identity": decision["basis"]["state_identity"]},
                              decision["basis"]["state_identity"]))
            mutate(repo, ws.transition_checkpoint_in_progress, "C1", start_commit=repo.head(), now="t")
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["evidence"]["started_checkpoints"]), ("no_progress", ["C1"]))

    def test_an_unchanged_state_after_a_block_is_gate_reached(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            decision = next_action(repo)
            self.assertEqual(reconciled(self, repo, decision)["class"], "no_progress", "no new verdict")
            write_feedback(repo, verdict("BLOCK", rcid=ids["P"], role="LOCAL_MODEL_PLAN_REVIEW"))
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["next"]["action"]["id"]), ("gate_reached", "review.resolve_block"))

    def test_reaching_plan_approval_is_gate_reached(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW"))
            decision = next_action(repo)
            self.assertEqual(decision["action"]["id"], "plan.record_external")
            mutate(repo, ws.record_manual_plan_review, verdict="APPROVE", bundle_id=ids["B"], round=1, now="t",
                   current_review_content_id=ids["P"], feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                   feedback_review_content_id=ids["P"])
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["to"]["phase"], result["evidence"]["recorded_stage"]),
                             ("gate_reached", "AWAITING_PLAN_APPROVAL", "MANUAL_EXTERNAL_PLAN_REVIEW"))

    def test_a_v1_plan_apply_needs_its_revision_advance(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW")
            h.generate_plan_bundle(repo)
            write_feedback(repo, verdict("REVISE", bundle=current_bundle_id(repo), base=repo.base))
            decision = next_action(repo)
            self.assertEqual(decision["action"]["id"], "plan.apply_review")
            with mock.patch.object(ws, "plan_review_publication_status", side_effect=AssertionError("not for 1")):
                self.assertEqual(reconciled(self, repo, decision)["class"], "no_progress")
                audit = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_AUDIT.md"
                audit.write_text(audit.read_text() + "applied\n")
                h.generate_plan_bundle(repo)
                edit_item(repo, plan_revision=2)
                result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["next"]["row"]), ("gate_reached", "20"))

    def test_plan_start(self):
        with h.ScratchRepo() as repo:
            write_config(repo, "2.2")
            write_state(repo, h.base_state(old=minimal_item("MILESTONE_COMPLETE", "2.2", work_item_id="old")))
            decision = next_action(repo)
            self.assertEqual(decision["action"]["id"], "plan.start")
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["from"], result["to"], result["basis"]),
                             ("no_progress", None, None, None))
            state = h.base_state(old=minimal_item("MILESTONE_COMPLETE", "2.2", work_item_id="old"),
                                 wi=minimal_item("PLANNING", "2.2", base_commit=repo.base))
            state["active_work_item_id"] = WI
            write_state(repo, state)
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["from"], result["to"]["phase"]), ("progress", None, "PLANNING"))
            for active, extra in ((WI, {"third": minimal_item("PLANNING", "2.2", work_item_id="third",
                                                                base_commit=repo.base)}),
                                  ("third", {"third": minimal_item("PLANNING", "2.2", work_item_id="third",
                                                                     base_commit=repo.base)})):
                state = h.base_state(old=minimal_item("MILESTONE_COMPLETE", "2.2", work_item_id="old"),
                                     wi=minimal_item("PLANNING", "2.2", base_commit=repo.base), **extra)
                state["active_work_item_id"] = active
                write_state(repo, state)
                result = reconciled(self, repo, decision)
                self.assertEqual((result["class"], result["invalid_reasons"][0]["code"]),
                                 ("invalid", "plan_start_item_ambiguous"))
            state = h.base_state(old=minimal_item("AMENDING_PLAN", "2.2", work_item_id="old", base_commit=repo.base))
            state["active_work_item_id"] = "old"
            write_state(repo, state)
            self.assertEqual(reconciled(self, repo, decision)["invalid_reasons"][0]["code"], "plan_start_item_ambiguous")

    def test_a_class_outside_allowed_results_is_invalid(self):
        with h.ScratchRepo() as repo:
            edges = dict(wp.EDGES["implementation.checkpoint"], allowed_results=["progress"])
            with mock.patch.dict(wp.EDGES, {"implementation.checkpoint": edges}):
                decision = self.checkpoint_decision(repo)
                result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["invalid_reasons"][0]["code"]), ("invalid", "result_not_allowed"))

    def test_an_illegal_edge_and_milestone_complete_are_invalid(self):
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            decision = next_action(repo)
            edit_item(repo, phase="IMPLEMENTING")
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["invalid_reasons"][0]["code"]), ("invalid", "illegal_edge"))
            state = edit_item(repo, phase="MILESTONE_COMPLETE")
            state["active_work_item_id"] = None  # as complete_work_item leaves it
            h.write_state(repo, state)
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["invalid_reasons"][0]["code"]), ("invalid", "illegal_edge"))
            self.assertNotIn("complete", wp.RECONCILE_CLASSES)
            self.assertEqual((result["next"]["row"], result["next"]["disposition"]), ("40", "complete"))

    def test_a_review_phase_reached_with_a_marker_or_unbound_is_invalid(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="PLANNING")
            decision = next_action(repo)
            h.publish_and_bind_plan_bundle(repo)
            reject_bundle(repo)
            codes = [reason["code"] for reason in reconciled(self, repo, decision)["invalid_reasons"]]
            self.assertIn("bundle_rejected", codes)
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="PLANNING")
            decision = next_action(repo)
            edit_item(repo, phase="AWAITING_LOCAL_PLAN_REVIEW")
            result = reconciled(self, repo, decision)
            self.assertEqual(result["class"], "invalid")
            self.assertIn("plan_review_not_bound", [reason["code"] for reason in result["invalid_reasons"]])

    def test_a_foreign_tampered_or_gate_decision_is_invalid_request(self):
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW")
            decision = next_action(repo)
            tampered = json.loads(json.dumps(decision))
            tampered["action"]["id"] = "implementation.checkpoint"
            tampered["action"]["allowed_results"] = wp.EDGES["implementation.checkpoint"]["allowed_results"]
            invocation = json.loads(json.dumps(decision))
            invocation["action"]["invocation"] = "/review-plan someone-else"
            foreign = json.loads(json.dumps(decision))
            foreign["basis"]["work_item_id"] = "nobody"
            foreign["action"]["arguments"]["work_item_id"] = "nobody"
            foreign["action"]["invocation"] = "/review-plan nobody"
            for name, bad in (("tampered action", tampered), ("tampered invocation", invocation),
                              ("unknown item", foreign)):
                with self.subTest(case=name):
                    body, code = reconcile(repo, bad)
                    self.assertEqual((code, body["error"]["code"]), (2, "invalid_request"))
            body, code = reconcile(repo, decision, "--work-item", "another")
            self.assertEqual((code, body["error"]["code"]), (2, "invalid_request"))
            edit_item(repo, phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            gate = next_action(repo)
            self.assertEqual(gate["disposition"], "external_gate")
            body, code = reconcile(repo, gate)
            self.assertEqual((code, body["error"]["code"]), (2, "invalid_request"))
            body, code = call("--repo-root", str(repo.root), "reconcile", "--decision", str(repo.root / "missing.json"))
            self.assertEqual((code, body["error"]["code"]), (2, "invalid_request"))

    def test_the_full_envelope_is_accepted_as_the_decision(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="PLANNING")
            body, _code = call("--repo-root", str(repo.root), "next-action")
            self.assertEqual(reconcile(repo, body)[0]["result"]["class"], "no_progress")


class TestNeverWrites(unittest.TestCase):
    def test_next_action_and_reconcile_leave_the_state_untouched(self):
        with h.ScratchRepo() as repo:
            ids = plan_item_at(repo, "REVISING_PLAN")
            write_feedback(repo, verdict("REVISE", rcid=ids["P"]))
            state_path = repo.root / ws.DEFAULT_STATE_PATH
            before = (state_path.read_bytes(), state_path.stat().st_mtime_ns)
            status_before = h.git(repo, "status", "--porcelain", "--ignored")
            decision = next_action(repo)
            reconcile(repo, decision)
            self.assertEqual((state_path.read_bytes(), state_path.stat().st_mtime_ns), before)
            self.assertEqual(h.git(repo, "status", "--porcelain", "--ignored"), status_before)


class TestIngestToApplyRouting(unittest.TestCase):
    """`MPR-R8-001`: every verdict a stage ingest records leads to the
    stage's apply action, whose command then accepts the state, or to a
    gate -- never to a different automatic action (`plan.author` in
    particular). The ingests are recorded through their state writers here;
    CP5's `record-external-result` calls the same writers."""

    PLAN_VARIANTS = {
        "full": lambda ids, repo: verdict("REVISE", rcid=ids["P"], bundle=ids["B"], base=repo.base,
                                          role="MANUAL_EXTERNAL_PLAN_REVIEW"),
        "no bundle fields": lambda ids, repo: verdict("REVISE", rcid=ids["P"], work_item=None,
                                                      role="MANUAL_EXTERNAL_PLAN_REVIEW"),
        "stale bundle id": lambda ids, repo: verdict("REVISE", rcid=ids["P"], bundle="f" * 64,
                                                     role="MANUAL_EXTERNAL_PLAN_REVIEW"),
    }

    def test_plan_stage(self):
        for version in ("2.1", "2.2"):
            for name, build in self.PLAN_VARIANTS.items():
                with self.subTest(version=version, verdict=name), h.ScratchRepo() as repo:
                    ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", version)
                    write_feedback(repo, build(ids, repo))
                    mutate(repo, ws.record_manual_plan_review, verdict="REVISE", bundle_id=ids["B"], round=1,
                           now="t", current_review_content_id=ids["P"], feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                           feedback_review_content_id=ids["P"])
                    result = next_action(repo)
                    self.assertEqual((result["row"], result["action"]["id"]), ("8", "plan.apply_review"))
                    COMMAND_GUARDS["plan.apply_review"][0](repo, h.read_state(repo))
            with self.subTest(version=version, verdict="local REVISE"), h.ScratchRepo() as repo:
                ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW", version)
                write_feedback(repo, verdict("REVISE", rcid=ids["P"], bundle=ids["B"], base=repo.base,
                                             role="LOCAL_MODEL_PLAN_REVIEW"))
                mutate(repo, ws.record_local_plan_review, verdict="REVISE", bundle_id=ids["B"],
                       review_content_id=ids["P"], round=1, now="t")
                self.assertEqual(next_action(repo)["row"], "8")
            with self.subTest(version=version, verdict="APPROVEs"), h.ScratchRepo() as repo:
                ids = plan_item_at(repo, "AWAITING_LOCAL_PLAN_REVIEW", version)
                write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="LOCAL_MODEL_PLAN_REVIEW"))
                mutate(repo, ws.record_local_plan_review, verdict="APPROVE", bundle_id=ids["B"],
                       review_content_id=ids["P"], round=1, now="t")
                self.assertEqual(next_action(repo)["disposition"], "external_gate")
                write_feedback(repo, verdict("APPROVE", rcid=ids["P"], role="MANUAL_EXTERNAL_PLAN_REVIEW"))
                mutate(repo, ws.record_manual_plan_review, verdict="APPROVE", bundle_id=ids["B"], round=1, now="t",
                       current_review_content_id=ids["P"], feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                       feedback_review_content_id=ids["P"])
                self.assertEqual(next_action(repo)["disposition"], "human_gate")

    def test_implementation_stage(self):
        variants = {
            "full": lambda ids, repo: verdict("REVISE", rcid=ids["I"], bundle=ids["B"], base=repo.base,
                                              role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"),
            "no bundle fields": lambda ids, repo: verdict("REVISE", rcid=ids["I"], work_item=None,
                                                          role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"),
            "stale bundle id": lambda ids, repo: verdict("REVISE", rcid=ids["I"], bundle="f" * 64,
                                                         role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"),
        }
        for name, build in variants.items():
            with self.subTest(verdict=name), h.ScratchRepo() as repo:
                ids = implementation_at_manual(repo)
                write_feedback(repo, build(ids, repo))
                mutate(repo, ws.record_manual_implementation_review, verdict="REVISE", bundle_id=ids["B"], round=1,
                       now="t", current_review_content_id=ids["I"],
                       feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", feedback_review_content_id=ids["I"])
                result = next_action(repo)
                self.assertEqual((result["row"], result["action"]["id"]), ("36", "implementation.apply_review"))
                self.assertEqual(COMMAND_GUARDS["implementation.apply_review"][0](repo, h.read_state(repo))["binding"],
                                 "content")
        with h.ScratchRepo() as repo:
            ids = implementation_item_at(repo, "2.2")
            write_feedback(repo, verdict("REVISE", rcid=ids["I"], bundle=ids["B"], base=repo.base,
                                         role="LOCAL_MODEL_IMPLEMENTATION_REVIEW"))
            mutate(repo, ws.record_local_implementation_review, verdict="REVISE", bundle_id=ids["B"],
                   review_content_id=ids["I"], round=1, now="t")
            self.assertEqual(next_action(repo)["row"], "36")
        with h.ScratchRepo() as repo:
            implementation_at_external_2_2(repo)
            self.assertEqual(next_action(repo)["disposition"], "human_gate")


class TestPlanGateRace(unittest.TestCase):
    def test_a_binding_changed_between_the_two_reads_is_plan_review_bundle_unbound(self):
        """Rows 5 and 10 read the publication status first; if it changes
        before the gate wrapper's own read, the wrapper still reports it."""
        with h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_PLAN_APPROVAL")
            state = h.read_state(repo)
            bound = ws.plan_review_publication_status(repo.root, state, WI)
            plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan.write_text(plan.read_text() + "changed between the reads\n")
            real = ws.plan_review_publication_status
            calls = {"n": 0}

            def first_read_is_stale(*args, **kwargs):
                calls["n"] += 1
                return dict(bound) if calls["n"] == 1 else real(*args, **kwargs)

            with mock.patch.object(ws, "plan_review_publication_status", side_effect=first_read_is_stale):
                result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("16", "plan_review_bundle_unbound"))


# ---------------------------------------------------------------------------
# record-external-result (D-OP-External, CP5)
# ---------------------------------------------------------------------------


def record_external(repo: h.ScratchRepo, kind: str, text: str, work_item: str = WI) -> tuple[dict, int]:
    """`record-external-result` in-process, schema-checked, with `text` as
    its input file (outside the repository)."""
    tmp = Path(repo.root).parent / f"{Path(repo.root).name}-verdict.md"
    tmp.write_text(text)
    try:
        return call("--repo-root", str(repo.root), "record-external-result", "--work-item", work_item,
                    "--kind", kind, "--input", str(tmp))
    finally:
        tmp.unlink(missing_ok=True)


def recorded(test: unittest.TestCase, repo: h.ScratchRepo, kind: str, text: str) -> dict:
    body, code = record_external(repo, kind, text)
    test.assertEqual(code, wp.EXIT_OK, body)
    return body["result"]


def refusal(test: unittest.TestCase, repo: h.ScratchRepo, kind: str, text: str, code: str,
            native: str | None = None) -> dict:
    """Refused with `code` (and the `native` exception), writing nothing."""
    state_path = repo.root / ws.DEFAULT_STATE_PATH
    before = (state_path.read_bytes(), _fb_text(repo))
    body, exit_code = record_external(repo, kind, text)
    test.assertEqual((exit_code, body["error"]["code"]), (wp.exit_code_for(code), code), body)
    if native is not None:
        test.assertEqual(body["error"]["native"]["exception"], native, body)
    test.assertEqual((state_path.read_bytes(), _fb_text(repo)), before)
    return body["error"]


_TWO_STAGE_KINDS = {
    "plan_review_verdict": ("MANUAL_EXTERNAL_PLAN_REVIEW", "P",
                            lambda repo, version: plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", version),
                            ("2.1", "2.2")),
    "implementation_review_verdict": ("MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "I",
                                      lambda repo, version: implementation_at_manual(repo), ("2.2",)),
}


def _feedback_only_item(repo: h.ScratchRepo, kind: str, version: str) -> str:
    """An item at a feedback-only row's phase with its bundle generated.
    Returns the bundle id."""
    if kind == "plan_review_verdict":
        h.seed_bundle_item(repo, governing_workflow_version=version, phase="AWAITING_EXTERNAL_PLAN_REVIEW")
        h.generate_plan_bundle(repo)
    else:
        implementation_item_at(repo, version)
    return current_bundle_id(repo)


_FEEDBACK_ONLY_ROWS = (("plan_review_verdict", "1"), ("implementation_review_verdict", "1"),
                       ("implementation_review_verdict", "2.1"))


class TestRecordExternalResultTwoStage(unittest.TestCase):
    def test_each_verdict_records_the_command_paths_state(self):
        for kind, (role, key, build, versions) in _TWO_STAGE_KINDS.items():
            writer = ws.record_manual_plan_review if kind == "plan_review_verdict" else ws.record_manual_implementation_review
            for version in versions:
                for status in ("APPROVE", "REVISE", "BLOCK"):
                    with self.subTest(kind=kind, version=version, status=status), h.ScratchRepo() as repo:
                        ids = build(repo, version)
                        before = h.read_state(repo)
                        text = verdict(status, rcid=ids[key], bundle=ids["B"], base=repo.base, role=role)
                        result = recorded(self, repo, kind, text)
                        after = h.read_state(repo)
                        now = after["work_items"][WI]["last_transition"]
                        expected = writer(before, WI, verdict=status, bundle_id=ids["B"], round=1, now=now,
                                          current_review_content_id=ids[key], feedback_role=role,
                                          feedback_review_content_id=ids[key])
                        self.assertEqual(after, expected)
                        self.assertEqual(_fb_text(repo), text)
                        self.assertEqual(
                            {k: result[k] for k in ("stage", "verdict", "review_content_id", "round", "bundle_id",
                                                    "advisory")},
                            {"stage": "plan" if kind == "plan_review_verdict" else "implementation",
                             "verdict": status, "review_content_id": ids[key], "round": 1, "bundle_id": ids["B"],
                             "advisory": None})
                        self.assertEqual(result["basis"], wp.basis(repo.root, after, WI))

    def test_the_command_path_and_the_protocol_path_give_the_same_values(self):
        """`round` and `bundle_id` (`LPR-R3-005`): a stated `Round:` and an
        absent bundle id, through both paths."""
        for kind, (role, key, build, versions) in _TWO_STAGE_KINDS.items():
            stage = "plan" if kind == "plan_review_verdict" else "implementation"
            for path in ("command", "protocol"):
                with self.subTest(kind=kind, path=path), h.ScratchRepo() as repo:
                    ids = build(repo, versions[-1])
                    text = verdict("APPROVE", rcid=ids[key], role=role).replace(
                        "Status: APPROVE\n", "Status: APPROVE\nRound: 4\n")
                    if path == "command":
                        result = ws.ingest_manual_review_verdict(repo.root, WI, stage=stage, verdict_text=text,
                                                                 now="t", two_stage_only=True)
                    else:
                        result = recorded(self, repo, kind, text)
                    self.assertEqual((result["round"], result["bundle_id"], result["advisory"]),
                                     (4, None, ws.ABSENT_REVIEWED_BUNDLE_ID_ADVISORY))

    def test_refusals(self):
        for kind, (role, key, build, versions) in _TWO_STAGE_KINDS.items():
            with self.subTest(kind=kind), h.ScratchRepo() as repo:
                ids = build(repo, versions[-1])
                ok = dict(rcid=ids[key], bundle=ids["B"], role=role)
                refusal(self, repo, kind, verdict("APPROVE", **dict(ok, rcid=None)), "refused",
                        "ManualVerdictHeaderError")
                refusal(self, repo, kind, verdict("APPROVE", **ok, work_item="other-item"), "refused",
                        "ManualFeedbackForeignWorkItemError")
                refusal(self, repo, kind, verdict("APPROVE", **dict(ok, role="LOCAL_MODEL_PLAN_REVIEW")), "refused",
                        "WrongReviewerRoleError")
                refusal(self, repo, kind, verdict("APPROVE", **dict(ok, rcid="e" * 64)), "refused",
                        "StaleReviewContentIdError")
                reject_bundle(repo)
                refusal(self, repo, kind, verdict("APPROVE", **ok), "refused", "BundleRejectedError")

    def test_a_duplicate_is_not_applicable(self):
        for kind, (role, key, build, versions) in _TWO_STAGE_KINDS.items():
            with self.subTest(kind=kind), h.ScratchRepo() as repo:
                ids = build(repo, versions[-1])
                state = h.read_state(repo)
                ledger = state["work_items"][WI]["plan_review_stages" if key == "P" else "implementation_review_stages"]
                local = next(k for k in ledger if k.startswith("LOCAL_MODEL"))
                ledger[role] = dict(ledger[local])
                h.write_state(repo, state)
                refusal(self, repo, kind, verdict("APPROVE", rcid=ids[key], bundle=ids["B"], role=role),
                        "not_applicable")

    def test_a_retry_after_the_record_is_not_applicable(self):
        for kind, (role, key, build, versions) in _TWO_STAGE_KINDS.items():
            with self.subTest(kind=kind), h.ScratchRepo() as repo:
                ids = build(repo, versions[-1])
                text = verdict("APPROVE", rcid=ids[key], bundle=ids["B"], role=role)
                recorded(self, repo, kind, text)
                refusal(self, repo, kind, text, "not_applicable")

    def test_the_crash_window_is_retried_once_through_either_path(self):
        """The file is written and the state write fails: the item stays at
        its phase, next-action reports the unrecorded verdict (rows 13 and
        27), and a retry through the command path or the protocol records
        it once."""
        for kind, (role, key, build, versions) in _TWO_STAGE_KINDS.items():
            stage = "plan" if kind == "plan_review_verdict" else "implementation"
            for retry in ("command", "protocol"):
                with self.subTest(kind=kind, retry=retry), h.ScratchRepo() as repo:
                    ids = build(repo, versions[-1])
                    text = verdict("REVISE", rcid=ids[key], bundle=ids["B"], role=role)
                    revision = h.read_state(repo)["work_items"][WI]["state_revision"]
                    with mock.patch.object(ws, "_publish_state_file", side_effect=OSError("disk full")):
                        body, code = record_external(repo, kind, text)
                    self.assertEqual((code, body["error"]["code"]), (wp.EXIT_INTERNAL, "internal_error"))
                    self.assertEqual(_fb_text(repo), text)
                    self.assertEqual(next_action(repo)["row"], "13" if stage == "plan" else "27")
                    if retry == "command":
                        ws.ingest_manual_review_verdict(repo.root, WI, stage=stage, verdict_text=text, now="t",
                                                        two_stage_only=True)
                    else:
                        recorded(self, repo, kind, text)
                    self.assertEqual(h.read_state(repo)["work_items"][WI]["state_revision"], revision + 1)
                    refusal(self, repo, kind, text, "not_applicable")

    def test_a_manual_revise_without_current_bundle_fields_routes_to_its_apply(self):
        """`MPR-R8-001`: recorded by the bundle-id rule, then the stage's
        apply action, whose command accepts the stored file by content."""
        for kind, (role, key, build, versions) in _TWO_STAGE_KINDS.items():
            for version in versions:
                for variant in ("absent", "pre-regeneration"):
                    with self.subTest(kind=kind, version=version, variant=variant), h.ScratchRepo() as repo:
                        ids = build(repo, version)
                        bundle = None
                        if variant == "pre-regeneration":
                            bundle = ids["B"]
                            if kind == "plan_review_verdict":
                                regenerate_wrapper_only(repo)
                            else:
                                summary = bundle_dir(repo) / "IMPLEMENTATION_SUMMARY.md"
                                summary.write_text(summary.read_text() + "wrapper-only rerun\n")
                                h._run_generator(repo, "implementation", WI)
                            self.assertNotEqual(current_bundle_id(repo), bundle)
                        result = recorded(self, repo, kind, verdict("REVISE", rcid=ids[key], bundle=bundle,
                                                                    role=role))
                        self.assertEqual(result["bundle_id"], bundle)
                        if bundle is None:
                            self.assertEqual(result["advisory"], ws.ABSENT_REVIEWED_BUNDLE_ID_ADVISORY)
                        else:
                            self.assertIn("advisory only", result["advisory"])
                        decision = next_action(repo)
                        action = "plan.apply_review" if kind == "plan_review_verdict" else "implementation.apply_review"
                        self.assertEqual((decision["row"], decision["action"]["id"]),
                                         ("8" if kind == "plan_review_verdict" else "36", action))
                        self.assertEqual(COMMAND_GUARDS[action][0](repo, h.read_state(repo))["binding"], "content")


class TestRecordExternalResultFeedbackOnly(unittest.TestCase):
    def test_stored_with_and_without_the_label_and_the_state_unchanged(self):
        for kind, version in _FEEDBACK_ONLY_ROWS:
            for label in (True, False):
                with self.subTest(kind=kind, version=version, label=label), h.ScratchRepo() as repo:
                    B = _feedback_only_item(repo, kind, version)
                    rcid = "c" * 64 if label else None
                    text = verdict("REVISE", rcid=rcid, bundle=B, base=repo.base)
                    state_before = (repo.root / ws.DEFAULT_STATE_PATH).read_bytes()
                    result = recorded(self, repo, kind, text)
                    self.assertEqual((repo.root / ws.DEFAULT_STATE_PATH).read_bytes(), state_before)
                    self.assertEqual(_fb_text(repo), text)
                    self.assertEqual((result["verdict"], result["review_content_id"], result["round"],
                                      result["bundle_id"], result["advisory"]), ("REVISE", rcid, None, None, None))
                    fingerprint.assert_feedback_matches_bundle(
                        fingerprint.parse_review_feedback_binding_fields(_fb_text(repo)),
                        bundle_id=B, base_commit=repo.base, work_item_id=WI)

    def test_refusals(self):
        for kind, version in _FEEDBACK_ONLY_ROWS:
            with self.subTest(kind=kind, version=version), h.ScratchRepo() as repo:
                B = _feedback_only_item(repo, kind, version)
                refusal(self, repo, kind, verdict("APPROVE", bundle=B, base=repo.base, work_item="other-item"),
                        "refused", "ManualFeedbackForeignWorkItemError")
                refusal(self, repo, kind, verdict("APPROVE", bundle="f" * 64, base=repo.base), "refused",
                        "FeedbackBundleMismatchError")
                refusal(self, repo, kind, verdict("APPROVE", base=repo.base), "refused", "ManualVerdictHeaderError")
                reject_bundle(repo)
                refusal(self, repo, kind, verdict("APPROVE", bundle=B, base=repo.base), "refused",
                        "BundleRejectedError")

    def test_a_different_current_verdict_conflicts_and_an_earlier_rounds_is_replaced(self):
        for kind, version in _FEEDBACK_ONLY_ROWS:
            with self.subTest(kind=kind, version=version), h.ScratchRepo() as repo:
                B = _feedback_only_item(repo, kind, version)
                first = verdict("APPROVE", bundle=B, base=repo.base)
                recorded(self, repo, kind, first)
                refusal(self, repo, kind, verdict("REVISE", bundle=B, base=repo.base), "refused",
                        "ConflictingReviewFeedbackError")
                recorded(self, repo, kind, first)
                write_feedback(repo, verdict("APPROVE", bundle="f" * 64, base=repo.base))
                recorded(self, repo, kind, first)
                self.assertEqual(_fb_text(repo), first)


class TestRecordExternalResultApplicability(unittest.TestCase):
    def test_reserved_and_unknown_kinds(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=minimal_item("IMPLEMENTING", "2.2")))
            for kind in wp.RESERVED_RESULT_KINDS:  # none in protocol 1.1; the loop keeps a future kind honest
                with self.subTest(kind=kind):
                    refusal(self, repo, kind, "anything", "unsupported_result_kind")
            refusal(self, repo, "no_such_kind", "anything", "invalid_request")

    def test_the_wrong_phase_is_not_applicable(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=minimal_item("IMPLEMENTING", "2.2")))
            for kind in wp.VERDICT_RESULT_KINDS:
                with self.subTest(kind=kind):
                    refusal(self, repo, kind, verdict("APPROVE", rcid="a" * 64), "not_applicable")

    def test_a_kind_at_the_other_shape_of_row_is_not_applicable(self):
        """A two-stage kind's phase at a `"1"`/`"2.1"` item, and a
        feedback-only phase at a two-stage item."""
        cases = [
            ("plan_review_verdict", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "1"),
            ("implementation_review_verdict", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "2.1"),
            ("plan_review_verdict", "AWAITING_EXTERNAL_PLAN_REVIEW", "2.2"),
            ("implementation_review_verdict", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2"),
        ]
        for kind, phase, version in cases:
            with self.subTest(kind=kind, phase=phase, version=version), h.ScratchRepo() as repo, \
                    mock.patch.object(ws, "validate_state"):
                write_state(repo, h.base_state(wi=minimal_item(phase, version)))
                refusal(self, repo, kind, verdict("APPROVE", rcid="a" * 64, bundle="b" * 64, base="0" * 40),
                        "not_applicable")

    def test_an_unknown_work_item_and_an_unreadable_input(self):
        with h.ScratchRepo() as repo:
            write_state(repo, h.base_state(wi=minimal_item("IMPLEMENTING", "2.2")))
            body, _code = record_external(repo, "plan_review_verdict", "x", work_item="nope")
            self.assertEqual(body["error"]["code"], "unknown_work_item")
            body, code = call("--repo-root", str(repo.root), "record-external-result", "--work-item", WI,
                              "--kind", "plan_review_verdict", "--input", str(repo.root / "missing.md"))
            self.assertEqual((code, body["error"]["code"]), (wp.EXIT_INVALID_REQUEST, "invalid_request"))



# ---------------------------------------------------------------------------
# The specification (CP6): ORCHESTRATION_PROTOCOL.md's mirrored tables
# ---------------------------------------------------------------------------

SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "ai-workflow" / "ORCHESTRATION_PROTOCOL.md"
_VERSION_ORDER = ("1", "2.1", "2.2")


def spec_table(header: str) -> list[list[str]]:
    """The body rows of the specification's one Markdown table whose header
    line is exactly `header`, each as its stripped cells."""
    lines = SPEC_PATH.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == header]
    assert len(starts) == 1, f"{len(starts)} tables with header {header!r}"
    rows = []
    for line in lines[starts[0] + 2:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _ticked(cell: str) -> list[str]:
    """The backticked values of a cell, in order; `—` and `none` are none."""
    return re.findall(r"`([^`]*)`", cell)


def _single(cell: str) -> str | None:
    if cell in ("—", "none"):
        return None
    values = _ticked(cell)
    assert len(values) == 1 and cell == f"`{values[0]}`", cell
    return values[0]


def _pairs(phases_cell: str, versions_cell: str) -> frozenset:
    if versions_cell != "as listed":
        return frozenset((phase, version) for phase in _ticked(phases_cell) for version in _ticked(versions_cell))
    pairs = set()
    for entry in phases_cell.split(", "):
        match = re.fullmatch(r"`([A-Z_]+)` at (.+)", entry)
        assert match, entry
        pairs |= {(match.group(1), version) for version in _ticked(match.group(2))}
    return frozenset(pairs)


def _versions(cell: str) -> list[str]:
    versions = _ticked(cell)
    assert versions == [v for v in _VERSION_ORDER if v in versions], cell
    return versions


_CATALOGUE_HEADER = "| row | phases | gv | condition | disposition | action | reason, invocation and notes |"


def catalogue_table_mismatches() -> list[str]:
    """Every difference between the specification's catalogue table and
    `CATALOGUE`: the row ids in order, then each row's `(phase, gv)`
    pairs, disposition and action id."""
    rows = spec_table(_CATALOGUE_HEADER)
    ids = [cells[0] for cells in rows]
    if ids != list(wp.ROW_IDS):
        return [f"row ids {ids} != {list(wp.ROW_IDS)}"]
    mismatches = []
    for (row_id, phases, versions, _condition, disposition, action, _notes), row in zip(rows, wp.CATALOGUE):
        pairs = frozenset() if (phases, versions) == ("—", "—") else _pairs(phases, versions)
        expected = (frozenset() if row.no_item else row.pairs, row.disposition, row.action_id)
        if (pairs, disposition, _single(action)) != expected:
            mismatches.append(f"row {row_id}: {(sorted(pairs), disposition, _single(action))} != "
                              f"{(sorted(expected[0]), *expected[1:])}")
    return mismatches


class TestSpecificationTablesEqualTheCode(unittest.TestCase):
    """`ORCHESTRATION_PROTOCOL.md` is normative, and its mirrored tables are
    parsed and compared with the module's own, row for row and in order:
    the catalogue, the condition calls, the legal edges with their proofs
    and `allowed_results`, and the actions."""

    def test_the_catalogue_table_is_the_catalogue_in_order(self):
        self.assertEqual(catalogue_table_mismatches(), [])

    def test_the_condition_call_table_is_condition_calls(self):
        rows = spec_table("| row | call | kind | classes |")
        parsed: dict[str, list[dict]] = {}
        for row_id, function, kind, classes in rows:
            calls = parsed.setdefault(row_id, [])
            if function == "—":
                self.assertEqual((kind, classes), ("—", "—"), row_id)
                continue
            calls.append({"function": _single(function), "kind": kind, "classes": _ticked(classes)})
        self.assertEqual(list(parsed), list(wp.ROW_IDS), "every row, in catalogue order")
        self.assertEqual(parsed, wp.CONDITION_CALLS)

    def test_the_edge_table_is_the_legal_edges(self):
        rows = spec_table("| action id | from | to | gv |")
        parsed: dict[str, list[dict]] = {}
        for action, source, target, versions in rows:
            parsed.setdefault(_single(action), []).append(
                {"from": _single(source), "to": _single(target), "versions": _versions(versions)})
        self.assertEqual(list(parsed), list(wp.EDGES))
        self.assertEqual(parsed, {action: entry["edges"] for action, entry in wp.EDGES.items()})

    def test_the_proof_table_is_the_proofs_and_allowed_results(self):
        rows = spec_table("| action id | same-phase proof | allowed_results |")
        parsed = {_single(action): {"proof": _single(proof), "allowed_results": _ticked(allowed)}
                  for action, proof, allowed in rows}
        self.assertEqual(list(parsed), list(wp.EDGES))
        self.assertEqual(parsed, {action: {"proof": entry["proof"], "allowed_results": entry["allowed_results"]}
                                  for action, entry in wp.EDGES.items()})
        for proof in {entry["proof"] for entry in wp.EDGES.values()} - {None}:
            self.assertIn(f"- `{proof}`:", SPEC_PATH.read_text(), "each proof is defined")

    def test_the_action_table_is_the_actions(self):
        rows = spec_table("| action id | command | invocation | role | fresh_session | independent_of | user_only |")
        parsed = {}
        for action, command, invocation, role, fresh, independent, user_only in rows:
            self.assertIn(fresh, ("yes", "no"))
            self.assertIn(user_only, ("yes", "no"))
            parsed[_single(action)] = {
                "command": _single(command), "invocation": _single(invocation), "role": _single(role),
                "fresh_session": fresh == "yes", "independent_of": _ticked(independent),
                "user_only": user_only == "yes",
            }
        self.assertEqual(list(parsed), list(wp.ACTION_IDS))
        self.assertEqual(parsed, wp.ACTIONS)

    def test_the_vocabulary_tables_name_the_code_values(self):
        text = SPEC_PATH.read_text()
        codes = spec_table("| code | meaning | retryable |")
        self.assertEqual([_single(cells[0]) for cells in codes], list(wp.ERROR_CODES))
        self.assertEqual({_single(cells[0]): cells[2] != "no" for cells in codes}, wp.ERROR_CODES)
        kinds = spec_table("| kind | resolved by |")
        self.assertEqual([_single(cells[0]) for cells in kinds], list(wp.ARTIFACT_KINDS))
        exits = spec_table("| exit code | meaning |")
        self.assertEqual([_single(cells[0]) for cells in exits],
                         [str(c) for c in (wp.EXIT_OK, wp.EXIT_REFUSED, wp.EXIT_INVALID_REQUEST, wp.EXIT_INTERNAL)])
        for check_id in wp.VERIFY_CHECK_IDS:
            self.assertIn(f"`{check_id}`", text)
        for name in (*wp.DISPOSITIONS, *wp.WORKER_ROLES, *wp.EXTERNAL_RESULT_KINDS, *wp.RESERVED_RESULT_KINDS,
                     *wp.RECONCILE_CLASSES, *wp.WORKFLOW_EXCEPTION_CODES):
            self.assertIn(f"`{name}`", text)
        self.assertIn(f'"version": "{wp.PROTOCOL_VERSION}"', text)
        self.assertIn(f"**Workflow {wp.WORKFLOW_RELEASE}**", text, "the tested releases name this one")

    def test_the_ingest_table_names_each_rows_kind_and_versions(self):
        rows = spec_table("| kind | gv | accepted phase | required header fields | guards, in order | records |")
        self.assertEqual(
            [(_single(kind), tuple(_versions(versions)), _single(phase)) for kind, versions, phase, *_ in rows],
            [("plan_review_verdict", ("2.1", "2.2"), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"),
             ("implementation_review_verdict", ("2.2",), "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"),
             ("plan_review_verdict", ("1",), "AWAITING_EXTERNAL_PLAN_REVIEW"),
             ("implementation_review_verdict", ("1", "2.1"), "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")])
        for kind, versions, phase, _fields, _guards, records in rows:
            stage = wp.EXTERNAL_RESULT_KIND_STAGES[_single(kind)]
            for version in _versions(versions):
                with self.subTest(kind=kind, version=version):
                    item = minimal_item(_single(phase), version)
                    self.assertEqual(ws.select_manual_verdict_row(item, stage=stage),
                                     {"stage": stage, "two_stage": records != "nothing (feedback only)",
                                      "phase": _single(phase)})

    def test_the_parser_catches_a_drifted_table(self):
        real = SPEC_PATH.read_text()
        drifted = real.replace("| 23 | `IMPLEMENTING` | `2.1`, `2.2` |", "| 23 | `IMPLEMENTING` | `2.2` |", 1)
        self.assertNotEqual(drifted, real)
        with mock.patch(f"{__name__}.SPEC_PATH", _DriftedSpec(drifted)):
            self.assertEqual(catalogue_table_mismatches(), [
                f"row 23: {([('IMPLEMENTING', '2.2')], 'automatic', 'implementation.checkpoint')} != "
                f"{([('IMPLEMENTING', '2.1'), ('IMPLEMENTING', '2.2')], 'automatic', 'implementation.checkpoint')}"])


class _DriftedSpec:
    """A stand-in for `SPEC_PATH` whose text is given."""

    def __init__(self, text: str):
        self._text = text

    def read_text(self) -> str:
        return self._text


class TestOperatorDocuments(unittest.TestCase):
    """CP6's command and operator documentation."""

    def accept_step_2a(self) -> str:
        text = _command_text("accept-milestone")
        return text.split("\n2a. ", 1)[1].split("\n2b. ", 1)[0]

    def test_accept_milestone_offers_no_command_that_cannot_run_here(self):
        """`v2.6.0-003`, `LPR-R5-003`, `LPR-R6-001`: step 2a names neither
        `/milestone-implement` nor `/request-plan-amendment` as a way
        forward from `AWAITING_FUNCTIONAL_REVIEW`; it says no command
        completes the checkpoint there (from 2.9.0 it names the user-only `/resume-implementation`), and keeps the functional routing."""
        step = self.accept_step_2a()
        self.assertNotIn("/request-plan-amendment", step)
        self.assertNotIn("finish it with `/milestone-implement`", step)
        for sentence in re.split(r"(?<=[.:;])\s+", step):
            if "/milestone-implement" in sentence:
                self.assertRegex(sentence, r"cannot", sentence)
        self.assertIn("no command completes one here", " ".join(step.split()))
        self.assertIn("`/resume-implementation <id>`", step)
        self.assertIn("v2.6.0-003", step)
        self.assertIn("route that\n      finding through `/apply-functional-review` instead", step)

    def test_the_operator_reference_points_at_next_action(self):
        text = (Path(__file__).resolve().parent.parent / "docs" / "ai-workflow"
                / "WORKFLOW_V2_1_OPERATOR_REFERENCE.md").read_text()
        table = text.split("## Which command do I run next?", 1)[1].split("\n---", 1)[0]
        self.assertIn("workflow_protocol.py next-action", table)
        self.assertNotIn("`/milestone-implement` — finish it", table)
        self.assertIn("## Driving the Workflow by protocol", text)
        self.assertIn("docs/ai-workflow/ORCHESTRATION_PROTOCOL.md", text)

    def test_milestone_workflow_says_the_protocol_adds_no_gate(self):
        text = (Path(__file__).resolve().parent.parent / "docs" / "ai-workflow" / "MILESTONE_WORKFLOW.md").read_text()
        summary = text.split("## Hard gates summary", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Claude must stop and wait for a human/external input at exactly six points", summary)
        self.assertIn("reports these same gates and\nadds none", summary)



# ---------------------------------------------------------------------------
# The lifecycle end to end, by protocol only (CP6)
# ---------------------------------------------------------------------------

LIFECYCLE_CHECKPOINTS = [{"id": "C1", "depends_on": []}, {"id": "C2", "depends_on": ["C1"]}]
_PLAN_DOC = "docs/ai-workflow/WORKFLOW_V2_PLAN.md"


class _Lifecycle:
    """One disposable repository driven by the protocol alone: every step
    takes `next-action`'s `action.id` and `arguments`, runs the action's
    command guards (`COMMAND_GUARDS`) and then applies the Workflow writer
    sequence that command performs -- the same functions, never a state
    edit by hand. `reconcile` is called only after an automatic action; a
    gate's action is applied as the user's writer sequence, and a verdict
    enters through `record-external-result`, each followed by
    `next-action`. `verdicts[action id]` scripts what each review returns,
    in order."""

    def __init__(self, test: unittest.TestCase, repo: h.ScratchRepo, verdicts: dict[str, list[str]]):
        self.test = test
        self.repo = repo
        self.verdicts = {key: list(values) for key, values in verdicts.items()}
        self.trace: list[tuple] = []
        self.rounds = 0
        self.edits = 0
        self.writers = {
            "plan.start": self.plan_start,
            "plan.review.local": self.plan_review_local,
            "plan.apply_review": self.plan_apply_review,
            "implementation.checkpoint": self.implementation_checkpoint,
            "implementation.self_review": self.implementation_self_review,
            "implementation.review.local": self.implementation_review_local,
            "implementation.apply_review": self.implementation_apply_review,
            "functional.prepare": self.functional_prepare,
        }
        self.gates = {
            "plan.approve": self.user_approves_plan,
            "implementation.approve": self.user_approves_implementation,
            "functional.review": self.user_accepts_milestone,
        }

    # -- the protocol calls ------------------------------------------------

    def decide(self) -> dict:
        state = h.read_state(self.repo)
        if state.get("active_work_item_id") is None and WI in state.get("work_items", {}):
            return next_action(self.repo, "--work-item", WI)
        return next_action(self.repo)

    def run(self, decision: dict) -> dict:
        """Execute one decision and return the next one. An automatic
        decision is executed and reconciled; a gate's is resolved."""
        action = decision["action"]
        if decision["disposition"] == "automatic":
            if decision.get("basis") is not None:
                self.test.assertEqual(
                    next_action(self.repo, "--work-item", WI, "--expect-state-identity",
                                decision["basis"]["state_identity"])["row"], decision["row"],
                    "the identity check immediately before the launch")
                COMMAND_GUARDS.get(action["id"], (lambda repo, state: None,))[0](self.repo, h.read_state(self.repo))
            self.writers[action["id"]](action["arguments"])
            result = reconciled(self.test, self.repo, decision)
            self.trace.append((decision["row"], action["id"], result["class"]))
            self.test.assertEqual(result["invalid_reasons"], [])
            self.test.assertEqual(result["next"], self.decide(), "reconcile's next is next-action's decision")
            return result["next"]
        if decision["disposition"] == "external_gate":
            self.trace.append((decision["row"], action["id"], decision["satisfied_by"]))
            self.record_external(decision["satisfied_by"])
        else:
            self.test.assertEqual(decision["disposition"], "human_gate", decision)
            self.trace.append((decision["row"], action["id"], "user"))
            self.gates[action["id"]](decision)
        return self.decide()

    def drive(self, decision: dict | None = None, *, until: str = "complete") -> dict:
        decision = decision or self.decide()
        for _ in range(60):
            if decision["disposition"] == until:
                return decision
            self.test.assertNotEqual(decision["disposition"], "blocked", decision)
            decision = self.run(decision)
        raise AssertionError(f"no {until} after 60 steps: {self.trace}")

    def record_external(self, kind: str) -> None:
        stage = wp.EXTERNAL_RESULT_KIND_STAGES[kind]
        status = self.verdicts[f"{stage}.manual"].pop(0)
        state = h.read_state(self.repo)
        work_item = state["work_items"][WI]
        if work_item["governing_workflow_version"] in ("2.1", "2.2") and (
                stage == "plan" or work_item["governing_workflow_version"] == "2.2"):
            text = verdict(status, rcid=self.current_content(stage), bundle=self.bundle_id(stage),
                           base=work_item["base_commit"], role=f"MANUAL_EXTERNAL_{stage.upper()}_REVIEW")
        else:
            text = verdict(status, bundle=self.bundle_id(stage), base=work_item["base_commit"])
        result = recorded(self.test, self.repo, kind, text)
        self.test.assertEqual((result["stage"], result["verdict"]), (stage, status))

    # -- what the commands compute -----------------------------------------

    def bundle_id(self, stage: str) -> str:
        directory = fingerprint.resolve_bundle_dir(self.repo.root, WI, **({"stage": "plan"} if stage == "plan" else {}))
        return fingerprint.compute_bundle_id(self.repo.root / directory)[0]

    def current_content(self, stage: str) -> str:
        if stage == "plan":
            return fingerprint.compute_review_content_id_plan_stage_for_work_item(self.repo.root, WI)[0]
        return current_I(self.repo)

    def tick(self) -> str:
        self.rounds += 1
        return f"t-{self.rounds}"

    # -- the automatic actions' writer sequences --------------------------

    def plan_start(self, arguments: dict) -> None:
        """`/milestone-plan`'s `[2.1]` creation: `route_work_item` at the
        config's default (committed), then the publication and the bound
        generation."""
        self.test.assertEqual(arguments, {})
        state = ws.route_work_item(
            h.read_state(self.repo), ws.load_config(self.repo.root), work_item_id=WI, work_item_type="process",
            work_item_kind="process", plan_path=_PLAN_DOC, registry_path=f"docs/ai-workflow/registry/{WI}-registry.json",
            mapping_path=f"docs/ai-workflow/requirements/{WI}-mapping.json", plan_revision=1, now=self.tick(),
            base_commit=self.repo.base, repo_root=self.repo.root)
        h.write_state(self.repo, state)
        h.commit_state(self.repo, "route the work item")
        h.publish_and_bind_plan_bundle(self.repo)

    def plan_review_local(self, arguments: dict) -> None:
        """`/review-plan`: the verdict file, then `record_local_plan_review`."""
        status = self.verdicts["plan.local"].pop(0)
        P, B = self.current_content("plan"), self.bundle_id("plan")
        write_feedback(self.repo, verdict(status, rcid=P, bundle=B, base=self.repo.base, role="LOCAL_MODEL_PLAN_REVIEW"))
        mutate(self.repo, ws.record_local_plan_review, verdict=status, bundle_id=B, review_content_id=P,
               round=self.rounds + 1, now=self.tick())

    def plan_apply_review(self, arguments: dict) -> None:
        """`/apply-plan-review`: the applied edit (committed), then the
        publication and the regeneration -- a `2.x` item's bound bundle
        (`AWAITING_LOCAL_PLAN_REVIEW`), a `"1"` item's next revision at its
        phase."""
        self.edits += 1
        plan = self.repo.root / _PLAN_DOC
        plan.write_text(plan.read_text() + f"applied finding {self.edits}\n")
        work_item = h.read_state(self.repo)["work_items"][WI]
        v1 = work_item["governing_workflow_version"] == "1"
        if v1:  # a "1" round advances the plan's and the registry's revision with the mirror
            revision = work_item["plan_revision"]
            plan.write_text(plan.read_text().replace(f"(Revision {revision})", f"(Revision {revision + 1})", 1))
            registry_path = self.repo.root / f"docs/ai-workflow/registry/{WI}-registry.json"
            registry = json.loads(registry_path.read_text())
            registry["plan_revision"] = revision + 1
            registry_path.write_text(json.dumps(registry) + "\n")
        h.git(self.repo, "add", "-A", "--", "docs/ai-workflow")
        h.git(self.repo, "reset", "-q", "--", str(ws.DEFAULT_STATE_PATH))
        h.git(self.repo, "commit", "-q", "-m", f"apply plan review round {self.edits}")
        if v1:
            mutate(self.repo, ws.publish_plan_revision, work_item["plan_revision"] + 1, self.tick())
            h.generate_plan_bundle(self.repo)
        else:
            h.publish_and_bind_plan_bundle(self.repo)

    def implementation_checkpoint(self, arguments: dict) -> None:
        """`/milestone-implement`'s `[2.1]` step 1: the checkpoint started,
        implemented, completed, and committed with its trailers."""
        checkpoint_id = arguments["checkpoint_id"]
        registry = json.loads((self.repo.root / f"docs/ai-workflow/registry/{WI}-registry.json").read_text())
        mutate(self.repo, ws.transition_checkpoint_in_progress, checkpoint_id, start_commit=self.repo.head(),
               now=self.tick())
        (self.repo.root / h.BUNDLE_ITEM_IMPLEMENTATION_PATH).write_text(f"{checkpoint_id}\n")
        mutate(self.repo, ws.complete_checkpoint, checkpoint_id, registry, now=self.tick(), repo_root=self.repo.root)
        h.git(self.repo, "add", "-A")
        h.git(self.repo, "commit", "-q", "-m",
              f"{checkpoint_id}\n\nWorkflow-Checkpoint: {checkpoint_id}\nWorkflow-Work-Item: {WI}")

    def implementation_self_review(self, arguments: dict) -> None:
        """`/milestone-implement` steps 2 to 4: the (no-op) self-review
        entry, then the bundle generation."""
        registry = json.loads((self.repo.root / f"docs/ai-workflow/registry/{WI}-registry.json").read_text())
        mutate(self.repo, ws.enter_self_reviewing_implementation, registry, now=self.tick())
        h.generate_implementation_bundle(self.repo)

    def implementation_review_local(self, arguments: dict) -> None:
        """`/review-implementation` at `"2.2"`: the verdict file, then
        `record_local_implementation_review`."""
        status = self.verdicts["implementation.local"].pop(0)
        I, B = self.current_content("implementation"), self.bundle_id("implementation")
        write_feedback(self.repo, verdict(status, rcid=I, bundle=B, base=self.repo.base,
                                          role="LOCAL_MODEL_IMPLEMENTATION_REVIEW"))
        mutate(self.repo, ws.record_local_implementation_review, verdict=status, bundle_id=B,
               review_content_id=I, round=self.rounds + 1, now=self.tick())

    def implementation_apply_review(self, arguments: dict) -> None:
        """`/apply-implementation-review`: at the external phase of a
        `"1"`/`"2.1"` item, `enter_applying_review_feedback`; then the fix,
        committed with the recorded verdict's state (the round's committed
        source phase), and the `post-fix` generation."""
        if h.read_state(self.repo)["work_items"][WI]["phase"] == "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW":
            mutate(self.repo, ws.enter_applying_review_feedback, now=self.tick())
            h.commit_state(self.repo, "enter APPLYING_REVIEW_FEEDBACK")
        self.edits += 1
        (self.repo.root / h.BUNDLE_ITEM_IMPLEMENTATION_PATH).write_text(f"fix {self.edits}\n")
        h.git(self.repo, "add", "--", h.BUNDLE_ITEM_IMPLEMENTATION_PATH, str(ws.DEFAULT_STATE_PATH))
        h.git(self.repo, "commit", "-q", "-m", f"apply implementation review round {self.edits}")
        h.generate_implementation_bundle(self.repo, stage="post-fix")

    def functional_prepare(self, arguments: dict) -> None:
        """`/prepare-functional-review`: the checklist-evidence commit for
        the item's round."""
        commit_checklist_evidence(self.repo, h.read_state(self.repo)["work_items"][WI]["implementation_revision"])

    # -- the user's writer sequences at the gates -------------------------

    def _feedback_fields(self) -> dict:
        return fingerprint.parse_review_feedback_binding_fields(ws.read_review_feedback(self.repo.root, WI))

    def user_approves_plan(self, decision: dict) -> None:
        """`/approve-review plan`: the gate wrapper, the basis, the record
        over the committed plan-stage projection, `apply_plan_approval`,
        and the approval commit."""
        state = h.read_state(self.repo)
        self.test.assertTrue(ws.plan_approval_gate_status(self.repo.root, state, WI)["reachable"])
        confirmation = f"I approve {WI} at the plan stage"
        B = self.bundle_id("plan")
        basis = ws.resolve_approval_basis(
            latest_round_status=self._feedback_fields()["status"],
            feedback_bundle_id=self._feedback_fields()["reviewed_bundle_id"], current_bundle_id=B,
            user_confirmation=confirmation, work_item_id=WI, stage="plan")
        digest, projection = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
            self.repo.root, WI, "HEAD", base=self.repo.base)
        record = ws.build_approval_record(
            basis=basis, stage="plan", user_confirmation=confirmation, now=self.tick(), reviewed_bundle_id=B,
            approved_review_content_id=digest, review_content_manifest=projection["review_content_manifest"])
        mutate(self.repo, ws.apply_plan_approval, record, self.tick())
        h.commit_state(self.repo, "approve the plan", {"Workflow-Plan-Approval": digest, "Workflow-Work-Item": WI})
        self.plan_basis = basis

    def user_approves_implementation(self, decision: dict) -> None:
        """`/approve-review implementation`: the gate wrapper's inputs, the
        basis, the record, `apply_technical_approval`, and the approval
        commit."""
        state = h.read_state(self.repo)
        gate = ws.technical_approval_gate_status(self.repo.root, state, WI)
        self.test.assertTrue(gate["reachable"], gate)
        confirmation = f"I approve {WI} at the implementation stage"
        inputs = gate["inputs"]
        basis = ws.resolve_approval_basis(
            latest_round_status=inputs["latest_round_status"],
            feedback_bundle_id=self._feedback_fields()["reviewed_bundle_id"], current_bundle_id=inputs["bundle_id"],
            user_confirmation=confirmation, work_item_id=WI, stage="implementation",
            pinned_block=inputs["pinned_block"])
        work_item = state["work_items"][WI]
        record = ws.build_approval_record(
            basis=basis, stage="implementation", user_confirmation=confirmation, now=self.tick(),
            reviewed_bundle_id=inputs["bundle_id"], approved_review_content_id=inputs["current_review_content_id"],
            review_content_manifest=[], reviewed_content_commit=work_item["reviewed_implementation_head"])
        mutate(self.repo, ws.apply_technical_approval, record, self.tick())
        h.commit_state(self.repo, "approve the implementation", {
            "Workflow-Technical-Approval": inputs["current_review_content_id"], "Workflow-Work-Item": WI})

    def user_accepts_milestone(self, decision: dict) -> None:
        """At the functional gate the user tests and accepts: the offered
        `milestone.accept` alternative, applied as `/accept-milestone`'s
        writer sequence (`complete_work_item`) and its completion commit."""
        self.test.assertIn("milestone.accept", [a["id"] for a in decision["alternatives"]])
        state = h.read_state(self.repo)
        is_terminal, _outstanding = ws.resolve_own_registry_completion_status(self.repo.root, state["work_items"][WI])
        self.test.assertTrue(ws.milestone_complete_gate_reachable(phase="AWAITING_FUNCTIONAL_REVIEW",
                                                                  is_terminal=is_terminal))
        h.write_state(self.repo, ws.complete_work_item(state, WI, self.tick(), repo_root=self.repo.root))
        h.commit_state(self.repo, "accept the milestone", {"Workflow-Work-Item": WI})


def _seed_unrouted(repo: h.ScratchRepo, default: str) -> None:
    """The repository `/milestone-plan` starts from, committed as the base:
    the declared plan-stage files and the installed scripts, a config whose
    default is `default`, and no work item yet. The checkpoints' file is
    classified at the plan stage too (excluded), as a real declaration
    classifies every path the work writes."""
    h.seed_bundle_item(repo, registry_checkpoints=LIFECYCLE_CHECKPOINTS)
    h.git(repo, "reset", "-q", "--hard", repo.base)  # drop the seeded entry: ids are never reused
    artifacts = repo.root / "docs" / "ai-workflow" / "registry" / f"{WI}-artifacts.json"
    declarations = json.loads(artifacts.read_text())
    declarations["plan_stage"]["excluded_paths"][h.BUNDLE_ITEM_IMPLEMENTATION_PATH] = "the checkpoints' output"
    artifacts.write_text(json.dumps(declarations) + "\n")
    write_config(repo, default)
    h.write_state(repo, h.base_state())
    h.git(repo, "add", "-A")
    h.git(repo, "commit", "-q", "-m", "no work item yet")
    repo.base = repo.head()


class TestLifecycleEndToEnd2_2(unittest.TestCase):
    """A `"2.2"` item from no work item to `MILESTONE_COMPLETE`, driven by
    `next-action`'s decisions only: a `REVISE` round at both stages of both
    reviews (each manual verdict recorded through `record-external-result`)
    and two checkpoints, the first of them same-phase progress."""

    VERDICTS = {
        "plan.local": ["REVISE", "APPROVE", "APPROVE"],
        "plan.manual": ["REVISE", "APPROVE"],
        "implementation.local": ["REVISE", "APPROVE", "APPROVE"],
        "implementation.manual": ["REVISE", "APPROVE"],
    }

    EXPECTED = [
        ("1", "plan.start", "progress"),
        ("12", "plan.review.local", "progress"),
        ("8", "plan.apply_review", "progress"),
        ("12", "plan.review.local", "gate_reached"),
        ("14", "plan.review.external", "plan_review_verdict"),
        ("8", "plan.apply_review", "progress"),
        ("12", "plan.review.local", "gate_reached"),
        ("14", "plan.review.external", "plan_review_verdict"),
        ("15", "plan.approve", "user"),
        ("23", "implementation.checkpoint", "progress"),
        ("23", "implementation.checkpoint", "progress"),
        ("24", "implementation.self_review", "progress"),
        ("26", "implementation.review.local", "progress"),
        ("36", "implementation.apply_review", "progress"),
        ("26", "implementation.review.local", "gate_reached"),
        ("28", "implementation.review.external", "implementation_review_verdict"),
        ("36", "implementation.apply_review", "progress"),
        ("26", "implementation.review.local", "gate_reached"),
        ("28", "implementation.review.external", "implementation_review_verdict"),
        ("29", "implementation.approve", "user"),
        ("37", "functional.prepare", "gate_reached"),
        ("39", "functional.review", "user"),
    ]

    def test_from_no_work_item_to_milestone_complete(self):
        with h.ScratchRepo() as repo:
            _seed_unrouted(repo, "2.2")
            run = _Lifecycle(self, repo, self.VERDICTS)
            final = run.drive()
            self.assertEqual(run.trace, self.EXPECTED)
            self.assertEqual((final["row"], final["disposition"], final["action"]), ("40", "complete", None))
            state = h.read_state(repo)
            self.assertEqual((state["work_items"][WI]["phase"], state["active_work_item_id"]),
                             ("MILESTONE_COMPLETE", None))
            self.assertEqual(run.plan_basis, "EXTERNAL_APPROVE")
            self.assertTrue(all(value == [] for value in run.verdicts.values()), run.verdicts)

    def test_the_first_checkpoint_is_same_phase_progress(self):
        with h.ScratchRepo() as repo:
            _seed_unrouted(repo, "2.2")
            run = _Lifecycle(self, repo, self.VERDICTS)
            decision = run.drive(until="human_gate")  # the plan approval gate
            decision = run.run(decision)
            self.assertEqual((decision["row"], decision["action"]["arguments"]),
                             ("23", {"work_item_id": WI, "checkpoint_id": "C1"}))
            run.implementation_checkpoint(decision["action"]["arguments"])
            result = reconciled(self, repo, decision)
            self.assertEqual((result["class"], result["from"]["phase"], result["to"]["phase"],
                              result["evidence"]["completed_checkpoints"]),
                             ("progress", "IMPLEMENTING", "IMPLEMENTING", ["C1"]))
            self.assertEqual(result["next"]["action"]["arguments"]["checkpoint_id"], "C2")

    def test_a_gates_own_decision_is_refused_by_reconcile(self):
        with h.ScratchRepo() as repo:
            _seed_unrouted(repo, "2.2")
            run = _Lifecycle(self, repo, self.VERDICTS)
            gate = run.drive(until="human_gate")
            self.assertEqual(gate["action"]["id"], "plan.approve")
            body, code = reconcile(repo, gate)
            self.assertEqual((code, body["error"]["code"]), (wp.EXIT_INVALID_REQUEST, "invalid_request"))


class TestLifecycleEndToEndV1(unittest.TestCase):
    """The two `"1"` runs (`LPR-R3-001`), each from a pre-constructed
    `"1"` state entry in the phase the named 2.6.0 writer persists, using
    only what the `"1"` commands write."""

    def test_the_plan_round(self):
        """From `AWAITING_EXTERNAL_PLAN_REVIEW` as `publish_plan_revision`'s
        `"1"` branch writes it, to row 6a at `IMPLEMENTING`."""
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW")
            h.generate_plan_bundle(repo)
            run = _Lifecycle(self, repo, {"plan.manual": ["REVISE"]})
            decision = run.decide()
            self.assertEqual((decision["row"], decision["disposition"]), ("21", "external_gate"))
            decision = run.run(decision)
            self.assertEqual((decision["row"], decision["action"]["id"]), ("18", "plan.apply_review"))
            decision = run.run(decision)
            self.assertEqual(run.trace[-1], ("18", "plan.apply_review", "gate_reached"))
            self.assertEqual((decision["row"], decision["action"]["id"]), ("20", "plan.approve"))
            decision = run.run(decision)
            self.assertEqual(run.plan_basis, "USER_OVERRIDE")
            self.assertEqual((decision["row"], decision["disposition"], decision["reason"]["code"],
                              decision["snapshot"]["phase"]),
                             ("6a", "blocked", "v1_state_not_advanced", "IMPLEMENTING"))
            self.assertIsNone(decision["action"])

    def test_the_implementation_round(self):
        """From `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` as
        `record_bundle_generation` writes it, with `registry_path: null`, to
        `complete`."""
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="SELF_REVIEWING_IMPLEMENTATION",
                               registry_path=None)
            repo.commit("implement", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
            h.generate_implementation_bundle(repo)
            run = _Lifecycle(self, repo, {"implementation.manual": ["REVISE", "APPROVE"]})
            decision = run.decide()
            self.assertEqual((decision["row"], decision["disposition"]), ("35", "external_gate"))
            final = run.drive(decision)
            self.assertEqual(run.trace, [
                ("35", "implementation.review.external", "implementation_review_verdict"),
                ("32", "implementation.apply_review", "gate_reached"),
                ("35", "implementation.review.external", "implementation_review_verdict"),
                ("33", "implementation.approve", "user"),
                ("37", "functional.prepare", "gate_reached"),
                ("39", "functional.review", "user"),
            ])
            self.assertEqual((final["row"], final["disposition"]), ("40", "complete"))


# ---------------------------------------------------------------------------
# workflow-2.8.0 CP6 (`D-GP-Rows`, `D-GP-Compat`): protocol 1.1. The golden
# all-human equivalence matrix (INV-1), the rows a gate policy adds, the new
# actions' edges, and the two evidence result kinds.
# ---------------------------------------------------------------------------

import contextlib  # noqa: E402
import copy  # noqa: E402
import hashlib  # noqa: E402
import tempfile  # noqa: E402

import workflow_gate_policy_test as gpt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
V270_TAG = "v2.7.0"
V270_MODULES = ("workflow_fingerprint.py", "workflow_state.py", "workflow_protocol.py")
NEW_ROW_IDS = ("14a", "14b", "28a", "28b", "38d", "38e", "38f", "38g", "38h", "38i")
NEW_ACTION_IDS = ("plan.satisfy", "implementation.satisfy", "acceptance.satisfy", "pr.apply_review",
                  "functional.evidence.external", "pr.review.external")
#: The 1.1 to 1.2 delta against v2.8.0 (workflow-2.9.0, `I1`). CP3 adds
#: `legacy.retire`; CP5 adds `implementation.resume`.
NEW_1_2_ACTION_IDS = ("legacy.retire", "implementation.resume")


class V270:
    """The published 2.7.0 modules, read from the immutable tag `v2.7.0` into a
    scratch directory and run as a subprocess: the golden outputs are produced
    by the 2.7.0 code itself (INV-1), never restated here. `available` is false
    when the tag is not in the checkout this suite runs from (an installation
    holds no history of the release source)."""

    directory: Path | None = None
    available = False
    reason = f"the tag {V270_TAG} is not in this checkout"

    @classmethod
    def load(cls) -> None:
        if cls.directory is not None or cls.available:
            return
        try:
            directory = Path(tempfile.mkdtemp(prefix="workflow-v270-"))
            for name in V270_MODULES:
                blob = subprocess.run(["git", "show", f"{V270_TAG}:payload/scripts/{name}"], cwd=REPO_ROOT,
                                      capture_output=True, check=True).stdout
                (directory / name).write_bytes(blob)
        except (OSError, subprocess.CalledProcessError):
            return
        cls.directory, cls.available = directory, True

    @classmethod
    def run(cls, root: Path, *argv: str, now: str | None = None) -> tuple[dict, int]:
        return run_protocol(cls.directory / "workflow_protocol.py", root, *argv, now=now)


#: Runs a protocol module with its `_utc_now` pinned to `argv[2]`: a writer's
#: timestamps (and so the state identity) then do not depend on which second
#: each of two compared runs lands in.
_PINNED_CLOCK = ("import sys; sys.path.insert(0, sys.argv[1]); import workflow_protocol as module; "
                 "module._utc_now = lambda: sys.argv[2]; sys.exit(module.main(sys.argv[3:]))")


def run_protocol(script: Path, root: Path, *argv: str, now: str | None = None) -> tuple[dict, int]:
    command = ([sys.executable, str(script)] if now is None
               else [sys.executable, "-c", _PINNED_CLOCK, str(script.parent), now])
    out = subprocess.run([*command, "--repo-root", str(root), *argv], capture_output=True, text=True)
    return json.loads(out.stdout), out.returncode


def run_new(root: Path, *argv: str, now: str | None = None) -> tuple[dict, int]:
    return run_protocol(SCRIPT, root, *argv, now=now)


PINNED_NOW = "2026-10-03T12:00:00Z"


def without_release(body: dict) -> dict:
    """An envelope minus the two fields D-GP-Compat delta 1 changes."""
    body = copy.deepcopy(body)
    body["protocol"]["version"] = None
    body["workflow_release"] = None
    return body


def equivalence_scenarios() -> list:
    """`(name, builder)`: every persisted phase the catalogue's builders reach,
    at every governing version they cover, each a real repository."""
    cases = [(name, build) for name, build in _scenarios().items() if name != "no item"]
    cases.append(("no item", _scenarios()["no item"]))
    for row_id, versions, build in _automatic_scenarios():
        for version in versions:
            cases.append((f"row{row_id}@{version}", lambda repo, build=build, version=version: build(repo, version)))
    for version in wp.SUPPORTED_GOVERNING_VERSIONS:
        cases.append((f"functional@{version}", lambda repo, version=version: functional_item(repo, version)))
        cases.append((f"functional-incomplete@{version}",
                      lambda repo, version=version: functional_item(repo, version, complete=False)))
    for phase in ("AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_PLAN_APPROVAL",
                  "REVISING_PLAN"):
        for version in ("2.1", "2.2"):
            cases.append((f"{phase}@{version}", lambda repo, phase=phase, version=version:
                          plan_item_at(repo, phase, version)))
    return cases


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestAllHumanEquivalence(unittest.TestCase):
    """INV-1 (`REQ-3`): with every gate human, `next-action`, `verify`,
    `describe` and `record-external-result` equal the 2.7.0 module's, byte for
    byte, apart from the deltas D-GP-Compat lists. Every fixture repository
    here carries the harness's all-human `GATE_POLICY.json`, committed and not
    adopted."""

    @classmethod
    def setUpClass(cls):
        V270.load()

    def setUp(self):
        if not V270.available:
            self.skipTest(V270.reason)

    def test_next_action_is_byte_equal_across_every_scenario(self):
        for name, build in equivalence_scenarios():
            with self.subTest(scenario=name), h.ScratchRepo() as repo:
                build(repo)
                old, old_code = V270.run(repo.root, "next-action")
                new, new_code = run_new(repo.root, "next-action")
                self.assertEqual(old_code, new_code, (old, new))
                assert_valid(new)
                # the 1.2 text-only exemptions (workflow-2.9.0, rows 6a and 38b's
                # `v2.6.0-003` text), enumerated in `EXEMPT_1_2`; row, disposition,
                # action and reason code still must match
                self.assertEqual(json.dumps(_normalize_1_2(old), sort_keys=True),
                                 json.dumps(_normalize_1_2(new), sort_keys=True))

    def test_verify_differs_only_by_the_advisory_gate_policy_check(self):
        for name, build in equivalence_scenarios()[:12]:
            with self.subTest(scenario=name), h.ScratchRepo() as repo:
                build(repo)
                old, _ = V270.run(repo.root, "verify")
                new, _ = run_new(repo.root, "verify")
                assert_valid(new)
                new_checks = [c for c in new["result"]["checks"] if c["id"] != "gate_policy"]
                self.assertEqual(new["result"]["healthy"], old["result"]["healthy"])
                # `installation_release_matches` names the release the scripts are, by design
                strip = lambda checks: [{k: v for k, v in c.items() if k != "detail"} if c["id"] ==
                                        "installation_release_matches" else c for c in checks]
                self.assertEqual(strip(new_checks), strip(old["result"]["checks"]))
                self.assertEqual([c["status"] for c in new["result"]["checks"] if c["id"] == "gate_policy"], ["warn"],
                                 "the fixtures' committed all-human file is not adopted")

    def test_verify_with_no_file_an_unadopted_file_and_an_invalid_file(self):
        """D-GP-Compat delta 3: the `gate_policy` check is advisory, so the
        overall status is 2.7.0's in every case."""
        for label, body, status in (("no file", None, "pass"), ("unadopted file", h.ALL_HUMAN_GATE_POLICY, "warn"),
                                    ("invalid file", "{not json", "fail")):
            with self.subTest(file=label), h.ScratchRepo() as repo:
                h.seed_bundle_item(repo, phase="PLANNING", gate_policy=None)
                if body is not None:
                    write_policy_text(repo, body if isinstance(body, str) else json.dumps(body))
                old, _ = V270.run(repo.root, "verify")
                new, _ = run_new(repo.root, "verify")
                assert_valid(new)
                checks = {c["id"]: c for c in new["result"]["checks"]}
                self.assertEqual(checks["gate_policy"]["status"], status)
                self.assertEqual(new["result"]["healthy"], old["result"]["healthy"])
                self.assertEqual([c["id"] for c in new["result"]["checks"] if c["id"] != "gate_policy"],
                                 [c["id"] for c in old["result"]["checks"]])
                self.assertEqual([c["status"] for c in new["result"]["checks"] if c["id"] != "gate_policy"],
                                 [c["status"] for c in old["result"]["checks"]])

    def test_describe_differs_only_by_the_listed_additions(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state())
            old, _ = V270.run(repo.root, "describe")
            new, _ = run_new(repo.root, "describe")
        assert_valid(new)
        old_result, new_result = old["result"], new["result"]
        self.assertEqual((old_result["protocol_version"], new_result["protocol_version"]), ("1.0", "1.2"))
        for key in ("supported_protocol_majors", "supported_governing_versions"):
            self.assertEqual(new_result[key], old_result[key])
        old_caps, new_caps = old_result["capabilities"], new_result["capabilities"]
        for key in ("operations", "dispositions", "artifact_kinds", "error_codes"):
            self.assertEqual(new_caps[key], old_caps[key], key)
        self.assertEqual(sorted(set(new_caps["action_ids"]) - set(old_caps["action_ids"])),
                         sorted(NEW_ACTION_IDS + NEW_1_2_ACTION_IDS))
        self.assertLessEqual(set(old_caps["action_ids"]), set(new_caps["action_ids"]))
        self.assertEqual(sorted(set(new_caps["external_result_kinds"]) - set(old_caps["external_result_kinds"])),
                         ["functional_evidence", "pr_review_result"])
        self.assertEqual((old_caps["reserved_result_kinds"], new_caps["reserved_result_kinds"]),
                         (["functional_evidence", "pr_review_result"], []))

    def test_record_external_result_writes_the_same_bytes_and_no_reviewer_model(self):
        """The all-human golden case under the new default `require`
        (`LPR-R16-002`): no `Reviewer model:` line and no ledger key."""
        def plan_case(repo):
            ids = plan_item_at(repo, "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
            return "plan_review_verdict", verdict("APPROVE", rcid=ids["P"], bundle=ids["B"], base=repo.base,
                                                  role="MANUAL_EXTERNAL_PLAN_REVIEW")

        def implementation_case(repo):
            ids = implementation_at_manual(repo)
            return "implementation_review_verdict", verdict("APPROVE", rcid=ids["I"], bundle=ids["B"],
                                                            base=repo.base, role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")

        for name, case in (("plan", plan_case), ("implementation", implementation_case)):
            with self.subTest(stage=name), h.ScratchRepo() as repo:
                kind, text = case(repo)
                verdict_file = repo.root.parent / f"{repo.root.name}-verdict.md"
                verdict_file.write_text(text)
                state_path = repo.root / ws.DEFAULT_STATE_PATH
                feedback = repo.root / f".ai-review/{WI}/feedback/REVIEW_FEEDBACK.md"
                snapshot = (state_path.read_bytes(), feedback.read_bytes() if feedback.exists() else None)
                argv = ("record-external-result", "--work-item", WI, "--kind", kind, "--input", str(verdict_file))
                try:
                    old, old_code = V270.run(repo.root, *argv, now=PINNED_NOW)
                    written = (state_path.read_bytes(), feedback.read_bytes())
                    state_path.write_bytes(snapshot[0])  # restore, so both modules start from one state
                    if snapshot[1] is None:
                        feedback.unlink()
                    else:
                        feedback.write_bytes(snapshot[1])
                    new, new_code = run_new(repo.root, *argv, now=PINNED_NOW)
                    self.assertEqual((old_code, new_code), (0, 0), (old, new))
                    assert_valid(new)
                    self.assertEqual(json.dumps(without_release(old), sort_keys=True),
                                     json.dumps(without_release(new), sort_keys=True))
                    self.assertEqual((state_path.read_bytes(), feedback.read_bytes()), written,
                                     "the state and the feedback file are byte-equal to 2.7.0's writes")
                    stages = h.read_state(repo)["work_items"][WI][
                        "plan_review_stages" if name == "plan" else "implementation_review_stages"]
                    self.assertNotIn("reviewer_model", json.dumps(stages))
                    self.assertNotIn("Reviewer model:", feedback.read_text())
                finally:
                    verdict_file.unlink(missing_ok=True)

    def test_an_adopted_all_human_file_changes_no_decision(self):
        """The adopted fixture (D-GP-Compat delta 7): the adoption commit adds
        top-level state fields 2.7.0 cannot read, so the comparison is the
        2.8.0 output before and after, at phases that have no bundle for the
        commit to stale."""
        builders = {"implementing": _scenarios()["implementing"], "functional": _scenarios()["functional"],
                    "no item": _scenarios()["no item"]}
        for name, build in builders.items():
            with self.subTest(scenario=name), h.ScratchRepo() as repo:
                build(repo)
                before, _ = run_new(repo.root, "next-action")
                policy = json.loads((repo.root / gpt.POLICY_REL).read_text()) if (repo.root / gpt.POLICY_REL).exists() \
                    else h.ALL_HUMAN_GATE_POLICY
                commit_policy_file(repo, policy)
                if name == "no item":
                    continue  # adoption needs a committed state; the other two cover it
                ws.adopt_gate_policy(repo.root, confirmation=gpt.confirmation_for(policy), now="2026-10-03T12:00:00Z")
                after, _ = run_new(repo.root, "next-action")
                for body in (before, after):
                    if "basis" in body["result"]:
                        body["result"]["basis"]["head"] = None
                self.assertEqual(before, after)

    def test_under_the_default_only_the_gate_rows_differ(self):
        """The default configuration's `next-action` equals the all-human one
        everywhere except where a gate's human row is replaced by one of the
        new rows (D-GP-Compat, "The default is different, on purpose"). Each
        scenario is built twice, once under the harness's all-human file and
        once with no file (the default); a committed human setting would
        survive its deletion (the floor), so the default is seeded, not edited."""
        replaced = {"15": {"14a", "14b"}, "29": {"28a", "28b"}, "39": {"38e", "38f", "38g", "38h", "38i"}}
        seed = h.seed_bundle_item

        def default_seed(*args, **kwargs):
            kwargs["gate_policy"] = None
            return seed(*args, **kwargs)

        seen_new = set()
        for name, build in equivalence_scenarios():
            with self.subTest(scenario=name):
                with h.ScratchRepo() as repo:
                    build(repo)
                    human = run_new(repo.root, "next-action")[0]["result"]
                with h.ScratchRepo() as repo, mock.patch.object(h, "seed_bundle_item", default_seed):
                    build(repo)
                    default_body = run_new(repo.root, "next-action")[0]
                    assert_valid(default_body)
                    default = default_body["result"]
                if default["row"] == human["row"]:
                    self.assertEqual((default["reason"]["code"], default["disposition"]),
                                     (human["reason"]["code"], human["disposition"]))
                    self.assertNotIn("policy", default)
                else:
                    self.assertIn(default["row"], replaced.get(human["row"], set()), (human["row"], default["row"]))
                    self.assertIn("policy", default)
                    seen_new.add(default["row"])
        self.assertLessEqual({"14b", "28b"}, seen_new, seen_new)

    def test_the_policy_object_appears_only_on_the_new_rows(self):
        for name, build in equivalence_scenarios():
            with self.subTest(scenario=name), h.ScratchRepo() as repo:
                build(repo)
                result = next_action(repo)
                self.assertNotIn("policy", result, name)
                self.assertNotIn(result["row"], NEW_ROW_IDS)


def at_approval(repo: h.ScratchRepo, stage: str, *, version: str = "2.2", policy="default") -> dict:
    """An item whose manual review stage was ingested under the default policy
    (so its ledger carries the audit keys): `AWAITING_PLAN_APPROVAL` for
    `plan`, `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` for `implementation`."""
    if stage == "plan":
        P, B = gpt.plan_at_manual(repo, version=version)
        gpt.ingest(repo, "plan", gpt.manual_text("plan", P, B, base=repo.base))
        return {"P": P, "B": B}
    rcid, bundle = gpt.implementation_at_manual(repo)
    gpt.ingest(repo, "implementation", gpt.manual_text("implementation", rcid, bundle, base=repo.base))
    return {"I": rcid, "B": bundle}


def complete_item(ar) -> None:
    """`MILESTONE_COMPLETE`, as `complete_work_item` leaves it: the active
    pointer is reset (a reopen never sets it again)."""
    state = ar.state()
    state["work_items"][WI]["phase"] = "MILESTONE_COMPLETE"
    state["active_work_item_id"] = None
    h.write_state(ar.repo, state)


def write_policy_text(repo: h.ScratchRepo, text: str) -> None:
    path = repo.root / gpt.POLICY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    gpt.g.clear_caches()


def write_policy_file(repo: h.ScratchRepo, body: dict) -> None:
    path = repo.root / gpt.POLICY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    gpt.g.clear_caches()


def commit_policy_file(repo: h.ScratchRepo, body: dict) -> None:
    """`write_policy_file` plus a commit: an adoption refuses a file that
    differs from `HEAD`'s."""
    write_policy_file(repo, body)
    h.git(repo, "add", "--", str(gpt.POLICY_REL))
    if h.git(repo, "diff", "--cached", "--name-only"):
        h.git(repo, "commit", "-q", "-m", "policy file")


class TestPlanAndTechnicalGateRows(unittest.TestCase):
    """Rows `14a`/`14b` and `28a`/`28b` (D-GP-Rows)."""

    def assert_satisfy(self, result, row, action, gate):
        self.assertEqual((result["row"], result["disposition"], result["action"]["id"]), (row, "validation", action))
        self.assertEqual(result["action"]["worker"]["role"], "validator")
        self.assertFalse(result["action"]["worker"]["user_only"])
        self.assertEqual(result["action"]["allowed_results"], ["progress", "gate_reached", "no_progress"])
        self.assertEqual({k: result["policy"][k] for k in ("gate", "mode")}, {"gate": gate, "mode": "automatic"})
        self.assertEqual(result["policy"]["source"], "default")
        self.assertEqual(len(result["policy"]["digest"]), 64)
        self.assertNotIn("gate_lowering", result["policy"])

    def test_a_satisfiable_plan_gate_emits_plan_satisfy_at_both_two_stage_versions(self):
        for version in ("2.1", "2.2"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                at_approval(repo, "plan", version=version)
                result = next_action(repo)
                self.assert_satisfy(result, "14a", "plan.satisfy", "plan_approval")
                self.assertEqual(result["action"]["invocation"], f"/satisfy-gate plan {WI}")

    def test_a_satisfiable_technical_gate_emits_implementation_satisfy_at_2_2_only(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "implementation")
            result = next_action(repo)
            self.assert_satisfy(result, "28a", "implementation.satisfy", "technical_approval")
        for version in ("1", "2.1"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                ids = implementation_item_at(repo, version)
                write_policy_file(repo, {"schema_version": 1, "human_approval": False})
                write_feedback(repo, verdict("APPROVE", bundle=ids["B"], base=repo.base))
                self.assertEqual(next_action(repo)["row"], "33", "the technical gate of a 1 or 2.1 item stays human")

    def test_a_version_1_plan_gate_stays_human(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW",
                               gate_policy=None)
            h.generate_plan_bundle(repo)
            write_feedback(repo, verdict("APPROVE", bundle=current_bundle_id(repo), base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"]), ("19", "human_gate"))
            self.assertNotIn("policy", result)

    def test_each_toggle_changes_only_its_own_gates_rows(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            write_policy_file(repo, {"schema_version": 1, "gates": {"technical_approval": {"human": True},
                                                                    "acceptance": {"human": True}}})
            self.assertEqual(next_action(repo)["row"], "14a", "the technical and acceptance toggles do not touch it")
            write_policy_file(repo, gpt.PLAN_HUMAN)
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("15", "plan.approve"))
            self.assertNotIn("policy", result)
        with h.ScratchRepo() as repo:
            at_approval(repo, "implementation")
            write_policy_file(repo, gpt.PLAN_HUMAN)
            self.assertEqual(next_action(repo)["row"], "28a")
            write_policy_file(repo, {"schema_version": 1, "gates": {"technical_approval": {"human": True}}})
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("29", "implementation.approve"))

    def unaudited(self, repo, stage):
        state = h.read_state(repo)
        key = "plan_review_stages" if stage == "plan" else "implementation_review_stages"
        manual = gpt.MANUAL_PLAN if stage == "plan" else gpt.MANUAL_IMPL
        for audit_key in ("verdict_sha256", "run_ref", "reviewer_model"):
            state["work_items"][WI][key][manual].pop(audit_key, None)  # recorded while the gate was human
        h.write_state(repo, state)

    def test_an_unmet_requirement_is_blocked_with_only_executable_remedies(self):
        for stage, row, human_row, command, withdraw in (
                ("plan", "14b", "15", "/approve-review plan", "/milestone-plan"),
                ("implementation", "28b", "29", "/approve-review implementation", "/apply-implementation-review")):
            with self.subTest(stage=stage), h.ScratchRepo() as repo:
                at_approval(repo, stage)
                self.unaudited(repo, stage)
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["action"]), (row, "blocked", None))
                self.assertEqual(result["reason"]["code"], "gate_evidence_unmet")
                self.assertIn("review_evidence_audited", result["reason"]["text"])
                remedy = result["reason"]["remedy"]
                self.assertIn(command, remedy)
                self.assertIn(withdraw, remedy)
                self.assertNotIn("/adopt-gate-policy", remedy, "adoption is not a remedy at an open bundle")
                self.assertNotIn("/record-manual", remedy)
                self.assertEqual(result["policy"]["mode"], "automatic")

    def test_one_family_blocks_with_distinct_reviewer_models_unmet_and_require_empty_satisfies(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            state = h.read_state(repo)
            stages = state["work_items"][WI]["plan_review_stages"]
            stages[gpt.MANUAL_PLAN]["reviewer_model"] = stages[gpt.LOCAL_PLAN]["reviewer_model"]
            h.write_state(repo, state)
            result = next_action(repo)
            self.assertEqual(result["row"], "14b")
            self.assertIn("distinct_reviewer_models", result["reason"]["text"])
            self.assertIn("/milestone-plan", result["reason"]["remedy"])
            write_policy_file(repo, {"schema_version": 1, "gates": {"plan_approval": {"require": []}}})
            self.assertEqual(next_action(repo)["row"], "14b", "a loosening file is ignored until adopted")

    def test_an_unreachable_wrapper_keeps_its_rows_16_and_30_remedies(self):
        """`LPR-R5-003`: `14a`/`14b` and `28a`/`28b` match only a reachable
        wrapper, so an unreachable one reaches row 16 or 30 with that cause's
        remedy, as under all-human."""
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            move_head(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("16", "bundle_generation_mismatch"))
            self.assertNotIn("policy", result)
        with h.ScratchRepo() as repo:
            at_approval(repo, "implementation")
            move_head(repo)
            result = next_action(repo)
            self.assertEqual((result["row"], result["reason"]["code"]), ("30", "bundle_generation_mismatch"))
            self.assertEqual([a["id"] for a in result["alternatives"]], ["implementation.recover_provenance"])
        with h.ScratchRepo() as repo:
            at_approval(repo, "implementation")
            reject_bundle(repo)
            self.assertEqual(next_action(repo)["row"], "6", "a rejected bundle precedes every gate row")

    def test_a_gate_lowering_adoption_is_named_on_the_decision(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="2.2", phase="PLANNING", gate_policy=None)
            policy = {"schema_version": 1, "gates": {"plan_approval": {"require": []}}}
            commit_policy_file(repo, policy)
            ws.adopt_gate_policy(repo.root, confirmation=gpt.confirmation_for(policy), now="2026-10-03T12:00:00Z")
            gpt.g.clear_caches()
            P, B = h.publish_and_bind_plan_bundle(repo)
            gpt.record_local(repo, "plan", P, B, model=gpt.CLAUDE)
            gpt.ingest(repo, "plan", gpt.manual_text("plan", P, B, base=repo.base, model=None))
            result = next_action(repo)
            self.assertEqual(result["row"], "14a", "an adopted empty require is satisfied with no declared family")
            self.assertEqual(result["policy"]["source"], "adopted")
            self.assertEqual(result["policy"]["gate_lowering"]["lowered"], ["plan_approval.require"])
            self.assertEqual(len(result["policy"]["gate_lowering"]["sha256"]), 64)


class TestAcceptanceRows(unittest.TestCase):
    """Rows `38d` to `38i` (D-GP-Rows, D-GP-Acceptance)."""

    def repo(self, **kw):
        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
        commit_checklist_evidence(ar.repo, 1)
        return ar

    def decide(self, ar, **kw) -> dict:
        gpt.g.clear_caches()
        body, code = call("--repo-root", str(ar.root), "next-action", "--work-item", WI)
        self.assertEqual(code, 0, body)
        return body["result"]

    def test_the_acceptance_gate_walks_its_requirements_in_order(self):
        ar = self.repo()
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"], result["action"]["id"], result["satisfied_by"]),
                         ("38e", "external_gate", "functional.evidence.external", "functional_evidence"))
        self.assertEqual(result["action"]["worker"]["role"], "external")
        self.assertEqual(result["policy"]["gate"], "acceptance")
        ar.flow()
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                         ("38h", "validation", "acceptance.satisfy"), "no stored fact: pending the query")
        self.assertEqual(result["action"]["invocation"], f"/satisfy-gate acceptance {WI}")
        ar.query([gpt.pr_record(ar.anchor, checks="pending")])
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"], result["action"]["id"], result["satisfied_by"]),
                         ("38f", "external_gate", "pr.review.external", "pr_review_result"))
        self.assertIn("/satisfy-gate acceptance", result["reason"]["remedy"])
        ar.query([gpt.pr_record(ar.anchor)])
        self.assertEqual(self.decide(ar)["row"], "38h")

    def test_a_pull_request_that_is_none_or_behind_is_38f(self):
        ar = self.repo()
        ar.flow()
        ar.query("[]")
        self.assertEqual(self.decide(ar)["row"], "38f")
        behind = ar.protected_commit("a later protected change")
        ar.query([gpt.pr_record(ar.anchor)])
        gpt.g.clear_caches()
        ar.set_state(reviewed_implementation_head=ar.anchor)
        result = self.decide(ar)
        self.assertIn(result["row"], ("38f", "38i", "38d"), (behind, result["row"]))

    def test_a_failed_flow_or_stale_approval_is_38i_blocked(self):
        ar = self.repo()
        ar.flow(status="failed")
        ar.query([gpt.pr_record(ar.anchor)])
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"], result["action"], result["reason"]["code"]),
                         ("38i", "blocked", None, "gate_evidence_unmet"))
        self.assertIn("functional_flows_passed", result["reason"]["text"])
        self.assertIn("/apply-functional-review", result["reason"]["remedy"])
        ar = self.repo()
        ar.flow()
        ar.set_state(technical_approval=None)
        self.assertEqual(self.decide(ar)["row"], "38i", "a stale technical approval is blocked, not obtainable")

    def test_a_red_workflow_fact_reopens_before_any_acceptance_row(self):
        ar = self.repo()
        ar.flow()
        ar.query([gpt.pr_record(ar.anchor, checks="failure")])
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"], result["action"]["id"], result["reason"]["code"]),
                         ("38d", "automatic", "pr.apply_review", "pr_review_actionable"))
        self.assertEqual(result["action"]["worker"]["role"], "applier")
        self.assertEqual(result["policy"]["gate"], "pr_review")
        ar.query([gpt.pr_record(ar.anchor, decision="CHANGES_REQUESTED",
                                reviews=gpt.changes_requested(ar.anchor))])
        self.assertEqual(self.decide(ar)["row"], "38d")

    def test_with_pr_review_off_a_red_fact_is_blocked_not_reopened(self):
        ar = self.repo()
        ar.write_adopted({"schema_version": 1, "pr_review": {"enabled": False}})
        ar.flow()
        ar.query([gpt.pr_record(ar.anchor, checks="failure")])
        result = self.decide(ar)
        self.assertEqual(result["row"], "38i")
        self.assertIn("ci_green", result["reason"]["text"])

    def test_an_applied_key_no_longer_matches_38d(self):
        ar = self.repo()
        ar.flow()
        ar.query([gpt.pr_record(ar.anchor, checks="failure")])
        self.assertEqual(self.decide(ar)["row"], "38d")
        key = g_key = gpt.g.cause_key("checks_failed", ar.evidence()["pr"])
        state = ws.mark_pr_key_applied(ar.state(), WI, g_key, now="t-applied")
        h.write_state(ar.repo, state)
        self.assertEqual(self.decide(ar)["row"], "38i")

    def test_a_reported_fact_only_triggers_and_never_satisfies(self):
        """`LPR-R10-003`: with only an `orchestrator_forge` fact stored (all
        green or red), the acceptance rows emit `38h`, never `38f`/`38g`;
        `38d` reads it as the query trigger only."""
        ar = self.repo()
        ar.flow()
        ar.report([gpt.pr_record(ar.anchor)])
        result = self.decide(ar)
        self.assertEqual(result["row"], "38d", "a reported fact that differs arms the Workflow's own query")
        self.assertEqual(result["reason"]["code"], "pr_query_due")
        ar.write_adopted({"schema_version": 1, "pr_review": {"enabled": False}})
        for records in ([gpt.pr_record(ar.anchor)], [gpt.pr_record(ar.anchor, checks="failure")]):
            ar.report(records)
            result = self.decide(ar)
            self.assertEqual(result["row"], "38h", "with PR review off nothing triggers, and nothing satisfies")
            self.assertIsNone(ar.evidence()["pr"])

    def test_a_successful_query_clears_the_trigger_and_the_next_decision_is_not_38d(self):
        ar = self.repo()
        ar.flow()
        ar.report([gpt.pr_record(ar.anchor, number=9)])
        self.assertEqual(self.decide(ar)["row"], "38d")
        ar.query([gpt.pr_record(ar.anchor, number=9)])
        result = self.decide(ar)
        self.assertEqual(result["row"], "38h")
        ar.report([gpt.pr_record(ar.anchor, number=11)])
        self.assertEqual(self.decide(ar)["row"], "38d", "a newer report with another PR number arms it again")
        ar.query("[]")  # state none: still a successful query
        self.assertNotEqual(self.decide(ar)["row"], "38d")

    def test_a_newer_report_over_a_stale_stored_fact_is_38h_not_38f(self):
        """`LPR-R11-001`."""
        ar = self.repo()
        ar.flow()
        ar.query([gpt.pr_record(ar.anchor, checks="pending")])
        self.assertEqual(self.decide(ar)["row"], "38f")
        ar.write_adopted({"schema_version": 1, "pr_review": {"enabled": False}})
        ar.report([gpt.pr_record(ar.anchor)])
        result = self.decide(ar)
        self.assertEqual(result["row"], "38h")
        self.assertIn("queries GitHub itself", result["reason"]["text"])

    def test_requires_pr_approved_in_both_modes(self):
        policy = {"schema_version": 1, "gates": {"acceptance": {"requires_pr_approved": True}}}
        ar = self.repo()
        write_policy_file(ar.repo, policy)
        ar.flow()
        ar.query([gpt.pr_record(ar.anchor)])
        result = self.decide(ar)
        self.assertEqual((result["row"], result["satisfied_by"]), ("38g", "pr_review_result"))
        self.assertIn("/satisfy-gate acceptance", result["reason"]["remedy"])
        ar.query([gpt.pr_record(ar.anchor, decision="APPROVED", reviews=gpt.approved(ar.anchor))])
        self.assertEqual(self.decide(ar)["row"], "38h")
        # a human acceptance with the setting: 38g names /accept-milestone (delta 9)
        human = {"schema_version": 1, "gates": {"acceptance": {"human": True, "requires_pr_approved": True}}}
        write_policy_file(ar.repo, human)
        ar.query([gpt.pr_record(ar.anchor)])
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"], result["policy"]["mode"]),
                         ("38g", "external_gate", "human"))
        self.assertIn("/accept-milestone", result["reason"]["remedy"])
        self.assertNotIn("/satisfy-gate", result["reason"]["remedy"].split("(")[0])
        # no stored fact, or one older than the report: pending the query, so row 39
        ar2 = self.repo()
        write_policy_file(ar2.repo, human)
        self.assertEqual(self.decide(ar2)["row"], "39")

    def test_a_human_acceptance_without_the_setting_is_row_39_unchanged(self):
        ar = self.repo()
        write_policy_file(ar.repo, gpt.ACCEPTANCE_HUMAN)
        ar.flow()
        ar.query([gpt.pr_record(ar.anchor)])
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"]), ("39", "human_gate"))
        self.assertNotIn("policy", result)

    def test_a_completed_item_reads_complete_until_a_key_or_trigger_holds(self):
        ar = self.repo()
        complete_item(ar)
        result = self.decide(ar)
        self.assertEqual((result["row"], result["disposition"]), ("40", "complete"))
        ar.query([gpt.pr_record(ar.anchor, checks="failure")])
        result = self.decide(ar)
        self.assertEqual((result["row"], result["action"]["id"]), ("38d", "pr.apply_review"))
        complete_item(ar)
        ar.query([gpt.pr_record(ar.anchor)])
        self.assertEqual(self.decide(ar)["row"], "40")

    def test_every_new_row_decision_is_schema_valid_and_names_a_validator_or_external(self):
        ar = self.repo()
        seen = set()
        ar.flow()
        for records in ("[]", [gpt.pr_record(ar.anchor)], [gpt.pr_record(ar.anchor, checks="pending")],
                        [gpt.pr_record(ar.anchor, checks="failure")]):
            ar.query(records)
            seen.add(self.decide(ar)["row"])
        self.assertLessEqual({"38f", "38h", "38d"}, seen)


class TestTriggerAndRemedyRuns(unittest.TestCase):
    """The trigger is cleared by any successful query (`LPR-R29-001`), `gh`
    unavailable (`LPR-R12-003`), and each `14b`/`28b` remedy run against a real
    repository (`LPR-R17-001`, `LPR-R18-001`)."""

    def reopen_repo(self, phase):
        ev = gpt.ReopenRepo()
        ev.__enter__()
        self.addCleanup(ev.__exit__, None, None, None)
        ev.flow()
        ev.query([gpt.pr_record(ev.anchor, number=7)])
        ev.report([gpt.pr_record(ev.anchor, number=9)])
        if phase == "MILESTONE_COMPLETE":
            complete_item(ev)
        else:
            commit_checklist_evidence(ev.repo, 1)
        return ev

    def decide(self, ev):
        gpt.g.clear_caches()
        return next_action(ev.repo, "--work-item", WI)

    def test_any_successful_query_clears_the_trigger_at_both_phases(self):
        for phase in ("AWAITING_FUNCTIONAL_REVIEW", "MILESTONE_COMPLETE"):
            for label, records in (("state none", []), ("the same PR", None)):
                with self.subTest(phase=phase, answer=label):
                    ev = self.reopen_repo(phase)
                    self.assertEqual(self.decide(ev)["row"], "38d")
                    seq = ev.evidence()["pr_keys"]["ingest_seq"]
                    result = ev.begin(records if records is not None else [gpt.pr_record(ev.anchor, number=7)])
                    self.assertEqual((result["result"], result["reopened"]), ("pr_fact_refreshed", False))
                    self.assertGreater(ev.evidence()["pr_keys"]["ingest_seq"], seq)
                    result = self.decide(ev)
                    self.assertNotEqual(result["row"], "38d")
                    if phase == "MILESTONE_COMPLETE":
                        self.assertEqual(result["row"], "40")
                    else:  # a stored `state: none` fact awaits a pull request (38f); the same PR is satisfiable
                        self.assertEqual(result["row"], "38f" if records == [] else "38h")
                    if phase == "MILESTONE_COMPLETE":
                        self.assertEqual(result["disposition"], "complete")

    def test_gh_unavailable_stores_nothing_and_the_next_decision_is_38h_again(self):
        import workflow_forge

        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
        commit_checklist_evidence(ar.repo, 1)
        ar.flow()
        before = ar.state()
        self.assertEqual(next_action(ar.repo)["row"], "38h")

        def unavailable(root):
            raise workflow_forge.ForgeUnavailableError("gh is not installed")

        with self.assertRaises(workflow_forge.ForgeUnavailableError):
            ws.satisfy_acceptance_gate(ar.root, WI, now="t-sat", resolve=unavailable)
        self.assertEqual(ar.state(), before, "nothing is stored")
        self.assertEqual(next_action(ar.repo)["row"], "38h", "the stated retry contract")
        decision = next_action(ar.repo)
        result, code = reconcile(ar.repo, decision)
        self.assertEqual((code, result["result"]["class"]), (0, "no_progress"))

    def test_the_toggle_remedy_of_14b_reaches_the_human_gate_with_the_bundle_intact(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            TestPlanAndTechnicalGateRows.unaudited(self, repo, "plan")
            self.assertEqual(next_action(repo)["row"], "14b")
            write_policy_file(repo, gpt.PLAN_HUMAN)
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("15", "plan.approve"))
            self.assertEqual(ws.plan_approval_gate_status(repo.root, h.read_state(repo), WI)["reachable"], True)

    def test_an_adoption_at_14b_stales_the_bundle_and_is_not_a_remedy(self):
        """`LPR-R18-001`: the adoption commit moves HEAD past the open bundle's
        `generation_head`, so the wrapper reports `bundle_generation_mismatch`
        and the item falls to row 16 with that cause's remedy."""
        for stage, row, fallen in (("plan", "14b", "16"), ("implementation", "28b", "30")):
            with self.subTest(stage=stage), h.ScratchRepo() as repo:
                at_approval(repo, stage)
                TestPlanAndTechnicalGateRows.unaudited(self, repo, stage)
                self.assertEqual(next_action(repo)["row"], row)
                policy = {"schema_version": 1, "human_approval": True}
                commit_policy_file(repo, policy)
                ws.adopt_gate_policy(repo.root, confirmation=gpt.confirmation_for(policy), now="2026-10-03T12:00:00Z")
                gpt.g.clear_caches()
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]), (fallen, "bundle_generation_mismatch"))
                self.assertNotIn("policy", result)

    def test_the_withdrawal_remedy_of_14b_reaches_plan_satisfy_through_both_stages_again(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            TestPlanAndTechnicalGateRows.unaudited(self, repo, "plan")
            self.assertEqual(next_action(repo)["row"], "14b")
            mutate(repo, ws.withdraw_plan_review, "t-withdraw")
            self.assertEqual(h.read_state(repo)["work_items"][WI]["phase"], "REVISING_PLAN")
            self.assertEqual(next_action(repo)["row"], "9", "the author edits and regenerates")
            plan = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan.write_text(plan.read_text() + "\nrevised after the withdrawal\n")
            h.git(repo, "add", "--", "docs/ai-workflow/WORKFLOW_V2_PLAN.md")
            h.git(repo, "commit", "-q", "-m", "revise the plan")
            P, B = h.publish_and_bind_plan_bundle(repo)
            gpt.record_local(repo, "plan", P, B, model=gpt.CLAUDE)
            gpt.ingest(repo, "plan", gpt.manual_text("plan", P, B, base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("14a", "plan.satisfy"))

    def test_a_pending_floor_is_evaluated_virtually_and_next_action_writes_nothing(self):
        """`LPR-R18-001`: a tightening in the file that is not yet in the floor is
        read by `next-action` without a commit, so the open bundle still
        verifies; the floor commit comes only after a satisfying commit, and
        passes its validator."""
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            write_policy_file(repo, {"schema_version": 1, "gates": {"acceptance": {"required_flows": ["e2e"]}}})
            head, state_bytes = repo.head(), (repo.root / ws.DEFAULT_STATE_PATH).read_bytes()
            result = next_action(repo)
            self.assertEqual(result["row"], "14a")
            self.assertEqual(result["policy"]["source"], "file_tightened")
            self.assertEqual((repo.head(), (repo.root / ws.DEFAULT_STATE_PATH).read_bytes()), (head, state_bytes))
            self.assertTrue(ws.plan_approval_gate_status(repo.root, h.read_state(repo), WI)["reachable"])
            sha = ws.commit_gate_policy_floor(repo.root, now="2026-10-03T12:00:00Z")
            self.assertIsNotNone(sha, "the floor commit is the one that moves HEAD")
            ws.validate_gate_policy_floor_commit(repo.root, sha)


class TestNewActionEdgesAndReconcile(unittest.TestCase):
    """The six edges of `plan.satisfy`, `implementation.satisfy` and
    `acceptance.satisfy`, `pr.apply_review`'s, and `reconcile` of a `validation`
    decision (`LPR-R12-004`, `LPR-R13-002`, `LPR-R14-001`, `LPR-R25-001`)."""

    def test_the_forward_and_unchanged_edges(self):
        edge = wp.edge_is_legal
        for version in ("2.1", "2.2"):
            self.assertTrue(edge("plan.satisfy", "AWAITING_PLAN_APPROVAL", "IMPLEMENTING", version))
            self.assertTrue(edge("plan.satisfy", "AWAITING_PLAN_APPROVAL", "AWAITING_PLAN_APPROVAL", version))
        self.assertFalse(edge("plan.satisfy", "AWAITING_PLAN_APPROVAL", "IMPLEMENTING", "1"))
        self.assertTrue(edge("implementation.satisfy", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                             "AWAITING_FUNCTIONAL_REVIEW", "2.2"))
        self.assertFalse(edge("implementation.satisfy", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                              "AWAITING_FUNCTIONAL_REVIEW", "2.1"))
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            self.assertTrue(edge("acceptance.satisfy", "AWAITING_FUNCTIONAL_REVIEW", "MILESTONE_COMPLETE", version))
            self.assertTrue(edge("acceptance.satisfy", "AWAITING_FUNCTIONAL_REVIEW", "AWAITING_FUNCTIONAL_REVIEW",
                                 version))
            self.assertTrue(edge("pr.apply_review", "MILESTONE_COMPLETE", "AWAITING_FUNCTIONAL_REVIEW", version))
            self.assertTrue(edge("pr.apply_review", "MILESTONE_COMPLETE", "MILESTONE_COMPLETE", version))
            self.assertTrue(edge("pr.apply_review", "AWAITING_FUNCTIONAL_REVIEW", "AWAITING_FUNCTIONAL_REVIEW",
                                 version))
            self.assertTrue(edge("pr.apply_review", "AWAITING_FUNCTIONAL_REVIEW",
                                 ws.bundle_generation_target_phase("post-fix", version), version))
        for action in ("plan.satisfy", "implementation.satisfy", "acceptance.satisfy", "pr.apply_review"):
            self.assertEqual((wp.EDGES[action]["proof"], wp.EDGES[action]["allowed_results"]),
                             (None, ["progress", "gate_reached", "no_progress"]), action)

    def test_only_the_validation_and_automatic_rows_have_edges(self):
        self.assertEqual(set(NEW_ACTION_IDS) - set(wp.EDGES), {"functional.evidence.external", "pr.review.external"})
        for row in wp.CATALOGUE:
            if row.disposition in ("automatic", "validation"):
                self.assertIn(row.action_id, wp.EDGES, row.row_id)
        validation_rows = {row.row_id for row in wp.CATALOGUE if row.disposition == "validation"}
        self.assertEqual(validation_rows, {"14a", "28a", "38h"})

    def reconcile_decision(self, repo, decision):
        body, code = reconcile(repo, decision)
        self.assertEqual(code, 0, body)
        return body["result"]

    def test_a_validation_decision_reconciles_and_a_refusal_is_the_unchanged_edge(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            decision = next_action(repo)
            self.assertEqual(decision["row"], "14a")
            # the act has not run: the same phase, the same decision -> no_progress through the unchanged edge
            result = self.reconcile_decision(repo, decision)
            self.assertEqual((result["class"], result["invalid_reasons"]), ("no_progress", []))
            self.assertEqual(result["next"]["row"], "14a")

    def test_a_refusal_after_the_toggle_turned_human_is_gate_reached(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            decision = next_action(repo)
            write_policy_file(repo, gpt.PLAN_HUMAN)
            result = self.reconcile_decision(repo, decision)
            self.assertEqual((result["class"], result["next"]["row"], result["invalid_reasons"]),
                             ("gate_reached", "15", []))

    def test_an_acceptance_satisfy_refusal_classes_follow_the_existing_classifier(self):
        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
        commit_checklist_evidence(ar.repo, 1)
        ar.flow()
        decision = next_action(ar.repo)
        self.assertEqual((decision["row"], decision["action"]["id"]), ("38h", "acceptance.satisfy"))
        cases = [  # (the state the refusal leaves, the row it lands on, the class)
            ("pending checks stored", lambda: ar.query([gpt.pr_record(ar.anchor, checks="pending")]), "38f",
             "gate_reached"),
            ("the toggle turned human", lambda: write_policy_file(ar.repo, gpt.ACCEPTANCE_HUMAN), "39",
             "gate_reached"),
            ("a red fact stored", lambda: (write_policy_file(ar.repo, {"schema_version": 1}),
                                           ar.query([gpt.pr_record(ar.anchor, checks="failure")])), "38d",
             "no_progress"),
        ]
        for label, make, row, expected in cases:
            with self.subTest(label):
                make()
                gpt.g.clear_caches()
                result = self.reconcile_decision(ar.repo, decision)
                self.assertEqual((result["next"]["row"], result["class"], result["invalid_reasons"]),
                                 (row, expected, []))

    def test_forge_unavailable_stays_38h_and_is_no_progress(self):
        """`LPR-R12-003`: the act refuses and stores nothing; the next decision is `38h` again."""
        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
        commit_checklist_evidence(ar.repo, 1)
        ar.flow()
        decision = next_action(ar.repo)
        before = h.read_state(ar.repo)
        result = self.reconcile_decision(ar.repo, decision)
        self.assertEqual((result["class"], result["next"]["row"]), ("no_progress", "38h"))
        self.assertEqual(h.read_state(ar.repo), before, "reconcile and next-action never write")

    def test_pr_apply_review_at_a_completed_item_reconciles_through_the_unchanged_edge(self):
        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        complete_item(ar)
        ar.query([gpt.pr_record(ar.anchor, checks="failure")])
        decision = next_action(ar.repo, "--work-item", WI)
        self.assertEqual((decision["row"], decision["action"]["id"]), ("38d", "pr.apply_review"))
        # a refusal (merged / forge unavailable ...) leaves the completed item as it was
        result = self.reconcile_decision(ar.repo, decision)
        self.assertEqual((result["from"]["phase"], result["to"]["phase"], result["class"], result["invalid_reasons"]),
                         ("MILESTONE_COMPLETE", "MILESTONE_COMPLETE", "no_progress", []))
        # the reopen: MILESTONE_COMPLETE -> AWAITING_FUNCTIONAL_REVIEW is progress
        ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
        result = self.reconcile_decision(ar.repo, decision)
        self.assertEqual((result["to"]["phase"], result["class"], result["invalid_reasons"]),
                         ("AWAITING_FUNCTIONAL_REVIEW", "progress", []))

    def test_a_validation_decision_must_name_its_own_row_and_disposition(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            decision = next_action(repo)
            forged = copy.deepcopy(decision)
            forged["disposition"] = "automatic"
            body, code = reconcile(repo, forged)
            self.assertEqual((code, body["error"]["code"]), (wp.EXIT_INVALID_REQUEST, "invalid_request"))
            forged = copy.deepcopy(decision)
            forged["row"] = "14b"
            body, code = reconcile(repo, forged)
            self.assertEqual((code, body["error"]["code"]), (wp.EXIT_INVALID_REQUEST, "invalid_request"))
            human = copy.deepcopy(decision)
            human["disposition"] = "human_gate"
            body, code = reconcile(repo, human)
            self.assertEqual((code, body["error"]["code"]), (wp.EXIT_INVALID_REQUEST, "invalid_request"))
            self.assertIn("automatic or validation", body["error"]["message"])


class TestUnawareConsumer(unittest.TestCase):
    """INV-8 (`LPR-R2-008`): a `1.0` consumer fails closed. Obligation 3 maps
    an unknown action id to `blocked`; obligation 5 runs an `automatic` action
    only, so a `validation` decision is never run and the item stalls."""

    KNOWN_1_0_ACTIONS = frozenset(set(wp.ACTION_IDS) - set(NEW_ACTION_IDS) - set(NEW_1_2_ACTION_IDS))

    @staticmethod
    def consumer_1_0(decision: dict) -> str:
        action = decision["action"]
        if action is not None and action["id"] not in TestUnawareConsumer.KNOWN_1_0_ACTIONS:
            return "blocked"  # obligation 3
        if decision["disposition"] == "automatic":
            return "run"  # obligation 5
        return "stalled"

    def test_each_new_action_id_is_blocked_and_each_validation_decision_stalls(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            decision = next_action(repo)
            self.assertEqual(decision["disposition"], "validation")
            self.assertEqual(self.consumer_1_0(decision), "blocked")
        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
        commit_checklist_evidence(ar.repo, 1)
        for build, row in ((lambda: None, "38e"), (lambda: ar.flow(), "38h"),
                           (lambda: ar.query([gpt.pr_record(ar.anchor, checks="failure")]), "38d")):
            build()
            decision = next_action(ar.repo)
            self.assertEqual(decision["row"], row)
            self.assertEqual(self.consumer_1_0(decision), "blocked", row)
        self.assertEqual(self.consumer_1_0({"action": None, "disposition": "validation"}), "stalled")


class TestUnaware1_1Consumer(unittest.TestCase):
    """INV-8 (workflow-2.9.0): a `1.1` consumer fails closed on `legacy.retire`.
    Obligation 3 maps an unknown action id to `blocked`; the alternative is
    never an action it runs, and row 3 is `blocked` for it as before."""

    KNOWN_1_1_ACTIONS = frozenset(wp.ACTION_IDS) - frozenset(NEW_1_2_ACTION_IDS)

    @staticmethod
    def consumer_1_1(decision: dict) -> str:
        action = decision["action"]
        if action is not None and action["id"] not in TestUnaware1_1Consumer.KNOWN_1_1_ACTIONS:
            return "blocked"  # obligation 3
        if decision["disposition"] == "automatic":
            return "run"  # obligation 5
        return "stalled"

    def test_a_legacy_ready_item_stalls_and_the_retirement_is_unknown_to_it(self):
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            with self.subTest(version=version), h.ScratchRepo() as repo:
                write_state(repo, h.base_state(wi=minimal_item("LEGACY_READY", version, base_commit=repo.base)))
                decision = next_action(repo, "--work-item", WI)
                self.assertEqual(self.consumer_1_1(decision), "stalled")
                for alternative in decision["alternatives"]:
                    self.assertNotIn(alternative["id"], self.KNOWN_1_1_ACTIONS)
                    self.assertEqual(self.consumer_1_1({"action": alternative, "disposition": "blocked"}), "blocked")
        self.assertEqual(self.consumer_1_1({"action": {"id": "legacy.retire"}, "disposition": "automatic"}), "blocked")

    def test_an_outstanding_checkpoint_stalls_and_the_resume_is_unknown_to_it(self):
        for version in ("2.1", "2.2"):
            with self.subTest(version=version), h.ScratchRepo() as repo:
                functional_item(repo, version, complete=False)
                decision = next_action(repo)
                self.assertEqual(decision["row"], "38c")
                self.assertEqual(self.consumer_1_1(decision), "stalled")
                resume = [a for a in decision["alternatives"] if a["id"] == "implementation.resume"]
                self.assertEqual(len(resume), 1)
                self.assertNotIn("implementation.resume", self.KNOWN_1_1_ACTIONS)
                self.assertEqual(self.consumer_1_1({"action": resume[0], "disposition": "blocked"}), "blocked")
        self.assertEqual(
            self.consumer_1_1({"action": {"id": "implementation.resume"}, "disposition": "automatic"}), "blocked")

    def test_the_new_envelope_fields_are_additive(self):
        """A `1.0` consumer ignores unknown response fields (obligation 2):
        the `policy` object is the only field a decision gains."""
        base = set(SCHEMA["$defs"]["decision"]["properties"]) - {"policy"}
        self.assertEqual(base, {"row", "basis", "snapshot", "disposition", "action", "satisfied_by", "alternatives",
                                "reason"})
        self.assertNotIn("policy", SCHEMA["$defs"]["decision"]["required"])


class TestEvidenceResultKinds(unittest.TestCase):
    """`record-external-result` for `functional_evidence` and `pr_review_result`
    (`OD-W2-9`): accepted under every policy, delegated to the CP3 library."""

    def repo(self):
        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        return ar

    def test_functional_evidence_is_recorded_under_every_policy(self):
        for label, policy in (("default", None), ("all human", h.ALL_HUMAN_GATE_POLICY),
                              ("acceptance human", gpt.ACCEPTANCE_HUMAN)):
            with self.subTest(policy=label):
                ar = self.repo()
                if policy is not None:
                    write_policy_file(ar.repo, policy)
                body = recorded(self, ar.repo, "functional_evidence", json.dumps(gpt.functional_record(ar.anchor)))
                self.assertEqual((body["stage"], body["flow_id"]), ("functional", "migration-suite"))
                self.assertEqual(body["identity"], ar.evidence()["functional"]["migration-suite"]["identity"])
                self.assertEqual(body["basis"]["work_item_id"], WI)

    def test_a_malformed_or_unknown_head_record_is_refused_and_writes_nothing(self):
        ar = self.repo()
        bad = gpt.functional_record(ar.anchor)
        del bad["summary"]
        for text, native in ((json.dumps(bad), "EvidenceRefusedError"),
                             (json.dumps(gpt.functional_record("e" * 40)), "EvidenceRefusedError")):
            error = refusal(self, ar.repo, "functional_evidence", text, "refused", native)
            self.assertRegex(error["message"], r"evidence_(malformed|head_unknown)")
        refusal(self, ar.repo, "functional_evidence", "not json", "invalid_request")
        refusal(self, ar.repo, "functional_evidence", "[1, 2]", "invalid_request")

    def test_pr_review_result_needs_its_forge_block(self):
        ar = self.repo()
        error = refusal(self, ar.repo, "pr_review_result", json.dumps({"run_ref": "r"}), "refused",
                        "ForgeProvenanceRequiredError")
        self.assertIn("forge provenance", error["message"])
        payload = gpt.reported_payload([gpt.pr_record(ar.anchor)], ar.anchor, repository="o/other")
        error = refusal(self, ar.repo, "pr_review_result", json.dumps(payload), "refused", "ForgeRepositoryMismatchError")
        payload = gpt.reported_payload([gpt.pr_record(ar.anchor)], ar.anchor)
        payload["forge"]["raw_sha256"] = "0" * 64
        refusal(self, ar.repo, "pr_review_result", json.dumps(payload), "refused", "ForgeDigestMismatchError")

    def test_a_reported_pr_fact_is_stored_tighten_only(self):
        ar = self.repo()
        payload = gpt.reported_payload([gpt.pr_record(ar.anchor)], ar.anchor)
        body = recorded(self, ar.repo, "pr_review_result", json.dumps(payload))
        self.assertEqual((body["stage"], body["slot"]), ("pr_review", "pr_reported"))
        evidence = ar.evidence()
        self.assertEqual(evidence["pr_reported"]["fact_id"], body["fact_id"])
        self.assertIsNone(evidence["pr"], "a reported fact never fills the slot a decision reads")
        self.assertEqual(evidence["pr_reported"]["provenance"]["source"], "orchestrator_forge")

    def test_run_ref_flag_is_carried_into_a_reported_fact(self):
        ar = self.repo()
        payload = gpt.reported_payload([gpt.pr_record(ar.anchor)], ar.anchor, run_ref=None)
        path = ar.root.parent / f"{ar.root.name}-pr.json"
        path.write_text(json.dumps(payload))
        body, code = call("--repo-root", str(ar.root), "record-external-result", "--work-item", WI, "--kind",
                          "pr_review_result", "--input", str(path), "--run-ref", "run-42")
        self.assertEqual(code, 0, body)
        self.assertEqual(ar.evidence()["pr_reported"]["provenance"]["run_ref"], "run-42")

    def test_the_input_schemas_describe_the_accepted_shapes(self):
        ar = self.repo()
        functional = gpt.functional_record(ar.anchor)
        self.assertEqual(schema_errors(functional, SCHEMA["$defs"]["inputs"]["properties"]["functional_evidence"]), [])
        forge = gpt.reported_payload([gpt.pr_record(ar.anchor)], ar.anchor)
        self.assertEqual(schema_errors(forge, SCHEMA["$defs"]["inputs"]["properties"]["pr_review_result"]), [])
        self.assertTrue(schema_errors({"run_ref": "r"}, SCHEMA["$defs"]["inputs"]["properties"]["pr_review_result"]))

    def test_the_new_kinds_are_not_ingested_through_the_verdict_ingest(self):
        with h.ScratchRepo() as repo, mock.patch.object(ws, "ingest_manual_review_verdict",
                                                        side_effect=AssertionError("verdict ingest")):
            write_state(repo, h.base_state(wi=minimal_item("IMPLEMENTING", "2.2")))
            body, code = record_external(repo, "functional_evidence", "not json")
            self.assertEqual(body["error"]["code"], "invalid_request")


class TestProtocolSchemaAndDescribe(unittest.TestCase):
    def test_every_new_action_and_role_is_in_the_schema(self):
        ids = SCHEMA["$defs"]["action"]["properties"]["id"]["enum"]
        self.assertEqual(sorted(ids), sorted(wp.ACTION_IDS))
        self.assertEqual(ids, SCHEMA["$defs"]["nullable_action"]["properties"]["id"]["enum"])
        self.assertEqual(sorted(SCHEMA["$defs"]["worker"]["properties"]["role"]["enum"]), sorted(wp.WORKER_ROLES))
        self.assertEqual(sorted(x for x in SCHEMA["$defs"]["decision"]["properties"]["satisfied_by"]["enum"] if x),
                         ["functional_evidence", "implementation_review_verdict", "plan_review_verdict",
                          "pr_review_result"])
        self.assertEqual(SCHEMA["$defs"]["results"]["properties"]["record-external-result"]["properties"]["stage"]
                         ["enum"], ["functional", "implementation", "plan", "pr_review"])

    def test_the_policy_object_is_schema_checked(self):
        policy = {"source": "default", "digest": "a" * 64, "gate": "plan_approval", "mode": "automatic"}
        self.assertEqual(schema_errors(policy, SCHEMA["$defs"]["policy"]), [])
        policy["gate_lowering"] = {"sha256": "b" * 64, "adopted_at": "t", "lowered": ["plan_approval.require"]}
        self.assertEqual(schema_errors(policy, SCHEMA["$defs"]["policy"]), [])
        self.assertTrue(schema_errors({**policy, "mode": "sometimes"}, SCHEMA["$defs"]["policy"]))
        self.assertTrue(schema_errors({**policy, "extra": 1}, SCHEMA["$defs"]["policy"]))

    def test_describe_lists_the_new_capabilities_and_1_1(self):
        body, code = call("describe")
        self.assertEqual(code, wp.EXIT_OK)
        result = body["result"]
        self.assertEqual((result["protocol_version"], result["workflow_release"]), ("1.2", "2.9.0"))
        caps = result["capabilities"]
        self.assertEqual(sorted(set(NEW_ACTION_IDS + NEW_1_2_ACTION_IDS) - set(caps["action_ids"])), [])
        self.assertEqual(caps["reserved_result_kinds"], [])
        self.assertEqual(caps["external_result_kinds"],
                         ["functional_evidence", "implementation_review_verdict", "plan_review_verdict",
                          "pr_review_result"])
        self.assertIn("validation", caps["dispositions"])

    def test_the_release_constant_and_the_protocol_version(self):
        self.assertEqual((wp.WORKFLOW_RELEASE, wp.PROTOCOL_VERSION, wp.PROTOCOL_MAJOR), ("2.9.0", "1.2", 1))
        self.assertIn("validator", wp.WORKER_ROLES)
        self.assertEqual(wp.ACTIONS["acceptance.satisfy"]["role"], "validator")
        self.assertEqual(wp.ACTIONS["pr.apply_review"]["role"], "applier")
        for action in ("functional.evidence.external", "pr.review.external"):
            self.assertEqual((wp.ACTIONS[action]["command"], wp.ACTIONS[action]["role"]), (None, "external"))

    def test_a_gate_policy_evaluation_error_is_refused_not_internal(self):
        with h.ScratchRepo() as repo:
            at_approval(repo, "plan")
            with mock.patch.object(wgp, "evaluate_gate", side_effect=wgp.GatePolicyError("patched")):
                result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["reason"]["code"]),
                             ("14a", "blocked", "condition_refused"))
            self.assertEqual(result["reason"]["native"]["exception"], "GatePolicyError")

    def test_a_forge_exception_is_a_workflow_exception_and_refused(self):
        import workflow_forge

        self.assertTrue(wp.is_workflow_exception(workflow_forge.ForgeUnavailableError("gh")))
        self.assertEqual(wp.code_for_workflow_exception(workflow_forge.ForgeUndecidableError("x")), "refused")



# ---------------------------------------------------------------------------
# workflow-2.9.0 CP3 (`D-INV1-Proof`): INV-1 re-proved against the published
# 2.8.0 modules. Each protocol minor version anchors its proof at the
# immediately preceding release; the `V270` tests above stay as the 1.1 history.
# ---------------------------------------------------------------------------

V280_TAG = "v2.8.0"
#: The complete dependency closure of the protocol (revision 5, `O1`).
V280_MODULES = ("workflow_fingerprint.py", "workflow_forge.py", "workflow_gate_policy.py", "workflow_state.py",
                "workflow_protocol.py")


class V280(V270):
    """The published 2.8.0 modules, loaded exactly as `V270` loads 2.7.0."""

    directory: Path | None = None
    available = False
    reason = f"the tag {V280_TAG} is not in this checkout"

    @classmethod
    def load(cls) -> None:
        if cls.directory is not None or cls.available:
            return
        try:
            directory = Path(tempfile.mkdtemp(prefix="workflow-v280-"))
            for name in V280_MODULES:
                blob = subprocess.run(["git", "show", f"{V280_TAG}:payload/scripts/{name}"], cwd=REPO_ROOT,
                                      capture_output=True, check=True).stdout
                (directory / name).write_bytes(blob)
        except (OSError, subprocess.CalledProcessError):
            return
        cls.directory, cls.available = directory, True


#: The enumerated exemptions (D-INV1-Proof): row id -> the fields of the
#: decision that may differ. A cell on no row below must match byte for byte.
#: Only `reason.text`/`reason.remedy` and the listed lists are ever exempt: the
#: row, the disposition, the action and the reason code still must match.
EXEMPT_1_2 = {
    "3": ("alternatives",),
    "6a": ("remedy_commands",),
    "38b": (),
    "38c": ("remedy_commands", "refusing_commands", "alternatives"),
}


def _normalize_1_2(body: dict) -> dict:
    """The envelope minus the protocol version and release, and minus the
    exempt fields of the row it decided (`reason.text`/`reason.remedy` for any
    exempt row)."""
    body = without_release(body)
    result = body.get("result", {})
    row = result.get("row")
    reason_text = (result.get("reason") or {}).get("text") or ""
    # row 6a is exempt at IMPLEMENTING only (D-INV1-Proof); PLANNING and
    # AMENDING_PLAN keep their 2.8.0 text and remedy byte for byte
    if row == "6a" and not reason_text.startswith("IMPLEMENTING"):
        return body
    if row in EXEMPT_1_2:
        for key in EXEMPT_1_2[row]:
            result.pop(key, None)
        reason = result.get("reason")
        if isinstance(reason, dict):
            reason["text"] = reason["remedy"] = None
    return body


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestEquivalenceAgainstV280(unittest.TestCase):
    """INV-1 (workflow-2.9.0): `next-action`, `verify` and `describe` equal the
    published 2.8.0 module's, byte for byte, apart from the enumerated
    exemptions. The added scenarios make the exemptions non-vacuous: the exempt
    states are the only cells that change."""

    @classmethod
    def setUpClass(cls):
        V280.load()

    def setUp(self):
        if not V280.available:
            self.skipTest(V280.reason)

    def compare(self, repo_root: Path, *argv: str) -> tuple[dict, dict]:
        old, old_code = V280.run(repo_root, *argv)
        new, new_code = run_new(repo_root, *argv)
        self.assertEqual(old_code, new_code, (old, new))
        assert_valid(new)
        self.assertEqual((old["protocol"]["version"], new["protocol"]["version"]), ("1.1", "1.2"))
        return old, new

    def added_scenarios(self) -> list:
        def minimal(phase, version, **extra):
            def build(repo):
                write_state(repo, h.base_state(wi=minimal_item(phase, version, base_commit=repo.base, **extra)))
            return build

        # each case: (name, builder, the row it must decide); `next-action` selects the item
        # with `--work-item`, because `h.base_state` leaves the active pointer null
        cases = [(f"legacy-ready@{v}", minimal("LEGACY_READY", v), "3") for v in wp.SUPPORTED_GOVERNING_VERSIONS]
        cases.append(("implementing@1 no registry", minimal("IMPLEMENTING", "1", registry_path=None), "6a"))
        cases.append(("implementing@1 registry",
                      lambda repo: h.seed_bundle_item(repo, governing_workflow_version="1", phase="IMPLEMENTING",
                                                      registry_checkpoints=CHECKPOINT_C1), None))
        for phase in ("PLANNING", "AMENDING_PLAN"):
            cases.append((f"{phase}@1", minimal(phase, "1", registry_path=None), "6a"))
        return cases

    def test_next_action_is_equal_except_on_the_exempt_rows(self):
        added = [(name, build, row) for name, build, row in self.added_scenarios()]
        for name, build, expected_row in [*[(n, b, False) for n, b in equivalence_scenarios()], *added]:
            with self.subTest(scenario=name), h.ScratchRepo() as repo:
                build(repo)
                selected = ("--work-item", WI) if expected_row is not False else ()
                old, new = self.compare(repo.root, "next-action", *selected)
                if expected_row:
                    self.assertEqual((old["result"].get("row"), new["result"].get("row")),
                                     (expected_row, expected_row), name)
                self.assertEqual(json.dumps(_normalize_1_2(old), sort_keys=True),
                                 json.dumps(_normalize_1_2(new), sort_keys=True))
                row = new["result"].get("row")
                if row in EXEMPT_1_2:
                    for key in ("row", "disposition", "action"):
                        self.assertEqual(old["result"].get(key), new["result"].get(key), (name, key))
                    self.assertEqual(old["result"]["reason"]["code"], new["result"]["reason"]["code"])

    def test_row_6a_is_byte_identical_outside_implementing_and_differs_at_it(self):
        for phase in ("PLANNING", "AMENDING_PLAN", "IMPLEMENTING"):
            with self.subTest(phase=phase), h.ScratchRepo() as repo:
                write_state(repo, h.base_state(wi=minimal_item(phase, "1", base_commit=repo.base, registry_path=None)))
                old, new = self.compare(repo.root, "next-action", "--work-item", WI)
                self.assertEqual((old["result"]["row"], new["result"]["row"]), ("6a", "6a"))
                strip = lambda body: json.dumps(without_release(body), sort_keys=True)
                if phase == "IMPLEMENTING":
                    self.assertNotEqual(strip(old), strip(new))
                else:
                    self.assertEqual(strip(old), strip(new))

    def test_row_3_gains_only_the_user_only_alternative(self):
        for version in wp.SUPPORTED_GOVERNING_VERSIONS:
            with self.subTest(version=version), h.ScratchRepo() as repo:
                write_state(repo, h.base_state(wi=minimal_item("LEGACY_READY", version, base_commit=repo.base)))
                old, new = self.compare(repo.root, "next-action", "--work-item", WI)
                self.assertEqual(old["result"]["alternatives"], [])
                self.assertEqual([a["id"] for a in new["result"]["alternatives"]], ["legacy.retire"])
                self.assertEqual(new["result"]["disposition"], "blocked")

    def test_a_retired_item_is_never_automatic_while_2_8_0_reported_the_pull_request_action(self):
        for red in (False, True):
            with self.subTest(red=red), gpt.ReopenRepo() as ev:
                ev.set_state(phase="MILESTONE_COMPLETE", governing_workflow_version="1",
                             technical_approval=gpt._legacy_approval())
                if red:
                    ev.red()
                state = ev.state()
                state["active_work_item_id"] = None  # a terminal item is never the active one
                h.write_state(ev.repo, state)
                old, new = self.compare(ev.root, "next-action", "--work-item", gpt.WI)
                self.assertNotEqual(new["result"]["disposition"], "automatic")
                if red:
                    self.assertEqual(old["result"]["disposition"], "automatic", "2.8.0 offered pr.apply_review")
                if not red:
                    self.assertEqual(json.dumps(_normalize_1_2(old), sort_keys=True),
                                     json.dumps(_normalize_1_2(new), sort_keys=True))
                old_v, new_v = self.compare(ev.root, "verify")
                for body in (old_v, new_v):
                    for check in body["result"]["checks"]:
                        if check["id"] == "installation_release_matches":
                            check.pop("detail", None)
                self.assertEqual(json.dumps(without_release(old_v), sort_keys=True),
                                 json.dumps(without_release(new_v), sort_keys=True))

    def test_verify_is_byte_identical_in_every_cell(self):
        for name, build in [*equivalence_scenarios(), *[(n, b) for n, b, _ in self.added_scenarios()]]:
            with self.subTest(scenario=name), h.ScratchRepo() as repo:
                build(repo)
                old, new = self.compare(repo.root, "verify")
                strip = lambda body: json.dumps(without_release(body), sort_keys=True)
                # `installation_release_matches` names the release the scripts are, by design
                for body in (old, new):
                    for check in body["result"]["checks"]:
                        if check["id"] == "installation_release_matches":
                            check.pop("detail", None)
                self.assertEqual(strip(old), strip(new))

    def test_describe_differs_only_by_the_protocol_version_and_the_new_action_ids(self):
        with h.ScratchRepo() as repo:
            write_state(repo, empty_state())
            old, new = self.compare(repo.root, "describe")
        old_result, new_result = old["result"], new["result"]
        self.assertEqual((old_result["protocol_version"], new_result["protocol_version"]), ("1.1", "1.2"))
        self.assertEqual((old_result["workflow_release"], new_result["workflow_release"]), ("2.8.0", "2.9.0"))
        old_caps, new_caps = old_result["capabilities"], new_result["capabilities"]
        added = sorted(set(new_caps["action_ids"]) - set(old_caps["action_ids"]))
        self.assertEqual(added, sorted(NEW_1_2_ACTION_IDS))
        self.assertLessEqual(set(old_caps["action_ids"]), set(new_caps["action_ids"]))
        for key in set(old_caps) - {"action_ids"}:
            self.assertEqual(new_caps[key], old_caps[key], key)
        for key in set(old_result) - {"protocol_version", "workflow_release", "capabilities"}:
            self.assertEqual(new_result[key], old_result[key], key)


# ---------------------------------------------------------------------------
# workflow-2.8.0 CP7 (`D-GP-Compat`): the update simulation. A disposable
# repository holding in-flight 2.7.0 items is "updated" to 2.8.0 and the two
# configurations are compared against the immutable v2.7.0 modules, which
# produce the 2.7.0 side of every comparison.
# ---------------------------------------------------------------------------

PLAN_STAGE, IMPLEMENTATION_STAGE = "plan_stage", "implementation_stage"
AI_WORKFLOW_PREFIX = "docs/ai-workflow/"
TOGGLE_PATH = "docs/ai-workflow/GATE_POLICY.json"
INSTALLED_GUIDE = "docs/ai-workflow/GATE_POLICY.md"
TECHNICAL_HUMAN = {"schema_version": 1, "gates": {"technical_approval": {"human": True}}}
DECLARATIONS_PATH = f"docs/ai-workflow/registry/{WI}-artifacts.json"


@contextlib.contextmanager
def no_policy_file():
    """Fixtures built inside carry no `GATE_POLICY.json`: the 2.8.0 default
    (the harness writes the all-human file unless told otherwise)."""
    seed = h.seed_bundle_item

    def default_seed(*args, **kwargs):
        kwargs["gate_policy"] = None
        return seed(*args, **kwargs)

    with mock.patch.object(h, "seed_bundle_item", default_seed):
        yield


@contextlib.contextmanager
def legacy_declarations():
    """Declarations as an older hand-authored file has them: the
    `docs/ai-workflow/` prefix is not excluded at either stage."""
    original = ws.generate_artifacts_declarations

    def legacy(*args, **kwargs):
        declarations = original(*args, **kwargs)
        for stage in (PLAN_STAGE, IMPLEMENTATION_STAGE):
            declarations[stage]["excluded_prefixes"].pop(AI_WORKFLOW_PREFIX)
        return declarations

    with mock.patch.object(ws, "generate_artifacts_declarations", legacy):
        yield


def edit_declarations(repo: h.ScratchRepo, edit) -> None:
    path = repo.root / DECLARATIONS_PATH
    declarations = json.loads(path.read_text())
    edit(declarations)
    path.write_text(json.dumps(declarations) + "\n")


def declare_the_prefix(repo: h.ScratchRepo) -> None:
    def edit(declarations):
        for stage in (PLAN_STAGE, IMPLEMENTATION_STAGE):
            declarations[stage]["excluded_prefixes"][AI_WORKFLOW_PREFIX] = "the gate policy file lives here"
    edit_declarations(repo, edit)


def declare_the_exact_path(repo: h.ScratchRepo) -> None:
    def edit(declarations):
        for stage in (PLAN_STAGE, IMPLEMENTATION_STAGE):
            declarations[stage]["excluded_paths"][TOGGLE_PATH] = "the gate policy file"
    edit_declarations(repo, edit)


def commit_paths(repo: h.ScratchRepo, subject: str, *paths: str) -> str:
    h.git(repo, "add", "--", *paths)
    h.git(repo, "commit", "-q", "-m", subject)
    return repo.head()


def plan_stage_content_id(repo: h.ScratchRepo) -> str:
    return fingerprint.compute_review_content_id_plan_stage_for_work_item(repo.root, WI)[0]


def tree_snapshot(repo: h.ScratchRepo) -> dict:
    """Every file outside `.git`, by content hash."""
    snapshot = {}
    for path in sorted(repo.root.rglob("*")):
        relative = path.relative_to(repo.root)
        if relative.parts[0] != ".git" and path.is_file():
            snapshot[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def changed_paths(before: dict, after: dict) -> set:
    """The paths an update changed, the managed ones (the installed scripts
    and the guide) removed: what is left must be empty."""
    return {path for path in set(before) | set(after) if before.get(path) != after.get(path)
            and path != INSTALLED_GUIDE and not path.startswith("scripts/")}


def changed_paths_including_managed(before: dict, after: dict) -> set:
    return {path for path in set(before) | set(after) if before.get(path) != after.get(path)}


def apply_update(repo: h.ScratchRepo) -> None:
    """The update, as far as a disposable repository can model it: the
    installed scripts are replaced by the release's (`install_workflow_scripts`,
    the harness's copy of what `workflow-manager update` installs) and the new
    managed guide appears. Command files are not copied: a declaration that
    protects `.claude/commands/` would see any release's update as a content
    change, which is not this release's behaviour."""
    h.install_workflow_scripts(repo)
    (repo.root / INSTALLED_GUIDE).write_text("# gate policy guide (installed with 2.8.0)\n")


#: The items the simulation holds, by `equivalence_scenarios()` name: every
#: persisted phase at which a human gate can stand, plus the neighbours.
SIMULATION_ITEMS = (
    "plan.local", "plan.manual", "plan.approval", "v1.plan", "implementing", "impl.local", "impl.manual",
    "impl.external", "v1.external", "applying", "functional", "no item", "functional@1", "functional@2.1",
    "AWAITING_PLAN_APPROVAL@2.1",
)

#: D-GP-Compat's default list for these items: the human gate's row, and the
#: row the default's decision is. Every other item keeps its row.
DEFAULT_DELTAS = {
    "plan.approval": ("15", "14b"), "AWAITING_PLAN_APPROVAL@2.1": ("15", "14b"),
    "impl.external": ("29", "28b"),
    "functional": ("39", "38i"), "functional@1": ("39", "38i"), "functional@2.1": ("39", "38i"),
}


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestUpdateSimulation27To28(unittest.TestCase):
    """`D-GP-Compat`, CP7: a repository at 2.7.0 with items in several phases
    is updated to 2.8.0 and compared in both configurations."""

    @classmethod
    def setUpClass(cls):
        V270.load()

    def setUp(self):
        if not V270.available:
            self.skipTest(V270.reason)

    def scenarios(self):
        wanted = dict(equivalence_scenarios())
        self.assertLessEqual(set(SIMULATION_ITEMS), set(wanted))
        return [(name, wanted[name]) for name in SIMULATION_ITEMS]

    # -- (a) no policy file ------------------------------------------------

    def test_with_no_policy_file_only_the_gate_rows_change(self):
        seen = set()
        for name, build in self.scenarios():
            with self.subTest(item=name), no_policy_file(), h.ScratchRepo() as repo:
                build(repo)
                self.assertFalse((repo.root / TOGGLE_PATH).exists())
                old, old_code = V270.run(repo.root, "next-action")
                before = tree_snapshot(repo)
                apply_update(repo)
                after = tree_snapshot(repo)
                self.assertEqual(changed_paths(before, after), set(),
                                 "no file other than the managed ones changes: the state, the config, the "
                                 "declarations and the bundles are byte-identical")
                self.assertIn(INSTALLED_GUIDE, changed_paths_including_managed(before, after))
                new, new_code = run_new(repo.root, "next-action")
                assert_valid(new)
                self.assertEqual(tree_snapshot(repo), after, "deciding writes nothing")
                self.assertEqual(old_code, new_code)
                old_result, new_result = old["result"], new["result"]
                if name in DEFAULT_DELTAS:
                    old_row, new_row = DEFAULT_DELTAS[name]
                    self.assertEqual((old_result["row"], old_result["disposition"]), (old_row, "human_gate"))
                    self.assertEqual((new_result["row"], new_result["disposition"], new_result["reason"]["code"]),
                                     (new_row, "blocked", "gate_evidence_unmet"))
                    self.assertEqual(new_result["policy"]["source"], "default")
                    self.assertEqual(new_result["policy"]["mode"], "automatic")
                    seen.add(new_row)
                else:
                    self.assertEqual(json.dumps(without_release(old), sort_keys=True),
                                     json.dumps(without_release(new), sort_keys=True))
        self.assertEqual(seen, {"14b", "28b", "38i"})

    def test_an_item_with_flows_to_report_waits_for_the_orchestrator_not_a_person(self):
        """The acceptance gate of a 2.7.0 item at the functional gate: the
        human row 39 becomes row 38e (the orchestrator reports the flows)."""
        ar = gpt.AcceptanceRepo()
        ar.__enter__()
        self.addCleanup(ar.__exit__, None, None, None)
        ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
        commit_checklist_evidence(ar.repo, 1)
        old, _ = V270.run(ar.root, "next-action", "--work-item", WI)
        before = tree_snapshot(ar.repo)
        apply_update(ar.repo)
        new = next_action(ar.repo, "--work-item", WI)
        self.assertEqual(changed_paths(before, tree_snapshot(ar.repo)), set())
        self.assertEqual((old["result"]["row"], new["row"], new["disposition"], new["satisfied_by"]),
                         ("39", "38e", "external_gate", "functional_evidence"))

    # -- (b) human_approval: true --------------------------------------------

    def test_with_human_approval_every_next_action_is_unchanged_apart_from_the_versions(self):
        for name, build in self.scenarios():
            with self.subTest(item=name), h.ScratchRepo() as repo:
                build(repo)
                if name != "no item":
                    self.assertEqual(json.loads((repo.root / TOGGLE_PATH).read_text()),
                                     {"schema_version": 1, "human_approval": True})
                old, old_code = V270.run(repo.root, "next-action")
                before = tree_snapshot(repo)
                apply_update(repo)
                self.assertEqual(changed_paths(before, tree_snapshot(repo)), set())
                new, new_code = run_new(repo.root, "next-action")
                assert_valid(new)
                self.assertEqual(old_code, new_code)
                self.assertEqual(json.dumps(without_release(old), sort_keys=True),
                                 json.dumps(without_release(new), sort_keys=True))
                self.assertNotIn("policy", new["result"])
                self.assertEqual((old["protocol"]["version"], new["protocol"]["version"]), ("1.0", "1.2"))
                self.assertEqual(new["workflow_release"], "2.9.0")

    # -- the downgrade posture ------------------------------------------------

    def test_2_7_0_reads_every_simulated_state_and_refuses_the_new_basis(self):
        for name, build in self.scenarios():
            with self.subTest(item=name), no_policy_file(), h.ScratchRepo() as repo:
                build(repo)
                body, code = V270.run(repo.root, "next-action")
                self.assertEqual(code, 0, body)
        with h.ScratchRepo() as repo:
            P, B = gpt.plan_at_manual(repo)
            gpt.ingest(repo, "plan", gpt.manual_text("plan", P, B, base=repo.base))
            self.assertEqual(V270.run(repo.root, "next-action")[0]["result"]["row"], "15",
                             "a state holding no new basis is read, audit keys in the ledger included")
            record = ws.build_policy_approval_record(repo.root, h.read_state(repo), WI, "plan", now="t-sat")
            ws.state_transaction(repo.root, lambda state: ws.apply_plan_approval(state, WI, record, "t-sat"))
            body, code = V270.run(repo.root, "next-action")
            self.assertEqual((code, body["error"]["code"]), (wp.exit_code_for("state_invalid"), "state_invalid"))
            self.assertEqual(body["error"]["native"]["exception"], "InvalidApprovalRecordError")
            self.assertIn("POLICY_SATISFIED", body["error"]["message"])
            checks = checks_by_id(V270.run(repo.root, "verify")[0])
            self.assertEqual(checks["state_valid"]["status"], "fail")
            self.assertEqual(run_new(repo.root, "next-action")[1], 0, "2.8.0 reads its own state")

    def test_the_posture_rests_on_the_basis_for_the_optional_fields_2_7_0_does_not_check(self):
        """Observed against D-GP-Compat's "Downgrade posture" sentence, which
        says 2.7.0 refuses *any* new field at `validate_state`: the published
        2.7.0 validator accepts an item's `gate_evidence`, `reopenings` and
        `acceptance_satisfaction` and the top-level `gate_policy_adoption` and
        `gate_policy_floor` (unknown optional keys), and refuses only the new
        basis. This pins the immutable 2.7.0's behaviour so the compatibility
        notes can state it exactly."""
        with gpt.AcceptanceRepo() as ar:
            ar.set_state(phase="AWAITING_FUNCTIONAL_REVIEW")
            ar.flow()
            self.assertIn("gate_evidence", ar.item())
            for label, edit in (
                    ("reopenings", lambda state: state["work_items"][WI].update(reopenings=[])),
                    ("acceptance_satisfaction", lambda state: state["work_items"][WI].update(
                        acceptance_satisfaction={"x": 1})),
                    ("gate_policy_adoption", lambda state: state.update(gate_policy_adoption={})),
                    ("gate_policy_floor", lambda state: state.update(gate_policy_floor={}))):
                with self.subTest(field=label):
                    state = ar.state()
                    edit(state)
                    h.write_state(ar.repo, state)
                    body, code = V270.run(ar.root, "verify")
                    self.assertEqual(checks_by_id(body)["state_valid"]["status"], "pass")


GATE_ROWS = frozenset({"14a", "14b", "15", "28a", "28b", "29"})


def readers_refuse(test: unittest.TestCase, repo: h.ScratchRepo, stage: str) -> str:
    """The readers of an open bundle all refuse. The error raised depends on
    the order the checks run, so each assertion accepts any of
    `UnclassifiedPathError`, `ReviewedContentDriftError`, an unverified bundle
    or `WorktreeOrHeadMismatchError` and pins none (CP7). Returns the name of
    what refused."""
    refusals = (fingerprint.UnclassifiedPathError, ws.ReviewedContentDriftError,
                fingerprint.WorktreeOrHeadMismatchError, ws.PlanReviewBundleUnverifiedError,
                ws.ImplementationReviewBundleUnverifiedError)
    gate = ws.plan_approval_gate_status if stage == "plan" else ws.technical_approval_gate_status
    try:
        status = gate(repo.root, h.read_state(repo), WI)
        named = f"unreachable ({status['cause']})"
        test.assertFalse(status["reachable"], status)
    except refusals as exc:
        named = type(exc).__name__
    body, code = call("--repo-root", str(repo.root), "next-action")
    if code == 0:
        test.assertNotIn(body["result"]["row"], GATE_ROWS, body["result"])
    return named


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestUpdateSimulationLegacyDeclaration(unittest.TestCase):
    """`LPR-R19-O1`, `LPR-R20-002`, `LPR-R21-001`: an in-flight item whose
    declaration predates the `docs/ai-workflow/` exclusion, through the real
    generation check, in both cases."""

    def toggled_plan_item(self, *, committed: bool) -> h.ScratchRepo:
        repo = h.ScratchRepo().__enter__()
        self.addCleanup(repo.__exit__, None, None, None)
        h.seed_bundle_item(repo, governing_workflow_version="2.2", phase="PLANNING")
        plan_stage_content_id(repo)  # classified today
        write_policy_file(repo, h.ALL_HUMAN_GATE_POLICY)  # the toggle's new file
        if committed:
            commit_paths(repo, "commit the toggle", TOGGLE_PATH)
        return repo

    # -- (i) before the stage's bundle is generated ----------------------------

    def test_the_toggle_is_unclassified_and_committing_it_does_not_help(self):
        with no_policy_file(), legacy_declarations():
            repo = self.toggled_plan_item(committed=False)
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                plan_stage_content_id(repo)
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                h.publish_and_bind_plan_bundle(repo)  # the real generation
            repo = self.toggled_plan_item(committed=True)
            self.assertIn(TOGGLE_PATH, h.git(repo, "diff", "--name-only", f"{repo.base}..HEAD").splitlines())
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                plan_stage_content_id(repo)  # the plan-stage classifier
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                current_I(repo)  # the implementation-stage classifier
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                h.publish_and_bind_plan_bundle(repo)

    def test_declaring_the_prefix_or_the_exact_path_clears_it_at_both_stages(self):
        for label, declare in (("the prefix", declare_the_prefix), ("the exact path", declare_the_exact_path)):
            for committed in (False, True):
                with self.subTest(declaring=label, committed=committed), no_policy_file(), legacy_declarations():
                    repo = self.toggled_plan_item(committed=committed)
                    declare(repo)
                    self.assertEqual(len(plan_stage_content_id(repo)), 64)
                    self.assertEqual(len(current_I(repo)), 64)
                    P, B = h.publish_and_bind_plan_bundle(repo)  # the generation now succeeds
                    self.assertEqual(len(P) + len(B), 128)

    def test_a_declaration_with_the_prefix_classifies_the_file_without_any_edit(self):
        """The 2.7.0 template's declaration: the toggle works before generation."""
        with no_policy_file(), h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="2.2", phase="PLANNING")
            write_policy_file(repo, h.ALL_HUMAN_GATE_POLICY)
            commit_paths(repo, "commit the toggle", TOGGLE_PATH)
            self.assertEqual(len(plan_stage_content_id(repo)), 64)
            self.assertEqual(len(current_I(repo)), 64)

    def test_the_guide_the_update_installs_is_itself_unclassified_for_a_legacy_declaration(self):
        """Observed, beyond the toggle's file: the new managed guide
        `docs/ai-workflow/GATE_POLICY.md` that the update installs is a new path
        under the same unexcluded prefix, so the plan-stage classifier of a
        legacy-declared item raises on it before any toggle, and the same
        declaration remedies clear it."""
        with no_policy_file(), legacy_declarations(), h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, governing_workflow_version="2.2", phase="PLANNING")
            plan_stage_content_id(repo)
            apply_update(repo)
            with self.assertRaises(fingerprint.UnclassifiedPathError) as caught:
                plan_stage_content_id(repo)
            self.assertIn(INSTALLED_GUIDE, str(caught.exception))
            declare_the_prefix(repo)
            self.assertEqual(len(plan_stage_content_id(repo)), 64)

    # -- (ii) with a generated bundle -------------------------------------------

    def test_at_awaiting_plan_approval_neither_the_prefix_nor_a_commit_helps(self):
        for label, prefix, committed in (("file only", False, False), ("prefix declared", True, False),
                                         ("file committed", False, True), ("both committed", True, True)):
            with self.subTest(case=label), no_policy_file(), legacy_declarations(), h.ScratchRepo() as repo:
                plan_item_at(repo, "AWAITING_PLAN_APPROVAL")
                self.assertEqual(next_action(repo)["row"], "14b", "the baseline: the gate stands")
                if prefix:
                    declare_the_prefix(repo)
                write_policy_file(repo, gpt.PLAN_HUMAN)
                if committed:
                    commit_paths(repo, "commit the toggle", TOGGLE_PATH, *([DECLARATIONS_PATH] if prefix else []))
                readers_refuse(self, repo, "plan")

    def test_at_the_external_implementation_review_neither_the_prefix_nor_a_commit_helps(self):
        for label, prefix, committed in (("prefix declared", True, False), ("file committed", False, True),
                                         ("both committed", True, True)):
            with self.subTest(case=label), no_policy_file(), legacy_declarations(), h.ScratchRepo() as repo:
                implementation_at_external_2_2(repo)
                self.assertEqual(next_action(repo)["row"], "28b", "the baseline: the gate stands")
                if prefix:
                    declare_the_prefix(repo)
                write_policy_file(repo, TECHNICAL_HUMAN)
                if committed:
                    commit_paths(repo, "commit the toggle", TOGGLE_PATH, *([DECLARATIONS_PATH] if prefix else []))
                readers_refuse(self, repo, "implementation")

    def test_the_withdrawal_route_at_the_plan_stage_reaches_the_gate(self):
        with no_policy_file(), legacy_declarations(), h.ScratchRepo() as repo:
            plan_item_at(repo, "AWAITING_PLAN_APPROVAL")
            declare_the_prefix(repo)  # before the regeneration
            write_policy_file(repo, gpt.PLAN_HUMAN)
            readers_refuse(self, repo, "plan")
            mutate(repo, ws.withdraw_plan_review, "t-withdraw")
            self.assertEqual(h.read_state(repo)["work_items"][WI]["phase"], "REVISING_PLAN")
            plan = repo.root / _PLAN_DOC
            plan.write_text(plan.read_text() + "\nrevised after the withdrawal\n")
            commit_paths(repo, "revise the plan, declare the prefix, add the toggle", _PLAN_DOC, DECLARATIONS_PATH,
                         TOGGLE_PATH)
            P, B = h.publish_and_bind_plan_bundle(repo)
            mutate(repo, ws.record_local_plan_review, verdict="APPROVE", bundle_id=B, review_content_id=P,
                   round=2, now="t-local-2")
            mutate(repo, ws.record_manual_plan_review, verdict="APPROVE", bundle_id=B, round=2, now="t-manual-2",
                   current_review_content_id=P, feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                   feedback_review_content_id=P)
            write_feedback(repo, verdict("APPROVE", rcid=P, bundle=B, base=repo.base,
                                         role="MANUAL_EXTERNAL_PLAN_REVIEW"))
            self.assertTrue(ws.plan_approval_gate_status(repo.root, h.read_state(repo), WI)["reachable"])
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("15", "plan.approve"))

    def test_the_withdrawal_route_at_the_implementation_stage_reaches_the_gate(self):
        with no_policy_file(), legacy_declarations(), h.ScratchRepo() as repo:
            implementation_at_external_2_2(repo)
            declare_the_prefix(repo)
            write_policy_file(repo, TECHNICAL_HUMAN)
            readers_refuse(self, repo, "implementation")
            commit_paths(repo, "declare the prefix, add the toggle", DECLARATIONS_PATH, TOGGLE_PATH)
            mutate(repo, ws.enter_applying_review_feedback, "t-withdraw")
            h.commit_state(repo, "enter APPLYING_REVIEW_FEEDBACK")
            repo.commit("fix", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
            I = h.generate_implementation_bundle(repo, stage="post-fix")
            B = current_bundle_id(repo)
            mutate(repo, ws.record_local_implementation_review, verdict="APPROVE", bundle_id=B,
                   review_content_id=I, round=2, now="t-local-2")
            mutate(repo, ws.record_manual_implementation_review, verdict="APPROVE", bundle_id=B, round=2,
                   now="t-manual-2", current_review_content_id=I, feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                   feedback_review_content_id=I)
            write_feedback(repo, verdict("APPROVE", rcid=I, bundle=B, base=repo.base,
                                         role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))
            self.assertTrue(ws.technical_approval_gate_status(repo.root, h.read_state(repo), WI)["reachable"])
            result = next_action(repo)
            self.assertEqual((result["row"], result["action"]["id"]), ("29", "implementation.approve"))


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestUpdateSimulationInFlightDefault(unittest.TestCase):
    """An in-flight 2.7.0 item updated under the default: its ledger lacks the
    audit keys and any `Reviewer model:` line, so it blocks at `14b`/`28b` on
    both requirements, and the routes the commands allow are run against the
    real generated bundle (`LPR-R16-003`, `LPR-R17-001`, `LPR-R18-001`)."""

    STAGES = {
        "plan": ("14b", "15", "plan.approve", "plan_item_at", "/approve-review plan", "/milestone-plan"),
        "implementation": ("28b", "29", "implementation.approve", "implementation_at_external_2_2",
                           "/approve-review implementation", "/apply-implementation-review"),
    }

    def build(self, stage: str, repo: h.ScratchRepo) -> None:
        if stage == "plan":
            plan_item_at(repo, "AWAITING_PLAN_APPROVAL")
        else:
            implementation_at_external_2_2(repo)

    def test_both_unmet_requirements_are_named_together_with_their_remedies(self):
        for stage, (row, _human, _action, _builder, command, withdraw) in self.STAGES.items():
            with self.subTest(stage=stage), no_policy_file(), h.ScratchRepo() as repo:
                self.build(stage, repo)
                ledger = ledger_stages(repo, stage)
                for entry in ledger.values():
                    if isinstance(entry, dict):
                        self.assertNotIn("verdict_sha256", entry)
                        self.assertNotIn("reviewer_model", entry)
                self.assertNotIn("Reviewer model:", ws.read_review_feedback(repo.root, WI))
                result = next_action(repo)
                self.assertEqual((result["row"], result["disposition"], result["action"]), (row, "blocked", None))
                self.assertEqual(result["reason"]["code"], "gate_evidence_unmet")
                for requirement in ("review_evidence_audited", "distinct_reviewer_models"):
                    self.assertIn(requirement, result["reason"]["text"])
                remedy = result["reason"]["remedy"]
                self.assertIn('"human_approval": true', remedy)
                self.assertIn(command, remedy)
                self.assertIn(withdraw, remedy)
                self.assertNotIn("/adopt-gate-policy", remedy)

    def test_the_human_toggle_then_the_approval_reaches_the_gate_immediately(self):
        for stage, (_row, human_row, action, _builder, _command, _withdraw) in self.STAGES.items():
            with self.subTest(stage=stage), no_policy_file(), h.ScratchRepo() as repo:
                self.build(stage, repo)
                write_policy_file(repo, h.ALL_HUMAN_GATE_POLICY)
                result = next_action(repo)
                self.assertEqual((result["row"], result["action"]["id"], result["disposition"]),
                                 (human_row, action, "human_gate"))
                self.assertNotIn("policy", result)
                run = _Lifecycle(self, repo, {})
                (run.user_approves_plan if stage == "plan" else run.user_approves_implementation)(result)
                phase = h.read_state(repo)["work_items"][WI]["phase"]
                self.assertEqual(phase, "IMPLEMENTING" if stage == "plan" else "AWAITING_FUNCTIONAL_REVIEW")

    def test_the_withdrawal_route_reaches_satisfy_after_both_stages_are_re_recorded(self):
        with no_policy_file(), h.ScratchRepo() as repo:
            self.build("plan", repo)
            mutate(repo, ws.withdraw_plan_review, "t-withdraw")
            plan = repo.root / _PLAN_DOC
            plan.write_text(plan.read_text() + "\nrevised after the withdrawal\n")
            commit_paths(repo, "revise the plan", _PLAN_DOC)
            P, B = h.publish_and_bind_plan_bundle(repo)
            gpt.record_local(repo, "plan", P, B, model=gpt.CLAUDE)
            gpt.ingest(repo, "plan", gpt.manual_text("plan", P, B, base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("14a", "validation", "plan.satisfy"))
            ledger = ledger_stages(repo, "plan")
            self.assertEqual(sorted(entry["reviewer_model"] for entry in ledger.values()
                                    if isinstance(entry, dict) and "reviewer_model" in entry),
                             sorted([gpt.CLAUDE, gpt.OPENAI]))
        with no_policy_file(), h.ScratchRepo() as repo:
            self.build("implementation", repo)
            mutate(repo, ws.enter_applying_review_feedback, "t-withdraw")
            h.commit_state(repo, "enter APPLYING_REVIEW_FEEDBACK")
            repo.commit("fix", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
            I = h.generate_implementation_bundle(repo, stage="post-fix")
            B = current_bundle_id(repo)
            gpt.record_local(repo, "implementation", I, B, model=gpt.CLAUDE)
            gpt.ingest(repo, "implementation", gpt.manual_text("implementation", I, B, base=repo.base))
            result = next_action(repo)
            self.assertEqual((result["row"], result["disposition"], result["action"]["id"]),
                             ("28a", "validation", "implementation.satisfy"))

    def test_the_adoption_is_not_a_route_it_stales_the_open_bundle(self):
        for stage, (row, _human, _action, _builder, _command, _withdraw) in self.STAGES.items():
            with self.subTest(stage=stage), no_policy_file(), h.ScratchRepo() as repo:
                self.build(stage, repo)
                self.assertEqual(next_action(repo)["row"], row)
                commit_policy_file(repo, gpt.NO_REQUIRE)  # the adoption the guide offers for before generation
                ws.adopt_gate_policy(repo.root, confirmation=gpt.confirmation_for(gpt.NO_REQUIRE),
                                     now="2026-10-03T12:00:00Z")
                gpt.g.clear_caches()
                result = next_action(repo)
                self.assertEqual((result["row"], result["reason"]["code"]),
                                 ("16" if stage == "plan" else "30", "bundle_generation_mismatch"))
                self.assertNotIn(result["row"], GATE_ROWS)
                self.assertFalse((ws.plan_approval_gate_status if stage == "plan" else
                                  ws.technical_approval_gate_status)(repo.root, h.read_state(repo), WI)["reachable"])


def ledger_stages(repo: h.ScratchRepo, stage: str) -> dict:
    return h.read_state(repo)["work_items"][WI]["plan_review_stages" if stage == "plan" else
                                                "implementation_review_stages"]


# ---------------------------------------------------------------------------
# workflow-2.8.0 CP7: the lifecycles end to end, driven by the protocol alone
# (`next-action`, `reconcile`, `record-external-result`) with the writer
# sequences the commands name. The forge is the CP3 double.
# ---------------------------------------------------------------------------


def seed_lifecycle(repo: h.ScratchRepo, policy: dict | None = None) -> None:
    """`_seed_unrouted` under the 2.8.0 default (no policy file), or with a
    committed `policy` (a tightening needs no adoption), and an `origin` on
    github.com so the forge identities are the Workflow's own."""
    with no_policy_file():
        _seed_unrouted(repo, "2.2")
    if policy is not None:
        write_policy_file(repo, policy)
        commit_paths(repo, "commit the gate policy", TOGGLE_PATH)
        repo.base = repo.head()
    h.git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    gpt.g.clear_caches()


class _AutoLifecycle(_Lifecycle):
    """`_Lifecycle` extended with the gate-policy actions. With `audited` (the
    default) the reviews are recorded the way the 2.8.0 commands record them
    for an automatic gate: a local review and an external one from another
    family, each with its `Reviewer model:` line and audit keys. Without it
    the reviews are 2.7.0's (`verdicts` scripts them), for the all-human run.
    `pr` scripts what the forge double answers."""

    def __init__(self, test, repo, verdicts=None, *, audited: bool = True):
        super().__init__(test, repo, verdicts or {})
        self.audited = audited
        self.pr = {"checks": "success", "review": None}
        self.review_seq = 0
        self.forge_calls = []
        self.refusals = []
        self.begun = []
        self.acceptances = []
        self.satisfied = []
        self.writers.update({
            "plan.satisfy": self.plan_satisfy, "implementation.satisfy": self.implementation_satisfy,
            "acceptance.satisfy": self.acceptance_satisfy, "pr.apply_review": self.pr_apply_review,
        })

    # -- the forge double -----------------------------------------------------

    def anchor(self) -> str:
        return wgp.anchor_of(self.repo.root, WI, h.read_state(self.repo)["work_items"][WI])["commit"]

    def records(self) -> list:
        head = self.anchor()
        kwargs = {"checks": self.pr["checks"]}
        if self.pr["review"] == "changes_requested":
            kwargs.update(decision="CHANGES_REQUESTED", reviews=gpt.changes_requested(head, f"R{self.review_seq}"))
        elif self.pr["review"] == "approved":
            kwargs.update(decision="APPROVED", reviews=gpt.approved(head))
        return [gpt.pr_record(head, **kwargs)]

    def run_forge(self, argv, timeout):
        self.forge_calls.append(argv)
        return json.dumps(self.records())

    def resolve_gh(self, root):
        return dict(gpt.FAKE_GH)

    # -- evidence the orchestrator reports ------------------------------------

    def report_flows(self, *flow_ids: str) -> None:
        for flow_id in flow_ids:
            result = recorded(self.test, self.repo, "functional_evidence",
                              json.dumps(gpt.functional_record(self.anchor(), flow_id=flow_id)))
            self.test.assertEqual((result["stage"], result["flow_id"]), ("functional", flow_id))

    def report_pr(self) -> dict:
        payload = gpt.reported_payload(self.records(), self.anchor())
        result = recorded(self.test, self.repo, "pr_review_result", json.dumps(payload))
        self.test.assertEqual((result["stage"], result["slot"]), ("pr_review", "pr_reported"))
        return result

    def record_external(self, kind: str) -> None:
        if kind == "functional_evidence":
            return self.report_flows("migration-suite")
        if kind == "pr_review_result":
            self.report_pr()
            return None
        if not self.audited:
            return super().record_external(kind)
        stage = wp.EXTERNAL_RESULT_KIND_STAGES[kind]
        work_item = h.read_state(self.repo)["work_items"][WI]
        role = gpt.MANUAL_PLAN if stage == "plan" else gpt.MANUAL_IMPL
        text = gpt.verdict_text("APPROVE", rcid=self.current_content(stage), role=role, bundle=self.bundle_id(stage),
                                model=gpt.OPENAI, base=work_item["base_commit"])
        result = recorded(self.test, self.repo, kind, text)
        self.test.assertEqual((result["stage"], result["verdict"]), (stage, "APPROVE"))
        return None

    # -- the reviews ----------------------------------------------------------

    def record_local(self, stage: str, rcid: str, bundle: str) -> None:
        role = gpt.LOCAL_PLAN if stage == "plan" else gpt.LOCAL_IMPL
        text = gpt.verdict_text("APPROVE", rcid=rcid, role=role, bundle=bundle, model=gpt.CLAUDE, base=self.repo.base)
        path = gpt.put_feedback(self.repo, text)
        state = h.read_state(self.repo)
        audit = ws.local_review_audit(self.repo.root, state, WI, stage, feedback_text=text, feedback_path=str(path))
        writer = ws.record_local_plan_review if stage == "plan" else ws.record_local_implementation_review
        h.write_state(self.repo, writer(state, WI, verdict="APPROVE", bundle_id=bundle, review_content_id=rcid,
                                        round=self.rounds + 1, now=self.tick(), audit=audit))

    def plan_review_local(self, arguments: dict) -> None:
        if not self.audited:
            return super().plan_review_local(arguments)
        return self.record_local("plan", self.current_content("plan"), self.bundle_id("plan"))

    def implementation_review_local(self, arguments: dict) -> None:
        if not self.audited:
            return super().implementation_review_local(arguments)
        return self.record_local("implementation", self.current_content("implementation"),
                                 self.bundle_id("implementation"))

    def functional_prepare(self, arguments: dict) -> None:
        """`/prepare-functional-review`: the checklist-evidence commit for the
        item's round (the checklist changes with the round)."""
        revision = h.read_state(self.repo)["work_items"][WI]["implementation_revision"]
        path = self.repo.root / ws.FUNCTIONAL_CHECKLIST_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Active milestone\n\n## Functional review checklist\n\n- exercise the flow ({revision})\n")
        h.git(self.repo, "add", "--", ws.FUNCTIONAL_CHECKLIST_PATH)
        blob = h.git(self.repo, "hash-object", "--", ws.FUNCTIONAL_CHECKLIST_PATH)
        h.git(self.repo, "commit", "-q", "-m",
              f"checklist\n\nWorkflow-Functional-Checklist: {WI}/{revision}/{blob}\nWorkflow-Work-Item: {WI}")

    # -- the validation actions (`/satisfy-gate`) and `/apply-pr-review` -----------

    def run(self, decision: dict) -> dict:
        if decision["disposition"] != "validation":
            return super().run(decision)
        action = decision["action"]
        self.test.assertEqual(
            next_action(self.repo, "--work-item", WI, "--expect-state-identity",
                        decision["basis"]["state_identity"])["row"], decision["row"], "the identity check")
        COMMAND_GUARDS[action["id"]][0](self.repo, h.read_state(self.repo))
        self.writers[action["id"]](action["arguments"])
        result = reconciled(self.test, self.repo, decision)
        self.trace.append((decision["row"], action["id"], result["class"]))
        self.test.assertEqual(result["invalid_reasons"], [])
        self.test.assertEqual(result["next"], self.decide(), "reconcile's next is next-action's decision")
        return result["next"]

    def plan_satisfy(self, arguments: dict) -> None:
        """`/satisfy-gate plan`: the policy record, the transaction's state
        write and the approval commit with its three trailers."""
        record = ws.build_policy_approval_record(self.repo.root, h.read_state(self.repo), WI, "plan", now=self.tick())
        ws.state_transaction(self.repo.root, lambda state: ws.apply_plan_approval(state, WI, record, self.tick()))
        h.commit_state(self.repo, "approve the plan by policy", {
            "Workflow-Plan-Approval": record["approved_review_content_id"], "Workflow-Work-Item": WI,
            "Workflow-Gate-Satisfied-By": ws.gate_satisfied_by_trailer(record)})
        self.satisfied.append(("plan", record))
        self.plan_basis = record["basis"]

    def implementation_satisfy(self, arguments: dict) -> None:
        """`/satisfy-gate implementation`: the record, the technical approval
        in one transaction, one metadata-only commit and its validator."""
        record = ws.build_policy_approval_record(self.repo.root, h.read_state(self.repo), WI, "implementation",
                                                 now=self.tick())
        ws.state_transaction(self.repo.root, lambda state: ws.apply_technical_approval(state, WI, record, self.tick()))
        commit = h.commit_state(self.repo, "approve the implementation by policy", {
            "Workflow-Technical-Approval": record["approved_review_content_id"], "Workflow-Work-Item": WI,
            "Workflow-Gate-Satisfied-By": ws.gate_satisfied_by_trailer(record)})
        ws.validate_technical_approval_commit(self.repo.root, commit, WI)
        self.satisfied.append(("implementation", record))

    def acceptance_satisfy(self, arguments: dict) -> None:
        """`/satisfy-gate acceptance`: the Workflow's own query first, the
        evaluation, then the completion with its record and commit; a refusal
        stores the fact the query read and nothing else."""
        try:
            result = ws.satisfy_acceptance_gate(self.repo.root, WI, now=self.tick(), run=self.run_forge,
                                                resolve=self.resolve_gh)
        except ws.GateNotSatisfiableError as exc:
            self.refusals.append(str(exc))
            return
        h.commit_state(self.repo, "accept the milestone by policy",
                       {"Workflow-Work-Item": WI, "Workflow-Gate-Satisfied-By": result["trailer"]})
        self.acceptances.append(result["record"])

    def pr_apply_review(self, arguments: dict) -> None:
        """`/apply-pr-review`: step 1 (`begin_pr_review`), ending the
        invocation after a reopen from `MILESTONE_COMPLETE`; then the bounded
        fix with its durable ordering (the stale approval committed first,
        then the fix, then the `post-fix` generation carrying the applied
        key)."""
        started = h.read_state(self.repo)["work_items"][WI]["phase"]
        result = ws.begin_pr_review(self.repo.root, WI, now=self.tick(), run=self.run_forge, resolve=self.resolve_gh)
        self.begun.append(result)
        if result["result"] not in ("reopened", "already_reopened"):
            return
        if started == "MILESTONE_COMPLETE" and result["result"] == "reopened":
            return
        self.test.assertIn(result["cause"], ("changes_requested", "checks_failed"))
        ws.state_transaction(self.repo.root, lambda state: ws.mark_technical_approval_stale(state, WI, self.tick()))
        h.commit_state(self.repo, "stale the technical approval before the fix")
        self.edits += 1
        (self.repo.root / h.BUNDLE_ITEM_IMPLEMENTATION_PATH).write_text(f"remediation {self.edits}\n")
        h.git(self.repo, "add", "--", h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
        h.git(self.repo, "commit", "-q", "-m", f"fix the pull request finding {self.edits}")
        h.write_state(self.repo, ws.mark_pr_key_applied(h.read_state(self.repo), WI, result["key"], self.tick()))
        h.generate_implementation_bundle(self.repo, stage="post-fix")
        ws.validate_bundle_generation_record_commit(self.repo.root, self.repo.head(), WI)
        self.pr = {"checks": "success", "review": None}  # the author fixed it, the review is cleared

    # -- helpers for the tests ----------------------------------------------------

    def item(self) -> dict:
        return h.read_state(self.repo)["work_items"][WI]

    def step_to(self, row: str) -> dict:
        """Runs decisions until `next-action` names `row`, and returns it."""
        decision = self.decide()
        for _ in range(60):
            if decision["row"] == row:
                return decision
            self.test.assertNotEqual(decision["disposition"], "blocked", (row, decision))
            self.test.assertNotEqual(decision["disposition"], "complete", (row, self.trace))
            decision = self.run(decision)
        raise AssertionError(f"row {row} not reached: {self.trace}")


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestAutomaticLifecycle(unittest.TestCase):
    """Protocol-only, default policy: a `"2.2"` item from no work item to
    `MILESTONE_COMPLETE` with no person at any gate, then reopened by a
    changes-requested fact and completed again (`D-GP-Acceptance`,
    `D-GP-Reopen`, `LPR-R11-001`)."""

    PLANNED_AND_IMPLEMENTED = [
        ("1", "plan.start", "progress"), ("12", "plan.review.local", "gate_reached"),
        ("14", "plan.review.external", "plan_review_verdict"), ("14a", "plan.satisfy", "progress"),
        ("23", "implementation.checkpoint", "progress"), ("23", "implementation.checkpoint", "progress"),
        ("24", "implementation.self_review", "progress"), ("26", "implementation.review.local", "gate_reached"),
        ("28", "implementation.review.external", "implementation_review_verdict"),
        ("28a", "implementation.satisfy", "progress"), ("37", "functional.prepare", "gate_reached"),
        ("38e", "functional.evidence.external", "functional_evidence"),
    ]

    def accepted(self, repo: h.ScratchRepo) -> _AutoLifecycle:
        """The first acceptance, including the sequence of acceptance
        attempted while CI is pending, left at `MILESTONE_COMPLETE`."""
        seed_lifecycle(repo)
        run = _AutoLifecycle(self, repo)
        decision = run.step_to("14a")
        self.assertEqual((decision["disposition"], decision["action"]["id"], decision["policy"]["mode"]),
                         ("validation", "plan.satisfy", "automatic"))
        decision = run.step_to("28a")
        self.assertEqual((decision["disposition"], decision["action"]["id"]), ("validation", "implementation.satisfy"))
        self.assertEqual(run.plan_basis, "POLICY_SATISFIED")
        decision = run.step_to("38h")
        self.assertEqual(run.trace, self.PLANNED_AND_IMPLEMENTED)
        self.assertEqual((run.item()["phase"], decision["disposition"], decision["action"]["id"]),
                         ("AWAITING_FUNCTIONAL_REVIEW", "validation", "acceptance.satisfy"))
        self.assertIn("migration-suite", run.item()["gate_evidence"]["functional"])
        self.assertIsNone(run.item()["gate_evidence"]["pr"], "nothing has asked GitHub yet")
        # acceptance attempted while CI is pending: the act refuses, storing the fact it read
        run.pr["checks"] = "pending"
        decision = run.run(decision)
        self.assertEqual(run.trace[-1], ("38h", "acceptance.satisfy", "gate_reached"))
        self.assertEqual(len(run.refusals), 1)
        self.assertIn("ci_green", run.refusals[0])
        item = run.item()
        self.assertEqual((item["phase"], "acceptance_satisfaction" in item), ("AWAITING_FUNCTIONAL_REVIEW", False))
        fact = item["gate_evidence"]["pr"]
        self.assertEqual((fact["provenance"]["source"], fact["checks"]["state"]), ("workflow_gh", "pending"))
        self.assertEqual((decision["row"], decision["disposition"], decision["satisfied_by"]),
                         ("38f", "external_gate", "pr_review_result"))
        # CI goes green and the orchestrator reports a fresh pr_review_result
        run.pr["checks"] = "success"
        decision = run.run(decision)
        self.assertEqual(run.trace[-1], ("38f", "pr.review.external", "pr_review_result"))
        reported = run.item()["gate_evidence"]["pr_reported"]
        self.assertEqual((reported["provenance"]["source"], reported["checks"]["state"]),
                         ("orchestrator_forge", "success"))
        self.assertEqual(run.item()["gate_evidence"]["pr"]["checks"]["state"], "pending",
                         "a reported fact never fills the slot a decision reads")
        # code: the report differs from the stored fact, so it arms the Workflow's own query first (38d)
        self.assertEqual((decision["row"], decision["reason"]["code"], decision["action"]["id"]),
                         ("38d", "pr_query_due", "pr.apply_review"))
        decision = run.run(decision)
        self.assertEqual(run.trace[-1], ("38d", "pr.apply_review", "no_progress"))
        self.assertEqual(run.begun[-1]["result"], "pr_fact_refreshed")
        self.assertEqual(run.item()["gate_evidence"]["pr"]["checks"]["state"], "success")
        self.assertEqual((decision["row"], decision["disposition"], decision["action"]["id"]),
                         ("38h", "validation", "acceptance.satisfy"))
        calls = len(run.forge_calls)
        decision = run.run(decision)  # its act queries GitHub itself again and completes
        self.assertEqual(len(run.forge_calls), calls + 1)
        self.assertEqual(run.trace[-1], ("38h", "acceptance.satisfy", "progress"))
        self.assertEqual((decision["row"], decision["disposition"]), ("40", "complete"))
        return run

    def test_the_item_is_planned_implemented_and_accepted_with_no_person(self):
        with h.ScratchRepo() as repo:
            run = self.accepted(repo)
            state = h.read_state(repo)
            item = state["work_items"][WI]
            self.assertEqual((item["phase"], state["active_work_item_id"]), ("MILESTONE_COMPLETE", None))
            for approval, stage in (("plan_approval", "plan"), ("technical_approval", "implementation")):
                ws.validate_approval_record(item[approval], stage=stage)
                self.assertEqual((item[approval]["basis"], item[approval]["status"]), ("POLICY_SATISFIED", "CURRENT"))
                self.assertEqual(item[approval]["user_confirmation"],
                                 f"policy:{item[approval]['policy_evidence']['policy_digest']}")
                self.assertEqual(item[approval]["policy_evidence"]["trust"], {"review_verdicts": "orchestrator"})
            for stages in (item["plan_review_stages"], item["implementation_review_stages"]):
                self.assertEqual({entry["reviewer_model"] for entry in stages.values()
                                  if isinstance(entry, dict) and "reviewer_model" in entry},
                                 {gpt.CLAUDE, gpt.OPENAI})
            record = item["acceptance_satisfaction"]
            self.assertEqual(ws.acceptance_satisfaction_errors(record), [])
            self.assertEqual(record, run.acceptances[0])
            self.assertEqual(record["trust"]["pr_fact"], "workflow_gh")
            self.assertEqual(record["inputs"]["pr"]["provenance"], "workflow_gh")
            self.assertEqual(set(record["inputs"]["functional"]), {"migration-suite"})
            self.assertEqual(record["policy_digest"], wgp.effective_policy(repo.root, state)["digest"])
            log = h.git(repo, "log", "--format=%B", "-n", "40")
            for trailer in ("Workflow-Plan-Approval:", "Workflow-Technical-Approval:", "Workflow-Gate-Satisfied-By: policy:"):
                self.assertIn(trailer, log)
            self.assertEqual(h.git(repo, "log", "-1", "--format=%B").count("Workflow-Gate-Satisfied-By: policy:"), 1,
                             "the completion commit names the policy")
            self.assertEqual(item.get("reopenings"), None)

    def test_a_changes_requested_fact_reopens_the_item_and_it_completes_again(self):
        with h.ScratchRepo() as repo:
            run = self.accepted(repo)
            first = run.item()["acceptance_satisfaction"]
            trace = len(run.trace)
            # a reviewer asks for changes; the orchestrator reports the fact
            run.pr.update(review="changes_requested")
            run.review_seq = 1
            run.report_pr()
            decision = run.decide()
            self.assertEqual((decision["row"], decision["reason"]["code"], decision["snapshot"]["phase"]),
                             ("38d", "pr_query_due", "MILESTONE_COMPLETE"))
            decision = run.run(decision)
            self.assertEqual(run.begun[-1]["result"], "reopened")
            item = run.item()
            self.assertEqual(item["phase"], "AWAITING_FUNCTIONAL_REVIEW")
            entry, = item["reopenings"]
            self.assertEqual((entry["cause"], entry["from_phase"], entry["n"]),
                             ("changes_requested", "MILESTONE_COMPLETE", 1))
            self.assertEqual(item["technical_approval"]["status"], "CURRENT", "the reopen alone stales nothing")
            self.assertIsNone(h.read_state(repo)["active_work_item_id"], "a reopen never sets the pointer")
            self.assertEqual((decision["row"], decision["reason"]["code"]), ("38d", "pr_review_actionable"))
            # remediation: the stale approval first, then the fix, then the post-fix generation
            commits = h.git(repo, "rev-list", "--count", "HEAD")
            decision = run.run(decision)
            self.assertEqual(run.trace[-1], ("38d", "pr.apply_review", "progress"))
            self.assertEqual(run.begun[-1]["result"], "already_reopened")
            item = run.item()
            keys = item["gate_evidence"]["pr_keys"]
            self.assertEqual((item["phase"], item["technical_approval"]["status"], item["implementation_revision"]),
                             ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "STALE", 2))
            self.assertEqual(keys["applied"], keys["reopened_for"])
            self.assertEqual(int(h.git(repo, "rev-list", "--count", "HEAD")) - int(commits), 3)
            self.assertIn("Workflow-Bundle-Generation-Record", h.git(repo, "log", "-1", "--format=%B"))
            # re-review, re-validation, completion
            self.assertEqual(decision["row"], "26")
            decision = run.step_to("28a")
            decision = run.step_to("37")
            decision = run.step_to("38h")
            self.assertEqual(run.trace[trace:], [
                ("38d", "pr.apply_review", "progress"), ("38d", "pr.apply_review", "progress"),
                ("26", "implementation.review.local", "gate_reached"),
                ("28", "implementation.review.external", "implementation_review_verdict"),
                ("28a", "implementation.satisfy", "progress"), ("37", "functional.prepare", "gate_reached"),
                ("38e", "functional.evidence.external", "functional_evidence"),
                ("38f", "pr.review.external", "pr_review_result"),
                ("38d", "pr.apply_review", "no_progress")])
            self.assertEqual(h.read_state(repo)["work_items"][WI]["technical_approval"]["basis"], "POLICY_SATISFIED")
            decision = run.run(decision)
            self.assertEqual(run.trace[-1], ("38h", "acceptance.satisfy", "progress"))
            self.assertEqual((decision["row"], decision["disposition"]), ("40", "complete"))
            item = run.item()
            self.assertEqual((item["phase"], item["technical_approval"]["status"]), ("MILESTONE_COMPLETE", "CURRENT"))
            self.assertEqual(len(item["reopenings"]), 1)
            self.assertNotEqual(item["acceptance_satisfaction"]["evaluated_at"], first["evaluated_at"])
            self.assertEqual(ws.acceptance_satisfaction_errors(item["acceptance_satisfaction"]), [])
            self.assertEqual(item["acceptance_satisfaction"]["inputs"]["pr"]["head"], run.anchor())


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestMixedLifecycle(unittest.TestCase):
    """Only one gate human: the run stops at exactly that gate and runs the
    others automatically."""

    def test_only_the_plan_human_stops_at_the_plan_gate_and_runs_the_rest_automatically(self):
        with h.ScratchRepo() as repo:
            seed_lifecycle(repo, gpt.PLAN_HUMAN)
            run = _AutoLifecycle(self, repo)
            gate = run.drive(until="human_gate")
            self.assertEqual((gate["row"], gate["action"]["id"], gate["disposition"]),
                             ("15", "plan.approve", "human_gate"), "the plan gate, and nothing earlier validated it")
            self.assertEqual([step[:2] for step in run.trace],
                             [("1", "plan.start"), ("12", "plan.review.local"), ("14", "plan.review.external")])
            decision = run.run(gate)  # the person approves the plan
            self.assertEqual(run.trace[-1], ("15", "plan.approve", "user"))
            self.assertEqual(run.item()["plan_approval"]["basis"], "EXTERNAL_APPROVE")
            self.assertNotIn("policy_evidence", run.item()["plan_approval"])
            decision = run.step_to("28a")
            self.assertEqual((decision["disposition"], decision["policy"]["mode"]), ("validation", "automatic"))
            run.step_to("38h")
            run.run(run.decide())
            final = run.decide()
            self.assertEqual((final["row"], final["disposition"]), ("40", "complete"))
            item = run.item()
            self.assertEqual((item["technical_approval"]["basis"], "acceptance_satisfaction" in item),
                             ("POLICY_SATISFIED", True))
            self.assertEqual([step[:2] for step in run.trace if step[0] in ("15", "14a", "28a", "38h")],
                             [("15", "plan.approve"), ("28a", "implementation.satisfy"),
                              ("38h", "acceptance.satisfy")])

    def test_only_the_acceptance_human_stops_at_the_functional_gate(self):
        with h.ScratchRepo() as repo:
            seed_lifecycle(repo, gpt.ACCEPTANCE_HUMAN)
            run = _AutoLifecycle(self, repo)
            gate = run.drive(until="human_gate")
            self.assertEqual((gate["row"], gate["action"]["id"]), ("39", "functional.review"))
            self.assertEqual([step[:2] for step in run.trace if step[0] in ("14a", "15", "28a", "29")],
                             [("14a", "plan.satisfy"), ("28a", "implementation.satisfy")],
                             "the plan and technical gates ran automatically")
            self.assertNotIn("38e", [step[0] for step in run.trace], "no evidence is asked of the orchestrator")
            item = run.item()
            self.assertEqual((item["plan_approval"]["basis"], item["technical_approval"]["basis"]),
                             ("POLICY_SATISFIED", "POLICY_SATISFIED"))
            self.assertEqual((item["phase"], "acceptance_satisfaction" in item), ("AWAITING_FUNCTIONAL_REVIEW", False))
            final = run.run(gate)  # the person tests and accepts
            self.assertEqual(run.trace[-1], ("39", "functional.review", "user"))
            self.assertEqual((final["row"], final["disposition"]), ("40", "complete"))
            self.assertNotIn("acceptance_satisfaction", run.item(), "a person accepted, not the policy")
            self.assertEqual(run.forge_calls, [], "nothing queried the forge")

    def test_only_the_technical_gate_human_stops_at_the_technical_gate(self):
        with h.ScratchRepo() as repo:
            seed_lifecycle(repo, TECHNICAL_HUMAN)
            run = _AutoLifecycle(self, repo)
            gate = run.drive(until="human_gate")
            self.assertEqual((gate["row"], gate["action"]["id"]), ("29", "implementation.approve"))
            self.assertIn(("14a", "plan.satisfy", "progress"), run.trace)
            self.assertEqual(run.item()["plan_approval"]["basis"], "POLICY_SATISFIED")


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestAllHumanLifecycle(unittest.TestCase):
    """Every gate stops for a person as in 2.7.0, and a reported
    `CHANGES_REQUESTED` fact triggers the Workflow's own query even so, whose
    red answer reopens the item and is followed by `pr.apply_review`
    (`LPR-R3-003`, `LPR-R28-001`)."""

    VERDICTS = {**{key: values + ["APPROVE"] for key, values in TestLifecycleEndToEnd2_2.VERDICTS.items()
                   if key.startswith("implementation")},
                **{key: list(values) for key, values in TestLifecycleEndToEnd2_2.VERDICTS.items()
                   if key.startswith("plan")}}

    def test_every_gate_stops_for_a_person_and_a_red_fact_reopens_the_item(self):
        with h.ScratchRepo() as repo:
            _seed_unrouted(repo, "2.2")  # the harness's all-human file, committed, not adopted
            h.git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
            run = _AutoLifecycle(self, repo, self.VERDICTS, audited=False)
            final = run.drive()
            self.assertEqual(run.trace, TestLifecycleEndToEnd2_2.EXPECTED, "2.7.0's run, row for row")
            self.assertEqual((final["row"], final["disposition"]), ("40", "complete"))
            state = h.read_state(repo)
            item = state["work_items"][WI]
            self.assertEqual((run.plan_basis, item["technical_approval"]["basis"]),
                             ("EXTERNAL_APPROVE", "EXTERNAL_APPROVE"))
            self.assertNotIn("POLICY_SATISFIED", json.dumps(state))
            self.assertNotIn("verdict_sha256", json.dumps(item["plan_review_stages"]))
            self.assertNotIn("acceptance_satisfaction", item)
            self.assertNotIn("gate_policy_adoption", state)
            self.assertEqual(run.forge_calls, [], "the Workflow queried nothing until a fact was reported")
            # a reported CHANGES_REQUESTED fact: the Workflow's query, then the reopen
            run.pr.update(review="changes_requested")
            run.review_seq = 1
            run.report_pr()
            self.assertIsNone(run.item()["gate_evidence"]["pr"], "a reported fact stores nothing a decision reads")
            decision = run.decide()
            self.assertEqual((decision["row"], decision["disposition"], decision["reason"]["code"],
                              decision["action"]["id"]), ("38d", "automatic", "pr_query_due", "pr.apply_review"))
            decision = run.run(decision)
            self.assertEqual(len(run.forge_calls), 1, "the Workflow's own query ran")
            self.assertEqual(run.begun[-1]["result"], "reopened")
            fact = run.item()["gate_evidence"]["pr"]
            self.assertEqual((fact["provenance"]["source"], fact["review_decision"]),
                             ("workflow_gh", "CHANGES_REQUESTED"))
            self.assertEqual(run.item()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
            self.assertEqual((decision["row"], decision["action"]["id"], decision["reason"]["code"]),
                             ("38d", "pr.apply_review", "pr_review_actionable"), "followed by pr.apply_review")
            decision = run.run(decision)
            self.assertEqual(run.begun[-1]["result"], "already_reopened")
            self.assertEqual((run.item()["phase"], decision["row"]), ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "26"))
            # the rest is the person's, as before
            before = len(run.trace)
            final = run.drive(decision)
            self.assertEqual([step[:2] for step in run.trace[before:] if step[2] == "user"],
                             [("29", "implementation.approve"), ("39", "functional.review")])
            self.assertEqual((final["row"], run.item()["phase"], len(run.item()["reopenings"])),
                             ("40", "MILESTONE_COMPLETE", 1))
            self.assertNotIn("POLICY_SATISFIED", json.dumps(h.read_state(repo)))


if __name__ == "__main__":
    unittest.main()
