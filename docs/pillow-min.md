# pillow_min.py

`pillow_min.py` is a Pillow replacement for PNG and APNG. It is a single file using only standard modules and runs on Python 3.9+. The source of truth is this repository's `tools/stdlib_min/pillow_min.py` (moved from Ironmate on 2026-11-15; it started in [Ironmate #153](https://github.com/myon-bioinformatics/Ironmate/issues/153)). Consumers lock the exact bytes from here, like the other `tools/stdlib_min/` stand-ins.

## Scope

The Pillow 13 APIs that consumers actually call are implemented to give the same results.

| Category | API |
|---|---|
| Reading | `Image.open` (path, bytes, file-like object), `n_frames` / `is_animated` / `seek` / `tell`, `info` |
| Creating images | `Image.new` / `frombytes` / `putdata` / `putpixel` / `putpalette` / `getpalette` |
| Conversion / access | `convert` (to L / LA / RGB / RGBA), `crop`, `resize` (`NEAREST` / `BOX`), `split`, `point`, `histogram`, `getbbox`, `getextrema`, `getpixel`, `tobytes`, `width` / `height` / `size` / `mode` |
| Writing | `save` (PNG only; modes 1 / L / LA / P / I;16 / RGB / RGBA, `transparency` / `bits` / `compress_level`) |
| Differences | `ImageChops.difference` / `lighter` / `darker` |
| Own helpers | `open_bytes` (returns RGBA), `decode_png_rgba`, `iter_apng_frames`, `encode_png`, `decode_base64`, `Image.to_base64`, `diff_metrics`, `paeth`, `unfilter_scanlines` |

PNG supports all color types and bit depths (1 / 2 / 4 / 8 / 16-bit), interlacing (Adam7), tRNS and short palettes. The mode on open matches Pillow (e.g. 16-bit gray is `I;16`, 16-bit gray + alpha is `RGBA`, 1-bit gray is `1`).

## Matching Pillow's behavior

To match Pillow's results, the following behaviors are reproduced as-is.

- Converting 16-bit gray (`I;16`) to 8-bit clamps at 255 instead of scaling.
- tRNS transparency keys compare only the low 8 bits. For 2-bit and 4-bit gray, the expanded value is compared to the key directly.
- Grayscale conversion uses the same fixed-point formula as Pillow (L24).
- An integer color passed to `Image.new` is unpacked little-endian for multi-band modes. The RGBA default is transparent black.
- APNG frame compositing follows Pillow's dispose / blend steps. OVER compositing uses the masked-paste formula. As in Pillow, the result of "dispose to previous" depends on whether the previous frame's pixels were read.
- Size limits match Pillow. Above `MAX_IMAGE_PIXELS` (default 89,478,485 pixels) gives `DecompressionBombWarning`; above twice that gives `DecompressionBombError`. The value is a module attribute re-read on each call.

## Intentional differences from Pillow

| Item | Pillow | pillow_min |
|---|---|---|
| Formats | Also reads JPEG, WebP, GIF, etc. | PNG and APNG only; others raise `UnsupportedImageError` |
| `resize` | Six filters; the default is `BICUBIC`; `BOX` weighs partly covered source pixels | `NEAREST` and `BOX` only; the default is `BOX` (L / LA / RGB / RGBA) or `NEAREST` (1 / P / I;16); `BOX` averages whole source pixels (integer block edges, alpha-weighted), so results can differ from Pillow by a step at block edges |
| Broken files | Skips CRC on some chunks; may read files with trailing data or truncated files | Verifies CRC on every chunk; trailing data and truncated files error on open |
| Error types | Subclasses of `OSError` (`UnidentifiedImageError` etc.) | `ImageDecodeError` subclasses both `ValueError` and `OSError`, so catching either works. Message wording differs |
| OVER compositing of 16-bit gray APNG | Fails with `conversion from I;16 to RGBA not supported` | Composites (the mask is treated as opaque) |
| Second and later frames of interlaced APNG | Fails due to a Pillow 13 bug (`decoderconfig` grows per frame) | Reads |
| Rewinding (`seek` to an earlier frame) | OVER compositing rewrites the palette alpha, so the converted result may differ from a fresh open | Same result as a fresh open seeking to the same frame |
| `info['transparency']` after conversion | Kept, rewritten e.g. as an RGB key depending on the target | Removed on conversion to another mode |
| Unsupported APIs | — | APNG writing (`save_all`), conversion to P / 1 / I;16, `resize` / `paste` / `ImageDraw`, `histogram` / `point` on `I;16`, text/dpi/gamma/ICC `info` |
| Speed | C implementation | Pure Python. Reading 1280×720 takes 0.1–1 s (slower with more Paeth filters). `image_diff`-equivalent diffing is around 0.3 s |

## Verification

- Ironmate `tests/test_grid.py` (consumer side, runs against its locked copy): seeded random comparison with Pillow as the reference (Pillow is a dev-only dependency; `pillow_min` itself does not import it). It compares reading all color types, bit depths, filters, interlacing and out-of-range transparency keys, plus every frame of Pillow-written and hand-made APNGs (partial frames, all dispose / blend modes, with/without default image, split fdAT). Frames are compared both "reading each frame in sequence" and "seeking directly". It also compares each Image / ImageChops operation, `save` round-trips and limit handling.
- Ironmate `tests/test_grid.py`: unit tests added in Ironmate #152.
- browser-test-kit's `scripts/image_diff.py` was switched to pillow_min and compared with the Pillow version. Over 300 random cases (all PNG formats, APNG, zip members, clip / viewport, tolerance, broken inputs), exit codes and stdout all matched. Only error wording on broken inputs differed.
