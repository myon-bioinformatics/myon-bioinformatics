#!/usr/bin/env python3
"""Opt-in, observe-only external executable audit for trusted local workloads.

Requires Linux strace installed separately; it is NOT a Python/Node dependency.
Never treat unavailable tracing as proof of a Python/Node-only workload.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

EXEC = re.compile(r'^\s*(?:(\d+)\s+)?(?:\[pid\s+(\d+)\]\s+)?(?:\d\d:\d\d:\d\d(?:\.\d+)?\s+)?execve(?:at)?\(')
QUOTED = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"')
DEFAULT_LIMIT = 1024 * 1024


def classify(path):
    base = os.path.basename(path).lower()
    if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?(?:\.exe)?', base) or base in ("node", "node.exe"):
        return "python-node"
    return "external"


def parse_trace(raw, max_events=1000):
    """Parse a bounded strace excerpt; record uncertainty rather than inventing args."""
    events = []
    for line in raw.splitlines():
        match = EXEC.match(line)
        if not match:
            continue
        tail = line[match.end():]
        value = QUOTED.match(tail)
        path = value.group(1) if value else None
        result = re.search(r'\)\s+=\s+(-?\d+)', tail)
        events.append({"pid": int(match.group(1) or match.group(2)) if
                       (match.group(1) or match.group(2)) else None,
                       "executable": path, "category": classify(path) if path else "unknown",
                       "exec_result": int(result.group(1)) if result else None,
                       "argv": None, "ppid": None,
                       "scope": "strace-execve-events-only"})
        if len(events) >= max_events:
            break
    return events


def audit(argv, timeout=30, max_trace_bytes=DEFAULT_LIMIT):
    if not argv or type(max_trace_bytes) is not int or max_trace_bytes < 1 or timeout <= 0:
        raise ValueError("invalid command, timeout or trace limit")
    tool = shutil.which("strace") if sys.platform.startswith("linux") else None
    if tool is None:
        return {"status": "unavailable", "reason": "strace unavailable",
                "coverage": "unknown", "events": [], "external_count": None}
    with tempfile.TemporaryDirectory() as folder:
        trace = Path(folder) / "exec.log"
        started = time.monotonic()
        try:
            result = subprocess.run([tool, "-f", "-qq", "-e", "trace=execve,execveat",
                                     "-s", "256", "-o", str(trace), "--", *argv],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout, check=False)
            status = "observed"
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            status, exit_code = "timeout", None
        data = trace.read_bytes() if trace.exists() else b""
        truncated = len(data) > max_trace_bytes
        events = parse_trace(data[:max_trace_bytes].decode("utf-8", "replace"))
        complete = status == "observed" and not truncated and len(events) < 1000
        return {"status": status, "exit": exit_code,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "trace_bytes_retained": min(len(data), max_trace_bytes),
                "trace_truncated": truncated, "coverage": "traced-process-tree" if complete else "partial-or-unknown",
                "events": events, "external_count": sum(e["category"] == "external" for e in events)
                if complete else None,
                "limitations": "No guarantee for detached descendants, tracing restrictions or process escape; "
                               "argv and PPID deliberately unmeasured; strace adds overhead."}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--max-trace-bytes", type=int, default=DEFAULT_LIMIT)
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        report = audit(command, args.timeout, args.max_trace_bytes)
    except (ValueError, OSError) as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "observed" and report["exit"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
