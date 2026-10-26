# Codex build pin: the Controller-run Codex review accepts codex-cli 0.163.0 (Revision 4)

Work item: `workflow-controller-codex-build-pin`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `47453526deb37f06396a103c0623652ccf8c6863` ("docs: release notes and history row for 1.22.1 (#178)"), the tip of `main`, passed explicitly as `/milestone-plan 4745352…`.
Lifecycle authority: this repository's installed Workflow **2.20.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` "Next, in order" item 1, RB3 (H7 and the rest of the harness-neutral roles epic follow).
Released baseline: `workflow-controller 1.22.1`. The change makes a launch that was refused succeed, so the squash commit is a `fix:` change and releases a patch (1.22.2).
Pull request title: `fix: the Controller-run Codex review accepts codex-cli 0.163.0 (RB3, #168)`

## Goal

Close [#168](https://github.com/RodrigoFAbreu/workflow-controller/issues/168). The first live use of `reviewer.harness=codex` (the H6 trial, 2026-10-25) failed in three seconds: the installed Codex updated itself to `codex-cli 0.163.0`, whose `codex features list` reports three features enabled that are in neither `TOOL_ALLOWLIST` nor `DISABLED_FEATURES` (`api_key_cyber_access_programs`, `cli_daybreak`, `code_mode_interrupt`), so `check_features` refuses the launch, as designed. The version string itself is not checked: `review.PINNED_VERSION` and the adapter's `pinned_version` only record which build the captures came from (nothing in `controller/` reads them at launch), so drift is caught only where it changes a checked surface (an enabled feature, a layer, a state entry, a flag).

One concern only: **move the pinned Codex boundary to the build that is installed today, from the operator's free-probe evidence of 2026-10-26.** No new code path, no change to what a review does, no change to a setting, an exit code or a gate. The refusal itself is correct and stays.

## Decisions

Each decision states one principle.

- **D1. The pin moves on the operator's free-probe evidence; nothing live is repeated.** The Claude Code implementer never runs the real `codex` and never reads the operator's Codex home. On 2026-10-26 the operator ran the free part of the `tests/harness_contract/codex/README.md` pre-step against the installed `codex-cli 0.163.0` (no model call): `codex features list` in an empty `CODEX_HOME`, and `codex features list --disable <name>` for each of the three new names (exit 0, then `false`; an unknown name still exits 1). That evidence is posted on issue #168 (the planning-guidance comment, the full feature list in a collapsed block) and is the only new measurement. CP1 stores the feature list as `features_list.0.163.0.txt` (the output as posted, header warning line removed) and removes `features_list.0.162.1.txt`. Every other capture (`exec_argv.json`, `rollout_excerpt.jsonl`, `rollouts_multi.jsonl`, `stderr_shapes.txt`, `compose.txt`, `state_entries.txt`, `env.txt`, `flags.txt`, `sandbox_probe.txt`) is retained byte for byte, so its digest does not change and `rollouts_multi.jsonl` stays unpinned, as now. The historical captures are not rewritten.
- **D2. All three new features are disabled, unconditionally.** `api_key_cyber_access_programs`, `cli_daybreak` and `code_mode_interrupt` go to `DISABLED_FEATURES` and none to `TOOL_ALLOWLIST`: the default is fail-closed, there is no evidence any is inert, and a review needs none of them. There is no allowlisting fallback; moving one later is a separate, evidenced change. `--disable` accepts each name on 0.163.0 (the probe above), so the argv carries three more `--disable` pairs.
- **D3. The version and its copies move together.** `review.PINNED_VERSION`, the Codex adapter's `pinned_version` in `controller/harness.py` (a test already asserts the two are equal), the fake Codex's `VERSION` and its default features capture, and the capture file name all become 0.163.0. Nothing else about the adapter changes (its models and efforts are H8's and stay).
- **D4. A later Codex update fails closed only where it changes a checked surface, and the documentation says so.** This milestone adds no version check, no `verify-codex` and no version range: those are C10g-2 and H7, which also decide whether `fails_closed_on_unknown_version` should mean a version probe. The documentation says what an operator sees on the next update (`ReviewLaunchRefused` naming the check and the feature, layer, state entry or flag that changed; an update that changes none of them is accepted) and that the lane runs the Codex review by hand until a person re-measures the pin. CP3 corrects the `docs/guide/workers.md` sentence that claims a refusal on any version other than the pin, and words the `docs/guide/harnesses.md` prose around its generated "Unknown version refused" column the same way; the declaration itself (`fails_closed_on_unknown_version=True`) is not changed here.
- **D5. What is and is not re-verified on 0.163.0 is stated, and a missing capture is a stop.** The README states that only the feature list and the `--disable` acceptance were re-measured on 0.163.0, and that the seven real `exec` runs, the sandbox probe, the composing command, the state entries, the environment and the flag table remain the 2026-10-15 measurements on 0.162.1; the first Controller-run Codex review after the release is the live check, and the launch still fails closed on further drift. If the feature list cannot be read from the issue, CP1 stops and reports that; it never derives it from the 0.162.1 file.

## Out of scope

`verify-codex` and verify-on-first-use (C10g-2), a version range or build-digest cache (H7), enabling `reviewer.harness=codex` in the shared settings (the operator's choice after this ships), the Codex tester and worker (C10g, H4), and any change to the review prompt, the verdict format, the usage reading or the argv beyond the three added `--disable` names.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The 0.163.0 feature capture | - | 2 | 1 |
| CP2 | Move the pin | CP1 | 2 | 1 |
| CP3 | The guides and the roadmap | CP2 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The 0.163.0 feature capture (#168)

**Files:** `tests/harness_contract/codex/README.md`, `tests/harness_contract/codex/features_list.0.163.0.txt`, `tests/harness_contract/codex/features_list.0.162.1.txt`

D1, D5. Create `features_list.0.163.0.txt` from the operator's output on issue #168 (the collapsed block of the planning-guidance comment, verbatim, header warning line removed) and remove `features_list.0.162.1.txt`. Run no `codex` command and read no Codex home. Rewrite the README opening and its table row for the feature list: the new file name and its SHA-256, and a provenance paragraph with two dated parts, the free probes of 2026-10-26 on `codex-cli 0.163.0` (the feature list and the `--disable` acceptance of the three names) and the retained 2026-10-15 measurements on `codex-cli 0.162.1` (every other row, with the digests unchanged). Add a short table of the three features: each is `stable` and `true` on 0.163.0 (they were `false` on 0.162.1), each is disabled by D2, and the evidence is the `--disable` acceptance. No other capture file is touched, and no digest row other than the feature list changes.

Done when: `features_list.0.163.0.txt` names its 0.163.0 origin in the README, the README digest of the feature list matches the file, the other digest rows are byte-identical to before, `git diff --stat` shows no other capture changed, and no `features_list.0.162.1.txt` remains. This checkpoint changes no source; the suite is red only where it still reads the old capture name, which CP2 fixes.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- Move the pin (#168)

**Files:** `controller/review.py`, `controller/harness.py`, `tests/fake_codex.py`, `tests/test_fake_codex_contract.py`, `tests/test_review.py`, `tests/test_harness.py`, `docs/guide/harnesses.md`

D2, D3, D4. In `controller/review.py` set `PINNED_VERSION` to `codex-cli 0.163.0`, add `api_key_cyber_access_programs`, `cli_daybreak` and `code_mode_interrupt` to `DISABLED_FEATURES` (none to `TOOL_ALLOWLIST`), and update the comments and docstrings that name 0.162.1 or the old capture. Set the Codex adapter's `pinned_version` in `controller/harness.py` to the same string. Point `tests/fake_codex.py` (`DEFAULT_FEATURES`, `VERSION`) and the three test modules at the 0.163.0 capture; the fake must report the 0.163.0 feature list so the existing drift tests exercise the new set.

Generated table: the adapter's `pinned_version` is rendered into the generated region of `docs/guide/harnesses.md` (the "Pinned version" column), and `tests.test_docs` checks that region through `check_docs.check_tree`. So CP2 regenerates it in the same checkpoint: run `python3 tools/harness_table.py --write` after the `pinned_version` edit, and touch nothing else on that page (its prose is CP3's).

Contract-test adjustment: `test_review_argv_is_the_captured_recommended_invocation` (`tests/test_fake_codex_contract.py`) compares `review.argv("exec", ...)` with the retained `exec_argv.json`, which is a 0.162.1 capture and lacks the three new pairs. `exec_argv.json` is not rewritten. The test derives the expected argv from the historical capture plus the three pairs, inserted into the capture's sorted `--disable` run, and says so in its docstring; the rest of the comparison (paths aside, without the operator-rule `-m`) is unchanged.

Tests: the existing `test_the_capture_passes_the_drift_check_once_the_overrides_took` and `test_every_enabled_feature_of_the_capture_is_allowlisted_or_disabled` (`tests/test_review.py`) are the feature-acceptance check (a); they run unchanged once `FEATURES_CAPTURE` names the 0.163.0 file, and no duplicate is added. New tests: (b) `check_features` still refuses the 0.163.0 capture when one of the three names is neither allowlisted nor disabled (a regression guard of the fail-closed property; it passes on the base commit too, since none of the three is listed there); (c) each of the three names is in `DISABLED_FEATURES`, is absent from `TOOL_ALLOWLIST`, and `review.argv("exec", ...)` carries its `--disable` pair; `check_argv` refuses an argv that omits any one of the three.

Done when: the new tests pass; (a) and (c) fail against the base commit (checked once by stashing the source edits), and (b) is stated to pass on both; the adjusted argv test passes against the retained `exec_argv.json`; `python3 -m unittest tests.test_review tests.test_fake_codex_contract tests.test_harness` is green; `python3 tools/check_docs.py` and `python3 -m unittest tests.test_docs` pass (the generated harness table is current); the full suite is green but for the known Git 2.56 trailer tests and the known 2.5.1 plan-stage golden drift; no edit outside the declared files.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The guides and the roadmap (#168)

**Files:** `docs/guide/harnesses.md` (prose only), `docs/guide/workers.md`, `docs/guide/runtime.md`, `docs/common-problems.md`, `docs/ROADMAP.md`

D4. Replace the pinned version where the guides state it (`docs/guide/harnesses.md`'s generated table was already regenerated in CP2 and is never edited by hand; `docs/guide/workers.md` and `docs/guide/runtime.md` name the new build, and `workers.md`'s sentence "fails closed on a version other than its pin" is corrected per D4), and in `docs/common-problems.md` extend the exit 30 entry with the next-update case: what the refusal names (the check and the changed feature, layer, state entry or flag, never a version string, since none is checked), that it is correct, and that the lane runs the Codex review by hand until a person re-measures the pin (until `verify-codex` ships). `docs/ROADMAP.md` marks RB3 done and H7 next. The architecture decision record and the 1.10.0 release note stay as they are: they record the 2026-10-15 measurement. The release note, history row and compatibility line belong to the docs pull request after the 1.22.2 release, as for every earlier release.

Done when: `python3 tools/check_docs.py` passes (the generated tables are current) and a search of the guides for `0.162.1` finds it only in dated records of the earlier measurement.
<!-- /CP3 -->
