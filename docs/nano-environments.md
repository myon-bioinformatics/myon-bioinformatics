# Nano environments: current screening and opt-in setup

## Scope and result

The 2026-10-31 `current_snapshot` in
[the existing dependency inventory](stdlib-shrink-queue.json) screens **24 public,
non-archived repositories** at the listed immutable default-branch commits.
It supersedes the old queue's current-action claims, without erasing its dated
evidence. This is source/manifests screening, not a complete transitive closure,
compatibility test, install benchmark or deployment. Private sources are outside
this public artifact. Open PRs are not treated as merged behavior.

**Do not redo already-stdlib cores.** The useful unit is a feature, not a whole
repository label or the section in which its dependency happens to be declared.
For example, Ironmate's lazy PDF-preview imports are optional feature runtime
dependencies even though its manifest lists them in `dev`.

The inventory stage added no runtime implementation or deletion. Its snapshot
counts remain historical. Follow-ups add explicit parent preparation tools,
`tools/nano_export.py` and `tools/nano_node.py`, not new application/library replacements. No repository dependency
upgrade/removal, adopted pin, child change, CI/Pages setting or test exception is
introduced. Application/runtime files and lines deleted: **0/0**; application
functions added: **0**; tests removed: **0**.
The existing [two profiles and static planner](runtime-profiles.md) retain their
v1 API, frozen source/package examples and null execution evidence.

## Feature screening

Each row's exact revision, manifests and actual consumer source blobs are in
`current_snapshot.repositories`; package names alone are not proof of use.

