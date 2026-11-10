"""The review recorder (H4e2, D1): the Controller records a local review
verdict in a managed repository through the Workflow's own writers.

``/review-plan`` and ``/review-implementation`` record a local stage's
verdict by calling Workflow library functions in a fixed order. The
Workflow has no protocol operation for a local stage, so the Controller
runs the same calls itself: a small driver program (:data:`DRIVER_SOURCE`)
is run through :func:`controller.workflow_contract.run_in_private_copy`
beside the digest-checked script set, under the same Git isolation. The
Controller never imports Workflow code into its own process and decides no
state field: the ledger, phase, binding, counts and optional findings are
written by the Workflow's code against its own validators.

Four operations, each a function here (the last two are the carry's, ``workflow-controller-h4e3`` D4 and D6a):

- :func:`check` runs before any launch and writes nothing;
- :func:`record` takes the path of a composed verdict file and makes the
  command's write set. It is the only operation that passes
  ``real_index=True``, because the commit an implementation ``REVISE``
  makes needs the target's own index.

- :func:`commit_pending` commits a pending ``APPLYING_REVIEW_FEEDBACK`` entry through the Workflow's own committer;
- :func:`carry_state` replaces one work item's entry of the state file, inside the Workflow's ``state_transaction``.

A refusal of the Workflow is a :class:`RecorderResult` with ``ok`` false and
one cause of :data:`CAUSES`. The runner's own errors
(:class:`~controller.errors.WorkflowQueryError`,
:class:`~controller.errors.WorkflowProtocolFailedError`) propagate unchanged:
the caller decides their ending (D5), and after a ``record`` they do not say
whether the state was written -- the Workflow's state is the truth.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Mapping

from controller import protocol, workflow_contract
from controller.errors import (
    ControllerError,
    WorkflowProtocolFailedError,
    WorkflowQueryError,
    WorkflowReleaseChangedError,
)

#: The two stages a local review has.
STAGES = ("plan", "implementation")

#: One cause per refusal (D1), in the order the driver can raise them.
CAUSES = (
    "recorder_branch_unsupported",
    "recorder_feedback_foreign",
    "recorder_index_dirty",
    "recorder_identity_mismatch",
    "recorder_interface_changed",
    "state_identity_changed",
    "workflow_release_changed",
    "recorder_commit_pending",
    "workflow_refused",
    "carry_managed_changed",
)

#: Where the driver sits among the sources (``run_in_private_copy`` writes
#: each under the private ``scripts/`` directory by its base name).
DRIVER_KEY = "scripts/controller_review_recorder_driver.py"

#: The Workflow functions the driver calls and the parameter names it calls
#: them with. ``check`` probes each with ``inspect.signature``: a release
#: that renames, adds or removes a parameter fails closed before any launch
#: (``recorder_interface_changed``). A private name appears because the
#: commands name it (``_write_review_feedback_atomically``).
INTERFACE: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "workflow_state": {
        "state_transaction": ("repo_root", "mutator", "path", "before_publish", "on_failure"),
        "validate_local_plan_review_preconditions": ("work_item",),
        "validate_local_plan_review_preconditions_bound": ("repo_root", "work_item"),
        "validate_local_implementation_review_preconditions": ("work_item",),
        "verify_implementation_review_bundle": ("repo_root", "work_item_id", "state"),
        "review_stage_gate_context": ("repo_root", "state", "work_item_id", "stage"),
        "record_local_plan_review": ("state", "work_item_id", "verdict", "bundle_id", "review_content_id",
                                     "round", "now", "audit", "verdict_text"),
        "record_local_implementation_review": ("state", "work_item_id", "verdict", "bundle_id",
                                               "review_content_id", "round", "now", "audit", "verdict_text"),
        "local_review_audit": ("repo_root", "state", "work_item_id", "stage", "feedback_text", "feedback_path"),
        "commit_pending_applying_review_feedback_entry": ("repo_root", "work_item_id", "attribution"),
        "_write_review_feedback_atomically": ("path", "text"),
    },
    "workflow_fingerprint": {
        "assert_local_generation_matches": ("repo_root", "manifest_path", "require_metadata"),
        "assert_bundle_not_rejected": ("repo_root", "work_item_id"),
        "assert_feedback_not_owned_by_other_work_item": ("existing_content", "work_item_id", "state"),
        "ensure_feedback_dir": ("repo_root", "work_item_id"),
        "assert_verdict_well_formed": ("content",),
        "resolve_bundle_dir": ("repo_root", "work_item_id", "stage"),
        "resolve_feedback_dir": ("repo_root", "work_item_id"),
        "compute_bundle_id": ("bundle_dir", "manifest_content_override"),
        "compute_review_content_id_plan_stage_for_work_item": ("repo_root", "work_item_id", "base"),
        "read_plan_stage_manifest_fields": ("manifest_path",),
    },
}

#: The driver. It prints one JSON object: ``{"ok": true, ...}`` or
#: ``{"ok": false, "cause", "class", "message"}``. :func:`driver_source`
#: prefixes the ``INTERFACE_JSON`` line that carries :data:`INTERFACE`.
DRIVER_SOURCE = r'''
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

INTERFACE = json.loads(INTERFACE_JSON)
ROLES = {"plan": "LOCAL_MODEL_PLAN_REVIEW", "implementation": "LOCAL_MODEL_IMPLEMENTATION_REVIEW"}


class Refuse(Exception):
    def __init__(self, cause, cls=None, message=""):
        super().__init__(message)
        self.cause, self.cls, self.message = cause, cls, message


def refuse_from(exc, cause="workflow_refused"):
    return Refuse(cause, type(exc).__name__, str(exc))


def modules():
    import workflow_fingerprint
    import workflow_state
    return {"workflow_state": workflow_state, "workflow_fingerprint": workflow_fingerprint}


def probe(mods):
    for module_name, functions in INTERFACE.items():
        for name, expected in functions.items():
            function = getattr(mods[module_name], name, None)
            if function is None:
                raise Refuse("recorder_interface_changed", None, f"{module_name}.{name} does not exist")
            found = tuple(inspect.signature(function).parameters)
            if found != tuple(expected):
                raise Refuse("recorder_interface_changed", None,
                             f"{module_name}.{name} takes {list(found)}, the recorder calls it with {list(expected)}")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, check=False)


def guards(root, stage, wid, mods):
    """The command's version, phase and bundle guards, in its order. Writes
    nothing. Returns the values the header needs."""
    ws, fp = mods["workflow_state"], mods["workflow_fingerprint"]
    try:
        state = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text(encoding="utf-8"))
        wi = state["work_items"][wid]
    except (OSError, ValueError, KeyError) as exc:
        raise refuse_from(exc)
    advisory = None
    if stage == "implementation":
        if wi.get("governing_workflow_version") != "2.2" or wi.get("phase") != "AWAITING_LOCAL_IMPLEMENTATION_REVIEW":
            raise Refuse("recorder_branch_unsupported", None,
                         f"{wid} is at phase {wi.get('phase')!r}, governing version "
                         f"{wi.get('governing_workflow_version')!r}: the command routes it to its advisory branch")
        try:
            ws.validate_local_implementation_review_preconditions(wi)
        except Exception as exc:
            raise refuse_from(exc)
        staged = git(root, "diff", "--cached", "--name-only", "-z")
        if staged.returncode != 0:
            raise Refuse("workflow_refused", "GitError", staged.stderr.decode("utf-8", "replace").strip())
        if staged.stdout:
            names = [n for n in staged.stdout.decode("utf-8", "replace").split("\0") if n]
            raise Refuse("recorder_index_dirty", None, f"the index holds staged content: {', '.join(names[:5])}")
    else:
        try:
            ws.validate_local_plan_review_preconditions(wi)
        except Exception as exc:
            raise refuse_from(exc)
    try:
        bundle_rel = fp.resolve_bundle_dir(root, wid, stage="plan") if stage == "plan" else fp.resolve_bundle_dir(root, wid)
        bundle_dir = root / bundle_rel
        manifest = bundle_dir / "MANIFEST.md"
        fp.assert_local_generation_matches(root, manifest)
        fp.assert_bundle_not_rejected(root, wid)
    except Exception as exc:
        raise refuse_from(exc)
    try:
        if stage == "implementation":
            fresh = ws.verify_implementation_review_bundle(root, wid, state=state)
            bundle_id, content_id = fresh["bundle_id"], fresh["review_content_id"]
        else:
            bundle_id = fp.compute_bundle_id(bundle_dir)[0]
            content_id = fp.compute_review_content_id_plan_stage_for_work_item(root, wid)[0]
            fields = fp.read_plan_stage_manifest_fields(manifest)
            for label, fresh_value, recorded in (("bundle_id", bundle_id, fields.get("bundle_id")),
                                                 ("review_content_id", content_id, fields.get("review_content_id"))):
                if fresh_value != recorded:
                    raise Refuse("recorder_identity_mismatch", None,
                                 f"{label}: MANIFEST.md records {recorded!r}, the bundle recomputes to {fresh_value!r}")
    except Refuse:
        raise
    except Exception as exc:
        name = type(exc).__name__
        raise refuse_from(exc, "recorder_identity_mismatch" if name == "ImplementationReviewBundleUnverifiedError"
                          else "workflow_refused")
    if stage == "plan":
        try:
            advisory = ws.validate_local_plan_review_preconditions_bound(root, wi)
        except Exception as exc:
            raise refuse_from(exc)
    try:
        feedback = root / fp.resolve_feedback_dir(root, wid) / "REVIEW_FEEDBACK.md"
        existing = feedback.read_text(encoding="utf-8") if feedback.is_file() else None
        fp.assert_feedback_not_owned_by_other_work_item(existing, work_item_id=wid, state=state)
    except Exception as exc:
        raise refuse_from(exc, "recorder_feedback_foreign"
                          if type(exc).__name__ == "FeedbackOwnedByOtherWorkItemError" else "workflow_refused")
    counts = (wi.get("review_round_counts") or {}).get(stage) or {}
    revision_key = "plan_revision" if stage == "plan" else "implementation_revision"
    return state, wi, {
        "bundle_id": bundle_id, "review_content_id": content_id, "base_commit": wi["base_commit"],
        "work_item_id": wid, "reviewer_role": ROLES[stage],
        "revision_label": "Plan revision reviewed:" if stage == "plan" else "Implementation revision reviewed:",
        "revision": str(wi.get(revision_key)), "round": int(counts.get("local", 0)) + 1,
        "advisory": advisory,
        "gate": ws.review_stage_gate_context(root, state, wid, stage),
    }


def header_value(text, label):
    head = text.split("\n## ", 1)[0]
    for line in head.splitlines():
        if line.startswith(label):
            return line[len(label):].strip()
    return None


def do_check(root, stage, wid):
    mods = modules()
    probe(mods)
    _state, _wi, values = guards(root, stage, wid, mods)
    return values


def do_record(root, stage, wid, verdict_file):
    mods = modules()
    probe(mods)
    ws, fp = mods["workflow_state"], mods["workflow_fingerprint"]
    text = Path(verdict_file).read_bytes().decode("utf-8")
    status = header_value(text, "Status:")
    now = header_value(text, "Completed at:")
    if status not in ("APPROVE", "REVISE", "BLOCK") or not now:
        raise Refuse("workflow_refused", "MalformedVerdictError", "the composed verdict has no Status: or Completed at: line")
    state, wi, values = guards(root, stage, wid, mods)
    for label, key in (("Reviewed bundle ID:", "bundle_id"), ("Reviewed base commit:", "base_commit"),
                       ("Work item:", "work_item_id"), ("Reviewed review_content_id:", "review_content_id"),
                       ("Reviewer role:", "reviewer_role"), ("Review round:", "round")):
        if header_value(text, label) != str(values[key]):
            raise Refuse("recorder_identity_mismatch", None,
                         f"{label} {header_value(text, label)!r} is not the recomputed {values[key]!r}")
    try:
        fp.assert_bundle_not_rejected(root, wid)
        feedback_dir = root / fp.resolve_feedback_dir(root, wid)
        feedback = feedback_dir / "REVIEW_FEEDBACK.md"
        existing = feedback.read_text(encoding="utf-8") if feedback.is_file() else None
        fp.assert_feedback_not_owned_by_other_work_item(existing, work_item_id=wid, state=state)
    except Exception as exc:
        raise refuse_from(exc, "recorder_feedback_foreign"
                          if type(exc).__name__ == "FeedbackOwnedByOtherWorkItemError" else "workflow_refused")
    try:
        fp.ensure_feedback_dir(root, wid)
        fp.assert_verdict_well_formed(text)
        ws._write_review_feedback_atomically(feedback, text)
        rel = feedback.relative_to(root).as_posix()
        writer = ws.record_local_plan_review if stage == "plan" else ws.record_local_implementation_review

        def mutator(current):
            return writer(
                current, wid, verdict=status, bundle_id=values["bundle_id"],
                review_content_id=values["review_content_id"], round=values["round"], now=now,
                audit=ws.local_review_audit(root, current, wid, stage, feedback_text=text, feedback_path=rel),
                verdict_text=text)

        written = ws.state_transaction(root, mutator)
    except Exception as exc:
        raise refuse_from(exc)
    item = written["work_items"][wid]
    answer = {"ok": True, "status": status, "phase": item["phase"], "round": values["round"],
              "feedback_path": rel, "state_revision": item.get("state_revision"), "committed": None}
    if stage == "implementation" and status == "REVISE":
        try:
            answer["committed"] = ws.commit_pending_applying_review_feedback_entry(root, wid, attribution=())
        except Exception as exc:
            raise Refuse("recorder_commit_pending", type(exc).__name__, str(exc))
    return answer


def do_commit_pending(root, wid):
    mods = modules()
    probe(mods)
    try:
        committed = mods["workflow_state"].commit_pending_applying_review_feedback_entry(root, wid, attribution=())
    except Exception as exc:
        raise Refuse("recorder_commit_pending", type(exc).__name__, str(exc))
    return {"ok": True, "committed": committed}


def do_carry_state(root, wid, payload_file):
    mods = modules()
    probe(mods)
    ws = mods["workflow_state"]
    payload = json.loads(Path(payload_file).read_bytes().decode("utf-8"))
    before, after = payload["before"], payload["after"]

    def mutator(current):
        entry = current["work_items"].get(wid)
        if entry == after:
            return current
        if entry != before:
            raise Refuse("carry_managed_changed", None, f"the entry of {wid} in the state file is not the one carried in")
        fresh = dict(current)
        fresh["work_items"] = {**current["work_items"], wid: after}
        return fresh

    try:
        current = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text(encoding="utf-8"))
        if current["work_items"].get(wid) == after:
            return {"ok": True, "written": False}
        ws.state_transaction(root, mutator)
    except Refuse:
        raise
    except Exception as exc:
        raise refuse_from(exc)
    return {"ok": True, "written": True}


def main(argv):
    operation, stage, wid = argv[0], argv[1], argv[2]
    root = Path.cwd()
    try:
        if operation == "commit_pending":
            answer = do_commit_pending(root, wid)
        elif operation == "carry_state":
            answer = do_carry_state(root, wid, argv[3])
        elif operation == "check":
            answer = {"ok": True, **do_check(root, stage, wid)}
        else:
            answer = do_record(root, stage, wid, argv[3])
    except Refuse as refusal:
        answer = {"ok": False, "cause": refusal.cause, "class": refusal.cls, "message": refusal.message}
    except Exception as exc:
        answer = {"ok": False, "cause": "workflow_refused", "class": type(exc).__name__, "message": str(exc)}
    sys.stdout.write(json.dumps(answer, sort_keys=True) + "\n")


main(sys.argv[1:])
'''


@dataclasses.dataclass(frozen=True)
class RecorderResult:
    """One driver answer. ``ok`` false carries ``cause`` (one of
    :data:`CAUSES`), the Workflow's ``workflow_class`` (``None`` when the
    refusal is the recorder's own) and its ``message``. ``values`` is
    ``check``'s header values, gate context, round and script digests, or
    ``record``'s phase, round, feedback path and commit."""

    ok: bool
    cause: str | None
    workflow_class: str | None
    message: str | None
    values: Mapping[str, Any]


