#!/usr/bin/env python3
"""Revision-95/96 verification pass for item 372(h)'s graph arm
(`OPUS-R119-001`, tightened by `OPUS-R120`).

`verify_372h_lock_primitive_predicate.py` mechanizes the completeness arm
(which pathnames are primitives) but, as `OPUS-R119` found, "there is none
[no code-derived reproduction] for the edge set, and the edge set is where
every error of the last four rounds has landed" -- `OPUS-R92-004`,
`OPUS-R93-002`, `WF8C-S372H-001`, `OPUS-R118-001`/`-002`, and
`OPUS-R119-001` itself are five separate rounds in which a hand-written
edge list was found short. This script closes that gap for the edges that
are genuinely closable: the ones provable from `scripts/workflow_state.py`
alone.

**The raw edge set is not uniformly code-derivable, and this script does
not pretend otherwise.** Ten edges are declared, read fresh from
`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s own fenced ten-edge table
(`parse_declared_raw_edges_from_plan` below) rather than duplicated as a
second hand-maintained Python literal (`OPUS-R120-002`: the prior
`RAW_EDGES = CODE_DERIVABLE | COMMAND_ORCHESTRATED` definition made the
union/equality check compare a set against itself -- provably unreachable
dead code, confirmed by 200 000 randomised trials never firing it). Six
are provable from this module's own AST -- two structural shapes, both
mechanized below:

  - **Guard-bracket nesting**: a `with G(...):` block, where `G` is one of
    this module's seven lock-opening functions (the five bare `fcntl.flock`
    context managers, plus `owner_mutation`/`plan_approval_guarded_mutation`,
    whose own bodies open guard (6)/(7) and hand the caller a still-open
    lease (5)/(1)) -- every acquisition reachable, transitively through
    same-module calls, from that block's own body is a target of an edge
    sourced at `G`'s primitive. Produces `(7)->(1)`, `(6)->(5)`, `(6)->(8)`.
  - **Manual acquire/release window**: the same relationship, for the two
    places this module hands back a *value* (a lease dict) instead of using
    a context manager -- `lease = acquire_guard(...)` / `release_guard(...,
    lease)` and the `acquire_plan_approval_guard`/`release_plan_approval_guard`
    equivalent -- where every acquisition reachable from a call made
    between those two lines, in the same function, is a target of an edge
    sourced at the held lease's primitive. Produces `(5)->(3)` (via
    `take_over_claim`/`recover_abandoned_destructive_guard`, both of which
    call `_establish_or_repair_identity` inside this exact window -- true
    independently of the command-file orchestration `(5)->(2)` needs, per
    `OPUS-R119` observation 3).
  - **The claim's existence window, scoped to `release_checkpoint`
    specifically** (primitive (8) is not a guard -- there is no
    `acquire_claim`/`release_claim` pair to generalize the rule above to):
    `release_checkpoint` is the one function in this module that both opens
    a window reaching guard (6) and lease (5) (via `with owner_mutation(...):`)
    *and*, later in that same function body, unlinks claim (8)'s own
    pathname -- the unlink is proof, internal to this one function, that
    (8) was already durably published before the window opened (nothing in
    this module re-publishes it in between). Produces `(8)->(6)`, `(8)->(5)`.

**Four edges are not code-derivable, and are not claimed to be.** They
depend on `.claude/commands/milestone-implement.md`'s own step ordering --
which functions this module exposes as independently callable primitives
get invoked next to each other -- not on anything `scripts/workflow_state.py`
itself enforces:

  - `(1)->(2)`: no production call site at all (`plan_approval_guarded_mutation`
    has six call sites, all in `workflow_integration_test.py`) -- declared by
    design intent alone, per `OPUS-R116-004`'s original finding, unresolved
    by design (see item 372(h)'s own "declared, not derivable" clause). Its
    evidence is the sanctioned `D-Approval-Commits` step 8b procedure, not a
    current production call site -- `(1)->(2)` is tracked in
    `COMMAND_ORCHESTRATED` on that footing, exactly as the other three are on
    theirs, and is never asked to satisfy the mechanical AST check the six
    code-derivable edges do.
  - `(5)->(2)`, `(8)->(2)`, `(8)->(3)`: real, live edges (step 1d/1f, per
    `.claude/commands/milestone-implement.md:121-136`), but nothing in
    `scripts/workflow_state.py` calls `claim_checkpoint`/`owner_mutation` next
    to each other in this order -- that sequencing is the command file's own
    orchestration, external to this module.

**Both directions of equality are checked, for the six code-derivable edges
only**: every edge this script's own passes discover is asserted to be one
of the six declared code-derivable edges (a spurious extra edge fails,
naming it -- the property `OPUS-R119` asked this arm to have); every one of
the six declared code-derivable edges is asserted to be rediscovered (a
declared edge silently going stale -- e.g. a future refactor that moves
`_establish_or_repair_identity`'s call outside the guard window -- fails
too). The one shared `os.link` statement in this module
(`_publish_claim_exclusive`, reached from both `_publish_guard` (5) and
`_claim_or_refuse` (8)) is attributed *caller-aware* -- using the actual
call site the walk arrived through, not an over-approximation of every
primitive the statement could ever resolve to regardless of caller
(`OPUS-R120-001`: the prior over-approximation made `(6)->(5)` and
`(6)->(8)` alibi each other, so deleting either edge's only real code
evidence left it "discovered" anyway, satisfied by the other edge's own
evidence for the same shared statement -- reproduced below by
`MUTATION_REMOVE_6_TO_8`/`MUTATION_REMOVE_6_TO_5`, each removing exactly
one edge's only evidence independently). The four command-orchestrated
edges are compared, bidirectionally, against the same independently-parsed
plan table the six code-derivable edges are (`parse_declared_raw_edges_from_plan`),
never claimed to pass the mechanical AST check the other six do.

**Single-attempt precondition, call-chain-aware** (`OPUS-R119-004`,
tightened by `OPUS-R120-003`): the three `os.link`-based publication entry
points this design uses (`_publish_plan_approval_guard`, `_publish_guard`,
`_claim_or_refuse`) are non-blocking only so long as their own `os.link`
acquisition is never enclosed in a wait/retry construct -- checked not only
at the acquisition's own call site but transitively up through every frame
on its static call chain, to unbounded depth, mirroring the completeness
arm's own `eexist_guarded` climb, not a call-site-only lexical check a
one-frame wrapper around the *caller's* invocation would defeat (the exact
defect class `OPUS-R117-002` found and fixed for the EEXIST clause,
reappearing here for the wait/retry clause -- reproduced below by
`MUTATION_WRAP_PUBLISH_GUARD_CALL_IN_RETRY`, which wraps `_publish_guard`'s
own call to `_publish_claim_exclusive` in a retry loop one frame away from
the `os.link` statement itself, never moving that statement).

Run: `python3 docs/ai-workflow/dry-run/verify_372h_raw_edge_derivation.py`
from the repository root. Exits non-zero on any mismatch.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_372h_lock_primitive_predicate import (  # noqa: E402
    DECLARED_PRIMITIVES, PathResolver, _synthetic_call_marker, callers_of, canon, parse_module,
)

PATHNAME_TO_PRIMITIVE = {canon(v): k for k, v in DECLARED_PRIMITIVES.items()}

# ---------------------------------------------------------------------------
# The two code-derivable/command-orchestrated tiers this script itself
# claims responsibility for. The full ten-edge set is never duplicated here
# as a third literal -- `parse_declared_raw_edges_from_plan` reads it fresh
# from the plan document instead, so the comparison below has two genuinely
# independent sides (`OPUS-R120-002`).
# ---------------------------------------------------------------------------

CODE_DERIVABLE = {
    ("7", "1"), ("6", "5"), ("6", "8"), ("8", "6"), ("8", "5"), ("5", "3"),
}

COMMAND_ORCHESTRATED = {
    ("1", "2"), ("5", "2"), ("8", "2"), ("8", "3"),
}

BLOCKING_TARGETS = {"2", "3", "4", "6", "7"}  # fcntl.flock primitives

PLAN_PATH = Path(__file__).resolve().parent.parent / "WORKFLOW_V2_PLAN.md"

# The fenced ```text ten-edge table's own opening and closing rows -- stable
# anchors independent of surrounding prose, so a reorganized or duplicated
# table is caught (fails closed) rather than silently mis-parsed.
RAW_EDGE_TABLE_START_ANCHOR = "(7) PLAN_APPROVAL_MUTATION.guardlock"
RAW_EDGE_TABLE_END_ANCHOR = "(4) identity-gap.lock"


def is_blocking(edge: tuple[str, str]) -> bool:
    return edge[1] in BLOCKING_TARGETS


# ---------------------------------------------------------------------------
# The independent evidence source for the ten-edge union/equality check:
# the plan document's own fenced table, parsed fresh from disk each run
# (`OPUS-R120-002`). Deliberately *not* derived from `CODE_DERIVABLE`/
# `COMMAND_ORCHESTRATED` -- that was the prior definition's exact defect.
# ---------------------------------------------------------------------------


def parse_declared_raw_edges_from_plan(plan_text: str) -> set[tuple[str, str]]:
    """The ten-edge table lives in exactly one place in the plan document --
    a fenced ```text block anchored by its own opening
    (`PLAN_APPROVAL_MUTATION.guardlock`) and closing (`identity-gap.lock`,
    the isolated leaf) rows. Read fresh from disk here so the comparison in
    `compare_to_declared` checks the script's own `CODE_DERIVABLE`/
    `COMMAND_ORCHESTRATED` constants against a source that is not derived
    from those same constants. Fails closed, naming the reason, if the
    anchors are not found exactly once, or if the parsed count is not
    exactly ten -- an edited or reorganized table must not silently stop
    being checked against."""
    blocks = re.findall(r"```text\n(.*?)\n```", plan_text, re.DOTALL)
    candidates = [b for b in blocks
                  if RAW_EDGE_TABLE_START_ANCHOR in b and RAW_EDGE_TABLE_END_ANCHOR in b]
    if len(candidates) != 1:
        raise AssertionError(
            f"expected exactly one fenced ten-edge table in {PLAN_PATH}, found "
            f"{len(candidates)} -- the raw-edge table's anchors have moved or been duplicated"
        )
    block = candidates[0]
    edges = {(m.group(1), m.group(2))
             for m in re.finditer(r"^\((\d)\)[^\n]*?→[^\n]*?\((\d)\)", block, re.MULTILINE)}
    if len(edges) != 10:
        raise AssertionError(
            f"parsed {len(edges)} edges from the plan's ten-edge table, expected exactly 10 "
            f"-- {sorted(edges)}"
        )
    return edges


# ---------------------------------------------------------------------------
# Step 1: recognize an acquisition Call node and resolve it to a declared
# primitive (reusing `PathResolver` exactly as the completeness-arm script
# does -- same resolution rules, same anchor convention, no divergence).
# ---------------------------------------------------------------------------


def is_flock_ex_call(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "flock"
            and isinstance(f.value, ast.Name) and f.value.id == "fcntl"
            and len(call.args) >= 2 and isinstance(call.args[1], ast.Attribute)
            and call.args[1].attr == "LOCK_EX")


def is_link_call(call: ast.Call) -> bool:
    f = call.func
    return ((isinstance(f, ast.Attribute) and f.attr == "link"
             and isinstance(f.value, ast.Name) and f.value.id == "os")
            or (isinstance(f, ast.Name) and f.id == "link"))


def _target_var_name(call: ast.Call) -> str | None:
    """`os.link(src, dst)` -- `dst` (the second, positional-Name argument)
    is the exclusive/final pathname; mirrors `PathResolver._locate_target_var`'s
    own `os.link` branch (`src` is always the `tempfile.mkstemp` staging
    name, never the acquisition target)."""
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Name):
        return call.args[1].id
    return None


def _is_required_param(fn: ast.FunctionDef, name: str) -> bool:
    args = fn.args
    all_params = list(args.args) + list(args.kwonlyargs)
    all_defaults = ([None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
                     + list(args.kw_defaults))
    for param, default in zip(all_params, all_defaults):
        if param.arg == name:
            return default is None
    return False


def _matching_argument(call: ast.Call, fn: ast.FunctionDef, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    for i, p in enumerate(fn.args.args):
        if p.arg == name and i < len(call.args):
            return call.args[i]
    return None


def _resolve_via_actual_caller(call: ast.Call, fn: ast.FunctionDef, resolver: PathResolver,
                                caller: tuple[ast.Call, ast.FunctionDef] | None) -> str | None:
    """Narrow a shared acquisition statement's over-approximated resolution
    (`resolve_call_site_all` found more than one caller-supplied pathname,
    because the statement is reached from more than one caller passing a
    different concrete path) down to the single pathname the *actual* walk
    that reached this call used, rather than the ones every other caller
    anywhere in the module would supply (`OPUS-R120-001`). Returns `None`
    -- no narrowing -- if the target variable is not a required parameter
    of `fn` (nothing to narrow) or the walk has no recorded caller for this
    hop, so the caller falls back to the conservative over-approximation
    instead of guessing."""
    if caller is None:
        return None
    target_name = _target_var_name(call)
    if target_name is None or not _is_required_param(fn, target_name):
        return None
    caller_call, caller_fn = caller
    arg_expr = _matching_argument(caller_call, fn, target_name)
    if arg_expr is None:
        return None
    try:
        return resolver.resolve_arbitrary_expr(arg_expr, caller_fn)
    except AssertionError:
        return None


def primitives_of_call(call: ast.Call, fn: ast.FunctionDef, resolver: PathResolver,
                        caller: tuple[ast.Call, ast.FunctionDef] | None = None) -> set[str]:
    """If `call` is itself an acquisition, the set of declared primitives
    it resolves to -- normally exactly one. The one shared `os.link`
    statement in this module (`_publish_claim_exclusive`, reached from both
    `_publish_guard` (5) and `_claim_or_refuse` (8)) resolves to *two*
    pathnames when asked context-free (`resolve_call_site_all`); resolved
    here caller-aware instead, using the actual call site (`caller`) the
    walk arrived through, so `(6)->(5)` and `(6)->(8)` each stand on their
    own evidence rather than alibiing each other. Falls back to the full
    over-approximated set only when no caller-specific narrowing is
    possible -- never occurs in this module today (every shared statement
    is reached with `caller` populated by the time it is visited) -- kept
    as a fail-open default so an as-yet-unseen shared statement is still
    conservatively over-approximated rather than silently dropped."""
    if not (is_flock_ex_call(call) or is_link_call(call)):
        return set()
    try:
        if is_flock_ex_call(call):
            resolved = [resolver.resolve_call_site(call, fn)]
        else:
            resolved = resolver.resolve_call_site_all(call, fn)
            if len(resolved) > 1:
                narrowed = _resolve_via_actual_caller(call, fn, resolver, caller)
                if narrowed is not None:
                    resolved = [narrowed]
    except AssertionError:
        return set()
    return {PATHNAME_TO_PRIMITIVE[canon(r)] for r in resolved if canon(r) in PATHNAME_TO_PRIMITIVE}


# ---------------------------------------------------------------------------
# Step 2: transitive reachability -- from a seed list of (call, owning
# function) pairs, walk same-module calls to unbounded depth (cycle-guarded)
# collecting every primitive acquired anywhere in that reachable set. Each
# recursive step records the exact (call, fn) hop it descended through, so
# a shared statement reached deeper in the walk can be resolved caller-aware
# against the specific hop that led to it, not every caller in the module.
# ---------------------------------------------------------------------------


def reachable_primitives_from(entries: list[tuple[ast.Call, ast.FunctionDef]],
                               functions: dict[str, ast.FunctionDef], resolver: PathResolver,
                               seen: frozenset[str],
                               caller: tuple[ast.Call, ast.FunctionDef] | None = None) -> set[str]:
    found: set[str] = set()
    for call, fn in entries:
        found |= primitives_of_call(call, fn, resolver, caller)
        if (isinstance(call.func, ast.Name) and call.func.id in functions
                and call.func.id not in seen):
            callee = functions[call.func.id]
            nested = [(n, callee) for n in ast.walk(callee) if isinstance(n, ast.Call)]
            found |= reachable_primitives_from(nested, functions, resolver, seen | {call.func.id},
                                                caller=(call, fn))
    return found


def calls_in(nodes: list[ast.stmt]) -> list[ast.Call]:
    mod = ast.Module(body=nodes, type_ignores=[])
    return [n for n in ast.walk(mod) if isinstance(n, ast.Call)]


# ---------------------------------------------------------------------------
# Step 3: the two lexical window shapes.
# ---------------------------------------------------------------------------

# `with G(...):` -- G's own primitive is held for the with-block's body.
# The five bare flock context managers, plus the two `@contextmanager`
# wrappers whose *own* body (steps 4/4b below) is what actually reaches the
# lease primitive -- both are lock-opening functions in exactly the sense
# this rule needs, whether the flock call is direct or one level removed.
DIRECT_WITH_PRIMITIVE = {
    "state_lock": "2",
    "identity_document_lock": "3",
    "identity_gap_lock": "4",
    "guard_mutation_lock": "6",
    "_plan_approval_guard_lock": "7",
    "owner_mutation": "5",
    "plan_approval_guarded_mutation": "1",
}

# `lease = acquire_X(...)` ... `release_X(..., lease, ...)` in the same
# function -- the lease's primitive is held for everything called strictly
# between those two lines (by `end_lineno`, so a multi-line acquire call's
# own keyword-argument expressions are never mistaken for "after" it).
MANUAL_PAIRS: dict[tuple[str, str], str] = {
    ("acquire_guard", "release_guard"): "5",
    ("acquire_plan_approval_guard", "release_plan_approval_guard"): "1",
}


def find_with_block_windows(tree: ast.Module) -> list[tuple[list[ast.Call], ast.FunctionDef, str]]:
    """(calls-in-body, owning function, primitive) for every `with G(...):`
    in the module whose `G` is a known lock-opening function."""
    windows: list[tuple[list[ast.Call], ast.FunctionDef, str]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.With) or len(node.items) != 1:
                continue
            ctx = node.items[0].context_expr
            if not (isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name)):
                continue
            primitive = DIRECT_WITH_PRIMITIVE.get(ctx.func.id)
            if primitive is None:
                continue
            windows.append((calls_in(node.body), fn, primitive))
    return windows


def find_manual_pair_windows(tree: ast.Module) -> list[tuple[list[tuple[ast.Call, ast.FunctionDef]], str]]:
    """(entries, primitive) for every manual acquire/release window."""
    windows: list[tuple[list[tuple[ast.Call, ast.FunctionDef]], str]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for (acq_name, rel_name), primitive in MANUAL_PAIRS.items():
            acq_assign = None
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name) and node.value.func.id == acq_name):
                    acq_assign = node
                    break
            if acq_assign is None:
                continue
            acq_var = acq_assign.targets[0].id
            acq_end = acq_assign.value.end_lineno or acq_assign.value.lineno
            rel_call = None
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == rel_name
                        and any(isinstance(a, ast.Name) and a.id == acq_var for a in node.args)):
                    rel_call = node
                    break
            if rel_call is None or rel_call.lineno <= acq_end:
                continue
            window_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                             and n is not acq_assign.value and n is not rel_call
                             and acq_end < n.lineno < rel_call.lineno]
            windows.append(([(c, fn) for c in window_calls], primitive))
    return windows


# ---------------------------------------------------------------------------
# Step 4: primitive (8)'s existence window, scoped to `release_checkpoint`
# alone -- verified structurally, not assumed (`OPUS-R119` observation 3's
# own caution against carrying forward an unverified claim applies here
# just as much as to `(5)->(3)`).
# ---------------------------------------------------------------------------


def find_claim_existence_window(functions: dict[str, ast.FunctionDef],
                                 resolver: PathResolver) -> bool:
    """True iff `release_checkpoint` demonstrably both opens a window
    reaching guard (6)/lease (5) (`with owner_mutation(...):`) and, later
    in the same function body, unlinks claim (8)'s own resolved pathname
    -- the structural precondition for attributing `(8)->(6)`/`(8)->(5)`."""
    fn = functions.get("release_checkpoint")
    if fn is None:
        return False
    owner_mutation_line = None
    for node in ast.walk(fn):
        if isinstance(node, ast.With) and len(node.items) == 1:
            ctx = node.items[0].context_expr
            if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name) and ctx.func.id == "owner_mutation":
                owner_mutation_line = node.lineno
                break
    if owner_mutation_line is None:
        return False
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "unlink" and node.lineno > owner_mutation_line):
            continue
        try:
            resolved = resolver.resolve_arbitrary_expr(node.func.value, fn)
        except AssertionError:
            continue
        if PATHNAME_TO_PRIMITIVE.get(canon(resolved)) == "8":
            return True
    return False


# ---------------------------------------------------------------------------
# Step 5: the single-attempt discriminator, call-chain-aware (`OPUS-R120-003`).
# Every `os.link` acquisition's non-blocking classification depends on it
# genuinely being one attempt -- not lexically enclosed in a wait/retry
# construct at its own call site, nor at any frame on its static call
# chain. Mirrors `eexist_guarded`'s own unbounded, cycle-guarded climb
# through `callers_of`, substituting "encloses the call in a `for`/`while`"
# for "encloses the call in a guarding `try`/`except`".
# ---------------------------------------------------------------------------


def call_is_in_wait_construct(call: ast.Call, fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, (ast.For, ast.While)):
            if any(n is call for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))):
                return True
    return False


def enclosed_in_wait_construct(tree: ast.Module, call: ast.Call, fn: ast.FunctionDef,
                                _seen: frozenset[str] | None = None) -> bool:
    """Transitively up through the call's own static call chain -- its
    immediately enclosing `for`/`while`, or, failing that, up through every
    caller's own enclosing `for`/`while`, to unbounded depth (cycle-guarded
    by `_seen`), never capped at one level -- so a wrapper one frame away
    from the acquisition (retrying the *call* to the shared publisher,
    rather than the `os.link` statement itself) cannot silently keep the
    non-blocking classification (`OPUS-R120-003`, mirroring `OPUS-R117-002`'s
    identical fix for the EEXIST clause)."""
    if call_is_in_wait_construct(call, fn):
        return True
    seen = (_seen or frozenset()) | {fn.name}
    for caller in callers_of(tree, fn.name):
        if caller.name in seen:
            continue
        for node in ast.walk(caller):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == fn.name and call_is_in_wait_construct(node, caller)):
                return True
        if enclosed_in_wait_construct(tree, _synthetic_call_marker(caller, fn.name), caller, seen):
            return True
    return False


def find_all_link_calls(tree: ast.Module,
                         functions: dict[str, ast.FunctionDef]) -> list[tuple[ast.Call, ast.FunctionDef]]:
    return [(node, fn) for fn in functions.values() for node in ast.walk(fn)
            if isinstance(node, ast.Call) and is_link_call(node)]


def check_single_attempt_discriminator(tree: ast.Module,
                                        functions: dict[str, ast.FunctionDef]) -> list[str]:
    """The blocking/non-blocking discriminator's own precondition: every
    `os.link` acquisition in this module must be a single attempt, checked
    not just at its own call site but transitively up through every frame
    on its static call chain -- so a future wrapper cannot silently
    invalidate the model by reusing the `link` tag underneath real wait
    semantics one frame removed from the acquisition itself."""
    failures = []
    for call, fn in find_all_link_calls(tree, functions):
        if enclosed_in_wait_construct(tree, call, fn):
            failures.append(
                f"os.link acquisition in {fn.name} (line {call.lineno}) is enclosed in a "
                f"wait/retry construct somewhere on its static call chain -- its non-blocking "
                f"classification no longer holds"
            )
    return failures


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------


def run_pass(tree: ast.Module, *, verbose: bool = True) -> tuple[set[tuple[str, str]], list[str]]:
    functions = {fn.name: fn for fn in ast.walk(tree)
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
    resolver = PathResolver(tree)
    discovered: set[tuple[str, str]] = set()
    failures: list[str] = []

    for calls, fn, primitive in find_with_block_windows(tree):
        reached = reachable_primitives_from([(c, fn) for c in calls], functions, resolver,
                                             frozenset({fn.name}))
        for target in reached - {primitive}:
            discovered.add((primitive, target))

    for entries, primitive in find_manual_pair_windows(tree):
        reached = reachable_primitives_from(entries, functions, resolver, frozenset())
        for target in reached - {primitive}:
            discovered.add((primitive, target))

    if find_claim_existence_window(functions, resolver):
        reached = reachable_primitives_from(
            [(c, functions["owner_mutation"]) for c in ast.walk(functions["owner_mutation"])
             if isinstance(c, ast.Call)],
            functions, resolver, frozenset({"owner_mutation"}))
        for target in reached - {"8"}:
            discovered.add(("8", target))
    else:
        failures.append("release_checkpoint no longer demonstrates the (8)-existence "
                         "window structurally -- (8)->(6)/(8)->(5) cannot be attributed")

    if verbose:
        print(f"Discovered {len(discovered)} code-derivable raw edges:")
        for edge in sorted(discovered):
            tag = "blocking" if is_blocking(edge) else "non-blocking"
            print(f"  ({edge[0]}) -> ({edge[1]})  -- {tag}")

    return discovered, failures


def compare_to_declared(discovered: set[tuple[str, str]], plan_declared_edges: set[tuple[str, str]],
                         *, verbose: bool = True) -> list[str]:
    failures: list[str] = []
    undeclared = discovered - CODE_DERIVABLE
    stale = CODE_DERIVABLE - discovered
    if undeclared:
        failures.append(f"code-discovered edges not among the six declared code-derivable "
                         f"edges: {sorted(undeclared)}")
    if stale:
        failures.append(f"declared code-derivable edges not rediscovered live in the code: "
                         f"{sorted(stale)}")
    if verbose:
        print(f"\n-- Bidirectional comparison against the six declared code-derivable edges --")
        if not failures:
            print(f"  {len(discovered)} discovered == {len(CODE_DERIVABLE)} declared. PASS.")
        print(f"\n-- Tier-2 command-orchestrated edges (not mechanically checked against code; "
              f"compared bidirectionally against the plan's own independently-parsed ten-edge "
              f"table below) --")
        for edge in sorted(COMMAND_ORCHESTRATED):
            print(f"  ({edge[0]}) -> ({edge[1]})  -- declared, command-file-orchestrated or "
                  f"design-intent-only, not derivable from scripts/workflow_state.py alone")

    # `OPUS-R120-002`: `plan_declared_edges` is parsed fresh from the plan
    # document, independent of `CODE_DERIVABLE`/`COMMAND_ORCHESTRATED` --
    # so this comparison is reachable and non-vacuous regardless of whether
    # `undeclared`/`stale` above are already non-empty (the prior
    # `and not failures` guard on this check is gone; it was exactly what
    # made the check unreachable).
    script_total = discovered | COMMAND_ORCHESTRATED
    undeclared_in_plan = script_total - plan_declared_edges
    stale_in_script = plan_declared_edges - script_total
    if undeclared_in_plan:
        failures.append(f"script-derived edges (code-discovered ∪ COMMAND_ORCHESTRATED) not "
                         f"present in the plan's own declared ten-edge table: "
                         f"{sorted(undeclared_in_plan)}")
    if stale_in_script:
        failures.append(f"plan-declared edges not tracked by the script's CODE_DERIVABLE/"
                         f"COMMAND_ORCHESTRATED constants: {sorted(stale_in_script)}")

    blocking = {e for e in plan_declared_edges if is_blocking(e)}
    non_blocking = plan_declared_edges - blocking
    if len(blocking) != 6 or len(non_blocking) != 4:
        failures.append(f"blocking/non-blocking split is not 6/4: "
                         f"{len(blocking)} blocking, {len(non_blocking)} non-blocking")
    sources = {e[0] for e in blocking}
    targets = {e[1] for e in blocking}
    if sources & targets:
        failures.append(f"blocking sub-order is not acyclic -- sources/targets overlap at "
                         f"{sorted(sources & targets)}")
    elif verbose:
        print(f"\n-- Blocking sub-order acyclicity (six edges) --")
        print(f"  blocking edges: {sorted(blocking)}")
        print(f"  sources {sorted(sources)}, targets {sorted(targets)}, disjoint. Acyclic. PASS.")
    return failures


# ---------------------------------------------------------------------------
# Required regression checks:
# (a)/(a2) both directions of "not vacuous" for the code-derivable tier --
#     removing the code evidence for a declared code-derivable edge must
#     make it disappear (the reverse-direction check is not vacuous),
#     including independently for each of the two edges that share one
#     `os.link` statement (`OPUS-R120-001`);
# (b) injecting a spurious nested acquisition must make a NEW edge appear,
#     and that new edge must not be in the declared six, so the
#     forward-direction check fails and names it;
# (c)/(d) the plan-table comparison (`OPUS-R120-002`) is reachable and
#     non-vacuous in both directions;
# (e) the single-attempt discriminator (`OPUS-R120-003`) is call-chain-aware,
#     not call-site-only.
# ---------------------------------------------------------------------------

MUTATION_REMOVE_5_TO_3 = (
    # (5)->(3) has *two* independent live call paths -- take_over_claim and
    # recover_abandoned_destructive_guard both call
    # `_establish_or_repair_identity` inside their own acquire_guard/
    # release_guard window (`OPUS-R119` observation 3). Proving the check
    # is not vacuous means removing *both*, not just one -- removing only
    # one (tried first, while writing this script) left the edge
    # discovered via the other path and silently passed, which would have
    # been exactly the kind of false-negative regression check `OPUS-R117
    # -002` found in the completeness arm's own prior evidence table.
    "        _establish_or_repair_identity(repo_root, work_item_id, now=now,\n"
    "                                      evidence=evidence, operation=\"takeover\")\n"
    "        _publish_claim_replacing(path, record,\n",
    "        _publish_claim_replacing(path, record,\n",
)

