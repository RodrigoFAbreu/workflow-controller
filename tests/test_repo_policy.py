"""The repository policy (trunk-branch-pr-release CP2, "Repository policy").

``.workflow-controller/policy.json`` is validated strictly, every rule
refusing with its own case, and is read from a committed tree, never the
working tree. An absent file means both switches are off.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import repo_policy  # noqa: E402
from controller.errors import ControllerError, InvalidRepositoryPolicyError  # noqa: E402
from tests import fixtures  # noqa: E402

REFERENCE = fixtures.REPO_ROOT / repo_policy.POLICY_PATH
PLAN = fixtures.REPO_ROOT / "docs" / "ai-workflow" / "CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md"
SQUASH_PLAN = fixtures.REPO_ROOT / "docs" / "ai-workflow" / "CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md"


def _legacy() -> dict:
    return json.loads(fixtures.LEGACY_POLICY)


def _conventional() -> dict:
    return json.loads(fixtures.CONVENTIONAL_POLICY)


def _plan_block(path: Path, heading: str) -> dict:
    block = re.search(re.escape(heading) + r"\n\n```json\n(.*?)\n```", path.read_text(encoding="utf-8"),
                      re.DOTALL)
    assert block is not None, f"{path.name} has no JSON block after {heading!r}"
    return json.loads(block.group(1))


#: The trunk plan's reference block: this repository's policy before the
#: squash-merge cutover.
PRE_CUTOVER_HEADING = "Reference file (the exact CP2 content):"
#: The squash-merge plan's block: the policy after it (Design H).
POST_CUTOVER_HEADING = "**Post-cutover policy (the exact CP7 rehearsal content):**"


def _encode(data: dict) -> bytes:
    return json.dumps(data, indent=2).encode("utf-8")


class ReferencePolicyTest(unittest.TestCase):
    def test_the_reference_file_validates(self) -> None:
        policy = repo_policy.parse_policy(REFERENCE.read_bytes())
        self.assertEqual(policy.trunk_branch, "main")
        self.assertEqual(policy.trunk_remote, "origin")
        self.assertEqual(policy.forge_repository, "RodrigoFAbreu/workflow-controller")
        self.assertTrue(repo_policy.milestone_branches_enabled(policy))
        self.assertTrue(repo_policy.release_enabled(policy))
        self.assertEqual(policy.milestone_branches.branch_name("some-item"), "milestone/some-item")
        self.assertEqual(policy.release.tag_for("1.2.3"), "v1.2.3")
        self.assertEqual(policy.release.version_of_tag("v1.1.0"), "1.1.0")
        self.assertEqual(policy.release.abandoned_tags, ("v1.1.0",))
        self.assertEqual(policy.sha256, repo_policy.hashlib.sha256(REFERENCE.read_bytes()).hexdigest())
        argv, env = policy.release.verify.render(
            {"artifact": "dist/w.whl", "tag": "v1.2.3", "commit": "c" * 40, "version": "1.2.3"})
        self.assertEqual(argv, ["python3", "tools/release.py", "verify-wheel", "dist/w.whl",
                                "--tag", "v1.2.3", "--commit", "c" * 40])
        self.assertEqual(env, {})
        _, env = policy.release.build.render({"tag": "v1.2.3", "version": "1.2.3", "commit": "c"})
        self.assertEqual(env, {"WORKFLOW_CONTROLLER_RELEASE_TAG": "v1.2.3"})

    def test_the_reference_file_is_one_plans_exact_content(self) -> None:
        # Exactly the pre-cutover or the post-cutover content, and no other.
        reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        pre = _plan_block(PLAN, PRE_CUTOVER_HEADING)
        post = _plan_block(SQUASH_PLAN, POST_CUTOVER_HEADING)
        self.assertIn(reference, (pre, post))
        self.assertEqual(reference["release"]["trigger"],
                         "version_change" if reference == pre else "conventional_commit")

    def test_the_named_fixture_policies_are_the_plans_blocks(self) -> None:
        self.assertEqual(_legacy(), _plan_block(PLAN, PRE_CUTOVER_HEADING))
        self.assertEqual(_conventional(), _plan_block(SQUASH_PLAN, POST_CUTOVER_HEADING))

    def test_the_post_cutover_block_validates(self) -> None:
        # The rehearsed content can never drift from what the parser accepts.
        raw = json.dumps(_plan_block(SQUASH_PLAN, POST_CUTOVER_HEADING)).encode("utf-8")
        policy = repo_policy.parse_policy(raw)
        self.assertEqual(policy.release.trigger, "conventional_commit")
        self.assertEqual(policy.milestone_branches.merge_method, "squash")
        self.assertEqual(policy.release.change_types["feat"], "minor")
        self.assertEqual(policy.release.bump_overrides, {})
        self.assertIsNone(policy.release.version_source_kind)
        self.assertIsNone(policy.release.version_source_path)
        # Everything but the release trigger and the merge method is the
        # pre-cutover policy's.
        legacy = repo_policy.parse_policy(fixtures.LEGACY_POLICY)
        for attribute in ("trunk_branch", "trunk_remote", "forge_kind", "forge_repository"):
            self.assertEqual(getattr(policy, attribute), getattr(legacy, attribute))
        for attribute in ("enabled", "version_scheme", "tag_format", "abandoned_tags", "build", "verify",
                          "artifact_paths", "checksums", "publication_kind", "publication_title",
                          "publication_notes"):
            self.assertEqual(getattr(policy.release, attribute), getattr(legacy.release, attribute))

    def test_the_reference_version_source_reads_this_repositorys_pyproject(self) -> None:
        policy = repo_policy.parse_policy(REFERENCE.read_bytes())
        text = (fixtures.REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        if policy.release.trigger == "version_change":
            self.assertEqual(policy.release.read_version(text), fixtures.CONTROLLER_VERSION)
        else:
            with self.assertRaisesRegex(ValueError, "conventional_commit"):
                policy.release.read_version(text)


class _RefusalCase(unittest.TestCase):
    def assertRefuses(self, data: dict | bytes, field: str | None, pattern: str) -> None:
        raw = data if isinstance(data, bytes) else _encode(data)
        with self.assertRaises(InvalidRepositoryPolicyError) as caught:
            repo_policy.parse_policy(raw)
        self.assertIsInstance(caught.exception, ControllerError)
        self.assertEqual(caught.exception.code, "INVALID_REPOSITORY_POLICY")
        self.assertEqual(caught.exception.evidence["field"], field)
        self.assertRegex(caught.exception.message, pattern)


class ValidationRefusalTest(_RefusalCase):
    """Every rule refuses, each with its own case."""

    def mutate(self, edit) -> dict:
        data = copy.deepcopy(_legacy())
        edit(data)
        return data

    # -- encoding, shape, schema ------------------------------------------

    def test_not_utf8_json_or_an_object(self) -> None:
        self.assertRefuses(b"\xff\xfe", None, "not UTF-8")
        self.assertRefuses(b"{", None, "not valid JSON")
        self.assertRefuses(b"[]", "(top level)", "must be a JSON object")

    def test_duplicate_key(self) -> None:
        raw = fixtures.LEGACY_POLICY.replace(b'"schema_version": 1,', b'"schema_version": 1, "schema_version": 1,')
        self.assertRefuses(raw, None, "duplicate key 'schema_version'")

    def test_unknown_key_at_every_level(self) -> None:
        cases = [
            ((lambda d: d.__setitem__("extra", 1)), "(top level)"),
            ((lambda d: d["trunk"].__setitem__("protected", True)), "trunk"),
            ((lambda d: d["forge"].__setitem__("host", "x")), "forge"),
            ((lambda d: d["milestone_branches"].__setitem__("merge_method", "merge")), "milestone_branches"),
            ((lambda d: d["milestone_branches"]["pull_request"].__setitem__("x", 1)),
             "milestone_branches.pull_request"),
            ((lambda d: d["release"].__setitem__("channel", "beta")), "release"),
            ((lambda d: d["release"]["version_source"].__setitem__("x", 1)), "release.version_source"),
            ((lambda d: d["release"]["build"].__setitem__("shell", True)), "release.build"),
            ((lambda d: d["release"]["verify"].__setitem__("cwd", ".")), "release.verify"),
            ((lambda d: d["release"]["artifacts"].__setitem__("x", 1)), "release.artifacts"),
            ((lambda d: d["release"]["publication"].__setitem__("draft", True)), "release.publication"),
        ]
        for edit, field in cases:
            with self.subTest(field=field):
                self.assertRefuses(self.mutate(edit), field, "unknown key")

    def test_missing_required_key(self) -> None:
        self.assertRefuses(self.mutate(lambda d: d["release"].pop("tag_format")), "release", "missing required")
        self.assertRefuses(self.mutate(lambda d: d.pop("trunk")), "(top level)", "missing required")

    def test_schema_version(self) -> None:
        for value in (2, 0, "1", True, 1.0):
            with self.subTest(value=value):
                self.assertRefuses(self.mutate(lambda d: d.__setitem__("schema_version", value)),
                                   "schema_version", "unsupported schema_version")

    # -- placeholders -----------------------------------------------------

    def test_unknown_placeholder(self) -> None:
        data = self.mutate(lambda d: d["release"]["verify"]["argv"].append("{branch}"))
        self.assertRefuses(data, "release.verify.argv[8]", r"unknown placeholder \{branch\}")

    def test_placeholder_outside_its_fields_set(self) -> None:
        data = self.mutate(lambda d: d["release"]["build"]["argv"].append("{artifact}"))
        self.assertRefuses(data, "release.build.argv[8]", r"does not admit")
        data = self.mutate(lambda d: d["release"]["artifacts"]["paths"].append("dist/{tag}.whl"))
        self.assertRefuses(data, "release.artifacts.paths[1]", r"does not admit")

    def test_unmatched_brace(self) -> None:
        data = self.mutate(lambda d: d["release"]["publication"].__setitem__("title", "{tag"))
        self.assertRefuses(data, "release.publication.title", "unmatched brace")

    # -- branch format ----------------------------------------------------

    def test_branch_format_needs_the_id_exactly_once(self) -> None:
        for value in ("milestone/fixed", "m/{work_item_id}/{work_item_id}"):
            with self.subTest(value=value):
                data = self.mutate(lambda d: d["milestone_branches"].__setitem__("branch_format", value))
                self.assertRefuses(data, "milestone_branches.branch_format", "exactly once")

    def test_branch_format_must_render_a_valid_ref_over_the_id_alphabet(self) -> None:
        for value in ("milestone/.{work_item_id}", "milestone..{work_item_id}", "-{work_item_id}",
                      "m/{work_item_id}.", "m//{work_item_id}", "m/{work_item_id} x",
                      # Invalid only for the ids that complete ".lock": "lock", "ck", "k".
                      "m/x.{work_item_id}", "m/x.lo{work_item_id}", "m/x.loc{work_item_id}"):
            with self.subTest(value=value):
                data = self.mutate(lambda d: d["milestone_branches"].__setitem__("branch_format", value))
                self.assertRefuses(data, "milestone_branches.branch_format", "not a valid branch name")

    def test_branch_format_must_not_render_the_trunk(self) -> None:
        cases = [
            ("main", "{work_item_id}"),              # renders "main"
            ("main", "ma{work_item_id}"),            # renders "main" for id "in"
            ("main", "main/{work_item_id}"),         # nested under the trunk
            ("release/main", "{work_item_id}"),      # renders "release", a component of the trunk
            ("release/main", "{work_item_id}/main"),
            ("main", "mai{work_item_id}/x"),         # "main/x" for id "n"
        ]
        for trunk, value in cases:
            with self.subTest(trunk=trunk, value=value):
                def edit(d, trunk=trunk, value=value):
                    d["trunk"]["branch"] = trunk
                    d["milestone_branches"]["branch_format"] = value
                self.assertRefuses(self.mutate(edit), "milestone_branches.branch_format", "trunk")

    def test_admissible_branch_formats(self) -> None:
        for trunk, value in (("main", "feature/{work_item_id}-wip"), ("main", "mainline/{work_item_id}"),
                             ("Trunk", "{work_item_id}"), ("main", "Main/{work_item_id}")):
            with self.subTest(trunk=trunk, value=value):
                def edit(d, trunk=trunk, value=value):
                    d["trunk"]["branch"] = trunk
                    d["milestone_branches"]["branch_format"] = value
                policy = repo_policy.parse_policy(_encode(self.mutate(edit)))
                self.assertEqual(policy.milestone_branches.branch_format, value)

    def test_trunk_must_be_a_valid_branch(self) -> None:
        self.assertRefuses(self.mutate(lambda d: d["trunk"].__setitem__("branch", "a..b")),
                           "trunk.branch", "not a valid branch name")
        self.assertRefuses(self.mutate(lambda d: d["trunk"].__setitem__("remote", "-o")),
                           "trunk.remote", "not a remote name")

    # -- tag format -------------------------------------------------------

    def test_tag_format_must_be_invertible(self) -> None:
        for value in ("release", "v{version}-{version}", "v{version}-{commit}", "{tag}"):
            with self.subTest(value=value):
                data = self.mutate(lambda d: d["release"].__setitem__("tag_format", value))
                self.assertRefuses(data, "release.tag_format", "not invertible")

    def test_tag_format_must_render_a_valid_tag(self) -> None:
        for value in ("v {version}", "v{version}.lock", "v{version}/", "v{version}~"):
            with self.subTest(value=value):
                data = self.mutate(lambda d: d["release"].__setitem__("tag_format", value))
                self.assertRefuses(data, "release.tag_format", "not a valid tag name")

    def test_another_invertible_tag_format(self) -> None:
        def edit(d):
            d["release"]["tag_format"] = "release-{version}-final"
            d["release"]["abandoned_tags"] = ["release-1.1.0-final"]
        release = repo_policy.parse_policy(_encode(self.mutate(edit))).release
        self.assertEqual(release.version_of_tag("release-2.0.1-final"), "2.0.1")
        for tag in ("release-2.0.1", "v2.0.1", "release--final", "release-2.0-final"):
            self.assertIsNone(release.version_of_tag(tag), tag)

    def test_abandoned_tag_must_render_from_the_tag_format(self) -> None:
        for value in ("1.1.0", "v1.1", "vx.y.z", "v01.1.0", ""):
            with self.subTest(value=value):
                data = self.mutate(lambda d: d["release"].__setitem__("abandoned_tags", [value]))
                field = "release.abandoned_tags[0]"
                self.assertRefuses(data, field, "does not render" if value else "non-empty string")
        data = self.mutate(lambda d: d["release"].__setitem__("abandoned_tags", ["v1.1.0", "v1.1.0"]))
        self.assertRefuses(data, "release.abandoned_tags", "more than once")

    def test_abandoned_tags_are_optional(self) -> None:
        release = repo_policy.parse_policy(_encode(self.mutate(
            lambda d: d["release"].pop("abandoned_tags")))).release
        self.assertEqual(release.abandoned_tags, ())

    # -- adapters ---------------------------------------------------------

    def test_unknown_adapter_kind_names_the_known_ones(self) -> None:
        cases = [
            ((lambda d: d["release"]["version_source"].__setitem__("kind", "gradle-properties")),
             "release.version_source.kind", r"\['pyproject'\]"),
            ((lambda d: d["release"].__setitem__("version_scheme", "calver")),
             "release.version_scheme", r"\['semver'\]"),
            ((lambda d: d["release"]["build"].__setitem__("kind", "shell")),
             "release.build.kind", r"\['command'\]"),
            ((lambda d: d["release"]["verify"].__setitem__("kind", "script")),
             "release.verify.kind", r"\['command'\]"),
            ((lambda d: d["release"]["publication"].__setitem__("kind", "gitlab_release")),
             "release.publication.kind", r"\['github_release'\]"),
            ((lambda d: d["forge"].__setitem__("kind", "gitlab")), "forge.kind", r"\['github'\]"),
            ((lambda d: d["release"].__setitem__("trigger", "on_push")),
             "release.trigger", r"\['conventional_commit', 'version_change'\]"),
        ]
        for edit, field, known in cases:
            with self.subTest(field=field):
                self.assertRefuses(self.mutate(edit), field, "unknown kind .*" + known)

    def test_dynamic_pyproject_version_refuses(self) -> None:
        release = repo_policy.parse_policy(fixtures.LEGACY_POLICY).release
        for text, pattern in (
            ('[project]\nname = "x"\ndynamic = ["version"]\n', "dynamic version"),
            ('[project]\nname = "x"\nversion = "1.0.0"\ndynamic = ["version"]\n', "dynamic version"),
            ('[project]\nname = "x"\n', "no static"),
            ('[tool.x]\nversion = "1.0.0"\n', r"no \[project\]"),
            ('[project]\nversion = "1.0"\n', "MAJOR.MINOR.PATCH"),
            ('[project\n', "not valid TOML"),
        ):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, pattern):
                    release.read_version(text)
        self.assertEqual(release.read_version('[project]\nversion = "2.3.4"\n'), "2.3.4")

    def test_semver_scheme_is_a_total_order(self) -> None:
        release = repo_policy.parse_policy(fixtures.LEGACY_POLICY).release
        versions = ["1.10.0", "1.9.9", "0.0.1", "2.0.0", "1.9.10"]
        self.assertEqual(sorted(versions, key=release.version_key),
                         ["0.0.1", "1.9.9", "1.9.10", "1.10.0", "2.0.0"])

    def test_command_env_and_argv(self) -> None:
        self.assertRefuses(self.mutate(lambda d: d["release"]["build"]["env"].__setitem__("1X", "y")),
                           "release.build.env", "environment variable name")
        self.assertRefuses(self.mutate(lambda d: d["release"]["build"].__setitem__("argv", [])),
                           "release.build.argv", "non-empty list")
        self.assertRefuses(self.mutate(lambda d: d["release"]["verify"]["argv"].__setitem__(0, 3)),
                           "release.verify.argv[0]", "non-empty string")

    # -- other values -----------------------------------------------------

    def test_forge_repository_is_owner_name(self) -> None:
        for value in ("workflow-controller", "a/b/c", "/name", "owner/", "owner/.."):
            with self.subTest(value=value):
                data = self.mutate(lambda d: d["forge"].__setitem__("repository", value))
                self.assertRefuses(data, "forge.repository", "OWNER/NAME")

    def test_only_draft_pull_requests(self) -> None:
        data = self.mutate(lambda d: d["milestone_branches"]["pull_request"].__setitem__("draft", False))
        self.assertRefuses(data, "milestone_branches.pull_request.draft", "Draft")

    def test_switches_must_be_booleans(self) -> None:
        self.assertRefuses(self.mutate(lambda d: d["release"].__setitem__("enabled", "yes")),
                           "release.enabled", "true or false")
        self.assertRefuses(self.mutate(lambda d: d["milestone_branches"].__setitem__("enabled", 1)),
                           "milestone_branches.enabled", "true or false")

    def test_artifact_paths(self) -> None:
        for value, pattern in (("/abs/x.whl", "relative path"), ("dist/../x.whl", "relative path"),
                               ("dist//x.whl", "relative path")):
            with self.subTest(value=value):
                data = self.mutate(lambda d: d["release"]["artifacts"].__setitem__("paths", [value]))
                self.assertRefuses(data, "release.artifacts.paths[0]", pattern)
        self.assertRefuses(self.mutate(lambda d: d["release"]["version_source"].__setitem__("path", "../p")),
                           "release.version_source.path", "relative path")
        for value in ("dist/SHA256SUMS", "{version}.sums", "workflow_controller-1.2.3-py3-none-any.whl"):
            with self.subTest(checksums=value):
                def edit(d, value=value):
                    d["release"]["artifacts"]["checksums"] = value
                    d["release"]["artifacts"]["paths"] = ["dist/workflow_controller-1.2.3-py3-none-any.whl"]
                self.assertRefuses(self.mutate(edit), "release.artifacts.checksums", "file name")


class SwitchesTest(unittest.TestCase):
    def test_absent_policy_means_both_switches_off(self) -> None:
        self.assertFalse(repo_policy.milestone_branches_enabled(None))
        self.assertFalse(repo_policy.release_enabled(None))

    def test_each_switch_is_independent(self) -> None:
        data = _legacy()
        data["milestone_branches"]["enabled"] = False
        policy = repo_policy.parse_policy(_encode(data))
        self.assertFalse(repo_policy.milestone_branches_enabled(policy))
        self.assertTrue(repo_policy.release_enabled(policy))
        data = _legacy()
        data["release"]["enabled"] = False
        policy = repo_policy.parse_policy(_encode(data))
        self.assertTrue(repo_policy.milestone_branches_enabled(policy))
        self.assertFalse(repo_policy.release_enabled(policy))


class CommittedTreeReaderTest(unittest.TestCase):
    """The reader reads ``HEAD``'s tree through Git and ignores the
    working tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = fixtures.build_target_git_repo(Path(self._tmp.name) / "repo")
        self.policy_file = self.root / repo_policy.POLICY_PATH

    def _commit_policy(self, raw: bytes) -> str:
        self.policy_file.parent.mkdir(exist_ok=True)
        self.policy_file.write_bytes(raw)
        return fixtures.commit_all(self.root, "policy")

    def test_absent_file_means_disabled(self) -> None:
        (self.root / "README").write_text("x\n")
        fixtures.commit_all(self.root, "init")
        self.assertIsNone(repo_policy.read_committed_policy(self.root))

    def test_an_uncommitted_policy_is_absent(self) -> None:
        (self.root / "README").write_text("x\n")
        fixtures.commit_all(self.root, "init")
        self.policy_file.parent.mkdir()
        self.policy_file.write_bytes(fixtures.LEGACY_POLICY)
        fixtures.run(["git", "add", "-A"], cwd=self.root)  # staged, still not committed
        self.assertIsNone(repo_policy.read_committed_policy(self.root))

    def test_reads_git_show_head_and_ignores_working_tree_edits(self) -> None:
        self._commit_policy(fixtures.LEGACY_POLICY)
        self.policy_file.write_text("not json", encoding="utf-8")
        with fixtures.git_call_spy() as calls:
            policy = repo_policy.read_committed_policy(self.root)
        self.assertEqual(policy.raw, fixtures.LEGACY_POLICY)
        reads = [call[3:] for call in calls if call[1] == "-C" and call[3] in ("ls-tree", "cat-file")]
        # The spy sees each ``subprocess.run`` twice: the call and its ``Popen``.
        reads = [read for index, read in enumerate(reads) if index == 0 or read != reads[index - 1]]
        self.assertEqual(reads[0], ["ls-tree", "-z", "HEAD", "--", repo_policy.POLICY_PATH])
        self.assertEqual(reads[1][:2], ["cat-file", "blob"])
        self.assertEqual(len(reads), 2)

    def test_a_committed_policy_deleted_in_the_worktree_is_still_read(self) -> None:
        self._commit_policy(fixtures.LEGACY_POLICY)
        self.policy_file.unlink()
        self.assertIsNotNone(repo_policy.read_committed_policy(self.root))

    def test_reads_the_named_revision(self) -> None:
        first = self._commit_policy(fixtures.LEGACY_POLICY)
        data = _legacy()
        data["release"]["enabled"] = False
        self._commit_policy(_encode(data))
        self.assertFalse(repo_policy.read_committed_policy(self.root).release.enabled)
        self.assertTrue(repo_policy.read_committed_policy(self.root, first).release.enabled)

    def test_an_inadmissible_committed_policy_refuses(self) -> None:
        self._commit_policy(b'{"schema_version": 2}')
        self.policy_file.write_bytes(fixtures.LEGACY_POLICY)  # a valid working copy does not help
        with self.assertRaises(InvalidRepositoryPolicyError) as caught:
            repo_policy.read_committed_policy(self.root)
        self.assertEqual(caught.exception.evidence["rev"], "HEAD")

    def test_a_non_regular_file_refuses(self) -> None:
        self.policy_file.parent.mkdir()
        os.symlink("elsewhere.json", self.policy_file)
        fixtures.commit_all(self.root, "symlink")
        with self.assertRaisesRegex(InvalidRepositoryPolicyError, "not a regular file"):
            repo_policy.read_committed_policy(self.root)

    def test_an_undecidable_read_refuses_never_absent(self) -> None:
        # Unborn HEAD and a non-repository: ``ls-tree`` exits 128. The
        # lifecycle probe (CP8) classifies an unborn HEAD itself; this
        # reader never guesses.
        with self.assertRaisesRegex(InvalidRepositoryPolicyError, "cannot list"):
            repo_policy.read_committed_policy(self.root)
        with self.assertRaisesRegex(InvalidRepositoryPolicyError, "cannot list"):
            repo_policy.read_committed_policy(Path(self._tmp.name))
        with self.assertRaisesRegex(InvalidRepositoryPolicyError, "cannot list"):
            repo_policy.read_committed_policy(self.root, "no-such-rev")

    def test_committed_version_reads_the_version_source_at_the_revision(self) -> None:
        policy = repo_policy.parse_policy(fixtures.LEGACY_POLICY)
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "3.1.4"\n')
        commit = fixtures.commit_all(self.root, "v")
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "9.9.9"\n')
        self.assertEqual(repo_policy.read_committed_version(self.root, policy), "3.1.4")
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\ndynamic = ["version"]\n')
        fixtures.commit_all(self.root, "dynamic")
        with self.assertRaisesRegex(InvalidRepositoryPolicyError, "dynamic version"):
            repo_policy.read_committed_version(self.root, policy)
        self.assertEqual(repo_policy.read_committed_version(self.root, policy, commit), "3.1.4")
        (self.root / "pyproject.toml").unlink()
        fixtures.commit_all(self.root, "gone")
        with self.assertRaisesRegex(InvalidRepositoryPolicyError, "does not exist"):
            repo_policy.read_committed_version(self.root, policy)


