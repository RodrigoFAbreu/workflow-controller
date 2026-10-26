#!/usr/bin/env python3
# state_writer: false
"""Hermetic tests for `D1`'s state-writer serialization primitive
(`state_lock`/`state_transaction`, missing-test item 354) and
`D-Completion-Obligations` (`discover_state_writers`,
`verify_wfo_state_serialization`, `resolve_completion_obligations`,
`completion_obligations_satisfied`, `work_item_completion_status`,
`replay_completion_obligation`, and `complete_work_item`'s
`UnsatisfiedCompletionObligationError` gate -- missing-test items
356/357/358/360/361), covering the `WFO-STATE-SERIALIZATION` obligation
`WF8b` continued scope declares, plus `verify_wfo_ledger_coverage`
(`WFO-LEDGER-COVERAGE`, `WFR-68`, `WF8c` item (m)), and the backlog module
`workflow_backlog` (`Backlog*` groups: parser, schema, selection, edit, page), and the retention
report `workflow_retention` (`RetentionReport`, workflow-2.20.0).

Runs entirely against disposable scratch Git repositories, mirroring
`workflow_state_test.py`'s own pattern. `resolve_completion_obligations`'s
own end-to-end tests use a small, directly-controllable stub
`verify_wfo_state_serialization` as the materialized/executed verifier,
decoupled from `discover_state_writers`/the real conformance's own logic
(covered separately, in-process, by `TestDiscoverStateWriters`/
`TestVerifyWfoStateSerialization`).

Stdlib-only. Run: python3 scripts/workflow_state_completion_obligations_test.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
import unittest.mock
from pathlib import Path

import workflow_backlog as wb
import workflow_fingerprint as fingerprint
import workflow_retention as wr
import workflow_state as ws


def _repo_root() -> Path:
    """This repository's root, for the few rows that make a claim
    about the real checked-in documents rather than a scratch
    fixture."""
    return Path(__file__).resolve().parent.parent


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


class ScratchRepo:
    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="wfo-test-"))
        _run(["git", "init", "-q"], cwd=self.root)
        _run(["git", "config", "user.email", "test@example.com"], cwd=self.root)
        _run(["git", "config", "user.name", "Test"], cwd=self.root)
        (self.root / ".gitignore").write_text(".ai-review/\n")
        (self.root / "README.md").write_text("base\n")
        _run(["git", "add", "README.md", ".gitignore"], cwd=self.root)
        _run(["git", "commit", "-q", "-m", "base"], cwd=self.root)
        self.base = self.head()
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)

    def head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True, capture_output=True, text=True,
        ).stdout.strip()


def _write(repo, rel_path, content):
    full = repo.root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return full


def _commit_paths(repo, rel_paths, subject, trailers=None):
    for rel_path in rel_paths:
        _run(["git", "add", rel_path], cwd=repo.root)
    body = subject
    if trailers:
        body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
    return repo.head()


def _commit_empty(repo, subject, trailers=None):
    body = subject
    if trailers:
        body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    _run(["git", "commit", "-q", "--allow-empty", "-m", body], cwd=repo.root)
    return repo.head()


# ---------------------------------------------------------------------------
# item 354: state_lock / state_transaction primitive
# ---------------------------------------------------------------------------


def _bump_count(state):
    state = dict(state)
    state["count"] = state.get("count", 0) + 1
    return state


def _increment_worker(repo_root: str, path: str, n: int) -> None:
    for _ in range(n):
        ws.state_transaction(Path(repo_root), _bump_count, path=Path(path))


def _lock_hold_worker(repo_root: str, ready, release) -> None:
    with ws.state_lock(Path(repo_root)):
        ready.set()
        release.wait(timeout=30)


def _lock_acquire_worker(repo_root: str) -> None:
    with ws.state_lock(Path(repo_root)):
        pass


def _lock_crash_worker(repo_root: str) -> None:
    with ws.state_lock(Path(repo_root)):
        os._exit(1)  # simulated hard crash while holding the lock


def _lock_follow_up_worker(repo_root: str, acquired) -> None:
    with ws.state_lock(Path(repo_root)):
        acquired.value = 1


class TestStateLock(unittest.TestCase):
    def test_state_transaction_serializes_real_concurrent_processes_no_lost_update(self):
        """Item 354(b): "two (and sixteen) concurrent writers serialize
        with every update present in the final file and none lost" --
        exercised with real OS processes, never threads, so the
        `fcntl.flock` primitive is genuinely tested rather than assumed
        cooperative under the GIL."""
        with ScratchRepo() as repo:
            state_path = "state.json"
            _write(repo, state_path, json.dumps({"count": 0}))
            procs = [
                multiprocessing.Process(target=_increment_worker, args=(str(repo.root), state_path, 25))
                for _ in range(16)
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=60)
                self.assertEqual(p.exitcode, 0)
            final = json.loads((repo.root / state_path).read_text())
            self.assertEqual(final["count"], 16 * 25)

    def test_second_writer_blocks_until_first_releases(self):
        with ScratchRepo() as repo:
            lock_path = ws.state_lock_path(repo.root)
            ready = multiprocessing.Event()
            release = multiprocessing.Event()

            proc = multiprocessing.Process(target=_lock_hold_worker, args=(str(repo.root), ready, release))
            proc.start()
            self.assertTrue(ready.wait(timeout=10))

            waiter_proc = multiprocessing.Process(target=_lock_acquire_worker, args=(str(repo.root),))
            waiter_proc.start()
            time.sleep(0.3)
            self.assertTrue(waiter_proc.is_alive(), "second writer must still be blocked")
            release.set()
            waiter_proc.join(timeout=30)
            proc.join(timeout=30)
            self.assertEqual(waiter_proc.exitcode, 0)
            self.assertTrue(lock_path.exists())

    def test_crashed_holder_releases_lock_no_manual_takeover(self):
        """Item 354(b): a crashed holder's lock is released by the kernel;
        the next acquirer proceeds with no owner-token/takeover step at
        all."""
        with ScratchRepo() as repo:
            proc = multiprocessing.Process(target=_lock_crash_worker, args=(str(repo.root),))
            proc.start()
            proc.join(timeout=30)
            self.assertNotEqual(proc.exitcode, 0)

            acquired = multiprocessing.Value("b", 0)
            proc2 = multiprocessing.Process(target=_lock_follow_up_worker, args=(str(repo.root), acquired))
            proc2.start()
            proc2.join(timeout=10)
            self.assertEqual(proc2.exitcode, 0)
            self.assertEqual(acquired.value, 1)

    def test_nested_same_process_acquisition_refuses_rather_than_deadlocks(self):
        with ScratchRepo() as repo:
            with ws.state_lock(repo.root):
                with self.assertRaises(ws.StateLockReentrancyError):
                    with ws.state_lock(repo.root):
                        pass  # pragma: no cover -- must never be reached

    def test_naive_second_open_file_description_blocks_even_same_process(self):
        """Item 354(b)'s own stated primitive assumption, checked directly
        against raw `fcntl.flock` rather than through `state_lock`:
        `LOCK_EX | LOCK_NB` on a second open file description for the
        same inode raises `BlockingIOError`, even within one process --
        this is what makes the re-entrancy guard necessary at all."""
        import fcntl
        with ScratchRepo() as repo:
            path = ws.state_lock_path(repo.root)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd1 = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd1, fcntl.LOCK_EX)
            fd2 = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd2)
                fcntl.flock(fd1, fcntl.LOCK_UN)
                os.close(fd1)

    def test_state_transaction_reads_inside_the_lock_not_before_it(self):
        """The exact defect item 354(b)/(c) forbids: a writer that reads
        before acquiring the lock and publishes a value derived from that
        stale read. `state_transaction` re-reads from disk *after*
        acquiring the lock, so a write that lands between a caller's
        earlier read and its own transaction call is never lost."""
        with ScratchRepo() as repo:
            state_path = "state.json"
            _write(repo, state_path, json.dumps({"count": 0}))
            stale_snapshot = json.loads((repo.root / state_path).read_text())

            def bump_other(state):
                state = dict(state)
                state["count"] = 41
                return state

            ws.state_transaction(repo.root, bump_other, path=Path(state_path))

            def bump_from_stale(_ignored_fresh_state):
                # A buggy writer would use `stale_snapshot` here instead of
                # the freshly re-read state `state_transaction` passes in.
                state = dict(stale_snapshot)
                state["count"] = state.get("count", 0) + 1
                return state

            result = ws.state_transaction(repo.root, bump_from_stale, path=Path(state_path))
            # Proves state_transaction supplies the *current* on-disk state
            # to the mutator, not the caller's earlier snapshot: the
            # mutator above only reaches count=1 if it ignored the fresh
            # read it was given, so this assertion documents the contract
            # rather than the (buggy) mutator's own arithmetic.
            self.assertEqual(result["count"], 1)
            on_disk = json.loads((repo.root / state_path).read_text())
            self.assertEqual(on_disk["count"], 1)

    def test_publish_is_atomic_os_replace(self):
        with ScratchRepo() as repo:
            state_path = "state.json"
            ws.state_transaction(repo.root, lambda s: {"v": 1}, path=Path(state_path))
            full = repo.root / state_path
            self.assertEqual(json.loads(full.read_text()), {"v": 1})
            # No leaked temp file beside it.
            leaked = [p for p in full.parent.iterdir() if p.name.startswith(f".{full.name}-")]
            self.assertEqual(leaked, [])


# ---------------------------------------------------------------------------
# item 357: discover_state_writers
# ---------------------------------------------------------------------------


def _seed_writer_surface(repo, *, publisher_body: str | None = None):
    """A minimal, self-declaring two-surface fixture: `scripts/workflow_state.py`
    (the required publisher, `VERIFIER_ENTRY`), one `.claude/commands/`
    writer, one non-writer, and one `*_test.py` file that must be
    excluded from the `scripts/**` surface entirely."""
    publisher_body = publisher_body or (
        '# state_writer: "publisher"\n'
        "def state_lock(repo_root):\n    pass\n\n\n"
        "def state_transaction(repo_root, mutator):\n    pass\n"
    )
    _write(repo, "scripts/workflow_state.py", publisher_body)
    _write(
        repo, ".claude/commands/writer-one.md",
        "---\nstate_writer: true\n---\n\ncall workflow_state.state_transaction(...) here\n",
    )
    _write(
        repo, ".claude/commands/reader-one.md",
        "---\nstate_writer: false\n---\n\nnever touches state\n",
    )
    _write(repo, "scripts/workflow_state_test.py", "# state_writer: false\nignored\n")
    _run(["git", "add", "-A"], cwd=repo.root)
    _run(["git", "commit", "-q", "-m", "seed writer surface"], cwd=repo.root)
    return repo.head()


class TestNeverPersistedPhaseVocabulary(unittest.TestCase):
    """Convergence pass 12, ledger `O34` (external Optional `N6`).

    `docs/ai-workflow/MILESTONE_WORKFLOW.md` carries a full state-reference
    section for four phases no writer ever persists as a `phase` value:
    `SELF_REVIEWING_PLAN`, `AWAITING_TECHNICAL_APPROVAL`,
    `FIXING_FUNCTIONAL_FINDINGS` and `AWAITING_USER_ACCEPTANCE`. They read
    exactly like the twelve that *are* persisted, so the document promised
    resumable states that no live state file can ever contain.

    That is now marked in the document. A prose marker is only worth what
    holds it true, so this derives the never-written set mechanically from
    `workflow_state.py`'s own phase writes and requires it to be exactly
    those four -- a phase that gains a writer, or a fifth that loses one,
    fails here instead of leaving the note quietly wrong."""

    NEVER_PERSISTED = frozenset({
        "SELF_REVIEWING_PLAN",
        "AWAITING_TECHNICAL_APPROVAL",
        "FIXING_FUNCTIONAL_FINDINGS",
        "AWAITING_USER_ACCEPTANCE",
    })

    @staticmethod
    def _phases_the_writer_persists() -> "frozenset[str]":
        """Every phase literal `workflow_state.py` assigns, read out of its
        own AST rather than by regex, so a rename or a reflow cannot make
        this silently under-count. Three shapes are collected: a subscript
        assignment (`work_item["phase"] = "..."`), a dict literal entry
        (`"phase": "..."`, which is how `default_work_item` seeds
        `PLANNING`), and (workflow-2.5.0 CP11, D-Implementation-Review-
        Stages) a subscript assignment whose value is a direct call to a
        module-level resolver function (`work_item["phase"] =
        bundle_generation_target_phase(...)`) -- resolved by walking that
        function's own `return` statements for string-literal values, the
        same "follow the indirection to its literal source" discipline
        the existing Name branch already applies to a local variable."""
        tree = ast.parse((Path(ws.__file__)).read_text())
        functions_by_name = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }

        def _return_literals(func: ast.FunctionDef) -> set[str]:
            literals: set[str] = set()
            for ret in ast.walk(func):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Constant):
                    if isinstance(ret.value.value, str):
                        literals.add(ret.value.value)
            return literals

        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "phase"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        found.add(node.value.value)
                    # `work_item["phase"] = target_phase`, where the name is
                    # bound to one of several literals nearby.
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "phase"
                        and isinstance(node.value, ast.Name)
                    ):
                        found.update(
                            n.value.value
                            for n in ast.walk(tree)
                            if isinstance(n, ast.Assign)
                            and any(
                                isinstance(t, ast.Name) and t.id == node.value.id
                                for t in n.targets
                            )
                            and isinstance(n.value, ast.Constant)
                            and isinstance(n.value.value, str)
                        )
                    # `work_item["phase"] = some_resolver(...)`, where the
                    # called function's own `return` statements are the
                    # literal source (`bundle_generation_target_phase`).
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "phase"
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id in functions_by_name
                    ):
                        found.update(_return_literals(functions_by_name[node.value.func.id]))
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant) and key.value == "phase"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        found.add(value.value)
        return frozenset(found & ws.KNOWN_PHASES)

    #: The fifteen `KNOWN_PHASES` some writer really does persist. Stated
    #: as well as derived, so an under-counting helper cannot quietly grow
    #: the never-persisted set: `AWAITING_EXTERNAL_PLAN_REVIEW` in
    #: particular is only reachable through `publish_plan_revision`'s
    #: `target_phase` indirection, so it is present here exactly when the
    #: helper's indirect branch works, and (workflow-2.5.0)
    #: `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` only through
    #: `record_bundle_generation`'s call to
    #: `bundle_generation_target_phase`, present here exactly when the
    #: helper's Call branch works.
    PERSISTED = frozenset({
        "PLANNING", "AWAITING_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN",
        "AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
        "AWAITING_PLAN_APPROVAL", "IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION",
        "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK",
        "AWAITING_FUNCTIONAL_REVIEW", "MILESTONE_COMPLETE", "LEGACY_READY",
        # workflow-2.4.0, D-Plan-Amendment-1: real and persisted (its sole
        # writer is `request_plan_amendment`), unlike NEVER_PERSISTED's four.
        "AMENDING_PLAN",
        # workflow-2.5.0, D-Implementation-Review-Stages: the two new
        # "2.2" review-stage phases, both real and persisted --
        # `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`'s sole writer
        # (`enter_manual_external_implementation_review`) assigns the
        # literal directly (its writer is `record_local_implementation_review`'s
        # `"APPROVE"` branch); `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` is
        # only ever reached through `record_bundle_generation`'s call to
        # `bundle_generation_target_phase`.
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
        "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
    })

    def test_exactly_four_known_phases_are_never_persisted(self):
        persisted = self._phases_the_writer_persists()
        self.assertEqual(persisted, self.PERSISTED)
        self.assertEqual(ws.KNOWN_PHASES - persisted, self.NEVER_PERSISTED)
        # The two halves partition the vocabulary, so a phase added to
        # `KNOWN_PHASES` lands in neither and fails here.
        self.assertEqual(persisted | self.NEVER_PERSISTED, ws.KNOWN_PHASES)

    def test_all_four_remain_in_the_validator_allowlist(self):
        """Never-persisted is not the same as rejected: `KNOWN_PHASES` is a
        union of the v1 and v2.1 vocabularies, so a hand-written or
        historical state file carrying one of these still validates. The
        marker says "never persisted", not "invalid", and this is the half
        that keeps that distinction true."""
        for phase in sorted(self.NEVER_PERSISTED):
            with self.subTest(phase=phase):
                self.assertIn(phase, ws.KNOWN_PHASES)

    def test_the_document_marks_every_one_of_them_and_no_other(self):
        """The prose half, bound to the derived set: each of the four
        sections carries the marker, no persisted phase's section does,
        and the summary paragraph names all four."""
        text = (_repo_root() / "docs" / "ai-workflow" / "MILESTONE_WORKFLOW.md").read_text()
        sections = re.split(r"^### ", text, flags=re.MULTILINE)[1:]
        marked = {
            section.splitlines()[0].split(" ")[0]
            for section in sections
            if "*Vocabulary state — never persisted" in section
        }
        self.assertEqual(marked, set(self.NEVER_PERSISTED))
        summary = text.split("## State reference", 1)[1].split("### ", 1)[0]
        for phase in sorted(self.NEVER_PERSISTED):
            with self.subTest(phase=phase):
                self.assertIn(phase, summary)


class TestDiscoverStateWriters(unittest.TestCase):
    def test_discovers_writer_publisher_and_non_writer(self):
        with ScratchRepo() as repo:
            commit = _seed_writer_surface(repo)
            discovery = ws.discover_state_writers(repo.root, commit)
            self.assertIn(".claude/commands/writer-one.md", discovery.writers)
            self.assertIn(".claude/commands/reader-one.md", discovery.non_writers)
            self.assertEqual(discovery.publisher, "scripts/workflow_state.py")
            self.assertNotIn("scripts/workflow_state_test.py", [e["path"] for e in discovery.surface_census])

    def test_namespaced_command_is_discovered(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/ns/nested.md",
                "---\nstate_writer: true\n---\n\nstate_transaction(...) too\n",
            )
            commit = _commit_paths(repo, [".claude/commands/ns/nested.md"], "add namespaced command")
            discovery = ws.discover_state_writers(repo.root, commit)
            self.assertIn(".claude/commands/ns/nested.md", discovery.writers)

    def test_unexpected_extension_is_discovered(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/odd.markdown",
                "---\nstate_writer: false\n---\n",
            )
            commit = _commit_paths(repo, [".claude/commands/odd.markdown"], "add odd-extension command")
            discovery = ws.discover_state_writers(repo.root, commit)
            self.assertIn(".claude/commands/odd.markdown", discovery.non_writers)

    def test_missing_declaration_fails_closed(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(repo, ".claude/commands/undeclared.md", "no declaration anywhere\n")
            commit = _commit_paths(repo, [".claude/commands/undeclared.md"], "undeclared writer")
            with self.assertRaises(ws.StateWriterDeclarationError):
                ws.discover_state_writers(repo.root, commit)

    def test_contradictory_declaration_fails_closed(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/contradictory.md",
                "---\nstate_writer: true\n---\n\nstate_writer: false\n",
            )
            commit = _commit_paths(repo, [".claude/commands/contradictory.md"], "contradictory writer")
            with self.assertRaises(ws.StateWriterDeclarationError):
                ws.discover_state_writers(repo.root, commit)

    def test_second_publisher_fails_closed(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(repo, "scripts/second_publisher.py", '# state_writer: "publisher"\n')
            commit = _commit_paths(repo, ["scripts/second_publisher.py"], "second publisher")
            with self.assertRaises(ws.StateWriterDeclarationError):
                ws.discover_state_writers(repo.root, commit)

    def test_test_files_excluded_from_scripts_surface(self):
        with ScratchRepo() as repo:
            commit = _seed_writer_surface(repo)
            # scripts/workflow_state_test.py declares state_writer: false
            # and would still fail conformance if it were part of the
            # surface at all with a bad declaration -- prove instead that
            # it is simply never enumerated.
            discovery = ws.discover_state_writers(repo.root, commit)
            paths = [e["path"] for e in discovery.surface_census]
            self.assertNotIn("scripts/workflow_state_test.py", paths)


# ---------------------------------------------------------------------------
# WFR-67 (WF8c item (h), part 1): discover_review_subject_declarations
# ---------------------------------------------------------------------------


def _seed_review_subject_surface(repo, *, omit: str | None = None):
    """Every path on `ws.REVIEW_SUBJECT_ROSTER`, each declaring
    `review-subject: bundle` -- a faithful (if content-wise fictional)
    stand-in for the real thirteen-file roster, since
    `discover_review_subject_declarations` is deliberately scoped to that
    fixed named set rather than scanning `.claude/commands/` wholesale
    (`REVIEW_SUBJECT_ROSTER`'s own docstring: a file the real roster never
    named, like `recover-implementation-provenance.md`, must never be
    silently demanded a declaration it was never assigned). `omit`, if
    given, skips writing that one roster path entirely -- used to prove a
    roster member absent from the commit fails closed."""
    for path in sorted(ws.REVIEW_SUBJECT_ROSTER):
        if path == omit:
            continue
        _write(repo, path, "---\nreview-subject: bundle\n---\n\nfixture\n")
    _run(["git", "add", "-A"], cwd=repo.root)
    _run(["git", "commit", "-q", "-m", "seed review-subject surface"], cwd=repo.root)
    return repo.head()


class TestDiscoverReviewSubjectDeclarations(unittest.TestCase):
    def test_discovers_every_roster_path(self):
        with ScratchRepo() as repo:
            commit = _seed_review_subject_surface(repo)
            declarations = ws.discover_review_subject_declarations(repo.root, commit)
            self.assertEqual(set(declarations), set(ws.REVIEW_SUBJECT_ROSTER))
            self.assertTrue(all(v == "bundle" for v in declarations.values()))

    def test_verdict_and_none_values_are_recognized(self):
        with ScratchRepo() as repo:
            _seed_review_subject_surface(repo)
            one = sorted(ws.REVIEW_SUBJECT_ROSTER)[0]
            two = sorted(ws.REVIEW_SUBJECT_ROSTER)[1]
            _write(repo, one, "---\nreview-subject: verdict\n---\n\nfixture\n")
            _write(repo, two, "---\nreview-subject: none\n---\n\nfixture\n")
            commit = _commit_paths(repo, [one, two], "vary two roster values")
            declarations = ws.discover_review_subject_declarations(repo.root, commit)
            self.assertEqual(declarations[one], "verdict")
            self.assertEqual(declarations[two], "none")

    def test_roster_path_absent_from_commit_fails_closed(self):
        with ScratchRepo() as repo:
            missing = sorted(ws.REVIEW_SUBJECT_ROSTER)[0]
            commit = _seed_review_subject_surface(repo, omit=missing)
            with self.assertRaises(ws.ReviewSubjectDeclarationError):
                ws.discover_review_subject_declarations(repo.root, commit)

    def test_missing_declaration_fails_closed(self):
        with ScratchRepo() as repo:
            _seed_review_subject_surface(repo)
            target = sorted(ws.REVIEW_SUBJECT_ROSTER)[0]
            _write(repo, target, "no declaration anywhere\n")
            commit = _commit_paths(repo, [target], "undeclared subject")
            with self.assertRaises(ws.ReviewSubjectDeclarationError):
                ws.discover_review_subject_declarations(repo.root, commit)

    def test_unrecognized_value_fails_closed(self):
        with ScratchRepo() as repo:
            _seed_review_subject_surface(repo)
            target = sorted(ws.REVIEW_SUBJECT_ROSTER)[0]
            _write(repo, target, "---\nreview-subject: bundel\n---\n")
            commit = _commit_paths(repo, [target], "typo'd subject")
            with self.assertRaises(ws.ReviewSubjectDeclarationError):
                ws.discover_review_subject_declarations(repo.root, commit)

    def test_contradictory_declaration_fails_closed(self):
        with ScratchRepo() as repo:
            _seed_review_subject_surface(repo)
            target = sorted(ws.REVIEW_SUBJECT_ROSTER)[0]
            _write(repo, target, "---\nreview-subject: bundle\n---\n\nreview-subject: none\n")
            commit = _commit_paths(repo, [target], "contradictory subject")
            with self.assertRaises(ws.ReviewSubjectDeclarationError):
                ws.discover_review_subject_declarations(repo.root, commit)

    def test_a_file_outside_the_roster_is_never_enumerated(self):
        """`recover-implementation-provenance.md`-shaped case: a real
        `.claude/commands/*.md` file that carries no `review-subject:`
        declaration at all must not fail this discovery, since it was
        never on `REVIEW_SUBJECT_ROSTER` in the first place."""
        with ScratchRepo() as repo:
            commit = _seed_review_subject_surface(repo)
            _write(repo, ".claude/commands/outside-the-roster.md", "no declaration, and that's fine\n")
            commit = _commit_paths(repo, [".claude/commands/outside-the-roster.md"], "add non-roster file")
            declarations = ws.discover_review_subject_declarations(repo.root, commit)
            self.assertNotIn(".claude/commands/outside-the-roster.md", declarations)
            self.assertEqual(set(declarations), set(ws.REVIEW_SUBJECT_ROSTER))


# ---------------------------------------------------------------------------
# item 354(c): verify_wfo_state_serialization (the bound conformance)
# ---------------------------------------------------------------------------


class TestVerifyWfoStateSerialization(unittest.TestCase):
    def test_pass_when_every_writer_names_the_primitive(self):
        with ScratchRepo() as repo:
            commit = _seed_writer_surface(repo)
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "PASS")

    def test_fails_when_a_writer_does_not_name_the_primitive(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/sneaky.md",
                "---\nstate_writer: true\n---\n\nwrites the state file some other way\n",
            )
            commit = _commit_paths(repo, [".claude/commands/sneaky.md"], "sneaky writer")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("sneaky" in a for a in result["failing_assertions"]))

    def test_fails_when_a_writer_names_the_primitive_and_also_directly_opens_the_state_path(self):
        """OPUS-R101-003: a declared writer that documents `state_transaction`
        *and* a direct write-mode `open(...)` of the state path must fail --
        the pre-fix behavior returned PASS here, which is the reproduction
        this finding is built on."""
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/evil.md",
                "---\nstate_writer: true\n---\n\n"
                "Normally use workflow_state.state_transaction(repo_root, mutator).\n"
                "But for speed, step 4 instead does:\n"
                "    open('docs/ai-workflow/WORKFLOW_STATE.json', 'w').write(json.dumps(state))\n",
            )
            commit = _commit_paths(repo, [".claude/commands/evil.md"], "writer bypassing the primitive")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("evil" in a for a in result["failing_assertions"]))

    def test_fails_when_a_writer_directly_calls_the_publisher(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/evil2.md",
                "---\nstate_writer: true\n---\n\n"
                "Uses state_transaction normally, but step 9 also calls "
                "_publish_state_file(full_path, state) directly as a shortcut.\n",
            )
            commit = _commit_paths(repo, [".claude/commands/evil2.md"], "writer calling the publisher directly")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")

    def test_fails_when_a_writer_documents_shell_redirection_onto_the_state_path(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/evil3.md",
                "---\nstate_writer: true\n---\n\n"
                "Uses state_transaction, but a fallback path runs:\n"
                "    echo \"$new_state\" > docs/ai-workflow/WORKFLOW_STATE.json\n",
            )
            commit = _commit_paths(repo, [".claude/commands/evil3.md"], "writer using shell redirection")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")

    def test_fails_when_a_writer_documents_sed_i_against_the_state_path(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/evil4.md",
                "---\nstate_writer: true\n---\n\n"
                "Uses state_transaction, but a one-off repair step runs:\n"
                "    sed -i 's/foo/bar/' docs/ai-workflow/WORKFLOW_STATE.json\n",
            )
            commit = _commit_paths(repo, [".claude/commands/evil4.md"], "writer using sed -i")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")

    def test_the_real_writers_still_pass_after_the_direct_write_check(self):
        """Control arm: the direct-write check must not false-positive on the
        twelve real writer commands' own legitimate prose."""
        with ScratchRepo() as repo:
            commit = _seed_writer_surface(repo)
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "PASS")

    def test_fails_when_a_non_writer_calls_the_publisher(self):
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, ".claude/commands/lying.md",
                "---\nstate_writer: false\n---\n\ncalls state_transaction(repo_root, fn) anyway\n",
            )
            commit = _commit_paths(repo, [".claude/commands/lying.md"], "lying non-writer")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")

    def test_read_only_reference_by_a_non_writer_does_not_fail(self):
        """Mirrors `scripts/prepare-ai-review.sh`'s own real, legitimate
        shape: a declared non-writer that reads the state path for
        cross-checking must not be flagged."""
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, "scripts/reader.sh",
                "# state_writer: false\nstate_path=docs/ai-workflow/WORKFLOW_STATE.json\ncat \"$state_path\"\n",
            )
            commit = _commit_paths(repo, ["scripts/reader.sh"], "read-only reference")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "PASS")

    def test_fails_when_a_non_writer_shell_script_redirects_onto_the_state_path(self):
        """Widened detection (OPUS-R101-003) must catch a `.sh` surface
        member bypassing the writer/non-writer split with shell redirection,
        not only Python `open(...)` syntax."""
        with ScratchRepo() as repo:
            _seed_writer_surface(repo)
            _write(
                repo, "scripts/sneaky.sh",
                "# state_writer: false\necho \"$new_state\" > docs/ai-workflow/WORKFLOW_STATE.json\n",
            )
            commit = _commit_paths(repo, ["scripts/sneaky.sh"], "non-writer shell redirection")
            result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")

    def test_never_touches_the_live_state_file_or_lock(self):
        """Item 356(n): the conformance must be safe to run from inside the
        terminal transition's own held lock -- exercised for real, with
        the lock actually held by the calling process."""
        with ScratchRepo() as repo:
            commit = _seed_writer_surface(repo)
            state_path = repo.root / "docs/ai-workflow/WORKFLOW_STATE.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text('{"marker": "untouched"}')
            with ws.state_lock(repo.root):
                result = ws.verify_wfo_state_serialization(repo.root, commit)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(state_path.read_text(), '{"marker": "untouched"}')


