"""Fresh Claude worker abstraction (capability 4,
``docs/ACTIVE_MILESTONE.md``).

``launch(task, *, cwd, permission_mode, timeout, claude_bin=None) ->
WorkerResult`` is the whole entry point: it runs exactly the mechanism
``docs/ACTIVE_MILESTONE.md`` records as proven --

    claude -p "<task>" --output-format json --permission-mode <mode> \\
        < /dev/null

against ``cwd``, waits for it to finish (or times out), and classifies the
result into exactly one of four outcomes. It never inspects, repairs, or
writes Workflow state; it never decides *what* task to run (that is
``controller.decision``); it is a pure launch-and-classify primitive
``controller.job`` (CP6) composes.

See ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP5 -- Fresh Claude
worker abstraction".
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
from pathlib import Path

from controller.errors import UserOnlyCommandError, WorkerLaunchError

#: The four outcomes :func:`launch` classifies a completed (or interrupted)
#: worker run into. Fixed classification order, per the plan's table:
#: ``INTERRUPTED`` is checked first (a timeout, or termination by signal --
#: a negative return code), then ``FAILURE`` (non-zero exit, or exit 0 with
#: parsed ``is_error: true``), then ``AMBIGUOUS`` (exit 0 but stdout is not
#: a single parseable JSON object carrying the fields the Controller
#: needs), and only then ``SUCCESS``.
SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
AMBIGUOUS = "AMBIGUOUS"
INTERRUPTED = "INTERRUPTED"

#: The three Workflow commands whose own procedural text calls a
#: ``workflow_state.validate_..._confirmation`` guard against a live human
#: turn (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "The user-only
#: denylist, derived from the property rather than from a proxy"). CP4's
#: ``controller.decision.derive_user_only_commands`` derives this same
#: three-name set fresh from the installed ``.claude/commands/*.md`` files,
#: by the qualified-literal property. This module keeps its own,
#: independent literal copy so :func:`launch` can refuse a task naming one
#: of these commands without depending on an installed commands directory
#: at call time -- "never fabricate user approval" is enforced twice, by
#: two different mechanisms fed by two different sources, never by one
#: shared code path a single defect could disable in both places at once.
USER_ONLY_COMMANDS: frozenset[str] = frozenset({
    "approve-review",
    "accept-milestone",
    "recover-implementation-provenance",
})

#: Trailing punctuation a command mention can carry in ordinary prose (a
#: backticked or sentence-final mention) -- stripped from a token's tail
#: before the denylist comparison, per the plan's own text model.
_TRAILING_PUNCTUATION = "`'\",.;:)]}"

#: Leading characters a command mention can carry: the ``/`` the plan's
#: text model names explicitly, plus the opening backtick of a backticked
#: mention -- both stripped as a run before the denylist comparison. The
#: plan's own worked example (a backticked `` `/approve-review` `` mention
#: recognised the same as the bare token) requires stripping the opening
#: backtick as well as the slash: a token that begins with a literal
#: backtick immediately followed by ``/`` has no leading ``/`` to strip
#: until the backtick is stripped first.
_LEADING_MARKERS = "`/"

#: The two required fields a parsed worker-stdout JSON object must carry
#: for its result to be decidable at all (``docs/ACTIVE_MILESTONE.md``'s
#: own field list; every other field is optional and defaults to
#: ``None`` when absent).
_REQUIRED_FIELDS = ("session_id", "is_error")

#: The remaining fields ``docs/ACTIVE_MILESTONE.md`` names as what the
#: Controller needs from a worker's JSON result, preserved verbatim (as
#: ``None`` when absent) on every :class:`WorkerResult`.
_OPTIONAL_FIELDS = (
    "subtype", "terminal_reason", "stop_reason", "result", "num_turns",
    "permission_denials", "total_cost_usd", "duration_ms",
)


def _task_tokens(task: str) -> list[str]:
    """Split ``task`` on whitespace runs into tokens, each with a leading
    ``/`` and any run of trailing punctuation from ``_TRAILING_PUNCTUATION``
    stripped -- the plan's own text model (revision 47), so a backticked
    `` `/approve-review` `` or a sentence-final ``/approve-review.`` is
    recognised the same as the bare token ``approve-review``."""
    tokens = []
    for raw in task.split():
        token = raw.lstrip(_LEADING_MARKERS)
        token = token.rstrip(_TRAILING_PUNCTUATION)
        tokens.append(token)
    return tokens


def _assert_not_user_only(task: str) -> None:
    """Raise :class:`~controller.errors.UserOnlyCommandError` if any
    whitespace-delimited, punctuation-stripped token of ``task`` equals
    (case-sensitively, whole-token) one of :data:`USER_ONLY_COMMANDS`'s
    three bare names. A whole-token equality, never a substring test --
    a task that merely contains one of the three names' letters as part
    of a longer word is not refused."""
    for token in _task_tokens(task):
        if token in USER_ONLY_COMMANDS:
            raise UserOnlyCommandError(
                f"refusing to launch a worker with a task naming the user-only "
                f"command {token!r} -- {token} requires a live human confirmation "
                f"turn no fresh worker can supply",
                evidence={"task": task, "matched_token": token},
            )


@dataclasses.dataclass(frozen=True)
class WorkerResult:
    """The classified outcome of one ``launch()`` call.

    ``outcome`` is one of :data:`SUCCESS`/:data:`FAILURE`/
    :data:`AMBIGUOUS`/:data:`INTERRUPTED`. ``returncode`` is the worker
    process's own exit status (negative when it was terminated by a
    signal, including the Controller's own timeout-driven ``SIGKILL``).
    ``stdout``/``stderr`` are the worker's raw, undecoded-error-tolerant
    text streams, preserved verbatim so a human can read exactly what the
    worker said -- a later checkpoint (CP6) is what writes them to
    ``.controller/jobs/<job_id>/worker.std{out,err}``, this module never
    writes any file itself. ``raw_json`` is the full parsed JSON result
    object when one was decidable (``None`` otherwise); the eight named
    fields below are that same object's own fields, extracted for
    convenience and ``None`` wherever the object was undecidable or the
    field was absent.
    """

    outcome: str
    returncode: int | None
    session_id: str | None
    is_error: bool | None
    subtype: str | None
    terminal_reason: str | None
    stop_reason: str | None
    result: str | None
    num_turns: int | None
    permission_denials: object | None
    total_cost_usd: float | None
    duration_ms: int | None
    stdout: str
    stderr: str
    raw_json: dict | None


def _parse_worker_stdout(stdout: str) -> dict | None:
    """``REQ-26``: delimit the worker-stdout JSON classification's
    candidate span over the worker's own byte stream, rather than leaving
    it implicit.

    - ``stdout`` reaches this function already decoded with
      ``errors="replace"`` (:func:`launch`'s own
      ``subprocess.Popen(..., text=True, errors="replace")``), so a
      non-UTF-8 byte in the worker's output degrades to the U+FFFD
      replacement character rather than raising -- it then simply fails
      to parse as JSON below, exactly like any other malformed byte
      content, rather than crashing the Controller mid-classification.
    - the candidate span is ``stdout`` with **exactly one** trailing
      newline (``\\r\\n`` or ``\\n``) stripped -- ``--output-format json``
      emits its JSON body as a single terminated line, and this is the
      only trim this function performs. It is never a general
      ``.strip()``: leading or embedded whitespace inside a malformed
      body is preserved and still fails to parse, rather than being
      trimmed into something that accidentally does.
    - the whole candidate span is parsed as **exactly one** JSON
      document. Python's own ``json.loads`` already refuses trailing
      "extra data" after a complete value, so two concatenated JSON
      documents (or one valid document followed by trailing prose) fail
      to parse as a single candidate and fall through to ``None`` here --
      classified :data:`AMBIGUOUS` by the caller -- rather than either
      document being silently selected.

    Returns the parsed object only when it is a JSON *object* (a Python
    ``dict``); any other JSON value (a list, string, number, boolean or
    ``null``) is not the shape the Controller needs and also returns
    ``None``.
    """
    if stdout.endswith("\r\n"):
        candidate = stdout[:-2]
    elif stdout.endswith("\n"):
        candidate = stdout[:-1]
    else:
        candidate = stdout
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _classify(returncode: int | None, parsed: dict | None) -> str:
    """The fixed classification order the plan's table states, applied to
    an already-completed (or already-reaped-after-timeout) process."""
    if returncode is None or returncode < 0:
        return INTERRUPTED
    if returncode != 0:
        return FAILURE
    if parsed is None or not all(field in parsed for field in _REQUIRED_FIELDS):
        return AMBIGUOUS
    if parsed["is_error"]:
        return FAILURE
    return SUCCESS


def _extract_fields(parsed: dict | None) -> dict:
    if parsed is None:
        return {field: None for field in (*_REQUIRED_FIELDS, *_OPTIONAL_FIELDS)}
    return {field: parsed.get(field) for field in (*_REQUIRED_FIELDS, *_OPTIONAL_FIELDS)}


def _kill_process_group(pid: int) -> None:
    """``os.killpg`` the whole process group ``pid`` started
    (``start_new_session=True`` makes the worker its own process-group
    leader), so a timeout tears down every subprocess the worker itself
    spawned rather than leaving orphans holding the target repository.
    Tolerates the group already being gone."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def launch(
    task: str,
    *,
    cwd: str | Path,
    permission_mode: str,
    timeout: float,
    claude_bin: str | None = None,
) -> WorkerResult:
    """Launch one fresh, bounded ``claude -p`` worker against ``cwd`` and
    wait synchronously for it to finish.

    ``permission_mode`` has no default: ``acceptEdits`` against a real
    development repository, ``bypassPermissions`` only against a
    disposable throwaway repository -- the caller states its posture
    explicitly every time.

    Raises :class:`~controller.errors.UserOnlyCommandError` before
    spawning anything if ``task`` names one of :data:`USER_ONLY_COMMANDS`
    (the second denylist layer -- CP4's ``decision.decide`` already never
    *selects* such a command; this makes it impossible to *execute* one
    even through a hand-written task string). Raises
    :class:`~controller.errors.WorkerLaunchError` if the worker process
    itself could not be started at all (an ``OSError`` from
    ``subprocess.Popen``, e.g. ``claude_bin`` does not exist). Every other
    outcome -- including a non-zero exit, a malformed result, or a
    timeout/signal interruption -- is not a raised exception but the
    returned :class:`WorkerResult`'s own ``outcome``.
    """
    _assert_not_user_only(task)

    resolved_claude_bin = claude_bin or "claude"
    args = [
        resolved_claude_bin, "-p", task, "--output-format", "json",
        "--permission-mode", permission_mode,
    ]

    # The worker's environment is the Controller's own, with PYTHONPATH
    # removed (revision 35, local round 34's OPUS-R34-O1): CP1's re-exec
    # assigns PYTHONPATH the immutable-snapshot directory, and this
    # subprocess otherwise inherits it -- without this, every worker (and
    # everything it shells out to inside the target repository) would run
    # with the Controller's own code first on sys.path.
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
            env=env,
        )
    except OSError as exc:
        raise WorkerLaunchError(
            f"could not launch the Claude worker ({resolved_claude_bin!r}): {exc}",
            evidence={"claude_bin": resolved_claude_bin, "os_error": str(exc)},
        ) from exc

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # `communicate()`'s own timeout handling never kills the child (see
        # its docstring's recommended usage) -- and killing only the direct
        # child would leave a process-group sibling the worker itself
        # spawned still running and holding the target repository, so the
        # whole group is torn down before reaping.
        _kill_process_group(proc.pid)
        stdout, stderr = proc.communicate()

    returncode = proc.returncode
    parsed = _parse_worker_stdout(stdout)
    outcome = _classify(returncode, parsed)
    fields = _extract_fields(parsed)

    return WorkerResult(
        outcome=outcome,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        raw_json=parsed,
        **fields,
    )
