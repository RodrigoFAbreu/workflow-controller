"""Tests for ``tools/ci_workflows.py`` (CP9, and
``workflow-controller-trunk-branch-pr-release-orchestration`` CP5): the
committed workflow files equal the render, the retired ``release.yml`` is
absent, the emitter's quoting and layout, and the structure of ``ci.yml``,
``tests.yml`` and ``main.yml``; and
(``workflow-controller-adaptive-test-sharding`` CP6) ``tests.yml``'s
planned ``plan`` / ``tests`` / ``tests-result`` jobs, the CI placement
partition and the conformance family; and
(``workflow-controller-squash-merge-tag-versioning`` CP4) ``pr-title.yml``
and ``main.yml``'s ``build`` job taking the classified version as
``RELEASE_VERSION``; and (``ci/standardize-actions``) ``ci.yml`` as the one
entry point that calls ``tests.yml`` and, on ``main`` after a successful
validation, ``build-publish.yml`` (which replaced ``main.yml``), both
reusable workflows also dispatchable by hand, and the shared
``python-tests.yml`` running the documentation test modules.

The assertions are made on the Python model, and the committed bytes are
asserted equal to its render, so what GitHub runs is what is tested here. A
PyYAML cross-check runs only when PyYAML happens to be importable.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import importlib.util
import io
import json
import re
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

CI_WORKFLOWS_PY = fixtures.REPO_ROOT / "tools" / "ci_workflows.py"
_spec = importlib.util.spec_from_file_location("ci_workflows", CI_WORKFLOWS_PY)
ci = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ci
_spec.loader.exec_module(ci)
shards = ci.test_shards

WORKFLOWS = fixtures.REPO_ROOT / ".github" / "workflows"
CONFORMANCE_YML = WORKFLOWS / "workflow-conformance.yml"
INSTALLATION = fixtures.REPO_ROOT / ".workflow-manager" / "installation.json"

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - depends on the environment
    yaml = None


def _models() -> dict[str, dict]:
    return {name: build() for name, (build, _) in ci.WORKFLOWS.items()}


def _jobs(workflow: dict) -> dict[str, dict]:
    return workflow["jobs"]


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _run(step: dict) -> str:
    return step.get("run", "")


def _uses(step: dict) -> str:
    return str(step.get("uses", ""))


def _index(steps: list[dict], predicate) -> int:
    matches = [i for i, step in enumerate(steps) if predicate(step)]
    assert len(matches) == 1, f"expected exactly one matching step, found {len(matches)}"
    return matches[0]


def _all_steps():
    for name, workflow in _models().items():
        for job_name, job in _jobs(workflow).items():
            for step in _steps(job):
                yield name, job_name, job, step


def _dir_of(glob: str) -> str:
    return str(PurePosixPath(glob).parent)


def _normalise(path: str) -> str:
    return path.rstrip("/")


def _managed_suites(text: str) -> list[str]:
    """Suites of the managed workflow: ``run: python3 <file>`` lines (up to
    Workflow 2.9.x) or one ``suites="a.py b.py"`` assignment (2.10.0)."""
    runs = re.findall(r"(?m)^\s*run: python3 (\S+)\s*$", text)
    if runs:
        return runs
    return [s for m in re.findall(r'(?m)^\s*suites="([^"\n]*)"\s*$', text) for s in m.split()]


class CommittedFilesTest(unittest.TestCase):
    def test_check_passes_on_the_committed_files(self) -> None:
        self.assertEqual(ci.check(), [])
        result = fixtures.run([sys.executable, str(CI_WORKFLOWS_PY), "--check"],
                              cwd=fixtures.REPO_ROOT, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_one_edited_byte_fails_the_check(self) -> None:
        for name in ci.WORKFLOWS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                shutil.copytree(WORKFLOWS, root / ".github" / "workflows")
                path = root / ".github" / "workflows" / name
                data = bytearray(path.read_bytes())
                data[len(data) // 2] ^= 0x01
                path.write_bytes(bytes(data))
                self.assertEqual(ci.check(root), [f".github/workflows/{name}"])
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(ci.main(["--check", "--root", str(root)]), 1)
                self.assertIn(name, stderr.getvalue())

    def test_a_missing_file_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sorted(ci.check(Path(tmp))),
                             sorted(f".github/workflows/{name}" for name in ci.WORKFLOWS))

    def test_controller_tests_workflow_is_removed(self) -> None:
        self.assertFalse((WORKFLOWS / "controller-tests.yml").exists())

    def test_the_tag_triggered_release_workflow_and_main_yml_are_retired(self) -> None:
        self.assertEqual(ci.RETIRED_WORKFLOWS, ("release.yml", "main.yml", "validate.yml"))
        for name in ci.RETIRED_WORKFLOWS:
            with self.subTest(name=name):
                self.assertNotIn(name, ci.WORKFLOWS)
                self.assertFalse((WORKFLOWS / name).exists())

    def test_a_retired_file_fails_the_check_and_write_removes_it(self) -> None:
        for name in ci.RETIRED_WORKFLOWS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                shutil.copytree(WORKFLOWS, root / ".github" / "workflows")
                retired = root / ".github" / "workflows" / name
                retired.write_text("name: Retired\n")
                self.assertEqual(ci.check(root), [f".github/workflows/{name}"])
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(ci.main(["--check", "--root", str(root)]), 1)
                self.assertIn(name, stderr.getvalue())
                self.assertEqual(ci.main(["--write", "--root", str(root)]), 0)
                self.assertFalse(retired.exists())
                self.assertEqual(ci.check(root), [])

    def test_managed_conformance_workflow_is_untouched(self) -> None:
        record = json.loads(INSTALLATION.read_text(encoding="utf-8"))
        expected = record["managed"][".github/workflows/workflow-conformance.yml"]["sha256"]
        self.assertEqual(hashlib.sha256(CONFORMANCE_YML.read_bytes()).hexdigest(), expected)

    def test_the_generated_files(self) -> None:
        self.assertEqual(sorted(ci.WORKFLOWS), ["build-publish.yml", "ci.yml", "main-reuse-report.yml",
                                                "pr-title.yml", "tests.yml"])
        self.assertEqual(sorted(ci.render()), sorted(ci.WORKFLOWS))
        self.assertTrue((WORKFLOWS / "pr-title.yml").is_file())

    def test_only_the_model_and_the_managed_workflow_exist(self) -> None:
        present = sorted(path.name for path in WORKFLOWS.iterdir())
        self.assertEqual(present, sorted([*ci.WORKFLOWS, CONFORMANCE_YML.name]))


class EmitterTest(unittest.TestCase):
    def test_golden_structures(self) -> None:
        model = {
            "name": "Demo",
            "on": {"push": {"branches": ["main"]}, "pull_request": None},
            "jobs": {
                "a": {
                    "runs-on": "ubuntu-latest",
                    "strategy": {"fail-fast": False, "matrix": {"include": [{"x": 1, "y": "b c"}]}},
                    "steps": [
                        {"uses": "actions/checkout@v4", "with": {"fetch-depth": 0}},
                        {"run": "one\ntwo\n\nthree"},
                        {"with": {}},
                    ],
                },
            },
        }
        expected = (
            "# first\n"
            "#\n"
            "# second\n"
            "name: Demo\n"
            '"on":\n'
            "  push:\n"
            "    branches:\n"
            "      - main\n"
            "  pull_request:\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    strategy:\n"
            "      fail-fast: false\n"
            "      matrix:\n"
            "        include:\n"
            "          - x: 1\n"
            '            "y": "b c"\n'
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "        with:\n"
            "          fetch-depth: 0\n"
            "      - run: |\n"
            "          one\n"
            "          two\n"
            "\n"
            "          three\n"
            "      - with: {}\n"
        )
        self.assertEqual(ci.emit(model, "first\n\nsecond"), expected)

    def test_pinned_action_carries_its_tag_as_a_comment(self) -> None:
        pinned = ci.Pinned("actions/checkout")
        sha, tag = ci.ACTION_PINS["actions/checkout"]
        self.assertEqual(ci.emit({"uses": pinned}), f"uses: actions/checkout@{sha} # {tag}\n")

    def test_strings_the_safe_regex_rejects_are_quoted(self) -> None:
        for text in ["3.12", "v*", "${{ github.ref }}", "a b", "a: b", "#x", "-x", ".x",
                     "x#y", "*x", "&x", "!x", "%x", "'x'", '"x"', "", "1e3", "0x1f",
                     "a,b", "[x]", "{x}", "x|y", "x>y", "~"]:
            with self.subTest(text=text):
                self.assertIsNone(ci.SAFE_SCALAR_RE.fullmatch(text))
                self.assertEqual(ci.scalar(text), json.dumps(text))

    def test_reserved_words_are_quoted(self) -> None:
        for text in ["on", "On", "ON", "off", "yes", "no", "y", "n", "true", "False", "null"]:
            with self.subTest(text=text):
                self.assertEqual(ci.scalar(text), json.dumps(text))

    def test_safe_strings_are_bare(self) -> None:
        for text in ["main", "ubuntu-latest", "actions/checkout@v4", "workflow_call",
                     "tests.test_resume", "fail-fast"]:
            with self.subTest(text=text):
                self.assertEqual(ci.scalar(text), text)

    def test_non_strings(self) -> None:
        self.assertEqual(ci.scalar(True), "true")
        self.assertEqual(ci.scalar(False), "false")
        self.assertEqual(ci.scalar(0), "0")
        self.assertEqual(ci.scalar(None), "null")
        with self.assertRaises(TypeError):
            ci.scalar(1.5)

    def test_on_is_rendered_as_a_quoted_key(self) -> None:
        for name, text in ci.render().items():
            with self.subTest(name=name):
                self.assertIn('\n"on":\n', text)
                self.assertNotRegex(text, r"(?m)^on:")

    def test_unrepresentable_literal_blocks_are_refused(self) -> None:
        for text in ["a\nb\n", " a\nb", "a \nb"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                ci.emit({"run": text})

    @unittest.skipIf(yaml is None, "PyYAML is not importable; the cross-check is optional")
    def test_rendered_text_parses_back_to_the_model(self) -> None:
        def plain(value):
            if isinstance(value, dict):
                return {key: plain(item) for key, item in value.items()}
            if isinstance(value, list):
                return [plain(item) for item in value]
            if isinstance(value, ci.Pinned):
                return str(value)
            if isinstance(value, str) and "\n" in value:
                return value + "\n"
            return value

        for name, text in ci.render().items():
            with self.subTest(name=name):
                self.assertEqual(yaml.safe_load(text), plain(_models()[name]))


class CoverageTest(unittest.TestCase):
    """The CI placement partitions the inventory, which the ``plan`` job
    plans in full: nothing is hand-listed in the workflow model any more."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = shards.build_inventory(fixtures.REPO_ROOT)
        cls.partition = shards.ci_partition(cls.inventory)

    def test_the_hand_curated_lists_are_gone(self) -> None:
        for name in ("CONTROLLER_SHARDS", "EXCLUDED_TEST_MODULES", "CONFORMANCE_SUITES"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(ci, name))
        self.assertNotIn("conformance", _jobs(ci.tests_workflow()))
        self.assertNotIn("controller", _jobs(ci.tests_workflow()))

    def test_every_test_module_is_in_the_inventory(self) -> None:
        discovered = {f"tests.{path.stem}" for path in (fixtures.REPO_ROOT / "tests").glob("test_*.py")}
        loaded = {atom.module for atom in self.inventory.atoms if atom.family == shards.CONTROLLER}
        self.assertEqual(loaded, discovered)

    def test_the_placement_partitions_the_inventory(self) -> None:
        placed = [test_id for ids in self.partition.values() for test_id in ids]
        self.assertEqual(len(placed), len(set(placed)))
        self.assertEqual(set(placed), set(self.inventory.test_ids))
        self.assertEqual(set(self.partition),
                         {shards.SHARDS, shards.MAIN, shards.PACKAGE, shards.EXCLUDED})

    def test_the_tests_matrix_plans_exactly_the_shards_part(self) -> None:
        selection = self.inventory.select([], ci_placement=True)
        self.assertEqual(tuple(selection.test_ids), self.partition[shards.SHARDS])

    def test_the_placed_modules(self) -> None:
        by_place = {}
        for module, (where, reason) in shards.CI_PLACEMENT.items():
            by_place.setdefault(where, []).append(module)
            with self.subTest(module=module):
                self.assertTrue(reason.strip())
        self.assertEqual(by_place, {shards.PACKAGE: ["tests.test_packaged_runtime"],
                                    shards.MAIN: ["tests.test_catalogue_determinism",
                                                  "tests.test_scenarios_negative"],
                                    shards.EXCLUDED: ["tests.test_integration_disposable_repo"]})
        self.assertIn("claude", shards.CI_PLACEMENT["tests.test_integration_disposable_repo"][1])

    def test_package_job_runs_the_package_placed_modules(self) -> None:
        self.assertEqual(ci.PACKAGE_TEST_MODULES, ("tests.test_packaged_runtime",))
        package = _jobs(ci.tests_workflow())["package"]
        runs = [_run(step) for step in _steps(package)]
        self.assertIn("CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python -m unittest "
                      "tests.test_packaged_runtime -v", runs)
        package_ids = {test_id for test_id in self.partition[shards.PACKAGE]}
        self.assertTrue(package_ids)
        self.assertTrue(all(test_id.startswith("tests.test_packaged_runtime.")
                            for test_id in package_ids))

    def test_conformance_family_equals_the_managed_workflow(self) -> None:
        managed = _managed_suites(CONFORMANCE_YML.read_text(encoding="utf-8"))
        self.assertTrue(managed)
        conformance = [test_id for test_id in self.partition[shards.SHARDS]
                       if test_id.startswith(shards.CONFORMANCE_PREFIX)]
        self.assertEqual(conformance, [shards.CONFORMANCE_PREFIX + suite for suite in managed])


