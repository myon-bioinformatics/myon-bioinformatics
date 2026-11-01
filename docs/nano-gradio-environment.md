# Markdown Gradio: a real frontend, separately installed

This 2026-11-01 follow-up selects the existing **text-input Gradio API** feature,
not the already-stdlib Markdown converter. It is the final measurement target
in this preparation wave. The [24-repository matrix](nano-environments.md#feature-screening)
still distinguishes selected features from genuinely required native/service
dependencies; it does not declare every repository minimized or adopted.

## Exact boundary and preparation

Source: [`markdown@f04d233d2edc63650439fbb7692434e9f2c66266`](https://github.com/myon-bioinformatics/markdown/tree/f04d233d2edc63650439fbb7692434e9f2c66266).
Its [frontend group](https://github.com/myon-bioinformatics/markdown/blob/f04d233d2edc63650439fbb7692434e9f2c66266/pyproject.toml)
and documented frontend requirements combine Gradio, Streamlit and Playwright.
The unchanged `demos/gradio_app.py` imports Gradio on its real UI path; it does
not need the other frontend or a browser engine to serve its API.

The existing parent `tools/nano_export.py` gains only the named
`markdown-gradio` selector. It verifies two fixed Git blobs, the inventory
identity, known group/lock structure and official-PyPI-only package sources.
Official uv exports locked/offline with no project or default dev groups,
selects `frontend`, then **prunes Streamlit and Playwright**. The complete
reviewed 56-row closure is checked, including both mutually exclusive NumPy
Python-version rows. This is not a new dependency resolver or Gradio substitute.

```sh
set -eu
OUT=$(mktemp -d "${TMPDIR:-/tmp}/nano-markdown-gradio-XXXXXXXX")
python3 -S tools/nano_export.py --feature markdown-gradio \
  --source /path/to/exact/markdown-manifest-lock --output-parent "$OUT"
```

Preparation installs nothing and starts no server. It uses an existing trusted
uv executable and Python 3.11+; no interpreter or package is automatically
downloaded. The source commit is provenance, not a claim that checkout HEAD or
all files were verified. Existing Aoi/DRA/MCP selectors and defaults are unchanged.

Both compared profiles exclude default dev groups equally. Baseline selects the
combined frontend group; feature adds the two prune options. The feature's
versions, markers and wheel hashes are a byte-preserved subset of the baseline.
Gradio 6.30.0, gradio-client 2.7.2, NumPy, Pandas, Pillow and other real transitive
requirements remain. This is still a substantial optional environment, not stdlib.

## Actual installation: one completed pair, not three

The planned order was full/Gradio, Gradio/full, full/Gradio with fresh caches.
**Planned 6, attempted 3, completed 2, failed 1, unstarted 3.** Trial 1 completed.
Trial 2 Gradio failed after 13.503627s when the official NumPy 2.5.4 Linux wheel
connection timed out. The series stopped without retry, alternate registry or
permission escalation. Its partial venv/cache and logs remain in the evidence.

| Observed trial 1 metric | Combined frontend | Gradio only |
|---|---:|---:|
| Exported requirement rows / distinct names | 77 / 75 | 56 / 55 |
| Actual Linux installed distributions | 72 | 52 |
| Venv apparent regular-file bytes | 642,781,043 | 239,130,663 |
| Venv allocated regular-file bytes | 667,291,648 | 257,626,112 |
| Package installation seconds | 17.575416 | 10.444845 |
| Venv creation seconds, separate | 0.064119 | 0.064024 |

These are **one successful matched installation sample per profile**, not
three-sample medians or proof of general speed improvement. Export rows include
platform/Python markers and are not installed counts. Venv bytes exclude shared
interpreter/stdlib, source, caches and symlink targets. uv uses copy mode; cache
bytes are separate, not network transfer. Filesystem deduplication and remote
CDN/OS cache state are unknown. No source builds, dependency upgrades or browser
downloads occurred.

## Real local API and unchanged tests

After the registry failure, the complete matched **trial 1** was explicitly
selected for bounded local-only validation. No package installation was retried.
The unchanged app runs a real Gradio queue and server in each worker. Three
synthetic text fixtures (rich links/images, empty, Unicode/code) pass through
both raw HTTP/SSE and the real Gradio client. All four callback output fields
are compared exactly against independent expected facts and across profiles.
Opaque session/job IDs are retained separately, not normalized into output proof.

- **12 batches / 24 API workers / 144 complete responses:** all matched.
- Four unique unchanged original API tests ran in each profile: **8 executions,
  0 skips**, including **8 original curl invocations**. This is not eight unique
  tests or the full child suite. curl 8.14.1 and separately accounted existing
  pytest/iniconfig/pluggy files were reused; no test-tool installation was added.
- **952 persisted audit events, 0 denials** in this bounded run; all 26 worker
  processes exited successfully and owned process groups were reaped.
- The tested upload argument is always `None`. File uploads, browser rendering,
  all widget behaviors, Streamlit and visual/CSP parity are **not** established.
  Every existing frontend/browser test remains untouched in its full lane.

Each batch performs exactly two jobs, at concurrency one or two, with three
repetitions per profile. Each job has private HOME, TMPDIR, cache, HF/Gradio
directories, reports and a local server. Dependencies/source are used read-only.

| Profile | Median fixed-two-job batch, concurrency 1 | Concurrency 2 |
|---|---:|---:|
| Combined frontend | 10.270s | 5.556s |
| Gradio only | 9.901s | 5.074s |

Timing includes starts, imports, server launch, API calls, shutdown and receipts;
it is not pure Markdown conversion time. Both profiles already parallelize.
Small runtime samples from one matched installation pair do not establish a
general scaling or build-time benefit.

## Isolation, limits and reproducibility

Install caches/HOME/config/tmp are private per attempt. During workloads,
provider/proxy environment settings are removed, telemetry is disabled before
imports, and persisted Python audit hooks restrict sockets to owned loopback
ports and child processes to the exact original curl requests. These are bounded
defense-in-depth checks, **not OS/container security isolation**. No external API,
model, browser, Docker or Office workload was run.

Observed host: Linux x86_64/glibc 2.41, CPython 3.12.14, uv 0.13.4. Before the run,
the host reported AMD EPYC 9V74, 9 visible/affinity CPUs and MemTotal 10,206,472 KiB;
effective CPU/memory quotas were unavailable. These observations are not promised
resource allocations. RSS is sampled every 20ms; summed RSS can
double-count shared pages and miss peaks. Worker high-water precedes final
receipt serialization and is not PSS or final lifetime peak.

- [Results and retained failure](evidence/nano-features/2026-11-01-markdown-gradio/RESULTS.md)
- [Numeric evidence](evidence/nano-features/2026-11-01-markdown-gradio/evidence.json)
- [Exact source, hashes and gated reproduction](evidence/nano-features/2026-11-01-markdown-gradio/REPRODUCE.md)

The compact bundle keeps all attempted install samples, every runtime batch,
worker identities and one full canonical output. Large installed environments,
child source copies and test-tool packages are not committed.

## Reuse and the next stage

Read-only comparison with [`web-ui@fe83ddd`](https://github.com/myon-bioinformatics/web-ui/blob/fe83ddd2913d4cff6bc506886247189e314e0d71/uv.lock)
found 64→57 rows for its Gradio-group selection, excluding default dev groups
equally. All 56 Markdown version/wheel-hash records occur there, but platform
markers differ and web-ui additionally retains an emscripten `httpx2-jsfetch`
path. This suggests native-host package reuse; it is **not byte-identical export,
cross-platform equivalence or executed web-ui validation**. No web-ui install
or workload was performed.

Stop adding speculative feature benchmarks after this wave. The next decision
is separately authorized child adoption of the reviewed opt-in preparations:
fixed parent revision, exact child inputs, explicit install/run instructions,
preserved full tests and selected acceptance evidence. Aoi's existing PR is not
duplicated. Parent/child merges, new child PRs and vendor rollout are not performed
by this measurement update. No application/runtime files or lines are deleted,
no application/library replacement is introduced, and no CI/Pages setting changes.
