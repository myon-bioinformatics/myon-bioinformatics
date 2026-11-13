"""Python 3.9 and 3.10 compatibility lanes get their interpreter from uv on current runners."""
import json
from pathlib import Path

import yaml


def test_python_39_lane_uses_uv_on_current_runner_without_lowering_pytest_floor():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/python-tests.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    call = workflow["jobs"]["stdlib-runtime"]
    assert call["uses"] == "./.github/workflows/reusable-stdlib-runtime.yml"
    assert json.loads(call["with"]["python-versions"]) == ["3.9", "3.10"]
    assert call["with"]["commands"] == "python -S tests/stdlib_runtime_smoke.py"
    runtime = yaml.load(
        (root / ".github/workflows/reusable-stdlib-runtime.yml").read_text(),
        Loader=yaml.BaseLoader,
    )["jobs"]["runtime"]
    assert runtime["runs-on"] == "${{ matrix.os }}"
    assert runtime["strategy"]["matrix"]["os"] == ["ubuntu-latest"]
    assert runtime["strategy"]["fail-fast"] == "false"
    assert not any(step.get("uses", "").startswith("actions/setup-python@")
                   for step in runtime["steps"])
    setup = next(step for step in runtime["steps"] if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert "matrix.python" in setup["with"]["python-version"]
    assert setup["with"]["activate-environment"] == "true"
    pytest = workflow["jobs"]["pytest"]
    assert pytest["runs-on"] == "ubuntu-latest"
    assert all(tuple(map(int, version.split("."))) >= (3, 11)
               for version in pytest["strategy"]["matrix"]["python"])
    assert "3.15" in pytest["strategy"]["matrix"]["python"]


def test_node_python_39_lane_uses_uv_on_current_runner_without_changing_modern_matrix():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/njs-launch-tests.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    legacy = workflow["jobs"]["stdlib-py39"]
    assert legacy["runs-on"] == "ubuntu-latest"
    assert not any(step.get("uses", "").startswith("actions/setup-python@")
                   for step in legacy["steps"])
    setup = next(step for step in legacy["steps"]
                 if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert setup["with"]["python-version"] == "3.9"
    test = next(step for step in legacy["steps"] if "run" in step)
    assert test["run"] == (
        "uv run --python 3.9 --no-project --with pytest==8.4.2 pytest -q "
        "tests/test_njs_launch.py"
    )
    modern = workflow["jobs"]["node-test"]
    assert modern["runs-on"] == "${{ matrix.os }}"
    assert modern["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
        "node": ["26", "latest"],
        "python": ["3.11", "3.12"],
    }
