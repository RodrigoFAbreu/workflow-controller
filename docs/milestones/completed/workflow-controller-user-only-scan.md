# TK1: the user-only scan sees a command name wrapped in punctuation (Revision 2)

Work item: `workflow-controller-user-only-scan`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `d10fc049a3e14e16dacea99b2d2cdb41b3fafcbf` ("docs: add TK1 and NF3 before H4b (#247, #244) (#248)"), the tip of `main`.
Lifecycle authority: this repository's installed Workflow, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row TK1 (issue [#247](https://github.com/RodrigoFAbreu/workflow-controller/issues/247)), before NF3 and H4b.
Released baseline: `workflow-controller 1.27.0`. The change closes a safety gap in the shipped Claude worker launch, so the squash commit is a `fix:` (a patch release): an operator who gives a worker a task that names a user-only command in one of the missed spellings was not refused before and is refused now. No setting, option, exit code, gate, route or record changes.
Pull request title: `fix: refuse a user-only command named with wrapping punctuation (TK1, #247)`

## Goal

Make the scan that keeps a model from running a user-only command named inside a task or argument string (`worker._assert_not_user_only`, which `worker.launch` runs before any process starts, and the argument scan in `controller/command_files.py`, which reuses the same tokeniser) refuse a command name in every ordinary spelling, without refusing a task that names no user-only command.

## What exists

- `controller/worker.py`: `_task_tokens(task)` splits a task on whitespace, strips leading `` ` `` and `/` characters (`_LEADING_MARKERS`) and trailing `` ` ' " , . ; : ) ] } `` characters (`_TRAILING_PUNCTUATION`) from each chunk, and returns the chunks. `_assert_not_user_only(task)` raises `UserOnlyCommandError` when a token equals one of the four literal names in `USER_ONLY_COMMANDS` (whole-token equality, case-sensitive). `launch` calls it on the task.
- `controller/command_files.py` (H4a): `render_command` compares each token of the argument string with the eleven names it derives from the real command files, and also calls `worker._assert_not_user_only(f"/{name} {arguments}")`. It has no tokeniser of its own.
- Measured over the real tokeniser (base commit), the name `approve-review` in each spelling:

