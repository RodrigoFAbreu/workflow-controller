# state_writer: false
"""The declarative gate policy (workflow-2.8.0, `gate-policy-and-reopening`,
D-GP-Policy): `docs/ai-workflow/GATE_POLICY.json`, its closed schema, the
built-in default, the tighten-only effective policy, the recorded floor, and
the content-and-chain provenance of the adopted policy and the floor that
survives a squash merge.

Stdlib-only, and it never imports `workflow_state` (which imports it): every
Git read here is its own, and `workflow_state` owns the state writers and the
commit validators that call back into this module.

The module reads; it writes nothing. The only functions that return a changed
state are `record_gate_policy_floor`, which takes and returns a dict, and
`gate_lowering_event`/`effective_policy`, which return reports.

Vocabulary used throughout:

- a **policy** is the raw file body (`DEFAULT_POLICY` is one);
- the **resolved** (flat) form is the policy after the master switch and the
  per-gate override are applied and every omitted key took its default;
- **stricter**/**looser** compare resolved forms field by field.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path

import workflow_forge as forge

POLICY_PATH = Path("docs/ai-workflow/GATE_POLICY.json")
STATE_PATH = Path("docs/ai-workflow/WORKFLOW_STATE.json")
SCHEMA_VERSION = 1

GATE_IDS = ("plan_approval", "technical_approval", "acceptance")
REQUIRE_VALUES = frozenset({"distinct_reviewer_models"})
REOPEN_CAUSES = frozenset({"changes_requested", "checks_failed"})
FLOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CONFIRMATION_LITERAL = "gate_policy"
CONFIRMATION_DIGEST_CHARS = 12

ADOPTION_KEY = "gate_policy_adoption"
FLOOR_KEY = "gate_policy_floor"
ADOPTION_TRAILER = "Workflow-Gate-Policy-Adoption"
FLOOR_TRAILER = "Workflow-Gate-Policy-Floor"

#: `source` of an effective policy (D-GP-Policy's table).
SOURCES = (
    "default", "adopted", "file_tightened", "file_loosening_ignored", "floor", "invalid",
    "provenance_failed",
)

_GATE_KEYS = {
    "plan_approval": frozenset({"human", "require"}),
    "technical_approval": frozenset({"human", "require"}),
    "acceptance": frozenset({"human", "require_ci", "requires_pr_approved", "required_flows"}),
}
_TOP_KEYS = frozenset({"schema_version", "human_approval", "gates", "pr_review"})
_PR_REVIEW_KEYS = frozenset({"enabled", "reopen_on"})


class GatePolicyError(Exception):
    """Base of every refusal this module raises."""


class InvalidGatePolicyError(GatePolicyError):
    """A policy (the file, an adopted body, a floor) breaks the closed
    schema. `errors` lists every problem found, in a stable order."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class GatePolicyConfirmationRejectedError(GatePolicyError):
    """`validate_gate_policy_confirmation` refused the text."""


class GatePolicyFieldsChangedError(GatePolicyError):
    """`assert_gate_policy_fields_unchanged_or_tightened` found a commit
    whose adoption or floor change breaks the content or chain rules."""

    def __init__(self, failures: list[dict]):
        self.failures = list(failures)
        super().__init__("; ".join(
            f"{f['field']} at {f['commit']}: {f['reason']}" for f in self.failures))


# ---------------------------------------------------------------------------
# schema, default, resolved form
# ---------------------------------------------------------------------------

#: The built-in default (D-GP-Policy "The built-in default"): switch off, no
#: override, the one non-empty `require` is `distinct_reviewer_models` on the
#: plan and technical gates (`OD-W2-17` (b)).
DEFAULT_POLICY: dict = {
    "schema_version": SCHEMA_VERSION,
    "human_approval": False,
    "gates": {
        "plan_approval": {"require": ["distinct_reviewer_models"]},
        "technical_approval": {"require": ["distinct_reviewer_models"]},
        "acceptance": {"require_ci": True, "requires_pr_approved": False, "required_flows": []},
    },
    "pr_review": {"enabled": True, "reopen_on": ["changes_requested", "checks_failed"]},
}

#: Every resolved leaf: (gate or section, key, kind). `bool`: true is the
#: stricter value. `set`: a larger set is stricter.
FIELDS: tuple[tuple[str, str, str], ...] = (
    ("plan_approval", "human", "bool"),
    ("plan_approval", "require", "set"),
    ("technical_approval", "human", "bool"),
    ("technical_approval", "require", "set"),
    ("acceptance", "human", "bool"),
    ("acceptance", "require_ci", "bool"),
    ("acceptance", "requires_pr_approved", "bool"),
    ("acceptance", "required_flows", "set"),
    ("pr_review", "enabled", "bool"),
    ("pr_review", "reopen_on", "set"),
)


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _string_list(value, label: str, errors: list[str], *, allowed=None, pattern=None) -> None:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return
    seen = set()
    for item in value:
        if not isinstance(item, str):
            errors.append(f"{label} entries must be strings, got {item!r}")
            continue
        if item in seen:
            errors.append(f"{label} lists {item!r} twice")
        seen.add(item)
        if allowed is not None and item not in allowed:
            errors.append(f"{label} entry {item!r} is not one of {sorted(allowed)}")
        if pattern is not None and not pattern.match(item):
            errors.append(f"{label} entry {item!r} does not match {pattern.pattern}")


def policy_errors(policy) -> list[str]:
    """Every way `policy` breaks the closed schema, in a stable order; empty
    when it is valid. An unknown key is refused at every level."""
    errors: list[str] = []
    if not isinstance(policy, dict):
        return [f"the policy must be a JSON object, got {type(policy).__name__}"]
    for key in sorted(set(policy) - _TOP_KEYS):
        errors.append(f"unknown key {key!r}")
    if "schema_version" in policy and (
            not isinstance(policy["schema_version"], int) or isinstance(policy["schema_version"], bool)
            or policy["schema_version"] != SCHEMA_VERSION):
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if "human_approval" in policy and not _is_bool(policy["human_approval"]):
        errors.append("human_approval must be a boolean")
    gates = policy.get("gates", {})
    if not isinstance(gates, dict):
        errors.append("gates must be an object")
    else:
        for gate in sorted(set(gates) - set(GATE_IDS)):
            errors.append(f"unknown gate {gate!r} in gates")
        for gate in GATE_IDS:
            if gate not in gates:
                continue
            body = gates[gate]
            if not isinstance(body, dict):
                errors.append(f"gates.{gate} must be an object")
                continue
            for key in sorted(set(body) - _GATE_KEYS[gate]):
                errors.append(f"unknown key {key!r} in gates.{gate}")
            for key in ("human", "require_ci", "requires_pr_approved"):
                if key in body and key in _GATE_KEYS[gate] and not _is_bool(body[key]):
                    errors.append(f"gates.{gate}.{key} must be a boolean")
            if "require" in body and "require" in _GATE_KEYS[gate]:
                _string_list(body["require"], f"gates.{gate}.require", errors, allowed=REQUIRE_VALUES)
            if "required_flows" in body and "required_flows" in _GATE_KEYS[gate]:
                _string_list(body["required_flows"], f"gates.{gate}.required_flows", errors,
                             pattern=FLOW_ID_RE)
    pr_review = policy.get("pr_review", {})
    if not isinstance(pr_review, dict):
        errors.append("pr_review must be an object")
    else:
        for key in sorted(set(pr_review) - _PR_REVIEW_KEYS):
            errors.append(f"unknown key {key!r} in pr_review")
        if "enabled" in pr_review and not _is_bool(pr_review["enabled"]):
            errors.append("pr_review.enabled must be a boolean")
        if "reopen_on" in pr_review:
            _string_list(pr_review["reopen_on"], "pr_review.reopen_on", errors, allowed=REOPEN_CAUSES)
    return errors


def validate_policy(policy) -> None:
    """Raises `InvalidGatePolicyError` unless `policy` is a valid raw policy."""
    errors = policy_errors(policy)
    if errors:
        raise InvalidGatePolicyError(errors)


def resolve_policy(policy: dict) -> dict:
    """The resolved (flat) form of a valid raw `policy`: every key present,
    lists sorted, each gate's `human` the per-gate override or else the master
    switch. `{"human_approval": true}` still lists the default `require`."""
    validate_policy(policy)
    master = bool(policy.get("human_approval", False))
    gates = policy.get("gates", {})
    default_gates = DEFAULT_POLICY["gates"]
    resolved: dict = {}
    for gate in GATE_IDS:
        body = gates.get(gate, {})
        default = default_gates[gate]
        entry: dict = {"human": bool(body["human"]) if "human" in body else master}
        for key in sorted(_GATE_KEYS[gate] - {"human"}):
            value = body[key] if key in body else default[key]
            entry[key] = sorted(value) if isinstance(value, list) else value
        resolved[gate] = entry
    pr_review = policy.get("pr_review", {})
    resolved["pr_review"] = {
        "enabled": pr_review.get("enabled", DEFAULT_POLICY["pr_review"]["enabled"]),
        "reopen_on": sorted(pr_review.get("reopen_on", DEFAULT_POLICY["pr_review"]["reopen_on"])),
    }
    return resolved


DEFAULT_RESOLVED: dict = resolve_policy(DEFAULT_POLICY)
ALL_HUMAN_POLICY: dict = {"schema_version": SCHEMA_VERSION, "human_approval": True}
ALL_HUMAN_RESOLVED: dict = resolve_policy(ALL_HUMAN_POLICY)


def resolved_errors(resolved) -> list[str]:
    """The shape errors of a resolved (flat) policy, the form the floor stores."""
    errors: list[str] = []
    if not isinstance(resolved, dict):
        return ["the resolved policy must be an object"]
    expected = set(GATE_IDS) | {"pr_review"}
    for key in sorted(set(resolved) ^ expected):
        errors.append(f"resolved policy key {key!r} is unexpected or missing")
    for section, key, kind in FIELDS:
        entry = resolved.get(section)
        if not isinstance(entry, dict) or key not in entry:
            errors.append(f"resolved {section}.{key} is missing")
            continue
        value = entry[key]
        if kind == "bool" and not _is_bool(value):
            errors.append(f"resolved {section}.{key} must be a boolean")
        if kind == "set" and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)
                              or value != sorted(set(value))):
            errors.append(f"resolved {section}.{key} must be a sorted, duplicate-free string list")
    for section in expected & set(resolved):
        entry = resolved[section]
        allowed = {key for sec, key, _ in FIELDS if sec == section}
        if isinstance(entry, dict):
            for key in sorted(set(entry) - allowed):
                errors.append(f"resolved {section} has unknown key {key!r}")
    return errors


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def policy_digest(policy: dict) -> str:
    """The sha256 of the canonical bytes of `policy` (a raw body or a resolved
    form): sorted keys, compact separators, UTF-8."""
    return hashlib.sha256(canonical_bytes(policy)).hexdigest()


# ---------------------------------------------------------------------------
# stricter / looser
# ---------------------------------------------------------------------------


def _field_stricter(kind: str, a, b):
    if kind == "bool":
        return bool(a) or bool(b)
    return sorted(set(a) | set(b))


def _field_looser(kind: str, before, after) -> bool:
    """Whether `after` is looser than `before` for this field."""
    if kind == "bool":
        return bool(before) and not bool(after)
    return bool(set(before) - set(after))


def stricter(a: dict, b: dict) -> dict:
    """The field-wise stricter of two resolved policies."""
    result: dict = {}
    for section, key, kind in FIELDS:
        result.setdefault(section, {})[key] = _field_stricter(kind, a[section][key], b[section][key])
    return result


def loosened_fields(before: dict, after: dict) -> list[str]:
    """The dotted names of the fields where resolved `after` is looser than
    resolved `before` (a gate turned automatic, a `require` or flow entry
    removed, a switch off, a `reopen_on` cause removed), in `FIELDS` order."""
    return [f"{section}.{key}" for section, key, kind in FIELDS
            if _field_looser(kind, before[section][key], after[section][key])]


