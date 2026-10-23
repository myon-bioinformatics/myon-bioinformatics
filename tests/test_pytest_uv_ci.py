"""Parent pytest jobs run through uv. Stdlib unittest lanes stay on python -S."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SETUP_UV = "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2"
PYTEST_WORKFLOWS = (
    "python-tests.yml",
    "vendor-candidate.yml",
    "vendor-consumers-compat.yml",
    "vendor-fleet-plan.yml",
    "vendor-lock-reconciliation.yml",
    "vendor-parent-child-ci.yml",
    "workflow-wrapper-tests.yml",
    "junit-collector-test.yml",
)


def _requirement_lines(path):
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def test_dev_group_matches_requirements_lower_bounds():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.11"
    assert project["project"]["dependencies"] == []
    assert project["tool"]["uv"]["package"] is False
    assert project["dependency-groups"]["dev"] == _requirement_lines(ROOT / "tests" / "requirements.txt")
    dev = set(project["dependency-groups"]["dev"])
    for name in ("workflow-requirements.txt", "junit-requirements.txt"):
        assert set(_requirement_lines(ROOT / "tests" / name)) <= dev
    assert "uv.lock" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "pythonpath = ." in ini


def test_pytest_workflows_use_uv_run_pytest():
    for name in PYTEST_WORKFLOWS:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "uv run pytest" in text, name
        assert "python -m pytest" not in text, name
        assert "python -m pip install" not in text, name
        assert SETUP_UV in text, name
    candidate = (WORKFLOWS / "vendor-candidate.yml").read_text(encoding="utf-8")
    assert "uv run python -S vendor_candidate.py" in candidate
    others = [path.name for path in WORKFLOWS.glob("*.yml") if path.name not in PYTEST_WORKFLOWS]
    for name in others:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "python -m pytest" not in text, name
        assert "uv run pytest" not in text, name


def test_stdlib_lanes_stay_on_python():
    python_tests = (WORKFLOWS / "python-tests.yml").read_text(encoding="utf-8")
    stdlib = python_tests.split("jobs:", 1)[1].split("\n  pytest:", 1)[0]
    assert "python -S tests/stdlib_runtime_smoke.py" in stdlib
    assert "uv run" not in stdlib
    assert 'python: ["3.9", "3.10"]' in stdlib
    constrained = (WORKFLOWS / "constrained-lab.yml").read_text(encoding="utf-8")
    assert "python -S -m unittest discover" in constrained
    assert "uv run" not in constrained
    template = (ROOT / "templates" / "vendor-child-ci.workflow.yml").read_text(encoding="utf-8")
    assert "uv run" not in template
    assert "pytest" not in template
