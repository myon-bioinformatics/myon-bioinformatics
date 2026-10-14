# image_diff.py

Compare two screenshots (`PATH` or `ZIP::member`) and print one line: size, changed pixels, bbox, max channel delta.

Ported from [browser-test-kit #66](https://github.com/myon-bioinformatics/browser-test-kit/pull/66). Lives here as a parent-level optional tool (Pillow), not as a vendored stdlib module and not in gh_identity (stdlib-only).

```bash
pip install pillow   # optional; not in tests/requirements.txt
python scripts/image_diff.py a.png b.png [--clip X,Y,W,H] [--viewport-width CSS_PX] [--tolerance N]
```

Exit 0 = identical within tolerance, 1 = differs, 2 = input / missing Pillow.