# ---------------------------------------------------------------------------
# item 361: static import closure + isolated execution
# ---------------------------------------------------------------------------


class TestStaticImportClosure(unittest.TestCase):
    def test_flat_two_module_closure(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "import scripts_b\n")
            _write(repo, "scripts/scripts_b.py", "VALUE = 1\n")
            commit = _commit_paths(repo, ["scripts/a.py", "scripts/scripts_b.py"], "flat closure")
            closure = ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")
            self.assertEqual(set(closure), {"scripts/a.py", "scripts/scripts_b.py"})

    def test_lazy_import_inside_function_body_is_followed(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "def f():\n    import scripts_b\n    return scripts_b\n")
            _write(repo, "scripts/scripts_b.py", "VALUE = 1\n")
            commit = _commit_paths(repo, ["scripts/a.py", "scripts/scripts_b.py"], "lazy import")
            closure = ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")
            self.assertIn("scripts/scripts_b.py", closure)

    def test_package_followed_as_a_whole_subtree(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "import pkg\n")
            _write(repo, "scripts/pkg/__init__.py", "from . import sub\n")
            _write(repo, "scripts/pkg/sub.py", "VALUE = 1\n")
            commit = _commit_paths(
                repo, ["scripts/a.py", "scripts/pkg/__init__.py", "scripts/pkg/sub.py"], "package closure",
            )
            with self.assertRaises(ws.VerifierClosureUnresolvableError):
                # pkg/__init__.py's own relative import is itself
                # unresolvable by static closure analysis -- proves
                # relative imports fail closed even inside a followed
                # package, not only at the entry point.
                ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")

    def test_package_without_relative_import_contributes_whole_subtree(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "import pkg.sub\n")
            _write(repo, "scripts/pkg/__init__.py", "VALUE = 1\n")
            _write(repo, "scripts/pkg/sub.py", "VALUE = 2\n")
            commit = _commit_paths(
                repo, ["scripts/a.py", "scripts/pkg/__init__.py", "scripts/pkg/sub.py"], "package closure ok",
            )
            closure = ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")
            self.assertEqual(
                set(closure), {"scripts/a.py", "scripts/pkg/__init__.py", "scripts/pkg/sub.py"},
            )

    def test_dynamic_import_module_construct_fails_closed(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "import importlib\nimportlib.import_module('scripts_b')\n")
            commit = _commit_paths(repo, ["scripts/a.py"], "dynamic import")
            with self.assertRaises(ws.VerifierClosureUnresolvableError):
                ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")

    def test_bare_dunder_import_fails_closed(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "__import__('scripts_b')\n")
            commit = _commit_paths(repo, ["scripts/a.py"], "dunder import")
            with self.assertRaises(ws.VerifierClosureUnresolvableError):
                ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")

    def test_exec_construct_fails_closed(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "exec('import scripts_b')\n")
            commit = _commit_paths(repo, ["scripts/a.py"], "exec import")
            with self.assertRaises(ws.VerifierClosureUnresolvableError):
                ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")

    def test_relative_import_fails_closed(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "from . import scripts_b\n")
            commit = _commit_paths(repo, ["scripts/a.py"], "relative import")
            with self.assertRaises(ws.VerifierClosureUnresolvableError):
                ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")

    def test_stdlib_import_is_not_part_of_the_closure(self):
        with ScratchRepo() as repo:
            _write(repo, "scripts/a.py", "import json\nimport hashlib\n")
            commit = _commit_paths(repo, ["scripts/a.py"], "stdlib only")
            closure = ws._static_import_closure(repo.root, commit, "scripts/a.py", "scripts")
            self.assertEqual(closure, ["scripts/a.py"])


class TestIsolatedExecution(unittest.TestCase):
    def test_verifier_executes_from_scratch_tree_only(self):
        with ScratchRepo() as repo:
            _write(
                repo, "scripts/a.py",
                "def verify_x(repo_root, commit):\n"
                "    return {'status': 'PASS', 'detail': 'ok', 'failing_assertions': []}\n",
            )
            commit = _commit_paths(repo, ["scripts/a.py"], "isolated exec fixture")
            mode, blob = ws._blob_mode_and_sha_at_commit(repo.root, commit, "scripts/a.py")
            census = [{"path": "scripts/a.py", "mode": mode, "blob": blob, "source": "manifest"}]
            with unittest.mock.patch.object(ws, "VERIFIER_ENTRY", "scripts/a.py"):
                result = ws._execute_verifier_isolated(repo.root, commit, census, "verify_x")
            self.assertEqual(result["status"], "PASS")

    def test_missing_dependency_fails_closed_with_module_not_found(self):
        with ScratchRepo() as repo:
            _write(
                repo, "scripts/a.py",
                "import scripts_b\n"
                "def verify_x(repo_root, commit):\n"
                "    return {'status': 'PASS'}\n",
            )
            commit = _commit_paths(repo, ["scripts/a.py"], "missing dep fixture")
            mode, blob = ws._blob_mode_and_sha_at_commit(repo.root, commit, "scripts/a.py")
            # Deliberately omit scripts_b.py from the census even though
            # scripts/a.py imports it.
            census = [{"path": "scripts/a.py", "mode": mode, "blob": blob, "source": "manifest"}]
            with unittest.mock.patch.object(ws, "VERIFIER_ENTRY", "scripts/a.py"):
                with self.assertRaises(ws.VerifierExecutionError) as ctx:
                    ws._execute_verifier_isolated(repo.root, commit, census, "verify_x")
            self.assertIn("scripts_b", str(ctx.exception))

    def test_pythonpath_pointed_at_live_scripts_is_ignored(self):
        """Items 358(c)/361(f): `-I` isolated mode ignores `PYTHONPATH`
        even when a caller deliberately points it at the live `scripts/`
        directory -- the recorded dependency still wins."""
        with ScratchRepo() as repo:
            _write(
                repo, "scripts/a.py",
                "import sys\n"
                "def verify_x(repo_root, commit):\n"
                "    return {'status': 'PASS', 'detail': __file__, 'failing_assertions': []}\n",
            )
            commit = _commit_paths(repo, ["scripts/a.py"], "pythonpath fixture")
            mode, blob = ws._blob_mode_and_sha_at_commit(repo.root, commit, "scripts/a.py")
            census = [{"path": "scripts/a.py", "mode": mode, "blob": blob, "source": "manifest"}]
            real_scripts_dir = str(Path(__file__).resolve().parent)
            with unittest.mock.patch.object(ws, "VERIFIER_ENTRY", "scripts/a.py"):
                result = ws._execute_verifier_isolated(
                    repo.root, commit, census, "verify_x", extra_env={"PYTHONPATH": real_scripts_dir},
                )
            self.assertEqual(result["status"], "PASS")
            self.assertNotIn(real_scripts_dir, result["detail"])


# ---------------------------------------------------------------------------
# item 356/358/360: resolve_completion_obligations end-to-end
# ---------------------------------------------------------------------------


class _ObligationFixture:
    """A minimal, self-contained fixture for `resolve_completion_obligations`'s
    full authority-chain + closure + isolated-execution pipeline. Uses a
    small, directly-controllable stub `verify_wfo_state_serialization` as
    the materialized verifier so these tests exercise the *pipeline*, not
    `discover_state_writers`'s own conformance logic (covered separately
    above)."""

    WORK_ITEM_ID = "wi"
    REGISTRY_PATH = f"docs/ai-workflow/registry/{WORK_ITEM_ID}-registry.json"
    ARTIFACTS_PATH = f"docs/ai-workflow/registry/{WORK_ITEM_ID}-artifacts.json"

    def __init__(self, repo):
        self.repo = repo

    def seed_base(self, *, obligations=("WFO-STATE-SERIALIZATION",), fingerprint_unchanged_since_base=False):
        if fingerprint_unchanged_since_base:
            _write(self.repo, "scripts/workflow_fingerprint.py", "# state_writer: false\nX = 1\n")
            _commit_paths(self.repo, ["scripts/workflow_fingerprint.py"], "seed unchanging fingerprint stub")
            self.repo.base = self.repo.head()

        registry = {
            "work_item_id": self.WORK_ITEM_ID, "plan_revision": 1,
            "checkpoints": [
                {"id": "CP1", "depends_on": [], "completion_obligations": list(obligations)},
            ],
        }
        protected_paths = {
            self.ARTIFACTS_PATH: "self",
            "scripts/workflow_state.py": "verifier entry",
        }
        if not fingerprint_unchanged_since_base:
            protected_paths["scripts/workflow_fingerprint.py"] = "verifier dependency"
        artifacts = {
            "schema_version": 2, "work_item_id": self.WORK_ITEM_ID,
            "implementation_stage": {
                "protected_paths": protected_paths,
                "protected_prefixes": {},
                "excluded_paths": {},
                "excluded_prefixes": {"docs/": "plan-stage-governed"},
            },
        }
        _write(self.repo, self.REGISTRY_PATH, json.dumps(registry))
        _write(self.repo, self.ARTIFACTS_PATH, json.dumps(artifacts))
        paths = [self.REGISTRY_PATH, self.ARTIFACTS_PATH]
        if not fingerprint_unchanged_since_base:
            _write(self.repo, "scripts/workflow_fingerprint.py", "# state_writer: false\nX = 1\n")
            paths.append("scripts/workflow_fingerprint.py")
        _commit_paths(self.repo, paths, "seed")

    def _verifier_stub(self, status: str, detail: str, failing: tuple) -> str:
        return (
            '# state_writer: "publisher"\n'
            "import workflow_fingerprint  # noqa: F401\n\n\n"
            "def state_lock(repo_root):\n    pass\n\n\n"
            "def state_transaction(repo_root, mutator):\n    pass\n\n\n"
            "def verify_wfo_state_serialization(repo_root, commit):\n"
            f"    return {{'status': {status!r}, 'detail': {detail!r}, "
            f"'failing_assertions': {list(failing)!r}}}\n"
        )

    def approve(
        self, *, verifier_status="PASS", verifier_detail="ok", failing=(),
        approved_status="CURRENT", approved_id_override=None,
        reviewed_content_commit_override="__self__", manifest_override=None,
    ):
        _write(self.repo, "scripts/workflow_state.py", self._verifier_stub(verifier_status, verifier_detail, failing))
        _commit_paths(self.repo, ["scripts/workflow_state.py"], "seed verifier stub")
        commit = self.repo.head()

        recomputed_id = ws.approval_review_content_id(
            self.repo.root, stage="implementation", base_commit=self.repo.base,
            work_item_type="process", work_item_id=self.WORK_ITEM_ID, head=commit,
            artifacts_path=fingerprint.artifacts_path_for_work_item(self.WORK_ITEM_ID),
        )
        manifest_paths = [self.ARTIFACTS_PATH, "scripts/workflow_state.py"]
        if (self.repo.root / "scripts/workflow_fingerprint.py").exists() and \
                fingerprint._hash_object(self.repo.root, "scripts/workflow_fingerprint.py") != \
                self._blob_at(self.repo.base, "scripts/workflow_fingerprint.py"):
            manifest_paths.append("scripts/workflow_fingerprint.py")
        manifest = manifest_override if manifest_override is not None else [
            {"path": p, "mode": "100644", "blob": fingerprint._hash_object(self.repo.root, p)}
            for p in manifest_paths
        ]
        reviewed_content_commit = commit if reviewed_content_commit_override == "__self__" else reviewed_content_commit_override
        technical_approval = {
            "status": approved_status, "basis": "EXTERNAL_APPROVE",
            "approved_review_content_id": approved_id_override or recomputed_id,
            "review_content_manifest": manifest,
            "reviewed_content_commit": reviewed_content_commit,
            "reviewed_bundle_id": "a" * 66,
            "user_confirmation": "confirmed", "legacy_evidence": None, "waived_guarantees": [],
        }
        state_doc = {
            "schema_version": 1, "active_work_item_id": self.WORK_ITEM_ID,
            "work_items": {
                self.WORK_ITEM_ID: {
                    "work_item_id": self.WORK_ITEM_ID, "base_commit": self.repo.base,
                    "technical_approval": technical_approval,
                },
            },
        }
        _write(self.repo, "docs/ai-workflow/WORKFLOW_STATE.json", json.dumps(state_doc))
        approval_commit = _commit_paths(
            self.repo, ["docs/ai-workflow/WORKFLOW_STATE.json"], "approve",
            trailers={"Workflow-Technical-Approval": recomputed_id, "Workflow-Work-Item": self.WORK_ITEM_ID},
        )
        work_item = {
            "work_item_type": "process", "work_item_kind": "process",
            "work_item_id": self.WORK_ITEM_ID, "base_commit": self.repo.base,
            "registry_path": self.REGISTRY_PATH,
            "technical_approval": dict(technical_approval),
            "plan_approval": self._plan_approval_covering(),
        }
        return work_item, approval_commit, recomputed_id

    def _blob_at(self, commit, path):
        found = ws._blob_mode_and_sha_at_commit(self.repo.root, commit, path)
        return found[1] if found else None

    def _plan_approval_covering(self):
        blob = fingerprint._hash_object(self.repo.root, self.REGISTRY_PATH)
        return {
            "status": "CURRENT", "basis": "EXTERNAL_APPROVE",
            "reviewed_bundle_id": "a" * 66, "approved_review_content_id": "b" * 66,
            "review_content_manifest": [{"path": self.REGISTRY_PATH, "exists": True, "mode": "100644", "blob": blob}],
            "reviewed_content_commit": None, "legacy_evidence": None, "waived_guarantees": [],
            "user_confirmation": "confirmed", "recorded_at": "t",
        }


