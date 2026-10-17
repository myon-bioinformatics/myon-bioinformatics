#!/usr/bin/env python3
"""Bundle one Python entry file and its intra-repo imports into a single file.

Stdlib only. Children fetch this file from the parent repository by path
(``tools/minpy.py``). The generated ``.py`` is fetched and vendored like any
other repo-name module. See ``docs/minpy.md``.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import sysconfig
import textwrap
from pathlib import Path, PurePosixPath

__version__ = "0.1.0"
__all__ = [
    "BUNDLE_SCHEMA",
    "CHECK_SCHEMA",
    "PREFIX",
    "MinpyError",
    "bundle",
    "bundle_metadata",
    "catalog_plan",
    "check_bundle",
    "content_sha256",
    "git_blob_sha1",
    "main",
    "min_source_name",
    "parse_header",
]

BUNDLE_SCHEMA = "minpy-bundle/1"
CHECK_SCHEMA = "minpy-check/1"
PREFIX = "_minpy_"
HASH_PREFIX = "# content-sha256: "
HASH_RULE = "# content-sha256-rule: sha256-utf8-with-content-sha256-hex-replaced-by-64-zeros"
HASH_ZERO = "0" * 64
MAX_SOURCE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXTENSION_SUFFIXES = (".so", ".pyd", ".dylib")

_LOADER = """\
import importlib.machinery
import sys

_MINPY_PREFIX = {prefix}
_MINPY_ENTRY = {entry}
_MINPY_PACKAGES = {packages}
_MINPY_SOURCES = {sources}
_MINPY_SKIP = (
    "__builtins__",
    "__cached__",
    "__file__",
    "__loader__",
    "__name__",
    "__package__",
    "__path__",
    "__spec__",
)


def _minpy_original(fullname):
    if fullname == _MINPY_PREFIX:
        return ""
    prefix = _MINPY_PREFIX + "."
    if fullname.startswith(prefix):
        return fullname[len(prefix):]
    return None


class _MinpyLoader:
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        original = _minpy_original(module.__name__)
        if original is None:
            raise ImportError(module.__name__)
        if original == "":
            module.__path__ = []
            return
        source = _MINPY_SOURCES[original]
        filename = "minpy://" + original + ".py"
        module.__file__ = filename
        exec(compile(source, filename, "exec"), module.__dict__)


class _MinpyFinder:
    def __init__(self):
        self._minpy_sources = _MINPY_SOURCES

    def find_spec(self, fullname, path, target=None):
        original = _minpy_original(fullname)
        if original is None:
            return None
        if original == "":
            is_package = True
        elif original not in _MINPY_SOURCES:
            return None
        else:
            is_package = original in _MINPY_PACKAGES
        return importlib.machinery.ModuleSpec(
            fullname,
            _MinpyLoader(),
            origin="minpy://" + (original or _MINPY_PREFIX),
            is_package=is_package,
        )


def _minpy_install():
    for finder in sys.meta_path:
        if getattr(finder, "_minpy_sources", None) is _MINPY_SOURCES:
            return
    sys.meta_path.insert(0, _MinpyFinder())


def _minpy_run_entry_as_main():
    import types

    original = _MINPY_ENTRY
    full = _MINPY_PREFIX + "." + original
    module = types.ModuleType("__main__")
    origin = "minpy://" + original + ".py"
    module.__file__ = globals().get("__file__") or origin
    if original in _MINPY_PACKAGES:
        module.__package__ = full
        module.__path__ = []
    elif "." in original:
        module.__package__ = full.rpartition(".")[0]
    else:
        module.__package__ = _MINPY_PREFIX
    sys.modules["__main__"] = module
    sys.modules[full] = module
    exec(compile(_MINPY_SOURCES[original], origin, "exec"), module.__dict__)


def _minpy_export_entry(namespace):
    import importlib

    module = importlib.import_module(_MINPY_PREFIX + "." + _MINPY_ENTRY)
    for key, value in module.__dict__.items():
        if key not in _MINPY_SKIP:
            namespace[key] = value


_minpy_install()
if __name__ == "__main__":
    _minpy_run_entry_as_main()
else:
    _minpy_export_entry(globals())
