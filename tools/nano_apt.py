#!/usr/bin/env python3
"""Check, and only with --install apply, plain-text apt package lists (Debian/Ubuntu).

The list files are the single source of truth: one package per line, ``name`` or
``name=version``, whole-line ``#`` comments only. A Dockerfile can read the same
file without Python (``grep -v '^#' FILE | xargs apt-get install ...``). One file
is one group; the caller names every group it needs, so nothing is added "just in
case". Without apt-get/dpkg-query nothing is run and that fact is reported.

``--receipt FILE`` also writes the JSON report (installed versions before and after,
list SHA-256, OS id/version, seconds) to a new file. ``--log FILE`` keeps apt's own
output in a new file and prints only its summary line (plus the tail on failure).
apt runs with bounded retries/timeouts given by ``-o``, so /etc/apt is never written.

``--background --state FILE`` starts ``update`` + ``install --download-only`` as a detached
process and returns at once, so slower setup (uv sync, npm ci) can overlap the download.
A later ``--install --state FILE`` waits for it, skips ``update`` when the same packages
were fetched, and installs from the downloaded files. apt itself still runs one at a time.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

SCHEMA = "nano-apt/1"
MAX_BYTES = 64 * 1024
LINE = re.compile(r"([a-z0-9][a-z0-9+.-]+)(?:=([A-Za-z0-9][A-Za-z0-9.+:~-]*))?\Z")
PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV = {"PATH": PATH, "DEBIAN_FRONTEND": "noninteractive", "LANG": "C"}
QUERY = "${Package} ${db:Status-Abbrev} ${Version}\n"
# Per-file retry and connection/idle limits (not a total deadline); same values Ironmate
# wrote to /etc/apt/apt.conf.d, here passed per call instead.
ACQUIRE = ["-o", "Acquire::Retries=1", "-o", "Acquire::http::Timeout=15",
           "-o", "Acquire::https::Timeout=15", "-o", "APT::Update::Error-Mode=any"]
WAIT_SECONDS = 600  # longest a later --install waits for a background download
POLL_SECONDS = 0.2
SUMMARY = re.compile(r"\d+ upgraded, \d+ newly installed, \d+ to remove and \d+ not upgraded\.")


def _add(result, name, version):
    old = result.get(name)
    if old and version and old != version:
        raise ValueError("conflicting versions for " + name)
    result[name] = version or old


def read_list(path):
    """Parse one regular, non-symlink list file into {name: version-or-None}."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(str(path) + ": expected a regular, non-symlink file")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(str(path) + ": list exceeds 64 KiB")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError(str(path) + ": not UTF-8") from exc
    result = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if not match:
            raise ValueError("%s:%d: expected 'name' or 'name=version'" % (path, number))
        _add(result, *match.groups())
    return result


def merge(paths):
    merged = {}
    for path in paths:
        for name, version in read_list(path).items():
            _add(merged, name, version)
    return merged


class PackageQueryError(RuntimeError):
    """Package observation failed; missing packages cannot be inferred."""


def observe(packages, dpkg_query):
    """Return {name: installed version or None} from dpkg-query; run nothing else."""
    try:
        proc = subprocess.run([dpkg_query, "-W", "-f", QUERY] + sorted(packages),
                              capture_output=True, text=True, env=ENV)
    except OSError as exc:
        raise PackageQueryError("dpkg-query could not start: " + str(exc)) from exc
    if proc.returncode not in (0, 1):
        raise PackageQueryError(f"dpkg-query failed: exit {proc.returncode}")
    seen = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "ii" and parts[0] in packages:
            seen.setdefault(parts[0], parts[2])
    return {name: seen.get(name) for name in packages}


def judge(packages, installed):
    rows = {}
    for name, wanted in sorted(packages.items()):
        have = installed[name]
        state = "missing" if have is None else "version" if wanted not in (None, have) else "ok"
        rows[name] = {"wanted": wanted, "installed": have, "state": state}
    return rows


