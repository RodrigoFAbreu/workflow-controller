"""The measured harness contract, pinned from the committed fixtures, and the
fake harness held to it (`workflow-controller-worker-lifecycle-ownership`
CP1).

- :class:`FixtureDigestTest`: every fixture's SHA-256 equals the one
  ``tests/harness_contract/README.md`` records.
- :class:`RecogniserEvidenceTest`: each fact the wakeup-fire recogniser
  (plan decisions 12/13) rests on, read from the fixture files alone.
- :class:`P12FixtureTest` / :class:`P12AdmissionTest`: the adopted P12
  captures pass the gate, and ``p12_admission`` separates the model's
  mistakes (``RECAPTURE``) from harness observations (``ADOPT`` with a
  non-empty ``gate_deviations``, the plan-amendment trigger).
- :class:`FixtureReplayTest`: each fixture, translated into a
  ``FAKE_CLAUDE_TURNS`` script and played by ``tests/fake_claude.py``'s
  streaming-input mode, reproduces the fixture's projected event sequence.
- :class:`FakeStreamingModeTest`: the fake's own streaming behaviours
  (existing variables as one turn, EOF handling, descriptor closing, the
  fire bracket and its faults, the stop count, orphans).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT_DIR = HERE / "harness_contract"
FAKE_CLAUDE = HERE / "fake_claude.py"
sys.path.insert(0, str(CONTRACT_DIR))
import p12_admission as p12  # noqa: E402

DROPPED = p12.DROPPED_KINDS

#: Every fixture CP1 commits: the seven imported job streams, P1-P6 and
#: P8-P11 (amendment 0's capture), and P12's two.
FIXTURES = (
    "job_12a9f268_applying_review_feedback", "job_2857a730_two_results", "job_5d4a976a_implementing",
    "job_6082a90b_self_reviewing", "job_66988e17_self_reviewing", "job_8b244f42_implementing",
    "job_cb43fe49_self_reviewing",
    "p1_print_mode_background_bash", "p2_escaped_descendants", "p2_escaped_descendants_subreaper",
    "p3_streaming_background_bash", "p4_monitor", "p5_p11_wakeup_fires", "p6_p10_slash_command",
    "p8_task_stop", "p8_monitor_timeout", "p9_wakeup_cancel", "p11_subagent_handback",
    "p12_stop_inside_fire_single", "p12_stop_inside_fire_nested",
)
#: Outside the fake's model (README, "Replay through the fake harness").
NOT_REPLAYED = ("job_2857a730_two_results",)


def load(name: str) -> list[dict]:
    with open(CONTRACT_DIR / f"{name}.jsonl") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def kind(event: dict) -> tuple:
    return (event.get("type"), event.get("subtype"))


def content(event: dict) -> list:
    value = (event.get("message") or {}).get("content")
    return value if isinstance(value, list) else []


def projection(events: list[dict]) -> list[tuple]:
    """The plan's fixed projection: ``(type, subtype)``, plus ``state`` for
    ``command_lifecycle`` and ``is_error`` for a ``user`` ``tool_result``,
    with the six non-structural kinds dropped."""
    out = []
    for event in events:
        if kind(event) in DROPPED:
            continue
        item = kind(event)
        if event.get("type") == "command_lifecycle":
            item += (event.get("state"),)
        elif event.get("type") == "user":
            results = [c for c in content(event) if c.get("type") == "tool_result"]
            item += (tuple(r.get("is_error") for r in results),)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Digests.
# ---------------------------------------------------------------------------


def readme_digests() -> dict[str, str]:
    text = (CONTRACT_DIR / "README.md").read_text()
    return dict(re.findall(r"^\| `([^`]+\.(?:jsonl|meta\.json))` \| `([0-9a-f]{64})` \|$", text, re.M))


class FixtureDigestTest(unittest.TestCase):
    def test_every_fixture_file_matches_its_recorded_digest(self):
        digests = readme_digests()
        expected_files = {f"{n}.jsonl" for n in FIXTURES} | {f"{n}.meta.json" for n in FIXTURES}
        self.assertEqual(set(digests), expected_files)
        on_disk = {p.name for p in CONTRACT_DIR.iterdir() if p.name.endswith((".jsonl", ".meta.json"))}
        self.assertEqual(on_disk, expected_files)
        for name, digest in sorted(digests.items()):
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((CONTRACT_DIR / name).read_bytes()).hexdigest(), digest)


# ---------------------------------------------------------------------------
# The recogniser's evidence (amendment 0).
# ---------------------------------------------------------------------------


def brackets(events: list[dict]) -> list[tuple[int, int, str]]:
    """``(started index, completed index, command_uuid)`` for each pair."""
    open_at: dict[str, int] = {}
    pairs = []
    for index, event in enumerate(events):
        if event.get("type") != "command_lifecycle":
            continue
        if event["state"] == "started":
            open_at[event["command_uuid"]] = index
        elif event["state"] == "completed":
            pairs.append((open_at.pop(event["command_uuid"]), index, event["command_uuid"]))
    return pairs


class RecogniserEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.p5 = load("p5_p11_wakeup_fires")

    def test_p5_holds_two_distinct_pairs_each_around_one_turn(self):
        lifecycle = [e for e in self.p5 if e.get("type") == "command_lifecycle"]
        self.assertEqual(len(lifecycle), 4)
        for event in lifecycle:
            self.assertEqual(set(event), {"type", "command_uuid", "state", "uuid", "session_id"})
        pairs = brackets(self.p5)
        self.assertEqual([(s, c) for s, c, _ in pairs], [(13, 24), (25, 32)])
        self.assertNotEqual(pairs[0][2], pairs[1][2])
        for start, end, _ in pairs:
            inside = self.p5[start + 1:end]
            self.assertEqual(sum(1 for e in inside if e.get("type") == "result"), 1)
            self.assertEqual(sum(1 for e in inside if kind(e) == ("system", "init")), 1)
            self.assertEqual(inside[-1]["type"], "result")
            self.assertNotIn("origin", inside[-1])
            # No opening user event: nothing but system events before the
            # first assistant event.
            first_assistant = next(i for i, e in enumerate(inside) if e.get("type") == "assistant")
            self.assertTrue(all(e.get("type") == "system" for e in inside[:first_assistant]))

    def test_every_user_event_inside_a_bracket_answers_a_call_of_that_turn(self):
        users = []
        for start, end, _ in brackets(self.p5):
            issued = set()
            for index in range(start + 1, end):
                event = self.p5[index]
                for item in content(event):
                    if event.get("type") == "assistant" and item.get("type") == "tool_use":
                        issued.add(item["id"])
                    if event.get("type") == "user":
                        self.assertEqual(item.get("type"), "tool_result")
                        self.assertIn(item["tool_use_id"], issued)
                        users.append(index)
        self.assertEqual(users, [19])
        self.assertEqual(content(self.p5[19])[0]["tool_use_id"], content(self.p5[18])[0]["id"])

    def test_the_wakeup_prompts_occur_only_in_their_scheduling_inputs(self):
        lines = (CONTRACT_DIR / "p5_p11_wakeup_fires.jsonl").read_text().splitlines()
        for prompt, scheduling_line in (("P11 fire ALPHA", 5), ("P11 fire BRAVO second", 18)):
            holders = [i for i, line in enumerate(lines) if prompt in line]
            self.assertEqual(holders, [scheduling_line], prompt)
            self.assertEqual(content(self.p5[scheduling_line])[0]["input"]["prompt"], prompt)
        for event in self.p5:
            if event.get("type") == "user":
                self.assertNotIn("P11 fire", json.dumps(event))

    def test_a_fire_does_not_identify_its_wakeup(self):
        lines = (CONTRACT_DIR / "p5_p11_wakeup_fires.jsonl").read_text().splitlines()
        for call_line, result_line, (start, end) in ((5, 6, (13, 24)), (18, 19, (25, 32))):
            tool_use_id = content(self.p5[call_line])[0]["id"]
            scheduled_for = str(self.p5[result_line]["tool_use_result"]["scheduledFor"])
            self.assertEqual([i for i, line in enumerate(lines) if tool_use_id in line], [call_line, result_line])
            self.assertEqual([i for i, line in enumerate(lines) if scheduled_for in line], [result_line])
            # Neither occurs on any line of the wakeup's own fire bracket.
            for token in (tool_use_id, scheduled_for):
                self.assertFalse([i for i in range(start, end + 1) if token in lines[i]])

    def test_no_other_fixture_holds_a_command_lifecycle_event(self):
        for name in FIXTURES:
            if name in ("p5_p11_wakeup_fires", "p12_stop_inside_fire_single", "p12_stop_inside_fire_nested"):
                continue
            with self.subTest(fixture=name):
                self.assertFalse([e for e in load(name) if e.get("type") == "command_lifecycle"])

    def test_notification_turns_carry_task_notification_origin_and_no_bracket(self):
        for name in ("p3_streaming_background_bash", "p4_monitor", "p8_monitor_timeout", "p11_subagent_handback"):
            events = load(name)
            results = [e for e in events if e.get("type") == "result"]
            self.assertGreater(len(results), 1, name)
            for result in results[1:]:
                with self.subTest(fixture=name):
                    self.assertEqual(result.get("origin"), {"kind": "task-notification"})
            self.assertFalse(brackets(events))

    def test_p9_holds_no_fire_and_no_command_lifecycle_event(self):
        events = load("p9_wakeup_cancel")
        self.assertEqual(sum(1 for e in events if e.get("type") == "result"), 1)
        self.assertEqual(sum(1 for e in events if kind(e) == ("system", "init")), 1)
        self.assertFalse([e for e in events if e.get("type") == "command_lifecycle"])
        stop = [e for e in events if isinstance(e.get("tool_use_result"), dict)
                and e["tool_use_result"].get("stopped")]
        self.assertEqual([e["tool_use_result"]["cancelledWakeups"] for e in stop], [1])


# ---------------------------------------------------------------------------
# P12.
# ---------------------------------------------------------------------------


class P12FixtureTest(unittest.TestCase):
    """The adopted captures pass the gate. A failure here is the
    plan-amendment trigger, never a reason to edit the test or recapture."""

    def test_both_fixtures_are_adopted_and_pass_the_gate(self):
        for name in p12.PROBES:
            with self.subTest(fixture=name):
                events = load(name)
                admission = p12.admit(name, events)
                self.assertEqual(admission.verdict, p12.ADOPT, admission.reasons)
                self.assertEqual(p12.gate_deviations(name, events), [])

    def test_the_measured_counts(self):
        for name, count in (("p12_stop_inside_fire_single", 0), ("p12_stop_inside_fire_nested", 1)):
            events = load(name)
            stops = [e for e in events if isinstance(e.get("tool_use_result"), dict)
                     and e["tool_use_result"].get("stopped") is True]
            self.assertEqual([e["tool_use_result"]["cancelledWakeups"] for e in stops], [count], name)
            (start, end, _), = brackets(events)
            stop_index = events.index(stops[0])
            self.assertTrue(start < stop_index < end)


def _p5_parts() -> dict:
    """Templates cut from ``p5_p11_wakeup_fires``'s own events: turn 1 and
    ALPHA's bracketed fire turn."""
    events = load("p5_p11_wakeup_fires")
    return {
        "turn1_head": events[0:5],   # init, thinking tokens, assistant thinking
        "call": events[5],           # assistant tool_use
        "result": events[6],         # user tool_result
        "after_call": events[7:11],  # rate limit, thinking tokens, assistant thinking
        "text": events[11],          # assistant text
        "turn_result": events[12],   # result
        "started": events[13],
        "fire_head": events[14:18],  # init, thinking tokens, assistant thinking
        "completed": events[24],
        "fire_tail": [events[20], events[21]],  # rate limit, assistant thinking
    }


