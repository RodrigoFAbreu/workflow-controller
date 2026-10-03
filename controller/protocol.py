"""The client of the Workflow Orchestration Protocol v1
(``docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md``, Design A).

Workflow 2.7.0 ships ``scripts/workflow_protocol.py``, a stdlib script that
answers one operation per run with one JSON envelope on stdout (exit ``0``
ok, ``3`` a refusal with a stable code, ``2`` an invalid request, ``1`` an
internal error). :func:`run` runs one operation and returns an
:class:`Envelope`; :func:`describe`, :func:`verify`, :func:`next_action` and
:func:`reconcile` return the typed result of the operation.

**What runs (invariant I6).** The Workflow's own script set: the
``scripts/<name>.py`` keys of the installation record's ``managed`` map
(``.workflow-manager/installation.json``) whose ``<name>`` does not end in
``_test``. Never a directory listing, a glob, or a list the Controller
keeps: the protocol script imports its sibling modules from its own
directory, so a release that adds a module needs no Controller change.
Each file is read once from the target, copied into a private directory the
Controller owns and digested; the path-to-digest map is the script set's
identity (:func:`script_set`). **Every operation derives it afresh** and runs
from its own copy, with the Git isolation of ADR 0006 that
``controller.workflow_contract`` shares. A listed file that is missing or
not a regular file is refused, naming it; a file the record does not list
(a product's own ``scripts/extra.py``) is never copied, digested or refused.

**What is believed.** The document is validated against the vendored schema
(:mod:`controller.protocol_schema`) with the open-document rules of plan A.2.
Exit code and ``ok`` must agree, the operation must be the one asked for and
the protocol major must be ``1``; anything else is
:class:`~controller.errors.WorkflowProtocolFailedError` (or
:class:`~controller.errors.WorkflowProtocolUnsupportedError`). An unknown
value that the schema leaves open is the caller's to fail closed on.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

from controller import protocol_schema, workflow_contract
from controller.errors import (
    WorkflowProtocolFailedError,
    WorkflowProtocolRefusedError,
    WorkflowProtocolUnsupportedError,
    WorkflowQueryError,
)

PROTOCOL_MAJOR = 1
SCRIPT_KEY = "scripts/workflow_protocol.py"
INSTALLATION_RECORD = ".workflow-manager/installation.json"

#: Exit status by error code (every other known code is a refusal, ``3``).
_EXIT_BY_CODE = {"invalid_request": 2, "internal_error": 1}
_REFUSED_EXIT = 3
#: The error codes the vendored schema lists. Each has one exit status; a
#: code a later minor adds may use any non-zero one.
KNOWN_ERROR_CODES = frozenset(protocol_schema.SCHEMA.enum_at("#/$defs/error/properties/code"))
#: The action ids the vendored schema lists: the catalogue this Controller
#: release was built against, automatic or not.
KNOWN_ACTION_IDS = frozenset(protocol_schema.SCHEMA.enum_at("#/$defs/action/properties/id"))
_NON_ZERO_EXITS = (1, 2, 3)
_MISSING_MODULE_RE = re.compile(r"No module named '([^']+)'")
_STDERR_TAIL_CHARS = 4000


# ---------------------------------------------------------------------------
# The script set.
# ---------------------------------------------------------------------------


def qualifying_script_keys(managed: Any) -> list[str]:
    """The keys of ``managed`` that belong to the Workflow's script set,
    sorted. A key qualifies only when it is exactly ``scripts/<name>.py``:
    one path segment, so a ``scripts/pkg/mod.py`` or a traversal key
    (``scripts/../x.py``) never does, and ``<name>`` does not end in
    ``_test``. The rule is structural, never a glob. A ``managed`` that is
    absent or not an object lists nothing."""
    if not isinstance(managed, dict):
        return []
    keys = []
    for key in managed:
        if not isinstance(key, str):
            continue
        parts = key.split("/")
        if len(parts) != 2 or parts[0] != "scripts":
            continue
        name = parts[1]
        stem = name[:-3] if name.endswith(".py") else ""
        if not stem or "\x00" in name or stem.endswith("_test"):
            continue
        keys.append(key)
    return sorted(keys)


def protocol_script_listed(managed: Any) -> bool:
    """Whether ``managed`` lists the protocol script: the capability test
    that makes a release a protocol target (``no_protocol`` otherwise)."""
    return SCRIPT_KEY in qualifying_script_keys(managed)


@dataclasses.dataclass(frozen=True)
class ScriptSet:
    """The Workflow's script set as read from a target: ``sources`` (key to
    bytes) and ``digests`` (key to sha256). ``digests`` is its identity."""

    sources: Mapping[str, bytes]
    digests: Mapping[str, str]


def read_managed(root: Path) -> Any:
    """The installation record's ``managed`` field, or ``None`` when the
    record, or the field, is absent or not an object. An unreadable or
    malformed record is refused, never read as "absent"."""
    path = root / INSTALLATION_RECORD
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise _failed(f"the installation record {path} cannot be read ({exc})",
                      reason="protocol_installation_unreadable", path=str(path), error=str(exc)) from exc
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise _failed(f"the installation record {path} is not JSON ({exc})",
                      reason="protocol_installation_unreadable", path=str(path), error=str(exc)) from exc
    managed = record.get("managed") if isinstance(record, dict) else None
    return managed if isinstance(managed, dict) else None


def script_set(root: Path, managed: Any) -> ScriptSet:
    """Read, once each, the qualifying files of ``managed`` from ``root`` and
    digest them. Refuses naming the path when a listed file is missing or is
    not a regular file (a symlink, a directory); examines nothing else."""
    sources: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    for key in qualifying_script_keys(managed):
        path = root / key
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise _modified(key, f"cannot be opened ({exc.strerror or exc})") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise _modified(key, "is not a regular file")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read()
        except OSError as exc:
            raise _modified(key, f"cannot be read ({exc.strerror or exc})") from exc
        finally:
            os.close(fd)
        sources[key] = data
        digests[key] = hashlib.sha256(data).hexdigest()
    return ScriptSet(sources, digests)


def managed_digests(root: Path) -> dict[str, str] | None:
    """The managed-script digest map of ``root`` read from the installation
    record and the files' bytes alone -- no Workflow script is run, which is
    what makes it usable under a drifted installation. ``None`` when the
    record or a managed file cannot be read (nothing then equals a recorded
    map)."""
    try:
        return dict(script_set(root, read_managed(root)).digests)
    except WorkflowProtocolFailedError:
        return None


def _modified(key: str, problem: str) -> WorkflowProtocolFailedError:
    return _failed(f"the Workflow script {key} {problem}; nothing was run",
                   reason="protocol_script_modified", path=key)


# ---------------------------------------------------------------------------
# Typed documents.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Refusal:
    code: str
    message: str
    retryable: bool
    native: dict | None


@dataclasses.dataclass(frozen=True)
class Envelope:
    """One validated answer. ``document`` is the parsed envelope as
    received; ``digests`` the script set's identity it was run under."""

    document: dict
    operation: str
    workflow_release: str
    protocol_version: str
    ok: bool
    result: dict | None
    error: Refusal | None
    digests: Mapping[str, str]


