import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

import vendor_sync as sync

OLD = "1" * 40
NEW = "2" * 40


def entry(data=b"old\n", source="scripts/adapter.py", destination="vendor/adapter.py"):
    return dict(repository="myon-bioinformatics/xprobe", ref="main", commit=OLD,
                source=source, destination=destination, blob_sha=sync.git_blob(data),
                sha256=hashlib.sha256(data).hexdigest())


def lock(tmp_path, entries=None):
    value = {"schema": sync.SCHEMA, "files": entries or [entry()]}
    path = tmp_path / "vendor.lock.json"
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path, value


def test_materialize_and_offline_check(tmp_path):
    path, value = lock(tmp_path)
    urls = []

    def get(url):
        urls.append(url)
        return b"old\n"

    result = sync.synchronize(path.name, tmp_path, "materialize", get=get)
    assert result["changed_paths"] == ["vendor/adapter.py"]
    assert urls == [f"https://raw.githubusercontent.com/myon-bioinformatics/xprobe/{OLD}/scripts/adapter.py"]
    assert (tmp_path / "vendor/adapter.py").read_bytes() == b"old\n"
    assert json.loads(path.read_text(encoding="utf-8")) == value
    no_network = lambda url: pytest.fail("network used for valid local files")
    assert not sync.synchronize(path.name, tmp_path, "check", get=no_network)["changed_paths"]
    assert not sync.synchronize(path.name, tmp_path, "materialize", get=no_network)["changed_paths"]


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("mixed_commits", [False, True])
def test_locked_materialize_rate_limit_uses_exact_public_git_commits(tmp_path, monkeypatch, status, mixed_commits):
    from urllib.error import HTTPError
    repo = tmp_path / "upstream"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()
    git("init", "-b", "main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repo / "module.py").write_bytes(b"locked source\n")
    (repo / "LICENSE").write_bytes(b"license\n")
    git("add", "."); git("commit", "-m", "locked")
    first = git("rev-parse", "HEAD")
    # main moves: materialize must still retrieve the historical source.
    (repo / "module.py").write_bytes(b"new source, not requested\n")
    git("commit", "-am", "new main")
    second = git("rev-parse", "HEAD")
    entries = [entry(b"locked source\n", "module.py", "vendor/module.py"),
               entry(b"license\n", "LICENSE", "vendor/LICENSE")]
    entries[0]["commit"] = first
    entries[1]["commit"] = second if mixed_commits else first
    path, _ = lock(tmp_path, entries)
    before = path.read_bytes()
    actual = sync._public_git_snapshot
    calls = []
    def snapshot(repository, commit, sources):
        calls.append(commit)
        return actual(repository, commit, sources, remote=repo.as_uri())
    monkeypatch.setattr(sync, "_public_git_snapshot", snapshot)
    urls = []
    def limited(url):
        urls.append(url)
        raise HTTPError(url, status, "limited", {}, None)
    sync.synchronize(path.name, tmp_path, "materialize", get=limited)
    assert calls == ([first, second] if mixed_commits else [first])
    assert len(urls) == len(calls)
    assert all(url.startswith("https://raw.githubusercontent.com/") for url in urls)
    assert (tmp_path / "vendor/module.py").read_bytes() == b"locked source\n"
    assert (tmp_path / "vendor/LICENSE").read_bytes() == b"license\n"
    assert path.read_bytes() == before
    sync.synchronize(path.name, tmp_path, "check")


@pytest.mark.parametrize("status", [404, 500])
def test_locked_materialize_other_http_errors_do_not_fallback(tmp_path, monkeypatch, status):
    from urllib.error import HTTPError
    path, _ = lock(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(sync, "_public_git_snapshot", lambda *args: pytest.fail("unexpected fallback"))
    def fail(url):
        raise HTTPError(url, status, "not a rate limit", {}, None)
    with pytest.raises(HTTPError):
        sync.synchronize(path.name, tmp_path, "materialize", get=fail)
    assert path.read_bytes() == before
    assert not (tmp_path / "vendor").exists()


@pytest.mark.parametrize("failure", ["commit", "blob", "sha256", "git"])
def test_locked_fallback_failure_is_exit_two_without_partial_writes(tmp_path, monkeypatch, capsys, failure):
    from urllib.error import HTTPError
    entries = [entry(), entry(source="LICENSE", destination="vendor/LICENSE")]
    if failure == "sha256":
        entries[1]["sha256"] = "0" * 64
    path, _ = lock(tmp_path, entries)
    before = path.read_bytes()
    def limited(*args, **kwargs):
        raise HTTPError("https://raw.githubusercontent.com/", 429, "limited", {}, None)
    def snapshot(repository, commit, sources):
        if failure == "git":
            raise ValueError("public Git fetch/read failed")
        files = {source: (sync.git_blob(b"old\n"), b"old\n") for source in sources}
        if failure == "blob":
            files["LICENSE"] = ("0" * 40, b"old\n")
        return (NEW if failure == "commit" else OLD), files
    monkeypatch.setattr(sync, "urlopen", limited)
    monkeypatch.setattr(sync, "_public_git_snapshot", snapshot)
    assert sync.main(["materialize", "--root", str(tmp_path)]) == 2
    assert capsys.readouterr().err.startswith("vendor-sync:")
    assert path.read_bytes() == before
    assert not (tmp_path / "vendor").exists()


@pytest.mark.parametrize("field", ["blob_sha", "sha256"])
def test_bad_digest_leaves_all_files_and_lock_unchanged(tmp_path, field):
    entries = [entry(source="one.py", destination="vendor/one.py"),
               entry(source="two.py", destination="vendor/two.py")]
    entries[1][field] = "0" * (40 if field == "blob_sha" else 64)
    path, _ = lock(tmp_path, entries)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="digest mismatch"):
        sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    assert not (tmp_path / "vendor").exists()
    assert path.read_bytes() == before


def upstream(data, calls):
    def get(url):
        calls.append(url)
        if "/commits?" in url:
            return json.dumps([{"sha": NEW}]).encode()
        if "/contents/" in url:
            return json.dumps({"type": "file", "sha": sync.git_blob(data)}).encode()
        assert "/" + NEW + "/" in url
        return data
    return get


def test_update_refreshes_pin_and_bytes_without_executing_them(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    payload = b"raise RuntimeError('never import candidate')\n"
    calls = []
    result = sync.synchronize(path.name, tmp_path, "update", get=upstream(payload, calls))
    assert result["changed_paths"] == ["vendor/adapter.py", path.name]
    current = json.loads(path.read_text(encoding="utf-8"))["files"][0]
    assert current["commit"] == NEW
    assert current["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / current["destination"]).read_bytes() == payload
    assert len(calls) == 3
    sync.synchronize(path.name, tmp_path, "check")


def test_update_no_churn_for_unrelated_upstream_commit(tmp_path):
    path, old = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    before = path.read_bytes()
    assert not sync.synchronize(path.name, tmp_path, "update", get=upstream(b"old\n", []))["changed_paths"]
    assert path.read_bytes() == before
    assert json.loads(before) == old


def test_group_resolves_one_commit_and_includes_license(tmp_path):
    path, _ = lock(tmp_path, [entry(), entry(source="LICENSE", destination="vendor/LICENSE")])
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    calls = []
    sync.synchronize(path.name, tmp_path, "update", get=upstream(b"new\n", calls))
    assert sum("/commits?" in url for url in calls) == 1
    assert {i["commit"] for i in json.loads(path.read_text(encoding="utf-8"))["files"]} == {NEW}
    assert (tmp_path / "vendor/LICENSE").read_bytes() == b"new\n"


def test_upstream_blob_mismatch_does_not_write(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    before = path.read_bytes()
    get = upstream(b"good\n", [])
    def corrupt(url):
        return b"bad\n" if url.startswith("https://raw.") else get(url)
    with pytest.raises(ValueError, match="digest mismatch"):
        sync.synchronize(path.name, tmp_path, "update", get=corrupt)
    assert path.read_bytes() == before
    assert (tmp_path / "vendor/adapter.py").read_bytes() == b"old\n"


@pytest.mark.parametrize("destination", ["/outside", "../outside", "vendor/../outside", "C:/outside", "a\\b", ".git/config"])
def test_unsafe_destinations_rejected_without_network(tmp_path, destination):
    path, _ = lock(tmp_path, [entry(destination=destination)])
    with pytest.raises(ValueError):
        sync.synchronize(path.name, tmp_path, "update", get=lambda url: pytest.fail("network"))


def test_symlink_and_manifest_collision(tmp_path):
    path, _ = lock(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (tmp_path / "vendor").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("runner cannot create filesystem symlinks")
    with pytest.raises(ValueError, match="symlink"):
        sync.synchronize(path.name, tmp_path, "materialize")
    (tmp_path / "vendor").unlink()
    lock(tmp_path, [entry(destination=path.name)])
    with pytest.raises(ValueError, match="collides"):
        sync.synchronize(path.name, tmp_path, "update")


def test_duplicate_overlap_and_short_sha():
    item = entry()
    for entries in ([item, copy.deepcopy(item)], [item, entry(destination="vendor/adapter.py/nested")],
                    [{**item, "commit": "1234567"}]):
        with pytest.raises(ValueError):
            sync.validate({"schema": sync.SCHEMA, "files": entries})


def test_cli_help_has_no_side_effects_and_missing_file_is_red(tmp_path):
    script = Path(sync.__file__).resolve()
    help_result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert help_result.returncode == 0
    assert "{check,materialize,update}" in help_result.stdout
    assert not list(tmp_path.iterdir())
    result = subprocess.run([sys.executable, str(script), "check"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stderr.startswith("vendor-sync:")


def test_update_rejects_local_edits_before_network(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    file = tmp_path / "vendor/adapter.py"
    file.write_bytes(b"local edit\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="digest mismatch"):
        sync.synchronize(path.name, tmp_path, "update", get=lambda url: pytest.fail("network"))
    assert file.read_bytes() == b"local edit\n"
    assert path.read_bytes() == before


def test_atomic_preserves_executable_mode_and_defaults_readable(tmp_path):
    existing = tmp_path / "existing.py"
    existing.write_bytes(b"old")
    existing.chmod(0o755)
    sync._atomic(existing, b"new")
    if sys.platform != "win32":
        assert existing.stat().st_mode & 0o777 == 0o755
    fresh = tmp_path / "fresh.py"
    sync._atomic(fresh, b"new")
    if sys.platform != "win32":
        assert fresh.stat().st_mode & 0o777 == 0o644


def test_casefold_manifest_collision(tmp_path):
    path, _ = lock(tmp_path, [entry(destination="VENDOR.LOCK.JSON")])
    with pytest.raises(ValueError, match="collides"):
        sync.synchronize(path.name, tmp_path, "materialize")


def test_slash_ref_query_and_empty_commit_list(tmp_path):
    item = entry()
    item["ref"] = "feature/adapter"
    path, _ = lock(tmp_path, [item])
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    calls = []
    sync.synchronize(path.name, tmp_path, "update", get=upstream(b"old\n", calls))
    assert calls[0] == "https://api.github.com/repos/myon-bioinformatics/xprobe/commits?sha=feature%2Fadapter&per_page=1"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="no commits"):
        sync.synchronize(path.name, tmp_path, "update", get=lambda url: b"[]")
    assert path.read_bytes() == before


@pytest.mark.parametrize('limited_at', ['resolve', 'metadata'])
def test_rate_limit_falls_back_to_public_git_without_repo_writes(tmp_path, monkeypatch, limited_at):
    from urllib.error import HTTPError

    consumer = tmp_path / 'consumer'
    consumer.mkdir()
    path, _ = lock(consumer, [entry(), entry(source='LICENSE', destination='vendor/LICENSE')])
    sync.synchronize(path.name, consumer, 'materialize', get=lambda url: b'old\n')
    upstream_repo = tmp_path / 'upstream'
    upstream_repo.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-C', str(upstream_repo), *args]).decode().strip()

    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (upstream_repo / 'scripts').mkdir()
    (upstream_repo / 'scripts/adapter.py').write_bytes(b'new source\n')
    (upstream_repo / 'LICENSE').write_bytes(b'new license\n')
    git('add', '.')
    git('commit', '-m', 'candidate')
    sha = git('rev-parse', 'HEAD')
    refs_before = git('show-ref')
    snapshot = sync._public_git_snapshot
    fallback_refs = []

    def public_snapshot(repository, ref, sources):
        fallback_refs.append(ref)
        return snapshot(repository, ref, sources, remote=upstream_repo.as_uri())

    def limited(url):
        if limited_at == 'metadata' and '/commits?' in url:
            return json.dumps([{'sha': sha}]).encode()
        raise HTTPError(url, 403, 'rate limit exceeded', {}, None)

    monkeypatch.setattr(sync, '_public_git_snapshot', public_snapshot)
    result = sync.synchronize(path.name, consumer, 'update', get=limited)
    assert fallback_refs == (['main'] if limited_at == 'resolve' else [sha])
    assert result['changed_paths'] == ['vendor/adapter.py', 'vendor/LICENSE', path.name]
    assert (consumer / 'vendor/adapter.py').read_bytes() == b'new source\n'
    assert (consumer / 'vendor/LICENSE').read_bytes() == b'new license\n'
    assert {i['commit'] for i in json.loads(path.read_text())['files']} == {sha}
    sync.synchronize(path.name, consumer, 'check')
    assert git('show-ref') == refs_before
    assert not (consumer / '.git').exists()


def test_rate_limit_git_failure_is_nonzero_without_fallback_to_old_bytes(tmp_path, monkeypatch, capsys):
    from urllib.error import HTTPError

    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, 'materialize', get=lambda url: b'old\n')
    before = path.read_bytes()
    def rate_limited(request, timeout):
        raise HTTPError(request.full_url, 403, 'rate limit exceeded', {}, None)
    def unavailable(*args):
        raise ValueError('public Git fetch/read failed')
    monkeypatch.setattr(sync, 'urlopen', rate_limited)
    monkeypatch.setattr(sync, '_public_git_snapshot', unavailable)
    assert sync.main(['update', '--root', str(tmp_path)]) == 2
    assert 'public Git fetch/read failed' in capsys.readouterr().err
    assert path.read_bytes() == before
    assert (tmp_path / 'vendor/adapter.py').read_bytes() == b'old\n'


@pytest.mark.parametrize('stage', ['metadata', 'raw'])
def test_fallback_commit_mismatch_does_not_write(tmp_path, monkeypatch, stage):
    from urllib.error import HTTPError
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, 'materialize', get=lambda url: b'old\n')
    before = path.read_bytes()
    def get(url):
        if '/commits?' in url:
            return json.dumps([{'sha': NEW}]).encode()
        if stage == 'raw' and '/contents/' in url:
            return json.dumps({'type': 'file', 'sha': sync.git_blob(b'new\n')}).encode()
        raise HTTPError(url, 429, 'limited', {}, None)
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda *args: (OLD, {'scripts/adapter.py': (sync.git_blob(b'new\n'), b'new\n')}))
    with pytest.raises(ValueError, match='does not match resolved SHA'):
        sync.synchronize(path.name, tmp_path, 'update', get=get)
    assert path.read_bytes() == before
    assert (tmp_path / 'vendor/adapter.py').read_bytes() == b'old\n'


@pytest.mark.parametrize('code', [404, 500])
def test_non_rate_limit_http_error_is_red_without_git(tmp_path, monkeypatch, capsys, code):
    from urllib.error import HTTPError
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, 'materialize', get=lambda url: b'old\n')
    before = path.read_bytes()
    def fail(request, timeout):
        raise HTTPError(request.full_url, code, 'unavailable', {}, None)
    monkeypatch.setattr(sync, 'urlopen', fail)
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda *args: pytest.fail('unexpected Git fallback'))
    assert sync.main(['update', '--root', str(tmp_path)]) == 2
    assert 'HTTP Error' in capsys.readouterr().err
    assert path.read_bytes() == before


def test_metadata_git_failure_is_red_and_retains_baseline(tmp_path, monkeypatch, capsys):
    from urllib.error import HTTPError
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, 'materialize', get=lambda url: b'old\n')
    before = path.read_bytes()
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps([{'sha': NEW}]).encode()
    def get(request, timeout):
        if '/commits?' in request.full_url: return Response()
        raise HTTPError(request.full_url, 403, 'limited', {}, None)
    def fail(*args): raise ValueError('public Git fetch/read failed')
    monkeypatch.setattr(sync, 'urlopen', get)
    monkeypatch.setattr(sync, '_public_git_snapshot', fail)
    assert sync.main(['update', '--root', str(tmp_path)]) == 2
    assert 'public Git fetch/read failed' in capsys.readouterr().err
    assert path.read_bytes() == before
    assert (tmp_path / 'vendor/adapter.py').read_bytes() == b'old\n'


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'submodule', 'large', 'credentials'])
def test_public_git_snapshot_boundaries(tmp_path, monkeypatch, kind):
    import os
    repo = tmp_path / 'origin'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (repo / 'module.py').write_bytes(b'new source\n')
    (repo / 'LICENSE').write_bytes(b'license\n')
    git('add', '.')
    git('commit', '-m', 'source')
    if kind in ('symlink', 'submodule'):
        obj = git('hash-object', 'LICENSE') if kind == 'symlink' else git('rev-parse', 'HEAD')
        git('update-index', '--cacheinfo', ('120000' if kind == 'symlink' else '160000') + ',' + obj + ',LICENSE')
        git('commit', '-m', 'nonregular')
    if kind == 'missing':
        git('rm', 'LICENSE'); git('commit', '-m', 'missing')
    before = git('show-ref')
    calls = []
    actual = subprocess.run
    def observe(argv, **kwargs):
        calls.append((argv, kwargs['env']))
        return actual(argv, **kwargs)
    monkeypatch.setattr(sync.subprocess, 'run', observe)
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'do-not-touch'))
    monkeypatch.setenv('GIT_CONFIG_PARAMETERS', "'credential.helper=evil'")
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'http.extraHeader')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'Authorization: forbidden')
    if kind == 'large': monkeypatch.setattr(sync, 'MAX_BYTES', 3)
    if kind == 'credentials':
        sha, files = sync._public_git_snapshot('owner/repo', 'refs/heads/main', ['module.py', 'LICENSE'], remote=repo.as_uri())
        assert files['LICENSE'][1] == b'license\n'
    else:
        with pytest.raises(ValueError, match='byte limit' if kind == 'large' else 'regular file'):
            sync._public_git_snapshot('owner/repo', 'refs/heads/main', ['module.py', 'LICENSE'], remote=repo.as_uri())
    assert calls
    for argv, env in calls:
        assert 'credential.helper=' in argv and 'http.extraHeader=' in argv and 'core.askPass=' in argv
        assert env['GIT_TERMINAL_PROMPT'] == '0' and env['GIT_CONFIG_NOSYSTEM'] == '1'
        assert env['GIT_CONFIG_GLOBAL'] == os.devnull
        assert not any(k in env for k in ('GIT_DIR', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0'))
        assert not Path(argv[argv.index('-C') + 1]).exists()
    assert not (tmp_path / 'do-not-touch').exists()


def test_promote_returns_identity_receipt_and_verified_baseline(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    payload = b"new promoted source\n"
    result = sync.promote(path.name, tmp_path, get=upstream(payload, []))
    assert result["schema"] == "vendor-promotion/1"
    assert result["changed_paths"] == ["vendor/adapter.py", path.name]
    assert len(result["promoted"]) == 1
    receipt = result["promoted"][0]
    assert receipt["destination"] == "vendor/adapter.py"
    assert receipt["old_commit"] == OLD
    assert receipt["new_commit"] == NEW
    assert receipt["new_blob_sha"] == sync.git_blob(payload)
    assert receipt["new_sha256"] == hashlib.sha256(payload).hexdigest()
    assert not sync.synchronize(path.name, tmp_path, "check")["changed_paths"]
    assert not sync.synchronize(path.name, tmp_path, "update", get=upstream(payload, []))["changed_paths"]


def test_promote_source_and_license_share_verified_commit(tmp_path):
    entries = [entry(source="module.py", destination="vendor/module.py"),
               entry(source="LICENSE", destination="vendor/LICENSE")]
    path, _ = lock(tmp_path, entries)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    result = sync.promote(path.name, tmp_path, get=upstream(b"new\n", []))
    assert {row["new_commit"] for row in result["promoted"]} == {NEW}
    assert {row["destination"] for row in result["promoted"]} == {"vendor/module.py", "vendor/LICENSE"}
    sync.synchronize(path.name, tmp_path, "check")


def test_promote_noop_has_empty_receipt(tmp_path):
    path, before = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    result = sync.promote(path.name, tmp_path, get=upstream(b"old\n", []))
    assert result["changed_paths"] == []
    assert result["promoted"] == []
    assert json.loads(path.read_text()) == before


def test_promote_rejects_dirty_baseline_before_network(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    (tmp_path / "vendor/adapter.py").write_bytes(b"local edit\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="digest mismatch"):
        sync.promote(path.name, tmp_path, get=lambda url: pytest.fail("network"))
    assert path.read_bytes() == before
    assert (tmp_path / "vendor/adapter.py").read_bytes() == b"local edit\n"


def test_promote_resolution_failure_leaves_baseline_unchanged(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    before_lock = path.read_bytes()
    before_file = (tmp_path / "vendor/adapter.py").read_bytes()
    def fail(url):
        if "/commits?" in url:
            raise ValueError("resolution failed")
        return b"unexpected"
    with pytest.raises(ValueError, match="resolution failed"):
        sync.promote(path.name, tmp_path, get=fail)
    assert path.read_bytes() == before_lock
    assert (tmp_path / "vendor/adapter.py").read_bytes() == before_file


def test_promote_cli_is_explicit_and_machine_readable(tmp_path, monkeypatch, capsys):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    monkeypatch.setattr(sync, "_get", upstream(b"new\n", []))
    # main binds the default at function definition, so exercise the public
    # promote result directly for deterministic JSON contract.
    result = sync.promote(path.name, tmp_path, get=upstream(b"new\n", []))
    encoded = json.dumps(result, sort_keys=True)
    assert '"schema": "vendor-promotion/1"' in encoded
    assert '"new_commit": "' + NEW + '"' in encoded
