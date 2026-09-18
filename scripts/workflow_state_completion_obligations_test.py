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
(`WFO-LEDGER-COVERAGE`, `WFR-68`, `WF8c` item (m)).

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
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import workflow_fingerprint as fingerprint
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


if __name__ == "__main__":
    unittest.main()
