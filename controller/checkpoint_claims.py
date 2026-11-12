"""The managed repository's checkpoint claim and amendment witness, read before an implementer worker's clone is made
(``workflow-controller-h4f``, D5).

A Codex worker that runs ``milestone-implement`` works in a private clone whose Git common directory is its own and
empty, so the Workflow's ``claim_checkpoint`` there can neither see a claim another run holds nor the repository-wide
amendment witness. This module reads both in the **managed** repository's common directory, read-only, and refuses the
launch when either says a worker must not start.

The Controller never imports ``scripts/workflow_state.py`` (``controller/errors.py``'s ``UnsupportedWorkflowVersionError``
text, invariant I6), and its only route to Workflow code is ``workflow_contract``'s verified-bytes query, which offers no
claim or witness query. So the layout is a **Controller-side literal**: the claim and witness paths, the witness's
statuses and the resolution projection are copied here, and ``tests/test_checkpoint_claims.py`` pins each of them to
the installed release's own functions in a subprocess, as ``controller/decision.py`` copies ``KNOWN_PHASES``.

Nothing here writes: the claim and the witness are read with ``lstat`` and no symlink following, bounded in size, and the
committed state is read through the hardened runner as data.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from . import gitrepo
from .errors import ControllerError, GitOperationError

#: Where the Workflow keeps its claims and amendment witnesses, relative to the Git common directory
#: (``workflow_state.CLAIMS_RELDIR``).
CLAIMS_RELDIR = "ai-workflow/checkpoint-claims"

#: The Workflow's amendment witness (``workflow_state.AMENDMENT_WITNESS_SCHEMA_VERSION``, ``AMENDMENT_WITNESS_STATUSES``).
WITNESS_SCHEMA_VERSION = 1
WITNESS_STATUSES = frozenset({"OPEN", "RESOLVING", "RESOLVED", "NONE"})
#: The statuses that mean an amendment of the item is in flight (``managed_amendment_open``).
OPEN_STATUSES = frozenset({"OPEN", "RESOLVING"})

#: The three keys ``apply_plan_approval`` adds to an ``amendment_history`` entry when it resolves it
#: (``workflow_state.AMENDMENT_RESOLUTION_TIME_KEYS``).
RESOLUTION_TIME_KEYS = frozenset({"resolved_at_plan_revision", "reconciliation_outcome", "resolved_review_content_id"})

#: The managed repository's state file, read at ``HEAD`` for the resolution comparison.
STATE_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"

#: The most a claim, a witness or the committed state may weigh before it is refused as unreadable.
MAX_BYTES = 4 * 1024 * 1024

#: The causes of a :class:`ManagedStateRefused`, each with the remedy its message ends with.
REMEDIES = {
    "managed_tree_not_clean": "commit, or reset, the managed tree's own uncommitted work, or route the role to "
                              "`claude-code`",
    "managed_claim_present": "resume or release the claim with the Workflow's commands on the managed tree, or route "
                             "the role to `claude-code`",
    "managed_claim_unreadable": "inspect and repair the claim path by hand, or route the role to `claude-code`",
    "managed_amendment_open": "finish the amendment (`/milestone-plan`, review, `/approve-review plan`) and merge its "
                              "approval into this branch, or route the role to `claude-code`",
    "managed_amendment_stale": "merge the resolved amendment into this branch, or route the role to `claude-code`",
    "managed_amendment_conflict": "discard the divergent approval on this branch and merge the recorded one, or "
                                  "route the role to `claude-code`",
    "managed_amendment_unreadable": "inspect and repair the amendment witness, or the committed state, by hand, or "
                                    "route the role to `claude-code`",
}
CAUSES = tuple(REMEDIES)


class ManagedStateRefused(ControllerError):
    """The managed repository is not in a state an implementer worker's clone may start from. ``cause`` is one of
    :data:`CAUSES`; the message names it and ends with its remedy, and ``evidence`` carries it too. Raised before the
    clone is made, so the job ends ``WorkerNotStarted``."""

    code = "ManagedStateRefused"

    def __init__(self, cause: str, message: str, *, evidence: dict | None = None) -> None:
        super().__init__(f"{cause}: {message}; {REMEDIES[cause]}", evidence={**(evidence or {}), "cause": cause})
        self.cause = cause


def _token(work_item_id: str) -> str:
    return hashlib.sha256(work_item_id.encode()).hexdigest()


def claim_path(common_dir: Path, work_item_id: str) -> Path:
    """``workflow_state.claim_path`` for a repository whose common directory is ``common_dir``."""
    return Path(common_dir) / CLAIMS_RELDIR / f"{_token(work_item_id)}.json"


def witness_path(common_dir: Path, work_item_id: str) -> Path:
    """``workflow_state.amendment_witness_path``."""
    return Path(common_dir) / CLAIMS_RELDIR / f"{_token(work_item_id)}.amendment.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def request_projection_sha256(entry: dict) -> str:
    """``workflow_state.amendment_request_projection_sha256``: the hash of the entry without its resolution keys."""
    return hashlib.sha256(_canonical({k: v for k, v in entry.items() if k not in RESOLUTION_TIME_KEYS})).hexdigest()


def resolution_projection_sha256(entry: dict) -> str:
    """``workflow_state.amendment_resolution_projection_sha256``, defined only for a resolved entry."""
    return hashlib.sha256(_canonical({
        "request_projection_sha256": request_projection_sha256(entry),
        "resolved_at_plan_revision": entry.get("resolved_at_plan_revision"),
        "reconciliation_outcome": entry.get("reconciliation_outcome"),
        "resolved_review_content_id": entry.get("resolved_review_content_id"),
    })).hexdigest()


_ABSENT = object()


def _read(path: Path) -> bytes | object:
    """The bytes of the regular file at ``path``, ``_ABSENT`` when nothing is there, and an :class:`OSError` for every
    other shape (a link, a directory, a special file, an unreadable or oversized file). The parent is checked by the
    caller. ``lstat`` first and ``O_NOFOLLOW`` on the open, so a link swapped in between is refused too."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return _ABSENT
    if not stat.S_ISREG(info.st_mode):
        raise OSError(errno.EINVAL, "not a regular file")
    if info.st_size > MAX_BYTES:
        raise OSError(errno.EFBIG, "too large")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        data = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise OSError(errno.EFBIG, "too large")
    return data


