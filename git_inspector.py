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


def _run(root, args, *, max_bytes=1_000_000, ok=(0,), input_bytes=None,
         delimiter=None):
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
    if truncated and delimiter is not None and not raw.endswith(delimiter):
        boundary = raw.rfind(delimiter)
        raw = b"" if boundary < 0 else raw[:boundary + len(delimiter)]
    return raw, truncated, proc.returncode


def _decode(raw):
    return raw.decode("utf-8", "surrogateescape")


def _complete_nul_fields(raw, truncated):
    """Return only complete NUL-delimited fields from bounded structured output."""
    if truncated and not raw.endswith(b"\0"):
        raw = raw.rsplit(b"\0", 1)[0] + b"\0" if b"\0" in raw else b""
    return [item for item in raw.split(b"\0") if item]


def status(root=".", *, max_bytes=1_000_000):
    """Return structured porcelain-v2 status without inventing identity."""
    raw, truncated, _ = _run(
        root, ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        max_bytes=max_bytes, delimiter=b"\\0",
    )
    chunks = raw.split(b"\\0")
    if chunks and chunks[-1] == b"":
        chunks.pop()
    records = []
    index = 0
    while index < len(chunks):
        text = _decode(chunks[index])
        kind = text[:1]
        if kind == "2":
            fields = text.split(" ", 9)
            if len(fields) != 10 or index + 1 >= len(chunks):
                truncated = True
                break
            records.append({"kind": "2", "record": text, "path": fields[9],
                            "orig_path": _decode(chunks[index + 1])})
            index += 2
            continue
        if kind == "1":
            fields = text.split(" ", 8)
            path = fields[8] if len(fields) == 9 else None
        elif kind == "u":
            fields = text.split(" ", 10)
            path = fields[10] if len(fields) == 11 else None
        elif kind in ("?", "!") and text.startswith(kind + " "):
            path = text[2:]
        else:
            path = None
        records.append({"kind": kind, "record": text, "path": path})
        index += 1
    return {"clean": not records, "records": records, "truncated": truncated}
