"""Tests for the user settings file (``controller.settings``,
``workflow-controller-settings-and-telemetry`` CP1, Design A).

Covers the pinned table and its compatibility rule, the location order,
the validation refusals (I2), the additive, forward-only fill (A.4) and its
lock (I4), unknown keys and ``settings clean`` (A.5), two releases sharing
one file, the routing section's lenient parse beside ``--routing-config``'s
strict one, the ``settings`` subcommand (A.6), ``--settings`` across the
re-exec, and the I5 guard: no test reads or writes the operator's file.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tests  # noqa: E402
from tests import fixtures  # noqa: E402
from controller import cli, routing, runtime, settings  # noqa: E402
from controller.errors import RoutingConfigError, SettingsError  # noqa: E402

S = settings.Setting

#: The v1 table, pinned (I3): key, type, default, minimum, maximum,
#: generation.
V1_TABLE = (
    ("worker.drain_detach_seconds", "int", 10800, 60, 604800, 1),
    ("worker.timeout_seconds", "optional-int", None, 60, 172800, 1),
    ("run.max_steps", "int", 20, 1, 1000, 1),
    ("follow.heartbeat_seconds", "int", 30, 1, 3600, 1),
    ("follow.replay_events", "int", 20, 0, 10000, 1),
    ("timeouts.git_seconds", "int", 600, 30, 7200, 1),
    ("timeouts.release_command_seconds", "int", 1800, 60, 21600, 1),
    ("timeouts.workflow_query_seconds", "int", 120, 10, 3600, 1),
    ("forge.pr_list_limit", "int", 200, 50, 1000, 1),
    ("routing", "routing", {"default": {}, "roles": {}}, None, None, 1),
)

#: Every generation's type and bounds of each key, oldest first. A release
#: that changes a default appends nothing here (the rule forbids changing
#: type or range); a release that adds a key adds a row.
KEY_HISTORY = {
    key: [(kind, minimum, maximum)] for key, kind, _default, minimum, maximum, _gen in V1_TABLE
}

#: The routing names the table pins with it (A.5).
V1_ROLES = frozenset({
    "milestone-implement", "milestone-implement-self-review", "apply-plan-review",
    "apply-implementation-review", "review-plan", "review-implementation", "milestone-plan",
    "record-manual-plan-review", "record-manual-implementation-review",
})


def _defaults_file() -> dict:
    """The file the v1 fill writes over a missing one."""
    data: dict = {"schema_version": 1, "_table_generation": 1, "_defaults_written": {}}
    for key, _kind, default, _minimum, _maximum, generation in V1_TABLE:
        section, _, name = key.partition(".")
        if name:
            data.setdefault(section, {})[name] = copy.deepcopy(default)
        else:
            data[key] = copy.deepcopy(default)
        data["_defaults_written"][key] = {"value": copy.deepcopy(default), "generation": generation}
    return data


def _write(path: Path, data: dict | str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, dict):
        data = json.dumps(data, indent=2, sort_keys=True) + "\n"
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def _release(table: tuple, generation: int, roles: frozenset | None = None):
    """Stand in for another Controller release: its settings table, its
    ``TABLE_GENERATION`` and its routing roles."""
    roles = routing.ROLES if roles is None else roles
    route_table = dict(routing.ROLE_ROUTES)
    for role in roles - set(route_table):
        route_table[role] = routing.Route(model=None, effort=None, single_agent=False)
    with mock.patch.object(settings, "TABLE", table), \
            mock.patch.object(settings, "TABLE_GENERATION", generation), \
            mock.patch.object(routing, "ROLES", roles), \
            mock.patch.object(routing, "ROLE_ROUTES", route_table):
        settings._warned.clear()
        yield


#: Release N+1 for the two-release tests: ``run.max_steps``' default moves
#: to 25 (generation 2, inside v1's bounds), a new key and a new role.
_NEXT_TABLE = tuple(
    S("run.max_steps", "int", 25, 1, 1000, "run --max-steps", 2) if row.key == "run.max_steps" else row
    for row in settings.TABLE
) + (S("worker.new_knob", "int", 5, 1, 10, None, 2),)
_NEXT_ROLES = routing.ROLES | {"new-role"}


class _TempSettings(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "cfg" / "settings.json"
        settings._warned.clear()
        self.addCleanup(settings._warned.clear)

    def stderr_of(self, fn, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = fn(*args)
        return result, err.getvalue()


class TableTest(unittest.TestCase):
    def test_the_table_is_pinned(self) -> None:
        rows = tuple((s.key, s.type, s.default, s.minimum, s.maximum, s.generation) for s in settings.TABLE)
        self.assertEqual(rows, V1_TABLE)
        self.assertEqual(settings.TABLE_GENERATION, 1)
        self.assertEqual(settings.TABLE_GENERATION, max(s.generation for s in settings.TABLE))
        self.assertEqual(routing.ROLES, V1_ROLES)
        self.assertEqual(routing.FIELDS, ("model", "effort"))
        self.assertEqual(routing.SECTION_KEYS, ("default", "roles"))

    def test_every_default_fits_every_generation_of_its_key(self) -> None:
        """The compatibility rule (A.3): a release never moves a default
        outside the bounds any earlier generation gave the key and never
        changes a key's type -- otherwise an older release sharing the file
        would refuse a value the operator never touched. A change of type
        or range retires the key and adds a new one."""
        for setting in settings.TABLE:
            history = KEY_HISTORY[setting.key]
            for kind, minimum, maximum in history:
                with self.subTest(key=setting.key, generation_bounds=(kind, minimum, maximum)):
                    self.assertEqual(setting.type, kind)
                    if kind == "routing":
                        routing.validate_routing_mapping(
                            setting.default, path="<table>", where="routing.", require_schema_version=False,
                            unknown="refuse")
                    elif setting.default is not None:
                        self.assertIs(type(setting.default), int)
                        self.assertTrue(minimum <= setting.default <= maximum)
                    else:
                        self.assertEqual(kind, "optional-int")

    def test_the_cli_flags_are_named(self) -> None:
        flags = {s.key: s.cli_flag for s in settings.TABLE}
        self.assertEqual(flags["worker.drain_detach_seconds"], "resume --drain-timeout")
        self.assertEqual(flags["worker.timeout_seconds"], "--timeout")
        self.assertEqual(flags["run.max_steps"], "run --max-steps")
        self.assertEqual(flags["follow.replay_events"], "follow --from-start")
        self.assertIsNone(flags["timeouts.git_seconds"])


class LocationTest(unittest.TestCase):
    def test_the_location_order(self) -> None:
        home = Path("/home/someone")
        env = {"WORKFLOW_CONTROLLER_SETTINGS": "/env/s.json", "XDG_CONFIG_HOME": "/xdg"}
        self.assertEqual(settings.resolve_path("/cli/s.json", env=env, home=home), Path("/cli/s.json"))
        self.assertEqual(settings.resolve_path(None, env=env, home=home), Path("/env/s.json"))
        self.assertEqual(settings.resolve_path(None, env={"XDG_CONFIG_HOME": "/xdg"}, home=home),
                         Path("/xdg/workflow-controller/settings.json"))
        self.assertEqual(settings.resolve_path(None, env={}, home=home),
                         home / ".config" / "workflow-controller" / "settings.json")
        # Empty values are unset.
        self.assertEqual(settings.resolve_path(None, env={"WORKFLOW_CONTROLLER_SETTINGS": "", "XDG_CONFIG_HOME": ""},
                                               home=home),
                         home / ".config" / "workflow-controller" / "settings.json")

    def test_a_relative_path_is_made_absolute(self) -> None:
        self.assertEqual(settings.resolve_path("rel/s.json", env={}), Path(os.getcwd()) / "rel" / "s.json")
        self.assertEqual(settings.resolve_path(None, env={"WORKFLOW_CONTROLLER_SETTINGS": "e.json"}),
                         Path(os.getcwd()) / "e.json")

    def test_settings_is_passed_on_absolute_across_the_re_exec(self) -> None:
        with mock.patch.object(cli.os, "execve") as execve:
            cli._reexec(snapshot_dir=Path("/snap"), runtime_root=Path("/rt"),
                        argv=["--settings", "rel/s.json", "--settings=other.json", "step", "/repo"],
                        exec_depth_seen=0, source_kind="worktree", source_commit=None,
                        settings_path="rel/s.json")
        child_argv = execve.call_args.args[1]
        self.assertEqual(child_argv[child_argv.index("--settings") + 1], str(Path(os.getcwd()) / "rel" / "s.json"))
        self.assertEqual(child_argv.count("--settings"), 1)
        self.assertFalse(any(token.startswith("--settings=") for token in child_argv))
        self.assertEqual(child_argv[-2:], ["step", "/repo"])
        with mock.patch.object(cli.os, "execve") as execve:
            cli._reexec(snapshot_dir=Path("/snap"), runtime_root=Path("/rt"), argv=["step", "/repo"],
                        exec_depth_seen=0, source_kind="worktree", source_commit=None)
        self.assertNotIn("--settings", execve.call_args.args[1])


class ValidationTest(_TempSettings):
    """I2: every malformed file is ``SettingsError`` (exit 20), naming the
    path and the dotted key, and is never rewritten."""

    CASES = {
        "not JSON": ("{", ""),
        "not UTF-8": (b"\xff\xfe", ""),
        "not an object": ("[]", ""),
        "duplicate key": ('{"run": {"max_steps": 3, "max_steps": 4}}', "max_steps"),
        "schema_version 2": ({"schema_version": 2}, "schema_version"),
        "schema_version a string": ({"schema_version": "1"}, "schema_version"),
        "section not an object": ({"run": 5}, "run"),
        "wrong type": ({"run": {"max_steps": "20"}}, "run.max_steps"),
        "boolean for an integer": ({"run": {"max_steps": True}}, "run.max_steps"),
        "below the minimum": ({"run": {"max_steps": 0}}, "run.max_steps"),
        "above the maximum": ({"forge": {"pr_list_limit": 1001}}, "forge.pr_list_limit"),
        "float": ({"follow": {"heartbeat_seconds": 1.5}}, "follow.heartbeat_seconds"),
        "optional int out of bounds": ({"worker": {"timeout_seconds": 59}}, "worker.timeout_seconds"),
        "_table_generation zero": ({"_table_generation": 0}, "_table_generation"),
        "_table_generation boolean": ({"_table_generation": True}, "_table_generation"),
        "_table_generation string": ({"_table_generation": "1"}, "_table_generation"),
        "_defaults_written not an object": ({"_defaults_written": []}, "_defaults_written"),
        "entry not an object": ({"run": {"max_steps": 20}, "_defaults_written": {"run.max_steps": 20}},
                                "_defaults_written.run.max_steps"),
        "entry without generation": ({"run": {"max_steps": 20}, "_defaults_written": {"run.max_steps": {"value": 20}}},
                                     "_defaults_written.run.max_steps"),
        "entry with an extra field": (
            {"run": {"max_steps": 20},
             "_defaults_written": {"run.max_steps": {"value": 20, "generation": 1, "x": 1}}},
            "_defaults_written.run.max_steps"),
        "entry generation zero": ({"run": {"max_steps": 20},
                                   "_defaults_written": {"run.max_steps": {"value": 20, "generation": 0}}},
                                  "_defaults_written.run.max_steps"),
        "entry generation boolean": ({"run": {"max_steps": 20},
                                      "_defaults_written": {"run.max_steps": {"value": 20, "generation": True}}},
                                     "_defaults_written.run.max_steps"),
        "entry for an absent key": ({"_defaults_written": {"run.max_steps": {"value": 20, "generation": 1}}},
                                    "_defaults_written.run.max_steps"),
        "routing not an object": ({"routing": []}, "routing"),
        "routing roles not an object": ({"routing": {"roles": []}}, "routing.roles"),
        "routing value refused": ({"routing": {"roles": {"review-plan": {"model": "-x"}}}},
                                  "routing.roles.review-plan.model"),
        "routing default value refused": ({"routing": {"default": {"effort": ""}}}, "routing.default.effort"),
        "routing schema_version reserved": ({"routing": {"schema_version": 1, "default": {}}},
                                            "routing.schema_version"),
    }

    def test_every_malformed_file_is_refused_and_left_alone(self) -> None:
        for name, (content, key) in self.CASES.items():
            for action in ("load", "fill", "clean"):
                with self.subTest(case=name, action=action):
                    _write(self.path, content)
                    before = self.path.read_bytes()
                    with self.assertRaises(SettingsError) as ctx:
                        getattr(settings, action)(self.path)
                    self.assertEqual(ctx.exception.evidence["path"], str(self.path))
                    self.assertEqual(ctx.exception.evidence["key"], key)
                    self.assertIn(str(self.path), ctx.exception.message)
                    self.assertEqual(self.path.read_bytes(), before)

    def test_a_routing_refusal_is_a_settings_error_naming_the_dotted_key(self) -> None:
        _write(self.path, {"routing": {"roles": {"review-plan": {"model": "-x"}}}})
        with self.assertRaises(SettingsError) as ctx:
            settings.load(self.path)
        self.assertNotIsInstance(ctx.exception, RoutingConfigError)
        self.assertIn("routing.roles.review-plan.model", ctx.exception.message)
        self.assertNotIn("routing config", ctx.exception.message)
        self.assertEqual(ctx.exception.evidence["routing_evidence"]["where"], "routing.roles.review-plan.model")

    def test_the_cli_refuses_a_bad_file_with_exit_20(self) -> None:
        _write(self.path, {"run": {"max_steps": 0}})
        for action in ("show", "clean"):
            with self.subTest(action=action):
                code, _out, err = _main(["--settings", str(self.path), "settings", action])
                self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
                self.assertIn("run.max_steps must be an integer from 1 to 1000, got 0", err)
        self.assertEqual(_read(self.path), {"run": {"max_steps": 0}})

    def test_a_missing_file_is_never_an_error(self) -> None:
        loaded = settings.load(self.path)
        self.assertFalse(loaded.exists)
        self.assertIsNone(loaded.sha256)
        self.assertEqual(dict(loaded.values), {})
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.parent.exists())


class FillTest(_TempSettings):
    def test_the_fill_creates_a_missing_file_with_the_defaults(self) -> None:
        loaded = settings.fill(self.path)
        self.assertEqual(_read(self.path), _defaults_file())
        self.assertTrue(loaded.exists)
        self.assertEqual(loaded.unknown, ())
        self.assertEqual(loaded.sha256, hashlib.sha256(self.path.read_bytes()).hexdigest())
        # The canonical form: sorted keys, two-space indent, trailing newline.
        self.assertEqual(self.path.read_text(), json.dumps(_defaults_file(), indent=2, sort_keys=True) + "\n")

    def test_the_fill_is_additive_and_keeps_operator_values(self) -> None:
        _write(self.path, {"run": {"max_steps": 7}, "worker": {"timeout_seconds": 600}})
        settings.fill(self.path)
        data = _read(self.path)
        self.assertEqual(data["run"]["max_steps"], 7)
        self.assertEqual(data["worker"]["timeout_seconds"], 600)
        self.assertEqual(data["worker"]["drain_detach_seconds"], 10800)
        # A value the operator set has no `_defaults_written` entry.
        self.assertNotIn("run.max_steps", data["_defaults_written"])
        self.assertEqual(data["_defaults_written"]["follow.heartbeat_seconds"], {"value": 30, "generation": 1})

    def test_the_fill_writes_only_when_something_changes(self) -> None:
        settings.fill(self.path)
        inode = self.path.stat().st_ino
        settings.fill(self.path)
        self.assertEqual(self.path.stat().st_ino, inode)
        # A complete file in another layout is not rewritten either.
        compact = json.dumps(_defaults_file()).encode()
        _write(self.path, compact)
        inode = self.path.stat().st_ino
        settings.fill(self.path)
        self.assertEqual(self.path.read_bytes(), compact)
        self.assertEqual(self.path.stat().st_ino, inode)

    def test_the_fill_never_lowers_the_table_generation(self) -> None:
        data = _defaults_file()
        data["_table_generation"] = 7
        _write(self.path, data)
        before = self.path.read_bytes()
        settings.fill(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_an_unwritable_directory_warns_and_still_applies_the_file(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        _write(self.path, {"run": {"max_steps": 7}})
        before = self.path.read_bytes()
        os.chmod(self.path.parent, 0o500)
        self.addCleanup(os.chmod, self.path.parent, 0o700)
        loaded, err = self.stderr_of(settings.fill, self.path)
        self.assertEqual(err.count("warning: could not fill the settings file"), 1)
        self.assertEqual(loaded.values["run.max_steps"], 7)
        effective = settings.resolve(loaded)
        self.assertEqual(effective["run.max_steps"], 7)
        self.assertEqual(effective.sources["run.max_steps"], "file")
        self.assertEqual(effective["follow.heartbeat_seconds"], 30)
        self.assertEqual(effective.sources["follow.heartbeat_seconds"], "default")
        self.assertEqual(self.path.read_bytes(), before)

    def test_an_unwritable_missing_directory_uses_the_defaults(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        os.chmod(self.dir, 0o500)
        self.addCleanup(os.chmod, self.dir, 0o700)
        loaded, err = self.stderr_of(settings.fill, self.path)
        self.assertIn("could not fill the settings file", err)
        self.assertFalse(loaded.exists)
        self.assertEqual(settings.resolve(loaded)["run.max_steps"], 20)


class UnknownKeyTest(_TempSettings):
    def test_unknown_keys_are_ignored_with_one_warning_per_invocation(self) -> None:
        data = _defaults_file()
        data["mystery"] = 1
        data["run"]["speed"] = "fast"
        _write(self.path, data)
        loaded, err = self.stderr_of(settings.load, self.path)
        self.assertEqual(loaded.unknown, ("mystery", "run.speed"))
        self.assertEqual(err.count("warning:"), 1)
        self.assertIn("unknown key(s) mystery, run.speed", err)
        self.assertIn("settings clean", err)
        _loaded, err = self.stderr_of(settings.fill, self.path)
        self.assertEqual(err, "")
        self.assertEqual(settings.resolve(loaded)["run.max_steps"], 20)

    def test_a_newer_release_s_file_says_so(self) -> None:
        data = _defaults_file()
        data["_table_generation"] = 3
        data["forge"]["new_thing"] = 1
        _write(self.path, data)
        _loaded, err = self.stderr_of(settings.load, self.path)
        self.assertIn("forge.new_thing", err)
        self.assertIn("last filled by a newer Controller release (table generation 3", err)


class CleanTest(_TempSettings):
    def test_clean_removes_unknown_keys_and_their_bookkeeping(self) -> None:
        data = _defaults_file()
        data["mystery"] = {"a": 1}
        data["_defaults_written"]["mystery.a"] = {"value": 1, "generation": 1}
        data["run"]["speed"] = "fast"
        data["_defaults_written"]["run.speed"] = {"value": "fast", "generation": 1}
        data["routing"]["roles"]["gone-role"] = {"model": "m"}
        data["routing"]["roles"]["review-plan"] = {"model": "x", "temperature": "hot"}
        data["routing"]["extra"] = {}
        _write(self.path, data)
        code, out, _err = _main(["--settings", str(self.path), "settings", "clean"])
        self.assertEqual(code, 0)
        self.assertIn("mystery", out)
        expected = _defaults_file()
        expected["routing"]["roles"]["review-plan"] = {"model": "x"}
        self.assertEqual(_read(self.path), expected)

    def test_clean_removes_an_unknown_dotted_role_named_after_a_known_one(self) -> None:
        # ``routing.roles.review-plan.future`` reads as the unknown role
        # ``review-plan.future`` or as ``review-plan``'s field ``future``.
        for roles in ({"review-plan.future": {"model": "m"}},
                      {"review-plan.future": {"model": "m"}, "review-plan": {"model": "x", "future": 1}}):
            with self.subTest(roles=sorted(roles)):
                data = _defaults_file()
                data["routing"]["roles"] = roles
                _write(self.path, data)
                _loaded, removed = settings.clean(self.path)
                self.assertEqual(removed, ["routing.roles.review-plan.future"])
                expected = _defaults_file()
                expected["routing"]["roles"] = {"review-plan": {"model": "x"}} if "review-plan" in roles else {}
                self.assertEqual(_read(self.path), expected)
                self.assertEqual(settings.load(self.path).unknown, ())

    def test_clean_keeps_a_known_value_whose_path_an_unknown_dotted_key_shares(self) -> None:
        # The unknown role ``review-plan.model`` and the unknown routing key
        # ``default.effort`` share a dotted path with a known, valid value.
        cases = (
            ("routing.roles.review-plan.model",
             {"default": {}, "roles": {"review-plan": {"model": "opus"}, "review-plan.model": {"model": "x"}}},
             {"default": {}, "roles": {"review-plan": {"model": "opus"}}}),
            ("routing.default.effort",
             {"default": {"effort": "high"}, "default.effort": 1, "roles": {}},
             {"default": {"effort": "high"}, "roles": {}}),
        )
        for dotted, section, kept in cases:
            with self.subTest(dotted=dotted):
                data = _defaults_file()
                data["routing"] = section
                _write(self.path, data)
                _loaded, removed = settings.clean(self.path)
                self.assertEqual(removed, [dotted])
                expected = _defaults_file()
                expected["routing"] = kept
                self.assertEqual(_read(self.path), expected)
                self.assertEqual(settings.load(self.path).unknown, ())

    def test_clean_keeps_the_record_of_a_known_key_an_unknown_dotted_key_names(self) -> None:
        # A top-level key named ``worker.timeout_seconds`` is unknown; the
        # known setting's ``_defaults_written`` record must survive it.
        data = _defaults_file()
        data["worker.timeout_seconds"] = 5
        _write(self.path, data)
        _loaded, removed = settings.clean(self.path)
        self.assertEqual(removed, ["worker.timeout_seconds"])
        self.assertEqual(_read(self.path), _defaults_file())
        self.assertEqual(settings.load(self.path).unknown, ())

    def test_clean_fills_a_missing_file(self) -> None:
        loaded, removed = settings.clean(self.path)
        self.assertEqual(removed, [])
        self.assertEqual(_read(self.path), _defaults_file())
        self.assertTrue(loaded.exists)

    def test_clean_refuses_a_file_filled_by_a_newer_release(self) -> None:
        data = _defaults_file()
        data["_table_generation"] = 2
        data["run"]["speed"] = "fast"
        _write(self.path, data)
        before = self.path.read_bytes()
        with self.assertRaises(SettingsError) as ctx:
            settings.clean(self.path)
        self.assertIn("table generation 2", ctx.exception.message)
        self.assertIn("newest installed release", ctx.exception.message)
        self.assertEqual(ctx.exception.evidence["key"], "_table_generation")
        self.assertEqual(self.path.read_bytes(), before)
        code, _out, err = _main(["--settings", str(self.path), "settings", "clean"])
        self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
        self.assertEqual(self.path.read_bytes(), before)


class TwoReleasesTest(_TempSettings):
    """Two patched tables standing in for releases N and N+1 share one
    file in alternation (A.4, A.5)."""

    def test_n_plus_1_moves_the_untouched_value_and_n_never_moves_it_back(self) -> None:
        with _release(settings.TABLE, 1):
            settings.fill(self.path)
        self.assertEqual(_read(self.path)["run"]["max_steps"], 20)
        with _release(_NEXT_TABLE, 2):
            settings.fill(self.path)
        data = _read(self.path)
        self.assertEqual(data["run"]["max_steps"], 25)
        self.assertEqual(data["_defaults_written"]["run.max_steps"], {"value": 25, "generation": 2})
        self.assertEqual(data["worker"]["new_knob"], 5)
        self.assertEqual(data["_table_generation"], 2)
        after_next = self.path.read_bytes()
        with _release(settings.TABLE, 1):
            loaded, err = self.stderr_of(settings.fill, self.path)
            self.assertEqual(self.path.read_bytes(), after_next)
            self.assertEqual(settings.resolve(loaded)["run.max_steps"], 25)
            self.assertIn("worker.new_knob", err)
            self.assertIn("newer Controller release", err)
            with self.assertRaises(SettingsError):
                settings.clean(self.path)
        self.assertEqual(self.path.read_bytes(), after_next)
        with _release(_NEXT_TABLE, 2):
            loaded, err = self.stderr_of(settings.fill, self.path)
            self.assertEqual(err, "")
            self.assertEqual(settings.resolve(loaded)["worker.new_knob"], 5)

    def test_a_value_the_operator_set_is_never_moved(self) -> None:
        with _release(settings.TABLE, 1):
            settings.fill(self.path)
        data = _read(self.path)
        data["run"]["max_steps"] = 50
        _write(self.path, data)
        with _release(_NEXT_TABLE, 2):
            settings.fill(self.path)
        self.assertEqual(_read(self.path)["run"]["max_steps"], 50)

    def test_a_value_reset_to_the_old_default_by_hand_is_untouched_only_by_its_record(self) -> None:
        """An operator who sets the value back to the recorded default is
        indistinguishable from one who never touched it: the record decides
        (A.4's first condition)."""
        with _release(settings.TABLE, 1):
            settings.fill(self.path)
        data = _read(self.path)
        del data["_defaults_written"]["run.max_steps"]
        _write(self.path, data)
        with _release(_NEXT_TABLE, 2):
            settings.fill(self.path)
        self.assertEqual(_read(self.path)["run"]["max_steps"], 20)


class SharedRoutingTest(_TempSettings):
    """Release N+1 adds a role; the shared file's routing section carries
    it, a known role's extra field and a new key under ``routing``
    (R4-003, MPR6-O1)."""

    def _shared_file(self) -> None:
        with _release(_NEXT_TABLE, 2, _NEXT_ROLES):
            settings.fill(self.path)
        data = _read(self.path)
        data["routing"] = {
            "default": {"effort": "high"},
            "roles": {"new-role": {"model": "m-new"}, "review-plan": {"model": "m-review", "temperature": "hot"}},
            "budgets": {"daily": 3},
        }
        _write(self.path, data)

    def test_release_n_ignores_what_it_does_not_know_and_routes_the_rest(self) -> None:
        self._shared_file()
        before = self.path.read_bytes()
        with _release(settings.TABLE, 1):
            code, out, err = _main(["--settings", str(self.path), "settings", "show"])
            self.assertEqual(code, 0)
            self.assertEqual(err.count("warning:"), 1)
            for dotted in ("routing.roles.new-role", "routing.roles.review-plan.temperature", "routing.budgets",
                           "worker.new_knob"):
                self.assertIn(dotted, err)
            self.assertIn('routing = {"default": {"effort": "high"}, "roles": {"review-plan": {"model": "m-review"}}} '
                          '(file)', out)
            loaded = settings.load(self.path)
            route = routing.resolve_route("review-plan", config=loaded.routing_config)
            self.assertEqual((route.model, route.model_source), ("m-review", "config-role"))
            self.assertEqual((route.effort, route.effort_source), ("high", "config-default"))
            # N's fill leaves the section alone; N's clean refuses and keeps it.
            settings.fill(self.path)
            self.assertEqual(self.path.read_bytes(), before)
            with self.assertRaises(SettingsError):
                settings.clean(self.path)
            self.assertEqual(self.path.read_bytes(), before)
        with _release(_NEXT_TABLE, 2, _NEXT_ROLES):
            loaded, _err = self.stderr_of(settings.load, self.path)
            route = routing.resolve_route("new-role", config=loaded.routing_config)
            self.assertEqual((route.model, route.model_source), ("m-new", "config-role"))

    def test_release_n_still_refuses_what_is_malformed(self) -> None:
        for routing_section, key in (
            ({"roles": {"review-plan": {"model": "-bad"}}}, "routing.roles.review-plan.model"),
            ({"roles": "all"}, "routing.roles"),
        ):
            with self.subTest(key=key):
                _write(self.path, {"routing": routing_section})
                code, _out, err = _main(["--settings", str(self.path), "settings", "show"])
                self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
                self.assertIn(key, err)

    def test_routing_config_with_the_same_unknown_role_still_refuses(self) -> None:
        config = self.dir / "routing.json"
        _write(config, {"schema_version": 1, "roles": {"new-role": {"model": "m"}}})
        with self.assertRaises(RoutingConfigError) as ctx:
            routing.load_routing_config(config)
        self.assertIn("unknown role(s) ['new-role']", ctx.exception.message)
        code, _out, err = _main(["--settings", str(self.path), "--routing-config", str(config), "settings", "show"])
        self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
        self.assertIn("the routing config", err)


class RoutingSectionTest(_TempSettings):
    def test_a_default_filled_file_loads_with_no_warning_and_routes_as_none(self) -> None:
        settings.fill(self.path)
        settings._warned.clear()
        loaded, err = self.stderr_of(settings.load, self.path)
        self.assertEqual(err, "")
        for role in routing.ROLES:
            with self.subTest(role=role):
                self.assertEqual(routing.resolve_route(role, config=loaded.routing_config),
                                 routing.resolve_route(role))

    def test_a_known_role_resolves_the_same_through_either_source(self) -> None:
        section = {"default": {"model": "m-default"}, "roles": {"review-plan": {"effort": "low"}}}
        _write(self.path, {"routing": section})
        from_settings = settings.load(self.path).routing_config
        from_file = routing.parse_routing_config(json.dumps({"schema_version": 1, **section}), path="<cfg>")
        for role in routing.ROLES:
            with self.subTest(role=role):
                self.assertEqual(routing.resolve_route(role, config=from_settings),
                                 routing.resolve_route(role, config=from_file))

    def test_routing_config_replaces_the_section_whole(self) -> None:
        _write(self.path, {"routing": {"default": {"model": "from-settings"}}})
        config = self.dir / "routing.json"
        _write(config, {"schema_version": 1, "roles": {"review-plan": {"effort": "low"}}})
        code, out, _err = _main(["--json", "--settings", str(self.path), "--routing-config", str(config),
                                 "settings", "show"])
        self.assertEqual(code, 0)
        shown = json.loads(out)
        self.assertEqual(shown["values"]["routing"], {"default": {}, "roles": {"review-plan": {"effort": "low"}}})
        self.assertEqual(shown["sources"]["routing"], "cli")

    def test_the_validator_s_two_modes(self) -> None:
        data = {"default": {"model": "m", "x": 1}, "roles": {"nope": {}}, "extra": 1}
        config, ignored = routing.validate_routing_mapping(
            data, path="p", where="routing.", require_schema_version=False, unknown="ignore")
        self.assertEqual(ignored, ["routing.extra", "routing.default.x", "routing.roles.nope"])
        self.assertEqual(dict(config.default), {"model": "m"})
        self.assertEqual(dict(config.roles), {})
        with self.assertRaises(RoutingConfigError):
            routing.validate_routing_mapping({"schema_version": 1, **data}, path="p")


class ShowAndPathTest(_TempSettings):
    def test_path(self) -> None:
        code, out, _err = _main(["--settings", str(self.path), "settings", "path"])
        self.assertEqual((code, out), (0, f"{self.path}\n"))
        code, out, _err = _main(["--json", "--settings", str(self.path), "settings", "path"])
        self.assertEqual(json.loads(out), {"path": str(self.path)})
        with mock.patch.dict(os.environ, {"WORKFLOW_CONTROLLER_SETTINGS": str(self.path)}):
            code, out, _err = _main(["settings", "path"])
        self.assertEqual(out, f"{self.path}\n")
        self.assertFalse(self.path.exists())

    def test_show_names_each_source_and_writes_nothing(self) -> None:
        _write(self.path, {"run": {"max_steps": 7}})
        before = self.path.read_bytes()
        code, out, _err = _main(["--timeout", "120", "--settings", str(self.path), "settings", "show"])
        self.assertEqual(code, 0)
        self.assertIn(f"settings file: {self.path}\n", out)
        self.assertIn("run.max_steps = 7 (file)", out)
        self.assertIn("worker.timeout_seconds = 120 (cli)", out)
        self.assertIn("follow.heartbeat_seconds = 30 (default)", out)
        self.assertIn('routing = {"default": {}, "roles": {}} (default)', out)
        self.assertEqual(self.path.read_bytes(), before)
        code, out, _err = _main(["--json", "--settings", str(self.path), "settings", "show"])
        shown = json.loads(out)
        self.assertEqual(shown["values"]["run.max_steps"], 7)
        self.assertEqual(shown["sources"]["run.max_steps"], "file")
        self.assertEqual(shown["sha256"], hashlib.sha256(before).hexdigest())
        self.assertTrue(shown["exists"])

    def test_show_of_a_missing_file_does_not_create_it(self) -> None:
        code, out, _err = _main(["--settings", str(self.path), "settings", "show"])
        self.assertEqual(code, 0)
        self.assertIn("(not created yet)", out)
        self.assertFalse(self.path.parent.exists())


def _python(code: str, *, env: dict | None = None) -> subprocess.Popen:
    prelude = f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); from pathlib import Path\n"
    return subprocess.Popen([sys.executable, "-c", prelude + code], cwd=REPO_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _finish(test: unittest.TestCase, proc: subprocess.Popen) -> str:
    out, err = proc.communicate(timeout=60)
    test.assertEqual(proc.returncode, 0, err)
    return out


#: How long a test holds the lock so a child process reaches it first.
_LOCK_HOLD_SECONDS = 0.5


class LockTest(_TempSettings):
    """I4: the whole read-modify-write runs under the lock, re-reading
    the file's bytes there."""

    def test_concurrent_fills_leave_one_valid_file(self) -> None:
        procs = [_python(f"from controller import settings; settings.fill(Path({str(self.path)!r}))")
                 for _ in range(4)]
        for proc in procs:
            _finish(self, proc)
        self.assertEqual(_read(self.path), _defaults_file())
        self.assertEqual([p.name for p in self.path.parent.iterdir() if p.name.endswith(".tmp")], [])

    def _under_held_lock(self, child_code: str, edit) -> None:
        settings.fill(self.path)
        with runtime.settings_lock(self.path):
            proc = _python(child_code)
            time.sleep(_LOCK_HOLD_SECONDS)
            data = _read(self.path)
            edit(data)
            runtime.write_settings_atomically(self.path, data)
        _finish(self, proc)

    def test_the_fill_re_reads_under_the_lock(self) -> None:
        def edit(data: dict) -> None:
            data["run"]["max_steps"] = 7
            del data["follow"]["replay_events"]
            del data["_defaults_written"]["follow.replay_events"]

        self._under_held_lock(f"from controller import settings; settings.fill(Path({str(self.path)!r}))", edit)
        data = _read(self.path)
        self.assertEqual(data["run"]["max_steps"], 7)
        self.assertEqual(data["follow"]["replay_events"], 20)

    def test_clean_re_reads_under_the_lock(self) -> None:
        def edit(data: dict) -> None:
            data["run"]["max_steps"] = 7
            data["late"] = 1

        self._under_held_lock(f"from controller import settings; settings.clean(Path({str(self.path)!r}))", edit)
        data = _read(self.path)
        self.assertEqual(data["run"]["max_steps"], 7)
        self.assertNotIn("late", data)

    def test_clean_racing_a_fill_keeps_both_changes(self) -> None:
        data = _defaults_file()
        del data["follow"]["replay_events"]
        del data["_defaults_written"]["follow.replay_events"]
        data["bogus"] = 1
        _write(self.path, data)
        with runtime.settings_lock(self.path):
            fill = _python(f"from controller import settings; settings.fill(Path({str(self.path)!r}))")
            clean = _python(f"from controller import settings; settings.clean(Path({str(self.path)!r}))")
            time.sleep(_LOCK_HOLD_SECONDS)
        _finish(self, fill)
        _finish(self, clean)
        self.assertEqual(_read(self.path), _defaults_file())


class IsolationTest(unittest.TestCase):
    """I5: every test process runs with ``XDG_CONFIG_HOME`` redirected and
    ``WORKFLOW_CONTROLLER_SETTINGS`` removed (``tests/__init__.py``)."""

    def test_this_process_is_isolated(self) -> None:
        self.assertEqual(os.environ.get("XDG_CONFIG_HOME"), tests.ISOLATED_XDG_CONFIG_HOME)
        self.assertNotIn("WORKFLOW_CONTROLLER_SETTINGS", os.environ)
        resolved = settings.resolve_path()
        self.assertTrue(resolved.is_relative_to(tests.ISOLATED_XDG_CONFIG_HOME))
        self.assertNotEqual(resolved, settings.resolve_path(None, env={}, home=Path.home()))

    def _child_env(self, sentinel: Path, parent_xdg: Path) -> dict:
        env = dict(os.environ)
        env["WORKFLOW_CONTROLLER_SETTINGS"] = str(sentinel)
        env["XDG_CONFIG_HOME"] = str(parent_xdg)
        return env

    def test_the_operator_s_file_and_a_sentinel_are_never_touched(self) -> None:
        """A child test process started with ``WORKFLOW_CONTROLLER_SETTINGS``
        naming a sentinel and ``XDG_CONFIG_HOME`` naming a parent directory
        fills, shows and cleans its settings with an audit hook that fails
        on any open of the operator's file, its directory's files or the
        sentinel. The sentinel holds invalid JSON, so reading it would
        refuse, and its bytes stay the same; a missing sentinel stays
        missing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            real_dir = Path.home() / ".config" / "workflow-controller"
            for index, sentinel_bytes in enumerate((b"{not json", None)):
                sentinel = tmp_path / f"sentinel-{index}" / "settings.json"
                if sentinel_bytes is not None:
                    _write(sentinel, sentinel_bytes)
                parent_xdg = tmp_path / "parent-xdg"
                parent_xdg.mkdir(exist_ok=True)
                code = (
                    "import os, tests\n"
                    f"watched = ({str(real_dir)!r}, {str(sentinel.parent)!r}, {str(parent_xdg)!r})\n"
                    "def hook(event, args):\n"
                    "    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):\n"
                    "        name = os.fsdecode(args[0])\n"
                    "        if any(name == w or name.startswith(w + os.sep) for w in watched):\n"
                    "            os.write(2, ('touched ' + name).encode()); os._exit(3)\n"
                    "sys.addaudithook(hook)\n"
                    "from controller import cli, settings\n"
                    "assert cli.main(['settings', 'clean']) == 0\n"
                    "assert cli.main(['settings', 'show']) == 0\n"
                    "settings.fill(settings.resolve_path())\n"
                    "print(settings.resolve_path())\n"
                )
                with self.subTest(sentinel_exists=sentinel_bytes is not None):
                    out = _finish(self, _python(code, env=self._child_env(sentinel, parent_xdg)))
                    printed = Path(out.strip().splitlines()[-1])
                    self.assertTrue(printed.name == "settings.json" and
                                    "workflow-controller-tests-config-" in str(printed), printed)
                    self.assertFalse(printed.exists())  # the child's own temporary directory is gone
                    if sentinel_bytes is None:
                        self.assertFalse(sentinel.exists())
                    else:
                        self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
                    self.assertEqual(list(parent_xdg.iterdir()), [])

    def test_a_direct_unittest_run_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "sentinel.json"
            parent_xdg = Path(tmp) / "xdg"
            parent_xdg.mkdir()
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "tests.test_settings.IsolationTest.test_this_process_is_isolated"],
                cwd=REPO_ROOT, env=self._child_env(sentinel, parent_xdg), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(sentinel.exists())
            self.assertEqual(list(parent_xdg.iterdir()), [])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# CP2 (Design B): the process-wide leaf values.
# ---------------------------------------------------------------------------


def _completed(argv, *, stdout=b"", returncode=0):
    return subprocess.CompletedProcess(list(argv), returncode, stdout=stdout, stderr=b"")


class ProcessDefaultsTest(unittest.TestCase):
    """The four leaf settings with no options path are process-wide values
    each leaf module reads at call time (LPR5-002): a runner or forge built
    before :func:`settings.apply_process_defaults` still applies the value
    set later, and the module constant stays the built-in default."""

    def setUp(self) -> None:
        self.enterContext(settings.process_defaults({}))

    def _run_timeouts(self, module, call) -> list:
        seen = []

        def run(argv, **kwargs):
            seen.append(kwargs.get("timeout"))
            return _completed(argv)

        with mock.patch.object(module.subprocess, "run", run):
            call()
        return seen

    def test_git_seconds_reaches_the_runner_built_at_import_and_a_forge_built_before(self) -> None:
        from controller import forge, gitrepo
        built_before = forge.GhForge("owner/name")
        calls = (lambda: gitrepo._DEFAULT_RUNNER(["git", "--version"]),
                 lambda: built_before._runner(["gh", "--version"]))
        self.assertEqual(self._run_timeouts(gitrepo, lambda: [call() for call in calls]), [600, 600])
        with settings.process_defaults({"timeouts.git_seconds": 75}):
            self.assertEqual(self._run_timeouts(gitrepo, lambda: [call() for call in calls]), [75, 75])
        # An explicit `timeout=` keeps its meaning.
        explicit = gitrepo.subprocess_runner(timeout=5)
        with settings.process_defaults({"timeouts.git_seconds": 75}):
            self.assertEqual(self._run_timeouts(gitrepo, lambda: explicit(["git", "--version"])), [5])

    def test_pr_list_limit_reaches_gh_pr_list(self) -> None:
        from controller import forge
        argvs = []

        def runner(argv):
            argvs.append(list(argv))
            return _completed(argv, stdout=b"[]")

        built_before = forge.GhForge("owner/name", runner=runner)
        with settings.process_defaults({"forge.pr_list_limit": 123}):
            self.assertEqual(built_before.list_prs("feature"), [])
        built_before.list_prs("feature")
        limits = [argv[argv.index("--limit") + 1] for argv in argvs]
        self.assertEqual(limits, ["123", "200"])

    def test_release_command_seconds_reaches_the_policy_commands(self) -> None:
        from controller import release_txn
        call = lambda: release_txn.run_command(["true"], {}, Path("."))  # noqa: E731
        with settings.process_defaults({"timeouts.release_command_seconds": 90}):
            self.assertEqual(self._run_timeouts(release_txn, call), [90])
        self.assertEqual(self._run_timeouts(release_txn, call), [1800])

    def test_workflow_query_seconds_reaches_the_query(self) -> None:
        from types import SimpleNamespace

        from controller import workflow_contract
        seen = []

        def execute(private_dir, sources, script, args, *, root, timeout, context):
            seen.append(timeout)
            return _completed(["query"])

        contract = SimpleNamespace(release="2.6.0", query_script_sha256="0" * 64)
        query = lambda: workflow_contract._run_query(  # noqa: E731
            Path("."), contract, workflow_contract.STATE_SCRIPT, [], query="q", work_item_id="w")
        with mock.patch.object(workflow_contract, "_verified_sources", return_value={}), \
                mock.patch.object(workflow_contract, "_copy_and_execute", execute):
            with settings.process_defaults({"timeouts.workflow_query_seconds": 45}):
                query()
            query()
        self.assertEqual(seen, [45, 120.0])

    def test_process_defaults_restores_what_was_there(self) -> None:
        from controller import gitrepo
        with settings.process_defaults({"timeouts.git_seconds": 31}):
            with settings.process_defaults({"timeouts.git_seconds": 32}):
                self.assertEqual(gitrepo.timeout_seconds(), 32)
            self.assertEqual(gitrepo.timeout_seconds(), 31)
        self.assertEqual(gitrepo.timeout_seconds(), gitrepo.DEFAULT_TIMEOUT_SECONDS)

    def test_apply_process_defaults_has_one_production_caller(self) -> None:
        """``cli._apply_settings`` is the only production call, and ``cli``'s
        ``main`` path (``_dispatch``) its only caller -- once per invocation,
        before any thread starts (the ``--follow`` renderer starts in
        dispatch)."""
        import ast
        calls: dict[str, list[tuple[str, str]]] = {"apply_process_defaults": [], "_apply_settings": []}
        for path in sorted((REPO_ROOT / "controller").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    if isinstance(node, ast.Call):
                        func = node.func
                        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                        if name in calls:
                            calls[name].append((path.name, function.name))
        self.assertEqual(calls["apply_process_defaults"], [("cli.py", "_apply_settings")])
        self.assertEqual({site for site in calls["_apply_settings"]}, {("cli.py", "_dispatch")})

    def test_main_applies_the_files_values(self) -> None:
        from controller import forge, gitrepo, release_txn, workflow_contract
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(tmp) / "settings.json"
        _write(path, {"timeouts": {"git_seconds": 61, "release_command_seconds": 62,
                                   "workflow_query_seconds": 63}, "forge": {"pr_list_limit": 64}})
        repo = fixtures.build_target_git_repo(Path(tmp) / "repo")
        code, out, err = _main(["--runtime-dir", str(Path(tmp) / "runtime"), "--settings", str(path),
                                "follow", str(repo)])
        self.assertEqual(code, 0, err)
        self.assertEqual((gitrepo.timeout_seconds(), release_txn.command_timeout_seconds(),
                          workflow_contract.query_timeout_seconds(), forge.pr_list_limit()), (61, 62, 63, 64))
        # `follow` is read-only: the file was loaded, never filled.
        self.assertNotIn("_defaults_written", _read(path))
