"""``usage --refresh`` (UF1): one cheap probe per provider that re-reads its limit.

A caller that must decide whether a hold still applies runs one tiny
``codex exec`` and one tiny ``claude -p`` call, each in a throwaway private
home, and records the ``rate_limits`` / ``rate_limit_event`` the provider
reported through :func:`controller.usage.record_reading`. A probe that fails
changes nothing and is named with its cause (D4).

The probes are built on :class:`controller.review.ReviewHarness` (``review.CODEX``,
``review.CLAUDE``) and the review launches' own public pieces (private homes,
the allowlisted environment, the boundary checks, the credential copy-back and
the bounded runner), which are only **read** here: ``controller/review.py`` is
not edited, and no looser command line is built. Each probe's argv differs from
a review launch's (no tools, one turn, a cheap model, no sandbox table), so it
is pinned in this module and checked by :func:`check_probe_argv`.

The checks of the named models run at **import** (D2): a table that drops
``haiku`` or ``gpt-6.1-sol`` fails here, and ``tests.test_usage_probe`` fails
with it.
"""

from __future__ import annotations

import dataclasses
import json
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from controller import harness, review, runtime, usage
from controller.errors import ReviewLaunchRefusedError

#: The probes' outcomes (D4).
OUTCOME_OK = "ok"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_FAILED = "failed"
OUTCOME_TIMED_OUT = "timed_out"
OUTCOME_NO_READING = "no_reading"

#: The one-word prompt, the hard deadline of one probe (and of its compose command), the runtime area of a
#: probe's layout and how old a stale layout must be before the next refresh removes it (more than twice
#: the deadline, so a running probe's layout is never taken).
PROMPT = "Reply with the single word: ok"
TIMEOUT_SECONDS = 120.0
AREA = "usage-probe"
STALE_SECONDS = 300.0

#: The cheap model each probe names itself: ``controller/harness.py`` declares its models with no cost order,
#: so "cheapest" cannot be derived from the table. The effort is the adapter's lowest declared, ``efforts[0]``
#: (``low`` for Claude Code; ``high`` for Codex, whose table declares only ``high`` and ``xhigh``).
CLAUDE_MODEL = "haiku"
CODEX_MODEL = "gpt-6.1-sol"
CLAUDE_EFFORT = harness.CLAUDE_CODE.efforts[0]
CODEX_EFFORT = harness.CODEX.efforts[0]

if CLAUDE_MODEL not in harness.CLAUDE_CODE.models or CODEX_MODEL not in harness.CODEX.models:  # pragma: no cover
    raise RuntimeError(f"the usage probes name {CLAUDE_MODEL!r} and {CODEX_MODEL!r}, which controller/harness.py "
                       "no longer declares")


def _refuse(message: str, **evidence) -> ReviewLaunchRefusedError:
    return ReviewLaunchRefusedError(message, evidence=evidence)


# ---------------------------------------------------------------------------
# The pinned command lines (D2)
# ---------------------------------------------------------------------------


def claude_argv(binary: Path, *, effort: str = CLAUDE_EFFORT) -> list[str]:
    """The Claude probe: no tools, one turn, the cheap model, the prompt on stdin."""
    return [str(binary), "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
            "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--tools", "", "--max-turns", "1", "--disable-slash-commands", "--model", CLAUDE_MODEL,
            "--effort", effort]


def _codex_common(effort: str) -> list[str]:
    out = ["--strict-config"]
    for feature in review.DISABLED_FEATURES:
        out += ["--disable", feature]
    # ``skills.bundled.enabled=false`` is the review's own override: without it Codex writes ``skills/.system``
    # (startup-loaded, so ``review.check_home`` refuses it) and loads the bundled skills into the probe's context.
    for item in ('model_provider="openai"', "notify=[]", 'web_search="disabled"', f'model="{CODEX_MODEL}"',
                 f'model_reasoning_effort="{effort}"', 'approval_policy="never"', "skills.bundled.enabled=false"):
        out += ["-c", item]
    return out


def codex_exec_argv(binary: Path, tree: Path, *, effort: str = CODEX_EFFORT, prompt: str = PROMPT) -> list[str]:
    """The Codex probe: read-only, the throwaway ``tree`` as its working root, no ``-o`` and never
    ``--ephemeral`` (which would stop the session file the reading comes from)."""
    return [str(binary), "exec", *_codex_common(effort), "-s", "read-only", "--skip-git-repo-check",
            "--ignore-rules", "--color", "never", "-C", str(tree), prompt]


