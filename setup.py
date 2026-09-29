"""Build hook only: every piece of project metadata lives in ``pyproject.toml``.

The ``build_py`` subclass below writes ``controller/BUILD_INFO.json`` into
``build_lib`` after the standard copy. It never writes into the source tree
and does nothing at all for an editable install, so an editable install stays
a source runtime by construction. ``controller/buildinfo.py`` is loaded by
file path; ``controller/__init__.py`` is never imported.

The version is setuptools' own static read of ``pyproject.toml``'s
``[project].version`` when one is declared. When ``pyproject.toml`` declares
``dynamic = ["version"]`` instead, the version is passed to ``setup()``: a
release build's comes from its ``WORKFLOW_CONTROLLER_RELEASE_TAG`` (a
malformed tag fails the build), and any other build's is
``controller/version.py``'s ``local_build_version`` (the highest release tag
reachable from ``HEAD``, or ``0.0.0``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.errors import SetupError

SOURCE_DIR = Path(__file__).resolve().parent
PACKAGE = "controller"
RELEASE_TAG_ENV = "WORKFLOW_CONTROLLER_RELEASE_TAG"
#: The build inputs whose uncommitted changes make a build dirty.
DIRTY_SCOPE = ("controller", "pyproject.toml", "setup.py")


def _load_module(name: str):
    path = SOURCE_DIR / PACKAGE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_workflow_controller_build_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(SOURCE_DIR), *args],
                                capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _source_provenance() -> tuple[str | None, bool | None]:
    """``(source_commit, source_dirty)``, or ``(None, None)`` when the source
    directory is not the top level of a Git work tree with a commit."""
    toplevel = _git("rev-parse", "--show-toplevel")
    if toplevel is None or Path(toplevel.strip()).resolve() != SOURCE_DIR:
        return None, None
    head = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--", *DIRTY_SCOPE)
    if head is None or status is None:
        return None, None
    return head.strip(), bool(status.strip())


class build_py(_build_py):
    def run(self) -> None:
        if self.editable_mode:
            # setuptools' own build_py.run returns early here too. Raising
            # would only be swallowed by editable_wheel with a warning.
            super().run()
            return
        # Command.copy_file passes update=not self.force, and build_py keeps
        # mtimes: without this a changed file whose mtime did not advance
        # would keep its old bytes in build_lib.
        self.force = True
        super().run()
        self._write_build_info()

    def _write_build_info(self) -> None:
        buildinfo = _load_module("buildinfo")
        version = self.distribution.get_version()
        package_dir = Path(self.build_lib) / PACKAGE

        source_commit, source_dirty = _source_provenance()
        if RELEASE_TAG_ENV in os.environ:
            build_origin, release_tag = buildinfo.BUILD_ORIGIN_RELEASE, os.environ[RELEASE_TAG_ENV]
        else:
            build_origin, release_tag = buildinfo.BUILD_ORIGIN_LOCAL, None

        self._refuse_stale_build_dir(buildinfo, package_dir)

        record = {
            "schema_version": buildinfo.BUILD_INFO_SCHEMA_VERSION,
            "name": buildinfo.PACKAGE_NAME,
            "version": version,
            "source_commit": source_commit,
            "source_dirty": source_dirty,
            "package_digest": buildinfo.compute_package_digest(package_dir),
            "build_origin": build_origin,
            "release_tag": release_tag,
        }
        try:
            buildinfo.validate_build_info(record, expected_version=version)
        except ValueError as exc:
            raise SetupError(f"refusing to build {buildinfo.BUILD_INFO_NAME}: {exc}") from None
        (package_dir / buildinfo.BUILD_INFO_NAME).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n")

    def _refuse_stale_build_dir(self, buildinfo, package_dir: Path) -> None:
        """A module deleted or renamed since an earlier in-tree build would
        otherwise stay in build_lib, ship, and be hashed into package_digest."""
        expected = {Path(p).resolve() for p in self.get_outputs(include_bytecode=False)}
        stray = []
        for dirpath, dirnames, filenames in os.walk(package_dir):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                path = Path(dirpath) / name
                if name.endswith(".pyc") or path == package_dir / buildinfo.BUILD_INFO_NAME:
                    continue
                if path.is_file() and path.resolve() not in expected:
                    stray.append(path)
        if stray:
            names = ", ".join(sorted(str(p) for p in stray))
            raise SetupError(f"stale build directory: {names} is not part of this build; "
                             f"delete build/ and rebuild")


def _dynamic_version() -> dict:
    """``{"version": ...}`` for a dynamic ``pyproject.toml``, else ``{}``
    (setuptools reads the static version itself)."""
    version = _load_module("version")
    try:
        if not version.declares_dynamic_version((SOURCE_DIR / "pyproject.toml").read_text(encoding="utf-8")):
            return {}
        if RELEASE_TAG_ENV in os.environ:
            return {"version": _load_module("buildinfo").version_for_tag(os.environ[RELEASE_TAG_ENV])}
        return {"version": version.local_build_version(SOURCE_DIR)}
    except (OSError, ValueError) as exc:
        raise SetupError(f"cannot derive the version: {exc}") from None


if __name__ == "__main__":  # setuptools.build_meta executes this file as __main__
    setup(cmdclass={"build_py": build_py}, **_dynamic_version())