def tightened_fields(before: dict, after: dict) -> list[str]:
    """The dotted names of the fields where resolved `after` is stricter."""
    return [f"{section}.{key}" for section, key, kind in FIELDS
            if _field_looser(kind, after[section][key], before[section][key])]


# ---------------------------------------------------------------------------
# the confirmation
# ---------------------------------------------------------------------------


def validate_gate_policy_confirmation(text: str, digest: str) -> None:
    """The adoption's user-only guard (`LPR-R1-003`): the text must contain
    the literal `gate_policy` and the first 12 hex characters of the digest
    shown to the user. Work-item-free; neither `validate_user_confirmation`
    nor `APPROVAL_STAGES` is involved."""
    if not isinstance(text, str) or not text.strip():
        raise GatePolicyConfirmationRejectedError(
            f"the confirmation is empty -- it must name {CONFIRMATION_LITERAL!r} and the digest "
            f"prefix {digest[:CONFIRMATION_DIGEST_CHARS]!r}")
    if CONFIRMATION_LITERAL not in text:
        raise GatePolicyConfirmationRejectedError(
            f"the confirmation does not contain the literal {CONFIRMATION_LITERAL!r}: {text!r}")
    if digest[:CONFIRMATION_DIGEST_CHARS] not in text:
        raise GatePolicyConfirmationRejectedError(
            f"the confirmation does not contain the digest prefix "
            f"{digest[:CONFIRMATION_DIGEST_CHARS]!r}: {text!r}")


# ---------------------------------------------------------------------------
# record validators (shape only; content and chain are provenance's)
# ---------------------------------------------------------------------------


def adoption_record_errors(record) -> list[str]:
    """Shape and self-consistency of a `gate_policy_adoption` record:
    `{sha256, adopted_at, confirmation, policy, history, lowered}` (`lowered`
    optional), the sha256
    of the canonical bytes of `policy` equal to `sha256`, and `confirmation`
    passing `validate_gate_policy_confirmation` for that digest."""
    if not isinstance(record, dict):
        return ["gate_policy_adoption must be an object"]
    errors: list[str] = []
    required = {"sha256", "adopted_at", "confirmation", "policy", "history"}
    for key in sorted((set(record) ^ required) - {"lowered"}):
        errors.append(f"gate_policy_adoption key {key!r} is unexpected or missing")
    if errors:
        return errors
    if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
        errors.append("gate_policy_adoption.sha256 must be 64 lowercase hex characters")
    if not isinstance(record["adopted_at"], str) or not record["adopted_at"]:
        errors.append("gate_policy_adoption.adopted_at must be a non-empty string")
    if not isinstance(record["confirmation"], str):
        errors.append("gate_policy_adoption.confirmation must be a string")
    history = record["history"]
    if not isinstance(history, list) or any(
            not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for h in history):
        errors.append("gate_policy_adoption.history must be a list of 64-hex digests")
    # `lowered` is an audit label, never a precondition: a record that omits
    # it is accepted (and a gate-lowering event is still recomputed).
    lowered = record.get("lowered", [])
    names = [f"{section}.{key}" for section, key, _ in FIELDS]
    if (not isinstance(lowered, list) or any(f not in names for f in lowered)
            or lowered != sorted(set(lowered), key=names.index)):
        errors.append(f"gate_policy_adoption.lowered must be distinct fields among {names}, in that order")
    policy_problems = policy_errors(record["policy"])
    if policy_problems:
        errors.extend(f"gate_policy_adoption.policy: {p}" for p in policy_problems)
        return errors
    if errors:
        return errors
    digest = policy_digest(record["policy"])
    if record["sha256"] != digest:
        errors.append(
            f"gate_policy_adoption.sha256 {record['sha256'][:12]} is not the digest of its policy "
            f"({digest[:12]})")
        return errors
    try:
        validate_gate_policy_confirmation(record["confirmation"], digest)
    except GatePolicyConfirmationRejectedError as exc:
        errors.append(f"gate_policy_adoption.confirmation: {exc}")
    return errors


def floor_record_errors(record) -> list[str]:
    """Shape and self-consistency of a `gate_policy_floor` record:
    `{policy, digest, recorded_at, observed}`, `policy` the resolved (flat)
    form and `digest` its digest."""
    if not isinstance(record, dict):
        return ["gate_policy_floor must be an object"]
    required = {"policy", "digest", "recorded_at", "observed"}
    errors = [f"gate_policy_floor key {key!r} is unexpected or missing"
              for key in sorted(set(record) ^ required)]
    if errors:
        return errors
    if not isinstance(record["recorded_at"], str) or not record["recorded_at"]:
        errors.append("gate_policy_floor.recorded_at must be a non-empty string")
    observed = record["observed"]
    if not isinstance(observed, list) or any(not isinstance(o, str) for o in observed):
        errors.append("gate_policy_floor.observed must be a list of digests")
    problems = resolved_errors(record["policy"])
    if problems:
        errors.extend(f"gate_policy_floor.policy: {p}" for p in problems)
        return errors
    if record["digest"] != policy_digest(record["policy"]):
        errors.append("gate_policy_floor.digest is not the digest of its policy")
    return errors


# ---------------------------------------------------------------------------
# Git reads (read-only, cached per repository and object id)
# ---------------------------------------------------------------------------

_BLOB_CACHE: dict[tuple[str, str], bytes] = {}
_GP_CACHE: dict[tuple[str, str], tuple[dict | None, dict | None]] = {}
_HISTORY_CACHE: dict[tuple[str, str], dict] = {}


def clear_caches() -> None:
    """Drops every cache (tests that rebuild a repository call it)."""
    _BLOB_CACHE.clear()
    _GP_CACHE.clear()
    _HISTORY_CACHE.clear()


def _git(repo_root: Path, *args: str, input: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_root, input=input, capture_output=True, check=check)


def _git_text(repo_root: Path, *args: str) -> str:
    return _git(repo_root, *args).stdout.decode("utf-8", "replace").strip()


def head_commit(repo_root: Path, rev: str = "HEAD") -> str | None:
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    return result.stdout.decode().strip() if result.returncode == 0 else None


def _blob_shas(repo_root: Path, commits: list[str], path: Path) -> dict[str, str | None]:
    """The blob id of `path` at each commit (None when absent), one process."""
    if not commits:
        return {}
    lines = "".join(f"{c}:{path.as_posix()}\n" for c in commits).encode()
    out = _git(repo_root, "cat-file", "--batch-check", input=lines).stdout.decode().splitlines()
    result: dict[str, str | None] = {}
    for commit, line in zip(commits, out):
        parts = line.split()
        result[commit] = parts[0] if len(parts) == 3 and parts[1] == "blob" else None
    return result


def _prefetch_blobs(repo_root: Path, shas) -> None:
    """Loads every uncached blob in one `git cat-file --batch` process (a
    long history has thousands of distinct state blobs; one process per blob
    would dominate the cost)."""
    wanted = sorted({sha for sha in shas if sha is not None and (str(repo_root), sha) not in _BLOB_CACHE})
    if not wanted:
        return
    out = _git(repo_root, "cat-file", "--batch", input=("".join(f"{sha}\n" for sha in wanted)).encode()).stdout
    position = 0
    for sha in wanted:
        newline = out.index(b"\n", position)
        header = out[position:newline].split()
        if len(header) != 3 or header[1] != b"blob":
            raise GatePolicyError(f"git cat-file could not read blob {sha}")
        size = int(header[2])
        _BLOB_CACHE[(str(repo_root), sha)] = out[newline + 1:newline + 1 + size]
        position = newline + 1 + size + 1


def _blob(repo_root: Path, sha: str) -> bytes:
    key = (str(repo_root), sha)
    if key not in _BLOB_CACHE:
        _BLOB_CACHE[key] = _git(repo_root, "cat-file", "blob", sha).stdout
    return _BLOB_CACHE[key]


def _parse_gp(raw: bytes) -> tuple[dict | None, dict | None]:
    if b"gate_policy_" not in raw:
        return None, None
    try:
        state = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(state, dict):
        return None, None
    return state.get(ADOPTION_KEY), state.get(FLOOR_KEY)


def _load_gp(repo_root: Path, shas, chunk: int = 256) -> None:
    """Parses (adoption, floor) of every uncached state blob, `chunk` blobs
    per `git cat-file --batch` process, dropping each raw blob once parsed
    (a long history would otherwise hold every state revision in memory)."""
    wanted = sorted({sha for sha in shas if sha is not None and (str(repo_root), sha) not in _GP_CACHE})
    for start in range(0, len(wanted), chunk):
        part = wanted[start:start + chunk]
        _prefetch_blobs(repo_root, part)
        for sha in part:
            _GP_CACHE[(str(repo_root), sha)] = _parse_gp(_BLOB_CACHE.pop((str(repo_root), sha)))


def _gp_fields(repo_root: Path, blob_sha: str | None) -> tuple[dict | None, dict | None]:
    """(adoption, floor) of the state blob; (None, None) when the file is
    absent or unparseable (a corrupt state carries no readable record)."""
    if blob_sha is None:
        return None, None
    _load_gp(repo_root, [blob_sha])
    return _GP_CACHE[(str(repo_root), blob_sha)]


class _History:
    """The first-parent history of one `HEAD`, newest first, with each
    commit's parents and its adoption and floor records. Built once per
    `HEAD` and cached."""

    def __init__(self, repo_root: Path, head: str):
        self.repo_root = repo_root
        self.head = head
        out = _git_text(repo_root, "rev-list", "--first-parent", "--parents", head)
        self.commits: list[str] = []
        self.parents: dict[str, list[str]] = {}
        for line in out.splitlines():
            commit, *parents = line.split()
            self.commits.append(commit)
            self.parents[commit] = parents
        self._state_blobs = _blob_shas(repo_root, self.commits, STATE_PATH)
        self._policy_blobs = _blob_shas(repo_root, self.commits, POLICY_PATH)
        _load_gp(repo_root, self._state_blobs.values())
        _prefetch_blobs(repo_root, self._policy_blobs.values())
        self._extra: dict[str, tuple[dict | None, dict | None]] = {}

    def gp(self, commit: str) -> tuple[dict | None, dict | None]:
        """(adoption, floor) at `commit`: a first-parent commit or any other
        commit (a non-first parent), read on demand."""
        if commit in self._state_blobs:
            return _gp_fields(self.repo_root, self._state_blobs[commit])
        if commit not in self._extra:
            blob = _blob_shas(self.repo_root, [commit], STATE_PATH)[commit]
            self._extra[commit] = _gp_fields(self.repo_root, blob)
        return self._extra[commit]

    def first_parent_gp(self, commit: str) -> tuple[dict | None, dict | None]:
        parents = self.parents.get(commit)
        if parents is None:
            parents = _git_text(self.repo_root, "rev-list", "--parents", "-n1", commit).split()[1:]
        return self.gp(parents[0]) if parents else (None, None)

    def other_parents(self, commit: str) -> list[str]:
        return self.parents[commit][1:]

    def policy_blob(self, commit: str) -> str | None:
        return self._policy_blobs.get(commit)


def _history(repo_root: Path, head: str | None = None) -> _History | None:
    head = head_commit(repo_root, head or "HEAD")
    if head is None:
        return None
    key = (str(repo_root), head)
    if key not in _HISTORY_CACHE:
        _HISTORY_CACHE[key] = {"history": _History(repo_root, head)}
    return _HISTORY_CACHE[key]["history"]


def _adoption_digests_held(history: _History, commit: str) -> set[str]:
    """Every adoption digest a non-first parent of `commit` holds, as its
    current sha256 or in its history (`LPR-R11-003`)."""
    held: set[str] = set()
    for parent in history.other_parents(commit):
        adoption, _ = history.gp(parent)
        if isinstance(adoption, dict):
            if isinstance(adoption.get("sha256"), str):
                held.add(adoption["sha256"])
            if isinstance(adoption.get("history"), list):
                held.update(h for h in adoption["history"] if isinstance(h, str))
    return held