def codex_compose_argv(binary: Path, *, effort: str = CODEX_EFFORT) -> list[str]:
    """The composing command: the exec argv's ``--strict-config``, ``--disable`` and ``-c`` set, and so the
    configuration the probe runs under. ``-s``, ``--skip-git-repo-check``, ``--ignore-rules``, ``--color``,
    ``-C`` (``codex app-server`` has no such option) and the prompt are left out; ``--listen stdio://`` is
    passed, as ``review.argv("app-server")`` passes it."""
    return [str(binary), "app-server", *_codex_common(effort), "--listen", "stdio://"]


def check_probe_argv(kind: str, command: Sequence[str], *, tree: Path | None = None, effort: str | None = None) -> None:
    """Refuse a launch whose command line is not exactly the pinned one for ``kind`` (``claude``, ``exec`` or
    ``app-server``), naming the first element that differs."""
    binary = Path(command[0]) if command else Path(kind)
    if kind == "claude":
        expected = claude_argv(binary, effort=effort or CLAUDE_EFFORT)
    elif kind == "exec":
        expected = codex_exec_argv(binary, tree or Path("."), effort=effort or CODEX_EFFORT)
    elif kind == "app-server":
        expected = codex_compose_argv(binary, effort=effort or CODEX_EFFORT)
    else:  # pragma: no cover -- a programming error
        raise ValueError(kind)
    for index, want in enumerate(expected):
        have = command[index] if index < len(command) else None
        if have != want:
            raise _refuse(f"the {kind} probe command line differs from the pinned one at element {index}: "
                          f"{have!r}, expected {want!r}", check="argv", entry=want)
    if len(command) != len(expected):
        raise _refuse(f"the {kind} probe command line carries the extra element {command[len(expected)]!r}",
                      check="argv", entry=command[len(expected)])


# ---------------------------------------------------------------------------
# The composed configuration of the Codex probe (D2, revision 6)
# ---------------------------------------------------------------------------


def _answered(stdout: bytes, request_id: int = 2) -> bool:
    """True once ``stdout`` holds a complete JSON-RPC message answering ``request_id``: the app-server exits
    at end of input before it answers, so stdin stays open until then (as in ``review.compose_config``)."""
    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return True
    return False


def compose_config(command: Sequence[str], layout: review.JobLayout, env: Mapping[str, str], *, timeout: float,
                   run: Callable[..., review.RunResult] = review.run_bounded) -> dict:
    """Run the composing command and return its ``config/read`` result with layers. A command that does not
    run, times out or prints no answer is a refusal."""
    requests = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "workflow-controller", "version": "1"}}},
        {"method": "initialized"},
        {"id": 2, "method": "config/read", "params": {"includeLayers": True, "cwd": str(layout.tree)}},
    ]
    result = run(command, cwd=layout.tree, env=env, timeout=timeout,
                 stdin_text="".join(json.dumps(item) + "\n" for item in requests), hold_open_until=_answered)
    stderr = result.stderr.decode("utf-8", "replace")
    if result.timed_out or result.exit_code != 0:
        raise _refuse("the composing command did not run", check="effective", value=stderr.strip()[-400:])
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == 2:
            if isinstance(message.get("result"), dict):
                return message["result"]
            raise _refuse("the composing command answered config/read with an error", check="effective",
                          value=json.dumps(message.get("error"))[:400])
    raise _refuse("the composing command printed no config/read answer", check="effective", value=stderr.strip()[-400:])


def _origin(origins: Mapping, key: str) -> object:
    if key in origins:
        return origins[key]
    for name in sorted(origins):
        if name.startswith(f"{key}."):
            return origins[name]
    return None


