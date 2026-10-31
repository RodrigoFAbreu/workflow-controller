"""Render a shared Workflow command file for a Codex worker.

``.claude/commands/<name>.md`` is the one copy of each Workflow command
(its contract is ``docs/ai-workflow/COMMAND_FILES.md``). ``render_command``
turns one such file and an argument string into the prompt a Codex run
would be sent, applying what the contract tells an adapter to do for each
frontmatter key and substituting ``$ARGUMENTS``. It reads the requested file
and, to refuse an argument string that names a user-only command, every
sibling command file in the directory; it writes nothing, launches nothing and is called by nothing yet: the Codex
launch is a later step.

The user-only commands are refused by the union of the two recognisers
``controller.decision`` already holds, never by a copied list. The
contract's rule that the agent must not run one "as a step of another
command's execution" binds the agent; the control is each command's own
literal-confirmation check, which stays in the body. The body is sent
verbatim, so the renderer cannot enforce that sentence and does not claim to.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from . import decision, errors, worker

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DELIMITER = "---"
_ARGUMENTS_TOKEN = "$ARGUMENTS"
_REVIEW_SUBJECTS = frozenset({"bundle", "verdict", "none"})


@dataclasses.dataclass(frozen=True)
class RenderedCommand:
    """The prompt for one command, plus the frontmatter it was built from.

    ``state_writer`` is carried for the launcher to honour; ``review_subject``
    is ``bundle``, ``verdict``, ``none`` or ``None`` when the file omits it.
    ``description`` and ``argument_hint`` are empty when absent."""

    name: str
    prompt: str
    description: str
    argument_hint: str
    state_writer: bool
    review_subject: str | None


def _refuse(cause: str, name: str, path: Path, message: str, **extra: object) -> errors.CommandRenderError:
    return errors.CommandRenderError(
        f"cannot render command {name!r}: {message}",
        evidence={"cause": cause, "command": name, "file": str(path), **extra},
    )


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _read(path: Path) -> str:
    """The file's text with its line endings untouched (no newline translation),
    so the body is sent exactly as written."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _parse(text: str, name: str, path: Path) -> tuple[dict[str, str], str]:
    """The frontmatter pairs and the body, as ``COMMAND_FILES.md`` states the
    format. A malformed file is refused with the line named."""
    lines = text.splitlines(keepends=True)

    def bad(line: int, why: str) -> errors.CommandRenderError:
        return _refuse("malformed_frontmatter", name, path, f"{why} (line {line})", line=line)

    if not lines or lines[0].rstrip("\r\n") != _DELIMITER:
        raise bad(1, "the file does not open with a '---' line")
    pairs: dict[str, str] = {}
    key_lines: dict[str, int] = {}
    for index in range(1, len(lines)):
        number = index + 1
        line = lines[index].rstrip("\r\n")
        if line == _DELIMITER:
            body = "".join(lines[index + 1:])
            return _validate(pairs, key_lines, number, name, path), body
        if not line.strip():
            continue
        key, colon, value = line.partition(":")
        key = key.strip()
        if not colon:
            raise bad(number, "a frontmatter line without a colon")
        if not _KEY_PATTERN.match(key):
            raise bad(number, f"an invalid frontmatter key {key!r}")
        if key in pairs:
            raise bad(number, f"a duplicate frontmatter key {key!r}")
        pairs[key] = _unquote(value)
        key_lines[key] = number
    raise bad(len(lines), "the frontmatter has no closing '---' line")


def _validate(pairs: dict[str, str], key_lines: dict[str, int], closing: int,
              name: str, path: Path) -> dict[str, str]:
    """Check the declared values. A bad value is reported at its own line; a
    missing required key at the closing ``---`` line, where it should have been
    declared."""
    def bad(key: str, why: str) -> errors.CommandRenderError:
        line = key_lines.get(key, closing)
        return _refuse("malformed_frontmatter", name, path, f"{why} (line {line})", key=key, line=line)

    if pairs.get("state_writer") not in ("true", "false"):
        raise bad("state_writer", "state_writer must be declared as true or false")
    flag = pairs.get("disable-model-invocation")
    if flag is not None and flag not in ("true", "false"):
        raise bad("disable-model-invocation", "disable-model-invocation must be true or false")
    subject = pairs.get("review-subject")
    if subject is not None and subject not in _REVIEW_SUBJECTS:
        raise bad("review-subject", "review-subject must be bundle, verdict or none")
    return pairs


