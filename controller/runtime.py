"""The Controller-owned runtime root: resolution ladder, atomic JSON I/O,
and the containment guard that makes "the Controller never writes Workflow
state" a checked invariant rather than a promise.

See ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "Controller-owned runtime
state (the runtime root)".
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path

from controller.errors import RuntimeContainmentError, RuntimeRootUnwritableError

#: Precedence ladder rows, named so evidence can report which one fired.
LADDER_RUNTIME_DIR = 1
LADDER_ENV_HOME = 2
LADDER_ORIGIN_CHECKOUT = 3
LADDER_XDG_STATE = 4

_ENV_HOME = "WORKFLOW_CONTROLLER_HOME"


def _is_git_repository(path: Path) -> bool:
    """Whether ``path`` is (inside) a Git working tree, checked by asking
    Git rather than by looking for ``.git`` directly -- a worktree's own
    ``.git`` is a file, not a directory, and this must agree with every
    other ``git`` invocation in this package."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def resolve_runtime_root(
    *,
    runtime_dir: str | os.PathLike | None,
    origin_source_root: Path,
    env: dict | None = None,
) -> tuple[Path, int]:
    """Resolve the runtime root per the stated precedence ladder. Returns
    ``(resolved_path, ladder_row)`` -- both are reported by ``status`` and
    carried in refusal evidence, never silently discarded.

    ``env`` defaults to ``os.environ`` and is overridable only for tests
    (row 4's own ``XDG_STATE_HOME`` case, and row 2's env-var case, both
    need to be exercised without mutating the real process environment).
    """
    env = os.environ if env is None else env

    if runtime_dir is not None:
        return Path(runtime_dir).expanduser().resolve(), LADDER_RUNTIME_DIR

    home = env.get(_ENV_HOME)
    if home:
        return Path(home).expanduser().resolve(), LADDER_ENV_HOME

    if _is_git_repository(origin_source_root):
        return (origin_source_root / ".controller").resolve(), LADDER_ORIGIN_CHECKOUT

    xdg_state = env.get("XDG_STATE_HOME")
    if xdg_state:
        base = Path(xdg_state).expanduser()
    else:
        base = Path.home() / ".local" / "state"
    return (base / "workflow-controller").resolve(), LADDER_XDG_STATE


def ensure_runtime_root(root: Path, *, ladder_row: int) -> Path:
    """Create ``root`` if it does not exist and confirm it is writable.
    Never a bare ``PermissionError``/``OSError`` -- always a named
    ``RuntimeRootUnwritableError`` carrying the resolved path and the
    ladder row that produced it."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".write-probe-{secrets.token_hex(8)}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise RuntimeRootUnwritableError(
            f"the runtime root at {root} (ladder row {ladder_row}) cannot be created or "
            f"written: {exc}",
            evidence={"path": str(root), "ladder_row": ladder_row, "os_error": str(exc)},
        ) from exc
    return root


def _assert_contained(runtime_root: Path, full_path: Path) -> None:
    runtime_root = runtime_root.resolve()
    try:
        full_path.resolve().relative_to(runtime_root)
    except ValueError:
        raise RuntimeContainmentError(
            f"refusing to write {full_path} -- it is outside the runtime root {runtime_root}",
            evidence={"path": str(full_path), "runtime_root": str(runtime_root)},
        ) from None


def write_json(runtime_root: Path, rel_path: str | os.PathLike, obj: dict) -> Path:
    """Atomically write ``obj`` as canonical JSON to
    ``<runtime_root>/<rel_path>``. Raises ``RuntimeContainmentError`` unless
    the resolved destination is inside ``runtime_root`` -- this is the sole
    write path in the package, so every writer in it (including snapshot
    materialisation) is confined by this one guard.

    Atomic: writes to a ``.tmp`` sibling in the same directory, ``fsync``s
    it, then ``os.replace``s it onto the target -- an interruption never
    leaves a half-written record."""
    full_path = (runtime_root / rel_path)
    _assert_contained(runtime_root, full_path)
    full_path = full_path.resolve()
    full_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = full_path.parent / f".{full_path.name}.{secrets.token_hex(8)}.tmp"
    data = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, full_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return full_path


def read_json(path: Path) -> dict | None:
    """Read and parse a JSON file, returning ``None`` if it does not exist.
    A malformed file raises ``json.JSONDecodeError`` (or ``OSError``) --
    this function never silently substitutes a default for corrupt content."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    return json.loads(raw)