| Repository | Core / deeper-work decision | Dependencies to retain for selected features | Delivery status / next concrete decision |
|---|---|---|---|
| Aoi | Five min distributions already exist; do not rewrite them | Research runtime group: Polars, NumPy, HTTPX, Python-Markdown; separate CP-SAT/Pandas research and native backend boundaries | **Prepared:** runtime-only export; 20→12 rows, not installed counts. Remaining research APIs stay package-bearing. |
| PythDRagoras | Full analysis remains package-bearing | Polars expressions and broad NumPy APIs, including operations outside the existing minimum | **Retain:** native analysis needs real Polars/NumPy; a bundler is not a replacement. Next work needs representative analysis parity, not another source-only demo. |
| SakAnalytics | Full analysis remains package-bearing | Polars; optional OR-Tools exact solver; retain complete numerical tests | **Retain:** separate ordinary analysis from optional CP-SAT; do not remove solver/native tests or claim a stdlib equivalent. |
| DRAgoWing | Existing explicit stdlib overlap path is a useful minimal slice | Full path still uses Python-Markdown; rendered plots need Plotly.js, exports need Chromium; default overlap retains Polars/NumPy | **Measured:** complete HTML+Markdown/stdlib feature; native/browser exports remain separate. |
| mcp-toolcall-lab | Reuse existing portable-execution slice; full server is not stdlib | FastMCP/MCP/Pydantic; optional UI/browser/model/product-integration environments remain separate | **Measured caller feature:** 69→28 installed distributions; separate full server still required, so no whole-host saving claim. |
| NagoyAction | Pipeline runner already stdlib | Existing digest `polars_min` covers one path only; inbox/worker still needs Aoi sources and Polars metadata; optional model/Docker hook | **Retain feature boundary:** keep existing min collector; full worker still requires Aoi source and installed Polars metadata. Do not repeat the already-minimal runner. |
| QueRyu | Already no mandatory pip runtime dependency | BlueProbe source integration; optional ctypes LAPACK/BLAS auto-discovery is not pure-only execution | **Core excluded:** already vendored numpy_min; retain optional native acceleration and BlueProbe integration. |
| BlueProbe | Observation/cache core already stdlib | Optional lab extractor bridge or caller-provided extractor; preserve Lexbor comparison tests | **Core excluded:** retain explicitly selected extractor bridge; no new mandatory package to remove. |
| Ironmate | Python core/PNG paths already minimal | Optional ExcelJS/PptxGenJS, Pillow/NumPy, Playwright + pypdf, LibreOffice/UNO features; no repeated SheetJS removal | **Measured:** PPTX-only 18 installed paths; XLSX, codecs, preview and UNO remain independently optional. |
| markdown | Conversion core already stdlib | Optional frontends and independent Node mirrors; legacy-Python TOML parser path is separate | **Measured Gradio feature:**72→52 installed distributions for text-input API; conversion core stays excluded, Streamlit/browser/mirror tests stay in their full lanes. |
| web-ui | Python/Node core has no installed runtime packages | Optional Gradio; generated Plotly/PptxGenJS browser resources remain actual dependencies | **Core excluded:** later isolate real Gradio serving from Stagehand/browser tooling; browser assets are not replaced by HTML generation. |
| browser-test-kit | Core and current PNG diff already stdlib | Real browser engines and independent Markdown/HTML/XLSX mirror readers are purposeful tests/features | **Measured mirror feature:** 221→30 installed package paths, both engines and original test runner retained; unrelated XLSX tests stay in the full lane. |
| xprobe | Canonical core already one stdlib file | YAML needs Node + js-yaml; TOML uses tomllib on Python 3.11+, Node + smol-toml on older Python | **Core excluded:** YAML/legacy TOML need genuine parsers; fixed optional npm lock is missing, so no reproducible new install claim. |
| TalkCompress | Compression/estimator core already stdlib | Exact tokenizers, translation engines/packs, SVG renderer/fonts and explicitly selected model verification are not replaced by estimates/XML | **Core excluded:** keep tokenizer/translation/render/model environments explicit; fixture output is not exact external-feature parity. |
| gh_identity | Core and Node wrapper already minimal | HTML-only DOM-lab export avoids Gradio; interactive lab still needs it; wrapper also needs Python | **Core excluded:** static export already avoids Gradio; retain interactive serving and authenticated Git/GitHub semantics. |
| flutter_navigation_basic | Flutter/Dart application, not a Python/Node replacement target | Existing SDK/packages/browser matrix; optional photo regeneration needs Pillow and host encoders | **Other runtime:** preserve actual Flutter/Dart build and platform plugins; do not count Python tooling as app reduction. |
| myon-bioinformatics.github.io | Static site/core import helpers already minimal | Browser/Stagehand developer lanes; optional import Docker image is not a production requirement | **Core excluded:** host static build/serve already available; retain browser validation separately from publication. |
| ascii_artist | Stdlib text-rendering core: exclude deeper reduction | Preserve tests and existing Pages browser capture tools | **Excluded:** already minimal core; retain browser capture tests. |
| search_seq_including_spaces | Stdlib readers/acquisition: exclude deeper reduction | Live endpoints need explicit network/service access, not a new SDK | **Excluded:** already stdlib; explicit live acquisition remains separate. |
| nvd_nist_known_vulns | Stdlib implementation: exclude deeper reduction | Live NVD access is distinct from offline helpers; Node launcher still needs Python | **Excluded:** already stdlib; network/API access is not package setup. |
| yourself | Stdlib environment inspection: exclude deeper reduction | Retain developer tests; optional launcher hosts | **Excluded:** already stdlib observation; missing inspected programs are data, not install requests. |
| cli_args | Stdlib argument handling: exclude deeper reduction | Explicitly executed commands have their own prerequisites | **Excluded:** already stdlib; selected child command owns its runtime. |
| Err-o-RoadRunner | Stdlib diagnostics: exclude deeper reduction | Build/test tools and the explicitly inspected/executed programs remain separate | **Excluded:** already stdlib; keep diagnostic/test contracts. |
| myon-bioinformatics | Stdlib parent tools: exclude deeper reduction | Git/gh when selected, Node for bridge, full contributor/oracle environment | **Excluded core / coordinator:** owns opt-in preparation and coverage; old runtime-profile/planner API unchanged. |

### Coverage is not a claim that every repository has been minimized

The table covers every repository in the **original 24-public snapshot** once.
It distinguishes seven already-minimal whole-core exclusions, ten other
repositories with retained optional/native/service boundaries, one Flutter/Dart
application, and six selected repositories (Aoi, DRAgoWing, Ironmate, MCP lab,
browser-test-kit, markdown). A selected feature does not complete all features in its
repository. DRA HTML, Ironmate PPTX, MCP chat client, BTK document mirrors and
Markdown Gradio have bounded installed-output measurements; Aoi remains export-only.
No unmeasured row is marked complete merely because its source was inspected.

