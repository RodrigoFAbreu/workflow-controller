"""`workflow-controller-h4e3b` CP1: the guarded run of the managed generator (`controller.generator_run`), D1 to D5a.

Offline, over real Git repositories and a stub generator that does what a test asks through `.ai-review/mode`; the real
generator is driven through the job in `tests/test_codex_apply_regeneration.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import generator_run as gr  # noqa: E402

WID = "wi-1"
RECORD = ".workflow-manager/installation.json"
LIST = f".ai-review/{WID}/current/CONTEXT_FILES.txt"
PLAN_LIST = f".ai-review/{WID}/plan-inputs/CONTEXT_FILES.txt"
COVERED: set[str] = set()

STUB = """#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
mode=$(cat .ai-review/mode 2>/dev/null || echo ok)
printf '%s\\n' "$@" > .ai-review/args.txt
case "$mode" in
  ok)
    PYTHONPATH="$PWD/scripts:${PYTHONPATH:-}" python3 - > .ai-review/probe.json <<'PY'
import json, os, sys
print(json.dumps({"path": sys.path, "exe": os.path.realpath(sys.executable), "pycache": os.environ.get("PYTHONPYCACHEPREFIX")}))
PY
    ;;
  fail) echo "boom" >&2; exit 3 ;;
  armour) echo "-----BEGIN OPENSSH PRIVATE KEY-----"; { echo "-----BEGIN RSA PRIVATE KEY-----"; echo "secret"; } >&2; exit 3 ;;
  sleep) sleep 30 ;;
  plant) : > scripts/sitecustomize.py ;;
  list) echo /etc/passwd > .ai-review/wi-1/current/CONTEXT_FILES.txt ;;
