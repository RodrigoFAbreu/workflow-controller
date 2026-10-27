"""A fallback chain per role (H9a, ``workflow-controller-fallback-chain``): the
shape, its validation at load, the resolver and the family count. Nothing here
selects a later entry, launches a worker or needs a real ``claude``."""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import harness, review_mode, routing, settings  # noqa: E402
from controller.errors import RoutingConfigError, SettingsError  # noqa: E402
from tests import test_review_mode as trm  # noqa: E402
from tests import test_settings as ts  # noqa: E402

LANE = Path(__file__).resolve().parent / "fixtures" / "lane_routing.json"
ROLE = routing.REVIEW_PLAN
FIRST = {"model": "claude-opus-5-5", "effort": "medium"}
SONNET = {"harness": "claude-code", "model": "claude-sonnet-5-5"}


def config_text(entry: dict, default: dict | None = None) -> str:
    data: dict = {"schema_version": 1, "roles": {ROLE: entry}}
    if default is not None:
        data["default"] = default
    return json.dumps(data)


def parse(entry: dict, default: dict | None = None) -> routing.RoutingConfig:
    return routing.parse_routing_config(config_text(entry, default), path="r.json")


def refused(entry: dict, default: dict | None = None) -> RoutingConfigError:
    with tempfile.TemporaryDirectory():
        try:
            parse(entry, default)
        except RoutingConfigError as exc:
            return exc
    raise AssertionError(f"{entry} loaded")


def codex_registry() -> mock._patch:
    """``codex`` also takes the worker role, with Claude's models and efforts."""
    patched = dataclasses.replace(
        harness.CODEX, roles=(harness.WORKER, harness.REVIEWER), models=harness.CLAUDE_CODE.models,
        efforts=harness.CLAUDE_CODE.efforts)
    return mock.patch.object(harness, "ADAPTERS", {harness.CLAUDE_CODE.name: harness.CLAUDE_CODE,
                                                   patched.name: patched})


class UnchangedTest(unittest.TestCase):
    """Nothing that loads today changes."""

    def test_a_chainless_routing_has_one_entry_equal_to_resolve(self) -> None:
        lane = routing.load_routing_config(LANE)
        self.assertEqual(lane.chains, {})
        for options in (routing.NO_OVERRIDES, routing.RoutingOptions(config=lane)):
            for role in sorted(routing.ROLES):
                self.assertEqual(options.chain(role), (options.resolve(role),))
        self.assertEqual(review_mode.worker_families(routing.NO_OVERRIDES), frozenset({"anthropic"}))
        self.assertEqual(review_mode.worker_families(routing.RoutingOptions(config=lane)),
                         frozenset({"anthropic"}))

    def test_the_mapping_written_back_is_the_one_parsed(self) -> None:
        raw = json.loads(LANE.read_text(encoding="utf-8"))
        raw.pop("schema_version")
        self.assertEqual(settings.routing_mapping(routing.load_routing_config(LANE)), raw)

    def test_a_settings_section_with_no_fallback_is_unchanged(self) -> None:
        section = {"default": {"model": "claude-sonnet-5-5"}, "roles": {ROLE: dict(FIRST)}}
        config, ignored = routing.validate_routing_mapping(
            section, path="s", where="routing.", require_schema_version=False, unknown=routing.UNKNOWN_IGNORE)
        self.assertEqual((ignored, config.chains), ([], {}))
        self.assertEqual(settings.routing_mapping(config), section)


