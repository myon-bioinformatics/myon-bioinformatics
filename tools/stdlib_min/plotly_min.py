"""Stdlib stand-in for the plotly ``graph_objects`` calls our consumers make (Python 3.9+).

The figure is plotly's own JSON (``{"data": [...], "layout": {...}}``), so ``to_dict`` / ``to_json`` /
``write_html`` feed plotly.js unchanged. Static export is the difference: plotly needs Kaleido and a
Chrome install for ``write_image``; here ``to_image`` / ``write_image`` draw SVG directly with the
standard library, in plotly's default look (colorway, #E5ECF6 plot area, white grid).

Covered: ``Figure`` (``add_trace`` / ``update_layout`` / ``add_shape`` / ``add_annotation`` /
``add_vline`` / ``add_hline`` / ``to_dict`` / ``to_json`` / ``to_html`` / ``write_html`` / ``to_image`` /
``write_image``) and the traces ``Scatter`` / ``Bar`` / ``Waterfall`` / ``Sankey`` / ``Heatmap``, with
plotly's "magic underscore" keywords (``line_color=...``). One cartesian plot area (``xaxis`` /
``yaxis``) or one Sankey per figure; no subplots, 3D, geo, polar, templates or animation. Text uses
``FONT`` (Meiryo first) unless ``layout.font.family`` says otherwise. Scope and differences:
``docs/plotly-min.md``.
"""
from __future__ import annotations

import json, math
from html import escape
from pathlib import Path
from types import SimpleNamespace

__all__ = ['Figure', 'Scatter', 'Bar', 'Waterfall', 'Sankey', 'Heatmap', 'graph_objects', 'FONT', 'COLORWAY',
           'PLOTLY_JS']

PLOTLY_JS = '4.1.2'  # plotly.js on the CDN for write_html (v2+ needs an exact version)
FONT = 'Meiryo, メイリオ, sans-serif'  # Meiryo cannot be bundled; other OSes fall back to sans-serif
COLORWAY = ('#636efa', '#EF553B', '#00cc96', '#ab63fa', '#FFA15A', '#19d3f3', '#FF6692', '#B6E880', '#FF97FF',
            '#FECB52')
_INK, _PLOT_BG, _GRID = '#2a3f5f', '#E5ECF6', 'white'
_WATERFALL = {'increasing': '#3D9970', 'decreasing': '#FF4136', 'totals': '#4499FF'}
_SCALES = {
    'plasma': ('#0d0887', '#46039f', '#7201a8', '#9c179e', '#bd3786', '#d8576b', '#ed7953', '#fb9f3a', '#fdca26',
               '#f0f921'),
    'viridis': ('#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6ece58', '#b5de2b',
                '#fde725'),
    'rdbu': ('#053061', '#2166ac', '#4393c3', '#92c5de', '#d1e5f0', '#f7f7f7', '#fddbc7', '#f4a582', '#d6604d',
             '#b2182b', '#67001f'),
}
_KEEP = {'paper_bgcolor', 'plot_bgcolor', 'error_x', 'error_y'}  # attribute names that contain "_"


def _html_json(value) -> str:
    """JSON for inline HTML scripts; retain values through JSON Unicode escapes."""
    return (json.dumps(value, ensure_ascii=False)
            .replace('&', r'\u0026').replace('<', r'\u003c').replace('>', r'\u003e'))


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, 'to_plotly_json'):
        return _plain(value.to_plotly_json())
    return value


def _merge(target: dict, updates: dict) -> dict:
    """Deep-merge ``updates`` into ``target``; ``a_b=1`` means ``{"a": {"b": 1}}`` (plotly's magic underscore),
    and a bare string title means ``{"text": ...}`` as in plotly.py."""
    for key, value in updates.items():
        path = [key] if key in _KEEP or '_' not in key else key.split('_')
        node = target
        for part in path[:-1]:
            node = node.setdefault(part, {})
        value = _plain(value)
        if path[-1] == 'title' and isinstance(value, str):
            value = {'text': value}
        if isinstance(value, dict) and isinstance(node.get(path[-1]), dict):
            _merge(node[path[-1]], value)
        else:
            node[path[-1]] = value
    return target


class _Trace(dict):
    kind = ''

    def __init__(self, arg=None, **kwargs):
        super().__init__(type=self.kind)
        _merge(self, {**(arg or {}), **kwargs})

    def to_plotly_json(self) -> dict:
        return dict(self)


class Scatter(_Trace):
    kind = 'scatter'


class Bar(_Trace):
    kind = 'bar'


class Waterfall(_Trace):
    kind = 'waterfall'


class Sankey(_Trace):
    kind = 'sankey'


