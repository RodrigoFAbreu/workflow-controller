#!/usr/bin/env python3
"""The Controller's GitHub Actions workflows, as a model. Stdlib only.

The tests are stdlib-only and cannot parse YAML, so the three workflows live
here as Python data and are rendered by a small deterministic emitter:

- ``validate.yml`` (``workflow_call`` only) is the single definition of the
  required validation: the Controller shard matrix, the frozen conformance
  matrix and the ``package`` job;
- ``ci.yml`` runs it on every pull request;
- ``main.yml`` runs it on every push to ``main`` (and on
  ``workflow_dispatch``), then classifies the trunk commit with
  ``tools/release.py classify`` and, when a release is due or resumable,
  builds, verifies and publishes it as one release transaction.

A ``v*`` tag push triggers nothing: the release transaction creates the tag,
after validation.

``--write`` renders ``.github/workflows/{validate,ci,main}.yml`` and removes
the retired ``release.yml``; ``--check`` exits ``1`` naming every committed
file that differs from the render, and a retired file that still exists. The
committed YAML is what GitHub runs, so ``--check`` passing means GitHub runs
exactly the model ``tests.test_ci_workflows`` asserts on.

Emitter rules: mappings keep insertion order, lists become block sequences,
multi-line strings become ``|`` literal blocks, and a string is emitted bare
only when ``SAFE_SCALAR_RE`` accepts it and it is not a YAML 1.1 reserved word.
Every other string is JSON-quoted, which is valid YAML double-quoted style.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = Path(".github") / "workflows"

#: A string emitted bare: a leading letter or underscore, then only characters
#: that can never start a YAML indicator, comment or mapping value mid-scalar.
SAFE_SCALAR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./@-]*")
#: Plain scalars YAML 1.1 (PyYAML, and GitHub's own ``on`` key history) would
#: read as a boolean or null rather than a string.
RESERVED_SCALARS = frozenset({
    "y", "n", "yes", "no", "on", "off", "true", "false", "null",
})

PYTHON_VERSION = "3.12"
RUNNER = "ubuntu-latest"

#: Every action ``main.yml``'s ``release-plan``, ``build`` and ``publish``
#: jobs use, pinned to the full commit its tag named when the retired
#: ``release.yml`` first read it (``git ls-remote
#: https://github.com/actions/<repo>``; each is a lightweight tag, so the
#: listed object is the commit). ``validate.yml`` keeps the major tag, as
#: the workflows before it did (ADR 0002).
ACTION_PINS = {
    "actions/checkout": ("11d5960a326750d5838078e36cf38b85af677262", "v4"),
    "actions/setup-python": ("a26af69be951a213d495a4c3e4e4022e16d87065", "v5"),
    "actions/upload-artifact": ("ea165f8d65b6e75b540449e92b4886f43607fa02", "v4"),
    "actions/download-artifact": ("d3f86a106a0bac45b974a628896c90dbdf5c8093", "v4"),
}

#: What ``$GITHUB_SHA`` holds for the runs ``main.yml`` classifies.
GITHUB_SHA_NOTE = (
    "GitHub documents GITHUB_SHA for a push event as the \"Tip commit pushed to the ref\" "
    "(https://docs.github.com/en/actions/reference/workflows-and-actions/"
    "events-that-trigger-workflows#push), and for workflow_dispatch as the last commit on "
    "the dispatched ref. release-plan peels it with git rev-parse \"$GITHUB_SHA^{commit}\" "
    "regardless, a no-op for a commit."
)
#: How ``controller/forge.py``'s ``create_release`` (``gh release create``)
#: publishes assets.
GH_RELEASE_CREATE_NOTE = (
    "gh release create with asset arguments creates the release as a draft, uploads "
    "the assets and then publishes it (gh manual: \"separate API calls are made to "
    "create the release as a draft, upload the assets, and then publish the release\"; "
    "https://cli.github.com/manual/gh_release_create, and draftWhileUploading in "
    "https://github.com/cli/cli/blob/trunk/pkg/cmd/release/create/create.go), so the "
    "single-command form is compatible with immutable releases and no gh release edit "
    "is needed. An interrupted upload leaves that draft behind, which the next run resumes."
)

#: The Controller test shards. Every ``tests/test_*.py`` module is in exactly
#: one shard, in ``PACKAGE_TEST_MODULES`` or in ``EXCLUDED_TEST_MODULES``.
CONTROLLER_SHARDS = {
    "identity": ["test_identity", "test_runtime", "test_package_structure",
                 "test_write_containment", "test_lock", "test_handoff"],
    "job": ["test_job", "test_job_validation"],
    "resume": ["test_resume"],
    "decision": ["test_evidence", "test_decision", "test_golden_plan_stage_decisions",
                 "test_target_state", "test_managed_repo", "test_routing"],
    "cli": ["test_cli", "test_lifecycle_orchestration"],
    "worker": ["test_worker", "test_observe", "test_observation_equivalence"],
    "docs": ["test_plan_document_consistency", "test_checklist_corrections",
             "test_ci_workflows", "test_release_tools", "test_buildinfo"],
    "trunk": ["test_version_authority", "test_repo_policy", "test_gitrepo", "test_forge", "test_milestone_branch",
              "test_release_txn", "test_no_rewrite_invariants"],
}
PACKAGE_TEST_MODULES = ["test_packaged_runtime"]
EXCLUDED_TEST_MODULES = {
    "test_integration_disposable_repo":
        "needs the live claude binary, network access and real spend (CONTROLLER_LIVE_WORKER=1)",
}

#: The frozen Workflow conformance suites, run from ``scripts/``. They equal
#: the managed ``workflow-conformance.yml``'s own ``run: python3 <file>`` lines.
CONFORMANCE_SUITES = [
    "workflow_fingerprint_test.py",
    "workflow_state_test.py",
    "workflow_test_harness_test.py",
    "workflow_integration_test.py",
    "workflow_acceptance_matrix_test.py",
    "workflow_state_completion_obligations_test.py",
    "workflow_fingerprint_generalization_test.py",
]

DIST_DIR = "dist"
ARTIFACT_NAME = "dist"

PIPX_SMOKE = "\n".join([
    f"pipx install {DIST_DIR}/*.whl",
    'expected="workflow-controller $(python3 tools/release.py version)"',
    'actual="$(workflow-controller --version)"',
    "actual=\"${actual%%$'\\n'*}\"",
    'if [ "$actual" != "$expected" ]; then',
    '  echo "workflow-controller --version line 1 is \'$actual\', expected \'$expected\'" >&2',
    "  exit 1",
    "fi",
])


class Pinned:
    """A ``uses:`` value pinned to a commit, rendered with its tag as a
    trailing comment: ``actions/checkout@<sha> # v4``."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.sha, self.tag = ACTION_PINS[action]

    def __str__(self) -> str:
        return f"{self.action}@{self.sha}"


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------

