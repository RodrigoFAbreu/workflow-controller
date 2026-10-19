"""A stand-in for the ``claude`` CLI as the functional tester's launch runs it
(``workflow-controller-automated-functional-review`` CP3, D10).

``review.launch_functional`` runs ``claude -p ... --settings <file>`` in the
sandbox's output folder with the sandbox's environment, so (like
``fake_claude_review.py``) the fake is driven by a scenario file written with
:func:`write_binary`. It spends nothing and connects nowhere.

It **emulates the CLI's sandbox** for the writes it makes: a write is refused
when the settings file's ``denyWrite``/``allowWrite`` lists refuse it (the most
specific entry wins, a tie goes to the deny, a path in neither list is refused).
That checks the Controller hands the CLI the right settings; the enforcement
itself is proved on the real CLI in ``tests/test_functional_enforcement.py``.
``"emulate_sandbox": false`` turns the emulation off, which is what a sandbox
that failed would look like, so the detection layers can be shown to work alone.

Scenario keys (all optional): ``log`` (JSON lines of what the run did),
``actions`` (a list, run in order), ``exit``, ``hang_seconds``, ``verdict``
(``ok``, ``limit`` or ``error``), ``init`` (fields of the ``init`` event to
override) and ``emulate_sandbox``.

Actions, each ``{"op": ..., ...}``:

- ``results`` -- ``entries`` (RESULTS.json's list) and ``logs`` (name -> text),
  written into the working directory (the output folder);
- ``write`` -- ``path``, ``data``: one write, by absolute path (a relative
  ``path`` is below the clone);
- ``restore`` -- ``path`` (relative to the clone), ``new``: write, run a check
  that reads it, restore the original bytes;
- ``shell`` -- ``argv``: run a command with the environment as launched;
- ``copy_edit`` -- copy the clone into ``TMPDIR``, edit the copy, read it back;
- ``git_config`` -- ``key``, ``value``: append to the clone's ``.git/config``;
- ``session`` -- create a transcript in ``CLAUDE_CONFIG_DIR/projects``;
- ``sleep`` -- ``seconds``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_claude_review as fcr  # noqa: E402  (by file: tests/__init__ rewrites the environment)


def write_binary(directory: Path, scenario: dict, *, name: str = "claude") -> Path:
    """Write ``directory/<name>`` (an executable wrapper) and its scenario file."""
    directory.mkdir(parents=True, exist_ok=True)
    scenario_path = directory / f"{name}.scenario.json"
    scenario_path.write_text(json.dumps(scenario))
    wrapper = directory / name
    wrapper.write_text(
        f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(HERE)!r})\n"
        f"sys.argv[1:1] = ['--scenario', {str(scenario_path)!r}]\n"
        "import fake_functional_tester\nsys.exit(fake_functional_tester.main())\n")
    wrapper.chmod(0o755)
    return wrapper


def read_log(path: str | Path) -> list[dict]:
    return fcr.read_log(path)


class Sandbox:
    """The CLI's sandbox as the settings file states it."""

    def __init__(self, settings: dict, emulate: bool) -> None:
        filesystem = settings.get("sandbox", {}).get("filesystem", {})
        self.allow = [Path(p) for p in filesystem.get("allowWrite", [])]
        self.deny = [Path(p) for p in filesystem.get("denyWrite", [])]
        self.emulate = emulate

    def allows(self, path: Path) -> bool:
        if not self.emulate:
            return True
        path = Path(os.path.abspath(path))
        best: tuple[int, bool] | None = None
        for entries, allowed in ((self.allow, True), (self.deny, False)):
            for entry in entries:
                if path == entry or entry in path.parents:
                    depth = len(entry.parts)
                    if best is None or depth > best[0] or (depth == best[0] and not allowed):
                        best = (depth, allowed)
        return best is not None and best[1]


