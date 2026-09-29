"""Reproduce the two fixed public cases from existing local clones."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from patch_witness.core import compare, git

CASES = (
    ("repo-workbench", "36608419cfcf2e5f3a12d726deeaf3598d9c9445", "tests/test_workbench.py", [
        "GitHubTests.test_paginated_gh_contract_requests_exact_sha", "GitHubTests.test_slug_does_not_leak_token"]),
    ("learn-codex", "93277559e7c80e47ccea0efdff4b85f1d7fe6e8c", "tests/test_exercise.py", [
        "ExerciseTests.test_acceptance_rejects_missing_tag_validation", "ExerciseTests.test_reference_passes_blackbox_acceptance"]),
)


def fingerprint(repo):
    index = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip())
    names = set(git(repo, "ls-files", "-z").split(b"\0")) | set(git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
    digest = hashlib.sha256()
    count = 0
    for name in sorted(names - {b""}):
        path = repo / os.fsdecode(name)
        digest.update(name + b"\0")
        if path.is_symlink():
            digest.update(b"link:" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(b"file:" + hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b"absent")
        count += 1
    return {"head": git(repo, "rev-parse", "HEAD").decode().strip(),
            "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
            "tracked_and_unignored_files": count, "worktree_sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True, help="Parent of existing repo-workbench and learn-codex clones")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    verification = []
    for name, head, test_file, tests in CASES:
        repo = (args.workspace / name).resolve()
        before = fingerprint(repo)
        report = compare(repo, head + "^", head, test_file, tests)
        after = fingerprint(repo)
        assert before == after, f"{name}: source state changed"
        assert report["tests"][0]["witness"], report
        assert [report["tests"][1][s]["status"] for s in ("base", "head")] == ["pass", "pass"], report
        (args.output / (name + ".json")).write_text(json.dumps(report, indent=2) + "\n")
        verification.append({"repository": name, "unchanged": before == after, "before": before, "after": after})
    (args.output / "source-state.json").write_text(json.dumps(verification, indent=2) + "\n")
    print("Two assertion-failure→pass witnesses; two pass→pass controls; both source states unchanged.")


if __name__ == "__main__":
    main()
