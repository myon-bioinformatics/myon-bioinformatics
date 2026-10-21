#!/usr/bin/env python3
"""Offline stdlib-only seeded sharding runner for trusted local commands.

Hard resource limits and network isolation must be supplied by the parent OS.
"""
import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

def plan(cases, rounds, seed):
    if type(rounds) is not int or rounds < 1 or type(seed) is not int:
        raise ValueError("invalid rounds/seed")
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    order = list(cases)
    random.Random(seed).shuffle(order)
    return [order[i::rounds] for i in range(rounds)]

def execute(case, round_number, seed, timeout):
    digest = hashlib.sha256(("%s:%s:%s" % (seed, round_number, case["id"])).encode()).digest()
    trial_seed = int.from_bytes(digest[:8], "big")
    argv = [x.replace("{seed}", str(trial_seed)).replace("{round}", str(round_number))
            for x in case["argv"]]
    started = time.monotonic()
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, check=False)
        return {"id": case["id"], "status": "pass" if result.returncode == case["expected_exit"] else "fail",
                "exit": result.returncode, "elapsed": round(time.monotonic()-started, 6),
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr).hexdigest()}
    except subprocess.TimeoutExpired:
        return {"id": case["id"], "status": "timeout", "elapsed": round(time.monotonic()-started, 6)}
    except OSError as exc:
        return {"id": case["id"], "status": "spawn_error", "error": type(exc).__name__}

def campaign(cases, rounds=10, seed=7, workers=2, timeout=10, keep_going=False):
    if type(workers) is not int or workers < 1 or timeout <= 0:
        raise ValueError("invalid workers/timeout")
    for case in cases:
        if (not isinstance(case, dict) or type(case.get("id")) is not str or
            not case["id"] or type(case.get("expected_exit")) is not int or
            not isinstance(case.get("argv"), list) or not case["argv"] or
            any(type(x) is not str or not x for x in case["argv"])):
            raise ValueError("invalid case")
    result = []
    for n, shard in enumerate(plan(cases, rounds, seed), 1):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(lambda c: execute(c, n, seed, timeout), shard))
        passed = all(r["status"] == "pass" for r in rows)
        result.append({"round": n, "passed": passed, "results": rows})
        if not passed and not keep_going:
            break
    return {"schema": "constrained-lab/1", "seed": seed, "rounds_planned": rounds,
            "rounds_completed": len(result), "rounds_passed": sum(r["passed"] for r in result),
            "cases_planned": len(cases), "cases_completed": sum(len(r["results"]) for r in result),
            "reports": result}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("cases")
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--keep-going", action="store_true")
    a = p.parse_args()
    try:
        cases = json.loads(Path(a.cases).read_text(encoding="utf-8"))
        if not isinstance(cases, list):
            raise ValueError("cases must be a JSON list")
        report = campaign(cases, a.rounds, a.seed, a.workers, a.timeout, a.keep_going)
    except (OSError, ValueError, KeyError) as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["rounds_completed"] == report["rounds_passed"] == report["rounds_planned"] else 1

if __name__ == "__main__":
    sys.exit(main())
