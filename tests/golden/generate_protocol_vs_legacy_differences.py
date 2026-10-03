"""Generator for ``tests/golden/protocol_vs_legacy_differences.json`` -- the
measured differences between the Workflow's own ``next-action`` (protocol
mode) and 1.6.0's ``controller.evidence.decide`` (legacy mode)
(``docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md``, C.7).

**What is compared.** One fixture repository per scenario. The legacy
decision is made with Workflow 2.6.0 installed (its real contract queries
run); the same repository, the same state and the same files then have
Workflow 2.7.0 installed over them, and the protocol decision is made by
``controller.protocol_decision.decide``. The dimensions are exactly the
plan's: the decision kind (``launch``, or ``non-launch`` for any gate, block,
decline or completion), the command token of a launch, and whether a launch
names a user-only command. Reason text is never compared.

**Rule.** Every scenario names the difference it is expected to show
(``D1``..``D7``, plan "Differences the protocol makes") or ``None``. The test
asserts the measured difference equals the named one, so a difference outside
D1-D7 fails and is a plan amendment, not a silent addition.

**Scope.** The plan's: the plan-stage and implementation-review scenarios a
real 2.7.0 fixture can reproduce, ``AWAITING_FUNCTIONAL_REVIEW`` built as
fixtures (no checklist evidence, a current checklist and no findings,
unconsumed findings), and the ``"1"``/``"2.1"``
``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` cases of
``tests/golden/external_implementation_review_decisions.json``.
:data:`EXCLUDED` names, with the measured reason, each case that replays a
state a real repository cannot hold.

Run ``python3 tests/golden/generate_protocol_vs_legacy_differences.py`` to
rewrite the golden, or with ``--check`` to compare without writing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from controller import decision as _decision  # noqa: E402
from controller import evidence, protocol, protocol_decision, target_state  # noqa: E402
from controller.errors import WorkflowProtocolRefusedError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.golden import generate_external_implementation_review_decisions as external_golden  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "protocol_vs_legacy_differences.json"

WID = "wi-1"
STATE_REL = "docs/ai-workflow/WORKFLOW_STATE.json"
TARGET_PROTOCOL = {"major": 1, "version": "1.0", "release": "2.7.0"}

#: The seven observable differences (plan, "Differences the protocol makes"):
#: id -> the class (phase, governing version, scenario family) it names.
DIFFERENCES: dict[str, str] = {
    "D1": "REVISING_PLAN (2.1/2.2) with no applicable REVISE: 1.6.0 launches /apply-plan-review, the protocol "
          "(row 9) launches /milestone-plan",
    "D2": "a \"1\" item at a phase where 1.6.0 launches and the protocol is blocked (rows 4, 6a)",
    "D3": "AMENDING_PLAN (2.1/2.2): /milestone-plan is automatic (row 7); 1.6.0 declined it",
    "D4": "APPLYING_REVIEW_FEEDBACK at 1/2.1 is automatic (row 36); 1.6.0's static gate is more conservative",
    "D5": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW at 1/2.1 with a current REVISE or BLOCK (rows 31/32): the "
          "protocol launches /apply-implementation-review, 1.6.0 only reports",
    "D6": "AWAITING_FUNCTIONAL_REVIEW with no current checklist evidence (row 37): the protocol launches "
          "/prepare-functional-review, 1.6.0 gates",
    "D7": "AWAITING_FUNCTIONAL_REVIEW with an unconsumed FUNCTIONAL_REVIEW.md (row 38): the protocol launches "
          "/apply-functional-review, 1.6.0 declines it",
}

# ---------------------------------------------------------------------------
# Fixture building.
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def build(tmp: str, phase: str, version: str, *, layout: str | None = "scoped", **entry_fields) -> Path:
    """A committed repository with Workflow 2.6.0 installed and one work
    item at ``phase`` (governing ``version``), its state written by hand:
    what a worker's own writers leave, for the phases a protocol answer does
    not need a real bundle for."""
    root = Path(tmp) / "target"
    root.mkdir()
    fixtures.git_init(root)
    _git(root, "config", "user.email", "equivalence@example.invalid")
    _git(root, "config", "user.name", "Equivalence")
    (root / "README.md").write_text("equivalence fixture\n")
    (root / ".gitignore").write_text(".ai-review/\n")
    fixtures.install_workflow_release(root, "2.6.0")
    docs = root / "docs" / "ai-workflow"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "WORKFLOW_CONFIG.json").write_text(json.dumps(
        {"schema_version": 1, "default_workflow_version": "2.2", "supported_versions": ["1", "2.1", "2.2"]}))
    base = fixtures.commit_all(root, "base")
    entry = {
        "work_item_type": "product", "work_item_kind": "product", "work_item_id": WID,
        "governing_workflow_version": version, "phase": phase, "plan_revision": 1,
        "implementation_revision": None, "reviewed_implementation_head": None, "state_revision": 1,
        "checkpoints": {}, "current_bundle_id": None, "last_completed_checkpoint_id": None,
        "base_commit": base, "parent_work_item_id": None, "registry_path": None, "plan_approval": None,
    }
    if layout is not None and version != "1":
        entry["feedback_layout"] = layout
    entry.update(entry_fields)
    fixtures.write_workflow_state(root, {"schema_version": 1, "active_work_item_id": WID, "work_items": {WID: entry}})
    fixtures.commit_all(root, "state")
    return root


def seeded(tmp: str, stage: str, version: str) -> Path:
    """A repository whose work item was routed and published by Workflow
    2.6.0's own writers, up to ``stage`` (``fixtures.WORKFLOW_SEED_SCRIPT``)."""
    root = Path(tmp) / "target"
    fixtures.seed_workflow_item(root, "2.6.0", stage, work_item_id=WID, governing_workflow_version=version)
    return root