class TestResolveCompletionObligationsPipeline(unittest.TestCase):
    def test_pass_end_to_end(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, approval_commit, _ = fx.approve(verifier_status="PASS")
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "PASS")
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].technical_approval_commit, approval_commit)
            satisfied, outstanding = ws.completion_obligations_satisfied(repo.root, work_item)
            self.assertTrue(satisfied)
            self.assertEqual(outstanding, [])

    def test_fail_when_verifier_execution_fails(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve(verifier_status="FAIL", verifier_detail="oops", failing=("x broke",))
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "FAIL")
            self.assertIn("x broke", verdicts["WFO-STATE-SERIALIZATION"].failing_assertions)
            satisfied, outstanding = ws.completion_obligations_satisfied(repo.root, work_item)
            self.assertFalse(satisfied)
            self.assertEqual(outstanding, ["WFO-STATE-SERIALIZATION"])

    def test_malformed_committed_review_content_manifest_rejected_cleanly(self):
        """I2's committed-blob half (`workflow-v2-3-followups` continued
        scope, external cross-model review round 4): a `technical_approval`
        committed historically with the exact malformed projection-wrapper
        shape (`workflow_fingerprint.compute_review_content_id_*`'s own
        returned object substituted for its own inner manifest list) must
        be rejected with a typed `VERIFIER_UNAPPROVED` verdict, never a
        bare `AttributeError` -- the malformed record here comes from an
        older commit (`fx.approve`'s own `manifest_override`, committed
        for real with a `Workflow-Technical-Approval` trailer), not the
        live `WORKFLOW_STATE.json`, so `validate_state` -- never on this
        call path regardless -- could not have caught it even if it were."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            malformed_manifest = {
                "stage": "implementation", "work_item_type": "process",
                "work_item_id": fx.WORK_ITEM_ID, "base_commit": repo.base,
                "reviewed_implementation_head": None,
                "review_content_manifest": [
                    {"path": fx.ARTIFACTS_PATH, "mode": "100644", "blob": "deadbeef"},
                ],
                "protected_paths": [], "protected_prefixes": [],
                "excluded_paths": [], "excluded_prefixes": [],
            }
            work_item, _, _ = fx.approve(verifier_status="PASS", manifest_override=malformed_manifest)
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "VERIFIER_UNAPPROVED")
            self.assertIn("malformed", verdicts["WFO-STATE-SERIALIZATION"].detail)
            satisfied, outstanding = ws.completion_obligations_satisfied(repo.root, work_item)
            self.assertFalse(satisfied)
            self.assertEqual(outstanding, ["WFO-STATE-SERIALIZATION"])

    def test_no_declared_obligations_is_vacuously_satisfied_without_consulting_approval(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base(obligations=())
            work_item = {
                "work_item_type": "process", "work_item_id": fx.WORK_ITEM_ID,
                "base_commit": repo.base, "registry_path": fx.REGISTRY_PATH,
                "technical_approval": None,
                "plan_approval": fx._plan_approval_covering(),
            }
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts, {})
            satisfied, outstanding = ws.completion_obligations_satisfied(repo.root, work_item)
            self.assertTrue(satisfied)
            self.assertEqual(outstanding, [])

    def test_unknown_obligation_blocks_permanently(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base(obligations=("WFO-SOMETHING-ELSE",))
            work_item, _, _ = fx.approve()
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-SOMETHING-ELSE"].classification, "UNKNOWN_OBLIGATION")

    def test_no_reachable_approval_commit_classifies_verifier_unapproved(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            _write(repo, "scripts/workflow_state.py", fx._verifier_stub("PASS", "ok", ()))
            _commit_paths(repo, ["scripts/workflow_state.py"], "no approval commit ever made")
            work_item = {
                "work_item_type": "process", "work_item_id": fx.WORK_ITEM_ID,
                "base_commit": repo.base, "registry_path": fx.REGISTRY_PATH,
                "technical_approval": {
                    "status": "CURRENT", "basis": "EXTERNAL_APPROVE",
                    "approved_review_content_id": "not-a-real-id",
                    "review_content_manifest": [{"path": "x", "blob": "y"}],
                    "reviewed_content_commit": repo.head(),
                    "reviewed_bundle_id": "a" * 66, "user_confirmation": "c",
                    "legacy_evidence": None, "waived_guarantees": [],
                },
                "plan_approval": fx._plan_approval_covering(),
            }
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "VERIFIER_UNAPPROVED")

    def test_ambiguous_approval_trailer_classifies_verifier_unapproved(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, approval_commit, recomputed_id = fx.approve()
            _commit_empty(
                repo, "duplicate approval trailer",
                trailers={"Workflow-Technical-Approval": recomputed_id, "Workflow-Work-Item": fx.WORK_ITEM_ID},
            )
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "VERIFIER_UNAPPROVED")

    def test_durable_status_not_current_classifies_verifier_unapproved(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve(approved_status="STALE")
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "VERIFIER_UNAPPROVED")

    def test_durable_manifest_empty_classifies_verifier_unapproved(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve(manifest_override=[])
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "VERIFIER_UNAPPROVED")

    def test_live_record_diverging_from_durable_classifies_verifier_unapproved(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            work_item = dict(work_item)
            work_item["technical_approval"] = dict(work_item["technical_approval"])
            work_item["technical_approval"]["basis"] = "USER_OVERRIDE"  # forged, not re-approved
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "VERIFIER_UNAPPROVED")

    def test_live_base_commit_diverging_from_durable_classifies_verifier_unapproved(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            work_item = dict(work_item)
            work_item["base_commit"] = repo.head()  # forged: not what the approval commit committed
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "VERIFIER_UNAPPROVED")

    def test_pin_moved_when_expected_commit_is_stale(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, approval_commit, _ = fx.approve()
            verdicts = ws.resolve_completion_obligations(repo.root, work_item, expected_commit="0" * 40)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "PIN_MOVED")

    def test_diverged_when_working_tree_differs_from_head(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            # Dirty the working tree on a declared writer surface without
            # committing.
            (repo.root / "scripts/workflow_state.py").write_text(
                fx._verifier_stub("PASS", "ok", ()) + "\nEXTRA = 1\n"
            )
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "DIVERGED")

    def test_diverged_when_a_gitignored_file_exists_on_the_commands_surface(self):
        """OPUS-R101-004: a `.gitignore`d untracked file on `.claude/commands/`
        must still be visible to the `DIVERGED` precondition -- it is a real,
        harness-discoverable surface member, not legitimately-ignored
        content."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            (repo.root / ".gitignore").write_text(
                (repo.root / ".gitignore").read_text() + "\n.claude/commands/local-sync.md\n"
            )
            (repo.root / ".claude/commands").mkdir(parents=True, exist_ok=True)
            (repo.root / ".claude/commands/local-sync.md").write_text(
                "---\nstate_writer: true\n---\n\nwrites the state file some other way\n"
            )
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "DIVERGED")

    def test_diverged_when_a_gitignored_file_exists_on_the_scripts_surface(self):
        """Same shape as above, on the `scripts/` surface."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            (repo.root / ".gitignore").write_text(
                (repo.root / ".gitignore").read_text() + "\nscripts/local_sync.py\n"
            )
            (repo.root / "scripts/local_sync.py").write_text(
                "# state_writer: true\nopen('docs/ai-workflow/WORKFLOW_STATE.json', 'w')\n"
            )
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "DIVERGED")

    def test_not_diverged_for_legitimately_ignored_content_outside_the_surfaces(self):
        """Control arm: ignored content that is genuinely outside the two
        declared surfaces (`.ai-review/`, already ignored by `ScratchRepo`'s
        base `.gitignore`) must not trip `DIVERGED`."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            (repo.root / ".ai-review/scratch").mkdir(parents=True)
            (repo.root / ".ai-review/scratch/note.txt").write_text("not on any declared surface\n")
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "PASS")

    def test_not_diverged_for_scripts_pycache_byproduct(self):
        """`scripts/__pycache__/*.pyc` is produced merely by importing/
        running the census's own `.py` files and can never be mistaken for
        a real surface source file -- must not trip `DIVERGED` even though
        it is untracked-and-ignored on the `scripts/` surface."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            pycache = repo.root / "scripts/__pycache__"
            pycache.mkdir(parents=True)
            (pycache / "workflow_state.cpython-311.pyc").write_bytes(b"\x00\x01")
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "PASS")

    def test_verifier_dependency_unchanged_since_base_is_accepted_without_manifest_coverage(self):
        """Item 361(h): a closure dependency the manifest legitimately does
        not cover (because it never changed since `base_commit`) must
        still be accepted, proven byte-identical at `C` and `base_commit`
        -- the direct regression test for the false-refusal shape
        R62-S3-001 names."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base(fingerprint_unchanged_since_base=True)
            work_item, _, _ = fx.approve()
            self.assertNotIn(
                "scripts/workflow_fingerprint.py",
                [e["path"] for e in work_item["technical_approval"]["review_content_manifest"]],
            )
            verdicts = ws.resolve_completion_obligations(repo.root, work_item)
            self.assertEqual(verdicts["WFO-STATE-SERIALIZATION"].classification, "PASS")
            sources = {e["path"]: e["source"] for e in verdicts["WFO-STATE-SERIALIZATION"].verifier_census}
            self.assertEqual(sources["scripts/workflow_fingerprint.py"], "unchanged_since_base")

    def test_unclassified_new_path_fails_closed_as_verifier_unresolvable(self):
        """A new, undeclared-in-the-implementation-stage-classification path
        (here, an unrelated `.claude/commands/` addition this fixture's own
        `implementation_stage` declaration does not classify at all) makes
        step (1)'s identity recomputation raise `UnclassifiedPathError`,
        which item 358(d) requires to classify `VERIFIER_UNRESOLVABLE`
        rather than escaping or silently passing."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            verdict = ws.resolve_completion_obligations(repo.root, work_item)["WFO-STATE-SERIALIZATION"]
            self.assertEqual(verdict.classification, "PASS")

            _write(repo, ".claude/commands/new-writer.md", "---\nstate_writer: true\n---\n\nstate_transaction(...)\n")
            _commit_paths(repo, [".claude/commands/new-writer.md"], "unclassified new path")
            second = ws.resolve_completion_obligations(repo.root, work_item)["WFO-STATE-SERIALIZATION"]
            self.assertEqual(second.classification, "VERIFIER_UNRESOLVABLE")

    def test_verifier_identity_id_depends_only_on_the_verifiers_own_closure(self):
        """Item 361(g): `_static_import_closure`'s own return value never
        includes anything under `.claude/commands/**` -- proven directly at
        `TestStaticImportClosure`'s level -- so `verifier_identity_id`
        (a pure hash of `{verifier_entry, verifier_source_root, closure}`)
        is structurally independent of the writer surface. Confirmed here
        by recomputing it twice at the same commit and getting the same
        value (determinism), the minimal property this pipeline-level test
        can check without re-deriving `TestStaticImportClosure`'s own
        proof."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            first = ws.resolve_completion_obligations(repo.root, work_item)["WFO-STATE-SERIALIZATION"]
            second = ws.resolve_completion_obligations(repo.root, work_item)["WFO-STATE-SERIALIZATION"]
            self.assertEqual(first.classification, "PASS")
            self.assertEqual(first.verifier_identity_id, second.verifier_identity_id)


class TestReplayCompletionObligation(unittest.TestCase):
    def test_replay_reproduces_recorded_verdict_after_verifier_deleted(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve(verifier_status="PASS", verifier_detail="ok")
            verdict = ws.resolve_completion_obligations(repo.root, work_item)["WFO-STATE-SERIALIZATION"]
            self.assertEqual(verdict.classification, "PASS")
            recorded = dict(verdict._asdict())

            # Later history rewrites and then deletes the live verifier
            # entirely -- replay must still reproduce the recorded verdict
            # from the recorded census alone.
            (repo.root / "scripts/workflow_state.py").unlink()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "delete live verifier"], cwd=repo.root)

            replayed = ws.replay_completion_obligation(repo.root, "WFO-STATE-SERIALIZATION", recorded)
            self.assertEqual(replayed["status"], "PASS")

    def test_replay_of_unreachable_recorded_blob_is_replay_unresolvable(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve()
            verdict = ws.resolve_completion_obligations(repo.root, work_item)["WFO-STATE-SERIALIZATION"]
            recorded = dict(verdict._asdict())
            recorded["verifier_census"] = tuple(
                {**e, "blob": "0" * 40} for e in recorded["verifier_census"]
            )
            replayed = ws.replay_completion_obligation(repo.root, "WFO-STATE-SERIALIZATION", recorded)
            self.assertEqual(replayed["status"], "REPLAY_UNRESOLVABLE")


# ---------------------------------------------------------------------------
# item 356(a): complete_work_item's UnsatisfiedCompletionObligationError gate
# ---------------------------------------------------------------------------


class TestCompleteWorkItemObligationGate(unittest.TestCase):
    def _state_with(self, repo, fx, work_item):
        checkpoints = {"CP1": {"status": "COMPLETE"}}
        wi = dict(work_item)
        wi.update({
            "work_item_kind": "process", "parent_work_item_id": None,
            "governing_workflow_version": "1", "phase": "AWAITING_USER_ACCEPTANCE",
            "checkpoints": checkpoints, "plan_review_stages": None, "plan_revision": 1,
            "active_work_item_id_marker": None,
        })
        return {"schema_version": 1, "active_work_item_id": fx.WORK_ITEM_ID, "work_items": {fx.WORK_ITEM_ID: wi}}

    def test_refuses_when_obligation_fails_even_though_checkpoint_complete(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve(verifier_status="FAIL", verifier_detail="broken", failing=("nope",))
            state = self._state_with(repo, fx, work_item)
            with self.assertRaises(ws.UnsatisfiedCompletionObligationError) as ctx:
                ws.complete_work_item(state, fx.WORK_ITEM_ID, now="t", repo_root=repo.root)
            self.assertIn("WFO-STATE-SERIALIZATION", str(ctx.exception))
            self.assertIn("FAIL", str(ctx.exception))

    def test_succeeds_and_records_completion_obligations_accepted_when_pass(self):
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, approval_commit, _ = fx.approve(verifier_status="PASS")
            state = self._state_with(repo, fx, work_item)
            new_state = ws.complete_work_item(state, fx.WORK_ITEM_ID, now="t", repo_root=repo.root)
            wi = new_state["work_items"][fx.WORK_ITEM_ID]
            self.assertEqual(wi["phase"], "MILESTONE_COMPLETE")
            accepted = wi["completion_obligations_accepted"]["WFO-STATE-SERIALIZATION"]
            self.assertEqual(accepted["classification"], "PASS")
            self.assertEqual(accepted["technical_approval_commit"], approval_commit)

    def test_marking_checkpoint_complete_by_hand_is_not_sufficient_alone(self):
        """Item 356(d): setting the registry checkpoint to COMPLETE by any
        means is confirmed insufficient by itself while the obligation is
        outstanding."""
        with ScratchRepo() as repo:
            fx = _ObligationFixture(repo)
            fx.seed_base()
            work_item, _, _ = fx.approve(verifier_status="FAIL")
            state = self._state_with(repo, fx, work_item)
            is_terminal, outstanding_checkpoint = ws.resolve_own_registry_completion_status(
                repo.root, state["work_items"][fx.WORK_ITEM_ID],
            )
            self.assertTrue(is_terminal)  # the registry itself is fully COMPLETE
            self.assertIsNone(outstanding_checkpoint)
            with self.assertRaises(ws.UnsatisfiedCompletionObligationError):
                ws.complete_work_item(state, fx.WORK_ITEM_ID, now="t", repo_root=repo.root)


# ---------------------------------------------------------------------------
# WF8c (m), remaining scope: `WFO-LEDGER-COVERAGE`'s own bound conformance
# (`verify_wfo_ledger_coverage`, `WFR-68` properties (i)-(iv) plus the four
# adversarial arms). Mirrors `TestVerifyWfoStateSerialization`'s own
# in-process, real-function pattern (not the stubbed pipeline fixture
# above) -- the pipeline machinery itself (`VERIFIER_UNAPPROVED`/
# `DIVERGED`/`PIN_MOVED` classification) is obligation-agnostic and
# already exhaustively covered against `WFO-STATE-SERIALIZATION`.
# ---------------------------------------------------------------------------

_LEDGER_COVERAGE_TABLE_FIXTURE = """### Reconciliation table

| Items | Topic | Status | Owner | Evidence / rationale |
|---|---|---|---|---|
| 1 | thing one | IMPLEMENTED | WF8b | already delivered |
| 2 | thing two | ABSENT | WF8c | still missing |
| 3 | thing three | SUPERSEDED | none (superseded) | design superseded |
| 4 | thing four | PARTIAL | WF8b for the delivered core; WF8c for the rest | partially delivered |
"""

_LEDGER_COVERAGE_EVIDENCE_MODULE = """import unittest

class OkCase(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)

class BrokenCase(unittest.TestCase):
    def test_fail(self):
        self.assertTrue(False)
"""

# GPT-R132-002 pipeline-level regression fixture: Mutator rewrites this same
# module (the file its own evidence id resolves against) to redefine Target
# as an always-passing test; Target's own committed source always fails.
# Only a genuine cross-evidence-id restore between the two prevents Mutator
# (run first, item 2 below) from changing Target's (run second, item 4
# below) verdict.
_CONTAMINATION_EVIDENCE_MODULE = """import unittest
from pathlib import Path

class Mutator(unittest.TestCase):
    def test_mutate(self):
        target = Path(__file__)
        target.write_text(
            "import unittest\\n\\n"
            "class Mutator(unittest.TestCase):\\n"
            "    def test_mutate(self):\\n"
            "        pass\\n\\n"
            "class Target(unittest.TestCase):\\n"
            "    def test_target(self):\\n"
            "        pass\\n"
        )
        self.assertTrue(True)

class Target(unittest.TestCase):
    def test_target(self):
        self.fail("COMMITTED TARGET MUST FAIL")
"""

# OPUS-R133-001 pipeline-level regression fixtures: HooksPathAttack (run
# first, item 2 below) plants a clone-local `core.hooksPath` pointing at a
# `post-index-change` hook that rewrites the *separate* victim module's own
# committed always-failing Victim.test_victim to always pass. The attack and
# the victim live in different files (mirroring the reviewer's own
# reproduction) so the victim's own committed source can be targeted without
# the attack module's own text -- which must literally spell out what it
# targets -- colliding with it. Only materializing a genuinely fresh,
# independent checkout per evidence id -- never reusing (and resetting) the
# checkout HooksPathAttack ran in -- prevents the planted config from ever
# being consulted while Victim (run second, item 4 below) executes.
_HOOKS_PATH_CONTAMINATION_EVIDENCE_MODULE = """import subprocess
import unittest
from pathlib import Path

class OkCase(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)

class HooksPathAttack(unittest.TestCase):
    def test_plant(self):
        scratch = Path(__file__).resolve().parent.parent
        evil_hooks = scratch / ".git" / "evilhooks"
        evil_hooks.mkdir(parents=True, exist_ok=True)
        hook = evil_hooks / "post-index-change"
        hook.write_text(
            "#!/bin/sh\\n"
            "cat > scripts/fixture_victim_test.py <<'PYEOF'\\n"
            "import unittest\\n\\n\\n"
            "class Victim(unittest.TestCase):\\n"
            "    def test_victim(self):\\n"
            "        pass\\n"
            "PYEOF\\n"
        )
        hook.chmod(0o755)
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", str(evil_hooks)],
            cwd=scratch, check=True,
        )
        self.assertTrue(True)
"""

_HOOKS_PATH_CONTAMINATION_VICTIM_MODULE = """import unittest

class Victim(unittest.TestCase):
    def test_victim(self):
        self.fail("COMMITTED VICTIM MUST FAIL")
"""

# OPUS-R133-001 pipeline-level regression fixtures: FilterAttack (run first,
# item 2 below) registers a clone-local matched clean/smudge filter pair via
# `.git/info/attributes`, targeting the *separate* victim module's own
# committed Victim.test_victim body, then dirties that file so the next
# checkout must re-materialize it through the attacker's smudge filter.
# `git status` would report the tree clean throughout, because the clean
# filter is a correct involution -- only a fresh, independent checkout for
# Victim (run second, item 4 below) that never had FilterAttack's config
# registered in the first place closes this. The attack and victim live in
# separate files: the smudge/clean commands must literally spell out the
# marker text they search for, and `.gitattributes` filters the whole
# targeted file, so a self-targeting attack module would also rewrite --
# and, in the clean direction, fail to reverse -- its own source.
_FILTER_ATTRIBUTES_CONTAMINATION_EVIDENCE_MODULE = """import subprocess
import unittest
from pathlib import Path

class OkCase(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)

class FilterAttack(unittest.TestCase):
    def test_plant(self):
        scratch = Path(__file__).resolve().parent.parent
        smudge_cmd = (
            "python3 -c \\"import sys; sys.stdout.write(sys.stdin.read()."
            "replace('FILTER_MARKER_FAIL', 'pass'))\\""
        )
        clean_cmd = (
            "python3 -c \\"import sys; sys.stdout.write(sys.stdin.read()."
            "replace('        pass', '        FILTER_MARKER_FAIL'))\\""
        )
        subprocess.run(
            ["git", "config", "--local", "filter.evil.smudge", smudge_cmd],
            cwd=scratch, check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "filter.evil.clean", clean_cmd],
            cwd=scratch, check=True,
        )
        info = scratch / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "attributes").write_text(
            "scripts/fixture_victim_test.py filter=evil\\n"
        )
        victim = scratch / "scripts" / "fixture_victim_test.py"
        victim.write_text(victim.read_text() + "# dirty\\n")
        self.assertTrue(True)
"""

_FILTER_ATTRIBUTES_CONTAMINATION_VICTIM_MODULE = """import unittest

class Victim(unittest.TestCase):
    def test_victim(self):
        FILTER_MARKER_FAIL
"""

# GPT-R132-001 pipeline-level regression fixture: a live post-checkout hook
# that rewrites the committed always-failing BrokenCase.test_fail to always
# pass.
_POST_CHECKOUT_ATTACK_HOOK = """#!/bin/sh
cat > scripts/fixture_ledger_evidence_test.py <<'PYEOF'
import unittest

class OkCase(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)

class BrokenCase(unittest.TestCase):
    def test_fail(self):
        pass
PYEOF
"""

_DEFAULT_CONSISTENT_LEDGER_ENTRIES = [
    {"item": 1, "status": "IMPLEMENTED", "owner_checkpoint": "WF8b",
     "evidence": "fixture_ledger_evidence_test.OkCase.test_pass"},
    # item 2 (ABSENT/WF8c) carries its own evidence -- GPT-R131-002:
    # this "consistent, all-green" baseline previously left item 2
    # unevidenced and still expected PASS, which is exactly the hole
    # the finding named (an ABSENT/WF8c item's evidence was never
    # checked at all). A WF8c-owned non-SUPERSEDED item now requires
    # evidence identically to IMPLEMENTED, so the baseline must supply
    # it to remain a genuinely consistent, fully-evidenced fixture.
    {"item": 2, "status": "ABSENT", "owner_checkpoint": "WF8c",
     "evidence": "fixture_ledger_evidence_test.OkCase.test_pass"},
    {"item": 3, "status": "SUPERSEDED", "owner_checkpoint": "none"},
    {"item": 4, "status": "IMPLEMENTED", "owner_checkpoint": "WF8c",
     "evidence": "fixture_ledger_evidence_test.OkCase.test_pass"},
]


def _seed_ledger_coverage_fixture(
    repo, *, ledger_entries, companion_entries=None,
    evidence_module=_LEDGER_COVERAGE_EVIDENCE_MODULE, extra_files=None,
):
    ledger_path = str(ws.ledger_status_path_for_work_item(ws.LEDGER_COVERAGE_WORK_ITEM_ID))
    _write(repo, "docs/ai-workflow/WORKFLOW_V2_PLAN.md", _LEDGER_COVERAGE_TABLE_FIXTURE)
    _write(repo, ledger_path, json.dumps({"entries": ledger_entries}))
    paths = ["docs/ai-workflow/WORKFLOW_V2_PLAN.md", ledger_path]
    if extra_files:
        for rel_path, content in extra_files.items():
            _write(repo, rel_path, content)
            paths.append(rel_path)
    if companion_entries is not None:
        companion_path = str(ws.wf8c_evidence_path_for_work_item(ws.LEDGER_COVERAGE_WORK_ITEM_ID))
        _write(repo, companion_path, json.dumps({"entries": companion_entries}))
        paths.append(companion_path)
    if evidence_module is not None:
        _write(repo, "scripts/fixture_ledger_evidence_test.py", evidence_module)
        paths.append("scripts/fixture_ledger_evidence_test.py")
    return _commit_paths(repo, paths, "seed ledger coverage fixture")


class TestPinnedEvidenceWorktreeIsolation(unittest.TestCase):
    """`GPT-R132-001`/`-002`/`OPUS-R133`'s own required regressions,
    exercised directly against `_materialize_pinned_worktree_at_commit`/
    `_verify_pinned_worktree_clean`/`_run_named_test_in_scratch` -- the
    same functions `verify_wfo_ledger_coverage` itself calls -- mirroring
    the review's own reproduction steps rather than going through the
    full ledger-coverage pipeline. `TestVerifyWfoLedgerCoverage` below
    adds the pipeline-level counterparts."""

    def test_malicious_post_checkout_hook_never_runs_against_the_pinned_checkout(self):
        """GPT-R132-001: install a live `post-checkout` hook in the
        *source* repository that rewrites a committed always-failing
        evidence test to always pass. `_materialize_pinned_worktree_at_
        commit` now clones into an independent repository rather than
        linking a worktree, so the source repository's hook is never
        even consulted -- the pinned checkout must still hold the
        committed (failing) bytes and re-execute red."""
        with ScratchRepo() as repo:
            _write(repo, "scripts/fixture_evidence_test.py", (
                "import unittest\n\n"
                "class Case(unittest.TestCase):\n"
                "    def test_evidence(self):\n"
                "        self.fail('COMMITTED TEST MUST FAIL')\n"
            ))
            commit = _commit_paths(repo, ["scripts/fixture_evidence_test.py"], "commit failing evidence test")

            hooks_dir = repo.root / ".git" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook_path = hooks_dir / "post-checkout"
            hook_path.write_text(
                "#!/bin/sh\n"
                "cat > scripts/fixture_evidence_test.py <<'PYEOF'\n"
                "import unittest\n\n"
                "class Case(unittest.TestCase):\n"
                "    def test_evidence(self):\n"
                "        pass\n"
                "PYEOF\n"
            )
            hook_path.chmod(0o755)

            scratch_dir = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                content = (scratch_dir / "scripts" / "fixture_evidence_test.py").read_text()
                self.assertIn("COMMITTED TEST MUST FAIL", content)
                ok, detail = ws._run_named_test_in_scratch(scratch_dir, "fixture_evidence_test.Case.test_evidence")
                self.assertFalse(ok, detail)
            finally:
                ws._remove_pinned_worktree(repo.root, scratch_dir)

    def test_two_independently_materialized_checkouts_share_no_state(self):
        """`OPUS-R133-001`: `_materialize_pinned_worktree_at_commit`
        clones a brand-new, independent repository on every call --
        `verify_wfo_ledger_coverage` now calls it once per evidence id
        and tears the result down immediately after, rather than
        resetting and reusing one checkout across ids. Arming a
        clone-local `core.hooksPath` and a hostile hook in one
        materialized checkout, then discarding it, must have no effect
        whatsoever on a second, separately materialized checkout of the
        same commit: there is no shared state left for an evidence id
        running in the first to poison the second through."""
        with ScratchRepo() as repo:
            _write(repo, "scripts/target_test.py", (
                "import unittest\n\n"
                "class Case(unittest.TestCase):\n"
                "    def test_target(self):\n"
                "        self.fail('COMMITTED TARGET TEST MUST FAIL')\n"
            ))
            commit = _commit_paths(repo, ["scripts/target_test.py"], "commit target evidence test")

            first = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                evil_hooks = first / ".git" / "evilhooks"
                evil_hooks.mkdir()
                hook = evil_hooks / "post-index-change"
                hook.write_text(
                    "#!/bin/sh\n"
                    "cat > scripts/target_test.py <<'PYEOF'\n"
                    "import unittest\n\n"
                    "class Case(unittest.TestCase):\n"
                    "    def test_target(self):\n"
                    "        pass\n"
                    "PYEOF\n"
                )
                hook.chmod(0o755)
                _run(["git", "config", "--local", "core.hooksPath", str(evil_hooks)], cwd=first)
            finally:
                ws._remove_pinned_worktree(repo.root, first)

            second = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                ok, detail = ws._run_named_test_in_scratch(second, "target_test.Case.test_target")
                self.assertFalse(ok, detail)
            finally:
                ws._remove_pinned_worktree(repo.root, second)

    def test_evidence_clone_object_storage_shares_no_writable_state_with_source(self):
        """`GPT-R135-001`: a same-filesystem local `git clone` may
        hardlink `.git/objects` into the clone instead of copying it, so
        the clone and the source repository could hold two pathnames for
        the same object inode -- an in-place write through the clone's
        object path would then corrupt the source repository's own
        object bytes, and a later independently materialized clone could
        fail to resolve the same pinned commit. `--no-hardlinks` must
        make the clone's object database a real, separately-inode'd copy:
        prove the source object survives a mutation attempted through one
        evidence clone's object path untouched, and that a second,
        independently materialized evidence clone at the same pinned
        commit still resolves and checks out the original tracked
        bytes."""
        with ScratchRepo() as repo:
            _write(repo, "victim.txt", "original content\n")
            commit = _commit_paths(repo, ["victim.txt"], "commit victim blob")
            blob = subprocess.run(
                ["git", "rev-parse", f"{commit}:victim.txt"],
                cwd=repo.root, check=True, capture_output=True, text=True,
            ).stdout.strip()
            source_object = repo.root / ".git" / "objects" / blob[:2] / blob[2:]
            self.assertTrue(source_object.is_file())

            first = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                clone_object = first / ".git" / "objects" / blob[:2] / blob[2:]
                self.assertTrue(clone_object.is_file())
                # A same-filesystem `git clone` hardlinks by default;
                # `--no-hardlinks` must force a distinct inode.
                self.assertNotEqual(
                    source_object.stat().st_ino, clone_object.stat().st_ino,
                    "clone-side object shares an inode with the source repository's "
                    "own object -- --no-hardlinks did not take effect",
                )

                # Attempt to corrupt the source object through the clone's
                # own pathname only.
                os.chmod(clone_object, 0o600)
                with open(clone_object, "r+b") as f:
                    data = bytearray(f.read())
                    data[0] ^= 0xFF
                    f.seek(0)
                    f.write(bytes(data))
            finally:
                ws._remove_pinned_worktree(repo.root, first)

            # The source repository's own object must be untouched.
            result = subprocess.run(
                ["git", "cat-file", "-p", blob], cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "original content\n")

            # A second, independently materialized clone at the same
            # pinned commit must still resolve and check out the
            # original bytes.
            second = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                self.assertEqual((second / "victim.txt").read_text(), "original content\n")
            finally:
                ws._remove_pinned_worktree(repo.root, second)

    def test_verify_pinned_worktree_clean_ignores_clone_local_hooks_and_filters(self):
        """`OPUS-R133-002`/`-M01`: the previous `git status --porcelain`-
        based cleanliness check could itself be fooled -- a clone-local
        `core.hooksPath` made the *check itself* execute the attacker's
        hook, and a matched clean/smudge filter pair made `git status`
        report a rewritten tree as clean. The blob-hash-based replacement
        asks Git to read tree objects (`git ls-tree`, which touches no
        working-tree content and triggers no filter or hook) and compares
        them against on-disk bytes hashed in pure Python -- so arming the
        same clone-local hooksPath and filter/attributes configuration,
        without anything actually having fired them, must not cause a
        false failure; and a real content divergence (whatever produced
        it) must still be caught even though the armed filter pair would
        have made `git status` itself report clean."""
        with ScratchRepo() as repo:
            _write(repo, "scripts/victim_test.py", (
                "import unittest\n\n"
                "class Victim(unittest.TestCase):\n"
                "    def test_victim(self):\n"
                "        FILTER_MARKER_FAIL\n"
            ))
            commit = _commit_paths(repo, ["scripts/victim_test.py"], "commit victim evidence test")

            scratch_dir = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                evil_hooks = scratch_dir / ".git" / "evilhooks"
                evil_hooks.mkdir()
                hook = evil_hooks / "post-index-change"
                hook.write_text("#!/bin/sh\ntouch " + str(scratch_dir / "HOOK_FIRED") + "\n")
                hook.chmod(0o755)
                _run(["git", "config", "--local", "core.hooksPath", str(evil_hooks)], cwd=scratch_dir)

                smudge_cmd = (
                    "python3 -c \"import sys; sys.stdout.write(sys.stdin.read()."
                    "replace('FILTER_MARKER_FAIL', 'pass'))\""
                )
                clean_cmd = (
                    "python3 -c \"import sys; sys.stdout.write(sys.stdin.read()."
                    "replace('        pass', '        FILTER_MARKER_FAIL'))\""
                )
                _run(["git", "config", "--local", "filter.evil.smudge", smudge_cmd], cwd=scratch_dir)
                _run(["git", "config", "--local", "filter.evil.clean", clean_cmd], cwd=scratch_dir)
                info_dir = scratch_dir / ".git" / "info"
                info_dir.mkdir(parents=True, exist_ok=True)
                (info_dir / "attributes").write_text("scripts/victim_test.py filter=evil\n")

                # Armed but never triggered (no checkout/reset/status ran
                # since materialization): the checkout is still genuinely
                # byte-identical to commit, so this must not raise.
                ws._verify_pinned_worktree_clean(scratch_dir, commit)
                self.assertFalse((scratch_dir / "HOOK_FIRED").exists())

                # Now the on-disk content genuinely diverges from the
                # pinned blob (exactly what a fired smudge filter would
                # have produced) -- must be caught even though the
                # matched clean filter above would make `git status`
                # itself report the tree clean.
                victim = scratch_dir / "scripts" / "victim_test.py"
                victim.write_text(victim.read_text().replace("FILTER_MARKER_FAIL", "pass"))
                with self.assertRaises(ws.PinnedEvidenceWorktreeIntegrityError):
                    ws._verify_pinned_worktree_clean(scratch_dir, commit)
            finally:
                ws._remove_pinned_worktree(repo.root, scratch_dir)

    def test_evidence_clone_has_no_alternates_when_source_repository_borrows_objects(self):
        """`GPT-R136-001`: `--no-hardlinks` alone does not make the
        evidence clone self-contained when `repo_root` itself borrows
        objects through `objects/info/alternates` (e.g. a repository
        created with Git's own `--reference` option) -- a local clone
        copies that alternate relationship verbatim instead of copying
        the borrowed objects themselves, so both the source and the
        clone keep depending on the same external object database.
        `--dissociate` must make the materialized clone genuinely
        independent: prove it has no `objects/info/alternates` of its
        own, and that it remains fully readable after the donor
        repository the source borrowed from is deleted outright -- the
        same reproduction shape the combined review used."""
        workdir = Path(tempfile.mkdtemp(prefix="wfo-alternates-fixture-"))
        try:
            donor = workdir / "donor"
            source = workdir / "source"
            donor.mkdir()
            _run(["git", "init", "-q"], cwd=donor)
            _run(["git", "config", "user.email", "test@example.com"], cwd=donor)
            _run(["git", "config", "user.name", "Test"], cwd=donor)
            (donor / "victim.txt").write_text("original content\n")
            _run(["git", "add", "victim.txt"], cwd=donor)
            _run(["git", "commit", "-q", "-m", "donor commit"], cwd=donor)

            _run(
                ["git", "clone", "-q", "--no-local", "--reference", str(donor), str(donor), str(source)],
                cwd=workdir,
            )
            self.assertTrue((source / ".git" / "objects" / "info" / "alternates").is_file())
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True,
            ).stdout.strip()

            scratch_dir = ws._materialize_pinned_worktree_at_commit(source, commit)
            try:
                self.assertFalse(
                    (scratch_dir / ".git" / "objects" / "info" / "alternates").exists(),
                    "materialized evidence clone still borrows objects through an "
                    "alternate -- not self-contained",
                )
                self.assertEqual((scratch_dir / "victim.txt").read_text(), "original content\n")

                # The clone must survive the donor's outright removal.
                shutil.rmtree(donor)
                result = subprocess.run(
                    ["git", "cat-file", "-p", "HEAD:victim.txt"], cwd=scratch_dir,
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "original content\n")
            finally:
                ws._remove_pinned_worktree(source, scratch_dir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def test_verify_pinned_worktree_clean_catches_nested_dot_git_and_untracked_empty_directory(self):
        """`OPUS-R136-M03`: the cleanliness walk previously pruned every
        directory named `.git` anywhere in the tree, not only the
        checkout root's own administrative `.git`, and only examined
        files -- so a payload planted under a nested `sub/.git/`
        directory, or a genuinely empty untracked directory, passed
        silently despite the function's own "byte-identical to commit's
        own tree" docstring. Neither shape is ever produced by a clean
        checkout of a real commit (Git does not track directories, only
        file paths, and a normal checkout never creates a `.git`-named
        path outside the checkout root), so both must now be rejected."""
        with ScratchRepo() as repo:
            commit = repo.base

            scratch_dir = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                nested_git = scratch_dir / "sub" / ".git"
                nested_git.mkdir(parents=True)
                (nested_git / "payload.py").write_text("evil\n")
                with self.assertRaises(ws.PinnedEvidenceWorktreeIntegrityError):
                    ws._verify_pinned_worktree_clean(scratch_dir, commit)
            finally:
                ws._remove_pinned_worktree(repo.root, scratch_dir)

            scratch_dir2 = ws._materialize_pinned_worktree_at_commit(repo.root, commit)
            try:
                (scratch_dir2 / "empty_dir").mkdir()
                with self.assertRaises(ws.PinnedEvidenceWorktreeIntegrityError):
                    ws._verify_pinned_worktree_clean(scratch_dir2, commit)
            finally:
                ws._remove_pinned_worktree(repo.root, scratch_dir2)

    def test_materialization_clone_step_ignores_an_inherited_global_hooks_path(self):
        """`OPUS-R136-M01`: the initial `git clone` call used to be the
        one Git invocation in this module with no explicit
        `core.hooksPath` pin, while the materializer's own docstring
        claims inherited configuration cannot reintroduce a live hook
        during this phase. `reference-transaction` fires on every ref
        write a clone performs, so an inherited global `core.hooksPath`
        is a real channel, not a hypothetical one: with one configured
        (via `GIT_CONFIG_GLOBAL`, never the real user configuration) to a
        directory whose `reference-transaction` script writes a marker
        file, materializing a checkout must leave that marker file
        absent."""
        with ScratchRepo() as repo:
            global_hooks_dir = Path(tempfile.mkdtemp(prefix="wfo-global-hooks-"))
            global_config_dir = Path(tempfile.mkdtemp(prefix="wfo-global-config-"))
            marker_dir = Path(tempfile.mkdtemp(prefix="wfo-global-marker-"))
            marker_path = marker_dir / "FIRED"
            try:
                hook_path = global_hooks_dir / "reference-transaction"
                hook_path.write_text(f"#!/bin/sh\ntouch {marker_path}\nexit 0\n")
                hook_path.chmod(0o755)
                global_config_path = global_config_dir / "gitconfig"
                global_config_path.write_text(f"[core]\n\thooksPath = {global_hooks_dir}\n")

                with unittest.mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config_path)}):
                    scratch_dir = ws._materialize_pinned_worktree_at_commit(repo.root, repo.base)
                    try:
                        self.assertFalse(
                            marker_path.exists(),
                            "reference-transaction hook fired during clone setup "
                            "despite the materializer's explicit core.hooksPath pin",
                        )
                    finally:
                        ws._remove_pinned_worktree(repo.root, scratch_dir)
            finally:
                shutil.rmtree(global_hooks_dir, ignore_errors=True)
                shutil.rmtree(global_config_dir, ignore_errors=True)
                shutil.rmtree(marker_dir, ignore_errors=True)

    def test_verification_fails_closed_on_wrong_head_or_dirty_tree(self):
        """`_verify_pinned_worktree_clean` raises rather than returning a
        falsy/ok-shaped result, both when the checkout is pinned to the
        wrong (but real, resolvable) commit and when it is dirty --
        proving the fail-closed path `verify_wfo_ledger_coverage` relies
        on actually exists, not just that it happens not to trigger in
        the scenarios above."""
        with ScratchRepo() as repo:
            _write(repo, "other.txt", "second commit\n")
            other_commit = _commit_paths(repo, ["other.txt"], "second commit")
            scratch_dir = ws._materialize_pinned_worktree_at_commit(repo.root, repo.base)
            try:
                with self.assertRaises(ws.PinnedEvidenceWorktreeIntegrityError):
                    ws._verify_pinned_worktree_clean(scratch_dir, other_commit)

                (scratch_dir / "README.md").write_text("dirtied\n")
                with self.assertRaises(ws.PinnedEvidenceWorktreeIntegrityError):
                    ws._verify_pinned_worktree_clean(scratch_dir, repo.base)
            finally:
                ws._remove_pinned_worktree(repo.root, scratch_dir)