class Heatmap(_Trace):
    kind = 'heatmap'


_TRACES = {c.kind: c for c in (Scatter, Bar, Waterfall, Sankey, Heatmap)}


class Figure:
    """``go.Figure``: ``data`` (traces) and ``layout`` as plotly's JSON."""

    def __init__(self, data=None, layout=None):
        if isinstance(data, dict) and 'data' in data:  # Figure(dict) like plotly
            data, layout = data.get('data'), data.get('layout', layout)
        self.data: list = []
        self.layout: dict = {}
        for trace in ([data] if isinstance(data, dict) else data or []):
            self.add_trace(trace)
        if layout:
            self.update_layout(layout)

    def add_trace(self, trace) -> 'Figure':
        if not isinstance(trace, _Trace):
            trace = dict(trace)
            kind = trace.pop('type', 'scatter')
            if kind not in _TRACES:
                raise ValueError(f'trace type {kind!r} is not covered by plotly_min')
            trace = _TRACES[kind](trace)
        self.data.append(trace)
        return self

    def update_layout(self, dict1=None, **kwargs) -> 'Figure':
        _merge(self.layout, {**(dict1 or {}), **kwargs})
        return self

    def add_shape(self, dict1=None, **kwargs) -> 'Figure':
        self.layout.setdefault('shapes', []).append(_merge({}, {**(dict1 or {}), **kwargs}))
        return self

    def add_annotation(self, dict1=None, **kwargs) -> 'Figure':
        self.layout.setdefault('annotations', []).append(_merge({}, {**(dict1 or {}), **kwargs}))
        return self

    def add_vline(self, x, **kwargs) -> 'Figure':
        return self.add_shape(type='line', x0=x, x1=x, xref='x', y0=0, y1=1, yref='y domain', **kwargs)

    def add_hline(self, y, **kwargs) -> 'Figure':
        return self.add_shape(type='line', y0=y, y1=y, yref='y', x0=0, x1=1, xref='x domain', **kwargs)

    def to_dict(self) -> dict:
        return {'data': [_plain(t) for t in self.data], 'layout': _plain(self.layout)}

    to_plotly_json = to_dict

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    def to_html(self, *, include_plotlyjs='cdn', full_html: bool = True, div_id: str = 'plotly-figure') -> str:
        """HTML that draws the figure with plotly.js (``'cdn'`` = script tag to cdn.plot.ly, ``False`` = none)."""
        fig = self.to_dict()
        fig['layout'] = _merge({'font': {'family': FONT}}, fig['layout'])
        payload = _html_json(fig)
        script = f'<script src="https://cdn.plot.ly/plotly-{PLOTLY_JS}.min.js" charset="utf-8"></script>' \
            if include_plotlyjs == 'cdn' else ''
        div = (f'<div id="{escape(div_id)}"></div>{script}<script>const f = {payload};'
               f'Plotly.newPlot({_html_json(div_id)}, f.data, f.layout);</script>')
        if not full_html:
            return div
        return f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{div}</body></html>\n'

    def write_html(self, file, **kwargs) -> None:
        Path(file).write_text(self.to_html(**kwargs), encoding='utf-8')

    def to_image(self, format: str = 'svg', width: int | None = None, height: int | None = None) -> bytes:
        if format != 'svg':
            raise ValueError('plotly_min draws SVG only (plotly needs Kaleido and Chrome for other formats)')
        return _svg(self, width, height).encode('utf-8')

    def write_image(self, file, format: str | None = None, width: int | None = None,
                    height: int | None = None) -> None:
        fmt = format or Path(file).suffix.lstrip('.').lower() or 'svg'
        Path(file).write_bytes(self.to_image(fmt, width, height))


graph_objects = SimpleNamespace(Figure=Figure, Scatter=Scatter, Bar=Bar, Waterfall=Waterfall, Sankey=Sankey,
                                Heatmap=Heatmap)


# --- SVG ---------------------------------------------------------------------------------------------

def _n(x: float) -> str:
    """Fixed, short number text (the same figure always gives the same bytes)."""
    text = f'{x:.2f}'.rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def _text_width(text: str, size: float) -> float:
    return sum(size if ord(ch) > 0x2E7F else 0.58 * size for ch in str(text))


def _label(value: float, step: float) -> str:
    digits = f'{step:.12f}'.rstrip('0')
    places = len(digits.split('.')[1]) if '.' in digits else 0
    text = f'{abs(value):.{places}f}'
    text = text.rstrip('0').rstrip('.') if '.' in text else text  # 1.5, 2, 2.5 like plotly
    return ('−' if value < 0 and float(text) != 0 else '') + text  # plotly prints U+2212