# ---------------------------------------------------------------------------
# The scenarios.
# ---------------------------------------------------------------------------

_FEEDBACK_SCOPED = Path(".ai-review") / WID / "feedback"
_FEEDBACK_FLAT = Path(".ai-review") / "feedback"
_BUNDLE_FLAT = Path(".ai-review") / "current"
#: Where the Workflow keeps the checklist (``workflow_state.FUNCTIONAL_CHECKLIST_PATH``).
_CHECKLIST_PATH = "docs/ACTIVE_MILESTONE.md"


def _flat_manifest(root: Path, *, generation_head: str | None = None) -> None:
    head = _git(root, "rev-parse", "HEAD") if generation_head is None else generation_head
    fixtures.write_manifest(root, _BUNDLE_FLAT, fixtures.build_manifest_text(generation_head=head))


def _flat_feedback(root: Path, status: str | None) -> None:
    """Feedback bound to the flat bundle above and to the item's real base
    commit, so the Workflow's own binding check accepts it as current."""
    base = _git(root, "rev-parse", "HEAD~1")
    # The Workflow computes a bundle's id from its content; it is never read
    # from the manifest, so the feedback must name the computed one.
    bundle_id = fixtures.run_workflow_python(
        root, "print(fingerprint.compute_bundle_id(Path.cwd() / '.ai-review' / 'current')[0])").stdout.strip()
    fixtures.write_review_feedback(root, _FEEDBACK_FLAT, fixtures.build_review_feedback_text(
        status=status, reviewed_base_commit=base, reviewed_bundle_id=bundle_id))


def _checklist_commit(root: Path, round_: int = 1) -> None:
    path = root / _CHECKLIST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Functional review checklist\n\n- [ ] it works\n")
    blob = _git(root, "hash-object", "--", _CHECKLIST_PATH)
    _git(root, "add", "--", _CHECKLIST_PATH)
    _git(root, "commit", "-q", "-m",
         f"checklist\n\nWorkflow-Functional-Checklist: {WID}/{round_}/{blob}\nWorkflow-Work-Item: {WID}")


