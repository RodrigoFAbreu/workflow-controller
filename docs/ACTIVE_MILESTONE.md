# Active Milestone

## Status

**In functional review.** `workflow-controller-codex-build-pin` (RB3, issue #168; plan `docs/ai-workflow/CONTROLLER_CODEX_BUILD_PIN_PLAN.md`)
moves the pinned Codex boundary to `codex-cli 0.163.0`.

| checkpoint | state |
| --- | --- |
| CP1 The 0.163.0 feature capture | complete: `features_list.0.163.0.txt` (operator's 2026-10-26 probe) replaces the 0.162.1 file, README digest and provenance updated |
| CP2 Move the pin | complete: `PINNED_VERSION` and the adapter pin are `codex-cli 0.163.0`; `api_key_cyber_access_programs`, `cli_daybreak` and `code_mode_interrupt` join `DISABLED_FEATURES`; the fake, the contract tests and the generated harness table follow |
| CP3 The guides and the roadmap | complete: the guides name 0.163.0 and say a refusal follows a changed checked surface, not the version string; `common-problems.md` covers the next-update case; the roadmap marks RB3 done |

## Next action

The implementation is complete and technically approved (implementation revision 2); the functional-review checklist below is ready. Put findings in `.ai-review/workflow-controller-codex-build-pin/feedback/FUNCTIONAL_REVIEW.md`; if testing is clean, `/accept-milestone`.

## Functional review checklist

**Functional round 1 (automated tester):** flow 8 failed on the docs, a usability defect (the harness and worker guides named no 0.163.0 and no manual-review fallback; the runtime bullet had no explicit fallback). Fixed in `docs/guide/workers.md`, `harnesses.md` and `runtime.md`; flows 1-7 and 9-10 passed and need no re-test. Re-test flow 8. Nothing deferred to a remediation child.

Scope: RB3 (#168) only, the pin moves to `codex-cli 0.163.0`. Everything runs offline in the read-only clone against the repository's fake Codex; no real `codex`, `claude`, `gh` or network is needed, and no flow writes inside the clone. Run each command from the clone root with `export PYTHONDONTWRITEBYTECODE=1` (no `__pycache__` writes).

**Setup:** none beyond `python3` and the clone. No feature flags, no seeded data (the captures under `tests/harness_contract/codex/` are the data).

| # | Flow | Expected |
| --- | --- | --- |
| 1 | `python3 -c "from controller import review, harness; print(review.PINNED_VERSION); print(harness.ADAPTERS['codex'].pinned_version)"` | Both lines print `codex-cli 0.163.0`. |
| 2 | `ls tests/harness_contract/codex/; sha256sum tests/harness_contract/codex/features_list.0.163.0.txt; grep -n features_list tests/harness_contract/codex/README.md` | `features_list.0.163.0.txt` exists and no `features_list.0.162.1.txt`; the digest is `6dd1708362bdc7de0a50f7e4ad9087410fbfe42df7793bf9977cf6d2103ff810` and equals the README row; the README names 2026-10-26 and 0.163.0 for the feature list and keeps 0.162.1 for the other captures. |
| 3 | `python3 -c "from controller import review; [print(n, n in review.DISABLED_FEATURES, n in review.TOOL_ALLOWLIST) for n in ('api_key_cyber_access_programs','cli_daybreak','code_mode_interrupt')]"` | Each line ends `True False`: disabled, not allowlisted. |
| 4 | `python3 -c "from pathlib import Path; from controller import review; a=review.argv('exec',binary=Path('/x/codex'),tree=Path('/t'),verdict=Path('/v.md'),tmpdir=Path('/m')); [print(n, a[a.index(n)-1]) for n in ('api_key_cyber_access_programs','cli_daybreak','code_mode_interrupt')]"` | Each name is preceded by `--disable`. |
| 5 | Fail-closed guard: `python3 -m unittest -v tests.test_review -k 0_163_0` | Both `test_a_feature_new_in_0_163_0...` and `test_the_features_new_in_0_163_0...` pass: leaving any one of the three names enabled is refused naming it (`ReviewLaunchRefusedError`, `check: features`), and each is disabled, not allowlisted, and carried by `--disable` in the argv. |
| 6 | `python3 -m unittest tests.test_review tests.test_fake_codex_contract tests.test_harness tests.test_docs` | `OK`, 224 tests. |
| 7 | `python3 tools/check_docs.py` and `python3 tools/harness_table.py --check` | Both pass; the generated `codex` row in `docs/guide/harnesses.md` shows `codex-cli 0.163.0`. |
| 8 | Docs read-through: `docs/guide/workers.md` (harness table and the sentence after it), `docs/guide/harnesses.md` (paragraph before the model-and-effort text), `docs/guide/runtime.md` (`reviewer.harness` bullet), `docs/common-problems.md` (exit 30, "After a Codex update") | Each names 0.163.0; none claims a refusal on any version other than the pin; the refusal is described as following a changed checked surface (feature, layer, state entry, flag) and naming it, never a version string; an update that changes none is accepted; the manual-review fallback until the pin is re-measured is stated. |
| 9 | `git grep -n '0\.162\.1' -- docs/guide docs/common-problems.md docs/ROADMAP.md` | Only `docs/guide/runtime.md` (the dated "recorded on 0.162.1" note). The ADR and release note keep 0.162.1 as history. |
| 10 | `grep -n 'RB3' docs/ROADMAP.md` | RB3 is marked done and H7 is next. |

**Known limitations / out of scope:** the real Codex is not run, so the live check is the first Controller-run Codex review after the 1.22.2 release; only the feature list and the `--disable` acceptance were re-measured on 0.163.0 (the other captures are the 2026-10-15 measurements on 0.162.1, by design, README says so); no version probe, `verify-codex` or version range (C10g-2, H7); `reviewer.harness=codex` is not enabled by this milestone; two suite tests fail locally for known reasons (Git 2.56 trailer rule, 2.5.1 plan-stage golden drift), unrelated to RB3.
