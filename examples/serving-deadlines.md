# Two deadline regressions with an existing dependency environment

On 2026-09-30, the two preselected deadline tests both produced a test-body assertion failure on base and passed on head. The preselected cancellation control passed on both revisions. Tests used a fake backend and controlled events; no model, browser, network service, or performance measurement ran.

| Input | Fixed value |
|---|---|
| Repository | [agent-serving-lab](https://github.com/original4422/agent-serving-lab) |
| Base | `c4c03b63ae56e0aef1aa00cc6b587cd27cd6bf8b` |
| Head | `93460c5afd055b19c10781210c4c5429ba895f88` |
| Head test file | `tests/test_deadlines.py` |
| Test SHA-256 | `0bb0b84d7feb74398fc9d1a32f9cc294ed14b66ad9c7fb48f723f490ef3531af` |
| Worker | Existing CPython 3.13.2, `-I -S -B`, macOS |
| Dependencies | Explicit existing Python 3.13 site-packages directory; httpx 0.28.1 |
| Source scope | `serving_lab`, including submodules |
| Measured worker.py SHA-256 | `48a82ce383355f8582afd273411178c2f2346b5131d6889c360b2aecc5918d45` |

Neither the test file nor dependency declarations were changed for the comparison. `pyproject.toml` and `uv.lock` have no diff between the selected revisions. The parent CLI ran with CPython 3.14.7; both sides ran with the explicitly selected 3.13.2 interpreter.

| `DeadlineTests` method | Base | Head | Witness |
|---|---|---|---|
| `test_deadline_crossed_during_selection_never_enters_backend` | `assertion_failure`: expired `r0` entered the backend | `pass` | true |
| `test_reverse_descendants_block_before_waiting_for_unrelated_stream` | `assertion_failure`: blocked descendant completed at 0.03 instead of 0.01 on the controlled clock | `pass` | true |
| `test_caller_cancellation_cleans_active_requests` | `pass` | `pass` | false |

[serving-deadlines.json](serving-deadlines.json) is the original report; `all_witnesses` is false because the third row is the P2P control. [serving-state.json](serving-state.json) records identical before/after repository HEAD, index bytes, tracked/unignored worktree files, and environment fingerprints. The existing environment's 316 files/symlinks were inventoried by path, mode, and content/link target; interpreter bytes were also hashed. Ignored repository files outside that environment were not inventoried.

The environment contained an editable installation of serving. Its `.pth` was not processed. All loaded `serving_lab` modules in each worker report have snapshot-relative paths and source hashes. Installed distribution metadata includes `agent-serving-lab`; that is environment inventory, not the imported source identity.

After this measurement, the parent gained an additional rule: differing or missing observed worker identities cannot produce a witness. Its targeted tests cover both cases. The original JSON predates `environment_match`/`comparison_status` and has been preserved without adding those fields. All three original pairs have equal recorded environment identities. The worker implementation above is unchanged; this parent rule was not used during the recorded run.

## Reproduce

Use an existing clone and prepared environment. The script does not fetch, checkout, install, or modify either environment or source repository. Supply the actual direct dependency directory for your Python version:

```sh
python3 -B examples/reproduce_serving.py \
  --repo ../agent-serving-lab \
  --python ../agent-serving-lab/.venv/bin/python \
  --dependency-dir ../agent-serving-lab/.venv/lib/python3.13/site-packages \
  --environment-root ../agent-serving-lab/.venv \
  --output /tmp/serving-witness-new
```

The output directory must be new. The script saves observed outcomes and preservation fingerprints, then exits zero only for the fixed two F2P / one P2P outcomes, matching observed worker identities for all three pairs, and unchanged source and environment. A missing dependency remains `import_error`; an unavailable scoped package/module remains `source_error`.

## Isolation counterexamples

Temporary-repository tests cover an executable `.pth` marker, `sitecustomize`, an editable path pointing at staged and dirty source, and a dependency available only through a `.pth`. No marker appears; the dirty source and index remain unchanged; the `.pth`-only dependency is unavailable. With a same-named installed package present, a missing base package or submodule yields `source_error` rather than using that copy. A caught missing-module exception or a package path extended outside the snapshot still prevents a witness.

Default-mode historical examples and their JSON remain unchanged. The explicit mode records observed interpreter and distribution metadata; the full environment-tree preservation check here belongs to this reproduction script.