def _act(action: dict, ctx: dict) -> None:
    op, sandbox, clone, log = action["op"], ctx["sandbox"], ctx["clone"], ctx["record"]["actions"]
    if op == "results":
        out = Path.cwd()
        for name, text in action.get("logs", {}).items():
            (out / name).write_text(text)
        if "raw" in action:
            (out / "RESULTS.json").write_text(action["raw"])
        else:
            (out / "RESULTS.json").write_text(json.dumps(action["entries"]))
        for name, target in action.get("symlinks", {}).items():
            os.symlink(target, out / name)
        log.append({"op": op, "ok": True})
    elif op in ("write", "git_config"):
        if op == "git_config":
            path = clone / ".git" / "config"
            data = f"[{action['key']}]\n\t{action['value']}\n"
        else:
            path = Path(action["path"]) if os.path.isabs(action["path"]) else clone / action["path"]
            data = action.get("data", "x\n")
        if not sandbox.allows(path):
            log.append({"op": op, "path": str(path), "refused": True})
            return
        if action.get("mode") == "append" or op == "git_config":
            with open(path, "a") as fh:
                fh.write(data)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        if action.get("fifo"):
            os.mkfifo(path)
        log.append({"op": op, "path": str(path), "refused": False})
    elif op == "restore":
        path = clone / action["path"]
        original = path.read_bytes()
        if not sandbox.allows(path):
            log.append({"op": op, "path": str(path), "refused": True})
            return
        path.write_text(action["new"])
        seen = path.read_text()  # the check that "passes on the edit"
        path.write_bytes(original)
        log.append({"op": op, "path": str(path), "refused": False, "check_saw": seen})
    elif op == "copy_edit":
        scratch = Path(os.environ["TMPDIR"]) / "copy"
        subprocess.run(["cp", "-a", str(clone), str(scratch)], check=True)
        target = scratch / action["path"]
        target.write_text(action["new"])
        log.append({"op": op, "copy": str(scratch), "check_saw": target.read_text()})
    elif op == "shell":
        done = subprocess.run(action["argv"], capture_output=True, cwd=action.get("cwd") or clone,
                              timeout=60, check=False)
        log.append({"op": op, "argv": action["argv"], "exit": done.returncode,
                    "stderr": done.stderr.decode("utf-8", "replace")[-300:]})
    elif op == "session":
        project = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "nested"
        project.mkdir(parents=True, exist_ok=True)
        (project / "session.jsonl").write_text("{}\n")
        log.append({"op": op})
    elif op == "sleep":
        time.sleep(float(action["seconds"]))
    else:
        raise ValueError(f"unknown action {op!r}")


def main() -> int:
    argv = sys.argv[1:]
    scenario = json.loads(Path(argv[argv.index("--scenario") + 1]).read_text())
    del argv[argv.index("--scenario"):argv.index("--scenario") + 2]
    if argv[:1] == ["--version"]:
        print(fcr.VERSION)
        return 0
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    credential = config / ".credentials.json"
    prompt = sys.stdin.read()
    model = fcr._value(argv, "--model") or "claude-sonnet-5-5"
    record: dict = {"argv": argv, "cwd": str(Path.cwd()), "env": dict(os.environ), "prompt": prompt, "actions": []}
    if not credential.is_file():
        fcr._log(scenario, record)
        print(json.dumps(fcr._result_event("Not logged in", scenario, model, error=True, status=None)))
        return 1
    settings_path = fcr._value(argv, "--settings")
    settings = json.loads(Path(settings_path).read_text()) if settings_path else {}
    record["settings"] = settings
    sandbox = Sandbox(settings, scenario.get("emulate_sandbox", True))
    clone = Path(scenario.get("clone") or "")
    if not clone.name and settings:
        clone = Path(settings["sandbox"]["filesystem"]["denyWrite"][0])
    out = [fcr._init_event(argv, [], scenario, Path.cwd())]
    ctx = {"sandbox": sandbox, "clone": clone, "record": record}
    if scenario.get("hang_seconds"):
        time.sleep(float(scenario["hang_seconds"]))
    for action in scenario.get("actions", []):
        _act(action, ctx)
    kind = scenario.get("verdict", "ok")
    out.append(fcr._rate_limit_event(kind == "limit"))
    if kind == "limit":
        out.append(fcr._result_event("You've hit your limit", scenario, model, error=True, status=429))
    elif kind == "error":
        out.append(fcr._result_event("Overloaded", scenario, model, error=True, status=529))
    elif kind == "unavailable":
        event = fcr._result_event("", scenario, model, error=True, status=None)
        event["errors"] = ["Error: sandbox required but unavailable: dependencies are missing: bubblewrap"]
        out.append(event)
    else:
        out.append(fcr._result_event("done", scenario, model, error=False, status=None))
    sys.stdout.write("".join(json.dumps(e) + "\n" for e in out))
    sys.stdout.flush()
    fcr._log(scenario, record)
    return int(scenario.get("exit", 1 if kind in ("limit", "error", "unavailable") else 0))