"""


class MinpyError(ValueError):
    """The bundle cannot be produced or verified safely."""


class _Module:
    def __init__(
        self,
        name: str,
        path: Path,
        relative: str,
        is_package: bool,
        source: str,
        import_module_names: frozenset[str] = frozenset(),
        importlib_module_names: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.path = path
        self.relative = relative
        self.is_package = is_package
        self.source = source
        self.import_module_names = import_module_names
        self.importlib_module_names = importlib_module_names


class _Ref:
    def __init__(
        self,
        level: int,
        module: str,
        names: tuple[str, ...],
        optional: bool,
        lineno: int,
        scope: str,
        function: str | None,
    ) -> None:
        self.level = level
        self.module = module
        self.names = names
        self.optional = optional
        self.lineno = lineno
        self.scope = scope
        self.function = function


class _Graph:
    def __init__(self) -> None:
        self.modules: dict[str, _Module] = {}
        self.edges: dict[str, set[str]] = {}
        self.problems: list[str] = []
        self.library_external: list[str] = []
        self.script_external: list[str] = []
        self.third_party: list[str] = []


def git_blob_sha1(data: bytes) -> str:
    """Return the Git blob SHA-1 of ``data``."""
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def bundle_metadata(
    *,
    entry: str,
    search_paths: list[str],
    allow: list[str],
    allow_cycles: bool,
) -> dict[str, object]:
    """Return ``minpy-bundle/1`` metadata suitable for a vendor lock entry."""
    return {
        "schema": BUNDLE_SCHEMA,
        "tool": "minpy",
        "tool_version": __version__,
        "entry": entry,
        "search_paths": list(search_paths),
        "allow": list(allow),
        "allow_cycles": allow_cycles,
    }


def _repository(value: str) -> str:
    if not isinstance(value, str) or not _REPO.fullmatch(value) or any(
        part in (".", "..") for part in value.split("/")
    ):
        raise MinpyError("expected owner/repository")  # minpy: uncovered
    return value


def _commit(value: str) -> str:
    if not isinstance(value, str) or not _HEX40.fullmatch(value):
        raise MinpyError("commit must be a full 40-character lowercase SHA")  # minpy: uncovered
    return value


def _search_path(value: str) -> str:
    if value == ".":
        return "."
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise MinpyError("search path must be '.' or a relative POSIX path")  # minpy: uncovered
    parts = value.split("/")
    if any(part in ("", ".", "..", ".git") for part in parts) or any(ord(char) < 32 for char in value):
        raise MinpyError("unsafe search path: " + value)
    if "," in value or " " in value:
        raise MinpyError("search path cannot contain spaces or commas")  # minpy: uncovered
    return value


def _allow_name(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise MinpyError("allowlist entries must be top-level Python module names")  # minpy: uncovered
    if value.startswith(PREFIX):
        raise MinpyError(value + " is reserved by minpy")
    return value


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _git_head(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=60,
    )
    if result.returncode != 0:
        raise MinpyError("git rev-parse HEAD failed; pass --skip-head-check for a non-git tree")  # minpy: uncovered
    sha = result.stdout.strip().lower()
    if not _HEX40.fullmatch(sha):
        raise MinpyError("git HEAD is not a full commit SHA")  # minpy: uncovered
    return sha


def _require_head(root: Path, commit: str, *, skip_head_check: bool, require_clean: bool) -> None:
    if require_clean and skip_head_check:
        raise MinpyError("--require-clean cannot be combined with --skip-head-check")
    if skip_head_check:
        return
    head = _git_head(root)
    if head is None:
        raise MinpyError("checkout has no git HEAD; pass --skip-head-check for a synthetic tree")
    if head != commit:
        raise MinpyError("checkout HEAD does not match --commit; pass --skip-head-check to stamp a different SHA")
    if not require_clean:
        return
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=60,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise MinpyError("checkout has uncommitted changes")


def _is_stdlib(top: str) -> bool:
    if top in sys.builtin_module_names or top == "__future__":
        return True
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return top in names
    return top in _stdlib_fallback()


def _stdlib_fallback() -> frozenset[str]:
    cached = getattr(_stdlib_fallback, "names", None)
    if cached is not None:
        return cached
    stdlib = sysconfig.get_path("stdlib")
    search = []
    if stdlib and os.path.isdir(stdlib):
        search.append(stdlib)
        dynload = os.path.join(stdlib, "lib-dynload")
        if os.path.isdir(dynload):
            search.append(dynload)
    found = set()
    for name in ("os", "sys", "json", "pathlib", "importlib"):
        found.add(name)
    if search:
        for name in _listdir_modules(search[0]):
            found.add(name)
        if len(search) > 1:
            for entry in os.listdir(search[1]):
                stem = entry.split(".", 1)[0]
                if stem.isidentifier():
                    found.add(stem)
    _stdlib_fallback.names = frozenset(found)  # type: ignore[attr-defined]
    return _stdlib_fallback.names  # type: ignore[attr-defined]


def _listdir_modules(stdlib: str) -> set[str]:
    names = set()
    for entry in os.listdir(stdlib):
        path = os.path.join(stdlib, entry)
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py")):
            names.add(entry)
    return names


def _read_source(path: Path, display: str) -> str:
    if path.is_symlink():
        raise MinpyError("refusing to bundle symlink: " + display)
    data = path.read_bytes()
    if len(data) > MAX_SOURCE_BYTES:
        raise MinpyError("source exceeds size limit: " + display)
    if b"\0" in data:
        raise MinpyError("source is binary: " + display)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MinpyError("source is not UTF-8: " + display) from error


def _extension_hits(directory: Path, stem: str) -> list[Path]:
    if not directory.is_dir():
        return []
    hits = []
    for child in directory.iterdir():
        if not child.is_file():
            continue
        name = child.name
        if name == stem + child.suffix and child.suffix in _EXTENSION_SUFFIXES:
            hits.append(child)
        elif name.startswith(stem + ".") and child.suffix in _EXTENSION_SUFFIXES:
            hits.append(child)
    return hits


def _find_in_root(module_name: str, root: Path) -> tuple[str, Path] | None:
    relative = PurePosixPath(*module_name.split("."))
    py_path = root.joinpath(*relative.with_suffix(".py").parts)
    init_path = root.joinpath(*relative.parts) / "__init__.py"
    parent = root.joinpath(*relative.parent.parts) if relative.parent.parts else root
    extensions = _extension_hits(parent, relative.name)
    py_exists = py_path.is_file()
    init_exists = init_path.is_file()
    if py_exists and init_exists:
        raise MinpyError("ambiguous module and package: " + module_name)  # minpy: uncovered
    if (py_exists or init_exists) and extensions:
        raise MinpyError("Python source and a binary extension both exist: " + module_name)  # minpy: uncovered
    if extensions:
        raise MinpyError(
            "C extension cannot be bundled: " + module_name + " (" + extensions[0].name + ")"
        )
    directory = root.joinpath(*relative.parts)
    if directory.is_dir() and not py_exists and not init_exists:
        raise MinpyError("namespace package is not supported (missing __init__.py): " + module_name)  # minpy: uncovered
    if py_exists:
        return ("module", py_path)
    if init_exists:
        return ("package", init_path)
    return None


def _find(module_name: str, roots: list[Path]) -> tuple[str, Path, Path] | None:
    for root in roots:
        found = _find_in_root(module_name, root)
        if found is not None:
            kind, path = found
            return kind, path, root
    return None


def _shadows(top: str, roots: list[Path]) -> bool:
    try:
        return _find(top, roots) is not None
    except MinpyError:
        return True


def _attribute_dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.refs: list[_Ref] = []
        self.problems: list[str] = []
        self._optional = False
        self._pkgutil: set[str] = set()
        self._get_data_names: set[str] = set()
        self._importlib_modules: set[str] = set()
        self._resources_modules: set[str] = set()
        self._import_module_names: set[str] = set()
        self._bound_import_module_names: set[str] = set()
        self._current_call_func: ast.AST | None = None
        self._stack: list[str] = ["library"]
        self.defined: set[str] = set()
        self.loads: dict[str, set[str]] = {}
        self.exported: set[str] = set()
        self.all_unparsed = False

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for statement in node.orelse:
                self.visit(statement)
            return
        if _is_main_guard(node.test) and self._stack == ["library"]:
            self._stack.append("script")
            for statement in node.body:
                self.visit(statement)
            self._stack.pop()
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append("class:" + node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                self._record_all(node.value)
        if self._is_importlib_import_module_value(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._bound_import_module_names.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id == "__all__" and node.value is not None:
            self._record_all(node.value)
        if node.value is not None and self._is_importlib_import_module_value(node.value):
            if isinstance(node.target, ast.Name):
                self._bound_import_module_names.add(node.target.id)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id == "__all__":
            self._record_all(node.value)
        self.generic_visit(node)

    def _record_all(self, value: ast.AST) -> None:
        if self._stack != ["library"]:
            return
        names = _constant_strings(value)
        if names is None:
            self.all_unparsed = True
            return
        self.exported.update(names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.AST) -> None:
        if self._stack[-1] == "library":
            self.defined.add(node.name)
            self._stack.append("function:" + node.name)
            self.generic_visit(node)
            self._stack.pop()
            return
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.AST) -> None:
        self._visit_try(node)

    def _visit_try(self, node: ast.AST) -> None:
        handlers = getattr(node, "handlers")
        if not any(_catches_import_error(handler.type) for handler in handlers):
            self.generic_visit(node)
            return
        previous = self._optional
        self._optional = True
        for statement in node.body:
            self.visit(statement)
        self._optional = previous
        for statement in node.orelse:
            self.visit(statement)
        for statement in node.finalbody:
            self.visit(statement)
        for handler in handlers:
            self.visit(handler)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._note_module(alias.name, node.lineno)
            if alias.name == "pkgutil" or alias.name.startswith("pkgutil."):
                self._pkgutil.add(alias.asname or alias.name.split(".")[0])
            if alias.name == "importlib" or alias.name.startswith("importlib."):
                self._importlib_modules.add(alias.asname or "importlib")
            self._add_ref(0, alias.name, (), node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        names = tuple(alias.name for alias in node.names)
        self._note_module("." * node.level + module, node.lineno)
        star_messages = {
            "importlib": "importlib star-import is not bundled",
            "pkgutil": "pkgutil star-import is not bundled",
            "importlib.resources": "importlib.resources star-import is not bundled",
            "pkg_resources": "pkg_resources star-import is not bundled",
        }
        if node.module in star_messages and any(alias.name == "*" for alias in node.names):
            self.problems.append(
                "line " + str(node.lineno) + ": " + star_messages[node.module]
            )
        if node.module == "importlib":
            for alias in node.names:
                if alias.name == "resources":
                    self._reject_data_dotted(node.lineno or 0, "importlib.resources")
                    self._resources_modules.add(alias.asname or "resources")
                if alias.name == "importlib":
                    self._importlib_modules.add(alias.asname or "importlib")
                if alias.name == "import_module":
                    self._import_module_names.add(alias.asname or "import_module")
        self._add_ref(node.level, module, names, node.lineno)
        for alias in node.names:
            if node.module == "pkgutil" and alias.name == "pkgutil":
                self._pkgutil.add(alias.asname or alias.name)
            if node.module == "pkgutil" and alias.name == "get_data":
                self._get_data_names.add(alias.asname or "get_data")
        if node.module == "pkgutil":
            self._pkgutil.add("pkgutil")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            if (
                node.attr == "import_module"
                and self._import_module_owner_is_importlib(node)
                and node is not self._current_call_func
            ):
                self._reject_import_module_reference(node.lineno or 0)
            dotted = _attribute_dotted_name(node)
            if dotted:
                self._reject_data_dotted(node.lineno or 0, dotted)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "__file__" and "script" not in self._stack:
            self.problems.append("line " + str(node.lineno) + ": __file__ (data files are not bundled)")
        if isinstance(node.ctx, ast.Load):
            if node.id in self._get_data_names:
                self._reject_data_dotted(node.lineno or 0, "pkgutil.get_data")
            elif node.id in self._bound_import_module_names:
                self._reject_import_module_reference(node.lineno or 0)
            elif node.id in self._import_module_names and node is not self._current_call_func:
                self._reject_import_module_reference(node.lineno or 0)
        if isinstance(node.ctx, ast.Load):
            self.loads.setdefault(self._stack[-1], set()).add(node.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        previous_call_func = self._current_call_func
        self._current_call_func = function
        if isinstance(function, ast.Name):
            if function.id in {"__import__", "exec", "eval"}:
                self.problems.append(  # minpy: uncovered
                    "line " + str(node.lineno) + ": dynamic " + function.id + " is not bundled"
                )
            elif function.id in self._bound_import_module_names:
                self._reject_import_module_reference(node.lineno or 0)
            elif function.id in self._import_module_names:
                self._visit_import_module_call(node)
            elif function.id == "getattr" and len(node.args) >= 2:
                self._visit_getattr_call(node)
        elif isinstance(function, ast.Attribute):
            if function.attr == "import_module" and self._import_module_owner_is_importlib(function):
                self._visit_import_module_call(node)
            elif function.attr in {
                "__import__",
                "spec_from_file_location",
            }:
                self.problems.append(
                    "line " + str(node.lineno) + ": dynamic " + function.attr + " is not bundled"
                )
        self.generic_visit(node)
        self._current_call_func = previous_call_func

    def _is_importlib_import_module_value(self, value: ast.AST) -> bool:
        return (
            isinstance(value, ast.Attribute)
            and value.attr == "import_module"
            and self._import_module_owner_is_importlib(value)
        )

    def _import_module_owner_is_importlib(self, function: ast.Attribute) -> bool:
        owner = function.value
        return isinstance(owner, ast.Name) and owner.id in self._importlib_modules

    def _reject_import_module_reference(self, lineno: int) -> None:
        self._data_file_problem(lineno, "importlib.import_module")

    def _visit_import_module_call(self, node: ast.Call) -> None:
        literal = _literal_module(node)
        if literal is None:
            return
        self._note_module(literal, node.lineno or 0)
        self._add_ref(0, literal, (), node.lineno or 0)

    def _visit_getattr_call(self, node: ast.Call) -> None:
        owner = node.args[0]
        attribute = _literal_string(node.args[1])
        if attribute is None:
            return
        if attribute == "resources" and isinstance(owner, ast.Name) and owner.id in self._importlib_modules:
            self._reject_data_dotted(node.lineno or 0, "importlib.resources")
        if attribute == "get_data" and isinstance(owner, ast.Name) and (
            owner.id in self._pkgutil or owner.id == "pkgutil"
        ):
            self._reject_data_dotted(node.lineno or 0, "pkgutil.get_data")
        if attribute == "import_module" and isinstance(owner, ast.Name) and owner.id in self._importlib_modules:
            self._reject_import_module_reference(node.lineno or 0)

    def _reject_data_dotted(self, lineno: int, dotted: str) -> None:
        parts = dotted.split(".")
        root = parts[0]
        if root in self._resources_modules:
            self._data_file_problem(lineno, "importlib.resources")
            return
        if dotted == "importlib.resources" or dotted.startswith("importlib.resources."):
            self._data_file_problem(lineno, "importlib.resources")
            return
        if len(parts) >= 3 and parts[0] in self._importlib_modules and parts[1] == "resources":
            self._data_file_problem(lineno, "importlib.resources")
            return
        if dotted == "pkg_resources" or dotted.startswith("pkg_resources."):
            self._data_file_problem(lineno, "pkg_resources")
            return
        if dotted.endswith(".get_data") or dotted == "get_data":
            if root in self._pkgutil or root in self._get_data_names or root == "pkgutil":
                self._data_file_problem(lineno, "pkgutil.get_data")

    def _data_file_problem(self, lineno: int, kind: str) -> None:
        message = {
            "importlib.resources": "importlib.resources (data files are not bundled)",
            "importlib.import_module": "importlib.import_module (dynamic import is not bundled)",
            "pkg_resources": "pkg_resources (data files are not bundled)",
            "pkgutil.get_data": "pkgutil.get_data (data files are not bundled)",
        }[kind]
        self.problems.append("line " + str(lineno) + ": " + message)

    def _add_ref(self, level: int, module: str, names: tuple[str, ...], lineno: int) -> None:
        self.refs.append(
            _Ref(level, module, names, self._optional, lineno, self._scope_kind(), self._function_name())
        )

    def _scope_kind(self) -> str:
        if "script" in self._stack:
            return "script"
        if any(item.startswith("function:") for item in self._stack):
            return "deferred"
        return "library"

    def _function_name(self) -> str | None:
        for item in reversed(self._stack):
            if item.startswith("function:"):
                return item.split(":", 1)[1]
        return None

    def script_only_functions(self) -> set[str]:
        """Functions reachable only from ``if __name__ == '__main__'``."""
        if self.all_unparsed:
            return set()
        library_loads = set(self.loads.get("library", ())) | set(self.exported)
        script_loads = set(self.loads.get("script", ()))
        func_loads: dict[str, set[str]] = {}
        for scope, names in self.loads.items():
            if scope.startswith("function:"):
                func_loads[scope.split(":", 1)[1]] = set(names)
        script_only: set[str] = set()
        changed = True
        while changed:
            changed = False
            for name in sorted(self.defined):
                if name in script_only or name in library_loads:
                    continue
                outside = [
                    owner
                    for owner, used in func_loads.items()
                    if owner != name and name in used and owner not in script_only
                ]
                if outside:
                    continue
                used_by_script = name in script_loads or any(
                    name in func_loads.get(owner, ()) for owner in script_only
                )
                if used_by_script:
                    script_only.add(name)
                    changed = True
        return script_only

    def _note_module(self, module: str, lineno: int) -> None:
        dotted = module.lstrip(".")
        if dotted == "importlib.resources" or dotted.startswith("importlib.resources."):
            self._reject_data_dotted(lineno, dotted)
        elif dotted == "pkg_resources" or dotted.startswith("pkg_resources."):
            self._reject_data_dotted(lineno, dotted)


def _is_type_checking(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    if not isinstance(node.ops[0], ast.Eq):
        return False

    def is_name(item: ast.AST) -> bool:
        return isinstance(item, ast.Name) and item.id == "__name__"

    def is_main(item: ast.AST) -> bool:
        return isinstance(item, ast.Constant) and item.value == "__main__"

    left = node.left
    right = node.comparators[0]
    return (is_name(left) and is_main(right)) or (is_main(left) and is_name(right))


def _constant_strings(value: ast.AST) -> set[str] | None:
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        found: set[str] = set()
        for item in value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                found.add(item.value)
            else:
                return None
        return found
    return None


def _literal_module(node: ast.Call) -> str | None:
    if not node.args:
        return None
    return _literal_string(node.args[0])


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value
    return None


def _catches_import_error(node: ast.AST | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name) and node.id in {"ImportError", "ModuleNotFoundError"}:
        return True
    if isinstance(node, ast.Attribute) and node.attr in {"ImportError", "ModuleNotFoundError"}:
        return True
    if isinstance(node, ast.Tuple):
        return any(_catches_import_error(item) for item in node.elts)
    return False


def _package_name(module_name: str, is_package: bool) -> str:
    if is_package:
        return module_name
    return module_name.rpartition(".")[0]


def _resolve_relative(package: str, level: int, module: str) -> str:
    if level <= 0:
        if not module:
            raise MinpyError("invalid import")  # minpy: unreachable
        return module
    if not package:
        raise MinpyError("relative import in a top-level module")  # minpy: uncovered
    bits = package.rsplit(".", level - 1)
    if len(bits) < level:
        raise MinpyError("relative import beyond top-level package")
    base = bits[0]
    return base + "." + module if module else base


def _parents(module_name: str) -> list[str]:
    parts = module_name.split(".")
    return [".".join(parts[:index]) for index in range(1, len(parts))]


def _module_name(root: Path, entry: str, search_paths: list[str]) -> tuple[str, Path]:
    entry_path = (root / PurePosixPath(entry)).resolve()
    if not entry_path.is_file():
        raise MinpyError("entry file does not exist: " + entry)  # minpy: uncovered
    if entry_path.suffix != ".py":
        raise MinpyError("entry must be a .py file")  # minpy: uncovered
    for search in search_paths:
        base = (root / PurePosixPath(search)).resolve()
        try:
            relative = entry_path.relative_to(base)
        except ValueError:
            continue
        if relative.name == "__init__.py":
            parts = relative.parent.parts
        else:
            parts = relative.with_suffix("").parts
        if not parts or any(not part.isidentifier() for part in parts):
            raise MinpyError("entry path is not an importable module: " + entry)  # minpy: uncovered
        if any(part.startswith(PREFIX) for part in parts):
            raise MinpyError(PREFIX + " is reserved by minpy")  # minpy: uncovered
        return ".".join(parts), entry_path
    raise MinpyError("entry is not under a search path: " + entry)  # minpy: uncovered


def _display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _scan_tree(source: str, display: str) -> _Scanner:
    try:
        tree = ast.parse(source, filename=display)
    except SyntaxError as error:
        raise MinpyError("syntax error in " + display + ": " + str(error.msg)) from error  # minpy: uncovered
    scanner = _Scanner()
    scanner.visit(tree)
    return scanner


def _future_lineno(source: str) -> int | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:  # minpy: unreachable
        return None  # minpy: unreachable
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" and node.lineno is not None
    ]
    return min(lines) if lines else None


def _targets(package: str, ref: _Ref) -> list[str]:
    base = _resolve_relative(package, ref.level, ref.module)
    found = [base] if base else []
    for name in ref.names:
        if name == "*":
            continue
        found.append(base + "." + name if base else name)
    return found


def _resolve_target(
    dotted: str,
    *,
    search_roots: list[Path],
    third_roots: list[Path],
    allow: set[str],
) -> tuple[str, str | None]:
    """Return ``("stdlib", None)``, ``("local", module)``, or ``("missing", top)``."""
    top = dotted.split(".")[0]
    if top.startswith(PREFIX):
        raise MinpyError("import of reserved module " + top)
    if _is_stdlib(top):
        if _shadows(top, search_roots) or (top in allow and _shadows(top, third_roots)):
            raise MinpyError("module " + top + " shadows the standard library")
        return "stdlib", None
    parts = dotted.split(".")
    for length in range(len(parts), 0, -1):
        name = ".".join(parts[:length])
        try:
            found = _find(name, search_roots)
        except MinpyError:
            raise
        if found is None and top in allow:
            found = _find(name, third_roots)
        if found is not None:
            return "local", name
    return "missing", top


def _remember(bucket: list[str], name: str | None) -> None:
    if name and name not in bucket:
        bucket.append(name)


def _ref_is_script_only(ref: _Ref, script_functions: set[str]) -> bool:
    if ref.scope == "script":
        return True
    return ref.scope == "deferred" and ref.function in script_functions


def _collect(
    *,
    root: Path,
    entry_name: str,
    entry_path: Path,
    search_roots: list[Path],
    third_roots: list[Path],
    allow: set[str],
) -> _Graph:
    graph = _Graph()
    queue = [entry_name]
    seen: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        if any(part.startswith(PREFIX) or not part.isidentifier() for part in name.split(".")):
            graph.problems.append(name + ": invalid or reserved module name")  # minpy: uncovered
            continue
        from_third = False
        if name == entry_name:
            path = entry_path
            is_package = path.name == "__init__.py"
            base = root
        else:
            found = _find(name, search_roots)
            if found is None and name.split(".")[0] in allow:
                found = _find(name, third_roots)
                from_third = found is not None
            if found is None:
                graph.problems.append(name + ": bundled parent or module is missing")  # minpy: uncovered
                continue
            kind, path, base = found
            is_package = kind == "package"
            if is_package is False and kind != "module":
                graph.problems.append(name + ": not a Python module")  # minpy: unreachable
                continue  # minpy: unreachable
            if from_third:
                _remember(graph.third_party, name.split(".")[0])
        for parent in _parents(name):
            parent_found = _find(parent, search_roots)
            if parent_found is None and parent.split(".")[0] in allow:
                parent_found = _find(parent, third_roots)
            if parent_found is None or parent_found[0] != "package":
                graph.problems.append(parent + ": parent is not a regular package (__init__.py required)")  # minpy: uncovered
        display = _display(base, path)
        try:
            source = _read_source(path, display)
            scanner = _scan_tree(source, display)
        except MinpyError as error:
            graph.problems.append(str(error))
            continue
        for problem in scanner.problems:
            graph.problems.append(display + ": " + problem)
        graph.modules[name] = _Module(
            name,
            path,
            display,
            is_package,
            source,
            frozenset(scanner._import_module_names),
            frozenset(scanner._importlib_modules),
        )
        package = _package_name(name, is_package)
        script_functions = scanner.script_only_functions()
        for ref in scanner.refs:
            script_only = _ref_is_script_only(ref, script_functions)
            try:
                targets = _targets(package, ref)
            except MinpyError as error:
                graph.problems.append(display + ":" + str(ref.lineno) + ": " + str(error))
                continue
            for target in targets:
                try:
                    status, resolved = _resolve_target(
                        target,
                        search_roots=search_roots,
                        third_roots=third_roots,
                        allow=allow,
                    )
                except MinpyError as error:
                    graph.problems.append(display + ":" + str(ref.lineno) + ": " + str(error))
                    continue
                if status == "stdlib":
                    continue
                if status == "missing":
                    if ref.optional:
                        continue
                    if script_only:
                        _remember(graph.script_external, resolved)
                        continue
                    _remember(graph.library_external, resolved)
                    continue
                if not resolved or resolved == name:
                    continue
                if name.startswith(resolved + "."):
                    if resolved not in graph.modules:
                        queue.append(resolved)
                    for parent in _parents(resolved):
                        if parent not in graph.modules:
                            queue.append(parent)
                    continue
                graph.edges.setdefault(name, set()).add(resolved)
                if resolved not in graph.modules:
                    queue.append(resolved)
                for parent in _parents(resolved):
                    if parent not in graph.modules:
                        queue.append(parent)
        for parent in _parents(name):
            if parent not in graph.modules:
                queue.append(parent)
    graph.problems = list(dict.fromkeys(graph.problems))
    graph.library_external = sorted(graph.library_external)
    graph.script_external = sorted(graph.script_external)
    graph.third_party = sorted(graph.third_party)
    return graph


def _cycles(edges: dict[str, set[str]]) -> list[tuple[str, ...]]:
    nodes = sorted(set(edges) | {dep for deps in edges.values() for dep in deps})
    color = {node: 0 for node in nodes}
    stack: list[str] = []
    found: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in sorted(edges.get(node, ())):
            state = color.get(nxt, 0)
            if state == 1:
                start = stack.index(nxt)
                found.append(_normalize_cycle(stack[start:]))
            elif state == 0:
                visit(nxt)
        stack.pop()
        color[node] = 2

    for node in nodes:
        if color[node] == 0:
            visit(node)
    return sorted(set(found))


def _normalize_cycle(cycle: list[str]) -> tuple[str, ...]:
    rotations = [tuple(cycle[index:] + cycle[:index]) for index in range(len(cycle))]
    return min(rotations)


def _cycle_text(cycles: list[tuple[str, ...]]) -> str:
    rendered = []
    for cycle in cycles:
        rendered.append(" -> ".join(cycle + (cycle[0],)))
    return "; ".join(rendered)


def _import_tops(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def _script_stub(top: str, entry_file: str) -> str:
    message = (
        top
        + " is not included in the stdlib minpy bundle. Use the original "
        + entry_file
        + " with the companion tool."
    )
    return "raise ImportError(" + json.dumps(message) + ")"


def _rewrite_source(
    source: str,
    modules: set[str],
    display: str,
    script_external: set[str],
    prefix: str,
    entry_file: str,
    import_module_names: frozenset[str] = frozenset(),
    importlib_module_names: frozenset[str] = frozenset(),
) -> str:
    try:
        tree = ast.parse(source, filename=display)
    except SyntaxError as error:  # minpy: unreachable
        raise MinpyError("syntax error in " + display + ": " + str(error.msg)) from error  # minpy: unreachable
    future = _future_lineno(source)
    lines = source.splitlines(keepends=True)
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            tops = _import_tops(node)
            external = [top for top in tops if top in script_external]
            if external and external != tops:
                raise MinpyError(  # minpy: uncovered
                    display
                    + ":"
                    + str(node.lineno)
                    + ": split mixed stdlib and script-only external imports onto separate lines"
                )
            if external:
                rendered: str | None = _script_stub(external[0], entry_file)
            elif isinstance(node, ast.Import):
                rendered = _render_import(node, modules, prefix)
            else:
                rendered = _render_from(node, modules, prefix)
        else:
            continue
        if rendered is None:
            continue
        if node.lineno is None or node.end_lineno is None:
            raise MinpyError(display + ": import is missing line information")  # minpy: unreachable
        if future is not None and node.lineno < future:
            raise MinpyError(display + ": import appears before __future__; move __future__ to the top")  # minpy: unreachable
        spans.append((node.lineno, node.end_lineno, rendered))
    spans.sort()
    for index, (start, end, _) in enumerate(spans):
        if index and start <= spans[index - 1][1]:
            raise MinpyError(display + ": overlapping imports; put each import on its own lines")  # minpy: uncovered
        segment = "".join(lines[start - 1:end])
        try:
            parsed = ast.parse(textwrap.dedent(segment))
        except SyntaxError as error:
            raise MinpyError(  # minpy: uncovered
                display + ":" + str(start) + ": import shares a line with other code; split it"
            ) from error
        if len(parsed.body) != 1 or not isinstance(parsed.body[0], (ast.Import, ast.ImportFrom)):
            raise MinpyError(display + ":" + str(start) + ": import shares a line with other code; split it")  # minpy: uncovered
    for start, end, rendered in reversed(spans):
        indent = lines[start - 1][: len(lines[start - 1]) - len(lines[start - 1].lstrip(" \t"))]
        fitted = _fit_import_span(rendered, end - start + 1)
        body = "\n".join((indent + part) if part else "" for part in fitted) + "\n"
        lines[start - 1:end] = [body]
    return _rewrite_import_module_calls(
        "".join(lines),
        modules,
        prefix,
        display,
        import_module_names=import_module_names,
        importlib_module_names=importlib_module_names,
    )


def _fit_import_span(rendered: str, span_len: int) -> list[str]:
    """Keep rewritten imports on the original line count so tracebacks stay aligned."""
    pieces = [part.strip() for part in rendered.split("\n") if part.strip()]
    if not pieces:
        pieces = ["pass"]  # minpy: unreachable
    while len(pieces) > span_len:
        extra = pieces.pop()  # minpy: unreachable
        pieces[-1] = pieces[-1] + "; " + extra  # minpy: unreachable
    pieces.extend([""] * (span_len - len(pieces)))
    return pieces


def _import_module_call_from_importlib(
    function: ast.AST,
    *,
    import_module_names: frozenset[str],
    importlib_module_names: frozenset[str],
) -> bool:
    if isinstance(function, ast.Name):
        return function.id in import_module_names
    if isinstance(function, ast.Attribute) and function.attr == "import_module":
        owner = function.value
        return isinstance(owner, ast.Name) and owner.id in importlib_module_names
    return False


def _rewrite_import_module_calls(
    source: str,
    modules: set[str],
    prefix: str,
    display: str,
    *,
    import_module_names: frozenset[str] = frozenset(),
    importlib_module_names: frozenset[str] = frozenset(),
) -> str:
    """Point literal import_module calls at the prefixed module object."""
    try:
        tree = ast.parse(source, filename=display)
    except SyntaxError as error:
        raise MinpyError("syntax error in " + display + ": " + str(error.msg)) from error  # minpy: uncovered
    replacements: list[tuple[int, int, int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not _import_module_call_from_importlib(
            function,
            import_module_names=import_module_names,
            importlib_module_names=importlib_module_names,
        ):
            continue
        argument = node.args[0]
        if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
            continue
        if argument.value not in modules and not any(
            name.startswith(argument.value + ".") for name in modules
        ):
            continue
        if argument.lineno is None or argument.end_lineno is None or argument.col_offset is None:
            raise MinpyError(display + ": import_module literal is missing location")  # minpy: unreachable
        if argument.end_col_offset is None or argument.lineno != argument.end_lineno:
            raise MinpyError(display + ": import_module literal must be on one line")  # minpy: uncovered
        replacements.append((
            argument.lineno,
            argument.col_offset,
            argument.end_lineno,
            argument.end_col_offset,
            json.dumps(prefix + "." + argument.value),
        ))
    if not replacements:
        return source
    lines = source.splitlines(keepends=True)
    for lineno, col, _end_lineno, end_col, rendered in sorted(replacements, reverse=True):
        raw = lines[lineno - 1].encode("utf-8")
        lines[lineno - 1] = (raw[:col] + rendered.encode("utf-8") + raw[end_col:]).decode("utf-8")
    return "".join(lines)


def _import_is_bundled(name: str, modules: set[str]) -> bool:
    return name in modules


def _render_import(node: ast.Import, modules: set[str], prefix: str) -> str | None:
    if not any(_import_is_bundled(alias.name, modules) for alias in node.names):
        return None
    parts: list[str] = []
    for alias in node.names:
        if not _import_is_bundled(alias.name, modules):
            parts.append("import " + alias.name + (" as " + alias.asname if alias.asname else ""))
            continue
        prefixed = prefix + "." + alias.name
        if alias.asname:
            parts.append("import " + prefixed + " as " + alias.asname)
            continue
        top = alias.name.split(".")[0]
        if alias.name == top:
            parts.append("import " + prefixed + " as " + top)
        else:
            parts.append("__import__(" + json.dumps(prefixed) + "); from " + prefix + " import " + top)
    return "; ".join(parts)


def _render_from(node: ast.ImportFrom, modules: set[str], prefix: str) -> str | None:
    if node.level or not node.module:
        return None
    names = [alias.name for alias in node.names]
    bundled = node.module in modules or any(
        name != "*" and node.module + "." + name in modules for name in names
    )
    if not bundled:
        return None
    rendered = []
    for alias in node.names:
        if alias.name == "*":
            rendered.append("*")
        elif alias.asname:
            rendered.append(alias.name + " as " + alias.asname)
        else:
            rendered.append(alias.name)
    return "from " + prefix + "." + node.module + " import " + ", ".join(rendered)


def _sources_literal(sources: dict[str, str]) -> str:
    lines = ["{"]
    for name in sorted(sources):
        lines.append("    " + json.dumps(name) + ": " + json.dumps(sources[name], ensure_ascii=True) + ",")
    lines.append("}")
    return "\n".join(lines)


def _packages_literal(packages: list[str]) -> str:
    if not packages:
        return "()"
    return "(\n" + "".join("    " + json.dumps(name) + ",\n" for name in packages) + ")"


def _python_version() -> str:
    return str(sys.version_info[0]) + "." + str(sys.version_info[1])


def bundle_prefix(repository: str, entry: str, commit: str) -> str:
    """Return a module prefix unique to this repository, entry, and commit."""
    material = repository + "\n" + entry.replace("\\", "/") + "\n" + commit
    return PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _header(
    *,
    repository: str,
    commit: str,
    entry: str,
    entry_module: str,
    search_paths: list[str],
    allow: list[str],
    allow_cycles: bool,
    cycles: list[tuple[str, ...]],
    modules: list[str],
    prefix: str,
    digest: str,
) -> str:
    return "\n".join([
        "#!/usr/bin/env python3",
        "# minpy-bundle/1",
        "# tool: minpy/" + __version__,
        "# python: " + _python_version(),
        "# repository: " + repository,
        "# commit: " + commit,
        "# entry: " + entry,
        "# entry-module: " + entry_module,
        "# search-paths: " + ",".join(search_paths),
        "# allow: " + ",".join(allow),
        "# allow-cycles: " + ("yes" if allow_cycles else "no"),
        "# cycles: " + _cycle_text(cycles),
        "# modules: " + ",".join(modules),
        "# prefix: " + prefix,
        HASH_PREFIX + digest,
        HASH_RULE,
        "",
        "",
    ])


def _render_bundle(
    *,
    repository: str,
    commit: str,
    entry: str,
    entry_module: str,
    search_paths: list[str],
    allow: list[str],
    allow_cycles: bool,
    cycles: list[tuple[str, ...]],
    sources: dict[str, str],
    packages: list[str],
    prefix: str,
) -> str:
    modules = sorted(sources)
    placeholder = _header(
        repository=repository,
        commit=commit,
        entry=entry,
        entry_module=entry_module,
        search_paths=search_paths,
        allow=allow,
        allow_cycles=allow_cycles,
        cycles=cycles,
        modules=modules,
        prefix=prefix,
        digest=HASH_ZERO,
    )
    loader = _LOADER.format(
        prefix=json.dumps(prefix),
        entry=json.dumps(entry_module),
        packages=_packages_literal(packages),
        sources=_sources_literal(sources),
    )
    text = placeholder + loader
    if text.count(HASH_PREFIX + HASH_ZERO) != 1:
        raise MinpyError("bundle source collides with the content-sha256 placeholder")  # minpy: uncovered
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    text = text.replace(HASH_PREFIX + HASH_ZERO, HASH_PREFIX + digest, 1)
    if len(text.encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise MinpyError("bundle exceeds size limit")  # minpy: uncovered
    return text


def content_sha256(text: str) -> str:
    """Return the content hash, or raise if the hash line is missing or duplicated."""
    lines = text.splitlines(keepends=True)
    found = 0
    rebuilt: list[str] = []
    for line in lines:
        ending = ""
        stripped = line
        if stripped.endswith("\r\n"):
            stripped = stripped[:-2]
            ending = "\r\n"
        elif stripped.endswith("\n"):
            stripped = stripped[:-1]
            ending = "\n"
        if stripped.startswith(HASH_PREFIX) and _HEX64.fullmatch(stripped[len(HASH_PREFIX):] or ""):
            found += 1
            rebuilt.append(HASH_PREFIX + HASH_ZERO + ending)
        else:
            rebuilt.append(line)
    if found != 1:
        raise MinpyError("bundle must contain exactly one content-sha256 line")
    return hashlib.sha256("".join(rebuilt).encode("utf-8")).hexdigest()


def parse_header(text: str) -> dict[str, object]:
    """Parse a bundle header and check its content hash.

    The hash detects edits. It is not authentication: anyone can recompute it.
    """
    lines = text.splitlines()
    expected = [
        "#!/usr/bin/env python3",
        "# minpy-bundle/1",
    ]
    if lines[:2] != expected or len(lines) < 17 or lines[16] != "":
        raise MinpyError("bundle header is not a minpy-bundle/1 file")  # minpy: uncovered
    if lines[15] != HASH_RULE:
        raise MinpyError("bundle content-sha256 rule is missing or was edited")

    def field(index: int, label: str) -> str:
        prefix = "# " + label + ": "
        if not lines[index].startswith(prefix):
            raise MinpyError("bundle header field " + label + " is missing")  # minpy: uncovered
        return lines[index][len(prefix):]

    tool = field(2, "tool")
    if not tool.startswith("minpy/"):
        raise MinpyError("bundle tool is not minpy")  # minpy: uncovered
    version = tool[len("minpy/"):]
    if not _VERSION.fullmatch(version):
        raise MinpyError("bundle tool version is invalid")  # minpy: uncovered
    python = field(3, "python")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", python):
        raise MinpyError("bundle python version is invalid")  # minpy: uncovered
    repository = _repository(field(4, "repository"))
    commit = _commit(field(5, "commit"))
    entry = field(6, "entry")
    if not entry.endswith(".py") or "\\" in entry or entry.startswith("/"):
        raise MinpyError("bundle entry path is invalid")  # minpy: uncovered
    entry_module = field(7, "entry-module")
    search_raw = field(8, "search-paths")
    search_paths = [_search_path(part) for part in search_raw.split(",") if part]
    if not search_paths:
        raise MinpyError("bundle search-paths is empty")  # minpy: uncovered
    allow_raw = field(9, "allow")
    allow = [_allow_name(part) for part in allow_raw.split(",") if part]
    if allow != sorted(set(allow)):
        raise MinpyError("bundle allow list must be sorted unique names")  # minpy: uncovered
    cycles_flag = field(10, "allow-cycles")
    if cycles_flag not in {"yes", "no"}:
        raise MinpyError("bundle allow-cycles must be yes or no")  # minpy: uncovered
    cycles = field(11, "cycles")
    modules_raw = field(12, "modules")
    modules = [part for part in modules_raw.split(",") if part]
    if modules != sorted(set(modules)):
        raise MinpyError("bundle modules list must be sorted unique names")  # minpy: uncovered
    prefix = field(13, "prefix")
    if not re.fullmatch(r"_minpy_[0-9a-f]{16}", prefix):
        raise MinpyError("bundle prefix is invalid")  # minpy: uncovered
    if prefix != bundle_prefix(repository, entry, commit):
        raise MinpyError("bundle prefix does not match repository, entry, and commit")
    digest = field(14, "content-sha256")
    if not _HEX64.fullmatch(digest):
        raise MinpyError("bundle content-sha256 is invalid")  # minpy: uncovered
    if content_sha256(text) != digest:
        raise MinpyError("bundle content-sha256 does not match the file bytes")
    if entry_module not in modules:
        raise MinpyError("bundle entry-module is not in the module list")  # minpy: uncovered
    return {
        "schema": BUNDLE_SCHEMA,
        "tool": "minpy",
        "tool_version": version,
        "python": python,
        "prefix": prefix,
        "repository": repository,
        "commit": commit,
        "entry": entry,
        "entry_module": entry_module,
        "search_paths": search_paths,
        "allow": allow,
        "allow_cycles": cycles_flag == "yes",
        "cycles": cycles,
        "modules": modules,
        "content_sha256": digest,
    }


class _Prepared:
    def __init__(
        self,
        graph: _Graph,
        entry: str,
        entry_name: str,
        paths: list[str],
        allowed: list[str],
        cycles: list[tuple[str, ...]],
        allow_cycles: bool,
        repository: str,
        commit: str,
    ) -> None:
        self.graph = graph
        self.entry = entry
        self.entry_name = entry_name
        self.paths = paths
        self.allowed = allowed
        self.cycles = cycles
        self.allow_cycles = allow_cycles
        self.repository = repository
        self.commit = commit


def min_source_name(entry: str) -> str:
    """Return ``<stem>_min.py`` for a repo-name entry such as ``blueprobe.py``."""
    posix = entry.replace("\\", "/")
    path = PurePosixPath(posix)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py" or not path.stem:
        raise MinpyError("entry must be a relative .py path")  # minpy: uncovered
    stem = path.stem
    if not stem.isidentifier() or stem.startswith("_") or stem.endswith("_min"):
        raise MinpyError("entry stem cannot be turned into <name>_min.py: " + path.name)  # minpy: uncovered
    return stem + "_min.py"


def _external_message(entry: str, names: list[str]) -> str:
    artifact = min_source_name(entry)
    listed = ", ".join(names)
    verb = "is" if len(names) == 1 else "are"
    return (
        "cannot produce a stdlib-only "
        + artifact
        + ": "
        + listed
        + " "
        + verb
        + " not the standard library and not an allowlisted pure-Python package. "
        + "Register "
        + PurePosixPath(entry.replace("\\", "/")).name
        + " as skipped (requires_external_package). "
        + "Pass --allow "
        + names[0]
        + " with --third-party-root only when that dependency is pure Python; "
        + "an allowlisted bundle is not registered as "
        + artifact
        + "."
    )


def _prepare(
    *,
    root: str | Path,
    entry: str,
    repository: str,
    commit: str,
    search_paths: list[str] | None,
    allow: list[str] | None,
    third_party_roots: list[str | Path] | None,
    allow_cycles: bool,
    skip_head_check: bool,
    require_clean: bool,
) -> _Prepared:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise MinpyError("root is not a directory")  # minpy: uncovered
    repository = _repository(repository)
    commit = _commit(commit)
    paths = [_search_path(path) for path in (search_paths or ["."])]
    if len(paths) != len(set(paths)):
        raise MinpyError("duplicate search path")  # minpy: uncovered
    if allow and len(allow) != len(set(allow)):
        raise MinpyError("duplicate allowlist name")  # minpy: uncovered
    allowed = sorted({_allow_name(name) for name in (allow or [])})
    _require_head(root_path, commit, skip_head_check=skip_head_check, require_clean=require_clean)
    entry_posix = entry.replace("\\", "/")
    entry_name, entry_path = _module_name(root_path, entry_posix, paths)
    search_roots = [(root_path / PurePosixPath(path)).resolve() for path in paths]
    for search_root in search_roots:
        if not search_root.is_dir() or not search_root.is_relative_to(root_path):
            raise MinpyError("search path is outside the root or missing")  # minpy: uncovered
    third_roots = [Path(path).resolve() for path in (third_party_roots or [])]
    for third in third_roots:
        if not third.is_dir():
            raise MinpyError("third-party root is not a directory")  # minpy: uncovered
    graph = _collect(
        root=root_path,
        entry_name=entry_name,
        entry_path=entry_path,
        search_roots=search_roots,
        third_roots=third_roots,
        allow=set(allowed),
    )
    if graph.problems:
        raise MinpyError("\n".join(graph.problems))
    cycles = _cycles(graph.edges)
    if cycles and not allow_cycles:
        raise MinpyError(
            "circular import: "
            + _cycle_text(cycles)
            + ". Pass --allow-cycles only when the cycle is safe under ordinary imports."
        )
    return _Prepared(
        graph,
        entry_posix,
        entry_name,
        paths,
        allowed,
        cycles,
        allow_cycles,
        repository,
        commit,
    )


def _describe(prepared: _Prepared) -> dict[str, object]:
    graph = prepared.graph
    extra = [name for name in sorted(graph.modules) if name != prepared.entry_name]
    stdlib_min = not graph.library_external and not graph.third_party and bool(graph.script_external or extra)
    return {
        "schema": "minpy-catalog/1",
        "stdlib_min": stdlib_min,
        "min_source": min_source_name(prepared.entry) if stdlib_min else None,
        "library_external": list(graph.library_external),
        "script_external": list(graph.script_external),
        "third_party": list(graph.third_party),
        "modules": sorted(graph.modules),
    }


def _catalog_tool(
    prepared: _Prepared,
    source: str,
    reason: str,
    enrollment: str | None = None,
    enrollment_reason: str | None = None,
) -> dict[str, object]:
    if not isinstance(source, str) or not source.strip():
        raise MinpyError("catalog source is required")  # minpy: uncovered
    tool: dict[str, object] = {
        "repository": prepared.repository,
        "commit": prepared.commit,
        "source": source,
        "reason": reason,
    }
    if enrollment is not None:
        tool["default_enrollment"] = {"enrollment": enrollment, "reason": enrollment_reason}
    return tool


def catalog_plan(
    *,
    root: str | Path,
    entry: str,
    repository: str,
    commit: str,
    reason: str,
    search_paths: list[str] | None = None,
    allow: list[str] | None = None,
    third_party_roots: list[str | Path] | None = None,
    allow_cycles: bool = False,
    skip_head_check: bool = False,
    require_clean: bool = False,
    min_reason: str | None = None,
) -> dict[str, object]:
    """Return catalog entries for this entry. Every tool sets ``source`` explicitly.

    A library-scope non-stdlib import is ``skipped`` / ``requires_external_package``
    and produces no ``<name>_min.py`` entry. A stdlib bundle that inlines same-repo
    modules, or that only needs a companion from the script entrypoint, adds a
    second tool whose ``source`` is ``<name>_min.py``.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise MinpyError("catalog reason is required")  # minpy: uncovered
    if min_reason is None:
        min_reason = "Stdlib-only single-file bundle produced by minpy."
    if not isinstance(min_reason, str) or not min_reason.strip():
        raise MinpyError("catalog min reason is required")  # minpy: uncovered
    prepared = _prepare(
        root=root,
        entry=entry,
        repository=repository,
        commit=commit,
        search_paths=search_paths,
        allow=allow,
        third_party_roots=third_party_roots,
        allow_cycles=allow_cycles,
        skip_head_check=skip_head_check,
        require_clean=require_clean,
    )
    described = _describe(prepared)
    graph = prepared.graph
    if graph.library_external or graph.third_party:
        tools = [
            _catalog_tool(
                prepared,
                prepared.entry,
                reason,
                "skipped",
                "requires_external_package",
            )
        ]
    elif described["stdlib_min"]:
        skip_reason = "requires_companion_tool" if graph.script_external else "requires_sibling_module"
        tools = [
            _catalog_tool(prepared, prepared.entry, reason, "skipped", skip_reason),
            _catalog_tool(
                prepared,
                str(described["min_source"]),
                min_reason,
                "enrolled",
                "stdlib_min_bundle",
            ),
        ]
    else:
        tools = [_catalog_tool(prepared, prepared.entry, reason)]
    described["catalog"] = {"schema": "vendor-catalog/1", "tools": tools}
    return described


