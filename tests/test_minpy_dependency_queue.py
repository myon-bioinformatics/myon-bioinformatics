"""minpy dependency discovery with shared modules and package parents, and dependency-cycle diagnostics."""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from tools import minpy


# ═══ · minpy dependency queue ════════════════════════════════════════════════════════════════════════════
# Compatibility of dependency discovery with shared modules and package parents.

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


# ═══ · minpy cycles ══════════════════════════════════════════════════════════════════════════════════════
# Depth and compatibility contracts for minpy's dependency-cycle diagnostics.

def _recursive_cycles(edges):
    """Pre-change DFS, retained as an oracle for small graphs only."""
    nodes = sorted(set(edges) | {dep for deps in edges.values() for dep in deps})
    color = {node: 0 for node in nodes}
    stack, found = [], []

    def visit(node):
        color[node] = 1
        stack.append(node)
        for nxt in sorted(edges.get(node, ())):
            state = color.get(nxt, 0)
            if state == 1:
                start = stack.index(nxt)
                found.append(minpy._normalize_cycle(stack[start:]))
            elif state == 0:
                visit(nxt)
        stack.pop()
        color[node] = 2

    for node in nodes:
        if color[node] == 0:
            visit(node)
    return sorted(set(found))


@pytest.mark.parametrize("back_edge", [False, True])
def test_cycles_above_interpreter_recursion_limit(back_edge):
    limit = sys.getrecursionlimit()
    nodes = [f"n{i:06}" for i in range(max(1500, limit + 100))]
    edges = {left: {right} for left, right in zip(nodes, nodes[1:])}
    if back_edge:
        edges[nodes[-1]] = {nodes[0]}
    assert minpy._cycles(edges) == ([tuple(nodes)] if back_edge else [])
    assert sys.getrecursionlimit() == limit


@pytest.mark.parametrize("edges, expected", [
    ({}, []),
    ({"only": set()}, []),
    ({"root": {"leaf"}}, []),  # Neighbor-only nodes need a completed frame too.
    ({"self": {"self"}}, [("self",)]),
    ({"z": {"y"}, "y": {"z"}, "b": {"a"}, "a": {"b"}, "solo": set()},
     [("a", "b"), ("y", "z")]),
    ({"a": {"b", "d"}, "b": {"c"}, "c": {"a", "b"}, "d": {"a", "c"}},
     [("a", "b", "c"), ("a", "d"), ("b", "c")]),
    ({"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set()}, []),
])
def test_cycle_order_normalization_and_disconnected_components(edges, expected):
    original = {node: deps.copy() for node, deps in edges.items()}
    assert minpy._cycles(edges) == expected
    assert minpy._cycles(dict(reversed(list(edges.items())))) == expected
    assert edges == original


def test_cycles_match_recursive_dfs_for_all_three_node_graphs():
    nodes = ("a", "b", "c")
    possible = list(itertools.product(nodes, repeat=2))
    for mask in range(1 << len(possible)):
        edges = {node: set() for node in nodes}
        for bit, (source, target) in enumerate(possible):
            if mask & (1 << bit):
                edges[source].add(target)
        assert minpy._cycles(edges) == _recursive_cycles(edges), edges


def test_seeded_graphs_preserve_diagnostics_and_bundle_bytes(tmp_path, monkeypatch):
    rng = random.Random(119)
    current_cycles = minpy._cycles
    for case in range(30):
        root = tmp_path / str(case)
        root.mkdir()
        nodes = [f"n{i}" for i in range(8)]
        rng.shuffle(nodes)
        edges = {node: {nxt for nxt in nodes if rng.random() < 0.25} for node in nodes}
        assert current_cycles(edges) == _recursive_cycles(edges), edges
        for node, deps in edges.items():
            (root / (node + ".py")).write_text(
                "".join(f"import {dep}\n" for dep in sorted(deps)) + "VALUE = 1\n",
                encoding="utf-8",
            )
        # Exercise public rejection text and opt-in cycle metadata, not just DFS.
        for allow_cycles in (False, True):
            outcomes = []
            for implementation in (_recursive_cycles, current_cycles):
                with monkeypatch.context() as patch:
                    patch.setattr(minpy, "_cycles", implementation)
                    try:
                        text = minpy.bundle(root=root, entry="n0.py", repository="example/cycles",
                                            commit="a" * 40, skip_head_check=True,
                                            allow_cycles=allow_cycles)
                        outcomes.append(("bundle", text))
                    except minpy.MinpyError as error:
                        outcomes.append(("error", str(error)))
            assert outcomes[0] == outcomes[1], (case, allow_cycles, edges)


@pytest.mark.parametrize("allow_cycles", [False, True])
def test_stdlib_cli_cycle_contracts_are_deterministic(tmp_path, allow_cycles):
    for name, source in {
        "app": "import a\nimport x\n",
        "a": "import b\nimport d\n",
        "b": "import c\n",
        "c": "import a\nimport b\n",
        "d": "import a\nimport c\n",
        "x": "import y\n",
        "y": "import x\n",
    }.items():
        (tmp_path / (name + ".py")).write_text(source, encoding="utf-8")
    output = tmp_path / "bundle.py"
    args = ["bundle", "--root", str(tmp_path), "--entry", "app.py",
            "--repository", "example/cycles", "--commit", "a" * 40,
            "--skip-head-check", "--output", str(output)]
    if allow_cycles:
        args.append("--allow-cycles")
    expected_cycles = "a -> b -> c -> a; a -> d -> a; b -> c -> b; x -> y -> x"
    results, artifacts = [], []
    for seed, command in [
        ("1", [sys.executable, "-S", "tools/minpy.py"]),
        ("321", [sys.executable, "-S", "-c",
                 "from tools.minpy import main; raise SystemExit(main())"]),
    ]:
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(command + args, cwd=ROOT, input="unused stdin\n",
                                capture_output=True, text=True, env=env, timeout=30)
        assert result.returncode == (0 if allow_cycles else 1)
        if allow_cycles:
            assert result.stderr == ""
            assert json.loads(result.stdout)["schema"] == "minpy-bundle/1"
            artifacts.append(output.read_bytes())
            header = minpy.parse_header(artifacts[-1].decode("utf-8"))
            assert header["allow_cycles"] is True
            assert header["cycles"] == expected_cycles
            output.unlink()
        else:
            assert result.stdout == ""
            assert result.stderr == (
                "minpy: circular import: " + expected_cycles
                + ". Pass --allow-cycles only when the cycle is safe under ordinary imports.\n"
            )
            assert not output.exists()
        results.append((result.stdout, result.stderr, result.returncode))
    assert results[0] == results[1]
    if artifacts:
        assert artifacts[0] == artifacts[1]
