# Claude harness contract fixtures

`workflow-controller-review-modes` CP2, plan decisions D5 and D9.

D9's operator pre-step was run on **2026-10-17** against `claude 2.1.296 (Claude Code)` (eight real `claude -p` runs:
seven on `claude-haiku-4-5-20251001`, one on `claude-opus-5-5 --effort high`; one of the eight (`--bare`) failed
before any API call; about 142k tokens in all, almost all cache reads/creations, total `total_cost_usd` **0.152**, each
run capped by `--max-budget-usd 0.50`), plus free probes (`--help` and 25 flag-parse probes with no model call). Every
run used a scratch `HOME`/`CLAUDE_CONFIG_DIR` holding only a copy of the operator's credential file, `env -i`, and a
disposable probe copy (README, `CLAUDE.md` with a sentinel and an instruction, `.claude/settings.json` with hooks
that write marker files, `.claude/commands/`, `agents/`, `skills/`, a `.mcp.json` whose server writes a marker),
from run 3 on nested under an ancestor with its own `CLAUDE.md` and settings hook. These captures are the only source
for the Claude harness's constants; a Claude Code upgrade that changes one is meant to fail a contract test, not a launch.

| file | what it pins | SHA-256 |
| --- | --- | --- |
| `argv.json` | the real command line and env of every run (label, exit code) and `recommended_run` | `984474efd379dd44494d449587db1f7a03e854657c9d6be360252a050c1d0d38` |
| `flags.txt` | which flags parse, which fail closed (`--setting-sources`, `--permission-mode`, unknown options) and which do NOT (`--effort <unknown>` warns, `--tools <unknown>` is dropped, non-JSON `--mcp-config` is a path) | `4b40bd87fa149d7936e5051c7392bdb24107cc928459840b7ffc02d4fcb42ce4` |
| `help.2.1.296.txt` | `claude --help` (`--bare` OAuth limit, `--setting-sources`, `--restricted`, `--safe-mode`, `--permission-prompts`) | `550db4f88ae1d97bf7244ca96dc89554aabd3978241ceb69f79001e816c35f09` |
| `init_events.jsonl` | the `system`/`init` event of each run: `tools`, `mcp_servers`, `model`, `permissionMode`, `slash_commands`, `skills`, `agents`, `plugins`, `memory_paths`, `apiKeySource`, `cwd`, `session_id` | `645bc68c8be80fc54213de57552c2a6c826ceb46691f2c3f6414b0aa5ce6670c` |
| `layers.txt` | the layer matrix: hooks, `.mcp.json`, claude.ai connectors, `CLAUDE.md` (cwd, ancestors, user, the real `~/.claude`), commands/skills/agents, with and without `--setting-sources ""` and `--bare`; managed settings paths | `10b3aa06204d17fa22a54db1cb9e123ebaee3878ddb3a248e71da8bf1b1f2e86` |
| `tools_permissions.txt` | the tools offered per argv, `dontAsk` denials (outside-cwd Read refused, inside-cwd Read allowed), the write refusal | `b4649280fd6ee47662350ad9019083b8ed790290705dfd93d97f167a0c00c906` |
| `credentials.txt` | the credential file and shape (key names only), digest/mode/mtime before and after every run, `--bare` not logged in | `abdaf6ff943998a2980dd25a6b51816e43b6fdc3bb53d5683d92bd5ff9611508` |
| `state_entries.txt` | the entries each run created in the scratch home, with and without `--no-session-persistence` and auto-memory | `c67bf43a55f50c651de707ba9c16b9c346d37591db540d51d00e69b141e6247d` |
| `env.txt` | the exact environment the installed Claude Code needs (single ELF, embedded ripgrep, git from PATH) and the disable variables | `0b19e507ec7e24a128f60e7f194af152d8abbb44dd935f1ea43d8c3b82fcd7c8` |
| `result_events.json` | the `result` event of every run: `usage`, `modelUsage`, `total_cost_usd`, `permission_denials`, `is_error`/`terminal_reason` (incl. the not-logged-in shape) | `f05a9365a0ada18fea51fa9e28af33635ce7a9a25f39eb60cb8549bf98cac8ff` |
| `rate_limit_event_run6.json` | a real `rate_limit_event` (five-hour and seven-day `utilization`, `resetsAt`, `status`) | `c0a683598ea9421df77a2ceed89a78172c744b23ebd7cbd72e830de7234085d3` |
| `stream_recommended_run5.jsonl` | the whole stream of the recommended argv (haiku, ancestor layout): init, `permission_denied` events, tool results, result | `b6eed5768762ecba107685af3c654927520ae0f15e5d066687354e18e84618e6` |
| `stream_opus_high_run6.jsonl` | the whole stream of the recommended argv on `claude-opus-5-5 --effort high`, minimal home | `1d46ea53a0121fce1e9b2e82f9e6551274251d384ee2f8cb6999c6a1c168c562` |
| `stream_bare_not_logged_in_run4.jsonl` | the whole stream of `--bare` with only the OAuth file: "Not logged in", exit 1 | `df9533d6ca1add92d17481813a6cf519c72018ed0c68feb018e96566881483e5` |
| `debug_excerpts.txt` | filtered `--debug-file` lines (runs 7, 8): settings paths read, skills dirs, hooks count, claude.ai MCP fetch, managed settings | `1d8e39dd37080cd2be4e14fc162951390d6b0579fc00609022ba7b733a89b2e8` |
| `prompts.txt` | the probe prompts (stdin) | `3ecc772ef16227af9337b7625c1b6c2a45f6e286df509a1724714c9f749c477d` |
| `prestep_runner.sh`, `flags_probe.sh` | the scripts that built each copy/home and ran each probe (provenance) | `29d8df3e20770c60e13db6b909d4161a236bf706c085dd079e5107b06ba3e347`, `74bb2f60ed6271c01cf5d528748ab4a0f32a3c4486fccf33a784dbb204c6fd3f` |