Do not rerun a broad census for each change. Keep the original source pins as
historical evidence and refresh only the selected feature's relevant inputs.
The [second measured pair](nano-client-mirror-environments.md) is **MCP SDK chat
client** and **BTK Markdown/HTML mirror checks**, retaining their real SDK/parser
implementations and every existing full-suite test. The final preparation-wave
measurement selects [Markdown Gradio](nano-gradio-environment.md), not its
already-stdlib converter. Stop speculative feature additions here and separately
plan child adoption of the reviewed opt-in recipes. Remaining genuine native,
solver, browser and service dependencies are retained, not labeled minimized.

### Docker boundary: host execution versus actual integration products

- DRA HTML, Ironmate PPTX and the selected MCP client/BTK mirror paths can use
  installed host Python/Node plus their feature packages. No container is needed
  to prepare them. A separately running MCP server remains a real prerequisite
  for client protocol work; it is not counted as removed by thinning the client.
- MCP portable source execution already works without Docker; do not count that
  existing stdlib slice again. Its build-time model summaries still need the
  real pinned model/runtime, and LibreChat/Open WebUI tests need actual product
  services. A no-model/source-only export does not replace those checks.
- Portfolio's Dockerfile is a combined static-build/browser-test recipe, not a
  deployed Pages runtime. Host static build/serve remains available. Its recorded
  Node22 image versus Node24+ manifest mismatch is not silently repaired here.
- Markdown's Compose setup tests real Open WebUI/FastMCP integration; it is not
  required for core conversion. web-ui/ascii_artist host paths are already
  package-free; optional Gradio and browser assets remain actual dependencies.
- Flutter's E2E image packages its real SDK/browsers. TalkCompress/Aoi/NagoyAction
  model hooks and native libraries are opt-in features, not substitutes supplied
  by minpy/minjs. Their installed/native/model measurements are outside
  the selected preparation recipes.

No Docker image/build setting, Pages publisher or CI job is removed or altered.
Avoiding Docker for a selected host feature is not evidence of reduced image
build time, equivalent isolation, or elimination of service/native dependencies.

### Reuse before writing another minimum

- [minpy](minpy.md) bundles supported same-repository Python source. It does not
  implement third-party APIs or remove native libraries, package data or host
  runtimes. Allowlisted third-party source is still third-party, not stdlib.
- Aoi's [mindeps limits](https://github.com/myon-bioinformatics/Aoi/blob/385e14a09ad56b69c57ec1cbae04c16299d387c5/mindeps/LIMITS.md)
  define narrow HTTPX/HTML-selector call surfaces. HTTP exceptions/features and
  HTML5 error recovery differ. Keep the real-package comparison tests.
- Aoi's [pkgmin study](https://github.com/myon-bioinformatics/Aoi/blob/385e14a09ad56b69c57ec1cbae04c16299d387c5/pkgmin/LIMITS.md)
  found an **empty** requests/tqdm/Bio surface; its stand-ins export no functions.
  They are not reusable HTTP clients, progress bars or sequence parsers.
