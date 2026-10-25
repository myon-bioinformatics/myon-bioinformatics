"""Dispatch vendor-catalog entries from the catalog JSON alone.

Each tool already names ``repository``, ``commit``, and ``source``. Those
three fields are the GitHub location: ``https://github.com/<repository>`` at
``--resolve-ref`` (or the recorded commit) and the source path. Resolved
commits are written back into the same ``commit`` fields. No second manifest
and no ``uv.lock`` are produced.

A child ``vendor-lock/1`` manifest is accepted too. Its ``files`` rows carry
the same GitHub coordinates plus ``destination``, ``blob_sha`` and ``sha256``.
For a lock, every reachable row is also fetched and checked against the
recorded digest, and ``--record`` writes ``ref``, ``commit``, ``blob_sha`` and
``sha256`` back into that same lock. A recorded digest that no longer matches
its recorded commit is an error, not an update.

``vendor-catalog/1`` cannot store a branch next to the commit, and it cannot
store a digest. ``--resolve-ref main`` is the dynamic selector. Fetch digests
are printed, not stored. ``vendor.lock.json`` stays the enrolled digest record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "vendor-catalog.json"
UV_FLOOR = (3, 11, 0)
NODE_FLOOR = 24
NODE_SUFFIXES = {".js", ".cjs", ".mjs"}
MAX_BYTES = 2_000_000
USER_AGENT = "vendor-dispatch/1"


class Env(NamedTuple):
    python: tuple
    no_site: bool
    offline: bool
    uv: bool
    node: int | None


class Probe(NamedTuple):
    pyproject: str | None
    package_json: dict | None


class Decision(NamedTuple):
    action: str
    reason: str


def one_line(text):
    return " ".join(str(text).split())


def git_spec(repository, ref, subdirectory=None):
    """Git URL uv accepts for ``uv pip install``.

    A subdirectory fragment is only valid when that directory contains a
    project file. This pilot's catalog paths are files, so callers leave
    ``subdirectory`` unset unless they have such a directory.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("expected owner/repository")
    if ref != "main" and not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("ref must be main or a commit sha")
    spec = "git+https://github.com/" + repository + "@" + ref
    if subdirectory:
        parts = Path(subdirectory).parts
        if subdirectory.startswith("/") or ".." in parts or not subdirectory:
            raise ValueError("invalid subdirectory")
        spec += "#subdirectory=" + subdirectory.replace("\\", "/")
    return spec


def pyproject_url(repository, ref):
    return "https://raw.githubusercontent.com/" + repository + "/" + quote(ref, safe="") + "/pyproject.toml"


def package_json_url(repository, ref):
    return "https://raw.githubusercontent.com/" + repository + "/" + quote(ref, safe="") + "/package.json"


