# uv_tool_build (single-file stdlib entry → uv tool project)

`tools/uv_tool_build.py` is the one shared builder that turns a single-file,
standard-library-only Python entry into a project that `uv tool install` can
install. It replaces the per-child builders (`build_stdlib_tool.py`,
`scripts/build_single_file.py`, `tools/build_uv_tool.py`, ...) that each Aoi
child carried. It is stdlib only and lives next to [`tools/minpy.py`](minpy.md),
the parent's other single-file tool.

## What it does

1. Validates each entry `.py` and each optional `--module` file:
   - The file name is lowercase (`[a-z_][a-z0-9_]*.py`). For an entry this is the lowercase repository name.
   - It parses, has no relative imports, and every static `import` / `from ... import` is the standard library
     (`sys.stdlib_module_names`), another entry or `--module` file passed in the same build, or an optional
     import inside `try: ... except ImportError/ModuleNotFoundError`.
     Imports inside functions are checked too. A non-stdlib import that only runs
     inside a function on an optional path (for example BlueProbe's lazy
     `from queryu import Cache`) can be allowed explicitly with `--allow-import NAME`;
     module-level imports are never allowed that way.
   - Every function used by a command (default `main` on each entry) is a synchronous function
     defined at top level. `--module` files are not commands, so they do not need `main`
     unless a `--script` points at them. An outer `try` does not make imports in a deferred
     function body optional; that body needs its own guard or an explicit allowance.
2. Copies the entries and `--module` files byte-for-byte into a new (or empty) output directory.
3. Writes `pyproject.toml` with `dependencies = []`, `[project.scripts]` (entries and `--script` only) and
   `[tool.setuptools] py-modules` (entries and `--module` files).

Nothing is written when validation fails (exit code 1).

This is a static syntax check, not a security boundary or proof of runtime
stdlib-only behavior. It does not resolve dynamic imports (`__import__`,
`importlib`), name rebinding/shadowing, callable signatures, or all control flow.
Validate each installed command and its supported paths in an isolated environment;
passing this check alone does not establish that the command runs successfully.

## Naming

| Item | Default | Override |
| --- | --- | --- |
| Command | lowercase entry stem (`blueprobe.py` → `blueprobe`) | `--command NAME` (first entry only) |
| Extra commands | none | `--script NAME=MODULE[:FUNC]` (repeatable) |
| Extra modules (no command) | none | `--module FILE` (repeatable) |
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

Extra modules with no command (NagoyAction style: ship only `nagoyaction`, but
bundle `local_forge.py` and `localci.py` so `nagoyaction hub` / `nagoyaction actionlint`
can import them), and extra command names on one entry:

```sh
python tools/uv_tool_build.py nagoyaction.py --out out \
  --module local_forge.py --module localci.py
python tools/uv_tool_build.py sakanalytics.py --out out \
  --script sakanalytics-boundary=sakanalytics
```

Passing a second file as an entry still gives it its own command (`local_forge.py` →
`local_forge`). `--script` can also point at a `--module` file when a second command
is wanted without the default stem command.

For QueRyu, first generate its single-file `queryu.py` with the child generator,
then allow its function-local BlueProbe import explicitly:

```sh
python tools/uv_tool_build.py queryu.py --out out --allow-import blueprobe
```

This allowance neither bundles nor installs BlueProbe. QueRyu paths that need
BlueProbe or a source module still require those components; successful packaging
alone does not verify those paths.

`--json` prints a `uv-tool-build/1` summary (modules, scripts, imports per entry, and `extra_modules`).

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