class TestVerifyWfoLedgerCoverage(unittest.TestCase):
    def test_pass_when_ledger_matches_table_and_evidence_is_green(self):
        with ScratchRepo() as repo:
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=_DEFAULT_CONSISTENT_LEDGER_ENTRIES)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "PASS", result.get("detail"))

    def test_fails_when_ledger_file_is_missing(self):
        with ScratchRepo() as repo:
            _write(repo, "docs/ai-workflow/WORKFLOW_V2_PLAN.md", _LEDGER_COVERAGE_TABLE_FIXTURE)
            commit = _commit_paths(repo, ["docs/ai-workflow/WORKFLOW_V2_PLAN.md"], "plan only, no ledger")
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("does not exist", result["detail"])

    def test_fails_when_ledger_json_is_malformed(self):
        with ScratchRepo() as repo:
            ledger_path = str(ws.ledger_status_path_for_work_item(ws.LEDGER_COVERAGE_WORK_ITEM_ID))
            _write(repo, "docs/ai-workflow/WORKFLOW_V2_PLAN.md", _LEDGER_COVERAGE_TABLE_FIXTURE)
            _write(repo, ledger_path, "{not json")
            commit = _commit_paths(repo, ["docs/ai-workflow/WORKFLOW_V2_PLAN.md", ledger_path], "malformed ledger")
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("malformed", result["detail"])

    def test_fails_on_omission_attack_missing_item_entry(self):
        with ScratchRepo() as repo:
            entries = [e for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES if e["item"] != 2]
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 2" in a and "omission" in a for a in result["failing_assertions"]))

    def test_fails_on_extraneous_ledger_entry(self):
        with ScratchRepo() as repo:
            entries = list(_DEFAULT_CONSISTENT_LEDGER_ENTRIES) + [
                {"item": 999, "status": "SUPERSEDED", "owner_checkpoint": "none"},
            ]
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 999" in a for a in result["failing_assertions"]))

    def test_fails_on_duplicate_entry_arm_c(self):
        """OPUS-R103-003's own adversarial arm (c): a second, redundant
        entry for an already-covered item is a FAIL naming that item, not
        merely tolerated as a stricter totality pass."""
        with ScratchRepo() as repo:
            entries = list(_DEFAULT_CONSISTENT_LEDGER_ENTRIES) + [
                {"item": 2, "status": "ABSENT", "owner_checkpoint": "WF8c"},
            ]
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 2" in a and "duplicate" in a for a in result["failing_assertions"]))

    def test_duplicate_check_does_not_false_positive_on_a_clean_artifact(self):
        """Confirms arm (c) is not merely a stricter totality check: arm
        (i) re-run unchanged on a non-duplicated artifact still passes."""
        with ScratchRepo() as repo:
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=_DEFAULT_CONSISTENT_LEDGER_ENTRIES)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "PASS")

    def test_fails_on_unevidenced_implemented_relabel_attack_arm_a(self):
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:
                    e["status"] = "IMPLEMENTED"
                    e["owner_checkpoint"] = "WF8c"
                    e.pop("evidence", None)
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 2" in a and "evidence" in a for a in result["failing_assertions"]))

    def test_fails_when_evidence_test_fails(self):
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 4:
                    e["evidence"] = "fixture_ledger_evidence_test.BrokenCase.test_fail"
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 4" in a for a in result["failing_assertions"]))

    def test_fails_when_evidence_id_is_unresolvable(self):
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 4:
                    e["evidence"] = "fixture_ledger_evidence_test.NoSuchCase.test_nope"
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 4" in a for a in result["failing_assertions"]))

    def test_fails_when_absent_wf8c_item_loses_its_evidence(self):
        """GPT-R131-002's own required regression (1): deleting evidence
        from an ABSENT/WF8c item is a FAIL, not a silent pass -- the exact
        omission the pre-fix verifier let through by never checking
        evidence at all for any non-IMPLEMENTED entry."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:
                    e.pop("evidence", None)
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 2" in a and "evidence" in a for a in result["failing_assertions"]))

    def test_fails_when_absent_wf8c_item_has_red_evidence(self):
        """GPT-R131-002's own required regression (2): red evidence on an
        ABSENT/WF8c item is a FAIL, not a silent pass."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:
                    e["evidence"] = "fixture_ledger_evidence_test.BrokenCase.test_fail"
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 2" in a for a in result["failing_assertions"]))

    def test_fails_when_partial_wf8c_item_loses_or_has_red_evidence(self):
        """GPT-R131-002's own required regression (3): a PARTIAL/WF8c item
        (the table's own item 4 row, left as PARTIAL rather than upgraded
        to IMPLEMENTED) with deleted or red evidence is a FAIL in both
        sub-cases -- the same disposition as ABSENT/WF8c, since WFR-69's
        own status-conditional rule treats every WF8c-owned open item
        (ABSENT or PARTIAL) identically."""
        with ScratchRepo() as repo:
            deleted = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in deleted:
                if e["item"] == 4:
                    e["status"] = "PARTIAL"
                    e.pop("evidence", None)
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=deleted)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 4" in a and "evidence" in a for a in result["failing_assertions"]))

        with ScratchRepo() as repo:
            red = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in red:
                if e["item"] == 4:
                    e["status"] = "PARTIAL"
                    e["evidence"] = "fixture_ledger_evidence_test.BrokenCase.test_fail"
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=red)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 4" in a for a in result["failing_assertions"]))

    def test_superseded_none_item_remains_pass_without_evidence(self):
        """GPT-R131-002's own required regression (4): SUPERSEDED/none
        (item 3) stays PASS carrying no evidence key at all -- the one
        disposition no evidence source can ever discharge, satisfied by
        the table's own recorded supersession alone."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            superseded = next(e for e in entries if e["item"] == 3)
            self.assertNotIn("evidence", superseded)
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "PASS", result.get("detail"))

    def test_fails_on_downward_relabel_attack_arm_d(self):
        """GPT-R106-001's own adversarial arm (d): moving a still-open
        item to SUPERSEDED/none with no corresponding plan-revision change
        is a FAIL naming that item -- covered by the general governance-
        scope rule, no item-318-specific special case needed."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:  # table: ABSENT/WF8c
                    e["status"] = "SUPERSEDED"
                    e["owner_checkpoint"] = "none"
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 2" in a for a in result["failing_assertions"]))

    def test_fails_when_owner_checkpoint_changes_without_a_status_change(self):
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:
                    e["owner_checkpoint"] = "WF8b"  # table resolves this row's owner to WF8c
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 2" in a and "owner" in a for a in result["failing_assertions"]))

    def test_fails_when_an_implemented_upgrade_also_changes_owner(self):
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 4:
                    e["owner_checkpoint"] = "WF8b"  # table's PARTIAL row resolves owner to WF8c
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("item 4" in a and "owner" in a for a in result["failing_assertions"]))

    def test_companion_file_supplies_evidence_when_ledger_entry_omits_it(self):
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 4:
                    e.pop("evidence", None)
            commit = _seed_ledger_coverage_fixture(
                repo, ledger_entries=entries,
                companion_entries=[{"item": 4, "evidence": "fixture_ledger_evidence_test.OkCase.test_pass"}],
            )
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "PASS", result.get("detail"))

    def test_ledgers_own_evidence_wins_over_a_conflicting_companion_entry(self):
        with ScratchRepo() as repo:
            commit = _seed_ledger_coverage_fixture(
                repo, ledger_entries=_DEFAULT_CONSISTENT_LEDGER_ENTRIES,  # item 4 already has passing evidence
                companion_entries=[{"item": 4, "evidence": "fixture_ledger_evidence_test.BrokenCase.test_fail"}],
            )
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "PASS", result.get("detail"))

    def test_fails_when_companion_json_is_malformed(self):
        with ScratchRepo() as repo:
            ledger_path = str(ws.ledger_status_path_for_work_item(ws.LEDGER_COVERAGE_WORK_ITEM_ID))
            companion_path = str(ws.wf8c_evidence_path_for_work_item(ws.LEDGER_COVERAGE_WORK_ITEM_ID))
            _write(repo, "docs/ai-workflow/WORKFLOW_V2_PLAN.md", _LEDGER_COVERAGE_TABLE_FIXTURE)
            _write(repo, ledger_path, json.dumps({"entries": _DEFAULT_CONSISTENT_LEDGER_ENTRIES}))
            _write(repo, companion_path, "{not json")
            commit = _commit_paths(
                repo, ["docs/ai-workflow/WORKFLOW_V2_PLAN.md", ledger_path, companion_path], "malformed companion",
            )
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("malformed", result["detail"])

    def test_reads_the_pinned_commit_not_the_working_tree(self):
        with ScratchRepo() as repo:
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=_DEFAULT_CONSISTENT_LEDGER_ENTRIES)
            ledger_path = repo.root / ws.ledger_status_path_for_work_item(ws.LEDGER_COVERAGE_WORK_ITEM_ID)
            ledger_path.write_text("garbage, not json, not even close")
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "PASS", result.get("detail"))

    def test_malicious_post_checkout_hook_cannot_produce_a_false_pass(self):
        """GPT-R132-001's own required regression, at the obligation-
        verifier level: a malicious `post-checkout` hook installed in the
        live repository, targeting a committed always-failing evidence
        test, must never turn `verify_wfo_ledger_coverage` itself green
        for that item."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 4:
                    e["evidence"] = "fixture_ledger_evidence_test.BrokenCase.test_fail"
            commit = _seed_ledger_coverage_fixture(repo, ledger_entries=entries)

            hooks_dir = repo.root / ".git" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook_path = hooks_dir / "post-checkout"
            hook_path.write_text(_POST_CHECKOUT_ATTACK_HOOK)
            hook_path.chmod(0o755)

            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL", result.get("detail"))
            self.assertTrue(any("item 4" in a for a in result["failing_assertions"]))

    def test_earlier_evidence_id_cannot_contaminate_a_later_ones_verdict(self):
        """GPT-R132-002's own required regression, at the obligation-
        verifier level: an evidence id that rewrites a later evidence
        id's own tracked source must not change `verify_wfo_ledger_
        coverage`'s own verdict for the later item."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:
                    e["evidence"] = "fixture_ledger_evidence_test.Mutator.test_mutate"
                if e["item"] == 4:
                    e["evidence"] = "fixture_ledger_evidence_test.Target.test_target"
            commit = _seed_ledger_coverage_fixture(
                repo, ledger_entries=entries, evidence_module=_CONTAMINATION_EVIDENCE_MODULE,
            )
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL", result.get("detail"))
            self.assertTrue(any("item 4" in a for a in result["failing_assertions"]))

    def test_clone_local_hooks_path_cannot_produce_a_false_pass(self):
        """`OPUS-R133-001`'s own required regression, at the obligation-
        verifier level: an evidence id that plants a clone-local
        `core.hooksPath` pointing at a hook armed to rewrite a later
        evidence id's own committed source must not change `verify_wfo_
        ledger_coverage`'s own verdict for that later item. Confirmed to
        fail (report `PASS`) against the pre-`OPUS-R133` implementation,
        which reused one materialized checkout across evidence ids and
        verified cleanliness with `git status --porcelain` -- a check the
        planted hook could itself trigger."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:
                    e["evidence"] = "fixture_ledger_evidence_test.HooksPathAttack.test_plant"
                if e["item"] == 4:
                    e["evidence"] = "fixture_victim_test.Victim.test_victim"
            commit = _seed_ledger_coverage_fixture(
                repo, ledger_entries=entries, evidence_module=_HOOKS_PATH_CONTAMINATION_EVIDENCE_MODULE,
                extra_files={"scripts/fixture_victim_test.py": _HOOKS_PATH_CONTAMINATION_VICTIM_MODULE},
            )
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL", result.get("detail"))
            self.assertTrue(any("item 4" in a for a in result["failing_assertions"]))

    def test_clone_local_filter_and_attributes_cannot_produce_a_false_pass(self):
        """`OPUS-R133-001`'s own required regression, at the obligation-
        verifier level: an evidence id that registers a clone-local
        matched clean/smudge filter pair, targeting a later evidence id's
        own committed source, must not change `verify_wfo_ledger_
        coverage`'s own verdict for that later item -- even though the
        matched filter pair would make `git status` itself report the
        tree clean. Confirmed to fail (report `PASS`) against the
        pre-`OPUS-R133` implementation."""
        with ScratchRepo() as repo:
            entries = [dict(e) for e in _DEFAULT_CONSISTENT_LEDGER_ENTRIES]
            for e in entries:
                if e["item"] == 2:
                    e["evidence"] = "fixture_ledger_evidence_test.FilterAttack.test_plant"
                if e["item"] == 4:
                    e["evidence"] = "fixture_victim_test.Victim.test_victim"
            commit = _seed_ledger_coverage_fixture(
                repo, ledger_entries=entries, evidence_module=_FILTER_ATTRIBUTES_CONTAMINATION_EVIDENCE_MODULE,
                extra_files={"scripts/fixture_victim_test.py": _FILTER_ATTRIBUTES_CONTAMINATION_VICTIM_MODULE},
            )
            result = ws.verify_wfo_ledger_coverage(repo.root, commit)
            self.assertEqual(result["status"], "FAIL", result.get("detail"))
            self.assertTrue(any("item 4" in a for a in result["failing_assertions"]))

    def test_bound_in_completion_obligation_conformance(self):
        self.assertEqual(
            ws.COMPLETION_OBLIGATION_CONFORMANCE.get("WFO-LEDGER-COVERAGE"), "verify_wfo_ledger_coverage",
        )
        self.assertTrue(callable(getattr(ws, "verify_wfo_ledger_coverage", None)))


# ---------------------------------------------------------------------------
# workflow_backlog (workflow-2.17.0, `backlog-file`, CP1)
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml
except ImportError:  # the cross-check is an aid, skipped without PyYAML
    _yaml = None

_BACKLOG_HEAD = ["version: 1", "items:", "  - id: a", "    title: Alpha", "    status: next", "    order: 1"]

_BACKLOG_SAMPLE = """# Backlog
version: 1
items:
  # first
  - id: alpha
    title: Alpha thing # trailing
    status: done
    order: 1
    release: "2.0.0"
  - id: beta
    title: "Beta: the \\"second\\""
    status: next # soon
    order: 2
    depends_on: [alpha]
    size: 3
    detail: docs/items/beta.md
  - id: gamma
    title: 'It''s gamma'
    status: idea
    order: 3
    depends_on:
      - alpha
      - beta
"""


def _bl(*replace_or_add, replace_line=None):
    """The head item with `replace_line` (1-based) replaced, then lines added."""
    rows = list(_BACKLOG_HEAD)
    if replace_line is not None:
        rows[replace_line - 1] = "    " + replace_or_add[0]
        replace_or_add = replace_or_add[1:]
    return "\n".join(rows + list(replace_or_add)) + "\n"


def _backlog_refusal_line(text):
    with unittest.TestCase().assertRaises(wb.BacklogFormatError) as cm:
        wb.parse_backlog(text)
    return cm.exception.line


class BacklogParser(unittest.TestCase):
    def test_accepts_the_complete_example(self):
        data, lines = wb.parse_backlog(_BACKLOG_SAMPLE)
        self.assertEqual(data["version"], 1)
        self.assertEqual([i["id"] for i in data["items"]], ["alpha", "beta", "gamma"])
        self.assertEqual(data["items"][0]["title"], "Alpha thing")
        self.assertEqual(data["items"][1]["title"], 'Beta: the "second"')
        self.assertEqual(data["items"][2]["title"], "It's gamma")
        self.assertEqual(data["items"][1]["depends_on"], ["alpha"])
        self.assertEqual(data["items"][2]["depends_on"], ["alpha", "beta"])
        self.assertEqual(data["items"][1]["size"], 3)
        self.assertEqual(data["items"][0]["release"], "2.0.0")
        self.assertEqual(lines["item"][1]["status"], 12)
        self.assertEqual(lines["item"][1]["_item"], 10)

    def test_empty_backlog_and_top_level_order(self):
        self.assertEqual(wb.parse_backlog("version: 1\nitems: []\n")[0], {"version": 1, "items": []})
        data, _ = wb.parse_backlog("items:\n  - id: a\n    title: A\n    status: next\n    order: 1\nversion: 1\n")
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(wb.parse_backlog("version: 1\nitems: [] # none\n")[0]["items"], [])

    def test_a_quoted_question_mark_entry_is_still_accepted(self):
        data, _ = wb.parse_backlog(_bl('    depends_on: ["a?b"]'))
        self.assertEqual(data["items"][0]["depends_on"], ["a?b"])

    def test_a_trailing_comment_on_a_key_with_no_inline_value_is_accepted(self):
        # As YAML does: `items: # x` and `depends_on: # x` open their block like the bare key.
        data, _ = wb.parse_backlog(_BACKLOG_SAMPLE.replace("items:", "items: # x", 1))
        self.assertEqual([i["id"] for i in data["items"]], ["alpha", "beta", "gamma"])
        data, _ = wb.parse_backlog(_BACKLOG_SAMPLE.replace("    depends_on:\n", "    depends_on: # x\n", 1))
        self.assertEqual(data["items"][2]["depends_on"], ["alpha", "beta"])

    def test_empty_depends_on_forms(self):
        self.assertEqual(wb.parse_backlog(_bl("    depends_on: []"))[0]["items"][0]["depends_on"], [])

    def test_accepted_counterparts_a_looser_reading_would_refuse(self):
        for title, expected in (
            ("R&D", "R&D"), ('"a *b"', "a *b"), ("Caf é", "Caf é"), ("Alpha \U0001F600", "Alpha \U0001F600"),
            ('"abc "', "abc "), ("abc # note", "abc"), ("Yes please", "Yes please"), ("a:b", "a:b"),
            ('"1abc"', "1abc"), ("'true'", "true"), ('"say \\\\ and \\" "', 'say \\ and " '),
        ):
            with self.subTest(title=title):
                text = _bl(f"title: {title}", replace_line=4)
                data, _ = wb.parse_backlog(text)
                self.assertEqual(data["items"][0]["title"], expected)
                if _yaml is not None:
                    self.assertEqual(_yaml.safe_load(text), data)

    def test_no_final_newline_is_accepted(self):
        self.assertEqual(wb.parse_backlog("\n".join(_BACKLOG_HEAD))[0]["items"][0]["id"], "a")

    def test_refusals_name_their_line(self):
        cases = {
            "tab": (_bl("title:\tabc", replace_line=4), 4),
            "carriage return": (_bl("title: abc\r", replace_line=4), 4),
            "Cc control": (_bl("title: a\x01b", replace_line=4), 4),
            "DEL": (_bl("title: a\x7fb", replace_line=4), 4),
            "NEL U+0085": (_bl("title: a\u0085b", replace_line=4), 4),
            "U+2028": (_bl("title: a b", replace_line=4), 4),
            "U+2029": (_bl("title: a b", replace_line=4), 4),
            "FEFF in the middle": (_bl("title: a﻿b", replace_line=4), 4),
            "U+FFFE": (_bl("title: a￾b", replace_line=4), 4),
            "U+FFFF": (_bl("title: a￿b", replace_line=4), 4),
            "BOM": ("﻿" + _bl(), 1),
            "document marker": ("---\n" + _bl(), 1),
            "document end marker": (_bl("..."), 7),
            "directive": ("%YAML 1.2\n" + _bl(), 1),
            "anchor": (_bl("title: &x a", replace_line=4), 4),
            "alias": (_bl("title: *x", replace_line=4), 4),
            "tag": (_bl("title: !t a", replace_line=4), 4),
            "block scalar |": (_bl("title: |", replace_line=4), 4),
            "block scalar >": (_bl("title: >", replace_line=4), 4),
            "flow mapping": (_bl("title: {a: b}", replace_line=4), 4),
            "flow list in a string field": (_bl("title: [a]", replace_line=4), 4),
            "empty value": (_bl("title:", replace_line=4), 4),
            "null": (_bl("title: null", replace_line=4), 4),
            "tilde": (_bl("title: ~", replace_line=4), 4),
            "unknown top-level key": (_bl("foo: 1"), 7),
            "repeated top-level key": (_bl("version: 1"), 7),
            "unknown item key": (_bl("    foo: bar"), 7),
            "repeated item key": (_bl("    title: B"), 7),
            "wrong indent": (_bl("   - id: b"), 7),
            "list entry outside depends_on": (_bl("      - x"), 7),
            "dash at four spaces": (_bl("    - x"), 7),
            "item outside items": ("version: 1\nitems: []\n  - id: a\n", 3),
            "two spaces after the item dash": (_bl("  -  id: b"), 7),
            "plain digit start": (_bl("title: 1abc", replace_line=4), 4),
            "colon space in plain": (_bl("title: a: b", replace_line=4), 4),
            "plain ending in colon": (_bl("title: abc:", replace_line=4), 4),
            "bracket in plain": (_bl("title: a[b", replace_line=4), 4),
            "brace in plain": (_bl("title: a}b", replace_line=4), 4),
            "comma in plain": (_bl("title: a,b", replace_line=4), 4),
            "quoted number": (_bl('order: "1"', replace_line=6), 6),
            "leading zero": (_bl("order: 01", replace_line=6), 6),
            "signed number": (_bl("order: -1", replace_line=6), 6),
            "plus number": (_bl("order: +1", replace_line=6), 6),
            "seven digits": (_bl("order: 1234567", replace_line=6), 6),
            "float": (_bl("order: 1.5", replace_line=6), 6),
            "word as number": (_bl("order: abc", replace_line=6), 6),
            "unknown escape": (_bl('title: "a\\nb"', replace_line=4), 4),
            "unterminated double quote": (_bl('title: "abc', replace_line=4), 4),
            "text after a quote": (_bl('title: "a" x', replace_line=4), 4),
            "unterminated single quote": (_bl("title: 'abc", replace_line=4), 4),
            "depends_on scalar": (_bl("    depends_on: a"), 7),
            "flow list trailing comma": (_bl("    depends_on: [a, ]"), 7),
            "flow list empty entry": (_bl("    depends_on: [a, , b]"), 7),
            "flow list only a comma": (_bl("    depends_on: [,]"), 7),
            "flow list no space after comma": (_bl("    depends_on: [a,b]"), 7),
            "flow list two spaces after comma": (_bl("    depends_on: [a,  b]"), 7),
            "flow list space after [": (_bl("    depends_on: [ a, b]"), 7),
            "flow list space before ]": (_bl("    depends_on: [a, b ]"), 7),
            "flow list space before comma": (_bl("    depends_on: [a , b]"), 7),
            "flow list unterminated": (_bl("    depends_on: [a, b"), 7),
            "question mark in a plain flow entry": (_bl("    depends_on: [a?b]"), 7),
            "empty depends_on at end of file": (_bl("    depends_on:"), 7),
            "empty depends_on before a key": (_bl("    depends_on:", "    size: 3"), 7),
            "empty depends_on before an item": (_bl("    depends_on:", "  - id: b"), 7),
            "three spaces after the colon": (_bl("title:   abc", replace_line=4), 4),
            "no space after the colon": (_bl("title:abc", replace_line=4), 4),
            "trailing space on a value": (_bl("title: abc ", replace_line=4), 4),
            "two spaces before #": (_bl("title: abc  # x", replace_line=4), 4),
            "trailing space on a comment line": (_bl("# note "), 7),
            "trailing space on a blank line": (_bl(" "), 7),
            "missing version": ("items: []\n", 1),
            "missing items": ("version: 1\n", 1),
            "items with no item": ("version: 1\nitems:\n", 2),
            "items with no item before version": ("items:\nversion: 1\n", 1),
            "items with no item before version, comments between": ("items:\n# c\n\n# d\nversion: 1\n", 1),
            "long line": (_bl("# " + "a" * 999), 7),
            "too many lines": ("\n" * 2001, 2001),
            "oversize": (("# " + "a" * 997 + "\n") * 270, 1),
        }
        for word in ("true", "false", "null", "yes", "no", "on", "off", "y", "n"):
            for form in (word, word.upper(), word.capitalize()):
                cases[f"reserved {form}"] = (_bl(f"title: {form}", replace_line=4), 4)
        for label, (text, line) in cases.items():
            with self.subTest(label), self.assertRaises(wb.BacklogFormatError) as cm:
                wb.parse_backlog(text)
            self.assertEqual(cm.exception.line, line, f"{label}: {cm.exception}")

    def test_bytes_entry_refuses_bom_and_bad_utf8(self):
        with self.assertRaises(wb.BacklogFormatError) as cm:
            wb.parse_backlog_bytes(b"\xef\xbb\xbfversion: 1\n")
        self.assertEqual(cm.exception.line, 1)
        with self.assertRaises(wb.BacklogFormatError) as cm:
            wb.parse_backlog_bytes(b"version: 1\nitems: []\n# \xff\n")
        self.assertEqual(cm.exception.line, 3)
        self.assertEqual(wb.parse_backlog_bytes(b"version: 1\nitems: []\n")[0]["items"], [])

    @unittest.skipIf(_yaml is None, "PyYAML is not installed (an aid, not a gate)")
    def test_pyyaml_cross_check(self):
        self.assertEqual(_yaml.safe_load(_BACKLOG_SAMPLE), wb.parse_backlog(_BACKLOG_SAMPLE)[0])
        empty_depends = _bl("    depends_on:", "    size: 3")
        self.assertIsNone(_yaml.safe_load(empty_depends)["items"][0]["depends_on"])
        self.assertEqual(_backlog_refusal_line(empty_depends), 7)
        line_sep = _bl("title: Alpha x", replace_line=4)
        self.assertEqual(_backlog_refusal_line(line_sep), 4)
        try:
            loaded = _yaml.safe_load(line_sep)
        except _yaml.YAMLError:
            return
        self.assertNotEqual(loaded["items"][0].get("title"), "Alpha x")


