#!/usr/bin/env python3
"""Run a bounded offline fixture comparison against staged, immutable OSS sources.

Source snapshots must be staged ahead of time; no network or package install.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

def compare_fixture(spec, root):
    if not isinstance(spec, dict) or not {"name", "argv", "expected_exit", "sha256"} <= set(spec) or set(spec) - {"name", "argv", "expected_exit", "sha256", "sources"}:
        raise ValueError("invalid fixture")
    if type(spec["name"]) is not str or not spec["name"]:
        raise ValueError("invalid name")
    if type(spec["expected_exit"]) is not int:
        raise ValueError("invalid expected exit")
    if not isinstance(spec["argv"], list) or not spec["argv"] or any(type(x) is not str or not x for x in spec["argv"]):
        raise ValueError("invalid argv")
    if not isinstance(spec["sha256"], str) or len(spec["sha256"]) != 64:
        raise ValueError("invalid sha256")
    try:
        int(spec["sha256"], 16)
    except ValueError:
        raise ValueError("invalid sha256")
    sources = spec.get("sources", {})
    if not isinstance(sources, dict):
        raise ValueError("invalid sources")
    root_path = Path(root).resolve()
    for relative, expected in sources.items():
        if type(relative) is not str or type(expected) is not str or len(expected) != 64:
            raise ValueError("invalid source entry")
        if any(part in ("", ".", "..") for part in relative.split("/")) or Path(relative).is_absolute():
            raise ValueError("invalid source path")
        try:
            int(expected, 16)
        except ValueError:
            raise ValueError("invalid source digest")
        path = root_path.joinpath(*relative.split("/"))
        if not path.resolve().is_relative_to(root_path) or not path.is_file() or path.is_symlink():
            raise ValueError("source escapes root or is missing")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("source SHA-256 mismatch")
    start = time.perf_counter()
    done = subprocess.run(spec["argv"], cwd=root, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=10, check=False)
    digest = hashlib.sha256(done.stdout).hexdigest()
    return {"name": spec["name"], "exit": done.returncode,
            "output_sha256": digest, "expected_sha256": spec["sha256"],
            "pass": done.returncode == spec["expected_exit"] and digest == spec["sha256"],
            "wall_seconds": round(time.perf_counter()-start, 6)}

def run(manifest, root):
    if not isinstance(manifest, list):
        raise ValueError("manifest must be list")
    results = [compare_fixture(item, root) for item in manifest]
    return {"schema": "constrained-lab-fixture/1", "results": results,
            "passed": sum(item["pass"] for item in results),
            "total": len(results)}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest")
    p.add_argument("--root", default=".")
    args = p.parse_args()
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        report = run(manifest, args.root)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] == report["total"] else 1

if __name__ == "__main__":
    sys.exit(main())
