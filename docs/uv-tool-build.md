# uv_tool_build (single-file stdlib entry → uv tool project)

`tools/uv_tool_build.py` is the one shared builder that turns a single-file,
standard-library-only Python entry into a project that `uv tool install` can
install. It replaces the per-child builders (`build_stdlib_tool.py`,
`scripts/build_single_file.py`, `tools/build_uv_tool.py`, ...) that each Aoi
child carried. It is stdlib only and lives next to [`tools/minpy.py`](minpy.md),
the parent's other single-file tool.

## What it does

1. Validates each entry `.py`:
   - The file name is lowercase (`[a-z_][a-z0-9_]*.py`), i.e. the lowercase repository name.
   - It parses, has no relative imports, and every import is the standard library
     (`sys.stdlib_module_names`), another entry passed in the same build, or an optional
     import inside `try: ... except ImportError/ModuleNotFoundError`.
     Imports inside functions are checked too. A non-stdlib import that only runs
     inside a function on an optional path (for example BlueProbe's lazy
     `from queryu import Cache`) can be allowed explicitly with `--allow-import NAME`;
     module-level imports are never allowed that way.
   - Every function used by a command (default `main`) is defined at top level.
2. Copies the entries byte-for-byte into a new (or empty) output directory.
3. Writes `pyproject.toml` with `dependencies = []`, `[project.scripts]` and
   `[tool.setuptools] py-modules`.

Nothing is written when validation fails (exit code 1).

## Naming

| Item | Default | Override |
| --- | --- | --- |
| Command | lowercase entry stem (`blueprobe.py` → `blueprobe`) | `--command NAME` (first entry only) |
| Extra commands | none | `--script NAME=MODULE[:FUNC]` (repeatable) |
| Distribution name | lowercase first entry stem (= lowercase repository name, no `-stdlib` suffix) | `--name` |
| Allowed lazy imports | none | `--allow-import NAME` (function-local only, repeatable) |
| Version / requires-python | `0.1.0` / `>=3.11` | `--version` / `--requires-python` |

CamelCase entry names (for example `BlueProbe.py`) are rejected; rename them to the
lowercase repository name first. Former suffixed command names such as DRAgoWing's
`dragowing-html` are not kept as aliases: the command is `dragowing` only.

## Usage

```sh
python tools/uv_tool_build.py blueprobe.py --out "$RUNNER_TEMP/blueprobe-tool"
uv tool install "$RUNNER_TEMP/blueprobe-tool"
blueprobe --help
```

Multiple entries (NagoyAction style) and extra command names:

```sh
python tools/uv_tool_build.py nagoyaction.py local_forge.py --out out \
  --script local-forge=local_forge
python tools/uv_tool_build.py sakanalytics.py --out out \
  --script sakanalytics-boundary=sakanalytics
```

`--json` prints a `uv-tool-build/1` summary (modules, scripts, imports per entry).

## Scope

- Packaging only. When a child has to *generate* its single file first (splicing
  canonical modules, as SakAnalytics, PythDRagoras, QueRyu and Err-o-RoadRunner do),
  that generation stays in the child for now (or moves to `tools/minpy.py`), and the
  generated file is then passed to this builder.
- Runtime dependencies are always empty. Building the installed wheel uses
  `setuptools>=68` from the index, as the child builders did.
- Requires Python 3.10+ to run the builder (`sys.stdlib_module_names`).

## Distribution

Same path as `py_launch.cjs`: after merge, the parent merge commit is registered in
`vendor-catalog.json`, Aoi vendors it, and the Aoi children receive it from Aoi
(merge-commit pins only, no generation skipping). Each child then drops its own
builder and calls the vendored copy.