Redactions: no credential bytes appear anywhere; token-like strings and e-mail addresses are replaced, and the account's
claude.ai connector names are replaced by `Connector1..5`. Nothing else was changed.

## The recommended reviewer invocation

```
env -i PATH=<dir of claude>:/usr/local/bin:/usr/bin:/bin HOME=<job>/home TMPDIR=<job>/tmp CLAUDE_CONFIG_DIR=<job>/claude-home \
  XDG_CONFIG_HOME=... XDG_STATE_HOME=... XDG_CACHE_HOME=... (inside the job) GIT_CEILING_DIRECTORIES=<job parent> \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 ENABLE_CLAUDEAI_MCP_SERVERS=false \
claude -p --output-format stream-json --verbose --no-session-persistence --setting-sources "" \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' --tools Read,Grep,Glob \
  --permission-mode dontAsk --permission-prompts none --disable-slash-commands \
  --model <reviewer.same_provider_model> --effort <reviewer.same_provider_effort> [--max-budget-usd <cap>]  < prompt
```
cwd = the disposable copy (`build_copy`, with `.claude/` and `.mcp.json` stripped). The flag set is exactly run 6's
(exit 0); the two extra env variables were exercised in runs 7 and 8 with the same flags, not in one run together with
Opus. The verdict is the `result` event's `result` string (there is no `-o` file); `is_error` / `terminal_reason`
/ `api_error_status` classify failures (a not-logged-in run is `subtype: success`, `is_error: true`,
`terminal_reason: api_error`, exit 1).

Checks the harness must make itself, because the CLI does not fail closed: the init event's `tools` equals
`["Glob","Grep","Read"]`, `mcp_servers` is `[]`, `permissionMode` is `"dontAsk"`, `skills` and `slash_commands`
are `[]`, `model` is the requested id, `memory_paths` is null; and the effort value is validated before launch.

## What is proven, what is not

Proven by the runs: a fresh session (new `session_id` every run, no transcript written under `--no-session-persistence`);
read-only tools only (no write, shell, web, agent, skill or MCP tool offered; writes refused; Read outside cwd denied
by `dontAsk`); no hooks (user, project or ancestor), no MCP (project `.mcp.json` or claude.ai connectors), no
user or project settings, no auto-loaded `CLAUDE.md` from cwd, ancestors, the user home or the real `~/.claude`;
the credential file read from `CLAUDE_CONFIG_DIR` and never rewritten; the copy unchanged; usage per run in the
`result` event (`usage`, `modelUsage.<model>`, `total_cost_usd`) and the subscription window in `rate_limit_event`.

Not achievable: `--bare` (OAuth is never read under it). The settings-source control stops `CLAUDE.md` auto-loading
but not prompt injection when the reviewer reads the file as content (Haiku obeyed the read file's instruction, Opus
declined): D5's "kept as ordinary content" holds only as far as the model resists; stripping it is the stricter choice.

## Open facts the captures do not settle

- No token refresh happened (access token had ~1.7 h left), so whether a refresh rewrites the copied
  `.credentials.json` and rotates the refresh token is not observed; the copy-back path is untested on Claude.
- No real usage-limit (429 / `rejected`) result was captured here (C8's two real job streams in
  `~/.local/state/workflow-controller/jobs` remain the source); the `rate_limit_event` seen is `status: allowed`.
- Grep/Glob outside cwd, and symlinks inside the copy pointing outside it, were not exercised.
- Managed settings (`/etc/claude-code/managed-settings.json`, `managed-settings.d/`, `managed-mcp.json`) do not exist
  on this host; they apply regardless of `--setting-sources`, so on a host that has them they would load.
- The `[uds-messaging]` socket under `$TMPDIR/cc-socks/` accepts injected messages during a run; its mode and
  whether its token is enforced ("auth line optional here") were not checked.
- The two builtin plugins (`cc-plugin-agents-md`, `cc-plugin-plugin-authoring`) still load (0 hooks, project memory
  off); `--safe-mode`, `--restricted` and `CLAUDE_CODE_DISABLE_CLAUDE_MDS` were parsed or named but not run.
- The `--effort` value is not echoed in the stream (only `per_turn_effort_active: true`).
- `.last-cleanup` appears in some runs and not others.