class LoadsTest(unittest.TestCase):
    ENTRY = {**FIRST, "fallback": [dict(SONNET)]}

    def check_chain(self, config: routing.RoutingConfig) -> None:
        options = routing.RoutingOptions(config=config)
        first, second = options.chain(ROLE)
        self.assertEqual(first, options.resolve(ROLE))
        self.assertEqual((first.model, first.model_source), ("claude-opus-5-5", routing.SOURCE_CONFIG_ROLE))
        self.assertEqual((second.harness, second.model, second.effort),
                         ("claude-code", "claude-sonnet-5-5", None))
        self.assertEqual((second.harness_source, second.model_source, second.effort_source),
                         (routing.SOURCE_CONFIG_CHAIN, routing.SOURCE_CONFIG_CHAIN, routing.SOURCE_INHERIT))
        self.assertTrue(second.single_agent)
        self.assertEqual(len(options.chain(routing.REVIEW_IMPLEMENTATION)), 1)

    def test_from_a_routing_config_file(self) -> None:
        config = parse(self.ENTRY)
        self.check_chain(config)
        self.assertEqual(settings.routing_mapping(config), {"default": {}, "roles": {ROLE: self.ENTRY}})

    def test_from_a_settings_section(self) -> None:
        section = {"roles": {ROLE: copy.deepcopy(self.ENTRY)}}
        config, ignored = routing.validate_routing_mapping(
            section, path="s", where="routing.", require_schema_version=False, unknown=routing.UNKNOWN_IGNORE)
        self.assertEqual(ignored, [])
        self.check_chain(config)
        self.assertEqual(settings.routing_mapping(config)["roles"], {ROLE: self.ENTRY})

    def test_a_chain_may_stand_without_a_first_route(self) -> None:
        config = parse({"fallback": [dict(SONNET, effort="high")]})
        later = routing.RoutingOptions(config=config).chain(ROLE)[1]
        self.assertEqual((later.effort, later.effort_source), ("high", routing.SOURCE_CONFIG_CHAIN))
        self.assertEqual(settings.routing_mapping(config)["roles"][ROLE]["fallback"][0]["effort"], "high")

    def test_a_two_family_chain_loads_with_a_second_worker_harness(self) -> None:
        with codex_registry():
            config = parse({"fallback": [{"harness": "codex", "model": "claude-sonnet-5-5"}]})
            chain = routing.RoutingOptions(config=config).chain(ROLE)
            self.assertEqual([route.harness for route in chain], ["claude-code", "codex"])

    def test_a_fallback_entry_equal_to_the_first_route_loads(self) -> None:
        entry = {"harness": "claude-code", "model": "claude-opus-5-5", "effort": "medium"}
        config = parse({**FIRST, "harness": "claude-code", "fallback": [entry]})
        self.assertEqual(len(routing.RoutingOptions(config=config).chain(ROLE)), 2)


