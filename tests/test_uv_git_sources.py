"""Catalog dispatch pilot: one JSON, no extra package projects."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "uv-git-sources.yml"
SETUP_PYTHON = "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0"
SETUP_NODE = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0"
SETUP_UV = "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2"
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
    values = dict(python=(3, 12, 0), no_site=False, offline=False, uv=True, node=24)
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


def test_environment_skips_are_explicit():
    module = _driver()
    assert module.classify("gh_ops.py", _env(module, no_site=True), None).reason.startswith("python -S")
    low = module.classify("gh_ops.py", _env(module, python=(3, 9, 0)), None)
    assert low.action == "skip" and "3.9" in low.reason and "uv was not invoked" in low.reason
    missing_uv = module.classify("gh_ops.py", _env(module, uv=False), None)
    assert missing_uv.reason == "uv is not on PATH"
    old_node = module.classify("py_launch.cjs", _env(module, node=22), None)
    assert old_node.action == "skip" and "older than 24" in old_node.reason
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

    results = module.dispatch(catalog, _env(module), "main", _opener, install, dry_run=False)
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
    fetched = by_source["py_launch.cjs"]
    assert fetched["action"] == "fetch" and fetched["commit"] == "b" * 40
    assert fetched["sha256"]
    private = by_source["scripts/interaction.mjs"]
    assert private["action"] == "fetch" and "private" in private["reason"]
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
    assert "actions/checkout@v7" in text
    assert SETUP_PYTHON in text
    assert SETUP_NODE in text
    assert SETUP_UV in text
    assert 'node-version: "24"' in text
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
