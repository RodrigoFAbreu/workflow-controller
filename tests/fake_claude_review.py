"""A stand-in for the ``claude`` CLI as the *review* harness runs it
(`workflow-controller-review-modes` CP2, D5/D9).

``controller.review`` runs ``claude -p --output-format stream-json ...`` with an
allowlisted environment, so (like ``fake_codex.py``) the fake is driven by a
scenario file, written with :func:`write_binary` together with the executable
wrapper that bakes its path in. It is not ``fake_claude.py``, the *worker* fake.

It does what the real tool is shown to do in ``tests/harness_contract/claude/``,
no more, and **spends nothing and connects nowhere**:

- the credential file is read from ``$CLAUDE_CONFIG_DIR/.credentials.json``; a
  missing one prints the captured not-logged-in result and exits 1;
- project hooks (``.claude/settings.json``) and the project ``.mcp.json`` server
  load only when the command line does not carry ``--setting-sources ""`` /
  ``--strict-mcp-config`` (``layers.txt``), and each one it loads is a local
  script whose only effect is a marker file, recorded in the log;
- the ``init`` event shows the tools offered by ``--tools``, the MCP servers
  loaded, the permission mode, the model and ``memory_paths`` (null only with
  ``CLAUDE_CODE_DISABLE_AUTO_MEMORY=1``);
- the stream ends with one ``result`` event holding the verdict, the usage and
  a ``rate_limit_event`` shaped as the capture's.

Scenario keys (all optional): ``log`` (JSON lines: argv, cwd, env, hooks and MCP
servers loaded, the credential digest), ``orphan_seconds`` (as the Codex fake),
``ready_file``, ``hang_seconds``, ``verdict`` (the Codex fake's kinds, plus
``limit``, ``error`` and ``none``), ``exit``, ``refresh_auth`` (rewrite the
credential file), ``init`` (fields of the ``init`` event to override),
``no_init``, ``extra_state`` (names created in ``CLAUDE_CONFIG_DIR``),
``write_outside`` (paths to write, as a sandbox failure would) and ``stdout_extra``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_codex  # noqa: E402  (by file: tests/__init__ rewrites the environment)

VERSION = "2.1.296 (Claude Code)"
CAPTURES = HERE / "harness_contract" / "claude"


def write_binary(directory: Path, scenario: dict, *, name: str = "claude") -> Path:
    """Write ``directory/<name>`` (an executable wrapper) and its scenario file;
    returns the wrapper's path."""
    directory.mkdir(parents=True, exist_ok=True)
    scenario_path = directory / f"{name}.scenario.json"
    scenario_path.write_text(json.dumps(scenario))
    wrapper = directory / name
    wrapper.write_text(
        f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(HERE)!r})\n"
        f"sys.argv[1:1] = ['--scenario', {str(scenario_path)!r}]\n"
        "import fake_claude_review\nsys.exit(fake_claude_review.main())\n")
    wrapper.chmod(0o755)
    return wrapper


def read_log(path: str | Path) -> list[dict]:
    return fake_codex.read_log(path)


def _log(scenario: dict, record: dict) -> None:
    if scenario.get("log"):
        with open(scenario["log"], "a") as fh:
            fh.write(json.dumps(record) + "\n")


