"""The release transaction (``workflow-controller-trunk-branch-pr-release-
orchestration`` CP4, "Release transaction").

A target repository whose committed policy enables ``release`` publishes a
release exactly when a trunk commit carries a version that has none yet.
:func:`classify` decides what a trunk commit ``C`` calls for -- desired
state, compared with the release tags in ``C``'s own history, never with the
previous push -- and :func:`publish` carries out a publishing
classification: tag after validation, publish, or resume an interrupted
publication at the tag's own commit.

Everything adopter-specific comes from the policy (:mod:`controller.
repo_policy`): the version source, the tag format, the ``build`` and
``verify`` commands, the artifact paths and the checksums file name. Git
goes through :mod:`controller.gitrepo` and GitHub through
:mod:`controller.forge`, so nothing here can force, move or delete a tag or
a release (I2, I8). Every undecidable read refuses (I9).
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath

from . import gitrepo, repo_policy, runtime
from .errors import GitOperationError, ReleaseTransactionError
from .forge import Forge, Release

# -- classification states, in the plan's row order --------------------------
COLLISION_TAG_ELSEWHERE = "COLLISION_TAG_ELSEWHERE"
ABANDONED_VERSION = "ABANDONED_VERSION"
ABANDONED_TAG_INCONSISTENT = "ABANDONED_TAG_INCONSISTENT"
ALREADY_RELEASED = "ALREADY_RELEASED"
RELEASE_MISMATCH = "RELEASE_MISMATCH"
NO_CHANGE = "NO_CHANGE"
COLLISION_RELEASE_WITHOUT_TAG = "COLLISION_RELEASE_WITHOUT_TAG"
BASELINE_UNRELEASED = "BASELINE_UNRELEASED"
RESUME = "RESUME"
INVALID_TRANSITION = "INVALID_TRANSITION"
RELEASE_DUE = "RELEASE_DUE"

STATES = (COLLISION_TAG_ELSEWHERE, ABANDONED_VERSION, ABANDONED_TAG_INCONSISTENT, ALREADY_RELEASED,
          RELEASE_MISMATCH, NO_CHANGE, COLLISION_RELEASE_WITHOUT_TAG, BASELINE_UNRELEASED, RESUME,
          INVALID_TRANSITION, RELEASE_DUE)
#: The states a CI run succeeds on.
SUCCESS_STATES = frozenset({ABANDONED_VERSION, ALREADY_RELEASED, NO_CHANGE, RESUME, RELEASE_DUE})
#: The states :func:`publish` acts on.
PUBLISHING_STATES = frozenset({RESUME, RELEASE_DUE})

#: The one tag refspec :func:`gitrepo.fetch` admits.
TAGS_REFSPEC = "refs/tags/*:refs/tags/*"

#: A build or verify command is still a bounded wait.
COMMAND_TIMEOUT_SECONDS = 1800

_CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")

#: ``(argv, extra environment, cwd) -> CompletedProcess`` for the policy's
#: ``build`` and ``verify`` commands.
CommandRunner = Callable[[Sequence[str], Mapping[str, str], Path], subprocess.CompletedProcess]


def run_command(argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a policy command without a shell, in ``cwd``, with ``env`` over
    ``os.environ`` and stdin closed."""
    return subprocess.run(list(argv), cwd=cwd, env={**os.environ, **env}, capture_output=True,
                          stdin=subprocess.DEVNULL, check=False, timeout=COMMAND_TIMEOUT_SECONDS)


@dataclasses.dataclass(frozen=True)
class ReleaseContext:
    """What the transaction acts on: the repository, the policy committed at
    the classified commit, the forge, and the injectable runners."""

    repo_root: Path
    policy: repo_policy.RepositoryPolicy
    forge: Forge
    git_runner: gitrepo.Runner | None = None
    command_runner: CommandRunner = run_command

    @property
    def release(self) -> repo_policy.Release:
        return self.policy.release

    @property
    def remote(self) -> str:
        return self.policy.trunk_remote


