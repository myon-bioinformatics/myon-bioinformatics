#!/usr/bin/env python3
"""Linux cgroup-v2 constrained execution probe, stdlib only.

The *caller* must isolate the process from network and supply cgroup quotas.
This module measures actual cgroup counters; it does not create a sandbox.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

def read_cgroup(root="/sys/fs/cgroup"):
    root = Path(root)
    def number(name):
        value = (root / name).read_text(encoding="ascii").strip()
        return None if value == "max" else int(value)
    stat = {}
    for line in (root / "cpu.stat").read_text(encoding="ascii").splitlines():
        key, value = line.split()
        stat[key] = int(value)
    return {"memory_current": number("memory.current"),
            "memory_peak": number("memory.peak"),
            "memory_max": number("memory.max"),
            "cpu_usage_usec": stat["usage_usec"],
            "cpu_throttled_usec": stat.get("throttled_usec", 0)}

def probe(argv, *, root="/sys/fs/cgroup", timeout=10):
    if not argv or any(type(v) is not str for v in argv) or timeout <= 0:
        raise ValueError("invalid argv or timeout")
    before = read_cgroup(root)
    started = time.monotonic()
    timed_out = False
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=timeout, check=False)
        rc = done.returncode
    except subprocess.TimeoutExpired:
        timed_out, rc = True, None
    after = read_cgroup(root)
    return {"schema": "constrained-lab-resource/1", "returncode": rc,
            "timeout": timed_out, "wall_seconds": round(time.monotonic()-started, 6),
            "cpu_seconds": round((after["cpu_usage_usec"]-before["cpu_usage_usec"])/1e6, 6),
            "cpu_throttled_seconds": round((after["cpu_throttled_usec"]-before["cpu_throttled_usec"])/1e6, 6),
            "memory_current_before": before["memory_current"],
            "memory_current_after": after["memory_current"],
            "memory_peak_cgroup": after["memory_peak"],
            "memory_max": after["memory_max"],
            "measurement_scope": "entire-cgroup-not-per-process"}

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--cgroup", default="/sys/fs/cgroup")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("command", nargs="+")
    a = p.parse_args()
    try:
        result = probe(a.command, root=a.cgroup, timeout=a.timeout)
    except (OSError, KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["timeout"] or result["returncode"] else 0

if __name__ == "__main__":
    sys.exit(main())
