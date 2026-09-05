#!/usr/bin/env python3
# state_writer: false
"""Shared hermetic test fixtures for Workflow v2.1's own test suites
(`WF8a-i`, `WFR-36`).

**Documented fixture format** (this is the "documented fixture format"
`WFR-36` requires; every future consumer -- starting with `WF8a-ii`'s
cross-checkpoint integration tests -- should be able to build what it
needs from the two pieces below without reading `workflow_state.py`'s or
`workflow_fingerprint.py`'s own internals first):

- `ScratchRepo`: a disposable, real `git` repository under a temp
  directory. `repo.root` is its path, `repo.base` is the initial commit's
  full SHA, `repo.head()` returns current `HEAD`. `repo.commit(subject,
  trailers=..., filename=...)` writes one file and one commit carrying the
  named Git trailers in its body, exactly like a real
  `Workflow-Checkpoint`/`Workflow-Work-Item`/`Workflow-Plan-Approval`-
  bearing commit, and returns the new commit's SHA.
  `repo.commit_files(subject, {path: content}, trailers=...)` is the
  multi-file counterpart, for a fixture that needs one commit to carry
  both a trailer-bearing change and an accompanying `WORKFLOW_STATE.json`
  update together (added by `WF8a-ii` for its own integration fixtures).
  `repo.write_plan_docs(...)`/`repo.commit_plan_docs_as_base()` seed and
  commit the five plan-stage protected files
  (`docs/ai-workflow/WORKFLOW_V2_PLAN.md`, `WORKFLOW_V2_AUDIT.md`,
  `docs/TECHNICAL_DECISIONS.md`, and the registry/mapping JSON pair) at a
  given `work_item_id`, mirroring this repository's real layout closely
  enough that `workflow_fingerprint.py`'s plan-stage functions accept it
  unmodified -- see `workflow_test_harness_test.py` for the round-trip
  proof. `workflow_fingerprint.PLAN_STAGE_PROTECTED` is itself hardcoded
  to `workflow-v2-1-core`'s own registry/mapping paths (a known, already-
  documented limitation of that module, not this harness -- see its own
  `GPT-R9-011` note), so a caller using a different `work_item_id` must
  pass `plan_stage_protected_paths(work_item_id)` as the `protected`
  argument to any plan-stage identity function; `workflow-v2-1-core`
  itself needs no override.
- `base_work_item(**overrides)` / `base_state(**work_items)`: minimal,
  schema-valid dicts (accepted by `workflow_state.validate_state`) for
  tests that exercise state-machine logic directly, without going through
  the full `default_work_item`/`route_work_item` production path. Every
  field `validate_state` currently checks is present; `**overrides`
  replaces individual fields the way `dict.update` does.
- `base_registry(**overrides)` / `base_mapping(**overrides)`: minimal,
  schema-valid registry/mapping dicts (accepted by
  `workflow_state.validate_registry_topological_order`/
  `validate_registry_mapping_coverage`) for tests that don't need this
  work item's own real 17-checkpoint registry.

Deliberately **not** wired into the already-committed, already-approved
hermetic suites (`workflow_fingerprint_test.py`, `workflow_state_test.py`)
-- retrofitting frozen, previously-reviewed checkpoints to use a shared
harness is out of this checkpoint's own scope (no behavior change to
already-passing tests). This module exists for checkpoints that land
*after* `WF8a-i`, starting with `WF8a-ii`.

Stdlib-only. `workflow_test_harness_test.py` is this module's own
hermetic self-verification; there is no `_demo_test.py` counterpart since
this module makes no real-repository claims of its own.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


class ScratchRepo:
    """A disposable Git repo with an initial commit, usable both as a bare
    trailer/commit-history fixture and, once `write_plan_docs` has been
    called and committed, as a target for
    `workflow_fingerprint.compute_review_content_id_plan_stage`."""

    def __enter__(self) -> "ScratchRepo":
        self.root = Path(tempfile.mkdtemp(prefix="wf-harness-test-"))
        _run(["git", "init", "-q"], cwd=self.root)
        _run(["git", "config", "user.email", "test@example.com"], cwd=self.root)
        _run(["git", "config", "user.name", "Test"], cwd=self.root)
        (self.root / "README.md").write_text("base\n")
        _run(["git", "add", "README.md"], cwd=self.root)
        _run(["git", "commit", "-q", "-m", "base"], cwd=self.root)
        self.base = self.head()
        return self

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def commit(
        self, subject: str, trailers: dict[str, str] | None = None,
        filename: str | None = None,
    ) -> str:
        """Writes one file (new content each call, so the commit is never
        empty) and one commit whose body carries `trailers` as trailing
        `Key: value` lines. Returns the new commit's full SHA."""
        filename = filename or f"{subject.replace(' ', '_')}.txt"
        (self.root / filename).write_text(subject + "\n")
        _run(["git", "add", filename], cwd=self.root)
        body = subject
        if trailers:
            body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
        _run(["git", "commit", "-q", "-m", body], cwd=self.root)
        return self.head()

    def commit_files(
        self, subject: str, files: dict[str, str],
        trailers: dict[str, str] | None = None,
    ) -> str:
        """The multi-file counterpart of `commit()` (added for `WF8a-ii`):
        writes every `{repo-relative path: content}` entry in `files`
        (creating parent directories as needed) and commits them together
        in one commit carrying `trailers`. Exists for fixtures that need a
        single commit to carry both a trailer-bearing marker/product
        change and an accompanying `WORKFLOW_STATE.json` update side by
        side, mirroring a real checkpoint/approval commit -- D3/
        `OPUS-R6-005` already establishes that `WORKFLOW_STATE.json` is
        never itself a protected path, so it can legitimately ride along
        in the same commit as a checkpoint/approval trailer. Returns the
        new commit's full SHA."""
        for rel_path, content in files.items():
            full = self.root / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
        _run(["git", "add", "-A"], cwd=self.root)
        body = subject
        if trailers:
            body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
        _run(["git", "commit", "-q", "-m", body], cwd=self.root)
        return self.head()

    def write_plan_docs(
        self,
        work_item_id: str = "wi",
        plan_text: str | None = None,
        audit_text: str = "audit v1\n",
        decisions_text: str = "decisions v1\n",
        registry_text: str | None = None,
        mapping_text: str | None = None,
        plan_revision: int = 1,
    ) -> None:
        """Seeds the five plan-stage protected files at their real
        repository-relative paths for the given `work_item_id`, mirroring
        this repository's own layout (`docs/ai-workflow/registry/
        <work_item_id>-registry.json` / `.../requirements/
        <work_item_id>-mapping.json`), **plus** (`OPUS-R25-014`,
        `D-Fingerprint-Generalization`) a matching per-item
        `<work_item_id>-artifacts.json` declaring this same five-path
        fixture shape as its own `plan_stage.protected_paths` -- so
        `resolve_plan_stage_metadata`, given a `WORKFLOW_STATE.json` entry
        naming these same paths, resolves this fixture with no override
        needed, for `work_item_id="workflow-v2-1-core"` (`plan_stage_protected_paths`'s
        historical override plumbing) included. Defaulted `registry_text`/
        `mapping_text` embed `work_item_id`/`plan_revision` fields (never
        omitted, since `resolve_plan_stage_metadata` requires both); a
        caller passing an explicit `registry_text`/`mapping_text` overriding
        this default is responsible for embedding its own `work_item_id`
        field if it exercises the resolver."""
        if plan_text is None:
            plan_text = f"# Plan (Revision {plan_revision})\n\nplan v1\n"
        if registry_text is None:
            registry_text = json.dumps({
                "work_item_id": work_item_id, "plan_revision": plan_revision, "checkpoints": [],
            }) + "\n"
        if mapping_text is None:
            mapping_text = json.dumps({"work_item_id": work_item_id, "requirements": {}}) + "\n"
        (self.root / "docs" / "ai-workflow").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text(plan_text)
        (self.root / "docs" / "ai-workflow" / "WORKFLOW_V2_AUDIT.md").write_text(audit_text)
        (self.root / "docs" / "TECHNICAL_DECISIONS.md").write_text(decisions_text)
        registry_dir = self.root / "docs" / "ai-workflow" / "registry"
        registry_dir.mkdir(parents=True, exist_ok=True)
        (registry_dir / f"{work_item_id}-registry.json").write_text(registry_text)
        requirements_dir = self.root / "docs" / "ai-workflow" / "requirements"
        requirements_dir.mkdir(parents=True, exist_ok=True)
        (requirements_dir / f"{work_item_id}-mapping.json").write_text(mapping_text)
        artifacts = {
            "schema_version": 2,
            "work_item_id": work_item_id,
            "plan_stage": {
                "protected_paths": sorted(plan_stage_protected_paths(work_item_id)),
                "excluded_paths": {
                    ".gitignore": "repository housekeeping, not design content",
                    "docs/ai-workflow/WORKFLOW_STATE.json": "runtime-mutable per-work-item state",
                    "docs/ai-workflow/WORKFLOW_CONFIG.json": "runtime-mutable repository-level config",
                },
                "excluded_prefixes": {
                    "docs/ai-workflow/registry/": "non-immutable registry artifacts, including this item's own artifacts-declarations file",
                    "docs/ai-workflow/requirements/": "non-immutable requirements artifacts",
                    "scripts/": "workflow tooling scripts, present or future",
                },
            },
        }
        (registry_dir / f"{work_item_id}-artifacts.json").write_text(json.dumps(artifacts) + "\n")

    def commit_plan_docs_as_base(self) -> None:
        """Commits every currently-written/untracked file and advances
        `self.base` to that commit -- used when a test varies the
        protected-set *parameter* itself rather than file content, where
        the docs must already be settled, unclassified-content-neutral
        history."""
        _run(["git", "add", "-A"], cwd=self.root)
        _run(["git", "commit", "-q", "-m", "settle plan docs"], cwd=self.root)
        self.base = self.head()

    def write_workflow_state(
        self, *, active_work_item_id: str | None = None, **work_items: dict,
    ) -> None:
        """Writes `docs/ai-workflow/WORKFLOW_STATE.json` declaring the
        given `work_items` (each already a full per-item dict, e.g. from
        `workflow_state.default_work_item`), for tests exercising
        `resolve_plan_stage_metadata`/`route_work_item`/`validate_state`
        against a real, on-disk state file rather than an in-memory dict
        alone (`D-Fingerprint-Generalization`). Not committed by this call
        -- callers needing a commit-source read call `git add`/`git commit`
        (or `commit_plan_docs_as_base`) themselves, same as every other
        fixture file this harness writes."""
        state_path = self.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "schema_version": 1,
            "active_work_item_id": active_work_item_id,
            "work_items": work_items,
        }))


