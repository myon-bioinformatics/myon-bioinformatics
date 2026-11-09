"""Bounded retries for transient public GitHub fetch failures in vendor_sync."""
import subprocess
from io import BytesIO; from urllib.error import HTTPError, URLError

import pytest

import vendor_sync as sync


def test_public_http_get_retries_transient_http_then_succeeds(monkeypatch):
    calls = []

    def urlopen(request, timeout=30):
        calls.append(1)
        if len(calls) < 2:
            raise HTTPError(request.full_url, 503, "unavailable", None, None)
        return BytesIO(b"ok")

    sleeps = []
    monkeypatch.setattr(sync.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(sync, "urlopen", urlopen)

    assert sync._public_http_get("https://api.github.com/repos/owner/repo") == b"ok"
    assert len(calls) == 2
    assert sleeps == [sync.PUBLIC_FETCH_RETRY_DELAYS_SEC[0]]


def test_public_http_get_persistent_transient_failure(monkeypatch):
    calls = []

    def urlopen(request, timeout=30):
        calls.append(1)
        raise HTTPError(request.full_url, 500, "error", None, None)

    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(sync, "urlopen", urlopen)

    with pytest.raises(ValueError, match=sync.PUBLIC_GIT_FETCH_ERROR):
        sync._public_http_get("https://api.github.com/repos/owner/repo")
    assert sync._public_fetch_max_attempts() == 3
    assert len(calls) == 3


def test_public_http_get_does_not_retry_404(monkeypatch):
    calls = []

    def urlopen(request, timeout=30):
        calls.append(1)
        raise HTTPError(request.full_url, 404, "missing", None, None)

    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: pytest.fail("sleep"))
    monkeypatch.setattr(sync, "urlopen", urlopen)

    with pytest.raises(sync.PublicGitNotFound):
        sync._public_http_get("https://api.github.com/repos/owner/repo")
    assert len(calls) == 1


def test_public_http_get_does_not_retry_403(monkeypatch):
    calls = []

    def urlopen(request, timeout=30):
        calls.append(1)
        raise HTTPError(request.full_url, 403, "forbidden", None, None)

    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: pytest.fail("sleep"))
    monkeypatch.setattr(sync, "urlopen", urlopen)

    with pytest.raises(sync.PublicGitAccessBlocked):
        sync._public_http_get("https://api.github.com/repos/owner/repo")
    assert len(calls) == 1


def test_public_http_get_retries_urlerror(monkeypatch, capsys):
    calls = []

    def urlopen(request, timeout=30):
        calls.append(1)
        if len(calls) == 1:
            raise URLError("network down")
        return BytesIO(b"payload")

    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(sync, "urlopen", urlopen)

    assert sync._public_http_get("https://raw.githubusercontent.com/o/r/ref/path") == b"payload"
    stderr = capsys.readouterr().err
    assert "public fetch retry 1/3" in stderr
    assert len(calls) == 2


def test_default_branch_name_retries_git_ls_remote(monkeypatch):
    outcomes = [1, 0]
    sleeps = []
    calls = []

    def run(argv, env, capture_output, timeout):
        calls.append(1)
        code = outcomes.pop(0)
        stdout = b"ref: refs/heads/main\tHEAD\n" if code == 0 else b""
        return subprocess.CompletedProcess(argv, code, stdout, b"")

    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(sync.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert sync._default_branch_name("https://github.com/owner/repo.git") == "main"
    assert sleeps == [sync.PUBLIC_FETCH_RETRY_DELAYS_SEC[0]]
    assert len(calls) == 2


def test_default_branch_name_does_not_retry_missing_symref(monkeypatch):
    calls = []

    def run(argv, env, capture_output, timeout):
        calls.append(1)
        return subprocess.CompletedProcess(argv, 0, b"deadbeef\tHEAD\n", b"")

    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: pytest.fail("sleep"))

    with pytest.raises(ValueError, match=sync.PUBLIC_GIT_FETCH_ERROR):
        sync._default_branch_name("https://github.com/owner/repo.git")
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 410, 422])
def test_public_http_get_does_not_retry_client_errors(monkeypatch, status):
    calls = []

    def urlopen(request, timeout=30):
        calls.append(1)
        raise HTTPError(request.full_url, status, "client error", None, None)

    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: pytest.fail("sleep"))
    monkeypatch.setattr(sync, "urlopen", urlopen)

    with pytest.raises(ValueError, match=sync.PUBLIC_GIT_FETCH_ERROR):
        sync._public_http_get("https://api.github.com/repos/owner/repo")
    assert len(calls) == 1


def test_retry_log_redacts_token_bearing_remote(monkeypatch, capsys):
    token = "ghp_testtoken1234567890123456789012345678"
    remote = "https://x-access-token:" + token + "@github.com/owner/repo.git"
    outcomes = [1, 0]

    def run(argv, env, capture_output, timeout):
        code = outcomes.pop(0)
        stdout = b"ref: refs/heads/main\tHEAD\n" if code == 0 else b""
        return subprocess.CompletedProcess(argv, code, stdout, b"")

    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(sync.time, "sleep", lambda _seconds: None)

    sync._default_branch_name(remote)
    stderr = capsys.readouterr().err
    assert token not in stderr
    assert "https://***@github.com/owner/repo.git" in stderr


