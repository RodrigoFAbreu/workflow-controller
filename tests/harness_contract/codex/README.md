# Codex harness contract fixtures

`workflow-controller-codex-reviewer` CP2, plan decisions D4 and D12.

D12's operator pre-step was run on **2026-10-15** against `codex-cli 0.162.1` (seven real
`codex exec` runs, about 132k tokens, plus free app-server and `mcp list` probes). These are its
captures, copied verbatim from the operator's scratch directory, except the feature list, which
was re-measured on **2026-10-26** against `codex-cli 0.163.0` (`workflow-controller-codex-build-pin`,
issue #168). They are the only source of the constants in `controller/review.py` that used to be
marked *provisional*; a Codex upgrade that changes one of them is meant to fail a contract test,
not a launch.

What was re-verified on 0.163.0, and what was not: the operator ran the free probes only (no model
call). `codex features list` in an empty `CODEX_HOME` is `features_list.0.163.0.txt`, and
`codex features list --disable <name>` exits 0 and then reports the feature `false` for each of the
three names below (an unknown name still exits 1). The seven real `exec` runs, the sandbox probe,
the composing command, the state entries, the environment and the flag table are still the
2026-10-15 measurements on 0.162.1 and were not repeated; their digests are unchanged. The first
Controller-run Codex review after the release is the live check, and the launch still fails closed
on further drift.

| file | what it pins | SHA-256 |
| --- | --- | --- |
| `features_list.0.163.0.txt` | `codex features list` in an empty `CODEX_HOME` (no model call), measured on `codex-cli 0.163.0` on 2026-10-26: the source of `TOOL_ALLOWLIST` and `DISABLED_FEATURES` | `6dd1708362bdc7de0a50f7e4ad9087410fbfe42df7793bf9977cf6d2103ff810` |
| `exec_argv.json` | the real `exec` command lines (`pinned_run` and `recommended_run`, the shape `review.argv` now builds), the flag-rejection behaviour and what the first run showed | `abe973aa983c41f1af7c9285aad43f4edf91e512902dc56f4504763e93f9ff41` |
| `rollout_excerpt.jsonl` | a real rollout's event shapes and a failing `task_complete` (`codex_error_info`): the source of limit classification and usage readings | `60779143bedf8051d9c985d7e10a9c3cd6ed935f08fc7e4fac1e6d81b8019f88` |
| `stderr_shapes.txt` | stdout is the final message only; stderr is the whole transcript (never read for classification) | `a206acba62218e49a80738cb3b5bfa0c37496bf086e466538ac4e8c2e115a063` |
| `compose.txt` | the composing command (`app-server` JSON-RPC `config/read` with layers), the `ConfigLayerSource` types and a composed sample | `30b2c74fc962b18cbe19ef283ceebc0be45f07d3f620ee30ca47b1ba81432eb0` |
| `state_entries.txt` | the entries Codex writes into a minimal home, per invocation kind | `74f13b2aa51bbe93d0b6d3377fd78404b58c01c4b0a9ad5e6cd3348535503647` |
| `env.txt` | the exact environment the installed Codex needs (the symlink's directory holds `node`) | `1bb441add406d50c57237393823a162c3c65373d28ace2801956b086ff6ae296` |
| `flags.txt` | which invocation kind accepts which flag; unknown `--disable` and `-c` names fail closed | `0da03da748d8781b244ab2ad2e9b40c2c60b896a531d95f20968eef4d9b2e017` |
| `sandbox_probe.txt` | the sandbox probe (P1-P4, C1-C2, T6-T7): what a reviewer can and cannot reach, the code-mode host, project trust, sub-agents | `cd0ba3546d15212856a9fd84eabbee5e56410d20d43ae20cb51c4a15da0135cc` |
| `tester_compose.txt` | the Codex tester's composed configuration (C10g-3a): the composing command, the layers, the origins and values of the tester's keys and the state the run wrote, measured on `codex-cli 0.163.0` on 2026-10-30 with no model call: the source of `codex_sandbox.check_effective` | `3a09120933d6c3acff9f244a4f9305731b7b0c2943d071a983a34c1c5aa83505` |

Features that became enabled between 0.162.1 and 0.163.0 (all `stable` and `true` on 0.163.0, `false`
on 0.162.1). Each is disabled (`DISABLED_FEATURES`, never allowlisted): a review needs none of them
and there is no evidence any is inert. The evidence that 0.163.0 accepts each as a `--disable`
name is the free probe above.

| feature | state on 0.163.0 | decision |
| --- | --- | --- |
| `api_key_cyber_access_programs` | `stable`, `true` | disabled |
| `cli_daybreak` | `stable`, `true` | disabled |
| `code_mode_interrupt` | `stable`, `true` | disabled |

`tests/test_fake_codex_contract.py` checks every digest and pins `review.argv("exec", ...)`, the
composing command and `tests/fake_codex.py`'s shapes to these files.

Open facts the captures do not settle: no real usage-limit event was captured (the rollout spelling
of `usage_limit_exceeded` comes from the binary and the protocol schema, the 401 shape is real);
refresh-token rotation was not observed; `/etc/codex` has no relocation, so control (a) is the only
control for it; `allow_login_shell=false` and `--ignore-user-config` were not exercised.
