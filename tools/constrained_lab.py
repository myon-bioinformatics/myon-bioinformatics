#!/usr/bin/env python3
"""Offline stdlib-only seeded sharding runner for trusted local commands.

Hard resource limits and network isolation must be supplied by the parent OS.
"""
import argparse
import hashlib
import json
import os
import signal
import threading
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_OUTPUT_LIMIT = 1024 * 1024  # Each of stdout and stderr.
_POLL_SECONDS = 0.01

def plan(cases, rounds, seed):
    if type(rounds) is not int or rounds < 1 or type(seed) is not int:
        raise ValueError("invalid rounds/seed")
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    if not ids:
        raise ValueError("cases must not be empty")
    if rounds > len(ids):
        raise ValueError("rounds must not exceed the number of cases")
    order = list(cases)
    random.Random(seed).shuffle(order)
    return [order[i::rounds] for i in range(rounds)]


def _linux_rss_bytes(pid):
    """Current RSS of one direct child; missing readings remain unmeasured."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        for line in Path("/proc/%d/status" % pid).read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class _ActiveRSS:
    """Sampled sum of direct-child current RSS; not the host/cgroup peak."""
    def __init__(self):
        self._lock = threading.Lock()
        self._pids = set()
        self.peak = None
        self.samples = 0
        self._stop = threading.Event()

    def register(self, pid):
        with self._lock:
            self._pids.add(pid)
        self.sample()

    def unregister(self, pid):
        self.sample()
        with self._lock:
            self._pids.discard(pid)

    def sample(self):
        with self._lock:
            pids = tuple(self._pids)
        if not pids:
            return
        values = [_linux_rss_bytes(pid) for pid in pids]
        if all(v is not None for v in values):
            with self._lock:
                self.peak = max(self.peak or 0, sum(values))
                self.samples += 1

    def run(self):
        while not self._stop.wait(_POLL_SECONDS):
            self.sample()

    def stop(self):
        self._stop.set()


def _nonblocking_pipe_reader(pipe):
    """Return a reader that never waits for a writer, including on Windows."""
    fd = pipe.fileno()
    if os.name != "nt":
        os.set_blocking(fd, False)
        return lambda: os.read(fd, 65536)

    # select() cannot watch Windows pipes. PeekNamedPipe also works on the
    # anonymous pipes created by Popen, including on Python before 3.12.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD)]
    peek.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(fd)

    def read():
        available = wintypes.DWORD()
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error == 109:  # ERROR_BROKEN_PIPE: all writers have closed.
                return b""
            raise ctypes.WinError(error)
        if not available.value:
            raise BlockingIOError()
        # This thread is the sole reader, so these bytes cannot be consumed
        # between the peek and read by another reader.
        return os.read(fd, min(available.value, 65536))

    return read


def _read_bounded(pipe, limit, overflow, cancel):
    """Read both pipes concurrently, retaining no more than limit bytes each."""
    kept = bytearray()
    try:
        read = _nonblocking_pipe_reader(pipe)
        while not cancel.is_set():
            try:
                chunk = read()
            except BlockingIOError:
                cancel.wait(_POLL_SECONDS)
                continue
            if not chunk:
                break
            remaining = limit - len(kept)
            kept.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()
                break
    finally:
        pipe.close()
    return bytes(kept)


def _stop_process(proc):
    """Kill the POSIX worker group or the direct process on Windows."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _reap(proc):
    """Reap a worker with OS wait4 usage, not direct-only/tree-wide accounting."""
    if os.name == "posix" and hasattr(os, "wait4"):
        try:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        except ChildProcessError:
            return proc.poll() is not None, None
        if pid == 0:
            return False, None
        proc.returncode = os.waitstatus_to_exitcode(status)
        return True, usage
    return proc.poll() is not None, None


def _resource_values(usage):
    user = round(usage.ru_utime, 6) if usage is not None else None
    system = round(usage.ru_stime, 6) if usage is not None else None
    total = round(user + system, 6) if usage is not None else None
    if usage is not None and sys.platform.startswith("linux"):
        peak = int(usage.ru_maxrss) * 1024  # Linux wait4 units: KiB
    elif usage is not None and sys.platform == "darwin":
        peak = int(usage.ru_maxrss)  # macOS wait4 units: bytes
    else:
        peak = None
    return {"cpu_user_seconds": user, "cpu_system_seconds": system,
            "cpu_seconds": total, "peak_rss_bytes": peak,
            "resource_source": "wait4/worker-with-reaped-descendants" if usage is not None else None}


