"""Resolves the Controller's version. Holds no version value itself.

``pyproject.toml``'s static ``[project].version`` is the single version
authority. A running Controller learns its version from where its own code
lives:

- a source runtime reads ``<code_root>/pyproject.toml`` (:func:`source_version`),
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
import re
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


def _require_semver(value: object, where: str) -> str:
    # fullmatch, not match: ``$`` also matches before a trailing newline.
    if not isinstance(value, str) or SEMVER_RE.fullmatch(value) is None:
        raise ValueError(f"{where} is {value!r}, not MAJOR.MINOR.PATCH")
    return value


def parse_pyproject_version(text: str, *, where: str = "pyproject.toml") -> str:
    """The static ``[project].version`` in ``text`` (a ``pyproject.toml``'s
    content), validated as semver. Raises ``ValueError`` naming the failure."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{where} is not valid TOML: {exc}") from None
    project = data.get("project")
    if not isinstance(project, dict) or "version" not in project:
        raise ValueError(f"{where} declares no static [project].version")
    return _require_semver(project["version"], f"{where} [project].version")


def source_version(code_root: Path) -> str:
    """The version ``<code_root>/pyproject.toml`` declares. Raises
    ``ValueError`` naming the failure."""
    path = Path(code_root) / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from None
    return parse_pyproject_version(text, where=str(path))


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
