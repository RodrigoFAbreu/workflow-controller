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
3. prepares the Git every command of the query runs (:func:`_git_isolation`):
   a private copy of the target's index, so the target is never written,
   no transport, and command-scope configuration that switches off every
   hook, filter driver and fsmonitor, verified to be in force. A filter
   driver with a program is also made to fail wherever Git would run it, so
   a query whose answer depends on a filter fails instead of answering
   differently from Workflow. It refuses, before the query runs, a hook the
   query's Git would fire and an fsmonitor program, whose absence could
   change Workflow's answer, and what it cannot switch off: a hook command
   in the target's own configuration, or a populated submodule;
4. runs ``sys.executable -B -E -s <private dir>/scripts/<script> ...`` with
   ``cwd`` the target root, stdin closed and one timeout for all of it. The
   target's ``scripts/`` directory is never on the query's ``sys.path``, so
   a planted ``scripts/uuid.py`` or ``scripts/__pycache__/*.pyc`` never
   runs, and ``-B`` writes no bytecode anywhere.

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
import os
import stat
import subprocess
import sys
import tempfile
import time
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
#: Inside the private directory: the verified scripts (the query's
#: ``sys.path[0]``, holding nothing else) and the private copy of the
#: target's index.
_PRIVATE_SCRIPTS_DIR = "scripts"
_PRIVATE_INDEX = "index"
_OUTPUT_TAIL_CHARS = 4000

#: Configuration every Git command of a query runs under, as command-scope
#: entries, which take precedence over every configuration file: no hook
#: file runs (the target's ``.git/hooks`` or any ``core.hooksPath``), no
#: fsmonitor hook or daemon starts, a refreshed private index never writes
#: a shared index into the target, and no commit signature is verified.
#: :func:`_git_named_overrides` adds the filter drivers and configured
#: hooks, by name.
_GIT_PINNED_CONFIG: tuple[tuple[str, str], ...] = (
    ("core.hookspath", os.devnull),
    ("core.fsmonitor", ""),
    ("core.splitindex", "false"),
    ("log.showsignature", "false"),
)
#: ``git config --show-scope`` scopes whose files are the operator's. Every
#: other scope (``local``, ``worktree``, and the files they include) is the
#: target's own.
_OPERATOR_GIT_SCOPES = frozenset({"system", "global", "command"})
#: The hook events a query's Git can fire. The 2.6.0 queries run ``git
#: rev-parse``, ``config``, ``ls-files``, ``hash-object`` and ``diff
#: --name-only``, and only the index refresh of ``git diff`` fires a hook.
#: A test installs every hook githooks(5) names and runs Workflow's queries
#: in place to check this.
_QUERY_HOOK_EVENTS = ("post-index-change",)


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
    """Steps 2 to 4: write the verified bytes under their own names into
    ``private_dir``'s scripts directory, prepare the query's Git, then run
    ``script`` from there. ``timeout`` bounds all of it."""
    deadline = time.monotonic() + timeout
    for rel_path, data in sources.items():
        try:
            runtime.write_bytes(private_dir, f"{_PRIVATE_SCRIPTS_DIR}/{PurePosixPath(rel_path).name}", data)
        except (OSError, RuntimeContainmentError) as exc:
            raise _private_copy_failed(context, f"write the private copy of {rel_path}", exc) from exc
    env = _git_isolation(root, private_dir, context=context, timeout=timeout, deadline=deadline)
    argv = [sys.executable, "-B", "-E", "-s",
            str(private_dir / _PRIVATE_SCRIPTS_DIR / PurePosixPath(script).name), *args]
    context["argv"] = argv
    try:
        completed = _execute_query(argv, cwd=root, env=env, timeout=_remaining(deadline))
    except subprocess.TimeoutExpired as exc:
        raise _timed_out(context, timeout, exc) from exc
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


def _execute_query(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
                   ) -> subprocess.CompletedProcess:
    """Step 4, and the private runner hook: a test replaces only this
    function, so the read, the digest check, the private copy and the Git
    preparation still run first. Output is captured as bytes; decoding is
    part of validation."""
    return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                          check=False, timeout=timeout)


def _remaining(deadline: float) -> float:
    """What is left of the query's timeout; never zero, which ``subprocess``
    would read as "no wait" rather than "already late"."""
    return max(deadline - time.monotonic(), 0.001)


def _timed_out(context: dict, timeout: float, exc: subprocess.TimeoutExpired) -> WorkflowQueryError:
    return WorkflowQueryError(
        f"{context['query']} did not finish within {timeout} s",
        evidence={**context, "reason": "query_timeout", "timeout": timeout,
                  "stdout_tail": _tail(exc.stdout), "stderr_tail": _tail(exc.stderr)},
    )


# ---------------------------------------------------------------------------
# Step 3: the query's Git.
# ---------------------------------------------------------------------------


def _git_isolation(root: Path, private_dir: Path, *, context: dict, timeout: float,
                   deadline: float) -> dict[str, str]:
    """The environment the query runs in, which every Git command it starts
    inherits (invariant I6: the Workflow scripts run ``git hash-object``,
    ``git diff --name-only`` and ``git ls-files`` in the target, and Git
    would otherwise run the target's hooks, filters and fsmonitor).

    - ``GIT_INDEX_FILE`` is a private copy of the target's index, so the
      stat refresh ``git diff`` makes never writes the target;
    - ``GIT_ALLOW_PROTOCOL`` is empty: no transport, so no remote helper, SSH
      command or partial-clone fetch;
    - command-scope configuration switches off every hook file, fsmonitor,
      shared-index write and signature check (:data:`_GIT_PINNED_CONFIG`),
      every filter driver and every configured hook, named from the
      configuration Git reads for the target, whichever file defines them.

    A query gives Workflow's answer or none, so a program is switched off
    only where that cannot change the answer. A hook or an fsmonitor
    program may edit the files Workflow hashes, and an fsmonitor decides
    which paths Git re-checks. So, refused with ``query_git_not_isolated``
    before the query runs: a hook the query's Git would fire (an executable
    hook file for one of :data:`_QUERY_HOOK_EVENTS`, or a hook any
    configuration file sets for one), an fsmonitor program, a hook command
    in the target's own configuration (not every Git release that runs one
    lets it be switched off), a populated submodule (its Git reads its own
    configuration), and any setting Git does not apply (Git before 2.31
    ignores ``GIT_CONFIG_COUNT``).

    Switching a filter off changes what ``git hash-object`` answers, and
    Workflow's content identity is that answer. So a driver that has a
    ``clean`` or ``process`` program is also made ``required``
    (:func:`_filters_with_programs`): wherever Git would have run the
    program, it fails instead, and so does the query (``query_failed``).
    Where Git would not have run it, the answer is Workflow's own. The
    drivers are ``context['refused_filters']``.

    Hooks for other events are switched off as well; the query's Git never
    fires them. Git's built-in fsmonitor daemon (``core.fsmonitor`` a
    boolean) is Git's own code and reports what a full stat check finds, so
    switching it off leaves the answer unchanged.

    The configuration is read again under that environment, and each
    setting must be in force as the last, command-scope value."""
    base = {**os.environ, "GIT_ALLOW_PROTOCOL": ""}
    run = _PreflightGit(root, context=context, timeout=timeout, deadline=deadline)
    pinned = _with_git_config(base, _GIT_PINNED_CONFIG, context)
    index = Path(os.fsdecode(run(["rev-parse", "--git-path", "index"], pinned).rstrip(b"\n")))
    _copy_index(root / index, private_dir, context)
    private_index = {"GIT_INDEX_FILE": str(private_dir / _PRIVATE_INDEX)}
    # What Workflow's own Git reads, without the Controller's settings. The
    # commands asked under it (`config`, `rev-parse --git-path`) read no
    # index or working tree, and fire no hook.
    entries = _git_config_entries(run, base)
    _refuse_target_hook_commands(entries, context)
    _refuse_hooks_the_query_fires(root, run, base, entries, context)
    _refuse_fsmonitor_programs(run, base, entries, context)
    _refuse_populated_submodules(root, run, {**pinned, **private_index}, context)
    context["refused_filters"] = _filters_with_programs(entries)
    overrides = _GIT_PINNED_CONFIG + _git_named_overrides(entries, context["refused_filters"])
    env = _with_git_config({**base, **private_index}, overrides, context)
    in_force: dict[str, tuple[str, str | None]] = {}
    for scope, key, value in _git_config_entries(run, env):
        in_force[key] = (scope, value)
    for key, value in overrides:
        if in_force.get(key) != ("command", value):
            raise _not_isolated(
                context, key,
                f"Git does not apply the Controller's {key}={value!r} (it reads "
                f"{in_force.get(key)!r}); Git 2.31 or later is required",
            )
    return env


class _PreflightGit:
    """``git <args>``'s stdout in the target, under ``env``, within what is
    left of the query's timeout. Every failure is a
    :class:`WorkflowQueryError`; :meth:`completed` leaves the exit status to
    the caller."""

    def __init__(self, root: Path, *, context: dict, timeout: float, deadline: float) -> None:
        self._root, self._context, self._timeout, self._deadline = root, context, timeout, deadline

    def completed(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        argv = ["git", *args]
        try:
            return subprocess.run(argv, cwd=self._root, env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, check=False, timeout=_remaining(self._deadline))
        except subprocess.TimeoutExpired as exc:
            error = _timed_out(self._context, self._timeout, exc)
            error.evidence["git_argv"] = argv
            raise error from exc
        except OSError as exc:
            raise _not_isolated(self._context, "git", f"{' '.join(argv)} could not be started: {exc}",
                                git_argv=argv) from exc

    def __call__(self, args: list[str], env: dict[str, str]) -> bytes:
        argv = ["git", *args]
        completed = self.completed(args, env)
        if completed.returncode != 0:
            raise _not_isolated(
                self._context, "git",
                f"{' '.join(argv)} exited {completed.returncode}: "
                f"{_last_line(completed.stderr) or '(no output)'}",
                git_argv=argv,
            )
        return completed.stdout


def _with_git_config(env: dict[str, str], pairs: tuple[tuple[str, str], ...], context: dict) -> dict[str, str]:
    """``env`` with ``pairs`` appended to its ``GIT_CONFIG_COUNT`` entries,
    after any the operator's environment already has."""
    count = env.get("GIT_CONFIG_COUNT", "0")
    if not count.isdecimal():
        raise _not_isolated(context, "GIT_CONFIG_COUNT",
                            f"the environment's GIT_CONFIG_COUNT {count!r} is not a count")
    env = dict(env)
    for index, (key, value) in enumerate(pairs, start=int(count)):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["GIT_CONFIG_COUNT"] = str(int(count) + len(pairs))
    return env


def _copy_index(index: Path, private_dir: Path, context: dict) -> None:
    """Copy the target's index into ``private_dir``, with the index's own
    mtime: Git re-hashes an entry no older than the index file (a "racily
    clean" entry), and a fresh copy would let an edit made in the same
    clock tick as the index pass as unchanged where Workflow's Git sees it.
    A target without an index has none copied: Git reads the missing
    private index as empty, as it reads the target's. ``O_NONBLOCK``: a
    planted FIFO is refused, never waited on."""
    try:
        fd = os.open(index, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _not_isolated(context, "index", f"the target's index {index} cannot be read ({exc})") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            status = os.fstat(handle.fileno())
            if not stat.S_ISREG(status.st_mode):
                raise _not_isolated(context, "index", f"the target's index {index} is not a regular file")
            data = handle.read()
    except OSError as exc:
        raise _not_isolated(context, "index", f"the target's index {index} cannot be read ({exc})") from exc
    try:
        copy = runtime.write_bytes(private_dir, _PRIVATE_INDEX, data)
        os.utime(copy, ns=(status.st_atime_ns, status.st_mtime_ns))
    except (OSError, RuntimeContainmentError) as exc:
        raise _private_copy_failed(context, "write the private copy of the index", exc) from exc


def _git_config_entries(run: _PreflightGit, env: dict[str, str]) -> list[tuple[str, str, str | None]]:
    """``(scope, key, value)`` for every entry Git reads for the target, in
    Git's order (the last of a key is the one in force), includes followed.
    ``value`` is ``None`` for a key written without ``=``."""
    fields = run(["config", "--list", "--show-scope", "--includes", "-z"], env).split(b"\0")
    if fields[-1] == b"":
        fields.pop()
    entries = []
    for scope, item in zip(fields[0::2], fields[1::2]):
        key, newline, value = item.partition(b"\n")
        entries.append((os.fsdecode(scope), os.fsdecode(key), os.fsdecode(value) if newline else None))
    return entries


def _split_git_key(key: str) -> tuple[str, str | None, str]:
    """``(section, subsection, variable)`` of a key as ``git config --list``
    prints it; a subsection may itself hold dots."""
    section, _, rest = key.partition(".")
    subsection, dot, variable = rest.rpartition(".")
    return section, subsection if dot else None, variable


def _filters_with_programs(entries: list[tuple[str, str, str | None]]) -> list[str]:
    """The filter drivers whose ``clean`` or ``process`` in force (the last
    value of the key) is not empty: those Git would run to hash a path whose
    ``filter`` attribute names one. A driver with only a ``smudge`` has
    nothing to run for a query, which never checks out."""
    programs: dict[tuple[str, str], str | None] = {}
    for _scope, key, value in entries:
        section, subsection, variable = _split_git_key(key)
        if section == "filter" and subsection is not None and variable in ("process", "clean"):
            programs[subsection, variable] = value
    return sorted({name for (name, _variable), value in programs.items() if value != ""})


def _git_named_overrides(entries: list[tuple[str, str, str | None]],
                         refused_filters: list[str]) -> tuple[tuple[str, str], ...]:
    """Every filter driver's ``process``, ``clean`` and ``smudge`` set empty,
    which Git reads as no filter, each of ``refused_filters`` made
    ``required``, which Git then fails rather than skip ("clean filter
    '<name>' failed"), and every configured hook, and each event one names,
    disabled."""
    filters: set[str] = set()
    hooks: set[str] = set()
    for _scope, key, value in entries:
        section, subsection, variable = _split_git_key(key)
        if subsection is None:
            continue
        if section == "filter":
            filters.add(subsection)
        elif section == "hook":
            hooks.add(subsection)
            if variable == "event" and value:
                hooks.add(value)
    return (tuple((f"filter.{name}.{variable}", "") for name in sorted(filters)
                  for variable in ("process", "clean", "smudge"))
            + tuple((f"filter.{name}.required", "true") for name in refused_filters)
            + tuple((f"hook.{name}.enabled", "false") for name in sorted(hooks)))


def _refuse_target_hook_commands(entries: list[tuple[str, str, str | None]], context: dict) -> None:
    for scope, key, _value in entries:
        section, subsection, variable = _split_git_key(key)
        if (scope not in _OPERATOR_GIT_SCOPES and section == "hook" and subsection is not None
                and variable == "command"):
            raise _not_isolated(
                context, key,
                f"the target's own Git configuration ({scope}) defines the hook command {key}, "
                f"which the Controller cannot switch off in every Git release",
                scope=scope,
            )


def _refuse_hooks_the_query_fires(root: Path, run: _PreflightGit, env: dict[str, str],
                                  entries: list[tuple[str, str, str | None]], context: dict) -> None:
    """A hook Workflow's Git would run during the query: a hook file for one
    of :data:`_QUERY_HOOK_EVENTS` that Git would execute (``access(X_OK)``,
    as Git decides) in the hooks directory Workflow's Git uses, or a hook any
    configuration file sets for one. A hook may edit the files Workflow
    hashes, so switching it off could change the answer."""
    hooks = Path(os.fsdecode(run(["rev-parse", "--git-path", "hooks"], env).rstrip(b"\n")))
    for event in _QUERY_HOOK_EVENTS:
        path = root / hooks / event
        if os.access(path, os.X_OK):
            raise _not_isolated(
                context, "hook",
                f"the hook {str(path)!r} would run on {event} during the query, and the Controller "
                f"cannot know what it changes",
                event=event, path=str(path),
            )
    for scope, key, value in entries:
        section, subsection, variable = _split_git_key(key)
        if section == "hook" and subsection is not None and variable == "event" and value in _QUERY_HOOK_EVENTS:
            raise _not_isolated(
                context, key,
                f"the Git configuration ({scope}) sets the hook {subsection!r} to run on {value} during "
                f"the query, and the Controller cannot know what it changes",
                scope=scope, event=value,
            )


def _refuse_fsmonitor_programs(run: _PreflightGit, env: dict[str, str],
                               entries: list[tuple[str, str, str | None]], context: dict) -> None:
    """An fsmonitor program Workflow's Git would run: ``core.fsmonitor`` set
    to anything Git does not read as a boolean, or, with it unset, the
    environment's ``GIT_TEST_FSMONITOR``. The program decides which paths
    Git re-checks, and may edit the files Workflow hashes. Git decides
    what is a boolean: ``git config --type=bool`` exits 0 for one and 1 for
    an unset key."""
    completed = run.completed(["config", "--type=bool", "--get", "core.fsmonitor"], env)
    if completed.returncode == 0:
        return
    if completed.returncode == 1:
        program: str | None = env.get("GIT_TEST_FSMONITOR", "")
        if not program:
            return
        source = "the environment's GIT_TEST_FSMONITOR"
    else:
        program = next((value for _scope, key, value in reversed(entries) if key == "core.fsmonitor"), None)
        source = f"core.fsmonitor, which Git reads as no boolean ({_last_line(completed.stderr) or 'no output'}),"
    raise _not_isolated(
        context, "fsmonitor",
        f"{source} names the fsmonitor program {program!r}, which Workflow's Git would run during the "
        f"query, and the Controller cannot know what it reports or changes",
        program=program,
    )


def _refuse_populated_submodules(root: Path, run: _PreflightGit, env: dict[str, str], context: dict) -> None:
    """A gitlink whose directory holds a ``.git``: ``git diff`` would run Git
    inside it, under that repository's own configuration."""
    for record in run(["ls-files", "--stage", "-z"], env).split(b"\0"):
        if not record.startswith(b"160000 "):
            continue
        path = os.fsdecode(record.partition(b"\t")[2])
        if os.path.lexists(root / path / ".git"):
            raise _not_isolated(
                context, "submodule",
                f"the target has a populated submodule at {path!r}, whose own Git configuration "
                f"a query's Git would read",
                path=path,
            )


def _not_isolated(context: dict, facility: str, detail: str, **evidence) -> WorkflowQueryError:
    return WorkflowQueryError(
        f"{context['query']} was not run: its Git cannot be isolated from the target ({detail})",
        evidence={**context, "reason": "query_git_not_isolated", "facility": facility, "detail": detail,
                  **evidence},
    )


def _json_object(context: dict, completed: subprocess.CompletedProcess) -> dict:
    try:
        answer = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _output_invalid(context, completed, f"stdout is not JSON ({exc})") from exc
    if not isinstance(answer, dict):
        raise _output_invalid(context, completed, "stdout is not a JSON object")
    return answer


def _query_failed(context: dict, completed: subprocess.CompletedProcess) -> WorkflowQueryError:
    message = (f"{context['query']} for {context['work_item_id']!r} exited {completed.returncode}: "
               f"{_last_line(completed.stderr) or _last_line(completed.stdout) or '(no output)'}")
    if context.get("refused_filters"):
        drivers = ", ".join(repr(name) for name in context["refused_filters"])
        message += (f" (the query's Git fails, rather than run a filter program, on any path whose "
                    f"filter attribute names {drivers})")
    return WorkflowQueryError(message, evidence={**context, "reason": "query_failed"})


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
