"""The repository policy (``workflow-controller-trunk-branch-pr-release-
orchestration`` CP2, "Repository policy").

A target repository opts into milestone branches and the release
transaction by committing ``.workflow-controller/policy.json``
(:data:`POLICY_PATH`). The policy is always read from a **committed tree**,
never the working tree (:func:`read_committed_policy`): a worker editing the
file, or a milestone changing it on its own branch, cannot change the rules
for work already in flight. An absent file means both switches are off
(:func:`milestone_branches_enabled`, :func:`release_enabled`); a present but
inadmissible one refuses (:class:`InvalidRepositoryPolicyError`), never
ignored.

Validation (:func:`parse_policy`) is strict: an unknown key at any level, a
duplicate key, a missing required key, an unknown adapter ``kind`` or an
unknown placeholder all refuse. The adapter registries are closed: exactly
what the reference adopter needs is implemented, and each unknown ``kind``
refusal names the known ones.

Nothing here names a particular trunk, tag prefix, version file or
artifact: those are the adopter's policy values.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from . import conventional_commit
from .buildinfo import SEMVER_RE
from .errors import InvalidRepositoryPolicyError

#: Where the policy lives, relative to the repository root.
POLICY_PATH = ".workflow-controller/policy.json"
SCHEMA_VERSION = 1

#: Workflow's own work-item id rule: the alphabet every ``branch_format``
#: must render a valid branch name for.
WORK_ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: The closed placeholder set. Each templated field admits a subset
#: (:data:`_FIELD_PLACEHOLDERS`).
PLACEHOLDERS = frozenset({"work_item_id", "version", "tag", "commit", "artifact", "release_notes"})

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_FORGE_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Work-item ids :func:`_check_branch_format` renders. An id contributes only
#: ``[a-z0-9_-]`` and starts alphanumeric, so every ``check-ref-format``
#: rule but one is decided by the format's literal text, which any single id
#: exercises: no id can supply a ``.``, ``/``, ``@``, ``{``, a leading ``-``
#: or ``.``, or a trailing ``.``. The exception is a component ending in
#: ``.lock``, which a literal ``.`` followed by the id can complete only when
#: the id is a substring of ``lock`` -- so every such substring is sampled.
_SAMPLE_WORK_ITEM_IDS = (
    "a", "0", "z9", "a-", "a_", "0-_z", "a" * 64,
    "l", "lo", "loc", "lock", "o", "oc", "ock", "c", "ck", "k",
)
#: Versions :func:`_check_tag_format` renders. A semver version starts and
#: ends with a digit and never holds ``..``, so, as above, the format's
#: literal text decides every rule.
_SAMPLE_VERSIONS = ("0.0.0", "1.2.3", "10.20.30")


def _refuse(problem: str, *, field: str | None = None, **evidence: Any) -> InvalidRepositoryPolicyError:
    where = f"{POLICY_PATH}: {field}: " if field else f"{POLICY_PATH}: "
    return InvalidRepositoryPolicyError(
        where + problem,
        evidence={"path": POLICY_PATH, "field": field, "problem": problem, **evidence},
    )


# ---------------------------------------------------------------------------
# Templates.
# ---------------------------------------------------------------------------


def template_placeholders(template: str, *, field: str, allowed: frozenset[str]) -> list[str]:
    """The placeholder names ``template`` uses, in order. Refuses a stray
    brace, a placeholder outside :data:`PLACEHOLDERS`, or one outside
    ``allowed``."""
    names = _PLACEHOLDER_RE.findall(template)
    literal = _PLACEHOLDER_RE.sub("", template)
    if "{" in literal or "}" in literal:
        raise _refuse(f"{template!r} has an unmatched brace", field=field)
    for name in names:
        if name not in PLACEHOLDERS:
            raise _refuse(f"{template!r} uses the unknown placeholder {{{name}}}; the known "
                          f"placeholders are {sorted(PLACEHOLDERS)}", field=field)
        if name not in allowed:
            raise _refuse(f"{template!r} uses {{{name}}}, which this field does not admit "
                          f"(it admits {sorted(allowed)})", field=field)
    return names


def render(template: str, values: Mapping[str, str]) -> str:
    """``template`` with each ``{name}`` replaced by ``values[name]``. The
    template must already have been validated; a missing value raises
    ``KeyError``."""
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


# ---------------------------------------------------------------------------
# Git ref-format checks.
# ---------------------------------------------------------------------------


def _check_ref_format(args: list[str], *, invalid: tuple[int, bytes | None]) -> bool:
    """Run ``git check-ref-format``: ``True`` on exit 0, ``False`` on the
    exact ``invalid`` outcome (exit code, and a required stderr fragment
    under ``LC_ALL=C``), and a refusal on anything else (I9)."""
    result = subprocess.run(["git", "check-ref-format", *args], capture_output=True, check=False,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode == 0:
        return True
    code, fragment = invalid
    if result.returncode == code and (fragment is None or fragment in result.stderr):
        return False
    raise _refuse(f"git check-ref-format {args} is undecidable (exit {result.returncode}): "
                  f"{result.stderr.decode('utf-8', 'replace').strip()}")


def _valid_branch(name: str) -> bool:
    # ``--branch`` reports an invalid name as a fatal error, exit 128.
    return _check_ref_format(["--branch", name], invalid=(128, b"is not a valid branch name"))


def _valid_tag(name: str) -> bool:
    return _check_ref_format([f"refs/tags/{name}"], invalid=(1, None))


# ---------------------------------------------------------------------------
# Adapter registries.
# ---------------------------------------------------------------------------


def _pyproject_version(text: str, *, where: str) -> str:
    """The static ``[project].version`` in a ``pyproject.toml``'s text.
    Refuses a dynamic version or a missing one."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{where} is not valid TOML: {exc}") from None
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"{where} has no [project] table")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or "version" in dynamic:
        raise ValueError(f"{where} declares a dynamic version; the pyproject version source "
                         f"requires a static [project].version")
    version = project.get("version")
    if not isinstance(version, str):
        raise ValueError(f"{where} declares no static [project].version")
    return version


