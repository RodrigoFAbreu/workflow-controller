"""Tests for ``tools/ci_workflows.py`` (CP9): the committed workflow files
equal the render, the emitter's quoting and layout, shard coverage, the
conformance matrix, and the structure of ``ci.yml``, ``validate.yml`` and
``release.yml``.

The assertions are made on the Python model, and the committed bytes are
asserted equal to its render, so what GitHub runs is what is tested here. A
PyYAML cross-check runs only when PyYAML happens to be importable.
"""

from __future__ import annotations

import contextlib
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

    def test_managed_conformance_workflow_is_untouched(self) -> None:
        record = json.loads(INSTALLATION.read_text(encoding="utf-8"))
        expected = record["managed"][".github/workflows/workflow-conformance.yml"]["sha256"]
        self.assertEqual(hashlib.sha256(CONFORMANCE_YML.read_bytes()).hexdigest(), expected)

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
    def test_every_test_module_is_placed_exactly_once(self) -> None:
        discovered = {path.stem for path in (fixtures.REPO_ROOT / "tests").glob("test_*.py")}
        placed = [module for modules in ci.CONTROLLER_SHARDS.values() for module in modules]
        placed += ci.PACKAGE_TEST_MODULES + list(ci.EXCLUDED_TEST_MODULES)
        duplicates = sorted({module for module in placed if placed.count(module) > 1})
        self.assertEqual(duplicates, [])
        self.assertEqual(set(placed), discovered)

    def test_the_shards_are_what_the_controller_job_runs(self) -> None:
        controller = _jobs(ci.validate_workflow())["controller"]
        include = controller["strategy"]["matrix"]["include"]
        self.assertEqual({row["shard"]: row["modules"].split() for row in include},
                         {name: [f"tests.{m}" for m in modules]
                          for name, modules in ci.CONTROLLER_SHARDS.items()})
        self.assertIn("python -m unittest ${{ matrix.modules }} -v", map(_run, _steps(controller)))

    def test_package_job_runs_the_packaged_runtime_tests(self) -> None:
        package = _jobs(ci.validate_workflow())["package"]
        runs = [_run(step) for step in _steps(package)]
        for module in ci.PACKAGE_TEST_MODULES:
            self.assertIn(f"CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python -m unittest tests.{module} -v",
                          runs)

    def test_the_exclusion_has_a_reason(self) -> None:
        for module, reason in ci.EXCLUDED_TEST_MODULES.items():
            self.assertIn("claude", reason, module)

    def test_conformance_matrix_equals_the_managed_workflow(self) -> None:
        managed = re.findall(r"(?m)^\s*run: python3 (\S+)\s*$",
                             CONFORMANCE_YML.read_text(encoding="utf-8"))
        self.assertTrue(managed)
        conformance = _jobs(ci.validate_workflow())["conformance"]
        self.assertEqual(conformance["strategy"]["matrix"]["suite"], managed)
        step = _steps(conformance)[-1]
        self.assertEqual((step["working-directory"], _run(step)),
                         ("scripts", "python3 ${{ matrix.suite }}"))