@dataclasses.dataclass(frozen=True)
class Worker:
    role: str
    fresh_session: bool
    independent_of: tuple[str, ...]
    user_only: bool


@dataclasses.dataclass(frozen=True)
class Action:
    id: str
    arguments: Mapping[str, str]
    invocation: str | None
    worker: Worker
    allowed_results: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Basis:
    work_item_id: str
    state_revision: int | None
    state_identity: str
    phase: str
    head: str | None
    checkpoints: Mapping[str, str]


@dataclasses.dataclass(frozen=True)
class Reason:
    code: str
    text: str
    remedy: str | None


@dataclasses.dataclass(frozen=True)
class Decision:
    """The ``next-action`` result. ``raw`` is the result object as received,
    the document ``reconcile`` is later handed (I5)."""

    row: str
    basis: Basis | None
    snapshot: Mapping[str, Any]
    disposition: str
    action: Action | None
    satisfied_by: str | None
    alternatives: tuple[Action, ...]
    reason: Reason
    raw: dict


@dataclasses.dataclass(frozen=True)
class Describe:
    workflow_release: str
    protocol_version: str
    supported_protocol_majors: tuple[int, ...]
    supported_governing_versions: tuple[str, ...]
    capabilities: Mapping[str, tuple[str, ...]]


@dataclasses.dataclass(frozen=True)
class Check:
    id: str
    status: str
    detail: str


