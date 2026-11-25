#!/usr/bin/env python3
"""Shared child-repository contract checks (stdlib only, Python 3.9+).

Vendored as ``vendor/fleet_check.py``. Each child calls these from one
``tests/test_fleet.py`` instead of keeping per-repository copies of the same
lock, parent pin, workflow, benchmark, Node parity, launcher and script tests. Checks raise
``AssertionError`` naming the offending row; the helpers return plain data so
the child keeps only its own expectations (pins, cases, messages).
"""
from __future__ import annotations

import ast, functools, hashlib, json, os, re, shlex, shutil, signal, subprocess, sys, tempfile
from pathlib import Path

__all__ = ["check_actions", "check_imports", "check_lock", "check_scripts", "launcher_contract", "node", "node_parity", "parent_refs", "python_floor", "repo_root", "run_main", "script_catalog", "script_facts", "vendor_staging", "workflow_job"]
PARENT = "myon-bioinformatics/myon-bioinformatics"


def repo_root(start) -> Path:
    """Nearest directory at or above ``start`` that holds ``.github/workflows``."""
    start = Path(start).resolve()
    return next(p for p in (start, *start.parents) if (p / ".github/workflows").is_dir())


def script_facts(path) -> dict:
    """What a script says about itself, read without running it.

    ``summary`` is the first line of its module docstring, or of a JS module's leading ``//`` / ``/*`` comment past
    the shebang and imports ('' without one). ``argv``: it reads ``sys.argv`` itself. ``argparse``: it imports
    ``argparse`` or ``cli_args``, so its ``--help`` is the declared one and safe to ask for (nothing else is run).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix != ".py":
        lines = [line.strip() for line in text.splitlines()]
        for i, line in enumerate(lines):
            if line.startswith(("//", "/*")):   # a /** block may open on a line of its own: its first words count
                block = lines[i:i + 1] if line.startswith("//") else lines[i:i + 1 + next((k for k, l in enumerate(lines[i:]) if "*/" in l), 0)]
                words = next((w for w in (l.removeprefix("//").removeprefix("/*").lstrip("*").removesuffix("*/").strip() for l in block) if w), "")
                return {"summary": words, "argv": False, "argparse": False}
            if line and not line.startswith(("#!", "import ", "'use strict'", '"use strict"')):
                break
        return {"summary": "", "argv": False, "argparse": False}
    tree = ast.parse(text)
    nodes = list(ast.walk(tree))
    names = {a.name.split(".")[-1] for n in nodes if isinstance(n, ast.Import) for a in n.names}
    names |= {m for n in nodes if isinstance(n, ast.ImportFrom) for m in [(n.module or "").split(".")[-1], *(a.name for a in n.names)]}
    argv = any(isinstance(n, ast.Attribute) and n.attr == "argv" and isinstance(n.value, ast.Name) and n.value.id == "sys" for n in nodes)
    argv = argv or any(isinstance(n, ast.ImportFrom) and n.module == "sys" and any(a.name == "argv" for a in n.names) for n in nodes)
    return {"summary": (ast.get_docstring(tree) or "").strip().split("\n", 1)[0], "argv": argv,
            "argparse": bool(names & {"argparse", "cli_args"})}



def script_catalog(root, patterns, *, timeout: float = 20) -> list[dict]:
    """📜 ``{path, summary, usage}`` for every file the globs match under ``root``, sorted and once each: a scripts table that
    follows the code instead of being written by hand (the portfolio's project pages, a repository's Pages).

    ``summary`` is ``script_facts``'. Only a .py that imports argparse or cli_args is asked for ``--help`` (``python3 -S``,
    an empty HOME and only PATH and LANG of this machine's variables); ``usage`` is the first line of that and its indented
    continuations (≤ 300 characters). Nothing else is run. Best effort, never raises."""
    root, items = Path(root), []
    for path in sorted({p for pattern in patterns for p in root.glob(pattern) if p.is_file()}):
        relative, facts, lines = path.relative_to(root).as_posix(), script_facts(path), []
        if facts["argparse"] and path.suffix == ".py":
            try:
                with tempfile.TemporaryDirectory() as home:   # 🔒 PATH only: no names or values of this machine's other variables
                    done = subprocess.run(["python3", "-S", relative, "--help"], cwd=root, capture_output=True, text=True, timeout=timeout,
                                          stdin=subprocess.DEVNULL, env={"PATH": os.environ.get("PATH", ""), "HOME": home, "LANG": "C.UTF-8"})
                lines = done.stdout.strip().splitlines() if done.returncode == 0 else []
            except (OSError, subprocess.TimeoutExpired):
                pass
        lines = lines if lines and lines[0].lower().startswith("usage") else []
        end = next((i for i, line in enumerate(lines[1:], 1) if not line[:1].isspace() or not line.strip()), len(lines))
        usage = " ".join(" ".join(line.split()) for line in lines[:end])   # the first line and its indented continuations
        items.append({"path": relative, "summary": facts["summary"], "usage": usage[:300] + ("…" if len(usage) > 300 else "")})
    return items

def check_scripts(root, patterns=("*.py", "*.mjs", "*.cjs", "scripts/*", "tools/*", "examples/*"), passthrough=()) -> dict:
    """📝 Every script the globs match (.py / .mjs / .cjs) describes itself, and ⌨️ one that reads ``sys.argv``
    declares its arguments with argparse or cli_args, except ``passthrough`` launchers that hand argv on untouched.

    ``script_catalog`` turns the same facts into a table. Returns ``script_facts`` by path.
    """
    root = Path(root)
    found = {p.relative_to(root).as_posix(): script_facts(p) for pattern in patterns for p in sorted(root.glob(pattern))
             if p.is_file() and p.suffix in (".py", ".mjs", ".cjs") and p.name not in ("conftest.py", "__init__.py")}
    found = dict(sorted(found.items()))
    undescribed = [path for path, facts in found.items() if not facts["summary"]]
    undeclared = [path for path, facts in found.items() if facts["argv"] and not facts["argparse"] and path not in passthrough]
    assert not undescribed, f"📝 no docstring / leading comment: {undescribed}"
    assert not undeclared, f"⌨️ reads sys.argv without argparse or cli_args: {undeclared}"
    return found


def _stdlib_names() -> frozenset:
    names = getattr(sys, "stdlib_module_names", None)   # Python 3.10+; 3.9 falls back to the stdlib directory
    if names is None:
        import sysconfig
        lib = Path(sysconfig.get_paths()["stdlib"])
        names = {p.stem if p.suffix == ".py" else p.name for p in lib.iterdir() if p.suffix == ".py" or (p / "__init__.py").is_file()}
    return frozenset(names) | frozenset(sys.builtin_module_names) | {"__future__"}


def check_imports(root, entry, allow=(), paths=(".",)) -> dict:
    """🔍 Follow ``entry``'s imports through this repository (best effort, read without running) and fail on any
    third-party import that is not in ``allow``.

    Same-repository modules are looked up next to the importing file and under ``paths`` (relative to ``root``).
    Not counted: imports under ``if TYPE_CHECKING:`` or ``if __name__ == "__main__":`` (script-only), and in a ``try``
    whose handlers catch ``ImportError`` (optional). ``importlib.import_module("literal")`` is followed; a non-literal
    one or ``__import__`` is only listed. Returns ``{"modules": [...], "external": {name: ["file:line"]}, "dynamic": [...]}``.
    """
    root = Path(root).resolve()
    stdlib, allow = _stdlib_names(), set(allow)
    modules, external, dynamic, queue = set(), {}, [], [root / entry]

    def find(name, near):
        parts = name.split(".")
        for base in (near, *(root / p for p in paths)):
            for cand in (base.joinpath(*parts[:-1], parts[-1] + ".py"), base.joinpath(*parts, "__init__.py")):
                if cand.is_file():
                    return cand
        return None

    def counted(chain):   # chain: the import node, then its ancestors
        for child, p in zip(chain, chain[1:]):
            if isinstance(p, ast.If) and child in p.body and re.search(r"TYPE_CHECKING|__name__\W+==\W+.__main__.", ast.unparse(p.test)):
                return False
            if isinstance(p, ast.Try) and child in p.body and any(
                    h.type is None or re.search(r"\b(ImportError|ModuleNotFoundError)\b", ast.unparse(h.type)) for h in p.handlers):
                return False
        return True

    while queue:
        path = queue.pop()
        rel = path.relative_to(root).as_posix()
        if rel in modules:
            continue
        modules.add(rel)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            near, names = path.parent, []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                for _ in range(max(node.level - 1, 0)):   # relative: resolve against the package directory
                    near = near.parent
                head = node.module or ""
                names = [head] * bool(head) + [f"{head}.{a.name}".lstrip(".") for a in node.names]
            elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("import_module", "__import__"):
                arg = node.args[0] if node.args else None
                if getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module" and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names = [arg.value]
                else:
                    dynamic.append(f"{rel}:{node.lineno}")
            chain = [node]
            while chain[-1] in parent:
                chain.append(parent[chain[-1]])
            for name in names:
                hit = find(name, near) or find(name.split(".")[0], near)
                if hit:
                    queue.append(hit)
                elif getattr(node, "level", 0) == 0 and name.split(".")[0] not in stdlib | allow and counted(chain):
                    external.setdefault(name.split(".")[0], []).append(f"{rel}:{node.lineno}")
    found = {"modules": sorted(modules), "external": {k: sorted(set(v)) for k, v in sorted(external.items())}, "dynamic": sorted(dynamic)}
    assert not found["external"], f"🔍 third-party imports reached from {entry}: {found['external']}"
    return found


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


def _blocks(text, indent):
    """Top-level keys at ``indent`` spaces → their lines (comments and blank lines dropped)."""
    out, key = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lead = len(line) - len(line.lstrip(" "))
        if lead == indent and not line.lstrip().startswith("-"):
            key = line.strip().split(":", 1)[0].strip("'\"")
            out[key] = [line]
        elif lead > indent and key is not None:
            out[key].append(line)
        elif lead < indent:
            key = None
    return out


def check_ci(root, workflow="*.y*ml", allow=()) -> list:
    """⏱ Workflows that run on pull requests stay quick; return the workflow names checked.

    🔁 ``push`` names its branches or tags (else every push to a PR branch runs a second copy);
    🛑 a ``concurrency`` with ``cancel-in-progress`` (superseded PR runs stop); ⏲ every job has
    ``timeout-minutes`` (a job that ``uses:`` a reusable workflow is timed and cancelled there, so a workflow of
    such jobs only needs the 🔁 rule). ``allow`` holds
    ``"<file>:<rule>"`` exceptions, rule one of ``push``, ``cancel`` or ``timeout:<job>``.
    """
    problems, checked = [], []
    for path in sorted((Path(root) / ".github/workflows").glob(workflow)):
        text = path.read_text(encoding="utf-8")
        top = _blocks(text, 0)
        on = top.get("on") or top.get("true") or []
        head = on[0].split(":", 1)[1].strip() if on else ""
        events = re.findall(r"[a-z_]+", head) if head else [l.strip().split(":")[0] for l in on[1:] if l.startswith("  ") and not l.startswith("   ")]
        if "pull_request" not in events:
            continue
        checked.append(path.name)
        flag = lambda rule, message: None if f"{path.name}:{rule}" in allow else problems.append(f"{path.name}: {message}")
        push = _blocks("\n".join(l[2:] for l in on[1:]), 0).get("push") if not head else (["push:"] if "push" in events else None)
        if push is not None and not any(re.match(r"\s*(branches|tags)\s*:", l) for l in push[1:]):
            flag("push", "🔁 push runs on every branch (limit it to branches: [main])")
        jobs = {job: _blocks("\n".join(l[2:] for l in lines[1:]), 0)
                for job, lines in _blocks("\n".join(l[2:] for l in top.get("jobs", [])[1:]), 0).items()}
        own = {job: body for job, body in jobs.items() if "uses" not in body}   # reusable jobs carry their own
        concurrency = top.get("concurrency", [])
        if own and not any(re.match(r"\s*cancel-in-progress\s*:\s*(?!false\b)\S", l) for l in concurrency):
            flag("cancel", "🛑 no concurrency with cancel-in-progress")
        for job, body in own.items():
            if "timeout-minutes" not in body:
                flag(f"timeout:{job}", f"⏲ job {job} has no timeout-minutes")
    assert not problems, "\n".join(problems)
    return checked


def workflow_job(root, job, workflow="ci.yml", node_floor=None, setup=None, inputs=(), dispatch_lock=None) -> str:
    """Body of one top-level job of ``.github/workflows/<workflow>``.

    With ``node_floor`` the job must set up that Node major and carry the bash
    guard ``if [[ "$node_major" -lt FLOOR ]]; then … fi``; the guard is run in
    ``bash`` around the floor and must reject every older major only (the run
    is left to the POSIX lanes; on Windows the text of the guard is still checked).
    With ``setup`` (such as ``"astral-sh/setup-uv@"``) the job has exactly one
    ``uses: SETUP…`` step, it comes before the first ``python``/``python3``/``uv``
    command, and that step holds every string of ``inputs``.
    With ``dispatch_lock`` (such as ``"vendor.lock.json"``) the job is the
    vendor-dispatch verification: it checks the parent's repository id,
    resolves ``refs/heads/main`` with ``git ls-remote`` and runs
    ``pilot/vendor_dispatch.py --manifest …LOCK`` without ``--record`` or ``-S``.
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
    if dispatch_lock is not None:
        for wanted in ('PARENT_REPOSITORY_ID: "638801160"', "git ls-remote", "refs/heads/main"):  # the id of PARENT
            assert wanted in match[1], f"{where}: lacks {wanted}"
        code = re.sub(r"\\\n\s*", " ", "\n".join(line for line in match[1].splitlines() if not line.lstrip().startswith("#")))
        runs = [line.strip() for line in code.splitlines() if re.search(r"\bpython3?\s.*pilot/vendor_dispatch\.py", line)]
        assert runs, f"{where}: pilot/vendor_dispatch.py is not run"
        for line in runs:
            assert re.search(r"--manifest\s+\S*" + re.escape(dispatch_lock), line), f"{where}: no --manifest {dispatch_lock}: {line}"
        assert "--record" not in code and not any(re.search(r"\s-S\s", line) for line in runs), f"{where}: verify only, without --record or -S"
    if node_floor is not None:
        assert re.search(r"(?m)^\s*node-version:\s*['\"]?%d['\"]?\s*$" % node_floor, match[1]), f"{where}: no setup of Node {node_floor}"
        guard = re.search(r'(?ms)^([ \t]*)if \[\[ "\$node_major" -lt (\d+) \]\]; then\n.*?^\1fi$', match[1])
        assert guard and int(guard[2]) == node_floor, f"{where}: no node_major guard at {node_floor}"
        for major in range(node_floor - 2, node_floor + 2) if os.name != "nt" else ():
            done = subprocess.run(["bash", "-c", f"node_major={major}\n{guard[0]}"], capture_output=True, text=True, timeout=30)
            assert done.returncode == (major < node_floor), (where, major, done.stdout, done.stderr)
    return match[1]


