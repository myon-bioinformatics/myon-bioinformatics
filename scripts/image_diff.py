#!/usr/bin/env python3
"""Compare two images (files or zip members) and print one line: size, changed pixels, bbox, max delta.

Needs Pillow (only dependency). Optional --clip X,Y,W,H in CSS px, scaled by
image width / --viewport-width (screenshots taken at devicePixelRatio != 1).
Source forms: PATH or ZIP::member. Exit 0 = identical (within --tolerance), 1 = differs, 2 = input error.
"""
from __future__ import annotations

import argparse
import io
import math
import os
import sys
import zipfile


MAX_INPUT_BYTES = 64 * 1024 * 1024


def finite_positive(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('value must be numeric') from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('value must be finite and positive')
    return number


def _require_pillow_import():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("image_diff: Pillow is required (pip install pillow)", file=sys.stderr)
        raise SystemExit(2) from None


def parse_clip(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("clip must be X,Y,W,H (four comma-separated values)")
    try:
        x, y, w, h = (float(part.strip()) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("clip values must be numeric") from None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        raise argparse.ArgumentTypeError('clip values must be finite')
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("clip width and height must be positive")
    return x, y, w, h


def load(spec: str):
    _require_pillow_import()
    from PIL import Image

    if "::" in spec:
        archive_path, member = spec.split("::", 1)
        if not member:
            raise ValueError(f"zip member path is empty in {spec!r}")
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.getinfo(member)
            if info.file_size > MAX_INPUT_BYTES:
                raise ValueError('image input exceeds size limit')
            with archive.open(info) as stream:
                data = stream.read(MAX_INPUT_BYTES + 1)
            if len(data) > MAX_INPUT_BYTES:
                raise ValueError('image input exceeds size limit')
            return Image.open(io.BytesIO(data)).convert('RGBA')
    if os.path.getsize(spec) > MAX_INPUT_BYTES:
        raise ValueError('image input exceeds size limit')
    with open(spec, 'rb') as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('image input exceeds size limit')
    return Image.open(io.BytesIO(data)).convert('RGBA')


def scaled_clip_box(
    clip: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
    viewport_width: float | None,
) -> tuple[int, int, int, int]:
    x, y, w, h = clip
    scale = image_width / viewport_width if viewport_width is not None else 1.0
    values = (scale, x * scale, y * scale, (x + w) * scale, (y + h) * scale)
    if not all(math.isfinite(value) for value in values):
        raise ValueError('clip coordinates must remain finite after scaling')
    left = round(x * scale)
    top = round(y * scale)
    right = round((x + w) * scale)
    bottom = round((y + h) * scale)
    if left < 0 or top < 0 or right > image_width or bottom > image_height:
        raise ValueError("clip rectangle is outside image bounds")
    if right <= left or bottom <= top:
        raise ValueError("clip rectangle has zero area after scaling")
    return left, top, right, bottom


def compare(image_a, image_b, clip=None, viewport_width=None, tolerance=0):
    _require_pillow_import()
    from PIL import ImageChops

    if image_a.size != image_b.size:
        return {
            "size_a": image_a.size,
            "size_b": image_b.size,
            "same_size": False,
            "differs": True,
        }
    work_a, work_b = image_a, image_b
    if clip is not None:
        box = scaled_clip_box(clip, image_a.width, image_a.height, viewport_width)
        work_a, work_b = image_a.crop(box), image_b.crop(box)
    diff = ImageChops.difference(work_a, work_b)
    bands = diff.split()
    max_band = bands[0]
    for band in bands[1:]:
        max_band = ImageChops.lighter(max_band, band)
    mask = max_band.point(lambda value: 255 if value > tolerance else 0, mode="L")
    changed = mask.histogram()[255]
    total = mask.width * mask.height
    return {
        "size": work_a.size,
        "changed": changed,
        "ratio": round(changed / total, 6) if total else 0,
        "bbox": mask.getbbox(),
        "max_delta": max(high for _low, high in diff.getextrema()),
        "same_size": True,
        "differs": changed > 0,
    }


def format_result(result: dict) -> str:
    return " ".join(f"{key}={value}" for key, value in result.items())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--clip", type=parse_clip, metavar="X,Y,W,H")
    parser.add_argument("--viewport-width", type=finite_positive)
    parser.add_argument("--tolerance", type=int, default=0)
    args = parser.parse_args(argv)

    if not 0 <= args.tolerance <= 255:
        print("image_diff: tolerance must be between 0 and 255", file=sys.stderr)
        return 2
    if args.viewport_width is not None and args.viewport_width <= 0:
        print("image_diff: --viewport-width must be positive", file=sys.stderr)
        return 2
    if args.clip is not None and args.viewport_width is None:
        # CSS clip without viewport width uses 1:1 mapping (scale = image width / viewport omitted).
        pass

    try:
        result = compare(
            load(args.a),
            load(args.b),
            clip=args.clip,
            viewport_width=args.viewport_width,
            tolerance=args.tolerance,
        )
    except SystemExit:
        raise
    except (OSError, KeyError, zipfile.BadZipFile, ValueError) as exc:
        print(f"image_diff: {exc}", file=sys.stderr)
        return 2
    print(format_result(result))
    return 1 if result["differs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