class AllJobsTest(unittest.TestCase):
    def test_matrix_jobs_do_not_fail_fast(self) -> None:
        for name, workflow in _models().items():
            for job_name, job in _jobs(workflow).items():
                if "strategy" in job:
                    with self.subTest(workflow=name, job=job_name):
                        self.assertIn("matrix", job["strategy"])
                        self.assertIs(job["strategy"]["fail-fast"], False)

    def test_every_job_runs_on_ubuntu_latest_or_calls_validate(self) -> None:
        for name, workflow in _models().items():
            for job_name, job in _jobs(workflow).items():
                with self.subTest(workflow=name, job=job_name):
                    if "uses" in job:
                        self.assertEqual(job["uses"], "./.github/workflows/validate.yml")
                        self.assertNotIn("steps", job)
                    else:
                        self.assertEqual(job["runs-on"], "ubuntu-latest")

    def test_validate_jobs_are_read_only(self) -> None:
        for job_name, job in _jobs(ci.validate_workflow()).items():
            with self.subTest(job=job_name):
                self.assertEqual(job["permissions"], {"contents": "read"})

    def test_only_publish_can_write(self) -> None:
        writers = [(name, job_name) for name, workflow in _models().items()
                   for job_name, job in _jobs(workflow).items()
                   if "write" in job.get("permissions", {}).values()]
        self.assertEqual(writers, [("release.yml", "publish")])
        for name, workflow in _models().items():
            with self.subTest(workflow=name):
                self.assertNotIn("write", workflow.get("permissions", {}).values())

    def test_no_cache_no_secret_no_live_worker(self) -> None:
        for name, text in ci.render().items():
            with self.subTest(workflow=name):
                self.assertNotIn("actions/cache", text)
                self.assertNotIn("secrets.", text)
                self.assertNotIn("CONTROLLER_LIVE_WORKER", text)
                self.assertNotRegex(text, r"(?m)^\s*(run: .*)?\bclaude\b")

    def test_artifacts_only_in_release_build_and_publish(self) -> None:
        found = sorted((name, job_name, _uses(step).split("@")[0])
                       for name, job_name, _, step in _all_steps()
                       if "-artifact" in _uses(step))
        self.assertEqual(found, [("release.yml", "build", "actions/upload-artifact"),
                                 ("release.yml", "publish", "actions/download-artifact")])

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
        package = _jobs(ci.validate_workflow())["package"]
        verify = [_run(s) for s in _steps(package) if "verify-wheel" in _run(s)]
        self.assertEqual(verify, ['python3 tools/release.py verify-wheel dist/*.whl --local '
                                  '--commit "$(git rev-parse "$GITHUB_SHA^{commit}")"'])
        self.assertEqual(_steps(package)[0]["with"], {"fetch-depth": 0})
        self.assertIn('pip install "setuptools>=70.1"', map(_run, _steps(package)))
        self.assertIn(ci.PIPX_SMOKE, map(_run, _steps(package)))

    def test_no_release_mutation_other_than_create(self) -> None:
        for name, job_name, _, step in _all_steps():
            run = _run(step)
            with self.subTest(workflow=name, job=job_name, run=run):
                for forbidden in ("--clobber", "gh release upload", "gh release delete",
                                  "gh release edit"):
                    self.assertNotIn(forbidden, run)

    def test_gh_steps_see_gh_token(self) -> None:
        checked = 0
        for name, job_name, job, step in _all_steps():
            run = _run(step)
            if re.search(r"(^|\s)gh\s", run) or "check-unpublished" in run:
                checked += 1
                env = {**job.get("env", {}), **step.get("env", {})}
                with self.subTest(workflow=name, job=job_name, run=run):
                    self.assertEqual(env.get("GH_TOKEN"), "${{ github.token }}")
        self.assertEqual(checked, 2)

    def test_release_commit_steps_have_it_in_their_environment(self) -> None:
        checked = 0
        for job_name, job in _jobs(ci.release_workflow()).items():
            steps = _steps(job)
            peel = [i for i, step in enumerate(steps) if step.get("id") == "peel"]
            for i, step in enumerate(steps):
                if "$RELEASE_COMMIT" not in _run(step) or step.get("id") == "peel":
                    continue
                checked += 1
                env = {**job.get("env", {}), **step.get("env", {})}
                with self.subTest(job=job_name, step=step.get("name")):
                    self.assertTrue("RELEASE_COMMIT" in env or (peel and peel[0] < i))
        for name, workflow in _models().items():
            if name != "release.yml":
                self.assertNotIn("$RELEASE_COMMIT", ci.emit(workflow))
        self.assertEqual(checked, 4)


