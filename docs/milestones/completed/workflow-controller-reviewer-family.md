# H2: the workers' provider is derived from the routed harness (Revision 2)

Work item: `workflow-controller-reviewer-family`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `71c1a15d30bcda125133cfeb82d740bbdbba118c` ("docs: link the H2 issue from the roadmap row and the next action (#186)"), the tip of `main`, passed explicitly as `/milestone-plan 71c1a15…`.
Lifecycle authority: this repository's installed Workflow **2.21.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` row H2, the next item of the harness-neutral roles epic (owner decision, 2026-10-24); `docs/ACTIVE_MILESTONE.md` "Next action". Issue [#185](https://github.com/RodrigoFAbreu/workflow-controller/issues/185).
Released baseline: `workflow-controller 1.22.2`. With today's registry no outcome changes, so the squash commit is a `chore:` change and releases nothing (see D6).
Pull request title: `chore: derive the workers' provider from the routed harness for the review mode (H2, #185)`

## Goal

`controller/review_mode.py` decides whether a configured `reviewer.harness` is a cross-model reviewer by comparing its family with a constant, `WORKER_FAMILY = "anthropic"`, whose comment says "Claude is the only worker harness". Since H1 a worker role's harness is routed and each adapter declares its `family`, so that constant is an assumption the registry can now answer. Today `reviewer.harness=claude` reads as unset (`distinct_harness` returns `None`), so a Claude reviewer can only ever be the lowered same-provider review; the day a worker role is routed to a non-Anthropic harness, the same constant would still call Codex "distinct" and Claude "the workers' own provider", which would be wrong.

This milestone derives the workers' provider family from the routing, so a configured reviewer whose family differs from the workers' is cross-model by the registry's own declaration. With the registry as it is, every outcome stays exactly as today (see D5).

One concern only: **where the review mode gets the workers' family.** Out of scope: a Codex worker (H4), the launch seam (H3), the fallback chain (H9), the independence rule in routing (H10), any change to the review modes themselves, the same-provider path, the gate policy, a setting, an exit code, a gate code, the launch of either reviewer or the verdict format.

## Current behaviour, checked

- `reviewer.harness` is an optional string of 1 to 64 characters (`controller/settings.py`); the registry check happens at the review step (`review.harness(name)`), which already registers `claude` beside `codex` (`review.HARNESSES`, `harness.REVIEWER_FAMILY`). So `reviewer.harness=claude` is accepted today and is not a cross-model reviewer: `distinct_harness` returns `None`, `cross-model` refuses it with "the workers' own provider", and `auto` and `single-provider` treat it as unset. The guides say so only in passing; D7 states it.
- `harness.worker_adapter()` returns the first worker-role adapter and is used by `telemetry` for the name only. It is not a statement about routing, and this milestone does not change it.
- Roles that run as a worker are the routing roles whose `routing.ADAPTER_ROLE` is `worker`; the functional tester runs as `functional-tester` and the Controller-run review job has its own route (`review.REVIEW_ROLE`), so neither says what the workers' provider is.

## Decisions

Each decision states one principle.