def scalar(value: object) -> str:
    """One scalar as YAML text (never a multi-line string)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Pinned):
        return f"{value} # {value.tag}"
    if isinstance(value, str):
        if SAFE_SCALAR_RE.fullmatch(value) and value.lower() not in RESERVED_SCALARS:
            return value
        return json.dumps(value)
    raise TypeError(f"cannot emit {type(value).__name__} {value!r}")


def _literal_block(text: str, indent: str) -> list[str]:
    if text.endswith("\n") or text.startswith((" ", "\t")):
        raise ValueError(f"multi-line string must not start with a blank or end with a newline: {text!r}")
    lines = text.split("\n")
    if any(line != line.rstrip() for line in lines):
        raise ValueError(f"multi-line string has trailing whitespace: {text!r}")
    return [f"{indent}{line}" if line else "" for line in lines]


def _is_block(value: object) -> bool:
    return (isinstance(value, dict) and bool(value)) or (isinstance(value, list) and bool(value))


def _emit_value(key_prefix: str, value: object, indent: str) -> list[str]:
    """``key_prefix`` is ``"<key>:"`` or ``"-"`` already indented."""
    if isinstance(value, str) and "\n" in value:
        return [f"{key_prefix} |", *_literal_block(value, indent + "  ")]
    if isinstance(value, dict) and not value:
        return [f"{key_prefix} {{}}"]
    if isinstance(value, list) and not value:
        return [f"{key_prefix} []"]
    if value is None and key_prefix.endswith(":"):
        return [key_prefix]
    if _is_block(value):
        if key_prefix.endswith("-"):
            return _emit_sequence_item(key_prefix, value, indent)
        return [key_prefix, *_emit_block(value, indent + "  ")]
    return [f"{key_prefix} {scalar(value)}"]


def _emit_sequence_item(dash: str, value: object, indent: str) -> list[str]:
    # A mapping or sequence as a list item starts on the dash line.
    inner = _emit_block(value, indent + "  ")
    first = inner[0][len(indent) + 2:]
    return [f"{dash} {first}", *inner[1:]]


def _emit_block(value: object, indent: str) -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"mapping key {key!r} is not a string")
            lines.extend(_emit_value(f"{indent}{scalar(key)}:", item, indent))
    elif isinstance(value, list):
        for item in value:
            lines.extend(_emit_value(f"{indent}-", item, indent))
    else:
        raise TypeError(f"top level must be a mapping or a sequence, not {type(value).__name__}")
    return lines


def emit(value: object, header: str = "") -> str:
    """``value`` (a mapping or a sequence) as a YAML document, preceded by
    ``header`` as ``#`` comment lines."""
    comments = [f"# {line}".rstrip() for line in header.split("\n")] if header else []
    return "\n".join([*comments, *_emit_block(value, "")]) + "\n"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _checkout(pinned: bool, fetch_depth: int | None = None, *, ref: str | None = None,
              fetch_tags: bool = False, persist_credentials: bool = False) -> dict:
    """A checkout step. Only the job that pushes a tag (``publish``) keeps
    the token in the checkout's Git configuration."""
    step: dict = {"uses": Pinned("actions/checkout") if pinned else "actions/checkout@v4"}
    options: dict = {}
    if ref is not None:
        options["ref"] = ref
    if fetch_depth is not None:
        options["fetch-depth"] = fetch_depth
    if fetch_tags:
        options["fetch-tags"] = True
    if not persist_credentials:
        options["persist-credentials"] = False
    if options:
        step["with"] = options
    return step


