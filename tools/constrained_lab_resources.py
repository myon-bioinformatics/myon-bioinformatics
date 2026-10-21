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

def verify_budget(root="/sys/fs/cgroup", *, max_memory_bytes=134217728,
                  max_cpu_fraction=1.0):
    """Fail closed if the current cgroup has weaker limits than requested.

    This verifies configured limits, not network isolation or actual usage.
    """
    if (type(max_memory_bytes) is not int or max_memory_bytes < 1 or
            type(max_cpu_fraction) not in (int, float) or
            not 0 < max_cpu_fraction <= 1):
        raise ValueError("invalid budget")
    root = Path(root)
    memory = (root / "memory.max").read_text(encoding="ascii").strip()
    cpu = (root / "cpu.max").read_text(encoding="ascii").strip().split()
    if len(cpu) != 2:
        raise ValueError("invalid cpu.max")
    quota, period = cpu
    if memory == "max" or int(memory) > max_memory_bytes:
        raise ValueError("memory cgroup limit exceeds budget")
    if quota == "max" or int(period) <= 0 or int(quota) / int(period) > max_cpu_fraction:
        raise ValueError("CPU cgroup quota exceeds budget")
    return {"memory_limit": int(memory), "cpu_quota": int(quota),
            "cpu_period": int(period), "cpu_fraction": int(quota) / int(period)}

def probe(argv, *, root="/sys/fs/cgroup", timeout=10, verify=False,
          max_memory_bytes=134217728, max_cpu_fraction=1.0):
    if not argv or any(type(v) is not str for v in argv) or timeout <= 0:
        raise ValueError("invalid argv or timeout")
    budget = (verify_budget(root, max_memory_bytes=max_memory_bytes,
                            max_cpu_fraction=max_cpu_fraction) if verify else None)
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
    return {"schema": "constrained-lab-resource/1", "budget_verified": budget is not None,
            "budget": budget, "returncode": rc,
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
    p.add_argument("--verify-budget", action="store_true")
    p.add_argument("--max-memory-bytes", type=int, default=134217728)
    p.add_argument("--max-cpu-fraction", type=float, default=1.0)
    p.add_argument("command", nargs="+")
    a = p.parse_args()
    try:
        result = probe(a.command, root=a.cgroup, timeout=a.timeout,
                       verify=a.verify_budget, max_memory_bytes=a.max_memory_bytes,
                       max_cpu_fraction=a.max_cpu_fraction)
    except (OSError, KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["timeout"] or result["returncode"] else 0

if __name__ == "__main__":
    sys.exit(main())
