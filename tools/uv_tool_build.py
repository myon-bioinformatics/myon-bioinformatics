#!/usr/bin/env python3
"""Package single-file stdlib Python entries as an installable uv tool project.

Stdlib only. This is the one shared copy for the Aoi children's single-file
(stdlib) entries; it replaces the per-child ``build_stdlib_tool.py`` style
builders. It copies each entry ``.py`` into a new work directory and writes a
``pyproject.toml`` with ``dependencies = []`` and a ``[project.scripts]`` table
(``command = "module:main"``), so ``uv tool install <dir>`` works.

Command names default to the lowercase entry stem, which by convention is the
lowercase repository name (``blueprobe.py`` -> ``blueprobe``). Entry files must
already use lowercase names. See ``docs/uv-tool-build.md``.
"""
from __future__ import annotations

import argparse, ast, json, re, shutil, sys
from pathlib import Path

__version__ = "0.1.0"
__all__ = ["SCHEMA", "UvToolBuildError", "build", "check_entry", "default_command",
           "main", "parse_script", "render_pyproject"]

SCHEMA = "uv-tool-build/1"
DEFAULT_VERSION = "0.1.0"
DEFAULT_REQUIRES_PYTHON = ">=3.11"
BUILD_REQUIRES = "setuptools>=68"
_MODULE = re.compile(r"[a-z_][a-z0-9_]*")
_COMMAND = re.compile(r"[a-z0-9][a-z0-9._-]*")
_DIST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")
_FUNC = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*")
_OPTIONAL_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError"}


class UvToolBuildError(ValueError):
    """Raised when entries or options cannot produce a stdlib-only uv tool."""


def _stdlib_names() -> frozenset[str]:
    names = getattr(sys, "stdlib_module_names", None)
    if names is None:  # Python < 3.10 cannot tell stdlib from third-party.
        raise UvToolBuildError("Python 3.10+ is required (sys.stdlib_module_names)")
    return frozenset(names) | {"__future__"}


def default_command(entry: str | Path) -> str:
    """Return the default command name: the entry stem, lowercased."""
    return Path(entry).stem.lower()


def parse_script(text: str) -> tuple[str, str, str]:
    """Parse ``NAME=MODULE[:FUNC]`` into ``(name, module, func)``."""
    name, sep, target = text.partition("=")
    module, _, func = target.partition(":")
    func = func or "main"
    if not sep or not _COMMAND.fullmatch(name) or not _MODULE.fullmatch(module) or not _FUNC.fullmatch(func):
        raise UvToolBuildError(f"--script must be NAME=MODULE[:FUNC] with a lowercase NAME and MODULE: {text!r}")
    return name, module, func


def _optional_import_nodes(tree: ast.AST) -> set[int]:
    """Find syntactically guarded imports without crossing deferred bodies."""
    def immediate_nodes(node):
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        for child in ast.iter_child_nodes(node):
            yield from immediate_nodes(child)
    optional: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        caught = set()
        for handler in node.handlers:
            kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            caught.update(k.id for k in kinds if isinstance(k, ast.Name))
        if caught & _OPTIONAL_IMPORT_ERRORS:
            for stmt in node.body:
                optional.update(id(n) for n in immediate_nodes(stmt) if isinstance(n, (ast.Import, ast.ImportFrom)))
    return optional


