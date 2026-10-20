# Stdlib shrink survey

Four production dependencies are ready to hand to parallel agents, one item each. The machine-readable list is [stdlib-shrink-queue.json](stdlib-shrink-queue.json). Everything else in the public account is a test harness, an optional accelerator that already has a stdlib path, a native or framework library, a vendored copy of an archived template, or work already owned inside Aoi.

| Priority | Repo | Package | Difficulty | Approach |
| --- | --- | --- | --- | --- |
| 1 | DRAgoWing `360afbde` | markdown | easy | CDN-fetch the pure wheel, as in Aoi PR 87 |
| 2 | NagoyAction `1181e435` | polars | moderate | `polars_min.py` for one join in `research_digest.collect` |
| 3 | DRAgoWing `360afbde` | numpy | moderate | Port Aoi `shrink/numpy_min.py` (PR 89) for `load_overlap` |
| 4 | Ironmate `9839f8cd` | pypdf | easy | CDN-fetch the pure wheel for `PdfReader.pages` |

Agents should re-resolve wheels when they implement an item. Versions below are what PyPI returned on 2026-10-20.

## Listing

`GET /orgs/myon-bioinformatics/repos` returns 404. The account is a user, so the public list is `GET /users/myon-bioinformatics/repos?per_page=100&type=owner`. That response had 41 repositories, no `Link` header, and `private: false` on every item. Unauthenticated listing does not include private repositories. Eighteen archived repositories were skipped and not cloned:

AttentionToJapaneseBeginners, audio_any2any_gui, convert_img_fmt_to_webp-CUI-, convert_img_fmt_to_webp-GUI-, cumlative_usage, GitHub_api_Template, HelpYouBuildServer, image_ext_converter_gui, LINENotifyAPI_Template, mock_server_JS, niconicoSearchAPI_Template, oci-cost-analysis, python3nmap_GUI_for_Beginners, Qiita_API_Template, scapy_GUI_for_Beginners, video2audio_gui, video_to_image_gui, vlm_ocr_jp_gui.

The other 23 were shallow-cloned at the default-branch tip and scanned with CPython 3.12. Checkout tips in the JSON are stored as `head`, so a 40-character value there is a surveyed revision and not a parent-repository pin. Python declarations came from `pyproject.toml`, `uv.lock`, and `requirements*.txt`. Imports came from the AST (`import`, `from`, and string-literal `importlib.import_module` / `__import__`). JS declarations came from `package.json`. CDN script and stylesheet URLs were matched in HTML, JS, and docs. Dart `pubspec.yaml` dependencies in flutter_navigation_basic are listed and left outside this Python/JS classification.

A name that exists as a `.py` file or a directory of Python anywhere in the same checkout was treated as first-party. Imports such as `ask`, `rule_search`, `propositions`, `blueprobe`, and `sakanalytics` are sibling checkouts, not PyPI packages.

Attribute counts follow the Aoi `shrink_module.py` survey: aliases and `from pkg import name`. Methods on returned values (`frame.join`, `selected.sum`) are invisible to that walk. Where a queue item depends on them, the source was read and the methods are named in the item.

Absence checks used `python -S` so the distro `numpy` 1.26.4 and PyYAML on this machine were not importable. Two wheels were fetched, sha256-checked against PyPI, and imported.

## Queue

### 1. DRAgoWing markdown — easy, CDN fetch

`htmlout.render_md` loads Python-Markdown with `importlib.import_module("markdown")` and calls `markdown(source, extensions=["tables", "fenced_code", "attr_list", "md_in_html"])`. The dependency is `markdown>=3.11.1`. There is no static `import markdown`, which is why an alias survey reports zero uses.

With `python -S` and the clones directory kept off `sys.path`, `render_md("# t")` raises `ModuleNotFoundError: No module named 'markdown'` at `htmlout.py:77`.