def _ticks(lo: float, hi: float, count: int = 6) -> tuple[list[float], float]:
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw * (1 - 1e-9))
    first = math.ceil(lo / step - 1e-9)
    return [round((first + i) * step, 12) for i in range(int(math.floor(hi / step + 1e-9)) - first + 1)], step


def _color_at(scale, t: float) -> str:
    """Colour at t in [0, 1] of a plotly colorscale (name or [[pos, colour], ...])."""
    if isinstance(scale, str):
        stops = _SCALES[scale.lower()]
        scale = [[i / (len(stops) - 1), c] for i, c in enumerate(stops)]
    t = min(1.0, max(0.0, t))
    for (p0, c0), (p1, c1) in zip(scale, scale[1:]):
        if t <= p1:
            f = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            a, b = _rgb(c0), _rgb(c1)
            return '#%02x%02x%02x' % tuple(round(x + (y - x) * f) for x, y in zip(a, b))
    return scale[-1][1]


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.strip()
    if color.startswith('#'):
        h = color[1:]
        h = ''.join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    if color.startswith('rgb'):
        return tuple(int(float(v)) for v in color[color.index('(') + 1:color.index(')')].split(',')[:3])
    return {'white': (255, 255, 255), 'black': (0, 0, 0)}.get(color, (0, 0, 0))


class _Svg:
    def __init__(self, width: int, height: int, font: dict):
        self.w, self.h, self.out = width, height, []
        self.family = font.get('family', FONT)
        self.size = font.get('size', 12)
        self.ink = font.get('color', _INK)

    def rect(self, x, y, w, h, fill, extra=''):
        self.out.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(max(w, 0))}" height="{_n(max(h, 0))}" '
                        f'fill="{escape(fill)}"{extra}/>')

    def line(self, x0, y0, x1, y1, stroke, width=1.0, dash=None):
        self.out.append(f'<line x1="{_n(x0)}" y1="{_n(y0)}" x2="{_n(x1)}" y2="{_n(y1)}" stroke="{escape(stroke)}" '
                        f'stroke-width="{_n(width)}"{_dash(dash, width)}/>')

    def text(self, x, y, text, *, anchor='middle', size=None, color=None, rotate=None, weight=None, baseline='middle'):
        extra = f' transform="rotate({_n(rotate)} {_n(x)} {_n(y)})"' if rotate else ''
        extra += f' font-weight="{weight}"' if weight else ''
        self.out.append(f'<text x="{_n(x)}" y="{_n(y)}" text-anchor="{anchor}" dominant-baseline="{baseline}" '
                        f'font-family="{escape(self.family)}" font-size="{_n(size or self.size)}" '
                        f'fill="{escape(color or self.ink)}"{extra}>{escape(str(text), quote=False)}</text>')

    def raw(self, element: str):
        self.out.append(element)

    def done(self, bg: str) -> str:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}"><rect width="{self.w}" height="{self.h}" fill="{escape(bg)}"/>')
        return head + ''.join(self.out) + '</svg>\n'


def _dash(dash, width) -> str:
    pattern = {'dash': (3, 3), 'dot': (1, 2), 'dashdot': (3, 2, 1, 2), 'longdash': (5, 3)}.get(dash)
    return f' stroke-dasharray="{",".join(_n(p * max(width, 1)) for p in pattern)}"' if pattern else ''


def _title(layout: dict) -> str:
    title = layout.get('title', {})
    return title.get('text', '') if isinstance(title, dict) else str(title)


def _svg(fig: Figure, width=None, height=None) -> str:
    lay = fig.layout
    w, h = int(width or lay.get('width', 700)), int(height or lay.get('height', 500))
    svg = _Svg(w, h, {**{'family': FONT}, **lay.get('font', {})})
    m = {'l': 80, 'r': 80, 't': 100, 'b': 80, **lay.get('margin', {})}
    legend = [t for t in fig.data if t.get('name') and t.get('showlegend', True) and t['type'] != 'sankey']
    show_legend = lay.get('showlegend', len(legend) > 1)
    if show_legend and legend:
        m['r'] += max(_text_width(t['name'], svg.size) for t in legend) + 40
    box = (m['l'], m['t'], w - m['l'] - m['r'], h - m['t'] - m['b'])
    if _title(lay):
        svg.text(w * lay.get('title', {}).get('x', 0.05), m['t'] / 2, _title(lay), anchor='start', size=17)
    if any(t['type'] == 'sankey' for t in fig.data):
        for trace in fig.data:
            if trace['type'] == 'sankey':
                _sankey(svg, trace, box)
        for note in lay.get('annotations', []):
            _annotation(svg, note, box, None, None)
    else:
        _cartesian(svg, fig, box, lay.get('plot_bgcolor', _PLOT_BG))
    if show_legend:
        for i, trace in enumerate(legend):
            x, y = w - m['r'] + 20, m['t'] + 10 + 20 * i
            color = _trace_color(trace, fig.data.index(trace))
            if trace['type'] == 'scatter' and 'lines' in _mode(trace):
                svg.line(x, y, x + 20, y, color, trace.get('line', {}).get('width', 2), trace.get('line', {}).get('dash'))
            elif trace['type'] == 'scatter':
                _marker(svg, x + 10, y, trace.get('marker', {}), color)
            else:
                svg.rect(x + 4, y - 6, 12, 12, color)
            svg.text(x + 26, y, trace['name'], anchor='start')
    return svg.done(lay.get('paper_bgcolor', 'white'))