class CiWorkflowTest(unittest.TestCase):
    def test_triggers(self) -> None:
        workflow = ci.ci_workflow()
        self.assertEqual(workflow["on"], {"push": {"branches": ["main"]}, "pull_request": None})
        self.assertNotIn("tags", workflow["on"]["push"])

    def test_same_ref_runs_cancel(self) -> None:
        concurrency = ci.ci_workflow()["concurrency"]
        self.assertIn("github.workflow", concurrency["group"])
        self.assertIn("github.ref", concurrency["group"])
        self.assertIs(concurrency["cancel-in-progress"], True)

    def test_calls_validate(self) -> None:
        self.assertEqual(_jobs(ci.ci_workflow()),
                         {"validate": {"uses": "./.github/workflows/validate.yml"}})

    def test_validate_is_workflow_call_only(self) -> None:
        self.assertEqual(ci.validate_workflow()["on"], {"workflow_call": None})


class ReleaseWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = ci.release_workflow()
        self.jobs = _jobs(self.workflow)
        self.build = _steps(self.jobs["build"])
        self.publish = _steps(self.jobs["publish"])

    def test_trigger_and_concurrency(self) -> None:
        self.assertEqual(self.workflow["on"], {"push": {"tags": ["v*"]}})
        self.assertIs(self.workflow["concurrency"]["cancel-in-progress"], False)
        self.assertIn("github.ref", self.workflow["concurrency"]["group"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_gating(self) -> None:
        self.assertEqual(self.jobs["validate"], {"uses": "./.github/workflows/validate.yml"})
        self.assertLessEqual({"validate"}, set(self.jobs["build"]["needs"]))
        self.assertLessEqual({"validate", "build"}, set(self.jobs["publish"]["needs"]))
        self.assertEqual(self.jobs["publish"]["permissions"], {"contents": "write"})

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        uses = re.findall(r"(?m)^\s*(?:- )?uses: (\S+)(.*)$", ci.render()["release.yml"])
        actions = [(ref, comment) for ref, comment in uses if not ref.startswith('"./')]
        self.assertEqual(len(actions), 6)
        for ref, comment in actions:
            with self.subTest(ref=ref):
                self.assertRegex(ref, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
                action = ref.split("@")[0]
                self.assertEqual(comment, f" # {ci.ACTION_PINS[action][1]}")

    def test_build_order(self) -> None:
        def at(fragment: str) -> int:
            return _index(self.build, lambda s: fragment in _run(s))

        order = [at("release.py verify-tag "), _index(self.build, lambda s: s.get("id") == "peel"),
                 at("merge-base --is-ancestor"), at("pip wheel"), at("verify-wheel"),
                 at("pipx install"), at("checksums")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(self.build[0]["with"], {"fetch-depth": 0})
        self.assertEqual(_run(self.build[order[2]]),
                         'git merge-base --is-ancestor "$RELEASE_COMMIT" origin/main')
        self.assertEqual(_run(self.build[order[4]]),
                         'python3 tools/release.py verify-wheel dist/*.whl '
                         '--tag "$GITHUB_REF_NAME" --commit "$RELEASE_COMMIT"')
        self.assertIn('WORKFLOW_CONTROLLER_RELEASE_TAG="$GITHUB_REF_NAME"', _run(self.build[order[3]]))

    def test_peel_and_its_output(self) -> None:
        peel_at = _index(self.build, lambda s: s.get("id") == "peel")
        run = _run(self.build[peel_at])
        self.assertIn('RELEASE_COMMIT=$(git rev-parse "$GITHUB_SHA^{commit}")', run)
        self.assertIn('echo "RELEASE_COMMIT=$RELEASE_COMMIT" >> "$GITHUB_ENV"', run)
        self.assertIn('echo "release_commit=$RELEASE_COMMIT" >> "$GITHUB_OUTPUT"', run)
        self.assertEqual(self.jobs["build"]["outputs"],
                         {"release_commit": "${{ steps.peel.outputs.release_commit }}"})
        for job in self.jobs.values():
            for value in job.get("outputs", {}).values():
                self.assertNotIn("env.", value)
        for step in self.build[:peel_at]:
            self.assertNotIn("RELEASE_COMMIT", _run(step))
        for step in [*self.build[peel_at + 1:], *self.publish]:
            self.assertNotIn("GITHUB_SHA", json.dumps(step, default=str))

    def test_publish_environment(self) -> None:
        self.assertEqual(self.jobs["publish"]["env"],
                         {"GH_TOKEN": "${{ github.token }}",
                          "RELEASE_COMMIT": "${{ needs.build.outputs.release_commit }}"})
        for step in self.publish:
            self.assertNotIn("env", step)

    def test_publish_order(self) -> None:
        checkout = _index(self.publish, lambda s: _uses(s).startswith("actions/checkout@"))
        first_tool = min(i for i, s in enumerate(self.publish) if "tools/release.py" in _run(s))
        self.assertLess(checkout, first_tool)
        unpublished = _index(self.publish, lambda s: "check-unpublished" in _run(s))
        reverify = _index(self.publish, lambda s: "verify-wheel" in _run(s))
        tag_commit = _index(self.publish, lambda s: "verify-tag-commit" in _run(s))
        create = _index(self.publish, lambda s: "gh release create" in _run(s))
        self.assertLess(unpublished, tag_commit)
        self.assertEqual(tag_commit + 1, create)
        self.assertEqual(create, len(self.publish) - 1)
        self.assertEqual(_run(self.publish[tag_commit]),
                         'python3 tools/release.py verify-tag-commit "$GITHUB_REF_NAME" "$RELEASE_COMMIT"')
        self.assertIn('--commit "$RELEASE_COMMIT"', _run(self.publish[reverify]))
        self.assertIn('--tag "$GITHUB_REF_NAME"', _run(self.publish[reverify]))
        self.assertIn("--verify-tag", _run(self.publish[create]).split())
        self.assertNotIn("--draft", _run(self.publish[create]))

    def test_artifact_contract(self) -> None:
        upload = self.build[_index(self.build, lambda s: "upload-artifact" in _uses(s))]["with"]
        download = self.publish[_index(self.publish, lambda s: "download-artifact" in _uses(s))]["with"]
        self.assertEqual(upload["name"], download["name"])

        wheel_dir = re.search(r"pip wheel .*-w (\S+)", _run(
            self.build[_index(self.build, lambda s: "pip wheel" in _run(s))])).group(1)
        sums_dir = re.search(r"checksums (\S+)$", _run(
            self.build[_index(self.build, lambda s: "checksums" in _run(s))])).group(1)
        self.assertEqual(_normalise(upload["path"]), _normalise(wheel_dir))
        self.assertEqual(_normalise(upload["path"]), _normalise(sums_dir))

        after = self.publish[_index(self.publish, lambda s: "download-artifact" in _uses(s)) + 1:]
        globs = []
        for step in after:
            run = _run(step)
            if "verify-wheel" in run:
                globs.append(re.search(r"verify-wheel (\S+)", run).group(1))
            if "gh release create" in run:
                for arg in shlex.split(run)[4:]:
                    if arg.startswith("-"):
                        break
                    globs.append(arg)
        self.assertEqual(globs, ["dist/*.whl", "dist/*.whl", "dist/SHA256SUMS"])
        for glob in globs:
            self.assertEqual(_dir_of(glob), _normalise(download["path"]))

    def test_cp9_records_the_documented_sources(self) -> None:
        self.assertIn("docs.github.com", ci.GITHUB_SHA_NOTE)
        self.assertIn("Tip commit pushed to the ref", ci.GITHUB_SHA_NOTE)
        self.assertIn("cli.github.com/manual/gh_release_create", ci.GH_RELEASE_CREATE_NOTE)
        release = ci.render()["release.yml"]
        self.assertIn("Tip commit pushed to the ref", release)
        self.assertIn("gh_release_create", release)


if __name__ == "__main__":
    unittest.main()
