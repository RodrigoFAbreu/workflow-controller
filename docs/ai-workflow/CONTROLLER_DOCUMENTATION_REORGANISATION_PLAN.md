# Controller documentation reorganisation (Revision 5)

Work item: `workflow-controller-documentation-reorganisation`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `914d8f4e0edea9c713c4d1ff8900c5f8ae146b0e` ("docs: D1 adds cross-repository links, readability pages and two documentation checks (#24)"), the tip of `main`, passed explicitly as `/milestone-plan 914d8f4…`.
Lifecycle authority: this repository's installed Workflow **2.6.0**. Driving Controller: the shared pipx install (1.5.0).
Roadmap slot: `docs/ROADMAP.md` step **D1** of "At a glance", section **11.8**. It runs while the lane waits for W2; C8 follows.
Released baseline preserved: `workflow-controller 1.7.0`. No Controller behaviour changes.
Pull request title: `docs: reorganise the guides, add task pages and two offline documentation checks`
(`docs` releases nothing, `.workflow-controller/policy.json` maps it to `none`).

## Goal

Section 11.8 is the specification; this plan adds only the decisions it leaves open. A reader who wants to install, run or update the Controller finds one short page for each, every page says who it is for and what it was last checked against, and two offline checks keep links and shown commands true.

Deliverables (all under `docs/` unless noted; "user pages" are the ones marked **U**):

| Page | Purpose |
|---|---|
| `README.md` **U** | what the Controller is; the shared "how the pieces fit" paragraph; five-minute quick start; links to every page below |
| `docs/README.md` **U** | documentation map (reader goal -> page), kept short; carries the user-page header |
| `docs/install.md` **U** | shared install, verifying a release wheel, the settings file, uninstall |
| `docs/run.md` **U** | a first run, `step` versus `run`, following, the usage pause, approval gates |
| `docs/update.md` **U** | upgrading and rolling back the Controller; moving a repository to a newer Workflow |
| `docs/compatibility.md` **U** | single source of truth: which Controller admitted which Workflow |
| `docs/common-problems.md` **U** | the frequent stops, one-line fix each, linking into `docs/guide/troubleshooting.md` |
| `docs/exit-codes.md` **U** | the one exit-code table |
| `docs/glossary.md` **U** | work item, phase, checkpoint, approval gate, bundle, binding, protocol |
| `docs/release-history.md` **U** | one plain-words line per release, linking to `docs/releases/` |
| `docs/guide/*.md` **U** | reorganised reference guides (sections below) |
| `tools/check_docs.py`, `tests/test_docs.py` | the two documentation checks (the only code) |

## Non-goals

- No Controller behaviour, CLI, settings or release change. Generating reference pages from code.
- No edit to the Workflow's shipped documents (`docs/ai-workflow/*` managed files), ADR bodies, milestone narratives or release notes under `docs/releases/`, not even to repoint a link. They are linked, not rewritten; links into them from a retired guide are kept valid by the stub in D-2.
- No change to the Workflow Manager or Workflow repositories; this repository goes first and the others mirror its names.
- Plans and completed-milestone narratives stay historical (`CLAUDE.md`).

## Investigation (measured at `914d8f4`)

