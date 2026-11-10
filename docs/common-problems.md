# Common problems

> For: anyone whose run stopped and wants the quick fix. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0.

Start with `workflow-controller explain <repo>`. It is read-only, exits 0 (but refuses with exit 20 when the repository is not admitted, as on an unmanaged one, and under Controller 1.7.0 also when `verify` reports `warn`), and prints the pending job files with the command that clears each one, the next decision with its evidence and, at a gate, exactly what a person must do. Each entry below is the symptom, the one-line fix and where the detail is. What each exit status means is in [exit codes](exit-codes.md); the long account of every stop is the [troubleshooting guide](guide/troubleshooting.md).

## The run stopped and exited 10

Not a failure: it is a [gate stop](glossary.md#gate-stop), the Controller waiting for you. Run `workflow-controller explain <repo>`, do what it says, then run again. See [run](run.md#steps).

## The run stopped at a Workflow 2.8 automatic gate

Controller 1.7.0 to 1.13.1 report `workflow_unknown_disposition` here and show the action (`plan.satisfy`, `implementation.satisfy` or `acceptance.satisfy`) but no command to run: run `/satisfy-gate plan|implementation|acceptance <id>` yourself in a Claude session, or update the Controller. Later releases launch it themselves; a refused validation (a requirement unmet) is a stop with no progress, and `explain` shows the unmet requirement. At acceptance, `explain` may first report `functional_evidence_needed` (record the functional result) or `pr_evidence_needed` (push the pull request, wait for its checks, then run `/satisfy-gate acceptance <id>`), or you can set `gates.acceptance.human: true`, commit the file, run `/adopt-gate-policy` in a Claude session, and accept with `/accept-milestone`. See [run](run.md#steps), which has an example command. To switch the repository to human gates, see the Workflow's [gates page](https://github.com/RodrigoFAbreu/workflow/blob/main/docs/gates.md), commit the policy file (`docs/ai-workflow/GATE_POLICY.json`) and run `/adopt-gate-policy` in a Claude session (see the next entry and the [gate policy reference](https://github.com/RodrigoFAbreu/workflow/blob/main/payload/docs/ai-workflow/GATE_POLICY.md), which explains when to commit and adopt it).

## The run refuses with "protocol verify gave an answer outside the protocol" and `'warn'` (exit 20)

Fixed in Controller 1.7.1: it treats a `warn` check as advisory, so `explain`, `step` and `run` carry on and `explain` shows the check's detail as a `verify <check>: warn: ...` line. Later releases treat a status or other value a protocol 1.x minor release adds as advisory, or, where a decision depends on it, stop: an unlisted action id, worker role or disposition is a blocked gate before any launch (`workflow_unknown_action`, `workflow_unknown_worker_role`, `workflow_unknown_disposition`), and an unlisted reconcile class or record stage, which arrives after the worker ran, ends the job `FAILED` with the evidence reason `workflow_unknown_value`; only a protocol major change is refused with exit 20. Controller 1.7.0 does not know `warn`: the Workflow's `verify` reported a check as `warn`, so it refuses `explain`, `step` and `run` (`inspect` still works); upgrade to 1.7.1 or later. The `warn` itself has a cause to clear: for example, the gate policy file (`docs/ai-workflow/GATE_POLICY.json`) is present, committed or not, and not adopted yet. Commit the policy file, then run `/adopt-gate-policy` in a Claude session (the timing advice is under Human gates in [run](run.md#steps)). Some causes are not cleared by adoption. To see which one, run `python3 scripts/workflow_protocol.py --protocol-major 1 --repo-root . verify` in the repository and read the `gate_policy` check's detail.

A lowered gate is such a cause. While an adoption that lowers a gate (for example, switching a gate back to automatic) is the newest adoption, Workflow 2.8.0 and later report `warn`; Controller 1.7.1 shows it and carries on, and 1.7.0 refuses. The warning stays until a later adoption that lowers nothing becomes the newest. Do not adopt again only to hide it: the Workflow treats the warning as the expected signal of a lowered gate. See the [gate policy reference](https://github.com/RodrigoFAbreu/workflow/blob/main/payload/docs/ai-workflow/GATE_POLICY.md) and [compatibility](compatibility.md).

## The run exited 16

`run` reached `--max-steps` with work still outstanding. Run it again, or raise `--max-steps`. See [run](run.md).

## A step refuses because of a pending job (exit 20)

A previous [job](glossary.md#job) was never reconciled. Run `workflow-controller resume <repo>`. A record `resume` cannot reconcile needs `workflow-controller resume --abandon <job-id> <repo>`. See [job dispositions](guide/workers.md#job-dispositions).

## A worker failed or stopped (exit 30 or 35)

`explain` shows what was checked and why the job's expected outcome was not met. Fix what it names and run again, or finish the step by hand. The job's log, which `workflow-controller follow <repo>` and `status` point to, shows what the worker did. See [troubleshooting](guide/troubleshooting.md#common-situations).

## A Codex worker's landing was refused

This applies only to the fixture role behind the Codex worker seam (H4e); no real role runs a Codex worker yet. The job ended `FAILED` with `reconciliation_evidence.code` `CodexLandingRefused`, a `cause` and the clone's path in `reconciliation_evidence.clone`. The commits the worker made are in that clone, nothing was landed, and the managed repository is exactly as it was, except for `landing_unverified` and `merge_left_tree_dirty` below. The mechanism is in [harnesses](guide/harnesses.md#a-codex-workers-private-clone-and-its-landing). By cause:

- `managed_branch_moved`, `managed_tree_not_clean`: the managed branch moved, or the tree gained a change, between the clone and the landing. Put the repository back (commit or discard the change) and run again. A file ignored only through your own global ignore file (`~/.config/git/ignore`) counts as untracked here, because the Controller reads with a scrubbed environment; ignore it in the repository's `.gitignore` or `.git/info/exclude` instead.
- `runtime_inside_managed`: the runtime root is inside the managed repository. Keep it outside, for example with `--runtime-dir` on a path elsewhere.
- `managed_hooks_configured`: the managed repository's configuration defines a Git hook (`hook.*`). Remove it for the run.
- `clone_git_changed`, `extra_refs`, `sandbox_breach`: the worker changed the clone's `.git` (hooks, config, a link, alternates), made a tag, branch or stash, or the launcher judged the run a breach. Inspect the clone; the Controller lands none of it.
- `not_a_descendant`, `merge_commit`, `too_many_commits` (more than 200), `submodule`: the worker's commits are not a linear stack of at most 200 commits on the base without a submodule. Rework them in the clone or by hand.
- `fetch_failed`, `tip_moved`, `would_overwrite_ignored`, `fast_forward_failed`, `landing_error`: Git refused the fetch or the fast-forward, the clone's tip moved after it was checked, or the move would overwrite an ignored file in the managed tree. The record's `message` carries Git's words. The managed repository is unchanged.
- `landing_unverified`: the managed branch moved, or cannot be shown unmoved (`moved` is `true` in the landing block). Look at `git log` and the tree before anything else; nothing was repaired. One ordinary way to get here: a commit that removes a path from `.gitignore` lands, and the files it stops ignoring are then untracked, so the check after the move fails.
- `merge_left_tree_dirty`: the branch is still at the base but a failed merge left the worker's content in the tree and the index (the paths are in the landing block's `dirty` list). This is also why the next job refuses with `managed_tree_not_clean`. Clear it with `git restore --staged --worktree .` in the managed repository. If the merge was killed by the Controller's Git timeout, it may have left `.git/index.lock`: remove that file first, because the restore cannot run while it exists.
- `landing_interrupted`: the Controller was lost during the landing, before the branch moved. `resume` landed nothing and deleted any leftover `refs/controller/landing/<job id>`.
- `landing_unreadable`: the record's `clone` or `landing` block or the repository could not be read when `resume` looked, so whether the branch moved is not known (`moved` is `null`). `resume` touched nothing in the repository, so a leftover `refs/controller/landing/<job id>` stays. Fix what could not be read, look at `git log`, and delete that ref by hand if it is there.

A refused landing keeps its clone. If `clone_removed` is `true` in the landing block and `clone` is `null`, the clone is not on disk (an earlier recovery or you removed it) and there is nothing to remove. Otherwise remove it with `rm -rf` of the exact path in `reconciliation_evidence.clone` (Git's object files are read-only, so name the path; do not glob). Any other job that leaves a clone on disk, a `FINISHED` one included, names it the same way, with the code `CodexCloneKept` when it has no evidence of its own. There is no `runtime` command to do it and no retention. A landing whose ref could not be deleted afterwards keeps its outcome, with `ref_left: true` and the ref's name in `ref`; delete it with `git update-ref -d refs/controller/landing/<job id>` in the managed repository.

If `.git` or the managed root is removed under a `COMPLETED` Codex record, `resume` and `resume --abandon` both refuse (`NotARepositoryError` on the command, `GitDirectoryUnresolvableError` in the job layer) and write nothing. Removing the directory does not make `--abandon` run. Restore `.git`, or the repository at the same path, and run `resume --abandon`. If the repository is gone for good, remove the record file and the kept clone by hand.

## A Codex apply was refused (`carry_*`)

This applies when `apply-plan-review` or `apply-implementation-review` is routed to `codex` ([harnesses](guide/harnesses.md#routing-the-two-apply-roles-to-codex)). The job ended `FAILED` with `reconciliation_evidence.code` `CodexCarryRefused`, a `cause`, the `stage` and, after the worker ran, the clone's path and the members not yet written. Before the worker ran nothing was launched. By cause:

- `carry_foreign_dirt`: the managed repository has uncommitted paths outside the role's carry set (the message names them). Commit or discard them, then run again. The Controller never carries or leaves alone a path outside the set.
- `carry_commit_pending_failed`: the implementation role's pending feedback entry could not be committed through the Workflow's writer, or the commit is not the only step on the branch. Look at `git status` and `git log`, finish the commit with the Workflow's own command, then run again.
- `carry_member_refused`: a carried path is a link, a hardlink, a special file, a submodule, outside the repository, a credential-like name or holds a private-key armour line. The `detail` names the check; fix or remove that path.
- `carry_unexpected_commit`, `clone_git_changed`, `carry_index_changed`: the worker made a commit, ref or tag in a clone that should have none (the plan role), changed the clone's `.git`, or changed the intent-to-add set. Nothing was carried out. Inspect the kept clone; do not copy from it blindly.
- `carry_out_of_set`: the worker changed a file outside the carry set. Nothing was carried out. The clone is kept so you can see what it changed.
- `carry_state_foreign`: the worker's state file changed something other than this work item's entry, or is not a state document. Nothing was carried out.
- `carry_managed_changed`: a member of the managed repository changed while the worker ran (someone else edited it), or a write failed part-way. Nothing already written is undone; `resume` finishes a carry out that was interrupted at a member boundary, and a member someone else changed must be reconciled by hand from the kept clone.
- `carry_clone_gone`: the clone was removed before the carry out finished. The evidence names the members not yet written; redo the apply for them.

A refusal after the worker ran keeps the clone; remove it with `rm -rf` of the exact path in the evidence once you no longer need it.

## A routed apply stopped at `carry_bundle_regeneration_pending`

The Codex apply succeeded and its files are in the managed repository, but the Controller regenerates no review bundle and runs no script of the managed repository, so the lane stops at its own gate (the job is `GATE_BLOCKED`). This is expected, not a failure. In the managed repository, in the order the gate prints:

1. After an **implementation-role** landing, read the landed range first (`git log -p <carry base>..<landed head>`, both in `reconciliation_evidence`): the generator you run next executes `scripts/` and any module the landing added to its import path.
2. Run the generator the gate names: `scripts/prepare-ai-review.sh <base> plan <work item>` for the plan role, `scripts/prepare-ai-review.sh <base> post-fix <work item>` for the implementation role.
3. Take the next step (the gate prints the command), for example `controller resume` or the next run.

Running the generator safely without a person is [H4e3b](items/workflow-controller-h4e3b.md); until then it is yours to run.

## The command exited 45 and nothing was launched

The worktree is held: another Controller, a `run` waiting for checks or a merge, or a worker that may still be running holds its lock. Nothing is wrong. Wait, or follow the message; after a Ctrl-C or a drain timeout, `workflow-controller resume <repo>` re-attaches. See [restart](guide/workers.md#restart-resume-re-attaches) and [the drain bound](guide/workers.md#owned-processes-the-daemon-list-and-the-drain-bound).

## The command exited 17 and nothing was started

The usage budget held the job back before it started, so there is no job to resume. (A message that says a worker "reached the usage limit mid-job" is a different case: see [a worker hit a usage limit while it ran](#a-worker-hit-a-usage-limit-while-it-ran).) The message says which of two causes it was:

- **A timed pause**: the Claude or Codex window would pass its threshold (`usage.pause_at_percent`, `usage.weekly_pause_at_percent`). `run` normally waits for it by itself; `step`, or a wait longer than `usage.max_wait_seconds`, stops instead. Run again after the resume time the message names (in local time).
- **A cap**: the run or the repository has spent `usage.run_cap_percent` or `usage.repository_cap_percent`, counting work still outstanding. No reset time is waited for. Raise the cap or `--usage-cap`, start a new run for a run cap, or for a repository cap run again after the five-hour window resets, when its spending falls.

`workflow-controller usage` shows the readings and reservations. See [pausing for usage limits](run.md#pausing-for-usage-limits), [`usage`](guide/commands.md#usage) and [exit codes](exit-codes.md).

## No notifications arrive

Nothing in the lifecycle depends on a notification, so a step that succeeded
is not undone by one that did not send. To find out why none arrived:

1. Run `workflow-controller notifications`. It names where it looked for the
   server address and the key and which it used (never the key). "Not
   configured" means neither was found: set `notifications.url` and
   `notifications.api_key_file`, or the `SIGNALHUB_URL` and
   `SIGNALHUB_API_KEY_FILE` variables, in the environment the run starts in.
   A run started by a script that does not export them finds only the
   settings file.
2. Check that the type is on. `notifications.enabled: false`, a file named
   `disabled` in `~/.config/signalhub/`, or `enabled: false` for the event
   type (`usage_released` and `usage_reservation_expired` are off by
   default) sends nothing.
3. Run `workflow-controller notifications --test`. It exits `0` when the
   server accepted the test, `1` when it rejected it, `3` when it could not
   be reached and `2` when nothing was sent.
4. Read `runs/<run_id>/notifications.jsonl` in the runtime root (outside a
   run, `notifications.jsonl` there). Each line is one event's outcome and
   its key: `sent` and `duplicate` arrived; `rejected` (a refused request),
   `conflict` (the server holds that key for different content) and `failed`
   (attempts spent) did not; `skipped` is the pause after repeated failures;
   `dropped` means the queue was full. The run's `events.jsonl` ends with a
   `notifications_summary`.

A notice for a step that was cut off when the process died is derived again
by `resume`, for a job that ended in the last 30 minutes. See
[unattended runs](guide/automation.md#notifications-from-an-unattended-run).

## A review the Controller ran did not finish

Only when the Controller ran the review itself (`reviewer.harness` set, or a same-provider review under `review.mode`; the same outcomes apply to the Claude reviewer, with the Claude windows). The Controller started the external review itself and the job did not hand a verdict to the Workflow. `explain` and the job record say which of these it was; the external review gate is still open in every case, so you can always do the review by hand and paste the verdict.

- **Exit 35, `INCOMPLETE`**: the reviewer hit a usage limit, passed `reviewer.timeout_seconds`, ended with no verdict, or the repository or the Workflow state changed while it ran (the verdict is then never ingested). Run again. After a limit, that `run` is held at admission (exit 17 for `step`) until the reviewer's own window resets (the Codex windows for a Codex review, the Claude windows for a Claude review), because the review's retained rollout or stream is counted.
- **Exit 30, `FAILED`**: the verdict's header did not match the bundle exactly even after the one retry, the retry's `Status:` differed from the first verdict's (the two attempts disagreed; both verdicts are in the job record and nothing is ingested), the Workflow refused the ingest (`refused`, or `not_applicable` because the stage had moved), or the reviewer failed with no limit text. A limit is recognised only from the harness's own record (Codex's rollout, or the rate-limit result in the Claude reviewer's stream), never from words in the transcript, so a review whose files merely mention a rate limit is `FAILED`, not `INCOMPLETE`. Nothing was ingested. Fix what the record names, or review by hand.
- **Exit 20 before anything started**: `reviewer.harness` names a harness that is not registered. Nothing was recorded.
- **Exit 30, `ReviewLaunchRefused`**: the launch boundary was not reproduced. For Codex: an outside configuration layer, an MCP server in one, an unlisted feature, a managed provider, `model_catalog_json`, no credential file. For Claude: a command line that differs from the pinned one (`argv`), a `reviewer.same_provider_model` that is not a full model id such as `claude-opus-5-5` (an alias like `opus` is refused), a session whose `init` event reports another model, tools, MCP servers or permission mode (`init`), no credential file (`credentials`), an unexpected entry in the scratch home (`home`), or a managed settings or MCP layer on the host that carries content (`layer`). The record is `FAILED` and the message names the check. The checks made before launch (`argv`, the model id, `credentials`, `home`, `layer`, and the Codex causes) leave `not_started: true`: no reviewer was run and nothing was spent. The `init` check is made only after the Claude process has run, so an `init` refusal, or any refusal at the retry's re-check, follows a charged run: the record has `not_started: false`, and the run's cost and tokens are in `review.telemetry` for a Claude review. A Codex review keeps no figures, and neither does a Claude run whose stream had no `result` event.
  - **After a Codex update:** the refusal names the check and the feature, layer, state entry or flag that changed (for 0.163.0, three newly enabled features), never a version string, because none is checked; an update that changes none of them is accepted. The refusal is correct: run the Codex review by hand until a person re-measures the pin (the free part of `tests/harness_contract/codex/README.md`) and moves it. `verify-codex` (planned) will replace this manual step.
- **Exit 20, a Codex review failed with `refusing to remove ... is a symbolic link`** (the record is `FAILED`, `outcome: error`, `not_started: true`): the worktree's `.agents` is a symbolic link. The review removes `.agents/skills` and `.agents/plugins` from its private copy of the worktree, and the Controller refuses to remove a path below a link. The link's destination is untouched and nothing was spent. Remove the link from the repository, or replace it with a real folder, then run again.
- **It cost more than expected**: the reviewer spawned a sub-agent, which forks the parent's context. The job record's `review.subagents` and the step's event give the count and the tokens. The prompt asks the reviewer not to spawn; Codex offers no switch that removes the tool.
- **After Ctrl-C or a crash**: `workflow-controller resume <repo>` ends the review's process group, copies the credentials back, removes the copy and closes the record.

See [pausing for usage limits](run.md#pausing-for-usage-limits), [`resume`](guide/workers.md#restart-resume-re-attaches) and [ADR 0013](adr/0013-review-only-harness-seam.md).

## A run stopped at review_mode_unavailable

`step` or `run` exits `10` with the reason `review_mode_unavailable` at an external review gate. Nothing was launched. Three causes:

- **`cross-model` without a distinct provider**: `reviewer.harness` is unset or names the workers' own provider. Set `reviewer.harness` to a distinct one, or run with `--review-mode manual`.
- **Worker roles on more than one provider family** (`auto`, `cross-model` or `single-provider`): there is no single provider for a reviewer to be distinct from, so the step stops. The message names the stage's local review role (`review-plan` or `review-implementation`), its harness and family, and `reviewer.harness` and its family, and says whether the two are of one family or distinct; when they are distinct, the other roles cause the stop. Route every worker role to harnesses of one family (and, for a collision, route the local review role or `reviewer.harness` to another family), or run with `--review-mode manual`, which this stop never applies to.
- **`single-provider` without the lowered-policy signal**, or a same-provider review the Workflow refused (`DistinctReviewerModelsRequiredError`, job `INCOMPLETE`). The stop holds while the state and the policy signal are unchanged and the mode again selects a same-family reviewer. The message names the ways out that apply to the mode and phase:
  1. lower the stage's `require` by adopting a policy (`docs/ai-workflow/GATE_POLICY.md`); an open review bundle is not changed by it, so withdraw the bundle and generate it again first;
  2. under `auto` only: turn the stage's gate human with an uncommitted edit of `docs/ai-workflow/GATE_POLICY.json`, which makes the next step stop at the ordinary gate;
  3. record a verdict from a distinct model by hand (the Workflow's external-result ingest), turn on a distinct provider (`reviewer.harness` or `--review-mode cross-model`), or run with `--review-mode manual`.

`explain` prints the same text. See [the mode table](guide/runtime.md#what-it-holds) and [the same-provider reviewer](guide/workers.md#the-same-provider-reviewer).

## A run stopped at the review round cap

`run` or `step` exits `10` at a gate whose reason code is `review_round_cap_reached`. The plan or the implementation stage has reached a review round cap, so the next `apply_review`, local review or external review was not launched. Nothing ran and the Workflow state is unchanged; the stop is one `GATE_BLOCKED` job record, which no round count includes. The gate text names the stage, which cap fired (`local`, `review.local_round_cap`, default 6, or `external`, `review.external_round_cap`, default 4), the round and the cap (for example `plan review external round 4 has reached the external round cap of 4`; with the single fallback number, `plan review round 4 has reached ...`), and where the rounds came from: `given by the Workflow as split counts` means the Workflow gave both the local and the external round (Workflow 2.15.0 and later); `the Workflow gave no split counts, so this single number is checked against the external cap` means it gave neither, and the text then names the one number used, which is the Workflow's own `review_round` (`given by the Workflow`) or the Controller's count from its job records (`counted from the Controller's job records`, and for the plan stage the Workflow's own count may then be higher; a work item the Workflow governs as `"1"` is counted at `AWAITING_EXTERNAL_PLAN_REVIEW`). The Controller cannot tell an older Workflow, an item created before the counts, a promoted item and a malformed counter apart, so the text names no reason. The Controller never accepts at the cap itself. You choose, in this order:

1. **Raise the cap that fired and run again**: `workflow-controller run --review-local-round-cap N <repo>` or `--review-external-round-cap N`, or set `review.local_round_cap` or `review.external_round_cap`. The option applies to that command only, so the next plain `run` stops again at the next capped action of the item. `explain` shows the settings' caps, not an option's. `workflow-controller settings show` prints the caps in force and their origin. The deprecated `--review-round-cap` and `review.round_cap` still set every cap you have not set yourself.
2. **Accept at the cap** with `/approve-review`, where the Workflow's gate is reachable (after a `REVISE`, with the confirmation it asks for).
3. **Withdraw and re-scope** with `/milestone-plan <work-item-id>`.

## The usage record cannot be read

`step` or `run` stops with "the usage record ... is not a version 2 object" or "cannot be read". `usage.json` in the runtime root is either damaged or was written by a newer Controller release than this one. It is never overwritten. To keep working, set `usage.enabled: false` (the budget is then off and the record is not touched), or install the release that wrote it. Deleting the file also works but forgets every lane's reservations and spend. A job whose accounting was pending is settled by repairing or restoring the original record in place, but only before the next `step`, `run` or `resume`: one of those that runs on a fresh record finds the job's reservation unknown and marks its accounting done with no charge (an `usage_accounting_orphan` event), and restoring the record afterwards no longer settles it. Restore the record first. (`usage` alone does not do this.) `step`, `run` and `resume` never lose a job's outcome to it: the accounting is skipped with a warning, the job stays unaccounted, and a release that can read the record settles it later. The `usage` view itself reports the error.

## A worker hit a usage limit while it ran

The budget forecasts, it cannot stop a job that exceeds its forecast. The Controller recognises the provider's limit from structured fields in the worker's stream (a rejected rate-limit event, a 429 result and the reset time), not from text. What happens next depends on what the worker did first:

- **It changed nothing** (no commit, no tracked-file edit; untracked files do not count): the job is recorded `INTERRUPTED` and `run` waits for the reset plus `usage.resume_grace_seconds`, with no lock held, then repeats the step without counting it. This works with `usage.enabled` false too. `step` stops with exit 17 and a line naming the reset time; run it again after that time. A wait longer than `usage.max_wait_seconds`, or three limited attempts in a row, also stops with exit 17, and the line says the worker ran (it never says "nothing was started").
- **It had changed state**: the job is `FAILED` (exit 30) and its evidence carries the limit. Do not repeat it blindly: run `explain`, check what the worker left, wait for the reset (`workflow-controller usage`), then run again.

See [pausing for usage limits](run.md#pausing-for-usage-limits) and [how the limit is recognised](guide/workers.md#how-a-usage-limit-is-recognised).

## A worker's shell command waits for a y/n answer

An operator's shell can alias `rm` to `rm -i` and `cp` to `cp -i`, and a background command in a worker then waits for an answer nobody gives. The worker lifecycle note now tells workers to use `command rm -f` and `command cp -f`. In a command you run for a worker, or in a script it calls, do the same.

## I pressed Ctrl-C

Only the Controller stopped. The worker keeps running headless and keeps the worktree locked. Run `workflow-controller resume <repo>` to re-attach and let it finish. See [troubleshooting](guide/troubleshooting.md#common-situations).

## The repository is refused as unmanaged or unsupported (exit 20)

Workflow Manager must verify the installation, and the Controller must admit its Workflow release. Run `workflow-manager verify <repo>` to see what is wrong with the installation, and check [compatibility](compatibility.md) for the release. See [troubleshooting](guide/troubleshooting.md#the-repository-is-refused-as-unmanaged-or-unsupported).

## A step refuses with WORKFLOW_RELEASE_CHANGED

The repository's Workflow release changed after the command started, usually because Workflow Manager's `update` ran. Nothing was launched; run again and the new release is admitted if the Controller supports it. See [troubleshooting](guide/troubleshooting.md#workflow_release_changed-and-workflow_release_changed).

## A plan or milestone was refused as malformed

A work item whose state names a registry file that does not exist is refused before any decision. Finish the planning command in a supervised session, then run again. See [troubleshooting](guide/troubleshooting.md#common-situations).

## A run or step refuses a model or effort (exit 20)

Since 1.22.0 the message reads `role '<role>' routes the model '<value>' to the harness '<harness>', which declares [...]` (or the effort), followed by where the model, effort and harness came from, and nothing was written or launched. A model the harness does not list is added to the `harness.models` entry of the settings file; an effort it does not list is changed in the routing. See [Upgrading to 1.22.0](compatibility.md#upgrading-to-1220) and [the routing section](guide/runtime.md#the-routing-section).

## The settings file is refused (exit 20)

The message names the file and the key. Fix that key by hand and run again, or move the file aside and the next `step`, `run`, `resume` or `milestone-binding` creates a fresh one. `workflow-controller settings path` prints the file in use. See [troubleshooting](guide/troubleshooting.md#the-settings-file-is-refused-settingserror).

## The run stopped at the functional review (row 38e)

From Controller 1.18.0, under an automatic acceptance gate, the Controller runs the functional tester itself and stops for a person only for a fault: `flows_not_runnable` (flows that need the network, a pull request or a tool outside the sandbox), `functional_tester_unavailable` (see below), `checklist_unrecognised` (no flow recognised, or only some items of the checklist, or a flow of another shape that lies outside the flows read: the message names the others; or text between two flows that no flow holds; or a bold `Flow` line that no shape reads, such as `**Flow: Title**`, `**Flow 1 Title**` or `**Flow 1**: Title` next to a recognised flow (the message names its line; write it as `**Flow K: Title.**` or a `### Flow K: Title` heading, or put it inside a flow); or a link that leaves the repository; a missing section names the heading to write), `results_invalid`, `isolation_breach`, `tested_bytes_unbound`, `recording_refused` or `functional_rounds_exhausted` (three failed tester runs). Each message names its remedy: run the head's flows by hand and record one `functional_evidence` result per flow, or set `functional.automated` to `false`. See [the automated functional review](guide/automation.md#protocol-mode-workflow-27-and-later) and [troubleshooting](guide/troubleshooting.md#common-situations).

### `functional_tester_unavailable`

The Controller stopped before it launched the tester. The message names the cause. The host causes (the first five
below) are found before any usage is reserved, so the stop is one record and the usage budget shows no reservation for it;
the others arise at launch, because they need a job or a built sandbox:

- **No `claude` binary found.** Install `claude` or put it on `PATH`.
- **The functional-tester role was refused** (for example by the routing file). Fix the role in the routing file.
- **`bwrap` or `socat` is not installed**, or `bwrap` cannot make an unprivileged namespace here. Install them, or
  allow user namespaces on the host.
- **The kernel does not accept a seccomp filter**, so the sandbox cannot block Unix sockets. Use a kernel that does.
- **The `claude` build is not a verified one.** Only a native build whose SHA-256 is in `VERIFIED_NATIVE_BUILDS`
  (Claude Code 2.1.303 for 1.18.0) or recorded by `workflow-controller verify-claude` is admitted; a launcher or a
  script-launched `claude` is refused. The message names the build's digest. Run `workflow-controller verify-claude`:
  it runs the live sandbox probes against the `claude` on `PATH` (up to two short model turns, with your
  credentials) and records the digest only when every probe holds, Claude Code 2.1.304 and later included (the tester's
  writable folders lie outside the clone behind links, so the build's deny-above-wins rule does not refuse them). The record belongs to one runtime root, so a
  source checkout and the shared install each need their own run. If the message says the record was ignored, the
  file is malformed, a symlink, or owned by someone else or writable by others; delete it and run the command again.
- **The fresh clone did not match the manifest** of the candidate the Controller captured (`clone_mismatch`). Run
  the step again; if it repeats, look at the repository for files that change on checkout.
- **No folder is short enough for the tester's `TMPDIR`.** Claude's bridge sockets need a path of at most 107 bytes.
  Set `XDG_RUNTIME_DIR` to a short path, or make `/tmp` usable.
- **The runtime root lies inside your Claude home.** The tester's read deny on that home would cover its own folders.
  Move the runtime root out of it.
- **The tree cannot be tested here**: it holds a submodule, the checklist is not tracked at the head, or the sandbox
  folder already exists.

Whatever the cause, you can run the head's flows by hand and record one `functional_evidence` result per flow, or set
`functional.automated` to `false` to keep the person's stop.

## The milestone stopped at a pull request or release gate

`checks_pending`, `checks_failing`, `integration_required`, `pr_title_invalid`, `merge_pending`, `release_pending` and the others are named gates with their own fixes. See [milestone branches](guide/milestone-branches.md) and [troubleshooting](guide/troubleshooting.md#common-situations).
