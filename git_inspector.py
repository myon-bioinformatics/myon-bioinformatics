"""Compatibility names for local Git reads now owned by gh_identity.

Uses the locked adjacent/parent vendor copy; never downloads at import time.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

__version__ = "0.1.0"
__all__ = ["status", "ls_files", "diff", "log", "log_numstat", "show", "blame", "grep", "check_ignore"]

_base = Path(__file__).resolve().parent
_path = _base / "gh_identity.py"
if not _path.is_file():
    _path = _base / "vendor/gh_identity.py"
_spec = importlib.util.spec_from_file_location("_git_inspector_ghi", _path)
if _spec is None or _spec.loader is None:
    raise ImportError("locked gh_identity is unavailable")
_ghi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ghi)
GitInspectionError = _ghi.GitInspectionError
for _name in __all__:
    globals()[_name] = getattr(_ghi, "git_" + _name)