def plan_stage_protected_paths(work_item_id: str = "wi") -> frozenset[str]:
    """The plan-stage protected-path set `write_plan_docs`'s fixture
    actually populates for `work_item_id`, matching
    `workflow_fingerprint.PLAN_STAGE_PROTECTED`'s own three fixed-path
    entries plus the two paths `write_plan_docs` names for this specific
    `work_item_id`. Pass this as the `protected` argument to
    `compute_review_content_id_plan_stage`/`..._at_commit` for any
    `work_item_id` other than `workflow-v2-1-core`, whose real registry/
    mapping paths are what `PLAN_STAGE_PROTECTED`'s own default already
    names."""
    return frozenset({
        "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
        "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
        "docs/TECHNICAL_DECISIONS.md",
        f"docs/ai-workflow/registry/{work_item_id}-registry.json",
        f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
    })


def plan_stage_excluded_paths() -> Mapping[str, str]:
    """The plan-stage excluded-path set `write_plan_docs`'s fixture
    actually writes into `<work_item_id>-artifacts.json` (`GPT-R30-005`):
    a caller passing an explicit `protected` for a `work_item_id` other
    than `workflow-v2-1-core` must pass its own matching
    `excluded_paths`/`excluded_prefixes` too, never
    `PLAN_STAGE_EXCLUDED_PATHS`/`PLAN_STAGE_EXCLUDED_PREFIXES` (that
    module default is retired for exactly this reason -- it is
    `workflow-v2-1-core`'s own set, not a generic fixture default)."""
    return {
        ".gitignore": "repository housekeeping, not design content",
        "docs/ai-workflow/WORKFLOW_STATE.json": "runtime-mutable per-work-item state",
        "docs/ai-workflow/WORKFLOW_CONFIG.json": "runtime-mutable repository-level config",
    }