class ValidatePlanTest(unittest.TestCase):
    """``plan`` -> ``tests`` (one job per planned shard) -> ``tests-result``."""

    def setUp(self) -> None:
        self.jobs = _jobs(ci.tests_workflow())

    def _argv(self, job: str, fragment: str) -> list[str]:
        steps = _steps(self.jobs[job])
        return shlex.split(_run(steps[_index(steps, lambda s: fragment in _run(s))]))

    def test_job_graph(self) -> None:
        self.assertEqual(list(self.jobs),
                         ["changes", "docs-tests", "docs", "plan", "tests", "tests-result", "package"])
        self.assertNotIn("needs", self.jobs["changes"])
        self.assertNotIn("needs", self.jobs["docs-tests"])
        self.assertEqual(self.jobs["docs"]["needs"], ["docs-tests"])
        self.assertEqual(self.jobs["plan"]["needs"], ["changes"])
        self.assertEqual(self.jobs["tests"]["needs"], ["changes", "plan"])
        self.assertEqual(self.jobs["tests-result"]["needs"], ["changes", "docs", "plan", "tests"])
        self.assertEqual(self.jobs["tests-result"]["if"], "always()")
        # The shards are skipped only when a reused run's id is passed in.
        self.assertEqual(self.jobs["tests"]["if"],
                         "inputs.tests-from-run == '' && needs.changes.outputs.code == 'true'")
        # The third job of the one docs-tests -> docs -> package lane; whatever
        # docs ended as, package runs unless the run is cancelled (CI3 D4).
        self.assertEqual(self.jobs["package"]["needs"], ["docs"])
        self.assertEqual(self.jobs["package"]["if"], "!cancelled()")

    def test_check_names(self) -> None:
        self.assertNotIn("name", self.jobs["plan"])
        self.assertEqual(self.jobs["tests"]["name"], "tests (${{ matrix.shard }})")
        self.assertNotIn("name", self.jobs["tests-result"])
        self.assertNotIn("name", self.jobs["package"])

    def test_plan_outputs_and_argv(self) -> None:
        plan = self.jobs["plan"]
        self.assertEqual(plan["outputs"], {name: f"${{{{ steps.plan.outputs.{name} }}}}"
                                           for name in ("shards", "count", "digest")})
        steps = _steps(plan)
        step = steps[_index(steps, lambda s: "run_tests.py plan" in _run(s))]
        self.assertEqual(step["id"], "plan")
        self.assertEqual(self._argv("plan", "run_tests.py plan"),
                         ["python3", "tools/run_tests.py", "plan", "--profile", "ci",
                          "--ci-placement", "--output", "plan.json", "--github-output"])
        self.assertIn("pip install -e .", map(_run, steps))

    def test_matrix_is_the_planned_shards(self) -> None:
        strategy = self.jobs["tests"]["strategy"]
        self.assertEqual(strategy, {"fail-fast": False, "max-parallel": ci.TEST_MAX_PARALLEL,
                                    "matrix": {"shard": "${{ fromJSON(needs.plan.outputs.shards) }}"}})

    def test_exec_shard_recomputes_the_plan_and_checks_its_digest(self) -> None:
        argv = self._argv("tests", "exec-shard")
        self.assertEqual(argv, ["python3", "tools/run_tests.py", "exec-shard", "--profile", "ci",
                                "--ci-placement", "--shard", "${{", "matrix.shard", "}}",
                                "--expect-digest", "${{", "needs.plan.outputs.digest", "}}",
                                "--results-dir", "results",
                                "--run-attempt", "${{", "github.run_attempt", "}}"])
        for absent in ("--count", "--shards", "--plan", "--target-seconds", "--min-shards",
                       "--max-shards"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, argv)
        # Exactly the plan job's planning inputs, so the digests can match.
        plan_argv = self._argv("plan", "run_tests.py plan")
        self.assertEqual(argv[3:6], plan_argv[3:6])
        self.assertIn("pip install -e .", map(_run, _steps(self.jobs["tests"])))

    def test_tests_result_aggregates_the_plan_and_every_result(self) -> None:
        steps = _steps(self.jobs["tests-result"])
        self.assertEqual(self._argv("tests-result", "--plan-artifact"),
                         ["python3", "tools/run_tests.py", "aggregate", "--plan",
                          "results/plan.json", "--results-dir", "results/shards",
                          "--run-attempt", "${{", "github.run_attempt", "}}",
                          "--write-selected", "results/selected",
                          "--upstream-result", "plan=${{", "needs.plan.result", "}}",
                          "--upstream-result", "tests=${{", "needs.tests.result", "}}",
                          "--plan-artifact", "test-plan", "--results-artifact-prefix",
                          "results-"])
        downloads = [s for s in steps if "download-artifact" in _uses(s)]
        # Every attempt's results artifacts, each into its own directory:
        # merged, the last writer of shard-<i>.json would win.
        self.assertEqual([s["with"] for s in downloads[:2]],
                         [{"name": "test-plan", "path": "results"},
                          {"pattern": "results-*", "path": "results/shards"}])
        self.assertEqual(downloads[1]["if"],
                         "inputs.tests-from-run == '' && needs.changes.outputs.code == 'true'")
        self.assertNotIn("merge-multiple", ci.emit(ci.tests_workflow()))
        # A missing artifact must reach aggregate (which exits 2, naming it),
        # not end the job before it can say so.
        self.assertTrue(all(s["continue-on-error"] is True for s in downloads))
        aggregate = _index(steps, lambda s: s.get("name") == "aggregate")
        self.assertTrue(all(steps.index(s) < aggregate for s in downloads))
        self.assertEqual(steps[aggregate]["if"],
                         "inputs.tests-from-run == '' && needs.changes.outputs.code == 'true'")
        self.assertNotIn("continue-on-error", steps[aggregate])

    def test_artifact_names(self) -> None:
        plan_upload = [s["with"] for s in _steps(self.jobs["plan"]) if "upload-artifact" in _uses(s)]
        self.assertEqual(plan_upload, [{"name": "test-plan", "path": "plan.json"}])
        tests_upload = [s for s in _steps(self.jobs["tests"]) if "upload-artifact" in _uses(s)]
        self.assertEqual([(s["if"], s["with"]) for s in tests_upload],
                         [("always()", {"name": "results-${{ matrix.shard }}-attempt-"
                                                "${{ github.run_attempt }}",
                                        "path": "results/"})])
        # The download directory name aggregate cross-checks each record against.
        name = tests_upload[0]["with"]["name"].replace("${{ matrix.shard }}", "3").replace(
            "${{ github.run_attempt }}", "2")
        self.assertRegex(name, ci.test_shards.RESULTS_ARTIFACT_DIR_RE)
        timings = [s for s in _steps(self.jobs["tests-result"]) if "upload-artifact" in _uses(s)]
        self.assertEqual([(s["if"], s["with"]) for s in timings],
                         [("always() && needs.changes.outputs.code == 'true'",
                           {"name": "timings-ci-attempt-${{ github.run_attempt }}",
                            "path": "results/selected/"})])
        # The require step is last (CI1a D5); the upload is the one before it.
        self.assertEqual(timings[0], _steps(self.jobs["tests-result"])[-2])
        # The tree is recorded before the upload, whatever the verdict.
        tree = _index(_steps(self.jobs["tests-result"]), lambda s: s.get("name") == "record the tree")
        self.assertEqual(_steps(self.jobs["tests-result"])[tree]["if"],
                         "always() && needs.changes.outputs.code == 'true'")
        self.assertIn("results/selected/tree.txt", _run(_steps(self.jobs["tests-result"])[tree]))

    def test_tests_job_allows_user_namespaces_before_the_shard(self) -> None:
        guarded = ("if [ -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then "
                   "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0; fi")
        for job_name, job in self.jobs.items():
            named = [s for s in _steps(job) if s.get("name") == "allow unprivileged user namespaces"]
            with self.subTest(job=job_name):
                self.assertEqual(len(named), 1 if job_name == "tests" else 0)
        steps = _steps(self.jobs["tests"])
        step_index = _index(steps, lambda s: s.get("name") == "allow unprivileged user namespaces")
        self.assertLess(step_index, _index(steps, lambda s: s.get("name") == "shard"))
        self.assertEqual(steps[step_index], {"name": "allow unprivileged user namespaces", "run": guarded})

    def test_validate_keeps_major_tag_actions(self) -> None:
        # ADR 0002: tests.yml's actions keep their major tags; the jobs
        # outside it are pinned to commits.
        for job_name, job in self.jobs.items():
            for step in _steps(job):
                if "uses" in step:
                    with self.subTest(job=job_name, uses=_uses(step)):
                        self.assertIsInstance(step["uses"], str)
                        self.assertRegex(step["uses"], r"^actions/[a-z-]+@v\d+$")


