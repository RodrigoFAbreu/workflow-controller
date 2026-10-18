"""How the external review runs (``workflow-controller-review-modes`` D1-D4, D6).

Pure: no I/O, no clock. The Controller's choice at an ``external_gate`` of
row 14 or 28 is made here from the owner's ``review.mode``, the configured
``reviewer.harness`` and the Workflow's lowered-policy signal; the caller
(:mod:`controller.job`) reads the signal from ``verify`` and acts on the
:class:`Choice`. The Workflow still decides whether a verdict is acceptable
(the ingest of the verdict runs every guard); nothing here does.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Mapping

from controller import harness
from controller.settings import REVIEW_MODES

MODE_AUTO, MODE_CROSS_MODEL, MODE_SINGLE_PROVIDER, MODE_MANUAL = REVIEW_MODES

#: The stop's gate code (D4, D6).
REVIEW_MODE_UNAVAILABLE = "review_mode_unavailable"

#: The family of the Controller's workers (Claude is the only worker harness).
WORKER_FAMILY = "anthropic"

#: The verdict family of each review harness (``review.HARNESSES``; a test
#: pins this table to the registry). A harness not listed is a distinct,
#: unknown provider here: the launch refuses it as it always has.
HARNESS_FAMILY: Mapping[str, str] = harness.REVIEWER_FAMILY

#: The harness a same-provider review runs on.
SAME_PROVIDER_HARNESS = "claude"

#: The ``gate_policy`` check field a stage's lowered ``require`` is named by.
STAGE_FIELD = {"plan": "plan_approval.require", "implementation": "technical_approval.require"}

CHECK_ID = "gate_policy"

# D3's allow-list: the only clauses of the check's detail that leave the
# signal standing. Every other clause (``file_tightened``, ``floor``, the
# pending-floor clause, ``file_loosening_ignored``, an invalid file, anything
# a later Workflow adds) cancels it for both stages.
_LOWERING = re.compile(
    r"gate-lowering event: the adoption [0-9a-f]+ \(commit [0-9a-f]+\) lowers "
    r"(?P<fields>[a-z_]+\.[a-z_]+(?:, [a-z_]+\.[a-z_]+)*)")
_LABEL = re.compile(r"the adoption's recorded lowered label .+ disagrees with the recomputed lowering .+")
_UNREFERENCED = re.compile(
    r"\d+ review verdict\(s\) were recorded without a run_ref, so the producing run cannot be traced \(.*\)")


@dataclasses.dataclass(frozen=True)
class Signal:
    """What the ``gate_policy`` check says (D3): ``status`` as reported,
    ``lowered`` the fields of its lowering clause (empty when there is none),
    ``label_clause`` whether the label-disagreement clause is present and
    ``other`` whether any clause outside the allow-list is."""

    status: str
    lowered: tuple[str, ...]
    label_clause: bool
    other: bool

    def holds(self, stage: str) -> bool:
        """Whether the Workflow reports ``stage``'s ``require`` lowered and
        nothing else (``warn`` only: ``fail`` still carries the lowering
        clause of an invalid file whose policy ``effective_policy`` keeps
        stricter)."""
        return self.status == "warn" and not self.other and STAGE_FIELD[stage] in self.lowered

    def key(self, stage: str) -> tuple:
        """D6's signal key: the status, the allow-listed parts and the
        "some other clause present" flag, never the whole detail."""
        return (self.status, STAGE_FIELD[stage] in self.lowered, self.label_clause, self.other)


def read_signal(status: str, detail: str) -> Signal:
    """The :class:`Signal` of one ``gate_policy`` check (``status``,
    ``detail``). A clause is whatever lies between ``"; "`` separators, so a
    clause that itself contains one is read as several, none of which is on
    the allow-list: fail closed."""
    lowered: list[str] = []
    label = other = False
    for clause in detail.split("; "):
        matched = _LOWERING.fullmatch(clause)
        if matched:
            lowered.extend(matched["fields"].split(", "))
        elif _LABEL.fullmatch(clause):
            label = True
        elif not _UNREFERENCED.fullmatch(clause):
            other = True
    if status == "pass":  # nothing is lowered; the detail is a sentence of its own
        return Signal(status, (), False, False)
    return Signal(status, tuple(lowered), label, other)


def signal_of_checks(checks: Any) -> Signal | None:
    """The signal of a ``verify`` answer's checks (``id``/``status``/``detail``
    attributes), or ``None`` when it has no ``gate_policy`` check."""
    for check in checks:
        if check.id == CHECK_ID:
            return read_signal(check.status, check.detail)
    return None


def harness_family(name: str) -> str | None:
    return HARNESS_FAMILY.get(name)


def distinct_harness(reviewer: Mapping | None) -> str | None:
    """The configured ``reviewer.harness`` when it is a distinct provider
    (D2, D4): set, and not of the workers' family. A same-family harness
    (``claude``) reads as unset here."""
    if reviewer is None:
        return None
    name = reviewer["harness"]
    return None if harness_family(name) == WORKER_FAMILY else name


@dataclasses.dataclass(frozen=True)
class Choice:
    """What the step does at the gate: ``kind`` ``"gate"`` (the ordinary
    gate, as today), ``"review"`` (run ``harness``; ``lowered`` for a
    same-provider review) or ``"refused"`` (the ``review_mode_unavailable``
    stop, ``reason`` its text before the remedies). ``signal`` is what
    was read, for D6's key; ``None`` when it was not needed."""

    kind: str
    harness: str | None = None
    lowered: bool = False
    reason: str = ""
    signal: Signal | None = None


