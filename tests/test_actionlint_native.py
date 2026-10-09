"""Contract for Docker-free, checksum-pinned reusable actionlint."""
from pathlib import Path
import re
import subprocess

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-actionlint.yml"
VERSION = "1.7.12"
DIGEST = "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"


def test_native_actionlint_is_pinned_and_fails_closed():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["actionlint"]["steps"]
    step = next(s for s in steps if s.get("name") == "Validate changed workflow syntax")
    script = step["run"]
    assert step["if"] == "steps.files.outputs.count != '0'"
    assert "set -euo pipefail" in script
    assert f"version={VERSION}" in script
    assert f"sha256={DIGEST}" in script
    assert "github.com/rhysd/actionlint/releases/download/v" in script
    assert "sha256sum --check --status" in script
    assert script.index("sha256sum --check --status") < script.index("tar -xzf")
    assert "docker run" not in script
    assert "curl --fail" in script
    assert '"${files[@]}"' in script
    assert "trap 'rm -rf" in script
    assert not re.search(r"\b(latest|main)\b", script)
    result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
