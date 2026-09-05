#!/usr/bin/env bash
# state_writer: false
# This script reads docs/ai-workflow/WORKFLOW_STATE.json (to cross-check
# that record_bundle_generation already advanced reviewed_implementation_head
# before regenerating a bundle) but never writes it -- the declaration
# above is about publication, not about reference.
# Build a review bundle under .ai-review/<work-item-id>/current/ (or the
# flat .ai-review/current/ compatibility path for a non-plan stage with no
# work-item-id given) and archive it. See docs/ai-workflow/REVIEW_PROTOCOL.md.
#
# Usage: scripts/prepare-ai-review.sh <base-sha> <stage> [work-item-id]
#   stage: plan | implementation | post-fix | functional-review
#   work-item-id: REQUIRED for stage "plan" (D-Fingerprint-Generalization,
#     OPUS-R27-003) -- never resolved from the live active_work_item_id for
#     this stage, since MANIFEST.md's identity binding depends on it.
#     Optional for every other stage: omitted -> the flat .ai-review/current/
#     compatibility layout; given -> .ai-review/<work-item-id>/current/
#     (WF5's relayout, D-Bundle-Manifest).
set -euo pipefail

usage() {
  echo "Usage: $0 <base-sha> <stage> [work-item-id]" >&2
  echo "  stage: plan | implementation | post-fix | functional-review" >&2
  echo "  work-item-id: required for stage 'plan'; optional otherwise" >&2
  exit 1
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
fi

REQUESTED_BASE_SHA=$1
BASE_SHA=$1
STAGE=$2
WORK_ITEM_ID=${3:-}

case "$STAGE" in
  plan | implementation | post-fix | functional-review) ;;
  *)
    echo "error: unknown stage '$STAGE'" >&2
    usage
    ;;
esac

if [[ "$STAGE" == "plan" && -z "$WORK_ITEM_ID" ]]; then
  echo "error: work-item-id is required for stage 'plan' (D-Fingerprint-Generalization) -- never resolved from the live active_work_item_id" >&2
  usage
fi

if [[ -n "$WORK_ITEM_ID" ]] && ! [[ "$WORK_ITEM_ID" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]]; then
  echo "error: work-item-id '$WORK_ITEM_ID' does not match ^[a-z0-9][a-z0-9_-]{0,63}\$" >&2
  exit 1
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "error: not inside a git repository" >&2
  exit 1
}
cd "$REPO_ROOT"

if ! BASE_SHA=$(git rev-parse --verify "${BASE_SHA}^{commit}" 2>/dev/null); then
  echo "error: base-sha '$REQUESTED_BASE_SHA' does not resolve to a commit" >&2
  exit 1
fi