class RefusedTest(unittest.TestCase):
    def check(self, fallback: object, *words: str, **evidence: object) -> RoutingConfigError:
        exc = refused({"fallback": fallback})
        for word in words:
            self.assertIn(word, exc.message)
        for key, value in evidence.items():
            self.assertEqual(exc.evidence[key], value, exc.evidence)
        return exc

    def test_malformed_lists_and_entries(self) -> None:
        self.check([], f"roles.{ROLE}.fallback", "non-empty list")
        self.check("claude-code", "non-empty list")
        self.check({"harness": "claude-code"}, "non-empty list")
        self.check(["claude-code"], f"roles.{ROLE}.fallback[1]", "object", entry=1)
        self.check([{"model": "m"}], "fallback[1]", "harness")
        self.check([{"harness": "claude-code"}], "fallback[1]", "model")
        self.check([dict(SONNET, speed="fast")], "fallback[1].speed", "not a key", unknown_keys=["speed"])
        self.check([dict(SONNET, model="")], "fallback[1].model")
        self.check([dict(SONNET, effort="-x")], "fallback[1].effort")

    def test_the_harness_is_judged_for_the_role(self) -> None:
        self.check([{"harness": "nope", "model": "m"}], "fallback[1].harness", "nope")
        # ``codex`` is a reviewer only in the shipped registry.
        self.check([{"harness": "codex", "model": "m"}], "fallback[1].harness", "codex", entry=1, role=ROLE)

    def test_a_repeated_fallback_entry(self) -> None:
        self.check([dict(SONNET), dict(SONNET)], "fallback[2]", "repeats", entry=2)

    def test_default_has_no_chain(self) -> None:
        exc = refused({}, default={"fallback": [dict(SONNET)]})
        self.assertIn("default.fallback", exc.message)
        self.assertIn("not allowed", exc.message)

    def test_a_model_or_effort_the_harness_does_not_declare_is_refused_naming_the_entry(self) -> None:
        for entry, word in (({"harness": "claude-code", "model": "gpt-9"}, "gpt-9"),
                            ({**SONNET, "effort": "turbo"}, "turbo")):
            config = parse({"fallback": [dict(SONNET), entry, dict(SONNET, effort="low")]})
            with self.assertRaises(RoutingConfigError) as ctx:
                routing.RoutingOptions(config=config)
            self.assertIn(word, ctx.exception.message)
            self.assertIn("fallback entry 2", ctx.exception.message)
            self.assertEqual((ctx.exception.evidence["role"], ctx.exception.evidence["entry"]), (ROLE, 2))

    def test_harness_models_accepts_the_model_again(self) -> None:
        config = parse({"fallback": [{"harness": "claude-code", "model": "gpt-9"}]})
        with self.assertRaises(RoutingConfigError) as ctx:
            routing.RoutingOptions(config=config)
        self.assertIn("harness.models", ctx.exception.message)
        options = routing.RoutingOptions(config=config, harness_models={"claude-code": ("gpt-9",)})
        self.assertEqual(options.chain(ROLE)[1].model, "gpt-9")

    def test_the_settings_section_refuses_an_unknown_key_with_the_dotted_key(self) -> None:
        for where, section in (
                ("fallback[1].speed", {"roles": {ROLE: {"fallback": [dict(SONNET, speed="fast")]}}}),
                ("default.fallback", {"default": {"fallback": [dict(SONNET)]}})):
            with self.assertRaises(RoutingConfigError) as ctx:
                routing.validate_routing_mapping(
                    section, path="s", where="routing.", require_schema_version=False,
                    unknown=routing.UNKNOWN_IGNORE)
            self.assertTrue(ctx.exception.evidence["key"].startswith("routing."), ctx.exception.evidence)
            self.assertIn(where, ctx.exception.evidence["key"])