def plan_stage_excluded_prefixes() -> Mapping[str, str]:
    """Prefix counterpart of `plan_stage_excluded_paths` -- see there."""
    return {
        "docs/ai-workflow/registry/": "non-immutable registry artifacts, including this item's own artifacts-declarations file",
        "docs/ai-workflow/requirements/": "non-immutable requirements artifacts",
        "scripts/": "workflow tooling scripts, present or future",
    }


def base_work_item(**overrides: object) -> dict:
    """A minimal work-item dict accepted by
    `workflow_state.validate_state`/`_validate_work_item`. `**overrides`
    replaces individual fields."""
    work_item = {
        "work_item_type": "process",
        "work_item_kind": "process",
        "work_item_id": "wi",
        "parent_work_item_id": None,
        "governing_workflow_version": "1",
        "phase": "IMPLEMENTING",
        "checkpoints": {},
        "plan_review_stages": None,
        # Matches `base_registry`'s own default `plan_revision=1`
        # (`GPT-R30-004`, `PlanRevisionMirrorMismatchError`): a fixture
        # combining both builders' defaults with neither overridden must
        # not spuriously fail `validate_state`'s state/registry mirror
        # check.
        "plan_revision": 1,
    }
    work_item.update(overrides)
    return work_item


def base_state(**work_items: dict) -> dict:
    """A minimal `WORKFLOW_STATE.json`-shaped dict; `active_work_item_id`
    is left `None`. Pass keyword work items, e.g. `base_state(wi=
    base_work_item())`."""
    return {"schema_version": SCHEMA_VERSION, "active_work_item_id": None, "work_items": work_items}


def base_registry(
    work_item_id: str = "wi", plan_revision: int = 1, checkpoints: list[dict] | None = None,
) -> dict:
    """A minimal registry dict accepted by
    `workflow_state.validate_registry_topological_order`/
    `validate_registry_mapping_coverage`. `checkpoints` must already be in
    a topological order of its own `depends_on` columns if non-empty --
    this builder does not validate it, so a test can also use it to
    construct a deliberately invalid fixture."""
    return {
        "schema_version": SCHEMA_VERSION,
        "work_item_id": work_item_id,
        "plan_revision": plan_revision,
        "checkpoints": checkpoints if checkpoints is not None else [],
    }


def base_mapping(work_item_id: str = "wi", requirements: dict | None = None) -> dict:
    """A minimal requirements-mapping dict accepted by
    `workflow_state.validate_registry_mapping_coverage`."""
    return {
        "schema_version": SCHEMA_VERSION,
        "work_item_id": work_item_id,
        "requirements": requirements if requirements is not None else {},
    }
