# minpy (single-file Python bundler)

`tools/minpy.py` combines an entry file in the repository, plus the same-repository Python modules statically reachable from it, into one `.py`. It runs on the standard library only, and is fetched from the parent repository's `tools/minpy.py` under a fixed file name. The output can be fetched from a raw URL and vendored, just like the current repository-named files (e.g. `xprobe.py`).

It does not resolve dependencies or fetch from PyPI.

## Dependency policy

- The standard library stays external (it is not bundled).
- Regular packages (with `__init__.py`) and modules inside the repository are included. Both absolute and relative imports are handled.
- Third-party code is included only for top-level names on an explicit allowlist, and only as pure-Python source under `--third-party-root`. Artifacts built with allowlisted code are not standard-library-only, so they are not listed in the catalog as `<name>_min.py`.
- A non-standard library needed by the library itself (at module level, or inside functions used from anywhere other than the script entry point) is an error. Even if same-repository modules exist, no standard-library-only `<name>_min.py` is produced.
- Only **direct calls** to `importlib.import_module("name")` with a string literal, and to `import_module("name")` imported from importlib (or an alias of it), are followed as normal imports, and the callee is rewritten to the prefixed name. A self-defined `import_module`, or another object's `loader.import_module`, is not rewritten. Forms that take `importlib.import_module` out by assignment, argument passing, or `getattr(importlib, "import_module")`, and forms that only reference it after `from importlib import import_module` (such as `g = import_module`), are rejected. `from importlib import *`, `from pkgutil import *`, `from importlib.resources import *`, and `from pkg_resources import *` are also rejected. `import_module(name)` with a variable argument is left as plugin loading, and bundled modules are not loaded under their original names. The finder only answers for names under its own prefix.
- `__import__`, `exec`, `eval`, and `spec_from_file_location` are rejected.
- `__file__` is allowed only directly under `if __name__ == "__main__":`. Anywhere else it is treated as bundling data files and rejected. Imports of, attribute access to, and calls into `importlib.resources`, `importlib.import_module` (other than the direct calls above), `pkgutil.get_data`, and `pkg_resources` (including aliases and the string-literal forms `getattr(importlib, "resources")` / `getattr(importlib, "import_module")`) are also rejected. Referencing the module objects `importlib`, `pkgutil`, `pkg_resources`, or `importlib.resources` without taking an attribute (assignment, argument, list element, etc.) is rejected too. `importlib.metadata`, `importlib.util`, local variables named `resources` / `files` / `get_data` / `import_module`, and an `import_module` attribute on unrelated objects are not rejected. `getattr(module, name)` after binding to a variable, or attribute names built at runtime, may not be rejectable by static analysis.
- External imports that appear only inside the script entry point (that `if` and functions called only from it) are replaced with `ImportError` in the min file. The message points to the original file and its companion tool. Importing alone does not trigger it. Names listed in `__all__`, or functions of a module whose `__all__` cannot be read statically, are treated as library code. In the catalog, only when the import is truly script-only is the original file skipped with `requires_companion_tool`, with `<name>_min.py` as a separate entry.
- Imports from a child module to its parent package (`from . import helpers` or `from pkg import helpers`) are dependencies of package initialization and do not count as cycles. Cycles between siblings fail by default. Because parent references do not count as cycles, a bug that uses the parent's names before `__init__` finishes is not detected; that failure happens at runtime, just as with the original code.
- The following also stop with exit code 1 without writing a broken file:
  - C extensions (`.so` / `.pyd` / `.dylib`)
  - Namespace packages (directories without `__init__.py`)
  - Circular imports (by default). Use `--allow-cycles` only when CPython's partial initialization makes it safe
- Imports inside `try/except ImportError` that are neither in the repository nor on the allowlist are left to fail at runtime (optional dependencies). That alone does not cause a skip.
- Imports inside `TYPE_CHECKING` are treated as never executed and are not followed.

The entry file's `if __name__ == "__main__"` runs only when the bundle is executed as a script, not when it is imported. When run as a script, the entry's `__file__` is the generated file's own path.

The output is named `<entry stem>_min.py`; `probe.py` becomes `probe_min.py`. If `--output` is omitted, it is written under that name in the current directory.

## Usage

The commit SHA is always passed as an argument; there is no SHA constant in the code. When `.git` exists, HEAD must match that SHA, and uncommitted changes can be rejected with `--require-clean`. A synthetic tree without `.git` fails unless `--skip-head-check` is given. The generated bytes vary by Python minor version. Producers should fix the Python version used to generate the bundle, since the set of standard-library modules depends on it. Regenerating with an interpreter different from the header's `python` fails.

```sh
python tools/minpy.py bundle \
  --root /path/to/source \
  --entry probe.py \
  --repository example/probe \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --skip-head-check \
  --output probe_min.py
```

If `--output` is omitted, `<stem>_min.py` (here `probe_min.py`) is written to the current directory from the entry's stem. If the library itself imports a non-standard library, no file is written and the exit code is 1. To get only the catalog skip entry, use `catalog`.

```sh
python tools/minpy.py catalog \
  --root /path/to/source \
  --entry drag.py \
  --repository example/drag \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --skip-head-check \
  --reason "top-level third-party import"
```

The default search path is the repository root (`.`). For a `src` layout, repeat `--path src`.

Verification checks the header's content-sha256 (the SHA-256 of the whole UTF-8 file with that line's hex replaced by 64 zeros) and that regenerating with the same tool version and the same Python major.minor matches byte for byte. Only with `--root` is `status` `regenerated`, `ok` true, and the exit code 0. Without `--root`, only the self-hash is checked: `status` is `hash-only`, `ok` is false, and the exit code is non-zero. Both write the report JSON to standard output. CI regenerates and compares `tests/fixtures/minpy_widget` with `minpy check --root` against a file generated on the spot. This CI does not detect drift from a committed `<name>_min.py`. When registering a real bundle, compare the committed artifact with `minpy check --root`. This document change does not register pins for public repositories.

