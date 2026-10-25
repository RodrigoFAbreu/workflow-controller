"""Tests for role-based worker routing (``controller.routing``,
``workflow-controller-automatic-lifecycle-orchestration`` CP6).

Covers the routing table (the five named lifecycle roles and the final
self-review pass at ``claude-opus-5-5``/``xhigh``, the review roles and
the self-review pass single-agent, the three unnamed roles inheriting),
role derivation from durable state, the per-field precedence ladder
(each level beats the one below it, model and effort independently),
``single_agent`` never being overridable, and the config file's refusals.
The worker's command line is ``tests/test_worker.py``'s and
``tests/test_job.py``'s; the CLI options are ``tests/test_cli.py``'s.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import decision, harness, routing  # noqa: E402
from controller.errors import ControllerError, RoutingConfigError  # noqa: E402

#: The roles the milestone scope names, at Opus 5.5 ``xhigh`` by default,
#: plus the final self-review pass of ``/milestone-implement``.
NAMED_ROLES = (
    "milestone-implement", "apply-plan-review", "apply-implementation-review", "review-plan",
    "review-implementation", "apply-functional-review", "apply-pr-review",
)
#: The validator roles (cleanup-2 D2): not Opus; each has its own built-in route.
VALIDATOR_ROUTES = {
    "satisfy-gate": ("claude-haiku-5-5", "high"),
    "satisfy-gate-acceptance": ("claude-sonnet-5-5", "medium"),
}
#: The tester (workflow-controller-automated-functional-review D2): a
#: Controller-run job, not a lifecycle worker, so no command stem maps to it;
#: Sonnet at medium, single-agent.
TESTER_ROLE = "functional-tester"
SELF_REVIEW_ROLE = "milestone-implement-self-review"
SINGLE_AGENT_ROLES = frozenset({SELF_REVIEW_ROLE, "review-plan", "review-implementation",
                                *VALIDATOR_ROUTES, "functional-tester"})
INHERIT_ROLES = ("milestone-plan", "record-manual-plan-review", "record-manual-implementation-review",
                 "prepare-functional-review")
#: The roles a protocol target adds (orchestration-protocol-v1 C.3, and the
#: validator of gate-policy D4): the legacy command map names none of them.
PROTOCOL_ONLY_ROLES = ("prepare-functional-review", "apply-functional-review", *VALIDATOR_ROUTES, "apply-pr-review")


def _config(**data) -> routing.RoutingConfig:
    return routing.parse_routing_config(json.dumps({"schema_version": 1, **data}), path="<test>")


class RoutingTableTest(unittest.TestCase):
    def test_the_role_set_is_closed_and_exactly_the_table(self) -> None:
        self.assertEqual(routing.ROLES, frozenset(routing.ROLE_ROUTES))
        self.assertEqual(routing.ROLES, frozenset({*NAMED_ROLES, *VALIDATOR_ROUTES, SELF_REVIEW_ROLE, *INHERIT_ROLES, TESTER_ROLE}))

    def test_each_named_role_defaults_to_opus_5_5_at_xhigh(self) -> None:
        for role in (*NAMED_ROLES, SELF_REVIEW_ROLE):
            with self.subTest(role=role):
                route = routing.resolve_route(role)
                self.assertEqual((route.model, route.effort), ("claude-opus-5-5", "xhigh"))
                self.assertEqual((route.model_source, route.effort_source), ("default", "default"))
        self.assertEqual((routing.DEFAULT_MODEL, routing.DEFAULT_EFFORT), ("claude-opus-5-5", "xhigh"))

    def test_the_functional_tester_is_sonnet_medium_single_agent_and_overridable(self) -> None:
        route = routing.resolve_route(TESTER_ROLE)
        self.assertEqual((route.model, route.effort), ("claude-sonnet-5-5", "medium"))
        self.assertEqual((route.model_source, route.effort_source), ("default", "default"))
        self.assertTrue(route.single_agent)
        self.assertEqual(route.disallowed_tools, routing.SUBAGENT_TOOLS)
        self.assertNotIn(TESTER_ROLE, routing.ROLE_BY_COMMAND_STEM.values())
        by_option = routing.RoutingOptions(cli_role_models={TESTER_ROLE: "m-opt"},
                                           cli_role_efforts={TESTER_ROLE: "e-opt"}).resolve(TESTER_ROLE)
        self.assertEqual((by_option.model, by_option.effort, by_option.model_source, by_option.effort_source),
                         ("m-opt", "e-opt", "role-cli", "role-cli"))
        config = _config(roles={TESTER_ROLE: {"model": "m-file"}}, default={"effort": "e-default"})
        by_file = routing.resolve_route(TESTER_ROLE, config=config)
        self.assertEqual((by_file.model, by_file.model_source), ("m-file", "config-role"))
        self.assertEqual((by_file.effort, by_file.effort_source), ("e-default", "config-default"))
        self.assertTrue(by_file.single_agent)

    def test_the_validator_roles_have_their_own_single_agent_routes(self) -> None:
        for role, (model, effort) in VALIDATOR_ROUTES.items():
            with self.subTest(role=role):
                route = routing.resolve_route(role)
                self.assertEqual((route.model, route.effort), (model, effort))
                self.assertEqual((route.model_source, route.effort_source), ("default", "default"))
                self.assertTrue(route.single_agent)
                self.assertEqual(route.disallowed_tools, routing.SUBAGENT_TOOLS)

    def test_each_validator_route_is_overridable_alone(self) -> None:
        for role in VALIDATOR_ROUTES:
            other = next(r for r in VALIDATOR_ROUTES if r != role)
            with self.subTest(role=role):
                route = routing.resolve_route(role, cli_role_models={role: "claude-opus-5-5"},
                                              cli_role_efforts={role: "xhigh"})
                self.assertEqual((route.model, route.effort), ("claude-opus-5-5", "xhigh"))
                self.assertEqual((route.model_source, route.effort_source), ("role-cli", "role-cli"))
                kept = routing.resolve_route(other, cli_role_models={role: "claude-opus-5-5"})
                self.assertEqual((kept.model, kept.effort), VALIDATOR_ROUTES[other])

    def test_the_self_review_role_and_the_review_roles_are_single_agent(self) -> None:
        for role in routing.ROLES:
            with self.subTest(role=role):
                route = routing.resolve_route(role)
                self.assertIs(route.single_agent, role in SINGLE_AGENT_ROLES)
                self.assertEqual(route.disallowed_tools,
                                 routing.SUBAGENT_TOOLS if role in SINGLE_AGENT_ROLES else ())

    def test_the_subagent_tools_name_the_installed_clis_delegation_tools(self) -> None:
        # `Skill` joined after CP9's live probe ran a forked skill in a
        # subagent on a route that disallowed only `Agent` and `Workflow`.
        self.assertEqual(routing.SUBAGENT_TOOLS, ("Agent", "Workflow", "Skill"))

    def test_the_three_unnamed_roles_inherit_both_fields(self) -> None:
        for role in INHERIT_ROLES:
            with self.subTest(role=role):
                route = routing.resolve_route(role)
                self.assertIsNone(route.model)
                self.assertIsNone(route.effort)
                self.assertFalse(route.single_agent)
                self.assertEqual((route.model_source, route.effort_source), ("inherit", "inherit"))

    def test_an_unknown_role_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            routing.resolve_route("review-everything")

    def test_the_record_shape(self) -> None:
        record = routing.resolve_route("review-plan", cli_effort="high").to_record()
        self.assertEqual(record, {
            "role": "review-plan", "model": "claude-opus-5-5", "effort": "high", "harness": "claude-code",
            "single_agent": True, "fresh_session": True,
            "sources": {"model": "default", "effort": "cli", "harness": "default"},
        })
        json.dumps(record)


class RoleDerivationTest(unittest.TestCase):
    def test_every_launchable_command_has_a_role(self) -> None:
        self.assertEqual(set(routing.ROLE_BY_COMMAND_STEM), set(decision.SELECTED_COMMANDS))
        self.assertLessEqual(set(routing.ROLE_BY_COMMAND_STEM.values()), routing.ROLES)
        for phase, _version, token in decision.AUTOMATIC_TRIPLES:
            with self.subTest(phase=phase, token=token):
                self.assertIn(routing.role_for(phase, f"{token} wi-1", False), routing.ROLES)

    def test_milestone_implement_is_the_checkpoint_role_while_a_checkpoint_is_outstanding(self) -> None:
        for registry_complete in (False, None):
            with self.subTest(registry_complete=registry_complete):
                self.assertEqual(
                    routing.role_for("IMPLEMENTING", "/milestone-implement wi-1", registry_complete),
                    "milestone-implement",
                )

    def test_milestone_implement_is_the_self_review_role_for_the_final_pass(self) -> None:
        self.assertEqual(routing.role_for("IMPLEMENTING", "/milestone-implement wi-1", True), SELF_REVIEW_ROLE)
        for registry_complete in (True, False, None):
            with self.subTest(registry_complete=registry_complete):
                self.assertEqual(
                    routing.role_for("SELF_REVIEWING_IMPLEMENTATION", "/milestone-implement wi-1",
                                     registry_complete),
                    SELF_REVIEW_ROLE,
                )

    def test_every_other_command_maps_to_its_own_role_whatever_the_phase(self) -> None:
        for stem, role in routing.ROLE_BY_COMMAND_STEM.items():
            if stem == "milestone-implement":
                continue
            for phase in ("PLANNING", "IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION", decision.NO_PHASE):
                with self.subTest(stem=stem, phase=phase):
                    self.assertEqual(routing.role_for(phase, f"/{stem} wi-1", True), role)
                    self.assertEqual(role, stem)

    def test_an_unroutable_command_is_an_invariant_violation(self) -> None:
        with self.assertRaises(AssertionError):
            routing.role_for("PLANNING", "/prepare-review wi-1", None)


class PrecedenceTest(unittest.TestCase):
    """Each level beats the one below it, per field independently."""

    #: One override per level, for both fields, each value naming its level.
    LEVELS = ("role-cli", "cli", "config-role", "config-default")

    def _resolve(self, role: str, levels: set[str], field: str):
        other = "effort" if field == "model" else "model"
        kwargs: dict = {}
        if "role-cli" in levels:
            kwargs[f"cli_role_{field}s"] = {role: f"{field}-role-cli"}
        if "cli" in levels:
            kwargs[f"cli_{field}"] = f"{field}-cli"
        config: dict = {}
        if "config-role" in levels:
            config["roles"] = {role: {field: f"{field}-config-role"}}
        if "config-default" in levels:
            config["default"] = {field: f"{field}-config-default"}
        if config:
            kwargs["config"] = _config(**config)
        route = routing.resolve_route(role, **kwargs)
        # The other field is untouched by every override of this one.
        builtin = getattr(routing.ROLE_ROUTES[role], other)
        self.assertEqual(getattr(route, other), builtin)
        self.assertEqual(getattr(route, f"{other}_source"), "default" if builtin is not None else "inherit")
        return getattr(route, field), getattr(route, f"{field}_source")

    def test_each_level_beats_every_level_below_it_for_each_field(self) -> None:
        for role in ("review-implementation", "milestone-plan"):
            builtin_route = routing.ROLE_ROUTES[role]
            for field in ("model", "effort"):  # the harness resolves in CP2
                for index, level in enumerate(self.LEVELS):
                    present = set(self.LEVELS[index:])
                    with self.subTest(role=role, field=field, winner=level):
                        self.assertEqual(self._resolve(role, present, field), (f"{field}-{level}", level))
                with self.subTest(role=role, field=field, winner="builtin"):
                    value, source = self._resolve(role, set(), field)
                    builtin = getattr(builtin_route, field)
                    self.assertEqual((value, source),
                                     (builtin, "default" if builtin is not None else "inherit"))

    def test_the_fields_resolve_independently(self) -> None:
        route = routing.resolve_route(
            "apply-implementation-review",
            cli_role_models={"apply-implementation-review": "m-role"},
            config=_config(default={"effort": "e-default"}),
        )
        self.assertEqual((route.model, route.model_source), ("m-role", "role-cli"))
        self.assertEqual((route.effort, route.effort_source), ("e-default", "config-default"))

    def test_another_roles_entries_never_apply(self) -> None:
        route = routing.resolve_route(
            "review-plan",
            cli_role_models={"review-implementation": "other"},
            config=_config(roles={"apply-plan-review": {"model": "other", "effort": "other"}}),
        )
        self.assertEqual((route.model, route.model_source), ("claude-opus-5-5", "default"))
        self.assertEqual((route.effort, route.effort_source), ("xhigh", "default"))

    def test_a_config_routes_an_inherit_role_explicitly(self) -> None:
        route = routing.resolve_route(
            "milestone-plan", config=_config(roles={"milestone-plan": {"model": "claude-sonnet-5"}}),
        )
        self.assertEqual((route.model, route.model_source), ("claude-sonnet-5", "config-role"))
        self.assertEqual((route.effort, route.effort_source), (None, "inherit"))

    def test_routing_options_resolve_like_resolve_route(self) -> None:
        options = routing.RoutingOptions(
            cli_model="m", cli_role_efforts={"review-plan": "low"},
            config=_config(default={"effort": "medium"}),
        )
        self.assertEqual(
            options.resolve("review-plan"),
            routing.resolve_route("review-plan", cli_model="m", cli_role_efforts={"review-plan": "low"},
                                  config=options.config),
        )
        self.assertEqual(routing.NO_OVERRIDES.resolve("review-plan"), routing.resolve_route("review-plan"))


class SingleAgentNotOverridableTest(unittest.TestCase):
    def test_no_override_at_any_level_changes_single_agent(self) -> None:
        for role in routing.ROLES:
            with self.subTest(role=role):
                route = routing.resolve_route(
                    role, cli_model="m", cli_effort="e", cli_role_models={role: "rm"},
                    cli_role_efforts={role: "re"},
                    config=_config(default={"model": "dm", "effort": "de"},
                                   roles={role: {"model": "cm", "effort": "ce"}}),
                )
                self.assertIs(route.single_agent, role in SINGLE_AGENT_ROLES)

    def test_no_override_surface_names_single_agent(self) -> None:
        fields = {field.name for field in routing.RoutingOptions.__dataclass_fields__.values()}
        self.assertNotIn("single_agent", fields)
        for data in ({"default": {"single_agent": False}},
                     {"roles": {"review-plan": {"single_agent": False}}},
                     {"single_agent": False}):
            with self.subTest(data=data), self.assertRaises(RoutingConfigError):
                _config(**data)


class ValueCheckTest(unittest.TestCase):
    def test_a_value_is_a_non_empty_string_not_beginning_with_a_dash(self) -> None:
        self.assertEqual(routing.check_value("claude-opus-5-5"), "claude-opus-5-5")
        for bad in ("", "-c", "--resume", None, 5, ["x"]):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                routing.check_value(bad)

    def test_role_assignments(self) -> None:
        self.assertEqual(routing.parse_role_assignment("review-plan=m=x"), ("review-plan", "m=x"))
        for bad in ("review-plan", "nobody=m", "review-plan=", "review-plan=--resume", "=m"):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                routing.parse_role_assignment(bad)

    def test_routing_options_refuse_unknown_roles_and_unusable_values(self) -> None:
        for kwargs in ({"cli_model": ""}, {"cli_effort": "-x"}, {"cli_role_models": {"nobody": "m"}},
                       {"cli_role_efforts": {"review-plan": ""}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                routing.RoutingOptions(**kwargs)


class HarnessFieldTest(unittest.TestCase):
    """workflow-controller-routing-harness D1-D4: ``harness`` is a third
    routable field, its vocabulary the registry's, validated against the
    adapter's declared roles at load."""

    def test_harness_is_the_third_routable_field(self) -> None:
        self.assertEqual(routing.FIELDS, ("model", "effort", "harness"))

    def test_claude_code_is_accepted_everywhere_a_route_is_named(self) -> None:
        config = _config(default={"harness": "claude-code", "model": "m", "effort": "e"},
                         roles={role: {"harness": "claude-code"} for role in routing.ROLES})
        self.assertEqual(dict(config.default), {"harness": "claude-code", "model": "m", "effort": "e"})
        self.assertEqual(set(config.roles), set(routing.ROLES))
        beside = _config(roles={"review-plan": {"model": "m", "effort": "e", "harness": "claude-code"}})
        self.assertEqual(dict(beside.roles["review-plan"]), {"model": "m", "effort": "e", "harness": "claude-code"})

    def test_the_accepted_pairs_follow_the_registry(self) -> None:
        accepted = {name: sorted(role for role in routing.ROLES
                                 if routing.ADAPTER_ROLE[role] in adapter.roles)
                    for name, adapter in harness.ADAPTERS.items()}
        self.assertEqual(accepted, {"claude-code": sorted(routing.ROLES), "codex": []})
        for role in routing.ROLES:
            self.assertEqual(routing.check_role_harness(role, "claude-code"), "claude-code")

    def test_adapter_role_maps_every_routing_role_to_a_declared_one(self) -> None:
        self.assertEqual(set(routing.ADAPTER_ROLE), set(routing.ROLES))
        self.assertEqual(routing.ADAPTER_ROLE["functional-tester"], harness.FUNCTIONAL_TESTER)
        for role in routing.ROLES - {"functional-tester"}:
            self.assertEqual(routing.ADAPTER_ROLE[role], harness.WORKER)
        for adapter_role in routing.ADAPTER_ROLE.values():
            self.assertIn(adapter_role, harness.CLAUDE_CODE.roles)

    def test_a_later_codex_declaration_flips_exactly_that_row(self) -> None:
        wider = harness.dataclasses.replace(harness.CODEX, roles=(harness.REVIEWER, harness.FUNCTIONAL_TESTER))
        with unittest.mock.patch.dict(harness.ADAPTERS, {"codex": wider}):
            self.assertEqual(routing.check_role_harness("functional-tester", "codex"), "codex")
            with self.assertRaises(ValueError):
                routing.check_role_harness("review-plan", "codex")
            config = _config(roles={"functional-tester": {"harness": "codex"}})
            self.assertEqual(dict(config.roles["functional-tester"]), {"harness": "codex"})

    def test_codex_is_refused_in_a_default_and_in_each_kind_of_role(self) -> None:
        for role in ("milestone-implement", "review-plan", "satisfy-gate", "functional-tester"):
            with self.subTest(role=role), self.assertRaises(RoutingConfigError) as ctx:
                _config(roles={role: {"harness": "codex"}})
            self.assertIn(f"role {role!r} runs as a {routing.ADAPTER_ROLE[role]!r} and harness 'codex' "
                          f"declares only ['reviewer']", ctx.exception.message)
            self.assertEqual({k: ctx.exception.evidence[k] for k in ("role", "adapter_role", "harness", "declared_roles")},
                             {"role": role, "adapter_role": routing.ADAPTER_ROLE[role], "harness": "codex",
                              "declared_roles": ["reviewer"]})
        with self.assertRaises(RoutingConfigError) as ctx:
            _config(default={"harness": "codex"})
        self.assertIn("role 'apply-functional-review'", ctx.exception.message)
        self.assertIn("(and 14 further role(s) the default would apply to)", ctx.exception.message)

    def test_a_default_is_checked_only_against_roles_with_no_harness_of_their_own(self) -> None:
        overrides = {role: {"harness": "claude-code"} for role in routing.ROLES}
        config = _config(default={"harness": "codex"}, roles=overrides)
        self.assertEqual(dict(config.default), {"harness": "codex"})
        overrides.pop("review-plan")
        with self.assertRaises(RoutingConfigError) as ctx:
            _config(default={"harness": "codex"}, roles=overrides)
        self.assertIn("role 'review-plan'", ctx.exception.message)
        self.assertNotIn("further", ctx.exception.message)

    def test_a_value_that_is_not_a_registered_name_is_refused(self) -> None:
        for value in ("claude", "", "-x", 5, None, ["claude-code"], "Claude-Code"):
            with self.subTest(value=value), self.assertRaises(RoutingConfigError) as ctx:
                _config(roles={"review-plan": {"harness": value}})
            self.assertIn("roles.review-plan.harness", ctx.exception.message)
            with self.assertRaises(ValueError):
                routing.check_harness(value)

    def test_the_settings_section_refuses_with_the_dotted_key_and_refuses_the_entry_whole(self) -> None:
        for section, key in (
            ({"roles": {"review-plan": {"model": "m", "harness": "codex"}}}, "routing.roles.review-plan.harness"),
            ({"default": {"harness": "codex"}}, "routing.default.harness"),
            ({"roles": {"review-plan": {"harness": 5}}}, "routing.roles.review-plan.harness"),
            ({"roles": {"review-plan": {"harness": ""}}}, "routing.roles.review-plan.harness"),
            ({"default": {"harness": "-x"}}, "routing.default.harness"),
        ):
            with self.subTest(section=section), self.assertRaises(RoutingConfigError) as ctx:
                routing.validate_routing_mapping(section, path="/s.json", where="routing.",
                                                 require_schema_version=False, unknown=routing.UNKNOWN_IGNORE)
            self.assertEqual(ctx.exception.evidence["key"], key)