def npm_git_spec(repository, commit):
    """Install only a reviewed commit of a named GitHub package."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid GitHub repository")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("npm git installation needs a full 40-character commit")
    return "git+https://github.com/" + repository + ".git#" + commit


def node_export(package, source):
    """Return the declared Node export for this exact catalog source, or None."""
    if not isinstance(package, dict):
        return None
    exports, files = package.get("exports"), package.get("files")
    if not isinstance(exports, dict) or not isinstance(files, list) or source not in files:
        return None
    # Do not install packages with install/prepare scripts in this passive pilot.
    scripts = package.get("scripts") or {}
    if not isinstance(scripts, dict) or any(k in scripts for k in (
        "preinstall", "install", "postinstall", "prepare", "prepack", "postpack"
    )):
        return None
    for key, target in exports.items():
        if key == "." or (isinstance(key, str) and key.startswith("./")):
            if target == "./" + source:
                return key
    return None


def commit_url(repository, ref):
    return "https://api.github.com/repos/" + repository + "/commits/" + quote(ref, safe="")


def raw_url(repository, sha, source):
    return (
        "https://raw.githubusercontent.com/"
        + repository + "/" + sha + "/" + quote(source, safe="/")
    )


def http_get(url):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read(MAX_BYTES + 1)
            status = response.status
    except HTTPError as error:
        data = error.read(MAX_BYTES + 1)
        status = error.code
    if len(data) > MAX_BYTES:
        raise RuntimeError("response exceeds byte limit")
    return status, data


def _python_satisfies(specifier, version):
    if specifier is None:
        return True, ""
    if not isinstance(specifier, str):
        return False, "requires-python is not a string"
    match = re.fullmatch(r">=(\d+)\.(\d+)(?:\.(\d+))?", specifier.strip())
    if not match:
        return False, "requires-python is not a single lower bound"
    required = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    if version < required:
        return False, "requires-python " + specifier.strip() + " is not satisfied by this interpreter"
    return True, ""


def installable(text, version):
    """Whether ``uv pip install`` of this repository root is in the pilot."""
    import tomllib

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False, "pyproject.toml could not be parsed"
    if not isinstance(data, dict):
        return False, "pyproject.toml could not be parsed"
    uv_tool = data.get("tool", {}).get("uv", {}) if isinstance(data.get("tool"), dict) else {}
    if isinstance(uv_tool, dict) and uv_tool.get("package") is False:
        return (
            False,
            "tool.uv package is false, so this repository is not an installable project yet. "
            "uv pip install of the git URL still attempts a build and fails. "
            "uv management waits on a project file in that git tree",
        )
    if "build-system" not in data:
        return (
            False,
            "no build-system table. uv pip install of the git URL falls back to setuptools "
            "discovery and does not install the file yet",
        )
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    dependencies = project.get("dependencies") or []
    if not isinstance(dependencies, list):
        return False, "project dependencies could not be read"
    if dependencies:
        return False, "project has third-party dependencies. uv install of this project is later"
    ok, reason = _python_satisfies(project.get("requires-python"), version)
    if not ok:
        return False, reason
    if not project.get("name"):
        return False, "pyproject has no project name"
    return (
        True,
        "installable project at the repository root. The source path is a file in that "
        "project, not a uv subdirectory",
    )


def needs_probe(source, env):
    suffix = Path(source).suffix
    if suffix == ".py":
        return not (env.no_site or env.python < UV_FLOOR or env.offline or not env.uv)
    if suffix in NODE_SUFFIXES:
        return not (env.node is None or env.node < NODE_FLOOR or env.offline)
    return False


def classify(source, env, probe):
    suffix = Path(source).suffix
    if suffix == ".py":
        if env.no_site:
            return Decision("skip", "python -S lane does not use uv")
        if env.python < UV_FLOOR:
            shown = ".".join(str(part) for part in env.python[:2])
            return Decision(
                "skip",
                "Python " + shown + " is below the uv pilot floor 3.11. uv was not invoked",
            )
        if env.offline:
            return Decision("skip", "offline. uv git install was not run")
        if not env.uv:
            return Decision("skip", "uv is not on PATH")
        if isinstance(probe, Exception):
            return Decision("error", one_line(probe))
        if probe is None or probe.pyproject is None:
            return Decision(
                "skip",
                "no pyproject.toml at the repository root. uv cannot install this file "
                "from a git URL until that repository contains a project file",
            )
        ok, reason = installable(probe.pyproject, env.python)
        return Decision("uv" if ok else "skip", reason)
    if suffix in NODE_SUFFIXES:
        if env.node is None:
            return Decision("skip", "node is not on PATH")
        if env.node < NODE_FLOOR:
            return Decision("skip", "node " + str(env.node) + " is older than 24")
        if env.offline:
            return Decision("skip", "offline. node fetch was not run")
        if isinstance(probe, Exception):
            return Decision("error", one_line(probe))
        if probe is None or probe.package_json is None:
            return Decision("fetch", "no package.json. npm git dependency does not apply. plain fetch")
        # 'private': true blocks npm registry publication, NOT git installs.
        # Use npm only when the exact catalog source is explicitly exported and
        # included in the packed files. Otherwise preserve raw-fetch semantics.
        exported = node_export(probe.package_json, source)
        if exported and isinstance(probe.package_json.get("name"), str):
            return Decision("npm", "explicitly exported Node entry; npm Git dependency")
        return Decision("fetch", "source is not an exported packed Node entry; plain fetch")
    return Decision(
        "skip",
        "not a Python or Node entry, so uv does not install it. "
        "Copy the file from the GitHub repo for a standalone copy",
    )


def load_probe(repository, ref, want_python, want_node, opener):
    pyproject = None
    package_json = None
    if want_python:
        status, body = opener(pyproject_url(repository, ref))
        if status == 200:
            pyproject = body.decode("utf-8")
        elif status != 404:
            raise RuntimeError("pyproject.toml request failed with status " + str(status))
    if want_node:
        status, body = opener(package_json_url(repository, ref))
        if status == 200:
            parsed = json.loads(body.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise RuntimeError("package.json was not an object")
            package_json = parsed
        elif status != 404:
            raise RuntimeError("package.json request failed with status " + str(status))
    return Probe(pyproject, package_json)


def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def fetch_bytes(repository, ref, source, opener):
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        sha = ref
    else:
        status, body = opener(commit_url(repository, ref))
        if status != 200:
            raise RuntimeError("commit lookup failed with status " + str(status))
        sha = json.loads(body.decode("utf-8")).get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError("commit lookup did not return a sha")
    status, body = opener(raw_url(repository, sha, source))
    if status != 200:
        raise RuntimeError("fetch failed with status " + str(status))
    return sha, body


def fetch_source(repository, ref, source, opener):
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        sha = ref
    else:
        status, body = opener(commit_url(repository, ref))
        if status != 200:
            raise RuntimeError("commit lookup failed with status " + str(status))
        sha = json.loads(body.decode("utf-8")).get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError("commit lookup did not return a sha")
    status, body = opener(raw_url(repository, sha, source))
    if status != 200:
        raise RuntimeError("fetch failed with status " + str(status))
    return sha, hashlib.sha256(body).hexdigest()


def _venv_python(venv):
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _resolved_commit(venv, repository):
    found = []
    seen = set()
    for path in venv.glob("**/direct_url.json"):
        if not path.parent.name.endswith(".dist-info"):
            continue
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    chosen = None
    for path in found:
        info = json.loads(path.read_text(encoding="utf-8"))
        url = info.get("url") or ""
        if repository in url:
            chosen = info
            break
    if chosen is None and len(found) == 1:
        chosen = json.loads(found[0].read_text(encoding="utf-8"))
    if not isinstance(chosen, dict):
        raise RuntimeError("uv install did not write direct_url.json")
    commit = (chosen.get("vcs_info") or {}).get("commit_id")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("uv install did not record a resolved commit")
    return commit


def uv_install(repository, ref, modules):
    """Install the repository with uv and import the named modules.

    The environment is temporary. uv may cache the checkout. It is not asked
    for a lockfile, and this function does not write one into the repository.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH")
    spec = git_spec(repository, ref)
    with tempfile.TemporaryDirectory(prefix="vendor-dispatch-") as tmp:
        root = Path(tmp)
        venv = root / "venv"
        subprocess.run([uv, "venv", "--python", sys.executable, os.fspath(venv)], cwd=root, check=True)
        python = _venv_python(venv)
        completed = subprocess.run(
            [uv, "pip", "install", "--python", os.fspath(python), spec],
            cwd=root, text=True, capture_output=True,
        )
        if completed.returncode != 0:
            detail = one_line(completed.stderr)[-300:]
            raise RuntimeError("uv pip install failed: " + detail)
        commit = _resolved_commit(venv, repository)
        failures = {}
        for module in modules:
            if not module.isidentifier():
                failures[module] = "module name is not an identifier"
                continue
            trial = subprocess.run(
                [os.fspath(python), "-c", "import " + module],
                text=True, capture_output=True,
            )
            if trial.returncode != 0:
                failures[module] = one_line(trial.stderr)[-200:] or "import failed"
        return commit, failures