```sh
python tools/minpy.py check --bundle probe_min.py --root /path/to/source
```

## Vendor metadata

Existing `vendor-lock/1` and `vendor-consumers/1` entries remain valid as-is. Add the optional `bundle` only when pointing at a bundle artifact. A `html-import-vendor/1` source may carry the same object optionally. Pin resolution for sources without it is unchanged.

`source` / `destination` / `commit` / `blob_sha` / `sha256` describe byte identity of the generated file. `bundle` is the entry information needed to regenerate it.

```json
{
  "repository": "example/widget",
  "ref": "refs/heads/main",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "source": "widget_min.py",
  "destination": "vendor/widget_min.py",
  "blob_sha": "...",
  "sha256": "...",
  "bundle": {
    "schema": "minpy-bundle/1",
    "tool": "minpy",
    "tool_version": "0.1.0",
    "entry": "widget.py",
    "search_paths": ["."],
    "allow": [],
    "allow_cycles": false
  }
}
```

In a `vendor-consumers/1` entry, add the same `bundle` alongside `repository`, `source`, and `destination`. No commit or digests are written. A `bundle_mismatch` comparison result means the topology is the same but `bundle` differs.

Check the lock and regeneration together:

```sh
python tools/minpy.py check \
  --bundle vendor/widget_min.py \
  --lock vendor.lock.json \
  --destination vendor/widget_min.py \
  --root /path/to/source
```

## Catalog

`catalog` emits `vendor-catalog/1` tools. Every tool states its `source` explicitly; it does not rely on a default that fills in `<repository>.py` from the repository name. The min version is not a separate repository but a different `source` in the same `repository`. This is compatible with changes that make `source` required and normalize repository names (lowercase, with `-` and `.` turned into `_`).

- The library itself imports a non-standard library: only the original `source`, as `skipped` / `requires_external_package`. No `<name>_min.py` is emitted.
- Importable with the standard library alone once same-repository modules are included: the original `source` is `skipped` (`requires_companion_tool` for a script-only companion import, `requires_sibling_module` for other sibling modules). `<name>_min.py` is `enrolled` / `stdlib_min_bundle`.
- A single file that is standard-library-only from the start: only the original `source`; no min entry is created.
- A bundle that includes pure Python via the allowlist can be built as a file but does not become a `<name>_min.py` catalog entry.

Among public entry files, a shape like BlueProbe, with a same-repository module (`snapshot_source`) and a companion import used only from the script entry point (`queryu`), can be turned into a min file. A shape like PythDRagoras, with both a top-level external package (`polars`) and a same-repository module (`propositions`), cannot become a standard-library-only min file because of the external package. Tests for both use synthetic fixtures, not copies of public sources.

`vendor_sync.py check` still checks locked bytes as before. Matching regeneration is the job of `minpy.py check`. This tool never imports the artifact.

## Output

The header contains the tool version, the generating Python's major.minor, `owner/repo`, the 40-character commit, the entry, the allowlist, whether cycles are allowed, the module list, and content-sha256. Modules are stored in name order. The same input, tool version, and Python major.minor produce the same bytes.

Internal module names live under a prefix starting with `_minpy_`, built from the repository, entry, and commit. Each bundle has its own prefix, so importing several bundles into one process does not mix them. Names starting with `_minpy_` are reserved. The finder returns only prefixed names and never takes over a same-named module on the consumer side. The entry's public names are just copied into the bundle module; a replacement like `probe_min.func = ...` does not reach internal functions that are already bound. When run as a script, traceback filenames are `minpy://<module>.py`. `__file__` stays the generated file's path.

## Limitations

- Regeneration matching is checked with the same `minpy` version that built the bundle.
- The third-party root is not recorded in the header. Pass the same `--third-party-root` at check time.
- Lines mixing an import with another statement are rejected.
- Reads relative to the current directory, such as `open("data.txt")`, depend on the runtime current directory, just like the original script. Package data is not bundled.
- `importlib.import_module(name)` with a variable argument does not load bundled modules under their original names. Plugins are left to the normal import path.
- If a function parameter has the same name as an imported name, references inside the parameter's scope cannot be distinguished from the import. An `import pkgutil` / `import importlib` elsewhere in the file or in another function may cause same-named local variables in unrelated functions (such as `pkgutil = 'x'` or `return pkgutil`) to be rejected too (scopes are not considered).

### Dependency search queue

Dependencies are explored FIFO in order of first discovery, and shared dependencies and package
parents are enqueued only once. `collections.deque` is used to avoid shifting elements when
removing from the front of a list. Diagnostics for dependency edges and traversal failures are
not omitted. This change does not alter AST analysis or module resolution rules.
See [the scoped survey and verification record for Issue #119](stdlib-algorithm-audit-119.md).

### Cycle diagnostics for deep dependency graphs

Cycle diagnostics use explicit DFS frames so that dependency depth never reaches Python's
recursion limit. Name order for roots and neighbors, cycles found via edges to ancestors under
exploration, and normalization, deduplication, and diagnostic order are unchanged. This is not a
change to an API that enumerates every simple cycle. Cycles are rejected by default, and
`--allow-cycles` works as before.

This changes graph diagnostics at bundle time. It does not lift Python's import recursion or AST
analysis limits when running the output. The recursion limit is not changed, and there is no
blanket replacement with `graphlib`.