- **D1. The workers' family is read from the routing.** `review_mode.worker_families(options)` takes a `routing.RoutingOptions` and returns the frozen set of the adapter families of the harnesses that `options.resolve(role)` gives for every routing role whose `ADAPTER_ROLE` is `worker`. `options` defaults to `routing.NO_OVERRIDES`, so a caller that passes nothing gets the built-in routing. `review_mode` imports `routing` and `harness`; `routing` imports `harness`, `decision` and `errors`, none of which imports `review_mode`, so there is no cycle, and `review_mode` stays pure (no I/O, no clock).
- **D2. A reviewer is cross-model when its family is not the workers'.** `distinct_harness(reviewer, workers)` returns the reviewer's harness name when `HARNESS_FAMILY` gives it a family that is not in `workers`, and `None` for an unset reviewer or one of the workers' family. A name `HARNESS_FAMILY` does not know stays a distinct, unknown provider, as the table's comment already says. `workers` defaults to the families of `NO_OVERRIDES`. So `reviewer.harness=claude` becomes a cross-model reviewer exactly when the routed workers are not Anthropic.
- **D3. Mixed worker families stop in a named way.** When `worker_families` returns more than one family, "the worker's provider" has no single answer and the code does not guess: `choose` returns the existing `review_mode_unavailable` refusal for `auto`, `cross-model` and `single-provider`, with a reason that names the families and the roles' harnesses ("the worker roles are routed to harnesses of more than one family (anthropic, openai), so there is no single provider to be distinct from; route every worker role to harnesses of one family, or choose --review-mode manual"). `manual` is never refused, as today, and `needs_signal` returns `False` for a mixed set (no refusal reads the signal). With the registry as it is this cannot happen, because `codex` declares only the `reviewer` role and `routing.check_role_harness` refuses it for a worker role; it is reached only through a patched registry in the tests.
- **D4. The caller passes the routing; the constant goes.** `controller/job.py` `_choose_review` and `review_mode_report` gain the run's `routing.RoutingOptions` (the object `step` builds as `_routing_options(args)`; `explain` does not build it today, CP1 adds the call) and pass `worker_families(options)` into `needs_signal` and `choose`. The parameter is **keyword-only and defaults to `routing.NO_OVERRIDES`** on both `_choose_review` and `review_mode_report`, so the existing positional callers (`tests/test_review_job.py`, `tests/test_review_same_provider.py`) stay as they are and need no edit. `controller/cli.py` `explain` builds `_routing_options(args)` and passes it; `explain` is read-only and never refuses on routing, so when building it raises `RoutingConfigError` (a route `step` would refuse with exit 20) `explain` uses `routing.NO_OVERRIDES` for the mode report only and reports nothing new. With today's registry the family set is `{"anthropic"}` either way, so the report is the same. `WORKER_FAMILY` is removed. Its one other reader, `job.py`'s choice of the usage provider for the review job, asks which provider *runs the review*, not what the workers are; it uses a new `review_mode.CLAUDE_FAMILY = "anthropic"` (the family whose usage is the Claude provider's) and nothing else about that line changes.
- **D5. Every outcome stays as today, and a test says so.** For the registry as it is, `worker_families(NO_OVERRIDES)` is `{"anthropic"}`, and so is the set for any routing the registry accepts (every worker role can only take `claude-code`). A test runs `choose`, `needs_signal` and `distinct_harness` over every mode, stage, reviewer (unset, `claude`, `codex`) and signal the existing tests use, once with the default and once with `worker_families` derived from a `RoutingOptions` carrying `--role-harness` for every worker role, and requires identical results. The existing tests of `review_mode` stay green and unchanged apart from the two that read `WORKER_FAMILY`, which are rewritten to pin the derived set: `test_worker_declarations` (CP1 below) and `FamilyTest.test_a_registered_harness_is_in_the_family_table` (`tests/test_review_mode.py:201`), whose last assertion becomes `HARNESS_FAMILY["codex"] not in worker_families()`.
- **D6. Nothing a user sees changes, so nothing is released.** No setting, exit code, refusal, command or gate behaves differently with the registry as it is. The squash commit is `chore:`, which `.workflow-controller/policy.json` maps to no release; `feat:` was rejected because the feature (a Claude reviewer that is cross-model) cannot occur until a non-Anthropic worker harness is registered (H4), and a release note for it now would promise a behaviour nobody can use.
- **D7. The guides say what the setting does.** `docs/guide/runtime.md` (the `reviewer` rows and the mode table) and `docs/guide/harnesses.md` state that `claude` is a registered `reviewer.harness`, that it is cross-model only when the routed worker roles are of another family (never with the current registry, where it reads as unset), that the workers' family comes from the routing, and that mixed families stop the review with `review_mode_unavailable`. The same-provider review is unchanged and still runs Claude (`SAME_PROVIDER_HARNESS`); making that harness follow the routing belongs to the item that registers a non-Anthropic worker (H4), and the guide says so.

## Out of scope

Everything listed under Goal, plus making the same-provider harness follow the routing, a worker harness other than Claude Code, and any change to which harness and role pairs H1 accepts.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Derive the workers' family and use it in the review mode | - | 2 | 1 |
| CP2 | The guides and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- Derive the workers' family and use it in the review mode