class ChangeDetectionTest(unittest.TestCase):
    """CI1a: ``changes`` and ``docs`` jobs, and the documentation-only mode of
    ``tests-result`` (D3, D4, D5, D6)."""

    CODE = "needs.changes.outputs.code == 'true'"

    def setUp(self) -> None:
        self.validate = ci.tests_workflow()
        self.jobs = _jobs(self.validate)
        self.result_steps = _steps(self.jobs["tests-result"])

    def _expressions(self, job: dict) -> list[str]:
        """Every string of the job that can hold a ``${{ }}`` or an ``if``."""
        found = []

        def walk(value) -> None:
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk({k: v for k, v in job.items() if k != "outputs"})
        return found

    def test_every_needs_read_names_a_direct_dependency_and_a_declared_output(self) -> None:
        for name, job in self.jobs.items():
            direct = set(job.get("needs", []))
            for text in self._expressions(job):
                for needed, rest in re.findall(r"needs\.([a-z-]+)((?:\.[a-z]+)*)", text):
                    with self.subTest(job=name, read=f"needs.{needed}{rest}"):
                        self.assertIn(needed, direct)
                        if rest.startswith(".outputs."):
                            output = rest[len(".outputs."):]
                            self.assertIn(output, self.jobs[needed].get("outputs", {}))

    def test_changes_declares_code_from_the_detect_step(self) -> None:
        changes = self.jobs["changes"]
        self.assertEqual(changes["outputs"], {"code": "${{ steps.detect.outputs.code }}"})
        steps = _steps(changes)
        detect = steps[_index(steps, lambda s: s.get("id") == "detect")]
        self.assertEqual(_run(detect), "python3 tools/ci_changes.py detect")
        checkout = steps[_index(steps, lambda s: _uses(s).startswith("actions/checkout@"))]
        self.assertEqual(checkout["with"]["fetch-depth"], 0)

    def test_docs_runs_the_documentation_gate_from_a_shallow_checkout(self) -> None:
        docs = self.jobs["docs"]
        # It waits for the documentation test modules, and has no if of its
        # own: a failed docs-tests skips it, and require then fails closed.
        self.assertEqual(docs["needs"], ["docs-tests"])
        self.assertNotIn("if", docs)
        self.assertNotIn("if", self.jobs["docs-tests"])
        steps = _steps(docs)
        checkout = steps[_index(steps, lambda s: _uses(s).startswith("actions/checkout@"))]
        self.assertNotIn("fetch-depth", checkout.get("with", {}))
        self.assertEqual(_run(steps[-1]), "python3 tools/check_docs.py")
        self.assertTrue(any(_run(s) == "pip install -e ." for s in steps[:-1]))

    def test_plan_and_tests_carry_the_code_condition(self) -> None:
        self.assertEqual(self.jobs["plan"]["needs"], ["changes"])
        self.assertEqual(self.jobs["plan"]["if"], self.CODE)
        self.assertEqual(self.jobs["tests"]["needs"], ["changes", "plan"])
        self.assertEqual(self.jobs["tests"]["if"], f"inputs.tests-from-run == '' && {self.CODE}")

    def test_tests_result_needs_every_gate_job_and_still_always_runs(self) -> None:
        self.assertEqual(self.jobs["tests-result"]["needs"], ["changes", "docs", "plan", "tests"])
        self.assertEqual(self.jobs["tests-result"]["if"], "always()")

    def test_require_is_the_last_step_and_runs_after_the_report(self) -> None:
        last = self.result_steps[-1]
        self.assertEqual(last["if"], "always()")
        self.assertEqual(shlex.split(_run(last)),
                         ["python3", "tools/ci_changes.py", "require",
                          "--changes", "${{", "needs.changes.result", "}}",
                          "--docs", "${{", "needs.docs.result", "}}",
                          "--code", "${{ needs.changes.outputs.code }}"])
        self.assertEqual(sum("ci_changes.py require" in _run(s) for s in self.result_steps), 1)
        # The aggregate has run before require can fail the job.
        aggregate = _index(self.result_steps, lambda s: s.get("name") == "aggregate")
        self.assertLess(aggregate, len(self.result_steps) - 1)

    def test_the_tree_and_the_timings_keep_always_with_the_code_condition(self) -> None:
        tree = self.result_steps[_index(self.result_steps, lambda s: s.get("name") == "record the tree")]
        uploads = [s for s in self.result_steps if "upload-artifact" in _uses(s)]
        self.assertEqual(len(uploads), 1)
        for step in (tree, uploads[0]):
            self.assertEqual(step["if"], f"always() && {self.CODE}")

    def test_only_require_decides_when_code_is_false(self) -> None:
        for step in self.result_steps:
            if _uses(step).startswith(("actions/checkout@", "actions/setup-python@")):
                self.assertNotIn("if", step)
                continue
            if "ci_changes.py require" in _run(step):
                continue
            with self.subTest(step=step.get("name") or step.get("uses")):
                self.assertIn(self.CODE, step["if"])

    def test_no_paths_filter_in_any_generated_workflow(self) -> None:
        for name, workflow in _models().items():
            text = ci.emit(workflow)
            with self.subTest(workflow=name):
                self.assertNotIn("paths-ignore", text)
                self.assertNotRegex(text, r"(?m)^\s+paths:")

    def test_ci_calls_tests_unchanged_and_build_publish_runs_no_tests(self) -> None:
        models = _models()
        self.assertEqual(_jobs(models["ci.yml"])["validate"]["uses"], "./.github/workflows/tests.yml")
        self.assertEqual([job for job in _jobs(models["build-publish.yml"]).values() if "uses" in job], [])
        for name in ("ci.yml", "build-publish.yml"):
            self.assertNotIn("changes", ci.emit(models[name]).replace("tests-from-run", ""))

    def test_the_required_check_and_package_names_are_unchanged(self) -> None:
        self.assertIn("tests-result", self.jobs)
        self.assertIn("package", self.jobs)
        self.assertNotIn("name", self.jobs["tests-result"])
        self.assertNotIn("name", self.jobs["package"])
        self.assertEqual((self.jobs["package"]["needs"], self.jobs["package"]["if"]),
                         (["docs"], "!cancelled()"))


class AllJobsTest(unittest.TestCase):
    #: The reusable workflows a job may call instead of running steps.
    CALLED = {"./.github/workflows/tests.yml", "./.github/workflows/build-publish.yml",
              "RodrigoFAbreu/action-workflows/.github/workflows/python-tests.yml@main"}

    def test_matrix_jobs_do_not_fail_fast(self) -> None:
        for name, workflow in _models().items():
            for job_name, job in _jobs(workflow).items():
                if "strategy" in job:
                    with self.subTest(workflow=name, job=job_name):
                        self.assertIn("matrix", job["strategy"])
                        self.assertIs(job["strategy"]["fail-fast"], False)

    def test_every_job_runs_on_ubuntu_latest_or_calls_a_reusable_workflow(self) -> None:
        called = set()
        for name, workflow in _models().items():
            for job_name, job in _jobs(workflow).items():
                with self.subTest(workflow=name, job=job_name):
                    if "uses" in job:
                        self.assertIn(job["uses"], self.CALLED)
                        self.assertNotIn("steps", job)
                        self.assertNotIn("runs-on", job)
                        called.add(job["uses"])
                    else:
                        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(called, self.CALLED)

    def test_validate_jobs_are_read_only(self) -> None:
        for job_name, job in _jobs(ci.tests_workflow()).items():
            with self.subTest(job=job_name):
                # tests-result alone may read another run's artifact (reuse).
                expected = {"contents": "read", "actions": "read"} \
                    if job_name == "tests-result" else {"contents": "read"}
                self.assertEqual(job["permissions"], expected)

    def test_only_publish_can_write(self) -> None:
        writers = [(name, job_name) for name, workflow in _models().items()
                   for job_name, job in _jobs(workflow).items()
                   if "write" in job.get("permissions", {}).values()]
        # ci.yml's build-publish job only grants what the called workflow's
        # publish job asks for: a called workflow cannot exceed its caller.
        self.assertEqual(writers, [("ci.yml", "build-publish"), ("build-publish.yml", "publish")])
        self.assertEqual(_jobs(ci.ci_workflow())["build-publish"]["permissions"],
                         {"contents": "write"})
        for job_name, job in _jobs(ci.build_publish_workflow()).items():
            with self.subTest(job=job_name):
                self.assertIn("permissions", job)
                if job_name != "publish":
                    self.assertNotIn("write", job["permissions"].values())
        for name, workflow in _models().items():
            with self.subTest(workflow=name):
                self.assertEqual(workflow.get("permissions", {"contents": "read"}), {"contents": "read"})

    def test_no_cache_no_secret_no_live_worker(self) -> None:
        for name, text in ci.render().items():
            with self.subTest(workflow=name):
                self.assertNotIn("actions/cache", text)
                self.assertNotIn("secrets", text)
                self.assertNotIn("CONTROLLER_LIVE_WORKER", text)
                self.assertNotRegex(text, r"(?m)^\s*(run: .*)?\bclaude\b")

    def test_artifacts_only_where_expected(self) -> None:
        found = sorted((name, job_name, _uses(step).split("@")[0])
                       for name, job_name, _, step in _all_steps()
                       if "-artifact" in _uses(step))
        self.assertEqual(found, [("build-publish.yml", "build", "actions/upload-artifact"),
                                 ("build-publish.yml", "publish", "actions/download-artifact"),
                                 ("ci.yml", "reuse", "actions/download-artifact"),
                                 ("main-reuse-report.yml", "reuse-report", "actions/download-artifact"),
                                 ("tests.yml", "plan", "actions/upload-artifact"),
                                 ("tests.yml", "tests", "actions/upload-artifact"),
                                 ("tests.yml", "tests-result", "actions/download-artifact"),
                                 ("tests.yml", "tests-result", "actions/download-artifact"),
                                 ("tests.yml", "tests-result", "actions/download-artifact"),
                                 ("tests.yml", "tests-result", "actions/upload-artifact")])
        # ci.yml calls tests.yml and build-publish.yml in one run, and a
        # dispatched build-publish.yml calls tests.yml: no name may collide.
        names = [step["with"].get("name") for _, _, _, step in _all_steps()
                 if "upload-artifact" in _uses(step)]
        self.assertEqual(len(names), len(set(names)))

    def test_no_bare_github_sha_commit(self) -> None:
        for name, text in ci.render().items():
            with self.subTest(workflow=name):
                self.assertNotIn('--commit \\"$GITHUB_SHA\\"', text)
                self.assertNotIn('--commit "$GITHUB_SHA"', text)
        for name, job_name, _, step in _all_steps():
            for match in re.finditer(r'--commit (\S+)', _run(step)):
                with self.subTest(workflow=name, job=job_name, arg=match.group(1)):
                    self.assertNotEqual(match.group(1), '"$GITHUB_SHA"')

    def test_package_peels_github_sha(self) -> None:
        package = _jobs(ci.tests_workflow())["package"]
        verify = [_run(s) for s in _steps(package) if "verify-wheel" in _run(s)]
        self.assertEqual(verify, ['python3 tools/release.py verify-wheel dist/*.whl --local '
                                  '--commit "$(git rev-parse "$GITHUB_SHA^{commit}")"'])
        self.assertEqual(_steps(package)[0]["with"]["fetch-depth"], 0)
        self.assertIn('pip install "setuptools>=70.1"', map(_run, _steps(package)))
        self.assertIn(ci.PIPX_SMOKE, map(_run, _steps(package)))

    def test_validate_smoke_test_is_unchanged_in_text(self) -> None:
        # 1.3.0's text: the local build's version, now tag-derived after the cutover.
        smoke = [_run(s) for s in _steps(_jobs(ci.tests_workflow())["package"])
                 if s.get("name") == "pipx smoke test"]
        self.assertEqual(smoke, [PIPX_SMOKE_1_3_0])

    def test_no_step_tags_pushes_or_mutates_a_release(self) -> None:
        # The only tag is created inside ``tools/release.py publish``, after
        # validation, through controller/gitrepo.py and controller/forge.py.
        for name, job_name, _, step in _all_steps():
            run = _run(step)
            with self.subTest(workflow=name, job=job_name, run=run):
                for forbidden in ("--clobber", "--force", "delete", "git tag", "git push",
                                  "gh release", "gh api"):
                    self.assertNotIn(forbidden, run)
                self.assertNotRegex(run, r"(^|\s)-f(\s|$)")
                self.assertNotRegex(run, r"(^|\s)gh\s")
        for name, text in ci.render().items():
            with self.subTest(workflow=name):
                self.assertNotIn("--clobber", text)
                self.assertNotIn("delete", text)

    def test_release_py_forge_steps_see_gh_token(self) -> None:
        checked = []
        for name, job_name, job, step in _all_steps():
            run = _run(step)
            if re.search(r"tools/release\.py (classify|publish)\b", run):
                checked.append((name, job_name))
                env = {**job.get("env", {}), **step.get("env", {})}
                with self.subTest(workflow=name, job=job_name, run=run):
                    self.assertEqual(env.get("GH_TOKEN"), "${{ github.token }}")
        self.assertEqual(checked, [("build-publish.yml", "release-plan"), ("build-publish.yml", "publish")])

    def test_non_write_checkouts_do_not_persist_credentials(self) -> None:
        checkouts = 0
        for name, job_name, job, step in _all_steps():
            if not _uses(step).startswith("actions/checkout@"):
                continue
            checkouts += 1
            persist = step.get("with", {}).get("persist-credentials", True)
            with self.subTest(workflow=name, job=job_name):
                if "write" in job.get("permissions", {}).values():
                    self.assertIs(persist, True)
                else:
                    self.assertIs(persist, False)
        # tests.yml's six step jobs; ci.yml's reuse and two after-merge jobs;
        # build-publish.yml's release-plan, two release-target, build and
        # publish; reuse-report; title.
        self.assertEqual(checkouts, 6 + 3 + 5 + 1 + 1)

    def test_the_two_reusable_workflows_are_also_dispatchable_without_inputs(self) -> None:
        # A manual run hands nothing in: tests.yml never reuses, and
        # build-publish.yml cannot claim a validation: it builds nothing.
        for workflow in (ci.tests_workflow(), ci.build_publish_workflow()):
            with self.subTest(workflow=workflow["name"]):
                self.assertEqual(list(workflow["on"]), ["workflow_call", "workflow_dispatch"])
                self.assertIsNone(workflow["on"]["workflow_dispatch"])