class HarnessResolutionTest(unittest.TestCase):
    """workflow-controller-routing-harness D5: the harness resolves by the
    existing precedence minus the global option, independently of model and
    effort."""

    ROLE = "review-plan"

    def _resolve(self, *, role_cli: bool = False, role: bool = False, default: bool = False):
        config = None
        if role or default:
            config = _config(default={"harness": "claude-code"} if default else {},
                             roles={self.ROLE: {"harness": "claude-code"}} if role else {})
        return routing.resolve_route(
            self.ROLE, cli_role_harnesses={self.ROLE: "claude-code"} if role_cli else None, config=config)

    def test_each_level_names_its_source_and_the_built_in_is_claude_code(self) -> None:
        cases = (
            (dict(role_cli=True, role=True, default=True), "role-cli"),
            (dict(role=True, default=True), "config-role"),
            (dict(default=True), "config-default"),
            ({}, "default"),
        )
        for levels, source in cases:
            with self.subTest(source=source):
                route = self._resolve(**levels)
                self.assertEqual((route.harness, route.harness_source), ("claude-code", source))

    def test_a_hand_built_route_defaults_to_claude_code(self) -> None:
        route = routing.ResolvedRoute(role=self.ROLE, model=None, effort=None, single_agent=True,
                                      model_source="inherit", effort_source="inherit")
        self.assertEqual((route.harness, route.harness_source), ("claude-code", "default"))

    def test_the_fields_resolve_independently(self) -> None:
        route = routing.resolve_route(
            self.ROLE, cli_role_models={self.ROLE: "m"},
            config=_config(roles={self.ROLE: {"harness": "claude-code", "effort": "e"}}))
        self.assertEqual((route.model_source, route.effort_source, route.harness_source),
                         ("role-cli", "config-role", "config-role"))

    def test_the_role_harness_option_parses_and_refuses(self) -> None:
        self.assertEqual(routing.parse_role_harness("review-plan=claude-code"), ("review-plan", "claude-code"))
        for text in ("review-plan", "nobody=claude-code", "review-plan=codex", "review-plan=claude",
                     "review-plan=", "review-plan=--resume"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                routing.parse_role_harness(text)

    def test_a_hand_built_option_set_cannot_hold_an_invalid_pair(self) -> None:
        for bad in ({"review-plan": "codex"}, {"nobody": "claude-code"}, {"review-plan": "claude"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                routing.RoutingOptions(cli_role_harnesses=bad)
        options = routing.RoutingOptions(cli_role_harnesses={"review-plan": "claude-code"})
        self.assertEqual(options.resolve("review-plan").harness_source, "role-cli")


class RoutingConfigTest(unittest.TestCase):
    def test_a_full_config_parses(self) -> None:
        config = _config(default={"model": "m", "effort": "e"},
                         roles={"review-plan": {"effort": "max"}, "milestone-plan": {}})
        self.assertEqual(dict(config.default), {"model": "m", "effort": "e"})
        self.assertEqual(dict(config.roles["review-plan"]), {"effort": "max"})
        self.assertEqual(dict(config.roles["milestone-plan"]), {})

    def test_default_and_roles_are_optional(self) -> None:
        config = _config()
        self.assertEqual(dict(config.default), {})
        self.assertEqual(dict(config.roles), {})

    def test_every_malformed_config_is_a_routing_config_error(self) -> None:
        cases = {
            "unparseable": "{not json",
            "not an object": "[1]",
            "missing schema_version": json.dumps({"default": {}}),
            "schema_version 2": json.dumps({"schema_version": 2}),
            "schema_version string": json.dumps({"schema_version": "1"}),
            "schema_version true": json.dumps({"schema_version": True}),
            "schema_version float": json.dumps({"schema_version": 1.0}),
            "unknown top-level key": json.dumps({"schema_version": 1, "defaults": {}}),
            "unknown role": json.dumps({"schema_version": 1, "roles": {"review-everything": {}}}),
            "unknown field key": json.dumps({"schema_version": 1, "default": {"temperature": "1"}}),
            "unknown role field key": json.dumps({"schema_version": 1, "roles": {"review-plan": {"x": "y"}}}),
            "default not an object": json.dumps({"schema_version": 1, "default": "m"}),
            "roles not an object": json.dumps({"schema_version": 1, "roles": ["review-plan"]}),
            "role entry not an object": json.dumps({"schema_version": 1, "roles": {"review-plan": "m"}}),
            "null value": json.dumps({"schema_version": 1, "default": {"model": None}}),
            "empty value": json.dumps({"schema_version": 1, "default": {"effort": ""}}),
            "non-string value": json.dumps({"schema_version": 1, "roles": {"review-plan": {"model": 5}}}),
            "option-like value": json.dumps({"schema_version": 1, "default": {"model": "--resume"}}),
            "duplicate key": '{"schema_version": 1, "default": {"model": "a", "model": "b"}}',
            "duplicate role": '{"schema_version": 1, "roles": {"review-plan": {}, "review-plan": {}}}',
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(RoutingConfigError) as ctx:
                    routing.parse_routing_config(text, path="/cfg.json")
                self.assertIsInstance(ctx.exception, ControllerError)
                self.assertIn("/cfg.json", ctx.exception.message)
                self.assertEqual(ctx.exception.evidence["path"], "/cfg.json")

    def test_load_reads_the_file_and_refuses_an_unreadable_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "routing.json"
            path.write_text(json.dumps({"schema_version": 1, "roles": {"review-plan": {"model": "m"}}}))
            config = routing.load_routing_config(path)
            self.assertEqual(config.path, str(path))
            self.assertEqual(dict(config.roles["review-plan"]), {"model": "m"})
            for unreadable in (Path(td) / "missing.json", Path(td)):
                with self.subTest(path=unreadable), self.assertRaises(RoutingConfigError):
                    routing.load_routing_config(unreadable)
            binary = Path(td) / "binary.json"
            binary.write_bytes(b"\xff\xfe\x00")
            with self.assertRaises(RoutingConfigError):
                routing.load_routing_config(binary)


if __name__ == "__main__":
    unittest.main()
