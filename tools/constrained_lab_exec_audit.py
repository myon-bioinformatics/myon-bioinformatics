#!/usr/bin/env python3
"""Opt-in, observe-only external executable audit for trusted local workloads.

Requires Linux strace installed separately; it is NOT a Python/Node dependency.
Never treat unavailable tracing as proof of a Python/Node-only workload.
"""
import argparse
import json
import os
import re
import shutil
import selectors
import signal
import subprocess
import sys
import time

EXEC = re.compile(r'^\s*(?:(\d+)\s+)?(?:\[pid\s+(\d+)\]\s+)?(?:\d\d:\d\d:\d\d(?:\.\d+)?\s+)?(execve(?:at)?)\(')
QUOTED = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"')
DEFAULT_LIMIT = 1024 * 1024


def classify(path):
    base = os.path.basename(path).lower()
    if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?(?:\.exe)?', base) or base in ("node", "node.exe"):
        return "python-node"
    return "external"



def _exec_result(tail):
    """Find the syscall's closing parenthesis, never a result-like argv string."""
    depth, quoted, escaped = 1, False, False
    for index, char in enumerate(tail):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                result = re.match(r'\s+=\s+(-?\d+)(?=\s|$)', tail[index + 1:])
                return int(result.group(1)) if result else None
    return None


def parse_trace(raw, max_events=1000):
    """Parse a bounded strace excerpt; record uncertainty rather than inventing args."""
    if type(max_events) is not int or max_events < 1:
        raise ValueError("invalid event limit")
    events = []
    for line in raw.splitlines():
        match = EXEC.match(line)
        if not match:
            continue
        tail = line[match.end():]
        result = _exec_result(tail)
        if match.group(3) == "execveat":
            tail = tail.partition(",")[2]
        value = QUOTED.match(tail)
        path = value.group(1) if value and value.group(1) else None
        path_complete = value is not None and not tail[value.end():].lstrip().startswith("...")
        category = classify(path) if path and "\\" not in path and path_complete else "unknown"
        events.append({"pid": int(match.group(1) or match.group(2)) if
                       (match.group(1) or match.group(2)) else None,
                       "executable": path, "category": category,
                       "exec_result": result,
                       "argv": None, "ppid": None,
                       "scope": "strace-execve-events-only"})
        if len(events) >= max_events:
            break
    return events


def _stop_process_group(proc):
    """Best-effort cleanup of the traced group, not escaped descendants."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        if proc.poll() is None:
            proc.kill()


def _capture_trace(tool, argv, timeout, limit):
    """Retain a bounded private pipe; never spool raw argv/environment to disk."""
    read_fd, write_fd = os.pipe()
    proc = None
    data = bytearray()
    status, truncated = "observed", False
    started = time.monotonic()
    try:
        # Keep tracer output separate from workload stderr, so the workload
        # cannot accidentally be interpreted as strace events.
        proc = subprocess.Popen(
            [tool, "-f", "-qq", "-e", "trace=execve,execveat", "-s", "256",
             "-o", "/proc/self/fd/%d" % write_fd, "--", *argv],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, pass_fds=(write_fd,), start_new_session=True)
        os.close(write_fd)
        write_fd = None
        os.set_blocking(read_fd, False)
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            trace_open = True
            while True:
                if trace_open and selector.select(0.01):
                    chunk = os.read(read_fd, 65536)
                    if not chunk:
                        selector.unregister(read_fd)
                        trace_open = False
                    else:
                        remaining = limit - len(data)
                        data.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            truncated, status = True, "trace_limit"
                            break
                if proc.poll() is not None and not trace_open:
                    break
                if time.monotonic() - started >= timeout:
                    status = "timeout"
                    break
                if not trace_open:
                    time.sleep(0.01)
        if status != "observed":
            _stop_process_group(proc)
        proc.wait()
        return bytes(data), status, proc.returncode, truncated
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if proc is not None and proc.poll() is None:
            _stop_process_group(proc)
            proc.wait()


def audit(argv, timeout=30, max_trace_bytes=DEFAULT_LIMIT):
    if (not isinstance(argv, (list, tuple)) or not argv or
        any(type(arg) is not str for arg in argv) or not argv[0] or
        type(max_trace_bytes) is not int or max_trace_bytes < 1 or
        type(timeout) not in (int, float) or not 0 < timeout < float("inf")):
        raise ValueError("invalid command, timeout or trace limit")
    tool = shutil.which("strace") if sys.platform.startswith("linux") else None
    if tool is None:
        return {"status": "unavailable", "reason": "strace unavailable",
                "coverage": "unknown", "events": [], "external_count": None,
                "observed_external_count": 0}
    started = time.monotonic()
    data, status, exit_code, truncated = _capture_trace(tool, argv, timeout, max_trace_bytes)
    events = parse_trace(data.decode("utf-8", "replace"), max_events=1001)
    events_truncated = len(events) > 1000
    events = events[:1000]
    if status == "observed" and not events:
        status = "tracing_error"
    # Counts describe retained events only. Even a normal strace exit cannot
    # establish complete dependency coverage or a Python/Node-only workload.
    return {"status": status, "exit": exit_code,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "trace_bytes_retained": len(data), "trace_truncated": truncated,
            "events_truncated": events_truncated,
            "coverage": "partial-or-unknown" if events else "unknown",
            "events": events, "external_count": None,
            "observed_external_count": sum(e["category"] == "external" for e in events),
            "unknown_event_count": sum(e["category"] == "unknown" or
                                       e["exec_result"] is None for e in events),
            "limitations": "Retained exec attempts only, including failed attempts. No guarantee for "
                           "detached descendants, tracing restrictions or process escape; argv and PPID "
                           "deliberately unmeasured; strace adds overhead."}


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