def _introduces_adoption(history: _History, commit: str, adoption: dict | None) -> bool:
    """An adoption-introducing commit (`LPR-R12-001`): it changes the
    adoption against its first parent and the new digest is held by no other
    parent. An update merge that only brings in another parent's adoption is
    not one."""
    if not isinstance(adoption, dict) or not isinstance(adoption.get("sha256"), str):
        return False
    return adoption["sha256"] not in _adoption_digests_held(history, commit)


def _adopted_resolved(adoption: dict | None) -> dict | None:
    """The resolved form of an adoption record's policy, or None when the
    record is absent or its policy is unusable."""
    if not isinstance(adoption, dict) or policy_errors(adoption.get("policy")):
        return None
    return resolve_policy(adoption["policy"])


def _floor_resolved(floor: dict | None) -> dict | None:
    if not isinstance(floor, dict) or resolved_errors(floor.get("policy")):
        return None
    return floor["policy"]


def _base_resolved(adoption: dict | None) -> dict:
    return _adopted_resolved(adoption) or DEFAULT_RESOLVED


def _effective_floor_of(adoption: dict | None, floor: dict | None) -> dict:
    """A parent's or base's floor for the monotonicity bound: its recorded
    floor, else its adopted policy's resolved form, else the default."""
    return _floor_resolved(floor) or _base_resolved(adoption)


# ---------------------------------------------------------------------------
# provenance: the content and chain checks
# ---------------------------------------------------------------------------


def _commit_trailer(repo_root: Path, commit: str, key: str) -> str | None:
    body = _git(repo_root, "log", "-1", "--format=%B", commit).stdout
    parsed = _git(repo_root, "interpret-trailers", "--parse", input=body).stdout.decode()
    for line in parsed.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() == key:
            return value.strip()
    return None


def _check_adoption_change(history: _History, commit: str) -> str | None:
    """The reason the adoption change `commit` made is not valid, or None.
    Valid means self-consistent, chained onto its first parent's adoption
    (its `history` begins with the parent's history then the parent's
    sha256), and, if the commit carries the audit trailer, equal to it."""
    adoption, _ = history.gp(commit)
    parent_adoption, _ = history.first_parent_gp(commit)
    if adoption is None:
        return "the adoption record was removed"
    problems = adoption_record_errors(adoption)
    if problems:
        return "; ".join(problems)
    prefix: list[str] = []
    if isinstance(parent_adoption, dict):
        prefix = list(parent_adoption.get("history", [])) + [parent_adoption.get("sha256")]
    if list(adoption["history"][:len(prefix)]) != prefix:
        return (f"the adoption history does not begin with its parent's chain "
                f"({len(prefix)} earlier digest(s) expected)")
    trailer = _commit_trailer(history.repo_root, commit, ADOPTION_TRAILER)
    if trailer is not None and trailer != adoption["sha256"]:
        return (f"the {ADOPTION_TRAILER} trailer {trailer[:12]} does not equal the adoption's "
                f"sha256 {adoption['sha256'][:12]}")
    return None


def _merge_bases(history: _History, commit: str, other: str) -> list[str]:
    first = history.parents[commit][0] if commit in history.parents else \
        _git_text(history.repo_root, "rev-list", "--parents", "-n1", commit).split()[1]
    result = _git(history.repo_root, "merge-base", "--all", first, other, check=False)
    return result.stdout.decode().split() if result.returncode in (0, 1) else []


def _check_floor_change(history: _History, commit: str) -> str | None:
    """The reason the floor change `commit` made is not valid, or None
    (D-GP-Policy, "Provenance of the adopted policy and the floor")."""
    adoption, floor = history.gp(commit)
    parent_adoption, parent_floor = history.first_parent_gp(commit)
    if floor is None:
        return "the floor record was removed"
    problems = floor_record_errors(floor)
    if problems:
        return "; ".join(problems)
    merged = floor["policy"]
    parents = history.parents.get(commit)
    if parents is None:
        parents = _git_text(history.repo_root, "rev-list", "--parents", "-n1", commit).split()[1:]
    adoption_changed = adoption != parent_adoption
    incoming = None
    for other in parents[1:]:
        bases = _merge_bases(history, commit, other)
        if len(bases) > 1:
            return (f"the merge has more than one merge base ({', '.join(b[:12] for b in bases)}) "
                    f"with {other[:12]}; a criss-cross floor cannot be bound deterministically")
    if adoption_changed and isinstance(adoption, dict):
        holders = [p for p in parents[1:]
                   if isinstance(history.gp(p)[0], dict)
                   and (history.gp(p)[0].get("sha256") == adoption.get("sha256")
                        or adoption.get("sha256") in history.gp(p)[0].get("history", []))]
        if holders:
            incoming = holders[0]
    if incoming is not None:
        # An update merge that brought in another parent's adoption: three-way
        # against the merge base (`LPR-R13-001`).
        bases = _merge_bases(history, commit, incoming)
        base_adoption, base_floor = history.gp(bases[0]) if bases else (None, None)
        base = _effective_floor_of(base_adoption, base_floor)
        first = _effective_floor_of(parent_adoption, parent_floor)
        inc_adoption, inc_floor = history.gp(incoming)
        inc = _effective_floor_of(inc_adoption, inc_floor)
        bound: dict = {}
        for section, key, kind in FIELDS:
            if first[section][key] == base[section][key]:
                value = inc[section][key]
            else:
                value = _field_stricter(kind, first[section][key], inc[section][key])
            bound.setdefault(section, {})[key] = value
    elif adoption_changed and isinstance(adoption, dict) and _check_adoption_change(history, commit) is None:
        # The reset: the commit introduces and validly records an adoption, so
        # the floor is bound by the adopted policy's resolved form.
        bound = _base_resolved(adoption)
    else:
        bound = _effective_floor_of(parent_adoption, parent_floor)
    loosened = loosened_fields(bound, merged)
    if loosened:
        return f"the floor is looser than its bound in {', '.join(loosened)}"
    return None


def _range_for_floor(history: _History) -> list[str]:
    """First-parent commits from `HEAD` back to and including the newest
    adoption-introducing commit, or to the root when there is none."""
    result: list[str] = []
    for commit in history.commits:
        result.append(commit)
        adoption, _ = history.gp(commit)
        parent_adoption, _ = history.first_parent_gp(commit)
        if adoption != parent_adoption and _introduces_adoption(history, commit, adoption):
            break
    return result


def verify_gate_policy_provenance(repo_root: Path, head: str | None = None) -> dict:
    """Verifies the adopted policy and the floor at `HEAD` (never the working
    tree) by content and by the chain of their changes, never by a commit
    message (`LPR-R9-001`). Returns `{ok, failures, range}` where each failure is
    `{field, commit, reason}` and `range` the first-parent commits the floor
    was checked over. Cached per `HEAD`."""
    repo_root = Path(repo_root)
    history = _history(repo_root, head)
    if history is None:
        return {"ok": True, "failures": [], "range": []}
    cached = _HISTORY_CACHE[(str(repo_root), history.head)].get("provenance")
    if cached is not None:
        return cached
    failures: list[dict] = []
    # The adoption: the newest first-parent change, a removal included.
    for commit in history.commits:
        adoption, _ = history.gp(commit)
        parent_adoption, _ = history.first_parent_gp(commit)
        if adoption != parent_adoption:
            reason = _check_adoption_change(history, commit)
            if reason is not None:
                failures.append({"field": ADOPTION_KEY, "commit": commit, "reason": reason})
            break
    # The floor: every first-parent change since the newest introducing commit.
    bounded = _range_for_floor(history)
    for commit in bounded:
        _, floor = history.gp(commit)
        _, parent_floor = history.first_parent_gp(commit)
        if floor != parent_floor:
            reason = _check_floor_change(history, commit)
            if reason is not None:
                failures.append({"field": FLOOR_KEY, "commit": commit, "reason": reason})
    result = {"ok": not failures, "failures": failures, "range": bounded}
    _HISTORY_CACHE[(str(repo_root), history.head)]["provenance"] = result
    return result


def assert_gate_policy_fields_unchanged_or_tightened(repo_root: Path, commit: str) -> None:
    """The two content rules applied to one commit (the unvalidated
    whole-file state commits call it after they land): an adoption change
    must be valid (self-consistent, chained, trailer-consistent) and a floor
    change must be no looser than its bound. Raises
    `GatePolicyFieldsChangedError` naming each failure."""
    repo_root = Path(repo_root)
    sha = head_commit(repo_root, commit)
    if sha is None:
        raise GatePolicyError(f"{commit!r} names no commit")
    history = _history(repo_root, sha)
    adoption, floor = history.gp(sha)
    parent_adoption, parent_floor = history.first_parent_gp(sha)
    failures: list[dict] = []
    if adoption != parent_adoption:
        reason = _check_adoption_change(history, sha)
        if reason is not None:
            failures.append({"field": ADOPTION_KEY, "commit": sha, "reason": reason})
    if floor != parent_floor:
        reason = _check_floor_change(history, sha)
        if reason is not None:
            failures.append({"field": FLOOR_KEY, "commit": sha, "reason": reason})
    if failures:
        raise GatePolicyFieldsChangedError(failures)


def adoption_lowering(adoption_before: dict | None, floor_before: dict | None, policy: dict) -> list[str]:
    """The fields adopting raw `policy` lowers: `loosened_fields` of the
    stricter of the policy in effect before it (the previous adoption or the
    default, and the recorded floor) against the adopted policy's resolved
    form. The one definition the adoption's `lowered` label and
    `gate_lowering_event`'s recomputation share."""
    base = _base_resolved(adoption_before)
    before = stricter(base, _floor_resolved(floor_before) or base)
    return loosened_fields(before, resolve_policy(policy))


def gate_lowering_event(repo_root: Path, state: dict | None = None, head: str | None = None) -> dict | None:
    """The gate-lowering event of the adoption now in effect, recomputed from
    history and never read from the record's `lowered` label (D-GP-Policy,
    `LPR-R23-002`). The newest first-parent commit whose adoption differs
    from its first parent's is compared with that first parent: the lowering
    is `loosened_fields` of the stricter of the policy before it (the previous
    adoption or the default, and the recorded floor) against the adopted
    policy. Returns `{sha256, adopted_at, lowered, commit, label_lowered,
    label_agrees}` for a non-empty lowering, else None."""
    history = _history(Path(repo_root), head)
    if history is None:
        return None
    for commit in history.commits:
        adoption, _ = history.gp(commit)
        parent_adoption, parent_floor = history.first_parent_gp(commit)
        if adoption == parent_adoption:
            continue
        if _adopted_resolved(adoption) is None:
            return None
        lowered = adoption_lowering(parent_adoption, parent_floor, adoption["policy"])
        if not lowered:
            return None
        label = list(adoption["lowered"]) if isinstance(adoption.get("lowered"), list) else None
        return {
            "sha256": adoption["sha256"], "adopted_at": adoption.get("adopted_at"),
            "lowered": lowered, "commit": commit, "label_lowered": label,
            "label_agrees": label == lowered,
        }
    return None


# ---------------------------------------------------------------------------
# the file, the floor and the effective policy
# ---------------------------------------------------------------------------


def read_policy_file(repo_root: Path) -> dict:
    """The working-tree `GATE_POLICY.json`: `{present, valid, errors, sha256,
    policy, resolved}`. Never raises: an unreadable, non-JSON or invalid
    file is `valid: false` with its errors."""
    path = Path(repo_root) / POLICY_PATH
    if not path.is_symlink() and not path.exists():
        return {"present": False, "valid": False, "errors": [], "sha256": None,
                "policy": None, "resolved": None}
    if path.is_symlink() or not path.is_file():
        return {"present": True, "valid": False, "errors": [f"{POLICY_PATH} is not a regular file"],
                "sha256": None, "policy": None, "resolved": None}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"present": True, "valid": False, "errors": [f"{POLICY_PATH} is unreadable: {exc}"],
                "sha256": None, "policy": None, "resolved": None}
    return _parse_policy_bytes(raw)


