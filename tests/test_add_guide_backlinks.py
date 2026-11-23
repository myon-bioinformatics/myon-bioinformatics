"""Back-links injected into child guide pages, and never into the audit dashboard."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.add_guide_backlinks import (
    GITHUB_LABEL,
    MAIN_OPEN,
    NAV_LABEL,
    PORTFOLIO_LABEL,
    PORTFOLIO_URL,
    attr,
    github_repository_url,
    inject,
    main,
    nav_html,
    text,
)
from scripts import build_audit_pages as build

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/add_guide_backlinks.py"
GUIDE = '<!doctype html><html lang="en"><body data-ui-theme="auto"><main class="ui-page"><h1 class="ui-title">Guide</h1></main></body></html>\n'


def _run(args: list[str], *, stdin: str | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=check,
    )


def test_github_url_is_derived_from_the_calling_repository():
    assert github_repository_url("example/widget") == "https://github.com/example/widget"
    assert github_repository_url("myon-bioinformatics/BlueProbe") == "https://github.com/myon-bioinformatics/BlueProbe"
    assert github_repository_url("Org-Name/repo.py") == "https://github.com/Org-Name/repo.py"


@pytest.mark.parametrize(
    "value",
    ["", "widget", "/widget", "example/", "example/widget/extra", "example /widget",
     "./widget", "example/..", "example/widget<script>", "https://github.com/example/widget",
     "example/repo name", 123],
)
def test_invalid_repository_names_are_rejected(value):
    with pytest.raises(ValueError, match="OWNER/REPO"):
        github_repository_url(value)


def test_attribute_and_text_values_are_escaped():
    assert attr('https://example.test/"x') == "https://example.test/&quot;x"
    assert text("A <b> & 'c'") == "A &lt;b&gt; &amp; 'c'"


def test_nav_contains_portfolio_and_derived_github_links():
    html = nav_html("example/widget")
    assert f'<nav aria-label="{NAV_LABEL}">' in html
    assert f'<p class="ui-links"><a href="{PORTFOLIO_URL}">{PORTFOLIO_LABEL}</a>' in html
    assert f'<a href="https://github.com/example/widget">{GITHUB_LABEL}</a></p></nav>' == html[html.index("<a href=\"https://github.com"):]
    assert html.count("<a href=") == 2


def test_inject_inserts_nav_once_at_the_start_of_main():
    once = inject(GUIDE, "example/widget")
    assert once.count(nav_html("example/widget")) == 1
    assert MAIN_OPEN + nav_html("example/widget") + '<h1 class="ui-title">Guide</h1>' in once
    assert inject(once, "example/widget") == once


def test_inject_requires_the_guide_main_element():
    with pytest.raises(ValueError, match="ui-page"):
        inject("<html><body>no main</body></html>", "example/widget")
    with pytest.raises(ValueError, match="ui-page"):
        inject(None, "example/widget")  # type: ignore[arg-type]


def test_cli_writes_stdout_from_stdin_and_files(tmp_path, capsys):
    result = _run(["--repository", "example/widget"], stdin=GUIDE, check=True)
    assert result.returncode == 0
    assert nav_html("example/widget") in result.stdout
    source = tmp_path / "index.html"
    source.write_text(GUIDE, encoding="utf-8")
    result = _run(["--repository", "example/widget", str(source)], check=True)
    assert source.read_text(encoding="utf-8") == GUIDE
    assert nav_html("example/widget") in result.stdout
    assert main(["--repository", "example/widget", str(source)]) == 0
    assert nav_html("example/widget") in capsys.readouterr().out


def test_cli_rewrites_the_file_in_place(tmp_path):
    source = tmp_path / "index.html"
    source.write_text(GUIDE, encoding="utf-8")
    result = _run(["--repository", "example/widget", "--in-place", str(source)], check=True)
    assert result.stdout == ""
    assert nav_html("example/widget") in source.read_text(encoding="utf-8")
    assert not (tmp_path / "index.html.tmp").exists()


def test_cli_rejects_bad_input_and_in_place_without_a_file(tmp_path, capsys, monkeypatch):
    result = _run(["--repository", "example/widget", "--in-place"], stdin=GUIDE)
    assert result.returncode == 2
    assert "--in-place requires FILE" in result.stderr
    with pytest.raises(SystemExit) as exited:
        main(["--repository", "example/widget", "--in-place"])
    assert exited.value.code == 2
    assert "--in-place requires FILE" in capsys.readouterr().err
    result = _run(["--repository", "not-a-repo"], stdin=GUIDE)
    assert result.returncode == 1
    assert "OWNER/REPO" in result.stderr
    monkeypatch.setattr(sys.stdin, "read", lambda: GUIDE)
    assert main(["--repository", "not-a-repo"]) == 1
    assert "OWNER/REPO" in capsys.readouterr().err
    monkeypatch.setattr(sys.stdin, "read", lambda: "<p>no main</p>")
    assert main(["--repository", "example/widget"]) == 1
    assert "ui-page" in capsys.readouterr().err
    result = _run(["--repository", "example/widget"], stdin="<p>no main</p>")
    assert result.returncode == 1
    assert "ui-page" in result.stderr
    missing = tmp_path / "missing.html"
    result = _run(["--repository", "example/widget", "--in-place", str(missing)])
    assert result.returncode == 1
    assert result.stderr.startswith("add_guide_backlinks:")
    assert main(["--repository", "example/widget", "--in-place", str(missing)]) == 1
    assert capsys.readouterr().err.startswith("add_guide_backlinks:")


def test_cli_reads_stdin_in_process(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "read", lambda: GUIDE)
    assert main(["--repository", "example/widget"]) == 0
    assert nav_html("example/widget") in capsys.readouterr().out


def test_audit_dashboard_html_does_not_include_guide_backlinks():
    page = build.observatory_page()
    nav = nav_html("example/widget")
    assert nav not in page
    assert f'aria-label="{NAV_LABEL}"' not in page
    assert f'href="{PORTFOLIO_URL}"' not in page
    assert PORTFOLIO_LABEL not in page
    assert f">{GITHUB_LABEL}</a>" not in page