def _mode(trace: dict) -> str:
    return trace.get('mode') or ('lines+markers' if len(trace.get('y', trace.get('x', []))) < 20 else 'lines')


def _trace_color(trace: dict, index: int) -> str:
    for key in ('line', 'marker'):
        color = trace.get(key, {}).get('color')
        if isinstance(color, str):
            return color
    return COLORWAY[index % len(COLORWAY)]


class _Axis:
    def __init__(self, spec: dict, values: list, lo_px: float, hi_px: float, *, zero: bool, pad: float):
        self.spec, self.lo_px, self.hi_px = spec, lo_px, hi_px
        self.categories = None
        if spec.get('type') == 'category' or any(isinstance(v, str) for v in values):
            self.categories = list(dict.fromkeys(str(v) for v in values if v is not None))
            self.range = (-0.5, len(self.categories) - 0.5)
        elif 'range' in spec:
            self.range = tuple(spec['range'])
        else:
            nums = [float(v) for v in values if v is not None] + ([0.0] if zero else [])
            lo, hi = (min(nums), max(nums)) if nums else (0.0, 1.0)
            if hi == lo:
                lo, hi = lo - 1, hi + 1
            span = hi - lo
            lo -= pad * span if not (zero and lo == 0) else 0
            hi += pad * span if not (zero and hi == 0) else 0
            self.range = (lo, hi)
        if spec.get('autorange') == 'reversed':
            self.range = self.range[::-1]

    def unit(self) -> float:
        """Pixels per one unit (one category step on a category axis)."""
        lo, hi = self.range
        return abs(self.hi_px - self.lo_px) / abs(hi - lo)

    def pos(self, value) -> float:
        v = self.categories.index(str(value)) if self.categories is not None else float(value)
        lo, hi = self.range
        return self.lo_px + (v - lo) / (hi - lo) * (self.hi_px - self.lo_px)

    def ticks(self) -> list[tuple[float, str]]:
        if self.categories is not None:
            return [(self.pos(c), c) for c in self.categories]
        lo, hi = sorted(self.range)
        ticks, step = _ticks(lo, hi, self.spec.get('nticks', 6))
        return [(self.pos(t), _label(t, step)) for t in ticks if lo - 1e-9 <= t <= hi + 1e-9]


