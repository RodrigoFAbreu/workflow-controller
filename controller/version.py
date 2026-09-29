"""Resolves the Controller's version. Holds no version value itself.

Two version models, told apart by ``pyproject.toml``:

- **static** (every checkout before the squash-merge cutover): the static
  ``[project].version`` is the version;
- **tag-derived** (``dynamic = ["version"]``): the Git tag is the only
  version authority. A checkout reports the highest ``vMAJOR.MINOR.PATCH``
  tag reachable from its commit (:func:`tag_version`), or ``0.0.0`` without
  one, and a release build is told its version by its release tag
  (``setup.py``).

A running Controller learns its version from where its own code lives:

- a source runtime reads ``<code_root>`` itself (:func:`source_version`),
  never distribution metadata: a stale ``*.egg-info`` in the checkout, or
  another ``workflow-controller`` distribution on ``sys.path``, must not
  decide what a checkout reports;
- a package runtime reads the metadata of the one distribution installed in
  its own ``code_root`` directory (:func:`package_version`), which
  ``controller.identity`` then cross-checks against ``BUILD_INFO.json``.

Stdlib only, and imports nothing from ``controller``: ``setup.py`` and the
release tools load this module without the package being importable.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import tomllib
from pathlib import Path

#: The distribution this module identifies: the Controller itself, never an
#: adopter's package.
DISTRIBUTION_NAME = "workflow-controller"
#: The ``RECORD`` entry that proves a distribution owns this package.
_OWNED_FILE = "controller/__init__.py"

#: ``MAJOR.MINOR.PATCH``, the same pattern as ``buildinfo.SEMVER_RE``
#: (spelled here because this module imports nothing from the package).
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
#: The version a tag-derived checkout reports when no release tag is
#: reachable (a shallow clone, or a history before its first release).
NO_TAG_VERSION = "0.0.0"


def _require_semver(value: object, where: str) -> str:
    # fullmatch, not match: ``$`` also matches before a trailing newline.
    if not isinstance(value, str) or SEMVER_RE.fullmatch(value) is None:
        raise ValueError(f"{where} is {value!r}, not MAJOR.MINOR.PATCH")
    return value


def _pyproject_project(text: str, where: str) -> dict:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{where} is not valid TOML: {exc}") from None
    project = data.get("project")
    return project if isinstance(project, dict) else {}


def parse_static_version(text: str, *, where: str = "pyproject.toml") -> str | None:
    """The static ``[project].version`` in ``text`` (a ``pyproject.toml``'s
    content), validated as semver, or ``None`` when the version is dynamic
    or absent. Raises ``ValueError`` for malformed TOML or a static version
    that is not ``MAJOR.MINOR.PATCH``."""
    project = _pyproject_project(text, where)
    if "version" not in project:
        return None
    return _require_semver(project["version"], f"{where} [project].version")


def declares_dynamic_version(text: str, *, where: str = "pyproject.toml") -> bool:
    """Whether ``text`` (a ``pyproject.toml``'s content) lists ``version``
    in ``[project].dynamic``."""
    dynamic = _pyproject_project(text, where).get("dynamic", [])
    return isinstance(dynamic, list) and "version" in dynamic


def _read_pyproject(root: Path) -> tuple[str, str]:
    path = Path(root) / "pyproject.toml"
    try:
        return path.read_text(encoding="utf-8"), str(path)
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from None


def static_pyproject_version(root: Path) -> str | None:
    """The static version ``<root>/pyproject.toml`` declares, or ``None``
    when it is dynamic or absent. Raises ``ValueError`` for an unreadable
    file, malformed TOML or a non-semver static version."""
    text, where = _read_pyproject(root)
    return parse_static_version(text, where=where)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    # GIT_DIR and friends would point git at another repository than root.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              env=env, check=False)
    except OSError as exc:
        raise ValueError(f"cannot run git in {root}: {exc}") from None


def _is_work_tree_top(root: Path) -> bool:
    """Whether ``root`` is the top level of a Git work tree. Raises
    ``ValueError`` when ``git`` cannot run."""
    result = _git(root, "rev-parse", "--show-toplevel")
    return (result.returncode == 0
            and Path(result.stdout.strip()).resolve() == Path(root).resolve())


def _version_key(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def tag_version(root: Path, rev: str = "HEAD") -> str:
    """The highest ``v<MAJOR.MINOR.PATCH>`` tag reachable from ``rev`` in
    the Git work tree at ``root``, without its ``v``, or ``0.0.0`` when none
    is (an unborn ``HEAD`` included). Other tags are ignored. The tag format
    is the Controller's own fixed ``v{version}``, never an adopter policy's.
    Raises ``ValueError`` when ``root`` is not the top of a Git work tree,
    ``rev`` does not resolve (other than an unborn ``HEAD``), or ``git``
    fails."""
    root = Path(root)
    if not _is_work_tree_top(root):
        raise ValueError(f"{root} is not the top level of a Git work tree")
    probe = _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if probe.returncode != 0:
        if rev == "HEAD" and probe.returncode == 1 and not probe.stdout.strip():
            return NO_TAG_VERSION  # an unborn HEAD reaches no tag
        raise ValueError(f"{rev} does not name a commit in {root}: {probe.stderr.strip()}")
    commit = probe.stdout.strip()
    result = _git(root, "for-each-ref", f"--merged={commit}", "--format=%(refname:strip=2)",
                  "refs/tags/")
    if result.returncode != 0:
        raise ValueError(f"cannot list the tags reachable from {rev} in {root}: "
                         f"{result.stderr.strip()}")
    versions = [name[1:] for name in result.stdout.splitlines()
                if name.startswith("v") and SEMVER_RE.fullmatch(name[1:])]
    return max(versions, key=_version_key, default=NO_TAG_VERSION)


def source_version(code_root: Path) -> str:
    """A source checkout's version: the static version its
    ``pyproject.toml`` declares, else the tag-derived one
    (:func:`tag_version` at ``HEAD``). Raises ``ValueError`` naming the
    failure."""
    static = static_pyproject_version(code_root)
    return static if static is not None else tag_version(code_root)


def local_build_version(root: Path) -> str:
    """The version a non-release build of ``root``'s ``HEAD`` carries: the
    static version when one is declared, else :func:`tag_version`, or
    ``0.0.0`` when ``root`` is not a Git work tree (a build from a copied
    tree, which has no provenance either). Raises ``ValueError`` naming the
    failure."""
    static = static_pyproject_version(root)
    if static is not None:
        return static
    try:
        in_git = _is_work_tree_top(root)
    except ValueError:
        in_git = False  # no git at all: setup.py records no provenance either
    return tag_version(root) if in_git else NO_TAG_VERSION


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def package_version(code_root: Path) -> str:
    """The ``Version`` of the one ``workflow-controller`` distribution
    installed in ``code_root`` itself (not anywhere else on ``sys.path``)
    whose ``RECORD`` lists ``controller/__init__.py``. Raises ``ValueError``
    naming the failure."""
    code_root = Path(code_root)
    owners = []
    for dist in importlib.metadata.distributions(path=[str(code_root)]):
        name = dist.metadata.get("Name") if dist.metadata is not None else None
        if not name or _normalise(name) != DISTRIBUTION_NAME:
            continue
        files = dist.files or []
        if any(f.as_posix() == _OWNED_FILE for f in files):
            owners.append(dist)
    if len(owners) != 1:
        raise ValueError(f"{code_root} holds {len(owners)} {DISTRIBUTION_NAME} distributions "
                         f"whose RECORD lists {_OWNED_FILE}, not exactly one")
    return _require_semver(owners[0].metadata.get("Version"),
                           f"the {DISTRIBUTION_NAME} distribution metadata Version in {code_root}")
