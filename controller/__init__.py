"""Workflow Controller -- Generation 1.

Automates operation of the Workflow state machine over managed development
repositories. See ``docs/ACTIVE_MILESTONE.md`` and
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``.

Every module is imported eagerly, in dependency order, so a later import
site can never see a partially-loaded package -- and so a test can assert
this literal always names exactly the modules that exist under
``controller/``.
"""

from . import (
    buildinfo, version, errors, harness, conventional_commit, repo_policy, gitrepo, release_notes, forge, runtime, release_txn, milestone_branch, lock, identity, workflow_contract, protocol_schema, protocol, pr_report, functional_review,
    decision, managed_repo, target_state, evidence, protocol_decision, routing, notify, settings, anchor, usage, worker_stream, usage_record, worker, review_rounds, telemetry, review, review_mode, handoff, job, observe, cli,
)

__all__ = [
    "buildinfo", "version", "errors", "harness", "conventional_commit", "repo_policy", "gitrepo", "release_notes", "forge", "runtime", "release_txn", "milestone_branch", "lock", "identity",
    "workflow_contract", "protocol_schema", "protocol", "pr_report", "functional_review", "decision", "managed_repo", "target_state", "evidence", "protocol_decision", "routing", "notify", "settings", "anchor", "usage", "worker_stream", "usage_record", "worker", "review_rounds", "telemetry", "review", "review_mode", "handoff", "job", "observe",
    "cli",
]
