"""A stand-in for the ``codex`` CLI (`workflow-controller-codex-reviewer` CP2).

``controller.review`` runs ``<reviewer.binary> exec`` with an *allowlisted
environment* (D4), so unlike ``fake_claude.py`` this fake cannot be driven by
``FAKE_*`` variables: a test writes a scenario file with :func:`write_binary`,
which also writes the executable wrapper that bakes the scenario path in.

The fake does what the real tool is shown to do, no more, and **spends nothing
and connects nowhere**: a "server", "hook", "notify" or "authentication"
command it would start is a local script the test wrote, and its only effect is
a marker file.

What it composes. Configuration layers, lowest to highest: ``system_dir``
(``config.toml``, ``managed_config.toml``, ``requirements.toml``), the user file
``$CODEX_HOME/config.toml``, the project layers (``.codex/config.toml`` in the
working root and its ancestors, up to the first directory that has ``.git``
when ``project_root_markers`` is overridden, otherwise to the filesystem root),
``scenario["managed"]`` (a file standing for an MDM/cloud layer) and finally
the ``-c`` overrides. Tables merge key by key, so ``-c mcp_servers={}`` does
**not** remove an inherited server (measured on codex-cli 0.162.1); lists and
scalars replace. A ``-p <profile>`` argument overlays ``[profiles.<name>]``.

Startup, ``exec`` only: every composed ``mcp_servers.*.command``, every
composed ``hooks.*.command`` while the ``hooks`` feature is on, the composed
``notify`` command at the end of the turn, and a custom provider's
``auth.command`` when ``model_provider`` is not ``openai`` are run (their
output is not read); ``mcp list`` reports the composed server names without
running them. Each run is recorded in the log.

Composing command. ``app-server`` answers the JSON-RPC ``config/read`` request
with ``includeLayers`` the way the pinned Codex does (``compose.txt``):
``config`` (the composed table plus the packaged defaults), ``origins`` (one
entry per leaf, lists by index, none for defaults and empty containers) and
``layers`` (``sessionFlags``, then any managed layer, ``user``, ``system`` and
project layers, a project layer carrying a ``disabledReason`` and no effect when
the project is marked untrusted). ``exec`` appends the project-trust entry to
the home's ``config.toml`` unless a ``projects`` override names the working
root, writes only the state entries the capture lists and, with the bundled
skills left on, ``skills/.system``.

The log (``scenario["log"]``, JSON lines) records, per invocation: the
subcommand, argv, working directory, full environment, ``CODEX_HOME``, the
composed values with their source layer, the effective sandbox roots, network
policy, approval policy, model, effort and tier, the enabled features, the
commands it ran, tool families it used or found unavailable, writes it
attempted outside its working root and whether they were blocked, and the
credential file it refreshed.

Scenario keys (all optional): ``log``, ``system_dir``, ``managed``,
``orphan_seconds`` (default 300, ``0`` disables), ``ready_file`` (the pid is
written here once the watchdog is armed: a test that orphans a fake waits for it),
``hang_seconds``,
``features`` (path of a ``features list`` capture; default the committed one),
``state_extra`` (names created in ``CODEX_HOME`` on every invocation),
``rate_limits`` (``{"primary": percent, "secondary": percent}``), and
``exec``: ``verdict`` (``exact``, ``wrong_id_first``, ``bad_revision``,
``none``, ``limit``, ``hang``, ``missing_status``, ``block``, ``revise``; the header-retry
kinds ``revise_then_approve``, ``revise_then_revise`` and ``no_status_then_exact`` each give a wrong id first and an exact
header on the retry, with the ``Status:`` the name says), ``exit``,
``limit_shape`` (``string``, ``http429`` or ``percent``: how the rollout reports the
limit), ``stderr`` (text printed on stderr, a transcript line), ``subagent``
(write a sub-agent rollout with that many total tokens),
``write_outside`` (list of paths to write), ``escape_to`` (a real-worktree path
the fake writes as if the sandbox failed), ``git`` (argvs run in the working
root), ``tool_attempts`` (feature names), ``use_web_search``,
``refresh_auth``, ``descendant`` (a bounded sleeping child).
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FEATURES = HERE / "harness_contract" / "codex" / "features_list.0.162.1.txt"
VERSION = "codex-cli 0.162.1"
ORPHAN_SECONDS_DEFAULT = 300.0
_ORPHAN_POLL_SECONDS = 0.5


def write_binary(directory: Path, scenario: dict, *, name: str = "codex") -> Path:
    """Write ``directory/<name>`` (an executable wrapper) and its scenario file;
    returns the wrapper's path."""
    directory.mkdir(parents=True, exist_ok=True)
    scenario_path = directory / f"{name}.scenario.json"
    scenario_path.write_text(json.dumps(scenario))
    wrapper = directory / name
    wrapper.write_text(
        f"#!{sys.executable}\nimport sys\n# by file, not as tests.fake_codex: tests/__init__ rewrites the environment\n"
        f"sys.path.insert(0, {str(HERE)!r})\nsys.argv[1:1] = ['--scenario', {str(scenario_path)!r}]\n"
        "import fake_codex\nsys.exit(fake_codex.main())\n")
    wrapper.chmod(0o755)
    return wrapper


