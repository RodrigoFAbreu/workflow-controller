#!/usr/bin/env python3
"""Release tooling for the Workflow Controller. Stdlib only.

The CI and release workflows call these subcommands; each one either succeeds
or exits ``1`` with a single line naming the check that failed:

- ``version`` prints ``controller.version.__version__``;
- ``verify-tag TAG`` checks ``TAG`` is ``v<version>`` and that
  ``pyproject.toml`` still reads the version dynamically from
  ``controller/version.py``;
- ``verify-wheel WHEEL (--tag TAG | --local) --commit SHA`` checks a built
  wheel's name, metadata, entry point, required and forbidden files, its
  ``BUILD_INFO.json`` and the package digest recomputed from the wheel's own
  members;
- ``check-unpublished TAG`` refuses unless ``gh`` says the release does not
  exist. Any other ``gh`` failure is undecidable and refused (fail closed);
- ``verify-tag-commit TAG SHA`` asks the remote which commit ``TAG`` names
  now, peeling an annotated tag, and refuses unless it is ``SHA``;
- ``checksums DIR`` writes ``DIR/SHA256SUMS`` in ``sha256sum`` format.

The ``gh`` and Git runners are injectable so the tests never reach a network.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import buildinfo  # noqa: E402
from controller import version as version_module  # noqa: E402

PYPROJECT = REPO_ROOT / "pyproject.toml"
VERSION_ATTR = "controller.version.__version__"
ENTRY_POINT_NAME = "workflow-controller"
ENTRY_POINT_TARGET = "controller.cli:main"
PACKAGE_DIR = "controller"
REQUIRED_MEMBERS = (f"{PACKAGE_DIR}/GENERATION.json", f"{PACKAGE_DIR}/{buildinfo.BUILD_INFO_NAME}")
SOURCE_PIN_NAME = "SOURCE_PIN.json"
CHECKSUMS_NAME = "SHA256SUMS"
GH_NOT_FOUND = "release not found"

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_LS_REMOTE_LINE_RE = re.compile(r"([0-9a-f]{40})\t(\S+)")

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class Refusal(Exception):
    """A failed release check: ``check`` names it, ``detail`` says why."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _is_semver(text: object) -> bool:
    # fullmatch, not SEMVER_RE.match: ``$`` also matches before a trailing newline.
    return isinstance(text, str) and buildinfo.SEMVER_RE.fullmatch(text) is not None


def _require_tag_format(tag: str) -> None:
    if not (tag.startswith("v") and _is_semver(tag[1:])):
        raise Refusal("tag format", f"{tag!r} is not vMAJOR.MINOR.PATCH")


def _require_commit(sha: str) -> None:
    if not _COMMIT_RE.fullmatch(sha):
        raise Refusal("commit format", f"{sha!r} is not a 40-hex commit")


# --- verify-tag ------------------------------------------------------------

def verify_tag(tag: str, *, version: str, pyproject: Path = PYPROJECT) -> None:
    _require_tag_format(tag)
    if tag != f"v{version}":
        raise Refusal("tag/version", f"tag {tag!r} does not match controller version {version!r} "
                                     f"(expected 'v{version}')")
    try:
        project_file = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise Refusal("pyproject version source", f"cannot read {pyproject}: {exc}") from None
    project = project_file.get("project", {})
    if "version" in project:
        raise Refusal("pyproject version source",
                      f"[project] declares a static version {project['version']!r}")
    if "version" not in project.get("dynamic", []):
        raise Refusal("pyproject version source", "[project] dynamic does not include 'version'")
    dynamic = project_file.get("tool", {}).get("setuptools", {}).get("dynamic", {})
    if dynamic.get("version") != {"attr": VERSION_ATTR}:
        raise Refusal("pyproject version source",
                      f"[tool.setuptools.dynamic] version is {dynamic.get('version')!r}, "
                      f"not {{attr = {VERSION_ATTR!r}}}")


# --- verify-wheel ----------------------------------------------------------

def _metadata_field(text: str, field: str) -> str | None:
    # Core metadata headers end at the first blank line; the body follows.
    for line in text.split("\n"):
        if not line.strip():
            return None
        name, sep, value = line.partition(":")
        if sep and name == field:
            return value.strip()
    return None


def _wheel_digest(zf: zipfile.ZipFile) -> str:
    prefix = f"{PACKAGE_DIR}/"
    entries: dict[str, str] = {}
    for info in zf.infolist():
        if info.is_dir() or not info.filename.startswith(prefix):
            continue
        rel = info.filename[len(prefix):]
        if rel == buildinfo.BUILD_INFO_NAME:
            continue
        entries[rel] = hashlib.sha256(zf.read(info)).hexdigest()
    return buildinfo.digest_of_file_hashes(entries)


