"""Minimal Pillow replacement for PNG/APNG read/write and image diff (stdlib only, Python 3.9+).

Single-file module: no third-party imports. Non-PNG formats are rejected with a clear error.

The ``Image`` / ``ImageChops`` surface follows Pillow 13 for the calls our consumers make
(``Image.open`` / ``new`` / ``convert`` / ``crop`` / ``split`` / ``point`` / ``histogram`` /
``getbbox`` / ``getextrema`` / ``putdata`` / ``putpalette`` / ``save`` / APNG ``seek``, and
``ImageChops.difference`` / ``lighter``). Decoded pixels, modes and ``info`` keys match Pillow,
including its quirks (16-bit grayscale clips to 255 when converted to 8-bit; a 2/4-bit grayscale
tRNS key is compared after scaling; APNG frames are composited with Pillow's dispose/blend model).
Known, intentional differences from Pillow are listed in ``docs/pillow-min.md``.
"""
from __future__ import annotations

import base64, binascii, os, re, struct, warnings, zlib
from operator import add, sub; from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

__all__ = [
    'Image',
    'ImageChops',
    'ImageDecodeError',
    'UnsupportedImageError',
    'DecompressionBombError',
    'DecompressionBombWarning',
    'MAX_IMAGE_PIXELS',
    'MAX_PIXELS',
    'MAX_DIMENSION',
    'decode_base64',
    'decode_png_rgba',
    'encode_png',
    'open_bytes',
    'diff_metrics',
    'iter_apng_frames',
    'paeth',
    'unfilter_scanlines',
]

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
# Pillow's defaults: warn above MAX_IMAGE_PIXELS, refuse above twice that. Like Pillow,
# ``Image.open`` reads this module attribute at call time, so callers may change it.
MAX_IMAGE_PIXELS: Optional[int] = 89478485
MAX_PIXELS = 2 * 89478485
MAX_DIMENSION = 2 ** 31 - 1
MAX_DECOMPRESSED = None  # default: exactly the size the PNG header implies
_DATA_URI = re.compile(
    r'data:([\w.+-]+/[\w.+-]+)?((?:;[\w.+-]+=[\w.+-]+)*);base64,',
    re.ASCII | re.IGNORECASE,
)
_BASE64 = re.compile(r'[A-Za-z0-9+/]*={0,2}', re.ASCII)
_SIMPLE_PALETTE = re.compile(b'^\xff*\x00\xff*$')

_ADAM7_START = ((0, 0), (4, 0), (0, 4), (2, 0), (0, 2), (1, 0), (0, 1))
_ADAM7_INCREMENT = ((8, 8), (8, 8), (4, 8), (4, 4), (2, 4), (2, 2), (1, 2))

_COLOR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
# (depth, color type) -> Pillow mode, as in PngImagePlugin._MODES
_PNG_MODES = {
    (1, 0): '1', (2, 0): 'L', (4, 0): 'L', (8, 0): 'L', (16, 0): 'I;16',
    (8, 2): 'RGB', (16, 2): 'RGB',
    (1, 3): 'P', (2, 3): 'P', (4, 3): 'P', (8, 3): 'P',
    (8, 4): 'LA', (16, 4): 'RGBA',
    (8, 6): 'RGBA', (16, 6): 'RGBA',
}
_BPP = {'1': 1, 'L': 1, 'P': 1, 'LA': 2, 'I;16': 2, 'RGB': 3, 'RGBA': 4}
_BANDS = {'1': 1, 'L': 1, 'P': 1, 'LA': 2, 'I;16': 1, 'RGB': 3, 'RGBA': 4}
_MODE_BANDNAMES = {'1': ('1',), 'L': ('L',), 'P': ('P',), 'LA': ('L', 'A'), 'I;16': ('I',),
                   'RGB': ('R', 'G', 'B'), 'RGBA': ('R', 'G', 'B', 'A')}

# Lookup tables for C-speed byte arithmetic through ``map``.
_WRAP = list(range(256)) * 2                             # (a + b) & 255 for 0 <= a + b <= 510
_ABS = list(range(256)) + list(range(255, 0, -1))         # abs(d) for -255 <= d <= 255 via list[d]


class ImageDecodeError(ValueError, OSError):
    """PNG or image payload could not be decoded (catchable as ValueError or OSError)."""


class UnsupportedImageError(ImageDecodeError):
    """Format is not PNG."""


class DecompressionBombError(ImageDecodeError):
    """Image has more pixels than twice ``MAX_IMAGE_PIXELS`` (Pillow's hard limit)."""


class DecompressionBombWarning(RuntimeWarning):
    """Image has more pixels than ``MAX_IMAGE_PIXELS`` (Pillow's warning threshold)."""


def paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def unfilter_scanlines(raw: bytes, width: int, height: int, bpp: int) -> List[bytes]:
    """Reverse PNG scanline filters into ``height`` rows of ``width * bpp`` bytes."""
    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise ImageDecodeError('PNG image data has unexpected length')
    rows: List[bytes] = []
    prev = bytes(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        line = raw[start + 1:start + 1 + stride]
        if kind == 0:
            out = bytes(line)
        elif kind == 2:
            out = bytes(map(_WRAP.__getitem__, map(add, line, prev)))
        elif kind == 1:
            buf = bytearray(line)
            for i in range(bpp, stride):
                buf[i] = (buf[i] + buf[i - bpp]) & 255
            out = bytes(buf)
        elif kind == 3:
            buf = bytearray(line)
            for i in range(stride):
                left = buf[i - bpp] if i >= bpp else 0
                buf[i] = (buf[i] + ((left + prev[i]) >> 1)) & 255
            out = bytes(buf)
        elif kind == 4:
            buf = bytearray(line)
            for i in range(stride):
                if i >= bpp:
                    a, c = buf[i - bpp], prev[i - bpp]
                else:
                    a = c = 0
                b = prev[i]
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                buf[i] = (buf[i] + pred) & 255
            out = bytes(buf)
        else:
            raise ImageDecodeError(f'unknown PNG filter type {kind}')
        rows.append(out)
        prev = out
    return rows


def _check_limits(width: int, height: int, max_pixels: Optional[int] = None,
                  max_dimension: Optional[int] = None, *, warn: bool = False) -> None:
    if width <= 0 or height <= 0:
        raise ImageDecodeError('image has zero width or height')
    if max_dimension is not None and (width > max_dimension or height > max_dimension):
        raise ImageDecodeError(f'image dimension exceeds limit {max_dimension}')
    pixels = width * height
    if max_pixels is not None and pixels > max_pixels:
        raise DecompressionBombError(f'image has {pixels} pixels; limit is {max_pixels}')
    if warn and MAX_IMAGE_PIXELS is not None and pixels > MAX_IMAGE_PIXELS:
        warnings.warn(f'Image size ({pixels} pixels) exceeds limit of {MAX_IMAGE_PIXELS} pixels, '
                      'could be decompression bomb DOS attack.', DecompressionBombWarning)


def _default_max_pixels(value):
    if value is not None:
        return value
    return None if MAX_IMAGE_PIXELS is None else 2 * MAX_IMAGE_PIXELS


def _pass_size(width: int, height: int, pass_no: int) -> Tuple[int, int]:
    sx, sy = _ADAM7_START[pass_no]
    ix, iy = _ADAM7_INCREMENT[pass_no]
    w = 0 if sx >= width else (width - sx + ix - 1) // ix
    h = 0 if sy >= height else (height - sy + iy - 1) // iy
    return w, h


def _unpack_table(depth: int) -> List[bytes]:
    per = 8 // depth
    mask = (1 << depth) - 1
    return [bytes((byte >> (8 - depth * (k + 1))) & mask for k in range(per)) for byte in range(256)]


_UNPACK = {d: _unpack_table(d) for d in (1, 2, 4)}
_SCALE = {1: bytes(255 if v else 0 for v in range(256)),
          2: bytes((v * 85) & 255 for v in range(256)),
          4: bytes((v * 17) & 255 for v in range(256))}


def _unpack(line: bytes, width: int, depth: int) -> bytes:
    if depth == 8:
        return bytes(line[:width])
    return b''.join(map(_UNPACK[depth].__getitem__, line))[:width]


def _native_row(line: bytes, width: int, depth: int, color: int) -> bytes:
    """One unfiltered scanline -> pixel bytes in the Pillow mode's ``tobytes`` layout
    (mode '1' is kept unpacked as 0/255 bytes internally)."""
    if color == 3:
        return _unpack(line, width, depth)
    if color == 0:
        if depth < 8:
            return _unpack(line, width, depth).translate(_SCALE[depth])
        if depth == 8:
            return bytes(line)
        out = bytearray(len(line))  # I;16 big-endian -> little-endian
        out[0::2] = line[1::2]
        out[1::2] = line[0::2]
        return bytes(out)
    if depth == 16:
        high = line[0::2]
        if color == 4:  # Pillow opens 16-bit LA as RGBA via 'LA;16B'
            out = bytearray(width * 4)
            gray, alpha = high[0::2], high[1::2]
            out[0::4] = gray
            out[1::4] = gray
            out[2::4] = gray
            out[3::4] = alpha
            return bytes(out)
        return bytes(high)
    return bytes(line)


def _chunk_crc(kind: bytes, body: bytes, crc: bytes) -> None:
    if zlib.crc32(kind + body) != struct.unpack('>I', crc)[0]:
        raise ImageDecodeError(f'PNG chunk {kind!r} has a bad CRC')


def _decompress_idat(chunks: Iterable[bytes], expected: int, max_decompressed: Optional[int]) -> bytes:
    limit = expected if max_decompressed is None else max_decompressed
    inflater = zlib.decompressobj()
    out = bytearray()
    for piece in chunks:
        out.extend(inflater.decompress(piece, limit + 1 - len(out)))
        if len(out) > limit:
            raise ImageDecodeError('PNG image data exceeds decompressed size limit')
    if not inflater.eof:
        out.extend(inflater.decompress(inflater.unconsumed_tail, limit + 1 - len(out)))
    if inflater.unused_data:
        raise ImageDecodeError('PNG image data has extra bytes after the zlib stream')
    if len(out) != expected:
        raise ImageDecodeError('PNG image data has unexpected length')
    return bytes(out)


def _decode_samples(chunks, width, height, depth, color, interlace, max_decompressed) -> bytes:
    """Compressed image data of one frame -> native mode bytes (row-major)."""
    channels = _COLOR_CHANNELS[color]
    bits = depth * channels
    filter_bpp = max(1, bits // 8)

    def row_bytes(w):
        return (w * bits + 7) // 8

    if not interlace:
        raw_len = height * (row_bytes(width) + 1)
        if max_decompressed is not None and raw_len > max_decompressed:
            raise ImageDecodeError('PNG image data exceeds decompressed size limit')
        raw = _decompress_idat(chunks, raw_len, max_decompressed)
        if bits >= 8:
            rows = unfilter_scanlines(raw, width, height, filter_bpp)
        else:
            rows = unfilter_scanlines(raw, row_bytes(width), height, 1)
        return b''.join(_native_row(r, width, depth, color) for r in rows)
    passes = [(p,) + _pass_size(width, height, p) for p in range(7)]
    passes = [(p, pw, ph) for p, pw, ph in passes if pw and ph]
    raw_len = sum(ph * (row_bytes(pw) + 1) for _, pw, ph in passes)
    if max_decompressed is not None and raw_len > max_decompressed:
        raise ImageDecodeError('PNG image data exceeds decompressed size limit')
    raw = _decompress_idat(chunks, raw_len, max_decompressed)
    mode = _PNG_MODES[(depth, color)]
    bpp = _BPP[mode]
    out = bytearray(width * height * bpp)
    pos = 0
    for p, pw, ph in passes:
        need = ph * (row_bytes(pw) + 1)
        chunk = raw[pos:pos + need]
        pos += need
        if bits >= 8:
            rows = unfilter_scanlines(chunk, pw, ph, filter_bpp)
        else:
            rows = unfilter_scanlines(chunk, row_bytes(pw), ph, 1)
        sx, sy = _ADAM7_START[p]
        ix, iy = _ADAM7_INCREMENT[p]
        for py, line in enumerate(rows):
            native = _native_row(line, pw, depth, color)
            y = sy + py * iy
            base = y * width
            for px in range(pw):
                dst = (base + sx + px * ix) * bpp
                out[dst:dst + bpp] = native[px * bpp:px * bpp + bpp]
    return bytes(out)


class _PngFile:
    """Parsed chunk structure of one PNG/APNG (pixel data is decoded on demand)."""

    def __init__(self, data: bytes, max_pixels: Optional[int], max_dimension: Optional[int],
                 max_decompressed: Optional[int], warn: bool):
        if not data.startswith(PNG_SIGNATURE):
            raise UnsupportedImageError('only PNG input is supported')
        self.max_decompressed = max_decompressed
        pos = 8
        header = None
        palette = b''
        trns: Optional[bytes] = None
        idat: List[bytes] = []
        events: List[Tuple[bytes, bytes]] = []
        actl = None
        actl_count = 0
        seen_idat = False
        while True:
            if pos + 8 > len(data):
                raise ImageDecodeError('truncated PNG (missing IEND)')
            length, kind = struct.unpack('>I4s', data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + length]
            crc = data[pos + 8 + length:pos + 12 + length]
            if len(body) != length or len(crc) != 4:
                raise ImageDecodeError('truncated PNG chunk')
            _chunk_crc(kind, body, crc)
            pos += 12 + length
            if header is None and kind != b'IHDR':
                raise ImageDecodeError('PNG must start with IHDR')
            if kind == b'IHDR':
                if length != 13:
                    raise ImageDecodeError('invalid IHDR')
                header = struct.unpack('>IIBBBBB', body)
                width, height, depth, color, compression, filtering, interlace = header
                if (depth, color) not in _PNG_MODES:
                    raise ImageDecodeError(f'unsupported PNG (bit depth {depth}, color type {color})')
                if compression or filtering or interlace not in (0, 1):
                    raise ImageDecodeError('invalid PNG compression/filter/interlace method')
                _check_limits(width, height, max_pixels, max_dimension, warn=warn)
            elif kind == b'PLTE':
                if length % 3 or length > 768:
                    raise ImageDecodeError('invalid PLTE length')
                if header[3] == 3:  # Pillow ignores a suggested palette on truecolor images
                    palette = body
            elif kind == b'tRNS':
                if not seen_idat:  # Pillow only reads chunks before the image data
                    trns = body
            elif kind == b'acTL':
                if not seen_idat:
                    if length < 8:
                        raise ImageDecodeError('APNG contains truncated acTL chunk')
                    actl_count += 1
                    actl = body
            elif kind == b'IDAT':
                seen_idat = True
                idat.append(body)
                events.append((kind, body))
            elif kind in (b'fcTL', b'fdAT'):
                events.append((kind, body))
            elif kind == b'IEND':
                if pos != len(data):
                    raise ImageDecodeError(f'{len(data) - pos} bytes of trailing data after PNG IEND')
                break
        if header is None:
            raise ImageDecodeError('PNG missing IHDR')
        if not idat:
            raise ImageDecodeError('PNG has no image data')
        self.width, self.height, self.depth, self.color, _, _, self.interlace = header
        if self.color == 3 and not palette:
            raise ImageDecodeError('palette PNG missing PLTE')
        self.mode = _PNG_MODES[(self.depth, self.color)]
        self.palette = palette
        self.info = {}
        if self.interlace:
            self.info['interlace'] = 1
        if trns is not None:
            self._set_transparency(trns)
        self.idat = idat
        # --- APNG structure (Pillow: an invalid acTL falls back to the default image) ---
        self.frames: List[Tuple[Optional[dict], List[bytes]]] = []
        self.frame_error: Optional[Tuple[int, str]] = None
        self.default_image = False
        self.n_frames = 1
        self.loop = None
        if actl is not None and actl_count == 1:
            n_frames, loop = struct.unpack('>II', actl[:8])
            if 0 < n_frames <= 0x80000000:
                self.loop = loop
                self._collect_frames(events)
                self.n_frames = n_frames + (1 if self.default_image else 0)
        if not self.frames:
            self.frames = [(None, idat)]
        elif self.default_image:
            self.frames.insert(0, (None, idat))

    def _set_transparency(self, s: bytes) -> None:
        mode = self.mode
        if mode == 'P':
            if _SIMPLE_PALETTE.match(s):
                i = s.find(b'\0')
                if i >= 0:
                    self.info['transparency'] = i
            else:
                self.info['transparency'] = bytes(s)
        elif mode in ('1', 'L', 'I;16', 'RGB'):
            if len(s) < (6 if mode == 'RGB' else 2):
                raise ImageDecodeError('Truncated tRNS chunk')
            if mode == '1':
                self.info['transparency'] = 255 if struct.unpack('>H', s[:2])[0] else 0
            elif mode in ('L', 'I;16'):
                self.info['transparency'] = struct.unpack('>H', s[:2])[0]
            else:
                self.info['transparency'] = struct.unpack('>3H', s[:6])
        # LA / RGBA (and 16-bit LA, opened as RGBA): Pillow ignores tRNS

    def _collect_frames(self, events) -> None:
        """Group fcTL/IDAT/fdAT into frames like Pillow; frame errors surface on seek."""
        frames: List[Tuple[Optional[dict], List[bytes]]] = []
        seq_num = None
        current: Optional[Tuple[dict, List[bytes]]] = None
        default = False
        error: Optional[Tuple[int, str]] = None
        for kind, body in events:
            if kind == b'IDAT':
                if current is None and not frames:
                    default = True  # image data before the first fcTL is not part of the animation
                elif current is not None and not frames:
                    current[1].append(body)  # the first animation frame is stored in IDAT
                continue
            index = len(frames) + (1 if current is not None and current[1] else 0)
            if kind == b'fcTL':
                if len(body) < 26:
                    error = (index, 'APNG contains truncated fcTL chunk')
                    break
                seq = struct.unpack('>I', body[:4])[0]
                if (seq_num is None and seq != 0) or (seq_num is not None and seq_num != seq - 1):
                    error = (index, 'APNG contains frame sequence errors')
                    break
                seq_num = seq
                fw, fh, px, py, dnum, dden = struct.unpack('>IIIIHH', body[4:24])
                if not fw or not fh or px + fw > self.width or py + fh > self.height:
                    error = (index, 'APNG contains invalid frames')
                    break
                if current is not None:
                    if not current[1]:
                        error = (len(frames), 'APNG missing frame data')
                        break
                    frames.append(current)
                current = ({'bbox': (px, py, px + fw, py + fh),
                            'duration': float(dnum) / float(dden or 100) * 1000,
                            'disposal': body[24], 'blend': body[25]}, [])
                continue
            # fdAT
            if len(body) < 4:
                error = (index, 'APNG contains truncated fdAT chunk')
                break
            seq = struct.unpack('>I', body[:4])[0]
            if seq_num is None or seq != seq_num + 1:
                error = (index, 'APNG contains frame sequence errors')
                break
            seq_num = seq
            if current is not None:
                current[1].append(body[4:])
        else:
            if current is not None and current[1]:
                frames.append(current)
        self.default_image = default
        if error is not None:
            if error[0] == 0 and not default:
                raise ImageDecodeError(error[1])
            error = (error[0] + (1 if default else 0), error[1])
        self.frame_error = error
        self.frames = frames

    def decode_frame(self, index: int) -> Tuple[Optional[dict], bytes]:
        fc, chunks = self.frames[index]
        if fc is None:
            w, h = self.width, self.height
        else:
            x0, y0, x1, y1 = fc['bbox']
            w, h = x1 - x0, y1 - y0
        return fc, _decode_samples(chunks, w, h, self.depth, self.color, self.interlace,
                                   self.max_decompressed)


def _palette_tuples(palette: bytes) -> List[Tuple[int, int, int]]:
    return [tuple(palette[i:i + 3]) for i in range(0, len(palette) - len(palette) % 3, 3)]


def _l_from_rgb(r: int, g: int, b: int) -> int:
    return (r * 19595 + g * 38470 + b * 7471 + 0x8000) >> 16  # Pillow's L24


def _div255(value: int) -> int:
    tmp = value + 128
    return ((tmp >> 8) + tmp) >> 8


def _pack_color(mode: str, color) -> bytes:
    bands = _BANDS[mode]
    if color is None:
        color = 0
    if mode == 'I;16':
        value = color if isinstance(color, int) else int(color[0])
        return struct.pack('<H', max(0, min(65535, value)))
    if isinstance(color, (int, float)) and not isinstance(color, bool):
        value = int(color)
        if bands == 1:
            return bytes((max(0, min(255, value)),))
        return bytes((value >> (8 * k)) & 255 for k in range(bands))  # Pillow packs ints little-endian
    color = tuple(color)
    if mode == 'RGBA' and len(color) == 3:
        color = color + (255,)
    if mode == 'LA' and len(color) == 1:
        color = color + (255,)
    if len(color) != bands:
        raise ValueError(f'color must have {bands} values for mode {mode!r}')
    return bytes(max(0, min(255, int(v))) for v in color)


class Image:
    """Subset of ``PIL.Image.Image`` for PNG data (modes 1, L, LA, P, I;16, RGB, RGBA)."""

    modes = frozenset(_BPP)

    def __init__(self, mode: str, size: Tuple[int, int], data: bytes, *,
                 info: Optional[dict] = None, palette: Optional[bytes] = None):
        if mode not in self.modes:
            raise ValueError(f'unsupported mode {mode!r}')
        w, h = size
        if len(data) != w * h * _BPP[mode]:
            raise ValueError('image data length does not match size and mode')
        self.mode = mode
        self._size = (int(w), int(h))
        self._data = bytes(data)
        self.info = dict(info or {})
        self._palette = palette  # bytes 'RGB' triplets, P mode only
        self._png: Optional[_PngFile] = None
        self._loaded = True
        self._frame = 0
        self.n_frames = 1
        self.is_animated = False

    # --- basic attributes -------------------------------------------------------
    @property
    def size(self) -> Tuple[int, int]:
        return self._size

    @property
    def width(self) -> int:
        return self._size[0]

    @property
    def height(self) -> int:
        return self._size[1]

    def getbands(self) -> Tuple[str, ...]:
        return _MODE_BANDNAMES[self.mode]

    def __enter__(self) -> 'Image':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        pass

    def load(self) -> None:
        self._loaded = True

    def copy(self) -> 'Image':
        self._loaded = True
        return Image(self.mode, self._size, self._data, info=self.info, palette=self._palette)

    def _new(self, mode: str, size, data: bytes, palette=None) -> 'Image':
        return Image(mode, size, data, info=self.info, palette=palette)

    # --- construction -----------------------------------------------------------
    @classmethod
    def from_rgba_rows(cls, width: int, height: int, rows: Sequence[bytes]) -> 'Image':
        if len(rows) != height:
            raise ValueError('row count mismatch')
        stride = width * 4
        for row in rows:
            if len(row) != stride:
                raise ValueError('row stride mismatch')
        return cls('RGBA', (width, height), b''.join(rows))

    @staticmethod
    def new(mode: str, size: Tuple[int, int], color=0) -> 'Image':
        if mode not in Image.modes:
            raise ValueError(f'unsupported mode {mode!r}')
        w, h = size
        fill = _pack_color(mode, color) * (w * h)
        return Image(mode, (w, h), fill, palette=b'' if mode == 'P' else None)

    @staticmethod
    def frombytes(mode: str, size: Tuple[int, int], data: bytes) -> 'Image':
        if mode == '1':
            w, h = size
            stride = (w + 7) // 8
            rows = [_unpack(data[y * stride:(y + 1) * stride], w, 1).translate(_SCALE[1]) for y in range(h)]
            return Image('1', size, b''.join(rows))
        return Image(mode, size, data, palette=b'' if mode == 'P' else None)

    @classmethod
    def open(cls, fp, mode: str = 'r', formats=None, *, max_pixels: Optional[int] = None,
             max_dimension: Optional[int] = None, max_decompressed: Optional[int] = None) -> 'Image':
        if mode != 'r':
            raise ValueError(f'bad mode {mode!r}')
        if formats is not None and 'PNG' not in [str(f).upper() for f in formats]:
            raise UnsupportedImageError('only PNG input is supported')
        if isinstance(fp, (bytes, bytearray, memoryview)):
            data = bytes(fp)
        elif hasattr(fp, 'read'):
            data = fp.read()
        else:
            with open(os.fspath(fp), 'rb') as handle:
                data = handle.read()
        warn = max_pixels is None
        png = _PngFile(data, _default_max_pixels(max_pixels), max_dimension, max_decompressed, warn)
        fc, samples = png.decode_frame(0)
        im = cls(png.mode, (png.width, png.height), cls._place(png, fc, samples, None),
                 info=png.info, palette=png.palette if png.mode == 'P' else None)
        im._png = png
        im.n_frames = png.n_frames
        im.is_animated = png.n_frames > 1
        if png.loop is not None:
            im.info['loop'] = png.loop
        if png.default_image:
            im.info['default_image'] = True
        if fc is not None:
            im.info.update(fc)
        im._init_frames(fc)
        return im

    @staticmethod
    def _place(png: _PngFile, fc, samples: bytes, canvas: Optional[bytearray]) -> bytes:
        """Write a frame's samples into the canvas at its bbox (Pillow's decode into self.im)."""
        if fc is None and canvas is None:
            return samples
        bpp = _BPP[png.mode]
        W = png.width
        if canvas is None:
            canvas = bytearray(W * png.height * bpp)
        x0, y0, x1, y1 = fc['bbox'] if fc is not None else (0, 0, W, png.height)
        fw = x1 - x0
        for row in range(y1 - y0):
            dst = ((y0 + row) * W + x0) * bpp
            canvas[dst:dst + fw * bpp] = samples[row * fw * bpp:(row + 1) * fw * bpp]
        return bytes(canvas)

    # --- APNG frames (Pillow's PngImageFile._seek / load_end model) -------------
    def _init_frames(self, fc) -> None:
        self._frame = 0
        self._prev = None        # Pillow's _prev_im if the current frame is never loaded
        self._prev_loaded = None  # ... and after load_end pasted an OVER frame into it in place
        self._loaded = False
        self._dispose_op = fc['disposal'] if fc else None
        self._blend_op = fc['blend'] if fc else None
        self._extent = fc['bbox'] if fc else None
        self._frame0 = (self._data, dict(self.info))
        if self._prev is None and self._dispose_op == 2:
            self._dispose_op = 1

    def tell(self) -> int:
        return self._frame

    def seek(self, frame: int) -> None:
        if frame < 0 or frame >= self.n_frames:
            raise EOFError('attempt to seek outside sequence')
        if frame == self._frame:
            return
        if frame < self._frame:
            self._data, self.info = self._frame0[0], dict(self._frame0[1])
            png = self._png
            fc0 = png.frames[0][0] if png is not None else None
            self._init_frames(fc0)
        while self._frame < frame:
            self._advance()

    def _advance(self) -> None:
        png = self._png
        if png is None:
            raise EOFError('no more images')
        target = self._frame + 1
        if target >= len(png.frames):
            if png.frame_error is not None and png.frame_error[0] <= target:
                raise ImageDecodeError(png.frame_error[1])
            raise EOFError('No more images in APNG file')
        bpp = _BPP[self.mode]
        W, H = self.size
        canvas = bytearray(self._data)
        # dispose of the previous frame
        dispose = None
        if self._extent is not None:
            x0, y0, x1, y1 = self._extent
            if self._dispose_op == 1:
                dispose = 'background'
            elif self._dispose_op == 2 and self._prev is not None:
                dispose = 'previous'
            # Pillow captures the "previous" region before lazily loading the current frame, so it
            # sees the OVER-composited canvas only if the caller already read this frame's pixels.
            source = self._prev_loaded if self._loaded else self._prev
            if dispose:
                for y in range(y0, y1):
                    a = (y * W + x0) * bpp
                    b = (y * W + x1) * bpp
                    canvas[a:b] = bytes(b - a) if dispose == 'background' else source[a:b]
        prev = bytes(canvas)
        fc, samples = png.decode_frame(target)
        canvas = bytearray(Image._place(png, fc, samples, canvas))
        self.info.update(fc)
        if fc['blend'] == 1:  # APNG_BLEND_OP_OVER: paste(updated, bbox, mask=alpha of updated)
            x0, y0, x1, y1 = fc['bbox']
            fw = x1 - x0
            frame_img = Image(self.mode, (fw, y1 - y0), samples, info=self.info, palette=self._palette)
            alpha = frame_img._mask_alpha()
            out = bytearray(prev)
            for row in range(y1 - y0):
                base = ((y0 + row) * W + x0) * bpp
                for col in range(fw):
                    m = alpha[row * fw + col]
                    off = base + col * bpp
                    src = (row * fw + col) * bpp
                    if m == 255:
                        out[off:off + bpp] = samples[src:src + bpp]
                    elif m:
                        inv = 255 - m
                        for k in range(bpp):
                            out[off + k] = _div255(out[off + k] * inv + samples[src + k] * m)
            canvas = out
        self._prev = prev
        # load_end pastes an OVER frame into _prev_im in place and makes it the current image
        self._prev_loaded = bytes(canvas) if fc['blend'] == 1 else prev
        self._loaded = False
        self._data = bytes(canvas)
        self._frame = target
        self._dispose_op = fc['disposal']
        self._blend_op = fc['blend']
        self._extent = fc['bbox']

    def _mask_alpha(self) -> bytes:
        """Alpha used as the paste mask when blending an APNG frame (Pillow's load_end)."""
        t = self.info.get('transparency')
        if self.mode == 'RGBA':
            return self._data[3::4]
        if self.mode == 'LA':
            return self._data[1::2]
        if self.mode == 'RGB' and t is not None:
            return self.convert('RGBA').tobytes()[3::4]
        if self.mode == 'P' and t is not None:
            return self.convert('RGBA').tobytes()[3::4]
        return b'\xff' * (self.width * self.height)

    # --- pixel access -------------------------------------------------------------
    def tobytes(self) -> bytes:
        self._loaded = True
        if self.mode == '1':
            w, h = self.size
            out = bytearray()
            for y in range(h):
                row = self._data[y * w:(y + 1) * w]
                for i in range(0, w, 8):
                    byte = 0
                    for k, v in enumerate(row[i:i + 8]):
                        if v:
                            byte |= 0x80 >> k
                    out.append(byte)
            return bytes(out)
        return self._data

    def getpixel(self, xy: Tuple[int, int]):
        self._loaded = True
        x, y = xy
        w, h = self.size
        if not (0 <= x < w and 0 <= y < h):
            raise IndexError('image index out of range')
        bpp = _BPP[self.mode]
        off = (y * w + x) * bpp
        if self.mode == 'I;16':
            return struct.unpack('<H', self._data[off:off + 2])[0]
        if bpp == 1:
            return self._data[off]
        return tuple(self._data[off:off + bpp])

    def putdata(self, data, scale: float = 1.0, offset: float = 0.0) -> None:
        self._loaded = True
        values = list(data)
        w, h = self.size
        if len(values) > w * h:
            raise ValueError('too many data entries')
        out = bytearray(self._data)
        bpp = _BPP[self.mode]
        for i, v in enumerate(values):
            if scale != 1.0 or offset != 0.0:
                v = v * scale + offset if not isinstance(v, tuple) else tuple(c * scale + offset for c in v)
            out[i * bpp:(i + 1) * bpp] = _pack_color(self.mode, v)
        self._data = bytes(out)

    def putpixel(self, xy: Tuple[int, int], value) -> None:
        self._loaded = True
        x, y = xy
        w, h = self.size
        if x < 0:
            x += w
        if y < 0:
            y += h
        if not (0 <= x < w and 0 <= y < h):
            raise IndexError('image index out of range')
        bpp = _BPP[self.mode]
        off = (y * w + x) * bpp
        data = bytearray(self._data)
        data[off:off + bpp] = _pack_color(self.mode, value)
        self._data = bytes(data)

    def putpalette(self, data, rawmode: str = 'RGB') -> None:
        if self.mode not in ('L', 'P'):
            raise ValueError('illegal image mode')
        if rawmode != 'RGB':
            raise ValueError('only RGB palettes are supported')
        self.mode = 'P'
        self._palette = bytes(data)[:768]

    def getpalette(self, rawmode: str = 'RGB'):
        if self.mode != 'P':
            return None
        return list(self._palette or b'')

    # --- conversion ---------------------------------------------------------------
    def _rgba(self) -> bytes:
        mode, data = self.mode, self._data
        n = self.width * self.height
        t = self.info.get('transparency')
        if mode == 'RGBA':
            return data
        out = bytearray(n * 4)
        if mode in ('L', '1', 'I;16'):
            if mode == 'I;16':  # Pillow clips to 255, then compares the key with the clipped value
                lo, hi = data[0::2], data[1::2]
                gray = bytes(255 if h_ else l_ for l_, h_ in zip(lo, hi))
            elif mode == '1':
                gray = data.translate(_SCALE[1])
            else:
                gray = data
            out[0::4] = gray
            out[1::4] = gray
            out[2::4] = gray
            if isinstance(t, int):  # Pillow compares with the key's low 8 bits
                key = t & 255
                table = bytes(0 if v == key else 255 for v in range(256))
                out[3::4] = gray.translate(table)
            else:
                out[3::4] = b'\xff' * n
            return bytes(out)
        if mode == 'LA':
            gray = data[0::2]
            out[0::4] = gray
            out[1::4] = gray
            out[2::4] = gray
            out[3::4] = data[1::2]
            return bytes(out)
        if mode == 'RGB':
            out[0::4] = data[0::3]
            out[1::4] = data[1::3]
            out[2::4] = data[2::3]
            out[3::4] = b'\xff' * n
            if isinstance(t, tuple):  # Pillow compares with each component's low 8 bits
                key = bytes(c & 255 for c in t)
                for i in range(n):
                    if data[i * 3:i * 3 + 3] == key:
                        out[i * 4 + 3] = 0
            return bytes(out)
        if mode == 'P':
            pal = (self._palette or b'')[:768]
            pal = pal + bytes(768 - len(pal))
            alphas = bytearray(b'\xff' * 256)
            if isinstance(t, (bytes, bytearray)):
                alphas[:len(t[:256])] = t[:256]
            elif isinstance(t, int) and 0 <= t < 256:
                alphas[t] = 0
            lut = [pal[i * 3:i * 3 + 3] + bytes((alphas[i],)) for i in range(256)]
            return b''.join(map(lut.__getitem__, data))
        raise ValueError(f'cannot convert {mode!r} to RGBA')

    def convert(self, mode: Optional[str] = None, *args, **kwargs) -> 'Image':
        self._loaded = True
        if mode is None or mode == self.mode:
            return self.copy()
        if mode not in ('L', 'LA', 'RGB', 'RGBA'):
            raise ValueError(f'conversion from {self.mode} to {mode} not supported')
        info = dict(self.info)
        if mode == 'RGBA':
            data = self._rgba()
            info.pop('transparency', None)
            return Image('RGBA', self.size, data, info=info)
        if mode == 'RGB':
            if self.mode == 'P':
                rgba = Image('P', self.size, self._data, palette=self._palette)._rgba()
            else:
                rgba = Image(self.mode, self.size, self._data)._rgba()
            out = bytearray(len(rgba) // 4 * 3)
            out[0::3] = rgba[0::4]
            out[1::3] = rgba[1::4]
            out[2::3] = rgba[2::4]
            info.pop('transparency', None)
            return Image('RGB', self.size, bytes(out), info=info)
        # L / LA
        if self.mode in ('L', '1', 'I;16'):
            gray = Image(self.mode, self.size, self._data)._rgba()[0::4]
        elif self.mode == 'LA':
            gray = self._data[0::2]
        else:
            src = Image(self.mode, self.size, self._data, palette=self._palette)._rgba()
            gray = bytes(_l_from_rgb(src[i], src[i + 1], src[i + 2]) for i in range(0, len(src), 4))
        info.pop('transparency', None)
        if mode == 'L':
            return Image('L', self.size, gray, info=info)
        # Pillow applies the tRNS key when converting to a mode with alpha, as for RGBA
        alpha = self._data[1::2] if self.mode == 'LA' else self._rgba()[3::4]
        out = bytearray(len(gray) * 2)
        out[0::2] = gray
        out[1::2] = alpha
        info.pop('transparency', None)
        return Image('LA', self.size, bytes(out), info=info)

    # --- geometry / bands ---------------------------------------------------------
    def crop(self, box: Optional[Tuple[int, int, int, int]] = None) -> 'Image':
        self._loaded = True
        if box is None:
            return self.copy()
        left, upper, right, lower = (int(round(v)) for v in box)
        if right < left:
            raise ValueError("Coordinate 'right' is less than 'left'")
        if lower < upper:
            raise ValueError("Coordinate 'lower' is less than 'upper'")
        w, h = self.size
        bpp = _BPP[self.mode]
        cw, ch = right - left, lower - upper
        out = bytearray(cw * ch * bpp)  # outside the image stays zero, as in Pillow
        x0, x1 = max(left, 0), min(right, w)
        for y in range(max(upper, 0), min(lower, h)):
            if x1 <= x0:
                break
            src = (y * w + x0) * bpp
            dst = ((y - upper) * cw + (x0 - left)) * bpp
            out[dst:dst + (x1 - x0) * bpp] = self._data[src:src + (x1 - x0) * bpp]
        return self._new(self.mode, (cw, ch), bytes(out), palette=self._palette)

    def split(self) -> Tuple['Image', ...]:
        self._loaded = True
        bands = _BANDS[self.mode]
        if bands == 1:
            return (self.copy(),)
        return tuple(Image('L', self.size, self._data[c::bands]) for c in range(bands))

    def getbbox(self, *, alpha_only: bool = True) -> Optional[Tuple[int, int, int, int]]:
        self._loaded = True
        w, h = self.size
        bpp = _BPP[self.mode]
        if self.mode == 'RGBA' and alpha_only:
            plane, bpp = self._data[3::4], 1
        elif self.mode == 'LA' and alpha_only:
            plane, bpp = self._data[1::2], 1
        else:
            plane = self._data
        stride = w * bpp
        top = bottom = None
        left, right = w, 0
        zero = bytes(stride)
        for y in range(h):
            row = plane[y * stride:(y + 1) * stride]
            if row == zero:
                continue
            if top is None:
                top = y
            bottom = y
            stripped = row.lstrip(b'\0')
            first = (stride - len(stripped)) // bpp
            last = (len(row.rstrip(b'\0')) - 1) // bpp
            left = min(left, first)
            right = max(right, last + 1)
        if top is None:
            return None
        return left, top, right, bottom + 1

    def getextrema(self):
        self._loaded = True
        if self.mode == 'I;16':
            values = struct.unpack(f'<{self.width * self.height}H', self._data)
            return min(values), max(values)
        bands = _BANDS[self.mode]
        if bands == 1:
            return min(self._data), max(self._data)
        return tuple((min(self._data[c::bands]), max(self._data[c::bands])) for c in range(bands))

    def histogram(self, mask=None, extrema=None) -> List[int]:
        self._loaded = True
        if mask is not None or extrema is not None:
            raise ValueError('histogram mask/extrema are not supported')
        if self.mode == 'I;16':
            raise ValueError('histogram is not supported for mode I;16')
        bands = _BANDS[self.mode]
        out: List[int] = []
        for c in range(bands):
            plane = self._data[c::bands] if bands > 1 else self._data
            out.extend(plane.count(v) for v in range(256))
        return out

    def point(self, lut, mode: Optional[str] = None) -> 'Image':
        self._loaded = True
        if self.mode == 'I;16':
            raise ValueError('point is not supported for mode I;16')
        if mode is not None and mode != self.mode and not (
                self.mode in ('L', 'P') and mode in ('1', 'L')):
            raise ValueError('point operation not supported for this mode')
        bands = _BANDS[self.mode]
        if callable(lut):
            table = [lut(i) for i in range(256)] * bands
        else:
            table = list(lut)
            if len(table) != 256 * bands:
                raise ValueError('wrong number of lut entries')
        table = [max(0, min(255, int(round(v)))) for v in table]
        if bands == 1:
            data = self._data.translate(bytes(table))
        else:
            out = bytearray(len(self._data))
            for c in range(bands):
                out[c::bands] = self._data[c::bands].translate(bytes(table[c * 256:(c + 1) * 256]))
            data = bytes(out)
        out_mode = mode or self.mode
        if out_mode == '1':
            data = data.translate(_SCALE[1])
        return self._new(out_mode, self.size, data, palette=self._palette if out_mode == 'P' else None)

    # --- output -------------------------------------------------------------------
    def save(self, fp, format: Optional[str] = None, **params) -> None:
        self._loaded = True
        if params.get('save_all') or params.get('append_images'):
            raise ValueError('writing animated PNG (save_all) is not supported')
        if format is None:
            name = fp if isinstance(fp, (str, os.PathLike)) else getattr(fp, 'name', None)
            ext = os.path.splitext(os.fspath(name))[1].lower() if isinstance(name, (str, os.PathLike)) else ''
            if ext not in ('.png', '.apng') and not (ext == '' and hasattr(fp, 'write')):
                raise ValueError(f'unknown file extension: {ext!r} (only PNG output is supported)')
        elif format.upper() not in ('PNG', 'APNG'):
            raise ValueError('only PNG output is supported')
        data = _encode(self, params)
        if hasattr(fp, 'write'):
            fp.write(data)
        else:
            with open(os.fspath(fp), 'wb') as handle:
                handle.write(data)

    def to_base64(self, data_uri: bool = False) -> str:
        img = self.convert('RGBA') if self.mode not in ('RGB', 'RGBA') else self
        text = base64.b64encode(encode_png(img)).decode('ascii')
        return ('data:image/png;base64,' + text) if data_uri else text


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))


def _encode(image: Image, params: dict) -> bytes:
    mode = image.mode
    w, h = image.size
    _check_limits(w, h)
    level = int(params.get('compress_level', 6))
    transparency = params.get('transparency', image.info.get('transparency'))
    chunks = []
    data = image._data
    if mode == '1':
        depth, color = 1, 0
        packed = image.tobytes()
        stride = (w + 7) // 8
        rows = [packed[y * stride:(y + 1) * stride] for y in range(h)]
    elif mode == 'P':
        bits = int(params.get('bits', 8))
        if bits not in (1, 2, 4, 8):
            bits = 8
        depth, color = bits, 3
        rows = []
        for y in range(h):
            idx = data[y * w:(y + 1) * w]
            if bits == 8:
                rows.append(idx)
                continue
            per = 8 // bits
            row = bytearray((w + per - 1) // per)
            for x, v in enumerate(idx):
                row[x // per] |= (v & ((1 << bits) - 1)) << (8 - bits * (x % per + 1))
            rows.append(bytes(row))
        entries = 1 << bits
        palette = (image._palette or b'')[:entries * 3]
        palette = palette + bytes(entries * 3 - len(palette))
        chunks.append(_png_chunk(b'PLTE', palette))
        if isinstance(transparency, int):
            if 0 <= transparency < entries:
                chunks.append(_png_chunk(b'tRNS', b'\xff' * transparency + b'\x00'))
        elif isinstance(transparency, (bytes, bytearray)):
            chunks.append(_png_chunk(b'tRNS', bytes(transparency[:entries])))
    elif mode == 'I;16':
        depth, color = 16, 0
        swapped = bytearray(len(data))
        swapped[0::2] = data[1::2]
        swapped[1::2] = data[0::2]
        rows = [bytes(swapped[y * w * 2:(y + 1) * w * 2]) for y in range(h)]
    else:
        depth = 8
        color = {'L': 0, 'LA': 4, 'RGB': 2, 'RGBA': 6}[mode]
        stride = w * _BPP[mode]
        rows = [data[y * stride:(y + 1) * stride] for y in range(h)]
    if mode in ('1', 'L', 'I;16') and isinstance(transparency, int):
        key = (1 if transparency else 0) if mode == '1' else transparency
        if key < (1 << depth):
            chunks.append(_png_chunk(b'tRNS', struct.pack('>H', key)))
    if mode == 'RGB' and isinstance(transparency, tuple) and len(transparency) == 3:
        chunks.append(_png_chunk(b'tRNS', struct.pack('>3H', *transparency)))
    raw = b''.join(b'\x00' + r for r in rows)
    header = struct.pack('>IIBBBBB', w, h, depth, color, 0, 0, 0)
    head = _png_chunk(b'IHDR', header)
    # PLTE must precede tRNS; both precede IDAT
    return (PNG_SIGNATURE + head + b''.join(chunks)
            + _png_chunk(b'IDAT', zlib.compress(raw, level)) + _png_chunk(b'IEND', b''))


def encode_png(image: Image, *, compress_level: int = 9) -> bytes:
    if image.mode not in ('RGB', 'RGBA'):
        raise ValueError('encode_png supports RGB and RGBA only')
    return _encode(Image(image.mode, image.size, image._data), {'compress_level': compress_level})


class ImageChops:
    @staticmethod
    def _check(im1: Image, im2: Image) -> None:
        if im1.mode != im2.mode or im1.size != im2.size:
            raise ValueError('images do not match')
        if im1.mode == 'I;16':
            raise ValueError('mode I;16 is not supported')

    @staticmethod
    def difference(im1: Image, im2: Image) -> Image:
        ImageChops._check(im1, im2)
        data = bytes(map(_ABS.__getitem__, map(sub, im1._data, im2._data)))
        return Image(im1.mode, im1.size, data, palette=im1._palette)

    @staticmethod
    def lighter(im1: Image, im2: Image) -> Image:
        ImageChops._check(im1, im2)
        return Image(im1.mode, im1.size, bytes(map(max, im1._data, im2._data)), palette=im1._palette)

    @staticmethod
    def darker(im1: Image, im2: Image) -> Image:
        ImageChops._check(im1, im2)
        return Image(im1.mode, im1.size, bytes(map(min, im1._data, im2._data)), palette=im1._palette)


def _limits(max_pixels, max_dimension, max_decompressed):
    return {'max_pixels': max_pixels, 'max_dimension': max_dimension, 'max_decompressed': max_decompressed}


def decode_png_rgba(
    data: bytes,
    *,
    max_pixels: Optional[int] = None,
    max_dimension: Optional[int] = None,
    max_decompressed: Optional[int] = None,
) -> Tuple[int, int, List[bytes]]:
    """PNG bytes -> (width, height, RGBA rows) of the default image (frame 0)."""
    img = open_bytes(data, max_pixels=max_pixels, max_dimension=max_dimension,
                     max_decompressed=max_decompressed)
    stride = img.width * 4
    raw = img.tobytes()
    return img.width, img.height, [raw[y * stride:(y + 1) * stride] for y in range(img.height)]


def open_bytes(
    data: bytes,
    *,
    max_pixels: Optional[int] = None,
    max_dimension: Optional[int] = None,
    max_decompressed: Optional[int] = None,
) -> Image:
    """PNG bytes -> RGBA :class:`Image` of frame 0 (``Image.open(...).convert('RGBA')``)."""
    if not bytes(data[:8]).startswith(PNG_SIGNATURE):
        raise UnsupportedImageError('only PNG input is supported')
    return Image.open(bytes(data), **_limits(max_pixels, max_dimension, max_decompressed)).convert('RGBA')


def iter_apng_frames(
    data: bytes,
    *,
    max_pixels: Optional[int] = None,
    max_dimension: Optional[int] = None,
    max_decompressed: Optional[int] = None,
) -> Iterator[Image]:
    """Yield every frame as RGBA, composited exactly as Pillow's ``seek`` does."""
    img = Image.open(bytes(data), **_limits(max_pixels, max_dimension, max_decompressed))
    for index in range(img.n_frames):
        try:
            img.seek(index)
        except EOFError:
            return
        yield img.convert('RGBA')


def max_base64_chars(max_bytes: int = 32 * 1024 * 1024) -> int:
    return 4 * ((max_bytes + 2) // 3)


def decode_base64(text: Union[str, bytes], max_bytes: int = 32 * 1024 * 1024) -> bytes:
    if isinstance(text, bytes):
        try:
            text = text.decode('ascii')
        except UnicodeDecodeError:
            raise ValueError('Base64 input must be ASCII') from None
    text = text.lstrip('\ufeff').strip()
    match = _DATA_URI.match(text)
    mime = (match.group(1) or '').lower() if match else ''
    if mime and mime not in ('image/png',):
        raise ValueError(f'unsupported data URI type {mime!r}; only image/png is supported')
    if match:
        text = text[match.end():]
    elif text[:5].lower() == 'data:':
        raise ValueError('data URI must be base64-encoded (data:<type>;base64,...)')
    text = ''.join(text.split())
    if not text:
        raise ValueError('Base64 input is empty')
    if len(text) > max_base64_chars(max_bytes):
        raise ValueError(f'decoded input exceeds {max_bytes} bytes')
    if len(text) % 4 or not _BASE64.fullmatch(text):
        raise ValueError('invalid Base64 input')
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('invalid Base64 input') from None
    if len(data) > max_bytes:
        raise ValueError(f'decoded input exceeds {max_bytes} bytes')
    if mime == 'image/png' and not data.startswith(PNG_SIGNATURE):
        raise ValueError('data URI says image/png but the decoded bytes are not that format')
    return data


def diff_metrics(
    im1: Image,
    im2: Image,
    threshold: int = 1,
) -> dict:
    """One-pass RGB diff summary (alpha ignored; ``changed`` means max RGB delta >= threshold)."""
    if im1.size != im2.size:
        raise ValueError('images must be the same size')
    a = im1.convert('RGBA')._data
    b = im2.convert('RGBA')._data
    w, h = im1.size
    changed = 0
    max_diff = 0
    min_x = min_y = w
    max_x = max_y = -1
    for y in range(h):
        for x in range(w):
            off = (y * w + x) * 4
            m = max(abs(a[off] - b[off]), abs(a[off + 1] - b[off + 1]), abs(a[off + 2] - b[off + 2]))
            max_diff = max(max_diff, m)
            if m >= threshold:
                changed += 1
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    bbox = None if changed == 0 else (min_x, min_y, max_x + 1, max_y + 1)
    return {'changed_pixels': changed, 'bbox': bbox, 'max_diff': max_diff}


def _self_test() -> None:
    img = Image.new('RGB', (2, 2), (10, 20, 30))
    roundtrip = open_bytes(encode_png(img))
    assert roundtrip.size == (2, 2)
    assert roundtrip.getpixel((0, 0)) == (10, 20, 30, 255)
    native = Image.open(encode_png(img))
    assert native.mode == 'RGB' and native.width == 2 and native.tobytes() == img.tobytes()


if __name__ == '__main__':
    _self_test()
