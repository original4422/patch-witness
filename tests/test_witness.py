import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from patch_witness.core import classify, compare, git


def command(repo, *args):
    return git(repo, *args).decode().strip()


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        command(self.repo, "init", "-q")
        command(self.repo, "config", "user.name", "Test")
        command(self.repo, "config", "user.email", "test@example.invalid")
        self.write("subject.py", "VALUE = 0\n")
        self.base = self.commit()
        self.write("subject.py", "VALUE = 1\n")

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))

    def commit(self):
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-qm", "fixture")
        return command(self.repo, "rev-parse", "HEAD")

    def run_case(self, source, test_id="Tests.test_value", **options):
        self.write("test_subject.py", source)
        head = self.commit()
        return compare(self.repo, self.base, head, "test_subject.py", [test_id], **options)

    def states(self, report):
        row = report["tests"][0]
        return row["base"]["status"], row["head"]["status"], row["witness"]

    def test_assertion_to_pass_and_snapshot_import(self):
        result = self.run_case('''
            import unittest
            import subject
            class Tests(unittest.TestCase):
                def test_value(self): self.assertEqual(subject.VALUE, 1)
        ''')
        self.assertEqual(self.states(result), ("assertion_failure", "pass", True))
        for side in ("base", "head"):
            self.assertIn({"module": "subject", "path": "subject.py"}, result["tests"][0][side]["record"]["imports"])
        self.assertEqual(len(result["test_sha256"]), 64)
        self.assertEqual(len(result["base"]), 40)

    def test_pass_to_pass_is_not_witness(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                def test_value(self): self.assertEqual(1, 1)
        ''')
        self.assertEqual(self.states(report), ("pass", "pass", False))

    def test_missing_dependency_is_import_error(self):
        result = self.run_case("import missing_dependency_witness\n")
        self.assertEqual(self.states(result), ("import_error", "import_error", False))

    def test_pythonpath_does_not_supply_missing_dependency(self):
        outside = Path(self.tmp.name) / "external"
        outside.mkdir()
        (outside / "external_subject.py").write_text("VALUE = 1")
        with patch.dict(os.environ, {"PYTHONPATH": str(outside)}):
            report = self.run_case("import external_subject\n")
        self.assertEqual(self.states(report), ("import_error", "import_error", False))

    def test_explicit_src_root(self):
        self.write("src/nested.py", "VALUE = 1")
        report = self.run_case('''
            import unittest
            import nested
            class Tests(unittest.TestCase):
                def test_value(self): self.assertEqual(nested.VALUE, 1)
        ''', source_roots=["src"])
        self.assertEqual(self.states(report), ("import_error", "pass", False))

    def test_skip(self):
        report = self.run_case('''
            import unittest
            @unittest.skip("deliberate")
            class Tests(unittest.TestCase):
                def test_value(self): self.fail()
        ''')
        self.assertEqual(self.states(report), ("skip", "skip", False))

    def test_zero_collection(self):
        report = self.run_case("import unittest\nclass Tests(unittest.TestCase): pass", "Tests")
        self.assertEqual(self.states(report), ("zero_tests", "zero_tests", False))

    def test_missing_test_id_is_collection_error(self):
        report = self.run_case("import unittest\nclass Tests(unittest.TestCase): pass")
        self.assertEqual(self.states(report), ("collection_error", "collection_error", False))

    def test_multiple_collection(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                def test_one(self): pass
                def test_two(self): pass
        ''', "Tests")
        self.assertEqual(self.states(report), ("multiple_tests", "multiple_tests", False))

    def test_setup_assertion_not_body_failure(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                def setUp(self): self.fail("setup")
                def test_value(self): pass
        ''')
        self.assertEqual(self.states(report), ("setup_error", "setup_error", False))

    def test_class_setup_assertion(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                @classmethod
                def setUpClass(cls): raise AssertionError("fixture")
                def test_value(self): pass
        ''')
        self.assertEqual(self.states(report), ("setup_error", "setup_error", False))

    def test_teardown_failure_overrides_body_assertion(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                def tearDown(self): self.fail("teardown")
                def test_value(self): self.fail("body")
        ''')
        self.assertEqual(self.states(report), ("teardown_error", "teardown_error", False))

    def test_cleanup_failure(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                def test_value(self): self.addCleanup(self.fail, "cleanup")
        ''')
        self.assertEqual(self.states(report), ("cleanup_error", "cleanup_error", False))

    def test_runtime_error_not_assertion(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                def test_value(self): raise RuntimeError("body")
        ''')
        self.assertEqual(self.states(report), ("test_error", "test_error", False))

    def test_subtest_assertion(self):
        report = self.run_case('''
            import unittest, subject
            class Tests(unittest.TestCase):
                def test_value(self):
                    for i in range(2):
                        with self.subTest(i=i): self.assertEqual(subject.VALUE, 1)
        ''')
        self.assertEqual(self.states(report), ("assertion_failure", "pass", True))

    def test_expected_failure(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                @unittest.expectedFailure
                def test_value(self): self.fail()
        ''')
        self.assertEqual(self.states(report), ("expected_failure", "expected_failure", False))

    def test_unexpected_success(self):
        report = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                @unittest.expectedFailure
                def test_value(self): pass
        ''')
        self.assertEqual(self.states(report), ("unexpected_success", "unexpected_success", False))

    def test_exit_zero_is_missing_result(self):
        result = self.run_case("import os\nos._exit(0)")
        self.assertEqual(self.states(result), ("missing_result", "missing_result", False))

    def test_exit_nonzero(self):
        result = self.run_case("import os\nos._exit(3)")
        self.assertEqual(self.states(result), ("worker_exit", "worker_exit", False))

    def test_system_exit(self):
        result = self.run_case('''
            import unittest
            class Tests(unittest.TestCase):
                def test_value(self): raise SystemExit(0)
        ''')
        self.assertEqual(self.states(result), ("worker_exit", "worker_exit", False))

    def test_timeout_cleans_descendant(self):
        marker = Path(self.tmp.name) / "late-write"
        child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()"
        result = self.run_case(f'''\
            import subprocess, sys, time
            subprocess.Popen([sys.executable, "-c", {child!r}])
            time.sleep(20)
        ''', timeout=0.2)
        self.assertEqual(self.states(result), ("timeout", "timeout", False))
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_dirty_worktree_and_index_unchanged(self):
        source = "import unittest\nclass Tests(unittest.TestCase):\n    def test_value(self): pass"
        self.write("test_subject.py", source)
        head = self.commit()
        self.write("subject.py", "STAGED = True")
        command(self.repo, "add", "subject.py")
        self.write("subject.py", "UNSTAGED = True")
        self.write("untracked.txt", "keep me")
        def state():
            return (command(self.repo, "rev-parse", "HEAD"), (self.repo / ".git/index").read_bytes(),
                    {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.parts})
        before = state()
        compare(self.repo, self.base, head, "test_subject.py", ["Tests.test_value"])
        self.assertEqual(state(), before)

    def test_symlink_snapshot_rejected(self):
        (self.repo / "link").symlink_to("subject.py")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.run_case("pass")

    def test_path_escape_rejected(self):
        with self.assertRaisesRegex(ValueError, "repository-relative"):
            compare(self.repo, self.base, self.base, "../test.py", ["Tests.test_value"])

    def test_parent_rejects_incomplete_or_contradictory_results(self):
        data = {"protocol": 1, "test_id": "T.test", "completed": True, "collected": 1, "tests_run": 1,
                "events": [{"status": "pass", "phase": "test"}]}
        self.assertEqual(classify(data, "T.test"), "pass")
        self.assertEqual(classify({**data, "completed": False}, "T.test"), "invalid_result")
        self.assertEqual(classify({**data, "tests_run": 0}, "T.test"), "invalid_result")
        self.assertEqual(classify({**data, "events": []}, "T.test"), "missing_result")
        self.assertEqual(classify({**data, "events": [{"status": "assertion_failure", "phase": "setup"}]}, "T.test"), "invalid_result")

    def test_old_interpreter_is_rejected_before_execution(self):
        with patch("patch_witness.core.sys.version_info", (3, 10)):
            with self.assertRaisesRegex(ValueError, "Python 3.11"):
                compare(self.repo, self.base, self.base, "missing.py", ["T.test"])

    def test_cli_exit_codes(self):
        self.write("test_subject.py", "import unittest\nclass Tests(unittest.TestCase):\n    def test_value(self): pass")
        head = self.commit()
        args = [sys.executable, "-m", "patch_witness", "--repo", str(self.repo), "--base", self.base,
                "--head", head, "--test-file", "test_subject.py", "--test-id", "Tests.test_value"]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["all_witnesses"])
        result = subprocess.run(args + ["--timeout", "0"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
