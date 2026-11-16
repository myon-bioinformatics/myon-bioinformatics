"""Parent tools: repo patches, evidence guards, CLI ergonomics, fleet checks, git helpers, preflight, publish_check, py_matrix, pytest_brief, hygiene."""
from __future__ import annotations

import ast
import functools
import http.server
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import venv
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
import gh_ops
import git_inspector as gi
import preflight
from browser_evidence import main, record


# ═══ · apply repo patches ════════════════════════════════════════════════════════════════════════════════
# Real local Git regressions; no network, credentials, or external service.

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "apply_repo_patches.py"
spec = importlib.util.spec_from_file_location("apply_repo_patches", SCRIPT)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Isolate test repos from developer signing/hooks/global Git configuration.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    root = tmp_path / "repo space 日本語"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Patch Test")
    git(root, "config", "user.email", "patch-test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "file.txt").write_text("".join(f"line {i}\n" for i in range(20)), encoding="utf-8")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "base")
    return root


def mailbox(repo, tmp_path, *, conflict=False, fallback=False):
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "switch", "-c", "patch-source")
    file = repo / "file.txt"
    file.write_text(file.read_text().replace("line 5\n", "patched 5\n"))
    git(repo, "commit", "-am", "Update shared pin")
    patch = tmp_path / "patch space 日本語.mbox"
    patch.write_text(git(repo, "format-patch", "--stdout", "--full-index", "-1").stdout, encoding="utf-8")
    git(repo, "switch", "main")
    if conflict or fallback:
        line = 5 if conflict else 8
        file.write_text(file.read_text().replace(f"line {line}\n", f"local {line}\n"))
        git(repo, "commit", "-am", "Keep local change")
    return patch, base


def args(repo, patch, tmp_path, apply=False):
    result = ["--target", str(repo), str(patch), "--branch", "chore/shared-pins",
              "--worktree-root", str(tmp_path / "worktrees")]
    return result + (["--apply"] if apply else [])