def working_file_differs_from_head(repo_root: Path) -> bool:
    """True when the working-tree `GATE_POLICY.json` is not byte-identical to
    `HEAD`'s (absent on exactly one side, or different bytes). An adoption
    commit stages only the state file, so a policy file that is not yet
    committed would be adopted without ever reaching `HEAD`'s tree."""
    path = Path(repo_root) / POLICY_PATH
    working = path.is_symlink() or path.exists()
    committed = None
    if head_commit(repo_root) is not None:
        shown = _git(repo_root, "rev-parse", "--verify", "--quiet", f"HEAD:{POLICY_PATH.as_posix()}", check=False)
        committed = shown.stdout.decode().strip() if shown.returncode == 0 else None
    if not working or committed is None:
        return working != (committed is not None)
    if path.is_symlink() or not path.is_file():
        return True
    return _git(repo_root, "hash-object", "--", POLICY_PATH.as_posix()).stdout.decode().strip() != committed


def _parse_policy_bytes(raw: bytes) -> dict:
    try:
        policy = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"present": True, "valid": False, "errors": [f"{POLICY_PATH} is not valid JSON: {exc}"],
                "sha256": hashlib.sha256(raw).hexdigest(), "policy": None, "resolved": None}
    errors = policy_errors(policy)
    if errors:
        return {"present": True, "valid": False, "errors": errors,
                "sha256": policy_digest(policy) if isinstance(policy, (dict, list)) else
                hashlib.sha256(raw).hexdigest(), "policy": None, "resolved": None}
    return {"present": True, "valid": True, "errors": [], "sha256": policy_digest(policy),
            "policy": policy, "resolved": resolve_policy(policy)}


def observed_file_versions(repo_root: Path, history: _History | None, *,
                           include_working: bool = True) -> list[dict]:
    """Every valid version of `GATE_POLICY.json` the Workflow can see: the
    working-tree file, the file at `HEAD`, and every version in a first-parent
    commit since the newest adoption-introducing commit (the same lower bound
    the floor range uses), deduplicated by digest. `include_working=False`
    leaves out the working-tree file and `HEAD`'s copy of it (the adoption's
    "before"). Each entry is `{sha256, resolved}`."""
    versions: dict[str, dict] = {}
    working = read_policy_file(repo_root)
    if include_working and working["valid"]:
        versions[working["sha256"]] = {"sha256": working["sha256"], "resolved": working["resolved"]}
    if history is not None:
        blob_shas: list[str] = []
        # Without the working-tree file the adoption's "before" also leaves out
        # the file `HEAD` holds (an adoption needs it committed, so it is the
        # file being adopted, not part of the policy in effect without it).
        skipped = None if include_working else history.policy_blob(history.head)
        for commit in _range_for_floor(history):
            sha = history.policy_blob(commit)
            if sha is not None and sha != skipped and sha not in blob_shas:
                blob_shas.append(sha)
        for sha in blob_shas:
            parsed = _parse_policy_bytes(_blob(repo_root, sha))
            if parsed["valid"]:
                versions.setdefault(parsed["sha256"],
                                    {"sha256": parsed["sha256"], "resolved": parsed["resolved"]})
    return list(versions.values())


def _committed_gp(history: _History | None) -> tuple[dict | None, dict | None]:
    return history.gp(history.head) if history is not None else (None, None)


def _virtual_floor(repo_root: Path, state: dict | None, history: _History | None,
                   base: dict, *, include_working: bool = True) -> tuple[dict | None, list[str]]:
    """The floor `/satisfy-gate` evaluates against and `next-action` computes:
    the field-wise stricter of the committed floor, a tighter working-tree
    floor, `base` and every observed file version. Returns `(resolved or
    None, observed digests)`; None when it is no stricter than `base` and no
    floor record exists (nothing to record)."""
    _, committed = _committed_gp(history)
    floor = _floor_resolved(committed)
    candidates: list[dict] = []
    working = (state or {}).get(FLOOR_KEY)
    if _floor_resolved(working) is not None and not floor_record_errors(working):
        candidates.append(working["policy"])
    observed = observed_file_versions(repo_root, history, include_working=include_working)
    result = floor if floor is not None else base
    for candidate in candidates:
        result = stricter(result, candidate)
    for version in observed:
        result = stricter(result, version["resolved"])
    if floor is None and result == base and not candidates:
        return None, []
    return result, sorted(v["sha256"] for v in observed)


def effective_policy(repo_root: Path, state: dict | None = None, *, ignore_file: bool = False) -> dict:
    """The policy in effect (D-GP-Policy's table). Never raises on a bad
    file. `ignore_file` computes it as if the working-tree file were absent
    (the adoption's "before"). Returns `{policy, source, digest, base, file, floor, ignored_fields,
    provenance, gate_lowering}`; `policy` is the resolved form."""
    repo_root = Path(repo_root)
    history = _history(repo_root)
    file_info = read_policy_file(repo_root)
    if ignore_file:
        file_info = {"present": False, "valid": False, "errors": [], "sha256": None,
                     "policy": None, "resolved": None}
    provenance = verify_gate_policy_provenance(repo_root)
    lowering = gate_lowering_event(repo_root, state) if provenance["ok"] else None
    adoption, committed_floor = _committed_gp(history)
    base = _base_resolved(adoption) if provenance["ok"] else DEFAULT_RESOLVED
    base_source = "adopted" if provenance["ok"] and _adopted_resolved(adoption) is not None else "default"
    report = {
        "file": {k: file_info[k] for k in ("present", "valid", "errors", "sha256")},
        "provenance": provenance,
        "gate_lowering": lowering,
        "ignored_fields": [],
    }
    if not provenance["ok"]:
        resolved = copy.deepcopy(ALL_HUMAN_RESOLVED)
        report.update({
            "policy": resolved, "source": "provenance_failed", "digest": policy_digest(resolved),
            "base": {"source": "default", "sha256": None},
            "floor": {"present": False, "digest": None, "pending": False},
        })
        return report
    floor, observed = _virtual_floor(repo_root, state, history, base, include_working=not ignore_file)
    effective = stricter(base, floor) if floor is not None else copy.deepcopy(base)
    floor_stricter = floor is not None and bool(tightened_fields(base, floor))
    ignored: list[str] = []
    if file_info["present"] and not file_info["valid"]:
        source = "invalid"
    elif file_info["valid"]:
        effective = stricter(effective, file_info["resolved"])
        ignored = loosened_fields(effective, file_info["resolved"])
        if file_info["resolved"] == base and not floor_stricter:
            source = base_source
        elif ignored:
            source = "file_loosening_ignored"
        else:
            source = "file_tightened"
    elif floor_stricter:
        source = "floor"
    else:
        source = base_source
    committed_floor_resolved = _floor_resolved(committed_floor)
    report.update({
        "policy": effective, "source": source, "digest": policy_digest(effective),
        "base": {"source": base_source,
                 "sha256": adoption["sha256"] if base_source == "adopted" else None},
        "floor": {"present": floor is not None,
                  "digest": policy_digest(floor) if floor is not None else None,
                  "pending": floor is not None and floor != committed_floor_resolved,
                  "observed": observed},
        "ignored_fields": ignored,
    })
    return report


def record_gate_policy_floor(repo_root: Path, state: dict, now: str) -> dict:
    """The one writer of the top-level `gate_policy_floor` (a ratchet, B1):
    returns `state` with the floor set to the field-wise stricter of its
    current value and of every file version the Workflow can see, or `state`
    itself (the same object) when nothing observed is stricter than the
    current floor and the base. It never loosens. The caller commits the
    change in its own state-only commit (`workflow_state.commit_gate_policy_floor`).
    A failed provenance records nothing."""
    repo_root = Path(repo_root)
    history = _history(repo_root)
    if not verify_gate_policy_provenance(repo_root)["ok"]:
        return state
    adoption, committed = _committed_gp(history)
    base = _base_resolved(adoption)
    floor, observed = _virtual_floor(repo_root, state, history, base)
    if floor is None:
        return state
    if _floor_resolved(state.get(FLOOR_KEY)) == floor:
        return state
    new_state = copy.deepcopy(state)
    if _floor_resolved(committed) == floor:
        # A working-tree edit removed or loosened the committed floor: the
        # committed one stands.
        new_state[FLOOR_KEY] = copy.deepcopy(committed)
    else:
        new_state[FLOOR_KEY] = {
            "policy": floor, "digest": policy_digest(floor), "recorded_at": now, "observed": observed,
        }
    return new_state


_LEDGER_KEYS = ("plan_review_stages", "implementation_review_stages")


def unreferenced_runs(state) -> list[str]:
    """`<work item>/<stage>` of every review-stage ledger entry that carries
    the audit keys (it was recorded while its gate was automatic) but a null
    `run_ref` (`D-GP-Trust`: declared, never verified, and an absent one is
    shown as a warning)."""
    found: list[str] = []
    for work_item_id, work_item in sorted(((state or {}).get("work_items") or {}).items()):
        if not isinstance(work_item, dict):
            continue
        for key in _LEDGER_KEYS:
            ledger = work_item.get(key)
            if not isinstance(ledger, dict):
                continue
            for stage, entry in sorted(ledger.items()):
                if isinstance(entry, dict) and "verdict_sha256" in entry and entry.get("run_ref") is None:
                    found.append(f"{work_item_id}/{stage}")
    return found


def verify_check(repo_root: Path, state: dict | None = None) -> tuple[str, str]:
    """`verify`'s advisory `gate_policy` check: `(status, detail)`. `pass`
    with no file and nothing stricter recorded, or a file equal to the adopted
    (or default) policy; `warn` for an unadopted differing file, an ignored
    loosening, a floor not yet recorded in a commit, a floor holding a setting
    the file no longer carries, a gate-lowering event and a record whose
    `lowered` label disagrees with the recomputation and a review verdict
    recorded with audit keys but no `run_ref`; `fail` for an invalid
    file and a failed provenance. Advisory: the caller never lets it change
    the overall status."""
    effective = effective_policy(repo_root, state)
    fails: list[str] = []
    warns: list[str] = []
    provenance = effective["provenance"]
    for failure in provenance["failures"]:
        fails.append(
            f"the {failure['field']} fails provenance at {failure['commit'][:12]}: "
            f"{failure['reason']}; every gate is human until a new /adopt-gate-policy commit "
            f"restores a normal state")
    file_info = effective["file"]
    if file_info["present"] and not file_info["valid"]:
        fails.append(f"{POLICY_PATH} is invalid ({'; '.join(file_info['errors'])}); the adopted policy "
                     f"and the recorded floor stay in effect")
    source = effective["source"]
    if source == "file_tightened":
        warns.append(f"{POLICY_PATH} ({(file_info['sha256'] or '')[:12]}) is not adopted and tightens "
                     f"the policy; it takes effect now and stays in effect once observed")
    elif source == "file_loosening_ignored":
        warns.append(f"{POLICY_PATH} loosens {', '.join(effective['ignored_fields'])}; the loosening "
                     f"is ignored (only /adopt-gate-policy loosens)")
    elif source == "floor":
        warns.append(f"the recorded floor holds a setting {POLICY_PATH} no longer carries; it stays in "
                     f"effect (only /adopt-gate-policy lowers it)")
    if effective["floor"].get("pending"):
        warns.append("a stricter setting is observed but not yet recorded in a committed floor "
                     "(a /satisfy-gate run records it, or adopt it)")
    lowering = effective["gate_lowering"]
    if lowering is not None:
        warns.append(
            f"gate-lowering event: the adoption {lowering['sha256'][:12]} (commit "
            f"{lowering['commit'][:12]}) lowers {', '.join(lowering['lowered'])}")
        if not lowering["label_agrees"]:
            warns.append(
                f"the adoption's recorded lowered label {lowering['label_lowered']!r} disagrees with "
                f"the recomputed lowering {lowering['lowered']!r}")
    unreferenced = unreferenced_runs(state)
    if unreferenced:
        warns.append(
            f"{len(unreferenced)} review verdict(s) were recorded without a run_ref, so the producing "
            f"run cannot be traced ({', '.join(unreferenced[:5])}{', ...' if len(unreferenced) > 5 else ''})")
    if fails:
        return "fail", "; ".join(fails + warns)
    if warns:
        return "warn", "; ".join(warns)
    if source == "adopted":
        return "pass", f"the adopted policy {effective['base']['sha256'][:12]} is in effect"
    return "pass", "no gate policy file; the default applies" if not file_info["present"] \
        else "the file equals the default policy"