class LegacyPolicyMeaningTest(unittest.TestCase):
    """Every policy 1.3.0 accepts still parses, to the same values (I6)."""

    VARIANTS = {
        "reference": lambda d: None,
        "no abandoned tags": lambda d: d["release"].pop("abandoned_tags"),
        "release off": lambda d: d["release"].__setitem__("enabled", False),
        "branches off": lambda d: d["milestone_branches"].__setitem__("enabled", False),
        "green checks not required": lambda d: d["milestone_branches"]["pull_request"].__setitem__(
            "ready_requires_green_checks", False),
        "another tag format": lambda d: d["release"].update(tag_format="release-{version}", abandoned_tags=[]),
    }

    def test_every_variant_keeps_its_meaning(self) -> None:
        for name, edit in self.VARIANTS.items():
            with self.subTest(variant=name):
                data = _legacy()
                edit(data)
                policy = repo_policy.parse_policy(_encode(data))
                release, branches = data["release"], data["milestone_branches"]
                self.assertEqual(policy.release.trigger, "version_change")
                self.assertEqual(policy.release.enabled, release["enabled"])
                self.assertEqual(policy.release.version_source_kind, "pyproject")
                self.assertEqual(policy.release.version_source_path, "pyproject.toml")
                self.assertEqual(policy.release.tag_format, release["tag_format"])
                self.assertEqual(policy.release.abandoned_tags, tuple(release.get("abandoned_tags", [])))
                self.assertEqual(policy.release.change_types, {})
                self.assertEqual(policy.release.bump_overrides, {})
                self.assertEqual(policy.milestone_branches.enabled, branches["enabled"])
                self.assertEqual(policy.milestone_branches.ready_requires_green_checks,
                                 branches["pull_request"]["ready_requires_green_checks"])
                # No merge_method key: 1.3.0's merge-commit behaviour.
                self.assertEqual(policy.milestone_branches.merge_method, "merge")
                self.assertEqual(policy.release.read_version('[project]\nversion = "1.2.3"\n'), "1.2.3")

    def test_the_conventional_keys_are_refused_under_version_change(self) -> None:
        for key, value in (("change_types", {"feat": "minor"}), ("bump_overrides", {})):
            with self.subTest(key=key):
                data = _legacy()
                data["release"][key] = value
                with self.assertRaisesRegex(InvalidRepositoryPolicyError, rf"unknown key\(s\) \['{key}'\]"):
                    repo_policy.parse_policy(_encode(data))


