#!/usr/bin/env python3
"""Run the lifecycle scenario catalogue and write functional-evidence records.

``python3 tools/run_scenarios.py list [--module M] [--all]``
``python3 tools/run_scenarios.py run [--output DIR] [--module M] [--degraded-diagnostic] [--check-budget] [names...]``
``python3 tools/run_scenarios.py check [--output DIR]``

``run`` writes ``DIR/<flow_id>.json`` (exactly the keys of
``controller/protocol_schema.json``'s ``functional_evidence``) and one
``DIR/index.json``, bound to the ``HEAD`` it ran against. It refuses (exit 2,
nothing written) a tree whose code differs from ``HEAD`` and a machine whose
sandbox is unavailable. A failed scenario still writes a ``failed`` record and
the exit status is 1. Standard library only; the Controller gains nothing.

Exit status: 0 every scenario passed, 1 a scenario failed or ``check`` found a
problem, 2 the run was refused (nothing written).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.scenarios import kit  # noqa: E402
from tests.scenarios import FLOW_ID_RE  # noqa: E402
from controller import version as controller_version  # noqa: E402

DEFAULT_MODULE = "tests.scenarios.catalogue"
DEFAULT_OUTPUT = REPO_ROOT / ".ai-review" / "scenario-evidence"
BUDGET_SECONDS = 120.0
INDEX_NAME = "index.json"
RECORD_KEYS = ("flow_id", "status", "head", "ran_at", "summary", "log_digest", "run_ref", "reporter")
CODE_PATHS = kit.CODE_PATHS


class Refused(Exception):
    """The run is refused: nothing is written (exit 2)."""


def _reporter() -> str:
    try:
        found = controller_version.source_version(REPO_ROOT)
    except ValueError:
        found = "unknown"
    return f"workflow-controller-scenarios/{found}"


def _find_functional(node):
    if isinstance(node, dict):
        if "functional_evidence" in node.get("properties", {}):
            return node["properties"]["functional_evidence"]
        for value in node.values():
            found = _find_functional(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_functional(value)
            if found:
                return found
    return None


def validate_record(record: object, flow_id: "str | None" = None) -> list[str]:
    """Problems with ``record`` against the protocol's ``functional_evidence``
    sub-schema and the Workflow's ``FLOW_ID_RE`` (the schema alone only checks
    that ``flow_id`` is a string)."""
    schema = _find_functional(json.loads((REPO_ROOT / "controller" / "protocol_schema.json").read_text()))
    problems: list[str] = []
    if not isinstance(record, dict):
        return ["the record is not an object"]
    if set(record) != set(schema["required"]):
        problems.append(f"keys {sorted(record)} differ from {sorted(schema['required'])}")
        return problems
    types = {"string": str, "object": dict}
    for key, spec in schema["properties"].items():
        allowed = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        value = record[key]
        if value is None:
            ok = "null" in allowed
        else:
            ok = any(isinstance(value, types[t]) for t in allowed if t in types)
        if not ok:
            problems.append(f"{key} has the wrong type")
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"{key} {value!r} is not one of {spec['enum']}")
    if isinstance(record["flow_id"], str) and not FLOW_ID_RE.match(record["flow_id"]):
        problems.append(f"flow_id {record['flow_id']!r} does not match FLOW_ID_RE")
    if flow_id is not None and record["flow_id"] != flow_id:
        problems.append(f"flow_id {record['flow_id']!r} differs from the file name {flow_id!r}")
    if isinstance(record["log_digest"], str) and not re.fullmatch(r"[0-9a-f]{64}", record["log_digest"]):
        problems.append("log_digest is not a sha256")
    if isinstance(record["head"], str) and not re.fullmatch(r"[0-9a-f]{40}", record["head"]):
        problems.append("head is not a 40-character commit")
    if isinstance(record["ran_at"], str) and not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["ran_at"]):
        problems.append("ran_at is not a UTC timestamp")
    return problems


def _index_problems(data: object) -> list[str]:
    """Problems with ``data`` as the ``index.json`` :func:`cmd_run` writes (the keys, the reporter and the digests)."""
    if not isinstance(data, dict) or set(data) != {"head", "isolation", "reporter", "order", "exit_status", "records"}:
        return ["not the index this runner writes"]
    problems = []
    if not (isinstance(data["reporter"], str) and data["reporter"].startswith("workflow-controller-scenarios/")):
        problems.append("reporter")
    records = data["records"]
    if not isinstance(records, dict) or not all(
            isinstance(k, str) and FLOW_ID_RE.match(k) and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
            for k, v in records.items()):
        problems.append("records")
    return problems


def _load_json(path: Path) -> object:
    """The parsed JSON of a regular file that is not a symlink, else ``None``."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _generated_content_ok(path: Path, relative: tuple) -> bool:
    """The file's *content* is what the runner writes there: an index, a record that validates against the protocol's
    schema under its own name, or a degraded report. A name alone proves nothing (a Python scenario can be called
    ``review-alias.json``)."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return False
    if relative[0] == "degraded":
        keys = ({"evidence", "isolation", "failed_step", "detail", "reports"} if relative[-1] == INDEX_NAME
                else {"scenario", "passed", "observations", "message", "problems"})
        return set(data) == keys
    if relative[0] == INDEX_NAME:
        return not _index_problems(data)
    return not validate_record(data, relative[0][:-5])


def _is_generated_evidence(checkout: Path, path: str, output: Path) -> bool:
    """An untracked file the runner itself writes under ``output``: ``index.json``, ``<flow_id>.json`` and the
    ``degraded/`` reports. The changed path keeps its identity: it is never resolved (a symlink to somewhere else is not
    the file it points at), no component of it may be a symlink, its directory must be ``output`` itself, and its content
    must be the runner's own output. Nothing else beneath the output directory is exempt: a scenario module, a tracked
    file or a stray script stays code however the output directory is placed."""
    pure = Path(path)
    if pure.is_absolute() or ".." in pure.parts:
        return False
    walked = checkout
    for part in pure.parts:
        walked = walked / part
        if walked.is_symlink():
            return False
    full = checkout / pure
    try:
        relative = full.parent.resolve().relative_to(output).parts + (full.name,)
    except ValueError:
        return False
    if len(relative) == 1:
        named = relative[0] == INDEX_NAME or (relative[0].endswith(".json") and FLOW_ID_RE.match(relative[0][:-5]) is not None)
    else:
        named = (relative[0] == "degraded" and len(relative) == 2
                 and (relative[1] == INDEX_NAME or relative[1].endswith(".report.json")))
    return named and _generated_content_ok(full, relative)


def dirty_code_paths(checkout: Path, ignore: "Path | None" = None) -> list[str]:
    """Tracked files modified or staged, and untracked files, under the code
    paths (``controller/``, ``tests/``, ``tools/``, packaging). Workflow
    bookkeeping is not code: every input a scenario executes comes from the
    verified commit or from fixtures under ``tests/``. ``ignore`` is the output
    directory: only the untracked evidence files the runner writes there are
    exempt (:func:`_is_generated_evidence`)."""
    env = kit.sanitised_git_env()
    out = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, "-C", str(checkout), "status",
         "--porcelain=v1", "-z", "--untracked-files=all", "--", *CODE_PATHS],
        capture_output=True, env=env, check=True).stdout.decode(errors="replace")
    paths: list[str] = []
    entries = iter(out.split("\0"))
    for entry in entries:
        if not entry:
            continue
        path = entry[3:]
        if entry[0] in "RC":
            next(entries, None)  # the rename source follows as its own entry
        if entry[:2] == "??" and ignore is not None and _is_generated_evidence(checkout, path, ignore):
            continue
        paths.append(path)
    return paths


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _previous_outputs(output: Path) -> "tuple[Path | None, list[Path]]":
    """What an earlier run of this runner wrote under ``output``, as that run's own ``index.json`` attests: the index
    (when it is valid, a regular file and untracked) and each record the index lists whose content still digests to
    the listed value. Nothing else is owned: a file merely shaped like a record or an index, a record from another run
    or output directory, a file edited since, a symlink and a tracked file are all foreign."""
    index = output / INDEX_NAME
    data = _load_json(index)
    if _index_problems(data):
        return None, []
    tracked = _tracked_paths()
    if index.resolve() in tracked or index.stat().st_nlink > 1:
        return None, []
    owned = []
    for flow_id, digest in sorted(data["records"].items()):
        path = output / f"{flow_id}.json"
        if path.is_symlink() or not path.is_file() or path.resolve() in tracked:
            continue
        try:
            if _digest(path.read_text()) == digest:
                owned.append(path)
        except OSError:
            continue
    return index, owned


def _publish(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as a new file renamed into place. The destination's old inode is never opened for
    writing, so a hard link to it (a tracked file, a file elsewhere) keeps its content."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _tracked_paths() -> "set[Path]":
    listed = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(REPO_ROOT), "ls-files", "-z"],
                            capture_output=True, env=kit.sanitised_git_env())
    return {(REPO_ROOT / name).resolve() for name in listed.stdout.decode(errors="replace").split("\0") if name}


def _unowned_degraded(output: Path, flow_ids: list) -> list[str]:
    """What a diagnostic run would overwrite that it did not write: a ``degraded/`` that is not a plain directory,
    and a destination that is a symlink, a hard-linked or tracked file, not a regular file, or not shaped like the
    runner's own degraded report or index. Absent destinations are free."""
    target = output / "degraded"
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        return ["degraded/"]
    tracked = _tracked_paths()
    found = []
    for name in [*(f"{f}.report.json" for f in flow_ids), INDEX_NAME]:
        path = target / name
        if not (path.exists() or path.is_symlink()):
            continue
        if (path.is_symlink() or not path.is_file() or path.stat().st_nlink > 1 or path.resolve() in tracked
                or not _generated_content_ok(path, ("degraded", name))):
            found.append(f"degraded/{name}")
    return found