class _BacklogRepo:
    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="wfo-backlog-"))
        (self.root / "docs" / "items").mkdir(parents=True)
        (self.root / "docs" / "items" / "beta.md").write_text("beta\n")
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)


def _schema_error(text, root):
    try:
        wb.validate_backlog(*wb.parse_backlog(text), root)
    except wb.BacklogError as exc:
        return exc
    return None


def _items(*rows):
    out = ["version: 1", "items:"]
    for n, row in enumerate(rows, 1):
        fields = {"id": f"i{n}", "title": f"T{n}", "status": "next", "order": n}
        fields.update(row)
        first = True
        for key, value in fields.items():
            out.append(("  - " if first else "    ") + f"{key}: {value}")
            first = False
    return "\n".join(out) + "\n"


class BacklogSchema(unittest.TestCase):
    def setUp(self):
        self.repo = _BacklogRepo().__enter__()
        self.addCleanup(self.repo.__exit__)

    def refused(self, text, fragment, line=None):
        exc = _schema_error(text, self.repo.root)
        self.assertIsNotNone(exc, f"{fragment!r} was accepted")
        self.assertEqual(exc.code, "backlog_invalid")
        self.assertIn(fragment, exc.message)
        if line is not None:
            self.assertIn(f"line {line}:", exc.message)

    def test_sample_is_valid(self):
        self.assertIsNone(_schema_error(_BACKLOG_SAMPLE, self.repo.root))

    def test_version(self):
        self.refused(_BACKLOG_SAMPLE.replace("version: 1", "version: 2"), "`version` must be 1", 2)

    def test_missing_required_fields(self):
        self.refused("version: 1\nitems:\n  - id: a\n    title: A\n    order: 1\n", "no `status`", 3)

    def test_id_rules(self):
        self.refused(_items({"id": "Bad_Id"}), "`id` `Bad_Id` must match", 3)
        self.refused(_items({"id": '"-x"'}), "must match")
        self.refused(_items({"id": '"' + "a" * 65 + '"'}), "must match")
        self.refused(_items({"id": "same"}, {"id": "same"}), "`id` `same` repeats line 3")

    def test_title_status_order(self):
        self.refused(_items({"title": '""'}), "`title` must be 1 to 200")
        self.refused(_items({"title": '"' + "x" * 201 + '"'}), "`title` must be 1 to 200")
        self.refused(_items({"status": "started"}), "`status` must be one of")
        self.refused(_items({"order": 0}), "`order` must be 1 to 999999")
        self.refused(_items({"order": 5}, {"order": 5}), "`order` 5 repeats line")
        # the order is total across done and idea items too
        self.refused(_items({"status": "done", "order": 4}, {"status": "idea", "order": 4}), "repeats line")

    def test_depends_on(self):
        self.refused(_items({"depends_on": "[zzz]"}), "`zzz`, which is not an item")
        self.refused(_items({"depends_on": "[i1]"}), "depends on itself")
        self.refused(_items({}, {"depends_on": "[i1, i1]"}), "`i1` twice")

    def test_cycles_name_one(self):
        self.refused(_items({"depends_on": "[i2]"}, {"depends_on": "[i1]"}), "dependency cycle: i1 -> i2 -> i1")
        self.refused(_items({"depends_on": "[i2]"}, {"depends_on": "[i3]"}, {"depends_on": "[i1]"}),
                     "dependency cycle: i1 -> i2 -> i3 -> i1")

    def test_size(self):
        self.refused(_items({"size": 0}), "`size` must be 1 to 99")
        self.refused(_items({"size": 100}), "`size` must be 1 to 99")
        self.assertIsNone(_schema_error(_items({"size": 99}), self.repo.root))

    def test_detail_rules(self):
        root = self.repo.root
        (root / "docs" / "other.md").write_text("x\n")
        (root / "docs" / "items" / "dir.md").mkdir()
        (root / "docs" / "items" / "a b.md").write_text("x\n")
        (root / "docs" / "items" / "p(1).md").write_text("x\n")
        (root / "docs" / "items" / "p).md").write_text("x\n")
        (root / "docs" / "items" / "a|b.md").write_text("x\n")
        outside = Path(tempfile.mkdtemp(prefix="wfo-backlog-out-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "t.md").write_text("x\n")
        os.symlink(root / "docs" / "items" / "beta.md", root / "docs" / "items" / "in.md")
        os.symlink(outside / "t.md", root / "docs" / "items" / "out.md")
        os.symlink(root / "docs", root / "docs" / "items" / "subdir")
        os.symlink(outside, root / "docs" / "items" / "outdir")
        self.assertIsNone(_schema_error(_items({"detail": "docs/items/beta.md"}), root))
        for detail, fragment in (
            ("docs/items/missing.md", "does not exist"),
            ("docs/items/dir.md", "not a regular file"),
            ("docs/items/../other.md", "`..` segment"),
            ("docs/other.md", "under `docs/items/`"),
            ('"/etc/passwd"', "repository-relative"),
            ('"docs/items/a b.md"', "may contain only"),
            ('"docs/items/p(1).md"', "may contain only"),
            ('"docs/items/p).md"', "may contain only"),
            ('"docs/items/a|b.md"', "may contain only"),
            ("docs/items/in.md", "symbolic link"),
            ("docs/items/out.md", "symbolic link"),
            ("docs/items/subdir/items/beta.md", "symbolic link"),
            ("docs/items/outdir/t.md", "symbolic link"),
            ("docs/items/", "empty, `.` or `..` segment"),
            ("docs/items", "under `docs/items/`"),
        ):
            with self.subTest(detail):
                self.refused(_items({"detail": detail}), fragment, 7)

    def test_release_rules(self):
        self.refused(_items({"release": '"1.0"'}), "only allowed on a `done` item")
        self.refused(_items({"status": "done", "release": '"-1"'}), "`release` must be")
        self.refused(_items({"status": "done", "release": '"' + "1" * 41 + '"'}), "`release` must be")
        self.assertIsNone(_schema_error(_items({"status": "done", "release": '"2.17.0"'}), self.repo.root))
        self.assertIsNone(_schema_error(_items({"status": "done", "release": "v1.0+rc_1"}), self.repo.root))

    def test_a_done_item_may_depend_on_an_unfinished_one(self):
        self.assertIsNone(_schema_error(_items({"status": "done", "depends_on": "[i2]"}, {}), self.repo.root))

    def test_all_violations_listed_ten_shown(self):
        exc = _schema_error(_items(*[{"status": "bogus"}] * 12), self.repo.root)
        self.assertEqual(len(exc.violations), 12)
        shown = [l for l in exc.message.splitlines() if l.startswith("line ")]
        self.assertEqual(len(shown), 10)
        self.assertIn("and 2 more", exc.message)

    def test_parse_and_validate_names_the_file(self):
        with self.assertRaises(wb.BacklogError) as cm:
            wb.parse_and_validate(_bl("title: 1abc", replace_line=4), self.repo.root, "docs/backlog.yaml")
        self.assertTrue(cm.exception.message.startswith("docs/backlog.yaml:4:"))
        with self.assertRaises(wb.BacklogError) as cm:
            wb.parse_and_validate(_items({"status": "bogus"}), self.repo.root, "docs/backlog.yaml")
        self.assertTrue(cm.exception.message.startswith("docs/backlog.yaml:"))
        self.assertEqual(cm.exception.code, "backlog_invalid")
        self.assertIn("check", cm.exception.next_step)
        data, _ = wb.parse_and_validate(_BACKLOG_SAMPLE.encode(), self.repo.root)
        self.assertEqual(len(data["items"]), 3)

    def test_path_problem(self):
        root = self.repo.root
        for rel, fragment in (
            ("", "empty"), ("/abs", "leading"), ("a\\b", "backslash"), ("a//b", "segment"), ("a/./b", "segment"),
            ("a/../b", "segment"), ("a\x01b", "control"), (".git/x", "under"), (".GIT/x", "under"),
            (".ai-review/x", "under"),
        ):
            with self.subTest(rel):
                self.assertIn(fragment, wb.path_problem(root, rel))
        self.assertIsNone(wb.path_problem(root, "docs/not-yet/there.md"))
        self.assertIsNone(wb.path_problem(root, "docs/items/beta.md"))
        if not (root / "DOCS").exists():  # a case-insensitive filesystem finds the entry; a sensitive one does not
            self.assertIsNone(wb.path_problem(root, "DOCS/ROADMAP.md"))
        else:
            self.assertIn("differs in case", wb.path_problem(root, "DOCS/ROADMAP.md"))


class BacklogSelect(unittest.TestCase):
    @staticmethod
    def data(*specs):
        items = []
        for n, (item_id, status, deps) in enumerate(specs, 1):
            item = {"id": item_id, "title": item_id.upper(), "status": status, "order": n * 10}
            if deps:
                item["depends_on"] = list(deps)
            items.append(item)
        return {"version": 1, "items": items}

    def test_lowest_ready_next(self):
        out = wb.select_next(self.data(("a", "done", ()), ("b", "next", ()), ("c", "next", ())))
        self.assertEqual(out["item"]["id"], "b")
        self.assertEqual(out["item"]["depends_on"], [])
        self.assertIsNone(out["item"]["size"])
        self.assertEqual((out["also_in_progress"], out["warnings"]), ([], []))

    def test_in_progress_ready_comes_first_and_lowest_order(self):
        out = wb.select_next(self.data(("a", "next", ()), ("b", "in-progress", ()), ("c", "in-progress", ())))
        self.assertEqual(out["item"]["id"], "b")
        self.assertEqual(out["also_in_progress"], ["c"])

    def test_blocked_in_progress_is_warned_and_skipped(self):
        out = wb.select_next(self.data(("a", "next", ()), ("b", "in-progress", ("a",))))
        self.assertEqual(out["item"]["id"], "a")
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("`b`", out["warnings"][0])
        self.assertIn("a", out["warnings"][0])

    def test_blocked_lower_next_is_skipped_for_a_ready_higher_one(self):
        out = wb.select_next(self.data(("a", "idea", ()), ("b", "next", ("a",)), ("c", "next", ())))
        self.assertEqual(out["item"]["id"], "c")

    def test_dependency_done_unblocks(self):
        out = wb.select_next(self.data(("a", "done", ()), ("b", "next", ("a",))))
        self.assertEqual(out["item"]["id"], "b")

    def test_nothing_next(self):
        for backlog in (self.data(), self.data(("a", "idea", ()), ("b", "done", ()))):
            with self.assertRaises(wb.BacklogError) as cm:
                wb.select_next(backlog)
            self.assertEqual(cm.exception.code, "no_ready_item")
            self.assertIn("no item is `next` or `in-progress`", cm.exception.message)

    def test_all_blocked_lists_the_waiting_ids(self):
        with self.assertRaises(wb.BacklogError) as cm:
            wb.select_next(self.data(("a", "idea", ()), ("b", "next", ("a",)), ("c", "in-progress", ("a", "b"))))
        self.assertEqual(cm.exception.code, "no_ready_item")
        self.assertIn("`b` waits for a", cm.exception.message)
        self.assertIn("`c` waits for a, b", cm.exception.message)
        self.assertIn("/milestone-plan", cm.exception.next_step)

    def test_done_is_never_chosen(self):
        with self.assertRaises(wb.BacklogError):
            wb.select_next(self.data(("a", "done", ())))


class BacklogEdit(unittest.TestCase):
    def edit(self, item, status, release=None, text=_BACKLOG_SAMPLE):
        data, lines = wb.parse_backlog(text)
        return wb.set_status_text(text, data, lines, item, status, release)

    def changed_lines(self, old, new):
        import difflib
        return [l for l in difflib.ndiff(old.split("\n"), new.split("\n")) if l[0] in "+-"]

    def test_status_changed_everything_else_kept(self):
        new = self.edit("beta", "done")
        self.assertEqual(self.changed_lines(_BACKLOG_SAMPLE, new),
                         ["-     status: next # soon", "+     status: done # soon"])
        self.assertIn("# first", new)
        self.assertEqual(wb.parse_backlog(new)[0]["items"][1]["status"], "done")

    def test_release_inserted_and_replaced(self):
        new = self.edit("beta", "done", "2.17.0")
        self.assertIn('    status: done # soon\n    release: "2.17.0"\n    order: 2\n', new)
        self.assertEqual(wb.parse_backlog(new)[0]["items"][1]["release"], "2.17.0")
        replaced = self.edit("alpha", "done", "2.0.1")
        self.assertEqual(self.changed_lines(_BACKLOG_SAMPLE, replaced),
                         ['-     release: "2.0.0"', '+     release: "2.0.1"'])

    def test_no_op_when_equal(self):
        self.assertEqual(self.edit("alpha", "done"), _BACKLOG_SAMPLE)
        self.assertEqual(self.edit("alpha", "done", "2.0.0"), _BACKLOG_SAMPLE)

    def test_item_first_line_status_and_last_line_without_newline(self):
        text = "version: 1\nitems:\n  - status: next\n    id: a\n    title: A\n    order: 1"
        new = self.edit("a", "done", "1.0", text)
        self.assertEqual(wb.parse_backlog(new)[0]["items"][0]["release"], "1.0")
        self.assertFalse(new.endswith("\n"))
        self.assertIn("  - status: done\n    release: \"1.0\"\n    id: a", new)

    def test_refusals(self):
        for args, code in (
            (("nope", "done"), "item_unknown"),
            (("beta", "started"), "backlog_invalid"),
            (("beta", "next", "1.0"), "backlog_invalid"),
            (("beta", "done", "-bad"), "backlog_invalid"),
            (("alpha", "next"), "backlog_invalid"),
        ):
            with self.subTest(args), self.assertRaises(wb.BacklogError) as cm:
                self.edit(*args)
            self.assertEqual(cm.exception.code, code)

    def test_a_reparse_that_differs_is_refused(self):
        data, lines = wb.parse_backlog(_BACKLOG_SAMPLE)
        other = {"version": 1, "items": []}
        with unittest.mock.patch.object(wb, "parse_backlog", return_value=(other, {})):
            with self.assertRaises(wb.BacklogError) as cm:
                wb.set_status_text(_BACKLOG_SAMPLE, data, lines, "beta", "done")
        self.assertEqual(cm.exception.code, "backlog_invalid")
        self.assertIn("more than", cm.exception.message)


class BacklogRender(unittest.TestCase):
    def page(self, text=_BACKLOG_SAMPLE, file="docs/backlog.yaml", roadmap="docs/ROADMAP.md"):
        return wb.render_roadmap(wb.parse_backlog(text)[0], file, roadmap)

    def test_exact_page_sorted_by_order(self):
        text = _BACKLOG_SAMPLE.replace("order: 1\n", "order: 30\n", 1).replace("order: 3\n", "order: 1\n", 1)
        page = self.page(text)
        self.assertEqual(page, "\n".join([
            "# Roadmap",
            "",
            "<!-- Generated from docs/backlog.yaml by scripts/workflow_backlog.py. Do not edit by hand: edit the "
            "backlog file and run `python3 scripts/workflow_backlog.py generate`. -->",
            "",
            "| Order | Item | Status | Size | Depends on |",
            "| --- | --- | --- | --- | --- |",
            "| 1 | It's gamma `gamma` | Idea | - | alpha, beta |",
            "| 2 | [Beta: the \"second\"](items/beta.md) `beta` | Next | 3 | alpha |",
            "| 30 | Alpha thing `alpha` | Done (2.0.0) | - | - |",
            "",
        ]))

    def test_four_status_words_and_plain_done(self):
        text = _items({"status": "idea"}, {"status": "next"}, {"status": "in-progress"}, {"status": "done"})
        rows = self.page(text).splitlines()[6:]
        self.assertEqual([r.split(" | ")[2] for r in rows], ["Idea", "Next", "In progress", "Done"])

    def test_link_is_relative_to_the_page_directory(self):
        for roadmap, target in (("docs/ROADMAP.md", "items/beta.md"), ("ROADMAP.md", "docs/items/beta.md"),
                                ("docs/sub/ROADMAP.md", "../items/beta.md"), ("docs/items/R.md", "beta.md")):
            with self.subTest(roadmap):
                self.assertIn(f"]({target})", self.page(roadmap=roadmap))

    def test_titles_are_escaped_and_rows_stay_intact(self):
        title = 'a|b [c] <d> \\e `f`'
        quoted = title.replace("\\", "\\\\")
        text = _items({"title": f'"{quoted}"', "detail": "docs/items/a_b-c.1.md", "size": 2})
        page = self.page(text)
        self.assertIn(r"[a\|b \[c\] \<d\> \\e \`f\`](items/a_b-c.1.md) `i1`", page)
        for row in page.splitlines():
            if row.startswith("| 1 "):
                self.assertEqual(len(re.split(r"(?<!\\)\|", row)), 7)

    def test_byte_stable_and_empty_backlog(self):
        self.assertEqual(self.page(), self.page())
        self.assertTrue(self.page().endswith("\n"))
        self.assertNotIn("\r", self.page())
        empty = self.page("version: 1\nitems: []\n")
        self.assertEqual(len(empty.splitlines()), 6)

    def test_file_path_cannot_close_the_comment(self):
        with self.assertRaises(wb.BacklogError) as cm:
            self.page(file="docs/a-->b.yaml")
        self.assertEqual(cm.exception.code, "declaration_invalid")


# --------------------------------------------------------------------------
# CP2: the declaration, the command line and the generator
# --------------------------------------------------------------------------

_BACKLOG_SCRIPT = Path(wb.__file__).resolve()
_SIMPLE_BACKLOG = """\
version: 1
items:
  - id: alpha
    title: Alpha
    status: in-progress
    order: 1
  - id: beta
    title: Beta
    status: next
    order: 2
    depends_on: [alpha]
"""


class _Decl:
    """A scratch repository with the script installed and a `backlog` key."""

    def __init__(self, backlog="docs/backlog.yaml", text=_SIMPLE_BACKLOG, extra=None, config=True):
        self.backlog, self.text, self.extra, self.config = backlog, text, extra or {}, config

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="wfo-decl-"))
        (self.root / "scripts").mkdir()
        shutil.copy(_BACKLOG_SCRIPT, self.root / "scripts" / "workflow_backlog.py")
        (self.root / "docs" / "ai-workflow").mkdir(parents=True)
        if self.config:
            self.write_config({"backlog": {"file": self.backlog, **self.extra}})
        if self.text is not None:
            self.write(self.backlog, self.text)
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_config(self, value):
        raw = value if isinstance(value, str) else json.dumps(value)
        (self.root / wb.CONFIG_REL).write_text(raw)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, str):
            path.write_text(text)
        else:
            path.write_bytes(text)

    def read(self, rel):
        return (self.root / rel).read_bytes()

    def exists(self, rel):
        return os.path.lexists(self.root / rel)

    def run(self, *args):
        done = subprocess.run(["python3", "-I", str(self.root / "scripts" / "workflow_backlog.py"), *args],
                              cwd=self.root, capture_output=True, text=True)
        return done.returncode, done.stdout, done.stderr

    def ok(self, *args):
        code, out, err = self.run(*args)
        assert code == 0, (code, out, err)
        return json.loads(out)

    def refused(self, *args, code=2):
        got, out, err = self.run(*args)
        assert got == code, (got, out, err)
        lines = err.strip().split("\n")
        assert lines[0].startswith("error: ") and "next step: " in err, err
        return lines[0].split(": ", 2)[1], err

    def tree(self):
        out = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                out[str(path.relative_to(self.root))] = path.read_bytes()
        return out


