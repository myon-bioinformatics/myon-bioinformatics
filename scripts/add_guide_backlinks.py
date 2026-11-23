#!/usr/bin/env python3
"""Insert portfolio and GitHub back-links into a rendered guide page (stdlib only)."""
from __future__ import annotations

import argparse
import sys
from html import escape
from pathlib import Path

PORTFOLIO_URL = "https://myon-bioinformatics.github.io/"
PORTFOLIO_LABEL = "ポートフォリオ"
GITHUB_LABEL = "GitHub リポジトリ"
NAV_LABEL = "関連リンク"
MAIN_OPEN = '<main class="ui-page">'


def github_repository_url(repository: str) -> str:
    """Return the GitHub URL for an OWNER/REPO value supplied at build time."""
    if not isinstance(repository, str):
        raise ValueError("repository must be OWNER/REPO")
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repository must be OWNER/REPO")
    for part in parts:
        if part in {".", ".."} or not all(ch.isalnum() or ch in "._-" for ch in part):
            raise ValueError("repository must be OWNER/REPO")
    return "https://github.com/{0}/{1}".format(parts[0], parts[1])


def attr(value: str) -> str:
    return escape(value, quote=True)


def text(value: str) -> str:
    return escape(value, quote=False)


def nav_html(repository: str) -> str:
    return (
        '<nav aria-label="{label}">'
        '<p class="ui-links">'
        '<a href="{portfolio}">{portfolio_label}</a>'
        " · "
        '<a href="{github}">{github_label}</a>'
        "</p></nav>"
    ).format(
        label=attr(NAV_LABEL),
        portfolio=attr(PORTFOLIO_URL),
        portfolio_label=text(PORTFOLIO_LABEL),
        github=attr(github_repository_url(repository)),
        github_label=text(GITHUB_LABEL),
    )


def inject(document: str, repository: str) -> str:
    if not isinstance(document, str) or MAIN_OPEN not in document:
        raise ValueError('rendered guide is missing <main class="ui-page">')
    nav = nav_html(repository)
    if nav in document:
        return document
    return document.replace(MAIN_OPEN, MAIN_OPEN + nav, 1)


def _write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="calling repository as OWNER/REPO")
    parser.add_argument("--in-place", action="store_true", help="rewrite FILE instead of writing stdout")
    parser.add_argument("file", nargs="?", help="rendered HTML (stdin when omitted)")
    args = parser.parse_args(argv)
    if args.in_place and not args.file:
        parser.error("--in-place requires FILE")
    try:
        if args.file:
            document = Path(args.file).read_text(encoding="utf-8")
        else:
            document = sys.stdin.read()
        result = inject(document, args.repository)
    except (ValueError, OSError) as error:
        print("add_guide_backlinks: {0}".format(error), file=sys.stderr)
        return 1
    if args.in_place:
        _write(Path(args.file), result)
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
