# Browser evidence: parent-owned contract (Issue #56)

This is the first **contract-only** increment. It does not yet install or run
Playwright or Stagehand. Engine execution adapters and real-browser CI are
separate follow-ups, and must be verified before consumer deletion.

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