@dataclasses.dataclass(frozen=True)
class Classification:
    """The state of trunk commit ``commit`` for ``version``.

    ``target`` is the commit a publishing state builds, verifies and
    publishes: the tag's own commit when the tag exists (``RESUME`` at a
    strict ancestor included), ``commit`` otherwise. ``unsettled`` names the
    tags behind ``BASELINE_UNRELEASED``; ``problems`` the asset-set failures
    behind ``RELEASE_MISMATCH``."""

    state: str
    version: str
    tag: str
    commit: str
    target: str
    tag_commit: str | None
    release: Release | None
    detail: str
    unsettled: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.state in SUCCESS_STATES

    @property
    def publishes(self) -> bool:
        return self.state in PUBLISHING_STATES

    def outputs(self) -> dict[str, str]:
        """The CI outputs: ``commit`` is the target commit."""
        return {"state": self.state, "version": self.version, "tag": self.tag, "commit": self.target}


@dataclasses.dataclass(frozen=True)
class PublishOutcome:
    """``action`` is ``created`` (a new release), ``resumed_draft`` (a draft
    completed and published) or ``already_published`` (nothing to do)."""

    state: str
    tag: str
    target: str
    action: str
    tagged: bool
    uploaded: tuple[str, ...]
    url: str


def _refuse(message: str, **evidence) -> ReleaseTransactionError:
    return ReleaseTransactionError(message, evidence=evidence)


# ---------------------------------------------------------------------------
# Artifacts, checksums and the policy's commands.
# ---------------------------------------------------------------------------


def _values(ctx: ReleaseContext, version: str, commit: str) -> dict[str, str]:
    return {"version": version, "tag": ctx.release.tag_for(version), "commit": commit}


def artifact_paths(ctx: ReleaseContext, version: str) -> list[str]:
    """The policy's artifact paths for ``version``, relative to the
    repository root. Their file names are the release's asset names, so two
    paths sharing a file name refuse."""
    paths = [repo_policy.render(path, {"version": version}) for path in ctx.release.artifact_paths]
    names = [PurePosixPath(path).name for path in paths]
    if len(set(names)) != len(names) or ctx.release.checksums in names:
        raise _refuse(f"the artifact paths {paths} do not have distinct file names apart from "
                      f"{ctx.release.checksums}", paths=paths)
    return paths


def asset_names(ctx: ReleaseContext, version: str) -> list[str]:
    """Every asset a complete release holds: the artifacts' file names, then
    the checksums file."""
    return [PurePosixPath(path).name for path in artifact_paths(ctx, version)] + [ctx.release.checksums]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def render_checksums(digests: Mapping[str, str]) -> bytes:
    """``sha256sum`` format, sorted by name."""
    return "".join(f"{digests[name]}  {name}\n" for name in sorted(digests)).encode()


