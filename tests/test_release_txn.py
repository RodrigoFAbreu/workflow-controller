"""``controller.release_txn``: release classification and the release
transaction (``workflow-controller-trunk-branch-pr-release-orchestration``
CP4), over disposable trunk histories with a bare origin and the fake
``gh``.

The adopter here is deliberately not this repository: a toy policy whose
``build`` writes ``dist/pkg-<version>.txt`` (with a random nonce, so a
rebuild is not byte-identical, as a wheel is not) and whose ``verify``
checks the artifact's first line names its version and commit. That shows
the transaction reads everything adopter-specific from the policy.

The same toy adopter under the ``conventional_commit`` trigger
(``workflow-controller-squash-merge-tag-versioning`` CP3) derives its
version from the release tags and the subjects of the trunk commits since
the highest one; its history starts under ``version_change``, as an
adopter's does.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import forge, gitrepo, release_txn, repo_policy  # noqa: E402
from controller.errors import ForgeUndecidableError, ReleaseTransactionError  # noqa: E402
from controller.release_txn import (  # noqa: E402
    ABANDONED_TAG_INCONSISTENT, ABANDONED_VERSION, ALREADY_RELEASED, BASELINE_UNRELEASED,
    COLLISION_RELEASE_WITHOUT_TAG, COLLISION_TAG_ELSEWHERE, INVALID_SUBJECT, INVALID_TRANSITION,
    NO_CHANGE, RELEASE_DUE, RELEASE_MISMATCH, RESUME,
)
from tests import fake_gh  # noqa: E402
from tests.fixtures import (  # noqa: E402
    FAKE_GH_REPOSITORY, build_origin_pair, commit_all, fake_gh_env, git_clone, run,
)

TOY_BUILD = """\
import secrets, sys
from pathlib import Path
version, commit = sys.argv[1:3]
Path("dist").mkdir(exist_ok=True)
Path("dist", f"pkg-{version}.txt").write_text(
    f"pkg {version} built from {commit}\\nnonce {secrets.token_hex(8)}\\n")
"""

TOY_VERIFY = """\
import json, os, sys
from pathlib import Path
artifact, version, commit, tag = sys.argv[1:5]
if os.environ.get("TOY_VERIFY_LOG"):
    with open(os.environ["TOY_VERIFY_LOG"], "a") as handle:
        handle.write(json.dumps({"artifact": Path(artifact).name, "commit": commit, "tag": tag}) + "\\n")
path = Path(artifact)
ok = (path.name == f"pkg-{version}.txt" and tag == f"v{version}"
      and path.read_text().splitlines()[:1] == [f"pkg {version} built from {commit}"])