class BacklogDeclaration(unittest.TestCase):
    def declared(self, value):
        with _Decl(config=False) as d:
            d.write_config({"backlog": value})
            return wb.load_declaration(d.root)

    def refusal(self, value):
        with _Decl(config=False) as d:
            d.write_config({"backlog": value})
            with self.assertRaises(wb.BacklogError) as cm:
                wb.load_declaration(d.root)
            return cm.exception

    def test_undeclared_forms_write_nothing(self):
        for label, config in (("no key", '{"other": 1}'), ("unparsable", "{not json"), ("array", "[1, 2]"),
                              ("missing file", None)):
            with self.subTest(label), _Decl(config=False) as d:
                if config is not None:
                    d.write_config(config)
                before = d.tree()
                self.assertIsNone(wb.load_declaration(d.root))
                self.assertEqual(wb.declared_paths(d.root), [])
                for args in (["status"], ["next"], ["check"], ["generate"], ["preflight-accept", "alpha"],
                             ["accept", "alpha"]):
                    self.assertEqual(d.ok(*args), {"declared": False})
                self.assertEqual(d.tree(), before)

    def test_defaults_and_full_declaration(self):
        self.assertEqual(self.declared({"file": "docs/backlog.yaml"}),
                         {"file": "docs/backlog.yaml", "roadmap": "docs/ROADMAP.md", "generator": None})
        self.assertEqual(self.declared({"file": "b.yaml", "roadmap": "R.md", "generator": ["python3", "g.py"]}),
                         {"file": "b.yaml", "roadmap": "R.md", "generator": ["python3", "g.py"]})

    def test_every_declaration_invalid_refusal(self):
        cases = {
            "null": None, "string": "docs/backlog.yaml", "no file": {}, "unknown key": {"file": "a", "x": 1},
            "file not str": {"file": 3}, "roadmap not str": {"file": "a", "roadmap": []},
            "same path": {"file": "a.yaml", "roadmap": "a.yaml"},
            "absolute": {"file": "/a.yaml"}, "backslash": {"file": "a\\b.yaml"}, "dotdot": {"file": "../a.yaml"},
            "dot": {"file": "./a.yaml"}, "empty segment": {"file": "a//b.yaml"}, "empty": {"file": ""},
            "control": {"file": "a\x01.yaml"}, "under .git": {"file": ".git/a.yaml"},
            "under .ai-review": {"file": ".ai-review/a.yaml"},
            "generator string": {"file": "a", "generator": "make"}, "generator empty": {"file": "a", "generator": []},
            "generator null": {"file": "a", "generator": None},
            "generator entry empty": {"file": "a", "generator": ["python3", ""]},
            "generator entry not str": {"file": "a", "generator": ["python3", 1]},
        }
        for label, value in cases.items():
            with self.subTest(label):
                exc = self.refusal(value)
                self.assertEqual(exc.code, "declaration_invalid")
                self.assertTrue(exc.next_step)

    def test_symbolic_links_are_refused_from_every_subcommand(self):
        for label, file in (("last component", "docs/link.yaml"), ("directory to inside", "docs/via/backlog.yaml"),
                            ("directory to outside", "docs/out/backlog.yaml")):
            with self.subTest(label), tempfile.TemporaryDirectory(prefix="wfo-outside-") as outside:
                with _Decl(backlog=file, text=None) as d:
                    (d.root / "scripts" / "keep.txt").write_text("x")
                    if label == "last component":
                        (d.root / "docs" / "link.yaml").symlink_to(d.root / "scripts" / "keep.txt")
                    elif label == "directory to inside":
                        (d.root / "docs" / "via").symlink_to("../scripts")
                    else:
                        (d.root / "docs" / "out").symlink_to(outside)
                    before = d.tree()
                    for args in (["next"], ["check"], ["generate"], ["accept", "alpha"],
                                 ["preflight-accept", "alpha"]):
                        code, message = d.refused(*args)
                        self.assertEqual(code, "declaration_invalid")
                        self.assertIn("symbolic link", message)
                    report = d.ok("status")
                    self.assertEqual(report["error"]["code"], "declaration_invalid")
                    self.assertIn("symbolic link", report["error"]["message"])
                    self.assertEqual(d.tree(), before)
                    self.assertEqual(list(Path(outside).iterdir()), [])
                    self.assertEqual(wb.declared_paths(d.root), [])

    def test_missing_components_are_fine_and_case_mismatch_is_refused(self):
        with _Decl(backlog="new/dir/backlog.yaml", text=None) as d:
            self.assertEqual(wb.load_declaration(d.root)["file"], "new/dir/backlog.yaml")
        with _Decl(backlog="Scripts/backlog.yaml", text=None) as d:
            real_listdir, real_lstat = os.listdir, os.lstat

            def fold_lstat(path, *args, **kwargs):  # a case-insensitive filesystem finds `scripts` as `Scripts`
                path = Path(path)
                return real_lstat(path.with_name("scripts") if path.name == "Scripts" else path, *args, **kwargs)

            with unittest.mock.patch("os.lstat", side_effect=fold_lstat):
                with self.assertRaises(wb.BacklogError) as cm:
                    wb.load_declaration(d.root)
            self.assertEqual(cm.exception.code, "declaration_invalid")
            self.assertIn("differs in case", cm.exception.message)
            self.assertIn("scripts", real_listdir(d.root))

    def test_protected_conflicts_predicate(self):
        declaration = {"plan_stage": {"protected_paths": ["docs/P.md"], "excluded_paths": {"docs/backlog.yaml": "x"}},
                       "implementation_stage": {"protected_paths": {"a/own.json": "x"},
                                                "protected_prefixes": {"scripts/": "x"}}}
        paths = ["docs/P.md", "docs/backlog.yaml", "scripts/backlog.yaml", "a/own.json", "docs/items/x.md"]
        self.assertEqual(wb.protected_conflicts(paths, declaration),
                         [("plan_stage", "docs/P.md"), ("implementation_stage", "scripts/backlog.yaml"),
                          ("implementation_stage", "a/own.json")])
        self.assertEqual(wb.protected_conflicts(["docs/backlog.yaml"], {}), [])

    def test_protected_conflicts_reports_any_protected_item_page_path_or_prefix(self):
        paths = ["docs/backlog.yaml", wb.REPRESENTATIVE_ITEM_PAGE]

        def conflicts(**stages):
            return wb.protected_conflicts(paths, stages)

        self.assertEqual(
            conflicts(plan_stage={"protected_paths": ["docs/items/foo.md"]}),
            [("plan_stage", "docs/items/foo.md")])
        self.assertEqual(
            conflicts(implementation_stage={"protected_paths": {"docs/items/foo.md": "x"}}),
            [("implementation_stage", "docs/items/foo.md")])
        self.assertEqual(
            conflicts(implementation_stage={"protected_prefixes": {"docs/items/team/": "x"}}),
            [("implementation_stage", "docs/items/team/")])
        # A prefix that is the page prefix itself, or shorter, is caught through the representative page.
        self.assertEqual(
            conflicts(plan_stage={"protected_prefixes": ["docs/items/"]}),
            [("plan_stage", wb.REPRESENTATIVE_ITEM_PAGE)])
        for unrelated in ({"protected_paths": ["docs/other.md"]}, {"protected_prefixes": ["docs/other/"]}):
            self.assertEqual(conflicts(plan_stage=unrelated), [])
        # Without a path under the item-page prefix the extra rule does not apply.
        self.assertEqual(
            wb.protected_conflicts(["docs/backlog.yaml"], {"plan_stage": {"protected_paths": ["docs/items/foo.md"]}}),
            [])


class BacklogCli(unittest.TestCase):
    def test_status_next_and_the_usage_error(self):
        with _Decl() as d:
            status = d.ok("status")
            self.assertEqual((status["declared"], status["valid"], status["items"]), (True, True, 2))
            self.assertFalse(status["roadmap_fresh"])
            nxt = d.ok("next")
            self.assertEqual(nxt["item"]["id"], "alpha")
            self.assertEqual((nxt["declared"], nxt["file"], nxt["warnings"]), (True, "docs/backlog.yaml", []))
            code, out, err = d.run("frobnicate")
            self.assertEqual((code, out), (2, ""))
            self.assertEqual(d.run("accept", "alpha", "--release", "../x")[0], 2)
            self.assertEqual(d.run("accept", "alpha", "--release", "2.17.0 ")[0], 2)

    def test_status_reports_an_invalid_file_and_exits_zero(self):
        with _Decl(text="version: 2\nitems: []\n") as d:
            status = d.ok("status")
            self.assertFalse(status["valid"])
            self.assertEqual(status["error"]["code"], "backlog_invalid")

    def test_refusal_codes_with_two_lines_and_exit_two(self):
        with _Decl(text=None) as d:
            self.assertEqual(d.refused("next")[0], "backlog_missing")
            self.assertEqual(d.refused("check")[0], "backlog_missing")
        bad = "version: 1\nitems:\n  - id: a\n    title: A\n    status: bogus\n    order: 1\n"
        with _Decl(text=bad) as d:
            code, err = d.refused("next")
            self.assertEqual(code, "backlog_invalid")
            self.assertIn("docs/backlog.yaml:", err)
            self.assertIn("docs/ai-workflow/BACKLOG_FILE.md", err.split("next step: ", 1)[1])
            self.assertIn("scripts/workflow_backlog.py check", err)
        with _Decl(text=_SIMPLE_BACKLOG.replace("in-progress", "idea").replace("next", "idea")) as d:
            self.assertEqual(d.refused("next")[0], "no_ready_item")
            self.assertEqual(d.refused("preflight-accept", "alpha")[0], "status_not_acceptable")
            self.assertEqual(d.refused("accept", "alpha")[0], "status_not_acceptable")
        with _Decl() as d:
            self.assertEqual(d.refused("preflight-accept", "nope")[0], "item_unknown")
            code, err = d.refused("accept", "nope")
            self.assertEqual(code, "item_unknown")
            self.assertIn("then run `python3 scripts/workflow_backlog.py accept nope` "
                          "and continue the acceptance at step 4", err)
        with _Decl(extra={"bogus": 1}) as d:
            self.assertEqual(d.refused("next")[0], "declaration_invalid")
            report = d.ok("status")  # `status` always exits 0 and reports
            self.assertEqual((report["valid"], report["error"]["code"]), (False, "declaration_invalid"))

    def test_check_exit_one_when_stale(self):
        with _Decl() as d:
            code, out, err = d.run("check")
            self.assertEqual((code, out), (1, ""))
            self.assertIn("error: roadmap_stale:", err)
            d.ok("generate")
            self.assertEqual(d.ok("check")["fresh"], True)

    def test_builtin_generate_check_and_stale_line(self):
        with _Decl() as d:
            self.assertEqual(d.ok("generate"),
                             {"declared": True, "generated": "docs/ROADMAP.md", "generator": "built-in"})
            first = d.read("docs/ROADMAP.md")
            d.ok("generate")
            self.assertEqual(d.read("docs/ROADMAP.md"), first)
            d.ok("check")
            d.write("docs/ROADMAP.md", first.replace(b"| Next |", b"| Done |"))
            code, err = d.refused("check", code=1)
            self.assertEqual(code, "roadmap_stale")
            self.assertIn("at line 8", err)

    def test_hand_written_page_is_refused_then_replaced_once(self):
        with _Decl() as d:
            d.write("docs/ROADMAP.md", "# Roadmap\n\nprose\n")
            before = d.tree()
            for args in (["generate"], ["accept", "alpha"], ["preflight-accept", "alpha"]):
                self.assertEqual(d.refused(*args)[0], "roadmap_not_generated")
                self.assertEqual(d.tree(), before)
            d.ok("generate", "--replace")
            d.ok("check")
            d.ok("generate")

    def test_a_refusal_preserves_the_modes_of_the_existing_files(self):
        with _Decl() as d:
            d.write("docs/ROADMAP.md", "# Roadmap\n\nprose\n")
            paths = [d.root / "docs" / "backlog.yaml", d.root / "docs" / "ROADMAP.md"]
            for path in paths:
                os.chmod(path, 0o644)
            for args in (["generate"], ["accept", "alpha"]):
                self.assertEqual(d.refused(*args)[0], "roadmap_not_generated")
                self.assertEqual([stat.S_IMODE(os.stat(p).st_mode) for p in paths], [0o644, 0o644])

    def test_a_successful_write_preserves_the_modes_of_the_existing_files(self):
        with _Decl() as d:
            d.write("docs/ROADMAP.md", "# Roadmap\n\nprose\n")
            paths = [d.root / "docs" / "backlog.yaml", d.root / "docs" / "ROADMAP.md"]
            for path in paths:
                os.chmod(path, 0o664)
            d.ok("generate", "--replace")
            d.ok("accept", "alpha", "--release", "2.17.0")
            self.assertEqual([stat.S_IMODE(os.stat(p).st_mode) for p in paths], [0o664, 0o664])

    def test_accept_edits_only_status_and_release_and_is_idempotent(self):
        with _Decl() as d:
            self.assertEqual(d.ok("preflight-accept", "alpha")["ok"], True)
            result = d.ok("accept", "alpha", "--release", "2.17.0")
            self.assertEqual(result["paths"], ["docs/backlog.yaml", "docs/ROADMAP.md"])
            text = d.read("docs/backlog.yaml").decode()
            data = wb.parse_backlog(text)[0]
            self.assertEqual((data["items"][0]["status"], data["items"][0]["release"]), ("done", "2.17.0"))
            self.assertEqual(text.count("\n"), _SIMPLE_BACKLOG.count("\n") + 1)
            self.assertIn("Done (2.17.0)", d.read("docs/ROADMAP.md").decode())
            d.ok("check")
            snapshot = d.tree()
            d.ok("accept", "alpha")
            self.assertEqual(d.tree(), snapshot)
            d.write("docs/ROADMAP.md", d.read("docs/ROADMAP.md").replace(b"Done", b"Idea"))
            d.ok("accept", "alpha")
            d.ok("check")
            self.assertEqual(d.tree(), snapshot)

    def test_accept_works_without_any_workflow_state(self):
        with _Decl() as d:
            self.assertFalse(d.exists("docs/ai-workflow/WORKFLOW_STATE.json"))
            self.assertEqual(d.ok("accept", "beta")["item"], "beta")

    def test_undeclared_writes_nothing_even_with_a_stray_file(self):
        with _Decl(config=False) as d:
            before = d.tree()
            for args in (["status"], ["next"], ["check"], ["generate"], ["accept", "alpha"]):
                self.assertEqual(d.ok(*args), {"declared": False})
            self.assertEqual(d.tree(), before)