def driver_source() -> str:
    return f"INTERFACE_JSON = {json.dumps(json.dumps(INTERFACE, sort_keys=True))}\n{DRIVER_SOURCE}"


def _scripts(root: Path) -> protocol.ScriptSet:
    managed = protocol.read_managed(root)
    if not protocol.protocol_script_listed(managed):
        raise WorkflowProtocolFailedError(
            f"{root}'s installation record does not list {protocol.SCRIPT_KEY}: the Workflow does not ship "
            f"the orchestration protocol", evidence={"reason": "no_protocol", "operation": "review recorder"})
    return protocol.script_set(root, managed)


def _run(root: Path, scripts: protocol.ScriptSet, operation: str, args: list[str], *, work_item_id: str,
         real_index: bool, timeout: float | None) -> dict:
    if timeout is None:
        timeout = workflow_contract.query_timeout_seconds()
    context = {
        "release": None, "query": f"review recorder {operation}", "work_item_id": work_item_id,
        "operation": operation, "cwd": str(root), "argv": None, "returncode": None,
        "stdout_tail": "", "stderr_tail": "",
    }
    sources = {**scripts.sources, DRIVER_KEY: driver_source().encode("utf-8")}
    completed = workflow_contract.run_in_private_copy(
        root, sources, DRIVER_KEY, [operation, *args], timeout=timeout, context=context, real_index=real_index)
    problem = None
    answer: Any = None
    if completed.returncode != 0:
        problem = f"the driver exited {completed.returncode}"
        reason = "query_failed"
    else:
        reason = "query_output_invalid"
        try:
            answer = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            problem = f"stdout is not JSON ({exc})"
        else:
            if not isinstance(answer, dict) or not isinstance(answer.get("ok"), bool):
                problem = "stdout is not a JSON object with a boolean ok"
    if problem is not None:
        raise WorkflowQueryError(
            f"{context['query']} for {work_item_id!r} gave no answer the Controller may use: {problem}",
            evidence={**context, "reason": reason, "problem": problem})
    return answer


