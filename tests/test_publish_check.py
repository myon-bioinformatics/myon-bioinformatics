"""publish_check: deploy/http/bytes/text/regex checks and exit-code contracts."""
import functools
import http.server
import importlib.util
import json
import subprocess
import threading
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish_check", ROOT / "publish_check.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


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


def run(argv, **kw):
    kw.setdefault("api", api_for())
    kw.setdefault("sleep", lambda s: None)
    return pc.main(argv, **kw)


def test_all_angles_ok(site, capsys):
    base, served = site
    code = run(["--site", base, "tools/panel.js", "", "--local-root", str(served),
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
    code = run(["--site", base, "tools/panel.js", "--local-root", str(tmp_path / "local"), "--contains", "v = 3"])
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
    code = run(["--site", base, "tools/panel.js", "--regex", r"export const v = \d+;"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert r"ok regex: 'export const v = \\d+;'" in out

    code = run(["--site", base, "tools/panel.js", "--regex", r"nonexistent_pattern_\d+"])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "fail regex:" in out

    code = run(["--site", base, "tools/panel.js", "", "--regex", "v = 2", "--regex-on", "tools/panel.js"])
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
    code = run(argv, get=lambda url: (200, b"export const v = 2;\n", ""))
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
    code = run(["https://example.test/", "--regex", "(?x)\n["],
               get=lambda url: (200, b"body", ""))
    captured = capsys.readouterr()
    assert code == 1
    assert len(captured.out.splitlines()) == 3  # HTTP, regex, summary
    assert "fail regex: invalid pattern" in captured.out


def test_regex_on_filters_exact_targets_and_is_repeatable(capsys):
    bodies = {"https://example.test/a": b"wanted", "https://example.test/b": b"wanted",
              "https://example.test/c": b"absent"}
    code = run(["--site", "https://example.test/", "a", "b", "c", "--no-bust",
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
    code = run(["https://example.test/", "--regex", pattern],
               get=lambda url: (status, b"body", error))
    captured = capsys.readouterr()
    assert code == expected
    assert line in captured.out
    assert "regex:" not in captured.out
    assert captured.err == ""


def test_regex_failure_takes_precedence_over_other_unreachable_target(capsys):
    code = run(["https://example.test/good", "https://example.test/blocked", "--regex", "["],
               get=lambda url: (None, b"", "blocked") if "/blocked" in url else (200, b"body", ""))
    out = capsys.readouterr().out
    assert code == 1
    assert "fail regex: invalid pattern" in out and "unreachable http:" in out


def test_usage_errors():
    with pytest.raises(SystemExit):
        pc.main([])
    with pytest.raises(SystemExit):
        pc.main(["relative/path.js"])
