"""The functional tester's sandbox and run (``workflow-controller-automated-functional-review``
CP3, D2, D3, D5, D10) against ``tests/fake_functional_tester.py``.

The fake emulates the CLI's sandbox from the settings file it is launched with,
so these tests show the Controller hands the CLI the right boundary and that
each detection layer works with the enforcement switched off
(``emulate_sandbox`` false). The enforcement itself is proved on the real CLI
in ``tests/test_functional_enforcement.py``. No test reaches a model, a network
or GitHub.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import functional_review as fr  # noqa: E402
from controller import review  # noqa: E402
from controller.errors import HarnessRoleError  # noqa: E402
from tests import fake_functional_tester as fake  # noqa: E402

MODEL = "claude-sonnet-5-5"
CHECKLIST = """# Milestone

## Functional review checklist

| # | Do | Expect |
| --- | --- | --- |
| A | run the app | it prints hello |
| B | read the data | it holds one line |

## Notes
"""
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent"}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout.strip()


def make_repo(root: Path, files: dict[str, str] | None = None, *, origin: Path | None = None) -> str:
    """A repository with a checklist, a source file, a data file, a tracked
    symlink, an executable and a ``.gitignore`` that ignores ``build/``."""
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    tree = {"docs/ACTIVE_MILESTONE.md": CHECKLIST, "src/app.py": "print('hello')\n", "src/data.txt": "one\n",
            ".gitignore": "build/\n*.pyc\n__pycache__/\n", "tool.sh": "#!/bin/sh\necho tool\n", **(files or {})}
    for name, text in tree.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / "tool.sh").chmod(0o755)
    os.symlink("src", root / "link")
    if origin is not None:
        git(origin.parent, "init", "-q", "--bare", str(origin))
        git(root, "remote", "add", "origin", str(origin))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return git(root, "rev-parse", "HEAD")


def results(entries: list[dict] | None = None, **extra) -> dict:
    entries = entries if entries is not None else [
        {"flow_id": "flow-1", "status": "passed", "summary": "ok", "log": "flow-1.log"},
        {"flow_id": "flow-2", "status": "passed", "summary": "ok", "log": "flow-2.log"}]
    return {"op": "results", "entries": entries, "logs": {"flow-1.log": "log one\n", "flow-2.log": "log two\n"}, **extra}


class Fixture(unittest.TestCase):
    """A real repository, a runtime root beside it, an operator home with a credential file and a tools
    directory for the fake ``claude``."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wfr-cp3-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        self.origin = self.tmp / "origin.git"
        self.repo = self.tmp / "repo"
        self.head = make_repo(self.repo, origin=self.origin)
        git(self.repo, "push", "-q", "origin", "main")
        self.runtime = self.tmp / "rt"
        self.runtime.mkdir()
        self.home = self.tmp / "operator"
        self.home.mkdir()
        (self.home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "t"}}))
        self.tools = self.tmp / "tools"
        self.candidate = fr.capture_candidate(self.repo, self.head, branch="main")
        self.flows = fr.candidate_flows(self.candidate)
        self.count = 0

    def launch(self, scenario: dict, *, timeout: float = 60, job_id: str | None = None, item: str = "item-1",
               probe=lambda: None, candidate: fr.Candidate | None = None) -> dict:
        self.count += 1
        job = job_id or f"job-{self.count}"
        scenario = {"log": str(self.tmp / f"{job}.log"), **scenario}
        wrapper = fake.write_binary(self.tools, scenario, name=f"claude{self.count}")
        self.log_path = self.tmp / f"{job}.log"
        return review.launch_functional(
            candidate=candidate or self.candidate, flows=self.flows, runtime_root=self.runtime, job_id=job,
            work_item_id=item, operator_home=self.home, model=MODEL, effort="medium", timeout=timeout,
            binary_name=str(wrapper), probe=probe, inherited_env={})

    def settle(self, result: dict) -> None:
        review.settle_functional(result)

    def actions(self) -> list[dict]:
        return fake.read_log(self.log_path)[0]["actions"]

    def snapshot(self) -> dict:
        return review.snapshot_repository(self.repo, exclude=[])


