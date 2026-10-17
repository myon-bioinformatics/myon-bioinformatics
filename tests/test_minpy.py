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
    assert fresh["regenerated"] is True
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
    assert "queryu is not included in the stdlib minpy bundle" in text
    assert "import_module" in text
    out = tmp_path / "dist" / "probe_min.py"
    out.parent.mkdir()
    out.write_bytes(text.encode("utf-8"))
    loaded = _load_expr(out, "module.inspect_cache('n')")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "view:n"
    plugin = _load_expr(out, "module.load_source('snapshot_source').CacheView('n').label()")
    assert plugin.returncode == 0, plugin.stderr
    assert plugin.stdout.strip() == "view:n"
    stdlib_plugin = _load_expr(out, "module.load_source('json').loads('1')")
    assert stdlib_plugin.returncode == 0, stdlib_plugin.stderr
    assert stdlib_plugin.stdout.strip() == "1"
    executed = _run(out, [str(out)])
    assert executed.returncode != 0
    assert "queryu is not included in the stdlib minpy bundle" in executed.stderr

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


def test_checked_in_registry_still_validates() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "vendor-consumers.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))
    assert vendor_consumers.compare(registry, lock, "myon-bioinformatics/myon-bioinformatics")["matches"]