class Capture:
    """A synthetic P12 capture spliced from p5's events (no file written)."""

    def __init__(self) -> None:
        self.parts = _p5_parts()
        self.events: list[dict] = []
        self.counter = 0

    def _clone(self, event: dict) -> dict:
        event = copy.deepcopy(event)
        event["uuid"] = f"synthetic-{len(self.events)}"
        return event

    def call(self, tool_input, *, name="ScheduleWakeup", answer="ok", is_error=None, cancelled=None):
        """A model call and (unless ``answer`` is ``None``) its answer."""
        self.counter += 1
        call_id = f"toolu_synthetic_{self.counter}"
        event = self._clone(self.parts["call"])
        event["message"]["content"] = [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]
        event["wire_tool_inputs"] = {call_id: tool_input}
        self.events.append(event)
        if answer is None:
            return self
        result = self._clone(self.parts["result"])
        item = {"tool_use_id": call_id, "type": "tool_result", "content": answer}
        if is_error is not None:
            item["is_error"] = is_error
        result["message"]["content"] = [item]
        if is_error:
            result["tool_use_result"] = f"Error: {answer}"
        elif tool_input == p12.STOP:
            result["tool_use_result"] = {"scheduledFor": 0, "clampedDelaySeconds": 0, "wasClamped": False,
                                         "stopped": True, "cancelledWakeups": cancelled}
            if cancelled is None:
                del result["tool_use_result"]["cancelledWakeups"]
        else:
            result["tool_use_result"] = {"scheduledFor": 1790350680000, "clampedDelaySeconds": 60,
                                         "wasClamped": False}
        self.events.append(result)
        return self

    def text(self, text: str | None):
        if text is not None:
            event = self._clone(self.parts["text"])
            event["message"]["content"] = [{"type": "text", "text": text}]
            self.events.append(event)
        return self

    def thinking_only(self):
        self.events.append(self._clone(self.parts["after_call"][3]))
        return self

    def turn_start(self, first=False):
        self.events.extend(self._clone(e) for e in (self.parts["turn1_head"] if first else self.parts["fire_head"]))
        return self

    def turn_end(self):
        result = self._clone(self.parts["turn_result"])
        self.events.append(result)
        return self

    def started(self, command_uuid="11111111-aaaa"):
        event = self._clone(self.parts["started"])
        event["command_uuid"] = command_uuid
        self.events.append(event)
        return self

    def completed(self, command_uuid="11111111-aaaa"):
        event = self._clone(self.parts["completed"])
        event["command_uuid"] = command_uuid
        self.events.append(event)
        return self

    def extra(self, event: dict):
        self.events.append(self._clone(event))
        return self


