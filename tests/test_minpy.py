"""minpy bundle behavior, fail-closed cases, and vendor metadata."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import vendor_catalog
import vendor_consumers
import vendor_sync
from tools import minpy

REPO = "example/widget"
COMMIT = "a" * 40


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source.encode("utf-8"))


def _bundle(root: Path, entry: str = "app.py", **kwargs) -> str:
    kwargs.setdefault("repository", REPO)
    kwargs.setdefault("commit", COMMIT)
    kwargs.setdefault("skip_head_check", True)
    return minpy.bundle(root=root, entry=entry, **kwargs)


def _run(path: Path, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = ""
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd or path.parent,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _load_expr(bundle_path: Path, expression: str) -> subprocess.CompletedProcess[str]:
    script = bundle_path.parent / "load_bundle.py"
    script.write_text(
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('bundled_widget', {str(bundle_path)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        f"print({expression})\n",
        encoding="utf-8",
    )
    return _run(script, [str(script)], cwd=bundle_path.parent)


def test_intra_repo_bundle_matches_original_behavior(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "def add(left, right):\n    return left + right\n")
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/format.py", "def shout(text):\n    return text.upper()\n")
    _write(
        root,
        "app.py",
        "import mathx\n"
        "from pkg.format import shout\n"
        "def compute(value):\n"
        "    return shout(str(mathx.add(value, 1)))\n"
        "if __name__ == '__main__':\n"
        "    print(compute(41))\n",
    )
    original = _run(root / "app.py", [str(root / "app.py")], cwd=root)
    assert original.returncode == 0
    assert original.stdout.strip() == "42"

    text = _bundle(root)
    out = tmp_path / "dist" / "app.min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    executed = _run(out, [str(out)])
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout.strip() == "42"
    loaded = _load_expr(out, "module.compute(41)")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "42"
    assert "main" not in loaded.stdout
    plan = minpy.catalog_plan(
        root=root,
        entry="app.py",
        repository=REPO,
        commit=COMMIT,
        reason="synthetic intra-repo bundle",
        skip_head_check=True,
    )
    assert plan["min_source"] == "app_min.py"
    assert [tool["source"] for tool in plan["catalog"]["tools"]] == ["app.py", "app_min.py"]
    assert plan["catalog"]["tools"][0]["default_enrollment"] == {
        "enrollment": "skipped",
        "reason": "requires_sibling_module",
    }
    validated = vendor_catalog.validate(plan["catalog"])
    assert validated["tools"][1]["source"] == "app_min.py"


def test_relative_imports_and_main_guard(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "pkg/__init__.py", "from .util import VALUE as PKG_VALUE\n")
    _write(root, "pkg/util.py", "VALUE = 'rel-ok'\n")
    _write(
        root,
        "pkg/cli.py",
        "from .util import VALUE\n"
        "def current():\n"
        "    return VALUE\n"
        "if __name__ == '__main__':\n"
        "    print(current())\n",
    )
    original = _run(root / "pkg", ["-m", "pkg.cli"], cwd=root)
    assert original.returncode == 0, original.stderr
    assert original.stdout.strip() == "rel-ok"

    text = _bundle(root, "pkg/cli.py")
    out = tmp_path / "dist" / "cli.min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    executed = _run(out, [str(out)])
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout.strip() == "rel-ok"
    loaded = _load_expr(out, "module.current()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "rel-ok"
    quiet = _load_expr(out, "'imported'")
    assert quiet.stdout.strip() == "imported"
    assert "rel-ok" not in quiet.stdout


def test_circular_import_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "a.py", "import b\ndef use():\n    return b.value\n")
    _write(root, "b.py", "import a\nvalue = 1\n")
    _write(root, "app.py", "import a\n")
    with pytest.raises(minpy.MinpyError, match=r"circular import:.*\ba\b.*\bb\b"):
        _bundle(root)
    code = minpy.main([
        "bundle", "--root", str(root), "--entry", "app.py",
        "--repository", REPO, "--commit", COMMIT, "--skip-head-check",
        "--output", str(tmp_path / "out.py"),
    ])
    assert code == 1


def test_allow_cycles_preserves_runtime_cycle(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "a.py", "import b\ndef use():\n    return b.value\n")
    _write(root, "b.py", "import a\nvalue = 1\n")
    _write(root, "app.py", "import a\ndef result():\n    return a.use()\n")
    text = _bundle(root, allow_cycles=True)
    assert minpy.check_bundle(text, source_root=root, skip_head_check=True)["regenerated"] is True
    out = tmp_path / "dist" / "app.min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.result()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "1"


@pytest.mark.parametrize(
    ("filename", "source", "needle"),
    [
        ("app.py", "import native\n", "C extension"),
        ("app.py", "import importlib.util\nimportlib.util.spec_from_file_location('x', 'y')\n", "dynamic"),
        ("app.py", "from pathlib import Path\nDATA = Path(__file__).name\n", "__file__"),
        ("app.py", "import requests\n", "allow"),
        ("pkg/util.py", "from .. import missing\n", "beyond top-level"),
    ],
)
def test_unsupported_cases_fail_closed(tmp_path: Path, filename: str, source: str, needle: str) -> None:
    root = tmp_path / "src"
    if filename == "pkg/util.py":
        _write(root, "pkg/__init__.py", "")
        _write(root, "pkg/util.py", source)
        entry = "pkg/util.py"
    else:
        _write(root, "app.py", source)
        entry = "app.py"
    if "native" in source:
        _write(root, "native.so", "")
    with pytest.raises(minpy.MinpyError, match=needle):
        _bundle(root, entry)


def test_optional_third_party_import_stays_external(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(
        root,
        "app.py",
        "try:\n"
        "    import missing_dep_xyz\n"
        "except ImportError:\n"
        "    missing_dep_xyz = None\n"
        "def flag():\n"
        "    return missing_dep_xyz is None\n",
    )
    text = _bundle(root)
    out = tmp_path / "dist" / "app.min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.flag()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "True"


def test_allowlisted_pure_python_dependency(tmp_path: Path) -> None:
    root = tmp_path / "src"
    third = tmp_path / "third"
    _write(third, "tinydep/__init__.py", "def ping():\n    return 'pong'\n")
    _write(root, "app.py", "import tinydep\ndef run():\n    return tinydep.ping()\n")
    text = _bundle(root, allow=["tinydep"], third_party_roots=[third])
    out = tmp_path / "dist" / "app.min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.run()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "pong"
    assert "def ping" in text
    plan = minpy.catalog_plan(
        root=root,
        entry="app.py",
        repository=REPO,
        commit=COMMIT,
        reason="synthetic allowlisted dependency",
        allow=["tinydep"],
        third_party_roots=[third],
        skip_head_check=True,
    )
    assert plan["stdlib_min"] is False
    assert plan["min_source"] is None
    assert plan["third_party"] == ["tinydep"]
    assert plan["catalog"]["tools"] == [{
        "repository": REPO,
        "commit": COMMIT,
        "source": "app.py",
        "reason": "synthetic allowlisted dependency",
        "default_enrollment": {"enrollment": "skipped", "reason": "requires_external_package"},
    }]


def test_output_is_deterministic_and_self_describing(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "def add(left, right):\n    return left + right\n")
    _write(root, "app.py", "from mathx import add\nVALUE = add(1, 2)\n")
    first = _bundle(root, allow=["zzz", "aaa"])
    second = _bundle(root, allow=["aaa", "zzz"])
    assert first == second
    header = minpy.parse_header(first)
    assert header["repository"] == REPO
    assert header["commit"] == COMMIT
    assert header["tool_version"] == minpy.__version__
    assert header["modules"] == ["app", "mathx"]
    assert header["content_sha256"] == minpy.content_sha256(first)
    assert len(str(header["commit"])) == 40
    tampered = first.replace("VALUE = add(1, 2)", "VALUE = add(1, 3)", 1)
    with pytest.raises(minpy.MinpyError, match="content-sha256"):
        minpy.parse_header(tampered)
    fresh = minpy.check_bundle(first, source_root=root, skip_head_check=True)
    assert fresh["ok"] is True
    assert fresh["status"] == "regenerated"
    assert fresh["regenerated"] is True
    partial = minpy.check_bundle(first)
    assert partial["ok"] is False
    assert partial["status"] == "hash-only"
    assert partial["regenerated"] is False
    out = tmp_path / "app.min.py"
    out.write_bytes(first.encode("utf-8"))
    assert minpy.main([
        "check", "--bundle", str(out), "--root", str(root), "--skip-head-check",
    ]) == 0


def _metadata() -> dict[str, object]:
    return minpy.bundle_metadata(
        entry="app.py",
        search_paths=["."],
        allow=[],
        allow_cycles=False,
    )


def test_lock_and_consumer_bundle_metadata(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "helper.py", "def answer():\n    return 7\n")
    _write(
        root,
        "app.py",
        "from helper import answer\n"
        "RESULT = answer()\n"
        "if __name__ == '__main__':\n"
        "    print(RESULT)\n",
    )
    text = _bundle(root)
    artifact = root / "dist" / "widget.min.py"
    artifact.parent.mkdir()
    artifact.write_bytes(text.encode("utf-8"))
    raw = artifact.read_bytes()
    metadata = _metadata()
    metadata["entry"] = "app.py"
    lock = {
        "schema": "vendor-lock/1",
        "files": [
            {
                "repository": REPO,
                "ref": "refs/heads/main",
                "commit": COMMIT,
                "source": "dist/widget.min.py",
                "destination": "vendor/widget.min.py",
                "blob_sha": minpy.git_blob_sha1(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bundle": metadata,
            }
        ],
    }
    assert vendor_sync.validate(lock)["files"][0]["bundle"] == metadata
    plain = json.loads(json.dumps(lock))
    del plain["files"][0]["bundle"]
    assert vendor_sync.validate(plain) == plain
    with pytest.raises(ValueError, match="invalid file fields"):
        vendor_sync.validate({"schema": "vendor-lock/1", "files": [{**plain["files"][0], "note": "x"}]})

    consumer_root = tmp_path / "consumer"
    destination = consumer_root / "vendor" / "widget.min.py"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(raw)
    lock_path = consumer_root / "vendor.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    report = minpy.check_bundle(
        text,
        source_root=root,
        skip_head_check=True,
        lock_path=lock_path,
        destination="vendor/widget.min.py",
    )
    assert report["regenerated"] is True

    registry = {
        "schema": "vendor-consumers/1",
        "consumers": [
            {
                "repository": "example/consumer",
                "state": "locked",
                "lock": "vendor.lock.json",
                "entries": [
                    {
                        "repository": REPO,
                        "source": "dist/widget.min.py",
                        "destination": "vendor/widget.min.py",
                        "bundle": metadata,
                    }
                ],
            }
        ],
    }
    assert vendor_consumers.validate(registry)["consumers"][0]["entries"][0]["bundle"] == metadata
    comparison = vendor_consumers.compare(registry, lock, "example/consumer")
    assert comparison["matches"] is True
    assert comparison["bundle_mismatch"] == []

    drifted = json.loads(json.dumps(lock))
    drifted["files"][0]["bundle"]["allow_cycles"] = True
    mismatch = vendor_consumers.compare(registry, drifted, "example/consumer")
    assert mismatch["matches"] is False
    assert mismatch["bundle_mismatch"][0]["expected"]["allow_cycles"] is False

    html_lock = {
        "schema": "html-import-vendor/1",
        "sources": [
            {"repository": REPO, "commit": COMMIT, "path": "index.html"},
            {
                "repository": REPO,
                "commit": "b" * 40,
                "path": "dist/widget.min.py",
                "bundle": metadata,
            },
        ],
    }
    pins = vendor_sync.collect_lock_pins(html_lock, "tools/html-import/vendor.lock.json")
    assert [pin["commit"] for pin in pins] == [COMMIT, "b" * 40]
    assert pins[1]["kind"] == "html-import-vendor"
    broken = json.loads(json.dumps(html_lock))
    broken["sources"][1]["bundle"] = {"schema": "minpy-bundle/1"}
    with pytest.raises(ValueError, match="minpy"):
        vendor_sync.collect_lock_pins(broken, "tools/html-import/vendor.lock.json")

    drifted_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    drifted_lock["files"][0]["sha256"] = "c" * 64
    lock_path.write_text(json.dumps(drifted_lock), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="sha256"):
        minpy.check_bundle(
            text,
            lock_path=lock_path,
            destination="vendor/widget.min.py",
        )


def test_git_head_must_match_commit(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "minpy test",
        "GIT_AUTHOR_EMAIL": "minpy@example.com",
        "GIT_COMMITTER_NAME": "minpy test",
        "GIT_COMMITTER_EMAIL": "minpy@example.com",
    })
    subprocess.check_call(["git", "init", "-b", "main"], cwd=root, env=env, stdout=subprocess.DEVNULL)
    subprocess.check_call(["git", "add", "app.py"], cwd=root, env=env, stdout=subprocess.DEVNULL)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=root, env=env, stdout=subprocess.DEVNULL)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    text = minpy.bundle(root=root, entry="app.py", repository=REPO, commit=head)
    assert minpy.parse_header(text)["commit"] == head
    with pytest.raises(minpy.MinpyError, match="HEAD"):
        minpy.bundle(root=root, entry="app.py", repository=REPO, commit=COMMIT)
    clean = minpy.bundle(root=root, entry="app.py", repository=REPO, commit=head, require_clean=True)
    assert minpy.parse_header(clean)["commit"] == head
    _write(root, "extra.py", "VALUE = 2\n")
    with pytest.raises(minpy.MinpyError, match="uncommitted"):
        minpy.bundle(root=root, entry="app.py", repository=REPO, commit=head, require_clean=True)
    with pytest.raises(minpy.MinpyError, match="cannot be combined"):
        minpy.bundle(
            root=root,
            entry="app.py",
            repository=REPO,
            commit=head,
            skip_head_check=True,
            require_clean=True,
        )


def _normalized_repo_module(repository: str) -> str:
    return repository.split("/")[1].casefold().replace("-", "_").replace(".", "_")


def _assert_explicit_catalog_sources(plan: dict) -> None:
    """Match vendor-catalog tools that require source and reject normalized repo collisions."""
    catalog = plan["catalog"]
    assert catalog["schema"] == "vendor-catalog/1"
    modules: dict[str, str] = {}
    for tool in catalog["tools"]:
        assert {"repository", "commit", "reason", "source"} <= set(tool)
        assert isinstance(tool["source"], str) and tool["source"].strip()
        assert "\\" not in tool["source"] and not tool["source"].startswith("/")
        module = _normalized_repo_module(tool["repository"])
        prior = modules.get(module)
        assert prior in (None, tool["repository"].casefold())
        modules[module] = tool["repository"].casefold()
    validated = vendor_catalog.validate(catalog)
    assert [tool["source"] for tool in validated["tools"]] == [tool["source"] for tool in catalog["tools"]]


def test_companion_script_bundle_registers_stdlib_min(tmp_path: Path) -> None:
    """Same-repo helper plus a script-only companion import.

    The shape follows the public BlueProbe entry file: a same-repo module, a
    variable import_module plugin loader, and a companion import that runs only
    from the script entrypoint. The source here is synthetic.
    """
    root = tmp_path / "src"
    _write(
        root,
        "snapshot_source.py",
        "class CacheView:\n"
        "    def __init__(self, cache):\n"
        "        self.cache = cache\n"
        "    def label(self):\n"
        "        return 'view:' + str(self.cache)\n",
    )
    _write(
        root,
        "probe.py",
        "import importlib\n"
        "def load_source(name):\n"
        "    return importlib.import_module(name)\n"
        "def inspect_cache(cache):\n"
        "    from snapshot_source import CacheView\n"
        "    return CacheView(cache).label()\n"
        "def main():\n"
        "    from queryu import Cache\n"
        "    return Cache\n"
        "if __name__ == '__main__':\n"
        "    import sys\n"
        "    from pathlib import Path\n"
        "    sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        "    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'queryu'))\n"
        "    main()\n",
    )
    text = _bundle(root, "probe.py", repository="example/probe")
    assert "class CacheView" in text
    assert "from queryu import" not in text
    assert "import queryu" not in text
    assert "queryu is not included in the stdlib minpy bundle. Use the original probe.py with the companion tool." in text
    assert "import_module" in text
    out = tmp_path / "dist" / "probe_min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.inspect_cache('n')")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "view:n"
    plugin = _load_expr(
        out,
        "(module.inspect_cache('n'), 'snapshot_source' in __import__('sys').modules)",
    )
    assert plugin.returncode == 0, plugin.stderr
    assert plugin.stdout.strip() == "('view:n', False)"
    missing = _load_expr(out, "module.load_source('snapshot_source')")
    assert missing.returncode != 0
    assert "snapshot_source" in missing.stderr
    stdlib_plugin = _load_expr(out, "module.load_source('json').loads('1')")
    assert stdlib_plugin.returncode == 0, stdlib_plugin.stderr
    assert stdlib_plugin.stdout.strip() == "1"
    executed = _run(out, [str(out)])
    assert executed.returncode != 0
    assert "Use the original probe.py with the companion tool." in executed.stderr

    plan = minpy.catalog_plan(
        root=root,
        entry="probe.py",
        repository="example/probe",
        commit=COMMIT,
        reason="synthetic companion entrypoint",
        skip_head_check=True,
    )
    assert plan["stdlib_min"] is True
    assert plan["min_source"] == "probe_min.py"
    assert plan["script_external"] == ["queryu"]
    assert plan["library_external"] == []
    assert plan["modules"] == ["probe", "snapshot_source"]
    assert [tool["source"] for tool in plan["catalog"]["tools"]] == ["probe.py", "probe_min.py"]
    assert plan["catalog"]["tools"][0]["default_enrollment"] == {
        "enrollment": "skipped",
        "reason": "requires_companion_tool",
    }
    assert plan["catalog"]["tools"][1]["default_enrollment"] == {
        "enrollment": "enrolled",
        "reason": "stdlib_min_bundle",
    }
    _assert_explicit_catalog_sources(plan)

    outdir = tmp_path / "cli"
    outdir.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools" / "minpy.py"),
            "bundle",
            "--root", str(root),
            "--entry", "probe.py",
            "--repository", "example/probe",
            "--commit", COMMIT,
            "--skip-head-check",
            "--reason", "synthetic companion entrypoint",
        ],
        cwd=outdir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert Path(receipt["output"]).name == "probe_min.py"
    assert receipt["min_source"] == "probe_min.py"
    assert receipt["stdlib_min"] is True
    assert [tool["source"] for tool in receipt["catalog"]["tools"]] == ["probe.py", "probe_min.py"]
    assert (outdir / "probe_min.py").is_file()


def test_literal_import_module_is_inlined(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "VALUE = 7\n")
    _write(
        root,
        "app.py",
        "import importlib\n"
        "def value():\n"
        "    return importlib.import_module('mathx').VALUE\n",
    )
    text = _bundle(root)
    out = tmp_path / "dist" / "app_min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.value()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "7"


def test_top_level_external_dependency_is_not_a_stdlib_min(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Top-level third-party import plus a same-repo helper.

    The shape follows the public PythDRagoras entry file. The source here is synthetic.
    """
    root = tmp_path / "src"
    _write(root, "propositions.py", "def label(rows):\n    return 'rows:' + str(len(rows))\n")
    _write(
        root,
        "drag.py",
        "import polars as pl\n"
        "def summarize(rows):\n"
        "    from propositions import label\n"
        "    return label(rows) + ':' + pl.__name__\n",
    )
    with pytest.raises(minpy.MinpyError, match=r"drag_min\.py[\s\S]*polars[\s\S]*requires_external_package"):
        _bundle(root, "drag.py", repository="example/drag")
    plan = minpy.catalog_plan(
        root=root,
        entry="drag.py",
        repository="example/drag",
        commit=COMMIT,
        reason="synthetic external dependency",
        skip_head_check=True,
    )
    assert plan["stdlib_min"] is False
    assert plan["min_source"] is None
    assert plan["library_external"] == ["polars"]
    assert "propositions" in plan["modules"]
    assert plan["catalog"]["tools"] == [{
        "repository": "example/drag",
        "commit": COMMIT,
        "source": "drag.py",
        "reason": "synthetic external dependency",
        "default_enrollment": {"enrollment": "skipped", "reason": "requires_external_package"},
    }]
    _assert_explicit_catalog_sources(plan)
    code = minpy.main([
        "catalog",
        "--root", str(root),
        "--entry", "drag.py",
        "--repository", "example/drag",
        "--commit", COMMIT,
        "--skip-head-check",
        "--reason", "synthetic external dependency",
    ])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["min_source"] is None
    assert printed["catalog"]["tools"][0]["source"] == "drag.py"