- Eleven guides, 131 to 663 lines (`wc -l`); `README.md` 184 lines; `docs/README.md` 113.
- The exit-code table exists three times: ADR 0001 `## Exit codes` (normative, parsed by `tests/test_plan_document_consistency.py` against the CLI's `EXIT_*` constants), `docs/guide/troubleshooting.md:11-30` (full) and `README.md:151-166` (five rows).
- Workflow compatibility is spread over `concepts.md:73-90`, `installation.md:31-69` and `:146-215`, `troubleshooting.md:220-498`, `development.md:169-285`, and `milestone-branches.md`.
- The glossary is `concepts.md:69` and its terms are re-explained in other guides.
- `tests/test_plan_document_consistency.py` already parses every `workflow-controller ...` line in `README.md` and each `docs/guide/*.md` (glob, so renames are safe) against the live parser: README needs at least five invocation lines and one `follow` line, the guides at least ten together; README must name `plan`, `tests`, `tests-result` and `package` as code spans (`validate.yml` job ids). These constraints are kept.
- Outside `docs/`, only `CLAUDE.md:42-47` names guide paths (`docs/README.md`, `docs/guide/`, `docs/guide/milestone-branches.md`); nothing in `controller/`, `tools/`, `.claude/commands/` or `.github/` does. Inside `docs/`, the links the new link check will scope in and that this milestone may not edit are six links to `docs/guide/installation.md`: `docs/adr/0010-orchestration-protocol-admission-by-capability.md:18` (the file), `docs/adr/0006-workflow-release-admission-and-per-release-contracts.md:291` (`#moving-a-target-to-another-workflow-release`), `docs/releases/1.3.0.md:22` (`#supported-workflow-releases`), `:134` (`#upgrade`), `:136` (`#moving-a-target-to-another-workflow-release`) and `docs/releases/1.4.0.md:109` (`#upgrade`). Nothing under `docs/adr/` or `docs/releases/` links to `concepts.md`. The rest are guide-to-guide and README links that CP4/CP5 rewrite.
- Lifecycle diagram: `docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg` is the Workflow's shipped lifecycle diagram. Until the Workflow repository's overview exists, pages link to it (a relative link).
- Sibling repositories (remotes): `RodrigoFAbreu/workflow-manager`, `RodrigoFAbreu/workflow`, and this one `RodrigoFAbreu/workflow-controller`.
- `validate.yml`'s `tests` job runs every module `unittest discover tests` loads, sharded by the adaptive planner (ADR 0005); a new `tests/test_docs.py` is picked up with no workflow change, so the checks run inside the existing validate job in seconds and `.github/workflows/*` stay byte-identical (the rendered files equal `tools/ci_workflows.py`).

## Invariants

- **I1** No file under `controller/`, `.github/`, `.workflow-controller/`, `pyproject.toml`, `setup.py`, `docs/adr/` or `docs/releases/` changes (ADR 0001 keeps its exit table, as the existing test requires). The full existing suite passes unchanged.
- **I2** No link is left broken: every moved guide leaves no inbound reference except the three-heading `installation.md` stub of D-2, which exists for the links in `docs/adr/` and `docs/releases/` that I1 forbids editing (`CLAUDE.md` is updated; the check proves the rest). A renamed guide is renamed in the same checkpoint that rewrites its inbound links.
- **I3** Facts are carried over from the current guides and the code, never invented. Every command on a task page is run by the implementer against a throwaway repository or `--help`; behaviour the code does not have is not documented. History stays in narratives, ADRs and release notes.
- **I4** User pages carry no internal ids (checkpoint numbers such as `CP3`, finding ids such as `R2-001`, milestone work-item ids). A check enforces it.
- **I5** Offline: the checks never touch the network and need only the standard library.

## Design

### A. Layout and naming (decision D-1)

Task pages sit at `docs/install.md`, `docs/run.md`, `docs/update.md` (names shared with the Workflow Manager lane). Reference and design detail stays in `docs/guide/`, reorganised, never duplicated on a task page: a task page states the steps and links to the guide for why.
Every task page has, in order: a header line (below), the goal in one line, prerequisites, numbered steps with copy-paste commands, "What you should see", "If it fails" (linking to `common-problems.md`).
Header line, on every **U** page, first non-blank line after the H1 title: `> For: <reader>. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.` (reader per page). The compatibility page lists the versions it covers in the same form.
A term is linked to `docs/glossary.md#<anchor>` at its first use on a page, not re-explained. Approval gates are named "approval gates" (plan approval, implementation approval, milestone acceptance); a sentence notes that from Workflow 2.8 a gate policy can make them automatic, without claiming that Controller 1.7.0 has changed.

### B. What happens to each existing guide (decision D-2)

| Guide | Fate |
|---|---|
| `concepts.md` | keeps "Three pieces" and "A milestone end to end", renamed `docs/guide/how-it-works.md`; its Glossary moves to `docs/glossary.md`; the link inventory is updated |
| `installation.md` | content moved: install and uninstall -> `install.md`; upgrade, roll back, move a target -> `update.md`; supported releases -> `compatibility.md`; install from a checkout -> `development.md`. The file stays as a **stub** of exactly three headings, `## Supported Workflow releases`, `## Upgrade` and `## Moving a target to another Workflow release`, each one line pointing at `compatibility.md` or `update.md`, plus a one-line title note that the page was split; nothing else. It exists only for the six inbound links of the Investigation, which I1 forbids editing. No user page, README or guide links to it |
| `commands.md` | stays the command reference; exit text replaced by a link to `exit-codes.md`; settings detail stays in `runtime.md` |
| `runtime.md` | stays; the settings file is described once here and linked from `install.md`, `run.md`, `troubleshooting.md` |
| `automation.md`, `workers.md` | stay as design/behaviour reference; per-release history sentences cut; exit text -> link |
| `milestone-branches.md` | stays; the untitled 147-line preamble gets a title and short intro; history cut |
| `ci-and-releases.md`, `development.md` | stay (maintainer pages); `development.md` gains "Documentation checks" and "install from a checkout" |
| `troubleshooting.md` | stays as the detail; its exit table is replaced by a link; `common-problems.md` is the short front door |

A guide whose heading anchors other pages use keeps those headings (the checks catch the rest). The reduction of `installation.md` to a stub and the rename of `concepts.md` are the only path changes (decision D-2 for the user). The stub is not a **U** page and carries no header; it is on the link check's page list and `tests/test_docs.py` asserts its heading set is exactly the three above.

### C. Reference pages

- **`exit-codes.md`**: one table of every exit status (0, 2, 10, 15, 16, 20, 30, 35, 40, 45, 50, and the shell's 130 for Ctrl-C), each with a plain meaning and a one-line "what to do", derived from ADR 0001 and `controller/cli.py`. ADR 0001's table stays normative; `tests/test_docs.py` asserts the two tables carry the same codes and the page's codes equal the `EXIT_*` values in `controller/cli.py` plus `SIGINT_EXIT_STATUS` (130, `controller/cli.py:67`), the shell's status for Ctrl-C. The ADR comparison ignores 130, which ADR 0001's table does not carry; the page's codes minus 130 equal the ADR's. Every other page that names a status links here.
- **`compatibility.md`**: a table of Controller release -> admitted Workflow releases and how (exact release with pinned script digests, or protocol capability). Rows: 1.2.1 and earlier (2.5.1); 1.3.0 to 1.6.x (2.5.1, 2.6.0, exact release, digests taken from `controller/workflow_contract.py` `RELEASE_CONTRACTS`); 1.7.0 and later (protocol major 1: any Workflow whose `describe` answers it, 2.7.0 first; 2.5.1 and 2.6.0 still legacy). A few sentences on "what admitted means", how to check (`workflow-controller inspect .`) and a rule: other repositories link here instead of repeating the table. The 2.6.0 digests are copied from the contract and `tests/test_docs.py` asserts both directions: every 2.6.0 digest in `RELEASE_CONTRACTS` appears on the page, and no other 64-hex string does.
- **`glossary.md`**: the seven terms plus phase names an operator meets, from `concepts.md`'s current glossary and ADR 0001; each term is a heading so it has an anchor.
- **`release-history.md`**: one plain line per release 1.1.1 to 1.7.0 (1.1.0 was abandoned, `abandoned_tags`), from `docs/releases/*.md` and the milestone narratives; each links to its notes where they exist (notes begin at 1.3.0); no internal ids.
- **`common-problems.md`**: usage pause, waiting for checks, exit 10, 20, 30 and 45, a job held draining, a refused plan, a refused settings file; each: symptom, one-line fix, link.

### D. The two documentation checks (CP1)

`tools/check_docs.py` (standard library; importable and runnable as `python3 tools/check_docs.py [--root DIR]`, exit 0 clean, 1 with one line per problem) and `tests/test_docs.py` (runs it on the repository, plus synthetic-tree tests of each rule, good and bad).

1. **Links and anchors**: every relative Markdown link and `#anchor` in `README.md`, `docs/*.md`, `docs/guide/`, `docs/adr/` and `docs/releases/` (not `docs/ROADMAP.md` and `docs/ACTIVE_MILESTONE.md`, which Workflow commands rewrite, nor `docs/ai-workflow/` and `docs/milestones/`, which are historical records) resolves to an existing file and a heading of it, using GitHub's slug rule, which is `github-slugger`'s: the heading's text with inline-code backticks removed but the code text **kept** (so `` ### `WORKFLOW_QUERY_FAILED` and `workflow_query_failed` `` is `workflow_query_failed-and-workflow_query_failed`); lowercased; every character removed except letters, digits, **underscores and literal hyphens**, which are preserved, and spaces, which become hyphens (so a link's `#anchor` is compared case-sensitively against the slug, which is already lowercase: `#Upgrade` does not match `## Upgrade`); and duplicates allocated against the anchors already used on the page, not by counting: a slug already taken gets the first `-N` (N from 1) that is itself not taken, so headings `foo-1`, `foo`, `foo` yield `foo-1`, `foo`, `foo-2`. Link text and targets in code fences and inline code are skipped; heading text in inline code is not. External `https://github.com/RodrigoFAbreu/<repo>` links must name `workflow-controller`, `workflow-manager` or `workflow` and be well formed (path forms `blob/<ref>/<path>`, `tree/...`, `releases[...]`, `issues/N`, `pull/N`, or the bare repository); any other host is allowed only when listed in the check's small allow-list, seeded from the external hosts present at the base commit, which are `github.com` (release URLs and the three repositories) and `pipx.pypa.io` (twice, in the guides); `docs.github.com` is added only if a page needs it; nothing is fetched.
2. **Commands and flags**: in the fenced code blocks of the task pages (`install.md`, `run.md`, `update.md`, `common-problems.md`, `README.md`) every shell line starting `workflow-controller` (after optional `$ ` and global options) is parsed with `controller.cli.build_parser()` (no run), so each subcommand, option and choice must exist. Parsing must neither run the Controller nor touch the runtime: `--version` and `-h`/`--help` are terminating actions (`_VersionAction` calls `version_text()`, which calls `identity.pin()` and so Git, and `argparse` exits). The check therefore validates against a parser in which every terminating action (the version action and every help action, in the root parser and each subparser) is replaced by a recording no-op with the same option strings and `nargs=0`: the flag parses, is recorded, prints nothing, exits nothing and resolves no runtime, and the rest of the invocation is still validated in full, so `workflow-controller --version --bogus` and `workflow-controller step --help --bogus` fail (the real CLI accepts both; the check rejects them on purpose). **Required-presence rule:** the real CLI exits 0 at a terminating action before argparse checks for required arguments, but a no-op does not, so argparse would report them missing, and it reports that before it reports unknown trailing options, so accepting the error would also accept `--version --bogus`. The check therefore never accepts an error; it suppresses the presence check and still completes parsing. It builds a second, relaxed parser from the same recording-no-op parser: every action in the root parser and, recursively, each subparser has `required` set to `False` (positionals, required options and the subparsers action) and every mutually exclusive group has `required` set to `False` (so `milestone-binding . --help`, whose group `--new-pr`/`--abandon` is required at `controller/cli.py:349`, passes). A line is parsed with the relaxed parser first. If no terminating action was recorded, the line is parsed again with the strict parser, so a line without `--help`/`--version` that omits a required argument or a required group member still fails. If one was recorded, the relaxed parse's result stands: the real CLI would have exited 0 at the flag. The relaxed parser keeps everything else, so an unknown option, an invalid choice, a wrong subcommand name, a malformed option value, a conflict inside a mutually exclusive group (`--new-pr --abandon`) and any leftover argument anywhere in the line still fail. A bare `workflow-controller --version` and `workflow-controller --help` (missing subcommand), `workflow-controller step --help` (missing positional `repo`), `workflow-controller settings --help` (missing nested `settings_action`) and `workflow-controller milestone-binding . --help` (missing required group) are instances of this one rule, not separate exemptions; `workflow-controller step . --help` needs no relaxation. Placeholders written `<like-this>` and shell variables are substituted by fixed words before parsing; `# ...` comments and lines continued by `\` are handled; a block marked ```` ```text ```` is not checked. The existing `test_plan_document_consistency.py` checks keep covering `README.md` and `docs/guide/*.md`. `tools/check_docs.py` deliberately does not reuse that module's `extract_invocation_lines`: that function pairs inline code spans per paragraph, while this check is fence-scoped with placeholder substitution, and `tools/` must not import from `tests/`. To keep the two parsers from drifting, `tests/test_docs.py` runs a shared table of cases (a global option before the subcommand, a `<placeholder>`, a bracketed line) through both and asserts they agree on what is an invocation.

Two small structural assertions live in the same module and are not separate checks. Each is active only for the pages in the check's **U** list, which grows with the checkpoints so an unfinished page never fails an earlier checkpoint: CP1 starts it empty, CP2 adds the four reference pages, CP3 the three task pages, CP4 `common-problems.md` and the reorganised guides, CP5 `README.md` and `docs/README.md`. (The link check, by contrast, runs on every file from CP1, so CP4's rename rewrites every inbound link, including those in `README.md` and `docs/README.md`, in the same checkpoint.) Each **U** page's first non-blank line after the H1 has the "For: ... Last checked with: ..." header, and no **U** page matches the internal-id patterns (`\bCP\d+\b`, `\b[A-Z]{2,5}-R\d+-\d+\b`) or names a real work-item id. A work-item id is a key of `docs/ai-workflow/WORKFLOW_STATE.json`'s `work_items` or the stem of a `docs/milestones/completed/*.md` file, matched as a whole token; so `$TMPDIR/workflow-controller-tests/<run_id>/` and `workflow-controller-tests/timings-local.json` (`docs/guide/development.md:118,130`) are not ids and are not flagged, and a link target is not scanned. The page list is the check's own constant, so a new user page is added to it deliberately.