def turn1(capture: Capture | None = None, *, alpha=None, alpha_error=None, reply=p12.WAITING, extra_calls=()):
    capture = capture or Capture()
    capture.turn_start(first=True)
    capture.call(p12.ALPHA if alpha is None else alpha,
                 answer=alpha_error or "Next wakeup scheduled for 16:38:00 (in 112s).",
                 is_error=True if alpha_error else None)
    for tool_input, is_error in extra_calls:
        capture.call(tool_input, answer="rejected" if is_error else "ok", is_error=is_error)
    capture.text(reply).turn_end()
    return capture


def fire(capture: Capture, calls, *, reply=p12.DONE, uuid="11111111-aaaa", bracket=True, thinking_only=False):
    if bracket:
        capture.started(uuid)
    capture.turn_start()
    for call in calls:
        capture.call(**call) if isinstance(call, dict) else capture.call(call)
    if thinking_only:
        capture.thinking_only()
    capture.text(reply).turn_end()
    if bracket:
        capture.completed(uuid)
    return capture


STOP0 = {"tool_input": p12.STOP, "cancelled": 0}
STOP1 = {"tool_input": p12.STOP, "cancelled": 1}
BRAVO_OK = {"tool_input": p12.BRAVO}


class P12AdmissionTest(unittest.TestCase):
    """Plan CP1's P12 contract-test cases, built in memory from p5's events."""

    def assertAdopted(self, probe, capture, *needles):
        admission = p12.admit(probe, capture.events)
        self.assertEqual(admission.verdict, p12.ADOPT, admission.reasons)
        deviations = p12.gate_deviations(probe, capture.events)
        self.assertTrue(deviations, "an adopted harness observation must reach the gate")
        joined = "\n".join(deviations)
        for needle in needles:
            self.assertIn(needle, joined)
        return deviations

    def assertRecapture(self, probe, capture, needle):
        admission = p12.admit(probe, capture.events)
        self.assertEqual(admission.verdict, p12.RECAPTURE)
        self.assertIn(needle, "\n".join(admission.reasons))
        return admission

    # -- the clean shapes -----------------------------------------------------

    def test_clean_single_and_nested_captures_pass(self):
        single = fire(turn1(), [STOP0])
        self.assertEqual(p12.admit(p12.SINGLE, single.events).verdict, p12.ADOPT)
        self.assertEqual(p12.gate_deviations(p12.SINGLE, single.events), [])
        nested = fire(turn1(), [BRAVO_OK, STOP1])
        self.assertEqual(p12.admit(p12.NESTED, nested.events).verdict, p12.ADOPT)
        self.assertEqual(p12.gate_deviations(p12.NESTED, nested.events), [])

    # -- harness observations: ADOPT, non-empty gate ----------------------------

    def test_no_fire_no_bracket_no_later_turn(self):
        self.assertAdopted(p12.SINGLE, turn1(), "no command_lifecycle event")

    def test_stop_without_a_tool_result(self):
        capture = fire(turn1(), [{"tool_input": p12.STOP, "answer": None}])
        self.assertAdopted(p12.SINGLE, capture, "no tool_result answers the stop call")

    def test_bracket_enclosing_two_turns_the_stop_in_the_second(self):
        capture = turn1()
        capture.started()
        capture.turn_start().text(p12.EXTRA).turn_end()
        capture.turn_start().call(p12.STOP, cancelled=0).text(p12.DONE).turn_end()
        capture.completed()
        self.assertAdopted(p12.SINGLE, capture, "not exactly one turn")

    def test_stop_rejected_on_the_exact_named_arguments(self):
        capture = fire(turn1(), [{"tool_input": p12.STOP, "answer": "no", "is_error": True}])
        self.assertAdopted(p12.SINGLE, capture, "answered is_error")

    def test_cancelled_wakeups_missing_and_one_in_the_single_probe(self):
        self.assertAdopted(p12.SINGLE, fire(turn1(), [{"tool_input": p12.STOP}]), "not an integer")
        self.assertAdopted(p12.SINGLE, fire(turn1(), [STOP1]), "cancelledWakeups is 1")

    def test_a_second_pair_after_completed(self):
        capture = fire(turn1(), [STOP0])
        capture.started("22222222-bbbb").turn_start().text(p12.EXTRA).turn_end().completed("22222222-bbbb")
        self.assertAdopted(p12.SINGLE, capture, "extra command_lifecycle event")

    def test_non_structural_events_after_completed_add_no_deviation(self):
        parts = _p5_parts()
        capture = fire(turn1(), [STOP0])
        capture.extra(parts["after_call"][0]).extra(parts["fire_head"][1])  # rate_limit, thinking_tokens
        self.assertEqual(kind(capture.events[-2]), ("rate_limit_event", None))
        self.assertEqual(kind(capture.events[-1]), ("system", "thinking_tokens"))
        self.assertEqual(p12.gate_deviations(p12.SINGLE, capture.events), [])

    # -- harness observations followed by the model's reaction ------------------

    def test_alpha_rejected_then_retried_with_different_arguments(self):
        capture = turn1(alpha_error="`noop` is required when `stop` is not true.",
                        extra_calls=[({"delaySeconds": 60, "prompt": "P12 fire ALPHA",
                                       "reason": "contract probe alpha"}, False)])
        self.assertAdopted(p12.SINGLE, capture, "ALPHA call", "answered is_error", "call 2 is")

    def test_alpha_rejected_then_reply_holds_probe_done(self):
        capture = turn1(alpha_error="rejected", reply=p12.DONE)
        self.assertAdopted(p12.SINGLE, capture, "answered is_error")

    def test_single_stop_rejected_then_retried(self):
        capture = fire(turn1(), [{"tool_input": p12.STOP, "answer": "no", "is_error": True}, STOP0])
        self.assertAdopted(p12.SINGLE, capture, "answered is_error", "call 3 is")

    def test_nested_bravo_rejected_then_retried_then_stop(self):
        capture = fire(turn1(), [{"tool_input": p12.BRAVO, "answer": "no", "is_error": True}, BRAVO_OK, STOP1])
        self.assertAdopted(p12.NESTED, capture, "BRAVO call", "answered is_error", "call 3 is")

    def test_nested_bravo_rejected_and_no_stop(self):
        capture = fire(turn1(), [{"tool_input": p12.BRAVO, "answer": "no", "is_error": True}])
        self.assertAdopted(p12.NESTED, capture, "answered is_error", "no stop call")

    def test_second_fire_turn_with_another_stop(self):
        capture = fire(turn1(), [STOP0])
        fire(capture, [STOP0], uuid="22222222-bbbb")
        self.assertAdopted(p12.SINGLE, capture, "call 3 is", "extra command_lifecycle event")

    def test_second_fire_turn_with_probe_extra_and_no_call(self):
        capture = fire(turn1(), [STOP0])
        fire(capture, [], reply=p12.EXTRA, uuid="22222222-bbbb")
        self.assertAdopted(p12.SINGLE, capture, "extra command_lifecycle event")

    def test_stated_trade_a_second_stop_after_completion_goes_to_the_gate(self):
        capture = fire(turn1(), [STOP0, STOP0])
        admission = p12.admit(p12.SINGLE, capture.events)
        self.assertEqual(admission.cut_reason, "named sequence complete")
        self.assertAdopted(p12.SINGLE, capture, "call 3 is")

    # -- the PROBE-EXTRA-only case ---------------------------------------------

    def test_unbracketed_probe_extra_turn_and_nothing_after(self):
        capture = turn1()
        capture.turn_start().text(p12.EXTRA).turn_end()
        self.assertAdopted(p12.SINGLE, capture, "unbracketed turn event after turn 1", "no command_lifecycle")

    def test_bracketed_fire_answered_probe_extra(self):
        capture = fire(turn1(), [], reply=p12.EXTRA)
        self.assertAdopted(p12.SINGLE, capture, "holds no stop call")

    def test_unasked_probe_extra_turn_then_the_fire_and_the_stop(self):
        capture = turn1()
        capture.turn_start().text(p12.EXTRA).turn_end()
        fire(capture, [STOP0])
        self.assertAdopted(p12.SINGLE, capture, "before started(X) outside turn 1")

    def test_empty_fire_reply_then_probe_extra_turn_is_adopted(self):
        capture = fire(turn1(), [], reply=None, thinking_only=True)
        capture.turn_start().text(p12.EXTRA).turn_end()
        self.assertAdopted(p12.SINGLE, capture, "holds no stop call")

    # -- model mistakes: RECAPTURE ---------------------------------------------

    def test_turn1_without_alpha(self):
        capture = Capture().turn_start(first=True).text(p12.WAITING).turn_end()
        self.assertRecapture(p12.SINGLE, capture, "turn 1's calls")

    def test_alpha_with_delay_30(self):
        wrong = dict(p12.ALPHA, delaySeconds=30)
        self.assertRecapture(p12.SINGLE, fire(turn1(alpha=wrong), [STOP0]), "turn 1's calls")

    def test_alpha_with_delay_30_rejected_is_no_cut_point(self):
        wrong = dict(p12.ALPHA, delaySeconds=30)
        admission = self.assertRecapture(p12.SINGLE, turn1(alpha=wrong, alpha_error="no"), "turn 1's calls")
        self.assertIsNone(admission.cut)

    def test_stop_in_turn1_with_alpha_not_rejected(self):
        capture = turn1(extra_calls=[(p12.STOP, None)])
        self.assertRecapture(p12.SINGLE, capture, "turn 1's calls")

    def test_probe_done_in_turn1(self):
        self.assertRecapture(p12.SINGLE, fire(turn1(reply=p12.DONE), [STOP0]), "PROBE-DONE")

    def test_fire_turn_without_stop_and_off_script_reply(self):
        self.assertRecapture(p12.SINGLE, fire(turn1(), [], reply="All done."), "calls after turn 1")

    def test_fire_turn_without_stop_replying_probe_done(self):
        self.assertRecapture(p12.SINGLE, fire(turn1(), [], reply=p12.DONE), "calls after turn 1")

    def test_fire_turn_without_stop_and_empty_reply(self):
        capture = fire(turn1(), [], reply=None, thinking_only=True)
        self.assertRecapture(p12.SINGLE, capture, "calls after turn 1")

    def test_unbracketed_off_script_turn_and_nothing_after(self):
        capture = turn1()
        capture.turn_start().text("Waiting.").turn_end()
        self.assertRecapture(p12.SINGLE, capture, "calls after turn 1")

    def test_single_probe_extra_reply_with_a_call_to_another_tool(self):
        capture = fire(turn1(), [{"tool_input": {"command": "true"}, "name": "Bash"}], reply=p12.EXTRA)
        self.assertRecapture(p12.SINGLE, capture, "calls after turn 1")

    def test_nested_bravo_no_stop_then_probe_extra_turn(self):
        capture = fire(turn1(), [BRAVO_OK], reply=p12.DONE)
        capture.turn_start().text(p12.EXTRA).turn_end()
        self.assertRecapture(p12.NESTED, capture, "calls after turn 1")

    def test_nested_stop_before_bravo(self):
        self.assertRecapture(p12.NESTED, fire(turn1(), [STOP1, BRAVO_OK]), "calls after turn 1")

    def test_nested_bravo_twice_without_rejection(self):
        self.assertRecapture(p12.NESTED, fire(turn1(), [BRAVO_OK, BRAVO_OK, STOP1]), "calls after turn 1")

    def test_model_mistake_plus_harness_fault_is_recapture(self):
        wrong = dict(p12.ALPHA, delaySeconds=30)
        capture = fire(turn1(alpha=wrong), [{"tool_input": p12.STOP, "answer": None}])
        self.assertRecapture(p12.SINGLE, capture, "turn 1's calls")

    # -- admit reads nothing of the harness but the one flag -------------------

    def _variants(self, capture: Capture, probe: str):
        events = capture.events
        exact_ids = {c["id"] for e in events if e.get("type") == "assistant" for c in content(e)
                     if c.get("type") == "tool_use" and p12.label(c["name"], c["input"])}
        stripped = [e for e in events if e.get("type") != "command_lifecycle" and not (
            e.get("type") == "user" and not any(c.get("tool_use_id") in exact_ids for c in content(e)))]
        replaced = copy.deepcopy(events)
        for event in replaced:
            if event.get("type") == "user":
                event["tool_use_result"] = {"replaced": True}
                for item in content(event):
                    item["content"] = "replaced"
        flipped = copy.deepcopy(events)
        for event in flipped:
            if event.get("type") == "user":
                for item in content(event):
                    if item.get("tool_use_id") not in exact_ids:
                        item["is_error"] = not item.get("is_error")
        return [stripped, replaced, flipped]

    def test_admit_verdict_is_independent_of_the_harness(self):
        cases = [
            (p12.SINGLE, fire(turn1(), [STOP0])),
            (p12.NESTED, fire(turn1(), [{"tool_input": p12.BRAVO, "answer": "no", "is_error": True}])),
            (p12.SINGLE, fire(turn1(), [{"tool_input": {"command": "x"}, "name": "Bash"}], reply=p12.EXTRA)),
            (p12.SINGLE, fire(turn1(alpha=dict(p12.ALPHA, delaySeconds=30), alpha_error="no"), [STOP0])),
            (p12.SINGLE, fire(turn1(), [], reply=p12.EXTRA)),
        ]
        for probe, capture in cases:
            verdict = p12.admit(probe, capture.events)
            for variant in self._variants(capture, probe):
                with self.subTest(probe=probe, verdict=verdict.verdict):
                    again = p12.admit(probe, variant)
                    self.assertEqual((again.verdict, again.cut), (verdict.verdict, verdict.cut))

    def test_after_turn1_admit_reads_only_whether_text_is_probe_extra(self):
        one = fire(turn1(), [], reply="Some off-script text.")
        two = fire(turn1(), [], reply="Different off-script words.")
        self.assertEqual(p12.admit(p12.SINGLE, one.events).verdict, p12.admit(p12.SINGLE, two.events).verdict)
        self.assertEqual(p12.admit(p12.SINGLE, one.events).reasons, p12.admit(p12.SINGLE, two.events).reasons)


