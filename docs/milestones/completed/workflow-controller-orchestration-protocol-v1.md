# Archived milestone narrative — `workflow-controller-orchestration-protocol-v1`

Archived verbatim from `docs/ACTIVE_MILESTONE.md` on 2026-10-03, as part of
`/accept-milestone`'s own step 5, at this work item's own acceptance. This
file's content below this notice is unedited from the version
`docs/ACTIVE_MILESTONE.md` carried immediately before this acceptance
(functional review round 1, implementation revision 4: checklist evidence
commit `f668606f59bd0006f3667b628133f4ff4a472e04`; the deferred follow-ups
and the post-acceptance release steps are listed in
`docs/ACTIVE_MILESTONE.md`'s completion status, not here). Its
`## Release notes` section holds the 1.7.0 release notes. Its `## Status`
section still describes the previous milestone (C4) as it did before. The
plan, registry, artifacts and mapping files and the code stay at their
original paths.

---

# Active Milestone

## In progress: `workflow-controller-orchestration-protocol-v1` (ROADMAP step C9, release 1.7.0)

Plan revision 11 is approved (`8b29d66`, amendment 0: the artifacts declaration and the checkpoint anchors only; the design is unchanged, so each checkpoint is revalidated against its anchored section). Implementation runs one checkpoint per session;
`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for which are `COMPLETE`. The section
below describes the previous milestone (C4) and is kept until this one is accepted.

**Status:** implementation revision 4 has technical approval (`25adda4`, `EXTERNAL_APPROVE` of bundle `6f6770bd`); the work item is at
`AWAITING_FUNCTIONAL_REVIEW`, round 1. The checklist is the next section.

- **CP1, the protocol client** -- `controller/protocol.py` runs one Workflow Orchestration Protocol
  operation (`--protocol-major 1`, `--repo-root`) from a private copy of the Workflow's own script
  set, taken from the installation record's `managed` map (`scripts/<name>.py`, not `_test`), with a
  path-to-digest map derived afresh for every call and the ADR 0006 Git isolation that
  `workflow_contract.run_in_private_copy` now shares. `controller/protocol_schema.py` validates every
  envelope and result against the vendored 2.7.0 schema (`controller/protocol_schema.json`, sha256
  pinned) with a stdlib validator for the schema's keyword subset: an unsupported schema keyword is
  refused at load, and for documents `additionalProperties: false` and the minor-extensible enums are
  open, so a protocol 1.1 answer validates. `WORKFLOW_PROTOCOL_FAILED` and
  `WORKFLOW_PROTOCOL_UNSUPPORTED` (exit 20) are the new refusals. `tests/workflow_releases/2.7.0/`
  vendors the published archive (`tools/workflow_releases.py sync --archive-sha256`), including the
  protocol script, its sibling `workflow_test_harness.py` and the schema.
  Tests: `tests/test_protocol.py` (including a round trip against the real 2.7.0 scripts),
  `tests/test_protocol_schema.py`.
  Revalidated against plan revision 11 (the CP1 section is unchanged by the amendment): the 69
  protocol, schema and package-structure tests pass.
- **CP2, admission by capability** -- `managed_repo.inspect`'s version gate is two-way: a release with
  a `RELEASE_CONTRACTS` entry (2.5.1, 2.6.0) is legacy mode exactly as before; any other release is
  protocol mode when the installation record's `managed` map lists `scripts/workflow_protocol.py` and
  `describe` answers protocol major 1, else refused (`no_protocol` for a release newer than the
  supported lines, `unsupported_protocol_major`, the unchanged `outside_supported_line` /
  `unvalidated_release`). `ManagedRepository` gains `target_protocol` and `script_digests`;
  `protocol.identity()` is the one identity function (the release `describe` reports and the managed
  script digest map, derived afresh; a non-Workflow `scripts/extra.py` never enters it). The job pin,
  the per-decision re-check and `WORKFLOW_RELEASE_CHANGED` are CP4's.
  Tests: `tests/test_managed_repo.py` (`ProtocolAdmissionTest`, `ProtocolIdentityTest`); the 2.7.0
  case in `test_managed_repo` and `test_cli` now expects `no_protocol` on purpose.

- **CP3, decisions from `next-action`** -- `controller/protocol_decision.py` turns the Workflow's own
  `next-action` answer into the existing `Decision` (plus a `protocol` attribute carrying the answer as
  received, its row, state identity and release). `automatic` with a known action id, a worker role the
  Controller knows and a non-`user_only` worker launches; `human_gate`/`external_gate` and `blocked` are
  gates carrying the Workflow's reason, remedy and alternatives; `complete` is the no-action outcome;
  `validation` and any unknown disposition, action id or worker role are blocked gates
  (`workflow_unknown_*`). `PROTOCOL_ACTIONS` names the twelve automatic ids with a command token and a
  route key; the worker's command is rendered from it (`/<command> <work_item_id>`, plus the Controller's
  own base for `plan.start`), and an `invocation` that differs from the rendering is blocked
  `workflow_invocation_mismatch`. Two routing roles, `prepare-functional-review` (inherit) and
  `apply-functional-review` (Opus), join `routing.ROLE_ROUTES`/`ROLES` only (not `ROLE_BY_COMMAND_STEM`),
  and `settings.TABLE_GENERATION` is 3. 1.6.0's committed-state gate applies in its own scope only.
  A protocol target never enters `evidence.decide` (`job._execute_step_locked`, `cli explain`); its
  pre-state reads no lifecycle file. `explain` shows the row, disposition and action (additive).
  **Interim, removed by CP5:** `job._protocol_launch_unavailable` declines every protocol launch, because
  nothing verifies a protocol job's outcome until CP5's protocol branch of `_verify_transition`; the job
  record's `protocol` block and the currency checks are CP4's.
  The equivalence comparison (`tests/golden/generate_protocol_vs_legacy_differences.py`, table
  `tests/golden/protocol_vs_legacy_differences.json`) runs 58 fixture repositories through 1.6.0 and the
  protocol and holds exactly the seven observable differences D1-D7, each asserted by name.
  Tests: `tests/test_protocol_decision.py`, `tests/test_protocol_equivalence.py`; the pins changed on
  purpose are `tests/test_routing.py` (the role set) and `tests/test_settings.py` (`V1_ROLES`,
  `TABLE_GENERATION == 3`, plus a file filled by this release read by a 1.6.0-shaped release).
  `tests/test_job.py`'s `worker_result_prose_feeds_only_the_report` now skips `protocol.py`, whose
  `Envelope.result` is the protocol's answer (it failed at CP1/CP2). Full suite: 2939 tests pass.

- **CP4, the stale check, the job record and the loop guard** -- a protocol job's record gains the optional
  `protocol` block (`job._protocol_block`: the decision as received, `envelope_digest` -- the sha256 of the
  answer's result document, `action_id`, `state_identity`, `workflow_release`, `protocol_version`,
  `script_sha256` and `reconcile`, filled by CP5); a legacy record has no such key. `protocol_decision.
  check_currency` is the one currency check: `next-action --expect-state-identity` for an item decision
  (a `stale_decision` refusal, or an answer that differs on row, disposition, action id or arguments, is
  stale), and for `plan.start` a no-item re-call whose row, disposition, action id and
  `snapshot.work_item_ids` must be equal. `job._decide_protocol_current` runs it before the `PLANNED` write
  and re-decides from a fresh state read, at most three decisions per step, then the `decision_unstable`
  gate naming the last two identities (or work item id lists); `_launch_job` runs it again immediately
  before `worker.launch` and a stale answer is a terminal `FAILED` record with
  `reconciliation_evidence.code` `decision_stale_at_launch` (no worker, nothing for `resume`).
  `job._no_progress_gate` is the generic `no_progress_repeated` guard keyed on `(work item, action id)`
  (a `FAILED` or interrupted job and one not yet reconciled are skipped, progress or a gate resets);
  it reads `protocol.reconcile.class` and a `FINISHED` status, which CP5 writes.
  `_refuse_changed_release` also re-derives a protocol target's identity (`protocol.identity`) after the
  preflight and refuses `WORKFLOW_RELEASE_CHANGED` when the release or a managed script's digest differs
  from the one `inspect` admitted, or when the protocol script no longer answers; a non-Workflow
  `scripts/extra.py` never enters it. A `resume` of a job recorded under another release is the existing
  `workflow_release_changed` verification failure. **Interim, removed by CP5:** `job.PROTOCOL_LAUNCH_ENABLED`
  is `False`, so a protocol decision that would launch is still declined (the checks above run only when it
  is on, which the tests set); CP5 deletes the switch with `_protocol_launch_unavailable`.
  Tests: `tests/test_protocol_job.py`.

- **CP5, outcomes from `reconcile`** -- a protocol job's outcome is the Workflow's own `reconcile`, on launch
  and on `resume` (`job._protocol_resolution`, shared by `_launch_job`, `_reconcile_launched` and
  `_reconcile_completed`; a record with no `protocol` block still takes the legacy branch, so 1.6.0 records
  resume unchanged). Order: the release identity is re-derived first (`_protocol_release_change`: the release
  and the managed-script digest map against the record's) and a difference is a terminal `FAILED`
  `workflow_release_changed` with `reconcile` never called, whatever the worker did; then a failed or timed-out
  worker is `FAILED` `worker_outcome` (reconcile not called); then `reconcile --decision` runs on the stored
  decision (written to `jobs/<job_id>/decision.json`). `progress` and `gate_reached` verify and store the
  answer in `protocol.reconcile`; a successful `no_progress` is terminal `FINISHED` with `protocol.progress:
  "none"` (no `resume` is needed; the loop guard counts it); an interrupted or unrecorded worker with
  `no_progress` is `INTERRUPTED`; `invalid` is `FAILED` `reconcile_invalid` with the reasons verbatim; a
  failing `reconcile` or an unknown class is `FAILED` `workflow_protocol_failed`. After a `progress` for
  `implementation.checkpoint` the Controller keeps one committed-state fact
  (`evidence.uncommitted_implementation_state`): a non-empty result is `FAILED`
  `completion_not_committed_at_head` with the facts. The interim `job.PROTOCOL_LAUNCH_ENABLED` switch and
  `_protocol_launch_unavailable` are deleted: a protocol decision now launches.
  `managed_repo.inspect_for_resume` is the drift-tolerant `resume` path: on `DriftedInstallationError` alone it
  yields a repository carrying the error in `drift`, and `job.resume` then ends a pending protocol job whose
  managed-file digest map (`protocol.managed_digests`, from the manifest and the files' bytes, no script run)
  differs from its record's as `FAILED` `workflow_release_changed`; an equal map, a legacy record or nothing
  pending re-raises the drift before any write. `resume --abandon` keeps the strict check.
  Tests: `tests/test_protocol_outcome.py` (each class and invalid reason, the release-identity cases, the
  checkpoint-completion cases on a 2.7.0 fixture, resume at each crash point, the drifted resume through
  `job.resume` and `cli.cmd_resume`); `tests/test_protocol_decision.py`'s two job-wiring tests now expect a
  reconciled `FINISHED` job. Full suite: 2993 tests pass (one `test_evidence` stderr match fails only under
  `FORCE_COLOR=3`).

- **CP6, both modes end to end** -- `tests/test_protocol_lifecycle.py` drives a disposable repository
  through the real `job.execute_step` from a bound plan bundle to `MILESTONE_COMPLETE` and the next
  milestone's `/milestone-plan` launch. The fake worker is a `worker.launch` double whose effects are the
  target's own installed writers and generator (checkpoint commit, bundle generation, local and manual
  review records with their `REVIEW_FEEDBACK.md`, the functional-review checklist evidence commit); the
  user-only steps (the manual external verdicts, plan approval, technical approval, acceptance) are the test's.
  On the vendored 2.7.0 (protocol mode) every launch carries the `protocol` block and a `reconcile` class
  (`gate_reached`, `progress`, `progress`, `gate_reached`, `gate_reached`), the user gates launch nothing, and
  the Workflow's own `next-action` ends at `complete`. On 2.6.0 (legacy mode) the lifecycle ends the same way
  with no `protocol` block and the same launches except `prepare-functional-review`, which 1.6.0 gates and
  the test performs as the user (difference D6). A repository updated 2.6.0 -> 2.7.0 between steps: the step
  admitted under 2.6.0 raises `WorkflowReleaseChangedError` (`admitted` 2.6.0, `installed` 2.7.0) with no
  worker and no record, and the next run completes in protocol mode. The three golden generators other than
  the plan-stage one and the 2.6.0 plan-stage golden pass `--check` byte for byte.
  **For CP7:** `generate_plan_stage_decisions.py --check` (default 2.5.1 file) fails on the unmodified base
  `f2ca24d` and at every C9 checkpoint, because the `AMENDING_PLAN` cases carry a permitted difference
  that `tests/test_golden_plan_stage_decisions.py` names and asserts; that test, not the bare `--check`, is
  its check, and the golden is not rewritten.

- **CP7, documentation and full verification (terminal)** -- ADR 0010
  (`docs/adr/0010-orchestration-protocol-admission-by-capability.md`, amends ADR 0006), the guides
  (`concepts`, `automation`, `installation`, `commands`, `troubleshooting`, `development`, and the
  routing, settings and telemetry mentions of the two new roles), `README.md`, `docs/README.md`, and
  the `## Release notes` section below, checked by `release_notes.notes_problem` and
  `paragraph_problem`. `inspect`'s `repository` object now carries `workflow_mode`, `target_protocol`
  and `script_digests` for a protocol target only (plan F; CP2 had not built it, a legacy target's
  output is unchanged; `tests.test_protocol_lifecycle.InspectRepositoryBlockTest`). Verified: the
  full suite passes (2999 tests, `tools/run_tests.py` under the reaping wrapper); the
  golden generators pass `--check` except the default-file `generate_plan_stage_decisions.py`, whose
  permitted `AMENDING_PLAN` difference `tests.test_golden_plan_stage_decisions` asserts (it fails
  identically at the base).

- **Self-review (SELF_REVIEWING_IMPLEMENTATION)** -- the full milestone diff reviewed against the plan.
  No Blocking findings; three Important gaps between the approved design and the code, each fixed with
  tests:
  I1 (plan A.3): a step's protocol preflight never ran the Workflow's `verify`. `protocol_decision.
  health_gate` now runs it before every protocol decision (`job._decide_protocol_current`, `cli explain`);
  an unhealthy answer is the `workflow_unhealthy` gate naming each failing check and its detail, and
  nothing is decided or launched.
  I2 (plan C.3): `describe`'s action ids were never compared with `PROTOCOL_ACTIONS` at admission.
  `inspect` now reports `unknown_action_ids` (an advisory, never a refusal): the ids `describe` lists that
  are neither in the table nor in the vendored schema's catalogue, whose other ids are gates the Controller
  never launches by design (empty for 2.7.0).
  I3 (plan F): `status` did not show a protocol job's reconcile class or invalid reasons. Its job entries
  now carry `reconcile_class` and `invalid_reasons`, and the line shows `[reconcile <class>: <codes>]`,
  for a protocol record only (a legacy entry gains no key).
  Tests: `tests.test_protocol_decision` (`JobWiringTest`'s unhealthy-verify case on a real 2.7.0 fixture,
  `ActionTableTest`'s advisory case), `tests.test_protocol_lifecycle.InspectRepositoryBlockTest`,
  `tests.test_observe.StatusJobSummaryTest`; guides (`automation`, `commands`, `troubleshooting`), ADR 0010
  and the release notes updated.

## Functional review checklist

Round 1, implementation revision 4: technical approval `25adda4` (`EXTERNAL_APPROVE` of bundle `6f6770bd`,
review content id `a9429a2b`, reviewed implementation head `9919f92`), PR #21 (draft) at `25adda4`. The
milestone is C9 (`docs/ROADMAP.md` step C9, ships 1.7.0): the Controller drives a Workflow that answers
Orchestration Protocol major 1 (Workflow 2.7.0) through `next-action` and `reconcile`, and 2.5.1 and 2.6.0
behave exactly as under 1.6.0. Every expected result below was measured on 2026-10-03 against `25adda4`
by running the blocks as written, in a scratch directory `/tmp/c9-fr`; the helpers print the target's own
paths as `<R>` (its repository), `<T>` (its directory) and `<S>` (the scratch root), job ids as `<job>`,
times as `<t>` and hashes as `<sha>`/`<sha256>`. The flows were run twice and the transcripts compared;
they are identical.

**What the flows use.** A scratch target is a real Workflow tree vendored under `tests/workflow_releases/`
(2.7.0, 2.6.0 or 2.5.1), seeded by that Workflow's own writers up to a bound plan bundle (the same
fixtures `tests/test_protocol_lifecycle.py` uses), driven by the **built-from-the-branch** Controller
(`$W`), a **fake worker** (`claude-fake`, in front of `tests/fake_claude.py`) that leaves the effect of the
Workflow command it was launched for, and the Manager's `verify`/`status` stubbed offline. The user-only
steps (the manual external verdicts, plan and technical approval, acceptance) are performed by the driver
with the Workflow's own writers, not by the real commands. Two flows need a Workflow answer a real 2.7.0
never gives (an unknown disposition, a script edited mid-run, someone else writing the state between two
calls): for them the target's `scripts/workflow_protocol.py` carries a small **shim** that obeys a
`shim.json` beside the repository and otherwise changes nothing; it is committed with its digest in the
installation record, so the Controller sees it as the Workflow. No real model, no GitHub write, no
Workflow Manager: the only GitHub access is the read-only `gh pr view 21` / `gh pr checks 21` of flow O.

**Round 1 focus, by plan decision.** D1 admission by capability: B. D2 decisions from `next-action`, the
fail-closed handling and the invocation-mismatch gate: C, E. Pre-spawn state-identity checks: F. Outcomes
from `reconcile`, the terminal successful no-progress: D, G. The loop guard: H. Bound executed bytes: I.
Drift-tolerant resume: J. The three new automatic launches D5-D7 and the other differences D1-D4:
K. Legacy behaviour unchanged (decision 3's "2.5.1/2.6.0 exactly as 1.6.0"): A, D3. The settings
generation and routing roles: L. The implementation-review rounds' regressions (same-second ordering,
unreadable-unchanged scripts, executed bytes, no global Git identity): M and Q.

**Automated state.** The last full run (revision 4, `tools/run_tests.py`) ran 3011 tests in 7 shards, every
shard PASS with exact coverage; flow Q reruns it without any global Git identity and flow M the
milestone's modules. Nothing changed after the reviewed code (`9919f92`) except
`docs/ai-workflow/WORKFLOW_STATE.json` and this narrative. PR #21's CI at the pushed head `25adda4` is
the full suite plus `workflow-conformance` (flow O: all twelve checks pass).

Nothing in the flows writes to this repository, its remote, GitHub, the user's settings file
(`~/.config/workflow-controller/`) or the shared runtime root (`~/.local/state/workflow-controller/`), and
no flow uses the installed Controller (1.5.0) other than to compare with it. Every command runs under
`/tmp/c9-fr`, with `XDG_CONFIG_HOME` and `XDG_STATE_HOME` pointing into it, an explicit `--runtime-dir`
and an explicit `--settings` file per target. This repository is only read (flows A, M, N, P and Q; the
build is a clone at the pinned head).

### Setup

Run each block below as one non-interactive `bash` script (for example, save it to a file and run
`bash <file>`), in the order given: not in `zsh`, and not pasted line by line. Every block after setup
starts with `. /tmp/c9-fr/fr.sh`, which unsets `FORCE_COLOR`, `PYTHONPATH`, `WORKFLOW_CONTROLLER_SETTINGS`,
`GH_TOKEN`, `GITHUB_OUTPUT` and `FAKE_GH_FAIL`. Never set `PYTHONPATH=.`, and run everything in the
foreground. Each flow builds its own targets, so the flows are independent and can run in any order
after setup; a rerun replaces a target of the same name. Flows M and Q take a few minutes (about 4 and
4.5); the rest take seconds to two minutes (D and K about 20 s each).

**Setup.** The scratch directory, the helpers, the driver, the fake worker and a reaping-subreaper
wrapper (all saved from this file, below), a clone of this repository pinned at the head `25adda4`, and a
local build of it in a throwaway virtual environment:

```bash
S=/tmp/c9-fr; R0=/home/rodrigo/Workspace/workflow-controller; H=25adda4a2d9c20c912fb1d67e9d48f91f4d393e5
rm -rf "$S"; mkdir -p "$S"; cd "$S"
for f in fr.sh drive.py claude-fake reap.py; do
  awk -v f="$f" '$0 == "<!-- " f " begin -->" {on = 1; next} $0 == "<!-- " f " end -->" {on = 0} on' \
    "$R0/docs/ACTIVE_MILESTONE.md" | sed '1d;$d' > "$S/$f"
done
chmod +x "$S/claude-fake"
git clone -q "$R0" "$S/src" && git -C "$S/src" checkout -q --detach "$H"
python3 -m venv "$S/venv" && "$S/venv/bin/pip" -q install "$S/src"
. "$S/fr.sh"
head -1 "$S/drive.py"; grep -c . "$S/fr.sh" "$S/claude-fake"; "$W" --version
fresh smoke p-ready; wcx inspect "$R" | norm | head -2; git -C "$R" log --oneline | wc -l
```
Expected: `"""Functional-review driver for workflow-controller-orchestration-protocol-v1:`, the non-empty line
counts `/tmp/c9-fr/fr.sh:50` and `/tmp/c9-fr/claude-fake:48`, `workflow-controller 1.6.0` and
`runtime: package (local build from 25adda4a2d9c)` (the version is tag-derived until `v1.7.0` exists),
then `repository: <R> (Workflow 2.7.0, profile full)`, `workflow mode: protocol (protocol 1.0, 4 managed scripts)`
and the smoke target's commit count `1`.

The helpers (`fr.sh`), the driver (`drive.py`), the fake worker (`claude-fake`) and the reaping wrapper
(`reap.py`), extracted by the setup block:

<!-- fr.sh begin -->
```bash
unset FORCE_COLOR PYTHONPATH WORKFLOW_CONTROLLER_SETTINGS GH_TOKEN GITHUB_OUTPUT FAKE_GH_FAIL
export S=/tmp/c9-fr R0=/home/rodrigo/Workspace/workflow-controller
export W=$S/venv/bin/workflow-controller XDG_CONFIG_HOME=$S/xdg XDG_STATE_HOME=$S/xdg-state
export H=25adda4a2d9c20c912fb1d67e9d48f91f4d393e5
mkdir -p "$XDG_CONFIG_HOME" "$XDG_STATE_HOME"
# drive new NAME KIND | user ROOT STEP | ...: scratch targets, the user-only steps (see drive.py)
drive() { (cd /tmp && python3 "$S/drive.py" "$@"); }
# use NAME: load a target ($T its directory, $R its repository, $RT its runtime root, $SM its stub manager, $WID its work item)
use() { . "$S/t/$1/env"; N=$1; }
# fresh NAME KIND: a new scratch target, loaded; usr STEP: a user-only step on it; mode MODE: what the fake worker does next
fresh() { drive new "$1" "$2" >/dev/null; use "$1"; }
usr() { drive user "$R" "$@"; }
mode() { echo "$1" > "$T/mode"; }
# wcx ARGS: the local build against the loaded target: its own runtime root, settings file and stub Workflow Manager, the fake worker
wcx() { (cd /tmp && "$W" --runtime-dir "$RT" --settings "$T/settings.json" --workflow-manager "$SM" \
  --claude-binary "$S/claude-fake" --timeout 60 "$@"); }
step() { wcx step "$R"; echo "step exit $?"; }
explain() { wcx explain "$R" | tail -n +2 | norm; }
# norm: the loaded target's paths, job ids, times and hashes as placeholders
norm() { sed -e "s#${R:-/nonexistent}#<R>#g" -e "s#${T:-/nonexistent}#<T>#g" -e "s#$S#<S>#g" \
  -e 's/20[0-9][0-9][01][0-9][0-3][0-9]T[0-9]*Z-[0-9a-f]\{8\}/<job>/g' -e 's/20[0-9][0-9]-[01][0-9]-[0-3][0-9]T[0-9:]*Z/<t>/g' \
  -e 's/wall [0-9]* s/wall <n> s/' -e 's/\b[0-9a-f]\{64\}\b/<sha256>/g' -e 's/\b[0-9a-f]\{40\}\b/<sha>/g'; }
# jobs: every job record of the loaded target, oldest first: status | command | action id | reconcile class | progress | reason | gate text
jobs() { python3 - "$RT" <<'PY' | norm | cut -c1-${JOBS_WIDTH:-230}
import glob, json, os, sys
for path in sorted(glob.glob(sys.argv[1] + "/jobs/*.json"), key=lambda p: os.stat(p).st_mtime_ns):
    r = json.load(open(path))
    p = r.get("protocol") or {}
    rc = p.get("reconcile") or {}
    ev = r.get("reconciliation_evidence") or {}
    g = r.get("human_gate_pending") or {}
    print(r["status"], "|", r["selected_action"]["command"] if r.get("selected_action") else None, "|", p.get("action_id"), "|",
          rc.get("class"), "|", p.get("progress"), "|", ev.get("reason"), ",".join(i["code"] for i in ev.get("invalid_reasons") or []), "|",
          g.get("what_is_required") or "")
PY
}
# gatetext: the newest job record's status and its gate text (or its reconciliation evidence)
gatetext() { python3 - "$RT" <<'PY' | norm
import glob, json, os, sys
r = json.load(open(max(glob.glob(sys.argv[1] + "/jobs/*.json"), key=lambda p: os.stat(p).st_mtime_ns)))
print(r["status"], "--", (r.get("human_gate_pending") or {}).get("what_is_required") or json.dumps(r.get("reconciliation_evidence"), sort_keys=True))
PY
}
# launched: how many workers the fake worker was started for in the loaded target
launched() { echo "workers launched: $([ -e "$T/tasks.log" ] && wc -l < "$T/tasks.log" || echo 0)"; }
# shimmed NAME JSON: a plan-stage 2.7.0 target whose protocol script obeys $T/shim.json (see drive.py)
shimmed() { fresh "$1" p-ready; drive shim "$1" >/dev/null; echo "$2" > "$T/shim.json"; }
# impl NAME JSON: the same, past the plan approval: at IMPLEMENTING, an empty runtime root, the shim counting from zero
impl() { fresh "$1" p-ready; drive shim "$1" >/dev/null; echo '{}' > "$T/shim.json"; wcx step "$R"; usr plan-gate >/dev/null; usr approve-plan >/dev/null
  rm -f "$T/shim.count" "$T/tasks.log"; rm -rf "$RT"; mkdir "$RT"; echo "$2" > "$T/shim.json"; }
```
<!-- fr.sh end -->
<!-- drive.py begin -->
```python
"""Functional-review driver for workflow-controller-orchestration-protocol-v1:
disposable scratch targets (a real Workflow 2.7.0, 2.6.0 or 2.5.1 tree, seeded
by that Workflow's own writers), the user-only steps a person would perform,
the effect of each Workflow command the fake worker is launched for, and a
protocol shim for the scenarios a real Workflow cannot be made to produce.
Run as: python3 $S/drive.py <command> ...   (commands: new, user, effect,
invalid, bump, shim)"""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

S = Path(os.environ.get("S", "/tmp/c9-fr"))
sys.path.insert(0, str(S / "src"))
from tests import fixtures  # noqa: E402
from tests import test_protocol_lifecycle as life  # noqa: E402
from tests.golden import generate_protocol_vs_legacy_differences as gen  # noqa: E402

# The lifecycle test's review writer, also leaving the plan stage's REVIEW_FEEDBACK.md (as /review-plan does).
PLAN_REVIEW = life._PLAN_REVIEW.replace(
    'if stage == "implementation":\n    role = "LOCAL_MODEL_IMPLEMENTATION_REVIEW" if sys.argv[4] == "local" else "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"',
    'if True:\n    role = ("LOCAL_MODEL" if sys.argv[4] == "local" else "MANUAL_EXTERNAL") + "_" + stage.upper() + "_REVIEW"')
assert PLAN_REVIEW != life._PLAN_REVIEW
RELEASES = {"p": "2.7.0", "l": "2.6.0", "o": "2.5.1"}


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def workflow(root, code, *args):
    """Run ``code`` inside the target with its own installed Workflow scripts importable."""
    return fixtures.run_workflow_python(root, "import json\n" + code, *args).stdout


def wid_of(root):
    return (Path(root).parent / "env").read_text().split("WID=")[1].split()[0]


def phase_of(root):
    state = json.loads((Path(root) / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
    return state["work_items"][wid_of(root)]["phase"]


def new(name, kind):
    """kind: p-ready, l-ready, o-ready (a bound plan bundle under Workflow 2.7.0, 2.6.0, 2.5.1), or
    p:<scenario>, l:<scenario> (a scenario of tests/golden/generate_protocol_vs_legacy_differences.py:
    built under 2.6.0, then for p with Workflow 2.7.0 installed over it)."""
    mode, _, what = kind.replace("-", ":", 1).partition(":")
    release = RELEASES[mode]
    tdir = S / "t" / name
    shutil.rmtree(tdir, ignore_errors=True)
    tdir.mkdir(parents=True)
    wid = life.WID if what == "ready" else gen.WID
    if what == "ready":
        fixtures.seed_workflow_item(tdir / "target", release, "ready", work_item_id=wid)
    else:
        built = next(s for s in gen.SCENARIOS if s.id == what).make(str(tdir))
        assert built == tdir / "target", built
        if mode == "p":
            fixtures.install_workflow_release(built, "2.7.0")
            fixtures.commit_all(built, "Install Workflow 2.7.0")
    root = tdir / "target"
    git(root, "config", "user.email", "scratch@example.invalid")  # every scratch repository has its own identity
    git(root, "config", "user.name", "Scratch")
    fixtures.write_stub_workflow_manager(tdir / "wm", release=release)
    (tdir / "runtime").mkdir()
    (tdir / "env").write_text(f"export T={tdir} R={root} RT={tdir}/runtime SM={tdir}/wm WID={wid} REL={release}\n")
    print(f"-- driver: {name} ({release}, {what}); now run: use {name}")


def user(root, step):
    """The user-only steps (the manual external verdicts, the approvals, the acceptance, a pasted checklist)."""
    wid = wid_of(root)
    if step == "plan-gate":
        workflow(root, PLAN_REVIEW, wid, "plan", "APPROVE", "manual")
    elif step == "impl-gate":
        workflow(root, PLAN_REVIEW, wid, "implementation", "APPROVE", "manual")
    else:
        code = {"approve-plan": life._APPROVE_PLAN, "technical-approval": life._TECHNICAL_APPROVAL,
                "accept": life._ACCEPT, "checklist": life._FUNCTIONAL_CHECKLIST}[step]
        workflow(root, code, wid)
    print(f"-- driver: user step {step} done; phase {phase_of(root)}")


def effect(root, task):
    """What the launched Workflow command leaves behind (the lifecycle test's own table)."""
    root, wid = Path(root), wid_of(root)
    token = task.split()[0].lstrip("/")
    if token in life._EFFECTS:
        stage, verdict, how = life._EFFECTS[token]
        workflow(root, PLAN_REVIEW, wid, stage, verdict, how)
    elif token == "milestone-implement":
        workflow(root, life._GENERATE_BUNDLE if phase_of(root) == "SELF_REVIEWING_IMPLEMENTATION"
                 else life._CHECKPOINT, wid)
    elif token == "prepare-functional-review":
        workflow(root, life._FUNCTIONAL_CHECKLIST, wid)
    elif token in ("milestone-plan", "apply-implementation-review", "apply-functional-review"):
        pass  # only the launch is observed
    else:
        raise SystemExit(f"no scripted effect for {task!r}")


def bump(root):
    """Someone else writes the work item's state (state_revision + 1, through the Workflow's own writer)."""
    workflow(root, "import sys\nws.state_transaction(Path.cwd(), lambda s: (s['work_items'][sys.argv[1]].__setitem__("
                   "'state_revision', s['work_items'][sys.argv[1]].get('state_revision', 0) + 1), s)[1])", wid_of(root))
    print("-- driver: someone else wrote the work item's state")


# What the shim adds to the target's own scripts/workflow_protocol.py (committed, with its digest in the record).
SHIM = """

def _shim_before(argv):
    import subprocess
    from pathlib import Path as _P
    try:
        root = _P(argv[argv.index("--repo-root") + 1])
        cfg = json.loads((root.parent / "shim.json").read_text())
    except Exception:
        return None
    if "next-action" not in argv:
        return cfg
    counter = root.parent / "shim.count"
    n = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(n))
    act = cfg.get("at", {}).get(str(n))
    if act == "state":  # someone else writes the work item's state, just before the Nth next-action runs
        wid = argv[argv.index("--work-item") + 1]
        def bump(state):
            item = state["work_items"][wid]
            item["state_revision"] = item.get("state_revision", 0) + 1
            return state
        workflow_state.state_transaction(root, bump)
    elif act == "edit-script":  # a managed script is edited after the check that admitted it
        path = root / "scripts" / "workflow_state.py"
        path.write_text(path.read_text() + "\\n# edited between the check and the execution\\n")
    return cfg


def _shim_after(argv, body, cfg):
    if cfg and "next-action" in argv and body.get("ok") and cfg.get("override"):
        action = (body.get("result") or {}).get("action")
        for key, value in cfg["override"].items():
            if key == "disposition":
                body["result"]["disposition"] = value
            elif action is not None and key == "action_id":
                action["id"] = value
            elif action is not None and key == "invocation":
                action["invocation"] = value
    return body
"""


def shim(name):
    """Make the protocol script of target NAME consult $T/shim.json (it changes nothing without that file):
    ``at`` mutates the real repository just before the Nth next-action, ``override`` rewrites its answer."""
    root = S / "t" / name / "target"
    path = root / "scripts" / "workflow_protocol.py"
    text = path.read_text()
    old = "def main(argv: list[str] | None = None) -> int:\n    body, code = run(sys.argv[1:] if argv is None else argv)\n"
    new_main = ("def main(argv: list[str] | None = None) -> int:\n    _argv = sys.argv[1:] if argv is None else argv\n"
                "    _cfg = _shim_before(_argv)\n    body, code = run(_argv)\n    body = _shim_after(_argv, body, _cfg)\n")
    assert old in text
    path.write_text(text.replace(old, SHIM.lstrip("\n") + "\n\n" + new_main))
    record = root / ".workflow-manager" / "installation.json"
    data = json.loads(record.read_text())
    data["managed"]["scripts/workflow_protocol.py"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record.write_text(json.dumps(data, indent=2) + "\n")
    fixtures.commit_all(root, "Install the scratch protocol shim")
    wid = wid_of(root)
    if (root / ".ai-review" / wid / "current").exists() and phase_of(root) == "AWAITING_LOCAL_PLAN_REVIEW":
        # the commit moved HEAD: regenerate the plan bundle at it (the bundle is bound to the generating HEAD)
        base = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]["base_commit"]
        results = root / ".ai-review" / wid / "plan-inputs" / "TEST_RESULTS.md"
        results.write_text(f"stage: plan (revision 1)\nhead: {git(root, 'rev-parse', 'HEAD')}\n\nNo checks at the plan stage.\n")
        subprocess.run(["./scripts/prepare-ai-review.sh", base, "plan", wid], cwd=root, check=True,
                       capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    print(f"-- driver: {name}: protocol shim installed")


def main(argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "new":
        new(*args)
    elif cmd == "user":
        user(*args)
    elif cmd == "effect":
        effect(*args)
    elif cmd == "bump":
        bump(*args)
    elif cmd == "invalid":
        fixtures.update_workflow_state(Path(args[0]), wid_of(args[0]), phase="IMPLEMENTING")
    elif cmd == "shim":
        shim(*args)
    else:
        raise SystemExit(f"unknown command {cmd}")


main(sys.argv[1:])
```
<!-- drive.py end -->
<!-- claude-fake begin -->
```python
#!/usr/bin/env python3
"""Fake `claude` for the functional review: reads the task (the first stream-json user line on stdin) and
does what $T/mode says (default `effect`; $T is the directory above the working directory, the target's):
  effect   the effect of that Workflow command (driver.py effect), then the canned result of tests/fake_claude.py
  noop     nothing at all, then the canned result
  invalid  leaves the work item's phase illegal (IMPLEMENTING), then the canned result
  tamper   the effect, then an edit of the managed scripts/workflow_protocol.py
  crash    the effect, then SIGKILL of the Controller itself (the job record stays LAUNCHED)
  fail     exits 3 at once"""
import json, os, subprocess, sys, threading
from pathlib import Path
S = "/tmp/c9-fr"
line = sys.stdin.buffer.readline()
content = json.loads(line)["message"]["content"]
task = content if isinstance(content, str) else "".join(i.get("text", "") for i in content)
repo = Path.cwd()
mode_file = repo.parent / "mode"
mode = mode_file.read_text().strip() if mode_file.exists() else "effect"
with open(repo.parent / "tasks.log", "a") as log:
    log.write(task + "\n")
def drive(*args):
    subprocess.run([sys.executable, S + "/drive.py", *args], check=True, env={**os.environ, "S": S}, stdout=subprocess.DEVNULL)
if mode == "fail":
    sys.exit(3)
if mode == "invalid":
    drive("invalid", str(repo))
if mode in ("effect", "tamper", "crash"):
    drive("effect", str(repo), task)
if mode == "crash":
    os.kill(os.getppid(), 9)
    sys.exit(0)
if mode == "tamper":
    p = repo / "scripts" / "workflow_protocol.py"
    p.write_text(p.read_text() + "\n# edited by the worker\n")
child = subprocess.Popen([S + "/src/tests/fake_claude.py", *sys.argv[1:]], stdin=subprocess.PIPE)
child.stdin.write(line); child.stdin.flush()
def pump():
    try:
        for chunk in iter(lambda: sys.stdin.buffer.read1(4096), b""):
            child.stdin.write(chunk); child.stdin.flush()
    except (BrokenPipeError, ValueError):
        pass
    try:
        child.stdin.close()
    except OSError:
        pass
threading.Thread(target=pump, daemon=True).start()
sys.exit(child.wait())
```
<!-- claude-fake end -->
<!-- reap.py begin -->
```python
"""Run argv as a child under a reaping subreaper: orphans re-parented here are collected."""
import ctypes, os, sys
ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
pid = os.fork()
if pid == 0:
    os.execvp(sys.argv[1], sys.argv[1:])
status = 1
while True:
    try:
        reaped, st = os.waitpid(-1, 0)
    except ChildProcessError:
        break
    if reaped == pid:
        status = os.waitstatus_to_exitcode(st)
        break
sys.exit(status)
```
<!-- reap.py end -->

No other test data is needed: every flow builds its own targets with `fresh` (a kind is `p-ready`,
`l-ready` or `o-ready`, a bound plan bundle under Workflow 2.7.0, 2.6.0 or 2.5.1, or `p:<scenario>` /
`l:<scenario>`, a scenario of `tests/golden/generate_protocol_vs_legacy_differences.py` built under 2.6.0
and, for `p`, with 2.7.0 installed over it); `mode` says what the fake worker does next (`effect`, the
default, `noop`, `invalid`, `tamper`, `crash`, `fail`).

### Flows

**A. The local build, and 2.5.1/2.6.0 unchanged (decision 3, I1).** This repository (Workflow 2.6.0) and a fresh 2.6.0 and a
fresh 2.5.1 target, `inspect` and `explain` with the installed 1.5.0 and with the local build, byte for
byte; then `--json`, and the one packaging change of the milestone.

<!-- flow A begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
BASE=97b85f04cb62e8f483f7e76bf68a280bdc8c9d4b
"$W" --version
# this repository (Workflow 2.6.0, legacy mode), then a fresh 2.6.0 and a fresh 2.5.1 target: the installed 1.5.0 against the local build
fresh l0 l-ready; L0=$R; fresh o0 o-ready; O0=$R
for target in "$R0" "$L0" "$O0"; do
  label=$([ "$target" = "$R0" ] && echo repo || basename "$(dirname "$target")")
  for c in inspect explain; do
    workflow-controller --runtime-dir "$S/rt-old" --settings "$S/set-old.json" --workflow-manager "$SM" $c "$target" > "$S/$c-old.txt" 2>&1; e1=$?
    "$W" --runtime-dir "$S/rt-new" --settings "$S/set-new.json" --workflow-manager "$SM" $c "$target" > "$S/$c-new.txt" 2>&1; e2=$?
    echo "$label $c: 1.5.0 exit $e1, local exit $e2; $(cmp -s "$S/$c-old.txt" "$S/$c-new.txt" && echo byte-identical || echo DIFFERENT)"
  done
done
# --json, apart from the controller's own identity
for c in inspect status; do
  a=$([ $c = inspect ] && echo "$L0")
  workflow-controller --json --runtime-dir "$S/rt-old" --settings "$S/set-old.json" --workflow-manager "$SM" $c $a > "$S/j-old.txt" 2>&1
  "$W" --json --runtime-dir "$S/rt-old" --settings "$S/set-new.json" --workflow-manager "$SM" $c $a > "$S/j-new.txt" 2>&1
  python3 - $c <<'PY'
import json, sys
a, b = (json.load(open(f"/tmp/c9-fr/j-{n}.txt")) for n in ("old", "new"))
for d in (a, b):
    for k in ("controller", "pinned_identity", "last_job_telemetry"):
        d.pop(k, None)
print(sys.argv[1], "--json equal apart from the controller identity:", a == b)
PY
done
git -C "$R0" diff --stat "$BASE" "$H" -- .workflow-controller/ pyproject.toml setup.py .github/
git -C "$R0" diff "$BASE" "$H" -- pyproject.toml | grep '^[-+][^-+]'
(cd "$S/src" && python3 tools/ci_workflows.py --check; echo "ci_workflows --check exit $?")
```
<!-- flow A end -->
Expected output:
```text
workflow-controller 1.6.0
runtime: package (local build from 25adda4a2d9c)
repo inspect: 1.5.0 exit 0, local exit 0; byte-identical
repo explain: 1.5.0 exit 0, local exit 0; byte-identical
l0 inspect: 1.5.0 exit 0, local exit 0; byte-identical
l0 explain: 1.5.0 exit 0, local exit 0; byte-identical
o0 inspect: 1.5.0 exit 0, local exit 0; byte-identical
o0 explain: 1.5.0 exit 0, local exit 0; byte-identical
inspect --json equal apart from the controller identity: True
status --json equal apart from the controller identity: True
 pyproject.toml | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
-controller = ["GENERATION.json"]
+controller = ["GENERATION.json", "protocol_schema.json"]
ci_workflows --check exit 0
```
- `workflow-controller 1.6.0`, `runtime: package (local build from 25adda4a2d9c)`;
- all six pairs `byte-identical` with exit 0 on both sides: a legacy target's `inspect` and `explain`
  carry no protocol line (the 1.6.0 behaviour, which 1.5.0 shares);
- `--json` of `inspect` and `status` equal once the Controller's own identity is removed (no new key for a
  legacy target);
- `pyproject.toml` is the only packaging/policy/CI file changed from the base, by one line that ships
  `protocol_schema.json` in the package; `.workflow-controller/` and `.github/` are unchanged;
  `ci_workflows --check exit 0`.

**B. Admission by capability (plan A/B, decision 1).** 2.7.0 is admitted because its record lists the protocol
script and `describe` answers major 1; the legacy releases are admitted as before and gain no key; the
installed 1.5.0 refuses 2.7.0; a stand-in `2.7.1` that speaks the protocol is admitted with no Controller
change; a release without the protocol script, or with another major, is refused.

<!-- flow B begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
echo "-- B1: Workflow 2.7.0, admitted by capability"
fresh b1 p-ready; wcx inspect "$R" | norm; echo "exit ${PIPESTATUS[0]}"
wcx --json inspect "$R" | python3 -c "
import json, sys
r = json.load(sys.stdin)['repository']
print(sorted(r)); print(r['workflow_mode'], r['target_protocol'], sorted(r['script_digests']), r['unknown_action_ids'])"
echo "-- B2: 2.6.0 and 2.5.1, legacy: no protocol keys"
for k in l-ready o-ready; do fresh b2 $k; wcx --json inspect "$R" | python3 -c "
import json, sys
r = json.load(sys.stdin)['repository']; print(r['workflow_version'], sorted(r))"; done
echo "-- B3: the installed 1.5.0 refuses 2.7.0"
fresh b3 p-ready; workflow-controller --runtime-dir "$S/rt-old" --settings "$S/set-old.json" --workflow-manager "$SM" inspect "$R" 2>&1 | norm; echo "exit ${PIPESTATUS[0]}"
echo "-- B4: a later release that speaks the protocol (2.7.1 stand-in) needs no Controller change"
fresh b4 p-ready
sed -i 's/"workflow_version": "2.7.0"/"workflow_version": "2.7.1"/' "$R/.workflow-manager/installation.json"
sed -i 's/^WORKFLOW_RELEASE = "2.7.0"/WORKFLOW_RELEASE = "2.7.1"/' "$R/scripts/workflow_protocol.py"
printf '%s\n' '#!/bin/sh' 'echo "workflow 2.7.1 (full profile) -- clean"; exit 0' > "$T/wm"
wcx inspect "$R" | norm | head -2; echo "exit ${PIPESTATUS[0]}"
echo "-- B5: no protocol script in the installation record: refused no_protocol"
fresh b5 p-ready
python3 - "$R" <<'PY'
import json, sys
p = sys.argv[1] + "/.workflow-manager/installation.json"
d = json.load(open(p)); del d["managed"]["scripts/workflow_protocol.py"]; json.dump(d, open(p, "w"), indent=2)
PY
wcx inspect "$R" 2>&1 | norm; echo "exit ${PIPESTATUS[0]}"
echo "-- B6: protocol major 2: refused unsupported_protocol_major"
fresh b6 p-ready; sed -i 's/^PROTOCOL_MAJOR = 1/PROTOCOL_MAJOR = 2/' "$R/scripts/workflow_protocol.py"
wcx inspect "$R" 2>&1 | norm; echo "exit ${PIPESTATUS[0]}"
```
<!-- flow B end -->
Expected output:
```text
-- B1: Workflow 2.7.0, admitted by capability
repository: <R> (Workflow 2.7.0, profile full)
workflow mode: protocol (protocol 1.0, 4 managed scripts)
lifecycle lock: free
work item: demo (type=product kind=product governing_workflow_version=2.2)
phase: AWAITING_LOCAL_PLAN_REVIEW
plan_revision=1 implementation_revision=None functional_review_round=None
current_checkpoint_id=None last_completed_checkpoint_id=None registry_complete=False
exit 0
['profile', 'root', 'script_digests', 'target_protocol', 'unknown_action_ids', 'workflow_mode', 'workflow_version']
protocol {'major': 1, 'version': '1.0', 'release': '2.7.0'} ['scripts/workflow_fingerprint.py', 'scripts/workflow_protocol.py', 'scripts/workflow_state.py', 'scripts/workflow_test_harness.py'] []
-- B2: 2.6.0 and 2.5.1, legacy: no protocol keys
2.6.0 ['profile', 'root', 'workflow_version']
2.5.1 ['profile', 'root', 'workflow_version']
-- B3: the installed 1.5.0 refuses 2.7.0
error: <R> runs Workflow '2.7.0', which is outside the Controller's supported lines ['2.5', '2.6'] (validated releases: ['2.5.1', '2.6.0'])
exit 20
-- B4: a later release that speaks the protocol (2.7.1 stand-in) needs no Controller change
repository: <R> (Workflow 2.7.1, profile full)
workflow mode: protocol (protocol 1.0, 4 managed scripts)
exit 0
-- B5: no protocol script in the installation record: refused no_protocol
error: <R> runs Workflow '2.7.0', which is newer than the Controller's supported lines ['2.5', '2.6'] and does not list scripts/workflow_protocol.py in its installation record, so it does not ship the orchestration protocol
exit 20
-- B6: protocol major 2: refused unsupported_protocol_major
error: <R> runs Workflow '2.7.0', whose protocol is not one the Controller speaks (major 1): protocol describe: the Workflow speaks protocol 1.0 and said 'protocol major 1 is not supported; supported: [2]'; the Controller speaks major 1
exit 20
```
- B1: `workflow mode: protocol (protocol 1.0, 4 managed scripts)`; the `--json` repository block adds exactly
  `script_digests`, `target_protocol`, `unknown_action_ids` (empty) and `workflow_mode`;
- B2: a legacy target's block is `profile`, `root`, `workflow_version` and nothing more;
- B3: 1.5.0: `outside the Controller's supported lines ['2.5', '2.6']`, exit 20;
- B4: 2.7.1 admitted in protocol mode, exit 0;
- B5/B6: exit 20 with `does not list scripts/workflow_protocol.py in its installation record` and with
  `protocol major 1 is not supported; supported: [2]` (`no_protocol`, `unsupported_protocol_major`).

**C. Decisions come from `next-action`, and a user-only action is a gate (plan C.2).** One target walked
through an automatic decision, an external gate (the manual review), a user-only approval gate and the
next automatic decision.

<!-- flow C begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
fresh c1 p-ready
echo "-- C1: an automatic decision from next-action"; explain
step; jobs
echo "-- C2: the manual external review is the user's: a gate, nothing launched"; explain; step; gatetext; cat "$T/tasks.log"
echo "-- C3: the user records the verdict; the plan approval is user-only"; usr plan-gate; explain; step; gatetext; cat "$T/tasks.log"
echo "-- C4: the user approves; the next action is automatic again"; usr approve-plan; explain
```
<!-- flow C end -->
Expected output:
```text
-- C1: an automatic decision from next-action
phase: AWAITING_LOCAL_PLAN_REVIEW
workflow mode: protocol (Workflow 2.7.0, protocol 1.0)
  protocol row 12: disposition automatic, action plan.review.local
  evidence: protocol row 12: local_review_due (automatic)
  evidence: protocol action: plan.review.local
reason: the bound plan bundle awaits its local model review
next automatic action: /review-plan demo
step exit 0
FINISHED | /review-plan demo | plan.review.local | gate_reached | None | None  | 
-- C2: the manual external review is the user's: a gate, nothing launched
phase: AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW
workflow mode: protocol (Workflow 2.7.0, protocol 1.0)
  protocol row 14: disposition external_gate, action plan.review.external
  evidence: protocol row 14: awaiting_external_review (external_gate)
reason: AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW: awaiting_external_review: awaiting the manual external plan-review verdict
human gate -- what is required: awaiting the manual external plan-review verdict; action: plan.review.external; satisfied by: plan_review_verdict
  safe resume command: workflow-controller --work-item demo explain <R>
step exit 10
GATE_BLOCKED -- awaiting the manual external plan-review verdict; action: plan.review.external; satisfied by: plan_review_verdict
/review-plan demo
-- C3: the user records the verdict; the plan approval is user-only
-- driver: user step plan-gate done; phase AWAITING_PLAN_APPROVAL
phase: AWAITING_PLAN_APPROVAL
workflow mode: protocol (Workflow 2.7.0, protocol 1.0)
  protocol row 15: disposition human_gate, action plan.approve
  evidence: protocol row 15: approval_gate_reachable (human_gate)
reason: AWAITING_PLAN_APPROVAL: approval_gate_reachable: the plan approval gate is reachable
human gate -- what is required: the plan approval gate is reachable; action: plan.approve (/approve-review plan demo)
  safe resume command: /approve-review plan demo
step exit 10
GATE_BLOCKED -- the plan approval gate is reachable; action: plan.approve (/approve-review plan demo)
/review-plan demo
-- C4: the user approves; the next action is automatic again
-- driver: user step approve-plan done; phase IMPLEMENTING
phase: IMPLEMENTING
workflow mode: protocol (Workflow 2.7.0, protocol 1.0)
  protocol row 23: disposition automatic, action implementation.checkpoint
  evidence: protocol row 23: checkpoint_outstanding (automatic)
  evidence: protocol action: implementation.checkpoint
reason: checkpoint CP1 is next
next automatic action: /milestone-implement demo
```
- C1: `protocol row 12: disposition automatic, action plan.review.local`, `next automatic action:
  /review-plan demo`; the step launches it and the record's class is `gate_reached`;
- C2: `disposition external_gate, action plan.review.external`; the step exits 10, writes a gate record and
  launches nothing (`tasks.log` still holds the one `/review-plan`);
- C3: after the verdict, `disposition human_gate, action plan.approve`, whose resume command is
  `/approve-review plan demo` (user-only: nothing is launched, exit 10);
- C4: after the approval, `disposition automatic, action implementation.checkpoint`.

**D. Both modes end to end (plan K2, differences D6).** The whole lifecycle with `run`, the driver doing only
the user-only steps: protocol mode on 2.7.0 (D1), one protocol job record and what `status` shows (D2), then
the same lifecycle on 2.6.0 (D3).

<!-- flow D begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
run() { wcx run "$R"; echo "run exit $?"; }
echo "-- D1: Workflow 2.7.0 (protocol mode), plan review to the next milestone, the user doing only the user-only steps"
fresh d1 p-ready
run; usr plan-gate >/dev/null; run; usr approve-plan >/dev/null; run; usr impl-gate >/dev/null; run
usr technical-approval >/dev/null; run; usr accept >/dev/null; run
echo "launched:"; cat "$T/tasks.log"
echo "jobs:"; jobs
echo "-- D2: one protocol job record, and what status shows"
python3 - "$RT" <<'PY'
import glob, json, sys
r = [json.load(open(p)) for p in sorted(glob.glob(sys.argv[1] + "/jobs/*.json"))]
r = next(x for x in sorted(r, key=lambda x: x["created_at"]) if x["status"] == "FINISHED" and x["protocol"]["action_id"] == "implementation.checkpoint")
p = r["protocol"]
print(sorted(p))
print(p["action_id"], p["protocol_version"], p["workflow_release"], sorted(p["script_sha256"]), p["reconcile"]["class"], sorted(p["reconcile"]))
print(r["target_workflow_version"], r["transition_verified"], r["observed_phase_before"], "->", r["observed_phase_after"])
PY
wcx status | grep -c reconcile
wcx status | grep reconcile | sed 's/^ *<job> //' | norm | sed 's/^[^ ]* //' | sort | uniq -c
wcx --json status | python3 -c "
import json, sys
jobs = json.load(sys.stdin)['jobs']['recent']
print(sorted(next(j for j in jobs if 'reconcile_class' in j)))"
echo "-- D3: Workflow 2.6.0 (legacy mode), the same lifecycle: no protocol block; the checklist is the user's (D6)"
fresh d3 l-ready
run; usr plan-gate >/dev/null; run; usr approve-plan >/dev/null; run; usr impl-gate >/dev/null; run
usr technical-approval >/dev/null; run; usr checklist >/dev/null; run; usr accept >/dev/null; run
echo "launched:"; cat "$T/tasks.log"
echo "jobs:"; jobs
python3 - "$RT" <<'PY'
import glob, json, sys
rs = [json.load(open(p)) for p in glob.glob(sys.argv[1] + "/jobs/*.json")]
print("records with a protocol block:", sum("protocol" in r for r in rs), "of", len(rs))
PY
wcx --json status | python3 -c "
import json, sys
print(sorted(set().union(*[set(j) for j in json.load(sys.stdin)['jobs']['recent']])))"
```
<!-- flow D end -->
Expected output:
```text
-- D1: Workflow 2.7.0 (protocol mode), plan review to the next milestone, the user doing only the user-only steps
run exit 10
run exit 10
run exit 10
run exit 10
run exit 10
run exit 10
launched:
/review-plan demo
/milestone-implement demo
/milestone-implement demo
/review-implementation demo
/prepare-functional-review demo
/milestone-plan
/milestone-plan
jobs:
FINISHED | /review-plan demo | plan.review.local | gate_reached | None | None  | 
GATE_BLOCKED | None | None | None | None | None  | awaiting the manual external plan-review verdict; action: plan.review.external; satisfied by: plan_review_verdict
GATE_BLOCKED | None | None | None | None | None  | the plan approval gate is reachable; action: plan.approve (/approve-review plan demo)
FINISHED | /milestone-implement demo | implementation.checkpoint | progress | None | None  | 
FINISHED | /milestone-implement demo | implementation.self_review | progress | None | None  | 
FINISHED | /review-implementation demo | implementation.review.local | gate_reached | None | None  | 
GATE_BLOCKED | None | None | None | None | None  | awaiting the manual external implementation-review verdict; action: implementation.review.external; satisfied by: implementation_review_verdict
GATE_BLOCKED | None | None | None | None | None  | the technical approval gate is reachable; action: implementation.approve (/approve-review implementation demo)
FINISHED | /prepare-functional-review demo | functional.prepare | gate_reached | None | None  | 
GATE_BLOCKED | None | None | None | None | None  | the registry is terminal; the user's functional review is the gate; action: functional.review; alternatives: milestone.accept (/accept-milestone demo), functional.apply_findings (
FINISHED | /milestone-plan | plan.start | no_progress | none | None  | 
FINISHED | /milestone-plan | plan.start | no_progress | none | None  | 
GATE_BLOCKED | None | None | None | None | None  | no_progress_repeated: the last two jobs for plan.start (<job> and <job>) both ended without progress, so a third is not launched; read their records, then change the work item (ta
-- D2: one protocol job record, and what status shows
['action_id', 'decided_ns', 'decision', 'envelope_digest', 'protocol_version', 'reconcile', 'script_sha256', 'state_identity', 'workflow_release']
implementation.checkpoint 1.0 2.7.0 ['scripts/workflow_fingerprint.py', 'scripts/workflow_protocol.py', 'scripts/workflow_state.py', 'scripts/workflow_test_harness.py'] progress ['class', 'evidence', 'from', 'invalid_reasons', 'next', 'to']
2.7.0 True IMPLEMENTING -> SELF_REVIEWING_IMPLEMENTATION
6
      2  <job> FINISHED /milestone-implement demo (work item demo) [reconcile progress]: wall <n> s, cost $0.00
      2  <job> FINISHED /milestone-plan (work item none) [reconcile no_progress]: wall <n> s, cost $0.00
      1  <job> FINISHED /prepare-functional-review demo (work item demo) [reconcile gate_reached]: wall <n> s, cost $0.00
      1  <job> FINISHED /review-implementation demo (work item demo) [reconcile gate_reached]: wall <n> s, cost $0.00
['age_seconds', 'command', 'cost_usd', 'created_at', 'finished', 'invalid_reasons', 'job_id', 'reconcile_class', 'status', 'wall_seconds', 'work_item_id']
-- D3: Workflow 2.6.0 (legacy mode), the same lifecycle: no protocol block; the checklist is the user's (D6)
run exit 10
run exit 10
run exit 10
run exit 10
run exit 10
run exit 10
run exit 30
launched:
/review-plan demo
/milestone-implement demo
/milestone-implement demo
/review-implementation demo
/milestone-plan
jobs:
FINISHED | /review-plan demo | None | None | None | None  | 
GATE_BLOCKED | None | None | None | None | None  | upload the current plan bundle to a manual external reviewer and paste the verdict into REVIEW_FEEDBACK.md, declaring Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW
GATE_BLOCKED | None | None | None | None | None  | a human runs /approve-review plan to approve or reject the reviewed plan
FINISHED | /milestone-implement demo | None | None | None | None  | 
FINISHED | /milestone-implement demo | None | None | None | None  | 
FINISHED | /review-implementation demo | None | None | None | None  | 
GATE_BLOCKED | None | None | None | None | None  | upload .ai-review/demo/current (bundle_id <sha256>, review_content_id <sha256> from the ledger) to a manual external reviewer and paste the verdict into .ai-review/demo/feedback/R
GATE_BLOCKED | None | None | None | None | None  | both implementation-review stages approved the current content; a human runs the user-only /approve-review implementation
GATE_BLOCKED | None | None | None | None | None  | no current-round functional-review checklist evidence commit is on file; run /prepare-functional-review to write and commit the checklist
GATE_BLOCKED | None | None | None | None | None  | the checklist is current; a human must perform manual functional testing and place findings at .ai-review/demo/feedback/FUNCTIONAL_REVIEW.md
FAILED | /milestone-plan | None | None | None | phase_not_in_to_any_of  | 
records with a protocol block: 0 of 11
['age_seconds', 'command', 'cost_usd', 'created_at', 'finished', 'job_id', 'status', 'wall_seconds', 'work_item_id']
```
- D1: the worker launches are `/review-plan`, `/milestone-implement` twice, `/review-implementation`,
  `/prepare-functional-review` (the Controller prepares the checklist: D6), then `/milestone-plan` twice;
  every launched job carries `plan.review.local`, `implementation.checkpoint`, `implementation.self_review`,
  `implementation.review.local`, `functional.prepare`, `plan.start` and the reconcile classes
  `gate_reached`, `progress`, `progress`, `gate_reached`, `gate_reached`, `no_progress`, `no_progress`; the
  user's gates launch nothing; the second `/milestone-plan` is a terminal `FINISHED` with progress `none`
  and the third attempt is the gate `no_progress_repeated` (the fake worker plans nothing);
- D2: the `protocol` block holds the decision as received, its envelope digest, state identity, release
  `2.7.0`, `protocol_version 1.0`, the managed-script digest map (four scripts) and the reconcile answer;
  `status` shows `[reconcile <class>]` on six jobs and the JSON job entries gain `reconcile_class` and
  `invalid_reasons`;
- D3: 2.6.0: the same launches minus `/prepare-functional-review` (1.6.0 gates and the driver writes the
  checklist as the user), no record has a `protocol` block, `status --json` has no reconcile key, and the
  final `/milestone-plan` is a `FAILED` job (`phase_not_in_to_any_of`, exit 30: 1.6.0's verification needs a
  phase transition), where the protocol records a successful no-progress.

**E. Fail-closed handling (plan C.2, C.3, I3, A.3).** The Workflow answers an unknown disposition, an unknown
action id, and an invocation that differs from the Controller's rendering; then the Workflow's own `verify`
reports the repository unhealthy. Each is a gate with the Workflow's text; no worker is started and no
record is a launch.

<!-- flow E begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
for c in '{"override": {"disposition": "validation"}}' '{"override": {"action_id": "plan.bogus"}}' '{"override": {"invocation": "/review-plan something-else"}}'; do
  echo "-- E: the Workflow answers $c"
  shimmed e1 "$c"; explain | sed -n 2,3p; explain | grep '^human gate' | cut -c1-330
  step; gatetext | cut -c1-200; launched
done
echo "-- E4: the Workflow's own verify reports the repository unhealthy: nothing is decided or launched"
fresh e4 p-ready; sed -i 's/"workflow_version": "2.7.0"/"workflow_version": "2.7.1"/' "$R/.workflow-manager/installation.json"
step; gatetext | cut -c1-260; explain | grep 'evidence: verify'; launched
```
<!-- flow E end -->
Expected output:
```text
-- E: the Workflow answers {"override": {"disposition": "validation"}}
workflow mode: protocol (Workflow 2.7.0, protocol 1.0)
  protocol row 12: disposition validation, action plan.review.local
human gate -- what is required: workflow_unknown_disposition: the Workflow answered the disposition 'validation', which this Controller release does not know (row 12: local_review_due: the bound plan bundle awaits its local model review); nothing is launched
step exit 10
GATE_BLOCKED -- workflow_unknown_disposition: the Workflow answered the disposition 'validation', which this Controller release does not know (row 12: local_review_due: the bound plan bundle awaits it
workers launched: 0
-- E: the Workflow answers {"override": {"action_id": "plan.bogus"}}
workflow mode: protocol (Workflow 2.7.0, protocol 1.0)
  protocol row 12: disposition automatic, action plan.bogus
human gate -- what is required: workflow_unknown_action: the Workflow's next action is 'plan.bogus', which this Controller release has no command for (row 12); a later Workflow minor may add actions, and one that becomes next stops here
step exit 10
GATE_BLOCKED -- workflow_unknown_action: the Workflow's next action is 'plan.bogus', which this Controller release has no command for (row 12); a later Workflow minor may add actions, and one that bec
workers launched: 0
-- E: the Workflow answers {"override": {"invocation": "/review-plan something-else"}}
workflow mode: protocol (Workflow 2.7.0, protocol 1.0)
  protocol row 12: disposition automatic, action plan.review.local
human gate -- what is required: workflow_invocation_mismatch: the Workflow's invocation text '/review-plan something-else' for 'plan.review.local' differs from the command this Controller renders, '/review-plan demo', under Workflow release 2.7.0; the Workflow's reconcile would refuse the decision, so no worker is launched. This
step exit 10
GATE_BLOCKED -- workflow_invocation_mismatch: the Workflow's invocation text '/review-plan something-else' for 'plan.review.local' differs from the command this Controller renders, '/review-plan demo'
workers launched: 0
-- E4: the Workflow's own verify reports the repository unhealthy: nothing is decided or launched
step exit 10
GATE_BLOCKED -- workflow_unhealthy: the Workflow's verify reports the repository unhealthy (installation_release_matches: the installation record names '2.7.1', these scripts are 2.7.0); nothing is launched. A failing state_valid can be transient (a planning w
  evidence: verify installation_release_matches: fail: the installation record names '2.7.1', these scripts are 2.7.0
workers launched: 0
```
- the three answers are gates `workflow_unknown_disposition`, `workflow_unknown_action` and
  `workflow_invocation_mismatch` (naming both texts and the release, `a Workflow release defect to report,
  not a command to run`), `step exit 10`, `workers launched: 0`; `explain` shows the same text;
- E4: `workflow_unhealthy` names the failing check (`installation_release_matches`), exit 10, `workers
  launched: 0`.

**F. The state identity is checked again before every spawn (plan D.2).** "Someone else" writes the work item's
state (through the Workflow's own writer) just before the Nth `next-action`: before the first check
(re-decides and launches), under three decisions in a row (gate), and between the check and the spawn
(terminal `FAILED`, no worker).

<!-- flow F begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
# the shim makes "someone else" write the work item's state (state_revision + 1, through the Workflow's own writer) just before the Nth next-action
echo "-- F1: the state moves before the first currency check: the Controller re-decides (5 next-action calls) and launches"
impl f1 '{"at": {"2": "state"}}'; step; echo "next-action calls: $(cat "$T/shim.count")"; jobs; launched
echo "-- F2: it moves under three decisions in a row: decision_unstable, no worker"
impl f2 '{"at": {"2": "state", "4": "state", "6": "state"}}'; step; echo "next-action calls: $(cat "$T/shim.count")"; gatetext | cut -c1-330; launched
echo "-- F3: it moves after the check and before the spawn: a terminal FAILED record, no worker; the next step re-decides and launches"
impl f3 '{"at": {"3": "state"}}'; step; echo "next-action calls: $(cat "$T/shim.count")"; jobs
python3 - "$RT" <<'PY'
import glob, json, sys
for p in glob.glob(sys.argv[1] + "/jobs/*.json"):
    r = json.load(open(p)); e = r["reconciliation_evidence"]
    print(e["code"], e["gate"], e["evidence"]["reason"], "|", e["message"])
PY
launched; echo '{}' > "$T/shim.json"; step; jobs; launched
```
<!-- flow F end -->
Expected output:
```text
-- F1: the state moves before the first currency check: the Controller re-decides (5 next-action calls) and launches
step exit 0
next-action calls: 5
FINISHED | /milestone-implement demo | implementation.checkpoint | progress | None | None  | 
workers launched: 1
-- F2: it moves under three decisions in a row: decision_unstable, no worker
step exit 10
next-action calls: 6
GATE_BLOCKED -- decision_unstable: the Workflow's state changed under 3 consecutive decisions, so none was launched; the last state identities were <sha256> and <sha256>. Something else is writing the target repository's state; stop it, then re-run
workers launched: 0
-- F3: it moves after the check and before the spawn: a terminal FAILED record, no worker; the next step re-decides and launches
step exit 30
next-action calls: 3
FAILED | /milestone-implement demo | implementation.checkpoint | None | None | None  | 
decision_stale_at_launch decision_unstable stale_decision | the decision was no longer the Workflow's answer immediately before the spawn (stale_decision); no worker was started, and the next run re-decides
workers launched: 0
step exit 0
FAILED | /milestone-implement demo | implementation.checkpoint | None | None | None  | 
FINISHED | /milestone-implement demo | implementation.checkpoint | progress | None | None  | 
workers launched: 1
```
- F1: five `next-action` calls (decide, stale check, re-decide, check, check before the spawn), one worker,
  `progress`;
- F2: six calls, `decision_unstable` naming the last two state identities, `workers launched: 0`;
- F3: three calls, exit 30, a terminal `FAILED` record with `decision_stale_at_launch` (reason
  `stale_decision`), `workers launched: 0`; the next step re-decides and launches (a second record,
  `progress`).

**G. Outcomes come from `reconcile` (plan E).** A worker that does nothing, one that leaves an illegal state, and
one that fails. (`progress` and `gate_reached` are flow D.)

<!-- flow G begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
echo "-- G1: a worker that does nothing: a terminal FINISHED job with progress none (nothing for resume)"
fresh g1 p-ready; mode noop; step; jobs; wcx resume "$R" | norm; echo "resume exit ${PIPESTATUS[0]}"
echo "-- G2: a worker that leaves an illegal state (the phase jumped to IMPLEMENTING): FAILED reconcile_invalid, the reasons verbatim"
fresh g2 p-ready; mode invalid; step; jobs
python3 - "$RT" <<'PY'
import glob, json, sys
r = json.load(open(glob.glob(sys.argv[1] + "/jobs/*.json")[0]))
print(json.dumps(r["reconciliation_evidence"]["invalid_reasons"]))
PY
wcx status | grep reconcile | sed 's/^ *[^ ]* //' | norm
echo "-- G3: a failed worker: FAILED worker_outcome, reconcile not called (no reconcile class)"
fresh g3 p-ready; mode fail; step; jobs
```
<!-- flow G end -->
Expected output:
```text
-- G1: a worker that does nothing: a terminal FINISHED job with progress none (nothing for resume)
step exit 0
FINISHED | /review-plan demo | plan.review.local | no_progress | none | None  | 
<job>: FINISHED
resume exit 0
-- G2: a worker that leaves an illegal state (the phase jumped to IMPLEMENTING): FAILED reconcile_invalid, the reasons verbatim
step exit 30
FAILED | /review-plan demo | plan.review.local | invalid | None | reconcile_invalid illegal_edge | 
[{"code": "illegal_edge", "text": "plan.review.local has no legal edge AWAITING_LOCAL_PLAN_REVIEW -> IMPLEMENTING at '2.2'"}]
FAILED /review-plan demo (work item demo) [reconcile invalid: illegal_edge]: wall <n> s, cost $0.00
-- G3: a failed worker: FAILED worker_outcome, reconcile not called (no reconcile class)
step exit 30
FAILED | /review-plan demo | plan.review.local | None | None | worker_outcome  |
```
- G1: a terminal `FINISHED` record with class `no_progress` and progress `none`; `resume` has nothing to
  do (it only lists the job);
- G2: `FAILED`, reason `reconcile_invalid`, the class `invalid`, the Workflow's reason verbatim
  (`illegal_edge`); `status` shows `[reconcile invalid: illegal_edge]`;
- G3: `FAILED`, reason `worker_outcome`, no reconcile class (`reconcile` was not called).

**H. The loop guard (plan D.3).** Two no-progress jobs for the same item and action gate the third; a change of
the work item by someone else restarts the count.

<!-- flow H begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
echo "-- H1: two no-progress jobs for the same item and action: the third launch is a gate"
fresh h1 p-ready; mode noop; step; step; step; jobs; launched
echo "-- H2: someone else moves the work item after the gate: the count restarts (two more launches), then the gate again"
drive bump "$R"; step; step; step; jobs; launched
```
<!-- flow H end -->
Expected output:
```text
-- H1: two no-progress jobs for the same item and action: the third launch is a gate
step exit 0
step exit 0
step exit 10
FINISHED | /review-plan demo | plan.review.local | no_progress | none | None  | 
FINISHED | /review-plan demo | plan.review.local | no_progress | none | None  | 
GATE_BLOCKED | None | None | None | None | None  | no_progress_repeated: the last two jobs for plan.review.local on demo (<job> and <job>) both ended without progress, so a third is not launched; read their records, then change th
workers launched: 2
-- H2: someone else moves the work item after the gate: the count restarts (two more launches), then the gate again
-- driver: someone else wrote the work item's state
step exit 0
step exit 0
step exit 10
FINISHED | /review-plan demo | plan.review.local | no_progress | none | None  | 
FINISHED | /review-plan demo | plan.review.local | no_progress | none | None  | 
GATE_BLOCKED | None | None | None | None | None  | no_progress_repeated: the last two jobs for plan.review.local on demo (<job> and <job>) both ended without progress, so a third is not launched; read their records, then change th
FINISHED | /review-plan demo | plan.review.local | no_progress | none | None  | 
FINISHED | /review-plan demo | plan.review.local | no_progress | none | None  | 
GATE_BLOCKED | None | None | None | None | None  | no_progress_repeated: the last two jobs for plan.review.local on demo (<job> and <job>) both ended without progress, so a third is not launched; read their records, then change th
workers launched: 4
```
- H1: two `FINISHED` no-progress jobs, then a gate `no_progress_repeated` naming both jobs; `workers
  launched: 2`;
- H2: after the move two more jobs launch (the older job no longer continues the streak), then the gate
  again; `workers launched: 4`. The same-second ordering has no flow (each job takes about a second): it is
  `LoopGuardTest.test_same_second_jobs_are_ordered_by_their_decision_time_not_their_random_suffix`, run
  by name in flow M.

**I. The executed bytes are the checked bytes (plan A.1, decision 2).** A managed script edited after the check
that admitted it is refused before anything runs; a worker that edits one ends its job `FAILED`.

<!-- flow I begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
echo "-- I1: a managed script edited between the check and the execution: refused, the edited bytes are never run"
shimmed i1 '{"at": {"1": "edit-script"}}'; step 2>&1 | norm; jobs; launched; git -C "$R" status --short | grep -v '^??'
echo "-- I2: a worker that edits a managed Workflow script: FAILED workflow_release_changed, reconcile never called"
fresh i2 p-ready; mode tamper; step; jobs; git -C "$R" status --short | grep 'scripts/'
```
<!-- flow I end -->
Expected output:
```text
-- I1: a managed script edited between the check and the execution: refused, the edited bytes are never run
error: <R>'s managed Workflow scripts are not the ones this operation is bound to (scripts/workflow_state.py); protocol next-action was not run
step exit 20
workers launched: 0
 M scripts/workflow_state.py
-- I2: a worker that edits a managed Workflow script: FAILED workflow_release_changed, reconcile never called
step exit 30
FAILED | /review-plan demo | plan.review.local | None | None | workflow_release_changed  | 
 M scripts/workflow_protocol.py
```
- I1: `managed Workflow scripts are not the ones this operation is bound to (scripts/workflow_state.py);
  protocol next-action was not run`, exit 20, `workers launched: 0`; the edit is left in the worktree;
- I2: `FAILED`, reason `workflow_release_changed`, no reconcile class (`reconcile` not called), exit 30.

**J. Resume under a drifted installation (plan E.2).** The Controller is killed (SIGKILL) right after the worker
finished, leaving a `LAUNCHED` record; the Manager then reports drift (`verify` exit 1).

<!-- flow J begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
# crash NAME: a target whose Controller was killed (SIGKILL) right after its worker finished: the job record stays LAUNCHED
crash() { fresh "$1" p-ready; mode crash; { wcx step "$R"; } 2>/dev/null; echo "controller killed: exit $?"; mode effect; jobs; }
drift() { printf '%s\n' '#!/bin/sh' 'echo "workflow 2.7.0 (full profile) -- drifted"; exit 1' > "$T/wm"; }
echo "-- J1: drift, the managed scripts unchanged: resume refuses, the record is left as it was"
crash j1; drift; wcx resume "$R" 2>&1 | norm; echo "resume exit ${PIPESTATUS[0]}"; jobs
echo "-- J2: drift, a script unreadable as a regular file (a symlink to an identical copy): still no difference is established"
crash j2; drift; cp "$R/scripts/workflow_state.py" "$T/copy.py"; rm "$R/scripts/workflow_state.py"; ln -s "$T/copy.py" "$R/scripts/workflow_state.py"
wcx resume "$R" 2>&1 | norm; echo "resume exit ${PIPESTATUS[0]}"; jobs
rm "$R/scripts/workflow_state.py"; cp "$T/copy.py" "$R/scripts/workflow_state.py"; wcx resume "$R" 2>&1 | norm; echo "resume exit ${PIPESTATUS[0]} (readable and identical: still no difference)"; jobs
echo "-- J3: drift with a real digest difference in a managed script: the pending job is marked FAILED workflow_release_changed; no Workflow script runs"
crash j3; drift; echo "# edited after the job started" >> "$R/scripts/workflow_state.py"
wcx resume "$R" 2>&1 | norm; echo "resume exit ${PIPESTATUS[0]}"; jobs
echo "-- J4: no drift: resume reconciles the same pending job from the stored decision"
crash j4; wcx resume "$R" 2>&1 | norm; echo "resume exit ${PIPESTATUS[0]}"; jobs
```
<!-- flow J end -->
Expected output:
```text
-- J1: drift, the managed scripts unchanged: resume refuses, the record is left as it was
controller killed: exit 137
LAUNCHED | /review-plan demo | plan.review.local | None | None | None  | 
error: <R>'s Workflow installation failed verification (verify exit 1, status exit 1)
resume exit 20
LAUNCHED | /review-plan demo | plan.review.local | None | None | None  | 
-- J2: drift, a script unreadable as a regular file (a symlink to an identical copy): still no difference is established
controller killed: exit 137
LAUNCHED | /review-plan demo | plan.review.local | None | None | None  | 
error: <R>'s Workflow installation failed verification (verify exit 1, status exit 1)
resume exit 20
LAUNCHED | /review-plan demo | plan.review.local | None | None | None  | 
error: <R>'s Workflow installation failed verification (verify exit 1, status exit 1)
resume exit 20 (readable and identical: still no difference)
LAUNCHED | /review-plan demo | plan.review.local | None | None | None  | 
-- J3: drift with a real digest difference in a managed script: the pending job is marked FAILED workflow_release_changed; no Workflow script runs
controller killed: exit 137
LAUNCHED | /review-plan demo | plan.review.local | None | None | None  | 
<job>: FAILED
resume exit 0
FAILED | /review-plan demo | plan.review.local | None | None | workflow_release_changed  | 
-- J4: no drift: resume reconciles the same pending job from the stored decision
controller killed: exit 137
LAUNCHED | /review-plan demo | plan.review.local | None | None | None  | 
<job>: FINISHED
resume exit 0
FINISHED | /review-plan demo | plan.review.local | gate_reached | None | None  |
```
- J1: scripts unchanged: `failed verification (verify exit 1, status exit 1)`, exit 20, the record is
  still `LAUNCHED`;
- J2: a script that is a symlink (unreadable as the managed file): the same refusal and the same record;
  restored to a readable identical file: the same again (no difference is established);
- J3: a real digest difference in a managed script: the pending job is marked `FAILED`
  `workflow_release_changed` (`resume` exit 0), no Workflow script runs;
- J4: no drift: the same kind of pending job is reconciled from the stored decision: `FINISHED`,
  `gate_reached`.

**K. The differences from 1.6.0 and the three new automatic launches (plan decision 3).** For each scenario the
same repository's `explain` under 2.6.0 (1.6.0's decision) and under 2.7.0 (the protocol's): D1
(`REVISING_PLAN`, no REVISE), D3 (`AMENDING_PLAN`), D5 (a current `REVISE` at `"2.1"`), D6 (no checklist
evidence), D7 (unconsumed findings). Then the three new launches run, with their routes, and the roles'
override.

<!-- flow K begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
# each scenario twice: Workflow 2.6.0 (legacy mode, 1.6.0's decision) and Workflow 2.7.0 (protocol mode)
for sc in revising_no_feedback_2.2 amending_2.2 external_review_current_revise_2.1 functional_no_checklist_2.2 functional_unconsumed_findings_2.2; do
  for m in l p; do
    fresh "k-$m" "$m:$sc"; echo "-- K: $sc, Workflow $REL"
    explain | grep -E '^(phase|reason|next automatic action|human gate|declined)' | cut -c1-210
  done
done
echo "-- K6: the three launches that 1.6.0 never made, run (the fake worker does nothing, so the first and last end without progress)"
for sc in external_review_current_revise_2.1:noop functional_no_checklist_2.2:effect functional_unconsumed_findings_2.2:noop; do
  fresh k6 "p:${sc%%:*}"; mode "${sc##*:}"; echo "== ${sc%%:*}"; step; jobs; cat "$T/tasks.log"
  python3 - "$RT" <<'PY'
import glob, json, sys
r = json.load(open(glob.glob(sys.argv[1] + "/jobs/*.json")[0]))["worker_route"]
print("route:", r["role"], r["model"], r["effort"], r["sources"])
PY
done
echo "-- K7: the two new routing roles accept an operator override"
fresh k7 p:functional_unconsumed_findings_2.2; mode noop
(cd /tmp && "$W" --runtime-dir "$RT" --settings "$T/settings.json" --workflow-manager "$SM" --claude-binary "$S/claude-fake" --timeout 60 \
   --role-model apply-functional-review=claude-sonnet-5-5 --role-effort apply-functional-review=low step "$R"); echo "step exit $?"
python3 - "$RT" <<'PY'
import glob, json, sys
r = json.load(open(glob.glob(sys.argv[1] + "/jobs/*.json")[0]))["worker_route"]
print("route:", r["role"], r["model"], r["effort"], r["sources"])
PY
```
<!-- flow K end -->
Expected output:
```text
-- K: revising_no_feedback_2.2, Workflow 2.6.0
phase: REVISING_PLAN
reason: REVISING_PLAN has one legal next step: apply the recorded plan-review feedback
next automatic action: /apply-plan-review wi-1
-- K: revising_no_feedback_2.2, Workflow 2.7.0
phase: REVISING_PLAN
reason: no applicable REVISE: a withdrawal, an absent feedback file, or a verdict of another round
next automatic action: /milestone-plan wi-1
-- K: amending_2.2, Workflow 2.6.0
phase: AMENDING_PLAN
reason: AMENDING_PLAN selects /milestone-plan wi-1, which is model-invocable, but no verifiable ExpectedOutcome is declared for (AMENDING_PLAN, "2.2", /milestone-plan), so this Controller reports it instead of 
declined by this generation (automation-safe, but not automated here): /milestone-plan wi-1
-- K: amending_2.2, Workflow 2.7.0
phase: AMENDING_PLAN
reason: wi-1 is at AMENDING_PLAN
next automatic action: /milestone-plan wi-1
-- K: external_review_current_revise_2.1, Workflow 2.6.0
phase: AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
reason: AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW is a report-only phase (revision 10's scope): Status: REVISE is on file; run /apply-implementation-review
human gate -- what is required: Status: REVISE is on file; run /apply-implementation-review
-- K: external_review_current_revise_2.1, Workflow 2.7.0
phase: AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
reason: a current REVISE verdict
next automatic action: /apply-implementation-review wi-1
-- K: functional_no_checklist_2.2, Workflow 2.6.0
phase: AWAITING_FUNCTIONAL_REVIEW
reason: AWAITING_FUNCTIONAL_REVIEW: no current-round Workflow-Functional-Checklist evidence found
human gate -- what is required: no current-round functional-review checklist evidence commit is on file; run /prepare-functional-review to write and commit the checklist
-- K: functional_no_checklist_2.2, Workflow 2.7.0
phase: AWAITING_FUNCTIONAL_REVIEW
reason: no committed checklist evidence matches the current checklist
next automatic action: /prepare-functional-review wi-1
-- K: functional_unconsumed_findings_2.2, Workflow 2.6.0
phase: AWAITING_FUNCTIONAL_REVIEW
reason: AWAITING_FUNCTIONAL_REVIEW selects /apply-functional-review wi-1, which is model-invocable, but no verifiable ExpectedOutcome is declared for (AWAITING_FUNCTIONAL_REVIEW, "2.2", /apply-functional-review
declined by this generation (automation-safe, but not automated here): /apply-functional-review wi-1
-- K: functional_unconsumed_findings_2.2, Workflow 2.7.0
phase: AWAITING_FUNCTIONAL_REVIEW
reason: FUNCTIONAL_REVIEW.md holds findings not yet applied
next automatic action: /apply-functional-review wi-1
-- K6: the three launches that 1.6.0 never made, run (the fake worker does nothing, so the first and last end without progress)
== external_review_current_revise_2.1
step exit 0
FINISHED | /apply-implementation-review wi-1 | implementation.apply_review | no_progress | none | None  | 
/apply-implementation-review wi-1
route: apply-implementation-review claude-opus-5-5 xhigh {'effort': 'default', 'model': 'default'}
== functional_no_checklist_2.2
step exit 0
FINISHED | /prepare-functional-review wi-1 | functional.prepare | gate_reached | None | None  | 
/prepare-functional-review wi-1
route: prepare-functional-review None None {'effort': 'inherit', 'model': 'inherit'}
== functional_unconsumed_findings_2.2
step exit 0
FINISHED | /apply-functional-review wi-1 | functional.apply_findings | no_progress | none | None  | 
/apply-functional-review wi-1
route: apply-functional-review claude-opus-5-5 xhigh {'effort': 'default', 'model': 'default'}
-- K7: the two new routing roles accept an operator override
step exit 0
route: apply-functional-review claude-sonnet-5-5 low {'effort': 'role-cli', 'model': 'role-cli'}
```
- K: under 2.6.0 the gate or the decline (`declined by this generation`, `report-only phase`) or
  `/apply-plan-review`; under 2.7.0 `next automatic action:` `/milestone-plan wi-1` (D1, D3),
  `/apply-implementation-review wi-1` (D5), `/prepare-functional-review wi-1` (D6) and
  `/apply-functional-review wi-1` (D7);
- K6: each launches (`step exit 0`): D5 and D7 end `no_progress` (the fake worker does nothing), D6 ends
  `gate_reached` with the checklist committed; routes `apply-implementation-review` and
  `apply-functional-review` on Opus at `xhigh`, `prepare-functional-review` inheriting (no model, no effort);
- K7: `--role-model`/`--role-effort` override the new role (`sources` `role-cli`).

**L. Settings generation 3 and the two routing roles (plan C.3).**

<!-- flow L begin -->
```bash
. /tmp/c9-fr/fr.sh; cd /tmp
rm -rf "$S/set"; mkdir "$S/set"
echo "-- L1: the two new roles are settings rows' routing roles; the installed 1.5.0 does not know them"
echo '{"routing": {"roles": {"prepare-functional-review": {"model": "claude-sonnet-5-5"}, "apply-functional-review": {"effort": "high"}}}}' > "$S/set/s.json"
"$W" --settings "$S/set/s.json" settings show 2>&1 | grep -E 'warning|^routing' | norm
workflow-controller --settings "$S/set/s.json" settings show 2>&1 | grep -E 'warning|^routing' | cut -c1-260
echo "-- L2: a settings file filled by this release is generation 3; the installed 1.5.0 warns on show and refuses clean"
fresh l2 p-ready; step >/dev/null
python3 -c "import json; d = json.load(open('$T/settings.json')); print('_table_generation', d['_table_generation'])"
cp "$T/settings.json" "$S/set/filled.json"
workflow-controller --settings "$S/set/filled.json" settings show 2>&1 | head -1 | cut -c1-250 | norm
workflow-controller --settings "$S/set/filled.json" settings clean 2>&1 | norm | cut -c1-260; echo "1.5.0 clean exit ${PIPESTATUS[0]}"
"$W" --settings "$S/set/filled.json" settings show 2>&1 | head -1 | norm
```
<!-- flow L end -->
Expected output:
```text
-- L1: the two new roles are settings rows' routing roles; the installed 1.5.0 does not know them
routing = {"default": {}, "roles": {"apply-functional-review": {"effort": "high"}, "prepare-functional-review": {"model": "claude-sonnet-5-5"}}} (file)
workflow-controller: warning: the settings file /tmp/c9-fr/set/s.json has unknown key(s) routing.roles.apply-functional-review, routing.roles.prepare-functional-review; they are ignored (`workflow-controller settings clean` removes them)
routing = {"default": {}, "roles": {}} (file)
-- L2: a settings file filled by this release is generation 3; the installed 1.5.0 warns on show and refuses clean
_table_generation 3
workflow-controller: warning: the settings file <S>/set/filled.json has unknown key(s) merge; they are ignored (the file was last filled by a newer Controller release (table generation 3, this release's is 1), whose keys these probably are)
error: the settings file <S>/set/filled.json cannot be used: it was last filled by a newer Controller release (table generation 3, this release's is 1), so this release cannot tell that release's keys from retired ones; run `workflow-controller settings clean`
1.5.0 clean exit 20
settings file: <S>/set/filled.json
```
- L1: the two roles are accepted in `routing.roles` by the local build; the installed 1.5.0 warns that
  `routing.roles.apply-functional-review, routing.roles.prepare-functional-review` are unknown and
  ignores them;
- L2: a file this release filled is `_table_generation 3`; 1.5.0 warns on `settings show` and its
  `settings clean` refuses (exit 20); the local build reads it.

**M. The automated evidence** (three to four minutes, under the reaping wrapper):

<!-- flow M begin -->
```bash
. /tmp/c9-fr/fr.sh; cd "$S/src"
last() { tail -3 | sed -E 's/ in [0-9.]+s$//'; }
python3 "$S/reap.py" python3 -m unittest tests.test_protocol tests.test_protocol_schema tests.test_package_structure tests.test_workflow_releases 2>&1 | last
python3 "$S/reap.py" python3 -m unittest tests.test_managed_repo tests.test_protocol_decision tests.test_protocol_equivalence 2>&1 | last
python3 "$S/reap.py" python3 -m unittest tests.test_protocol_job tests.test_protocol_outcome tests.test_protocol_lifecycle 2>&1 | last
python3 "$S/reap.py" python3 -m unittest tests.test_routing tests.test_settings tests.test_observe tests.test_fixtures_git_hygiene 2>&1 | last
# the regressions of the two review rounds, by name (each fails without its fix)
python3 -m unittest -v tests.test_protocol_job.LoopGuardTest tests.test_protocol_job.ExecutedBytesBindingTest \
  tests.test_protocol_outcome.DriftedResumeTest tests.test_protocol.NoGlobalGitIdentityTest 2>&1 \
  | grep -E 'same_second|out_of_band|unreadable_unchanged|identity_check|NoGlobalGit|expected_digests|Ran |^OK' | sed -E -e 's/ \(tests\..*//' -e 's/ in [0-9.]+s$//'
python3 tests/golden/generate_protocol_vs_legacy_differences.py --check; echo "differences golden --check: exit $?"
python3 tests/golden/generate_no_policy_lifecycle.py --check >/dev/null; echo "no-policy golden --check: exit $?"
python3 tests/golden/generate_external_implementation_review_decisions.py --check >/dev/null; echo "external review golden --check: exit $?"
python3 tests/golden/generate_plan_stage_decisions.py --release 2.6.0 --check >/dev/null; echo "plan-stage golden --release 2.6.0 --check: exit $?"
python3 -m unittest tests.test_golden_plan_stage_decisions 2>&1 | last
git status --short | wc -l
```
<!-- flow M end -->
Expected output:
```text
Ran 89 tests

OK (skipped=1)
Ran 98 tests

OK
Ran 66 tests

OK
Ran 180 tests

OK
test_a_no_progress_job_after_an_out_of_band_move_starts_a_new_count
test_same_second_jobs_are_ordered_by_their_decision_time_not_their_random_suffix
test_a_script_replaced_after_the_identity_check_is_not_decided_under
test_a_script_replaced_between_the_identity_check_and_reconcile_fails_the_job
test_unreadable_unchanged_scripts_leave_the_record_untouched
test_scratch_repositories_commit_without_a_global_or_system_identity
Ran 20 tests
OK
/tmp/c9-fr/src/tests/golden/protocol_vs_legacy_differences.json is current
differences golden --check: exit 0
no-policy golden --check: exit 0
external review golden --check: exit 0
plan-stage golden --release 2.6.0 --check: exit 0
Ran 11 tests

OK
0
```
- `Ran 89 tests` OK (one skip), `Ran 98 tests` OK, `Ran 66 tests` OK, `Ran 180 tests` OK: the protocol
  client, schema, package structure and releases; admission, decisions and the 58-fixture equivalence
  comparison; the job, outcome and both-modes lifecycle tests; routing, settings (generation 3), `status`;
- the named regressions (each fails without its fix), `Ran 20 tests` OK;
- the four golden checks `exit 0` (the differences table holds exactly D1-D7), the plan-stage default-file
  check by its test (11 tests OK), and `0` changed files in the clone.

**N. It releases 1.7.0, and its notes pass** (the classification over a scratch origin with the real tags and
`v1.1.1`-`v1.6.0` published; the pull request title is the plan's):

<!-- flow N begin -->
```bash
. /tmp/c9-fr/fr.sh; X=$S/ver; rm -rf "$X"; mkdir -p "$X/bin"
BASE=97b85f04cb62e8f483f7e76bf68a280bdc8c9d4b
git init -q --bare "$X/origin.git"
git --git-dir="$X/origin.git" fetch -q --no-tags "$R0" "+$BASE:refs/heads/main" "+refs/tags/v*:refs/tags/v*" \
  "+$H:refs/heads/milestone/workflow-controller-orchestration-protocol-v1"
git clone -q "$X/origin.git" "$X/work"; cd "$X/work"; git config user.name fr; git config user.email fr@example.invalid
cp "$S/src/tests/fake_gh.py" "$X/bin/gh"; chmod +x "$X/bin/gh"
python3 - "$X/gh-state.json" <<'PY'
import json, sys
repo = "RodrigoFAbreu/workflow-controller"
rel = [{"tagName": t, "isDraft": False, "url": f"https://github.com/{repo}/releases/tag/{t}", "assets": [], "title": t,
        "notes": f"workflow-controller {t}"} for t in ("v1.1.1", "v1.2.0", "v1.2.1", "v1.3.0", "v1.4.0", "v1.4.1", "v1.4.2", "v1.5.0", "v1.6.0")]
json.dump({"repository": repo, "url": f"https://github.com/{repo}", "next_number": 22, "prs": [], "releases": rel, "runs": []},
          open(sys.argv[1], "w"), indent=2)
PY
export PATH="$X/bin:$PATH" FAKE_GH_STATE="$X/gh-state.json" FAKE_GH_ORIGIN="$X/origin.git" FAKE_GH_LOG="$X/gh.log" FAKE_GH_FAIL='{}'
git checkout -q --detach "$H"
python3 tools/release.py check-title "feat: drive Workflow through Orchestration Protocol v1, decisions then outcomes"
Q=$(git commit-tree "$H^{tree}" -p origin/main -m "feat: drive Workflow through Orchestration Protocol v1, decisions then outcomes (#21)")
git push -q origin "$Q:refs/heads/main"; git fetch -q origin; git checkout -q --detach "$Q"
python3 tools/release.py version; python3 tools/release.py classify --commit "$Q" 2>&1 | sed "s/$Q/<Q>/"; echo "exit ${PIPESTATUS[0]}"
git -C "$R0" show "$H:docs/ACTIVE_MILESTONE.md" > "$S/narrative.md"
"$S/venv/bin/python" - <<'PY'
from controller import release_notes as rn
notes = rn.extract_section(open("/tmp/c9-fr/narrative.md", encoding="utf-8").read(), "Release notes")
print("lines", len(notes.split("\n")), "| first:", notes.split("\n")[0], "| problem:", rn.notes_problem(notes))
PY
```
<!-- flow N end -->
Expected output:
```text
ok: feat → minor
1.6.0
ok: RELEASE_DUE: 1.7.0 has no tag and no release
state=RELEASE_DUE
version=1.7.0
tag=v1.7.0
commit=<Q>
exit 0
lines 43 | first: ### The Controller drives Workflow through its protocol (1.7.0) | problem: None
```
Expected: `ok: feat → minor`; `1.6.0` (the highest reachable tag); `RELEASE_DUE: 1.7.0 has no tag and no
release`, `state=RELEASE_DUE`, `version=1.7.0`, `tag=v1.7.0`, `exit 0`; and the 1.7.0 release notes section
of the narrative (43 lines) with `problem: None`. This repository's policy does not opt in to release notes,
so the release itself publishes the fixed text `workflow-controller v1.7.0`.

**O. PR #21.** Read-only.

<!-- flow O begin -->
```bash
cd "$R0" 2>/dev/null || cd /home/rodrigo/Workspace/workflow-controller
gh pr view 21 --json title,isDraft,headRefOid,state
gh pr checks 21 | awk -F'\t' '{print $1 " | " $2}'
```
<!-- flow O end -->
Expected output:
```text
{"headRefOid":"25adda4a2d9c20c912fb1d67e9d48f91f4d393e5","isDraft":true,"state":"OPEN","title":"feat: drive Workflow through Orchestration Protocol v1, decisions then outcomes"}
PR title | pass
validate / package | pass
validate / plan | pass
validate / tests (0) | pass
validate / tests (1) | pass
validate / tests (2) | pass
validate / tests (3) | pass
validate / tests (4) | pass
validate / tests (5) | pass
validate / tests (6) | pass
validate / tests-result | pass
workflow-conformance | pass
```
Expected: the draft pull request at `25adda4`, titled with the plan's declared `feat: drive Workflow through
Orchestration Protocol v1, decisions then outcomes`, and twelve checks, every one `pass` (measured at
`25adda4`; the checklist evidence commit is not pushed, so its own CI does not exist yet).

**P. The documentation agrees with A-L.** Which files name each term (ADR 0010, the guides, `README.md`,
`docs/README.md`):

<!-- flow P begin -->
```bash
. /tmp/c9-fr/fr.sh; cd "$R0"
# which files name each term (README.md, docs/README.md, ADR 0010 and the guides)
for t in workflow_unhealthy no_progress_repeated decision_unstable decision_stale_at_launch workflow_invocation_mismatch workflow_release_changed \
         workflow_unknown_action workflow_unknown_disposition reconcile_invalid completion_not_committed_at_head WORKFLOW_PROTOCOL_FAILED \
         WORKFLOW_PROTOCOL_UNSUPPORTED no_protocol unsupported_protocol_major unknown_action_ids prepare-functional-review apply-functional-review; do
  printf '%-34s' "$t"; grep -l -- "$t" README.md docs/README.md docs/adr/0010-*.md docs/guide/*.md | sed -e 's#docs/guide/##' -e 's#docs/adr/0010-.*#ADR0010#' -e 's#\.md$##' | sort | tr '\n' ' '; echo
done
grep -n "0010" docs/README.md | cut -c1-120
```
<!-- flow P end -->
Expected output:
```text
workflow_unhealthy                ADR0010 automation troubleshooting 
no_progress_repeated              ADR0010 automation troubleshooting 
decision_unstable                 ADR0010 automation troubleshooting 
decision_stale_at_launch          ADR0010 automation troubleshooting 
workflow_invocation_mismatch      ADR0010 automation troubleshooting 
workflow_release_changed          ADR0010 automation installation troubleshooting workers 
workflow_unknown_action           automation troubleshooting 
workflow_unknown_disposition      automation troubleshooting 
reconcile_invalid                 ADR0010 automation troubleshooting 
completion_not_committed_at_head  ADR0010 automation troubleshooting 
WORKFLOW_PROTOCOL_FAILED          ADR0010 automation troubleshooting 
WORKFLOW_PROTOCOL_UNSUPPORTED     ADR0010 automation troubleshooting 
no_protocol                       ADR0010 development installation troubleshooting 
unsupported_protocol_major        ADR0010 development installation troubleshooting 
unknown_action_ids                automation commands 
prepare-functional-review         ADR0010 automation commands runtime 
apply-functional-review           ADR0010 automation commands runtime 
66:| [0010](adr/0010-orchestration-protocol-admission-by-capability.md) | admission of a Workflow release by capability
```
Then read each and check it against the flows:
- `docs/adr/0010-orchestration-protocol-admission-by-capability.md` (amends ADR 0006, which stands for
  2.5.1 and 2.6.0): admission by capability (B), decisions and outcomes from the protocol (C, D, G), the
  currency check and the loop guard (F, H), the executed bytes (I), what stays the Controller's, and the
  exit-20 errors;
- `docs/guide/automation.md`: the protocol mode section, the seven differences D1-D7 and the three new
  launches (K), the gates (E, F, H) and `reconcile` classes (D, G); `docs/guide/troubleshooting.md`: each
  gate and reason with its remedy (E, F, H, I, J); `docs/guide/installation.md` and `development.md`:
  admission, the refusals (B) and the vendored release; `docs/guide/commands.md`: `inspect` keys, `explain`
  and `status` lines (B, D); `docs/guide/runtime.md`: generation 3 and the two roles (L); `workers.md`: the
  release-changed rule (I);
- the `## Release notes` section below (flow N): each claim matches a flow, including "Three launches that
  1.6.0 never made", `AMENDING_PLAN` now launches `/milestone-plan` (K), a repeated no-progress job stops at
  `no_progress_repeated` (H), and a 1.6.0 Controller sharing the settings file ignores the roles and
  refuses `settings clean` (L).

**Q. CI-like: no global Git identity (`GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`).** The protocol
lifecycle once more with none, then the whole suite (about four minutes). Every scratch repository sets
its own identity (the seed, the scenario builder and the driver each do), and `fixtures.commit_all`
supplies the tests' own.

<!-- flow Q begin -->
```bash
# CI has no global Git identity: the lifecycle and then the whole suite with none (every fixture and scratch repository carries its own)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
. /tmp/c9-fr/fr.sh; cd /tmp
git config --get user.name; echo "global identity: exit $? (1 = none)"
fresh q1 p-ready; for s in "" plan-gate "" approve-plan "" impl-gate "" technical-approval "" accept ""; do if [ -n "$s" ]; then usr "$s" >/dev/null; else wcx run "$R"; fi; done
cat "$T/tasks.log"
cd "$S/src"
python3 "$S/reap.py" python3 tools/run_tests.py 2>&1 | grep -E '^# Test run|selected tests|coverage:' | sed -E 's/plan `?[0-9a-f]{12,64}`?/plan <id>/; s/results in .*/results in <dir>/'
```
<!-- flow Q end -->
Expected output:
```text
global identity: exit 1 (1 = none)
/review-plan demo
/milestone-implement demo
/milestone-implement demo
/review-implementation demo
/prepare-functional-review demo
/milestone-plan
/milestone-plan
3011 selected tests, 7 shards (plan <id>); results in <dir>
# Test run: PASS (exit 0)
- plan <id>, profile `local`, 3011 selected tests in 7 shards
- coverage: exact; all 3011 planned tests ran once; 0 missing, 0 unplanned, 0 duplicate
```
Expected: no global identity (`exit 1`), the same seven launches as flow D1, and
`3011 selected tests in 7 shards`, `# Test run: PASS (exit 0)`, coverage exact.

### Known limitations and out of scope

- **Fixture realism.** The user-only steps and the worker effects are the writers `tests/test_protocol_lifecycle.py`
  uses, not the real Workflow commands run by a model; no flow starts a real Claude session or touches a
  real GitHub repository (only `gh pr view/checks` read PR #21). The shim of E, F and I makes a real
  2.7.0 say what it never says (an unknown disposition) or lets "someone else" write between two calls.
- **No live 2.7.0 repository.** This repository stays on Workflow 2.6.0 (plan decision 7); 2.7.0 is
  exercised through the vendored release only. `target_state.read` still parses `WORKFLOW_STATE.json` for
  item selection and display (decision 5); retiring the legacy path is a later milestone.
- **Not shown by a flow:** the same-second job ordering and the unreadable-unchanged-scripts case at
  the `cmd_resume` level (named unit tests in M), a `reconcile` that fails or answers an unknown class
  (`workflow_protocol_failed`), the `completion_not_committed_at_head` fact (unit tests in
  `tests/test_protocol_outcome.py`), and a future `scripts/<package>/` layout (the script-set copy is not
  recursive).
- **Trust model (decision 2).** A script edited by a worker ends *that* job `FAILED` (I2), but the next
  step derives the digests afresh and admits the edited script: there are no digest pins for protocol
  releases. Flow I shows the first half only.
- `tests/golden/generate_plan_stage_decisions.py --check` without `--release` fails identically at the
  base (its `AMENDING_PLAN` cases carry the permitted difference that
  `tests.test_golden_plan_stage_decisions` asserts).
- Left as the plan scoped them: pasted manual verdicts still go through the Workflow's own commands (no
  `record-external-result` use), no automatic acceptance (C10), no notifications (C5).

### After testing

If every flow matches, `/accept-milestone` is the only acceptance command; all seven registry
checkpoints are `COMPLETE`. Findings go to
`.ai-review/workflow-controller-orchestration-protocol-v1/feedback/FUNCTIONAL_REVIEW.md`, and
`/apply-functional-review` routes each: its bounded branch for a same-scope fix, its broad branch (a
`<parent-id>-remediation-<n>` child work item) for new or wider scope.

## Release notes

### The Controller drives Workflow through its protocol (1.7.0)

**A Workflow release is admitted by what it can do.** A target whose
installation record lists `scripts/workflow_protocol.py` and whose
`describe` answers Orchestration Protocol major 1 is admitted whatever
its release number: Workflow 2.7.0 now works, and a later release that
speaks the protocol needs no Controller change. 2.5.1 and 2.6.0 behave
exactly as under 1.6.0. A release with no protocol script, or a protocol
that is not major 1, is refused (`no_protocol`,
`unsupported_protocol_major`).

**Decisions and outcomes come from the Workflow.** For a protocol
target, the next action, the gates and their reasons come from the
Workflow's own `next-action`, bound to a state identity that is checked
again just before a worker is launched. Whether a finished worker made
progress comes from its `reconcile`, on the launch and on `resume`.
The protocol runs from a private copy of the Workflow's managed
scripts under the existing Git isolation, every answer is validated
against the vendored schema, and anything unknown is blocked, not run.
Each step first runs the Workflow's `verify`, and an unhealthy answer
stops at the `workflow_unhealthy` gate. `status` shows a protocol job's
reconcile class, and `inspect` lists any action id the Workflow offers
that this release does not know. New errors:
`WORKFLOW_PROTOCOL_FAILED` and `WORKFLOW_PROTOCOL_UNSUPPORTED` (exit
20).

**Three launches that 1.6.0 never made.** On a protocol target the
Controller applies a `"1"`/`"2.1"` external implementation review that
asks for changes, prepares and commits the functional-review checklist,
and applies the operator's functional-review findings, each without a
prompt. They add two routing roles, `prepare-functional-review` and
`apply-functional-review` (settings table generation 3). Other
differences: `AMENDING_PLAN` now launches `/milestone-plan` instead of
exiting 15, and a `REVISING_PLAN` item with no review to apply launches
`/milestone-plan`.

**What to know when upgrading.** A job records the Workflow release and
the managed-script digests it ran under; a worker that edits a managed
`scripts/workflow_*.py` ends that job `FAILED` with
`workflow_release_changed`, as the legacy pin did. A repeated
no-progress job stops at `no_progress_repeated`. A 1.6.0 Controller
sharing the settings file ignores the new roles, and its
`settings clean` refuses the generation-3 file.

## Status

**Complete.** `workflow-controller-auto-merge-release-wait` (`docs/ROADMAP.md` step C4, section
11.3) reached `MILESTONE_COMPLETE` on 2026-10-03. When a repository opts in
(`milestone_branches.pull_request.auto_merge`) and the operator's settings have not turned it off
(`merge.auto`), the Controller now finishes an accepted milestone by itself:
- **it merges**: one head-bound `gh pr merge --squash --match-head-commit <A>` per attempt at the
  acceptance commit, never GitHub's own auto-merge request; a draft, a later commit, a red check or
  a conflict stops it at a gate that names the exit (`merge_pending`, `merge_held`);
- **it waits for the release**: it classifies the squash commit read-only, waits for the
  publishing workflow, records the published release or stops at `release_failed`
  (`release_pending`, `release_failed`);
- **it closes out and stops**, instead of planning the next milestone in the same run;
- **`run` waits without a worker**: the pending gates are polled every `merge.poll_seconds` for up
  to `merge.wait_seconds` per step; `status`, `status --json` and `inspect` show the merge and the
  release (`docs/adr/0009-auto-merge-and-release-wait.md`).

A policy without the key behaves exactly as 1.5.0 (plan I1).

The user accepted it in functional review round 3 (implementation revision 11), against checklist
evidence commit `5f95a08d66d09509f789084bf6deb4149ee0521a`.
- Plan revision 7 was approved at `a06aeb3` (`EXTERNAL_APPROVE`, review content id `fcdfd33b`).
  Decision 9 deliberately departs from the roadmap's wording: the Controller sends its own
  head-bound squash merge rather than enabling GitHub's auto-merge, which could merge a later
  push. Decision 2 (stop after close-out when opted in) was raised at approval.
- Implementation revisions 1-5 were revised after local and Codex review rounds; the fixes include
  deciding an accepted merge before the pull request's lagging reads (`5689d82`), a fresh merge
  record on `--new-pr` (`c8dae39`), deciding a lost merge reply from the trunk rather than refusal
  text (`4fd9bbe`) and adopting a trunk squash only when its content is the acceptance commit's
  (`a595551`). Both stages approved revision 6 (technical approval `3d875a7`).
- Functional review round 1 (checklist `10a6efd`) found F1-F6 (gate texts, the guides, Ctrl-C in a
  waiting run, the fake `gh`'s merge texts against real `gh` in the optional live flow Q). They
  were fixed in revisions 7-8; revision 9 names a waiting run in exit 45 only when it holds the
  lifecycle lock (`c89f2ba`) and revision 10 tests it (`9efa17c`); technical approval `08dd5c1`.
- Functional review round 2 (checklist `15f7823`) found R2-F1: the F6 body rewording reached
  bindings without `auto_merge`, against I1. Fixed in `7f24c3a` (implementation revision 11); both
  stages approved it (technical approval `6e4e991`, `EXTERNAL_APPROVE` of bundle `0b2b25f3`,
  `CURRENT`). Functional review round 3 re-ran flows D, E, K, M and O, and it was clean.

All six registry checkpoints (`CP1`-`CP6`) are `COMPLETE`, and the registry declares no completion
obligations. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of this transition,
and `active_work_item_id` is now `null`. `docs/ROADMAP.md` marks section 11.3 and step C4 of "At a
glance" complete. The full milestone narrative is archived verbatim at
`docs/milestones/completed/workflow-controller-auto-merge-release-wait.md`. It covers the goal,
checkpoint progress, the review-round fixes, functional review round 3's checklist and the 1.6.0
release notes.

This milestone's own deliverables remain live in the tree, unmoved (the archive file's own preface
says why):
- `docs/ai-workflow/CONTROLLER_AUTO_MERGE_RELEASE_WAIT_PLAN.md` and its registry, artifacts and
  mapping files, still at the paths its `docs/ai-workflow/WORKFLOW_STATE.json` entry declares;
- the changes to `controller/milestone_branch.py`, `cli.py`, `decision.py`, `forge.py`, `job.py`,
  `lock.py`, `observe.py`, `release_txn.py`, `repo_policy.py` and `settings.py`;
- the fake `gh`'s merge, merge-state, workflow-run and release model (`tests/fake_gh.py`), the
  regenerated `tests/golden/no_policy_lifecycle.json`, and the additions to `tests/test_cli.py`,
  `test_evidence.py`, `test_forge.py`, `test_lock.py`, `test_no_rewrite_invariants.py`,
  `test_observe.py`, `test_pull_request_lifecycle.py`, `test_release_txn.py`,
  `test_repo_policy.py`, `test_settings.py` and `test_trunk_preflight.py`;
- `docs/adr/0009-auto-merge-and-release-wait.md`; `README.md`; `docs/guide/milestone-branches.md`,
  `automation.md`, `ci-and-releases.md`, `commands.md`, `concepts.md`, `runtime.md` and
  `troubleshooting.md`, and `docs/README.md`.

`.workflow-controller/policy.json`, `pyproject.toml`, `setup.py` and `.github/workflows/` are
unchanged from the base `854d25c` (plan I9: this repository does not opt in yet).

Deferred follow-ups, not conditions of acceptance:
- **Release 1.6.0.** The milestone branch is on Draft PR #18, titled
  `feat: auto-merge an accepted milestone and wait for its release`. The pushed head is `5f95a08`;
  this acceptance commit is local only. This repository has not opted in to `auto_merge`, so the
  release goes the 1.5.0 way: the next Controller step pushes it and runs readiness. Once every
  check passes, the PR is marked ready and merged with "Squash and merge", without editing the
  commit message. `main.yml` then classifies `RELEASE_DUE` 1.6.0 from `v1.5.0` and publishes it,
  and the next Controller step closes the milestone out (`MERGED_SQUASHED` → `CLOSED`).
- **Install timing.** 1.6.0 goes into the shared install only between Workflow Manager milestones
  (shared lane plan), since the Manager lane's Controller runs from it.
- **The post-C4 docs pull request**:
  - `docs/releases/1.6.0.md` from the archived narrative's `## Release notes` section (release notes
    are still not turned on in `.workflow-controller/policy.json`);
  - the roadmap order the user set on 2026-10-02: C9, then C8, then C5, then C6, C7, C10 and C11.
    C8 before C7 means C8 tracks Claude's limits on its own and reads Codex's without the C7 seam,
    or defers the Codex part; C8's plan says which;
  - in `AMENDING_PLAN`, `run` declines `/milestone-plan` (exit 15) because no `ExpectedOutcome` is
    declared for (`AMENDING_PLAN`, `"2.2"`, `/milestone-plan`), reported by the Manager lane on
    2026-10-02; it sits next to the `explain` item already listed under "Known follow-ups carried
    forward";
  - two CI timing flakes in code this milestone did not touch, both passing on a re-run:
    `tests.test_resume.ReattachAfterControllerLossTest.test_r15` and
    `tests.test_worker.OwnershipTest.test_a_gated_escapee_is_published_as_group_then_as_tag`;
  - a CP3 supervise test can leak its fake worker (`tests/fake_claude.py`) under load, which left
    a Controller job draining for three hours on 2026-10-02.
- Opting this repository in to `auto_merge` (and to release notes) is a later small `chore:` pull
  request, after 1.6.0 is released and installed into the shared install.
- Left as the plan scoped them: the fix loop for a red pull request after acceptance and automatic
  acceptance (C10), integrating `main` into a milestone branch, merge mode and merge queues,
  publishing or retrying a release, and notifications (C5). A close-out from the trunk does not
  fast-forward `main`, as in 1.5.0.
- `tests/golden/generate_plan_stage_decisions.py --check` (without `--release`) reports that its
  `AMENDING_PLAN` cases differ in this environment. It does the same at the base, so this milestone
  did not change it, and `tests.test_golden_plan_stage_decisions` passes.
- Carried over, unchanged: the deferred items of the earlier milestones, listed in their
  acceptance commits.

**Next action:** release 1.6.0 as above, then merge the post-C4 docs pull request. After close-out,
run `/milestone-plan` for step C9 of "At a glance": the Controller on Orchestration Protocol v1
(section 1.7), which the user put next on 2026-10-02. Its dependency W1, Workflow 2.7.0, was
published on 2026-10-02. (The roadmap table still lists C5 next until the docs pull request
reorders it.) Plan it from `main`'s tip, with that base passed explicitly
(`/milestone-plan <main tip>`). `/milestone-plan` creates a fresh `work_items` entry and claims
`active_work_item_id`, ready for `PLANNING`.
