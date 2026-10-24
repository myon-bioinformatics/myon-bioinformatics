# Using stdlib algorithms: Issue #119, stage 2

2026-10-20. A scoped survey of 6 repositories whose product code and related tests were read closely.
It does not mean the inventory of all public repositories is complete. Related PRs were also checked, and existing features are not rebuilt.

## mains checked

| Repository | main SHA at survey time |
|---|---|
| myon-bioinformatics | `e5e7df176f570a7a8fa74b448912d11fca12ef04` |
| browser-test-kit | `4dcc5aa6b1e376dae9b6150a4782b9c19848a6c1` |
| cli_args | `2f24639400d6ee222b06c563284925b07e6bc7bf` |
| DRAgoWing | `360afbde2b55b54f205664eb4cf1a9cc17f9564a` |
| PythDRagoras | `b35ab6ee3cc5d71eed946c7292914a0fab0749ba` |
| NagoyAction | `1181e43508c9b2a9ad4cc4ce50f5ee38032bb678` |

## Existing features and overlap

| Implementation | Usage read closely | Decision |
|---|---|---|
| Parent `gh_ops.py::_lines_differ` | Counts changed lines with `SequenceMatcher.get_opcodes()`. Checking a PR body rewrite uses exact string match | Keep the existing diff display feature |
| browser-test-kit `scripts/gh_ops.py` | A distributed copy from the parent with the same line-diff logic | Don't create a separate source of truth. The whole-file hash differs from the parent, so no unconditional replacement |
| browser-test-kit `scripts/page_text.py::compare_rendered_text` | Matches normalized lines and classifies additions, deletions, and changes on both sides. ratio is a secondary metric | Don't reimplement DOM text comparison. Don't infer JS/CSS causes from the class names |
| cli_args `cli_args.py` | Opt-in suggestions for unknown long options via `get_close_matches`. Never auto-applied | PR #17 is done. Don't confuse `tests/test_stdlib_functions.py` and the stdlib-function examples in the docs with new product features |
| DRAgoWing `reports/build_reports.py` | Renders Markdown source line diffs to HTML with `HtmlDiff.make_table`. Additions/deletions are identified by path existence | A different role from `page_text`'s post-fetch text comparison. Don't merge into a single API |
| PythDRagoras `rule_search.py` | Generates candidates with `combinations` and keeps the top ones with `bisect.insort(..., key=...)`. The matrix path uses `heapq.nsmallest` and `chain` to avoid keeping result objects for every candidate | Selection order is already defined by rounded MCC, condition count, and expression string. No blanket replacement |
| PythDRagoras `littlebit_prune.py` / `littlebit_holdout.py` | Uses `heapq.nsmallest`, `chain` / `combinations` | Don't add separate helpers just for the survey |
| PythDRagoras `tests/test_littlebit_holdout_drift.py` | Limits failure output with `unified_diff` and `islice`; generates synthetic tests with `product` | A test helper; not counted as a new product feature |
| Parent `tools/minpy.py` | Parses imports and scopes with `ast`, explores local dependencies, and bundles them into a single file | The existing API's search queue is improved this time |
| NagoyAction `nagoyaction.py` | Selects and runs steps in the order declared in TOML. No dependency DAG is declared | Reordering by introducing `graphlib` does not fit the current contract |

The parent's `vendor-consumers.json` registers the distribution targets of `gh_ops.py`.
Distributed copies are distinguished from duplicate development of the source of truth, and their locks, pinned SHAs, and licenses are kept.
There is no reason to change `gh_ops` or other consumers for this `minpy` improvement.
At survey time, `minpy` was not registered in the parent catalog or consumer registry, and no new distribution registration is made.

## Adopted: FIFO queue for minpy dependency search

`_collect()`, the real product code path, repeatedly appended the same shared dependencies and package
parents to a list and took them from the front with `pop(0)`. Reading itself was limited to once by `seen`,
but allocating and removing duplicate queue entries and shifting list elements remained.

This is replaced with standard-library `collections.deque`'s `popleft()` and a set filled at first registration.
No custom generic queue class is made; the registration rule is gathered into a small `enqueue()` inside the
existing single-file `tools/minpy.py`, which can be copied and reused.

