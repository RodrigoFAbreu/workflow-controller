#!/usr/bin/env python3
"""Capture the harness contract fixtures in this directory.

Never run by the test suite: it launches the real, installed ``claude``
(network, real spend), and is run by hand when the fixtures are
(re)measured -- ``workflow-controller-worker-lifecycle-ownership`` CP1,
and again whenever the installed ``claude`` changes. See ``README.md`` for
what each fixture pins.

Two jobs:

``python3 tests/harness_contract/capture.py probe <name>... [--raw-dir DIR]``
    Runs each named probe (or ``all``) against the installed ``claude``,
    each in its own scratch working directory and its own capture
    process, in parallel, and writes ``<name>.jsonl`` (the redacted
    stream) and ``<name>.meta.json`` (argv, exit status, per-line arrival
    offsets and the probe's own post-exit observations) beside this file.
    The raw, unredacted stream is kept in ``--raw-dir`` only.

``python3 tests/harness_contract/capture.py import-job <job_dir> <name>``
    Redacts a real Controller job's ``worker.stdout`` into ``<name>.jsonl``
    (the observed failing jobs; the structural events are kept, every
    assistant text, tool input and tool output is replaced by a
    placeholder).

The probe argv is the production argv of the plan's design A: streaming
input, ``--permission-mode auto``, the route's effort, the Controller's
worker lifecycle system note and the full disallow list, overriding only
the model (haiku). CP3 extracts the Controller's own argv construction
into ``controller.worker.build_worker_argv``; until then
:func:`production_argv` below mirrors it, and from CP3 on it calls it.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = str(Path.home())
USER = Path.home().name

#: The model every probe overrides to (cheap, fast). Everything else in the
#: argv is the production value.
PROBE_MODEL = "haiku"
#: The production model (``controller.routing.DEFAULT_MODEL``). Measured
#: with ``claude`` 2.1.282: haiku does not support ``--permission-mode
#: auto`` -- its ``system/init`` reports ``permissionMode: default`` -- and
#: in ``-p`` mode the default mode denies every Bash command that writes a
#: file or backgrounds a process (``system/permission_denied``). The probes
#: that need such a command (P1, P2, P4) therefore run on the production model,
#: which keeps ``auto``; their argv is then the production argv exactly.
PRODUCTION_MODEL = "claude-opus-5-5"
#: The built-in route effort (``controller.routing.DEFAULT_EFFORT``).
PROBE_EFFORT = "xhigh"
PERMISSION_MODE = "auto"

#: The Controller's worker lifecycle system note (plan decision 11), sent
#: with ``--append-system-prompt``. CP3 defines it as
#: ``controller.worker.WORKER_LIFECYCLE_NOTE``; this is the same text.
WORKER_LIFECYCLE_NOTE = (
    "Workflow Controller worker lifecycle: the Controller keeps this session open while you own "
    "background work and delivers every background-task, Monitor and subagent notification to you "
    "as a new turn. Do not schedule fallback wakeups (ScheduleWakeup) in case a notification never "
    "arrives, and cancel any wakeup you no longer need (ScheduleWakeup with stop: true) before "
    "ending your final turn."
)

SUBAGENT_TOOLS = ("Agent", "Workflow", "Skill")
ASYNC_UNOWNABLE_TOOLS = ("CronCreate", "CronDelete", "RemoteTrigger")

#: The done marker every streaming probe's final reply carries.
DONE = "PROBE-DONE"


def production_argv(claude_bin: str, *, single_agent: bool, streaming: bool = True,
                    prompt: str | None = None, model: str = PROBE_MODEL) -> list[str]:
    """The plan's design-A argv (the model overridden to haiku). ``streaming
    False`` is today's print-mode argv (``-p <prompt>``), for P1/P2."""
    args = [claude_bin, "-p"]
    if streaming:
        args += ["--input-format", "stream-json"]
    else:
        args += [prompt or ""]
    args += ["--output-format", "stream-json", "--verbose", "--permission-mode", PERMISSION_MODE,
             "--model", model, "--effort", PROBE_EFFORT]
    if streaming:
        args += ["--append-system-prompt", WORKER_LIFECYCLE_NOTE]
    tools = (SUBAGENT_TOOLS if single_agent else ()) + ASYNC_UNOWNABLE_TOOLS
    args += ["--disallowedTools", ",".join(tools)]
    return args


