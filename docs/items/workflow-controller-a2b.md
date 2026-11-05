# A2b: the prose migration

Item id: `workflow-controller-a2b`. GitHub issue: [#298](https://github.com/RodrigoFAbreu/workflow-controller/issues/298).

**A2b: the prose migration (owner decisions 2026-10-18 and 2026-11-05).** The backlog is declared and the roadmap page is generated (A2a, #294). The old hand-written page is kept unchanged in `docs/roadmap-archive.md` ("Frozen"). `/milestone-plan` now reads an item's `detail` page as the milestone's description and falls back to the title alone when there is none, so the prose must move to `docs/items/<id>.md` pages. One mechanism, a `chore:` (no release): move text, change no code but one test.

## Scope

1. **Item pages.** A page `docs/items/<id>.md` for every item of `docs/backlog.yaml` whose archive row carries prose beyond its title, and the `detail: docs/items/<id>.md` line on the entry (the validator requires the file to exist, to be a regular file with no link in the path, and the path to use only `A-Za-z0-9._/-`). A throwaway script (not committed) reads the archive's two tables and writes the pages, as A2a built the file; its output is reviewed. A page holds the row's text verbatim (complexity, dependencies, owner decisions, issue links) under a short heading; **no status in a page** (status lives in the backlog).
2. **Open items by hand.** The pages of the open items (A2b itself, H4e to H4g, C10d-a/b, CL1a to CL1e, C12b to C12d, C8c and the ideas) are what the next planners read: check each by hand for a complete description, the rules that apply (sizing rule, one mechanism, releasable alone) and the planning guidance in its GitHub issue where one exists.
3. **Titles.** Every title is a complete short phrase. `docs/backlog.yaml` line 172 (C10g-3c2) ends mid-phrase ("... and one"); list every title that does, fix them and regenerate.
4. **Rules and direction live.** The archive holds sections that are not items: the direction sections, the clean-up limit (more than 8 open `review-follow-up` issues), the sizing rule, "Next, in order", the owner decisions. Planners read the live roadmap, which cannot hold them. Move them **unchanged** to a live page the docs map links (the name is the plan's choice), and repoint every reference to "the roadmap's rule" (`.claude/commands/`, the Controller's own docs, `docs/guide/development.md`).
5. **Small repairs left by A2a** (comment on #174): release notes `docs/releases/1.4.1.md`, `1.4.2.md`, `1.5.0.md`, `1.6.0.md`, `1.7.0.md` point at "Release notes follow the milestone" in `docs/ROADMAP.md`; and `test_selection_survives_reordering_open_items_and_accepting_one` (`tests/test_backlog.py`) must swap the selected item with the next open one so a mutant that ignores `order` fails.

## Measure

Every archive row with prose has exactly one page and its text appears in it (a one-off script, result in the report); every `detail` resolves; `python3 scripts/workflow_backlog.py check` and `python3 tools/check_docs.py` pass; `next` still names the right item.

## Out of scope

The Manager and Workflow lane rows (other repositories), a declared generator, a tracker, changing the Workflow's scripts (file issues in `RodrigoFAbreu/workflow`).

## Complexity

Medium. If the plan exceeds 5 checkpoints, split as: CP1 the script and the done items' pages, CP2 the open items' pages and titles, CP3 the rules page, links and the two repairs.