- Order of first discovery is kept. The set is used only for membership checks, never iterated for search order.
- Modules that error are not re-registered either. The old implementation also registered into `seen` before scanning.
- Every edge from a different importer to the same dependency is recorded. Queue deduplication never drops edges.
- Order, cycle detection, output, public API, CLI arguments, schema, and tool version are kept.
- Queue operations are O(V) in the number of registered modules V, and queue space is also O(V). The cost of
  per-reference resolution, AST parsing, and file lookup remains. The whole process is not claimed to be O(V).
- The old queue's front removal was O(Q) in the number of waiting entries Q. With many shared dependencies the
  queue grows up to the number of import references E. The new approach removes this element shifting and duplicate waiting.

Adopting `difflib`, `graphlib`, `ast`, `bisect`, `heapq`, or `itertools` is not a goal in itself.
The change is limited to the queue, where a concrete problem was confirmed. Similarity is never used as a
substitute for authorization, hash verification, or the semantic correctness of propositions.

## Verification

`tests/test_minpy_dependency_queue.py` checks shared dependencies, repeated imports, first-discovery order of
package parents, dependency edges, source retention, diagnostic order of shared syntax errors, and cycle rejection.
`python -S tools/minpy.py` and `python -S -c 'from tools.minpy import main; ...'` are run with
different `PYTHONHASHSEED` values to check JSON stdout, empty stdout on error, stderr,
exit codes 0/1, presence of output files, and matching generated bytes.
The CLI keeps its existing file/argument input and does not add stdin consumption.

- Diff check against the old implementation: on 30 graphs with seed 119 (including cycles and syntax errors), the generated bytes or full error text match
- Added regression tests: 5 passed
- Existing minpy: 98 passed
- Whole parent: 1278 passed / 63 skipped / 30 subtests passed (Python 3.12.14)
- `python -S tests/stdlib_runtime_smoke.py`: 3 tests OK
- The skips are existing gh/Docker and similar environment conditions; they are not treated as all executed.
- Measurement method and raw data: [performance record](evidence/stdlib-119/README.md)

## Remaining work

- Inventory of repositories not read this time, and further adoption based on concrete individual problems.
- Distribution to new consumers is decided after separately checking need, pinned SHA, and distribution contract.
- The `RecursionError` reproduced with a 1500-level synthetic dependency chain in minpy is a separate fix, below.
  It is not treated as resolved by the FIFO queue improvement.
- DAG checking with `graphlib` is a candidate, but existing multi-cycle diagnostics and ordering must be kept.
  A local measurement on a 500-level DAG gave about 0.49 ms vs. the existing 0.30 ms, so a blanket replacement is not adopted.
- Issue #119 as a whole stays open. After confirming CI success and an independent review, this combined PR is
  merged to main exactly once.

## Follow-up fix: cycle diagnostics for deep dependency graphs

After stage 2, a `RecursionError` was reproduced again on main `9951fbc23c8a4864417efad4cbff76322dcc7873` with a
1500-node DAG. Only the recursive calls in `_cycles()` are replaced with explicit DFS frames that hold the node under
exploration and an iterator over its unprocessed neighbors.

- Roots and neighbors are explored in name order, and cycles are recorded only from edges to ancestors under exploration.
- `_normalize_cycle()`, deduplication, final sort, public API, CLI, and output format are kept.
- The recursion limit is not raised. `_collect()`, consumers, workflows, and vendor pins are unchanged.
- Enumerating all simple cycles, and lifting depth limits of import recursion or AST parsing in the output, are out of scope.
  The complexity of existing cycle normalization is also unchanged.

Regression tests check DAGs / long cycles beyond the recursion limit, self-loops, disconnected components, edges to shared
targets and already-finished nodes, and the order of multiple cycles. All 512 directed graphs on 3 nodes are compared
with the old DFS, and on 30 graphs with seed 119 the public API's generated bytes or full diagnostics are compared too.
`python -S` scripts / one-liners are run with different `PYTHONHASHSEED` values to verify cycle rejection and explicit
allowance, stdout / stderr / exit codes, and matching output. Deep DAGs and cycles are also added to the existing stdlib
smoke test, continuing to check the Python 3.9 / 3.10 runtime contract.

This fix alone does not mark Issue #119 as done; the remaining work on unsurveyed repos and distribution stays.
