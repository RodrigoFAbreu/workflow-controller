# H7: a harness adapter declares how its build is verified (Revision 1)

Work item: `workflow-controller-harness-verification`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `f4d34b17a7631d322ee13f2f01c39a508c552b36` ("docs: link the H7 issue from the roadmap row and the next action (#183)"), the tip of `main`, passed explicitly as `/milestone-plan f4d34b1…`.
Lifecycle authority: this repository's installed Workflow **2.21.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H7, the next item of the harness-neutral roles epic (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#182](https://github.com/RodrigoFAbreu/workflow-controller/issues/182).
Released baseline: `workflow-controller 1.22.2`. The change is a declaration and documentation with no behaviour change, so the squash commit is a `chore:` change and releases nothing (see D5).
Pull request title: `chore: a harness adapter declares how its build is verified (H7, #182)`

## Goal

Two harnesses are registered, and each one decides in its own code whether a build may launch: Claude Code by the digest that `verify-claude` records (C10e), Codex by the fixed feature, layer, state and flag checks of the review launch (RB3). The registry says none of this. It carries `pinned_version` and `fails_closed_on_unknown_version`, and the generated page shows them as "Pinned version" and "Unknown version refused", which reads as if each harness is tied to one version string, when nothing in `controller/` compares a version at launch.

This milestone makes the adapter say how a build of its harness is verified, and relabels the version it records as what it is: the build the captures were taken from, for information.

One concern only: **the adapter's declaration of its build verification, and what the generated table and the guides say about it.** Out of scope: `verify-codex` and verify-on-first-use for Codex (C10g-2), the launch seam (H3), a Codex worker or tester, any version range or build-digest cache for Codex, and changing which builds are accepted.

## Decisions

Each decision states one principle.