def verify_wheel(wheel: Path, *, version: str, commit: str, tag: str | None) -> buildinfo.BuildInfo:
    """``tag`` is the release tag for a release wheel, ``None`` for ``--local``."""
    _require_commit(commit)
    if tag is not None:
        _require_tag_format(tag)

    expected_name = f"workflow_controller-{version}-py3-none-any.whl"
    if wheel.name != expected_name:
        raise Refusal("wheel filename", f"{wheel.name!r} is not {expected_name!r}")
    try:
        zf = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise Refusal("wheel archive", f"cannot open {wheel}: {exc}") from None

    with zf:
        names = set(zf.namelist())
        dist_info = f"workflow_controller-{version}.dist-info"

        metadata_name = f"{dist_info}/METADATA"
        if metadata_name not in names:
            raise Refusal("wheel metadata", f"{metadata_name} is missing")
        metadata = zf.read(metadata_name).decode("utf-8", errors="replace")
        if _metadata_field(metadata, "Name") != buildinfo.PACKAGE_NAME:
            raise Refusal("wheel metadata", f"Name is {_metadata_field(metadata, 'Name')!r}, "
                                            f"not {buildinfo.PACKAGE_NAME!r}")
        if _metadata_field(metadata, "Version") != version:
            raise Refusal("wheel metadata", f"Version is {_metadata_field(metadata, 'Version')!r}, "
                                            f"not {version!r}")

        entry_points_name = f"{dist_info}/entry_points.txt"
        parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
        parser.optionxform = str
        try:
            parser.read_string(zf.read(entry_points_name).decode("utf-8"))
        except KeyError:
            raise Refusal("entry point", f"{entry_points_name} is missing") from None
        except (configparser.Error, UnicodeDecodeError) as exc:
            raise Refusal("entry point", f"{entry_points_name} is unreadable: {exc}") from None
        declared = parser.get("console_scripts", ENTRY_POINT_NAME, fallback=None)
        if declared != ENTRY_POINT_TARGET:
            raise Refusal("entry point", f"console script {ENTRY_POINT_NAME!r} is {declared!r}, "
                                         f"not {ENTRY_POINT_TARGET!r}")

        for required in REQUIRED_MEMBERS:
            if required not in names:
                raise Refusal("required files", f"{required} is missing")
        for name in sorted(names):
            parts = PurePosixPath(name).parts
            if parts and parts[-1] == SOURCE_PIN_NAME:
                raise Refusal("forbidden files", f"{name} must not ship")
            if "__pycache__" in parts or name.endswith(".pyc"):
                raise Refusal("forbidden files", f"{name} is bytecode and must not ship")

        try:
            record = json.loads(zf.read(REQUIRED_MEMBERS[1]))
            info = buildinfo.validate_build_info(record, expected_version=version)
        except (ValueError, UnicodeDecodeError) as exc:
            raise Refusal("build info", str(exc)) from None

        actual_digest = _wheel_digest(zf)
    if actual_digest != info.package_digest:
        raise Refusal("package digest", f"the wheel's {PACKAGE_DIR}/ digests to {actual_digest}, "
                                        f"BUILD_INFO records {info.package_digest}")
    if info.source_commit != commit:
        raise Refusal("source commit", f"built from {info.source_commit!r}, expected {commit!r}")
    if info.source_dirty is not False:
        raise Refusal("source dirty", f"source_dirty is {info.source_dirty!r}, not false")
    if tag is None:
        if info.build_origin != buildinfo.BUILD_ORIGIN_LOCAL:
            raise Refusal("build origin", f"--local expects a local build, got "
                                          f"{info.build_origin!r} ({info.release_tag!r})")
    elif info.build_origin != buildinfo.BUILD_ORIGIN_RELEASE or info.release_tag != tag:
        raise Refusal("build origin", f"--tag {tag} expects a release build for {tag!r}, got "
                                      f"{info.build_origin!r} ({info.release_tag!r})")
    return info


# --- check-unpublished -----------------------------------------------------

def check_unpublished(tag: str, *, run_gh: Runner = _run) -> None:
    _require_tag_format(tag)
    try:
        result = run_gh(["gh", "release", "view", tag, "--json", "tagName"])
    except OSError as exc:
        raise Refusal("release lookup undecidable", f"cannot run gh: {exc}") from None
    if result.returncode == 0:
        raise Refusal("already published", f"a GitHub Release for {tag} already exists")
    lines = [line.strip() for line in (result.stderr or "").splitlines()]
    if GH_NOT_FOUND not in lines:
        detail = " | ".join(line for line in lines if line) or "no output"
        raise Refusal("release lookup undecidable",
                      f"gh release view exited {result.returncode}: {detail}")