# ---------------------------------------------------------------------------
# evaluate_gate's skeleton
# ---------------------------------------------------------------------------

#: D-GP-Gates' governing-version table: the gate is human whatever the policy
#: says where it has no ledger to read.
_ALWAYS_HUMAN = {
    "plan_approval": frozenset({"1"}),
    "technical_approval": frozenset({"1", "2.1"}),
    "acceptance": frozenset(),
}


def gate_mode(effective: dict, gate_id: str, governing_version: str | None) -> str:
    """`human` or `automatic` for `gate_id` under `effective` and the work
    item's governing version."""
    if gate_id not in GATE_IDS:
        raise GatePolicyError(f"unknown gate {gate_id!r}; expected one of {list(GATE_IDS)}")
    if governing_version in _ALWAYS_HUMAN[gate_id]:
        return "human"
    return "human" if effective["policy"][gate_id]["human"] else "automatic"


_FAMILY_SPLIT_RE = re.compile(r"[/: ]")
STAGE_GATE = {"plan": "plan_approval", "implementation": "technical_approval"}


def reviewer_family(value) -> str | None:
    """The family of a declared `Reviewer model:` (`D-GP-Gates`): the value
    lowercased and trimmed, up to the first `/`, `:` or space. A family is
    meant to be a vendor-level identity (`anthropic/...` against `openai/...`);
    two models of one vendor are one family. The values are declared and
    unverified (`OD-W2-3`). `None` for an absent or empty value."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return _FAMILY_SPLIT_RE.split(normalized, maxsplit=1)[0] or None


def distinct_reviewer_models(local_entry, manual_entry) -> tuple[bool, str]:
    """`(met, detail)` for the `distinct_reviewer_models` requirement: both
    ledger stages record a `reviewer_model` and the two families differ."""
    local = reviewer_family((local_entry or {}).get("reviewer_model"))
    manual = reviewer_family((manual_entry or {}).get("reviewer_model"))
    if local is None or manual is None:
        missing = [name for name, family in (("local", local), ("manual", manual)) if family is None]
        return False, (
            f"the {' and '.join(missing)} stage records no reviewer_model; both stages must declare "
            f"a `Reviewer model:` of a different family")
    if local == manual:
        return False, f"both stages declare the family {local!r}; they must differ"
    return True, f"families {local!r} and {manual!r} differ"


def ledger_entry_sha256(entry) -> str | None:
    """The sha256 of a ledger entry's canonical bytes, or `None` for an absent one."""
    if entry is None:
        return None
    return hashlib.sha256(canonical_bytes(entry)).hexdigest()


def _review_gate_inputs(repo_root: Path, state: dict, work_item_id: str, gate_id: str) -> dict:
    """What a plan or technical gate reads (the wrapper, the latest verdict,
    the two ledger stages). Read-only; the imports are lazy because
    `workflow_state` imports this module."""
    import workflow_fingerprint as fingerprint
    import workflow_state

    work_item = state["work_items"][work_item_id]
    plan_stage = gate_id == "plan_approval"
    status = (workflow_state.plan_approval_gate_status if plan_stage
              else workflow_state.technical_approval_gate_status)(repo_root, state, work_item_id)
    feedback = workflow_state.read_review_feedback(repo_root, work_item_id)
    fields = fingerprint.parse_review_feedback_binding_fields(feedback) if feedback is not None else {}
    bundle_id = status["inputs"].get("bundle_id")
    if bundle_id is None and plan_stage:
        try:
            bundle_rel = fingerprint.resolve_bundle_dir(repo_root, work_item_id, stage="plan")
            bundle_id, _ = fingerprint.compute_bundle_id(Path(repo_root) / bundle_rel)
        except Exception:  # an unverifiable bundle is simply an unmet binding
            bundle_id = None
    if plan_stage:
        stages = workflow_state.normalize_plan_review_stages(work_item.get("plan_review_stages") or {})
        local = stages.get(workflow_state.LOCAL_MODEL_PLAN_REVIEW)
        manual = stages.get(workflow_state.MANUAL_EXTERNAL_PLAN_REVIEW)
    else:
        stages = workflow_state.normalize_implementation_review_stages(
            work_item.get("implementation_review_stages") or {})
        local = stages.get(workflow_state.LOCAL_MODEL_IMPLEMENTATION_REVIEW)
        manual = stages.get(workflow_state.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW)
    return {
        "status": status, "fields": fields, "bundle_id": bundle_id, "local": local, "manual": manual,
        "review_content_id": status["inputs"].get("current_review_content_id"),
    }


def _review_gate_requirements(inputs: dict, require: list[str]) -> list[dict]:
    status, fields = inputs["status"], inputs["fields"]
    requirements = [{
        "id": "gate_reachable", "met": bool(status["reachable"]),
        "detail": "the approval gate is reachable" if status["reachable"]
        else f"the approval gate is not reachable: {status['cause']}",
    }]
    verdict = fields.get("status")
    bound = (verdict == "APPROVE" and inputs["bundle_id"] is not None
             and fields.get("reviewed_bundle_id") == inputs["bundle_id"])
    requirements.append({
        "id": "verdict_approve_bound", "met": bound,
        "detail": ("the latest verdict is APPROVE and names the current bundle" if bound else
                   f"the latest verdict is {verdict!r} naming bundle {fields.get('reviewed_bundle_id')!r}; "
                   f"APPROVE naming the current bundle {inputs['bundle_id']!r} is required"),
    })
    for name in require:
        if name == "distinct_reviewer_models":
            met, detail = distinct_reviewer_models(inputs["local"], inputs["manual"])
            requirements.append({"id": name, "met": met, "detail": detail})
    unaudited = [label for label, entry in (("local", inputs["local"]), ("manual", inputs["manual"]))
                 if entry is None or not entry.get("verdict_sha256")]
    requirements.append({
        "id": "review_evidence_audited", "met": not unaudited,
        "detail": ("both stages carry the audit keys" if not unaudited else
                   f"the {' and '.join(unaudited)} stage was recorded without verdict_sha256 (while its gate "
                   f"was human); turn that gate human with \"human_approval\": true, or withdraw and "
                   f"re-review"),
    })
    return requirements


def evaluate_gate(repo_root: Path, state: dict, work_item_id: str, gate_id: str) -> dict:
    """`{gate, mode, source, policy_digest, satisfiable, obtainable,
    requirements, ...}`; writes nothing (`D-GP-Gates`). A gate in `human` mode
    is `satisfiable: false` with the single requirement `human_gate`.
    `plan_approval` and `technical_approval`, in `automatic` mode, are
    satisfiable only when the wrapper is reachable, the latest verdict is an
    `APPROVE` naming the current bundle (so a `USER_OVERRIDE` is never
    automatic), every policy `require` entry is met and both ledger stages
    carry the audit keys (`review_evidence_audited`). The result also holds
    `evidence` (the inputs a satisfying record binds) and `digests`.
    `acceptance` is evaluated by D-GP-Acceptance (`_evaluate_acceptance`)."""
    work_item = (state.get("work_items") or {}).get(work_item_id)
    if not isinstance(work_item, dict):
        raise GatePolicyError(f"{work_item_id!r} names no work item")
    effective = effective_policy(repo_root, state)
    mode = gate_mode(effective, gate_id, work_item.get("governing_workflow_version"))
    result = {
        "gate": gate_id, "mode": mode, "source": effective["source"],
        "policy_digest": effective["digest"], "satisfiable": False, "obtainable": [],
        "digests": {"file": effective["file"]["sha256"], "adopted": effective["base"]["sha256"],
                    "floor": effective["floor"]["digest"]},
    }
    if mode == "human":
        result["requirements"] = [{"id": "human_gate", "met": False,
                                   "detail": f"{gate_id} is a human gate; a person decides it"}]
        return result
    if gate_id == "acceptance":
        result.update(_evaluate_acceptance(repo_root, state, work_item_id, work_item, effective))
        return result
    inputs = _review_gate_inputs(repo_root, state, work_item_id, gate_id)
    requirements = _review_gate_requirements(inputs, effective["policy"][gate_id]["require"])
    result["requirements"] = requirements
    result["satisfiable"] = all(r["met"] for r in requirements)
    result["bundle_id"] = inputs["bundle_id"]
    result["evidence"] = {
        "review_content_id": inputs["review_content_id"], "bundle_id": inputs["bundle_id"],
        "gate_status_digest": hashlib.sha256(canonical_bytes(
            {"reachable": inputs["status"]["reachable"], "cause": inputs["status"]["cause"]})).hexdigest(),
        "ledger": {"local": inputs["local"], "manual": inputs["manual"]},
    }
    return result


# ---------------------------------------------------------------------------
# workflow-2.8.0 CP3 (`D-GP-Evidence`, `D-GP-Invalidation`, `D-GP-Trust`):
# functional evidence, pull-request facts with forge provenance, the anchor and
# a fact's position against it, the cause table, the one query trigger and the
# stale-evidence table. Every function here is pure over the `state` it is
# given and returns a new one; `workflow_state` wraps them in
# `state_transaction`. Nothing here reopens an item (that is CP5's
# `reopen_work_item`): a stored fact is only recorded and judged.
# ---------------------------------------------------------------------------

GATE_EVIDENCE_KEY = "gate_evidence"
GATE_EVIDENCE_KEYS = frozenset({"functional", "pr", "pr_reported", "pr_keys"})
PR_KEYS_FIELDS = frozenset({"reopened_for", "applied", "ingest_seq"})
SOURCE_WORKFLOW_GH = "workflow_gh"
SOURCE_ORCHESTRATOR_FORGE = "orchestrator_forge"
CAUSES = ("content_changed", "changes_requested", "checks_failed")
#: The phases at which a stored fact can be acted on (`D-GP-Reopen`).
REOPENABLE_PHASES = frozenset({"AWAITING_FUNCTIONAL_REVIEW", "MILESTONE_COMPLETE"})


def is_retired_legacy_item(work_item: object) -> bool:
    """A legacy item retired by `/retire-legacy-work-item` (workflow-2.9.0,
    INV-6): `MILESTONE_COMPLETE` at governing version `1` on a `LEGACY_V1`
    technical approval. A structural, enduring predicate, never the absence of
    gate evidence. A promotion sets the version to `2.1`, so an item promoted and
    then accepted never matches, and no other writer produces the combination.
    A retired item is never reopened (`reopen_retired_legacy_item`)."""
    if not isinstance(work_item, dict):
        return False
    approval = work_item.get("technical_approval")
    return (work_item.get("phase") == "MILESTONE_COMPLETE"
            and work_item.get("governing_workflow_version") == "1"
            and isinstance(approval, dict) and approval.get("basis") == "LEGACY_V1")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FUNCTIONAL_STATUSES = ("passed", "failed")
_FUNCTIONAL_FIELDS = ("flow_id", "status", "head", "ran_at", "summary", "log_digest", "run_ref", "reporter")
_FORGE_BLOCK_FIELDS = ("source", "query", "repository", "queried_commit", "fetched_at", "fetched_by", "raw",
                       "raw_sha256")
_FACT_FIELDS = ("pr", "state", "head", "review_decision", "reviewed_head", "checks", "review_id", "findings")