def run_apt(argv, log=None):
    """Run one apt command; its output goes to the open log file, else to stderr."""
    if log is None:
        return subprocess.run(argv, env=ENV, stdout=sys.stderr).returncode
    return subprocess.run(argv, env=ENV, stdout=log, stderr=subprocess.STDOUT).returncode


def summarize(path):
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    found = SUMMARY.findall("\n".join(lines))
    return {"path": str(path), "bytes": os.path.getsize(path),
            "summary": found[-1] if found else None,
            "warnings": sum(1 for line in lines if line.startswith(("W:", "Warning:"))),
            "errors": sum(1 for line in lines if line.startswith(("E:", "Err:", "Error:"))),
            "tail": lines[-10:]}


def install(todo, apt_get, prefix, result, log_path, download_only=False, skip_update=False):
    """update + install (or only download); True when every step exits 0. Records the log summary."""
    steps = [] if skip_update else [prefix + [apt_get] + ACQUIRE + ["update"]]
    steps.append(prefix + [apt_get] + ACQUIRE + ["install", "-y", "--no-install-recommends"]
                 + (["--download-only"] if download_only else []) + todo)
    ok, log = True, (open(log_path, "x", encoding="utf-8") if log_path else None)
    try:
        for step in steps:
            result["executed"].append(step)
            if run_apt(step, log) != 0:
                ok = False
                break
    finally:
        if log:
            log.close()
    if log_path:
        info = summarize(log_path)
        tail = info.pop("tail")
        result["log"] = info
        print("nano_apt: %s (warnings %d, errors %d)" % (
            info["summary"] or "no apt summary line", info["warnings"], info["errors"]), file=sys.stderr)
        if not ok:
            print("\n".join(tail), file=sys.stderr)
    return ok


def write_state(path, data):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True)
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # exists but is not ours to signal
        return True
    return True


def join(path):
    """Wait for a background download; returns (final state or None, seconds waited)."""
    started = time.monotonic()
    while True:
        state, waited = read_json(path), time.monotonic() - started
        if state is None or state.get("status") not in ("starting", "running"):
            return state, waited
        pid = read_json(path + ".pid")
        if isinstance(pid, int) and not alive(pid):
            state = read_json(path)  # it may have finished between the two reads
            if state and state.get("status") not in ("starting", "running"):
                return state, waited
            return {"status": "failed", "reason": "background process exited early"}, waited
        if waited >= WAIT_SECONDS:
            return {"status": "timeout"}, waited
        time.sleep(POLL_SECONDS)


def start_background(args):
    """Start this tool detached with --download-only; its own output goes to STATE.log."""
    argv = [sys.executable, "-S", os.path.abspath(__file__)]
    for path in args.list:
        argv += ["--list", path]
    argv += ["--download-only", "--state", args.state]
    created, proc = [], None
    try:
        with ExitStack() as stack:
            streams = []
            # Reserve every output before spawning. Exclusive creation also rejects
            # symlinks and files appearing after the CLI's friendly preflight check.
            for path in (args.state, args.state + ".log", args.state + ".pid"):
                stream = stack.enter_context(open(path, "x", encoding="utf-8"))
                created.append((path, os.fstat(stream.fileno())))
                streams.append(stream)
            state, log, pid = streams
            json.dump({"status": "starting"}, state)
            state.flush()
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, close_fds=True)
            pid.write(str(proc.pid))
    except OSError:
        # Once spawned, retain outputs for diagnostics and a possible later join.
        if proc is None:
            for path, owned in reversed(created):
                try:
                    current = os.lstat(path)
                    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        os.unlink(path)
                except FileNotFoundError:
                    pass
        raise
    return proc.pid


