"""Generator for ``tests/golden/no_policy_lifecycle.json`` -- the no-policy
golden of ``workflow-controller-trunk-branch-pr-release-orchestration`` CP8
(invariant I1, "No activation, no change").

The golden pins what a lifecycle leaves behind on a target with no
``.workflow-controller/policy.json`` at ``HEAD`` and no binding record: every
job record, every worker argv, every ``inspect``/``explain`` JSON document
and every exit code. It was generated **before CP8's lifecycle wiring**, from
the unchanged ``controller`` package of commit ``86801f6`` (CP7), so the CP8
test that re-derives it from the code as it stands shows the repository
preflight, the worker tool restrictions, the post-step verification and the
observation blocks change nothing at all without a policy -- not even a new
``null`` key.

It was rewritten once since, deliberately, by
``workflow-controller-worker-lifecycle-ownership`` CP3, whose streaming
launch changes every job, policy or not: each record gains
``ownership_tag``, ``worker_anchor`` and ``worker_state`` (``STARTING``),
and each worker argv is the streaming-input form. Nothing else moved: every
status, outcome, exit code and ``inspect``/``explain`` document is as
generated at ``86801f6``.

And once more by that work item's CP4, which persists the supervisor's
``worker_state`` transitions: each launched job's record gains the
``worker_state`` flushes (``RUNNING``, ``ENDING``, ``ENDED``, so a higher
``event_seq``), ``ending_offset`` and ``worker.stream_diagnosis``/
``owned_processes_seen``, and stream byte offsets became volatile keys.
Statuses, outcomes, exit codes and ``inspect``/``explain`` documents are
unchanged.

And once for the 1.2.0 release, whose bump alone broke it: every
``inspect``/``explain`` ``controller.version`` became ``<VERSION>`` (see
**Normalisation**). Nothing else moved.

And once by ``workflow-controller-settings-and-telemetry`` CP2, whose
settings are wired into every launch, policy or not: each launched job's
record gains the optional ``controller_settings`` block (the settings file
each case now names with ``--settings``, inside its own case directory, and
every effective value with its source) and ``worker_route.config_source``
(``settings``: the filled file's empty routing section). Nothing else moved.

**Scenarios** (:data:`SCENARIOS`), each over its own temporary target, run
through the real ``cli.main`` with the pinned test identity, the offline
stub Workflow Manager and ``tests/fake_claude.py``:

- ``implementation_run``: ``tests/test_lifecycle_orchestration``'s full
  ``"2.2"`` run from ``IMPLEMENTING`` to the manual gate (six workers, then a
  ``GATE_BLOCKED`` record), with ``inspect``/``explain`` before and after;
- ``bootstrap_step``: no work item yet, so ``step`` launches the bare
  ``/milestone-plan`` (the unscripted worker fails);
- ``unborn_head_step``: the same on a Workflow-installed repository with no
  commit at all (``HEAD`` unborn).

**Normalisation** (:func:`normalise`): the temporary directory becomes
``<TMP>``; job and run ids become ``<ID>``; ISO timestamps ``<TS>``; 40- and
64-hex-digit tokens ``<SHA>``; the values of process- and host-specific keys
(:data:`VOLATILE_KEYS`) ``<V>``; a ``version`` equal to this checkout's own
Controller version (``pyproject.toml``'s, which the pinned test identity
reports) ``<VERSION>``, so a release bump is not a behaviour change -- any
other ``version`` value still shows verbatim. The file is canonical JSON
(``sort_keys``, indent 2, trailing newline).

Run ``python3 tests/golden/generate_no_policy_lifecycle.py`` to rewrite the
golden, or with ``--check`` to compare without writing. Rewriting it is a
deliberate act: the golden's whole value is that it does not move.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest.mock
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from controller import cli, identity  # noqa: E402
from controller.identity import SOURCE_KIND_COMMIT, ControllerIdentity  # noqa: E402
from tests import fixtures  # noqa: E402
from tests import test_lifecycle_orchestration as lifecycle  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "no_policy_lifecycle.json"

FAKE_CLAUDE = REPO_ROOT / "tests" / "fake_claude.py"

#: Keys whose values are process-, host- or clock-specific.
VOLATILE_KEYS = frozenset({
    "pid", "pgid", "start_ticks", "boot_id", "pid_namespace", "hostname", "machine_id", "remaining_pids",
    "duration_ms", "duration_api_ms", "exec_depth",
    # Worker-lifecycle-ownership CP4: byte offsets into a worker stream that
    # embeds the (normalised) temporary directory, so they vary with its
    # length; every ``*_offset`` key is volatile too (:func:`_volatile`).
    "offset", "ending_point",
})


def _volatile(key: str) -> bool:
    return key in VOLATILE_KEYS or key.endswith("_offset")

_ID_RE = re.compile(r"\b\d{8}T\d{6}Z-[0-9a-f]{8}\b")
_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b")
_SHA_RE = re.compile(r"\b(?:[0-9a-f]{64}|[0-9a-f]{40})\b")


def normalise(value: Any, tmp: str) -> Any:
    if isinstance(value, dict):
        return {key: "<V>" if _volatile(key) and value[key] is not None else
                "<VERSION>" if key == "version" and item == fixtures.CONTROLLER_VERSION else normalise(item, tmp)
                for key, item in value.items()}
    if isinstance(value, list):
        return [normalise(item, tmp) for item in value]
    if isinstance(value, str):
        text = value.replace(tmp, "<TMP>")
        text = _ID_RE.sub("<ID>", text)
        text = _TS_RE.sub("<TS>", text)
        return _SHA_RE.sub("<SHA>", text)
    return value


class _Harness:
    """The pinned identity, the stub Workflow Manager and a ``cli.main``
    driver -- ``tests/test_lifecycle_orchestration``'s, outside a TestCase."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        origin = fixtures.build_checkout(tmp / "controller-origin", generation=1)
        self.ident = ControllerIdentity(
            generation=1, source_root=origin, origin_source_root=origin, source_kind=SOURCE_KIND_COMMIT,
            source_commit=fixtures.current_head(origin), tree_digest="d" * 64, generation_source="head",
            pinned_at=lifecycle.COMPLETED_AT, version=fixtures.CONTROLLER_VERSION,
        )
        self.stub_manager = fixtures.write_stub_workflow_manager(tmp / "workflow-manager")

    def cli(self, case_dir: Path, root: Path, command: str, *, script: dict | None = None,
            json_out: bool = False) -> dict:
        runtime_dir = case_dir / "runtime"
        script_path = case_dir / "worker-script.json"
        fixtures.write_worker_script(script_path, script or {})
        jobs_dir = runtime_dir / "jobs"
        before = {p.name for p in jobs_dir.glob("*.json")} if jobs_dir.is_dir() else set()
        env = {"FAKE_CLAUDE_SCRIPT": str(script_path), "FAKE_CLAUDE_DIAG_LOG": str(case_dir / "argv.jsonl")}
        argv = ["--runtime-dir", str(runtime_dir), "--workflow-manager", str(self.stub_manager),
                "--claude-binary", str(FAKE_CLAUDE), "--timeout", "60",
                "--settings", str(case_dir / "settings.json")]
        if json_out:
            argv.append("--json")
        argv += [command, str(root)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(identity, "pin", return_value=self.ident), \
                unittest.mock.patch.object(identity, "current", return_value=self.ident), \
                unittest.mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        created = sorted((p for p in jobs_dir.glob("*.json") if p.name not in before),
                         key=lambda p: p.stat().st_mtime_ns) if jobs_dir.is_dir() else []
        result: dict[str, Any] = {"exit_code": code, "jobs": [json.loads(p.read_text()) for p in created]}
        if json_out:
            result["json"] = json.loads(stdout.getvalue())
        return result

    @staticmethod
    def worker_argv(case_dir: Path) -> list[list[str]]:
        path = case_dir / "argv.jsonl"
        return [json.loads(line)["argv"] for line in path.read_text().splitlines()] if path.exists() else []


def _observed(harness: _Harness, case_dir: Path, root: Path) -> dict:
    return {"inspect": harness.cli(case_dir, root, "inspect", json_out=True),
            "explain": harness.cli(case_dir, root, "explain", json_out=True)}


def _implementation_run(harness: _Harness, case_dir: Path) -> dict:
    root = case_dir / "target"
    fixtures.build_target_git_repo(root)
    (root / "README.md").write_text("lifecycle fixture\n")
    (root / ".gitignore").write_text(".ai-review/\n")
    fixtures.write_installation_manifest(root)
    base = fixtures.commit_all(root, "initial")
    lc = lifecycle.Lifecycle(case_dir, root.resolve(), base, phase=lifecycle.IMPLEMENTING, checkpoints={})
    fixtures.write_registry(root, lifecycle.WI, lifecycle.CHECKPOINT_IDS)
    (root / lifecycle.STATE_REL).write_text(lc.state_text())
    fixtures.commit_all(root, "Seed the work item")
    lifecycle.script_full_lifecycle(lc)
    before = _observed(harness, case_dir, lc.root)
    run = harness.cli(case_dir, lc.root, "run", script=lc.script)
    return {"before": before, "run": run, "after": _observed(harness, case_dir, lc.root),
            "worker_argv": harness.worker_argv(case_dir)}


def _bootstrap_target(root: Path, *, commit: bool) -> Path:
    fixtures.build_target_git_repo(root)
    fixtures.write_installation_manifest(root)
    fixtures.write_workflow_state(root, {"schema_version": 1, "active_work_item_id": None, "work_items": {}})
    if commit:
        fixtures.commit_all(root, "initial")
    return root.resolve()


def _bootstrap_step(harness: _Harness, case_dir: Path, *, commit: bool = True) -> dict:
    root = _bootstrap_target(case_dir / "target", commit=commit)
    before = _observed(harness, case_dir, root)
    step = harness.cli(case_dir, root, "step")
    return {"before": before, "step": step, "after": _observed(harness, case_dir, root),
            "worker_argv": harness.worker_argv(case_dir)}


SCENARIOS = {
    "implementation_run": _implementation_run,
    "bootstrap_step": _bootstrap_step,
    "unborn_head_step": lambda harness, case_dir: _bootstrap_step(harness, case_dir, commit=False),
}


def derive() -> dict:
    """Every scenario's normalised observations, from the code as it stands."""
    with tempfile.TemporaryDirectory(prefix="controller-no-policy-golden-") as name:
        tmp = Path(name).resolve()
        harness = _Harness(tmp)
        out = {}
        for scenario, body in SCENARIOS.items():
            case_dir = tmp / scenario
            case_dir.mkdir()
            out[scenario] = normalise(body(harness, case_dir), str(tmp))
        return out


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render(derive())
    if "--check" in argv:
        if GOLDEN_PATH.read_text() != text:
            print(f"{GOLDEN_PATH} differs from the re-derived golden", file=sys.stderr)
            return 1
        return 0
    GOLDEN_PATH.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