MUTATION_REMOVE_5_TO_3_SECOND_PATH = (
    "        _establish_or_repair_identity(repo_root, work_item_id, now=now,\n"
    "                                      evidence=evidence, operation=\"recovery\")\n"
    "        _publish_claim_replacing(path, record, allow_unreadable_target=claim_undecidable)\n",
    "        _publish_claim_replacing(path, record, allow_unreadable_target=claim_undecidable)\n",
)

MUTATION_INJECT_EXTRA_EDGE = (
    # write_worktree_identity: inject a spurious nested `with state_lock(...):`
    # inside the already-open identity_document_lock window -- must surface
    # a NEW edge, (3)->(2), not among the declared six, and the
    # forward-direction check must fail, naming it.
    "    full_path = repo_root / path\n"
    "    with identity_document_lock(repo_root):\n"
    "        existing = _load_json(full_path) or {}\n",
    "    full_path = repo_root / path\n"
    "    with identity_document_lock(repo_root):\n"
    "        with state_lock(repo_root):\n"
    "            pass\n"
    "        existing = _load_json(full_path) or {}\n",
)

MUTATION_REMOVE_6_TO_8 = (
    # adopt_claim: dedent `_claim_or_refuse(...)` out of the
    # `with guard_mutation_lock` block -- destroys (6)->(8)'s *only* code
    # evidence without touching (6)->(5)'s (acquire_guard's own, separate,
    # window). Under the prior context-free over-approximation, (6)->(8)
    # stayed "discovered" anyway, satisfied by (6)->(5)'s own evidence for
    # the shared `_publish_claim_exclusive` statement (`OPUS-R120-001`).
    "    with guard_mutation_lock(repo_root, work_item_id):\n"
    "        checkpoint_origination_provable(repo_root, work_item_id, checkpoint_id, state_rel_path=state_rel_path)\n"
    "        return _claim_or_refuse(repo_root, work_item_id, record)\n",
    "    with guard_mutation_lock(repo_root, work_item_id):\n"
    "        checkpoint_origination_provable(repo_root, work_item_id, checkpoint_id, state_rel_path=state_rel_path)\n"
    "    return _claim_or_refuse(repo_root, work_item_id, record)\n",
)

