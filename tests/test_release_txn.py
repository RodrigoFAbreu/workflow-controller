"""``controller.release_txn``: release classification and the release
transaction (``workflow-controller-trunk-branch-pr-release-orchestration``
CP4), over disposable trunk histories with a bare origin and the fake
``gh``.

The adopter here is deliberately not this repository: a toy policy whose
``build`` writes ``dist/pkg-<version>.txt`` (with a random nonce, so a
rebuild is not byte-identical, as a wheel is not) and whose ``verify``
checks the artifact's first line names its version and commit. That shows
the transaction reads everything adopter-specific from the policy.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import forge, gitrepo, release_txn, repo_policy  # noqa: E402
from controller.errors import ForgeUndecidableError, ReleaseTransactionError  # noqa: E402
from controller.release_txn import (  # noqa: E402
    ABANDONED_TAG_INCONSISTENT, ABANDONED_VERSION, ALREADY_RELEASED, BASELINE_UNRELEASED,
    COLLISION_RELEASE_WITHOUT_TAG, COLLISION_TAG_ELSEWHERE, INVALID_TRANSITION, NO_CHANGE,
    RELEASE_DUE, RELEASE_MISMATCH, RESUME,
)
from tests import fake_gh  # noqa: E402
from tests.fixtures import FAKE_GH_REPOSITORY, build_origin_pair, commit_all, fake_gh_env, run  # noqa: E402

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

    def build_at(self, commit: str) -> None:
        run(["git", "switch", "-q", "--detach", commit], cwd=self.clone)
        release_txn.build(self.ctx(commit), commit)

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
            run(["git", "clone", "-q", str(self.origin), str(other)])
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


if __name__ == "__main__":
    unittest.main()