def npm_install(repository, commit, package_name, exported_paths):
    """Probe a pinned Git dependency in an isolated temporary npm consumer.

    No consumer lockfile is edited here. The child must commit its own
    package.json/package-lock.json and use npm ci after opting in.
    """
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        raise RuntimeError("npm and Node 24+ are required for npm Git install")
    if not re.fullmatch(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+", package_name):
        raise RuntimeError("invalid npm package name")
    with tempfile.TemporaryDirectory(prefix="vendor-dispatch-npm-") as tmp:
        root = Path(tmp)
        (root / "package.json").write_text(
            json.dumps({"name": "vendor-dispatch-probe", "version": "0.0.0",
                        "private": True}), encoding="utf-8",
        )
        spec = npm_git_spec(repository, commit)
        result = subprocess.run(
            [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund",
             "--save=false", spec], cwd=root, text=True, capture_output=True,
            timeout=180,
        )
        if result.returncode:
            raise RuntimeError("npm git install failed: " + one_line(result.stderr)[-300:])
        for exported in exported_paths:
            suffix = "" if exported == "." else "/" + exported[2:]
            request = package_name + suffix
            js = ("const mod = require(" + json.dumps(request) + ");" +
                  "if (mod === undefined) process.exit(3)")
            trial = subprocess.run(
                [node, "-e", js], cwd=root, text=True, capture_output=True,
                timeout=20,
            )
            if trial.returncode:
                raise RuntimeError("installed npm export unavailable: " + request +
                                   " " + one_line(trial.stderr)[-180:])


LOCK_SCHEMA = "vendor-lock/1"
CATALOG_SCHEMA = "vendor-catalog/1"


def entries_of(manifest):
    if manifest.get("schema") == LOCK_SCHEMA:
        return manifest["files"]
    return manifest["tools"]


def _key(tool):
    return (tool["repository"], tool["source"], tool.get("destination"))


def verify_lock(results, tools, opener):
    """Fetch every non-offline lock row and compare with its recorded digest."""
    by_key = {_key(tool): tool for tool in tools}
    for row in results:
        if row.get("offline"):
            continue
        tool = by_key[_key(row)]
        if row["action"] == "error":
            continue
        try:
            commit, body = fetch_bytes(row["repository"], row["ref"], row["source"], opener)
        except (RuntimeError, URLError, json.JSONDecodeError, UnicodeError, OSError, KeyError) as exc:
            row["action"] = "error"
            row["reason"] = one_line(exc)
            continue
        if row["commit"] and row["commit"] != commit:
            row["action"] = "error"
            row["reason"] = "uv resolved " + row["commit"] + " but fetch resolved " + commit
            continue
        row["commit"] = commit
        row["sha256"] = hashlib.sha256(body).hexdigest()
        row["blob_sha"] = git_blob_sha(body)
        if commit == tool.get("commit"):
            if tool.get("sha256") != row["sha256"] or tool.get("blob_sha") != row["blob_sha"]:
                row["action"] = "error"
                row["reason"] = "recorded digest does not match the recorded commit"
                continue
            row["digest"] = "match"
        else:
            row["digest"] = "new commit"


def dispatch(catalog, env, resolve_ref, opener, install, dry_run=False, node_install=None):
    tools = entries_of(catalog)
    wanted = {}
    for tool in tools:
        if not needs_probe(tool["source"], env):
            continue
        ref = resolve_ref or tool["commit"]
        slot = wanted.setdefault((tool["repository"], ref), {"python": False, "node": False})
        suffix = Path(tool["source"]).suffix
        if suffix == ".py":
            slot["python"] = True
        elif suffix in NODE_SUFFIXES:
            slot["node"] = True
    probes = {}
    for (repository, ref), slot in wanted.items():
        try:
            probes[(repository, ref)] = load_probe(
                repository, ref, slot["python"], slot["node"], opener,
            )
        except (RuntimeError, URLError, json.JSONDecodeError, UnicodeError, OSError) as exc:
            probes[(repository, ref)] = exc
    results = []
    groups = {}
    node_groups = {}
    for tool in tools:
        ref = resolve_ref or tool["commit"]
        probe = probes.get((tool["repository"], ref))
        decision = classify(tool["source"], env, probe)
        row = {
            "repository": tool["repository"],
            "source": tool["source"],
            "ref": ref,
            "action": decision.action,
            "reason": decision.reason,
            "commit": None,
            "sha256": None,
            "recorded": False,
        }
        if tool.get("destination") is not None:
            row["destination"] = tool["destination"]
        if env.offline:
            row["offline"] = True
        if decision.action == "uv":
            row["spec"] = git_spec(tool["repository"], ref)
            if not dry_run:
                groups.setdefault((tool["repository"], ref), []).append(row)
        elif decision.action == "npm":
            row["export"] = node_export(probe.package_json, tool["source"])
            row["spec"] = "git+https://github.com/" + tool["repository"] + ".git#" + ref
            if not dry_run:
                node_groups.setdefault((tool["repository"], ref), []).append(row)
        results.append(row)
    if not dry_run:
        for (repository, ref), rows in groups.items():
            modules = [Path(row["source"]).stem for row in rows]
            try:
                commit, failures = install(repository, ref, modules)
            except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
                message = one_line(exc)
                for row in rows:
                    row["action"] = "error"
                    row["reason"] = message
                continue
            for row in rows:
                row["commit"] = commit
                failure = failures.get(Path(row["source"]).stem)
                if failure:
                    row["action"] = "error"
                    row["reason"] = failure
        for (repository, ref), rows in node_groups.items():
            package = probes[(repository, ref)].package_json
            name = package["name"]
            try:
                # Resolve the commit before invoking npm, never install a
                # moving branch while reporting a different source revision.
                for row in rows:
                    commit, digest = fetch_source(repository, ref, row["source"], opener)
                    row["commit"], row["sha256"] = commit, digest
                commits = {row["commit"] for row in rows}
                if len(commits) != 1:
                    raise RuntimeError("npm entries resolved to different commits")
                installer = node_install if node_install is not None else npm_install
                installer(repository, rows[0]["commit"], name, [r["export"] for r in rows])
            except (RuntimeError, URLError, json.JSONDecodeError, UnicodeError,
                    OSError, KeyError, subprocess.CalledProcessError) as exc:
                for row in rows:
                    row["action"] = "error"
                    row["reason"] = one_line(exc)
        for row in results:
            if row["action"] != "fetch":
                continue
            try:
                commit, digest = fetch_source(row["repository"], row["ref"], row["source"], opener)
            except (RuntimeError, URLError, json.JSONDecodeError, UnicodeError, OSError, KeyError) as exc:
                row["action"] = "error"
                row["reason"] = one_line(exc)
                continue
            row["commit"] = commit
            row["sha256"] = digest
        if catalog.get("schema") == LOCK_SCHEMA:
            verify_lock(results, tools, opener)
    return results


def apply_record(catalog, results):
    by_key = {_key(row): row for row in results}
    changed = []
    if catalog.get("schema") == LOCK_SCHEMA:
        for tool in catalog["files"]:
            row = by_key[_key(tool)]
            if row.get("digest") not in ("match", "new commit"):
                continue
            update = {
                "ref": row["commit"], "commit": row["commit"],
                "blob_sha": row["blob_sha"], "sha256": row["sha256"],
            }
            if any(tool.get(name) != value for name, value in update.items()):
                tool.update(update)
                changed.append(tool["destination"])
            row["recorded"] = True
        return changed
    for tool in catalog["tools"]:
        row = by_key[_key(tool)]
        if row["action"] not in ("uv", "npm", "fetch") or not row["commit"]:
            continue
        if tool["commit"] != row["commit"]:
            tool["commit"] = row["commit"]
            changed.append(tool["source"])
        row["recorded"] = True
    return changed


def _validate_catalog(catalog):
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import vendor_catalog

    vendor_catalog.validate(catalog)


def write_catalog(path, catalog):
    if catalog.get("schema") == CATALOG_SCHEMA:
        _validate_catalog(catalog)
    payload = (json.dumps(catalog, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _detect_node():
    if shutil.which("node") is None:
        return None
    try:
        out = subprocess.check_output(["node", "-p", "process.versions.node"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    major = out.split(".", 1)[0]
    if not major.isdigit():
        return None
    return int(major)


def current_env(assume_python, offline):
    if assume_python:
        parts = [int(part) for part in assume_python.split(".")]
        version = tuple(parts[:3] + [0] * (3 - len(parts[:3])))
    else:
        version = sys.version_info[:3]
    return Env(
        python=version,
        no_site=bool(sys.flags.no_site),
        offline=offline,
        uv=shutil.which("uv") is not None,
        node=_detect_node(),
    )


def write_summary(report):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    counts = {}
    for row in report["entries"]:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    lines = [
        "# Vendor dispatch",
        "",
        "Resolved commits are written into the catalog ``commit`` fields in this workspace.",
        "",
        "| action | repository | source | commit | reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["entries"]:
        reason = row["reason"].replace("|", "/")
        lines.append(
            "| {action} | {repository} | {source} | {commit} | {reason} |".format(
                action=row["action"],
                repository=row["repository"],
                source=row["source"],
                commit=row["commit"] or "",
                reason=reason,
            )
        )
    lines.append("")
    lines.append("Counts: " + ", ".join(key + "=" + str(counts[key]) for key in sorted(counts)))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def build_parser():
    parser = argparse.ArgumentParser(description="Dispatch vendor-catalog entries from their GitHub coordinates")
    parser.add_argument("--manifest", default=os.fspath(DEFAULT_MANIFEST))
    parser.add_argument("--resolve-ref", default=None, help="main, or a 40-character commit. Default: each entry commit")
    parser.add_argument("--record", action="store_true", help="write resolved commits back into the manifest")
    parser.add_argument("--dry-run", action="store_true", help="classify only; do not install or fetch")
    parser.add_argument("--offline", action="store_true", help="do not contact GitHub or uv")
    parser.add_argument("--assume-python", default=None, help="classify as this X.Y interpreter")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.resolve_ref is not None and args.resolve_ref != "main" and not re.fullmatch(
        r"[0-9a-f]{40}", args.resolve_ref
    ):
        raise SystemExit("resolve ref must be main or a 40-character sha")
    if args.assume_python is not None and not re.fullmatch(r"\d+\.\d+", args.assume_python):
        raise SystemExit("assume-python must be X.Y")
    if args.record and (args.dry_run or args.offline or args.assume_python):
        raise SystemExit("--record runs only for a real resolve on this interpreter")
    manifest = Path(args.manifest)
    catalog = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or catalog.get("schema") not in (CATALOG_SCHEMA, LOCK_SCHEMA):
        raise SystemExit("manifest must be vendor-catalog/1 or vendor-lock/1")
    if not isinstance(entries_of(catalog), list):
        raise SystemExit("manifest has no entry list")
    env = current_env(args.assume_python, args.offline)
    results = dispatch(
        catalog, env, args.resolve_ref, http_get, uv_install, dry_run=args.dry_run,
    )
    changed = []
    if args.record:
        changed = apply_record(catalog, results)
        write_catalog(manifest, catalog)
        print("recorded " + str(len(changed)) + " commits into " + manifest.name, file=sys.stderr)
    report = {
        "schema": "vendor-dispatch/1",
        "manifest": manifest.name,
        "resolve_ref": args.resolve_ref,
        "entries": results,
    }
    write_summary(report)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    if any(row["action"] == "error" for row in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