def _result(answer: dict, extra: Mapping[str, Any]) -> RecorderResult:
    if not answer["ok"]:
        return RecorderResult(False, answer.get("cause"), answer.get("class"), answer.get("message"), dict(extra))
    values = {key: value for key, value in answer.items() if key != "ok"}
    return RecorderResult(True, None, None, None, {**values, **extra})


def check(root: Path, stage: str, work_item_id: str, *, timeout: float | None = None) -> RecorderResult:
    """Everything a local review needs to be launched and later recorded,
    and nothing written: the header values, the gate context, the round and
    the digests of the script set it ran under."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, not {stage!r}")
    scripts = _scripts(root)
    answer = _run(root, scripts, "check", [stage, work_item_id], work_item_id=work_item_id,
                  real_index=False, timeout=timeout)
    return _result(answer, {"digests": dict(scripts.digests)})


def _check_digests(root: Path, digests: Mapping[str, str] | None) -> tuple[RecorderResult | None, protocol.ScriptSet]:
    scripts = _scripts(root)
    if digests is None:
        return None, scripts
    changed = sorted(key for key in {*digests, *scripts.digests} if digests.get(key) != scripts.digests.get(key))
    if changed:
        return RecorderResult(False, "workflow_release_changed", "WorkflowReleaseChangedError",
                              f"the Workflow scripts changed since the decision ({', '.join(changed)}); nothing was "
                              f"written", {"changed_scripts": changed}), scripts
    return None, scripts


def commit_pending(root: Path, work_item_id: str, *, digests: Mapping[str, str] | None = None,
                   timeout: float | None = None) -> RecorderResult:
    """Commit a pending ``APPLYING_REVIEW_FEEDBACK`` entry of ``work_item_id`` through the Workflow's
    ``commit_pending_applying_review_feedback_entry`` (D4 of ``workflow-controller-h4e3``). ``values["committed"]`` is
    the new commit, or ``None`` when there was nothing to commit. With ``digests`` a script set that differs is
    ``workflow_release_changed``, before anything is written. A Workflow refusal is ``recorder_commit_pending``."""
    refusal, scripts = _check_digests(root, digests)
    if refusal is not None:
        return refusal
    answer = _run(root, scripts, "commit_pending", ["-", work_item_id], work_item_id=work_item_id,
                  real_index=True, timeout=timeout)
    return _result(answer, {})


def carry_state(root: Path, work_item_id: str, payload_path: Path, *, digests: Mapping[str, str] | None = None,
                timeout: float | None = None) -> RecorderResult:
    """Replace ``work_item_id``'s entry of the state file inside the Workflow's ``state_transaction`` (D6a). The
    file at ``payload_path`` holds ``{"before": <the carried-in entry>, "after": <the worker's entry>}``; the write is
    refused as ``carry_managed_changed`` when the freshly read entry is neither, and is a no-op (``values["written"]``
    false) when it already equals ``after``. Every other work item's entry is kept as it is read under the lock."""
    refusal, scripts = _check_digests(root, digests)
    if refusal is not None:
        return refusal
    answer = _run(root, scripts, "carry_state", ["-", work_item_id, str(payload_path)], work_item_id=work_item_id,
                  real_index=False, timeout=timeout)
    return _result(answer, {})


def record(root: Path, stage: str, work_item_id: str, verdict_path: Path, *, state_identity: str,
           digests: Mapping[str, str], timeout: float | None = None) -> RecorderResult:
    """Record the composed verdict at ``verdict_path`` through the
    Workflow's writers. ``state_identity`` is the identity the caller
    decided at and ``digests`` the script digests ``check`` returned: a
    state that moved is ``state_identity_changed`` and a script set that
    differs is ``workflow_release_changed``, each before anything is
    written. The runner's own errors propagate unchanged."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, not {stage!r}")
    try:
        protocol.next_action(root, work_item_id, expect_state_identity=state_identity,
                             expected_digests=digests)
    except WorkflowReleaseChangedError as exc:
        return RecorderResult(False, "workflow_release_changed", type(exc).__name__, exc.message,
                              {"evidence": exc.evidence})
    except ControllerError as exc:
        refusal = getattr(exc, "evidence", {}).get("refusal") or {}
        if refusal.get("code") != "stale_decision":
            raise
        return RecorderResult(False, "state_identity_changed", None, exc.message, {"evidence": exc.evidence})
    scripts = _scripts(root)
    changed = sorted(key for key in {*digests, *scripts.digests} if digests.get(key) != scripts.digests.get(key))
    if changed:
        return RecorderResult(False, "workflow_release_changed", "WorkflowReleaseChangedError",
                              f"the Workflow scripts changed since check ({', '.join(changed)}); nothing was written",
                              {"changed_scripts": changed})
    answer = _run(root, scripts, "record", [stage, work_item_id, str(verdict_path)], work_item_id=work_item_id,
                  real_index=True, timeout=timeout)
    return _result(answer, {})
