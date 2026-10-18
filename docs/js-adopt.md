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
python -S js_adopt.py oneliners purify      # curl + sha256sum, browser fetch with integrity, urllib
```

Downloads have a 30 s timeout and a 10 MB cap (`fetch-guard`). Exit codes: 0 OK; 1
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
