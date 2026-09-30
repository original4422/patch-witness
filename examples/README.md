# Fixed historical cases

The commits and positive test IDs below were selected from their diffs before the first run. Neither test file was rewritten. Both use only the standard library. The candidate check was then paired with one existing pass-to-pass control from the same file.

| Repository | Base | Head | Positive test ID |
|---|---|---|---|
| repo-workbench | `734dc2ba940d862cd7e79694807fc2e4bb5cf7a5` | `36608419cfcf2e5f3a12d726deeaf3598d9c9445` | `GitHubTests.test_paginated_gh_contract_requests_exact_sha` |
| learn-codex | `34a571d2db7992b4db7df8b5928840c804fcfea6` | `93277559e7c80e47ccea0efdff4b85f1d7fe6e8c` | `ExerciseTests.test_acceptance_rejects_missing_tag_validation` |

- [repo-workbench.json](repo-workbench.json): the old GitHub API request lacks `filter=all`; the head request includes it. GitHub requests are mocked by the repository test. The control checks remote URL parsing.
- [learn-codex.json](learn-codex.json): the head test constructs a Taskboard candidate with tag validation removed, then invokes the snapshot's acceptance script. The base acceptance incorrectly returns zero; head rejects that candidate. This is a regression in the **acceptance checker**, not a fix to Taskboard itself. The control checks the reference implementation.
- [source-state.json](source-state.json): before/after HEAD, index byte hash, and combined tracked/unignored file-content hash. These are a specific run's preservation check, not an expected fingerprint of every clone. Ignored files were not inventoried.

Each report intentionally has `all_witnesses: false`: its second test is a P2P control. The first test has `witness: true`.

Run from patch-witness with existing sibling clones (the reproduction script does no fetch/checkout):

```sh
python3 examples/reproduce.py --workspace .. --output /tmp/witness-evidence
```

For new clones, obtain the public repositories first:

```sh
git clone https://github.com/original4422/repo-workbench.git ../repo-workbench
git clone https://github.com/original4422/learn-codex.git ../learn-codex
```

The reports contain no source machine paths. Run recorded 2026-09-30, macOS, CPython 3.14.7. Assertion messages may include subprocess timings, so reproduction does not require byte-identical JSON.

## Explicit dependency environment

The [serving deadline case](serving-deadlines.md) adds two fixed F2P tests and a cancellation P2P control using an existing httpx environment. Its separate JSON and preservation report keep these original stdlib examples unchanged.