PyPI markdown 3.11.2 publishes one wheel, `markdown-3.11.2-py3-none-any.whl`, sha256 `f80390747a2a0ff2ba54da074ce4f862f72a6685f608cd2637b646a6d83993f5`, and `requires_dist` is empty. Importing that wheel and rendering a table, a fenced code block, and `{: .provenance}` produced 177 characters containing `<table>`, `<pre><code>`, and `class="provenance"`.

Copy the Aoi PR 87 experiment: resolve, verify sha256, cache, put the wheel on `sys.path`, assert markdown is not otherwise installed, run `render_md`. Leave the pyproject dependency declared.

### 2. NagoyAction polars — moderate, shrink

`research_digest.py:18` imports polars at module level. This checkout has no `pyproject.toml`. `import research_digest` raises `ModuleNotFoundError: No module named 'polars'` at that line.

`collect` builds two frames, explodes `proposition_id`, drops nulls, left-joins on `proposition_id`, and reads named rows. The alias survey sees `DataFrame` and `col`. The source also uses `explode`, `filter`, `join`, `iter_rows`, and `is_not_null`.

Polars 2.0.0 publishes a `py3-none-any` wheel that requires `polars-runtime-32==2.0.0`, so CDN fetch is the wrong tool. A `polars_min.py` for this call, preloaded as `sys.modules["polars"]`, is the right size. The acceptance check is a two-note fixture whose `current_judgements` match real polars, including a missing exit code on the left join. Any other polars name should fail the generator.

### 3. DRAgoWing numpy — moderate, port the existing min

`overlap.py:11` imports numpy. Under `python -S`, loading that module raises `ModuleNotFoundError: No module named 'numpy'` at that line.

`load_overlap` uses `asarray`, `ones`, `flatnonzero`, indexing, `.shape`, `.sum()`, `&=`, and `~`. That is the same region-counting loop Aoi PR 89 already runs on `shrink/numpy_min.py`. The masks come from `rule_search._mask` and `_masks_to_matrix`, which are not in this repository. At PythDRagoras `6570f77f` those functions import numpy themselves and need `zeros`, `frombuffer`, `uint8`, and `unpackbits`, which the existing min file already implements.

Preload that min module as `sys.modules["numpy"]` before both imports. Keep polars installed: the frame is passed into `rule_search._mask`, so a local `read_ndjson` shim does not remove polars. This item is the bitmask path. The SVD path in QueRyu is a different agent.

### 4. Ironmate pypdf — easy, CDN fetch

`pptx_preview.render_preview_html` uses `len(PdfReader(pdf, strict=True).pages)`. Tests also import `PdfReadError`. `from pypdf import PdfReader` raises `ModuleNotFoundError: No module named 'pypdf'`.

pypdf 6.21.1 publishes `pypdf-6.21.1-py3-none-any.whl`, sha256 `553e2d42f5bb3bb2a785a7d3de8ef90bb7df74b5413eff5a7b4388007ea9254b`. `PdfReader` imports from that wheel. The only `requires_dist` entry is `typing_extensions>=4.0; python_version < "3.11"`. Ironmate requires Python >=3.11, so the marker is inactive. Aoi `fetch_module.py` rejects every non-extra `requires_dist` line. Evaluate the marker, then fetch.

Calling the real function with an HTML file and an output directory raises `ModuleNotFoundError: No module named 'playwright'` at `pptx_preview.py:25`, one line before the pypdf import. Playwright stays. Prove `PdfReader` on a one-page PDF without launching a browser.

## Already owned in Aoi

Aoi `06b6235e` is not in the queue.