| spelling | token the scan sees | refused today |
|---|---|---|
| bare, `/name`, `` `/name` ``, `/name,` `/name.` `/name;` `/name:` `/name)` `/name...`, `/name` then a newline | `name` | yes |
| `/name!`, `/name?`, `/name>`, `/name/`, `/name&`, `/name\`, `/name` followed by an em dash | `name!`, `name?`, ... | **no** |
| `(/name)`, `"/name"`, `'/name'`, `[/name]`, `{/name}`, `(`` `/name` ``)`, a curly-quoted `/name` | `(/name`, `"/name`, ... | **no** |
| `--x=/name`, `x=/name`, `/name=x` | `--x=/name`, ... | **no** |
| `<name>`, `<name/>`, `*/name*`, `**/name**`, `_/name_`, `!/name`, `?/name`, `-/name`, `\|/name\|`, `@/name`, `#/name`, `$/name` | wrapped | **no** |
| `run /name!` | `run`, `name!` | **no** |
| `foo/name-bar`, `docs/ai-workflow/name.md` | unchanged | no (and must stay unrefused) |
| `./name`, `../name`, `../../name`, `a/name`, `./a/name` | unchanged | no (and must stay unrefused) |

Nothing outside `worker.py` and `command_files.py` uses the two punctuation constants; `_task_tokens` is used by `command_files.py`, by `tests/test_lifecycle_orchestration.py` (a set intersection) and by `_assert_not_user_only`.

## Decisions

Each decision states one principle.

- **D1. A mention is delimited by anything that cannot be part of a name or a path, and by `=`.** Each whitespace chunk is split into pieces on `=` and on every run of characters that is not a letter, digit, `_`, `-`, `.` or `/` (so quotes, brackets, angle brackets, `!`, `?`, `,`, `;`, `:`, `|`, `&`, `*`, `` ` ``, `#`, `@`, `$`, `\`, a dash or quote outside ASCII and the like all delimit). The principle is that a name stays one piece with the characters a path or a file name keeps, and a character that only decorates a word does not hide it.
- **D2. A piece loses every trailing character that is not a letter or digit, and every leading character that is neither a letter, a digit nor `.`.** That removes `/`, `-` and `_` at the front (`/name`, `_name_`, `--name`) and `/`, `.`, `-` and `_` at the end (`name.`, `name...`, `name/`). A leading `.` stops the leading strip, so a relative-path prefix (`./name`, `../name`, `../../name`) keeps its dots and its slash and the piece stays a path, as the baseline judges it (review round 1, I1). A piece is kept only when something remains. The result is the token list `_task_tokens` returns; empty strings are no longer in it (the callers only test membership).
- **D3. A name inside a longer word or path is judged by the rule the existing tests set, unchanged: not refused.** Because `/`, `.`, `-` and `_` do not delimit (D1) and are stripped only at the edges (D2), `foo/approve-review-bar`, `docs/ai-workflow/approve-review.md`, `approve-review.md`, `xapprove-review`, `approve-reviewer`, `./approve-review`, `../approve-review` and `../../approve-review` stay single pieces that are not a name. A bare name, a `/name`, and a name behind `=` are refused, because the scan has always refused the bare name and the issue asks for `--x=/name`. This is the only boundary rule; the plan does not widen it to path segments (a path that ends in the name's segment, such as `a/approve-review` or `./a/approve-review`, stays unrefused as today). One path form is refused: `~` is a delimiter (D1), so `~/approve-review` leaves the piece `/approve-review`, which is a refused spelling. That is the fail-closed direction and is written down here and in the guide text rather than left accidental; CP1 tests it.
- **D4. The fix is on the token side, in one place.** `_task_tokens` is rewritten and `_LEADING_MARKERS` and `_TRAILING_PUNCTUATION` are removed (nothing else uses them). `_assert_not_user_only` and `command_files.render_command` are not edited: both get the fix through `_task_tokens`, which is the shared mechanism H4a chose on purpose. The refusal text, the evidence keys and `USER_ONLY_COMMANDS` do not change.
- **D5. The set of names does not change.** The Claude launch scan keeps its four literal names and the renderer keeps its eleven derived names; this item fixes how a name is found in a string, not which commands are user-only. The roadmap's later items own the set.
- **D6. A task that names no user-only command is judged exactly as before.** A task is refused if and only if some token equals a user-only name. The new tokeniser can only produce a name where the old one left punctuation on it, or where `=` or a delimiter hid it, so a task is newly refused only when it names a user-only command in a spelling the old scan missed. A test runs a corpus of ordinary tasks (the shipped commands' own argument hints, task strings the lifecycle tests use, and words containing the names) through both the old rule (kept in the test as the reference) and the new one, and requires the same verdict on every entry, with no filter. The corpus is a fixed list of strings that name no user-only command at all (a filter chosen with the new tokeniser would prove nothing, review round 1, OPT-2), and it includes the relative-path forms of D3, the task strings the Controller builds itself (`job.worker_task` output for each rendered action command, `functional_review.sandbox_limits_note()` and `evidence.pending_review_stage_write_addendum(...)`), which are what `worker.launch` scans in production. The spellings that are newly refused are tested separately, as refusals.
- **D7. Release type.** `fix:`. It closes a gap in the shipped Claude launch and changes the behaviour of the guard only by refusing more strings that name a user-only command; an operator-visible new setting, option or exit code would make it a `feat:`, and there is none.

## Checkpoints

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The tokeniser and its tests | - | 2 | 1 |
| CP2 | The guide text, the roadmap and the next action | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1. The tokeniser and its tests

**Files:** `controller/worker.py`, `tests/test_worker.py`, `tests/test_command_files.py`

D1 to D6. `_task_tokens` splits each whitespace chunk on `=` and on every run of characters outside `\w`, `.`, `/` and `-`, strips each piece's trailing characters that are not a letter or digit and its leading characters that are neither a letter, a digit nor `.`, and keeps the non-empty pieces. `_LEADING_MARKERS` and `_TRAILING_PUNCTUATION` are removed; their comments move into `_task_tokens`' docstring. Nothing else in `controller/` changes.

Done when: `tests/test_worker.py` shows, for every name in `USER_ONLY_COMMANDS`, a refusal by `_assert_not_user_only` and by `launch` (no process started) in each spelling of the table above (one subtest per spelling), the longer-word and path forms unrefused (including `./name`, `../name`, `../../name` and `a/name`, for the Claude names and, in `tests/test_command_files.py`, for the renderer's), `~/name` refused, the D6 fixed corpus (with the Controller-built task strings) agreeing with the reference rule on every entry, and the intended punctuation spellings still refused; `tests/test_command_files.py` shows, for every name `_user_only_names` derives from this repository's `.claude/commands/` (eleven), a refusal by `render_command` in the same spellings, in the argument string and as a spelling the renderer's own scan alone catches; the existing tests pass unchanged.
<!-- /CP1 -->

<!-- CP2 -->
### CP2. The guide text, the roadmap and the next action

**Files:** `docs/guide/workers.md`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

In place, no stale narrative. `docs/guide/workers.md` states the delimiter rule (what delimits a mention, what a longer word or path keeps, including a relative path with a dot prefix, and that `~/name` is refused; that the set of names is unchanged) where it describes the scan for a backticked or sentence-final mention; `docs/ROADMAP.md` marks TK1 done and leaves NF3 then H4b next; `docs/ACTIVE_MILESTONE.md` records the item, its functional-review checklist and the next action.

Done when `python3 -B tools/check_docs.py` passes.
<!-- /CP2 -->

## Requirements

| id | requirement | checkpoints |
|---|---|---|
| R1 | A user-only command name is found in every spelling the issue lists (a trailing ! or ?, brackets, quotes, an = prefix, the other wrapping characters), by the one shared tokeniser, in both the Claude launch scan and the Codex renderer (#247) | CP1 |
| R2 | A command name inside a longer word or path, and every task that names no user-only command, is judged exactly as before; both directions are tested, and every user-only command of the real command directory is refused in every spelling (#247) | CP1 |
| R3 | No new setting, option, exit code or gate; the set of names, the refusal text and the evidence keep their shape; the guide and the roadmap state the rule in place (#247) | CP1, CP2 |

## Functional review

Every flow is offline and unit-level (no real Claude, Codex, network or `gh`). Run from the product clone with `TMPDIR` set to a writable scratch directory, one module at a time, `python3 -B` and no `PYTHONPATH=.`. The tester's sandbox has a read-only `/tmp`: a flow that runs `tests.test_worker` or `tests.test_command_files` creates folders there, so on that sandbox these flows are `environment`-limited and operator-run; the plan names them in the checklist. The full suite is not a flow.

## Risks

- **A false positive blocks a legitimate task.** A task that contains a user-only name as its own whitespace-or-delimiter-separated piece (`--approve-review`, `foo=approve-review`) is now refused where it was not. That is the fail-closed direction, the issue asks for it, and D3 keeps the longer-word and path forms unrefused; the corpus test in D6 shows no other change.
- **Unicode delimiters.** D1 treats anything outside `\w`, `.`, `/` and `-` as a delimiter, so a name next to a curly quote or an em dash is found. A letter outside ASCII counts as part of a word, so `éapprove-review` is not refused.
- **A mention that is not whitespace- or punctuation-separated at all** (a name glued to letters) is still not seen. That is the existing whole-token rule and not a gap this item closes.
- **Out of scope:** the set of user-only names (D5), the nested-invocation limit the H4a guide states, NF3, H4b (which wires the renderer into a launch and relies on this fix) and every other launch behaviour.
