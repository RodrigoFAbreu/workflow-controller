# The backlog file

A repository can keep its list of planned work in one structured file, the
**backlog file**, instead of prose in `docs/ROADMAP.md`. Once the repository
declares it, `/milestone-plan` with no argument takes the next ready item from
it, acceptance marks the item `done`, and the roadmap page is regenerated from
the file inside the acceptance commit. A repository that declares nothing
behaves exactly as it did before: nothing in this document applies to it.

Everything is done by one standard-library script,
`scripts/workflow_backlog.py`. This is the document its error messages name.

- [Setup](#setup)
- [The file](#the-file)
- [How `/milestone-plan` chooses](#how-milestone-plan-chooses)
- [What acceptance does](#what-acceptance-does)
- [The roadmap page and its generator](#the-roadmap-page-and-its-generator)
- [Paths](#paths)
- [Items in flight at adoption](#items-in-flight-at-adoption)
- [Commands and exit codes](#commands-and-exit-codes)
- [Moving back to an earlier release](#moving-back-to-an-earlier-release)

## Setup

1. Add one key to `docs/ai-workflow/WORKFLOW_CONFIG.json`:

   ```json
   "backlog": {
     "file": "docs/backlog.yaml",
     "roadmap": "docs/ROADMAP.md",
     "generator": ["python3", "tools/roadmap.py"]
   }
   ```

   `file` is required. `roadmap` is optional and defaults to `docs/ROADMAP.md`.
   `generator` is optional: a non-empty array of non-empty strings, an argument
   vector that is never run through a shell. Unknown keys inside `backlog` are
   refused, and `backlog` must be an object. A missing key, a missing or
   unparsable config, or a config that is not an object all mean "no backlog":
   the repository behaves as before. Only `scripts/workflow_backlog.py` reads
   this key, so a mistake in it stops the backlog steps and nothing else
   (`declaration_invalid`).

2. Create the file. This is the smallest valid one:

   <!-- backlog-example:starter -->
   ```yaml
   version: 1
   items:
     - id: first-item
       title: My first item
       status: next
       order: 1
   ```

3. Generate the roadmap page once. If a hand-written page already exists at
   `roadmap`, the built-in generator refuses to overwrite it silently
   (`roadmap_not_generated`); replace it deliberately, once (the old page stays
   in Git history):

   ```bash
   python3 scripts/workflow_backlog.py generate --replace
   ```

   Commit the file, the page and the config change together.

4. Optional but recommended: make your CI fail when the page is stale by adding
   one step that runs `python3 scripts/workflow_backlog.py check`. The
   managed workflow does not add it for you.

## The file

The file is a strict, documented subset of YAML read by the script's own
parser. Every file it accepts means the same thing to any YAML parser, and
everything else is refused with `<file>:<line>: <what>`.

### The accepted form

- UTF-8 without a byte-order mark, LF line ends only (a carriage return is
  refused), at most 256 KiB and 2000 lines, no line longer than 1000
  characters. Control characters (tab included), U+0085, U+2028, U+2029,
  U+FEFF and the noncharacters U+FFFE and U+FFFF are refused with their line.
- Blank lines and comment lines (first non-space character `#`) are allowed
  anywhere. A trailing comment is exactly one space, then `#`, after a value
  outside quotes.
- Not allowed: document markers (`---`, `...`), directives, anchors (`&`),
  aliases (`*`), tags (`!`), block scalars (`|`, `>`), flow mappings (`{`), merge
  keys, multi-line scalars, nested mappings, and any `null`, `~` or empty value.
  Anchors, aliases, tags and block-scalar indicators are refused only as the
  first character of an unquoted value; inside a string they are ordinary
  characters (`R&D` and `"a *b"` are fine).
- The top level has exactly two keys at column 0, in either order:
  `version: 1` and `items:`. `items:` is followed by a block sequence, or on the
  same line by `[]` for an empty backlog. A trailing comment is also allowed after
  `items:` and after `depends_on:` when the value is a block list on the following
  lines (the key has no inline value).
- Each item starts with `  - ` (two spaces, a dash, a space) and its first
  `key: value`; its other keys are at four spaces. The keys are `id`, `title`,
  `status`, `order`, `depends_on`, `size`, `detail` and `release`, each at most
  once. An unknown or repeated key is refused.
- A string is **double-quoted** (the only escapes are `\\` and `\"`),
  **single-quoted** (`''` is one quote) or **plain**. A plain string starts with
  a letter, is not one of `true false null yes no on off y n` in any case,
  contains no `: ` or ` #`, does not end in `:`, and contains none of
  `[ ] { } ,`. Anything else is quoted. So an `id` or `release` that starts with a
  digit (`2.17.0`) is written in quotes.
- The integers `order`, `size` and `version` are plain decimal digits, no sign,
  no leading zero, at most six digits. A quoted number there is refused.
- `depends_on` is `[]`, a one-line list (`[a, b]`), or `depends_on:` followed by
  a block list of `      - id` lines at six spaces (at least one). A bare
  `depends_on:` with nothing after it is refused (YAML reads it as `null`), as
  is a flow list with a trailing comma or an empty entry. Leaving the key out
  means no dependencies.
- **Whitespace is pinned, not normalised**: exactly one space after a key's
  `:`; a value is followed by nothing, or by exactly one space and `#`; no line
  ends in a space; inside a flow list entries are separated by a comma and one
  space (`[a, b]`), with no space after `[` or before `]` or `,`. The one
  reading in every parser is what lets acceptance change only the `status`
  (and `release`) lines and leave every other byte alone.

### The schema

Applied after parsing; every violation of one pass is listed (ten shown), each
with its line.

| Field | Rule |
|---|---|
| `version` | `1` |
| `id` | required, `^[a-z0-9][a-z0-9_-]{0,63}$`, unique. An item id **is** its work item id. |
| `title` | required, 1 to 200 characters |
| `status` | required: `idea`, `next`, `in-progress` or `done` |
| `order` | required, 1 to 999999, **unique across all items** (done and idea included) |
| `depends_on` | each entry is the `id` of another item (not its own), no duplicate, no cycle |
| `size` | optional, 1 to 99: the estimated checkpoint count |
| `detail` | optional: a path under `docs/items/` made only of `A-Za-z0-9._/-`; the file must exist and be a regular file, and no component of the path is a symbolic link |
| `release` | optional, only on a `done` item: 1 to 40 characters from `A-Za-z0-9._+-`, starting with a letter or digit |

The order of items in the file is free; the page sorts by `order`. A `done`
item may depend on an item that is not `done`: it is shown, not refused.

### One complete example

<!-- backlog-example:full -->
```yaml
# Comments are allowed on their own line.
version: 1
items:
  - id: login
    title: "Sign-in: email and password" # quoted: it contains ": "
    status: done
    order: 1
    size: 3
    detail: docs/items/login.md
    release: "1.0.0"
  - id: profile
    title: Profile page
    status: in-progress
    order: 2
    depends_on: [login]
  - id: settings
    title: Settings
    status: next
    order: 3
    size: 2
    depends_on:
      - login
      - profile
  - id: dark-mode
    title: 'Dark mode (it''s optional)'
    status: idea
    order: 4
```

### Refused forms

| Written | Why it is refused |
|---|---|
| `title:   abc` | more than one space after the `:` |
| `title: abc ` | a trailing space |
| `order: "3"` or `order: 03` | a quoted number, a leading zero |
| `title: 2.17.0 release` | a plain string must start with a letter: quote it |
| `title: A, B` | a plain string cannot contain a comma: quote it |
| `depends_on: [a, ]` | a trailing comma in a flow list |
| `depends_on:` (nothing after it) | YAML reads it as `null` |
| `status: done` twice in one item | a repeated key |
| `owner: me` | an unknown key |
| `title: &x Name` or `title: |` | an anchor, a block scalar |
| a line ending in a carriage return | CRLF files are refused |

## How `/milestone-plan` chooses

`/milestone-plan` with no argument runs `python3 scripts/workflow_backlog.py
next`, which picks:

1. a ready `in-progress` item first (the one with the lowest `order`): an item
   already started, by the Controller or by hand, is resumed, never skipped.
   *Ready* means every `depends_on` entry is `done`;
2. otherwise the ready `next` item with the lowest `order`. A `next` item that
   is waiting is skipped, not a block;
3. otherwise a refusal, `no_ready_item`, saying whether nothing is `next` or
   `in-progress`, or whether each such item waits (and for which ids).

An `in-progress` item that is not ready is never chosen; the output carries a
`warnings` line naming it and the unfinished ids. Other ready `in-progress`
items are listed as `also_in_progress`.

The backlog's choice is used even when `active_work_item_id` names a different
unfinished work item. When the two differ, the command prints one advisory line
naming both ids and the explicit form that continues the active one,
`/milestone-plan <active id>`, then proceeds with the backlog's choice.

The chosen item's `id` is the work item id. When the item has a `detail` page,
the command reads it as the milestone's description; without one it uses the
`title` alone and says so. The command never writes the file: it does not set
`in-progress`, so re-running it picks the same item until acceptance marks it
`done`. If that id already has a finished work item, the Workflow refuses it as
it does any reused id: mark the item `done` in the file.

## What acceptance does

Both acceptance paths (`/accept-milestone` and `/satisfy-gate acceptance`) have
a backlog branch when the repository declares one:

- **Pre-flight**, before anything is written:
  `python3 scripts/workflow_backlog.py preflight-accept <work-item-id>`. It
  checks the declaration, the file, that the id is an item whose status is
  `next`, `in-progress` or `done` (an `idea` is refused: promote it first),
  that a built-in-generated page can be replaced, that a declared generator's
  program resolves, and that the declared `file` and `roadmap` are not protected
  by the work item's artifact declaration (see [Paths](#paths)). It writes
  nothing.
- **Apply**, at the step that edits the roadmap today:
  `python3 scripts/workflow_backlog.py accept <work-item-id> [--release V]`. It
  sets `status: done` (and `release: V` when the milestone itself names a
  release) by editing only those lines, keeps every comment and every other
  byte, regenerates the page, and prints the two changed paths as JSON. The
  completion commit stages exactly those two paths. `release` is optional and
  usually added later by hand, followed by `generate`: the file is the source,
  so that is a normal edit.
- Accepting an item that is already `done` changes nothing in the file but
  regenerates the page, which repairs a stale one. A remediation child is
  exempt, as it was for the roadmap before.
- The edit is atomic. The new text is built in memory and re-parsed, and must
  equal the old data with only `status` (and `release`) changed. Before writing,
  `accept` snapshots the file and the page. If the generator or any later step
  fails, both are restored (a page that did not exist is removed).
- Afterwards the command reads `next` (read-only) and reports the next item in
  `docs/ACTIVE_MILESTONE.md`'s "Next action", or "no item is ready: <reason>".
- `/accept-milestone` itself requires a non-terminal `WORKFLOW_STATE.json`
  entry for the item (its step 0), so acceptance, with its backlog branch,
  is only offered for an item that has one.

### Recovery after an `accept` refusal

`accept` runs after the work item was already completed in the state file, so a
refusal leaves the item `MILESTONE_COMPLETE` with no completion commit, and
running the command again is refused (the item is finished). The recovery is one
named step:

1. Fix the cause (the generator, the file) and run
   `python3 scripts/workflow_backlog.py accept <work-item-id>` by hand. It is
   idempotent and reads no Workflow state, so it works on a finished item.
2. Continue the command that was running, from the step after `accept`, with the
   printed paths staged by its completion commit:
   - **`/accept-milestone`**: continue at step 4, then 5, 5a, 6 (the completion
     commit, trailer `Workflow-Work-Item: <id>`), 7 and 8;
   - **`/satisfy-gate acceptance`**: continue at its step 4, which runs
     `/accept-milestone` steps 2b and 3 to 8 (step 3 re-runs `accept`, an
     idempotent regeneration); the completion commit carries
     `Workflow-Gate-Satisfied-By: policy:<first 12 characters of the policy
     digest>` as well as `Workflow-Work-Item: <id>`; then its step 5 (record the
     floor in its own commit if the observed setting is stricter than
     `gate_policy_floor`) and step 6. The human-path steps are not a
     substitute: they make a commit without the policy trailer and record no
     floor.

Every refusal `accept` can print ends with this recovery.

## The roadmap page and its generator

**A declared `generator`, when present, produces the page for `generate` and
for acceptance; the built-in generator is used only when none is declared; the
two are never combined.**

### The built-in generator

Deterministic: LF, no timestamps. The page is a heading, the line
`<!-- Generated from <file> by scripts/workflow_backlog.py. ... -->`, and a table
sorted by `order` with the columns Order, Item (the title, a link when `detail`
is set, and the id in backticks), Status (`Idea`, `Next`, `In progress`, `Done`
or `Done (<release>)`), Size and Depends on (`-` when empty). Markdown-significant
characters in a title are escaped. It replaces an existing page only if the page
contains a line beginning `<!-- Generated from `; a hand-written page is refused
(`roadmap_not_generated`) unless you pass `generate --replace`. A page that does
not exist is created. `check` compares the page byte for byte with what
`generate` would write and reports the first differing line (`roadmap_stale`).

### A declared generator

The command runs with the argument vector as given, the repository root as its
working directory, no shell, a 120-second timeout, and the environment variable
`WORKFLOW_BACKLOG_SHA256` set to the SHA-256 (lower-case hex) of the backlog
file's bytes at that moment. It should write **only the page**; anything else it
writes is the repository's own business, and the Workflow neither detects nor
stages it.

**The freshness contract.** The generator writes the page with exactly one line
`<!-- backlog-sha256: <that hex> -->`, anywhere in it. After the command the
Workflow requires, in this order:

1. exit status 0;
2. the backlog file's bytes unchanged;
3. the `roadmap` path is a regular file (not a link, not absent);
4. the page has exactly one line of that form carrying the file's digest, and
   no line of the stamped form below (so a generator that did nothing to a page
   stamped earlier fails, and so does one that copied the old line forward).

Any failure is `generator_failed`, naming which of the four failed, and the file
and the page are restored. Then the Workflow **stamps** the page: it replaces
that one line, in place and atomically, with
`<!-- backlog-sha256: <file hex> page-sha256: <page hex> -->`, where
`<page hex>` is the SHA-256 of the page's bytes with that one line (its line
terminator included) removed. Lines are split on LF alone; a final line without
an LF is a line; a line ending in CR LF is not of the generator form and fails
closed. Every other byte is the generator's.

`check` with a declared generator runs **no user code**. It verifies from the
stamp that the page is a regular file with exactly one stamped line, whose file
digest equals the digest of the file now **and** whose page digest equals the
digest of the page with that line removed. So a page edited by hand fails
`check` (its page digest changed), and a generator must rewrite the page on every
run. Recomputing the stamp by hand is writing the generator, not editing a page.

A short example generator, saved as `tools/roadmap.py` and declared as
`"generator": ["python3", "tools/roadmap.py"]`:

<!-- backlog-example:generator -->
```python
import os
import pathlib
import re

digest = os.environ["WORKFLOW_BACKLOG_SHA256"]
text = pathlib.Path("docs/backlog.yaml").read_text(encoding="utf-8")
titles = re.findall(r"^    title: (.*)$", text, re.M)
lines = ["# Roadmap", "", f"<!-- backlog-sha256: {digest} -->", ""]
lines += [f"- {title}" for title in titles]
pathlib.Path("docs/ROADMAP.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
```

## Paths

- A path (`file`, `roadmap`, a `detail`) is repository-relative with `/`
  separators: no leading `/`, no backslash, no empty, `.` or `..` segment, no
  control character, not under `.git/` or `.ai-review/`. `file` and `roadmap`
  must differ.
- **No component of the path, the last included, may be a symbolic link**, and
  every existing component must match its directory entry's exact name (so a
  declared `Scripts/backlog.yaml` cannot pass on a case-insensitive filesystem
  while the write lands in `scripts/`). A violation is `declaration_invalid`,
  naming the component. The declared path is therefore the path Git sees.
- **The path rule.** The declared `file` and `roadmap` must not be inside an
  area the work item's artifact declaration protects, such as `config/` or
  `scripts/` (and `.claude/commands/` for a process item): acceptance edits them
  after the work item's content was approved, and a protected path would turn
  that edit into a stale approval. The refusal is `declaration_conflict`; move
  the path out of the protected area. `docs/backlog.yaml` and `docs/ROADMAP.md`
  are never protected.
- Item pages live under `docs/items/`, which the artifact template classifies as
  excluded content. The template classifies the declared `file`, the `roadmap`
  and `docs/items/` for every declaration it generates in a repository that
  declares a backlog, at both the plan and the implementation stage. A
  repository with no declaration gets exactly what it got before.

## Items in flight at adoption

A work item whose `docs/ai-workflow/registry/<id>-artifacts.json` was written
before the repository declared its backlog (or before this release) does not
classify the backlog paths, so acceptance's edit of the file, or yours, would
raise `UnclassifiedPathError`. Two repairs may be needed:

- **The item is not in the file** (`item_unknown`): add an entry whose `id` is
  the work item id, with status `in-progress`.
- **The declaration does not classify the paths**: add the declared `file`, the
  declared `roadmap` and the prefix `docs/items/` as **excluded** entries to
  both `plan_stage` and `implementation_stage` of that item's
  `docs/ai-workflow/registry/<id>-artifacts.json`. **This edit changes the
  declaration's bytes and stales the item's existing approvals.** Follow
  "Repairing an artifact declaration after an approval" in
  [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md#repairing-an-artifact-declaration-after-an-approval):
  before the plan approval it costs nothing; after it, a new plan revision
  through both review stages and `/approve-review plan`; after the technical
  approval, a `post-fix` round and `/approve-review implementation`. Never widen
  an exclusion to hide a path. If the paths are protected (the
  `declaration_conflict` case), the repair is to move the path, not to exclude
  it.

## Commands and exit codes

| Command | What it does |
|---|---|
| `status` | reports whether a backlog is declared, whether the file is valid, item counts, and whether the page is fresh. Always exits 0. |
| `next` | the choice of `/milestone-plan` (JSON). Read-only. |
| `check` | the file is valid and the page is fresh. For CI. |
| `preflight-accept <id>` | the pre-flight of acceptance. Writes nothing. |
| `accept <id> [--release V]` | sets the item `done` and regenerates the page. |
| `generate [--replace]` | regenerates the page. `--replace` allows replacing a hand-written page once. |

`--repo-root DIR` before the subcommand names another repository root; by
default the root is the git top level of the working directory, else the working
directory. Success
prints one JSON object on standard output and exits 0; a repository that
declares nothing prints `{"declared": false}` and exits 0. A refusal prints, on
standard error, an `error: <code>: <message>` section (one line, or a located
diagnostic per violation when several are found) followed by
`next step: <one step>`, and exits
2 (so does a usage error). `check` on a stale page exits 1.

| Code | When | The one next step |
|---|---|---|
| `declaration_invalid` | the `backlog` key breaks the rules in [Setup](#setup) or [Paths](#paths) | correct the key, or remove it to leave the backlog |
| `backlog_missing` | the declared file does not exist | create the file (the starter is in [Setup](#setup)), or remove the key |
| `backlog_invalid` | the grammar or the schema is broken | fix the lines named as this document says, then run `check` |
| `no_ready_item` | nothing is ready to plan | set an item to `next`, or finish what it waits for, then run `/milestone-plan` again |
| `item_unknown` | acceptance names an id that is not an item | add an item with that id and status `in-progress`; see [Items in flight at adoption](#items-in-flight-at-adoption) |
| `status_not_acceptable` | acceptance of an `idea` item | set the item to `next`, then accept |
| `roadmap_not_generated` | the page exists and was not generated, with the built-in generator | run `generate --replace` once, commit, retry |
| `generator_failed` | the declared command failed, timed out, changed the file, or left no regular page with exactly one current `backlog-sha256` line | fix the generator (the file and page are restored), run `accept <id>`, and continue at step 4 ([recovery](#recovery-after-an-accept-refusal)) |
| `roadmap_stale` | `check`, exit 1: the page does not match the file | run `generate` and commit the page |
| `declaration_conflict` | the declared `file` or `roadmap` is protected by the work item's artifact declaration | move the path out of the protected area in the `backlog` key, then retry |

## Moving back to an earlier release

An earlier release (2.16.0) ignores the `backlog` key. `/milestone-plan` then reads
the generated page as if it were prose, and acceptance **hand-edits the
generated page** (when `roadmap` is the default `docs/ROADMAP.md`), after which
`check` fails in your CI and the file still says `next`. With a non-default
`roadmap` nothing fails: the earlier release edits or creates `docs/ROADMAP.md`,
the generated page is untouched, `check` still passes, and the backlog silently
stops advancing. Nothing in `WORKFLOW_STATE.json`, the ledgers or the commit
shapes changes in either direction.

The copied script's built-in page header and next-step lines still name
`scripts/workflow_backlog.py` after you move it; that is expected, and they must
not be hand-edited, because `check` compares the page byte for byte.

Before updating down, finish or accept the items in flight. Afterwards choose
one:

1. **Keep the backlog.** Copy `scripts/workflow_backlog.py` out first, to
   `tools/workflow_backlog.py` (or any path outside `scripts/`, `.claude/`,
   `docs/ai-workflow/` and `.workflow-manager/`, which the earlier release
   removes or manages; it imports nothing else). Set the status in the file by
   hand and run the copy's `generate` yourself at each acceptance. This holds
   with a declared generator too: `generate` runs it and stamps the page, so
   never run the generator directly, which leaves the marker unstamped and fails
   `check`. Point the CI step at the copy:
   `python3 tools/workflow_backlog.py check`.
2. **Leave the backlog.** Delete the `backlog` key and the CI check, and treat
   `docs/ROADMAP.md` as an ordinary hand-maintained page again (it is a plain
   Markdown table).