MUTATION_REMOVE_6_TO_5 = (
    # acquire_guard: dedent `_acquire_guard_locked(...)` out of the
    # `with guard_mutation_lock` block -- destroys (6)->(5)'s *only* code
    # evidence without touching (6)->(8)'s (adopt_claim's own, separate,
    # window) -- the independent mutation proving the reverse direction is
    # not vacuous for this edge either.
    "    with guard_mutation_lock(repo_root, work_item_id):\n"
    "        return _acquire_guard_locked(repo_root, work_item_id, body=body,\n"
    "                                     holder_owner_token=holder_owner_token, role=role,\n"
    "                                     authorized_lease_id=authorized_lease_id)\n",
    "    with guard_mutation_lock(repo_root, work_item_id):\n"
    "        pass\n"
    "    return _acquire_guard_locked(repo_root, work_item_id, body=body,\n"
    "                                 holder_owner_token=holder_owner_token, role=role,\n"
    "                                 authorized_lease_id=authorized_lease_id)\n",
)

MUTATION_WRAP_PUBLISH_GUARD_CALL_IN_RETRY = (
    # _publish_guard: wrap its own call to `_publish_claim_exclusive` in a
    # retry loop, one frame away from the `os.link` statement itself, which
    # stays exactly where it is inside `_publish_claim_exclusive`. A
    # call-site-only lexical check would see no loop in
    # `_publish_claim_exclusive`'s own body and miss this entirely --
    # exactly the shape `OPUS-R117-002` found defeating the EEXIST clause's
    # own one-level-only climb (`OPUS-R120-003`).
    "def _publish_guard(repo_root: Path, work_item_id: str, body: dict) -> None:\n"
    "    _publish_claim_exclusive(guard_path(repo_root, work_item_id), body)\n",
    "def _publish_guard(repo_root: Path, work_item_id: str, body: dict) -> None:\n"
    "    while True:\n"
    "        try:\n"
    "            _publish_claim_exclusive(guard_path(repo_root, work_item_id), body)\n"
    "            return\n"
    "        except FileExistsError:\n"
    "            time.sleep(0.05)\n",
)


