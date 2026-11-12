# N1: Controller ACTION_REQUIRED events as answerable questions, one per gate

Item id: `workflow-controller-n1`.

Not yet planned. Make the Controller's `ACTION_REQUIRED` event at a decision gate the one question the owner answers, in SignalHub's answers format with choices, and send it once per gate. It needs H4g, the last item of the harness epic, and comes before C10d-a. Source: [#336](https://github.com/RodrigoFAbreu/workflow-controller/issues/336). Complexity: medium.

## Owner request (2026-11-10)

After the Codex worker bundle (H4e3, H4e3b, H4f, H4g), the owner asked to adapt the `ACTION_REQUIRED` events the Controller sends so that they use SignalHub's answers format, and to stop sending duplicates.

Today a stop at a decision gate (a review cap reached, `integration_required`, a held usage budget, a failed acceptance) produces several events. The Controller sends its own state or blocker notification ("Waiting on the orchestrator ... GATE_BLOCKED ..."), and then the orchestrator lane sends its own SignalHub question with choices for the same stop; when a first send loses its id, the lane sends a repeat. The owner sees duplicates of one decision, and one of them cannot be answered.

## Wanted

- The Controller's `ACTION_REQUIRED` event for a decision gate is itself the question. It carries `--choice` ids and labels that match the real remedies at that gate, with the recommendation in the message:
  - a review cap: raise the cap by N, split the item, accept at the cap, stop;
  - `integration_required`: merge, hold;
  - a usage hold: wait, raise.
- One event per gate: a stable idempotency key per (work item, gate, round), so a restart, a watchdog retry or an orchestrator never sends a second one. An answered or resolved gate closes its question (`close-question`).
- The answer is readable by the lane (`wait-answer` on the Controller's event id, recorded in the job or run record), so the orchestrator waits on that one event instead of sending its own.
- Informational events stay informational, with no choices.

## Open design points

- Which gates become questions. Start with the review caps, the most frequent.
- Where the Controller records the event id.
- Who acts on an answer: the orchestrator first; the Controller applying "raise cap" itself is a later step.
- How this works with the existing `notifications.*` settings.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Sizing note

One mechanism: a question event with a stable key per gate. Split at the first repeated finding class, not after a second pause. Start with the review caps; the other gates follow as their own items if the first plan shows they need more than the same mechanism.