def bundle(
    *,
    root: str | Path,
    entry: str,
    repository: str,
    commit: str,
    search_paths: list[str] | None = None,
    allow: list[str] | None = None,
    third_party_roots: list[str | Path] | None = None,
    allow_cycles: bool = False,
    skip_head_check: bool = False,
    require_clean: bool = False,
    output: str | Path | None = None,
) -> str:
    """Return a deterministic bundle. Write it when ``output`` is set."""
    prepared = _prepare(
        root=root,
        entry=entry,
        repository=repository,
        commit=commit,
        search_paths=search_paths,
        allow=allow,
        third_party_roots=third_party_roots,
        allow_cycles=allow_cycles,
        skip_head_check=skip_head_check,
        require_clean=require_clean,
    )
    if prepared.graph.library_external:
        raise MinpyError(_external_message(prepared.entry, prepared.graph.library_external))
    modules = prepared.graph.modules
    names = set(modules)
    script_external = set(prepared.graph.script_external)
    prefix = bundle_prefix(prepared.repository, prepared.entry, prepared.commit)
    entry_file = PurePosixPath(prepared.entry).name
    rewritten = {
        name: _rewrite_source(
            module.source,
            names,
            module.relative,
            script_external,
            prefix,
            entry_file,
            module.import_module_names,
            module.importlib_module_names,
        )
        for name, module in modules.items()
    }
    text = _render_bundle(
        repository=prepared.repository,
        commit=prepared.commit,
        entry=prepared.entry,
        entry_module=prepared.entry_name,
        search_paths=prepared.paths,
        allow=prepared.allowed,
        allow_cycles=prepared.allow_cycles,
        cycles=prepared.cycles if prepared.allow_cycles else [],
        sources=rewritten,
        packages=sorted(name for name, module in modules.items() if module.is_package),
        prefix=prefix,
    )
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode("utf-8"))
    return text