class CandidateTest(Fixture):
    def test_the_manifest_holds_every_tracked_path_with_its_mode_size_and_digest(self) -> None:
        manifest = self.candidate.manifest
        self.assertEqual(sorted(manifest), [".gitignore", "docs/ACTIVE_MILESTONE.md", "link", "src/app.py",
                                            "src/data.txt", "tool.sh"])
        self.assertEqual(manifest["tool.sh"].mode, "100755")
        self.assertEqual(manifest["link"].target, "src")
        self.assertEqual(manifest["src/app.py"].size, len("print('hello')\n"))
        self.assertEqual(manifest["src/app.py"].digest, fr.sha256_bytes(b"print('hello')\n"))

    def test_the_checklist_blob_is_the_real_repositorys(self) -> None:
        self.assertEqual(self.candidate.checklist_blob, git(self.repo, "rev-parse", f"{self.head}:docs/ACTIVE_MILESTONE.md"))

    def test_the_flows_are_numbered_from_the_real_blob(self) -> None:
        self.assertEqual([(f.flow_id, f.label) for f in self.flows], [("flow-1", "A"), ("flow-2", "B")])

    def test_a_submodule_is_not_supported(self) -> None:
        sub = self.tmp / "sub"
        make_repo(sub)
        git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor")
        git(self.repo, "commit", "-q", "-m", "submodule")
        with self.assertRaises(fr.TesterUnavailable) as caught:
            fr.capture_candidate(self.repo, git(self.repo, "rev-parse", "HEAD"))
        self.assertIn("submodule", str(caught.exception))


