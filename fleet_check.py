#!/usr/bin/env python3
"""Shared child-repository contract checks (stdlib only, Python 3.9+).

Vendored as ``vendor/fleet_check.py``. Each child calls these from one
``tests/test_fleet.py`` instead of keeping per-repository copies of the same
lock, parent pin, workflow, benchmark and Node parity tests. Checks raise
``AssertionError`` naming the offending row; the helpers return plain data so
the child keeps only its own expectations (pins, cases, messages).
"""
from __future__ import annotations

import functools, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path

__all__ = ["check_lock", "node", "node_parity", "parent_refs", "repo_root", "run_main", "workflow_job"]
PARENT = "myon-bioinformatics/myon-bioinformatics"


def repo_root(start) -> Path:
    """Nearest directory at or above ``start`` that holds ``.github/workflows``."""
    start = Path(start).resolve()
    return next(p for p in (start, *start.parents) if (p / ".github/workflows").is_dir())


def check_lock(root, lock="vendor.lock.json", commits=None) -> dict:
    """Verify every vendor-lock/1 row against its placed bytes; return rows by destination.

    Each row needs a full-SHA ``ref`` equal to ``commit`` and bytes matching
    ``blob_sha`` and ``sha256``. ``commits`` maps chosen destinations to the
    accepted upstream commit they must stay on.
    """
    root = Path(root)
    data = json.loads((root / lock).read_text(encoding="utf-8"))
    assert data.get("schema") == "vendor-lock/1", lock
    rows = {}
    for row in data["files"]:
        where = row["destination"]
        assert re.fullmatch(r"[0-9a-f]{40}", row["ref"]) and row["ref"] == row["commit"], where
        body = (root / where).read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()
        assert (blob, hashlib.sha256(body).hexdigest()) == (row["blob_sha"], row["sha256"]), where
        rows[where] = row
    for where, commit in (commits or {}).items():
        assert rows[where]["commit"] == commit, where
    return rows


def parent_refs(root, skip=()) -> list:
    """Every ref a workflow uses to reach the parent repository.

    Collects ``uses: PARENT/…@ref`` (mutable and short refs included) and the
    ``ref:`` of each ``repository: PARENT`` checkout (``None`` when missing).
    Checkouts whose ``path:`` is in ``skip`` (such as ``.vendor-sync-tools``,
    resolved separately) are left out.
    """
    name = re.escape(PARENT)
    refs = []
    for path in sorted((Path(root) / ".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        refs += re.findall(r"uses:\s*['\"]?" + name + r"/[^\s@'\"]+@([^\s'\"]+)", text)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not re.fullmatch(r"\s+(?:-\s+)?repository:\s*['\"]?" + name + r"['\"]?\s*", line):
                continue
            keys = {}
            for item in lines[i + 1:i + 12]:
                if re.match(r"\s*- ", item):
                    break  # the next step starts; its ref is not this checkout's
                match = re.fullmatch(r"\s+(ref|path):\s*['\"]?(.*?)['\"]?\s*", item)
                if match:
                    keys.setdefault(match[1], match[2])
            if keys.get("path") not in skip:
                refs.append(keys.get("ref"))
    return refs


def workflow_job(root, job, workflow="ci.yml") -> str:
    """Body of one top-level job of ``.github/workflows/<workflow>``."""
    text = (Path(root) / ".github/workflows" / workflow).read_text(encoding="utf-8")
    match = re.search(r"(?ms)^  " + re.escape(job) + r":\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text)
    assert match, f"{workflow}: missing job {job}"
    return match[1]


def run_main(root, script, patch="", *, optimized=False, timeout=60) -> subprocess.CompletedProcess:
    """Run ``script``'s ``main()`` in a fresh ``python -S`` (plus ``-O`` when ``optimized``).

    ``patch`` is Python source run just before ``main()`` with ``g`` bound to
    main's globals, so a benchmark guard can swap in a wrong result and prove
    the mismatch still fails when ``assert`` statements are stripped.
    """
    code = ("import runpy, sys, types\nfrom unittest import mock\n"
            "g = runpy.run_path(sys.argv[1])['main'].__globals__\n" + patch + "\ng['main']()\n")
    argv = [sys.executable, "-S", *(["-O"] if optimized else []), "-c", code, str(Path(root) / script)]
    return subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


@functools.lru_cache(maxsize=None)
def node(minimum=26):
    """``$NODE`` or ``node`` on PATH when it runs version ``minimum``+, else ``None``.

    Under GitHub Actions a missing or old Node is an ``AssertionError``, so CI
    runs the parity checks instead of skipping them.
    """
    found = os.environ.get("NODE") or shutil.which("node")
    version = ""
    if found:
        try:
            version = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    match = re.match(r"v(\d+)", version.strip())
    if match and int(match[1]) >= minimum:
        return found
    assert os.environ.get("GITHUB_ACTIONS") != "true", f"Node {minimum}+ is required in CI"
    return None


def node_parity(root, tool, env_var, *args, input=None, timeout=120) -> tuple:
    """``(rc, stdout, stderr)`` through ``vendor/py_launch.cjs`` and through ``python -S``.

    The Node side is the documented direct command
    ``node vendor/py_launch.cjs --env-var ENV_VAR -- TOOL ARGS`` with
    ``ENV_VAR`` set to this interpreter, so both sides run the same Python.
    """
    root = Path(root)
    env = {**os.environ, env_var: sys.executable, "PYTHONIOENCODING": "utf-8"}
    env.pop("PY_LAUNCH_ERROR_FORMAT", None)
    script = str(root / tool)
    exe = node()
    assert exe, "Node 26+ is not available (set NODE)"
    launcher = [exe, str(root / "vendor/py_launch.cjs"), "--env-var", env_var, "--"]
    results = []
    for argv in ([*launcher, script, *args], [sys.executable, "-S", script, *args]):
        done = subprocess.run(argv, cwd=root, env=env, input=input, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        results.append((done.returncode, done.stdout, done.stderr))
    return tuple(results)