def _cartesian(svg: _Svg, fig: Figure, box, plot_bg: str) -> None:
    lay = fig.layout
    x0, y0, pw, ph = box
    xs, ys, bars_x, bars_y = [], [], False, False
    for t in fig.data:
        if t['type'] == 'scatter':
            xs += list(t.get('x', range(len(t.get('y', [])))))
            ys += list(t.get('y', []))
        elif t['type'] in ('bar', 'waterfall'):
            cats, vals = _bar_parts(t)
            ends = [b + v for b, v in zip(vals[0], vals[1])] + vals[0]
            if t.get('orientation', 'v') == 'h':
                ys += cats
                xs += ends
                bars_x = True
            else:
                xs += cats
                ys += ends
                bars_y = True
        elif t['type'] == 'heatmap':
            z = t.get('z', [[]])
            xs += list(t.get('x', range(len(z[0]) if z else 0)))
            ys += list(t.get('y', range(len(z))))
    for s in lay.get('shapes', []):
        if s.get('xref', 'x') == 'x':
            xs += [s.get('x0'), s.get('x1')]
        if s.get('yref', 'y') == 'y':
            ys += [s.get('y0'), s.get('y1')]
    heat = any(t['type'] == 'heatmap' for t in fig.data)
    marks = any('markers' in _mode(t) or 'text' in _mode(t) for t in fig.data if t['type'] == 'scatter')
    labels = any(t.get('text') is not None for t in fig.data if t['type'] in ('bar', 'waterfall'))
    pad_x = 0.0 if heat else 0.12 if bars_x and labels else 0.05 if bars_x or marks else 0.0
    pad_y = 0.0 if heat else 0.12 if bars_y and labels else 0.05 if bars_y or marks else 0.0
    if heat:  # cells are centred on their x / y
        xs, ys = _cell_edges(xs), _cell_edges(ys)
    ax = _Axis(lay.get('xaxis', {}), xs, x0, x0 + pw, zero=bars_x, pad=pad_x)
    ay = _Axis(lay.get('yaxis', {}), ys, y0 + ph, y0, zero=bars_y, pad=pad_y)
    svg.rect(x0, y0, pw, ph, plot_bg)
    for px, _label_text in ax.ticks():
        svg.line(px, y0, px, y0 + ph, _GRID)
    for py, _label_text in ay.ticks():
        svg.line(x0, py, x0 + pw, py, _GRID)
    if ax.categories is None and min(ax.range) < 0 < max(ax.range):  # zero lines, 2 px like plotly
        svg.line(ax.pos(0), y0, ax.pos(0), y0 + ph, _GRID, 2)
    if ay.categories is None and min(ay.range) < 0 < max(ay.range):
        svg.line(x0, ay.pos(0), x0 + pw, ay.pos(0), _GRID, 2)
    svg.raw(f'<clipPath id="plot-area"><rect x="{_n(x0)}" y="{_n(y0)}" width="{_n(pw)}" height="{_n(ph)}"/>'
            '</clipPath><g clip-path="url(#plot-area)">')
    bar_traces = [t for t in fig.data if t['type'] in ('bar', 'waterfall')]
    for i, t in enumerate(fig.data):
        if t['type'] == 'heatmap':
            _heatmap(svg, t, ax, ay, box)
        elif t['type'] in ('bar', 'waterfall'):
            _bars(svg, t, i, ax, ay, bar_traces.index(t), len(bar_traces), lay)
        elif t['type'] == 'scatter':
            _scatter(svg, t, i, ax, ay)
    for s in lay.get('shapes', []):
        _shape(svg, s, ax, ay, box)
    svg.raw('</g>')
    for t in fig.data:
        if t['type'] == 'heatmap' and t.get('showscale', True):
            _colorbar(svg, t, box)
    for note in lay.get('annotations', []):
        _annotation(svg, note, box, ax, ay)
    angle = lay.get('xaxis', {}).get('tickangle')
    for px, text in ax.ticks():  # tickangle rotates the labels about their top end, as in plotly
        if angle:
            svg.text(px, y0 + ph + 10, text, anchor='end' if angle < 0 else 'start', rotate=angle)
        else:
            svg.text(px, y0 + ph + 14, text)
    for py, text in ay.ticks():
        svg.text(x0 - 6, py, text, anchor='end')
    xt, yt = _title(lay.get('xaxis', {})), _title(lay.get('yaxis', {}))
    if xt:
        svg.text(x0 + pw / 2, y0 + ph + 40, xt, size=14)
    if yt:
        width = max((_text_width(text, svg.size) for _p, text in ay.ticks()), default=0)
        svg.text(x0 - width - 24, y0 + ph / 2, yt, size=14, rotate=-90)


def _cell_edges(values: list) -> list:
    if any(isinstance(v, str) for v in values) or len(values) < 2:
        return values
    nums = sorted(set(float(v) for v in values))
    half = (nums[1] - nums[0]) / 2 if len(nums) > 1 else 0.5
    return [nums[0] - half, nums[-1] + half]


def _bar_parts(t: dict) -> tuple[list, tuple[list, list]]:
    """(categories, (starts, lengths)) of each bar; waterfall bars float on the running total."""
    horizontal = t.get('orientation', 'v') == 'h'
    vals = list(t.get('x' if horizontal else 'y', []))
    cats = list(t.get('y' if horizontal else 'x', range(len(vals))))
    if t['type'] == 'waterfall':
        starts, lengths, total = [], [], 0.0
        for v, kind in zip(vals, t.get('measure', ['relative'] * len(vals))):
            if kind == 'absolute':
                starts.append(0.0), lengths.append(v)
                total = v
            elif kind == 'total':
                starts.append(0.0), lengths.append(total)
            else:
                starts.append(total), lengths.append(v)
                total += v
        return cats, (starts, lengths)
    base = t.get('base', 0)
    starts = list(base) if isinstance(base, (list, tuple)) else [base] * len(vals)
    return cats, ([float(s) for s in starts], [float(v) for v in vals])


