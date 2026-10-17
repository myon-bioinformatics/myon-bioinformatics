from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("py_matrix", ROOT / "py_matrix.py")
py_matrix = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(py_matrix)


class FakeRun:
    """Records argv/env and replies per Python version."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        reply = self.replies[argv[argv.index("--python") + 1]]
        if reply == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        code, stdout = reply
        return subprocess.CompletedProcess(argv, code, stdout, "")


def test_exact_uv_argv_and_one_summary_line_per_version(capsys) -> None:
    run = FakeRun({"3.9": (0, "....\n39 passed in 1.61s\n"), "3.13": (0, "39 passed in 1.69s\n")})
    code = py_matrix.main(["3.9", "3.13", "--with", "pytest>=8", "--", "tests/test_gh_ops.py", "-k", "merge"],
                          which=lambda name: "/usr/bin/uv", run=run)
    assert code == 0
    assert capsys.readouterr().out == "py3.9: 39 passed in 1.61s\npy3.13: 39 passed in 1.69s\n"
    argv, kwargs = run.calls[0]
    assert argv == ["uv", "run", "--no-project", "--quiet", "--python", "3.9", "--with", "pytest>=8", "python",
                    "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_gh_ops.py", "-k", "merge"]
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_failure_and_timeout_are_reported_and_exit_1(capsys) -> None:
    failing = "\n".join(f"line {n}" for n in range(30)) + "\n1 failed, 38 passed in 1.7s\n"
    run = FakeRun({"3.9": (1, failing), "3.12": "timeout", "3.13": (0, "39 passed in 1.6s\n")})
    assert py_matrix.main(["3.9", "3.12", "3.13", "--tail", "2", "--timeout", "5"],
                          which=lambda name: "/usr/bin/uv", run=run) == 1
    assert capsys.readouterr().out.splitlines() == [
        "py3.9: FAILED (exit 1): 1 failed, 38 passed in 1.7s",
        "  line 29",
        "  1 failed, 38 passed in 1.7s",
        "py3.12: TIMEOUT after 5s",
        "py3.13: 39 passed in 1.6s",
    ]
    assert run.calls[0][0][run.calls[0][0].index("--with") + 1] == "pytest"


def test_missing_uv_is_exit_2(capsys) -> None:
    assert py_matrix.main(["3.9"], which=lambda name: None, run=FakeRun({})) == 2
    assert "uv not found" in capsys.readouterr().err


def test_default_version_is_3_13(capsys) -> None:
    run = FakeRun({"3.13": (0, "1 passed\n")})
    assert py_matrix.main(["--", "tests/x.py"], which=lambda name: "/usr/bin/uv", run=run) == 0
    assert capsys.readouterr().out == "py3.13: 1 passed\n"