def check_probe_composed(composed: object, *, codex_home: Path) -> None:
    """Refuse a composed configuration the probe must not run under: a layer of an unknown type, a ``managed``
    or ``absent`` layer carrying any leaf (``MANAGED_APPROVED_LEAVES`` is empty), an enabled ``project`` layer,
    a ``user`` layer that is not the private home's ``config.toml``, a non-empty ``mcp_servers``, ``hooks`` or
    ``notify``, and any leaf whose origin is not in ``ORIGIN_ALLOWED``. The sandbox, tier and trust values a
    review pins are not compared: :func:`check_probe_argv` pins the probe's own."""
    config = composed.get("config") if isinstance(composed, Mapping) else None
    origins = composed.get("origins") if isinstance(composed, Mapping) else None
    layers = composed.get("layers") if isinstance(composed, Mapping) else None
    if not isinstance(config, Mapping) or not isinstance(origins, Mapping) or not isinstance(layers, list):
        raise _refuse("the composed configuration is not a config/read result with layers", check="effective")
    for layer in layers:
        name = layer.get("name") if isinstance(layer, Mapping) else None
        kind = name.get("type") if isinstance(name, Mapping) else None
        if kind not in review.CONFIG_SOURCES:
            raise _refuse(f"the composed configuration has a layer of the unknown type {kind!r}", check="effective",
                          entry=str(kind), source=name)
        disposition = review.CONFIG_SOURCES[kind]
        if disposition in ("managed", "absent"):
            extra = sorted(leaf for leaf, value in review.flatten(layer.get("config") or {}).items()
                           if leaf and review.MANAGED_APPROVED_LEAVES.get(leaf, object()) != value)
            if extra:
                raise _refuse(f"the {kind} layer sets {extra[0]}, which no {disposition} layer may supply",
                              check="effective", entry=kind, key=extra[0], source=name)
        if kind == "project" and not layer.get("disabledReason"):
            raise _refuse("a project layer is enabled", check="effective", entry=kind, source=name)
        if kind == "user" and name.get("file") != str(codex_home / "config.toml"):
            raise _refuse("the user layer is not the private home's config.toml", check="effective", entry=kind,
                          source=name)
    for key in ("mcp_servers", "hooks", "notify"):
        if config.get(key):
            raise _refuse(f"the composed configuration sets {key}", check="effective", key=key, value=config.get(key),
                          source=_origin(origins, key))
    leaves = review.flatten(config)
    for name in sorted(origins):
        origin = origins[name]
        source = origin.get("name", origin) if isinstance(origin, Mapping) else origin
        kind = source.get("type") if isinstance(source, Mapping) else None
        if kind not in review.ORIGIN_ALLOWED:
            raise _refuse(f"the composed configuration sets {name} from a {kind} layer", check="effective", key=name,
                          value=leaves.get(name), source=origin)


# ---------------------------------------------------------------------------
# One probe
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProbeResult:
    """What one probe did: ``outcome`` (D4), the cause when it was not ``ok``, the readings it recorded and how
    its credential copy ended (``unchanged``, ``copied``, ``operator-changed``, ``invalid``, ``missing`` or
    ``error``; ``None`` when no copy was made)."""

    provider: str
    outcome: str
    detail: str | None = None
    readings: list[dict] = dataclasses.field(default_factory=list)
    credentials: str | None = None


def _tail(run: review.RunResult) -> str:
    return (run.stderr.decode("utf-8", "replace") + run.stdout.decode("utf-8", "replace")).strip()[-400:]


def _remove_layout(runtime_root: Path, layout: review.JobLayout) -> None:
    runtime.remove_tree(runtime_root, layout.rel())


def _probe(provider: str, selected: review.ReviewHarness, *, runtime_root: Path, binary_name: str, operator_home: Path,
           system_dir: Path, timeout: float, run: Callable[..., review.RunResult],
           launch: Callable[[review.JobLayout, Path, Mapping[str, str], Path, float, Callable[..., review.RunResult]],
                            ProbeResult]) -> ProbeResult:
    """The steps both probes share: resolve the binary, build the private layout and home, run ``launch``, copy a
    refreshed credential back and remove the layout, each step guarded so one failing never skips the next."""
    job_id = f"{provider}-{uuid.uuid4().hex[:12]}"
    layout = review.JobLayout(runtime_root, job_id, area=AREA)
    try:
        binary = review.resolve_binary(binary_name)
    except ReviewLaunchRefusedError as exc:
        return ProbeResult(provider, OUTCOME_NOT_FOUND, str(exc))
    captured: str | None = None
    result = ProbeResult(provider, OUTCOME_FAILED)
    try:
        captured = selected.build_home(layout, operator_home)
        layout.tree.mkdir(parents=True, exist_ok=True)
        env = selected.environment(layout, binary, job_id)
        result = launch(layout, binary, env, system_dir, timeout, run)
    except ReviewLaunchRefusedError as exc:
        result = ProbeResult(provider, OUTCOME_FAILED, str(exc))
    except OSError as exc:  # a binary that resolves but cannot be started, a layout that cannot be written (D4)
        result = ProbeResult(provider, OUTCOME_FAILED, f"the probe could not run: {exc}")
    finally:
        failures: list[Exception] = []
        if captured is not None:
            try:
                result.credentials = review.copy_back_credentials(layout, operator_home, captured, selected)
            except Exception as exc:  # noqa: BLE001 -- named in the result; the layout is still removed
                result.credentials = "error"
                failures.append(exc)
        try:
            _remove_layout(runtime_root, layout)
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)
        if failures and result.outcome == OUTCOME_OK:
            result.detail = "; ".join(str(f) for f in failures)
    return result


