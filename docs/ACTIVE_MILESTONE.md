# Active Milestone

## Status

**In implementation.** `workflow-controller-fallback-chain` (H9a, issue
[#197](https://github.com/RodrigoFAbreu/workflow-controller/issues/197)): CP1 (the chain shape, validation, resolver and
family count, table generation 17) is committed and CP2 (the guides and the roadmap) is done; nothing selects a later
entry yet, which is H9b.

Previously: **Complete.** `workflow-controller-reviewer-independence` (H10, issue
[#194](https://github.com/RodrigoFAbreu/workflow-controller/issues/194)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the mixed-lane stop of `review_mode_unavailable` now names the stage's local review role,
`reviewer.harness` and their families, and says whether they are of one family (`review_mode.mixed_lane_reason`, used in
`job._choose_review`). `manual` and every single-family lane are unchanged. The change is `chore:`, so nothing is released.
The plan is archived at `docs/milestones/completed/workflow-controller-reviewer-independence.md`.

## Next action

Next incomplete milestone in `docs/ROADMAP.md`: H9b (the selection of a fallback chain's entry, depends on H9a, `feat:`; its open question is the narrow definition of "unavailable or held by a limit"). Run `/milestone-plan` to start planning.

## Functional review checklist

`workflow-controller-fallback-chain` (H9a, #197), implementation revision 1. Every flow runs offline from the product
clone root (read-only is fine: nothing writes inside it). Scratch files go in `$T`, a `mktemp -d` folder.

**Setup.** `T=$(mktemp -d)`; no build, no feature flag, no network, no `gh`/`claude`/`codex`/`ssh`.

**Test data.** Write these four files into `$T`:

- `ok.json`: `{"schema_version":1,"roles":{"review-plan":{"model":"claude-opus-5-5","fallback":[{"harness":"claude-code","model":"claude-sonnet-5-5"}]}}}`
- `bad-key.json`: `ok.json` with `"bogus":1` added inside the fallback entry.
- `bad-default.json`: `{"schema_version":1,"default":{"fallback":[{"harness":"claude-code","model":"x"}]}}`
- `settings-ok.json`: `{"routing":{"roles":{"review-plan":{"model":"claude-opus-5-5","fallback":[{"harness":"claude-code","model":"claude-sonnet-5-5"}]}}}}`; `settings-bad.json` is the same with `"bogus":1` in the entry.

**Flows.**

1. Valid chain loads. `python3 -I -c "import sys; sys.path.insert(0,'.'); from controller import routing; print(dict(routing.load_routing_config('$T/ok.json').chains))"`.
   Expected: prints `{'review-plan': (mappingproxy({'harness': 'claude-code', 'model': 'claude-sonnet-5-5'}),)}`, no error.
2. Unknown key inside a fallback entry is refused (routing config). Same command on `bad-key.json`. Expected: refusal naming
   `roles.review-plan.fallback[1].bogus` and listing `['harness', 'model', 'effort']`.
3. `default` takes no chain. Same command on `bad-default.json`. Expected: refusal `default.fallback is not allowed`, with the reason.
4. Settings file accepts the chain. `python3 -m controller --settings $T/settings-ok.json settings show`. Expected: exit 0, the usual
   `key = value (source)` list, no error.
5. Settings file refuses an unknown fallback key. Same on `settings-bad.json`. Expected: non-zero exit and
   `error: ... routing.roles.review-plan.fallback[1].bogus is not a key of a fallback entry ...`.
6. Entry validation matches the first route's. Edit `ok.json`: set the fallback `harness` to `nonesuch`, then drop `model`, then set
   `effort` to `-x`, one at a time. Expected: each is refused at load, naming the role and position `fallback[1]`.
7. CLI overrides change only the first route. Run `python3 -m controller --routing-config $T/ok.json --role-model review-plan=claude-haiku-5-5 explain` (or any read-only command that
   resolves routing) and check the chain's later entry still says `claude-sonnet-5-5`. Expected: first route is haiku, the fallback entry is untouched.
8. Review-mode family count. In Python, build options with a chain whose fallback entry is on a different-family harness for a worker role
   and call `review_mode.worker_families(options)`; expected: the fallback's family is included. `review_mode.mixed_lane_reason` on a
   review role with a chain should read "... falls back to <harness> (<family>)".
9. Docs. Read `docs/guide/commands.md` ("Worker routing") and `docs/guide/runtime.md` (settings, table generation 17); expected: they
   match flows 1-5 and say the chain is not yet used to select a route. Run `python3 tools/check_docs.py`; expected: clean.
10. Regression. `python3 tests/run_tests.py tests.test_routing_chain tests.test_settings` (not backgrounded); expected: pass.

**Known limitations / out of scope.** Nothing selects a fallback entry: a worker always launches on the first route (H9b, which
also defines "unavailable or held by a limit"). No live `claude`/`codex` run, no pull request and no release are involved. A shared
settings file written by this release records `"_table_generation": 17`; an older Controller ignores `fallback` with a warning.