class SandboxBuildTest(Fixture):
    def build(self, candidate: fr.Candidate | None = None) -> fr.Sandbox:
        self.count += 1
        return fr.build_sandbox(candidate or self.candidate, runtime_root=self.runtime, job_id=f"b{self.count}",
                                work_item_id="item-1")

    def test_the_clone_shares_no_object_file_with_the_real_repository(self) -> None:
        box = self.build()
        real = {p.stat().st_ino for p in (self.repo / ".git" / "objects").rglob("*") if p.is_file()}
        mine = {p.stat().st_ino for p in (box.clone / ".git" / "objects").rglob("*") if p.is_file()}
        self.assertFalse(real & mine)

    def test_origin_is_a_scratch_bare_repository_holding_the_branch(self) -> None:
        box = self.build()
        self.assertEqual(git(box.clone, "remote", "get-url", "origin"), str(box.origin))
        self.assertEqual(git(box.origin, "rev-parse", "main"), self.head)
        self.assertEqual(git(box.clone, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(box.clone, "rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_a_fresh_clone_is_bound_and_the_writable_locations_exist_empty(self) -> None:
        box = self.build()
        self.assertEqual(box.writable, ("build", "__pycache__"))
        self.assertTrue((box.clone / "__pycache__").is_dir())
        self.assertEqual(sorted(p.pattern for p in box.refused), ["*.pyc"])
        self.assertEqual(fr.check_bound(box), [])

    def test_the_launchers_are_scripts_the_controller_wrote_with_mode_0755(self) -> None:
        box = self.build()
        for name in fr.FAKE_PROGRAMS:
            path = box.bin_dir / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
            text = path.read_text()
            self.assertTrue(text.startswith("#!/bin/sh\n"))
            self.assertIn(f"-P -m controller.sandbox_fakes.{name}", text)
            self.assertIn(sys.executable, text)

    def test_each_fake_logs_its_argv_and_exits_non_zero(self) -> None:
        box = self.build()
        for name in fr.FAKE_PROGRAMS:
            done = subprocess.run([str(box.bin_dir / name), "pr", "list"], cwd=box.clone, capture_output=True,
                                  env={"PATH": "/usr/bin:/bin"})
            self.assertNotEqual(done.returncode, 0)
            self.assertIn(b"stand-in", done.stderr)
        calls = fr.read_fake_calls(box.fake_logs)
        self.assertEqual(sorted(calls), sorted(fr.FAKE_PROGRAMS))
        self.assertEqual(calls["gh"][0]["argv"], ["pr", "list"])

    def test_the_launcher_runs_the_package_not_the_clone_the_tester_stands_in(self) -> None:
        decoy = {"controller/__init__.py": "raise SystemExit('the clone was imported')\n",
                 "controller/sandbox_fakes/__init__.py": "", "controller/sandbox_fakes/gh.py": "raise SystemExit(0)\n"}
        repo = self.tmp / "repo2"
        head = make_repo(repo, decoy)
        box = fr.build_sandbox(fr.capture_candidate(repo, head), runtime_root=self.runtime, job_id="decoy",
                               work_item_id="item-1")
        done = subprocess.run([str(box.bin_dir / "gh")], cwd=box.clone, capture_output=True, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(done.returncode, 127, done.stderr)

    def test_the_environment_has_the_sandbox_path_and_the_git_restrictions(self) -> None:
        env = self.build().environment()
        self.assertTrue(env["PATH"].endswith(":" + fr.SYSTEM_PATH))
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "file")
        self.assertEqual(env["GIT_SSH_COMMAND"], "false")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        for key in ("HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            self.assertIn("/functional/", env[key])

    def test_a_crlf_eol_rule_ends_at_unavailable_before_launch(self) -> None:
        repo = self.tmp / "crlf"
        head = make_repo(repo, {".gitattributes": "*.txt text eol=crlf\n"})
        with self.assertRaises(fr.TesterUnavailable) as caught:
            fr.build_sandbox(fr.capture_candidate(repo, head), runtime_root=self.runtime, job_id="crlf",
                             work_item_id="item-1")
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_CLONE_MISMATCH)
        self.assertIn("src/data.txt", str(caught.exception))
        self.assertFalse((self.runtime / "functional" / "crlf").exists())

    def test_core_autocrlf_ends_at_unavailable_before_launch(self) -> None:
        real = fr._plain

        def convert(cwd: Path, *args: str) -> None:
            return real(cwd, *(("-c", "core.autocrlf=true", *args) if args[0] == "checkout" else args))

        with mock.patch.object(fr, "_plain", convert):
            repo = self.tmp / "autocrlf"
            head = make_repo(repo, {"win.txt": "a\r\nb\r\n"})
            with self.assertRaises(fr.TesterUnavailable) as caught:
                fr.build_sandbox(fr.capture_candidate(repo, head), runtime_root=self.runtime, job_id="auto",
                                 work_item_id="item-1")
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_CLONE_MISMATCH)


class BindingTest(Fixture):
    """The second layer alone: a hand-made clone that differs after a run."""

    def setUp(self) -> None:
        super().setUp()
        self.box = fr.build_sandbox(self.candidate, runtime_root=self.runtime, job_id="bound", work_item_id="item-1")
        self.clone = self.box.clone

    def test_each_binding_case_is_refused(self) -> None:
        cases = {
            "an edited tracked file": lambda: (self.clone / "src/app.py").write_text("print('edited')\n"),
            "an untracked non-ignored file": lambda: (self.clone / "new.py").write_text("x\n"),
            "altered checklist bytes": lambda: (self.clone / "docs/ACTIVE_MILESTONE.md").write_text(CHECKLIST + " "),
            "a deleted tracked file": lambda: (self.clone / "src/data.txt").unlink(),
            "a changed mode": lambda: (self.clone / "tool.sh").chmod(0o644),
            "a replaced symlink": lambda: (os.unlink(self.clone / "link"), os.symlink("docs", self.clone / "link")),
        }
        for name, change in cases.items():
            with self.subTest(name):
                box = fr.build_sandbox(self.candidate, runtime_root=self.runtime, job_id=f"case-{name.replace(' ', '-')}",
                                       work_item_id="item-1")
                self.clone = box.clone
                change()
                self.assertTrue(fr.check_bound(box), name)

    def test_a_moved_head_is_refused(self) -> None:
        git(self.clone, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "moved")
        problems = fr.check_bound(self.box)
        self.assertTrue(any("HEAD" in p or "refs/heads" in p for p in problems), problems)

    def test_an_ignored_build_output_file_and_a_writable_location_are_allowed(self) -> None:
        (self.clone / "build" / "out.o").write_text("o")
        (self.clone / "__pycache__" / "m.cpython-312.pyc").write_text("c")
        (self.clone / "src" / "x.pyc").write_text("c")
        self.assertEqual(fr.check_bound(self.box), [])

    def test_a_tester_that_only_refreshes_the_index_and_writes_logs_still_passes(self) -> None:
        git(self.clone, "status", "--porcelain")
        git(self.clone, "update-index", "--refresh")
        (self.clone / ".git" / "FETCH_HEAD").write_text("fetch\n")
        (self.clone / ".git" / "logs" / "extra").write_text("log\n")
        subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=self.clone, env=GIT_ENV, input=b"new blob\n",
                       check=True, capture_output=True)
        self.assertEqual(fr.check_bound(self.box), [])

    def test_git_cases_a_config_that_would_run_code_is_itself_the_failure(self) -> None:
        marker = self.tmp / "ran"
        for key, value in (("diff", f"external = touch {marker}"), ("core", f"fsmonitor = touch {marker}"),
                           ("filter \"x\"", f"clean = touch {marker}"), ("alias", f"st = !touch {marker}")):
            with open(self.clone / ".git" / "config", "a") as fh:
                fh.write(f"[{key}]\n\t{value}\n")
        problems = fr.check_bound(self.box)
        self.assertIn(".git/config: changed", problems)
        self.assertFalse(marker.exists(), "the Controller ran a command the clone's configuration names")

    def test_git_cases_a_hook_or_info_attributes_is_the_failure(self) -> None:
        (self.clone / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\n")
        (self.clone / ".git" / "info").mkdir(exist_ok=True)
        (self.clone / ".git" / "info" / "attributes").write_text("* diff=x\n")
        problems = fr.check_bound(self.box)
        self.assertIn(".git/hooks/post-checkout: added", problems)
        self.assertIn(".git/info/attributes: added", problems)

    def test_git_cases_skip_worktree_and_assume_unchanged_do_not_hide_an_edit(self) -> None:
        for flag in ("--skip-worktree", "--assume-unchanged"):
            with self.subTest(flag):
                box = fr.build_sandbox(self.candidate, runtime_root=self.runtime, job_id=f"flag{flag[2:5]}",
                                       work_item_id="item-1")
                (box.clone / "src/app.py").write_text("print('hidden edit')\n")
                git(box.clone, "update-index", flag, "src/app.py")
                self.assertIn("differs: src/app.py", fr.check_bound(box))

    def test_git_cases_info_exclude_and_an_untracked_gitignore_do_not_hide_a_file(self) -> None:
        (self.clone / ".git" / "info").mkdir(exist_ok=True)
        (self.clone / ".git" / "info" / "exclude").write_text("hidden.py\n")
        (self.clone / "hidden.py").write_text("x\n")
        (self.clone / "sub").mkdir()
        (self.clone / "sub" / ".gitignore").write_text("*\n")
        (self.clone / "sub" / "tool.py").write_text("x\n")
        problems = fr.check_bound(self.box)
        self.assertIn("extra file: hidden.py", problems)
        self.assertIn("unlisted .gitignore: sub/.gitignore", problems)
        self.assertIn(".git/info/exclude: changed", problems)

    def test_git_cases_a_rewritten_loose_object_is_the_failure(self) -> None:
        loose = next(p for p in (self.clone / ".git" / "objects").glob("??/*"))
        loose.chmod(0o644)
        loose.write_bytes(b"rewritten")
        self.assertTrue(any(p.startswith(".git/objects/") and p.endswith("changed") for p in fr.check_bound(self.box)))

    def test_the_walk_never_follows_a_symlink_loop_or_a_link_to_a_directory_and_refuses_a_fifo(self) -> None:
        os.symlink(".", self.clone / "loop")
        os.symlink("/usr", self.clone / "to-dir")
        os.symlink("loop2", self.clone / "loop2")
        os.mkfifo(self.clone / "pipe")
        done = []
        thread = threading.Thread(target=lambda: done.append(fr.check_bound(self.box)))
        thread.start()
        thread.join(30)
        self.assertFalse(thread.is_alive(), "the walk hung")
        problems = done[0]
        for name in ("loop", "to-dir", "loop2"):
            self.assertIn(f"extra file: {name}", problems)
        self.assertIn("special file: pipe", problems)

    def test_a_fifo_under_git_is_a_failure_too(self) -> None:
        os.mkfifo(self.clone / ".git" / "pipe")
        self.assertTrue(any("pipe" in p for p in fr.check_bound(self.box)))


class RunTest(Fixture):
    def test_a_clean_run_leaves_the_real_repository_byte_identical_and_the_sandbox_until_settled(self) -> None:
        before = self.snapshot()
        result = self.launch({"actions": [results()]})
        self.assertEqual(result["outcome"], "ran")
        self.assertEqual((result["breach"], result["unbound"], result["keep"]), ([], [], False))
        self.assertEqual(review.compare_snapshots(before, self.snapshot()), [])
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        entries = fr.validate_results(Path(result["output_dir"]), self.flows)
        self.assertEqual([e.flow_id for e in entries], ["flow-1", "flow-2"])
        sandbox = Path(result["sandbox"])
        self.assertTrue(sandbox.is_dir())
        self.settle(result)
        self.assertFalse(sandbox.exists())
        self.settle(result)  # more than once is safe

    def test_the_tester_is_launched_under_the_closed_boundary(self) -> None:
        result = self.launch({"actions": [results()]})
        record = fake.read_log(self.log_path)[0]
        argv = record["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "Bash")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Bash")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(record["cwd"], result["output_dir"].__str__())
        sandbox = record["settings"]["sandbox"]
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["excludedCommands"], [])
        env = record["env"]
        self.assertEqual(env["PATH"].split(":")[0], str(Path(result["sandbox"]) / "bin"))
        self.assertNotIn(str(self.tools), env["PATH"])
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "file")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(Path(result["sandbox"]) / "claude-home"))
        self.assertIn(f"Tested head: {self.head}", record["prompt"])
        self.assertIn("flow-2", record["prompt"])
        self.settle(result)

    def test_a_write_into_the_real_repository_is_refused_by_the_sandbox_and_the_repository_is_unchanged(self) -> None:
        before = self.snapshot()
        target = self.repo / "src" / "app.py"
        original = target.read_bytes()
        result = self.launch({"actions": [{"op": "write", "path": str(target), "data": "hacked\n"}, results()]})
        self.assertTrue(self.actions()[0]["refused"])
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(result["breach"], [])
        self.assertEqual(review.compare_snapshots(before, self.snapshot()), [])
        self.settle(result)

    def test_a_write_into_the_real_repository_is_a_breach_when_the_sandbox_fails(self) -> None:
        target = self.repo / "src" / "app.py"
        result = self.launch({"emulate_sandbox": False,
                              "actions": [{"op": "write", "path": str(target), "data": "hacked\n"}, results()]})
        self.assertIn("file:src/app.py", result["breach"])
        self.assertTrue(result["keep"])
        self.assertTrue(Path(result["sandbox"]).is_dir())

    def test_a_write_into_an_ignored_path_of_the_real_repository_is_refused_by_the_sandbox(self) -> None:
        paths = [self.repo / ".ai-review" / "x", self.repo / ".controller" / "y", self.repo / "build" / "z"]
        actions = [{"op": "write", "path": str(p), "data": "x"} for p in paths]
        result = self.launch({"actions": [*actions, results()]})
        self.assertTrue(all(a["refused"] for a in self.actions()[:3]))
        self.assertFalse(any(p.exists() for p in paths))
        self.settle(result)

    def test_a_push_to_the_real_origin_path_is_detected(self) -> None:
        result = self.launch({"emulate_sandbox": False, "actions": [
            {"op": "shell", "argv": ["git", "push", str(self.origin), "HEAD:refs/heads/evil"]}, results()]})
        self.assertIn("origin refs", result["breach"])
        self.assertTrue(result["keep"])
        self.assertIn("refs/heads/evil", git(self.origin, "for-each-ref"))

    def test_a_push_by_url_to_a_non_file_remote_is_prevented_by_git_and_the_real_origin_is_unchanged(self) -> None:
        refs = git(self.origin, "for-each-ref")
        result = self.launch({"actions": [
            {"op": "shell", "argv": ["git", "push", "https://example.invalid/x.git", "HEAD:refs/heads/evil"]},
            {"op": "shell", "argv": ["git", "ls-remote", "ssh://git@example.invalid/x.git"]}, results()]})
        first, second = self.actions()[:2]
        self.assertNotEqual(first["exit"], 0)
        self.assertNotEqual(second["exit"], 0)
        self.assertRegex(first["stderr"].lower(), "protocol|not allowed|transport")
        self.assertEqual(git(self.origin, "for-each-ref"), refs)
        self.assertEqual(result["breach"], [])
        self.settle(result)

    def test_a_call_of_a_fake_is_logged_and_is_not_a_breach(self) -> None:
        result = self.launch({"actions": [{"op": "shell", "argv": ["gh", "pr", "create"]},
                                          {"op": "shell", "argv": ["claude", "-p", "hi"]},
                                          {"op": "shell", "argv": ["codex", "exec", "x"]},
                                          {"op": "shell", "argv": ["ssh", "host"]}, results()]})
        self.assertEqual(sorted(result["fake_calls"]), sorted(fr.FAKE_PROGRAMS))
        self.assertEqual(result["fake_calls"]["gh"][0]["argv"], ["pr", "create"])
        self.assertEqual((result["breach"], result["unbound"]), ([], []))
        self.assertTrue(all(a["exit"] == 127 for a in self.actions()[:4]))
        self.settle(result)

    def test_the_real_claude_started_by_absolute_path_makes_a_session_and_is_a_breach(self) -> None:
        stand_in = self.tools / "real-claude"
        self.tools.mkdir(exist_ok=True)
        stand_in.write_text("#!/bin/sh\nmkdir -p \"$CLAUDE_CONFIG_DIR/projects/p\"\necho '{}' > "
                            "\"$CLAUDE_CONFIG_DIR/projects/p/s.jsonl\"\n")
        stand_in.chmod(0o755)
        result = self.launch({"actions": [{"op": "shell", "argv": [str(stand_in)]}, results()]})
        self.assertEqual(result["breach"], ["session: projects/p/s.jsonl"])
        self.assertTrue(result["keep"])

    def test_a_session_created_in_the_jobs_claude_home_is_a_breach(self) -> None:
        result = self.launch({"actions": [{"op": "session"}, results()]})
        self.assertEqual(result["breach"], ["session: projects/nested/session.jsonl"])

    def test_the_jobs_credential_copy_does_not_survive_a_kept_sandbox(self) -> None:
        result = self.launch({"actions": [{"op": "session"}, results()]})
        self.assertFalse((Path(result["sandbox"]) / "claude-home").exists())

    def test_restored_source_and_checklist_edits_are_refused_at_the_first_write_and_never_differ(self) -> None:
        stop = threading.Event()
        seen: list[str] = []
        job = "job-1"
        paths = {rel: self.runtime / "functional" / job / "clone" / rel
                 for rel in ("src/app.py", "docs/ACTIVE_MILESTONE.md")}
        originals = {rel: (self.repo / rel).read_bytes() for rel in paths}

        def poll() -> None:
            while not stop.is_set():
                for rel, path in paths.items():
                    try:
                        if path.read_bytes() != originals[rel]:
                            seen.append(rel)
                    except OSError:
                        pass
                time.sleep(0.002)

        watcher = threading.Thread(target=poll, daemon=True)
        watcher.start()
        try:
            result = self.launch({"actions": [
                {"op": "restore", "path": "src/app.py", "new": "print('edited')\n"},
                {"op": "restore", "path": "docs/ACTIVE_MILESTONE.md", "new": "edited\n"},
                results()]}, job_id=job)
        finally:
            stop.set()
            watcher.join(2)
        self.assertEqual(seen, [])
        self.assertTrue(all(a["refused"] for a in self.actions()[:2]))
        self.assertEqual((result["breach"], result["unbound"]), ([], []))
        self.settle(result)

    def test_an_edited_copy_under_tmpdir_is_recorded_as_the_stated_boundary_allows(self) -> None:
        result = self.launch({"actions": [{"op": "copy_edit", "path": "src/app.py", "new": "print('edited')\n"},
                                          results()]})
        self.assertEqual(self.actions()[0]["check_saw"], "print('edited')\n")
        self.assertEqual((result["breach"], result["unbound"]), ([], []))
        self.assertEqual((Path(result["sandbox"]) / "clone/src/app.py").read_text(), "print('hello')\n")
        self.assertEqual(len(fr.validate_results(Path(result["output_dir"]), self.flows)), 2)
        self.settle(result)

    def test_the_four_binding_cases_report_passed_and_are_refused_with_the_sandbox_kept(self) -> None:
        cases = {
            "an edit": [{"op": "write", "path": "src/app.py", "data": "edited\n"}],
            "an untracked file": [{"op": "write", "path": "new.py", "data": "x\n"}],
            "the checklist": [{"op": "write", "path": "docs/ACTIVE_MILESTONE.md", "data": "x\n"}],
            "a moved head": [{"op": "shell", "argv": ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                                                      "-q", "--allow-empty", "-m", "moved"]}],
        }
        for name, actions in cases.items():
            with self.subTest(name):
                result = self.launch({"emulate_sandbox": False, "actions": [*actions, results()]}, item=name)
                self.assertTrue(result["unbound"], name)
                self.assertTrue(result["keep"])
                self.assertEqual(result["outcome"], "ran")

    def test_an_ignored_build_output_file_is_allowed(self) -> None:
        result = self.launch({"actions": [{"op": "write", "path": "build/out.o", "data": "o"}, results()]})
        self.assertFalse(self.actions()[0]["refused"])
        self.assertEqual(result["unbound"], [])
        self.settle(result)

    def test_the_git_cases_of_a_tester_that_steers_git_end_unbound(self) -> None:
        marker = self.tmp / "ran-marker"
        result = self.launch({"emulate_sandbox": False, "actions": [
            {"op": "git_config", "key": "diff", "value": f"external = touch {marker}"},
            {"op": "git_config", "key": "core", "value": f"fsmonitor = touch {marker}"},
            {"op": "git_config", "key": "filter \"c\"", "value": f"clean = touch {marker}"}, results()]})
        self.assertIn(".git/config: changed", result["unbound"])
        self.assertFalse(marker.exists())

    def test_the_sandbox_is_kept_after_a_breach_and_removed_when_the_items_next_job_starts(self) -> None:
        first = self.launch({"actions": [{"op": "session"}, results()]})
        kept = Path(first["sandbox"])
        self.assertTrue(kept.is_dir())
        other = self.launch({"actions": [{"op": "session"}, results()]}, item="other-item")
        self.assertTrue(kept.is_dir(), "another item's job sweeps nothing of this item's")
        again = self.launch({"actions": [results()]})
        self.assertFalse(kept.exists())
        self.assertTrue(Path(other["sandbox"]).is_dir())
        self.settle(again)
        self.assertEqual(fr.kept_sandboxes(self.runtime, "item-1"), [])

    def test_a_run_past_the_timeout_ends_at_timeout_with_the_group_killed(self) -> None:
        result = self.launch({"hang_seconds": 30, "actions": [results()]}, timeout=2)
        self.assertEqual(result["outcome"], "timeout")
        self.assertEqual(result["breach"], [])
        self.settle(result)

    def test_a_usage_limit_is_reported_and_the_credential_copy_back_ran(self) -> None:
        result = self.launch({"verdict": "limit", "actions": [results()]})
        self.assertEqual(result["outcome"], "limit")
        self.assertEqual(result["credentials"], "unchanged")
        self.assertIn("telemetry", result)
        self.settle(result)