class SettingsFileTest(ts._TempSettings):
    def put(self, roles: dict) -> None:
        settings.fill(self.path)
        data = ts._read(self.path)
        data["routing"] = {"roles": roles}
        ts._write(self.path, data)

    def test_the_chain_loads_with_no_warning_and_clean_keeps_it(self) -> None:
        roles = {ROLE: {**FIRST, "fallback": [dict(SONNET)]}}
        self.put(roles)
        loaded, err = self.stderr_of(settings.load, self.path)
        self.assertEqual((err, loaded.unknown), ("", ()))
        self.assertEqual(len(routing.RoutingOptions(config=loaded.routing_config).chain(ROLE)), 2)
        before = self.path.read_bytes()
        _loaded, removed = settings.clean(self.path)
        self.assertEqual(removed, [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_an_unknown_role_named_like_the_key_is_removed_and_the_chain_survives(self) -> None:
        roles = {ROLE: {**FIRST, "fallback": [dict(SONNET)]}, f"{ROLE}.fallback": {"model": "m"}}
        self.put(roles)
        loaded, err = self.stderr_of(settings.load, self.path)
        self.assertEqual(list(loaded.unknown), [f"routing.roles.{ROLE}.fallback"])
        self.assertEqual(err.count("warning:"), 1)
        _loaded, removed = settings.clean(self.path)
        self.assertEqual(removed, [f"routing.roles.{ROLE}.fallback"])
        self.assertEqual(ts._read(self.path)["routing"]["roles"],
                         {ROLE: {**FIRST, "fallback": [dict(SONNET)]}})
        self.assertEqual(settings.load(self.path).unknown, ())

    def test_an_unknown_key_in_an_entry_is_exit_20_naming_the_key(self) -> None:
        self.put({ROLE: {"fallback": [dict(SONNET, speed="fast")]}})
        before = self.path.read_bytes()
        with self.assertRaises(SettingsError) as ctx:
            settings.load(self.path)
        self.assertEqual(ctx.exception.evidence["key"], f"routing.roles.{ROLE}.fallback[1].speed")
        code, _out, err = ts._main(["--settings", str(self.path), "settings", "show"])
        self.assertEqual(code, 20, err)
        self.assertIn("fallback[1].speed", err)
        self.assertEqual(self.path.read_bytes(), before)


class PrecedenceTest(unittest.TestCase):
    def test_the_command_line_changes_entry_zero_only(self) -> None:
        config = parse({**FIRST, "fallback": [dict(SONNET, effort="low")]})
        plain = routing.RoutingOptions(config=config).chain(ROLE)
        for kwargs in ({"cli_role_models": {ROLE: "claude-haiku-5-5"}}, {"cli_role_efforts": {ROLE: "high"}},
                       {"cli_role_harnesses": {ROLE: "claude-code"}}, {"cli_model": "claude-haiku-5-5"},
                       {"cli_effort": "high"}):
            with self.subTest(kwargs=sorted(kwargs)):
                chain = routing.RoutingOptions(config=config, **kwargs).chain(ROLE)
                self.assertNotEqual(chain[0], plain[0])
                self.assertEqual(chain[1:], plain[1:])
                self.assertEqual(chain[0], routing.RoutingOptions(config=config, **kwargs).resolve(ROLE))


class FamiliesTest(unittest.TestCase):
    def setUp(self) -> None:
        patch = codex_registry()
        patch.start()
        self.addCleanup(patch.stop)
        self.options = routing.RoutingOptions(config=parse(
            {"fallback": [{"harness": "codex", "model": "claude-sonnet-5-5"}]}))

    def test_a_fallback_entry_joins_the_worker_families(self) -> None:
        self.assertEqual(review_mode.worker_families(self.options), frozenset({"anthropic", "openai"}))
        self.assertEqual(review_mode.worker_families(routing.NO_OVERRIDES), frozenset({"anthropic"}))

    def test_the_mixed_lane_is_refused_by_every_mode_but_manual(self) -> None:
        workers = review_mode.worker_families(self.options)
        reviewer = trm.reviewer("codex")
        for mode in (review_mode.MODE_AUTO, review_mode.MODE_CROSS_MODEL, review_mode.MODE_SINGLE_PROVIDER):
            self.assertEqual(review_mode.choose(mode, reviewer, "plan", None, workers).kind, "refused", mode)
        self.assertEqual(review_mode.choose(review_mode.MODE_MANUAL, reviewer, "plan", None, workers),
                         review_mode.GATE)

    def test_the_reason_names_the_fallback_entry_and_judges_the_pair_over_the_chain(self) -> None:
        text = review_mode.mixed_lane_reason(self.options, trm.reviewer("codex"), "plan")
        self.assertIn("falls back to codex (openai)", text)
        self.assertIn("of one family", text)
        other = review_mode.mixed_lane_reason(self.options, trm.reviewer("claude"), "plan")
        self.assertIn("of one family", other)  # claude (anthropic) is the first route's family
        implementation = review_mode.mixed_lane_reason(self.options, trm.reviewer("codex"), "implementation")
        self.assertNotIn("falls back", implementation)
        self.assertIn("that pair is distinct", implementation)

    def test_the_shipped_registry_changes_nothing(self) -> None:
        text = review_mode.mixed_lane_reason(routing.NO_OVERRIDES, trm.reviewer("codex"), "plan")
        self.assertNotIn("falls back", text)


if __name__ == "__main__":
    unittest.main()
