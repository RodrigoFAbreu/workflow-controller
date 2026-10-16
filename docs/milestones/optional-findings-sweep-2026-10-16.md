# Minor review findings: the backlog sweep of 2026-10-16

[Back to the documentation map](../README.md)

Since Workflow 2.12.0 a review round whose findings are all minor (Optional) is an `APPROVE`, and
nothing required those findings to be acted on. They were kept only in the local, gitignored review
archive (`.ai-review/archive/`). On 2026-10-16 the owner decided that every minor finding is
**fixed**, **dropped** with a reason, or kept as a **follow-up**: a GitHub issue labelled
[`review-follow-up`](https://github.com/RodrigoFAbreu/workflow-controller/labels/review-follow-up).
From then on this happens at each milestone's acceptance and is recorded in its completed-milestone
narrative; the Workflow side is workflow#34. This page records the one-time sweep of the findings
archived before that decision. Like the other history pages, it is not updated afterwards.

**Scope.** Every `## Optional findings` entry and every `## Missing tests` entry that is not just
"None." in the 45 archived `APPROVE` verdicts, checked against `main` at `45e9325`. `REVISE`
verdicts were skipped, because their findings were applied by the next revision. The Usability,
Architecture and Migration sections were not in scope.

Work items are named by their roadmap step: C1 squash-merge-tag-versioning, C2 ci-reliability, C3
settings-and-telemetry, C4 auto-merge-release-wait, C5 signalhub-notifications, C6
automated-lifecycle-scenarios, C7 codex-reviewer, C8 usage-budget, C8b usage-limit-mid-job, C9
orchestration-protocol-v1, C9b protocol-warn-status, C9c workflow-2-9-0-move, C9d
installed-release-test-agnostic, C12a test-leftover-lifetime, D1 documentation-reorganisation.
"FU n" is item n of the roadmap's [known follow-ups](../ROADMAP.md#known-follow-ups-carried-forward).
"MT" is a Missing-tests entry.

## Result

| Disposition | Entries (after merging duplicates) |
|---|---|
| fixed | 6 |
| already tracked | 12 (FU 18, 19, 20, 21, 22 and 29) |
| dropped | 20 |
| follow-up | 5, in issues [#55](https://github.com/RodrigoFAbreu/workflow-controller/issues/55) to [#58](https://github.com/RodrigoFAbreu/workflow-controller/issues/58) |
| **total** | **43** |

One more issue, [#59](https://github.com/RodrigoFAbreu/workflow-controller/issues/59), is a new case found during the sweep: a roadmap step id is back on a
user page.

| Issue | Title |
|---|---|
| [#55](https://github.com/RodrigoFAbreu/workflow-controller/issues/55) | Stale wording about the installed-release test |
| [#56](https://github.com/RodrigoFAbreu/workflow-controller/issues/56) | The test-leftover guard misses some process starts and some cleanups are group kills |
| [#57](https://github.com/RodrigoFAbreu/workflow-controller/issues/57) | The unmerged-index usage-limit test does not change the blob for DD and covers two of seven statuses |
| [#58](https://github.com/RodrigoFAbreu/workflow-controller/issues/58) | No test for a lost merge reply after a squash onto a moved trunk |
| [#59](https://github.com/RodrigoFAbreu/workflow-controller/issues/59) | A roadmap step id is back on a user page, and the docs check does not catch it |

## Every finding


| # | Source (archive file, id) | Finding | Disposition | Justification (current main) |
|---|---|---|---|---|
| 1 | C6 `external-implementation-review-rev8-APPROVE.md` O2 | `$WORKFLOW_CONTROLLER_SETTINGS` and some missing locations lack mount protection | already tracked | FU 29 lists it word for word |
| 2 | C6 impl rev8 O4 | namespace watchdog allows `timeout + 15`; a degraded worker has no deadline of its own after the parent dies | already tracked | FU 29 |
| 3 | C6 impl rev8 O6 | most negative checks accept a generic assertion failure | already tracked | FU 29 |
| 4 | C6 impl rev8 O7 | registry tests import selftest scenario code on the host | already tracked | FU 29 |
| 5 | C6 impl rev8 O8 | ignored inputs, special index flags, and external symlink targets can evade provenance checks | already tracked | FU 29 |
| 6 | C6 impl rev8 O9 | flow id `index` collides with `index.json` | already tracked | FU 29. Still present: `tools/run_scenarios.py:42` `INDEX_NAME = "index.json"`, and the flow ids follow the Workflow's `FLOW_ID_RE` (`tests/scenarios/__init__.py:35`) |
| 7 | C6 impl rev8 O10 | configured Git clean filters can run during dirty checks | already tracked | FU 29 |
| 8 | C6 `external-plan-review-rev10-APPROVE.md` O1 | runner-exemption wording in CP5 | dropped | Plan wording only. Plans are historical records, and the implementation was reviewed and accepted afterwards |
| 9 | C6 `external-plan-review-rev6-APPROVE.md` O1 | remove the superseded `job-before` wording in D5(4) | dropped | Plan wording only (as #8) |
| 10 | C6 plan rev6 MT ("CI-image enforcement remains to be demonstrated"), C6 `external-plan-review-rev8-APPROVE.md` MT, C7 `external-implementation-review-rev7-APPROVE.md` MT ("CI must exercise them") | the namespace scenarios must really run on CI, not be skipped | fixed | `tests/scenarios/kit.py:71-79` `isolation_gate` turns "isolation unavailable" into a failure when `CI` is set, and `.github/workflows/validate.yml:62-63` allows unprivileged user namespaces. A green CI run therefore means the scenarios ran. CI logs were not read |
| 11 | C4 `external-impl-review-rev6-APPROVE.md` MT | no automated regression for a lost merge reply plus lagging reads after a squash onto a moved trunk | **follow-up ([#58](https://github.com/RodrigoFAbreu/workflow-controller/issues/58))** | The lost-reply and lagging-read tests (`tests/test_pull_request_lifecycle.py:2394-2475`) all assert `first_parent_log(before, m) == [m]`, so the trunk never moved. The moved-trunk test (`:2351`) has no lost reply. No test combines the two |
| 12 | C2 `external-impl-review-rev1-APPROVE.md` LOCAL-IMPL-R1-001 | `--write-selected` inside `--results-dir` makes a later `aggregate` see duplicates and refuse | dropped | Still possible (`tools/run_tests.py:265-296` has no guard). It fails closed with an error naming both paths (`tools/test_shards.py:2211-2214`). CI writes to a sibling directory (`validate.yml:113,116`). This is operator misuse with a loud failure, so a guard adds little |
| 13 | D1 `external-implementation-review-rev16-APPROVE.md` F5 | `(roadmap C10)` on `docs/guide/troubleshooting.md:402` | fixed (but see [#59](https://github.com/RodrigoFAbreu/workflow-controller/issues/59)) | Removed before the D1 merge (`git log -S "roadmap C10"` → 48c386a). A new step id has since appeared on a user page ([#59](https://github.com/RodrigoFAbreu/workflow-controller/issues/59)) |
| 14 | D1 `external-implementation-review-rev18-APPROVE.md` #1 | the troubleshooting `warn` remedy implies adoption clears every `warn` | fixed | b2323e1 (C9b): `docs/guide/troubleshooting.md:440-447` now says "Some causes differ", names the `--run-ref` and lowering cases, and links common-problems |
| 15 | D1 `external-implementation-review-rev20-APPROVE.md` #1 | adoption-timing advice is only indirect on README, glossary, common-problems, and troubleshooting | fixed | b2323e1 added direct pointers: `docs/glossary.md:50` ("which also has the adoption timing"), `docs/common-problems.md:17` ("the timing advice is under Human gates"), `docs/guide/troubleshooting.md:409`. `README.md:116` links `run.md#steps` (48c386a). FU 11 records this as taken up by C9b |
| 16 | C9d `external-implementation-review-rev1-APPROVE.md` #1, #2; C9d `external-plan-review-rev2-APPROVE.md` bullet 1 | stale `tests/test_workflow_releases.py` docstring ("installed files equal its vendored tree"); the development guide says tests never read the installed Workflow and does not mention the installed protocol's admission | **follow-up ([#55](https://github.com/RodrigoFAbreu/workflow-controller/issues/55))** | Still stale: `tests/test_workflow_releases.py:4-8` and `docs/guide/development.md:370-371`. Both contradict `InstalledReleaseTest` (`tests/test_workflow_releases.py:182-202`) and the guide's own lines 389-396 |
| 17 | C9d plan rev2 bullet 2; C9d impl rev1 MT ("synthetic-future-release test was not added") | prove permanence with a synthetic future installed release | dropped | Shown in practice: the Workflow 2.11.0 (#50, f4323ea) and 2.12.0 (#54, 45e9325) moves changed no file under `tests/`, and `managed_repo` tests cover a bumped release (as the reviewer noted) |
| 18 | C9d plan rev2 MT; C9d impl rev1 MT ("true-to-false remains optional") | executable-flag mismatch cases in both directions | dropped | The check is a symmetric `!=` (`tests/test_workflow_releases.py:172`). The existing test flips `workflow_state.py` from non-executable to executable (`:236-240`), and the other direction exercises the same line |
| 19 | C9 `external-plan-review-rev10-APPROVE.md` [apparatus] | say that an unreadable managed file leaves the pending record untouched, and tell the operator to repair the installation and retry `resume` | fixed (in substance) | Code: `controller/job.py:3114-3124` ("an unreadable map is unknown, not a difference: the record is left alone") and `:3089-3092`. Docs: `docs/guide/troubleshooting.md:314-325` (restore with Manager `update`/repair; an unchanged-digest job "still refuses with the drift (exit 20) and is left untouched"). The docs do not name the "unreadable file" case separately. Too minor to reopen |
| 20 | C9 `external-plan-review-rev11-APPROVE.md` [apparatus] | revision note says the mapping changed only in `plan_revision` | dropped | Plan wording only |
| 21 | C9b `external-plan-review-rev2-APPROVE.md` O1 | narrow R4's "byte-identical" promise | dropped | Plan wording only |
| 22 | C9b plan rev2 MT | direct baseline-versus-branch comparison of no-warning decision output | dropped | The decision goldens (`tests/golden/plan_stage_decisions*.json`, `external_implementation_review_decisions*.json`, `protocol_vs_legacy_differences.json`) pin ordinary decision output, and `tests/test_protocol_decision.py:509` pins "without a warn the evidence is unchanged" |
| 23 | C9b `external-implementation-review-rev2-APPROVE.md` MT | end-to-end coverage of warn-bearing `no_progress_repeated`/`decision_unstable` through job recording and `explain` | dropped | Helper-level coverage exists (`tests/test_protocol_job.py:418`, `tests/test_protocol_decision.py:544,563,698`), and the reviewer verified the end-to-end paths. C10 (FU 17) reworks closed-enum and advisory handling anyway |
| 24 | C5 `external-implementation-review-rev6-APPROVE.md` #1 and `-rev7-APPROVE.md` #1, plus both MT "sender cleanup" | the sender thread waits forever, and `uninstall` only drops the reference | already tracked | FU 20. Still present: `controller/notify.py:619-630` (`self._queue.get()` with no timeout) and `:732-734` |
| 25 | C5 impl rev6/rev7 #2 | usage notifications omit `run_id`, so outcomes go to the runtime-wide log | already tracked | FU 21. Still present: `controller/usage.py:681` calls `notify.send(...)` without `run_id=` |
| 26 | C5 impl rev6/rev7 #3 | plan-ready title omits the revision | already tracked | FU 22. Still present: `controller/job.py:5050` `f"Plan ready for review: {work_item}"` |
| 27 | C5 impl rev6/rev7 MT | terminal-job pending-accounting display sweep | dropped | The reviewer verified it independently ("The terminal sweep and real-signal behavior were independently verified here"). Low value |
| 28 | C5 impl rev6/rev7 MT | a committed real-signal close-to-CLI-finally regression | dropped | Verified by the reviewer's probe. Whether a committed test exists was not checked. Low value while lane notifications stay off (no key) |
| 29 | C5 impl rev6/rev7 MT | actual re-attachment/reconciliation recovery of notifications | dropped | The recovery rules are unit-tested (`tests/test_notifications_wiring.py:747-815`). Only the full resume-path wiring is untested. Low value |
| 30 | C5 `external-plan-review-rev16-APPROVE.md` Optional | CP3 names the wrong fallback timestamp | dropped | Plan wording only |
| 31 | C5 plan rev16 MT | tests for an intervening retry pause before rollover, three reservations-only episodes, and an exact five-hour gap | dropped | This only affects how notifications are deduplicated (`controller/usage.py:612-652`, "never steers usage"). Both sides of the five-hour rule are tested (`tests/test_notifications_wiring.py:411-425`, `600 + 5*3600 + 1`). Optional; could be added if this code is touched again |
| 32 | C12a `external-implementation-review-rev3-APPROVE.md` #1 | the leftover-inventory scan misses helper-built `subprocess.run(argv, timeout=600)` without a Controller marker, an imported `check_call`, and shell FIFO reads | **follow-up ([#56](https://github.com/RodrigoFAbreu/workflow-controller/issues/56))** | Still present: `tests/process_fixtures.py:160-190`. A `timeout=` call without a marker returns `None` (`:173-175`). A bare `check_call` has owner `None` and matches no branch (`:170`). `_LOOP_TEXT` (`:144`) does not match `read < fifo` |
| 33 | C12a impl rev3 #2 | new guard tests clean up with `kill_group` instead of D16's exact-pid cleanup | **follow-up ([#56](https://github.com/RodrigoFAbreu/workflow-controller/issues/56))** | Still present: `tests/test_resume.py:1264,3642` and `tests/test_cli.py:2153` (`addCleanup(process_fixtures.kill_group, ...)`). C7 later added another at `tests/test_fake_codex_contract.py:275` |
| 34 | C12a impl rev3 #3 | incident-replay evidence incomplete in the bundle | dropped | Bundle evidence for an accepted milestone (b32754c). It is historical, and the reviewer replayed the incident independently |
| 35 | C12a impl rev3 #4 | `TEST_RESULTS.md`/`REVIEW_REQUEST.md` name older revisions | dropped | Review-bundle files for an accepted milestone, not kept up to date afterwards |
| 36 | C12a impl rev3 MT | automated killed-test and delayed/failed-helper-startup regressions | dropped | The incident replay is manual by design (plan D7), and the reviewer reproduced both cases. The orphan-lifetime contract has unit tests |
| 37 | C12a `external-plan-review-rev11-APPROVE.md` #1 | hold-file expiry must not publish readiness without the release | dropped | Literally still true: on expiry `tests/fake_claude.py:1411-1421` falls through and writes the ready file. A live test can never see it, though: `wait_fake_ready` gives up after 30 s (`tests/process_fixtures.py:449`), far below the 300 s hold bound, and the orphan watchdog ends an abandoned fake |
| 38 | C12a plan rev11 #2 | name the parent guard's earliest startup window as an accepted exception | dropped | Plan wording only |
| 39 | C8 `external-implementation-review-rev13-APPROVE.md` #1, plus MT | run/repository accumulators can pass `_NUMBER_LIMIT` with crafted totals | already tracked | FU 18. Still present: `controller/usage.py:775,822` add with no clamp against `:159-165` |
| 40 | C8 `external-implementation-review-rev15-APPROVE.md` #1, plus MT "add phase-1 reattachment coverage" | the unreadable-usage-record warning is printed twice on `resume` reattachment | already tracked | FU 19. Still present: `_usage_account` warns on every call (`controller/job.py:6055-6068`), and there is no once-per-resume dedupe |
| 41 | C8 `external-plan-review-rev11-APPROVE.md` | qualify "missing" in D6's "never loses accounting" | dropped | Plan wording only. The phrase was not carried into `docs/` or `controller/` (grep) |
| 42 | C8b `external-implementation-review-rev7-APPROVE.md` Optional, plus MT "retain actual-resume coverage for the all-seven-status matrix" | the `DD` branch never changes its blob, and only DU/DD are tested, live only | **follow-up ([#57](https://github.com/RodrigoFAbreu/workflow-controller/issues/57))** | Still present: `tests/test_job.py:2222-2243`. With `stages=(1,)`, `blob = blobs[0] if number == 1` stays `blobs[0]`, so `stage(blobs[2])` restages the same blob. Only `("DU", (1, 3)), ("DD", (1,))` are covered, and not through `resume` |
| 43 | C9c `external-implementation-review-rev1-APPROVE.md` | `docs/ACTIVE_MILESTONE.md:77` names `/milestone-plan` as the next action | fixed (obsolete) | `docs/ACTIVE_MILESTONE.md` has been rewritten for later milestones (now C8b complete) and no longer mentions C9c |

### Entries that are not findings (listed for completeness)

These "Missing tests" entries open with "None" and only add context: C6 impl rev8; C6 plan
rev10; C2 impl rev1 ("live rerun probe remains an acceptance check", done at acceptance); C2 plan
rev3; C7 impl rev8, rev9, plan rev15; D1 plan rev5; C9 impl rev4; C9b impl rev4; C3 impl rev3,
plan rev14; C1 plan rev3; C12a plan rev11; C8 plan rev11; C8b impl rev8, plan rev5; C9c plan rev3;
C9d impl rev1 ("None required." line; its optional remarks are rows 17-18). C6 plan rev6/rev8 and C7
rev7 add a CI precondition (row 10). The Optional entries "None new." (C9b impl rev2, C3 impl rev3)
and "None within this bounded review." (C8b impl rev8) are not findings either.

## Notes

- Six FU items (18-22, 29) already hold 12 of these entries. They are all still present in the code,
  checked at the lines cited above.
- None of the follow-ups needs C14, C10, C11 or O1-O7. Only [#56](https://github.com/RodrigoFAbreu/workflow-controller/issues/56) has a natural home (C12). O4 ("follow-ups
  and optional notes become roadmap items") is the roadmap step that would automate this triage.
- Not verified: CI logs (row 10; the isolation gate makes a skip impossible on CI by
  construction), and whether a committed real-signal notifier-close test exists (row 28).
