"""Snapshot preparation, subprocess lifecycle, and parent-side verdicts."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import tempfile


GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=GIT_ENV).stdout


def relative_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] == ".git":
        raise ValueError("paths must be repository-relative and outside .git")
    return str(path)


def snapshot(repo, sha, destination):
    # git archive respects export-ignore; this tool requires the exact committed tree.
    tree = git(repo, "ls-tree", "-r", "-z", sha)
    for item in tree.split(b"\0"):
        if not item:
            continue
        metadata, name = item.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("snapshots support regular files only (no symlinks or submodules)")
        target = destination / relative_path(os.fsdecode(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git(repo, "cat-file", "blob", oid))
        target.chmod(0o755 if mode == "100755" else 0o644)


def classify(data, test_id):
    if not isinstance(data, dict) or data.get("protocol") != 1 or data.get("test_id") != test_id or data.get("completed") is not True:
        return "invalid_result"
    events = data.get("events")
    if not isinstance(events, list) or not events or any(not isinstance(e, dict) or not isinstance(e.get("status"), str) for e in events):
        return "missing_result"
    states = [e["status"] for e in events]
    # Any non-body failure wins over a prior assertion or pass.
    other = [s for s in states if s not in ("assertion_failure", "pass")]
    if other:
        return other[0]
    if data.get("collected") != 1 or data.get("tests_run") != 1:
        return "invalid_result"
    if all(s == "assertion_failure" for s in states):
        return "assertion_failure" if all(e.get("phase") == "test" for e in events) else "invalid_result"
    return "pass" if states == ["pass"] else "invalid_result"


def kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_side(root, test_file, test_id, source_roots, timeout, environment=None):
    worker = Path(__file__).with_name("worker.py").resolve()
    with tempfile.TemporaryDirectory(prefix="witness-result-") as directory:
        result_file = Path(directory) / "result.json"
        log_path = Path(directory) / "output.log"
        options = {"source_roots": source_roots, "environment": environment}
        command = [(environment or {}).get("python", sys.executable), "-I", "-S", "-B", str(worker),
                   str(root), test_file, test_id, str(result_file), json.dumps(options)]
        with log_path.open("wb") as log:
            proc = subprocess.Popen(command, cwd=root, stdout=log, stderr=log, start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Signal the group once, before reaping a timed-out worker.
                # Descendants may outlive a successful worker as well.
                kill_group(proc)
                proc.wait()
            if timed_out:
                return {"status": "timeout"}
        if proc.returncode:
            return {"status": "worker_exit", "exit_code": proc.returncode}
        if not result_file.exists():
            return {"status": "missing_result", "exit_code": proc.returncode}
        try:
            data = json.loads(result_file.read_text())
        except (ValueError, UnicodeDecodeError):
            return {"status": "invalid_result"}
        status = classify(data, test_id)
        return {"status": status, "record": data}


def compare(repo, base, head, test_file, test_ids, source_roots=(), timeout=30, *,
            python=None, dependency_dirs=(), packages=()):
    # Python 3.10 defers unittest callbacks until after teardown.
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11+ is required for phase-specific unittest events")
    if os.name != "posix":
        raise ValueError("this version requires POSIX process groups")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    environment = None
    if python or dependency_dirs or packages:
        if not (python and dependency_dirs and packages):
            raise ValueError("--python, --dependency-dir and --package must be supplied together")
        if any(not name.isidentifier() for name in packages):
            raise ValueError("--package must name a top-level regular Python package")
        interpreter = Path(python).absolute()
        directories = [Path(p).resolve() for p in dependency_dirs]
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise ValueError("--python must be an executable file")
        if any(not p.is_dir() for p in directories):
            raise ValueError("--dependency-dir must be an existing directory")
        environment = {"python": str(interpreter), "dependency_dirs": [str(p) for p in directories],
                       "packages": list(dict.fromkeys(packages))}
    repo = Path(repo).resolve()
    test_file = relative_path(test_file)
    source_roots = [relative_path(p) for p in source_roots]
    base_sha, head_sha = [git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip() for ref in (base, head)]
    test_bytes = git(repo, "show", f"{head_sha}:{test_file}")
    report = {"schema_version": 1, "base": base_sha, "head": head_sha,
              "test_file": test_file, "test_sha256": hashlib.sha256(test_bytes).hexdigest(),
              "python": {"implementation": sys.implementation.name, "version": sys.version.split()[0]},
              "source_roots": source_roots, "timeout_seconds": timeout, "tests": []}
    if environment:
        report["environment_mode"] = "explicit-directories"
        report["packages"] = environment["packages"]
    for test_id in test_ids:
        row = {"test_id": test_id}
        for side, sha in (("base", base_sha), ("head", head_sha)):
            with tempfile.TemporaryDirectory(prefix="patch-witness-") as directory:
                root = Path(directory)
                snapshot(repo, sha, root)
                target = root / test_file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(test_bytes)
                row[side] = run_side(root, test_file, test_id, source_roots, timeout, environment)
                if environment:
                    for path in [environment["python"], *environment["dependency_dirs"]]:
                        row[side] = json.loads(json.dumps(row[side]).replace(path, "<explicit-environment>"))
                # Keep public reports relocatable, including unittest exception messages.
                row[side] = json.loads(json.dumps(row[side]).replace(str(root), "<snapshot>").replace(str(repo), "<repository>"))
        row["witness"] = row["base"]["status"] == "assertion_failure" and row["head"]["status"] == "pass"
        if environment:
            identities = [row[side].get("record", {}).get("environment") for side in ("base", "head")]
            row["environment_match"] = identities[0] == identities[1] if all(identities) else None
            row["comparison_status"] = ("comparable" if row["environment_match"] else
                                        "environment_mismatch" if row["environment_match"] is False else
                                        "environment_unavailable")
            row["witness"] = row["witness"] and row["environment_match"] is True
        report["tests"].append(row)
    report["all_witnesses"] = bool(report["tests"]) and all(r["witness"] for r in report["tests"])
    return report