class EvidenceRefusedError(GatePolicyError):
    """An ingest the Workflow refuses, naming a stable `code`; nothing was written."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def empty_gate_evidence() -> dict:
    return {"functional": {}, "pr": None, "pr_reported": None,
            "pr_keys": {"reopened_for": [], "applied": [], "ingest_seq": 0}}


def gate_evidence_of(work_item: dict) -> dict:
    """A deep copy of the item's `gate_evidence`, every key present."""
    stored = work_item.get(GATE_EVIDENCE_KEY) or {}
    evidence = empty_gate_evidence()
    for key in GATE_EVIDENCE_KEYS & set(stored):
        evidence[key] = copy.deepcopy(stored[key])
    return evidence


def _fact_errors(label: str, fact, source: str) -> list[str]:
    if fact is None:
        return []
    if not isinstance(fact, dict):
        return [f"{label} must be an object or null"]
    errors: list[str] = []
    expected = set(_FACT_FIELDS) | {"identity_at_head", "observed_at", "fact_id", "ingest_seq", "provenance",
                                    "repository", "queried_commit"}
    for key in sorted(set(fact) ^ expected):
        errors.append(f"{label} key {key!r} is unexpected or missing")
    if errors:
        return errors
    if fact["state"] not in ("open", "closed", "merged", "none"):
        errors.append(f"{label}.state is {fact['state']!r}")
    if not isinstance(fact["ingest_seq"], int) or isinstance(fact["ingest_seq"], bool) or fact["ingest_seq"] < 1:
        errors.append(f"{label}.ingest_seq must be a positive integer")
    provenance = fact["provenance"]
    if not isinstance(provenance, dict) or provenance.get("source") != source:
        errors.append(f"{label}.provenance.source must be {source!r}")
    elif source == SOURCE_WORKFLOW_GH:
        for key in ("gh_path", "gh_sha256"):
            if not provenance.get(key):
                errors.append(f"{label}.provenance.{key} is required for a workflow_gh fact")
        if provenance.get("gh_sha256") and not _SHA256_RE.match(str(provenance["gh_sha256"])):
            errors.append(f"{label}.provenance.gh_sha256 must be 64 hex")
    return errors


def gate_evidence_errors(evidence) -> list[str]:
    """Shape errors of an item's `gate_evidence`; an unknown key is rejected."""
    if not isinstance(evidence, dict):
        return ["gate_evidence must be an object"]
    errors = [f"gate_evidence key {key!r} is unknown" for key in sorted(set(evidence) - GATE_EVIDENCE_KEYS)]
    functional = evidence.get("functional", {})
    if not isinstance(functional, dict):
        errors.append("gate_evidence.functional must be an object")
    else:
        for flow_id, record in sorted(functional.items()):
            if not isinstance(record, dict) or record.get("flow_id") != flow_id or \
                    not set(_FUNCTIONAL_FIELDS) <= set(record) or record.get("status") not in _FUNCTIONAL_STATUSES:
                errors.append(f"gate_evidence.functional[{flow_id!r}] is malformed")
    errors += _fact_errors("gate_evidence.pr", evidence.get("pr"), SOURCE_WORKFLOW_GH)
    errors += _fact_errors("gate_evidence.pr_reported", evidence.get("pr_reported"), SOURCE_ORCHESTRATOR_FORGE)
    keys = evidence.get("pr_keys", empty_gate_evidence()["pr_keys"])
    if not isinstance(keys, dict) or set(keys) != PR_KEYS_FIELDS:
        errors.append(f"gate_evidence.pr_keys must have exactly {sorted(PR_KEYS_FIELDS)}")
    else:
        for name in ("reopened_for", "applied"):
            if not isinstance(keys[name], list) or any(not isinstance(k, str) for k in keys[name]):
                errors.append(f"gate_evidence.pr_keys.{name} must be a list of strings")
        if not isinstance(keys["ingest_seq"], int) or isinstance(keys["ingest_seq"], bool) or keys["ingest_seq"] < 0:
            errors.append("gate_evidence.pr_keys.ingest_seq must be a non-negative integer")
        else:
            seqs = [f["ingest_seq"] for f in (evidence.get("pr"), evidence.get("pr_reported"))
                    if isinstance(f, dict) and isinstance(f.get("ingest_seq"), int)]
            if seqs and keys["ingest_seq"] < max(seqs):
                errors.append("gate_evidence.pr_keys.ingest_seq is behind a stored fact's ingest_seq")
    return errors


def key_string(*parts) -> str:
    """The semantic key of a cause, canonical and storable."""
    return json.dumps(list(parts), separators=(",", ":"), ensure_ascii=False)


# -- identity, anchor and position -------------------------------------------

def identity_at(repo_root: Path, work_item_id: str, work_item: dict, head: str) -> str:
    """The implementation-stage `review_content_id` at `head`, recomputed by
    the Workflow (never read from a reporter)."""
    import workflow_fingerprint as fingerprint
    import workflow_state

    return workflow_state.approval_review_content_id(
        Path(repo_root), stage="implementation", base_commit=work_item["base_commit"],
        work_item_type=work_item["work_item_type"], work_item_id=work_item_id, head=head,
        artifacts_path=fingerprint.artifacts_path_for_work_item(work_item_id))


def anchor_of(repo_root: Path, work_item_id: str, work_item: dict) -> dict | None:
    """`{commit, identity}`: the technical approval's `reviewed_content_commit`
    and `approved_review_content_id`; with no technical approval, the
    `reviewed_implementation_head` and the identity computed there; `None`
    before either exists (`D-GP-Invalidation`, `LPR-R1-002`)."""
    approval = work_item.get("technical_approval")
    if isinstance(approval, dict) and approval.get("reviewed_content_commit") \
            and approval.get("approved_review_content_id"):
        return {"commit": approval["reviewed_content_commit"], "identity": approval["approved_review_content_id"]}
    commit = work_item.get("reviewed_implementation_head")
    if commit:
        return {"commit": commit, "identity": identity_at(repo_root, work_item_id, work_item, commit)}
    return None


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return _git(Path(repo_root), "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def position_of(repo_root: Path, work_item_id: str, work_item: dict, head: str,
                *, identity_at_head: str | None = None, anchor: dict | None = None) -> str | None:
    """`equal` (the identity at `head` is the anchor's), `ahead` (it differs
    and `head` is not an ancestor of the anchor commit) or `behind` (it
    differs and `head` is an ancestor); `None` while there is no anchor."""
    anchor = anchor or anchor_of(repo_root, work_item_id, work_item)
    if anchor is None:
        return None
    identity = identity_at_head or identity_at(repo_root, work_item_id, work_item, head)
    if identity == anchor["identity"]:
        return "equal"
    return "behind" if is_ancestor(repo_root, head, anchor["commit"]) else "ahead"


def current_at_anchor(repo_root: Path, work_item_id: str, work_item: dict, head: str,
                      *, identity_at_head: str | None = None, anchor: dict | None = None) -> bool:
    return position_of(repo_root, work_item_id, work_item, head,
                       identity_at_head=identity_at_head, anchor=anchor) == "equal"


def _require_head(repo_root: Path, head, prefix: str) -> str:
    if not isinstance(head, str) or not _COMMIT_RE.match(head) or head_commit(Path(repo_root), head) != head:
        raise EvidenceRefusedError(f"{prefix}_head_unknown", f"head {head!r} does not resolve in this repository")
    if not is_ancestor(repo_root, head, "HEAD"):
        raise EvidenceRefusedError(
            f"{prefix}_head_not_in_branch",
            f"head {head} is not contained in the local HEAD; fetch and merge it first")
    return head


def _work_item(state: dict, work_item_id: str) -> dict:
    work_item = (state.get("work_items") or {}).get(work_item_id)
    if not isinstance(work_item, dict):
        raise GatePolicyError(f"{work_item_id!r} names no work item")
    return work_item


def _with_evidence(state: dict, work_item_id: str, evidence: dict) -> dict:
    new_state = copy.deepcopy(state)
    new_state["work_items"][work_item_id][GATE_EVIDENCE_KEY] = evidence
    return new_state


def _next_seq(evidence: dict) -> int:
    evidence["pr_keys"]["ingest_seq"] += 1
    return evidence["pr_keys"]["ingest_seq"]


# -- functional evidence ------------------------------------------------------

def ingest_functional_evidence(repo_root: Path, state: dict, work_item_id: str, record, *, now: str) -> tuple[dict, dict]:
    """Records one flow's result (`D-GP-Evidence`): the identity is recomputed
    at `head` here, never read from the reporter; the latest record per flow id
    wins. Returns `(new_state, {flow_id, identity, stored})`. Refuses with an
    `EvidenceRefusedError` and writes nothing."""
    work_item = _work_item(state, work_item_id)
    if not isinstance(record, dict) or set(record) != set(_FUNCTIONAL_FIELDS):
        raise EvidenceRefusedError("evidence_malformed", f"a functional record has exactly {list(_FUNCTIONAL_FIELDS)}")
    if not isinstance(record["flow_id"], str) or not FLOW_ID_RE.match(record["flow_id"]):
        raise EvidenceRefusedError("evidence_malformed", f"flow_id {record['flow_id']!r} is not a valid flow id")
    if record["status"] not in _FUNCTIONAL_STATUSES:
        raise EvidenceRefusedError("evidence_malformed", f"status must be one of {list(_FUNCTIONAL_STATUSES)}")
    if not isinstance(record["log_digest"], str) or not _SHA256_RE.match(record["log_digest"]):
        raise EvidenceRefusedError("evidence_malformed", "log_digest must be 64 hex")
    if not isinstance(record["summary"], dict) or not isinstance(record["reporter"], str):
        raise EvidenceRefusedError("evidence_malformed", "summary must be an object and reporter a string")
    head = _require_head(repo_root, record["head"], "evidence")
    stored = {key: copy.deepcopy(record[key]) for key in _FUNCTIONAL_FIELDS}
    stored["identity"] = identity_at(repo_root, work_item_id, work_item, head)
    stored["recorded_at"] = now
    evidence = gate_evidence_of(work_item)
    evidence["functional"][stored["flow_id"]] = stored
    return _with_evidence(state, work_item_id, evidence), {
        "flow_id": stored["flow_id"], "identity": stored["identity"], "stored": stored}


def functional_record_current(repo_root: Path, work_item_id: str, work_item: dict, record: dict,
                              *, anchor: dict | None = None) -> bool:
    """A record is current when it passed and the identity at its `head` is the anchor's."""
    anchor = anchor or anchor_of(repo_root, work_item_id, work_item)
    return record.get("status") == "passed" and anchor is not None and record.get("identity") == anchor["identity"]


# -- pull-request facts -------------------------------------------------------

def _build_fact(repo_root: Path, work_item_id: str, work_item: dict, derived: dict, *, source: str,
                provenance: dict, repository: str, queried_commit: str, now: str, seq: int) -> dict:
    fact = copy.deepcopy(derived)
    head = fact["head"]
    fact["identity_at_head"] = identity_at(repo_root, work_item_id, work_item, head) if head else None
    fact.update({"observed_at": now, "ingest_seq": seq, "repository": repository,
                 "queried_commit": queried_commit, "provenance": provenance})
    digest_input = {k: v for k, v in fact.items() if k not in ("observed_at", "ingest_seq")}
    fact["fact_id"] = hashlib.sha256(canonical_bytes(digest_input)).hexdigest()
    return fact


def _check_fact_head(repo_root: Path, derived: dict) -> None:
    if derived["head"] is not None:
        _require_head(repo_root, derived["head"], "pr")