class ConventionalTriggerTest(_RefusalCase):
    """The ``conventional_commit`` trigger: no version source, a required
    ``change_types`` table, optional ``bump_overrides``."""

    def mutate(self, edit) -> dict:
        data = _conventional()
        edit(data)
        return data

    def test_the_fixture_parses(self) -> None:
        release = repo_policy.parse_policy(fixtures.CONVENTIONAL_POLICY).release
        self.assertEqual(release.trigger, "conventional_commit")
        self.assertIsNone(release.version_source_kind)

    def test_version_source_is_refused(self) -> None:
        data = self.mutate(lambda d: d["release"].__setitem__(
            "version_source", {"kind": "pyproject", "path": "pyproject.toml"}))
        self.assertRefuses(data, "release.version_source", "the release tags are the version")

    def test_change_types_is_required(self) -> None:
        self.assertRefuses(self.mutate(lambda d: d["release"].pop("change_types")), "release",
                           r"missing required key\(s\) \['change_types'\]")
        self.assertRefuses(self.mutate(lambda d: d["release"].__setitem__("change_types", {})),
                           "release.change_types", "at least one entry")

    def test_malformed_change_types(self) -> None:
        for value, field, pattern in (
            ([["feat", "minor"]], "release.change_types", "JSON object"),
            ({"Feat": "minor"}, "release.change_types", "not a lowercase type"),
            ({"feat-x": "minor"}, "release.change_types", "not a lowercase type"),
            ({"": "minor"}, "release.change_types", "not a lowercase type"),
            ({"feat": "major"}, "release.change_types.feat", r"not one of \['minor', 'none', 'patch'\]"),
            ({"feat": "Minor"}, "release.change_types.feat", "not one of"),
            ({"feat": None}, "release.change_types.feat", "not one of"),
            ({"feat": ["minor"]}, "release.change_types.feat", "not one of"),
            ({"feat": {"bump": "minor"}}, "release.change_types.feat", "not one of"),
        ):
            with self.subTest(value=value):
                self.assertRefuses(self.mutate(lambda d: d["release"].__setitem__("change_types", value)),
                                   field, pattern)

    def test_bump_overrides(self) -> None:
        commit = "0123456789abcdef0123456789abcdef01234567"
        for bump in ("major", "minor", "patch", "none"):
            with self.subTest(bump=bump):
                data = self.mutate(lambda d: d["release"].__setitem__("bump_overrides", {commit: bump}))
                self.assertEqual(repo_policy.parse_policy(_encode(data)).release.bump_overrides, {commit: bump})
        self.assertEqual(repo_policy.parse_policy(_encode(self.mutate(
            lambda d: d["release"].__setitem__("bump_overrides", {})))).release.bump_overrides, {})
        for value, field, pattern in (
            ([commit], "release.bump_overrides", "JSON object"),
            ({commit.upper(): "patch"}, "release.bump_overrides", "not a 40-hex commit"),
            ({commit[:12]: "patch"}, "release.bump_overrides", "not a 40-hex commit"),
            ({"v1.2.3": "patch"}, "release.bump_overrides", "not a 40-hex commit"),
            ({commit: ["patch"]}, f"release.bump_overrides.{commit}", "not one of"),
            ({commit: "huge"}, f"release.bump_overrides.{commit}",
             r"not one of \['major', 'minor', 'none', 'patch'\]"),
        ):
            with self.subTest(value=value):
                self.assertRefuses(self.mutate(lambda d: d["release"].__setitem__("bump_overrides", value)),
                                   field, pattern)

    def test_version_scheme_other_than_semver(self) -> None:
        self.assertRefuses(self.mutate(lambda d: d["release"].__setitem__("version_scheme", "calver")),
                           "release.version_scheme", r"unknown kind .*\['semver'\]")

    def test_unknown_key_names_the_triggers_keys(self) -> None:
        self.assertRefuses(self.mutate(lambda d: d["release"].__setitem__("channel", "beta")), "release",
                           r"unknown key.*'bump_overrides'.*'change_types'")