def _setup_python(pinned: bool) -> dict:
    return {"uses": Pinned("actions/setup-python") if pinned else "actions/setup-python@v5",
            "with": {"python-version": PYTHON_VERSION}}


def validate_workflow() -> dict:
    shards = [{"shard": name, "modules": " ".join(f"tests.{m}" for m in modules)}
              for name, modules in CONTROLLER_SHARDS.items()]
    read = {"contents": "read"}
    return {
        "name": "Validate",
        "on": {"workflow_call": None},
        "jobs": {
            "controller": {
                "name": "controller (${{ matrix.shard }})",
                "runs-on": RUNNER,
                "permissions": read,
                "strategy": {"fail-fast": False, "matrix": {"include": shards}},
                "steps": [
                    _checkout(pinned=False),
                    _setup_python(pinned=False),
                    {"name": "install (editable)", "run": "pip install -e ."},
                    {"name": "Controller tests", "run": "python -m unittest ${{ matrix.modules }} -v"},
                ],
            },
            "conformance": {
                "name": "conformance (${{ matrix.suite }})",
                "runs-on": RUNNER,
                "permissions": read,
                "strategy": {"fail-fast": False, "matrix": {"suite": list(CONFORMANCE_SUITES)}},
                "steps": [
                    _checkout(pinned=False),
                    _setup_python(pinned=False),
                    {"name": "conformance suite", "working-directory": "scripts",
                     "run": "python3 ${{ matrix.suite }}"},
                ],
            },
            "package": {
                "runs-on": RUNNER,
                "permissions": read,
                "steps": [
                    _checkout(pinned=False, fetch_depth=0),
                    _setup_python(pinned=False),
                    {"name": "build prerequisite", "run": 'pip install "setuptools>=70.1"'},
                    {"name": "build wheel",
                     "run": f"python -m pip wheel --no-deps -w {DIST_DIR} ."},
                    {"name": "verify wheel",
                     "run": f"python3 tools/release.py verify-wheel {DIST_DIR}/*.whl --local "
                            '--commit "$(git rev-parse "$GITHUB_SHA^{commit}")"'},
                    {"name": "packaged runtime tests",
                     "run": "CONTROLLER_REQUIRE_PACKAGING_TESTS=1 "
                            "python -m unittest tests.test_packaged_runtime -v"},
                    {"name": "pipx smoke test", "run": PIPX_SMOKE},
                ],
            },
        },
    }