def parse_checksums(data: bytes) -> dict[str, str]:
    """``{name: sha256}`` from ``sha256sum`` output. Raises ``ValueError``
    for a malformed line or a name listed twice."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not UTF-8: {exc}") from None
    listing: dict[str, str] = {}
    for line in text.splitlines():
        match = _CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"malformed line {line!r}")
        digest, name = match.groups()
        if name in listing:
            raise ValueError(f"{name} is listed twice")
        listing[name] = digest
    return listing


def write_checksums(directory: Path, digests: Mapping[str, str], name: str) -> Path:
    """Write the checksums file ``name`` for ``digests`` into ``directory``."""
    target = directory / name
    runtime.assert_contained(directory, target)
    target.write_bytes(render_checksums(digests))
    return target


def _command_failure(result: subprocess.CompletedProcess) -> str:
    output = "".join(data.decode("utf-8", "replace") if isinstance(data, bytes) else data or ""
                     for data in (result.stdout, result.stderr)).strip()
    return f"exit {result.returncode}" + (f": {output[-2000:]}" if output else "")


def _run_policy_command(ctx: ReleaseContext, command: repo_policy.Command,
                        values: Mapping[str, str]) -> subprocess.CompletedProcess:
    argv, env = command.render(values)
    try:
        return ctx.command_runner(argv, env, ctx.repo_root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _refuse(f"{argv[0]} could not run: {exc}", argv=argv) from None


def verify_artifact(ctx: ReleaseContext, artifact: Path, version: str, commit: str) -> str | None:
    """Run the policy's ``verify`` command on ``artifact`` for ``commit``;
    ``None`` when it passes, else the failure."""
    result = _run_policy_command(ctx, ctx.release.verify,
                                 {**_values(ctx, version, commit), "artifact": str(artifact)})
    return None if result.returncode == 0 else f"{artifact.name} fails verify ({_command_failure(result)})"


def asset_set_problems(ctx: ReleaseContext, directory: Path, names: Sequence[str], version: str,
                       commit: str) -> list[str]:
    """Why the release assets ``names``, downloaded into ``directory``, are
    not a *consistent* asset set for ``commit``: exactly the artifacts plus
    the checksums file, a checksums file listing exactly the other assets
    with matching SHA-256s, and every artifact passing ``verify``. Empty
    when consistent."""
    expected = asset_names(ctx, version)
    checksums = ctx.release.checksums
    problems = []
    missing = sorted(set(expected) - set(names))
    extra = sorted(set(names) - set(expected))
    if missing:
        problems.append(f"missing asset(s) {missing}")
    if extra:
        problems.append(f"unexpected asset(s) {extra}")
    artifacts = [name for name in expected[:-1] if name in names]
    if checksums in names:
        try:
            listing = parse_checksums((directory / checksums).read_bytes())
        except ValueError as exc:
            problems.append(f"{checksums} is unreadable: {exc}")
        else:
            others = sorted(name for name in names if name != checksums)
            if sorted(listing) != others:
                problems.append(f"{checksums} lists {sorted(listing)}, the other assets are {others}")
            for name in others:
                if name in listing and listing[name] != sha256_file(directory / name):
                    problems.append(f"{checksums} records the wrong SHA-256 for {name}")
    for name in artifacts:
        failure = verify_artifact(ctx, directory / name, version, commit)
        if failure:
            problems.append(failure)
    return problems


def _downloaded(ctx: ReleaseContext, tag: str, directory: Path, release: Release) -> list[str]:
    """Download ``release``'s assets into the empty ``directory`` and return
    their names, refusing when the files on disk are not exactly the
    release's listed assets."""
    names = [asset.name for asset in release.assets]
    if len(set(names)) != len(names):
        raise _refuse(f"the release for {tag} lists an asset name twice: {names}", tag=tag)
    if names:
        ctx.forge.download_assets(tag, directory)
    on_disk = sorted(path.name for path in directory.iterdir())
    if on_disk != sorted(names):
        raise _refuse(f"downloading {tag}'s assets gave {on_disk}, the release lists {sorted(names)}",
                      tag=tag)
    return names


def release_problems(ctx: ReleaseContext, tag: str, release: Release, version: str, commit: str) -> list[str]:
    """:func:`asset_set_problems` for ``release``, downloaded to a scratch
    directory."""
    with tempfile.TemporaryDirectory(prefix="release-assets-") as scratch:
        directory = Path(scratch)
        names = _downloaded(ctx, tag, directory, release)
        return asset_set_problems(ctx, directory, names, version, commit)


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------


def _resolve_commit(ctx: ReleaseContext, commit: str) -> str:
    resolved = gitrepo.ref_commit(ctx.repo_root, commit, runner=ctx.git_runner)
    if resolved is None:
        raise _refuse(f"{commit} does not name a commit", commit=commit)
    return resolved


def _require_on_trunk(ctx: ReleaseContext, commit: str) -> None:
    trunk = ctx.policy.trunk_branch
    tip = gitrepo.fetch_branch(ctx.repo_root, ctx.remote, trunk, runner=ctx.git_runner)
    if not gitrepo.is_ancestor(ctx.repo_root, commit, tip, runner=ctx.git_runner):
        raise _refuse(f"{commit} is not on {ctx.remote}/{trunk} ({tip}); only a trunk commit is "
                      f"classified", commit=commit, trunk_tip=tip)


