#!/usr/bin/env python3
"""Release tooling for the Workflow Controller. Stdlib only.

The CI workflows call these subcommands; each one either succeeds or exits
``1`` with a single line naming the check that failed. The release
transaction itself is generic (``controller/release_txn.py``) and reads
everything adopter-specific from the committed
``.workflow-controller/policy.json``; this file is its reference adopter's
thin CLI:

- ``version`` prints the version the policy's version source declares at the
  checked-out commit (``HEAD``'s committed tree);
- ``classify --commit C`` classifies trunk commit ``C`` and prints the
  ``state``, ``version``, ``tag`` and target ``commit`` as ``key=value``
  lines (also appended to ``$GITHUB_OUTPUT`` when set). A failing state
  exits ``1``;
- ``build`` runs the policy's ``build`` command at the checked-out commit;
- ``verify`` runs the policy's ``verify`` command on every built artifact
  for the checked-out commit;
- ``publish --commit TARGET`` recomputes the classification of the
  checked-out trunk commit and, for ``RELEASE_DUE`` or ``RESUME`` targeting
  ``TARGET``, tags (after validation, never moving a tag), publishes or
  completes a draft, and verifies the published release;
- ``verify-wheel WHEEL (--tag TAG | --local) --commit SHA`` checks a built
  wheel's name, metadata, entry point, required and forbidden files, its
  ``BUILD_INFO.json`` and the package digest recomputed from the wheel's own
  members (the reference policy's ``verify`` command);
- ``checksums DIR`` writes ``DIR/SHA256SUMS`` in ``sha256sum`` format;
- ``check-title TITLE`` validates a pull request title against the policy
  committed at ``HEAD`` (for a ``pull_request`` run, the merge ref): under
  the ``conventional_commit`` trigger it prints ``ok: <type> → <bump>`` or
  refuses; under ``version_change`` the title is not release input.

There is no tag-first subcommand: a release tag is created only by
``publish``, after validation, so a hand-pushed ``v*`` tag triggers nothing.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import buildinfo  # noqa: E402
from controller import conventional_commit  # noqa: E402
from controller import forge as forge_module  # noqa: E402
from controller import gitrepo, release_txn, repo_policy  # noqa: E402
from controller import version as version_module  # noqa: E402
from controller.errors import ControllerError  # noqa: E402

ENTRY_POINT_NAME = "workflow-controller"
ENTRY_POINT_TARGET = "controller.cli:main"
PACKAGE_DIR = "controller"
REQUIRED_MEMBERS = (f"{PACKAGE_DIR}/GENERATION.json", f"{PACKAGE_DIR}/{buildinfo.BUILD_INFO_NAME}")
SOURCE_PIN_NAME = "SOURCE_PIN.json"
CHECKSUMS_NAME = "SHA256SUMS"
#: The identity a new release tag is created with on GitHub Actions.
TAGGER = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class Refusal(Exception):
    """A failed release check: ``check`` names it, ``detail`` says why."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


def _is_semver(text: object) -> bool:
    # fullmatch, not SEMVER_RE.match: ``$`` also matches before a trailing newline.
    return isinstance(text, str) and buildinfo.SEMVER_RE.fullmatch(text) is not None


def _require_tag_format(tag: str) -> None:
    if not (tag.startswith("v") and _is_semver(tag[1:])):
        raise Refusal("tag format", f"{tag!r} is not vMAJOR.MINOR.PATCH")


def _require_commit(sha: str) -> None:
    if not _COMMIT_RE.fullmatch(sha):
        raise Refusal("commit format", f"{sha!r} is not a 40-hex commit")


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
    sub.add_parser("version", help="print the policy's version at the checked-out commit")
    classify = sub.add_parser("classify", help="classify a trunk commit for release")
    classify.add_argument("--commit", required=True, help="the trunk commit to classify")
    sub.add_parser("build", help="run the policy's build command at the checked-out commit")
    sub.add_parser("verify", help="run the policy's verify command on every built artifact")
    publish = sub.add_parser("publish", help="tag and publish the checked-out trunk commit's release")
    publish.add_argument("--commit", required=True, help="the target commit the build job built")
    wheel = sub.add_parser("verify-wheel", help="verify a built wheel")
    wheel.add_argument("wheel", type=Path)
    mode = wheel.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tag", help="a release wheel built for TAG")
    mode.add_argument("--local", action="store_true", help="a local (non-release) wheel")
    wheel.add_argument("--commit", required=True, help="the commit the wheel must be built from")
    sums = sub.add_parser("checksums", help="write DIR/SHA256SUMS")
    sums.add_argument("directory", type=Path)
    title = sub.add_parser("check-title", help="validate a pull request title against the committed policy")
    title.add_argument("title")
    return parser


