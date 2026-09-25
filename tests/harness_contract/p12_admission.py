"""Admission and gate for the P12 captures (plan revision 16, CP1).

P12 measures a ``ScheduleWakeup {stop: true}`` issued from inside a wakeup's
fire turn, in two probes (``capture.py``'s ``p12_stop_inside_fire_single``
and ``p12_stop_inside_fire_nested``). Two separate decisions are made about
a capture, and this module keeps them apart:

:func:`admit`
    Did the *model* do what the prompt asked? ``RECAPTURE`` (the capture is
    discarded, its reasons recorded in ``README.md``, and the probe is run
    again) or ``ADOPT`` (the capture becomes the fixture and is never run
    again). It reads only what the model controls -- its own ``tool_use``
    calls with their names and inputs, turn 1's reply text, and, after
    turn 1, only whether each ``text`` block is exactly ``PROBE-EXTRA`` --
    plus one harness fact, used only to find the *cut point*: the
    ``is_error`` flag of a ``tool_result`` answering an *exact named* call.
    It never reads a ``command_lifecycle`` event, any other ``user`` event,
    a ``tool_result``'s content or ``tool_use_result``, a
    ``cancelledWakeups``, an exit status, or a turn boundary after turn 1.

:func:`gate_deviations`
    Every way an adopted capture differs from the contract the plan's
    settlement rule relies on. An empty list is the only pass; anything
    else is a ``/request-plan-amendment`` trigger, never a reason to
    recapture.

Pure: both functions take the parsed event list of one capture and touch
no file, process or clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ADOPT = "ADOPT"
RECAPTURE = "RECAPTURE"

SINGLE = "p12_stop_inside_fire_single"
NESTED = "p12_stop_inside_fire_nested"
PROBES = (SINGLE, NESTED)

WAITING = "WAITING-ALPHA"
DONE = "PROBE-DONE"
EXTRA = "PROBE-EXTRA"

TOOL = "ScheduleWakeup"
ALPHA = {"delaySeconds": 60, "prompt": "P12 fire ALPHA", "reason": "contract probe alpha", "noop": False}
BRAVO = {"delaySeconds": 60, "prompt": "P12 fire BRAVO", "reason": "contract probe bravo", "noop": False}
STOP = {"stop": True}
NAMED_LABELS = (("ALPHA", ALPHA), ("BRAVO", BRAVO), ("stop", STOP))

#: The named sequence each probe asks for, in order.
NAMED_SEQUENCE = {SINGLE: (ALPHA, STOP), NESTED: (ALPHA, BRAVO, STOP)}
#: The ``cancelledWakeups`` the plan's settlement rule assumes (B, "A stop
#: inside a bracket"): the wakeup being fired is no longer held.
EXPECTED_CANCELLED = {SINGLE: 0, NESTED: 1}

#: The non-structural event kinds the replay comparison drops (plan CP1,
#: "Contract tests"). They depend on the model, the account and the
#: machine; the gate ignores them after ``completed(X)`` as well.
DROPPED_KINDS = frozenset({
    ("system", "thinking_tokens"), ("rate_limit_event", None), ("system", "task_progress"),
    ("tool_progress", None), ("system", "commands_changed"), ("system", "vcs_state_changed"),
})
LIFECYCLE_FIELDS = frozenset({"type", "command_uuid", "state", "uuid", "session_id"})


def kind(event: dict) -> tuple:
    return (event.get("type"), event.get("subtype"))


def _is_turn_event(event: dict) -> bool:
    return kind(event) == ("system", "init") or event.get("type") in ("assistant", "user", "result")


def _content(event: dict) -> list:
    content = (event.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def _is_model_event(event: dict) -> bool:
    """An ``assistant`` event of the session's own model (a subagent's
    events carry a ``parent_tool_use_id``)."""
    return event.get("type") == "assistant" and not event.get("parent_tool_use_id")


def label(name: str, tool_input) -> str | None:
    """The named call ``(name, tool_input)`` is exactly, or ``None``."""
    if name != TOOL:
        return None
    for text, args in NAMED_LABELS:
        if tool_input == args and all(type(tool_input[k]) is type(v) for k, v in args.items()):
            return text
    return None


def _named_args(text: str) -> dict:
    return dict(NAMED_LABELS)[text]


def _label_of(args: dict) -> str:
    return next(text for text, named in NAMED_LABELS if named is args)


@dataclass
class Call:
    index: int  # event index in the capture
    id: str
    name: str
    input: object
    turn1: bool

    @property
    def label(self) -> str | None:
        return label(self.name, self.input)

    def describe(self) -> str:
        return f"{self.name} {self.input!r} (event {self.index})"


@dataclass
class Text:
    index: int
    text: str
    turn1: bool


@dataclass
class _Actions:
    calls: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    model_events_after_turn1: int = 0
    turn1_end: int | None = None  # index of the first ``result``


def _model_actions(events: list) -> _Actions:
    actions = _Actions()
    for index, event in enumerate(events):
        if event.get("type") == "result" and actions.turn1_end is None:
            actions.turn1_end = index
    for index, event in enumerate(events):
        if not _is_model_event(event):
            continue
        turn1 = actions.turn1_end is None or index < actions.turn1_end
        if not turn1:
            actions.model_events_after_turn1 += 1
        for item in _content(event):
            if item.get("type") == "tool_use":
                actions.calls.append(Call(index, item.get("id"), item.get("name"), item.get("input"), turn1))
            elif item.get("type") == "text":
                actions.texts.append(Text(index, item.get("text") or "", turn1))
    return actions


def _results_by_id(events: list) -> dict:
    """``tool_use_id`` -> ``(event index, tool_result item, event)``."""
    results = {}
    for index, event in enumerate(events):
        if event.get("type") != "user":
            continue
        for item in _content(event):
            if item.get("type") == "tool_result" and item.get("tool_use_id") not in results:
                results[item.get("tool_use_id")] = (index, item, event)
    return results


def _cut_point(probe_name: str, calls: list, events: list) -> tuple[int | None, str | None]:
    """``(position in calls, why)`` of the cut, or ``(None, None)``.

    The first of: the call after which the calls so far *equal* the named
    sequence exactly; an exact named call that a ``tool_result`` answers
    with ``is_error: true``. The only harness fact read is that flag, and
    only for exact named calls."""
    named = list(NAMED_SEQUENCE[probe_name])
    exact_ids = {call.id for call in calls if call.label is not None}
    rejected = set()
    for event in events:
        if event.get("type") != "user":
            continue
        for item in _content(event):
            if (item.get("type") == "tool_result" and item.get("tool_use_id") in exact_ids
                    and item.get("is_error") is True):
                rejected.add(item.get("tool_use_id"))
    named_labels = [_label_of(args) for args in named]
    for position, call in enumerate(calls):
        if [c.label for c in calls[:position + 1]] == named_labels:
            return position, "named sequence complete"
        if call.label is not None and call.id in rejected:
            return position, f"exact {call.label} call answered is_error"
    return None, None


@dataclass
class Admission:
    verdict: str
    reasons: list
    cut: int | None = None
    cut_reason: str | None = None


def admit(probe_name: str, events: list) -> Admission:
    """Judge the model's actions, up to the cut point, against the prompt."""
    if probe_name not in NAMED_SEQUENCE:
        raise ValueError(f"not a P12 probe: {probe_name!r}")
    named = list(NAMED_SEQUENCE[probe_name])
    actions = _model_actions(events)
    cut, cut_reason = _cut_point(probe_name, actions.calls, events)
    cut_index = actions.calls[cut].index if cut is not None else None
    admitted_calls = actions.calls if cut is None else actions.calls[:cut + 1]

    def before_cut(text: Text) -> bool:
        # A text block in the cut call's own event precedes it only if it
        # comes first in that event; assistant events carry one block each
        # in every measured capture, so ``<`` is exact.
        return cut_index is None or text.index < cut_index

    reasons = []
    turn1_calls = [c for c in admitted_calls if c.turn1]
    if [c.label for c in turn1_calls] != ["ALPHA"]:
        reasons.append("turn 1's calls are not exactly one ScheduleWakeup call with ALPHA's named "
                       "arguments: " + ("; ".join(c.describe() for c in turn1_calls) or "no call"))
    if any(DONE in t.text for t in actions.texts if t.turn1 and before_cut(t)):
        reasons.append(f"turn 1's reply holds {DONE}")
    if actions.model_events_after_turn1:
        later = [c for c in admitted_calls if not c.turn1]
        rest = named[1:]
        later_args = [_named_args(c.label) if c.label else None for c in later]
        if cut is not None:
            ok = later_args == rest[:len(later)]
        else:
            ok = later_args == rest and len(later) == len(rest)
            if not ok and not [c for c in actions.calls if not c.turn1]:
                extra_texts = [t for t in actions.texts if not t.turn1]
                ok = bool(extra_texts) and all(t.text.strip() == EXTRA for t in extra_texts)
        if not ok:
            reasons.append("the model's calls after turn 1" + (" up to the cut point" if cut is not None else "")
                           + " are not " + ("a prefix of" if cut is not None else "")
                           + " the rest of the named sequence: "
                           + ("; ".join(c.describe() for c in later) or "no call")
                           + ("" if cut is not None else ", and the replies after turn 1 are not only "
                              f"{EXTRA}"))
    return Admission(RECAPTURE if reasons else ADOPT, reasons, cut, cut_reason)


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------