### E. Entry points and cross-repository links

`README.md`: one paragraph on the Controller; the shared three-sentence "how the pieces fit" paragraph (Workflow = the process and its commands, installed into a repository; Workflow Manager installs, updates and verifies the Workflow from published digest-pinned releases; Controller runs the lifecycle steps automatically), each piece linking to the other repository's README (`https://github.com/RodrigoFAbreu/<repo>#readme`); a quick start (install, verify, first run, about five minutes); links to the task pages, compatibility, common problems, glossary, release history, lifecycle diagram. The existing README constraints (five invocation lines, one `follow`, the four job-id code spans, `docs/README.md` map) are kept, with the `validate.yml` job paragraph moved under "Development" briefly.
`docs/README.md`: a user page with the header; a map by reader goal; ADRs, roadmap, releases, history indexes kept. `CLAUDE.md` is updated only for moved paths.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The documentation checks: tools/check_docs.py and tests/test_docs.py (links and anchors with GitHub slugs, cross-repository link form, commands and flags in task-page code blocks via controller.cli.build_parser, page-header and internal-id assertions, exit-table consistency including SIGINT_EXIT_STATUS, digest consistency in both directions), synthetic-tree tests of every rule good and bad (including a stale link into docs/adr and docs/releases, parser agreement with test_plan_document_consistency, `--version`/`--help` validated with subprocess and runtime resolution forbidden: `workflow-controller --version`, `--help`, `step --help`, `settings --help` and `milestone-binding . --help` pass (required-presence suppressed when a terminating action was recorded), while `--version --bogus`, `--help --bogus`, `step --help --bogus`, `milestone-binding . --help --bogus`, `milestone-binding . --help --new-pr --abandon` and `stpe --help` still fail, and a line without a terminating action that omits a required argument or group member (`step`, `milestone-binding .`) still fails, and slug cases: existing underscore-bearing anchors, literal hyphens, inline-code headings, and the collision `foo-1`, `foo`, `foo`), the page lists as constants with the checkpoint that activates each | - | 2 | 1 |
| CP2 | Reference pages: docs/exit-codes.md, docs/glossary.md, docs/compatibility.md (digests from RELEASE_CONTRACTS), docs/release-history.md, each with the user-page header, plus their rows in the checks' page list | CP1 | 2 | 1 |
| CP3 | Task pages: docs/install.md, docs/run.md, docs/update.md with the fixed structure, every command run against a scratch repository, and the README quick start | CP2 | 2 | 1 |
| CP4 | Reorganise the guides: reduce installation.md to its three-heading stub, rename concepts.md to how-it-works.md, add docs/common-problems.md, repoint the README and documentation-map links to the renamed guide in the same checkpoint, replace repeated exit tables and Workflow-compatibility text with links, cut milestone history, title the milestone-branches preamble, add the headers | CP3 | 3 | 1 |
| CP5 | Entry points and verification (terminal): README and docs/README.md rewritten around the pages and added to the user-page list with their headers, CLAUDE.md paths, development.md documentation-checks section, cross-repository links, the full suite, both checks clean, ci_workflows --check unchanged, the protected-path diff from the base | CP4 | 2 | 1 |

