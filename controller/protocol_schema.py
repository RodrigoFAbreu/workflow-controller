"""The Workflow Orchestration Protocol v1 schema and a stdlib validator for
the keyword subset it uses
(``docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md``, A.2).

``protocol_schema.json`` is the schema Workflow 2.9.0 publishes (protocol 1.2), vendored
byte for byte (its sha256 is pinned by a test) and read once, at import.

The validator checks a *document* the way the protocol's "a minor bump only
adds things" rule needs, so a protocol ``1.1`` envelope validates with no
Controller release:

- ``type``, ``required``, ``items`` and ``$ref`` are checked strictly;
- ``additionalProperties`` is **open**: an unknown key is ignored, never
  rejected, and a schema given as its value validates the extra values;
- an ``enum`` is checked strictly except at :data:`OPEN_ENUMS`, the sets a
  minor version may extend. Those are open strings (the ``type`` is still
  checked); the Controller fails closed on an unknown value in its own
  semantic layer, never here;
- annotation keywords are accepted and ignored.

A *schema* that uses any keyword outside :data:`VALIDATION_KEYWORDS` and
:data:`ANNOTATION_KEYWORDS` is rejected at load, so a later vendored schema
that needs more fails closed instead of passing unchecked.

This module imports nothing from the rest of the package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALIDATION_KEYWORDS = frozenset({
    "type", "required", "properties", "items", "$ref", "$defs", "enum", "additionalProperties",
})
ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "title", "description"})

#: Schema locations whose ``enum`` a minor protocol version may extend.
OPEN_ENUMS = frozenset({
    "#/$defs/error/properties/code",
    "#/$defs/action/properties/id",
    "#/$defs/nullable_action/properties/id",
    "#/$defs/decision/properties/disposition",
    "#/$defs/decision/properties/satisfied_by",
    "#/$defs/worker/properties/role",
    "#/$defs/results/properties/verify/properties/checks/items/properties/id",
})

SCHEMA_PATH = Path(__file__).with_name("protocol_schema.json")

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(ValueError):
    """The schema itself is unusable: an unsupported keyword, an unknown
    type name or an unresolvable ``$ref``."""


class SchemaViolation(ValueError):
    """A document does not match the schema. ``path`` locates the failing
    value (``$.result.action.id``)."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.problem = message


class Schema:
    """A loaded, checked schema."""

    def __init__(self, document: dict) -> None:
        if not isinstance(document, dict):
            raise SchemaError("the schema is not a JSON object")
        self._root = document
        self._check_schema(document, "#")

    # -- load-time checks ---------------------------------------------------

    def _check_schema(self, node: Any, pointer: str) -> None:
        if not isinstance(node, dict):
            raise SchemaError(f"{pointer}: a schema must be an object")
        for key in node:
            if key not in VALIDATION_KEYWORDS and key not in ANNOTATION_KEYWORDS:
                raise SchemaError(f"{pointer}: the keyword {key!r} is outside the supported subset")
        kinds = node.get("type")
        if kinds is not None:
            for kind in [kinds] if isinstance(kinds, str) else kinds:
                if kind not in _TYPES:
                    raise SchemaError(f"{pointer}: unknown type {kind!r}")
        if "$ref" in node:
            self._resolve(node["$ref"], pointer)
        for name, child in node.get("$defs", {}).items():
            self._check_schema(child, f"{pointer}/$defs/{name}")
        for name, child in node.get("properties", {}).items():
            self._check_schema(child, f"{pointer}/properties/{name}")
        if "items" in node:
            self._check_schema(node["items"], f"{pointer}/items")
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            self._check_schema(extra, f"{pointer}/additionalProperties")
        elif extra is not None and not isinstance(extra, bool):
            raise SchemaError(f"{pointer}: additionalProperties is neither a schema nor a boolean")

    def _resolve(self, ref: Any, pointer: str) -> dict:
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise SchemaError(f"{pointer}: $ref {ref!r} is not a local reference")
        node: Any = self._root
        for part in [p for p in ref[1:].split("/") if p]:
            if not isinstance(node, dict) or part not in node:
                raise SchemaError(f"{pointer}: $ref {ref!r} does not resolve")
            node = node[part]
        if not isinstance(node, dict):
            raise SchemaError(f"{pointer}: $ref {ref!r} does not name a schema")
        return node

    def enum_at(self, pointer: str) -> tuple:
        """The ``enum`` listed at schema ``pointer``, as published."""
        node = self._resolve(pointer, "#")
        if "enum" not in node:
            raise SchemaError(f"{pointer}: no enum")
        return tuple(node["enum"])

    # -- document validation ------------------------------------------------

    def validate_envelope(self, document: Any) -> None:
        """Raise :class:`SchemaViolation` unless ``document`` is an envelope."""
        self._validate(document, self._root, "#", "$")

    def validate_result(self, operation: str, result: Any) -> None:
        """Raise :class:`SchemaViolation` unless ``result`` matches
        ``$defs.results.<operation>``. An operation the schema does not list
        is a violation, never skipped."""
        pointer = f"#/$defs/results/properties/{operation}"
        try:
            node = self._resolve(pointer, "#")
        except SchemaError as exc:
            raise SchemaViolation("$.operation", f"the schema has no result for {operation!r}") from exc
        self._validate(result, node, pointer, "$.result")

    def _validate(self, value: Any, node: dict, pointer: str, path: str) -> None:
        if "$ref" in node:
            ref = node["$ref"]
            self._validate(value, self._resolve(ref, pointer), ref, path)
        kinds = node.get("type")
        if kinds is not None:
            names = [kinds] if isinstance(kinds, str) else list(kinds)
            if not any(_TYPES[name](value) for name in names):
                raise SchemaViolation(path, f"expected {'|'.join(names)}, got {_describe(value)}")
        if "enum" in node and pointer not in OPEN_ENUMS and value not in node["enum"]:
            raise SchemaViolation(path, f"{value!r} is not one of {node['enum']}")
        if isinstance(value, dict):
            for name in node.get("required", ()):
                if name not in value:
                    raise SchemaViolation(path, f"missing required property {name!r}")
            properties = node.get("properties", {})
            extra = node.get("additionalProperties")
            for name, child in value.items():
                if name in properties:
                    self._validate(child, properties[name], f"{pointer}/properties/{name}", f"{path}.{name}")
                elif isinstance(extra, dict):
                    self._validate(child, extra, f"{pointer}/additionalProperties", f"{path}.{name}")
        elif isinstance(value, list) and "items" in node:
            for index, child in enumerate(value):
                self._validate(child, node["items"], f"{pointer}/items", f"{path}[{index}]")


def _describe(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def load(path: Path = SCHEMA_PATH) -> Schema:
    return Schema(json.loads(path.read_text(encoding="utf-8")))


#: The vendored schema, loaded and checked once, at import.
SCHEMA = load()
