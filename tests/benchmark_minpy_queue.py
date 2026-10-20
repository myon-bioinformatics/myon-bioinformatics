"""Compare collectors against trusted local Git source (no network or dependencies).

python -S tests/benchmark_minpy_queue.py --baseline <commit> --sizes 20
The old collector is executed locally. Timings are observations, not CI limits.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
import gc
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import minpy


def legacy_collector(ref):
    root = Path(minpy.__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"],
        cwd=root, text=True).strip()
    source = subprocess.check_output(
        ["git", "show", commit + ":tools/minpy.py"], cwd=root, text=True)
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "_collect")
    namespace = dict(vars(minpy))
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<baseline-collector>", "exec"), namespace)
    return namespace["_collect"]


def signature(graph):
    # Include all module fields and their discovery order, not only final names.
    return ([(name, vars(module)) for name, module in graph.modules.items()],
            graph.edges, graph.problems, graph.library_external,
            graph.script_external, graph.third_party)


def measure(collector, kwargs, repeats):
    samples = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        graph = collector(**kwargs)
        samples.append(time.perf_counter() - start)
        del graph
    gc.collect()
    tracemalloc.start()
    try:
        graph = collector(**kwargs)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return dict(median_seconds=statistics.median(samples), samples_seconds=samples, peak_bytes=peak)


def benchmark(folder, size, baseline, repeats, isolate_io=False):
    folder.mkdir()
    (folder / "app.py").write_text("".join(f"import branch{i:04}\n" for i in range(size)), encoding="utf-8")
    shared = "".join(f"import leaf{i:04}\n" for i in range(size))
    for i in range(size):
        (folder / f"branch{i:04}.py").write_text(shared, encoding="utf-8")
        (folder / f"leaf{i:04}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
    kwargs = dict(root=folder, entry_name="app", entry_path=folder / "app.py",
                  search_roots=[folder], third_roots=[], allow=set())
    # Optional component measurement: keep AST scanning and collector logic real,
    # but supply source reads and module resolution from this fixed fixture.
    # This is not an end-to-end speed estimate or a production resolver cache.
    sources = {p: p.read_text(encoding="utf-8") for p in folder.glob("*.py")}
    modules = {p.stem: ("module", p, folder) for p in sources}
    overrides = {"_find": lambda name, roots: modules.get(name),
                 "_read_source": lambda path, display: sources[path]}
    with ExitStack() as stack:
        if isolate_io:
            stack.enter_context(patch.dict(vars(minpy), overrides))
            stack.enter_context(patch.dict(baseline.__globals__, overrides))
        assert signature(baseline(**kwargs)) == signature(minpy._collect(**kwargs))
        bundle_args = dict(root=folder, entry="app.py", repository="example/queue",
                           commit="a" * 40, skip_head_check=True)
        current = minpy.bundle(**bundle_args)
        collector = minpy._collect
        try:
            minpy._collect = baseline
            assert minpy.bundle(**bundle_args) == current
        finally:
            minpy._collect = collector
        return dict(mode="cached-source-and-resolution" if isolate_io else "filesystem",
                    branches_and_shared_leaves=size, modules=2 * size + 1,
                    references=size + size * size, legacy=measure(baseline, kwargs, repeats),
                    deque=measure(minpy._collect, kwargs, repeats))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="trusted local Git commit with the old collector")
    parser.add_argument("--sizes", type=int, nargs="+", default=[20])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--isolate-io", action="store_true", help="profile the collector with fixture-backed source/resolver stubs; not end-to-end timing")
    args = parser.parse_args()
    if args.repeat < 1 or any(n < 1 for n in args.sizes):
        parser.error("sizes and repeat must be positive")
    baseline = legacy_collector(args.baseline)
    with tempfile.TemporaryDirectory() as tmp:
        for index, size in enumerate(args.sizes):
            print(json.dumps(benchmark(Path(tmp) / str(index), size, baseline, args.repeat, args.isolate_io)), flush=True)


if __name__ == "__main__":
    main()
