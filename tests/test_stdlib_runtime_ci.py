"""Do not request an unavailable Python 3.9 build on Ubuntu 26.04."""
from pathlib import Path

import yaml


def test_python_39_uses_legacy_runner_without_lowering_pytest_floor():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/python-tests.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    runtime = workflow["jobs"]["stdlib-runtime"]
    assert runtime["runs-on"] == (
        "${{ matrix.python == '3.9' && 'ubuntu-24.04' || 'ubuntu-latest' }}"
    )
    assert runtime["strategy"]["matrix"]["python"] == ["3.9", "3.10"]
    assert runtime["strategy"]["fail-fast"] == "false"
    smoke = next(step for step in runtime["steps"] if "run" in step)
    assert smoke["run"] == "python -S tests/stdlib_runtime_smoke.py"
    setup = next(step for step in runtime["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "${{ matrix.python }}"
    pytest = workflow["jobs"]["pytest"]
    assert pytest["runs-on"] == "ubuntu-latest"
    assert all(tuple(map(int, version.split("."))) >= (3, 11)
               for version in pytest["strategy"]["matrix"]["python"])


def test_node_python_39_uses_legacy_runner_without_changing_modern_matrix():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/node-launch-tests.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    legacy = workflow["jobs"]["stdlib-py39"]
    assert legacy["runs-on"] == "ubuntu-24.04"
    setup = next(step for step in legacy["steps"]
                 if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "3.9"
    test = next(step for step in legacy["steps"] if "run" in step)
    assert test["run"] == (
        "uv run --python 3.9 --no-project --with pytest==8.4.2 pytest -q "
        "tests/test_node_launch.py tests/test_node_launch_cli_args_parity.py"
    )
    modern = workflow["jobs"]["node-test"]
    assert modern["runs-on"] == "${{ matrix.os }}"
    assert modern["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
        "node": ["24", "latest"],
        "python": ["3.11", "3.12"],
    }
