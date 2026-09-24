"""Build identity: the schema, digest and tag rules of ``controller/BUILD_INFO.json``.

``BUILD_INFO.json`` exists only inside built artifacts. ``setup.py``'s
``build_py`` hook writes it into ``build_lib/controller/`` and never into the
source tree. This module is dependency-free and imports nothing from the
package, because the hook loads it by file path, before the package itself is
importable.

``compute_package_digest`` is deliberately separate from
``identity.compute_tree_digest``: it ignores mode bits, so pip's installed
file modes cannot move it, and it excludes the build info file itself.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
from pathlib import Path

BUILD_INFO_NAME = "BUILD_INFO.json"
BUILD_INFO_SCHEMA_VERSION = 1
PACKAGE_NAME = "workflow-controller"

BUILD_ORIGIN_RELEASE = "release"
BUILD_ORIGIN_LOCAL = "local"
BUILD_ORIGINS = (BUILD_ORIGIN_RELEASE, BUILD_ORIGIN_LOCAL)

#: Plain ``MAJOR.MINOR.PATCH``. Every match is also a valid PEP 440 version,
#: so the wheel's ``Version:`` and the tag ``v<version>`` spell the same string.
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

_FIELDS = (
    "schema_version", "name", "version", "source_commit", "source_dirty", "package_digest",
    "build_origin", "release_tag",
)


@dataclasses.dataclass(frozen=True)
class BuildInfo:
    schema_version: int
    name: str
    version: str
    source_commit: str | None
    source_dirty: bool | None
    package_digest: str
    build_origin: str
    release_tag: str | None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def tag_for_version(version: str) -> str:
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise ValueError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    return f"v{version}"


def version_for_tag(tag: str) -> str:
    if not isinstance(tag, str) or not tag.startswith("v") or not SEMVER_RE.fullmatch(tag[1:]):
        raise ValueError(f"tag {tag!r} is not vMAJOR.MINOR.PATCH")
    return tag[1:]


def compute_package_digest(package_dir: Path) -> str:
    """SHA-256 over the canonical JSON of ``{relative POSIX path: sha256 of
    bytes}`` for every regular file under ``package_dir``, excluding
    ``BUILD_INFO.json`` at the root, any ``__pycache__/`` directory and any
    ``*.pyc``. Mode bits are ignored."""
    package_dir = Path(package_dir)
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(package_dir):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(package_dir).as_posix()
            if rel == BUILD_INFO_NAME or name.endswith(".pyc"):
                continue
            if not stat.S_ISREG(os.lstat(path).st_mode):
                continue
            entries[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest_of_file_hashes(entries)


def digest_of_file_hashes(entries: dict[str, str]) -> str:
    """The canonical form ``compute_package_digest`` hashes: ``entries``
    maps each included file's relative POSIX path to the SHA-256 hex of its
    bytes. ``tools/release.py`` feeds it a wheel's members directly."""
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_build_info(obj: object, *, expected_version: str) -> BuildInfo:
    """Return ``obj`` as a typed ``BuildInfo``, or raise ``ValueError``
    naming the first violated rule."""
    if not isinstance(obj, dict):
        raise ValueError(f"build info must be a JSON object, not {type(obj).__name__}")
    schema_version = obj.get("schema_version")
    if type(schema_version) is not int or schema_version != BUILD_INFO_SCHEMA_VERSION:
        raise ValueError(f"unknown build info schema_version {schema_version!r}")
    if set(obj) != set(_FIELDS):
        missing = sorted(set(_FIELDS) - set(obj))
        extra = sorted(set(obj) - set(_FIELDS))
        raise ValueError(f"build info fields are wrong (missing {missing}, unexpected {extra})")
    if obj["name"] != PACKAGE_NAME:
        raise ValueError(f"build info name {obj['name']!r} is not {PACKAGE_NAME!r}")

    version = obj["version"]
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise ValueError(f"build info version {version!r} is not MAJOR.MINOR.PATCH")
    if version != expected_version:
        raise ValueError(f"build info version {version!r} does not match {expected_version!r}")

    source_commit = obj["source_commit"]
    if source_commit is not None and not (isinstance(source_commit, str) and _COMMIT_RE.fullmatch(source_commit)):
        raise ValueError(f"build info source_commit {source_commit!r} is not null or a 40-hex commit")
    source_dirty = obj["source_dirty"]
    if source_dirty is not None and type(source_dirty) is not bool:
        raise ValueError(f"build info source_dirty {source_dirty!r} is not true, false or null")
    if (source_commit is None) != (source_dirty is None):
        raise ValueError("build info source_commit and source_dirty must be null together "
                         "(unknown provenance) or both known")

    package_digest = obj["package_digest"]
    if not (isinstance(package_digest, str) and _DIGEST_RE.fullmatch(package_digest)):
        raise ValueError(f"build info package_digest {package_digest!r} is not a 64-hex digest")

    build_origin = obj["build_origin"]
    if build_origin not in BUILD_ORIGINS:
        raise ValueError(f"build info build_origin {build_origin!r} is not one of {list(BUILD_ORIGINS)}")
    release_tag = obj["release_tag"]
    if build_origin == BUILD_ORIGIN_RELEASE:
        if release_tag is None:
            raise ValueError("a release build must carry a release_tag")
        if release_tag != tag_for_version(version):
            raise ValueError(f"release_tag {release_tag!r} does not match version {version!r} "
                             f"(expected {tag_for_version(version)!r})")
        if source_commit is None:
            raise ValueError("a release build must record its source_commit")
        if source_dirty is not False:
            raise ValueError("a release build must be built from a clean source tree")
    elif release_tag is not None:
        raise ValueError(f"a local build must not carry a release_tag (got {release_tag!r})")

    return BuildInfo(**{field: obj[field] for field in _FIELDS})
