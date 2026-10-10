# Active Milestone

## Status

**In progress.** `workflow-controller-installed-release-test-agnostic` (`docs/ROADMAP.md` step C9d,
follow-up 16), plan revision 2, base commit `5532cdd`. The plan is
`docs/ai-workflow/CONTROLLER_INSTALLED_RELEASE_TEST_PLAN.md`;
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of the phase.

- **CP1 -- complete (verified).** `InstalledReleaseTest` in `tests/test_workflow_releases.py` now runs the
  three non-Manager steps of `managed_repo.inspect` (read the record, the `RELEASE_CONTRACTS` arm, the
  profile check) on the real repository, and checks every installed file against the `sha256` and
  `executable` flag in `.workflow-manager/installation.json` plus the installed command set. No vendored
  tree and no version literal. `InstalledReleaseRefusalTest` shows a modified, missing, extra and
  mode-changed file, a release without the protocol script, a protocol-major-2 `describe` and an
  unsupported profile each refused, on a disposable installation. Checked with `tests/workflow_releases/2.9.0/`
  deleted from a copy: the new tests pass, the old ones fail. `python3 -m unittest tests.test_workflow_releases
  tests.test_managed_repo` passes (the live-Manager test is skipped here).
- **CP2 -- complete (verified).** `docs/guide/development.md` now says the installed tree needs no vendored tree and is checked by capability and `installation.json`; `docs/ROADMAP.md` marks C9d and follow-up 16 complete and the Next list names C8. `docs/compatibility.md` and `docs/update.md` state no vendored-tree requirement, so are unchanged. `tools/check_docs.py` passes; the full suite (`tools/run_tests.py --jobs 2`, 3095 tests) fails only the two Git 2.56 trailer tests (follow-up 12).

- **Self-review -- complete (2026-10-10).** The full milestone diff matches D1-D6: the test's admission
  follows `managed_repo.inspect`'s non-Manager steps in order, no `controller/` change, and the vendored-tree
  tests are unchanged. No defects found. `tools/check_docs.py` and `tools/workflow_releases.py check` exit 0;
  `tools/run_tests.py` (3095 tests, 8 shards) fails only the two Git 2.56 trailer tests (follow-up 12).

## Functional review checklist

Work item `workflow-controller-installed-release-test-agnostic` (C9d), technical approval `bb4fa81`.
The change is test and docs only: `tests/test_workflow_releases.py` (`InstalledReleaseTest`,
`InstalledReleaseRefusalTest`), `docs/guide/development.md`, `docs/ROADMAP.md`. Nothing in `controller/` changed.

### Setup (isolation)

Do everything in scratch directories. Never touch `~/.config/workflow-controller/`, the real repository's
working tree beyond reading it, or `~/Workspace/workflow`. Never run a Controller `step` or `run`.

```
S=$(mktemp -d)
export XDG_CONFIG_HOME=$S/xdg-config XDG_STATE_HOME=$S/xdg-state
git clone --bare /home/rodrigo/Workspace/workflow-controller $S/origin.git
git clone -b milestone/workflow-controller-installed-release-test-agnostic $S/origin.git $S/clone
git -C $S/clone remote set-url origin $S/origin.git   # do this before anything else
```

For each tamper case below make a fresh clone the same way (`git clone $S/origin.git $S/cN`, check out the
milestone branch, set the remote URL first). Commit the change in the clone where it says so; the tests read
the working tree, so an uncommitted change is enough for the tamper cases.

### Flows

1. Targeted tests pass in the real repo (read-only run):
   `cd /home/rodrigo/Workspace/workflow-controller && python3 -m unittest tests.test_workflow_releases tests.test_managed_repo`
   Expected: `OK`, exit 0. The live-Manager test is reported as skipped; there are no failures or errors.
2. No vendored tree needed. In the clone: `git rm -rq tests/workflow_releases/2.9.0 && git commit -qm scratch`, then
   `python3 -m unittest tests.test_workflow_releases.InstalledReleaseTest tests.test_workflow_releases.InstalledReleaseRefusalTest`.
   Expected: `OK`, exit 0 (this is the point of C9d). Running the whole `tests.test_workflow_releases` module
   in that clone may fail in the tree-dependent vendored-release tests (plan D5); that is expected, and no
   `InstalledRelease*` test is among the failures.
3. Tamper cases, each in a fresh clone, then
   `python3 -m unittest tests.test_workflow_releases.InstalledReleaseTest`:
   a. append a line to `scripts/workflow_protocol.py`;
   b. `rm scripts/workflow_state.py`;
   c. `echo x > .claude/commands/extra.md`;
   d. `chmod -x scripts/prepare-ai-review.sh` (or `chmod +x` on a non-executable managed file).
   Expected for each: `FAILED`, exit non-zero, and the failure message names the offending path
   (modified / missing / extra / mode-changed file).
4. Future release admitted by capability. In a fresh clone with `tests/workflow_releases/2.9.0/` deleted, edit
   `.workflow-manager/installation.json` and set `workflow_version` to `2.99.0` (leave the file digests alone).
   Run `python3 -m unittest tests.test_workflow_releases.InstalledReleaseTest`.
   Expected: the release is still admitted, because the installed protocol script answers major 1; no
   version-literal failure. (If a test fails only on a record field you did not change, note it.)
5. In the clone (unmodified): `python3 tools/check_docs.py` and `python3 tools/workflow_releases.py check`.
   Expected: both exit 0; `workflow_releases.py check` prints nothing on success.
6. Read `docs/guide/development.md` section "Workflow release trees" and the C9d lines of `docs/ROADMAP.md`.
   Expected: they say the installed Workflow needs no vendored tree, is admitted by the Controller's own
   capability rule and checked against `installation.json`, and that vendored trees remain for the
   Workflow-derived inventories, goldens, query and migration tests.

### Known limitations and out of scope

- The module docstring at `tests/test_workflow_releases.py:7-8` still mentions the vendored tree (Codex optional note).
- `docs/guide/development.md` could also mention the installed protocol's `describe` (Codex optional note).
- Two Git 2.56 trailer tests fail locally in the full suite (follow-up 12), unrelated to this change.
- Unknown enum values from a newer Workflow are C10's concern, not this milestone's.
- No `controller/` change, no release: the PR is a `test:` PR.

## Next action

Functional review in progress: perform the checklist above and put any findings in
`<feedback_dir>/FUNCTIONAL_REVIEW.md`. When clean, `/satisfy-gate acceptance` (the gates are automatic), then
merge as a `test:` PR (no release), then C8.