# ---------------------------------------------------------------------------
# The fake harness.
# ---------------------------------------------------------------------------


STREAMING_ARGV = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]


def task_line(text: str = "the task") -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


class FakeRun:
    """One streaming fake: send the task, read events, close stdin on cue."""

    def __init__(self, env: dict, *, cwd: str | None = None, close_immediately=False) -> None:
        full_env = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_CLAUDE_")}
        full_env.update(env)
        self.proc = subprocess.Popen([sys.executable, str(FAKE_CLAUDE), *STREAMING_ARGV], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=full_env, text=True,
                                     cwd=cwd, start_new_session=True)
        self.proc.stdin.write(task_line())
        self.proc.stdin.flush()
        if close_immediately:
            self.close()
        self.events: list[dict] = []

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def read_until(self, predicate, timeout: float = 20.0) -> list[dict]:
        """Read events until ``predicate(events)`` holds (bounded)."""
        deadline = time.monotonic() + timeout
        result: dict = {}

        def reader():
            for line in self.proc.stdout:
                self.events.append(json.loads(line))
                if predicate(self.events):
                    result["done"] = True
                    return
            result["eof"] = True

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive() or not result.get("done"):
            self.kill()
            raise AssertionError(f"fake did not reach the expected point (exit {self.proc.poll()}); "
                                 f"events so far: {projection(self.events)}")
        return self.events

    def finish(self, timeout: float = 20.0) -> int:
        self.close()
        for line in self.proc.stdout:
            self.events.append(json.loads(line))
        return self.proc.wait(timeout)

    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass
        self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass


def count(events, type_, state=None):
    return sum(1 for e in events if e.get("type") == type_ and (state is None or e.get("state") == state))


def settled(results: int):
    """The fake has played ``results`` turns and closed every bracket."""
    def predicate(events):
        return count(events, "result") >= results and \
            count(events, "command_lifecycle", "started") == count(events, "command_lifecycle", "completed")
    return predicate


# -- fixture -> FAKE_CLAUDE_TURNS ---------------------------------------------


def print_mode(name: str) -> bool:
    meta = json.loads((CONTRACT_DIR / f"{name}.meta.json").read_text())
    return meta.get("mode") == "print" or meta.get("stdin") == "/dev/null"


def translate(name: str) -> list:
    """The fixture as a turn script for the fake's own step vocabulary.

    The fake's events are not copied from the fixture: each step makes the
    fake play one harness behaviour (a background task's start, a kill, a
    Monitor, a wakeup and its fire), and the replay test compares what it
    emits with what the real harness did."""
    events = [e for e in load(name) if kind(e) not in DROPPED]
    printing = print_mode(name)
    turns: list[list] = []
    wakeups: list[dict] = []
    tasks: dict[str, dict] = {}
    current: list | None = None
    in_bracket = False
    pending_completion = None  # the task whose completion started the next turn
    index = 0

    def result_of(tool_use_id, start):
        for j in range(start, len(events)):
            if events[j].get("type") == "user":
                for item in content(events[j]):
                    if item.get("tool_use_id") == tool_use_id:
                        return j, item, events[j]
        raise AssertionError(f"{name}: no result for {tool_use_id}")

    def task_events_for(tool_use_id, start, end):
        return [e for e in events[start:end] if e.get("type") == "system" and e.get("tool_use_id") == tool_use_id
                and e.get("subtype") == "task_started"]

    while index < len(events):
        event = events[index]
        etype = event.get("type")
        if kind(event) == ("system", "init"):
            steps: list = []
            if in_bracket:
                wakeup = next(w for w in wakeups if "fire_turn" not in w and not w.get("cancelled"))
                wakeup["fire_turn"] = steps
            else:
                if turns and pending_completion is None and current is None:
                    # A turn with no completion before it: a Monitor tick.
                    monitor = next((t for t in tasks.values() if t["step"] == "monitor" and t.get("open")), None)
                    if monitor is not None:
                        monitor["ticks"] += 1
                pending_completion = None
                turns.append(steps)
            current = steps
            index += 1
            continue
        if etype == "command_lifecycle":
            in_bracket = event["state"] == "started"
            index += 1
            continue
        if etype == "result":
            current = None
            index += 1
            continue
        if etype == "assistant" and event.get("parent_tool_use_id"):
            agent = next(t for t in tasks.values() if t.get("tool_use_id") == event["parent_tool_use_id"])
            agent["events"].append({"step": content(event)[0]["type"]})
            index += 1
            continue
        if etype == "assistant":
            item = content(event)[0]
            if item["type"] in ("thinking", "text"):
                current.append({"step": item["type"]})
                index += 1
                continue
            group = []
            j = index
            while j < len(events) and events[j].get("type") == "assistant" and not events[j].get("parent_tool_use_id") \
                    and content(events[j])[0]["type"] == "tool_use":
                group.append(content(events[j])[0])
                j += 1
            answered = {}
            last = j
            for call in group:
                r_index, r_item, _ = result_of(call["id"], j)
                answered[call["id"]] = (r_index, r_item)
                last = max(last, r_index)
            calls = []
            for call in group:
                r_index, r_item = answered[call["id"]]
                started = task_events_for(call["id"], index, r_index)
                step = {"step": "tool", "name": call["name"], "input": call.get("input") or {},
                        "is_error": r_item.get("is_error")}
                if started and started[0].get("is_backgrounded"):
                    task_id = started[0]["task_id"]
                    if call["name"] == "Monitor":
                        step = {"step": "monitor", "id": task_id, "ticks": 0, "interval": 0.3}
                    elif started[0].get("task_type") == "local_agent":
                        step = {"step": "subagent_handback", "id": task_id, "after": 0.2, "events": [],
                                "tool_use_id": call["id"]}
                    else:
                        step = {"step": "bash_bg", "id": task_id, "seconds": 0.2}
                    step["open"] = True
                    tasks[task_id] = step
                elif started:
                    step["fg_task"] = True
                elif call["name"] == "TaskStop":
                    stopped = next(e for e in events[index:r_index] if e.get("subtype") == "task_updated")
                    step = {"step": "task_stop", "id": stopped["task_id"]}
                    tasks[stopped["task_id"]]["open"] = False
                    tasks[stopped["task_id"]]["seconds"] = 30
                elif call["name"] == "ScheduleWakeup" and not r_item.get("is_error"):
                    if (call.get("input") or {}).get("stop"):
                        step = {"step": "wakeup_stop"}
                        for wakeup in wakeups:
                            wakeup.setdefault("cancelled", "fire_turn" not in wakeup)
                    else:
                        tool_input = call.get("input") or {}
                        step = {"step": "wakeup", "delay": 0.2, "prompt": tool_input.get("prompt", "fake"),
                                "reason": tool_input.get("reason", "fake")}
                        wakeups.append(step)
                calls.append(step)
            if len(calls) == 1:
                current.append(calls[0])
            else:
                # Parallel calls: every tool_use first, then the answers in
                # the fixture's order; a background member keeps the task's
                # own step dict, so a later kill still adjusts it.
                order = sorted(range(len(group)), key=lambda k: answered[group[k]["id"]][0])
                members = [{"bash_bg": call} if call["step"] == "bash_bg" else call for call in calls]
                current.append({"step": "tools", "calls": members, "result_order": order})
            index = last + 1
            continue
        if kind(event) == ("system", "background_tasks_changed"):
            updated, notified = events[index + 1], events[index + 2]
            assert updated.get("subtype") == "task_updated" and notified.get("subtype") == "task_notification", name
            task = tasks[updated["task_id"]]
            status = updated["patch"]["status"]
            task["open"] = False
            if status == "completed":
                if current is not None:  # mid-turn
                    current.append({"step": "await", "id": updated["task_id"], "notify": not printing})
                else:
                    pending_completion = task
            else:  # killed: at EOF (print mode), or a Monitor's own timeout
                if printing:
                    if task["step"] == "monitor":
                        task["interval"] = 3600
                    else:
                        task["seconds"] = 30
                else:
                    task["timeout"] = 0.3
                    pending_completion = task
            index += 3
            continue
        raise AssertionError(f"{name}: untranslated event {index} {kind(event)}")

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ("open", "cancelled", "tool_use_id")}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return clean(turns)


