#!/usr/bin/env python3
"""Vendored, hash-pinned Workflow release trees for the Controller's tests.
Stdlib only.

``tests/workflow_releases/<release>/`` holds, at their target-relative paths,
exactly the part of a released Workflow payload the Controller's checks and
disposable-repository tests read: the release's ``.claude/commands/*.md``
files (seventeen up to 2.7.0, twenty-two for 2.9.0),
``scripts/workflow_state.py``, ``scripts/workflow_fingerprint.py`` and
``scripts/prepare-ai-review.sh``, plus the optional protocol and gate-policy
scripts a release ships. A ``RELEASE.json`` beside them records the
release, the Workflow Manager source and commit the files were taken from,
and each file's ``sha256`` and executable flag, both equal to the Manager
manifest's entry for that ``target_path``. Tests read these trees, never
this repository's own installed Workflow (plan invariant I7).

- ``sync <release> --from <dir>`` copies the subset out of a Workflow Manager
  checkout's ``distribution/workflow`` directory (the one holding
  ``<release>/manifest.json``) and writes ``RELEASE.json`` from that
  release's manifest. The recorded commit is ``--manager-commit`` (default
  ``HEAD``), and it is refused unless the release's distribution directory
  in the working tree is identical to that commit's.
- ``check`` verifies every vendored file against its ``RELEASE.json``, that
  a tree holds nothing else, and that every admitted release
  (``managed_repo.VALIDATED_WORKFLOW_RELEASES``) has a tree. It exits ``1``
  naming every problem.

This tool writes only under ``tests/workflow_releases/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import managed_repo  # noqa: E402

RELEASES_DIR = Path("tests") / "workflow_releases"
RELEASE_FILE = "RELEASE.json"
SCHEMA_VERSION = 1

#: The three scripts every vendored tree carries.
VENDORED_SCRIPTS = (
    "scripts/prepare-ai-review.sh",
    "scripts/workflow_fingerprint.py",
    "scripts/workflow_state.py",
)
#: Present from the first release that ships the orchestration protocol (2.7.0):
#: vendored when the manifest has them, never required of an older release.
PROTOCOL_PATHS = (
    "scripts/workflow_protocol.py",
    "scripts/workflow_test_harness.py",
    "docs/ai-workflow/orchestration-protocol-v1.schema.json",
)
#: Present from the first release that ships the gate policy (2.8.0), which
#: ``workflow_protocol.py`` and ``workflow_state.py`` import at module level:
#: vendored when the manifest has them, never required of an older release.
GATE_POLICY_PATHS = (
    "scripts/workflow_forge.py",
    "scripts/workflow_gate_policy.py",
)
_COMMAND_FILE_RE = re.compile(r"\.claude/commands/[^/]+\.md")
_RELEASE_RE = re.compile(r"\d+\.\d+\.\d+")


def is_vendored_path(target_path: str) -> bool:
    """Whether a Manager manifest ``target_path`` belongs to the vendored
    subset: a command file, one of :data:`VENDORED_SCRIPTS`, or one of the
    optional :data:`PROTOCOL_PATHS` and :data:`GATE_POLICY_PATHS`."""
    return (
        target_path in VENDORED_SCRIPTS
        or target_path in PROTOCOL_PATHS
        or target_path in GATE_POLICY_PATHS
        or _COMMAND_FILE_RE.fullmatch(target_path) is not None
    )


def release_dir(release: str, root: Path = REPO_ROOT) -> Path:
    return root / RELEASES_DIR / release


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def load_manager_manifest(distribution: Path, release: str) -> dict:
    """``<distribution>/<release>/manifest.json``, checked to declare
    ``release``."""
    path = distribution / release / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("workflow_version") != release:
        raise ValueError(f"{path} declares workflow_version {manifest.get('workflow_version')!r}, "
                         f"not {release!r}")
    return manifest


def manifest_subset(manifest: dict) -> dict[str, dict]:
    """The Manager manifest's artifacts in the vendored subset, keyed by
    ``target_path``."""
    return {entry["target_path"]: entry for entry in manifest["artifacts"]
            if is_vendored_path(entry["target_path"])}


def _manager_provenance(distribution: Path, release: str, commit: str) -> tuple[str, str]:
    """``(full commit, Manager-relative payload path)``, refused unless the
    release's distribution directory in the working tree equals that
    commit's, untracked files included."""
    release_path = distribution / release
    resolved = _git(["rev-parse", "--verify", f"{commit}^{{commit}}"], release_path)
    if resolved.returncode != 0:
        raise ValueError(f"{commit!r} is not a commit of the Workflow Manager checkout at {distribution}: "
                         f"{resolved.stderr.strip()}")
    full = resolved.stdout.strip()
    if _git(["diff", "--quiet", full, "--", "."], release_path).returncode != 0:
        raise ValueError(f"{release_path} differs from commit {full}")
    untracked = _git(["status", "--porcelain", "--untracked-files=all", "--", "."], release_path)
    if untracked.returncode != 0 or untracked.stdout.strip():
        raise ValueError(f"{release_path} has uncommitted or untracked files:\n{untracked.stdout}")
    prefix = _git(["rev-parse", "--show-prefix"], release_path).stdout.strip()
    return full, f"{prefix}payload"