def test_stdlib_single_file_sets_source_and_skips_min_entry(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "import json\ndef parse(text):\n    return json.loads(text)\n")
    text = _bundle(root)
    assert "json" in text
    plan = minpy.catalog_plan(
        root=root,
        entry="app.py",
        repository=REPO,
        commit=COMMIT,
        reason="synthetic stdlib module",
        skip_head_check=True,
    )
    assert plan["stdlib_min"] is False
    assert plan["min_source"] is None
    assert plan["catalog"]["tools"] == [{
        "repository": REPO,
        "commit": COMMIT,
        "source": "app.py",
        "reason": "synthetic stdlib module",
    }]
    _assert_explicit_catalog_sources(plan)


def _load_modules(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "load_modules.py"
    path.write_text(script, encoding="utf-8")
    return _run(path, [str(path)], cwd=tmp_path)


def test_two_bundles_do_not_share_modules(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write(left, "utils.py", 'NAME = "A-utils"\n')
    _write(left, "probe.py", "import utils\ndef who():\n    return utils.NAME\n")
    _write(right, "utils.py", 'NAME = "B-utils"\n')
    _write(right, "other.py", "import utils\ndef who():\n    return utils.NAME\n")
    text_left = _bundle(left, "probe.py", repository="example/alpha")
    text_right = _bundle(right, "other.py", repository="example/beta")
    header_left = minpy.parse_header(text_left)
    header_right = minpy.parse_header(text_right)
    assert header_left["prefix"] != header_right["prefix"]
    assert header_left["prefix"] == minpy.bundle_prefix("example/alpha", "probe.py", COMMIT)
    probe = tmp_path / "probe_min.py"
    other = tmp_path / "other_min.py"
    probe.write_bytes(text_left.encode("utf-8"))
    other.write_bytes(text_right.encode("utf-8"))
    loaded = _load_modules(
        tmp_path,
        "import importlib.util\n"
        "import sys\n"
        "def load(path, name):\n"
        "    spec = importlib.util.spec_from_file_location(name, path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[name] = module\n"
        "    spec.loader.exec_module(module)\n"
        "    return module\n"
        f"probe = load({str(probe)!r}, 'probe_min')\n"
        f"other = load({str(other)!r}, 'other_min')\n"
        "print(probe.who())\n"
        "print(other.who())\n"
        "prefixed = [name for name in sys.modules if name.endswith('.utils')]\n"
        "print(len(prefixed))\n"
        "print('utils' in sys.modules)\n"
        "print(sys.modules[prefixed[0]] is sys.modules[prefixed[1]])\n",
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines() == ["A-utils", "B-utils", "2", "False", "False"]


def test_bundle_does_not_hijack_consumer_module(tmp_path: Path) -> None:
    root = tmp_path / "src"
    consumer = tmp_path / "consumer"
    _write(root, "utils.py", 'NAME = "B-utils"\n')
    _write(root, "other.py", "import utils\ndef who():\n    return utils.NAME\n")
    _write(consumer, "utils.py", 'NAME = "consumer-utils"\n')
    text = _bundle(root, "other.py", repository="example/beta")
    bundle = tmp_path / "other_min.py"
    bundle.write_bytes(text.encode("utf-8"))
    prefix = minpy.parse_header(text)["prefix"]
    loaded = _load_modules(
        tmp_path,
        "import importlib.util\n"
        "import sys\n"
        f"sys.path.insert(0, {str(consumer)!r})\n"
        "def load(path, name):\n"
        "    spec = importlib.util.spec_from_file_location(name, path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[name] = module\n"
        "    spec.loader.exec_module(module)\n"
        "    return module\n"
        f"other = load({str(bundle)!r}, 'other_min')\n"
        "print('bare-before', 'utils' in sys.modules)\n"
        "import utils\n"
        "print(other.who())\n"
        "print(utils.NAME)\n"
        f"bundled = sys.modules[{prefix!r} + '.utils']\n"
        "print(utils is bundled)\n"
        "print(other.who.__globals__['utils'] is bundled)\n",
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines() == [
        "bare-before False",
        "B-utils",
        "consumer-utils",
        "False",
        "True",
    ]


def test_parent_package_import_is_not_a_cycle(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "pkg/__init__.py", "from .core import add\n")
    _write(
        root,
        "pkg/core.py",
        "from . import helpers\n"
        "def add(value):\n"
        "    return helpers.bump(value)\n",
    )
    _write(root, "pkg/helpers.py", "def bump(value):\n    return value + 1\n")
    text = _bundle(root, "pkg/__init__.py")
    out = tmp_path / "pkg_min.py"
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.add(1)")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "2"
    _write(root, "pkg/left.py", "import pkg.right\ndef ping():\n    return pkg.right.pong()\n")
    _write(root, "pkg/right.py", "import pkg.left\ndef pong():\n    return 'pong'\n")
    with pytest.raises(minpy.MinpyError, match="circular import"):
        _bundle(root, "pkg/left.py")


def test_public_all_is_not_script_only(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(
        root,
        "tool.py",
        "__all__ = ['run', 'calc']\n"
        "def calc():\n"
        "    return 3\n"
        "def run():\n"
        "    import queryu\n"
        "    return queryu\n"
        "if __name__ == '__main__':\n"
        "    run()\n",
    )
    with pytest.raises(minpy.MinpyError, match="queryu"):
        _bundle(root, "tool.py")
    plan = minpy.catalog_plan(
        root=root,
        entry="tool.py",
        repository=REPO,
        commit=COMMIT,
        reason="public companion import",
        skip_head_check=True,
    )
    assert plan["min_source"] is None
    assert plan["library_external"] == ["queryu"]
    assert plan["catalog"]["tools"][0]["default_enrollment"]["reason"] == "requires_external_package"
    _write(
        root,
        "tool.py",
        "names = ['run']\n"
        "__all__ = names\n"
        "def run():\n"
        "    import queryu\n"
        "    return queryu\n"
        "if __name__ == '__main__':\n"
        "    run()\n",
    )
    with pytest.raises(minpy.MinpyError, match="queryu"):
        _bundle(root, "tool.py")


def test_script_only_sibling_stays_stubbed_when_all_lists_other_names(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(
        root,
        "tool.py",
        "__all__ = ['calc']\n"
        "def calc():\n"
        "    return 4\n"
        "def run():\n"
        "    import queryu\n"
        "    return queryu\n"
        "if __name__ == '__main__':\n"
        "    run()\n",
    )
    text = _bundle(root, "tool.py")
    assert "Use the original tool.py with the companion tool." in text
    out = tmp_path / "tool_min.py"
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.calc()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "4"
    failed = _load_expr(out, "module.run()")
    assert failed.returncode != 0
    assert "companion tool" in failed.stderr


def test_main_traceback_uses_minpy_filename(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "def run():\n    raise RuntimeError('boom')\nrun()\n")
    text = _bundle(root)
    out = tmp_path / "app_min.py"
    out.write_bytes(text.encode("utf-8"))
    executed = _run(out, [str(out)])
    assert executed.returncode != 0
    assert 'File "minpy://app.py"' in executed.stderr
    assert "# allow-cycles" not in executed.stderr
    assert executed.stderr.find("boom") != -1


def test_crlf_hash_line_is_checked(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    text = _bundle(root)
    crlf = text.replace("\n", "\r\n")
    with pytest.raises(minpy.MinpyError, match="content-sha256"):
        minpy.parse_header(crlf)
    assert minpy.content_sha256(text) == minpy.parse_header(text)["content_sha256"]


def test_missing_git_requires_skip_head_check(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    with pytest.raises(minpy.MinpyError, match="skip-head-check"):
        minpy.bundle(root=root, entry="app.py", repository=REPO, commit=COMMIT)


def test_stdlib_fallback_without_stdlib_module_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "stdlib_module_names", raising=False)
    monkeypatch.delattr(minpy._stdlib_fallback, "names", raising=False)
    assert minpy._is_stdlib("json") is True
    assert minpy._is_stdlib("json") is True
    assert minpy._is_stdlib("polars_not_a_stdlib_module") is False


def test_html_import_lock_check_and_blob_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 5\n")
    text = _bundle(root)
    raw = text.encode("utf-8")
    metadata = minpy.bundle_metadata(entry="app.py", search_paths=["."], allow=[], allow_cycles=False)
    html_lock = {
        "schema": "html-import-vendor/1",
        "sources": [
            {
                "repository": REPO,
                "commit": COMMIT,
                "source_path": "dist/app_min.py",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "blob_sha": minpy.git_blob_sha1(raw),
                "bundle": metadata,
            }
        ],
    }
    path = tmp_path / "html.lock.json"
    path.write_text(json.dumps(html_lock), encoding="utf-8")
    report = minpy.check_bundle(text, lock_path=path, source="dist/app_min.py")
    assert report["status"] == "hash-only"
    assert report["ok"] is False
    with pytest.raises(minpy.MinpyError, match="--source"):
        minpy.check_bundle(text, lock_path=path)
    broken = json.loads(json.dumps(html_lock))
    broken["sources"][0]["blob_sha"] = "d" * 40
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="blob_sha"):
        minpy.check_bundle(text, lock_path=path, source="dist/app_min.py")
    broken["sources"][0]["blob_sha"] = minpy.git_blob_sha1(raw)
    broken["sources"][0]["bundle"] = ["not", "an", "object"]
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="bundle metadata"):
        minpy.check_bundle(text, lock_path=path, source="dist/app_min.py")
    with pytest.raises(minpy.MinpyError, match="locked file bytes"):
        minpy.check_bundle(text, consumer_bytes=b"different")


def test_regeneration_mismatch_and_missing_lock_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    text = _bundle(root)
    tampered = text.replace("VALUE = 1", "VALUE = 2", 1)
    # Keep the hash line consistent so regeneration, not the self-hash, fails.
    digest = minpy.content_sha256(tampered)
    tampered = tampered.replace(minpy.parse_header(text)["content_sha256"], digest, 1)  # type: ignore[arg-type]
    with pytest.raises(minpy.MinpyError, match="first difference"):
        minpy.check_bundle(tampered, source_root=root, skip_head_check=True)
    raw = text.encode("utf-8")
    lock = {
        "schema": "vendor-lock/1",
        "files": [{
            "repository": REPO,
            "ref": "refs/heads/main",
            "commit": COMMIT,
            "source": "app_min.py",
            "destination": "vendor/app_min.py",
            "blob_sha": minpy.git_blob_sha1(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }],
    }
    path = tmp_path / "vendor.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="missing bundle metadata"):
        minpy.check_bundle(text, lock_path=path, destination="vendor/app_min.py")
    bundle_path = tmp_path / "app_min.py"
    bundle_path.write_bytes(b"\xff")
    assert minpy.main(["check", "--bundle", str(bundle_path)]) == 1
    assert "minpy:" in capsys.readouterr().err


def test_bundle_cli_receipt_in_process(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "def add(left, right):\n    return left + right\n")
    _write(root, "app.py", "from mathx import add\nVALUE = add(1, 2)\n")
    output = tmp_path / "app_min.py"
    code = minpy.main([
        "bundle",
        "--root", str(root),
        "--entry", "app.py",
        "--repository", REPO,
        "--commit", COMMIT,
        "--skip-head-check",
        "--output", str(output),
        "--reason", "in-process receipt",
    ])
    assert code == 0, capsys.readouterr()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["min_source"] == "app_min.py"
    assert receipt["stdlib_min"] is True
    assert receipt["catalog"]["tools"][1]["source"] == "app_min.py"
    assert output.is_file()
    check = minpy.main(["check", "--bundle", str(output)])
    captured = capsys.readouterr()
    assert check == 1
    partial = json.loads(captured.out)
    assert partial["status"] == "hash-only"
    assert partial["ok"] is False


def test_checked_in_fixture_regenerates(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "minpy_widget"
    output = tmp_path / "app_min.py"
    code = minpy.main([
        "bundle",
        "--root", str(root),
        "--entry", "app.py",
        "--repository", "example/widget",
        "--commit", "a" * 40,
        "--skip-head-check",
        "--output", str(output),
    ])
    assert code == 0
    code = minpy.main([
        "check",
        "--bundle", str(output),
        "--root", str(root),
        "--skip-head-check",
    ])
    assert code == 0


def _restamp(text: str, old: str, new: str) -> str:
    updated = text.replace(old, new, 1)
    digest = minpy.content_sha256(updated)
    previous = str(minpy.parse_header(text)["content_sha256"])
    return updated.replace(previous, digest, 1)


def _write_bundle(tmp_path: Path, text: str) -> Path:
    out = tmp_path / "dist" / "app_min.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode("utf-8"))
    return out


def test_import_forms_are_rewritten_without_shifting_lines(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "def add(left, right):\n    return left + right\n")
    _write(root, "pkg/__init__.py", "from .core import VALUE\n__all__ = ['VALUE']\n")
    _write(root, "pkg/core.py", "VALUE = 9\n")
    _write(
        root,
        "app.py",
        "import mathx as mx\n"
        "import os, mathx\n"
        "import pkg.core\n"
        "from mathx import add as plus\n"
        "from pkg import *\n"
        "\n"
        "def run():\n"
        "    raise RuntimeError('boom')\n"
        "\n"
        "def value():\n"
        "    return plus(mx.add(1, 2), mathx.add(0, 1)), pkg.core.VALUE, VALUE, os.name\n",
    )
    text = _bundle(root)
    out = _write_bundle(tmp_path, text)
    loaded = _load_expr(out, "module.value()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip().startswith("(4, 9, 9, ")
    probe = out.parent / "probe_line.py"
    probe.write_text(
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('line_bundle', {str(out)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "try:\n"
        "    module.run()\n"
        "except RuntimeError:\n"
        "    import traceback\n"
        "    traceback.print_exc()\n",
        encoding="utf-8",
    )
    executed = _run(probe, [str(probe)])
    assert executed.returncode == 0, executed.stderr
    assert 'File "minpy://app.py", line 8, in run' in executed.stderr
    assert "boom" in executed.stderr


def test_conditionals_follow_runtime_imports_only(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "def add(left, right):\n    return left + right\n")
    _write(root, "other.py", "def add(left, right):\n    return left * right\n")
    _write(
        root,
        "app.py",
        "import typing\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import missing_type_only\n"
        "elif typing.TYPE_CHECKING:\n"
        "    import missing_type_attr\n"
        "else:\n"
        "    MARK = 'runtime'\n"
        "if True:\n"
        "    from mathx import add\n"
        "else:\n"
        "    from other import add\n"
        "if __name__ == '__main__':\n"
        "    import missing_script_only\n"
        "else:\n"
        "    FROM_IMPORT = add(2, 3)\n"
        "VALUE = add(1, 2)\n",
    )
    text = _bundle(root)
    header = minpy.parse_header(text)
    assert header["modules"] == ["app", "mathx", "other"]
    assert "missing_type_only" not in header["modules"]
    assert "missing_type_attr" not in header["modules"]
    out = _write_bundle(tmp_path, text)
    loaded = _load_expr(out, "(module.VALUE, module.FROM_IMPORT, module.MARK)")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "(3, 5, 'runtime')"


def test_try_forms_mark_optional_imports(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "def add(left, right):\n    return left + right\n")
    _write(root, "extra.py", "READY = 7\n")
    _write(root, "side.py", "MARK = 'else'\n")
    _write(
        root,
        "app.py",
        "import builtins\n"
        "try:\n"
        "    from mathx import add\n"
        "except RuntimeError:\n"
        "    add = None\n"
        "try:\n"
        "    import missing_modulenotfound\n"
        "except ModuleNotFoundError:\n"
        "    missing_modulenotfound = None\n"
        "try:\n"
        "    import missing_tuple\n"
        "except (OSError, ImportError):\n"
        "    missing_tuple = None\n"
        "try:\n"
        "    import missing_attr\n"
        "except builtins.ModuleNotFoundError:\n"
        "    missing_attr = None\n"
        "try:\n"
        "    import missing_bare\n"
        "except:\n"
        "    missing_bare = None\n"
        "try:\n"
        "    import missing_else\n"
        "except ImportError:\n"
        "    missing_else = None\n"
        "else:\n"
        "    from side import MARK\n"
        "finally:\n"
        "    from extra import READY\n"
        "VALUE = add(1, 2) + READY\n",
    )
    text = _bundle(root)
    header = minpy.parse_header(text)
    assert "mathx" in header["modules"]
    assert "extra" in header["modules"]
    assert "side" in header["modules"]
    out = _write_bundle(tmp_path, text)
    loaded = _load_expr(
        out,
        "(module.VALUE, module.missing_modulenotfound, module.missing_tuple, module.missing_attr, module.missing_bare)",
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "(10, None, None, None, None)"


def test_augmented_and_annotated_all_are_library(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(
        root,
        "app.py",
        "__all__: list[str] = ['calc']\n"
        "__all__ += ['run']\n"
        "\n"
        "def calc():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    import queryu_not_real\n"
        "    return queryu_not_real\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    print(run())\n",
    )
    with pytest.raises(minpy.MinpyError, match="queryu_not_real"):
        _bundle(root)


def test_async_function_import_is_bundled(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "mathx.py", "def add(left, right):\n    return left + right\n")
    _write(
        root,
        "app.py",
        "import asyncio\n"
        "\n"
        "async def total():\n"
        "    from mathx import add\n"
        "    return add(2, 3)\n"
        "\n"
        "def value():\n"
        "    return asyncio.run(total())\n",
    )
    text = _bundle(root)
    assert "mathx" in minpy.parse_header(text)["modules"]
    out = _write_bundle(tmp_path, text)
    loaded = _load_expr(out, "module.value()")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "5"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="except* requires Python 3.11")
def test_except_star_optional_import(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(
        root,
        "app.py",
        "try:\n"
        "    import missing_star\n"
        "except* ModuleNotFoundError:\n"
        "    missing_star = None\n"
        "VALUE = missing_star\n",
    )
    text = _bundle(root)
    out = _write_bundle(tmp_path, text)
    loaded = _load_expr(out, "module.VALUE")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "None"


def test_check_rejects_tool_python_and_lock_identity(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    text = _bundle(root)
    other_python = "3.8" if minpy._python_version() != "3.8" else "3.9"
    wrong_python = _restamp(text, "# python: " + minpy._python_version(), "# python: " + other_python)
    with pytest.raises(minpy.MinpyError, match="standard-library set can differ"):
        minpy.check_bundle(wrong_python, source_root=root, skip_head_check=True)
    wrong_tool = _restamp(text, "# tool: minpy/" + minpy.__version__, "# tool: minpy/9.9.9")
    with pytest.raises(minpy.MinpyError, match="minpy/9.9.9"):
        minpy.check_bundle(wrong_tool, source_root=root, skip_head_check=True)
    raw = text.encode("utf-8")
    lock = {
        "schema": "vendor-lock/1",
        "files": [{
            "repository": "example/other",
            "ref": "refs/heads/main",
            "commit": COMMIT,
            "source": "app_min.py",
            "destination": "vendor/app_min.py",
            "blob_sha": minpy.git_blob_sha1(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bundle": _metadata(),
        }],
    }
    path = tmp_path / "vendor.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="lock repository or commit"):
        minpy.check_bundle(text, lock_path=path, destination="vendor/app_min.py")
    lock["files"][0]["repository"] = REPO
    lock["files"][0]["commit"] = "b" * 40
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="lock repository or commit"):
        minpy.check_bundle(text, lock_path=path, destination="vendor/app_min.py")


def test_checked_in_registry_still_validates() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "vendor-consumers.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))
    assert vendor_consumers.compare(registry, lock, "myon-bioinformatics/myon-bioinformatics")["matches"]


def test_refuses_symlinked_source(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    _write(root, "real.py", "VALUE = 1\n")
    (root / "dep.py").symlink_to("real.py")
    _write(root, "app.py", "from dep import VALUE\n")
    with pytest.raises(minpy.MinpyError, match="symlink"):
        _bundle(root)


def test_refuses_oversized_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(minpy, "MAX_SOURCE_BYTES", 8)
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 'toolong'\n")
    with pytest.raises(minpy.MinpyError, match="size limit"):
        _bundle(root)


def test_refuses_binary_and_non_utf8_sources(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "binary.py").write_bytes(b"VALUE = 1\x00\n")
    with pytest.raises(minpy.MinpyError, match="binary"):
        _bundle(root, "binary.py")
    (root / "bad.py").write_bytes(b"VALUE = '\xff'\n")
    with pytest.raises(minpy.MinpyError, match="UTF-8"):
        _bundle(root, "bad.py")


@pytest.mark.parametrize("search_path", ["../escape", "pkg/.git", ".."])
def test_rejects_unsafe_search_paths(tmp_path: Path, search_path: str) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    with pytest.raises(minpy.MinpyError, match="unsafe search path|search path must"):
        minpy.bundle(
            root=root,
            entry="app.py",
            repository=REPO,
            commit=COMMIT,
            search_paths=[search_path],
            skip_head_check=True,
        )


def test_rejects_reserved_allowlist_and_shadowed_stdlib(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "import json\nVALUE = json.dumps(1)\n")
    with pytest.raises(minpy.MinpyError, match="reserved by minpy"):
        minpy.bundle(
            root=root,
            entry="app.py",
            repository=REPO,
            commit=COMMIT,
            allow=[minpy.PREFIX + "blocked"],
            skip_head_check=True,
        )
    _write(root, "json.py", "def dumps(value):\n    return 'shadow:' + str(value)\n")
    with pytest.raises(minpy.MinpyError, match="shadows the standard library"):
        _bundle(root)


def test_rejects_reserved_import_prefix(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", f"import {minpy.PREFIX}missing\n")
    with pytest.raises(minpy.MinpyError, match="reserved module"):
        _bundle(root)


def test_rejects_from_importlib_import_resources(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "from importlib import resources\nresources.files('pkg')\n")
    with pytest.raises(minpy.MinpyError, match="importlib.resources"):
        _bundle(root)


def test_rejects_from_importlib_import_resources_alias(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "from importlib import resources as res\nres.files('pkg')\n")
    with pytest.raises(minpy.MinpyError, match="importlib.resources"):
        _bundle(root)


def test_rejects_importlib_resources_attribute_access(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "import importlib\nimportlib.resources.files('pkg')\n")
    with pytest.raises(minpy.MinpyError, match="importlib.resources"):
        _bundle(root)


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        ("import pkgutil\npkgutil.get_data('x', 'y')\n", "pkgutil.get_data"),
        ("from pkgutil import get_data\nget_data('x', 'y')\n", "pkgutil.get_data"),
        ("from pkgutil import get_data as load_pkg\nload_pkg('x', 'y')\n", "pkgutil.get_data"),
        ("import importlib.resources\n", "importlib.resources"),
        ("import pkg_resources\n", "pkg_resources"),
        ("from pkg_resources import resource_filename\nresource_filename('pkg', 'x')\n", "pkg_resources"),
    ],
)
def test_rejects_data_file_import_hooks(tmp_path: Path, source: str, needle: str) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", source)
    with pytest.raises(minpy.MinpyError, match=needle):
        _bundle(root)


def test_rejects_tampered_bundle_header(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    text = _bundle(root)
    broken = text.replace(minpy.HASH_RULE, "# content-sha256-rule: edited", 1)
    with pytest.raises(minpy.MinpyError, match="content-sha256 rule"):
        minpy.parse_header(broken)
    stripped_hashes = "\n".join(
        line for line in text.splitlines() if not line.startswith(minpy.HASH_PREFIX)
    )
    with pytest.raises(minpy.MinpyError, match="exactly one content-sha256"):
        minpy.content_sha256(stripped_hashes)
    lines = text.splitlines()
    hash_index = next(i for i, line in enumerate(lines) if line.startswith(minpy.HASH_PREFIX))
    doubled_lines = lines[: hash_index + 1] + [lines[hash_index]] + lines[hash_index + 1 :]
    doubled = "\n".join(doubled_lines) + "\n"
    with pytest.raises(minpy.MinpyError, match="exactly one content-sha256"):
        minpy.content_sha256(doubled)


def test_rejects_wrong_bundle_prefix_in_header(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    text = _bundle(root)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# prefix: "):
            lines[index] = "# prefix: _minpy_0123456789abcdef"
            break
    else:
        raise AssertionError("bundle header missing prefix field")
    tampered = "\n".join(lines) + "\n"
    with pytest.raises(minpy.MinpyError, match="prefix does not match"):
        minpy.parse_header(tampered)


def test_lock_schema_and_metadata_checks(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(root, "app.py", "VALUE = 1\n")
    text = _bundle(root)
    raw = text.encode("utf-8")
    metadata = minpy.bundle_metadata(entry="app.py", search_paths=["."], allow=[], allow_cycles=False)
    lock = {
        "schema": "vendor-lock/1",
        "files": [{
            "repository": REPO,
            "ref": "refs/heads/main",
            "commit": COMMIT,
            "source": "app_min.py",
            "destination": "vendor/app_min.py",
            "blob_sha": minpy.git_blob_sha1(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bundle": {**metadata, "allow_cycles": True},
        }],
    }
    path = tmp_path / "vendor.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="bundle metadata"):
        minpy.check_bundle(text, lock_path=path, destination="vendor/app_min.py")
    lock["files"][0]["bundle"] = metadata
    lock["files"].append({**lock["files"][0], "source": "other_min.py"})
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="exactly one destination"):
        minpy.check_bundle(text, lock_path=path, destination="vendor/app_min.py")
    other_lock = tmp_path / "other.lock.json"
    other_lock.write_text(json.dumps({"schema": "unknown/1"}), encoding="utf-8")
    with pytest.raises(minpy.MinpyError, match="unsupported lock schema"):
        minpy.check_bundle(text, lock_path=other_lock)


def test_unexecuted_statements_are_classified(tmp_path: Path) -> None:
    """Classify missed minpy.py lines against coverage.

    The sum check guarantees that unreachable lines were not executed and that
    every missed error-reporting line carries either ``minpy: unreachable`` or
    ``minpy: uncovered``. It does not prove full line coverage by itself.
    """
    pytest.importorskip("coverage")
    if os.environ.get("MINPY_COVERAGE_CHILD"):
        return
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["MINPY_COVERAGE_CHILD"] = "1"
    env["COVERAGE_FILE"] = str(tmp_path / ".coverage")
    rcfile = tmp_path / "coveragerc"
    rcfile.write_text("[run]\nbranch = false\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--rcfile",
            str(rcfile),
            "--include",
            "*/minpy.py",
            "-m",
            "pytest",
            "-q",
            "tests/test_minpy.py",
            "--tb=line",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    report_path = tmp_path / "coverage.json"
    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "json",
            "-o",
            str(report_path),
            "--include",
            "*/minpy.py",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(payload["files"]) == 1
    info = next(iter(payload["files"].values()))
    executed = set(info["executed_lines"])
    missed = set(info["missing_lines"])
    source = (repo / "tools" / "minpy.py").read_text(encoding="utf-8").splitlines()

    def marker(number: int) -> str | None:
        text = source[number - 1]
        if "minpy: unreachable" in text:
            return "unreachable"
        if "minpy: uncovered" in text:
            return "uncovered"
        return None

    unreachable = {number for number in executed | missed if marker(number) == "unreachable"}
    assert unreachable <= missed
    untagged = []
    for number in sorted(missed):
        text = source[number - 1]
        reports_error = (
            "raise MinpyError" in text or "problems.append" in text or "raise SystemExit" in text
        )
        if reports_error and marker(number) not in {"unreachable", "uncovered"}:
            untagged.append(number)
    assert untagged == []
    uncovered = missed - unreachable
    assert len(executed) + len(unreachable) + len(uncovered) == len(executed | missed)
    assert not (executed & unreachable)
    assert not (unreachable & uncovered)