def _ledger(root: Path) -> dict[str, Any]:
    """The ids a `/review-plan` REVISE binds to: the review content the
    recorded round consumed, and the plan bundle's computed id."""
    state = json.loads((root / STATE_REL).read_text())["work_items"][WID]
    bundle_id = fixtures.run_workflow_python(
        root, "print(fingerprint.compute_bundle_id(Path.cwd() / '.ai-review' / sys.argv[1] / 'current')[0])",
        WID).stdout.strip()
    return {"bundle_id": bundle_id, "review_content_id": state["plan_review_binding"]["consumed"]["review_content_id"],
            "base": state["base_commit"]}


def _local_revise_feedback(root: Path) -> None:
    """The REVIEW_FEEDBACK.md `/review-plan` leaves with its REVISE: bound to
    the recorded bundle, content and base."""
    ids = _ledger(root)
    fixtures.write_review_feedback(root, _FEEDBACK_SCOPED, fixtures.build_review_feedback_text(
        status="REVISE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id=ids["bundle_id"],
        reviewed_base_commit=ids["base"], reviewed_content_id=ids["review_content_id"]))


def _local_approve(root: Path) -> None:
    """Workflow's own writer, as `/review-plan` calls it with an APPROVE: the
    item moves to AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW."""
    fixtures.run_workflow_python(root, (
        "import datetime\n"
        "now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')\n"
        "wid = sys.argv[1]\n"
        "bundle_dir = Path.cwd() / fingerprint.resolve_bundle_dir(Path.cwd(), wid, stage='plan')\n"
        "bundle_id = fingerprint.read_manifest_identifiers(bundle_dir / 'MANIFEST.md')['bundle_id']\n"
        "content_id = fingerprint.compute_review_content_id_plan_stage_for_work_item(Path.cwd(), wid)[0]\n"
        "ws.state_transaction(Path.cwd(), lambda state: ws.record_local_plan_review(\n"
        "    state, wid, verdict='APPROVE', bundle_id=bundle_id, review_content_id=content_id, round=1, now=now))\n"
    ), WID)


def _findings(root: Path) -> None:
    findings = root / _FEEDBACK_SCOPED / "FUNCTIONAL_REVIEW.md"
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text("# Functional review\n\n- the button does nothing\n")


class Scenario:
    def __init__(self, scenario_id: str, phase: str, version: str, make: Callable[[str], Path],
                 expected: str | None, family: str) -> None:
        self.id = scenario_id
        self.phase = phase
        self.version = version
        self.make = make
        self.expected = expected
        self.family = family


def _built(phase: str, version: str, *, setup: Callable[[Path], None] | None = None, **fields):
    def make(tmp: str) -> Path:
        root = build(tmp, phase, version, **fields)
        if setup is not None:
            setup(root)
        return root
    return make


def _built_afr(setup: Callable[[Path], None] | None = None):
    return _built("AWAITING_FUNCTIONAL_REVIEW", "2.2", implementation_revision=1, setup=setup)


def _afr_unconsumed(root: Path) -> None:
    _checklist_commit(root)
    _findings(root)


def _external(version: str, setup: Callable[[Path], None] | None = None, **fields):
    # The flat layout: no `feedback_layout`, as the golden's flat cases.
    return _built("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", version, layout=None, implementation_revision=1,
                  reviewed_implementation_head="1" * 40, setup=setup, **fields)


def _flat_with(status: str | None):
    def setup(root: Path) -> None:
        _flat_manifest(root)
        _flat_feedback(root, status)
    return setup


def _after(root: Path, step: Callable[[Path], None]) -> Path:
    step(root)
    return root


def _seeded(stage: str, version: str):
    return lambda tmp: seeded(tmp, stage, version)