def read_log(path: str | Path) -> list[dict]:
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        return []


# -- composition ------------------------------------------------------------


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _parse_override(item: str) -> tuple[list[str], object]:
    key, _, raw = item.partition("=")
    try:
        value = tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw
    return key.strip().split("."), value


def _project_layers(cwd: Path, markers_override: list | None) -> list[Path]:
    layers, here = [], cwd.resolve()
    chain = [here, *here.parents]
    for directory in chain:
        layers.append(directory / ".codex" / "config.toml")
        if markers_override is not None and any((directory / m).exists() for m in markers_override):
            break
    return list(reversed(layers))


def _flatten(prefix: str, value, source: str, out: dict) -> None:
    if isinstance(value, dict) and value:
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else key, item, source, out)
    else:
        out[prefix] = {"value": value, "source": source}


def compose(args: list[str], scenario: dict, cwd: Path, codex_home: Path) -> tuple[dict, dict]:
    """``(merged, sourced)``: the composed configuration and the same flattened
    with the layer each leaf came from. The layers, for ``config/read``, are in
    ``compose.layers``."""
    compose.layers = []
    overrides = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-c"]
    parsed = [_parse_override(o) for o in overrides]
    markers = next((v for k, v in parsed if k == ["project_root_markers"]), None)
    system = Path(scenario["system_dir"]) if scenario.get("system_dir") else Path("/nonexistent-codex-system")
    low: list[tuple[str, dict]] = [(f"system:{system / 'config.toml'}", _toml(system / "config.toml")),
                                   (f"user:{codex_home / 'config.toml'}", _toml(codex_home / "config.toml"))]
    low += [(f"project:{p}", _toml(p)) for p in _project_layers(cwd, markers) if p.is_file()]
    # managed layers outrank the command line
    high = [(f"system:{system / name}", _toml(system / name)) for name in ("managed_config.toml", "requirements.toml")
            if (system / name).is_file()]
    if scenario.get("managed"):
        high.append((f"managed:{scenario['managed']}", _toml(Path(scenario["managed"]))))
    merged: dict = {}
    sourced: dict = {}
    trust = next((v for k, v in parsed if k == ["projects"]), None)
    untrusted = isinstance(trust, dict) and isinstance(trust.get(str(cwd)), dict) \
        and trust[str(cwd)].get("trust_level") == "untrusted"

    def apply(layers) -> None:
        nonlocal merged
        for source, data in layers:
            disabled = None
            if source.startswith("project:") and untrusted:
                disabled = f"To load project-local config, add {cwd} as a trusted project."
                compose.layers.append((source, data, disabled))
                continue
            compose.layers.append((source, data, disabled))
            merged = _merge(merged, data)
            if data:
                leaves: dict = {}
                _flatten("", data, source, leaves)
                sourced.update(leaves)

    apply(low)
    for flag in ("-p", "--profile"):
        if flag in args:
            name = args[args.index(flag) + 1]
            apply([(f"profile:{name}", merged.get("profiles", {}).get(name, {}))])
    for key_path, value in parsed:
        node = merged
        for part in key_path[:-1]:
            node = node.setdefault(part, {})
        node[key_path[-1]] = _merge(node[key_path[-1]], value) if isinstance(value, dict) \
            and isinstance(node.get(key_path[-1]), dict) else value
        sourced[".".join(key_path)] = {"value": value, "source": "override"}
    compose.layers.insert(0, ("override", {".".join(k): v for k, v in parsed}, None)) if parsed else None
    apply(high)
    return merged, sourced