def _bars(svg: _Svg, t: dict, index: int, ax: _Axis, ay: _Axis, slot: int, count: int, lay: dict) -> None:
    horizontal = t.get('orientation', 'v') == 'h'
    cats, (starts, lengths) = _bar_parts(t)
    cat_axis, val_axis = (ay, ax) if horizontal else (ax, ay)
    group = lay.get('barmode', 'group') == 'group' and t['type'] == 'bar'
    full = (1 - lay.get('bargap', 0.2)) * (cat_axis.unit() if cat_axis.categories is not None else _slot(cats, cat_axis))
    width = full / count if group else full
    shift = (-full / 2 + width * (slot + 0.5)) if group else 0.0
    colors = t.get('marker', {}).get('color', _trace_color(t, index))
    texts = t.get('text')
    measures = t.get('measure', [])
    prev = None
    for k, (c, s, v) in enumerate(zip(cats, starts, lengths)):
        if t['type'] == 'waterfall':
            kind = measures[k] if k < len(measures) else 'relative'
            color = _WATERFALL['totals' if kind in ('total', 'absolute') else 'increasing' if v >= 0 else 'decreasing']
        else:
            color = colors[k] if isinstance(colors, (list, tuple)) else colors
        mid = cat_axis.pos(c) + shift
        a, b = sorted((val_axis.pos(s), val_axis.pos(s + v)))
        if horizontal:
            svg.rect(a, mid - width / 2, b - a, width, color)
        else:
            svg.rect(mid - width / 2, a, width, b - a, color)
        if t['type'] == 'waterfall' and prev is not None:  # connector from the last bar's end
            if horizontal:
                svg.line(prev[0], prev[1], prev[0], mid - width / 2, '#444', 1)
            else:
                svg.line(prev[0], prev[1], mid - width / 2, prev[1], '#444', 1)
        end = val_axis.pos(s + v)
        prev = (end, mid + width / 2) if horizontal else (mid + width / 2, end)
        if texts is not None:
            label = texts[k] if isinstance(texts, (list, tuple)) else texts
            if horizontal:
                svg.text(max(a, b) + 4, mid, label, anchor='start')
            else:
                svg.text(mid, min(a, b) - 8, label)


def _slot(cats: list, axis: _Axis) -> float:
    nums = sorted(set(float(c) for c in cats))
    step = min((b - a for a, b in zip(nums, nums[1:])), default=1.0)
    return abs(axis.pos(nums[0] + step) - axis.pos(nums[0])) if nums else 1.0


def _scatter(svg: _Svg, t: dict, index: int, ax: _Axis, ay: _Axis) -> None:
    ys = list(t.get('y', []))
    xs = list(t.get('x', range(len(ys))))
    pts = [(ax.pos(x), ay.pos(y)) for x, y in zip(xs, ys) if x is not None and y is not None]
    if not pts:
        return
    mode, color = _mode(t), _trace_color(t, index)
    line = t.get('line', {})
    fill = t.get('fill')
    if fill in ('tozeroy', 'toself'):
        base = ay.pos(0) if fill == 'tozeroy' else None
        ring = pts + ([(pts[-1][0], base), (pts[0][0], base)] if base is not None else [])
        svg.raw(f'<path d="M{" L".join(f"{_n(x)} {_n(y)}" for x, y in ring)} Z" '
                f'fill="{escape(t.get("fillcolor", color))}" fill-opacity="{_n(t.get("opacity", 0.5))}" stroke="none"/>')
    if 'lines' in mode:
        svg.raw(f'<path d="M{" L".join(f"{_n(x)} {_n(y)}" for x, y in pts)}" fill="none" stroke="{escape(color)}" '
                f'stroke-width="{_n(line.get("width", 2))}"{_dash(line.get("dash"), line.get("width", 2))}/>')
    if 'markers' in mode:
        marker = t.get('marker', {})
        r = marker.get('size', 6) / 2
        mc = marker.get('color', color)
        for k, (x, y) in enumerate(pts):
            _marker(svg, x, y, marker, mc[k] if isinstance(mc, (list, tuple)) else mc)
    if 'text' in mode and t.get('text') is not None:
        texts = t['text'] if isinstance(t['text'], (list, tuple)) else [t['text']] * len(pts)
        below = t.get('textposition', 'top center').startswith('bottom')
        for (x, y), label in zip(pts, texts):
            svg.text(x, y + (12 if below else -12), label)


def _marker(svg: _Svg, x: float, y: float, marker: dict, color: str) -> None:
    """One marker: circle (default), square or diamond, ``marker.size`` px across."""
    r, symbol = marker.get('size', 6) / 2, marker.get('symbol', 'circle')
    if symbol == 'square':
        svg.raw(f'<path d="M{_n(x - r)} {_n(y - r)} h{_n(2 * r)} v{_n(2 * r)} h{_n(-2 * r)} Z" fill="{escape(color)}"/>')
    elif symbol == 'diamond':
        d = r * 1.3
        svg.raw(f'<path d="M{_n(x)} {_n(y - d)} L{_n(x + d)} {_n(y)} L{_n(x)} {_n(y + d)} L{_n(x - d)} {_n(y)} Z" '
                f'fill="{escape(color)}"/>')
    else:
        svg.raw(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(r)}" fill="{escape(color)}"/>')


