#!/usr/bin/env python3
"""Shared child-repository contract checks (stdlib only, Python 3.9+).

Vendored as ``vendor/fleet_check.py``. Each child calls these from one
``tests/test_fleet.py`` instead of keeping per-repository copies of the same
lock, parent pin, workflow, benchmark, Node parity and launcher tests. Checks raise
``AssertionError`` naming the offending row; the helpers return plain data so
the child keeps only its own expectations (pins, cases, messages).
"""
from __future__ import annotations

import functools, hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile
from pathlib import Path

__all__ = ["check_lock", "launcher_contract", "node", "node_parity", "parent_refs", "repo_root", "run_main", "workflow_job"]
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
def node(minimum=26, ci_jobs=("test",)):
    """``$NODE`` or ``node`` on PATH when it runs version ``minimum``+, else ``None``.

    In a GitHub Actions job named in ``ci_jobs`` (``GITHUB_JOB``) a missing or
    old Node is an ``AssertionError``, so the job that provisions Node runs the
    parity checks instead of skipping them; other jobs (portability, stdlib) skip.
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
    in_job = os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_JOB") in ci_jobs
    assert not in_job, f"Node {minimum}+ is required in CI job {os.environ.get('GITHUB_JOB')}"
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


def launcher_contract(root, env_var="FLEET_PYTHON", timeout=60) -> None:
    """Check the child's own ``vendor/py_launch.cjs`` bytes against the launcher contract.

    A missing interpreter exits 2 with empty stdout; argv, stdin and exit codes
    pass through unchanged under ``-S``; ``-S`` also covers interpreter
    discovery (no ``sitecustomize``), and the library ``resolvePython`` honors
    ``noSite`` and ``isolated`` independently; on POSIX, repeated signals are forwarded
    until the child exits, ending in exit 2 with a text or JSON ``ESIGNAL`` error.
    """
    exe = node()
    assert exe, "Node 26+ is not available (set NODE)"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "vendor").mkdir()
        shutil.copy(Path(root) / "vendor/py_launch.cjs", tmp / "vendor/py_launch.cjs")
        tool = tmp / "tool.py"
        argv = [exe, str(tmp / "vendor/py_launch.cjs"), "--env-var", env_var, "--", str(tool)]
        env = {**os.environ, env_var: sys.executable, "PYTHONIOENCODING": "utf-8"}
        env.pop("PY_LAUNCH_ERROR_FORMAT", None)

        def run(*args, extra=None, **kw):
            return subprocess.run([*argv, *args], env={**env, **(extra or {})}, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw)

        tool.write_text("print('ok')\n", encoding="utf-8")
        missing = run(extra={env_var: str(tmp / "no-such-python")})
        assert (missing.returncode, missing.stdout) == (2, ""), missing
        marker = tmp / "site-hook-ran"
        (tmp / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        probe = run(extra={"PYTHONPATH": str(tmp)})
        assert probe.returncode == 0 and not marker.exists(), "-S must cover interpreter discovery too"
        library = json.dumps(str(tmp / "vendor/py_launch.cjs"))
        for no_site in (False, True):
            for isolated in (False, True):
                marker.unlink(missing_ok=True)
                options = json.dumps({"python": sys.executable, "noSite": no_site, "isolated": isolated})
                found = subprocess.run([exe, "-e", f"require({library}).resolvePython({options});"],
                                       env={**env, "PYTHONPATH": str(tmp)}, capture_output=True, timeout=timeout)
                assert found.returncode == 0, found.stderr
                assert marker.exists() == (not (no_site or isolated)), ("resolvePython", no_site, isolated)
        for code in (0, 1, 3, 7):
            tool.write_text("import json, sys\nsys.stdout.write(json.dumps({'argv': sys.argv[1:], "
                            "'stdin': sys.stdin.read(), 'nosite': sys.flags.no_site}))\n"
                            f"sys.exit({code})\n", encoding="utf-8")
            done = run("a b", "--x=\u00fc", input="\u3053\u3093\u306b\u3061\u306f\n")
            assert done.returncode == code, done
            assert json.loads(done.stdout) == {"argv": ["a b", "--x=\u00fc"], "stdin": "\u3053\u3093\u306b\u3061\u306f\n",
                                               "nosite": 1}, done
        if os.name == "nt":
            return
        import selectors
        tool.write_text("import signal, time\nsignal.signal(signal.SIGINT, lambda *_: print('ACK', flush=True))\n"
                        "print('READY', flush=True)\nwhile True: time.sleep(60)\n", encoding="utf-8")
        for fmt in ("", "json"):
            child = subprocess.Popen(argv, env={**env, "PY_LAUNCH_ERROR_FORMAT": fmt}, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    for expected, sent in ((b"READY\n", signal.SIGINT), (b"ACK\n", signal.SIGINT), (b"ACK\n", signal.SIGTERM)):
                        assert selector.select(timeout=5), "child did not acknowledge forwarded signal"
                        assert child.stdout.readline() == expected
                        child.send_signal(sent)
                stdout, stderr = child.communicate(timeout=5)
                assert (child.returncode, stdout) == (2, b""), stderr
                if fmt:
                    error = json.loads(stderr)
                    assert (error["code"], error["exit_code"]) == ("ESIGNAL", 2) and "SIGTERM" in error["message"]
                else:
                    assert b"terminated by signal SIGTERM" in stderr
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.communicate(timeout=5)