SCENARIOS: tuple[Scenario, ...] = (
    # --- Plan stage, real Workflow 2.6.0 writers.
    Scenario("planning_published_2.2", "PLANNING", "2.2", _seeded("publish", "2.2"), None, "plan"),
    Scenario("planning_published_2.1", "PLANNING", "2.1", _seeded("publish", "2.1"), None, "plan"),
    Scenario("planning_bundle_generated_2.2", "PLANNING", "2.2", _seeded("generate", "2.2"), None, "plan"),
    Scenario("local_review_ready_2.2", "AWAITING_LOCAL_PLAN_REVIEW", "2.2", _seeded("ready", "2.2"), None, "plan"),
    Scenario("local_review_ready_2.1", "AWAITING_LOCAL_PLAN_REVIEW", "2.1", _seeded("ready", "2.1"), None, "plan"),
    Scenario("revising_no_feedback_2.2", "REVISING_PLAN", "2.2", _seeded("revise", "2.2"), "D1", "plan"),
    Scenario("revising_no_feedback_2.1", "REVISING_PLAN", "2.1", _seeded("revise", "2.1"), "D1", "plan"),
    Scenario("revising_with_revise_feedback_2.2", "REVISING_PLAN", "2.2",
             lambda tmp: _after(_seeded("revise", "2.2")(tmp), _local_revise_feedback), None, "plan"),
    Scenario("manual_external_review_2.2", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.2",
             lambda tmp: _after(_seeded("ready", "2.2")(tmp), _local_approve), None, "plan"),
    # --- Plan stage, state written by hand.
    Scenario("planning_v1", "PLANNING", "1", _built("PLANNING", "1"), "D2", "plan"),
    Scenario("amending_2.2", "AMENDING_PLAN", "2.2", _built("AMENDING_PLAN", "2.2"), "D3", "plan"),
    Scenario("amending_2.1", "AMENDING_PLAN", "2.1", _built("AMENDING_PLAN", "2.1"), "D3", "plan"),
    Scenario("amending_v1", "AMENDING_PLAN", "1", _built("AMENDING_PLAN", "1"), None, "plan"),
    Scenario("revising_v1", "REVISING_PLAN", "1", _built("REVISING_PLAN", "1"), None, "plan"),
    Scenario("plan_approval_2.2", "AWAITING_PLAN_APPROVAL", "2.2", _built("AWAITING_PLAN_APPROVAL", "2.2"), None, "plan"),
    Scenario("plan_approval_v1", "AWAITING_PLAN_APPROVAL", "1", _built("AWAITING_PLAN_APPROVAL", "1"), None, "plan"),
    Scenario("external_plan_review_v1", "AWAITING_EXTERNAL_PLAN_REVIEW", "1",
             _built("AWAITING_EXTERNAL_PLAN_REVIEW", "1"), None, "plan"),
    Scenario("implementing_v1", "IMPLEMENTING", "1", _built("IMPLEMENTING", "1"), None, "implementation"),
    # --- Implementation review, "1"/"2.1": the golden's cases a real fixture reproduces.
    Scenario("external_review_no_manifest_v1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "1", _external("1"),
             None, "external_implementation_review"),
    Scenario("external_review_no_feedback_2.1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.1",
             _external("2.1", lambda root: _flat_manifest(root)), None,
             "external_implementation_review"),
    Scenario("external_review_stale_generation_head_v1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "1",
             _external("1", lambda root: _flat_manifest(root, generation_head="9" * 40)), None,
             "external_implementation_review"),
    Scenario("external_review_current_approve_v1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "1",
             _external("1", _flat_with("APPROVE")), None, "external_implementation_review"),
    Scenario("external_review_current_revise_v1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "1",
             _external("1", _flat_with("REVISE")), "D5", "external_implementation_review"),
    Scenario("external_review_current_revise_2.1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.1",
             _external("2.1", _flat_with("REVISE")), "D5", "external_implementation_review"),
    Scenario("external_review_current_block_2.1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.1",
             _external("2.1", _flat_with("BLOCK")), "D5", "external_implementation_review"),
    Scenario("applying_review_feedback_v1", "APPLYING_REVIEW_FEEDBACK", "1",
             _built("APPLYING_REVIEW_FEEDBACK", "1", layout=None, implementation_revision=1,
                                               setup=_flat_with("REVISE")), "D4", "implementation"),
    Scenario("applying_review_feedback_2.1", "APPLYING_REVIEW_FEEDBACK", "2.1",
             _built("APPLYING_REVIEW_FEEDBACK", "2.1", layout=None, implementation_revision=1,
                                               setup=_flat_with("REVISE")), "D4", "implementation"),
    # --- Functional review.
    Scenario("functional_no_checklist_2.2", "AWAITING_FUNCTIONAL_REVIEW", "2.2", _built_afr(), "D6", "functional"),
    Scenario("functional_checklist_current_2.2", "AWAITING_FUNCTIONAL_REVIEW", "2.2", _built_afr(_checklist_commit),
             None, "functional"),
    Scenario("functional_unconsumed_findings_2.2", "AWAITING_FUNCTIONAL_REVIEW", "2.2", _built_afr(_afr_unconsumed),
             "D7", "functional"),
)