_DEFAULTS = {"chatgpt_base_url": "https://chatgpt.com/backend-api/", "model_providers": {}, "profiles": {},
             "profile": None, "openai_base_url": None, "auto_review": None, "permissions": None,
             "default_permissions": None, "hooks": None, "mcp_servers": {}, "notify": []}


def _layer_name(source: str) -> dict:
    if source == "override":
        return {"type": "sessionFlags"}
    kind, _, rest = source.partition(":")
    if kind == "system":
        name = Path(rest).name
        return {"type": {"managed_config.toml": "legacyManagedConfigTomlFromFile"}.get(name, "system"), "file": rest}
    if kind == "managed":
        return {"type": "enterpriseManaged", "id": "fake", "name": rest}
    if kind == "project":
        return {"type": "project", "dotCodexFolder": str(Path(rest).parent)}
    return {"type": "user", "file": rest, "profile": None}


def _leaves(prefix: str, value, out: list) -> None:
    if isinstance(value, dict) and value:
        for key, item in value.items():
            _leaves(f"{prefix}.{key}" if prefix else key, item, out)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            _leaves(f"{prefix}.{index}" if prefix else str(index), item, out)
    elif value not in ({}, []):
        out.append(prefix)


def config_read(args: list[str], scenario: dict, cwd: Path, codex_home: Path) -> dict:
    """The ``config/read`` result with layers for this invocation."""
    merged, sourced = compose(args, scenario, cwd, codex_home)
    config = _merge(_DEFAULTS, merged)
    origins: dict = {}
    for key, entry in sourced.items():
        leaves: list = []
        _leaves(key, entry["value"], leaves)
        for leaf in leaves:
            origins[leaf] = _layer_name(entry["source"])
    layers = [{"name": _layer_name(source), "config": data, "disabledReason": disabled}
              for source, data, disabled in compose.layers]
    system = Path(scenario["system_dir"]) if scenario.get("system_dir") else Path("/nonexistent-codex-system")
    if not any(layer["name"]["type"] == "system" for layer in layers):
        layers.append({"name": {"type": "system", "file": str(system / "config.toml")}, "config": {},
                       "disabledReason": None})
    return {"config": config, "origins": origins, "layers": layers}


def _serve_app_server(scenario: dict, cwd: Path, home: Path, argv: list[str]) -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("method") == "initialize":
            print(json.dumps({"id": message["id"], "result": {"userAgent": VERSION}}), flush=True)
        elif message.get("method") == "config/read":
            result = config_read(argv, scenario, Path(message["params"].get("cwd", cwd)), home)
            print(json.dumps({"id": message["id"], "result": result}), flush=True)


def features(args: list[str], scenario: dict) -> dict[str, bool]:
    text = Path(scenario.get("features") or DEFAULT_FEATURES).read_text()
    state = {p[0]: p[-1] == "true" for p in (line.split() for line in text.splitlines()) if len(p) >= 3
             and p[-1] in ("true", "false")}
    for i, arg in enumerate(args[:-1]):
        if arg == "--disable":
            state[args[i + 1]] = False
        elif arg == "--enable":
            state[args[i + 1]] = True
    return state


# -- helpers ----------------------------------------------------------------


def _log(scenario: dict, record: dict) -> None:
    path = scenario.get("log")
    if path:
        with open(path, "a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")


def _run_command(command, cwd: Path) -> dict:
    """Start a composed server/hook/notify/auth command (a local test script)."""
    shell = isinstance(command, str)
    try:
        result = subprocess.run(command, shell=shell, cwd=cwd, capture_output=True, timeout=20, check=False,
                                stdin=subprocess.DEVNULL, env=dict(os.environ))
        return {"command": command, "exit": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "error": str(exc)}


def _command_of(table: dict):
    return table.get("command") if isinstance(table, dict) else None