def user_message(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


# ---------------------------------------------------------------------------
# Probes.
# ---------------------------------------------------------------------------

#: A descendant probe's markers (``argv[0]``), per probe, so probes run in
#: parallel never see each other's processes.
ESCAPE_MARKER = "wlo-{probe}-escaped"
ATTACHED_MARKER = "wlo-{probe}-attached"

PROBES: dict[str, dict] = {
    "p1_print_mode_background_bash": {
        "pins": "P1: print mode ends the session at the first idle turn and kills the open task",
        "streaming": False,
        "model": PRODUCTION_MODEL,
        "prompt": (
            "Use the Bash tool with run_in_background set to true to run exactly this command: "
            "`sleep 30; echo finished > p1.out`. Do not wait for it and do not check on it. "
            f"Immediately after starting it, reply with exactly: {DONE}"
        ),
        "post_wait": 40,
    },
    "p2_escaped_descendants": {
        "pins": "P2: an orphaned setsid descendant survives the harness's kill, keeps the "
                "ownership tag in its environ and does not hold the descriptor passed to claude",
        "streaming": False,
        "model": PRODUCTION_MODEL,
        "prompt": (
            "Use the Bash tool with run_in_background set to true to run exactly this command: "
            "`(setsid bash -c 'exec -a {escaped} sleep 90' </dev/null >/dev/null 2>&1 &); "
            "bash -c 'exec -a {attached} sleep 90' & sleep 60`. "
            f"Do not wait for it. Immediately after starting it, reply with exactly: {DONE}"
        ),
        "descendants": True,
    },
    "p2_escaped_descendants_subreaper": {
        "pins": "P2: with the supervising process a child subreaper, the orphan is adopted by it",
        "streaming": False,
        "model": PRODUCTION_MODEL,
        "subreaper": True,
        "prompt": None,  # p2_escaped_descendants's
        "descendants": True,
    },
    "p3_streaming_background_bash": {
        "pins": "P3: streaming input keeps the session alive across a background task; its "
                "completion starts a new turn (P11: the task-completion turn's origin)",
        "prompt": (
            "Use the Bash tool with run_in_background set to true to run exactly: "
            "`sleep 15; echo p3-task-output`. Then reply with exactly STARTED and end your turn "
            "without waiting or polling. When you are later notified that the task completed, "
            f"reply with exactly: {DONE} FINISHED"
        ),
    },
    "p4_monitor": {
        "pins": "P4: a Monitor is a local_bash background task and each event starts a turn "
                "(P11: the Monitor event turn's origin)",
        "model": PRODUCTION_MODEL,  # haiku's default mode denies the watched loop ("simple_expansion")
        "prompt": (
            "Use the Monitor tool to watch exactly this command: "
            "`for i in 1 2 3; do sleep 6; echo tick-$i; done`, with a description of "
            "'probe ticks' and timeout_ms 120000. Then reply with exactly WATCHING and end your "
            "turn. For every monitor event you receive, reply with just the tick text. Once the "
            f"monitor has completed (after tick-3), reply with exactly: {DONE}"
        ),
    },
    "p6_p10_slash_command": {
        "pins": "P6/P10: a slash command sent as the stream-json user message expands as the "
                "-p prompt did, with --append-system-prompt and the single-agent disallow list",
        "prompt": "/probecmd alpha-42",
        "commands": {"probecmd.md": f"Reply with exactly this text and nothing else: {DONE} ARG=$ARGUMENTS\n"},
    },
    "p8_task_stop": {
        "pins": "P8: a worker-initiated TaskStop of its own background task, mid-session",
        "prompt": (
            "Use the Bash tool with run_in_background set to true to run exactly: `sleep 300`. "
            "Then use the TaskStop tool to stop that background task. Then run the Bash command "
            f"`echo still-here` in the foreground. Then reply with exactly: {DONE}"
        ),
    },
    "p8_monitor_timeout": {
        "pins": "P8: a Monitor that reaches its own timeout, mid-session",
        "prompt": (
            "Use the Monitor tool to watch exactly this command: `sleep 300; echo never`, with a "
            "description of 'probe timeout' and timeout_ms 15000. Then reply with exactly "
            "WATCHING and end your turn. When you are notified that the monitor ended or timed "
            f"out, reply with exactly: {DONE}"
        ),
    },
    "p9_wakeup_cancel": {
        "pins": "P9: ScheduleWakeup {stop: true} after a pending wakeup produces no fire turn",
        "prompt": (
            "Use the ScheduleWakeup tool with delaySeconds 60, prompt 'P9 wakeup fired' and "
            "reason 'contract probe'. Then use the ScheduleWakeup tool again with stop set to "
            f"true, to cancel it. Then reply with exactly: {DONE}"
        ),
        "linger": 200,
    },
    "p5_p11_wakeup_fires": {
        "pins": "P5/P11: a ScheduleWakeup fires as a new turn; the fire turn's result origin, "
                "captured twice under different prompt texts, and its opening user event",
        "prompt": (
            "This is a two-step wakeup test. Step 1: use the ScheduleWakeup tool with "
            "delaySeconds 60, prompt 'P11 fire ALPHA' and reason 'contract probe alpha', then "
            "reply with exactly WAITING-ALPHA and end your turn. Step 2: when you are woken with "
            "'P11 fire ALPHA', use the ScheduleWakeup tool with delaySeconds 60, prompt "
            "'P11 fire BRAVO second' and reason 'contract probe bravo', then reply with exactly "
            "WAITING-BRAVO and end your turn. Step 3: when you are woken with 'P11 fire BRAVO "
            f"second', reply with exactly: {DONE}"
        ),
    },
    "p11_subagent_handback": {
        "pins": "P11: the turn after a background subagent's hand-back (multi-agent route)",
        "single_agent": False,
        "prompt": (
            "Use the Agent tool with subagent_type 'general-purpose', run_in_background set to "
            "true, description 'probe handback' and prompt 'Reply with exactly the word "
            "HANDBACK and nothing else. Do not use any tools.' Then reply with exactly STARTED "
            "and end your turn without waiting. When the subagent's result is delivered to you, "
            f"reply with exactly: {DONE}"
        ),
    },
}
PROBES["p2_escaped_descendants_subreaper"]["prompt"] = PROBES["p2_escaped_descendants"]["prompt"]

#: P12 (plan revision 12+, CP1): a ``ScheduleWakeup {stop: true}`` issued
#: from inside a fire turn. Admission and the gate live in
#: ``p12_admission.py``, never here. The exact arguments, the named
#: sequence, the three markers and ``"linger": 200`` are the plan's contract;
#: only the surrounding wording may be changed by CP1.
P12_ALPHA_ARGS = ("delaySeconds 60, prompt 'P12 fire ALPHA', reason 'contract probe alpha', "
                  "noop false")
P12_BRAVO_ARGS = ("delaySeconds 60, prompt 'P12 fire BRAVO', reason 'contract probe bravo', "
                  "noop false")
P12_RULES = (
    "This is a wakeup contract test. Make each tool call named below exactly once, with exactly "
    "the arguments given and no other argument. Never retry a call and never change its "
    "arguments, whatever a tool returns. "
)
P12_OTHER_TURNS = (
    " In any other turn, call no tool at all and reply with exactly: PROBE-EXTRA"
)
PROBES["p12_stop_inside_fire_single"] = {
    "pins": "P12: ScheduleWakeup {stop: true} inside the fire turn of the only wakeup "
            "(the plan's settlement rule expects cancelledWakeups 0)",
    "prompt": (
        P12_RULES
        + f"Step 1 (now): call the ScheduleWakeup tool once with exactly: {P12_ALPHA_ARGS}. "
        "Call no other tool in this turn. Then reply with exactly WAITING-ALPHA and end your "
        "turn. Step 2: when you are woken with 'P12 fire ALPHA', call the ScheduleWakeup tool "
        "once with exactly: stop true. Call no other tool in that turn. Then reply with "
        f"exactly: {DONE}" + P12_OTHER_TURNS
    ),
    "linger": 200,
}
PROBES["p12_stop_inside_fire_nested"] = {
    "pins": "P12: inside ALPHA's fire turn, schedule BRAVO and then ScheduleWakeup "
            "{stop: true} (the plan's settlement rule expects cancelledWakeups 1)",
    "prompt": (
        P12_RULES
        + f"Step 1 (now): call the ScheduleWakeup tool once with exactly: {P12_ALPHA_ARGS}. "
        "Call no other tool in this turn. Then reply with exactly WAITING-ALPHA and end your "
        "turn. Step 2: when you are woken with 'P12 fire ALPHA', first call the ScheduleWakeup "
        f"tool once with exactly: {P12_BRAVO_ARGS}; then call the ScheduleWakeup tool once "
        "with exactly: stop true. Call no other tool in that turn. Then reply with exactly: "
        f"{DONE}" + P12_OTHER_TURNS
    ),
    "linger": 200,
}

PR_SET_CHILD_SUBREAPER = 36
OWNERSHIP_VAR = "WORKFLOW_CONTROLLER_OWNERSHIP"


def _proc_stat(pid: int) -> dict | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rest = text[text.rfind(")") + 1:].split()
    return {"state": rest[0], "ppid": int(rest[1]), "pgid": int(rest[2]), "sid": int(rest[3]),
            "start_ticks": int(rest[19])}


def _find_marked(marker: str) -> list[int]:
    pids = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{name}/cmdline").read_bytes()
        except OSError:
            continue
        if cmdline.split(b"\0", 1)[0] == marker.encode():
            pids.append(int(name))
    return pids


def _descendant_facts(pid: int, lock_path: str, tag: str) -> dict:
    stat = _proc_stat(pid)
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        env_tag = any(item == f"{OWNERSHIP_VAR}={tag}".encode() for item in environ)
    except OSError as exc:
        env_tag = f"unreadable: {exc.__class__.__name__}"
    holds = False
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                if os.readlink(f"/proc/{pid}/fd/{fd}") == lock_path:
                    holds = True
            except OSError:
                pass
    except OSError:
        holds = None
    return {"alive": stat is not None and stat["state"] not in ("Z", "X"), "stat": stat,
            "environ_has_tag": env_tag, "holds_lock_descriptor": holds}


def run_probe(name: str, raw_dir: Path) -> dict:
    probe = PROBES[name]
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        raise SystemExit("no claude on PATH")
    version = subprocess.run([claude_bin, "--version"], capture_output=True, text=True).stdout.strip()
    workdir = Path(tempfile.mkdtemp(prefix=f"wlo-{name}-"))
    for command_name, text in (probe.get("commands") or {}).items():
        (workdir / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
        (workdir / ".claude" / "commands" / command_name).write_text(text)
    markers = {"escaped": ESCAPE_MARKER.format(probe=name), "attached": ATTACHED_MARKER.format(probe=name)}
    if probe.get("descendants"):
        probe = {**probe, "prompt": probe["prompt"].format(**markers)}
    streaming = probe.get("streaming", True)
    single_agent = probe.get("single_agent", True)
    argv = production_argv(claude_bin, single_agent=single_agent, streaming=streaming,
                           prompt=None if streaming else probe["prompt"],
                           model=probe.get("model", PROBE_MODEL))
    tag = f"probe-{name}"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env[OWNERSHIP_VAR] = tag
    if probe.get("subreaper"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            raise SystemExit("prctl(PR_SET_CHILD_SUBREAPER) failed")
    lock_path = str(workdir / "probe.lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    raw_path = raw_dir / f"{name}.raw.jsonl"
    started = time.monotonic()
    proc = subprocess.Popen(
        argv, cwd=workdir, env=env, start_new_session=True, pass_fds=(lock_fd,),
        stdin=subprocess.PIPE if streaming else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if streaming:
        proc.stdin.write(user_message(probe["prompt"]).encode())
        proc.stdin.flush()
    arrivals: list[float] = []
    lines: list[str] = []
    state = {"open": set(), "done_at": None, "closed_at": None}
    deadline = started + float(probe.get("max_seconds", 600))
    linger = float(probe.get("linger", 0))

    def close_stdin() -> None:
        if streaming and state["closed_at"] is None:
            state["closed_at"] = round(time.monotonic() - started, 3)
            try:
                proc.stdin.close()
            except OSError:
                pass

    def watchdog() -> None:
        while proc.poll() is None:
            now = time.monotonic()
            if now > deadline:
                close_stdin()
                if now > deadline + 60:
                    proc.kill()
            elif state["done_at"] is not None and not state["open"] and now - started >= state["done_at"] + linger:
                close_stdin()
            time.sleep(0.2)

    threading.Thread(target=watchdog, daemon=True).start()
    for raw in proc.stdout:
        line = raw.decode()
        lines.append(line)
        arrivals.append(round(time.monotonic() - started, 3))
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "background_tasks_changed":
            state["open"] = {task.get("id") for task in event.get("tasks") or []}
        if event.get("type") == "result" and DONE in str(event.get("result") or "") and state["done_at"] is None:
            state["done_at"] = round(time.monotonic() - started, 3)
    stderr = proc.stderr.read().decode()
    returncode = proc.wait()
    ended = round(time.monotonic() - started, 3)
    raw_path.write_text("".join(lines))
    observations: dict = {}
    if probe.get("post_wait"):
        time.sleep(max(0.0, probe["post_wait"] - ended))
        observations["task_output_exists"] = (workdir / "p1.out").exists()
    if probe.get("descendants"):
        time.sleep(2)
        for label, marker in markers.items():
            pids = _find_marked(marker)
            observations[label] = [_descendant_facts(pid, lock_path, tag) for pid in pids] or "none alive"
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        observations["capture_pid"] = os.getpid()
        observations["capture_is_subreaper"] = bool(probe.get("subreaper"))
    os.close(lock_fd)
    meta = {
        "probe": name, "pins": probe["pins"], "claude_version": version,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "argv": [_redact_text(a, str(workdir)) for a in argv[1:]],
        "stdin": "stream-json user message, held open" if streaming else "/dev/null",
        "prompt": probe["prompt"], "returncode": returncode, "stderr": _redact_text(stderr, str(workdir)),
        "done_at": state["done_at"], "stdin_closed_at": state["closed_at"], "exited_at": ended,
        "line_arrival_seconds": arrivals, "observations": observations,
    }
    events = [json.loads(line) for line in lines if line.strip()]
    write_fixture(name, [redact_probe_event(e, str(workdir)) for e in events], meta)
    shutil.rmtree(workdir, ignore_errors=True)
    return meta


# ---------------------------------------------------------------------------
# Redaction.
# ---------------------------------------------------------------------------

#: The ``system/init`` fields a fixture keeps. The rest (plugins, skills,
#: slash commands, MCP servers, memory paths, ...) describe the capturing
#: machine's own configuration, not the contract.
INIT_FIELDS = ("type", "subtype", "session_id", "cwd", "tools", "model", "permissionMode",
               "claude_code_version", "uuid")
PLACEHOLDER = "<redacted>"


def _redact_text(text: str, workdir: str | None = None) -> str:
    if workdir:
        text = text.replace(workdir, "<workdir>")
    return text.replace(HOME, "<home>").replace(USER, "<user>")


def _deep(value, fn):
    if isinstance(value, dict):
        return {k: _deep(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep(v, fn) for v in value]
    if isinstance(value, str):
        return fn(value)
    return value


def _init(event: dict) -> dict:
    return {k: event[k] for k in INIT_FIELDS if k in event}


def redact_probe_event(event: dict, workdir: str) -> dict:
    """A probe's own event: the probe's prompts and outputs are not
    repository content, so they are kept; the capturing machine's paths and
    configuration and the opaque thinking signatures are not."""
    if event.get("type") == "system" and event.get("subtype") == "init":
        event = _init(event)
    if event.get("type") == "assistant":
        event = dict(event)
        message = dict(event.get("message") or {})
        message["content"] = [
            {"type": "thinking", "thinking": PLACEHOLDER} if item.get("type") == "thinking" else item
            for item in message.get("content") or []
        ]
        event["message"] = message
    return _deep(event, lambda s: _redact_text(s, workdir))


def _scalars_only(value):
    if isinstance(value, dict):
        return {k: _scalars_only(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scalars_only(v) for v in value]
    if isinstance(value, str):
        return PLACEHOLDER
    return value


#: The fields a real job's ``assistant``/``user`` event keeps: identity,
#: ordering and timing. ``wire_tool_inputs``, ``tool_use_result``, usage and
#: the like carry tool inputs and outputs, so they are dropped.
MESSAGE_EVENT_FIELDS = ("type", "subtype", "session_id", "uuid", "parent_tool_use_id", "timestamp",
                        "isReplay", "isSynthetic", "origin", "message")
MESSAGE_FIELDS = ("role", "content", "model", "stop_reason")

#: Tools whose ``tool_result`` text is written by the harness itself (the
#: stated schedule), not by the worker or the repository.
HARNESS_AUTHORED_RESULTS = ("ScheduleWakeup",)


def redact_job_event(event: dict, tool_names: dict) -> dict:
    """A real job's event: structural events verbatim (``system/*`` apart
    from init's machine configuration, ``result`` apart from its text,
    ``tool_use``/``tool_result`` names and ids); assistant text, thinking,
    tool inputs (their strings) and tool outputs become placeholders."""
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        event = _init(event)
    elif kind in ("assistant", "user"):
        event = {k: v for k, v in event.items() if k in MESSAGE_EVENT_FIELDS}
        if isinstance(event.get("origin"), dict):
            event["origin"] = _scalars_only(event["origin"])
        message = {k: v for k, v in (event.get("message") or {}).items() if k in MESSAGE_FIELDS}
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = PLACEHOLDER
        else:
            items = []
            for item in content or []:
                item_type = item.get("type")
                if item_type == "tool_use":
                    tool_names[item.get("id")] = item.get("name")
                    items.append({"type": "tool_use", "id": item.get("id"), "name": item.get("name"),
                                  "input": _scalars_only(item.get("input") or {})})
                elif item_type == "tool_result":
                    kept = tool_names.get(item.get("tool_use_id")) in HARNESS_AUTHORED_RESULTS
                    items.append({"type": "tool_result", "tool_use_id": item.get("tool_use_id"),
                                  "is_error": item.get("is_error"),
                                  "content": item.get("content") if kept else PLACEHOLDER})
                else:
                    items.append({"type": item_type, **({"text": PLACEHOLDER} if item_type == "text" else {})})
            message["content"] = items
        event["message"] = message
    elif kind == "result":
        event = dict(event)
        if "result" in event:
            event["result"] = PLACEHOLDER
        if isinstance(event.get("origin"), dict) and "body" in event["origin"]:
            event["origin"] = {**event["origin"], "body": PLACEHOLDER}
        event.pop("permission_denials", None) if not event.get("permission_denials") else None
    return _deep(event, _redact_text)


def write_fixture(name: str, events: list[dict], meta: dict) -> None:
    (HERE / f"{name}.jsonl").write_text(
        "".join(json.dumps(e, separators=(",", ":"), sort_keys=False) + "\n" for e in events))
    (HERE / f"{name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def import_job(job_dir: Path, name: str) -> None:
    stream = job_dir / "worker.stdout"
    tool_names: dict = {}
    events = [redact_job_event(json.loads(line), tool_names) for line in stream.read_text().splitlines() if line.strip()]
    meta = {"source": f"job {job_dir.name}", "source_file": "worker.stdout", "mode": "print",
            "lines": len(events), "redaction": "structural events verbatim; texts, tool inputs' strings "
            "and tool outputs replaced by placeholders"}
    write_fixture(name, events, meta)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("names", nargs="+")
    probe.add_argument("--raw-dir", default=None)
    probe.add_argument("--one", action="store_true", help=argparse.SUPPRESS)
    imp = sub.add_parser("import-job")
    imp.add_argument("job_dir")
    imp.add_argument("name")
    args = parser.parse_args(argv)
    if args.command == "import-job":
        import_job(Path(args.job_dir), args.name)
        return 0
    raw_dir = Path(args.raw_dir or tempfile.mkdtemp(prefix="wlo-raw-"))
    raw_dir.mkdir(parents=True, exist_ok=True)
    names = list(PROBES) if args.names == ["all"] else args.names
    if args.one:
        meta = run_probe(names[0], raw_dir)
        print(json.dumps({k: meta[k] for k in ("probe", "returncode", "done_at", "exited_at")}))
        return 0
    # Each probe in its own capture process: the subreaper probe marks its
    # own process only.
    procs = {n: subprocess.Popen([sys.executable, __file__, "probe", n, "--one", "--raw-dir", str(raw_dir)])
             for n in names}
    failed = [n for n, p in procs.items() if p.wait() != 0]
    print(f"raw streams in {raw_dir}; failed: {failed or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