def _gh_eval(expression: str, context: dict) -> object:
    """A GitHub Actions expression over ``context`` (``github.*``,
    ``inputs.*``, ``vars.*``; a missing input is null), for the operators
    the model uses. ``a == b`` follows GitHub's loose equality: a null
    equals ``''`` (both coerce to 0) and differs from every other string."""

    def eq(left, right) -> bool:
        if left is None or right is None:
            other = right if left is None else left
            return other is None or other == ""
        return left == right

    text = expression.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    names: dict = {}

    def ref(match: "re.Match[str]") -> str:
        key = f"v{len(names)}"
        names[key] = context.get(match.group(0))
        return key

    text = re.sub(r"\b(?:github|inputs|vars|needs)\.[A-Za-z0-9_.-]*[A-Za-z0-9_]", ref, text)
    operand = r"(\w+|'[^']*')"
    text = re.sub(rf"{operand} (==|!=) {operand}",
                  lambda m: f"{'' if m.group(2) == '==' else 'not '}eq({m.group(1)}, {m.group(3)})", text)
    text = text.replace("!cancelled()", "True").replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    text = text.replace("format(", "fmt(")
    assert "!" not in text, text
    return eval(text, {"__builtins__": {}},  # noqa: S307 - a fixed, model-derived expression
                {**names, "eq": eq, "True": True, "fmt": lambda f, *a: f.format(*a)})


class CiWorkflowTest(unittest.TestCase):
    """``ci.yml``: the one entry point, on pull requests, pushes to ``main``
    and ``workflow_dispatch``."""

    def setUp(self) -> None:
        self.workflow = ci.ci_workflow()
        self.jobs = _jobs(self.workflow)

    def test_triggers(self) -> None:
        self.assertEqual(self.workflow["on"], {"pull_request": None, "push": {"branches": ["main"]},
                                               "workflow_dispatch": None})
        self.assertNotIn("tags", self.workflow["on"]["push"])
        self.assertNotIn("inputs", ci.render()["ci.yml"])

    def _concurrency(self, event: str, ref: str) -> tuple:
        context = {"github.event_name": event, "github.ref": ref, "github.workflow": "CI",
                   "github.run_id": "123"}
        concurrency = self.workflow["concurrency"]
        return (_gh_eval(concurrency["group"], context), _gh_eval(concurrency["cancel-in-progress"], context))

    def test_concurrency(self) -> None:
        # A pull request keeps the group it always had and cancels its older run.
        self.assertEqual(self._concurrency("pull_request", "refs/pull/7/merge"),
                         ("CI-refs/pull/7/merge", True))
        # main takes the serialised release slot and never cancels.
        self.assertEqual(self._concurrency("push", "refs/heads/main"), ("main-release", False))
        self.assertEqual(self._concurrency("workflow_dispatch", "refs/heads/main"), ("main-release", False))
        # A dispatch on any other ref can never hold or replace main's slot.
        self.assertEqual(self._concurrency("workflow_dispatch", "refs/heads/topic"), ("CI-123", False))
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_jobs(self) -> None:
        self.assertEqual(list(self.jobs), ["reuse", "validate", "after-merge-determinism",
                                           "after-merge-mutations", "build-publish"])
        self.assertEqual(self.jobs["validate"], {
            "needs": ["reuse"], "if": "!cancelled()",
            "permissions": {"contents": "read", "actions": "read"},
            "uses": "./.github/workflows/tests.yml",
            "with": {"tests-from-run": "${{ vars.CI_MAIN_REUSE == 'on' && "
                                       "needs.reuse.outputs.run-id || '' }}"}})
        self.assertEqual(self.jobs["build-publish"], {
            "needs": ["validate", "after-merge-determinism", "after-merge-mutations"],
            "if": "!cancelled() && needs.validate.result == 'success' && "
                  "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'",
            "permissions": {"contents": "write"},
            "uses": "./.github/workflows/build-publish.yml",
            "with": {"validate-result": "${{ needs.validate.result }}",
                     "after-merge-determinism": "${{ needs.after-merge-determinism.result }}",
                     "after-merge-mutations": "${{ needs.after-merge-mutations.result }}"}})
        # The inputs it passes are exactly the ones build-publish.yml declares.
        declared = ci.build_publish_workflow()["on"]["workflow_call"]["inputs"]
        self.assertEqual(list(self.jobs["build-publish"]["with"]), list(declared))

    def test_the_required_check_contexts_are_unchanged(self) -> None:
        # Branch protection requires "validate / plan", "validate / tests-result",
        # "validate / package" (a called job's context is "<caller job> / <job name>"),
        # "PR title" and the managed "workflow-conformance".
        validate_jobs = _jobs(ci.tests_workflow())
        contexts = {f"validate / {validate_jobs[job].get('name', job)}"
                    for job in ("plan", "tests-result", "package")}
        self.assertEqual(contexts, {"validate / plan", "validate / tests-result", "validate / package"})
        self.assertEqual(self.jobs["validate"]["uses"], "./.github/workflows/tests.yml")
        self.assertEqual(ci.pr_title_workflow()["jobs"]["title"]["name"], "PR title")
        self.assertIn("workflow-conformance:", CONFORMANCE_YML.read_text(encoding="utf-8"))

    def _would_run(self, job: str, event: str, ref: str, results: dict) -> bool:
        context = {"github.event_name": event, "github.ref": ref,
                   **{f"needs.{name}.result": result for name, result in results.items()}}
        for needed in re.findall(r"needs\.([a-z-]+)\.", self.jobs[job].get("if", "")):
            self.assertIn(needed, self.jobs[job]["needs"])
        return bool(_gh_eval(self.jobs[job]["if"], context))

    def test_build_publish_is_called_only_on_main_after_a_successful_validate(self) -> None:
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                self.assertTrue(self._would_run("build-publish", event, "refs/heads/main",
                                                {"validate": "success"}))
                # The after-merge results are handed in, not required here: a
                # RESUME of an ancestor is gated by that ancestor's own checks.
                self.assertTrue(self._would_run("build-publish", event, "refs/heads/main",
                                                {"validate": "success", "after-merge-determinism": "failure"}))
                for result in ("failure", "skipped", "cancelled"):
                    self.assertFalse(self._would_run("build-publish", event, "refs/heads/main",
                                                     {"validate": result}))
                self.assertFalse(self._would_run("build-publish", event, "refs/heads/topic",
                                                 {"validate": "success"}))
        self.assertFalse(self._would_run("build-publish", "pull_request", "refs/pull/7/merge",
                                         {"validate": "success"}))

    def test_after_merge_checks_never_run_on_a_pull_request(self) -> None:
        for check in ("determinism", "mutations"):
            job = self.jobs[f"after-merge-{check}"]
            # They wait for validate, so they never share its runners (CI3 D4).
            self.assertEqual(job["needs"], ["validate"])
            self.assertEqual(job["if"], "!cancelled() && needs.validate.result == 'success' && "
                                        "github.event_name != 'pull_request'")
            ok = {"needs.validate.result": "success"}
            self.assertFalse(_gh_eval(job["if"], {"github.event_name": "pull_request", **ok}))
            self.assertTrue(_gh_eval(job["if"], {"github.event_name": "push", **ok}))
            self.assertTrue(_gh_eval(job["if"], {"github.event_name": "workflow_dispatch", **ok}))
            for result in ("failure", "skipped", "cancelled"):
                self.assertFalse(_gh_eval(job["if"], {"github.event_name": "push",
                                                      "needs.validate.result": result}))

    def test_validate_is_workflow_call_and_dispatch(self) -> None:
        on = ci.tests_workflow()["on"]
        self.assertEqual(list(on), ["workflow_call", "workflow_dispatch"])
        self.assertIsNone(on["workflow_dispatch"])
        self.assertEqual(list(on["workflow_call"]), ["inputs"])
        inputs = on["workflow_call"]["inputs"]
        self.assertEqual(list(inputs), ["tests-from-run"])
        self.assertEqual((inputs["tests-from-run"]["type"], inputs["tests-from-run"]["default"],
                          inputs["tests-from-run"]["required"]), ("string", "", False))
        # Dispatched, the input is null, which runs the shards like ''.
        tests_if = _jobs(ci.tests_workflow())["tests"]["if"]
        self.assertTrue(_gh_eval(tests_if, {"needs.changes.outputs.code": "true"}))


class DocsTestsTest(unittest.TestCase):
    """``docs-tests``: the documentation test modules through the shared
    generic ``python-tests.yml``."""

    def setUp(self) -> None:
        self.job = _jobs(ci.tests_workflow())["docs-tests"]

    def test_the_job_calls_the_shared_workflow_through_its_structured_inputs(self) -> None:
        self.assertEqual(self.job, {
            "permissions": {"contents": "read"},
            "uses": "RodrigoFAbreu/action-workflows/.github/workflows/python-tests.yml@main",
            "with": {"python-version": "3.12", "test-directory": "tests",
                     "top-level-directory": ".", "test-pattern": ci.DOCS_TEST_PATTERN}})
        self.assertEqual(self.job["with"]["python-version"], ci.PYTHON_VERSION)
        self.assertNotIn("test-command", ci.render()["tests.yml"])

    def test_the_pattern_selects_exactly_the_documentation_test_modules(self) -> None:
        # unittest discover matches the pattern against file names in tests/
        # and in its packages; it must load these modules and no other.
        tests = fixtures.REPO_ROOT / "tests"
        matched = sorted("tests." + ".".join(path.relative_to(tests).with_suffix("").parts)
                         for path in tests.rglob("*.py")
                         if fnmatch.fnmatch(path.name, ci.DOCS_TEST_PATTERN))
        self.assertEqual(matched, sorted(ci.DOCS_TEST_MODULES))
        self.assertEqual(ci.DOCS_TEST_MODULES, ("tests.test_backlog", "tests.test_docs"))

    def test_the_modules_run_once(self) -> None:
        for name, job_name, _, step in _all_steps():
            for module in ci.DOCS_TEST_MODULES:
                with self.subTest(workflow=name, job=job_name, module=module):
                    self.assertNotIn(module, _run(step))


class BuildPublishWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = ci.build_publish_workflow()
        self.jobs = _jobs(self.workflow)
        self.plan = _steps(self.jobs["release-plan"])
        self.build = _steps(self.jobs["build"])
        self.publish = _steps(self.jobs["publish"])

    def test_triggers(self) -> None:
        # workflow_dispatch has no inputs: it classifies the dispatched ref's
        # tip, and RESUME reaches an interrupted release's own tagged commit.
        on = self.workflow["on"]
        self.assertEqual(list(on), ["workflow_call", "workflow_dispatch"])
        self.assertIsNone(on["workflow_dispatch"])
        inputs = on["workflow_call"]["inputs"]
        self.assertEqual(list(inputs), ["validate-result", "after-merge-determinism", "after-merge-mutations"])
        for name, spec in inputs.items():
            with self.subTest(input=name):
                self.assertEqual((spec["type"], spec["required"], spec["default"]), ("string", False, ""))
        self.assertNotIn("pull_request", on)
        self.assertNotIn("push", on)

    def test_concurrency(self) -> None:
        self.assertIs(self.workflow["concurrency"]["cancel-in-progress"], False)
        # No self-run test job is left, so the workflow never takes the
        # release slot: its group is its own run's, called or dispatched.
        self.assertEqual(self.workflow["concurrency"]["group"], "build-publish-${{ github.run_id }}")
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    TARGETS = ["release-target-determinism", "release-target-mutations"]

    def test_needs_chain(self) -> None:
        self.assertEqual(list(self.jobs), ["release-plan", *self.TARGETS, "build", "publish"])
        for name in self.jobs:
            self.assertFalse(name == "validate" or name.startswith("after-merge-"), name)
        self.assertNotIn("needs", self.jobs["release-plan"])
        for check in ("determinism", "mutations"):
            self.assertEqual(self.jobs[f"release-target-{check}"]["needs"], ["release-plan"])
        self.assertEqual(self.jobs["build"]["needs"], ["release-plan", *self.TARGETS])
        self.assertEqual(self.jobs["publish"]["needs"], ["release-plan", "build", *self.TARGETS])

    def test_conditions(self) -> None:
        on_trunk = "github.ref == 'refs/heads/main'"
        validated = "inputs.validate-result == 'success'"
        publishing = (f"{on_trunk} && (needs.release-plan.outputs.state == 'RELEASE_DUE' || "
                      "needs.release-plan.outputs.state == 'RESUME')")
        self.assertEqual(self.jobs["release-plan"]["if"], f"!cancelled() && {on_trunk}")
        coverage = " && ".join(
            f"((needs.release-plan.outputs.commit == github.sha && inputs.after-merge-{c} == 'success') || "
            f"(needs.release-plan.outputs.commit != github.sha && needs.release-target-{c}.result == 'success'))"
            for c in ("determinism", "mutations"))
        self.assertEqual(self.jobs["build"]["if"],
                         f"!cancelled() && {validated} && needs.release-plan.result == 'success' "
                         f"&& {publishing} && {coverage}")
        self.assertEqual(self.jobs["publish"]["if"],
                         f"!cancelled() && {validated} && needs.release-plan.result == 'success' "
                         f"&& needs.build.result == 'success' && {publishing} && {coverage}")
        for check in ("determinism", "mutations"):
            self.assertEqual(self.jobs[f"release-target-{check}"]["needs"], ["release-plan"])
            self.assertEqual(self.jobs[f"release-target-{check}"]["if"],
                             f"!cancelled() && needs.release-plan.result == 'success' && {validated} && "
                             f"{publishing} && needs.release-plan.outputs.commit != github.sha")
        self.assertEqual(ci._publishing_condition(), publishing)

    @staticmethod
    def _conjuncts(expression: str) -> list:
        """The top-level ``&&`` operands of ``expression``, parsed by
        parenthesis depth (never by substring)."""
        parts, depth, start = [], 0, 0
        for index, char in enumerate(expression):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0 and expression.startswith(" && ", index):
                parts.append(expression[start:index])
                start = index + 4
        parts.append(expression[start:])
        return [part.strip() for part in parts]

    def test_every_release_job_requires_the_callers_validate_result_as_a_conjunct(self) -> None:
        for name in ("build", "publish", *self.TARGETS):
            with self.subTest(job=name):
                expression = self.jobs[name]["if"]
                self.assertIn("inputs.validate-result == 'success'", self._conjuncts(expression))
                self.assertNotIn("always()", expression)
                self.assertIn("!cancelled()", self._conjuncts(expression))
                # Only the inputs, release-plan, the targets and the build are read.
                for reference in re.findall(r"\b(?:inputs|needs|github)\.[A-Za-z0-9_.-]*[A-Za-z0-9_]", expression):
                    self.assertRegex(reference, r"^(inputs\.(validate-result|after-merge-[a-z]+)|"
                                                r"needs\.(release-plan|build|release-target-[a-z]+)\.[a-z.]+|"
                                                r"github\.(ref|sha))$")
        self.assertNotIn("inputs.validate-result", self.jobs["release-plan"]["if"])

    def _would_run(self, name: str, results: dict, state: str, ref: str = "refs/heads/main",
                   *, target_is_pushed: bool = True, inputs: "dict | None" = None) -> bool:
        """The job's ``if`` over ``results`` (job -> result), ``inputs`` (default: a
        successful call of ``ci.yml``; ``{}`` is a dispatch, every input null) and
        ``release-plan``'s commit equal to the pushed one or not."""
        inputs = self.CALLED if inputs is None else inputs
        job = self.jobs[name]
        # Only a job named in the if's own needs may be read (a GitHub Actions rule).
        for needed in re.findall(r"needs\.([a-z-]+)\.", job.get("if", "")):
            self.assertIn(needed, job.get("needs", []), (name, needed))
        context = {"github.ref": ref, "github.sha": "pushed",
                   "needs.release-plan.outputs.commit": "pushed" if target_is_pushed else "ancestor",
                   "needs.release-plan.outputs.state": state,
                   **{f"needs.{job_name}.result": result for job_name, result in results.items()},
                   **{f"inputs.{key}": value for key, value in inputs.items()}}
        return bool(_gh_eval(job["if"], context))

    #: ``ci.yml``'s call: its validate and after-merge results for the pushed commit.
    CALLED = {"validate-result": "success", "after-merge-determinism": "success",
              "after-merge-mutations": "success"}
    STATES = ("RELEASE_DUE", "RESUME", "NO_CHANGE", "NOT_DUE")

    def _chain(self, state: str = "RELEASE_DUE", *, inputs: "dict | None" = None,
               target_is_pushed: bool = True, targets: str = "skipped") -> tuple:
        """(release-plan, release-target-*, build, publish) would-run flags."""
        results = {"release-plan": "success" if self._would_run(
            "release-plan", {}, state, inputs=inputs) else "skipped"}
        ran = tuple(self._would_run(name, results, state, inputs=inputs, target_is_pushed=target_is_pushed)
                    for name in self.TARGETS)
        results.update({name: targets if run else "skipped" for name, run in zip(self.TARGETS, ran)})
        build = self._would_run("build", results, state, inputs=inputs, target_is_pushed=target_is_pushed)
        results["build"] = "success" if build else "skipped"
        publish = self._would_run("publish", results, state, inputs=inputs, target_is_pushed=target_is_pushed)
        return results["release-plan"] == "success", any(ran), build, publish

    def test_called_it_builds_and_publishes_what_the_caller_validated(self) -> None:
        self.assertEqual(self._chain(), (True, False, True, True))
        # An ancestor RESUME runs the target's own checks first.
        self.assertEqual(self._chain("RESUME", target_is_pushed=False, targets="success"),
                         (True, True, True, True))
        self.assertEqual(self._chain("NO_CHANGE"), (True, False, False, False))
        # A caller result other than success releases nothing.
        for result in ("failure", "cancelled", "skipped"):
            with self.subTest(result=result):
                inputs = {**self.CALLED, "validate-result": result}
                self.assertEqual(self._chain(inputs=inputs), (True, False, False, False))
        # A caller that hands in no after-merge results publishes nothing for the pushed commit.
        self.assertEqual(self._chain(inputs={"validate-result": "success"}), (True, False, False, False))

    def test_a_direct_dispatch_runs_no_test_and_publishes_nothing(self) -> None:
        # Every input a dispatch can produce: absent (null) or empty or any other string.
        for values in ({}, {"validate-result": ""},
                       {"validate-result": "", "after-merge-determinism": "", "after-merge-mutations": ""},
                       {"validate-result": "failure"}, {"validate-result": "skipped"},
                       {"after-merge-determinism": "success", "after-merge-mutations": "success"}):
            for state in self.STATES:
                for pushed in (True, False):
                    for targets in ("success", "failure", "skipped"):
                        with self.subTest(inputs=values, state=state, pushed=pushed, targets=targets):
                            plan, target, build, publish = self._chain(
                                state, inputs=values, target_is_pushed=pushed, targets=targets)
                            self.assertEqual((target, build, publish), (False, False, False))
        # Only the read-only classification runs, and it names the CI dispatch.
        self.assertTrue(self._would_run("release-plan", {}, "RESUME", inputs={}))
        notice = [step for step in _steps(self.jobs["release-plan"]) if step.get("name") == "no test results"]
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice[0]["if"], "inputs.validate-result != 'success'")
        self.assertIn("dispatch the CI workflow on main", notice[0]["run"])
        self.assertTrue(_gh_eval(notice[0]["if"], {}))
        self.assertFalse(_gh_eval(notice[0]["if"], {"inputs.validate-result": "success"}))

    def test_a_ci_dispatch_publishes_from_main_only(self) -> None:
        ci_jobs = _jobs(ci.ci_workflow())
        call = ci_jobs["build-publish"]
        self.assertEqual(call["with"]["validate-result"], "${{ needs.validate.result }}")
        for check in ("determinism", "mutations"):
            self.assertEqual(call["with"][f"after-merge-{check}"], f"${{{{ needs.after-merge-{check}.result }}}}")
            self.assertTrue(_gh_eval(ci_jobs[f"after-merge-{check}"]["if"],
                                     {"github.event_name": "workflow_dispatch",
                                      "needs.validate.result": "success"}))
        for ref, expected in (("refs/heads/main", True), ("refs/heads/topic", False)):
            context = {"github.event_name": "workflow_dispatch", "github.ref": ref,
                       "needs.validate.result": "success"}
            self.assertEqual(bool(_gh_eval(call["if"], context)), expected, ref)

    def test_every_release_job_states_its_condition(self) -> None:
        for name in ("release-plan", "build", "publish"):
            expression = self.jobs[name]["if"]
            self.assertNotIn("always()", expression)
            self.assertIn("!cancelled()", expression)

    def test_permissions(self) -> None:
        self.assertEqual(self.jobs["release-plan"]["permissions"], {"contents": "read"})
        self.assertEqual(self.jobs["build"]["permissions"], {"contents": "read"})
        self.assertEqual(self.jobs["publish"]["permissions"], {"contents": "write"})
        self.assertEqual(self.jobs["release-plan"]["env"], {"GH_TOKEN": "${{ github.token }}"})
        self.assertEqual(self.jobs["build"]["env"],
                         {"RELEASE_VERSION": "${{ needs.release-plan.outputs.version }}"})
        self.assertEqual(self.jobs["publish"]["env"],
                         {"GH_TOKEN": "${{ github.token }}",
                          "COMMIT": "${{ needs.release-plan.outputs.commit }}"})
        steps_with_env = [(job_name, step.get("name")) for job_name, job in self.jobs.items()
                          for step in _steps(job) if "env" in step]
        self.assertEqual(steps_with_env, [("release-plan", "classify")])

    def test_classify_alone_lends_git_the_read_only_token(self) -> None:
        # The checkout does not persist its token; classify's fetch and
        # ls-remote reach origin through gh as a credential helper, which
        # reads the job's read-only GH_TOKEN.
        classify = self.plan[_index(self.plan, lambda s: "classify" in _run(s))]
        self.assertEqual(classify["env"], {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
            "GIT_CONFIG_VALUE_0": "!gh auth git-credential",
        })
        self.assertIs(self.plan[0]["with"]["persist-credentials"], False)

    def test_every_action_outside_validate_is_pinned_to_a_commit(self) -> None:
        for name, count in (("build-publish.yml", 12), ("ci.yml", 7), ("main-reuse-report.yml", 3),
                            ("pr-title.yml", 2)):
            uses = re.findall(r"(?m)^\s*(?:- )?uses: (\S+)(.*)$", ci.render()[name])
            actions = [(ref, comment) for ref, comment in uses if not ref.startswith('"./')]
            with self.subTest(workflow=name):
                self.assertEqual(len(actions), count)
            for ref, comment in actions:
                with self.subTest(workflow=name, ref=ref):
                    self.assertRegex(ref, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
                    action = ref.split("@")[0]
                    self.assertEqual(comment, f" # {ci.ACTION_PINS[action][1]}")

    def test_release_plan_classifies_the_peeled_trunk_commit(self) -> None:
        checkout = self.plan[0]
        self.assertTrue(_uses(checkout).startswith("actions/checkout@"))
        self.assertEqual(checkout["with"], {"fetch-depth": 0, "fetch-tags": True,
                                            "persist-credentials": False})
        classify = self.plan[_index(self.plan, lambda s: "classify" in _run(s))]
        self.assertEqual(classify["id"], "classify")
        self.assertEqual(_run(classify), 'python3 tools/release.py classify '
                                         '--commit "$(git rev-parse "$GITHUB_SHA^{commit}")"')
        self.assertEqual(self.jobs["release-plan"]["outputs"],
                         {name: f"${{{{ steps.classify.outputs.{name} }}}}"
                          for name in ("state", "version", "tag", "commit")})

    def test_build_order(self) -> None:
        checkout = self.build[0]
        self.assertEqual(checkout["with"], {"ref": "${{ needs.release-plan.outputs.commit }}",
                                            "fetch-depth": 0, "persist-credentials": False})

        def at(fragment: str) -> int:
            return _index(self.build, lambda s: fragment in _run(s))

        order = [at("release.py build"), at("release.py verify"), at("pipx install"),
                 at("release.py checksums"),
                 _index(self.build, lambda s: "upload-artifact" in _uses(s))]
        self.assertEqual(order, sorted(order))
        self.assertEqual(order[-1], len(self.build) - 1)
        # 1.3.0's command lines byte for byte: a RESUME target carrying
        # 1.3.0's tooling, whose build and verify take no option, accepts them.
        self.assertEqual(_run(self.build[order[0]]), "python3 tools/release.py build")
        self.assertEqual(_run(self.build[order[1]]), "python3 tools/release.py verify")
        self.assertEqual(_run(self.build[order[2]]), ci.RELEASE_PIPX_SMOKE)

    def test_the_release_smoke_test_compares_with_the_classified_version(self) -> None:
        smoke = ci.RELEASE_PIPX_SMOKE
        self.assertIn('expected="workflow-controller $RELEASE_VERSION"', smoke.split("\n"))
        self.assertNotIn("release.py version", smoke)
        self.assertEqual(smoke, PIPX_SMOKE_1_3_0.replace("$(python3 tools/release.py version)",
                                                          "$RELEASE_VERSION"))
        self.assertIn('          expected="workflow-controller $RELEASE_VERSION"\n',
                      ci.render()["build-publish.yml"])

    def test_release_plan_outputs_are_unchanged(self) -> None:
        self.assertEqual(list(self.jobs["release-plan"]["outputs"]), ["state", "version", "tag", "commit"])

    def test_publish_calls_the_transaction_with_the_peeled_commit(self) -> None:
        checkout = self.publish[0]
        self.assertTrue(_uses(checkout).startswith("actions/checkout@"))
        # The trunk commit itself (no ref), with its credentials, to push the tag.
        self.assertEqual(checkout["with"], {"fetch-depth": 0, "fetch-tags": True})
        download = _index(self.publish, lambda s: "download-artifact" in _uses(s))
        tools = [i for i, s in enumerate(self.publish) if "tools/release.py" in _run(s)]
        self.assertEqual(tools, [len(self.publish) - 1])
        self.assertLess(download, tools[0])
        self.assertEqual(_run(self.publish[-1]), 'python3 tools/release.py publish --commit "$COMMIT"')

    def test_artifact_contract(self) -> None:
        upload = self.build[_index(self.build, lambda s: "upload-artifact" in _uses(s))]["with"]
        download = self.publish[_index(self.publish, lambda s: "download-artifact" in _uses(s))]["with"]
        self.assertEqual(upload["name"], download["name"])
        policy = json.loads((fixtures.REPO_ROOT / ".workflow-controller" / "policy.json").read_text())
        artifact_dirs = {_dir_of(path) for path in policy["release"]["artifacts"]["paths"]}
        sums_dir = re.search(r"checksums (\S+)$", _run(
            self.build[_index(self.build, lambda s: "checksums" in _run(s))])).group(1)
        self.assertEqual(artifact_dirs, {_normalise(upload["path"])})
        self.assertEqual(_normalise(upload["path"]), _normalise(sums_dir))
        self.assertEqual(_normalise(download["path"]), _normalise(upload["path"]))

    def test_the_documented_sources_are_recorded(self) -> None:
        self.assertIn("docs.github.com", ci.GITHUB_SHA_NOTE)
        self.assertIn("Tip commit pushed to the ref", ci.GITHUB_SHA_NOTE)
        self.assertIn("cli.github.com/manual/gh_release_create", ci.GH_RELEASE_CREATE_NOTE)
        text = ci.render()["build-publish.yml"]
        self.assertIn("Tip commit pushed to the ref", text)
        self.assertIn("gh_release_create", text)


class AfterMergeChecksTest(unittest.TestCase):
    """The two whole-catalogue checks run after the merge and gate the release
    (``workflow-controller-ci-time`` D2): ``after-merge-*`` in ``ci.yml``
    beside ``validate``, ``release-target-*`` in ``build-publish.yml`` for an
    ancestor a ``RESUME`` publishes. Borrows ``_would_run``."""

    _would_run = BuildPublishWorkflowTest._would_run
    _chain = BuildPublishWorkflowTest._chain
    CALLED = BuildPublishWorkflowTest.CALLED
    STATES = BuildPublishWorkflowTest.STATES
    TARGETS = BuildPublishWorkflowTest.TARGETS

    def setUp(self) -> None:
        self.workflow = ci.build_publish_workflow()
        self.jobs = _jobs(self.workflow)
        self.ci_jobs = _jobs(ci.ci_workflow())

    CHECKS = {"determinism": "tests.test_catalogue_determinism",
              "mutations": "tests.test_scenarios_negative"}

    def test_the_jobs_exist_outside_tests(self) -> None:
        self.assertEqual(ci.MAIN_TEST_MODULES, self.CHECKS)
        for check in self.CHECKS:
            self.assertIn(f"release-target-{check}", self.jobs)
            self.assertNotIn(f"after-merge-{check}", self.jobs)
            self.assertIn(f"after-merge-{check}", self.ci_jobs)
            self.assertNotIn(f"release-target-{check}", self.ci_jobs)
            for kind in ("after-merge", "release-target"):
                self.assertNotIn(f"{kind}-{check}", _jobs(ci.tests_workflow()))
        self.assertNotIn("main-check", ci.render()["tests.yml"])

    def test_each_job_has_the_shard_jobs_setup_and_the_fail_closed_command(self) -> None:
        shard = _steps(_jobs(ci.tests_workflow())["tests"])
        for check, module in self.CHECKS.items():
            for kind, jobs in (("after-merge", self.ci_jobs), ("release-target", self.jobs)):
                steps = _steps(jobs[f"{kind}-{check}"])
                with self.subTest(job=f"{kind}-{check}"):
                    self.assertTrue(_uses(steps[0]).startswith("actions/checkout@"))
                    self.assertTrue(_uses(steps[1]).startswith("actions/setup-python@"))
                    self.assertEqual(_run(steps[2]), "pip install -e .")
                    self.assertEqual(_run(steps[3]), ci.ALLOW_USERNS)
                    self.assertEqual(steps[3], shard[3])
                    self.assertEqual(_run(steps[4]), f"python3 tools/run_tests.py main-check {module}")
                    self.assertEqual(len(steps), 5)

    def test_the_target_jobs_check_out_the_publication_target(self) -> None:
        for check in self.CHECKS:
            pushed = _steps(self.ci_jobs[f"after-merge-{check}"])[0]
            target = _steps(self.jobs[f"release-target-{check}"])[0]
            self.assertNotIn("ref", pushed.get("with", {}))
            self.assertEqual(target["with"]["ref"], "${{ needs.release-plan.outputs.commit }}")
            self.assertEqual(target["with"]["fetch-depth"], 0)

    def test_none_depends_on_reuse(self) -> None:
        for check in self.CHECKS:
            # validate waits for reuse, so waiting for validate is not a direct need of it.
            self.assertEqual(self.ci_jobs[f"after-merge-{check}"]["needs"], ["validate"])
            self.assertEqual(self.jobs[f"release-target-{check}"]["needs"], ["release-plan"])
        for jobs in (self.jobs, self.ci_jobs):
            for name, job in jobs.items():
                if name.startswith(("after-merge-", "release-target-")):
                    self.assertNotIn("reuse", job.get("needs", []))
                    self.assertNotIn("reuse", job.get("if", ""))
        self.assertNotIn("needs", self.jobs["release-plan"])

    def test_no_if_reads_a_job_outside_its_own_needs(self) -> None:
        for workflow in (ci.build_publish_workflow(), ci.ci_workflow(), ci.tests_workflow()):
            for name, job in _jobs(workflow).items():
                with self.subTest(workflow=workflow["name"], job=name):
                    for needed in re.findall(r"needs\.([a-z-]+)\.", job.get("if", "")):
                        self.assertIn(needed, job["needs"])

    def _publishes(self, *, target_is_pushed: bool, inputs: "dict | None" = None,
                   targets: str = "skipped") -> bool:
        return self._chain("RELEASE_DUE", inputs=inputs, target_is_pushed=target_is_pushed,
                           targets=targets)[3]

    def test_an_ordinary_release_publishes_only_on_successful_after_merge_checks(self) -> None:
        self.assertTrue(self._publishes(target_is_pushed=True))
        for check in self.CHECKS:
            for result in ("failure", "cancelled", "skipped", "pending", ""):
                with self.subTest(check=check, result=result):
                    inputs = {**self.CALLED, f"after-merge-{check}": result}
                    self.assertFalse(self._publishes(target_is_pushed=True, inputs=inputs))
            # a missing input (never handed in) is not a success either
            missing = {k: v for k, v in self.CALLED.items() if k != f"after-merge-{check}"}
            self.assertFalse(self._publishes(target_is_pushed=True, inputs=missing))

    def test_a_target_job_result_is_irrelevant_when_the_target_is_the_pushed_commit(self) -> None:
        # The target jobs do not even run: the target is the pushed commit.
        for result in ("success", "failure", "skipped"):
            self.assertTrue(self._publishes(target_is_pushed=True, targets=result))

    def test_an_ancestor_resume_needs_the_checks_of_the_target_not_of_the_pushed_commit(self) -> None:
        self.assertTrue(self._publishes(target_is_pushed=False, targets="success"))
        for result in ("failure", "cancelled", "skipped"):
            with self.subTest(result=result):
                self.assertFalse(self._publishes(target_is_pushed=False, targets=result))
        # the pushed commit's checks failed: they do not decide an ancestor target
        failed = {**self.CALLED, "after-merge-determinism": "failure", "after-merge-mutations": "failure"}
        self.assertTrue(self._publishes(target_is_pushed=False, inputs=failed, targets="success"))
        # and a pushed commit's successes say nothing about the ancestor
        self.assertFalse(self._publishes(target_is_pushed=False, targets="skipped"))

    def test_the_target_jobs_run_for_an_ancestor_resume_and_only_then(self) -> None:
        for name in self.TARGETS:
            for state, pushed, expected in (("RESUME", False, True), ("RELEASE_DUE", False, True),
                                            ("RESUME", True, False), ("NOT_DUE", False, False)):
                with self.subTest(job=name, state=state, pushed=pushed):
                    self.assertEqual(self._would_run(name, {"release-plan": "success"}, state,
                                                     target_is_pushed=pushed), expected)
            self.assertFalse(self._would_run(name, {"release-plan": "failure"}, "RESUME",
                                             target_is_pushed=False))
            self.assertFalse(self._would_run(name, {"release-plan": "success"}, "RESUME",
                                             "refs/heads/other", target_is_pushed=False))
            # ... and never inside a run the caller did not validate (D2).
            self.assertFalse(self._would_run(name, {"release-plan": "success"}, "RESUME",
                                             target_is_pushed=False, inputs={}))

    def test_a_failed_validate_stops_the_release_whatever_the_checks_say(self) -> None:
        for validate in ("failure", "skipped", "cancelled", ""):
            self.assertFalse(self._publishes(target_is_pushed=True,
                                             inputs={**self.CALLED, "validate-result": validate}))
            self.assertFalse(self._publishes(target_is_pushed=False, targets="success",
                                             inputs={**self.CALLED, "validate-result": validate}))


class ReuseTest(unittest.TestCase):
    """``CI_MAIN_REUSE``: ``off`` and ``report`` run the shards exactly as
    before (the ``reuse`` job passes no run id), ``on`` passes one and
    ``tests-result`` re-verifies it, failing closed. Never on a pull request."""

    def setUp(self) -> None:
        self.ci = ci.ci_workflow()
        self.reuse = _jobs(self.ci)["reuse"]
        self.report_workflow = ci.reuse_report_workflow()
        self.report = _jobs(self.report_workflow)["reuse-report"]
        self.validate = _jobs(ci.tests_workflow())

    def test_reuse_job_is_least_privilege_and_cannot_write(self) -> None:
        self.assertEqual(self.reuse["permissions"],
                         {"actions": "read", "contents": "read", "pull-requests": "read"})
        self.assertNotIn("needs", self.reuse)
        self.assertEqual(self.reuse["outputs"], {"run-id": "${{ steps.decide.outputs.run-id }}"})

    def test_reuse_runs_only_for_on_off_pull_requests_and_report_only_beside_ci(self) -> None:
        self.assertEqual(self.reuse["if"], "vars.CI_MAIN_REUSE == 'on' && github.event_name != 'pull_request'")
        self.assertEqual(self.report["if"],
                         "vars.CI_MAIN_REUSE != 'on' && vars.CI_MAIN_REUSE != 'off'")
        # off matches neither; unset, empty, report and unknown match the report only.
        for value, event, expected in (("on", "push", (True, False)), ("on", "pull_request", (False, False)),
                                       ("on", "workflow_dispatch", (True, False)),
                                       ("off", "push", (False, False)), ("", "push", (False, True)),
                                       ("report", "push", (False, True)), ("maybe", "push", (False, True))):
            context = {"vars.CI_MAIN_REUSE": value, "github.event_name": event}
            with self.subTest(value=value, event=event):
                self.assertEqual((bool(_gh_eval(self.reuse["if"], context)),
                                  bool(_gh_eval(self.report["if"], context))), expected)
        # The report is a workflow of its own: CI has no report job, so nothing in
        # CI can wait for it or be held by it, and it shares no concurrency group.
        self.assertNotIn("reuse-report", _jobs(self.ci))
        self.assertNotIn("reuse-report:", ci.render()["ci.yml"])
        wf = self.report_workflow
        self.assertEqual(wf["on"], {"push": {"branches": ["main"]}})
        self.assertEqual(wf["permissions"], {"contents": "read"})
        self.assertEqual(wf["concurrency"],
                         {"group": "main-reuse-report-${{ github.sha }}", "cancel-in-progress": False})
        self.assertNotIn("main-release", wf["concurrency"]["group"])
        self.assertEqual(list(_jobs(wf)), ["reuse-report"])
        self.assertIs(self.report["continue-on-error"], True)
        self.assertNotIn("outputs", self.report)
        self.assertNotIn("continue-on-error", self.reuse)
        self.assertEqual(self.report["permissions"], self.reuse["permissions"])
        self.assertEqual(ci.emit({"steps": self.report["steps"]}), ci.emit({"steps": self.reuse["steps"]}))
        # on still blocks validate and passes the run id; a skipped reuse still lets validate start.
        validate = _jobs(self.ci)["validate"]
        self.assertEqual(validate["needs"], ["reuse"])
        self.assertEqual(validate["if"], "!cancelled()")

    def test_the_variable_reaches_the_script_only_through_the_environment(self) -> None:
        self.assertEqual(self.reuse["env"]["CI_MAIN_REUSE"], "${{ vars.CI_MAIN_REUSE }}")
        for name, text in ci.render().items():
            for line in text.splitlines():
                if "vars." in line:
                    self.assertIn(name, ("ci.yml", "main-reuse-report.yml"))
                    self.assertRegex(line.strip(), r"^(CI_MAIN_REUSE: \"\$\{\{ vars\.CI_MAIN_REUSE \}\}\"|"
                                                   r"if: \"vars\.CI_MAIN_REUSE (==|!=) 'o[nf]+'.*|"
                                                   r"tests-from-run: .*)$")
        find = self.reuse["steps"][_index(self.reuse["steps"], lambda s: s.get("id") == "find")]
        self.assertIn('--mode "$CI_MAIN_REUSE"', find["run"])
        self.assertNotIn("${{", find["run"])
        # tests.yml and build-publish.yml do not know the variable.
        self.assertNotIn("vars.", ci.render()["tests.yml"] + ci.render()["build-publish.yml"])

    def test_the_lookup_steps_only_run_with_a_candidate_and_the_report_cannot_fail_ci(self) -> None:
        steps = self.reuse["steps"]
        guarded = [s for s in steps if s.get("if")]
        self.assertEqual(len(guarded), 3)
        for step in guarded:
            self.assertEqual(step["if"], "steps.find.outputs.candidate != ''")
            self.assertIs(step["continue-on-error"], True)
        for step in steps:
            if step not in guarded:
                self.assertNotIn("continue-on-error", step)
        decide = steps[-1]
        self.assertEqual(decide["id"], "decide")
        self.assertIn("ci_reuse.py decide", decide["run"])

    def test_reuse_downloads_only_the_candidates_timings_artifact(self) -> None:
        download = [s for s in self.reuse["steps"] if "download-artifact" in _uses(s)]
        self.assertEqual(len(download), 1)
        self.assertEqual(download[0]["with"]["pattern"], "timings-ci-attempt-*")
        self.assertEqual(download[0]["with"]["run-id"], "${{ steps.find.outputs.candidate }}")

    def test_validate_gets_the_run_id_and_survives_a_failed_reuse_job(self) -> None:
        validate = _jobs(self.ci)["validate"]
        # The id is forwarded only while the variable is still "on" at evaluation
        # time: "Re-run failed jobs" keeps a successful reuse job's old output.
        self.assertEqual(validate["with"], {"tests-from-run": "${{ vars.CI_MAIN_REUSE == 'on' && "
                                                              "needs.reuse.outputs.run-id || '' }}"})
        self.assertEqual(validate["if"], "!cancelled()")
        self.assertEqual(validate["permissions"], {"contents": "read", "actions": "read"})
        # A pull request: reuse is skipped, its output empty, so the shards run.
        context = {"vars.CI_MAIN_REUSE": "on", "needs.reuse.outputs.run-id": ""}
        self.assertEqual(_gh_eval(validate["with"]["tests-from-run"], context), "")
        context = {"vars.CI_MAIN_REUSE": "on", "needs.reuse.outputs.run-id": "42"}
        self.assertEqual(_gh_eval(validate["with"]["tests-from-run"], context), "42")
        context = {"vars.CI_MAIN_REUSE": "off", "needs.reuse.outputs.run-id": "42"}
        self.assertEqual(_gh_eval(validate["with"]["tests-from-run"], context), "")

    def test_the_release_jobs_still_need_validate(self) -> None:
        # Through the caller's result: build-publish.yml has no validate job.
        jobs = _jobs(ci.build_publish_workflow())
        for name in ("build", "publish"):
            with self.subTest(job=name):
                self.assertIn("inputs.validate-result == 'success'", jobs[name]["if"])
                self.assertNotIn("reuse", jobs[name]["needs"])
        self.assertIn("validate", _jobs(self.ci)["build-publish"]["needs"])
        self.assertNotIn("reuse", _jobs(self.ci)["build-publish"]["needs"])

    def test_the_shards_run_unless_a_run_is_reused(self) -> None:
        code = " && needs.changes.outputs.code == 'true'"
        self.assertEqual(self.validate["tests"]["if"], "inputs.tests-from-run == ''" + code)
        steps = _steps(self.validate["tests-result"])
        by_name = {s.get("name"): s for s in steps}
        self.assertEqual(by_name["aggregate"]["if"], "inputs.tests-from-run == ''" + code)
        for name in ("verify the reused results", "aggregate the reused results"):
            self.assertEqual(by_name[name]["if"], "inputs.tests-from-run != ''" + code)
        # The plan is this run's own, in both cases: plan runs whatever the
        # input, and depends only on the change detection (CI1a D4).
        self.assertEqual(self.validate["plan"]["if"], "needs.changes.outputs.code == 'true'")
        self.assertEqual(self.validate["package"]["if"], "!cancelled()")

    def test_reused_results_are_verified_then_aggregated_and_never_continue_on_error(self) -> None:
        steps = _steps(self.validate["tests-result"])
        verify = _index(steps, lambda s: s.get("id") == "verify")
        aggregate = _index(steps, lambda s: s.get("name") == "aggregate the reused results")
        download = _index(steps, lambda s: "run-id" in s.get("with", {}))
        self.assertLess(download, verify)
        self.assertLess(verify, aggregate)
        self.assertEqual(steps[download]["with"]["run-id"], "${{ inputs.tests-from-run }}")
        self.assertEqual(steps[download]["with"]["repository"], "${{ github.repository }}")
        self.assertEqual(shlex.split(_run(steps[verify])),
                         ["python3", "tools/ci_reuse.py", "verify", "--reused-dir", "reused",
                          "--plan", "results/plan.json"])
        self.assertNotIn("continue-on-error", steps[verify])
        self.assertNotIn("continue-on-error", steps[aggregate])
        argv = shlex.split(_run(steps[aggregate]))
        self.assertEqual(argv[:4], ["python3", "tools/run_tests.py", "aggregate", "--plan"])
        self.assertEqual(argv[argv.index("--plan") + 1], "results/plan.json")
        self.assertEqual(argv[argv.index("--results-dir") + 1:argv.index("--results-dir") + 5],
                         ["${{", "steps.verify.outputs.selected-dir", "}}", "--write-selected"])
        # Only the plan job's result is judged: the shards did not run.
        self.assertIn("plan=${{", argv)
        self.assertNotIn("tests=${{", argv)

    def test_the_full_aggregate_is_unchanged(self) -> None:
        steps = _steps(self.validate["tests-result"])
        run = _run(steps[_index(steps, lambda s: s.get("name") == "aggregate")])
        self.assertTrue(run.startswith("python3 tools/run_tests.py aggregate --plan results/plan.json "
                                       "--results-dir results/shards --run-attempt "))
        self.assertIn("--upstream-result tests=${{ needs.tests.result }}", run)

    def test_callers_grant_actions_read_for_the_artifact_download(self) -> None:
        # Only a job calling tests.yml needs it; build-publish.yml has none.
        self.assertEqual(_jobs(ci.ci_workflow())["validate"]["permissions"],
                         {"contents": "read", "actions": "read"})
        self.assertNotIn("validate", _jobs(ci.build_publish_workflow()))
        for name, job in _jobs(ci.build_publish_workflow()).items():
            self.assertNotIn("actions", job["permissions"], name)

    def test_the_generated_reuse_job_is_readable_and_documented_in_the_header(self) -> None:
        text = ci.render()["ci.yml"]
        self.assertIn("# reuse (read-only, never on a pull request) is switched by the repository variable", text)
        self.assertIn("main-reuse-report.yml", text)
        self.assertIn("A workflow apart from ci.yml", ci.render()["main-reuse-report.yml"])
        self.assertIn("tests-from-run", ci.render()["tests.yml"].split("name: Validate")[0])


class PrTitleWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = ci.pr_title_workflow()
        self.jobs = _jobs(self.workflow)
        self.text = ci.render()["pr-title.yml"]

    def test_triggers(self) -> None:
        self.assertEqual(self.workflow["on"],
                         {"pull_request": {"types": ["opened", "edited", "reopened", "synchronize"]}})
        self.assertEqual(self.workflow["name"], "PR title")

    def test_read_only(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(list(self.jobs), ["title"])
        self.assertNotIn("permissions", self.jobs["title"])
        self.assertNotIn("env", self.jobs["title"])
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("token", self.text)

    def test_the_job_name_is_the_check_context(self) -> None:
        self.assertEqual(self.jobs["title"]["name"], "PR title")
        self.assertEqual(ci.PR_TITLE_CHECK, "PR title")

    def test_the_title_is_passed_only_through_env(self) -> None:
        steps = _steps(self.jobs["title"])
        check = steps[_index(steps, lambda s: "check-title" in _run(s))]
        self.assertEqual(check["env"], {"TITLE": "${{ github.event.pull_request.title }}"})
        self.assertEqual(_run(check), 'python3 tools/release.py check-title "$TITLE"')
        self.assertEqual(check, steps[-1])
        for step in steps:
            with self.subTest(step=step.get("name") or _uses(step)):
                self.assertNotIn("${{", _run(step))
        run_lines = re.findall(r"(?m)^\s*(?:- )?run: (.*)$", self.text)
        self.assertEqual(run_lines, ['"python3 tools/release.py check-title \\"$TITLE\\""'])
        self.assertEqual(self.text.count("github.event.pull_request.title"), 1)

    def test_pinned_actions_without_persisted_credentials(self) -> None:
        steps = _steps(self.jobs["title"])
        uses = re.findall(r"(?m)^\s*(?:- )?uses: (\S+)(.*)$", self.text)
        self.assertEqual([ref.split("@")[0] for ref, _ in uses],
                         ["actions/checkout", "actions/setup-python"])
        for ref, comment in uses:
            with self.subTest(ref=ref):
                action, sha = ref.split("@")
                self.assertEqual((sha, comment), (ci.ACTION_PINS[action][0],
                                                  f" # {ci.ACTION_PINS[action][1]}"))
        # The default depth: only the merge ref's own committed policy is read.
        self.assertEqual(steps[0]["with"], {"persist-credentials": False})
        self.assertEqual(steps[1]["with"], {"python-version": "3.12"})


class _WorstCase(dict):
    """A ``_gh_eval`` context that assumes the worst for what a run decides
    while it runs: every ``needs.*.result`` is ``success``, the change
    detection says code, and ``release-plan`` resumes an ancestor. Anything
    else not named is null."""

    def get(self, key, default=None):
        if key in self:
            return self[key]
        if key.startswith("needs.") and key.endswith(".result"):
            return "success"
        return {"needs.changes.outputs.code": "true",
                "needs.release-plan.outputs.state": "RESUME",
                "needs.release-plan.outputs.commit": "a",
                "github.sha": "b"}.get(key)


def _called_model(job: dict) -> "dict | None":
    uses = job.get("uses", "")
    if uses.startswith("./.github/workflows/"):
        return {fname: build() for fname, (build, _) in ci.WORKFLOWS.items()}[uses.rsplit("/", 1)[1]]
    return None


def _runner_nodes(model: dict, context: _WorstCase) -> dict:
    """``model``'s jobs, the called local workflows flattened in, as
    ``{node: (weight, active, direct_dependencies)}``. A static job is one
    runner, a call to a shared workflow is one (assumed, see CI3 D4), a matrix
    counts its ``max-parallel`` and fails without one."""
    nodes: dict = {}
    groups: dict = {}
    for name, job in _jobs(model).items():
        active = bool(_gh_eval(job["if"], context)) if "if" in job else True
        outer = [member for need in job.get("needs", []) for member in groups[need]]
        child = _called_model(job)
        if child is None:
            matrix = job.get("strategy", {}).get("matrix")
            if matrix is not None and "max-parallel" not in job["strategy"]:
                raise AssertionError(f"{name}: a matrix without max-parallel is unbounded")
            weight = job["strategy"]["max-parallel"] if matrix is not None else 1
            nodes[name] = (weight, active, set(outer))
            groups[name] = [name]
            continue
        declared = child["on"]["workflow_call"].get("inputs", {})
        inner = _WorstCase(context)
        inner.update({f"inputs.{key}": "" if key == ci.REUSE_INPUT else "success" for key in declared})
        grandchildren = _runner_nodes(child, inner)
        groups[name] = []
        for key, (weight, child_active, deps) in grandchildren.items():
            nodes[f"{name}/{key}"] = (weight, active and child_active,
                                      {f"{name}/{d}" for d in deps} | set(outer))
            groups[name].append(f"{name}/{key}")
    return nodes


def _heaviest_independent_set(nodes: dict) -> int:
    """The largest total weight of active jobs, no two ordered by ``needs``
    (directly or through any job, a skipped one included): the most runners
    the graph can hold at once, with no assumption about how long any job runs."""
    after = {name: set(deps) for name, (_, _, deps) in nodes.items()}
    changed = True
    while changed:
        changed = False
        for name, deps in after.items():
            grown = set(deps).union(*(after[d] for d in deps))
            if grown != deps:
                after[name] = grown
                changed = True
    live = sorted((name for name, (weight, active, _) in nodes.items() if active and weight),
                  key=lambda name: -nodes[name][0])
    ordered = lambda a, b: a in after[b] or b in after[a]  # noqa: E731
    best = 0

    def search(index: int, chosen: list, total: int) -> None:
        nonlocal best
        best = max(best, total)
        if index == len(live) or total + sum(nodes[n][0] for n in live[index:]) <= best:
            return
        name = live[index]
        if not any(ordered(name, other) for other in chosen):
            search(index + 1, chosen + [name], total + nodes[name][0])
        search(index + 1, chosen, total)

    search(0, [], 0)
    return best


class RunnerLimitTest(unittest.TestCase):
    """CI3 D4/D5: one run holds at most ``RUNNER_LIMIT`` runners, the managed
    conformance job, ``PR title`` and the main reuse report included, proved
    from the generated models and counted against the whole set: no runner is
    excused because it ends quickly."""

    MODES = ("on", "off", None, "report")

    @classmethod
    def setUpClass(cls) -> None:
        cls.models = _models()
        text = CONFORMANCE_YML.read_text(encoding="utf-8")
        cls.conformance_text = text
        trigger_block = re.search(r"(?m)^on:\n((?:  .*\n)+)", text).group(1)
        cls.conformance_events = set(re.findall(r"(?m)^  (\w+):", trigger_block))

    def _outside(self, event: str, ref: str, mode: "str | None") -> int:
        total = 0
        if event in self.conformance_events:
            total += 1  # workflow-conformance: one job, asserted below
        if event in self.models["pr-title.yml"]["on"]:
            total += 1  # PR title, on every activity type, beside the shards
        report = self.models["main-reuse-report.yml"]
        if event in report["on"] and ref == "refs/heads/main":
            context = _WorstCase({"github.event_name": event, "github.ref": ref,
                                  "vars.CI_MAIN_REUSE": mode})
            total += sum(1 for job in _jobs(report).values() if _gh_eval(job["if"], context))
        return total

    def _peak(self, entry: str, event: str, ref: str, mode: "str | None") -> int:
        context = _WorstCase({"github.event_name": event, "github.ref": ref,
                              "vars.CI_MAIN_REUSE": mode})
        if entry != "ci.yml":  # dispatched alone: no input can be handed in
            context["inputs.tests-from-run"] = None
        nodes = _runner_nodes(self.models[entry], context)
        return _heaviest_independent_set(nodes) + self._outside(event, ref, mode)

    def _scenarios(self):
        yield "ci.yml", "pull_request", "refs/pull/7/merge"
        yield "ci.yml", "push", "refs/heads/main"
        yield "ci.yml", "workflow_dispatch", "refs/heads/main"
        yield "ci.yml", "workflow_dispatch", "refs/heads/topic"
        yield "tests.yml", "workflow_dispatch", "refs/heads/main"
        yield "tests.yml", "workflow_dispatch", "refs/heads/topic"
        yield "build-publish.yml", "workflow_dispatch", "refs/heads/main"
        yield "build-publish.yml", "workflow_dispatch", "refs/heads/topic"

    def test_no_event_and_no_reuse_mode_holds_more_than_the_limit(self) -> None:
        for entry, event, ref in self._scenarios():
            for mode in self.MODES:
                with self.subTest(entry=entry, event=event, ref=ref, mode=mode):
                    self.assertLessEqual(self._peak(entry, event, ref, mode), ci.RUNNER_LIMIT)

    def test_the_count_is_not_vacuous(self) -> None:
        # Shards, the docs/package lane, conformance and PR title: exactly the limit.
        self.assertEqual(self._peak("ci.yml", "pull_request", "refs/pull/7/merge", None), ci.RUNNER_LIMIT)
        # On main: the shards and the lane, conformance, and the report only where its if holds.
        main = {mode: self._peak("ci.yml", "push", "refs/heads/main", mode) for mode in self.MODES}
        self.assertEqual(main, {"on": 5, "off": 5, None: 6, "report": 6})
        # A direct build-publish run classifies and nothing else.
        self.assertEqual(self._peak("build-publish.yml", "workflow_dispatch", "refs/heads/main", None), 1)

    def test_a_matrix_without_max_parallel_fails(self) -> None:
        model = {"jobs": {"tests": {"runs-on": "ubuntu-latest", "strategy": {"matrix": {"shard": "x"}}}}}
        with self.assertRaises(AssertionError):
            _runner_nodes(model, _WorstCase())

    def test_every_shard_matrix_is_bounded_and_tied_to_the_planner_ceiling(self) -> None:
        count = 0
        for name, model in self.models.items():
            for job_name, job in _jobs(model).items():
                if "matrix" in job.get("strategy", {}):
                    count += 1
                    with self.subTest(workflow=name, job=job_name):
                        self.assertEqual(job["strategy"]["max-parallel"], ci.TEST_MAX_PARALLEL)
        self.assertEqual(count, 1)
        # The model does not import the planner: this test ties the two numbers.
        self.assertLessEqual(ci.TEST_MAX_PARALLEL, shards.CI_MAX_SHARDS)
        # Option B (CI3 O1): the planner plans no more shards than may run.
        self.assertEqual(ci.TEST_MAX_PARALLEL, shards.CI_MAX_SHARDS)
        self.assertEqual(ci.RUNNER_LIMIT, 6)

    def test_the_ordering_is_what_the_count_relies_on(self) -> None:
        jobs = _jobs(self.models["tests.yml"])
        self.assertEqual((jobs["docs"]["needs"], jobs["package"]["needs"]), (["docs-tests"], ["docs"]))
        for check in ("determinism", "mutations"):
            self.assertEqual(_jobs(self.models["ci.yml"])[f"after-merge-{check}"]["needs"], ["validate"])

    def test_docs_tests_counts_one_runner_through_a_pinned_called_workflow(self) -> None:
        job = _jobs(self.models["tests.yml"])["docs-tests"]
        # An assumption about a workflow on a mutable ref this test cannot read
        # offline: pinning the value makes a change of the called workflow visible.
        self.assertEqual(job["uses"], "RodrigoFAbreu/action-workflows/.github/workflows/python-tests.yml@main")
        nodes = _runner_nodes(self.models["tests.yml"], _WorstCase({"inputs.tests-from-run": ""}))
        self.assertEqual(nodes["docs-tests"][0], 1)

    def test_the_managed_conformance_workflow_is_one_job(self) -> None:
        body = self.conformance_text.split("\njobs:\n", 1)[1]
        self.assertEqual(re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", body), ["workflow-conformance"])
        self.assertEqual(self.conformance_events, {"pull_request", "push"})

    def test_package_is_never_skipped_by_an_upstream_result(self) -> None:
        # GitHub treats a skipped required check as satisfied, so package must
        # run whatever docs (and a skipped tests job, on a documentation-only
        # pull request) ended as, on every event.
        job = _jobs(self.models["tests.yml"])["package"]
        for event in ("pull_request", "push", "workflow_dispatch"):
            for result in ("success", "failure", "cancelled", "skipped"):
                with self.subTest(event=event, docs=result):
                    context = _WorstCase({"github.event_name": event, "needs.docs.result": result,
                                          "needs.tests.result": "skipped"})
                    self.assertTrue(_gh_eval(job["if"], context))
        self.assertEqual(job["if"], "!cancelled()")

    def test_the_required_check_names_are_unchanged(self) -> None:
        names = {f"validate / {job}" for job in ("plan", "tests-result", "package")}
        self.assertEqual(names, {"validate / plan", "validate / tests-result", "validate / package"})
        for job in ("plan", "tests-result", "package"):
            self.assertIn(job, _jobs(self.models["tests.yml"]))
            self.assertNotIn("name", _jobs(self.models["tests.yml"])[job])
        self.assertIn("validate", _jobs(self.models["ci.yml"]))
        self.assertEqual(_jobs(self.models["pr-title.yml"])["title"]["name"], "PR title")


#: ``tests.yml``'s and 1.3.0's ``main.yml``'s pipx smoke test (``build-publish.yml``'s with
#: ``$RELEASE_VERSION``).
PIPX_SMOKE_1_3_0 = "\n".join([
    "pipx install dist/*.whl",
    'expected="workflow-controller $(python3 tools/release.py version)"',
    'actual="$(workflow-controller --version)"',
    "actual=\"${actual%%$'\\n'*}\"",
    'if [ "$actual" != "$expected" ]; then',
    '  echo "workflow-controller --version line 1 is \'$actual\', expected \'$expected\'" >&2',
    "  exit 1",
    "fi",
])


if __name__ == "__main__":
    unittest.main()
