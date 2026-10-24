# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-sandbox-layout` (C10f, `docs/ROADMAP.md`; issue
[#129](https://github.com/RodrigoFAbreu/workflow-controller/issues/129)), plan revision 4 approved by gate policy.
`verify-claude` refused Claude Code 2.1.304 and later because the tester's settings allowed writes below a denied path.
The fix is a layout in which no allowed write location lies below a denied one: the writable locations live in
`<sandbox>/writable/<name>/` and the clone holds a link to each. Plan: `docs/ai-workflow/CONTROLLER_SANDBOX_LAYOUT_PLAN.md`.
Pull request title: `fix: lay out the functional tester's sandbox so Claude Code 2.1.304 and later pass verify-claude (#129)`.

| Checkpoint | State |
| --- | --- |
| CP1 -- the layout: settings invariant, external folders with links, the walk | complete (narrow checks green: sandbox, enforcement, review and claude-build tests) |
| CP2 -- the probes, the fake tester and the verification | complete (narrow checks green: enforcement, claude-build, sandbox, review and lifecycle tests) |
| CP3 -- live measurement on the installed Claude builds | complete (live tests and the symlink assumption written; the measurement on the installed builds is recorded at the functional review) |
| CP4 -- documentation, roadmap and the whole suite | complete (install, compatibility, common-problems, automation and development pages and the roadmap agree; `check_docs` and the suite run, see the test results) |

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of checkpoint state. The previous milestone (C10e,
`workflow-controller-claude-build-verify`) is archived at `docs/milestones/completed/workflow-controller-claude-build-verify.md`.

## Next action

The implementation is approved and every checkpoint is complete, so the item waits at the functional review. The owner runs the checklist below (the live flows outside the Controller's tester sandbox) and writes any findings to `.ai-review/workflow-controller-sandbox-layout/feedback/FUNCTIONAL_REVIEW.md`. Clean testing goes to `/accept-milestone`; findings go to `/apply-functional-review`. After C10f: #126, **CU3** and **A2** (see `docs/ROADMAP.md`).

## Functional review checklist

Code under review: `milestone/workflow-controller-sandbox-layout` at `befe031` (implementation revision 3, approved by the local and manual implementation reviews). The later commits change only workflow state. The plan is `docs/ai-workflow/CONTROLLER_SANDBOX_LAYOUT_PLAN.md`.

### Setup

- Run from the repository root with a clean tree. Python 3 and the standard library only; the tests need no install step. Git 2.56 is installed here.
- Leave `PYTHONPATH` unset for the `unittest` commands (`PYTHONPATH=.` breaks the decoy test). The `verify-claude` commands set it to the checkout, as shown.
- Use this checkout's code for `verify-claude`. The shared `workflow-controller` (1.19.0) is the released build without this fix, and it refuses Claude Code 2.1.309.
- Claude builds on this host: `claude` on `PATH` (`~/.local/bin/claude`) is **2.1.309**, the build under test. Also installed: 2.1.304 and 2.1.308 under `~/.local/share/claude/versions/`. **2.1.303 is not installed**, so the old-build check in CP3 cannot run here.

### Who runs each flow

- **[T] Tester-runnable.** Offline: fakes, stubs and scratch folders only. No network, no real `gh`, `claude`, `codex` or `ssh`, and no write inside the product clone. The functional tester can run these.
- **[O] Owner-run.** Uses the real `claude`, the owner's Claude credentials and model turns (the live enforcement tests and `verify-claude`). The tester's sandbox refuses a real `claude`, so these cannot run inside it; the owner runs them outside it, as CP3 requires.
- No flow here needs a pull request, a forge call or `gh`.

### Flows

**F1 [T] The settings refuse any allowed write at or below a denied path.**
`python3 -m unittest tests.test_functional_enforcement.SettingsTests -v`
Expected: all pass. A child of the clone root, the clone root itself, a child of `.git`, and an allowance equal to a denial are refused; a denial below an allowance is accepted.

**F2 [T] Writable folders sit outside the clone, reached by links; `git status` stays empty.**
`python3 -m unittest tests.test_functional_sandbox -v`
Expected: all pass, including `test_the_writable_locations_are_external_folders_reached_by_links`, `test_git_status_is_empty_in_a_fresh_clone_and_in_a_cp_a_copy_of_it`, `test_the_prompt_states_the_links_the_copy_rule_and_the_rmtree_limit` and `test_an_ignored_build_output_file_is_allowed`, whose expectation is unchanged.

**F3 [T] The post-run walk accepts the Controller's own links and reports every other change to them.**
Same module as F2. Expected: `test_the_controllers_own_links_pass_and_every_other_change_to_them_is_a_difference` passes (a missing, retargeted, replaced or second symlink is a difference naming the path), and `test_a_symlink_inside_a_writable_folder_is_not_walked` passes (writing inside the external folder is not a difference).

**F4 [T] The fake tester judges writes through the link as the real builds do.**
`python3 -m unittest tests.test_functional_enforcement.FakeSandboxEmulationTests -v`
Expected: all pass. A write through `clone/build` lands in the external folder and is allowed; replacing the link is refused; the previous layout is refused; two `cp -a` copies do not meet.

**F5 [T] The shared `verify-claude` verification fails closed, and the link probe is judged.**
`python3 -m unittest tests.test_claude_build tests.test_functional_enforcement.JudgeTests tests.test_functional_enforcement.StaticProbeTests -v`
Expected: all pass, with one expected skip: `test_this_host_passes_the_static_probe` reports that the installed `claude` is not in `VERIFIED_NATIVE_BUILDS`. That is by design: a current build is admitted by its digest recorded by `verify-claude`, not by a built-in one.

**F6 [T] The docs describe the new layout.**
`python3 tools/check_docs.py` (exit 0), then read the Claude Code 2.1.304 passages in `docs/common-problems.md` (the `verify-claude` entry, around line 164), `docs/guide/automation.md` (the sandbox paragraph, around line 424), `docs/compatibility.md`, `docs/install.md` and `docs/guide/development.md`.
Expected: none describes Claude Code 2.1.304 or later as refused. Each says that the writable locations live outside the clone behind links and that `verify-claude` verifies those builds.

**F7 [O] The live enforcement tests on the current build.**
`CONTROLLER_LIVE_CLAUDE_TESTS=1 python3 -m unittest tests.test_functional_enforcement.LiveEnforcementTests -v`
Spends real model usage; each test uses its own empty HOME. Expected: all pass on 2.1.309. They cover the boundary on the pinned CLI, an unsandboxed retry and a file-tool change changing nothing on the host, a launch without an enforcement component refused before any model call, and a launch from above the clone that can neither write through a link nor write a tracked file.

**F8 [O] `verify-claude --force` on the current build.**
`PYTHONPATH=$PWD python3 -P -m controller verify-claude --force`
Expected: exit 0; the output names Claude Code 2.1.309 and its SHA-256 digest. The record goes to the Controller's runtime root, never to the repository or the settings file. It spends up to two short model turns at low effort.

**F9 [O] Other installed builds (optional).**
`PYTHONPATH=$PWD python3 -P -m controller verify-claude --force --claude-binary ~/.local/share/claude/versions/2.1.304`, and the same with `2.1.308`.
Expected: exit 0 for each. 2.1.304 is the first build that enforces the deny above a path, so it is the nearest check to the bug. The plan's old-build check (2.1.303) cannot run on this host; record it as not performed.

### Stop conditions

- If F7 or F8 shows a current build refusing a write through a link into the external folder, stop. Plan decision D6 applies: amend the milestone with `/request-plan-amendment`. Do not fall back to a deny that leaves the clone root creatable.
- Any other failing flow is a finding (below).

### Evidence to record

The owner records the outcome of each owner-run flow here, with the build's version and digest (plan CP3).

| Flow | Build | Outcome (pass, fail, exit code) | Digest | Date |
| --- | --- | --- | --- | --- |
| F7 live enforcement tests | 2.1.309 | | not applicable | |
| F8 `verify-claude --force` | 2.1.309 | | | |
| F9 `verify-claude --force` | 2.1.304 | | | |
| F9 `verify-claude --force` | 2.1.308 | | | |
| F9 old-build check | 2.1.303 | not installed on this host: not performed | | |

### Round 1 fixes

The first functional round (tester report `.ai-review/archive/workflow-controller-sandbox-layout/functional-tester-round1-RESULTS.md`; the operator ran F7-F9, which passed) found two defects, both fixed in this bounded round, and one documentation gap:

- **Finding 1, F1 (defect).** `assert_writes_not_below_denies` compared only spellings, so an allowance below a denial through a symlinked parent was accepted. It now also compares `os.path.realpath` of the allowances and the denials; the table gains the alias-parent, `..`-through-a-link, swapped-external-folder and aliased-denial cases. Re-test: F1 and F4.
- **Finding 2, F3 (defect).** `walk_differences` filtered unexpected symlinks through the ignore rules. A symlink is now always a difference (decision D3); ordinary ignored output files stay accepted. Test: `test_a_symlink_matching_an_ignore_rule_is_still_a_difference`. Re-test: F2 and F3.
- **Finding 3, F6 (Optional, fixed).** `docs/install.md` and `docs/compatibility.md` now state that the writable folders live outside the clone behind links. Re-test: F6.
- **F5** was blocked in the tester's sandbox; the operator re-runs it outside the sandbox at the fix head. F7-F9 results stand: the fixes do not touch the build-facing code, but the operator may repeat F8 on request.

### Findings

Write findings to `.ai-review/workflow-controller-sandbox-layout/feedback/FUNCTIONAL_REVIEW.md`. `/apply-functional-review` routes each one: a bounded fix for the same scope, or a `workflow-controller-sandbox-layout-remediation-<n>` child item for wider scope.

### Known limits

- Out of scope for this milestone: the credential-read denial and `file://` origin detection ([#115](https://github.com/RodrigoFAbreu/workflow-controller/issues/115)), probes in CI, the sandbox-start check of `verify-claude` ([#132](https://github.com/RodrigoFAbreu/workflow-controller/issues/132)), and the Codex tester (C10g, [#131](https://github.com/RodrigoFAbreu/workflow-controller/issues/131)).
- No built-in digest is added to `VERIFIED_NATIVE_BUILDS`; a current build is admitted by `verify-claude`.
- The full suite was not rerun for this checklist. The CP4 record is the reference for it.
- The live flows spend model usage and do not run in CI.
