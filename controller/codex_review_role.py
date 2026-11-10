"""The read-only run of a local review role in a Codex clone (``workflow-controller-h4e2``, CP2, D2 to D5).

:func:`run_review` runs one of the two local review roles (``LOCAL_MODEL_PLAN_REVIEW``,
``LOCAL_MODEL_IMPLEMENTATION_REVIEW``) as a Codex worker in H4e's private clone, on a copy of what the in-place
reviewer reads (the bound bundle, the verified changes file and the previous feedback), and hands the verdict it
returns to :mod:`controller.review_recorder`. The role returns a verdict *body*; this module composes the header from
the recorder's ``check`` values, so the vendor, model, round and identity are the Controller's facts, never the
role's claim. Nothing is landed and nothing is copied back from the clone. Every path ends in one :class:`Ending`
and the clone is removed on each.

:mod:`controller.job` calls :func:`run_review` for a local review role routed to ``codex`` (H4e2b, D3) and
:func:`recover` at every recovery site of a record holding the ``codex_review`` block (D4); this module does not import
``codex_worker_landing``.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import (codex_worker, codex_worker_sandbox, codex_worker_tree, gitrepo, protocol, review, review_recorder,
               runtime, worker)
from .errors import (
    ControllerError,
    WorkflowProtocolFailedError,
    WorkflowQueryError,
    WorkflowReleaseChangedError,
)

#: D4: one cause per refusal.
CAUSES = (
    "special_file", "submodule", "credential_input", "path_escape", "input_over_bound", "verdict_over_bound",
    "verdict_unparsed", "credential_in_verdict", "read_only_violated",
)

#: The bounds of D4.
MAX_INPUT_PATHS = 2000
MAX_INPUT_BYTES = 64 << 20
MAX_INPUT_FILE_BYTES = 8 << 20
MAX_VERDICT_BYTES = 1 << 20

#: D5's ending codes.
NOT_STARTED = "CodexReviewNotStarted"
WORKER_FAILED = "CodexReviewWorkerFailed"
REFUSED = "CodexReviewRefused"
STATE_IDENTITY_CHANGED = "ReviewStateIdentityChanged"
RELEASE_CHANGED = "workflow_release_changed"
RECORD_REFUSED = "CodexReviewRecordRefused"
RECORD_PENDING = "CodexReviewRecordPending"
RECORDED = "CodexReviewRecorded"
NOT_RECORDED = "CodexReviewNotRecorded"
RECORD_INTERRUPTED = "CodexReviewRecordInterrupted"
#: The codes a job's ``abandon`` and a drifted resume write today (``controller/job.py``'s
#: ``OPERATOR_ABANDONED_CODE`` and ``WORKFLOW_RELEASE_CHANGED_REASON``); a test pins them to those.
OPERATOR_ABANDONED = "OperatorAbandoned"

#: The ``codex_review`` block's stages, in order, and the recovery paths.
STAGES_IN_ORDER = ("preparing", "recording", "recorded")
RECOVERY_PATHS = ("resume", "abandon", "drift")

#: The slash command each stage's launch names (parsed, then replaced by the local prompt).
COMMANDS = {"plan": "/review-plan", "implementation": "/review-implementation"}

#: A private-key armour line: five dashes, ``BEGIN``, optional upper-case key-type words, ``PRIVATE KEY``, five
#: dashes, as one token. Prose naming the markers separately does not match.
ARMOUR = re.compile(rb"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----")

_CREDENTIAL_NAMES = frozenset({"auth.json", ".env", "id_rsa", "id_ed25519", ".claude.json", ".credentials.json"})
_CREDENTIAL_DIRS = frozenset({".ssh", ".aws", ".codex"})


class InputRefused(ControllerError):
    """An input path the copy refuses (D4); ``cause`` is one of :data:`CAUSES`."""

    code = "CodexReviewInputRefused"

    def __init__(self, cause: str, message: str, **evidence: Any) -> None:
        super().__init__(message, evidence={**evidence, "cause": cause})
        self.cause = cause


class CodexReviewRecoveryError(ControllerError):
    """:func:`recover` was given a block stage or a path it does not know."""

    code = "CodexReviewRecoveryError"


@dataclasses.dataclass(frozen=True)
class Ending:
    """How a run ended: ``code`` (D5), ``cause`` (the reason within the code, or ``None``), the ``worker`` result
    when a Codex turn ran, and the ``evidence``."""

    code: str
    cause: str | None
    worker: worker.WorkerResult | None
    evidence: Mapping[str, Any]


# ---------------------------------------------------------------------------
# The clone's removal
# ---------------------------------------------------------------------------


def remove_clone(runtime_root: Path, job_id: str) -> str | None:
    """Remove the job's clone (``runtime.remove_tree`` of the layout's ``tree``). Never raises: a failure is returned
    as its text, for the evidence's ``clone_removal_error``."""
    layout = codex_worker_sandbox.WorkerLayout(Path(runtime_root), job_id)
    try:
        runtime.remove_tree(layout.runtime_root, layout.rel("tree"))
    except Exception as exc:  # noqa: BLE001 -- a removal failure is named, never raised
        return f"{type(exc).__name__}: {exc}"
    return None


