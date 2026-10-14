from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "image_diff.py"
spec = importlib.util.spec_from_file_location("image_diff", SCRIPT)
image_diff = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(image_diff)


def run_image_diff(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def solid_rgba(size: tuple[int, int], color: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new("RGBA", size, color)
    return image


def write_png(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def test_identical_images_exit_zero(tmp_path: Path) -> None:
    base = solid_rgba((100, 50), (255, 255, 255, 255))
    left = tmp_path / "a.png"
    right = tmp_path / "b.png"
    write_png(left, base)
    write_png(right, base.copy())
    proc = run_image_diff(str(left), str(right))
    assert proc.returncode == 0
    assert "changed=0" in proc.stdout
    assert "differs=False" in proc.stdout


def test_red_rect_diff_metrics(tmp_path: Path) -> None:
    base = solid_rgba((100, 50), (255, 255, 255, 255))
    changed = base.copy()
    ImageDraw.Draw(changed).rectangle((20, 10, 29, 14), fill=(255, 0, 0, 255))
    left = tmp_path / "a.png"
    right = tmp_path / "b.png"
    write_png(left, base)
    write_png(right, changed)
    proc = run_image_diff(str(left), str(right))
    assert proc.returncode == 1
    assert "changed=50" in proc.stdout
    assert "bbox=(20, 10, 30, 15)" in proc.stdout


def test_clip_excluding_rect_exit_zero(tmp_path: Path) -> None:
    base = solid_rgba((100, 50), (255, 255, 255, 255))
    changed = base.copy()
    ImageDraw.Draw(changed).rectangle((20, 10, 29, 14), fill=(255, 0, 0, 255))
    left = tmp_path / "a.png"
    right = tmp_path / "b.png"
    write_png(left, base)
    write_png(right, changed)
    proc = run_image_diff(str(left), str(right), "--clip", "0,0,20,50")
    assert proc.returncode == 0
    assert "changed=0" in proc.stdout


def test_viewport_width_scales_clip(tmp_path: Path) -> None:
    """DPR=2: 780px-wide image with 390 CSS viewport; clip top bar in CSS coords."""
    base = solid_rgba((780, 200), (255, 255, 255, 255))
    changed = base.copy()
    draw = ImageDraw.Draw(changed)
    draw.rectangle((0, 120, 779, 199), fill=(0, 0, 255, 255))
    left = tmp_path / "a.png"
    right = tmp_path / "b.png"
    write_png(left, base)
    write_png(right, changed)
    proc = run_image_diff(
        str(left),
        str(right),
        "--clip",
        "0,0,390,60",
        "--viewport-width",
        "390",
    )
    assert proc.returncode == 0
    assert "changed=0" in proc.stdout


def test_tolerance_boundary(tmp_path: Path) -> None:
    size = (2, 1)
    a = Image.new("RGBA", size, (100, 100, 100, 255))
    b = Image.new("RGBA", size, (100, 100, 100, 255))
    b.putpixel((0, 0), (105, 100, 100, 255))
    left = tmp_path / "a.png"
    right = tmp_path / "b.png"
    write_png(left, a)
    write_png(right, b)
    assert run_image_diff(str(left), str(right), "--tolerance", "5").returncode == 0
    assert run_image_diff(str(left), str(right), "--tolerance", "4").returncode == 1


def test_different_sizes_exit_one(tmp_path: Path) -> None:
    write_png(tmp_path / "a.png", solid_rgba((10, 10), (0, 0, 0, 255)))
    write_png(tmp_path / "b.png", solid_rgba((11, 10), (0, 0, 0, 255)))
    proc = run_image_diff(str(tmp_path / "a.png"), str(tmp_path / "b.png"))
    assert proc.returncode == 1
    assert "same_size=False" in proc.stdout
    assert "size_a=(10, 10)" in proc.stdout
    assert "size_b=(11, 10)" in proc.stdout


def test_zip_member_success_and_missing(tmp_path: Path) -> None:
    image = solid_rgba((8, 8), (1, 2, 3, 255))
    archive = tmp_path / "evidence.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("t/header.png", _png_bytes(image))
    proc = run_image_diff(f"{archive}::t/header.png", f"{archive}::t/header.png")
    assert proc.returncode == 0
    missing = run_image_diff(f"{archive}::t/nope.png", f"{archive}::t/header.png")
    assert missing.returncode == 2
    assert "image_diff:" in missing.stderr


def test_nonexistent_file_exit_two(tmp_path: Path) -> None:
    proc = run_image_diff(str(tmp_path / "missing.png"), str(tmp_path / "also-missing.png"))
    assert proc.returncode == 2


def test_bad_zip_exit_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    proc = run_image_diff(f"{bad}::member.png", f"{bad}::member.png")
    assert proc.returncode == 2


@pytest.mark.parametrize(
    "clip",
    [
        "1,2,3",
        "a,b,c,d",
        "0,0,0,10",
        "0,0,10,-1",
    ],
)
def test_invalid_clip_exit_two(tmp_path: Path, clip: str) -> None:
    left = tmp_path / "a.png"
    write_png(left, solid_rgba((20, 20), (0, 0, 0, 255)))
    proc = run_image_diff(str(left), str(left), "--clip", clip)
    assert proc.returncode == 2


def test_clip_out_of_bounds_exit_two(tmp_path: Path) -> None:
    left = tmp_path / "a.png"
    write_png(left, solid_rgba((10, 10), (0, 0, 0, 255)))
    proc = run_image_diff(str(left), str(left), "--clip", "0,0,20,20")
    assert proc.returncode == 2
    assert "outside image bounds" in proc.stderr


def test_viewport_width_non_positive_exit_two(tmp_path: Path) -> None:
    left = tmp_path / "a.png"
    write_png(left, solid_rgba((10, 10), (0, 0, 0, 255)))
    proc = run_image_diff(str(left), str(left), "--viewport-width", "0")
    assert proc.returncode == 2


def test_tolerance_out_of_range_exit_two(tmp_path: Path) -> None:
    left = tmp_path / "a.png"
    write_png(left, solid_rgba((4, 4), (0, 0, 0, 255)))
    assert run_image_diff(str(left), str(left), "--tolerance", "256").returncode == 2
    assert run_image_diff(str(left), str(left), "--tolerance", "-1").returncode == 2


def test_missing_pillow_exit_two(tmp_path: Path) -> None:
    left = tmp_path / "a.png"
    write_png(left, solid_rgba((4, 4), (0, 0, 0, 255)))
    code = textwrap.dedent(
        f"""
        import builtins
        import runpy
        import sys
        _real = builtins.__import__
        def _block(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("No module named 'PIL'")
            return _real(name, globals, locals, fromlist, level)
        builtins.__import__ = _block
        sys.argv = [{repr(str(SCRIPT))}, {repr(str(left))}, {repr(str(left))}]
        runpy.run_path({repr(str(SCRIPT))}, run_name="__main__")
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 2
    assert proc.stderr.strip() == "image_diff: Pillow is required (pip install pillow)"


def _png_bytes(image: Image.Image) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("flag,value", [
    ("--viewport-width", "nan"), ("--viewport-width", "inf"),
    ("--viewport-width", "-inf"), ("--clip", "nan,0,1,1"),
    ("--clip", "0,inf,1,1"), ("--clip", "0,0,inf,1"),
    ("--clip", "0,0,1,nan"), ("--clip", "1e308,0,1e308,1"),
])
def test_nonfinite_and_overflow_input_exit_two(tmp_path, flag, value):
    path = tmp_path / "ok.png"
    write_png(path, solid_rgba((10, 10), (0, 0, 0, 255)))
    proc = run_image_diff(str(path), str(path), flag, value)
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr


def test_regular_file_over_limit_exit_two(tmp_path, monkeypatch):
    path = tmp_path / "ok.png"
    write_png(path, solid_rgba((10, 10), (0, 0, 0, 255)))
    monkeypatch.setattr(image_diff, "MAX_INPUT_BYTES", 16)
    with pytest.raises(ValueError, match="size limit"):
        image_diff.load(str(path))


def test_compressed_zip_member_over_limit_exit_two(tmp_path, monkeypatch):
    archive = tmp_path / "compressed.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.png", b"0" * 8192)
    monkeypatch.setattr(image_diff, "MAX_INPUT_BYTES", 1024)
    with pytest.raises(ValueError, match="size limit"):
        image_diff.load(f"{archive}::huge.png")


def test_normal_zip_and_file_within_limit(tmp_path):
    path = tmp_path / "ok.png"
    write_png(path, solid_rgba((8, 8), (1, 2, 3, 255)))
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, "ok.png")
    assert run_image_diff(str(path), f"{archive}::ok.png").returncode == 0


def test_negative_clip_coordinate_exit_two(tmp_path):
    path = tmp_path / "ok.png"
    write_png(path, solid_rgba((10, 10), (0, 0, 0, 255)))
    proc = run_image_diff(str(path), str(path), "--clip", "-1,0,5,5")
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
