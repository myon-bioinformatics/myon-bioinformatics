"""Shared Python launcher for Node.js scripts (stdlib only, Python 3.9+).

Library: ``run(target, *args, node=..., input=..., timeout=..., max_input_bytes=...,
response_max_bytes=..., env=..., env_var=...)`` returns stdout/stderr as ``bytes``.
``run_argv(argv, ...)`` prepends a resolved Node executable to ``argv``.

Node resolution (first match that runs Node 24+): explicit ``node``,
``os.environ[env_var]`` when set, then ``shutil.which("node")``. Explicit ``node`` or a
set ``env_var`` value does not fall back to later candidates. Windows ``.cmd``/``.bat``
shims are never spawned with ``shell=True``; explicit paths are rejected.

``env`` merges over ``os.environ`` case-insensitively on Windows. Optional
``max_input_bytes`` / ``response_max_bytes`` raise errors with stable ``code`` values:
``EINPUTTOOLARGE``, ``ERESPONSETOOLARGE``, ``ELAUNCHFAILURE``, and
``ELAUNCHSTDINCLOSED`` when stdin breaks (``EPIPE``) before the child finishes reading.
Stdout is read incrementally when ``response_max_bytes`` is set (cap plus
``HOST_RESPONSE_MARGIN_BYTES``, matching ``py_launch.cjs`` ``maxBuffer`` / ``ENOBUFS``).
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Any, Mapping, Optional, Sequence, Union

MIN_NODE_MAJOR = 24
DEFAULT_ENV_VAR = "NODE"
DEFAULT_PROBE_TIMEOUT = 5.0
HOST_RESPONSE_MARGIN_BYTES = 512
_NODE_VERSION = re.compile(r"(?:^|\s)v?(?P<major>\d+)(?:\.\d+)*", re.IGNORECASE)
_NODE_PROBE_CACHE: dict[str, str] = {}


class NodeNotFoundError(FileNotFoundError):
    """Raised when Node.js cannot be located or does not meet the minimum version."""


class LaunchError(RuntimeError):
    """Raised when the Node child fails to start, exceeds caps, or exits unexpectedly."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "ELAUNCHFAILURE",
        status: Optional[int] = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        signal: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.signal = signal


def is_node_target(path: Union[str, os.PathLike[str]]) -> bool:
    """True when ``path`` names a JavaScript entry file by extension."""
    return os.path.splitext(os.fspath(path))[1].lower() in (".js", ".cjs", ".mjs")


def _env_key_fold(key: str) -> str:
    return str(key).casefold()