def test_plan_is_read_only(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    before = git(repo, "show-ref").stdout
    assert runner.main(args(repo, patch, tmp_path)) == 0
    assert git(repo, "show-ref").stdout == before
    assert not (tmp_path / "worktrees").exists()


def test_apply_preserves_original_checkout(repo, tmp_path):
    patch, base = mailbox(repo, tmp_path)
    assert runner.main(args(repo, patch, tmp_path, True)) == 0
    target = tmp_path / "worktrees" / repo.name
    assert "patched 5" in (target / "file.txt").read_text()
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == base
    assert git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert "patched 5" not in (repo / "file.txt").read_text()
    assert not git(target, "status", "--porcelain").stdout


def test_real_three_way_fallback_preserves_independent_change(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path, fallback=True)
    assert git(repo, "apply", "--check", str(patch), check=False).returncode != 0
    assert runner.main(args(repo, patch, tmp_path, True)) == 0
    text = (tmp_path / "worktrees" / repo.name / "file.txt").read_text()
    assert "patched 5" in text and "local 8" in text


def test_conflict_stops_and_keeps_main_unchanged(repo, tmp_path, capsys):
    patch, _ = mailbox(repo, tmp_path, conflict=True)
    before = git(repo, "rev-parse", "HEAD").stdout
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    target = tmp_path / "worktrees" / repo.name
    assert git(target, "ls-files", "--unmerged").stdout
    assert git(repo, "rev-parse", "HEAD").stdout == before
    assert "APPLIED" not in capsys.readouterr().out
    git(target, "am", "--abort")
    assert git(target, "rev-parse", "HEAD").stdout == before


@pytest.mark.parametrize("kind", ["modified", "staged", "untracked"])
def test_dirty_source_is_rejected(repo, tmp_path, kind):
    patch, _ = mailbox(repo, tmp_path)
    path = repo / ("new.txt" if kind == "untracked" else "file.txt")
    path.write_text("uncommitted\n")
    if kind == "staged":
        git(repo, "add", str(path))
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert not (tmp_path / "worktrees").exists()
    assert path.read_text() == "uncommitted\n"


def test_all_targets_preflight_before_any_mutation(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True) + ["--target", str(tmp_path / "missing"), str(patch)]
    assert runner.main(argv) == 1
    assert not (tmp_path / "worktrees").exists()
    assert not git(repo, "for-each-ref", "refs/heads/chore/shared-pins").stdout


def test_existing_branch_is_not_overwritten(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    git(repo, "branch", "chore/shared-pins")
    before = git(repo, "show-ref").stdout
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert git(repo, "show-ref").stdout == before


def test_duplicate_target_is_rejected(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True) + ["--target", str(repo), str(patch)]
    assert runner.main(argv) == 1
    assert not (tmp_path / "worktrees").exists()


def test_empty_patch_is_rejected(repo, tmp_path):
    patch = tmp_path / "empty.mbox"
    patch.touch()
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert not (tmp_path / "worktrees").exists()


def test_output_inside_source_is_rejected(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True)
    argv[argv.index("--worktree-root") + 1] = str(repo / "output")
    assert runner.main(argv) == 1
    assert not (repo / "output").exists()


def test_second_application_does_not_duplicate_commits(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True)
    assert runner.main(argv) == 0
    before = git(repo, "show-ref").stdout
    assert runner.main(argv) == 1
    assert git(repo, "show-ref").stdout == before


def test_inherited_index_override_does_not_hide_dirty_source(repo, tmp_path, monkeypatch):
    patch, _ = mailbox(repo, tmp_path)
    (repo / "file.txt").write_text("dirty\n")
    bogus = tmp_path / "unrelated-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(bogus))
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert not bogus.exists()


def test_stdlib_only_help():
    proc = subprocess.run([sys.executable, "-S", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--apply" in proc.stdout


def test_invalid_branch_fails_before_mutation(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True)
    argv[argv.index("--branch") + 1] = "bad..branch"
    assert runner.main(argv) == 1
    assert not (tmp_path / "worktrees").exists()


def test_pending_operation_is_not_overwritten(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    marker = repo / git(repo, "rev-parse", "--git-path", "rebase-apply").stdout.strip()
    marker.mkdir()
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert marker.is_dir()
    assert not (tmp_path / "worktrees").exists()


def test_malformed_mailbox_is_not_reported_applied(repo, tmp_path, capsys):
    patch = tmp_path / "malformed.mbox"
    patch.write_text("This is not a format-patch mailbox.\n")
    before = git(repo, "rev-parse", "HEAD").stdout
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert git(repo, "rev-parse", "HEAD").stdout == before
    assert "APPLIED" not in capsys.readouterr().out


def test_batch_stops_on_conflict_without_touching_later_targets(repo, tmp_path, capsys):
    patch, _ = mailbox(repo, tmp_path)
    second, third = tmp_path / "second", tmp_path / "third"
    for root in (second, third):
        git(tmp_path, "clone", str(repo), str(root))
        git(root, "config", "user.name", "Patch Test")
        git(root, "config", "user.email", "patch-test@example.invalid")
        git(root, "config", "commit.gpgsign", "false")
    file = second / "file.txt"
    file.write_text(file.read_text().replace("line 5\n", "conflicting line\n"))
    git(second, "commit", "-am", "Conflict")
    before = {root: git(root, "rev-parse", "HEAD").stdout for root in (repo, second, third)}
    argv = args(repo, patch, tmp_path, True)
    argv += ["--target", str(second), str(patch), "--target", str(third), str(patch)]
    assert runner.main(argv) == 1
    assert capsys.readouterr().out.count("APPLIED ") == 1
    assert (tmp_path / "worktrees" / repo.name).exists()
    assert (tmp_path / "worktrees" / second.name).exists()
    assert not (tmp_path / "worktrees" / third.name).exists()
    for root in (repo, second, third):
        assert git(root, "rev-parse", "HEAD").stdout == before[root]
    assert not git(third, "for-each-ref", "refs/heads/chore/shared-pins").stdout


def test_missing_git_is_reported_without_traceback(repo, tmp_path, monkeypatch, capsys):
    patch, _ = mailbox(repo, tmp_path)
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("git not available")
    monkeypatch.setattr(runner.subprocess, "run", missing)
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert "Traceback" not in capsys.readouterr().err


# ═══ · browser evidence ══════════════════════════════════════════════════════════════════════════════════
# Independent Playwright/Stagehand evidence contract regression.

def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


PNG = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
       + chunk(b"IEND", b""))
SHA = "a" * 40


class BrowserEvidenceTests(unittest.TestCase):
    def test_each_engine_is_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shot.png").write_bytes(PNG)
            for engine in ("playwright", "stagehand"):
                value = record(engine, root / "shot.png", root, run_id="123", head_sha=SHA)
                self.assertEqual(value["engine"], engine)
                self.assertEqual(value["screenshot"], "shot.png")
                self.assertEqual(value["size_bytes"], len(PNG))
                self.assertEqual(value["schema"], "browser-screenshot-evidence/1")

    def test_no_traversal_or_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            safe = root / "evidence"
            safe.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(PNG)
            (safe / "alias.png").symlink_to(outside)
            for path in (outside, safe / "alias.png"):
                with self.assertRaises(ValueError):
                    record("playwright", path, safe, run_id="1", head_sha=SHA)

    def test_invalid_engine_sha_and_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "shot.png"
            image.write_bytes(b"not png")
            for engine, sha in (("other", SHA), ("playwright", "bad"), ("playwright", SHA)):
                with self.assertRaises(ValueError):
                    record(engine, image, root, run_id="1", head_sha=sha)

    def test_png_structure_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "shot.png"
            invalid = (
                b"\x89PNG\r\n\x1a\n" + b"not a real image",
                PNG[:-3],
                PNG + b"trailing",
                PNG[:29] + bytes([PNG[29] ^ 1]) + PNG[30:],
                PNG[:8] + PNG[33:],
            )
            for data in invalid:
                with self.subTest(size=len(data)):
                    image.write_bytes(data)
                    with self.assertRaises(ValueError):
                        record("playwright", image, root, run_id="1", head_sha=SHA)

    def test_cli_writes_one_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "shot.png"
            image.write_bytes(PNG)
            output = root / "manifest.json"
            self.assertEqual(main(["--engine", "playwright", "--root", str(root),
                "--screenshot", str(image), "--run-id", "7", "--head-sha", SHA,
                "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["run_id"], "7")


# ═══ · cli ergonomics ════════════════════════════════════════════════════════════════════════════════════
# Real process runs for opt-in JSON errors, full flags, and guiding hints.
#
# Default text and exit codes stay compatible with vendored callers. These tests
# spawn the tools; they do not replace the in-process suites.

def run(script: str, argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", str(ROOT / script), *argv],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
    )


def test_gh_ops_help_documents_exit_codes_and_refuses_abbreviations():
    help_run = run("gh_ops.py", ["--help"])
    assert help_run.returncode == 0, help_run.stderr
    assert "Exit codes" in help_run.stdout
    assert "--json-errors" in help_run.stdout
    assert "issue-comments" in help_run.stdout

    abbreviated = run("gh_ops.py", ["--js", "pr-status", "octo/demo", "1"])
    assert abbreviated.returncode == 2
    assert abbreviated.stdout == ""
    assert "unrecognized arguments" in abbreviated.stderr

    plain = run("gh_ops.py", ["pr-status", "bad repo", "1"])
    assert plain.returncode == 2 and plain.stdout == ""
    assert "repository must look like" in plain.stderr

    guided = run("gh_ops.py", ["--json-errors", "pr-status", "bad repo", "1"])
    assert guided.returncode == 2
    assert "repository must look like" in guided.stderr
    payload = json.loads(guided.stdout)
    assert payload["ok"] is False and payload["exit_code"] == 2
    assert "OWNER/REPO" in payload["hint"]
    assert "bad repo" not in payload["hint"]


def test_publish_check_json_against_a_local_server(tmp_path):
    served = tmp_path / "site"
    served.mkdir()
    (served / "index.html").write_text("hello-page\n", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        result = run("publish_check.py", ["--json", "--site", base, "index.html", "--contains", "hello-page", "--no-bust"])
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["verdict"] == "ok"
    assert any(row["state"] == "ok" and "hello-page" in row["line"] for row in payload["checks"])

    missing = run("publish_check.py", ["--json"])
    assert missing.returncode == 2
    assert "at least one target" in missing.stderr


def test_py_matrix_json_when_uv_is_missing():
    help_run = run("py_matrix.py", ["--help"])
    assert help_run.returncode == 0
    assert "Exit codes" in help_run.stdout and "--json" in help_run.stdout
    if shutil.which("uv"):
        return
    result = run("py_matrix.py", ["--json", "3.12"])
    assert result.returncode == 2
    assert "uv not found" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False and payload["exit_code"] == 2
    assert "Install uv" in payload["hint"]


def test_pytest_brief_json_runs_a_real_test(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    result = run("pytest_brief.py", ["--json", "--", "test_sample.py"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["exit_code"] == 0
    assert any("passed" in line for line in payload["lines"])


def test_ci_status_json_explains_a_bad_repository_without_network():
    result = run("ci_status.py", ["not a repo", "--json", "--explain"])
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["error"] == "ValueError"
    assert "invalid repository" in payload["errors"][0]["detail"]
    assert "Exit 2" in payload["errors"][0]["hint"]


def test_gh_workflow_rejects_inputs_without_echoing_them():
    secret = "super-secret-token-value"
    result = run("gh_workflow.py", ["o/r", "ci.yml", "--inputs", "{" + secret])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "invalid_argument"
    assert "JSON object" in payload["hint"]
    assert secret not in result.stdout + result.stderr


# ═══ · evidence optimization ═════════════════════════════════════════════════════════════════════════════
# Real evidence guards must reject invalid inputs when Python removes assertions.

EVIDENCE = ROOT / 'docs/evidence/nano-features'


class EvidenceOptimizationTests(unittest.TestCase):
    def test_measurement_rejects_failed_install_listing(self):
        for optimized in (False, True):
            for folder, script in [('2026-11-01-ironmate', 'measure_node.py'), ('2026-11-01-browser-test-kit', 'measure_btk.py')]:
                with self.subTest(optimized=optimized, script=script):
                    code = r'''
import json, runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import MagicMock, patch
source = Path(sys.argv[1])
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    script = base / source.name
    script.write_bytes(source.read_bytes())
    prepared = base / 'prepared'
    prepared.mkdir()
    (base / 'measurement-prepared.txt').write_text(str(prepared))
    (base / 'run_btk_fixture.py').write_text('fixture')
    (base / 'prototype/tools').mkdir(parents=True)
    (base / 'prototype/tools/nano_node.py').write_text('fixture')
    snapshot = {'package.json': b'{"dependencies":{}}', 'package-lock.json': b'{"packages":{}}'}
    nano = types.SimpleNamespace(COMMIT='fixed', BTK_COMMIT='fixed', BTK_FEATURE='fixed',
                                 BTK_SOURCE_BLOBS={}, read_snapshot=lambda *a, **kw: snapshot, validate_original=lambda *a, **kw: None)
    g = runpy.run_path(str(script))['main'].__globals__
    g['platform'].platform()
    def run(args, **kwargs):
        if args[1] == 'ci': return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        if args[1] == 'ls': return types.SimpleNamespace(returncode=17, stdout='not-json', stderr='listing rejected sentinel')
        raise RuntimeError('unexpected subprocess')
    with patch.object(g['importlib'].util, 'spec_from_file_location', return_value=types.SimpleNamespace(loader=MagicMock())), \
         patch.object(g['importlib'].util, 'module_from_spec', return_value=nano), \
         patch.object(g['subprocess'], 'check_output', return_value='test-runtime'), \
         patch.object(g['subprocess'], 'run', side_effect=run), \
         patch.object(g['shutil'], 'which', return_value='/fake/npm'), \
         patch.object(sys, 'argv', [str(script), '--execute', '--exclusive-window']):
        try: g['main']()
        except AssertionError as exc:
            if str(exc) != 'listing rejected sentinel': raise
        else: raise RuntimeError('failed listing accepted')
    work = Path((base / 'latest-measurement.txt').read_text().strip())
    receipt = json.loads((work / 'receipt.json').read_text())
    if receipt['complete'] or receipt['error'] != 'listing rejected sentinel':
        raise RuntimeError('failure receipt changed')
    print('failed listing rejected; incomplete receipt preserved')
'''
                    result = subprocess.run([sys.executable, '-S', *(['-O'] if optimized else []), '-c', code,
                                             str(EVIDENCE / folder / script)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('failed listing rejected', result.stdout)

    def test_offline_continuation_rejects_invalid_install_receipt(self):
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                code = r'''
import json, runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import MagicMock, patch
script = sys.argv[1]
g = runpy.run_path(script)['main'].__globals__
with tempfile.TemporaryDirectory() as tmp:
    prior = Path(tmp)
    (prior / 'receipt.json').write_text(json.dumps({'installs': []}))
    nano = types.SimpleNamespace()
    with patch.object(g['importlib'].util, 'spec_from_file_location', return_value=types.SimpleNamespace(loader=MagicMock())), \
         patch.object(g['importlib'].util, 'module_from_spec', return_value=nano), \
         patch.object(g['tempfile'], 'mkdtemp', side_effect=RuntimeError('work created before validation')), \
         patch.object(sys, 'argv', [script, '--execute', '--exclusive-window', '--installed-trials', tmp]):
        g['main']()
'''
                result = subprocess.run([sys.executable, '-S', *(['-O'] if optimized else []), '-c', code,
                                         str(EVIDENCE / '2026-11-01-ironmate/measure_node_offline.py')], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1)
                self.assertIn('AssertionError', result.stderr)
                self.assertNotIn('work created before validation', result.stderr)
                self.assertEqual(result.stdout, '')

    def test_pptx_oracle_accepts_valid_and_rejects_invalid_zip(self):
        # Load the actual pure inspector without executing the historical Node workload.
        path = EVIDENCE / '2026-11-01-ironmate/run_pptx_fixture.py'
        tree = ast.parse(path.read_text())
        definitions = ast.unparse(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[]))
        for optimized in (False, True):
            for mutation in ('none', 'duplicate', 'traversal', 'missing', 'external', 'dangling'):
                with self.subTest(optimized=optimized, mutation=mutation):
                    code = r'''
import hashlib, json, posixpath, sys, tempfile, warnings
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import ZipFile
exec(compile(sys.argv[1], 'pptx-inspector', 'exec'))
mutation = sys.argv[2]
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'sample.pptx'
    with warnings.catch_warnings(), ZipFile(path, 'w') as z:
        warnings.simplefilter('ignore', UserWarning)
        z.writestr('[Content_Types].xml', '<Types><Default Extension="xml"/><Default Extension="rels"/></Types>')
        if mutation != 'missing': z.writestr('ppt/presentation.xml', '<presentation/>')
        if mutation == 'duplicate': z.writestr('ppt/presentation.xml', '<presentation/>')
        if mutation == 'traversal': z.writestr('../outside.xml', '<outside/>')
        if mutation in ('external', 'dangling'):
            z.writestr('_rels/.rels', '<Relationships><Relationship Id="one" Target="missing.xml"' + (' TargetMode="External"' if mutation == 'external' else '') + '/></Relationships>')
    result = inspect(path)
    if result['file_count'] != 2: raise RuntimeError('wrong valid package inventory')
    print('valid package accepted')
'''
                    result = subprocess.run([sys.executable, '-S', *(['-O'] if optimized else []), '-c', code, definitions, mutation],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0 if mutation == 'none' else 1, result.stderr)
                    self.assertEqual(result.stdout, 'valid package accepted\n' if mutation == 'none' else '')
                    if mutation != 'none': self.assertIn('AssertionError', result.stderr)


# ═══ · fleet check ═══════════════════════════════════════════════════════════════════════════════════════
# Shared child contract checks (vendored as vendor/fleet_check.py); real files and subprocesses, no network.

import fleet_check


def _lock_row(root: Path, where: str, data: bytes, commit: str = "a" * 40) -> dict:
    (root / where).parent.mkdir(parents=True, exist_ok=True)
    (root / where).write_bytes(data)
    blob = __import__("hashlib").sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return {"destination": where, "repository": "o/" + Path(where).suffix[1:], "source": Path(where).name, "ref": commit, "commit": commit, "blob_sha": blob,
            "sha256": __import__("hashlib").sha256(data).hexdigest()}


def test_fleet_check_lock_verifies_bytes_and_accepted_commits(tmp_path):
    rows = [_lock_row(tmp_path, "python/vendor/a.py", b"print(1)\n"), _lock_row(tmp_path, "vendor/b.cjs", b"//\n", "b" * 40)]
    (tmp_path / "python").mkdir(exist_ok=True)
    (tmp_path / "python/vendor.lock.json").write_text(json.dumps({"schema": "vendor-lock/1", "files": rows}))
    found = fleet_check.check_lock(tmp_path, "python/vendor.lock.json", {"vendor/b.cjs": "b" * 40})
    assert sorted(found) == ["python/vendor/a.py", "vendor/b.cjs"]
    with pytest.raises(AssertionError, match="vendor/b.cjs"):
        fleet_check.check_lock(tmp_path, "python/vendor.lock.json", {"vendor/b.cjs": "c" * 40})
    # provenance: listed destinations match, and a listed repository has no unlisted row
    rows.append(_lock_row(tmp_path, "vendor/c.cjs", b"//c\n"))
    (tmp_path / "python/vendor.lock.json").write_text(json.dumps({"schema": "vendor-lock/1", "files": rows}))
    cjs = {"vendor/b.cjs": ("o/cjs", "b.cjs"), "vendor/c.cjs": ["o/cjs", "c.cjs"]}
    for sources in ({}, {"python/vendor/a.py": ("o/py", "a.py")}, cjs, {**cjs, "python/vendor/a.py": ("o/py", "a.py")}):
        assert len(fleet_check.check_lock(tmp_path, "python/vendor.lock.json", sources=sources)) == 3
    for sources, message in (({"vendor/b.cjs": ("o/cjs", "b.cjs")}, r"unlisted rows from a listed repository: \['vendor/c.cjs'\]"),
                             ({**cjs, "vendor/b.cjs": ("o/cjs", "other.cjs")}, "vendor/b.cjs"),
                             ({**cjs, "vendor/b.cjs": ("o/other", "b.cjs")}, "vendor/b.cjs"),
                             ({**cjs, "vendor/d.cjs": ("o/cjs", "d.cjs")}, "vendor/d.cjs")):
        with pytest.raises(AssertionError, match=message):
            fleet_check.check_lock(tmp_path, "python/vendor.lock.json", sources=sources)
    (tmp_path / "python/vendor/a.py").write_bytes(b"print(2)\n")
    with pytest.raises(AssertionError, match="python/vendor/a.py"):
        fleet_check.check_lock(tmp_path, "python/vendor.lock.json")
    rows[0]["ref"] = "main"
    (tmp_path / "python/vendor.lock.json").write_text(json.dumps({"schema": "vendor-lock/1", "files": rows}))
    with pytest.raises(AssertionError, match="python/vendor/a.py"):
        fleet_check.check_lock(tmp_path, "python/vendor.lock.json")


def test_fleet_check_parent_refs_keep_mutable_and_missing_refs(tmp_path):
    flows = tmp_path / ".github/workflows"
    flows.mkdir(parents=True)
    parent = fleet_check.PARENT
    (flows / "ci.yml").write_text(f"""jobs:
  a:
    uses: {parent}/.github/workflows/x.yml@{'1' * 40}
  b:
    uses: '{parent}/.github/workflows/y.yml@main'
  c:
    steps:
      - uses: actions/checkout@v7
        with:
          repository: {parent}
          ref: '{'2' * 40}'
      - uses: actions/checkout@v7
        with:
          repository: "{parent}"
      - uses: actions/checkout@v7
        with:
          repository: {parent}
          ref: ${{{{ steps.parent.outputs.sha }}}}
          path: .vendor-sync-tools
""", encoding="utf-8")
    assert fleet_check.parent_refs(tmp_path) == ["1" * 40, "main", "2" * 40, None, "${{ steps.parent.outputs.sha }}"]
    assert fleet_check.parent_refs(tmp_path, skip=(".vendor-sync-tools",)) == ["1" * 40, "main", "2" * 40, None]
    assert fleet_check.parent_refs(tmp_path, only=(".vendor-sync-tools",)) == ["${{ steps.parent.outputs.sha }}"]
    assert fleet_check.parent_refs(tmp_path, only=(None, ".vendor-sync-tools"), skip=(".vendor-sync-tools",)) == ["2" * 40, None]
    assert fleet_check.parent_refs(tmp_path, only=(".no-such-path",)) == []
    assert fleet_check.repo_root(flows / "ci.yml") == tmp_path.resolve()


def test_fleet_check_actions_need_full_shas_and_version_comments(tmp_path):
    flows = tmp_path / ".github/workflows"
    flows.mkdir(parents=True)
    parent, sha, other = fleet_check.PARENT, "1" * 40, "2" * 40
    good = f"""jobs:
  a:
    uses: {parent}/.github/workflows/x.yml@{sha}
  b:
    steps:
      - uses: actions/checkout@{sha} # v7.0.1
      - name: quoted
        uses: "actions/cache/save@{other}"  # v6
      - uses: ./.github/actions/local
      - uses: docker://alpine:3
"""
    (flows / "ci.yml").write_text(good, encoding="utf-8")
    rows = [(f"{parent}/.github/workflows/x.yml", sha, None), ("actions/checkout", sha, "v7.0.1"), ("actions/cache/save", other, "v6")]
    assert fleet_check.check_actions(tmp_path) == rows
    pins = {"actions/checkout": sha, "actions/cache/save": other + " # v6"}
    assert fleet_check.check_actions(tmp_path, pins) == rows
    for wrong, message in (({**pins, "actions/checkout": other}, "actions/checkout@1+ # v7.0.1: not the reviewed pin"),
                           ({"actions/checkout": sha}, "cache/save.*not the reviewed pin"),
                           ({**pins, "actions/cache/save": other + " # v7"}, "cache/save.*not the reviewed pin")):
        with pytest.raises(AssertionError, match=message):
            fleet_check.check_actions(tmp_path, wrong)
    for broken, message in (
        (good.replace(f"checkout@{sha} # v7.0.1", "checkout@v7"), "not a full SHA"),
        (good.replace(f"checkout@{sha} # v7.0.1", f"checkout@{sha[:7]} # v7.0.1"), "not a full SHA"),
        (good.replace(f"x.yml@{sha}", "x.yml@main"), "not a full SHA"),
        (good.replace(" # v7.0.1", ""), "other.yaml: - uses: actions/checkout@1+: no version comment"),
        (good.replace(" # v7.0.1", " # latest"), "no version comment"),
        # only the parent's reusable workflows pass on the SHA alone
        (good.replace(parent + "/.github", "myon-bioinformatics/Aoi/.github"), "Aoi/.github/workflows/x.yml@1+: no version comment"),
        (good.replace(parent + "/.github/workflows/x.yml", parent + "/actions/x"), "no version comment"),
    ):
        assert broken != good
        (flows / "other.yaml").write_text(broken, encoding="utf-8")
        with pytest.raises(AssertionError, match=message):
            fleet_check.check_actions(tmp_path)
        assert fleet_check.check_actions(tmp_path, workflow="ci.yml") == rows


def test_fleet_check_vendor_staging_runs_the_workflow_commands(tmp_path):
    rows = [_lock_row(tmp_path, "vendor/a.py", b"print(1)\n"), _lock_row(tmp_path, "vendor/a.txt", b"MIT\n")]
    (tmp_path / "vendor.lock.json").write_text(json.dumps({"schema": "vendor-lock/1", "files": rows}), encoding="utf-8")
    (tmp_path / "legacy.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".github/workflows").mkdir(parents=True)
    good = ("jobs:\n  test:\n    steps:\n      - name: Stage canonical vendor evidence\n        if: always()\n        shell: bash\n"
            "        run: >-\n          python -S .vendor-sync-tools/vendor_stage.py\n"
            "          --manifest vendor.lock.json --kind locked --output build/vendor-evidence-test\n"
            "          --runtime-evidence vendor-enrollment.json --legacy-evidence legacy.json\n"
            "      - name: Preserve vendor lock used by this run\n        if: always()\n        uses: actions/upload-artifact@v7\n"
            "        with:\n          name: vendor-lock\n          path: build/vendor-evidence-test\n          retention-days: 14\n")
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text(good, encoding="utf-8")
    tool = ROOT / "vendor_stage.py"
    command = " ".join(line.strip() for line in good.split("run: >-\n")[1].split("      - name")[0].splitlines())
    assert fleet_check.vendor_staging(tmp_path, tool) == [command]
    assert sorted(p.name for p in tmp_path.iterdir()) == [".github", "legacy.json", "vendor", "vendor.lock.json"]  # the child stays untouched
    with pytest.raises(AssertionError, match="no staging step, or no .vendor-sync-tools/vendor_stage.py"):
        fleet_check.vendor_staging(tmp_path)
    for broken, message in (
        (good.replace("        if: always()\n        shell", "        shell"), "always run and fail closed"),
        (good.replace("        shell: bash", "        continue-on-error: true"), "always run and fail closed"),
        (good.replace("          path: build/vendor-evidence-test", "          path: build/other"), "staged output is not uploaded"),
        (good.replace(" --legacy-evidence legacy.json", " --promotion-receipt vendor-promotion.json"), "--promotion-receipt"),
        (good.replace("Stage canonical vendor evidence", "Stage"), "no staging step"),
    ):
        assert broken != good
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(AssertionError, match=message):
            fleet_check.vendor_staging(tmp_path, tool)
    path.write_text(good, encoding="utf-8")
    (tmp_path / "vendor/a.py").write_bytes(b"print(2)\n")  # the checked-in bytes left the lock: staging refuses them
    with pytest.raises(AssertionError, match="--manifest vendor.lock.json"):
        fleet_check.vendor_staging(tmp_path, tool)


def test_fleet_check_python_floor_agrees_across_project_lock_and_runtime_job(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    files = {
        "pyproject.toml": '[build-system]\nrequires-python = ">=3.8"\n[project]\nname = "x"\nrequires-python = ">=3.11"\n'
                          '[tool.uv.dependency-groups]\ndev = { requires-python = ">=3.12" }\n',
        "uv.lock": 'version = 1\nrequires-python = ">=3.11"\n\n[[package]]\nname = "x"\n',
        ".github/workflows/runtime-compatibility.yml":
            'jobs:\n  runtime:\n    strategy:\n      fail-fast: false\n      matrix:\n        os: [ubuntu-latest]\n'
            '        python: ["3.12", "3.11", "3.14"]\n        include:\n          - os: windows-latest\n            python: "3.11"\n'
            '    steps:\n      - run: python -S -m unittest discover -s tests -p test_core.py -v\n',
    }
    def write(name, old="", new=""):
        for key, text in files.items():
            (tmp_path / key).write_text(text.replace(old, new) if key == name else text, encoding="utf-8")
            assert key != name or not old or old in text
    write("")
    assert fleet_check.python_floor(tmp_path) == ("3.11", ["3.12", "3.11", "3.14"])
    flow = ".github/workflows/runtime-compatibility.yml"
    for name, old, new, message in (
        ("uv.lock", ">=3.11", ">=3.12", "uv.lock requires-python differs"),
        ("uv.lock", "requires-python", "python", "uv.lock requires-python differs"),
        ("pyproject.toml", '\nrequires-python = ">=3.11"', "", r"\[project\] requires-python"),
        ("pyproject.toml", '">=3.11"', '"==3.11.*"', r"\[project\] requires-python"),
        (flow, '"3.11", ', "", "does not start at the floor 3.11"),
        (flow, '"3.12", ', '"3.9", ', "does not start at the floor 3.11"),
        (flow, 'python: "3.11"', 'python: "3.13"', r"include rows outside the matrix list: \['3.13'\]"),
        (flow, "python -S -m", "python -m", "python -S -m unittest"),
        (flow, "    steps:", "    continue-on-error: true\n    steps:", "fail closed"),
        (flow, "  runtime:", "  other:", "missing job runtime"),
    ):
        write(name, old, new)
        with pytest.raises(AssertionError, match=message):
            fleet_check.python_floor(tmp_path)
    write(flow, "runtime:", "stdlib:")
    assert fleet_check.python_floor(tmp_path, "stdlib")[0] == "3.11"


def test_fleet_check_workflow_job_and_run_main(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text("jobs:\n  test:\n    runs-on: x\n  lint:\n    runs-on: y\n")
    assert fleet_check.workflow_job(tmp_path, "test") == "    runs-on: x\n"
    with pytest.raises(AssertionError, match="missing job build"):
        fleet_check.workflow_job(tmp_path, "build")
    (tmp_path / "bench.py").write_text(
        "import sys\ndef answer():\n    return 42\n"
        "def main():\n    if answer() != 42:\n        sys.exit('result mismatch')\n"
        "    print({'optimize': sys.flags.optimize, 'nosite': sys.flags.no_site})\n")
    for optimized in (False, True):
        ok = fleet_check.run_main(tmp_path, "bench.py", optimized=optimized)
        assert (ok.returncode, ok.stdout) == (0, f"{{'optimize': {int(optimized)}, 'nosite': 1}}\n"), ok.stderr
        bad = fleet_check.run_main(tmp_path, "bench.py", "g['answer'] = lambda: 0", optimized=optimized)
        assert (bad.returncode, bad.stdout) == (1, "") and "result mismatch" in bad.stderr


def test_fleet_check_workflow_job_setup_precedes_python_and_uv(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    setup, version = "astral-sh/setup-uv@", 'python-version: "${{ matrix.python }}"'
    good = ("jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v7\n        with:\n          python-version: \"3.9\"\n"
            "      - name: Set up uv\n        uses: astral-sh/setup-uv@" + "1" * 40 + " # v10.3.3\n        with:\n"
            "          version: \"latest\"\n          python-version: \"${{ matrix.python }}\"\n          activate-environment: true\n"
            "      - run: python -m compileall -q .\n      - name: tests\n        run: |\n          uv run --locked pytest -q\n"
            "        env:\n          enable-cache: true\n  lint:\n    steps:\n      - run: echo\n")
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text(good, encoding="utf-8")
    assert fleet_check.workflow_job(tmp_path, "test", setup=setup, inputs=(version, "activate-environment: true")) \
        == fleet_check.workflow_job(tmp_path, "test")
    for inputs, message in ((('python-version: "3.9"',), "step lacks python-version"),   # another step's input
                            (("enable-cache: true",), "step lacks enable-cache")):       # a later step's key
        with pytest.raises(AssertionError, match=message):
            fleet_check.workflow_job(tmp_path, "test", setup=setup, inputs=inputs)
    moved = "      - run: python -m compileall -q .\n"
    for broken, message in (
        (good.replace(moved, "").replace("      - name: Set up uv", moved + "      - name: Set up uv"), "python runs before"),
        (good.replace("      - uses: actions/checkout@v7", "      - run: |\n          uv --version\n      - uses: actions/checkout@v7"), "uv runs before"),
        (good.replace("      - uses: actions/checkout@v7", "      - run: echo \"$(python3 -c 'print(1)')\"\n      - uses: actions/checkout@v7"), "python3 -c runs before"),
        (good.replace("      - name: tests", "      - uses: astral-sh/setup-uv@v10\n      - name: tests"), "exactly one uses: astral-sh/setup-uv@"),
        (good.replace("uses: astral-sh/setup-uv@", "uses: actions/setup-python@"), "exactly one uses: astral-sh/setup-uv@"),
        (good.replace(moved, "").replace("          uv run --locked pytest -q", "          pytest -q"), "no python or uv command"),
    ):
        assert broken != good
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(AssertionError, match=message):
            fleet_check.workflow_job(tmp_path, "test", setup=setup, inputs=(version,))


def test_fleet_check_workflow_job_vendor_dispatch_verifies_without_record(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    good = ("jobs:\n  vendor-dispatch:\n    steps:\n      # Verify mode does not --record.\n      - env:\n"
            "          PARENT_REPOSITORY_ID: \"638801160\"\n        run: |\n"
            "          sha=$(git ls-remote \"$url\" refs/heads/main)\n          test -f .parent/pilot/vendor_dispatch.py\n"
            "          python \"$GITHUB_WORKSPACE/.parent/pilot/vendor_dispatch.py\" \\\n"
            "            --manifest \"$GITHUB_WORKSPACE/vendor.lock.json\" | tee \"$out\"\n")
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text(good, encoding="utf-8")
    assert fleet_check.workflow_job(tmp_path, "vendor-dispatch", dispatch_lock="vendor.lock.json") == good.split("vendor-dispatch:\n")[1]
    with pytest.raises(AssertionError, match="no --manifest python/vendor.lock.json"):
        fleet_check.workflow_job(tmp_path, "vendor-dispatch", dispatch_lock="python/vendor.lock.json")
    for broken, message in (
        (good.replace("638801160", "1"), "lacks PARENT_REPOSITORY_ID"),
        (good.replace("git ls-remote", "git rev-parse"), "lacks git ls-remote"),
        (good.replace("refs/heads/main", "refs/heads/dev"), "lacks refs/heads/main"),
        (good.replace(" | tee", " --record | tee"), "without --record or -S"),
        (good.replace("          test -f", "          python x.py --record\n          test -f"), "without --record or -S"),
        (good.replace('python "$GITHUB', 'python -S "$GITHUB'), "without --record or -S"),
        (good.replace('python "$GITHUB', '# python "$GITHUB'), "vendor_dispatch.py is not run"),
        (good.replace("--manifest", "--lock"), "no --manifest vendor.lock.json"),
    ):
        assert broken != good
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(AssertionError, match=message):
            fleet_check.workflow_job(tmp_path, "vendor-dispatch", dispatch_lock="vendor.lock.json")


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="the guard is run on POSIX lanes only")
def test_fleet_check_workflow_job_runs_the_node_floor_guard(tmp_path, monkeypatch):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    good = ("jobs:\n  lint:\n    runs-on: y\n  vendor-dispatch:\n    steps:\n      - uses: actions/setup-node@v7\n        with:\n"
            "          node-version: \"26\"\n      - run: |\n          set -euo pipefail\n"
            "          node_major=$(node -p \"process.versions.node.split('.')[0]\")\n"
            "          if [[ \"$node_major\" -lt 26 ]]; then\n            echo \"node ${node_major} is older than 26\" >&2\n"
            "            exit 1\n          fi\n          python - <<'PY'\n          if True:\n              pass\n          PY\n"
            "  test:\n    runs-on: x\n")
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text(good, encoding="utf-8")
    body = fleet_check.workflow_job(tmp_path, "vendor-dispatch", node_floor=26)
    assert body == fleet_check.workflow_job(tmp_path, "vendor-dispatch") and "node_major" in body
    for broken, message in (
        (good.replace("            exit 1\n", ""), r"vendor-dispatch', 24"),             # the guard only warns
        (good.replace("-lt 26", "-lt 24"), "no node_major guard at 26"),                 # the guard's floor drifted
        (good.replace('node-version: "26"', "node-version: '24'"), "no setup of Node 26"),
        (good.replace('if [[ "$node_major" -lt 26 ]]', 'if [[ "$node_major" -gt 26 ]]'), "no node_major guard at 26"),
        (good.replace("-lt 26 ]]; then", "-lt 26 ]] && false; then"), "no node_major guard at 26"),
    ):
        assert broken != good
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(AssertionError, match=message):
            fleet_check.workflow_job(tmp_path, "vendor-dispatch", node_floor=26)
        assert "runs-on: x" in fleet_check.workflow_job(tmp_path, "test")
    with pytest.raises(AssertionError, match="no setup of Node 26"):
        fleet_check.workflow_job(tmp_path, "test", node_floor=26)
    path.write_text(good, encoding="utf-8")
    monkeypatch.setattr(fleet_check, "os", types.SimpleNamespace(name="nt"))           # Windows: text only, no bash
    monkeypatch.setattr(fleet_check.subprocess, "run", lambda *a, **k: pytest.fail("bash was run on Windows"))
    assert fleet_check.workflow_job(tmp_path, "vendor-dispatch", node_floor=26) == body


def test_fleet_check_node_gate_skips_locally_and_fails_in_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE", str(tmp_path / "no-such-node"))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    fleet_check.node.cache_clear()
    try:
        assert fleet_check.node() is None
        with pytest.raises(AssertionError, match="Node 26"):
            fleet_check.node_parity(tmp_path, "tool.py", "TOOL_PYTHON")
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_JOB", "portability")
        fleet_check.node.cache_clear()
        assert fleet_check.node() is None
        monkeypatch.setenv("GITHUB_JOB", "test")
        fleet_check.node.cache_clear()
        with pytest.raises(AssertionError, match="required in CI job test"):
            fleet_check.node()
        assert fleet_check.node(ci_jobs=("node-parity",)) is None
        with pytest.raises(AssertionError, match="required in CI job test"):
            fleet_check.node(ci_jobs=None)
        monkeypatch.setenv("GITHUB_JOB", "portability")
        with pytest.raises(AssertionError, match="required in CI job portability"):
            fleet_check.node(ci_jobs=None)
        monkeypatch.delenv("GITHUB_ACTIONS")
        fleet_check.node.cache_clear()
        assert fleet_check.node(ci_jobs=None) is None
    finally:
        fleet_check.node.cache_clear()


def test_fleet_check_node_parity_runs_the_same_python(tmp_path, monkeypatch):
    # The parent's pytest jobs have no Node 26; the CI gate is for children's parity jobs.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    fleet_check.node.cache_clear()
    if fleet_check.node() is None:
        fleet_check.node.cache_clear()
        pytest.skip("Node 26+ not available (set NODE)")
    (tmp_path / "vendor").mkdir()
    shutil.copy(ROOT / "py_launch.cjs", tmp_path / "vendor/py_launch.cjs")
    (tmp_path / "tool.py").write_text(
        "import json, sys\nprint(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'nosite': sys.flags.no_site}))\n"
        "sys.exit(3)\n", encoding="utf-8")
    via_node, via_python = fleet_check.node_parity(tmp_path, "tool.py", "TOOL_PYTHON", "a b", "--x=ü", input="こんにちは\n")
    assert via_node == via_python
    assert via_python[0] == 3
    assert json.loads(via_python[1]) == {"argv": ["a b", "--x=ü"], "stdin": "こんにちは\n", "nosite": 1}


def test_fleet_check_launcher_contract_holds_for_the_parent_launcher(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    fleet_check.node.cache_clear()
    if fleet_check.node() is None:
        fleet_check.node.cache_clear()
        pytest.skip("Node 26+ not available (set NODE)")
    (tmp_path / "vendor").mkdir()
    shutil.copy(ROOT / "py_launch.cjs", tmp_path / "vendor/py_launch.cjs")
    fleet_check.launcher_contract(tmp_path)
    source = (ROOT / "py_launch.cjs").read_text(encoding="utf-8")
    for old, new, message in (
        (source, "process.exit(0)\n", ""),
        # library run() drops the discovery flags: only the hostile sitecustomize check sees it
        ("  const resolved = resolvePython(options);", "  const resolved = resolvePython({ python: options.python });",
         r"'run', \{'noSite': True\}"),
        ("terminated by signal ${signal}`);\n      wrapped.code", "terminated by signal ${signal} (forwarded)`);\n      wrapped.code",
         r"'SIGINT', 'SIGTERM', '', 0, 2, b'', b'[^']*\(forwarded\)"),
        # item by item from BrowserTestKit's py_launch.test.cjs: Node bounds, JSON ENOINTERPRETER, interpreter prefix
        ('    err.code = "ENODEVERSION";', '    err.code = "EOLDNODE";', "assertNodeVersion bounds or interpreter prefix"),
        ('${result.error.message}`, {\n          code: "ENOINTERPRETER",', '${result.error.message}`, {\n          code: "ENOENT",', "no-such-python"),
        ("    ...pythonArgv(\"-c\", [VERSION_PROBE], Boolean(opts.noSite), Boolean(opts.isolated)),",
         "    ...pythonArgv(\"-c\", [VERSION_PROBE], Boolean(opts.noSite), Boolean(opts.isolated)).reverse(),",
         "assertNodeVersion bounds or interpreter prefix"),
        # SIGTERM no longer forwarded: it ends the launcher and orphans the Python child
        ('  process.on("SIGTERM", forwardSignal);\n', "", r"'SIGINT', 'SIGTERM', '', 0, -15, b'', b''"),
        # a second stderr line after the JSON error
        ('      wrapped.hint = launcherHint({ code: "ESIGNAL" });\n      writeLauncherError(wrapped, opts);\n',
         '      wrapped.hint = launcherHint({ code: "ESIGNAL" });\n      writeLauncherError(wrapped, opts);\n      console.error("extra");\n',
         r"'SIGINT', 'SIGTERM', '', 0, 2, b'', b'[^']*extra"),
    ):
        assert source.count(old) == 1, old
        (tmp_path / "vendor/py_launch.cjs").write_text(source.replace(old, new), encoding="utf-8")
        with pytest.raises(AssertionError, match=message or None):
            fleet_check.launcher_contract(tmp_path)

# ═══ · git commit ════════════════════════════════════════════════════════════════════════════════════════

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_COMMIT_SCRIPT = ROOT / "gh_ops.py"


class FakeRun:
    """Records argv/kwargs and replies in order: (returncode, stdout, stderr)."""

    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.calls: list = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        code, stdout, stderr = self.replies.pop(0)
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


def git_env(**overrides: str) -> dict:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
              GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
              GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
    env.update(overrides)
    return env


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=git_env())
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True, env=git_env())
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "initial"], check=True, env=git_env())


def run_cli(root: Path, *args: str, write: bool = True) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-S", str(GIT_COMMIT_SCRIPT), "git-commit", *args]
    if write:
        argv.append("--write")
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", cwd=str(root), env=git_env())


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                          env=git_env()).stdout


def sha_line(stdout: str) -> str:
    matches = [line for line in stdout.splitlines() if SHA_RE.match(line)]
    assert len(matches) == 1, f"expected exactly one SHA line, got: {stdout!r}"
    return matches[0]


def test_read_message_strips_trailing_newlines(tmp_path: Path) -> None:
    path = tmp_path / "msg.txt"
    path.write_text("Subject\n\nBody\n\n\n", encoding="utf-8")
    assert gh_ops.git_commit_read_message(path) == "Subject\n\nBody"


def test_build_message_appends_new_trailers_with_blank_line() -> None:
    assert gh_ops.git_commit_build_message("Subject\n\nBody.\n", ["Key: value"]) == "Subject\n\nBody.\n\nKey: value\n"


def test_build_message_skips_trailer_already_present_in_base() -> None:
    base = "Subject\n\nKey: value\n"
    assert gh_ops.git_commit_build_message(base, ["Key: value"]) == base


def test_build_message_dedups_repeated_trailer_within_same_call() -> None:
    out = gh_ops.git_commit_build_message("Subject\n", ["Key: value", "Key: value"])
    assert out.count("Key: value") == 1


def test_staged_files_parses_name_only_output() -> None:
    run = FakeRun([(0, "a.py\nb.py\n", "")])
    assert gh_ops.git_commit_staged_files(run=run) == ["a.py", "b.py"]


def test_push_with_retry_retries_only_network_errors_then_succeeds() -> None:
    run = FakeRun([
        (1, "", "fatal: unable to access 'https://x': Could not resolve host: x"),
        (1, "", "fatal: unable to access 'https://x': Connection timed out"),
        (0, "done\n", ""),
    ])
    sleeps: list = []
    ok, output = gh_ops.git_commit_push_with_retry(run=run, sleep=sleeps.append)
    assert ok is True
    assert "done" in output
    assert sleeps == [2.0, 4.0]


def test_dry_run_does_not_touch_git(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "message.txt").write_text("msg\n", encoding="utf-8")
    done = run_cli(tmp_path, "--message-file", "message.txt", "a.txt", write=False)
    assert done.returncode == 0
    assert "dry run" in done.stdout


def test_cli_stages_only_named_files_prints_status_diff_and_sha(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "message.txt").write_text("Update files\n", encoding="utf-8")
    done = run_cli(tmp_path, "--message-file", "message.txt", "a.txt", "b.txt",
                   "--trailer", "Co-Authored-By: Claude <noreply@anthropic.com>")
    assert done.returncode == 0, done.stderr
    assert "M  a.txt" in done.stdout
    assert "A  b.txt" in done.stdout
    assert "2 files changed" in done.stdout
    assert sha_line(done.stdout) == git_output(tmp_path, "rev-parse", "HEAD").strip()


def test_cli_refuses_when_other_files_already_staged(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
    (tmp_path / "extra.txt").write_text("extra\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "extra.txt"], check=True, env=git_env())
    (tmp_path / "message.txt").write_text("msg\n", encoding="utf-8")
    done = run_cli(tmp_path, "--message-file", "message.txt", "a.txt")
    assert done.returncode == 2
    assert "extra.txt" in done.stderr


def test_cli_push_against_a_real_remote(tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    work.mkdir()
    init_repo(work)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(bare)], check=True, env=git_env())
    subprocess.run(["git", "-C", str(work), "push", "-q", "-u", "origin", "HEAD"], check=True, env=git_env())
    branch = git_output(work, "branch", "--show-current").strip()
    (work / "a.txt").write_text("changed\n", encoding="utf-8")
    (work / "message.txt").write_text("second commit\n", encoding="utf-8")
    done = run_cli(work, "--message-file", "message.txt", "a.txt", "--push")
    assert done.returncode == 0, done.stderr
    remote_sha = subprocess.run(["git", "-C", str(bare), "rev-parse", branch], capture_output=True, text=True,
                                check=True).stdout.strip()
    assert sha_line(done.stdout) == remote_sha


# ═══ · git inspector ═════════════════════════════════════════════════════════════════════════════════════
# Parent exposes compatibility aliases, not a second Git implementation.

def test_every_public_reader_delegates():
    for name in gi.__all__:
        assert getattr(gi, name) is getattr(gi._ghi, "git_" + name)
    assert gi.GitInspectionError is gi._ghi.GitInspectionError


def test_legacy_status_and_diff_work_without_mutation(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert gi.status(tmp_path) == {"clean": True, "records": [], "truncated": False}
    (tmp_path / "日本語.txt").write_text("text")
    assert not gi.status(tmp_path)["clean"]
    assert gi.diff(tmp_path)["truncated"] is False
    assert not (tmp_path / ".git/index").exists()


# ═══ · preflight ═════════════════════════════════════════════════════════════════════════════════════════

def test_observation_uses_adopted_ghi_and_keeps_no_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.ghi, 'capabilities', lambda: {'git': True, 'gh': False, 'token_present': False})
    monkeypatch.setattr(preflight.ghi, 'git_status', lambda root: {'clean': False, 'truncated': False,
                                                               'records': [{'path': 'sensitive.txt'}]})
    value = preflight.observe(tmp_path, required=['git', 'git', 'gh'], require_clean=True, require_token=True)
    assert not value['ready']
    assert [r['check'] for r in value['checks']] == ['git', 'gh', 'clean_worktree', 'token_present']
    assert value['worktree']['changed_records'] == 1
    assert 'sensitive.txt' not in json.dumps(value)


def test_runtime_facts_are_whitelisted_and_not_live(tmp_path, monkeypatch):
    path = tmp_path / 'facts.json'
    data = {'schema_version': 1, 'os': {'system': 'Linux', 'release': 'test', 'machine': 'x86_64'},
            'runtime': {'implementation': 'CPython', 'version': '3.9.0', 'bits': 64},
            'host': {'name': 'private'}, 'directory': {'entries': ['secret']}}
    path.write_text(json.dumps(data))
    facts = preflight.runtime_facts(path)
    assert set(facts) == {'os', 'runtime'}
    monkeypatch.setattr(preflight.ghi, 'capabilities', lambda: {})
    monkeypatch.setattr(preflight.ghi, 'git_status', lambda root: {'clean': True, 'truncated': False, 'records': []})
    result = preflight.observe(tmp_path, python_min='3', reported_runtime=facts)
    assert result['ready']
    assert result['reported_runtime']['freshness'] == 'not_verified'
    assert 'private' not in json.dumps(result)
    for invalid in ([], {}, {**data, 'schema_version': True}, {**data, 'runtime': {}},
                    {**data, 'os': {**data['os'], 'system': '\nsecret'}}):
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError): preflight.runtime_facts(path)
    path.write_bytes(b' ' * 65537)
    with pytest.raises(ValueError): preflight.runtime_facts(path)


def test_stdlib_cli_exit_codes_with_real_git(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    command = [sys.executable, '-S', str(Path(preflight.__file__)), '--root', str(tmp_path), '--require', 'git']
    result = subprocess.run(command + ['--require-clean', '--python-min', '3'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ready']
    (tmp_path / 'untracked').write_text('x')
    result = subprocess.run(command + ['--require-clean'], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['worktree']['changed_records'] == 1
    for args in (['--python-min', '999'], ['--python-min', 'bad'],
                 ['--runtime-facts', str(tmp_path / 'missing')]):
        result = subprocess.run(command + args, capture_output=True)
        assert result.returncode == (1 if args == ['--python-min', '999'] else 2)
    result = subprocess.run(command[:4] + [str(tmp_path / 'missing')], capture_output=True)
    assert result.returncode == 2
    nested = tmp_path / 'nested.json'
    nested.write_bytes(b'[' * 10000 + b'0' + b']' * 10000)
    result = subprocess.run(command + ['--runtime-facts', str(nested)], capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stdout == ''
    assert result.stderr == 'preflight: ValueError\n'
    explained = subprocess.run(command + ['--runtime-facts', str(nested), '--explain'],
                               capture_output=True, text=True)
    assert explained.returncode == 2 and explained.stdout == ''
    assert explained.stderr.startswith('preflight: ValueError: ')
    assert explained.stderr.endswith('\n') and len(explained.stderr) < 200
    assert '[[' not in explained.stderr


# ═══ · publish check ═════════════════════════════════════════════════════════════════════════════════════
# publish_check: deploy/http/bytes/text/regex checks and exit-code contracts.

spec_publish_check = importlib.util.spec_from_file_location("publish_check", ROOT / "publish_check.py")
pc = importlib.util.module_from_spec(spec_publish_check)
spec_publish_check.loader.exec_module(pc)


@pytest.fixture()
def site(tmp_path):
    served = tmp_path / "served"
    (served / "tools").mkdir(parents=True)
    (served / "tools" / "panel.js").write_text("export const v = 2;\n")
    (served / "index.html").write_text("<script src='tools/panel.js'></script>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/", served
    server.shutdown()


def api_for(sha="abc1234def", state="success"):
    def api(path):
        return [{"id": 7, "sha": sha}] if path.endswith("per_page=1") and "statuses" not in path else [{"state": state}]
    return api


def run_publish_check(argv, **kw):
    kw.setdefault("api", api_for())
    kw.setdefault("sleep", lambda s: None)
    return pc.main(argv, **kw)


def test_all_angles_ok(site, capsys):
    base, served = site
    code = run_publish_check(["--site", base, "tools/panel.js", "", "--local-root", str(served),
                "--contains", "panel.js", "--contains-on", "", "--repo", "o/r", "--commit", "abc1234"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "ok deploy: abc1234 success" in out and "ok bytes:" in out and "ok text: 'panel.js'" in out
    assert out.strip().endswith("verdict=ok")


def test_stale_bytes_and_missing_text_fail(site, tmp_path, capsys):
    base, _served = site
    local = tmp_path / "local" / "tools"
    local.mkdir(parents=True)
    (local / "panel.js").write_text("export const v = 3;\n")
    code = run_publish_check(["--site", base, "tools/panel.js", "--local-root", str(tmp_path / "local"), "--contains", "v = 3"])
    out = capsys.readouterr().out
    assert code == 1 and "stale bytes:" in out and "fail text: 'v = 3'" in out


def test_unreachable_is_its_own_verdict_and_does_not_wait(capsys):
    get = lambda url: (None, b"", "connect_rejected")
    sleeps = []
    code = pc.main(["https://blocked.example/x", "--wait", "100", "--no-bust"], get=get, sleep=sleeps.append)
    out = capsys.readouterr().out
    assert code == 3 and sleeps == [] and "unreachable http: https://blocked.example/x (connect_rejected)" in out


def test_regex_matching_and_filtering(site, capsys):
    base, _served = site
    code = run_publish_check(["--site", base, "tools/panel.js", "--regex", r"export const v = \d+;"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert r"ok regex: 'export const v = \\d+;'" in out

    code = run_publish_check(["--site", base, "tools/panel.js", "--regex", r"nonexistent_pattern_\d+"])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "fail regex:" in out

    code = run_publish_check(["--site", base, "tools/panel.js", "", "--regex", "v = 2", "--regex-on", "tools/panel.js"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "ok regex: 'v = 2'" in out
    assert out.count("ok regex:") == 1


@pytest.mark.parametrize("patterns, expected, states", [
    ([r"v = \d+", "export"], 0, ["ok", "ok"]),
    (["export", "absent"], 1, ["ok", "fail"]),
    (["["], 1, ["fail"]),
    (["a{4294967296}", "export"], 1, ["fail", "ok"]),
    (["(" * 1000, "export"], 1, ["fail", "ok"]),
    (["[", "export", "(", "absent"], 1, ["fail", "ok", "fail", "fail"]),
    (["(?i)EXPORT", ""], 0, ["ok", "ok"]),
])
def test_regex_verdicts(patterns, expected, states, capsys):
    argv = ["https://example.test/panel.js", "--no-bust"]
    for pattern in patterns:
        argv.extend(["--regex", pattern])
    code = run_publish_check(argv, get=lambda url: (200, b"export const v = 2;\n", ""))
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if " regex:" in line]
    assert code == expected
    assert [line.split()[0] for line in lines] == states
    assert captured.err == ""
    assert "Traceback" not in captured.out
    if "[" in patterns:
        assert "fail regex: invalid pattern '['" in captured.out
    assert captured.out.strip().endswith("verdict=" + ("ok" if expected == 0 else "not-yet-or-failed"))


def test_invalid_regex_diagnostic_is_one_line(capsys):
    code = run_publish_check(["https://example.test/", "--regex", "(?x)\n["],
               get=lambda url: (200, b"body", ""))
    captured = capsys.readouterr()
    assert code == 1
    assert len(captured.out.splitlines()) == 3  # HTTP, regex, summary
    assert "fail regex: invalid pattern" in captured.out


def test_regex_on_filters_exact_targets_and_is_repeatable(capsys):
    bodies = {"https://example.test/a": b"wanted", "https://example.test/b": b"wanted",
              "https://example.test/c": b"absent"}
    code = run_publish_check(["--site", "https://example.test/", "a", "b", "c", "--no-bust",
                "--regex", "wanted", "--regex-on", "a", "--regex-on", "b"],
               get=lambda url: (200, bodies[url], ""))
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("ok regex:") == 2
    assert "regex: 'wanted' in https://example.test/c" not in out


@pytest.mark.parametrize("status, error, expected, line", [
    (None, "connect_rejected", 3, "unreachable http:"),
    (404, "", 1, "fail http 404:"),
    (503, "", 1, "fail http 503:"),
])
@pytest.mark.parametrize("pattern", ["body", "["])
def test_regex_skipped_when_http_fails(status, error, expected, line, pattern, capsys):
    code = run_publish_check(["https://example.test/", "--regex", pattern],
               get=lambda url: (status, b"body", error))
    captured = capsys.readouterr()
    assert code == expected
    assert line in captured.out
    assert "regex:" not in captured.out
    assert captured.err == ""


def test_regex_failure_takes_precedence_over_other_unreachable_target(capsys):
    code = run_publish_check(["https://example.test/good", "https://example.test/blocked", "--regex", "["],
               get=lambda url: (None, b"", "blocked") if "/blocked" in url else (200, b"body", ""))
    out = capsys.readouterr().out
    assert code == 1
    assert "fail regex: invalid pattern" in out and "unreachable http:" in out


def test_usage_errors():
    with pytest.raises(SystemExit):
        pc.main([])
    with pytest.raises(SystemExit):
        pc.main(["relative/path.js"])


# ═══ · py matrix ═════════════════════════════════════════════════════════════════════════════════════════

spec_py_matrix = importlib.util.spec_from_file_location("py_matrix", ROOT / "py_matrix.py")
py_matrix = importlib.util.module_from_spec(spec_py_matrix)
assert spec_py_matrix.loader
spec_py_matrix.loader.exec_module(py_matrix)


class FakeRun_py_matrix:
    """Records argv/env and replies per Python version."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        reply = self.replies[argv[argv.index("--python") + 1]]
        if reply == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        code, stdout = reply
        return subprocess.CompletedProcess(argv, code, stdout, "")


def test_exact_uv_argv_and_one_summary_line_per_version(capsys) -> None:
    run = FakeRun_py_matrix({"3.9": (0, "....\n39 passed in 1.61s\n"), "3.13": (0, "39 passed in 1.69s\n")})
    code = py_matrix.main(["3.9", "3.13", "--with", "pytest>=8", "--", "tests/test_gh_ops.py", "-k", "merge"],
                          which=lambda name: "/usr/bin/uv", run=run)
    assert code == 0
    assert capsys.readouterr().out == "py3.9: 39 passed in 1.61s\npy3.13: 39 passed in 1.69s\n"
    argv, kwargs = run.calls[0]
    assert argv == ["uv", "run", "--no-project", "--quiet", "--python", "3.9", "--with", "pytest>=8", "python",
                    "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_gh_ops.py", "-k", "merge"]
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_failure_and_timeout_are_reported_and_exit_1(capsys) -> None:
    failing = "\n".join(f"line {n}" for n in range(30)) + "\n1 failed, 38 passed in 1.7s\n"
    run = FakeRun_py_matrix({"3.9": (1, failing), "3.12": "timeout", "3.13": (0, "39 passed in 1.6s\n")})
    assert py_matrix.main(["3.9", "3.12", "3.13", "--tail", "2", "--timeout", "5"],
                          which=lambda name: "/usr/bin/uv", run=run) == 1
    assert capsys.readouterr().out.splitlines() == [
        "py3.9: FAILED (exit 1): 1 failed, 38 passed in 1.7s",
        "  line 29",
        "  1 failed, 38 passed in 1.7s",
        "py3.12: TIMEOUT after 5s",
        "py3.13: 39 passed in 1.6s",
    ]
    assert run.calls[0][0][run.calls[0][0].index("--with") + 1] == "pytest"


def test_missing_uv_is_exit_2(capsys) -> None:
    assert py_matrix.main(["3.9"], which=lambda name: None, run=FakeRun_py_matrix({})) == 2
    assert "uv not found" in capsys.readouterr().err


def test_default_version_is_3_13(capsys) -> None:
    run = FakeRun_py_matrix({"3.13": (0, "1 passed\n")})
    assert py_matrix.main(["--", "tests/x.py"], which=lambda name: "/usr/bin/uv", run=run) == 0
    assert capsys.readouterr().out == "py3.13: 1 passed\n"


# ═══ · pytest brief ══════════════════════════════════════════════════════════════════════════════════════
# pytest_brief: requirement parsing, missing-only install, and the kept summary lines.

PYTEST_BRIEF_SCRIPT = ROOT / "pytest_brief.py"
spec_pytest_brief = importlib.util.spec_from_file_location("pytest_brief", PYTEST_BRIEF_SCRIPT)
pb = importlib.util.module_from_spec(spec_pytest_brief)
spec_pytest_brief.loader.exec_module(pb)


class ResolverRegressionTests(unittest.TestCase):
    """Exercise the real target pip offline; no package index or PEP parser mocks."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        venv.EnvBuilder(with_pip=True).create(cls.root / "venv")
        cls.python = str(cls.root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        cls.wheels = cls.root / "wheels"
        cls.wheels.mkdir()
        for name, version, extra in [
            ("brief_base", "1.0", False), ("brief_base", "2.0", True),
            ("brief_extra", "1.0", False),
        ]:
            info = f"{name}-{version}.dist-info"
            metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            if extra:
                metadata += 'Provides-Extra: feature\nRequires-Dist: brief-extra>=1; extra == "feature"\n'
            with zipfile.ZipFile(cls.wheels / f"{name}-{version}-py3-none-any.whl", "w") as archive:
                archive.writestr(info + "/METADATA", metadata)
                archive.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr(info + "/RECORD", "")
        cls.pip = [cls.python, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
        subprocess.run(cls.pip + ["--no-index", "--find-links", str(cls.wheels), "brief-base==1.0"],
                       capture_output=True, text=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_versions_markers_extras_in_separate_interpreter(self):
        req = self.root / "requirements.txt"
        included = self.root / "included.txt"
        constraint = self.root / "constraints.txt"
        included.write_text('brief-base[feature]>=2\nabsent-package; python_version < "0"\n', encoding="utf-8")
        constraint.write_text("brief-base==2.0\n", encoding="utf-8")
        req.write_text(f'--no-index\n--find-links {self.wheels}\n-r included.txt\n-c constraints.txt\n', encoding="utf-8")
        need = pb.resolve(self.pip, ["-r", str(req)])
        self.assertEqual(set(need), {"brief_base", "brief_extra"})
        # A dry run must leave the installed 1.0 alone.
        version = subprocess.run([self.python, "-c", "from importlib.metadata import version; print(version('brief-base'))"],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(version.stdout.strip(), "1.0")
        base_req = self.root / "base.txt"
        base_req.write_text(f"--no-index\n--find-links {self.wheels}\nbrief-base>=2\n", encoding="utf-8")
        subprocess.run(self.pip + ["-r", str(base_req)], capture_output=True, text=True, check=True)
        # Base distribution present at the right version does not satisfy an extra.
        self.assertEqual(pb.resolve(self.pip, ["-r", str(req)]), ["brief_extra"])
        subprocess.run(self.pip + ["-r", str(req)], capture_output=True, text=True, check=True)
        self.assertEqual(pb.resolve(self.pip, ["-r", str(req)]), [])
        self.assertFalse(pb.externally_managed(self.python))

    def test_real_target_managed_marker_and_venv_exemption(self):
        site = self.root / "probe-site"
        site.mkdir()
        (site / "EXTERNALLY-MANAGED").write_text("test marker", encoding="utf-8")
        customize = site / "sitecustomize.py"
        customize.write_text(
            f"import sysconfig\nsysconfig.get_path = lambda name: {str(site)!r}\n",
            encoding="utf-8")
        with patch.dict(os.environ, {"PYTHONPATH": str(site)}):
            # `uv run pytest` executes inside a virtualenv. PEP 668 exempts that
            # environment, so the marker is probed on the base interpreter.
            base = getattr(sys, "_base_executable", sys.executable)
            self.assertTrue(pb.externally_managed(base))
            self.assertFalse(pb.externally_managed(self.python))

    def test_resolution_failure_stops_before_pytest(self):
        req = self.root / "invalid.txt"
        req.write_text("--no-index\nbrief-base>=999\n", encoding="utf-8")
        with patch.object(pb.subprocess, "run", wraps=subprocess.run) as run:
            self.assertEqual(pb.main(["--python", self.python, "--requirements", str(req)]), 3)
        self.assertFalse(any("-m" in c.args[0] and "pytest" in c.args[0] for c in run.call_args_list))


class CommandRegressionTests(unittest.TestCase):
    def test_target_managed_probe_and_flag(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            if "-c" in command:
                return subprocess.CompletedProcess(command, 0, "1\n", "")
            if "--dry-run" in command:
                Path(command[command.index("--report") + 1]).write_text(
                    json.dumps({"version": "1", "install": [{"metadata": {"name": "demo"}}]}))
            return subprocess.CompletedProcess(command, 0, "1 passed in 0.01s\n", "")

        with patch.object(pb.subprocess, "run", side_effect=run):
            self.assertEqual(pb.main(["--python", "other-python", "--requirements", "req.txt"]), 0)
        self.assertTrue(all(c[0] == "other-python" for c in commands))
        installs = [c for c in commands if "install" in c]
        self.assertEqual(len(installs), 2)
        self.assertTrue(all("--break-system-packages" in c for c in installs))
        self.assertTrue(all(c[-2:] == ["-r", "req.txt"] for c in installs))

    def test_install_failure_and_satisfied_skip(self):
        for need, install_code, expected in [(["demo"], 1, 3), ([], 0, 1)]:
            with self.subTest(need=need):
                def run(command, **kwargs):
                    if "pip" in command:
                        raise subprocess.CalledProcessError(install_code, command, "", "install failed")
                    return subprocess.CompletedProcess(command, 1, "1 failed in 0.01s\n", "")
                with patch.object(pb, "externally_managed", return_value=False), patch.object(pb, "resolve", return_value=need), patch.object(pb.subprocess, "run", side_effect=run) as mock:
                    self.assertEqual(pb.main(["--requirements", "req.txt"]), expected)
                self.assertEqual(len(mock.call_args_list), 1)
                command = mock.call_args.args[0]
                self.assertNotIn("--break-system-packages", command)
                self.assertEqual("pip" in command, bool(need))

    def test_broken_report_or_interpreter_is_dependency_failure(self):
        with patch.object(pb, "externally_managed", side_effect=FileNotFoundError("python missing")):
            self.assertEqual(pb.main(["--requirements", "req.txt"]), 3)
        with patch.object(pb, "externally_managed", return_value=False), patch.object(pb, "resolve", side_effect=ValueError("bad report")):
            self.assertEqual(pb.main(["--requirements", "req.txt"]), 3)


def test_brief_keeps_failures_coverage_and_summary():
    out = """....F
=========================== short test summary info ============================
FAILED tests/test_a.py::test_x - assert 1 == 2
Name        Stmts   Miss Branch BrPart  Cover   Missing
xprobe.py     560      4    296      3    99%   314
TOTAL         560      4    296      3    99%
Required test coverage of 90% reached. Total coverage: 99.18%
1 failed, 229 passed in 6.16s
"""
    assert pb.brief(out, 1, 5) == ["FAILED tests/test_a.py::test_x - assert 1 == 2",
                                   "TOTAL         560      4    296      3    99%",
                                   "Required test coverage of 90% reached. Total coverage: 99.18%",
                                   "1 failed, 229 passed in 6.16s"]


def test_brief_falls_back_to_tail_when_pytest_could_not_run():
    out = "ERROR: usage: python -m pytest\npython -m pytest: error: unrecognized arguments: --cov=x\n"
    assert pb.brief(out, 4, 1) == ["python -m pytest: error: unrecognized arguments: --cov=x"]


def test_cli_runs_a_project_and_returns_pytest_code(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "requirements.txt").write_text("pytest\n")
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n\ndef test_bad():\n    assert 1 == 2\n")
    done = subprocess.run([sys.executable, "-S", str(PYTEST_BRIEF_SCRIPT), "--", "-o", "addopts="], cwd=tmp_path,
                          capture_output=True, text=True)
    assert done.returncode == 1
    lines = done.stdout.strip().splitlines()
    assert lines[0].startswith("FAILED tests/test_ok.py::test_bad")
    assert lines[-1].startswith("1 failed, 1 passed") and "installed" not in done.stdout
    bad = subprocess.run([sys.executable, "-S", str(PYTEST_BRIEF_SCRIPT), "--requirements", "nope.txt"], cwd=tmp_path,
                         capture_output=True, text=True)
    assert bad.returncode == 3


def test_cli_preserves_plugin_startup_errors_in_json(tmp_path):
    """Exercise real pytest startup, rather than mocked return codes."""
    (tmp_path / "broken_brief_plugin.py").write_text(
        'raise ImportError("brief plugin dependency unavailable")\n', encoding="utf-8")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONPATH=str(tmp_path))
    for plugin, code, diagnostic in [
        ("absent_brief_plugin", 4, "absent_brief_plugin"),
        ("broken_brief_plugin", 3, "brief plugin dependency unavailable"),
    ]:
        done = subprocess.run(
            [sys.executable, "-S", str(PYTEST_BRIEF_SCRIPT), "--json", "--", "-p", plugin],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert done.returncode == code, done.stdout + done.stderr
        payload = json.loads(done.stdout)
        assert payload["ok"] is False
        assert payload["exit_code"] == code
        assert diagnostic in "\n".join(payload["lines"])


# ═══ · repository hygiene ════════════════════════════════════════════════════════════════════════════════
# Keep Python runtime artifacts out of the Git index.

def test_no_tracked_python_runtime_artifacts():
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    artifacts = [path for path in paths if "__pycache__" in PurePosixPath(path).parts
                 or PurePosixPath(path).suffix in {".pyc", ".pyo", ".pyd"}]
    assert not artifacts, "Tracked Python runtime artifacts: " + repr(artifacts)


def test_python_runtime_artifacts_are_ignored():
    paths = ["__pycache__/example", "tests/__pycache__/example", "example.pyc",
             "tests/example.pyo", "vendor/example.pyd"]
    result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                            cwd=ROOT, input="\n".join(paths) + "\n",
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == paths


def test_stdlib_min_stand_ins_import_only_the_standard_library():
    """tools/stdlib_min/ is what consumers lock instead of the third-party package, so it must run with nothing installed."""
    files = sorted((ROOT / "tools" / "stdlib_min").glob("*_min.py"))
    assert files
    for file in files:
        names = set()
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, (file.name, "relative import")
                names.add((node.module or "").split(".")[0])
        assert names <= set(sys.stdlib_module_names) | {"__future__"}, (file.name, sorted(names - set(sys.stdlib_module_names)))
        result = subprocess.run([sys.executable, "-S", "-I", "-c", "import runpy, sys; runpy.run_path(sys.argv[1], run_name='stdlib_min_check')", str(file)],
                                capture_output=True, text=True, check=False, timeout=60)
        assert result.returncode == 0, (file.name, result.stderr)



# ──── plotly_min（tools/stdlib_min/plotly_min.py）───────────────────────────────────

def _plotly_min():
    spec = importlib.util.spec_from_file_location("plotly_min", ROOT / "tools" / "stdlib_min" / "plotly_min.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scales(seed: int, count: int) -> list[float]:
    """A×10^-N with A in [1, 10) and N in 0..3 picked at random (not hand-picked powers of ten)."""
    import random
    rng = random.Random(seed)
    return [round(rng.uniform(1, 10), 3) * 10 ** -rng.randint(0, 3) for _ in range(count)]


def _svg_tree(fig):
    import xml.etree.ElementTree as ET
    return ET.fromstring(fig.to_image("svg").decode("utf-8"))


_SVG = "{http://www.w3.org/2000/svg}"


def test_plotly_min_figure_json_is_plotly_shaped():
    pm = _plotly_min()
    go = pm.graph_objects
    fig = go.Figure(go.Scatter(x=[1, 2], y=[3, 4], line_color="red", line_dash="dash", name="a"))
    fig.update_layout(title="題", xaxis_title="x [s]", paper_bgcolor="white", font_size=14)
    fig.add_vline(1.5, line_width=1)
    data = fig.to_dict()
    assert data["data"] == [{"type": "scatter", "x": [1, 2], "y": [3, 4], "line": {"color": "red", "dash": "dash"},
                             "name": "a"}]
    assert data["layout"] == {"title": {"text": "題"}, "xaxis": {"title": {"text": "x [s]"}},
                              "paper_bgcolor": "white", "font": {"size": 14},
                              "shapes": [{"type": "line", "x0": 1.5, "x1": 1.5, "xref": "x", "y0": 0, "y1": 1,
                                          "yref": "y domain", "line": {"width": 1}}]}
    assert pm.Figure(json.loads(fig.to_json())).to_dict() == data  # Figure(dict) round trip
    assert "題" in fig.to_json()
    with pytest.raises(ValueError):
        pm.Figure([{"type": "scatter3d"}])
    with pytest.raises(ValueError):
        fig.to_image("png")  # SVG only: plotly needs Kaleido + Chrome for raster formats


def test_plotly_min_svg_is_deterministic_and_meiryo(tmp_path):
    pm = _plotly_min()
    go = pm.graph_objects
    fig = go.Figure([go.Bar(x=["燃料", "電気"], y=[100, 41], text=["100", "41"]),
                     go.Scatter(x=["燃料", "電気"], y=[90, 40], name="線")])
    fig.update_layout(title="🔀 テスト <&>", width=640, height=360)
    assert fig.to_image() == fig.to_image()
    root = _svg_tree(fig)
    assert (root.get("width"), root.get("height")) == ("640", "360")
    texts = root.iter(_SVG + "text")
    families = {t.get("font-family") for t in texts}
    assert families == {pm.FONT} and pm.FONT.startswith("Meiryo")
    assert any(t.text == "🔀 テスト <&>" for t in root.iter(_SVG + "text"))  # escaped, then parsed back
    fig.write_image(tmp_path / "f.svg")
    assert (tmp_path / "f.svg").read_bytes() == fig.to_image()
    fig.update_layout(font_family="serif")
    assert {t.get("font-family") for t in _svg_tree(fig).iter(_SVG + "text")} == {"serif"}


def test_plotly_min_scatter_is_linear_in_the_data():
    pm = _plotly_min()
    go = pm.graph_objects
    xs, ys = [0.0, 1.0, 2.5, 4.0], [1.0, -2.0, 0.5, 3.0]

    def path(scale: float) -> str:
        fig = go.Figure(go.Scatter(x=[x * scale for x in xs], y=[y * scale for y in ys], mode="lines"))
        fig.update_layout(xaxis_range=[0, 4 * scale], yaxis_range=[-2 * scale, 3 * scale], width=480, height=320,
                          margin=dict(l=40, r=40, t=40, b=40))
        return next(p.get("d") for p in _svg_tree(fig).iter(_SVG + "path") if p.get("fill") == "none")

    base = path(1.0)
    pts = [tuple(map(float, part.split())) for part in base[1:].split(" L")]
    assert pts == [(40 + x / 4 * 400, 280 - (y + 2) / 5 * 240) for x, y in zip(xs, ys)]
    for scale in _scales(41, 3):  # the picture does not depend on the units
        assert path(scale) == base, scale


def test_plotly_min_bars_and_waterfall():
    pm = _plotly_min()
    go = pm.graph_objects
    fig = go.Figure(go.Bar(y=["a", "b"], x=[3.0, 2.0], base=[-1.0, 0.5], orientation="h", marker_color="#123456"))
    fig.update_layout(xaxis_range=[-1, 3], width=500, height=300, margin=dict(l=50, r=50, t=50, b=50))
    bars = [r for r in _svg_tree(fig).iter(_SVG + "rect") if r.get("fill") == "#123456"]
    assert [(float(r.get("x")), float(r.get("width"))) for r in bars] == [(50.0, 300.0), (50 + 1.5 * 100, 200.0)]
    steps = [100.0, -6.0, -49.0, -4.0, 0.0]
    wf = go.Figure(go.Waterfall(x=list("abcde"), y=steps, measure=["absolute", "relative", "relative", "relative", "total"]))
    wf.update_layout(yaxis_range=[0, 100], width=500, height=300, margin=dict(l=50, r=50, t=50, b=50))
    rects = [r for r in _svg_tree(wf).iter(_SVG + "rect") if r.get("fill") in pm._WATERFALL.values()]
    heights = [round(float(r.get("height")), 2) for r in rects]
    assert heights == [200.0, 12.0, 98.0, 8.0, 82.0]  # 1 unit = 2 px; the total is 100 − 6 − 49 − 4 = 41
    assert [r.get("fill") for r in rects] == ["#4499FF", "#FF4136", "#FF4136", "#FF4136", "#4499FF"]


def test_plotly_min_sankey_keeps_flows_and_ignores_units():
    pm = _plotly_min()
    go = pm.graph_objects
    src, dst, val = [0, 1, 1, 2, 2], [1, 3, 2, 4, 5], [100.0, 6.0, 94.0, 45.0, 49.0]

    def svg(scale: float) -> bytes:
        fig = go.Figure(go.Sankey(node=dict(label=["燃料", "ボイラ", "蒸気", "煙突", "送電", "海"]),
                                  link=dict(source=src, target=dst, value=[v * scale for v in val])))
        return fig.update_layout(width=600, height=400).to_image()

    base = svg(1.0)
    for scale in _scales(42, 3):
        assert svg(scale) == base, scale
    import xml.etree.ElementTree as ET
    root = ET.fromstring(base.decode("utf-8"))
    nodes = [r for r in root.iter(_SVG + "rect") if r.get("stroke") == "#444"]
    heights = [float(r.get("height")) for r in nodes]
    ky = heights[0] / 100.0
    for got, want in zip(heights, [100, 100, 94, 6, 45, 49]):  # node height = max(in, out)
        assert abs(got - want * ky) < 0.02
    bands = [p for p in root.iter(_SVG + "path") if p.get("fill") == "rgba(0,0,0,0.2)"]
    assert len(bands) == len(val)
    for band, v in zip(bands, val):  # a band keeps its width from end to end
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", band.get("d"))]
        assert abs((nums[15] - nums[1]) - v * ky) < 0.02 and abs((nums[9] - nums[7]) - v * ky) < 0.02


def test_plotly_min_heatmap_and_html():
    pm = _plotly_min()
    go = pm.graph_objects
    fig = go.Figure(go.Heatmap(z=[[0.0, 1.0], [2.0, 3.0]], colorscale="viridis", showscale=False))
    fills = [r.get("fill") for r in _svg_tree(fig).iter(_SVG + "rect")]
    assert pm._SCALES["viridis"][0] in fills and pm._SCALES["viridis"][-1] in fills
    html = go.Figure(go.Scatter(y=[1, 2], name="water < steam")).to_html()
    assert f"https://cdn.plot.ly/plotly-{pm.PLOTLY_JS}.min.js" in html
    payload = json.loads(html.split("const f = ", 1)[1].split(";Plotly.newPlot", 1)[0])
    assert payload["layout"]["font"]["family"] == pm.FONT and payload["data"][0]["name"] == "water < steam"
    assert "<script src" not in go.Figure().to_html(include_plotlyjs=False, full_html=False)


@pytest.mark.parametrize("text", ["water < steam", "water & steam", 'say "hello"', "single 'quote'"])
@pytest.mark.parametrize("target", ["figure", "div_id"])
def test_plotly_min_html_json_preserves_text_without_html_metacharacters(text, target):
    from html.parser import HTMLParser

    pm = _plotly_min()
    name = text if target == "figure" else "water"
    div_id = text if target == "div_id" else "figure"
    fig = pm.Figure(pm.Scatter(y=[1, 2], name=name))
    html = fig.to_html(include_plotlyjs=False, full_html=False, div_id=div_id)

    class Document(HTMLParser):
        def __init__(self):
            super().__init__()
            self.div_ids = []
            self.script_count = 0

        def handle_starttag(self, tag, attrs):
            if tag == "div":
                self.div_ids.append(dict(attrs)["id"])
            if tag == "script":
                self.script_count += 1

    document = Document()
    document.feed(html)
    assert document.div_ids == [div_id]
    assert document.script_count == 1
    inline = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert not any(char in inline for char in "<>&")
    decoder = json.JSONDecoder()
    figure, end = decoder.raw_decode(inline.removeprefix("const f = "))
    rest = inline.removeprefix("const f = ")[end:]
    identifier, end = decoder.raw_decode(rest.removeprefix(";Plotly.newPlot("))
    assert rest.removeprefix(";Plotly.newPlot(")[end:] == ", f.data, f.layout);"
    assert figure["data"] == fig.to_dict()["data"]
    assert figure["layout"]["font"]["family"] == pm.FONT
    assert identifier == div_id



def test_plotly_min_subplots_and_scaleanchor():
    pm = _plotly_min()
    go = pm.graph_objects
    fig = go.Figure([go.Scatter(x=[1, 6], y=[1, 6], mode="markers", marker_color="#111111"),
                     go.Scatter(x=[1, 6], y=[1, 6], mode="markers", xaxis="x2", yaxis="y2", marker_color="#222222")])
    fig.update_layout(width=800, height=400, margin=dict(l=40, r=40, t=40, b=40), showlegend=False, template={},
                      xaxis=dict(domain=[0, 0.4], range=[0, 7]), yaxis=dict(range=[0, 7]),
                      xaxis2=dict(domain=[0.6, 1], range=[0, 7]), yaxis2=dict(range=[0, 7]))
    root = _svg_tree(fig)
    dots = {c.get("fill"): [] for c in root.iter(_SVG + "circle")}
    for c in root.iter(_SVG + "circle"):
        dots[c.get("fill")].append(float(c.get("cx")))
    assert dots["#111111"] == [round(40 + 1 / 7 * 288, 2), round(40 + 6 / 7 * 288, 2)]  # first area: 0–40 % of 720 px
    assert dots["#222222"] == [round(40 + 432 + 1 / 7 * 288, 2), round(40 + 432 + 6 / 7 * 288, 2)]
    grids = {l.get("stroke") for l in root.iter(_SVG + "line")}
    assert "#eee" in grids  # template={} draws plotly.js's own look (white, #eee grid), not plotly.py's default
    sq = go.Figure(go.Scatter(x=[0, 10], y=[0, 1], mode="markers"))
    sq.update_layout(width=600, height=400, margin=dict(l=0, r=0, t=0, b=0), xaxis_range=[0, 10], yaxis_range=[0, 1],
                     yaxis=dict(scaleanchor="x", scaleratio=1))
    xs = [float(c.get("cx")) for c in _svg_tree(sq).iter(_SVG + "circle")]
    ys = [float(c.get("cy")) for c in _svg_tree(sq).iter(_SVG + "circle")]
    assert abs((xs[1] - xs[0]) / 10 - (ys[0] - ys[1]) / 1) < 1e-6  # one unit is the same length on both axes


def test_plotly_min_stacks_heatmap_text_and_markup():
    pm = _plotly_min()
    go = pm.graph_objects
    fig = go.Figure([go.Bar(y=["a", "b"], x=[2, 1], orientation="h", marker=dict(color="#000001", line=dict(color="#fff", width=2))),
                     go.Bar(y=["a", "b"], x=[3, 1], orientation="h", marker_color="#000002")])
    fig.update_layout(barmode="stack", width=500, height=300, margin=dict(l=50, r=50, t=50, b=50), xaxis_range=[0, 5],
                      yaxis=dict(categoryorder="array", categoryarray=["b", "a"], autorange="reversed"), showlegend=False)
    rects = {r.get("fill"): [] for r in _svg_tree(fig).iter(_SVG + "rect")}
    for r in (r for r in _svg_tree(fig).iter(_SVG + "rect") if r.get("x") is not None):
        rects[r.get("fill")].append((float(r.get("x")), float(r.get("width")), float(r.get("y")), r.get("stroke")))
    assert [(x, w) for x, w, _y, _s in rects["#000002"]] == [(50 + 2 * 80, 3 * 80), (50 + 80, 80)]  # on top of the first
    assert rects["#000001"][0][3] == "#fff"  # marker.line draws the edge
    assert rects["#000001"][0][2] > rects["#000001"][1][2]  # categoryarray b, a then reversed: "a" below "b"
    heat = go.Figure(go.Heatmap(z=[[1, 20]], x=[0, 1], y=[0], texttemplate="%{z}", xgap=4, showscale=False,
                                colorscale=[[0, "#ffffff"], [1, "#000000"]]))
    heat.update_layout(width=400, height=300, margin=dict(l=0, r=0, t=0, b=0), xaxis=dict(tickvals=[0, 1], ticktext=["左", "右"]))
    root = _svg_tree(heat)
    texts = [(t.text, t.get("fill")) for t in root.iter(_SVG + "text")]
    assert ("1", pm._INK) in texts and ("20", "white") in texts  # cell text in a contrasting colour
    assert {"左", "右"} <= {t for t, _f in texts}
    cells = [r for r in root.iter(_SVG + "rect") if r.get("fill") in ("#ffffff", "#000000")]
    assert [round(float(r.get("width")), 2) for r in cells][-2:] == [196.0, 196.0]  # 200 px cells, 2 px gap each side
    note = go.Figure(go.Scatter(x=[0], y=[0]))
    note.update_layout(title="一行目<br><b>二行目 &gt; 0</b>")
    note.add_annotation(x=0.5, y=0.5, xref="paper", yref="paper", text="<b>S1</b>", showarrow=False,
                        bgcolor="#ffffff", bordercolor="#2a78d6", borderwidth=2)
    root = _svg_tree(note)
    lines = [(t.text, t.get("font-weight")) for t in root.iter(_SVG + "text")]
    assert ("一行目", None) in lines and ("二行目 > 0", "bold") in lines and ("S1", "bold") in lines
    assert any(r.get("stroke") == "#2a78d6" for r in root.iter(_SVG + "rect"))  # the annotation's box

@pytest.mark.skipif(importlib.util.find_spec("plotly") is None, reason="plotly is a reference only, not installed")
def test_plotly_min_matches_plotly_figure_json():
    import plotly.graph_objects as real
    pm = _plotly_min()
    go = pm.graph_objects
    cases = [(go.Scatter, real.Scatter, dict(x=[1, 2], y=[3, 4], line_color="red", mode="lines")),
             (go.Bar, real.Bar, dict(y=["a"], x=[2], base=[1], orientation="h", marker_color="#123456")),
             (go.Waterfall, real.Waterfall, dict(x=["a", "b"], y=[1, -1], measure=["absolute", "relative"])),
             (go.Sankey, real.Sankey, dict(node=dict(label=["a", "b"]), link=dict(source=[0], target=[1], value=[1]))),
             (go.Heatmap, real.Heatmap, dict(z=[[1, 2]], colorscale="viridis"))]
    for mine, theirs, kwargs in cases:
        assert go.Figure(mine(**kwargs)).to_dict()["data"] == real.Figure(theirs(**kwargs)).to_dict()["data"]

if __name__ == "__main__":
    unittest.main()