class UnavailableTest(Fixture):
    def test_no_claude_binary_is_unavailable_and_nothing_is_built(self) -> None:
        with self.assertRaises(fr.TesterUnavailable) as caught:
            review.launch_functional(
                candidate=self.candidate, flows=self.flows, runtime_root=self.runtime, job_id="x", work_item_id="i",
                operator_home=self.home, model=MODEL, effort="medium", timeout=5,
                binary_name=str(self.tmp / "missing" / "claude"), probe=lambda: None, inherited_env={})
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_NO_BINARY)
        self.assertFalse((self.runtime / "functional").exists())

    def test_a_refused_role_is_unavailable(self) -> None:
        with mock.patch.object(review.harness_registry, "for_role", side_effect=HarnessRoleError("no", evidence={})):
            with self.assertRaises(fr.TesterUnavailable) as caught:
                self.launch({"actions": [results()]})
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_ROLE_REFUSED)

    def test_a_failing_probe_is_unavailable_and_nothing_is_built(self) -> None:
        def probe() -> None:
            raise fr.unavailable(fr.UNAVAILABLE_ENFORCEMENT, "bwrap is missing")

        with self.assertRaises(fr.TesterUnavailable):
            self.launch({"actions": [results()]}, probe=probe)
        self.assertFalse((self.runtime / "functional").exists())

    def test_the_clis_own_refusal_to_start_without_its_sandbox_is_unavailable_and_the_sandbox_is_removed(self) -> None:
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.launch({"verdict": "unavailable"}, job_id="gone")
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_ENFORCEMENT)
        self.assertFalse((self.runtime / "functional" / "gone").exists())

    def test_a_clone_that_does_not_match_the_manifest_is_unavailable_before_the_tester_starts(self) -> None:
        repo = self.tmp / "crlf"
        head = make_repo(repo, {".gitattributes": "*.txt text eol=crlf\n"})
        candidate = fr.capture_candidate(repo, head)
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.launch({"actions": [results()]}, candidate=candidate)
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_CLONE_MISMATCH)
        self.assertFalse(self.tmp.joinpath("job-1.log").exists(), "the tester was started")


