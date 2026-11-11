"""Shared single-file stdlib -> uv tool builder (tools/uv_tool_build.py)."""
from __future__ import annotations

import json, os, shutil, subprocess, sys, tomllib
from pathlib import Path

import pytest

from tools import uv_tool_build as utb

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "uv_tool_build.py"
ENTRY = '"""Example."""\nimport json, sys\n\n\ndef main(argv=None):\n    print(json.dumps({"argv": list(sys.argv[1:] if argv is None else argv)}))\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


def _entry(tmp_path: Path, name: str = "blueprobe.py", source: str = ENTRY) -> Path:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _pyproject(out: Path) -> dict:
    return tomllib.loads((out / "pyproject.toml").read_text(encoding="utf-8"))


def test_build_copies_entry_and_writes_pyproject(tmp_path):
    entry = _entry(tmp_path)
    out = tmp_path / "tool"
    result = utb.build([entry], out)
    assert (out / "blueprobe.py").read_bytes() == entry.read_bytes()
    data = _pyproject(out)
    assert data["build-system"] == {"requires": ["setuptools>=68"], "build-backend": "setuptools.build_meta"}
    assert data["project"]["name"] == "blueprobe"
    assert data["project"]["version"] == "0.1.0"
    assert data["project"]["requires-python"] == ">=3.11"
    assert data["project"]["dependencies"] == []
    assert data["project"]["scripts"] == {"blueprobe": "blueprobe:main"}
    assert data["tool"]["setuptools"]["py-modules"] == ["blueprobe"]
    assert result["schema"] == "uv-tool-build/1"
    assert result["scripts"] == {"blueprobe": "blueprobe:main"}
    assert result["entries"] == [{"module": "blueprobe", "imports": ["json", "sys"]}]
    assert sorted(p.name for p in out.iterdir()) == ["blueprobe.py", "pyproject.toml"]


@pytest.mark.parametrize("entry, command", [("blueprobe.py", "blueprobe"), ("dragowing.py", "dragowing"),
                                            ("Path/To/queryu.py", "queryu"), ("SakAnalytics.py", "sakanalytics")])
def test_default_command_is_lowercase_stem(entry, command):
    assert utb.default_command(entry) == command


def test_command_override_and_extra_scripts(tmp_path):
    entry = _entry(tmp_path, "sakanalytics.py")
    utb.build([entry], tmp_path / "o", command="sakanalytics", scripts=["sakanalytics-boundary=sakanalytics"],
              name="sakanalytics-cli", version="0.2.0", description="SakAnalytics \"stdlib\" CLI")
    data = _pyproject(tmp_path / "o")
    assert data["project"]["scripts"] == {"sakanalytics": "sakanalytics:main",
                                          "sakanalytics-boundary": "sakanalytics:main"}
    assert data["project"]["name"] == "sakanalytics-cli"
    assert data["project"]["version"] == "0.2.0"
    assert data["project"]["description"] == 'SakAnalytics "stdlib" CLI'
    utb.build([entry], tmp_path / "o2", command="sakanalytics-x")
    assert _pyproject(tmp_path / "o2")["project"]["scripts"] == {"sakanalytics-x": "sakanalytics:main"}


def test_multiple_entries_may_import_each_other(tmp_path):
    main_entry = _entry(tmp_path, "nagoyaction.py", "import local_forge\n\ndef main():\n    return 0\n")
    forge = _entry(tmp_path, "local_forge.py", "import os\n\ndef main():\n    return 0\n\ndef serve():\n    return 0\n")
    result = utb.build([main_entry, forge], tmp_path / "o",
                       scripts=["local-forge=local_forge", "forge-serve=local_forge:serve"])
    data = _pyproject(tmp_path / "o")
    assert data["project"]["name"] == "nagoyaction"
    assert data["tool"]["setuptools"]["py-modules"] == ["nagoyaction", "local_forge"]
    assert data["project"]["scripts"] == {"nagoyaction": "nagoyaction:main", "local_forge": "local_forge:main",
                                          "local-forge": "local_forge:main", "forge-serve": "local_forge:serve"}
    assert result["entries"][0]["imports"] == ["local_forge"]


def test_optional_and_function_local_stdlib_imports_are_allowed(tmp_path):
    source = ("from __future__ import annotations\ntry:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
              "try:\n    import numpy as np\nexcept (ModuleNotFoundError, OSError):\n    np = None\n\n"
              "def main():\n    import statistics, xml.etree.ElementTree\n    return 0\n")
    utb.build([_entry(tmp_path, "pythdragoras.py", source)], tmp_path / "o")