**Files:** `controller/review_mode.py`, `controller/job.py`, `controller/cli.py`, `tests/test_review_mode.py`, `tests/test_harness.py`

D1 to D5. `controller/review_mode.py`: `worker_families`, the `workers` parameter of `distinct_harness`, `needs_signal` and `choose` (keyword, defaulting to the families of `routing.NO_OVERRIDES`), the mixed-family refusal in `choose` after the `manual` short-circuit, `CLAUDE_FAMILY` in place of `WORKER_FAMILY`. `controller/job.py`: `_choose_review` and `review_mode_report` take the `routing.RoutingOptions` as a keyword-only parameter defaulting to `routing.NO_OVERRIDES` (existing positional callers unchanged), `execute_step` passes the one it already holds, and the usage-provider line uses `CLAUDE_FAMILY`. `controller/cli.py`: `explain` builds `_routing_options(args)` and passes it to `review_mode_report`, falling back to `routing.NO_OVERRIDES` for that report when the build raises `RoutingConfigError` (a test covers both).

Tests:
- `worker_families(NO_OVERRIDES)` is `{"anthropic"}`, and equals the family of `harness.worker_adapter()` (the existing `test_worker_declarations` is rewritten to pin this instead of `WORKER_FAMILY`); `FamilyTest.test_a_registered_harness_is_in_the_family_table` keeps its other assertions and ends with `HARNESS_FAMILY["codex"] not in worker_families()`;
- the D5 equivalence test: identical `choose`, `needs_signal` and `distinct_harness` results for the default and for the derived set, over every mode, stage, reviewer and signal;
- through a patched registry whose `codex` adapter also declares the `worker` role and the models and efforts the built-in worker routes use (or whose routing names `codex` models and efforts for every worker role, by role models/efforts or `harness.models`, because `RoutingOptions` validates every role's route and the real `codex` adapter declares only `gpt-6.1-sol` and `high`/`xhigh`), and whose routing sends every worker role to it, `worker_families` is `{"openai"}`, `distinct_harness` of a `claude` reviewer is `"claude"` and of a `codex` reviewer is `None`, `choose(cross-model, claude)` returns `review` on `claude`, and `choose(cross-model, codex)` is refused with the "workers' own provider" reason;
- through the same patched registry with one worker role left on `claude-code`, `worker_families` has both families, and `choose` returns `refused` with the D3 reason for `auto`, `cross-model` and `single-provider`, not for `manual`, and `needs_signal` is `False`;
- `job._choose_review` and `review_mode_report` with a patched registry and routing follow the derived set (a `step`-level case is not needed: the unit above and one `review_mode_report` call cover the threading), and with the default routing give today's report;
- the source scan: no module under `controller/` reads `WORKER_FAMILY` any more.

Done when: `python3 -m unittest tests.test_review_mode tests.test_harness tests.test_review_claude` is green with the existing cases unchanged apart from the two rewritten above (`test_worker_declarations` and `FamilyTest`'s last assertion); the whole suite is green.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guides and the roadmap

**Files:** `docs/guide/runtime.md`, `docs/guide/harnesses.md`, `docs/ROADMAP.md`

D6 and D7. `docs/guide/runtime.md`: the `reviewer.harness` paragraph says that `claude` is also a registered name, that a reviewer is cross-model when its family is not the workers', and that the workers' family comes from the routing; the `cross-model` row of the mode table says the same; the mixed-family stop is named with its remedies. `docs/guide/harnesses.md`: the sentence on `reviewer.harness` and family says the same in one place. `docs/ROADMAP.md`: row H2 is marked done in the form of the H7 row (what is derived, that no outcome changes with today's registry, the `chore:` with no release), and the "Next, in order" list names H3 as next. `docs/ACTIVE_MILESTONE.md` is written by the Workflow's own close-out, not by hand.

Done when: `python3 tools/check_docs.py` and `python3 tools/harness_table.py --check` exit 0; the full suite is green.
<!-- /CP2 -->
