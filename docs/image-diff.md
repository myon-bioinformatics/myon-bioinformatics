# image_diff.py

Compare two screenshots (`PATH` or `ZIP::member`) and print one line: size, changed pixels, bbox, max channel delta.

Ported from [browser-test-kit #66](https://github.com/myon-bioinformatics/browser-test-kit/pull/66). Lives here as a parent-level optional tool (Pillow), not as a vendored stdlib module and not in gh_identity (stdlib-only).

```bash
pip install pillow   # optional at runtime; required for the regression tests
python scripts/image_diff.py a.png b.png [--clip X,Y,W,H] [--viewport-width CSS_PX] [--tolerance N]
```

Exit 0 = identical within tolerance, 1 = differs, 2 = input / missing Pillow.

## Regression tests and consumers

Install `tests/requirements.txt` and run `python -m pytest tests/test_image_diff.py`.
Pillow is a test dependency (including the Python 3.10–3.14 CI matrix); the test
module imports it directly so a missing dependency fails collection instead of
silently skipping the regressions. Runtime imports remain lazy, and the missing
Pillow exit-2 diagnostic is tested separately by blocking PIL imports.

Flutter PR #187 uses a thin CLI wrapper and a fixed-SHA copy of this file with
its LICENSE and vendor-lock/1 provenance. That direct reuse of an optional tool
does not enroll it in the stdlib-only vendor catalog. Keep the shared implementation
here; consumers should adopt the reviewed merge commit when this PR is merged.