GATE = Choice("gate")


def _refuse(reason: str, signal: Signal | None) -> Choice:
    return Choice("refused", reason=reason, signal=signal)


def needs_signal(mode: str, reviewer: Mapping | None) -> bool:
    """Whether :func:`choose` reads the lowered-policy signal: only where a
    same-provider review could run (``auto`` with no distinct harness, and
    an explicit ``single-provider``). ``manual``, ``cross-model`` and ``auto``
    with a distinct harness never ask ``verify`` for it."""
    if mode == MODE_SINGLE_PROVIDER:
        return True
    return mode == MODE_AUTO and distinct_harness(reviewer) is None


def choose(mode: str, reviewer: Mapping | None, stage: str, signal: Signal | None) -> Choice:
    """The choice at an ``external_gate`` of ``stage`` (D1-D4). ``reviewer``
    is the ``reviewer.*`` options (``None`` while ``reviewer.harness`` is
    unset); ``signal`` is the ``gate_policy`` check's, ``None`` when it is
    absent or could not be read (no signal)."""
    if mode == MODE_MANUAL:
        return GATE
    distinct = distinct_harness(reviewer)
    if mode == MODE_CROSS_MODEL:
        if distinct is not None:
            return Choice("review", harness=distinct)
        set_to = None if reviewer is None else reviewer["harness"]
        missing = ("reviewer.harness is unset" if set_to is None
                   else f"reviewer.harness is {set_to!r}, the workers' own provider")
        return _refuse(f"review.mode is cross-model, but {missing}: there is no distinct provider to ask. "
                       "Set reviewer.harness to a distinct provider's harness (codex), or choose "
                       "--review-mode manual (or the review.mode setting)", None)
    if mode == MODE_AUTO and distinct is not None:
        return Choice("review", harness=distinct)
    lowered = signal is not None and signal.holds(stage)
    if lowered:
        return Choice("review", harness=SAME_PROVIDER_HARNESS, lowered=True, signal=signal)
    if mode == MODE_AUTO:
        return Choice("gate", signal=signal)
    return _refuse(
        f"review.mode is single-provider, but the Workflow does not report the {stage} stage's "
        f"{STAGE_FIELD[stage]} lowered (a same-provider approval is refused at APPROVE unless the "
        f"policy lowered distinct_reviewer_models). {remedies(mode, stage)}", signal)


#: The ``GATE_POLICY.json`` gate each stage's approval is held by.
STAGE_GATE = {"plan": "plan_approval", "implementation": "technical_approval"}


def remedies(mode: str, stage: str) -> str:
    """The ways out of a same-provider refusal (D6), each with what the
    Controller does after it, naming only those that act for ``mode``. Path 2
    (a human gate) releases ``auto`` only: an explicit ``single-provider``
    finds the signal cancelled and is refused again, so it names
    ``--review-mode manual`` there."""
    field, gate = STAGE_FIELD[stage], STAGE_GATE[stage]
    paths = [
        f"(1) lower the {stage} stage's {field} by adopting a policy (docs/ai-workflow/GATE_POLICY.md): the signal "
        "changes and the next step runs the review; an open review bundle is not changed by it, so withdraw the "
        "bundle and generate it again first"]
    if mode == MODE_AUTO:
        paths.append(
            f"(2) turn the {gate} gate human with an uncommitted edit of docs/ai-workflow/GATE_POLICY.json: it "
            "takes effect at once and the next step stops at the ordinary gate for a person")
    third = ("record a verdict from a distinct model by hand through the Workflow's external-result ingest, or run with "
             "--review-mode manual (or set review.mode)" if mode == MODE_SINGLE_PROVIDER else
             "record a verdict from a distinct model by hand through the Workflow's external-result ingest")
    paths.append(f"(3) {third}, or turn on a distinct provider (reviewer.harness, or "
                 "--review-mode cross-model), which runs that review")
    return "Ways out: " + "; ".join(paths) + "."


def distinct_refusal_message(mode: str, stage: str, detail: str) -> str:
    """The text of a ``DistinctReviewerModelsRequiredError`` ending a
    same-provider review (D6): the Workflow's own sentence, then
    :func:`remedies`. ``detail`` is the Workflow's message."""
    return f"{detail} The next step stops without launching while the state and the policy signal are unchanged. " \
           + remedies(mode, stage)
