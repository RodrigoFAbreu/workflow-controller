# Active Milestone

## Status

**Implementation complete, in review.** `workflow-controller-routing-harness` (H1, issue #158): a routing entry
names a harness. Plan: `docs/ai-workflow/CONTROLLER_ROUTING_HARNESS_PLAN.md`. CP1 (the field, its validation, settings
generation 15), CP2 (`--role-harness`, the route record) and CP3 (the guides and the roadmap) are complete, followed
by the self-review and the full verification. The previous milestone, `workflow-controller-reviewer-brief` (RB1,
#150), is archived at `docs/milestones/completed/workflow-controller-reviewer-brief.md`.

## Next action

The implementation review bundle is generated: the local implementation review runs next. H6 follows H1.

## Functional review checklist

`workflow-controller-routing-harness` (H1, #158), implementation revision 1. Every flow runs offline: no network, no
`gh`, no `claude`/`codex`/`ssh`, nothing written inside the clone. A scratch folder `$W` holds the files; `$H` is the
repository clone (read-only).

**Setup.**

```sh
W=$(mktemp -d); cd "$W"
export PYTHONPATH=$H XDG_CONFIG_HOME=$W/xdg
ctl() { python3 -m controller "$@"; echo "[rc=$?]"; }
```

**Test data** (files in `$W`):

```sh
echo '{"schema_version":1,"default":{"harness":"claude-code"},"roles":{"review-plan":{"harness":"claude-code","model":"sonnet"}}}' > ok.json
echo '{"schema_version":1,"default":{},"roles":{"review-plan":{"harness":"codex"}}}' > codex-role.json
echo '{"routing":{"default":{"harness":"codex"}}}' > codex-default-settings.json
echo '{"routing":{"roles":{"review-plan":{"harness":"claude-code"}}}}' > ok-settings.json
```

| # | Flow | Expected |
| --- | --- | --- |
| 1 | `ctl --routing-config ok.json settings show` | rc 0; the `routing` line shows `harness: claude-code` in `default` and in `roles.review-plan` beside `model: sonnet`, source `(cli)` |
| 2 | `ctl --routing-config codex-role.json settings show` | rc 20; `roles.review-plan.harness: role 'review-plan' runs as a 'worker' and harness 'codex' declares only ['reviewer']` |
| 3 | `ctl --settings codex-default-settings.json settings show` | rc 20; refused at `routing.default.harness`, naming the first uncovered role (`apply-functional-review`) and `(and 14 further role(s) the default would apply to)` |
| 4 | `ctl --settings codex-default-settings.json settings clean`, then `cat codex-default-settings.json` | rc 20, same refusal; the file is unchanged |
| 5 | `ctl --settings ok-settings.json settings show` | rc 0; the settings file accepts `claude-code` for a role, source `(file)` |
| 6 | `ctl --role-harness review-plan=codex settings show` | rc 2 (usage error), before anything runs; same pair message |
| 7 | `ctl --role-harness review-plan=claude settings show` | rc 2; `expected a registered harness name, got 'claude'; registered: ['claude-code', 'codex']` (the reviewer alias is not accepted) |
| 8 | `ctl --role-harness bogus=claude-code settings show` | rc 2; `unknown role 'bogus'` and the list of 15 known roles |
| 9 | `ctl --role-harness review-plan=claude-code --role-harness review-plan=claude-code settings show` | rc 2; `role 'review-plan' is assigned more than once` |
| 10 | `ctl --role-harness review-plan=claude-code settings show` | rc 0 |
| 11 | `ctl --help` | lists `--role-harness ROLE=HARNESS`; no `--harness` option exists |
| 12 | Docs: `python3 tools/check_docs.py` (run in the clone, read-only) and read the "The routing section" part of `docs/guide/runtime.md` | passes; the `harness` field, the accepted pairs (claude-code for all 15 roles, codex for none yet and why), the load-time refusal and the generation 15 paragraph are present and match flows 1-10; every list of routing options in `docs/guide/` also lists `--role-harness` |

**Not runnable in the sandbox (covered by the automated suite, not by this review).** The resolved harness in a real
job record (`worker_route.harness` is `claude-code`, `sources.harness` is `default` or `config-role`, and the tester
record writes `claude-code`, not the alias `claude`) needs a launched worker, so it needs the real `claude`. The
precedence of the four sources (`role-cli` > `config-role` > `config-default` > built-in) is tested in
`tests/test_routing.py`.

**Known limitations / out of scope.** No launch code: every role still runs on Claude Code and `codex` is refused for
every routing role until a later item (H3, H4a-H4g, C10g) lets it run. No `--harness` global option, no model or
effort check against the harness (H8), `explain` prints no routes. A `default.harness` is checked against every role
without its own entry and is refused even when `--role-harness` would override all of them.
