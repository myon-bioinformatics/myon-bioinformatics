"""uv git-source pilot stays beside the vendor lock."""
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
PACKAGES = (
    ROOT / "packages" / "gh_workflow",
    ROOT / "packages" / "node_launch",
    ROOT / "packages" / "gh_ops",
)
CONSUMERS = (
    ROOT / "pilot" / "uv-upstream" / "pyproject.toml",
    ROOT / "pilot" / "uv-parent" / "pyproject.toml",
)
WORKFLOW = ROOT / ".github" / "workflows" / "uv-git-sources.yml"
SETUP_PYTHON = "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0"
SETUP_NODE = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0"
SETUP_UV = "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2"


def _resolver():
    spec = importlib.util.spec_from_file_location(
        "resolve_sources", ROOT / "pilot" / "resolve_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _uv():
    path = shutil.which("uv")
    if path is None:
        pytest.fail("uv is required for the git-source pilot")
    return path


def test_package_projects_are_py311_lower_bounds_only():
    for project in PACKAGES:
        data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
        assert data["project"]["requires-python"] == ">=3.11"
        assert data["project"]["dependencies"] == []
        requires = data["build-system"]["requires"]
        assert requires == ["setuptools>=69"]
        assert "==" not in requires[0]


def test_symlinks_point_at_standalone_files():
    expected = {
        ROOT / "packages" / "gh_workflow" / "gh_workflow.py": ROOT / "gh_workflow.py",
        ROOT / "packages" / "node_launch" / "node_launch.py": ROOT / "node_launch.py",
        ROOT / "packages" / "gh_ops" / "gh_ops.py": ROOT / "gh_ops.py",
        ROOT / "packages" / "gh_ops" / "gh_identity.py": ROOT / "vendor" / "gh_identity.py",
    }
    for link, target in expected.items():
        assert link.is_symlink()
        assert link.resolve() == target
        assert target.is_file()
        assert not target.is_symlink()


def test_consumers_track_branch_main_without_a_sha():
    upstream = tomllib.loads(CONSUMERS[0].read_text(encoding="utf-8"))
    parent = tomllib.loads(CONSUMERS[1].read_text(encoding="utf-8"))
    for data in (upstream, parent):
        assert data["project"]["requires-python"] == ">=3.11"
        assert data["tool"]["uv"]["package"] is False
        for spec in data["tool"]["uv"]["sources"].values():
            assert spec["branch"] == "main"
            assert "rev" not in spec
            assert spec["git"].startswith("https://github.com/myon-bioinformatics/")
            assert "==" not in spec["git"]
    assert upstream["tool"]["uv"]["sources"]["browser-test-kit-tools"]["git"].endswith("/browser-test-kit")
    sources = parent["tool"]["uv"]["sources"]
    assert sources["gh-workflow"]["subdirectory"] == "packages/gh_workflow"
    assert sources["node-launch"]["subdirectory"] == "packages/node_launch"
    assert sources["gh-ops"]["subdirectory"] == "packages/gh_ops"
    assert "subdirectory" not in upstream["tool"]["uv"]["sources"]["browser-test-kit-tools"]


def test_vendor_lock_and_stdlib_contract_stay():
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    assert lock["schema"] == "vendor-lock/1"
    assert any(item["source"] == "gh_identity.py" and item["destination"] == "vendor/gh_identity.py"
               for item in lock["files"])
    subprocess.run(
        [sys.executable, "-S", "-c", "import gh_workflow, node_launch, gh_ops"],
        cwd=ROOT, check=True,
    )
    note = (ROOT / "docs" / "uv-git-source-migration.md").read_text(encoding="utf-8")
    assert 'branch = "main"' in note
    assert "uv lock --upgrade" in note
    assert "vendor.lock.json" in note
    template = (ROOT / "templates" / "vendor-child-ci.workflow.yml").read_text(encoding="utf-8")
    assert "uv lock" not in template


def test_workflow_pins_and_dynamic_main():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@v7" in text
    assert SETUP_PYTHON in text
    assert SETUP_NODE in text
    assert SETUP_UV in text
    assert 'node-version: "24"' in text
    assert "uv lock --upgrade" not in text
    assert "python pilot/resolve_sources.py upstream" in text
    assert "python pilot/resolve_sources.py parent --exercise-node" in text
    assert "uv run pytest" not in text
    assert "python -m pytest" not in text
    resolver = (ROOT / "pilot" / "resolve_sources.py").read_text(encoding="utf-8")
    assert '["uv", "lock", "--upgrade"]' in resolver
    assert "HEAD:refs/heads/main" in resolver
    assert "--rev" not in resolver


def test_revision_parser_reads_resolved_git_commits():
    module = _resolver()
    found = module.revisions(
        '\n'.join([
            'name = "gh-workflow"',
            'source = { git = "https://github.com/myon-bioinformatics/myon-bioinformatics?branch=main#aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }',
            'name = "uv-parent-pilot"',
            'source = { virtual = "." }',
        ])
    )
    assert found == {"gh-workflow": "a" * 40}


def test_path_install_matches_standalone_bytes(tmp_path):
    uv = _uv()
    env = tmp_path / "venv"
    subprocess.run([uv, "venv", os.fspath(env), "--python", sys.executable], check=True)
    python = env / "bin" / "python"
    for project in PACKAGES:
        subprocess.run([uv, "pip", "install", "--python", os.fspath(python), os.fspath(project)], check=True)
    code = """
import os
import pathlib
import gh_ops, gh_workflow, node_launch
root = pathlib.Path(os.environ["ROOT"])
assert pathlib.Path(gh_workflow.__file__).read_bytes() == (root / "gh_workflow.py").read_bytes()
assert pathlib.Path(node_launch.__file__).read_bytes() == (root / "node_launch.py").read_bytes()
assert pathlib.Path(gh_ops.__file__).read_bytes() == (root / "gh_ops.py").read_bytes()
identity = pathlib.Path(gh_ops.gh_identity.__file__)
assert identity.read_bytes() == (root / "vendor" / "gh_identity.py").read_bytes()
assert identity.parent == pathlib.Path(gh_ops.__file__).parent
"""
    subprocess.run(
        [os.fspath(python), "-c", code],
        cwd=tmp_path, check=True, env={**os.environ, "ROOT": os.fspath(ROOT)},
    )


def test_local_branch_main_resolves_the_commit_under_test(tmp_path):
    uv = _uv()
    assert shutil.which("git")
    previous = os.environ.get("PATH", "")
    os.environ["PATH"] = os.path.dirname(uv) + os.pathsep + previous
    for relative in ("gh_workflow.py", "node_launch.py", "gh_ops.py", "vendor/gh_identity.py"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    shutil.copytree(ROOT / "packages", tmp_path / "packages", symlinks=True)
    shutil.copytree(ROOT / "pilot" / "uv-parent", tmp_path / "pilot" / "uv-parent")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=dev@localhost", "-c", "user.name=dev", "commit", "-m", "pilot"],
        cwd=tmp_path, check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True,
    ).stdout.strip()
    try:
        found = _resolver().resolve_parent(tmp_path, force_local=True, exercise_node=False)
    finally:
        os.environ["PATH"] = previous
    assert found == {
        "gh-ops": head,
        "gh-workflow": head,
        "node-launch": head,
    }
