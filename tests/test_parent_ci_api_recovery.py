from io import BytesIO
from urllib.error import HTTPError

import pytest

import vendor_sync as sync


def test_rate_limit_retry_preserves_auth_and_reports_safe_headers(monkeypatch, capsys):
    calls, sleeps = [], []
    token = "arbitrary-secret"

    def fetch(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 403, token,
                            {"Retry-After": "3", "X-RateLimit-Remaining": "0"}, None)
        return BytesIO(b'{"ok": true}')

    monkeypatch.setattr(sync, "urlopen", fetch)
    monkeypatch.setattr(sync.time, "sleep", sleeps.append)
    assert sync._github_json("https://api.github.com/repos/private/name", token) == {"ok": True}
    assert sleeps == [3]
    assert all(r.get_header("Authorization") == "Bearer " + token for r in calls)
    log = capsys.readouterr().err
    assert token not in log and "private/name" not in log
    assert '"authenticated": true' in log
    assert '"rate_limited": true' in log


@pytest.mark.parametrize("headers", [{}, {"X-RateLimit-Remaining": "0", "Retry-After": "3600"}])
def test_forbidden_or_long_cooldown_is_not_blindly_retried(monkeypatch, headers):
    calls = []

    def fetch(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, 403, "forbidden", headers, None)

    monkeypatch.setattr(sync, "urlopen", fetch)
    monkeypatch.setattr(sync.time, "sleep", lambda _: pytest.fail("unexpected sleep"))
    with pytest.raises(HTTPError, match="GitHub GET failed"):
        sync._github_api("GET", "https://api.github.com/repos/owner/repo", "token")
    assert len(calls) == 1


def test_write_is_not_retried(monkeypatch):
    calls = []

    def fetch(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, 503, "unavailable", {}, None)

    monkeypatch.setattr(sync, "urlopen", fetch)
    with pytest.raises(HTTPError):
        sync._github_api("POST", "https://api.github.com/repos/o/r/pulls", "token", {})
    assert len(calls) == 1


def test_history_cache_is_success_only_and_scoped_to_one_audit(monkeypatch):
    calls = []
    ghi = sync._gh_identity()

    def check(repository, commit, **kwargs):
        calls.append((repository, commit))
        return {"reason": "found", "contains": commit == "a" * 40}

    monkeypatch.setattr(ghi, "first_parent_check", check)

    @sync._parent_fetch_scope
    def audit():
        for _ in range(2):
            assert sync._commit_merged_via_git_bare_fetch("owner/repo", "a" * 40)
        for _ in range(2):
            with pytest.raises(ValueError, match=sync.PIN_NOT_MERGED_ERROR):
                sync._commit_merged_via_git_bare_fetch("owner/repo", "b" * 40)

    audit()
    audit()
    assert calls.count(("owner/repo", "a" * 40)) == 2
    assert calls.count(("owner/repo", "b" * 40)) == 4
    assert sync._PARENT_FETCH_CACHE.get() is None


def test_json_cache_returns_independent_values_and_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "_github_read_bytes", lambda *args: calls.append(args) or b'{"items": []}')

    @sync._parent_fetch_scope
    def audit():
        sync._github_json("url")["items"].append("mutated")
        assert sync._github_json("url") == {"items": []}

    audit()
    audit()
    assert len(calls) == 2
