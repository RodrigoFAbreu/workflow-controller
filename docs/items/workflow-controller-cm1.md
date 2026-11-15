# CM1: list the Codex models in use instead of one fixed model

Item id: `workflow-controller-cm1`.

Not yet planned. Owner request 2026-11-15. Complexity: medium. Priority: medium. Follows [TS1](workflow-controller-ts1.md) in order.

## Why

In Controller 1.31.1 the Codex adapter (`controller/harness.py`) declares `models=("gpt-6.1-sol",)` and `efforts=("high","xhigh")` (comment: `tests/harness_contract/codex/exec_argv.json`). `routing.check_route_values` refuses any other model unless the `harness.models` setting adds it. The owner wanted the functional tester routed to `gpt-6.1-terra` (effort `high`), and the Manager lane had to add `harness.models: {"codex": ["gpt-6.1-terra"]}` to its private settings file. `controller/prices.json` has no entry for that model, so its usage cost is recorded as unknown (cost `None`, source `UNKNOWN`).

The owner's request: by default the Controller should list the models Codex currently offers instead of one fixed entry.

## Scope

One mechanism, chosen in planning:

1. The adapter's model list comes from a discovery step (the Codex CLI's own model list, cached with a timestamp). The pinned list is the fallback when discovery fails.
2. A read-only `workflow-controller` command or doctor line prints the models each harness currently accepts and where each came from (declared, discovered, added by `harness.models`).
3. An unknown model in a routing file gives a refusal that names the accepted list and the setting to add it.
4. A discovered model without a price entry is reported as "unpriced" rather than silently unknown.

Split suggestion for planning: (1) and (3) first; (2) and (4) as follow-ups if it grows past one mechanism. The sizing rule (at most one new mechanism, releasable alone) is on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Discovery source found

`codex debug models` (codex-cli 0.167.0, read 2026-11-15) prints JSON `{"models": [...]}` with, per model, `slug`, `visibility` (`list` or `hide`), `supported_in_api` and `supported_reasoning_levels`. Slugs seen: `gpt-6.1-sol`, `gpt-6-astra`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` (all `list`), and `gpt-reserve`, `codex-auto-review` (`hide`). Efforts offered: `low`, `medium`, `high`, `xhigh`, `max`, and `ultra` on some models, so the efforts list is also fixed today and could be discovered the same way. Planning should decide whether hidden models count as accepted.

## Price table lookup

Retrieved 2026-11-15 from `https://developers.openai.com/api/docs/pricing`, Standard processing, USD per 1M tokens. Short context is up to 272K input tokens, long is above. The cache-write columns are the 30-minute and 12-hour rates. The figures come from a page-to-text extraction, not a visual check.

| Model | Tier | Input | Cached input | Output | 30m cache write | 12h cache write |
|---|---|---|---|---|---|---|
| gpt-6.1-sol | short | 2.00 | 0.10 | 10.00 | 2.50 | 5.00 |
| gpt-6.1-sol | long | 4.00 | 0.20 | 15.00 | 5.00 | 10.00 |
| gpt-6.1-terra | short | 1.00 | 0.05 | 5.00 | 1.25 | 2.50 |
| gpt-6.1-terra | long | 2.00 | 0.10 | 7.50 | 2.50 | 5.00 |
| gpt-6-astra | short | 10.00 | 1.00 | 50.00 | 12.50 | 25.00 |
| gpt-6-astra | long | 20.00 | 2.00 | 75.00 | 25.00 | 50.00 |
| gpt-6-sol | short | 2.00 | 0.20 | 10.00 | 2.50 | 5.00 |
| gpt-6-sol | long | 4.00 | 0.40 | 15.00 | 5.00 | 10.00 |
| gpt-6-luna | short | 0.10 | 0.01 | 0.50 | 0.125 | 0.25 |
| gpt-6-luna | long | 0.20 | 0.02 | 0.75 | 0.25 | 0.50 |
| gpt-5.6-sol | short | 4.00 | 0.40 | 20.00 | 5.00 | 10.00 |
| gpt-5.6-sol | long | 8.00 | 0.80 | 30.00 | 10.00 | 20.00 |
| gpt-5.6-terra | short | 2.00 | 0.20 | 12.00 | 2.50 | 5.00 |
| gpt-5.6-terra | long | 4.00 | 0.40 | 18.00 | 5.00 | 10.00 |
| gpt-5.6-luna | short | 0.20 | 0.02 | 1.20 | 0.25 | 0.50 |
| gpt-5.6-luna | long | 0.40 | 0.04 | 1.80 | 0.50 | 1.00 |

Notes:

- `controller/prices.json` (version 2) stores price per token with the keys `input`, `cache_read`, `cache_write` and `output`, entered at the short-context tier and the 30-minute cache write. Its `gpt-6.1-sol` entry (0.000002, 0.0000001, 0.0000025, 0.00001) matches the short-tier row above. A `gpt-6.1-terra` entry would be 0.000001, 0.00000005, 0.00000125, 0.000005.
- The page notes that GPT-5.6 Sol has promotional pricing until 2026-11-30, so that row is not a stable figure.
- Not verified: the two `hide` slugs (`gpt-reserve`, `codex-auto-review`) have no price on the page. Whether Codex subscription use maps to these API list prices is not stated; the table is an API list-price equivalent only.
- This page records figures only. `controller/prices.json` is changed by the item's own work.