sys.exit(0 if ok else 1)
"""

TAGGER = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")

#: Every argv this module ran through ``gh`` or Git, for the module-level
#: "never forced, clobbered or deleted" check.
_ARGVS: list[list[str]] = []


def tearDownModule() -> None:  # noqa: N802 -- unittest's hook name
    forbidden = {"--force", "--clobber", "delete", "-f", "-d", "--delete"}
    bad = [argv for argv in _ARGVS if forbidden & set(argv) or any(a.startswith("--force") for a in argv)]
    assert not bad, f"the release transaction ran a forbidden argv: {bad}"
    assert _ARGVS, "the module-level argv check ran over nothing"


def toy_policy(abandoned: list[str], *, enabled: bool = True) -> dict:
    return {
        "schema_version": 1,
        "trunk": {"branch": "main", "remote": "origin"},
        "forge": {"kind": "github", "repository": FAKE_GH_REPOSITORY},
        "milestone_branches": {"enabled": False, "branch_format": "milestone/{work_item_id}",
                               "pull_request": {"draft": True, "ready_requires_green_checks": True}},
        "release": {
            "enabled": enabled, "trigger": "version_change",
            "version_source": {"kind": "pyproject", "path": "pyproject.toml"},
            "version_scheme": "semver", "tag_format": "v{version}", "abandoned_tags": abandoned,
            "build": {"kind": "command", "argv": [sys.executable, "build.py", "{version}", "{commit}"]},
            "verify": {"kind": "command",
                       "argv": [sys.executable, "verify.py", "{artifact}", "{version}", "{commit}", "{tag}"]},
            "artifacts": {"paths": ["dist/pkg-{version}.txt"], "checksums": "SHA256SUMS"},
            "publication": {"kind": "github_release", "title": "{tag}", "notes": "pkg {tag}"},
        },
    }


#: The reference adopter's ``change_types`` (``tests.fixtures.CONVENTIONAL_POLICY``).
CHANGE_TYPES = {"feat": "minor", "fix": "patch", "perf": "patch", "refactor": "patch",
                "revert": "patch", "build": "patch", "style": "patch",
                "docs": "none", "chore": "none", "ci": "none", "test": "none"}


def conventional_policy(abandoned: list[str], overrides: dict[str, str] | None = None) -> dict:
    """:func:`toy_policy` under the ``conventional_commit`` trigger."""
    policy = toy_policy(abandoned)
    release = policy["release"]
    del release["version_source"]
    release.update(trigger="conventional_commit", change_types=dict(CHANGE_TYPES))
    if overrides:
        release["bump_overrides"] = dict(overrides)
    return policy


def artifact_bytes(version: str, commit: str, nonce: str = "0") -> bytes:
    return f"pkg {version} built from {commit}\nnonce {nonce}\n".encode()


def sums(assets: dict[str, bytes]) -> bytes:
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                   for name, data in sorted(assets.items())).encode()


class _Recorder:
    """A runner recording every argv (into the module log too) before
    running it for real."""

    def __init__(self, env: dict[str, str], before=None) -> None:
        self.calls: list[list[str]] = []
        self._real = gitrepo.subprocess_runner(env)
        self._before = before

    def __call__(self, argv):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        _ARGVS.append(argv)
        if self._before is not None:
            self._before(argv)
        return self._real(argv)


class _ReleaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.origin, self.clone = build_origin_pair(self.tmp)
        self.env = fake_gh_env(self.tmp, origin=self.origin)
        self.verify_log = self.tmp / "verify.jsonl"
        self.abandoned: list[str] = []
        (self.clone / "build.py").write_text(TOY_BUILD)
        (self.clone / "verify.py").write_text(TOY_VERIFY)
        (self.clone / ".gitignore").write_text("dist/\n")
        self.base = self.bump("1.0.0")

    def tearDown(self) -> None:
        _ARGVS.extend(fake_gh.invocations(Path(self.env["FAKE_GH_LOG"])))
        self._tmp.cleanup()

    # -- history -----------------------------------------------------------

    def _commit(self, message: str, *, push: bool = True) -> str:
        sha = commit_all(self.clone, message)
        if push:
            run(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], cwd=self.clone)
        return sha

    def bump(self, version: str, *, abandoned: list[str] | None = None, push: bool = True) -> str:
        """A trunk commit carrying ``version`` (and ``abandoned`` tags)."""
        if abandoned is not None:
            self.abandoned = abandoned
        (self.clone / "pyproject.toml").write_text(f'[project]\nname = "pkg"\nversion = "{version}"\n')
        policy = self.clone / repo_policy.POLICY_PATH
        policy.parent.mkdir(exist_ok=True)
        policy.write_text(json.dumps(toy_policy(self.abandoned), indent=2))
        return self._commit(f"version {version} {self.abandoned}", push=push)

    def squash(self, subject: str, *, overrides: dict[str, str] | None = None,
               abandoned: list[str] | None = None, push: bool = True) -> str:
        """A trunk commit with ``subject`` under the conventional policy (and
        ``abandoned`` tags and ``overrides``), as a squash merge lands one."""
        if abandoned is not None:
            self.abandoned = abandoned
        policy = self.clone / repo_policy.POLICY_PATH
        policy.parent.mkdir(exist_ok=True)
        policy.write_text(json.dumps(conventional_policy(self.abandoned, overrides), indent=2))
        path = self.clone / "work.txt"
        path.write_text((path.read_text() if path.exists() else "") + f"{subject}\n")
        return self._commit(subject, push=push)

    def merge(self, name: str = "change") -> str:
        """A trunk commit that changes nothing release-relevant."""
        path = self.clone / "work.txt"
        path.write_text(path.read_text() + f"{name}\n" if path.exists() else f"{name}\n")
        return self._commit(name)

    def tag(self, tag: str, commit: str) -> None:
        """Push an annotated tag, as a hand-pushed tag or an earlier run."""
        run(["git", "tag", "-a", "-m", tag, tag, commit], cwd=self.clone)
        run(["git", "push", "-q", "origin", f"refs/tags/{tag}"], cwd=self.clone)

    def seed_release(self, tag: str, assets: dict[str, bytes], *, draft: bool = False) -> None:
        state_path = Path(self.env["FAKE_GH_STATE"])
        state = fake_gh.read_state(state_path)
        directory = state_path.parent / "assets" / tag
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in assets.items():
            (directory / name).write_bytes(data)
        state["releases"].append({
            "tagName": tag, "isDraft": draft, "url": f"{state['url']}/releases/tag/{tag}",
            "assets": [{"name": name, "size": len(data), "state": "uploaded"} for name, data in assets.items()],
            "title": tag, "notes": "seeded"})
        fake_gh.write_state(state_path, state)

    def good_assets(self, version: str, commit: str, nonce: str = "0") -> dict[str, bytes]:
        artifact = {f"pkg-{version}.txt": artifact_bytes(version, commit, nonce)}
        return {**artifact, "SHA256SUMS": sums(artifact)}

    def published(self, tag: str, commit: str) -> None:
        """``tag`` at ``commit`` with a consistent published release."""
        self.tag(tag, commit)
        self.seed_release(tag, self.good_assets(tag[1:], commit))

    def release_state(self, tag: str) -> dict | None:
        state = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))
        return next((r for r in state["releases"] if r["tagName"] == tag), None)

    def release_asset(self, tag: str, name: str) -> bytes:
        return (Path(self.env["FAKE_GH_STATE"]).parent / "assets" / tag / name).read_bytes()

    # -- the transaction ---------------------------------------------------

    def ctx(self, commit: str, *, failures: dict[str, str] | None = None,
            before_git=None) -> release_txn.ReleaseContext:
        env = dict(self.env, FAKE_GH_FAIL=json.dumps(failures or {}))
        self.gh = _Recorder(env)
        self.git = _Recorder(env, before_git)
        log = str(self.verify_log)

        def commands(argv, extra, cwd):
            return release_txn.run_command(argv, {**extra, "TOY_VERIFY_LOG": log}, cwd)

        policy = repo_policy.read_committed_policy(self.clone, commit)
        return release_txn.ReleaseContext(repo_root=self.clone, policy=policy,
                                          forge=forge.GhForge(FAKE_GH_REPOSITORY, self.gh),
                                          git_runner=self.git, command_runner=commands)

    def classify(self, commit: str) -> release_txn.Classification:
        return release_txn.classify(self.ctx(commit), commit)

    def assertState(self, commit: str, state: str, *, target: str | None = None) -> release_txn.Classification:
        result = self.classify(commit)
        self.assertEqual(result.state, state, result.detail)
        if target is not None:
            self.assertEqual(result.target, target)
        return result

    def release_views(self) -> list[str]:
        return [argv[3] for argv in self.gh.calls if argv[1:3] == ["release", "view"]]

    #: The classified version :meth:`build_at` builds for (``None``: the
    #: committed one, under ``version_change``).
    build_version: str | None = None

    def build_at(self, commit: str) -> None:
        run(["git", "switch", "-q", "--detach", commit], cwd=self.clone)
        release_txn.build(self.ctx(commit), commit, self.build_version)

    def verify_commits(self) -> list[str]:
        if not self.verify_log.exists():
            return []
        return [json.loads(line)["commit"] for line in self.verify_log.read_text().splitlines()]


class ClassificationTest(_ReleaseCase):
    def test_a_version_bump_is_release_due_and_no_bump_is_no_change(self) -> None:
        state = self.assertState(self.base, RELEASE_DUE, target=self.base)
        self.assertEqual(state.outputs(), {"state": RELEASE_DUE, "version": "1.0.0", "tag": "v1.0.0",
                                           "commit": self.base})
        self.published("v1.0.0", self.base)
        later = self.merge()
        self.assertState(later, NO_CHANGE, target=self.base)
        # NO_CHANGE never downloads assets.
        self.assertFalse(any(argv[1:3] == ["release", "download"] for argv in self.gh.calls))
        bumped = self.bump("1.1.0")
        self.assertState(bumped, RELEASE_DUE, target=bumped)

    def test_the_v1_1_0_shape_is_abandoned_version_then_no_change(self) -> None:
        self.published("v1.0.0", self.base)
        orphan = self.bump("1.1.0")
        self.tag("v1.1.0", orphan)
        acknowledged = self.bump("1.1.0", abandoned=["v1.1.0"])
        state = self.assertState(acknowledged, ABANDONED_VERSION)
        self.assertTrue(state.succeeded)
        self.assertIn("v1.1.0", state.detail)
        patch = self.bump("1.1.1")
        self.assertState(patch, RELEASE_DUE)
        self.published("v1.1.1", patch)
        self.assertState(patch, ALREADY_RELEASED)
        self.assertState(self.merge(), NO_CHANGE)

    def _interrupted(self, *, draft: bool) -> None:
        self.published("v1.0.0", self.base)
        bump = self.bump("1.2.0")
        self.tag("v1.2.0", bump)
        if draft:
            self.seed_release("v1.2.0", {}, draft=True)
        self.assertState(self.merge(), RESUME, target=bump)
        # A dispatch at the trunk tip classifies the same.
        self.assertState(self.merge("another"), RESUME, target=bump)

    def test_an_interrupted_release_whose_tag_landed_resumes_at_the_tag(self) -> None:
        self._interrupted(draft=False)

    def test_an_interrupted_release_left_as_a_draft_resumes_at_the_tag(self) -> None:
        self._interrupted(draft=True)

    def test_an_abandoned_tag_with_a_release_or_without_a_tag_is_inconsistent(self) -> None:
        with_release = self.bump("1.1.0", abandoned=["v1.1.0"])
        self.tag("v1.1.0", with_release)
        self.seed_release("v1.1.0", {}, draft=True)
        self.assertState(with_release, ABANDONED_TAG_INCONSISTENT)
        untagged = self.bump("1.3.0", abandoned=["v1.3.0"])
        state = self.assertState(untagged, ABANDONED_TAG_INCONSISTENT)
        self.assertFalse(state.succeeded)

    def test_a_later_bump_cannot_skip_an_interrupted_release(self) -> None:
        self.published("v1.0.0", self.base)
        bump = self.bump("1.2.0")
        self.tag("v1.2.0", bump)
        c2 = self.bump("1.2.1")
        state = self.assertState(c2, BASELINE_UNRELEASED)
        self.assertEqual(state.unsettled, ("v1.2.0",))
        self.assertIn("v1.2.0", state.detail)

        # Only a draft: still unsettled.
        self.seed_release("v1.2.0", {}, draft=True)
        self.assertEqual(self.assertState(c2, BASELINE_UNRELEASED).unsettled, ("v1.2.0",))

    def test_acknowledging_the_interrupted_tag_makes_the_bump_release_due(self) -> None:
        self.published("v1.0.0", self.base)
        self.tag("v1.2.0", self.bump("1.2.0"))
        self.bump("1.2.1")
        acknowledged = self.bump("1.2.1", abandoned=["v1.2.0"])
        self.assertState(acknowledged, RELEASE_DUE, target=acknowledged)

    def test_restoring_the_old_version_resumes_it_and_then_the_bump_is_due(self) -> None:
        self.published("v1.0.0", self.base)
        self.tag("v1.1.0", self.bump("1.1.0"))
        self.published("v1.1.1", self.bump("1.1.1", abandoned=["v1.1.0"]))
        bump = self.bump("1.2.0")
        self.tag("v1.2.0", bump)
        self.bump("1.2.1")
        resume = self.bump("1.2.0")
        state = self.assertState(resume, RESUME, target=bump)
        # The baseline holds v1.0.0 and v1.1.1 (published) and v1.1.0
        # (acknowledged, never read), never v1.2.0 itself.
        self.assertEqual(sorted(self.release_views()), ["v1.0.0", "v1.1.1", "v1.2.0"])
        self.assertEqual(state.unsettled, ())
        self.seed_release("v1.2.0", self.good_assets("1.2.0", bump))
        self.assertState(self.bump("1.2.1"), RELEASE_DUE)

    def test_a_hand_pushed_later_tag_cannot_skip_an_unreleased_lower_tag(self) -> None:
        self.published("v1.0.0", self.base)
        self.tag("v1.2.0", self.bump("1.2.0"))
        c2 = self.bump("1.2.1")
        self.tag("v1.2.1", c2)
        self.assertEqual(self.assertState(c2, BASELINE_UNRELEASED).unsettled, ("v1.2.0",))
        self.assertEqual(self.assertState(self.merge(), BASELINE_UNRELEASED).unsettled, ("v1.2.0",))
        acknowledged = self.bump("1.2.1", abandoned=["v1.2.0"])
        self.assertState(acknowledged, RESUME, target=c2)

    def test_an_acknowledged_higher_tag_does_not_settle_a_lower_one(self) -> None:
        self.published("v1.0.0", self.base)
        self.tag("v1.1.5", self.bump("1.1.5"))
        self.tag("v1.1.6", self.bump("1.1.6"))
        c = self.bump("1.2.0")
        self.tag("v1.2.0", c)
        self.bump("1.2.0", abandoned=["v1.2.0"])
        later = self.bump("1.2.1")
        state = self.assertState(later, BASELINE_UNRELEASED)
        self.assertEqual(state.unsettled, ("v1.1.5", "v1.1.6"))
        # One release view per non-acknowledged lower ancestor tag, plus the tag's own.
        self.assertEqual(sorted(self.release_views()), ["v1.0.0", "v1.1.5", "v1.1.6", "v1.2.1"])
        self.bump("1.2.1", abandoned=["v1.2.0", "v1.1.5"])
        self.assertEqual(self.assertState(self.merge(), BASELINE_UNRELEASED).unsettled, ("v1.1.6",))
        settled = self.bump("1.2.1", abandoned=["v1.2.0", "v1.1.5", "v1.1.6"])
        self.assertState(settled, RELEASE_DUE)
        self.assertNotIn("v1.1.5", self.release_views())

    def test_a_lower_version_is_an_invalid_transition(self) -> None:
        self.published("v1.2.0", self.bump("1.2.0"))
        lower = self.bump("1.1.9")
        self.assertState(lower, INVALID_TRANSITION)

    def test_a_tag_on_a_side_branch_is_a_collision(self) -> None:
        run(["git", "switch", "-q", "-c", "side"], cwd=self.clone)
        (self.clone / "side.txt").write_text("side\n")
        side = self.bump("1.1.0", push=False)
        run(["git", "push", "-q", "origin", "side"], cwd=self.clone)
        self.tag("v1.1.0", side)
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        trunk = self.bump("1.1.0")
        self.assertState(trunk, COLLISION_TAG_ELSEWHERE)

    def test_a_release_without_a_tag_is_a_collision(self) -> None:
        self.seed_release("v1.0.0", self.good_assets("1.0.0", self.base))
        self.assertState(self.base, COLLISION_RELEASE_WITHOUT_TAG)

    def test_a_published_release_with_a_wrong_asset_is_a_mismatch(self) -> None:
        self.tag("v1.0.0", self.base)
        good = self.good_assets("1.0.0", self.base)
        wrong_commit = {"pkg-1.0.0.txt": artifact_bytes("1.0.0", "0" * 40)}
        variants = {
            "unverifiable artifact": {**wrong_commit, "SHA256SUMS": sums(wrong_commit)},
            "extra asset": {**good, "extra.txt": b"x"},
            "wrong checksum": {**good, "SHA256SUMS": sums({"pkg-1.0.0.txt": b"other"})},
            "missing checksums": {"pkg-1.0.0.txt": good["pkg-1.0.0.txt"]},
        }
        for name, assets in variants.items():
            with self.subTest(name):
                state = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))
                state["releases"] = []
                fake_gh.write_state(Path(self.env["FAKE_GH_STATE"]), state)
                self.seed_release("v1.0.0", assets)
                result = self.assertState(self.base, RELEASE_MISMATCH)
                self.assertTrue(result.problems)
        state = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))
        state["releases"] = []
        fake_gh.write_state(Path(self.env["FAKE_GH_STATE"]), state)
        self.seed_release("v1.0.0", good)
        self.assertState(self.base, ALREADY_RELEASED)
        self.assertEqual(self.verify_commits()[-1], self.base)

    def test_a_commit_off_the_trunk_or_a_disabled_policy_refuses(self) -> None:
        run(["git", "switch", "-q", "-c", "side"], cwd=self.clone)
        side = self.bump("1.1.0", push=False)
        with self.assertRaises(ReleaseTransactionError):
            self.classify(side)
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        policy = toy_policy([], enabled=False)
        (self.clone / repo_policy.POLICY_PATH).write_text(json.dumps(policy))
        disabled = self._commit("disable release")
        with self.assertRaises(ReleaseTransactionError):
            self.classify(disabled)

    def test_an_undecidable_release_read_refuses(self) -> None:
        with self.assertRaises(ForgeUndecidableError):
            release_txn.classify(self.ctx(self.base, failures={"release view": "network"}), self.base)


class TransactionTest(_ReleaseCase):
    def publish(self, commit: str, built: str, **kw) -> release_txn.PublishOutcome:
        return release_txn.publish(self.ctx(commit, **kw), commit, built, tagger=TAGGER)

    def assertPublishedAt(self, tag: str, commit: str) -> None:
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", tag), commit)
        found = self.release_state(tag)
        self.assertFalse(found["isDraft"])
        self.assertEqual(sorted(a["name"] for a in found["assets"]), sorted([f"pkg-{tag[1:]}.txt", "SHA256SUMS"]))

    def test_release_due_tags_after_validation_and_publishes(self) -> None:
        self.build_at(self.base)
        outcome = self.publish(self.base, self.base)
        self.assertEqual((outcome.state, outcome.action, outcome.tagged), (RELEASE_DUE, "created", True))
        self.assertPublishedAt("v1.0.0", self.base)
        tagger = run(["git", "for-each-ref", "--format=%(objecttype) %(taggername)",
                      "refs/tags/v1.0.0"], cwd=self.clone).stdout.strip()
        self.assertEqual(tagger, "tag github-actions[bot]")
        self.assertEqual(self.release_asset("v1.0.0", "pkg-1.0.0.txt"),
                         (self.clone / "dist" / "pkg-1.0.0.txt").read_bytes())
        self.assertState(self.base, ALREADY_RELEASED)
        # Publishing again is a verified no-op.
        self.assertEqual(self.publish(self.base, self.base).action, "already_published")

    def test_an_unbuilt_or_unverifiable_artifact_refuses_before_tagging(self) -> None:
        with self.assertRaises(ReleaseTransactionError):
            self.publish(self.base, self.base)
        self.build_at(self.base)
        (self.clone / "dist" / "pkg-1.0.0.txt").write_bytes(artifact_bytes("1.0.0", "0" * 40))
        with self.assertRaises(ReleaseTransactionError):
            self.publish(self.base, self.base)
        self.assertIsNone(gitrepo.remote_tag_commit(self.clone, "origin", "v1.0.0"))
        self.assertIsNone(gitrepo.tag_commit(self.clone, "v1.0.0"))

    def test_a_build_of_another_commit_refuses(self) -> None:
        self.build_at(self.base)
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        later = self.merge()
        with self.assertRaises(ReleaseTransactionError) as caught:
            self.publish(later, self.base)
        self.assertIn("targets", str(caught.exception))

    def test_interrupted_after_the_tag_push_resumes_at_the_tag(self) -> None:
        self.build_at(self.base)
        with self.assertRaises(ForgeUndecidableError):
            self.publish(self.base, self.base, failures={"release create": "server_error"})
        peeled = gitrepo.ls_remote(self.clone, "origin", ["refs/tags/v1.0.0"])
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        later = self.merge()
        self.assertState(later, RESUME, target=self.base)
        self.verify_log.unlink()
        self.build_at(self.base)
        outcome = self.publish(later, self.base)
        self.assertEqual((outcome.state, outcome.action, outcome.tagged), (RESUME, "created", False))
        self.assertPublishedAt("v1.0.0", self.base)
        # The policy's verify ran for the tag's own commit, never the trunk tip.
        self.assertEqual(set(self.verify_commits()), {self.base})
        # The tag object itself is unchanged: never moved or recreated.
        tag_object = run(["git", "rev-parse", "refs/tags/v1.0.0"], cwd=self.clone).stdout.strip()
        self.assertEqual(run(["git", "ls-remote", "origin", "refs/tags/v1.0.0"],
                             cwd=self.clone).stdout.split()[0], tag_object)
        self.assertEqual(gitrepo.ls_remote(self.clone, "origin", ["refs/tags/v1.0.0"]), peeled)

    def test_a_mid_upload_draft_keeps_its_artifact(self) -> None:
        self.tag("v1.0.0", self.base)
        present = artifact_bytes("1.0.0", self.base, nonce="earlier-run")
        self.seed_release("v1.0.0", {"pkg-1.0.0.txt": present}, draft=True)
        self.build_at(self.base)
        outcome = self.publish(self.base, self.base)
        self.assertEqual((outcome.action, outcome.uploaded), ("resumed_draft", ("SHA256SUMS",)))
        self.assertPublishedAt("v1.0.0", self.base)
        self.assertEqual(self.release_asset("v1.0.0", "pkg-1.0.0.txt"), present)
        self.assertEqual(self.release_asset("v1.0.0", "SHA256SUMS"), sums({"pkg-1.0.0.txt": present}))

    def test_a_draft_with_only_checksums_is_filled_or_refused(self) -> None:
        self.tag("v1.0.0", self.base)
        self.build_at(self.base)
        built = (self.clone / "dist" / "pkg-1.0.0.txt").read_bytes()
        self.seed_release("v1.0.0", {"SHA256SUMS": sums({"pkg-1.0.0.txt": b"an earlier build"})}, draft=True)
        before = self.release_state("v1.0.0")
        with self.assertRaises(ReleaseTransactionError):
            self.publish(self.base, self.base)
        self.assertEqual(self.release_state("v1.0.0"), before)

        state = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))
        state["releases"] = []
        fake_gh.write_state(Path(self.env["FAKE_GH_STATE"]), state)
        self.seed_release("v1.0.0", {"SHA256SUMS": sums({"pkg-1.0.0.txt": built})}, draft=True)
        outcome = self.publish(self.base, self.base)
        self.assertEqual(outcome.uploaded, ("pkg-1.0.0.txt",))
        self.assertPublishedAt("v1.0.0", self.base)

    def test_a_draft_with_a_foreign_or_unverifiable_asset_is_left_untouched(self) -> None:
        self.tag("v1.0.0", self.base)
        self.build_at(self.base)
        for name, assets in {"foreign": {"notes.txt": b"x"},
                             "unverifiable": {"pkg-1.0.0.txt": artifact_bytes("1.0.0", "0" * 40)}}.items():
            with self.subTest(name):
                state = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))
                state["releases"] = []
                fake_gh.write_state(Path(self.env["FAKE_GH_STATE"]), state)
                self.seed_release("v1.0.0", assets, draft=True)
                before = self.release_state("v1.0.0")
                with self.assertRaises(ReleaseTransactionError):
                    self.publish(self.base, self.base)
                self.assertEqual(self.release_state("v1.0.0"), before)
                self.assertFalse(any(argv[1:3] in (["release", "upload"], ["release", "edit"])
                                     for argv in self.gh.calls))

    def _other_clone(self) -> Path:
        other = self.tmp / "other"
        if not other.exists():
            git_clone(self.origin, other)
            run(["git", "config", "user.email", "other@example.invalid"], cwd=other)
            run(["git", "config", "user.name", "Other"], cwd=other)
        return other

    def _race_tag(self, commit: str):
        """A git ``before`` hook: another run pushes ``v1.0.0`` at
        ``commit`` just before our tag push."""
        def before(argv):
            if "push" in argv and "refs/tags/v1.0.0:refs/tags/v1.0.0" in argv:
                other = self._other_clone()
                run(["git", "fetch", "-q", "origin"], cwd=other)
                run(["git", "tag", "-a", "-m", "other run", "v1.0.0", commit], cwd=other)
                run(["git", "push", "-q", "origin", "refs/tags/v1.0.0"], cwd=other)
        return before

    def test_a_concurrent_tag_at_the_same_commit_continues(self) -> None:
        self.build_at(self.base)
        outcome = self.publish(self.base, self.base, before_git=self._race_tag(self.base))
        self.assertEqual((outcome.state, outcome.tagged), (RELEASE_DUE, False))
        self.assertPublishedAt("v1.0.0", self.base)

    def test_a_concurrent_tag_at_another_commit_fails(self) -> None:
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        earlier = self.base
        # The version is new at a later commit; another run tags the earlier one.
        later = self.merge()
        self.build_at(later)
        with self.assertRaises(ReleaseTransactionError):
            self.publish(later, later, before_git=self._race_tag(earlier))
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.0.0"), earlier)
        self.assertIsNone(self.release_state("v1.0.0"))

    def test_a_concurrent_tag_at_an_unknown_commit_fails_with_the_named_refusal(self) -> None:
        self.build_at(self.base)
        other = self._other_clone()
        run(["git", "commit", "-q", "--allow-empty", "-m", "not on the trunk"], cwd=other)
        foreign = run(["git", "rev-parse", "HEAD"], cwd=other).stdout.strip()
        with self.assertRaises(ReleaseTransactionError) as raised:
            self.publish(self.base, self.base, before_git=self._race_tag(foreign))
        self.assertIn("pushing v1.0.0 was rejected", str(raised.exception))
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.0.0"), foreign)
        self.assertIsNone(self.release_state("v1.0.0"))


class ConventionalClassificationTest(_ReleaseCase):
    """Design C: the version at ``C`` is the highest ancestor tag bumped by
    the first-parent commits since it. ``self.base`` is a legacy commit
    (``version_change``, 1.0.0), published as ``v1.0.0`` where a test says
    so."""

    def restart(self) -> None:
        """A fresh history, for one scenario per sub-test."""
        self.tearDown()
        self.setUp()

    def assertDue(self, commit: str, version: str) -> release_txn.Classification:
        state = self.assertState(commit, RELEASE_DUE, target=commit)
        self.assertEqual((state.version, state.tag), (version, f"v{version}"))
        return state

    def assertRefused(self, commit: str, *fragments: str) -> None:
        """Publishing ``commit`` refuses, naming ``fragments``, and creates
        no tag or release."""
        tags = gitrepo.ls_remote_tags(self.clone, "origin")
        releases = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))["releases"]
        with self.assertRaises(ReleaseTransactionError) as caught:
            release_txn.publish(self.ctx(commit), commit, commit, tagger=TAGGER)
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))
        self.assertEqual(gitrepo.ls_remote_tags(self.clone, "origin"), tags)
        self.assertEqual(fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))["releases"], releases)

    def drop_release(self, tag: str) -> None:
        state = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))
        state["releases"] = [r for r in state["releases"] if r["tagName"] != tag]
        fake_gh.write_state(Path(self.env["FAKE_GH_STATE"]), state)

    def test_each_type_releases_what_change_types_says(self) -> None:
        cases = [("feat: add x", RELEASE_DUE, "1.1.0"), ("fix: y", RELEASE_DUE, "1.0.1"),
                 ("perf(core): z", RELEASE_DUE, "1.0.1"), ("feat!: drop x", RELEASE_DUE, "2.0.0"),
                 ("fix(api)!: rename", RELEASE_DUE, "2.0.0"),
                 ("docs: a", NO_CHANGE, "1.0.0"), ("chore: b", NO_CHANGE, "1.0.0"),
                 ("ci: c", NO_CHANGE, "1.0.0"), ("test: d", NO_CHANGE, "1.0.0")]
        for index, (subject, expected, version) in enumerate(cases):
            with self.subTest(subject):
                if index:
                    self.restart()
                self.published("v1.0.0", self.base)
                commit = self.squash(subject)
                target = commit if expected == RELEASE_DUE else self.base
                state = self.assertState(commit, expected, target=target)
                self.assertEqual(state.version, version)

    def test_a_breaking_change_from_0_x_is_a_major(self) -> None:
        self.published("v0.4.2", self.base)
        self.assertDue(self.squash("feat!: stable"), "1.0.0")

    def test_the_highest_bump_in_the_range_wins(self) -> None:
        self.published("v1.0.0", self.base)
        self.squash("docs: guide")
        feat = self.squash("feat: thing (#12)")
        self.assertDue(feat, "1.1.0")
        self.published("v1.1.0", feat)
        # Two pushes, one run: the run the middle push skipped is covered.
        self.squash("feat!: two")
        self.assertDue(self.squash("fix: three"), "2.0.0")

    def test_an_unclassifiable_subject_is_invalid_subject_until_an_override_settles_it(self) -> None:
        for index, subject in enumerate(("Merge pull request #5 from o/feature", "feature: x",
                                         "docs!: y", "feat:no space")):
            with self.subTest(subject):
                if index:
                    self.restart()
                self.published("v1.0.0", self.base)
                bad = self.squash(subject)
                state = self.assertState(self.squash("fix: after"), INVALID_SUBJECT)
                self.assertFalse(state.succeeded)
                self.assertFalse(state.publishes)
                self.assertEqual((state.version, state.tag, state.target), ("1.0.0", "v1.0.0", self.base))
                self.assertIn(bad, state.detail)
                self.assertIn(repr(subject), state.detail)
                self.assertIn("bump_overrides", state.detail)
                self.assertEqual(len(state.problems), 1)
                # A later pull request acknowledges it.
                self.assertDue(self.squash("chore: acknowledge", overrides={bad: "none"}), "1.0.1")
        # INVALID_SUBJECT is a failure the CLI reports; nothing was tagged.
        self.assertIn(INVALID_SUBJECT, release_txn.STATES)
        self.assertNotIn(INVALID_SUBJECT, release_txn.SUCCESS_STATES)

    def test_every_unclassifiable_commit_is_named(self) -> None:
        self.published("v1.0.0", self.base)
        first, second = self.squash("oops"), self.squash("more oops")
        state = self.assertState(self.squash("feat: x"), INVALID_SUBJECT)
        self.assertEqual(len(state.problems), 2)
        self.assertTrue(state.problems[0].startswith(first) and state.problems[1].startswith(second))

    def test_an_override_never_replaces_a_decision(self) -> None:
        self.published("v1.0.0", self.base)
        legacy = self.merge("a legacy change")
        breaking = self.squash("feat!: big")
        docs = self.squash("docs: small")
        for commit, bump, subject, decided in ((breaking, "none", "feat!: big", "major by its subject"),
                                               (docs, "major", "docs: small", "none by its subject"),
                                               (legacy, "patch", "a legacy change", "legacy commit")):
            with self.subTest(subject):
                tip = self.squash("chore: override", overrides={commit: bump})
                self.assertRefused(tip, commit, repr(subject), decided, "remove the entry")
        # An override naming a commit before the base tag settled an earlier
        # range, and is ignored.
        tip = self.squash("chore: old override", overrides={self.base: "major"})
        self.assertDue(tip, "2.0.0")

    def test_legacy_commits_contribute_nothing(self) -> None:
        self.published("v1.0.0", self.base)
        # No policy at all at a commit.
        (self.clone / repo_policy.POLICY_PATH).unlink()
        self._commit("remove the policy")
        self.assertState(self.squash("docs: back"), NO_CHANGE, target=self.base)
        self.assertDue(self.squash("fix: x"), "1.0.1")

    def _merge_commit(self, name: str) -> str:
        """A ``--no-ff`` merge of a one-commit side branch into the trunk,
        under the policy the trunk already carries."""
        run(["git", "switch", "-q", "-c", name], cwd=self.clone)
        (self.clone / f"{name}.txt").write_text(f"{name}\n")
        commit_all(self.clone, f"work on {name}")
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        run(["git", "merge", "-q", "--no-ff", "-m", f"Merge pull request from o/{name}", name],
            cwd=self.clone)
        run(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], cwd=self.clone)
        return run(["git", "rev-parse", "HEAD"], cwd=self.clone).stdout.strip()

    def test_this_repositorys_history_since_v1_3_0_releases_the_minor(self) -> None:
        # v1.0.0 stands for v1.3.0: two legacy merge commits (#6, #7), then the
        # milestone's own legacy merge, then the cutover's feat squash.
        self.published("v1.0.0", self.base)
        self._merge_commit("phase0")
        self._merge_commit("roadmap")
        milestone = self._merge_commit("milestone")
        self.assertState(milestone, NO_CHANGE, target=self.base)
        self.assertDue(self.squash("feat: squash merges and tag-derived versions (#9)"), "1.1.0")

    def _interrupted(self, *, draft: bool) -> None:
        self.published("v1.0.0", self.base)
        feat = self.squash("feat: a")
        self.tag("v1.1.0", feat)
        if draft:
            self.seed_release("v1.1.0", {}, draft=True)
        fix = self.squash("fix: b")
        state = self.assertState(fix, RESUME, target=feat)
        self.assertEqual(state.version, "1.1.0")
        # Once published, the next run releases the patch since the tag.
        self.drop_release("v1.1.0")
        self.seed_release("v1.1.0", self.good_assets("1.1.0", feat))
        self.assertDue(fix, "1.1.1")

    def test_an_unsettled_base_is_resumed_before_the_bump_since_it(self) -> None:
        self._interrupted(draft=False)

    def test_a_draft_base_is_resumed_before_the_bump_since_it(self) -> None:
        self._interrupted(draft=True)

    def test_a_lower_unsettled_tag_is_baseline_unreleased_until_acknowledged(self) -> None:
        self.published("v1.0.0", self.base)
        self.tag("v1.1.0", self.squash("feat: a"))
        self.published("v1.2.0", self.squash("feat: b"))
        state = self.assertState(self.squash("fix: c"), BASELINE_UNRELEASED)
        self.assertEqual((state.version, state.unsettled), ("1.2.1", ("v1.1.0",)))
        self.assertDue(self.squash("chore: settle", abandoned=["v1.1.0"]), "1.2.1")

    def test_an_abandoned_base_is_abandoned_version_then_the_next_bump_is_due(self) -> None:
        self.published("v1.0.0", self.base)
        self.tag("v1.1.0", self.squash("feat: a"))
        state = self.assertState(self.squash("docs: acknowledge", abandoned=["v1.1.0"]), ABANDONED_VERSION)
        self.assertTrue(state.succeeded)
        self.assertDue(self.squash("fix: b"), "1.1.1")

    def test_the_collision_rows(self) -> None:
        self.published("v1.0.0", self.base)
        run(["git", "switch", "-q", "-c", "side"], cwd=self.clone)
        side = self.squash("feat: side", push=False)
        run(["git", "push", "-q", "origin", "side"], cwd=self.clone)
        self.tag("v1.1.0", side)
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        run(["git", "checkout", "-q", "main", "--", "."], cwd=self.clone)
        self.assertState(self.squash("feat: trunk"), COLLISION_TAG_ELSEWHERE)
        self.restart()
        self.published("v1.0.0", self.base)
        self.seed_release("v1.1.0", {})
        self.assertState(self.squash("feat: trunk"), COLLISION_RELEASE_WITHOUT_TAG)

    def test_a_rerun_at_the_release_commit_is_already_released_or_a_mismatch(self) -> None:
        self.published("v1.0.0", self.base)
        feat = self.squash("feat: a")
        self.tag("v1.1.0", feat)
        self.seed_release("v1.1.0", {"pkg-1.1.0.txt": b"wrong"})
        self.assertState(feat, RELEASE_MISMATCH)
        self.restart()
        self.published("v1.0.0", self.base)
        feat = self.squash("feat: a")
        self.published("v1.1.0", feat)
        self.assertState(feat, ALREADY_RELEASED, target=feat)
        self.assertState(self.squash("docs: b"), NO_CHANGE, target=feat)

    def test_without_a_base_tag_a_feat_is_0_1_0(self) -> None:
        self.assertDue(self.squash("feat: first"), "0.1.0")

    def test_without_a_base_tag_and_without_a_bump_nothing_is_released(self) -> None:
        for index, history in enumerate((["docs: a", "chore: b"], ["chore: switch to conventional"])):
            with self.subTest(history):
                if index:
                    self.restart()
                    self.merge("legacy one")
                    self.merge("legacy two")
                for subject in history:
                    tip = self.squash(subject)
                state = self.assertState(tip, NO_CHANGE, target=tip)
                self.assertTrue(state.succeeded)
                self.assertFalse(state.publishes)
                self.assertEqual((state.version, state.tag_commit, state.release), ("0.0.0", None, None))
                self.assertIn("no release tag is reachable", state.detail)
                self.assertEqual(self.release_views(), [])
                self.assertEqual(gitrepo.ls_remote_tags(self.clone, "origin"), {})
                with self.assertRaises(ReleaseTransactionError):
                    release_txn.publish(self.ctx(tip), tip, tip, tagger=TAGGER)
                self.assertEqual(gitrepo.ls_remote_tags(self.clone, "origin"), {})
                self.assertEqual(fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))["releases"], [])

    def test_an_unparsable_historical_policy_refuses_until_overridden(self) -> None:
        self.published("v1.0.0", self.base)
        (self.clone / repo_policy.POLICY_PATH).write_text("{ not json")
        broken = self._commit("feat: hand-broken policy")
        tip = self.squash("fix: repaired")
        self.assertRefused(tip, broken, "cannot be read", "bump_overrides")
        self.assertDue(self.squash("chore: settle", overrides={broken: "major"}), "2.0.0")

    def test_an_override_never_settles_a_failed_git_read_of_a_historical_policy(self) -> None:
        self.published("v1.0.0", self.base)
        (self.clone / repo_policy.POLICY_PATH).write_text("{ not json")
        broken = self._commit("feat: hand-broken policy")
        tip = self.squash("chore: settle", overrides={broken: "major"})
        self.assertDue(tip, "2.0.0")
        oid = run(["git", "rev-parse", f"{broken}:{repo_policy.POLICY_PATH}"], cwd=self.clone).stdout.strip()
        real_git = repo_policy._git
        for failing, fragment in ((["ls-tree", "-z", broken], "cannot list"), (["cat-file", "blob", oid], "cannot read")):
            def git(repo_root, args, failing=failing):
                if args[:len(failing)] == failing:
                    return subprocess.CompletedProcess(["git", *args], 128, b"", b"fatal: injected")
                return real_git(repo_root, args)
            with self.subTest(failing[0]), mock.patch.object(repo_policy, "_git", git):
                self.assertRefused(tip, broken, "cannot be read from Git", fragment, "injected")


class ConventionalTransactionTest(TransactionTest):
    """The whole transaction again under ``conventional_commit``: the toy
    history's ``v0.9.0`` at the legacy base is acknowledged in
    ``abandoned_tags`` (so no scenario's release edits unsettle it), and a
    ``feat!`` squash makes ``1.0.0`` due, so every scenario above names the
    same tag."""

    build_version = "1.0.0"

    def setUp(self) -> None:
        super().setUp()
        self.tag("v0.9.0", self.base)
        self.base = self.squash("feat!: one point oh", abandoned=["v0.9.0"])

    def merge(self, name: str = "change") -> str:
        return self.squash(f"docs: {name}")

    def test_the_build_is_of_the_classified_version(self) -> None:
        self.assertEqual(self.classify(self.base).version, "1.0.0")
        run(["git", "switch", "-q", "--detach", self.base], cwd=self.clone)
        for missing in (None, ""):
            with self.assertRaises(ReleaseTransactionError):
                release_txn.build(self.ctx(self.base), self.base, missing)
        with self.assertRaises(ReleaseTransactionError):
            release_txn.build(self.ctx(self.base), self.base, "1.0")
        # A build of another version: publish refuses before tagging.
        release_txn.build(self.ctx(self.base), self.base, "1.0.1")
        with self.assertRaises(ReleaseTransactionError):
            release_txn.verify(self.ctx(self.base), self.base, "1.0.0")
        with self.assertRaises(ReleaseTransactionError):
            self.publish(self.base, self.base)
        self.assertIsNone(gitrepo.remote_tag_commit(self.clone, "origin", "v1.0.0"))

    def test_version_change_refuses_a_version_that_is_not_the_committed_one(self) -> None:
        legacy = self.bump("1.5.0")
        run(["git", "switch", "-q", "--detach", legacy], cwd=self.clone)
        with self.assertRaises(ReleaseTransactionError):
            release_txn.build(self.ctx(legacy), legacy, "1.0.0")
        self.assertEqual([p.name for p in release_txn.build(self.ctx(legacy), legacy, "1.5.0")],
                         ["pkg-1.5.0.txt"])
        self.assertEqual([p.name for p in release_txn.verify(self.ctx(legacy), legacy, None)],
                         ["pkg-1.5.0.txt"])


# ---------------------------------------------------------------------------
# Release notes from the milestones (settings-and-telemetry CP4, D.3).
# ---------------------------------------------------------------------------

from controller import milestone_branch as mb, release_notes as rn  # noqa: E402
from tests.test_release_notes import NOTES, WID, block_bytes, github_wrap, readiness_body  # noqa: E402
from tools import release as release_tool  # noqa: E402

NOTES_TEMPLATE = "pkg {tag}\n\n{release_notes}"


class ReleaseRangeTest(_ReleaseCase):
    """``Classification.release_range``: ``(base_commit, commit)`` on every
    ``RELEASE_DUE`` result under both triggers, ``None`` otherwise."""

    def test_version_change(self) -> None:
        self.assertEqual(self.classify(self.base).release_range, (None, self.base))
        self.published("v1.0.0", self.base)
        self.assertIsNone(self.assertState(self.base, ALREADY_RELEASED).release_range)
        self.assertIsNone(self.assertState(self.merge(), NO_CHANGE).release_range)
        bumped = self.bump("1.1.0")
        self.assertEqual(self.assertState(bumped, RELEASE_DUE).release_range, (self.base, bumped))
        self.tag("v1.1.0", bumped)
        self.assertIsNone(self.assertState(self.merge("later"), RESUME).release_range)

    def test_conventional_commit(self) -> None:
        feat = self.squash("feat: no tag yet")
        self.assertEqual(self.assertState(feat, RELEASE_DUE).release_range, (None, feat))
        self.published("v0.1.0", feat)
        fix = self.squash("fix: since the tag")
        self.assertEqual(self.assertState(fix, RELEASE_DUE).release_range, (feat, fix))
        docs = self.squash("docs: nothing")
        self.assertEqual(self.assertState(docs, RELEASE_DUE).release_range, (feat, docs))
        self.tag("v0.1.1", fix)
        self.assertIsNone(self.assertState(self.squash("docs: after"), RESUME).release_range)


class ReleaseNotesTransactionTest(_ReleaseCase):
    """The publish renders ``{release_notes}`` from the notes blocks on the
    marker lines of the release range's commit messages, verified against
    their digests, or refuses before any tag or release (D.3)."""

    def setUp(self) -> None:
        super().setUp()
        self.opted = True
        self.template = NOTES_TEMPLATE
        self.published("v1.0.0", self.base)

    # -- history -----------------------------------------------------------

    def land(self, subject: str, body: str | bytes = "", *, opted: bool | None = None,
             template: str | None = None, files: dict[str, str] | None = None) -> str:
        """A trunk commit under the conventional policy (``{release_notes}``
        in its notes template when ``opted``), its message ``subject`` and
        ``body`` stored verbatim, as GitHub's squash stores one."""
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        if opted is not None:
            self.opted = opted
        if template is not None:
            self.template = template
        policy = conventional_policy([])
        if self.opted:
            policy["release"]["publication"]["notes"] = self.template
        path = self.clone / repo_policy.POLICY_PATH
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(policy, indent=2))
        work = self.clone / "work.txt"
        work.write_text((work.read_text() if work.exists() else "") + f"{subject}\n")
        for name, text in (files or {}).items():
            (self.clone / name).parent.mkdir(parents=True, exist_ok=True)
            (self.clone / name).write_text(text)
        raw = body if isinstance(body, bytes) else body.encode()
        message = subject.encode() + (b"\n\n" + raw if raw else b"") + b"\n"
        run(["git", "add", "-A"], cwd=self.clone)
        # A raw commit object: `git commit` would re-encode a non-UTF-8 message.
        tree = run(["git", "write-tree"], cwd=self.clone).stdout.strip()
        parent = run(["git", "rev-parse", "HEAD"], cwd=self.clone).stdout.strip()
        who = b"Someone <someone@example.invalid> 1700000000 +0000"
        obj = (f"tree {tree}\nparent {parent}\n".encode() + b"author " + who + b"\ncommitter " + who
               + b"\n\n" + message)
        sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=self.clone, input=obj,
                             capture_output=True, check=True).stdout.decode().strip()
        run(["git", "update-ref", "HEAD", sha], cwd=self.clone)
        run(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], cwd=self.clone)
        return sha

    def milestone(self, wid: str = WID, notes: str = NOTES, *, subject: str | None = None) -> str:
        """A milestone's squash commit, its body readiness's, wrapped as
        GitHub wraps it, with the rule and the co-author trailer."""
        body = github_wrap(readiness_body(notes, wid)).rstrip("\n")
        return self.land(subject or f"feat: {wid} (#7)",
                         f"{body}\n\n---------\n\nCo-authored-by: Someone <someone@example.invalid>")

    # -- the publish -------------------------------------------------------

    def release(self, commit: str, version: str = "1.1.0", *, built: str | None = None,
                **kw) -> release_txn.PublishOutcome:
        self.build_version = version
        self.build_at(built or commit)
        return release_txn.publish(self.ctx(commit, **kw), commit, built or commit, tagger=TAGGER)

    def tag_message(self, tag: str) -> str:
        raw = run(["git", "cat-file", "tag", f"refs/tags/{tag}"], cwd=self.clone).stdout
        return raw.partition("\n\n")[2]

    def assertIncluded(self, commit: str, text: str, version: str = "1.1.0", **kw) -> release_txn.PublishOutcome:
        outcome = self.release(commit, version, **kw)
        tag = f"v{version}"
        expected = f"pkg {tag}\n\n{text}"
        self.assertEqual(self.release_state(tag)["notes"], expected)
        self.assertEqual(self.tag_message(tag), expected + "\n")
        self.assertIn("included", outcome.notes)
        return outcome

    def gh_creates(self) -> list[list[str]]:
        return [argv for argv in fake_gh.invocations(Path(self.env["FAKE_GH_LOG"])) if argv[:2] == ["release", "create"]]

    def assertRefused(self, commit: str, *fragments: str, version: str = "1.1.0",
                      ctx: release_txn.ReleaseContext | None = None, built: str | None = None) -> str:
        """The publish refuses naming ``fragments``, with no tag created
        locally or pushed and no release created."""
        self.build_version = version
        self.build_at(built or commit)
        tags = gitrepo.ls_remote_tags(self.clone, "origin")
        local = run(["git", "tag", "-l"], cwd=self.clone).stdout
        creates = len(self.gh_creates())
        releases = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))["releases"]
        with self.assertRaises(ReleaseTransactionError) as caught:
            release_txn.publish(ctx or self.ctx(commit), commit, built or commit, tagger=TAGGER)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        self.assertEqual(gitrepo.ls_remote_tags(self.clone, "origin"), tags)
        self.assertEqual(run(["git", "tag", "-l"], cwd=self.clone).stdout, local)
        self.assertEqual(len(self.gh_creates()), creates)
        self.assertEqual(fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))["releases"], releases)
        return message

    SUPPLY = "supply the notes in a later trunk commit"
    OPT_OUT = "removes {release_notes} from release.publication.notes"

    # -- included ----------------------------------------------------------

    def test_notes_from_a_wrapped_squash_commit(self) -> None:
        m = self.milestone()
        outcome = self.assertIncluded(m, NOTES)
        self.assertIn(f"included {WID} ({m})", outcome.notes)

    def test_a_later_docs_commit_is_released_with_the_milestones_notes(self) -> None:
        self.milestone()
        self.assertIncluded(self.land("docs: release notes (#8)", "Words."), NOTES)

    def test_two_milestones_in_commit_order_under_their_ids_byte_for_byte_in_the_tag(self) -> None:
        self.milestone("b-item", "B notes.")
        self.land("docs: between")
        last = self.milestone("a-item", "A notes.\n\n### A heading\n# not a comment", subject="fix: a (#9)")
        self.assertIncluded(last, "### b-item\n\nB notes.\n\n### a-item\n\nA notes.\n\n### A heading\n# not a comment")

    def test_no_base_tag_includes_every_block_of_the_chain(self) -> None:
        self.tearDown()
        self.setUp_without_tags()
        self.milestone("one", "One.")
        last = self.milestone("two", "Two.")
        self.assertIncluded(last, "### one\n\nOne.\n\n### two\n\nTwo.", version="0.1.0")

    def setUp_without_tags(self) -> None:
        _ReleaseCase.setUp(self)
        self.opted, self.template = True, NOTES_TEMPLATE

    # -- missing, supplied -------------------------------------------------

    def test_missing_refuses_then_the_copied_block_publishes(self) -> None:
        no_block = github_wrap(mb.squash_body(WID, "plan.md", accepted="a" * 40, branch=f"milestone/{WID}"))
        for name, body in (("no section", no_block), ("body lost", "")):
            with self.subTest(name):
                if name == "body lost":
                    self.tearDown()
                    self.setUp()
                m = self.land(f"feat: {WID} (#7)", body)
                message = self.assertRefused(m, "missing", "no commit of the release range carries",
                                             self.SUPPLY, "notes-block", self.OPT_OUT)
                self.assertNotIn("rerun", message)
                docs = self.land("docs: supply the notes (#8)", rn.render_block(WID, NOTES))
                self.assertIncluded(docs, NOTES)

    def test_a_notes_block_tool_block_publishes_and_the_tool_refuses_bad_notes(self) -> None:
        m = self.land("feat: no notes (#7)")
        self.assertRefused(m, "missing")
        notes_file = self.tmp / "notes.md"
        notes_file.write_text("\nWritten by the operator.\n\n")
        block = release_tool.notes_block(WID, notes_file)
        self.assertIncluded(self.land("docs: notes", block), "Written by the operator.")
        for text in ("", "\n\n", "x" * 73):
            notes_file.write_text(text)
            with self.subTest(text=text), self.assertRaises(release_tool.Refusal):
                release_tool.notes_block(WID, notes_file)

    def test_an_empty_block_is_damaged_until_superseded(self) -> None:
        m = self.land("feat: x (#7)", block_bytes(WID, ""))
        self.assertEqual(rn.digest(""), hashlib.sha256(b"").hexdigest())
        self.assertRefused(m, "unverified", "damaged", "empty", self.SUPPLY)
        self.assertIncluded(self.land("docs: notes", block_bytes(WID, "Now.")), "Now.")

    def test_a_bad_digest_token_consumes_its_end_and_is_superseded(self) -> None:
        bad = block_bytes(WID, NOTES, digest="Z" * 64)
        m = self.land("feat: x (#7)", bad)
        message = self.assertRefused(m, "unverified", "sha256=", self.SUPPLY)
        self.assertNotIn("ends no block", message)
        self.assertIncluded(self.land("docs: notes", block_bytes(WID, NOTES)), NOTES)

    def test_a_digest_mismatch_is_superseded_and_named(self) -> None:
        edited = github_wrap(readiness_body()).replace("arrives", "arrived")
        m = self.land(f"feat: {WID} (#7)", edited)
        self.assertRefused(m, "unverified", rn.digest(NOTES), rn.digest(NOTES.replace("arrives", "arrived")),
                           m, WID, self.SUPPLY)
        docs = self.land("docs: notes", block_bytes(WID, NOTES))
        outcome = self.assertIncluded(docs, NOTES)
        self.assertIn(f"superseded {WID} ({m})", outcome.notes)

    def test_two_items_in_one_commit_and_two_blocks_of_one_item(self) -> None:
        both = self.land("feat: two (#7)", block_bytes("a-item", "A.") + "\n\n" + block_bytes("b-item", "B."))
        self.assertIncluded(both, "### a-item\n\nA.\n\n### b-item\n\nB.")
        twice = self.land("fix: twice (#8)", block_bytes(WID, "One.") + "\n\n" + block_bytes(WID, "Two."))
        self.assertRefused(twice, "more than one block", version="1.1.1")
        self.assertIncluded(self.land("docs: notes", block_bytes(WID, "Settled.")), "Settled.", version="1.1.1")

    # -- refusals ----------------------------------------------------------

    def test_a_failing_commit_read_refuses_naming_the_command_and_the_rerun(self) -> None:
        m = self.milestone()
        ctx = self.ctx(m)
        real = ctx.git_runner

        def failing(argv):
            if "cat-file" in argv and "commit" in argv:
                return subprocess.CompletedProcess(argv, 128, b"", b"fatal: injected\n")
            return real(argv)

        message = self.assertRefused(m, "unreadable", "cat-file commit", "exit 128", "injected", "rerun",
                                     ctx=dataclasses.replace(ctx, git_runner=failing))
        self.assertNotIn(self.SUPPLY, message)

    def test_malformed_markers(self) -> None:
        cases = {
            "slash id": (block_bytes("a/b", NOTES), False),
            "dotdot id": (block_bytes("..", NOTES), False),
            "orphan end": (rn.END_MARKER, False),
            "start with no end": (rn.start_marker(WID, NOTES) + "\n" + NOTES, True),
            "bad sha256": (block_bytes(WID, NOTES, digest="abc"), True),
        }
        for name, (body, supersedable) in cases.items():
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                m = self.land("feat: x (#7)", body)
                message = self.assertRefused(m, "unverified")
                if supersedable:
                    self.assertIn(self.SUPPLY, message)
                    self.assertIncluded(self.land("docs: notes", block_bytes(WID, NOTES)), NOTES)
                else:
                    self.assertIn("names no work item", message)
                    self.assertIn("no supplied block can clear this", message)
                    self.assertIn(self.OPT_OUT, message)
                    self.assertNotIn(self.SUPPLY, message)
                    # A later valid block does not clear it.
                    self.assertRefused(self.land("docs: notes", block_bytes(WID, NOTES)), "names no work item")

    def test_anchoring_and_encodings(self) -> None:
        self.milestone()
        quoted = f"The block starts `{rn.start_marker(WID, NOTES)}` and ends `{rn.END_MARKER}`."
        self.land("docs: describe the feature (#8)", quoted)
        latin = self.land("docs: latin-1 (#9)", "caf\xe9 bytes, no marker".encode("latin-1"))
        self.assertIn(b"caf\xe9", gitrepo.commit_message(self.clone, latin))
        last = self.land("docs: last")
        self.assertIncluded(last, NOTES)

    def test_a_marker_line_in_a_non_utf8_message_refuses_naming_the_opt_out(self) -> None:
        m = self.land("feat: x (#7)", block_bytes(WID, NOTES).encode() + b"\n\xff")
        message = self.assertRefused(m, "unreadable", "not valid UTF-8", self.OPT_OUT)
        self.assertNotIn(self.SUPPLY, message)

    def test_opting_out_after_a_refusal_releases_the_fixed_text(self) -> None:
        m = self.land("feat: no notes (#7)")
        self.assertRefused(m, "missing")
        self.template = "pkg {tag}"
        out = self.land("chore: opt out of release notes for this release")
        outcome = self.release(out)
        self.assertEqual(self.release_state("v1.1.0")["notes"], "pkg v1.1.0")
        self.assertEqual(self.tag_message("v1.1.0"), "pkg v1.1.0\n")
        self.assertEqual(outcome.notes, "")

    def test_the_publish_reads_no_tree(self) -> None:
        narrative = f"docs/milestones/completed/{WID}.md"
        self.milestone()
        last = self.land("docs: edit the narrative after readiness",
                         files={narrative: "## Release notes\n\nSomething else entirely.\n"})
        reads = []
        real = repo_policy._git

        def spy(root, args):
            reads.append(list(args))
            return real(root, args)

        with mock.patch.object(gitrepo, "show", side_effect=AssertionError("a tree was read")), \
                mock.patch.object(repo_policy, "_git", spy):
            self.assertIncluded(last, NOTES)
        argvs = self.git.calls + reads
        self.assertFalse([argv for argv in argvs if any(narrative in str(a) for a in argv)])
        self.assertFalse([argv for argv in self.git.calls if "show" in argv or "ls-tree" in argv])

    # -- RESUME and drafts -------------------------------------------------

    def test_a_draft_is_resumed_with_its_own_notes(self) -> None:
        m = self.milestone()
        self.tag("v1.1.0", m)
        self.seed_release("v1.1.0", {}, draft=True)
        outcome = self.release(self.land("docs: later"), built=m)
        self.assertEqual((outcome.state, outcome.action), (RESUME, "resumed_draft"))
        self.assertEqual(self.release_state("v1.1.0")["notes"], "seeded")

    def test_a_pushed_tag_with_no_release_reuses_the_tags_message(self) -> None:
        m = self.milestone()
        with self.assertRaises(ForgeUndecidableError):
            self.release(m, failures={"release create": "server_error"})
        self.assertIsNone(self.release_state("v1.1.0"))
        expected = f"pkg v1.1.0\n\n{NOTES}"
        self.assertEqual(self.tag_message("v1.1.0"), expected + "\n")
        for name, commit in (("same commit", m), ("a later docs commit", None)):
            with self.subTest(name):
                commit = commit or self.land("docs: after the tag")
                outcome = self.release(commit, built=m)
                self.assertEqual((outcome.state, outcome.action), (RESUME, "created"))
                self.assertIn("reused from tag v1.1.0", outcome.notes)
                self.assertEqual(self.release_state("v1.1.0")["notes"], expected)
                state = fake_gh.read_state(Path(self.env["FAKE_GH_STATE"]))
                state["releases"] = [r for r in state["releases"] if r["tagName"] != "v1.1.0"]
                fake_gh.write_state(Path(self.env["FAKE_GH_STATE"]), state)

    def test_a_tag_that_predates_the_opt_in_is_an_unverified_tag(self) -> None:
        self.milestone()
        self.opted = False
        unopted = self.land("fix: before the opt-in (#8)")
        with self.assertRaises(ForgeUndecidableError):
            self.release(unopted, failures={"release create": "server_error"})
        self.assertEqual(self.tag_message("v1.1.0"), "pkg v1.1.0\n")
        creates = len(self.gh_creates())
        later = self.land("chore: opt in", opted=True)
        message = self.assertRefused(later, "unverified tag", "v1.1.0", "create the release for v1.1.0 by hand",
                                     built=unopted)
        self.assertIn("first difference", message)
        self.assertEqual(len(self.gh_creates()), creates)

    def test_a_tag_under_another_opted_in_template_or_with_no_block_refuses(self) -> None:
        m = self.milestone()
        run(["git", "tag", "-a", "--cleanup=verbatim", "-m", f"other v1.1.0\n\n{NOTES}\n", "v1.1.0", m],
            cwd=self.clone)
        run(["git", "push", "-q", "origin", "refs/tags/v1.1.0"], cwd=self.clone)
        self.assertRefused(self.land("docs: later"), "unverified tag", built=m)
        self.tearDown()
        self.setUp()
        bare = self.land("feat: no block (#7)")
        self.tag("v1.1.0", bare)
        self.assertRefused(self.land("docs: later"), "missing", self.SUPPLY, built=bare)

    def test_a_lightweight_tag_or_a_failing_tag_read_refuses_before_create_release(self) -> None:
        m = self.milestone()
        run(["git", "tag", "v1.1.0", m], cwd=self.clone)
        run(["git", "push", "-q", "origin", "refs/tags/v1.1.0"], cwd=self.clone)
        later = self.land("docs: later")
        self.assertRefused(later, "not an annotated tag", "by hand", built=m)
        run(["git", "tag", "-d", "v1.1.0"], cwd=self.clone)
        run(["git", "push", "-q", "origin", ":refs/tags/v1.1.0"], cwd=self.clone)
        self.tag("v1.1.0", m)
        ctx = self.ctx(later)
        real = ctx.git_runner

        def failing(argv):
            if "cat-file" in argv and "tag" in argv:
                return subprocess.CompletedProcess(argv, 128, b"", b"fatal: injected\n")
            return real(argv)

        self.assertRefused(later, "unreadable", "injected", "rerun", built=m,
                           ctx=dataclasses.replace(ctx, git_runner=failing))

    # -- a lost tag-push race ----------------------------------------------

    def race(self, commit: str, *tag_args: str):
        """A git ``before`` hook: another run pushes ``v1.1.0`` at
        ``commit``, created with ``git tag <tag_args>``, just before ours."""
        def before(argv):
            if "push" in argv and "refs/tags/v1.1.0:refs/tags/v1.1.0" in argv:
                other = self.tmp / "other"
                if not other.exists():
                    git_clone(self.origin, other)
                    run(["git", "config", "user.email", "other@example.invalid"], cwd=other)
                    run(["git", "config", "user.name", "Other"], cwd=other)
                run(["git", "fetch", "-q", "origin"], cwd=other)
                run(["git", "tag", *tag_args, "v1.1.0", commit], cwd=other)
                run(["git", "push", "-q", "origin", "refs/tags/v1.1.0"], cwd=other)
        return before

    def test_a_concurrent_tag_with_the_right_notes_is_released_with_its_message(self) -> None:
        m = self.milestone()
        expected = f"pkg v1.1.0\n\n{NOTES}"
        outcome = self.release(m, before_git=self.race(m, "-a", "--cleanup=verbatim", "-m", expected + "\n"))
        self.assertEqual((outcome.state, outcome.tagged, outcome.action), (RELEASE_DUE, False, "created"))
        self.assertIn("reused from tag v1.1.0", outcome.notes)
        self.assertEqual(self.release_state("v1.1.0")["notes"], expected)

    def test_a_concurrent_tag_with_other_notes_or_lightweight_refuses_before_create_release(self) -> None:
        for name, tag_args, fragment in (
                ("other notes", ("-a", "--cleanup=verbatim", "-m", f"other v1.1.0\n\n{NOTES}\n"), "unverified tag"),
                ("lightweight", (), "not an annotated tag")):
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                m = self.milestone()
                creates = len(self.gh_creates())
                with self.assertRaises(ReleaseTransactionError) as caught:
                    self.release(m, before_git=self.race(m, *tag_args))
                self.assertIn(fragment, str(caught.exception))
                self.assertIn("create the release for v1.1.0 by hand", str(caught.exception))
                self.assertEqual(len(self.gh_creates()), creates)
                self.assertIsNone(self.release_state("v1.1.0"))
                # Our own tag stays local; the read wrote no ref.
                self.assertEqual(self.tag_message("v1.1.0"), f"pkg v1.1.0\n\n{NOTES}\n")


if __name__ == "__main__":
    unittest.main()
