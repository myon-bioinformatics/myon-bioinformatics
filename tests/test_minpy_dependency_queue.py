"""Compatibility of dependency discovery with shared modules and package parents."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import minpy

COMMIT = "a" * 40
REPO_ROOT = Path(__file__).resolve().parents[1]


def write(root: Path, name: str, source: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def collect(root: Path):
    return minpy._collect(root=root, entry_name="app", entry_path=root / "app.py",
                          search_roots=[root], third_roots=[], allow=set())


def shared_tree(root: Path) -> None:
    write(root, "app.py", "import left\nimport right\nimport pkg.shared\n")
    write(root, "left.py", "import pkg.shared\nimport pkg.shared\nVALUE = 1\n")
    write(root, "right.py", "from pkg import shared\nVALUE = 2\n")
    write(root, "pkg/__init__.py", "from . import shared\n")
    write(root, "pkg/shared.py", "from . import helper\nVALUE = 40\n")
    write(root, "pkg/helper.py", "VALUE = 2\n")


def test_shared_dependencies_preserve_fifo_order_edges_and_source(tmp_path):
    shared_tree(tmp_path)
    graph = collect(tmp_path)
    assert list(graph.modules) == ["app", "left", "right", "pkg.shared", "pkg", "pkg.helper"]
    assert graph.edges == {
        "app": {"left", "right", "pkg.shared"},
        "left": {"pkg.shared"}, "right": {"pkg", "pkg.shared"},
        "pkg.shared": {"pkg.helper"}, "pkg": {"pkg.shared"},
    }
    assert graph.modules["left"].source.count("import pkg.shared") == 2
    assert not graph.problems
    assert graph.library_external == graph.script_external == graph.third_party == []


def test_shared_invalid_source_keeps_first_discovery_error_order(tmp_path):
    write(tmp_path, "app.py", "import left\nimport right\nimport broken\nimport last\n")
    write(tmp_path, "left.py", "import broken\n")
    write(tmp_path, "right.py", "import broken\n")
    write(tmp_path, "broken.py", "if:\n")
    write(tmp_path, "last.py", "while:\n")
    graph = collect(tmp_path)
    assert list(graph.modules) == ["app", "left", "right"]
    assert graph.problems == ["syntax error in broken.py: invalid syntax",
                              "syntax error in last.py: invalid syntax"]
    assert graph.edges["left"] == graph.edges["right"] == {"broken"}


def test_cycle_through_shared_dependency_is_still_rejected(tmp_path):
    shared_tree(tmp_path)
    write(tmp_path, "pkg/helper.py", "import left\n")
    with pytest.raises(minpy.MinpyError, match=r"circular import: left -> pkg.shared -> pkg.helper -> left"):
        minpy.bundle(root=tmp_path, entry="app.py", repository="example/widget",
                     commit=COMMIT, skip_head_check=True)


@pytest.mark.parametrize("entry, expected_rc", [("app.py", 0), ("broken.py", 1)])
def test_stdlib_cli_script_and_oneliner_contracts(tmp_path, entry, expected_rc):
    shared_tree(tmp_path)
    write(tmp_path, "broken.py", "if:\n")
    output = tmp_path / "out.py"
    args = ["bundle", "--root", str(tmp_path), "--entry", entry,
            "--repository", "example/widget", "--commit", COMMIT,
            "--skip-head-check", "--output", str(output)]
    runs, digests = [], []
    for seed, command in [
        ("1", [sys.executable, "-S", "tools/minpy.py"]),
        ("321", [sys.executable, "-S", "-c",
                 "from tools.minpy import main; raise SystemExit(main())"]),
    ]:
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(command + args, cwd=REPO_ROOT, input="stdin remains unused\n",
                                capture_output=True, text=True, env=env, timeout=30)
        assert result.returncode == expected_rc
        if expected_rc == 0:
            assert result.stderr == ""
            receipt = json.loads(result.stdout)
            assert receipt["modules"] == ["app", "left", "pkg", "pkg.helper", "pkg.shared", "right"]
            assert receipt["schema"] == "minpy-bundle/1"
            digests.append(hashlib.sha256(output.read_bytes()).hexdigest())
        else:
            assert result.stdout == ""
            assert result.stderr == "minpy: syntax error in broken.py: invalid syntax\n"
            assert not output.exists()
        runs.append((result.stdout, result.stderr, result.returncode))
    assert runs[0] == runs[1]
    if digests:
        assert digests[0] == digests[1]
