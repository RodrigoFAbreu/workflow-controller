#!/usr/bin/env python3
"""Workflow v2.x end-to-end acceptance matrix (salvage audit, repair `R6`).

Every row of `docs/ai-workflow/audit/WORKFLOW_ACCEPTANCE_MATRIX.md` is
executed here against a disposable Git repository that contains a real
copy of this repository's own workflow tooling, driving the **real**
`scripts/prepare-ai-review.sh` and the exact `workflow_state`/
`workflow_fingerprint` entry points the `.claude/commands/*.md` files
name -- never a private reimplementation of a command's logic.

Why this suite exists (ledger row `I5`): before it, the `same_content`
and recovered generation modes were covered only at the helper level, so
two commands (`/apply-implementation-review`'s same-content post-fix path
and `/recover-implementation-provenance` in its entirety) had never been
executed end to end, and neither could publish its bundle at all. The one
suite that did drive the generation script for round identity built its
state by writing `WORKFLOW_STATE.json` directly rather than through
`record_bundle_generation`, and encoded that defect as its specification.

Scope and discipline:

- **Both work-item types.** Every lifecycle row runs for a `process` item
  (deliverable prefix `scripts/`) and a `product` item (deliverable
  prefix `app/`), since the plan-stage half of this system was type-blind
  for its whole history and the implementation-stage half still is
  wherever a declaration is hand-authored.
- **The real boundary.** Bundle generation always goes through
  `scripts/prepare-ai-review.sh` as a subprocess. State transitions always
  go through `workflow_state.state_transaction` with the exact mutator the
  owning command names.
- **Hermetic and stdlib-only**, like every other suite here: no network,
  no installed packages, no dependence on this repository's own live
  `WORKFLOW_STATE.json` or on a fixed historical base commit, so it is
  safe to run on every PR.

Run it directly:

    python3 scripts/workflow_acceptance_matrix_test.py
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import workflow_fingerprint as fingerprint
import workflow_state as ws

SCRIPT_FILES = (
    "workflow_state.py",
    "workflow_fingerprint.py",
    "prepare-ai-review.sh",
)


def _tooling_dir() -> Path:
    """The directory whose tooling is under test: the one this file lives
    in. Deliberately not resolved through `git rev-parse` -- the suite must
    exercise the copy sitting next to it, including when it is run from a
    scratch checkout of a different revision (which is how each repair's
    pre-fix reproduction is proven)."""
    return Path(__file__).resolve().parent


def _run(args, cwd, check=True, env=None, unset=()):
    full_env = {key: value for key, value in os.environ.items() if key not in unset}
    full_env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
    full_env.setdefault("GIT_CONFIG_SYSTEM", "/dev/null")
    if env:
        full_env.update(env)
    proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, env=full_env)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed ({proc.returncode}): {args}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


class Scratch:
    """A disposable Git repository carrying a real copy of the tooling."""

    def __init__(self, name="wf-acceptance"):
        self.root = Path(tempfile.mkdtemp(prefix=f"{name}-"))
        self._worktrees = []
        source = _tooling_dir()
        _run(["git", "init", "-q", "-b", "main"], cwd=self.root)
        _run(["git", "config", "user.email", "acceptance@example.com"], cwd=self.root)
        _run(["git", "config", "user.name", "Acceptance"], cwd=self.root)
        _run(["git", "config", "commit.gpgsign", "false"], cwd=self.root)
        (self.root / "scripts").mkdir()
        for name_ in SCRIPT_FILES:
            shutil.copy2(source / name_, self.root / "scripts" / name_)
        os.chmod(self.root / "scripts" / "prepare-ai-review.sh", 0o755)
        self.write(".gitignore", ".ai-review/\n__pycache__/\n*.pyc\n")
        self.write("README.md", "scratch repository\n")
        self.write("AGENTS.md", "agents\n")
        self.write("CLAUDE.md", "claude\n")

    @classmethod
    def attach(cls, root):
        """A `Scratch` view of an existing checkout -- a linked worktree
        of another scratch repository, or a scratch repository reopened
        from a worker process (workflow-2.6.0, CP6). Initializes nothing
        and cleans nothing up."""
        scratch = cls.__new__(cls)
        scratch.root = Path(root)
        scratch._worktrees = []
        return scratch

    def worktree(self, name):
        """A linked worktree on a new branch `name` at `HEAD`, removed by
        `cleanup` -- a second Claude Code session's checkout."""
        path = self.root.parent / f"{self.root.name}-{name}"
        self.git("worktree", "add", "-q", "-b", name, str(path), "HEAD")
        self._worktrees.append(path)
        return Scratch.attach(path)

    def cleanup(self):
        for path in getattr(self, "_worktrees", []):
            shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def read(self, rel):
        return (self.root / rel).read_text()

    def git(self, *args, check=True, unset=()):
        return _run(["git", *args], cwd=self.root, check=check, unset=unset)

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def commit(self, subject, trailers=None, paths=None):
        if paths is None:
            self.git("add", "-A")
        else:
            self.git("add", "--", *paths)
        body = subject
        if trailers:
            body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
        self.git("commit", "-q", "-m", body)
        return self.head()

    def prepare(self, base, stage, work_item_id=None, check=True):
        args = ["./scripts/prepare-ai-review.sh", base, stage]
        if work_item_id:
            args.append(work_item_id)
        return _run(args, cwd=self.root, check=check)


PROCESS_DELIVERABLE = "scripts/feature.py"
PRODUCT_DELIVERABLE = "app/src/main/kotlin/Feature.kt"

WORK_ITEM_PROFILES = {
    "process": {
        "work_item_id": "proc-item",
        "plan_path": "docs/ai-workflow/proc-item-plan.md",
        "deliverable": PROCESS_DELIVERABLE,
        "excluded_note": "docs/ai-workflow/requirements/proc-item-ledger.md",
    },
    "product": {
        "work_item_id": "milestone-9",
        "plan_path": "docs/milestones/milestone-9-plan.md",
        "deliverable": PRODUCT_DELIVERABLE,
        "excluded_note": "docs/ai-workflow/requirements/milestone-9-ledger.md",
    },
}

CHECKPOINTS = [
    {"id": "CP1", "name": "first", "depends_on": [], "complexity": "S", "session_target": 1},
]
REQUIREMENTS = {"R1": {"description": "do the thing", "checkpoint_ids": ["CP1"]}}

CHECKPOINTS_TWO = CHECKPOINTS + [
    {"id": "CP2", "name": "second", "depends_on": ["CP1"], "complexity": "S", "session_target": 1},
]
REQUIREMENTS_TWO = {
    "R1": {"description": "do the thing", "checkpoint_ids": ["CP1"]},
    "R2": {"description": "and the other thing", "checkpoint_ids": ["CP2"]},
}


def plan_anchors(registry):
    """One `D-Plan-Amendment-4` anchor pair per registry checkpoint, with
    content that depends only on the checkpoint row -- so an amendment that
    leaves a checkpoint unchanged reconciles it as unchanged (workflow-2.6.0
    routes every mid-implementation re-plan through an amendment)."""
    return "".join(
        f"<!-- {c['id']} -->\n{c['id']} -- {c['name']}.\n<!-- /{c['id']} -->\n\n"
        for c in registry["checkpoints"]
    )


class Item:
    """One work item's lifecycle, driven exactly the way the owning
    `.claude/commands/*.md` file says to drive it."""

    def __init__(self, scratch: Scratch, work_item_type: str, governing="2.1"):
        profile = WORK_ITEM_PROFILES[work_item_type]
        self.sim = scratch
        self.root = scratch.root
        self.wtype = work_item_type
        self.wid = profile["work_item_id"]
        self.plan_path = profile["plan_path"]
        self.deliverable = profile["deliverable"]
        self.excluded_note = profile["excluded_note"]
        self.registry_path = f"docs/ai-workflow/registry/{self.wid}-registry.json"
        self.mapping_path = f"docs/ai-workflow/requirements/{self.wid}-mapping.json"
        self.artifacts_path = f"docs/ai-workflow/registry/{self.wid}-artifacts.json"
        self.governing = governing
        self.base_commit = None
        self._clock = 0
        #: Set by `approve_implementation` to whatever
        #: `assert_feedback_matches_bundle` reported, or `None` when the
        #: binding matched -- observed and reported, never fatal, exactly
        #: as `/approve-review` step 2 says (ledger `O35`).
        self.last_feedback_binding_mismatch = None

    @classmethod
    def attach(cls, scratch, work_item_type, governing="2.1", clock_start=0):
        """The same work item seen from another checkout (a linked
        worktree, or a worker process): its `base_commit` read back from
        that checkout's own state (workflow-2.6.0, CP6)."""
        item = cls(scratch, work_item_type, governing)
        item.base_commit = item.entry()["base_commit"]
        item._clock = clock_start
        return item

    # ---------------- plumbing ----------------

    @staticmethod
    def _hook(hooks, point, *args):
        """Run the caller-supplied callable registered for `point`, if any
        -- the seams CP6's rows use to pause, crash or inspect the
        transaction between two of the command's steps."""
        callback = (hooks or {}).get(point)
        if callback is not None:
            callback(*args)

    def now(self):
        self._clock += 1
        return f"2026-01-01T{self._clock // 3600:02d}:{self._clock // 60 % 60:02d}:{self._clock % 60:02d}Z"

    def tx(self, mutator):
        return ws.state_transaction(self.root, mutator)

    def state(self):
        return json.loads(self.sim.read("docs/ai-workflow/WORKFLOW_STATE.json"))

    def entry(self):
        return self.state()["work_items"][self.wid]

    def registry(self):
        return json.loads(self.sim.read(self.registry_path))

    def bundle_dir(self, stage=None):
        """The **production** resolver, never a hardcoded substitute
        (convergence repair `I2`): every row below that claims a
        command-level generation, regeneration or recovery behavior has to
        resolve its bundle directory through the exact function the
        owning `.claude/commands/*.md` file names, or the row cannot see a
        misresolution at all -- which is precisely how repair `I1`'s
        first-plan-bundle defect survived a green matrix. `stage` is
        passed exactly where the owning command passes it: `"plan"` for
        the plan-stage commands (scoped by construction), omitted for the
        implementation/post-fix stages, whose commands document the
        scoped-else-flat compatibility rule because their own
        `prepare-ai-review.sh` `[work-item-id]` argument is optional.
        Deliberately does **not** create the directory: creating it is
        what made the hardcoded version answer correctly by side effect."""
        return self.root / fingerprint.resolve_bundle_dir(self.root, self.wid, stage=stage)

    def feedback_dir(self):
        """`resolve_feedback_dir`, the production resolver, for the same
        reason `bundle_dir` uses `resolve_bundle_dir` (convergence repair
        `I2`). It takes no stage argument at any stage: `feedback/` is
        stage-agnostic (`REVIEW_PROTOCOL.md`, `REQ-21`).

        Re-pointed by `D-Feedback-Layout` (workflow-2.6.0): this item is
        created through `route_work_item`, so it is stamped
        `feedback_layout: "scoped"` and resolves
        `.ai-review/<wid>/feedback` by construction -- asserted here, so a
        regression to the legacy scoped-else-flat rule fails every
        scenario that writes feedback. The directory is created through
        `ensure_feedback_dir`, the same call every production writer
        makes, never at a hardcoded path."""
        rel = fingerprint.ensure_feedback_dir(self.root, self.wid)
        assert rel == Path(".ai-review") / self.wid / "feedback", rel
        assert fingerprint.resolve_feedback_layout(self.root, self.wid) == "scoped"
        return self.root / rel

    def manifest(self):
        return (self.bundle_dir() / "MANIFEST.md").read_text()

    def bundle_id(self):
        return fingerprint.read_manifest_identifiers(self.bundle_dir() / "MANIFEST.md")["bundle_id"]

    def impl_classification(self):
        return fingerprint.load_implementation_stage_classification(
            self.root, fingerprint.artifacts_path_for_work_item(self.wid),
        )

    def impl_review_content_id(self, commit):
        digest, projection = fingerprint.compute_review_content_id_implementation_stage_at_commit(
            self.root, self.base_commit, commit, self.wtype, self.wid, *self.impl_classification(),
        )
        return digest, projection

    # ---------------- seeding ----------------

    def seed(self, extra_files=None):
        """Repository shape before this item's `base_commit`."""
        self.sim.write("docs/ai-workflow/WORKFLOW_CONFIG.json", json.dumps({
            "schema_version": 1,
            "default_workflow_version": self.governing,
            "supported_versions": ["1", "2.1"],
        }, indent=2) + "\n")
        self.sim.write("docs/ai-workflow/WORKFLOW_STATE.json", json.dumps({
            "schema_version": 1, "active_work_item_id": None, "work_items": {},
        }, indent=2) + "\n")
        self.sim.write("docs/ACTIVE_MILESTONE.md", "# Active milestone\n\n(nothing yet)\n")
        self.sim.write("docs/ROADMAP.md", "# Roadmap\n")
        if self.wtype == "product":
            self.sim.write("app/src/main/kotlin/Existing.kt", "class Existing\n")
        for rel, content in (extra_files or {}).items():
            self.sim.write(rel, content)
        self.base_commit = self.sim.commit("base: repository before this work item")
        return self.base_commit

    # ---------------- /milestone-plan ----------------

    def milestone_plan(self, plan_revision=1, checkpoints=None, requirements=None,
                       plan_body="Plan body.\n", artifacts=None, commit=False):
        """`/milestone-plan` steps 1-3 and 6's own writes. Nothing is
        committed by default: in the real flow the plan document, registry,
        mapping and artifacts declaration stay uncommitted until
        `/approve-review plan`'s own approval commit stages them as its
        four-or-five-member set -- which is what makes the conditional
        fifth member (the artifacts declaration) apply at all. The live
        `workflow-v2-3-1` plan-approval commit `718f619` has exactly that
        five-file shape."""
        config = json.loads(self.sim.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        checkpoints = CHECKPOINTS if checkpoints is None else checkpoints
        requirements = REQUIREMENTS if requirements is None else requirements
        self.tx(lambda state: ws.route_work_item(
            state, config, work_item_id=self.wid, work_item_type=self.wtype,
            work_item_kind=self.wtype, plan_path=self.plan_path,
            registry_path=self.registry_path, plan_revision=plan_revision,
            now=self.now(), mapping_path=self.mapping_path, base_commit=self.base_commit,
            repo_root=self.root,
        ))
        registry = ws.generate_registry(self.wid, plan_revision, checkpoints)
        mapping = ws.generate_mapping(self.wid, requirements, registry=registry)
        ws.write_registry_and_mapping(
            self.root, Path(self.registry_path), Path(self.mapping_path), registry, mapping,
        )
        declarations = artifacts if artifacts is not None else ws.generate_artifacts_declarations(
            self.wid, self.plan_path, self.registry_path, self.mapping_path,
            work_item_type=self.wtype,
        )
        self.sim.write(self.artifacts_path, json.dumps(declarations, indent=2) + "\n")
        self.sim.write(
            self.plan_path,
            f"# {self.wid} plan (Revision {plan_revision})\n\n{plan_body}\n"
            + plan_anchors(registry) + ws.render_registry_markdown(registry) + "\n",
        )
        # workflow-2.6.0 (D-Plan-Review-Bundle-Binding): the publication
        # point -- intent-to-add staging first (LPR-R3-003), then the
        # mirror-only publish of the fresh id. The phase stays non-ready
        # until `generate_plan_bundle` binds the generated bundle.
        self.stage_plan_files()
        self.publish()
        if commit:
            self.sim.commit(f"plan({self.wid}): revision {plan_revision}",
                            {"Workflow-Work-Item": self.wid})

    def publish(self):
        """`publish_plan_revision` at the registry's revision, with the
        fresh plan-stage id computed inside the same transaction."""
        def mutator(state):
            digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(self.root, self.wid)
            return ws.publish_plan_revision(
                state, self.wid, self.registry()["plan_revision"], self.now(), review_content_id=digest,
            )
        self.tx(mutator)

    def bind_plan_bundle(self):
        """`/milestone-plan` step 6's / `/apply-plan-review` step 7''s
        verify-then-bind."""
        binding = ws.verify_plan_review_bundle(self.root, self.wid)
        self.tx(lambda state: ws.bind_plan_review_bundle(state, self.wid, binding=binding, now=self.now()))
        return binding

    def stage_plan_files(self):
        """`/milestone-plan` step 3's staging step (salvage audit `B6`):
        the four plan-stage files are marked intent-to-add and left that
        way, so `resolve_plan_stage_metadata`'s tracked-path requirement
        is satisfied and `/approve-review plan`'s own commit is what
        commits them. Literal, as step 3 now states (round 3): a declared
        `:x` path is marked as itself, never as pathspec magic for `x`."""
        self.sim.git("--literal-pathspecs", "add", "-N", "--", self.plan_path, self.registry_path,
                     self.mapping_path, self.artifacts_path,
                     unset=fingerprint.CONFLICTING_PATHSPEC_ENV)

    # ---------------- plan-stage bundle ----------------

    def generate_plan_bundle(self, check=True, test_results=None, review_request=None, bind=True):
        """Writes the author inputs to `plan-inputs/` (workflow-2.6.0: never
        `current/`), runs the real generator, and -- as the commands do,
        straight after a successful generation at a non-ready phase --
        verifies and binds the bundle. A regeneration at a ready phase is
        wrapper-only and binds nothing."""
        bundle = self.root / fingerprint.resolve_plan_review_inputs_dir(self.root, self.wid)
        bundle.mkdir(parents=True, exist_ok=True)
        digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(self.root, self.wid)
        metadata = fingerprint.resolve_plan_stage_metadata(self.root, self.wid)
        (bundle / "REVIEW_REQUEST.md").write_text(review_request if review_request is not None else (
            f"# Review request\n\nstage: plan\nwork item: {self.wid}\n"
            f"review_content_id: {digest}\n"
        ))
        (bundle / "TEST_RESULTS.md").write_text(test_results if test_results is not None else (
            f"stage: plan (revision {metadata.plan_revision})\nhead: {self.sim.head()}\n\n"
            "No automated checks are required at the plan stage.\n"
        ))
        (bundle / "CONTEXT_FILES.txt").write_text("")
        proc = self.sim.prepare(self.base_commit, "plan", self.wid, check=check)
        if bind and proc.returncode == 0 and self.entry()["phase"] in ws.PLAN_REVIEW_NON_READY_PHASES:
            self.bind_plan_bundle()
        return proc

    # ---------------- review feedback ----------------

    def write_feedback(self, status="APPROVE", bundle_id=None, base_commit=None,
                       work_item=None, role=None, extra=""):
        lines = [
            "# Review Decision", "", f"Status: {status}", "",
            f"Reviewed bundle ID: {bundle_id or self.bundle_id()}",
            f"Reviewed base commit: {base_commit or self.base_commit}",
            f"Work item: {work_item or self.wid}",
        ]
        if role:
            lines.append(f"Reviewer role: {role}")
        lines += ["", extra, ""]
        (self.feedback_dir() / "REVIEW_FEEDBACK.md").write_text("\n".join(lines))

    def feedback_fields(self):
        return fingerprint.parse_review_feedback_binding_fields(
            (self.feedback_dir() / "REVIEW_FEEDBACK.md").read_text()
        )

    # ---------------- /review-plan + /record-manual-plan-review ----------------

    def record_plan_reviews(self, local="APPROVE", manual="APPROVE", round=1):
        review_content_id, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            self.root, self.wid,
        )
        bundle_id = self.bundle_id()
        self.tx(lambda state: ws.record_local_plan_review(
            state, self.wid, verdict=local, bundle_id=bundle_id,
            review_content_id=review_content_id, round=round, now=self.now(),
        ))
        if local != "APPROVE":
            return
        self.tx(lambda state: ws.record_manual_plan_review(
            state, self.wid, verdict=manual, bundle_id=bundle_id, round=round,
            now=self.now(), current_review_content_id=review_content_id,
            feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id=review_content_id,
        ))

    # ---------------- /approve-review plan ----------------

    def approve_plan(self, user_confirmation=None, stop_after=None, commit_env=None,
                     before_proof=None, hooks=None):
        """`/approve-review plan` steps 1-6d: the full journal/guard/
        staging/commit/classify/verify/materialize transaction.

        workflow-2.6.0 (`D-Plan-Approval-Closure`): a **command-shaped**
        driver. It follows the command's real data flow -- step 2's
        bundle-bound check, step 4a's fresh member set (protected paths
        and removals), step 5's in-window re-resolution, step 6.3a's
        write-tree proof, and step 6a's verification from the committed
        transaction (`verify_plan_approval_commit`), never a hand-built
        post-state. A failure from step 5 through 6.3a takes step 6b's
        rollback and re-raises. `stop_after="commit"` returns right after
        step 6.4, simulating a crash; `complete_plan_approval` is the
        resume. `commit_env` is passed to step 6.4's `git commit` (hooks);
        `before_proof` runs just before step 6.3a (index tampering).

        workflow-2.6.0 (`D-Repo-Global-Lifecycle`, CP6): step 4d reserves
        the open amendment's resolution right after 4c opens the journal
        (a no-op for an item with no open amendment), step 5 stages
        through `stage_plan_approval_members` in `first_commit` mode, a
        refused reservation takes step 6b's rollback, and 6b captures the
        journal's tokens before the rollback so the release that follows
        it matches the reservation. `hooks` maps a step boundary
        (`after_journal`, `after_reserve`, `after_stage`, `after_pin`,
        `after_commit`, and `complete_plan_approval`'s own) to a
        callable."""
        confirmation = user_confirmation or f"plan {self.wid}"
        entry = self.entry()
        review_content_id, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            self.root, self.wid,
        )
        bundle_id = self.bundle_id()
        fingerprint.assert_local_generation_matches(self.root, self.bundle_dir(stage="plan") / "MANIFEST.md")
        fingerprint.assert_bundle_not_rejected(self.root, self.wid)
        if entry["governing_workflow_version"] in ws.TWO_STAGE_PLAN_REVIEW_VERSIONS:
            ws.assert_plan_review_bundle_bound(self.root, self.wid)
        feedback = self.feedback_fields()
        if not ws.plan_approval_gate_reachable(
            latest_round_status=feedback["status"],
            governing_workflow_version=entry["governing_workflow_version"],
            plan_review_stages=entry.get("plan_review_stages"),
            current_review_content_id=review_content_id,
        ):
            raise AssertionError("plan-approval gate not reachable")
        basis = ws.resolve_approval_basis(
            latest_round_status=feedback["status"],
            feedback_bundle_id=feedback["reviewed_bundle_id"],
            current_bundle_id=bundle_id, user_confirmation=confirmation,
            work_item_id=self.wid, stage="plan",
        )
        approval_now = self.now()
        record = ws.build_approval_record(
            basis=basis, stage="plan", user_confirmation=confirmation, now=approval_now,
            reviewed_bundle_id=bundle_id, approved_review_content_id=review_content_id,
            review_content_manifest=projection["review_content_manifest"],
        )
        # Step 4a: the complete, fresh member set, before any mutation.
        plan = ws.resolve_fresh_plan_approval_members(self.root, self.wid)
        pre_state = self.state()
        amendment_kwargs = {}
        history = entry.get("amendment_history") or []
        if history and history[-1].get("resolved_at_plan_revision") is None:
            pre_plan_text, pre_registry = ws.load_pre_amendment_snapshot(
                self.root, self.wid, self.plan_path, self.registry_path, history[-1],
            )
            amendment_kwargs = dict(
                pre_registry=pre_registry, pre_plan_text=pre_plan_text,
                post_registry=self.registry(), post_plan_text=self.sim.read(self.plan_path),
            )
        journal = ws.open_plan_approval_journal(
            self.root, work_item_id=self.wid, base_commit=self.base_commit,
            pre_state=pre_state, record=record, approval_now=approval_now, **amendment_kwargs,
            expected_bundle_id=bundle_id, expected_review_content_id=review_content_id,
            applicable_paths=plan.paths, removal_paths=plan.removal_paths,
            fifth_member_applies=plan.artifacts_declaration_path is not None,
            fifth_member_sha256=plan.artifacts_declaration_sha256,
            user_confirmation=confirmation,
            quiescence_authorization="acceptance-matrix scenario",
        )
        owner = journal["owner_token"]
        self._hook(hooks, "after_journal", journal)
        try:
            # Step 4d: the reservation, holding (9) alone, before staging.
            ws.reserve_amendment_resolution(self.root, self.wid, journal, now=self.now())
        except BaseException:
            self.rollback_plan_approval(owner)
            raise
        self._hook(hooks, "after_reserve", journal)
        try:
            with ws.plan_approval_guarded_mutation(
                self.root, owner_token=owner, step="step-5-stage-and-pin", now=self.now(),
            ):
                ws.assert_plan_approval_member_set_unchanged(self.root, journal)
                self._stage_and_pin_members(journal, mode=ws.PLAN_APPROVAL_STAGING_FIRST_COMMIT)
            self._hook(hooks, "after_stage", journal)
            with ws.plan_approval_guarded_mutation(
                self.root, owner_token=owner, step="step-6.1b-state-pin", now=self.now(),
            ):
                self._pin_state_blob(journal)
            self._hook(hooks, "after_pin", journal)
            ws.assert_staged_path_set_within(self.root, journal["applicable_paths"])
            if before_proof is not None:
                before_proof()
            ws.prove_plan_approval_index_closure(self.root, journal)
        except BaseException:
            self.rollback_plan_approval(owner)
            raise
        with ws.plan_approval_guarded_mutation(
            self.root, owner_token=owner, step="step-6.5-commit", now=self.now(),
        ):
            _run(["git", "commit", "-q", "-m", (
                f"chore({self.wid}): plan-stage approval\n\nBasis: {basis}.\n\n"
                f"Workflow-Plan-Approval: {review_content_id}\n"
                f"Workflow-Work-Item: {self.wid}\n"
            )], cwd=self.root, env=commit_env)
        self._hook(hooks, "after_commit", journal)
        if stop_after == "commit":
            return self.sim.head()
        return self.complete_plan_approval(owner, stop_after=stop_after, hooks=hooks)

    def _stage_and_pin_members(self, journal, *, mode, resolution_held=None):
        """Step 5's (and 6a1's) staging body, through the one staging
        entry: every non-state member, removals as deletions, then the
        artifacts-declaration pin -- after asserting the evidence `mode`
        requires on an open-amendment item (workflow-2.6.0)."""
        ws.stage_plan_approval_members(self.root, journal, mode=mode,
                                       resolution_held=resolution_held)

    def _pin_state_blob(self, journal):
        """Step 6.2's (and 6a1's) body: compare-and-swap, pin, verify."""
        if not ws.plan_approval_state_matches_pre_transaction(
            self.root, journal["pre_procedure_state_sha256"],
        ):
            raise AssertionError("WORKFLOW_STATE.json changed since journal open")
        ws.pin_plan_approval_state_blob(
            self.root, base64.b64decode(journal["expected_post_state_b64"]),
        )
        ws.verify_staged_plan_approval_state_blob(
            self.root, journal["expected_post_state_sha256"],
        )

    def rollback_plan_approval(self, owner, *, release_tokens=None):
        """Step 6b: capture the journal's `owner_token` plus every
        `previous_owner_tokens` entry *before* the rollback closes the
        journal, then the guarded index reset and journal close, then
        (workflow-2.6.0) `release_amendment_resolution` with the captured
        tokens -- a no-op unless this transaction's reservation is live.
        `release_tokens` overrides the captured set (CP6 test 28's
        regression variant)."""
        journal = ws.read_plan_approval_journal(self.root)
        tokens = ([journal["owner_token"], *journal["previous_owner_tokens"]]
                  if journal is not None else [owner])
        lease = ws.acquire_plan_approval_guard(
            self.root, holder_owner_token=owner, step="rollback-index-reset", now=self.now(),
        )
        try:
            ws.rollback_plan_approval_transaction(self.root, owner_token=owner)
        finally:
            ws.release_plan_approval_guard(self.root, lease)
        ws.release_amendment_resolution(
            self.root, self.wid, tokens if release_tokens is None else release_tokens)

    def complete_plan_approval(self, owner, stop_after=None, hooks=None, release_tokens=None):
        """Steps 6a-6d from durable state alone -- what the in-session run
        and every resumed or taken-over run execute: classify, verify the
        committed transaction (6a1's single amend only for a
        `TREE_CONTENT` failure), materialize, close. `stop_after=
        "materialize"` returns before 6d, simulating a crash there.

        workflow-2.6.0 (CP6): 6a1 first runs
        `assert_amendment_resolution_held` (outside every guarded window)
        and re-stages in `amend_recovery` mode with its proof; the advance
        (`advance_amendment_witness` with the journal and the verified
        commit) runs between 6c and 6d, so the journal outlives the
        reservation's `RESOLVING` state. Never reaches step 4d."""
        evidence = ws.plan_approval_takeover_evidence(self.root)
        journal = evidence["journal"]
        if journal is None or evidence["owner_token"] != owner:
            raise AssertionError(f"no open plan-approval transaction owned by {owner!r}")
        outcome = evidence["outcome"]
        if outcome == ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED:
            self.rollback_plan_approval(owner, release_tokens=release_tokens)
            return None
        if outcome != ws.PLAN_APPROVAL_OUTCOME_COMMITTED:
            raise AssertionError(f"unexpected plan-approval outcome: {outcome}")
        commit = ws.discover_plan_approval_commit(
            self.root, self.wid, journal["expected_review_content_id"], journal["base_commit"], "HEAD",
        )
        try:
            ws.verify_plan_approval_commit(self.root, journal, commit)
        except Exception as exc:
            if ws.classify_post_commit_verification_failure(exc) != ws.POST_COMMIT_FAILURE_TREE_CONTENT:
                raise
            # 6a1: the one amend, only for a proven tree-content defect --
            # after the held check, which takes (9) alone and writes nothing.
            proof = ws.assert_amendment_resolution_held(self.root, self.wid, journal)
            self._hook(hooks, "after_held_check", journal)
            with ws.plan_approval_guarded_mutation(
                self.root, owner_token=owner, step="step-7b-amend-stage", now=self.now(),
            ):
                self._stage_and_pin_members(
                    journal, mode=ws.PLAN_APPROVAL_STAGING_AMEND_RECOVERY, resolution_held=proof)
                self._pin_state_blob(journal)
            with ws.plan_approval_guarded_mutation(
                self.root, owner_token=owner, step="step-7d-amend-commit", now=self.now(),
            ):
                self.sim.git("commit", "-q", "--amend", "--no-edit")
            commit = self.sim.head()
            ws.verify_plan_approval_commit(self.root, journal, commit)
        with ws.plan_approval_guarded_mutation(
            self.root, owner_token=owner, step="step-8b-materialize", now=self.now(),
        ):
            pre_state = json.loads(base64.b64decode(journal["pre_procedure_state_b64"]))
            post_state = json.loads(base64.b64decode(journal["expected_post_state_b64"]))
            if ws.classify_plan_approval_materialize_target(
                self.root, self.wid, pre_state, post_state,
            ) == ws.PLAN_APPROVAL_MATERIALIZE_WRITE:
                ws.materialize_plan_approval_state(
                    self.root, commit, journal["expected_post_state_sha256"],
                )
        self._hook(hooks, "after_materialize", journal)
        if stop_after == "materialize":
            return commit
        # Between 6c and 6d: bind the resolution (a no-op without one).
        ws.advance_amendment_witness(self.root, self.wid, journal=journal, commit=commit)
        self._hook(hooks, "after_advance", journal)
        with ws.plan_approval_guarded_mutation(
            self.root, owner_token=owner, step="step-8a-close-journal", now=self.now(),
        ):
            ws.close_plan_approval_journal(self.root)
        self._hook(hooks, "after_close", journal)
        return commit

    # ---------------- /request-plan-amendment + amended /milestone-plan ----------------

    def request_amendment(self, reason="plan correction", commit=False):
        """`/request-plan-amendment <id>` step 2 -- since workflow-2.6.0 the
        only sanctioned route from `IMPLEMENTING`/
        `SELF_REVIEWING_IMPLEMENTATION` back into plan review, and (CP6)
        always through `request_plan_amendment_transaction`: the lifecycle
        lock (9), the witness predicate list, then the `OPEN` witness and
        the state. `commit=True` adds step 3, the state write committed
        alone."""
        ws.request_plan_amendment_transaction(self.root, self.wid, reason, now=self.now())
        if commit:
            self.sim.commit(f"chore({self.wid}): request plan amendment",
                            {"Workflow-Work-Item": self.wid},
                            paths=["docs/ai-workflow/WORKFLOW_STATE.json"])

    def amend_plan(self, plan_revision, checkpoints=None, requirements=None,
                   plan_body="Plan body, amended.\n", artifacts=None):
        """`/milestone-plan <id>` on an `AMENDING_PLAN` item: step 1's
        mirror advance, the regenerated registry and mapping, the plan
        document with one anchor pair per checkpoint (`D-Plan-Amendment-4`),
        then the publication point. `generate_plan_bundle` binds it."""
        config = json.loads(self.sim.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        checkpoints = CHECKPOINTS if checkpoints is None else checkpoints
        requirements = REQUIREMENTS if requirements is None else requirements
        self.tx(lambda state: ws.ensure_plan_review_binding_marker(state, self.wid, self.now()))
        self.tx(lambda state: ws.route_work_item(
            state, config, work_item_id=self.wid, work_item_type=self.wtype,
            work_item_kind=self.wtype, plan_path=self.plan_path,
            registry_path=self.registry_path, plan_revision=plan_revision,
            now=self.now(), mapping_path=self.mapping_path, base_commit=self.base_commit,
            repo_root=self.root,
        ))
        registry = ws.generate_registry(self.wid, plan_revision, checkpoints)
        mapping = ws.generate_mapping(self.wid, requirements, registry=registry)
        ws.write_registry_and_mapping(
            self.root, Path(self.registry_path), Path(self.mapping_path), registry, mapping,
        )
        if artifacts is not None:
            self.sim.write(self.artifacts_path, json.dumps(artifacts, indent=2) + "\n")
        self.sim.write(
            self.plan_path,
            f"# {self.wid} plan (Revision {plan_revision})\n\n{plan_body}\n"
            + plan_anchors(registry) + ws.render_registry_markdown(registry) + "\n",
        )
        self.stage_plan_files()
        self.publish()

    # ---------------- /apply-plan-review ----------------

    def apply_plan_review(self, plan_revision, checkpoints=None, requirements=None,
                          plan_body="Plan body, revised.\n"):
        checkpoints = CHECKPOINTS if checkpoints is None else checkpoints
        requirements = REQUIREMENTS if requirements is None else requirements
        registry = ws.generate_registry(self.wid, plan_revision, checkpoints)
        mapping = ws.generate_mapping(self.wid, requirements, registry=registry)
        ws.write_registry_and_mapping(
            self.root, Path(self.registry_path), Path(self.mapping_path), registry, mapping,
        )
        self.sim.write(
            self.plan_path,
            f"# {self.wid} plan (Revision {plan_revision})\n\n{plan_body}\n"
            + plan_anchors(registry) + ws.render_registry_markdown(registry) + "\n",
        )
        # workflow-2.6.0: step 5 stages, then publishes on every round;
        # step 7' binds the bundle step 5 generated (`generate_plan_bundle`).
        self.tx(lambda state: ws.ensure_plan_review_binding_marker(state, self.wid, self.now()))
        self.stage_plan_files()
        self.publish()

    # ---------------- /milestone-implement ----------------

    def implement_checkpoint(self, checkpoint_id, files, subject=None,
                             stop_after="commit"):
        """`/milestone-implement`'s `[2.1 step 1]` in full: entry
        validation, selection, ownership resolution, the guarded
        `IN_PROGRESS` write, the edits, and the guarded
        completion-plus-commit followed by the durability check and the
        release. `stop_after` lets a scenario cut the sequence short at a
        named point to simulate an interrupted session."""
        entry = self.entry()
        if not ws.implementing_entry_reachable(self.root, entry, self.base_commit):
            raise AssertionError("implementing entry condition not reachable")
        selected = ws.select_next_checkpoint(entry, self.registry())
        outcome, resolved_id, owner_token = ws.resolve_checkpoint_ownership(
            self.root, entry, self.wid, selected, now=self.now(),
        )
        if outcome == ws.NO_CHECKPOINT:
            raise AssertionError("no checkpoint to implement")
        if checkpoint_id is not None and resolved_id != checkpoint_id:
            raise AssertionError(f"ownership resolved {resolved_id!r}, expected {checkpoint_id!r}")
        checkpoint_id = resolved_id
        if outcome == ws.FRESH:
            ws.write_worktree_identity(self.root, self.wid, now=self.now())
            owner_token = ws.claim_checkpoint(
                self.root, self.wid, checkpoint_id, now=self.now(),
            )["owner_token"]
        if outcome in (ws.FRESH, ws.CONTINUE_CLAIM):
            if stop_after == "claim":
                return owner_token
            start = self.sim.head()
            with ws.owner_mutation(
                self.root, self.wid, owner_token, checkpoint_id=checkpoint_id,
                step="1d", step_class=ws.DESTRUCTIVE, now=self.now(),
            ):
                ws.write_worktree_identity(self.root, self.wid, now=self.now())
                self.tx(lambda state: ws.transition_checkpoint_in_progress(
                    state, self.wid, checkpoint_id, start, self.now(),
                ))
        for rel, content in files.items():
            self.sim.write(rel, content)
        if stop_after == "edit":
            return owner_token
        registry = self.registry()
        with ws.owner_mutation(
            self.root, self.wid, owner_token, checkpoint_id=checkpoint_id,
            step="1f-commit", step_class=ws.DESTRUCTIVE, now=self.now(),
        ):
            self.tx(lambda state: ws.complete_checkpoint(
                state, self.wid, checkpoint_id, registry, self.now(), repo_root=self.root,
            ))
            commit = self.sim.commit(
                subject or f"feat({self.wid}): {checkpoint_id}",
                {"Workflow-Checkpoint": checkpoint_id, "Workflow-Work-Item": self.wid},
            )
        durable = ws.committed_checkpoint_status(self.root, self.wid, checkpoint_id)
        if durable != "COMPLETE":
            raise AssertionError(f"checkpoint completion is not durable: {durable!r}")
        if stop_after == "commit-before-release":
            return commit
        ws.release_checkpoint(
            self.root, self.wid, checkpoint_id, owner_token=owner_token, now=self.now(),
        )
        return commit

    # ---------------- /milestone-implement wrap-up ----------------

    def milestone_implement_wrap_up(self):
        """`/milestone-implement`'s `[2.1 step 1]` terminal branch followed
        by its **step 2** state write (salvage audit `B8`): entry
        validation, selection, ownership resolution -- which must resolve
        `NO_CHECKPOINT` here -- and then
        `enter_self_reviewing_implementation`, the writer that owns the
        `IMPLEMENTING` -> `SELF_REVIEWING_IMPLEMENTATION` edge.

        Every `stage="implementation"` generation below routes through
        this, so no row can reach a first-round bundle without the real
        transition having happened. Before the repair, the matrix's `C5`
        row hand-wrote the phase into `WORKFLOW_STATE.json` instead
        (salvage audit `O6`), which is exactly what hid `B8`."""
        entry = self.entry()
        if not ws.implementing_entry_reachable(self.root, entry, self.base_commit):
            raise AssertionError("implementing entry condition not reachable")
        selected = ws.select_next_checkpoint(entry, self.registry())
        outcome, resolved_id, _ = ws.resolve_checkpoint_ownership(
            self.root, entry, self.wid, selected, now=self.now(),
        )
        if outcome != ws.NO_CHECKPOINT:
            raise AssertionError(
                f"wrap-up reached with work left: ownership resolved {outcome!r} "
                f"for {resolved_id!r}"
            )
        registry = self.registry()
        phase_before = self.entry()["phase"]
        revision_before = self.entry()["state_revision"]
        self.tx(lambda state: ws.enter_self_reviewing_implementation(
            state, self.wid, registry, self.now(),
        ))
        if self.entry()["state_revision"] != revision_before:
            # Step 2's durability commit: only when the call actually
            # transitioned. `same_content` republication validates the
            # generation-record commit against its *parent's committed*
            # phase, so a source phase left in the working tree alone
            # refuses one step later (salvage audit `B8`).
            self.sim.commit(
                f"chore({self.wid}): enter SELF_REVIEWING_IMPLEMENTATION",
                {"Workflow-Work-Item": self.wid},
                paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
            )
        return phase_before, revision_before

    # ---------------- bundle generation ----------------

    def generate_impl_bundle(self, stage="implementation", check=True, expect_outcome=None,
                             summary=None, review_request=None, skip_durability=False,
                             wrap_up=True, refresh_inputs=True):
        """`refresh_inputs=False` leaves whatever the previous round wrote
        under `<bundle_dir>` exactly as it is -- the shape a command that
        never names its author-written preconditions actually produces
        (convergence pass 11, ledger `I19`, row `C9`)."""
        if stage == "implementation" and wrap_up:
            self.milestone_implement_wrap_up()
        entry = self.entry()
        head_before = self.sim.head()
        outcome, superseded = ws.resolve_bundle_generation_outcome(
            self.root, entry, base_commit=self.base_commit, head=head_before,
        )
        if expect_outcome is not None and outcome != expect_outcome:
            raise AssertionError(f"expected outcome {expect_outcome!r}, resolved {outcome!r}")
        self.tx(lambda state: ws.record_bundle_generation(
            state, self.wid, stage=stage, head=head_before, now=self.now(), outcome=outcome,
        ))
        revision = self.entry()["implementation_revision"]
        durability = head_before
        if not skip_durability:
            trailers = {
                "Workflow-Bundle-Generation-Record": f"{self.wid}/{revision}",
                "Workflow-Work-Item": self.wid,
            }
            if outcome == "same_content":
                trailers["Workflow-Supersedes"] = superseded
            durability = self.sim.commit(
                f"chore({self.wid}): record bundle generation, revision {revision} ({outcome})",
                trailers, paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
            )
        if refresh_inputs:
            self.write_bundle_inputs(stage, durability, revision,
                                     summary=summary, review_request=review_request)
        proc = self.sim.prepare(self.base_commit, stage, self.wid, check=check)
        return outcome, durability, proc

    def write_bundle_inputs(self, stage, commit, revision, summary=None, review_request=None):
        """The author-written half of one generation, performed exactly
        where the owning command's own step performs it: the recomputed
        implementation-stage `review_content_id` into `REVIEW_REQUEST.md`
        and the current counter into `IMPLEMENTATION_SUMMARY.md`.

        Both refreshes are named by every command this helper stands in
        for -- `/milestone-implement` step 4 (via
        `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Author-written files"),
        `/apply-implementation-review` step 7, and, since convergence pass
        11, `/apply-functional-review`'s bounded branch. That last one is
        why the note matters: before pass 11 this helper refreshed both
        lines for a bounded functional fix whose operator documentation
        named neither, so rows `C2`/`C3`/`C8` stayed green over a contract
        gap no operator following the command could have satisfied (ledger
        `I19`). Row `C9` is the row that would now catch it."""
        bundle = self.bundle_dir()
        bundle.mkdir(parents=True, exist_ok=True)
        digest, _ = self.impl_review_content_id(commit)
        (bundle / "REVIEW_REQUEST.md").write_text(review_request if review_request is not None else (
            f"# Review request\n\nstage: {stage}\nwork item: {self.wid}\n"
            f"review_content_id: {digest}\n"
        ))
        (bundle / "IMPLEMENTATION_SUMMARY.md").write_text(summary if summary is not None else (
            f"implementation_revision: {revision}\n\nWhat was built, per checkpoint.\n"
        ))
        (bundle / "TEST_RESULTS.md").write_text("ran: the narrow checks for this round\n")
        (bundle / "CONTEXT_FILES.txt").write_text("")

    # ---------------- /apply-implementation-review ----------------

    def enter_applying_feedback(self):
        self.tx(lambda state: ws.enter_applying_review_feedback(state, self.wid, self.now()))
        return self.sim.commit(
            f"chore({self.wid}): enter APPLYING_REVIEW_FEEDBACK",
            {"Workflow-Work-Item": self.wid},
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )

    # ---------------- /recover-implementation-provenance ----------------

    def recover_provenance(self, check=True):
        entry = self.entry()
        head = self.sim.head()
        superseded = ws.verify_implementation_provenance_recovery(
            self.root, entry, base_commit=self.base_commit, head=head,
        )
        ws.validate_implementation_provenance_recovery_confirmation(
            f"recover {self.wid} superseding {superseded}",
            work_item_id=self.wid, superseded_commit=superseded,
        )
        self.tx(lambda state: ws.apply_implementation_provenance_recovery(
            state, self.wid, self.now(),
        ))
        revision = self.entry()["implementation_revision"]
        s2 = self.sim.commit(
            f"chore({self.wid}): recover implementation provenance",
            {
                "Workflow-Bundle-Generation-Record": f"{self.wid}/{revision}",
                "Workflow-Work-Item": self.wid,
                "Workflow-Supersedes": superseded,
            },
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )
        stage = None
        for line in self.manifest().splitlines():
            if line.startswith("stage: "):
                stage = line.split(": ", 1)[1].strip()
        self.write_bundle_inputs(stage, s2, revision)
        proc = self.sim.prepare(self.base_commit, stage, self.wid, check=check)
        return superseded, s2, proc

    # ---------------- /approve-review implementation ----------------

    def approve_implementation(self, user_confirmation=None, expect_basis="EXTERNAL_APPROVE"):
        """`expect_basis` (convergence pass 12, ledger `O35`) makes the
        `USER_OVERRIDE` basis reachable from this helper at all.

        `/approve-review` step 2 says a missing or mismatched binding
        field is "not fatal to reading the file (an `EXTERNAL_APPROVE`
        basis simply becomes unreachable, per step 3), but report the
        mismatch naming both values" -- the decision belongs to
        `resolve_approval_basis`, not to a refusal one step earlier. This
        helper called `assert_feedback_matches_bundle` unconditionally
        instead, a step no owning command names, so every row that reached
        this method necessarily had matching feedback and the whole
        `USER_OVERRIDE` half of D2's basis decision was unexecuted by this
        suite -- including the interim remedy
        `/recover-implementation-provenance` step 7 names by that exact
        word. Now the binding check is *observed* and reported, exactly as
        the command says, and `expect_basis` pins which branch a row
        intends."""
        confirmation = user_confirmation or f"implementation {self.wid}"
        entry = self.entry()
        classification = self.impl_classification()
        review_content_id, projection = self.impl_review_content_id(self.sim.head())
        bundle_id = self.bundle_id()
        fingerprint.assert_local_generation_matches(self.root, self.bundle_dir() / "MANIFEST.md")
        fingerprint.assert_bundle_not_rejected(self.root, self.wid)
        feedback = self.feedback_fields()
        try:
            fingerprint.assert_feedback_matches_bundle(
                feedback, bundle_id=bundle_id, base_commit=self.base_commit,
                work_item_id=self.wid,
            )
            self.last_feedback_binding_mismatch = None
        except fingerprint.FeedbackBundleMismatchError as exc:
            # Reported, not fatal -- step 2's own words.
            self.last_feedback_binding_mismatch = str(exc)
        pinned = ws.is_technical_review_block_pinned(entry, bundle_id)
        dirty = ws.any_protected_path_dirty(self.root, *classification)
        interval_ok = ws.implementation_provenance_interval_reachable(
            self.root, entry, self.base_commit,
        )
        if not interval_ok:
            ws.verify_implementation_provenance_interval(self.root, entry, self.base_commit)
        if not ws.technical_approval_gate_reachable(
            latest_round_status=feedback["status"], protected_path_dirty=dirty,
            head_matches_reviewed_implementation_head=interval_ok, pinned_block=pinned,
            # workflow-2.5.0 REVISE round 7's own I1: these three are now
            # required keyword-only parameters (no longer defaulted to
            # None) -- passing the entry's real governing_workflow_version/
            # implementation_review_stages and the freshly recomputed
            # current_review_content_id here is behavior-preserving for
            # every "1"/"2.1" row this suite drives (the function's own
            # `!= "2.2"` branch), and is what a real "2.2" caller needs too.
            governing_workflow_version=entry.get("governing_workflow_version"),
            implementation_review_stages=entry.get("implementation_review_stages"),
            current_review_content_id=review_content_id,
        ):
            raise AssertionError(
                f"technical-approval gate not reachable: status={feedback['status']} "
                f"dirty={dirty} interval={interval_ok} pinned={pinned}"
            )
        basis = ws.resolve_approval_basis(
            latest_round_status=feedback["status"],
            feedback_bundle_id=feedback["reviewed_bundle_id"],
            current_bundle_id=bundle_id, user_confirmation=confirmation,
            work_item_id=self.wid, stage="implementation", pinned_block=pinned,
        )
        if expect_basis is not None and basis != expect_basis:
            raise AssertionError(f"expected basis {expect_basis!r}, resolved {basis!r}")
        record = ws.build_approval_record(
            basis=basis, stage="implementation", user_confirmation=confirmation, now=self.now(),
            reviewed_bundle_id=bundle_id, approved_review_content_id=review_content_id,
            review_content_manifest=projection["review_content_manifest"],
            reviewed_content_commit=entry["reviewed_implementation_head"],
        )
        self.tx(lambda state: ws.apply_technical_approval(state, self.wid, record, self.now()))
        commit = self.sim.commit(
            f"chore({self.wid}): record technical approval",
            {"Workflow-Technical-Approval": review_content_id, "Workflow-Work-Item": self.wid},
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )
        ws.validate_technical_approval_commit(self.root, commit, self.wid)
        ws.verify_post_approval_manifest_match(
            self.root, self.entry(), stage="implementation",
            base_commit=self.base_commit, commit=commit,
        )
        return commit

    # ---------------- functional review ----------------

    def prepare_functional_review(self, checklist=None):
        revision = self.entry()["implementation_revision"]
        path = ws.FUNCTIONAL_CHECKLIST_PATH
        self.sim.write(path, checklist if checklist is not None else (
            f"# Active milestone\n\n## Functional review checklist "
            f"(implementation revision {revision})\n\n- flow 1: exercise the thing\n"
        ))
        blob = self.sim.git("hash-object", path).stdout.strip()
        current = ws.discover_current_functional_checklist_evidence(
            self.root, self.wid, self.base_commit, self.sim.head(), revision,
        )
        if current is not None and current["blob"] == blob:
            return current
        self.sim.git("add", "--", path)
        staged = self.sim.git("diff", "--name-only", "--cached", "HEAD").stdout.split()
        body = (
            f"docs({self.wid}): functional-review checklist "
            f"(implementation revision {revision})\n\n"
            f"Workflow-Functional-Checklist: {self.wid}/{revision}/{blob}\n"
            f"Workflow-Work-Item: {self.wid}\n"
        )
        args = ["commit", "-q", "-m", body]
        if not staged:
            # `/prepare-functional-review` step 3a's unchanged-checklist
            # branch (salvage audit `I7`): the commit's payload is the
            # round-scoped trailer, which is new information even at an
            # identical blob.
            args.insert(1, "--allow-empty")
        self.sim.git(*args)
        return {"commit_sha": self.sim.head(), "blob": blob}

    def write_functional_findings(self, text):
        (self.feedback_dir() / "FUNCTIONAL_REVIEW.md").write_text(text)

    def mark_technical_approval_stale(self):
        self.tx(lambda state: ws.mark_technical_approval_stale(state, self.wid, self.now()))
        return self.sim.commit(
            f"chore({self.wid}): stale technical approval before a bounded functional fix",
            {"Workflow-Work-Item": self.wid},
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )

    # ---------------- /accept-milestone ----------------

    def accept_milestone(self, user_confirmation=None):
        confirmation = user_confirmation or f"acceptance {self.wid}"
        ws.validate_user_confirmation(confirmation, work_item_id=self.wid, stage="acceptance")
        entry = self.entry()
        is_terminal, outstanding = ws.resolve_own_registry_completion_status(self.root, entry)
        if not ws.milestone_complete_gate_reachable(
            phase=entry["phase"], is_terminal=is_terminal,
        ):
            raise AssertionError(
                f"milestone-complete gate not reachable: phase={entry['phase']} "
                f"terminal={is_terminal} outstanding={outstanding}"
            )
        self.tx(lambda state: ws.complete_work_item(
            state, self.wid, self.now(), repo_root=self.root,
        ))
        return self.sim.commit(f"docs({self.wid}): accept milestone",
                               {"Workflow-Work-Item": self.wid})


class MatrixCase(unittest.TestCase):
    """Base class: one disposable repository per test, torn down after."""

    work_item_type = "process"

    def setUp(self):
        self.scratch = Scratch()
        self.addCleanup(self.scratch.cleanup)
        self.item = Item(self.scratch, self.work_item_type)
        self.item.seed()

    # --- reusable stage helpers, each stopping at a named lifecycle point ---

    def reach_plan_approved(self):
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        commit = item.approve_plan()
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")
        return commit

    def reach_first_bundle(self):
        self.reach_plan_approved()
        item = self.item
        item.implement_checkpoint("CP1", {item.deliverable: "// round 1\n"})
        self.assertEqual(item.entry()["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        outcome, durability, _ = item.generate_impl_bundle(
            "implementation", expect_outcome="ordinary",
        )
        self.assertEqual(item.entry()["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(item.entry()["implementation_revision"], 1)
        return durability

    def reach_technical_approved(self):
        self.reach_first_bundle()
        self.item.write_feedback("APPROVE")
        commit = self.item.approve_implementation()
        self.assertEqual(self.item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        return commit


# ===========================================================================
# A. Plan lifecycle
# ===========================================================================


class PlanLifecycleProcess(MatrixCase):
    """Matrix rows A1-A4 for a `process` work item."""

    work_item_type = "process"

    def test_a1_fresh_plan_to_approval(self):
        item = self.item
        item.milestone_plan()
        # workflow-2.6.0 (D-Plan-Review-Bundle-Binding): the publish is
        # mirror-only; the item is review-ready only once its bundle binds.
        self.assertEqual(item.entry()["phase"], "PLANNING")
        self.assertEqual(item.entry()["plan_review_binding"]["status"], "PUBLISHED")
        item.generate_plan_bundle()
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(item.entry()["plan_review_binding"]["status"], "BOUND")
        self.assertEqual(item.entry()["current_bundle_id"], item.bundle_id())
        self.assertTrue((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())
        self.assertTrue((item.root / ".ai-review" / item.wid / "review-bundle.tar.gz").is_file())
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")
        commit = item.approve_plan()
        entry = item.entry()
        self.assertEqual(entry["phase"], "IMPLEMENTING")
        self.assertEqual(entry["plan_approval"]["status"], "CURRENT")
        self.assertEqual(entry["plan_approval"]["basis"], "EXTERNAL_APPROVE")
        self.assertIsNone(entry["plan_approval"]["reviewed_content_commit"])
        self.assertIsNotNone(
            ws.discover_plan_approval_commit(
                item.root, item.wid, entry["plan_approval"]["approved_review_content_id"],
                item.base_commit, commit,
            )
        )
        self.assertTrue(ws.implementing_entry_reachable(item.root, entry, item.base_commit))
        # The approval commit is the five-member set `D-Approval-Commits`
        # describes: the item's own three plan-stage files, its artifacts
        # declaration as the conditional fifth member, and the state file.
        # This is what the live `workflow-v2-3-1` approval commit 718f619
        # looks like, and it only holds because the four files were left
        # uncommitted (intent-to-add) until now.
        committed = self.scratch.git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", commit,
        ).stdout.split()
        self.assertEqual(sorted(committed), sorted([
            item.plan_path, item.registry_path, item.mapping_path,
            item.artifacts_path, "docs/ai-workflow/WORKFLOW_STATE.json",
        ]))

    def test_a5_unstaged_plan_files_refuse_before_writing_anything(self):
        """Ledger `B6`: `resolve_plan_stage_metadata` requires each
        declared path to be in the Git index, so `/milestone-plan`'s
        staging step is load-bearing, not housekeeping. Without it the
        generator refuses before writing any bundle content."""
        item = self.item
        item.milestone_plan()
        self.scratch.git("reset", "--", item.plan_path, item.registry_path,
                         item.mapping_path, item.artifacts_path)
        # Every plan-stage read goes through the same resolver, so the
        # refusal is identical whether the command computes the digest it
        # must state in REVIEW_REQUEST.md or the generator runs its own
        # plan-stage preflight.
        with self.assertRaises(fingerprint.InvalidPlanStageMetadataPathError):
            fingerprint.compute_review_content_id_plan_stage_for_work_item(
                item.root, item.wid,
            )
        proc = self.scratch.prepare(item.base_commit, "plan", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("is not a tracked path", proc.stderr)
        self.assertFalse((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())
        # Re-applying the staging step is the whole remedy.
        item.stage_plan_files()
        item.generate_plan_bundle()
        self.assertTrue((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())

    def test_a2_plan_revise_local(self):
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        first_bundle_id = item.bundle_id()
        item.record_plan_reviews(local="REVISE")
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")
        item.apply_plan_review(2, CHECKPOINTS_TWO, REQUIREMENTS_TWO)
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")
        self.assertEqual(item.entry()["plan_revision"], 2)
        item.generate_plan_bundle()
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertNotEqual(item.bundle_id(), first_bundle_id)
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=2)
        item.approve_plan()
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")
        self.assertEqual(item.entry()["plan_revision"], 2)

    def test_a3_plan_revise_multiple_rounds(self):
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        item.record_plan_reviews(local="REVISE")
        item.apply_plan_review(2)
        item.generate_plan_bundle()
        # A manual-external REVISE must also land back at the *local* stage:
        # no path re-enters manual review without a fresh local pass.
        item.record_plan_reviews(local="APPROVE", manual="REVISE", round=2)
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")
        item.apply_plan_review(3)
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")
        item.generate_plan_bundle()
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=3)
        item.approve_plan()
        self.assertEqual(item.entry()["plan_revision"], 3)
        stages = item.entry()["plan_review_stages"]
        self.assertEqual(stages[ws.LOCAL_MODEL_PLAN_REVIEW]["round"], 3)
        self.assertEqual(stages[ws.MANUAL_EXTERNAL_PLAN_REVIEW]["round"], 3)

    def test_a4_plan_stage_stale_test_results_withdraws(self):
        """Ledger `I3`: the marker lines are a hard generator precondition,
        and a miss never publishes. workflow-2.6.0 (the revised `WFR-67`,
        `D-Plan-Review-Bundle-Binding` item 5): the plan stage generates
        into staging, so the miss discards the staging area instead of
        withdrawing -- nothing becomes review-ready, no `REJECTED` marker
        is written, and the item stays unbound at `PLANNING` (row 9)."""
        item = self.item
        item.milestone_plan()
        proc = item.generate_plan_bundle(check=False, test_results="")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("staging discarded", proc.stdout + proc.stderr)
        self.assertIn("TEST_RESULTS.md", proc.stdout + proc.stderr)
        self.assertFalse(item.bundle_dir(stage="plan").is_dir())
        self.assertFalse((item.root / ".ai-review" / item.wid / "review-bundle.tar.gz").is_file())
        item_root = item.root / ".ai-review" / item.wid
        self.assertEqual(list(item_root.glob("current.rejected-*")), [])
        self.assertEqual(list(item_root.glob("current.staging-*")), [])
        self.assertEqual(list(item_root.glob(".pin.staging-*")), [])
        self.assertFalse((item_root / "REJECTED").exists())
        self.assertEqual(item.entry()["phase"], "PLANNING")
        self.assertEqual(
            ws.plan_review_publication_status(item.root, item.state(), item.wid)["status"],
            ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND,
        )
        # And a clean regeneration afterwards publishes and binds normally.
        item.generate_plan_bundle()
        self.assertTrue((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")


class PlanLifecycleProduct(PlanLifecycleProcess):
    """Matrix rows A1-A3 for a `product` work item -- the type that
    `resolve_plan_stage_metadata`'s own gate rejected outright until
    `workflow-v2-3-1` CP1, and that no end-to-end fixture had ever
    exercised."""

    work_item_type = "product"

    def test_a4_plan_stage_stale_test_results_withdraws(self):
        raise unittest.SkipTest("row A4 is type-independent; run once, on the process item")

    def test_a5_unstaged_plan_files_refuse_before_writing_anything(self):
        raise unittest.SkipTest("row A5 is type-independent; run once, on the process item")


# ===========================================================================
# B. Implementation lifecycle, all three generation modes
# ===========================================================================


class ImplementationLifecycleProcess(MatrixCase):
    """Matrix rows B1-B10 for a `process` work item."""

    work_item_type = "process"

    def test_b1_implementation_first_round_approve(self):
        item = self.item
        durability = self.reach_first_bundle()
        entry = item.entry()
        # The manifest's two head fields mean two different commits
        # (ledger `I1`): the state's reviewed round head, and the commit
        # the digest was measured at.
        self.assertIn(
            f"reviewed_implementation_head: {entry['reviewed_implementation_head']}",
            item.manifest(),
        )
        self.assertIn(f"generation_head: {durability}", item.manifest())
        self.assertNotEqual(entry["reviewed_implementation_head"], durability)
        self.assertIn("implementation_revision: 1", item.manifest())
        self.assertTrue(ws.implementation_provenance_interval_reachable(
            item.root, entry, item.base_commit,
        ))
        item.write_feedback("APPROVE")
        commit = item.approve_implementation()
        approved = item.entry()["technical_approval"]
        self.assertEqual(approved["status"], "CURRENT")
        self.assertEqual(approved["basis"], "EXTERNAL_APPROVE")
        self.assertEqual(approved["reviewed_content_commit"], entry["reviewed_implementation_head"])
        self.assertIsNotNone(ws.discover_technical_approval_commit(
            item.root, item.wid, approved["approved_review_content_id"],
            item.base_commit, commit,
        ))

    def test_b2_implementation_revise_rounds(self):
        item = self.item
        self.reach_first_bundle()
        for round_number, body in ((2, "// round 2\n"), (3, "// round 3\n")):
            item.write_feedback("REVISE")
            item.enter_applying_feedback()
            self.assertEqual(item.entry()["phase"], "APPLYING_REVIEW_FEEDBACK")
            self.scratch.write(item.deliverable, body)
            self.scratch.commit(f"fix({item.wid}): round {round_number - 1} finding",
                                {"Workflow-Work-Item": item.wid})
            item.generate_impl_bundle("post-fix", expect_outcome="ordinary")
            self.assertEqual(item.entry()["implementation_revision"], round_number)
            self.assertIn(f"implementation_revision: {round_number}", item.manifest())
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")

    def test_b3_same_content_round(self):
        """Ledger `B1`: a REVISE round resolved entirely in excluded-only
        content republishes as `same_content` -- the round stays pinned and
        the bundle must still publish."""
        item = self.item
        self.reach_first_bundle()
        pinned_head = item.entry()["reviewed_implementation_head"]
        first_bundle_id = item.bundle_id()

        item.write_feedback("REVISE")
        item.enter_applying_feedback()
        self.scratch.write(item.excluded_note, "# ledger\n\nround-1 findings recorded.\n")
        self.scratch.commit(f"docs({item.wid}): record round-1 dispositions (excluded-only)",
                            {"Workflow-Work-Item": item.wid})

        outcome, s2, proc = item.generate_impl_bundle("post-fix", expect_outcome="same_content")
        self.assertEqual(outcome, "same_content")
        self.assertEqual(proc.returncode, 0)
        entry = item.entry()
        self.assertEqual(entry["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(entry["implementation_revision"], 1)
        self.assertEqual(entry["reviewed_implementation_head"], pinned_head)
        self.assertIn(f"reviewed_implementation_head: {pinned_head}", item.manifest())
        self.assertIn(f"generation_head: {s2}", item.manifest())
        self.assertIn("implementation_revision: 1", item.manifest())
        self.assertNotEqual(item.bundle_id(), first_bundle_id)
        ws.validate_bundle_generation_record_commit(item.root, s2, item.wid)
        self.assertEqual(
            ws.verify_implementation_provenance_interval(item.root, entry, item.base_commit), s2,
        )
        # Regenerating again at the same head, same stage, is idempotent:
        # the republication does not become a reason to advance anything.
        republished_bundle_id = item.bundle_id()
        again = self.scratch.prepare(item.base_commit, "post-fix", item.wid)
        self.assertEqual(again.returncode, 0)
        self.assertEqual(item.bundle_id(), republished_bundle_id)
        self.assertEqual(item.entry()["implementation_revision"], 1)
        # And the round is approvable from here.
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")

    def test_b4_same_content_chain(self):
        """Two consecutive same-content republications: the supersession
        chain must stay continuous across more than one link."""
        item = self.item
        self.reach_first_bundle()
        pinned_head = item.entry()["reviewed_implementation_head"]
        tips = []
        for round_number in (1, 2):
            item.write_feedback("REVISE")
            item.enter_applying_feedback()
            self.scratch.write(item.excluded_note, f"# ledger\n\nround {round_number}\n")
            self.scratch.commit(f"docs({item.wid}): excluded-only note {round_number}",
                                {"Workflow-Work-Item": item.wid})
            outcome, tip, proc = item.generate_impl_bundle(
                "post-fix", expect_outcome="same_content",
            )
            self.assertEqual(proc.returncode, 0)
            tips.append(tip)
        entry = item.entry()
        self.assertEqual(entry["implementation_revision"], 1)
        self.assertEqual(entry["reviewed_implementation_head"], pinned_head)
        self.assertEqual(
            ws.verify_implementation_provenance_interval(item.root, entry, item.base_commit),
            tips[-1],
        )
        self.assertEqual(
            ws.discover_current_bundle_generation_record_commit(
                item.root, item.wid, item.base_commit, tips[-1], 1,
            ),
            tips[-1],
        )

    def test_b5_provenance_recovery(self):
        """Ledger `B2`: `/recover-implementation-provenance` end to end,
        including its step 6 regeneration."""
        item = self.item
        durability = self.reach_first_bundle()
        pinned_head = item.entry()["reviewed_implementation_head"]
        self.scratch.write(item.excluded_note, "# ledger\n\nconcurrent excluded-only note\n")
        self.scratch.commit(f"docs({item.wid}): concurrent excluded-only commit",
                            {"Workflow-Work-Item": item.wid})
        # The stale generation_head is exactly what this command repairs.
        with self.assertRaises(fingerprint.WorktreeOrHeadMismatchError):
            fingerprint.assert_local_generation_matches(
                item.root, item.bundle_dir() / "MANIFEST.md",
            )
        superseded, s2, proc = item.recover_provenance()
        self.assertEqual(superseded, durability)
        self.assertEqual(proc.returncode, 0)
        entry = item.entry()
        self.assertEqual(entry["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(entry["implementation_revision"], 1)
        self.assertEqual(entry["reviewed_implementation_head"], pinned_head)
        self.assertIn(f"generation_head: {s2}", item.manifest())
        fingerprint.assert_local_generation_matches(item.root, item.bundle_dir() / "MANIFEST.md")
        self.assertEqual(
            ws.verify_implementation_provenance_interval(item.root, entry, item.base_commit), s2,
        )
        # Recovery is a no-op once HEAD is already the record commit.
        with self.assertRaises(ws.ImplementationProvenanceRecoveryNotApplicableError):
            ws.verify_implementation_provenance_recovery(
                item.root, item.entry(), base_commit=item.base_commit, head=item.sim.head(),
            )

    def test_b6_implementation_stale_summary_withdraws(self):
        """Ledger `I4`: `IMPLEMENTATION_SUMMARY.md`'s revision line is a
        hard generator precondition."""
        item = self.item
        self.reach_plan_approved()
        item.implement_checkpoint("CP1", {item.deliverable: "// round 1\n"})
        _, _, proc = item.generate_impl_bundle(
            "implementation", check=False,
            summary="# What was built\n\nNo revision line at all.\n",
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("withdrawn", combined)
        self.assertIn("IMPLEMENTATION_SUMMARY.md", combined)
        self.assertFalse(item.bundle_dir().is_dir())

        # The re-audit's own finding: regenerating after that withdrawal
        # must succeed on its **first** attempt too. `withdraw_bundle`
        # renamed `current/` to a quarantine sibling, so a `current/`-gated
        # resolution flipped this item -- which had just been generating
        # scoped bundles -- back onto the flat `.ai-review/current/` for
        # its next round, while `prepare-ai-review.sh`, given the same
        # `[work-item-id]` argument, still wrote the scoped directory. The
        # author-written `IMPLEMENTATION_SUMMARY.md` landed at the flat
        # path and `assert_stage_completeness` read the generator's own
        # empty stub instead, withdrawing the regenerated bundle as well.
        self.assertEqual(
            fingerprint.resolve_bundle_dir(item.root, item.wid),
            Path(".ai-review") / item.wid / "current",
        )
        revision = item.entry()["implementation_revision"]
        item.write_bundle_inputs("implementation", self.scratch.head(), revision)
        proc = self.scratch.prepare(item.base_commit, "implementation", item.wid, check=False)
        self.assertEqual(
            proc.returncode, 0,
            f"first attempt after withdrawal failed:\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}",
        )
        self.assertTrue((item.bundle_dir() / "MANIFEST.md").is_file())
        self.assertFalse((item.root / ".ai-review" / "current").exists())

    def test_b7_idempotent_regeneration(self):
        """Re-running the generator for the same head, same stage and same
        round must reproduce the bundle byte for byte -- the property the
        preflight's own idempotence branch exists to protect."""
        item = self.item
        self.reach_first_bundle()
        before = item.bundle_id()
        proc = self.scratch.prepare(item.base_commit, "implementation", item.wid)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(item.bundle_id(), before)
        self.assertEqual(item.entry()["implementation_revision"], 1)

    def test_b8_revision_monotonicity_refusals(self):
        """The repaired preflight still refuses a two-step jump, a
        backwards move, and a same-head bump."""
        item = self.item
        self.reach_first_bundle()
        state_path = item.root / "docs/ai-workflow/WORKFLOW_STATE.json"

        def set_revision(value):
            state = json.loads(state_path.read_text())
            state["work_items"][item.wid]["implementation_revision"] = value
            state_path.write_text(json.dumps(state, indent=2) + "\n")

        # same head, revision bumped anyway
        set_revision(2)
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("GPT-R43-001", proc.stderr)
        set_revision(1)

        # new head, revision jumped by two
        self.scratch.write(item.deliverable, "// changed\n")
        self.scratch.commit(f"feat({item.wid}): more work", {"Workflow-Work-Item": item.wid})
        set_revision(3)
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("GPT-R43-001", proc.stderr)

        # new head, revision moved backwards
        set_revision(0)
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("GPT-R43-001", proc.stderr)

    def test_b9_unrecorded_new_head_refused(self):
        """The provenance-interval half is untouched by the `R1` repair:
        a new head with no generation-record commit of its own is still
        refused, even though its revision is left unchanged."""
        item = self.item
        self.reach_first_bundle()
        self.scratch.write(item.excluded_note, "# ledger\n")
        self.scratch.commit(f"docs({item.wid}): excluded-only, no record commit",
                            {"Workflow-Work-Item": item.wid})
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("GPT-R42-001", proc.stderr)
        self.assertIn("no valid provenance interval reaches it either", proc.stderr)
        self.assertIn("a further commit landed after it", proc.stderr)
        self.assertTrue((item.bundle_dir() / "MANIFEST.md").is_file())

    def test_b10_protected_change_without_revision_refused(self):
        """A genuinely changed protected path can never masquerade as a
        same-content republication: the interval classification refuses."""
        item = self.item
        self.reach_first_bundle()
        item.write_feedback("REVISE")
        item.enter_applying_feedback()
        self.scratch.write(item.deliverable, "// genuinely changed\n")
        self.scratch.commit(f"fix({item.wid}): a real change", {"Workflow-Work-Item": item.wid})
        outcome, _ = ws.resolve_bundle_generation_outcome(
            item.root, item.entry(), base_commit=item.base_commit, head=item.sim.head(),
        )
        self.assertEqual(outcome, "ordinary")
        # Forge the same-content shape anyway: pin the revision and write a
        # recovered-role commit for it.
        state_path = item.root / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"][item.wid]["phase"] = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        record = ws.discover_current_bundle_generation_record_commit(
            item.root, item.wid, item.base_commit, item.sim.head(), 1,
        )
        self.scratch.commit(
            f"chore({item.wid}): forged same-content record",
            {
                "Workflow-Bundle-Generation-Record": f"{item.wid}/1",
                "Workflow-Work-Item": item.wid,
                "Workflow-Supersedes": record,
            },
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("provenance interval", proc.stderr)


    def test_b11_same_content_chain_then_an_ordinary_round(self):
        """After two same-content links the round is still revision 1;
        the next genuine change must advance it to 2 and start a fresh
        chain whose earliest member is ordinary-role again."""
        item = self.item
        self.reach_first_bundle()
        for note in ("first", "second"):
            item.write_feedback("REVISE")
            item.enter_applying_feedback()
            self.scratch.write(item.excluded_note, f"# ledger\n\n{note}\n")
            self.scratch.commit(f"docs({item.wid}): excluded-only {note}",
                                {"Workflow-Work-Item": item.wid})
            item.generate_impl_bundle("post-fix", expect_outcome="same_content")
        self.assertEqual(item.entry()["implementation_revision"], 1)

        item.write_feedback("REVISE")
        item.enter_applying_feedback()
        self.scratch.write(item.deliverable, "// a genuine change at last\n")
        self.scratch.commit(f"fix({item.wid}): a genuine change",
                            {"Workflow-Work-Item": item.wid})
        outcome, record, proc = item.generate_impl_bundle(
            "post-fix", expect_outcome="ordinary",
        )
        self.assertEqual(proc.returncode, 0)
        entry = item.entry()
        self.assertEqual(entry["implementation_revision"], 2)
        self.assertEqual(
            ws.verify_implementation_provenance_interval(item.root, entry, item.base_commit),
            record,
        )
        self.assertIn("implementation_revision: 2", item.manifest())
        item.write_feedback("APPROVE")
        item.approve_implementation()

    def test_b12_same_content_round_then_provenance_recovery(self):
        """The two recovered-role producers in one chain: a same-content
        republication followed by a `/recover-implementation-provenance`
        `S2`. Both are recovered-role commits for the same revision, so
        the chain must stay continuous across the mix."""
        item = self.item
        self.reach_first_bundle()
        pinned_head = item.entry()["reviewed_implementation_head"]

        item.write_feedback("REVISE")
        item.enter_applying_feedback()
        self.scratch.write(item.excluded_note, "# ledger\n\nround-1 note\n")
        self.scratch.commit(f"docs({item.wid}): excluded-only note",
                            {"Workflow-Work-Item": item.wid})
        _, republished, _ = item.generate_impl_bundle(
            "post-fix", expect_outcome="same_content",
        )

        # Now a concurrent excluded-only commit lands past the new tip.
        self.scratch.write(item.excluded_note, "# ledger\n\nconcurrent note\n")
        self.scratch.commit(f"docs({item.wid}): concurrent excluded-only commit",
                            {"Workflow-Work-Item": item.wid})
        superseded, s2, proc = item.recover_provenance()
        self.assertEqual(superseded, republished)
        self.assertEqual(proc.returncode, 0)
        entry = item.entry()
        self.assertEqual(entry["implementation_revision"], 1)
        self.assertEqual(entry["reviewed_implementation_head"], pinned_head)
        self.assertEqual(
            ws.verify_implementation_provenance_interval(item.root, entry, item.base_commit), s2,
        )
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(
            item.entry()["technical_approval"]["reviewed_content_commit"], pinned_head,
        )

    def test_b14_plan_re_approved_with_every_checkpoint_already_complete(self):
        """Salvage audit `B8`, the terminal `IMPLEMENTING` wedge, driven
        end to end through the sanctioned lifecycle.

        A published, technically-approved round 1, then a plan revision
        that adds no checkpoint (a plan-document correction), re-reviewed
        and re-approved. `apply_plan_approval` writes `IMPLEMENTING`
        unconditionally and correctly; every registry checkpoint is
        already `COMPLETE`, so `select_next_checkpoint` returns `None` and
        `resolve_checkpoint_ownership` returns `NO_CHECKPOINT`.

        Before the repair, that state was terminal: no writer moved the
        item to `SELF_REVIEWING_IMPLEMENTATION`, so bundle generation
        refused at both stages, `enter_applying_review_feedback` refused,
        and `/accept-milestone`'s gate was unreachable -- the work item
        could not be advanced or completed by any command."""
        item = self.item
        # workflow-2.6.0 (`D-Plan-Review-Bundle-Binding`, `LPR-R4-002`): the
        # 2.5.1 route to this state -- re-publishing a revised plan from
        # `AWAITING_FUNCTIONAL_REVIEW` after a technical approval -- is now
        # refused at the plan-stage allow-list, and no plan re-entry exists
        # from that phase. The wedge is still reachable through the one
        # sanctioned route: an amendment requested from
        # `SELF_REVIEWING_IMPLEMENTATION` once every checkpoint is complete.
        self.reach_plan_approved()
        item.implement_checkpoint("CP1", {item.deliverable: "// round 1\n"})
        self.assertEqual(item.entry()["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        item.request_amendment("plan-document correction")
        item.amend_plan(2, plan_body="Plan body, corrected after round 1.\n")
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=2)
        item.approve_plan()

        entry = item.entry()
        self.assertEqual(entry["phase"], "IMPLEMENTING")
        self.assertEqual(entry["checkpoints"]["CP1"]["status"], "COMPLETE")
        self.assertTrue(ws.implementing_entry_reachable(item.root, entry, item.base_commit))
        self.assertIsNone(ws.select_next_checkpoint(entry, item.registry()))
        outcome, resolved, token = ws.resolve_checkpoint_ownership(
            item.root, entry, item.wid, None, now=item.now(),
        )
        self.assertEqual((outcome, resolved, token), (ws.NO_CHECKPOINT, None, None))

        # Every outgoing lifecycle path is refused from here -- the wedge.
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError):
            item.generate_impl_bundle("implementation", wrap_up=False)
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError):
            item.generate_impl_bundle("post-fix", wrap_up=False)
        with self.assertRaises(ws.IllegalApplyingReviewFeedbackEntryPhaseError):
            ws.enter_applying_review_feedback(item.state(), item.wid, item.now())
        is_terminal, outstanding = ws.resolve_own_registry_completion_status(
            item.root, item.entry(),
        )
        self.assertTrue(is_terminal)
        self.assertFalse(ws.milestone_complete_gate_reachable(
            phase="IMPLEMENTING", is_terminal=is_terminal,
        ))

        # `/milestone-implement` step 2's writer is the way out, and it is
        # a real transition, not a no-op, from this phase.
        phase_before, revision_before = item.milestone_implement_wrap_up()
        self.assertEqual(phase_before, "IMPLEMENTING")
        self.assertEqual(item.entry()["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        self.assertEqual(item.entry()["state_revision"], revision_before + 1)

        # Re-entering the branch on a later invocation is a true no-op.
        phase_before, revision_before = item.milestone_implement_wrap_up()
        self.assertEqual(phase_before, "SELF_REVIEWING_IMPLEMENTATION")
        self.assertEqual(item.entry()["state_revision"], revision_before)

        # And the round publishes from there -- the item's first
        # implementation round, since the amendment preceded any bundle.
        outcome, s2, proc = item.generate_impl_bundle(
            "implementation", expect_outcome="ordinary", wrap_up=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(item.entry()["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(item.entry()["implementation_revision"], 1)
        ws.validate_bundle_generation_record_commit(item.root, s2, item.wid)
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["technical_approval"]["status"], "CURRENT")
        item.prepare_functional_review()
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    def test_b13_plan_revised_and_re_approved_mid_implementation(self):
        """A plan revision landing after the plan approval: the approval
        stales by recomputation, implementation is blocked until a fresh
        two-stage review and a fresh approval, and the item's completed
        checkpoints survive intact."""
        item = self.item
        item.milestone_plan(checkpoints=CHECKPOINTS_TWO, requirements=REQUIREMENTS_TWO)
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        item.implement_checkpoint("CP1", {item.deliverable: "// CP1\n"})
        self.assertEqual(item.entry()["checkpoints"]["CP1"]["status"], "COMPLETE")

        # workflow-2.6.0 (`D-Plan-Review-Bundle-Binding`, `LPR-R4-002`): the
        # 2.5.1 in-place re-publish from `IMPLEMENTING` is refused before
        # any state write, naming the amendment route; the edits the
        # command made before its publish stay in the worktree.
        with self.assertRaises(ws.PlanReviewPhaseNotPlanStageError) as refused:
            item.apply_plan_review(2, CHECKPOINTS_TWO, REQUIREMENTS_TWO,
                                   plan_body="Plan body, revised mid-implementation.\n")
        self.assertIn(f"/request-plan-amendment {item.wid}", str(refused.exception))
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")
        self.assertEqual(item.entry()["plan_revision"], 1)
        # While the revision is uncommitted the approval is still current
        # by design: `approval_is_current` is commit-sourced, "exactly what
        # a fresh session sees, never uncommitted local edits". Ledger row
        # `O7` records the consequence and its containment.
        self.assertTrue(ws.approval_is_current(
            item.root, item.entry(), stage="plan", base_commit=item.base_commit,
        ))
        self.scratch.commit(f"plan({item.wid}): revision 2",
                            {"Workflow-Work-Item": item.wid})
        # Committed, it stales the approval and blocks further
        # implementation.
        self.assertFalse(ws.implementing_entry_reachable(
            item.root, item.entry(), item.base_commit,
        ))
        with self.assertRaises(AssertionError):
            item.implement_checkpoint("CP2", {item.deliverable: "// too early\n"})
        # Containment: even if a session carried the revision all the way
        # to the terminal gate, `/accept-milestone` refuses -- the live
        # plan-stage protected paths no longer match the approval's own
        # recorded manifest.
        with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
            ws.resolve_own_registry_completion_status(item.root, item.entry())
        # The sanctioned route back into plan review.
        item.request_amendment("revised mid-implementation")
        item.amend_plan(2, CHECKPOINTS_TWO, REQUIREMENTS_TWO,
                        plan_body="Plan body, revised mid-implementation.\n")
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=2)
        item.approve_plan()
        entry = item.entry()
        self.assertEqual(entry["phase"], "IMPLEMENTING")
        self.assertEqual(entry["plan_revision"], 2)
        self.assertEqual(entry["checkpoints"]["CP1"]["status"], "COMPLETE")
        self.assertTrue(ws.implementing_entry_reachable(item.root, entry, item.base_commit))
        self.assertEqual(ws.select_next_checkpoint(entry, item.registry()), "CP2")
        item.implement_checkpoint("CP2", {item.deliverable: "// CP1 + CP2\n"})
        item.generate_impl_bundle("implementation", expect_outcome="ordinary")
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")


class ImplementationLifecycleProduct(ImplementationLifecycleProcess):
    """Matrix rows B1-B5 for a `product` work item."""

    work_item_type = "product"

    def test_b6_implementation_stale_summary_withdraws(self):
        raise unittest.SkipTest("row B6 is type-independent; run once, on the process item")

    def test_b7_idempotent_regeneration(self):
        raise unittest.SkipTest("row B7 is type-independent; run once, on the process item")

    def test_b8_revision_monotonicity_refusals(self):
        raise unittest.SkipTest("row B8 is type-independent; run once, on the process item")

    def test_b9_unrecorded_new_head_refused(self):
        raise unittest.SkipTest("row B9 is type-independent; run once, on the process item")

    def test_b10_protected_change_without_revision_refused(self):
        raise unittest.SkipTest("row B10 is type-independent; run once, on the process item")

    def test_b11_same_content_chain_then_an_ordinary_round(self):
        raise unittest.SkipTest("row B11 is type-independent; run once, on the process item")

    def test_b12_same_content_round_then_provenance_recovery(self):
        raise unittest.SkipTest("row B12 is type-independent; run once, on the process item")

    def test_b13_plan_revised_and_re_approved_mid_implementation(self):
        raise unittest.SkipTest("row B13 is type-independent; run once, on the process item")

    def test_b14_plan_re_approved_with_every_checkpoint_already_complete(self):
        raise unittest.SkipTest("row B14 is type-independent; run once, on the process item")


# ===========================================================================
# C. Functional lifecycle and acceptance
# ===========================================================================


class FunctionalLifecycleProcess(MatrixCase):
    """Matrix rows C1-C5 for a `process` work item."""

    work_item_type = "process"

    def test_c1_functional_clean_to_acceptance(self):
        item = self.item
        self.reach_technical_approved()
        evidence = item.prepare_functional_review()
        self.assertEqual(
            ws.discover_current_functional_checklist_evidence(
                item.root, item.wid, item.base_commit, item.sim.head(),
                item.entry()["implementation_revision"],
            ),
            evidence,
        )
        is_terminal, outstanding = ws.resolve_own_registry_completion_status(
            item.root, item.entry(),
        )
        self.assertTrue(is_terminal)
        self.assertIsNone(outstanding)
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")
        self.assertIsNone(item.state()["active_work_item_id"])

    def test_c2_functional_bounded_fix_cycle(self):
        item = self.item
        self.reach_technical_approved()
        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: the label is wrong.\n")
        fingerprint.assert_functional_review_not_already_consumed(item.root, item.wid)

        item.mark_technical_approval_stale()
        self.assertEqual(item.entry()["technical_approval"]["status"], "STALE")
        self.scratch.write(item.deliverable, "// functional fix\n")
        self.scratch.commit(f"fix({item.wid}): functional finding 1",
                            {"Workflow-Work-Item": item.wid})
        item.generate_impl_bundle("post-fix", expect_outcome="ordinary")
        self.assertEqual(item.entry()["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(item.entry()["implementation_revision"], 2)
        fingerprint.mark_functional_review_consumed(item.root, item.wid)

        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(item.entry()["technical_approval"]["status"], "CURRENT")
        item.prepare_functional_review()
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    def test_c3_functional_same_content_fix(self):
        """Ledger `B3`: the bounded-fix branch's own `same_content`
        outcome, from `AWAITING_FUNCTIONAL_REVIEW`, with a `STALE`
        technical approval already recorded."""
        item = self.item
        self.reach_technical_approved()
        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: turns out to be expected behaviour.\n")
        pinned_head = item.entry()["reviewed_implementation_head"]

        item.mark_technical_approval_stale()
        self.scratch.write(item.excluded_note, "# ledger\n\nfunctional finding 1 rejected.\n")
        self.scratch.commit(f"docs({item.wid}): record the rejection (excluded-only)",
                            {"Workflow-Work-Item": item.wid})
        outcome, s2, proc = item.generate_impl_bundle("post-fix", expect_outcome="same_content")
        self.assertEqual(outcome, "same_content")
        self.assertEqual(proc.returncode, 0)
        entry = item.entry()
        self.assertEqual(entry["implementation_revision"], 1)
        self.assertEqual(entry["reviewed_implementation_head"], pinned_head)
        self.assertEqual(entry["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        ws.validate_bundle_generation_record_commit(item.root, s2, item.wid)
        fingerprint.mark_functional_review_consumed(item.root, item.wid)
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["technical_approval"]["status"], "CURRENT")

    def test_c4_functional_review_consumed_guard(self):
        item = self.item
        self.reach_technical_approved()
        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: narrative-only correction.\n")
        fingerprint.assert_functional_review_not_already_consumed(item.root, item.wid)
        fingerprint.mark_functional_review_consumed(item.root, item.wid)
        with self.assertRaises(fingerprint.FunctionalReviewAlreadyAppliedError):
            fingerprint.assert_functional_review_not_already_consumed(item.root, item.wid)
        # Fresh content is readable again.
        item.write_functional_findings("Finding 2: a genuinely new observation.\n")
        fingerprint.assert_functional_review_not_already_consumed(item.root, item.wid)

    def test_c5_outstanding_checkpoint_cannot_shortcut_the_self_review_gate(self):
        """Salvage audit `O6`, repaired as part of `B8`. This row used to
        hand-write `phase = "SELF_REVIEWING_IMPLEMENTATION"` straight into
        `WORKFLOW_STATE.json` so it could publish a `stage="implementation"`
        bundle while `CP2` was still outstanding. That forgery is what hid
        `B8`: it manufactured, out of band, the exact transition no
        command-reachable writer performed.

        The forgery is now refused by name, and the row proves the whole
        honest tail instead -- the outstanding checkpoint is implemented,
        `complete_checkpoint` performs the real transition, and the item
        reaches `MILESTONE_COMPLETE` through `/accept-milestone`."""
        item = self.item
        item.milestone_plan(checkpoints=CHECKPOINTS_TWO, requirements=REQUIREMENTS_TWO)
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        item.implement_checkpoint("CP1", {item.deliverable: "// CP1\n"})
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")

        # The registry is non-terminal, and every route to a first-round
        # bundle refuses -- including the writer itself, by name.
        is_terminal, outstanding = ws.resolve_own_registry_completion_status(
            item.root, item.entry(),
        )
        self.assertFalse(is_terminal)
        self.assertEqual(outstanding, "CP2")
        with self.assertRaises(ws.IncompleteCheckpointsForSelfReviewError) as ctx:
            ws.enter_self_reviewing_implementation(
                item.state(), item.wid, item.registry(), item.now(),
            )
        self.assertEqual(ctx.exception.outstanding_checkpoint_id, "CP2")
        self.assertIn("CP2", str(ctx.exception))
        # And `/milestone-implement`'s own wrap-up branch is not even
        # entered: ownership resolves a concrete checkpoint, not
        # `NO_CHECKPOINT`.
        self.assertEqual(
            ws.select_next_checkpoint(item.entry(), item.registry()), "CP2",
        )
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError):
            item.generate_impl_bundle("implementation", wrap_up=False)

        # The honest tail: implement it, and the ordinary writer fires.
        item.implement_checkpoint("CP2", {item.deliverable: "// CP1 + CP2\n"})
        self.assertEqual(item.entry()["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        item.generate_impl_bundle("implementation", expect_outcome="ordinary")
        item.write_feedback("APPROVE")
        item.approve_implementation()
        item.prepare_functional_review()
        is_terminal, outstanding = ws.resolve_own_registry_completion_status(
            item.root, item.entry(),
        )
        self.assertTrue(is_terminal)
        self.assertIsNone(outstanding)
        self.assertTrue(ws.milestone_complete_gate_reachable(
            phase=item.entry()["phase"], is_terminal=is_terminal,
        ))
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    def test_c8_unchanged_checklist_still_gets_its_own_round_evidence(self):
        """Ledger `I7`: a round that legitimately needs no checklist
        change still needs its own round-scoped evidence, and
        `discover_current_functional_checklist_evidence` is keyed by
        `<work_item_id>/<implementation_revision>/`."""
        item = self.item
        self.reach_technical_approved()
        checklist = (
            "# Active milestone\n\n## Functional review checklist\n\n"
            "- flow 1: exercise the thing\n"
        )
        first = item.prepare_functional_review(checklist=checklist)
        self.assertEqual(item.entry()["implementation_revision"], 1)

        # A bounded functional fix advances the round; the flows to
        # re-test are unchanged, so the checklist is too.
        item.write_functional_findings("Finding 1: a label typo.\n")
        item.mark_technical_approval_stale()
        self.scratch.write(item.deliverable, "// functional fix\n")
        self.scratch.commit(f"fix({item.wid}): functional finding 1",
                            {"Workflow-Work-Item": item.wid})
        item.generate_impl_bundle("post-fix", expect_outcome="ordinary")
        fingerprint.mark_functional_review_consumed(item.root, item.wid)
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["implementation_revision"], 2)

        second = item.prepare_functional_review(checklist=checklist)
        self.assertNotEqual(second["commit_sha"], first["commit_sha"])
        self.assertEqual(second["blob"], first["blob"])
        found = ws.discover_current_functional_checklist_evidence(
            item.root, item.wid, item.base_commit, self.scratch.head(), 2,
        )
        self.assertEqual(found, second)
        # Every downstream check accepts the empty commit unchanged. The
        # pre-commit evidence guard that used to be asserted here was
        # `/accept-scoped-remediation`'s alone and went with it (ledger
        # `I10`), so the two properties it proved are asserted directly:
        # the commit's own committed content at the checklist path is
        # exactly the blob its trailer names, and the working tree is clean
        # there -- which is what `/review-functional`'s live-blob
        # comparison actually depends on.
        committed_blob = subprocess.run(
            ["git", "rev-parse", f"{found['commit_sha']}:{ws.FUNCTIONAL_CHECKLIST_PATH}"],
            cwd=item.root, check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(committed_blob, found["blob"])
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ws.FUNCTIONAL_CHECKLIST_PATH],
            cwd=item.root, check=True, capture_output=True, text=True,
        ).stdout
        self.assertEqual(status.strip(), "")
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    def test_c9_bounded_fix_without_refreshed_author_inputs(self):
        """Convergence pass 11, ledger `I19`: `/apply-functional-review`'s
        bounded branch drives a `post-fix` generation, and before the
        repair named none of the author-written preconditions that
        generation hard-requires. This row is the behavioural half of that
        repair -- it drives the branch with the previous round's bundle
        inputs left exactly as they were, which is what a run following
        the pre-repair command text actually produced, and pins both
        failure modes in the order they really fire:

        1. `assert_review_request_states_review_content_id`, from
           `--write-manifest`, *refuses* the whole generation -- nothing
           published, nothing withdrawn, `current/` still in place;
        2. with only that corrected,
           `assert_stage_completeness`, from `finalize_bundle_generation`,
           *withdraws* the bundle: `current/` is renamed to a
           `current.rejected-<token>/` sibling and the archive is deleted.
           A completed withdrawal removes its own `REJECTED` marker, so
           the quarantine directory -- not the marker -- is the evidence;
        3. with both refreshed, the same generation publishes.

        The corresponding `same_content` round needs neither refresh, and
        that asymmetry is the reason the gap survived: row `C3` exercised
        exactly the branch whose author-written lines stay valid untouched
        (`test_c3_functional_same_content_fix`)."""
        item = self.item
        self.reach_technical_approved()
        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: the label is wrong.\n")
        bundle = item.bundle_dir()
        stale_summary = (bundle / "IMPLEMENTATION_SUMMARY.md").read_text()
        stale_request = (bundle / "REVIEW_REQUEST.md").read_text()
        self.assertIn("implementation_revision: 1", stale_summary)

        item.mark_technical_approval_stale()
        self.scratch.write(item.deliverable, "// functional fix\n")
        self.scratch.commit(f"fix({item.wid}): functional finding 1",
                            {"Workflow-Work-Item": item.wid})

        # (1) Neither line refreshed: the digest check refuses first.
        outcome, durability, proc = item.generate_impl_bundle(
            "post-fix", expect_outcome="ordinary", check=False, refresh_inputs=False,
        )
        self.assertEqual(item.entry()["implementation_revision"], 2)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ReviewContentIdMismatchError", proc.stderr)
        self.assertTrue(bundle.is_dir(), "a refusal must not withdraw the bundle")
        self.assertEqual((bundle / "IMPLEMENTATION_SUMMARY.md").read_text(), stale_summary)
        self.assertEqual((bundle / "REVIEW_REQUEST.md").read_text(), stale_request)

        # (2) Only the digest corrected: the revision check withdraws.
        digest, _ = item.impl_review_content_id(durability)
        (bundle / "REVIEW_REQUEST.md").write_text(
            f"# Review request\n\nstage: post-fix\nwork item: {item.wid}\n"
            f"review_content_id: {digest}\n"
        )
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("status: withdrawn", proc.stderr)
        self.assertIn("IMPLEMENTATION_SUMMARY.md states implementation_revision: 1", proc.stderr)
        self.assertFalse(bundle.exists())
        root = item.root / ".ai-review" / item.wid
        quarantined = [p for p in root.iterdir() if p.name.startswith("current.rejected-")]
        self.assertEqual(len(quarantined), 1, sorted(p.name for p in root.iterdir()))
        self.assertFalse((root / "review-bundle.tar.gz").exists())
        # The withdrawal completed, so it removed its own marker -- and the
        # resolver still answers scoped, because it keys off the work
        # item's own root directory, never the transient `current/`
        # (ledger `I18`).
        self.assertFalse(
            (item.root / fingerprint.resolve_rejected_marker_path(item.root, item.wid)).exists()
        )
        self.assertEqual(item.bundle_dir(), root / "current")

        # (3) Both refreshed, exactly as the repaired command now says:
        # the same generation publishes, first attempt.
        revision = item.entry()["implementation_revision"]
        item.write_bundle_inputs("post-fix", durability, revision)
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((bundle / "MANIFEST.md").is_file())
        self.assertTrue((root / "review-bundle.tar.gz").is_file())
        self.assertIn(f"implementation_revision: {revision}",
                      (bundle / "IMPLEMENTATION_SUMMARY.md").read_text())

        # And the round is still approvable from there -- the withdrawal
        # cost a regeneration, not the round.
        fingerprint.mark_functional_review_consumed(item.root, item.wid)
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(item.entry()["technical_approval"]["status"], "CURRENT")

    def test_c10_same_content_bounded_fix_needs_no_refresh_at_all(self):
        """The control arm for `C9`, and the reason ledger `I19` survived:
        a `same_content` bounded fix pins `implementation_revision` by
        contract, and its protected implementation-stage content is
        byte-identical by definition of the outcome, so the previous
        round's `review_content_id` recomputes to the same digest. Both
        author-written lines stay valid *untouched* -- this row drives the
        branch with nothing refreshed and requires a first-attempt
        publication."""
        item = self.item
        self.reach_technical_approved()
        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: expected behaviour.\n")
        bundle = item.bundle_dir()
        summary_before = (bundle / "IMPLEMENTATION_SUMMARY.md").read_text()
        request_before = (bundle / "REVIEW_REQUEST.md").read_text()

        item.mark_technical_approval_stale()
        self.scratch.write(item.excluded_note, "# ledger\n\nfinding 1 rejected.\n")
        self.scratch.commit(f"docs({item.wid}): record the rejection (excluded-only)",
                            {"Workflow-Work-Item": item.wid})
        outcome, _, proc = item.generate_impl_bundle(
            "post-fix", expect_outcome="same_content", check=False, refresh_inputs=False,
        )
        self.assertEqual(outcome, "same_content")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(item.entry()["implementation_revision"], 1)
        self.assertEqual((bundle / "IMPLEMENTATION_SUMMARY.md").read_text(), summary_before)
        self.assertEqual((bundle / "REVIEW_REQUEST.md").read_text(), request_before)

    def test_c6_broad_remediation_child(self):
        """`/apply-functional-review`'s broad branch: the fix is never
        implemented inline. A child work item is created, the parent
        cannot complete while it is open, and the child runs the ordinary
        cycle on its own."""
        item = self.item
        self.reach_technical_approved()
        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: this needs its own milestone.\n")
        config = json.loads(self.scratch.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        child_holder = {}

        def create(state):
            new_state, child_id = ws.create_remediation_child_work_item(
                state, config, parent_work_item_id=item.wid,
                plan_path=f"docs/ai-workflow/{item.wid}-remediation-1-plan.md",
                registry_path=(
                    f"docs/ai-workflow/registry/{item.wid}-remediation-1-registry.json"
                ),
                base_commit=self.scratch.head(), now=item.now(),
            )
            child_holder["id"] = child_id
            return new_state

        item.tx(create)
        child_id = child_holder["id"]
        self.assertEqual(child_id, f"{item.wid}-remediation-1")
        child = item.state()["work_items"][child_id]
        self.assertEqual(child["parent_work_item_id"], item.wid)
        self.assertEqual(child["work_item_type"], item.wtype)
        self.assertEqual(child["phase"], "PLANNING")
        # `active_work_item_id` is never stolen by the child.
        self.assertEqual(item.state()["active_work_item_id"], item.wid)
        # The parent's own registry/checkpoint history is untouched.
        self.assertEqual(
            item.entry()["checkpoints"]["CP1"]["status"], "COMPLETE",
        )
        fingerprint.mark_functional_review_consumed(item.root, item.wid)
        self.scratch.commit(
            f"chore({item.wid}): defer broad remediation to {child_id}",
            {"Workflow-Work-Item": item.wid},
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )

        # The parent cannot complete while the child is open, and the
        # block is on `phase`, not on any separate bookkeeping field.
        self.assertEqual(ws.incomplete_children(item.state(), item.wid), [child_id])
        with self.assertRaises(ws.IncompleteChildWorkItemError):
            ws.complete_work_item(item.state(), item.wid, item.now(), repo_root=item.root)

        # This row deliberately stops here. Clearing the block requires the
        # child to *actually* reach `MILESTONE_COMPLETE`, and the only
        # sanctioned way there is its own full cycle through the ordinary
        # commands -- which is row `C7`'s subject
        # (`RemediationChildFullCycle`), including the parent's own
        # acceptance afterwards. This row used to hand-write
        # `phase = "MILESTONE_COMPLETE"` into the child entry instead
        # (Workflow v2.x convergence campaign, ledger row `B9`): a forged
        # phase that skipped planning, approval, implementation and the
        # child's own completion obligations, and that would have stayed
        # green even while no command could select the child at all.
        self.assertEqual(item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(
            item.state()["work_items"][child_id]["phase"], "PLANNING",
        )


class FunctionalLifecycleProduct(FunctionalLifecycleProcess):
    """Matrix rows C1-C2 for a `product` work item."""

    work_item_type = "product"

    def test_c3_functional_same_content_fix(self):
        raise unittest.SkipTest("row C3 is type-independent; run once, on the process item")

    def test_c4_functional_review_consumed_guard(self):
        raise unittest.SkipTest("row C4 is type-independent; run once, on the process item")

    def test_c5_outstanding_checkpoint_cannot_shortcut_the_self_review_gate(self):
        raise unittest.SkipTest("row C5 is type-independent; run once, on the process item")

    def test_c6_broad_remediation_child(self):
        raise unittest.SkipTest("row C6 is type-independent; run once, on the process item")

    def test_c8_unchanged_checklist_still_gets_its_own_round_evidence(self):
        raise unittest.SkipTest("row C8 is type-independent; run once, on the process item")

    def test_c9_bounded_fix_without_refreshed_author_inputs(self):
        raise unittest.SkipTest("row C9 is type-independent; run once, on the process item")

    def test_c10_same_content_bounded_fix_needs_no_refresh_at_all(self):
        raise unittest.SkipTest("row C10 is type-independent; run once, on the process item")


# ===========================================================================
# D. Recovery, resume, staleness and concurrency
# ===========================================================================


class RecoveryAndConcurrency(MatrixCase):
    """Matrix rows D1-D5."""

    work_item_type = "process"

    def test_d1_disposition_only_update(self):
        """A REVISE round where every finding is rejected with evidence and
        nothing at all is committed in between: the republication has an
        empty intervening interval."""
        item = self.item
        self.reach_first_bundle()
        pinned_head = item.entry()["reviewed_implementation_head"]
        item.write_feedback("REVISE")
        item.enter_applying_feedback()
        outcome, s2, proc = item.generate_impl_bundle("post-fix", expect_outcome="same_content")
        self.assertEqual(proc.returncode, 0)
        entry = item.entry()
        self.assertEqual(entry["implementation_revision"], 1)
        self.assertEqual(entry["reviewed_implementation_head"], pinned_head)
        self.assertEqual(
            ws.verify_implementation_provenance_interval(item.root, entry, item.base_commit), s2,
        )

    def test_d2_stale_bundle_vs_durable_state(self):
        item = self.item
        self.reach_first_bundle()
        self.scratch.write(item.excluded_note, "# ledger\n")
        self.scratch.commit(f"docs({item.wid}): concurrent excluded-only commit",
                            {"Workflow-Work-Item": item.wid})
        with self.assertRaises(fingerprint.WorktreeOrHeadMismatchError):
            fingerprint.assert_local_generation_matches(
                item.root, item.bundle_dir() / "MANIFEST.md",
            )
        self.assertFalse(ws.implementation_provenance_interval_reachable(
            item.root, item.entry(), item.base_commit,
        ))
        item.write_feedback("APPROVE")
        with self.assertRaises(fingerprint.WorktreeOrHeadMismatchError):
            item.approve_implementation()

    def test_d3_interrupted_generation_resumes(self):
        """State written and its durability commit landed, but the
        generator never ran: a fresh session regenerates for the same head
        with no second durability commit and no revision change."""
        item = self.item
        self.reach_plan_approved()
        item.implement_checkpoint("CP1", {item.deliverable: "// round 1\n"})
        entry = item.entry()
        head_before = item.sim.head()
        outcome, _ = ws.resolve_bundle_generation_outcome(
            item.root, entry, base_commit=item.base_commit, head=head_before,
        )
        item.tx(lambda state: ws.record_bundle_generation(
            state, item.wid, stage="implementation", head=head_before,
            now=item.now(), outcome=outcome,
        ))
        durability = self.scratch.commit(
            f"chore({item.wid}): record bundle generation, revision 1 (ordinary)",
            {
                "Workflow-Bundle-Generation-Record": f"{item.wid}/1",
                "Workflow-Work-Item": item.wid,
            },
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )
        # The plan-stage bundle from `reach_plan_approved` is still what
        # sits in `current/`: this round's own generation never ran.
        self.assertIn("stage: plan", item.manifest())

        # "Fresh session": nothing but the on-disk repository is carried over.
        resumed = Item(self.scratch, self.work_item_type)
        resumed.base_commit = item.base_commit
        resumed.write_bundle_inputs("implementation", durability, 1)
        proc = self.scratch.prepare(resumed.base_commit, "implementation", resumed.wid)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.scratch.head(), durability)
        self.assertEqual(resumed.entry()["implementation_revision"], 1)
        self.assertIn(f"generation_head: {durability}", resumed.manifest())

    def test_d4_concurrent_excluded_commit(self):
        """A concurrent write to an excluded path never stales an
        approval: `review_content_id` is not a function of live HEAD."""
        item = self.item
        self.reach_technical_approved()
        before = ws.approval_is_current(
            item.root, item.entry(), stage="implementation", base_commit=item.base_commit,
        )
        self.assertTrue(before)
        self.scratch.write(item.excluded_note, "# ledger\n\nunrelated concurrent note\n")
        self.scratch.commit("docs: an unrelated concurrent excluded-only commit")
        self.assertTrue(ws.approval_is_current(
            item.root, item.entry(), stage="implementation", base_commit=item.base_commit,
        ))
        self.assertTrue(ws.approval_is_current(
            item.root, item.entry(), stage="plan", base_commit=item.base_commit,
        ))

    def test_d5_cold_resume(self):
        """Everything an implementing session needs must be re-derivable
        from the on-disk repository alone."""
        item = self.item
        self.reach_plan_approved()
        resumed = Item(self.scratch, self.work_item_type)
        resumed.base_commit = item.base_commit
        entry = resumed.entry()
        self.assertTrue(ws.implementing_entry_reachable(
            resumed.root, entry, resumed.base_commit,
        ))
        self.assertEqual(ws.select_next_checkpoint(entry, resumed.registry()), "CP1")
        resumed.implement_checkpoint("CP1", {resumed.deliverable: "// round 1\n"})
        resumed.generate_impl_bundle("implementation", expect_outcome="ordinary")

        again = Item(self.scratch, self.work_item_type)
        again.base_commit = item.base_commit
        self.assertTrue(ws.implementation_provenance_interval_reachable(
            again.root, again.entry(), again.base_commit,
        ))
        fingerprint.assert_local_generation_matches(
            again.root, again.bundle_dir() / "MANIFEST.md",
        )


class LegacyAdoption(unittest.TestCase):
    """Matrix row E13: `D-Legacy` phase 2's own freshness check must be
    evaluated against the *adopted item's* classification, never another
    work item's. Ledger row `I6`."""

    CORE_ID = "workflow-v2-1-core"
    LEGACY_ID = "legacy-milestone"

    def setUp(self):
        self.scratch = Scratch()
        self.addCleanup(self.scratch.cleanup)

    @staticmethod
    def _declaration(work_item_id, protected_prefix, excluded_prefix):
        return {
            "schema_version": 2, "work_item_id": work_item_id,
            "plan_stage": {
                "protected_paths": [], "excluded_paths": {}, "excluded_prefixes": {},
            },
            "implementation_stage": {
                "protected_paths": {},
                "protected_prefixes": {protected_prefix: "this item's own deliverable"},
                "excluded_paths": {
                    "docs/ACTIVE_MILESTONE.md": "narrative",
                    "docs/ai-workflow/WORKFLOW_STATE.json": "runtime state",
                },
                "excluded_prefixes": {
                    excluded_prefix: "the other type's territory",
                    "docs/ai-workflow/": "workflow bookkeeping",
                },
            },
        }

    def _seed(self):
        scratch = self.scratch
        scratch.write("docs/ACTIVE_MILESTONE.md", "Milestone 8: accepted and closed.\n")
        scratch.write("app/src/Feature.kt", "class Feature\n")
        scratch.write(
            f"docs/ai-workflow/registry/{self.CORE_ID}-artifacts.json",
            json.dumps(self._declaration(self.CORE_ID, "scripts/", "app/"), indent=2) + "\n",
        )
        scratch.write(
            f"docs/ai-workflow/registry/{self.LEGACY_ID}-artifacts.json",
            json.dumps(self._declaration(self.LEGACY_ID, "app/", "scripts/"), indent=2) + "\n",
        )
        reviewed = scratch.commit("base: the legacy milestone as reviewed under v1")
        state = ws.import_legacy_work_item(
            {"schema_version": 1, "active_work_item_id": None, "work_items": {}},
            work_item_id=self.LEGACY_ID, plan_path="docs/ACTIVE_MILESTONE.md",
            registry_path=None, base_commit=reviewed, reviewed_content_commit=reviewed,
            approved_review_content_id="a" * 64,
            legacy_evidence={"note": "reviewed entirely under Workflow v1"},
            user_confirmation="import legacy", now="t0",
        )
        scratch.write(
            "docs/ai-workflow/WORKFLOW_STATE.json", json.dumps(state, indent=2) + "\n",
        )
        scratch.commit("chore: import the legacy work item")
        return state, reviewed

    def test_e13_legacy_adoption_uses_the_adopted_items_own_declarations(self):
        state, reviewed = self._seed()
        own = fingerprint.artifacts_path_for_work_item(self.LEGACY_ID)
        # Clean: nothing protected changed since the reviewed commit.
        promoted = ws.promote_legacy_work_item(
            state, self.scratch.root, work_item_id=self.LEGACY_ID,
            required_active_milestone_substring="accepted and closed",
            artifacts_path=own, now="t1",
        )
        self.assertEqual(promoted["work_items"][self.LEGACY_ID]["phase"],
                         "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(
            promoted["work_items"][self.LEGACY_ID]["governing_workflow_version"], "2.1",
        )
        self.assertEqual(promoted["active_work_item_id"], self.LEGACY_ID)

        # A real product change lands after the reviewed commit.
        self.scratch.write("app/src/Feature.kt", "class Feature { fun changed() {} }\n")
        self.scratch.commit("feat: change the product after the legacy review")
        with self.assertRaises(ws.LegacyAdoptionStaleApprovalError):
            ws.promote_legacy_work_item(
                state, self.scratch.root, work_item_id=self.LEGACY_ID,
                required_active_milestone_substring="accepted and closed",
                artifacts_path=own, now="t2",
            )
        # And `artifacts_path` can no longer be omitted: a caller that
        # forgot it used to evaluate the check against `workflow-v2-1-core`'s
        # own sets, in which `app/` is excluded, and promote a stale
        # approval silently.
        with self.assertRaises(TypeError):
            ws.promote_legacy_work_item(
                state, self.scratch.root, work_item_id=self.LEGACY_ID,
                required_active_milestone_substring="accepted and closed", now="t2",
            )
        with self.assertRaises(TypeError):
            fingerprint.load_implementation_stage_classification(self.scratch.root)
        # The wrong item's declarations really would have said "not stale".
        wrong = fingerprint.load_implementation_stage_classification(
            self.scratch.root, fingerprint.artifacts_path_for_work_item(self.CORE_ID),
        )
        self.assertFalse(ws.any_protected_path_changed_since(
            self.scratch.root, reviewed, "HEAD", *wrong,
        ))
        right = fingerprint.load_implementation_stage_classification(self.scratch.root, own)
        self.assertTrue(ws.any_protected_path_changed_since(
            self.scratch.root, reviewed, "HEAD", *right,
        ))


class ProvenanceChainIntegrity(MatrixCase):
    """Matrix rows E9-E12: the supersession chain's own refusals. These
    are what the `R1` repair leans on -- it admits an unchanged revision
    at a new generation head *only* when the provenance interval verifies,
    so every way that verification can fail must still fail."""

    work_item_type = "process"

    def _forge_record_commit(self, trailers, subject="chore: forged record"):
        # A generation-record commit's own contract is "touches only
        # WORKFLOW_STATE.json", so a forgery still has to move that file.
        state_path = self.item.root / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"][self.item.wid]["state_revision"] += 1
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        return self.scratch.commit(
            subject, trailers, paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )

    def test_e9_broken_supersession_chain_refuses(self):
        item = self.item
        self.reach_first_bundle()
        # A recovered-role commit whose Workflow-Supersedes names the
        # wrong commit breaks the chain's linked-list property.
        forged = self._forge_record_commit({
            "Workflow-Bundle-Generation-Record": f"{item.wid}/1",
            "Workflow-Work-Item": item.wid,
            "Workflow-Supersedes": item.base_commit,
        })
        # Neither candidate is superseded by the other, so the chain has
        # no unique tip: refused as genuine ambiguity rather than
        # silently picked.
        with self.assertRaises((
            ws.AmbiguousBundleGenerationRecordTrailerError,
            ws.MalformedProvenanceSupersessionChainError,
        )):
            ws.verify_implementation_provenance_interval(
                item.root, item.entry(), item.base_commit,
            )
        self.assertFalse(ws.implementation_provenance_interval_reachable(
            item.root, item.entry(), item.base_commit,
        ))
        proc = self.scratch.prepare(item.base_commit, "post-fix", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        # The continuity rule itself, at the terminus: a recovered-role
        # tip whose Workflow-Supersedes names nothing the walk found.
        with self.assertRaises(ws.MalformedProvenanceSupersessionChainError):
            ws._assert_generation_record_terminal_chain_continuity(
                item.root, item.wid, forged, None,
            )

    def test_e10_second_ordinary_role_commit_in_a_chain_refuses(self):
        item = self.item
        self.reach_first_bundle()
        self._forge_record_commit({
            "Workflow-Bundle-Generation-Record": f"{item.wid}/1",
            "Workflow-Work-Item": item.wid,
        })
        # Two unsuperseded candidates for the same value: genuine
        # ambiguity, refused rather than silently picked.
        with self.assertRaises(ws.AmbiguousBundleGenerationRecordTrailerError):
            ws.discover_bundle_generation_record_commits(
                item.root, item.wid, item.base_commit, self.scratch.head(),
            )
        self.assertFalse(ws.implementation_provenance_interval_reachable(
            item.root, item.entry(), item.base_commit,
        ))

    def test_e11_record_commit_touching_a_second_path_refuses(self):
        item = self.item
        durability = self.reach_first_bundle()
        # The ordinary record commit's own contract: exactly
        # WORKFLOW_STATE.json, nothing else.
        ws.validate_bundle_generation_record_commit(item.root, durability, item.wid)
        state_path = item.root / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"][item.wid]["state_revision"] += 1
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.scratch.write(item.excluded_note, "# a second path in the same commit\n")
        forged = self.scratch.commit(
            "chore: forged record touching two paths",
            {
                "Workflow-Bundle-Generation-Record": f"{item.wid}/2",
                "Workflow-Work-Item": item.wid,
            },
            paths=["docs/ai-workflow/WORKFLOW_STATE.json", item.excluded_note],
        )
        with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
            ws.validate_bundle_generation_record_commit(item.root, forged, item.wid)

    def test_e12_merge_in_the_interval_refuses(self):
        item = self.item
        self.reach_first_bundle()
        # A side branch merged into the line the interval walks.
        head = self.scratch.head()
        self.scratch.git("checkout", "-q", "-b", "side", item.base_commit)
        self.scratch.write("README.md", "side branch\n")
        self.scratch.commit("chore: a side-branch commit")
        self.scratch.git("checkout", "-q", "main")
        self.scratch.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
        merged = self.scratch.head()
        self.assertNotEqual(merged, head)
        # HEAD is no longer the record commit at all, so the interval
        # refuses before any chain question is reached.
        with self.assertRaises(ws.HeadPastBundleGenerationRecordError):
            ws.verify_implementation_provenance_interval(
                item.root, item.entry(), item.base_commit,
            )
        # And an interval whose endpoint is reachable only across the
        # merge is refused by name.
        with self.assertRaises(ws.NonFirstParentProvenanceIntervalError):
            ws._generation_record_interval_first_parent_members(
                item.root,
                self.scratch.git("rev-parse", "side").stdout.strip(),
                merged,
            )


class StalenessAndIdentityGuards(MatrixCase):
    """Matrix rows D12-D13 and E7-E8: the guards that decide whether an
    already-granted approval, or an already-declared identity fact, still
    holds."""

    work_item_type = "process"

    def test_d12_plan_edit_after_approval_stales_the_plan_approval(self):
        item = self.item
        self.reach_plan_approved()
        entry = item.entry()
        self.assertTrue(ws.implementing_entry_reachable(item.root, entry, item.base_commit))
        self.assertTrue(ws.approval_is_current(
            item.root, entry, stage="plan", base_commit=item.base_commit,
        ))
        # A protected plan-stage edit after approval.
        self.scratch.write(
            item.plan_path,
            self.scratch.read(item.plan_path) + "\nAn unreviewed paragraph.\n",
        )
        self.scratch.commit(f"docs({item.wid}): unreviewed plan edit",
                            {"Workflow-Work-Item": item.wid})
        entry = item.entry()
        self.assertFalse(ws.approval_is_current(
            item.root, entry, stage="plan", base_commit=item.base_commit,
        ))
        self.assertFalse(ws.implementing_entry_reachable(item.root, entry, item.base_commit))
        # `/milestone-implement` step 1a refuses on exactly that.
        with self.assertRaises(AssertionError):
            item.implement_checkpoint("CP1", {item.deliverable: "// nope\n"})

    def test_d13_missing_bundle_refuses_by_name(self):
        item = self.item
        self.reach_first_bundle()
        shutil.rmtree(item.bundle_dir())
        # `compute_bundle_id` is what every consumer recomputes through,
        # and it names the missing required file rather than producing a
        # digest over an empty directory.
        with self.assertRaises(fingerprint.MissingRequiredBundleFileError):
            fingerprint.compute_bundle_id(item.bundle_dir())
        # The manifest-metadata readers stay quiet on an absent file by
        # contract, so they are never the refusal: an absent bundle is
        # `compute_bundle_id`'s to report.
        self.assertEqual(
            fingerprint.read_manifest_generation_metadata(
                item.bundle_dir() / "MANIFEST.md",
            ), {},
        )
        fingerprint.assert_local_generation_matches(
            item.root, item.bundle_dir() / "MANIFEST.md",
        )
        # And the plan-stage/implementation-stage manifest writers refuse
        # to invent a bundle directory of their own.
        with self.assertRaises(fingerprint.MissingRequiredBundleFileError):
            fingerprint.write_manifest_with_verified_identifiers_implementation_stage_for_work_item(
                item.root, item.wid, item.base_commit,
            )

    def test_e7_base_commit_is_immutable_once_declared(self):
        item = self.item
        item.milestone_plan()
        config = json.loads(self.scratch.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        with self.assertRaises(ws.WorkItemDeclarationFactConflictError):
            ws.route_work_item(
                item.state(), config, work_item_id=item.wid, work_item_type=item.wtype,
                work_item_kind=item.wtype, plan_path=item.plan_path,
                registry_path=item.registry_path, plan_revision=1, now=item.now(),
                mapping_path=item.mapping_path, base_commit="0" * 40,
            )
        # A terminal item's id is never reusable.
        terminal = json.loads(json.dumps(item.state()))
        terminal["work_items"][item.wid]["phase"] = "MILESTONE_COMPLETE"
        with self.assertRaises(ws.WorkItemTerminalReuseError):
            ws.route_work_item(
                terminal, config, work_item_id=item.wid, work_item_type=item.wtype,
                work_item_kind=item.wtype, plan_path=item.plan_path,
                registry_path=item.registry_path, plan_revision=2, now=item.now(),
            )

    def test_e8_plan_revision_mirror_disagreement_refuses_before_generating(self):
        """The generator's own plan-stage preflight has two halves; this
        is the `D-Plan-Revision-Publication`/`WFR-65` one."""
        item = self.item
        item.milestone_plan()
        state_path = item.root / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"][item.wid]["plan_revision"] = 7
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        proc = self.scratch.prepare(item.base_commit, "plan", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("plan_revision mirror", proc.stderr)
        self.assertIn("WFR-65", proc.stderr)
        self.assertFalse((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())
        # And the other half: a base commit that disagrees with the
        # item's own declared base_commit.
        state["work_items"][item.wid]["plan_revision"] = 1
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        other = self.scratch.git("rev-parse", "HEAD~0").stdout.strip()
        self.scratch.write("README.md", "moved on\n")
        moved = self.scratch.commit("chore: an unrelated commit")
        proc = self.scratch.prepare(moved, "plan", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("declares base_commit", proc.stderr)
        self.assertFalse((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())


class PlanApprovalTransactionRecovery(MatrixCase):
    """Matrix rows D10-D11: `/approve-review plan`'s own failure-atomicity
    transaction, interrupted. Reaching these needs the journal opened and
    the staging done but no commit created -- the exact window steps
    4c-6.3 occupy."""

    work_item_type = "process"

    def _build_record(self):
        item = self.item
        review_content_id, projection = (
            fingerprint.compute_review_content_id_plan_stage_for_work_item(item.root, item.wid)
        )
        return ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan",
            user_confirmation=f"plan {item.wid}", now=item.now(),
            reviewed_bundle_id=item.bundle_id(),
            approved_review_content_id=review_content_id,
            review_content_manifest=projection["review_content_manifest"],
        )

    def _open_transaction(self):
        """`/approve-review plan` steps 1-5, stopping just before the
        commit: journal open, members staged, state blob pinned."""
        import base64 as _base64
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        confirmation = f"plan {item.wid}"
        review_content_id, projection = (
            fingerprint.compute_review_content_id_plan_stage_for_work_item(item.root, item.wid)
        )
        bundle_id = item.bundle_id()
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan", user_confirmation=confirmation,
            now=item.now(), reviewed_bundle_id=bundle_id,
            approved_review_content_id=review_content_id,
            review_content_manifest=projection["review_content_manifest"],
        )
        plan = fingerprint.resolve_plan_stage_approval_commit_paths(
            item.root, item.wid, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
        )
        journal = ws.open_plan_approval_journal(
            item.root, work_item_id=item.wid, base_commit=item.base_commit,
            pre_state=item.state(), record=record, approval_now=item.now(),
            expected_bundle_id=bundle_id, expected_review_content_id=review_content_id,
            applicable_paths=plan.paths,
            fifth_member_applies=plan.artifacts_declaration_path is not None,
            fifth_member_sha256=plan.artifacts_declaration_sha256,
            user_confirmation=confirmation,
            quiescence_authorization="acceptance-matrix scenario",
        )
        owner = journal["owner_token"]
        ordinary = tuple(p for p in plan.paths if p != "docs/ai-workflow/WORKFLOW_STATE.json")
        with ws.plan_approval_guarded_mutation(
            item.root, owner_token=owner, step="step-5-stage-and-pin", now=item.now(),
        ):
            ws.stage_plan_approval_commit_paths(item.root, ordinary)
        with ws.plan_approval_guarded_mutation(
            item.root, owner_token=owner, step="step-6.1b-state-pin", now=item.now(),
        ):
            ws.pin_plan_approval_state_blob(
                item.root, _base64.b64decode(journal["expected_post_state_b64"]),
            )
        return journal

    def test_d10_interrupted_plan_approval_rolls_back_and_retries(self):
        item = self.item
        journal = self._open_transaction()
        staged = self.scratch.git("diff", "--name-only", "--cached", "HEAD").stdout.split()
        self.assertTrue(staged)

        # A fresh session classifies the outcome from durable Git state alone.
        reopened = ws.read_plan_approval_journal(item.root)
        self.assertIsNotNone(reopened)
        self.assertEqual(
            ws.classify_plan_approval_outcome(item.root, reopened),
            ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED,
        )
        lease = ws.acquire_plan_approval_guard(
            item.root, holder_owner_token=journal["owner_token"],
            step="rollback-index-reset", now=item.now(),
        )
        try:
            ws.rollback_plan_approval_transaction(
                item.root, owner_token=journal["owner_token"],
            )
        finally:
            ws.release_plan_approval_guard(item.root, lease)
        self.assertIsNone(ws.read_plan_approval_journal(item.root))
        self.assertEqual(
            self.scratch.git("diff", "--name-only", "--cached", "HEAD").stdout.strip(), "",
        )
        # No WORKFLOW_STATE.json bytes were written by the transaction.
        self.assertIsNone(item.entry()["plan_approval"])
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")
        # The four plan-stage files survive the rollback, still pending.
        for rel in (item.plan_path, item.registry_path, item.mapping_path,
                    item.artifacts_path):
            self.assertTrue((item.root / rel).is_file(), rel)
        # And a fresh, complete approval succeeds afterwards.
        item.stage_plan_files()
        item.approve_plan()
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")

    def test_d11_second_invocation_refuses_and_names_the_takeover_literal(self):
        item = self.item
        journal = self._open_transaction()
        with self.assertRaises(ws.PlanApprovalTransactionInProgressError):
            ws.open_plan_approval_journal(
                item.root, work_item_id=item.wid, base_commit=item.base_commit,
                pre_state=item.state(), record=self._build_record(),
                approval_now=item.now(),
                expected_bundle_id=item.bundle_id(),
                expected_review_content_id=fingerprint.read_manifest_identifiers(
                    item.bundle_dir(stage="plan") / "MANIFEST.md",
                )["review_content_id"],
                applicable_paths=(), fifth_member_applies=False, fifth_member_sha256=None,
                user_confirmation=f"plan {item.wid}",
                quiescence_authorization="acceptance-matrix scenario",
            )
        evidence = ws.plan_approval_takeover_evidence(item.root)
        self.assertIsNotNone(evidence["journal"])
        self.assertEqual(evidence["owner_token"], journal["owner_token"])
        self.assertEqual(evidence["outcome"], ws.PLAN_APPROVAL_OUTCOME_NOT_COMMITTED)
        literal = ws.plan_approval_takeover_authorization_literal(evidence)
        self.assertIn(item.wid, literal)
        with self.assertRaises(ws.PlanApprovalTakeoverRefusedError):
            ws.take_over_plan_approval_transaction(
                item.root, work_item_id=item.wid, now=item.now(), user_authorization="go ahead", evidence=evidence,
            )
        new_token = ws.take_over_plan_approval_transaction(
            item.root, work_item_id=item.wid, now=item.now(), user_authorization=literal, evidence=evidence,
        )
        self.assertNotEqual(new_token, journal["owner_token"])
        # The displaced owner can no longer act.
        with self.assertRaises(ws.PlanApprovalOwnershipError):
            ws.assert_plan_approval_journal_owner(item.root, journal["owner_token"])


# ===========================================================================
# E. Invalid, missing and malformed state
# ===========================================================================


class RemediationChildFullCycle(MatrixCase):
    """Matrix row C7: a remediation child is "routed through the full
    normal cycle via the ordinary commands, exactly like any other work
    item, because it is one". Row C6 proves it is created correctly; this
    proves that claim about what happens next -- a child starts with a
    `null` `mapping_path` and no declarations file of its own, so
    `/milestone-plan <child-id>` has to fill both in through the resume
    branch."""

    work_item_type = "product"

    def test_c7_remediation_child_runs_its_own_cycle(self):
        parent = self.item
        self.reach_technical_approved()
        parent.prepare_functional_review()
        config = json.loads(self.scratch.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        holder = {}

        def create(state):
            new_state, child_id = ws.create_remediation_child_work_item(
                state, config, parent_work_item_id=parent.wid,
                plan_path=f"docs/milestones/{parent.wid}-remediation-1-execution.md",
                registry_path=(
                    f"docs/ai-workflow/registry/{parent.wid}-remediation-1-registry.json"
                ),
                base_commit=self.scratch.head(), now=parent.now(),
            )
            holder["id"] = child_id
            return new_state

        parent.write_functional_findings(
            "Finding 1: this needs its own milestone.\n"
        )
        parent.tx(create)
        child_id = holder["id"]
        created = parent.state()["work_items"][child_id]
        self.assertIsNone(created["mapping_path"])
        self.assertEqual(created["work_item_type"], parent.wtype)
        self.assertFalse(
            (self.scratch.root / f"docs/ai-workflow/registry/{child_id}-artifacts.json").exists()
        )
        self.scratch.commit(f"chore({parent.wid}): defer to {child_id}",
                            {"Workflow-Work-Item": parent.wid},
                            paths=["docs/ai-workflow/WORKFLOW_STATE.json"])

        # `/milestone-plan <child-id>` onwards, through the ordinary
        # commands, on the child's own declared paths.
        child = Item(self.scratch, parent.wtype)
        child.wid = child_id
        child.plan_path = created["plan_path"]
        child.registry_path = created["registry_path"]
        child.mapping_path = f"docs/ai-workflow/requirements/{child_id}-mapping.json"
        child.artifacts_path = f"docs/ai-workflow/registry/{child_id}-artifacts.json"
        child.deliverable = parent.deliverable
        child.excluded_note = f"docs/ai-workflow/requirements/{child_id}-ledger.md"
        child.base_commit = created["base_commit"]

        child.milestone_plan()
        resumed = child.entry()
        self.assertEqual(resumed["mapping_path"], child.mapping_path)
        # Diagram finding `B1`: the child's governing version is the config
        # default fixed at creation -- `"2.1"` here, so its plan-review
        # entry is the two-stage one. Since workflow-2.6.0 the publish is
        # mirror-only (`D-Plan-Review-Bundle-Binding`): the child waits at
        # `PLANNING` with a `PUBLISHED` record until its bundle binds, and
        # the bind writes `AWAITING_LOCAL_PLAN_REVIEW` -- never
        # `AWAITING_EXTERNAL_PLAN_REVIEW`.
        self.assertEqual(resumed["governing_workflow_version"], "2.1")
        self.assertEqual(resumed["phase"], "PLANNING")
        self.assertEqual(resumed["plan_review_binding"]["status"], "PUBLISHED")
        self.assertEqual(resumed["parent_work_item_id"], parent.wid)
        # Focus still belongs to the parent -- every command below is
        # therefore driving a work item that is *not* `active_work_item_id`,
        # which is the whole point of the `[work-item-id]` argument
        # contract `TestWorkItemTargetingContract` guards
        # (convergence campaign ledger row `B9`).
        self.assertEqual(child.state()["active_work_item_id"], parent.wid)

        # A `REVISE` round on the child's own plan, driven through
        # `/apply-plan-review <child-id>` while the parent still holds the
        # pointer: `record_local_plan_review` writes `REVISING_PLAN` on the
        # child, `/apply-plan-review` republishes revision 2 and returns it
        # to `AWAITING_LOCAL_PLAN_REVIEW` -- never straight to manual
        # review, and never touching the parent's own entry.
        child.generate_plan_bundle()
        self.assertEqual(child.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        child.write_feedback("REVISE")
        child.record_plan_reviews(local="REVISE")
        self.assertEqual(child.entry()["phase"], "REVISING_PLAN")
        parent_before = dict(parent.entry())
        child.apply_plan_review(plan_revision=2)
        self.assertEqual(child.entry()["phase"], "REVISING_PLAN")
        self.assertEqual(child.entry()["plan_revision"], 2)
        self.assertEqual(parent.entry(), parent_before)
        self.assertEqual(child.state()["active_work_item_id"], parent.wid)

        child.generate_plan_bundle()
        self.assertEqual(child.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(parent.entry(), parent_before)
        child.write_feedback("APPROVE")
        child.record_plan_reviews(round=2)
        child.approve_plan()
        child.implement_checkpoint("CP1", {child.deliverable: "class Feature { fun fixed() {} }\n"})
        child.generate_impl_bundle("implementation", expect_outcome="ordinary")
        child.write_feedback("APPROVE")
        child.approve_implementation()
        child.prepare_functional_review()

        # The parent is still blocked right up to the child's own
        # acceptance -- and `/accept-milestone <child-id>` is the only
        # thing that clears it, since the child never holds the pointer.
        self.assertEqual(ws.incomplete_children(parent.state(), parent.wid), [child_id])
        with self.assertRaises(ws.IncompleteChildWorkItemError):
            ws.complete_work_item(parent.state(), parent.wid, parent.now(), repo_root=parent.root)

        # The child's own terminal gate is real, not inherited from the
        # parent: its own registry must be terminal and its own declared
        # completion obligations must derive `PASS`. Control arm first --
        # an obligation the child's registry declares but nothing can
        # verify refuses the child's completion outright, which is what
        # proves this gate runs for a child at all.
        child_entry = child.entry()
        self.assertTrue(ws.resolve_own_registry_completion_status(child.root, child_entry)[0])
        self.assertEqual(
            {oid for oid, verdict
             in ws.resolve_completion_obligations(child.root, child_entry).items()
             if verdict.classification != "PASS"},
            set(),
        )
        # Control arm: the child is not a second-class work item at its own
        # terminal gate. Slipping a new completion obligation into its
        # registry *after* its own plan approval does not quietly widen the
        # gate -- registry-derived completion refuses outright, because the
        # live plan-stage blob no longer matches the manifest that approval
        # recorded. (Declaring an obligation legitimately, before approval,
        # is `workflow_state_completion_obligations_test.py`'s subject.)
        pristine_registry = self.scratch.read(child.registry_path)
        tampered = json.loads(pristine_registry)
        tampered["checkpoints"][0]["completion_obligations"] = ["WFO-NEVER-VERIFIABLE"]
        self.scratch.write(child.registry_path, json.dumps(tampered, indent=2) + "\n")
        self.scratch.commit(f"chore({child_id}): tamper with the approved registry",
                            {"Workflow-Work-Item": child_id}, paths=[child.registry_path])
        with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
            ws.complete_work_item(child.state(), child_id, child.now(), repo_root=child.root)
        self.scratch.write(child.registry_path, pristine_registry)
        self.scratch.commit(f"chore({child_id}): restore the approved registry",
                            {"Workflow-Work-Item": child_id}, paths=[child.registry_path])

        child.accept_milestone()
        self.assertEqual(child.entry()["phase"], "MILESTONE_COMPLETE")
        self.assertIsNone(child.entry()["current_checkpoint_id"])
        # Completing the child does not steal or clear the parent's own
        # focus pointer: `complete_work_item` resets it only when it
        # pointed at the completed item.
        self.assertEqual(child.state()["active_work_item_id"], parent.wid)

        # Only now can the parent complete.
        self.assertEqual(ws.incomplete_children(parent.state(), parent.wid), [])
        fingerprint.mark_functional_review_consumed(parent.root, parent.wid)
        parent.prepare_functional_review()
        parent.accept_milestone()
        self.assertEqual(parent.entry()["phase"], "MILESTONE_COMPLETE")
        self.assertIsNone(parent.state()["active_work_item_id"])


class ConcurrentWorkItems(unittest.TestCase):
    """Matrix row D14: `D1`'s "resume-focus pointer, not an execution
    lock" claim, exercised end to end. Two work items of *different*
    types live in one repository at once, each created strictly through
    the sanctioned generators, and neither may stale or unclassify the
    other."""

    def setUp(self):
        self.scratch = Scratch()
        self.addCleanup(self.scratch.cleanup)
        self.process = Item(self.scratch, "process")
        self.product = Item(self.scratch, "product")
        self.process.seed()
        self.product.base_commit = self.process.base_commit

    def test_d14_two_typed_work_items_do_not_interfere(self):
        process, product = self.process, self.product
        (self.scratch.root / "docs/milestones").mkdir(parents=True, exist_ok=True)

        # The process item reaches a technical approval first.
        process.milestone_plan()
        process.generate_plan_bundle()
        process.write_feedback("APPROVE")
        process.record_plan_reviews()
        process.approve_plan()
        self.assertEqual(self.scratch.read("docs/ai-workflow/WORKFLOW_STATE.json").count(
            f'"{process.wid}"'), self.scratch.read(
            "docs/ai-workflow/WORKFLOW_STATE.json").count(f'"{process.wid}"'))
        process.implement_checkpoint("CP1", {process.deliverable: "// process work\n"})
        process.generate_impl_bundle("implementation", expect_outcome="ordinary")
        process.write_feedback("APPROVE")
        process.approve_implementation()
        self.assertEqual(process.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(self.process.state()["active_work_item_id"], process.wid)

        # The product item now runs its own plan cycle in the same
        # repository, writing its own plan/registry/mapping/artifacts and
        # its own product code.
        product.milestone_plan()
        # Routing never steals the focus pointer.
        self.assertEqual(product.state()["active_work_item_id"], process.wid)
        product.generate_plan_bundle()
        product.write_feedback("APPROVE")
        product.record_plan_reviews()
        product.approve_plan()
        product.implement_checkpoint("CP1", {product.deliverable: "class Feature\n"})
        product.generate_impl_bundle("implementation", expect_outcome="ordinary")

        # Neither item's approvals were disturbed by the other's writes.
        self.assertTrue(ws.approval_is_current(
            self.scratch.root, process.entry(), stage="plan",
            base_commit=process.base_commit,
        ))
        self.assertTrue(ws.approval_is_current(
            self.scratch.root, process.entry(), stage="implementation",
            base_commit=process.base_commit,
        ))
        self.assertTrue(ws.approval_is_current(
            self.scratch.root, product.entry(), stage="plan",
            base_commit=product.base_commit,
        ))
        # Each item's own classification still resolves every path the
        # other one touched.
        for item in (process, product):
            fingerprint.compute_review_content_id_plan_stage_for_work_item(
                self.scratch.root, item.wid,
            )
            item.impl_review_content_id(self.scratch.head())
        # And the process item can still finish.
        product.write_feedback("APPROVE")
        product.approve_implementation()
        process.prepare_functional_review()
        process.accept_milestone()
        self.assertEqual(process.entry()["phase"], "MILESTONE_COMPLETE")
        # Completing one item releases the focus pointer without touching
        # the other.
        self.assertIsNone(process.state()["active_work_item_id"])
        self.assertEqual(product.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")


class InvalidAndMalformedState(MatrixCase):
    """Matrix rows E1-E6."""

    work_item_type = "process"

    def _corrupt_type(self, value):
        state_path = self.item.root / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"][self.item.wid]["work_item_type"] = value
        state_path.write_text(json.dumps(state, indent=2) + "\n")

    #: Every documented refusal a corrupt `work_item_type` may produce.
    #: The point of ledger `I2` is that the refusal is always one of
    #: these, named and catchable -- never a raw `TypeError`.
    DOCUMENTED_TYPE_REFUSALS = (
        fingerprint.InvalidWorkItemTypeError,
        fingerprint.UnknownWorkItemError,
        fingerprint.PlanStageNotApplicableError,
    )

    def _assert_both_stages_refuse_cleanly(self):
        item = self.item
        with self.assertRaises(fingerprint.PlanStageNotApplicableError):
            fingerprint.resolve_plan_stage_metadata(item.root, item.wid)
        with self.assertRaises(self.DOCUMENTED_TYPE_REFUSALS):
            fingerprint.write_manifest_with_verified_identifiers_implementation_stage_for_work_item(
                item.root, item.wid, item.base_commit,
            )
        with self.assertRaises(self.DOCUMENTED_TYPE_REFUSALS):
            ws.resolve_bundle_generation_outcome(
                item.root, item.entry(), base_commit=item.base_commit, head=item.sim.head(),
            )
        proc = self.scratch.prepare(item.base_commit, "implementation", item.wid, check=False)
        self.assertNotEqual(proc.returncode, 0)
        # The refusal must be this module's own documented error, never a
        # bare `TypeError:` escaping from `re.match`/`frozenset.__contains__`
        # (ledger `I2`). "InvalidWorkItemTypeError" legitimately contains
        # the substring, so match the exception line itself.
        self.assertIsNone(
            re.search(r"^TypeError:", proc.stderr, re.MULTILINE), proc.stderr,
        )
        self.assertTrue(
            any(exc.__name__ in proc.stderr for exc in self.DOCUMENTED_TYPE_REFUSALS),
            proc.stderr,
        )

    def test_e1_invalid_work_item_type(self):
        self.reach_first_bundle()
        self._corrupt_type("widget")
        self._assert_both_stages_refuse_cleanly()

    def test_e2_non_string_work_item_type(self):
        """Ledger `I2`: an unhashable JSON value used to escape as a raw
        `TypeError` from every non-plan-stage reader."""
        self.reach_first_bundle()
        for value in (["process"], {"type": "process"}):
            with self.subTest(value=value):
                self._corrupt_type(value)
                self._assert_both_stages_refuse_cleanly()

    def test_e3_missing_work_item_type(self):
        self.reach_first_bundle()
        self._corrupt_type(None)
        self._assert_both_stages_refuse_cleanly()

    def test_e4_malformed_approval_record(self):
        """A persisted `review_content_manifest` holding the whole
        projection object must be refused by the real live-state consumer,
        not crash it."""
        item = self.item
        self.reach_technical_approved()
        item.prepare_functional_review()
        state_path = item.root / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        approval = state["work_items"][item.wid]["plan_approval"]
        approval["review_content_manifest"] = {
            "stage": "plan", "review_content_manifest": approval["review_content_manifest"],
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
            ws.resolve_own_registry_completion_status(item.root, item.entry())
        with self.assertRaises(ws.InvalidApprovalRecordError):
            ws.validate_state(json.loads(state_path.read_text()))

    def test_e5_malformed_feedback(self):
        item = self.item
        self.reach_first_bundle()
        bundle_id = item.bundle_id()
        # missing binding fields
        (item.feedback_dir() / "REVIEW_FEEDBACK.md").write_text(
            "# Review Decision\n\nStatus: APPROVE\n"
        )
        with self.assertRaises(fingerprint.MissingFeedbackBindingFieldError):
            fingerprint.assert_feedback_matches_bundle(
                item.feedback_fields(), bundle_id=bundle_id,
                base_commit=item.base_commit, work_item_id=item.wid,
            )
        # a different work item's feedback
        item.write_feedback("APPROVE", work_item="some-other-item")
        with self.assertRaises(fingerprint.FeedbackBundleMismatchError):
            fingerprint.assert_feedback_matches_bundle(
                item.feedback_fields(), bundle_id=bundle_id,
                base_commit=item.base_commit, work_item_id=item.wid,
            )
        with self.assertRaises(fingerprint.FeedbackOwnedByOtherWorkItemError):
            fingerprint.assert_feedback_not_owned_by_other_work_item(
                (item.feedback_dir() / "REVIEW_FEEDBACK.md").read_text(),
                work_item_id=item.wid,
            )
        # a stale bundle id
        item.write_feedback("APPROVE", bundle_id="0" * 64)
        with self.assertRaises(fingerprint.FeedbackBundleMismatchError):
            fingerprint.assert_feedback_matches_bundle(
                item.feedback_fields(), bundle_id=bundle_id,
                base_commit=item.base_commit, work_item_id=item.wid,
            )

    def test_e6_block_pin_survives_laundering(self):
        item = self.item
        self.reach_first_bundle()
        bundle_id = item.bundle_id()
        item.write_feedback("BLOCK")
        review_content_id, _ = item.impl_review_content_id(item.sim.head())
        item.tx(lambda state: ws.record_technical_review_block_pin(
            state, item.wid, bundle_id=bundle_id,
            review_content_id=review_content_id, now=item.now(),
        ))
        self.assertTrue(ws.is_technical_review_block_pinned(item.entry(), bundle_id))
        # The mutable feedback file is edited from BLOCK to REVISE, leaving
        # every binding field untouched.
        item.write_feedback("REVISE")
        self.assertEqual(item.feedback_fields()["status"], "REVISE")
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status="REVISE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True, pinned_block=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))
        with self.assertRaises(ws.BlockCannotApproveError):
            ws.resolve_approval_basis(
                latest_round_status="REVISE", feedback_bundle_id=bundle_id,
                current_bundle_id=bundle_id, user_confirmation=f"implementation {item.wid}",
                work_item_id=item.wid, stage="implementation", pinned_block=True,
            )
        with self.assertRaises(AssertionError):
            item.approve_implementation()


# ===========================================================================
# F. The sanctioned generators, and a repository adopting the workflow
# ===========================================================================


class GeneratedArtifactsAreUsable(MatrixCase):
    """Matrix rows F1-F2."""

    work_item_type = "product"

    def test_f1_generated_declarations_are_usable(self):
        """Ledger `B4`: a work item created strictly through the sanctioned
        generators -- no hand edit anywhere -- must reach a published
        implementation bundle. Every other row in this suite already relies
        on that (none of them passes an `artifacts=` override); this one
        asserts it directly, including that the on-disk declaration is
        byte-identical to the generator's own output."""
        item = self.item
        item.milestone_plan()
        on_disk = json.loads(self.scratch.read(item.artifacts_path))
        self.assertEqual(on_disk, ws.generate_artifacts_declarations(
            item.wid, item.plan_path, item.registry_path, item.mapping_path,
            work_item_type=item.wtype,
        ))
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        item.implement_checkpoint("CP1", {item.deliverable: "class Feature\n"})
        outcome, durability, proc = item.generate_impl_bundle(
            "implementation", expect_outcome="ordinary",
        )
        self.assertEqual(proc.returncode, 0)
        # The item's own product deliverable is what the round protects.
        _, projection = item.impl_review_content_id(durability)
        self.assertIn(
            item.deliverable, [entry["path"] for entry in projection["review_content_manifest"]],
        )
        item.write_feedback("APPROVE")
        item.approve_implementation()
        item.prepare_functional_review()
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    def test_f1b_process_generated_declarations_are_usable(self):
        scratch = Scratch()
        self.addCleanup(scratch.cleanup)
        item = Item(scratch, "process")
        item.seed()
        item.milestone_plan()
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        item.implement_checkpoint("CP1", {item.deliverable: "# feature\n"})
        _, durability, proc = item.generate_impl_bundle(
            "implementation", expect_outcome="ordinary",
        )
        self.assertEqual(proc.returncode, 0)
        _, projection = item.impl_review_content_id(durability)
        self.assertIn(
            item.deliverable, [entry["path"] for entry in projection["review_content_manifest"]],
        )

    def test_f3_product_milestone_with_a_realistic_documentation_and_build_footprint(self):
        """Salvage audit `I8`: a real product milestone touches more than
        `app/`. It updates the UX flows and the domain glossary it changed,
        records an ADR, bumps the root build script and the version
        catalog, and edits `README.md` -- all through generated
        declarations, with no hand edit anywhere.

        Before the repair, the generated `product` implementation-stage
        classification named `app/`, `gradle/`, `config/` and its own file
        and nothing else, so `docs/UX_FLOWS.md`, `docs/DOMAIN_GLOSSARY.md`,
        `docs/adr/`, `.github/`, `docs/agent-context/`,
        `docs/improvements/` and every repository-root build file were
        unclassified. The first implementation bundle raised
        `UnclassifiedPathError` -- after the plan-approval hard gate had
        already advanced durable state and the checkpoint work was already
        committed. The root build files were unclassified at the *plan*
        stage too, for both work-item types, so the plan-stage freshness
        recomputation every later gate performs failed closed as well."""
        scratch = Scratch()
        self.addCleanup(scratch.cleanup)
        item = Item(scratch, "product")
        item.seed(extra_files={
            "docs/UX_FLOWS.md": "# UX flows\n",
            "docs/DOMAIN_GLOSSARY.md": "# Domain glossary\n",
            "docs/adr/0003-layered-modular-architecture.md": "# ADR 3\n",
            "docs/agent-context/CONTEXT_INVENTORY.md": "# inventory\n",
            "docs/improvements/backlog.md": "# backlog\n",
            ".github/workflows/ci.yml": "name: CI\n",
            ".github/copilot-instructions.md": "# instructions\n",
            "build.gradle.kts": "// root build\n",
            "settings.gradle.kts": 'rootProject.name = "scratch"\n',
            "gradle.properties": "org.gradle.jvmargs=-Xmx2g\n",
            "gradlew": "#!/bin/sh\n",
            "gradlew.bat": "@echo off\n",
            "gradle/libs.versions.toml": "[versions]\n",
            "config/detekt/detekt.yml": "build:\n",
        })
        item.milestone_plan()
        # No hand edit: the declaration on disk is the generator's output.
        self.assertEqual(
            json.loads(scratch.read(item.artifacts_path)),
            ws.generate_artifacts_declarations(
                item.wid, item.plan_path, item.registry_path, item.mapping_path,
                work_item_type=item.wtype,
            ),
        )
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()

        footprint = {
            item.deliverable: "class Feature\n",
            "app/src/test/kotlin/FeatureTest.kt": "class FeatureTest\n",
            "docs/UX_FLOWS.md": "# UX flows\n\n## New flow\n",
            "docs/DOMAIN_GLOSSARY.md": "# Domain glossary\n\n- New term\n",
            "docs/adr/0004-new-decision.md": "# ADR 4\n",
            "build.gradle.kts": "// root build, bumped\n",
            "gradle/libs.versions.toml": "[versions]\ncompose = \"1.9.0\"\n",
            "config/detekt/detekt.yml": "build:\n  maxIssues: 0\n",
            "README.md": "scratch repository\n\nupdated\n",
            "docs/ACTIVE_MILESTONE.md": "# Active milestone\n\nmilestone-9\n",
        }
        item.implement_checkpoint("CP1", footprint)

        # Every path in the footprint classifies at both stages -- the
        # check that used to raise, run directly and by name.
        classification = item.impl_classification()
        plan_protected, plan_excluded_paths, plan_excluded_prefixes = (
            fingerprint.load_plan_stage_classification(
                item.root, fingerprint.artifacts_path_for_work_item(item.wid),
            )
        )
        expected_impl = {
            item.deliverable: "protected",
            "app/src/test/kotlin/FeatureTest.kt": "protected",
            "docs/UX_FLOWS.md": "protected",
            "docs/DOMAIN_GLOSSARY.md": "protected",
            "docs/adr/0004-new-decision.md": "protected",
            "build.gradle.kts": "protected",
            "gradle/libs.versions.toml": "protected",
            "config/detekt/detekt.yml": "protected",
            "README.md": "excluded",
            "docs/ACTIVE_MILESTONE.md": "excluded",
            # Shared territory: excluded for both types, never protected
            # by one of them.
            ".github/workflows/ci.yml": "excluded",
            "docs/agent-context/CONTEXT_INVENTORY.md": "excluded",
            "docs/improvements/backlog.md": "excluded",
            # The other type's deliverable tree stays excluded.
            "scripts/workflow_state.py": "excluded",
            ".claude/commands/milestone-plan.md": "excluded",
        }
        for path, expected in expected_impl.items():
            with self.subTest(path=path, stage="implementation"):
                self.assertEqual(
                    fingerprint.classify_path_implementation_stage(path, *classification),
                    expected,
                )
            with self.subTest(path=path, stage="plan"):
                # Never unclassified at the plan stage either -- the plan-stage
                # projection is recomputed by every later gate.
                self.assertIn(
                    fingerprint.classify_path(
                        path, plan_protected, plan_excluded_paths, plan_excluded_prefixes,
                    ),
                    ("protected", "excluded"),
                )
        # Self-protection is intact.
        self.assertEqual(
            fingerprint.classify_path_implementation_stage(
                item.artifacts_path, *classification,
            ),
            "protected",
        )
        # A genuinely novel path still fails closed -- the repair widened
        # the named sets, it did not exclude the repository.
        with self.assertRaises(fingerprint.UnclassifiedPathError):
            fingerprint.classify_path_implementation_stage(
                "vendor/thirdparty/blob.bin", *classification,
            )

        _, durability, proc = item.generate_impl_bundle(
            "implementation", expect_outcome="ordinary",
        )
        self.assertEqual(proc.returncode, 0)
        manifested = {
            entry["path"] for entry in item.impl_review_content_id(durability)[1][
                "review_content_manifest"
            ]
        }
        for path in ("docs/UX_FLOWS.md", "docs/DOMAIN_GLOSSARY.md",
                     "docs/adr/0004-new-decision.md", "build.gradle.kts",
                     item.deliverable):
            self.assertIn(path, manifested)
        self.assertNotIn("README.md", manifested)
        item.write_feedback("APPROVE")
        item.approve_implementation()
        item.prepare_functional_review()
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    def test_f4_declaration_repair_after_a_plan_approval_stales_and_must_be_re_approved(self):
        """Salvage audit `I9`: the artifact-declaration file's own bytes
        are plan-stage excluded, but each stage's `review_content_id`
        hashes that stage's classification *sets*, which are read out of
        that file. So editing `plan_stage.*` after an approval **does**
        stale it, and the repair has to go back through the plan gate.

        The contrary claim -- "editing the declaration does not stale the
        plan approval, because the declaration path is plan-stage
        excluded" -- was carried by two `workflow_fingerprint` docstrings
        and acted on in this repository's own history."""
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        approved = item.entry()["plan_approval"]["approved_review_content_id"]
        self.assertTrue(ws.approval_is_current(
            item.root, item.entry(), stage="plan", base_commit=item.base_commit,
        ))

        # A declaration repair that touches only `implementation_stage`
        # leaves the plan-stage identity alone.
        declarations = json.loads(self.scratch.read(item.artifacts_path))
        declarations["implementation_stage"]["excluded_prefixes"]["vendor/"] = (
            "third-party sources, not this item's deliverable"
        )
        self.scratch.write(item.artifacts_path, json.dumps(declarations, indent=2) + "\n")
        self.scratch.commit(f"chore({item.wid}): classify vendor/ at the implementation stage",
                            {"Workflow-Work-Item": item.wid},
                            paths=[item.artifacts_path])
        recomputed, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            item.root, item.wid,
        )
        self.assertEqual(recomputed, approved)
        self.assertTrue(ws.approval_is_current(
            item.root, item.entry(), stage="plan", base_commit=item.base_commit,
        ))

        # A repair that touches `plan_stage` moves the plan-stage identity,
        # even though every protected *file* is byte-identical.
        before_manifest = item.entry()["plan_approval"]["review_content_manifest"]
        declarations["plan_stage"]["excluded_prefixes"]["vendor/"] = (
            "third-party sources, not this item's plan content"
        )
        self.scratch.write(item.artifacts_path, json.dumps(declarations, indent=2) + "\n")
        self.scratch.commit(f"chore({item.wid}): classify vendor/ at the plan stage too",
                            {"Workflow-Work-Item": item.wid},
                            paths=[item.artifacts_path])
        recomputed, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            item.root, item.wid,
        )
        self.assertNotEqual(recomputed, approved)
        # ... and it is the classification set, not the content, that moved.
        self.assertEqual(projection["review_content_manifest"], before_manifest)
        self.assertIn("vendor/", projection["excluded_prefixes"])

        # The stored `status` field still says CURRENT -- it is a cache.
        # Recomputation is the authority, and every downstream gate uses it.
        self.assertEqual(item.entry()["plan_approval"]["status"], "CURRENT")
        self.assertFalse(ws.approval_is_current(
            item.root, item.entry(), stage="plan", base_commit=item.base_commit,
        ))
        self.assertFalse(ws.implementing_entry_reachable(
            item.root, item.entry(), item.base_commit,
        ))

        # Boundary, asserted rather than assumed: the registry-coverage
        # check is a *content* comparison against `plan_approval`'s own
        # per-path manifest, plus a read of the cached `status` field. A
        # classification-only edit changes neither, so it does not refuse
        # here -- unlike the committed registry edit row `B13` exercises,
        # which does. Ledger `O12` carries the standing cache-vs-recompute
        # contract; the gate that actually blocks this case is
        # `implementing_entry_reachable` above, which recomputes.
        self.assertEqual(
            ws.resolve_own_registry_completion_status(item.root, item.entry()),
            (False, "CP1"),  # non-terminal because CP1 is unimplemented, not refused
        )

        # The sanctioned repair: a fresh plan revision through the real
        # two-stage review and a fresh approval -- never a hand-edited
        # approval record and never a widened exclusion to hide the change.
        # Since workflow-2.6.0 a revision from `IMPLEMENTING` goes through
        # `/request-plan-amendment` (`D-Plan-Review-Bundle-Binding`'s
        # plan-stage allow-list): the 2.5.1 in-place re-publish is refused.
        with self.assertRaises(ws.PlanReviewPhaseNotPlanStageError):
            item.publish()
        item.request_amendment("declaration repair")
        item.amend_plan(2, plan_body="Plan body, unchanged in substance.\n")
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=2)
        item.approve_plan()
        self.assertTrue(ws.approval_is_current(
            item.root, item.entry(), stage="plan", base_commit=item.base_commit,
        ))
        self.assertNotEqual(
            item.entry()["plan_approval"]["approved_review_content_id"], approved,
        )
        item.implement_checkpoint("CP1", {item.deliverable: "// CP1\n"})
        _, _, proc = item.generate_impl_bundle("implementation", expect_outcome="ordinary")
        self.assertEqual(proc.returncode, 0)

    def test_f2_bootstrap_without_directories(self):
        """Ledger `O3`: a repository adopting the workflow has neither
        `docs/ai-workflow/registry/` nor `docs/ai-workflow/requirements/`
        yet, and `/milestone-plan` step 3 is what creates the artifacts
        that live there."""
        scratch = Scratch()
        self.addCleanup(scratch.cleanup)
        item = Item(scratch, "process")
        item.seed()
        self.assertFalse((scratch.root / "docs/ai-workflow/registry").exists())
        self.assertFalse((scratch.root / "docs/ai-workflow/requirements").exists())
        item.milestone_plan()
        self.assertTrue((scratch.root / item.registry_path).is_file())
        self.assertTrue((scratch.root / item.mapping_path).is_file())
        item.generate_plan_bundle()
        self.assertTrue((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())

    def test_f1_generated_declarations_are_usable_for_a_plan_touching_decisions(self):
        """Ledger `B5`: `/milestone-plan` step 5 directs the planner at
        `docs/TECHNICAL_DECISIONS.md`; recording a decision there is an
        ordinary planning act and must not make the plan bundle
        ungeneratable."""
        scratch = Scratch()
        self.addCleanup(scratch.cleanup)
        item = Item(scratch, "process")
        scratch.write("docs/TECHNICAL_DECISIONS.md", "# Technical decisions\n\n(none yet)\n")
        item.seed()
        item.milestone_plan()
        scratch.write(
            "docs/TECHNICAL_DECISIONS.md",
            "# Technical decisions\n\n| id | decision |\n|----|----------|\n| TD1 | use X |\n",
        )
        scratch.commit("docs: record decision TD1", {"Workflow-Work-Item": item.wid})
        item.generate_plan_bundle()
        self.assertTrue((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())


# ===========================================================================
# G. Replay of the live wedge
# ===========================================================================


class LiveWedgeReplay(MatrixCase):
    """Matrix row G1: the exact structural shape the live `workflow-v2-3-1`
    work item was left in at `79afd07` -- an ordinary round 2, a REVISE
    whose remediation touched only excluded content, a `same_content`
    republication pinning revision 2, and a stale on-disk bundle that no
    sanctioned command could regenerate."""

    work_item_type = "process"

    def test_g1_live_wedge_replay(self):
        item = self.item
        self.reach_first_bundle()

        # Round 2, ordinary: a real protected change.
        item.write_feedback("REVISE")
        item.enter_applying_feedback()
        self.scratch.write(item.deliverable, "// round 2, a real fix\n")
        self.scratch.commit(f"fix({item.wid}): round-1 finding", {"Workflow-Work-Item": item.wid})
        _, round2_record, _ = item.generate_impl_bundle("post-fix", expect_outcome="ordinary")
        self.assertEqual(item.entry()["implementation_revision"], 2)
        pinned_head = item.entry()["reviewed_implementation_head"]

        # Round 2's own REVISE, remediated entirely in excluded content --
        # the exact shape of 625249c/75394e4 in the real history.
        item.write_feedback("REVISE")
        item.enter_applying_feedback()
        self.scratch.write(item.excluded_note, "# ledger\n\nround-2 remediation recorded.\n")
        self.scratch.commit(f"docs({item.wid}): refresh the ledger (excluded-only)",
                            {"Workflow-Work-Item": item.wid})

        outcome, s2, proc = item.generate_impl_bundle("post-fix", expect_outcome="same_content")
        self.assertEqual(outcome, "same_content")
        self.assertEqual(proc.returncode, 0)
        entry = item.entry()
        self.assertEqual(entry["implementation_revision"], 2)
        self.assertEqual(entry["reviewed_implementation_head"], pinned_head)
        self.assertEqual(entry["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

        # The supersession chain's earliest member is round 2's own
        # ordinary record, exactly as 79afd07 supersedes 09e662d.
        trailers = subprocess.run(
            ["git", "show", "--no-patch", "--format=%(trailers)", s2],
            cwd=self.scratch.root, check=True, capture_output=True, text=True,
        ).stdout
        self.assertIn(f"Workflow-Supersedes: {round2_record}", trailers)
        self.assertIn(f"Workflow-Bundle-Generation-Record: {item.wid}/2", trailers)
        self.assertEqual(
            ws.verify_implementation_provenance_interval(item.root, entry, item.base_commit), s2,
        )
        fingerprint.assert_local_generation_matches(item.root, item.bundle_dir() / "MANIFEST.md")

        # And the round is approvable, which is the state the live item
        # could never reach.
        item.write_feedback("APPROVE")
        item.approve_implementation()
        self.assertEqual(item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(
            item.entry()["technical_approval"]["reviewed_content_commit"], pinned_head,
        )



# ===========================================================================
# H. Checkpoint ownership: interruption, resume and takeover refusal
# ===========================================================================


class CheckpointOwnership(MatrixCase):
    """Matrix rows D6-D9 -- `/milestone-implement`'s `[2.1 step 1]`
    ownership machinery, driven through its real primitives
    (`resolve_checkpoint_ownership`, `claim_checkpoint`, `owner_mutation`,
    `committed_checkpoint_status`, `release_checkpoint`)."""

    work_item_type = "process"

    def test_d6_interrupted_between_claim_and_state_write_continues(self):
        """A crash between publishing the claim and writing
        `IN_PROGRESS`: the next invocation must finish that acquisition,
        never re-acquire and never discard."""
        item = self.item
        self.reach_plan_approved()
        owner_token = item.implement_checkpoint(
            "CP1", {item.deliverable: "// partial\n"}, stop_after="claim",
        )
        self.assertIsNotNone(owner_token)
        self.assertIsNone(item.entry()["current_checkpoint_id"])
        claim = ws.resolve_claim(item.root, item.wid)
        self.assertEqual(claim["checkpoint_id"], "CP1")

        entry = item.entry()
        selected = ws.select_next_checkpoint(entry, item.registry())
        outcome, checkpoint_id, resumed_token = ws.resolve_checkpoint_ownership(
            item.root, entry, item.wid, selected, now=item.now(),
        )
        self.assertEqual(outcome, ws.CONTINUE_CLAIM)
        self.assertEqual(checkpoint_id, "CP1")
        self.assertEqual(resumed_token, owner_token)
        # And the round completes from there.
        item.implement_checkpoint("CP1", {item.deliverable: "// round 1\n"})
        self.assertEqual(item.entry()["checkpoints"]["CP1"]["status"], "COMPLETE")
        self.assertIsNone(ws.resolve_claim(item.root, item.wid))

    def test_d7_interrupted_between_commit_and_release_resumes(self):
        """The design's single automatic release: a self-owned claim on a
        checkpoint whose completion is already durable at HEAD."""
        item = self.item
        item.milestone_plan(checkpoints=CHECKPOINTS_TWO, requirements=REQUIREMENTS_TWO)
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        item.implement_checkpoint(
            "CP1", {item.deliverable: "// CP1\n"}, stop_after="commit-before-release",
        )
        claim = ws.resolve_claim(item.root, item.wid)
        self.assertEqual(claim["checkpoint_id"], "CP1")
        self.assertEqual(
            ws.committed_checkpoint_status(item.root, item.wid, "CP1"), "COMPLETE",
        )
        entry = item.entry()
        selected = ws.select_next_checkpoint(entry, item.registry())
        self.assertEqual(selected, "CP2")
        outcome, checkpoint_id, owner_token = ws.resolve_checkpoint_ownership(
            item.root, entry, item.wid, selected, now=item.now(),
        )
        self.assertEqual(outcome, ws.FRESH)
        self.assertEqual(checkpoint_id, "CP2")
        self.assertIsNone(owner_token)
        self.assertIsNone(ws.resolve_claim(item.root, item.wid))

    def test_d8_dirty_resume_without_a_worktree_identity_refuses(self):
        """An `IN_PROGRESS` checkpoint's uncommitted work is
        worktree-local, so resuming it requires this worktree's own
        identity record."""
        item = self.item
        self.reach_plan_approved()
        item.implement_checkpoint(
            "CP1", {item.deliverable: "// partial\n"}, stop_after="edit",
        )
        self.assertEqual(item.entry()["current_checkpoint_id"], "CP1")
        identity = item.root / ws.WORKTREE_IDENTITY_PATH
        self.assertTrue(identity.is_file())
        identity.unlink()
        entry = item.entry()
        with self.assertRaises(ws.WorktreeIdentityMissingError) as caught:
            ws.resolve_checkpoint_ownership(
                item.root, entry, item.wid, "CP1", now=item.now(),
            )
        self.assertIn("claim", caught.exception.ownership_evidence)

    def test_d9_foreign_claim_refuses_without_an_explicit_takeover(self):
        item = self.item
        self.reach_plan_approved()
        item.implement_checkpoint(
            "CP1", {item.deliverable: "// partial\n"}, stop_after="edit",
        )
        claim_path = ws.claim_path(item.root, item.wid)
        claim = json.loads(claim_path.read_text())
        claim["worktree_root"] = "/somewhere/else"
        claim_path.write_text(json.dumps(claim, indent=2) + "\n")
        entry = item.entry()
        with self.assertRaises(ws.CheckpointOwnedByOtherWorktreeError) as caught:
            ws.resolve_checkpoint_ownership(
                item.root, entry, item.wid, "CP1", now=item.now(),
            )
        evidence = caught.exception.ownership_evidence
        self.assertEqual(evidence["claim"]["worktree_root"], "/somewhere/else")
        self.assertIn("escape", evidence)
        # The evidence names a real, executable escape.
        takeover = ws.takeover_evidence(item.root, item.wid)
        literal = ws.takeover_authorization_literal(item.wid, takeover, "CP1")
        self.assertIn(item.wid, literal)
        self.assertIn("CP1", literal)


# ===========================================================================
# I. First-attempt bundle-directory resolution (convergence repair `I1`/`I2`)
# ===========================================================================


class FirstAttemptPlanBundleResolution(unittest.TestCase):
    """Convergence repair `I1`: a work item's **first** plan bundle must
    succeed on its first attempt.

    Before the repair it could not. `.claude/commands/milestone-plan.md`
    step 6 authors `<bundle_dir>/REVIEW_REQUEST.md`/`TEST_RESULTS.md`/
    `CONTEXT_FILES.txt` and only *then* runs
    `scripts/prepare-ai-review.sh <base> plan <id>`. `<bundle_dir>` is
    `workflow_fingerprint.resolve_bundle_dir`'s answer, and that answer
    was gated on existence: with nothing under `.ai-review/<id>/` on disk
    yet it was the flat `.ai-review/current/`, while the generator wrote
    and validated the scoped directory. The first attempt therefore died
    in `assert_review_request_states_review_content_id` against a
    `REVIEW_REQUEST.md` that had been authored somewhere else, and a
    second, identical attempt succeeded only because the failed first one
    had created the scoped directory the resolver then preferred.

    Each scenario below drives the real resolver and the real generation
    script, invokes `prepare-ai-review.sh` exactly **once**, and asserts
    that one invocation succeeded -- a second attempt is never made, so a
    regression cannot hide behind one.
    """

    def author_plan_bundle_and_generate(self, scratch, item):
        """`/milestone-plan` step 6, verbatim: resolve, author, generate,
        bind (workflow-2.6.0: the author inputs go to `<plan_inputs_dir>`,
        and the bind straight after the generator writes the ready phase)."""
        bundle_rel = fingerprint.resolve_bundle_dir(scratch.root, item.wid, stage="plan")
        self.assertEqual(bundle_rel, Path(".ai-review") / item.wid / "current")
        bundle = scratch.root / bundle_rel
        inputs = scratch.root / fingerprint.resolve_plan_review_inputs_dir(scratch.root, item.wid)
        digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            scratch.root, item.wid,
        )
        metadata = fingerprint.resolve_plan_stage_metadata(scratch.root, item.wid)
        inputs.mkdir(parents=True, exist_ok=True)
        (inputs / "REVIEW_REQUEST.md").write_text(
            f"# Review request\n\nstage: plan\nwork item: {item.wid}\n"
            f"review_content_id: {digest}\n"
        )
        (inputs / "TEST_RESULTS.md").write_text(
            f"stage: plan (revision {metadata.plan_revision})\nhead: {scratch.head()}\n\n"
            "No automated checks are required at the plan stage.\n"
        )
        (inputs / "CONTEXT_FILES.txt").write_text("")
        proc = scratch.prepare(item.base_commit, "plan", item.wid, check=False)
        self.assertEqual(
            proc.returncode, 0,
            f"first attempt failed:\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}",
        )
        self.assertTrue((bundle / "MANIFEST.md").is_file())
        if item.entry()["phase"] in ws.PLAN_REVIEW_NON_READY_PHASES:
            item.bind_plan_bundle()
        self.assertTrue(
            (scratch.root / ".ai-review" / item.wid / "review-bundle.tar.gz").is_file()
        )
        # Nothing was ever authored at the flat compatibility path.
        self.assertFalse((scratch.root / ".ai-review" / "current").exists())
        return proc

    def new_item(self, work_item_type):
        scratch = Scratch(name="wf-first-attempt")
        self.addCleanup(scratch.cleanup)
        item = Item(scratch, work_item_type)
        item.seed()
        item.milestone_plan()
        return scratch, item

    def test_first_plan_bundle_process_item_succeeds_first_attempt(self):
        scratch, item = self.new_item("process")
        self.assertFalse((scratch.root / ".ai-review" / item.wid).exists())
        self.author_plan_bundle_and_generate(scratch, item)

    def test_first_plan_bundle_product_item_succeeds_first_attempt(self):
        scratch, item = self.new_item("product")
        self.assertFalse((scratch.root / ".ai-review" / item.wid).exists())
        self.author_plan_bundle_and_generate(scratch, item)

    def test_regeneration_after_withdrawal_succeeds_first_attempt(self):
        """`withdraw_bundle` renames `current/` away, so the round after a
        quarantine was the second place the same split appeared."""
        scratch, item = self.new_item("process")
        self.author_plan_bundle_and_generate(scratch, item)
        quarantine = fingerprint.withdraw_bundle(scratch.root, item.wid, "matrix scenario")
        self.assertTrue(Path(quarantine).is_dir())
        self.assertFalse((scratch.root / ".ai-review" / item.wid / "current").exists())
        self.author_plan_bundle_and_generate(scratch, item)

    def test_first_plan_bundle_for_a_remediation_child_succeeds_first_attempt(self):
        """A `<parent-id>-remediation-<n>` child is a work item of its own
        with its own `.ai-review/<child-id>/` layout, so its first
        `/milestone-plan <child-id>` hits the identical first-generation
        case -- while the *parent's* scoped directory sits right beside it,
        already created, resolving nothing for the child."""
        scratch, parent = self.new_item("product")
        self.author_plan_bundle_and_generate(scratch, parent)
        config = json.loads(scratch.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        holder = {}

        def create(state):
            new_state, child_id = ws.create_remediation_child_work_item(
                state, config, parent_work_item_id=parent.wid,
                plan_path=f"docs/milestones/{parent.wid}-remediation-1-execution.md",
                registry_path=(
                    f"docs/ai-workflow/registry/{parent.wid}-remediation-1-registry.json"
                ),
                base_commit=scratch.head(), now=parent.now(),
            )
            holder["id"] = child_id
            return new_state

        parent.tx(create)
        child_id = holder["id"]
        scratch.commit(f"chore({parent.wid}): defer to {child_id}",
                       {"Workflow-Work-Item": parent.wid},
                       paths=["docs/ai-workflow/WORKFLOW_STATE.json"])
        created = parent.state()["work_items"][child_id]

        child = Item(scratch, parent.wtype)
        child.wid = child_id
        child.plan_path = created["plan_path"]
        child.registry_path = created["registry_path"]
        child.mapping_path = f"docs/ai-workflow/requirements/{child_id}-mapping.json"
        child.artifacts_path = f"docs/ai-workflow/registry/{child_id}-artifacts.json"
        child.deliverable = parent.deliverable
        child.excluded_note = f"docs/ai-workflow/requirements/{child_id}-ledger.md"
        child.base_commit = created["base_commit"]
        child.milestone_plan()

        self.assertTrue((scratch.root / ".ai-review" / parent.wid / "current").is_dir())
        self.assertFalse((scratch.root / ".ai-review" / child_id).exists())
        self.author_plan_bundle_and_generate(scratch, child)
        # The parent's own published bundle is untouched by the child's.
        self.assertTrue(
            (scratch.root / ".ai-review" / parent.wid / "current" / "MANIFEST.md").is_file()
        )

    def test_a_flat_compatibility_generation_succeeds_first_attempt(self):
        """Convergence pass 11, Optional 3. The row above proves what
        `resolve_bundle_dir` *answers* for the omitted-id form; nothing
        proved that answer is a usable generation target. It is the
        authoring half and the generating half agreeing that makes the
        compatibility rule a contract rather than a claim, so this row
        resolves `<bundle_dir>` through the production resolver, authors
        the bundle inputs there, runs `prepare-ai-review.sh` with the
        `[work-item-id]` argument **omitted**, and requires that one
        invocation to publish -- for every stage whose id argument is
        optional.

        Nothing here widens the compatibility semantics: the flat form
        writes no `MANIFEST.md` at the implementation/post-fix stages
        (`prepare-ai-review.sh` writes one only in its `-n "$WORK_ITEM_ID"`
        branch, the fact `/review-implementation` step 3 already reports
        as a clean refusal), so no finalization runs and no scoped
        directory is created."""
        scratch, item = self.new_item("process")
        self.assertFalse((scratch.root / ".ai-review" / item.wid).exists())
        for stage in ("implementation", "post-fix", "functional-review"):
            with self.subTest(stage=stage):
                bundle_rel = fingerprint.resolve_bundle_dir(scratch.root, item.wid, stage=stage)
                self.assertEqual(bundle_rel, Path(".ai-review/current"))
                bundle = scratch.root / bundle_rel
                bundle.mkdir(parents=True, exist_ok=True)
                (bundle / "REVIEW_REQUEST.md").write_text(
                    f"# Review request\n\nstage: {stage}\nwork item: {item.wid}\n"
                )
                (bundle / "IMPLEMENTATION_SUMMARY.md").write_text("Ad-hoc, untracked round.\n")
                (bundle / "TEST_RESULTS.md").write_text("ran: nothing that needs a counter\n")
                (bundle / "CONTEXT_FILES.txt").write_text("")
                proc = scratch.prepare(item.base_commit, stage, work_item_id=None, check=False)
                self.assertEqual(
                    proc.returncode, 0,
                    f"flat {stage} generation failed:\n--- stdout ---\n{proc.stdout}\n"
                    f"--- stderr ---\n{proc.stderr}",
                )
                self.assertIn("(none -- flat compatibility layout)", proc.stdout)
                # Published where the resolver said, with the generated
                # artifacts beside the authored ones.
                for generated in ("CHANGED_FILES.txt", "COMMITS.txt", "DIFF.patch"):
                    self.assertTrue((bundle / generated).is_file(), generated)
                self.assertTrue((scratch.root / ".ai-review" / "review-bundle.tar.gz").is_file())
                self.assertEqual(
                    (bundle / "REVIEW_REQUEST.md").read_text().splitlines()[2], f"stage: {stage}",
                )
                # The omitted-id form never creates the scoped layout, so
                # the resolver's answer is stable across the whole loop.
                self.assertFalse((scratch.root / ".ai-review" / item.wid).exists())

    def test_the_given_id_split_on_a_fresh_tree_is_fail_closed_and_self_healing(self):
        """Convergence pass 12, ledger `O32` (external Optional `N4`),
        executed rather than reasoned about.

        The implementation/post-fix stages keep a narrower version of the
        plan stage's authoring/generation split, because their
        `work-item-id` argument is optional and the resolver's existence
        gate is what makes the omitted-id compatibility form work at all.
        An operator who *passes* the optional id while
        `.ai-review/<work_item_id>/` does not exist authors flat while the
        generator writes scoped. Past the plan approval that needs the
        gitignored, documented-disposable `.ai-review/` tree to have been
        deleted between rounds -- `withdraw_bundle`'s quarantine does not
        cause it, since the gate keys on the work item's own root
        directory.

        This row pins the four properties that make it an accepted
        Optional rather than a defect to close: the failure is refusal not
        withdrawal, nothing is published, the flat bundle the author wrote
        survives untouched, and an identical second attempt -- the same
        command text, re-followed, with no extra step -- succeeds."""
        scratch, item = self.new_item("process")
        self.author_plan_bundle_and_generate(scratch, item)
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        item.implement_checkpoint("CP1", {item.deliverable: "// round 1\n"})

        # The operator cleans the disposable, gitignored bundle tree.
        shutil.rmtree(scratch.root / ".ai-review")
        self.assertEqual(
            fingerprint.resolve_bundle_dir(scratch.root, item.wid),
            Path(".ai-review/current"),
        )

        # `/milestone-implement` step 4's own state writes, so the round
        # identity the generator preflights is genuinely in place and the
        # refusal below is the split's, not a missing precondition's.
        item.milestone_implement_wrap_up()
        head = scratch.head()
        outcome, _ = ws.resolve_bundle_generation_outcome(
            scratch.root, item.entry(), base_commit=item.base_commit, head=head,
        )
        item.tx(lambda state: ws.record_bundle_generation(
            state, item.wid, stage="implementation", head=head, now=item.now(),
            outcome=outcome,
        ))
        revision = item.entry()["implementation_revision"]
        durability = scratch.commit(
            f"chore({item.wid}): record bundle generation, revision {revision}",
            {"Workflow-Bundle-Generation-Record": f"{item.wid}/{revision}",
             "Workflow-Work-Item": item.wid},
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        )

        def author_at_the_resolvers_answer():
            bundle_rel = fingerprint.resolve_bundle_dir(scratch.root, item.wid)
            bundle = scratch.root / bundle_rel
            bundle.mkdir(parents=True, exist_ok=True)
            digest, _ = item.impl_review_content_id(durability)
            (bundle / "REVIEW_REQUEST.md").write_text(
                f"# Review request\n\nstage: implementation\nwork item: {item.wid}\n"
                f"review_content_id: {digest}\n"
            )
            (bundle / "IMPLEMENTATION_SUMMARY.md").write_text(
                f"implementation_revision: {revision}\n"
            )
            (bundle / "TEST_RESULTS.md").write_text("ran: the narrow checks\n")
            (bundle / "CONTEXT_FILES.txt").write_text("")
            return bundle_rel

        flat_bundle_rel = author_at_the_resolvers_answer()
        self.assertEqual(flat_bundle_rel, Path(".ai-review/current"))
        first = scratch.prepare(item.base_commit, "implementation", item.wid, check=False)

        root_dir = scratch.root / ".ai-review" / item.wid
        self.assertNotEqual(first.returncode, 0)
        # Refusal, not withdrawal: the error names the scoped
        # REVIEW_REQUEST.md the author never wrote to.
        self.assertIn("MissingReviewContentIdStatementError", first.stderr)
        self.assertIn(f".ai-review/{item.wid}/current/REVIEW_REQUEST.md", first.stderr)
        self.assertFalse((root_dir / "current" / "MANIFEST.md").exists())
        self.assertFalse((root_dir / "review-bundle.tar.gz").exists())
        self.assertEqual(list(root_dir.glob("current.rejected-*")), [])
        self.assertFalse((root_dir / "REJECTED").exists())
        fingerprint.assert_bundle_not_rejected(scratch.root, item.wid)
        # And what the author did write is still exactly where they wrote it.
        self.assertTrue(
            (scratch.root / ".ai-review" / "current" / "IMPLEMENTATION_SUMMARY.md").is_file()
        )

        # Self-healing: the same command text, followed again unchanged.
        scoped_bundle_rel = author_at_the_resolvers_answer()
        self.assertEqual(scoped_bundle_rel, Path(".ai-review") / item.wid / "current")
        second = scratch.prepare(item.base_commit, "implementation", item.wid, check=False)
        self.assertEqual(
            second.returncode, 0,
            f"second attempt failed:\n--- stdout ---\n{second.stdout}\n"
            f"--- stderr ---\n{second.stderr}",
        )
        self.assertTrue((root_dir / "current" / "MANIFEST.md").is_file())
        self.assertTrue((root_dir / "review-bundle.tar.gz").is_file())

    def test_non_plan_stages_keep_the_flat_compatibility_rule(self):
        """The repair is stage-aware on purpose. The three stages whose
        `work-item-id` argument to `prepare-ai-review.sh` is optional keep
        the compatibility rule, because omitting that argument really does
        generate `.ai-review/current/` -- making them scoped by
        construction would break that documented invocation the
        mirror-image way."""
        scratch, item = self.new_item("process")
        self.assertFalse((scratch.root / ".ai-review" / item.wid).exists())
        for stage in ("implementation", "post-fix", "functional-review", None):
            with self.subTest(stage=stage):
                self.assertEqual(
                    fingerprint.resolve_bundle_dir(scratch.root, item.wid, stage=stage),
                    Path(".ai-review/current"),
                )
        # Once the scoped layout exists, every stage prefers it -- and goes
        # on preferring it while `current/` itself is quarantined.
        self.author_plan_bundle_and_generate(scratch, item)
        fingerprint.withdraw_bundle(scratch.root, item.wid, "matrix scenario")
        for stage in (None, "implementation", "post-fix", "functional-review"):
            with self.subTest(stage=stage, withdrawn=True):
                self.assertEqual(
                    fingerprint.resolve_bundle_dir(scratch.root, item.wid, stage=stage),
                    Path(".ai-review") / item.wid / "current",
                )


# ===========================================================================
# J. Cross-stage bundle-directory reuse and the manifest-producer gate
#    (convergence pass 12, ledger `I20`)
# ===========================================================================


class CrossStageGenerationReuse(MatrixCase):
    """Ledger `I20`, the defect and its whole neighbourhood.

    `prepare-ai-review.sh` gated its closing `finalize_bundle_generation`
    call on `-f "$BUNDLE_DIR/MANIFEST.md"` -- a *filesystem* test -- while
    two of its four stages write no manifest at all (`functional-review`
    at either layout, and `implementation`/`post-fix` with the
    work-item-id omitted). `$BUNDLE_DIR` is reused across stages by
    design, so a `functional-review` generation for a work item whose
    plan- or implementation-stage round had already published there found
    that round's `MANIFEST.md` still sitting in the directory and ran the
    three-way `bundle_id` check against it.

    That check could only ever fail: `CHANGED_FILES.txt` alone carries a
    `stage:` line this run has just rewritten, so the recomputed
    `bundle_id` disagrees with the recorded one by construction. The
    failure path is `withdraw_bundle`, so a valid, published, already-
    approved bundle was quarantined and its archive deleted by a
    generation that never claimed the bundle's identity -- with
    `WORKFLOW_STATE.json` untouched (the script is not a state writer),
    `technical_approval` left `CURRENT` over a bundle that no longer
    existed, and the completed withdrawal removing its own `REJECTED`
    marker on the way out, leaving `assert_bundle_not_rejected` nothing to
    see.

    The rows below drive the real generator as a subprocess, at every
    published-stage/consuming-stage pairing the directory can actually
    reach."""

    def published_snapshot(self):
        """Everything a withdrawal would change, read before and after."""
        item = self.item
        root_dir = item.root / ".ai-review" / item.wid
        bundle = root_dir / "current"
        return {
            "current_exists": bundle.is_dir(),
            "manifest": (bundle / "MANIFEST.md").read_text()
            if (bundle / "MANIFEST.md").is_file() else None,
            "archive_exists": (root_dir / "review-bundle.tar.gz").is_file(),
            "quarantines": sorted(p.name for p in root_dir.glob("current.rejected-*")),
            "marker_exists": (root_dir / "REJECTED").exists(),
            "state": item.sim.read("docs/ai-workflow/WORKFLOW_STATE.json"),
            "entry": item.entry(),
        }

    def assert_functional_review_preserved_the_bundle(self, before, proc):
        """Requirements 1-6 of the `I20` repair contract, in one place."""
        item = self.item
        root_dir = item.root / ".ai-review" / item.wid
        bundle = root_dir / "current"
        after = self.published_snapshot()

        self.assertEqual(
            proc.returncode, 0,
            f"functional-review generation failed:\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}",
        )
        # 1/2. Not withdrawn, not quarantined.
        self.assertTrue(after["current_exists"])
        self.assertEqual(after["quarantines"], [])
        self.assertFalse(after["marker_exists"])
        self.assertNotIn("withdrawn", proc.stdout + proc.stderr)
        # 3. The archive is still there, and is this run's own.
        self.assertTrue(after["archive_exists"])
        # 4/5. Nothing in state moved -- including any approval it carries.
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["entry"], before["entry"])
        # 6. And the functional-review outputs really were generated.
        header = (bundle / "CHANGED_FILES.txt").read_text()
        self.assertIn("stage: functional-review\n", header)
        self.assertIn(f"work_item_id: {item.wid}\n", header)
        for generated in ("COMMITS.txt", "DIFF.patch"):
            self.assertTrue((bundle / generated).is_file(), generated)
        with tempfile.TemporaryDirectory() as tmp:
            import tarfile
            with tarfile.open(root_dir / "review-bundle.tar.gz") as tf:
                tf.extractall(tmp)
            archived = (Path(tmp) / "current" / "CHANGED_FILES.txt").read_text()
        self.assertIn("stage: functional-review\n", archived)
        # The previous round's MANIFEST.md is left exactly as it was --
        # never deleted to suppress the check, never rewritten by a stage
        # that does not own it.
        self.assertEqual(after["manifest"], before["manifest"])
        return after

    # --- J1: an implementation-published, technically-approved bundle ---

    def test_j1_functional_review_over_a_published_implementation_bundle(self):
        self.reach_technical_approved()
        item = self.item
        before = self.published_snapshot()
        self.assertTrue(before["current_exists"])
        self.assertIsNotNone(before["manifest"])
        self.assertTrue(before["archive_exists"])
        self.assertEqual(before["entry"]["technical_approval"]["status"], "CURRENT")

        proc = item.sim.prepare(item.base_commit, "functional-review", item.wid, check=False)
        after = self.assert_functional_review_preserved_the_bundle(before, proc)
        self.assertEqual(after["entry"]["technical_approval"]["status"], "CURRENT")
        # And the approval's own bundle binding is untouched.
        self.assertEqual(
            after["entry"]["technical_approval"]["reviewed_bundle_id"],
            before["entry"]["technical_approval"]["reviewed_bundle_id"],
        )
        # The item is still acceptable end to end -- the published round
        # was not merely left on disk, it is still usable.
        fingerprint.assert_bundle_not_rejected(item.root, item.wid)
        item.prepare_functional_review()
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    # --- J2: a plan-published bundle, before any implementation exists ---

    def test_j2_functional_review_over_a_published_plan_bundle(self):
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        before = self.published_snapshot()
        self.assertIn("stage: plan", before["manifest"])

        proc = item.sim.prepare(item.base_commit, "functional-review", item.wid, check=False)
        self.assert_functional_review_preserved_the_bundle(before, proc)
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

    # --- J3: a post-fix-published bundle ---

    def test_j3_functional_review_over_a_published_post_fix_bundle(self):
        self.reach_technical_approved()
        item = self.item
        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: the label is wrong.\n")
        item.mark_technical_approval_stale()
        self.scratch.write(item.deliverable, "// functional fix\n")
        self.scratch.commit(f"fix({item.wid}): functional finding 1",
                            {"Workflow-Work-Item": item.wid})
        item.generate_impl_bundle("post-fix", expect_outcome="ordinary")
        before = self.published_snapshot()
        self.assertEqual(item.entry()["implementation_revision"], 2)

        proc = item.sim.prepare(item.base_commit, "functional-review", item.wid, check=False)
        self.assert_functional_review_preserved_the_bundle(before, proc)
        self.assertEqual(item.entry()["implementation_revision"], 2)

    # --- J4: the reverse direction -- a genuine generation after one ---

    def test_j4_a_genuine_generation_after_a_functional_review_run_still_finalizes(self):
        """No stale filesystem artifact from the `functional-review` run
        decides anything for the *next* invocation either: a real
        post-fix round writes its own manifest, finalizes it, and the
        published identity is this round's, not the one left behind."""
        self.reach_technical_approved()
        item = self.item
        bundle = item.root / ".ai-review" / item.wid / "current"

        proc = item.sim.prepare(item.base_commit, "functional-review", item.wid, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        inherited_bundle_id = fingerprint.read_manifest_identifiers(
            bundle / "MANIFEST.md"
        )["bundle_id"]
        # The inherited manifest does not describe the directory any more.
        ondisk, _ = fingerprint.compute_bundle_id(bundle)
        self.assertNotEqual(inherited_bundle_id, ondisk)

        item.prepare_functional_review()
        item.write_functional_findings("Finding 1: the label is wrong.\n")
        item.mark_technical_approval_stale()
        self.scratch.write(item.deliverable, "// functional fix\n")
        self.scratch.commit(f"fix({item.wid}): functional finding 1",
                            {"Workflow-Work-Item": item.wid})
        outcome, _, proc2 = item.generate_impl_bundle("post-fix", expect_outcome="ordinary")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)

        # Finalization ran and agreed: the manifest's own value, a fresh
        # recomputation over current/, and the archive's own extracted
        # content are one identity again.
        recorded = fingerprint.read_manifest_identifiers(bundle / "MANIFEST.md")["bundle_id"]
        self.assertNotEqual(recorded, inherited_bundle_id)
        ondisk, _ = fingerprint.compute_bundle_id(bundle)
        self.assertEqual(recorded, ondisk)
        with tempfile.TemporaryDirectory() as tmp:
            import tarfile
            with tarfile.open(
                item.root / ".ai-review" / item.wid / "review-bundle.tar.gz"
            ) as tf:
                tf.extractall(tmp)
            extracted, _ = fingerprint.compute_bundle_id(Path(tmp) / "current")
        self.assertEqual(recorded, extracted)

    # --- J5: finalization is not weakened where it really applies ---

    def test_j5_a_stale_marker_line_at_a_real_generation_stage_still_withdraws(self):
        """The control the repair must not break. A `post-fix` round
        whose `IMPLEMENTATION_SUMMARY.md` still states the previous
        round's counter is a *genuine* producer-side staleness, at a
        stage that really does write a manifest -- so it still fails
        closed exactly as before: withdrawn, quarantined, archive gone."""
        self.reach_technical_approved()
        item = self.item
        root_dir = item.root / ".ai-review" / item.wid
        item.mark_technical_approval_stale()
        self.scratch.write(item.deliverable, "// functional fix\n")
        self.scratch.commit(f"fix({item.wid}): functional finding 1",
                            {"Workflow-Work-Item": item.wid})
        # Revision 1's line, carried into revision 2's round.
        outcome, _, proc = item.generate_impl_bundle(
            "post-fix", expect_outcome="ordinary", check=False,
            summary="implementation_revision: 1\n\nStale on purpose.\n",
        )
        self.assertEqual(item.entry()["implementation_revision"], 2)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("withdrawn", proc.stderr)
        self.assertFalse((root_dir / "current").exists())
        self.assertEqual(len(list(root_dir.glob("current.rejected-*"))), 1)
        self.assertFalse((root_dir / "review-bundle.tar.gz").exists())

    # --- J6: and a functional-review run cannot manufacture a withdrawal ---

    def test_j6_functional_review_never_withdraws_even_with_a_stale_marker_line(self):
        """The mirror of `J5`. The same stale `IMPLEMENTATION_SUMMARY.md`
        line, at the stage that writes no manifest, must withdraw
        nothing: this run has no identity of its own to fail, so it has
        no standing to quarantine the previous round's artifact."""
        self.reach_technical_approved()
        item = self.item
        bundle = item.root / ".ai-review" / item.wid / "current"
        (bundle / "IMPLEMENTATION_SUMMARY.md").write_text(
            "implementation_revision: 99\n\nDeliberately wrong.\n"
        )
        before = self.published_snapshot()
        proc = item.sim.prepare(item.base_commit, "functional-review", item.wid, check=False)
        self.assert_functional_review_preserved_the_bundle(before, proc)

    # --- J7: the inherited manifest is reported, not silently carried ---

    def test_j7_a_non_manifest_stage_reports_the_inherited_manifest(self):
        """The residual this repair deliberately leaves in place is
        *stated*: the previous round's `MANIFEST.md` stays (deleting it
        would destroy the published round's identity record and would be
        the same filesystem-state reasoning inverted), so the run says
        that its `bundle_id` no longer describes the directory."""
        self.reach_technical_approved()
        item = self.item
        proc = item.sim.prepare(item.base_commit, "functional-review", item.wid, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("writes no MANIFEST.md", proc.stderr)
        self.assertIn("belongs", proc.stderr)
        self.assertIn("previous round", proc.stderr)

    # --- J8: and the approval gate stays fail-closed over that residual ---

    def test_j8_approval_is_fail_closed_over_an_inherited_manifest(self):
        """Why the residual is non-gating rather than merely tolerated.
        Every approval path recomputes `bundle_id` over the bundle rather
        than reading the manifest's declared value, so feedback naming
        the pre-regeneration identity no longer matches and
        `assert_feedback_matches_bundle` refuses -- the approval cannot
        be taken on the strength of a manifest that stopped describing
        the directory."""
        self.reach_first_bundle()
        item = self.item
        approved_bundle_id = item.bundle_id()
        item.write_feedback("APPROVE", bundle_id=approved_bundle_id)

        proc = item.sim.prepare(item.base_commit, "functional-review", item.wid, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        # The manifest still declares the old identity...
        self.assertEqual(item.bundle_id(), approved_bundle_id)
        # ...but the recomputation every consumer actually performs does not.
        recomputed, _ = fingerprint.compute_bundle_id(item.bundle_dir())
        self.assertNotEqual(recomputed, approved_bundle_id)
        with self.assertRaises(fingerprint.FeedbackBundleMismatchError):
            fingerprint.assert_feedback_matches_bundle(
                item.feedback_fields(), bundle_id=recomputed,
                base_commit=item.base_commit, work_item_id=item.wid,
            )


class UserOverrideApprovalBasis(MatrixCase):
    """Convergence pass 12, ledger `O35`.

    `resolve_approval_basis` has two outcomes, and until this pass the
    string `USER_OVERRIDE` appeared nowhere in this suite. `Item.
    approve_implementation` called `assert_feedback_matches_bundle`
    unconditionally -- a step `/approve-review` step 2 explicitly does
    *not* instruct ("not fatal to reading the file (an `EXTERNAL_APPROVE`
    basis simply becomes unreachable, per step 3), but report the mismatch
    naming both values") -- so every row that reached an implementation
    approval necessarily had matching feedback, and the override half of
    D2's basis decision was unexecuted end to end.

    It is not a hypothetical branch: `/recover-implementation-provenance`
    step 7 names it as the interim remedy after a recovery necessarily
    changes `bundle_id`, and `/apply-implementation-review`'s stale-feedback
    rounds land in the same place. These rows drive it through the
    production functions the command names."""

    def test_stale_feedback_resolves_user_override_and_still_approves(self):
        """Feedback that says `APPROVE` but names a bundle that is no
        longer current: the binding mismatch is observed and reported, the
        basis degrades, and the approval still completes on the user's own
        confirmation -- which is the whole point of the second basis."""
        item = self.item
        self.reach_first_bundle()
        item.write_feedback("APPROVE", bundle_id="0" * 64)

        commit = item.approve_implementation(expect_basis="USER_OVERRIDE")
        self.assertIsNotNone(item.last_feedback_binding_mismatch)
        entry = item.entry()
        self.assertEqual(entry["technical_approval"]["basis"], "USER_OVERRIDE")
        self.assertEqual(entry["technical_approval"]["status"], "CURRENT")
        self.assertEqual(entry["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        ws.validate_technical_approval_commit(item.root, commit, item.wid)
        # And the round is still acceptable end to end.
        item.prepare_functional_review()
        item.accept_milestone()
        self.assertEqual(item.entry()["phase"], "MILESTONE_COMPLETE")

    def test_a_revise_verdict_also_resolves_user_override(self):
        """The other documented route to the same basis: the reviewed
        round's status is not `APPROVE` at all."""
        item = self.item
        self.reach_first_bundle()
        item.write_feedback("REVISE")
        item.approve_implementation(expect_basis="USER_OVERRIDE")
        self.assertEqual(item.entry()["technical_approval"]["basis"], "USER_OVERRIDE")

    def test_matching_feedback_still_resolves_external_approve(self):
        """The control: relaxing the helper's own extra assertion did not
        relax the basis decision. Matching feedback still earns the
        stronger basis, and no mismatch is reported."""
        item = self.item
        self.reach_first_bundle()
        item.write_feedback("APPROVE")
        item.approve_implementation(expect_basis="EXTERNAL_APPROVE")
        self.assertIsNone(item.last_feedback_binding_mismatch)
        self.assertEqual(item.entry()["technical_approval"]["basis"], "EXTERNAL_APPROVE")

    def test_a_block_verdict_reaches_neither_basis(self):
        """And the refusal the two bases share is unaffected. `BLOCK` is
        enforced at *both* points, and the gate is the one that fires:
        `technical_approval_gate_reachable` refuses the literal `BLOCK`
        round, and refuses again on `pinned_block` alone once D2a's
        laundering path has rewritten the mutable file to a status the
        gate would otherwise admit. `resolve_approval_basis`'s own refusal
        is the independent second layer -- it is never reached through the
        command's own ordering, which is exactly why it is asserted here
        directly rather than inferred from the gate's behaviour."""
        item = self.item
        self.reach_first_bundle()
        item.write_feedback("BLOCK")
        bundle_id = item.bundle_id()
        review_content_id, _ = item.impl_review_content_id(item.sim.head())

        # First layer, literal BLOCK.
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status="BLOCK", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True, pinned_block=False,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

        # D2a's laundering path: pin the BLOCK, then rewrite the mutable
        # file to REVISE with every binding field unchanged.
        item.tx(lambda state: ws.record_technical_review_block_pin(
            state, item.wid, bundle_id=bundle_id,
            review_content_id=review_content_id, now=item.now(),
        ))
        item.write_feedback("REVISE")
        self.assertTrue(ws.is_technical_review_block_pinned(item.entry(), bundle_id))
        # The gate still refuses -- on the pin alone, not on the status.
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status="REVISE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True, pinned_block=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))
        with self.assertRaises(AssertionError) as ctx:
            item.approve_implementation(expect_basis=None)
        self.assertIn("pinned=True", str(ctx.exception))

        # Second layer, independent of the gate: were the gate ever
        # bypassed, the basis decision refuses on the same pin.
        with self.assertRaises(ws.BlockCannotApproveError):
            ws.resolve_approval_basis(
                latest_round_status="REVISE",
                feedback_bundle_id=bundle_id, current_bundle_id=bundle_id,
                user_confirmation=f"implementation {item.wid}",
                work_item_id=item.wid, stage="implementation", pinned_block=True,
            )
        # Nothing was recorded by either refusal.
        self.assertIsNone(item.entry().get("technical_approval"))

class UserOverrideApprovalBasisProduct(UserOverrideApprovalBasis):
    work_item_type = "product"


class PlanDocumentRegistryTableFreshness(MatrixCase):
    """Convergence pass 12, ledger `I22`.

    `/milestone-plan` step 3 calls the plan document's checkpoint table
    its own "generated, human-readable checkpoint table -- never
    hand-edited", produced by `workflow_state.render_registry_markdown`.
    `/apply-plan-review` step 5 regenerates `registry_path`/`mapping_path`
    at the new `plan_revision` and, before this pass, never said to
    re-embed the rendered table -- so a revision that added, removed or
    renamed a checkpoint published a plan bundle whose table silently
    disagreed with the authoritative registry.

    Nothing detects that. The table is not hashed separately (the whole
    document's bytes are, so a stale table simply *is* what
    `review_content_id` covers), and `assert_stage_completeness` checks
    only the `(Revision N)` marker. The external reviewer reviews the
    stale table as if it were the plan.

    This suite's own `Item.apply_plan_review` helper called
    `render_registry_markdown` all along -- a step no operator following
    the command would have performed -- which is exactly why every
    plan-revision row stayed green over the gap. That is ledger `I19`'s
    class, one command over."""

    def _revise_without_re_embedding(self, item, plan_revision, checkpoints, requirements):
        """`/apply-plan-review` step 5 as it read *before* this repair:
        regenerate the registry and mapping, edit the document's prose and
        revision marker, publish, re-stage. No re-embed."""
        registry = ws.generate_registry(item.wid, plan_revision, checkpoints)
        mapping = ws.generate_mapping(item.wid, requirements, registry=registry)
        ws.write_registry_and_mapping(
            item.root, Path(item.registry_path), Path(item.mapping_path), registry, mapping,
        )
        self.scratch.write(
            item.plan_path,
            self.scratch.read(item.plan_path).replace(
                "(Revision 1)", f"(Revision {plan_revision})",
            ),
        )
        # workflow-2.6.0: staging precedes the publish of the fresh id.
        item.stage_plan_files()
        item.publish()
        return registry

    def test_the_pre_repair_sequence_publishes_a_table_the_registry_contradicts(self):
        """The defect itself, executed: the document omits a checkpoint the
        registry declares, and the plan bundle publishes anyway."""
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        self.assertNotIn("CP2", self.scratch.read(item.plan_path))
        # The round a revision follows (workflow-2.6.0: a ready item is
        # never re-published in place).
        item.record_plan_reviews(local="REVISE")

        registry = self._revise_without_re_embedding(
            item, 2, CHECKPOINTS_TWO, REQUIREMENTS_TWO,
        )
        self.assertEqual([c["id"] for c in registry["checkpoints"]], ["CP1", "CP2"])
        document = self.scratch.read(item.plan_path)
        self.assertNotIn("CP2", document)
        self.assertNotIn(ws.render_registry_markdown(registry), document)

        # And nothing stops it: the bundle publishes, and the stage's own
        # completeness check passes on the `(Revision N)` marker alone.
        proc = item.generate_plan_bundle(check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        metadata = fingerprint.resolve_plan_stage_metadata(item.root, item.wid)
        self.assertEqual(metadata.plan_revision, 2)
        fingerprint.assert_stage_completeness(
            item.bundle_dir(stage="plan"), "plan", plan_revision=2,
        )

    def test_the_repaired_sequence_keeps_the_table_and_the_registry_in_agreement(self):
        """The repair: the same revision, with step 5's re-embed performed
        -- which is what `Item.apply_plan_review` has always done and what
        the command now actually says."""
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        item.record_plan_reviews(local="REVISE")
        item.apply_plan_review(2, checkpoints=CHECKPOINTS_TWO, requirements=REQUIREMENTS_TWO)

        registry = item.registry()
        document = self.scratch.read(item.plan_path)
        self.assertEqual([c["id"] for c in registry["checkpoints"]], ["CP1", "CP2"])
        self.assertIn(ws.render_registry_markdown(registry), document)
        proc = item.generate_plan_bundle(check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_re_embed_is_a_no_op_when_no_checkpoint_changed(self):
        """Why the command says to re-embed *unconditionally*: the render
        is a pure function of the checkpoint set, so a revision that
        changed none produces byte-identical output. Making the step
        conditional would only reintroduce the judgement call that
        produced the stale table, for no saving at all."""
        item = self.item
        item.milestone_plan()
        before = ws.render_registry_markdown(item.registry())
        item.apply_plan_review(2)
        after = ws.render_registry_markdown(item.registry())
        self.assertEqual(before, after)
        self.assertIn(after, self.scratch.read(item.plan_path))
        # And the plan_revision really did move, so this is the
        # unchanged-checkpoints case rather than a no-op revision.
        self.assertEqual(item.registry()["plan_revision"], 2)


class PlanDocumentRegistryTableFreshnessProduct(PlanDocumentRegistryTableFreshness):
    work_item_type = "product"


class TechnicalApprovalCommitValidation(MatrixCase):
    """Convergence pass 12, ledger `I21`.

    `validate_technical_approval_commit` was implemented, documented and
    unit-tested, and had **no production caller anywhere**: no command
    file named it, and neither `discover_technical_approval_commit` nor
    `verify_post_approval_manifest_match` nor the completion-obligation
    verifier ran it. Its own docstring calls it "the sibling
    `validate_bundle_generation_record_commit` already provides for
    generation-record commits" -- and that sibling really is live, called
    at the point every generation-record commit is identified inside the
    provenance-interval walk. So a technical-approval commit that also
    mutated a *different* work item's entry, or a top-level routing field,
    or recorded `technical_approval` without transitioning `phase`, was
    accepted by every live consumer.

    That is `OPUS-R133-003`'s class exactly (a documented, tested branch
    with no live caller), one approval stage over. `/approve-review`
    implementation-stage step 6 now names both post-commit checks the plan
    stage has always had, applied to the commit the invocation just
    created. These rows are the behavioural half: the same malformed
    commit, through the documented sequence."""

    def _approve_with_a_malformed_commit(self, corrupt):
        """`/approve-review implementation` steps 2-6, with `corrupt`
        applied to the state file after `apply_technical_approval` and
        before the commit. Returns the new commit's SHA."""
        item = self.item
        self.reach_first_bundle()
        item.write_feedback("APPROVE")
        entry = item.entry()
        review_content_id, projection = item.impl_review_content_id(self.scratch.head())
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="implementation",
            user_confirmation=f"implementation {item.wid}", now=item.now(),
            reviewed_bundle_id=item.bundle_id(),
            approved_review_content_id=review_content_id,
            review_content_manifest=projection["review_content_manifest"],
            reviewed_content_commit=entry["reviewed_implementation_head"],
        )
        item.tx(lambda state: ws.apply_technical_approval(
            state, item.wid, record, item.now(),
        ))
        state = json.loads(self.scratch.read("docs/ai-workflow/WORKFLOW_STATE.json"))
        corrupt(state)
        self.scratch.write(
            "docs/ai-workflow/WORKFLOW_STATE.json", json.dumps(state, indent=2) + "\n",
        )
        return self.scratch.commit(
            f"chore({item.wid}): record technical approval",
            {"Workflow-Technical-Approval": review_content_id,
             "Workflow-Work-Item": item.wid},
            paths=["docs/ai-workflow/WORKFLOW_STATE.json"],
        ), review_content_id

    def test_a_commit_touching_another_work_item_is_refused(self):
        item = self.item

        def corrupt(state):
            # A second work item's own entry, silently added in the same
            # commit -- item 267's exact forbidden mutation.
            other = dict(state["work_items"][item.wid])
            other["work_item_id"] = "other-item"
            state["work_items"]["other-item"] = other

        commit, review_content_id = self._approve_with_a_malformed_commit(corrupt)
        # The live consumers still accept it -- which is the point.
        self.assertEqual(
            ws.discover_technical_approval_commit(
                item.root, item.wid, review_content_id, item.base_commit, commit,
            ),
            commit,
        )
        # The documented post-commit check does not.
        with self.assertRaises(ws.MalformedTechnicalApprovalCommitError) as ctx:
            ws.validate_technical_approval_commit(item.root, commit, item.wid)
        self.assertIn("other-item", str(ctx.exception))

    def test_a_commit_recording_the_approval_without_the_phase_is_refused(self):
        item = self.item

        def corrupt(state):
            # The approval recorded, the transition not -- the second half
            # of the validator's contract.
            state["work_items"][item.wid]["phase"] = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"

        commit, _ = self._approve_with_a_malformed_commit(corrupt)
        with self.assertRaises(ws.MalformedTechnicalApprovalCommitError) as ctx:
            ws.validate_technical_approval_commit(item.root, commit, item.wid)
        self.assertIn("phase", str(ctx.exception))

    def test_a_top_level_routing_field_change_is_refused(self):
        item = self.item

        def corrupt(state):
            state["active_work_item_id"] = None

        commit, _ = self._approve_with_a_malformed_commit(corrupt)
        with self.assertRaises(ws.MalformedTechnicalApprovalCommitError):
            ws.validate_technical_approval_commit(item.root, commit, item.wid)

    def test_the_ordinary_approval_commit_passes_both_post_commit_checks(self):
        """The control: the sequence every other row in this suite drives
        produces a commit that satisfies the newly-live check, so the
        repair constrains malformed commits without refusing real ones."""
        item = self.item
        commit = self.reach_technical_approved()
        ws.validate_technical_approval_commit(item.root, commit, item.wid)
        ws.verify_post_approval_manifest_match(
            item.root, item.entry(), stage="implementation",
            base_commit=item.base_commit, commit=commit,
        )


class TechnicalApprovalCommitValidationProduct(TechnicalApprovalCommitValidation):
    work_item_type = "product"


class CanonicalReviewContentIdRecipe(MatrixCase):
    """Convergence pass 12, ledger `O31` (external Optional `N3`).

    Every generation driver tells the author to state
    `<bundle_dir>/REVIEW_REQUEST.md`'s `review_content_id: <hex>` line for
    the round, and `assert_review_request_states_review_content_id`
    *refuses* the whole generation on a wrong one. Until this pass, not
    one driver named any computation for that value, so the instruction
    had no procedure -- an operator following the command exactly had to
    guess which of seven `compute_review_content_id_*` entry points to
    call, with which of eight arguments, at which anchor.

    `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Computing `review_content_id`"
    now names exactly one entry point per stage. A documented recipe that
    nobody executes is a claim, so these rows *run* it -- through the
    documented entry point, with the documented arguments, at the
    documented anchor -- and require the result to be the value the
    generator's own `--write-manifest` step independently recorded."""

    def test_the_documented_plan_stage_recipe_reproduces_the_manifest(self):
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        recorded = fingerprint.read_manifest_identifiers(
            item.bundle_dir(stage="plan") / "MANIFEST.md"
        )["review_content_id"]
        # The protocol's plan-stage recipe, verbatim: one call, one
        # argument beyond repo_root, first element is the digest.
        digest = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            item.root, item.wid,
        )[0]
        self.assertEqual(digest, recorded)

    def test_the_documented_implementation_stage_recipe_reproduces_the_manifest(self):
        item = self.item
        self.reach_first_bundle()
        recorded = fingerprint.read_manifest_identifiers(
            item.bundle_dir() / "MANIFEST.md"
        )["review_content_id"]
        # The protocol's implementation/post-fix recipe, verbatim.
        digest = ws.approval_review_content_id(
            item.root,
            stage="implementation",
            base_commit=item.entry()["base_commit"],
            head="HEAD",
            work_item_type=item.wtype,
            work_item_id=item.wid,
            artifacts_path=fingerprint.artifacts_path_for_work_item(item.wid),
        )
        self.assertEqual(digest, recorded)

    def test_the_recipe_the_protocol_warns_against_does_not_reproduce_it(self):
        """The warning is load-bearing, not decorative: the worktree-source
        variant the protocol tells the author never to use really does
        answer something else the moment anything is dirty, which is the
        ordinary state of a working tree mid-round."""
        item = self.item
        self.reach_first_bundle()
        recorded = fingerprint.read_manifest_identifiers(
            item.bundle_dir() / "MANIFEST.md"
        )["review_content_id"]
        # An uncommitted protected-path edit -- exactly what a session has
        # in hand when it is about to author the next round's bundle.
        self.scratch.write(item.deliverable, "// uncommitted local edit\n")
        classification = item.impl_classification()
        worktree_sourced, _ = fingerprint.compute_review_content_id_implementation_stage(
            item.root, item.base_commit, item.wtype, item.wid, *classification,
        )
        self.assertNotEqual(worktree_sourced, recorded)
        # While the documented, commit-source recipe still answers the
        # value the manifest records, dirty tree and all.
        self.assertEqual(
            ws.approval_review_content_id(
                item.root, stage="implementation",
                base_commit=item.entry()["base_commit"], head="HEAD",
                work_item_type=item.wtype, work_item_id=item.wid,
                artifacts_path=fingerprint.artifacts_path_for_work_item(item.wid),
            ),
            recorded,
        )

    def test_the_default_artifacts_path_the_protocol_warns_against_differs(self):
        """The second documented caution, executed: resolving the
        classification through anything other than
        `artifacts_path_for_work_item(work_item_id)` computes a different
        work item's declaration, and the digest moves with it."""
        item = self.item
        self.reach_first_bundle()
        recorded = fingerprint.read_manifest_identifiers(
            item.bundle_dir() / "MANIFEST.md"
        )["review_content_id"]
        # A second, real work item with its own declaration in the same repo.
        other = Item(self.scratch, "product" if self.work_item_type == "process" else "process")
        other.base_commit = item.base_commit
        other.milestone_plan()
        self.assertNotEqual(
            ws.approval_review_content_id(
                item.root, stage="implementation",
                base_commit=item.entry()["base_commit"], head="HEAD",
                work_item_type=item.wtype, work_item_id=item.wid,
                artifacts_path=fingerprint.artifacts_path_for_work_item(other.wid),
            ),
            recorded,
        )


    def test_a_foreign_bundle_id_line_refuses_the_generation(self):
        """Convergence pass 12, ledger `O37`. The fourth author-written
        generation precondition, and the only *prohibition* among them:
        no bundle file other than `MANIFEST.md` may carry a `bundle_id:
        <64 hex>` line. It is an easy line to write by accident, because
        the feedback protocol asks the reviewer to quote the bundle id
        back -- and nothing documented it. Executed here so the rule the
        protocol now states is evidence: the generation refuses,
        `current/` is intact, nothing is published and nothing is
        withdrawn."""
        item = self.item
        item.milestone_plan()
        bundle = item.bundle_dir(stage="plan")
        bundle.mkdir(parents=True, exist_ok=True)
        digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            item.root, item.wid,
        )
        metadata = fingerprint.resolve_plan_stage_metadata(item.root, item.wid)
        (bundle / "REVIEW_REQUEST.md").write_text(
            f"# Review request\n\nstage: plan\nwork item: {item.wid}\n"
            f"review_content_id: {digest}\n"
            f"bundle_id: {'a' * 64}\n"
        )
        (bundle / "TEST_RESULTS.md").write_text(
            f"stage: plan (revision {metadata.plan_revision})\n"
            f"head: {item.sim.head()}\n\nNo automated checks.\n"
        )
        (bundle / "CONTEXT_FILES.txt").write_text("")
        proc = item.sim.prepare(item.base_commit, "plan", item.wid, check=False)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ForeignBundleIdFieldError", proc.stderr)
        self.assertIn("REVIEW_REQUEST.md", proc.stderr)
        # Refusal, not withdrawal.
        self.assertTrue(bundle.is_dir())
        self.assertFalse((bundle / "MANIFEST.md").exists())
        self.assertEqual(
            list((item.root / ".ai-review" / item.wid).glob("current.rejected-*")), [],
        )
        self.assertFalse((item.root / ".ai-review" / item.wid / "REJECTED").exists())

        # And removing the line is the whole remedy: the same generation
        # publishes, first attempt.
        (bundle / "REVIEW_REQUEST.md").write_text(
            f"# Review request\n\nstage: plan\nwork item: {item.wid}\n"
            f"review_content_id: {digest}\n"
        )
        second = item.sim.prepare(item.base_commit, "plan", item.wid, check=False)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue((bundle / "MANIFEST.md").is_file())


class CanonicalReviewContentIdRecipeProduct(CanonicalReviewContentIdRecipe):
    work_item_type = "product"


class CrossStageGenerationReuseProduct(CrossStageGenerationReuse):
    """Section `J` for a `product` work item. The defect was type-blind --
    it lived entirely in the generator's own control flow -- but so was
    every earlier one this campaign found on the strength of a
    process-only row."""

    work_item_type = "product"


# ===========================================================================
# CP4. D-Plan-Review-Bundle-Binding (workflow-2.6.0), driven in the real
# command order: publication split, bind, withdrawal, the total status
# function, the readers, and the recoverable plan-stage generator.
# ===========================================================================

CP4_STATE_REL = "docs/ai-workflow/WORKFLOW_STATE.json"


def cp4_state_bytes(item):
    return (item.root / CP4_STATE_REL).read_bytes()


def cp4_status(item):
    return ws.plan_review_publication_status(item.root, item.state(), item.wid)


def cp4_fresh_id(item):
    digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(item.root, item.wid)
    return digest


def cp4_render_plan(item, plan_revision, plan_body, registry):
    """The plan document exactly as `Item.milestone_plan`/`apply_plan_review`
    render it: title, body, one anchor pair per checkpoint, the table."""
    return (
        f"# {item.wid} plan (Revision {plan_revision})\n\n{plan_body}\n"
        + plan_anchors(registry) + ws.render_registry_markdown(registry) + "\n"
    )


def cp4_write_round_files(item, plan_revision, checkpoints=None, requirements=None,
                          plan_body="Plan body, revised.\n"):
    """`/apply-plan-review` steps 3-5's protected edits alone -- registry and
    mapping regeneration plus the table re-embed -- with no entry marker, no
    staging and no publish (so a scenario can stop between any two)."""
    checkpoints = CHECKPOINTS if checkpoints is None else checkpoints
    requirements = REQUIREMENTS if requirements is None else requirements
    registry = ws.generate_registry(item.wid, plan_revision, checkpoints)
    mapping = ws.generate_mapping(item.wid, requirements, registry=registry)
    ws.write_registry_and_mapping(
        item.root, Path(item.registry_path), Path(item.mapping_path), registry, mapping,
    )
    item.sim.write(item.plan_path, cp4_render_plan(item, plan_revision, plan_body, registry))
    return registry


def cp4_edit_prose(item, extra="An extra paragraph the author is still writing.\n"):
    """A partial prose edit to the plan document: the revision marker, the
    anchors and the table are untouched."""
    text = item.sim.read(item.plan_path)
    item.sim.write(item.plan_path, text.replace("\n\n", f"\n\n{extra}\n", 1))


def cp4_set_title_revision(item, revision):
    text = item.sim.read(item.plan_path)
    item.sim.write(item.plan_path, re.sub(r"\(Revision \d+\)", f"(Revision {revision})", text, count=1))


def cp4_item_root(item):
    return item.root / ".ai-review" / item.wid


def cp4_snapshot(path):
    """Every byte under `path` (a file or a directory tree), keyed by
    relative path -- for byte-identity comparisons."""
    path = Path(path)
    if path.is_file():
        return {".": path.read_bytes()}
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in sorted(path.rglob("*")) if p.is_file()
    }


def cp4_default_test_results(item, note=""):
    metadata = fingerprint.resolve_plan_stage_metadata(item.root, item.wid)
    return (
        f"stage: plan (revision {metadata.plan_revision})\nhead: {item.sim.head()}\n\n"
        f"No automated checks are required at the plan stage.\n{note}"
    )


def cp4_regenerate_wrapper_only(item, note="Re-run: wrapper-only regeneration.\n", bind=True):
    """A regeneration whose protected content is unchanged: only an author
    wrapper file (`TEST_RESULTS.md`'s prose) differs, so `review_content_id`
    is unchanged and `bundle_id` is not."""
    return item.generate_plan_bundle(test_results=cp4_default_test_results(item, note), bind=bind)


def cp4_hand_edit(item, mutate):
    """A hand edit of the item's entry -- how a scenario simulates a
    `2.5.1`-shaped item or an out-of-band record no `2.6.0` writer
    produces. Runs inside `state_transaction` so the file stays valid JSON
    under the lock."""
    def mutator(state):
        new_state = json.loads(json.dumps(state))
        mutate(new_state["work_items"][item.wid])
        return new_state
    item.tx(mutator)


def cp4_make_legacy(work_item):
    """A `2.5.1` item: no `plan_review_binding`, a null `current_bundle_id`."""
    work_item.pop("plan_review_binding", None)
    work_item["current_bundle_id"] = None


def cp4_manifest_binding(item):
    """The binding the on-disk `current/MANIFEST.md` claims, read without
    verification -- for a direct `bind_plan_review_bundle` attempt against
    a bundle the verifier would refuse."""
    fields = fingerprint.read_plan_stage_manifest_fields(item.bundle_dir(stage="plan") / "MANIFEST.md")
    return {
        "review_content_id": fields["review_content_id"],
        "bundle_id": fields["bundle_id"],
        "plan_revision": fields["plan_revision"],
    }


def cp4_publish(item, plan_revision=None, review_content_id=None):
    """`publish_plan_revision` at `plan_revision` (default: the registry's)
    with the fresh id computed inside the mutator, or a given one."""
    def mutator(state):
        digest = review_content_id or cp4_fresh_id(item)
        revision = plan_revision if plan_revision is not None else item.registry()["plan_revision"]
        return ws.publish_plan_revision(state, item.wid, revision, item.now(), review_content_id=digest)
    item.tx(mutator)


def cp4_route(item, plan_revision):
    config = json.loads(item.sim.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
    item.tx(lambda state: ws.route_work_item(
        state, config, work_item_id=item.wid, work_item_type=item.wtype,
        work_item_kind=item.wtype, plan_path=item.plan_path,
        registry_path=item.registry_path, plan_revision=plan_revision,
        now=item.now(), mapping_path=item.mapping_path, base_commit=item.base_commit,
        repo_root=item.root,
    ))


def cp4_open_plan_approval_journal(item):
    """`/approve-review plan` up to and including step 4's journal open --
    the same arguments `Item.approve_plan` passes -- and no further."""
    confirmation = f"plan {item.wid}"
    review_content_id, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
        item.root, item.wid,
    )
    bundle_id = item.bundle_id()
    feedback = item.feedback_fields()
    basis = ws.resolve_approval_basis(
        latest_round_status=feedback["status"], feedback_bundle_id=feedback["reviewed_bundle_id"],
        current_bundle_id=bundle_id, user_confirmation=confirmation,
        work_item_id=item.wid, stage="plan",
    )
    approval_now = item.now()
    record = ws.build_approval_record(
        basis=basis, stage="plan", user_confirmation=confirmation, now=approval_now,
        reviewed_bundle_id=bundle_id, approved_review_content_id=review_content_id,
        review_content_manifest=projection["review_content_manifest"],
    )
    plan = fingerprint.resolve_plan_stage_approval_commit_paths(item.root, item.wid, Path(CP4_STATE_REL))
    return ws.open_plan_approval_journal(
        item.root, work_item_id=item.wid, base_commit=item.base_commit,
        pre_state=item.state(), record=record, approval_now=approval_now,
        expected_bundle_id=bundle_id, expected_review_content_id=review_content_id,
        applicable_paths=plan.paths,
        fifth_member_applies=plan.artifacts_declaration_path is not None,
        fifth_member_sha256=plan.artifacts_declaration_sha256,
        user_confirmation=confirmation,
        quiescence_authorization="acceptance-matrix scenario",
    )


class _PlanReviewBindingCase(MatrixCase):
    """Shared lifecycle points for the CP4 scenarios. No tests of its own."""

    work_item_type = "process"

    # --- lifecycle points ---

    def reach_bound(self):
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(item.entry()["plan_review_binding"]["status"], "BOUND")
        return item.entry()["plan_review_binding"]["bound"]

    def reach_local_revise(self):
        bound = self.reach_bound()
        self.item.record_plan_reviews(local="REVISE")
        self.assertEqual(self.item.entry()["phase"], "REVISING_PLAN")
        return bound

    def reach_manual(self):
        bound = self.reach_bound()
        item = self.item
        item.tx(lambda state: ws.record_local_plan_review(
            state, item.wid, verdict="APPROVE", bundle_id=item.bundle_id(),
            review_content_id=cp4_fresh_id(item), round=1, now=item.now(),
        ))
        self.assertEqual(item.entry()["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        return bound

    def reach_approval(self):
        bound = self.reach_bound()
        self.item.write_feedback("APPROVE")
        self.item.record_plan_reviews()
        self.assertEqual(self.item.entry()["phase"], "AWAITING_PLAN_APPROVAL")
        return bound

    def reach_amending(self):
        self.reach_plan_approved()
        approved = self.item.entry()["plan_approval"]["approved_review_content_id"]
        self.item.request_amendment()
        self.assertEqual(self.item.entry()["phase"], "AMENDING_PLAN")
        return approved

    # --- assertions ---

    def assertRow(self, row, status, item=None):
        result = cp4_status(item or self.item)
        self.assertEqual((result["row"], result["status"]), (row, status), result)
        return result

    def assertBound(self, plan_revision=None, item=None):
        item = item or self.item
        entry = item.entry()
        self.assertEqual(entry["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        record = entry["plan_review_binding"]
        self.assertEqual(record["status"], "BOUND")
        self.assertEqual(record["bound"]["review_content_id"], cp4_fresh_id(item))
        self.assertEqual(record["bound"]["bundle_id"], item.bundle_id())
        self.assertEqual(entry["current_bundle_id"], item.bundle_id())
        if plan_revision is not None:
            self.assertEqual(entry["plan_revision"], plan_revision)
            self.assertEqual(record["bound"]["plan_revision"], plan_revision)
        self.assertRow("2", ws.PLAN_REVIEW_STATUS_BOUND, item=item)
        return record

    def assertRefusesWithoutWrite(self, exc_type, fn, item=None):
        item = item or self.item
        before = cp4_state_bytes(item)
        with self.assertRaises(exc_type) as ctx:
            fn()
        self.assertEqual(cp4_state_bytes(item), before, f"{exc_type.__name__} refusal wrote state")
        return ctx.exception

    def attempt_publish(self, exc_type, plan_revision=None, review_content_id=None):
        return self.assertRefusesWithoutWrite(
            exc_type, lambda: cp4_publish(self.item, plan_revision, review_content_id),
        )

    def attempt_bind(self, exc_type, binding=None):
        """A direct bind against the on-disk bundle: through the verifier
        when `binding` is None, else the given binding straight into the
        mutator."""
        item = self.item

        def run():
            use = binding if binding is not None else ws.verify_plan_review_bundle(item.root, item.wid)
            item.tx(lambda state: ws.bind_plan_review_bundle(state, item.wid, binding=use, now=item.now()))
        return self.assertRefusesWithoutWrite(exc_type, run)


class PlanReviewBindVerifierRefusals(_PlanReviewBindingCase):
    """`verify_plan_review_bundle` refuses every shape section 5.3 item 3
    names, by cause; `bind_plan_review_bundle` refuses content the author
    never published."""

    def test_no_bundle_refuses_unverified(self):
        """Published, never generated: nothing to bind."""
        self.item.milestone_plan()
        exc = self.attempt_bind(ws.PlanReviewBundleUnverifiedError)
        self.assertIn("no MANIFEST.md", str(exc))
        self.assertEqual(self.item.entry()["phase"], "PLANNING")

    def test_rejected_bundle_refuses_unverified(self):
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle(bind=False)
        (cp4_item_root(item) / "REJECTED").write_text("REJECTED: test\n")
        exc = self.attempt_bind(ws.PlanReviewBundleUnverifiedError)
        self.assertIn("REJECTED", str(exc))

    def test_stale_revision_manifest_refuses_unverified(self):
        """A REVISE round published at revision 2 with the revision-1
        bundle still on disk."""
        item = self.item
        self.reach_local_revise()
        item.apply_plan_review(2)
        exc = self.attempt_bind(ws.PlanReviewBundleUnverifiedError)
        self.assertIn("stale-revision", str(exc))

    def test_stale_review_content_id_refuses_drift(self):
        """Content edited after generation: the bundle is internally
        consistent, the worktree is not what it captured."""
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle(bind=False)
        cp4_edit_prose(item)
        self.attempt_bind(ws.ReviewedContentDriftError)
        self.assertEqual(item.entry()["phase"], "PLANNING")

    def test_mixed_bundle_refuses_unverified(self):
        """Section 3.3 variant 2's shape: `current/` content changed without
        the manifest being re-written."""
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle(bind=False)
        path = item.bundle_dir(stage="plan") / "TEST_RESULTS.md"
        path.write_text(path.read_text() + "edited in place\n")
        exc = self.attempt_bind(ws.PlanReviewBundleUnverifiedError)
        self.assertIn("bundle_id disagreement", str(exc))

    def test_mismatched_archive_refuses_unverified(self):
        import tarfile
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle(bind=False)
        forged = Path(tempfile.mkdtemp(prefix="cp4-archive-"))
        self.addCleanup(shutil.rmtree, forged, True)
        shutil.copytree(item.bundle_dir(stage="plan"), forged / "current")
        (forged / "current" / "TEST_RESULTS.md").write_text("not what current/ holds\n")
        with tarfile.open(cp4_item_root(item) / "review-bundle.tar.gz", "w:gz") as tf:
            tf.add(forged / "current", arcname="current")
        exc = self.attempt_bind(ws.PlanReviewBundleUnverifiedError)
        self.assertIn("archive=", str(exc))

    def test_bind_refuses_unpublished_record(self):
        """`PLANNING`, registry written and staged, bundle generated, but
        never published: no `PUBLISHED` record, no bind."""
        item = self.item
        cp4_route(item, 1)
        cp4_write_round_files(item, 1, plan_body="Plan body.\n")
        item.sim.write(item.artifacts_path, json.dumps(ws.generate_artifacts_declarations(
            item.wid, item.plan_path, item.registry_path, item.mapping_path,
            work_item_type=item.wtype,
        ), indent=2) + "\n")
        item.stage_plan_files()
        item.generate_plan_bundle(bind=False)
        self.assertNotIn("plan_review_binding", item.entry())
        self.attempt_bind(ws.PlanReviewNotPublishedError)

    def test_bind_refuses_content_other_than_the_published(self):
        """Published, then edited, then regenerated: the bundle verifies for
        content the author never declared complete."""
        item = self.item
        item.milestone_plan()
        cp4_edit_prose(item)
        item.generate_plan_bundle(bind=False)
        binding = ws.verify_plan_review_bundle(item.root, item.wid)
        self.assertNotEqual(binding["review_content_id"],
                            item.entry()["plan_review_binding"]["published"]["review_content_id"])
        self.attempt_bind(ws.PlanReviewNotPublishedError)


class PlanReviewGeneratorRecovery(_PlanReviewBindingCase):
    """The recoverable plan-stage generator (item 5) and the resume rows it
    leaves (rows 9, 2, 3, 4b)."""

    def _stale_inputs_round(self, **generate_kwargs):
        item = self.item
        consumed = self.reach_local_revise()
        item.apply_plan_review(2, plan_body="Plan body, revised.\n")
        self.assertEqual(item.entry()["plan_revision"], 2)
        state_revision = item.entry()["state_revision"]
        proc = item.generate_plan_bundle(check=False, **generate_kwargs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("staging discarded", proc.stdout + proc.stderr)
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")
        self.assertEqual(item.entry()["state_revision"], state_revision)
        self.assertEqual(cp4_status(item)["row"], "9")
        self.assertFalse(cp4_status(item)["bundle_verifies"])
        # Retry after fixing the inputs: no second revision bump, then a bind.
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)
        self.assertEqual(item.registry()["plan_revision"], 2)
        return consumed

    def test_variant_1_stale_test_results_ends_non_ready_then_retry_binds(self):
        """Section 3.3 variant 1 through the real generator: a
        `TEST_RESULTS.md` still naming the previous revision."""
        self._stale_inputs_round(test_results=(
            "stage: plan (revision 1)\nhead: HEAD\n\nNo automated checks are required.\n"
        ))

    def test_variant_2_stale_review_request_ends_non_ready_then_retry_binds(self):
        """Section 3.3 variant 2 through the real generator: a
        `REVIEW_REQUEST.md` still stating the reviewed (consumed) id."""
        item = self.item
        bound = self.reach_local_revise()
        item.apply_plan_review(2)
        proc = item.generate_plan_bundle(check=False, review_request=(
            f"# Review request\n\nstage: plan\nwork item: {item.wid}\n"
            f"review_content_id: {bound['review_content_id']}\n"
        ))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")
        self.assertEqual(cp4_status(item)["row"], "9")
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)

    def test_crash_between_generation_and_bind_bumping_round_binds_only(self):
        item = self.item
        self.reach_local_revise()
        item.apply_plan_review(2)
        item.generate_plan_bundle(bind=False)
        status = self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)
        self.assertTrue(status["bundle_verifies"])
        self.assertIn("bind only", status["remedy"])
        bundle_id = item.bundle_id()
        item.bind_plan_bundle()
        self.assertBound(plan_revision=2)
        self.assertEqual(item.bundle_id(), bundle_id)

    def test_crash_between_generation_and_bind_non_bumping_round_binds_only(self):
        """A REVISE round that edits prose without advancing plan_revision."""
        item = self.item
        self.reach_local_revise()
        item.apply_plan_review(1, plan_body="Plan body, reworded.\n")
        self.assertEqual(item.entry()["plan_revision"], 1)
        item.generate_plan_bundle(bind=False)
        self.assertTrue(self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)["bundle_verifies"])
        item.bind_plan_bundle()
        self.assertBound(plan_revision=1)

    def _assert_failed_generation_leaves_bound_bundle(self, **generate_kwargs):
        item = self.item
        self.reach_bound()
        root = cp4_item_root(item)
        current = cp4_snapshot(root / "current")
        archive = cp4_snapshot(root / "review-bundle.tar.gz")
        pin = cp4_snapshot(root / ".pin")
        state = cp4_state_bytes(item)
        proc = item.generate_plan_bundle(check=False, **generate_kwargs)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(cp4_snapshot(root / "current"), current)
        self.assertEqual(cp4_snapshot(root / "review-bundle.tar.gz"), archive)
        self.assertEqual(cp4_snapshot(root / ".pin"), pin)
        self.assertFalse((root / "REJECTED").exists())
        self.assertEqual(list(root.glob("current.rejected-*")), [])
        self.assertEqual(list(root.glob("current.staging-*")), [])
        self.assertEqual(list(root.glob(".pin.staging-*")), [])
        self.assertEqual(cp4_state_bytes(item), state)
        # The readers still accept the previous, still-bound bundle.
        self.assertIsNone(ws.assert_plan_review_bundle_bound(item.root, item.wid))
        self.assertIsNone(ws.validate_local_plan_review_preconditions_bound(item.root, item.entry()))
        self.assertRow("2", ws.PLAN_REVIEW_STATUS_BOUND)

    def test_failed_staging_generation_stale_test_results_keeps_bound_bundle(self):
        self._assert_failed_generation_leaves_bound_bundle(
            test_results="stage: plan (revision 7)\nhead: HEAD\n\nstale\n",
        )

    def test_failed_staging_generation_stale_review_request_keeps_bound_bundle(self):
        self._assert_failed_generation_leaves_bound_bundle(
            review_request=f"# Review request\n\nreview_content_id: {'a' * 64}\n",
        )

    def test_pre_existing_rejected_marker_refuses_readers_until_regeneration(self):
        item = self.item
        self.reach_bound()
        (cp4_item_root(item) / "REJECTED").write_text("REJECTED: written by 2.5.1\n")
        with self.assertRaises(ws.PlanReviewBundleUnverifiedError) as ctx:
            ws.assert_plan_review_bundle_bound(item.root, item.wid)
        self.assertIn("regenerate", str(ctx.exception))
        self.assertRow("4b", ws.PLAN_REVIEW_STATUS_BUNDLE_UNVERIFIED)
        # The next successful generation clears it; the new bundle_id is
        # advisory only (content unchanged).
        cp4_regenerate_wrapper_only(item)
        self.assertFalse((cp4_item_root(item) / "REJECTED").exists())
        advisory = ws.assert_plan_review_bundle_bound(item.root, item.wid)
        self.assertIsNotNone(advisory)
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

    def test_implementation_stage_failure_still_withdraws(self):
        """Stage scope: implementation-stage generation keeps `2.5.1`'s
        in-place generation and `withdraw_bundle` quarantine."""
        item = self.item
        self.reach_plan_approved()
        item.implement_checkpoint("CP1", {item.deliverable: "// round 1\n"})
        _, _, proc = item.generate_impl_bundle(
            "implementation", check=False, summary="# No revision line\n",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("withdrawn", proc.stdout + proc.stderr)
        root = cp4_item_root(item)
        self.assertFalse((root / "current").exists())
        self.assertFalse((root / "review-bundle.tar.gz").exists())
        self.assertEqual(len(list(root.glob("current.rejected-*"))), 1)
        self.assertEqual(list(root.glob("current.staging-*")), [])


class PlanReviewConsumedContent(_PlanReviewBindingCase):
    """Consumed content never re-binds (`LPR-R1-001`): publish and bind
    both refuse `ConsumedPlanReviewContentError` before an edit plus
    regeneration, and succeed after one."""

    def _assert_consumed_refused_then_edit_binds(self, *, next_revision, amend=False):
        item = self.item
        # Publish of the unchanged content refuses early.
        self.attempt_publish(ws.ConsumedPlanReviewContentError,
                             plan_revision=item.entry()["plan_revision"])
        # The reviewed bundle still on disk still verifies -- and bind refuses.
        binding = ws.verify_plan_review_bundle(item.root, item.wid)
        self.assertEqual(binding["review_content_id"],
                         item.entry()["plan_review_binding"]["consumed"]["review_content_id"])
        self.attempt_bind(ws.ConsumedPlanReviewContentError)
        if amend:
            item.amend_plan(next_revision)
        else:
            item.apply_plan_review(next_revision, plan_body="Plan body, after the verdict.\n")
        item.generate_plan_bundle()
        self.assertBound(plan_revision=next_revision)

    def test_fresh_local_revise_with_reviewed_bundle_on_disk(self):
        self.reach_local_revise()
        self.assertRow("10", ws.PLAN_REVIEW_STATUS_NEEDS_EDIT)
        self._assert_consumed_refused_then_edit_binds(next_revision=2)

    def test_fresh_manual_revise(self):
        item = self.item
        self.reach_bound()
        item.record_plan_reviews(local="APPROVE", manual="REVISE")
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")
        self.assertRow("10", ws.PLAN_REVIEW_STATUS_NEEDS_EDIT)
        self._assert_consumed_refused_then_edit_binds(next_revision=2)

    def test_fresh_amending_plan_with_approved_bundle_on_disk(self):
        item = self.item
        approved = self.reach_amending()
        consumed = item.entry()["plan_review_binding"]["consumed"]
        self.assertEqual(consumed["review_content_id"], approved)
        self.assertFalse(consumed["legacy"])
        self._assert_consumed_refused_then_edit_binds(next_revision=2, amend=True)

    def test_non_bumping_round_refused_before_edit_bound_after(self):
        item = self.item
        self.reach_local_revise()
        self.attempt_publish(ws.ConsumedPlanReviewContentError, plan_revision=1)
        item.apply_plan_review(1, plan_body="Plan body, same revision, reworded.\n")
        item.generate_plan_bundle()
        self.assertBound(plan_revision=1)

    def test_wrapper_only_regeneration_of_consumed_content_still_refused(self):
        item = self.item
        bound = self.reach_local_revise()
        cp4_regenerate_wrapper_only(item, bind=False)
        binding = ws.verify_plan_review_bundle(item.root, item.wid)
        self.assertNotEqual(binding["bundle_id"], bound["bundle_id"])
        self.assertEqual(binding["review_content_id"], bound["review_content_id"])
        self.attempt_bind(ws.ConsumedPlanReviewContentError)
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")


class PlanReviewMidEditCrash(_PlanReviewBindingCase):
    """A mid-edit crash never binds (`LPR-R2-001`): the entry status is
    `EDIT_IN_PROGRESS` (row 11) and a direct bind against the on-disk
    bundle refuses."""

    def test_bumping_apply_plan_review_round(self):
        """The plan document already carries the new checkpoint's section;
        the registry is not regenerated yet."""
        item = self.item
        self.reach_local_revise()
        text = item.sim.read(item.plan_path)
        item.sim.write(item.plan_path, text + "<!-- CP2 -->\nCP2 -- second.\n<!-- /CP2 -->\n")
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)
        self.attempt_bind(ws.ReviewedContentDriftError)
        self.attempt_bind(ws.ConsumedPlanReviewContentError, binding=cp4_manifest_binding(item))
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")

    def test_non_bumping_apply_plan_review_round(self):
        item = self.item
        self.reach_local_revise()
        cp4_edit_prose(item)
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)
        self.attempt_bind(ws.ReviewedContentDriftError)
        self.attempt_bind(ws.ConsumedPlanReviewContentError, binding=cp4_manifest_binding(item))

    def test_amendment_under_milestone_plan_with_mirror_advanced(self):
        """`/milestone-plan` step 1 advanced the mirror before any edit
        (M > R): a mirror advance is not an "edits complete" fact."""
        item = self.item
        self.reach_amending()
        item.tx(lambda state: ws.ensure_plan_review_binding_marker(state, item.wid, item.now()))
        cp4_route(item, 2)
        self.assertEqual(item.entry()["plan_revision"], 2)
        self.assertEqual(item.registry()["plan_revision"], 1)
        cp4_edit_prose(item)
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)
        exc = self.attempt_bind(ws.PlanReviewBundleUnverifiedError)
        self.assertIn("stale-revision", str(exc))
        self.assertEqual(item.entry()["phase"], "AMENDING_PLAN")

    def test_revision_title_bumped_ahead_of_registry(self):
        """`(Revision 2)` in the title, registry still at 1:
        `PlanRevisionMismatchError`, read as F = bottom."""
        item = self.item
        self.reach_local_revise()
        cp4_set_title_revision(item, 2)
        status = self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)
        self.assertIsNone(status["fresh_review_content_id"])
        self.attempt_bind(ws.ReviewedContentDriftError)

    def test_publish_followed_by_further_edits(self):
        item = self.item
        self.reach_local_revise()
        item.apply_plan_review(2)
        self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)
        cp4_edit_prose(item)
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)
        item.generate_plan_bundle(bind=False)
        self.attempt_bind(ws.PlanReviewNotPublishedError)
        # The normal path: step 5 publishes again, then the bind succeeds.
        cp4_publish(item)
        item.bind_plan_bundle()
        self.assertBound(plan_revision=2)


class PlanReviewLegacyItems(_PlanReviewBindingCase):
    """INV-7: items that entered their round under `2.5.1`."""

    def test_legacy_revising_plan_needs_the_marker_then_one_advance(self):
        item = self.item
        self.reach_local_revise()
        cp4_hand_edit(item, cp4_make_legacy)
        self.assertRow("5", ws.PLAN_REVIEW_STATUS_LEGACY_UNMARKED)
        # Before the marker: publish and bind both refuse, by name.
        self.attempt_bind(ws.LegacyPlanReviewBindingUnknownError)
        cp4_write_round_files(item, 2)
        item.stage_plan_files()
        exc = self.attempt_publish(ws.LegacyPlanReviewBindingUnknownError)
        self.assertIn("ensure_plan_review_binding_marker", str(exc))
        # Revert to the reviewed revision-1 content; the entry writes the marker.
        cp4_write_round_files(item, 1, plan_body="Plan body.\n")
        item.tx(lambda state: ws.ensure_plan_review_binding_marker(state, item.wid, item.now()))
        record = item.entry()["plan_review_binding"]
        self.assertEqual(record["status"], "CONSUMED")
        self.assertEqual(record["consumed"], {"review_content_id": None, "plan_revision": 1, "legacy": True})
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)
        # At the marker's revision: refused, even after a prose edit.
        self.attempt_publish(ws.ConsumedPlanReviewContentError, plan_revision=1)
        self.attempt_bind(ws.ConsumedPlanReviewContentError)
        cp4_edit_prose(item)
        self.attempt_publish(ws.ConsumedPlanReviewContentError, plan_revision=1)
        # One advance binds.
        item.apply_plan_review(2)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)

    def _legacy_amending_without_approved_id(self, work_item):
        cp4_make_legacy(work_item)
        work_item["amendment_history"][-1]["superseded_plan_approval"] = None

    def test_legacy_amending_plan_without_approved_id_needs_the_marker(self):
        item = self.item
        self.reach_amending()
        cp4_hand_edit(item, self._legacy_amending_without_approved_id)
        self.assertRow("5", ws.PLAN_REVIEW_STATUS_LEGACY_UNMARKED)
        self.attempt_publish(ws.LegacyPlanReviewBindingUnknownError, plan_revision=1)
        self.attempt_bind(ws.LegacyPlanReviewBindingUnknownError)
        item.tx(lambda state: ws.ensure_plan_review_binding_marker(state, item.wid, item.now()))
        self.assertEqual(item.entry()["plan_review_binding"]["consumed"],
                         {"review_content_id": None, "plan_revision": 1, "legacy": True})
        self.attempt_publish(ws.ConsumedPlanReviewContentError, plan_revision=1)
        self.attempt_bind(ws.ConsumedPlanReviewContentError)
        item.amend_plan(2)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)

    def test_legacy_amending_plan_with_approved_id_uses_the_amendment_record(self):
        """`LPR-R3-006`: 2.5.1's /milestone-plan step 1 already advanced the
        mirror; the marker comes from the amendment entry (non-legacy), and
        the item binds after its edit with no extra advance."""
        item = self.item
        approved = self.reach_amending()
        cp4_hand_edit(item, cp4_make_legacy)
        cp4_route(item, 2)  # 2.5.1's step 1, before the update
        self.assertRow("5", ws.PLAN_REVIEW_STATUS_LEGACY_UNMARKED)
        item.tx(lambda state: ws.ensure_plan_review_binding_marker(state, item.wid, item.now()))
        self.assertEqual(item.entry()["plan_review_binding"]["consumed"],
                         {"review_content_id": approved, "plan_revision": 1, "legacy": False})
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)
        # The amended-away content is refused.
        self.attempt_publish(ws.ConsumedPlanReviewContentError, plan_revision=2, review_content_id=approved)
        # The edit, at the already-advanced revision: no extra advance.
        cp4_write_round_files(item, 2, plan_body="Plan body, amended.\n")
        item.stage_plan_files()
        cp4_publish(item)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)

    def test_legacy_ready_item_is_accepted_and_never_written(self):
        """A 2.5.1 item at AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW with no record
        and a null current_bundle_id: the readers accept it; nothing writes
        a record or changes its phase."""
        item = self.item
        self.reach_manual()
        cp4_hand_edit(item, cp4_make_legacy)
        before = cp4_state_bytes(item)
        self.assertRow("3", ws.PLAN_REVIEW_STATUS_BOUND)
        self.assertIsNone(ws.assert_plan_review_bundle_bound(item.root, item.wid))
        self.assertEqual(cp4_state_bytes(item), before)
        self.assertNotIn("plan_review_binding", item.entry())
        self.assertEqual(item.entry()["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

    def test_legacy_marker_feedback_check(self):
        """`LPR-R3-006`: with the null-id marker, the durable check is exactly
        `Work item:` = target and `Status: REVISE`."""
        item = self.item
        self.reach_local_revise()
        cp4_hand_edit(item, cp4_make_legacy)
        item.tx(lambda state: ws.ensure_plan_review_binding_marker(state, item.wid, item.now()))
        status = self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)["status"]

        def check(content):
            return ws.assert_apply_plan_review_feedback(
                item.entry(), item.wid, feedback_content=content, publication_status=status,
            )
        item.write_feedback("REVISE")
        self.assertEqual(check((item.feedback_dir() / "REVIEW_FEEDBACK.md").read_text()), "durable")
        item.write_feedback("REVISE", work_item="some-other-item")
        with self.assertRaises(ws.FeedbackNotForConsumedContentError):
            check((item.feedback_dir() / "REVIEW_FEEDBACK.md").read_text())
        item.write_feedback("APPROVE")
        with self.assertRaises(ws.FeedbackStatusNotApplicableError):
            check((item.feedback_dir() / "REVIEW_FEEDBACK.md").read_text())


class PlanReviewStatusTable(_PlanReviewBindingCase):
    """The status function is total (`LPR-R2-001`): one test per row of
    section 5.3 item 6's decision table."""

    def test_row_1_not_plan_stage(self):
        item = self.item
        self.reach_plan_approved()
        status = self.assertRow("1", ws.PLAN_REVIEW_STATUS_NOT_PLAN_STAGE)
        self.assertIn(f"/request-plan-amendment {item.wid}", status["remedy"])

    def test_row_2_bound(self):
        self.reach_bound()
        status = self.assertRow("2", ws.PLAN_REVIEW_STATUS_BOUND)
        self.assertIsNone(status["advisory"])
        self.assertIsNone(ws.assert_plan_review_bundle_bound(self.item.root, self.item.wid))

    def test_row_3_legacy_ready_bundle_verifies(self):
        self.reach_bound()
        cp4_hand_edit(self.item, cp4_make_legacy)
        self.assertRow("3", ws.PLAN_REVIEW_STATUS_BOUND)
        self.assertIsNone(ws.assert_plan_review_bundle_bound(self.item.root, self.item.wid))

    def test_row_4a_content_drifted(self):
        item = self.item
        self.reach_bound()
        cp4_edit_prose(item)
        self.assertRow("4a", ws.PLAN_REVIEW_STATUS_CONTENT_DRIFTED)
        with self.assertRaises(ws.ReviewedContentDriftError) as ctx:
            ws.assert_plan_review_bundle_bound(item.root, item.wid)
        message = str(ctx.exception)
        self.assertIn("(row 4a)", message)
        self.assertIn(f".ai-review/{item.wid}/current/files/", message)
        self.assertIn(f"/milestone-plan {item.wid}", message)

    def test_row_4b_bundle_unverified(self):
        item = self.item
        self.reach_bound()
        path = item.bundle_dir(stage="plan") / "CONTEXT_FILES.txt"
        path.write_text("mixed\n")
        self.assertRow("4b", ws.PLAN_REVIEW_STATUS_BUNDLE_UNVERIFIED)
        with self.assertRaises(ws.PlanReviewBundleUnverifiedError) as ctx:
            ws.assert_plan_review_bundle_bound(item.root, item.wid)
        message = str(ctx.exception)
        self.assertIn("(row 4b)", message)
        self.assertIn(f"prepare-ai-review.sh <base> plan {item.wid}", message)
        self.assertIn(f"/milestone-plan {item.wid}", message)

    def test_row_4c_legacy_unverified(self):
        item = self.item
        self.reach_bound()
        cp4_hand_edit(item, cp4_make_legacy)
        shutil.rmtree(item.bundle_dir(stage="plan"))
        self.assertRow("4c", ws.PLAN_REVIEW_STATUS_LEGACY_UNVERIFIED)
        with self.assertRaises(ws.PlanReviewBundleUnverifiedError) as ctx:
            ws.assert_plan_review_bundle_bound(item.root, item.wid)
        message = str(ctx.exception)
        self.assertIn("(row 4c)", message)
        self.assertIn("regenerate", message)
        self.assertIn(f"/milestone-plan {item.wid}", message)

    def test_row_4d_ready_phase_with_published_record_is_inconsistent(self):
        item = self.item
        self.reach_bound()

        def hand_edit(work_item):
            record = work_item["plan_review_binding"]
            record["status"] = "PUBLISHED"
            record["bound"] = None
        cp4_hand_edit(item, hand_edit)
        with self.assertRaises(ws.PlanReviewBindingInconsistentError) as ctx:
            cp4_status(item)
        self.assertIn("row 4d", str(ctx.exception))
        self.assertIn(f"/milestone-plan {item.wid}", str(ctx.exception))
        with self.assertRaises(ws.PlanReviewBindingInconsistentError):
            ws.assert_plan_review_bundle_bound(item.root, item.wid)
        # The named remedy: the withdrawal writes the fail-closed marker.
        item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now()))
        self.assertEqual(item.entry()["plan_review_binding"]["consumed"]["legacy"], True)

    def test_row_5_legacy_unmarked(self):
        self.reach_local_revise()
        cp4_hand_edit(self.item, cp4_make_legacy)
        self.assertRow("5", ws.PLAN_REVIEW_STATUS_LEGACY_UNMARKED)

    def test_row_6_non_ready_phase_with_bound_record_is_inconsistent(self):
        item = self.item
        self.reach_bound()
        cp4_hand_edit(item, lambda work_item: work_item.update(phase="REVISING_PLAN"))
        with self.assertRaises(ws.PlanReviewBindingInconsistentError) as ctx:
            cp4_status(item)
        self.assertIn("row 6", str(ctx.exception))
        self.attempt_publish(ws.PlanReviewBindingInconsistentError, plan_revision=1)
        self.attempt_bind(ws.PlanReviewBindingInconsistentError)

    def test_row_7_first_round_planning_no_record_no_registry(self):
        item = self.item
        cp4_route(item, 1)
        self.assertNotIn("plan_review_binding", item.entry())
        self.assertFalse((item.root / item.registry_path).exists())
        self.assertRow("7", ws.PLAN_REVIEW_STATUS_NEEDS_EDIT)

    def test_row_11_planning_with_registry_and_no_record(self):
        item = self.item
        cp4_route(item, 1)
        cp4_write_round_files(item, 1, plan_body="Plan body.\n")
        item.sim.write(item.artifacts_path, json.dumps(ws.generate_artifacts_declarations(
            item.wid, item.plan_path, item.registry_path, item.mapping_path,
            work_item_type=item.wtype,
        ), indent=2) + "\n")
        item.stage_plan_files()
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)

    def test_row_8_needs_revision_resumes_step_5_without_differing(self):
        """The registry was regenerated at R > M, then the session stopped
        before the publish; re-running step 5 changes no bytes."""
        item = self.item
        self.reach_local_revise()
        cp4_write_round_files(item, 2)
        self.assertRow("8", ws.PLAN_REVIEW_STATUS_NEEDS_REVISION)
        written = {p: (item.root / p).read_bytes() for p in (item.registry_path, item.mapping_path, item.plan_path)}
        item.apply_plan_review(2)
        self.assertEqual(
            {p: (item.root / p).read_bytes() for p in written}, written,
        )
        self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)

    def test_row_9_published_unbound_before_and_after_generation(self):
        item = self.item
        item.milestone_plan()
        self.assertFalse(self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)["bundle_verifies"])
        item.generate_plan_bundle(bind=False)
        self.assertTrue(self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)["bundle_verifies"])

    def test_row_10_needs_edit_after_revise(self):
        self.reach_local_revise()
        self.assertRow("10", ws.PLAN_REVIEW_STATUS_NEEDS_EDIT)

    def test_row_11_edit_in_progress(self):
        self.reach_local_revise()
        cp4_edit_prose(self.item)
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)

    def _durable_feedback_check(self, expected_status):
        item = self.item
        consumed = item.entry()["plan_review_binding"]["consumed"]["review_content_id"]
        status = cp4_status(item)["status"]
        self.assertEqual(status, expected_status)

        def check(rcid):
            item.write_feedback("REVISE", extra=f"review_content_id: {rcid}")
            return ws.assert_apply_plan_review_feedback(
                item.entry(), item.wid,
                feedback_content=(item.feedback_dir() / "REVIEW_FEEDBACK.md").read_text(),
                publication_status=status,
            )
        self.assertEqual(check(consumed), "durable")
        before = cp4_state_bytes(item)
        with self.assertRaises(ws.FeedbackNotForConsumedContentError):
            check("b" * 64)
        self.assertEqual(cp4_state_bytes(item), before)

    def test_durable_feedback_check_under_published_unbound(self):
        self.reach_local_revise()
        self.item.apply_plan_review(2)
        self._durable_feedback_check(ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)

    def test_durable_feedback_check_under_edit_in_progress(self):
        self.reach_local_revise()
        cp4_edit_prose(self.item)
        self._durable_feedback_check(ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)

    def test_cli_prints_one_json_object(self):
        item = self.item
        item.milestone_plan()
        before = cp4_state_bytes(item)
        proc = _run(
            [sys.executable, "scripts/workflow_state.py", "--plan-review-publication-status", item.wid],
            cwd=item.root,
        )
        self.assertEqual(proc.stdout.count("\n"), 1)
        printed = json.loads(proc.stdout)
        self.assertEqual(printed["row"], "9")
        self.assertEqual(printed["status"], ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)
        self.assertEqual(printed["work_item_id"], item.wid)
        self.assertFalse(any(k.startswith("_") for k in printed))
        self.assertEqual(cp4_state_bytes(item), before)


class PlanReviewReadyPhaseWriters(_PlanReviewBindingCase):
    """Ready-phase writers (`LPR-R3-001`/`LPR-R2-002`): after every call the
    phase is ready only with a `BOUND` record, and no case wedges."""

    def _withdraw(self):
        item = self.item
        ws.assert_plan_review_withdrawal_allowed(item.root, item.wid, explicit_id=True)
        item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now()))

    def _withdraw_then_rebind(self, bound, expected_phase="REVISING_PLAN"):
        item = self.item
        stages = item.entry().get("plan_review_stages")
        self._withdraw()
        entry = item.entry()
        self.assertEqual(entry["phase"], expected_phase)
        self.assertEqual(entry["plan_review_binding"]["status"], "CONSUMED")
        self.assertEqual(entry["plan_review_binding"]["consumed"], {
            "review_content_id": bound["review_content_id"],
            "plan_revision": bound["plan_revision"], "legacy": False,
        })
        self.assertEqual(entry.get("plan_review_stages"), stages)
        self.assertRow("10", ws.PLAN_REVIEW_STATUS_NEEDS_EDIT)
        # The withdrawn content is refused ...
        self.attempt_publish(ws.ConsumedPlanReviewContentError, plan_revision=bound["plan_revision"])
        self.attempt_bind(ws.ConsumedPlanReviewContentError)
        # ... and new content re-binds through the normal path.
        next_revision = bound["plan_revision"] + 1
        if expected_phase == "AMENDING_PLAN":
            item.amend_plan(bound["plan_revision"], plan_body="Plan body, amended again.\n")
            next_revision = bound["plan_revision"]
        else:
            item.apply_plan_review(next_revision)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=next_revision)

    def test_withdraw_from_awaiting_local_plan_review(self):
        self._withdraw_then_rebind(self.reach_bound())

    def test_withdraw_from_awaiting_manual_external_plan_review(self):
        self._withdraw_then_rebind(self.reach_manual())

    def test_withdraw_from_awaiting_plan_approval(self):
        self._withdraw_then_rebind(self.reach_approval())

    def test_withdraw_with_open_amendment_lands_on_amending_plan(self):
        item = self.item
        self.reach_amending()
        item.amend_plan(2)
        item.generate_plan_bundle()
        bound = self.assertBound(plan_revision=2)["bound"]
        self._withdraw_then_rebind(bound, expected_phase="AMENDING_PLAN")

    def test_withdraw_at_non_ready_phase_refuses(self):
        item = self.item
        self.reach_local_revise()
        self.assertRefusesWithoutWrite(
            ws.PlanReviewNotReadyError,
            lambda: item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now())),
        )

    def test_edit_then_regeneration_at_local_review_refuses_review_plan(self):
        """An edit plus regeneration before any verdict: `/review-plan`
        refuses naming both remedies; restoring the bound bytes (then
        regenerating the now-stale bundle) returns to row 2."""
        item = self.item
        self.reach_bound()
        bound_files = cp4_snapshot(item.bundle_dir(stage="plan") / "files")
        # Without regenerating: restore straight from current/files/.
        cp4_edit_prose(item)
        with self.assertRaises(ws.ReviewedContentDriftError):
            ws.validate_local_plan_review_preconditions_bound(item.root, item.entry())
        (item.root / item.plan_path).write_bytes(
            (item.bundle_dir(stage="plan") / "files" / item.plan_path).read_bytes())
        self.assertRow("2", ws.PLAN_REVIEW_STATUS_BOUND)
        # With a regeneration: the bundle verifies for the edited content,
        # which is still not the bound content.
        cp4_edit_prose(item)
        proc = item.generate_plan_bundle()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertRow("4a", ws.PLAN_REVIEW_STATUS_CONTENT_DRIFTED)
        with self.assertRaises(ws.ReviewedContentDriftError) as ctx:
            ws.validate_local_plan_review_preconditions_bound(item.root, item.entry())
        self.assertIn("current/files/", str(ctx.exception))
        self.assertIn(f"/milestone-plan {item.wid}", str(ctx.exception))
        (item.root / item.plan_path).write_bytes(bound_files[item.plan_path])
        cp4_regenerate_wrapper_only(item)
        self.assertRow("2", ws.PLAN_REVIEW_STATUS_BOUND)
        self.assertIsNotNone(ws.validate_local_plan_review_preconditions_bound(item.root, item.entry()))

    def test_publish_and_route_refuse_at_each_ready_phase(self):
        item = self.item
        config = json.loads(item.sim.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        self.reach_bound()
        for advance in (
            lambda: None,
            lambda: item.tx(lambda state: ws.record_local_plan_review(
                state, item.wid, verdict="APPROVE", bundle_id=item.bundle_id(),
                review_content_id=cp4_fresh_id(item), round=1, now=item.now(),
            )),
            lambda: (item.write_feedback("APPROVE"), item.tx(lambda state: ws.record_manual_plan_review(
                state, item.wid, verdict="APPROVE", bundle_id=item.bundle_id(), round=1,
                now=item.now(), current_review_content_id=cp4_fresh_id(item),
                feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id=cp4_fresh_id(item),
            ))),
        ):
            advance()
            phase = item.entry()["phase"]
            with self.subTest(phase=phase):
                exc = self.attempt_publish(ws.PlanReviewInProgressError, plan_revision=1)
                self.assertIn(f"/milestone-plan {item.wid}", str(exc))
                self.attempt_publish(ws.PlanReviewInProgressError, plan_revision=2)
                self.assertRefusesWithoutWrite(ws.PlanReviewInProgressError, lambda: item.tx(
                    lambda state: ws.route_work_item(
                        state, config, work_item_id=item.wid, work_item_type=item.wtype,
                        work_item_kind=item.wtype, plan_path=item.plan_path,
                        registry_path=item.registry_path, plan_revision=2, now=item.now(),
                        mapping_path=item.mapping_path, base_commit=item.base_commit,
                        repo_root=item.root,
                    )))
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")

    def test_legacy_ready_item_with_withdrawn_bundle_recovers_by_regeneration(self):
        item = self.item
        self.reach_bound()
        cp4_hand_edit(item, cp4_make_legacy)
        # 2.5.1's withdrawal: the bundle quarantined, the archive removed.
        root = cp4_item_root(item)
        (root / "current").rename(root / "current.rejected-legacy")
        (root / "review-bundle.tar.gz").unlink()
        self.assertRow("4c", ws.PLAN_REVIEW_STATUS_LEGACY_UNVERIFIED)
        with self.assertRaises(ws.PlanReviewBundleUnverifiedError):
            ws.assert_plan_review_bundle_bound(item.root, item.wid)
        before = cp4_state_bytes(item)
        item.generate_plan_bundle()
        self.assertEqual(cp4_state_bytes(item), before)
        self.assertRow("3", ws.PLAN_REVIEW_STATUS_BOUND)
        self.assertIsNone(ws.assert_plan_review_bundle_bound(item.root, item.wid))
        self.assertNotIn("plan_review_binding", item.entry())


class PlanReviewWrapperOnlyRegeneration(_PlanReviewBindingCase):
    """A wrapper-only regeneration after the bind stays non-blocking
    (`LPR-R2-002`); `bind` never regresses a ready phase."""

    def test_manual_stage_ingests_with_advisory(self):
        item = self.item
        bound = self.reach_manual()
        cp4_regenerate_wrapper_only(item)
        new_bundle_id = item.bundle_id()
        self.assertNotEqual(new_bundle_id, bound["bundle_id"])
        advisory = ws.assert_plan_review_bundle_bound(item.root, item.wid)
        self.assertIn(bound["bundle_id"], advisory)
        self.assertIn(new_bundle_id, advisory)
        exc = self.attempt_bind(ws.PlanReviewAlreadyReadyError)
        self.assertIn("nothing was written", str(exc))
        self.assertEqual(item.entry()["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        rcid = cp4_fresh_id(item)
        item.tx(lambda state: ws.record_manual_plan_review(
            state, item.wid, verdict="APPROVE", bundle_id=new_bundle_id, round=1, now=item.now(),
            current_review_content_id=rcid, feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id=rcid,
        ))
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")

    def test_plan_approval_still_proceeds(self):
        item = self.item
        bound = self.reach_approval()
        cp4_regenerate_wrapper_only(item)
        self.assertNotEqual(item.bundle_id(), bound["bundle_id"])
        self.assertIsNotNone(ws.assert_plan_review_bundle_bound(item.root, item.wid))
        self.attempt_bind(ws.PlanReviewAlreadyReadyError)
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")
        item.write_feedback("APPROVE")
        item.approve_plan()
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")


class PlanReviewSelfReviewEditsBind(_PlanReviewBindingCase):
    """Self-review edits bind (`LPR-R4-001`), in the real `/milestone-plan`
    step order: step 1 routes; step 3 writes the registry, mapping,
    artifacts, table and stages; steps 4-5 edit; the publication point
    regenerates, re-embeds, re-stages and publishes; step 6 generates and
    binds."""

    def milestone_plan_real_order(self, plan_revision=1, self_review=None, publish_at_step_3=False,
                                  amendment=False):
        item = self.item
        # Step 1 (with the entry marker an amendment's entry writes).
        if amendment:
            item.tx(lambda state: ws.ensure_plan_review_binding_marker(state, item.wid, item.now()))
        cp4_route(item, plan_revision)
        # Step 3.
        round_ = {"checkpoints": CHECKPOINTS, "requirements": REQUIREMENTS,
                  "plan_body": "Plan body, amended.\n" if amendment else "Plan body.\n"}
        cp4_write_round_files(item, plan_revision, round_["checkpoints"], round_["requirements"],
                              round_["plan_body"])
        if not amendment:
            item.sim.write(item.artifacts_path, json.dumps(ws.generate_artifacts_declarations(
                item.wid, item.plan_path, item.registry_path, item.mapping_path,
                work_item_type=item.wtype,
            ), indent=2) + "\n")
        item.stage_plan_files()
        step_3_id = cp4_fresh_id(item)
        if publish_at_step_3:
            cp4_publish(item)  # 2.5.1's position
        # Steps 4-5: self-review edits in place.
        if self_review is not None:
            self_review(round_)
        if not publish_at_step_3:
            # The publication point.
            registry = cp4_write_round_files(item, plan_revision, round_["checkpoints"],
                                             round_["requirements"], round_["plan_body"])
            self.assertEqual(registry["plan_revision"], plan_revision)
            item.stage_plan_files()
            cp4_publish(item)
        return step_3_id

    def _edit_plan_body(self, round_):
        round_["plan_body"] += "Self-review: clarified the risk section.\n"
        registry = self.item.registry()
        self.item.sim.write(self.item.plan_path, cp4_render_plan(
            self.item, registry["plan_revision"], round_["plan_body"], registry))

    def test_self_review_edits_the_plan_document(self):
        item = self.item
        step_3_id = self.milestone_plan_real_order(self_review=self._edit_plan_body)
        item.generate_plan_bundle()
        record = self.assertBound(plan_revision=1)
        self.assertNotEqual(record["bound"]["review_content_id"], step_3_id)
        self.assertIn("Self-review", item.sim.read(item.plan_path))

    def test_self_review_edits_artifacts_plan_stage_sets(self):
        item = self.item

        def edit_artifacts(round_):
            declarations = json.loads(item.sim.read(item.artifacts_path))
            declarations["plan_stage"]["excluded_paths"]["docs/NOTES.md"] = "scratch notes, not design"
            item.sim.write(item.artifacts_path, json.dumps(declarations, indent=2) + "\n")
        step_3_id = self.milestone_plan_real_order(self_review=edit_artifacts)
        item.generate_plan_bundle()
        record = self.assertBound(plan_revision=1)
        self.assertNotEqual(record["bound"]["review_content_id"], step_3_id)
        bound_decl = json.loads((item.bundle_dir(stage="plan") / "files" / item.artifacts_path).read_text())
        self.assertIn("docs/NOTES.md", bound_decl["plan_stage"]["excluded_paths"])

    def test_self_review_adds_a_checkpoint(self):
        item = self.item

        def add_checkpoint(round_):
            round_["checkpoints"] = CHECKPOINTS_TWO
            round_["requirements"] = REQUIREMENTS_TWO
        self.milestone_plan_real_order(self_review=add_checkpoint)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=1)
        files = item.bundle_dir(stage="plan") / "files"
        bound_registry = json.loads((files / item.registry_path).read_text())
        self.assertEqual([c["id"] for c in bound_registry["checkpoints"]], ["CP1", "CP2"])
        self.assertIn(ws.render_registry_markdown(bound_registry), (files / item.plan_path).read_text())

    def test_amendment_round_with_a_self_review_edit_binds(self):
        item = self.item
        self.reach_amending()
        self.milestone_plan_real_order(plan_revision=2, self_review=self._edit_plan_body, amendment=True)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)
        self.assertEqual(item.entry()["plan_review_binding"]["consumed"]["plan_revision"], 1)

    def test_pinning_publish_at_2_5_1_step_3_then_edit_refuses_bind(self):
        item = self.item
        self.milestone_plan_real_order(self_review=self._edit_plan_body, publish_at_step_3=True)
        item.generate_plan_bundle(bind=False)
        self.attempt_bind(ws.PlanReviewNotPublishedError)
        self.assertEqual(item.entry()["phase"], "PLANNING")
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)

    def test_first_round_with_untracked_plan_files(self):
        """`LPR-R3-003`: publishing before the intent-to-add step refuses
        while computing the id; the real order publishes, generates, binds."""
        item = self.item
        cp4_route(item, 1)
        cp4_write_round_files(item, 1, plan_body="Plan body.\n")
        item.sim.write(item.artifacts_path, json.dumps(ws.generate_artifacts_declarations(
            item.wid, item.plan_path, item.registry_path, item.mapping_path,
            work_item_type=item.wtype,
        ), indent=2) + "\n")
        self.attempt_publish(fingerprint.InvalidPlanStageMetadataPathError, plan_revision=1)
        self.assertNotIn("plan_review_binding", item.entry())
        item.stage_plan_files()
        cp4_publish(item)
        item.generate_plan_bundle()
        self.assertBound(plan_revision=1)


class PlanReviewPhaseAllowList(_PlanReviewBindingCase):
    """The plan-stage allow-list (`LPR-R4-002`): outside
    PLANNING/REVISING_PLAN/AMENDING_PLAN and the ready phases, every
    plan-stage writer refuses before any write."""

    def _assert_refuses_outside_plan_stage(self, names_amendment):
        item = self.item
        config = json.loads(item.sim.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        before = cp4_state_bytes(item)
        for command in ("/milestone-plan", "/apply-plan-review"):
            with self.assertRaises(ws.PlanReviewPhaseNotPlanStageError) as ctx:
                ws.assert_plan_review_entry_phase(item.entry(), item.wid, command=command)
            self.assertEqual(
                f"/request-plan-amendment {item.wid}" in str(ctx.exception), names_amendment,
                str(ctx.exception),
            )
        exc = self.attempt_publish(ws.PlanReviewPhaseNotPlanStageError, plan_revision=2)
        self.assertEqual(f"/request-plan-amendment {item.wid}" in str(exc), names_amendment)
        self.assertRefusesWithoutWrite(ws.PlanReviewPhaseNotPlanStageError, lambda: item.tx(
            lambda state: ws.route_work_item(
                state, config, work_item_id=item.wid, work_item_type=item.wtype,
                work_item_kind=item.wtype, plan_path=item.plan_path,
                registry_path=item.registry_path, plan_revision=2, now=item.now(),
                mapping_path=item.mapping_path, base_commit=item.base_commit, repo_root=item.root,
            )))
        self.assertEqual(cp4_state_bytes(item), before)

    def test_implementing_refuses_and_names_the_amendment_route(self):
        self.reach_plan_approved()
        self._assert_refuses_outside_plan_stage(names_amendment=True)

    def test_awaiting_functional_review_refuses_without_an_amendment_route(self):
        self.reach_technical_approved()
        self._assert_refuses_outside_plan_stage(names_amendment=False)

    def test_remediation_child_re_declaration_at_planning_still_succeeds(self):
        parent = self.item
        self.reach_plan_approved()
        config = json.loads(self.scratch.read("docs/ai-workflow/WORKFLOW_CONFIG.json"))
        holder = {}

        def create(state):
            new_state, child_id = ws.create_remediation_child_work_item(
                state, config, parent_work_item_id=parent.wid,
                plan_path=f"docs/ai-workflow/{parent.wid}-remediation-1-plan.md",
                registry_path=f"docs/ai-workflow/registry/{parent.wid}-remediation-1-registry.json",
                base_commit=self.scratch.head(), now=parent.now(),
            )
            holder["id"] = child_id
            return new_state
        parent.tx(create)
        created = parent.state()["work_items"][holder["id"]]
        self.assertEqual(created["phase"], "PLANNING")
        child = Item(self.scratch, parent.wtype)
        child.wid = holder["id"]
        child.plan_path = created["plan_path"]
        child.registry_path = created["registry_path"]
        child.mapping_path = f"docs/ai-workflow/requirements/{child.wid}-mapping.json"
        child.artifacts_path = f"docs/ai-workflow/registry/{child.wid}-artifacts.json"
        child.base_commit = created["base_commit"]
        ws.assert_plan_review_entry_phase(child.entry(), child.wid, command="/milestone-plan")
        child.milestone_plan()  # the resume branch, at PLANNING
        self.assertEqual(child.entry()["mapping_path"], child.mapping_path)
        self.assertEqual(child.entry()["plan_review_binding"]["status"], "PUBLISHED")
        child.generate_plan_bundle()
        self.assertBound(plan_revision=1, item=child)

    def test_version_1_publish_from_implementing_is_unchanged(self):
        """`"1"`-governed `/bootstrap-workflow-v2` step 1 (dict level)."""
        self.reach_plan_approved()
        state = self.item.state()
        work_item = state["work_items"][self.item.wid]
        work_item["governing_workflow_version"] = "1"
        self.assertEqual(work_item["phase"], "IMPLEMENTING")
        new_state = ws.publish_plan_revision(state, self.item.wid, 2, "2026-02-01T00:00:00Z")
        published = new_state["work_items"][self.item.wid]
        self.assertEqual(published["phase"], "AWAITING_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(published["plan_revision"], 2)
        self.assertEqual(published.get("plan_review_binding"), work_item.get("plan_review_binding"))


class PlanReviewUnreadableFreshId(_PlanReviewBindingCase):
    """Unreadable fresh id at a ready phase (`LPR-R4-003`): drift, never a
    bare `PlanRevisionMismatchError`/`AbsentProtectedPathError`; the
    withdrawal needs no fresh id."""

    EXTRA = "docs/design-notes.md"

    def _reach_bound_with_extra_protected_path(self):
        item = self.item
        declarations = ws.generate_artifacts_declarations(
            item.wid, item.plan_path, item.registry_path, item.mapping_path,
            work_item_type=item.wtype,
        )
        declarations["plan_stage"]["protected_paths"].append(self.EXTRA)
        self.scratch.write(self.EXTRA, "design notes\n")
        self.scratch.git("add", "-N", "--", self.EXTRA)
        item.milestone_plan(artifacts=declarations)
        item.generate_plan_bundle()
        return self.assertBound(plan_revision=1)["bound"]

    def _drift(self, kind):
        if kind == "title":
            cp4_set_title_revision(self.item, 2)
        else:
            (self.item.root / self.EXTRA).unlink()

    def _assert_readers_refuse(self, exc_type, row):
        item = self.item
        for reader in (
            lambda: ws.assert_plan_review_bundle_bound(item.root, item.wid),
            lambda: ws.validate_local_plan_review_preconditions_bound(item.root, item.entry()),
        ):
            with self.assertRaises(exc_type) as ctx:
                reader()
            self.assertNotIsInstance(ctx.exception, fingerprint.PlanRevisionMismatchError)
            self.assertNotIsInstance(ctx.exception, fingerprint.AbsentProtectedPathError)
            self.assertIn(f"(row {row})", str(ctx.exception))
            self.assertIn(f"/milestone-plan {item.wid}", str(ctx.exception))

    def _bound_case(self, kind):
        item = self.item
        bound = self._reach_bound_with_extra_protected_path()
        self._drift(kind)
        status = self.assertRow("4a", ws.PLAN_REVIEW_STATUS_CONTENT_DRIFTED)
        self.assertIsNone(status["fresh_review_content_id"])
        self._assert_readers_refuse(ws.ReviewedContentDriftError, "4a")
        item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now()))
        self.assertEqual(item.entry()["plan_review_binding"]["consumed"]["review_content_id"],
                         bound["review_content_id"])
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)

    def _legacy_case(self, kind):
        item = self.item
        self._reach_bound_with_extra_protected_path()
        cp4_hand_edit(item, cp4_make_legacy)
        self._drift(kind)
        self.assertRow("4c", ws.PLAN_REVIEW_STATUS_LEGACY_UNVERIFIED)
        self._assert_readers_refuse(ws.PlanReviewBundleUnverifiedError, "4c")
        item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now()))
        self.assertTrue(item.entry()["plan_review_binding"]["consumed"]["legacy"])
        self.assertRow("11", ws.PLAN_REVIEW_STATUS_EDIT_IN_PROGRESS)

    def test_bound_bumped_title(self):
        self._bound_case("title")

    def test_bound_deleted_protected_path(self):
        self._bound_case("deleted")

    def test_legacy_bumped_title(self):
        self._legacy_case("title")

    def test_legacy_deleted_protected_path(self):
        self._legacy_case("deleted")

    def test_withdrawal_needs_no_fresh_id_when_the_plan_document_is_deleted(self):
        item = self.item
        self.reach_bound()
        (item.root / item.plan_path).unlink()
        item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now()))
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")

    def test_deleted_metadata_path_is_a_named_refusal_and_withdrawal_still_exits(self):
        """Section 5.3 item 6: `F` reads as unreadable only for
        `AbsentProtectedPathError`/`PlanRevisionMismatchError`; "any other
        failure refuses (INV-3)", and the plan names
        `InvalidPlanStageMetadataPathError` as outside that set. Deleting
        one of the three plan-stage *metadata* paths (here the mapping)
        therefore refuses by that name at the readers and the status
        function -- never a raw `TypeError`/`KeyError` -- and the withdrawal,
        which computes no fresh id (`LPR-R4-003`), still exits."""
        item = self.item
        self.reach_bound()
        (item.root / item.mapping_path).unlink()
        with self.assertRaises(fingerprint.InvalidPlanStageMetadataPathError):
            ws.assert_plan_review_bundle_bound(item.root, item.wid)
        with self.assertRaises(fingerprint.InvalidPlanStageMetadataPathError):
            ws.plan_review_publication_status(item.root, item.state(), item.wid)
        item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now()))
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")


class PlanReviewWithdrawalGuards(_PlanReviewBindingCase):
    """`/milestone-plan`'s entry guards before a withdrawal
    (`LPR-R4-004`/`LPR-R4-006`/`LPR-R5-004`)."""

    def test_implicit_target_refuses(self):
        item = self.item
        self.reach_bound()
        exc = self.assertRefusesWithoutWrite(
            ws.PlanReviewWithdrawalNeedsExplicitIdError,
            lambda: ws.assert_plan_review_withdrawal_allowed(item.root, item.wid, explicit_id=False),
        )
        self.assertIn(f"/milestone-plan {item.wid}", str(exc))

    def test_open_journal_for_this_item_refuses(self):
        item = self.item
        self.reach_approval()
        item.write_feedback("APPROVE")
        cp4_open_plan_approval_journal(item)
        exc = self.assertRefusesWithoutWrite(
            ws.PlanApprovalInProgressError,
            lambda: ws.assert_plan_review_withdrawal_allowed(item.root, item.wid, explicit_id=True),
        )
        self.assertIn(f"/approve-review plan {item.wid}", str(exc))

    def test_corrupt_journal_refuses(self):
        item = self.item
        self.reach_approval()
        journal = item.root / ws.PLAN_APPROVAL_JOURNAL_PATH
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("{ not json")
        self.assertRefusesWithoutWrite(
            ws.PlanApprovalJournalUnavailableError,
            lambda: ws.assert_plan_review_withdrawal_allowed(item.root, item.wid, explicit_id=True),
        )

    def test_journal_for_a_different_item_allows_withdrawal(self):
        item = self.item
        other = Item(self.scratch, "product")
        other.base_commit = item.base_commit
        (self.scratch.root / "docs/milestones").mkdir(parents=True, exist_ok=True)
        other.milestone_plan()
        other.generate_plan_bundle()
        other.write_feedback("APPROVE")
        other.record_plan_reviews()
        cp4_open_plan_approval_journal(other)
        self.reach_approval()
        ws.assert_plan_review_withdrawal_allowed(item.root, item.wid, explicit_id=True)
        item.tx(lambda state: ws.withdraw_plan_review(state, item.wid, item.now()))
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")


class PlanReviewNoExitBetweenPublishAndBind(_PlanReviewBindingCase):
    """No command exits between its publish and its bind (`LPR-R5-001`,
    `MPR-R1-O1`)."""

    def _apply_feedback(self, status):
        item = self.item
        item.write_feedback(status)
        publication = cp4_status(item)["status"]
        return self.assertRefusesWithoutWrite(
            ws.FeedbackStatusNotApplicableError,
            lambda: ws.assert_apply_plan_review_feedback(
                item.entry(), item.wid,
                feedback_content=(item.feedback_dir() / "REVIEW_FEEDBACK.md").read_text(),
                publication_status=publication,
            ),
        )

    def test_block_feedback_at_revising_plan_refuses(self):
        item = self.item
        self.reach_local_revise()
        exc = self._apply_feedback("BLOCK")
        self.assertIn(f"/review-plan {item.wid}", str(exc))
        self.assertIn(f"/milestone-plan {item.wid}", str(exc))

    def test_approve_feedback_at_revising_plan_refuses(self):
        self.reach_local_revise()
        self._apply_feedback("APPROVE")

    def test_forced_generator_failure_after_publish_then_rerun_binds(self):
        item = self.item
        self.reach_local_revise()
        item.apply_plan_review(2)
        state_revision = item.entry()["state_revision"]
        proc = item.generate_plan_bundle(check=False, test_results="")
        self.assertNotEqual(proc.returncode, 0)
        self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)
        self.assertEqual(item.entry()["state_revision"], state_revision)
        # The explicit-id re-run: regenerate, then bind; the revision is untouched.
        item.generate_plan_bundle()
        self.assertBound(plan_revision=2)
        self.assertEqual(item.registry()["plan_revision"], 2)

    def test_single_generation_per_round(self):
        item = self.item
        self.reach_local_revise()
        calls = []
        original = self.scratch.prepare

        def counting_prepare(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)
        self.scratch.prepare = counting_prepare
        # /apply-plan-review, one 2.1 round: step 5 (edits, publish,
        # generation), then step 7' (verify + bind, no generation).
        item.apply_plan_review(2)
        item.generate_plan_bundle()
        self.assertEqual(len(calls), 1)
        self.assertBound(plan_revision=2)

    def test_unverifiable_current_between_generation_and_bind_refuses_by_cause(self):
        item = self.item
        self.reach_local_revise()
        item.apply_plan_review(2)
        item.generate_plan_bundle(bind=False)
        (item.bundle_dir(stage="plan") / "TEST_RESULTS.md").write_text("tampered\n")
        self.attempt_bind(ws.PlanReviewBundleUnverifiedError)
        status = self.assertRow("9", ws.PLAN_REVIEW_STATUS_PUBLISHED_UNBOUND)
        self.assertFalse(status["bundle_verifies"])
        self.assertEqual(item.entry()["phase"], "REVISING_PLAN")

    def test_readers_refuse_an_unbound_bundle(self):
        """A bundle generated but never bound, with the phase hand-set ready
        (the record still PUBLISHED): row 4d at every reader."""
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle(bind=False)
        cp4_hand_edit(item, lambda work_item: work_item.update(phase="AWAITING_LOCAL_PLAN_REVIEW"))
        for reader in (
            lambda: ws.assert_plan_review_bundle_bound(item.root, item.wid),
            lambda: ws.validate_local_plan_review_preconditions_bound(item.root, item.entry()),
        ):
            with self.assertRaises(ws.PlanReviewBindingInconsistentError):
                reader()



# ===========================================================================
# workflow-2.6.0 CP5: `D-Plan-Approval-Closure` -- the approval commit
# closes over the declared protected set plus removals, proven before the
# commit exists, and verified from the committed transaction. Every row
# drives `Item.approve_plan`, the command-shaped driver: the command's real
# data flow, never a hand-built post-state.
# ===========================================================================

CP5_COMPANION = "docs/ai-workflow/WI_COMPANION.md"
CP5_RENAMED = "docs/ai-workflow/WI_COMPANION_RENAMED.md"


def cp5_declarations(item, protected_extra=()):
    declarations = ws.generate_artifacts_declarations(
        item.wid, item.plan_path, item.registry_path, item.mapping_path,
        work_item_type=item.wtype,
    )
    declarations["plan_stage"]["protected_paths"].extend(protected_extra)
    return declarations


def cp5_approval_commit_count(item, review_content_id=None):
    """Commits on `HEAD`'s history carrying this item's plan-approval
    trailer (for one `review_content_id`, or any)."""
    log = item.sim.git("log", "--format=%H%x00%B%x01", "HEAD").stdout
    count = 0
    for record in log.split("\x01"):
        body = record.partition("\x00")[2]
        if f"Workflow-Work-Item: {item.wid}" not in body:
            continue
        for line in body.splitlines():
            if line.startswith("Workflow-Plan-Approval: ") and (
                review_content_id is None or line.split(": ", 1)[1].strip() == review_content_id
            ):
                count += 1
    return count


def cp5_tree_blob(item, commit, path):
    out = item.sim.git("ls-tree", commit, "--", path).stdout.strip()
    return out.split()[2] if out else None


def cp5_changed(item, commit):
    return set(item.sim.git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).stdout.split())


class _PlanApprovalClosureCase(MatrixCase):
    """Shared lifecycle points for the CP5 rows. No tests of its own."""

    work_item_type = "process"

    def plan_with(self, protected_extra=(), files=None, intent_to_add=()):
        item = self.item
        for rel, content in (files or {}).items():
            self.scratch.write(rel, content)
        if intent_to_add:
            self.scratch.git("--literal-pathspecs", "add", "-N", "--", *intent_to_add,
                             unset=fingerprint.CONFLICTING_PATHSPEC_ENV)
        item.milestone_plan(artifacts=cp5_declarations(item, protected_extra))
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")

    def approve(self, **kwargs):
        item = self.item
        commit = item.approve_plan(**kwargs)
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")
        self.assertIsNone(ws.read_plan_approval_journal(item.root))
        self.assertEqual(commit, self.scratch.head())
        rcid = item.entry()["plan_approval"]["approved_review_content_id"]
        self.assertEqual(cp5_approval_commit_count(item, rcid), 1)
        self.assertTrue(ws.implementing_entry_reachable(item.root, item.entry(), item.base_commit))
        return commit

    def assert_refused_before_mutation(self, exc_type, action, contains=()):
        item = self.item
        head = self.scratch.head()
        index = self.scratch.git("diff", "--name-only", "--cached", "HEAD").stdout
        state = self.scratch.read(CP4_STATE_REL)
        with self.assertRaises(exc_type) as ctx:
            action()
        for text in contains:
            self.assertIn(text, str(ctx.exception))
        self.assertEqual(self.scratch.head(), head)
        self.assertEqual(self.scratch.git("diff", "--name-only", "--cached", "HEAD").stdout, index)
        self.assertEqual(self.scratch.read(CP4_STATE_REL), state)
        self.assertIsNone(ws.read_plan_approval_journal(item.root))
        return ctx.exception

    def approve_with_companion(self, content="companion v1\n"):
        """First approval of an item whose declaration protects a new,
        intent-to-add companion -- `HEAD` then carries a declaration that
        protects it."""
        self.plan_with((CP5_COMPANION,), {CP5_COMPANION: content}, (CP5_COMPANION,))
        return self.approve()

    def amend_to(self, protected_extra, plan_body, before_generation=None):
        """`/request-plan-amendment`, then an amended `/milestone-plan` whose
        declaration protects `protected_extra`, then the two reviews."""
        item = self.item
        item.request_amendment("companion change")
        if before_generation is not None:
            before_generation()
        item.amend_plan(2, plan_body=plan_body, artifacts=cp5_declarations(item, protected_extra))
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=2)
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")


class PlanApprovalClosureMembers(_PlanApprovalClosureCase):
    """Section 5.4 item 1: the member set, through the command."""

    def test_intent_to_add_companion_is_committed_and_verifies(self):
        """The previously observed case (`AbsentProtectedPathError` after
        the commit under 2.5.1): an intent-to-add declared-protected
        companion is now a member, committed once, and verifies."""
        commit = self.approve_with_companion()
        self.assertIn(CP5_COMPANION, cp5_changed(self.item, commit))
        self.assertEqual(self.scratch.git("show", f"{commit}:{CP5_COMPANION}").stdout, "companion v1\n")

    def test_the_2_5_1_member_set_would_have_omitted_the_companion(self):
        """Control arm: the four base members alone never name it."""
        self.plan_with((CP5_COMPANION,), {CP5_COMPANION: "companion\n"}, (CP5_COMPANION,))
        plan = ws.resolve_fresh_plan_approval_members(self.item.root, self.item.wid)
        item = self.item
        legacy = {item.plan_path, item.registry_path, item.mapping_path, CP4_STATE_REL, item.artifacts_path}
        self.assertNotIn(CP5_COMPANION, legacy)
        self.assertIn(CP5_COMPANION, plan.paths)
        self.assertIn(CP5_COMPANION, plan.protected_paths)
        self.assertEqual(plan.paths[:3], (item.plan_path, item.registry_path, item.mapping_path))

    def test_fully_untracked_companion_is_committed(self):
        self.plan_with((CP5_COMPANION,), {CP5_COMPANION: "untracked companion\n"})
        self.assertIn(CP5_COMPANION, self.scratch.git("ls-files", "--others", "--exclude-standard").stdout)
        commit = self.approve()
        self.assertIn(CP5_COMPANION, cp5_changed(self.item, commit))

    def test_tracked_and_edited_companion_is_committed(self):
        self.scratch.write(CP5_COMPANION, "pre-existing design notes\n")
        self.scratch.commit("docs: companion before this item's plan")
        self.item.base_commit = self.scratch.head()
        self.plan_with((CP5_COMPANION,), {CP5_COMPANION: "pre-existing design notes, edited\n"})
        commit = self.approve()
        self.assertIn(CP5_COMPANION, cp5_changed(self.item, commit))
        self.assertEqual(self.scratch.git("show", f"{commit}:{CP5_COMPANION}").stdout,
                         "pre-existing design notes, edited\n")

    def test_first_approval_has_an_empty_removal_set(self):
        """`LPR-R1-009`: no declaration at `HEAD`, so no removals."""
        self.plan_with((CP5_COMPANION,), {CP5_COMPANION: "c\n"}, (CP5_COMPANION,))
        self.assertFalse(self.scratch.git("cat-file", "-e", f"HEAD:{self.item.artifacts_path}",
                                          check=False).returncode == 0)
        plan = ws.resolve_fresh_plan_approval_members(self.item.root, self.item.wid)
        self.assertEqual(plan.removal_paths, ())

    def test_companion_dropped_from_the_declaration_is_committed_as_a_deletion(self):
        first = self.approve_with_companion()
        self.assertIsNotNone(cp5_tree_blob(self.item, first, CP5_COMPANION))

        def drop():
            (self.item.root / CP5_COMPANION).unlink()
        self.amend_to((), "Plan body, companion dropped.\n", before_generation=drop)
        plan = ws.resolve_fresh_plan_approval_members(self.item.root, self.item.wid)
        self.assertEqual(plan.removal_paths, (CP5_COMPANION,))
        commit = self.approve()
        self.assertIn(CP5_COMPANION, cp5_changed(self.item, commit))
        self.assertIsNone(cp5_tree_blob(self.item, commit, CP5_COMPANION))

    def test_rename_via_mv_is_a_removal_plus_an_addition(self):
        self.approve_with_companion()

        def rename():
            os.rename(self.item.root / CP5_COMPANION, self.item.root / CP5_RENAMED)
        self.amend_to((CP5_RENAMED,), "Plan body, companion renamed.\n", before_generation=rename)
        plan = ws.resolve_fresh_plan_approval_members(self.item.root, self.item.wid)
        self.assertEqual(plan.removal_paths, (CP5_COMPANION,))
        self.assertIn(CP5_RENAMED, plan.protected_paths)
        commit = self.approve()
        self.assertIsNone(cp5_tree_blob(self.item, commit, CP5_COMPANION))
        self.assertEqual(self.scratch.git("show", f"{commit}:{CP5_RENAMED}").stdout, "companion v1\n")

    def test_rename_via_git_mv_refuses_with_the_named_remedy_then_succeeds(self):
        self.approve_with_companion()

        def rename():
            self.scratch.git("mv", CP5_COMPANION, CP5_RENAMED)
        self.amend_to((CP5_RENAMED,), "Plan body, companion git-mv'd.\n", before_generation=rename)
        self.assertTrue(self.scratch.git("diff", "--name-only", "--cached", "HEAD").stdout.strip())
        self.assert_refused_before_mutation(
            ws.DirtyIndexBeforeStagingError, self.item.approve_plan,
            contains=("git mv", "git --literal-pathspecs restore --staged"),
        )
        # The named remedy, exactly as the refusal prints it: unstage both
        # sides, keep the rename.
        self.scratch.git(
            "--literal-pathspecs", "restore", "--staged", "--", CP5_COMPANION, CP5_RENAMED
        )
        commit = self.approve()
        self.assertIsNone(cp5_tree_blob(self.item, commit, CP5_COMPANION))
        self.assertIsNotNone(cp5_tree_blob(self.item, commit, CP5_RENAMED))

    def test_de_protected_path_recreated_in_the_worktree_is_not_a_removal(self):
        """`LPR-R4-005`: dropped from the declaration, deleted, then
        re-created under an excluded classification -- no deletion is
        staged, `HEAD`'s copy survives, the proofs pass, the approval
        succeeds. (The same path left deleted is the dropped-companion row
        above.)"""
        first = self.approve_with_companion()
        head_blob = cp5_tree_blob(self.item, first, CP5_COMPANION)

        def drop_and_recreate():
            (self.item.root / CP5_COMPANION).unlink()
            self.scratch.write(CP5_COMPANION, "re-created, now excluded\n")
        self.amend_to((), "Plan body, companion de-protected.\n", before_generation=drop_and_recreate)
        plan = ws.resolve_fresh_plan_approval_members(self.item.root, self.item.wid)
        self.assertEqual(plan.removal_paths, ())
        self.assertNotIn(CP5_COMPANION, plan.paths)
        commit = self.approve()
        self.assertNotIn(CP5_COMPANION, cp5_changed(self.item, commit))
        self.assertEqual(cp5_tree_blob(self.item, commit, CP5_COMPANION), head_blob)
        self.assertEqual(self.scratch.read(CP5_COMPANION), "re-created, now excluded\n")


class PlanApprovalClosureFreshness(_PlanApprovalClosureCase):
    """Section 5.4 item 2: freshness per member kind, before any mutation."""

    def test_companion_edited_after_generation_refuses_before_any_mutation(self):
        self.plan_with((CP5_COMPANION,), {CP5_COMPANION: "reviewed\n"}, (CP5_COMPANION,))
        self.scratch.write(CP5_COMPANION, "edited after the bundle\n")
        self.assert_refused_before_mutation(ws.ReviewedContentDriftError, self.item.approve_plan)
        # The per-member check refuses it too, independently of step 2.
        with self.assertRaises(ws.ReviewedContentDriftError) as ctx:
            ws.resolve_fresh_plan_approval_members(self.item.root, self.item.wid)
        self.assertIn(f"{CP5_COMPANION} (protected member)", str(ctx.exception))

    def test_failed_regeneration_then_approve_refuses_at_step_2(self):
        """`LPR-R1-002`/`LPR-R3-002`, in the real command order: refresh
        `plan-inputs/`, edit a protected member, force a staging-generation
        failure. `current/` is byte-identical, and `/approve-review plan`
        refuses at step 2 before any mutation."""
        item = self.item
        self.plan_with()
        bundle = item.root / item.bundle_dir(stage="plan")
        before = {p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
        item.sim.write(item.plan_path, item.sim.read(item.plan_path) + "\nAn edit after review.\n")
        proc = item.generate_plan_bundle(check=False, review_request=(
            f"# Review request\n\nstage: plan\nwork item: {item.wid}\n"
            f"review_content_id: {'0' * 64}\n"
        ))
        self.assertNotEqual(proc.returncode, 0)
        after = {p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assert_refused_before_mutation(ws.ReviewedContentDriftError, item.approve_plan)

    def test_artifacts_byte_edit_outside_its_key_sets_refuses_at_the_member_check(self):
        """The fresh id does not hash the declaration's bytes, so step 2
        passes; the per-member check refuses under the same name."""
        item = self.item
        self.plan_with()
        path = item.root / item.artifacts_path
        path.write_text(json.dumps(json.loads(path.read_text()), indent=4) + "\n")
        ws.assert_plan_review_bundle_bound(item.root, item.wid)
        exc = self.assert_refused_before_mutation(ws.ReviewedContentDriftError, item.approve_plan)
        self.assertIn("artifacts declaration member", str(exc))
        self.assertIsInstance(exc.__cause__, fingerprint.StaleArtifactsDeclarationError)

    def test_unchanged_tracked_companion_approves_against_its_base_commit_blob(self):
        """`LPR-R5-002`: present, tracked and unchanged since
        `base_commit`, so never captured -- compared against its
        `base_commit` blob, approves, and no content change is staged."""
        item = self.item
        self.scratch.write(CP5_COMPANION, "pre-existing design input\n")
        item.base_commit = self.scratch.commit("docs: design input before this item")
        self.plan_with((CP5_COMPANION,))
        self.assertFalse((item.root / item.bundle_dir(stage="plan") / "files" / CP5_COMPANION).exists())
        commit = self.approve()
        self.assertNotIn(CP5_COMPANION, cp5_changed(item, commit))

    def test_uncaptured_member_differing_from_base_commit_refuses(self):
        """Edited after generation, no capture: refuses (step 2 first; the
        per-member check independently)."""
        item = self.item
        self.scratch.write(CP5_COMPANION, "pre-existing design input\n")
        item.base_commit = self.scratch.commit("docs: design input before this item")
        self.plan_with((CP5_COMPANION,))
        self.scratch.write(CP5_COMPANION, "edited after generation\n")
        self.assert_refused_before_mutation(ws.ReviewedContentDriftError, item.approve_plan)
        with self.assertRaises(ws.ReviewedContentDriftError) as ctx:
            ws.resolve_fresh_plan_approval_members(item.root, item.wid)
        self.assertIn("base_commit", str(ctx.exception))

    def test_uncaptured_member_reverted_to_its_base_commit_blob_approves(self):
        """A commit after `base_commit` touched the member and the worktree
        reverted it: differs from `HEAD`, equals its `base_commit` blob,
        has no capture -- approves (the `HEAD`-keyed alternative would have
        needed a second source)."""
        item = self.item
        self.scratch.write(CP5_COMPANION, "pre-existing design input\n")
        item.base_commit = self.scratch.commit("docs: design input before this item")
        self.scratch.write(CP5_COMPANION, "touched after base\n")
        self.scratch.commit("docs: a post-base touch")
        self.scratch.write(CP5_COMPANION, "pre-existing design input\n")
        self.plan_with((CP5_COMPANION,))
        self.assertFalse((item.root / item.bundle_dir(stage="plan") / "files" / CP5_COMPANION).exists())
        commit = self.approve()
        self.assertIn(CP5_COMPANION, cp5_changed(item, commit))
        self.assertEqual(self.scratch.git("show", f"{commit}:{CP5_COMPANION}").stdout,
                         "pre-existing design input\n")

    def test_member_absent_at_base_commit_with_no_capture_refuses_directly(self):
        """Defense-in-depth pin, driven directly: a member that appeared
        after generation (step 2's id check refuses it in the command)."""
        item = self.item
        self.plan_with()
        declared = cp5_declarations(item, (CP5_COMPANION,))
        self.scratch.write(CP5_COMPANION, "appeared after generation\n")
        plan = fingerprint.PlanApprovalCommitPlan(
            (item.plan_path, CP5_COMPANION), None, None,
            protected_paths=(item.plan_path, CP5_COMPANION),
        )
        self.assertIn(CP5_COMPANION, declared["plan_stage"]["protected_paths"])
        with self.assertRaises(ws.ReviewedContentDriftError) as ctx:
            ws.assert_plan_approval_members_fresh(item.root, item.wid, plan)
        self.assertIn("appeared after the bundle was generated", str(ctx.exception))

    def test_removal_member_still_in_the_bound_bundle_refuses(self):
        """`LPR-R4-005`: the amended declaration no longer protects the
        companion, but the file was still present (now excluded) when the
        bound bundle was generated, so the bundle captured it; the author
        deleted it afterwards. The fresh id is unchanged -- step 2 passes --
        and only the per-member check refuses: the reviewer saw the file,
        so its deletion cannot be committed without a regeneration."""
        item = self.item
        self.approve_with_companion()
        self.amend_to((), "Plan body, companion de-protected.\n")
        bundle = item.root / item.bundle_dir(stage="plan")
        self.assertTrue((bundle / "files" / CP5_COMPANION).is_file())
        (item.root / CP5_COMPANION).unlink()
        ws.assert_plan_review_bundle_bound(item.root, item.wid)
        exc = self.assert_refused_before_mutation(ws.ReviewedContentDriftError, item.approve_plan)
        self.assertIn(f"{CP5_COMPANION} (removal member)", str(exc))


def cp5_changed_exact(item, commit):
    """`cp5_changed`, NUL-delimited: the exact committed path set, never
    `core.quotePath`'s display form or a whitespace split."""
    out = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-z", "-r", commit],
        cwd=item.root, capture_output=True, check=True,
    ).stdout
    return {os.fsdecode(raw) for raw in out.split(b"\0") if raw}


class PlanApprovalClosureLiteralPaths(_PlanApprovalClosureCase):
    """Implementation review round 2, `I1`: the closure machinery is total
    over the path strings a declaration admits. A protected member is a
    literal path at every Git boundary (`--literal-pathspecs`) and is read
    back NUL-delimited, so a pathspec-metacharacter or non-ASCII member is
    staged, committed and verified as exactly itself, and no non-member is
    touched. Each row carries a tracked, worktree-edited decoy that a
    pathspec reading of the member would reach."""

    DECOY = "docs/ai-workflow/DECOY.md"

    def setUp(self):
        super().setUp()
        self.scratch.write(self.DECOY, "decoy at base\n")
        self.item.base_commit = self.scratch.commit("docs: a decoy a glob member would match")
        self.scratch.write(self.DECOY, "decoy edited, never staged\n")

    def assert_decoy_untouched(self, commit):
        self.assertNotIn(self.DECOY, cp5_changed_exact(self.item, commit))
        self.assertEqual(self.scratch.git("show", f"{commit}:{self.DECOY}").stdout, "decoy at base\n")
        self.assertEqual(self.scratch.read(self.DECOY), "decoy edited, never staged\n")
        self.assertEqual(self.scratch.git("diff", "--name-only", "--cached", "HEAD").stdout, "")

    def approve_member(self, member, content):
        self.plan_with((member,), {member: content}, (member,))
        commit = self.approve()
        changed = cp5_changed_exact(self.item, commit)
        self.assertIn(member, changed)
        self.assertEqual(self.scratch.git("show", f"{commit}:{member}").stdout, content)
        self.assert_decoy_untouched(commit)
        return commit, changed

    def test_ascii_control_member_is_committed_exactly(self):
        _commit, changed = self.approve_member(CP5_COMPANION, "control\n")
        self.assertLessEqual(changed, {
            self.item.plan_path, self.item.registry_path, self.item.mapping_path,
            CP4_STATE_REL, self.item.artifacts_path, CP5_COMPANION,
        })

    def test_control_and_quoted_character_members_are_committed_exactly(self):
        """Round 3, O2: characters `core.quotePath` C-quotes even in ASCII
        -- a tab, a double quote, a backslash -- compare as themselves."""
        for member in ("docs/ai-workflow/tab\there.md", 'docs/ai-workflow/say "hi".md',
                       "docs/ai-workflow/back\\slash.md"):
            with self.subTest(member=member):
                self.setUp()
                self.approve_member(member, f"literal {member}\n")
                self.scratch.cleanup()

    def test_noglob_pathspec_mode_neither_aborts_nor_widens_approval(self):
        """Round 3, O1: Git refuses `--literal-pathspecs` alongside a
        global pathspec mode, so `GIT_NOGLOB_PATHSPECS=1` -- under which
        2.5.1 approved -- aborted 2.6.0's approval. Staging and the
        metadata checks now drop that mode and the glob member stays a
        literal. (`GIT_GLOB_PATHSPECS`/`GIT_ICASE_PATHSPECS` are not a row:
        Git's `ls-tree` rejects that magic outright, so every release's
        plain `ls-tree -- <path>` reads -- the identity-reference scan
        among them -- already refused under them, 2.5.1 included.)"""
        with mock.patch.dict(os.environ, {"GIT_NOGLOB_PATHSPECS": "1"}):
            self.approve_member("docs/ai-workflow/*.md", "a file literally named *.md\n")

    def test_literal_reads_drop_every_conflicting_global_pathspec_mode(self):
        """Round 3, O1, per call site: each literal declared-path read
        runs under every conflicting global mode rather than exiting 128."""
        self.scratch.write("docs/ai-workflow/*.md", "literal\n")
        head = self.scratch.commit("a literal glob-named file")
        for mode in fingerprint.CONFLICTING_PATHSPEC_ENV:
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {mode: "1"}):
                self.assertTrue(fingerprint._snapshot_commit(self.scratch.root, head, "docs/ai-workflow/*.md")["exists"])
                fingerprint._validate_plan_stage_metadata_path(
                    self.scratch.root, "plan_path", "docs/ai-workflow/*.md", None)
                fingerprint._validate_plan_stage_metadata_path(
                    self.scratch.root, "plan_path", "docs/ai-workflow/*.md", head)

    def test_glob_metacharacter_member_is_committed_as_a_literal(self):
        member = "docs/ai-workflow/*.md"
        _commit, changed = self.approve_member(member, "a file literally named *.md\n")
        self.assertEqual({p for p in changed if p.endswith(".md") and "WI" not in p
                          and p not in (self.item.plan_path,)}, {member})

    def test_bracket_and_magic_prefix_members_are_committed_as_literals(self):
        # What a pathspec reading of `:WI_MAGIC.md` would name instead,
        # tracked at base (a worktree edit to it would be an unclassified
        # change): that reading stages it, not the literal member.
        self.scratch.write(self.DECOY, "decoy at base\n")
        self.scratch.write("WI_MAGIC.md", "magic target at base\n")
        self.item.base_commit = self.scratch.commit("docs: what a ':' pathspec would name")
        self.scratch.write(self.DECOY, "decoy edited, never staged\n")
        members = ("docs/ai-workflow/[DW]ECOY.md", ":WI_MAGIC.md")
        self.plan_with(members, {m: f"literal {m}\n" for m in members}, members)
        commit = self.approve()
        changed = cp5_changed_exact(self.item, commit)
        for member in members:
            self.assertIn(member, changed)
            self.assertEqual(self.scratch.git("show", f"{commit}:{member}").stdout, f"literal {member}\n")
        self.assertNotIn("WI_MAGIC.md", changed)
        self.assertEqual(self.scratch.git("show", f"{commit}:WI_MAGIC.md").stdout, "magic target at base\n")
        self.assert_decoy_untouched(commit)

    def test_absent_glob_metacharacter_removal_member_deletes_only_itself(self):
        """The reproduced case: a dropped `*.md` member is staged as a
        deletion of exactly that file -- no other Markdown path is
        unstaged, and the approval commits."""
        member = "docs/ai-workflow/*.md"
        first, _ = self.approve_member(member, "literal glob member\n")
        self.assertIsNotNone(cp5_tree_blob(self.item, first, member))

        def drop():
            (self.item.root / member).unlink()
        self.amend_to((), "Plan body, glob member dropped.\n", before_generation=drop)
        plan = ws.resolve_fresh_plan_approval_members(self.item.root, self.item.wid)
        self.assertEqual(plan.removal_paths, (member,))
        commit = self.approve()
        changed = cp5_changed_exact(self.item, commit)
        self.assertIn(member, changed)
        self.assertIsNone(cp5_tree_blob(self.item, commit, member))
        self.assertLessEqual(changed, set(plan.paths))
        self.assert_decoy_untouched(commit)

    def test_non_ascii_member_is_committed_and_verified(self):
        member = "docs/ai-workflow/d\u00e9sign.md"
        self.assertEqual(self.scratch.git("config", "--get", "core.quotePath", check=False).stdout, "")
        _commit, changed = self.approve_member(member, "d\u00e9sign notes\n")
        self.assertNotIn('"docs/ai-workflow/d\\303\\251sign.md"', changed)


class AmendedApprovalLiteralMetadataPath(_PlanApprovalClosureCase):
    """Round 3, `I1-residual`: an amended plan approval whose declared
    `plan_path` has a leading `:`. `load_pre_amendment_snapshot`'s
    pinned-blob cross-check reads that path literally, never as pathspec
    magic naming the tracked decoy `proc-item-plan.md`."""

    def setUp(self):
        self.scratch = Scratch()
        self.addCleanup(self.scratch.cleanup)
        self.item = Item(self.scratch, self.work_item_type)
        self.item.plan_path = ":proc-item-plan.md"
        self.scratch.write("proc-item-plan.md", "decoy a ':' pathspec would name\n")
        self.item.seed()

    def test_amended_approval_cross_checks_the_literal_pre_amendment_plan(self):
        item = self.item
        self.plan_with()
        first = self.approve()
        pre_plan = self.scratch.git("show", f"{first}:{item.plan_path}").stdout
        self.amend_to((), "Plan body, amended.\n")
        entry = item.entry()["amendment_history"][-1]
        pre_plan_text, _ = ws.load_pre_amendment_snapshot(
            item.root, item.wid, item.plan_path, item.registry_path, entry,
        )
        self.assertEqual(pre_plan_text, pre_plan)
        self.approve()
        self.assertEqual(self.scratch.git("show", "HEAD:proc-item-plan.md").stdout,
                         "decoy a ':' pathspec would name\n")


class DeclaredPathGitReads(unittest.TestCase):
    """Round 3, `I1-residual` and its re-sweep, driven directly against a
    real repository: each declared-path read is a literal whatever the
    path's leading characters."""

    def setUp(self):
        self.scratch = Scratch()
        self.addCleanup(self.scratch.cleanup)

    def blob(self, rel):
        return self.scratch.git("rev-parse", f"HEAD:{rel}").stdout.strip()

    def test_pre_amendment_snapshot_reads_pathspec_shaped_paths_literally(self):
        registry = {"checkpoints": [], "marker": "literal"}
        self.scratch.write("plan.md", "decoy plan\n")
        self.scratch.write("reg.json", json.dumps({"marker": "decoy"}))
        self.scratch.write(":reg.json", json.dumps(registry))
        plans = (":plan.md", ":(glob)*.md", ":!plan.md")
        for plan in plans:
            self.scratch.write(plan, f"literal {plan}\n")
        commit = self.scratch.commit("pathspec-shaped metadata paths", paths=["."])
        for plan in plans:
            with self.subTest(plan_path=plan):
                entry = {
                    "pre_amendment_approval_commit": commit,
                    "superseded_plan_approval": {"review_content_manifest": [
                        {"path": plan, "blob": self.blob(plan)},
                        {"path": ":reg.json", "blob": self.blob(":reg.json")},
                    ]},
                }
                text, pre_registry = ws.load_pre_amendment_snapshot(
                    self.scratch.root, "wi", plan, ":reg.json", entry,
                )
                self.assertEqual(text, f"literal {plan}\n")
                self.assertEqual(pre_registry, registry)

    def test_staged_blob_check_reads_a_stage_number_shaped_member_literally(self):
        """`:0:x.md` is index stage 0 of `x.md` -- a member named `0:x.md`
        is read at `:0:0:x.md`, never at `:0:x.md`."""
        self.scratch.write("x.md", "not the member\n")
        self.scratch.write("0:x.md", "the member\n")
        self.scratch.git("add", "--", "x.md", "0:x.md")
        ws.verify_staged_blob_sha256(
            self.scratch.root, "0:x.md", hashlib.sha256(b"the member\n").hexdigest(),
        )
        with self.assertRaises(ws.StagedBlobMismatchError):
            ws.verify_staged_blob_sha256(
                self.scratch.root, "0:x.md", hashlib.sha256(b"not the member\n").hexdigest(),
            )


class PlanApprovalClosureProof(_PlanApprovalClosureCase):
    """Section 5.4 item 3: the write-tree proof, before the commit."""

    def test_proof_failure_is_a_not_committed_rollback_not_an_amend(self):
        item = self.item
        self.plan_with()
        head = self.scratch.head()

        def tamper():
            tampered = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=item.root, input=b"tampered\n",
                capture_output=True, check=True,
            ).stdout.decode().strip()
            self.scratch.git("update-index", "--cacheinfo", f"100644,{tampered},{item.plan_path}")
        with self.assertRaises(ws.PlanApprovalClosureProofError):
            item.approve_plan(before_proof=tamper)
        self.assertEqual(self.scratch.head(), head)
        self.assertEqual(self.scratch.git("diff", "--name-only", "--cached", "HEAD").stdout.strip(), "")
        self.assertIsNone(ws.read_plan_approval_journal(item.root))
        self.assertEqual(item.entry()["phase"], "AWAITING_PLAN_APPROVAL")
        self.assertEqual(cp5_approval_commit_count(item), 0)
        # Retried untampered, it commits once.
        item.stage_plan_files()
        self.approve()

    def test_commit_source_identity_accepts_a_bare_tree(self):
        """Item 3's generalization to a tree-ish: the approval commit's
        own tree recomputes to the same id as the commit."""
        item = self.item
        self.plan_with()
        commit = self.approve()
        tree = self.scratch.git("rev-parse", f"{commit}^{{tree}}").stdout.strip()
        self.assertEqual(self.scratch.git("cat-file", "-t", tree).stdout.strip(), "tree")
        by_tree, _ = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
            item.root, item.wid, tree, base=item.base_commit,
        )
        by_commit, _ = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
            item.root, item.wid, commit, base=item.base_commit,
        )
        self.assertEqual(by_tree, by_commit)
        self.assertEqual(by_tree, item.entry()["plan_approval"]["approved_review_content_id"])


class PlanApprovalCommittedTruth(_PlanApprovalClosureCase):
    """Section 5.4 items 4-6 (the section 3.5 fix): verification from the
    committed transaction, the named error, and the amend gate."""

    def test_first_approval_with_no_state_file_at_head_commits_once_and_verifies(self):
        """`v2.3.1-003` together with the section 3.5 `TypeError` case."""
        item = self.item
        self.scratch.git("rm", "-q", "--cached", "--", CP4_STATE_REL)
        self.scratch.git("commit", "-q", "-m", "chore: untrack the state file")
        item.base_commit = self.scratch.head()
        self.assertNotEqual(self.scratch.git("cat-file", "-e", f"HEAD:{CP4_STATE_REL}",
                                             check=False).returncode, 0)
        self.plan_with()
        pre_commit_entry = item.entry()
        self.assertIsNone(pre_commit_entry["plan_approval"])
        commit = self.approve()
        self.assertIn(CP4_STATE_REL, cp5_changed(item, commit))
        # The 2.5.1 data flow -- the pre-commit work item -- is now a named
        # verifier-input error, never a TypeError, and never an amend.
        with self.assertRaises(ws.MissingApprovalRecordError) as ctx:
            ws.verify_post_approval_manifest_match(
                item.root, pre_commit_entry, stage="plan", base_commit=item.base_commit, commit=commit,
            )
        self.assertEqual(ws.classify_post_commit_verification_failure(ctx.exception),
                         ws.POST_COMMIT_FAILURE_RECORD_OR_INPUT)

    def test_prior_stale_approval_verifies_with_no_false_mismatch(self):
        item = self.item
        self.plan_with()
        digest, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(item.root, item.wid)
        old = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan", user_confirmation=f"plan {item.wid}",
            now=item.now(), reviewed_bundle_id="1" * 64, approved_review_content_id="2" * 64,
            review_content_manifest=projection["review_content_manifest"],
        )
        old["status"] = "STALE"

        def seed_stale(state):
            state = copy.deepcopy(state)
            state["work_items"][item.wid]["plan_approval"] = old
            return state
        item.tx(seed_stale)
        pre_commit_entry = item.entry()
        commit = item.approve_plan(stop_after="commit")
        # 2.5.1's call shape: a false mismatch against the *old* id ...
        with self.assertRaises(ws.PostApprovalManifestMismatchError):
            ws.verify_post_approval_manifest_match(
                item.root, pre_commit_entry, stage="plan", base_commit=item.base_commit, commit=commit,
            )
        # ... which, given the pinned id, is now a named record error.
        with self.assertRaises(ws.CommittedApprovalRecordMismatchError) as ctx:
            ws.verify_post_approval_manifest_match(
                item.root, pre_commit_entry, stage="plan", base_commit=item.base_commit, commit=commit,
                expected_review_content_id=digest,
            )
        self.assertEqual(ws.classify_post_commit_verification_failure(ctx.exception),
                         ws.POST_COMMIT_FAILURE_RECORD_OR_INPUT)
        owner = ws.read_plan_approval_journal(item.root)["owner_token"]
        self.assertEqual(item.complete_plan_approval(owner), commit)
        self.assertEqual(item.entry()["plan_approval"]["status"], "CURRENT")
        self.assertEqual(cp5_approval_commit_count(item), 1)

    def test_prior_superseded_approval_verifies_with_no_false_mismatch(self):
        item = self.item
        first = self.approve_with_companion()
        self.amend_to((CP5_COMPANION,), "Plan body, amended.\n")
        self.assertEqual(item.entry()["plan_approval"]["status"], "SUPERSEDED")
        commit = self.approve()
        self.assertNotEqual(commit, first)
        self.assertEqual(cp5_approval_commit_count(item), 2)
        self.assertEqual(item.entry()["plan_approval"]["status"], "CURRENT")

    def test_crash_after_commit_resumes_in_session(self):
        item = self.item
        self.plan_with()
        commit = item.approve_plan(stop_after="commit")
        journal = ws.read_plan_approval_journal(item.root)
        self.assertEqual(ws.classify_plan_approval_outcome(item.root, journal),
                         ws.PLAN_APPROVAL_OUTCOME_COMMITTED)
        self.assertEqual(item.complete_plan_approval(journal["owner_token"]), commit)
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")
        self.assertIsNone(ws.read_plan_approval_journal(item.root))
        self.assertEqual(cp5_approval_commit_count(item), 1)

    def test_crash_after_commit_resumes_via_takeover(self):
        item = self.item
        self.plan_with()
        commit = item.approve_plan(stop_after="commit")
        evidence = ws.plan_approval_takeover_evidence(item.root)
        self.assertEqual(evidence["outcome"], ws.PLAN_APPROVAL_OUTCOME_COMMITTED)
        new_token = ws.take_over_plan_approval_transaction(
            item.root, work_item_id=item.wid, now=item.now(),
            user_authorization=ws.plan_approval_takeover_authorization_literal(evidence),
            evidence=evidence,
        )
        self.assertNotEqual(new_token, evidence["owner_token"])
        self.assertEqual(item.complete_plan_approval(new_token), commit)
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")
        self.assertEqual(cp5_approval_commit_count(item), 1)

    def test_crash_after_materialize_before_close_takes_the_noop_path(self):
        item = self.item
        self.plan_with()
        commit = item.approve_plan(stop_after="materialize")
        self.assertIsNotNone(ws.read_plan_approval_journal(item.root))
        materialized = self.scratch.read(CP4_STATE_REL)
        journal = ws.read_plan_approval_journal(item.root)
        pre_state = json.loads(base64.b64decode(journal["pre_procedure_state_b64"]))
        post_state = json.loads(base64.b64decode(journal["expected_post_state_b64"]))
        self.assertEqual(ws.classify_plan_approval_materialize_target(item.root, item.wid, pre_state, post_state),
                         ws.PLAN_APPROVAL_MATERIALIZE_NOOP)
        self.assertEqual(item.complete_plan_approval(journal["owner_token"]), commit)
        self.assertEqual(self.scratch.read(CP4_STATE_REL), materialized)
        self.assertIsNone(ws.read_plan_approval_journal(item.root))
        self.assertEqual(cp5_approval_commit_count(item), 1)

    def test_record_or_input_error_never_amends(self):
        """The section 3.5 data flow after a crash: the pre-commit work item
        reaches the verifier. Named error, classified record/input, `HEAD`
        unchanged, one approval commit -- and the real resume converges."""
        item = self.item
        self.plan_with()
        pre_commit_entry = item.entry()
        commit = item.approve_plan(stop_after="commit")
        with self.assertRaises(ws.MissingApprovalRecordError) as ctx:
            ws.verify_post_approval_manifest_match(
                item.root, pre_commit_entry, stage="plan", base_commit=item.base_commit, commit=commit,
            )
        self.assertEqual(ws.classify_post_commit_verification_failure(ctx.exception),
                         ws.POST_COMMIT_FAILURE_RECORD_OR_INPUT)
        owner = ws.read_plan_approval_journal(item.root)["owner_token"]
        for error in (ws.MissingApprovalRecordError("record"), KeyError("input"),
                      TypeError("verifier")):
            with mock.patch.object(ws, "verify_plan_approval_commit", side_effect=error):
                with self.assertRaises(type(error)):
                    item.complete_plan_approval(owner)
            self.assertEqual(self.scratch.head(), commit)
            self.assertEqual(cp5_approval_commit_count(item), 1)
        self.assertEqual(item.complete_plan_approval(owner), commit)
        self.assertEqual(cp5_approval_commit_count(item), 1)

    def _install_pre_commit_hook(self, body):
        hook = self.item.root / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nrm -f \"$0\"\n" + body)
        hook.chmod(0o755)

    def test_hook_tree_content_defect_takes_the_single_amend(self):
        """A one-shot `pre-commit` hook stages other bytes for a protected
        member after the proof: `TREE_CONTENT`, one amend, same parent,
        still exactly one approval commit."""
        item = self.item
        self.plan_with((CP5_COMPANION,), {CP5_COMPANION: "reviewed companion\n"}, (CP5_COMPANION,))
        head = self.scratch.head()
        self._install_pre_commit_hook(
            "blob=$(printf 'hook rewrite\\n' | git hash-object -w --stdin)\n"
            f"git update-index --cacheinfo 100644,$blob,{CP5_COMPANION}\n"
        )
        commit = item.approve_plan(stop_after="commit")
        self.assertEqual(self.scratch.git("show", f"{commit}:{CP5_COMPANION}").stdout, "hook rewrite\n")
        journal = ws.read_plan_approval_journal(item.root)
        with self.assertRaises(Exception) as ctx:
            ws.verify_plan_approval_commit(item.root, journal, commit)
        self.assertEqual(ws.classify_post_commit_verification_failure(ctx.exception),
                         ws.POST_COMMIT_FAILURE_TREE_CONTENT)
        amended = item.complete_plan_approval(journal["owner_token"])
        self.assertNotEqual(amended, commit)
        self.assertEqual(self.scratch.git("rev-parse", f"{amended}^").stdout.strip(), head)
        self.assertEqual(self.scratch.git("show", f"{amended}:{CP5_COMPANION}").stdout,
                         "reviewed companion\n")
        self.assertEqual(cp5_approval_commit_count(item), 1)
        self.assertEqual(item.entry()["phase"], "IMPLEMENTING")

    def test_hook_extra_path_stops_without_amending(self):
        """An extra (classified) path in the commit is a membership defect
        the amend cannot correct: `RECORD_OR_INPUT`, stop, `HEAD`
        unchanged, the journal left for a human."""
        item = self.item
        self.plan_with()
        self._install_pre_commit_hook(
            "printf 'roadmap edit\\n' >> docs/ROADMAP.md\ngit add docs/ROADMAP.md\n"
        )
        commit = item.approve_plan(stop_after="commit")
        journal = ws.read_plan_approval_journal(item.root)
        with self.assertRaises(ws.CommittedPathSetMismatchError) as ctx:
            item.complete_plan_approval(journal["owner_token"])
        self.assertEqual(ws.classify_post_commit_verification_failure(ctx.exception),
                         ws.POST_COMMIT_FAILURE_RECORD_OR_INPUT)
        self.assertEqual(self.scratch.head(), commit)
        self.assertIsNotNone(ws.read_plan_approval_journal(item.root))
        self.assertEqual(cp5_approval_commit_count(item), 1)

    def test_none_record_raises_the_named_error(self):
        for work_item in ({"work_item_id": "x", "plan_approval": None}, {"work_item_id": "x"},
                          {"work_item_id": "x", "plan_approval": {"approved_review_content_id": None}}):
            with self.assertRaises(ws.MissingApprovalRecordError):
                ws.verify_post_approval_manifest_match(
                    self.item.root, work_item, stage="plan", base_commit="HEAD", commit="HEAD",
                )

    def test_implementation_stage_helper_has_the_same_named_error_guard(self):
        with self.assertRaises(ws.MissingApprovalRecordError):
            ws.verify_post_approval_manifest_match(
                self.item.root, {"work_item_id": "x", "technical_approval": None},
                stage="implementation", base_commit="HEAD", commit="HEAD",
            )

    def test_implementation_stage_approval_is_otherwise_unchanged(self):
        self.reach_technical_approved()
        self.assertEqual(self.item.entry()["phase"], "AWAITING_FUNCTIONAL_REVIEW")


# ===========================================================================
# workflow-2.6.0, `D-Repo-Global-Lifecycle` (CP6): one resolution per
# amendment sequence, driven through `/approve-review plan`'s real flow in
# real linked worktrees and real processes.
# ===========================================================================

_CP6_APPROVE_WORKER_SOURCE = """
import json
import os
import signal
import sys
import time
from pathlib import Path

scripts_dir, root, wtype, clock, point, action, ready, go, out = sys.argv[1:10]
sys.path.insert(0, scripts_dir)
import workflow_acceptance_matrix_test as matrix

item = matrix.Item.attach(matrix.Scratch.attach(root), wtype, clock_start=int(clock))


def hook(*_args):
    # CP6: a real process that stops exactly at one step boundary of
    # `/approve-review plan` -- `SIGKILL`ed there, or paused until the
    # test releases it.
    if action == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
    Path(ready).write_text("paused")
    deadline = time.monotonic() + 120
    while not Path(go).exists():
        if time.monotonic() > deadline:
            sys.exit(3)
        time.sleep(0.005)


try:
    commit = item.approve_plan(hooks={} if action == "none" else {point: hook})
    Path(out).write_text(json.dumps({"outcome": "success", "commit": commit}))
except Exception as exc:  # noqa: BLE001 -- the test asserts the exact type
    Path(out).write_text(json.dumps({"outcome": "refused", "error": type(exc).__name__,
                                     "detail": str(exc)}))
"""

CHECKPOINTS_B = CHECKPOINTS_TWO + [
    {"id": "CP3", "name": "third", "depends_on": ["CP2"], "complexity": "S", "session_target": 1},
]
REQUIREMENTS_B = dict(REQUIREMENTS_TWO, R3={"description": "a third thing", "checkpoint_ids": ["CP3"]})
STATE_REL = "docs/ai-workflow/WORKFLOW_STATE.json"


def approval_trailer_commits(scratch, work_item_id):
    """Every commit reachable from any ref that carries a
    `Workflow-Plan-Approval:` trailer for `work_item_id` -- repository-wide,
    so both worktrees' branches are counted."""
    out = scratch.git(
        "log", "--all", "--format=%H%x09%(trailers:key=Workflow-Plan-Approval,valueonly,separator=%x2C)"
        "%x09%(trailers:key=Workflow-Work-Item,valueonly,separator=%x2C)",
    ).stdout
    commits = []
    for line in out.splitlines():
        sha, approval, work_item = (line.split("\t") + ["", ""])[:3]
        if approval.strip() and work_item.strip() == work_item_id:
            commits.append(sha)
    return commits


class RepoGlobalLifecycleAcrossWorktrees(MatrixCase):
    """CP6 tests 8, 17, 18, 22(a)/(b)/(e), 23, 25, 26 and 28: two linked
    worktrees whose branches carry the same unresolved amendment seq 1
    (witness `OPEN`), each driving `/approve-review plan` exactly as the
    command says -- 4b/4c/4d, the staging modes, 6a/6a1/6b/6c, the advance
    and 6d."""

    work_item_type = "process"

    def setUp(self):
        super().setUp()
        self.io = Path(tempfile.mkdtemp(prefix="wf-cp6-io-"))
        self.addCleanup(shutil.rmtree, self.io, True)
        self._procs = []
        self.addCleanup(lambda: [p.kill() for p in self._procs if p.poll() is None])

    # ---------------- fixtures ----------------

    def _open_amendment_in_two_worktrees(self):
        """Round 1 approved and CP1 implemented on `main`; the amendment
        requested and committed (`request-plan-amendment.md` step 3); then
        worktree `b` branched from it, so both branches carry seq 1."""
        item = self.item
        self.scratch.write(".workflow-manager/installation.json",
                           json.dumps({"schema_version": 1, "workflow_version": "2.6.0"}) + "\n")
        self.scratch.commit("install workflow 2.6.0", paths=[".workflow-manager/installation.json"])
        item.milestone_plan(checkpoints=CHECKPOINTS_TWO, requirements=REQUIREMENTS_TWO)
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews()
        item.approve_plan()
        item.implement_checkpoint("CP1", {item.deliverable: "// CP1\n"})
        item.request_amendment("one resolution per sequence", commit=True)
        worktree = self.scratch.worktree("b")
        return item, Item.attach(worktree, self.work_item_type, clock_start=50_000)

    @staticmethod
    def _review_amended_plan(item, *, divergent=False):
        """`/milestone-plan` on the `AMENDING_PLAN` item, then both review
        stages. `divergent` gives this worktree a different amended plan
        (a third checkpoint, so a different reconciliation outcome too)."""
        if divergent:
            item.amend_plan(2, CHECKPOINTS_B, REQUIREMENTS_B, plan_body="Plan body, amended by B.\n")
        else:
            item.amend_plan(2, CHECKPOINTS_TWO, REQUIREMENTS_TWO, plan_body="Plan body, amended.\n")
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=2)

    def _worker(self, item, point, action, tag):
        """`/approve-review plan` for `item` in its own OS process, stopped
        at `point` by `action` (`pause`, `kill` or `none`)."""
        worker = self.io / f"approve-{tag}.py"
        worker.write_text(_CP6_APPROVE_WORKER_SOURCE)
        env = dict(os.environ)
        env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
        env.setdefault("GIT_CONFIG_SYSTEM", "/dev/null")
        env["PYTHONPATH"] = str(_tooling_dir()) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.Popen(
            [sys.executable, str(worker), str(_tooling_dir()), str(item.root), item.wtype,
             str(item._clock + 10_000), point, action, str(self.io / f"ready-{tag}"),
             str(self.io / f"go-{tag}"), str(self.io / f"out-{tag}.json")],
            env=env,
        )
        self._procs.append(proc)
        return proc

    def _wait(self, tag):
        deadline = __import__("time").monotonic() + 120
        while not (self.io / f"ready-{tag}").exists():
            if __import__("time").monotonic() > deadline:
                raise AssertionError(f"worker {tag} never reached its pause point")
            __import__("time").sleep(0.005)

    def _release(self, tag):
        (self.io / f"go-{tag}").write_text("go")

    def _result(self, tag):
        return json.loads((self.io / f"out-{tag}.json").read_text())

    @staticmethod
    def _witness(item):
        return ws.read_amendment_witness(item.root, item.wid)

    @staticmethod
    def _witness_bytes(item):
        return ws.read_amendment_witness_bytes(item.root, item.wid)

    def _head_resolution_digest(self, item):
        committed = json.loads(item.sim.git("show", f"HEAD:{STATE_REL}").stdout)
        entry = committed["work_items"][item.wid]["amendment_history"][0]
        return ws.amendment_resolution_projection_sha256(entry)

    def _take_over(self, item):
        """`approve-review.md` step 4b's recovery: the next invocation finds
        the journal open, reports and stops; the user's literal takes the
        transaction over."""
        evidence = ws.plan_approval_takeover_evidence(item.root)
        self.assertIsNotNone(evidence["journal"], "4b: a transaction must be open")
        literal = ws.plan_approval_takeover_authorization_literal(evidence)
        return ws.take_over_plan_approval_transaction(
            item.root, work_item_id=item.wid, now=item.now(), user_authorization=literal,
            evidence=evidence)

    def _forbid_reservation(self):
        """Instrumentation: a taken-over run resumes at 6a and must never
        reach 4d (section 5.6's entry table)."""
        return mock.patch.object(ws, "reserve_amendment_resolution",
                                 side_effect=AssertionError("4d reached from a taken-over run"))

    def _install_pre_commit_hook(self, body):
        hook = self.scratch.root / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nrm -f \"$0\"\n" + body)
        hook.chmod(0o755)

    # ---------------- 17: divergent concurrent resolution ----------------

    def _divergent_concurrent_resolution(self, *, a_wins):
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        self._review_amended_plan(item_b, divergent=True)
        winner, loser = (item_a, item_b) if a_wins else (item_b, item_a)
        loser_head = loser.sim.head()
        self._worker(loser, "after_journal", "pause", "loser")
        self._wait("loser")
        self._worker(winner, "after_reserve", "pause", "winner")
        self._wait("winner")
        self._release("loser")
        self.assertEqual(self._procs[0].wait(timeout=120), 0)
        refused = self._result("loser")
        self.assertEqual((refused["outcome"], refused["error"]),
                         ("refused", "AmendmentResolutionReservedError"), refused)
        winner_rcid = self._witness(winner)["resolution_reservation"]["approved_review_content_id"]
        self.assertIn(os.path.realpath(winner.root), refused["detail"])
        self.assertIn(repr("main" if a_wins else "b"), refused["detail"])
        self.assertIn(winner_rcid, refused["detail"])
        # The loser took 6b: journal closed, HEAD and index untouched.
        self.assertIsNone(ws.read_plan_approval_journal(loser.root))
        self.assertEqual(loser.sim.head(), loser_head)
        self.assertEqual(loser.sim.git("diff", "--name-only", "--cached", "HEAD").stdout, "")
        self._release("winner")
        self.assertEqual(self._procs[1].wait(timeout=120), 0)
        self.assertEqual(self._result("winner")["outcome"], "success", self._result("winner"))
        witness = self._witness(winner)
        self.assertEqual(witness["status"], ws.AMENDMENT_WITNESS_RESOLVED)
        self.assertEqual(witness["resolution_projection_sha256"], self._head_resolution_digest(winner))
        self.assertEqual(len(approval_trailer_commits(self.scratch, self.item.wid)), 2,
                         "round 1's approval plus exactly one resolution of seq 1")

    def test_cp6_17_divergent_concurrent_resolution_a_reserves_first(self):
        self._divergent_concurrent_resolution(a_wins=True)

    def test_cp6_17_divergent_concurrent_resolution_b_reserves_first(self):
        self._divergent_concurrent_resolution(a_wins=False)

    # ---------------- 18: second resolution after RESOLVED ----------------

    def _second_resolution_after_resolved(self, *, identical):
        """CP6 test 18. Refusing the *identical* plan is intended, not a
        missing idempotence (revision 8, `LPR-R7-002`): predicate step 3
        refuses because B's `HEAD` lacks the resolution, "whether or not its
        amended plan is the same one" (section 5.6) -- an identical approval
        would be a second approval commit, so B merges A's instead."""
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        item_a.approve_plan()
        self.assertEqual(self._witness(item_a)["status"], ws.AMENDMENT_WITNESS_RESOLVED)
        self._review_amended_plan(item_b, divergent=not identical)
        head, witness = item_b.sim.head(), self._witness_bytes(item_b)
        tree = item_b.sim.git("write-tree").stdout
        with self.assertRaises(ws.StaleLifecycleStateError) as refused:
            item_b.approve_plan()
        self.assertIn("merge the resolved amendment first", str(refused.exception))
        self.assertEqual(item_b.sim.head(), head)
        self.assertEqual(item_b.sim.git("write-tree").stdout, tree)
        self.assertEqual(self._witness_bytes(item_b), witness)
        self.assertIsNone(ws.read_plan_approval_journal(item_b.root))
        self.assertEqual(len(approval_trailer_commits(self.scratch, self.item.wid)), 2)
        # B discards its own attempt and merges A's resolution; its claim
        # is then admitted.
        item_b.sim.git("reset", "-q", "--hard")
        item_b.sim.git("merge", "-q", "--no-edit", "main")
        claim = ws.claim_checkpoint(item_b.root, item_b.wid, "CP2", now=item_b.now())
        self.assertEqual(claim["checkpoint_id"], "CP2")

    def test_cp6_18_a_different_second_resolution_refuses_before_any_staging(self):
        self._second_resolution_after_resolved(identical=False)

    def test_cp6_18_an_identical_second_resolution_refuses_too(self):
        self._second_resolution_after_resolved(identical=True)

    # ---------------- 8: a lost advance self-heals ----------------

    def test_cp6_08_sigkill_after_the_commit_self_heals_in_the_resolvers_resumed_run(self):
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        proc = self._worker(item_a, "after_commit", "kill", "a")
        self.assertEqual(proc.wait(timeout=120), -9)
        self.assertEqual(self._witness(item_a)["status"], ws.AMENDMENT_WITNESS_RESOLVING)
        reserved = self._witness(item_a)["resolution_reservation"]["resolution_projection_sha256"]
        new_owner = self._take_over(item_a)
        with self._forbid_reservation():
            item_a.complete_plan_approval(new_owner)
        witness = self._witness(item_a)
        self.assertEqual(witness["status"], ws.AMENDMENT_WITNESS_RESOLVED)
        self.assertEqual(witness["resolution_projection_sha256"], reserved)
        self.assertEqual(witness["resolved_commit"], item_a.sim.head())
        self.assertIsNone(ws.read_plan_approval_journal(item_a.root))
        self.assertEqual(len(approval_trailer_commits(self.scratch, self.item.wid)), 2)

    def test_cp6_08_sigkill_after_the_commit_self_heals_from_another_worktrees_claim(self):
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        proc = self._worker(item_a, "after_commit", "kill", "a")
        self.assertEqual(proc.wait(timeout=120), -9)
        reserved = self._witness(item_a)["resolution_reservation"]["resolution_projection_sha256"]
        with self.assertRaises(ws.StaleLifecycleStateError):
            ws.claim_checkpoint(item_b.root, item_b.wid, "CP2", now=item_b.now())
        witness = self._witness(item_b)
        self.assertEqual((witness["status"], witness["resolution_projection_sha256"]),
                         (ws.AMENDMENT_WITNESS_RESOLVED, reserved))
        # The resolver's own resumed run then finds it already advanced.
        with self._forbid_reservation():
            item_a.complete_plan_approval(self._take_over(item_a))
        self.assertEqual(self._witness(item_a)["resolution_projection_sha256"], reserved)

    # ---------------- 22: reservation crash recovery ----------------

    def test_cp6_22a_sigkill_between_4c_and_4d_recovers_through_4b_6a_6b(self):
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        witness_before = self._witness_bytes(item_a)
        proc = self._worker(item_a, "after_journal", "kill", "a")
        self.assertEqual(proc.wait(timeout=120), -9)
        self.assertEqual(self._witness_bytes(item_a), witness_before)
        new_owner = self._take_over(item_a)
        with self._forbid_reservation():
            self.assertIsNone(item_a.complete_plan_approval(new_owner))  # 6a NOT_COMMITTED, 6b
        self.assertEqual(self._witness_bytes(item_a), witness_before)
        self.assertIsNone(ws.read_plan_approval_journal(item_a.root))
        # A fresh invocation then reserves at 4d and completes.
        item_a.stage_plan_files()
        item_a.approve_plan()
        self.assertEqual(self._witness(item_a)["status"], ws.AMENDMENT_WITNESS_RESOLVED)

    def test_cp6_22a_variant_another_worktree_reserves_in_the_window(self):
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        self._review_amended_plan(item_b, divergent=True)
        proc = self._worker(item_a, "after_journal", "kill", "a")
        self.assertEqual(proc.wait(timeout=120), -9)
        with self._forbid_reservation():
            item_a.complete_plan_approval(self._take_over(item_a))
        self._worker(item_b, "after_reserve", "pause", "b")
        self._wait("b")
        head = item_a.sim.head()
        item_a.stage_plan_files()
        with self.assertRaises(ws.AmendmentResolutionReservedError):
            item_a.approve_plan()
        self.assertEqual(item_a.sim.head(), head)
        self.assertIsNone(ws.read_plan_approval_journal(item_a.root))
        self._release("b")
        self.assertEqual(self._procs[-1].wait(timeout=120), 0)
        self.assertEqual(self._result("b")["outcome"], "success", self._result("b"))
        self.assertEqual(len(approval_trailer_commits(self.scratch, self.item.wid)), 2)

    def test_cp6_22b_a_killed_resolver_with_its_journal_open_keeps_its_reservation(self):
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        self._review_amended_plan(item_b, divergent=True)
        proc = self._worker(item_a, "after_reserve", "kill", "a")
        self.assertEqual(proc.wait(timeout=120), -9)
        reserved = self._witness_bytes(item_a)
        with self.assertRaises(ws.AmendmentResolutionReservedError):
            item_b.approve_plan()
        with self.assertRaises(ws.AmendmentInFlightError):
            ws.claim_checkpoint(item_b.root, item_b.wid, "CP2", now=item_b.now())
        self.assertEqual(self._witness_bytes(item_a), reserved, "nothing rolls it back automatically")

    def test_cp6_22e_and_28_a_taken_over_reservation_stays_live_then_releases_in_band(self):
        """22(e): after a takeover of A's journal the reservation is live
        against B through `previous_owner_tokens`, and the taken-over run
        never reaches 4d. 28: that run's `NOT_COMMITTED` rollback releases
        in band, with the tokens captured before the rollback; B can then
        reserve."""
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        self._review_amended_plan(item_b, divergent=True)
        proc = self._worker(item_a, "after_pin", "kill", "a")
        self.assertEqual(proc.wait(timeout=120), -9)
        new_owner = self._take_over(item_a)
        journal = ws.read_plan_approval_journal(item_a.root)
        reservation = self._witness(item_a)["resolution_reservation"]
        self.assertIn(reservation["journal_owner_token"], journal["previous_owner_tokens"])
        with self.assertRaises(ws.AmendmentResolutionReservedError):
            item_b.approve_plan()
        with self._forbid_reservation():
            self.assertIsNone(item_a.complete_plan_approval(new_owner))
        self.assertEqual(self._witness(item_a)["status"], ws.AMENDMENT_WITNESS_OPEN)
        item_b.stage_plan_files()
        item_b.approve_plan()
        self.assertEqual(self._witness(item_b)["resolution_projection_sha256"],
                         self._head_resolution_digest(item_b))

    def test_cp6_28_the_release_works_on_a_detached_head_and_needs_every_captured_token(self):
        for only_current in (False, True):
            with self.subTest(only_current_token=only_current):
                scratch = Scratch()
                self.addCleanup(scratch.cleanup)
                self.scratch, self.item = scratch, Item(scratch, self.work_item_type)
                self.item.seed()
                item_a, _item_b = self._open_amendment_in_two_worktrees()
                item_a.sim.git("checkout", "-q", "--detach")
                self._review_amended_plan(item_a)
                tag = f"a-{only_current}"
                proc = self._worker(item_a, "after_reserve", "kill", tag)
                self.assertEqual(proc.wait(timeout=120), -9)
                self.assertIsNone(self._witness(item_a)["resolution_reservation"]["resolver_branch"])
                new_owner = self._take_over(item_a)
                with self._forbid_reservation():
                    item_a.complete_plan_approval(
                        new_owner, release_tokens=[new_owner] if only_current else None)
                self.assertEqual(self._witness(item_a)["status"],
                                 ws.AMENDMENT_WITNESS_RESOLVING if only_current
                                 else ws.AMENDMENT_WITNESS_OPEN)

    # ---------------- 23: no flock across turns ----------------

    def test_cp6_23_the_lifecycle_lock_is_never_held_between_steps_or_inside_a_guarded_window(self):
        item_a, _item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        seen = []

        def nothing_held(point):
            def check(*_args):
                self.assertEqual(ws.held_primitives(), (), point)
                seen.append(point)
            return check

        points = ("after_journal", "after_reserve", "after_stage", "after_pin", "after_commit",
                  "after_materialize", "after_advance", "after_close")
        hooks = {point: nothing_held(point) for point in points}

        def guarded_window_refuses(journal):
            lease = ws.acquire_plan_approval_guard(
                item_a.root, holder_owner_token=journal["owner_token"],
                step="step-5-stage-and-pin", now=item_a.now())
            try:
                for call in (lambda: ws.reserve_amendment_resolution(item_a.root, item_a.wid, journal,
                                                                     now="t"),
                             lambda: ws.assert_amendment_resolution_held(item_a.root, item_a.wid, journal),
                             lambda: ws.advance_amendment_witness(item_a.root, item_a.wid, journal=journal)):
                    with self.assertRaises(ws.LifecycleLockOrderError):
                        call()
            finally:
                ws.release_plan_approval_guard(item_a.root, lease)
            nothing_held("after_reserve")()

        hooks["after_reserve"] = guarded_window_refuses
        item_a.approve_plan(hooks=hooks)
        self.assertEqual(set(seen), set(points))
        self.assertEqual(self._witness(item_a)["status"], ws.AMENDMENT_WITNESS_RESOLVED)

    # ---------------- 25/26: recovery after the journal opens ----------------

    def _amend_recovery(self, *, taken_over, pre_advanced):
        """CP6 test 25: a one-shot `pre-commit` hook rewrites a non-state
        member (`TREE_CONTENT`); 6a1 holds the resolution and amends."""
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        # The staged plan document plus one line: its `(Revision N)` title
        # survives, so the defect is pure tree content.
        self._install_pre_commit_hook(
            f"blob=$( (git show :{item_a.plan_path}; printf 'hook rewrite\\n') "
            "| git hash-object -w --stdin)\n"
            f"git update-index --cacheinfo 100644,$blob,{item_a.plan_path}\n")
        pre_amend = []

        def advance_from_b(_journal):
            pre_amend.append(item_a.sim.head())
            if pre_advanced:
                with self.assertRaises(ws.StaleLifecycleStateError):
                    ws.claim_checkpoint(item_b.root, item_b.wid, "CP2", now=item_b.now())
                self.assertEqual(self._witness(item_a)["status"], ws.AMENDMENT_WITNESS_RESOLVED)

        held_writes = []
        real_held = ws.assert_amendment_resolution_held

        def held_check(*args, **kwargs):
            before = self._witness_bytes(item_a)
            try:
                return real_held(*args, **kwargs)
            finally:
                held_writes.append(before == self._witness_bytes(item_a))

        with mock.patch.object(ws, "assert_amendment_resolution_held", side_effect=held_check):
            if taken_over:
                item_a.approve_plan(stop_after="commit", hooks={"after_commit": advance_from_b})
                advanced_before = self._witness(item_a)
                with self._forbid_reservation():
                    amended = item_a.complete_plan_approval(self._take_over(item_a))
            else:
                advanced_before = None

                def capture(journal):
                    advance_from_b(journal)
                    nonlocal advanced_before
                    advanced_before = self._witness(item_a)

                amended = item_a.approve_plan(hooks={"after_commit": capture})
        self.assertEqual(held_writes, [True], "6a1's held check ran once and wrote nothing")
        self.assertNotEqual(amended, pre_amend[0])
        self.assertEqual(approval_trailer_commits(self.scratch, self.item.wid)[0], amended)
        self.assertEqual(len(approval_trailer_commits(self.scratch, self.item.wid)), 2)
        witness = self._witness(item_a)
        self.assertEqual(witness["status"], ws.AMENDMENT_WITNESS_RESOLVED)
        self.assertEqual(witness["resolution_projection_sha256"], self._head_resolution_digest(item_a))
        self.assertEqual(witness["resolved_commit"], amended)
        if pre_advanced:
            self.assertEqual(advanced_before["resolved_commit"], pre_amend[0])
            self.assertEqual({k: v for k, v in witness.items() if k != "resolved_commit"},
                             {k: v for k, v in advanced_before.items() if k != "resolved_commit"},
                             "the owner's advance rewrites resolved_commit and nothing else")

    def test_cp6_25_amend_recovery_in_session_with_the_reservation_live(self):
        self._amend_recovery(taken_over=False, pre_advanced=False)

    def test_cp6_25_amend_recovery_in_session_already_advanced_by_another_worktree(self):
        self._amend_recovery(taken_over=False, pre_advanced=True)

    def test_cp6_25_amend_recovery_after_a_takeover_with_the_reservation_live(self):
        self._amend_recovery(taken_over=True, pre_advanced=False)

    def test_cp6_25_amend_recovery_after_a_takeover_already_advanced_by_another_worktree(self):
        self._amend_recovery(taken_over=True, pre_advanced=True)

    def test_cp6_27_6a1_stops_before_its_first_guarded_window_on_a_witness_it_cannot_hold(self):
        """CP6 test 27, through the command flow: a `TREE_CONTENT` commit
        routes 6a into 6a1, whose held check meets a planted `OPEN` witness
        and raises `AmendmentResolutionHeldError` -- no amend, and `HEAD`,
        the index, the journal, the owner progress and the witness bytes
        are exactly as found."""
        item_a, _item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        self._install_pre_commit_hook(
            f"blob=$( (git show :{item_a.plan_path}; printf 'hook rewrite\\n') "
            "| git hash-object -w --stdin)\n"
            f"git update-index --cacheinfo 100644,$blob,{item_a.plan_path}\n")
        commit = item_a.approve_plan(stop_after="commit")
        planted = self._witness(item_a)["previous"]  # the OPEN witness the reservation replaced
        path = ws.amendment_witness_path(item_a.root, item_a.wid)
        path.write_text(json.dumps(planted, indent=2, sort_keys=True) + "\n")
        witness = self._witness_bytes(item_a)
        journal = ws.read_plan_approval_journal(item_a.root)
        journal_bytes = ws.plan_approval_journal_path(item_a.root).read_bytes()
        progress = ws.read_plan_approval_owner_progress(item_a.root, journal["owner_token"])
        with self.assertRaises(ws.AmendmentResolutionHeldError):
            item_a.complete_plan_approval(journal["owner_token"])
        self.assertEqual(item_a.sim.head(), commit)
        self.assertEqual(item_a.sim.git("diff", "--name-only", "--cached", "HEAD").stdout, "")
        self.assertEqual(ws.plan_approval_journal_path(item_a.root).read_bytes(), journal_bytes)
        self.assertEqual(ws.read_plan_approval_owner_progress(item_a.root, journal["owner_token"]),
                         progress)
        self.assertEqual(self._witness_bytes(item_a), witness)
        self.assertEqual(ws.held_primitives(), ())

    def test_cp6_26_a_hook_rewriting_the_state_blob_is_reserved_not_a_conflict(self):
        """CP6 test 26: the committed entry N's digest differs from the
        reservation's. Before 6a1, B's claim reading the resolver's `HEAD`
        refuses with `AmendmentResolutionReservedError` (predicate step 1's
        in-flight exception) and binds nothing; 6a1's held check passes on
        the journal's pinned digest, the amend repairs the blob, and the
        advance binds the reserved digest."""
        item_a, item_b = self._open_amendment_in_two_worktrees()
        self._review_amended_plan(item_a)
        rewritten = self.io / "rewritten-state.json"

        def write_rewritten_state(journal):
            post = json.loads(base64.b64decode(journal["expected_post_state_b64"]))
            post["work_items"][item_a.wid]["amendment_history"][0]["reconciliation_outcome"] = {
                "CP1": "needs_revalidation"}
            rewritten.write_text(json.dumps(post, indent=2) + "\n")

        self._install_pre_commit_hook(
            f"blob=$(git hash-object -w {rewritten})\n"
            f"git update-index --cacheinfo 100644,$blob,{STATE_REL}\n")
        commit = item_a.approve_plan(stop_after="commit", hooks={"after_pin": write_rewritten_state})
        reserved = self._witness(item_a)["resolution_reservation"]["resolution_projection_sha256"]
        self.assertNotEqual(self._head_resolution_digest(item_a), reserved)
        witness_before = self._witness_bytes(item_a)
        with self.assertRaises(ws.AmendmentResolutionReservedError):
            ws.claim_checkpoint(item_b.root, item_b.wid, "CP2", now=item_b.now())
        self.assertEqual(self._witness_bytes(item_a), witness_before)
        amended = item_a.complete_plan_approval(ws.read_plan_approval_journal(item_a.root)["owner_token"])
        self.assertNotEqual(amended, commit)
        self.assertEqual(self._head_resolution_digest(item_a), reserved)
        witness = self._witness(item_a)
        self.assertEqual((witness["status"], witness["resolution_projection_sha256"]),
                         (ws.AMENDMENT_WITNESS_RESOLVED, reserved))
        self.assertEqual(len(approval_trailer_commits(self.scratch, self.item.wid)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