def sync(release: str, distribution: Path, *, manager_commit: str = "HEAD",
         archive_sha256: str | None = None, root: Path = REPO_ROOT) -> Path:
    """Replace ``tests/workflow_releases/<release>/`` with the vendored
    subset of the Manager's ``<distribution>/<release>`` payload."""
    if not _RELEASE_RE.fullmatch(release):
        raise ValueError(f"{release!r} is not a dotted release")
    manifest = load_manager_manifest(distribution, release)
    if archive_sha256 is None:
        commit, source = _manager_provenance(distribution, release, manager_commit)
    else:
        # A published archive unpacked under ``<distribution>/<release>``: its
        # provenance is the archive's own digest, not a Manager commit.
        commit, source = f"archive-sha256:{archive_sha256}", f"workflow-{release}.tar.gz"
    subset = manifest_subset(manifest)
    missing = [path for path in VENDORED_SCRIPTS if path not in subset]
    if missing or not any(_COMMAND_FILE_RE.fullmatch(path) for path in subset):
        raise ValueError(f"the {release} manifest lacks part of the vendored subset: {missing or 'commands'}")
    files: dict[str, dict] = {}
    payloads: dict[str, bytes] = {}
    for target_path, entry in sorted(subset.items()):
        data = (distribution / release / entry["location"]).read_bytes()
        if _sha256(data) != entry["sha256"]:
            raise ValueError(f"{release} {target_path}: payload sha256 {_sha256(data)} "
                             f"differs from the manifest's {entry['sha256']}")
        payloads[target_path] = data
        files[target_path] = {"sha256": entry["sha256"], "executable": bool(entry["executable"])}

    dest = release_dir(release, root)
    if dest.exists():
        shutil.rmtree(dest)
    for target_path, data in payloads.items():
        path = dest / target_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if files[target_path]["executable"] else 0o644)
    record = {
        "schema_version": SCHEMA_VERSION,
        "workflow_version": release,
        "manager_source": source,
        "manager_commit": commit,
        "files": files,
    }
    (dest / RELEASE_FILE).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def load_release(release: str, root: Path = REPO_ROOT) -> dict:
    return json.loads((release_dir(release, root) / RELEASE_FILE).read_text(encoding="utf-8"))


