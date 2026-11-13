"""Workflow contracts: actionlint, change detection, stdlib runtime lanes, uv pytest jobs, parent-pin parsing, Claude setup, action locks, CI dependencies."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml
from scripts.install_actions_lock import install
from tests.parent_pin_workflow_cli import cli_excludes_from_shell_run

ROOT = Path(__file__).resolve().parents[1]


# ═══ · actionlint native ═════════════════════════════════════════════════════════════════════════════════
# Contract for Docker-free, checksum-pinned reusable actionlint.

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


# ═══ · actionlint selection ══════════════════════════════════════════════════════════════════════════════
# Execute the reusable workflow's Bash selector against real Git histories.

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def write(root, name, text='name: sample\non: push\njobs: {}\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def commit(root):
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'fixture')
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    write(root, '.github/workflows/existing.yml')
    commit(root)
    return root


def select(root, tmp_path, **event):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    step = next(s for s in workflow['jobs']['actionlint']['steps']
                if s.get('id') == 'files')
    selected = tmp_path / 'selected'
    output = tmp_path / 'output'
    # Isolate only the fixed scratch filename; execute the production script.
    script = step['run'].replace('/tmp/actionlint-files', str(selected))
    env = dict(os.environ, EXPLICIT_PATHS='', EVENT_NAME='push', PR_BASE_SHA='',
               PR_HEAD_SHA='', PUSH_BEFORE_SHA='', PUSH_HEAD_SHA=git(root, 'rev-parse', 'HEAD'),
               GITHUB_OUTPUT=str(output))
    env.update(event)
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=root,
                            env=env, text=True, capture_output=True, timeout=20)
    files = selected.read_text().splitlines() if selected.exists() else []
    return result, files, output.read_text() if output.exists() else ''


def test_unavailable_before_lints_all_workflows_in_rewritten_checkout(repo, tmp_path):
    old = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '--orphan', 'rewritten')
    # A workflow introduced before the tip must not be lost by a head-only diff.
    write(repo, '.github/workflows/earlier.yaml')
    commit(repo)
    write(repo, 'README.md', 'latest commit only changes docs\n')
    commit(repo)
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '-q', '--depth=1', '--branch=rewritten',
                    repo.as_uri(), str(checkout)], check=True)
    assert subprocess.run(['git', '-C', str(checkout), 'cat-file', '-e', old],
                          capture_output=True).returncode != 0
    write(checkout, '.github/workflows/untracked.yml')
    result, files, output = select(checkout, tmp_path, PUSH_BEFORE_SHA=old)
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/earlier.yaml', '.github/workflows/existing.yml']
    assert 'count=2' in output
    assert '::warning::Push diff base is unavailable' in result.stdout


def test_available_before_docs_only_selects_nothing(repo, tmp_path):
    before = git(repo, 'rev-parse', 'HEAD')
    write(repo, 'README.md', 'docs\n')
    commit(repo)
    result, files, output = select(repo, tmp_path, PUSH_BEFORE_SHA=before)
    assert result.returncode == 0, result.stderr
    assert files == [] and 'count=0' in output
    assert '::warning::' not in result.stdout


@pytest.mark.parametrize('event', ['push', 'pull_request'])
def test_normal_diff_selects_changed_existing_files_and_skips_deletions(repo, tmp_path, event):
    before = git(repo, 'rev-parse', 'HEAD')
    (repo / '.github/workflows/existing.yml').unlink()
    write(repo, '.github/workflows/new.yaml')
    head = commit(repo)
    result, files, output = select(repo, tmp_path, EVENT_NAME=event,
                                  PUSH_BEFORE_SHA=before, PR_BASE_SHA=before, PR_HEAD_SHA=head)
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/new.yaml'] and 'count=1' in output


def test_new_ref_retains_root_commit_detection(repo, tmp_path):
    result, files, output = select(repo, tmp_path, PUSH_BEFORE_SHA='0' * 40)
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/existing.yml'] and 'count=1' in output


def test_missing_head_is_not_silently_accepted(repo, tmp_path):
    result, _, output = select(repo, tmp_path, PUSH_BEFORE_SHA='1' * 40, PUSH_HEAD_SHA='2' * 40)
    assert result.returncode != 0
    assert 'count=' not in output


def test_explicit_paths_keep_precedence(repo, tmp_path):
    result, files, output = select(repo, tmp_path, EVENT_NAME='workflow_dispatch',
                                  EXPLICIT_PATHS='.github/workflows/existing.yml missing.yml')
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/existing.yml'] and 'count=1' in output


# ═══ · change detection rollout ══════════════════════════════════════════════════════════════════════════
# Execute the actual reusable classifier with controlled Git diff output.

def classify(tmp_path, files, *, event='pull_request', forced='', fail=False, missing_base=False,
            push_forced=False, push_before_sha=None):
    doc = yaml.safe_load((ROOT / '.github/workflows/reusable-change-detection.yml').read_text())
    script = doc['jobs']['changes']['steps'][1]['run']
    paths = tmp_path / 'paths'
    paths.write_bytes(b''.join(p.encode() + b'\0' for p in files))
    git = tmp_path / 'git'
    git.write_text(
        '#!/bin/bash\n'
        'set -euo pipefail\n'
        'if [[ "$1" == "cat-file" && "$2" == "-e" ]]; then\n'
        '  [[ "${FAKE_MISSING_BASE:-}" == "1" ]] && exit 1\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1" == "fetch" ]]; then exit 0; fi\n'
        'if [[ "$1" == "diff" ]]; then\n'
        '  [[ "${FAKE_DIFF_FAIL:-}" == "1" ]] && exit 128\n'
        '  cat "$FAKE_PATHS"\n'
        '  exit 0\n'
        'fi\n'
        'exit 0\n'
    )
    git.chmod(0o755)
    out = tmp_path / 'output'
    before = push_before_sha if push_before_sha is not None else 'a' * 40
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'],
               FAKE_PATHS=str(paths), GITHUB_OUTPUT=str(out), RUNNER_TEMP=str(tmp_path),
               EVENT_NAME=event, PR_BASE_SHA='a'*40, PR_HEAD_SHA='b'*40,
               PUSH_BEFORE_SHA=before, PUSH_HEAD_SHA='b'*40, FORCE_HEAVY_PATHS=forced,
               PUSH_FORCED='true' if push_forced else 'false',
               FAKE_MISSING_BASE='1' if missing_base else '0',
               FAKE_DIFF_FAIL='1' if fail else '0')
    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
    values = dict(line.split('=', 1) for line in out.read_text().splitlines()) if out.exists() else {}
    return result, values


@pytest.mark.parametrize('files,product,docs,workflow', [
    (['docs/guide.md'], False, True, False),
    (['templates/parent-pr.ref.json'], False, False, False),
    (['.github/workflows/vendor-child-ci.yml'], False, False, True),
    (['templates/parent-pr.ref.json', '.github/workflows/vendor-child-ci.yml', 'README.md'], False, False, True),
    (['vendor/tool.py'], True, False, False),
    (['vendor.lock.json'], True, False, False),
    (['requirements.txt'], True, False, False),
    (['pyproject.toml'], True, False, False),
    (['tool.py'], True, False, False),
    (['.github/workflows/ci.yml'], True, False, True),
    (['templates/parent-pr.ref.json', 'vendor/tool.py'], True, False, False),
    (['unknown.json'], True, False, False),
    (['docs/looks-safe.md\nunsafe.py'], True, False, False),
    ([], True, False, False),
])
def test_exact_rollout_paths_and_legacy_outputs(tmp_path, files, product, docs, workflow):
    result, values = classify(tmp_path, files)
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == str(product).lower()
    assert values['docs_only'] == str(docs).lower()
    assert values['non_docs_changed'] == str(not docs).lower()
    assert values['workflow_changed'] == str(workflow).lower()


def test_force_override_and_manual_keep_product_tests(tmp_path):
    result, values = classify(tmp_path, ['templates/parent-pr.ref.json'], forced='templates')
    assert result.returncode == 0 and values['product_changed'] == 'true'
    result, values = classify(tmp_path, [], event='workflow_dispatch')
    assert result.returncode == 0
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'


def test_all_zero_push_before_fails_open_with_workflow_lint(tmp_path):
    result, values = classify(
        tmp_path, [], event='push', push_before_sha='0' * 40,
    )
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'


def test_diff_failure_cannot_claim_product_skip(tmp_path):
    result, values = classify(tmp_path, [], fail=True)
    assert result.returncode != 0
    assert values.get('product_changed') != 'false'


def test_missing_push_base_fails_open_with_workflow_lint(tmp_path):
    result, values = classify(tmp_path, [], event='push', missing_base=True)
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'


def test_forced_push_fails_open_with_workflow_lint(tmp_path):
    result, values = classify(tmp_path, ['docs/guide.md'], event='push', push_forced=True)
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'


# ═══ · reusable stdlib runtime ═══════════════════════════════════════════════════════════════════════════
# reusable-stdlib-runtime.yml: input contract and the command runner step.

REUSABLE_STDLIB_RUNTIME_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-stdlib-runtime.yml"


def _load():
    doc = yaml.safe_load(REUSABLE_STDLIB_RUNTIME_WORKFLOW.read_text(encoding="utf-8"))
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


# ═══ · stdlib runtime ci ═════════════════════════════════════════════════════════════════════════════════
# Python 3.9 and 3.10 compatibility lanes get their interpreter from uv on current runners.

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


# ═══ · pytest uv ci ══════════════════════════════════════════════════════════════════════════════════════
# Parent pytest jobs run through uv. Stdlib unittest lanes stay on python -S.

WORKFLOWS = ROOT / ".github" / "workflows"
SETUP_UV = "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3"
PYTEST_WORKFLOWS = (
    "njs-launch-tests.yml",
    "python-tests.yml",
    "vendor-candidate.yml",
    "vendor-consumers-compat.yml",
    "vendor-lock-reconciliation.yml",
    "vendor-parent-child-ci.yml",
    "workflow-wrapper-tests.yml",
    "junit-collector-test.yml",
)


def test_dev_group_matches_requirements_lower_bounds():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.11"
    assert project["project"]["dependencies"] == []
    assert project["tool"]["uv"]["package"] is False
    assert project["dependency-groups"]["dev"] == ["pytest>=8", "PyYAML>=6", "coverage>=7", "pip>=23"]
    assert not list((ROOT / "tests").glob("*requirements*.txt"))
    assert "uv.lock" not in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert lock.startswith("version = ")
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "pythonpath = ." in ini


def test_pytest_workflows_use_uv_run_pytest():
    for name in PYTEST_WORKFLOWS:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "uv run pytest" in text, name
        assert "python -m pytest" not in text, name
        assert "python -m pip install" not in text, name
        if name == "workflow-wrapper-tests.yml":
            import yaml
            lock = yaml.safe_load((WORKFLOWS / "actions.lock").read_text(encoding="utf-8"))
            ref = "astral-sh/setup-uv@v10.3.3"
            assert f"uses: {ref}" in text
            assert ref in lock["workflows"][f".github/workflows/{name}"]
            assert lock["dependencies"][ref]["commit"] == "sha1-" + SETUP_UV.split("@", 1)[1].split()[0]
        else:
            assert SETUP_UV in text, name
        assert 'UV_LOCKED: "1"' in text, name
    candidate = (WORKFLOWS / "vendor-candidate.yml").read_text(encoding="utf-8")
    assert "uv run python -S vendor_candidate.py" in candidate
    others = [path.name for path in WORKFLOWS.glob("*.yml") if path.name not in PYTEST_WORKFLOWS]
    for name in others:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "python -m pytest" not in text, name
        assert "uv run pytest" not in text, name


def test_parent_lockfile_change_triggers_a_real_ci_check():
    workflow = (WORKFLOWS / "python-tests.yml").read_text(encoding="utf-8")
    assert workflow.count('      - "uv.lock"') == 2
    assert 'UV_LOCKED: "1"' in workflow
    assert "run: uv lock --check" in workflow


def test_python39_no_project_compatibility_is_explicitly_outside_parent_lock():
    node = (WORKFLOWS / "njs-launch-tests.yml").read_text(encoding="utf-8")
    assert "--python 3.9 --no-project --with pytest==8.4.2" in node
    # uv ignores UV_LOCKED under --no-project, so keep this exception visible.
    policy = (ROOT / "docs" / "python-ci-policy.md").read_text(encoding="utf-8")
    assert "UV_LOCKED` does not apply" in policy
    assert "pytest==8.4.2" in policy


def test_stdlib_lanes_stay_on_python():
    python_tests = (WORKFLOWS / "python-tests.yml").read_text(encoding="utf-8")
    stdlib = python_tests.split("jobs:", 1)[1].split("\n  pytest:", 1)[0]
    assert "python -S tests/stdlib_runtime_smoke.py" in stdlib
    assert "uv run" not in stdlib
    assert "python-versions: '[\"3.9\",\"3.10\"]'" in stdlib
    template = (ROOT / "templates" / "vendor-child-ci.workflow.yml").read_text(encoding="utf-8")
    assert "uv run" not in template
    assert "pytest" not in template


# ═══ · parent pin workflow cli ═══════════════════════════════════════════════════════════════════════════
# Branch coverage for parent-pin-check workflow run parsing.

PARENT_REPO_COMPAT_EXCLUDE = ".github/workflows/vendor-consumers-compat.yml"


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ("python tools/parent_pin_check.py --exclude 'tests/**'", ["tests/**"]),
        ('python tools/parent_pin_check.py --exclude "tests/**"', ["tests/**"]),
        ("python tools/parent_pin_check.py --exclude tests/**", ["tests/**"]),
        ("python tools/parent_pin_check.py --exclude=tests/**", ["tests/**"]),
        (
            "python parent-pin-tool/tools/parent_pin_check.py \\\n"
            f'  --exclude "{PARENT_REPO_COMPAT_EXCLUDE}"',
            [PARENT_REPO_COMPAT_EXCLUDE],
        ),
        (
            "python parent-pin-tool/tools/parent_pin_check.py \\\n"
            f"  --exclude={PARENT_REPO_COMPAT_EXCLUDE}",
            [PARENT_REPO_COMPAT_EXCLUDE],
        ),
        (
            "python parent-pin-tool/tools/parent_pin_check.py \\\n"
            f"  --exclude {PARENT_REPO_COMPAT_EXCLUDE}",
            [PARENT_REPO_COMPAT_EXCLUDE],
        ),
    ],
)
def test_cli_excludes_from_shell_run_accepts_quote_styles(run: str, expected: list[str]) -> None:
    assert cli_excludes_from_shell_run(run) == expected


def test_cli_excludes_skips_blank_lines() -> None:
    run = "\n\npython tools/parent_pin_check.py --exclude 'only'\n  \n"
    assert cli_excludes_from_shell_run(run) == ["only"]


def test_cli_excludes_continues_command_across_blank_line_after_backslash() -> None:
    run = "python tools/parent_pin_check.py \\\n\n  --exclude 'joined'"
    assert cli_excludes_from_shell_run(run) == ["joined"]


def test_cli_excludes_flushes_segment_ending_with_trailing_backslash() -> None:
    run = "python tools/parent_pin_check.py --exclude 'tail' \\"
    assert cli_excludes_from_shell_run(run) == ["tail"]


def test_cli_excludes_returns_empty_when_no_parent_pin_invocation() -> None:
    assert cli_excludes_from_shell_run("set -euo pipefail\necho ok") == []


def test_cli_excludes_requires_value_for_bare_flag() -> None:
    with pytest.raises(ValueError, match="requires a value"):
        cli_excludes_from_shell_run("python tools/parent_pin_check.py --exclude")


def test_cli_excludes_collects_two_consecutive_bare_flags() -> None:
    run = "python tools/parent_pin_check.py --exclude 'a' --exclude 'b'"
    assert cli_excludes_from_shell_run(run) == ["a", "b"]


def test_cli_excludes_stops_at_first_parent_pin_segment() -> None:
    run = (
        "python tools/parent_pin_check.py --exclude 'first'\n"
        "python tools/parent_pin_check.py --exclude 'second'\n"
    )
    assert cli_excludes_from_shell_run(run) == ["first"]


# ═══ · claude env setup ══════════════════════════════════════════════════════════════════════════════════
# Offline setup failure contracts: relocated paths and fake commands only.
#
# Never execute the installer entry point or download/install real software.

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/claude_env_setup.sh"


def fixture(tmp_path):
    # Absolute writes/executables in the script are redirected into the fixture.
    text = SCRIPT.read_text()
    for prefix in ("/opt/node", "/usr/local", "/root", "/etc/profile.d", "/tmp/claude-env-setup.log"):
        text = text.replace(prefix, str(tmp_path) + prefix)
    script = tmp_path / "setup.sh"
    script.write_text(text)
    for name in ("bin", "usr/local/bin", "etc/profile.d"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PATH=f"{tmp_path}/bin:/usr/bin:/bin", HOME=str(tmp_path),
               LINK_DIR=str(tmp_path / "links"), MODERN=str(tmp_path / "modern"),
               TMPDIR=str(tmp_path), FIXTURE=str(tmp_path), NODE_MAJOR="26", NPM_MAJOR="11", UPDATE="0")
    # Network/package commands must never fall back to host executables.
    for cmd in ("npm", "uv", "python3", "curl", "tar", "git", "gh", "node"):
        fake(tmp_path / "bin" / cmd, "exit 97")
    return script, env


def fake(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


def run(script, env, body):
    return subprocess.run(["bash", "-c", 'source "$1"; ' + body, "test", str(script)],
                          env=env, text=True, capture_output=True, timeout=10)


def test_source_has_no_setup_side_effects(tmp_path):
    script, env = fixture(tmp_path)
    result = run(script, env, "true")
    assert result.returncode == 0
    assert not (tmp_path / "tmp/claude-env-setup.log").exists()
    assert not (tmp_path / "root").exists()


def test_npm_failure_then_retry_repairs_existing_node(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    fake(tmp_path / "opt/node26/bin/node", 'echo v26.13.0')
    fake(tmp_path / "bin/npm", '''if [[ "$1" == view ]]; then echo 26.13.0; exit; fi
printf '%s\\n' "$*" >> "$FIXTURE/calls"
[[ -e "$FIXTURE/allow-npm" ]] || exit 23
[[ "$*" == *npm@11* ]] || exit 98
mkdir -p "$FIXTURE/opt/node26/lib/node_modules/npm/bin"
for n in npm npx; do
 printf '#!/bin/bash\\necho 11.21.1\\n' > "$FIXTURE/opt/node26/lib/node_modules/npm/bin/$n-cli.js"
 chmod +x "$FIXTURE/opt/node26/lib/node_modules/npm/bin/$n-cli.js"
done''')
    failed = run(script, env, "node_step")
    assert failed.returncode != 0
    assert "Node v26" not in failed.stdout
    assert not (tmp_path / "usr/local/bin/node").exists()
    (tmp_path / "allow-npm").touch()
    repaired = run(script, env, "node_step")
    assert repaired.returncode == 0, repaired.stderr
    assert "npm 11.21.1" in repaired.stdout
    count = len((tmp_path / "calls").read_text().splitlines())
    assert run(script, env, "node_step").returncode == 0
    assert len((tmp_path / "calls").read_text().splitlines()) == count
    # An executable but corrupt npx must also trigger repair on the next run.
    fake(tmp_path / "opt/node26/lib/node_modules/npm/bin/npx-cli.js", "exit 23")
    assert run(script, env, "node_step").returncode == 0
    assert len((tmp_path / "calls").read_text().splitlines()) == count + 1


def test_node_install_failure_cleans_temp_and_does_not_publish(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    fake(tmp_path / "bin/npm", '[[ "$1" == view ]] && { echo 26.13.0; exit; }; exit 23')
    assert run(script, env, "node_step").returncode != 0
    assert not list(tmp_path.glob("tmp.*"))
    assert not (tmp_path / "usr/local/bin/node").exists()


def test_python_install_failure_is_not_success(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    failed = run(script, env, "python_step")
    assert failed.returncode != 0
    assert not (tmp_path / "root").exists()


def test_uv_failure_does_not_pin_or_report_success(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    fake(tmp_path / "bin/python3", "exit 0")
    fake(tmp_path / "bin/uv", 'echo "$*" >> "$FIXTURE/uv-calls"; exit 21')
    failed = run(script, env, "python_step")
    assert failed.returncode != 0
    assert (tmp_path / "uv-calls").read_text().strip() == "python install 3.14 3.15"
    assert "Python" not in failed.stdout


def test_modern_update_failure_not_hidden_by_existing_git(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "modern/env/conda-meta").mkdir(parents=True)
    for name in "git gh go gofmt ruby gem irb bundle bundler rake jq rg pandoc ffmpeg ffprobe curl".split():
        fake(tmp_path / "modern/env/bin" / name, "exit 0")
    fake(tmp_path / "modern/bin/micromamba", 'echo "$1" > "$FIXTURE/mm-call"; exit 23')
    failed = run(script, dict(env, UPDATE="1"), "modern_step")
    assert failed.returncode != 0
    assert (tmp_path / "mm-call").read_text().strip() == "update"
    assert not (tmp_path / "etc/profile.d/modern-tools.sh").exists()


def test_partial_modern_environment_attempts_repair(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "modern/env/conda-meta").mkdir(parents=True)
    fake(tmp_path / "modern/env/bin/git", "exit 0")
    fake(tmp_path / "modern/bin/micromamba", 'echo "$1" > "$FIXTURE/mm-call"; exit 23')
    assert run(script, env, "modern_step").returncode != 0
    assert (tmp_path / "mm-call").read_text().strip() == "install"


def test_main_continues_all_stages_but_returns_failure(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    result = run(script, env, '''node_step() { echo node-stage; return 1; }
python_step() { echo python-stage; return 0; }
modern_step() { echo modern-stage; return 1; }
ver() { echo fixture; }
main''')
    assert result.returncode == 1
    assert all(s in result.stdout for s in ("node-stage", "python-stage", "modern-stage"))


def test_failed_registry_response_is_not_used(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    fake(tmp_path / "bin/npm", 'echo 26.13.0; exit 23')
    assert run(script, env, "node_step").returncode != 0
    assert not list(tmp_path.glob("tmp.*"))


def test_link_failure_is_reported(tmp_path):
    script, env = fixture(tmp_path)
    fake(tmp_path / "payload/tool", "exit 0")
    fake(tmp_path / "bin/ln", "exit 23")
    assert run(script, env, 'link_into "$FIXTURE/payload" tool').returncode != 0


def test_main_success_and_stdin_entrypoint_guard(tmp_path):
    script, env = fixture(tmp_path)
    (tmp_path / "tmp").mkdir()
    result = run(script, env, 'node_step() { :; }; python_step() { :; }; modern_step() { :; }; ver() { echo fake; }; main')
    assert result.returncode == 0
    # Execute only the exact dispatch guard, with a mock main; never the installer.
    guard = script.read_text().split('if [[ "${BASH_SOURCE[0]:-$0}"')[1]
    probe = 'main() { echo called; }; if [[ "${BASH_SOURCE[0]:-$0}"' + guard
    result = subprocess.run(["bash"], input=probe, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == "called"


# ═══ · install actions lock ══════════════════════════════════════════════════════════════════════════════
# No installation occurs until both release identities have been checked.

@pytest.fixture
def supported_platform(monkeypatch):
    monkeypatch.setattr('scripts.install_actions_lock.platform.system', lambda: 'Linux')
    monkeypatch.setattr('scripts.install_actions_lock.platform.machine', lambda: 'x86_64')


@pytest.fixture
def manifest(tmp_path):
    p = tmp_path / 'tool.json'
    p.write_text((ROOT / 'actions-lock-tool.json').read_text())
    return p


def responses(config, wrong=None):
    return [subprocess.CompletedProcess([], 0, json.dumps({'object': {
        'type': kind, 'sha': '0' * 40 if wrong == index else config[key]}}))
        for index, (kind, key) in enumerate([('tag', 'tag_sha'), ('commit', 'commit_sha')])]


@pytest.mark.usefixtures("supported_platform")
def test_install_checks_tag_and_commit_before_installing(manifest, tmp_path, monkeypatch):
    prepare_binary(manifest, tmp_path, monkeypatch)
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    install(manifest, run)
    calls = [c.args[0] for c in run.call_args_list]
    assert calls == [
        ['gh', 'api', 'repos/github/gh-actions-lock/git/ref/tags/' + config['version']],
        ['gh', 'api', 'repos/github/gh-actions-lock/git/tags/' + config['tag_sha']],
        ['gh', 'extension', 'install', 'github/gh-actions-lock', '--pin', config['version']],
    ]


@pytest.mark.usefixtures("supported_platform")
@pytest.mark.parametrize('wrong', [0, 1])
def test_moved_release_never_installs(manifest, wrong):
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config, wrong))
    with pytest.raises(ValueError, match='changed'):
        install(manifest, run)
    assert not any('extension' in c.args[0] for c in run.call_args_list)


@pytest.mark.usefixtures("supported_platform")
@pytest.mark.parametrize('field,value', [('version', 'main'), ('tag_sha', 'bad'),
                                         ('commit_sha', None), ('linux_amd64_sha256', 'bad'), ('extra', 1)])
def test_invalid_config_never_calls_github(manifest, field, value):
    config = json.loads(manifest.read_text()); config[field] = value
    manifest.write_text(json.dumps(config))
    run = Mock()
    with pytest.raises(ValueError):
        install(manifest, run)
    run.assert_not_called()


@pytest.mark.usefixtures("supported_platform")
def test_api_failure_never_installs(manifest):
    run = Mock(side_effect=subprocess.CalledProcessError(1, ['gh', 'api']))
    with pytest.raises(subprocess.CalledProcessError):
        install(manifest, run)
    assert run.call_count == 1


def prepare_binary(manifest, tmp_path, monkeypatch, contents=b'reviewed test binary'):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    binary = tmp_path / 'data/gh/extensions/gh-actions-lock/gh-actions-lock'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(contents)
    config = json.loads(manifest.read_text())
    config['linux_amd64_sha256'] = hashlib.sha256(b'reviewed test binary').hexdigest()
    manifest.write_text(json.dumps(config))
    return binary


@pytest.mark.usefixtures("supported_platform")
def test_changed_binary_fails_before_any_extension_execution(manifest, tmp_path, monkeypatch):
    prepare_binary(manifest, tmp_path, monkeypatch, b'unreviewed bytes')
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    with pytest.raises(ValueError, match='binary checksum mismatch'):
        install(manifest, run)
    assert len(run.call_args_list) == 3
    assert all(c.args[0][1] != 'actions-lock' for c in run.call_args_list)


@pytest.mark.usefixtures("supported_platform")
def test_missing_binary_fails_closed(manifest, tmp_path, monkeypatch):
    prepare_binary(manifest, tmp_path, monkeypatch).unlink()
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    with pytest.raises(FileNotFoundError):
        install(manifest, run)


@pytest.mark.usefixtures("supported_platform")
def test_unsupported_platform_does_not_install(manifest, monkeypatch):
    monkeypatch.setattr('scripts.install_actions_lock.platform.machine', lambda: 'aarch64')
    run = Mock()
    with pytest.raises(ValueError, match='linux-amd64'):
        install(manifest, run)
    run.assert_not_called()


@pytest.mark.usefixtures("supported_platform")
def test_default_data_home_matches_gh(manifest, tmp_path, monkeypatch):
    binary = prepare_binary(manifest, tmp_path, monkeypatch)
    home = tmp_path / 'home'
    destination = home / '.local/share/gh/extensions/gh-actions-lock/gh-actions-lock'
    destination.parent.mkdir(parents=True)
    destination.write_bytes(binary.read_bytes())
    monkeypatch.delenv('XDG_DATA_HOME')
    monkeypatch.setattr('scripts.install_actions_lock.Path.home', lambda: home)
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    install(manifest, run)


# ═══ · nano ci dependencies ══════════════════════════════════════════════════════════════════════════════
# Dependency-only CI contracts: stdlib mocks, never install or execute tests.

SPEC = importlib.util.spec_from_file_location("nano_ci_dependencies", Path(__file__).parents[1] / "tools/nano_node.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)


class DependencyOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "out"
        self.manifest = {"private": True, "type": "module", "devDependencies": {
            "linkedom": "0.18.13", "markdown-it": "15.0.3", "marked": "18.1.1",
            "string-width": "8.3.0", "smol-toml": "1.9.1"}}
        self.lock = {"name": "mirror", "lockfileVersion": 3, "requires": True,
                     "packages": {"": {"devDependencies": self.manifest["devDependencies"]}}}
        for name in self.manifest["devDependencies"]:
            self.lock["packages"]["node_modules/" + name] = {
                "version": self.manifest["devDependencies"][name], "dev": True,
                "resolved": "https://registry.npmjs.org/fixture/-/fixture.tgz", "integrity": "sha512-fixture"}
        self.lock["packages"]["node_modules/linkedom"].update(
            peerDependencies={"canvas": ">= 2"}, peerDependenciesMeta={"canvas": {"optional": True}})
        self.blobs = {}
        for name, value in (("package.json", self.manifest), ("package-lock.json", self.lock)):
            data = json.dumps(value).encode()
            (self.source / name).write_bytes(data)
            self.blobs[name] = nano.git_blob(data)
        self.patchers = [patch.object(nano, "MARKDOWN_SOURCE_BLOBS", self.blobs),
                         patch.object(nano.shutil, "which", return_value=sys.executable),
                         patch.object(nano.subprocess, "run", side_effect=self.fake_npm)]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        self.mode = "good"

    def fake_npm(self, argv, **kwargs):
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "11.9.0-test\n", "")
        self.assertEqual(argv[1:7], ["install", "--package-lock-only", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"])
        project = Path(kwargs["cwd"])
        self.assertEqual({p.name for p in project.iterdir()}, {"package.json", "package-lock.json"})
        if self.mode == "fail":
            return subprocess.CompletedProcess(argv, 2, "", "failed")
        lock = json.loads((project / "package-lock.json").read_bytes())
        lock["name"] = "markdown-toml"
        lock["packages"] = {k: v for k, v in lock["packages"].items() if not k or k == "node_modules/smol-toml"}
        lock["packages"][""]["devDependencies"] = nano.MARKDOWN_SELECTED
        if self.mode == "missing": del lock["packages"]["node_modules/smol-toml"]
        if self.mode == "version": lock["packages"]["node_modules/smol-toml"]["version"] = "99"
        if self.mode == "extra": lock["packages"]["node_modules/other"] = {}
        if self.mode == "engines": lock["packages"][""]["engines"] = {"node": ">=24"}
        (project / "package-lock.json").write_text(json.dumps(lock))
        if self.mode == "source": (self.source / "package.json").write_text("{}")
        if self.mode == "installed": (project / "node_modules").mkdir()
        return subprocess.CompletedProcess(argv, 0, "", "")

    def prepare(self, **kwargs):
        return nano.prepare(self.source, self.output, feature=nano.MARKDOWN_FEATURE,
                            dependencies_only=True, **kwargs)

    def test_only_two_pinned_inputs_staged_and_scope_explicit(self):
        (self.source / "tests").mkdir()
        (self.source / "tests/test_live.py").write_text("raise RuntimeError('never executed or copied')")
        (self.source / ".npmrc").write_text("registry=https://invalid.example")
        result = self.prepare()
        receipt = json.loads((result / "receipt.json").read_text())
        self.assertEqual(set(receipt["source_git_blobs"]), {"package.json", "package-lock.json"})
        self.assertTrue(receipt["dependencies_only"])
        self.assertFalse(receipt["application_source_staged"])
        self.assertFalse(receipt["checkout_head_verified"])
        self.assertEqual(receipt["candidate_locked_package_records"], 1)
        self.assertIsNone(receipt["installed_package_count"])
        self.assertEqual({p.name for p in (result / "markdown-toml").iterdir()}, {"package.json", "package-lock.json"})
        self.assertEqual(json.loads((result / "markdown-toml/package-lock.json").read_text())["packages"]["node_modules/smol-toml"], self.lock["packages"]["node_modules/smol-toml"])

    def test_markdown_requires_explicit_dependency_only(self):
        with self.assertRaisesRegex(ValueError, "requires --dependencies-only"):
            nano.prepare(self.source, self.output, feature=nano.MARKDOWN_FEATURE)
        self.assertFalse(self.output.exists())
        nano.subprocess.run.assert_not_called()

    def test_dependency_inputs_fail_before_npm_or_output(self):
        (self.source / "package-lock.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "blob mismatch") as raised:
            self.prepare()
        self.assertIn(self.blobs["package-lock.json"], str(raised.exception))
        self.assertIn(nano.git_blob(b"{}"), str(raised.exception))
        self.assertIn("coordinate a fixed parent selector-pin update", str(raised.exception))
        nano.subprocess.run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_or_changed_projection_fails_without_partial_output(self):
        for mode in ("fail", "missing", "version", "extra", "engines", "installed"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(list(self.output.iterdir()), [])

    def test_input_change_during_preparation_fails(self):
        self.mode = "source"
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_exact_original_graph_and_overrides_remain_required(self):
        for change in ("override", "peer", "missing", "url", "dev"):
            manifest, lock = copy.deepcopy(self.manifest), copy.deepcopy(self.lock)
            if change == "override": manifest["overrides"] = {"smol-toml": "2"}
            if change == "peer": lock["packages"]["node_modules/smol-toml"]["peerDependencies"] = {"other": "*"}
            if change == "missing": del manifest["devDependencies"]["marked"]
            if change == "url": lock["packages"]["node_modules/smol-toml"]["resolved"] = "file:../unreviewed"
            if change == "dev": lock["packages"]["node_modules/smol-toml"]["dev"] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                nano.validate_original(manifest, lock, feature=nano.MARKDOWN_FEATURE)

    def test_output_inside_source_is_rejected_before_tools(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            nano.prepare(self.source, self.source / "out", feature=nano.MARKDOWN_FEATURE, dependencies_only=True)
        nano.subprocess.run.assert_not_called()

    def test_symlinked_package_input_rejected(self):
        target = self.root / "lock.json"
        path = self.source / "package-lock.json"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.prepare()
        nano.subprocess.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