## Requirements

| id | requirement |
|---|---|
| R1 | the documentation checks exist: links and anchors, cross-repository link form, commands and flags in task-page code blocks, the page-header and internal-id assertions, exit-table and digest consistency; offline, stdlib only, run in the existing validate job |
| R2 | `exit-codes.md`, `glossary.md`, `compatibility.md`, `release-history.md` exist, carry the **U** header, and agree with the code and ADR 0001 |
| R3 | `install.md`, `run.md`, `update.md` and the README quick start exist with the fixed structure and runnable commands |
| R4 | the guides are reorganised per section B, repetition and milestone history cut, exit text linking to the one table, the compatibility text linking to the one page; `common-problems.md` exists |
| R5 | README, `docs/README.md`, `CLAUDE.md` point to the new structure; cross-repository links use the three known repositories; both checks pass; the existing suite passes; invariants I1-I5 hold |

## Decisions for the reviewer and the user

- **D-1** Task pages in `docs/` root (`install.md`, `run.md`, `update.md`), as the other lane proposes. Alternative: under `docs/guide/`. Chosen for cross-repository name sharing.
- **D-2** Move the content of `docs/guide/installation.md` to the task pages and keep the file as a stub of exactly three headings (`Supported Workflow releases`, `Upgrade`, `Moving a target to another Workflow release`) so the six links from `docs/adr/` and `docs/releases/` stay valid while I1 and the Non-goals keep ADRs and release notes unedited. Rename `concepts.md` to `how-it-works.md` (the glossary leaving it) with no stub, since nothing outside the pages this milestone rewrites links to it. Alternatives: (b) drop `docs/adr/` and `docs/releases/` from the link check and accept broken historical links; (c) allow link-target-only edits in ADR bodies and release notes. Chosen: the stub. Say if you want (b) or (c) instead.
- **D-3** The checks are tests in the existing suite plus `tools/check_docs.py`, not a new CI job: the workflow files stay unchanged and the checks run in the validate job's `tests` shards. A separate step would change the rendered workflows and the CI-placement partition.
- **D-4** Version header: `Controller 1.7.0; Workflow 2.6.0 and 2.7.0`; it is updated by hand at each release that changes a page, and the checks assert only its presence and shape.
- **D-5** This is a `docs:` pull request (no release) even though it adds two small code files; they are test and tooling support, like the roadmap says.
- Gate: the user reads the pages before merge, and an independent reviewer checks the pages against the code (roadmap 11.8). Neither is skipped.

