"""🟩 njs_launch.py: Python starts Node (the mirror of py_launch.cjs). Unit tests need Node 26+ on PATH only where they probe it."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import njs_launch
import pytest

ROOT = Path(__file__).resolve().parents[1]


# ═══ 🚀 launcher ══════════════════════════════════════════════════════════════════════════════════════════
# Unit tests for njs_launch.py (requires Node 26+ when probing PATH).

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "njs_launch"
NODE = shutil.which("node")


def _node_major() -> int | None:
    if NODE is None:
        return None
    try:
        out = subprocess.check_output([NODE, "--version"], text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    match = njs_launch._NODE_VERSION.search(out)
    return int(match.group("major")) if match else None


NODE_MAJOR = _node_major()
SKIP_NO_NODE = pytest.mark.skipif(NODE is None, reason="node not on PATH")
SKIP_OLD_NODE = pytest.mark.skipif(
    NODE_MAJOR is None or NODE_MAJOR < njs_launch.MIN_NODE_MAJOR,
    reason=f"node {NODE_MAJOR} < {njs_launch.MIN_NODE_MAJOR}",
)
LARGE_STDIN = b"x" * (8 * 1024 * 1024)


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_run_passes_argv_and_returns_bytes_stdout():
    njs_launch.clear_probe_cache()
    result = njs_launch.run(FIXTURES / "echo_argv.mjs", "a", "b", node=NODE, probe=False)
    assert result.stdout.decode("utf-8").strip() == "a\nb"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_run_pipes_stdin():
    njs_launch.clear_probe_cache()
    payload = b'{"n": 3}\n'
    result = njs_launch.run(
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
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(FIXTURES / "exit_code.mjs", "4", node=NODE, probe=False)
    assert exc.value.code == "ELAUNCHFAILURE"
    assert exc.value.status == 4


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_max_input_bytes():
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(
            FIXTURES / "stdin_echo.mjs",
            node=NODE,
            probe=False,
            input=b"x",
            max_input_bytes=0,
        )
    assert exc.value.code == "EINPUTTOOLARGE"


@SKIP_NO_NODE
@SKIP_OLD_NODE
@pytest.mark.parametrize("response_max_bytes", [None, 1024], ids=["uncapped", "capped"])
def test_stdin_pipe_closed(response_max_bytes):
    njs_launch.clear_probe_cache()
    kwargs = {
        "node": NODE,
        "probe": False,
        "input": LARGE_STDIN,
        "timeout": 60,
    }
    if response_max_bytes is not None:
        kwargs["response_max_bytes"] = response_max_bytes
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(FIXTURES / "close_stdin.mjs", **kwargs)
    assert exc.value.code == "ELAUNCHSTDINCLOSED"


@SKIP_NO_NODE
@SKIP_OLD_NODE
@pytest.mark.parametrize("response_max_bytes", [None, 1024], ids=["uncapped", "capped"])
def test_stdin_write_timeout_when_child_never_reads(response_max_bytes):
    njs_launch.clear_probe_cache()
    kwargs = {
        "node": NODE,
        "probe": False,
        "input": LARGE_STDIN,
        "timeout": 2,
    }
    if response_max_bytes is not None:
        kwargs["response_max_bytes"] = response_max_bytes
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(FIXTURES / "hang_no_stdin.mjs", **kwargs)
    assert exc.value.code == "ELAUNCHFAILURE"
    assert "timed out" in str(exc.value).lower()


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_response_max_bytes():
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(
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
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(
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
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(
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
    njs_launch.clear_probe_cache()
    assert njs_launch.resolve_node(NODE, probe=True) == NODE


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_env_var_override(monkeypatch):
    njs_launch.clear_probe_cache()
    monkeypatch.setenv("NODE_LAUNCH_TEST_NODE", NODE)
    assert njs_launch.resolve_node(env_var="NODE_LAUNCH_TEST_NODE") == NODE


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_reject_cmd_shim(tmp_path):
    njs_launch.clear_probe_cache()
    shim = tmp_path / "node.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    with pytest.raises(njs_launch.NodeNotFoundError, match="cmd"):
        njs_launch.resolve_node(str(shim), probe=False)


def test_build_child_env_windows_fold(monkeypatch):
    monkeypatch.setattr(njs_launch.os, "name", "nt", raising=False)
    merged = njs_launch.build_child_env({"Path": "from-override"})
    assert merged is not None
    assert merged["Path"] == "from-override"
    assert "PATH" not in merged or merged.get("PATH") != os.environ.get("PATH")


def test_node_command_builds_argv():
    njs_launch.clear_probe_cache()
    argv = njs_launch.node_command("tool.mjs", "a", node="/bin/node", probe=False)
    assert argv == ["/bin/node", "tool.mjs", "a"]


def test_node_or_command_wraps_js():
    njs_launch.clear_probe_cache()
    argv = njs_launch.node_or_command(["cli.mjs", "x"], node="/bin/node", probe=False)
    assert argv == ["/bin/node", "cli.mjs", "x"]


def test_py39_import():
    subprocess.run(
        [sys.executable, "-c", "import njs_launch; assert njs_launch.MIN_NODE_MAJOR == 26"],
        cwd=ROOT,
        check=True,
    )


# ═══ 🤝 cli_args parity ═══════════════════════════════════════════════════════════════════════════════════
# Behaviour parity with myon-bioinformatics/cli_args node bridge (cli_args will vendor njs_launch).
#
# These tests encode the contract cli_args should match after vendoring; update both
# when resolution, argv shape, or error codes change.

PARITY_STDIN = b"y" * (8 * 1024 * 1024)


def test_resolution_order_explicit_over_env(monkeypatch):
    njs_launch.clear_probe_cache()
    monkeypatch.setenv("NODE_LAUNCH_PARITY_NODE", "/env/node")
    assert (
        njs_launch.resolve_node("/explicit/node", env_var="NODE_LAUNCH_PARITY_NODE", probe=False)
        == "/explicit/node"
    )


def test_resolution_order_env_over_path(monkeypatch):
    njs_launch.clear_probe_cache()
    monkeypatch.setenv("NODE_LAUNCH_PARITY_NODE", "/env/node")
    monkeypatch.setattr(shutil, "which", lambda _name: "/path/node")
    assert njs_launch.resolve_node(env_var="NODE_LAUNCH_PARITY_NODE", probe=False) == "/env/node"


def test_resolution_order_path_fallback(monkeypatch):
    njs_launch.clear_probe_cache()
    monkeypatch.delenv("NODE_LAUNCH_PARITY_NODE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: "/path/node")
    assert njs_launch.resolve_node(env_var="NODE_LAUNCH_PARITY_NODE", probe=False) == "/path/node"


def test_reject_windows_batch_extensions(tmp_path):
    njs_launch.clear_probe_cache()
    for suffix in (".cmd", ".bat"):
        shim = tmp_path / f"node{suffix}"
        shim.write_text("@echo off\n", encoding="utf-8")
        with pytest.raises(njs_launch.NodeNotFoundError, match="cmd|bat"):
            njs_launch.resolve_node(str(shim), probe=False)


def test_node_command_argv_shape():
    njs_launch.clear_probe_cache()
    argv = njs_launch.node_command("entry.mjs", "a", "b", node="/bin/node", node_flags=["--import"], probe=False)
    assert argv == ["/bin/node", "--import", "entry.mjs", "a", "b"]


def test_node_or_command_only_wraps_js():
    njs_launch.clear_probe_cache()
    assert njs_launch.node_or_command(["tool.mjs", "x"], node="/bin/node", probe=False) == [
        "/bin/node",
        "tool.mjs",
        "x",
    ]
    assert njs_launch.node_or_command(["python", "-c", "pass"], probe=False) == ["python", "-c", "pass"]


def test_error_code_einputtoolarge():
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run_argv(
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
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(
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
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(FIXTURES / "close_stdin.mjs", node=NODE, probe=False, input=PARITY_STDIN, timeout=60)
    assert exc.value.code == "ELAUNCHSTDINCLOSED"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_elaunchfailure_timeout():
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(
            FIXTURES / "hang_no_stdin.mjs",
            node=NODE,
            probe=False,
            input=PARITY_STDIN,
            timeout=2,
        )
    assert exc.value.code == "ELAUNCHFAILURE"


@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_elaunchfailure_nonzero_exit():
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run(FIXTURES / "exit_code.mjs", "3", node=NODE, probe=False)
    assert exc.value.code == "ELAUNCHFAILURE"
    assert exc.value.status == 3


@pytest.mark.skipif(sys.platform == "win32", reason="signal return codes are Unix-specific")
@SKIP_NO_NODE
@SKIP_OLD_NODE
def test_error_code_elaunchfailure_signal():
    njs_launch.clear_probe_cache()
    with pytest.raises(njs_launch.LaunchError) as exc:
        njs_launch.run("-e", "process.kill(process.pid, 'SIGKILL')", node=NODE, probe=False, timeout=10)
    assert exc.value.code == "ELAUNCHFAILURE"
    assert exc.value.signal is not None
