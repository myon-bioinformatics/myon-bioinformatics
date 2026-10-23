"""Behaviour parity with myon-bioinformatics/cli_args node bridge (cli_args will vendor node_launch).

These tests encode the contract cli_args should match after vendoring; update both
when resolution, argv shape, or error codes change.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import node_launch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "node_launch"
NODE = shutil.which("node")
LARGE_STDIN = b"y" * (8 * 1024 * 1024)


def _node_major() -> int | None:
    if NODE is None:
        return None
    import subprocess

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


def test_resolution_order_explicit_over_env(monkeypatch):
    node_launch.clear_probe_cache()
    monkeypatch.setenv("NODE_LAUNCH_PARITY_NODE", "/env/node")
    assert (
        node_launch.resolve_node("/explicit/node", env_var="NODE_LAUNCH_PARITY_NODE", probe=False)
        == "/explicit/node"
    )


def test_resolution_order_env_over_path(monkeypatch):
    node_launch.clear_probe_cache()
    monkeypatch.setenv("NODE_LAUNCH_PARITY_NODE", "/env/node")
    monkeypatch.setattr(shutil, "which", lambda _name: "/path/node")
    assert node_launch.resolve_node(env_var="NODE_LAUNCH_PARITY_NODE", probe=False) == "/env/node"


def test_resolution_order_path_fallback(monkeypatch):
    node_launch.clear_probe_cache()
    monkeypatch.delenv("NODE_LAUNCH_PARITY_NODE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: "/path/node")
    assert node_launch.resolve_node(env_var="NODE_LAUNCH_PARITY_NODE", probe=False) == "/path/node"


def test_reject_windows_batch_extensions(tmp_path):
    node_launch.clear_probe_cache()
    for suffix in (".cmd", ".bat"):
        shim = tmp_path / f"node{suffix}"
        shim.write_text("@echo off\n", encoding="utf-8")
        with pytest.raises(node_launch.NodeNotFoundError, match="cmd|bat"):
            node_launch.resolve_node(str(shim), probe=False)


def test_node_command_argv_shape():
    node_launch.clear_probe_cache()
    argv = node_launch.node_command("entry.mjs", "a", "b", node="/bin/node", node_flags=["--import"], probe=False)
    assert argv == ["/bin/node", "--import", "entry.mjs", "a", "b"]


def test_node_or_command_only_wraps_js():
    node_launch.clear_probe_cache()
    assert node_launch.node_or_command(["tool.mjs", "x"], node="/bin/node", probe=False) == [
        "/bin/node",
        "tool.mjs",
        "x",
    ]
    assert node_launch.node_or_command(["python", "-c", "pass"], probe=False) == ["python", "-c", "pass"]


def test_error_code_einputtoolarge():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run_argv(
            ["-e", "process.exit(0)"],
            node=NODE or "node",
            probe=False,
            input=b"x",
            max_input_bytes=0,
        )
    assert exc.value.code == "EINPUTTOOLARGE"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_eresponsetoolarge():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(
            "-e",
            "process.stdout.write('z'.repeat(64))",
            node=NODE,
            probe=False,
            response_max_bytes=8,
        )
    assert exc.value.code == "ERESPONSETOOLARGE"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_elaunchstdinclosed_uncapped():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(FIXTURES / "close_stdin.mjs", node=NODE, probe=False, input=LARGE_STDIN, timeout=60)
    assert exc.value.code == "ELAUNCHSTDINCLOSED"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_elaunchfailure_timeout():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(
            FIXTURES / "hang_no_stdin.mjs",
            node=NODE,
            probe=False,
            input=LARGE_STDIN,
            timeout=2,
        )
    assert exc.value.code == "ELAUNCHFAILURE"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_elaunchfailure_nonzero_exit():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run(FIXTURES / "exit_code.mjs", "3", node=NODE, probe=False)
    assert exc.value.code == "ELAUNCHFAILURE"
    assert exc.value.status == 3


@pytest.mark.skipif(sys.platform == "win32", reason="signal return codes are Unix-specific")
@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_elaunchfailure_signal():
    node_launch.clear_probe_cache()
    with pytest.raises(node_launch.LaunchError) as exc:
        node_launch.run("-e", "process.kill(process.pid, 'SIGKILL')", node=NODE, probe=False, timeout=10)
    assert exc.value.code == "ELAUNCHFAILURE"
    assert exc.value.signal is not None