def _golden_sweep() -> tuple[Scenario, ...]:
    """Every case of ``external_implementation_review_decisions.json`` a real
    repository can hold, at ``"1"`` and ``"2.1"``: the golden's own setup
    functions and state overrides, written as a real state file. None is
    expected to differ: its feedback names the golden's placeholder base
    commit, so the Workflow never reads it as current."""
    swept = []
    for scenario_id, setup, overrides in external_golden.SCENARIOS:
        if scenario_id in EXCLUDED_EXTERNAL_CASES:
            continue
        scoped = scenario_id.startswith(("scoped", "rejected_scoped"))
        fields = {key: value for key, value in dict(external_golden._BASE_OVERRIDES, **overrides).items()
                  if key in ("implementation_review_stages", "technical_review_block_pins", "implementation_revision",
                             "reviewed_implementation_head")}
        fields["technical_review_block_pins"] = list(fields["technical_review_block_pins"])
        for version in ("1", "2.1"):
            def make(tmp: str, version=version, setup=setup, fields=fields, scoped=scoped) -> Path:
                root = build(tmp, external_golden.PHASE, version, layout="scoped" if scoped else None, **fields)
                setup(root, _git(root, "rev-parse", "HEAD"))
                return root
            swept.append(Scenario(f"golden:{scenario_id}|{version}", external_golden.PHASE, version, make, None,
                                  "external_implementation_review_golden"))
    return tuple(swept)


#: The golden's ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` cases a real 2.7.0
#: repository cannot hold: at ``"1"``/``"2.1"`` the Workflow refuses a state
#: that carries ``implementation_review_stages`` (``state_invalid``), which the
#: golden's ``"2.2"``-handler variants replay. Asserted, not assumed.
EXCLUDED_EXTERNAL_CASES: tuple[str, ...] = (
    "scoped_coherent_manual_approve_complete_ledger", "scoped_coherent_manual_revise_complete_ledger",
    "scoped_coherent_manual_block_complete_ledger", "scoped_coherent_approve_pinned_bundle",
    "scoped_incoherent_revision_approve",
)

#: The other classes the comparison leaves out, with why (plan C.7).
EXCLUDED: dict[str, str] = {
    "publication_status_refusal_and_unexpected": (
        "replay recorded Workflow publication-status answers a real repository cannot produce; the catalogue "
        "row each reaches is asserted on the protocol side"),
    "legacy_flat_and_legacy_scoped_layouts": "the protocol does not distinguish them from `scoped`",
    "golden_cases_with_a_state_2_7_0_refuses_at_1_and_2_1": (
        "implementation_review_stages at \"1\"/\"2.1\" is `state_invalid` in 2.7.0: "
        + ", ".join(EXCLUDED_EXTERNAL_CASES)),
}


# ---------------------------------------------------------------------------
# Measuring one scenario.
# ---------------------------------------------------------------------------


def _user_only_commands() -> frozenset[str]:
    commands = fixtures.copy_real_commands_dir(Path(_scratch()) / "commands", release="2.6.0")
    return _decision.derive_user_only_commands(commands)


_scratch_dir: list[TemporaryDirectory] = []


def _scratch() -> str:
    if not _scratch_dir:
        _scratch_dir.append(TemporaryDirectory())
    return _scratch_dir[0].name


def _shape(decision: Any, user_only: frozenset[str]) -> dict[str, Any]:
    if decision.automatic:
        token = _decision.command_token(decision.action.command)
        return {"kind": "launch", "command": token, "launches_user_only": token.removeprefix("/") in user_only}
    return {"kind": "non-launch", "command": None, "launches_user_only": False}


