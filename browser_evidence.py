#!/usr/bin/env python3
"""Shared stdlib-only screenshot evidence manifest; independent browser engines."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

SCHEMA = "browser-screenshot-evidence/1"
ENGINES = frozenset({"playwright", "stagehand"})


def validate_png(data: bytes) -> None:
    """Check PNG chunk framing, CRC and required chunks (not pixel decoding)."""
    if not data.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
        raise ValueError("invalid PNG signature")
    offset = 8
    seen_ihdr = seen_idat = seen_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("truncated PNG chunk")
        size = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError("truncated PNG chunk")
        body = data[offset + 8:offset + 8 + size]
        expected_crc = struct.unpack_from(">I", data, offset + 8 + size)[0]
        if zlib.crc32(kind + body) != expected_crc:
            raise ValueError("PNG chunk CRC mismatch")
        if not seen_ihdr:
            if kind != b"IHDR" or size != 13:
                raise ValueError("missing PNG IHDR")
            width, height = struct.unpack_from(">II", body)
            if width == 0 or height == 0:
                raise ValueError("invalid PNG dimensions")
            seen_ihdr = True
        elif kind == b"IHDR":
            raise ValueError("duplicate PNG IHDR")
        if kind == b"IDAT":
            seen_idat = True
        if kind == b"IEND":
            if size != 0 or not seen_idat or end != len(data):
                raise ValueError("invalid PNG IEND")
            seen_iend = True
            break
        offset = end
    if not (seen_ihdr and seen_idat and seen_iend):
        raise ValueError("incomplete PNG")


def record(engine: str, screenshot: Path, root: Path, *, run_id: str, head_sha: str) -> dict:
    if engine not in ENGINES:
        raise ValueError("unsupported browser engine")
    if not run_id or not run_id.strip():
        raise ValueError("run_id is required")
    if len(head_sha) != 40 or any(c not in "0123456789abcdef" for c in head_sha):
        raise ValueError("head_sha must be a lowercase 40-character Git SHA")
    base = root.resolve(strict=True)
    image = screenshot.resolve(strict=True)
    if not image.is_relative_to(base) or not image.is_file() or image.suffix.lower() != ".png":
        raise ValueError("screenshot must be a PNG file within the evidence root")
    data = image.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid PNG signature")
    return {
        "schema": SCHEMA,
        "engine": engine,
        "run_id": run_id,
        "head_sha": head_sha,
        "screenshot": image.relative_to(base).as_posix(),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Record one engine's screenshot evidence")
    parser.add_argument("--engine", choices=sorted(ENGINES), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = record(args.engine, args.screenshot, args.root, run_id=args.run_id, head_sha=args.head_sha)
    except (OSError, ValueError) as exc:
        parser.exit(2, "browser-evidence: " + str(exc) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