class OriginTest(Fixture):
    def test_a_path_origins_refs_are_read_and_a_url_origin_is_never_read(self) -> None:
        refs = review._local_origin_refs(self.repo)
        self.assertIn("refs/heads/main", refs)
        git(self.repo, "remote", "set-url", "origin", "https://example.invalid/r.git")
        with mock.patch.object(review.subprocess, "run", wraps=review.subprocess.run) as spy:
            self.assertIsNone(review._local_origin_refs(self.repo))
        self.assertEqual(len(spy.call_args_list), 1, "only the local configuration was read")

    def test_an_unreadable_path_origin_is_an_error_the_run_reports_as_a_breach(self) -> None:
        git(self.repo, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        with self.assertRaises(OSError):
            review._local_origin_refs(self.repo)
        result = self.launch({"actions": [results()]})
        self.assertTrue(any(item.startswith("origin:") for item in result["breach"]))


class ArgvAndSettingsTest(Fixture):
    FLAGS = ("--tools", "Bash", "--allowedTools", "Bash", "--permission-mode", "dontAsk", "--permission-prompts", "none",
             "--settings", "/s.json")

    def test_the_command_line_is_checked_element_by_element(self) -> None:
        command = review.functional_argv(binary=Path("/b/claude"), model=MODEL, effort="medium", flags=self.FLAGS)
        review.check_functional_argv(command, model=MODEL, effort="medium", flags=self.FLAGS)
        for index in range(1, len(command)):
            with self.subTest(index=index), self.assertRaises(review.ReviewLaunchRefusedError):
                review.check_functional_argv([*command[:index], "x", *command[index + 1:]], model=MODEL,
                                             effort="medium", flags=self.FLAGS)
        with self.assertRaises(review.ReviewLaunchRefusedError):
            review.check_functional_argv([*command, "--add-dir", "/"], model=MODEL, effort="medium", flags=self.FLAGS)

    def test_an_alias_model_and_a_bad_effort_refuse(self) -> None:
        for kwargs in ({"model": "sonnet", "effort": "medium"}, {"model": MODEL, "effort": "ultra"}):
            with self.subTest(**kwargs), self.assertRaises(review.ReviewLaunchRefusedError):
                review.functional_argv(binary=Path("/b/claude"), flags=self.FLAGS, **kwargs)

    def test_the_settings_on_disk_must_be_the_ones_the_sandbox_derives(self) -> None:
        box = fr.build_sandbox(self.candidate, runtime_root=self.runtime, job_id="settings", work_item_id="i")
        review.check_functional_settings(box)
        document = json.loads(box.settings_path.read_text())
        for mutate in (lambda d: d["sandbox"]["filesystem"]["allowWrite"].append("/tmp"),
                       lambda d: d["sandbox"]["filesystem"]["allowWrite"].append(str(box.clone)),
                       lambda d: d["sandbox"].__setitem__("allowUnsandboxedCommands", True),
                       lambda d: d["sandbox"]["filesystem"]["denyWrite"].pop()):
            mutated = json.loads(json.dumps(document))
            mutate(mutated)
            box.settings_path.write_text(json.dumps(mutated))
            with self.assertRaises(review.ReviewLaunchRefusedError):
                review.check_functional_settings(box)

    def test_a_writable_root_outside_the_sandbox_or_at_the_candidate_is_refused_even_if_settings_agree(self) -> None:
        box = fr.build_sandbox(self.candidate, runtime_root=self.runtime, job_id="roots", work_item_id="i")
        for bad in (Path("/tmp"), box.clone, box.clone / ".git", box.clone / ".git" / "hooks"):
            with self.subTest(bad=str(bad)), mock.patch.object(
                    fr.Sandbox, "scratch_dirs", new_callable=mock.PropertyMock, return_value=[bad]):
                box.settings_path.write_text(json.dumps(box.settings()))
                with self.assertRaises(review.ReviewLaunchRefusedError):
                    review.check_functional_settings(box)


class LayoutTest(unittest.TestCase):
    def test_the_functional_layout_is_the_sandbox_folder_and_the_review_layout_is_unchanged(self) -> None:
        layout = review.JobLayout(Path("/rt"), "j", area="functional")
        self.assertEqual(layout.base, Path("/rt/functional/j"))
        self.assertEqual(layout.claude_home, Path("/rt/functional/j/claude-home"))
        self.assertEqual(layout.rel("x"), "functional/j/x")
        self.assertEqual(review.JobLayout(Path("/rt"), "j").base, Path("/rt/reviews/j"))
        self.assertEqual(review.JobLayout(Path("/rt"), "j").rel("x"), "reviews/j/x")

    def test_the_role_is_declared_for_claude_code_only(self) -> None:
        from controller import harness
        harness.for_role("claude-code", harness.FUNCTIONAL_TESTER)
        with self.assertRaises(HarnessRoleError):
            harness.for_role("codex", harness.FUNCTIONAL_TESTER)

    def test_the_init_check_takes_the_testers_tool_set(self) -> None:
        init = {"tools": ["Bash"], "mcp_servers": [], "permissionMode": "dontAsk", "skills": [],
                "slash_commands": [], "model": MODEL, "memory_paths": None}
        review.check_claude_init(init, model=MODEL, tools=fr.TESTER_TOOLS)
        with self.assertRaises(review.ReviewLaunchRefusedError):
            review.check_claude_init(init, model=MODEL)  # the review's tool set is not the tester's


if __name__ == "__main__":
    unittest.main()