def test_retry_loop_required_for_transient_http_recovery(monkeypatch):
    calls = []

    def urlopen(request, timeout=30):
        calls.append(1)
        if len(calls) < 2:
            raise HTTPError(request.full_url, 503, "unavailable", None, None)
        return BytesIO(b"ok")

    monkeypatch.setattr(sync, "_retry_transient_public_fetch", lambda _label, fn: fn())
    monkeypatch.setattr(sync, "urlopen", urlopen)

    with pytest.raises(sync._TransientPublicFetchError):
        sync._public_http_get("https://api.github.com/repos/owner/repo")
    assert len(calls) == 1


def test_repository_metadata_recovers_exhausted_html_transport(monkeypatch):
    import json
    calls = []
    record = dict(id=123, full_name="owner/repo", private=False,
                  visibility="public", archived=False)

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR) from sync._TransientPublicFetchError()
        return json.dumps(record).encode()

    monkeypatch.setattr(sync, "_public_http_get", fetch)
    assert sync.public_repository_page_record("owner/repo") == record
    assert calls == ["https://github.com/owner/repo", "https://api.github.com/repos/owner/repo"]


@pytest.mark.parametrize("change", [dict(private=True), dict(visibility="internal"),
    dict(full_name="owner/other"), dict(id=None), dict(archived=None)])
def test_repository_metadata_recovery_rejects_unverified_identity(monkeypatch, change):
    import json
    record = dict(id=123, full_name="owner/repo", private=False,
                  visibility="public", archived=False)
    record.update(change)

    def fetch(url):
        if url.startswith("https://github.com/"):
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR) from sync._TransientPublicFetchError()
        return json.dumps(record).encode()

    monkeypatch.setattr(sync, "_public_http_get", fetch)
    with pytest.raises(ValueError, match="invalid public repository identity"):
        sync.public_repository_page_record("owner/repo")


@pytest.mark.parametrize("error", [sync.PublicGitNotFound("not found"),
    sync.PublicGitAccessBlocked("blocked"), ValueError("malformed metadata")])
def test_repository_metadata_does_not_recover_access_or_validation_errors(monkeypatch, error):
    calls = []

    def fetch(url):
        calls.append(url)
        raise error

    monkeypatch.setattr(sync, "_public_http_get", fetch)
    with pytest.raises(type(error)):
        sync.public_repository_page_record("owner/repo")
    assert calls == ["https://github.com/owner/repo"]


def test_public_ref_recovery_checks_identity_and_full_sha(monkeypatch):
    import json
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith('.atom'):
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR) from sync._TransientPublicFetchError()
        if '/commits/' in url:
            return json.dumps({'sha': 'a' * 40}).encode()
        return json.dumps(dict(id=123, full_name='owner/repo', private=False,
                               visibility='public', archived=False)).encode()

    monkeypatch.setattr(sync, '_public_http_get', fetch)
    assert sync.public_ref_sha('owner/repo', 'feature/a') == 'a' * 40
    assert calls[-1].endswith('/commits/feature%2Fa')
    assert len(calls) == 3


@pytest.mark.parametrize('sha', [None, 'abc', 'g' * 40])
def test_public_ref_recovery_rejects_bad_sha(monkeypatch, sha):
    import json

    def fetch(url):
        if url.endswith('.atom'):
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR) from sync._TransientPublicFetchError()
        if '/commits/' in url:
            return json.dumps({'sha': sha}).encode()
        return json.dumps(dict(id=123, full_name='owner/repo', private=False,
                               visibility='public', archived=False)).encode()

    monkeypatch.setattr(sync, '_public_http_get', fetch)
    with pytest.raises(ValueError):
        sync.public_ref_sha('owner/repo')


def test_html_outage_and_public_metadata_cache_are_audit_scoped(monkeypatch):
    import json
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith('https://github.com/'):
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR) from sync._TransientPublicFetchError()
        if '/commits/' in url:
            return json.dumps({'sha': 'a' * 40}).encode()
        name = url.removeprefix('https://api.github.com/repos/')
        return json.dumps(dict(id=123, full_name=name, private=False,
                               visibility='public', archived=False, nested={'value': 1})).encode()

    monkeypatch.setattr(sync, '_public_http_get', fetch)

    @sync._parent_fetch_scope
    def audit():
        record = sync.public_repository_page_record('owner/repo')
        record['nested']['value'] = 99
        assert sync.public_repository_page_record('owner/repo')['nested']['value'] == 1
        sync.public_repository_page_record('owner/other')
        assert sync.public_ref_sha('owner/repo') == 'a' * 40

    audit()
    audit()
    assert sum(url.startswith('https://github.com/') for url in calls) == 2
    assert calls.count('https://api.github.com/repos/owner/repo') == 2
    assert sync._PARENT_FETCH_CACHE.get() is None


def test_bad_api_metadata_is_never_cached(monkeypatch):
    import json
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith('https://github.com/'):
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR) from sync._TransientPublicFetchError()
        return json.dumps(dict(id=123, full_name='owner/repo', private=True,
                               visibility='private', archived=False)).encode()

    monkeypatch.setattr(sync, '_public_http_get', fetch)

    @sync._parent_fetch_scope
    def audit():
        for _ in range(2):
            with pytest.raises(ValueError, match='invalid public repository identity'):
                sync.public_ref_sha('owner/repo')
        assert not any(url.startswith('https://api.github.com/') and '/commits/' in url for url in calls)

    audit()
    assert calls.count('https://api.github.com/repos/owner/repo') == 2