def _heatmap(svg: _Svg, t: dict, ax: _Axis, ay: _Axis, box) -> None:
    z = t.get('z', [])
    cols = list(t.get('x', range(len(z[0]) if z else 0)))
    rows = list(t.get('y', range(len(z))))
    zmin, zmax = _z_range(t)
    scale = t.get('colorscale', 'plasma')

    def edges(axis, keys):
        if axis.categories is not None:
            return [(axis.pos(k) - axis.unit() / 2, axis.pos(k) + axis.unit() / 2) for k in keys]
        nums = [float(k) for k in keys]
        mids = [(a + b) / 2 for a, b in zip(nums, nums[1:])]
        lo = [nums[0] - (mids[0] - nums[0] if mids else 0.5)] + mids
        hi = mids + [nums[-1] + (nums[-1] - mids[-1] if mids else 0.5)]
        return [(axis.pos(a), axis.pos(b)) for a, b in zip(lo, hi)]

    for (ya, yb), row in zip(edges(ay, rows), z):
        for (xa, xb), v in zip(edges(ax, cols), row):
            if v is not None:
                t_ = 0.0 if zmax == zmin else (v - zmin) / (zmax - zmin)
                svg.rect(min(xa, xb), min(ya, yb), abs(xb - xa) + 0.5, abs(yb - ya) + 0.5, _color_at(scale, t_))


def _z_range(t: dict) -> tuple[float, float]:
    flat = [v for row in t.get('z', []) for v in row if v is not None]
    return t.get('zmin', min(flat, default=0)), t.get('zmax', max(flat, default=1))


def _colorbar(svg: _Svg, t: dict, box) -> None:
    """A vertical colour bar right of the plot area (32 steps), ticks like the axes."""
    x0, y0, pw, ph = box
    zmin, zmax = _z_range(t)
    scale, bx, steps = t.get('colorscale', 'plasma'), x0 + pw + 12, 32
    for k in range(steps):
        svg.rect(bx, y0 + ph * (1 - (k + 1) / steps), 14, ph / steps + 0.5, _color_at(scale, (k + 0.5) / steps))
    if zmax != zmin:
        ticks, step = _ticks(min(zmin, zmax), max(zmin, zmax), 5)
        for v in ticks:
            if min(zmin, zmax) <= v <= max(zmin, zmax):
                svg.text(bx + 18, y0 + ph * (1 - (v - zmin) / (zmax - zmin)), _label(v, step), anchor='start')
    title = t.get('colorbar', {}).get('title', {})
    text = title.get('text', '') if isinstance(title, dict) else str(title or '')
    if text:
        svg.text(bx, y0 - 12, text, anchor='start')


def _shape(svg: _Svg, s: dict, ax: _Axis, ay: _Axis, box) -> None:
    x0, y0, pw, ph = box

    def px(v, ref):
        return x0 + float(v) * pw if ref in ('paper', 'x domain') else ax.pos(v)

    def py(v, ref):
        return y0 + ph - float(v) * ph if ref in ('paper', 'y domain') else ay.pos(v)

    xr, yr = s.get('xref', 'x'), s.get('yref', 'y')
    a, b = px(s.get('x0', 0), xr), px(s.get('x1', 0), xr)
    c, d = py(s.get('y0', 0), yr), py(s.get('y1', 0), yr)
    line = s.get('line', {})
    color, width = line.get('color', '#444'), line.get('width', 2)
    if s.get('type', 'line') == 'rect':
        extra = f' fill-opacity="{_n(s.get("opacity", 1))}"'
        if width:
            extra += f' stroke="{escape(color)}" stroke-width="{_n(width)}"'
        svg.rect(min(a, b), min(c, d), abs(b - a), abs(d - c), s.get('fillcolor', 'rgba(0,0,0,0)'), extra)
    else:
        svg.line(a, c, b, d, color, width, line.get('dash'))