def _value(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def _run_marker(command: str, cwd: Path) -> None:
    subprocess.run(command, shell=True, cwd=cwd, check=False, capture_output=True, timeout=30)


def _project_loads(argv: list[str], cwd: Path, record: dict) -> list[str]:
    """Hooks and MCP servers the project would load under this command line;
    returns the MCP server names (the ``init`` event's ``mcp_servers``)."""
    if _value(argv, "--setting-sources") != "":
        settings = cwd / ".claude" / "settings.json"
        if settings.is_file():
            for groups in json.loads(settings.read_text()).get("hooks", {}).values():
                for group in groups:
                    for hook in group.get("hooks", []):
                        record["hooks_loaded"].append(hook["command"])
                        _run_marker(hook["command"], cwd)
    servers: list[str] = []
    mcp = cwd / ".mcp.json"
    if "--strict-mcp-config" not in argv and mcp.is_file():
        for name, spec in json.loads(mcp.read_text()).get("mcpServers", {}).items():
            servers.append(name)
            record["mcp_loaded"].append(name)
            if spec.get("command"):
                _run_marker(" ".join([spec["command"], *spec.get("args", [])]), cwd)
    return servers


def _init_event(argv: list[str], servers: list[str], scenario: dict, cwd: Path) -> dict:
    tools = (_value(argv, "--tools") or "").split(",") if "--tools" in argv else ["Task", "Bash", "Edit", "Read", "Write"]
    event = {
        "type": "system", "subtype": "init", "cwd": str(cwd), "session_id": str(uuid.uuid4()),
        "tools": sorted(tools), "mcp_servers": servers, "model": _value(argv, "--model") or "claude-opus-5-5",
        "permissionMode": _value(argv, "--permission-mode") or "default",
        "slash_commands": [] if "--disable-slash-commands" in argv else ["code-review"],
        "apiKeySource": "none", "claude_code_version": "2.1.296", "skills": [], "agents": ["general-purpose"],
        "memory_paths": None if os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1" else {"auto": "/x/memory/"},
        "per_turn_effort_active": "--effort" in argv,
    }
    event.update(scenario.get("init", {}))
    return event


def _rate_limit_event(rejected: bool) -> dict:
    info = json.loads((CAPTURES / "rate_limit_event_run6.json").read_text())
    if rejected:
        info["rate_limit_info"]["status"] = "rejected"
        info["rate_limit_info"]["unifiedWindows"]["five_hour"]["utilization"] = 1.0
    return info


def _result_event(text: str, scenario: dict, model: str, *, error: bool, status: int | None) -> dict:
    return {
        "type": "result", "subtype": "success", "is_error": error, "api_error_status": status,
        "terminal_reason": "api_error" if error else "completed", "num_turns": 3, "result": text,
        "session_id": str(uuid.uuid4()), "total_cost_usd": 0.01,
        "usage": {"input_tokens": 12, "output_tokens": 300, "cache_creation_input_tokens": 50,
                  "cache_read_input_tokens": 1000},
        "modelUsage": {model: {"inputTokens": 12, "outputTokens": 300, "costUSD": 0.01}},
        "permission_denials": [],
    }


def main() -> int:
    argv = sys.argv[1:]
    scenario = json.loads(Path(argv[argv.index("--scenario") + 1]).read_text())
    del argv[argv.index("--scenario"):argv.index("--scenario") + 2]
    if argv[:1] == ["--version"]:
        print(VERSION)
        return 0
    fake_codex._arm_watchdog(scenario)
    cwd = Path.cwd()
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    credential = config / ".credentials.json"
    record = {"argv": argv, "cwd": str(cwd), "env": dict(os.environ), "config_dir": str(config),
              "hooks_loaded": [], "mcp_loaded": [], "writes": [],
              "credential_digest": hashlib.sha256(credential.read_bytes()).hexdigest() if credential.is_file() else None}
    prompt = sys.stdin.read()
    record["prompt"] = prompt
    model = _value(argv, "--model") or "claude-opus-5-5"
    spec = scenario
    if not credential.is_file():
        record["error"] = "not logged in"
        _log(scenario, record)
        print(json.dumps(_result_event("Not logged in · Please run /login", scenario, model, error=True, status=None)))
        return 1
    for name in ("backups", ".claude.json", "projects", "sessions", *spec.get("extra_state", [])):
        path = config / name
        if name in ("backups", "projects", "sessions"):
            path.mkdir(exist_ok=True)
        else:
            path.write_text("{}")
    servers = _project_loads(argv, cwd, record)
    if spec.get("hang_seconds"):
        time.sleep(float(spec["hang_seconds"]))
    for target in spec.get("write_outside", []):
        Path(target).write_text("written by the reviewer\n")
        record["writes"].append(target)
    if spec.get("escape_to"):
        Path(spec["escape_to"]).write_text("escaped\n")
    if spec.get("refresh_auth"):
        credential.write_text(json.dumps({"claudeAiOauth": {"accessToken": "refreshed", "n": str(uuid.uuid4())}}))
    kind = spec.get("verdict", "exact")
    out = []
    if not spec.get("no_init"):
        out.append(_init_event(argv, servers, scenario, cwd))
    limited = kind == "limit"
    out.append(_rate_limit_event(limited))
    if limited:
        out.append(_result_event("You've hit your limit", scenario, model, error=True, status=429))
    elif kind == "error":
        out.append(_result_event("Overloaded", scenario, model, error=True, status=529))
    elif kind == "none":
        out.append(_result_event("", scenario, model, error=False, status=None))
    else:
        text = fake_codex._verdict("exact" if kind == "exact" else kind, prompt) or ""
        out.append(_result_event(text, scenario, model, error=False, status=None))
    sys.stdout.write("".join(json.dumps(e) + "\n" for e in out) + spec.get("stdout_extra", ""))
    sys.stdout.flush()
    code = int(spec.get("exit", 1 if limited or kind == "error" else 0))
    record["exit"] = code
    _log(scenario, record)
    return code
