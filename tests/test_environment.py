"""Explicit dependencies without site initialization or source fallback."""
import hashlib
import json
import io
import contextlib
import runpy
import types
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from patch_witness.core import compare, git


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.deps = self.root / 'deps'
        self.deps.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self.write('src/subject/__init__.py', 'VALUE = 0\n')
        self.base = self.commit()
        self.write('src/subject/__init__.py', 'VALUE = 1\n')
        self.options = dict(source_roots=['src'], python=sys.executable,
                            dependency_dirs=[self.deps], packages=['subject'])

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))

    def commit(self):
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'fixture')
        return git(self.repo, 'rev-parse', 'HEAD').decode().strip()

    def run_case(self, source, **overrides):
        self.write('tests/test_subject.py', source)
        head = self.commit()
        return compare(self.repo, self.base, head, 'tests/test_subject.py', ['Tests.test_value'],
                       **(self.options | overrides))

    def states(self, report):
        row = report['tests'][0]
        return row['base']['status'], row['head']['status'], row['witness']

    def test_dependencies_pth_sitecustomize_and_dirty_source(self):
        marker = self.root / 'executed'
        (self.deps / 'custom.pth').write_text(f'import pathlib; pathlib.Path({str(marker)!r}).touch()\n{self.repo / "src"}\n')
        (self.deps / 'sitecustomize.py').write_text(f'from pathlib import Path; Path({str(marker)!r}).touch()\n')
        (self.deps / 'helper.py').write_text('VALUE = 1\n')
        metadata = self.deps / 'helper-1.0.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text('Name: helper\nVersion: 1.0\n')
        (metadata / 'direct_url.json').write_text('{"url": "https://secret:token@example.invalid/private"}')
        self.write('tests/test_subject.py', '''
            import sys, unittest, subject, helper
            class Tests(unittest.TestCase):
                def test_value(self):
                    self.assertNotIn('site', sys.modules)
                    self.assertEqual(subject.VALUE, helper.VALUE)
        ''')
        head = self.commit()
        self.write('src/subject/__init__.py', 'VALUE = 99\n')
        git(self.repo, 'add', 'src/subject/__init__.py')
        self.write('src/subject/__init__.py', 'VALUE = 100\n')
        before = (git(self.repo, 'status', '--porcelain'), (self.repo / '.git/index').read_bytes(),
                  (self.repo / 'src/subject/__init__.py').read_bytes())
        report = compare(self.repo, self.base, head, 'tests/test_subject.py', ['Tests.test_value'], **self.options)
        self.assertEqual(self.states(report), ('assertion_failure', 'pass', True))
        self.assertFalse(marker.exists())
        self.assertEqual(before, (git(self.repo, 'status', '--porcelain'), (self.repo / '.git/index').read_bytes(),
                                 (self.repo / 'src/subject/__init__.py').read_bytes()))
        self.assertNotIn('secret', json.dumps(report))
        for side, value in [('base', 0), ('head', 1)]:
            record = report['tests'][0][side]['record']
            self.assertEqual(record['environment']['python']['version'], sys.version.split()[0])
            self.assertEqual(record['environment']['distributions'][0]['name'], 'helper')
            self.assertIn({'module': 'subject', 'path': 'src/subject/__init__.py',
                           'sha256': hashlib.sha256(f'VALUE = {value}\n'.encode()).hexdigest()}, record['imports'])

    def test_missing_root_cannot_use_installed_package(self):
        (self.repo / 'src/subject/__init__.py').unlink()
        self.base = self.commit()
        self.write('src/subject/__init__.py', 'VALUE = 1\n')
        (self.deps / 'subject').mkdir()
        (self.deps / 'subject/__init__.py').write_text('VALUE = 1\n')
        report = self.run_case('''
            import subject, unittest
            class Tests(unittest.TestCase):
                def test_value(self): self.assertEqual(subject.VALUE, 1)
        ''')
        self.assertEqual(self.states(report), ('source_error', 'pass', False))

    def test_missing_submodule_cannot_use_installed_copy(self):
        # Even a package extending its path cannot pick an absent base module from the environment.
        source = f'__path__.append({str(self.deps / "subject")!r})\n'
        self.write('src/subject/__init__.py', source)
        self.base = self.commit()
        self.write('src/subject/__init__.py', '')
        self.write('src/subject/new.py', 'VALUE = 1\n')
        (self.deps / 'subject').mkdir()
        (self.deps / 'subject/__init__.py').write_text('')
        (self.deps / 'subject/new.py').write_text('VALUE = 1\n')
        report = self.run_case('''
            import subject.new, unittest
            class Tests(unittest.TestCase):
                def test_value(self): self.assertEqual(subject.new.VALUE, 1)
        ''')
        self.assertEqual(self.states(report), ('source_error', 'pass', False))
        self.assertIn('Python source missing', report['tests'][0]['base']['record']['events'][0]['message'])

    def test_caught_scope_failure_and_changed_path_cannot_pass(self):
        self.write('src/subject/__init__.py', '')
        self.base = self.commit()
        report = self.run_case(f'''
            import subject, unittest
            try: import subject.missing
            except ImportError: pass
            subject.__path__.append({str(self.deps)!r})
            class Tests(unittest.TestCase):
                def test_value(self): pass
        ''')
        self.assertEqual(self.states(report), ('source_error', 'source_error', False))

    def test_pth_only_dependency_remains_import_error(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'hidden.py').write_text('VALUE = 1\n')
        (self.deps / 'path.pth').write_text(str(outside) + '\n')
        report = self.run_case('import hidden\n')
        self.assertEqual(self.states(report), ('import_error', 'import_error', False))

    def test_explicit_mode_classification(self):
        cases = [
            ('import httpx\n', 'import_error'),
            ('raise AssertionError("import")\n', 'import_error'),
            ('import unittest\nclass Tests(unittest.TestCase):\n def setUp(self): self.fail()\n def test_value(self): pass\n', 'setup_error'),
            ('import unittest\n@unittest.skip("skip")\nclass Tests(unittest.TestCase):\n def test_value(self): pass\n', 'skip'),
            ('import unittest\nclass Tests:\n @staticmethod\n def test_value(): return unittest.TestSuite()\n', 'zero_tests'),
            ('import os\nos._exit(2)\n', 'worker_exit'),
            ('import time\ntime.sleep(3)\n', 'timeout'),
        ]
        for source, expected in cases:
            with self.subTest(expected=expected):
                report = self.run_case(source, timeout=0.5)
                self.assertEqual(self.states(report), (expected, expected, False))

    def test_observed_environment_change_cannot_be_witness(self):
        sides = [
            {"status": "assertion_failure", "record": {"environment": {"python": {"version": "3.11"}}}},
            {"status": "pass", "record": {"environment": {"python": {"version": "3.14"}}}},
        ]
        for records, status in [(sides, "environment_mismatch"),
                                ([sides[0], {"status": "pass"}], "environment_unavailable")]:
            with self.subTest(status=status), patch("patch_witness.core.run_side", side_effect=records):
                report = self.run_case(f"# {status}\npass")
            self.assertEqual(self.states(report), ("assertion_failure", "pass", False))
            self.assertFalse(report["tests"][0]["environment_match"])
            self.assertEqual(report["tests"][0]["comparison_status"], status)

    def test_reproduction_preserves_failed_report_and_exits_nonzero(self):
        script = Path(__file__).resolve().parents[1] / "examples/reproduce_serving.py"
        for case in ("success", "import_error", "control_environment_mismatch"):
            success = case == "success"
            output = self.root / case
            rows = [{"test_id": str(i), "base": {"status": base}, "head": {"status": head}, "witness": witness,
                     "environment_match": not (case == "control_environment_mismatch" and i == 2)}
                    for i, (base, head, witness) in enumerate(
                        [("assertion_failure", "pass", True), ("assertion_failure", "pass", True), ("pass", "pass", False)]
                        if case != "import_error" else [("import_error", "import_error", False)] * 3)]
            args = [str(script), "--repo", str(self.repo), "--python", sys.executable,
                    "--dependency-dir", str(self.deps), "--environment-root", str(self.deps), "--output", str(output)]
            with patch("sys.argv", args), patch("patch_witness.core.compare", return_value={"tests": rows}), \
                 patch("patch_witness.core.git", return_value=b""), \
                 patch.dict("sys.modules", {"reproduce": types.SimpleNamespace(fingerprint=lambda repo: {"head": "unchanged"})}), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as outcome:
                runpy.run_path(str(script), run_name="__main__")
            self.assertEqual(outcome.exception.code, 0 if success else 1)
            self.assertEqual(json.loads((output / "serving-deadlines.json").read_text())["tests"], rows)
            self.assertTrue(json.loads((output / "serving-state.json").read_text())["unchanged"])

    def test_complete_option_group_required(self):
        with self.assertRaisesRegex(ValueError, 'supplied together'):
            compare(self.repo, self.base, self.base, 'missing.py', ['T.test'], python=sys.executable)
        with self.assertRaisesRegex(ValueError, 'top-level'):
            compare(self.repo, self.base, self.base, 'missing.py', ['T.test'], **(self.options | {'packages': ['subject.child']}))

    def test_cli_explicit_options(self):
        self.write('tests/test_subject.py', '''
            import subject, unittest
            class Tests(unittest.TestCase):
                def test_value(self): self.assertEqual(subject.VALUE, 1)
        ''')
        head = self.commit()
        result = subprocess.run([sys.executable, '-B', '-m', 'patch_witness', '--repo', str(self.repo),
                                 '--base', self.base, '--head', head, '--test-file', 'tests/test_subject.py',
                                 '--test-id', 'Tests.test_value', '--source-root', 'src', '--python', sys.executable,
                                 '--dependency-dir', str(self.deps), '--package', 'subject'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.states(json.loads(result.stdout)), ('assertion_failure', 'pass', True))


if __name__ == '__main__':
    unittest.main()