# ---------------------------------------------------------------------------
# The inputs (D2, D4)
# ---------------------------------------------------------------------------


def _is_credential_name(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    if name in _CREDENTIAL_NAMES or name.startswith(".env.") or name.endswith((".pem", ".key")):
        return True
    if any(part in _CREDENTIAL_DIRS for part in parts[:-1]):
        return True
    return len(parts) >= 2 and parts[-2] == ".claude" and name.startswith("settings") and name.endswith(".json")


def _check_path(rel: str, root: Path, runtime_root: Path) -> None:
    """The path checks of D4 that need no content, in order."""
    parts = rel.split("/")
    if rel.startswith("/") or ".." in parts or not rel:
        raise InputRefused("path_escape", f"the input path {rel!r} is absolute or has a '..' component", path=rel)
    resolved = (root / rel).parent.resolve() / parts[-1]
    runtime_real = Path(runtime_root).resolve()
    if root.resolve() not in (resolved, *resolved.parents) or runtime_real in (resolved, *resolved.parents):
        raise InputRefused("path_escape", f"the input path {rel!r} resolves outside the repository or into the "
                           "Controller's runtime root", path=rel)
    if ".git" in parts or ".gitmodules" in parts:
        raise InputRefused("submodule", f"the input path {rel!r} is a Git metadata path", path=rel)
    if _is_credential_name(rel):
        raise InputRefused("credential_input", f"the input path {rel!r} is named like a credential", path=rel)


def _walk(root: Path, rel: str, found: list[tuple[str, Path, int]], runtime_root: Path) -> None:
    """Add the regular files under (or at) repository path ``rel`` to ``found``, refusing as D4 says."""
    _check_path(rel, root, runtime_root)
    path = root / rel
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise InputRefused("special_file", f"the input {rel!r} cannot be read: {exc}", path=rel) from exc
    if stat.S_ISREG(mode):
        found.append((rel, path, stat.S_IMODE(mode)))
        return
    if not stat.S_ISDIR(mode):
        raise InputRefused("special_file", f"the input {rel!r} is not a regular file or a directory", path=rel)
    with os.scandir(path) as scan:
        names = sorted(entry.name for entry in scan)
    for name in names:
        _walk(root, f"{rel}/{name}", found, runtime_root)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_inputs(root: Path, runtime_root: Path, paths: list[str]) -> list[tuple[str, bytes, int]]:
    """Every input file as ``(path, content, mode)``, read once, after D4's checks (name, type, bounds and the
    private-key armour line). Raises :class:`InputRefused`."""
    found: list[tuple[str, Path, int]] = []
    for rel in paths:
        _walk(root, rel, found, runtime_root)
    if len(found) > MAX_INPUT_PATHS:
        raise InputRefused("input_over_bound", f"{len(found)} input paths, over {MAX_INPUT_PATHS}", count=len(found))
    total = 0
    loaded: list[tuple[str, bytes, int]] = []
    for rel, path, mode in found:
        size = os.lstat(path).st_size
        if size > MAX_INPUT_FILE_BYTES:
            raise InputRefused("input_over_bound", f"the input {rel!r} is {size} bytes, over {MAX_INPUT_FILE_BYTES}",
                               path=rel, size=size)
        total += size
        if total > MAX_INPUT_BYTES:
            raise InputRefused("input_over_bound", f"the inputs total over {MAX_INPUT_BYTES} bytes", total=total)
        data = path.read_bytes()
        if ARMOUR.search(data):
            raise InputRefused("credential_input", f"the input {rel!r} holds a private-key armour line", path=rel)
        loaded.append((rel, data, mode))
    return loaded


def copy_inputs(runtime_root: Path, clone_rel: str, loaded: list[tuple[str, bytes, int]]) -> dict[str, dict]:
    """Copy ``loaded`` into the clone at their repository-relative paths and return the input manifest
    (``{path: {"mode", "sha256"}}``)."""
    manifest: dict[str, dict] = {}
    for rel, data, mode in loaded:
        target = runtime.write_bytes(runtime_root, f"{clone_rel}/{rel}", data)
        target.chmod(mode)
        manifest[rel] = {"mode": f"{mode:o}", "sha256": _sha256(data)}
    return manifest


def input_paths(root: Path, work_item_id: str, stage: str, digests: Mapping[str, str]) -> tuple[list[str], str, str]:
    """``(paths, bundle_path, changes_path)``: the bound bundle directory, the verified changes file when there is one
    and the previous feedback file when it exists, all repository-relative. ``changes_path`` is ``""`` for none."""
    bundle = protocol.resolve_artifact(root, work_item_id, "review_bundle", expected_digests=digests)
    feedback = protocol.resolve_artifact(root, work_item_id, "review_feedback", expected_digests=digests)
    manifest_path = root / bundle.path / "MANIFEST.md" if bundle.path else None
    if manifest_path is None or not manifest_path.is_file():
        raise InputRefused("special_file", f"the review bundle of {work_item_id} has no MANIFEST.md at "
                           f"{bundle.path!r}", path=bundle.path or "")
    manifest = review.parse_manifest(manifest_path.read_text("utf-8"))
    changes = review.changes_file_for(root, bundle.path, manifest, review.LOCAL_REVIEWER_ROLES[stage]) or ""
    paths = [bundle.path]
    if changes:
        paths.append(changes)
    if feedback.path and feedback.exists:
        paths.append(feedback.path)
    return paths, bundle.path, changes


# ---------------------------------------------------------------------------
# The verdict (D3, D4)
# ---------------------------------------------------------------------------


def _armour_in(*texts: str | bytes | None) -> bool:
    for text in texts:
        if not text:
            continue
        data = text if isinstance(text, bytes) else text.encode("utf-8", "replace")
        if ARMOUR.search(data):
            return True
    return False


def _worker_output_holds_armour(raw: bytes, result: worker.WorkerResult) -> bool:
    """D4's first check: whether the final message or any of the result's text holds an armour line."""
    return _armour_in(raw, result.result, result.stdout, result.stderr)


REVIEW_SCOPES = ("changes", "full")


def parse_verdict(data: bytes) -> tuple[str, str | None, str]:
    """``(status, review_scope, body)`` of the final message; a ``ValueError`` is the cause ``verdict_unparsed``.
    Before the first ``## `` line only the ``Status:`` line, one optional ``Review scope:`` line and blank lines are
    allowed. The body is the message's own text from that line on: a verdict the Workflow would refuse is refused,
    never repaired (D3). Every section of ``review.LOCAL_VERDICT_SECTIONS`` must be present as a heading line, and a
    ``Review scope:`` must be ``changes`` or ``full``."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"the final message is not valid UTF-8 ({exc})") from None
    lines = text.splitlines(keepends=True)
    first = next((n for n, line in enumerate(lines) if line.startswith("## ")), len(lines))
    status: str | None = None
    scope: str | None = None
    for line in lines[:first]:
        if not line.strip():
            continue
        if line.startswith("Status:") and status is None:
            status = line[len("Status:"):].strip()
        elif line.startswith("Review scope:") and scope is None:
            scope = line[len("Review scope:"):].strip()
        else:
            raise ValueError(f"the final message has {line[:60]!r} before its first section")
    if status not in review.STATUSES:
        raise ValueError(f"the final message's Status is {status!r}, not one of {', '.join(review.STATUSES)}")
    if scope is not None and scope not in REVIEW_SCOPES:
        raise ValueError(f"the final message's Review scope is {scope!r}, not one of {', '.join(REVIEW_SCOPES)}")
    headings = {line.rstrip("\r\n") for line in lines[first:] if line.startswith("## ")}
    missing = [name for name in review.LOCAL_VERDICT_SECTIONS if f"## {name}" not in headings]
    if missing:
        raise ValueError("the final message lacks the section(s) " + ", ".join(f"`## {name}`" for name in missing))
    return status, scope, "".join(lines[first:]).rstrip("\n") + "\n"


def compose_verdict(values: Mapping[str, Any], status: str, scope: str | None, body: str, *, model: str,
                    completed_at: str) -> str:
    """The verdict file: the header from the recorder's ``check`` values (D3), then the role's body unchanged.
    ``Reviewer model:`` is added exactly when the gate context says ``requires_distinct``."""
    lines = ["# Review Decision", "", f"Status: {status}", f"Reviewed bundle ID: {values['bundle_id']}",
             f"Reviewed base commit: {values['base_commit']}", f"Work item: {values['work_item_id']}",
             f"Reviewed review_content_id: {values['review_content_id']}",
             f"{values['revision_label']} {values['revision']}", f"Reviewer role: {values['reviewer_role']}"]
    if (values.get("gate") or {}).get("requires_distinct"):
        lines.append(f"Reviewer model: openai/{model}")
    lines.append(f"Review round: {values['round']}")
    if scope:
        lines.append(f"Review scope: {scope}")
    lines += [f"Completed at: {completed_at}", ""]
    return "\n".join(lines) + "\n" + body


# ---------------------------------------------------------------------------
# The after-check (D2)
# ---------------------------------------------------------------------------


def _clone_git(clone: Path, runner: Any, *args: str) -> str:
    result = runner(["git", "-C", str(clone), *args])
    if result.returncode != 0:
        raise OSError(f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout.decode("utf-8", "replace")


def after_check(prepared: codex_worker_tree.Prepared, layout: codex_worker_sandbox.WorkerLayout,
                manifest: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Why the clone is not as the run left it, as one entry per way; empty for unchanged (D2)."""
    clone = prepared.path
    problems: list[str] = []
    runner = codex_worker_tree.scrubbed_runner(layout.base)
    try:
        # The clone's own index is the worker's to write (it can set ``--skip-worktree``/``--assume-unchanged`` or
        # rewrite an entry's object id), so the tree is compared with the base commit through a fresh index the worker
        # never touched, kept outside the clone: nothing it holds can hide a tracked edit.
        with tempfile.TemporaryDirectory(dir=layout.base) as scratch:
            env = {**review._git_env(layout.base), "GIT_INDEX_FILE": str(Path(scratch) / "index")}
            fresh = gitrepo.subprocess_runner(env)
            _clone_git(clone, fresh, "read-tree", prepared.base)
            listing = _clone_git(clone, fresh, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        for record in (r for r in listing.split("\0") if r):
            path = record[3:]
            if not (path == ".ai-review" or path.startswith(".ai-review/")):
                problems.append(f"changed or new path {path}")
        refs = _clone_git(clone, runner, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
        if refs != [f"refs/heads/{prepared.branch} {prepared.base}"]:
            problems.append(f"refs are {refs}")
        head = _clone_git(clone, runner, "symbolic-ref", "-q", "HEAD").strip()
        if head != f"refs/heads/{prepared.branch}":
            problems.append(f"HEAD is {head}")
    except OSError as exc:
        problems.append(str(exc))
    # An input is read only while it can still be the file the manifest names, and a failure to read it is a change:
    # the run left it, so whatever the worker did to it is D2's refusal, never an exception or an unbounded read.
    seen: set[str] = set()
    base = clone / ".ai-review"
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        for name in dirnames:
            if stat.S_ISLNK(os.lstat(Path(dirpath) / name).st_mode):
                problems.append(f"symlink {(Path(dirpath) / name).relative_to(clone).as_posix()}")
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(clone).as_posix()
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                problems.append(f"non-regular {rel}")
                continue
            seen.add(rel)
            if rel not in manifest:
                continue
            if info.st_size > MAX_INPUT_FILE_BYTES:
                problems.append(f"input {rel} changed")
                continue
            try:
                digest = _sha256(path.read_bytes())
            except OSError as exc:
                problems.append(f"input {rel} cannot be read: {exc}")
                continue
            if manifest[rel]["sha256"] != digest:
                problems.append(f"input {rel} changed")
    if seen != set(manifest):
        problems.append(f".ai-review holds {sorted(seen ^ set(manifest))} beyond or short of the inputs")
    return problems


# ---------------------------------------------------------------------------
# The run (D5)
# ---------------------------------------------------------------------------


def _utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emptied(result: worker.WorkerResult) -> worker.WorkerResult:
    """``result`` with every field that could hold the armoured text emptied (D4, ``credential_in_verdict``)."""
    diagnosis = dict(result.stream_diagnosis or {})
    diagnosis["detail"] = ""
    fields = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
    return worker.WorkerResult(**{**fields, "result": "", "stdout": "", "stderr": "", "stream_diagnosis": diagnosis})


def without_message(result: worker.WorkerResult) -> worker.WorkerResult:
    """``result`` with its final message (the verdict) emptied, for the job record's ``worker`` block (review I1)."""
    fields = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
    return worker.WorkerResult(**{**fields, "result": ""})


def without_message_streams(result: worker.WorkerResult) -> tuple[str, str]:
    """``(stdout, stderr)`` of a routed review with the final message (the verdict) kept out (review round 3, I1).
    Non-``--json`` ``codex exec`` prints the final message alone on stdout and repeats it in the stderr transcript, so
    stdout is emptied and every stderr line that is a line of the message (compared stripped) is dropped; the rest of
    the transcript, the banner and the warnings stay as the diagnostic."""
    message_lines = {line.strip() for text in (result.result or "", result.stdout or "") for line in text.splitlines()}
    message_lines.discard("")
    kept = [line for line in (result.stderr or "").splitlines(keepends=True) if line.strip() not in message_lines]
    return "", "".join(kept)


def _recorded_block(code: str, recorded: Mapping[str, Any]) -> dict:
    return {"code": code, **recorded}


def run_review(managed_repo: Any, *, runtime_root: Path, job_id: str, operator_home: Path, stage: str,
               work_item_id: str, base: str, model: str, effort: str, state_identity: str, timeout: float,
               brief: str | None = None, on_progress: Callable[[dict], None] | None = None,
               launch: Callable[..., worker.WorkerResult] | None = None,
               launch_options: Mapping[str, Any] | None = None,
               clock: Callable[[], str] = _utc_stamp) -> Ending:
    """Run the ``stage``'s local review role read-only in a clone and record its verdict (D2 to D5). Returns the
    run's :class:`Ending`; an exception from any step propagates after the clone is removed."""
    if stage not in review_recorder.STAGES:
        raise ValueError(f"stage must be one of {review_recorder.STAGES}, not {stage!r}")
    runtime_root = Path(runtime_root)
    try:
        ending = _run(managed_repo, runtime_root=runtime_root, job_id=job_id, operator_home=Path(operator_home),
                      stage=stage, work_item_id=work_item_id, base=base, model=model, effort=effort,
                      state_identity=state_identity, timeout=timeout, brief=brief,
                      progress=on_progress or (lambda block: None), launch=launch or codex_worker.launch_codex_worker,
                      launch_options=dict(launch_options or {}), clock=clock)
    finally:
        removal_error = remove_clone(runtime_root, job_id)
    if removal_error:
        ending = Ending(ending.code, ending.cause, ending.worker,
                        {**ending.evidence, "clone_removal_error": removal_error})
    return ending


def _not_started(cause: str, message: str, **evidence: Any) -> Ending:
    return Ending(NOT_STARTED, cause, None, {"message": message, **evidence})


def _run(managed_repo: Any, *, runtime_root: Path, job_id: str, operator_home: Path, stage: str, work_item_id: str,
         base: str, model: str, effort: str, state_identity: str, timeout: float, brief: str | None,
         progress: Callable[[dict], None], launch: Callable[..., worker.WorkerResult],
         launch_options: dict, clock: Callable[[], str]) -> Ending:
    root = Path(managed_repo.root)
    layout = codex_worker_sandbox.WorkerLayout(runtime_root, job_id)
    progress({"read_only": True, "stage": "preparing"})
    try:
        checked = review_recorder.check(root, stage, work_item_id)
    except (WorkflowQueryError, WorkflowProtocolFailedError) as exc:
        return _not_started("check", exc.message, reason=exc.evidence.get("reason"), error=type(exc).__name__)
    if not checked.ok:
        return _not_started(checked.cause or "workflow_refused", checked.message or "",
                            workflow_class=checked.workflow_class)
    values = checked.values
    try:
        paths, bundle_path, changes_path = input_paths(root, work_item_id, stage, values["digests"])
        loaded = collect_inputs(root, runtime_root, paths)
        prepared = codex_worker_tree.prepare(managed_repo, layout, base, read_only=True)
    except InputRefused as exc:
        return _not_started(exc.cause, exc.message, **{k: v for k, v in exc.evidence.items() if k != "cause"})
    except codex_worker_tree.CodexTreeError as exc:
        return _not_started(exc.cause, exc.message)
    except WorkflowReleaseChangedError as exc:
        return _not_started(RELEASE_CHANGED, exc.message, error=type(exc).__name__)
    except (WorkflowQueryError, WorkflowProtocolFailedError) as exc:
        return _not_started("check", exc.message, reason=exc.evidence.get("reason"), error=type(exc).__name__)
    dirty = {"managed_dirty": prepared.dirty} if prepared.dirty and any(prepared.dirty.values()) else {}
    manifest = copy_inputs(runtime_root, layout.rel("tree"), loaded)
    prompt = review.build_local_prompt(stage, bundle_dir=bundle_path, brief=brief, changes_path=changes_path or None,
                                       local_round=int(values["round"]))
    spawned: list[int] = []

    def on_spawn(pid: int, pgid: int) -> None:
        spawned.append(pid)
        callback = launch_options.get("on_spawn")
        if callback is not None:
            callback(pid, pgid)

    options = {**launch_options, "on_spawn": on_spawn}
    try:
        result = launch(COMMANDS[stage], runtime_root=runtime_root, job_id=job_id, operator_home=operator_home,
                        cwd=prepared.path, timeout=timeout, model=model, effort=effort, prompt=prompt, **options)
    except ControllerError as exc:
        if spawned:
            raise
        return _not_started(exc.evidence.get("check") or exc.code, exc.message, **dirty)

    # D4: the credential check runs first, on every returned result, whatever its outcome.
    try:
        raw = layout.last_message.read_bytes()
    except OSError:
        raw = b""
    # The final message is the verdict: once read, the role's own copy goes, so the verdict file written below (and
    # the Workflow's feedback) are its only retained copies (review I1).
    try:
        runtime.remove_tree(runtime_root, layout.rel("last-message.md"))
    except (OSError, ControllerError):
        pass
    if _worker_output_holds_armour(raw, result):
        return Ending(REFUSED, "credential_in_verdict", _emptied(result), {
            "message": "the role's output holds a private-key armour line; nothing was recorded",
            "message_sha256": _sha256(raw), "message_length": len(raw), "worker_outcome": result.outcome,
            "worker_subtype": result.subtype, **dirty})
    if result.outcome != worker.SUCCESS:
        return Ending(WORKER_FAILED, result.subtype, result, {"message": "the worker did not succeed", **dirty})
    problems = after_check(prepared, layout, manifest)
    if problems:
        return Ending(REFUSED, "read_only_violated", result, {
            "message": "the clone is not as the read-only run left it; the verdict was discarded",
            "problems": problems, **dirty})
    if len(raw) > MAX_VERDICT_BYTES:
        return Ending(REFUSED, "verdict_over_bound", result, {
            "message": f"the final message is {len(raw)} bytes, over {MAX_VERDICT_BYTES}", "length": len(raw), **dirty})
    try:
        status, scope, body = parse_verdict(raw)
    except ValueError as exc:
        return Ending(REFUSED, "verdict_unparsed", result, {"message": str(exc), "message_sha256": _sha256(raw), **dirty})

    composed = compose_verdict(values, status, scope, body, model=model, completed_at=clock())
    composed_bytes = composed.encode("utf-8")
    digest = _sha256(composed_bytes)
    path = runtime.write_bytes(runtime_root, f"reviews/{job_id}-verdict.md", composed_bytes)
    progress({"read_only": True, "stage": "recording", "verdict_sha256": digest})
    evidence_base = {"verdict": composed, "verdict_sha256": digest, **dirty,
                     **({"advisory": values["advisory"]} if values.get("advisory") else {})}
    try:
        recorded = review_recorder.record(root, stage, work_item_id, path, state_identity=state_identity,
                                          digests=values["digests"])
    except (WorkflowQueryError, WorkflowProtocolFailedError) as exc:
        ending = Ending(RECORD_REFUSED, type(exc).__name__, result, {
            **evidence_base, "message": exc.message,
            "state_is_the_truth": "the runner can fail after the Workflow published its state; read the state before "
                                  "recording this verdict by hand, which its phase guard refuses once written"})
        progress({"read_only": True, "stage": "recorded", "result": _recorded_block(ending.code, {
            "cause": ending.cause, "message": exc.message})})
        return ending
    if recorded.ok:
        ending = Ending(RECORDED, None, result, {**evidence_base, "recorded": dict(recorded.values)})
    elif recorded.cause == "state_identity_changed":
        ending = Ending(STATE_IDENTITY_CHANGED, recorded.cause, result, {**evidence_base, "message": recorded.message})
    elif recorded.cause == "workflow_release_changed":
        ending = Ending(RELEASE_CHANGED, recorded.cause, result, {**evidence_base, "message": recorded.message})
    elif recorded.cause == "recorder_commit_pending":
        pending = {k: v for k, v in evidence_base.items() if k != "verdict"}
        ending = Ending(RECORD_PENDING, recorded.workflow_class or recorded.cause, result, {
            **pending, "message": recorded.message,
            "next": "the feedback and the state are written; the next role, /apply-implementation-review, commits "
                    "the pending entry"})
    else:
        ending = Ending(RECORD_REFUSED, recorded.workflow_class or recorded.cause, result, {
            **evidence_base, "message": recorded.message, "recorder_cause": recorded.cause})
    progress({"read_only": True, "stage": "recorded", "result": _recorded_block(ending.code, {
        "cause": ending.cause, "ok": recorded.ok, "message": recorded.message, "values": dict(recorded.values)})})
    return ending


# ---------------------------------------------------------------------------
# Recovery (D5)
# ---------------------------------------------------------------------------


def recover(runtime_root: Path, job_id: str, block: Mapping[str, Any], path: str) -> Ending:
    """What a job's recovery site calls for a record holding the ``codex_review`` block. Removes the clone, never
    records again and names no clone. ``path`` is one of :data:`RECOVERY_PATHS`."""
    error = remove_clone(runtime_root, job_id)
    extra = {"clone_removal_error": error} if error else {}
    stage = block.get("stage")
    if path not in RECOVERY_PATHS:
        raise CodexReviewRecoveryError(f"unknown recovery path {path!r}", evidence={"path": path, "block": dict(block)})
    if stage not in STAGES_IN_ORDER:
        raise CodexReviewRecoveryError(f"unknown codex_review stage {stage!r}", evidence={"stage": stage, "path": path})
    carried = {"result": block["result"]} if stage == "recorded" and "result" in block else {}
    evidence = {"stage": stage, **carried, **extra}
    if path == "abandon":
        return Ending(OPERATOR_ABANDONED, None, None, evidence)
    if path == "drift":
        return Ending(RELEASE_CHANGED, "workflow_release_changed", None, evidence)
    if stage == "preparing":
        return Ending(NOT_RECORDED, None, None, {
            **evidence, "message": "the worker may have run; nothing was recorded, and the next decision reviews again"})
    if stage == "recording":
        return Ending(RECORD_INTERRUPTED, None, None, {
            **evidence, "message": "the Workflow's state is the truth, whatever the record did"})
    code = (block.get("result") or {}).get("code")
    if code not in (RECORDED, STATE_IDENTITY_CHANGED, RELEASE_CHANGED, RECORD_REFUSED, RECORD_PENDING):
        raise CodexReviewRecoveryError(f"a recorded codex_review block names no ending ({code!r})",
                                       evidence={"stage": stage, "path": path})
    return Ending(code, (block.get("result") or {}).get("cause"), None, evidence)