class FixtureReplayTest(unittest.TestCase):
    """Each fixture's turn script, played by the fake, reproduces the
    fixture's projected event sequence (plan CP1, "Contract tests")."""

    def replay(self, name: str) -> list[dict]:
        events = load(name)
        turns = translate(name)
        printing = print_mode(name)
        run = FakeRun({"FAKE_CLAUDE_TURNS": json.dumps(turns)}, close_immediately=printing)
        try:
            if not printing:
                run.read_until(settled(count(events, "result")))
            run.finish()
        finally:
            run.kill()
        return run.events

    def test_every_fixture_replays(self):
        for name in FIXTURES:
            if name in NOT_REPLAYED:
                continue
            with self.subTest(fixture=name):
                self.assertEqual(projection(self.replay(name)), projection(load(name)))

    def test_the_excluded_fixture_is_outside_the_model(self):
        events = load("job_2857a730_two_results")
        self.assertTrue(any(e.get("parent_tool_use_id") and e.get("type") == "user" for e in events))
        results = [i for i, e in enumerate(events) if e.get("type") == "result"]
        self.assertEqual(results[-1] - results[-2], 1)  # two results, no turn between them

    def test_the_projection_drops_exactly_the_six_kinds(self):
        seen = {kind(e) for name in FIXTURES for e in load(name)}
        structural = seen - DROPPED
        self.assertTrue(DROPPED <= seen, DROPPED - seen)
        self.assertEqual(
            structural,
            {("system", "init"), ("assistant", None), ("user", None), ("result", "success"),
             ("system", "background_tasks_changed"), ("system", "task_started"), ("system", "task_updated"),
             ("system", "task_notification"), ("command_lifecycle", None)})