def measure(scenario: Scenario, user_only: frozenset[str]) -> dict[str, Any]:
    """The legacy and the protocol decision of ``scenario``, on the
    dimensions of C.7."""
    with TemporaryDirectory() as tmp:
        root = scenario.make(tmp)
        managed = fixtures.build_target_managed_repository(root)
        snapshot = target_state.read(managed)
        work_item = target_state.select_work_item(snapshot, work_item_id=WID)
        legacy = evidence.decide(managed, snapshot, work_item)
        fixtures.install_workflow_release(root, "2.7.0")
        stub = SimpleNamespace(root=root, target_protocol=TARGET_PROTOCOL, script_digests={}, workflow_version="2.7.0")
        answer = protocol_decision.decide(stub, work_item)
    return {
        "phase": scenario.phase, "version": scenario.version, "family": scenario.family,
        "legacy": _shape(legacy, user_only),
        "protocol": {**_shape(answer, user_only), "row": answer.protocol.row,
                     "disposition": answer.protocol.disposition, "action_id": answer.protocol.action_id},
        "expected_difference": scenario.expected,
    }


def difference_of(measured: dict[str, Any]) -> bool:
    """Whether the two decisions differ on a dimension of C.7: the kind,
    the command token of a launch, or the user-only property of a launch."""
    legacy, proto = measured["legacy"], measured["protocol"]
    return any(legacy[key] != proto[key] for key in ("kind", "command", "launches_user_only"))


def derive() -> dict[str, Any]:
    user_only = _user_only_commands()
    scenarios = {scenario.id: measure(scenario, user_only) for scenario in (*SCENARIOS, *_golden_sweep())}
    by_difference: dict[str, list[str]] = {key: [] for key in DIFFERENCES}
    for scenario_id, measured in scenarios.items():
        measured["differs"] = difference_of(measured)
        if measured["expected_difference"] is not None:
            by_difference[measured["expected_difference"]].append(scenario_id)
    return {
        "description": ("Measured differences between the Workflow's next-action (protocol mode) and "
                        "controller.evidence.decide (legacy mode, Workflow 2.6.0) on the dimensions of plan C.7: "
                        "decision kind, launch command token, a launch of a user-only command. "
                        "tests/golden/generate_protocol_vs_legacy_differences.py."),
        "differences": {key: {"class": DIFFERENCES[key], "scenarios": sorted(by_difference[key])}
                        for key in DIFFERENCES},
        "scenarios": scenarios,
        "excluded": EXCLUDED,
    }


def render() -> str:
    return json.dumps(derive(), sort_keys=True, indent=2) + "\n"


def refusal_of_excluded_case(case: str) -> str:
    """The Workflow's own refusal code for an excluded golden case, run for
    real: ``state_invalid`` for each of :data:`EXCLUDED_EXTERNAL_CASES`."""
    setup, overrides = next((s, o) for sid, s, o in external_golden.SCENARIOS if sid == case)
    fields = {key: value for key, value in dict(external_golden._BASE_OVERRIDES, **overrides).items()
              if key in ("implementation_review_stages", "technical_review_block_pins", "plan_revision",
                         "implementation_revision", "reviewed_implementation_head")}
    fields["technical_review_block_pins"] = list(fields["technical_review_block_pins"])
    with TemporaryDirectory() as tmp:
        root = build(tmp, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.1", **fields)
        fixtures.install_workflow_release(root, "2.7.0")
        try:
            protocol.next_action(root, WID)
        except WorkflowProtocolRefusedError as exc:
            return exc.refusal["code"]
    return "answered"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = GOLDEN_PATH.read_text() if GOLDEN_PATH.is_file() else None
        if current == text:
            print(f"{GOLDEN_PATH} is current")
            return 0
        print(f"{GOLDEN_PATH} differs from a fresh derivation", file=sys.stderr)
        return 1
    GOLDEN_PATH.write_text(text)
    document = json.loads(text)
    print(f"wrote {GOLDEN_PATH} ({len(document['scenarios'])} scenarios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