def _describe_event(index: int, event: dict) -> str:
    return f"event {index} {kind(event)}"


def gate_deviations(probe_name: str, events: list) -> list[str]:
    """Every way an adopted P12 capture differs from the plan's contract.

    An empty list is the only pass. Pinned by the plan's CP1 contract-test
    sub-bullet: exactly one ``command_lifecycle`` pair enclosing exactly one
    turn; no turn event outside turn 1 before ``started(X)``; the fire turn
    has no opening ``user`` event and its ``result`` no ``origin``; the
    stop's ``tool_use`` and its successful ``tool_result`` (``stopped:
    true``, an integer ``cancelledWakeups`` equal to the expected count)
    inside that turn; in the nested probe BRAVO's successful scheduling pair
    in the same turn before the stop; the model's calls exactly the named
    sequence; and after ``completed(X)`` nothing but the six non-structural
    kinds."""
    if probe_name not in NAMED_SEQUENCE:
        raise ValueError(f"not a P12 probe: {probe_name!r}")
    deviations: list[str] = []
    actions = _model_actions(events)
    results = _results_by_id(events)
    turn1_end = actions.turn1_end

    # The model's calls, against the named sequence, and each answer.
    named = list(NAMED_SEQUENCE[probe_name])
    for position, call in enumerate(actions.calls):
        expected = named[position] if position < len(named) else None
        if call.name != TOOL or call.input != expected or call.label is None:
            previous = actions.calls[position - 1] if position else None
            answer = ""
            if previous is not None and previous.id in results:
                _, item, event = results[previous.id]
                answer = (f", after {previous.describe()} was answered "
                          f"is_error={item.get('is_error')!r} tool_use_result="
                          f"{event.get('tool_use_result')!r}")
            deviations.append(f"call {position + 1} is {call.describe()}, not the named sequence's "
                              f"{('call ' + repr(expected)) if expected is not None else 'end'}{answer}")
    if len(actions.calls) < len(named):
        deviations.append(f"the model made {len(actions.calls)} call(s); the named sequence has {len(named)}")
    for call in actions.calls:
        if call.label is None:
            continue
        if call.id not in results:
            deviations.append(f"no tool_result answers the {call.label} call {call.describe()}")
            continue
        _, item, event = results[call.id]
        if item.get("is_error") is True:
            deviations.append(f"the {call.label} call {call.describe()} was answered is_error: "
                              f"{item.get('content')!r}")

    # Turn 1: ALPHA's pair.
    alpha = [c for c in actions.calls if c.label == "ALPHA" and c.turn1]
    if not alpha:
        deviations.append("turn 1 holds no exact ALPHA call")

    # The bracket.
    lifecycle = [(i, e) for i, e in enumerate(events) if e.get("type") == "command_lifecycle"]
    for index, event in lifecycle:
        if set(event) != LIFECYCLE_FIELDS or event.get("state") not in ("started", "completed"):
            deviations.append(f"malformed command_lifecycle at {_describe_event(index, event)}: {event!r}")
    if not lifecycle:
        deviations.append("no command_lifecycle event: ALPHA's fire was never bracketed "
                          "(missing or unidentified fire)")
        _report_after_turn1(events, turn1_end, deviations)
        return deviations
    if len(lifecycle) != 2:
        deviations.append(f"{len(lifecycle)} command_lifecycle events, not exactly one started/completed pair: "
                          + "; ".join(f"{i} {e.get('state')} {e.get('command_uuid')}" for i, e in lifecycle))
    start_index, start = lifecycle[0]
    if start.get("state") != "started":
        deviations.append(f"the first command_lifecycle event (event {start_index}) is {start.get('state')!r}, "
                          "not started")
    uuid = start.get("command_uuid")
    completed = [(i, e) for i, e in lifecycle[1:]
                 if e.get("state") == "completed" and e.get("command_uuid") == uuid]
    if not completed:
        deviations.append(f"no completed event for command_uuid {uuid!r}")
        end_index = len(events)
    else:
        end_index = completed[0][0]
    for index, event in lifecycle[1:]:
        if (index, event) != (completed[0] if completed else None):
            deviations.append(f"extra command_lifecycle event {index}: {event.get('state')} "
                              f"{event.get('command_uuid')}")

    # Before started(X): nothing but turn 1.
    for index in range(0, start_index):
        event = events[index]
        if turn1_end is not None and index > turn1_end and _is_turn_event(event):
            deviations.append(f"a turn event before started(X) outside turn 1: {_describe_event(index, event)}"
                              + (f" (reply {EXTRA})" if _is_model_event(event)
                                 and any((i.get('text') or '').strip() == EXTRA for i in _content(event)) else ""))

    # Inside the bracket: exactly one turn.
    inside = [(i, events[i]) for i in range(start_index + 1, end_index)
              if kind(events[i]) not in DROPPED_KINDS]
    turn_results = [(i, e) for i, e in inside if e.get("type") == "result"]
    inits = [(i, e) for i, e in inside if kind(e) == ("system", "init")]
    if len(turn_results) != 1 or len(inits) != 1:
        deviations.append(f"the bracket encloses {len(turn_results)} result(s) and {len(inits)} system/init "
                          "event(s), not exactly one turn")
    if inside and kind(inside[0][1]) != ("system", "init"):
        deviations.append(f"the bracketed turn does not open with system/init: {_describe_event(*inside[0])}")
    after_init = [(i, e) for i, e in inside if kind(e) != ("system", "init")]
    if after_init and after_init[0][1].get("type") != "assistant":
        deviations.append(f"the bracketed turn opens with {_describe_event(*after_init[0])}, not an "
                          "assistant event")
    for index, event in turn_results:
        if "origin" in event:
            deviations.append(f"the bracketed turn's result (event {index}) has an origin: {event['origin']!r}")
    if turn_results and turn_results[-1][0] != inside[-1][0]:
        deviations.append(f"events follow the bracketed turn's result inside the bracket: "
                          + "; ".join(_describe_event(i, e) for i, e in inside if i > turn_results[-1][0]))
    for index, event in inside:
        if event.get("type") not in ("system", "assistant", "user", "result"):
            deviations.append(f"unexpected event inside the bracket: {_describe_event(index, event)}")
        if event.get("type") == "user":
            for item in _content(event):
                issued = {c.id for c in actions.calls if start_index < c.index < index}
                if item.get("type") != "tool_result" or item.get("tool_use_id") not in issued:
                    deviations.append(f"a user event inside the bracket is not a tool_result for a call "
                                      f"issued in the bracketed turn: event {index}")

    def in_bracket(i: int) -> bool:
        return start_index < i < end_index

    # The stop, and BRAVO, inside that turn.
    stops = [c for c in actions.calls if c.label == "stop"]
    if not stops:
        deviations.append("the bracketed turn holds no stop call" if actions.model_events_after_turn1
                          else "no stop call")
    else:
        stop = stops[0]
        if not in_bracket(stop.index):
            deviations.append(f"the stop call {stop.describe()} is outside the bracket")
        if stop.id in results:
            r_index, item, event = results[stop.id]
            if not in_bracket(r_index):
                deviations.append(f"the stop's tool_result (event {r_index}) is outside the bracket")
            result = event.get("tool_use_result")
            if not isinstance(result, dict):
                deviations.append(f"the stop's tool_result has no tool_use_result object: {result!r}")
            else:
                if result.get("stopped") is not True:
                    deviations.append(f"the stop's tool_use_result.stopped is {result.get('stopped')!r}")
                count = result.get("cancelledWakeups", "<missing>")
                if type(count) is not int:
                    deviations.append(f"the stop's cancelledWakeups is not an integer: {count!r}")
                elif count != EXPECTED_CANCELLED[probe_name]:
                    deviations.append(f"the stop's cancelledWakeups is {count}, the settlement rule expects "
                                      f"{EXPECTED_CANCELLED[probe_name]}")
    if probe_name == NESTED:
        bravos = [c for c in actions.calls if c.label == "BRAVO"]
        if not bravos:
            deviations.append("the bracketed turn holds no BRAVO call")
        else:
            bravo = bravos[0]
            if not in_bracket(bravo.index):
                deviations.append(f"the BRAVO call {bravo.describe()} is outside the bracket")
            if stops and bravo.index > stops[0].index:
                deviations.append("BRAVO is scheduled after the stop")
            if bravo.id in results and not in_bracket(results[bravo.id][0]):
                deviations.append(f"BRAVO's tool_result (event {results[bravo.id][0]}) is outside the bracket")

    # After completed(X): only the six non-structural kinds.
    for index in range(end_index + 1, len(events)):
        event = events[index]
        if kind(event) not in DROPPED_KINDS:
            deviations.append(f"an event after completed(X): {_describe_event(index, event)}"
                              + (f" command_uuid {event.get('command_uuid')}"
                                 if event.get("type") == "command_lifecycle" else ""))
    return deviations


def _report_after_turn1(events: list, turn1_end: int | None, deviations: list) -> None:
    """With no bracket at all: name every turn after turn 1 (an unasked or
    unidentified turn)."""
    if turn1_end is None:
        deviations.append("turn 1 never ended (no result event)")
        return
    for index in range(turn1_end + 1, len(events)):
        event = events[index]
        if _is_turn_event(event):
            deviations.append(f"an unbracketed turn event after turn 1: {_describe_event(index, event)}")