def env_lookup(env: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive environment lookup (primarily for Windows tests)."""
    fold = _env_key_fold(name)
    for key, value in env.items():
        if _env_key_fold(key) == fold:
            return value
    return None


def build_child_env(overrides: Optional[Mapping[str, str]]) -> Optional[dict[str, str]]:
    """Merge ``overrides`` onto ``os.environ``; Windows overrides replace folded keys."""
    if overrides is None:
        return None
    if os.name != "nt":
        merged = dict(os.environ)
        merged.update(overrides)
        return merged
    env = dict(os.environ)
    index: dict[str, str] = {_env_key_fold(key): key for key in env}
    for override_key, value in overrides.items():
        fold = _env_key_fold(override_key)
        existing = index.get(fold)
        if existing is not None:
            del env[existing]
        env[override_key] = value
        index[fold] = override_key
    return env


def _reject_windows_batch_wrapper(path: str) -> None:
    lowered = path.lower()
    if lowered.endswith(".cmd") or lowered.endswith(".bat"):
        raise NodeNotFoundError(
            "node_launch: node batch wrappers (.cmd/.bat) are not supported: " + path
        )


def _decode(data: bytes) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _probe_node_once(path: str, probe_timeout: float) -> str:
    cached = _NODE_PROBE_CACHE.get(path)
    if cached is not None:
        return cached
    _reject_windows_batch_wrapper(path)
    try:
        completed = subprocess.run(
            (path, "--version"),
            capture_output=True,
            timeout=probe_timeout,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise NodeNotFoundError("node_launch: node executable not found: " + path) from exc
    except OSError as exc:
        raise NodeNotFoundError("node_launch: node version probe failed: " + path) from exc
    except subprocess.TimeoutExpired as exc:
        raise NodeNotFoundError("node_launch: node version probe timed out: " + path) from exc
    output = _decode(completed.stdout) + _decode(completed.stderr)
    match = _NODE_VERSION.search(output)
    if completed.returncode != 0 or match is None:
        raise NodeNotFoundError("node_launch: node version probe failed: " + path)
    major = int(match.group("major"))
    if major < MIN_NODE_MAJOR:
        raise NodeNotFoundError(
            f"node_launch: node version must be {MIN_NODE_MAJOR} or newer (found {major}): "
            + path
        )
    _NODE_PROBE_CACHE[path] = path
    return path


def _node_executable(
    node: Optional[Union[str, os.PathLike[str]]],
    *,
    env_var: str,
    probe: bool,
    probe_timeout: float,
) -> str:
    explicit = node is not None
    if not explicit and env_var and env_var in os.environ:
        node = os.environ[env_var]
        explicit = True
    if explicit:
        path = os.fspath(node)
        _reject_windows_batch_wrapper(path)
        return _probe_node_once(path, probe_timeout) if probe else path

    if not probe:
        discovered = shutil.which("node")
        path = discovered if discovered else "node"
        _reject_windows_batch_wrapper(path)
        return path

    discovered = shutil.which("node")
    if discovered:
        try:
            return _probe_node_once(discovered, probe_timeout)
        except NodeNotFoundError:
            pass
    raise NodeNotFoundError("node_launch: node executable not found")


def resolve_node(
    node: Optional[Union[str, os.PathLike[str]]] = None,
    *,
    env_var: str = DEFAULT_ENV_VAR,
    probe: bool = True,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> str:
    """Locate a Node 24+ executable.

    Explicit ``node`` or a set ``env_var`` environment value must work as given;
    auto-discovery uses ``shutil.which('node')`` on ``PATH``.
    """
    return _node_executable(
        node, env_var=env_var, probe=probe, probe_timeout=probe_timeout
    )


def node_command(
    target: Union[str, os.PathLike[str]],
    *args: Union[str, os.PathLike[str]],
    node: Optional[Union[str, os.PathLike[str]]] = None,
    node_flags: Sequence[Union[str, os.PathLike[str]]] = (),
    env_var: str = DEFAULT_ENV_VAR,
    probe: bool = True,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> list[str]:
    """Build ``[node, *node_flags, target, *args]`` without reinterpreting passthrough argv."""
    executable = _node_executable(
        node, env_var=env_var, probe=probe, probe_timeout=probe_timeout
    )
    flags = [os.fspath(value) for value in node_flags]
    script = os.fspath(target)
    trailing = [os.fspath(value) for value in args]
    return [executable, *flags, script, *trailing]


def node_or_command(
    argv: Sequence[Union[str, os.PathLike[str]]],
    *,
    node: Optional[Union[str, os.PathLike[str]]] = None,
    node_flags: Sequence[Union[str, os.PathLike[str]]] = (),
    env_var: str = DEFAULT_ENV_VAR,
    probe: bool = True,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> list[str]:
    """Prepend Node when ``argv[0]`` names a ``.js`` / ``.cjs`` / ``.mjs`` file."""
    tokens = [os.fspath(value) for value in argv]
    if not tokens:
        return tokens
    if is_node_target(tokens[0]):
        return node_command(
            tokens[0],
            *tokens[1:],
            node=node,
            node_flags=node_flags,
            env_var=env_var,
            probe=probe,
            probe_timeout=probe_timeout,
        )
    return tokens


def _nonnegative_int(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"node_launch: {name} must be a nonnegative integer")


def _input_byte_length(input_data: Optional[Union[str, bytes]]) -> int:
    if input_data is None:
        return 0
    if isinstance(input_data, str):
        return len(input_data.encode("utf-8"))
    return len(input_data)


def _normalize_input(input_data: Optional[Union[str, bytes]]) -> Optional[bytes]:
    if input_data is None:
        return None
    if isinstance(input_data, str):
        return input_data.encode("utf-8")
    return input_data


def _response_cap_bytes(response_max_bytes: int) -> int:
    _nonnegative_int("response_max_bytes", response_max_bytes)
    return response_max_bytes + HOST_RESPONSE_MARGIN_BYTES


def _launch_failure_from_returncode(
    returncode: int,
    stdout: bytes,
    stderr: bytes,
) -> LaunchError:
    if returncode < 0:
        signum = -returncode
        return LaunchError(
            f"node_launch: Node process terminated by signal {signum}",
            code="ELAUNCHFAILURE",
            signal=signum,
            stdout=stdout,
            stderr=stderr,
        )
    message = _decode(stderr).strip() or "Node script failed"
    return LaunchError(
        message,
        code="ELAUNCHFAILURE",
        status=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _exit_code_for_main(returncode: Optional[int]) -> None:
    if returncode is None or returncode == 0:
        return
    if returncode < 0:
        sys.exit(128 + (-returncode))
    sys.exit(returncode)


def _stream_communicate(
    proc: subprocess.Popen,
    input_bytes: Optional[bytes],
    timeout: Optional[float],
    response_max_bytes: Optional[int],
) -> tuple[bytes, bytes, int]:
    hard_limit = None
    cap: Optional[int] = None
    if response_max_bytes is not None:
        _nonnegative_int("response_max_bytes", response_max_bytes)
        cap = response_max_bytes
        hard_limit = _response_cap_bytes(response_max_bytes)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_len = 0
    exceeded = threading.Event()
    read_lock = threading.Lock()
    stdin_code: list[Optional[str]] = [None]
    stdin_os_error: list[Optional[OSError]] = [None]

    def read_stdout() -> None:
        nonlocal stdout_len
        stream = proc.stdout
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                if hard_limit is not None:
                    with read_lock:
                        stdout_len += len(chunk)
                        if stdout_len > hard_limit:
                            exceeded.set()
                            try:
                                proc.kill()
                            except OSError:
                                pass
                            break
                stdout_chunks.append(chunk)
        finally:
            stream.close()

    def read_stderr() -> None:
        stream = proc.stderr
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
        finally:
            stream.close()

    def write_stdin() -> None:
        stream = proc.stdin
        if stream is None:
            return
        if input_bytes is None:
            try:
                stream.close()
            except OSError:
                pass
            return
        try:
            offset = 0
            chunk_size = 65536
            while offset < len(input_bytes):
                stream.write(input_bytes[offset : offset + chunk_size])
                offset += chunk_size
            stream.flush()
            stream.close()
        except BrokenPipeError:
            stdin_code[0] = "ELAUNCHSTDINCLOSED"
            try:
                stream.close()
            except OSError:
                pass
        except OSError as exc:
            if exc.errno == errno.EPIPE:
                stdin_code[0] = "ELAUNCHSTDINCLOSED"
                try:
                    stream.close()
                except OSError:
                    pass
            else:
                stdin_os_error[0] = exc
                try:
                    stream.close()
                except OSError:
                    pass

    stdout_thread = threading.Thread(target=read_stdout, name="node_launch_stdout", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name="node_launch_stderr", daemon=True)
    stdin_thread = threading.Thread(target=write_stdin, name="node_launch_stdin", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    stdin_thread.start()

    timed_out = False
    returncode: Optional[int]
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
        returncode = proc.returncode

    stdin_thread.join()
    stdout_thread.join()
    stderr_thread.join()

    stdout = b"".join(stdout_chunks)
    stderr = b"".join(stderr_chunks)
    if returncode is None:
        returncode = proc.returncode if proc.returncode is not None else -1

    if stdin_os_error[0] is not None:
        exc = stdin_os_error[0]
        raise LaunchError(
            f"node_launch: Node process failed: {exc}",
            code="ELAUNCHFAILURE",
            stdout=stdout,
            stderr=stderr,
        ) from exc

    if timed_out:
        raise LaunchError(
            "node_launch: Node process timed out",
            code="ELAUNCHFAILURE",
            stdout=stdout,
            stderr=stderr,
        )

    if stdin_code[0] == "ELAUNCHSTDINCLOSED":
        raise LaunchError(
            "node_launch: Node stdin pipe closed unexpectedly",
            code="ELAUNCHSTDINCLOSED",
            status=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    if exceeded.is_set() or (cap is not None and len(stdout) > cap):
        raise LaunchError(
            f"node_launch: response exceeds response_max_bytes ({cap})",
            code="ERESPONSETOOLARGE",
            status=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return stdout, stderr, returncode


def _communicate(
    proc: subprocess.Popen,
    input_bytes: Optional[bytes],
    timeout: Optional[float],
    response_max_bytes: Optional[int],
) -> tuple[bytes, bytes, int]:
    return _stream_communicate(proc, input_bytes, timeout, response_max_bytes)


def run_argv(
    argv: Sequence[Union[str, os.PathLike[str]]],
    *,
    node: Optional[Union[str, os.PathLike[str]]] = None,
    env_var: str = DEFAULT_ENV_VAR,
    probe: bool = True,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    input: Optional[Union[str, bytes]] = None,
    timeout: Optional[float] = None,
    max_input_bytes: Optional[int] = None,
    response_max_bytes: Optional[int] = None,
    env: Optional[Mapping[str, str]] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``[resolved_node, *argv]`` with ``shell=False`` and byte stdout/stderr."""
    executable = _node_executable(
        node, env_var=env_var, probe=probe, probe_timeout=probe_timeout
    )
    _reject_windows_batch_wrapper(executable)
    command = [executable, *[os.fspath(value) for value in argv]]

    input_len = _input_byte_length(input)
    if max_input_bytes is not None:
        _nonnegative_int("max_input_bytes", max_input_bytes)
        if input_len > max_input_bytes:
            raise LaunchError(
                f"node_launch: input exceeds max_input_bytes ({max_input_bytes}; got {input_len} bytes)",
                code="EINPUTTOOLARGE",
            )

    child_env = build_child_env(env)
    input_bytes = _normalize_input(input)

    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=child_env,
        )
    except OSError as exc:
        raise LaunchError(
            f"node_launch: Node process failed: {exc}",
            code="ELAUNCHFAILURE",
        ) from exc

    stdout, stderr, returncode = _communicate(
        proc, input_bytes, timeout, response_max_bytes
    )

    if returncode != 0 and check:
        raise _launch_failure_from_returncode(returncode, stdout, stderr)

    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def run(
    target: Union[str, os.PathLike[str]],
    *args: Union[str, os.PathLike[str]],
    node: Optional[Union[str, os.PathLike[str]]] = None,
    node_flags: Sequence[Union[str, os.PathLike[str]]] = (),
    env_var: str = DEFAULT_ENV_VAR,
    probe: bool = True,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    input: Optional[Union[str, bytes]] = None,
    timeout: Optional[float] = None,
    max_input_bytes: Optional[int] = None,
    response_max_bytes: Optional[int] = None,
    env: Optional[Mapping[str, str]] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``node_command(...)`` and return captured stdout/stderr as bytes."""
    argv = node_command(
        target,
        *args,
        node=node,
        node_flags=node_flags,
        env_var=env_var,
        probe=probe,
        probe_timeout=probe_timeout,
    )
    return run_argv(
        argv[1:],
        node=argv[0],
        env_var=env_var,
        probe=False,
        input=input,
        timeout=timeout,
        max_input_bytes=max_input_bytes,
        response_max_bytes=response_max_bytes,
        env=env,
        check=check,
    )


def run_node(
    target: Union[str, os.PathLike[str]],
    *args: Union[str, os.PathLike[str]],
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Alias for :func:`run` matching the cli_args helper name."""
    return run(target, *args, **kwargs)


def main(
    script_path: Union[str, os.PathLike[str]],
    opts: Optional[Mapping[str, Any]] = None,
) -> None:
    """Spawn a Node script with inherited stdio and forwarded exit code."""
    options = dict(opts or {})
    argv = options.pop("argv", None)
    if argv is None:
        argv = sys.argv[1:]
    env_var = options.pop("env_var", DEFAULT_ENV_VAR)
    node = options.pop("node", None)
    node_flags = options.pop("node_flags", ())
    probe_timeout = options.pop("probe_timeout", DEFAULT_PROBE_TIMEOUT)
    child_env = build_child_env(options.pop("env", None))
    try:
        command = node_command(
            script_path,
            *argv,
            node=node,
            node_flags=node_flags,
            env_var=env_var,
            probe=True,
            probe_timeout=probe_timeout,
        )
        _reject_windows_batch_wrapper(command[0])
    except NodeNotFoundError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(2)
    try:
        completed = subprocess.run(
            command,
            shell=False,
            env=child_env,
            check=False,
        )
    except OSError as error:
        sys.stderr.write(f"node_launch: {error}\n")
        sys.exit(2)
    if completed.returncode != 0:
        _exit_code_for_main(completed.returncode)


def clear_probe_cache() -> None:
    """Clear the version-probe cache (for tests)."""
    _NODE_PROBE_CACHE.clear()