def _canonical(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def build_record(outcome: "kit.Outcome", reporter: str, ran_at: str) -> dict:
    """The ``functional_evidence`` record of one namespace-mode outcome. A
    record exists only for a namespace-mode run: the mode is asserted here."""
    assert outcome.isolation == "namespace", "evidence is written only for a namespace-mode run"
    summary: dict = {"isolation": outcome.isolation, "observations": outcome.result.get("observations", [])}
    if outcome.result.get("message"):
        summary["message"] = outcome.result["message"]
    if outcome.problems:
        summary["problems"] = outcome.problems
    return {"flow_id": outcome.flow_id, "status": "passed" if outcome.passed else "failed", "head": outcome.head,
            "ran_at": ran_at, "summary": summary, "log_digest": _digest(outcome.log), "run_ref": None,
            "reporter": reporter}


def selected(module: str, names: list[str], include_selftest: bool, mode: str = "namespace") -> list[str]:
    """The flow ids to run. The scenario modules are imported by the sandboxed discovery worker, never here."""
    if module.split(".")[0] != "tests":
        raise Refused(f"module {module!r} is outside the tests package: scenario code must come from the dirty-checked "
                      "code paths, or a record for it binds to no commit")
    try:
        ids = [s.flow_id for s in kit.list_scenarios(
            module if module != DEFAULT_MODULE else "", mode=mode, include_selftest=include_selftest)]
    except (kit.IsolationUnavailable, kit.MetadataRefused) as exc:
        raise Refused(str(exc))
    if names:
        unknown = [n for n in names if n not in ids]
        if unknown:
            raise Refused(f"no such scenario: {unknown}; registered: {ids}")
        return [n for n in ids if n in names]
    return ids


def cmd_list(args: argparse.Namespace) -> int:
    mode = "degraded" if args.degraded_diagnostic else "namespace"
    for flow_id in selected(args.module, [], args.all or args.module != DEFAULT_MODULE, mode):
        print(flow_id)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    mode = "degraded" if args.degraded_diagnostic else "namespace"
    if args.module.split(".")[0] != "tests":
        selected(args.module, [], False)  # refuses a module outside the tests package, importing nothing
    dirty = dirty_code_paths(REPO_ROOT, ignore=output)
    if dirty:
        raise Refused("code differs from HEAD (a record for uncommitted code binds to no commit): " + ", ".join(dirty))
    probe = kit.probe_isolation()
    if mode == "namespace" and not probe.ok:
        raise Refused(f"isolation unavailable at step {probe.step!r}: {probe.detail}")
    ids = selected(args.module, args.names, args.module != DEFAULT_MODULE, mode)
    if mode == "degraded":
        foreign = _unowned_degraded(output, ids)
        if foreign:
            raise Refused("the output directory holds files this runner did not write, which a diagnostic run would "
                          "overwrite: " + ", ".join(foreign))
    reporter, outcomes = _reporter(), []
    total = 0.0
    for flow_id in ids:
        try:
            outcome = kit.run_scenario(flow_id, mode=mode, module=args.module if args.module != DEFAULT_MODULE else "")
        except (kit.IsolationUnavailable, kit.MetadataRefused) as exc:
            raise Refused(str(exc))
        outcomes.append(outcome)
        total += outcome.seconds
        print(f"{'ok  ' if outcome.passed else 'FAIL'} {flow_id} {outcome.seconds:.1f}s", flush=True)
        if not outcome.passed:
            print(f"     {outcome.result.get('message', '')} {outcome.problems}", flush=True)
    print(f"total {total:.1f}s against a budget of {BUDGET_SECONDS:.0f}s")
    failed = [o for o in outcomes if not o.passed]
    over = args.check_budget and total > BUDGET_SECONDS
    if over:
        print(f"over budget: {total:.1f}s > {BUDGET_SECONDS:.0f}s")
    if mode == "degraded":
        _write_degraded(output, outcomes, probe)
        return 1 if failed else 0
    ran_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = {o.flow_id: build_record(o, reporter, ran_at) for o in outcomes}
    for flow_id, record in records.items():
        problems = validate_record(record, flow_id)
        if problems:
            raise Refused(f"{flow_id}: the record is malformed: {problems}")
    status = 1 if (failed or over) else 0
    output.mkdir(parents=True, exist_ok=True)
    previous_index, previous = _previous_outputs(output)
    ours = set(previous) | ({previous_index} if previous_index else set())
    foreign = [t for t in [output / INDEX_NAME, *(output / f"{f}.json" for f in records)]
               if (t.exists() or t.is_symlink()) and t not in ours]
    if foreign:
        raise Refused("the output directory holds files this runner did not write, which a run would overwrite: "
                      + ", ".join(t.name for t in foreign))
    for stale in previous:  # a smaller run must not leave the earlier run's records beside its index
        stale.unlink()
    texts = {flow_id: _canonical(record) for flow_id, record in records.items()}
    for flow_id, text in texts.items():
        _publish(output / f"{flow_id}.json", text)
    index = {"head": outcomes[0].head if outcomes else kit.head_of(REPO_ROOT), "isolation": "namespace",
             "reporter": reporter, "order": ids, "exit_status": status,
             "records": {flow_id: _digest(text) for flow_id, text in texts.items()}}
    _publish(output / INDEX_NAME, _canonical(index))
    return status


def _write_degraded(output: Path, outcomes: list, probe: "kit.Probe") -> None:
    """Reports that are not evidence: no ``flow_id``/``head``/``status`` keys."""
    target = output / "degraded"
    output.mkdir(parents=True, exist_ok=True)
    foreign = _unowned_degraded(output, [o.flow_id for o in outcomes])
    if foreign:
        raise Refused("the output directory holds files this runner did not write, which a diagnostic run would "
                      "overwrite: " + ", ".join(foreign))
    target.mkdir(exist_ok=True)
    for outcome in outcomes:
        report = {"scenario": outcome.flow_id, "passed": outcome.passed, "observations":
                  outcome.result.get("observations", []), "message": outcome.result.get("message", ""),
                  "problems": outcome.problems}
        _publish(target / f"{outcome.flow_id}.report.json", _canonical(report))
    _publish(target / INDEX_NAME, _canonical({
        "evidence": False, "isolation": "degraded", "failed_step": probe.step, "detail": probe.detail,
        "reports": [o.flow_id for o in outcomes]}))


def cmd_check(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    problems: list[str] = []
    try:
        index = json.loads((output / INDEX_NAME).read_text())
    except (OSError, ValueError) as exc:
        print(f"cannot read {output / INDEX_NAME}: {exc}")
        return 1
    head = kit.head_of(REPO_ROOT)
    if index.get("head") != head:
        problems.append(f"index head {index.get('head')} is not HEAD {head}")
    if index.get("isolation") != "namespace":
        problems.append(f"isolation is {index.get('isolation')!r}, not 'namespace'")
    files = sorted(p.stem for p in output.glob("*.json") if p.name != INDEX_NAME)
    if sorted(index.get("records", {})) != files:
        problems.append(f"index lists {sorted(index.get('records', {}))} but the directory holds {files}")
    for flow_id in files:
        text = (output / f"{flow_id}.json").read_text()
        if index.get("records", {}).get(flow_id) != _digest(text):
            problems.append(f"{flow_id}: the file does not match its index digest")
        try:
            record = json.loads(text)
        except ValueError:
            problems.append(f"{flow_id}: unreadable JSON")
            continue
        problems += [f"{flow_id}: {p}" for p in validate_record(record, flow_id)]
        if isinstance(record, dict):
            if record.get("head") != index.get("head"):
                problems.append(f"{flow_id}: head differs from the index")
            if record.get("status") != "passed":
                problems.append(f"{flow_id}: status is {record.get('status')!r}")
            if (record.get("summary") or {}).get("isolation") != "namespace":
                problems.append(f"{flow_id}: not a namespace-mode record")
    for problem in problems:
        print(problem)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_scenarios.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    lst = sub.add_parser("list", help="print the registered scenario flow ids")
    lst.add_argument("--module", default=DEFAULT_MODULE)
    lst.add_argument("--all", action="store_true", help="include the kit's selftest scenarios")
    lst.add_argument("--degraded-diagnostic", action="store_true",
                     help="discover without the sandbox for local debugging")
    run = sub.add_parser("run", help="run scenarios and write evidence records")
    run.add_argument("names", nargs="*")
    run.add_argument("--output", default=str(DEFAULT_OUTPUT))
    run.add_argument("--module", default=DEFAULT_MODULE, help="module registering the scenarios (tests use the selftest)")
    run.add_argument("--degraded-diagnostic", action="store_true",
                     help="run without the sandbox for local debugging; writes reports that are not evidence")
    run.add_argument("--check-budget", action="store_true", help=f"exit 1 when the total exceeds {BUDGET_SECONDS:.0f}s")
    check = sub.add_parser("check", help="verify a written output directory against HEAD")
    check.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {"list": cmd_list, "run": cmd_run, "check": cmd_check}[args.command](args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