def classify(ctx: ReleaseContext, commit: str, *, fetch_tags: bool = True) -> Classification:
    """Classify trunk commit ``commit`` (first matching row of the plan's
    table). Fetches the trunk and, unless ``fetch_tags`` is false, every
    tag; reads the remote's tags with ``ls-remote``, and each release with
    the forge. Refuses when the release switch is off or ``commit`` is not
    on the trunk."""
    release = ctx.release
    if not release.enabled:
        raise _refuse(f"{repo_policy.POLICY_PATH} does not enable release", commit=commit)
    commit = _resolve_commit(ctx, commit)
    _require_on_trunk(ctx, commit)
    version = repo_policy.read_committed_version(ctx.repo_root, ctx.policy, commit)
    key = release.version_key(version)
    tag = release.tag_for(version)
    abandoned = set(release.abandoned_tags)

    if fetch_tags:
        gitrepo.fetch(ctx.repo_root, ctx.remote, [TAGS_REFSPEC], runner=ctx.git_runner)
    remote_tags = gitrepo.ls_remote_tags(ctx.repo_root, ctx.remote, runner=ctx.git_runner)
    # The matching tags: those tag_format renders canonically from a version.
    matching = {}
    for name, oid in remote_tags.items():
        tag_version = release.version_of_tag(name)
        if tag_version is not None and release.tag_for(tag_version) == name:
            matching[name] = (tag_version, oid)
    ancestors = {name: entry for name, entry in matching.items()
                 if gitrepo.is_ancestor(ctx.repo_root, entry[1], commit, runner=ctx.git_runner)}
    tag_commit = remote_tags.get(tag)
    found = ctx.forge.view_release(tag)

    def result(state: str, detail: str, **extra) -> Classification:
        return Classification(state=state, version=version, tag=tag, commit=commit,
                              target=tag_commit or commit, tag_commit=tag_commit, release=found,
                              detail=detail, **extra)

    published = found is not None and not found.is_draft
    if tag_commit is not None and tag not in ancestors:
        return result(COLLISION_TAG_ELSEWHERE,
                      f"{tag} exists at {tag_commit}, which is not {commit} or an ancestor of it")
    if tag in abandoned:
        if tag_commit is not None and found is None:
            return result(ABANDONED_VERSION, f"{tag} is acknowledged in abandoned_tags; no release")
        return result(ABANDONED_TAG_INCONSISTENT,
                      f"{tag} is in abandoned_tags, but "
                      + ("a release exists for it" if found is not None else "the tag does not exist"))
    if tag_commit == commit and published:
        problems = release_problems(ctx, tag, found, version, commit)
        if problems:
            return result(RELEASE_MISMATCH, f"the published release for {tag} is inconsistent: "
                          + "; ".join(problems), problems=tuple(problems))
        return result(ALREADY_RELEASED, f"{tag} is published at {commit}")
    if tag_commit is not None and published:
        return result(NO_CHANGE, f"{tag} is published at {tag_commit}, an ancestor")
    if tag_commit is None and found is not None:
        return result(COLLISION_RELEASE_WITHOUT_TAG, f"a release named {tag} exists without the tag")

    # Every lower matching ancestor tag must be settled: published, or
    # acknowledged in abandoned_tags (which is never read).
    baseline = sorted((name for name, (tag_version, _) in ancestors.items()
                       if release.version_key(tag_version) < key),
                      key=lambda name: release.version_key(ancestors[name][0]))
    unsettled = []
    for name in baseline:
        if name in abandoned:
            continue
        lower = ctx.forge.view_release(name)
        if lower is None or lower.is_draft:
            unsettled.append(name)
    if unsettled:
        return result(BASELINE_UNRELEASED,
                      f"lower tag(s) {unsettled} have no published release and are not in "
                      f"abandoned_tags; resume or acknowledge them first", unsettled=tuple(unsettled))
    if tag_commit is not None:
        return result(RESUME, f"{tag} exists at {tag_commit} with "
                      + ("a draft release" if found is not None else "no release"))
    if ancestors:
        highest = max(ancestors, key=lambda name: release.version_key(ancestors[name][0]))
        if key <= release.version_key(ancestors[highest][0]):
            return result(INVALID_TRANSITION, f"{version} is not above {highest}, already in "
                          f"{commit}'s history")
    return result(RELEASE_DUE, f"{version} has no tag and no release")


# ---------------------------------------------------------------------------
# Build and verify (the build job, at the checked-out target commit).
# ---------------------------------------------------------------------------


def _checked_out(ctx: ReleaseContext, commit: str) -> str:
    commit = _resolve_commit(ctx, commit)
    head = gitrepo.head_state(ctx.repo_root, runner=ctx.git_runner).commit
    if head != commit:
        raise _refuse(f"HEAD is {head}, not the target commit {commit}", head=head, commit=commit)
    return commit