def _claims_dir_state(common_dir: Path) -> str:
    """``absent``, ``ok`` or ``unreadable`` for the claims directory (a link or a non-directory is unreadable)."""
    path = Path(common_dir) / CLAIMS_RELDIR
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    return "ok" if stat.S_ISDIR(info.st_mode) else "unreadable"


def _witness(raw: bytes, work_item_id: str) -> dict | None:
    """The witness parsed as the Workflow's schema, or ``None`` for a shape the Workflow's own reader refuses."""
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != WITNESS_SCHEMA_VERSION:
        return None
    if data.get("work_item_id") != work_item_id or data.get("status") not in WITNESS_STATUSES:
        return None
    seq = data.get("amendment_seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        return None
    if (data["status"] == "NONE") != (seq == 0):
        return None
    return data


def _committed_entry(root: Path, work_item_id: str, seq: int, runner: gitrepo.Runner) -> dict | None:
    """``work_items[<id>].amendment_history[seq - 1]`` of the state ``HEAD`` commits, ``None`` when the entry is absent
    or has no resolution; :class:`ManagedStateRefused` (``managed_amendment_unreadable``) for state that cannot be
    read as data."""
    def unreadable(reason: str) -> ManagedStateRefused:
        return ManagedStateRefused("managed_amendment_unreadable", f"the managed repository's committed state {reason}",
                                   evidence={"path": STATE_PATH})

    try:
        raw = gitrepo.show(root, "HEAD", STATE_PATH, runner=runner)
    except GitOperationError as exc:
        raise unreadable(f"cannot be read ({exc.message})") from exc
    if raw is None or len(raw) > MAX_BYTES:
        raise unreadable("is absent or too large")
    try:
        state = json.loads(raw)
        item = state["work_items"][work_item_id]
        history = item.get("amendment_history")
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        raise unreadable("is not the Workflow's state shape") from None
    if history is None:
        return None
    if not isinstance(history, list):
        raise unreadable("holds an amendment_history that is not a list")
    if seq > len(history):
        return None
    entry = history[seq - 1]
    if not isinstance(entry, dict):
        raise unreadable("holds an amendment_history entry that is not an object")
    return entry if entry.get("resolved_at_plan_revision") is not None else None


def check(managed_repo: Any, work_item_id: str, runner: gitrepo.Runner) -> None:
    """Refuse (:class:`ManagedStateRefused`) when the managed repository's common directory holds a checkpoint claim for
    ``work_item_id`` or an amendment of it that a worker in a clone could not see, in D5's order: the claim (present or
    unreadable), then the witness (unreadable, open or resolving, resolved without or against the committed
    resolution). Returns when nothing stands in the way. Reads only."""
    root = Path(managed_repo.root)
    try:
        common = gitrepo.common_dir(root, runner=runner)
    except GitOperationError as exc:
        raise ManagedStateRefused("managed_claim_unreadable",
                                  f"the managed repository's common directory cannot be read ({exc.message})",
                                  evidence=dict(exc.evidence)) from exc
    directory = _claims_dir_state(common)
    claim, witness = claim_path(common, work_item_id), witness_path(common, work_item_id)
    if directory == "unreadable":
        raise ManagedStateRefused("managed_claim_unreadable",
                                  f"{common / CLAIMS_RELDIR} is a link or not a directory", evidence={"path": str(claim)})
    if directory == "absent":
        return
    try:
        held = _read(claim)
    except OSError as exc:
        raise ManagedStateRefused("managed_claim_unreadable", f"{claim} cannot be read ({exc.strerror or exc})",
                                  evidence={"path": str(claim)}) from exc
    if held is not _ABSENT:
        raise ManagedStateRefused("managed_claim_present", f"{claim} holds a checkpoint claim for {work_item_id}",
                                  evidence={"path": str(claim)})
    def unreadable(reason: str) -> ManagedStateRefused:
        return ManagedStateRefused("managed_amendment_unreadable", f"the amendment witness {witness} {reason}",
                                   evidence={"path": str(witness)})

    try:
        raw = _read(witness)
    except OSError as exc:
        raise unreadable(f"cannot be read ({exc.strerror or exc})") from exc
    if raw is _ABSENT:
        return
    data = _witness(raw, work_item_id)  # type: ignore[arg-type]
    if data is None:
        raise unreadable("is torn, of another schema or item, or has an unknown status")
    status = data["status"]
    if status == "NONE":
        return
    if status in OPEN_STATUSES:
        raise ManagedStateRefused("managed_amendment_open",
                                  f"an amendment of {work_item_id} is {status.lower()} in this repository "
                                  f"(sequence {data.get('amendment_seq')})", evidence={"path": str(witness), "status": status})
    seq, recorded = data["amendment_seq"], data.get("resolution_projection_sha256")
    if seq < 1 or not isinstance(recorded, str):
        raise unreadable("is RESOLVED without a positive amendment_seq or a resolution projection")
    entry = _committed_entry(root, work_item_id, seq, runner)
    if entry is None:
        raise ManagedStateRefused("managed_amendment_stale",
                                  f"the amendment of {work_item_id} resolved elsewhere (sequence {seq}) is not in this "
                                  "branch's committed state", evidence={"path": str(witness), "amendment_seq": seq})
    committed = resolution_projection_sha256(entry)
    if committed != recorded:
        raise ManagedStateRefused("managed_amendment_conflict",
                                  f"this branch's resolution of amendment {seq} of {work_item_id} differs from the one "
                                  "the repository records",
                                  evidence={"path": str(witness), "amendment_seq": seq, "recorded": recorded,
                                            "committed": committed})