class FakeStreamingModeTest(unittest.TestCase):
    def run_turns(self, turns, *, results=1, extra_env=None, cwd=None):
        env = {"FAKE_CLAUDE_TURNS": json.dumps(turns), **(extra_env or {})}
        run = FakeRun(env, cwd=cwd)
        self.addCleanup(run.kill)
        run.read_until(settled(results))
        return run

    # -- existing variables, one turn ------------------------------------------

    def test_default_stdout_waits_for_stdin_eof(self):
        run = FakeRun({})
        self.addCleanup(run.kill)
        run.read_until(lambda events: count(events, "result") == 1)
        time.sleep(0.3)
        self.assertIsNone(run.proc.poll(), "a clean turn keeps the session open until stdin EOF")
        self.assertEqual(run.finish(), 0)

    def test_stream_without_result_exits_at_once(self):
        truncated = json.dumps({"type": "system", "subtype": "init", "session_id": "s"}) + "\n"
        run = FakeRun({"FAKE_CLAUDE_STDOUT": truncated, "FAKE_CLAUDE_EXIT": "3"})
        self.addCleanup(run.kill)
        self.assertEqual(run.proc.wait(10), 3)

    def test_task_is_the_stream_json_message_and_script_lookup_uses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            diag = os.path.join(tmp, "diag.json")
            script = os.path.join(tmp, "script.json")
            Path(script).write_text(json.dumps({"the task": [[{"action": "write", "path": "out.txt",
                                                              "text": "scripted"}]]}))
            run = FakeRun({"FAKE_CLAUDE_DIAG_FILE": diag, "FAKE_CLAUDE_SCRIPT": script}, cwd=tmp)
            self.addCleanup(run.kill)
            run.read_until(lambda events: count(events, "result") == 1)
            self.assertEqual(run.finish(), 0)
            record = json.loads(Path(diag).read_text())
            self.assertEqual(record["task_message"]["message"]["content"], "the task")
            self.assertFalse(record["stdin_at_eof"])
            self.assertEqual(Path(tmp, "out.txt").read_text(), "scripted")

    def test_descendant_inherits_no_descriptor_in_streaming_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = os.open(os.path.join(tmp, "lock"), os.O_RDWR | os.O_CREAT)
            self.addCleanup(os.close, lock)
            record = os.path.join(tmp, "descendant.json")
            env = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_CLAUDE_")}
            env.update({"FAKE_CLAUDE_DESCENDANT": "group:5", "FAKE_CLAUDE_DESCENDANT_FILE": record})
            proc = subprocess.Popen([sys.executable, str(FAKE_CLAUDE), *STREAMING_ARGV], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, env=env, text=True, pass_fds=(lock,),
                                    start_new_session=True)
            self.addCleanup(lambda: os.killpg(proc.pid, signal.SIGKILL) if proc.poll() is None else None)
            proc.stdin.write(task_line())
            proc.stdin.flush()
            deadline = time.monotonic() + 10
            while not os.path.exists(record) and time.monotonic() < deadline:
                time.sleep(0.02)
            pid = json.loads(Path(record).read_text())["pid"]
            self.addCleanup(lambda: _kill(pid))
            fds = {int(fd) for fd in os.listdir(f"/proc/{pid}/fd")}
            self.assertNotIn(lock, fds)
            self.assertTrue(fds <= {0, 1, 2})
            proc.stdin.close()
            proc.wait(10)
            proc.stdout.close()

    # -- turns -----------------------------------------------------------------

    def test_background_task_completion_starts_a_notification_turn(self):
        run = self.run_turns([[{"step": "bash_bg", "id": "b1", "seconds": 0.2}, {"step": "text"}],
                              [{"step": "text"}]], results=2)
        self.assertEqual(run.finish(), 0)
        results = [e for e in run.events if e.get("type") == "result"]
        self.assertNotIn("origin", results[0])
        self.assertEqual(results[1]["origin"], {"kind": "task-notification"})
        self.assertEqual([e["patch"]["status"] for e in run.events if e.get("subtype") == "task_updated"],
                         ["completed"])
        for event in run.events:
            self.assertEqual("timestamp" in event, event.get("type") in ("user", "assistant"), event)

    def test_stdin_eof_with_a_task_open_kills_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "finished")
            run = self.run_turns([[{"step": "bash_bg", "id": "b1", "seconds": 2, "write_file": marker}]])
            pid = next(int(p) for p in _children(run.proc.pid))
            self.assertEqual(run.finish(), 0)
            kinds = [(e.get("subtype"), e.get("status") or (e.get("patch") or {}).get("status"))
                     for e in run.events if e.get("type") == "system"][-3:]
            self.assertEqual(kinds, [("background_tasks_changed", None), ("task_updated", "killed"),
                                     ("task_notification", "stopped")])
            time.sleep(2.5)
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(_alive(pid))

    def test_task_stop_is_p8s_sequence_and_no_turn(self):
        run = self.run_turns([[{"step": "bash_bg", "id": "b1", "seconds": 30}, {"step": "task_stop", "id": "b1"},
                               {"step": "text"}]])
        time.sleep(0.3)
        self.assertEqual(run.finish(), 0)
        self.assertEqual(count(run.events, "result"), 1)
        seq = [kind(e) for e in run.events]
        stop = next(i for i, e in enumerate(run.events) if e.get("type") == "assistant"
                    and content(e)[0].get("name") == "TaskStop")
        self.assertEqual(seq[stop + 1:stop + 5], [("system", "background_tasks_changed"), ("system", "task_updated"),
                                                  ("system", "task_notification"), ("user", None)])

    def test_monitor_ticks_and_timeout(self):
        run = self.run_turns([[{"step": "monitor", "id": "m", "ticks": 2, "interval": 0.2}],
                              [{"step": "text"}], [{"step": "text"}], [{"step": "text"}]], results=4)
        self.assertEqual(run.finish(), 0)
        self.assertEqual([e["patch"]["status"] for e in run.events if e.get("subtype") == "task_updated"],
                         ["completed"])
        run = self.run_turns([[{"step": "monitor", "id": "m", "ticks": 0, "timeout": 0.2}], [{"step": "text"}]],
                             results=2)
        self.assertEqual(run.finish(), 0)
        self.assertEqual([e.get("status") for e in run.events if e.get("subtype") == "task_notification"],
                         ["stopped"])

    def test_subagent_handback_turn(self):
        run = self.run_turns([[{"step": "subagent_handback", "after": 0.1}], [{"step": "text"}]], results=2)
        self.assertEqual(run.finish(), 0)
        started = next(e for e in run.events if e.get("subtype") == "task_started")
        self.assertEqual(started["task_type"], "local_agent")
        sub = [e for e in run.events if e.get("parent_tool_use_id")]
        self.assertEqual(len(sub), 1)
        self.assertEqual(run.events[-1]["origin"], {"kind": "task-notification"})

    # -- wakeups -------------------------------------------------------------

    def test_wakeup_fire_is_the_measured_bracket(self):
        run = self.run_turns([[{"step": "wakeup", "delay": 0.2,
                                "fire_turn": [{"step": "wakeup", "delay": 0.2}, {"step": "text"}]}]], results=3)
        self.assertEqual(run.finish(), 0)
        pairs = brackets(run.events)
        self.assertEqual(len(pairs), 2)
        self.assertNotEqual(pairs[0][2], pairs[1][2])
        for start, end, _ in pairs:
            inside = run.events[start + 1:end]
            self.assertEqual(kind(inside[0]), ("system", "init"))
            self.assertEqual(inside[1]["type"], "assistant")
            self.assertEqual(inside[-1]["type"], "result")
            self.assertNotIn("origin", inside[-1])
        schedule = run.events[2]
        self.assertRegex(content(schedule)[0]["content"], r"in \d+s\)")
        self.assertEqual(set(schedule["tool_use_result"]), {"scheduledFor", "clampedDelaySeconds", "wasClamped"})

    def test_wakeup_stop_counts_the_fakes_own_pending_wakeups(self):
        # Inside ALPHA's fire, ALPHA no longer counts: 0, then 1 with BRAVO.
        for fire_turn, expected in (([{"step": "wakeup_stop"}], 0),
                                    ([{"step": "wakeup", "delay": 30}, {"step": "wakeup_stop"}], 1)):
            run = self.run_turns([[{"step": "wakeup", "delay": 0.2, "fire_turn": fire_turn}]], results=2)
            self.assertEqual(run.finish(), 0)
            stops = [e["tool_use_result"] for e in run.events if isinstance(e.get("tool_use_result"), dict)
                     and e["tool_use_result"].get("stopped")]
            self.assertEqual(stops, [{"scheduledFor": 0, "clampedDelaySeconds": 0, "wasClamped": False,
                                      "stopped": True, "cancelledWakeups": expected}])
            self.assertEqual(len(brackets(run.events)), 1)
        # A stop before any fire cancels the pending one, and nothing fires.
        run = self.run_turns([[{"step": "wakeup", "delay": 0.2}, {"step": "wakeup_stop"}]])
        time.sleep(0.5)
        self.assertEqual(run.finish(), 0)
        self.assertFalse(brackets(run.events))
        self.assertEqual(count(run.events, "command_lifecycle"), 0)

    def test_lifecycle_faults(self):
        def lifecycle(fault, results=2, extra_turn=False):
            first = [fault, {"step": "wakeup", "delay": 0.2}]
            turns = [first]
            if extra_turn:
                first.append({"step": "wakeup", "delay": 0.6})
            run = self.run_turns(turns, results=results + (1 if extra_turn else 0))
            if extra_turn:
                run.read_until(lambda events: count(events, "result") >= 3, timeout=10)
            time.sleep(0.2)
            run.finish()
            return [(e["state"], e["command_uuid"]) for e in run.events if e.get("type") == "command_lifecycle"]

        omitted = lifecycle({"step": "lifecycle_fault", "kind": "omit_started"})
        self.assertEqual([s for s, _ in omitted], ["completed"])
        self.assertEqual([s for s, _ in lifecycle({"step": "lifecycle_fault", "kind": "duplicate_completed"})],
                         ["started", "completed", "completed"])
        self.assertEqual([s for s, _ in lifecycle({"step": "lifecycle_fault", "kind": "completed_before_started"})],
                         ["completed", "started"])
        empty = self.run_turns([[{"step": "lifecycle_fault", "kind": "empty"}, {"step": "wakeup", "delay": 0.2}]])
        empty.read_until(lambda events: count(events, "command_lifecycle") == 2)
        empty.finish()
        start = next(i for i, e in enumerate(empty.events) if e.get("type") == "command_lifecycle")
        self.assertEqual(empty.events[start + 1]["type"], "command_lifecycle")
        malformed = self.run_turns([[{"step": "lifecycle_fault", "kind": "malformed", "field": "command_uuid"},
                                     {"step": "wakeup", "delay": 0.2}]], results=2)
        malformed.finish()
        started = [e for e in malformed.events if e.get("type") == "command_lifecycle" and e["state"] == "started"]
        self.assertNotIn("command_uuid", started[0])
        with self.assertRaises(AssertionError):
            self.run_turns([[{"step": "lifecycle_fault", "kind": "nonsense"}]])

    def test_spurious_bracket_leaves_the_real_fire_in_place(self):
        run = self.run_turns([[{"step": "lifecycle_fault", "kind": "spurious_bracket", "turn": "task"},
                               {"step": "bash_bg", "id": "b", "seconds": 0.1},
                               {"step": "wakeup", "delay": 0.6}], [{"step": "text"}]], results=3)
        run.finish()
        pairs = brackets(run.events)
        self.assertEqual(len(pairs), 2)
        first_turn = run.events[pairs[0][0] + 1:pairs[0][1]]
        self.assertEqual(first_turn[-1].get("origin"), {"kind": "task-notification"})
        self.assertNotIn("origin", run.events[pairs[1][1] - 1])

    # -- orphans -------------------------------------------------------------

    def test_orphan_modes_leave_a_tagged_descendant_behind(self):
        for mode in ("setsid", "reparent", "daemon"):
            with self.subTest(orphan=mode):
                argv0 = f"wlo-cp1-{mode}-{os.getpid()}"
                run = self.run_turns([[{"step": "bash_bg", "id": "b", "seconds": 30, "orphan": mode,
                                        "argv0": argv0, "orphan_seconds": 20}]],
                                     extra_env={"WORKFLOW_CONTROLLER_OWNERSHIP": f"tag-{mode}"})
                self.assertTrue(_wait(lambda: _find(argv0)))
                fake_sid = os.getsid(run.proc.pid)
                self.assertEqual(run.finish(), 0)
                (pid,) = _find(argv0)
                self.addCleanup(_kill, pid)
                self.assertTrue(_alive(pid), "the fake kills its own task, never the orphan")
                environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
                self.assertIn(f"WORKFLOW_CONTROLLER_OWNERSHIP=tag-{mode}".encode(), environ)
                fds = {int(fd) for fd in os.listdir(f"/proc/{pid}/fd")}
                self.assertTrue(fds <= {0, 1, 2})
                sid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[3])
                if mode == "reparent":
                    self.assertEqual(sid, fake_sid)
                else:
                    self.assertEqual(sid, pid)
                _kill(pid)


    # -- orphan bursts ---------------------------------------------------------

    def test_an_orphan_burst_is_one_bash_call_that_returns_after_the_burst(self):
        with tempfile.TemporaryDirectory() as tmp:
            marks = os.path.join(tmp, "marks.json")
            run = self.run_turns([[{"step": "orphan_burst", "count": 5, "spacing": 0.02, "lifetime": 0.05,
                                    "marks_file": marks}, {"step": "text"}]])
            self.assertTrue(os.path.exists(marks), "the tool call returned before the burst ended")
            self.assertEqual(run.finish(), 0)
            [use] = [c for e in run.events if e.get("type") == "assistant" for c in content(e)
                     if c.get("type") == "tool_use"]
            self.assertEqual(use["name"], "Bash")
            self.assertIn("--orphan-burst", use["input"]["command"])
            [result] = [c for e in run.events if e.get("type") == "user" for c in content(e)
                        if c.get("type") == "tool_result"]
            self.assertEqual(result["tool_use_id"], use["id"])
            times = json.loads(Path(marks).read_text())
            self.assertGreaterEqual(times["ended_at"] - times["started_at"], 5 * 0.02)

    def test_a_detached_orphan_burst_runs_after_the_fake_has_exited(self):
        with tempfile.TemporaryDirectory() as tmp:
            marks = os.path.join(tmp, "marks.json")
            run = self.run_turns([[{"step": "orphan_burst", "count": 3, "spacing": 0.02, "lifetime": 0.05,
                                    "detach": True, "marks_file": marks}]])
            self.assertFalse(os.path.exists(marks))
            self.assertEqual(run.finish(), 0)
            exited_at = time.time()
            self.assertTrue(_wait(lambda: os.path.exists(marks) and os.path.getsize(marks) > 0))
            self.assertGreaterEqual(json.loads(Path(marks).read_text())["started_at"], exited_at - 0.5)

    def test_every_burst_grandchild_is_a_setsid_orphan_of_the_nearest_subreaper(self):
        # A child interpreter marks itself a subreaper, runs the burst and
        # watches its own children: every one besides the burst process is
        # a session leader, and each is collected here, by that subreaper.
        probe = textwrap.dedent("""
            import ctypes, json, os, subprocess, sys, time
            ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)
            burst = subprocess.Popen([sys.executable, sys.argv[1], "--orphan-burst", "6", "0.02", "0.3"])
            sessions, reaped = {}, set()
            while True:
                for tid in os.listdir("/proc/self/task"):
                    with open(f"/proc/self/task/{tid}/children") as fh:
                        for pid in map(int, fh.read().split()):
                            if pid != burst.pid:
                                try:
                                    with open(f"/proc/{pid}/stat") as stat:
                                        sessions[pid] = int(stat.read().rsplit(")", 1)[1].split()[3])
                                except OSError:
                                    pass
                for pid in list(sessions):
                    if pid not in reaped and os.waitpid(pid, os.WNOHANG)[0]:
                        reaped.add(pid)
                if burst.poll() is not None and reaped >= set(sessions):
                    break
                time.sleep(0.01)
            print(json.dumps({"sessions": sessions, "returncode": burst.returncode}))
        """)
        out = subprocess.run([sys.executable, "-c", probe, str(FAKE_CLAUDE)], capture_output=True, text=True,
                             timeout=60, check=True)
        report = json.loads(out.stdout)
        self.assertEqual(report["returncode"], 0)
        self.assertEqual(len(report["sessions"]), 6)
        self.assertEqual({int(pid) for pid in report["sessions"]}, set(report["sessions"].values()))

