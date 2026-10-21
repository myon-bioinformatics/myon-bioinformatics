#!/usr/bin/env python3
"""Compare sequential and concurrent constrained-lab campaigns on identical cases.

This is an orchestration benchmark, not a hard CPU/RAM/network isolation test.
"""
import argparse
import json
import sys
import time
from pathlib import Path
try:
    from tools import constrained_lab
except ModuleNotFoundError:
    import constrained_lab


def compare(cases, *, rounds=1, seed=7, workers=2, timeout=10):
    if type(workers) is not int or workers < 2:
        raise ValueError("workers must be >= 2")
    reports = []
    for count in (1, workers):
        start = time.perf_counter()
        result = constrained_lab.campaign(
            cases, rounds=rounds, seed=seed, workers=count,
            timeout=timeout, keep_going=True)
        elapsed = time.perf_counter() - start
        reports.append({"workers": count, "wall_seconds": round(elapsed, 6),
                        "campaign": result})
    left, right = (r["campaign"] for r in reports)
    def identities(campaign):
        return sorted((row["id"], row["status"], row.get("exit"),
                       row.get("stdout_sha256"), row.get("stderr_sha256"))
                      for part in campaign["reports"] for row in part["results"])
    equivalent = identities(left) == identities(right)
    return {"schema": "constrained-lab-comparison/1",
            "seed": seed, "rounds": rounds, "equivalent": equivalent,
            "speedup": round(reports[0]["wall_seconds"] /
                             max(reports[1]["wall_seconds"], 0.000001), 4),
            "runs": reports}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("cases")
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--timeout", type=float, default=10)
    args = p.parse_args(argv)
    try:
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        report = compare(cases, rounds=args.rounds, seed=args.seed,
                         workers=args.workers, timeout=args.timeout)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    ok = report["equivalent"] and all(
        run["campaign"]["rounds_completed"] == run["campaign"]["rounds_passed"]
        for run in report["runs"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