def _metadata_matches(header: dict[str, object], metadata: dict[str, object]) -> None:
    expected = {
        "schema": BUNDLE_SCHEMA,
        "tool": "minpy",
        "tool_version": header["tool_version"],
        "entry": header["entry"],
        "search_paths": header["search_paths"],
        "allow": header["allow"],
        "allow_cycles": header["allow_cycles"],
    }
    if metadata != expected:
        raise MinpyError("lock bundle metadata does not match the bundle header")


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MinpyError("lock is not JSON: " + str(error)) from error  # minpy: uncovered
    if not isinstance(value, dict):
        raise MinpyError("lock must be a JSON object")  # minpy: uncovered
    return value


def _lock_entry(document: dict[str, object], *, destination: str | None, source: str | None) -> dict[str, object]:
    schema = document.get("schema")
    if schema == "vendor-lock/1":
        files = document.get("files")
        if not isinstance(files, list):
            raise MinpyError("vendor-lock/1 files must be a list")  # minpy: uncovered
        if not destination:
            raise MinpyError("vendor-lock/1 check requires --destination")  # minpy: uncovered
        matches = [
            item for item in files
            if isinstance(item, dict) and item.get("destination") == destination
        ]
        if len(matches) != 1:
            raise MinpyError("lock does not contain exactly one destination: " + destination)
        return matches[0]
    if schema == "html-import-vendor/1":
        sources = document.get("sources")
        if not isinstance(sources, list):
            raise MinpyError("html-import-vendor/1 sources must be a list")  # minpy: uncovered
        if not source:
            raise MinpyError("html-import-vendor/1 check requires --source")
        matches = []
        for item in sources:
            if not isinstance(item, dict):
                continue
            path = item.get("path") or item.get("source_path")
            if path == source:
                matches.append(item)
        if len(matches) != 1:
            raise MinpyError("html-import lock does not contain exactly one source: " + source)  # minpy: uncovered
        return matches[0]
    raise MinpyError("unsupported lock schema")