def ingest_pr_facts(repo_root: Path, state: dict, work_item_id: str, payload, *, now: str,
                    repository: str | None = None) -> tuple[dict, dict]:
    """An orchestrator's reported fact (`source: orchestrator_forge`,
    tighten-only). Requires the `forge` block, checks the digest and the
    repository, parses `raw` itself and stores the derived fact in
    `gate_evidence.pr_reported` with the next `ingest_seq`. It never touches
    `pr` or `pr_keys.reopened_for/applied`, reopens nothing, and arms at most
    the query trigger. Refuses with a `forge` error or `EvidenceRefusedError`
    and writes nothing."""
    work_item = _work_item(state, work_item_id)
    block = payload.get("forge") if isinstance(payload, dict) else None
    if not isinstance(block, dict) or set(_FORGE_BLOCK_FIELDS) - set(block) or block.get("source") != "github":
        raise forge.ForgeProvenanceRequiredError(
            "a pr_review_result is accepted only with the forge provenance block of the fixed query")
    if not isinstance(block["raw"], str) or hashlib.sha256(block["raw"].encode("utf-8")).hexdigest() != block["raw_sha256"]:
        raise forge.ForgeDigestMismatchError("sha256(raw) does not equal raw_sha256")
    repository = repository or forge.origin_repository(Path(repo_root))
    if block["repository"] != repository:
        raise forge.ForgeRepositoryMismatchError(f"the fact names {block['repository']!r}, this repository is {repository!r}")
    derived = forge.parse_forge_raw(block["raw"], repository, block["queried_commit"])
    _check_fact_head(repo_root, derived)
    evidence = gate_evidence_of(work_item)
    fetched_by = block["fetched_by"]
    provenance = {"source": SOURCE_ORCHESTRATOR_FORGE, "fetched_by": copy.deepcopy(fetched_by),
                  "fetched_at": block["fetched_at"], "raw_sha256": block["raw_sha256"],
                  "run_ref": payload.get("run_ref")}
    fact = _build_fact(repo_root, work_item_id, work_item, derived, source=SOURCE_ORCHESTRATOR_FORGE,
                       provenance=provenance, repository=repository, queried_commit=block["queried_commit"],
                       now=now, seq=_next_seq(evidence))
    evidence["pr_reported"] = fact
    return _with_evidence(state, work_item_id, evidence), {"slot": "pr_reported", "fact": fact}


def store_workflow_pr_fact(repo_root: Path, state: dict, work_item_id: str, query: dict, *, now: str,
                           run_ref: str | None = None) -> tuple[dict, dict]:
    """Stores the Workflow's own query result (`query_forge_pr_facts`'s dict)
    as the `gate_evidence.pr` fact, provenance `workflow_gh` with the `gh`
    path and sha256 that answered. A query that found no pull request stores
    `state: none`. Replacing `pr` never touches `pr_keys.reopened_for` or
    `applied`. It does not reopen: reopening is `reopen_work_item`'s."""
    work_item = _work_item(state, work_item_id)
    derived = query["facts"]
    _check_fact_head(repo_root, derived)
    evidence = gate_evidence_of(work_item)
    provenance = {"source": SOURCE_WORKFLOW_GH, "fetched_by": {"name": "workflow-gh", "version": forge.FORGE_QUERY_NAME},
                  "fetched_at": now, "raw_sha256": query["raw_sha256"], "run_ref": run_ref,
                  "gh_path": query["gh_path"], "gh_sha256": query["gh_sha256"]}
    fact = _build_fact(repo_root, work_item_id, work_item, derived, source=SOURCE_WORKFLOW_GH,
                       provenance=provenance, repository=query["repository"],
                       queried_commit=query["queried_commit"], now=now, seq=_next_seq(evidence))
    evidence["pr"] = fact
    return _with_evidence(state, work_item_id, evidence), {"slot": "pr", "fact": fact}


# -- the cause table ----------------------------------------------------------

def cause_key(cause: str, fact: dict) -> str | None:
    """The semantic key of `cause` for `fact` (`D-GP-Invalidation`'s cause
    table); deterministic and independent of `observed_at` and `findings`."""
    number = (fact.get("pr") or {}).get("number")
    if number is None:
        return None
    if cause == "content_changed":
        return key_string(number, cause, fact["head"], fact.get("identity_at_head"))
    if cause == "changes_requested":
        return key_string(number, cause, fact["head"], fact["reviewed_head"], fact["review_id"])
    if cause == "checks_failed":
        return key_string(number, cause, fact["head"], sorted(fact["checks"]["failing"]))
    raise GatePolicyError(f"unknown cause {cause!r}")


def evidential_causes(position: str | None, fact: dict) -> list[str]:
    """The causes a fact evidences at `position` (the "actionable when" column
    only). A changed identity is `content_changed` alone; the decision and the
    checks on a non-current head are not acted on."""
    if position is None or fact.get("state") == "none":
        return []
    if position == "ahead":
        return ["content_changed"]
    if position != "equal":
        return []
    causes = []
    if fact["review_decision"] == "CHANGES_REQUESTED" and fact["reviewed_head"] == fact["head"]:
        causes.append("changes_requested")
    if fact["checks"]["state"] == "failure":
        causes.append("checks_failed")
    return causes


def pr_key_actionable(policy: dict, fact: dict | None, cause: str) -> bool:
    """The one shared predicate (`LPR-R23-001`): `policy` is the resolved
    effective policy. True only when `pr_review.enabled`, the cause reopens
    under the policy (`content_changed` whenever enabled; the others only
    when listed in `reopen_on`), the fact is the Workflow's own
    (`workflow_gh`) and its `state` is `open`. It reads no reported fact."""
    if not isinstance(fact, dict) or (fact.get("provenance") or {}).get("source") != SOURCE_WORKFLOW_GH:
        return False
    section = policy["pr_review"]
    if not section["enabled"] or fact.get("state") != "open":
        return False
    return cause == "content_changed" or (cause in section["reopen_on"] and cause in REOPEN_CAUSES)


def actionable_pr_keys(repo_root: Path, state: dict, work_item_id: str, policy: dict) -> list[dict]:
    """`[{cause, key}]` of the stored `workflow_gh` fact that are evidentially
    actionable, policy-actionable and not in `applied`. Re-evaluated on the
    current policy and anchor on every call."""
    work_item = _work_item(state, work_item_id)
    evidence = gate_evidence_of(work_item)
    fact = evidence["pr"]
    if fact is None or fact.get("state") != "open":
        return []
    position = position_of(repo_root, work_item_id, work_item, fact["head"], identity_at_head=fact["identity_at_head"])
    result = []
    for cause in evidential_causes(position, fact):
        key = cause_key(cause, fact)
        if key is not None and key not in evidence["pr_keys"]["applied"] and pr_key_actionable(policy, fact, cause):
            result.append({"cause": cause, "key": key})
    return result


def reopen_candidate(repo_root: Path, state: dict, work_item_id: str, policy: dict) -> dict | None:
    """The first actionable, unapplied `{cause, key}` of the stored
    `workflow_gh` fact that has not yet caused a reopening entry (not in
    `reopened_for`), or `None` (`D-GP-Reopen`, `D-GP-Invalidation`). The one
    selector the store-time reopen and `/apply-pr-review`'s first step share,
    so neither restates the cause table."""
    reopened = gate_evidence_of(_work_item(state, work_item_id))["pr_keys"]["reopened_for"]
    for candidate in actionable_pr_keys(Path(repo_root), state, work_item_id, policy):
        if candidate["key"] not in reopened:
            return candidate
    return None


def _fact_signature(fact: dict) -> tuple:
    return ((fact.get("pr") or {}).get("number"), fact["state"], fact["head"], fact["review_decision"],
            fact["reviewed_head"], fact["checks"]["state"], tuple(fact["checks"]["failing"]), fact["review_id"])


def pr_query_trigger(state: dict, work_item_id: str, policy: dict) -> bool:
    """The single predicate for "run the Workflow's own query first"
    (`D-GP-Trust` principle 3, `LPR-R29-001`): `pr_review.enabled`, the
    `pr_reported` slot holds a fact whose `ingest_seq` is greater than the `pr`
    slot's (or `pr` is empty) and that differs from it in PR number, state,
    head, decision, reviewed head, checks or review id. Evaluated on the two
    slots as wholes. A reported fact never decides: it only arms this."""
    if not policy["pr_review"]["enabled"]:
        return False
    evidence = gate_evidence_of(_work_item(state, work_item_id))
    reported, stored = evidence["pr_reported"], evidence["pr"]
    if reported is None:
        return False
    if stored is None:
        return True
    return reported["ingest_seq"] > stored["ingest_seq"] and _fact_signature(reported) != _fact_signature(stored)


# -- the stale-evidence table --------------------------------------------------

#: One row per trigger of `D-GP-Invalidation`'s table, first match wins. `stays`
#: and `stale` name the evidence kinds; `effect` is the row's effect.
INVALIDATION_RULES: tuple[dict, ...] = (
    {"id": "merged", "stays": ("plan_approval", "technical_approval", "functional"), "stale": (),
     "effect": "recorded; a reopen is refused (pr_merged)"},
    {"id": "closed", "stays": ("plan_approval", "technical_approval", "functional"), "stale": (),
     "effect": "recorded; requires_pr_approved blocks (pr_closed)"},
    {"id": "ahead", "stays": ("plan_approval",), "stale": ("functional", "pr_facts"),
     "effect": "cause content_changed; technical review and full functional validation repeat"},
    {"id": "behind", "stays": ("plan_approval", "technical_approval", "functional"), "stale": ("pr_fact",),
     "effect": "recorded only; not actionable; automatic acceptance blocked until a fact for the current head"},
    {"id": "changes_requested", "stays": ("plan_approval", "technical_approval", "functional"), "stale": (),
     "effect": "cause changes_requested"},
    {"id": "checks_failed", "stays": ("plan_approval", "technical_approval", "functional"), "stale": (),
     "effect": "cause checks_failed"},
    {"id": "approved", "stays": ("plan_approval", "technical_approval", "functional"), "stale": (),
     "effect": "recorded; satisfies pr_approved when the head is current"},
    {"id": "head_changed_same_identity", "stays": ("plan_approval", "technical_approval", "functional"),
     "stale": ("pr_facts",), "effect": "none; new facts are awaited"},
)


def apply_invalidation(repo_root: Path, state: dict, work_item_id: str, fact: dict | None,
                       *, previous: dict | None = None) -> dict:
    """Classifies `fact` against the table: `{rule, stays, stale, effect,
    causes, position}`. Pure; it stales nothing (the technical approval is
    never staled at ingest, `/apply-pr-review` stales it before it edits). A
    reopened PR (`open` after a stored `closed`) is handled as a new fact."""
    work_item = _work_item(state, work_item_id)
    if fact is None or fact.get("state") == "none":
        return {"rule": None, "stays": (), "stale": (), "effect": "no pull request", "causes": [], "position": None}
    position = position_of(repo_root, work_item_id, work_item, fact["head"], identity_at_head=fact["identity_at_head"])
    causes = evidential_causes(position, fact)
    if fact["state"] == "merged":
        rule = "merged"
    elif fact["state"] == "closed":
        rule = "closed"
    elif position == "ahead":
        rule = "ahead"
    elif position == "behind":
        rule = "behind"
    elif "changes_requested" in causes:
        rule = "changes_requested"
    elif "checks_failed" in causes:
        rule = "checks_failed"
    elif (fact["review_decision"] == "APPROVED" and fact["reviewed_head"] == fact["head"]
          and fact["checks"]["state"] in ("pending", "success")):
        rule = "approved"
    else:
        rule = "head_changed_same_identity"
    row = next(r for r in INVALIDATION_RULES if r["id"] == rule)
    reopened_pr = bool(previous and previous.get("state") == "closed" and fact["state"] == "open")
    return {"rule": rule, "stays": row["stays"], "stale": row["stale"],
            "effect": row["effect"] + ("; a reopened PR is a new fact" if reopened_pr else ""),
            "causes": causes, "position": position}


# ---------------------------------------------------------------------------
# workflow-2.8.0 CP4 (`D-GP-Acceptance`): automatic milestone acceptance. The
# evaluation reads the state and the repository only; it never runs `gh`. The
# pull-request requirements read the stored `workflow_gh` fact, or are
# *pending the query* (`pending_query`) when there is none or a reported fact
# is newer; the act (`workflow_state.satisfy_acceptance_gate`) runs the query
# inside its transaction and re-evaluates. Every identity is recomputed here
# from the recorded `head`; a stored identity is never trusted (INV-3).
# ---------------------------------------------------------------------------

