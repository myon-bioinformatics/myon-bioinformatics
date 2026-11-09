"""reusable-stdlib-runtime.yml: input contract and the command runner step."""
import subprocess
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-stdlib-runtime.yml"


def _load():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc[True]["workflow_call"]["inputs"], doc["jobs"]["runtime"]


def test_inputs_and_matrix_come_from_the_caller():
    inputs, job = _load()
    assert inputs["commands"]["required"] is True
    assert inputs["python-versions"]["default"] == '["3.11","3.12","3.13","3.14"]'
    assert inputs["extra-runners"]["default"] == "[]"
    matrix = job["strategy"]["matrix"]
    assert matrix["python"] == "${{ fromJSON(inputs.python-versions) }}"
    assert matrix["include"] == "${{ fromJSON(inputs.extra-runners) }}"
    assert job["runs-on"] == "${{ matrix.os }}"


def test_commands_reach_the_shell_through_env_not_template_text():
    _, job = _load()
    step = job["steps"][-1]
    assert step["env"] == {"COMMANDS": "${{ inputs.commands }}"}
    assert "inputs." not in step["run"]


def _run(commands):
    _, job = _load()
    run = job["steps"][-1]["run"]
    return subprocess.run(
        ["bash", "-c", run], env={"COMMANDS": commands, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False,
    )


def test_commands_run_in_order():
    result = _run("echo one\necho two")
    assert result.returncode == 0
    assert result.stdout.split() == ["one", "two"]


def test_first_failure_stops_the_job():
    result = _run("echo one\nfalse\necho never")
    assert result.returncode != 0
    assert result.stdout.split() == ["one"]