def _claimed_user_only(text: str) -> bool:
    """The union of the two recognisers ``decision.classify_command_files``
    uses, applied to one file's text."""
    return decision.carries_disable_model_invocation_flag(text) or decision.carries_user_confirmation_guard(text)


def _file_is_user_only(path: Path) -> bool:
    """The same rule the requested file is held to, for a sibling: the
    recognisers or a parsed ``disable-model-invocation`` of ``true``. A file
    that cannot be read or parsed is treated as user-only (fail closed)."""
    try:
        text = _read(path)
    except (OSError, UnicodeDecodeError):
        return True
    if _claimed_user_only(text):
        return True
    try:
        pairs, _ = _parse(text, path.stem, path)
    except errors.CommandRenderError:
        return True
    return pairs.get("disable-model-invocation") == "true"


def _user_only_names(commands_dir: Path) -> frozenset[str]:
    names = {p.stem for p in commands_dir.glob("*.md") if p.is_file() and _file_is_user_only(p)}
    return frozenset(names) | worker.USER_ONLY_COMMANDS


def _refuse_user_only(name: str, path: Path, why: str) -> errors.UserOnlyCommandError:
    return errors.UserOnlyCommandError(
        f"refusing to render the user-only command {name!r}: {why}",
        evidence={"command": name, "file": str(path), "matched_token": name},
    )


def render_command(name: str, arguments: str, commands_dir: Path) -> RenderedCommand:
    """Render ``<commands_dir>/<name>.md`` with ``arguments`` for a Codex run.

    Order: the name check, the directory check and the file lookup, the read,
    the user-only refusal, the scan of ``arguments``, the frontmatter parse,
    the parsed-flag refusal, then the substitution.
    """
    commands_dir = Path(commands_dir)
    if not isinstance(name, str) or not _NAME_PATTERN.match(name):
        raise _refuse("unknown_command", name, commands_dir / "<invalid>", "not a bare command name")
    if not commands_dir.is_dir():
        raise errors.MissingCommandsDirectoryError(
            f"commands directory {str(commands_dir)!r} does not exist or is not a directory",
            evidence={"commands_dir": str(commands_dir)},
        )
    path = commands_dir / f"{name}.md"
    if not path.is_file():
        raise _refuse("unknown_command", name, path, "no such command file")
    try:
        text = _read(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise _refuse("unreadable", name, path, f"the file cannot be read ({exc})") from exc

    if _claimed_user_only(text):
        raise _refuse_user_only(name, path, "it carries the user-only flag or the confirmation guard")

    user_only = _user_only_names(commands_dir)
    for token in worker._task_tokens(arguments):
        if token in user_only:
            raise errors.UserOnlyCommandError(
                f"refusing to render {name!r}: its arguments name the user-only command {token!r}",
                evidence={"command": name, "file": str(path), "matched_token": token},
            )
    worker._assert_not_user_only(f"/{name} {arguments}")

    pairs, body = _parse(text, name, path)
    if pairs.get("disable-model-invocation") == "true":
        raise _refuse_user_only(name, path, "its disable-model-invocation value is true")

    hint = pairs.get("argument-hint", "")
    if arguments != "":
        if not hint:
            raise _refuse("arguments_not_accepted", name, path, "the command declares no argument-hint")
        if _ARGUMENTS_TOKEN not in body:
            raise _refuse("arguments_not_accepted", name, path, "the body has no $ARGUMENTS token")
    prompt = arguments.join(body.split(_ARGUMENTS_TOKEN))
    return RenderedCommand(
        name=name,
        prompt=prompt,
        description=pairs.get("description", ""),
        argument_hint=hint,
        state_writer=pairs["state_writer"] == "true",
        review_subject=pairs.get("review-subject"),
    )