def check_bundle(
    text: str,
    *,
    source_root: str | Path | None = None,
    third_party_roots: list[str | Path] | None = None,
    skip_head_check: bool = False,
    lock_path: str | Path | None = None,
    destination: str | None = None,
    source: str | None = None,
    consumer_bytes: bytes | None = None,
) -> dict[str, object]:
    """Verify a bundle hash and, when a source root is given, regenerate it."""
    if consumer_bytes is not None and consumer_bytes.decode("utf-8") != text:
        raise MinpyError("bundle text does not match the locked file bytes")
    header = parse_header(text)
    regenerated = False
    if source_root is not None:
        if header["tool_version"] != __version__:
            raise MinpyError(
                "bundle was produced by minpy/"
                + str(header["tool_version"])
                + " and this checker is minpy/"
                + __version__
            )
        if header["python"] != _python_version():
            raise MinpyError(
                "bundle records Python "
                + str(header["python"])
                + " and this interpreter is Python "
                + _python_version()
                + "; the standard-library set can differ"
            )
        fresh = bundle(
            root=source_root,
            entry=str(header["entry"]),
            repository=str(header["repository"]),
            commit=str(header["commit"]),
            search_paths=list(header["search_paths"]),
            allow=list(header["allow"]),
            third_party_roots=third_party_roots,
            allow_cycles=bool(header["allow_cycles"]),
            skip_head_check=skip_head_check,
        )
        if fresh != text:
            raise MinpyError("regenerated bundle does not match (" + _first_diff(text, fresh) + ")")
        regenerated = True
    if lock_path is not None:
        document = _load_json(Path(lock_path))
        item = _lock_entry(document, destination=destination, source=source)
        repository = item.get("repository")
        commit = item.get("commit")
        if repository != header["repository"] or commit != header["commit"]:
            raise MinpyError("lock repository or commit does not match the bundle header")
        raw = text.encode("utf-8")
        if "sha256" in item and item.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise MinpyError("lock sha256 does not match the bundle bytes")
        if "blob_sha" in item and item.get("blob_sha") != git_blob_sha1(raw):
            raise MinpyError("lock blob_sha does not match the bundle bytes")
        bundle_meta = item.get("bundle")
        if bundle_meta is not None:
            if not isinstance(bundle_meta, dict):
                raise MinpyError("lock bundle metadata must be an object")
            _metadata_matches(header, bundle_meta)
        elif document.get("schema") == "vendor-lock/1":
            raise MinpyError("vendor-lock entry is missing bundle metadata")
    return {
        "schema": CHECK_SCHEMA,
        "status": "regenerated" if regenerated else "hash-only",
        "ok": regenerated,
        "repository": header["repository"],
        "commit": header["commit"],
        "entry": header["entry"],
        "entry_module": header["entry_module"],
        "modules": header["modules"],
        "content_sha256": header["content_sha256"],
        "regenerated": regenerated,
        "tool_version": header["tool_version"],
        "python": header["python"],
        "prefix": header["prefix"],
    }


