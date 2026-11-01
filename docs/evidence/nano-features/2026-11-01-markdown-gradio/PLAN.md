# markdown Gradio: source-bound local API comparison

This experiment is limited to **text inputs, with uploaded_path=None**. It does
not prove uploads, browser widgets, visual rendering or the rest of the child
repository. No child source/test bytes or dependency versions are changed.

## Source and dependency boundary

Repository `myon-bioinformatics/markdown`, revision
`f04d233d2edc63650439fbb7692434e9f2c66266`:

- `markdown.py` Git blob `9294cabc0d72f457cc2a563812bc31f5848167b4`,274,045bytes.
- `demos/gradio_app.py` Git blob `4213a952a450ccc05cf081ee60a270c81a632aba`,2,373bytes.
- Exact pyproject blob `031f0def5bebc211a37b85d6ab769ac27721f86e` and
  lock blob `39599c712c22eddc9b2d42bd0f0a13cab90b7f38`.
- The four existing tests in `tests/frontend/test_gradio_curl.py` and
  `test_gradio_ui.py`, plus their original conftest, are staged unchanged.

Both exports select only the `frontend` dependency group, no default dev group,
no project build/install. Full baseline77 marker rows/75 distinct names; feature
prunes Streamlit and Playwright,56 rows/55 names. Actual installed counts must
come from each venv's distribution metadata, not these export counts. Gradio,
gradio-client, NumPy, Pandas, Pillow and the remaining closure stay real packages.

The worker calls the original `build_app()`, `queue()`, `launch()`, and real
Gradio routes using HTTP job submission/SSE and `gradio_client.predict`. It does
not replace the SDK/server or substitute direct `analyze()` calls as evidence.
Both routes return all four callback fields. Independent fixed fixture values
check headings, links/images, summary counts/URLs and empty/Unicode-code inputs.
Opaque event/session IDs and raw wire bodies are retained but are not content
parity fields. Exact complete callback data, including JSON-string whitespace,
must agree across environments.

## Isolation and execution design

Six planned fresh-cache hash-locked binary installs,AB/BA/AB; no upgrades/builds,
no interpreter download, no other registry/fallback. Installer HOME/config/tmp
are trial-private and prepared before timing. First install/network failure stops
all later attempts; incomplete pairs are never silently called trial3.

Runtime comparisons:3 repetitions ×1/2 workers ×full/Gradio =12 planned batches.
Each batch runs exactly2 identical fixed jobs. Every job owns one real local app,
private HOME/TMPDIR/cache/config/HF cache/Gradio temp/output and a dynamic loopback
port. Each executes3 text fixtures through2 API routes =6 callback calls.
Timing includes process/import/app startup, real queue/API, cleanup and receipt
writing; there is no omitted external server environment. Runtime byte counts,
source, private generated state, retained package caches and test tools are
separate costs.

Before package imports: inherited proxy/provider/model/share variables are
removed; Gradio analytics and SSR disabled, HF telemetry disabled/offline,
DO_NOT_TRACK enabled. Incrementally persisted audit records reject non-loopback
or unregistered-port connects, external DNS, subprocesses and public binds.
Only the two exact original curl command shapes are allowed: registered local
POST/GET,no redirects, no file input/options, known test payloads. Python's socket
audit cannot inspect curl's sockets; that narrow argv/environment boundary is
therefore explicit. No browser/Docker/model or external-source call is authorized.

Workers own process groups. Partial-launch errors/timeouts and already-exited
leaders still trigger group cleanup, with bounded SIGKILL fallback. Client close
errors cannot skip app close; the app closes before waiting on persistent SSE
executors. No port-collision retry silently changes a trial.

## Original tests and accounting

All4 original no-browser tests must pass in **each** selected environment,zero
skips. They exercise2 curl and2 gradio-client cases. Their optional browser fixture
stays untouched but is not invoked. They run after timed comparisons, not within
runtime timing. Process-group limits cover their original client lifecycle.

Three matching existing test-only distributions are reused,not installed into
either runtime venv: pytest9.1.1,iniconfig2.3.1,pluggy1.7.0.121 copied files,
1,454,604bytes, individually checked against installed RECORD SHA-256. Packaging
26.3 and Pygments2.21.0 already belong to both fixed runtime closures. Only these
three test-tool modules/metadata are exposed—not an old environment's entire
site-packages. Test-tool receipt and host curl8.14.1 identity are separate.

All source/export/tool/harness identities are checked before every installation
and around every batch,then after completion. Source and test files remain
unchanged. Original broader frontend/browser tests remain in their repository;
this selected proof is not a whole-suite pass or standard-library replacement.

## Interpretation limits

Venv/cache sizes count apparent/allocated regular files,not symlink targets or
shared Python/stdlib. Cache bytes are not download transfer. OS/DNS/CDN caches
are not reset. Sum-RSS is sampled20ms,can double-count shared pages and miss true
peaks; it is not PSS or an enforced quota. CPU/affinity/MemTotal are host
observations,not exclusive allocation. Few fixture trials establish neither a
general speed claim nor an optimal worker count. Every failure remains visible.
