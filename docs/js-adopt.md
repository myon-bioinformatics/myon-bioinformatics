# js-adopt: third-party browser JS from public GitHub files

Common identifier: `js-adopt`. Tool: [`js_adopt.py`](../js_adopt.py) (stdlib, `python -S`).

## Why not npm or git

A built file that a project commits to its own public repository (for example
`dist/purify.min.js` in cure53/DOMPurify) is one HTTPS GET away:
`https://raw.githubusercontent.com/OWNER/REPO/REF/PATH`. curl, browser `fetch` and Python
`urllib` all reach it, it needs no package manager, and it works where the npm registry or
a CDN is blocked. `vendor_sync.py` stays the tool for files shared between this
organization's repositories (git blob provenance); `js_adopt.py` is for third-party JS
that a page loads, where the browser's SRI check matters.

## What the lock records (`js.lock.json`, `js-adopt/1`)

| Field | Meaning |
| --- | --- |
| `repository`, `ref`, `path`, `url` | where the bytes came from; `ref` is a tag or a full commit SHA |
| `destination`, `bytes` | the local copy |
| `sha256` | what pins the bytes; a tag can move, so `check --remote` re-downloads and compares |
| `sri` | `sha384-…` for `<script integrity>` and `fetch(url, {integrity})`; the same value works for the local copy and jsDelivr's GitHub mirror |
| `license` | SPDX expression as stated by the adopter; `OR` needs one allowed branch, `AND` needs all |

Allowed without review: MIT, ISC, BSD-2/3-Clause, Apache-2.0, MPL-2.0, 0BSD, Unlicense,
CC0-1.0. Anything else (GPL, LGPL such as libheif, unknown) is refused unless
`--allow-license` records `license_reviewed: true`.

## Usage

```bash
python -S js_adopt.py add cure53/DOMPurify 3.4.17 dist/purify.min.js --dest vendor/js --license "MPL-2.0 OR Apache-2.0"
python -S js_adopt.py check [--remote]
python -S js_adopt.py tag purify [--cdn] [--base ../]
python -S js_adopt.py oneliners purify      # bounded curl / browser fetch + Web Crypto SRI / urllib
```

Downloads and all generated examples have a 30 s **total network deadline**
(including DNS, headers and a trickling body) and a 10,000,000-byte cap
(`fetch-guard`). Exact-limit bodies are accepted; one extra byte is rejected.
The Python network worker is killed at the deadline. Generated shell examples
require Python 3 (the curl example also requires curl), stream through a bounded
supervisor, verify SHA-256, then atomically replace the destination. Failures keep
any existing destination intact. The destination directory must already exist.
The displayed shell commands use POSIX quoting and contain embedded newlines;
copy each complete command.

The browser example streams with AbortController and a deadline race, then verifies
SHA-384 SRI with Web Crypto before returning text. It deliberately avoids fetch's
`integrity` option, which can buffer the body before exposing a stream. It requires
Web Crypto in a secure context and CORS permission from the source.

Supported license grammar: license identifiers, parentheses, `AND`, and `OR`;
`AND` binds tighter than `OR`. Every branch must parse. Unsupported syntax
(including `WITH`, legacy `+`, and LicenseRef/DocumentRef), malformed expressions,
and expressions over 4096 characters or 256 tokens are rejected even with
`--allow-license`. That flag only reviews a syntactically supported expression
containing licenses outside the allow list. The adopter remains responsible for
checking the actual license text.

Exit codes: 0 OK; 1
mismatch or license not allowed; 2 input error.

## Notes

- `add` writes `<destination> -text` to `.gitattributes`, and `check` fails without such a rule
  (an exact path or a `dir/**` pattern). Windows runners check out with `core.autocrlf=true`; the
  CRLF bytes no longer match the SRI hash and every engine refuses the file (seen in
  browser-test-kit platform-matrix: all 14 Windows environments failed, Linux and macOS passed).

- Some minified files drop the license header (seedrandom 3.0.5 keeps its MIT notice only
  in `seedrandom.js` and the README). When the notice must travel with the file, adopt the
  unminified file or also adopt the LICENSE file under the same `--dest`.
- Many projects do not commit built files (axe-core, KaTeX, Leaflet build on publish).
  Those need a GitHub release asset or the npm tarball; that path is not implemented yet.
- Loading and behaviour in every engine are checked by the consumer, for example
  browser-test-kit's platform-matrix: a page loads the adopted files with `integrity`, and a
  tampered copy must be refused.
