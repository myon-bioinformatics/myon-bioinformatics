# Measuring the minpy dependency search queue

2026-10-20 / Linux / CPython 3.12.14 / shared execution environment.
The baseline is `_collect()` on parent main `e5e7df176f570a7a8fa74b448912d11fca12ef04`.
Only the old function is loaded into the current, identical AST scanner and resolver, comparing the queue change.
`--baseline` takes a trusted local Git commit that is safe to execute.

The synthetic input has app import n branches, and each branch imports the same n leaves.
There are 2n+1 modules and n+n² references. Before measuring, all module fields, insertion order, edges,
diagnostics, and external dependencies in the search result, and byte equality of the generated bundle, are checked.

Time is the median of 3 runs with `perf_counter`. Memory is Python's peak allocation measured separately
with `tracemalloc`, not RSS. No speed thresholds are embedded in pytest.

| Measurement | Modules / references | Old / new median (s) | Old / new peak (bytes) |
|---|---:|---:|---:|
| Small search including the file system | 41 / 420 | 0.080298 / 0.077566 | 147618 / 144668 |
| Shared-dependency search including the file system | 301 / 22650 | 26.217374 / 26.698056 | 2701769 / 2509907 |
| Search only, fixed sources and resolution results | 201 / 10100 | 0.115822 / 0.078599 | 1216361 / 1133506 |
| Search only, fixed sources and resolution results | 701 / 122850 | 1.926878 / 1.163783 | 13509290 / 12501471 |

With 301 modules from file input, Python peak allocation fell by about 7.1%. The median time was about 1.8% slower,
so this does not demonstrate a speedup of the whole CLI.

In the large local measurement, about 39.6% less time and about 7.5% less Python peak allocation were observed.
For this fixed fixture, `--isolate-io` swaps out `_find` and `_read_source`, while real AST parsing, dependency search,
and edge recording run as-is. This does not mean a resolution cache was implemented in the product, nor that
resolution safety or behavior on file updates was verified with this stub. Those are checked by the normal regression tests.

The local improvement is not generalized to a speedup rate for the whole CLI. With small file input, a separate preliminary
measurement even gave about 8% slower values. Because shared-machine variance and I/O and resolution dominate, a speedup for
small inputs is not treated as demonstrated. The grounds for adoption are fewer duplicate queue entries and less front-element
shifting with large shared dependencies, the local time and memory improvement, and compatibility of output and diagnostics.

## Reproducing

```bash
# For a shallow checkout, fetch the baseline commit separately first.
python -S tests/benchmark_minpy_queue.py \
  --baseline e5e7df176f570a7a8fa74b448912d11fca12ef04 --sizes 20 150 --repeat 3
python -S tests/benchmark_minpy_queue.py \
  --baseline e5e7df176f570a7a8fa74b448912d11fca12ef04 --sizes 100 350 --repeat 3 --isolate-io
python -m pytest -q tests/test_minpy.py tests/test_minpy_dependency_queue.py
```

Raw data is in `small.jsonl` and `filesystem-medium.jsonl` (normal file input), and `component.jsonl` and
`component-large.jsonl` (local measurements). The 301-module file-input result was recorded from the same collector comparison in a preliminary script.
Values for the unfinished 701-module file-input measurement are not included.
