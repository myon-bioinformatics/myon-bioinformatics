#!/usr/bin/env python3
"""Offline integration of staged NagoyAction/Ironmate code and resource probe.

No network access is performed; sources must be staged and SHA-pinned.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

def run_pair(root, nagoya, ironmate, *, rounds=10, seed=7, workers=2):
    from tools import constrained_lab_bench
    root = Path(root).resolve()
    sources = [Path(nagoya).resolve(), Path(ironmate).resolve()]
    if any(not p.is_file() or not p.is_relative_to(root) for p in sources):
        raise ValueError("staged sources must be within root")
    if not 1 <= rounds <= 100 or workers < 2:
        raise ValueError("invalid rounds/workers")
    manifest = []
    for label, source in zip(("nagoyaction", "ironmate"), sources):
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        command = [sys.executable, "-S", "-m", "tools.constrained_lab_oss",
                   label, str(source), "--sha256", digest, "--structured-output"]
        manifest.append({"id": label, "argv": command, "expected_exit": 0,
                         "stdout_contract": "result-measurements/1"})
    started = time.perf_counter()
    report = constrained_lab_bench.compare(manifest, rounds=rounds, seed=seed, workers=workers)
    report["source_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    report["wall_total_seconds"] = round(time.perf_counter()-started, 6)
    report["staged_real_sources"] = True
    return report

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--nagoyaction", required=True)
    p.add_argument("--ironmate", required=True)
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--workers", type=int, default=2)
    a = p.parse_args()
    try:
        result = run_pair(a.root, a.nagoyaction, a.ironmate,
                          rounds=a.rounds, seed=a.seed, workers=a.workers)
    except (OSError, ValueError) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["equivalent"] and all(
        run["campaign"]["rounds_passed"] == run["campaign"]["rounds_planned"]
        for run in result["runs"]) else 1

if __name__ == "__main__":
    sys.exit(main())