| Package | What this checkout does | Why it is not queued |
| --- | --- | --- |
| polars | 1866 uses of 26 names in 15 production files | Expression engine, and the pure wheel needs `polars-runtime-32`. Assigned. |
| numpy | 478 uses of 66 names, including `linalg.norm` and `random` | Overlap subset is already `numpy_min`. Linalg is assigned. |
| httpx | `Client` in 3 production files | Assigned. Wheel depends on anyio, certifi, httpcore, idna. |
| selectolax | `lexbor.LexborHTMLParser`, 11 uses | Compiled wheels. Assigned. BlueProbe's only use is the parity test. |
| markdown | `dragowing/htmlout.py` dynamic import | PR 87 already fetches the wheel. |
| ortools | CP-SAT in two scripts | Native solver. Also in SakAnalytics `season_exact.py`. |
| pandas | `read_json` in one research script | No pure wheel. Left with the Aoi agents. |

QueRyu `ask/suggest.py` is byte-identical to Aoi `queryu/ask/suggest.py` (254 lines). Its numpy surface is `linalg.norm`, `linalg.svd`, `packbits`, and `log`. `python -S` fails at `suggest.py:18`. That file belongs to the Aoi numpy linalg agent, so QueRyu is not a second item.

## Not feasible, with the reason that decided it

**Native numeric and dataframe libraries whose used surface is the library.** PythDRagoras numpy is 56 names across `littlebit_approx`, `littlebit_holdout` (`linalg.pinv`), `littlebit_prune`, and `rule_search`. Importing `rule_search` under `python -S` fails at line 33. SakAnalytics polars is 1354 uses of 24 names; importing `sakanalytics` fails at line 125. PythDRagoras polars is 446 uses of 18 names in 6 production modules. ortools is CP-SAT and also depends on absl-py, numpy, pandas, and protobuf.

**Compiled imaging.** browser-test-kit `scripts/image_diff.py` requires Pillow >=13 and uses open, convert, crop, channel difference, lighter, point, histogram, getbbox, and getextrema. `python -S` exits 2 with `Pillow >=13 is required`. Pillow 13 publishes abi wheels only. Ironmate already decodes ordinary PNG without Pillow and raises `ImageDecodeError` naming the optional package for JPEG, WebP, and the PNG variants the stdlib path rejects. flutter_navigation_basic imports Pillow inside a `try` for one JPEG fixture path; `--check` is stdlib.

**PyYAML 6.0.3 has no `py3-none-any` wheel** (abi wheels plus an sdist). The uses are `safe_load`, `load`, `safe_dump`, and `BaseLoader`: the parser itself. That covers production scripts in the markdown repo and test-only loads in myon-bioinformatics, flutter_navigation_basic, browser-test-kit, Ironmate, web-ui, markdown, ascii_artist, nvd_nist_known_vulns, yourself, and mcp-toolcall-lab.