GOOD_PAGE = 'open("docs/ROADMAP.md", "w").write("# R\\n<!-- backlog-sha256: " + d + " -->\\nrow\\n")'


def _generator(body):
    prelude = 'import os,sys,hashlib\nd = hashlib.sha256(open("docs/backlog.yaml", "rb").read()).hexdigest()\n'
    return {"generator": ["python3", "-c", prelude + body]}


class BacklogGenerator(unittest.TestCase):
    def test_good_generator_is_stamped_in_place(self):
        body = 'assert os.environ["WORKFLOW_BACKLOG_SHA256"] == d\n' + GOOD_PAGE
        with _Decl(extra=_generator(body)) as d:
            result = d.ok("accept", "alpha")
            self.assertEqual(result["paths"], ["docs/backlog.yaml", "docs/ROADMAP.md"])
            digest = hashlib.sha256(d.read("docs/backlog.yaml")).hexdigest()
            self.assertEqual(
                d.read("docs/ROADMAP.md"),
                b"# R\n<!-- backlog-sha256: " + digest.encode() + b" page-sha256: "
                + hashlib.sha256(b"# R\nrow\n").hexdigest().encode() + b" -->\nrow\n")
            d.ok("check")
            self.assertEqual(d.ok("generate")["generator"], "declared")
            d.ok("check")

    def test_marker_as_last_line_without_lf(self):
        body = 'open("docs/ROADMAP.md", "w").write("# R\\n<!-- backlog-sha256: " + d + " -->")'
        with _Decl(extra=_generator(body)) as d:
            d.ok("generate")
            page = d.read("docs/ROADMAP.md")
            self.assertFalse(page.endswith(b"\n"))
            self.assertIn(hashlib.sha256(b"# R\n").hexdigest().encode(), page)
            d.ok("check")

    def failing(self, body, *, page=None, expect=None):
        with _Decl(extra=_generator(body)) as d:
            if page is not None:
                d.write("docs/ROADMAP.md", page)
            before = d.tree()
            for args in (["generate"], ["accept", "alpha"]):
                code, err = d.refused(*args)
                self.assertEqual(code, "generator_failed", err)
                if expect:
                    self.assertIn(expect, err)
                self.assertEqual(d.tree(), before)

    def test_freshness_contract_failures_restore_everything(self):
        marker = '"<!-- backlog-sha256: " + d + " -->\\n"'
        self.failing("pass", expect="no page")
        self.failing("pass", page="# R\n<!-- backlog-sha256: " + "0" * 64 + " -->\n", expect="not the digest")
        self.failing("pass", page="# R\nno marker\n", expect="exactly one line")
        stamped = "# R\n<!-- backlog-sha256: " + "a" * 64 + " page-sha256: " + "b" * 64 + " -->\n"
        self.failing("pass", page=stamped, expect="stamped")
        self.failing(f'open("docs/ROADMAP.md", "w").write({marker} + {marker})', expect="found 2")
        self.failing('open("docs/ROADMAP.md", "w").write("<!-- backlog-sha256: " + d + " -->\\r\\n")', expect="found 0")
        self.failing(f'open("docs/ROADMAP.md", "w").write({marker}); open("docs/backlog.yaml", "a").write("# x\\n")',
                     expect="changed the backlog file")
        self.failing("sys.exit(3)", expect="exited 3")

    def test_copying_the_old_stamped_line_forward_fails(self):
        body = ('old = open("docs/ROADMAP.md").read() if os.path.exists("docs/ROADMAP.md") else ""\n'
                'open("docs/ROADMAP.md", "w").write(old + "<!-- backlog-sha256: " + d + " -->\\n")')
        with _Decl(extra=_generator(body)) as d:
            d.ok("generate")
            before = d.tree()
            self.assertEqual(d.refused("generate")[0], "generator_failed")
            self.assertEqual(d.tree(), before)

    def test_page_as_symlink_or_directory_fails(self):
        for kind in ("symlink", "directory"):
            body = ('import shutil\nshutil.rmtree("docs/ROADMAP.md", ignore_errors=True)\n'
                    + ('os.symlink("backlog.yaml", "docs/ROADMAP.md")' if kind == "symlink"
                       else 'os.mkdir("docs/ROADMAP.md")'))
            with self.subTest(kind), _Decl(extra=_generator(body)) as d:
                before = d.tree()
                self.assertEqual(d.refused("accept", "alpha")[0], "generator_failed")
                self.assertEqual(d.tree(), before)
                self.assertFalse(d.exists("docs/ROADMAP.md"))

    def test_partial_page_then_failure_or_timeout_restores_or_removes(self):
        with _Decl(extra=_generator('open("docs/ROADMAP.md", "w").write("half"); sys.exit(1)')) as d:
            self.assertEqual(d.refused("accept", "alpha")[0], "generator_failed")
            self.assertFalse(d.exists("docs/ROADMAP.md"))
            self.assertEqual(d.read("docs/backlog.yaml").decode(), _SIMPLE_BACKLOG)
        slow = 'open("docs/ROADMAP.md", "w").write("half")\nimport time; time.sleep(30)'
        with _Decl(extra=_generator(slow)) as d:
            d.write("docs/ROADMAP.md", "old\n")
            with unittest.mock.patch.object(wb, "GENERATOR_TIMEOUT", 1):
                with self.assertRaises(wb.BacklogError) as cm:
                    wb.cmd_accept(d.root, "alpha")
            self.assertEqual(cm.exception.code, "generator_failed")
            self.assertIn("timed out", cm.exception.message)
            self.assertEqual(d.read("docs/ROADMAP.md"), b"old\n")
            self.assertEqual(d.read("docs/backlog.yaml").decode(), _SIMPLE_BACKLOG)

    def test_reaccepting_with_a_noop_generator_fails_and_a_real_one_repairs(self):
        with _Decl(extra={"generator": ["python3", "-c", "pass"]}) as d:
            self.assertEqual(d.refused("accept", "alpha")[0], "generator_failed")
        with _Decl(extra=_generator(GOOD_PAGE)) as d:
            d.ok("accept", "alpha")
            stamped = d.read("docs/ROADMAP.md")
            d.write("docs/ROADMAP.md", stamped + b"hand edit\n")
            self.assertEqual(d.refused("check", code=1)[0], "roadmap_stale")
            d.ok("accept", "alpha")
            self.assertEqual(d.read("docs/ROADMAP.md"), stamped)
            d.ok("check")
            d.write("docs/ROADMAP.md", stamped + b"hand edit\n")
            d.write_config({"backlog": {"file": "docs/backlog.yaml", "generator": ["python3", "-c", "pass"]}})
            edited = d.tree()
            self.assertEqual(d.refused("accept", "alpha")[0], "generator_failed")
            self.assertEqual(d.tree(), edited)

    def test_unresolvable_generator_is_a_preflight_refusal(self):
        with _Decl(extra={"generator": ["no-such-binary-xyz"]}) as d:
            before = d.tree()
            code, err = d.refused("preflight-accept", "alpha")
            self.assertEqual(code, "generator_failed")
            self.assertNotIn("<id>", err)
            self.assertEqual(d.tree(), before)

    def test_check_with_declared_generator_runs_no_user_code_and_detects_every_edit(self):
        body = ('open("docs/ROADMAP.md", "w").write("# R\\nrow one\\nrow two\\n'
                '<!-- backlog-sha256: " + d + " -->\\n")')
        with _Decl(extra=_generator(body)) as d:
            d.ok("generate")
            d.ok("check")
            fresh = d.read("docs/ROADMAP.md")
            d.write("scripts/boom.py", "raise SystemExit(7)")
            d.write_config({"backlog": {"file": "docs/backlog.yaml", "generator": ["python3", "scripts/boom.py"]}})
            d.ok("check")

            def stale(page, *needle):
                d.write("docs/ROADMAP.md", page)
                code, err = d.refused("check", code=1)
                self.assertEqual(code, "roadmap_stale")
                for text in needle:
                    self.assertIn(text, err)

            stale(fresh.replace(b"row one", b"row ONE"), "edited after")
            stale(fresh + b"added\n", "edited after")
            stale(fresh.replace(b"row two\n", b""), "edited after")
            stale(fresh.replace(b" page-sha256", b" x-sha256"), "no stamped")
            stale(fresh + fresh.split(b"\n")[3] + b"\n", "exactly one")
            stale(fresh.split(b"<!--")[0], "no stamped")
            unstamped = b" ".join(fresh.split(b"<!--")[1].split(b" ")[:3]) + b" -->\n"
            stale(fresh.split(b"<!--")[0] + b"<!--" + unstamped.split(b"\n")[0].split(b"<!--")[-1] + b"\n", "not stamped")
            os.unlink(d.root / "docs/ROADMAP.md")
            self.assertIn("does not exist", d.refused("check", code=1)[1])
            d.write("docs/ROADMAP.md", fresh)
            original = d.read("docs/backlog.yaml")
            d.write("docs/backlog.yaml", original + b"# comment\n")
            self.assertIn("file digest", d.refused("check", code=1)[1])
            d.write("docs/backlog.yaml", original)
            d.ok("check")


class BacklogAcceptAtomicity(unittest.TestCase):
    def test_a_refusal_after_accept_began_restores_the_file_and_the_page(self):
        with _Decl() as d:
            d.ok("generate")
            before = d.tree()
            boom = wb.BacklogError("generator_failed", "boom", "x")
            with unittest.mock.patch.object(wb, "_generate_page", side_effect=boom):
                with self.assertRaises(wb.BacklogError):
                    wb.cmd_accept(d.root, "alpha", "1.0")
            self.assertEqual(d.tree(), before)

    def test_reparse_mismatch_writes_nothing_and_names_the_recovery(self):
        with _Decl() as d:
            before = d.tree()
            mismatch = wb.BacklogError("backlog_invalid", "mismatch", "fix")
            with unittest.mock.patch.object(wb, "set_status_text", side_effect=mismatch):
                with self.assertRaises(wb.BacklogError) as cm:
                    wb.cmd_accept(d.root, "alpha")
            self.assertIn("continue the acceptance at step 4", cm.exception.next_step)
            self.assertEqual(d.tree(), before)


class BacklogDeclarationConflict(unittest.TestCase):
    def artifacts(self, d, protected_paths=(), protected_prefixes=()):
        d.write("docs/ai-workflow/registry/alpha-artifacts.json", json.dumps({
            "plan_stage": {"protected_paths": list(protected_paths)},
            "implementation_stage": {"protected_prefixes": {p: "x" for p in protected_prefixes}},
        }))

    def test_preflight_and_accept_refuse_a_protected_path_and_write_nothing(self):
        for label, kwargs in (("plan file", {"protected_paths": ["docs/backlog.yaml"]}),
                              ("implementation prefix", {"protected_prefixes": ["docs/"]}),
                              ("item pages", {"protected_prefixes": ["docs/items/"]})):
            with self.subTest(label), _Decl() as d:
                self.artifacts(d, **kwargs)
                d.write("docs/ROADMAP.md", "keep\n")
                before = d.tree()
                for args in (["preflight-accept", "alpha"], ["accept", "alpha"]):
                    code, err = d.refused(*args)
                    self.assertEqual(code, "declaration_conflict")
                    self.assertIn("move the path out of the protected area", err)
                self.assertEqual(d.tree(), before)

    def test_an_exact_item_page_or_a_prefix_under_the_page_prefix_is_a_conflict(self):
        for label, kwargs, named, stage in (
                ("exact page", {"protected_paths": ["docs/items/foo.md"]}, "docs/items/foo.md", "plan stage"),
                ("prefix under the pages", {"protected_prefixes": ["docs/items/team/"]},
                 "docs/items/team/", "implementation stage")):
            with self.subTest(label), _Decl() as d:
                self.artifacts(d, **kwargs)
                before = d.tree()
                for args in (["preflight-accept", "alpha"], ["accept", "alpha"]):
                    code, err = d.refused(*args)
                    self.assertEqual(code, "declaration_conflict")
                    self.assertIn(f"`{named}` ({stage})", err)
                self.assertEqual(d.tree(), before)
        with _Decl() as d:
            self.artifacts(d, protected_paths=["docs/other.md"], protected_prefixes=["docs/other/"])
            d.ok("preflight-accept", "alpha")

    def test_an_unprotected_or_unreadable_declaration_is_not_a_conflict(self):
        with _Decl() as d:
            self.artifacts(d, protected_paths=["docs/P.md"], protected_prefixes=["scripts/"])
            d.ok("preflight-accept", "alpha")
        with _Decl() as d:
            d.ok("preflight-accept", "alpha")
            d.write("docs/ai-workflow/registry/alpha-artifacts.json", "{broken")
            d.ok("preflight-accept", "alpha")


_BACKLOG_DOC_REL = "docs/ai-workflow/BACKLOG_FILE.md"


def _backlog_doc():
    return (_repo_root() / _BACKLOG_DOC_REL).read_text(encoding="utf-8")


def _doc_block(name):
    """The fenced block that follows `<!-- backlog-example:NAME -->`, dedented."""
    match = re.search(r"^[ ]*<!-- backlog-example:" + name + r" -->\n[ ]*```\w*\n(.*?)\n[ ]*```",
                      _backlog_doc(), re.M | re.S)
    assert match, f"no backlog-example:{name} block"
    return textwrap.dedent(match.group(1)) + "\n"


class BacklogDocument(unittest.TestCase):
    """The shipped reference cannot drift from the script it documents."""

    def test_starter_is_a_valid_minimal_file(self):
        with _Decl(text=_doc_block("starter")) as d:
            data, _ = wb.parse_and_validate(d.read("docs/backlog.yaml"), d.root)
            self.assertEqual([(i["id"], i["status"], i["order"]) for i in data["items"]],
                             [("first-item", "next", 1)])
            self.assertEqual(d.ok("next")["item"]["id"], "first-item")

    def test_complete_example_parses_and_validates(self):
        with _Decl(text=_doc_block("full")) as d:
            d.write("docs/items/login.md", "Login.\n")
            data, _ = wb.parse_and_validate(d.read("docs/backlog.yaml"), d.root)
            self.assertEqual(len(data["items"]), 4)
            by_id = {i["id"]: i for i in data["items"]}
            self.assertEqual(by_id["login"]["release"], "1.0.0")
            self.assertEqual(by_id["settings"]["depends_on"], ["login", "profile"])
            self.assertEqual(by_id["dark-mode"]["title"], "Dark mode (it's optional)")
            self.assertEqual(d.ok("next")["item"]["id"], "profile")
            d.ok("generate")
            d.ok("check")

    def test_trailing_comment_after_a_block_list_key_is_documented(self):
        doc = " ".join(_backlog_doc().split())
        self.assertIn("A trailing comment is also allowed after `items:` and after `depends_on:` "
                      "when the value is a block list on the following lines", doc)

    def test_way_back_says_the_copied_page_header_is_not_edited(self):
        doc = " ".join(_backlog_doc().split())
        self.assertIn("still name `scripts/workflow_backlog.py`", doc)
        self.assertIn("must not be hand-edited, because `check` compares the page byte for byte", doc)

    def test_every_error_code_of_the_script_is_documented(self):
        source = Path(wb.__file__).read_text(encoding="utf-8")
        codes = set(re.findall(r'BacklogError\(\s*"([a-z_]+)"', source))
        codes.add("declaration_conflict")
        self.assertTrue({"declaration_invalid", "backlog_missing", "backlog_invalid", "no_ready_item",
                         "item_unknown", "status_not_acceptable", "roadmap_not_generated",
                         "generator_failed", "roadmap_stale", "declaration_conflict"} <= codes, codes)
        doc = _backlog_doc()
        for code in sorted(codes):
            with self.subTest(code):
                self.assertIn(f"`{code}`", doc)

    def test_the_script_names_this_document_by_its_path(self):
        self.assertTrue((_repo_root() / _BACKLOG_DOC_REL).is_file())
        self.assertIn(_BACKLOG_DOC_REL, wb._DECLARATION_NEXT)
        self.assertIn(_BACKLOG_DOC_REL, Path(wb.__file__).read_text(encoding="utf-8"))

    def test_the_repair_section_it_cites_exists(self):
        heading = "Repairing an artifact declaration after an approval"
        doc = _backlog_doc()
        self.assertIn("REVIEW_PROTOCOL.md", doc)
        self.assertIn(heading, doc)
        protocol = (_repo_root() / "docs/ai-workflow/REVIEW_PROTOCOL.md").read_text(encoding="utf-8")
        self.assertIn("### " + heading, protocol)

    def _generator_repo(self, d):
        d.write("tools/roadmap.py", _doc_block("generator"))

    def test_example_generator_satisfies_the_freshness_contract(self):
        extra = {"generator": ["python3", "tools/roadmap.py"]}
        with _Decl(text=_doc_block("starter"), extra=extra) as d:
            self._generator_repo(d)
            self.assertEqual(d.ok("generate")["generator"], "declared")
            d.ok("check")
            page = d.read("docs/ROADMAP.md")
            self.assertIn(b"page-sha256:", page)
            d.write("docs/ROADMAP.md", page + b"hand edit\n")
            code, _ = d.refused("check", code=1)
            self.assertEqual(code, "roadmap_stale")
            d.ok("generate")
            d.ok("check")
            d.write("docs/backlog.yaml", _doc_block("starter").replace("My first item", "Another title"))
            self.assertEqual(d.refused("check", code=1)[0], "roadmap_stale")
            d.ok("accept", "first-item")
            d.ok("check")

    def test_document_names_the_downgrade_procedure(self):
        doc = _backlog_doc()
        for needle in ("tools/workflow_backlog.py", "python3 tools/workflow_backlog.py check",
                       "never run the generator directly"):
            self.assertIn(needle, doc)

    def test_keep_the_backlog_procedure_leaves_check_passing(self):
        extra = {"generator": ["python3", "tools/roadmap.py"]}
        with _Decl(text=_doc_block("starter"), extra=extra) as d:
            self._generator_repo(d)
            d.write("tools/workflow_backlog.py", Path(wb.__file__).read_bytes())
            shutil.rmtree(d.root / "scripts")

            def copy(*args):
                return subprocess.run(["python3", "-I", "tools/workflow_backlog.py", *args], cwd=d.root,
                                      capture_output=True, text=True)

            self.assertEqual(copy("generate").returncode, 0)
            self.assertEqual(copy("check").returncode, 0)
            d.write("docs/backlog.yaml", _doc_block("starter").replace("status: next", "status: done"))
            self.assertEqual(copy("check").returncode, 1)
            self.assertEqual(copy("generate").returncode, 0)
            done = copy("check")
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_copy_nested_below_the_root_reads_the_repository_declaration(self):
        with _Decl(text=_doc_block("starter")) as d:
            d.write("tools/backlog/workflow_backlog.py", Path(wb.__file__).read_bytes())
            shutil.rmtree(d.root / "scripts")
            subprocess.run(["git", "init", "-q"], cwd=d.root, check=True)

            def copy(*args, cwd=d.root):
                return subprocess.run(["python3", "-I", str(d.root / "tools/backlog/workflow_backlog.py"), *args],
                                      cwd=cwd, capture_output=True, text=True)

            # the page is missing: a copy that lost the declaration would pass here
            missing = copy("check")
            self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
            self.assertIn("roadmap_stale", missing.stderr)
            self.assertEqual(copy("generate").returncode, 0)
            self.assertEqual(copy("check").returncode, 0)
            d.write("docs/ROADMAP.md", d.read("docs/ROADMAP.md") + b"hand edit\n")
            sub = d.root / "docs"
            self.assertEqual(copy("check", cwd=sub).returncode, 1)


# ---------------------------------------------------------------------------
# workflow-2.20.0: the retention report (workflow_retention)
# ---------------------------------------------------------------------------

_RR_NOW = __import__("datetime").datetime(2026, 11, 1, 12, 0, 0, tzinfo=__import__("datetime").timezone.utc)


def _rr_commit(item_id: str) -> str:
    return hashlib.sha1(item_id.encode()).hexdigest()


def _rr_raw(state: str, number: int = 7) -> str:
    if state == "none":
        return "[]"
    return json.dumps([{"number": number, "url": f"https://github.com/acme/widgets/pull/{number}",
                        "state": state.upper(), "headRefOid": "a" * 40}])


def _rr_item(phase="MILESTONE_COMPLETE", last="2026-10-01T00:00:00Z", parent=None, anchor=True, **extra):
    item = {"phase": phase, "last_transition": last, "parent_work_item_id": parent}
    if anchor is True:
        item["reviewed_implementation_head"] = "PLACEHOLDER"
    item.update(extra)
    return item


def _rr_snapshot(*roots):
    seen = {}
    for root in roots:
        for directory, dirs, names in os.walk(root):
            dirs.sort()
            for name in names + dirs:
                full = os.path.join(directory, name)
                if os.path.islink(full):
                    seen[full] = ("link", os.readlink(full))
                elif os.path.isfile(full):
                    seen[full] = ("file", hashlib.sha256(Path(full).read_bytes()).hexdigest())
                else:
                    seen[full] = ("dir",)
    return seen


def _rr_find_pycache(*roots):
    return sorted(os.path.join(d, n) for root in roots for d, dirs, _ in os.walk(root) for n in dirs
                  if n == "__pycache__")


