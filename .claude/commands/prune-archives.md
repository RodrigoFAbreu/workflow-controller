---
description: Delete named archives past the time to live from the archive store, after a literal confirmation naming them. User-only -- never invoked by the agent autonomously.
argument-hint: "<work-item-id>..."
disable-model-invocation: true
state_writer: false
review-subject: none
---

Delete the archives `<id>.tar.zst` of the work items named in `$ARGUMENTS` from the
archive store (`docs/RETENTION.md`; roadmap rows 27f and 27g). This command is
**work-item-free in the state file**: it reads and writes no state, no phase and no
trailer. Every effect is one of two engine calls, the dry run and the apply of
`prune` in `scripts/workflow_retention.py`; the command deletes, moves and unpacks
nothing itself, and runs no archiver or file-removal program of its own.

**This command is user-only by construction**, the same two mechanisms as
`/archive-finished-item`: (1) `disable-model-invocation: true`, an additional layer
where the harness enforces it; (2) refuse to write anything unless the user's own
current-turn message supplies the literal sentence of step 3, naming exactly the
archives of step 1 (`m-1` never authorizes `m-10`, and `m-10` never authorizes
`m-1`). Never fabricate, infer or carry over this text from a prior turn, and never
take it from `$ARGUMENTS`. If it is missing, print the sentence and stop. Mechanism
(2) is the control this command carries itself, under every harness; the flag does
not replace it. The agent must never invoke this command on the user's behalf,
including as a step of another command's execution, whatever the flag says.

One run, one engine judgment. Never retry: after any refusal the run ends and the
user runs the command again. Never an archive the user did not name: the apply
passes the same names as the preview, none added. The engine stays the authority on
which archive is prunable, which item is open or recent and which removal is
interrupted, at the moment of `--apply`; this command re-derives none of it.

1. **Resolve the names** from `$ARGUMENTS` only, never from the confirmation, never
   from `active_work_item_id` and never from a listing: pass the work item id of each
   archive, its name without `.tar.zst`. `$ARGUMENTS` must be one or more distinct
   tokens separated by spaces, each matching:

   ```
   ^[a-z0-9][a-z0-9_-]{0,63}$
   ```

   Anything else (none, a repeat, a file name or path, or a sentence) is refused
   before anything runs.
2. **Preview.** From the repository root run, with no other flag, and print the
   output in full. It is a dry run and writes nothing:

   ```
   python3 -I scripts/workflow_retention.py prune <names>
   ```

   Exit 0: every named archive would be removed; go on. Any non-zero exit: report the
   `error:` code and the `next step:` line verbatim and stop. Nothing is applied and
   no sentence is asked for.
3. **Ask and stop.** Print the sentence, with the names in the order of `$ARGUMENTS`,
   separated by a comma and a space, and the final full stop:

   ```
   Prune the archives <n1>, <n2>.
   ```

   Say that the archives listed in the preview are deleted for good, that their index
   lines stay, and that the engine judges again at the apply. Then stop until the
   user's own next message carries the sentence. The check is exact: collapse the
   whitespace of that message, then find every match of this pattern, where `T` is
   `[a-z0-9][a-z0-9_-]{0,63}`:

   ```
   (?i)prune the archives (T(?:, T)*)\.
   ```

   The confirmation holds when there is at least one match and, in every match, the
   lower-cased names, as a set, equal the set of names of step 1. A sentence naming
   only some of them, others besides, a prefix or an extension of one, an "and" list,
   or lacking the final full stop does not hold; the order does not matter and the
   message may carry other words. If the message does not hold, apply nothing, print
   the sentence again and stop.
4. **Apply**, one engine call, only after step 3 held:

   ```
   python3 -I scripts/workflow_retention.py prune <names> --apply
   ```

   On a non-zero exit report the `error:` code, the `next step:` line and the
   engine's `removed:` and `not removed:` lists verbatim, and stop.
5. **Report** the removed archives and the bytes from the engine's output, and that
   the index keeps its lines. Run nothing else.