def check_entry(path: Path, local_modules: frozenset[str] = frozenset(),
                functions: tuple[str, ...] = ("main",), allow_imports: frozenset[str] = frozenset()) -> dict:
    """Validate one entry file; return the imports it needs.

    Fails on syntax errors, relative imports, non-stdlib imports (unless they
    are other bundled entries, optional ``try/except ImportError`` imports or
    names in ``allow_imports``) and missing top-level functions.
    ``allow_imports`` is only honoured inside functions: module-level imports
    always run, so they must be stdlib or bundled.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise UvToolBuildError(f"{path.name}: syntax error: {exc.msg} (line {exc.lineno})") from None
    stdlib = _stdlib_names()
    optional = _optional_import_nodes(tree)
    imports: set[str] = set()
    rejected: list[str] = []
    in_function = {id(n) for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                   for n in ast.walk(f)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            raise UvToolBuildError(f"{path.name}:{node.lineno}: relative imports are not single-file")
        if isinstance(node, ast.Import):
            tops = [alias.name.partition(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            tops = [(node.module or "").partition(".")[0]]
        else:
            continue
        for top in tops:
            imports.add(top)
            if top in stdlib or top in local_modules or id(node) in optional:
                continue
            if top in allow_imports and id(node) in in_function:
                continue
            rejected.append(f"{top} (line {node.lineno})")
    if rejected:
        raise UvToolBuildError(f"{path.name}: non-stdlib imports: {', '.join(sorted(set(rejected)))}"
                               " (function-local optional imports: --allow-import NAME)")
    defined = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for func in functions:
        if func not in defined:
            raise UvToolBuildError(f"{path.name}: no top-level {func}() for the command entry point")
        if isinstance(defined[func], ast.AsyncFunctionDef):
            raise UvToolBuildError(f"{path.name}: async {func}() is not supported as a command entry point")
    return {"module": path.stem, "imports": sorted(imports - {"__future__"})}


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid TOML basic strings.


def render_pyproject(name: str, modules: list[str], scripts: dict[str, str], *,
                     version: str = DEFAULT_VERSION, requires_python: str = DEFAULT_REQUIRES_PYTHON,
                     description: str | None = None) -> str:
    lines = ["[build-system]", f"requires = [{_toml(BUILD_REQUIRES)}]",
             'build-backend = "setuptools.build_meta"', "", "[project]",
             f"name = {_toml(name)}", f"version = {_toml(version)}"]
    if description:
        lines.append(f"description = {_toml(description)}")
    lines += [f"requires-python = {_toml(requires_python)}", "dependencies = []", "",
              "[project.scripts]"]
    lines += [f"{_toml(cmd)} = {_toml(target)}" for cmd, target in scripts.items()]
    lines += ["", "[tool.setuptools]", "py-modules = [" + ", ".join(_toml(m) for m in modules) + "]", ""]
    return "\n".join(lines)


def build(entries: list[str | Path], out: str | Path, *, command: str | None = None,
          scripts: list[str] = (), allow_imports: list[str] = (), name: str | None = None, version: str = DEFAULT_VERSION,
          requires_python: str = DEFAULT_REQUIRES_PYTHON, description: str | None = None) -> dict:
    """Write a uv tool project for ``entries`` into the new directory ``out``.

    Each entry gets the command ``default_command(entry)`` -> ``module:main``;
    ``command`` renames the first entry's command and ``scripts``
    (``NAME=MODULE[:FUNC]``) add more commands. Returns a JSON-ready summary.
    """
    paths = [Path(e) for e in entries]
    if not paths:
        raise UvToolBuildError("at least one entry .py is required")
    modules: list[str] = []
    for path in paths:
        if path.suffix != ".py" or not path.is_file():
            raise UvToolBuildError(f"entry must be an existing .py file: {path}")
        if not _MODULE.fullmatch(path.stem):
            raise UvToolBuildError(f"entry file name must be lowercase (lowercase repository name): {path.name}")
        if path.stem in modules:
            raise UvToolBuildError(f"duplicate entry module: {path.stem}")
        modules.append(path.stem)
    if command is not None and not _COMMAND.fullmatch(command):
        raise UvToolBuildError(f"command must be lowercase [a-z0-9._-]: {command!r}")
    if not _VERSION.fullmatch(version):
        raise UvToolBuildError(f"version must be numeric dotted: {version!r}")
    table: dict[str, tuple[str, str]] = {}
    for index, module in enumerate(modules):
        cmd = command if index == 0 and command else default_command(module)
        if cmd in table:
            raise UvToolBuildError(f"duplicate command: {cmd}")
        table[cmd] = (module, "main")
    for text in scripts:
        cmd, module, func = parse_script(text)
        if cmd in table:
            raise UvToolBuildError(f"duplicate command: {cmd}")
        if module not in modules:
            raise UvToolBuildError(f"--script {cmd}: module {module!r} is not one of the entries")
        table[cmd] = (module, func)
    dist = name or default_command(modules[0])
    if not _DIST.fullmatch(dist):
        raise UvToolBuildError(f"invalid distribution name: {dist!r}")
    for top in allow_imports:
        if not _FUNC.fullmatch(top):
            raise UvToolBuildError(f"--allow-import must be a top-level module name: {top!r}")
    local = frozenset(modules)
    checked = [check_entry(path, local, tuple(sorted({f for m, f in table.values() if m == path.stem})),
                           frozenset(allow_imports)) for path in paths]
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise UvToolBuildError(f"output must be a new or empty directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    for path in paths:
        shutil.copyfile(path, out / path.name)
    scripts_map = {cmd: f"{m}:{f}" for cmd, (m, f) in table.items()}
    (out / "pyproject.toml").write_text(
        render_pyproject(dist, modules, scripts_map, version=version,
                         requires_python=requires_python, description=description),
        encoding="utf-8", newline="\n")
    return {"schema": SCHEMA, "out": str(out), "name": dist, "version": version,
            "modules": modules, "scripts": scripts_map, "allow_imports": sorted(set(allow_imports)),
            "entries": checked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uv_tool_build", description=__doc__.split("\n\n")[0])
    parser.add_argument("entries", nargs="+", type=Path, metavar="ENTRY.py",
                        help="single-file stdlib entry (lowercase repository name), e.g. blueprobe.py")
    parser.add_argument("--out", required=True, type=Path, help="new or empty directory for the uv tool project")
    parser.add_argument("--command", help="rename the first entry's command (default: lowercase entry stem)")
    parser.add_argument("--script", action="append", default=[], metavar="NAME=MODULE[:FUNC]",
                        help="add another command (repeatable), e.g. local-forge=local_forge")
    parser.add_argument("--allow-import", action="append", default=[], metavar="NAME",
                        help="allow a non-stdlib import inside functions only (optional feature path), repeatable")
    parser.add_argument("--name", help="distribution name (default: lowercase first entry stem = repository name)")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--requires-python", default=DEFAULT_REQUIRES_PYTHON)
    parser.add_argument("--description")
    parser.add_argument("--json", action="store_true", help="print the build summary as JSON")
    args = parser.parse_args(argv)
    try:
        result = build(args.entries, args.out, command=args.command, scripts=args.script,
                       allow_imports=args.allow_import, name=args.name,
                       version=args.version, requires_python=args.requires_python,
                       description=args.description)
    except (UvToolBuildError, OSError) as exc:
        print(f"uv_tool_build: error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"wrote {result['out']} ({', '.join(result['scripts'])}); install: uv tool install {result['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
