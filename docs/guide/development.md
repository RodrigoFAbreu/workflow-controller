# Development and the test runner

> For: contributors and maintainers working from a checkout. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.9.0 and 2.12.0.

[Back to the documentation map](../README.md)

## Working from a checkout

```bash
pip install -e .
python3 tools/run_tests.py                          # everything, in parallel shards (about 1.5 min)
python3 -m unittest discover -s tests -t .          # the Controller's own suite, serially (about 8 min)
CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime -v  # wheel build + venv install
CONTROLLER_LIVE_WORKER=1 python3 -m unittest tests.test_integration_disposable_repo -v  # opt-in: live claude, real spend
CONTROLLER_LIVE_CLAUDE_TESTS=1 python3 -m unittest tests.test_functional_enforcement -v  # opt-in: live sandbox probes, real spend, own HOME
CONTROLLER_TEST_OLD_GIT=/path/to/git-2.35 python3 -m unittest tests.test_workflow_contract -k fsmonitor  # opt-in: a Git before 2.36
python -m pip wheel --no-deps -w dist .             # a local wheel (build_origin "local")
```

The packaging tests skip when a build prerequisite is missing, unless
`CONTROLLER_REQUIRE_PACKAGING_TESTS=1` makes that a failure. A local wheel
build refuses a stale `build/` directory left by an earlier build that
held files this one does not; delete `build/` and rebuild.

The live probes are one implementation, `controller/claude_build.py`, shared
by these tests and by `workflow-controller verify-claude`; a test with a fake
`claude` exercises it without a model.

The live sandbox probes run in their own empty home, into which they copy
`.credentials.json` from the operator's Claude home (`CLAUDE_CONFIG_DIR`, else
`~/.claude`); they pass no `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`.
Without that file the model probes skip and the run can still report `OK`;
only a run with no skips proves the enforcement.

`CONTROLLER_TEST_OLD_GIT` names a Git older than 2.36, built from source
if need be. It runs the live check that such a Git runs a boolean
`core.fsmonitor` as a program, which the Controller refuses. The rest of
the suite needs a recent Git: an older one lacks some of what the Git
isolation tests plant, such as `GIT_CONFIG_GLOBAL` (2.32) and configured
hooks.

Do not set `PYTHONPATH=.`: `tests.test_identity`'s decoy-package test
then imports its decoy and fails. Some tests need package-index access,
because `fixtures.editable_install` runs a build-isolated `pip install -e`.
Run the suite in the foreground: a job backgrounded with a plain `&`
ignores SIGINT, and the Ctrl-C tests then fail spuriously.

## Install from a checkout

Prefer a [release](../install.md). Build from a checkout only to test unreleased code, or when no release is reachable. There are two ways, and they give different runtimes.

**A local wheel (a `package` runtime, like a release).** Build the wheel and install it with pipx:

```bash
git clone https://github.com/RodrigoFAbreu/workflow-controller.git
cd workflow-controller
python3 -m pip wheel --no-deps -w dist .
pipx install --force ./dist/workflow_controller-*-py3-none-any.whl
workflow-controller --version    # runtime: package (local build from <commit>)
```

