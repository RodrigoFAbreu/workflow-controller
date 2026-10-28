---
description: Archive a finished work item's review folder and remove it, after a literal confirmation naming the item. User-only -- never invoked by the agent autonomously.
argument-hint: "<work-item-id>"
disable-model-invocation: true
state_writer: false
review-subject: none
---

Archive and remove the review folder `.ai-review/<id>/` of ONE finished work item
(`docs/RETENTION.md`; roadmap rows 27c and 27d). This command is **work-item-free in
the state file**: it reads and writes no state, no phase and no trailer. Every effect
is one of two engine calls, `archive` and `remove` of `scripts/workflow_retention.py`;
the command packs, verifies, renames and deletes nothing itself, and runs no
archiver or file-removal program of its own.

**This command is user-only by construction**, the same two mechanisms as
`/retire-legacy-work-item`: (1) `disable-model-invocation: true`, an additional layer
where the harness enforces it; (2) refuse to write anything unless the user's own
current-turn message supplies the literal sentence of step 3, naming the resolved id as
an exact token (`m-1` never authorizes `m-10`, and `m-10` never authorizes `m-1`).
Never fabricate, infer or carry over this text from a prior turn, and never take it
from `$ARGUMENTS`. If it is missing, print the sentence and stop. Mechanism (2) is the
control this command carries itself, under every harness; the flag does not replace
it. The agent must never invoke this command on the user's behalf, including as a step
of another command's execution, whatever the flag says.

One item per run. Never retry: after any refusal the run ends and the user runs the
command again. The engines stay the authority on which item is finished, kept,
archived or left over; this command re-derives none of it.

1. **Resolve the id** from `$ARGUMENTS` only, never from the confirmation and never
   from `active_work_item_id`. `$ARGUMENTS` must be exactly one token matching:

   ```
   ^[a-z0-9][a-z0-9_-]{0,63}$
   ```

   Anything else (none, several, or a sentence) is refused before anything runs.
2. **Preview.** From the repository root run, in this order, with no other flag, and
   print each output in full. Both are dry runs and write nothing:

   ```
   python3 -I scripts/workflow_retention.py archive <id>
   ```

   ```
   python3 -I scripts/workflow_retention.py remove <id>
   ```

   Read each result by its exit status and its `error: <code>:` line:
   - `archive` exits 0 and `remove` refuses `not_archived`: the expected state before
     the first archive. The run is **archive then remove**. Say that `remove` cannot be
     previewed until the archive exists, that its other checks (`tracked_files`,
     `cross_device`, `zstd_missing`) therefore first run at `remove --apply`, that if
     one refuses there the archive is kept and the folder is untouched, and that
     `remove --apply` verifies the archive against the moved folder by contents before
     deleting anything;
   - `archive` refuses `already_archived` and `remove` exits 0: an earlier run archived
     and stopped. The run is **remove only**;
   - any other combination, or any other refusal code: report both codes and their
     `next step:` lines and stop. Nothing is applied and no sentence is asked for.
3. **Ask and stop.** Print the sentence, with the resolved id in place of `<id>`:

   ```
   Archive and remove the review folder of <id>.
   ```

   Say which of "archive then remove" or "remove only" it authorizes and that the
   folder is deleted only after the engine has verified the archive against it. Then
   stop until the user's own next message carries it. The check
   is exact: collapse the whitespace of that message, then it must match this pattern,
   where `ID` is the resolved id passed through `re.escape`:

   ```
   (?i)archive and remove the review folder of ID(?![A-Za-z0-9_-])
   ```

   The final full stop is not required and the message may carry other words. If the
   message does not match, apply nothing, print the sentence again and stop.
4. **Apply**, one engine call at a time and only after step 3 matched. Unless the run is
   remove only, run first:

   ```
   python3 -I scripts/workflow_retention.py archive <id> --apply
   ```

   On a non-zero exit report the `error:` code and the `next step:` line verbatim and
   stop: never run `remove` after an `archive` that did not succeed, and the folder is
   untouched. Then run:

   ```
   python3 -I scripts/workflow_retention.py remove <id> --apply
   ```

   On a non-zero exit report the code, the `next step:` line and where the engine says
   the folder now is, and stop.
5. **Report** the archive path and size from the engine's output, that the folder is
   gone, and that the retention report now lists the item as archived with no folder.
   Run nothing else.