@pytest.mark.parametrize("source, message", [
    ("import requests\n\ndef main():\n    return 0\n", "non-stdlib imports: requests"),
    ("def main():\n    from polars import DataFrame\n", "non-stdlib imports: polars"),
    ("try:\n    import yaml\nexcept KeyError:\n    pass\n\ndef main():\n    return 0\n", "non-stdlib imports: yaml"),
    ("from . import helper\n\ndef main():\n    return 0\n", "relative imports"),
    ("def run():\n    return 0\n", "no top-level main()"),
    ("class X:\n    def main(self):\n        return 0\n", "no top-level main()"),
    ("def main(:\n", "syntax error"),
])
def test_rejects_non_single_file_stdlib_entries(tmp_path, source, message):
    out = tmp_path / "o"
    with pytest.raises(utb.UvToolBuildError, match=message.replace("(", r"\(").replace(")", r"\)")):
        utb.build([_entry(tmp_path, "queryu.py", source)], out)
    assert not out.exists()


def test_script_function_must_exist(tmp_path):
    with pytest.raises(utb.UvToolBuildError, match=r"no top-level serve\(\)"):
        utb.build([_entry(tmp_path)], tmp_path / "o", scripts=["bp-serve=blueprobe:serve"])


@pytest.mark.parametrize("kwargs, message", [
    ({"command": "BlueProbe"}, "command must be lowercase"),
    ({"scripts": ["Bad=blueprobe"]}, "--script must be"),
    ({"scripts": ["x"]}, "--script must be"),
    ({"scripts": ["x=other"]}, "not one of the entries"),
    ({"scripts": ["blueprobe=blueprobe"]}, "duplicate command"),
    ({"name": "-bad"}, "invalid distribution name"),
    ({"version": "v1"}, "version must be"),
])
def test_rejects_bad_options(tmp_path, kwargs, message):
    with pytest.raises(utb.UvToolBuildError, match=message):
        utb.build([_entry(tmp_path)], tmp_path / "o", **kwargs)


def test_rejects_camelcase_missing_and_non_py_entries(tmp_path):
    with pytest.raises(utb.UvToolBuildError, match="must be lowercase"):
        utb.build([_entry(tmp_path, "BlueProbe.py")], tmp_path / "o")
    with pytest.raises(utb.UvToolBuildError, match="existing .py"):
        utb.build([tmp_path / "missing.py"], tmp_path / "o")
    with pytest.raises(utb.UvToolBuildError, match="existing .py"):
        utb.build([_entry(tmp_path, "blueprobe.txt")], tmp_path / "o")
    with pytest.raises(utb.UvToolBuildError, match="at least one"):
        utb.build([], tmp_path / "o")


def test_output_must_be_new_or_empty(tmp_path):
    entry = _entry(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    utb.build([entry], empty)
    with pytest.raises(utb.UvToolBuildError, match="new or empty"):
        utb.build([entry], empty)
    (tmp_path / "file").write_text("x")
    with pytest.raises(utb.UvToolBuildError, match="new or empty"):
        utb.build([entry], tmp_path / "file")


def test_cli_json_and_error_exit(tmp_path):
    entry = _entry(tmp_path)
    run = subprocess.run([sys.executable, "-S", str(SCRIPT), str(entry), "--out", str(tmp_path / "o"), "--json"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["scripts"] == {"blueprobe": "blueprobe:main"}
    bad = subprocess.run([sys.executable, "-S", str(SCRIPT), str(entry), "--out", str(tmp_path / "o")],
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "new or empty" in bad.stderr


def test_builder_itself_is_stdlib_only():
    utb.check_entry(SCRIPT)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_uv_tool_install_runs_command(tmp_path):
    out = tmp_path / "tool"
    utb.build([_entry(tmp_path)], out)
    env = dict(os.environ, UV_TOOL_DIR=str(tmp_path / "uv-tools"), UV_TOOL_BIN_DIR=str(tmp_path / "bin"))
    env.pop("UV_LOCKED", None)
    install = subprocess.run(["uv", "tool", "install", "--python", sys.executable, str(out)],
                             capture_output=True, text=True, env=env, check=False, timeout=300)
    assert install.returncode == 0, install.stderr
    exe = tmp_path / "bin" / ("blueprobe.exe" if os.name == "nt" else "blueprobe")
    run = subprocess.run([str(exe), "a b", "--x"], capture_output=True, text=True, check=False,
                         cwd=tmp_path, timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"argv": ["a b", "--x"]}


def test_allow_import_only_for_function_local_imports(tmp_path):
    lazy = "def main():\n    from queryu import Cache\n    import snapshot_source\n    return 0\n"
    with pytest.raises(utb.UvToolBuildError, match="--allow-import"):
        utb.build([_entry(tmp_path, "blueprobe.py", lazy)], tmp_path / "o")
    result = utb.build([_entry(tmp_path, "blueprobe.py", lazy)], tmp_path / "o",
                       allow_imports=["queryu", "snapshot_source"])
    assert result["allow_imports"] == ["queryu", "snapshot_source"]
    eager = "import queryu\n\ndef main():\n    return 0\n"
    with pytest.raises(utb.UvToolBuildError, match="non-stdlib imports: queryu"):
        utb.build([_entry(tmp_path, "blueprobe.py", eager)], tmp_path / "o2", allow_imports=["queryu"])
    with pytest.raises(utb.UvToolBuildError, match="--allow-import must be"):
        utb.build([_entry(tmp_path)], tmp_path / "o3", allow_imports=["a.b"])
