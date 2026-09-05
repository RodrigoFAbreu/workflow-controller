# Workflow v2.1 — Phase 0 Audit

Audit of the real repository against `workflow_v2_1_improvement_plan.md`
(external source doc, kept at `.ai-review/source/`, gitignored — see
`WORKFLOW_V2_PLAN.md`'s Provenance section). Base commit: `162154d`
(= current `main` = current `feature/workflow-v2`, before any Workflow v2.1
change). Evaluated 2026-07-27.

**Scope note, added revision 8 (resolves `GPT-R9-012`)**: this audit's
component-inventory rows below (metrics, hooks, status-line, `.claude/agents/`,
subagent routing, OpenTelemetry) describe the **original source proposal's**
scope at the time this audit was written, and remain accurate as an audit
of *repository state* (e.g. "no hooks configured" is still true). Their
"Modify" column recommendations, however, describe work the current,
authoritative `WORKFLOW_V2_PLAN.md` has since **entirely deferred to
Workflow v2.2** (see that document's "Scope note" and "Deferred to
Workflow v2.2" section) — none of it is Workflow v2.1 "core" scope. Where
a row's recommendation conflicts with that deferral, `WORKFLOW_V2_PLAN.md`
governs.

## Round-1 plan-review corrections (2026-07-27)

Two wording corrections applied after external plan review
(`.ai-review/feedback/REVIEW_FEEDBACK.md`, PLAN-011 and PLAN-013):

- **PLAN-011.** "No existing subagent routing to attribute" (originally
  written below) was imprecise. Corrected: **no custom/project-level
  routing exists** (`.claude/agents/` is still empty), but Claude Code's
  **built-in automatic delegation** (Explore, Plan, general-purpose agents)
  already happens on every session and is the real baseline to measure —
  not "no subagents." `WORKFLOW_V2_PLAN.md`'s WF7 now compares a candidate
  custom config against this built-in baseline, not against zero.
- **PLAN-013.** Two audit claims below cited evidence outside this bundle's
  diff (a personal, user-global settings file and this conversation's own
  tool transcript). Both are reproduced verbatim here, labeled as
  **operator-observed, not repository-tracked**, so the claims are checkable
  rather than taken on faith:

  `~/.claude/settings.json` (user-global, outside this repo, read in full
  this session):
  ```json
  {
    "model": "haiku",
    "statusLine": {
      "type": "command",
      "command": "bash ~/.claude/statusline-command.sh"
    },
    "effortLevel": "high",
    "theme": "dark"
  }
  ```
  No `hooks` key present. `~/.claude/statusline-command.sh` (also outside
  this repo, read in full) renders, per refresh: model, effort, context
  used (tokens + %, color-coded), session cost, 5-hour/weekly rate-limit %,
  and reset countdown — it does not persist a running peak across refreshes
  (each refresh only sees the current snapshot).

  The worktree-deletion/`ExitWorktree`/`git worktree prune` recovery
  described below is this session's own tool-call sequence, not an
  artifact this bundle can independently reproduce — treat it as an
  operator observation corroborating the round-3 review-artifact-mismatch
  finding already documented in `docs/ACTIVE_MILESTONE.md` (which *is*
  repository-tracked, repo-relative evidence).

## Start-condition check (plan §3)

**Not satisfied at the time of writing** — Milestone 8 is still
`AWAITING_FUNCTIONAL_REVIEW` (implementation and 4 review rounds done and
`APPROVE`d; user functional-review checklist not yet run;
`/accept-milestone` not run; not archived; `docs/ROADMAP.md`/
`docs/ACTIVE_MILESTONE.md` still show it in progress). The user has
explicitly authorized starting Workflow v2.1 planning anyway, deferring
Milestone 8's functional review to after this workflow upgrade, and has set
aside (not deleted) the `milestone8-cp11-work` branch holding that
implementation. This audit and the resulting plan proceed under that explicit
override; the plan itself still must not touch product code or the Milestone
8 record.

## Component inventory

| Component | Current behavior | Keep | Modify | Remove | Evidence |
|---|---|---|---|---|---|
| `CLAUDE.md` | Concise router (~420 words): startup reading, conditional routing table, universal safety rules, git restrictions, command pointer | ✓ | minor additive only (point to new state file / new checkpoint docs if reviewer wants it discoverable) | | Read in full this session |
| `AGENTS.md` | 220-word router: always-read list, conditional-read table, workflow loop, stop conditions, commands | ✓ | no change needed for this milestone (touches product-work routing only) | | Read in full |
| `.github/copilot-instructions.md` | 119-word universal rule list, no path scoping | ✓ | | | Read in full |
| `.github/instructions/*.instructions.md` (domain/persistence/presentation/gradle) | `applyTo`-scoped conditional rules, no overlap issues | ✓ | | | Read all 4 |
| `.claude/commands/*.md` (8 files) | Each is a short (≤44 line) state-machine step: entry state, numbered actions, artifacts, exit/stop condition | ✓ structure | extend each with resumability/state-file/traceability steps per checkpoint below | | Read all 8 in full |
| `.claude/skills/` | **Does not exist as a directory.** The 8 "skills" the harness lists (`milestone-plan`, `apply-plan-review`, etc.) are the same 8 files under `.claude/commands/` — commands are dual-exposed as invocable skills by the harness, not a separate artifact | ✓ (no separate skill layer to design) | | | `ls .claude/skills/` → none; `Skill` tool's available list names match `.claude/commands/*.md` 1:1 |
| `.claude/agents/` | **Does not exist.** Zero custom subagents defined at the project level today | n/a | create bounded configs (§12 scope, reduced — see Checkpoint WF7) | | `ls .claude/agents/` → none |
| Project `.claude/settings.json` / `settings.local.json` | **Neither exists.** No project-level Claude Code settings at all | | create only if a workflow hook/permission genuinely needs it | | `ls .claude/` → only `commands/` |
| User-global `~/.claude/settings.json` | `{"model":"haiku","statusLine":{...},"effortLevel":"high","theme":"dark"}` — **no `hooks` key at all** | flag, don't silently change | | | Read in full |
| — model default | Global default model is **`haiku`**, a personal setting outside this repo | flag to user | — | | Same file. This session itself is running Sonnet 5, so something (job launcher / explicit `/model`) already overrides this default; still worth the user's explicit confirmation this is intentional, since it's exactly the kind of "global override silently flattening routing" §12.3 warns about — just at the main-session level, not subagent level |
| Status line | `~/.claude/statusline-command.sh` already renders model, effort, context used (tokens+%, color-coded), session cost, 5h/week rate-limit %, reset countdown | ✓ mostly satisfies §11.3 already | **round-3 plan targeted this file directly — rejected round 4 (OPUS-PLAN-014): it is outside the repository, unreviewable, untestable by CI, unrevertable by `git revert`.** Repo now owns `scripts/statusline.sh` (session name, **peak** context/limit tracking — script only has the latest snapshot each refresh today, no running max); the global file becomes a one-line pointer at it, a documented user action, not a Claude-made change | | Read script in full |
| Hooks | **None configured** — no `SessionStart/End`, `SubagentStart/Stop`, `PreCompact/PostCompact`, `StopFailure` | | add all 7, no-stdout, per §11.3/metrics §3.2 | | Confirmed via `hooks` key absent from settings.json |
| Env vars | No `CLAUDE_CODE_SUBAGENT_MODEL`, no `OTEL_*` set | ✓ (nothing to un-flatten) | | | `env \| grep -iE "otel\|subagent\|model"` → empty |
| Claude Code version | 2.1.220 | ✓ | | | `claude --version` |
| `docs/ai-workflow/MILESTONE_WORKFLOW.md` | 12-state machine (`PLANNING` → … → `MILESTONE_COMPLETE`), 4 hard gates already named exactly where the source plan wants them | ✓ almost entirely | add: state persistence (currently pure prose/convention, no machine-checkable record), explicit resumption/selection algorithm reference | | Read in full |
| `docs/ai-workflow/REVIEW_PROTOCOL.md` | Bundle structure (`REVIEW_REQUEST/PLAN/IMPLEMENTATION_SUMMARY/TEST_RESULTS/CHANGED_FILES/COMMITS/DIFF.patch/CONTEXT_FILES/files/`), feedback format, context-efficiency rules | ✓ core structure | add v2 artifacts (`MANIFEST.md`, `WORKFLOW_STATE.*`, `REQUIREMENTS_TRACEABILITY.md`, `FEEDBACK_RESOLUTION.md`, `SESSION_METRICS_SUMMARY.md` — last one only when relevant) | | Read in full |
| `scripts/prepare-ai-review.sh` | Deterministic regeneration from live git state; untracked-file intent-to-add + self-reverting cleanup trap; stub-only-if-missing for author files; copies changed+context files into `files/` | ✓ all of this | fix confirmed bug (below); add schema version/ID, revision binding, stage-completeness validation, context-path safety, archive check | | Read in full, 148 lines |
| — confirmed bug | Invalid base-SHA path reports `$2` (the **stage** argument) instead of the actual supplied base SHA in the error message (line 37) | | fix | | `scripts/prepare-ai-review.sh:36-39` |
| — ".ai-review self-inclusion" risk named in source plan | **Already structurally impossible today**: the script only diffs tracked files (`git diff`) plus untracked-but-not-gitignored files (`git ls-files --others --exclude-standard`); `.ai-review/` is gitignored, so it can never appear in its own bundle | ✓ no fix needed | add one defensive comment/assertion, not new logic | | `.gitignore:1` (`​.ai-review/`); script logic lines 52, 121 |
| `.gitignore` | Ignores `.ai-review/`, standard Android/Gradle/IDE noise | ✓ | add entries for new raw-metrics storage path once chosen | | Read in full |
| `.ai-review/` current tracking | Directory does not currently exist (clean between milestones); **never once committed** in this repo's history | ✓ discipline holding | | | `ls .ai-review` → none; `git log --all --oneline -- .ai-review` → empty |
| Checkpoint format (`docs/ACTIVE_MILESTONE.md`) | Simple markdown checklist (`P0`, `CP0`–`CP16`), each item a **prose paragraph** (some 400–600+ words) with commit hashes inlined in text, not a structured field | | needs structured registry (ID, deps, status, complexity, start/completion commit) alongside, not replacing, the narrative | | Read Milestone 8's full checklist (17 items) |
| — organic journal drift | `REVIEW_PROTOCOL.md` itself says keep `ACTIVE_MILESTONE.md` "factual and concise... not a chronological journal" — but Milestone 8's checkpoint entries have already grown into exactly that (very long retrospective prose per item) | flag as real, already-occurring problem | Session-handoff mechanism (§10) plus tighter per-checkpoint entry-size guidance addresses this going forward | | Same read; e.g. CP11/CP13/CP15 entries each ~400-600 words |
| Completed milestone handoffs | `docs/milestones/completed/milestone-{1..7}-{execution,reference}.md` — these ARE the milestone plan artifacts; **no separate "handoff" file has ever existed** | ✓ nothing to preserve/break | Session handoff (§10) is a genuinely new artifact type | | `ls docs/milestones/completed/` |
| Historical session summaries | None exist as dedicated files. The closest analog is `ACTIVE_MILESTONE.md`'s per-checkpoint prose (informal, accumulating, not session-scoped) | | Stage 0 metrics baseline will be sparse/weak — mark explicitly, per metrics spec's own fallback instruction | | Same |
| `docs/agent-context/` (`CONTEXT_INVENTORY.md`, `CONTEXT_EVALUATION.md`, `CONTEXT_CHANGELOG.md`, `BENCHMARK_SCENARIOS.md`) | A **prior, one-off, word-count-based** context-routing audit from 2026-07-26 on a now-superseded branch (`improving-docs-and-prompts`, commit `0cecefa`, Milestone 1 active). Single commit (`236b66a`), never revisited. Explicitly self-described as "diagnostic/archival... read it only when auditing or modifying the context system" — i.e. exactly this task | ✓ keep as archival evidence | do not expand or treat as live instrumentation; Workflow v2.1's metrics system is a distinct, automatic, ongoing replacement, not a continuation of this manual exercise | | Read all 4 files in full; `git log --oneline -- docs/agent-context` → 1 commit |
| — known-stale content within it | `CONTEXT_INVENTORY.md` references `docs/milestones/active/milestone-1-execution.md`, which no longer exists (per user memory `repo_stale_docs`, confirmed: `docs/milestones/active/` currently holds only Milestone 8 docs) | flag, don't fix (out of scope, per existing memory's own rationale) | | | Cross-checked against `ls docs/milestones/active/` |
| OpenTelemetry | Not configured anywhere (no env vars, no settings) | | do not enable initially — `PostToolUse` on foreground `Agent` calls plus `SubagentStart`/`SubagentStop` already answer *most* of the model/agent/token questions the metrics spec needs (see `WORKFLOW_V2_PLAN.md` revision 3's D4, corrected twice — verified directly against Claude Code's hook docs, not assumed); the real gap is background/async-launched subagents, where final token totals are unavailable at hook time and stay explicitly `null`. Revisit OTEL only if that specific gap needs closing | | `env` grep; settings.json read; direct fetch of `code.claude.com/docs/en/hooks.md` + targeted web search, round 3 |
| Review-bundle incident (first-party evidence) | Milestone 8's implementation-review round 3 was `BLOCK — REVIEW ARTIFACT MISMATCH`: work happened in worktree `milestone8-cp11-work`, the main checkout's own untracked `.ai-review/current` still showed a stale round-2 snapshot, because bundle regeneration only updated the worktree's copy | | this is exactly the base/head/revision-binding problem §14 and the bundle-manifest work (§16) are meant to prevent — concrete evidence, not hypothetical | | `docs/ACTIVE_MILESTONE.md`, "External implementation review round 3" entry |
| Worktree/branch practice (first-party evidence, this session) | This very session's own worktree (`.claude/worktrees/milestone8-cp11`) was deleted externally mid-session while the harness still believed it was active, requiring `ExitWorktree`/`git worktree prune`/re-`EnterWorktree` to recover | | reinforces: workflow state must be derivable from repo/branch state, never from a session's live belief about its own directory | | This conversation's own tool transcript |
| `docs/TECHNICAL_DECISIONS.md` "Open decisions" | 10 rows, all product/toolchain decisions (Gradle module split, Room schema, progression formulas, navigation structure, backup location, notification-permission behavior, cross-day workouts, substitutions, manual corrections, UI design system) | ✓ none touched by this plan | | | Read in full; confirmed none of these 10 rows are implicated by any Workflow v2.1 checkpoint below |
| CI (`.github/workflows/ci.yml`) | `spotlessCheck → detekt → lintDebug → testDebugUnitTest → assembleDebug → assembleDebugAndroidTest`, no device | ✓ | **correction (GPT-R4-PLAN-012): this row previously said "none" — wrong, contradicted the plan's own WF8a scope, which has always assigned CI wiring for the new conformance/fixture suite.** **Done, prototype rounds (`OPUS-R8-011`, WF8a-i's scope after the revision-7 split — see `WORKFLOW_V2_PLAN.md`'s checkpoint registry):** one narrow CI step ("Workflow fingerprint conformance suite") runs **only** the hermetic `workflow_fingerprint_test.py` — deliberately **not** `workflow_fingerprint_demo_test.py`, which computes classification from a fixed historical base commit and would fail on any later unrelated product PR (`GPT-R9-002`); no changes to the existing Android build/lint/test jobs | | Read in full; verified against `.github/workflows/ci.yml`'s current content |

## Determinations that change the source plan's scope

1. **No custom/project-level subagent routing exists, but built-in
   automatic delegation does and is the real baseline.** *(Corrected per
   PLAN-011 — see "Round-1 plan-review corrections" above.)* `.claude/agents/`
   is empty and no hooks have ever logged a subagent event, so there is
   nothing custom to attribute yet. But Claude Code's built-in Explore/Plan/
   general-purpose delegation already runs automatically on every session
   (confirmed: these are listed as available agent types independent of any
   project config) — that automatic behavior, not "no subagents," is the
   baseline WF7 must measure against before adopting any custom routing.
2. **No baseline data exists or can exist yet for a real adopt/reject
   verdict on cheap-agent routing within this same milestone.** The metrics
   spec's own thresholds (≥5 examples per task category, ≥3 matched
   checkpoints, ≥2 product milestones for a *confirmed* improvement) cannot
   be met by Workflow v2.1's own checkpoints alone, and Milestone 8 (the
   only candidate prior product milestone) predates all instrumentation.
   The refined plan (below) scopes Checkpoint 8 down to defining eligible
   categories/configs/escalation and wiring logging, explicitly deferring
   any adopt/reject decision to real subsequent product-milestone usage.
3. **Source Checkpoints 9, 11, and 12 are not additional implementation
   units — they are the workflow's own existing gates, generically
   applicable to any milestone.** "Command integration and documentation"
   (source CP9) is better distributed into each checkpoint that actually
   touches a given command, so each commit stays coherent and reviewable.
   "External implementation review" (source CP11) and "user functional
   workflow review" (source CP12) are just this milestone's own
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` and `AWAITING_FUNCTIONAL_REVIEW`
   gates — already defined generically by `MILESTONE_WORKFLOW.md`, requiring
   no new design. Folding these in shrinks the registry from 12 to 8 real
   implementation checkpoints (see refined plan).
4. **The review-bundle self-inclusion risk the source plan names is already
   structurally prevented** by the existing gitignore + script design.
   Downgraded from "fix" to "add a defensive comment," avoiding solving an
   already-solved problem.
5. **A concrete, first-party incident already validates** the base/head
   revision-binding design (§14) and bundle hardening (§16) — this is not
   speculative hardening, Milestone 8 round 3 hit exactly this failure mode.

See `WORKFLOW_V2_PLAN.md` for the refined checkpoint registry, requirements
traceability, and the specific design decisions flagged for reviewer/user
sign-off rather than silently finalized.