# --- plan-stage-only: cross-check the resolved work item's own declared
# base_commit against the resolved BASE_SHA, and its WORKFLOW_STATE.json
# plan_revision mirror against its own registry's plan_revision, before
# generating any bundle content (fail-closed matrix condition 13,
# OPUS-R27-003/OPUS-R28-006; the plan-revision mirror check is
# D-Plan-Revision-Publication/WFR-65's detection half). Routed through
# fingerprint.resolve_plan_stage_metadata -- the same resolver every other
# plan-stage read in this design uses -- never a second, ad hoc metadata
# reader.
if [[ "$STAGE" == "plan" ]]; then
  PLAN_STAGE_BASE_CHECK=$(
    PYTHONPATH="$REPO_ROOT/scripts:${PYTHONPATH:-}" python3 - "$WORK_ITEM_ID" "$BASE_SHA" "$REPO_ROOT" <<'PYEOF'
import json
import sys
from pathlib import Path
import workflow_fingerprint as fingerprint

work_item_id, base_sha, repo_root = sys.argv[1], sys.argv[2], Path(sys.argv[3])
try:
    metadata = fingerprint.resolve_plan_stage_metadata(repo_root, work_item_id)
except Exception as exc:  # noqa: BLE001 -- surfaced verbatim to the operator below
    print(f"error::{type(exc).__name__}: {exc}")
    sys.exit(0)
if metadata.base_commit != base_sha:
    print(f"mismatch_base::{metadata.base_commit}")
    sys.exit(0)
state_path = repo_root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
state = json.loads(state_path.read_text())
mirror_plan_revision = state.get("work_items", {}).get(work_item_id, {}).get("plan_revision")
if mirror_plan_revision != metadata.plan_revision:
    print(f"mismatch_revision::{mirror_plan_revision}::{metadata.plan_revision}")
    sys.exit(0)
print("ok")
PYEOF
  )
  case "$PLAN_STAGE_BASE_CHECK" in
    ok) ;;
    mismatch_base::*)
      DECLARED_BASE_COMMIT=${PLAN_STAGE_BASE_CHECK#mismatch_base::}
      echo "error: work item '$WORK_ITEM_ID' declares base_commit '$DECLARED_BASE_COMMIT', but the requested base '$REQUESTED_BASE_SHA' resolves to '$BASE_SHA' -- refusing to generate bundle content for a disagreeing base (D-Fingerprint-Generalization, fail-closed matrix condition 13)" >&2
      exit 1
      ;;
    mismatch_revision::*)
      REVISION_DETAIL=${PLAN_STAGE_BASE_CHECK#mismatch_revision::}
      MIRROR_PLAN_REVISION=${REVISION_DETAIL%%::*}
      REGISTRY_PLAN_REVISION=${REVISION_DETAIL#*::}
      echo "error: work item '$WORK_ITEM_ID' WORKFLOW_STATE.json plan_revision mirror is '$MIRROR_PLAN_REVISION', but its registry declares plan_revision '$REGISTRY_PLAN_REVISION' -- refusing to generate bundle content for a disagreeing plan-revision mirror (D-Plan-Revision-Publication, WFR-65)" >&2
      exit 1
      ;;
    error::*)
      echo "error: could not resolve work item '$WORK_ITEM_ID's plan-stage metadata: ${PLAN_STAGE_BASE_CHECK#error::}" >&2
      exit 1
      ;;
    *)
      echo "error: unexpected plan-stage base-commit/plan-revision check output: $PLAN_STAGE_BASE_CHECK" >&2
      exit 1
      ;;
  esac
fi

HEAD_SHA=$(git rev-parse HEAD)
BRANCH=$(git branch --show-current)
BRANCH=${BRANCH:-"(detached)"}

# Capture real working-tree status before any intent-to-add staging below,
# so the bundle reflects what the user's git state actually looks like.
WORKTREE_STATUS=$(git status --short)