@dataclasses.dataclass(frozen=True)
class Verify:
    healthy: bool
    checks: tuple[Check, ...]


@dataclasses.dataclass(frozen=True)
class PhaseIdentity:
    phase: str
    state_identity: str


@dataclasses.dataclass(frozen=True)
class Reconciliation:
    """The ``reconcile`` result: ``outcome`` is the schema's ``class``."""

    outcome: str
    from_: PhaseIdentity | None
    to: PhaseIdentity | None
    evidence: Mapping[str, Any]
    invalid_reasons: tuple[tuple[str, str], ...]
    basis: Basis | None
    next: dict | None
    raw: dict


def _worker(raw: dict) -> Worker:
    return Worker(raw["role"], raw["fresh_session"], tuple(raw["independent_of"]), raw["user_only"])


def _action(raw: dict | None) -> Action | None:
    if raw is None:
        return None
    return Action(raw["id"], dict(raw["arguments"]), raw["invocation"], _worker(raw["worker"]),
                  tuple(raw["allowed_results"]))


def _basis(raw: dict | None) -> Basis | None:
    if raw is None:
        return None
    return Basis(raw["work_item_id"], raw["state_revision"], raw["state_identity"], raw["phase"],
                 raw["head"], dict(raw["checkpoints"]))


def _phase_identity(raw: dict | None) -> PhaseIdentity | None:
    return None if raw is None else PhaseIdentity(raw["phase"], raw["state_identity"])


def parse_decision(result: dict) -> Decision:
    """The typed ``next-action`` result of a validated ``result`` object."""
    reason = result["reason"]
    return Decision(
        row=result["row"], basis=_basis(result.get("basis")), snapshot=dict(result["snapshot"]),
        disposition=result["disposition"], action=_action(result["action"]),
        satisfied_by=result["satisfied_by"],
        alternatives=tuple(_action(item) for item in result["alternatives"]),
        reason=Reason(reason["code"], reason["text"], reason["remedy"]), raw=result,
    )


# ---------------------------------------------------------------------------
# Running an operation.
# ---------------------------------------------------------------------------


def run(root: Path, operation: str, args: list[str] | tuple[str, ...] = (), *,
        timeout: float | None = None) -> Envelope:
    """Run one protocol ``operation`` against the repository at ``root`` and
    return its :class:`Envelope`, ``ok: false`` ones included (a refusal is
    an answer). Raises :class:`WorkflowProtocolFailedError` when nothing the
    Controller may act on came back and
    :class:`WorkflowProtocolUnsupportedError` for an unsupported protocol.
    ``timeout`` ``None`` is the Workflow query timeout in force."""
    if timeout is None:
        timeout = workflow_contract.query_timeout_seconds()
    managed = read_managed(root)
    if not protocol_script_listed(managed):
        raise _failed(f"{root}'s installation record does not list {SCRIPT_KEY}: the Workflow does not "
                      f"ship the orchestration protocol", reason="no_protocol", operation=operation)
    scripts = script_set(root, managed)
    argv_args = ["--protocol-major", str(PROTOCOL_MAJOR), "--repo-root", str(root), operation, *args]
    context = {
        "release": None, "query": f"protocol {operation}", "work_item_id": "", "operation": operation,
        "cwd": str(root), "argv": None, "returncode": None, "stdout_tail": "", "stderr_tail": "",
    }
    try:
        completed = workflow_contract.run_in_private_copy(
            root, dict(scripts.sources), SCRIPT_KEY, argv_args, timeout=timeout, context=context)
    except WorkflowQueryError as exc:
        evidence = dict(exc.evidence)
        reason = str(evidence.get("reason", "query_failed"))
        evidence["reason"] = "protocol_" + reason.removeprefix("query_")
        raise WorkflowProtocolFailedError(exc.message, evidence=evidence) from exc
    return _envelope(operation, completed, context, scripts)