def ci_workflow() -> dict:
    return {
        "name": "CI",
        "on": {"pull_request": None},
        "concurrency": {"group": "${{ github.workflow }}-${{ github.ref }}",
                        "cancel-in-progress": True},
        "permissions": {"contents": "read"},
        "jobs": {"validate": {"uses": "./.github/workflows/validate.yml"}},
    }


#: The ``release-plan`` states whose target ``build`` and ``publish`` act on.
PUBLISHING_STATES = ("RELEASE_DUE", "RESUME")
#: ``release-plan``'s outputs, written by ``tools/release.py classify``.
PLAN_OUTPUTS = ("state", "version", "tag", "commit")
ON_TRUNK = "github.ref == 'refs/heads/main'"
#: ``classify`` fetches the trunk and the tags and runs ``ls-remote`` against
#: ``origin``. The repository is private and ``release-plan``'s checkout does
#: not persist its token, so that one step lends Git the job's read-only
#: ``GH_TOKEN`` through ``gh`` as a credential helper, in its own environment
#: only: nothing is written to the checkout's Git configuration.
GIT_CREDENTIAL_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
    "GIT_CONFIG_VALUE_0": "!gh auth git-credential",
}


def _publishing_condition() -> str:
    states = " || ".join(f"needs.release-plan.outputs.state == '{state}'"
                         for state in PUBLISHING_STATES)
    return f"{ON_TRUNK} && ({states})"


def main_workflow() -> dict:
    classify = ('python3 tools/release.py classify '
                '--commit "$(git rev-parse "$GITHUB_SHA^{commit}")"')
    return {
        "name": "Main",
        "on": {"push": {"branches": ["main"]}, "workflow_dispatch": None},
        "concurrency": {"group": "main-release", "cancel-in-progress": False},
        "permissions": {"contents": "read"},
        "jobs": {
            "validate": {"uses": "./.github/workflows/validate.yml"},
            "release-plan": {
                "needs": ["validate"],
                "if": ON_TRUNK,
                "runs-on": RUNNER,
                "env": {"GH_TOKEN": "${{ github.token }}"},
                "outputs": {name: f"${{{{ steps.classify.outputs.{name} }}}}" for name in PLAN_OUTPUTS},
                "steps": [
                    _checkout(pinned=True, fetch_depth=0, fetch_tags=True),
                    _setup_python(pinned=True),
                    {"name": "classify", "id": "classify", "env": dict(GIT_CREDENTIAL_ENV), "run": classify},
                ],
            },
            "build": {
                "needs": ["validate", "release-plan"],
                "if": _publishing_condition(),
                "runs-on": RUNNER,
                "steps": [
                    _checkout(pinned=True, fetch_depth=0, ref="${{ needs.release-plan.outputs.commit }}"),
                    _setup_python(pinned=True),
                    {"name": "build", "run": "python3 tools/release.py build"},
                    {"name": "verify", "run": "python3 tools/release.py verify"},
                    {"name": "pipx smoke test", "run": PIPX_SMOKE},
                    {"name": "checksums", "run": f"python3 tools/release.py checksums {DIST_DIR}"},
                    {"uses": Pinned("actions/upload-artifact"),
                     "with": {"name": ARTIFACT_NAME, "path": f"{DIST_DIR}/"}},
                ],
            },
            "publish": {
                "needs": ["validate", "release-plan", "build"],
                "if": _publishing_condition(),
                "runs-on": RUNNER,
                "permissions": {"contents": "write"},
                "env": {"GH_TOKEN": "${{ github.token }}",
                        "COMMIT": "${{ needs.release-plan.outputs.commit }}"},
                "steps": [
                    _checkout(pinned=True, fetch_depth=0, fetch_tags=True, persist_credentials=True),
                    _setup_python(pinned=True),
                    {"uses": Pinned("actions/download-artifact"),
                     "with": {"name": ARTIFACT_NAME, "path": DIST_DIR}},
                    {"name": "publish", "run": 'python3 tools/release.py publish --commit "$COMMIT"'},
                ],
            },
        },
    }