def _check_tree(release: str, root: Path) -> list[str]:
    dest = release_dir(release, root)
    try:
        record = load_release(release, root)
    except (OSError, ValueError) as exc:
        return [f"{release}: {RELEASE_FILE} could not be read: {exc}"]
    problems = []
    if record.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{release}: {RELEASE_FILE} schema_version is {record.get('schema_version')!r}")
    if record.get("workflow_version") != release:
        problems.append(f"{release}: {RELEASE_FILE} declares workflow_version {record.get('workflow_version')!r}")
    files = record.get("files")
    if not isinstance(files, dict):
        return [*problems, f"{release}: {RELEASE_FILE} has no files mapping"]
    for path in VENDORED_SCRIPTS:
        if path not in files:
            problems.append(f"{release}: {path} is not recorded")
    for target_path, entry in sorted(files.items()):
        if not is_vendored_path(target_path):
            problems.append(f"{release}: {target_path} is outside the vendored subset")
            continue
        path = dest / target_path
        if path.is_symlink() or not path.is_file():
            problems.append(f"{release}: {target_path} is missing or not a regular file")
            continue
        digest = _sha256(path.read_bytes())
        if digest != entry.get("sha256"):
            problems.append(f"{release}: {target_path} sha256 {digest} != recorded {entry.get('sha256')}")
        executable = bool(path.stat().st_mode & 0o100)
        if executable != entry.get("executable"):
            problems.append(f"{release}: {target_path} executable is {executable}, "
                            f"recorded {entry.get('executable')!r}")
    expected = {RELEASE_FILE, *files}
    for path in sorted(dest.rglob("*")):
        rel = path.relative_to(dest).as_posix()
        if (path.is_symlink() or not path.is_dir()) and rel not in expected:
            problems.append(f"{release}: {rel} is not a vendored file")
    return problems


def check(root: Path = REPO_ROOT) -> list[str]:
    """Every problem with the vendored trees under ``root``: a file that
    differs from its ``RELEASE.json`` record, a file the record does not
    name, and an admitted release with no tree."""
    base = root / RELEASES_DIR
    releases = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    problems = [f"admitted release {release} has no vendored tree under {RELEASES_DIR}"
                for release in sorted(managed_repo.VALIDATED_WORKFLOW_RELEASES) if release not in releases]
    for release in releases:
        problems.extend(_check_tree(release, root))
    return problems


def manifest_mismatches(release: str, distribution: Path, root: Path = REPO_ROOT) -> list[str]:
    """Every difference between a vendored tree's ``RELEASE.json`` and the
    Manager manifest's vendored subset for the same release."""
    subset = manifest_subset(load_manager_manifest(distribution, release))
    files = load_release(release, root)["files"]
    problems = [f"{release}: {path} is in the Manager manifest but not vendored"
                for path in sorted(set(subset) - set(files))]
    problems += [f"{release}: {path} is vendored but not in the Manager manifest"
                 for path in sorted(set(files) - set(subset))]
    for path in sorted(set(files) & set(subset)):
        manager = {"sha256": subset[path]["sha256"], "executable": bool(subset[path]["executable"])}
        if files[path] != manager:
            problems.append(f"{release}: {path} recorded {files[path]} != Manager manifest {manager}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    sync_parser = commands.add_parser("sync", help="vendor one release from a Workflow Manager checkout")
    sync_parser.add_argument("release")
    sync_parser.add_argument("--from", dest="distribution", type=Path, required=True,
                             help="the Manager checkout's distribution/workflow directory")
    sync_parser.add_argument("--archive-sha256", help="the published archive's sha256: record it as the "
                             "provenance of a tree unpacked from a release archive")
    sync_parser.add_argument("--manager-commit", default="HEAD",
                             help="the Manager commit to record (default: HEAD)")
    commands.add_parser("check", help="verify every vendored tree against its RELEASE.json")
    args = parser.parse_args(argv)
    if args.command == "sync":
        try:
            dest = sync(args.release, args.distribution, manager_commit=args.manager_commit,
                        archive_sha256=args.archive_sha256, root=args.root)
        except (OSError, ValueError, KeyError) as exc:
            print(f"sync {args.release}: {exc}", file=sys.stderr)
            return 1
        print(f"vendored Workflow {args.release} into {dest.relative_to(args.root)}")
        return 0
    problems = check(args.root)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
