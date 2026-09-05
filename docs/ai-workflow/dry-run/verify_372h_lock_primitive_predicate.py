#!/usr/bin/env python3
"""Revision-93/-94 verification pass for item 372(h)'s completeness arm
(`OPUS-R117-001`/`-002`/`-003`/`-004`, `OPUS-R118-005`).

Revision 94 (`OPUS-R118-005`) extends `find_reader_functions`'s registry
with the module's third "read this object's own published content" shape
(`os.open` + `os.fdopen(fd, "rb").read()`, live in
`_read_identity_gap_authorization`) and `_trace_name_to_reader_call` with
a `json.loads(reader(...))` wrapper -- both were false-exclusion gaps in
the completeness/`releasable` arm this script implements, and neither
changes the eight-primitive derived set, since the identity-gap
authorization record independently fails `releasable` (no unlink resolves
to it at all). This script implements the completeness arm only -- the
graph arm (edges, blocking/non-blocking classification, acyclicity) that
revision 94 also restated in `WORKFLOW_V2_PLAN.md` is implemented
separately, in `verify_372h_raw_edge_derivation.py` (added revision 95,
`OPUS-R119-001`), which imports this module's own `PathResolver`/`canon`/
`DECLARED_PRIMITIVES` rather than duplicating them; item 372(h) itself
remains unimplemented -- both scripts are dry-run evidence, not `WF8c`'s
own eventual conformance test.

Not `WF8c`'s own eventual conformance test -- that remains future
implementation scope, per item 372(h)'s own text. This script exists only
to demonstrate, mechanically and reproducibly, that:

1. every `fcntl.flock(LOCK_EX)` and `os.link` acquisition site in
   `scripts/workflow_state.py` is discovered (parameter-default/helper
   indirection, the `fd`-to-path indirection every `fcntl.flock` site
   needs, and interprocedural `FileExistsError` handling all resolved,
   never assumed);
2. each discovered pathname resolves to the literal it locks, by walking
   parameter defaults, same-module helper bodies (any same-module
   function whose return value flows into the target path, not only one
   named `*_path()`/`*_dir()` -- `OPUS-R117-005`), and the anchor
   convention stated in item 372(h)'s own "Pathname resolution" clause
   (`repo_root`/`common_dir`/`git_common_dir`/`worktree_root`, including
   their tuple-unpacked bindings from `_git_identity`'s three-element
   return, elided exactly as the declared primitive list already elides
   them -- `OPUS-R117-003`);
3. each `os.link` site is classified member/excluded by *mechanically
   implementing* the "releasable" conjunct of item 372(h)'s predicate --
   walking the module for the unlink site(s) that target the identical
   resolved pathname and asking whether reaching that unlink is
   control-dependent on a same-object content comparison, never a
   hand-maintained path-keyed table (`OPUS-R117-002`: the prior table's
   answers were provably independent of the code they claimed to verify).
   "Reusable" is not evaluated here -- item 372(h) states why
   (`OPUS-R117-004`);
4. the resulting forward-direction candidate set is exactly the eight
   pathnames Revision 93's "The complete global partial order" declares --
   no more, no fewer -- and two required regression mutations (mutating
   `release_checkpoint`'s release to an unconditional unlink; mutating
   `close_plan_approval_journal` into a genuine compare-and-delete) each
   flip exactly the verdict they should, proving the predicate actually
   reads the code rather than returning a constant answer
   (`OPUS-R117-002`'s own required check).

Run: `python3 docs/ai-workflow/dry-run/verify_372h_lock_primitive_predicate.py`
from the repository root. Exits non-zero on any mismatch.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET = REPO_ROOT / "scripts" / "workflow_state.py"

# ---------------------------------------------------------------------------
# The declared eight-primitive universe (Revision 93 text), for the
# forward-direction comparison. Anchor-stripped, in the same convention
# `PathResolver` produces: a repo_root/common_dir prefix carries no
# information (every primitive is rooted at one or the other) so it is
# dropped on both sides of the comparison, and a per-work-item/per-attempt
# token is normalized to `<token>`.
# ---------------------------------------------------------------------------

DECLARED_PRIMITIVES = {
    "1": ".ai-review/runtime/PLAN_APPROVAL_MUTATION.lease",
    "2": ".ai-review/runtime/WORKFLOW_STATE.lock",
    "3": ".ai-review/runtime/WORKTREE_IDENTITY.lock",
    "4": "ai-workflow/identity-gap.lock",
    "5": "ai-workflow/checkpoint-claims/<token>.lease",
    "6": "ai-workflow/checkpoint-claims/<token>.guardlock",
    "7": ".ai-review/runtime/PLAN_APPROVAL_MUTATION.guardlock",
    "8": "ai-workflow/checkpoint-claims/<token>.json",
}

# ---------------------------------------------------------------------------
# Step 1: locate every fcntl.flock / os.link / *.unlink call site by AST,
# distinguishing flock(..., LOCK_EX) acquisitions from LOCK_UN releases.
# ---------------------------------------------------------------------------


def parse_module(src: str | None = None) -> ast.Module:
    if src is None:
        src = TARGET.read_text()
    return ast.parse(src, filename=str(TARGET))


def enclosing_function(tree: ast.Module, node: ast.AST) -> ast.FunctionDef | None:
    """Innermost `def` containing `node`, by walking every function and
    checking node ranges (stdlib `ast` has no parent pointers)."""
    best = None
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno):
                if best is None or fn.lineno > best.lineno:
                    best = fn
    return best


def find_lock_call_sites(tree: ast.Module) -> tuple[list[ast.Call], list[ast.Call]]:
    flock_ex_sites: list[ast.Call] = []
    link_sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "flock"
                and isinstance(f.value, ast.Name) and f.value.id == "fcntl"):
            # Second positional arg names the mode; LOCK_EX is an
            # acquisition, LOCK_UN a release -- only the former is a
            # lock-order candidate.
            if len(node.args) >= 2:
                mode = node.args[1]
                if (isinstance(mode, ast.Attribute) and mode.attr == "LOCK_EX"):
                    flock_ex_sites.append(node)
        elif (isinstance(f, ast.Attribute) and f.attr == "link"
                and isinstance(f.value, ast.Name) and f.value.id == "os"):
            link_sites.append(node)
        elif isinstance(f, ast.Name) and f.id == "link":
            link_sites.append(node)
    return flock_ex_sites, link_sites


def find_unlink_call_sites(tree: ast.Module) -> list[ast.Call]:
    sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "unlink"):
            sites.append(node)
    return sites


# ---------------------------------------------------------------------------
# Step 2: pathname resolution -- walk the target-path argument expression
# through local assignments, parameter defaults, and same-module helper
# calls, terminating at a module-level constant or a recognized anchor
# (item 372(h), "Pathname resolution": repo_root / common_dir /
# git_common_dir / worktree_root are elided, exactly as the declared
# primitive list already elides them).
# ---------------------------------------------------------------------------


def module_level_path_constants(tree: ast.Module) -> dict[str, ast.expr]:
    """Every module-level ALL_CAPS assignment, not just `*_PATH` --
    `CLAIMS_RELDIR`/`IDENTITY_GAP_LOCK_RELPATH`/etc. are equally load-bearing
    literals a resolution chain bottoms out at."""
    constants: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.isupper():
                constants[name] = node.value
    return constants


def literal_path_string(expr: ast.expr) -> str | None:
    """Render a `Path("literal")` / bare-string expression to its literal
    text, or None if it is not a simple literal (e.g. an f-string with a
    non-literal placeholder)."""
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "Path":
        if expr.args and isinstance(expr.args[0], ast.Constant):
            return str(expr.args[0].value)
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


class PathResolver:
    """Resolves a lock/link/unlink call's target-path argument to the
    literal pathname it locks, by walking parameter defaults and
    same-module helper bodies -- the indirection `OPUS-R116-002` found a
    naive pass missing for 4 of 5 `flock` sites, generalized past the
    `*_path()`/`*_dir()` naming convention per `OPUS-R117-005`."""

    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.functions = {
            fn.name: fn for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.constants = module_level_path_constants(tree)
        self._multi: list[str] = []

    def resolve_call_site_all(self, call: ast.Call, fn: ast.FunctionDef) -> list[str]:
        """One physical `os.link`/`fcntl.flock` call can still resolve to
        *multiple* distinct pathnames when it sits in a shared internal
        helper (`_publish_claim_exclusive`) reached from more than one
        caller passing a different concrete path (`_publish_guard`'s
        `.lease` vs. `_claim_or_refuse`'s `.json`) -- each such caller is
        a distinct real acquisition site sharing one `os.link` call in the
        source, and the discovery pass must report both, not just
        whichever caller a single-valued walk happens to find first."""
        self._multi: list[str] = []
        primary = self.resolve_call_site(call, fn)
        results = [primary] + [r for r in self._multi if r != primary]
        return results

    def resolve_call_site(self, call: ast.Call, fn: ast.FunctionDef) -> str:
        """Resolve the pathname a flock/link call at `call`, lexically
        inside `fn`, locks. Handles both call shapes in this module:
        `os.open(full, ...)` / `fcntl.flock(fd, ...)` where `full`/`path`
        is a local built from a parameter or helper call, and a bare
        `os.link(tmp_name, full_path)` where `full_path` is similarly
        local."""
        target_name = self._locate_target_var(call, fn)
        if target_name is None:
            raise AssertionError(f"cannot locate target-path variable for call at line {call.lineno}")
        return self._resolve_name(target_name, fn, {})

    def resolve_arbitrary_expr(self, expr: ast.expr, fn: ast.FunctionDef) -> str:
        """Public entry point for resolving an arbitrary expression (used
        by the releasable-conjunct's reader-function discovery, not just
        lock/link call sites)."""
        if isinstance(expr, ast.Name):
            return self._resolve_name(expr.id, fn, {})
        return self._resolve_expr(expr, fn)

    def _locate_target_var(self, call: ast.Call, fn: ast.FunctionDef) -> str | None:
        # For fcntl.flock(fd, ...): fd is assigned from os.open(<target>, ...)
        # somewhere earlier in fn -- the pathname is reachable only by way
        # of that `fd`, never as a direct argument of `flock` itself
        # (`OPUS-R117-006`). For os.link(tmp, full_path): full_path (the
        # *second* arg, the final published name) is the target -- the
        # first argument is always the `tempfile.mkstemp` staging name,
        # never the acquisition target (item 372(h), "mkstemp staging").
        f = call.func
        if isinstance(f, ast.Attribute) and f.attr == "flock":
            fd_arg = call.args[0]
            if isinstance(fd_arg, ast.Name):
                return self._var_bound_to_os_open(fd_arg.id, fn)
            return None
        # os.link(src, dst) -- dst is the exclusive/final pathname.
        if len(call.args) >= 2 and isinstance(call.args[1], ast.Name):
            return call.args[1].id
        return None

    def _var_bound_to_os_open(self, fd_var: str, fn: ast.FunctionDef) -> str | None:
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == fd_var
                    and isinstance(node.value, ast.Call)):
                call = node.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == "open":
                    first = call.args[0] if call.args else None
                    if isinstance(first, ast.Name):
                        return first.id
        return None

    def _resolve_name(self, name: str, fn: ast.FunctionDef, param_defaults: dict[str, ast.expr]) -> str:
        # 1. Is `name` a parameter of `fn` with a default? Resolve the default.
        default = self._param_default(name, fn)
        if default is not None:
            return self._resolve_expr(default, fn)
        # 2. Is `name` assigned locally to a call (helper or Path(repo_root/...))?
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
                return self._resolve_expr(node.value, fn)
            # a tuple-unpack target: `a, common_dir, c = _git_identity(repo_root)`.
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Tuple)
                    and any(isinstance(elt, ast.Name) and elt.id == name for elt in node.targets[0].elts)):
                if self._is_anchor(ast.Name(id=name)):
                    return name  # an anchor component of a tuple-unpack: elided, not resolved further
        # 3. Is `name` a *required* parameter (no default)? This module's
        # internal (`_`-prefixed) publish helpers all have exactly one
        # production call site each -- resolve interprocedurally through
        # the actual argument expression the caller passes.
        if self._is_param(name, fn):
            if self._is_anchor(ast.Name(id=name)):
                return name  # an anchor parameter: elided by name, never chased to its caller
            return self._resolve_via_caller(name, fn)
        raise AssertionError(f"cannot resolve local name {name!r} in {fn.name}")

    def _is_param(self, name: str, fn: ast.FunctionDef) -> bool:
        all_params = list(fn.args.args) + list(fn.args.kwonlyargs)
        return any(p.arg == name for p in all_params)

    def _resolve_via_caller(self, name: str, fn: ast.FunctionDef) -> str:
        callers = callers_of(self.tree, fn.name)
        if not callers:
            raise AssertionError(f"required parameter {name!r} of {fn.name} has no discoverable caller")
        param_index = self._param_position(name, fn)
        resolved: list[str] = []
        for caller in callers:
            for node in ast.walk(caller):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == fn.name):
                    arg_expr = self._matching_call_argument(node, name, param_index)
                    if arg_expr is not None:
                        resolved.append(self._resolve_expr(arg_expr, caller))
        if not resolved:
            raise AssertionError(f"could not find {fn.name}({name}=...) argument at any call site")
        for r in resolved[1:]:
            if r not in self._multi:
                self._multi.append(r)
        return resolved[0]

    def _param_position(self, name: str, fn: ast.FunctionDef) -> int | None:
        for i, p in enumerate(fn.args.args):
            if p.arg == name:
                return i
        return None

    def _matching_call_argument(self, call: ast.Call, name: str, position: int | None) -> ast.expr | None:
        for kw in call.keywords:
            if kw.arg == name:
                return kw.value
        if position is not None and position < len(call.args):
            return call.args[position]
        return None

    def _param_default(self, name: str, fn: ast.FunctionDef) -> ast.expr | None:
        args = fn.args
        all_params = list(args.args) + list(args.kwonlyargs)
        all_defaults = ([None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
                         + list(args.kw_defaults))
        for param, dflt in zip(all_params, all_defaults):
            if param.arg == name and dflt is not None:
                return dflt
        return None

    # Item 372(h), "Pathname resolution": the repository-root and
    # git-common-dir anchors are elided, exactly as the declared primitive
    # list's own paths already elide them (`.ai-review/runtime/...` is
    # repo-relative; `.../checkpoint-claims/...` is git-common-dir-relative,
    # written with a leading ellipsis) -- including their tuple-unpacked
    # bindings from `_git_identity`'s three-element return
    # (`repo_root_id, git_common_dir, worktree_root = _git_identity(...)`,
    # `claims_dir`'s own `_, common_dir, _ = _git_identity(repo_root)`).
    ANCHOR_NAMES = {"repo_root", "common_dir", "git_common_dir", "worktree_root",
                     "repo_root_id"}

    def _is_anchor(self, expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in self.ANCHOR_NAMES
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "Path":
            return len(expr.args) == 1 and self._is_anchor(expr.args[0])
        return False

    def _resolve_expr(self, expr: ast.expr, fn: ast.FunctionDef) -> str:
        # <anchor> / <path-ish>: the anchor (repo_root, or Path(common_dir)
        # etc.) is a runtime prefix, not part of the pathname being
        # compared -- descend into the non-anchor operand only. Otherwise
        # (e.g. `claims_dir(repo_root) / f"{token}.lease"`, where the left
        # operand is itself a `*_dir()` helper contributing a real
        # directory component) resolve and join both sides.
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            if self._is_anchor(expr.left):
                return self._resolve_expr(expr.right, fn)
            left = self._resolve_expr(expr.left, fn)
            right = self._resolve_expr(expr.right, fn)
            return f"{left}/{right}"
        # `<expr>.parent` -- resolve the base and drop the final segment.
        if isinstance(expr, ast.Attribute) and expr.attr == "parent":
            base = self._resolve_expr(expr.value, fn)
            return base.rsplit("/", 1)[0] if "/" in base else base
        # A bare Name: either a parameter/local of `fn`, or a module constant.
        if isinstance(expr, ast.Name):
            if self._is_anchor(expr):
                return expr.id
            if expr.id in self.constants:
                return self._render(self.constants[expr.id])
            return self._resolve_name(expr.id, fn, {})
        # A call to a same-module helper function: recurse into its body
        # using the *default* values of ITS OWN parameters (this module
        # never overrides them at any real call site -- verified separately
        # by `_assert_no_override`). Not limited to `*_path()`/`*_dir()` --
        # any same-module function whose return value flows into an
        # acquisition's target path is a real link in the resolution
        # chain, not merely one spelled with a recognized suffix
        # (`OPUS-R117-005`: a differently-named helper must not degrade to
        # an unreadable `ast.dump`).
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            helper = self.functions.get(expr.func.id)
            if helper is not None:
                return self._resolve_helper_return(helper)
        return self._render(expr)

    def _resolve_helper_return(self, helper: ast.FunctionDef) -> str:
        for node in ast.walk(helper):
            if isinstance(node, ast.Return) and node.value is not None:
                return self._resolve_expr(node.value, helper)
        raise AssertionError(f"helper {helper.name} has no return")

    def _render(self, expr: ast.expr) -> str:
        lit = literal_path_string(expr)
        if lit is not None:
            return lit
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            return f"{self._render(expr.left)}/{self._render(expr.right)}"
        if isinstance(expr, ast.JoinedStr):
            parts = []
            for v in expr.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name):
                    parts.append(f"<{v.value.id}>")
                else:
                    parts.append("<expr>")
            return "".join(parts)
        raise AssertionError(
            f"cannot resolve {ast.dump(expr)} to a literal/anchor/constant -- "
            f"undecidable, failing closed"
        )


def resolve_target_expr(expr: ast.expr, fn: ast.FunctionDef, resolver: PathResolver) -> str:
    return resolver.resolve_arbitrary_expr(expr, fn)


def canon(s: str) -> str:
    """Any bracketed placeholder (whatever the source variable was named
    -- `token`, `T`, `gap_oid`) denotes "the resource's own identity
    token" for comparison purposes; only the surrounding reldir/suffix
    carries ordering information."""
    return re.sub(r"<[A-Za-z0-9_]+>", "<token>", s)


# ---------------------------------------------------------------------------
# Step 3: EEXIST attribution -- is a given os.link call guarded by a real
# FileExistsError handler on its own static call chain (own try block, or
# transitively up through callers, to unbounded depth -- `OPUS-R117-002`'s
# secondary gap: the prior script accepted a `depth` parameter and never
# used it, capping the climb at one level)?
# ---------------------------------------------------------------------------


def _is_bare_reraise(handler: ast.ExceptHandler) -> bool:
    """`except FileExistsError: raise` handles nothing -- it is a
    pass-through, not a guard, and must not count as EEXIST attribution
    (the identity-gap-authorization site's own handler, harmless here
    since it doesn't affect membership, but worth narrowing per
    `OPUS-R117-002`)."""
    return (len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise)
            and handler.body[0].exc is None)


def call_is_in_try_except_fileexists(call: ast.Call, fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            in_body = any(
                n is call for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            )
            if in_body and _handles_file_exists(node):
                return True
    return False


def _handles_file_exists(try_node: ast.Try) -> bool:
    """Python dispatches a raised exception to the *first* `except`
    clause whose type matches, in source order -- a later, more capable
    handler is irrelevant to an exception type an earlier clause already
    claims. So this walks handlers in order and stops at the first one
    that would catch `FileExistsError`, and asks only whether *that one*
    genuinely handles it (rather than being a bare re-raise) -- an
    `except OSError:` positioned after `except FileExistsError: raise`
    never sees a `FileExistsError`, and must not count as guarding it."""
    for handler in try_node.handlers:
        t = handler.type
        names: list[str] = []
        if isinstance(t, ast.Tuple):
            names = [n.id for n in t.elts if isinstance(n, ast.Name)]
        elif isinstance(t, ast.Name):
            names = [t.id]
        elif t is None:
            names = ["BaseException"]  # bare `except:` catches everything
        if not ("FileExistsError" in names or "OSError" in names or "BaseException" in names):
            continue
        return not _is_bare_reraise(handler)
    return False


def callers_of(tree: ast.Module, fn_name: str) -> list[ast.FunctionDef]:
    callers = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name != fn_name:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == fn_name:
                    callers.append(fn)
                    break
    return callers


def eexist_guarded(tree: ast.Module, call: ast.Call, fn: ast.FunctionDef,
                    depth: int = 0, _seen: frozenset[str] | None = None) -> bool:
    """Transitively up through the call's own static call chain -- its
    immediately enclosing `try`, or, failing that, up through every
    caller's own enclosing `try`, to unbounded depth (cycle-guarded by
    `_seen`), never capped at one level."""
    if call_is_in_try_except_fileexists(call, fn):
        return True
    seen = (_seen or frozenset()) | {fn.name}
    for caller in callers_of(tree, fn.name):
        if caller.name in seen:
            continue
        for node in ast.walk(caller):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == fn.name:
                if call_is_in_try_except_fileexists(node, caller):
                    return True
        if eexist_guarded(tree, _synthetic_call_marker(caller, fn.name), caller, depth + 1, seen):
            return True
    return False


def _synthetic_call_marker(caller: ast.FunctionDef, callee_name: str) -> ast.Call:
    """A stand-in `ast.Call` node (the actual call to `callee_name` inside
    `caller`) so the recursive step above can re-run
    `call_is_in_try_except_fileexists` against `caller`'s own enclosing
    `try`, one frame further up."""
    for node in ast.walk(caller):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == callee_name:
            return node
    return ast.Call(lineno=caller.lineno, col_offset=0)


# ---------------------------------------------------------------------------
# Step 4: the "releasable" conjunct, implemented mechanically -- item
# 372(h) states "reusable" is a design-time property the conformance test
# does not decide (`OPUS-R117-004`); the sole mechanically-tested conjunct
# is "releasable". A pathname P is releasable if the module defines an
# unlink of P that is control-dependent on a comparison reading an
# identifier out of P's *own* published content -- never a hardcoded
# per-path table.
# ---------------------------------------------------------------------------


def find_reader_functions(tree: ast.Module, resolver: PathResolver) -> dict[str, str]:
    """`function_name -> resolved pathname` for every same-module function
    whose body reads full file bytes from a resolved target path via
    `_read_claim_bytes(<path>)`, `<path-expr>.read_bytes()`, or (added,
    revision 94, `OPUS-R118-005`) `os.open(<path>, ...)` bound to `fd`
    followed by `with os.fdopen(fd, "rb") as h: ... = h.read()` -- the
    three "read this object's own published content" primitives this
    module uses (the third is `_read_identity_gap_authorization`'s own
    shape). Keyed on those three structural I/O primitives, never on a
    specific object's path or name."""
    readers: dict[str, str] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            path_expr = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_read_claim_bytes" and node.args):
                path_expr = node.args[0]
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read_bytes" and not node.args):
                path_expr = node.func.value
            if path_expr is None:
                continue
            try:
                resolved = resolve_target_expr(path_expr, fn, resolver)
            except AssertionError:
                continue
            readers[fn.name] = resolved
            break
        if fn.name in readers:
            continue
        fdopen_resolved = _resolve_fdopen_read_reader(fn, resolver)
        if fdopen_resolved is not None:
            readers[fn.name] = fdopen_resolved
    return readers


