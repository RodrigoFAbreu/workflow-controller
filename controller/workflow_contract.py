"""The Workflow contract of each release the Controller knows, and the two
Workflow queries a ``workflow_query`` contract asks
(``docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md``, Design B).

A contract says where two facts come from for a target running that
release: the feedback path, and whether a plan-review bundle is current.
2.5.1 keeps the Controller's own rules. 2.6.0 asks Workflow:

- ``scripts/workflow_fingerprint.py --resolve-feedback-path <id>``
  (:func:`resolve_feedback_path`);
- ``scripts/workflow_state.py --plan-review-publication-status <id>``
  (:func:`plan_review_publication_status`).

Admission stays in ``managed_repo``: :data:`RELEASE_CONTRACTS` may name a
release that is not admitted yet, never the reverse.

**Running a query (invariant I6).** A query executes only the bytes of the
release the contract names, and no other module from the target. Every run
of :func:`_run_query`:

1. reads the target's ``scripts/workflow_state.py`` and
   ``scripts/workflow_fingerprint.py`` once each and checks their sha256
   against the contract's digests. Nothing is executed on a mismatch;
2. writes exactly those bytes into a fresh private temporary directory,
   through ``runtime.write_bytes``, and removes it when the query ends;
3. runs ``sys.executable -B -E -s <private dir>/<script> ...`` with ``cwd``
   the target root, stdin closed and a timeout. The target's ``scripts/``
   directory is never on the query's ``sys.path``, so a planted
   ``scripts/uuid.py`` or ``scripts/__pycache__/*.pyc`` never runs, and
   ``-B`` writes no bytecode anywhere.

Every failure of these steps, and every answer that does not validate, is a
:class:`~controller.errors.WorkflowQueryError`: nothing else leaves the
runner, and nothing is ever repaired or read as the 2.5.1 rule.

**The answers seam** (CP3). Evidence and job code never call the runners
directly: they read a :class:`BoundContract`, which :func:`bind` makes from
a contract, and ask its ``answers`` (``None`` for a contract that runs no
query). Production always binds :class:`QueryAnswers`, the real queries
with a memo that lives only as long as the binding (one decision, one
pre-state capture, or one verification). The only other provider is the
private :data:`_answers_factory` hook, which the decision-golden generators
set to replay recorded answers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping

from controller import runtime
from controller.errors import (
    RuntimeContainmentError,
    UnsupportedWorkflowVersionError,
    WorkflowQueryError,
)

#: ``WorkflowContract.feedback_path_source`` values.
CONTROLLER_RULE = "controller_rule"
#: ``WorkflowContract.plan_review_publication_source`` values (with
#: :data:`WORKFLOW_QUERY`, shared by both fields).
REVISION_COHERENCE = "revision_coherence"
WORKFLOW_QUERY = "workflow_query"

STATE_SCRIPT = "scripts/workflow_state.py"
FINGERPRINT_SCRIPT = "scripts/workflow_fingerprint.py"
#: The two scripts a query needs: ``workflow_state.py`` imports its sibling
#: ``workflow_fingerprint``, and neither imports anything else from the
#: target.
QUERY_SCRIPTS = (STATE_SCRIPT, FINGERPRINT_SCRIPT)

QUERY_TIMEOUT_SECONDS = 120.0

_PRIVATE_DIR_PREFIX = "workflow-controller-query-"
_OUTPUT_TAIL_CHARS = 4000


@dataclasses.dataclass(frozen=True)
class WorkflowContract:
    """What the Controller consumes from one Workflow release.

    ``query_script_sha256`` maps each of :data:`QUERY_SCRIPTS` to the
    Workflow Manager manifest's digest for that release, or is ``None`` for
    a contract that runs no query."""

    release: str
    feedback_path_source: str
    plan_review_publication_source: str
    query_script_sha256: Mapping[str, str] | None


#: One contract per release the Controller has measured. Every member of
#: ``managed_repo.VALIDATED_WORKFLOW_RELEASES`` has one (pinned by a test).
#: A release that runs queries carries its scripts' digests, taken from its
#: Workflow Manager manifest (the vendored ``tests/workflow_releases/
#: <release>/RELEASE.json`` records the same values), so admitting a release
#: also means executing exactly its bytes.
RELEASE_CONTRACTS: Mapping[str, WorkflowContract] = MappingProxyType({
    "2.5.1": WorkflowContract(
        release="2.5.1",
        feedback_path_source=CONTROLLER_RULE,
        plan_review_publication_source=REVISION_COHERENCE,
        query_script_sha256=None,
    ),
    "2.6.0": WorkflowContract(
        release="2.6.0",
        feedback_path_source=WORKFLOW_QUERY,
        plan_review_publication_source=WORKFLOW_QUERY,
        query_script_sha256=MappingProxyType({
            STATE_SCRIPT: "f57b8c36d15a3e7b50085712e74e9337a93196a1411fc279ee6d0a0dcc2f0688",
            FINGERPRINT_SCRIPT: "7e7fd9706e431d4c8676c3fa3b47b5c60e12e17ea90c50484fd3ad0aaf0b6092",
        }),
    ),
})


def contract_for(release: str) -> WorkflowContract:
    """The contract for ``release``. A release with none is refused, never
    looked up as a ``KeyError``."""
    contract = RELEASE_CONTRACTS.get(release)
    if contract is None:
        raise UnsupportedWorkflowVersionError(
            f"the Controller holds no Workflow contract for release {release!r} "
            f"(contracted releases: {sorted(RELEASE_CONTRACTS)})",
            evidence={
                "observed_workflow_version": release,
                "contracted_workflow_releases": sorted(RELEASE_CONTRACTS),
                "reason": "no_workflow_contract",
            },
        )
    return contract


# ---------------------------------------------------------------------------
# The answers.
# ---------------------------------------------------------------------------

FEEDBACK_LAYOUTS = frozenset({"scoped", "legacy-scoped", "legacy-flat"})
_FEEDBACK_PATH_KEYS = frozenset({
    "feedback_dir", "functional_review_path", "layout", "review_feedback_path", "work_item_id",
})

#: Workflow 2.6.0's publication-status table: each row it writes (a string,
#: as ``_row`` writes it) and the one status that row carries. Rows 4d and 6
#: are refusals (:class:`PublicationRefusal`), never a status.
PUBLICATION_STATUS_BY_ROW: Mapping[str, str] = MappingProxyType({
    "1": "NOT_PLAN_STAGE",
    "2": "BOUND",
    "3": "BOUND",
    "4a": "CONTENT_DRIFTED",
    "4b": "BUNDLE_UNVERIFIED",
    "4c": "LEGACY_UNVERIFIED",
    "5": "LEGACY_UNMARKED",
    "7": "NEEDS_EDIT",
    "8": "NEEDS_REVISION",
    "9": "PUBLISHED_UNBOUND",
    "10": "NEEDS_EDIT",
    "11": "EDIT_IN_PROGRESS",
})
PUBLICATION_STATUSES = frozenset(PUBLICATION_STATUS_BY_ROW.values())
_PUBLICATION_KEYS = frozenset({"work_item_id", "phase", "row", "status", "remedy"})
#: The per-row keys, each optional in every row, with the types they may
#: hold. ``fresh_review_content_id`` is absent from row 4c and ``null`` in
#: row 4a when the fresh id is unreadable; ``advisory`` is ``null`` unless a
#: ``bundle_id`` differs from ``current_bundle_id``.
_PUBLICATION_OPTIONAL_KEYS: Mapping[str, tuple[type, ...]] = MappingProxyType({
    "fresh_review_content_id": (str, type(None)),
    "bundle_id": (str,),
    "bundle_verifies": (bool,),
    "advisory": (str, type(None)),
    "detail": (str,),
})
PUBLICATION_REFUSAL_ERROR = "PlanReviewBindingInconsistentError"


@dataclasses.dataclass(frozen=True)
class FeedbackPath:
    """``--resolve-feedback-path``'s validated answer. Every path is
    repo-relative POSIX."""

    work_item_id: str
    layout: str
    feedback_dir: str
    review_feedback_path: str
    functional_review_path: str


@dataclasses.dataclass(frozen=True)
class PublicationStatus:
    """``--plan-review-publication-status``'s validated answer (exit 0). A
    per-row key the answer does not carry is ``None`` here."""

    work_item_id: str
    phase: str
    row: str
    status: str
    remedy: str
    fresh_review_content_id: str | None = None
    bundle_id: str | None = None
    bundle_verifies: bool | None = None
    advisory: str | None = None
    detail: str | None = None


@dataclasses.dataclass(frozen=True)
class PublicationRefusal:
    """Workflow's documented refusal of the status query (rows 4d and 6,
    exit 1): returned, not raised, so a decision can gate with Workflow's
    own ``message``."""

    work_item_id: str
    error: str
    message: str


def resolve_feedback_path(root: Path, contract: WorkflowContract, work_item_id: str) -> FeedbackPath:
    """Ask Workflow where ``work_item_id``'s feedback lives
    (``workflow_fingerprint.py --resolve-feedback-path``). Only exit 0 with
    exactly the documented answer is accepted: the five keys, the id echoed,
    a known layout, and the paths that layout implies."""
    query = "--resolve-feedback-path"
    completed, context = _run_query(root, contract, FINGERPRINT_SCRIPT, [f"{query}={work_item_id}"],
                                    query=query, work_item_id=work_item_id)
    if completed.returncode != 0:
        raise _query_failed(context, completed)
    answer = _json_object(context, completed)
    problem = _feedback_path_problem(answer, work_item_id)
    if problem is not None:
        raise _output_invalid(context, completed, problem)
    return FeedbackPath(
        work_item_id=answer["work_item_id"], layout=answer["layout"], feedback_dir=answer["feedback_dir"],
        review_feedback_path=answer["review_feedback_path"],
        functional_review_path=answer["functional_review_path"],
    )


def plan_review_publication_status(
    root: Path, contract: WorkflowContract, work_item_id: str,
) -> PublicationStatus | PublicationRefusal:
    """Ask Workflow for ``work_item_id``'s plan-review publication status
    (``workflow_state.py --plan-review-publication-status``).

    Exit 0 is validated into a :class:`PublicationStatus`: the five
    always-present keys, a known status paired with its row, the id echoed,
    and only documented per-row keys of the documented types. Exit 1 whose
    stdout is exactly Workflow's binding-inconsistent refusal is a
    :class:`PublicationRefusal`. Everything else raises."""
    query = "--plan-review-publication-status"
    completed, context = _run_query(root, contract, STATE_SCRIPT, [f"{query}={work_item_id}"],
                                    query=query, work_item_id=work_item_id)
    if completed.returncode == 1:
        refusal = _publication_refusal(completed.stdout, work_item_id)
        if refusal is None:
            raise _query_failed(context, completed)
        return refusal
    if completed.returncode != 0:
        raise _query_failed(context, completed)
    answer = _json_object(context, completed)
    problem = _publication_status_problem(answer, work_item_id)
    if problem is not None:
        raise _output_invalid(context, completed, problem)
    return PublicationStatus(**answer)


def _feedback_path_problem(answer: dict, work_item_id: str) -> str | None:
    if set(answer) != _FEEDBACK_PATH_KEYS:
        return f"the answer's keys are {sorted(answer)}, not {sorted(_FEEDBACK_PATH_KEYS)}"
    for key in sorted(answer):
        if not isinstance(answer[key], str):
            return f"{key!r} is {answer[key]!r}, not a string"
    if answer["work_item_id"] != work_item_id:
        return f"work_item_id {answer['work_item_id']!r} does not echo {work_item_id!r}"
    if answer["layout"] not in FEEDBACK_LAYOUTS:
        return f"layout {answer['layout']!r} is not one of {sorted(FEEDBACK_LAYOUTS)}"
    for key in ("feedback_dir", "review_feedback_path", "functional_review_path"):
        problem = _relative_posix_problem(answer[key])
        if problem is not None:
            return f"{key} {answer[key]!r} {problem}"
    expected_dir = (".ai-review/feedback" if answer["layout"] == "legacy-flat"
                    else f".ai-review/{work_item_id}/feedback")
    expected = {
        "feedback_dir": expected_dir,
        "review_feedback_path": f"{expected_dir}/REVIEW_FEEDBACK.md",
        "functional_review_path": f"{expected_dir}/FUNCTIONAL_REVIEW.md",
    }
    for key, value in expected.items():
        if answer[key] != value:
            return f"{key} {answer[key]!r} is not {value!r}, which layout {answer['layout']!r} implies"
    return None


def _relative_posix_problem(path: str) -> str | None:
    """``None`` for a normalised relative POSIX path: no leading ``/``, no
    empty, ``.`` or ``..`` component, no backslash and no NUL."""
    if path.startswith("/"):
        return "is absolute"
    if "\\" in path or "\x00" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        return "is not a normalised relative POSIX path"
    return None


def _publication_status_problem(answer: dict, work_item_id: str) -> str | None:
    missing = _PUBLICATION_KEYS - set(answer)
    if missing:
        return f"the answer lacks {sorted(missing)}"
    unknown = set(answer) - _PUBLICATION_KEYS - set(_PUBLICATION_OPTIONAL_KEYS)
    if unknown:
        return f"the answer carries undocumented keys {sorted(unknown)}"
    for key in sorted(_PUBLICATION_KEYS):
        if not isinstance(answer[key], str):
            return f"{key!r} is {answer[key]!r}, not a string"
    for key, types in _PUBLICATION_OPTIONAL_KEYS.items():
        if key in answer and not isinstance(answer[key], types):
            return f"{key!r} is {answer[key]!r}, not of type {' or '.join(t.__name__ for t in types)}"
    if answer["work_item_id"] != work_item_id:
        return f"work_item_id {answer['work_item_id']!r} does not echo {work_item_id!r}"
    if answer["status"] not in PUBLICATION_STATUSES:
        return f"status {answer['status']!r} is not one of {sorted(PUBLICATION_STATUSES)}"
    if answer["row"] not in PUBLICATION_STATUS_BY_ROW:
        return f"row {answer['row']!r} is not one of {list(PUBLICATION_STATUS_BY_ROW)}"
    if PUBLICATION_STATUS_BY_ROW[answer["row"]] != answer["status"]:
        return (f"row {answer['row']!r} carries status {answer['status']!r}, not "
                f"{PUBLICATION_STATUS_BY_ROW[answer['row']]!r}")
    return None


def _publication_refusal(stdout: bytes, work_item_id: str) -> PublicationRefusal | None:
    """Workflow's documented refusal, or ``None`` for any other exit-1
    output (a traceback prints nothing on stdout)."""
    try:
        answer = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if (not isinstance(answer, dict) or set(answer) != {"error", "message"}
            or answer["error"] != PUBLICATION_REFUSAL_ERROR or not isinstance(answer["message"], str)):
        return None
    return PublicationRefusal(work_item_id=work_item_id, error=answer["error"], message=answer["message"])


# ---------------------------------------------------------------------------
# The answers seam.
# ---------------------------------------------------------------------------


class QueryAnswers:
    """The production answers of a ``workflow_query`` contract: the two
    real queries, each asked at most once per ``(root, work item)`` for as
    long as this object lives. :func:`bind` makes a fresh one per binding,
    so nothing is cached across decisions or verifications. A failed query
    raises and is not remembered."""

    def __init__(self, contract: WorkflowContract) -> None:
        self._contract = contract
        self._feedback: dict[tuple[str, str], FeedbackPath] = {}
        self._status: dict[tuple[str, str], PublicationStatus | PublicationRefusal] = {}

    def feedback_path(self, root: Path, work_item_id: str) -> FeedbackPath:
        key = (str(root), work_item_id)
        if key not in self._feedback:
            self._feedback[key] = resolve_feedback_path(Path(root), self._contract, work_item_id)
        return self._feedback[key]

    def publication_status(self, root: Path, work_item_id: str) -> PublicationStatus | PublicationRefusal:
        key = (str(root), work_item_id)
        if key not in self._status:
            self._status[key] = plan_review_publication_status(Path(root), self._contract, work_item_id)
        return self._status[key]


@dataclasses.dataclass(frozen=True)
class BoundContract:
    """A contract with the answers one decision, pre-state capture or
    verification reads. ``answers`` has ``feedback_path(root, id)`` and
    ``publication_status(root, id)``, and is ``None`` for a contract that
    runs no query."""

    contract: WorkflowContract
    answers: Any

    @property
    def release(self) -> str:
        return self.contract.release


#: Test-only: when set, :func:`bind` builds a ``workflow_query`` contract's
#: answers with this factory instead of :class:`QueryAnswers`. Only the
#: decision-golden generators set it, to replay recorded Workflow answers.
#: No production caller can pass a provider.
_answers_factory: Callable[[WorkflowContract], Any] | None = None


def bind(contract: WorkflowContract) -> BoundContract:
    """``contract`` with fresh answers: :class:`QueryAnswers` when either
    source is :data:`WORKFLOW_QUERY`, else none."""
    if WORKFLOW_QUERY not in (contract.feedback_path_source, contract.plan_review_publication_source):
        return BoundContract(contract=contract, answers=None)
    factory = _answers_factory if _answers_factory is not None else QueryAnswers
    return BoundContract(contract=contract, answers=factory(contract))


def bind_release(release: str) -> BoundContract:
    """``bind(contract_for(release))``: the one way evidence and job code
    turn a release into a binding. An unknown release is
    ``UnsupportedWorkflowVersionError``."""
    return bind(contract_for(release))


# ---------------------------------------------------------------------------
# The runner.
# ---------------------------------------------------------------------------


def _run_query(
    root: Path, contract: WorkflowContract, script: str, args: list[str], *,
    query: str, work_item_id: str, timeout: float = QUERY_TIMEOUT_SECONDS,
) -> tuple[subprocess.CompletedProcess, dict]:
    """Run ``script`` (one of :data:`QUERY_SCRIPTS`) with ``args`` from a
    private copy of the digest-checked bytes, and return the completed
    process (bytes output) with the evidence context for the caller's own
    validation errors. Raises only :class:`WorkflowQueryError`."""
    context = {
        "release": contract.release, "query": query, "work_item_id": work_item_id,
        "cwd": str(root), "argv": None, "returncode": None, "stdout_tail": "", "stderr_tail": "",
    }
    if contract.query_script_sha256 is None:
        raise WorkflowQueryError(
            f"Workflow {contract.release}'s contract runs no query; {query} was not run",
            evidence={**context, "reason": "no_workflow_query"},
        )
    sources = _verified_sources(root, contract, context)
    try:
        scratch = tempfile.TemporaryDirectory(prefix=_PRIVATE_DIR_PREFIX)
    except OSError as exc:
        raise _private_copy_failed(context, "create the private directory", exc) from exc
    try:
        completed = _copy_and_execute(Path(scratch.name), sources, script, args, root=root,
                                      timeout=timeout, context=context)
    except BaseException as exc:
        try:
            scratch.cleanup()
        except OSError as removal:
            if isinstance(exc, WorkflowQueryError):
                exc.evidence["private_dir_removal_error"] = str(removal)
        raise
    try:
        scratch.cleanup()
    except OSError as exc:
        raise _private_copy_failed(context, "remove the private directory", exc) from exc
    return completed, context


def _verified_sources(root: Path, contract: WorkflowContract, context: dict) -> dict[str, bytes]:
    """Step 1: each query script's bytes, read once, whose sha256 is the
    contract's."""
    sources: dict[str, bytes] = {}
    for rel_path in QUERY_SCRIPTS:
        expected = contract.query_script_sha256[rel_path]
        path = root / rel_path
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise OSError(f"{path} is not a regular file")
            data = path.read_bytes()
        except OSError as exc:
            raise WorkflowQueryError(
                f"{context['query']} was not run: the target's {rel_path} cannot be read ({exc})",
                evidence={**context, "reason": "query_script_modified", "path": rel_path,
                          "expected_sha256": expected, "observed_sha256": None, "error": str(exc)},
            ) from exc
        observed = hashlib.sha256(data).hexdigest()
        if observed != expected:
            raise WorkflowQueryError(
                f"{context['query']} was not run: the target's {rel_path} is not Workflow "
                f"{contract.release}'s (sha256 {observed}, expected {expected})",
                evidence={**context, "reason": "query_script_modified", "path": rel_path,
                          "expected_sha256": expected, "observed_sha256": observed},
            )
        sources[rel_path] = data
    return sources


