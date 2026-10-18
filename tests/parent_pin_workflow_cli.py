"""Parse parent-pin-check workflow ``run`` blocks (stdlib only, test support)."""
from __future__ import annotations

import shlex


def cli_excludes_from_shell_run(run: str) -> list[str]:
    """Collect every ``--exclude`` / ``--exclude=`` from the first ``parent_pin_check.py`` command."""
    segments: list[str] = []
    buf = ""
    for line in run.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if buf:
            buf = f"{buf} {stripped}"
        else:
            buf = stripped
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        segments.append(buf)
        buf = ""
    if buf:
        segments.append(buf)

    command = ""
    for segment in segments:
        if "parent_pin_check.py" in segment:
            command = segment
            break
    if not command:
        return []

    tokens = shlex.split(command, posix=True)
    excludes: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--exclude":
            if index + 1 >= len(tokens):
                raise ValueError("--exclude requires a value")
            excludes.append(tokens[index + 1])
            index += 2
            continue
        if token.startswith("--exclude="):
            excludes.append(token.partition("=")[2])
        index += 1
    return excludes