esac
"""


def git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=check)


def write(root: Path, rel: str, data: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="gen-run-")
        self.addCleanup(tmp.cleanup)
        self.outside = Path(tmp.name) / "outside"
        self.outside.mkdir()
        self.canary = write(self.outside, "canary.txt", "CANARY-SECRET")
        self.root = Path(tmp.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        write(self.root, "scripts/prepare-ai-review.sh", STUB).chmod(0o755)
        write(self.root, "scripts/workflow_x.py", "VALUE = 1\n")
        write(self.root, ".gitignore", ".ai-review/\n")
        write(self.root, "src/a.txt", "a\n")
        write(self.root, ".ai-review/.keep", "")
        self.managed = {f"scripts/{name}": {"sha256": digest(self.root / "scripts" / name), "executable": False}
                        for name in ("prepare-ai-review.sh", "workflow_x.py")}
        self.write_record(self.managed)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD").stdout.strip()

    def write_record(self, managed: dict) -> None:
        write(self.root, RECORD, json.dumps({"managed": managed}))

    def commit(self, message: str = "change") -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD").stdout.strip()

    def refused(self, cause: str, call, *args, **kwargs) -> gr.GeneratorRefused:
        with self.assertRaises(gr.GeneratorRefused) as ctx:
            call(*args, **kwargs)
        self.assertEqual(ctx.exception.cause, cause, ctx.exception.message)
        COVERED.add(cause)
        return ctx.exception


class CheckLandingTest(Fixture):
    def land(self, mutate) -> str:
        mutate()
        return self.commit()

    def test_a_clean_range_none_and_the_base_itself_pass(self) -> None:
        head = self.land(lambda: write(self.root, "src/b.txt", "b\n"))
        gr.check_landing(self.root, self.base, head)
        gr.check_landing(self.root, self.base, None)
        gr.check_landing(self.root, self.base, self.base)

    def test_each_status_letter_on_the_surface_is_refused(self) -> None:
        cases = {
            "A": lambda: write(self.root, "scripts/sitecustomize.py", "x"),
            "M": lambda: write(self.root, "scripts/workflow_x.py", "VALUE = 2\n"),
            "D": lambda: (self.root / "scripts/workflow_x.py").unlink(),
            "T": lambda: ((self.root / "scripts/workflow_x.py").unlink(),
                          os.symlink("/etc/passwd", self.root / "scripts/workflow_x.py")),
        }
        for letter, mutate in cases.items():
            with self.subTest(letter):
                git(self.root, "reset", "-q", "--hard", self.base)
                head = self.land(mutate)
                exc = self.refused("surface_landing_touched", gr.check_landing, self.root, self.base, head)
                self.assertTrue(any(p.startswith(letter + " scripts/") for p in exc.evidence["paths"]),
                                exc.evidence["paths"])

    def test_the_installation_record_and_a_rename_are_on_the_surface(self) -> None:
        git(self.root, "reset", "-q", "--hard", self.base)
        head = self.land(lambda: write(self.root, RECORD, "{}"))
        self.refused("surface_landing_touched", gr.check_landing, self.root, self.base, head)
        git(self.root, "reset", "-q", "--hard", self.base)
        head = self.land(lambda: git(self.root, "mv", "scripts/workflow_x.py", "src/moved.py"))
        exc = self.refused("surface_landing_touched", gr.check_landing, self.root, self.base, head)
        self.assertIn("D scripts/workflow_x.py", exc.evidence["paths"])

    def test_a_head_that_is_not_a_descendant_and_an_unreadable_range_are_refused(self) -> None:
        head = self.land(lambda: write(self.root, "src/b.txt", "b\n"))
        git(self.root, "checkout", "-q", "-b", "other", self.base)
        other = self.land(lambda: write(self.root, "src/c.txt", "c\n"))
        self.refused("surface_landing_touched", gr.check_landing, self.root, head, other)
        self.refused("surface_landing_touched", gr.check_landing, self.root, self.base, "0" * 40)


class CheckTreeTest(Fixture):
    def test_the_installed_tree_passes_with_pycache_and_hidden_names(self) -> None:
        write(self.root, "scripts/__pycache__/workflow_x.cpython-312.pyc", b"junk")
        write(self.root, "scripts/.ai-review/notes", "x")
        gr.check_tree(self.root)

    def test_a_modified_missing_or_linked_managed_file_is_refused(self) -> None:
        for name, mutate in {
            "modified": lambda: write(self.root, "scripts/workflow_x.py", "VALUE = 2\n"),
            "missing": lambda: (self.root / "scripts/workflow_x.py").unlink(),
            "linked": lambda: ((self.root / "scripts/workflow_x.py").unlink(),
                               os.symlink("../src/a.txt", self.root / "scripts/workflow_x.py")),
        }.items():
            with self.subTest(name):
                git(self.root, "checkout", "-q", "--", "scripts")
                mutate()
                self.refused("surface_script_modified", gr.check_tree, self.root)

    def test_every_foreign_kind_of_entry_is_refused_by_name(self) -> None:
        plants = {
            "sitecustomize.py": lambda r: write(r, "scripts/sitecustomize.py", "x"),
            "secrets.py": lambda r: write(r, "scripts/secrets.py", "x"),
            "workflow_x.pyc": lambda r: write(r, "scripts/workflow_x.pyc", b"x"),
            "ext.cpython-312-x86_64-linux-gnu.so": lambda r: write(r, "scripts/ext.cpython-312-x86_64-linux-gnu.so",
                                                                    b"x"),
            "pkg": lambda r: write(r, "scripts/pkg/__init__.py", "x"),
            "linked": lambda r: os.symlink("../src", r / "scripts/linked"),
        }
        for name, plant in plants.items():
            with self.subTest(name):
                git(self.root, "clean", "-fdxq", "scripts")
                plant(self.root)
                exc = self.refused("surface_foreign_entry", gr.check_tree, self.root)
                self.assertEqual(len(exc.evidence["paths"]), 1)

    def test_an_unreadable_or_odd_record_is_refused(self) -> None:
        (self.root / RECORD).write_text("{not json")
        self.refused("surface_unreadable", gr.check_tree, self.root)
        (self.root / RECORD).unlink()
        os.symlink("../src/a.txt", self.root / RECORD)
        self.refused("surface_unreadable", gr.check_tree, self.root)
        (self.root / RECORD).unlink()
        self.write_record({**self.managed, "scripts/pkg/mod.py": {"sha256": "0" * 64}})
        self.refused("surface_unreadable", gr.check_tree, self.root)
        self.write_record({**self.managed, "scripts/x.py": {"sha256": "short"}})
        self.refused("surface_unreadable", gr.check_tree, self.root)

    def test_a_record_that_does_not_manage_the_generator_is_refused(self) -> None:
        self.write_record({"scripts/workflow_x.py": self.managed["scripts/workflow_x.py"]})
        self.refused("generator_not_managed", gr.check_tree, self.root)
        (self.root / RECORD).unlink()
        self.refused("generator_not_managed", gr.check_tree, self.root)

    def test_a_scripts_directory_that_is_a_link_is_refused(self) -> None:
        git(self.root, "mv", "scripts", "real")
        os.symlink("real", self.root / "scripts")
        self.refused("surface_script_modified", gr.check_tree, self.root)


class CheckCopySourcesTest(Fixture):
    def check(self, stage: str = "post-fix") -> None:
        gr.check_copy_sources(self.root, stage, self.base, WID)

    def test_a_clean_tree_and_a_clean_list_pass(self) -> None:
        write(self.root, "src/new.txt", "n\n")
        write(self.root, LIST, "src/a.txt\nsrc/new.txt\n")
        self.check()

    def test_each_reason_for_a_list_line(self) -> None:
        os.symlink(str(self.canary), self.root / ".ai-review/link.txt")  # ignored: not a changed path
        os.symlink(str(self.outside), self.root / ".ai-review/linkdir")
        cases = {
            "absolute": str(self.canary),
            "traversal": "../../../" + str(self.canary).lstrip("/"),
            "link": ".ai-review/link.txt",
            "parent link": ".ai-review/linkdir/canary.txt",
        }
        for name, line in cases.items():
            with self.subTest(name):
                write(self.root, LIST, f"src/a.txt\n{line}\n")
                exc = self.refused("copy_source_unsafe", self.check)
                self.assertEqual(exc.evidence["path"], line)
                self.assertEqual(exc.evidence["reason"], {"parent link": "link"}.get(name, name))
                self.assertNotIn("CANARY", exc.message)

    def test_escapes_is_a_path_whose_real_location_is_outside(self) -> None:
        write(self.root, LIST, "src/a.txt\n")
        with mock.patch.object(gr, "_under", return_value=False):
            exc = self.refused("copy_source_unsafe", self.check)
        self.assertEqual(exc.evidence["reason"], "escapes")

    def test_a_missing_file_a_source_proposal_and_a_directory_are_accepted(self) -> None:
        write(self.root, LIST, "no/such/file\n.ai-review/source/../../x\nsrc\n\n")
        self.check()

    def test_the_plan_stage_reads_the_plan_inputs_list_and_the_live_one(self) -> None:
        write(self.root, PLAN_LIST, f"{self.canary}\n")
        self.refused("copy_source_unsafe", self.check, "plan")
        self.check("post-fix")  # the implementation role's generator never reads plan-inputs
        (self.root / PLAN_LIST).unlink()
        write(self.root, LIST, "../x\n")
        self.refused("copy_source_unsafe", self.check, "plan")

    def test_a_changed_link_tracked_or_untracked_is_refused_and_an_ignored_one_is_not_a_candidate(self) -> None:
        os.symlink(str(self.canary), self.root / "src/tracked-link")
        head = self.commit()
        gr.check_copy_sources(self.root, "post-fix", head, WID)  # unchanged since its own base: not in the diff
        exc = self.refused("copy_source_unsafe", gr.check_copy_sources, self.root, "post-fix", self.base, WID)
        self.assertEqual((exc.evidence["path"], exc.evidence["reason"]), ("src/tracked-link", "link"))
        git(self.root, "reset", "-q", "--hard", self.base)
        os.symlink(str(self.canary), self.root / "src/untracked-link")
        exc = self.refused("copy_source_unsafe", self.check)
        self.assertEqual(exc.evidence["path"], "src/untracked-link")
        write(self.root, ".gitignore", ".ai-review/\nsrc/untracked-link\n")
        git(self.root, "add", ".gitignore")
        self.check()

    def test_a_parent_component_that_is_a_link_is_refused_for_a_changed_path(self) -> None:
        os.symlink(str(self.outside), self.root / "linkdir")
        git(self.root, "add", "linkdir")
        exc = self.refused("copy_source_unsafe", self.check)
        self.assertEqual(exc.evidence["path"], "linkdir")

    def test_a_deleted_path_is_not_a_copy_source(self) -> None:
        (self.root / "src/a.txt").unlink()
        self.check()

    def test_a_list_that_is_itself_a_link_is_refused(self) -> None:
        (self.root / LIST).parent.mkdir(parents=True)
        os.symlink(str(self.canary), self.root / LIST)
        self.refused("copy_source_unsafe", self.check)


class RunTest(Fixture):
    def mode(self, value: str) -> None:
        write(self.root, ".ai-review/mode", value)

    def run_it(self, **kwargs):
        kwargs.setdefault("stage", "post-fix")
        return gr.run(self.root, base_commit=self.base, work_item_id=WID, **kwargs)

    def test_a_clean_run_reports_progress_and_runs_the_generator_with_the_interpreter_pins(self) -> None:
        seen: list[str] = []
        result = self.run_it(progress=seen.append, stage="plan")
        self.assertEqual((result.returncode, seen), (0, ["checked", "started"]))
        args = (self.root / ".ai-review/args.txt").read_text().split()
        self.assertEqual(args, [self.base, "plan", WID])
        probe = json.loads((self.root / ".ai-review/probe.json").read_text())
        self.assertNotIn("", probe["path"])
        self.assertNotIn(str(self.root), probe["path"])
        self.assertIn(str(self.root / "scripts"), probe["path"])
        self.assertEqual(probe["exe"], os.path.realpath(sys.executable))
        self.assertFalse(list((self.root / "scripts").glob("__pycache__/*")))

    def test_a_refusal_before_the_run_does_not_start_the_generator(self) -> None:
        write(self.root, "scripts/sitecustomize.py", "x")
        seen: list[str] = []
        self.refused("surface_foreign_entry", self.run_it, progress=seen.append)
        self.assertEqual(seen, [])
        self.assertFalse((self.root / ".ai-review/args.txt").exists())
        write(self.root, "scripts/sitecustomize.py", "x").unlink()
        write(self.root, LIST, f"{self.canary}\n")
        self.refused("copy_source_unsafe", self.run_it)
        self.assertFalse((self.root / ".ai-review/args.txt").exists())

    def test_the_landing_is_checked_when_a_base_is_given(self) -> None:
        write(self.root, "scripts/workflow_x.py", "VALUE = 2\n")
        head = self.commit()
        self.refused("surface_landing_touched", self.run_it, carry_base=self.base, landed_head=head)

    def test_a_non_zero_exit_is_generator_failed_with_the_bounded_tail(self) -> None:
        self.mode("fail")
        exc = self.refused("generator_failed", self.run_it)
        self.assertEqual(exc.evidence["returncode"], 3)
        self.assertIn("boom", exc.evidence["stderr_tail"])

    def test_key_armour_in_the_output_is_withheld(self) -> None:
        self.mode("armour")
        exc = self.refused("generator_failed", self.run_it)
        self.assertNotIn("PRIVATE KEY", json.dumps(exc.evidence) + exc.message)
        self.assertNotIn("secret", json.dumps(exc.evidence) + exc.message)

    def test_a_long_output_is_cut_to_the_last_four_kibibytes(self) -> None:
        self.assertEqual(len(gr._tail(b"x" * 10000)), gr.TAIL_BYTES)

    def test_a_run_past_its_bound_is_generator_timed_out(self) -> None:
        self.mode("sleep")
        self.refused("generator_timed_out", self.run_it, timeout=2.0)

    def test_a_tree_change_during_the_run_is_reported_whatever_the_exit_status(self) -> None:
        self.mode("plant")
        exc = self.refused("surface_changed_during_run", self.run_it)
        self.assertEqual(exc.evidence["was"], "surface_foreign_entry")

    def test_a_context_list_rewritten_during_the_run_is_reported(self) -> None:
        write(self.root, LIST, "src/a.txt\n")
        self.mode("list")
        exc = self.refused("surface_changed_during_run", self.run_it)
        self.assertEqual(exc.evidence["was"], "copy_source_unsafe")

    def test_a_git_that_cannot_be_isolated_is_git_not_isolated(self) -> None:
        hook = write(self.root, ".git/hooks/post-index-change", "#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        self.refused("git_not_isolated", self.run_it)

    def test_an_unknown_stage_is_a_programming_error(self) -> None:
        with self.assertRaises(ValueError):
            self.run_it(stage="implementation")


class CausesCoveredTest(unittest.TestCase):
    def test_every_cause_has_a_case(self) -> None:
        # Runs after the classes above (alphabetical order of the suite's own classes is not relied on: the set is
        # filled by `refused`, and this test is repeated at the end of the module run by `load_tests`).
        self.assertEqual(len(set(gr.CAUSES)), len(gr.CAUSES))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(tests)

    class Last(unittest.TestCase):
        def test_every_cause_of_the_module_has_a_case(self) -> None:
            self.assertEqual(set(gr.CAUSES) - COVERED, set())

    suite.addTest(Last("test_every_cause_of_the_module_has_a_case"))
    return suite


if __name__ == "__main__":
    unittest.main()
