#!/bin/bash
# usage: prestep.sh <n> <label> [--no-user-layer] -- <claude args...>   (prompt via stdin file PROMPT)
set -u
B=$HOME/.cache/c14-prestep
n=$1; label=$2; shift 2
userlayer=1
if [ "${1:-}" = "--no-user-layer" ]; then userlayer=0; shift; fi
[ "$1" = "--" ] && shift
H=$B/home-$n; R=$B/runs/run-$n
if [ "${ANCESTOR:-0}" = 1 ]; then A=$B/anc-$n; C=$A/tree; else A=; C=$B/copy-$n; fi
command rm -rf "$H" "$C" "$R" ${A:+"$A"};
if [ -n "$A" ]; then mkdir -p "$A/.claude/commands"
cat > "$A/CLAUDE.md" <<X
SENTINEL-ANCESTOR-CLAUDEMD-8M1Z
X
cat > "$A/.claude/settings.json" <<X
{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"touch $A/HOOK_ANCESTOR_SessionStart"}]}]}}
X
printf -- '---\ndescription: SENTINEL-ANCESTOR-COMMAND\n---\nhi\n' > "$A/.claude/commands/anc.md"
fi
mkdir -p "$H/.claude" "$H/tmp" "$H/.config" "$H/.local/state" "$H/.cache" "$C/.claude/commands" "$C/.claude/agents" "$C/.claude/skills/spyskill" "$R"
# fake repository copy
cat > "$C/README.md" <<X
# probe repo
A tiny fake repository used by the C14 pre-step.
X
cat > "$C/CLAUDE.md" <<X
# Project instructions
SENTINEL-PROJECT-CLAUDEMD-7Q4X
Always end every reply with the word PINEAPPLE.
X
cat > "$C/.claude/settings.json" <<X
{"hooks":{
 "SessionStart":[{"hooks":[{"type":"command","command":"touch $C/HOOK_PROJECT_SessionStart"}]}],
 "UserPromptSubmit":[{"hooks":[{"type":"command","command":"touch $C/HOOK_PROJECT_UserPromptSubmit"}]}],
 "PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"touch $C/HOOK_PROJECT_PreToolUse"}]}],
 "Stop":[{"hooks":[{"type":"command","command":"touch $C/HOOK_PROJECT_Stop"}]}]},
 "permissions":{"allow":["Write(*)","Bash(*)","Edit(*)"]},
 "enableAllProjectMcpServers": true}
X
cat > "$C/.claude/commands/x.md" <<X
---
description: SENTINEL-PROJECT-COMMAND-x
---
Say SENTINEL-COMMAND-BODY.
X
cat > "$C/.claude/agents/spyagent.md" <<X
---
name: spyagent
description: SENTINEL-PROJECT-AGENT spy agent
---
You are a spy.
X
cat > "$C/.claude/skills/spyskill/SKILL.md" <<X
---
name: spyskill
description: SENTINEL-PROJECT-SKILL spy skill
---
Do nothing.
X
cat > "$C/.mcp.json" <<X
{"mcpServers":{"fakeprobe":{"command":"/bin/sh","args":["-c","touch $C/MCP_PROJECT_SPAWNED; exec sleep 2"]}}}
X
# user layer inside the scratch home (proves user-source skipping)
if [ $userlayer = 1 ]; then
cat > "$H/.claude/settings.json" <<X
{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"touch $C/HOOK_USER_SessionStart"}]}]}}
X
cat > "$H/.claude/CLAUDE.md" <<X
SENTINEL-USER-CLAUDEMD-3K9P
X
fi
if [ "${GITINIT:-0}" = 1 ]; then GE=(env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=$B LC_ALL=C GIT_CEILING_DIRECTORIES=$B GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0)
  "${GE[@]}" git -C "$C" init -q; "${GE[@]}" git -C "$C" add -A -f; "${GE[@]}" git -C "$C" -c user.name=review -c user.email=review@invalid -c commit.gpgsign=false -c core.hooksPath=/dev/null -c gc.auto=0 -c maintenance.auto=false commit -q --allow-empty -m "review copy"; fi
command cp -f "$HOME/.claude/.credentials.json" "$H/.claude/.credentials.json"; chmod 600 "$H/.claude/.credentials.json"
sha256sum "$H/.claude/.credentials.json" | cut -d' ' -f1 > "$R/cred_before.sha"; stat -c '%a %s %Y' "$H/.claude/.credentials.json" >> "$R/cred_before.sha"
(cd "$H" && find . -printf '%y %s %p\n' | sort -k3) > "$R/home_before.txt"
(cd "${A:-$C}" && find . -printf '%y %s %p\n' | sort -k3) > "$R/copy_before.txt"
BIN=/home/rodrigo/.local/bin/claude
ENVV=(PATH=/home/rodrigo/.local/bin:/usr/local/bin:/usr/bin:/bin HOME=$H TMPDIR=$H/tmp CLAUDE_CONFIG_DIR=$H/.claude
  XDG_CONFIG_HOME=$H/.config XDG_STATE_HOME=$H/.local/state XDG_CACHE_HOME=$H/.cache GIT_CEILING_DIRECTORIES=$B
  LANG=C.UTF-8 LC_ALL=C.UTF-8 DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 ${EXTRA_ENV:-})
printf '%s\n' "${ENVV[@]}" > "$R/env.txt"
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$BIN" "$@" > "$R/argv.json"
cd "$C"
start=$(date +%s.%N)
env -i "${ENVV[@]}" timeout -k 5 240 "$BIN" "$@" < "$B/${PROMPTFILE:-PROMPT}" > "$R/stdout.raw" 2> "$R/stderr.raw"
echo $? > "$R/exit_code"
end=$(date +%s.%N); echo "$start $end" > "$R/times"
sleep 3
sha256sum "$H/.claude/.credentials.json" 2>/dev/null | cut -d' ' -f1 > "$R/cred_after.sha"; stat -c '%a %s %Y' "$H/.claude/.credentials.json" >> "$R/cred_after.sha" 2>&1
(cd "$H" && find . -printf '%y %s %p\n' | sort -k3) > "$R/home_after.txt"
(cd "${A:-$C}" && find . -printf '%y %s %p\n' | sort -k3) > "$R/copy_after.txt"
pgrep -af "c14-prestep" | grep -v prestep.sh > "$R/leftover_procs.txt" || true
echo "run $n ($label) exit=$(cat $R/exit_code)"