def _envelope(operation: str, completed, context: dict, scripts: ScriptSet) -> Envelope:
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    if not stdout:
        raise _no_document(operation, completed, context)
    try:
        document = json.loads(stdout)
    except ValueError as exc:
        raise _no_document(operation, completed, context, f"stdout is not exactly one JSON document ({exc})") from exc

    def invalid(problem: str) -> WorkflowProtocolFailedError:
        return _failed(f"protocol {operation} gave an answer outside the protocol: {problem}",
                       reason="protocol_envelope_invalid", problem=problem, **_run_evidence(context, completed))

    try:
        protocol_schema.SCHEMA.validate_envelope(document)
    except protocol_schema.SchemaViolation as exc:
        raise invalid(str(exc)) from exc
    version = document["protocol"]["version"]
    major = version.split(".", 1)[0]
    error = document.get("error")
    if major != str(PROTOCOL_MAJOR) or (isinstance(error, dict) and error.get("code") == "unsupported_protocol"):
        raise WorkflowProtocolUnsupportedError(
            f"protocol {operation}: the Workflow speaks protocol {version} and said "
            f"{(error or {}).get('message', 'nothing')!r}; the Controller speaks major {PROTOCOL_MAJOR}",
            evidence={"reason": "protocol_unsupported", "operation": operation, "protocol_version": version,
                      "error": error, **_run_evidence(context, completed)})
    ok = document["ok"]
    if (completed.returncode == 0) != ok:
        raise invalid(f"exit status {completed.returncode} disagrees with ok={ok}")
    if document["operation"] not in (operation, None) or (ok and document["operation"] != operation):
        raise invalid(f"the envelope answers {document['operation']!r}, not {operation!r}")
    refusal = None
    result = None
    if ok:
        result = document.get("result")
        if not isinstance(result, dict):
            raise invalid("ok is true but there is no result object")
        try:
            protocol_schema.SCHEMA.validate_result(operation, result)
        except protocol_schema.SchemaViolation as exc:
            raise invalid(str(exc)) from exc
    else:
        if not isinstance(error, dict):
            raise invalid("ok is false but there is no error object")
        code = error["code"]
        expected = _EXIT_BY_CODE.get(code, _REFUSED_EXIT if code in KNOWN_ERROR_CODES else None)
        if (expected is not None and completed.returncode != expected) or (
                expected is None and completed.returncode not in _NON_ZERO_EXITS):
            raise invalid(f"exit status {completed.returncode} does not belong to error code {code!r}")
        refusal = Refusal(code, error["message"], error["retryable"], error["native"])
    return Envelope(document, operation, document["workflow_release"], version, ok, result, refusal,
                    dict(scripts.digests))


def _run_evidence(context: dict, completed) -> dict:
    return {key: context.get(key) for key in ("release", "operation", "cwd", "argv", "returncode",
                                               "stdout_tail", "stderr_tail")} | {"returncode": completed.returncode}


def _no_document(operation: str, completed, context: dict, problem: str | None = None) -> WorkflowProtocolFailedError:
    """The script printed no single document. The usual cause is a failure to
    start: a sibling module the protocol script imports is not in the copied
    set, which Python reports only as a traceback on stderr and exit ``1``
    (the imports run before the script's own error handler)."""
    stderr = completed.stderr.decode("utf-8", errors="replace")
    lines = [line for line in stderr.splitlines() if line.strip()]
    last = lines[-1].strip()[:500] if lines else ""
    match = _MISSING_MODULE_RE.search(last)
    evidence = {"reason": "protocol_no_document", **_run_evidence(context, completed)}
    if match:
        evidence["missing_module"] = match.group(1)
        message = (f"protocol {operation} could not start: the Workflow's protocol script imports the "
                   f"module {match.group(1)!r}, which is not in its managed script set ({last})")
    else:
        tail = stderr[-_STDERR_TAIL_CHARS:]
        message = (f"protocol {operation} exited {completed.returncode} and "
                   f"{problem or 'printed no JSON document'}; stderr: {tail.strip() or '(empty)'}")
    return WorkflowProtocolFailedError(message, evidence=evidence)