class MergeMethodTest(unittest.TestCase):
    def _parse(self, data: dict):
        return repo_policy.parse_policy(_encode(data))

    def _refused(self, data: dict) -> InvalidRepositoryPolicyError:
        with self.assertRaises(InvalidRepositoryPolicyError) as caught:
            self._parse(data)
        return caught.exception

    def test_values(self) -> None:
        for method in ("merge", "squash"):
            with self.subTest(method=method):
                data = _legacy()
                data["milestone_branches"]["pull_request"]["merge_method"] = method
                self.assertEqual(self._parse(data).milestone_branches.merge_method, method)
        for method in ("rebase", "", 1, None):
            with self.subTest(method=method):
                data = _legacy()
                data["milestone_branches"]["pull_request"]["merge_method"] = method
                self.assertEqual(self._refused(data).evidence["field"],
                                 "milestone_branches.pull_request.merge_method")

    def test_the_cross_field_rule(self) -> None:
        # conventional_commit with both switches on requires squash.
        for method in (None, "merge"):
            with self.subTest(method=method):
                data = _conventional()
                pull_request = data["milestone_branches"]["pull_request"]
                if method is None:
                    pull_request.pop("merge_method")
                else:
                    pull_request["merge_method"] = method
                exc = self._refused(data)
                self.assertEqual(exc.evidence["field"], "milestone_branches.pull_request.merge_method")
                self.assertIn("only a squash commit carries the pull request title", exc.message)
                # Either switch off lifts the rule.
                for switch in ("release", "milestone_branches"):
                    relaxed = copy.deepcopy(data)
                    relaxed[switch]["enabled"] = False
                    self.assertEqual(self._parse(relaxed).milestone_branches.merge_method, method or "merge")
        # version_change never requires it.
        data = _legacy()
        data["milestone_branches"]["pull_request"]["merge_method"] = "merge"
        self.assertEqual(self._parse(data).milestone_branches.merge_method, "merge")


