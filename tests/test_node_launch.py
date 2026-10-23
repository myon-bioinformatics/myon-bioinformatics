"""Unit tests for node_launch.py (requires Node 24+ when probing PATH)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import node_launch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "node_launch"
NODE = shutil.which("node")


def _node_major() -> int | None:
    if NODE is None:
        return None
    try:
        out = subprocess.check_output([NODE, "--version"], text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    match = node_launch._NODE_VERSION.search(out)
    return int(match.group("major")) if match else None


NODE_MAJOR = _node_major()
SKIP_NO_NODE = pytest.mark.skipif(NODE is None, reason="node not on PATH")
SKIP_OLD_NODE = pytest.mark.skipif(
    NODE_MAJOR is None or NODE_MAJOR < node_launch.MIN_NODE_MAJOR,
    reason=f"node {NODE_MAJOR} < {node_launch.MIN_NODE_MAJOR}",
)


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_run_passes_argv_and_returns_bytes_stdout():
    node_launch.clear_probe_cache()
    result = node_launch.run(FIXTURES / "echo_argv.mjs", "a", "b", node=NODE, probe=False)
    assert result.stdout.decode("utf-8").strip() == "a\nb"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_run_pipes_stdin():
    node_launch.clear_probe_cache()
    payload = b'{"n": 3}\n'
    result = node_launch.run(
        "-e",
        "let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>process.stdout.write(s));",
        node=NODE,
        probe=False,
        input=payload,
    )
    assert result.stdout == payload


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_run_nonzero_exit_attaches_streams():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(FIXTURES / "exit_code.mjs", "4", node=NODE, probe=False)
    assert exc.value.code == "ELAUNCHFAILURE"
    assert exc.value.status == 4


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_max_input_bytes():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(
            FIXTURES / "stdin_echo.mjs",
            node=NODE,
            probe=False,
            input=b"x",
            max_input_bytes=0,
        )
    assert exc.value.code == "EINPUTTOOLARGE"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_stdin_pipe_closed():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(
            "-e",
            "process.exit(0)",
            node=NODE,
            probe=False,
            input=b"payload",
        )
    assert exc.value.code == "ELAUNCHSTDINCLOSED"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_response_max_bytes():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(
            "-e",
            "process.stdout.write('x'.repeat(32))",
            node=NODE,
            probe=False,
            response_max_bytes=8,
        )
    assert exc.value.code == "ERESPONSETOOLARGE"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_response_max_bytes_streams_large_output():
    """Capped reads must not buffer hundreds of MiB (regression for subprocess.run)."""
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(
            "-e",
            "for (let i = 0; i < 200; i++) process.stdout.write('x'.repeat(1024 * 1024));",
            node=NODE,
            probe=False,
            timeout=120,
            response_max_bytes=1024,
        )
    assert exc.value.code == "ERESPONSETOOLARGE"


@pytest.mark.skipif(sys.platform == "win32", reason="signal return codes are Unix-specific")
@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_signal_termination_sets_signal():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(
            "-e",
            "process.kill(process.pid, 'SIGKILL')",
            node=NODE,
            probe=False,
            timeout=10,
        )
    assert exc.value.signal is not None
    assert "signal" in str(exc.value).lower()


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_resolve_node_explicit():
    node_launch.clear_probe_cache()
    assert node_launch.resolve_node(NODE, probe=True) == NODE


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_env_var_override(monkeypatch):
    node_launch.clear_probe_cache()
    monkeypatch.setenv("NODE_LAUNCH_TEST_NODE", NODE)
    assert node_launch.resolve_node(env_var="NODE_LAUNCH_TEST_NODE") == NODE


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_reject_cmd_shim(tmp_path):
    node_launch.clear_probe_cache()
    shim = tmp_path / "node.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    with pytest.raises(node_launch.NodeNotFoundError, match="cmd"):
        node_launch.resolve_node(str(shim), probe=False)


def test_build_child_env_windows_fold(monkeypatch):
    monkeypatch.setattr(node_launch.os, "name", "nt", raising=False)
    merged = node_launch.build_child_env({"Path": "from-override"})
    assert merged is not None
    assert merged["Path"] == "from-override"
    assert "PATH" not in merged or merged.get("PATH") != os.environ.get("PATH")


def test_node_command_builds_argv():
    node_launch.clear_probe_cache()
    argv = node_launch.node_command("tool.mjs", "a", node="/bin/node", probe=False)
    assert argv == ["/bin/node", "tool.mjs", "a"]


def test_node_or_command_wraps_js():
    node_launch.clear_probe_cache()
    argv = node_launch.node_or_command(["cli.mjs", "x"], node="/bin/node", probe=False)
    assert argv == ["/bin/node", "cli.mjs", "x"]


def test_py39_import():
    subprocess.run(
        [sys.executable, "-c", "import node_launch; assert node_launch.MIN_NODE_MAJOR == 24"],
        cwd=ROOT,
        check=True,
    )