def run_regression_checks(base_src: str, plan_declared_edges: set[tuple[str, str]]) -> list[str]:
    failures: list[str] = []

    old1, new1 = MUTATION_REMOVE_5_TO_3
    old2, new2 = MUTATION_REMOVE_5_TO_3_SECOND_PATH
    if old1 not in base_src or old2 not in base_src:
        failures.append("mutation (a) anchor text not found -- source has drifted, update the mutation")
    else:
        mutated_src = base_src.replace(old1, new1, 1).replace(old2, new2, 1)
        mutated_tree = parse_module(mutated_src)
        discovered, mfail = run_pass(mutated_tree, verbose=False)
        if mfail:
            failures.append(f"mutation (a) base pass unexpectedly failed: {mfail}")
        elif ("5", "3") not in discovered:
            print("regression (a) (remove both (5)->(3) call paths): edge absent, as required. PASS.")
        else:
            failures.append(
                "REGRESSION FAILURE: dedenting take_over_claim's identity-repair call out of "
                "the acquire/release window did not drop (5)->(3) -- the manual-pair window "
                "rule is vacuous"
            )

    old, new = MUTATION_INJECT_EXTRA_EDGE
    if old not in base_src:
        failures.append("mutation (b) anchor text not found -- source has drifted, update the mutation")
    else:
        mutated_tree = parse_module(base_src.replace(old, new, 1))
        discovered, mfail = run_pass(mutated_tree, verbose=False)
        if mfail:
            failures.append(f"mutation (b) base pass unexpectedly failed: {mfail}")
        elif ("3", "2") in discovered:
            cmp_failures = compare_to_declared(discovered, plan_declared_edges, verbose=False)
            if any("not among the six declared" in f for f in cmp_failures):
                print("regression (b) (inject spurious (3)->(2)): new edge discovered and "
                      "the forward-direction check names it. PASS.")
            else:
                failures.append(
                    "REGRESSION FAILURE: (3)->(2) was discovered but the forward-direction "
                    "equality check did not fail on it"
                )
        else:
            failures.append(
                "REGRESSION FAILURE: injecting a nested with-block inside "
                "write_worktree_identity's guard did not surface a new edge -- the "
                "guard-bracket-nesting rule is vacuous"
            )

    old_6_8, new_6_8 = MUTATION_REMOVE_6_TO_8
    if old_6_8 not in base_src:
        failures.append("mutation (a2) anchor text not found -- source has drifted, update the mutation")
    else:
        mutated_tree = parse_module(base_src.replace(old_6_8, new_6_8, 1))
        discovered, mfail = run_pass(mutated_tree, verbose=False)
        if mfail:
            failures.append(f"mutation (a2) base pass unexpectedly failed: {mfail}")
        elif ("6", "8") not in discovered:
            print("regression (a2) (destroy (6)->(8)'s only evidence, adopt_claim): "
                  "edge absent, as required. PASS.")
        else:
            failures.append(
                "REGRESSION FAILURE: dedenting adopt_claim's _claim_or_refuse call out of its "
                "guard_mutation_lock window did not drop (6)->(8) -- the shared os.link "
                "statement's caller-aware attribution is vacuous for this edge (OPUS-R120-001)"
            )

    old_6_5, new_6_5 = MUTATION_REMOVE_6_TO_5
    if old_6_5 not in base_src:
        failures.append("mutation (a3) anchor text not found -- source has drifted, update the mutation")
    else:
        mutated_tree = parse_module(base_src.replace(old_6_5, new_6_5, 1))
        discovered, mfail = run_pass(mutated_tree, verbose=False)
        if mfail:
            failures.append(f"mutation (a3) base pass unexpectedly failed: {mfail}")
        elif ("6", "5") not in discovered:
            print("regression (a3) (destroy (6)->(5)'s only evidence, acquire_guard): "
                  "edge absent, as required. PASS.")
        else:
            failures.append(
                "REGRESSION FAILURE: dedenting acquire_guard's _acquire_guard_locked call out "
                "of its guard_mutation_lock window did not drop (6)->(5) -- the shared os.link "
                "statement's caller-aware attribution is vacuous for this edge (OPUS-R120-001)"
            )

    dropped = plan_declared_edges - {("5", "3")}
    discovered_real, _ = run_pass(parse_module(base_src), verbose=False)
    cmp_fail = compare_to_declared(discovered_real, dropped, verbose=False)
    if any("not present in the plan's own declared" in f for f in cmp_fail):
        print("regression (c) (plan table missing a script-claimed edge ('5','3')): "
              "check fires and names it. PASS.")
    else:
        failures.append(
            "REGRESSION FAILURE: removing ('5','3') from the parsed plan-table set did not "
            "make the plan-table comparison fail -- it is still vacuous (OPUS-R120-002)"
        )

    fabricated = plan_declared_edges | {("2", "5")}
    cmp_fail = compare_to_declared(discovered_real, fabricated, verbose=False)
    if any("not tracked by the script's" in f for f in cmp_fail):
        print("regression (d) (plan table carries an edge the script doesn't track, "
              "('2','5')): check fires and names it. PASS.")
    else:
        failures.append(
            "REGRESSION FAILURE: adding a fabricated edge to the parsed plan-table set did not "
            "make the plan-table comparison fail -- it is still vacuous (OPUS-R120-002)"
        )

    old_wrap, new_wrap = MUTATION_WRAP_PUBLISH_GUARD_CALL_IN_RETRY
    if old_wrap not in base_src:
        failures.append("mutation (e) anchor text not found -- source has drifted, update the mutation")
    else:
        mutated_tree = parse_module(base_src.replace(old_wrap, new_wrap, 1))
        mutated_functions = {fn.name: fn for fn in ast.walk(mutated_tree)
                              if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
        target = next(((c, f) for c, f in find_all_link_calls(mutated_tree, mutated_functions)
                       if f.name == "_publish_claim_exclusive"), None)
        if target is None:
            failures.append("mutation (e): could not locate _publish_claim_exclusive's os.link "
                             "call in the mutated tree")
        else:
            call, fn = target
            if enclosed_in_wait_construct(mutated_tree, call, fn):
                print("regression (e) (enclosing wrapper one frame away from the os.link "
                      "statement, call-chain-aware climb): detected. PASS.")
            else:
                failures.append(
                    "REGRESSION FAILURE: wrapping _publish_guard's own call to "
                    "_publish_claim_exclusive in a retry loop was not detected -- the "
                    "single-attempt check is still call-site-only, not call-chain-aware "
                    "(OPUS-R120-003)"
                )

    return failures


def main() -> int:
    src = (Path(__file__).resolve().parents[3] / "scripts" / "workflow_state.py").read_text()
    tree = parse_module(src)
    functions = {fn.name: fn for fn in ast.walk(tree)
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
    discovered, failures = run_pass(tree, verbose=True)

    plan_declared_edges = parse_declared_raw_edges_from_plan(PLAN_PATH.read_text())
    failures += compare_to_declared(discovered, plan_declared_edges, verbose=True)

    print("\n-- Single-attempt discriminator sweep (call-chain-aware) --")
    single_attempt_failures = check_single_attempt_discriminator(tree, functions)
    if single_attempt_failures:
        failures += single_attempt_failures
    else:
        print("  no os.link acquisition is enclosed in a wait/retry construct anywhere on its "
              "static call chain. PASS.")

    print("\n-- Required regression checks (non-vacuousness) --")
    failures += run_regression_checks(src, plan_declared_edges)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nAll checks passed: the six code-derivable raw edges are mechanically "
          "rediscovered, exactly, bidirectionally, with the shared os.link statement "
          "attributed caller-aware; the four command-orchestrated edges are compared "
          "bidirectionally against the plan's own independently-parsed ten-edge table; the "
          "single-attempt discriminator is call-chain-aware; the blocking sub-order (six "
          "edges) is acyclic; all required regressions (a missing edge, a spurious extra "
          "edge, each shared-statement edge independently, both plan-table divergence "
          "directions, and a call-chain-only retry wrapper) are caught.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
