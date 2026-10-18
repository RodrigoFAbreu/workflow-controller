"""The usage record (workflow-controller-adapters-1, D6-D8).

One frozen shape for what a harness job used, built once from the harness's
own output by one converter per harness. Each token figure has one meaning
defined here, not by any harness: ``input`` is the prompt tokens neither read
from nor written to a cache, ``cache_read`` the prompt tokens served from a
cache, ``cache_write`` the prompt tokens written to a cache, ``output`` every
generated token (reasoning included), ``reasoning`` the reasoning share of
``output`` (informational, never priced). A figure a harness does not give is
``None`` with quality ``unknown``; a number is never invented.

The record is advisory data (D9): it only adds to a job record and nothing
reads it to decide anything.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from controller import harness
from controller.worker_stream import session_telemetry

RECORD_VERSION = 1

REPORTED = "reported"
DERIVED = "derived"
UNKNOWN = "unknown"

#: The figure groups, each with its own quality flag.
GROUPS = ("input", "cache_read", "cache_write", "output", "reasoning")

PRICES_PATH = Path(__file__).with_name("prices.json")

#: The four priced groups; reasoning is inside output and is never priced.
_PRICED = ("input", "cache_read", "cache_write", "output")


def load(path: Path = PRICES_PATH) -> dict:
    """The price table: ``version``, ``currency`` and ``models`` (model id ->
    price per token for each priced group)."""
    return json.loads(path.read_text(encoding="utf-8"))


#: The shipped table, read once at import.
PRICES = load()


@dataclasses.dataclass(frozen=True)
class UsageRecord:
    harness: str
    provider: str
    model: str | None
    input: int | None
    cache_read: int | None
    cache_write: int | None
    output: int | None
    reasoning: int | None
    quality: Mapping[str, str]
    wall_ms: int | None
    turns: int | None
    cost: float | None
    cost_source: str
    price_table_version: int | None
    currency: str | None
    subagent_total_tokens: int | None = None
    version: int = RECORD_VERSION

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["quality"] = dict(self.quality)
        return data


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def cost_of(figures: Mapping[str, int | None], model: str | None, table: Mapping) -> float | None:
    """The cost of four priced groups at ``model``'s table prices, or ``None``
    when the model, a price or a figure is missing. Never charges a token twice:
    reasoning is not a group here."""
    prices = (table.get("models") or {}).get(model) if model is not None else None
    if not isinstance(prices, Mapping):
        return None
    total = 0.0
    for group in _PRICED:
        count, price = figures.get(group), prices.get(group)
        if not _int(count) or not isinstance(price, (int, float)) or isinstance(price, bool):
            return None
        total += count * price
    return round(total, 12)


def _table_facts(table: Mapping) -> tuple[int | None, str | None]:
    version, currency = table.get("version"), table.get("currency")
    return (version if _int(version) else None), (currency if isinstance(currency, str) else None)


def _unknown_record(adapter: harness.HarnessAdapter, model: str | None, table: Mapping, **extra) -> UsageRecord:
    version, currency = _table_facts(table)
    return UsageRecord(
        harness=adapter.name, provider=adapter.family, model=model,
        input=None, cache_read=None, cache_write=None, output=None, reasoning=None,
        quality={group: UNKNOWN for group in GROUPS}, wall_ms=extra.get("wall_ms"), turns=extra.get("turns"),
        cost=None, cost_source=UNKNOWN, price_table_version=version, currency=currency,
        subagent_total_tokens=extra.get("subagent_total_tokens"),
    )


def _sum_present(values: Iterable) -> int | None:
    """The sum of the numbers, ``None`` when there is none or any is not one."""
    total, seen = 0, False
    for value in values:
        if not _int(value):
            return None
        total, seen = total + value, True
    return total if seen else None


def reasoning_tokens(results: list[dict]) -> int | None:
    """The sum of ``usage.output_tokens_details.thinking_tokens`` over the
    results that carry it; ``None`` only when none does. An additive,
    usage-only read: ``worker_stream.session_telemetry`` omits it."""
    present = []
    for event in results:
        usage = event.get("usage") if isinstance(event, dict) else None
        details = usage.get("output_tokens_details") if isinstance(usage, dict) else None
        value = details.get("thinking_tokens") if isinstance(details, dict) else None
        if _int(value):
            present.append(value)
    return sum(present) if present else None


def from_claude_results(results: Iterable[dict], *, model: str | None = None,
                        table: Mapping | None = None) -> UsageRecord:
    """The record of a Claude job from its ``result`` events, in stream order.
    Tokens, turns, wall time and cost are ``worker_stream.session_telemetry``'s
    own figures (the function bound at import: a fault injected into the telemetry block's computation is not
    the record's), so they equal the ``telemetry`` block's by construction."""
    events = [event for event in results if isinstance(event, dict)]
    return from_claude_block(session_telemetry(events), reasoning=reasoning_tokens(events),
                             model=model, table=table)


def from_claude_block(block: Mapping | None, *, reasoning: int | None, model: str | None = None,
                      wall_ms: int | None = None, table: Mapping | None = None) -> UsageRecord:
    """The record of a Claude job from a telemetry-shaped ``block`` (``tokens``,
    ``cost_usd``, ``duration_ms``, ``turns``) and its reasoning figure. A review
    keeps its attempts' figures summed in this form; ``None`` (no ``result`` ran)
    gives a record with every figure unknown."""
    table = PRICES if table is None else table
    adapter = harness.ADAPTERS["claude-code"]
    if not isinstance(block, Mapping):
        return _unknown_record(adapter, model, table, wall_ms=wall_ms)
    tokens = block.get("tokens") or {}
    if model is None:
        models = block.get("models")
        if isinstance(models, dict) and len(models) == 1:
            model = next(iter(models))
    figures = {
        "input": tokens.get("input"), "cache_read": tokens.get("cache_read"),
        "cache_write": tokens.get("cache_creation"), "output": tokens.get("output"),
        "reasoning": reasoning,
    }
    figures = {group: (value if _int(value) else None) for group, value in figures.items()}
    quality = {group: (REPORTED if figures[group] is not None else UNKNOWN) for group in GROUPS}
    reported_cost = block.get("cost_usd")
    if isinstance(reported_cost, (int, float)) and not isinstance(reported_cost, bool):
        cost, source = float(reported_cost), REPORTED
    else:
        cost = cost_of(figures, model, table)
        source = DERIVED if cost is not None else UNKNOWN
    version, currency = _table_facts(table)
    wall, turns = block.get("duration_ms"), block.get("turns")
    return UsageRecord(
        harness=adapter.name, provider=adapter.family, model=model, quality=quality,
        wall_ms=wall if _int(wall) else (wall_ms if _int(wall_ms) else None),
        turns=turns if _int(turns) else None,
        cost=cost, cost_source=source, price_table_version=version, currency=currency, **figures,
    )


def parse_rollout(events: Iterable[dict]) -> dict:
    """The figures of one Codex rollout: ``subagent`` (a sub-agent session) and
    ``usage``, the last ``token_count`` event's ``total_token_usage`` (cumulative
    inside a rollout), ``None`` when the rollout has none."""
    subagent, usage = False, None
    for event in events:
        payload = event.get("payload") if isinstance(event, dict) else None
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "session_meta":
            source = payload.get("source")
            spawn = source.get("subagent") if isinstance(source, dict) else None
            subagent = isinstance(spawn, dict) and bool(spawn.get("thread_spawn"))
        elif payload.get("type") == "token_count":
            info = payload.get("info")
            block = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(block, dict):
                usage = dict(block)
    return {"subagent": subagent, "usage": usage}


def from_codex_rollouts(rollouts: Iterable[Mapping], *, model: str | None,
                        wall_ms: int | None = None, table: Mapping | None = None) -> UsageRecord:
    """The record of a Codex job from ``parse_rollout`` results, one per rollout.

    Root rollouts are summed (the first and D5's retry); a sub-agent's total goes
    in ``subagent_total_tokens`` only, since whether a parent's total already
    contains its children is not shown. Codex's ``input_tokens`` includes its
    cached tokens and its ``output_tokens`` includes the reasoning ones, so
    input = ``input_tokens`` - cached. A positive cache-write figure is kept but
    claims no exact disjoint input breakdown: input and cost stay unknown."""
    table = PRICES if table is None else table
    adapter = harness.ADAPTERS["codex"]
    roots, sub_totals = [], []
    for rollout in rollouts:
        usage = rollout.get("usage") if isinstance(rollout, Mapping) else None
        if rollout.get("subagent"):
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            if _int(total):
                sub_totals.append(total)
        elif isinstance(usage, dict):
            roots.append(usage)
    sub_total = sum(sub_totals) if sub_totals else None
    if not roots:
        return _unknown_record(adapter, model, table, wall_ms=wall_ms, subagent_total_tokens=sub_total)
    raw_in = _sum_present(u.get("input_tokens") for u in roots)
    cached = _sum_present(u.get("cached_input_tokens") for u in roots)
    written = _sum_present(u.get("cache_write_input_tokens") for u in roots)
    output = _sum_present(u.get("output_tokens") for u in roots)
    reasoning = _sum_present(u.get("reasoning_output_tokens") for u in roots)
    pure_input = None
    per_root = [(u.get("input_tokens"), u.get("cached_input_tokens")) for u in roots]
    if (raw_in is not None and cached is not None and written == 0
            and all(_int(i) and _int(c) and c <= i for i, c in per_root)):
        pure_input = raw_in - cached
    figures = {"input": pure_input, "cache_read": cached, "cache_write": written,
               "output": output, "reasoning": reasoning}
    quality = {group: (DERIVED if value is not None else UNKNOWN) for group, value in figures.items()}
    cost = cost_of(figures, model, table)
    version, currency = _table_facts(table)
    return UsageRecord(
        harness=adapter.name, provider=adapter.family, model=model, quality=quality,
        wall_ms=wall_ms if _int(wall_ms) else None, turns=None,
        cost=cost, cost_source=DERIVED if cost is not None else UNKNOWN,
        price_table_version=version, currency=currency,
        subagent_total_tokens=sub_total, **figures,
    )
