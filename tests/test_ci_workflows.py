"""Tests for ``tools/ci_workflows.py`` (CP9, and
``workflow-controller-trunk-branch-pr-release-orchestration`` CP5): the
committed workflow files equal the render, the retired ``release.yml`` is
absent, the emitter's quoting and layout, shard coverage, the conformance
matrix, and the structure of ``ci.yml``, ``validate.yml`` and ``main.yml``.

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

    def test_the_tag_triggered_release_workflow_is_retired(self) -> None:
        self.assertEqual(ci.RETIRED_WORKFLOWS, ("release.yml",))
        self.assertNotIn("release.yml", ci.WORKFLOWS)
        self.assertFalse((WORKFLOWS / "release.yml").exists())

    def test_a_retired_file_fails_the_check_and_write_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(WORKFLOWS, root / ".github" / "workflows")
            retired = root / ".github" / "workflows" / "release.yml"
            retired.write_text("name: Release\n")
            self.assertEqual(ci.check(root), [".github/workflows/release.yml"])
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(ci.main(["--check", "--root", str(root)]), 1)
            self.assertIn("release.yml", stderr.getvalue())
            self.assertEqual(ci.main(["--write", "--root", str(root)]), 0)
            self.assertFalse(retired.exists())
            self.assertEqual(ci.check(root), [])

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
        self.assertEqual(writers, [("main.yml", "publish")])
        for name, workflow in _models().items():
            with self.subTest(workflow=name):
                self.assertEqual(workflow.get("permissions", {"contents": "read"}), {"contents": "read"})

    def test_no_cache_no_secret_no_live_worker(self) -> None:
        for name, text in ci.render().items():
            with self.subTest(workflow=name):
                self.assertNotIn("actions/cache", text)
                self.assertNotIn("secrets.", text)
                self.assertNotIn("CONTROLLER_LIVE_WORKER", text)
                self.assertNotRegex(text, r"(?m)^\s*(run: .*)?\bclaude\b")

    def test_artifacts_only_in_main_build_and_publish(self) -> None:
        found = sorted((name, job_name, _uses(step).split("@")[0])
                       for name, job_name, _, step in _all_steps()
                       if "-artifact" in _uses(step))
        self.assertEqual(found, [("main.yml", "build", "actions/upload-artifact"),
                                 ("main.yml", "publish", "actions/download-artifact")])

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
        self.assertEqual(_steps(package)[0]["with"]["fetch-depth"], 0)
        self.assertIn('pip install "setuptools>=70.1"', map(_run, _steps(package)))
        self.assertIn(ci.PIPX_SMOKE, map(_run, _steps(package)))

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
                checked.append(job_name)
                env = {**job.get("env", {}), **step.get("env", {})}
                with self.subTest(workflow=name, job=job_name, run=run):
                    self.assertEqual(env.get("GH_TOKEN"), "${{ github.token }}")
        self.assertEqual(checked, ["release-plan", "publish"])

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
        # validate.yml's three jobs, then release-plan, build and publish.
        self.assertEqual(checkouts, 6)


class CiWorkflowTest(unittest.TestCase):
    def test_triggers(self) -> None:
        self.assertEqual(ci.ci_workflow()["on"], {"pull_request": None})

    def test_same_ref_runs_cancel(self) -> None:
        concurrency = ci.ci_workflow()["concurrency"]
        self.assertEqual(concurrency["group"], "${{ github.workflow }}-${{ github.ref }}")
        self.assertIs(concurrency["cancel-in-progress"], True)
        self.assertEqual(ci.ci_workflow()["permissions"], {"contents": "read"})

    def test_calls_validate(self) -> None:
        self.assertEqual(_jobs(ci.ci_workflow()),
                         {"validate": {"uses": "./.github/workflows/validate.yml"}})

    def test_validate_is_workflow_call_only(self) -> None:
        self.assertEqual(ci.validate_workflow()["on"], {"workflow_call": None})


class MainWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = ci.main_workflow()
        self.jobs = _jobs(self.workflow)
        self.plan = _steps(self.jobs["release-plan"])
        self.build = _steps(self.jobs["build"])
        self.publish = _steps(self.jobs["publish"])

    def test_triggers(self) -> None:
        # workflow_dispatch has no inputs: it classifies the trunk tip, and
        # RESUME reaches an interrupted release's own tagged commit from there.
        self.assertEqual(self.workflow["on"], {"push": {"branches": ["main"]}, "workflow_dispatch": None})
        self.assertIn("workflow_dispatch:\n", ci.render()["main.yml"])
        self.assertNotIn("inputs", ci.render()["main.yml"])
        self.assertNotIn("pull_request", self.workflow["on"])
        self.assertNotIn("tags", self.workflow["on"]["push"])

    def test_concurrency_never_cancels(self) -> None:
        self.assertEqual(self.workflow["concurrency"],
                         {"group": "main-release", "cancel-in-progress": False})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_needs_chain(self) -> None:
        self.assertEqual(list(self.jobs), ["validate", "release-plan", "build", "publish"])
        self.assertEqual(self.jobs["validate"], {"uses": "./.github/workflows/validate.yml"})
        self.assertEqual(self.jobs["release-plan"]["needs"], ["validate"])
        self.assertEqual(self.jobs["build"]["needs"], ["validate", "release-plan"])
        self.assertEqual(self.jobs["publish"]["needs"], ["validate", "release-plan", "build"])

    def test_conditions(self) -> None:
        on_trunk = "github.ref == 'refs/heads/main'"
        self.assertEqual(self.jobs["release-plan"]["if"], on_trunk)
        publishing = (f"{on_trunk} && (needs.release-plan.outputs.state == 'RELEASE_DUE' || "
                      "needs.release-plan.outputs.state == 'RESUME')")
        self.assertEqual(self.jobs["build"]["if"], publishing)
        self.assertEqual(self.jobs["publish"]["if"], publishing)

    def test_permissions(self) -> None:
        self.assertNotIn("permissions", self.jobs["release-plan"])
        self.assertNotIn("permissions", self.jobs["build"])
        self.assertEqual(self.jobs["publish"]["permissions"], {"contents": "write"})
        self.assertEqual(self.jobs["release-plan"]["env"], {"GH_TOKEN": "${{ github.token }}"})
        self.assertNotIn("env", self.jobs["build"])
        self.assertEqual(self.jobs["publish"]["env"],
                         {"GH_TOKEN": "${{ github.token }}",
                          "COMMIT": "${{ needs.release-plan.outputs.commit }}"})
        steps_with_env = [(job_name, step.get("name")) for job_name, job in self.jobs.items()
                          for step in _steps(job) if "env" in step]
        self.assertEqual(steps_with_env, [("release-plan", "classify")])

    def test_classify_alone_lends_git_the_read_only_token(self) -> None:
        # The repository is private and the checkout does not persist its
        # token; classify's fetch and ls-remote reach origin through gh as a
        # credential helper, which reads the job's read-only GH_TOKEN.
        classify = self.plan[_index(self.plan, lambda s: "classify" in _run(s))]
        self.assertEqual(classify["env"], {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
            "GIT_CONFIG_VALUE_0": "!gh auth git-credential",
        })
        self.assertNotIn("permissions", self.jobs["release-plan"])
        self.assertIs(self.plan[0]["with"]["persist-credentials"], False)

    def test_every_action_outside_validate_is_pinned_to_a_commit(self) -> None:
        uses = re.findall(r"(?m)^\s*(?:- )?uses: (\S+)(.*)$", ci.render()["main.yml"])
        actions = [(ref, comment) for ref, comment in uses if not ref.startswith('"./')]
        self.assertEqual(len(actions), 8)
        for ref, comment in actions:
            with self.subTest(ref=ref):
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
        self.assertEqual(_run(self.build[order[0]]), "python3 tools/release.py build")
        self.assertEqual(_run(self.build[order[1]]), "python3 tools/release.py verify")
        self.assertEqual(_run(self.build[order[2]]), ci.PIPX_SMOKE)

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
        main = ci.render()["main.yml"]
        self.assertIn("Tip commit pushed to the ref", main)
        self.assertIn("gh_release_create", main)


if __name__ == "__main__":
    unittest.main()
