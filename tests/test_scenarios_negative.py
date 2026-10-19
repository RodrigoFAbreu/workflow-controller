"""Every scenario fails when its claim is broken (R7: a scenario must not pass vacuously).

Each case runs a registered scenario with ``kit.params["mutation"]`` set; the
scenario honours the mutation by breaking its own claim (the scripted worker
skips a commit trailer, a gate is not a gate, ``resume`` or the continuation
``run`` is replaced by ``true``, ...) and must then end ``failed`` with an
*assertion*, not a crash. One generated ``TestCase`` class per mutation, so each
is its own planner atom, like ``tests/test_scenarios.py``.
"""

from __future__ import annotations

import os
import re
import unittest

from tests.scenarios import inventory, kit

#: flow id -> the mutations that must make it fail.
MUTATIONS: dict[str, tuple[str, ...]] = {
    "scenario-full-lifecycle-to-merge": ("skip-trailer", "remote-outside"),
    "scenario-human-gates-stop-and-resume": ("gate-launches-worker",),
    "scenario-worker-failure-fails-closed": ("advancing-worker",),
    "scenario-usage-limit-mid-job-waits-and-repeats": ("plain-failure",),
    "scenario-trunk-drift": ("no-drift",),
    # `resume` reconciles a non-terminal job; where it legitimately does nothing the continuation `run` carries the claim.
    "scenario-kill-and-resume-binding-before": ("run-true",),
    "scenario-kill-and-resume-binding-after": ("run-true",),
    "scenario-kill-and-resume-run-before": ("run-true",),
    "scenario-kill-and-resume-run-after": ("run-true",),
    "scenario-kill-and-resume-job-before": ("resume-true",),
    "scenario-kill-and-resume-job-after": ("resume-true",),
    "scenario-review-round-cap-stops-and-continues": ("cap-ignored",),  # review-convergence CP2
}
MUTATIONS_FUNCTIONAL: dict[str, tuple[str, ...]] = {  # functional review CP5
    "scenario-functional-review-closes-the-cycle": ("applier-noop",),
    "scenario-functional-review-stops-for-a-person": ("no-bound",),
    "scenario-functional-review-without-a-run": ("automated-ignored",),
    "scenario-functional-tester-isolation": ("sandbox-off",),
}
MUTATIONS_CP4: dict[str, tuple[str, ...]] = {  # scenarios 6-10 (CP4)
    "scenario-squash-merge-and-release-classification": ("docs-as-feat",),
    "scenario-notifications-never-steer": ("steering-hub",),
    "scenario-usage-pause-and-resume": ("no-reading",),
    "scenario-read-only-commands-change-nothing": ("step-as-status",),
    "scenario-settings-file-effect": ("show-writes",),
}


MUTATIONS_REVIEW: dict[str, tuple[str, ...]] = {  # scenarios 11-12 (codex-reviewer CP4)
    "scenario-codex-review-plan-stage": (
        "no-header-check", "no-ingest", "copy-kept", "repository-unguarded", "sandbox-dropped", "overrides-dropped",
        "provider-pin-dropped", "trust-pin-dropped", "skills-pin-dropped", "features-not-disabled", "env-widened", "inherited-home", "mcp-list-unchecked",
        "effective-unchecked", "outside-layers-unchecked", "home-unchecked", "no-copy-back", "no-launch-intent", "retry-status-unchecked"),
    "scenario-codex-review-implementation-stage-limit": (
        "copy-back-over-changes", "admission-without-readings", "admission-operator-only"),
    "scenario-review-mode-single-provider": ("mode-ignored",),  # review-modes CP4
}


def all_mutations() -> dict[str, tuple[str, ...]]:
    return {**MUTATIONS, **MUTATIONS_CP4, **MUTATIONS_REVIEW, **MUTATIONS_FUNCTIONAL}


#: mutation -> text its failure message must carry, where the claim is one the mutation is aimed at by name.
EXPECTED = {"remote-outside": "resolves outside the scratch tree"}


def class_name(flow_id: str, mutation: str) -> str:
    return "Mutation_" + re.sub(r"[^0-9A-Za-z]+", "_", f"{flow_id}_{mutation}")


def mutation_class(flow_id: str, mutation: str) -> type:
    def test_mutation_fails_the_scenario(self) -> None:
        decision, reason = kit.isolation_gate(kit.probe_isolation(), os.environ)
        if decision == "skip":
            self.skipTest(reason)
        if decision == "fail":
            self.fail(reason)
        outcome = kit.run_scenario(flow_id, params={"mutation": mutation})
        self.assertEqual(outcome.result.get("status"), "failed", f"{flow_id} passed with {mutation} applied")
        message = outcome.result.get("message", "")
        self.assertRegex(message, r"^(ScenarioFailure|AssertionError)", f"{mutation} crashed instead of failing: {message}")
        self.assertNotIn("no scenario is registered", message)
        if mutation in EXPECTED:
            self.assertIn(EXPECTED[mutation], message)
    return type(class_name(flow_id, mutation), (unittest.TestCase,),
                {"test_mutation_fails_the_scenario": test_mutation_fails_the_scenario, "__module__": __name__,
                 "__doc__": f"{flow_id} fails under the {mutation} mutation."})


for _flow, _mutations in all_mutations().items():
    for _mutation in _mutations:
        globals()[class_name(_flow, _mutation)] = mutation_class(_flow, _mutation)


class EveryScenarioHasAMutationTest(unittest.TestCase):
    def test_every_registered_scenario_names_a_mutation(self) -> None:
        registered = set(inventory.catalogue_flow_ids())
        self.assertEqual(registered - set(all_mutations()), set(), "a scenario without a mutation check")
        self.assertEqual(set(all_mutations()) - registered, set(), "a mutation for a scenario that is not registered")
