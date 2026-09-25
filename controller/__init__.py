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
    buildinfo, version, errors, repo_policy, gitrepo, forge, runtime, release_txn, lock, identity, decision, managed_repo,
    target_state, evidence, routing, worker, handoff, job, observe, cli,
)

__all__ = [
    "buildinfo", "version", "errors", "repo_policy", "gitrepo", "forge", "runtime", "release_txn", "lock", "identity", "decision",
    "managed_repo", "target_state", "evidence", "routing", "worker", "handoff", "job", "observe", "cli",
]