def _annotation(svg: _Svg, note: dict, box, ax, ay) -> None:
    """Text at (x, y) in data (``xref="x"``) or paper coordinates. With ``showarrow`` (plotly's default) the text
    sits ``ax`` / ``ay`` px away (default -10 / -30) and a line points at (x, y)."""
    x0, y0, pw, ph = box
    xr, yr = note.get('xref', 'x'), note.get('yref', 'y')
    x = x0 + float(note.get('x', 0)) * pw if xr == 'paper' or ax is None else ax.pos(note.get('x', 0))
    y = y0 + ph - float(note.get('y', 0)) * ph if yr == 'paper' or ay is None else ay.pos(note.get('y', 0))
    font = note.get('font', {})
    color = font.get('color')
    tx, ty = x, y
    if note.get('showarrow', True):
        tx, ty = x + note.get('ax', -10), y + note.get('ay', -30)
        svg.line(tx, ty + (6 if note.get('ay', -30) < 0 else -6), x, y, note.get('arrowcolor', '#444'), 1)
        svg.raw(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="2" fill="{escape(note.get("arrowcolor", "#444"))}"/>')
    anchor = {'left': 'start', 'right': 'end'}.get(note.get('xanchor'), 'middle')
    svg.text(tx, ty, note.get('text', ''), anchor=anchor, size=font.get('size'), color=color)


# --- Sankey ------------------------------------------------------------------------------------------

def _sankey(svg: _Svg, t: dict, box) -> None:
    """Columns by longest path from the sources (sinks go to the last column), node height = max(in, out),
    nodes in input order, links as cubic bands (``node.pad`` / ``node.thickness`` as in plotly)."""
    x0, y0, pw, ph = box
    node, link = t.get('node', {}), t.get('link', {})
    src, dst, val = link.get('source', []), link.get('target', []), [float(v) for v in link.get('value', [])]
    labels = node.get('label', [])
    n = max([len(labels)] + [i + 1 for i in src + dst])
    out_v, in_v = [0.0] * n, [0.0] * n
    for s, d, v in zip(src, dst, val):
        out_v[s] += v
        in_v[d] += v
    size = [max(a, b) for a, b in zip(in_v, out_v)]
    depth = [0] * n
    for _ in range(n):  # longest path (a cycle stops growing after n rounds)
        changed = False
        for s, d in zip(src, dst):
            if depth[d] < depth[s] + 1 and depth[s] + 1 < n:
                depth[d], changed = depth[s] + 1, True
        if not changed:
            break
    last = max(depth, default=0)
    depth = [last if out_v[i] == 0 and in_v[i] > 0 else depth[i] for i in range(n)]
    pad, thick = node.get('pad', 15), node.get('thickness', 20)
    cols = [[i for i in range(n) if depth[i] == c] for c in range(last + 1)]
    ky = min((ph - pad * (len(c) - 1)) / max(sum(size[i] for i in c), 1e-300) for c in cols if c)
    top = [0.0] * n
    for c in cols:
        y = y0 + (ph - (sum(size[i] for i in c) * ky + pad * (len(c) - 1))) / 2
        for i in c:
            top[i] = y
            y += size[i] * ky + pad
    left = [x0 + (pw - thick) * (d / last if last else 0) for d in depth]
    colors = node.get('color', COLORWAY)
    colors = [colors[i % len(colors)] if isinstance(colors, (list, tuple)) else colors for i in range(n)]
    link_colors = link.get('color', 'rgba(0,0,0,0.2)')
    out_y, in_y = list(top), list(top)
    order = sorted(range(len(val)), key=lambda k: (top[src[k]], top[dst[k]]))
    bands = {}
    for k in order:  # outgoing ends, stacked by source then target position
        bands[k] = [out_y[src[k]]]
        out_y[src[k]] += val[k] * ky
    for k in sorted(range(len(val)), key=lambda k: (top[dst[k]], top[src[k]])):
        bands[k].append(in_y[dst[k]])
        in_y[dst[k]] += val[k] * ky
    for k in range(len(val)):
        s, d, hgt = src[k], dst[k], val[k] * ky
        xa, xb = left[s] + thick, left[d]
        xm = (xa + xb) / 2
        ya, yb = bands[k]
        color = link_colors[k] if isinstance(link_colors, (list, tuple)) else link_colors
        svg.raw(f'<path d="M{_n(xa)} {_n(ya)} C{_n(xm)} {_n(ya)} {_n(xm)} {_n(yb)} {_n(xb)} {_n(yb)} '
                f'L{_n(xb)} {_n(yb + hgt)} C{_n(xm)} {_n(yb + hgt)} {_n(xm)} {_n(ya + hgt)} {_n(xa)} {_n(ya + hgt)} Z" '
                f'fill="{escape(color)}"/>')
    for i in range(n):
        svg.rect(left[i], top[i], thick, size[i] * ky, colors[i], ' stroke="#444" stroke-width="0.5"')
        if i < len(labels):
            right = depth[i] < last
            svg.text(left[i] + (thick + 6 if right else -6), top[i] + size[i] * ky / 2, labels[i],
                     anchor='start' if right else 'end')
