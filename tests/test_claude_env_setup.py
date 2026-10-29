"""Offline setup failure contracts: relocated paths and fake commands only.

Never execute the installer entry point or download/install real software.
"""
from pathlib import Path
import os
import subprocess

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
