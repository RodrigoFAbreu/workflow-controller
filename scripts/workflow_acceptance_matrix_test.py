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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


def _run(args, cwd, check=True, env=None):
    full_env = dict(os.environ)
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

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def read(self, rel):
        return (self.root / rel).read_text()

    def git(self, *args, check=True):
        return _run(["git", *args], cwd=self.root, check=check)

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

    # ---------------- plumbing ----------------

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
        stage-agnostic and keeps the scoped-else-flat rule for every stage
        alike (`REVIEW_PROTOCOL.md`, `REQ-21`). The directory is created
        at whatever path the resolver answers -- an external reviewer
        placing `REVIEW_FEEDBACK.md` creates it the same way -- never at a
        hardcoded scoped path, which would silently move every later
        resolution for this work item."""
        path = self.root / fingerprint.resolve_feedback_dir(self.root, self.wid)
        path.mkdir(parents=True, exist_ok=True)
        return path

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
            + ws.render_registry_markdown(registry) + "\n",
        )
        self.tx(lambda state: ws.publish_plan_revision(
            state, self.wid, plan_revision, self.now(),
        ))
        self.stage_plan_files()
        if commit:
            self.sim.commit(f"plan({self.wid}): revision {plan_revision}",
                            {"Workflow-Work-Item": self.wid})

    def stage_plan_files(self):
        """`/milestone-plan` step 3's staging step (salvage audit `B6`):
        the four plan-stage files are marked intent-to-add and left that
        way, so `resolve_plan_stage_metadata`'s tracked-path requirement
        is satisfied and `/approve-review plan`'s own commit is what
        commits them."""
        self.sim.git("add", "-N", "--", self.plan_path, self.registry_path,
                     self.mapping_path, self.artifacts_path)

    # ---------------- plan-stage bundle ----------------

    def generate_plan_bundle(self, check=True, test_results=None, review_request=None):
        bundle = self.bundle_dir(stage="plan")
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
        return self.sim.prepare(self.base_commit, "plan", self.wid, check=check)

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

    def approve_plan(self, user_confirmation=None):
        """`/approve-review plan` steps 1-6d: the full journal/guard/
        staging/commit/classify/verify/materialize transaction."""
        confirmation = user_confirmation or f"plan {self.wid}"
        entry = self.entry()
        review_content_id, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            self.root, self.wid,
        )
        bundle_id = self.bundle_id()
        fingerprint.assert_local_generation_matches(self.root, self.bundle_dir(stage="plan") / "MANIFEST.md")
        fingerprint.assert_bundle_not_rejected(self.root, self.wid)
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
        plan = fingerprint.resolve_plan_stage_approval_commit_paths(
            self.root, self.wid, Path("docs/ai-workflow/WORKFLOW_STATE.json"),
        )
        pre_state = self.state()
        journal = ws.open_plan_approval_journal(
            self.root, work_item_id=self.wid, base_commit=self.base_commit,
            pre_state=pre_state, record=record, approval_now=approval_now,
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
            self.root, owner_token=owner, step="step-5-stage-and-pin", now=self.now(),
        ):
            ws.stage_plan_approval_commit_paths(self.root, ordinary)
            if plan.artifacts_declaration_path:
                ws.verify_staged_blob_sha256(
                    self.root, plan.artifacts_declaration_path, plan.artifacts_declaration_sha256,
                )
        with ws.plan_approval_guarded_mutation(
            self.root, owner_token=owner, step="step-6.1b-state-pin", now=self.now(),
        ):
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
        staged = self.sim.git("diff", "--name-only", "--cached", "HEAD").stdout.split()
        outside = [p for p in staged if p not in journal["applicable_paths"]]
        if outside:
            raise AssertionError(f"staged paths outside the applicable set: {outside}")
        with ws.plan_approval_guarded_mutation(
            self.root, owner_token=owner, step="step-6.5-commit", now=self.now(),
        ):
            self.sim.git("commit", "-q", "-m", (
                f"chore({self.wid}): plan-stage approval\n\nBasis: {basis}.\n\n"
                f"Workflow-Plan-Approval: {review_content_id}\n"
                f"Workflow-Work-Item: {self.wid}\n"
            ))
        commit = self.sim.head()
        outcome = ws.classify_plan_approval_outcome(self.root, journal)
        if outcome != ws.PLAN_APPROVAL_OUTCOME_COMMITTED:
            raise AssertionError(f"unexpected plan-approval outcome: {outcome}")
        post_item = dict(self.entry())
        post_item["plan_approval"] = record
        ws.verify_post_approval_manifest_match(
            self.root, post_item, stage="plan", base_commit=self.base_commit, commit=commit,
        )
        ws.assert_committed_path_set_matches(self.root, commit, journal["applicable_paths"])
        ws.verify_committed_plan_approval_state_blob(
            self.root, commit, journal["expected_post_state_sha256"],
        )
        with ws.plan_approval_guarded_mutation(
            self.root, owner_token=owner, step="step-8b-materialize", now=self.now(),
        ):
            post_state = json.loads(base64.b64decode(journal["expected_post_state_b64"]))
            if ws.classify_plan_approval_materialize_target(
                self.root, self.wid, pre_state, post_state,
            ) == ws.PLAN_APPROVAL_MATERIALIZE_WRITE:
                ws.materialize_plan_approval_state(
                    self.root, commit, journal["expected_post_state_sha256"],
                )
        with ws.plan_approval_guarded_mutation(
            self.root, owner_token=owner, step="step-8a-close-journal", now=self.now(),
        ):
            ws.close_plan_approval_journal(self.root)
        return commit

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
            + ws.render_registry_markdown(registry) + "\n",
        )
        self.tx(lambda state: ws.publish_plan_revision(state, self.wid, plan_revision, self.now()))
        self.stage_plan_files()
        self.tx(lambda state: ws.transition_to_awaiting_local_plan_review(
            state, self.wid, self.now(),
        ))

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
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        item.generate_plan_bundle()
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
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(item.entry()["plan_revision"], 2)
        item.generate_plan_bundle()
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
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        item.generate_plan_bundle()
        item.write_feedback("APPROVE")
        item.record_plan_reviews(round=3)
        item.approve_plan()
        self.assertEqual(item.entry()["plan_revision"], 3)
        stages = item.entry()["plan_review_stages"]
        self.assertEqual(stages[ws.LOCAL_MODEL_PLAN_REVIEW]["round"], 3)
        self.assertEqual(stages[ws.MANUAL_EXTERNAL_PLAN_REVIEW]["round"], 3)

    def test_a4_plan_stage_stale_test_results_withdraws(self):
        """Ledger `I3`: the marker lines are a hard generator precondition,
        and a miss withdraws rather than publishes."""
        item = self.item
        item.milestone_plan()
        proc = item.generate_plan_bundle(check=False, test_results="")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("withdrawn", proc.stdout + proc.stderr)
        self.assertIn("TEST_RESULTS.md", proc.stdout + proc.stderr)
        self.assertFalse(item.bundle_dir(stage="plan").is_dir())
        self.assertFalse((item.root / ".ai-review" / item.wid / "review-bundle.tar.gz").is_file())
        quarantined = list((item.root / ".ai-review" / item.wid).glob("current.rejected-*"))
        self.assertEqual(len(quarantined), 1)
        # And a clean regeneration afterwards publishes normally.
        item.generate_plan_bundle()
        self.assertTrue((item.bundle_dir(stage="plan") / "MANIFEST.md").is_file())


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
        self.reach_technical_approved()
        self.assertEqual(item.entry()["implementation_revision"], 1)

        item.apply_plan_review(2, plan_body="Plan body, corrected after round 1.\n")
        self.scratch.commit(f"plan({item.wid}): revision 2",
                            {"Workflow-Work-Item": item.wid})
        item.stage_plan_files()
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

        # And the round publishes from there. The plan revision changed no
        # protected *implementation* content, so `resolve_bundle_generation_
        # outcome` resolves `same_content` -- `D-Commit-Provenance`'s own
        # "a plan-only correction requiring no protected implementation
        # change" case (`GPT-R54-002`), legal from
        # `SELF_REVIEWING_IMPLEMENTATION` exactly as from
        # `APPLYING_REVIEW_FEEDBACK`. The revision and the reviewed head
        # both stay pinned, and the supersession chain stays continuous.
        pinned_head = item.entry()["reviewed_implementation_head"]
        outcome, s2, proc = item.generate_impl_bundle(
            "implementation", expect_outcome="same_content", wrap_up=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(item.entry()["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(item.entry()["implementation_revision"], 1)
        self.assertEqual(item.entry()["reviewed_implementation_head"], pinned_head)
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

        item.apply_plan_review(2, CHECKPOINTS_TWO, REQUIREMENTS_TWO,
                               plan_body="Plan body, revised mid-implementation.\n")
        self.assertEqual(item.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
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
        item.stage_plan_files()
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
        # Diagram finding `B1`: the child's plan-review entry is
        # `publish_plan_revision`'s own version branch, and the child's
        # governing version is the config default fixed at creation --
        # `"2.1"` here, so `AWAITING_LOCAL_PLAN_REVIEW`, never
        # `AWAITING_EXTERNAL_PLAN_REVIEW`.
        self.assertEqual(resumed["governing_workflow_version"], "2.1")
        self.assertEqual(resumed["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
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
        child.write_feedback("REVISE")
        child.record_plan_reviews(local="REVISE")
        self.assertEqual(child.entry()["phase"], "REVISING_PLAN")
        parent_before = dict(parent.entry())
        child.apply_plan_review(plan_revision=2)
        self.assertEqual(child.entry()["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(child.entry()["plan_revision"], 2)
        self.assertEqual(parent.entry(), parent_before)
        self.assertEqual(child.state()["active_work_item_id"], parent.wid)

        child.generate_plan_bundle()
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
        item.apply_plan_review(2, plan_body="Plan body, unchanged in substance.\n")
        self.scratch.commit(f"plan({item.wid}): revision 2",
                            {"Workflow-Work-Item": item.wid})
        item.stage_plan_files()
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
        """`/milestone-plan` step 6, verbatim: resolve, author, generate."""
        bundle_rel = fingerprint.resolve_bundle_dir(scratch.root, item.wid, stage="plan")
        self.assertEqual(bundle_rel, Path(".ai-review") / item.wid / "current")
        bundle = scratch.root / bundle_rel
        digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            scratch.root, item.wid,
        )
        metadata = fingerprint.resolve_plan_stage_metadata(scratch.root, item.wid)
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "REVIEW_REQUEST.md").write_text(
            f"# Review request\n\nstage: plan\nwork item: {item.wid}\n"
            f"review_content_id: {digest}\n"
        )
        (bundle / "TEST_RESULTS.md").write_text(
            f"stage: plan (revision {metadata.plan_revision})\nhead: {scratch.head()}\n\n"
            "No automated checks are required at the plan stage.\n"
        )
        (bundle / "CONTEXT_FILES.txt").write_text("")
        proc = scratch.prepare(item.base_commit, "plan", item.wid, check=False)
        self.assertEqual(
            proc.returncode, 0,
            f"first attempt failed:\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}",
        )
        self.assertTrue((bundle / "MANIFEST.md").is_file())
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
        item.tx(lambda state: ws.publish_plan_revision(
            state, item.wid, plan_revision, item.now(),
        ))
        item.stage_plan_files()
        return registry

    def test_the_pre_repair_sequence_publishes_a_table_the_registry_contradicts(self):
        """The defect itself, executed: the document omits a checkpoint the
        registry declares, and the plan bundle publishes anyway."""
        item = self.item
        item.milestone_plan()
        item.generate_plan_bundle()
        self.assertNotIn("CP2", self.scratch.read(item.plan_path))

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


if __name__ == "__main__":
    unittest.main(verbosity=1)