def local_artifacts(ctx: ReleaseContext, version: str, root: Path | None = None) -> list[Path]:
    """The built artifacts at their policy paths under ``root`` (default:
    the repository root). A missing one refuses."""
    root = ctx.repo_root if root is None else root
    files = [root / path for path in artifact_paths(ctx, version)]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise _refuse(f"built artifact(s) missing: {missing}", missing=missing)
    return files


def build(ctx: ReleaseContext, commit: str) -> list[Path]:
    """Run the policy's ``build`` command at the checked-out ``commit`` and
    return the artifacts it produced."""
    commit = _checked_out(ctx, commit)
    version = repo_policy.read_committed_version(ctx.repo_root, ctx.policy, commit)
    result = _run_policy_command(ctx, ctx.release.build, _values(ctx, version, commit))
    if result.returncode != 0:
        raise _refuse(f"the build command failed ({_command_failure(result)})", commit=commit)
    return local_artifacts(ctx, version)


def verify(ctx: ReleaseContext, commit: str, root: Path | None = None) -> list[Path]:
    """Run the policy's ``verify`` command on every artifact for ``commit``,
    refusing on the first failure."""
    commit = _resolve_commit(ctx, commit)
    version = repo_policy.read_committed_version(ctx.repo_root, ctx.policy, commit)
    files = local_artifacts(ctx, version, root)
    for path in files:
        failure = verify_artifact(ctx, path, version, commit)
        if failure:
            raise _refuse(failure, artifact=str(path), commit=commit)
    return files


# ---------------------------------------------------------------------------
# The transaction (the publish job).
# ---------------------------------------------------------------------------


def verify_published(ctx: ReleaseContext, tag: str, version: str, commit: str) -> Release:
    """Post-publication verification: the release is published and its
    asset set is consistent for ``commit``."""
    found = ctx.forge.view_release(tag)
    if found is None or found.is_draft:
        raise _refuse(f"after publication, {tag} is {'missing' if found is None else 'still a draft'}",
                      tag=tag)
    problems = release_problems(ctx, tag, found, version, commit)
    if problems:
        raise _refuse(f"the published release for {tag} is inconsistent: " + "; ".join(problems),
                      tag=tag, problems=problems)
    return found


def _complete_draft(ctx: ReleaseContext, draft: Release, tag: str, version: str, commit: str,
                    built: Mapping[str, Path], scratch: Path) -> list[str]:
    """Upload what ``draft`` lacks and return the uploaded names. Present
    artifacts are authoritative; a foreign, unverifiable or contradicting
    asset refuses before anything is uploaded."""
    present_dir, stage = scratch / "present", scratch / "stage"
    present_dir.mkdir()
    stage.mkdir()
    names = _downloaded(ctx, tag, present_dir, draft)
    checksums = ctx.release.checksums
    foreign = sorted(set(names) - set(built) - {checksums})
    if foreign:
        raise _refuse(f"the draft release for {tag} holds foreign asset(s) {foreign}; inspect it",
                      tag=tag, foreign=foreign)
    final = {}
    for name, path in built.items():
        if name in names:
            failure = verify_artifact(ctx, present_dir / name, version, commit)
            if failure:
                raise _refuse(f"the draft release for {tag}: {failure}; inspect it", tag=tag)
            final[name] = present_dir / name
        else:
            final[name] = path
    digests = {name: sha256_file(path) for name, path in final.items()}
    uploads = [final[name] for name in built if name not in names]
    if checksums in names:
        try:
            listed = parse_checksums((present_dir / checksums).read_bytes())
        except ValueError as exc:
            raise _refuse(f"the draft release for {tag} has an unreadable {checksums}: {exc}",
                          tag=tag) from None
        if listed != digests:
            raise _refuse(f"the draft release for {tag} has a {checksums} that does not match the "
                          f"artifacts ({listed} vs {digests}); inspect it", tag=tag)
    else:
        uploads.append(write_checksums(stage, digests, checksums))
    if uploads:
        ctx.forge.upload_assets(tag, uploads)
    ctx.forge.publish_draft(tag)
    return [path.name for path in uploads]


