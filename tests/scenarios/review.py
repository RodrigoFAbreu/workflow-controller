"""Scenarios 11-12: external review by a Codex the Controller starts itself
(`workflow-controller-codex-reviewer` CP4).

Both drive the real ``cli.main run`` over a real vendored ``2.9.0`` Workflow at
a ``AWAITING_MANUAL_EXTERNAL_*_REVIEW`` gate, with ``reviewer.harness`` set to
``codex``, the real ``review.launch_review`` and ``tests/fake_codex.py`` written
into the scratch ``bin/``. The harness registry is patched (the real one stays
empty until the operator records the D12 pre-step), the usage clock is injected
and the operator's Codex home is a scratch directory: no model usage, no
network, no operator configuration.

Like ``lifecycle.py`` they honour ``kit.params["mutation"]`` so that each claim
fails when the Controller check it rests on is broken (R7): a mutation patches
one product function and the scenario must then end in an assertion.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from tests.scenarios import ScenarioFailure, scenario
from tests.scenarios.features import write_settings
from tests.scenarios.lifecycle import mutation

#: The fake's verdict header values come from the bundle the Workflow wrote; this manifest only feeds the direct
#: launches of the boundary sweep, which never reach a Workflow.
SWEEP_MANIFEST = {"bundle_id": "b" * 64, "base_commit": "c" * 40, "work_item_id": "item-1",
                  "review_content_id": "d" * 64, "plan_revision": "3", "implementation_revision": "2"}
RESERVED_ENV = {"PATH": "/evil", "OPENAI_API_KEY": "evil"}


class Shim(unittest.TestCase):
    """The ``unittest`` object ``test_protocol_lifecycle.Lifecycle`` wants (cleanups, ``assertEqual``, ``fail``)."""

    def runTest(self) -> None:  # pragma: no cover
        pass


class World:
    """A 2.9.0 work item at a manual external review gate, the fake Codex, the usage clock and a ``run`` driver."""

    def __init__(self, kit) -> None:
        from controller import review
        from tests import fixtures
        from tests.test_protocol_lifecycle import Lifecycle
        self.kit = kit
        self.test = Shim()
        self.life = Lifecycle(self.test, "2.9.0")
        # the audit hook allows only executables under the scratch bin/
        self.life.stub = fixtures.write_stub_workflow_manager(kit.bin_dir / "workflow-manager-2.9.0", release="2.9.0")
        self.clock = [time.time()]
        self.sleeps: list[float] = []
        self.operator = kit.home / "codex-operator"
        self.operator.mkdir(parents=True, exist_ok=True)
        self.auth = self.operator / "auth.json"
        self.auth.write_text(json.dumps({"tokens": {"refresh_token": "orig"}}))
        self.system = kit.scratch / "etc-codex"
        self.system.mkdir(exist_ok=True)
        self.log = kit.scratch / "fake-codex.log"
        self.count = 0
        self.intent: list = []
        self.change_operator_during_launch = False
        self.stack = contextlib.ExitStack()
        self.real_launch = review.launch_review

    # --- plumbing ------------------------------------------------------------

    def __enter__(self) -> "World":
        from controller import cli, handoff, job, review, worker
        from tests.test_protocol_lifecycle import _result
        self.stack.enter_context(unittest.mock.patch.dict(review.HARNESSES, {"codex": review.CODEX}))
        self.stack.enter_context(unittest.mock.patch.object(job, "usage_now", lambda: self.clock[0]))
        self.stack.enter_context(unittest.mock.patch.object(cli, "_usage_sleep", self.sleep))
        self.stack.enter_context(unittest.mock.patch.object(handoff, "detect", lambda ident, origin: None))
        self.stack.enter_context(unittest.mock.patch.dict(os.environ, {"CODEX_HOME": str(self.operator)}))
        self.stack.enter_context(unittest.mock.patch.object(review, "launch_review", self.launch))

        def claude(task, *, cwd, **kwargs):
            self.life.effect(task)
            return _result()
        self.stack.enter_context(unittest.mock.patch.object(worker, "launch", claude))
        self.apply_mutation()
        return self

    def __exit__(self, *exc) -> None:
        self.stack.close()
        self.test.doCleanups()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.clock[0] += seconds

    def binary(self, scenario_values: dict | None = None) -> Path:
        """A fake ``codex`` in the scratch ``bin/`` (allowed to start) with its own scenario file."""
        from tests import fake_codex
        self.count += 1
        values = {"log": str(self.log), "system_dir": str(self.system), "orphan_seconds": 30, **(scenario_values or {})}
        return fake_codex.write_binary(self.kit.bin_dir, values, name=f"codex{self.count}")

    def configure(self, binary: Path, **settings) -> None:
        values = {"reviewer": {"harness": "codex", "binary": str(binary), "timeout_seconds": 120},
                  "usage": {"max_wait_seconds": 60, "resume_grace_seconds": 1}}
        for key, value in settings.items():
            values[key] = {**values.get(key, {}), **value}
        write_settings(self.kit, values)

    def launch(self, **kw) -> dict:
        """``review.launch_review`` with the composing command and the outside layer of the test (the real Codex
        records its own in the D12 pre-step), noting what the job record said when the launch began."""
        from controller import review
        path = self.life.runtime / "jobs" / f"{kw['job_id']}.json"
        self.intent.append(bool(json.loads(path.read_text())["review"].get("launched")))
        binary = Path(kw["binary_name"])
        layout = review.JobLayout(kw["runtime_root"], kw["job_id"])
        return self.real_launch(**{**kw, "system_dir": self.system, "inherited_env": dict(RESERVED_ENV),
                                   "compose": self.composer(binary, layout)})

    def composer(self, binary: Path, layout):
        from controller import review

        def compose(env):
            if self.change_operator_during_launch:  # an interactive login while the review runs
                self.auth.write_text(json.dumps({"tokens": {"refresh_token": "interactive"}}))
            pinned = review.argv("exec", binary=binary, tree=layout.tree, verdict=layout.verdict, tmpdir=layout.tmp,
                                 prompt="-")
            overrides = [item for i, item in enumerate(pinned) if i and pinned[i - 1] == "-c"]
            command = [str(binary), "debug", "config", "-C", str(layout.tree)]
            for item in overrides:
                command += ["-c", item]
            result = review.run_bounded(command, cwd=layout.tree, env=env, timeout=30)
            if result.exit_code != 0:
                raise ScenarioFailure(f"the fake's composing command failed: {result.stderr!r}")
            return json.loads(result.stdout)
        return compose

    def run(self, command: str = "run", *extra: str):
        """``cli.main <command>`` against the work item's repository with the Controller's own runtime directory."""
        from controller import cli, identity
        self.kit.install_audit_hook()
        ident = self.kit._identity()
        argv = ["--runtime-dir", str(self.life.runtime), "--workflow-manager", str(self.life.stub),
                "--claude-binary", str(self.kit.bin_dir / "claude"), "--timeout", "60", command, *extra,
                str(self.life.root)]
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(identity, "pin", return_value=ident), \
                unittest.mock.patch.object(identity, "current", return_value=ident), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return SimpleNamespace(code=code, stdout=out.getvalue(), stderr=err.getvalue())

    # --- reading the outcome ---------------------------------------------------

    def reviews(self) -> list[dict]:
        records = [json.loads(p.read_text()) for p in sorted((self.life.runtime / "jobs").glob("*.json"),
                                                            key=lambda p: p.stat().st_mtime_ns)]
        return [r for r in records if r.get("kind") == "review"]

    def log_records(self, kind: str | None = None) -> list[dict]:
        from tests import fake_codex
        return [r for r in fake_codex.read_log(self.log) if kind is None or r["kind"] == kind]

    def phase(self) -> str:
        return self.life.phase()

    def job_dirs(self) -> list[str]:
        base = self.life.runtime / "reviews"
        return sorted(p.name for p in base.glob("*/tree")) if base.is_dir() else []

    def record_usage(self, percent: float, *, window: str = "five_hour", resets_in: float = 3600) -> None:
        from controller import usage
        usage.record_reading(self.life.runtime, [usage.make_reading(
            "codex", window, percent, self.clock[0] + resets_in, self.clock[0], "scenario")])

    def forget_shared_codex_readings(self) -> None:
        """Drop the Codex readings from the shared usage record, as a second runtime directory or a rebuilt record
        would: afterwards only the retained review homes know what the last review saw."""
        from controller import runtime, usage
        with runtime.usage_lock(self.life.runtime):
            record = usage.load_record(self.life.runtime)
            record["readings"].pop("codex", None)
            usage._save(self.life.runtime, record)

    # --- mutations ---------------------------------------------------------------

    def apply_mutation(self) -> None:
        """One product check broken, so the claim resting on it must fail (R7)."""
        from controller import job, protocol, review, usage
        name = mutation(self.kit)
        patch = lambda target, attr, value: self.stack.enter_context(  # noqa: E731
            unittest.mock.patch.object(target, attr, value))
        real_argv, real_build_home, real_copy_back = review.argv, review.build_home, review.copy_back_credentials
        real_persist = job._persist
        noop = lambda *a, **k: None  # noqa: E731

        def without(pairs: tuple, flag_pairs: tuple = ()):
            """``review.argv`` minus every ``-c <override>`` in ``pairs`` and every ``<flag> <value>`` in ``flag_pairs``."""
            def build(kind, **kw):
                out = real_argv(kind, **kw)
                kept, skip = [], False
                for i, item in enumerate(out):
                    if skip:
                        skip = False
                        continue
                    nxt = out[i + 1] if i + 1 < len(out) else None
                    if (item == "-c" and any(nxt and nxt.startswith(p) for p in pairs)) or (item, nxt) in flag_pairs:
                        skip = True
                        continue
                    kept.append(item)
                return kept
            return build

        if name == "no-header-check":
            patch(review, "verify_verdict", lambda text, expected: review.VerdictCheck("APPROVE", ()))
        elif name == "no-ingest":
            patch(protocol, "record_external_result", lambda *a, **k: SimpleNamespace(
                outcome="recorded", basis=None, round=1, bundle_id="skipped"))
        elif name == "copy-kept":
            patch(review, "settle_job_dir", noop)
        elif name == "repository-unguarded":
            patch(review, "compare_snapshots", lambda before, after: [])
        elif name == "sandbox-dropped":
            patch(review, "argv", without(("approval_policy=", "sandbox_mode=", "sandbox_workspace_write.network_access="),
                                          (("-s", "workspace-write"),)))
            patch(review, "check_argv", noop)
        elif name == "overrides-dropped":
            patch(review, "argv", without(("notify=", "web_search="), (("--disable", "hooks"),)))
            patch(review, "check_argv", noop)
        elif name == "provider-pin-dropped":
            patch(review, "argv", without(("model_provider=",)))
            patch(review, "check_argv", noop)
        elif name == "features-not-disabled":
            patch(review, "DISABLED_FEATURES", ())
            patch(review, "check_features", noop)
            patch(review, "check_argv", noop)
        elif name == "env-widened":
            real_environment = review.child_environment
            patch(review, "child_environment", lambda layout, binary, tag, inherited=None: {
                **{k: v for k, v in (inherited or {}).items() if k != "PATH"},
                **real_environment(layout, binary, tag, inherited)})
        elif name == "inherited-home":
            def build_home_with_operator_config(layout, operator_home):
                digest = real_build_home(layout, operator_home)
                if (operator_home / "config.toml").is_file():
                    (layout.codex_home / "config.toml").write_bytes((operator_home / "config.toml").read_bytes())
                return digest
            patch(review, "build_home", build_home_with_operator_config)
            patch(review, "check_home", noop)
        elif name == "mcp-list-unchecked":
            patch(review, "check_mcp_list", noop)
        elif name == "effective-unchecked":
            patch(review, "check_effective", noop)
        elif name == "outside-layers-unchecked":
            patch(review, "check_outside_layers", noop)
        elif name == "home-unchecked":
            patch(review, "check_home", noop)
        elif name == "no-copy-back":
            patch(review, "copy_back_credentials", lambda layout, operator_home, captured: "unchanged")
        elif name == "copy-back-over-changes":
            patch(review, "copy_back_credentials", lambda layout, operator_home, captured: real_copy_back(
                layout, operator_home, review._sha256_file(operator_home / "auth.json")))
        elif name == "admission-without-readings":
            patch(usage, "read_codex_homes", lambda homes: [])
        elif name == "admission-operator-only":
            patch(review, "retained_codex_homes", lambda runtime_root: [])
        elif name == "no-launch-intent":
            def persist_without_intent(runtime_root, job_id, record, **kw):
                body = record.get("review")
                if isinstance(body, dict) and body.get("launched") is True:
                    record = {**record, "review": {**body, "launched": False}}
                return real_persist(runtime_root, job_id, record, **kw)
            patch(job, "_persist", persist_without_intent)
        elif name:
            raise ScenarioFailure(f"no such mutation: {name}")

    # --- direct launches (the boundary sweep) -------------------------------------

    def direct(self, scenario_values: dict | None = None, *, job: str, stage: str = "plan"):
        """``review.launch_review`` itself against a fake with ``scenario_values``; the refusal it raises, or ``None``."""
        from controller import review
        from controller.errors import ReviewLaunchRefusedError
        binary = self.binary(scenario_values)
        layout = review.JobLayout(self.life.runtime, job)
        try:
            self.real_launch(
                worktree=self.life.root, runtime_root=self.life.runtime, job_id=job, harness_name="codex",
                binary_name=str(binary), operator_home=self.operator, stage=stage, manifest=SWEEP_MANIFEST,
                bundle_dir="/b/current", model="gpt-6.1-sol", effort="high", timeout=60, system_dir=self.system,
                compose=self.composer(binary, layout), inherited_env=dict(RESERVED_ENV))
        except ReviewLaunchRefusedError as exc:
            return exc
        return None


# ---------------------------------------------------------------------------
# Scenario 11
# ---------------------------------------------------------------------------

PLAN_GATE = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
IMPLEMENTATION_GATE = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"


def hostile_script(world: World, name: str) -> Path:
    """A local script that leaves a marker file when anything starts it: the operator's own configuration names it as
    an MCP server, a hook and a ``notify`` command, none of which the review may run."""
    script = world.kit.scratch / f"{name}.sh"
    marker = world.kit.scratch / f"{name}.ran"
    script.write_text(f"#!/bin/sh\necho ran > {marker}\n")
    script.chmod(0o755)
    return marker


def sweep(world: World, kit) -> None:
    """The boundary refusals, each by a direct launch against a fake with one poisoned input: nothing is started."""
    features = kit.scratch / "features-with-a-new-tool.txt"
    features.write_text(review_features_capture().read_text() + "shiny_new_tool stable true\n")
    server = kit.scratch / "sweep-server.sh"
    server.write_text("#!/bin/sh\ntrue\n")
    server.chmod(0o755)
    managed_provider = kit.scratch / "managed-provider.toml"
    managed_provider.write_text('model_provider = "corp"\n[model_providers.corp]\nbase_url = "http://x"\n')
    managed_server = kit.scratch / "managed-server.toml"
    managed_server.write_text(f'[mcp_servers.m]\ncommand = "{server}"\n')
    cases = (
        ("outside layer", {}, "layer", "outside-layer"),
        ("managed provider", {"managed": str(managed_provider)}, "effective", None),
        ("mcp server", {"managed": str(managed_server)}, "mcp", None),
        ("unlisted feature", {"features": str(features)}, "features", None),
        ("startup-loaded entry", {"state_extra": ["hooks.json"]}, "home", None),
    )
    for index, (what, values, check, special) in enumerate(cases):
        if special == "outside-layer":  # content that only the inventory objects to: it is no server, hook or provider
            (world.system / "config.toml").write_text('model_verbosity = "low"\n')
        refusal = world.direct(values, job=f"sweep-{index}")
        (world.system / "config.toml").write_text("")
        kit.check(refusal is not None, f"{what}: the launch was not refused")
        kit.equal(refusal.evidence.get("check"), check, f"{what}: the check that refused")
    kit.equal(world.log_records("exec"), [], "a refused launch must not start the review")
    kit.observe("refusals", [case[0] for case in cases])


def review_features_capture() -> Path:
    from tests import fake_codex
    return fake_codex.DEFAULT_FEATURES


@scenario("scenario-codex-review-plan-stage", covers=("C7",))
def codex_review_plan_stage(kit) -> None:
    """Plan-stage external review by the Controller's own Codex, through ``run``: a usage hold at admission starts
    nothing; a verdict whose revision label is wrong is retried once and then ends ``FAILED`` with nothing ingested;
    a review that writes into the real repository is voided (``INCOMPLETE``, exit 35); an exact verdict is ingested
    by the Workflow with the whole boundary in place (sandbox, environment, minimal home, no inherited server, hook
    or ``notify``, features off, the disposable copy gone, the credential copy written back); and the refusals of
    the boundary sweep start nothing."""
    from controller import cli, job, review
    with World(kit) as world:
        life = world.life
        life.run_until(PLAN_GATE)
        claude_tasks = len(life.tasks)
        servers = [hostile_script(world, name) for name in ("mcp", "hook", "notify")]
        (world.operator / "config.toml").write_text(
            f'notify = ["{servers[2].with_suffix(".sh")}"]\n[mcp_servers.anything]\ncommand = "{servers[0].with_suffix(".sh")}"\n'
            f'[hooks.start]\ncommand = "{servers[1].with_suffix(".sh")}"\n')

        # 1. a hold at admission starts nothing
        world.configure(world.binary())
        world.record_usage(99)
        held = world.run()
        kit.equal(held.code, cli.EXIT_USAGE_PAUSED, "a run over the Codex budget: exit status")
        kit.check("codex" in held.stderr.lower(), "the hold message does not name the Codex provider")
        kit.equal((world.reviews(), world.log_records(), world.phase()), ([], [], PLAN_GATE),
                  "a held review: job records, fake invocations, phase")
        kit.observe("hold", {"exit": held.code})
        world.clock[0] += 3700

        # 2. the boundary sweep
        sweep(world, kit)

        # 3. a verdict that never carries its revision: one retry, then FAILED, nothing ingested
        world.configure(world.binary({"exec": {"verdict": "bad_revision"}}))
        failed = world.run()
        kit.equal(failed.code, cli.EXIT_WORKER_FAILED, "a verdict with a wrong header: exit status")
        [record] = world.reviews()
        kit.equal((record["status"], record["reconciliation_evidence"]["code"]),
                  (job.STATUS_FAILED, job.REVIEW_HEADER_MISMATCH_CODE), "the record of a verdict with a wrong header")
        kit.equal(len(world.log_records("exec")), 2, "the exec invocations of a wrong header (one bounded retry)")
        kit.equal(world.phase(), PLAN_GATE, "nothing is ingested for a wrong header")

        # 4. a sandbox escape into the real repository voids the verdict
        escaped = life.root / "ESCAPED.txt"
        world.configure(world.binary({"exec": {"escape_to": str(escaped)}}))
        voided = world.run()
        escaped.unlink(missing_ok=True)
        kit.equal(voided.code, cli.EXIT_INCOMPLETE, "a review that changed the real repository: exit status")
        kit.equal(world.reviews()[-1]["reconciliation_evidence"]["code"], job.REVIEW_CHANGED_CODE,
                  "the code of a voided review")
        kit.equal(world.phase(), PLAN_GATE, "a voided review ingests nothing")

        # 5. the exact verdict, with the whole boundary in place
        world.configure(world.binary({"exec": {"refresh_auth": True, "use_web_search": True, "tool_attempts": [
            "browser_use", "computer_use", "plugins", "image_generation", "multi_agent"]}}))
        before_exec = len(world.log_records("exec"))
        done = world.run()
        kit.equal(done.code, cli.EXIT_GATE, "the review ingested, the run stops at the plan approval gate")
        kit.equal(world.phase(), "AWAITING_PLAN_APPROVAL", "the phase after an ingested review")
        record = world.reviews()[-1]
        kit.equal((record["status"], record["worker_route"]["harness"]), (job.STATUS_FINISHED, "codex"),
                  "the review record")
        kit.equal(record["review"]["ingested"], "recorded", "the Workflow recorded the verdict")
        kit.equal(life.tasks[claude_tasks:], [], "no Claude worker launched for the external review")
        kit.check(world.intent and all(world.intent), "the launch intent was not flushed before every spawn")
        kit.equal(world.job_dirs(), [], "the disposable copies are removed")
        kit.equal(json.loads(world.auth.read_text())["tokens"]["refresh_token"].startswith("rotated-"), True,
                  "the refreshed credential was not written back to the operator's file")
        kit.check(not any(marker.exists() for marker in servers), "an inherited server, hook or notify command ran")
        boundary(world, kit, world.log_records()[-4:], record["job_id"], before_exec)
        kit.observe("boundary", {"exec_invocations": len(world.log_records("exec")), "phase": world.phase()})


def boundary(world: World, kit, records: list[dict], job_id: str, before_exec: int) -> None:
    """What the fake recorded for the exact-verdict job: every invocation and the boundary it ran under."""
    from controller import review
    kit.equal([r["kind"] for r in records], ["mcp", "features", "compose", "exec"], "the invocations of one review")
    layout = review.JobLayout(world.life.runtime, job_id)
    for record in records:
        kit.equal(record["codex_home"], str(layout.codex_home), f"{record['kind']}: the Codex home")
        kit.equal(record["ran"], [], f"{record['kind']}: commands started from inherited configuration")
        extra = set(record["env"]) - set(review.ENV_ALLOWLIST) - {"PWD", "SHLVL", "_", "OLDPWD", "LC_CTYPE"}
        kit.equal(sorted(extra), [], f"{record['kind']}: environment variables beyond the allowlist")
        kit.check("OPENAI_API_KEY" not in record["env"], f"{record['kind']}: an inherited key reached the child")
    execution = records[-1]
    kit.equal((execution["approval_policy"], execution["network_access"], execution["web_search"]),
              ("never", False, "disabled"), "approval, network and web search")
    kit.check(execution["argv"][execution["argv"].index("-s") + 1:][:1] == ["workspace-write"]
              if "-s" in execution["argv"] else False, "the workspace-write sandbox was not selected")
    kit.equal(len(execution["writable_roots"]), 2, "the writable roots: the copy and the private temporary directory")
    kit.equal(execution["overrides"].get("model_provider"), "openai", "the provider pin")
    kit.equal(execution["overrides"].get("notify"), [], "the notify override")
    allowed = set(review.TOOL_ALLOWLIST)
    kit.equal(sorted(set(execution["features_enabled"]) - allowed), [], "features enabled beyond the allowlist")
    kit.check("hooks" not in execution["features_enabled"], "the hooks feature is enabled")
    kit.equal([t for t in execution["tools"] if t["used"]], [], "tools a review used that it must not have")
    kit.equal(len(world.log_records("exec")), before_exec + 1, "one exec for the exact verdict")


# ---------------------------------------------------------------------------
# Scenario 12
# ---------------------------------------------------------------------------

def event_types(hub) -> list[str]:
    return [r["body"]["metadata"]["event_type"] for r in hub.requests]


@scenario("scenario-codex-review-implementation-stage-limit", covers=("C7", "C8"))
def codex_review_implementation_stage_limit(kit) -> None:
    """Implementation-stage external review when Codex hits its limit: the job ends ``INCOMPLETE`` and ``run`` exits 35
    with a ``lane_blocked`` notification and the reservation charged by the measured delta; a second ``run`` is held
    at admission by the retained rollout of that review (the operator's home holds nothing), exit 17, no new job; after
    the reset a third ``run`` is re-admitted, reviews, and the Workflow records the verdict; an operator's file that
    changed meanwhile is never overwritten by the credential copy-back."""
    from controller import cli, job, notify, usage
    from tests.fake_signalhub import FakeSignalHub
    secret = "scenario-review-secret-0123456789abcdef"
    hub = FakeSignalHub().start()
    try:
        env = {"SIGNALHUB_URL": hub.url, "SIGNALHUB_API_KEY": secret}
        with World(kit) as world, unittest.mock.patch.dict(os.environ, env), \
                unittest.mock.patch.object(notify, "FLUSH_MARGIN_SECONDS", 1.0):
            life = world.life
            life.manual_gate(PLAN_GATE, "plan")
            life.run_until("AWAITING_PLAN_APPROVAL")
            from tests.test_protocol_lifecycle import _APPROVE_PLAN
            life.user_step(_APPROVE_PLAN)
            life.run_until(IMPLEMENTATION_GATE)
            claude_tasks = len(life.tasks)
            notifications = {"notifications": {"timeout_seconds": 1, "attempts": 1, "retry_seconds": 1}}

            # 1. the limit during the job (the admission started from a 70% reading in the rollout's windows)
            world.record_usage(70.0, resets_in=3600)
            world.record_usage(1.0, window="weekly", resets_in=86400)
            world.configure(world.binary({"exec": {"verdict": "limit", "exit": 1},
                                          "rate_limits": {"primary": 99.0, "secondary": 6.0}}), **notifications)
            limited = world.run()
            kit.equal(limited.code, cli.EXIT_INCOMPLETE, "a limit during the review: exit status")
            [first] = world.reviews()
            kit.equal((first["status"], first["reconciliation_evidence"]["code"]),
                      (job.STATUS_INCOMPLETE, job.REVIEW_LIMIT_CODE), "the record of a limited review")
            kit.equal(world.phase(), IMPLEMENTATION_GATE, "a limited review ingests nothing")
            shared = usage.load_record(world.life.runtime)
            kit.equal((shared["reservations"], [e["five_hour"] for e in shared["ledger"]]), ({}, [29.0]),
                      "the reservation is settled by the measured delta")
            kit.equal(world.job_dirs(), [], "the disposable copy is removed after a limit")
            retained = sorted((world.life.runtime / "reviews").glob("*/codex-home/sessions"))
            kit.equal(len(retained), 1, "the review's rollout was not retained")
            world.forget_shared_codex_readings()
            kit.check("lane_blocked" in event_types(hub), "no lane_blocked notification for the limited review")
            kit.observe("limit", {"exit": limited.code, "events": event_types(hub)})

            # 2. a second run: the operator's home holds nothing and the shared record no longer has the reading
            #    (another runtime directory, a reset record): the retained rollout is the only place the 99% is
            exec_before = len(world.log_records("exec"))
            world.configure(world.binary(), **notifications)
            held = world.run()
            kit.equal(held.code, cli.EXIT_USAGE_PAUSED, "the run after the limit: exit status")
            kit.check("codex" in held.stderr.lower(), "the hold message does not name the Codex provider")
            kit.equal((len(world.reviews()), world.sleeps, len(world.log_records("exec"))), (1, [], exec_before),
                      "a held run: job records, waits, fake invocations")
            kit.observe("held", {"exit": held.code})

            # 3. after the reset: re-admitted, the verdict is ingested; the operator logged in again meanwhile
            world.clock[0] += 3700
            world.change_operator_during_launch = True
            world.configure(world.binary({"exec": {"refresh_auth": True}}), **notifications)
            done = world.run()
            records = world.reviews()
            kit.equal(len(records), 2, f"the job records after the re-admission (exit {done.code}: {done.stderr[-600:]})")
            kit.equal(records[-1]["status"], job.STATUS_FINISHED, "the re-admitted review")
            kit.check(world.phase() != IMPLEMENTATION_GATE, "the verdict was not ingested by the Workflow")
            kit.equal(records[-1]["review"]["credentials"], "operator-changed", "the credential copy-back result")
            kit.equal(json.loads(world.auth.read_text())["tokens"]["refresh_token"], "interactive",
                      "the operator's file was overwritten by the copy-back")
            kit.equal((done.code, life.tasks[claude_tasks:]), (cli.EXIT_GATE, []),
                      "the re-admitted run stops at the technical approval gate, no Claude worker launched")
            kit.equal(event_types(hub), ["lane_blocked", "run_finished", "usage_paused", "run_finished", "step_done",
                                         "user_gate", "run_finished"], "the notifications of the three runs, in order")
            kit.check(secret not in json.dumps([r["body"] for r in hub.requests]), "the API key is in an event body")
            kit.observe("readmitted", {"exit": done.code, "phase": world.phase(), "events": event_types(hub)})
    finally:
        hub.stop()