def _write_state(home: Path, scenario: dict, kind: str, argv: list[str]) -> None:
    """The entries the captured runs left in a minimal home (``state_entries.txt``):
    ``tmp/`` always; ``installation_id``, the state databases and (``exec``)
    ``models_cache.json`` and ``thread-writer-locks/`` for the kinds that start a
    thread or a server; ``skills/.system`` only while the bundled skills are on."""
    if not home.is_dir():
        return
    (home / "tmp").mkdir(exist_ok=True)
    if kind in ("exec", "compose"):
        (home / "installation_id").write_text("id")
        for name in ("goals_1", "logs_2", "memories_1", "queue_1", "state_5", "thread_history_1"):
            (home / f"{name}.sqlite").write_text("db")
        bundled_off = any(a == "skills.bundled.enabled=false" for a in argv)
        if not bundled_off:
            (home / "skills" / ".system").mkdir(parents=True, exist_ok=True)
            (home / "skills" / ".system" / ".codex-system-skills.marker").write_text("1")
    if kind == "exec":
        (home / "models_cache.json").write_text("{}")
        (home / "thread-writer-locks").mkdir(exist_ok=True)
    for extra in scenario.get("state_extra", []):
        target = home / extra
        if extra.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")


_LIMIT_INFO = {"string": "usage_limit_exceeded", "http429": {"http_connection_failed": {"http_status_code": 429}}}


