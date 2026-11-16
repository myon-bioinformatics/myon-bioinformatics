# plotly_min.py

`plotly_min.py` is a stand-in for the plotly `graph_objects` calls our consumers make. It is a single file using only standard modules and runs on Python 3.9+. The source of truth is this repository's `tools/stdlib_min/plotly_min.py`. Consumers lock the exact bytes from here, like the other `tools/stdlib_min/` stand-ins. The first consumer is FluidFullSpeedSimulation (README figures).

The figure is plotly's own JSON, so `to_dict` / `to_json` / `write_html` feed plotly.js unchanged. Static export is the difference. In plotly.py, `write_image` needs Kaleido 1.x, which drives a Chrome install. Here `to_image` / `write_image` draw SVG with the standard library, in plotly's default look (colorway, `#E5ECF6` plot area, white grid, U+2212 minus on tick labels).

## Scope

| Category | API |
|---|---|
| Figure | `Figure(data, layout)` (also `Figure(dict)`), `add_trace`, `update_layout`, `add_shape`, `add_annotation`, `add_vline`, `add_hline`, `to_dict` / `to_plotly_json`, `to_json` |
| Traces | `Scatter` (`mode` lines / markers / text, `line`, `marker`, `fill` `tozeroy` / `toself`), `Bar` (`orientation`, `base`, `marker.color`, `text`; `barmode` group, any other mode draws full-width overlaid bars, stacking is not covered), `Waterfall` (`measure` absolute / relative / total), `Sankey` (`node.label` / `color` / `pad` / `thickness`, `link.source` / `target` / `value` / `color`), `Heatmap` (`z`, `x`, `y`, `zmin` / `zmax`, `colorscale` name or list, colour bar) |
| Keywords | plotly's magic underscores (`line_color=...` → `{"line": {"color": ...}}`), bare string titles (→ `{"text": ...}`) |
| Output | `to_image` / `write_image` (SVG only), `to_html` / `write_html` (plotly.js from `cdn.plot.ly`, version `PLOTLY_JS`; `include_plotlyjs=False` leaves the script tag out) |
| Namespace | `graph_objects` (`go = plotly_min.graph_objects`) |

Text uses `FONT` (`Meiryo, メイリオ, sans-serif`, the same stack as DRAgoWing) unless `layout.font.family` is set. `write_html` also puts `FONT` into the layout when the figure sets no font, so the browser view matches the SVG. Meiryo cannot be bundled; other systems fall back to their sans-serif.

## Intentional differences from plotly

| Item | plotly | plotly_min |
|---|---|---|
| Static export | Kaleido 1.x + Chrome; PNG / JPEG / WebP / SVG / PDF | SVG only, drawn in Python; other formats raise `ValueError` |
| Layout engine | plotly.js (auto margins, text measurement in the browser) | Fixed margins (`l` 80, `r` 80, `t` 100, `b` 80, overridable); the right margin grows for the legend; text width is estimated (CJK = 1 em, others 0.58 em) |
| Axes | Many types and tick formats | One `xaxis` / `yaxis`; `linear` or `category` (any string value makes a category axis); `range`, `autorange="reversed"`, `nticks`; 1 / 2 / 2.5 / 5 steps |
| Sankey placement | Iterative relaxation (d3-sankey) | Column = longest path from a source, sinks in the last column, nodes in input order, each column centred; `node.x` / `node.y` are not read |
| Annotations | Full arrow styling | Text with an optional straight pointer (`showarrow`, `ax`, `ay`) |
| Not covered | — | Subplots, 3D / geo / polar, templates, animation, hover, legend groups, stacked bars, `tonexty` fills, other trace types (they raise `ValueError` when added as dicts) |

## Verification

- `tests/test_tools.py` (`test_plotly_min_*`): figure JSON (magic underscores, titles, shapes, `Figure(dict)` round trip); SVG is byte-stable, escaped, and every text node carries the font; scatter paths are linear in the data and unchanged when the data and ranges are scaled by random A×10^-N factors; bar `base` and waterfall running totals map to the expected rectangles and colours; Sankey node heights are max(in, out), each band keeps its width end to end, and the picture is unchanged when all values are scaled by A×10^-N; heatmap colours reach both ends of the colour scale; HTML loads the pinned CDN build and cannot be closed by a `</script>` in the data.
- `test_plotly_min_matches_plotly_figure_json` compares `to_dict()["data"]` with plotly.py for each trace type when plotly is installed. plotly is a reference only and is not installed by default, so the test is skipped there.
- `test_stdlib_min_stand_ins_import_only_the_standard_library` runs the file with nothing installed.