# `git diff <base>` only reports tracked content. Temporarily mark new,
# untracked (but not gitignored) files as intent-to-add so they show up as
# additions in the diff/patch/name-status output below, then restore their
# untracked status on exit so this script has no lasting effect on git state.
mapfile -d '' -t UNTRACKED_FILES < <(git ls-files --others --exclude-standard -z -- .)
if ((${#UNTRACKED_FILES[@]} > 0)); then
  git add -N -- "${UNTRACKED_FILES[@]}"
fi
cleanup() {
  if ((${#UNTRACKED_FILES[@]} > 0)); then
    git reset -- "${UNTRACKED_FILES[@]}" > /dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# --- .ai-review/<work_item_id>/ relayout, with a stated compatibility
# fallback (D-Bundle-Manifest, resolves OPUS-R6-021): a work-item-id
# argument opts into the new per-work-item layout; omitting it keeps
# writing the flat legacy layout, for any caller not yet passing one.
# For stage "plan", WORK_ITEM_ID is always non-empty here (enforced above),
# so ROOT_DIR always resolves scoped -- there is no "else" left to disagree
# with --write-manifest's own resolution below (OPUS-R26-002/OPUS-R27-003).
if [[ -n "$WORK_ITEM_ID" ]]; then
  ROOT_DIR=".ai-review/$WORK_ITEM_ID"
else
  ROOT_DIR=".ai-review"
fi
BUNDLE_DIR="$ROOT_DIR/current"
FILES_DIR="$BUNDLE_DIR/files"
mkdir -p "$FILES_DIR"

# --- generator-side stage-document binding (WFR-67, WF8c item (h) part 2):
# plan-stage-only, and must run before any other file under $BUNDLE_DIR is
# written (the `mkdir -p` above is the sole, harmless exception) --
# captures a private pinned snapshot of every plan-stage protected path and
# derives $BUNDLE_DIR/PLAN.md unconditionally from it, so PLAN.md, its
# files/ copy below, and review_content_id (computed from the same pin at
# --write-manifest time) can never independently disagree about which
# bytes they describe (OPUS-R89-001 through OPUS-R92-003).
if [[ "$STAGE" == "plan" ]]; then
  python3 "$REPO_ROOT/scripts/workflow_fingerprint.py" "$BASE_SHA" \
    --work-item-id "$WORK_ITEM_ID" --derive-plan-stage-document
fi

# --- round-identity preflight (GPT-R42-001, GPT-R43-001, GPT-R43-003): run
# before any other write below, never after (GPT-R43-003 -- `current/` is
# "the bundle currently under review" per REVIEW_PROTOCOL.md, not a
# scratch directory a rejected generation may leave mutated; `mkdir -p`
# above is the sole exception, a harmless no-op when the directory already
# exists). Proves two things about WORKFLOW_STATE.json's own
# work_items[work_item_id], both required by record_bundle_generation's
# contract (workflow_state.py, D-Approval-Commits' sole writer of both
# fields) before a bundle may be finalized for this head:
#
# 1. reviewed_implementation_head equals $HEAD_SHA, the exact commit this
#    generation run is for (GPT-R42-001) -- record_bundle_generation must
#    already have been called and persisted to the working tree
#    (committed or not) BEFORE this script runs, never after;
# 2. implementation_revision moved monotonically relative to the
#    PREVIOUS bundle already at $BUNDLE_DIR/MANIFEST.md, read here
#    before anything below can overwrite it (GPT-R43-001, corrected by
#    the salvage audit's ledger rows B1/B2/B3): regenerating for the
#    *same* generation head the previous manifest already named must
#    leave the revision unchanged (idempotent re-generation); at a *new*
#    generation head the revision must be either unchanged or exactly
#    one greater.
#
#    The original rule demanded "exactly one greater" at every new head.
#    That inference -- "a new generation head means a new reviewed
#    round" -- is false for both of this design's non-ordinary
#    generation modes, which exist precisely to move the generation head
#    while pinning the round: record_bundle_generation(...,
#    outcome="same_content") (WF8c (c)) and
#    /recover-implementation-provenance's own S2 commit (WF8c (b)) both
#    leave reviewed_implementation_head/implementation_revision
#    byte-identical by contract. Under the original rule neither could
#    ever publish its bundle, so both commands were unreachable end to
#    end.
#
#    The unchanged-revision case at a new head is admitted *only* when
#    check 1 above actually verified the provenance interval -- a
#    strictly stronger statement of round identity than any revision
#    delta: it proves live HEAD is exactly the current
#    Workflow-Bundle-Generation-Record commit T for the live
#    implementation_revision, that reviewed_implementation_head is
#    first-parent-reachable from T, that every non-record commit between
#    them classifies implementation-stage `excluded`, and that the
#    supersession chain is continuous. A round whose protected content
#    genuinely changed can never satisfy it
#    (ProtectedPathInProvenanceIntervalError), so nothing that the
#    original rule refused becomes reachable here.
#
#    `prev_head` is read from the previous manifest's `generation_head:`
#    line, not its `reviewed_implementation_head:` line: since the
#    salvage audit's ledger row I1 those are different values, and only
#    `generation_head` means "the commit that generation ran at" under
#    both the old and the new manifest semantics. The
#    `reviewed_implementation_head:` fallback below covers a manifest
#    predating `generation_head:` entirely, where the two were the same
#    value anyway.
#
#    A first-ever generation (no prior implementation-stage manifest to
#    compare against) skips this half, same skip condition as below.
#
# Skipped entirely for a work item with no WORKFLOW_STATE.json entry, same
# skip condition as the "record_bundle_generation" step this bundle
# generation precedes (docs/ai-workflow/REVIEW_PROTOCOL.md), and for the
# plan stage / a flat-compatibility (no work-item-id) invocation, which
# this guard does not govern.
if [[ ( "$STAGE" == "implementation" || "$STAGE" == "post-fix" ) && -n "$WORK_ITEM_ID" ]]; then
  PREV_MANIFEST="$BUNDLE_DIR/MANIFEST.md"
  PREV_HEAD=""
  PREV_REVISION=""
  if [[ -f "$PREV_MANIFEST" ]]; then
    PREV_HEAD=$(sed -n 's/^generation_head: //p' "$PREV_MANIFEST")
    if [[ -z "$PREV_HEAD" ]]; then
      # A manifest predating the generation_head: line records the same
      # value under reviewed_implementation_head: -- see the note above.
      PREV_HEAD=$(sed -n 's/^reviewed_implementation_head: //p' "$PREV_MANIFEST")
    fi
    PREV_REVISION=$(sed -n 's/^implementation_revision: //p' "$PREV_MANIFEST")
  fi

  GUARD_CHECK=$(
    python3 - "$REPO_ROOT" "$WORK_ITEM_ID" "$HEAD_SHA" "$PREV_HEAD" "$PREV_REVISION" "$BASE_SHA" <<'PYEOF'
import json
import sys
from pathlib import Path

repo_root, work_item_id, head_sha, prev_head, prev_revision, base_sha = sys.argv[1:7]
state_path = Path(repo_root) / "docs/ai-workflow/WORKFLOW_STATE.json"
state = json.loads(state_path.read_text())
work_item = state.get("work_items", {}).get(work_item_id)
if work_item is None:
    print("status: skip")
    sys.exit(0)

interval_verified = False
recorded_head = work_item.get("reviewed_implementation_head")
if recorded_head != head_sha:
    # Not bare equality -- WF8B-003's remediation (D-Commit-Provenance)
    # allows reviewed_implementation_head to lag head_sha by a bounded,
    # validated provenance interval (the dedicated Workflow-Bundle-
    # Generation-Record commit this generation's own durability write
    # creates, plus any excluded-only commits before it). Reuse the same
    # check /approve-review implementation's own gate uses, never a
    # second, independent notion of "close enough".
    sys.path.insert(0, str(Path(repo_root) / "scripts"))
    import workflow_state as ws
    interval_ok = False
    interval_reason = None
    if recorded_head:
        try:
            interval_ok = ws.implementation_provenance_interval_reachable(
                Path(repo_root), work_item, base_sha, head_sha,
            )
        except Exception:
            interval_ok = False
        if not interval_ok:
            try:
                ws.verify_implementation_provenance_interval(
                    Path(repo_root), work_item, base_sha, head_sha,
                )
            except Exception as exc:
                interval_reason = str(exc)
    if not interval_ok:
        print("status: mismatch")
        print(
            f"reason: reviewed_implementation_head {recorded_head!r} does not "
            f"match this generation's head {head_sha!r} (GPT-R42-001), and no "
            f"valid provenance interval reaches it either"
            + (f": {interval_reason}" if interval_reason else "")
        )
        sys.exit(0)
    interval_verified = True

live_revision = work_item.get("implementation_revision")
if prev_head and prev_revision:
    prev_revision_int = int(prev_revision)
    if prev_head == head_sha:
        if live_revision != prev_revision_int:
            print("status: mismatch")
            print(
                f"reason: regenerating for the same head {head_sha!r} the "
                f"previous bundle already named must leave "
                f"implementation_revision unchanged ({prev_revision_int}), "
                f"got {live_revision} (GPT-R43-001)"
            )
            sys.exit(0)
    elif live_revision == prev_revision_int:
        # A new generation head at an unchanged revision is exactly the
        # same-content republication / provenance-recovery shape (WF8c
        # (c)/(b)) -- legitimate only when check 1 above actually proved
        # the provenance interval for this head. Without that proof this
        # is an unrecorded regeneration and must still be refused.
        if not interval_verified:
            print("status: mismatch")
            print(
                f"reason: new generation head {head_sha!r} (previous bundle was "
                f"{prev_head!r}) left implementation_revision at "
                f"{prev_revision_int}, but no provenance interval was verified "
                f"for this head -- a same-content republication or provenance "
                f"recovery must first record its own "
                f"Workflow-Bundle-Generation-Record commit (GPT-R43-001)"
            )
            sys.exit(0)
    elif live_revision != prev_revision_int + 1:
        print("status: mismatch")
        print(
            f"reason: new generation head {head_sha!r} (previous bundle was "
            f"{prev_head!r}) requires implementation_revision to stay at "
            f"{prev_revision_int} (a same-content republication or provenance "
            f"recovery) or advance by exactly one to {prev_revision_int + 1} "
            f"(a fresh round), got {live_revision} (GPT-R43-001)"
        )
        sys.exit(0)
print("status: ok")
PYEOF
  )
  GUARD_STATUS=$(printf '%s\n' "$GUARD_CHECK" | sed -n 's/^status: //p')
  if [[ "$GUARD_STATUS" != "ok" && "$GUARD_STATUS" != "skip" ]]; then
    echo "error: WORKFLOW_STATE.json work_items['$WORK_ITEM_ID'] round identity does not agree with this generation (GPT-R42-001/GPT-R43-001) -- call workflow_state.record_bundle_generation(..., stage=..., head='$HEAD_SHA') and persist the result to the working tree BEFORE regenerating this bundle. No file under $BUNDLE_DIR was written by this run:" >&2
    printf '%s\n' "$GUARD_CHECK" >&2
    exit 1
  fi
fi

# --- author-written files: create empty stubs only if missing, never
# overwrite -- PLAN.md leaves this list for the plan stage (WFR-67): it was
# already derived, unconditionally, above.
STUB_FILES=(REVIEW_REQUEST.md IMPLEMENTATION_SUMMARY.md TEST_RESULTS.md CONTEXT_FILES.txt)
if [[ "$STAGE" != "plan" ]]; then
  STUB_FILES+=(PLAN.md)
fi
for f in "${STUB_FILES[@]}"; do
  path="$BUNDLE_DIR/$f"
  if [[ ! -f "$path" ]]; then
    : > "$path"
  fi
done

# --- generated: CHANGED_FILES.txt (metadata + stat + name-status + worktree status) ---
CHANGED_FILES="$BUNDLE_DIR/CHANGED_FILES.txt"
{
  echo "branch: $BRANCH"
  echo "base: $BASE_SHA"
  echo "head: $HEAD_SHA"
  echo "stage: $STAGE"
  echo "work_item_id: ${WORK_ITEM_ID:-(none -- flat compatibility layout)}"
  echo "generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "## Diff stat (base -> working tree)"
  git diff --stat "$BASE_SHA" -- .
  echo
  echo "## Changed files (name-status, base -> working tree)"
  git diff --name-status "$BASE_SHA" -- .
  echo
  echo "## Working tree status (git status --short)"
  printf '%s\n' "$WORKTREE_STATUS"
} > "$CHANGED_FILES"

# --- generated: COMMITS.txt ---
COMMITS_FILE="$BUNDLE_DIR/COMMITS.txt"
if [[ "$BASE_SHA" == "$HEAD_SHA" ]]; then
  echo "(no commits yet since base $BASE_SHA)" > "$COMMITS_FILE"
else
  git log --oneline --decorate "${BASE_SHA}..HEAD" > "$COMMITS_FILE"
fi

# --- generated: DIFF.patch ---
DIFF_FILE="$BUNDLE_DIR/DIFF.patch"
git diff "$BASE_SHA" -- . > "$DIFF_FILE"

# --- generated: files/ (final copies of changed files, excluding deletions) ---
rm -rf "$FILES_DIR"
mkdir -p "$FILES_DIR"

# NUL-delimited parsing throughout (resolves OPUS-R6-019): the previous
# `IFS=$'\t' read` loop over `git diff --name-status` (no `-z`) broke on a
# path containing a newline, and mis-split under `core.quotePath` on a
# non-ASCII path. `-z` terminates every record (and, for renames, every
# field within a record) with NUL instead, which is unambiguous regardless
# of what bytes the path itself contains.
while IFS= read -r -d '' status && IFS= read -r -d '' path; do
  [[ -z "$path" ]] && continue
  if [[ "$status" == R* ]]; then
    # Renames report as "R100\0old\0new\0" under -z: consume the second
    # (new) path as an extra NUL-delimited field.
    IFS= read -r -d '' new_path
    path=$new_path
  fi
  [[ "$status" == D* ]] && continue
  # Source-proposal exclusion (WFR-16): .ai-review/source/* is never part
  # of a normal review bundle, even if a diff somehow reported one -- it
  # is gitignored and this branch is defense in depth, not the primary
  # enforcement (CONTEXT_FILES.txt below is the one an author actually
  # populates).
  case "$path" in
    .ai-review/source/*) continue ;;
  esac
  if [[ -f "$path" ]]; then
    mkdir -p "$FILES_DIR/$(dirname -- "$path")"
    cp -- "$path" "$FILES_DIR/$path"
  fi
done < <(git diff --name-status -z "$BASE_SHA" -- .)

# --- generator-side stage-document binding, continued (WFR-67): overwrite
# any protected path's files/ copy just populated above with the pinned
# bytes captured before this run touched anything -- closing the "four of
# the five [protected] inputs stayed in [an independently-timed] window"
# gap (OPUS-R91-001) for the four protected paths PLAN.md's own derivation
# does not otherwise touch. A protected path absent from files/ (not part
# of this round's diff) is left absent.
if [[ "$STAGE" == "plan" ]]; then
  PYTHONPATH="$REPO_ROOT/scripts:${PYTHONPATH:-}" python3 - "$REPO_ROOT" "$WORK_ITEM_ID" "$BUNDLE_DIR" <<'PYEOF'
import sys
from pathlib import Path
import workflow_fingerprint as fingerprint

repo_root, work_item_id, bundle_dir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
metadata = fingerprint.resolve_plan_stage_metadata(repo_root, work_item_id)
pin_dir = repo_root / ".ai-review" / work_item_id / ".pin"
fingerprint.refresh_files_copy_from_pin(pin_dir, bundle_dir, metadata)
PYEOF
fi

# --- copy explicitly listed context files, if any ---
CONTEXT_FILES_LIST="$BUNDLE_DIR/CONTEXT_FILES.txt"
if [[ -s "$CONTEXT_FILES_LIST" ]]; then
  while IFS= read -r ctx_path; do
    [[ -z "$ctx_path" ]] && continue
    case "$ctx_path" in
      .ai-review/source/*)
        echo "warning: skipping source-proposal path in CONTEXT_FILES.txt (WFR-16): $ctx_path" >&2
        continue
        ;;
    esac
    if [[ -f "$ctx_path" ]]; then
      mkdir -p "$FILES_DIR/$(dirname -- "$ctx_path")"
      cp -- "$ctx_path" "$FILES_DIR/$ctx_path"
    else
      echo "warning: context file listed but not found: $ctx_path" >&2
    fi
  done < "$CONTEXT_FILES_LIST"
fi

# --- write MANIFEST.md as this script's own final content step, before
# archiving, so the archived bundle actually contains it
# (D-Fingerprint-Generalization, OPUS-R25-012, resolves the affected-
# commands audit gap: MANIFEST.md was previously only ever produced by a
# separate, manual CLI invocation nothing forced to carry a matching
# --work-item-id). Same CLI entry point as every other invocation, not a
# reimplementation; REVIEW_REQUEST.md must already state the same
# review_content_id (OPUS-R18-005, unchanged precondition) -- this call
# does not relax it.
#
# Implementation/post-fix stages get their own implementation-stage
# manifest here too (GPT-R30-001/002): a scoped bundle (WORK_ITEM_ID
# given) previously carried no manifest of its own at these stages,
# silently reusing whatever MANIFEST.md the plan stage had already
# written to the same directory -- a stale plan-stage identity with a
# pre-implementation generation_head, bound to the plan-stage
# review_content_id rather than a digest over the reviewed implementation
# diff. --stage implementation anchors review_content_id at $HEAD_SHA
# (the final reviewed implementation HEAD, recorded as
# reviewed_implementation_head), computed fresh every run, exactly like
# the plan stage's own manifest.
#
# `MANIFEST_WRITTEN` is this run's own producer state, set only on the far
# side of a `--write-manifest` call that actually returned 0 (under
# `set -e` a failed call never reaches its assignment). The closing check
# below is gated on it, never on whether a `MANIFEST.md` file happens to
# exist under $BUNDLE_DIR -- see that block's own note for why.
MANIFEST_WRITTEN=0
if [[ "$STAGE" == "plan" ]]; then
  python3 "$REPO_ROOT/scripts/workflow_fingerprint.py" "$BASE_SHA" \
    --work-item-id "$WORK_ITEM_ID" --write-manifest
  MANIFEST_WRITTEN=1
elif [[ ( "$STAGE" == "implementation" || "$STAGE" == "post-fix" ) && -n "$WORK_ITEM_ID" ]]; then
  python3 "$REPO_ROOT/scripts/workflow_fingerprint.py" "$BASE_SHA" \
    --work-item-id "$WORK_ITEM_ID" --stage implementation --write-manifest
  MANIFEST_WRITTEN=1
fi

# --- archive ---
# Item 318 (WF8c scope clause (j), OPUS-R102-011): write to a temp file
# first, then rename onto the real archive path -- an interrupted or
# failed tar invocation must never leave the previously valid archive
# corrupted or truncated in place.
ARCHIVE="$ROOT_DIR/review-bundle.tar.gz"
ARCHIVE_TMP="$ARCHIVE.tmp"
tar -czf "$ARCHIVE_TMP" -C "$ROOT_DIR" current
mv -f "$ARCHIVE_TMP" "$ARCHIVE"

# --- closing check (D-Fingerprint-Generalization, GPT-R30-001/003; WFR-67
# generator-side stage-document binding, parts 3/3b): when a manifest was
# written above, require bundle_id equality across three independent
# computations -- the value MANIFEST.md itself declares, a fresh
# recomputation directly over $BUNDLE_DIR, and a fresh recomputation over
# the archive's own extracted content -- plus, for the plan stage, the
# fourth byte-identity assertion (PLAN.md/files/archive vs. the pinned
# plan_path snapshot) -- so a stale or non-reproducible artifact fails
# closed here instead of being discovered only by an external reviewer's
# own independent recomputation. On failure, for a scoped work item, this
# withdraws the bundle (REJECTED marker, ordered removal, quarantine)
# rather than leaving a stale-but-self-verifying current/ and archive in
# place; on success, it clears any pre-existing REJECTED marker.
#
# Gated on $MANIFEST_WRITTEN -- this invocation's own producer state --
# never on `-f "$BUNDLE_DIR/MANIFEST.md"` (convergence pass 12, ledger
# I20). Two of this script's four stages write no manifest at all
# (`functional-review` at either layout, and `implementation`/`post-fix`
# with the work-item-id omitted), and $BUNDLE_DIR is a directory reused
# across stages by design: a `functional-review` generation over a work
# item whose plan- or implementation-stage round already published there
# found that round's MANIFEST.md still sitting in the directory, ran the
# three-way check against it, and could only ever fail -- CHANGED_FILES.txt
# alone carries a `stage:` line this run has just rewritten, so the
# recomputed bundle_id disagrees with the recorded one by construction.
# The failure path is `withdraw_bundle`, so a *valid, published, already-
# approved* bundle was quarantined and its archive deleted by a generation
# that never claimed the bundle's identity in the first place -- with
# WORKFLOW_STATE.json left untouched (this script is not a state writer),
# so `technical_approval` stayed CURRENT over a bundle that no longer
# existed, and the completed withdrawal removed its own REJECTED marker on
# the way out, leaving `assert_bundle_not_rejected` with nothing to see.
#
# The invariant this restores: finalization is a *producer's* obligation.
# A run that generated an identity must prove that identity reproduces; a
# run that generated none has nothing to prove, and no standing to
# withdraw another round's artifact. Deleting the stale manifest to
# suppress the check would be the same filesystem-state reasoning
# inverted, and would additionally destroy the published round's own
# identity record; the flag is the producer state itself.
if (( MANIFEST_WRITTEN )); then
  FINALIZE_ARGS=("$BASE_SHA" --finalize-bundle "$BUNDLE_DIR" "$ARCHIVE" --generation-stage "$STAGE")
  if [[ -n "$WORK_ITEM_ID" ]]; then
    FINALIZE_ARGS+=(--work-item-id "$WORK_ITEM_ID")
  fi
  set +e
  FINALIZE_CHECK=$(python3 "$REPO_ROOT/scripts/workflow_fingerprint.py" "${FINALIZE_ARGS[@]}")
  FINALIZE_EXIT=$?
  set -e
  FINALIZE_STATUS=$(printf '%s\n' "$FINALIZE_CHECK" | sed -n 's/^status: //p')
  if [[ "$FINALIZE_EXIT" -ne 0 || "$FINALIZE_STATUS" != "ok" ]]; then
    echo "error: bundle generation did not finalize cleanly:" >&2
    printf '%s\n' "$FINALIZE_CHECK" >&2
    exit 1
  fi
fi

# A stage that wrote no manifest of its own does not silently inherit the
# previous round's (convergence pass 12, ledger I20/O30). The file stays --
# it is the published round's identity record, and this run has no standing
# to delete it -- but its bundle_id no longer describes what is now in
# $BUNDLE_DIR or in the archive just written, so say so rather than leaving
# an operator to discover it when a reviewer's own recomputation disagrees.
# Every live consumer recomputes bundle_id fresh (/approve-review step 2,
# /review-plan step 5, /review-implementation step 4), so this is a
# reporting gap, not an approval gap -- but it is still the operator's to
# know before handing the archive over.
if (( ! MANIFEST_WRITTEN )) && [[ -f "$BUNDLE_DIR/MANIFEST.md" ]]; then
  echo "note: stage '$STAGE' writes no MANIFEST.md. The one in $BUNDLE_DIR belongs" >&2
  echo "      to a previous round; its bundle_id no longer describes this directory" >&2
  echo "      or this archive. Regenerate at a manifest-writing stage (plan, or" >&2
  echo "      implementation/post-fix with a work-item-id) before sending either" >&2
  echo "      out for identity-bound review." >&2
fi

echo "Bundle ready:"
echo "  stage:        $STAGE"
echo "  work_item_id: ${WORK_ITEM_ID:-(none -- flat compatibility layout)}"
echo "  branch:       $BRANCH"
echo "  base:         $BASE_SHA"
echo "  head:         $HEAD_SHA"
echo "  bundle:       $BUNDLE_DIR"
echo "  archive:      $ARCHIVE"