class ReapRecordedWorkersTest(unittest.TestCase):
    """The test-teardown guarantee (plan CP1, round 1's I7)."""

    def _spawn(self, env=None):
        proc = subprocess.Popen(["sleep", "60"], env=env, start_new_session=True)
        self.addCleanup(lambda: (_kill(proc.pid), proc.wait()))
        return proc

    def test_kills_recorded_identities_and_tagged_processes_only(self):
        from tests import process_fixtures

        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp, "jobs")
            jobs.mkdir()
            worker = self._spawn()
            anchor = self._spawn()
            owned = self._spawn()
            stale = self._spawn()  # recorded with the wrong start ticks: never killed
            tag = f"job-cp1-{os.getpid()}"
            tagged = self._spawn(env={**os.environ, "WORKFLOW_CONTROLLER_OWNERSHIP": f"outer:{tag}"})
            other = self._spawn(env={**os.environ, "WORKFLOW_CONTROLLER_OWNERSHIP": f"{tag}-not"})
            self.assertTrue(process_fixtures.wait_until(
                lambda: all(process_fixtures.read_stat(p.pid) for p in (worker, anchor, owned, stale))))
            record = {
                "job_id": "j", "status": "LAUNCHED", "ownership_tag": tag,
                "worker_process": process_fixtures.worker_process_dict(worker.pid),
                "worker_anchor": process_fixtures.worker_process_dict(anchor.pid),
                "worker_state": {"state": "WAITING",
                                 "owned_processes": [process_fixtures.worker_process_dict(owned.pid),
                                                     process_fixtures.worker_process_dict(stale.pid,
                                                                                          start_ticks=1)]},
            }
            Path(jobs, "j.json").write_text(json.dumps(record))
            Path(jobs, "broken.json").write_text("{not json")
            self.assertTrue(process_fixtures.wait_until(lambda: tagged.pid in process_fixtures.tagged_pids(tag)))
            killed = process_fixtures.reap_recorded_workers(tmp)
            self.assertEqual(sorted(killed), sorted([worker.pid, anchor.pid, owned.pid, tagged.pid]))
            for proc in (worker, anchor, owned, tagged):
                proc.wait(5)
            self.assertIsNone(stale.poll())
            self.assertIsNone(other.poll())
            self.assertEqual(process_fixtures.reap_recorded_workers(Path(tmp, "missing")), [])

    def test_mixin_registers_the_cleanup(self):
        from tests import process_fixtures

        calls = []

        class Probe(process_fixtures.ReapRecordedWorkersMixin, unittest.TestCase):
            def runTest(self):
                self.reap_workers_under("/nonexistent-runtime-root")

        case = Probe()
        original = process_fixtures.reap_recorded_workers
        process_fixtures.reap_recorded_workers = lambda root: calls.append(root) or []
        try:
            case.runTest()
            self.assertEqual(calls, [])
            case.doCleanups()
        finally:
            process_fixtures.reap_recorded_workers = original
        self.assertEqual(calls, ["/nonexistent-runtime-root"])


def _children(pid: int) -> list[str]:
    try:
        return Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    except OSError:
        return []


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state not in ("Z", "X")


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _find(argv0: str) -> list[int]:
    found = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                if Path(f"/proc/{name}/cmdline").read_bytes().split(b"\0", 1)[0] == argv0.encode():
                    found.append(int(name))
            except OSError:
                pass
    return found


def _wait(predicate, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


if __name__ == "__main__":
    unittest.main()
