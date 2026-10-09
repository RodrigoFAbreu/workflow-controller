# Make the installed-release test version-agnostic: check the repository's own Workflow by capability and by its installation record (Revision 2)

Work item: `workflow-controller-installed-release-test-agnostic`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `5532cdd07d23e889ecafe8c94e66752868c9ab53` ("chore: move this repository to Workflow 2.9.0 with automatic gates (#33)"), the tip of `main`, passed explicitly as `/milestone-plan 5532cdd…`.
Lifecycle authority: this repository's installed Workflow **2.9.0**, automatic gates (no `docs/ai-workflow/GATE_POLICY.json`). This is the first milestone run that way.
Roadmap slot: `docs/ROADMAP.md` step **C9d**, known follow-up 16.
Released baseline: `workflow-controller 1.7.1`. This milestone releases nothing.
Pull request title: `test: check the installed Workflow by capability and installation record, not by a vendored tree`
(`test` maps to `none` in `.workflow-controller/policy.json`, so no release is cut.)

## Goal

`tests/test_workflow_releases.py`'s `InstalledReleaseTest` still ties this repository's own installed Workflow to a vendored tree. C9c needed that tree before the repository could move to 2.9.0. Any later Workflow move has the same problem: the test fails until the new release is vendored and a test passes over it. Vendored trees exist to test the Controller against *other* Workflow releases (property inventories, goldens, migration). Checking this repository's own installation against one is a leftover from before C9.

After this milestone a Workflow that speaks protocol major 1 can be installed into this repository by Workflow Manager with no change to any test or vendored tree, and a protocol major 2 is still refused.

1. The installed release is read from `.workflow-manager/installation.json` and admitted through the Controller's own rule, with no vendored copy and no version literal.
2. The installed files are checked against the digests that Workflow Manager recorded for them in the same `installation.json`, so a hand-edited or partly updated installation still fails the suite.
3. The vendored trees keep their own tests (`CheckTest`, sync, the protocol-admission tests). Nothing about them changes.

Out of scope: `controller/` (the admission rule is reused, not changed), `VALIDATED_WORKFLOW_RELEASES`, `SUPPORTED_WORKFLOW_LINES`, vendoring any release, the 1.x-minor tolerance in the schema (C10, follow-up 17), a release.

## Decisions

- **D1. Admission runs every non-Manager step of `inspect`, in order, without Workflow Manager.** `managed_repo.inspect` (controller/managed_repo.py:390-433), after Manager's `verify`/`status`, does three things; the test does the same three, in the same order, on the real repository, and nothing else:
  1. the record is read through `manifest = managed_repo._read_manifest(REPO_ROOT)`, never a raw `json.loads`, so the `schema_version`, `workflow_version` and `profile` checks apply too;
  2. the arm is chosen by `manifest["workflow_version"] in workflow_contract.RELEASE_CONTRACTS`, exactly as `inspect` does (not by `VALIDATED_WORKFLOW_RELEASES`, a separate constant that equals it today but is not what production branches on): a member goes to `managed_repo._check_workflow_version(release, REPO_ROOT)`, any other release to `managed_repo._check_protocol_admission(release, REPO_ROOT, manifest)` (the `managed` map lists `scripts/workflow_protocol.py` and the installed `describe` answers major 1);
  3. `manifest["profile"]` must be in `managed_repo.SUPPORTED_PROFILES`, else the test fails as `UnsupportedInstallProfileError` does in production.

  The helpers are private; the test file already imports `managed_repo` and the unit tests for the gate call them. This needs no Manager binary, so it runs in CI, where the live-repository test (`test_real_workflow_manager_admits_this_repository`) is skipped, and it cannot admit an installation `inspect` would refuse for any reason other than Manager's own `verify`/`status`. The live test keeps `managed_repo.inspect` and is unchanged except that its comment no longer says the release must have a vendored tree.
- **D2. Integrity from the installation record, not from a tree.** `installation.json`'s `managed` map lists every installed Workflow file with its `sha256` and `executable` flag. The test checks that each listed file exists, equals that digest and has that mode, and that the installed `.claude/commands/*.md` set equals the listed commands. This catches exactly what the old equality test caught (a stray, edited or missing file) for any release, because the record is rewritten by every Manager update. It does not prove the record matches the published release; that is Workflow Manager's `verify`, which the live test runs, and the vendored trees' `RELEASE.json` check, which is unchanged.
- **D3. No guard for "a vendored tree exists".** The old test required one; the new test does not look for one, and does not compare to one when present. Two sources of truth for the installed bytes would let a vendored tree and the installation record disagree and still pass one of them.
- **D4. Negative tests on a synthetic installation.** Because the real installation is always valid, the check functions are written to take a root and a manifest, and the tests run them on a disposable copy of three files with a manifest built in the test: a modified file, a missing file and an extra command file each fail with a message naming the path; a protocol-major-2 `describe`, a release with no protocol script and a `profile` outside `SUPPORTED_PROFILES` are refused by the same three-step admission that production uses. The repositories come from `fixtures.install_workflow_release`, `fixtures.write_installation_manifest` and `fixtures.git_init`/`fixtures.commit_all`, the helpers `tests/test_managed_repo.py` already uses; the `_protocol_target` builder there is private to that module, so the new tests use a small builder of their own in `tests/test_workflow_releases.py` (the major-2 case sets `PROTOCOL_MAJOR = 2` in the installed `scripts/workflow_protocol.py` the way `test_protocol_major_2_is_refused` does) rather than moving it.
- **D5. The sync/check tests keep their own releases.** `CheckTest.test_an_admitted_release_without_a_tree_is_reported` concerns vendored trees and is left alone, as are `InstalledReleaseTest`'s `test_every_vendored_tree_is_validated_or_ships_the_protocol` and `test_a_tree_that_is_neither_is_not_admitted`. Only the two `InstalledReleaseTest` cases that read `installation.json` (`test_the_installed_release_is_admitted` and `test_the_installed_files_equal_the_vendored_tree`) change.
- **D6. No change in `controller/`.** The milestone changes `tests/`, two documentation pages and the roadmap. `check_docs`, the full suite and `tools/workflow_releases.py check` must be green.