#: Version-source kind -> a reader from the source file's text to the
#: version string. Each reader raises ``ValueError`` naming the failure.
VERSION_SOURCES = {"pyproject": _pyproject_version}


def _semver_key(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"{version!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


#: Version-scheme kind -> a total-order key that raises ``ValueError`` for a
#: version outside the scheme.
VERSION_SCHEMES = {"semver": _semver_key}
COMMAND_KINDS = frozenset({"command"})
PUBLICATION_KINDS = frozenset({"github_release"})
FORGE_KINDS = frozenset({"github"})
#: ``version_change`` reads the version from a version source and releases
#: when it changes; ``conventional_commit`` derives it from the release tags
#: and the Conventional Commit types of the trunk commits since the last one
#: (squash-merge-tag-versioning, Design A).
TRIGGER_VERSION_CHANGE = "version_change"
TRIGGER_CONVENTIONAL_COMMIT = "conventional_commit"
RELEASE_TRIGGERS = frozenset({TRIGGER_VERSION_CHANGE, TRIGGER_CONVENTIONAL_COMMIT})
#: ``milestone_branches.pull_request.merge_method``. ``merge`` is the
#: default and 1.3.0's behaviour.
MERGE_METHOD_MERGE = "merge"
MERGE_METHOD_SQUASH = "squash"
MERGE_METHODS = frozenset({MERGE_METHOD_MERGE, MERGE_METHOD_SQUASH})
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")

_FIELD_PLACEHOLDERS = {
    "branch_format": frozenset({"work_item_id"}),
    "tag_format": frozenset({"version"}),
    "build": frozenset({"version", "tag", "commit"}),
    "verify": frozenset({"version", "tag", "commit", "artifact"}),
    "artifacts": frozenset({"version"}),
    "publication": frozenset({"version", "tag", "commit"}),
    # settings-and-telemetry D.1: the release's notes only.
    "publication_notes": frozenset({"version", "tag", "commit", "release_notes"}),
    "release_notes_path": frozenset({"work_item_id"}),
}


# ---------------------------------------------------------------------------
# The parsed policy.
# ---------------------------------------------------------------------------


def _version_of_tag(tag_format: str, scheme: str, tag: str) -> str | None:
    prefix, suffix = tag_format.split("{version}")
    if len(tag) <= len(prefix) + len(suffix) or not tag.startswith(prefix) or not tag.endswith(suffix):
        return None
    version = tag[len(prefix):len(tag) - len(suffix)]
    try:
        VERSION_SCHEMES[scheme](version)
    except ValueError:
        return None
    return version


@dataclasses.dataclass(frozen=True)
class Command:
    """A ``command`` adapter: an argv and extra environment, both templated,
    run without a shell."""

    argv: tuple[str, ...]
    env: Mapping[str, str]

    def render(self, values: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
        return ([render(arg, values) for arg in self.argv],
                {name: render(value, values) for name, value in self.env.items()})


@dataclasses.dataclass(frozen=True)
class ReleaseNotes:
    """``milestone_branches.pull_request.release_notes`` (settings-and-
    telemetry D.1): where a milestone's notes section lives. ``path`` is a
    template admitting only ``{work_item_id}``; ``heading`` the section's
    ``## `` heading text."""

    path: str
    heading: str

    def path_for(self, work_item_id: str) -> str:
        return render(self.path, {"work_item_id": work_item_id})


@dataclasses.dataclass(frozen=True)
class MilestoneBranches:
    enabled: bool
    branch_format: str
    draft: bool
    ready_requires_green_checks: bool
    merge_method: str = MERGE_METHOD_MERGE
    #: ``None``: no notes, and the pull request body is 1.4's.
    release_notes: ReleaseNotes | None = None

    def branch_name(self, work_item_id: str) -> str:
        return render(self.branch_format, {"work_item_id": work_item_id})


@dataclasses.dataclass(frozen=True)
class Release:
    enabled: bool
    trigger: str
    #: ``None`` under ``conventional_commit``: the tags are the version.
    version_source_kind: str | None
    version_source_path: str | None
    version_scheme: str
    tag_format: str
    abandoned_tags: tuple[str, ...]
    build: Command
    verify: Command
    artifact_paths: tuple[str, ...]
    checksums: str
    publication_kind: str
    publication_title: str
    publication_notes: str
    #: ``conventional_commit`` only (empty under ``version_change``): type ->
    #: ``minor``/``patch``/``none``, and trunk commit -> the bump that settles
    #: it when its subject cannot be classified.
    change_types: Mapping[str, str] = dataclasses.field(default_factory=dict)
    bump_overrides: Mapping[str, str] = dataclasses.field(default_factory=dict)

    def read_version(self, text: str) -> str:
        """The version the version-source file's ``text`` declares,
        validated against the version scheme. Raises ``ValueError``, also
        under a trigger that has no version source."""
        if self.version_source_kind is None:
            raise ValueError(_no_version_source(self.trigger))
        version = VERSION_SOURCES[self.version_source_kind](text, where=self.version_source_path)
        self.version_key(version)
        return version

    def version_key(self, version: str) -> tuple:
        """The scheme's total-order key for ``version``. Raises ``ValueError``."""
        return VERSION_SCHEMES[self.version_scheme](version)

    def tag_for(self, version: str) -> str:
        return render(self.tag_format, {"version": version})

    def version_of_tag(self, tag: str) -> str | None:
        """The version ``tag`` names under ``tag_format``, or ``None`` when
        ``tag`` does not render from it."""
        return _version_of_tag(self.tag_format, self.version_scheme, tag)

    @property
    def uses_release_notes(self) -> bool:
        """Whether the notes template renders ``{release_notes}``, the
        release's opt-in to notes from the milestones (settings-and-telemetry
        D.3)."""
        return "release_notes" in _PLACEHOLDER_RE.findall(self.publication_notes)


@dataclasses.dataclass(frozen=True)
class RepositoryPolicy:
    """An admissible policy: the exact committed bytes, their SHA-256, and
    the validated values."""

    raw: bytes
    sha256: str
    data: Mapping[str, Any]
    trunk_branch: str
    trunk_remote: str
    forge_kind: str
    forge_repository: str
    milestone_branches: MilestoneBranches
    release: Release


def _no_version_source(trigger: str) -> str:
    return (f"the release trigger is {trigger!r}: the version is derived from the release tags, "
            f"not read from a version source")


def milestone_branches_enabled(policy: RepositoryPolicy | None) -> bool:
    """The Controller-runtime switch. An absent policy is off."""
    return policy is not None and policy.milestone_branches.enabled


def release_enabled(policy: RepositoryPolicy | None) -> bool:
    """The CI release-transaction switch. An absent policy is off."""
    return policy is not None and policy.release.enabled


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------


def _object(value: object, field: str, *, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise _refuse(f"must be a JSON object, not {type(value).__name__}", field=field)
    unknown = sorted(set(value) - required - optional)
    if unknown:
        raise _refuse(f"unknown key(s) {unknown}; the known keys are {sorted(required | optional)}",
                      field=field)
    missing = sorted(required - set(value))
    if missing:
        raise _refuse(f"missing required key(s) {missing}", field=field)
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _refuse(f"must be a non-empty string, not {value!r}", field=field)
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise _refuse(f"must be true or false, not {value!r}", field=field)
    return value


def _kind(value: object, field: str, known) -> str:
    kind = _string(value, field)
    if kind not in known:
        raise _refuse(f"unknown kind {kind!r}; the known kinds are {sorted(known)}", field=field)
    return kind


def _relative_path(value: object, field: str) -> str:
    text = _string(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or "\\" in text or any(part in ("", ".", "..") for part in text.split("/")):
        raise _refuse(f"{text!r} must be a normalised relative path inside the repository",
                      field=field)
    return text


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _refuse(f"must be a non-empty list of strings, not {value!r}", field=field)
    return [_string(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _branch_format_can_hit_trunk(prefix: str, suffix: str, trunk: str) -> bool:
    """Whether some work-item id renders ``prefix + id + suffix`` equal to
    ``trunk``, or to a ``/``-component prefix or extension of it -- names
    Git cannot create next to ``trunk``. Decided exactly over the id
    alphabet, not sampled."""
    # The rendered name equals trunk, or a component prefix of it.
    boundaries = [trunk] + [trunk[:i] for i, char in enumerate(trunk) if char == "/"]
    for candidate in boundaries:
        if (candidate.startswith(prefix) and candidate.endswith(suffix)
                and len(candidate) > len(prefix) + len(suffix)
                and WORK_ITEM_ID_RE.fullmatch(candidate[len(prefix):len(candidate) - len(suffix)])):
            return True
    # The rendered name extends trunk by a component: it starts with trunk + "/".
    head = trunk + "/"
    if prefix.startswith(head):
        return True
    if not head.startswith(prefix):
        return False
    rest = head[len(prefix):]
    # An id holds no "/", so the "/" closing ``rest`` comes from the suffix.
    return any(WORK_ITEM_ID_RE.fullmatch(rest[:split]) and suffix.startswith(rest[split:])
               for split in range(1, len(rest)))


def _check_branch_format(branch_format: str, trunk: str) -> None:
    field = "milestone_branches.branch_format"
    names = template_placeholders(branch_format, field=field, allowed=_FIELD_PLACEHOLDERS["branch_format"])
    if names != ["work_item_id"]:
        raise _refuse(f"{branch_format!r} must contain {{work_item_id}} exactly once", field=field)
    for work_item_id in _SAMPLE_WORK_ITEM_IDS:
        name = render(branch_format, {"work_item_id": work_item_id})
        if not _valid_branch(name):
            raise _refuse(f"{branch_format!r} renders {name!r} for work item {work_item_id!r}, "
                          f"which is not a valid branch name", field=field)
    prefix, suffix = branch_format.split("{work_item_id}")
    if _branch_format_can_hit_trunk(prefix, suffix, trunk):
        raise _refuse(f"{branch_format!r} can render the trunk branch {trunk!r}, or a name "
                      f"nested with it", field=field)


def _check_tag_format(tag_format: str) -> None:
    field = "release.tag_format"
    names = template_placeholders(tag_format, field=field, allowed=PLACEHOLDERS)
    if names != ["version"]:
        raise _refuse(f"{tag_format!r} is not invertible: it must contain {{version}} exactly "
                      f"once and no other placeholder", field=field)
    for version in _SAMPLE_VERSIONS:
        tag = render(tag_format, {"version": version})
        if not _valid_tag(tag):
            raise _refuse(f"{tag_format!r} renders {tag!r} for version {version}, which is not "
                          f"a valid tag name", field=field)


def _command(value: object, field: str, allowed: frozenset[str]) -> Command:
    entry = _object(value, field, required={"kind", "argv"}, optional={"env"})
    _kind(entry["kind"], f"{field}.kind", COMMAND_KINDS)
    argv = _string_list(entry["argv"], f"{field}.argv")
    for index, arg in enumerate(argv):
        template_placeholders(arg, field=f"{field}.argv[{index}]", allowed=allowed)
    env_value = entry.get("env", {})
    if not isinstance(env_value, dict):
        raise _refuse(f"must be a JSON object, not {env_value!r}", field=f"{field}.env")
    env = {}
    for name, template in env_value.items():
        if not _ENV_NAME_RE.fullmatch(name):
            raise _refuse(f"{name!r} is not an environment variable name", field=f"{field}.env")
        env[name] = _string(template, f"{field}.env.{name}")
        template_placeholders(template, field=f"{field}.env.{name}", allowed=allowed)
    return Command(argv=tuple(argv), env=env)


def _load_json(raw: bytes) -> dict:
    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
        seen: dict = {}
        for key, value in pairs:
            if key in seen:
                raise _refuse(f"duplicate key {key!r}")
            seen[key] = value
        return seen

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _refuse(f"is not UTF-8: {exc}") from None
    try:
        return json.loads(text, object_pairs_hook=no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise _refuse(f"is not valid JSON: {exc}") from None


def parse_policy(raw: bytes) -> RepositoryPolicy:
    """Validate a policy's exact bytes. Raises
    :class:`InvalidRepositoryPolicyError` naming the first rule it breaks."""
    data = _load_json(raw)
    top = _object(data, "(top level)",
                  required={"schema_version", "trunk", "forge", "milestone_branches", "release"})
    if type(top["schema_version"]) is not int or top["schema_version"] != SCHEMA_VERSION:
        raise _refuse(f"unsupported schema_version {top['schema_version']!r}; this Controller "
                      f"reads schema_version {SCHEMA_VERSION}", field="schema_version")

    trunk = _object(top["trunk"], "trunk", required={"branch", "remote"})
    trunk_branch = _string(trunk["branch"], "trunk.branch")
    if not _valid_branch(trunk_branch):
        raise _refuse(f"{trunk_branch!r} is not a valid branch name", field="trunk.branch")
    trunk_remote = _string(trunk["remote"], "trunk.remote")
    if not _REMOTE_RE.fullmatch(trunk_remote):
        raise _refuse(f"{trunk_remote!r} is not a remote name", field="trunk.remote")

    forge = _object(top["forge"], "forge", required={"kind", "repository"})
    forge_kind = _kind(forge["kind"], "forge.kind", FORGE_KINDS)
    forge_repository = _string(forge["repository"], "forge.repository")
    if not _FORGE_REPOSITORY_RE.fullmatch(forge_repository) or forge_repository.split("/")[1] in (".", ".."):
        raise _refuse(f"{forge_repository!r} is not OWNER/NAME", field="forge.repository")

    branches = _object(top["milestone_branches"], "milestone_branches",
                       required={"enabled", "branch_format", "pull_request"})
    pull_request = _object(branches["pull_request"], "milestone_branches.pull_request",
                           required={"draft", "ready_requires_green_checks"},
                           optional={"merge_method", "release_notes"})
    if _boolean(pull_request["draft"], "milestone_branches.pull_request.draft") is not True:
        raise _refuse("must be true: the Controller only opens Draft pull requests",
                      field="milestone_branches.pull_request.draft")
    branch_format = _string(branches["branch_format"], "milestone_branches.branch_format")
    _check_branch_format(branch_format, trunk_branch)
    milestone_branches = MilestoneBranches(
        enabled=_boolean(branches["enabled"], "milestone_branches.enabled"),
        branch_format=branch_format,
        draft=True,
        ready_requires_green_checks=_boolean(pull_request["ready_requires_green_checks"],
                                             "milestone_branches.pull_request.ready_requires_green_checks"),
        merge_method=_kind(pull_request.get("merge_method", MERGE_METHOD_MERGE),
                           "milestone_branches.pull_request.merge_method", MERGE_METHODS),
        release_notes=(_release_notes(pull_request["release_notes"]) if "release_notes" in pull_request
                       else None),
    )

    release = _parse_release(top["release"])
    # Only a squash commit carries the pull request title to the trunk.
    if (release.enabled and milestone_branches.enabled
            and release.trigger == TRIGGER_CONVENTIONAL_COMMIT
            and milestone_branches.merge_method != MERGE_METHOD_SQUASH):
        raise _refuse(f"must be {MERGE_METHOD_SQUASH!r} when milestone branches and releases are both "
                      f"enabled with the {TRIGGER_CONVENTIONAL_COMMIT!r} trigger: only a squash commit "
                      f"carries the pull request title to the trunk",
                      field="milestone_branches.pull_request.merge_method")
    return RepositoryPolicy(
        raw=raw, sha256=hashlib.sha256(raw).hexdigest(), data=data,
        trunk_branch=trunk_branch, trunk_remote=trunk_remote,
        forge_kind=forge_kind, forge_repository=forge_repository,
        milestone_branches=milestone_branches, release=release,
    )


def _release_notes(value: object) -> ReleaseNotes:
    """``milestone_branches.pull_request.release_notes`` (D.1): ``path`` a
    relative, normalised POSIX path template admitting only
    ``{work_item_id}``, any number of times; ``heading`` a non-empty single
    line."""
    field = "milestone_branches.pull_request.release_notes"
    section = _object(value, field, required={"path", "heading"})
    path = _string(section["path"], f"{field}.path")
    template_placeholders(path, field=f"{field}.path", allowed=_FIELD_PLACEHOLDERS["release_notes_path"])
    for sample in _SAMPLE_WORK_ITEM_IDS[:2]:
        _relative_path(render(path, {"work_item_id": sample}), f"{field}.path")
    heading = _string(section["heading"], f"{field}.heading")
    if "\n" in heading or "\r" in heading or not heading.strip():
        raise _refuse(f"{heading!r} must be a non-empty single line", field=f"{field}.heading")
    return ReleaseNotes(path=path, heading=heading)


def _parse_release(value: object) -> Release:
    # The trigger decides which keys the section takes, so it is read first.
    if isinstance(value, dict) and "trigger" in value:
        trigger = _kind(value["trigger"], "release.trigger", RELEASE_TRIGGERS)
    else:
        trigger = None  # ``_object`` below names the shape or the missing key
    common = {"enabled", "trigger", "version_scheme", "tag_format", "build", "verify", "artifacts",
              "publication"}
    if trigger == TRIGGER_CONVENTIONAL_COMMIT:
        if isinstance(value, dict) and "version_source" in value:
            raise _refuse(f"is not admitted under the {trigger!r} trigger: the release tags are the "
                          f"version", field="release.version_source")
        section = _object(value, "release", required=common | {"change_types"},
                          optional={"abandoned_tags", "bump_overrides"})
    else:
        section = _object(value, "release", required=common | {"version_source"},
                          optional={"abandoned_tags"})
    enabled = _boolean(section["enabled"], "release.enabled")
    scheme = _kind(section["version_scheme"], "release.version_scheme", VERSION_SCHEMES)

    source_kind = source_path = None
    change_types: dict[str, str] = {}
    bump_overrides: dict[str, str] = {}
    if trigger == TRIGGER_CONVENTIONAL_COMMIT:
        if scheme != "semver":
            raise _refuse(f"must be 'semver' under the {trigger!r} trigger: the bump arithmetic is "
                          f"SemVer's", field="release.version_scheme")
        change_types = _bump_table(section["change_types"], "release.change_types",
                                   key_re=conventional_commit.TYPE_RE, key_name="a lowercase type",
                                   values=conventional_commit.CHANGE_TYPE_BUMPS, required=True)
        bump_overrides = _bump_table(section.get("bump_overrides", {}), "release.bump_overrides",
                                     key_re=_COMMIT_RE, key_name="a 40-hex commit",
                                     values=frozenset(conventional_commit.BUMPS), required=False)
    else:
        source = _object(section["version_source"], "release.version_source", required={"kind", "path"})
        source_kind = _kind(source["kind"], "release.version_source.kind", VERSION_SOURCES)
        source_path = _relative_path(source["path"], "release.version_source.path")

    tag_format = _string(section["tag_format"], "release.tag_format")
    _check_tag_format(tag_format)

    build = _command(section["build"], "release.build", _FIELD_PLACEHOLDERS["build"])
    verify = _command(section["verify"], "release.verify", _FIELD_PLACEHOLDERS["verify"])

    artifacts = _object(section["artifacts"], "release.artifacts", required={"paths", "checksums"})
    paths = _string_list(artifacts["paths"], "release.artifacts.paths")
    for index, path in enumerate(paths):
        field = f"release.artifacts.paths[{index}]"
        template_placeholders(path, field=field, allowed=_FIELD_PLACEHOLDERS["artifacts"])
        _relative_path(render(path, {"version": _SAMPLE_VERSIONS[1]}), field)
    if len(set(paths)) != len(paths):
        raise _refuse("lists a path more than once", field="release.artifacts.paths")
    checksums = _string(artifacts["checksums"], "release.artifacts.checksums")
    if "/" in checksums or checksums in (".", "..") or "{" in checksums or "}" in checksums:
        raise _refuse(f"{checksums!r} must be a plain file name", field="release.artifacts.checksums")
    if checksums in {PurePosixPath(path).name for path in paths}:
        raise _refuse(f"{checksums!r} collides with an artifact's file name",
                      field="release.artifacts.checksums")

    publication = _object(section["publication"], "release.publication",
                          required={"kind", "title", "notes"})
    publication_kind = _kind(publication["kind"], "release.publication.kind", PUBLICATION_KINDS)
    title = _string(publication["title"], "release.publication.title")
    notes = _string(publication["notes"], "release.publication.notes")
    template_placeholders(title, field="release.publication.title", allowed=_FIELD_PLACEHOLDERS["publication"])
    template_placeholders(notes, field="release.publication.notes",
                          allowed=_FIELD_PLACEHOLDERS["publication_notes"])

    abandoned = section.get("abandoned_tags", [])
    if not isinstance(abandoned, list):
        raise _refuse(f"must be a list of tags, not {abandoned!r}", field="release.abandoned_tags")
    for index, tag in enumerate(abandoned):
        field = f"release.abandoned_tags[{index}]"
        _string(tag, field)
        if _version_of_tag(tag_format, scheme, tag) is None:
            raise _refuse(f"{tag!r} does not render from tag_format {tag_format!r} with a "
                          f"{scheme} version", field=field)
    if len(set(abandoned)) != len(abandoned):
        raise _refuse("lists a tag more than once", field="release.abandoned_tags")
    return Release(
        enabled=enabled, trigger=trigger,
        version_source_kind=source_kind, version_source_path=source_path, version_scheme=scheme,
        tag_format=tag_format, abandoned_tags=tuple(abandoned), build=build, verify=verify,
        artifact_paths=tuple(paths), checksums=checksums,
        publication_kind=publication_kind, publication_title=title, publication_notes=notes,
        change_types=change_types, bump_overrides=bump_overrides,
    )


def _bump_table(value: object, field: str, *, key_re: re.Pattern, key_name: str,
                values: frozenset[str], required: bool) -> dict[str, str]:
    """A ``change_types`` or ``bump_overrides`` object: each key matches
    ``key_re``, each value is one of ``values``; a ``required`` one is
    non-empty."""
    if not isinstance(value, dict):
        raise _refuse(f"must be a JSON object, not {value!r}", field=field)
    if required and not value:
        raise _refuse("must name at least one entry", field=field)
    for key, bump in value.items():
        if not key_re.fullmatch(key):
            raise _refuse(f"{key!r} is not {key_name}", field=field)
        if not isinstance(bump, str) or bump not in values:
            raise _refuse(f"{bump!r} is not one of {sorted(values)}", field=f"{field}.{key}")
    return dict(value)


# ---------------------------------------------------------------------------
# Reading from a committed tree.
# ---------------------------------------------------------------------------


def _git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, check=False)


def _read_blob(repo_root: Path, rev: str, path: str) -> bytes | None:
    """The bytes of ``path`` in ``rev``'s tree, or ``None`` when the tree
    has no such entry. Reads the blob by the object id ``ls-tree`` listed,
    so both calls see the same tree even if ``rev`` moves in between. Any
    other outcome refuses (an undecidable read is never "absent")."""
    listing = _git(repo_root, ["ls-tree", "-z", rev, "--", path])
    if listing.returncode != 0:
        raise _refuse(f"cannot list {path} at {rev}: "
                      f"{listing.stderr.decode('utf-8', 'replace').strip()}",
                      rev=rev, git_exit=listing.returncode)
    if not listing.stdout:
        return None
    entries = [entry for entry in listing.stdout.split(b"\0") if entry]
    meta, _, name = entries[0].partition(b"\t")
    fields = meta.split()
    if len(entries) != 1 or name.decode("utf-8", "replace") != path or len(fields) != 3:
        raise _refuse(f"git ls-tree returned an unexpected listing for {path} at {rev}: "
                      f"{listing.stdout!r}", rev=rev)
    mode, kind, oid = (field.decode("ascii", "replace") for field in fields)
    if kind != "blob" or mode not in ("100644", "100755"):
        raise _refuse(f"{path} at {rev} is a {kind} with mode {mode}, not a regular file",
                      rev=rev)
    blob = _git(repo_root, ["cat-file", "blob", oid])
    if blob.returncode != 0:
        raise _refuse(f"cannot read {path} ({oid}) at {rev}: "
                      f"{blob.stderr.decode('utf-8', 'replace').strip()}",
                      rev=rev, git_exit=blob.returncode)
    return blob.stdout


def read_committed_policy(repo_root: Path, rev: str = "HEAD") -> RepositoryPolicy | None:
    """The admissible policy committed at ``rev``, or ``None`` when ``rev``'s
    tree has no :data:`POLICY_PATH`. Never reads the working tree."""
    return parse_committed_policy(read_committed_policy_bytes(repo_root, rev), rev)


def read_committed_policy_bytes(repo_root: Path, rev: str = "HEAD") -> bytes | None:
    """The exact bytes of :data:`POLICY_PATH` committed at ``rev``, or
    ``None`` when ``rev``'s tree has none. A failed Git read refuses."""
    return _read_blob(Path(repo_root), rev, POLICY_PATH)


def parse_committed_policy(raw: bytes | None, rev: str) -> RepositoryPolicy | None:
    """:func:`parse_policy` of the bytes :func:`read_committed_policy_bytes`
    read at ``rev`` (``None`` for none), naming ``rev`` in a refusal."""
    if raw is None:
        return None
    try:
        return parse_policy(raw)
    except InvalidRepositoryPolicyError as exc:
        exc.evidence["rev"] = rev
        raise


def read_committed_version(repo_root: Path, policy: RepositoryPolicy, rev: str = "HEAD") -> str:
    """The version ``policy``'s version source declares at ``rev``, read
    from the committed tree. Refuses a missing file or an unreadable,
    dynamic or out-of-scheme version."""
    release = policy.release
    if release.version_source_path is None:
        raise _refuse(_no_version_source(release.trigger), field="release.trigger", rev=rev)
    raw = _read_blob(Path(repo_root), rev, release.version_source_path)
    if raw is None:
        raise _refuse(f"the version source {release.version_source_path} does not exist at {rev}",
                      field="release.version_source.path", rev=rev)
    try:
        return release.read_version(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _refuse(f"cannot read the version at {rev}: {exc}",
                      field="release.version_source", rev=rev) from None