# --- verify-tag-commit -----------------------------------------------------

def remote_tag_commit(tag: str, *, run_git: Runner = _run) -> str:
    """The commit ``TAG`` names on ``origin`` now: the peeled ``^{}`` line
    for an annotated tag, the direct line otherwise."""
    direct_ref, peeled_ref = f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"
    try:
        result = run_git(["git", "ls-remote", "origin", direct_ref, peeled_ref])
    except OSError as exc:
        raise Refusal("tag commit undecidable", f"cannot run git: {exc}") from None
    if result.returncode != 0:
        detail = (result.stderr or "").strip().replace("\n", " | ") or "no output"
        raise Refusal("tag commit undecidable", f"git ls-remote exited {result.returncode}: {detail}")
    found: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        if not line.strip():
            continue
        match = _LS_REMOTE_LINE_RE.fullmatch(line)
        if match is None:
            raise Refusal("tag commit undecidable", f"malformed ls-remote line {line!r}")
        sha, ref = match.groups()
        if ref not in (direct_ref, peeled_ref) or ref in found:
            raise Refusal("tag commit undecidable", f"unexpected ls-remote line {line!r}")
        found[ref] = sha
    if direct_ref not in found:
        raise Refusal("tag commit undecidable", f"origin has no {direct_ref}")
    return found.get(peeled_ref, found[direct_ref])


def verify_tag_commit(tag: str, sha: str, *, run_git: Runner = _run) -> None:
    _require_tag_format(tag)
    _require_commit(sha)
    current = remote_tag_commit(tag, run_git=run_git)
    if current != sha:
        raise Refusal("tag moved", f"{tag} now names {current}, the build is of {sha}")


# --- checksums -------------------------------------------------------------

def checksums(directory: Path) -> Path:
    """Write ``SHA256SUMS`` for every regular file in ``directory`` except
    ``SHA256SUMS`` itself, which is excluded by name and replaced."""
    if not directory.is_dir():
        raise Refusal("checksums", f"{directory} is not a directory")
    lines = []
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        if path.name == CHECKSUMS_NAME or not stat.S_ISREG(os.lstat(path).st_mode):
            continue
        if "\n" in path.name or "\\" in path.name:
            raise Refusal("checksums", f"{path.name!r} cannot be listed in sha256sum format")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    target = directory / CHECKSUMS_NAME
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{CHECKSUMS_NAME}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


# --- CLI -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the controller version")
    tag = sub.add_parser("verify-tag", help="check TAG is v<version>")
    tag.add_argument("tag")
    wheel = sub.add_parser("verify-wheel", help="verify a built wheel")
    wheel.add_argument("wheel", type=Path)
    mode = wheel.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tag", help="a release wheel built for TAG")
    mode.add_argument("--local", action="store_true", help="a local (non-release) wheel")
    wheel.add_argument("--commit", required=True, help="the commit the wheel must be built from")
    unpublished = sub.add_parser("check-unpublished", help="refuse if TAG's release exists")
    unpublished.add_argument("tag")
    tag_commit = sub.add_parser("verify-tag-commit", help="refuse unless origin's TAG names SHA")
    tag_commit.add_argument("tag")
    tag_commit.add_argument("sha")
    sums = sub.add_parser("checksums", help="write DIR/SHA256SUMS")
    sums.add_argument("directory", type=Path)
    return parser


def main(argv: list[str] | None = None, *, run_gh: Runner = _run, run_git: Runner = _run) -> int:
    args = build_parser().parse_args(argv)
    version = version_module.__version__
    try:
        if args.command == "version":
            print(version)
        elif args.command == "verify-tag":
            verify_tag(args.tag, version=version)
            print(f"ok: {args.tag} matches version {version}")
        elif args.command == "verify-wheel":
            verify_wheel(args.wheel, version=version, commit=args.commit, tag=args.tag)
            print(f"ok: {args.wheel.name} verified")
        elif args.command == "check-unpublished":
            check_unpublished(args.tag, run_gh=run_gh)
            print(f"ok: no GitHub Release exists for {args.tag}")
        elif args.command == "verify-tag-commit":
            verify_tag_commit(args.tag, args.sha, run_git=run_git)
            print(f"ok: {args.tag} names {args.sha}")
        elif args.command == "checksums":
            print(f"ok: wrote {checksums(args.directory)}")
    except Refusal as refusal:
        print(f"release.py {args.command}: refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