def _first_diff(left: str, right: str) -> str:
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    for index, (one, two) in enumerate(zip(left_lines, right_lines), 1):
        if one != two:
            return "first difference at line " + str(index)
    return "line count " + str(len(left_lines)) + " != " + str(len(right_lines))  # minpy: uncovered


def _read_bundle_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MinpyError("bundle is not UTF-8") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="minpy " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)

    bundle_cmd = commands.add_parser("bundle", help="write a single-file bundle")
    bundle_cmd.add_argument("--root", required=True)
    bundle_cmd.add_argument("--entry", required=True)
    bundle_cmd.add_argument("--repository", required=True)
    bundle_cmd.add_argument("--commit", required=True)
    bundle_cmd.add_argument("--output", help="default: <entry stem>_min.py in the current directory")
    bundle_cmd.add_argument("--path", action="append", dest="search_paths")
    bundle_cmd.add_argument("--allow", action="append", default=[])
    bundle_cmd.add_argument("--third-party-root", action="append", default=[])
    bundle_cmd.add_argument("--allow-cycles", action="store_true")
    bundle_cmd.add_argument("--skip-head-check", action="store_true")
    bundle_cmd.add_argument("--require-clean", action="store_true")
    bundle_cmd.add_argument("--reason", help="when set, include vendor-catalog/1 tools in the receipt")

    catalog_cmd = commands.add_parser("catalog", help="print vendor-catalog/1 tools for an entry")
    catalog_cmd.add_argument("--root", required=True)
    catalog_cmd.add_argument("--entry", required=True)
    catalog_cmd.add_argument("--repository", required=True)
    catalog_cmd.add_argument("--commit", required=True)
    catalog_cmd.add_argument("--reason", required=True)
    catalog_cmd.add_argument("--min-reason")
    catalog_cmd.add_argument("--path", action="append", dest="search_paths")
    catalog_cmd.add_argument("--allow", action="append", default=[])
    catalog_cmd.add_argument("--third-party-root", action="append", default=[])
    catalog_cmd.add_argument("--allow-cycles", action="store_true")
    catalog_cmd.add_argument("--skip-head-check", action="store_true")
    catalog_cmd.add_argument("--require-clean", action="store_true")

    check_cmd = commands.add_parser("check", help="verify a bundle hash, lock entry, and regeneration")
    check_cmd.add_argument("--bundle", required=True)
    check_cmd.add_argument("--root", help="source repository used to regenerate the bundle")
    check_cmd.add_argument("--third-party-root", action="append", default=[])
    check_cmd.add_argument("--skip-head-check", action="store_true")
    check_cmd.add_argument("--lock")
    check_cmd.add_argument("--destination")
    check_cmd.add_argument("--source", help="html-import-vendor/1 source path")

    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            receipt = catalog_plan(
                root=args.root,
                entry=args.entry,
                repository=args.repository,
                commit=args.commit,
                reason=args.reason,
                min_reason=args.min_reason,
                search_paths=args.search_paths,
                allow=args.allow,
                third_party_roots=args.third_party_root,
                allow_cycles=args.allow_cycles,
                skip_head_check=args.skip_head_check,
                require_clean=args.require_clean,
            )
        elif args.command == "bundle":
            output = args.output or min_source_name(args.entry)
            text = bundle(
                root=args.root,
                entry=args.entry,
                repository=args.repository,
                commit=args.commit,
                search_paths=args.search_paths,
                allow=args.allow,
                third_party_roots=args.third_party_root,
                allow_cycles=args.allow_cycles,
                skip_head_check=args.skip_head_check,
                require_clean=args.require_clean,
                output=output,
            )
            header = parse_header(text)
            described = _describe(
                _prepare(
                    root=args.root,
                    entry=args.entry,
                    repository=args.repository,
                    commit=args.commit,
                    search_paths=args.search_paths,
                    allow=args.allow,
                    third_party_roots=args.third_party_root,
                    allow_cycles=args.allow_cycles,
                    skip_head_check=args.skip_head_check,
                    require_clean=args.require_clean,
                )
            )
            receipt = {
                "schema": BUNDLE_SCHEMA,
                "output": str(Path(output)),
                "min_source": described["min_source"],
                "stdlib_min": described["stdlib_min"],
                "repository": header["repository"],
                "commit": header["commit"],
                "entry": header["entry"],
                "entry_module": header["entry_module"],
                "modules": header["modules"],
                "content_sha256": header["content_sha256"],
                "bytes": len(text.encode("utf-8")),
                "bundle": bundle_metadata(
                    entry=str(header["entry"]),
                    search_paths=list(header["search_paths"]),
                    allow=list(header["allow"]),
                    allow_cycles=bool(header["allow_cycles"]),
                ),
            }
            if args.reason:
                receipt["catalog"] = catalog_plan(
                    root=args.root,
                    entry=args.entry,
                    repository=args.repository,
                    commit=args.commit,
                    reason=args.reason,
                    search_paths=args.search_paths,
                    allow=args.allow,
                    third_party_roots=args.third_party_root,
                    allow_cycles=args.allow_cycles,
                    skip_head_check=args.skip_head_check,
                    require_clean=args.require_clean,
                )["catalog"]
        else:
            text = _read_bundle_file(Path(args.bundle))
            receipt = check_bundle(
                text,
                source_root=args.root,
                third_party_roots=args.third_party_root,
                skip_head_check=args.skip_head_check,
                lock_path=args.lock,
                destination=args.destination,
                source=args.source,
            )
    except (MinpyError, OSError) as error:
        for line in str(error).splitlines() or [""]:
            print("minpy: " + line, file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True))
    if args.command == "check" and receipt.get("ok") is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  # minpy: uncovered