- The [five Aoi distributions](https://github.com/myon-bioinformatics/Aoi/blob/385e14a09ad56b69c57ec1cbae04c16299d387c5/docs/distributions.md)
  expose narrower APIs, not all analysis functionality. The numerical minimum's
  decomposition contract does not promise complete NumPy or ranking equality.
- No implementation path named `mini.js`/`minjs` was identified in the 24 pinned
  trees or scoped search. Existing `.min.js` distribution references do not
  establish a general API-replacement tool. Node built-ins and existing JS
  helpers can be reused, but Python-backed launchers are not JS implementations.

No new self-written application/library replacement is proposed. Future candidates
must name a real uncovered call surface, preserve the original oracle, test
errors/boundaries and compare complete outputs before dependency removal.
Changing delivery from pip/npm to a downloaded wheel/CDN is not elimination.

## Reproducible preparation without changing a repository

### Measured cross-repository features (2026-11-01)

The [two feature environments](nano-feature-environments.md) extend preparation
past Aoi: complete DRAgoWing HTML generation with its existing explicit stdlib
overlap backend, and Ironmate PPTX generation without the XLSX engine.
Existing versions were installed in separate local environments and outputs
compared. These actual installed counts/bytes must not be confused with the
Aoi **export-row** comparison below or the historical inventory's null metrics.
The [MCP client / BTK mirrors follow-up](nano-client-mirror-environments.md) adds
two further selected environments with explicit server/test accounting. The
old runtime-profile schema and planning-only evidence remain unchanged.

### Executable Aoi runtime-only preparer (follow-up)

`tools/nano_export.py` operationalizes one package-bearing selection: Aoi's
research runtime, **not** its already-stdlib distributions. Aoi's default setup
includes both `dev` and `runtime`; this helper exports the existing `runtime`
group without pytest/coverage/selectolax and their dev-only dependencies. It
does not delete these tools or weaken their independent-oracle tests.

```sh
python -S tools/nano_export.py --source /path/to/Aoi-inputs \
  --output-parent /path/to/existing-export-directory
```

Use Python 3.11+ and a trusted installed `uv` (optionally `--uv /path/to/uv`).
The source directory must contain `pyproject.toml` and `uv.lock` matching the
exact Aoi Git blobs already recorded in the inventory. No fetch, source refresh,
full clone, install or workload is performed. Other snapshots/lock structures,
Git/path/workspace dependencies and edited or symlinked inputs fail closed.
The supported local Aoi project is omitted from the export. Hashes verify **only
these two input files**; the recorded source commit is inventory provenance,
not proof of a checkout HEAD, ancestry, full-source license/security or behavior.

Each call copies the verified inputs to a unique private directory outside the
source, disables uv configuration discovery, clears uv/pip/Git overrides and
Python environment selectors, forces
the current Python executable, and runs locked/offline/no-Python-download export
with `--no-default-groups --group runtime --no-emit-project`. Only after success
and unchanged input checks does it publish `requirements.txt` and `receipt.json`
and print their directory. Failures remove that invocation's directory; callers
must require exit 0 and its receipt, not watch for partially written files.
The trusted-tool subprocess has a timeout and bounded returned-file size; this
is not a hostile-process sandbox or a disk/process-tree quota.

The receipt records input Git blobs, inventory SHA-256, uv/Python versions,
requirements SHA-256 and **exported row count**. It explicitly says no checkout
verification, installation or workload occurred. Requirements use pinned
registry packages and hashes, with no local project path. After the helper has
exited 0, a separately chosen installation can use its completed directory:

```sh
NANO_EXPORT=/absolute/completed/directory/printed/by/helper
test -s "$NANO_EXPORT/receipt.json" && test -s "$NANO_EXPORT/requirements.txt" || exit 1
/path/to/selected/python -m venv "$NANO_EXPORT/venv" || exit 1
"$NANO_EXPORT/venv/bin/python" -m pip install --require-hashes \
  --only-binary=:all: -r "$NANO_EXPORT/requirements.txt"
```

This Aoi installation was **not run** and still needs compatible wheels. The manual
recipe below instead creates `FEATURE_WORK/runtime.txt`; its variables and
filenames are not the helper's output contract.

[Recorded offline comparison](evidence/nano-exports/2026-10-31-aoi.json): uv
0.13.4 / Python 3.12.14 exported **20 default-group rows → 12 runtime rows**.
The omitted dev-only names are colorama, coverage, iniconfig, packaging, pluggy,
pygments, pytest and selectolax. Both NumPy marker branches remain; these are
**not installed-package counts**. Two simultaneous real offline helper calls
completed in distinct directories with the same requirements digest, without
changing source files. This checks output isolation, **not parallel speedup**.
No Aoi installed bytes, install/build time or runtime performance was measured.

### Manual recipes and remaining features

Commands below are **opt-in recipes, not executed installation steps**. Use a
trusted checkout at the inventory's commit, an explicitly chosen installed
interpreter and a fresh per-feature work/environment directory. Verify
`git rev-parse HEAD`, lock identity and platform before preparation.
Do not resolve “latest”, upgrade, edit a lock, or silently change interpreter.
Source siblings/data described by each repository remain prerequisites.

### 1. No packages for an already-stdlib feature

Use the existing source entry directly; `-S` excludes site-packages but is not a
sandbox and does not prevent subprocesses, network or ctypes native loading.
For example, this parent's planner reads only the two explicit JSON inputs:

```sh
python -S tools/runtime_plan.py --profiles runtime-profiles.json \
  --profile stdlib-core --environment tests/fixtures/runtime_plan/unknown.json
```

Its unknown result exits 1 intentionally; this is not a failed installation.
Do not run all feature entrypoints merely to check their presence.
MCP lab already documents package-free Python/Node
[portable execution](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/06cdc8e4e14d50de45c8b8122a5ccff6911c3404/docs/portable-execution.md);
it is not the full FastMCP server or a model run.

### 2. Export only the selected existing Python dependency set

For the pinned PythDRagoras, SakAnalytics or MCP-lab checkout, this exports the
locked **project runtime** without dev/default groups or installation of the
project itself:

```sh
# Set EXPECTED_COMMIT to the selected repository's full inventory commit.
: "${EXPECTED_COMMIT:?Choose the exact inventory commit first}"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT" || exit 1
test -z "$(git rev-parse --show-prefix)" || exit 1  # repository root
git diff --quiet "$EXPECTED_COMMIT" -- pyproject.toml uv.lock || exit 1
umask 077
FEATURE_WORK=$(mktemp -d "${TMPDIR:-/tmp}/nano-runtime.XXXXXXXX") || exit 1
git rev-parse HEAD > "$FEATURE_WORK/source-commit.txt" || exit 1
git hash-object pyproject.toml uv.lock > "$FEATURE_WORK/source-blobs.txt" || exit 1
if ! uv export --offline --locked --no-python-downloads --no-default-groups \
  --no-emit-project --format requirements.txt > "$FEATURE_WORK/runtime.txt.tmp"; then
  exit 1
fi
git hash-object pyproject.toml uv.lock > "$FEATURE_WORK/source-blobs.after.txt" || exit 1
cmp "$FEATURE_WORK/source-blobs.txt" "$FEATURE_WORK/source-blobs.after.txt" || exit 1
mv "$FEATURE_WORK/runtime.txt.tmp" "$FEATURE_WORK/runtime.txt" || exit 1
```

These POSIX-shell examples share the same `FEATURE_WORK` value for exactly one
selected source/feature. Keep its source-commit receipt with the export; use a
different fresh directory for another feature or worker. The clean selected
commit and manifest/lock Git blobs bind the recipe to the reviewed inputs; do
not share a concurrently writable checkout. A failed export never
publishes `runtime.txt`, so a later install cannot consume a truncated result.

For **Aoi**, add `--group runtime`: its research libraries live in that group,
not `project.dependencies`. Without it the export omits the research runtime.
Never add `--all-extras` or all groups “just in case”.
An optional group/extra may be selected only when that exact manifest names it.
Keep full dev/oracle environments for tests; these are runtime selection steps,
not permission to skip tests.

This exact offline export was checked on the four pinned manifest/lock pairs
using uv 0.13.4 and CPython 3.12.14: all succeeded, and both input files remained
byte-identical. No packages were installed. The exports contain 12/4/2/72
requirement rows respectively (Aoi/PythDRagoras/SakAnalytics/MCP lab), including
platform/Python markers; these are **not installed-package counts**, closure
sizes or performance results. Reproduce using the source revisions in the
inventory; `--locked` failure is a stop, not a reason to re-lock.

A later, explicitly chosen pip install can consume that export in a new venv:

```sh
test -s "$FEATURE_WORK/runtime.txt" || exit 1
/path/to/selected/python -m venv "$FEATURE_WORK/venv" || exit 1
"$FEATURE_WORK/venv/bin/python" -m pip install --require-hashes \
  --only-binary=:all: -r "$FEATURE_WORK/runtime.txt"
```

This second step was **not run**. Wheel availability is OS/architecture/Python
specific. Stop if the selected target has no compatible wheel; do not silently
build, upgrade, drop hashes or use a different package version. The project and
its entry-point scripts are not installed by this export: use the documented
source invocation (MCP lab needs `PYTHONPATH=src`) or separately account for its
build backend. Do not apply this recipe blindly to Git/path/workspace dependency
exports. See uv's [group/lock semantics](https://docs.astral.sh/uv/concepts/projects/sync/),
[export options](https://docs.astral.sh/uv/reference/cli/#uv-export) and pip's
[hash-checking contract](https://pip.pypa.io/en/stable/topics/secure-installs/).

### 3. Reuse existing reduced-dependency feature switches

These are existing entrypoints recorded at inventory time, not themselves new
installation or parity claims. The [DRA HTML follow-up](nano-feature-environments.md)
now separately measures one complete supported feature path:

| Feature | Existing selection | Boundary |
|---|---|---|
| DRAgoWing overlap | `python -m dragowing --results <DIR> --out <OUT> --overlap-backend stdlib` | Requires the README's package-parent layout; [typed JSONL contract](https://github.com/myon-bioinformatics/DRAgoWing/blob/be2185b142aebcbedee97688fe08ee0092179472/docs/overlap-stdlib.md); report Markdown/Plotly needs remain |
| NagoyAction digest | `python scripts/collect_with_polars_min.py --deps min --cycle <CYCLE> <OUT>` | [Existing runner](https://github.com/myon-bioinformatics/NagoyAction/blob/11fc194f5257e090f2b2addd29f10fd8d6986cbe/scripts/collect_with_polars_min.py); requires Aoi layout/data and intentionally refuses installed Polars; not the full worker |
| QueRyu numeric subset | `NUMPY_MIN_PURE=1` on the existing caller | Suppresses optional native loading; does not promise full NumPy/ranking equivalence |
| gh_identity DOM lab | Existing `--export-html` mode | HTML export avoids Gradio; interactive UI and browser evaluation remain separate |

### 4. npm, apt and Docker remain feature-specific

Ironmate's unchanged locked npm runtime has ExcelJS and PptxGenJS. Its existing
`npm ci --ignore-scripts` installs that declared closure; the manifest does not
split the two into separately installable features. The initial inventory did
not install either closure. The [2026-11-01 parent preparer and measurement](nano-feature-environments.md)
now uses official npm's verified lock-only projection in a separate workdir to
install PPTX alone, without editing the child manifest/lock. Do not hand-edit
the lock or infer a one-engine install from the original command.
Browser-test-kit's npm mirror
dependencies are independent validation engines, not disposable runtime bloat.
Use its existing locked browserless test recipe rather than downloading engines
for offline utilities. Existing unpinned `npm i --no-save` examples (such as
xprobe optional parsers) are **not a no-upgrade reproducible recipe**; resolve
that gap in a later selected change instead of running them here.

There is no universal apt list. LibreOffice/UNO, browser libraries/fonts,
encoders and numerical BLAS/LAPACK belong only to the selected feature. The
[frozen native profile](runtime-profiles.md) is deliberately Debian 13/amd64,
glibc/LP64; no apt closure, hashes or ABI were verified by this inventory.
`--no-install-recommends` still includes mandatory dependencies. Do not copy
one shared object, assume musl/ILP64 compatibility, or remove OS packages needed
by another consumer.

A feature that does need apt packages can keep them in plain-text list files
(one group per file, one `name` or `name=version` per line, whole-line `#`
comments) and apply them with `tools/nano_apt.py`; a Dockerfile can read the same
files with `grep -v '^#' FILE | xargs apt-get install -y --no-install-recommends`.

```sh
python -S tools/nano_apt.py --list apt-runtime.txt [--list apt-test.txt] [--install] \
  [--receipt receipt.json] [--log apt.log]
```

Without `--install` it only reports (exit 1 when something is missing). With it,
`apt-get update` and `install` run only for missing or version-mismatched
packages, then the result is re-checked with `dpkg-query`; nothing runs when
all are present or when `apt-get`/`dpkg-query` are absent (reported as
`apt_unavailable`). It does not choose packages, resolve closures or pin hashes,
so the Debian 13/amd64 caveats above still apply. No CI step or child rollout is added.

`--receipt FILE` writes the same JSON report to a new file (installed versions
before and after, list SHA-256, OS id/version, seconds). `--log FILE` keeps
apt's own output in a new file and prints only its summary line, plus the last
lines on failure. Existing files are never overwritten (exit 2 before anything runs).
apt runs with bounded retries and timeouts given per call by `-o`
(`Acquire::Retries=1`, `Acquire::http/https::Timeout=15`, `APT::Update::Error-Mode=any`),
so `/etc/apt` is not written. These are per-file limits, not a total deadline; keep
the step's own `timeout-minutes`. Strace-based audits, cgroup measurement and
service start-up stay in `tools/constrained_lab_*.py`, not here.

To overlap the download with slower setup, start it first and join it later:

```sh
python -S tools/nano_apt.py --list apt-runtime.txt --background --state apt.state  # returns at once
uv sync && npm ci                                                                  # other preparation
python -S tools/nano_apt.py --list apt-runtime.txt --install --state apt.state --receipt receipt.json
```

`--background` detaches this tool as `--download-only` (`apt-get update`, then
`install --download-only` for what is missing) and writes `apt.state`,
`apt.state.pid` and `apt.state.log`; all three are reserved as new files before
spawning, never overwriting pre-existing outputs. Use a directory you own and
control: this is collision protection, not protection against another writer
replacing directory entries during state updates or failure cleanup.
`--install --state` waits for it (up to 600 s, and not at all when the process is
already gone), then skips `update` only if the download finished and the missing
packages and list SHA-256 are identical; any other outcome runs the normal
`update` + `install`. The receipt records this under `joined`. `--download-only`
also works on its own and does not install, so its `packages` stay missing.
apt still takes one lock at a time, so only work that does not call apt overlaps.

Measured once on an Ubuntu 24.04 container as root (4 small packages, 10 s of
simulated other work, empty apt lists and cache): 17.7 s serial against 12.1 s
overlapped, because the 6.6 s update + download hid behind the other work.
With only 1 s of other work the overlapped run was slower in both repeats
(9.7 s against 7.1 s, 9.9 s against 9.2 s): the split costs a second apt
dependency pass, roughly 1 s. The gain is bounded by the shorter of the
download and the other work; use it when the other work is longer. Not run on
GitHub Actions, so a process surviving between steps there is unverified.

Host Python/Node processes already support many source-only features without
Docker. That does not replace Docker isolation or the real products used by
integration tests. Portfolio's refreshed `6a7efcd1` [import Dockerfile](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/6a7efcd1d3ec6775aa181fdd1b81f133e5537da9/Dockerfile.html-import) says Node22
while its [manifest](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/6a7efcd1d3ec6775aa181fdd1b81f133e5537da9/package.json) requires Node24+; its [host builder](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/6a7efcd1d3ec6775aa181fdd1b81f133e5537da9/tools/build_html_import.py) remains separate. That source mismatch is recorded, not
fixed or treated as a validated recipe. No Docker/browser/model was run here.

## Parallel workers and measurements

Keep immutable source/dependency versions separate from each worker's writable
outputs, temp directories, ports, logs and input copies. Do not concurrently run
`npm ci` or an exact `uv sync` into a shared environment: sync can remove
packages another worker needs. Reuse prepared environments only for compatible
read-only workloads, and include source-specific mutable caches in the review.
A venv is package separation, not container-equivalent security isolation.

The JSON's `measurements` is a **null before/after template**, not zero cost.
For each later selected feature record: host OS/arch/runtime/tool versions,
source/lock/input hashes, exact command, cold/warm cache state, repetitions,
install/download/installed bytes, node_modules bytes, locked package-entry
definition, build time/artifact bytes, startup time, per-worker RSS, worker
count and aggregate peak RSS. Include setup, orchestration, failure and cleanup
costs. Compare identical inputs and full outputs/errors; retain raw logs and
the measured regression, even if the candidate is worse. Shared-page memory
and cached downloads must not be counted as independent-worker savings.

At the 2026-10-31 inventory stage, neither install/build savings nor concurrent
throughput had been measured. The five selected features in [DRA/Ironmate](nano-feature-environments.md),
[MCP/BTK](nano-client-mirror-environments.md) and
[Markdown Gradio](nano-gradio-environment.md) now record actual dependency bytes, install samples and fixed-volume parallel
workloads separately; the historical JSON null template is not overwritten.
All test matrices, browser engines, independent oracles and error cases stay.
**No cross-engine comparison is surrendered by this PR**; an old Ironmate
instruction is not a new exception for this work.

## Offline verification

```sh
python -S -m unittest discover -s tests -p 'test_nano_inventory.py' -v
python -S -m unittest discover -s tests -p 'test_nano_export.py' -v
python -S -m unittest discover -s tests -p 'test_nano_dragowing.py' -v
python -S -m unittest discover -s tests -p 'test_nano_node.py' -v
python -S -m unittest discover -s tests -p 'test_nano_mcp_client.py' -v
python -S -m unittest discover -s tests -p 'test_nano_btk.py' -v
python -S -m unittest discover -s tests -p 'test_nano_markdown_gradio.py' -v
python -S -m unittest discover -s tests -p 'test_nano_apt.py' -v
python -S -m unittest discover -s tests -p 'test_runtime_plan.py' -v
python -S tests/stdlib_runtime_smoke.py
```

The inventory checks validate public-scope metadata, immutable source-link shape
and absence of invented execution evidence. The preparer tests use synthetic
inputs/fake uv/npm to check failure cleanup, exact selection, input mutation and
parallel output independence. These suites do not fetch sources or execute the
listed features. Separate evidence records real offline exports and the five
explicitly selected install/workload comparisons. The existing Python workflow
discovers the added tests; no workflow is modified.