def _finish(provider: str, run: review.RunResult, readings: list[dict], timeout: float) -> ProbeResult:
    if run.timed_out:
        return ProbeResult(provider, OUTCOME_TIMED_OUT, f"the probe passed its {timeout:g}-second deadline")
    if run.exit_code != 0:
        return ProbeResult(provider, OUTCOME_FAILED, f"exit {run.exit_code}: {_tail(run)}")
    if not readings:
        return ProbeResult(provider, OUTCOME_NO_READING, "the probe's own record holds no rate limit")
    return ProbeResult(provider, OUTCOME_OK, readings=readings)


def _launch_claude(layout: review.JobLayout, binary: Path, env: Mapping[str, str], system_dir: Path, timeout: float,
                   run: Callable[..., review.RunResult]) -> ProbeResult:
    review.check_claude_home(layout)
    review.check_claude_outside_layers(system_dir)
    command = claude_argv(binary)
    check_probe_argv("claude", command)
    made = run(command, cwd=layout.tree, env=env, timeout=timeout, stdin_text=PROMPT)
    return _finish(usage.PROVIDER_CLAUDE, made, usage.read_claude_stream(made.stdout), timeout)


def _launch_codex(layout: review.JobLayout, binary: Path, env: Mapping[str, str], system_dir: Path, timeout: float,
                  run: Callable[..., review.RunResult]) -> ProbeResult:
    review.check_outside_layers(system_dir)
    review.check_home(layout)
    compose = codex_compose_argv(binary)
    check_probe_argv("app-server", compose)
    check_probe_composed(compose_config(compose, layout, env, timeout=timeout, run=run), codex_home=layout.codex_home)
    review.check_home(layout)  # the composing command may have written state entries; only those are allowed
    review.check_outside_layers(system_dir)
    command = codex_exec_argv(binary, layout.tree)
    check_probe_argv("exec", command, tree=layout.tree)
    made = run(command, cwd=layout.tree, env=env, timeout=timeout)
    # the probe's private home holds exactly the session file the probe wrote, never an earlier session's
    return _finish(usage.PROVIDER_CODEX, made, usage.read_codex_home(layout.codex_home) if made.exit_code == 0
                   and not made.timed_out else [], timeout)


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def sweep_stale(runtime_root: Path, *, now: float | None = None) -> list[str]:
    """Remove every ``usage-probe/<job id>/`` layout a killed probe left (it may hold a 0600 credential copy)
    once its modification time is more than :data:`STALE_SECONDS` old; a younger one is a running probe's."""
    now = time.time() if now is None else now
    area = Path(runtime_root) / AREA
    removed: list[str] = []
    try:
        entries = sorted(area.iterdir(), key=lambda p: p.name)
    except OSError:
        return removed
    for entry in entries:
        try:
            old = now - entry.lstat().st_mtime > STALE_SECONDS
        except OSError:
            continue
        if old:
            runtime.remove_tree(Path(runtime_root), f"{AREA}/{entry.name}")
            removed.append(entry.name)
    return removed


def refresh(providers: Iterable[str], *, runtime_root: str | Path, codex_home: Path, claude_home: Path,
            codex_binary: str = "codex", claude_binary: str = "claude", codex_system_dir: Path | None = None,
            claude_system_dir: Path | None = None, timeout: float = TIMEOUT_SECONDS,
            run: Callable[..., review.RunResult] = review.run_bounded, now: float | None = None
            ) -> dict[str, ProbeResult]:
    """Probe each of ``providers`` (``usage.PROVIDERS`` names, in the order given), merge the readings of every
    probe that read one through :func:`usage.record_reading`, and return ``{provider: ProbeResult}``. One
    provider failing never skips the other, and a failed probe records nothing (D4). Stale layouts of killed
    probes are removed first. ``run`` is the injectable bounded runner."""
    root = Path(runtime_root)
    sweep_stale(root, now=now)
    results: dict[str, ProbeResult] = {}
    for provider in providers:
        if provider == usage.PROVIDER_CODEX:
            result = _probe(provider, review.CODEX, runtime_root=root, binary_name=codex_binary,
                            operator_home=codex_home, system_dir=codex_system_dir or review.CODEX.system_dir,
                            timeout=timeout, run=run, launch=_launch_codex)
        elif provider == usage.PROVIDER_CLAUDE:
            result = _probe(provider, review.CLAUDE, runtime_root=root, binary_name=claude_binary,
                            operator_home=claude_home, system_dir=claude_system_dir or review.CLAUDE.system_dir,
                            timeout=timeout, run=run, launch=_launch_claude)
        else:
            raise ValueError(f"no usage probe for {provider!r}")
        if result.outcome == OUTCOME_OK:
            usage.record_reading(root, result.readings)
        results[provider] = result
    return results