## Open questions

None that block planning. The Workflow repository's own overview page does not exist yet; the lifecycle link points at the shipped SVG and is repointed by the other lane's clean-up.

## Verification

- `python3 -m unittest tests.test_docs tests.test_plan_document_consistency` and the full suite (`python3 tools/run_tests.py`, never backgrounded, no `PYTHONPATH=.`).
- `python3 tools/check_docs.py` clean; `python3 tools/ci_workflows.py --check` unchanged; golden generators only with `--check`.
- Protected-path diff from the base: only `README.md`, `docs/**` (non-adr, non-ai-workflow bookkeeping), `CLAUDE.md`, `tools/check_docs.py`, `tests/test_docs.py` and the work item's own state files.
- A manual run of every command shown on the task pages against a scratch repository, recorded in the milestone narrative.

## Migration / data-integrity notes

Documentation only. Moving `installation.md` and `concepts.md` would break external deep links to them. `installation.md` stays as the three-heading stub of D-2, so its six inbound links in `docs/adr/` and `docs/releases/` and any external deep link to those three anchors keep resolving without editing ADRs or release notes; `concepts.md` has no inbound link outside the guides, README and `docs/README.md`, all rewritten here, and external deep links to it (and to `installation.md` anchors other than the three) are accepted as broken. This is settled now, not left to a later amendment.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-documentation-reorganisation-artifacts.json` has the repository's established two-stage shape, copied from `workflow-controller-orchestration-protocol-v1-artifacts.json` with this item's own id and plan, registry and mapping paths (the raw product template is not used: it leaves `docs/guide/`, `docs/releases/`, `docs/README.md`, `tools/`, `tests/`, `controller/`, `.workflow-controller/`, `pyproject.toml`, `setup.py` and `.github/workflows/*.yml` unclassified and excludes `README.md` and `CLAUDE.md` at the implementation stage). Plan stage: the plan, registry and mapping are protected by exact path; everything this milestone writes (`README.md`, `CLAUDE.md`, `docs/README.md`, `docs/guide/`, `docs/releases/`, `docs/adr/`, `tools/`, `tests/`, `controller/`) is excluded, as are the eight new pages (`docs/install.md`, `run.md`, `update.md`, `compatibility.md`, `common-problems.md`, `exit-codes.md`, `glossary.md`, `release-history.md`) by exact path. Implementation stage: protected by exact path are `README.md`, `CLAUDE.md`, `docs/README.md`, the eight pages, `pyproject.toml`, `setup.py`, the four `.github/workflows/*.yml` and the declaration file itself; protected by prefix are `controller/`, `.workflow-controller/`, `docs/adr/`, `docs/guide/`, `docs/releases/`, `tests/`, `tools/`, `app/`, `config/` and `gradle/`. So `tools/check_docs.py`, `tests/test_docs.py` and the guides are classified, and a change to `controller/`, `.workflow-controller/`, `pyproject.toml`, `setup.py` or `docs/adr/` (I1) shows up in the reviewed diff. A page added later would need an exact-path entry through the declaration-repair route in `REVIEW_PROTOCOL.md`, so the page list is fixed here.

## Review disposition

| Round 1 finding | Disposition |
|---|---|
| LPR-R1-001 declaration is the raw template | accepted; declaration rebuilt from the orchestration-protocol-v1 shape with the eight pages added; "Artifact declaration" rewritten to match the file |
| LPR-R1-002 retiring `installation.md` breaks six inbound links | accepted; route (a): three-heading stub, settled in D-2, I1, I2, Non-goals, B, D.1 and the migration note; the Investigation lists the six links |
| LPR-R1-003 exit-code rule and 130 | accepted; C states codes = `EXIT_*` plus `SIGINT_EXIT_STATUS`, ADR comparison ignores 130 |
| LPR-R1-004 allow-list hosts | accepted; seeded from the base: `github.com`, `pipx.pypa.io` |
| LPR-R1-005 internal-id pattern false positives | accepted; match real work-item ids only (state keys plus completed-milestone stems) |
| LPR-R1-006 header position | accepted; "first non-blank line after the H1" in A and D |
| LPR-R1-007 extractor reuse | accepted in part: deliberately different (fenced vs inline, `tools/` must not import `tests/`), with a shared-cases agreement test |
| Missing test: stale-link synthetic test | accepted; added to CP1 (a link to a removed guide in `docs/adr/` and `docs/releases/` flagged; the stub passes) |
| Missing test: digest both directions | accepted; stated in C and CP1 |

| Round 2 finding | Disposition |
|---|---|
| EXT-R2-001 parsing `--version` executes runtime code | accepted (`controller/cli.py:240,264`, `identity.py:813`); D.2 validates against a parser whose terminating actions are recording no-ops, still validating the whole invocation; CP1 adds the regression cases with subprocess and runtime resolution forbidden |
| EXT-R2-002 slug rule drops underscores and hyphens | accepted (`docs/guide/automation.md:214` -> `troubleshooting.md:335`); D.1 states the preserved characters, inline-code heading text and used-anchor duplicate allocation; CP1 adds the cases |
| Optional: when structural assertions activate; repoint at CP4 | accepted; D.2 lists the activation per checkpoint, CP4 repoints README and map links |
| Optional: `docs/README.md` is a user page | accepted; table, E and CP5 |

| Round 4 finding | Disposition |
|---|---|
| EXT-R4-001 accepting missing-required errors hides invalid trailing options | accepted (reviewer probes; argparse reports the missing-required error before leftover arguments, so `--version --bogus` and `step --help --bogus` raised the same error as the valid lines); D.2 replaces error acceptance with a relaxed parser (every `required` flag cleared) used when a terminating action was recorded, so parsing and leftover validation complete; CP1 adds the trailing-option cases |
| EXT-R4-002 valid help for a required option group rejected | accepted (`controller/cli.py:349`, `required=True` group); the relaxed parser clears group `required` too and keeps conflict checks; CP1 adds `milestone-binding . --help` (passes), with `--bogus` and with `--new-pr --abandon` (fail) |