- **D1. An adapter names its verification command, or says it has none.** `HarnessAdapter` gains one field, `build_verification: str | None`: the name of the Controller command that admits a build of that harness where the Controller checks one, and it covers only the launches that check. For `claude-code` that is the functional-tester launch alone (`controller/review.py` `launch_functional` reads the digests `verify-claude` records; the worker and reviewer launches run any build on `PATH`); for `codex` it is none yet. The declaration states this scope in its comment, and the table and the guides repeat it (D3, D6). `claude-code` declares `"verify-claude"`; `codex` declares `None`, and the declaration's comment says that Codex keeps its fail-closed launch checks until `verify-codex` ships (C10g-2), which then changes this one value. The field is a plain declaration: `controller/harness.py` still imports only `controller.errors`. Nothing reads it at launch.
- **D2. A declared command must exist.** A test pins every non-`None` `build_verification` to a key of `cli._DISPATCH` (the Controller's command table), and pins `claude-code`'s to the command `controller/functional_review.py`'s refusal names, so the table cannot name a command that is not there, and a rename is caught.
- **D3. The recorded version is informational.** The field `pinned_version` keeps its name and its value (the pin-bump procedure, the `review.PINNED_VERSION` and `review.CLAUDE_PINNED_VERSION` pins and every test that reads it stay as they are), and gains a comment saying it names the build the harness's captures were taken from and is not compared at launch. The generated first table renames its column "Pinned version" to "Captured with" and adds a column "Build verification" right after it, showing `verify-claude` in code form or `none: launch checks only`. The column "Unknown version refused" and the field behind it are unchanged. The column header stays "Build verification" and the cell stays the bare command, so the generated page never claims more than the declaration; the scope (which launch the command admits) is stated in the guide prose beside the table (D6), and a test pins it (CP1).
- **D4. No launch behaviour changes.** `controller/review.py`, `controller/claude_build.py`, `controller/functional_review.py`, `controller/worker.py` and `controller/cli.py` are not edited. The Claude build check and the `verify-claude` cache, the Codex `check_features`, `DISABLED_FEATURES` and `TOOL_ALLOWLIST` checks and every refusal stay exactly as they are, and a test run shows the modules that exercise them unchanged and green.
- **D5. A declaration plus documentation releases nothing.** The change adds a registry field and relabels a generated column; no setting, exit code, refusal, command or gate behaves differently for an operator, and the package gains no capability. The squash commit is `chore:`, which `.workflow-controller/policy.json` maps to no release, so the roadmap row records the work as done with no version. `refactor:` was rejected because the policy releases a patch for it, and a patch release for a relabelled documentation column is noise.
- **D6. The guides say what the declaration means.** `docs/guide/harnesses.md` explains the two new column names in the prose that already explains the version columns, `docs/guide/workers.md` shows the same two columns, and `docs/guide/development.md` tells whoever adds a harness to declare its verification command (or `None`) and says a pin bump changes only the informational version.

## Out of scope

Everything listed under Goal, plus a version-range check, a build-digest cache for Codex, and a uniform `verify` command across harnesses.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The declaration, the generated columns and the tests | - | 2 | 1 |
| CP2 | The guides and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The declaration, the generated columns and the tests

**Files:** `controller/harness.py`, `tools/harness_table.py`, `tests/test_harness.py`, `tests/test_harness_table.py`, `docs/guide/harnesses.md`

D1, D2, D3 and D4. `controller/harness.py`: the `build_verification` field after `fails_closed_on_unknown_version`, its declaration on both adapters with a comment that names the launch the Claude command admits (the functional tester) and the Codex comment, and the comment on `pinned_version`. `tools/harness_table.py`: the renamed column and the new one in `render()`, with the wording of the `None` case kept as one constant next to the other wording tables. `docs/guide/harnesses.md`: only the generated region, rewritten with `python3 tools/harness_table.py --write` (the prose is CP2).

Tests, in `tests/test_harness.py` and `tests/test_harness_table.py`:
- both registered adapters declare `build_verification`: `claude-code` is `"verify-claude"` and `codex` is `None`; the existing declared-facts tests are extended with the field;
- every non-`None` value is a key of `cli._DISPATCH`, and the Claude value equals the command named in the refusal text of `functional_review` (the test calls `functional_review.identity_check` with an `is_native` that refuses and reads the raised text, not a copy);
- the first generated table has the columns "Captured with" and "Build verification" in that order after "Usage", the Claude row shows `` `verify-claude` `` and the Codex row shows `none: launch checks only`, and a patched registry whose Codex adapter declares `"verify-codex"` changes exactly that cell;
- the field is not read by any module under `controller/` other than `controller/harness.py` (a source scan test in the style of the existing harness pin tests), so a later change that starts reading it is a deliberate one;
- the scope is pinned: the only module under `controller/` that reads the recorded Claude digests (`claude_build.recorded_digests`) outside `controller/claude_build.py` and `controller/cli.py` is `controller/review.py`, and only inside `launch_functional`, so the statement "`verify-claude` admits the functional-tester launch only" in the comment and guides holds, and a later change that gates the worker or reviewer launch has to update the declaration deliberately;
- these tests fail against the base commit (checked once locally by stashing the source edits).

Done when: `python3 -m unittest tests.test_harness tests.test_harness_table` is green; `python3 tools/harness_table.py --check` exits 0; the modules of D4 show no diff; the full suite is green.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guides and the roadmap

**Files:** `docs/guide/harnesses.md`, `docs/guide/workers.md`, `docs/guide/development.md`, `docs/ROADMAP.md`

D5 and D6. `docs/guide/harnesses.md`: the paragraph on the version columns says that "Captured with" is the build the captures were taken from and is not compared at launch, that "Build verification" names the command that admits a build where the Controller checks one (`verify-claude` for Claude Code, and the guide says plainly that this admits the functional-tester launch only, while the worker and reviewer launches run any build on `PATH`; none for Codex, whose launch is refused by the checks of its surfaces until `verify-codex` ships), and that "Unknown version refused" keeps its meaning. `docs/guide/workers.md`: the harness table and the paragraph under it use the same two column names and say the same scope for `verify-claude`. `docs/guide/development.md`: step 1 of adding a harness names the verification command (or `None`) among the declarations, and the sentence on a pin bump says it changes the informational version only. `docs/ROADMAP.md`: row H7 is marked done in the form of the H6 and H8 rows (what the adapters declare, that nothing at launch changed, the `chore:` with no release), the epic's order names H2 as the next item, and the "Next, in order" list drops H7. `docs/ACTIVE_MILESTONE.md` is written by the Workflow's own close-out, not by hand.

Done when: `python3 tools/check_docs.py` and `python3 tools/harness_table.py --check` exit 0; the full suite is green; every point of the roadmap row is changed or named as already covered.
<!-- /CP2 -->