def _write_rollout(home: Path, scenario: dict, spec: dict | None = None, *, subagent_of: str | None = None,
                   tokens: int = 10441) -> str:
    """One rollout in the shapes of ``rollout_excerpt.jsonl``: ``session_meta``,
    ``token_count`` (with ``rate_limits``) and ``task_complete`` (carrying the
    ``error`` / ``codex_error_info`` of a limit when the scenario says so)."""
    spec = spec or {}
    now = datetime.datetime.now(datetime.timezone.utc)
    folder = home / "sessions" / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
    folder.mkdir(parents=True, exist_ok=True)
    limits = scenario.get("rate_limits") or {"primary": 7.0, "secondary": 3.0}
    stamp = now.isoformat().replace("+00:00", "Z")
    thread = str(uuid.uuid4())
    source = {"subagent": {"thread_spawn": {"parent_thread_id": subagent_of, "depth": 1}}} if subagent_of else "exec"
    limit = spec.get("verdict") == "limit" and subagent_of is None
    shape = spec.get("limit_shape", "string")
    reading = {"primary": {"used_percent": 100.0 if limit and shape == "percent" else limits["primary"],
                           "window_minutes": 300, "resets_at": int(now.timestamp()) + 3600},
               "secondary": {"used_percent": limits["secondary"], "window_minutes": 10080,
                             "resets_at": int(now.timestamp()) + 86400}}
    complete = {"type": "task_complete", "last_agent_message": None}
    if limit and shape in _LIMIT_INFO:
        complete["error"] = {"message": "You've hit your usage limit.", "codex_error_info": _LIMIT_INFO[shape]}
    lines = [
        {"timestamp": stamp, "type": "session_meta", "payload": {"id": thread, "source": source, "originator": "codex_exec"}},
        {"timestamp": stamp, "type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"total_tokens": tokens}}, "rate_limits": reading}},
        {"timestamp": stamp, "type": "event_msg", "payload": complete},
    ]
    (folder / f"rollout-{now:%Y-%m-%dT%H-%M-%S}-{thread}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines))
    return thread


def _header_from_prompt(prompt: str) -> list[str]:
    keys = ("Reviewed ", "Work item:", "Plan revision reviewed:", "Implementation revision reviewed:",
            "Reviewer role:", "Reviewer model:")
    return [line for line in prompt.splitlines() if line.startswith(keys)]


def _verdict(kind: str, prompt: str) -> str | None:
    header = _header_from_prompt(prompt)
    retry = "did not equal the required values" in prompt
    if kind in ("wrong_id_first", "revise_then_approve", "revise_then_revise", "no_status_then_exact") and not retry:
        header = [line[:-5] if line.startswith("Reviewed bundle ID:") else line for line in header]
    if kind == "bad_revision":
        header = [line for line in header if "revision reviewed" not in line]
    status = {"block": "BLOCK", "revise": "REVISE", "revise_then_revise": "REVISE"}.get(kind, "APPROVE")
    if kind == "revise_then_approve" and not retry:
        status = "REVISE"
    lines = ["# Review Decision", ""]
    if kind != "missing_status" and not (kind == "no_status_then_exact" and not retry):
        lines += [f"Status: {status}", ""]
    lines += [*header, "", "## Blocking findings", "", "None.", "", "## Required acceptance criteria", "", "None.", ""]
    return "\n".join(lines)


# -- the invocation kinds ---------------------------------------------------


def _arm_watchdog(scenario: dict) -> None:
    seconds = float(scenario.get("orphan_seconds", ORPHAN_SECONDS_DEFAULT))
    if seconds > 0:
        _install_watchdog(seconds)
    ready = scenario.get("ready_file")
    if ready:
        Path(f"{ready}.tmp").write_text(str(os.getpid()))
        os.replace(f"{ready}.tmp", ready)


def _install_watchdog(seconds: float) -> None:
    original_parent = os.getppid()
    orphaned_at: list[float] = []

    def check(_signum, _frame) -> None:
        if os.getppid() == original_parent:
            return
        now = time.monotonic()
        if not orphaned_at:
            orphaned_at.append(now)
        if now - orphaned_at[0] < seconds:
            return
        if os.getpgrp() == os.getpid():
            os.killpg(os.getpgrp(), signal.SIGKILL)
        os.kill(os.getpid(), signal.SIGKILL)

    signal.signal(signal.SIGALRM, check)
    signal.setitimer(signal.ITIMER_REAL, _ORPHAN_POLL_SECONDS, _ORPHAN_POLL_SECONDS)


def _roots(args: list[str], cwd: Path) -> dict:
    parsed = {".".join(k): v for k, v in (_parse_override(a) for a in
                                           (args[i + 1] for i, x in enumerate(args[:-1]) if x == "-c"))}
    return {"writable_roots": [str(cwd), *parsed.get("sandbox_workspace_write.writable_roots", [])],
            "network_access": parsed.get("sandbox_workspace_write.network_access", True),
            "approval_policy": parsed.get("approval_policy", "on-request"),
            "model": parsed.get("model"), "effort": parsed.get("model_reasoning_effort"),
            "service_tier": parsed.get("service_tier"), "web_search": parsed.get("web_search", "live"),
            "overrides": parsed}


def main() -> int:
    argv = sys.argv[1:]
    scenario = json.loads(Path(argv[argv.index("--scenario") + 1]).read_text())
    del argv[argv.index("--scenario"):argv.index("--scenario") + 2]
    if argv[:1] == ["--version"] or argv[:1] == ["-V"]:
        print(VERSION)
        return 0
    _arm_watchdog(scenario)
    cwd = Path(argv[argv.index("-C") + 1]) if "-C" in argv else Path.cwd()
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    positional = [a for i, a in enumerate(argv) if not a.startswith("-") and (i == 0 or argv[i - 1] not in (
        "-c", "--disable", "--enable", "-s", "-C", "-o", "--color", "-p", "--profile", "-m"))]
    kind = {"features": "features", "mcp": "mcp", "exec": "exec", "app-server": "compose"}.get(
        positional[0] if positional else "")
    record = {"kind": kind, "argv": argv, "cwd": str(cwd), "env": dict(os.environ), "codex_home": str(home),
              "ran": [], "tools": [], "writes": []}
    if kind is None:
        record["error"] = "unknown subcommand"
        _log(scenario, record)
        return 2
    if kind == "exec" and not (home / "auth.json").is_file():
        record["error"] = "not logged in"
        _log(scenario, record)
        print("Error: not logged in", file=sys.stderr)
        return 1
    merged, sourced = compose(argv, scenario, cwd, home)
    record.update(_roots(argv, cwd))
    state = features(argv, scenario)
    record["features_enabled"] = sorted(k for k, v in state.items() if v)
    _write_state(home, scenario, kind, [a for i, a in enumerate(argv) if i and argv[i - 1] == "-c"])
    code = 0
    if kind == "features":
        print("\n".join(f"{name:40} stable {'true' if on else 'false'}" for name, on in sorted(state.items())))
    elif kind == "mcp":
        servers = merged.get("mcp_servers", {})
        print("\n".join(f"{n}  {_command_of(t)}" for n, t in servers.items()) if servers
              else "No MCP servers configured yet. Try `codex mcp add my-tool -- my-command`.")
    elif kind == "compose":
        _serve_app_server(scenario, cwd, home, argv)
    else:
        code = _exec(argv, scenario, merged, state, cwd, home, record)
    record["exit"] = code
    _log(scenario, record)
    return code


def _exec(argv: list[str], scenario: dict, merged: dict, state: dict, cwd: Path, home: Path, record: dict) -> int:
    spec = scenario.get("exec", {})
    prompt = sys.stdin.read() if argv[-1] == "-" else argv[-1]
    for name, table in merged.get("mcp_servers", {}).items():
        record["ran"].append({"layer": "mcp_servers", "name": name, **_run_command(_command_of(table), cwd)})
    if state.get("hooks"):
        for name, table in merged.get("hooks", {}).items():
            record["ran"].append({"layer": "hooks", "name": name, **_run_command(_command_of(table), cwd)})
    provider = merged.get("model_provider", "openai")
    if provider != "openai":
        auth = _command_of(merged.get("model_providers", {}).get(provider, {}).get("auth", {}))
        record["ran"].append({"layer": "provider", "name": provider, **(_run_command(auth, cwd) if auth else {})})
        record["provider"] = provider
    record["endpoint"] = merged.get("openai_base_url") or merged.get("chatgpt_base_url")
    for feature in spec.get("tool_attempts", []):
        record["tools"].append({"feature": feature, "used": bool(state.get(feature))})
    if spec.get("use_web_search") and record["web_search"] != "disabled":
        record["tools"].append({"feature": "web_search", "used": True})
    roots = [Path(r).resolve() for r in record["writable_roots"]]
    for target in spec.get("write_outside", []):
        path = Path(target).resolve()
        allowed = any(path == r or r in path.parents for r in roots)
        record["writes"].append({"path": str(path), "blocked": not allowed})
        if allowed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fake codex wrote this")
    if spec.get("escape_to"):
        Path(spec["escape_to"]).write_text("sandbox escape")
        record["writes"].append({"path": spec["escape_to"], "blocked": False, "escaped": True})
    for command in spec.get("git", []):
        result = subprocess.run(["git", "-C", str(cwd), *command], capture_output=True, check=False,
                                env={**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@y",
                                     "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@y"})
        record["ran"].append({"layer": "git", "command": command, "exit": result.returncode})
    if spec.get("refresh_auth"):
        path = home / "auth.json"
        data = json.loads(path.read_text())
        data["tokens"] = {"refresh_token": "rotated-" + uuid.uuid4().hex}
        path.write_text(json.dumps(data))
        record["refreshed_auth"] = True
    if spec.get("descendant"):
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdin=subprocess.DEVNULL)
    if not any(a.startswith("projects=") and str(cwd) in a for a in argv):
        # the pinned Codex auto-trusts the workspace-write cwd by appending to the home's config.toml
        with open(home / "config.toml", "a") as fh:
            fh.write(f'[projects."{cwd}"]\ntrust_level = "trusted"\n')
    thread = _write_rollout(home, scenario, spec)
    if spec.get("subagent"):
        _write_rollout(home, scenario, spec, subagent_of=thread, tokens=int(spec["subagent"]))
    if spec.get("stderr"):
        print(spec["stderr"], file=sys.stderr)
    notify = merged.get("notify")
    verdict_kind = spec.get("verdict", "exact")
    if verdict_kind == "hang":
        end = time.monotonic() + float(scenario.get("hang_seconds", 120))
        while time.monotonic() < end:
            time.sleep(0.05)
    code = int(spec.get("exit", 0))
    if verdict_kind == "limit":
        print("ERROR: You've hit your usage limit.", file=sys.stderr)
        code = code or 1
    elif verdict_kind != "none":
        out = argv[argv.index("-o") + 1] if "-o" in argv else None
        text = _verdict(verdict_kind, prompt)
        if out and text:
            Path(out).write_text(text)
    if notify:
        record["ran"].append({"layer": "notify", **_run_command(notify, cwd)})
    return code


if __name__ == "__main__":
    sys.exit(main())
