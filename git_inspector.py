"""Bounded read-only Git inspection helpers.

This module intentionally exposes named observations instead of arbitrary Git
argv. It never fetches, checks out, stages, commits, resets, cleans, merges,
rebases, pushes, or mutates refs/worktree state.

Repository/head identity and provenance belong to the canonical repository
metadata producer. Revisions returned here are transient observations only.

The inspected checkout is trusted configuration input. This module suppresses
ambient process-level Git routing/config overrides and known optional helpers,
but it is not a sandbox for an attacker-controlled .git/config/.gitattributes.
Use a separate sandbox before exposing arbitrary untrusted repositories via MCP.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

__version__ = "0.1.0"
__all__ = ["status", "ls_files", "diff", "log", "show", "blame",
           "grep", "check_ignore"]


class GitInspectionError(RuntimeError):
    """A bounded read-only Git observation failed."""


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(name + " must be a positive integer")
    return value


def _root(root):
    path = Path(root)
    if not path.exists():
        raise ValueError("root does not exist")
    return path


def _path(value):
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError("path must be string or path-like")
    value = os.fspath(value)
    if not value or "\x00" in value:
        raise ValueError("path must be non-empty and contain no NUL")
    return value


def _ref(value):
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("-"):
        raise ValueError("revision must be a non-option string without NUL")
    return value


def _spawn(command, **kwargs):
    return subprocess.Popen(command, **kwargs)


def _drain_bounded(stream, max_bytes, result):
    """Drain one child pipe fully while retaining at most max_bytes bytes."""
    kept = bytearray()
    truncated = False
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            room = max_bytes - len(kept)
            if room > 0:
                kept.extend(chunk[:room])
            if len(chunk) > max(room, 0):
                truncated = True
    finally:
        stream.close()
    result.append((bytes(kept), truncated))


def _run(root, args, *, max_bytes=1_000_000, ok=(0,), input_bytes=None):
    _positive(max_bytes, "max_bytes")
    if input_bytes is not None and not isinstance(input_bytes, bytes):
        raise TypeError("input_bytes must be bytes")
    command = ["git", "-C", str(_root(root)), "--no-pager",
               "--no-optional-locks", "-c", "core.fsmonitor=false", *args]
    env = os.environ.copy()
    # Do not let inherited Git process-routing/config overrides redirect a
    # supposedly local inspection to another worktree/index/object database or
    # inject an external diff helper. Ordinary locale/identity variables are
    # harmless observations and remain untouched.
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                 "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
                 "GIT_REPLACE_REF_BASE", "GIT_NO_REPLACE_OBJECTS",
                 "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS",
                 "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
                 "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                 "GIT_CONFIG_NOSYSTEM"):
        env.pop(name, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    try:
        proc = _spawn(
            command,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=env,
        )
    except FileNotFoundError as error:
        raise GitInspectionError("git executable not found") from error
    except OSError as error:
        raise GitInspectionError(type(error).__name__) from error

    stdout_result = []
    stderr_result = []
    stdout_thread = threading.Thread(
        target=_drain_bounded, args=(proc.stdout, max_bytes, stdout_result),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_bounded, args=(proc.stderr, max_bytes, stderr_result),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    if input_bytes is not None:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except BrokenPipeError:
            pass
    returncode = proc.wait()
    stdout_thread.join()
    stderr_thread.join()

    raw, truncated = stdout_result[0]
    # stderr is deliberately drained and bounded even though the public
    # contract does not expose command stderr.
    _stderr, _stderr_truncated = stderr_result[0]
    if returncode not in ok:
        raise GitInspectionError("git exited with status " + str(returncode))
    return raw, truncated, returncode

def _decode(raw):
    # Results are JSON-compatible observations. Invalid or byte-truncated UTF-8
    # is made explicit as U+FFFD instead of leaking lone surrogate code points.
    return raw.decode("utf-8", "replace")


def _complete_fields(raw, delimiter, truncated):
    """Drop a byte-truncated trailing field instead of publishing corruption."""
    if truncated and not raw.endswith(delimiter):
        boundary = raw.rfind(delimiter)
        raw = b"" if boundary < 0 else raw[:boundary + len(delimiter)]
    fields = raw.split(delimiter)
    if fields and fields[-1] == b"":
        fields.pop()
    return fields


def status(root=".", *, max_bytes=1_000_000):
    """Return structured porcelain-v2 status without inventing identity."""
    raw, truncated, _ = _run(
        root, ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        max_bytes=max_bytes,
    )
    fields = _complete_fields(raw, b"\0", truncated)
    records = []
    index = 0
    while index < len(fields):
        text = _decode(fields[index])
        kind = text[:1]
        if kind == "2":
            parts = text.split(" ", 9)
            if len(parts) != 10 or index + 1 >= len(fields):
                if truncated:
                    break
                raise GitInspectionError("malformed porcelain-v2 type-2 record")
            records.append({"kind": "2", "record": text, "path": parts[9],
                            "orig_path": _decode(fields[index + 1])})
            index += 2
            continue
        if kind == "1":
            parts = text.split(" ", 8)
            path = parts[8] if len(parts) == 9 else None
        elif kind == "u":
            parts = text.split(" ", 10)
            path = parts[10] if len(parts) == 11 else None
        elif kind in ("?", "!") and text.startswith(kind + " "):
            path = text[2:]
        else:
            path = None
        records.append({"kind": kind, "record": text, "path": path})
        index += 1
    return {"clean": not records and not truncated, "records": records, "truncated": truncated}

def ls_files(root=".", *, include_untracked=False, max_files=10_000,\n             max_bytes=1_000_000):\n    """Return a bounded NUL-safe file inventory.\n\n    The default is tracked-only. include_untracked=True additionally observes\n    untracked-but-not-ignored files without weakening existing consumers.\n    """\n    if not isinstance(include_untracked, bool):\n        raise TypeError("include_untracked must be bool")\n    _positive(max_files, "max_files")\n    args = ["ls-files", "-z"]\n    if include_untracked:\n        args.extend(["--cached", "--others", "--exclude-standard"])\n    raw, byte_truncated, _ = _run(root, args, max_bytes=max_bytes)
    paths = [_decode(item) for item in _complete_fields(raw, b"\0", byte_truncated)]
    record_truncated = len(paths) > max_files
    return {"paths": paths[:max_files],
            "truncated": byte_truncated or record_truncated}


def diff(root=".", *, staged=False, base=None, head=None, path=None,
         max_bytes=1_000_000):
    """Return a bounded patch with external diff/textconv disabled."""
    args = ["-c", "diff.external=", "diff", "--no-ext-diff", "--no-textconv",
            "--no-color"]
    if staged:
        if base is not None or head is not None:
            raise ValueError("staged diff cannot also specify revisions")
        args.append("--cached")
    elif base is not None:
        args.append(_ref(base))
        if head is not None:
            args.append(_ref(head))
    elif head is not None:
        raise ValueError("head requires base")
    args.append("--")
    if path is not None:
        args.append(_path(path))
    raw, truncated, _ = _run(root, args, max_bytes=max_bytes)
    return {"patch": _decode(raw), "truncated": truncated}


def log(root=".", *, max_count=50, path=None, max_bytes=1_000_000):
    """Return bounded commit observations; not canonical repository metadata."""
    _positive(max_count, "max_count")
    fmt = "%H%x1f%aI%x1f%an%x1f%s%x1e"
    args = ["log", "--no-decorate", "--no-color", "--format=" + fmt,
            "--max-count=" + str(max_count)]
    if path is not None:
        args.extend(["--", _path(path)])
    raw, truncated, _ = _run(root, args, max_bytes=max_bytes)
    rows = []
    for record in _complete_fields(raw, b"\x1e", truncated):
        record = record.strip(b"\r\n")
        if not record:
            continue
        fields = _decode(record).split("\x1f")
        if len(fields) == 4:
            rows.append(dict(zip(("commit", "authored_at", "author", "subject"),
                                 fields)))
    return {"commits": rows, "truncated": truncated}


def show(root=".", revision="HEAD", *, path=None, max_bytes=1_000_000):
    """Show one revision/path with bounded output and no external textconv."""
    spec = _ref(revision)
    if path is not None:
        # The path is encoded in the revision:path object expression.
        # --end-of-options protects revision parsing from option-like specs.
        spec += ":" + _path(path)
    raw, truncated, _ = _run(
        root, ["-c", "diff.external=", "show", "--no-ext-diff", "--no-textconv",
               "--no-color", "--end-of-options", spec],
        max_bytes=max_bytes,
    )
    return {"content": _decode(raw), "truncated": truncated}


def blame(root=".", path=None, *, revision="HEAD", start=None, end=None,
          max_bytes=1_000_000):
    """Return bounded line-porcelain blame for one explicit path."""
    if path is None:
        raise ValueError("path is required")
    args = ["blame", "--line-porcelain"]
    if start is not None or end is not None:
        if start is None or end is None:
            raise ValueError("start and end must be supplied together")
        _positive(start, "start")
        _positive(end, "end")
        if end < start:
            raise ValueError("end must be >= start")
        args.extend(["-L", f"{start},{end}"])
    args.extend([_ref(revision), "--", _path(path)])
    raw, truncated, _ = _run(root, args, max_bytes=max_bytes)
    return {"porcelain": _decode(raw), "truncated": truncated}


def grep(root=".", pattern=None, *, max_bytes=1_000_000):
    """Return NUL-safe tracked filenames containing a fixed literal pattern.

    Matched line text is deliberately omitted so newline-containing filenames
    cannot become ambiguous with content records.
    """
    if not isinstance(pattern, str) or not pattern or "\x00" in pattern:
        raise ValueError("pattern must be a non-empty string without NUL")
    raw, truncated, code = _run(
        root, ["grep", "-z", "-l", "-I", "-F", "-e", pattern, "--"],
        max_bytes=max_bytes, ok=(0, 1),
    )
    paths = [] if code == 1 else [
        _decode(item) for item in _complete_fields(raw, b"\0", truncated)]
    return {"paths": paths, "truncated": truncated}

def check_ignore(root=".", paths=(), *, max_paths=1000, max_bytes=1_000_000):
    """Explain ignore state using bounded NUL-safe stdin and verbose output."""
    if isinstance(paths, (str, os.PathLike)):
        raise TypeError("paths must be a sequence")
    _positive(max_paths, "max_paths")
    paths = tuple(_path(p) for p in paths)
    if len(paths) > max_paths:
        raise ValueError("too many paths")
    if not paths:
        return {"records": [], "truncated": False}
    payload = b"\0".join(os.fsencode(p) for p in paths) + b"\0"
    if len(payload) > max_bytes:
        raise ValueError("path input exceeds byte limit")
    raw, truncated, _ = _run(
        root, ["check-ignore", "-z", "-v", "--no-index", "--stdin"],
        max_bytes=max_bytes, ok=(0, 1), input_bytes=payload,
    )
    fields = _complete_fields(raw, b"\0", truncated)
    if not truncated and len(fields) % 4:
        raise GitInspectionError("malformed check-ignore output")
    records = []
    for index in range(0, len(fields) - 3, 4):
        source, line, pattern, path = fields[index:index + 4]
        pattern_text = _decode(pattern)
        records.append({
            "path": _decode(path),
            "source": _decode(source),
            "line": int(line or b"0"),
            "pattern": pattern_text,
            "status": "not_ignored" if pattern_text.startswith("!") else "ignored",
        })
    by_path = {row["path"]: row for row in records}
    ordered = []
    for path in paths:
        row = by_path.get(path)
        ordered.append(dict(row) if row is not None else {
            "path": path,
            "status": "not_measured" if truncated else "not_ignored",
        })
    return {"records": ordered, "truncated": truncated}
