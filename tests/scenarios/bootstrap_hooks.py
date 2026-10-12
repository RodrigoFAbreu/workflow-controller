"""The in-process hooks of a scenario's interpreters (D7, D9, scenario 4).

Standalone (standard library only): the kit copies this file to
``<scratch>/boot/sitecustomize.py`` so every child Controller started
through the kit loads it, and the scenario worker loads it by path.
Configuration is the JSON in ``SCENARIO_BOOT_CONFIG``:

``allowed``  executables a process may start (absolute paths);
``bin``      the scratch ``bin/`` directory (anything resolved through it is allowed);
``log``      where violations and traced writes are appended (JSON lines);
``kill``     ``{"primitive", "pattern", "where", "occurrence", "when", "marker", "ready"}`` or null
            (``where`` filters on the written record's ``status``/``has_worker``);
``trace``    true to log every runtime write primitive call.

The audit hook is a **detector**: it sees only the interpreter that loaded
it (D7). The kill-point wrapper patches ``controller.runtime`` when that
module is first imported, so ``controller/`` gains no hook.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

_CONFIG: dict = {}
_COUNTS: dict = {}


def _log(entry: dict) -> None:
    path = _CONFIG.get("log")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass


def _resolve(executable, env) -> str:
    text = os.fsdecode(executable)
    if "/" in text:
        return os.path.abspath(text)
    path = (dict(env).get("PATH") if env else None) or os.environ.get("PATH", "")
    for directory in path.split(os.pathsep):
        candidate = os.path.join(directory or ".", text)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return text


def _allowed(path: str) -> bool:
    bin_dir = os.path.abspath(_CONFIG.get("bin", "")) + os.sep
    if path.startswith(bin_dir):
        return True
    return path in _CONFIG.get("allowed", ()) or os.path.realpath(path) in _CONFIG.get("allowed", ())


def _audit(event: str, args: tuple) -> None:
    if event == "subprocess.Popen":
        executable, env = args[0], args[3] if len(args) > 3 else None
    elif event in ("os.exec", "os.posix_spawn"):
        executable, env = args[0], args[2] if len(args) > 2 else None
    elif event == "os.system":
        _log({"violation": "os.system", "command": os.fsdecode(args[0])})
        raise RuntimeError("scenario: os.system is not allowed")
    else:
        return
    resolved = _resolve(executable, env)
    if not _allowed(resolved):
        _log({"violation": event, "executable": resolved})
        raise RuntimeError(f"scenario: {resolved} is outside the scratch bin/")


def _block(kill: dict) -> None:
    """Hold the Controller at its kill point, announcing it with ``kill["marker"]``. A post-spawn point is honoured
    only once the worker's readiness file ``kill["ready"]`` exists (both conditions, 10 s deadline, D5); a worker
    that never becomes ready is announced with ``kill["unready"]`` instead, so the scenario fails naming it rather
    than killing the Controller in a state the oracle does not describe."""
    marker, ready = kill["marker"], kill.get("ready")
    deadline = time.monotonic() + kill.get("ready_seconds", 10)
    while ready and not os.path.exists(ready) and time.monotonic() < deadline:
        time.sleep(0.02)
    if ready and not os.path.exists(ready):
        marker = kill.get("unready") or marker + ".not-ready"
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("reached\n")
        fh.flush()
        os.fsync(fh.fileno())
    end = time.monotonic() + 600  # the leftover bound; the scenario kills us long before
    while time.monotonic() < end:
        time.sleep(0.05)
    os._exit(97)


def _wrap(module, name: str) -> None:
    real = getattr(module, name)
    kill = _CONFIG.get("kill")
    trace = _CONFIG.get("trace")
    pattern = re.compile(kill["pattern"]) if kill else None

    def wrapper(runtime_root, rel_path, *args, **kwargs):
        rel = os.fspath(rel_path)
        obj = args[0] if args and isinstance(args[0], dict) else {}
        facts = {"status": obj.get("status"), "has_worker": bool(obj.get("worker_process"))}
        if trace:
            _log({"write": name, "rel": rel, **facts})
        hit = False
        where = (kill or {}).get("where") or {}
        if kill and name == kill["primitive"] and pattern.search(rel) \
                and all(facts.get(k) == v for k, v in where.items()):
            _COUNTS[name] = _COUNTS.get(name, 0) + 1
            hit = _COUNTS[name] == kill["occurrence"]
        if hit and kill["when"] == "before":
            _block(kill)
        result = real(runtime_root, rel_path, *args, **kwargs)
        if hit and kill["when"] == "after":
            _block(kill)
        return result
    wrapper.__wrapped__ = real
    setattr(module, name, wrapper)


class _RuntimeFinder:
    """Patch ``controller.runtime`` right after it is first imported."""

    def find_spec(self, name, path=None, target=None):
        if name != "controller.runtime":
            return None
        sys.meta_path.remove(self)
        import importlib.util
        spec = importlib.util.find_spec(name)
        loader = spec.loader
        real_exec = loader.exec_module

        def exec_module(module):
            real_exec(module)
            for primitive in ("write_json", "create_json", "rename_exclusive", "write_bytes"):
                if hasattr(module, primitive):
                    _wrap(module, primitive)
        loader.exec_module = exec_module
        return spec


def install(config: dict | None = None) -> None:
    """Install the audit hook and the write patch from ``config`` (or the
    environment's ``SCENARIO_BOOT_CONFIG``)."""
    global _CONFIG
    if config is None:
        text = os.environ.get("SCENARIO_BOOT_CONFIG")
        if not text:
            return
        config = json.loads(text)
    _CONFIG = config
    if config.get("allowed") is not None:
        sys.addaudithook(_audit)
    if config.get("kill") or config.get("trace"):
        sys.meta_path.insert(0, _RuntimeFinder())


if __name__ != "__main__" and os.environ.get("SCENARIO_BOOT_CONFIG") and __name__ == "sitecustomize":
    install()