def _resolve_fdopen_read_reader(fn: ast.FunctionDef, resolver: PathResolver) -> str | None:
    """Detects `fd = os.open(<path>, os.O_RDONLY...)` followed by
    `with os.fdopen(fd, "rb") as h: ... = h.read()` in `fn`'s own body --
    the module's third "read this object's own published content" shape
    (`_read_identity_gap_authorization`, `OPUS-R118-005`), which the two
    structural checks above (`_read_claim_bytes`/`.read_bytes()`) do not
    recognize."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.With) or len(node.items) != 1:
            continue
        item = node.items[0]
        ctx = item.context_expr
        if not (isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Attribute)
                and ctx.func.attr == "fdopen" and isinstance(ctx.func.value, ast.Name)
                and ctx.func.value.id == "os" and ctx.args):
            continue
        handle_name = item.optional_vars.id if isinstance(item.optional_vars, ast.Name) else None
        if handle_name is None:
            continue
        body_module = ast.Module(body=node.body, type_ignores=[])
        reads_whole_file = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "read" and not n.args
            and isinstance(n.func.value, ast.Name) and n.func.value.id == handle_name
            for n in ast.walk(body_module)
        )
        if not reads_whole_file:
            continue
        fd_expr = ctx.args[0]
        if not isinstance(fd_expr, ast.Name):
            continue
        path_name = resolver._var_bound_to_os_open(fd_expr.id, fn)
        if path_name is None:
            continue
        try:
            return resolver._resolve_name(path_name, fn, {})
        except AssertionError:
            continue
    return None


def _compare_operand_base(operand: ast.expr) -> ast.expr | None:
    """Strip one layer of `.get("field")` / `["field"]` / `.field` off a
    compare operand to reach the base expression that should trace to a
    reader-function call."""
    if isinstance(operand, ast.Call) and isinstance(operand.func, ast.Attribute) and operand.func.attr == "get":
        return operand.func.value
    if isinstance(operand, ast.Subscript):
        return operand.value
    if isinstance(operand, ast.Attribute):
        return operand.value
    return None


def _trace_name_to_reader_call(name: str, fn: ast.FunctionDef, readers: dict[str, str]) -> str | None:
    """Traces `name = reader(path)` directly, or (added, revision 94,
    `OPUS-R118-005`) `name = json.loads(reader(path))` -- a `json.loads`
    wrapper around a registered reader call defeated the one-level trace,
    a false-exclusion gap for any compare-and-delete gated on a
    JSON-decoded read of the object's own content."""
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in readers:
            return value.func.id
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr == "loads" and value.args
                and isinstance(value.args[0], ast.Call) and isinstance(value.args[0].func, ast.Name)
                and value.args[0].func.id in readers):
            return value.args[0].func.id
    return None