def os_info():
    info = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key in ("ID", "VERSION_ID"):
                info[key.lower()] = value.strip().strip('"')
    except OSError:
        pass
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="append", required=True, metavar="FILE",
                        help="package list file (one group); repeat for several groups")
    parser.add_argument("--install", action="store_true",
                        help="apt-get update + install what is missing (default: report only)")
    parser.add_argument("--download-only", action="store_true",
                        help="apt-get update + download what is missing, without installing")
    parser.add_argument("--background", action="store_true",
                        help="run --download-only detached and return at once (needs --state)")
    parser.add_argument("--state", metavar="FILE",
                        help="state of a background download: --background creates it, --install waits on it")
    parser.add_argument("--receipt", metavar="FILE", help="write the JSON report to this new file")
    parser.add_argument("--log", metavar="FILE", help="keep apt output in this new file (with --install)")
    args = parser.parse_args(argv)
    if args.background and (args.install or args.download_only or args.receipt or args.log or not args.state):
        parser.error("--background takes --list and --state only")
    if args.download_only and args.install:
        parser.error("--download-only and --install are mutually exclusive")
    started = time.monotonic()
    try:
        packages = merge(args.list)
        outputs = [args.receipt, args.log]
        if args.background:
            outputs += [args.state, args.state + ".log", args.state + ".pid"]
        for out in outputs:
            if out and os.path.lexists(out):
                raise ValueError(out + ": already exists; a new file is required")
        digests = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in args.list}
        if args.background:
            pid = start_background(args)
            print(json.dumps({"schema": SCHEMA, "status": "started", "pid": pid, "state": args.state,
                              "log": args.state + ".log", "lists": args.list}, sort_keys=True, indent=2))
            return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc)}), file=sys.stderr)
        return 2
    if args.download_only and args.state:
        write_state(args.state, {"status": "running", "pid": os.getpid()})
    result = {"schema": SCHEMA, "lists": args.list, "list_sha256": digests, "os": os_info(),
              "executed": [], "log": None}
    apt_get = shutil.which("apt-get", path=PATH)
    dpkg_query = shutil.which("dpkg-query", path=PATH)
    if not apt_get or not dpkg_query:
        result.update(status="apt_unavailable", packages={}, before={})
        code = 0
    else:
        try:
            rows = judge(packages, observe(packages, dpkg_query))
            todo = [name + ("=" + row["wanted"] if row["wanted"] else "")
                    for name, row in rows.items() if row["state"] != "ok"]
            result.update(packages=rows, before={name: row["installed"] for name, row in rows.items()},
                          to_install=todo, status="satisfied" if not todo else "missing")
            code = 0 if not todo else 1
            if todo and (args.install or args.download_only):
                prefix = [] if os.geteuid() == 0 else [shutil.which("sudo", path=PATH) or "", "-n"]
                if prefix and not prefix[0]:
                    result.update(status="failed", error="root or sudo is required")
                    print("nano_apt: root or sudo is required", file=sys.stderr)
                    code = 2
                else:
                    skip = False
                    if args.install and args.state:
                        final, waited = join(args.state)
                        skip = bool(final and final.get("status") == "downloaded"
                                    and final.get("to_install") == todo and final.get("list_sha256") == digests)
                        result["joined"] = {"state": final.get("status") if final else "none",
                                            "waited": round(waited, 3), "skipped_update": skip}
                    if not install(todo, apt_get, prefix, result, args.log, args.download_only, skip):
                        result["status"], code = "failed", 3
                    elif args.download_only:
                        result["status"], code = "downloaded", 0
                    else:
                        after = judge(packages, observe(packages, dpkg_query))
                        result["packages"] = after
                        ok = all(row["state"] == "ok" for row in after.values())
                        result["status"], code = ("installed", 0) if ok else ("failed", 3)
        except PackageQueryError as exc:
            result.update(status="failed", error=str(exc))
            result.setdefault("packages", {})
            result.setdefault("before", {})
            code = 2
    result["seconds"] = round(time.monotonic() - started, 3)
    if args.download_only and args.state:
        write_state(args.state, dict(result, pid=os.getpid()))
    text = json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2)
    print(text)
    if args.receipt:
        try:
            with open(args.receipt, "x", encoding="utf-8") as stream:
                stream.write(text + "\n")
        except OSError as exc:
            print(json.dumps({"schema": SCHEMA, "error": "receipt: " + str(exc)}), file=sys.stderr)
            return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