def checked_out_version(repo_root: Path = REPO_ROOT) -> str:
    """The checked-out ``<repo_root>/pyproject.toml``'s static version."""
    try:
        return version_module.source_version(repo_root)
    except ValueError as exc:
        raise Refusal("version source", str(exc)) from None


# --- the release transaction ------------------------------------------------

def committed_policy(repo_root: Path, rev: str = "HEAD") -> repo_policy.RepositoryPolicy:
    """The admissible policy committed at ``rev``; an absent one refuses."""
    try:
        policy = repo_policy.read_committed_policy(repo_root, rev)
    except ControllerError as exc:
        raise Refusal("repository policy", str(exc)) from None
    if policy is None:
        raise Refusal("repository policy", f"{rev} has no {repo_policy.POLICY_PATH}")
    return policy


def policy_version(repo_root: Path, rev: str = "HEAD") -> str:
    """The version the policy committed at ``rev`` reads from ``rev``."""
    try:
        return repo_policy.read_committed_version(repo_root, committed_policy(repo_root, rev), rev)
    except ControllerError as exc:
        raise Refusal("version source", str(exc)) from None


def release_context(repo_root: Path, rev: str = "HEAD") -> release_txn.ReleaseContext:
    policy = committed_policy(repo_root, rev)
    return release_txn.ReleaseContext(repo_root=repo_root, policy=policy,
                                      forge=forge_module.GhForge(policy.forge_repository))


def _head(repo_root: Path) -> str:
    commit = gitrepo.head_state(repo_root).commit
    if commit is None:
        raise Refusal("checkout", "HEAD has no commit")
    return commit


def write_outputs(outputs: dict[str, str]) -> None:
    """Print ``key=value`` lines, and append them to ``$GITHUB_OUTPUT``."""
    lines = "".join(f"{key}={value}\n" for key, value in outputs.items())
    sys.stdout.write(lines)
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(lines)


def cmd_classify(repo_root: Path, commit: str) -> None:
    _require_commit(commit)
    state = release_txn.classify(release_context(repo_root, commit), commit)
    write_outputs(state.outputs())
    if not state.succeeded:
        raise Refusal(state.state, state.detail)
    print(f"ok: {state.state}: {state.detail}", file=sys.stderr)


def cmd_build(repo_root: Path) -> None:
    head = _head(repo_root)
    for path in release_txn.build(release_context(repo_root), head):
        print(f"ok: built {path.relative_to(repo_root)}")


def cmd_verify(repo_root: Path) -> None:
    head = _head(repo_root)
    for path in release_txn.verify(release_context(repo_root), head):
        print(f"ok: {path.relative_to(repo_root)} verified for {head}")


def cmd_publish(repo_root: Path, target: str) -> None:
    _require_commit(target)
    outcome = release_txn.publish(release_context(repo_root), _head(repo_root), target, tagger=TAGGER)
    print(f"ok: {outcome.tag} at {outcome.target}: {outcome.action} ({outcome.url})")


def check_title(repo_root: Path, title: str) -> str:
    """The ``ok:`` line for ``title`` under the policy committed at ``HEAD``."""
    release = committed_policy(repo_root).release
    if release.trigger != repo_policy.TRIGGER_CONVENTIONAL_COMMIT:
        return f"ok: the release trigger is {release.trigger}; the title is not release input"
    try:
        bump = conventional_commit.bump(title, release.change_types)
    except ControllerError as exc:
        raise Refusal("title", exc.message) from None
    subject = conventional_commit.parse(title)
    return f"ok: {subject.type}{'!' if subject.breaking else ''} → {bump}"


def main(argv: list[str] | None = None, *, repo_root: Path = REPO_ROOT) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "version":
            print(policy_version(repo_root))
        elif args.command == "classify":
            cmd_classify(repo_root, args.commit)
        elif args.command == "build":
            cmd_build(repo_root)
        elif args.command == "verify":
            cmd_verify(repo_root)
        elif args.command == "publish":
            cmd_publish(repo_root, args.commit)
        elif args.command == "verify-wheel":
            version = checked_out_version(repo_root)
            verify_wheel(args.wheel, version=version, commit=args.commit, tag=args.tag)
            print(f"ok: {args.wheel.name} verified")
        elif args.command == "checksums":
            print(f"ok: wrote {checksums(args.directory)}")
        elif args.command == "check-title":
            print(check_title(repo_root, args.title))
    except (Refusal, ControllerError) as exc:
        reason = f"{exc.code}: {exc}" if isinstance(exc, ControllerError) else str(exc)
        reason = " | ".join(line for line in reason.splitlines() if line.strip())
        print(f"release.py {args.command}: refused: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
