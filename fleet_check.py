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

__all__ = ["check_actions", "check_lock", "launcher_contract", "node", "node_parity", "parent_refs", "repo_root", "run_main", "workflow_job"]
PARENT = "myon-bioinformatics/myon-bioinformatics"


def repo_root(start) -> Path:
    """Nearest directory at or above ``start`` that holds ``.github/workflows``."""
    start = Path(start).resolve()
    return next(p for p in (start, *start.parents) if (p / ".github/workflows").is_dir())


def check_lock(root, lock="vendor.lock.json", commits=None, sources=None) -> dict:
    """Verify every vendor-lock/1 row against its placed bytes; return rows by destination.

    Each row needs a full-SHA ``ref`` equal to ``commit`` and bytes matching
    ``blob_sha`` and ``sha256``. ``commits`` maps chosen destinations to the
    accepted upstream commit they must stay on. ``sources`` maps destinations
    to their ``(repository, source)``; a repository named there has no other
    locked row, so listing every row pins the whole lock's provenance.
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
    for where, origin in (sources or {}).items():
        assert where in rows and (rows[where]["repository"], rows[where]["source"]) == tuple(origin), where
    named = {origin[0] for origin in (sources or {}).values()}
    extra = sorted(where for where, row in rows.items() if row.get("repository") in named and where not in sources)
    assert not extra, f"unlisted rows from a listed repository: {extra}"
    return rows


def parent_refs(root, skip=(), only=()) -> list:
    """Every ref a workflow uses to reach the parent repository.

    Collects ``uses: PARENT/…@ref`` (mutable and short refs included) and the
    ``ref:`` of each ``repository: PARENT`` checkout (``None`` when missing).
    Checkouts whose ``path:`` is in ``skip`` (such as ``.vendor-sync-tools``,
    resolved separately) are left out. With ``only``, just the refs of the
    checkouts at those paths are returned (no ``uses:`` refs).
    """
    name = re.escape(PARENT)
    refs = []
    for path in sorted((Path(root) / ".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if not only:
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
            if keys.get("path") not in skip and (not only or keys.get("path") in only):
                refs.append(keys.get("ref"))
    return refs


def check_actions(root, pins=None, workflow="*.y*ml") -> list:
    """Verify every ``uses: owner/repo[/path]@ref`` of the workflows; return ``(action, sha, version)`` rows.

    Each ref is a full 40-hex SHA followed by a ``# vN[.N[.N]]`` version
    comment; only the parent's reusable workflows (no release tags) pass on
    the SHA alone, with version ``None``. ``pins`` maps every action in use to
    its reviewed ``"<sha>"`` or ``"<sha> # <version>"``. Local ``./`` and
    ``docker://`` steps are not refs to pin and are left out.
    """
    rows = []
    for path in sorted((Path(root) / ".github/workflows").glob(workflow)):
        for line in path.read_text(encoding="utf-8").splitlines():
            use = re.fullmatch(r"\s*(?:-\s+)?uses:\s*['\"]?([^\s'\"#]+)['\"]?\s*(?:#\s*(.*?))?\s*", line)
            if not use or use[1].startswith(("./", "docker://")):
                continue
            where = f"{path.name}: {line.strip()}"
            action, _, ref = use[1].partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{where}: not a full SHA"
            version = use[2] if re.fullmatch(r"v\d+(?:\.\d+){0,2}", use[2] or "") else None
            if not action.startswith(PARENT + "/.github/workflows/"):
                assert version, f"{where}: no version comment"
                if pins is not None:
                    assert pins.get(action) in (ref, f"{ref} # {version}"), f"{where}: not the reviewed pin"
            rows.append((action, ref, version))
    return rows


def workflow_job(root, job, workflow="ci.yml", node_floor=None, setup=None, inputs=()) -> str:
    """Body of one top-level job of ``.github/workflows/<workflow>``.

    With ``node_floor`` the job must set up that Node major and carry the bash
    guard ``if [[ "$node_major" -lt FLOOR ]]; then … fi``; the guard is run in
    ``bash`` around the floor and must reject every older major only.
    With ``setup`` (such as ``"astral-sh/setup-uv@"``) the job has exactly one
    ``uses: SETUP…`` step, it comes before the first ``python``/``python3``/``uv``
    command, and that step holds every string of ``inputs``.
    """
    text = (Path(root) / ".github/workflows" / workflow).read_text(encoding="utf-8")
    match = re.search(r"(?ms)^  " + re.escape(job) + r":\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text)
    assert match, f"{workflow}: missing job {job}"
    where = f"{workflow}: job {job}"
    if setup is not None:
        assert match[1].count("uses: " + setup) == 1, f"{where}: needs exactly one uses: {setup}"
        at = match[1].index("uses: " + setup)
        command = re.search(r"(?m)^\s*(?:-\s+)?(?:run:\s*)?(?:python3?|uv)\s|\bpython3?\s+(?:-c|-\s|[A-Za-z_])", match[1])
        assert command, f"{where}: no python or uv command to set up for"
        assert at < command.start(), f"{where}: {command[0].strip()} runs before {setup}"
        lead = match[1][match[1].rfind("\n", 0, at) + 1:at]  # "      - " or "        ": the step's list indent either way
        step = re.split(r"(?m)^" + " " * (len(lead) - 2) + r"- ", match[1][at:], maxsplit=1)[0]
        for wanted in inputs:
            assert wanted in step, f"{where}: {setup} step lacks {wanted}"
    if node_floor is not None:
        assert re.search(r"(?m)^\s*node-version:\s*['\"]?%d['\"]?\s*$" % node_floor, match[1]), f"{where}: no setup of Node {node_floor}"
        guard = re.search(r'(?ms)^([ \t]*)if \[\[ "\$node_major" -lt (\d+) \]\]; then\n.*?^\1fi$', match[1])
        assert guard and int(guard[2]) == node_floor, f"{where}: no node_major guard at {node_floor}"
        for major in range(node_floor - 2, node_floor + 2):
            done = subprocess.run(["bash", "-c", f"node_major={major}\n{guard[0]}"], capture_output=True, text=True, timeout=30)
            assert done.returncode == (major < node_floor), (where, major, done.stdout, done.stderr)
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
    ``ci_jobs=None`` requires Node in every GitHub Actions job.
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
    in_job = os.environ.get("GITHUB_ACTIONS") == "true" and (ci_jobs is None or os.environ.get("GITHUB_JOB") in ci_jobs)
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
    discovery (no ``sitecustomize``), and the library ``resolvePython``, ``run``
    and ``main`` honor ``noSite`` and ``isolated`` independently against a hostile
    ``sitecustomize`` (unprotected, the interpreter is "not suitable"); on POSIX,
    SIGINT and SIGTERM are forwarded in either order, directly or after repeated
    acknowledged signals, until the child exits: exit 2, empty stdout and exactly
    one stderr line (text or JSON ``ESIGNAL``, ``ok: false``) ending in
    ``terminated by signal NAME``.
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
        (tmp / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').close()\nraise SystemExit(42)\n", encoding="utf-8")
        probe = run(extra={"PYTHONPATH": str(tmp)})
        assert (probe.returncode, probe.stdout) == (0, "ok\n") and not marker.exists(), "-S must cover interpreter discovery too"
        library, script = json.dumps(str(tmp / "vendor/py_launch.cjs")), json.dumps(str(tool))
        for flags in ({}, {"noSite": True}, {"isolated": True}, {"noSite": True, "isolated": True}):
            options = json.dumps({"python": sys.executable, **flags})
            for name, call in (("resolvePython", f"launcher.resolvePython({options}); console.log('ok');"),
                               ("run", f"process.stdout.write(launcher.run({script}, [], {options}).stdout);"),
                               ("main", f"launcher.main({script}, {options});")):
                marker.unlink(missing_ok=True)
                found = subprocess.run([exe, "-e", f"const launcher = require({library}); {call}"], cwd=tmp,
                                       env={**env, "PYTHONPATH": str(tmp)}, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=timeout)
                seen = (name, flags, found.returncode, found.stdout, found.stderr)
                if flags:
                    assert not marker.exists() and seen[2:] == (0, "ok\n", ""), seen
                else:  # control: the hostile hook is reachable, and an unprotected probe refuses the interpreter
                    assert marker.exists() and found.returncode != 0 and "not suitable" in found.stderr, seen
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
        for ignored, last in (("SIGINT", "SIGTERM"), ("SIGTERM", "SIGINT")):
            tool.write_text(f"import signal, time\nsignal.signal(signal.{ignored}, lambda *_: print('ACK', flush=True))\n"
                            f"signal.signal(signal.{last}, signal.SIG_DFL)\n"
                            # short sleeps: a signal landing just before a long sleep would be handled only after it
                            "print('READY', flush=True)\nwhile True: time.sleep(0.05)\n", encoding="utf-8")
            for fmt, repeats in (("", 0), ("", 2), ("json", 0), ("json", 2)):
                child = subprocess.Popen(argv, env={**env, "PY_LAUNCH_ERROR_FORMAT": fmt}, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(child.stdout, selectors.EVENT_READ)
                        # Only the launcher PID is signalled, never the Python child or its group.
                        for step in range(repeats + 1):
                            assert selector.select(timeout=5), "child did not acknowledge forwarded signal"
                            assert child.stdout.readline() == (b"ACK\n" if step else b"READY\n") and child.poll() is None
                            child.send_signal(getattr(signal, last if step == repeats else ignored))
                    try:
                        stdout, stderr = child.communicate(timeout=5)
                    except subprocess.TimeoutExpired:  # the launcher went away and left its Python child holding the pipes
                        os.killpg(child.pid, signal.SIGKILL)
                        stdout, stderr = child.communicate(timeout=5)
                    seen = (ignored, last, fmt, repeats, child.returncode, stdout, stderr)
                    assert (child.returncode, stdout) == (2, b"") and stderr.count(b"\n") == 1, seen
                    message = stderr.decode("utf-8").strip()
                    if fmt:
                        error = json.loads(stderr)
                        assert (error["ok"], error["code"], error["exit_code"]) == (False, "ESIGNAL", 2), seen
                        message = error["message"]
                    assert message.endswith(f"terminated by signal {last}"), seen
                finally:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.communicate(timeout=5)
