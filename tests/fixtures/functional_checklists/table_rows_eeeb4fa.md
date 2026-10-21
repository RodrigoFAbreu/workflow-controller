## Functional review checklist

Technical approval: `6151258` (implementation revision 5, reviewed head `0624a71`). Put findings
in `.ai-review/feedback/FUNCTIONAL_REVIEW.md`.

**Setup.**

- On a checkout of `milestone/workflow-controller-workflow-2-6-integration`, build the 1.3.0 wheel
  into a throwaway virtual environment, so the installed 1.2.1 that drives this milestone is left
  alone:
  ```bash
  python3 -m pip wheel --no-deps -w /tmp/wc13/dist .
  python3 -m venv /tmp/wc13/venv && /tmp/wc13/venv/bin/pip install /tmp/wc13/dist/*.whl
  WC=/tmp/wc13/venv/bin/workflow-controller
  ```
- `workflow-manager` on `PATH`, with the 2.5.1 and 2.6.0 releases in its `distribution/`
  (`workflow-manager releases`). Git 2.36 or later, Python 3.12 or later.
- Do not set `PYTHONPATH=.` for the test runs.

**Test data.** Two throwaway Git repositories, created in the flows below (`/tmp/t26`, `/tmp/t25`).
Nothing in this repository is changed.

**Flows.**

| # | do | expect |
| --- | --- | --- |
| A | `$WC --version` | `workflow-controller 1.3.0`, runtime `package (local build from <commit>)`. |
| B | In this repository (Workflow 2.5.1): `$WC inspect .` and `$WC explain .`, then the same two commands with the installed `workflow-controller` (1.2.1) | Exit 0 each time. The 1.3.0 and 1.2.1 outputs are identical: a 2.5.1 target behaves exactly as before. |
| C | `mkdir /tmp/t26 && cd /tmp/t26 && git init -q -b main && git commit -q --allow-empty -m init && workflow-manager --release-version 2.6.0 bootstrap . && git add -A && git commit -qm "Install Workflow 2.6.0"`, then `$WC inspect /tmp/t26` and `$WC explain /tmp/t26` | `inspect` prints `(Workflow 2.6.0, profile full)`, exit 0. `explain` says `phase: NO_PHASE` and `next automatic action: /milestone-plan`. |
| D | `workflow-controller inspect /tmp/t26` (the installed 1.2.1) | Refused: `runs Workflow '2.6.0', which is outside the Controller's supported line '2.5'`. This is the behaviour 1.3.0 replaces. |
| E | As C in `/tmp/t25`, but bootstrap with `--release-version 2.5.1` and commit. `$WC inspect /tmp/t25`. Then `workflow-manager --release-version 2.6.0 update /tmp/t25`, commit, `workflow-manager verify /tmp/t25`, and `$WC inspect /tmp/t25` again | First `(Workflow 2.5.1 …)`. `verify` prints `installation matches workflow 2.6.0`. Then `(Workflow 2.6.0 …)`, exit 0. |
| F | `python3 -m unittest tests.test_workflow_release_migration` | `OK`, 10 tests, about 20 s. These are the migration scenarios M1-M5 on disposable repositories with the real 2.5.1 and 2.6.0 scripts: in-flight items keep their decision across the update, a new `scoped` item resolves its not-yet-created feedback directory, a job whose Workflow release changes before verification fails as `workflow_release_changed`. |
| G | `python3 -m unittest -v tests.test_integration_disposable_repo 2>&1 \| grep RealManagerMigrationTest` | Three `ok` lines: M1 through the real `workflow-manager` update, a 2.6.0 target's clean filter never runs, and its fsmonitor or hook is never hidden. |
| H | Optional: `CONTROLLER_TEST_OLD_GIT=<a Git 2.31–2.35 binary> python3 -m unittest tests.test_workflow_contract.GitIsolationTest` | `OK`. The old-Git live check runs instead of being skipped. |
| I | Open PR #5's checks | Every required check green. **Known to fail today**: see the limitation below. |
| J | Read `docs/releases/1.3.0.md`, and the "Supported Workflow releases" section of `docs/guide/installation.md` | They describe what A-G showed: 2.5.1 and 2.6.0 admitted by exact release, Workflow's two queries for 2.6.0 targets, the Git version rules, and the move of a target to 2.6.0. |

**Known limitations and out of scope.**

- **CI is red on PR #5 (found while preparing this checklist).** `validate / tests` shard 2 has
  failed since `0d7ecce` in four `tests.test_workflow_contract.GitIsolationTest` tests
  (`test_a_clean_filter_never_runs`, `test_a_filter_no_hashed_path_selects_leaves_the_answer_workflows`,
  `test_a_filter_that_changes_content_is_refused_rather_than_bound`,
  `test_the_querys_git_environment`). The GitHub runner configures a `git-lfs` filter in its
  system Git configuration, and the tests assert the exact set of filters the query sees. The
  Controller switches that filter off too, which is correct. Reproduced locally by adding a
  `[filter "lfs"]` section to a `GIT_CONFIG_GLOBAL` file: the same four tests fail and the other
  2246 pass, including the migration suites. It is a test-only defect, but it blocks the merge,
  so it should be recorded as a finding for `/apply-functional-review`.
- A target whose tracked files select a filter driver (for example Git LFS files) is refused for
  2.6.0 queries (`query_git_not_isolated`). This is by design, and is documented in
  `docs/guide/automation.md` ("Workflow's queries").
- Updating a target with a plan-approval journal in flight, and re-planning at `IMPLEMENTING`
  under 2.6.0, are unsupported (documented in the guide, not tested as working).
- This repository's own Workflow installation stays on 2.5.1 (plan Decision 2); moving it to
  2.6.0 is a separate change after this milestone.
- There is no end-to-end manual flow that drives a real 2.6.0 work item with live workers: that
  costs real worker spend. Flows F and G cover it with the real Workflow scripts.
