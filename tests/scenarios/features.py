"""Scenarios 6-10: releases, notifications, usage, read-only commands and settings
(`workflow-controller-automated-lifecycle-scenarios` CP4).

Like ``lifecycle.py`` this drives the real ``cli.main`` inside the kit's sandbox over the existing end-to-end models,
and honours ``kit.params["mutation"]`` so that every scenario fails when its claim is broken (R7).
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import shutil
import unittest.mock
from pathlib import Path

from tests.scenarios import REPO_ROOT, scenario
from tests.scenarios.lifecycle import e2e_class, mutation, opened


# ---------------------------------------------------------------------------
# Scenario 6
# ---------------------------------------------------------------------------

@functools.cache
def squash_class():
    """The squash lifecycle model (``SquashLifecycleTest``) with the sandbox wiring, over a policy that enables the
    release and a trunk that already carries the published ``v1.0.0``."""
    from tests import fixtures
    from tests import test_trunk_orchestration_e2e as e2e

    class ScenarioSquash(e2e_class(), e2e.SquashLifecycleTest):
        def build_target(self, tmp: Path) -> tuple[Path, Path]:
            origin, clone = super().build_target(tmp)
            policy_path = clone / e2e.POLICY
            data = json.loads(policy_path.read_text())
            data["release"]["enabled"] = True
            policy_path.write_text(json.dumps(data, indent=2) + "\n")
            self.base_tag_commit = fixtures.commit_all(clone, "Enable the release")
            fixtures.run(["git", "push", "-q", "origin", "main"], cwd=clone)
            fixtures.run(["git", "tag", "-a", "-m", "v1.0.0", "v1.0.0", self.base_tag_commit], cwd=clone)
            fixtures.run(["git", "push", "-q", "origin", "refs/tags/v1.0.0"], cwd=clone)
            # A documentation-only change lands on the trunk after the release.
            (clone / "NOTES.md").write_text("a note\n")
            subject = "feat: a note" if self.mutation == "docs-as-feat" else "docs: a note"
            self.docs_commit = fixtures.commit_all(clone, subject)
            fixtures.run(["git", "push", "-q", "origin", "main"], cwd=clone)
            return origin, clone
    return ScenarioSquash


@functools.cache
def release_tool():
    """``tools/release.py`` loaded by path (``tools`` is not a package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("scenario_release_tool", REPO_ROOT / "tools" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify(case, commit: str) -> dict:
    """``tools/release.py classify --commit`` over the case's target: ``key=value`` outputs, or the refusal."""
    release = release_tool()
    out, err = io.StringIO(), io.StringIO()
    env = dict(os.environ, **{k: v for k, v in case.gh_env.items() if k.startswith("FAKE_GH")})
    env.pop("GITHUB_OUTPUT", None)
    with unittest.mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        code = release.main(["classify", "--commit", commit], repo_root=case.root)
    outputs = dict(line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line)
    return {"exit": code, **outputs}


def publish_base_release(case) -> None:
    """The published ``v1.0.0`` release on the fake forge (the tag is already on the origin)."""
    state_path = Path(case.gh_env["FAKE_GH_STATE"])
    from tests import fake_gh
    state = fake_gh.read_state(state_path)
    state["releases"].append({"tagName": "v1.0.0", "isDraft": False, "url": f"{state['url']}/releases/tag/v1.0.0",
                              "assets": [], "title": "v1.0.0", "notes": "seeded"})
    fake_gh.write_state(state_path, state)


@scenario("scenario-squash-merge-and-release-classification", covers=("C1", "C2"))
def squash_merge_and_release_classification(kit) -> None:
    """Scenario 1 under a squash policy, and the ``tools/release.py`` classification of the resulting trunk history:
    no release for a ``docs:`` commit, a minor for the ``feat:`` squash."""
    from controller import cli, gitrepo, milestone_branch as mb
    from tests import test_trunk_orchestration_e2e as e2e
    with opened(kit, squash_class()) as case:
        publish_base_release(case)
        with e2e._run_spy() as argvs:
            case.drive_to_pr()
            kit.equal(case.gh_state()["prs"][0]["title"], case.TITLE, "the pull request carries the plan's declared title")
            case.drive_implementation()
            accepted = case.accept()
            synced = case.step(cli.EXIT_GATE)
            case.assert_branch_gate(synced, "just updated")
            kit.check(f"Accepted at {accepted}" in case.gh_state()["prs"][0]["body"], "readiness did not sync the body")
            case.gh_edit(1, checks=e2e.PASSING_CHECKS)
            ready = case.step(cli.EXIT_GATE)
            case.assert_branch_gate(ready, "Squash and merge")
            kit.equal(case.record()["state"], mb.READY, "the binding before the human merge")
            squash = case.squash_merge(1)
            case.script_next_plan(squash)
            closed = case.step(cli.EXIT_OK)
        record = case.record()
        kit.equal((record["state"], record["merged_head"], record["accepted_head"], record["merge_commit"]),
                  (mb.CLOSED, accepted, accepted, squash), "the binding after the squash merge")
        kit.equal((case.head().branch, case.head().commit), ("main", squash), "the target after close-out")
        kit.check(not gitrepo.is_ancestor(case.root, accepted, squash), "the squash commit contains the branch history")
        subject = case.git("log", "-1", "--format=%s", squash)
        kit.equal(subject, f"{case.TITLE} (#1)", "the squash commit's subject")
        kit.equal(case.git("log", "-1", "--format=%(trailers:only=true)", squash), "", "trailers on the squash commit")
        case.assert_launched(closed, f"{e2e.BOOTSTRAP_PLAN} {squash}", e2e.AWAITING_LOCAL_PLAN, bound=False)
        kit.equal(case.worker_tasks(), case.lifecycle_tasks(squash), "the ordered worker commands")
        events = case.binding_events()
        kit.check("merged_squashed" in events and "merged" not in events, "the merge event is not merged_squashed")
        case.assert_single_branch_and_pr()
        case.assert_no_force_and_no_merge(argvs)

        # The release tool's reading of the trunk history that results.
        verdicts = {"docs": classify(case, case.docs_commit), "feat": classify(case, squash)}
        kit.equal(verdicts["docs"].get("state"), "NO_CHANGE", "a docs: commit since the release releases nothing")
        kit.observe("squash", {"subject": subject, "events": events, "state": record["state"]})
        kit.observe("classification", verdicts)
        kit.equal((verdicts["feat"].get("state"), verdicts["feat"].get("version"), verdicts["feat"].get("tag")),
                  ("RELEASE_DUE", "1.1.0", "v1.1.0"), "the feat squash is a minor release")


# ---------------------------------------------------------------------------
# Scenario 7
# ---------------------------------------------------------------------------

#: Job-record fields that differ between two runs of the same step without being an outcome.
VOLATILE_JOB_FIELDS = ("job_id", "created_at", "updated_at", "reconciled_at", "worker_process", "worker_anchor",
                       "worker_streams", "worker", "telemetry", "usage", "worker_state", "ownership_tag",
                       "controller_runtime", "run_id", "event_seq", "ending_offset")
SECRET = "scenario-secret-key-0123456789abcdef"
#: What the six workers of ``script_full_lifecycle`` and the gate they stop at send, in order.
EXPECTED_EVENTS = ["step_done"] * 6 + ["user_gate", "run_finished"]


def normalised_jobs(kit, lc) -> list:
    """The job records of one lifecycle, in creation order, normalised (D9): volatile fields dropped, the case
    directory, scratch paths and commit ids replaced."""
    paths = sorted((lc.runtime / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    norm = type(kit.norm)(kit.scratch)
    out = []
    for path in paths:
        record = json.loads(path.read_text())
        for field in VOLATILE_JOB_FIELDS:
            record.pop(field, None)
        out.append(norm(json.loads(json.dumps(record).replace(str(lc.root.parent), "<case>"))))
    return out


def first_difference(a, b, path: str = "") -> str:
    """Where two JSON values first differ, as a path, so a failing comparison names the field."""
    if type(a) is not type(b):
        return f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}/{key}: present only in {'the first' if key in a else 'the second'}"
            found = first_difference(a[key], b[key], f"{path}/{key}")
            if found:
                return found
        return ""
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)} items"
        for index, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{index}]")
            if found:
                return found
        return ""
    return "" if a == b else f"{path}: {a!r} != {b!r}"