def _copy_and_execute(
    private_dir: Path, sources: dict[str, bytes], script: str, args: list[str], *,
    root: Path, timeout: float, context: dict,
) -> subprocess.CompletedProcess:
    """Steps 2 and 3: write the verified bytes under their own names into
    ``private_dir``, then run ``script`` from there."""
    for rel_path, data in sources.items():
        try:
            runtime.write_bytes(private_dir, PurePosixPath(rel_path).name, data)
        except (OSError, RuntimeContainmentError) as exc:
            raise _private_copy_failed(context, f"write the private copy of {rel_path}", exc) from exc
    argv = [sys.executable, "-B", "-E", "-s", str(private_dir / PurePosixPath(script).name), *args]
    context["argv"] = argv
    try:
        completed = _execute_query(argv, cwd=root, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise WorkflowQueryError(
            f"{context['query']} did not finish within {timeout} s",
            evidence={**context, "reason": "query_timeout", "timeout": timeout,
                      "stdout_tail": _tail(exc.stdout), "stderr_tail": _tail(exc.stderr)},
        ) from exc
    except (OSError, ValueError) as exc:
        # `ValueError`: an argument `subprocess` cannot pass, such as a work
        # item id holding a NUL byte.
        raise WorkflowQueryError(
            f"{context['query']} could not be started: {exc}",
            evidence={**context, "reason": "query_launch_failed", "error": str(exc)},
        ) from exc
    context.update(returncode=completed.returncode, stdout_tail=_tail(completed.stdout),
                   stderr_tail=_tail(completed.stderr))
    return completed


def _execute_query(argv: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    """Step 3, and the private runner hook: a test replaces only this
    function, so the read, the digest check and the private copy still run
    first. Output is captured as bytes; decoding is part of validation."""
    return subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, check=False,
                          timeout=timeout)


def _json_object(context: dict, completed: subprocess.CompletedProcess) -> dict:
    try:
        answer = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _output_invalid(context, completed, f"stdout is not JSON ({exc})") from exc
    if not isinstance(answer, dict):
        raise _output_invalid(context, completed, "stdout is not a JSON object")
    return answer


def _query_failed(context: dict, completed: subprocess.CompletedProcess) -> WorkflowQueryError:
    return WorkflowQueryError(
        f"{context['query']} for {context['work_item_id']!r} exited {completed.returncode}: "
        f"{_last_line(completed.stderr) or _last_line(completed.stdout) or '(no output)'}",
        evidence={**context, "reason": "query_failed"},
    )


def _output_invalid(context: dict, completed: subprocess.CompletedProcess, problem: str) -> WorkflowQueryError:
    return WorkflowQueryError(
        f"{context['query']} for {context['work_item_id']!r} gave an answer outside Workflow "
        f"{context['release']}'s contract: {problem}",
        evidence={**context, "reason": "query_output_invalid", "problem": problem},
    )


def _private_copy_failed(context: dict, action: str, exc: BaseException) -> WorkflowQueryError:
    return WorkflowQueryError(
        f"{context['query']} failed: could not {action} ({exc})",
        evidence={**context, "reason": "query_private_copy_failed", "action": action, "error": str(exc)},
    )


def _last_line(output: bytes) -> str:
    """The last non-blank line: a traceback's exception line."""
    lines = [line for line in _tail(output).splitlines() if line.strip()]
    return lines[-1].strip()[:500] if lines else ""


def _tail(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return output[-_OUTPUT_TAIL_CHARS:]
