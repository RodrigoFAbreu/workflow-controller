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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import decision, routing  # noqa: E402
from controller.errors import ControllerError, RoutingConfigError  # noqa: E402

#: The roles the milestone scope names, at Opus 5.5 ``xhigh`` by default,
#: plus the final self-review pass of ``/milestone-implement``.
NAMED_ROLES = (
    "milestone-implement", "apply-plan-review", "apply-implementation-review", "review-plan",
    "review-implementation",
)
SELF_REVIEW_ROLE = "milestone-implement-self-review"
SINGLE_AGENT_ROLES = frozenset({SELF_REVIEW_ROLE, "review-plan", "review-implementation"})
INHERIT_ROLES = ("milestone-plan", "record-manual-plan-review", "record-manual-implementation-review")


def _config(**data) -> routing.RoutingConfig:
    return routing.parse_routing_config(json.dumps({"schema_version": 1, **data}), path="<test>")


class RoutingTableTest(unittest.TestCase):
    def test_the_role_set_is_closed_and_exactly_the_table(self) -> None:
        self.assertEqual(routing.ROLES, frozenset(routing.ROLE_ROUTES))
        self.assertEqual(routing.ROLES, frozenset({*NAMED_ROLES, SELF_REVIEW_ROLE, *INHERIT_ROLES}))

    def test_each_named_role_defaults_to_opus_5_5_at_xhigh(self) -> None:
        for role in (*NAMED_ROLES, SELF_REVIEW_ROLE):
            with self.subTest(role=role):
                route = routing.resolve_route(role)
                self.assertEqual((route.model, route.effort), ("claude-opus-5-5", "xhigh"))
                self.assertEqual((route.model_source, route.effort_source), ("default", "default"))
        self.assertEqual((routing.DEFAULT_MODEL, routing.DEFAULT_EFFORT), ("claude-opus-5-5", "xhigh"))

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
            "role": "review-plan", "model": "claude-opus-5-5", "effort": "high", "single_agent": True,
            "fresh_session": True, "sources": {"model": "default", "effort": "cli"},
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
            for field in routing.FIELDS:
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
