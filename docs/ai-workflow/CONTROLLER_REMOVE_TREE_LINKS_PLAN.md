# remove_tree refuses a path below a symbolic link (Revision 2)

Work item: `workflow-controller-remove-tree-links`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `f61a99d0ab3aaa4429c6bbaa625903bd9662ed31` ("fix: functional checklist flow ends are read or refused and a link above the checklist's folder is followed (C10j, #116) (#284)"), the tip of `main`, passed explicitly as `/milestone-plan f61a99d…`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row C10k (split out of CU3); the next item is CL1a.
Released baseline: `workflow-controller 1.27.8`. The change makes a primitive refuse a path below a link; the one caller that can make such a call is a Codex review launch over a worktree whose `.agents` is a link (D4). It adds no setting, option, exit code or gate, so the squash commit is a `fix:` change and releases a patch (1.27.9).
Pull request title: `fix: remove_tree refuses a path below a symbolic link (C10k, #140)`

## Goal

Close [#140](https://github.com/RodrigoFAbreu/workflow-controller/issues/140), the one point of it still open (the fake tester's `..` handling and the two identical link tests were done in CU3a). `runtime.remove_tree(root, rel_path)` documents that it never follows a symbolic link, but it resolves `target.parent` before removing (`controller/runtime.py:486`). Called with a path *below* a link (`remove_tree(<sandbox>, "clone/build/artifacts")` where `clone/build` is a link to `<sandbox>/writable/build`), the resolved parent is the link's destination, so the call removes the destination's contents. No production caller reaches it except `review.build_copy` for a worktree whose `.agents` is a link (D4); the C10f functional tester found it with `probe-layout.py`. The primitive is the Controller's one removal path for sandboxes, job homes and credential copies, so it must hold its own documented rule.

## Decisions

Each decision states one principle.

- **D1. A removal never passes through a link below its root.** `remove_tree` walks the components of `rel_path` from `root` before it removes anything; every component that is *above the last one* is looked at with `lstat`, and one that is a symbolic link raises `RuntimeContainmentError` (the error the primitive already raises for a path outside the root), with evidence naming the root, the path and the link. The last component is exempt: removing a link by its own name has always unlinked the link and left its destination, and that stays. A component that does not exist ends the walk (the path is already gone, which is not an error). `root` itself may be a link or sit below one: only components below it are examined, as the containment check already resolves the root. An absolute `rel_path` is refused with the same error unless it lies inside `root`, in which case it is taken relative to `root` (`Path.relative_to`) before the walk, so the walk never examines a component above the root.
- **D2. A `..` component is refused rather than walked.** A lexical walk cannot vouch for what `a/../b` crosses, and no caller passes one. A `..` component in `rel_path` raises the same `RuntimeContainmentError`; `.` and empty components are skipped. When no component remains (`"."` or `""`) the target is the root itself and is handled exactly as today (`root.parent.resolve() / root.name`): a root that is a link is unlinked and its destination kept, a plain root is cleared and removed. The tests' cleanups (`remove_tree(d, ".")`) are unchanged.
- **D3. The refusal removes nothing, and nothing else changes.** The walk runs before the first `unlink`, so a refused call leaves the destination and the link untouched. After the walk, with at least one component, `target` is built from the resolved root and the original components, so the `target.parent.resolve()` line goes away (the empty-component case is D2's); `keep`, the mode fix on the way down and the already-gone case behave as before.
- **D4. Every caller is checked, and the one that changes is decided.** The relative paths all callers pass (`controller/review.py`, `functional_review.py`, `claude_build.py`, `codex_verify.py`, `codex_tester.py`, `codex_worker.py`, `job.py`) were read: every one is a fixed path or a last component below a real folder, except `review.build_copy`'s strip entries (`STRIPPED_COPY_PATHS`, `controller/review.py:221`). Two of those have a component above their last, `.agents/skills` and `.agents/plugins`, and only the Codex harness strips them (`CLAUDE_STRIPPED_COPY_PATHS` entries are single-component). So a Codex review of a worktree whose `.agents` is a *relative link inside the worktree* worked before (the link's destination lies in the disposable copy, the worktree's own files stayed untouched, control (e) held) and now fails; an absolute `.agents` link is already refused by containment today. **Decision: refuse.** A linked `.agents` is rare, and refusing is the primitive's rule applied unchanged and the fail-closed answer; `build_copy` is not changed. The error path, read at `controller/job.py:7249`: `launch_review` is `build_copy`'s one production caller; `RuntimeContainmentError` is not a `ReviewLaunchRefusedError`, so it takes the `except Exception` branch: the job record ends `FAILED` with `outcome: error`, `not_started` true and the reservation released, the error is re-raised and `controller/cli.py` prints `error: <message>` and exits fail-closed. This is the same end an absolute `.agents` link already has; no edit to `review.py` or `job.py` is made and none is conditional. The operator-visible cause and remedy (remove the link from the repository, or replace it with a real folder) go in `docs/common-problems.md` (CP2). Tests pin the refusal for both entries at `build_copy` and the job-level end (CP1).

## Out of scope

A race between the walk and the removal (the sandbox's own jobs are stopped before their folders are removed, as today), changing which paths callers remove, and any change to `copy_tree`, `write_private_bytes` or `_assert_contained`.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | remove_tree refuses a path whose parent chain holds a link | - | 2 | 1 |
| CP2 | The roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- remove_tree refuses a path whose parent chain holds a link

**Files:** `controller/runtime.py`, `tests/test_runtime.py`, `tests/test_review_job.py`

D1, D2, D3, D4. The walk and the removal of the `parent.resolve()` line in `controller/runtime.py`; the docstring says the refusal. Tests in `tests/test_runtime.py`: the repro (`clone/build` a link to `writable/build`, `remove_tree(sandbox, "clone/build/artifacts")`) raises `RuntimeContainmentError` and the destination's files are all still there; a link two levels up is refused too; a link as the last component is unlinked and its destination kept; a `..` component is refused; a path through a missing component is a no-op; `keep` and the read-only-subtree removal still work; a root that is itself reached through a link still removes a plain path below it; a root that is a link with `rel_path` `.` and `""` is unlinked and its destination's files are still there; an absolute `rel_path` inside the root removes that path and one outside is refused; a dangling link above the last component is refused (the base treated it as a no-op). `tests/test_review_job.py` has one test that loops over `.agents/skills` and `.agents/plugins` with `.agents` a link in a copied tree (`review.build_copy`) and pins the refusal, and one job-level test that a linked `.agents` ends the review record `FAILED` with `outcome: error`, `not_started` true and the destination untouched (D4). The caller read of D4 is recorded in the checkpoint's commit message.

Done when: the new tests pass and the repro test fails against the base commit (checked once locally); the full suite is green but for the known Git 2.56 trailer tests.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The roadmap

**Files:** `docs/ROADMAP.md`, `docs/common-problems.md`

The roadmap marks C10k done (1.27.9) and follow-up 84 done; `docs/ACTIVE_MILESTONE.md` is updated at acceptance. No guide or page describes `remove_tree`; `docs/common-problems.md` (under "A review the Controller ran did not finish") gains the linked-`.agents` entry D4 names: the message, the cause and the remedy.

Done when: `python3 tools/check_docs.py` passes and the roadmap marks C10k done.
<!-- /CP2 -->