_GENERATED = ("Generated by tools/ci_workflows.py -- do not edit by hand.\n"
              "Change the model there and run: python3 tools/ci_workflows.py --write")

WORKFLOWS = {
    "validate.yml": (validate_workflow,
                     "The required validation, called by ci.yml and main.yml."),
    "ci.yml": (ci_workflow, "Runs the required validation on pull requests. Milestone branches are\n"
                            "validated through their Draft PR."),
    "main.yml": (main_workflow,
                 "Trunk: validate every push to main, then classify it and, when a release is due or\n"
                 "resumable, build, verify and publish it as one release transaction\n"
                 "(tools/release.py over controller/release_txn.py). Runs never cancel one another.\n"
                 "Every action outside validate is pinned to a full commit SHA; publish is the only\n"
                 "job that can write.\n\n"
                 + GITHUB_SHA_NOTE + "\n\n" + GH_RELEASE_CREATE_NOTE),
}
#: Workflow files the model once rendered and no longer does. ``--write``
#: removes them and ``--check`` fails while one exists: the tag-triggered
#: ``release.yml`` would otherwise keep publishing on a hand-pushed ``v*`` tag.
RETIRED_WORKFLOWS = ("release.yml",)


def _wrap(text: str, width: int = 96) -> str:
    out: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            if line and len(line) + 1 + len(word) > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}" if line else word
        out.append(line)
    return "\n".join(out)


def render() -> dict[str, str]:
    """``{file name: rendered text}`` for every workflow in the model."""
    return {name: emit(build(), _wrap(f"{_GENERATED}\n\n{description}"))
            for name, (build, description) in WORKFLOWS.items()}


def check(root: Path = REPO_ROOT) -> list[str]:
    """The workflow files under ``root`` that differ from the render, then
    every retired workflow file that still exists."""
    mismatched = []
    for name, text in render().items():
        path = root / WORKFLOWS_DIR / name
        try:
            committed = path.read_bytes()
        except OSError:
            committed = None
        if committed != text.encode("utf-8"):
            mismatched.append(str(WORKFLOWS_DIR / name))
    for name in RETIRED_WORKFLOWS:
        if (root / WORKFLOWS_DIR / name).exists():
            mismatched.append(str(WORKFLOWS_DIR / name))
    return mismatched


def write(root: Path = REPO_ROOT) -> None:
    for name, text in render().items():
        path = root / WORKFLOWS_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    for name in RETIRED_WORKFLOWS:
        (root / WORKFLOWS_DIR / name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="render the workflow files")
    mode.add_argument("--check", action="store_true", help="fail if a committed file differs")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.write:
        write(args.root)
        return 0
    mismatched = check(args.root)
    for path in mismatched:
        print(f"{path} differs from the model or is retired; run python3 tools/ci_workflows.py --write",
              file=sys.stderr)
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
