"""Conventional Commit titles (``workflow-controller-squash-merge-tag-
versioning`` CP1, Design A).

One parser decides everywhere (I2): ``tools/release.py check-title``
validates a pull request title with it, the release transaction classifies
a trunk commit's subject with it, and the Controller validates the title it
sets. The grammar is SignalHub's, unchanged (:data:`SUBJECT_RE`); which type
releases what is the committed policy's ``release.change_types`` table,
passed in by the caller.

Stdlib only; imports nothing from the package but ``errors``.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping

from .errors import InvalidTitleError

#: The name the plan gives the refusal.
InvalidTitle = InvalidTitleError

#: SignalHub's ``scripts/release/release.py`` grammar. A ``(#N)`` squash
#: suffix is part of the description.
SUBJECT_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9._/-]+)\))?(?P<breaking>!)?: (?P<description>\S.*)$")
#: A ``change_types`` key: the grammar's own type alphabet.
TYPE_RE = re.compile(r"[a-z]+")

MAJOR, MINOR, PATCH, NONE = "major", "minor", "patch", "none"
#: The values a ``change_types`` entry may take: only ``!`` gives a major.
CHANGE_TYPE_BUMPS = frozenset({MINOR, PATCH, NONE})
#: Every bump, in ascending order.
BUMPS = (NONE, PATCH, MINOR, MAJOR)

_EXPECTED = ("expected 'type(scope)!: description': a lowercase type, an optional lowercase "
             "scope in parentheses, an optional '!', then ': ' and a description")


@dataclasses.dataclass(frozen=True)
class Subject:
    type: str
    scope: str | None
    breaking: bool
    description: str


def parse(subject: str) -> Subject:
    """``subject`` split by the grammar. Only a trailing newline is removed;
    no other whitespace is trimmed. Raises :class:`InvalidTitle`."""
    match = SUBJECT_RE.fullmatch(subject.removesuffix("\n"))
    if match is None:
        raise InvalidTitleError(f"{subject!r} is not a Conventional Commit title: {_EXPECTED}",
                                evidence={"subject": subject, "problem": "grammar"})
    return Subject(type=match["type"], scope=match["scope"], breaking=match["breaking"] is not None,
                   description=match["description"])


def bump(subject: str, change_types: Mapping[str, str]) -> str:
    """The release ``subject`` asks for under ``change_types``: ``major``,
    ``minor``, ``patch`` or ``none``. Raises :class:`InvalidTitle` for a
    subject outside the grammar, an unknown type, or ``!`` on a type the
    table maps to ``none``."""
    parsed = parse(subject)
    if parsed.type not in change_types:
        raise InvalidTitleError(
            f"{subject!r} has the unknown type {parsed.type!r}; the allowed types are "
            f"{sorted(change_types)}",
            evidence={"subject": subject, "problem": "unknown_type", "types": sorted(change_types)})
    release = change_types[parsed.type]
    if not parsed.breaking:
        return release
    if release == NONE:
        raise InvalidTitleError(
            f"{subject!r} marks a breaking change on {parsed.type!r}, which releases nothing; a "
            f"breaking change must release: use `feat!` or `fix!`",
            evidence={"subject": subject, "problem": "breaking_none", "types": sorted(change_types)})
    return MAJOR
