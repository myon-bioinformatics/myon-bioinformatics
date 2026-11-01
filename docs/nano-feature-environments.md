# Measured feature environments: DRAgoWing HTML and Ironmate PPTX

Observed **2026-11-01**, on one Linux x86_64 host. These are two explicit
package-bearing feature boundaries, not another census of already-stdlib
cores. The [inventory and Aoi preparation guide](nano-environments.md) remains
the starting point. Its historical null measurements remain historical; Aoi's
**20 → 12 requirement rows** are still export metadata, not installed counts.

## What is implemented

The parent owns two opt-in preparation commands. Neither installs packages,
downloads source, starts workloads, refreshes source pins, or edits a child.
Each validates exact input Git blobs, stages only reviewed files in a unique
private directory outside the source, isolates tool configuration, and removes
partial output on failure. Receipts attest those files, **not checkout HEAD or
ancestry**. A different snapshot fails closed and needs a new explicit review.

| Feature | Fixed source | Selected dependency boundary |
|---|---|---|
| DRAgoWing complete HTML | [`be2185b142aebcbedee97688fe08ee0092179472`](https://github.com/myon-bioinformatics/DRAgoWing/tree/be2185b142aebcbedee97688fe08ee0092179472) | Existing `--overlap-backend stdlib`, plus Markdown 3.11.2. No NumPy/Polars or dev group in this environment. |
| Ironmate PPTX generation | [`af846ee7b1fd440b8379e2a682dfc7d40365b30e`](https://github.com/myon-bioinformatics/Ironmate/tree/af846ee7b1fd440b8379e2a682dfc7d40365b30e) | PptxGenJS 4.0.1 and its exact lock closure; no ExcelJS/XLSX closure. |

DRA's full HTML path still needs the pinned QueRyu source for registration
status; its native comparison additionally needs pinned PythDRagoras source.
The evidence recipe supplies both, plus fixed Aoi data and a test-only layout
helper. This is **not** a claim that every DRA mode works with Markdown alone.
Image/SVG export, embedded browser output, Plotly rendering, arbitrary input
types and the native backend are outside this feature environment.

Ironmate's slice stages five production render files, the spec helpers, two
unchanged focused tests and LICENSE, in addition to manifest/lock. It supports
the tested PPTX mapping/image path, not XLSX, preview, PDF, Office integration,
or the full application. The original repository still has all dependencies
and tests. No library API has been reimplemented to obtain these reductions.

## Prepare without installing

Use an existing Python 3.11+ interpreter, trusted uv/npm, and the exact source
snapshots above. The measured tool versions were uv 0.13.4 and npm 11.9.0;
the tools do not install or upgrade them. The DRA helper needs just its exact
`pyproject.toml` and `uv.lock`; Ironmate needs the 11 paths in
`tools/nano_node.py:SOURCE_BLOBS`. Output parents must be outside the sources.

```sh
set -eu
OUT=$(mktemp -d "${TMPDIR:-/tmp}/nano-features-XXXXXXXX")
mkdir -p "$OUT/dra" "$OUT/pptx"
python3 -S tools/nano_export.py --feature dragowing-html \
  --source /path/to/exact/DRAgoWing-inputs --output-parent "$OUT/dra"
python3 -S tools/nano_node.py \
  --source /path/to/exact/Ironmate-inputs --output-dir "$OUT/pptx"
```

Each command prints its completed directory. DRA writes `requirements.txt`
and a receipt; its locked/offline export uses `--no-default-groups`,
`--prune numpy --prune polars`, and `--no-emit-project`, and requires the sole
remaining requirement to be Markdown. It does not merely omit package rows
while leaving an unchecked transitive closure. Aoi remains the default
`nano_export.py` feature with its unchanged runtime-group semantics.

Ironmate uses **official npm's offline lock-only projection**, not a homemade
dependency resolver. The manifest removes only root `exceljs`; all 18 retained
non-root lock records must be byte-value identical to their original records,
including versions, registry URLs, integrity and nested paths. Both overrides
(`image-size` 2.0.4 and `uuid` 14.0.3) remain. The only extra permitted lock-root
changes are its name `workspace` → `Ironmate` and synchronization of the existing
Node engine. No `node_modules` may be created during preparation.

To reproduce **installation and output comparisons**, use the complete staged
recipes, not an unsourced universal install command:

- [DRA source/fixture staging and commands](evidence/nano-features/2026-11-01-dragowing/REPRODUCE.md)
- [Ironmate source staging and commands](evidence/nano-features/2026-11-01-ironmate/REPRODUCE.md)

Those separately gated benchmark scripts perform downloads only when explicitly
requested. They are evidence artifacts, not new CI jobs. Run each feature's
timed work exclusively; do not overlap installs or heavy tests. Workdirs and
venvs provide dependency/output separation, **not container isolation**, CPU/RAM
quotas or protection against hostile code.

## Actual installation results

All versions came from the existing child locks; no upgrade or source build
was used. DRA used hash-required binary wheels from official PyPI, uv copy mode,
and a fresh cache per trial. Node used official npm, fresh caches, `npm ci`,
scripts/audit/funding/update notifications disabled. No Docker was used.

| Metric | DRA default runtime + dev | DRA HTML | Ironmate both Node engines | Ironmate PPTX |
|---|---:|---:|---:|---:|
| Actual installed distributions / package paths | 9 | 1 | 198 | 18 |
| Dependency environment apparent regular-file bytes | 254,997,482 | 374,693 | 40,035,745 | 6,949,782 |
| Allocated regular-file bytes | 259,055,616 | 483,328 | 58,380,288 | 8,089,600 |
| Successful install samples | 2 | 2 | 3 | 3 |
| Median install seconds | 13.135464 | 16.519192 | 8.287560 | 4.626664 |

Python counts are installed distribution metadata; Node counts are installed
package **paths**, including nested copies, verified by npm. They are not
interchangeable units. Python bytes are the measured venv regular files, Node
bytes are `node_modules` regular files; both exclude shared runtimes/stdlib,
source, fixtures, cache and symlink targets. Allocated bytes depend on the
filesystem. No intentional Python cache hardlinks were observed; filesystem
deduplication is unknown. This is not a total machine/container image size.

Install wall times cover the package-manager subprocess, excluding source
staging/preparation; DRA records venv creation separately. Fresh tool caches do
not reset OS/DNS/CDN caches. Cache footprints are recorded separately and are
not compressed network transfer sizes. **DRA was not faster to install** in
these samples. Node times were variable, including a feature trial slower than
its paired baseline. These are observations, not a general speed guarantee.

### Failures are retained, not removed from the denominator

- DRA planned three alternating pairs, but the third baseline timed out at the
  official PyPI Pygments endpoint. It stopped without retry; the third feature
  was not started. There are **two complete pairs**, not three. Offline output
  work used the already successful matched **trial 2**, explicitly recorded.
  An earlier sandbox startup failure was nonexecuted infrastructure setup,
  separate from package trials; normal sandbox controls remained enforced.
- DRA's initial unchanged test subset had 140 passes and one missing-layout-
  helper failure in the partial source snapshot. Adding the exact pinned
  test-only Aoi helper, without modifying any test, produced 141 passes.
- Node completed all six installs. Its first image oracle caught worker-specific
  absolute input paths in default image descriptions. The independent fixture
  was corrected to supply fixed `altText`, rather than deleting arbitrary
  descriptions during comparison. An offline continuation reused the installs;
  original test files remained unchanged. Both failure and corrected-run
  identities are retained. The corrected fixture is labeled an adaptation.

## Output and small parallel comparisons

DRA compared **complete generated HTML bytes** and embedded JSON across
default/native, default/stdlib and Markdown-only/stdlib for real c001, a
257-row nullable/numeric/Unicode fixture, and a one-row fixture. All matched;
unsupported list-valued cells failed explicitly. No fallback broadened the
stdlib backend's domain. The selected unchanged offline tests passed **141/141**.
HTML still references Plotly's CDN; no browser opened or fetched it, so this is
not browser-rendering proof.

Node compared all ZIP member identities and parsed XML/binary digests, validated
content types/relationships and embedded PNG bytes, and exercised Unicode,
tables, shapes, z-order and line/triangle arrows. Only archive metadata and core
created/modified timestamp text were excluded. All 72 decks from 24 workers
matched structurally. This is not Office-rendered visual equality. Both original
focused test files passed **8/8 in each environment, zero skips**. The already
created DRA baseline interpreter supplied unchanged pinned pytest tools; that
shared test environment is excluded from **both** Node runtime measurements.

Each timing batch performed exactly **two jobs**, with three repeats at each
concurrency. The same-algorithm DRA bridge prevents attributing an algorithm
change to package removal:

| Configuration | Median batch seconds, 1 worker | Median batch seconds, 2 workers |
|---|---:|---:|
| DRA default/native | 5.584030 | 2.737048 |
| DRA default/stdlib | 4.281018 | 2.161636 |
| DRA HTML/stdlib | 4.519753 | 2.234204 |
| Ironmate full Node dependencies | 0.766209 | 0.422767 |
| Ironmate PPTX only | 0.772874 | 0.406629 |

Both full and small environments already supported parallel jobs. Removing
unused dependencies **did not demonstrate faster serial execution**. DRA's
native/stdlib comparison also changes imports/algorithm. Node timings include
process startup, generation, I/O and the structural oracle. DRA batch timings
include worker startup, CLI, receipt extraction and monitoring; its separate
CLI timings exclude receipt extraction. Two workers are not a recommended
universal worker count.

## Host, evidence and limitations

The measured runtimes were CPython 3.12.14, uv 0.13.4, Node 24.22.1 and npm
11.9.0 on Linux 6.18.57 x86_64 / glibc 2.41. Their installation cost is excluded.
DRA native numerical thread settings were one per worker. DRA records worker
high-water RSS **at CLI completion**, before receipt parsing; this is not final
process-lifetime peak. Its 10ms sampled sum-RSS can miss peaks and double-count
shared pages; it is not PSS or quota enforcement. Node did not measure RSS.
Host CPU capacity/RAM allocation were not recorded during timing. A read-only
**post-run** observation at
02:16:00Z reported AMD EPYC 9V74, 9 visible/affinity CPUs and `/proc/meminfo`
MemTotal 10,206,472 kB. Effective CPU/RAM quotas were unavailable and remain
unknown; the CPU model's physical core count is not an allocation guarantee.
Other host activity was not controlled, so no scaling/resource-budget guarantee
follows from this run.

- [DRA results](evidence/nano-features/2026-11-01-dragowing/PUBLIC-RESULTS.md),
  [raw numeric evidence](evidence/nano-features/2026-11-01-dragowing/public-ready-evidence.json),
  [bundle hashes](evidence/nano-features/2026-11-01-dragowing/MANIFEST.json)
- [Ironmate raw samples and evidence](evidence/nano-features/2026-11-01-ironmate/evidence.json),
  [bundle hashes](evidence/nano-features/2026-11-01-ironmate/manifest.json)

The compact bundles contain source pins, exact commands, small harnesses,
per-trial numbers, failure summaries and receipt digests. Large child snapshots,
fixtures, generated HTML/PPTX, venvs, caches and machine-local raw logs are not
committed. Public path placeholders are not runnable paths. Historical measured
and later public-runner hashes are distinguished where wording or fixture
corrections occurred; do not relabel a modified runner as the original one.

All original tests and independent oracles remain; **no cross-engine test
exception is taken**. Full child CI, browser/Office/model testing, cross-platform
native ABI parity and adversarial/security audits were not performed here.
The parent profiles/planner, child source/dependencies, vendor pins and CI/Pages
configuration are unchanged. Application/runtime files or lines deleted: **0**;
new self-written application/library replacement functions: **0**; tests deleted:
**0**. The additions are explicit preparation/measurement tooling and evidence,
not a claim that minpy/minjs can replace the libraries. This PR is not merged.
