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
"""
from __future__ import annotations

import argparse
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


def observe(packages, dpkg_query):
    """Return {name: installed version or None} from dpkg-query; run nothing else."""
    proc = subprocess.run([dpkg_query, "-W", "-f", QUERY] + sorted(packages),
                          capture_output=True, text=True, env=ENV)
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


def install(todo, apt_get, prefix, result, log_path):
    """update + install; True when both exit 0. Records the log summary in result."""
    steps = [prefix + [apt_get] + ACQUIRE + ["update"],
             prefix + [apt_get] + ACQUIRE + ["install", "-y", "--no-install-recommends"] + todo]
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
    parser.add_argument("--receipt", metavar="FILE", help="write the JSON report to this new file")
    parser.add_argument("--log", metavar="FILE", help="keep apt output in this new file (with --install)")
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        packages = merge(args.list)
        for out in (args.receipt, args.log):
            if out and os.path.lexists(out):
                raise ValueError(out + ": already exists; a new file is required")
        digests = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in args.list}
    except (ValueError, OSError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc)}), file=sys.stderr)
        return 2
    result = {"schema": SCHEMA, "lists": args.list, "list_sha256": digests, "os": os_info(),
              "executed": [], "log": None}
    apt_get = shutil.which("apt-get", path=PATH)
    dpkg_query = shutil.which("dpkg-query", path=PATH)
    if not apt_get or not dpkg_query:
        result.update(status="apt_unavailable", packages={}, before={})
        code = 0
    else:
        rows = judge(packages, observe(packages, dpkg_query))
        todo = [name + ("=" + row["wanted"] if row["wanted"] else "")
                for name, row in rows.items() if row["state"] != "ok"]
        result.update(packages=rows, before={name: row["installed"] for name, row in rows.items()},
                      to_install=todo, status="satisfied" if not todo else "missing")
        code = 0 if not todo else 1
        if todo and args.install:
            prefix = [] if os.geteuid() == 0 else [shutil.which("sudo", path=PATH) or "", "-n"]
            if prefix and not prefix[0]:
                print(json.dumps({"schema": SCHEMA, "error": "root or sudo is required"}), file=sys.stderr)
                return 2
            if not install(todo, apt_get, prefix, result, args.log):
                result["status"], code = "failed", 3
            else:
                after = judge(packages, observe(packages, dpkg_query))
                result["packages"] = after
                ok = all(row["state"] == "ok" for row in after.values())
                result["status"], code = ("installed", 0) if ok else ("failed", 3)
    result["seconds"] = round(time.monotonic() - started, 3)
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
