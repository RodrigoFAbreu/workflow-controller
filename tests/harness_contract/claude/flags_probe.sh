#!/bin/bash
B=~/.cache/c14-prestep; H=$B/home-flags; command rm -rf $H; mkdir -p $H/.claude $H/tmp $B/copy-flags; cd $B/copy-flags
E=(PATH=/home/rodrigo/.local/bin:/usr/local/bin:/usr/bin:/bin HOME=$H TMPDIR=$H/tmp CLAUDE_CONFIG_DIR=$H/.claude XDG_CONFIG_HOME=$H/.config XDG_STATE_HOME=$H/.local/state XDG_CACHE_HOME=$H/.cache LANG=C.UTF-8 LC_ALL=C.UTF-8 DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1)
probe(){ echo "### claude $*  (stdin /dev/null, no credential in home)"; env -i "${E[@]}" timeout 30 /home/rodrigo/.local/bin/claude "$@" </dev/null 2>&1 | head -5; echo "exit=${PIPESTATUS[0]}"; }
probe -p
probe -p --output-format stream-json
probe -p --output-format stream-json --verbose
probe -p --setting-sources ""
probe -p --setting-sources bogus
probe -p --setting-sources user,project,local
probe -p --permission-mode bogus
probe -p --permission-mode dontAsk --permission-prompts none
probe -p --permission-prompts bogus
probe -p --tools Read,Grep,Glob
probe -p --tools NotATool
probe -p --effort bogus
probe -p --effort high
probe -p --max-budget-usd 0.5
probe -p --max-budget-usd abc
probe -p --strict-mcp-config --mcp-config '{"mcpServers":{}}'
probe -p --strict-mcp-config --mcp-config '{bad json'
probe -p --no-session-persistence
probe -p --bare
probe -p --restricted
probe -p --restricted --permission-mode bypassPermissions
probe -p --safe-mode
probe -p --disable-slash-commands
probe -p --model claude-haiku-4-5-20251001
probe -p --bogus-flag
echo "### files in the scratch home after all probes"; (cd $H && find . -printf '%y %s %p\n' | sort -k3)