**Frameworks and drivers.** Gradio (markdown demos, mcp-toolcall-lab, web-ui, optional gh_identity lab), Streamlit (markdown demo), FastMCP and mcp (mcp-toolcall-lab's servers; `Field` constraints are consumed by FastMCP; pydantic-core is compiled), httpx there (`post` in `chat_ui.py`, and tests use `AsyncClient` and transport errors; the wheel depends on anyio, certifi, httpcore, and idna), Playwright and `@playwright/test` (Ironmate preview, browser-test-kit, web-ui, ascii_artist, flutter e2e, github.io), stagehand, `serve`, FastAPI, Starlette, and uvicorn.

**JavaScript writers.** Ironmate `scripts/xlsx_io.mjs` uses exceljs (9 runtime dependencies) and SheetJS. SheetJS is already the CDN tarball `https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz`. `scripts/pptxgen_render.mjs` uses pptxgenjs `defineLayout`, `addSlide`, `background`, and `writeFile` to emit an OOXML package shell. pptxgenjs 4.0.1 depends on jszip. A min file would be a PPTX writer.

**Vendored archived templates inside Ironmate `vendor/`.** These are copies of archived repositories, not Ironmate's declared runtime. PySimpleGUI (924 uses, 25 widget names), FreeSimpleGUI, and scapy publish pure no-dependency wheels, and the call surface is the widget set or the packet stack (`send`, `sniff`, `sr`, protocol layers). requests, matplotlib, scipy, pandas, fastapi, uvicorn, python3-nmap, and pillow-heif sit in the same copies and fail the pure-wheel or the small-surface test.

**Test harnesses.** pytest is the used surface in every Python repo that has tests (raises, parametrize, fixtures, and custom marks in Err-o-RoadRunner). coverage is the same kind of dev tool. They are not runtime shrink targets.

**Declared and not imported.** browser-test-kit devDependencies exceljs, linkedom, markdown-it, and marked have no static import in that checkout. xprobe devDependencies js-yaml and smol-toml have none either. marked 18.1.1 and smol-toml 1.9.1 have zero npm runtime dependencies, so a CDN URL would be a fetch in the markdown repo's `tests/mirror/facts.mjs` (marked is imported there; smol-toml is only declared). That mirror is a test, so it is not in the handoff queue. markdown-it, linkedom, and string-width have runtime dependencies and are the libraries the mirror is comparing.

**Dart.** flutter_navigation_basic `pubspec.yaml` depends on `crypto`, `get`, `gal`, `image_picker`, and `shared_preferences`, plus the Flutter SDK. Those are not Python wheels or JS CDN scripts.

## CDN scripts already in place

DRAgoWing and Aoi document Plotly as `https://cdn.jsdelivr.net/npm/plotly.js-dist-min/plotly.min.js` and do not depend on the Python `plotly` package. github.io `tools/mcp-toolcall-lab/repository-diagnostics.html` loads first-party web-ui CSS and JS from jsDelivr at commit `adb23d7ba6ea94672b76457573f6655a081ee054`. Ironmate's SheetJS pin is the tarball above.

## Repos whose production code does not import a third-party Python or JS library

Err-o-RoadRunner, cli_args, and search_seq_including_spaces import pytest only. nvd_nist_known_vulns, yourself, and this parent repository add PyYAML in tests. gh_identity's only non-pytest import is an optional Gradio lab script. xprobe's non-pytest surface is two unused JS devDependencies.

## Confirmation log

| Check | Result |
| --- | --- |
| markdown wheel import | 3.11.2, sha256 `f8039074…9393f5`, table / code / provenance class rendered |
| `htmlout.render_md` under `python -S` | `ModuleNotFoundError` at `htmlout.py:77` |
| `overlap.py` under `python -S` | `ModuleNotFoundError` at `overlap.py:11` |
| `import research_digest` | `ModuleNotFoundError` at `research_digest.py:18` |
| `from pypdf import PdfReader` | `ModuleNotFoundError`; wheel 6.21.1 then imports `PdfReader` |
| `render_preview_html` with a real HTML file | `ModuleNotFoundError: playwright` at `pptx_preview.py:25` |
| Ironmate `python -S` auto backends | numpy and PIL both resolve to `stdlib` |
| `grid_tools._runs_gcd(..., "numpy")` under `python -S` | `ModuleNotFoundError` at `grid_tools.py:73` |
| browser-test-kit `image_diff.py` under `python -S` | exit 2, `Pillow >=13 is required` |
| `import rule_search` / `import ask.suggest` / `import sakanalytics` under `python -S` | `ModuleNotFoundError` at the numpy or polars import line |
| `from selectolax.lexbor import LexborHTMLParser` | `ModuleNotFoundError` |
| QueRyu `ask/suggest.py` vs Aoi `queryu/ask/suggest.py` | identical |

## Limits

The survey is one shallow clone of each public default branch on 2026-10-20. Dynamic imports whose module name is not a string literal are invisible. Methods on values are counted only where the queue item's source was read. `python -S` shows the import fails in a clean interpreter; it does not execute network, browser, or GUI entry points. Parent CI audits recorded revisions of enrolled children. This document does not change locks, workflows, or child checkouts, and a green parent check is not a claim that child merges are blocked.