def write_settings(kit, values: dict) -> None:
    """The scratch settings file the Controller reads by default (``$XDG_CONFIG_HOME``)."""
    from controller import settings
    path = settings.resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, **values}, indent=2, sort_keys=True) + "\n")


@scenario("scenario-notifications-never-steer", covers=("C5",))
def notifications_never_steer(kit) -> None:
    """The same lifecycle segment with the fake SignalHub up, answering 500, slow and down produces identical exit
    codes and normalised job records; the up case receives the documented ordered events with distinct
    ``Idempotency-Key``s, and the secret appears in no output, record or log."""
    from controller import cli, notify
    from tests import test_lifecycle_orchestration as lo
    from tests.fake_signalhub import FakeSignalHub, Step
    from tests.scenarios.lifecycle import seeded_class

    write_settings(kit, {"notifications": {"timeout_seconds": 1, "attempts": 1, "retry_seconds": 1}})
    steering = mutation(kit) == "steering-hub"
    results: dict[str, dict] = {}
    hubs: dict[str, FakeSignalHub] = {}
    with opened(kit, seeded_class()) as case:
        for variant in ("up", "server-error", "slow", "down"):
            hub = FakeSignalHub().start()
            hubs[variant] = hub
            if variant == "server-error":
                hub.queue(*([500] * 60))
            elif variant == "slow":
                hub.queue(*(Step(201, delay=3) for _ in range(60)))
            url = hub.url
            if variant == "down":
                hub.stop()
            lc = case.seed(f"notify-{variant}", lo.IMPLEMENTING)
            lo.script_full_lifecycle(lc)
            if steering and variant == "server-error":
                lc.script[lo.REVIEW_IMPLEMENTATION].pop()  # a hub that "changes" the lifecycle: the last review has no script
            env = {"SIGNALHUB_URL": url, "SIGNALHUB_API_KEY": SECRET}
            with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(notify, "FLUSH_MARGIN_SECONDS", 1.0):
                result = case.cli(lc, "run")
            results[variant] = {"exit": result.code, "workers": case.processes(lc), "jobs": normalised_jobs(kit, lc)}
            logged = (lc.runtime / "notifications.jsonl")
            leaks = [str(p) for p in [logged, *lc.runtime.rglob("*.json")] if p.exists() and SECRET in p.read_text()]
            kit.equal(leaks, [], f"{variant}: files that contain the API key")
            kit.check(SECRET not in result.stdout + result.stderr, f"{variant}: the API key reached the output")
            if variant != "down":
                hub.stop()

    plain = results["up"]
    for variant, got in results.items():
        kit.equal(got["exit"], cli.EXIT_GATE, f"{variant}: exit status")
        kit.equal(got["workers"], 6, f"{variant}: workers launched")
        kit.equal(first_difference(got["jobs"], plain["jobs"]), "", f"{variant}: the normalised job records")
    requests = hubs["up"].requests
    types = [r["body"]["metadata"]["event_type"] for r in requests]
    keys = [r["idempotency_key"] for r in requests]
    kit.equal(types, EXPECTED_EVENTS, "the events the up hub received, in order")
    kit.equal(len(set(keys)), len(keys), "Idempotency-Key values are unique")
    kit.check(all(r["authorization"] == f"Bearer {SECRET}" for r in requests), "a request without the bearer key")
    kit.check(SECRET not in json.dumps([r["body"] for r in requests]), "the API key is in an event body")
    kit.check(len(hubs["server-error"].requests) > 0 and len(hubs["slow"].requests) > 0,
              "the failing hubs were never contacted")
    kit.observe("event-types", types)
    kit.observe("outcomes", {v: {"exit": r["exit"], "workers": r["workers"]} for v, r in results.items()})
    kit.record_digest("normalised-jobs", {"jobs": plain["jobs"]})