The build records the checkout's commit, whether it had uncommitted
changes, and a digest of the package. The wheel's version is the checkout's:
the highest release tag reachable from `HEAD`, so a checkout ahead of
`v1.4.0` builds `workflow_controller-1.4.0-py3-none-any.whl` (see
[Building from a checkout](#building-from-a-checkout)). A clone that reaches
no release tag, a shallow one for example, builds `0.0.0`, so build from a
full clone. The line `package (local build from <commit>)`, not the
version, tells such a wheel apart from a release. A wheel built from
uncommitted changes, or with no verifiable provenance, needs
`--allow-dirty-source` to run `step`, `run` or `resume`. A plain
`pip install .` also gives a `package` runtime.

**An editable install (a `source` runtime, for development).** `pip install -e .`
runs the checkout itself. `step`, `run` and `resume` snapshot the checkout's
committed `HEAD` with `git archive`, so what runs is always a commit. With
uncommitted changes to the Controller's own files they refuse
(`DirtyControllerSourceError`) unless you pass `--allow-dirty-source`, which
snapshots the working tree instead. A source runtime keeps its state in
`<checkout>/.controller/` (row 3 of the
[runtime root ladder](runtime.md#controller-owned-runtime-state); see also
[Runtime identity](runtime.md#runtime-identity)).

## Building from a checkout

Once `pyproject.toml` declares `dynamic = ["version"]` (this repository
from the [cutover](ci-and-releases.md#cutover-from-the-version-file-model)
on), no file holds the version. `setup.py` supplies it at build time:

- a local build (`python -m pip wheel --no-deps -w dist .`, or
  `pip install -e .`) gets the highest `v<MAJOR.MINOR.PATCH>` tag
  reachable from `HEAD`, or `0.0.0` without one, or outside Git.
  `python3 tools/release.py version` prints it, and
  `tools/release.py verify-wheel --local` expects it;
- a release build sets `WORKFLOW_CONTROLLER_RELEASE_TAG` to the tag
  (the policy's build command does it, from `{tag}`), and the wheel gets
  that tag's version. A malformed tag fails the build.
  `verify-wheel --tag TAG` expects the tag's version.

A checkout between two releases therefore builds the last release's
version, not a development version; `BUILD_INFO.json`'s commit and
`build_origin: "local"` tell the build apart. With a static
`version = "..."` line (the legacy model), setuptools reads it, and a
release tag that does not match it fails the build.

The tests never depend on the real tags: `tests/fixtures.build_checkout()`
writes a static version into every disposable clone and never tags it.
Only the tests that exercise tag derivation keep the dynamic form, and
they create their own tags.

## Verification policy

- Full-suite verification uses the sharded default,
  `python3 tools/run_tests.py`. Its coverage check proves every planned
  test ran exactly once.
- A serial run (`--serial`, `--jobs 1`, `--shards 1`) is reference
  evidence for an exceptional question, such as whether a failure depends
  on co-residency. It is not a routine confidence rerun.
- Evidence that already exists is reused while its identity holds: the
  same implementation revision and the same test inventory. Rerun only
  when something relevant changed, never because a longer run feels more
  thorough.
- CI and local timing evidence is reusable on the same terms.

## The test runner

`tools/run_tests.py` (with its library `tools/test_shards.py`, both
stdlib only and outside the wheel) runs a selection of tests in
duration-balanced parallel shards. `python3 -m unittest ...` and
`cd scripts && python3 <suite>` keep working unchanged; the runner is
additive. The design record is
[ADR 0005](../adr/0005-adaptive-test-sharding.md).

```bash
python3 tools/run_tests.py                                  # the full selection
python3 tools/run_tests.py tests.test_worker tests.test_cli.RunRecordCtrlCTest conformance:workflow_state_test.py
python3 tools/run_tests.py --serial [names]                 # the same selection in one process, in order
python3 tools/run_tests.py --plan-only [names]              # print the plan and exit
python3 tools/run_tests.py --replay RESULTS/plan.json [--shard i]   # re-run a recorded plan, or one shard of it
```

- **Selection.** With no names the selection is every test
  `python3 -m unittest discover -s tests -t .` loads plus the frozen
  Workflow conformance suites of the installed release (seven up to 2.7.0; nine
  from 2.9.0, which adds `workflow_protocol_test.py` and
  `workflow_gate_policy_test.py`). The planner reads the suites from the managed
  `workflow-conformance.yml`: one `run: python3 <file>` line per suite (up to
  2.9.x) or the single `suites="a.py b.py ..."` assignment of 2.10.0; a file
  with both forms, several assignments, an empty list or a missing script is
  refused. A name is a unittest dotted name (`tests`,
  a module, a class or a test), `conformance` (every suite) or
  `conformance:<file>`. A name that matches nothing is refused.
- **Shards.** A class is the smallest unit of placement (a whole module
  when it defines `setUpModule`/`tearDownModule`, a whole file for a
  conformance suite), so class fixtures run once, as serially. Each
  shard is a separate process in its own session, with its own `TMPDIR`
  and `XDG_STATE_HOME`. The shard count is planned from recorded
  durations: about 60 s of work per shard, at least 2, at most
  `min(8, CPUs)`. CI plans from the `ci` profile, which has a ceiling of six shards
  (`CI_MAX_SHARDS`, the runner budget stated in
  [Continuous integration](ci-and-releases.md#continuous-integration)).
  Two modules stay out of the CI plan and run on `main` instead
  (`tests.test_catalogue_determinism` and `tests.test_scenarios_negative`, see
  [Checks that run after the merge](ci-and-releases.md#checks-that-run-after-the-merge));
  a local full run still runs them, and
  `python3 tools/run_tests.py main-check MODULE` runs one the way `main.yml`
  does (it fails on an empty selection, a skip or a failure). `--shards N` pins it, `--jobs J` runs at most `J` at
  once, and `--target-seconds`, `--min-shards` and `--max-shards`
  override the planning parameters. A shard runs its conformance suites
  in a helper process beside its Controller tests (the same executor in
  CI and locally), so it lasts as long as its longer lane and the planner
  balances by that; see
  [How a shard runs](ci-and-releases.md#how-a-shard-runs).
- **Proof.** A run passes only if every planned test ran exactly once and
  passed; a missing, extra, duplicated or substituted test id fails the
  run and is named. The summary states the check's verdict either way
  (`coverage: exact; ...` or `coverage: NOT exact; ...`, with the
  missing, unplanned and duplicate counts). A failed test is never
  retried. The exit status is 0 (pass), 1 (a test or fixture failed, or
  a process leaked), 2 (refused, crashed, not run, or a coverage
  violation) or 130 (interrupted: Ctrl-C stops every shard's process
  group).
- **Results.** Each run writes `plan.json`, one `shard-<i>.json` and
  `shard-<i>.log` per shard, and `SUMMARY.md` under
  `$TMPDIR/workflow-controller-tests/<run_id>/` (or `--results-dir`). The
  summary names every failing test with its traceback, its shard's log,
  and two reproduction commands: `python3 -m unittest <id>` alone, and
  `--replay ... --shard <i>` for the exact co-resident order.
- **Leaks.** A test must end every process it starts. A process that
  outlives its shard is reported, then killed, and fails the run: the
  shard's verdict is `LEAKED` (exit 1) even if every test passed, unless
  something worse happened to it. The summary's "Leaked processes
  (failure, killed)" section names each one with its shard, pid, age and
  command line.
- **Leftovers of a killed test run.** A test run killed mid-suite used to
  leave a fake worker and its stdin anchor behind, and inside a
  Controller-launched worker those held the real job draining for hours.
  Now `tests/fake_claude.py` ends itself and its process group
  `FAKE_CLAUDE_ORPHAN_SECONDS` (default 300) after its parent dies, and
  writes `FAKE_CLAUDE_ORPHAN_READY_FILE` once that watchdog runs, so a test
  that orphans a fake on purpose waits for the file first. Every other
  process a test starts that can outlive it (detached helpers, daemons,
  sleepers, release loops) lives at most
  `LEFTOVER_LIFETIME_SECONDS` (600) in `tests/process_fixtures.py`, and a
  child Controller ends with its test, by a parent guard (`-c` scripts) or
  an independent `timeout` deadline (`-m` and console-script children).
  `LEFTOVER_INVENTORY` lists each shape, and a check over every process
  start keeps a new one from going unclassified. A new test that orphans a
  fake on purpose must finish within that time or set the variable.
- **Timings.** Durations only decide where a test runs, never whether it
  runs. Local runs plan from the untracked
  `$XDG_CACHE_HOME/workflow-controller-tests/timings-local.json`
  (updated after every run), falling back to the committed
  `tools/test_timings.json`, which is all CI plans from. A missing or
  corrupt file falls back to defaults with a warning. A committed (CI)
  estimate used as the local fallback counts in the total and the
  assignment, but never sets the local largest-atom floor: CI seconds are
  not local seconds, so only local-machine estimates can hold the local
  shard count down. The committed
  profile changes only through an explicit, reviewed refresh, for
  example from the `timings-ci-attempt-<a>` artifact of a CI run's
  highest attempt, which holds the plan and only the records
  `tests-result` counted:
  `python3 tools/run_tests.py timings merge --into tools/test_timings.json DIR...`.
  Ordinary drift needs no refresh and costs wall time (the shard count
  and the balance), never coverage: a class
  whose test count changed is scaled per test, and a new class is
  estimated from its module's mean. A removed or renamed class is
  different: its old entry names an atom that no longer exists, which
  `CommittedTimingsTest` rejects until a `timings merge` prunes it (the
  merge drops every entry the inventory no longer contains).
- **Serialization.** A test runs alone only if `EXCLUSIVE_ATOMS` in
  `tools/test_shards.py` lists its class, with a reason. It is empty;
  an entry needs evidence that the race cannot be fixed in the test.

## Documentation checks

`tools/check_docs.py` keeps the documentation true without a network or a
running Controller. It uses only the standard library, and
`tests/test_docs.py` runs it on the repository and tries every rule on
small synthetic trees, so the checks run inside the validate job's `tests`
shards with no workflow change.

```bash
python3 tools/check_docs.py             # exit 0 when clean, 1 with one line per problem
python3 tools/check_docs.py --root DIR  # check another tree
python3 -m unittest tests.test_docs
```

What it checks:

- **Links and anchors.** Every relative Markdown link and `#anchor` in
  `README.md`, `docs/*.md`, `docs/guide/`, `docs/adr/` and
  `docs/releases/` must reach an existing file and, for a Markdown file, a
  heading, using GitHub's anchor rule (lowercase, inline-code text kept,
  letters, digits, underscores and hyphens preserved, a repeated heading
  numbered `-1`, `-2`, ...). `docs/ROADMAP.md` and
  `docs/ACTIVE_MILESTONE.md` are skipped, and so are the Workflow's own
  records under `docs/ai-workflow/` and `docs/milestones/`. A link to
  github.com must name one of the three repositories
  (`workflow-controller`, `workflow-manager`, `workflow`) in a known form
  (`#readme`, `blob/<ref>/<path>`, `tree/...`, `releases`, `issues/N`,
  `pull/N`); any other host must be on the check's short allow-list.
- **Commands and flags.** Every `workflow-controller` line in a fenced
  code block of `README.md`, `docs/install.md`, `docs/run.md`,
  `docs/update.md` and `docs/common-problems.md` is parsed with the
  Controller's own argument parser, never run: each subcommand, option and
  choice must exist. `--help` and `--version` are accepted without running
  anything, but the rest of the line is still checked. `<placeholders>`
  and shell variables are replaced by fixed words first; a block marked
  `text` is not checked.
- **User pages.** Each page in the check's `USER_PAGES` list opens with
  the `> For: <reader>. Last checked with: <versions>.` line right after
  its title, and names no internal id (checkpoint or review-finding
  numbers, work item ids).
- **Facts kept in one place.** The [exit-code page](../exit-codes.md) lists exactly the
  Controller's exit statuses (and the shell's 130 for Ctrl-C), matching
  ADR 0001's table; the compatibility page carries exactly the pinned
  script digests of `controller/workflow_contract.py`; the
  `docs/guide/installation.md` stub keeps its three headings, which older
  ADRs and release notes link to.

Adding a page: put a new user page in `USER_PAGES`, and in
`COMMAND_PAGES` too if it shows commands, with the number `ACTIVE_THROUGH`
holds. The per-page numbers already in the file are historical, so a new
page uses the current `ACTIVE_THROUGH` value. When a release changes a page,
update its "Last checked with" line by hand; the check only asserts the line is there and has that shape.

## Lifecycle scenarios

A scenario is a scripted run of one whole lifecycle path (a full lifecycle to
merge, a human gate, a worker failure, a killed Controller and its recovery,
a squash release, notifications, a usage pause, the read-only commands, the
settings file, a review loop stopped at the round cap, the four review modes) against disposable repositories, a scratch origin and the
fake `claude`, `gh`, `workflow-manager` and SignalHub. It launches no model
and touches no network. Scenarios are test-side code: they live in
`tests/scenarios/`, the runner is `tools/run_scenarios.py`, and `controller/`
gains nothing, so adding one is never a release.

```bash
python3 tools/run_scenarios.py list                  # the catalogue
python3 tools/run_scenarios.py run                   # all of it, about 40 s
python3 tools/run_scenarios.py run scenario-trunk-drift
python3 tools/run_scenarios.py check                 # re-validate what run wrote
```

`list` and `run` both load the scenarios inside the sandbox, so on a machine
without user namespaces they exit 2 unless `--degraded-diagnostic` is passed
(its reports are not evidence).

`run` writes one `<flow_id>.json` per scenario and an `index.json` under
`.ai-review/scenario-evidence/` (or `--output DIR`). Each record has the keys
of the protocol's `functional_evidence` (`flow_id`, `status`, `head`,
`ran_at`, `summary`, `log_digest`, `run_ref`, `reporter`), and the Workflow's
own `ingest_functional_evidence` accepts it. Flow ids are
`scenario-<name>`, and `scenario-kill-and-resume-<point>` for the six kill
points. The `summary` is the ordered observations (exit codes, phases,
commit subjects, worker invocation counts, normalised record digests), with
scratch paths, timestamps, pids, tokens and commit ids normalised, so two
runs against the same commit give the same `summary`. `run` exits 0 when
every scenario passed, 1 when one failed (a `failed` record is still
written), and 2 when it is refused and writes nothing: the code paths
(`controller/`, `tests/`, `tools/`, packaging) differ from `HEAD`, or the
sandbox is unavailable. `--check-budget` makes the runner exit 1 when the
catalogue exceeds 120 s.

**The sandbox.** Every scenario runs in user, network and mount namespaces
that each descendant inherits: no connection beyond loopback, every real
location (the checkout and its Git directories, the Controller's config and
state directories, the real runtime root) read-only, a `PATH` of only the
fakes and the allowed tools, and a sanitised Git environment. It is set up
in a privileged phase and then runs as your uid with no capability. There is
no PID namespace, on purpose: the Controller's liveness and lock answers
must match a host run. Without user namespaces (some distributions
restrict them) the runner exits 2 and the suite skips the scenarios; under
`CI` they fail instead, so a green CI run means the sandbox was exercised.
GitHub's `ubuntu-latest` image restricts them through AppArmor
(`kernel.apparmor_restrict_unprivileged_userns=1`), so the generated
`validate.yml` lifts the restriction in the `tests` job before the shard runs;
on such an image locally, run
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` or use the
diagnostic.
`--degraded-diagnostic` runs without enforcement for local debugging and
writes reports that are not evidence.

**What is enforced, and what is only detected.** The namespaces and the
`PATH` are enforcement. An audit hook that fails a scenario launching an
executable outside the scratch `bin/` is a detector only: it covers the
in-process Controller and child Controllers started through the kit's
bootstrap, not what an allowed shell or git hook launches, a `python -E`
child, or a re-exec.

**What a `passed` record proves.** That a scripted path ran on that commit
and produced those observations. It does not prove a real worker behaves:
the fakes follow the contract suite in `tests/test_fake_claude_contract.py`,
and the live-worker test stays opt-in. One path is not covered: anything a real
forge or SignalHub does that the fakes do not. (`scenario-review-mode-single-provider`, in `tests/scenarios/review.py`, drives `auto`, `cross-model`, `single-provider` and `manual` through `cli.main` against the fake `codex` and the fake review `claude`; the lowered-policy signal is the captured one, patched in, because the stub manager reports no adoption. A usage limit reached
*mid-job* is covered by the `usage-limit-mid-job-waits-and-repeats`
scenario, with the sanitised real stream replayed by a fake worker.) Records carry no signature; the gate policy
(step C10) decides what to require and whether the reporter and commit are
trusted.

**Adding a scenario.** Register a function with `@scenario(flow_id,
covers=...)` in a module `tests/scenarios/catalogue.py` imports. It receives
the kit, drives the real `cli.main`, records `kit.observe(name, value)` and
asserts outcomes only, never a function's branches (those stay in the unit
tests). Add a mutation check to `tests/test_scenarios_negative.py` that
breaks its claim and requires the scenario to fail, so it cannot pass
vacuously. `tests/test_scenarios.py` generates one `TestCase` class per
scenario, which the shard planner treats as one atom; add its measured time
to `tools/test_timings.json` with `tools/run_tests.py timings merge`.

## Shared lifecycle snapshots

The protocol lifecycle test classes (`tests/test_protocol_lifecycle.py` and the
classes built on it) used to drive a whole repository to the same row once per
case. `Lifecycle.reach(name, drive)` now drives to a row once per test process,
keeps the built tree (`fixtures.take_snapshot`), and gives every later life
with the same inputs a copy (`fixtures.restore_snapshot`) instead of driving
again. The snapshot is never writable by a test; a write to one copy is not seen
by the next. A copy is put back at the path of the first build, because a
bundle's `MANIFEST.md` records `worktree_root` and its `bundle_id` hashes it, so
a relocated copy would fail verification. If that path is in use, or the copy
fails, the life drives fresh and nothing is overwritten. Use `reach` for a new
lifecycle class that rebuilds the same row; the scenario kit (`stub_dir`) always
drives. `tests/test_shared_snapshot.py` proves the isolation, the rebased paths
and the fallbacks.

## Throwaway Git repositories

Every Git repository a test creates goes through `fixtures.git_init` or
`fixtures.git_clone` (`tests/fixtures.py`). Both write
`maintenance.auto=false` and `gc.auto=0` into the new repository's own
config, so no Git command a test runs starts automatic maintenance in
the background. Those detached maintenance processes outlive the test,
are re-parented to whichever process is the nearest subreaper (a
supervising Controller, when the suite runs inside a worker), and
accumulate there. The keys are written per repository rather than set
through `GIT_CONFIG_*`, because many tests replace the environment on
purpose. `tests/test_fixtures_git_hygiene.py` fails on any other
`git init` or `git clone` under `tests/` (the vendored
`tests/workflow_releases/` trees excepted).

## Workflow release trees

Every Workflow-derived check in the Controller's tests reads a vendored tree,
with one named exception: the review-round tests (`tests/test_review_rounds.py`
and `tests/test_review_round_lifecycle.py`) read this repository's installed
`scripts/`, because they compare the Controller's copy to the Workflow's
current behaviour and call `workflow_protocol._plan_review_round`, which no
vendored tree before 2.12.0 has. Every Workflow release the Controller admits
(`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES`, and each protocol-mode
release that is checked: 2.7.0, 2.9.0 and 2.12.0) is vendored under
`tests/workflow_releases/<release>/`: the `.claude/commands/*.md` files (seventeen
up to 2.7.0, twenty-two for 2.9.0 and 2.12.0),
`scripts/workflow_state.py`, `scripts/workflow_fingerprint.py` and
`scripts/prepare-ai-review.sh`, at their target paths and modes, and, for a
release that ships the protocol, `scripts/workflow_protocol.py`, its sibling
`scripts/workflow_test_harness.py` and
`docs/ai-workflow/orchestration-protocol-v1.schema.json`. A release that ships
gate policy (2.9.0 and 2.12.0) adds `scripts/workflow_forge.py` and
`scripts/workflow_gate_policy.py`; without them the tree could not import. Each tree's
`RELEASE.json` records the Workflow Manager commit it was taken from and each
file's sha256 and executable flag, equal to the Manager's manifest. The
Workflow-derived inventories (the phase set, the command partition, the
user-only set, property 5), the decision goldens, the query tests and the
migration tests all run against these trees, once per admitted release.
`WorkflowGivenRoundTest` in `tests/test_review_round_lifecycle.py` runs the review round cap on the vendored
2.12.0 tree, where the Workflow's own answer carries `snapshot.review_round`, and checks that the cap gate and
`telemetry` report that number; the other lifecycle tests run on 2.7.0, where the Controller's count is the only
source. A plan in a disposable repository carries a `<!-- CPn -->` anchor pair for each checkpoint, which 2.12.0
requires and older releases ignore.
This repository's own installed Workflow needs no vendored tree.
`InstalledReleaseTest` reads the installation record and admits it through the Controller's own capability rule
(the release manifest, the release contract and the profile check that
`managed_repo` applies), with no version literal, and checks the installed
files against the digests and modes in `installation.json`; a modified, missing
or extra file fails by name. Protocol major 1 itself is pinned by a separate
test that installs every vendored protocol tree into a disposable repository.
So updating the installed tree through Workflow Manager needs no code change.

Only `tools/workflow_releases.py` (stdlib only) writes these trees:

```bash
python3 tools/workflow_releases.py check     # every tree against its RELEASE.json; exit 1 names each problem
python3 tools/workflow_releases.py sync 2.6.0 --from ../workflow-manager/distribution/workflow
python3 tools/workflow_releases.py sync 2.7.0 --from DIR --archive-sha256 SHA256   # DIR/2.7.0: the published archive, unpacked
```

A tree taken from a published release archive, as 2.7.0's is, records the
archive's sha256 (`archive-sha256:<digest>`) as its provenance instead of a
Workflow Manager commit.

`check` also reports a file the record does not name and an admitted
release with no tree. `sync` copies the subset from a Workflow Manager
checkout's `distribution/workflow` directory and refuses when that
release's directory differs from the recorded commit
(`--manager-commit`, default `HEAD`) or a file's digest differs from the
manifest. With a local Workflow Manager checkout (a sibling
`../workflow-manager`, or `WORKFLOW_MANAGER_DISTRIBUTION` naming its
`distribution/workflow` directory), `tests.test_workflow_releases` also
compares each record with the Manager's manifest; CI has no checkout and
skips that class.

The decision goldens have one file per admitted release. Their generators
take `--release` (default `2.5.1`, the reference release), and `--check`
compares without writing:

```bash
python3 tests/golden/generate_plan_stage_decisions.py --release 2.6.0 --check
python3 tests/golden/generate_external_implementation_review_decisions.py --release 2.6.0 --check
python3 tests/golden/generate_no_policy_lifecycle.py --check                 # 2.5.1 only
python3 tests/golden/generate_protocol_vs_legacy_differences.py --check     # 2.7.0 against 2.6.0
```

Without `--check` a generator rewrites its golden, so only run it that way
to change the golden on purpose. The 2.5.1 plan-stage generator's `--check`
reports one known difference (the `AMENDING_PLAN` decline reason), which
`tests.test_golden_plan_stage_decisions` reverts as its one permitted
difference before comparing; that test, not the bare `--check`, is the
check for that file, and a failing bare `--check` on the default file is
expected.

`tests/golden/protocol_vs_legacy_differences.json` runs the same fixture
repositories through the Workflow's `next-action` (2.7.0, protocol mode)
and through 1.6.0's own decisions (2.6.0, legacy mode), and records where
they differ: exactly the seven differences D1-D7 of
[Protocol mode](automation.md#protocol-mode-workflow-27-and-later).
`tests.test_protocol_equivalence` asserts each by name, so a new
difference fails the suite until it is reviewed.

**How a release is admitted** (1.7.0 and later) is two-way, in
`managed_repo.inspect`. A release with a
`controller.workflow_contract.RELEASE_CONTRACTS` entry (2.5.1, 2.6.0) is
legacy mode, by exact release, as below. Any other release is admitted by
capability, with no code change: protocol mode when its installation
record's `managed` map lists `scripts/workflow_protocol.py` and `describe`
answers protocol major 1 (`controller.protocol.PROTOCOL_MAJOR`), else
refused (`no_protocol`, `unsupported_protocol_major`, or the older
reasons). A later protocol-mode release therefore needs no entry; to test
against one, vendor it and rerun the suite. A protocol answer is validated
against `controller/protocol_schema.json`, a byte-for-byte copy of the
2.7.0 schema whose sha256 a test pins. The record
is [ADR 0010](../adr/0010-orchestration-protocol-admission-by-capability.md).

**Admitting a future legacy-mode Workflow release** is a deliberate act in
a reviewed plan, never a version-string edit:

1. vendor it: `python3 tools/workflow_releases.py sync <release> --from ...`;
2. measure what changed against the previous release: the phase set, the
   command files and the user-only set, the state writers each automatic
   command declares (property 5), and anything the Controller reads from
   Workflow's files or asks Workflow's queries. For a release that runs
   queries, list the Git commands each query can run: the query's Git
   isolation (`workflow_contract._git_isolation`) relies on 2.6.0's queries
   never asking Git for a patch, a log, a checkout or a fetch, and on
   `post-index-change` being the only hook they fire
   (`workflow_contract._QUERY_HOOK_EVENTS`, which a hook the query fires
   refuses). `tests.test_workflow_contract.GitIsolationTest` pins both for
   2.6.0: it installs every hook githooks(5) names and runs the release's
   queries in place;
3. give it a `controller.workflow_contract.RELEASE_CONTRACTS` entry, with
   the query scripts' sha256 from the Manager manifest when it runs queries,
   and per-release writer declarations in `job.expected_outcomes_for` where
   its commands write differently;
4. add it to `VALIDATED_WORKFLOW_RELEASES`, generate its decision goldens,
   and run the whole suite, which runs every per-release inventory for it;
5. prove the migration from the previous release
   (`tests/test_workflow_release_migration.py` is the model).

The record of the 2.6.0 admission is
[ADR 0006](../adr/0006-workflow-release-admission-and-per-release-contracts.md).

## Adding a harness or a git host

**A harness** is one adapter, one converter and one captured sample:

1. Add a `HarnessAdapter` to `controller/harness.py` and to `ADAPTERS`: its
   name, reviewer name, family, roles, restriction abilities, usage reporting
   and pinned version. Extend the pinning tests in `tests/test_harness.py` so
   the objects that launch the harness cannot drift from the declaration.
2. Add a converter to `controller/usage_record.py` that builds a `UsageRecord`
   from the harness's own output, following the record's meanings (input
   excludes cached tokens, output includes reasoning) and returning `null` with
   quality `unknown` for any figure the harness does not give.
3. Capture a real sample under `tests/harness_contract/<harness>/` and test the
   converter against it. A derived sample is labelled as derived in its first
   line.

**A git host** is a `ForgeAdapter` in `controller/forge.py` (`FORGES`): its
`kind`, the constructor of the class implementing the `Forge` protocol and its
`ForgeCapabilities`. The registry's keys must equal `repo_policy.FORGE_KINDS`
(a test pins this), and an unregistered kind raises
`ForgeKindUnregisteredError`.

**Maintaining the price table.** `controller/prices.json` holds a `version`, the
`currency`, the `unit` (price per token, in the table's currency), the `date`,
the `source` and the prices per model identifier. A price change adds a new table
`version` with its source and date; it never edits history. A changed converter,
or a new version of a harness, is a new converter version. Stored records keep
the version they were computed under and are never rewritten.

## Design records

Every design decision behind the Controller is recorded either in an ADR or in
the reviewed plan of the milestone that made it. The
[documentation map](../README.md#design-decisions) lists them all.
