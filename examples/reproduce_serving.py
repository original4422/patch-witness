"""Fixed serving deadline cases using an existing explicit dependency directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from patch_witness.core import compare, git
from reproduce import fingerprint

BASE = 'c4c03b63ae56e0aef1aa00cc6b587cd27cd6bf8b'
HEAD = '93460c5afd055b19c10781210c4c5429ba895f88'
TESTS = [
    'DeadlineTests.test_deadline_crossed_during_selection_never_enters_backend',
    'DeadlineTests.test_reverse_descendants_block_before_waiting_for_unrelated_stream',
    'DeadlineTests.test_caller_cancellation_cleans_active_requests',
]


def environment_fingerprint(root, python):
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob('*')):
        if not (path.is_file() or path.is_symlink()):
            continue
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        digest.update(str(path.lstat().st_mode).encode() + b'\0')
        digest.update(b'link:' + os.fsencode(os.readlink(path)) if path.is_symlink()
                      else b'file:' + hashlib.sha256(path.read_bytes()).digest())
        count += 1
    return {'files': count, 'tree_sha256': digest.hexdigest(),
            'python_executable_sha256': hashlib.sha256(python.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--dependency-dir', type=Path, required=True)
    parser.add_argument('--environment-root', type=Path, required=True, help='Existing environment tree to fingerprint')
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    args = parser.parse_args()
    repo = args.repo.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    if git(repo, 'diff', BASE, HEAD, '--', 'pyproject.toml', 'uv.lock'):
        raise ValueError('fixed source pair changed dependency declarations')
    before = {'repository': fingerprint(repo), 'environment': environment_fingerprint(args.environment_root, args.python)}
    report = compare(repo, BASE, HEAD, 'tests/test_deadlines.py', TESTS,
                     python=args.python, dependency_dirs=[args.dependency_dir], packages=['serving_lab'])
    after = {'repository': fingerprint(repo), 'environment': environment_fingerprint(args.environment_root, args.python)}
    (args.output / 'serving-deadlines.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'serving-state.json').write_text(json.dumps({'unchanged': before == after, 'before': before, 'after': after}, indent=2) + '\n')
    print(json.dumps({'unchanged': before == after, 'tests': [
        {'test_id': row['test_id'], 'base': row['base']['status'], 'head': row['head']['status'], 'witness': row['witness']}
        for row in report['tests']]}, indent=2))
    expected = [('assertion_failure', 'pass', True), ('assertion_failure', 'pass', True), ('pass', 'pass', False)]
    observed = [(row['base']['status'], row['head']['status'], row['witness']) for row in report['tests']]
    comparable = all(row.get("environment_match") is True for row in report["tests"])
    return 0 if before == after and observed == expected and comparable else 1


if __name__ == '__main__':
    raise SystemExit(main())
