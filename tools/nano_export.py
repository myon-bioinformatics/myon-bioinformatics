#!/usr/bin/env python3
"""Export a reviewed feature's locked requirements; never install or execute it.

Python 3.11+ stdlib. The two input blobs, not a complete checkout or ancestry,
are verified against explicit source pins and the existing parent inventory.
uv is a trusted local tool.
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
DRAGOWING_PIN = {
    "repository": "myon-bioinformatics/DRAgoWing",
    "commit": "be2185b142aebcbedee97688fe08ee0092179472",
    "input_blobs_unchanged": {
        "pyproject.toml": "ca7cd1f307080d4c369a5a14031ee2cb4f93f14d",
        "uv.lock": "2788b84741af5ec04d59e5074e4010e067495654",
    },
}
MCP_CLIENT_PIN = {
    "repository": "myon-bioinformatics/mcp-toolcall-lab",
    "commit": "06cdc8e4e14d50de45c8b8122a5ccff6911c3404",
    "input_blobs_unchanged": {
        "pyproject.toml": "4c3d89820b99fd11717ce0c454e0149226e8d8cd",
        "uv.lock": "6fd1a4b3a927120d9684d5d3a4e3941de7b13415",
    },
}
MCP_CLIENT_PACKAGES = frozenset((
    "annotated-types", "anyio", "attrs", "cffi", "click", "cryptography", "h11",
    "httpcore2", "httpx2", "httpx2-jsfetch", "idna", "jsonschema",
    "jsonschema-specifications", "mcp", "mcp-types", "opentelemetry-api",
    "pycparser", "pydantic", "pydantic-core", "pyjwt", "python-multipart",
    "pywin32", "referencing", "rpds-py", "sse-starlette", "starlette",
    "truststore", "typing-extensions", "typing-inspection", "uvicorn",
))
MARKDOWN_GRADIO_PIN = {
    "repository": "myon-bioinformatics/markdown",
    "commit": "f04d233d2edc63650439fbb7692434e9f2c66266",
    "input_blobs_unchanged": {
        "pyproject.toml": "031f0def5bebc211a37b85d6ab769ac27721f86e",
        "uv.lock": "39599c712c22eddc9b2d42bd0f0a13cab90b7f38",
    },
}
MARKDOWN_GRADIO_PACKAGES = frozenset((
    "annotated-doc", "annotated-types", "anyio", "audioop-lts", "brotli",
    "certifi", "click", "colorama", "fastapi", "filelock", "fsspec", "gradio",
    "gradio-client", "groovy", "h11", "hf-gradio", "hf-xet", "httpcore",
    "httpcore2", "httpx", "httpx2", "huggingface-hub", "idna", "jinja2",
    "markdown-it-py", "markupsafe", "mdurl", "numpy", "opentelemetry-api",
    "orjson", "packaging", "pandas", "pillow", "pydantic", "pydantic-core",
    "pydub", "pygments", "python-dateutil", "python-multipart", "pytz", "pyyaml",
    "rich", "safehttpx", "semantic-version", "shellingham", "six", "starlette",
    "tomlkit", "tqdm", "truststore", "typer", "typing-extensions",
    "typing-inspection", "tzdata", "uvicorn",
))


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


def _dragowing_inputs(source, inventory):
    """One reviewed Markdown-only recipe, not a dependency resolver."""
    catalog = _read(inventory)
    snapshot = json.loads(catalog)["current_snapshot"]
    entries = [r for r in snapshot["repositories"]
               if r["repository"] == DRAGOWING_PIN["repository"]]
    if snapshot["schema"] != "nano-screening/1" or len(entries) != 1:
        raise ValueError("expected one reviewed DRAgoWing record")
    entry = entries[0]
    if (entry["commit"] != DRAGOWING_PIN["commit"] or entry["private"] is not False
            or entry["visibility"] != "public" or entry["archived"] is not False):
        raise ValueError("unsupported DRAgoWing source identity")
    data = {name: _read(source / name)
            for name in DRAGOWING_PIN["input_blobs_unchanged"]}
    if any(_blob(data[name]) != digest
           for name, digest in DRAGOWING_PIN["input_blobs_unchanged"].items()):
        raise ValueError("reviewed DRAgoWing input blob mismatch")
    project = tomllib.loads(data["pyproject.toml"].decode("utf-8"))
    lock = tomllib.loads(data["uv.lock"].decode("utf-8"))
    if (project["project"]["name"] != "dragowing" or project["tool"]["uv"] != {"package": False}
            or set(project["dependency-groups"]) != {"dev"}
            or lock["version"] != 1 or lock["revision"] != 5):
        raise ValueError("unsupported DRAgoWing project/lock structure")
    own = [p for p in lock["package"] if p["name"] == "dragowing"]
    if len(own) != 1 or own[0]["source"] != {"virtual": "."}:
        raise ValueError("unsupported DRAgoWing local project")
    for package in lock["package"]:
        if package["name"] != "dragowing" and package["source"] != {"registry": "https://pypi.org/simple"}:
            raise ValueError("path, Git or workspace dependencies are not supported")
    return data, DRAGOWING_PIN, hashlib.sha256(catalog).hexdigest()


def _mcp_client_inputs(source, inventory):
    """Keep the real SDK closure, excluding only the separately hosted FastMCP server."""
    catalog = _read(inventory)
    snapshot = json.loads(catalog)["current_snapshot"]
    entries = [r for r in snapshot["repositories"]
               if r["repository"] == MCP_CLIENT_PIN["repository"]]
    if snapshot["schema"] != "nano-screening/1" or len(entries) != 1:
        raise ValueError("expected one reviewed MCP lab record")
    entry = entries[0]
    if (entry["commit"] != MCP_CLIENT_PIN["commit"] or entry["private"] is not False
            or entry["visibility"] != "public" or entry["archived"] is not False):
        raise ValueError("unsupported MCP lab source identity")
    data = {name: _read(source / name)
            for name in MCP_CLIENT_PIN["input_blobs_unchanged"]}
    if any(_blob(data[name]) != digest
           for name, digest in MCP_CLIENT_PIN["input_blobs_unchanged"].items()):
        raise ValueError("reviewed MCP lab input blob mismatch")
    project = tomllib.loads(data["pyproject.toml"].decode("utf-8"))
    lock = tomllib.loads(data["uv.lock"].decode("utf-8"))
    if (project["project"]["name"] != "mcp-toolcall-lab"
            or project["project"]["requires-python"] != ">=3.12"
            or project["project"]["dependencies"] != ["fastmcp>=4.1.3", "mcp>=2.3.0", "pydantic>=2.14.1"]
            or set(project["project"]["optional-dependencies"]) != {"galleria", "test", "browser-test"}
            or project.get("dependency-groups")
            or project["tool"]["uv"] != {"override-dependencies": [
                "pillow>=13.0.0", "safehttpx>=0.2.0", "tomlkit>=0.15.2", "pyee>=15.0.0"]}
            or lock["version"] != 1 or lock["revision"] != 5):
        raise ValueError("unsupported MCP lab project/lock structure")
    own = [p for p in lock["package"] if p["name"] == "mcp-toolcall-lab"]
    if len(own) != 1 or own[0]["source"] != {"editable": "."}:
        raise ValueError("unsupported MCP lab local project")
    for package in lock["package"]:
        if package["name"] != "mcp-toolcall-lab" and package["source"] != {"registry": "https://pypi.org/simple"}:
            raise ValueError("path, Git or workspace dependencies are not supported")
    return data, MCP_CLIENT_PIN, hashlib.sha256(catalog).hexdigest()


def _markdown_gradio_inputs(source, inventory):
    """Retain real Gradio; exclude the independent Streamlit/browser frontends."""
    catalog = _read(inventory)
    snapshot = json.loads(catalog)["current_snapshot"]
    entries = [r for r in snapshot["repositories"]
               if r["repository"] == MARKDOWN_GRADIO_PIN["repository"]]
    if snapshot["schema"] != "nano-screening/1" or len(entries) != 1:
        raise ValueError("expected one reviewed Markdown record")
    entry = entries[0]
    if (entry["commit"] != MARKDOWN_GRADIO_PIN["commit"] or entry["private"] is not False
            or entry["visibility"] != "public" or entry["archived"] is not False):
        raise ValueError("unsupported Markdown source identity")
    data = {name: _read(source / name)
            for name in MARKDOWN_GRADIO_PIN["input_blobs_unchanged"]}
    if any(_blob(data[name]) != digest
           for name, digest in MARKDOWN_GRADIO_PIN["input_blobs_unchanged"].items()):
        raise ValueError("reviewed Markdown input blob mismatch")
    project = tomllib.loads(data["pyproject.toml"].decode("utf-8"))
    lock = tomllib.loads(data["uv.lock"].decode("utf-8"))
    if (project["project"]["name"] != "md-market"
            or project["project"]["requires-python"] != ">=3.11"
            or project["project"]["dependencies"] != []
            or project["project"].get("optional-dependencies")
            or project["dependency-groups"] != {
                "dev": ["pytest>=8.4.2", "PyYAML>=6.0.3"],
                "frontend": ["gradio>=6.30.0", "streamlit>=1.66.0", "playwright>=1.64.0"]}
            or project["tool"]["uv"] != {
                "environments": ["sys_platform != 'emscripten' and python_version >= '3.11'"],
                "override-dependencies": ["pillow>=13.0.0", "safehttpx>=0.2.0", "tomlkit>=0.15.2"],
                "dependency-groups": {"dev": {"requires-python": ">=3.11"},
                                      "frontend": {"requires-python": ">=3.11"}}}
            or lock["version"] != 1 or lock["revision"] != 5):
        raise ValueError("unsupported Markdown project/lock structure")
    own = [p for p in lock["package"] if p["name"] == "md-market"]
    if len(own) != 1 or own[0]["source"] != {"editable": "."}:
        raise ValueError("unsupported Markdown local project")
    for package in lock["package"]:
        if package["name"] != "md-market" and package["source"] != {"registry": "https://pypi.org/simple"}:
            raise ValueError("path, Git or workspace dependencies are not supported")
    return data, MARKDOWN_GRADIO_PIN, hashlib.sha256(catalog).hexdigest()


def prepare(source, output_parent, *, uv="uv", timeout=60, inventory=INVENTORY,
            feature="aoi-research-runtime"):
    """Return a completed artifact directory; remove this invocation's output on failure."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    source = Path(source).resolve(strict=True)
    parent = Path(output_parent).resolve(strict=True)
    if not source.is_dir() or not parent.is_dir() or parent == source or source in parent.parents:
        raise ValueError("output parent must be an existing directory outside the input directory")
    if feature == "aoi-research-runtime":
        data, pin, inventory_sha = _inputs(source, Path(inventory))
        repository, groups, pruned = REPOSITORY, ["runtime"], []
    elif feature == "dragowing-html":
        data, pin, inventory_sha = _dragowing_inputs(source, Path(inventory))
        repository, groups, pruned = DRAGOWING_PIN["repository"], [], ["numpy", "polars"]
    elif feature == "mcp-chat-client":
        data, pin, inventory_sha = _mcp_client_inputs(source, Path(inventory))
        repository, groups, pruned = MCP_CLIENT_PIN["repository"], [], ["fastmcp"]
    elif feature == "markdown-gradio":
        data, pin, inventory_sha = _markdown_gradio_inputs(source, Path(inventory))
        repository, groups, pruned = MARKDOWN_GRADIO_PIN["repository"], ["frontend"], ["streamlit", "playwright"]
    else:
        raise ValueError("unsupported named feature")
    executable = shutil.which(uv)
    if executable is None:
        raise ValueError("local uv executable not found")
    executable = str(Path(executable).resolve(strict=True))
    prefix = "aoi-runtime-" if feature == "aoi-research-runtime" else feature + "-"
    work = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
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
                "--no-default-groups", "--no-emit-project",
                "--no-header", "--format", "requirements.txt"]
        for group in groups:
            argv += ["--group", group]
        for package in pruned:
            argv += ["--prune", package]
        exported = _run(argv, work, env, pending, timeout)
        text = exported.decode("utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if (stripped and not stripped.startswith(("#", "--hash="))
                    and not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*==", stripped)):
                raise ValueError("non-registry requirement emitted")
        names = re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==", text, re.MULTILINE)
        if feature == "dragowing-html" and names != ["markdown"]:
            raise ValueError("export does not match the Markdown-only selection")
        if feature == "aoi-research-runtime" and (not ROOT_PACKAGES.issubset(names) or DEV_PACKAGES.intersection(names)):
            raise ValueError("export does not match the runtime-only selection")
        if feature == "mcp-chat-client" and (set(names) != MCP_CLIENT_PACKAGES or len(names) != len(MCP_CLIENT_PACKAGES)):
            raise ValueError("export does not match the reviewed MCP SDK client closure")
        if feature == "markdown-gradio" and (
                set(names) != MARKDOWN_GRADIO_PACKAGES
                or len(names) != len(MARKDOWN_GRADIO_PACKAGES) + 1
                or names.count("numpy") != 2):
            raise ValueError("export does not match the reviewed Markdown Gradio closure")
        # Do not publish success if either source or staged inputs changed.
        for name, content in data.items():
            if _read(source / name) != content or _read(stage / name) != content:
                raise ValueError("input changed during export: " + name)
        receipt = {
            "schema": "nano-locked-export/1", "recipe": feature,
            "source_repository": repository, "inventory_source_commit": pin["commit"],
            "inventory_sha256": inventory_sha, "verified_input_blobs": pin["input_blobs_unchanged"],
            "verification_scope": "two_manifest_blobs_only", "checkout_head_verified": False,
            "uv_version": version, "python_version": sys.version.split()[0],
            "groups": groups, "default_groups": False, "project_emitted": False,
            "requirements_sha256": hashlib.sha256(exported).hexdigest(),
            "requirements_bytes": len(exported), "exported_requirement_rows": len(names),
            "exported_package_names": sorted(set(names)), "installations_performed": False,
            "workloads_executed": False, "installed_bytes": None, "install_seconds": None,
        }
        if pruned:
            receipt["pruned_packages"] = pruned
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
    parser.add_argument("--source", required=True, type=Path, help="directory containing the two reviewed inputs")
    parser.add_argument("--feature", choices=("aoi-research-runtime", "dragowing-html", "mcp-chat-client", "markdown-gradio"),
                        default="aoi-research-runtime", help="explicit reviewed feature; Aoi remains the default")
    parser.add_argument("--output-parent", required=True, type=Path, help="existing directory outside the input directory")
    parser.add_argument("--uv", default="uv", help="trusted installed uv executable (never downloaded)")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.source, args.output_parent, uv=args.uv, feature=args.feature)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("nano-export-error: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"directory": str(result), "receipt": str(result / "receipt.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
