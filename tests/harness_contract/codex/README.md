# Codex harness contract fixtures

`workflow-controller-codex-reviewer` CP2, plan decisions D4 and D12.

D12's **operator pre-step is still open.** It is one tiny real `codex exec` (argv and rollout
excerpt), the sandbox probe (writes outside the copy, a command's network connection, a hostile
operator home, a browser or computer-use action), the command that composes the configuration
layers without starting anything, the `CODEX_STATE_ENTRIES` / `STARTUP_LOADED` /
`CODEX_STARTUP_SUBENTRIES` an empty home gets from `mcp list`, `features list` and `exec`,
the `ENV_ALLOWLIST` the installed Codex needs, and whether `--ignore-rules` and a relocation of
the system layers are accepted. A CP2 implementer must not spend Codex allowance, so none of that
is here, `controller.review.PRE_STEP_CAPTURED` is false, and the harness registry is empty.

What is here is the one capture that spends nothing:

| file | what it pins | SHA-256 |
| --- | --- | --- |
| `features_list.0.162.1.txt` | `codex features list` of `codex-cli 0.162.1` run in an empty `CODEX_HOME` with an empty environment (no model call, no credential): the source of `TOOL_ALLOWLIST` and `DISABLED_FEATURES` | `8095d5c2cf431c74c3634d12e304829509cd2cc4b1b17ea16ef6fba274a41e1e` |

`tests/test_fake_codex_contract.py` checks the digest. The constants in `controller/review.py`
marked *provisional* come from this capture alone.
