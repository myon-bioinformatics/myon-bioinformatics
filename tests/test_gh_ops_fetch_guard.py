"""Real local transports plus deterministic retry/deadline regressions (no Internet)."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gh_ops as g


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


def test_all_attempts_timeout_within_total_budget():
    clock, attempts = Clock(), []

    def run(argv, **kwargs):
        attempts.append(kwargs["timeout"])
        clock.now += kwargs["timeout"]
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    ok, error = g.git_commit_push_with_retry(run=run, sleep=clock.sleep, monotonic=clock)
    assert not ok and "total timeout after 300s" in error
    assert attempts == [60, 60, 60, 60, 30]
    assert clock.waits == [2, 4, 8, 16] and clock.now == 300


def test_attempt_longer_than_total_is_clamped_and_not_retried():
    clock, attempts = Clock(), []

    def run(argv, **kwargs):
        attempts.append(kwargs["timeout"])
        clock.now += kwargs["timeout"]
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    ok, error = g.git_commit_push_with_retry(run=run, sleep=clock.sleep, monotonic=clock,
                                            timeout=300, total_timeout=7)
    assert not ok and "total timeout after 7s" in error
    assert attempts == [7] and not clock.waits


def test_backoff_and_scheduler_overrun_cannot_launch_another_attempt():
    for overrun in (0, 10):
        clock, calls = Clock(), []

        def run(argv, **kwargs):
            calls.append(argv)
            clock.now += 1
            return subprocess.CompletedProcess(argv, 1, "", "Connection refused: original detail")

        def sleep(delay):
            clock.sleep(delay)
            clock.now += overrun

        ok, error = g.git_commit_push_with_retry(run=run, sleep=sleep, monotonic=clock,
                                                total_timeout=2, delays=(100,))
        assert not ok and "original detail" in error and "total timeout" in error
        assert len(calls) == 1 and clock.waits == [1]


@pytest.mark.parametrize("error", [
    "Could not resolve host: example.invalid", "Connection refused", "Connection reset by peer",
    "fatal: Could not read from remote repository.",
])
def test_connection_failures_retry_with_existing_delays_then_succeed(error):
    clock, calls = Clock(), []

    def run(argv, **kwargs):
        calls.append(kwargs["timeout"])
        clock.now += 1
        return subprocess.CompletedProcess(argv, int(len(calls) < 3), "success" if len(calls) == 3 else "", error)

    assert g.git_commit_push_with_retry(run=run, sleep=clock.sleep, monotonic=clock) == (True, "success" + error)
    assert len(calls) == 3 and clock.waits == [2, 4]


@pytest.mark.parametrize("error", [
    "fatal: Authentication failed", "fatal: could not read Username: terminal prompts disabled",
    "Permission denied (publickey).\nfatal: Could not read from remote repository.",
    "Host key verification failed.\nfatal: Could not read from remote repository.",
    "! [rejected] HEAD -> main (non-fast-forward)", "pre-receive hook declined",
])
def test_authentication_ssh_and_rejection_errors_do_not_retry(error):
    calls, clock = [], Clock()

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", error)

    assert g.git_commit_push_with_retry(run=run, sleep=clock.sleep, monotonic=clock) == (False, error)
    assert len(calls) == 1 and not clock.waits


def test_success_does_not_sleep_or_retry():
    clock, calls = Clock(), []

    def run(argv, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "progress\n")

    assert g.git_commit_push_with_retry(run=run, sleep=clock.sleep, monotonic=clock) == (True, "ok\nprogress\n")
    assert len(calls) == 1 and not clock.waits


@pytest.mark.parametrize("field,value", [
    ("timeout", 0), ("total_timeout", -1), ("timeout", float("nan")),
    ("total_timeout", float("inf")), ("timeout", True), ("delays", (-1,)),
    ("delays", (float("nan"),)), ("delays", (float("inf"),)),
])
def test_invalid_deadlines_fail_before_a_git_call(field, value):
    def run(*args, **kwargs):
        pytest.fail("invalid deadline must not launch Git")
    with pytest.raises(ValueError):
        g.git_commit_push_with_retry(run=run, **{field: value})


def test_guards_override_inherited_and_extra_prompt_settings(monkeypatch):
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setenv("GIT_ASKPASS", "interactive-helper")
    monkeypatch.setenv("GCM_INTERACTIVE", "always")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i '/key with spaces' -o BatchMode=yes")
    original = dict(os.environ)
    env = g._git_network_env({"GIT_TERMINAL_PROMPT": "1", "SSH_ASKPASS_REQUIRE": "force", "EXTRA": "kept"})
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GCM_INTERACTIVE"] == "never"
    assert env["GIT_ASKPASS"] == env["SSH_ASKPASS"] == ""
    assert env["SSH_ASKPASS_REQUIRE"] == "never" and env["EXTRA"] == "kept"
    assert env["GIT_SSH_COMMAND"] == original["GIT_SSH_COMMAND"]
    assert dict(os.environ) == original


def test_default_openssh_disables_password_and_hostkey_prompts(monkeypatch, git_env):
    seen = []
    popen = subprocess.Popen

    def record(argv, **kwargs):
        # Windows cleanup may invoke taskkill without an explicit env.\n        # Observe only Git transport calls; do not assume all Popen calls are Git.\n        if len(argv) > 1 and argv[1] == "ls-remote":\n            seen.append((argv, kwargs["env"]))
        return popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", record)
    remote_probe("ssh://git@127.0.0.1:1/absent.git", git_env)
    (_, env), = [(a, e) for a, e in seen if a[1] == "ls-remote"]
    assert env["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    monkeypatch.setattr(subprocess, "Popen", popen)
    if not shutil.which("ssh"):
        pytest.skip("OpenSSH unavailable")
    done = subprocess.run(["ssh", "-G", "-F", os.devnull, "-o", "BatchMode=yes", "example.invalid"],
                          capture_output=True, text=True, timeout=5)
    assert done.returncode == 0 and "batchmode yes" in done.stdout.lower()


def test_custom_git_ssh_executable_is_preserved(monkeypatch):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("GIT_SSH", "/custom path/transport")
    env = g._git_network_env()
    assert env["GIT_SSH"] == "/custom path/transport" and "GIT_SSH_COMMAND" not in env


@pytest.mark.skipif(os.name != "posix", reason="FIFO configuration-read timeout regression")
def test_ssh_configuration_lookup_shares_the_communication_deadline(git_env, tmp_path):
    fifo = tmp_path / "blocked-gitconfig"
    os.mkfifo(fifo)
    env = {**git_env, "GIT_CONFIG_GLOBAL": str(fifo)}
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        remote_probe("ssh://git@example.invalid/remote.git", env, timeout=0.3)
    assert time.monotonic() - started < 3


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    # Isolate all real probes from user credentials, proxies and global URL rewrites.
    for key in list(os.environ):
        if key.startswith(("GIT_", "GCM_", "SSH_")) or "proxy" in key.lower():
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "")
    return g._git_network_env()


@pytest.fixture
def http_git(tmp_path):
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path.startswith("/auth/"):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="fetch-guard regression"')
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def remote_probe(url, env, timeout=5, cwd="."):
    return g._git_network_run(["git", "ls-remote", url], run=subprocess.run, cwd=cwd, timeout=timeout, env=env)


@pytest.mark.parametrize("transport", ["http", "https"])
def test_real_401_disables_askpass_and_terminal_prompts(http_git, git_env, tmp_path, transport):
    server, requests = http_git
    env = git_env.copy()
    if transport == "https":
        if not shutil.which("openssl"):
            pytest.skip("openssl needed for local TLS test")
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-keyout", str(key), "-out", str(cert)],
                       capture_output=True, check=True, timeout=10)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        # Test-server trust only: this flag never enters production code.
        env["GIT_SSL_NO_VERIFY"] = "1"
    marker = tmp_path / "askpass-called"
    askpass = tmp_path / "askpass.sh"
    askpass.write_text(f'#!/bin/sh\necho called > "{marker.as_posix()}"\nexit 1\n')
    askpass.chmod(0o755)
    # Inherited GIT_ASKPASS and core.askPass are both suppressed by an empty override.
    env.update(GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_1="core.askPass", GIT_CONFIG_VALUE_1=str(askpass))
    url = f"{transport}://127.0.0.1:{server.server_port}/auth/repo.git"
    # Positive control: this same server invokes AskPass when the guard is absent.
    control = remote_probe(url, {**env, "GIT_ASKPASS": str(askpass)})
    assert control.returncode != 0 and marker.exists()
    marker.unlink()
    done = remote_probe(url, env)
    assert done.returncode != 0 and requests and not marker.exists()
    assert "terminal prompts disabled" in done.stderr.lower()
    assert not g._git_commit_looks_like_network_error(done.stderr)


def test_real_connection_refusal_is_distinct_from_authentication(git_env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # macOS can silently drop connections while the port remains bound without
    # listen(). Close it first so this probes refusal, rather than a timeout.
    done = remote_probe(f"http://127.0.0.1:{port}/repo.git", git_env)
    assert done.returncode != 0 and g._git_commit_looks_like_network_error(done.stderr)
    assert "terminal prompts disabled" not in done.stderr.lower()


def test_real_openssh_connection_refusal(git_env):
    if not shutil.which("ssh"):
        pytest.skip("OpenSSH unavailable")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    done = remote_probe(f"ssh://git@127.0.0.1:{port}/repo.git", git_env)
    assert done.returncode != 0 and "connection refused" in done.stderr.lower()
    assert g._git_commit_looks_like_network_error(done.stderr)


@pytest.mark.skipif(os.name != "posix", reason="POSIX exec-based local SSH transport fixture")
@pytest.mark.parametrize("selection", ["env", "core", "env_over_core", "core_over_git_ssh"])
def test_custom_ssh_success_preserves_options_and_reads_real_git_refs(git_env, tmp_path, selection):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, env=git_env)
    script = tmp_path / "local-ssh.py"
    args_file = tmp_path / "ssh-args"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(args_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"os.execvp('git-upload-pack', ['git-upload-pack', {str(bare)!r}])\n")
    command = f'"{sys.executable}" "{script}" -i "identity with spaces"'
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True, env=git_env)
    env = {**git_env, "GIT_SSH_VARIANT": "ssh"}
    if selection in ("core", "core_over_git_ssh", "env_over_core"):
        configured = "missing-ssh-command" if selection == "env_over_core" else command
        subprocess.run(["git", "-C", str(work), "config", "core.sshCommand", configured], check=True, env=env)
    if selection in ("env", "env_over_core"):
        env["GIT_SSH_COMMAND"] = command
    if selection == "core_over_git_ssh":
        env["GIT_SSH"] = "missing-ssh-command"
    done = remote_probe("ssh://git@example.invalid/remote.git", env, cwd=str(work))
    assert done.returncode == 0, done.stderr
    import json
    assert json.loads(args_file.read_text())[:2] == ["-i", "identity with spaces"]


def test_real_http_success_reads_remote_refs(http_git, git_env, tmp_path):
    server, requests = http_git
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, env=git_env)
    subprocess.run(["git", "-C", str(bare), "update-server-info"], check=True, env=git_env)
    done = remote_probe(f"http://127.0.0.1:{server.server_port}/repo.git", git_env)
    assert done.returncode == 0 and any(path.startswith("/repo.git/info/refs") for path in requests)


def test_exited_process_with_inherited_output_handle_does_not_wait_for_eof(git_env):
    # communicate() waits for the sleeping child's inherited output pipes even
    # after its parent exited successfully. File capture must return immediately.
    code = ("import subprocess, sys; "
            "p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(p.pid, flush=True); print('progress', file=sys.stderr, flush=True)")
    done = None
    start = time.monotonic()
    try:
        done = g._git_network_run([sys.executable, "-c", code], run=subprocess.run,
                                  cwd=".", timeout=3, env=git_env)
        assert done.returncode == 0 and time.monotonic() - start < 3
        assert done.stderr == "progress\n"
    finally:
        if done is not None and done.stdout.strip():
            import contextlib
            import signal
            with contextlib.suppress(OSError):
                os.kill(int(done.stdout.strip()), signal.SIGTERM)


def test_real_custom_ssh_hang_is_bounded_and_children_are_killed(git_env, tmp_path):
    script = tmp_path / "fake-ssh.py"
    pidfile = tmp_path / "child.pid"
    survived = tmp_path / "child-survived"
    child_code = f"import time; time.sleep(2); open({str(survived)!r}, 'w').write('alive')"
    script.write_text(
        "import subprocess, sys, time\n"
        "if '-G' in sys.argv: sys.exit(0)\n"
        f"p = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(30)\n")
    env = {**git_env, "GIT_SSH_COMMAND": f'"{sys.executable}" "{script}"', "GIT_SSH_VARIANT": "ssh"}
    start = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            remote_probe("ssh://git@example.invalid/repo.git", env, timeout=1)
        assert time.monotonic() - start < 5 and pidfile.exists()
        # Avoid /proc assumptions (containers can expose a different PID namespace).
        time.sleep(2)
        assert not survived.exists()
    finally:
        if pidfile.exists():
            import contextlib
            import signal
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/F", "/T", "/PID", pidfile.read_text()],
                               capture_output=True, timeout=2)


def test_sync_main_network_timeout_does_not_limit_local_commands():
    seen = []

    def run(argv, **kwargs):
        seen.append((argv[1], kwargs))
        if argv[1] == "fetch":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "feature\n" if argv[1] == "rev-parse" else "", "")

    with pytest.raises(g.GhOpsError, match="git fetch timed out after 300s"):
        g.sync_main(run=run)
    for command, kwargs in seen:
        if command == "fetch":
            assert kwargs["timeout"] == 300 and kwargs["stdin"] == subprocess.DEVNULL
            assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        else:
            assert "timeout" not in kwargs and "env" not in kwargs and "stdin" not in kwargs


@pytest.mark.parametrize("command", ["fetch", "push", "pull", "clone", "ls-remote"])
def test_subtree_network_errors_keep_typed_timeout_and_guards(command):
    def run(argv, **kwargs):
        assert kwargs["timeout"] == 9 and kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        raise subprocess.TimeoutExpired(argv, 9)

    with pytest.raises(g.SubtreePushError, match=f"git_timeout: git {command}"):
        g._subtree_git(".", [command, "origin"], run=run, timeout=9)