def compare_reads_pathname(cmp: ast.expr, fn: ast.FunctionDef, readers: dict[str, str],
                            target_pathname: str) -> bool:
    """Does an `Eq`/`NotEq` comparison have one operand tracing (through
    at most one local variable assignment) to a call to a registered
    reader function whose own resolved pathname is the *same* pathname
    being unlinked? Reading a *different* object's content (e.g. the
    plan-approval journal's own `owner_token`, compared while unlinking
    `PLAN_APPROVAL_JOURNAL.claim.<T>`) does not count -- releasability is
    about the object's own published content, not any nearby record."""
    if not (isinstance(cmp, ast.Compare) and len(cmp.ops) == 1
            and isinstance(cmp.ops[0], (ast.Eq, ast.NotEq))):
        return False
    for operand in (cmp.left, cmp.comparators[0]):
        base = _compare_operand_base(operand)
        if not isinstance(base, ast.Name):
            continue
        reader = _trace_name_to_reader_call(base.id, fn, readers)
        if reader is not None and canon(readers[reader]) == canon(target_pathname):
            return True
    return False


def _bool_op_tests(test: ast.expr) -> list[ast.expr]:
    return list(test.values) if isinstance(test, ast.BoolOp) else [test]


def local_if_gates(unlink_call: ast.Call, fn: ast.FunctionDef, readers: dict[str, str],
                    target_pathname: str) -> bool:
    """Two shapes, both "the unlink is reached only when a same-content
    comparison holds": (a) the unlink is lexically inside the `if`'s own
    true-branch (`_release_plan_approval_guard_locked`'s trailing
    unconditional unlink after `if held is None or held.get("lease_id")
    != lease_id: return` is shape (b); a hypothetical `if <compare>:
    <unlink>` would be shape (a)); (b) an earlier sibling `if <compare>:
    return/raise` gate, whose failure to hold is what lets control reach
    a later, textually-unconditional unlink in the same function."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        tests = _bool_op_tests(node.test)
        matches = any(compare_reads_pathname(t, fn, readers, target_pathname) for t in tests)
        if not matches:
            continue
        contains_unlink = any(n is unlink_call for n in ast.walk(ast.Module(body=node.body, type_ignores=[])))
        if contains_unlink:
            return True
        is_exit_gate = any(isinstance(s, (ast.Return, ast.Raise)) for s in node.body)
        if is_exit_gate and node.lineno < unlink_call.lineno:
            return True
    return False


def find_enclosing_with(tree: ast.Module, call: ast.Call, fn: ast.FunctionDef) -> ast.With | None:
    """Innermost `with` statement in `fn` whose body contains `call`."""
    best = None
    for node in ast.walk(fn):
        if isinstance(node, ast.With):
            if any(n is call for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def with_context_manager_name(with_node: ast.With) -> str | None:
    call = with_node.items[0].context_expr
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
        return call.func.id
    return None


def context_manager_gates(functions: dict[str, ast.FunctionDef], ctx_fn_name: str,
                           readers: dict[str, str], target_pathname: str,
                           depth: int = 0, _seen: frozenset[str] | None = None) -> bool:
    """Does the context-manager function reached by `with CTX(...):` --
    or something IT calls, transitively (a context manager's own body
    routinely delegates the actual comparison to a helper it calls before
    `yield`, as `owner_mutation` does to `assert_claim_owner`) -- contain
    a same-content comparison whose failure raises? Bounded by `_seen`
    against recursion cycles, not by an arbitrary depth cap."""
    fn = functions.get(ctx_fn_name)
    if fn is None:
        return False
    seen = (_seen or frozenset()) | {ctx_fn_name}
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            tests = _bool_op_tests(node.test)
            if any(compare_reads_pathname(t, fn, readers, target_pathname) for t in tests):
                if any(isinstance(s, ast.Raise) for s in node.body):
                    return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in functions and node.func.id not in seen):
            if context_manager_gates(functions, node.func.id, readers, target_pathname, depth + 1, seen):
                return True
    return False


def is_releasable(tree: ast.Module, resolver: PathResolver, functions: dict[str, ast.FunctionDef],
                   readers: dict[str, str], unlink_sites: list[ast.Call],
                   target_pathname: str) -> tuple[bool, str]:
    """Search every `.unlink(...)` call site in the module (not only ones
    "near" the acquisition site -- releasability is a module-wide
    property) for one resolving to `target_pathname` and control-dependent
    on a same-content comparison. Returns `(is_member, evidence)`."""
    matching_sites = []
    for call in unlink_sites:
        fn = enclosing_function(tree, call)
        if fn is None:
            continue
        try:
            resolved = resolver.resolve_call_site(call, fn) if False else None
        except AssertionError:
            resolved = None
        # `.unlink()` targets are resolved the same way any other target
        # expression is: the receiver of the `.unlink` call.
        receiver = call.func.value
        try:
            resolved = resolve_target_expr(receiver, fn, resolver)
        except AssertionError:
            continue
        if canon(resolved) != canon(target_pathname):
            continue
        matching_sites.append((call, fn))

    if not matching_sites:
        return False, "no unlink call in the module resolves to this pathname"

    for call, fn in matching_sites:
        if local_if_gates(call, fn, readers, target_pathname):
            return True, (f"{fn.name}:{call.lineno} unlinks it gated by a local comparison reading "
                           f"this pathname's own content")
        with_node = find_enclosing_with(tree, call, fn)
        if with_node is not None:
            ctx_name = with_context_manager_name(with_node)
            if ctx_name and context_manager_gates(functions, ctx_name, readers, target_pathname):
                return True, (f"{fn.name}:{call.lineno} unlinks it inside `with {ctx_name}(...)`, "
                               f"whose own call chain compares this pathname's own content and "
                               f"raises on mismatch")

    sites_desc = ", ".join(f"{fn.name}:{call.lineno}" for call, fn in matching_sites)
    return False, f"unlinked at {sites_desc}, but reaching the unlink is not gated by a same-content comparison"


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------


def run_pass(tree: ast.Module, *, verbose: bool = True) -> tuple[dict[str, str], list[str]]:
    """One full discovery+resolution+EEXIST+releasable pass over `tree`.
    Returns `(candidate_pathnames, failures)`."""
    flock_sites, link_sites = find_lock_call_sites(tree)
    unlink_sites = find_unlink_call_sites(tree)
    resolver = PathResolver(tree)
    functions = resolver.functions
    readers = find_reader_functions(tree, resolver)

    candidate_pathnames: dict[str, str] = {}
    failures: list[str] = []

    if verbose:
        print(f"Discovered {len(flock_sites)} fcntl.flock(LOCK_EX) sites, "
              f"{len(link_sites)} os.link sites, {len(unlink_sites)} unlink sites, "
              f"{len(readers)} reader functions.\n")
        print("-- fcntl.flock(LOCK_EX) sites --")

    for call in flock_sites:
        fn = enclosing_function(tree, call)
        try:
            resolved = resolver.resolve_call_site(call, fn)
        except AssertionError as exc:
            failures.append(f"line {call.lineno} ({fn.name}): {exc}")
            continue
        if verbose:
            print(f"  line {call.lineno:5d}  {fn.name:30s} -> {resolved}")
        candidate_pathnames[resolved] = "flock"

    if verbose:
        print("\n-- os.link sites (releasable conjunct, mechanically decided) --")

    for call in link_sites:
        fn = enclosing_function(tree, call)
        try:
            resolved_all = resolver.resolve_call_site_all(call, fn)
        except AssertionError as exc:
            failures.append(f"line {call.lineno} ({fn.name}): {exc}")
            continue
        guarded = eexist_guarded(tree, call, fn)
        for resolved_raw in resolved_all:
            is_member, evidence = is_releasable(tree, resolver, functions, readers, unlink_sites, resolved_raw)
            if verbose:
                print(f"  line {call.lineno:5d}  {fn.name:30s} -> {resolved_raw}")
                print(f"                 EEXIST-guarded: {guarded}   releasable: {is_member}")
                print(f"                 evidence: {evidence}")
            if is_member:
                if not guarded:
                    failures.append(
                        f"line {call.lineno} ({fn.name}): resolved to {resolved_raw!r}, releasable, "
                        f"but not EEXIST-guarded by the call-chain attribution rule"
                    )
                candidate_pathnames[resolved_raw] = "link"

    return candidate_pathnames, failures


def compare_to_declared(candidate_pathnames: dict[str, str], *, verbose: bool = True) -> list[str]:
    failures: list[str] = []
    declared_set = set(DECLARED_PRIMITIVES.values())
    discovered_canon = {canon(s) for s in candidate_pathnames}
    declared_canon = {canon(s) for s in declared_set}

    undeclared = discovered_canon - declared_canon
    stale = declared_canon - discovered_canon

    if undeclared:
        failures.append(f"code-discovered pathnames not in the declared list: {sorted(undeclared)}")
    if stale:
        failures.append(f"declared pathnames not discovered live in the code: {sorted(stale)}")

    if verbose:
        print(f"\n-- Forward-direction comparison against the declared {len(DECLARED_PRIMITIVES)}-primitive list --")
        if not failures:
            print(f"  {len(discovered_canon)} discovered == {len(declared_canon)} declared. PASS.")
    return failures


# ---------------------------------------------------------------------------
# Required regression check (`OPUS-R117-002`): the predicate must be shown
# non-vacuous by construction, not merely by inspection -- each of the two
# mutations the external review used to disprove the prior hardcoded table
# must flip exactly the verdict it should when run against *this*
# implementation.
# ---------------------------------------------------------------------------

MUTATION_UNCONDITIONAL_RELEASE = (
    # release_checkpoint: remove the owner_mutation/assert_claim_owner
    # wrapper entirely, so the claim unlink really is the unconditional
    # plain unlink the (now-corrected) exclusion prose used to assert it
    # already was. Expected: checkpoint-claims/<token>.json flips
    # member -> excluded.
    "        with owner_mutation(repo_root, work_item_id, owner_token,\n"
    "                            checkpoint_id=checkpoint_id, step=\"1f-release\",\n"
    "                            step_class=ORDINARY, now=now):\n"
    "            claim_path(repo_root, work_item_id).unlink(missing_ok=True)\n",
    "        claim_path(repo_root, work_item_id).unlink(missing_ok=True)\n",
)

MUTATION_JOURNAL_COMPARE_AND_DELETE = (
    # close_plan_approval_journal: turn the bare unconditional unlink into
    # a genuine compare-and-delete on owner_token, mirroring the exact
    # early-return shape _release_guard_path_locked/
    # _release_plan_approval_guard_locked already use. Expected:
    # PLAN_APPROVAL_JOURNAL.json flips excluded -> member.
    "def close_plan_approval_journal(repo_root: Path, path: Path = PLAN_APPROVAL_JOURNAL_PATH) -> None:\n"
    "    \"\"\"Deletes the journal -- idempotent, an already-absent journal is\n"
    "    success. Callers close only after a transaction has genuinely\n"
    "    reached a durable terminal state (a verified commit, or a verified\n"
    "    rollback); this function performs no such check itself.\"\"\"\n"
    "    plan_approval_journal_path(repo_root, path).unlink(missing_ok=True)\n",
    "def close_plan_approval_journal(repo_root: Path, owner_token: str,\n"
    "                                path: Path = PLAN_APPROVAL_JOURNAL_PATH) -> None:\n"
    "    \"\"\"MUTATION for regression testing only.\"\"\"\n"
    "    current = read_plan_approval_journal(repo_root, path)\n"
    "    if current is None or current.get(\"owner_token\") != owner_token:\n"
    "        return\n"
    "    plan_approval_journal_path(repo_root, path).unlink(missing_ok=True)\n",
)


def member_verdict(candidate_pathnames: dict[str, str], target: str) -> bool:
    return any(canon(k) == canon(target) and v == "link" for k, v in candidate_pathnames.items())


def run_regression_checks(base_src: str) -> list[str]:
    failures: list[str] = []
    target_claim = DECLARED_PRIMITIVES["8"]
    target_journal = ".ai-review/runtime/PLAN_APPROVAL_JOURNAL.json"

    base_tree = parse_module(base_src)
    base_candidates, base_failures = run_pass(base_tree, verbose=False)
    if base_failures:
        return [f"base pass unexpectedly failed: {base_failures}"]
    if not member_verdict(base_candidates, target_claim):
        failures.append("baseline: checkpoint-claims/<token>.json expected member, was not")
    if member_verdict(base_candidates, target_journal):
        failures.append("baseline: PLAN_APPROVAL_JOURNAL.json expected excluded, was member")

    old, new = MUTATION_UNCONDITIONAL_RELEASE
    if old not in base_src:
        failures.append("mutation 1 anchor text not found -- source has drifted, update the mutation")
    else:
        mutated_src = base_src.replace(old, new, 1)
        mutated_tree = parse_module(mutated_src)
        mutated_candidates, _ = run_pass(mutated_tree, verbose=False)
        if member_verdict(mutated_candidates, target_claim):
            failures.append(
                "REGRESSION FAILURE: making release_checkpoint's release unconditional did not "
                "flip checkpoint-claims/<token>.json from member to excluded -- predicate is vacuous"
            )
        else:
            print("regression 1 (unconditional claim release): member -> excluded, as required. PASS.")

    old, new = MUTATION_JOURNAL_COMPARE_AND_DELETE
    if old not in base_src:
        failures.append("mutation 2 anchor text not found -- source has drifted, update the mutation")
    else:
        mutated_src = base_src.replace(old, new, 1)
        mutated_tree = parse_module(mutated_src)
        mutated_candidates, _ = run_pass(mutated_tree, verbose=False)
        if not member_verdict(mutated_candidates, target_journal):
            failures.append(
                "REGRESSION FAILURE: making close_plan_approval_journal a genuine compare-and-delete "
                "did not flip PLAN_APPROVAL_JOURNAL.json from excluded to member -- predicate is vacuous"
            )
        else:
            print("regression 2 (journal compare-and-delete): excluded -> member, as required. PASS.")

    return failures


def main() -> int:
    src = TARGET.read_text()
    tree = parse_module(src)
    candidate_pathnames, failures = run_pass(tree, verbose=True)
    failures += compare_to_declared(candidate_pathnames, verbose=True)

    print("\n-- Required regression check (non-vacuousness) --")
    failures += run_regression_checks(src)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nAll checks passed: discovery+resolution is complete and correct; the "
          "releasable conjunct is decided mechanically, from the unlink/comparison "
          "sites themselves, not a hardcoded table; the forward direction produces "
          "exactly the eight declared pathnames; both required mutations flip "
          "exactly the verdict they should.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
