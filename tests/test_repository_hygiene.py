"""Keep Python runtime artifacts out of the Git index."""
from pathlib import Path, PurePosixPath
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_no_tracked_python_runtime_artifacts():
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    artifacts = [path for path in paths if "__pycache__" in PurePosixPath(path).parts
                 or PurePosixPath(path).suffix in {".pyc", ".pyo", ".pyd"}]
    assert not artifacts, "Tracked Python runtime artifacts: " + repr(artifacts)


def test_python_runtime_artifacts_are_ignored():
    paths = ["__pycache__/example", "tests/__pycache__/example", "example.pyc",
             "tests/example.pyo", "vendor/example.pyd"]
    result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                            cwd=ROOT, input="\n".join(paths) + "\n",
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == paths
