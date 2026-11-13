"""uv git-source catalog dispatch and the single-file stdlib uv tool builder."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from tools import uv_tool_build as utb


# ═══ · uv git sources ════════════════════════════════════════════════════════════════════════════════════
# Catalog dispatch pilot: one JSON, no extra package projects.

WORKFLOW = ROOT / ".github" / "workflows" / "uv-git-sources.yml"
SETUP_NODE = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0"
SETUP_UV = "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3"
INSTALLABLE = """
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "browser-test-kit-tools"
version = "0.1.0"
requires-python = ">=3.9"

[dependency-groups]
dev = ["pytest>=9"]
"""
PACKAGE_FALSE = """
[project]
name = "gh-identity-dev"
version = "0.0.0"
requires-python = ">=3.10"
dependencies = []

[tool.uv]
package = false
"""
WITH_DEPS = """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mcp-toolcall-lab"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastmcp>=4.1.1"]
"""


def _driver():
    spec = importlib.util.spec_from_file_location(
        "vendor_dispatch", ROOT / "pilot" / "vendor_dispatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool(repository, source, commit=None):
    return {
        "repository": repository,
        "commit": commit or ("a" * 40),
        "reason": "pilot fixture",
        "source": source,
        "consumers": {},
    }


def _catalog():
    return {
        "schema": "vendor-catalog/1",
        "tools": [
            _tool("myon-bioinformatics/browser-test-kit", "scripts/check_evidence.py"),
            _tool("myon-bioinformatics/gh_identity", "gh_identity.py"),
            _tool("myon-bioinformatics/mcp-toolcall-lab", "src/mcp_toolcall_lab/github_public_resolver.py"),
            _tool("myon-bioinformatics/myon-bioinformatics", "py_launch.cjs"),
            _tool("myon-bioinformatics/browser-test-kit", "scripts/interaction.mjs"),
        ],
    }


def _env(module, **overrides):
    values = dict(python=(3, 12, 0), no_site=False, offline=False, uv=True, node=26)
    values.update(overrides)
    return module.Env(**values)


def _opener(url):
    if url.endswith("/pyproject.toml"):
        if "/browser-test-kit/" in url:
            return 200, INSTALLABLE.encode()
        if "/gh_identity/" in url:
            return 200, PACKAGE_FALSE.encode()
        if "/mcp-toolcall-lab/" in url:
            return 200, WITH_DEPS.encode()
        return 404, b"missing"
    if url.endswith("/package.json"):
        if "/browser-test-kit/" in url:
            return 200, b'{"private": true, "devDependencies": {"playwright": "1.64.0"}}'
        if "/myon-bioinformatics/myon-bioinformatics/" in url:
            return 200, json.dumps({
                "name": "@myon-bioinformatics/portable-tools", "private": True,
                "files": ["py_launch.cjs", "LICENSE"],
                "exports": {".": "./py_launch.cjs", "./py_launch": "./py_launch.cjs"},
            }).encode("utf-8")
        return 404, b"missing"
    if "/commits/" in url:
        return 200, json.dumps({"sha": "b" * 40}).encode()
    if url.endswith("/py_launch.cjs"):
        return 200, b"console.log('ok')\n"
    if url.endswith("/scripts/interaction.mjs"):
        return 200, b"export {}\n"
    return 404, b"missing"


def test_git_spec_names_repository_ref_and_optional_subdirectory():
    module = _driver()
    assert module.git_spec("myon-bioinformatics/browser-test-kit", "main") == (
        "git+https://github.com/myon-bioinformatics/browser-test-kit@main"
    )
    assert module.git_spec(
        "myon-bioinformatics/browser-test-kit", "ab" * 20, subdirectory="scripts",
    ).endswith("@" + ("ab" * 20) + "#subdirectory=scripts")


def test_installable_project_and_explicit_skips():
    module = _driver()
    ok, reason = module.installable(INSTALLABLE, (3, 12, 0))
    assert ok and "repository root" in reason
    ok, reason = module.installable(PACKAGE_FALSE, (3, 12, 0))
    assert not ok and "package is false" in reason
    ok, reason = module.installable(WITH_DEPS, (3, 12, 0))
    assert not ok and "third-party" in reason and "later" in reason
    high = INSTALLABLE.replace('requires-python = ">=3.9"', 'requires-python = ">=3.12"')
    ok, reason = module.installable(high, (3, 11, 0))
    assert not ok and "requires-python" in reason


def test_node_export_requires_an_explicit_packed_source_and_no_lifecycle_scripts():
    module = _driver()
    private = {"name": "@myon-bioinformatics/portable-tools", "private": True,
               "files": ["py_launch.cjs"],
               "exports": {"./py_launch": "./py_launch.cjs"}}
    assert module.node_export(private, "py_launch.cjs") == "./py_launch"
    assert module.classify("py_launch.cjs", _env(module),
                           module.Probe(None, private)).action == "npm"
    assert module.node_export(dict(private, files=[]), "py_launch.cjs") is None
    assert module.node_export(dict(private, scripts={"prepare": "echo bad"}),
                              "py_launch.cjs") is None
    assert module.node_export(dict(private, exports={"./other": "./other.cjs"}),
                              "py_launch.cjs") is None
    commit = "a" * 40
    assert module.npm_git_spec("myon-bioinformatics/myon-bioinformatics", commit).endswith(
        ".git#" + commit
    )
    import pytest
    with pytest.raises(ValueError):
        module.npm_git_spec("myon-bioinformatics/myon-bioinformatics", "main")


def test_npm_probe_failure_does_not_claim_success():
    module = _driver()
    def broken(*args):
        raise RuntimeError("install deliberately failed")
    rows = module.dispatch(_catalog(), _env(module), "main", _opener,
                           lambda *args: ("c" * 40, {}), node_install=broken)
    affected = next(row for row in rows if row["source"] == "py_launch.cjs")
    assert affected["action"] == "error"
    assert "deliberately failed" in affected["reason"]


def test_environment_skips_are_explicit():
    module = _driver()
    assert module.classify("gh_ops.py", _env(module, no_site=True), None).reason.startswith("python -S")
    low = module.classify("gh_ops.py", _env(module, python=(3, 9, 0)), None)
    assert low.action == "skip" and "3.9" in low.reason and "uv was not invoked" in low.reason
    missing_uv = module.classify("gh_ops.py", _env(module, uv=False), None)
    assert missing_uv.reason == "uv is not on PATH"
    old_node = module.classify("py_launch.cjs", _env(module, node=22), None)
    assert old_node.action == "skip" and "older than 26" in old_node.reason
    no_node = module.classify("py_launch.cjs", _env(module, node=None), None)
    assert no_node.reason == "node is not on PATH"
    asset = module.classify("LICENSE", _env(module), None)
    assert asset.action == "skip" and "Copy the file from the GitHub repo" in asset.reason


def test_dispatch_installs_fetches_and_records(tmp_path):
    module = _driver()
    catalog = _catalog()
    calls = []

    def install(repository, ref, modules):
        calls.append((repository, ref, tuple(modules)))
        return "c" * 40, {}

    node_calls = []

    def install_node(repository, commit, package, exported):
        node_calls.append((repository, commit, package, tuple(exported)))

    results = module.dispatch(catalog, _env(module), "main", _opener, install,
                              dry_run=False, node_install=install_node)
    by_source = {row["source"]: row for row in results}
    assert calls == [(
        "myon-bioinformatics/browser-test-kit",
        "main",
        ("check_evidence",),
    )]
    uv_row = by_source["scripts/check_evidence.py"]
    assert uv_row["action"] == "uv"
    assert uv_row["commit"] == "c" * 40
    assert uv_row["spec"].endswith("browser-test-kit@main")
    assert by_source["gh_identity.py"]["action"] == "skip"
    assert "package is false" in by_source["gh_identity.py"]["reason"]
    assert by_source["src/mcp_toolcall_lab/github_public_resolver.py"]["action"] == "skip"
    installed = by_source["py_launch.cjs"]
    assert installed["action"] == "npm" and installed["commit"] == "b" * 40
    assert installed["sha256"] and installed["export"] == "."
    assert node_calls == [(
        "myon-bioinformatics/myon-bioinformatics", "b" * 40,
        "@myon-bioinformatics/portable-tools", (".",),
    )]
    private = by_source["scripts/interaction.mjs"]
    assert private["action"] == "fetch" and "not an exported" in private["reason"]
    changed = module.apply_record(catalog, results)
    assert "scripts/check_evidence.py" in changed
    assert "py_launch.cjs" in changed
    assert catalog["tools"][1]["commit"] == "a" * 40
    module._validate_catalog(catalog)
    manifest = tmp_path / "vendor-catalog.json"
    module.write_catalog(manifest, catalog)
    reloaded = json.loads(manifest.read_text(encoding="utf-8"))
    assert reloaded["tools"][0]["commit"] == "c" * 40
    assert set(reloaded) == {"schema", "tools"}


def test_dry_run_does_not_install():
    module = _driver()

    def install(repository, ref, modules):
        raise AssertionError("dry-run must not install")

    results = module.dispatch(_catalog(), _env(module), "main", _opener, install, dry_run=True)
    assert any(row["action"] == "uv" and row["commit"] is None for row in results)
    assert any(row["action"] == "npm" and row["commit"] is None for row in results)
    assert all(row["sha256"] is None for row in results)


def test_cli_skip_lanes_do_not_rewrite_the_catalog():
    before = (ROOT / "vendor-catalog.json").read_bytes()
    locked = (ROOT / "vendor.lock.json").read_bytes()
    for args in (
        [sys.executable, "-S", "pilot/vendor_dispatch.py", "--dry-run", "--offline"],
        [sys.executable, "pilot/vendor_dispatch.py", "--assume-python", "3.9", "--dry-run", "--offline"],
    ):
        completed = subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True)
        report = json.loads(completed.stdout)
        assert report["schema"] == "vendor-dispatch/1"
        python_rows = [row for row in report["entries"] if row["source"].endswith(".py")]
        assert python_rows
        if "-S" in args:
            assert all(row["action"] == "skip" and row["reason"].startswith("python -S") for row in python_rows)
        else:
            assert all(row["action"] == "skip" and "3.9" in row["reason"] for row in python_rows)
        node_rows = [row for row in report["entries"] if row["source"].endswith((".js", ".cjs", ".mjs"))]
        assert node_rows
        assert all(row["action"] == "skip" for row in node_rows)
    assert (ROOT / "vendor-catalog.json").read_bytes() == before
    assert (ROOT / "vendor.lock.json").read_bytes() == locked


def test_vendor_lock_and_template_stay():
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    assert lock["schema"] == "vendor-lock/1"
    assert any(
        item["source"] == "gh_identity.py" and item["destination"] == "vendor/gh_identity.py"
        for item in lock["files"]
    )
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    assert not any(path.startswith("packages/") for path in tracked)
    assert "pilot/resolve_sources.py" not in tracked
    assert not any(path.startswith("pilot/") and path.endswith("pyproject.toml") for path in tracked)
    assert (ROOT / "pilot" / "vendor_dispatch.py").is_file()
    driver = (ROOT / "pilot" / "vendor_dispatch.py").read_text(encoding="utf-8")
    assert "git commit" not in driver
    assert "git push" not in driver
    template = (ROOT / "templates" / "vendor-child-ci.workflow.yml").read_text(encoding="utf-8")
    assert "vendor_dispatch" not in template
    assert "uv lock" not in template
    assert "actions/setup-python" not in template
    assert "astral-sh/setup-uv@" in template
    assert 'python-version: "3.15"' in template
    assert "activate-environment: true" in template
    note = (ROOT / "docs" / "uv-git-source-migration.md").read_text(encoding="utf-8")
    assert "vendor-catalog.json" in note
    assert "package = false" in note
    assert "packages/" not in note
    assert "Move later" in note
    assert "gh_identity.py" in note
    assert "copy the file from the GitHub repo" in note
    assert "stays a standalone copy" not in note
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "copy the file from the GitHub repo" in readme
    assert "stays a standalone copy" not in driver


def test_workflow_resolves_main_and_skips_other_lanes():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1" in text
    assert "actions/setup-python" not in text
    assert SETUP_NODE in text
    assert SETUP_UV in text
    assert 'python-version: "3.15"' in text
    assert "activate-environment: true" in text
    assert 'node-version: "26"' in text
    assert "python -S pilot/vendor_dispatch.py --dry-run --offline" in text
    assert 'python pilot/vendor_dispatch.py --assume-python 3.9 --dry-run --offline' in text
    assert "python pilot/vendor_dispatch.py --resolve-ref main --record" in text
    assert "uv lock" not in text
    assert "uv run pytest" not in text
    assert "python -m pytest" not in text
    assert "packages/" not in text


def _lock_module():
    spec = importlib.util.spec_from_file_location("vendor_dispatch_lock", ROOT / "pilot" / "vendor_dispatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_child_lock_rows_are_verified_and_recorded_in_the_same_lock():
    import hashlib

    vd = _lock_module()
    body = b"print('hi')\n"
    old = "a" * 40
    new = "b" * 40
    lock = {"schema": "vendor-lock/1", "files": [
        {"repository": "o/r", "ref": old, "commit": old, "source": "tool.py",
         "destination": "vendor/tool.py", "blob_sha": vd.git_blob_sha(body),
         "sha256": hashlib.sha256(body).hexdigest()},
        {"repository": "o/r", "ref": old, "commit": old, "source": "LICENSE",
         "destination": "vendor/r-LICENSE", "blob_sha": "0" * 40, "sha256": "0" * 64},
    ]}

    def opener(url):
        if url.endswith("/pyproject.toml"):
            return 404, b""
        if "/commits/main" in url:
            return 200, json.dumps({"sha": new}).encode()
        return 200, body

    env = vd.Env((3, 12, 0), False, False, True, 24)
    results = vd.dispatch(lock, env, None, opener, None)
    assert results[0]["action"] == "skip" and results[0]["digest"] == "match"
    assert results[1]["action"] == "error"
    assert "recorded digest" in results[1]["reason"]

    results = vd.dispatch(lock, env, "main", opener, None)
    assert [row["digest"] for row in results] == ["new commit", "new commit"]
    changed = vd.apply_record(lock, results)
    assert changed == ["vendor/tool.py", "vendor/r-LICENSE"]
    assert all(row["commit"] == new and row["ref"] == new for row in lock["files"])
    assert lock["files"][1]["sha256"] == hashlib.sha256(body).hexdigest()


def test_child_lock_offline_skips_without_fetching():
    vd = _lock_module()
    lock = {"schema": "vendor-lock/1", "files": [
        {"repository": "o/r", "ref": "a" * 40, "commit": "a" * 40, "source": "tool.py",
         "destination": "vendor/tool.py", "blob_sha": "0" * 40, "sha256": "0" * 64},
    ]}

    def opener(url):
        raise AssertionError("offline must not fetch")

    env = vd.Env((3, 12, 0), False, True, True, 24)
    results = vd.dispatch(lock, env, None, opener, None)
    assert results[0]["action"] == "skip" and "offline" in results[0]["reason"]


def test_missing_main_catalog_source_retains_only_verified_pin_with_explicit_opt_in():
    import copy
    vd = _driver()
    old, new = 'a' * 40, 'b' * 40
    manifest={'schema':'vendor-catalog/1','tools':[_tool('o/r','scripts/tool.mjs',old)]}
    def opener(url):
        if '/commits/' in url:return 200,json.dumps({'sha':new}).encode()
        if url.endswith('/package.json'):return 404,b''
        if f'/{old}/scripts/tool.mjs' in url:return 200,b'export {}\n'
        return 404,b''
    for enabled in (False,True):
        catalog=copy.deepcopy(manifest)
        rows=vd.dispatch(catalog,_env(vd),'main',opener,None,preserve_missing_catalog_sources=enabled)
        assert rows[0]['action']==('pinned' if enabled else 'error')
        if enabled:
            assert rows[0]['migration_required'] and rows[0]['commit']==old
            assert rows[0]['sha256'] and not rows[0]['recorded']
            assert vd.apply_record(catalog,rows)==[]
            assert catalog==manifest
    def failed(url):
        if f'/{new}/scripts/tool.mjs' in url:return 503,b''
        return opener(url)
    rows=vd.dispatch(manifest,_env(vd),'main',failed,None,preserve_missing_catalog_sources=True)
    assert rows[0]['action']=='error' and '503' in rows[0]['reason']
    def both_missing(url):
        if url.endswith('/scripts/tool.mjs'):return 404,b''
        return opener(url)
    rows=vd.dispatch(manifest,_env(vd),'main',both_missing,None,preserve_missing_catalog_sources=True)
    assert rows[0]['action']=='error' and 'recorded pin also failed' in rows[0]['reason']
    lock={'schema':'vendor-lock/1','files':[dict(manifest['tools'][0],destination='vendor/tool.mjs',ref=old,blob_sha='0'*40,sha256='0'*64)]}
    rows=vd.dispatch(lock,_env(vd),'main',opener,None,preserve_missing_catalog_sources=True)
    assert rows[0]['action']=='error'


# ═══ · uv tool build ═════════════════════════════════════════════════════════════════════════════════════
# Shared single-file stdlib -> uv tool builder (tools/uv_tool_build.py).

SCRIPT = ROOT / "tools" / "uv_tool_build.py"
ENTRY = '"""Example."""\nimport json, sys\n\n\ndef main(argv=None):\n    print(json.dumps({"argv": list(sys.argv[1:] if argv is None else argv)}))\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


def _entry(tmp_path: Path, name: str = "blueprobe.py", source: str = ENTRY) -> Path:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _pyproject(out: Path) -> dict:
    return tomllib.loads((out / "pyproject.toml").read_text(encoding="utf-8"))


def test_build_copies_entry_and_writes_pyproject(tmp_path):
    entry = _entry(tmp_path)
    out = tmp_path / "tool"
    result = utb.build([entry], out)
    assert (out / "blueprobe.py").read_bytes() == entry.read_bytes()
    data = _pyproject(out)
    assert data["build-system"] == {"requires": ["setuptools>=68"], "build-backend": "setuptools.build_meta"}
    assert data["project"]["name"] == "blueprobe"
    assert data["project"]["version"] == "0.1.0"
    assert data["project"]["requires-python"] == ">=3.11"
    assert data["project"]["dependencies"] == []
    assert data["project"]["scripts"] == {"blueprobe": "blueprobe:main"}
    assert data["tool"]["setuptools"]["py-modules"] == ["blueprobe"]
    assert result["schema"] == "uv-tool-build/1"
    assert result["scripts"] == {"blueprobe": "blueprobe:main"}
    assert result["entries"] == [{"module": "blueprobe", "imports": ["json", "sys"]}]
    assert sorted(p.name for p in out.iterdir()) == ["blueprobe.py", "pyproject.toml"]


@pytest.mark.parametrize("entry, command", [("blueprobe.py", "blueprobe"), ("dragowing.py", "dragowing"),
                                            ("Path/To/queryu.py", "queryu"), ("SakAnalytics.py", "sakanalytics")])
def test_default_command_is_lowercase_stem(entry, command):
    assert utb.default_command(entry) == command


def test_command_override_and_extra_scripts(tmp_path):
    entry = _entry(tmp_path, "sakanalytics.py")
    utb.build([entry], tmp_path / "o", command="sakanalytics", scripts=["sakanalytics-boundary=sakanalytics"],
              name="sakanalytics-cli", version="0.2.0", description="SakAnalytics \"stdlib\" CLI")
    data = _pyproject(tmp_path / "o")
    assert data["project"]["scripts"] == {"sakanalytics": "sakanalytics:main",
                                          "sakanalytics-boundary": "sakanalytics:main"}
    assert data["project"]["name"] == "sakanalytics-cli"
    assert data["project"]["version"] == "0.2.0"
    assert data["project"]["description"] == 'SakAnalytics "stdlib" CLI'
    utb.build([entry], tmp_path / "o2", command="sakanalytics-x")
    assert _pyproject(tmp_path / "o2")["project"]["scripts"] == {"sakanalytics-x": "sakanalytics:main"}


def test_multiple_entries_may_import_each_other(tmp_path):
    main_entry = _entry(tmp_path, "nagoyaction.py", "import local_forge\n\ndef main():\n    return 0\n")
    forge = _entry(tmp_path, "local_forge.py", "import os\n\ndef main():\n    return 0\n\ndef serve():\n    return 0\n")
    result = utb.build([main_entry, forge], tmp_path / "o",
                       scripts=["local-forge=local_forge", "forge-serve=local_forge:serve"])
    data = _pyproject(tmp_path / "o")
    assert data["project"]["name"] == "nagoyaction"
    assert data["tool"]["setuptools"]["py-modules"] == ["nagoyaction", "local_forge"]
    assert data["project"]["scripts"] == {"nagoyaction": "nagoyaction:main", "local_forge": "local_forge:main",
                                          "local-forge": "local_forge:main", "forge-serve": "local_forge:serve"}
    assert result["entries"][0]["imports"] == ["local_forge"]


def test_extra_modules_are_copied_without_commands(tmp_path):
    entry = _entry(tmp_path, "nagoyaction.py",
                   "def hub():\n    import local_forge, localci\n\ndef main():\n    return 0\n")
    forge = _entry(tmp_path, "local_forge.py", "import os\n")
    lint = _entry(tmp_path, "localci.py", "import json\n")
    result = utb.build([entry], tmp_path / "o", extra_modules=[forge, lint])
    data = _pyproject(tmp_path / "o")
    assert data["project"]["name"] == "nagoyaction"
    assert data["project"]["scripts"] == {"nagoyaction": "nagoyaction:main"}
    assert data["tool"]["setuptools"]["py-modules"] == ["nagoyaction", "local_forge", "localci"]
    assert (tmp_path / "o" / "local_forge.py").read_bytes() == forge.read_bytes()
    assert (tmp_path / "o" / "localci.py").read_bytes() == lint.read_bytes()
    assert result["scripts"] == {"nagoyaction": "nagoyaction:main"}
    assert result["modules"] == ["nagoyaction", "local_forge", "localci"]
    assert result["entries"][0]["imports"] == ["local_forge", "localci"]
    assert [item["module"] for item in result["extra_modules"]] == ["local_forge", "localci"]
    assert "local_forge" not in data["project"]["scripts"]
    assert "localci" not in data["project"]["scripts"]


def test_extra_module_does_not_require_main(tmp_path):
    entry = _entry(tmp_path)
    helper = _entry(tmp_path, "helper.py", "import json\nVALUE = 1\n")
    utb.build([entry], tmp_path / "o", extra_modules=[helper])
    assert _pyproject(tmp_path / "o")["project"]["scripts"] == {"blueprobe": "blueprobe:main"}
    assert (tmp_path / "o" / "helper.py").is_file()


@pytest.mark.parametrize("source, message", [
    ("import requests\n", "non-stdlib imports: requests"),
    ("def load():\n    from polars import DataFrame\n", "non-stdlib imports: polars"),
    ("from . import helper\n", "relative imports"),
    ("def broken(:\n", "syntax error"),
])
def test_rejects_extra_module_non_stdlib_and_relative_imports(tmp_path, source, message):
    out = tmp_path / "o"
    helper = _entry(tmp_path, "helper.py", source)
    with pytest.raises(utb.UvToolBuildError, match=message.replace("(", r"\(").replace(")", r"\)")):
        utb.build([_entry(tmp_path)], out, extra_modules=[helper])
    assert not out.exists()


def test_rejects_duplicate_module_names(tmp_path):
    entry = _entry(tmp_path, "nagoyaction.py")
    forge = _entry(tmp_path, "local_forge.py", "import os\n")
    with pytest.raises(utb.UvToolBuildError, match="duplicate module"):
        utb.build([entry], tmp_path / "o", extra_modules=[forge, forge])
    other = tmp_path / "other" / "local_forge.py"
    other.parent.mkdir()
    other.write_text("import os\n", encoding="utf-8")
    with pytest.raises(utb.UvToolBuildError, match="duplicate module"):
        utb.build([entry], tmp_path / "o2", extra_modules=[forge, other])
    clone = tmp_path / "also" / "nagoyaction.py"
    clone.parent.mkdir()
    clone.write_text(ENTRY, encoding="utf-8")
    with pytest.raises(utb.UvToolBuildError, match="duplicate module"):
        utb.build([entry], tmp_path / "o3", extra_modules=[clone])
    assert not (tmp_path / "o").exists()
    assert not (tmp_path / "o2").exists()
    assert not (tmp_path / "o3").exists()


def test_rejects_bad_extra_module_files(tmp_path):
    entry = _entry(tmp_path)
    with pytest.raises(utb.UvToolBuildError, match="module file name must be lowercase"):
        utb.build([entry], tmp_path / "o", extra_modules=[_entry(tmp_path, "Helper.py", "VALUE = 1\n")])
    with pytest.raises(utb.UvToolBuildError, match="module must be an existing .py"):
        utb.build([entry], tmp_path / "o2", extra_modules=[tmp_path / "missing.py"])
    with pytest.raises(utb.UvToolBuildError, match="module must be an existing .py"):
        utb.build([entry], tmp_path / "o3", extra_modules=[_entry(tmp_path, "helper.txt", "VALUE = 1\n")])
    with pytest.raises(utb.UvToolBuildError, match="at least one"):
        utb.build([], tmp_path / "o4", extra_modules=[_entry(tmp_path, "helper.py", "VALUE = 1\n")])


def test_script_may_target_extra_module(tmp_path):
    entry = _entry(tmp_path, "nagoyaction.py")
    forge = _entry(tmp_path, "local_forge.py", "def main():\n    return 0\n")
    result = utb.build([entry], tmp_path / "o", extra_modules=[forge], scripts=["local-forge=local_forge"])
    data = _pyproject(tmp_path / "o")
    assert data["project"]["scripts"] == {"nagoyaction": "nagoyaction:main", "local-forge": "local_forge:main"}
    assert "local_forge" not in data["project"]["scripts"]
    assert result["modules"] == ["nagoyaction", "local_forge"]
    bare = _entry(tmp_path, "helper.py", "VALUE = 1\n")
    with pytest.raises(utb.UvToolBuildError, match=r"no top-level main\(\)"):
        utb.build([entry], tmp_path / "o2", extra_modules=[bare], scripts=["help=helper"])


def test_cli_module_flag(tmp_path):
    entry = _entry(tmp_path, "nagoyaction.py")
    forge = _entry(tmp_path, "local_forge.py", "import os\n")
    lint = _entry(tmp_path, "localci.py", "import json\n")
    run = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), str(entry), "--module", str(forge), "--module", str(lint),
         "--out", str(tmp_path / "o"), "--json"],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["scripts"] == {"nagoyaction": "nagoyaction:main"}
    assert payload["modules"] == ["nagoyaction", "local_forge", "localci"]
    assert [item["module"] for item in payload["extra_modules"]] == ["local_forge", "localci"]
    assert _pyproject(tmp_path / "o")["project"]["scripts"] == {"nagoyaction": "nagoyaction:main"}
    assert _pyproject(tmp_path / "o")["tool"]["setuptools"]["py-modules"] == [
        "nagoyaction", "local_forge", "localci",
    ]


def test_optional_and_function_local_stdlib_imports_are_allowed(tmp_path):
    source = ("from __future__ import annotations\ntry:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
              "try:\n    import numpy as np\nexcept (ModuleNotFoundError, OSError):\n    np = None\n\n"
              "def main():\n    import statistics, xml.etree.ElementTree\n    return 0\n")
    utb.build([_entry(tmp_path, "pythdragoras.py", source)], tmp_path / "o")


@pytest.mark.parametrize("source, message", [
    ("import requests\n\ndef main():\n    return 0\n", "non-stdlib imports: requests"),
    ("def main():\n    from polars import DataFrame\n", "non-stdlib imports: polars"),
    ("try:\n    import yaml\nexcept KeyError:\n    pass\n\ndef main():\n    return 0\n", "non-stdlib imports: yaml"),
    ("from . import helper\n\ndef main():\n    return 0\n", "relative imports"),
    ("def run():\n    return 0\n", "no top-level main()"),
    ("class X:\n    def main(self):\n        return 0\n", "no top-level main()"),
    ("def main(:\n", "syntax error"),
])
def test_rejects_non_single_file_stdlib_entries(tmp_path, source, message):
    out = tmp_path / "o"
    with pytest.raises(utb.UvToolBuildError, match=message.replace("(", r"\(").replace(")", r"\)")):
        utb.build([_entry(tmp_path, "queryu.py", source)], out)
    assert not out.exists()


def test_script_function_must_exist(tmp_path):
    with pytest.raises(utb.UvToolBuildError, match=r"no top-level serve\(\)"):
        utb.build([_entry(tmp_path)], tmp_path / "o", scripts=["bp-serve=blueprobe:serve"])


@pytest.mark.parametrize("kwargs, message", [
    ({"command": "BlueProbe"}, "command must be lowercase"),
    ({"scripts": ["Bad=blueprobe"]}, "--script must be"),
    ({"scripts": ["x"]}, "--script must be"),
    ({"scripts": ["x=other"]}, "not a bundled module"),
    ({"scripts": ["blueprobe=blueprobe"]}, "duplicate command"),
    ({"name": "-bad"}, "invalid distribution name"),
    ({"version": "v1"}, "version must be"),
])
def test_rejects_bad_options(tmp_path, kwargs, message):
    with pytest.raises(utb.UvToolBuildError, match=message):
        utb.build([_entry(tmp_path)], tmp_path / "o", **kwargs)


def test_rejects_camelcase_missing_and_non_py_entries(tmp_path):
    with pytest.raises(utb.UvToolBuildError, match="must be lowercase"):
        utb.build([_entry(tmp_path, "BlueProbe.py")], tmp_path / "o")
    with pytest.raises(utb.UvToolBuildError, match="existing .py"):
        utb.build([tmp_path / "missing.py"], tmp_path / "o")
    with pytest.raises(utb.UvToolBuildError, match="existing .py"):
        utb.build([_entry(tmp_path, "blueprobe.txt")], tmp_path / "o")
    with pytest.raises(utb.UvToolBuildError, match="at least one"):
        utb.build([], tmp_path / "o")


def test_output_must_be_new_or_empty(tmp_path):
    entry = _entry(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    utb.build([entry], empty)
    with pytest.raises(utb.UvToolBuildError, match="new or empty"):
        utb.build([entry], empty)
    (tmp_path / "file").write_text("x")
    with pytest.raises(utb.UvToolBuildError, match="new or empty"):
        utb.build([entry], tmp_path / "file")


def test_cli_json_and_error_exit(tmp_path):
    entry = _entry(tmp_path)
    run = subprocess.run([sys.executable, "-S", str(SCRIPT), str(entry), "--out", str(tmp_path / "o"), "--json"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["scripts"] == {"blueprobe": "blueprobe:main"}
    bad = subprocess.run([sys.executable, "-S", str(SCRIPT), str(entry), "--out", str(tmp_path / "o")],
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "new or empty" in bad.stderr


def test_builder_itself_is_stdlib_only():
    utb.check_entry(SCRIPT)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_uv_tool_install_runs_command(tmp_path):
    out = tmp_path / "tool"
    utb.build([_entry(tmp_path)], out)
    env = dict(os.environ, UV_TOOL_DIR=str(tmp_path / "uv-tools"), UV_TOOL_BIN_DIR=str(tmp_path / "bin"))
    env.pop("UV_LOCKED", None)
    install = subprocess.run(["uv", "tool", "install", "--python", sys.executable, str(out)],
                             capture_output=True, text=True, env=env, check=False, timeout=300)
    assert install.returncode == 0, install.stderr
    exe = tmp_path / "bin" / ("blueprobe.exe" if os.name == "nt" else "blueprobe")
    run = subprocess.run([str(exe), "a b", "--x"], capture_output=True, text=True, check=False,
                         cwd=tmp_path, timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"argv": ["a b", "--x"]}


def test_allow_import_only_for_function_local_imports(tmp_path):
    lazy = "def main():\n    from queryu import Cache\n    import snapshot_source\n    return 0\n"
    with pytest.raises(utb.UvToolBuildError, match="--allow-import"):
        utb.build([_entry(tmp_path, "blueprobe.py", lazy)], tmp_path / "o")
    result = utb.build([_entry(tmp_path, "blueprobe.py", lazy)], tmp_path / "o",
                       allow_imports=["queryu", "snapshot_source"])
    assert result["allow_imports"] == ["queryu", "snapshot_source"]
    eager = "import queryu\n\ndef main():\n    return 0\n"
    with pytest.raises(utb.UvToolBuildError, match="non-stdlib imports: queryu"):
        utb.build([_entry(tmp_path, "blueprobe.py", eager)], tmp_path / "o2", allow_imports=["queryu"])
    with pytest.raises(utb.UvToolBuildError, match="--allow-import must be"):
        utb.build([_entry(tmp_path)], tmp_path / "o3", allow_imports=["a.b"])


@pytest.mark.parametrize("kind", ["def", "async def"])
def test_outer_try_does_not_make_deferred_import_optional(tmp_path, kind):
    source = (f"try:\n    {kind} helper():\n        import missing_dependency\n"
              "except ImportError:\n    pass\n\ndef main():\n    return helper()\n")
    out = tmp_path / "o"
    with pytest.raises(utb.UvToolBuildError, match="non-stdlib imports: missing_dependency"):
        utb.build([_entry(tmp_path, source=source)], out)
    assert not out.exists()


def test_function_own_try_still_allows_optional_import(tmp_path):
    source = ("try:\n    def helper():\n        try:\n            import missing_dependency\n"
              "        except ImportError:\n            pass\nexcept ImportError:\n    pass\n"
              "def main():\n    return helper()\n")
    utb.build([_entry(tmp_path, source=source)], tmp_path / "o")


@pytest.mark.parametrize("function", ["main", "serve"])
def test_async_console_entry_is_rejected_before_writing(tmp_path, function):
    source = "def main():\n    return 0\n" if function == "serve" else ""
    source += f"async def {function}():\n    return 0\n"
    out = tmp_path / "o"
    scripts = ["bp-serve=blueprobe:serve"] if function == "serve" else []
    with pytest.raises(utb.UvToolBuildError, match="async.*command entry point"):
        utb.build([_entry(tmp_path, source=source)], out, scripts=scripts)
    assert not out.exists()
