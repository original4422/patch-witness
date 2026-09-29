import argparse
import json
import subprocess

from .core import compare


def main():
    parser = argparse.ArgumentParser(description="Run the same head unittest against base and head source snapshots.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--test-file", required=True)
    parser.add_argument("--test-id", action="append", required=True, help="Class.method within the test file; repeat for multiple tests")
    parser.add_argument("--source-root", action="append", default=[], help="Explicit repository-relative import directory, e.g. src")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    try:
        report = compare(args.repo, args.base, args.head, args.test_file, args.test_id, args.source_root, args.timeout)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"patch-witness: {exc}\n")
    print(json.dumps(report, indent=2))
    return 0 if report["all_witnesses"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