def vendor_staging(root, tool=".vendor-sync-tools/vendor_stage.py", timeout=60) -> list:
    """Run every ``Stage canonical vendor evidence`` step against the real locked bytes; return the commands.

    Each step runs ``if: always()`` without ``continue-on-error`` or
    ``--promotion-receipt``, and the next step uploads its ``--output``. With
    the parent's ``vendor_stage.py`` at ``tool``, each command is run on a copy
    of the locked files plus one newly enrolled row: the evidence lists and
    copies exactly the manifest, every locked row and the named legacy and
    runtime evidence (a stray promotion receipt is left out) with matching
    SHA-256, and a corrupted locked file exits 2 without leaving the output.
    """
    root, commands = Path(root).resolve(), []
    for path in sorted((root / ".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        for found in re.finditer(r"(?m)^( +)- name: Stage canonical vendor evidence\n", text):
            step, _, upload = text[found.end():].partition("\n" + found[1] + "- ")
            run = re.search(r"(?m)^" + found[1] + r"  run: >-\n((?:" + found[1] + r"    [^\n]*\n)+)", step + "\n")
            assert "if: always()" in step and "continue-on-error" not in step and run, f"{path.name}: staging must always run and fail closed"
            command = " ".join(line.strip() for line in run[1].splitlines())
            args = shlex.split(command)
            assert "--promotion-receipt" not in args, command
            assert "path: " + args[args.index("--output") + 1] in upload.split("retention-days:", 1)[0], f"{path.name}: staged output is not uploaded"
            commands.append(command)
    assert commands and (root / tool).is_file(), f"no staging step, or no {tool} (the workflow's parent checkout)"
    for command in commands:
        with tempfile.TemporaryDirectory() as tmp:
            tmp, args = Path(tmp), shlex.split(command)
            args[:3] = [sys.executable, "-S", str(root / tool)]
            manifest, output = args[args.index("--manifest") + 1], tmp / args[args.index("--output") + 1]
            lock = json.loads((root / manifest).read_text(encoding="utf-8"))
            data = b"extra reviewed fixture\n"  # a newly enrolled member is staged without a workflow edit
            (tmp / "extra").mkdir()
            (tmp / "extra/LICENSE").write_bytes(data)
            legacy, runtime = ([args[i + 1] for i, arg in enumerate(args) if arg == flag] for flag in ("--legacy-evidence", "--runtime-evidence"))
            for member in [row["destination"] for row in lock["files"]] + legacy:
                (tmp / member).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / member, tmp / member)
            lock["files"].append(dict(lock["files"][0], destination="extra/LICENSE", sha256=hashlib.sha256(data).hexdigest(),
                                      blob_sha=hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()))
            (tmp / manifest).parent.mkdir(parents=True, exist_ok=True)
            (tmp / manifest).write_text(json.dumps(lock), encoding="utf-8")
            for member in [*runtime, "vendor-promotion.json"]:
                (tmp / member).write_text("{}", encoding="utf-8")
            done = subprocess.run(args, cwd=tmp, capture_output=True, text=True, timeout=timeout)
            assert done.returncode == 0, (command, done.stderr)
            evidence = json.loads((output / "vendor-evidence.json").read_text(encoding="utf-8"))
            locked = {manifest, *(row["destination"] for row in lock["files"])}
            seen = (evidence["kind"], set(evidence["locked"]), set(evidence["candidate"]), set(evidence["legacy"]), set(evidence["runtime"]))
            assert seen == (args[args.index("--kind") + 1] if "--kind" in args else "locked", locked, locked, set(legacy), set(runtime)), command
            staged = locked | set(legacy) | set(runtime)
            assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == staged | {"vendor-evidence.json"}, command
            for member in staged:
                body = (tmp / member).read_bytes()
                assert (output / member).read_bytes() == body and evidence["sha256"][member] == hashlib.sha256(body).hexdigest(), member
            shutil.rmtree(output)
            (tmp / lock["files"][0]["destination"]).write_bytes(b"corrupt")
            done = subprocess.run(args, cwd=tmp, capture_output=True, text=True, timeout=timeout)
            assert done.returncode == 2 and not output.exists(), (command, done.stderr)
    return commands


def python_floor(root, job="runtime", workflow="runtime-compatibility.yml") -> tuple:
    """``(floor, versions)``: the supported Python floor and the stdlib runtime job's version list.

    ``[project] requires-python`` of ``pyproject.toml`` is ``>=FLOOR`` and equals
    the ``requires-python`` of ``uv.lock``; the job's matrix ``python`` (or
    ``python-version``) list starts at that floor, every ``include`` row names
    a listed version, and the job runs ``python -S -m unittest`` without
    ``continue-on-error``. The matrix itself stays the child's expectation.
    """
    root = Path(root)
    section = re.split(r"(?m)^\[", ("\n" + (root / "pyproject.toml").read_text(encoding="utf-8")).partition("\n[project]\n")[2])[0]
    project = re.search(r'(?m)^requires-python\s*=\s*"(>=(\d+\.\d+))"', section)
    assert project, "pyproject.toml: [project] requires-python is not >=X.Y"
    lock = re.search(r'(?m)^requires-python\s*=\s*"([^"]*)"', (root / "uv.lock").read_text(encoding="utf-8"))
    assert lock and lock[1] == project[1], f"uv.lock requires-python differs from pyproject.toml {project[1]}"
    body, where = workflow_job(root, job, workflow), f"{workflow}: job {job}"
    listed = re.search(r"(?m)^\s+python(?:-version)?:\s*\[([^\]]*)\]", body)
    versions = re.findall(r"\d+\.\d+", listed[1]) if listed else []
    order = sorted(versions, key=lambda version: tuple(map(int, version.split("."))))
    assert order and order[0] == project[2], f"{where}: matrix {versions} does not start at the floor {project[2]}"
    extra = set(re.findall(r"(?m)^\s+python(?:-version)?:\s*['\"]?(\d+\.\d+)['\"]?\s*$", body)) - set(versions)
    assert not extra, f"{where}: include rows outside the matrix list: {sorted(extra)}"
    assert "python -S -m unittest" in body and "continue-on-error" not in body, f"{where}: needs python -S -m unittest, fail closed"
    return project[2], versions


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

    A missing interpreter exits 2 with empty stdout (as one JSON
    ``ENOINTERPRETER`` line under ``PY_LAUNCH_ERROR_FORMAT=json`` or
    ``jsonErrors``); ``assertNodeVersion`` accepts Node 26 and newer without an
    upper bound and rejects older with ``ENODEVERSION``; a candidate's
    interpreter prefix stays before the probe's ``-S -I``; argv, stdin and exit codes
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
        library, script = json.dumps(str(tmp / "vendor/py_launch.cjs")), json.dumps(str(tool))
        absent = json.dumps({"python": str(tmp / "no-such-python"), "jsonErrors": True})
        for missing in (run(extra={env_var: str(tmp / "no-such-python"), "PY_LAUNCH_ERROR_FORMAT": "json"}),
                        subprocess.run([exe, "-e", f"require({library}).main({script}, {absent});"], env=env, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=timeout)):
            assert (missing.returncode, missing.stdout, missing.stderr.count("\n")) == (2, "", 1), missing
            error = json.loads(missing.stderr)
            assert (error["ok"], error["code"], error["exit_code"]) == (False, "ENOINTERPRETER", 2) and "Python 3.9+" in error["hint"], missing
        (tmp / "probe.cjs").write_text('require("node:assert/strict").deepEqual(process.argv.slice(2, -1), ["-S", "-I", "-c"]); '
                                       'console.log("3.12");\n', encoding="utf-8")
        library_checks = (
            f"const assert = require('node:assert/strict'), launcher = require({library});"
            "for (const version of ['26.0.0', '99.0.0']) launcher.assertNodeVersion(version);"
            "assert.throws(() => launcher.assertNodeVersion('24.0.0'), (e) => e.code === 'ENODEVERSION' && /Node 26\\+/.test(e.message));"
            f"const found = launcher.resolvePython({{candidates: [{{cmd: process.execPath, prefix: [{json.dumps(str(tmp / 'probe.cjs'))}]}}], "
            "noSite: true, isolated: true});"
            "assert.equal(found.cmd, process.execPath);")
        checked = subprocess.run([exe, "-e", library_checks], env=env, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout)
        assert checked.returncode == 0, ("assertNodeVersion bounds or interpreter prefix", checked.stderr)
        marker = tmp / "site-hook-ran"
        (tmp / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').close()\nraise SystemExit(42)\n", encoding="utf-8")
        probe = run(extra={"PYTHONPATH": str(tmp)})
        assert (probe.returncode, probe.stdout) == (0, "ok\n") and not marker.exists(), "-S must cover interpreter discovery too"
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
