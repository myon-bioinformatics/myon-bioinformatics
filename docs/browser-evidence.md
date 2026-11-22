# Browser evidence: parent-owned contract (Issue #56)

The `Browser capture evidence` workflow captures the checked-out evidence page
with each SDK, waits for its saved records to render and publishes separate PNG
and manifest pairs. The Stagehand lane uses a local SDK browser session and
requires no model credentials. Run either engine locally after installing its
SDK under `tools/browser` and starting the page you want to capture.

```sh
node tools/browser/capture.mjs stagehand http://localhost:8000/evidence.html build/browser run-123 '#content .record'
```

Use a new output directory per run. The optional final argument waits for a
page-specific selector. Each engine publishes its subdirectory only after the PNG
validates. The manifest records the actual local checkout SHA.

- `browser_evidence.py` is stdlib-only, accepts one existing PNG screenshot
  produced by either engine, and records engine, run ID, checkout SHA, relative
  path, size and SHA-256 in `browser-screenshot-evidence/1`.
- Playwright and Stagehand run **independently**. Neither is a prerequisite for
  the other; no paired screenshots, visual equivalence or double-pass gate.
- Engine-specific dependencies, prompts and launch options remain separate.
- The shared contract owns metadata and safe root-relative evidence paths, not
  screenshot capture or visual assertions.
- Flutter/Dart/navigation behavior stays in the Flutter consumer.
- Do not delete consumer implementations until a parent engine runner exists,
  equivalent tests pass and the actual screenshot artifacts are inspected.

Example (after an engine produced `build/browser/shot.png`):

```sh
python -S browser_evidence.py --engine playwright --root build/browser \
  --screenshot build/browser/shot.png --run-id 123 --head-sha "$GITHUB_SHA" \
  --output build/browser/evidence.json
```
