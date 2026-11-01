# DRAgoWing HTML-only environment comparison

One-host experiment, CPython 3.12.14, uv 0.13.4, pinned source and package
versions. No app source, tests, dependency versions or CI were changed.

## Installation

Planned: up to six fresh venv/cache installations, three alternating pairs.
Actual: five attempts, four successes (two complete pairs), third baseline
timed out connecting to the official PyPI Pygments endpoint, third feature not
started. No retry or alternate source was used. A preceding restricted-runtime-
path sandbox startup failure occurred before command execution and is recorded
separately, not as a package failure or timing trial.

| Successful installs, n=2 each | Default runtime + dev | HTML feature |
|---|---:|---:|
| Installed distributions | 9 | 1 (Markdown 3.11.2) |
| Apparent venv regular-file bytes | 254,997,482 | 374,693 |
| Allocated venv regular-file bytes | 259,055,616 | 483,328 |
| Retained cache apparent bytes | 257,180,839 | 396,654 |
| Install seconds, raw trials | 15.527826, 10.743102 | 15.204631, 17.833753 |
| Install seconds, median | 13.135464 | 16.519192 |

These venv sizes exclude the shared interpreter/stdlib and symlink targets.
No hardlinked regular files were observed; filesystem deduplication is unknown.
The cache footprint is not compressed transfer size. Fresh uv caches do not
reset OS/DNS/CDN cache state. **No faster-install claim is supported.**

## Offline outputs and parallelism

The explicitly selected successful install pair was **trial 2**. Full generated
`index.html` and `propositions.html` bytes, and canonical embedded figure JSON,
matched across default/native, default/stdlib and feature/stdlib for real c001
and two supported synthetic fixtures. An unsupported list-valued cell failed
explicitly. No fallback widened the lightweight backend's documented domain.

Real c001 outputs:

- index.html: 165,951 bytes; SHA-256
  `a56e5bce02f0b689b7b52fbeae527ee135eebd69fcac09e1732daacbfe6cb4b1`
- propositions.html: 2,061,809 bytes; SHA-256
  `40ff1a83be4164f97d2dc23686e679d41fcadc2d72ad00995ddf04b3fa315330`

Three repeated batches per configuration, two identical c001 tasks per batch:

| Environment/backend | Median batch seconds, 1 worker | Median batch seconds, 2 workers |
|---|---:|---:|
| default/native | 5.584030 | 2.737048 |
| default/stdlib | 4.281018 | 2.161636 |
| feature/stdlib | 4.519753 | 2.234204 |

All 36 timing tasks matched the same outputs. The same-algorithm bridge does
**not** show a speed advantage from removing unused installed packages. The
native/stdlib contrast also changes imports and the overlap algorithm.
Two-worker results are only for this small two-task workload, not a recommended
universal worker count. Native numerical thread settings were one per worker.

RSS receipts distinguish each worker's high-water RSS observed at CLI completion
from 10ms sampled sum-RSS. The latter can miss true peaks and double-counts shared
pages; neither is PSS or quota enforcement. Per-worker RSS was read before
receipt extraction, not at final process exit. Full raw values and limitations
are in `public-ready-evidence.json`; raw local logs and samples are unchanged.

## Tests and scope

Harness: 7 local tests passed. Selected unchanged native/stdlib parity, forms,
and HTML-tag tests: **141 passed** after providing the exact pinned Aoi layout
helper. The initial attempt (140 passed, one missing-helper setup failure) is
preserved. No test was edited or skipped to make the feature environment pass.

No browser, Docker, Office or model was started. Generated HTML references
Plotly's CDN but was not opened or fetched; this is not browser-rendering proof.
The full repository/browser test suite was not run. See `REPRODUCE.md` for exact
source/fixture staging and command boundaries. Public evidence preserves distinct
install- and workload-harness hashes; the runner's historical planned counts
must not be confused with the actual incomplete third pair above.
