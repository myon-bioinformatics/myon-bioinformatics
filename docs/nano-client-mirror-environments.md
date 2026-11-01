# MCP client and BTK document-mirror environments

This **2026-11-01** follow-up implements two further package-bearing feature
boundaries in the same parent PR. The [24-repository coverage matrix](nano-environments.md#feature-screening)
separates already-minimal cores, selected environments and remaining real
native/service dependencies. It does **not** mark every repository or feature
complete. [DRA HTML / Ironmate PPTX](nano-feature-environments.md) remain separate
earlier measurements; Aoi's 20→12 exported rows are still not installed counts.

## Explicit preparation, unchanged real implementations

| Selected feature | Exact child source | What remains |
|---|---|---|
| MCP lab SDK chat client | [`06cdc8e4e14d50de45c8b8122a5ccff6911c3404`](https://github.com/myon-bioinformatics/mcp-toolcall-lab/tree/06cdc8e4e14d50de45c8b8122a5ccff6911c3404) | Original `chat_sim.send_direct` / `send_via_chat`, real MCP 2.3.0 and Pydantic 2.14.1 closure. A separately hosted FastMCP server is still required. |
| BTK Markdown/HTML mirror checks | [`111f6d78befae28acfb198a45b72e5aa0e59dcde`](https://github.com/myon-bioinformatics/browser-test-kit/tree/111f6d78befae28acfb198a45b72e5aa0e59dcde) | Original fact readers/comparator, **both** markdown-it 15.0.3 and marked 18.1.1, linkedom 0.18.13, existing Playwright **test runner** 1.64.1; no browser engine needed by these cases. |

MCP's source HEAD was unchanged from the inventory. BTK's relevant source/lock
was refreshed for this feature only; the historical inventory is not rewritten
as a new census. Existing versions, source files, full test suites and child
manifests remain unchanged. These are parent-owned opt-in preparations, not
child adoption, a library replacement, or a general dependency resolver.

```sh
set -eu
OUT=$(mktemp -d "${TMPDIR:-/tmp}/nano-client-mirrors-XXXXXXXX")
mkdir -p "$OUT/mcp" "$OUT/btk"
python3 -S tools/nano_export.py --feature mcp-chat-client \
  --source /path/to/exact/MCP-manifest-lock --output-parent "$OUT/mcp"
python3 -S tools/nano_node.py --feature browser-test-kit-document-mirrors \
  --source /path/to/exact/BTK-nine-file-snapshot --output-dir "$OUT/btk"
```

These commands install nothing and start no server, model, browser or Docker.
Use an existing Python 3.12+ for MCP's project constraint and existing Node 24+
for BTK. The measured tools were uv 0.13.4 and npm 11.9.0. No tool/runtime is
automatically installed or upgraded. Each output is private and unique; failures
remove that invocation's partial preparation. Existing Aoi and Ironmate default
APIs are unchanged. Ironmate's default projected manifest/lock digests remain
identical; its historical reproduction now explicitly selects its prior parent
checkout rather than pretending the extended helper has the old measured hash.

### MCP boundary

The preparer verifies just the fixed `pyproject.toml` / `uv.lock` Git blobs,
matching the existing public inventory. It exports locked/offline with no extras,
no default groups, no project install and **`--prune fastmcp`**. Official uv removes
newly unneeded dependencies; the exact reviewed 30-name closure is checked.
This is not merely hiding a root requirement or inventing an SDK substitute.

The retained SDK closure still includes server-capable libraries such as uvicorn,
Starlette/SSE and crypto/native packages. They are real transitive requirements,
not arbitrarily removable because the selected caller is small. The baseline
is the **default server runtime**, not all Galleria/test/browser/model extras.
The source's lazy FastMCP import permits the client path, but the intact package
initializer also imports catalog/Wikipedia/Pixiv helper modules; the measurement
stages their exact bytes, not a fake replacement initializer.

### BTK boundary

The preparer verifies nine source/fixture/license/manifest blobs, then uses
official npm's offline lock-only projection to remove only the unrelated root
ExcelJS selection in a private copy. Both independent Markdown engines and the
existing test runner remain. All 30 retained non-root records must match their
original complete records, including versions, nested paths and integrity.
Existing overrides remain; only known root metadata changes are permitted.

linkedom's **exact optional canvas peer** metadata is permitted, while canvas
itself is absent from the reviewed closure. Unknown, required, changed or extra
peer metadata fails; this is not an allow-all peer-resolution policy. The 27-row
reader-only projection was checked offline, but the **30-record test-enabled**
environment is the installed/measured feature. Do not substitute the smaller
number for its measured package count or bytes.

The separate markdown repository's [pinned mirror lock](https://github.com/myon-bioinformatics/markdown/blob/f04d233d2edc63650439fbb7692434e9f2c66266/tests/mirror/package-lock.json) shares those 27 runtime records
unchanged, but its five additional width/TOML records are purposeful. This is
reuse evidence, not permission to delete its oracles or claim its whole suite
ran in the BTK environment. BTK's XLSX, browser, platform and headed tests stay
in their existing full environments.

## Actual setup observations

Six successful fresh-cache installs per feature, in baseline/feature,
feature/baseline, baseline/feature order. No retries, alternate registries,
dependency upgrades, source builds or browser downloads. Package-manager timers
exclude source staging and offline preparation; Python venv creation is separate.

| Metric | MCP default caller | MCP SDK caller | BTK full Node closure | BTK document mirrors |
|---|---:|---:|---:|---:|
| Export rows / lock records (not installed counts) | 72 | 30 | 221 | 30 |
| Actual Linux distributions / installed Node paths | 69 | 28 | 221 | 30 |
| Apparent regular-file dependency bytes | 58,981,931 | 30,223,954 | 61,017,247 | 25,424,737 |
| Median install seconds, n=3 | 9.848 | 7.994 | 8.343 | 7.253 |

Python distributions and nested Node package paths are different units. Python
bytes cover venv regular files; Node bytes cover `node_modules` regular files.
Both exclude shared runtimes/stdlib, source, caches and symlink targets. Allocated
bytes and retained cache bytes are recorded separately; neither is compressed
network transfer size. Python uses copy mode, not intentional cache hardlinks.
Filesystem deduplication and OS/DNS/CDN cache state are unknown. One BTK feature
install took **14.984s**, slower than its paired **9.687s** baseline; it is retained.
Three observations do not establish general installation-speed improvements.

**MCP co-hosting counterexample:** a separate full fixture server still needs
69 distributions and 58,981,931 bytes. Full server plus thin caller environments
total **89,205,885 bytes**, compared with **58,981,931** for one shared full
environment. The smaller caller is useful as a separately selected deployment
boundary; it is **not whole-host or whole-service disk savings**. Package
separation does not require one duplicate venv per parallel worker.

## Outputs, unchanged tests and parallelism

MCP exercised eight synthetic success/empty/error cases through both unchanged
client functions, using only deterministic local `find_*` mock tools. Real
MCP sessions/serialization were used, not a simulated SDK. Independent expected
facts checked fictional Exampleville/code90001, station, transaction price/year,
empty success and errors. All complete SDK result/chat JSON matched across
**12 batches, 24 clients and 384 local calls**. The one canonical output and
each worker's digest are retained. Twenty-one preparation/lifecycle fake tests
and ten original catalog tests passed; broader integration tests were not run
or modified. Their separate full-server/test-extra requirements remain.

BTK compared complete fact JSON and exact comparator errors for Unicode,
escaped links, code, raw HTML, ordinary links and intentional CommonMark/GFM
bare-URL disagreement. It does **not** force independent readers to agree when
their supported dialects differ. All **12 batches / 24 jobs** passed. Each job
ran the same **two unique original Node tests**, for 48 test executions total,
with zero skips—not 48 distinct tests or the full suite. After timing, the
unchanged Python comparator file passed **54 tests**, using an existing declared
pytest environment outside both Node size/time figures; no extra installation.

Exactly two jobs were performed in every batch, with three repetitions per
profile/concurrency. Environment/source inputs were shared read-only. BTK used
private HOME/cache/temp paths and reports per worker. MCP used distinct process
receipts/logs but shared one scratch HOME and inherited TMPDIR across its fixture
server and clients. Its bounded successful cases do not establish general
collision-freedom or per-worker filesystem isolation.

| Profile | Median two-job batch, 1 worker | Median two-job batch, 2 workers |
|---|---:|---:|
| MCP default caller | 3.1483s | 1.9989s |
| MCP SDK caller | 3.1057s | 1.9110s |
| BTK full Node closure | 8.810s | 4.758s |
| BTK document mirrors | 8.886s | 4.753s |

Both original environments already parallelized. These small samples show no
general serial-runtime improvement from removing unused installed packages.
MCP caller timing includes startup/imports/calls/receipt writing, with fixture
startup recorded separately. BTK timing includes process startup, source staging,
all fact/comparator invocations and the selected test runner—not pure parser time.
No Docker-image/build-time, browser-rendering or optimal-worker-count claim follows.

## Safety, process accounting and reproducibility

MCP binds a dynamic loopback port and permits only local mock calls. Worker
environments remove proxies/provider keys/model flags; audit hooks block outgoing
server connections and non-loopback client operations. They are defense in depth,
not an OS sandbox. All 24 clients exited 0. Twelve idle fixture servers were
intentionally SIGTERM-terminated/reaped after their jobs, returning -15; an ad-hoc
checker initially expected 0 and was corrected without any measurement rerun.
Client denied-event lists were empty. The server's in-memory denied-attempt count
was not persisted and may not survive SIGTERM, so that count is **unknown**, not
inferred as zero from logs. No live Wikipedia/Pixiv/GitHub/model call was made.

BTK uses the installed test CLI, not auto-fetching npx. It neither downloads nor
launches browsers; the private browser directory remains empty. All original
test files and their full-suite counterparts are retained. There was no BTK
fixture failure/correction in this measurement series.

Host: CPython 3.12.14, Node 24.22.1, Linux 6.18.57 x86_64 / glibc 2.41. A pre-pair
observation recorded AMD EPYC 9V74, 9 visible/affinity CPUs and MemTotal
10,206,472 KiB; effective quotas were unavailable, not guaranteed exclusive
resources. MCP samples caller/server/combined RSS every 20ms; sums can double-count
shared pages and miss peaks, and client high-water is observed before receipt
packaging. These are not PSS or enforced resource limits. BTK RSS was not measured.

- [MCP results and limits](evidence/nano-features/2026-11-01-mcp-client/RESULTS.md),
  [all numeric evidence](evidence/nano-features/2026-11-01-mcp-client/evidence.json),
  [exact source staging and gated commands](evidence/nano-features/2026-11-01-mcp-client/REPRODUCE.md)
- [BTK evidence](evidence/nano-features/2026-11-01-browser-test-kit/evidence.json),
  [exact source staging and gated commands](evidence/nano-features/2026-11-01-browser-test-kit/REPRODUCE.md)

The compact bundles retain all install/batch samples, source/tool hashes,
per-worker hashes and complete canonical output once. Large source copies,
venvs, node_modules, generated output trees and raw local logs are not committed;
their receipts/digests and reproduction steps are preserved. A public runner
with a configured local uv path has a new hash, not the historical measured hash.

Parent profiles/planner, child source/dependencies, CI/Pages and vendor pins are
unchanged. Application/runtime files or lines deleted: **0**; self-written
library-replacement functions: **0**; tests deleted: **0**. No cross-engine
exception is taken. Full child/visual/model suites remain outside this bounded
proof, and the PR remains **unmerged**.