No "Open decision" row in `docs/TECHNICAL_DECISIONS.md` is touched: that document describes the Android application's prototype, not the Controller.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The installed-release test admits the repository's own Workflow through the Controller's capability rule and checks its files against installation.json, with no vendored tree and no version literal; modified, missing, extra, non-protocol, protocol-major-2 and unsupported-profile cases are refused by name | - | 2 | 1 |
| CP2 | Documentation and roadmap: development, compatibility and update pages no longer imply a vendored tree is needed before a Workflow move, C9d and follow-up 16 are marked complete, check_docs and the full suite green | CP1 | 1 | 1 |

### CP1 -- The installed-release test

- `tests/test_workflow_releases.py`: replace `test_the_installed_release_is_admitted` with a test that runs D1's three-step admission (`_read_manifest`, the `RELEASE_CONTRACTS` arm, the profile check) on the real repository; replace `test_the_installed_files_equal_the_vendored_tree` with D2's installation-record check; keep `test_every_vendored_tree_is_validated_or_ships_the_protocol` and `test_a_tree_that_is_neither_is_not_admitted` as they are. The helpers that locate a vendored tree for the installed release are removed from the class.
- Tests for D4: a modified, a missing and an extra file are each reported by name; a release without a protocol script that is not validated, a protocol script answering major 2, and an unsupported `profile` are refused by the same admission.
- `tests/test_managed_repo.py`: comment-only update on the live test (D1).
- Verified by running the changed module on the current tree, and by a temporary copy of the repository with `tests/workflow_releases/2.9.0/` deleted (the installed release then has no vendored tree): the changed tests pass there and the old tests fail there. `python3 -m unittest tests.test_workflow_releases tests.test_managed_repo` passes.

### CP2 -- Documentation and roadmap

- `docs/guide/development.md` ("Workflow release trees"): the paragraph that says this repository's installed tree is checked for equality with the vendored tree and that the test admits the release through the vendored listing is replaced by D1 and D2; the sentence "updating the installed tree through Workflow Manager needs no code change once the release's tree is vendored" becomes "needs no code change".
- `docs/compatibility.md` and `docs/update.md`: any sentence that implies a vendored tree must exist before this repository moves is removed (read both before editing; edit only what states it).
- `docs/ROADMAP.md`: the C9d row and follow-up 16 are marked complete; the "Next" list names C8.
- `python3 tools/check_docs.py` and the full suite on the branch, serially and not backgrounded: green apart from the two Git 2.56 trailer failures locally (follow-up 12); "green" means CI for those two.

## Requirements

| id | requirement | checkpoints |
|---|---|---|
| R1 | The repository's own installed release is admitted through the Controller's capability rule with no vendored tree and no version literal, and a non-protocol release, a protocol-major-2 release or an unsupported profile is refused by the same admission | CP1 |
| R2 | The installed Workflow files are checked against the digests and modes in `installation.json`, and a modified, missing or extra file fails by name | CP1 |
| R3 | No test requires a vendored tree for the installed release; the vendored-tree tests are unchanged and pass | CP1 |
| R4 | The guide, compatibility and roadmap pages agree, and `check_docs` and the full suite are green | CP2 |

## Risks

- **Private helpers.** D1 calls three underscore-named helpers (`_read_manifest`, `_check_workflow_version`, `_check_protocol_admission`). If `managed_repo` changes their signature the test fails loudly, which is the intended coupling to the production gate; a public wrapper is not added because the milestone does not change `controller/`.
- **Record equals bytes, not the release.** D2 trusts the record. A Manager bug that records a wrong digest is out of scope here; the vendored `RELEASE.json` check and Manager `verify` cover it.
- **First automatic-gate milestone.** Approvals are recorded by `/satisfy-gate`; a reviewer model line may be required in the review request. The plan-stage `REVIEW_REQUEST.md` asks for it.
- **Artifact declaration.** The generator's default template classifies none of `controller/`, `tests/`, `tools/` or the `docs/*.md` pages (known follow-up 10). The declaration is C9c's reviewed classification with this item's ids, so every path the checkpoints name is classified at both stages: `tests/` and `docs/guide/` are protected prefixes, `docs/compatibility.md` and `docs/update.md` are protected by exact path.