def _failed(message: str, *, reason: str, **evidence) -> WorkflowProtocolFailedError:
    return WorkflowProtocolFailedError(message, evidence={"reason": reason, **evidence})


# ---------------------------------------------------------------------------
# The typed operations.
# ---------------------------------------------------------------------------


def _answer(root: Path, operation: str, args: list[str], timeout: float | None) -> Envelope:
    envelope = run(root, operation, args, timeout=timeout)
    if not envelope.ok:
        error = envelope.error
        refusal = {"code": error.code, "message": error.message, "retryable": error.retryable,
                   "native": error.native}
        raise WorkflowProtocolRefusedError(
            f"protocol {operation} was refused ({error.code}): {error.message}",
            evidence={"reason": "protocol_refused", "operation": operation, "refusal": refusal})
    return envelope


def _describe_of(result: dict) -> Describe:
    return Describe(
        workflow_release=result["workflow_release"], protocol_version=result["protocol_version"],
        supported_protocol_majors=tuple(result["supported_protocol_majors"]),
        supported_governing_versions=tuple(result["supported_governing_versions"]),
        capabilities={name: tuple(values) for name, values in result["capabilities"].items()},
    )


def describe(root: Path, *, timeout: float | None = None) -> Describe:
    return _describe_of(_answer(root, "describe", [], timeout).result)


@dataclasses.dataclass(frozen=True)
class Identity:
    """What a target's Workflow is, as the Controller derives it: the
    ``release`` its protocol script reports and the ``digests`` (key to
    sha256) of the script set it ran from. Two identities are equal when
    both are; ``describe`` is the answer they came from and is not compared."""

    release: str
    digests: Mapping[str, str]
    describe: Describe = dataclasses.field(compare=False)


def identity(root: Path, *, timeout: float | None = None) -> Identity:
    """Derive ``root``'s identity afresh: one ``describe`` run from a private
    copy of the script set read now, so the release and the digests describe
    the very bytes that answered. A file outside the installation record's
    ``managed`` map never enters it."""
    envelope = _answer(root, "describe", [], timeout)
    described = _describe_of(envelope.result)
    return Identity(described.workflow_release, dict(envelope.digests), described)


def verify(root: Path, *, timeout: float | None = None) -> Verify:
    result = _answer(root, "verify", [], timeout).result
    return Verify(result["healthy"], tuple(Check(c["id"], c["status"], c["detail"]) for c in result["checks"]))


def next_action(root: Path, work_item: str | None = None, *, expect_state_identity: str | None = None,
                timeout: float | None = None) -> Decision:
    args: list[str] = []
    if work_item is not None:
        args += ["--work-item", work_item]
    if expect_state_identity is not None:
        args += ["--expect-state-identity", expect_state_identity]
    return parse_decision(_answer(root, "next-action", args, timeout).result)


def reconcile(root: Path, decision_file: Path, work_item: str | None = None, *,
              timeout: float | None = None) -> Reconciliation:
    args = ["--decision", str(decision_file)]
    if work_item is not None:
        args += ["--work-item", work_item]
    result = _answer(root, "reconcile", args, timeout).result
    return Reconciliation(
        outcome=result["class"], from_=_phase_identity(result["from"]), to=_phase_identity(result["to"]),
        evidence=dict(result["evidence"]),
        invalid_reasons=tuple((r["code"], r["text"]) for r in result["invalid_reasons"]),
        basis=_basis(result["basis"]), next=result["next"], raw=result,
    )