class ReleaseNotesPolicyTest(_RefusalCase):
    """settings-and-telemetry D.1: the optional
    ``milestone_branches.pull_request.release_notes`` and the
    ``{release_notes}`` publication placeholder."""

    FIELD = "milestone_branches.pull_request.release_notes"

    def with_notes(self, value: object) -> dict:
        data = _conventional()
        data["milestone_branches"]["pull_request"]["release_notes"] = value
        return data

    def test_absent_means_no_notes_and_the_reference_policy_is_unchanged(self) -> None:
        self.assertIsNone(repo_policy.parse_policy(_encode(_conventional())).milestone_branches.release_notes)
        reference = repo_policy.parse_policy(REFERENCE.read_bytes())
        self.assertIsNone(reference.milestone_branches.release_notes)
        self.assertFalse(reference.release.uses_release_notes)

    def test_accepted_paths_and_headings(self) -> None:
        for path in ("docs/milestones/completed/{work_item_id}.md", "NOTES.md",
                     "docs/{work_item_id}/{work_item_id}.md"):
            with self.subTest(path=path):
                parsed = repo_policy.parse_policy(_encode(self.with_notes({"path": path, "heading": "Release notes"})))
                notes = parsed.milestone_branches.release_notes
                self.assertEqual((notes.path, notes.heading), (path, "Release notes"))
                self.assertEqual(notes.path_for("c3"), path.replace("{work_item_id}", "c3"))

    def test_refused_values(self) -> None:
        cases = [
            ("not an object", f"{self.FIELD}", "must be a JSON object"),
            ({"path": "NOTES.md"}, self.FIELD, "missing required"),
            ({"path": "NOTES.md", "heading": "h", "x": 1}, self.FIELD, "unknown key"),
            ({"path": "/abs/{work_item_id}.md", "heading": "h"}, f"{self.FIELD}.path", "normalised relative path"),
            ({"path": "docs/../{work_item_id}.md", "heading": "h"}, f"{self.FIELD}.path", "normalised relative"),
            ({"path": "docs//x.md", "heading": "h"}, f"{self.FIELD}.path", "normalised relative"),
            ({"path": "docs\\x.md", "heading": "h"}, f"{self.FIELD}.path", "normalised relative"),
            ({"path": "docs/{version}.md", "heading": "h"}, f"{self.FIELD}.path", "does not admit"),
            ({"path": "docs/{nope}.md", "heading": "h"}, f"{self.FIELD}.path", "unknown placeholder"),
            ({"path": "docs/{work_item_id.md", "heading": "h"}, f"{self.FIELD}.path", "unmatched brace"),
            ({"path": "", "heading": "h"}, f"{self.FIELD}.path", "non-empty string"),
            ({"path": "NOTES.md", "heading": ""}, f"{self.FIELD}.heading", "non-empty string"),
            ({"path": "NOTES.md", "heading": "two\nlines"}, f"{self.FIELD}.heading", "single line"),
            ({"path": "NOTES.md", "heading": "   "}, f"{self.FIELD}.heading", "single line"),
        ]
        for value, field, pattern in cases:
            with self.subTest(value=value):
                self.assertRefuses(self.with_notes(value), field, pattern)

    def test_the_release_notes_placeholder_is_confined_to_the_notes_template(self) -> None:
        data = _conventional()
        data["release"]["publication"]["notes"] = "pkg {tag}\n\n{release_notes}"
        parsed = repo_policy.parse_policy(_encode(data))
        self.assertTrue(parsed.release.uses_release_notes)
        self.assertFalse(repo_policy.parse_policy(_encode(_conventional())).release.uses_release_notes)
        refused = [
            ("release.publication.title", lambda d: d["release"]["publication"].__setitem__("title", "{release_notes}")),
            ("release.build.argv[0]", lambda d: d["release"]["build"]["argv"].__setitem__(0, "{release_notes}")),
            ("milestone_branches.branch_format",
             lambda d: d["milestone_branches"].__setitem__("branch_format", "m/{work_item_id}{release_notes}")),
            (f"{self.FIELD}.path",
             lambda d: d["milestone_branches"]["pull_request"].__setitem__(
                 "release_notes", {"path": "{release_notes}.md", "heading": "h"})),
        ]
        for field, edit in refused:
            with self.subTest(field=field):
                data = _conventional()
                edit(data)
                self.assertRefuses(data, field, "does not admit")


class ReadVersionUnderConventionalCommitTest(unittest.TestCase):
    """``Release.read_version`` refuses under the new trigger, naming it.
    Its callers still call it unconditionally at CP1; CP2 and CP3 remove
    them."""

    def test_read_version_refuses_naming_the_trigger(self) -> None:
        release = repo_policy.parse_policy(fixtures.CONVENTIONAL_POLICY).release
        with self.assertRaisesRegex(ValueError, "the release trigger is 'conventional_commit'"):
            release.read_version('[project]\nversion = "1.2.3"\n')

    def test_read_committed_version_refuses_without_reading(self) -> None:
        policy = repo_policy.parse_policy(fixtures.CONVENTIONAL_POLICY)
        with tempfile.TemporaryDirectory() as td:
            root = fixtures.build_target_git_repo(Path(td) / "repo")
            (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "3.1.4"\n')
            fixtures.commit_all(root, "v")
            with self.assertRaises(InvalidRepositoryPolicyError) as caught:
                repo_policy.read_committed_version(root, policy)
        self.assertIn("'conventional_commit'", caught.exception.message)
        self.assertEqual(caught.exception.evidence["field"], "release.trigger")


if __name__ == "__main__":
    unittest.main()