ACCEPTANCE_REQUIREMENT_IDS = (
    "checkpoints_complete", "technical_approval_current", "functional_flows_passed", "pr_fact_current",
    "no_standing_pr_objection", "ci_green", "pr_approved",
)
#: The evidence kinds a reporter can supply (`obtainable`).
OBTAINABLE_FUNCTIONAL = "functional_evidence"
OBTAINABLE_PR = "pr_review_result"
ACCEPTANCE_TRUST = {"review_verdicts": "orchestrator", "functional_evidence": "orchestrator",
                    "pr_fact": SOURCE_WORKFLOW_GH}


class AcceptanceEvidenceNotCurrentError(GatePolicyError):
    """`assert_acceptance_evidence_current`: the acceptance gate is not (or no longer) satisfiable."""


def _req(requirement_id: str, met: bool, detail: str) -> dict:
    return {"id": requirement_id, "met": bool(met), "detail": detail}


def _acceptance_registry_requirement(repo_root: Path, work_item: dict) -> dict:
    import workflow_state

    try:
        terminal, outstanding = workflow_state.resolve_own_registry_completion_status(Path(repo_root), work_item)
    except Exception as exc:  # an unreadable registry is an unmet requirement, never a pass
        return _req("checkpoints_complete", False, f"the item's own registry cannot be resolved: {exc}")
    if terminal:
        return _req("checkpoints_complete", True, "every checkpoint of the item's own registry is COMPLETE")
    return _req("checkpoints_complete", False, f"checkpoint {outstanding!r} is not COMPLETE")


def _acceptance_functional(repo_root: Path, work_item_id: str, work_item: dict, anchor: dict | None,
                           required_flows: list[str]) -> tuple[dict, bool, dict]:
    """`(requirement, obtainable, inputs)` for `functional_flows_passed`. A
    record counts only when the identity recomputed at its `head` is the
    anchor's (a stored `identity` is never read)."""
    records = gate_evidence_of(work_item)["functional"]
    if anchor is None:
        return _req("functional_flows_passed", False, "there is no anchor commit (no technical approval)"), False, {}
    current: dict[str, dict] = {}
    stale: list[str] = []
    for flow_id, record in sorted(records.items()):
        try:
            identity = identity_at(repo_root, work_item_id, work_item, record["head"])
        except Exception:
            identity = None
        if identity == anchor["identity"]:
            current[flow_id] = record
        else:
            stale.append(flow_id)
    failed = sorted(flow_id for flow_id, record in current.items() if record["status"] != "passed")
    missing = sorted(set(required_flows) - set(current))
    inputs = {flow_id: {"evidence_id": hashlib.sha256(canonical_bytes(record)).hexdigest(),
                        "log_digest": record["log_digest"], "run_ref": record["run_ref"]}
              for flow_id, record in current.items() if record["status"] == "passed"}
    if failed:
        return _req("functional_flows_passed", False, f"flow(s) {failed} did not pass at the approved content"), False, inputs
    if not current:
        detail = ("no functional flow is recorded" if not records else
                  f"every recorded flow {stale} is stale (recorded at other protected content)")
        return _req("functional_flows_passed", False, detail), True, inputs
    if missing:
        return _req("functional_flows_passed", False,
                    f"required flow(s) {missing} have no current passing record"), True, inputs
    return _req("functional_flows_passed", True, f"flows {sorted(current)} passed at the approved content"), False, inputs


def _acceptance_pr(repo_root: Path, work_item_id: str, work_item: dict, anchor: dict | None, *,
                   require_ci: bool, requires_pr_approved: bool) -> dict:
    """The pull-request requirements (4 to 6), the `pending_query` flag, the
    kinds obtainable and the fact the decision read."""
    evidence = gate_evidence_of(work_item)
    fact, reported = evidence["pr"], evidence["pr_reported"]
    pending = fact is None or (reported is not None and reported["ingest_seq"] > fact["ingest_seq"])
    wanted = ["pr_fact_current", "no_standing_pr_objection"]
    if require_ci:
        wanted.append("ci_green")
    if requires_pr_approved:
        wanted.append("pr_approved")
    if pending:
        why = ("no workflow_gh pull-request fact is stored" if fact is None else
               "a reported pull-request fact is newer than the stored workflow_gh fact")
        detail = f"pending the query: {why}; the act queries GitHub itself"
        return {"requirements": [_req(name, False, detail) for name in wanted], "pending_query": True,
                "obtainable": [OBTAINABLE_PR] if anchor is not None else [], "fact": None}
    if fact["state"] == "none":
        detail = "no pull request was found for the approved implementation head; open one and push"
        return {"requirements": [_req(name, False, detail) for name in wanted], "pending_query": False,
                "obtainable": [OBTAINABLE_PR], "fact": fact}
    position = position_of(repo_root, work_item_id, work_item, fact["head"], anchor=anchor) if anchor else None
    descends = bool(anchor) and fact["head"] is not None and is_ancestor(repo_root, anchor["commit"], fact["head"])
    current = fact["state"] == "open" and position == "equal" and descends
    if current:
        current_detail = f"workflow_gh fact for PR #{fact['pr']['number']} at {fact['head']} is current"
    elif fact["state"] != "open":
        current_detail = f"the pull request is {fact['state']}, not open"
    elif position == "behind":
        current_detail = (f"the fact is for an older head {fact['head']}, behind the approved implementation; "
                          f"push the approved head and re-run /satisfy-gate acceptance")
    elif position == "ahead":
        current_detail = (f"the fact is for head {fact['head']}, whose protected content differs from the "
                          f"approved content")
    else:
        current_detail = f"head {fact['head']} does not descend from the anchor commit"
    objection = fact["review_decision"] == "CHANGES_REQUESTED" or fact["checks"]["state"] == "failure"
    requirements = [
        _req("pr_fact_current", current, current_detail),
        _req("no_standing_pr_objection", not objection,
             "no CHANGES_REQUESTED decision and no failed check" if not objection else
             f"standing objection: review_decision {fact['review_decision']!r}, checks {fact['checks']['state']!r}"),
    ]
    if require_ci:
        requirements.append(_req("ci_green", fact["checks"]["state"] == "success",
                                 f"checks are {fact['checks']['state']!r}"))
    if requires_pr_approved:
        approved = fact["review_decision"] == "APPROVED" and fact["reviewed_head"] == fact["head"]
        requirements.append(_req("pr_approved", approved and fact["state"] == "open",
                                 f"review_decision is {fact['review_decision']!r} (reviewed head {fact['reviewed_head']}) "
                                 f"on a {fact['state']} pull request"))
    # What a reporter can still change: a missing, older or pending fact; never a failure or an objection
    # at the current head, a different content, or a closed or merged pull request.
    if fact["state"] != "open" or position == "ahead" or (position is None) or (position == "equal" and not descends):
        obtainable = []
    elif position == "behind":
        obtainable = [OBTAINABLE_PR]
    else:
        unmet_unobtainable = objection
        pending_checks = fact["checks"]["state"] == "pending"
        undecided = requires_pr_approved and not (
            fact["review_decision"] == "APPROVED" and fact["reviewed_head"] == fact["head"])
        obtainable = [OBTAINABLE_PR] if not unmet_unobtainable and (pending_checks or undecided) else []
    return {"requirements": requirements, "pending_query": False, "obtainable": obtainable, "fact": fact}


def _evaluate_acceptance(repo_root: Path, state: dict, work_item_id: str, work_item: dict, effective: dict) -> dict:
    """The `automatic` half of `D-GP-Acceptance`: every requirement recomputed
    now. Returns the keys `evaluate_gate` merges into its result:
    `requirements`, `satisfiable`, `satisfiable_after_query` (every
    requirement but the pending pull-request ones is met), `pending_query`,
    `obtainable` and `evidence` (the inputs a satisfying record binds)."""
    import workflow_state

    section = effective["policy"]["acceptance"]
    anchor = anchor_of(repo_root, work_item_id, work_item)
    requirements = [_acceptance_registry_requirement(repo_root, work_item)]
    try:
        approval_current = workflow_state.approval_is_current(
            Path(repo_root), work_item, stage="implementation", base_commit=work_item["base_commit"])
    except Exception:
        approval_current = False
    approval = work_item.get("technical_approval")
    requirements.append(_req(
        "technical_approval_current", approval_current,
        "the technical approval is CURRENT for the content now" if approval_current else
        "there is no CURRENT technical approval for the content now"))
    functional_req, functional_obtainable, functional_inputs = _acceptance_functional(
        repo_root, work_item_id, work_item, anchor if approval_current else None, section["required_flows"])
    requirements.append(functional_req)
    pr = _acceptance_pr(repo_root, work_item_id, work_item, anchor if approval_current else None,
                        require_ci=section["require_ci"], requires_pr_approved=section["requires_pr_approved"])
    requirements += pr["requirements"]
    obtainable = ([OBTAINABLE_FUNCTIONAL] if functional_obtainable else []) + pr["obtainable"]
    met = all(r["met"] for r in requirements)
    non_pr = [r for r in requirements if r["id"] in ("checkpoints_complete", "technical_approval_current",
                                                     "functional_flows_passed")]
    fact = pr["fact"]
    evidence = {
        "anchor_commit": anchor["commit"] if anchor else None,
        "anchor_identity": anchor["identity"] if anchor else None,
        "technical_approval": {"basis": approval.get("basis"), "approved_review_content_id":
                               approval.get("approved_review_content_id"),
                               "recorded_at": approval.get("recorded_at")} if isinstance(approval, dict) else None,
        "functional": functional_inputs,
        "pr": None if fact is None else {"fact_id": fact["fact_id"], "head": fact["head"],
                                         "provenance": (fact.get("provenance") or {}).get("source"),
                                         "raw_sha256": (fact.get("provenance") or {}).get("raw_sha256")},
    }
    return {"requirements": requirements, "satisfiable": met,
            "satisfiable_after_query": pr["pending_query"] and all(r["met"] for r in non_pr),
            "pending_query": pr["pending_query"], "obtainable": obtainable, "evidence": evidence}


def assert_acceptance_evidence_current(repo_root: Path, state: dict, work_item_id: str) -> dict:
    """The library predicate of the automatic acceptance gate (`LPR-R2-005`):
    returns the evaluation when the gate is `automatic` and `satisfiable` on
    the stored facts; otherwise raises `AcceptanceEvidenceNotCurrentError`
    naming every unmet requirement. It runs no query: the act that calls it
    has stored a fresh `workflow_gh` fact first."""
    evaluation = evaluate_gate(repo_root, state, work_item_id, "acceptance")
    if evaluation["mode"] != "automatic" or not evaluation["satisfiable"]:
        unmet = [f"{r['id']}: {r['detail']}" for r in evaluation["requirements"] if not r["met"]]
        raise AcceptanceEvidenceNotCurrentError(
            f"{work_item_id}: the acceptance gate is not satisfiable by policy "
            f"(mode {evaluation['mode']!r}); unmet: {unmet}")
    return evaluation


def requires_pr_approved(effective: dict) -> bool:
    """True when the effective acceptance gate asks for an approved pull
    request, whatever its mode (`D-GP-Acceptance`, "The human path")."""
    return bool(effective["policy"]["acceptance"]["requires_pr_approved"])


def pr_approved_requirements(repo_root: Path, state: dict, work_item_id: str) -> list[dict]:
    """`pr_fact_current` and `pr_approved` on the stored `workflow_gh` fact,
    the check `/accept-milestone` step 2a runs for a human acceptance when
    `requires_pr_approved` is set (after the Workflow's own query stored a
    fresh fact). Unmet, never raised: the caller refuses naming each."""
    work_item = _work_item(state, work_item_id)
    anchor = anchor_of(repo_root, work_item_id, work_item)
    pr = _acceptance_pr(repo_root, work_item_id, work_item, anchor, require_ci=False, requires_pr_approved=True)
    return [r for r in pr["requirements"] if r["id"] in ("pr_fact_current", "pr_approved")]
