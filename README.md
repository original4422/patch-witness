# patch-witness

**Does this regression test actually fail before the fix?** Run the same committed head test against base and head source snapshots, and get a per-test JSON answer.

[中文](README.zh.md) · [Real examples](examples/README.md) · [CI](https://github.com/original4422/patch-witness/actions)

Useful when reviewing an agent's patch: a green CI run shows that tests pass now; a witness records that one specific test also detected a difference before the fix. Python standard library only, no model calls.

```text
head test ─── base source ─── assertion_failure
          └── head source ─── pass                 → witness: true
```

## Run

Python 3.10+, Git, Linux or macOS. Clone and run without installing anything:

```sh
git clone https://github.com/original4422/patch-witness.git
cd patch-witness
python3 -m patch_witness \
  --repo ../repo-workbench \
  --base 3660841^ --head 3660841 \
  --test-file tests/test_workbench.py \
  --test-id GitHubTests.test_paginated_gh_contract_requests_exact_sha
```

The target repository and both commits must already exist locally. Repeat `--test-id` to compare several tests in the same file. Add `--source-root src` for a `src/` layout. Set `--timeout 30` for a per-test, per-side worker deadline. Optional installation: `python3 -m pip install .` provides the `patch-witness` command.

JSON includes full commit SHAs, the head test file's SHA-256, interpreter version, explicit source roots, collected/run counts, events, snapshot-relative imports, and each side's status. Exit codes: **0** all requested tests are witnesses; **1** at least one is not; **2** invocation/snapshot preparation failed.

## What counts

Only **test-body assertion failure → pass** produces `witness: true`. Each test runs alone, in fresh source snapshots on both sides. A passing result requires exactly one collected and executed test, a completed result record, and a successful worker exit.

| Side status | Meaning |
|---|---|
| `assertion_failure` | An `AssertionError` occurred during the test body, including a failing subtest |
| `pass` | unittest completed the selected test successfully |
| `skip`, `expected_failure`, `unexpected_success` | Explicit unittest outcomes, never witnesses |
| `import_error`, `collection_error` | Test module/dependency import or test selection failed |
| `zero_tests`, `multiple_tests` | The ID selected something other than exactly one test |
| `setup_error`, `teardown_error`, `cleanup_error`, `test_error` | Failure outside the body assertion path; overrides an earlier assertion |
| `timeout`, `worker_exit` | Deadline reached or worker exited abnormally |
| `missing_result`, `invalid_result` | No complete usable event record; exit code zero alone is insufficient |

The parent process checks the worker's record and computes the witness. The worker uses unittest callbacks and phase hooks to distinguish body assertions from fixtures. Source imports start from the snapshot, test directory, and explicit source roots; `-I -S` excludes inherited `PYTHONPATH` and site packages. Imported snapshot modules are listed in the report. Child processes launched by a test remain that test's responsibility for import selection; the tool cleans its process group when the worker ends or times out.

## Execution contract

The CLI reads Git objects with `ls-tree`/`cat-file` into temporary directories. It overlays **only the named head test file** on both revisions. Other fixtures and helpers remain at their respective revisions. There is no checkout, reset, fetch, or write to the target repository. Regular files are supported; symlinks and submodules are rejected. Tests use their snapshot as the working directory.

Run trusted test and source code. Temporary snapshots and a separate verdict process are not a security sandbox or an anti-tampering boundary against malicious Python. This release supports stdlib-only unittest environments; it does not install dependencies or adapt pytest projects. A witness describes this test's observed transition; review the assertion to decide whether it represents the intended fix.

## Verified examples

Two cases were selected before running them, with no test rewrites:

| Public fix | Observed transition | Passing control |
|---|---|---|
| [repo-workbench `3660841`](https://github.com/original4422/repo-workbench/commit/36608419cfcf2e5f3a12d726deeaf3598d9c9445): request all GitHub check runs | Missing `filter=all` assertion → pass | URL parsing: pass → pass |
| [learn-codex `9327755`](https://github.com/original4422/learn-codex/commit/93277559e7c80e47ccea0efdff4b85f1d7fe6e8c): strengthen Taskboard acceptance | Old acceptance accepted an invalid candidate → new acceptance rejects it | Reference implementation: pass → pass |

Both target repositories retained identical HEAD, index bytes, and tracked/unignored worktree file contents. [JSON evidence and reproduction](examples/README.md) include the two controls (`witness: false`). The local run used CPython 3.14.7 on macOS; CI checks Linux and macOS.

## Development

```sh
python3 -m unittest discover -s tests -v
```

26 tests exercise real temporary Git repositories, dirty staged/unstaged worktrees, snapshot imports, F2P/P2P, fixture errors, skips, missing dependencies, zero collection, process exits, timeout descendant cleanup, and parent result checks.

## Related tools

- [SWE-benchify](https://github.com/Red-Hat-AI-Innovation-Team/SWE-benchify) mines PRs into benchmark instances and validates F2P/P2P through its environment/agent pipeline (`swebenchify validate`).
- [Probity](https://github.com/nizos/probity) enforces TDD and other rules through agent hooks before actions happen.
- [red-green-mode](https://github.com/hidevinliu/red-green-mode) scans test changes and uses `rgm_mutation.py check-pair` to test a verifier against local mutations.

patch-witness focuses on explicit existing local Git revisions and one head unittest, after the patch is written. These official entry points were reviewed before building the two-case prototype; no broad uniqueness claim is made.

MIT license.