def publish(ctx: ReleaseContext, commit: str, built_commit: str, *, artifacts_root: Path | None = None,
            tagger: tuple[str, str] | None = None) -> PublishOutcome:
    """Carry out the publishing classification of trunk commit ``commit``,
    recomputed here with a fresh tag fetch and fresh forge reads.
    ``built_commit`` is the commit the build job built; the artifacts sit at
    their policy paths under ``artifacts_root``. ``tagger`` is the
    ``(name, email)`` a new tag is created with."""
    commit = _resolve_commit(ctx, commit)
    built_commit = _resolve_commit(ctx, built_commit)
    state = classify(ctx, commit)
    tag, version = state.tag, state.version
    if state.state in (ALREADY_RELEASED, NO_CHANGE) and state.target == built_commit:
        found = verify_published(ctx, tag, version, built_commit)
        return PublishOutcome(state.state, tag, built_commit, "already_published", False, (), found.url)
    if not state.publishes:
        raise _refuse(f"{state.state}: {state.detail}; nothing to publish", state=state.state, tag=tag)
    if state.target != built_commit:
        raise _refuse(f"{state.state} targets {state.target}, but the build is of {built_commit}",
                      state=state.state, target=state.target, built_commit=built_commit)
    target = state.target

    # 1. The built artifacts, re-verified for the target.
    files = local_artifacts(ctx, version, artifacts_root)
    for path in files:
        failure = verify_artifact(ctx, path, version, target)
        if failure:
            raise _refuse(failure, artifact=str(path), commit=target)
    built = {path.name: path for path in files}

    # 2. Tag after validation.
    tagged = False
    if state.state == RELEASE_DUE:
        values = _values(ctx, version, target)
        message = repo_policy.render(ctx.release.publication_notes, values)
        gitrepo.create_annotated_tag(ctx.repo_root, tag, target, message, tagger=tagger,
                                     runner=ctx.git_runner)
        try:
            gitrepo.push_tag(ctx.repo_root, ctx.remote, tag, runner=ctx.git_runner)
            tagged = True
        except GitOperationError as exc:
            # Our local tag now differs from any tag another run pushed, so
            # re-read the remote without fetching tags. A tag at a commit this
            # clone lacks cannot be classified; that is still the rejection.
            try:
                again = classify(ctx, commit, fetch_tags=False)
            except GitOperationError as reread:
                raise _refuse(f"pushing {tag} was rejected and {ctx.remote}'s tags could not be "
                              f"reclassified ({reread})", tag=tag, push=exc.evidence) from reread
            if not (again.state == RESUME and again.target == target):
                raise _refuse(f"pushing {tag} was rejected and {tag} is now {again.state} "
                              f"({again.detail})", tag=tag, state=again.state,
                              push=exc.evidence) from exc

    # 3. The remote tag names the target.
    remote_commit = gitrepo.remote_tag_commit(ctx.repo_root, ctx.remote, tag, runner=ctx.git_runner)
    if remote_commit != target:
        raise _refuse(f"{ctx.remote}'s {tag} names {remote_commit}, not {target}", tag=tag,
                      remote_commit=remote_commit, target=target)

    # 4. Publish, or complete a draft.
    values = _values(ctx, version, target)
    found = ctx.forge.view_release(tag)
    with tempfile.TemporaryDirectory(prefix="release-publish-") as scratch_dir:
        scratch = Path(scratch_dir)
        if found is None:
            action = "created"
            digests = {name: sha256_file(path) for name, path in built.items()}
            (scratch / "stage").mkdir()
            sums = write_checksums(scratch / "stage", digests, ctx.release.checksums)
            uploads = [*files, sums]
            ctx.forge.create_release(tag, uploads,
                                     repo_policy.render(ctx.release.publication_title, values),
                                     repo_policy.render(ctx.release.publication_notes, values))
            uploaded = tuple(path.name for path in uploads)
        elif found.is_draft:
            action = "resumed_draft"
            uploaded = tuple(_complete_draft(ctx, found, tag, version, target, built, scratch))
        else:
            action, uploaded = "already_published", ()

    # 5. Post-publication verification.
    published = verify_published(ctx, tag, version, target)
    return PublishOutcome(state.state, tag, target, action, tagged, uploaded, published.url)