# ---------------------------------------------------------------------------
# Scenario 8
# ---------------------------------------------------------------------------

@scenario("scenario-usage-pause-and-resume", covers=("C8",))
def usage_pause_and_resume(kit) -> None:
    """C8's admission pause: a worker that would exceed the budget is not started (``run`` exits 17 with no worker and
    no job record, naming the resume time); a run-cap hold exits 17 with no timed wait; after the injected clock passes
    the reset a second ``run`` launches the worker and completes the segment. A limit reached *mid-job* is not claimed
    here (C8b)."""
    from controller import cli, job, usage
    from tests import test_lifecycle_orchestration as lo
    from tests.scenarios.lifecycle import seeded_class

    clock = [1_800_000_000.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    five_hour_reset_in = 3600
    write_settings(kit, {"usage": {"max_wait_seconds": 60, "resume_grace_seconds": 1}})
    with opened(kit, seeded_class()) as case, \
            unittest.mock.patch.object(job, "usage_now", lambda: clock[0]), \
            unittest.mock.patch.object(cli, "_usage_sleep", sleep):
        lc = case.seed("usage", lo.IMPLEMENTING)
        lo.script_full_lifecycle(lc)
        if mutation(kit) != "no-reading":
            usage.record_reading(lc.runtime, [usage.make_reading("claude", usage.FIVE_HOUR, 80,
                                                                  clock[0] + five_hour_reset_in, clock[0], "scenario")])
        reset_at = int(clock[0] + five_hour_reset_in)

        held = case.cli(lc, "run")
        kit.equal((held.code, case.processes(lc), held.records), (cli.EXIT_USAGE_PAUSED, 0, []),
                  "a run over the budget: exit status, workers, job records")
        kit.check("run again after the resume time" in held.stderr and f"(epoch {reset_at})" in held.stderr,
                  "the message does not name the resume time")
        kit.equal(sleeps, [], "a wait beyond usage.max_wait_seconds must not sleep")
        kit.observe("timed-hold", {"exit": held.code, "workers": case.processes(lc), "records": len(held.records),
                                   "epoch_named": f"(epoch {reset_at})" in held.stderr})

        capped = case.cli(lc, "run", "--usage-cap", "5")
        kit.equal((capped.code, case.processes(lc), capped.records), (cli.EXIT_USAGE_PAUSED, 0, []),
                  "a run over its cap: exit status, workers, job records")
        kit.check("no reset will lift this" in capped.stderr, "the run-cap message does not say no reset lifts it")
        kit.equal(sleeps, [], "a run cap must not sleep")
        kit.observe("run-cap-hold", {"exit": capped.code, "workers": case.processes(lc), "records": len(capped.records)})

        # The injected clock passes the reset: a second `run` (not `resume`, which never launches) goes ahead.
        clock[0] = reset_at + 10
        resumed = case.cli(lc, "run")
        kit.equal(resumed.code, cli.EXIT_GATE, "run after the reset: exit status")
        kit.equal(case.processes(lc), 6, "run after the reset: workers launched")
        kit.equal([r["status"] for r in resumed.records[:6]], ["FINISHED"] * 6, "run after the reset: job statuses")
        kit.observe("after-reset", {"exit": resumed.code, "workers": case.processes(lc)})


# ---------------------------------------------------------------------------
# Scenario 9
# ---------------------------------------------------------------------------

def tree_digests(root: Path, *, skip: tuple[str, ...] = ()) -> dict[str, str]:
    """``{relative path: sha256}`` of every file under ``root`` except the top-level names in ``skip``."""
    import hashlib
    out = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] in skip or not path.is_file() or path.is_symlink():
            continue
        out[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def world(case) -> dict:
    """Everything a read-only command must leave alone, as comparable values."""
    from tests import fixtures
    refs = lambda *git: case.git(*git, "for-each-ref", "--format=%(refname) %(objectname)")  # noqa: E731
    return {
        "head": [case.head().branch, case.head().commit],
        "refs": refs(), "origin-refs": refs("--git-dir", str(case.origin)),
        "work-tree": tree_digests(case.root, skip=(".git",)),
        "forge": tree_digests(Path(case.gh_env["FAKE_GH_STATE"]).parent, skip=("assets",)),
        "runtime": tree_digests(case.lc.runtime),
    }


#: What a read-only command may add under the runtime root: the identity file every command except ``follow`` writes.
ALLOWED_RUNTIME_ADDITIONS = {"identity.json"}


@scenario("scenario-read-only-commands-change-nothing", covers=("C1", "C9"))
def read_only_commands_change_nothing(kit) -> None:
    """``status``, ``explain``, ``inspect`` and ``follow`` at every phase of scenario 1 up to the merge gate (not the
    human merge, close-out or next trunk start) leave the target's working tree and refs, the origin's refs, the fake
    forge and every job and run record unchanged, and add to the runtime root at most ``identity.json``."""
    from controller import cli

    commands = [("status", False), ("explain", False), ("inspect", False), ("follow", False), ("status", True)]
    if mutation(kit) == "step-as-status":
        commands[0] = ("step", False)
    checked: list[dict] = []
    with opened(kit, e2e_class()) as case:
        real_step = case.step

        def watched(expected_code):
            """Run every read-only command before the lifecycle's next step, each against an unchanged world."""
            from tests import fixtures
            fixtures.write_worker_script(case.lc.script_path, case.lc.script)
            phase = len({c["phase"] for c in checked})
            for command, json_out in commands:
                before = world(case)
                argv = case.argv(command, json_out=json_out)
                if command == "status":  # `status` reads the runtime root and takes no repository
                    argv = argv[:-1]
                code, _out, err = case.invoke(argv)
                after = world(case)
                added = set(after["runtime"]) - set(before["runtime"])
                kit.check(added <= ALLOWED_RUNTIME_ADDITIONS, f"phase {phase}: {command} added {sorted(added - ALLOWED_RUNTIME_ADDITIONS)}")
                changed = {key for key in before if key != "runtime" and before[key] != after[key]}
                changed |= {f"runtime/{name}" for name in before["runtime"] if before["runtime"][name] != after["runtime"].get(name)}
                kit.equal(sorted(changed), [], f"phase {phase}: what `{command}` changed")
                kit.check(code in (cli.EXIT_OK, cli.EXIT_GATE), f"phase {phase}: {command} exited {code}: {err}")
                checked.append({"phase": phase, "command": command + (" --json" if json_out else ""), "exit": code,
                                "added": sorted(added)})
            return real_step(expected_code)
        case.step = watched
        case.drive_to_pr()
        case.drive_implementation()
        case.drive_to_merge_gate()
    kit.check(len(checked) >= len(commands) * 7, "too few read-only checks ran")
    kit.observe("read-only-checks", checked)


# ---------------------------------------------------------------------------
# Scenario 10
# ---------------------------------------------------------------------------

def home_snapshot(kit) -> dict[str, str]:
    """``{path relative to HOME: sha256 | "<dir>"}`` for every file and directory under the scratch HOME (which holds
    the XDG tree), so a created directory or lock file shows."""
    import hashlib
    home = kit.home
    out = {}
    for path in sorted(home.rglob("*")):
        out[str(path.relative_to(home))] = "<dir>" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def home_delta(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    """What changed under HOME: ``added``, ``changed`` or ``removed`` per path."""
    out = {name: "added" for name in after if name not in before}
    out.update({name: "changed" for name in after if name in before and after[name] != before[name]})
    out.update({name: "removed" for name in before if name not in after})
    return dict(sorted(out.items()))


@scenario("scenario-settings-file-effect", covers=("C3",))
def settings_file_effect(kit) -> None:
    """The settings file's effect, as two contracts. (a) ``settings show`` and ``settings path`` report the effective
    values and sources (a command-line override wins) and write nothing, with a file or without one. (b) The writing
    commands (``settings clean``, ``step``/``run``) change only the settings file and its sibling lock file (and the
    directories they create), each with an exact footprint; (c) a malformed file is refused with exit 20."""
    from controller import cli, settings
    from tests import test_lifecycle_orchestration as lo
    from tests.scenarios.lifecycle import seeded_class

    path = settings.resolve_path()
    lock = path.with_name(path.name + ".lock")
    relative = lambda p: str(p.relative_to(kit.home))  # noqa: E731
    config_dir = relative(path.parent)

    def settings_cli(*argv: str):
        return kit.cli(list(argv))

    # (a) Observation writes nothing: without a file, with a file, with an override.
    reading = ("show", "path") if mutation(kit) != "show-writes" else ("clean", "path")
    before = home_snapshot(kit)
    for action in reading:
        run = settings_cli("--json", "settings", action)
        kit.equal(run.code, cli.EXIT_OK, f"settings {action} without a file: exit status")
    shown = json.loads(settings_cli("--json", "settings", "show").stdout)
    kit.equal((shown["exists"], shown["sources"]["worker.timeout_seconds"]), (False, "default"),
              "settings show without a file")
    kit.equal(home_delta(before, home_snapshot(kit)), {}, "settings show/path without a file: what changed under HOME")
    kit.check("(not created yet)" in settings_cli("settings", "show").stdout, "settings show did not say the file is not created")

    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "worker": {"timeout_seconds": 90}}, indent=2) + "\n")
    before = home_snapshot(kit)
    shown = json.loads(settings_cli("--json", "settings", "show").stdout)
    kit.equal((shown["values"]["worker.timeout_seconds"], shown["sources"]["worker.timeout_seconds"]), (90, "file"),
              "settings show with a file")
    overridden = json.loads(settings_cli("--timeout", "120", "--json", "settings", "show").stdout)
    kit.equal((overridden["values"]["worker.timeout_seconds"], overridden["sources"]["worker.timeout_seconds"]),
              (120, "cli"), "a command-line override wins")
    settings_cli("settings", "path")
    kit.equal(home_delta(before, home_snapshot(kit)), {}, "settings show/path with a file: what changed under HOME")
    kit.observe("observation", {"without-file": "unchanged", "file-source": shown["sources"]["worker.timeout_seconds"],
                                "override-source": overridden["sources"]["worker.timeout_seconds"]})

    # (b) `settings clean`: first use (no settings directory yet) and an existing file with an unknown key.
    shutil.rmtree(path.parent)
    before = home_snapshot(kit)
    first = settings_cli("--json", "settings", "clean")
    kit.equal(first.code, cli.EXIT_OK, "settings clean on first use: exit status")
    kit.equal(home_delta(before, home_snapshot(kit)),
              {config_dir: "added", relative(path): "added", relative(lock): "added"},
              "settings clean on first use: the footprint")
    filled = json.loads(path.read_text())
    filled["bogus"] = 1
    path.write_text(json.dumps(filled, indent=2, sort_keys=True) + "\n")
    before = home_snapshot(kit)
    cleaned = settings_cli("--json", "settings", "clean")
    kit.equal((cleaned.code, json.loads(cleaned.stdout)["removed"]), (cli.EXIT_OK, ["bogus"]), "settings clean removes the unknown key")
    kit.equal(home_delta(before, home_snapshot(kit)), {relative(path): "changed"}, "settings clean on an existing file: the footprint")
    kit.check("bogus" not in json.loads(path.read_text()), "the unknown key is still in the file")

    # A file last filled by a newer release is refused with exit 20 and left alone.
    newer = json.loads(path.read_text())
    newer["_table_generation"] = settings.TABLE_GENERATION + 1
    path.write_text(json.dumps(newer, indent=2, sort_keys=True) + "\n")
    before = home_snapshot(kit)
    refused = settings_cli("--json", "settings", "clean")
    kit.equal(refused.code, cli.EXIT_FAIL_CLOSED, "settings clean on a newer file: exit status")
    kit.equal(home_delta(before, home_snapshot(kit)), {}, "settings clean on a newer file: nothing changes")

    # `step` / `run` fill the missing defaults into the settings file: a first use (no directory yet) creates the file
    # and its lock; a later step on a file with missing keys changes only the file (the lock is persistent).
    shutil.rmtree(path.parent)
    with opened(kit, seeded_class()) as case:
        lc = case.seed("settings-step", lo.IMPLEMENTING)
        lc.add(lo.MILESTONE_IMPLEMENT, lc.implement("CP1"))
        lc.add(lo.MILESTONE_IMPLEMENT, lc.implement("CP2", last=True))
        before = home_snapshot(kit)
        stepped = case.cli(lc, "step")
        kit.equal(stepped.code, cli.EXIT_OK, "step on first use: exit status")
        kit.equal(home_delta(before, home_snapshot(kit)),
                  {config_dir: "added", relative(path): "added", relative(lock): "added"},
                  "step on first use: the footprint under HOME")
        path.write_text(json.dumps({"schema_version": 1, "worker": {"timeout_seconds": 90}}, indent=2) + "\n")
        before = home_snapshot(kit)
        stepped = case.cli(lc, "step")
        kit.equal(stepped.code, cli.EXIT_OK, "step: exit status")
        delta = home_delta(before, home_snapshot(kit))
        kit.equal(delta, {relative(path): "changed"}, "step: the footprint under HOME")
        filled = json.loads(path.read_text())
        kit.equal(filled["worker"]["timeout_seconds"], 90, "step kept the operator's value")
        kit.check(set(settings_defaults(settings)) <= set(flatten(filled)), "step did not fill every default")
        kit.observe("step-footprint", sorted(delta))

        # (c) A malformed file is refused by step and run; no worker starts and the file is untouched.
        broken = "{not json\n"
        path.write_text(broken)
        for command in ("step", "run"):
            before_workers = case.processes(lc)
            refusal = case.cli(lc, command)
            kit.equal((refusal.code, case.processes(lc) - before_workers, refusal.records), (cli.EXIT_FAIL_CLOSED, 0, []),
                      f"{command} on a malformed settings file")
            kit.equal(path.read_text(), broken, f"{command} rewrote a malformed settings file")
        kit.observe("malformed", {"exit": cli.EXIT_FAIL_CLOSED})


def settings_defaults(settings) -> list[str]:
    """Every dotted key the settings table defines (the keys a fill must leave in the file)."""
    return [setting.key for setting in settings.TABLE]


def flatten(data: dict, prefix: str = "") -> list[str]:
    """The dotted keys of a nested settings object (a leaf is anything but a dict, and ``notifications.events`` and
    ``routing`` are leaves of the table)."""
    keys = []
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        keys.append(dotted)
        if isinstance(value, dict):
            keys.extend(flatten(value, dotted + "."))
    return keys