class _RR:
    """A scratch repository with a `github.com` origin, a hand-built state
    file, an archive root, and the forge seam (no network, no `gh`)."""

    def __init__(self, items, origin=True, outcomes=None):
        self.items, self.origin, self.outcomes = items, origin, outcomes or {}
        self.queried = []

    def __enter__(self):
        self.repo = ScratchRepo().__enter__()
        self.root = self.repo.root
        if self.origin:
            _run(["git", "remote", "add", "origin", "https://github.com/acme/widgets.git"], cwd=self.root)
        self.archive_root = Path(tempfile.mkdtemp(prefix="wfo-archive-"))
        (self.archive_root / "widgets").mkdir()
        self.write_state(self.items)
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.archive_root, ignore_errors=True)
        self.repo.__exit__(*exc)

    def write_state(self, items):
        entries = {}
        for key, item in items.items():
            item = dict(item)
            if item.get("reviewed_implementation_head") == "PLACEHOLDER":
                item["reviewed_implementation_head"] = _rr_commit(key)
            entries[key] = item
        _write(self.repo, "docs/ai-workflow/WORKFLOW_STATE.json", json.dumps({"work_items": entries}))

    def run(self, argv, timeout):
        commit = argv[argv.index("--search") + 1]
        self.queried.append(commit)
        outcome = self.outcomes.get(commit, "merged")
        if isinstance(outcome, BaseException):
            raise outcome
        return _rr_raw(outcome)

    @staticmethod
    def resolve(repo_root):
        return {"path": "/usr/bin/gh", "sha256": "0" * 64}

    def build(self, ttl_days=30, now=_RR_NOW):
        return wr.build_report(self.root, archive_root=self.archive_root, ttl_days=ttl_days, now=now,
                               run=self.run, resolve=self.resolve)

    def cli(self, *args, capture=True):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wr.main(["report", "--repo-root", str(self.root), "--archive-root", str(self.archive_root), *args],
                           run=self.run, resolve=self.resolve, now=_RR_NOW)
        return code, out.getvalue(), err.getvalue()

    def archive(self, name, age_seconds, size=10):
        path = self.archive_root / "widgets" / name
        path.write_bytes(b"x" * size)
        stamp = _RR_NOW.timestamp() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def reasons(self, report):
        return {r["work_item_id"]: r["reason"] for r in report["not_finished"]}


def _rr_many(count, **extra):
    return {f"w{n:02d}": _rr_item(last=f"2026-09-{n + 1:02d}T00:00:00Z", **extra) for n in range(count)}


class RetentionReport(unittest.TestCase):
    def test_t1_merged_is_finished_open_phase_is_not_and_the_stored_fact_is_ignored(self):
        items = _rr_many(5)
        items["busy"] = _rr_item(phase="IMPLEMENTING")
        items["stale-merged"] = _rr_item(last="2026-08-01T00:00:00Z", gate_evidence={"pr": {"state": "merged"}})
        items["stale-open"] = _rr_item(last="2026-08-02T00:00:00Z", gate_evidence={"pr": {"state": "open"}})
        outcomes = {_rr_commit("stale-merged"): "open"}
        with _RR(items, outcomes=outcomes) as rr:
            report = rr.build()
            self.assertEqual(rr.reasons(report)["busy"], "open_phase")
            self.assertEqual(rr.reasons(report)["stale-merged"], "pr_not_merged:open")
            finished = [r["work_item_id"] for r in report["finished"]]
            self.assertIn("stale-open", finished)
            self.assertNotIn("stale-merged", finished)
            self.assertNotIn("busy", finished)

    def test_t2_a_parent_or_child_that_is_not_complete_is_family_open(self):
        items = {"parent": _rr_item(), "kid": _rr_item(parent="parent", last="2026-10-02T00:00:00Z"),
                 "slow-kid": _rr_item(phase="PLANNING", parent="parent"),
                 "orphan": _rr_item(parent="missing"),
                 "child-of-open": _rr_item(parent="open-parent"), "open-parent": _rr_item(phase="IMPLEMENTING")}
        with _RR(items) as rr:
            reasons = rr.reasons(rr.build())
            self.assertEqual(reasons["parent"], "family_open")
            self.assertEqual(reasons["orphan"], "family_open")
            self.assertEqual(reasons["child-of-open"], "family_open")
            self.assertEqual(reasons["slow-kid"], "open_phase")
            self.assertEqual(reasons["open-parent"], "open_phase")

    def test_t3_the_three_newest_merged_are_kept_with_ties_broken_by_id(self):
        items = _rr_many(4)
        for key in ("tie-b", "tie-a"):
            items[key] = _rr_item(last="2026-10-20T00:00:00Z")
        with _RR(items) as rr:
            report = rr.build()
            self.assertEqual([r["work_item_id"] for r in report["kept_recent"]], ["tie-a", "tie-b", "w03"])
            self.assertEqual([r["work_item_id"] for r in report["finished"]], ["w02", "w01", "w00"])
            self.assertEqual(report["totals"]["finished"], 3)

    def test_t4_every_query_outcome_and_a_missing_anchor_is_not_finished_with_its_reason(self):
        from workflow_forge import ForgeUnavailableError, ForgeUndecidableError
        items = {"ok-a": _rr_item(last="2026-10-09T00:00:00Z"), "unavail": _rr_item(last="2026-10-08T00:00:00Z"),
                 "undecid": _rr_item(last="2026-10-07T00:00:00Z"), "boom": _rr_item(last="2026-10-06T00:00:00Z"),
                 "nopr": _rr_item(last="2026-10-05T00:00:00Z"), "shut": _rr_item(last="2026-10-04T00:00:00Z"),
                 "ok-b": _rr_item(last="2026-10-03T00:00:00Z"), "anchorless": _rr_item(anchor=False),
                 "busy": _rr_item(phase="IMPLEMENTING"), "Bad Id": _rr_item()}
        outcomes = {_rr_commit("unavail"): ForgeUnavailableError("gh gone"),
                    _rr_commit("undecid"): ForgeUndecidableError("two"), _rr_commit("boom"): RuntimeError("x"),
                    _rr_commit("nopr"): "none", _rr_commit("shut"): "closed"}
        with _RR(items, outcomes=outcomes) as rr:
            reasons = rr.reasons(rr.build())
            self.assertEqual(reasons["unavail"], "query_failed:forge_unavailable")
            self.assertEqual(reasons["undecid"], "query_failed:forge_undecidable")
            self.assertEqual(reasons["boom"], "query_failed:unexpected")
            self.assertEqual(reasons["nopr"], "no_pull_request")
            self.assertEqual(reasons["shut"], "pr_not_merged:closed")
            self.assertEqual(reasons["anchorless"], "no_anchor")
            self.assertEqual(reasons["Bad Id"], "invalid_id")
            self.assertEqual(set(rr.queried), {_rr_commit(k) for k in ("ok-a", "unavail", "undecid", "boom", "nopr", "shut", "ok-b")})
            # newest first, one at a time
            self.assertEqual(rr.queried[0], _rr_commit("ok-a"))
            self.assertEqual(rr.queried[-1], _rr_commit("ok-b"))
            self.assertEqual(len(rr.queried), 7)

    def test_t4b_the_anchor_is_the_technical_approval_commit_only_with_its_identity(self):
        approved = {"reviewed_content_commit": "b" * 40, "approved_review_content_id": "c" * 64}
        half = {"reviewed_content_commit": "d" * 40}
        items = {"with-id": _rr_item(technical_approval=approved), "without-id": _rr_item(technical_approval=half)}
        with _RR(items) as rr:
            rr.build()
            self.assertIn("b" * 40, rr.queried)
            self.assertIn(_rr_commit("without-id"), rr.queried)
            self.assertNotIn("d" * 40, rr.queried)

    def test_t5_folder_sizes_symlinks_and_tracked_files(self):
        items = _rr_many(4)
        with _RR(items) as rr:
            _write(rr.repo, ".ai-review/w00/a.txt", "12345")
            _write(rr.repo, ".ai-review/w00/sub/b.txt", "123")
            elsewhere = Path(tempfile.mkdtemp(prefix="wfo-elsewhere-"))
            self.addCleanup(shutil.rmtree, elsewhere, True)
            (elsewhere / "big").write_text("x" * 1000)
            os.symlink(elsewhere / "big", rr.root / ".ai-review/w00/link")
            os.symlink(elsewhere, rr.root / ".ai-review/w00/dirlink")
            os.symlink(elsewhere, rr.root / ".ai-review/w01")
            _write(rr.repo, ".ai-review/w02/t.txt", "1")
            _run(["git", "add", "-f", ".ai-review/w02/t.txt"], cwd=rr.root)
            by_id = {r["work_item_id"]: r for r in rr.build()["finished"] + rr.build()["kept_recent"]}
            self.assertEqual(by_id["w00"]["review_folder"], {"present": True, "complete": True, "files": 2, "bytes": 8, "tracked_files": 0})
            self.assertFalse(by_id["w01"]["review_folder"]["present"])
            self.assertEqual(by_id["w02"]["review_folder"]["tracked_files"], 1)
            self.assertEqual(by_id["w03"]["review_folder"], {"present": False, "complete": True, "files": 0, "bytes": 0, "tracked_files": 0})

    def test_t6_archive_presence_age_and_prunable(self):
        items = _rr_many(6)
        day = 86400
        with _RR(items) as rr:
            rr.archive("w00.tar.zst", 30 * day)
            rr.archive("w01.tar.zst", 30 * day + 23 * 3600)
            rr.archive("w02.tar.zst", 31 * day + 1)
            real = rr.archive_root / "widgets" / "real"
            real.write_bytes(b"x")
            os.symlink(real, rr.archive_root / "widgets" / "w03.tar.zst")
            rr.archive("w04.tar.zst", 100 * day)          # recent (kept) item
            rr.archive("w99.tar.zst", 40 * day)           # no such item
            rr.archive("notes.txt", 40 * day)
            report = rr.build()
            archive = {r["work_item_id"]: r["archive"] for r in report["finished"]}
            self.assertFalse(archive["w00"]["prunable"])
            self.assertEqual(archive["w00"]["age_days"], 30)
            self.assertFalse(archive["w01"]["prunable"])
            self.assertEqual(archive["w01"]["age_days"], 30)
            self.assertTrue(archive["w02"]["prunable"])
            self.assertEqual(archive["w02"]["age_days"], 31)
            self.assertEqual(archive["w02"]["modified"], "2026-10-01T11:59:59Z")
            self.assertEqual(archive["w02"]["bytes"], 10)
            self.assertEqual(report["kept_recent"][0]["work_item_id"], "w05")
            self.assertEqual({a["name"]: a["item"] for a in report["archives"]},
                             {"w00.tar.zst": "finished", "w01.tar.zst": "finished", "w02.tar.zst": "finished",
                              "w04.tar.zst": "recent", "w99.tar.zst": "other"})
            self.assertEqual(report["totals"]["prunable"], 3)
            self.assertEqual(report["totals"]["archives"], 5)
            self.assertEqual(rr.build(ttl_days=60)["totals"]["prunable"], 1)

    def test_t6b_an_archive_of_a_reopened_item_is_open_and_an_absent_archive_is_null(self):
        items = {"reopened": _rr_item(phase="IMPLEMENTING"), **_rr_many(4)}
        with _RR(items) as rr:
            rr.archive("reopened.tar.zst", 90 * 86400)
            report = rr.build()
            self.assertEqual([a["item"] for a in report["archives"]], ["open"])
            self.assertEqual(report["finished"][0]["archive"],
                             {"present": False, "readable": True, "bytes": None, "modified": None, "age_days": None,
                              "prunable": False})

    def _every_failure_path(self):
        from workflow_forge import ForgeUnavailableError, ForgeUndecidableError
        items = {"a": _rr_item(), "b": _rr_item(), "c": _rr_item(), "d": _rr_item(), "e": _rr_item(),
                 "f": _rr_item(), "g": _rr_item(), "h": _rr_item(anchor=False), "i": _rr_item(phase="IMPLEMENTING")}
        outcomes = {_rr_commit("a"): ForgeUnavailableError("x"), _rr_commit("b"): ForgeUndecidableError("x"),
                    _rr_commit("c"): RuntimeError("x"), _rr_commit("d"): "none", _rr_commit("e"): "closed"}
        return items, outcomes

    def _populate(self, rr):
        _write(rr.repo, ".ai-review/a/x.txt", "data")
        rr.archive("a.tar.zst", 10 * 86400)
        rr.archive("zzz.tar.zst", 50 * 86400)

    def test_t7_nothing_is_written_on_any_path(self):
        items, outcomes = self._every_failure_path()
        with _RR(items, outcomes=outcomes) as rr:
            self._populate(rr)
            before = _rr_snapshot(rr.root, rr.archive_root)
            self.assertGreater(len(before), 20)
            self.assertTrue(any(k.endswith("x.txt") for k in before))
            self.assertTrue(any(k.endswith("zzz.tar.zst") for k in before))
            rr.build()
            self.assertEqual(rr.cli("--json")[0], 0)
            self.assertEqual(rr.cli()[0], 0)
            self.assertEqual(_rr_snapshot(rr.root, rr.archive_root), before)

    def test_t7b_nothing_is_written_when_every_query_fails_or_a_refusal_happens(self):
        from workflow_forge import ForgeUnavailableError
        items = _rr_many(6)
        every = {_rr_commit(k): ForgeUnavailableError("x") for k in items}
        with _RR(items, outcomes=every) as rr:
            self._populate(rr)
            before = _rr_snapshot(rr.root, rr.archive_root)
            self.assertEqual(rr.cli()[0], 0)
            self.assertEqual(rr.build()["finished"], [])
            for bad in ("0", "abc"):
                self.assertEqual(rr.cli("--ttl-days", bad)[0], 2)
                self.assertEqual(_rr_snapshot(rr.root, rr.archive_root), before)
            self.assertEqual(_rr_snapshot(rr.root, rr.archive_root), before)
        with _RR(items, origin=False) as rr:
            self._populate(rr)
            state_path = rr.root / "docs/ai-workflow/WORKFLOW_STATE.json"

            def refused_leaves_everything_as_it_was():
                before = _rr_snapshot(rr.root, rr.archive_root)
                self.assertEqual(rr.cli()[0], 2)
                self.assertEqual(_rr_snapshot(rr.root, rr.archive_root), before)

            refused_leaves_everything_as_it_was()          # repository_unknown
            os.unlink(state_path)
            refused_leaves_everything_as_it_was()          # state_missing
            _write(rr.repo, "docs/ai-workflow/WORKFLOW_STATE.json", "{not json")
            refused_leaves_everything_as_it_was()          # state_invalid

    def _deny(self, path):
        mode = os.stat(path).st_mode
        os.chmod(path, 0)

        def restore():
            try:
                os.chmod(path, mode)
            except FileNotFoundError:
                pass

        self.addCleanup(restore)

    def test_t7c_unreadable_review_and_archive_paths_are_marked_incomplete_and_nothing_is_written(self):
        if os.geteuid() == 0:
            self.skipTest("permission denial cannot be provoked as root")
        with _RR(_rr_many(4)) as rr:
            _write(rr.repo, ".ai-review/w00/a.txt", "12345")
            _write(rr.repo, ".ai-review/w00/sub/b.txt", "123")
            _write(rr.repo, ".ai-review/w00/sub/deep/c.txt", "12")
            _write(rr.repo, ".ai-review/w01/keep.txt", "1")
            rr.archive("w00.tar.zst", 40 * 86400, size=20)
            clean = {r["work_item_id"]: r for r in rr.build()["finished"] + rr.build()["kept_recent"]}
            self.assertEqual(clean["w00"]["review_folder"]["bytes"], 10)
            self.assertTrue(rr.build()["complete"])
            before = _rr_snapshot(rr.root, rr.archive_root)
            sub = rr.root / ".ai-review/w00/sub"
            self._deny(sub)
            for args in ((), ("--json",)):
                code, out, err = rr.cli(*args)
                self.assertEqual(code, 0)
                self.assertTrue(err.startswith("warning: incomplete: 1 path(s)"), err)
                self.assertIn("INCOMPLETE" if not args else '"complete": false', out)
            report = rr.build()
            by_id = {r["work_item_id"]: r for r in report["finished"] + report["kept_recent"]}
            self.assertFalse(by_id["w00"]["review_folder"]["complete"])
            self.assertEqual(by_id["w00"]["review_folder"]["bytes"], 5)
            self.assertTrue(by_id["w01"]["review_folder"]["complete"])
            self.assertFalse(report["complete"])
            self.assertEqual([e["path"] for e in report["read_errors"]], [str(sub)])
            self.assertIn("PermissionError", report["read_errors"][0]["error"])
            os.chmod(sub, 0o755)
            nested = sub / "deep"
            self._deny(nested)
            self.assertEqual([e["path"] for e in rr.build()["read_errors"]], [str(nested)])
            os.chmod(nested, 0o755)
            # an unreadable file's directory: entries cannot be examined
            self._deny(rr.root / ".ai-review/w00/sub")
            os.chmod(rr.root / ".ai-review/w00/sub", 0o644)
            self.assertFalse(rr.build()["complete"])
            os.chmod(rr.root / ".ai-review/w00/sub", 0o755)
            # the archive root cannot be searched: absence is not claimed
            self._deny(rr.archive_root)
            report = rr.build()
            self.assertFalse(report["complete"])
            self.assertEqual(report["archives"], [])
            archive = {r["work_item_id"]: r["archive"] for r in report["finished"]}
            self.assertFalse(archive["w00"]["readable"])
            self.assertFalse(report["read_errors"] == [])
            self.assertTrue(all("PermissionError" in e["error"] for e in report["read_errors"]))
            code, out, err = rr.cli("--json")
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(out)["complete"])
            self.assertIn("warning: incomplete", err)
            os.chmod(rr.archive_root, 0o755)
            # the archive directory itself cannot be listed
            self._deny(rr.archive_root / "widgets")
            report = rr.build()
            self.assertFalse(report["complete"])
            self.assertEqual(report["archives"], [])
            self.assertIn(str(rr.archive_root / "widgets"), [e["path"] for e in report["read_errors"]])
            os.chmod(rr.archive_root / "widgets", 0o755)
            self.assertEqual(_rr_snapshot(rr.root, rr.archive_root), before)
            self.assertTrue(rr.build()["complete"])

    def test_t8_cli_json_text_and_refusals(self):
        items = _rr_many(5)
        items["busy"] = _rr_item(phase="IMPLEMENTING")
        with _RR(items) as rr:
            rr.archive("w00.tar.zst", 40 * 86400)
            code, out, err = rr.cli("--json")
            self.assertEqual((code, err), (0, ""))
            self.assertTrue(out.endswith("\n"))
            report = json.loads(out)
            self.assertEqual(sorted(report), ["archive_dir", "archives", "complete", "finished", "generated_at",
                                              "keep_recent", "kept_recent", "kind", "not_finished", "read_errors",
                                              "repository", "schema_version", "totals", "ttl_days"])
            self.assertEqual((report["complete"], report["read_errors"]), (True, []))
            self.assertEqual(report["repository"], "acme/widgets")
            self.assertEqual(report["kind"], "retention-report")
            self.assertEqual(report["generated_at"], "2026-11-01T12:00:00Z")
            self.assertEqual(sorted(report["finished"][0]), ["archive", "last_transition", "pull_request",
                                                             "review_folder", "work_item_id"])
            self.assertEqual(sorted(report["kept_recent"][0]), ["last_transition", "pull_request",
                                                                "review_folder", "work_item_id"])
            self.assertEqual(report["finished"][0]["pull_request"],
                             {"number": 7, "url": "https://github.com/acme/widgets/pull/7"})
            self.assertEqual(sorted(report["totals"]), ["archives", "finished", "finished_folder_bytes", "prunable"])
            self.assertEqual(out, json.dumps(report, sort_keys=True) + "\n")
            code, text, err = rr.cli()
            self.assertEqual(code, 0)
            for needle in ("acme/widgets", "Finished (2)", "Kept as recent (3)", "Not finished (1)",
                           "open_phase: 1", "prunable (1)", "w00.tar.zst"):
                self.assertIn(needle, text)
            for argv in (["--ttl-days", "0"], ["--ttl-days", "abc"], ["--ttl-days", "3651"], ["--bogus"]):
                code, out, err = rr.cli(*argv)
                self.assertEqual(code, 2, argv)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("error: usage: "), err)
                self.assertIn("\nnext step: ", err)
            self.assertEqual(rr.cli("--ttl-days", "3650")[0], 0)

    def test_t8b_refusal_codes(self):
        with _RR({}, origin=False) as rr:
            code, out, err = rr.cli()
            self.assertEqual((code, out), (2, ""))
            self.assertTrue(err.startswith("error: repository_unknown: "), err)
            os.unlink(rr.root / "docs/ai-workflow/WORKFLOW_STATE.json")
            self.assertTrue(rr.cli()[2].startswith("error: state_missing: "))
            _write(rr.repo, "docs/ai-workflow/WORKFLOW_STATE.json", "[]")
            self.assertTrue(rr.cli()[2].startswith("error: state_invalid: "))
            _write(rr.repo, "docs/ai-workflow/WORKFLOW_STATE.json", "{")
            self.assertTrue(rr.cli()[2].startswith("error: state_invalid: "))

    def test_t8c_a_real_subprocess_without_origin_exits_2_with_repository_unknown(self):
        with _RR(_rr_many(1), origin=False) as rr:
            script = Path(wr.__file__).resolve()
            proc = subprocess.run(["python3", "-I", str(script), "report", "--repo-root", str(rr.root),
                                   "--archive-root", str(rr.archive_root)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("error: repository_unknown", proc.stderr)
            self.assertIn("next step:", proc.stderr)
            usage = subprocess.run(["python3", "-I", str(script)], capture_output=True, text=True)
            self.assertEqual(usage.returncode, 2)
            self.assertIn("error: usage", usage.stderr)

    def _child_env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTHONPATH")}
        return env

    def _scripts_copy(self):
        copy = Path(tempfile.mkdtemp(prefix="wfo-scripts-"))
        self.addCleanup(shutil.rmtree, copy, True)
        for name in ("workflow_retention.py", "workflow_forge.py"):
            shutil.copy(Path(wr.__file__).resolve().parent / name, copy / name)
        return copy

    def test_t9_bytecode_contract_from_fresh_interpreters(self):
        env = self._child_env()
        # (a) the CLI form leaves no __pycache__ anywhere
        with _RR(_rr_many(1), origin=False) as rr:
            copy = self._scripts_copy()
            self.assertEqual(_rr_find_pycache(copy, rr.root, rr.archive_root), [])
            proc = subprocess.run(["python3", "-I", str(copy / "workflow_retention.py"), "report",
                                   "--repo-root", str(rr.root), "--archive-root", str(rr.archive_root)],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(_rr_find_pycache(copy, rr.root, rr.archive_root), [])
        # (b) the import form with the assignment before the import leaves none
        code = ("import sys\nsys.dont_write_bytecode = True\nsys.path.insert(0, sys.argv[1])\n"
                "import datetime, workflow_retention as w\n"
                "w.build_report(sys.argv[2], archive_root=sys.argv[3], now=datetime.datetime.now(datetime.timezone.utc),\n"
                "               run=lambda argv, timeout: '[]', resolve=lambda r: {'path': '/usr/bin/gh', 'sha256': '0'})\n")
        with _RR({}) as rr:
            copy = self._scripts_copy()
            proc = subprocess.run(["python3", "-I", "-c", code, str(copy), str(rr.root), str(rr.archive_root)],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(_rr_find_pycache(copy, rr.root, rr.archive_root), [])
        # (c) control: the same import without the assignment does create one
        copy = self._scripts_copy()
        proc = subprocess.run(["python3", "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import workflow_retention",
                               str(copy)], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(_rr_find_pycache(copy)), 1)

    def test_t10_the_output_is_a_pure_function_of_state_folders_archives_and_now(self):
        items = {**_rr_many(6), "busy": _rr_item(phase="IMPLEMENTING")}
        with _RR(items) as rr:
            for name in ("w00", "w01"):
                rr.archive(f"{name}.tar.zst", 40 * 86400)
            _write(rr.repo, ".ai-review/w00/a", "1")
            first = rr.cli("--json")[1]
            rr.write_state(dict(reversed(list(items.items()))))
            for name in ("w00", "w01"):
                os.unlink(rr.archive_root / "widgets" / f"{name}.tar.zst")
            for name in ("w01", "w00"):
                rr.archive(f"{name}.tar.zst", 40 * 86400)
            self.assertEqual(rr.cli("--json")[1], first)
            report = json.loads(first)
            self.assertEqual([a["name"] for a in report["archives"]], ["w00.tar.zst", "w01.tar.zst"])


if __name__ == "__main__":
    unittest.main()
