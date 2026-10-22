#!/usr/bin/env python3
"""Offline staged-source contract probes for NagoyAction and Ironmate.

Uses *real* staged modules, never substitutes a mock for a reported OSS run.
Sources are pinned by SHA256. No network calls or installs are performed here.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import time
import tracemalloc

def load_pinned(path, expected):
    path = Path(path).resolve()
    if not path.is_file() or len(expected) != 64:
        raise ValueError("missing source or invalid digest")
    try:
        int(expected, 16)
    except ValueError:
        raise ValueError("invalid digest")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("source SHA256 mismatch")
    name = "_constrained_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module

def nagoyaction(module, seed=7, n=40, rounds=10):
    items = list(range(n))
    start = time.perf_counter()
    tracemalloc.start()
    try:
        shards = module.split_shards(items, rounds, seed)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    elapsed = time.perf_counter() - start
    flattened = [x for shard in shards for x in shard]
    return {"project": "NagoyAction", "pass": len(shards) == rounds and
            len(flattened) == n and sorted(flattened) == items,
            "cases": n, "rounds": rounds, "wall_seconds": round(elapsed, 6),
            "python_alloc_peak_bytes": peak, "shard_sizes": [len(x) for x in shards]}

def ironmate(module, width=32, height=32):
    rgba = bytes((x * 7 + y * 13 + c * 37) % 256
                 for y in range(height) for x in range(width) for c in range(4))
    start = time.perf_counter()
    tracemalloc.start()
    try:
        png = module.encode_png(width, height, rgba)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    elapsed = time.perf_counter() - start
    valid = (png.startswith(b"\\x89PNG\\r\\n\\x1a\\n".decode("unicode_escape").encode("latin1"))
             and png == module.encode_png(width, height, rgba))
    return {"project": "Ironmate", "pass": valid, "width": width, "height": height,
            "png_sha256": hashlib.sha256(png).hexdigest(), "png_bytes": len(png),
            "wall_seconds": round(elapsed, 6), "python_alloc_peak_bytes": peak}

def run(project, source, digest, dependencies=()):
    source = Path(source).resolve()
    for dependency, sha in dependencies:
        if hashlib.sha256(Path(dependency).read_bytes()).hexdigest() != sha:
            raise ValueError("dependency SHA256 mismatch")
    sys.path.insert(0, str(source.parent))
    try:
        module = load_pinned(source, digest)
        if project == "nagoyaction":
            return nagoyaction(module)
        if project == "ironmate":
            return ironmate(module)
        raise ValueError("unknown project")
    finally:
        sys.path.pop(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", choices=("nagoyaction", "ironmate"))
    parser.add_argument("source")
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--dependency", action="append", default=[],
                        help="PATH:SHA256 (repeatable)")
    args = parser.parse_args()
    try:
        deps = [item.rsplit(":", 1) for item in args.dependency]
        if any(len(pair) != 2 for pair in deps):
            raise ValueError("invalid dependency")
        result = run(args.project, args.source, args.sha256, deps)
    except (OSError, ValueError, ImportError, AttributeError) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1

if __name__ == "__main__":
    sys.exit(main())
