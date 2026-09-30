"""Bounded read-only Git inspection helpers.

This module intentionally exposes named observations instead of arbitrary Git
argv. It never fetches, checks out, stages, commits, resets, cleans, merges,
rebases, pushes, or mutates refs/worktree state.

Repository/head identity and provenance belong to the canonical repository
metadata producer. Revisions returned here are transient observations only.
"""

from __future__ import annotations

import os
import subprocess
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


def _run(root, args, *, max_bytes=1_000_000, ok=(0,), input_bytes=None):
    _positive(max_bytes, "max_bytes")
    if input_bytes is not None and not isinstance(input_bytes, bytes):
        raise TypeError("input_bytes must be bytes")
    command = ["git", "-C", str(_root(root)), "--no-pager", *args]
    try:
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                  "check": False, "shell": False}
        if input_bytes is None:
            kwargs["stdin"] = subprocess.DEVNULL
        else:
            kwargs["input"] = input_bytes
        proc = subprocess.run(command, **kwargs)
    except FileNotFoundError as error:
        raise GitInspectionError("git executable not found") from error
    except OSError as error:
        raise GitInspectionError(type(error).__name__) from error
    if proc.returncode not in ok:
        raise GitInspectionError("git exited with status " + str(proc.returncode))
    raw = proc.stdout
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    return raw, truncated, proc.returncode


def _decode(raw):
    return raw.decode("utf-8", "surrogateescape")


def _complete_nul_fields(raw, truncated):
    """Return only complete NUL-delimited fields from bounded structured output."""
    if truncated and not raw.endswith(b"\0"):
        raw = raw.rsplit(b"\0", 1)[0] + (b"\0" if b"\0" in raw else b"")
    return [item for item in raw.split(b"\0") if item]


def status(root="."):
    """Return porcelain-v2 status records without inventing repository identity."""
    raw, truncated, _ = _run(
        root, ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
    )
    fields = _complete_nul_fields(raw, truncated)
    records = []
    index = 0
    while index < len(fields):
        text = _decode(fields[index])
        kind = text[:1]
        if kind == "2":
            if index + 1 >= len(fields):
                truncated = True
                break
            records.append({
                "kind": kind,
                "record": text,
                "original_path": _decode(fields[index + 1]),
            })
            index += 2
            continue
        records.append({"kind": kind, "record": text})
        index += 1
    return {"clean": not records, "records": records, "truncated": truncated}


def ls_files(root=".", *, max_files=10_000, max_bytes=1_000_000):
    """Return a bounded NUL-safe tracked-file inventory."""
    _positive(max_files, "max_files")
    raw, byte_truncated, _ = _run(root, ["ls-files", "-z"], max_bytes=max_bytes)
    paths = [_decode(item) for item in _complete_nul_fields(raw, byte_truncated)]
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
    if path is not None:
        args.extend(["--", _path(path)])
    raw, truncated, _ = _run(root, args, max_bytes=max_bytes)
    return {"patch": _decode(raw), "truncated": truncated}


def log(root=".", *, max_count=50, path=None):
    """Return bounded commit observations; not canonical repository metadata."""
    _positive(max_count, "max_count")
    fmt = "%H%x1f%aI%x1f%an%x1f%s%x1e"
    args = ["log", "--no-decorate", "--no-color", "--format=" + fmt,
            "--max-count=" + str(max_count)]
    if path is not None:
        args.extend(["--", _path(path)])
    raw, truncated, _ = _run(root, args)
    rows = []
    for record in raw.split(b"\x1e"):
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
        # --end-of-options separates revision parsing; path is encoded in the
        # revision:path object expression and cannot begin with an option.
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

    Content/line output is intentionally omitted: Git's grep record separator
    does not make arbitrary newline-containing filenames and matched line text
    simultaneously unambiguous. Callers can inspect an explicit path separately.
    """
    if not isinstance(pattern, str) or not pattern or "\\x00" in pattern:
        raise ValueError("pattern must be a non-empty string without NUL")
    raw, truncated, code = _run(
        root, ["grep", "-z", "-l", "-I", "-F", "-e", pattern, "--"],
        max_bytes=max_bytes, ok=(0, 1),
    )
    paths = [] if code == 1 else [_decode(p) for p in raw.split(b"\\0") if p]
    return {"paths": paths, "truncated": truncated}