def execute(case, round_number, seed, timeout, max_output_bytes=DEFAULT_OUTPUT_LIMIT,
            tracker=None):
    digest = hashlib.sha256(("%s:%s:%s" % (seed, round_number, case["id"])).encode()).digest()
    trial_seed = int.from_bytes(digest[:8], "big")
    argv = [x.replace("{seed}", str(trial_seed)).replace("{round}", str(round_number))
            for x in case["argv"]]
    started = time.monotonic()
    row = {"id": case["id"]}
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=(os.name == "posix"))
    except OSError as exc:
        return {**row, "status": "spawn_error", "error": type(exc).__name__,
                **_resource_values(None)}
    tracking = tracker is not None
    if tracking:
        tracker.register(proc.pid)
    overflow = threading.Event()
    cancel_reads = threading.Event()
    usage = None
    timed_out = False
    readers = ThreadPoolExecutor(max_workers=2)
    try:
        out = readers.submit(_read_bounded, proc.stdout, max_output_bytes, overflow,
                             cancel_reads)
        err = readers.submit(_read_bounded, proc.stderr, max_output_bytes, overflow,
                             cancel_reads)
        finished = False
        # A descendant can keep pipes open after the direct child exits:
        # keep enforcing deadline and kill the original process group.
        while not (finished and out.done() and err.done()):
            for future in (out, err):
                if future.done() and future.exception() is not None:
                    future.result()  # Propagate a read error after cleanup.
            if not finished:
                finished, current = _reap(proc)
                if finished:
                    usage = current
                    if tracking:
                        # A dead PID must not suppress samples of other live
                        # workers while inherited output pipes are still open.
                        tracker.unregister(proc.pid)
                        tracking = False
            if overflow.is_set():
                _stop_process(proc)
                cancel_reads.set()
            elif (time.monotonic() - started >= timeout and
                  not (finished and out.done() and err.done())):
                timed_out = True
                _stop_process(proc)
                # Descendants may leave the group while retaining pipes.
                # Stop draining too; group termination alone is not enough.
                cancel_reads.set()
            if not (finished and out.done() and err.done()):
                time.sleep(_POLL_SECONDS)
        stdout = out.result()
        stderr = err.result()
    except BaseException:
        _stop_process(proc)
        raise
    finally:
        cancel_reads.set()
        readers.shutdown(wait=True)
        if tracking:
            tracker.unregister(proc.pid)
        if proc.returncode is None:
            _stop_process(proc)
            proc.wait()
    row.update({"exit": proc.returncode,
                "elapsed": round(time.monotonic() - started, 6),
                "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
                **_resource_values(usage)})
    if overflow.is_set():
        row["status"] = "output_limit"
        row["output_limit_bytes"] = max_output_bytes
        return row
    if timed_out:
        row["status"] = "timeout"
        return row
    row.update({"status": "pass" if proc.returncode == case["expected_exit"] else "fail",
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest()})
    if case.get("stdout_contract") == "result-measurements/1":
        try:
            payload = json.loads(stdout)
            if (not isinstance(payload, dict) or set(payload) != {"result", "measurements"}
                or not isinstance(payload["result"], dict)
                or not isinstance(payload["measurements"], dict)):
                raise ValueError("invalid result/measurements envelope")
            normalized = json.dumps(payload["result"], sort_keys=True,
                                    separators=(",", ":"), allow_nan=False)
            row["result_sha256"] = hashlib.sha256(normalized.encode()).hexdigest()
            row["measurements"] = payload["measurements"]
        except (ValueError, UnicodeError):
            row["status"] = "invalid_output"
    return row


def _summarize_resources(rows, tracker):
    def total(key):
        observed = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(observed), 6) if observed else None
    return {"cpu_seconds_sum_measured": total("cpu_seconds"),
            "cpu_seconds_measured_workers": sum(r.get("cpu_seconds") is not None for r in rows),
            "peak_rss_bytes_sum_measured": total("peak_rss_bytes"),
            "peak_rss_measured_workers": sum(r.get("peak_rss_bytes") is not None for r in rows),
            "sampled_concurrent_child_rss_peak_bytes": tracker.peak,
            "rss_sample_count": tracker.samples,
            "aggregate_host_peak_rss_bytes": None,
            "scope": "wait4 worker usage may include reaped descendants; not complete process-tree accounting; sum of individual peaks is not concurrent peak",
            "sampled_rss_scope": "live direct worker processes only"}


def campaign(cases, rounds=10, seed=7, workers=2, timeout=10, keep_going=False,
             max_output_bytes=DEFAULT_OUTPUT_LIMIT):
    if (type(workers) is not int or workers < 1 or
        type(timeout) not in (float, int) or not 0 < timeout < float("inf") or
        type(max_output_bytes) is not int or max_output_bytes < 1):
        raise ValueError("invalid workers/timeout/output limit")
    for case in cases:
        if (not isinstance(case, dict) or type(case.get("id")) is not str or
            not case["id"] or type(case.get("expected_exit")) is not int or
            not isinstance(case.get("argv"), list) or not case["argv"] or
            any(type(x) is not str or not x for x in case["argv"])):
            raise ValueError("invalid case")
        if case.get("stdout_contract") not in (None, "result-measurements/1"):
            raise ValueError("unknown stdout contract")
    shards = plan(cases, rounds, seed)
    tracker = _ActiveRSS()
    watch = threading.Thread(target=tracker.run, daemon=True)
    watch.start()
    result = []
    try:
        for n, shard in enumerate(shards, 1):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                rows = list(pool.map(lambda c: execute(c, n, seed, timeout,
                                                       max_output_bytes, tracker), shard))
            passed = all(r["status"] == "pass" for r in rows)
            result.append({"round": n, "passed": passed, "results": rows})
            if not passed and not keep_going:
                break
    finally:
        tracker.stop()
        watch.join()
    flattened = [row for part in result for row in part["results"]]
    return {"schema": "constrained-lab/1", "seed": seed, "rounds_planned": rounds,
            "rounds_completed": len(result), "rounds_passed": sum(r["passed"] for r in result),
            "cases_planned": len(cases), "cases_completed": len(flattened),
            "resources": _summarize_resources(flattened, tracker), "reports": result}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("cases")
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--max-output-bytes", type=int, default=DEFAULT_OUTPUT_LIMIT,
                   help="retained byte cap for EACH of stdout and stderr (default 1 MiB)")
    p.add_argument("--keep-going", action="store_true")
    a = p.parse_args()
    try:
        cases = json.loads(Path(a.cases).read_text(encoding="utf-8"))
        if not isinstance(cases, list):
            raise ValueError("cases must be a JSON list")
        report = campaign(cases, a.rounds, a.seed, a.workers, a.timeout, a.keep_going, a.max_output_bytes)
    except (OSError, ValueError, KeyError) as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["rounds_completed"] == report["rounds_passed"] == report["rounds_planned"] else 1

if __name__ == "__main__":
    sys.exit(main())
