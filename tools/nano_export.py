#!/usr/bin/env python3
"""Export the reviewed Aoi research-runtime lock subset; never install or execute it.

Python 3.11+ stdlib. The two input blobs, not a complete checkout or ancestry,
are verified against the existing parent inventory. uv is a trusted local tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

INVENTORY = Path(__file__).resolve().parents[1] / "docs/stdlib-shrink-queue.json"
REPOSITORY = "myon-bioinformatics/Aoi"
LIMIT = 2 * 1024 * 1024
ROOT_PACKAGES = {"polars", "numpy", "httpx", "markdown"}
DEV_PACKAGES = {"pytest", "coverage", "selectolax"}


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError("expected a bounded regular file: " + path.name)
    data = path.read_bytes()
    if len(data) > LIMIT:
        raise ValueError("input grew beyond the size limit")
    return data


def _blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _inputs(source, inventory):
    catalog = _read(inventory)
    snapshot = json.loads(catalog)["current_snapshot"]
    if snapshot["schema"] != "nano-screening/1":
        raise ValueError("unsupported inventory snapshot")
    entries = [r for r in snapshot["repositories"] if r["repository"] == REPOSITORY]
    pins = [r for r in snapshot["offline_export_validation"]["results"]
            if r["repository"] == REPOSITORY]
    if len(entries) != 1 or len(pins) != 1:
        raise ValueError("expected one reviewed Aoi input record")
    entry, pin = entries[0], pins[0]
    if (entry["visibility"] != "public" or entry["private"] is not False
            or entry["archived"] is not False or entry["commit"] != pin["commit"]
            or not re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
            or pin["selected_group"] != "runtime"):
        raise ValueError("unsupported source identity or group")
    expected = pin["input_blobs_unchanged"]
    if set(expected) != {"pyproject.toml", "uv.lock"}:
        raise ValueError("expected exactly the two reviewed inputs")
    data = {}
    for name, digest in expected.items():
        if not re.fullmatch(r"[0-9a-f]{40}", digest):
            raise ValueError("invalid input blob identity")
        data[name] = _read(source / name)
        if _blob(data[name]) != digest:
            raise ValueError("reviewed input blob mismatch: " + name)
    project = tomllib.loads(data["pyproject.toml"].decode("utf-8"))
    lock = tomllib.loads(data["uv.lock"].decode("utf-8"))
    config = project["tool"]["uv"]
    if (project["project"]["name"] != "aoi" or project["project"].get("dependencies")
            or set(config) != {"default-groups"} or config["default-groups"] != ["dev", "runtime"]
            or set(project["dependency-groups"]) != {"dev", "runtime"}
            or lock["version"] != 1 or lock["revision"] != 5):
        raise ValueError("unsupported project/lock structure; review the recipe")
    own = [p for p in lock["package"] if p["name"] == "aoi"]
    if len(own) != 1 or own[0]["source"] != {"editable": "."}:
        raise ValueError("unsupported local project")
    for package in lock["package"]:
        if package["name"] != "aoi" and package["source"] != {"registry": "https://pypi.org/simple"}:
            raise ValueError("path, Git or workspace dependencies are not supported")
    return data, pin, hashlib.sha256(catalog).hexdigest()


def _run(argv, directory, env, destination, timeout):
    # Trusted uv only, not a general command runner or hostile-process sandbox.
    with destination.open("wb") as out, tempfile.TemporaryFile() as err:
        result = subprocess.run(argv, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, timeout=timeout, check=False)
        if result.returncode:
            raise ValueError("uv failed with exit " + str(result.returncode))
    return _read(destination)


def prepare(source, output_parent, *, uv="uv", timeout=60, inventory=INVENTORY):
    """Return a completed artifact directory; remove this invocation's output on failure."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    source = Path(source).resolve(strict=True)
    parent = Path(output_parent).resolve(strict=True)
    if not source.is_dir() or not parent.is_dir() or parent == source or source in parent.parents:
        raise ValueError("output parent must be an existing directory outside the input directory")
    data, pin, inventory_sha = _inputs(source, Path(inventory))
    executable = shutil.which(uv)
    if executable is None:
        raise ValueError("local uv executable not found")
    executable = str(Path(executable).resolve(strict=True))
    work = Path(tempfile.mkdtemp(prefix="aoi-runtime-", dir=parent))
    try:
        stage = work / "inputs"
        stage.mkdir(mode=0o700)
        for name, content in data.items():
            (stage / name).write_bytes(content)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("UV_", "PIP_", "GIT_"))
               and k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"}}
        env.update(UV_CACHE_DIR=str(work / "cache"), UV_NO_PROGRESS="1",
                   XDG_CONFIG_HOME=str(work / "config"))
        version_file = work / ".uv-version"
        version = _run([executable, "--version"], work, env, version_file, timeout).decode("utf-8").strip()
        if not version.startswith("uv ") or len(version) > 500:
            raise ValueError("unexpected uv version response")
        pending = work / ".requirements.tmp"
        argv = [executable, "--no-config", "export", "--project", str(stage),
                "--offline", "--locked", "--no-python-downloads", "--python", sys.executable,
                "--no-default-groups", "--group", "runtime", "--no-emit-project",
                "--no-header", "--format", "requirements.txt"]
        exported = _run(argv, work, env, pending, timeout)
        text = exported.decode("utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if (stripped and not stripped.startswith(("#", "--hash="))
                    and not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*==", stripped)):
                raise ValueError("non-registry requirement emitted")
        names = re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==", text, re.MULTILINE)
        if not ROOT_PACKAGES.issubset(names) or DEV_PACKAGES.intersection(names):
            raise ValueError("export does not match the runtime-only selection")
        # Do not publish success if either source or staged inputs changed.
        for name, content in data.items():
            if _read(source / name) != content or _read(stage / name) != content:
                raise ValueError("input changed during export: " + name)
        receipt = {
            "schema": "nano-locked-export/1", "recipe": "aoi-research-runtime",
            "source_repository": REPOSITORY, "inventory_source_commit": pin["commit"],
            "inventory_sha256": inventory_sha, "verified_input_blobs": pin["input_blobs_unchanged"],
            "verification_scope": "two_manifest_blobs_only", "checkout_head_verified": False,
            "uv_version": version, "python_version": sys.version.split()[0],
            "groups": ["runtime"], "default_groups": False, "project_emitted": False,
            "requirements_sha256": hashlib.sha256(exported).hexdigest(),
            "requirements_bytes": len(exported), "exported_requirement_rows": len(names),
            "exported_package_names": sorted(set(names)), "installations_performed": False,
            "workloads_executed": False, "installed_bytes": None, "install_seconds": None,
        }
        (work / ".receipt.tmp").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        version_file.unlink()
        shutil.rmtree(work / "cache", ignore_errors=True)
        pending.rename(work / "requirements.txt")
        (work / ".receipt.tmp").rename(work / "receipt.json")
        return work
    except BaseException:
        shutil.rmtree(work)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="directory containing the two reviewed Aoi inputs")
    parser.add_argument("--output-parent", required=True, type=Path, help="existing directory outside the input directory")
    parser.add_argument("--uv", default="uv", help="trusted installed uv executable (never downloaded)")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.source, args.output_parent, uv=args.uv)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("nano-export-error: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"directory": str(result), "receipt": str(result / "receipt.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
